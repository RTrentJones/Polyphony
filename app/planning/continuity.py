"""Continuity checking: flag contradictions against the canon and prior prose.

Map-reduce over the prose: each chunk is checked against the book's fact sheet
— the WHOLE Canon (synopsis, cast with every bible field, canon entries, style)
plus the plot state (open threads with their events, chapter summaries) — and
the findings are merged and deduplicated (SPEC-02).

What changed, and why (docs/specs/SPEC-02-continuity-v2.md):

- The fact sheet used to be sent as ``fact_sheet[:4000]``. With a real synopsis
  at its head that cut the cast and every chapter summary — the same silent cap
  that produced "Elara" (docs/BRD.md §1). Nothing is cut now; an over-large
  input raises.
- The fact sheet was a second, thinner canon renderer (no canon entries, no
  style, one line per character). It is now `Canon.full_block()`.
- A chunk whose JSON could not be parsed used to become ``[]`` — the same answer
  as "no contradictions" — so a check that never ran reported clean. Each chunk
  now reports whether it was checked; the report says "checked 7 of 8".
- Each chunk also sees the findings so far and the tail of the previous chunk,
  so a contradiction that straddles a chunk boundary is visible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm_text import (
    MAX_CANON_CHARS,
    STORY_MATERIAL_NOTICE,
    as_quoted_block,
    clean_for_llm,
)
from app.core.logging_config import setup_logging
from app.llm.client import get_llm_client
from app.llm.json_utils import extract_json_array, extract_json_object
from app.versioning.trash import live as _live  # the one soft-delete filter

logger = setup_logging("planning.continuity")

CHUNK_WORDS = 2500
CARRY_WORDS = 300
FINDING_TYPES = {"timeline", "character", "object", "thread", "other"}
_SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2}


def chunk_prose(text: str, chunk_words: int = CHUNK_WORDS) -> list[str]:
    words = text.split()
    return [
        " ".join(words[i : i + chunk_words]) for i in range(0, len(words), chunk_words)
    ]


def validate_findings(raw) -> list[dict]:
    """Model output is untrusted until validated (BRD R2.4). Output fields are
    bounded for display — this is the model's text, not the author's."""
    if isinstance(raw, dict):
        raw = raw.get("findings")
    if not isinstance(raw, list):
        return []
    findings = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        finding_type = str(item.get("type", "other")).lower()
        findings.append(
            {
                "type": (finding_type if finding_type in FINDING_TYPES else "other"),
                "severity": _norm_severity(item.get("severity")),
                "detail": _bounded(item.get("detail"), 1000),
                "refs": _bounded(item.get("refs"), 300),
            }
        )
    return [f for f in findings if f["detail"]]


def _norm_severity(value) -> str:
    v = str(value or "minor").strip().lower()
    return v if v in _SEVERITY_RANK else "minor"


def _bounded(value, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _key(finding: dict) -> str:
    detail = re.sub(r"[^a-z0-9 ]", "", finding["detail"].lower())
    return f"{finding['type']}|{' '.join(detail.split())}"


def dedupe_findings(findings: list[dict]) -> list[dict]:
    """Merge findings that say the same thing (same type + normalised detail, or
    the same non-empty refs), keeping the most severe."""
    merged: dict[str, dict] = {}
    by_ref: dict[str, str] = {}
    for f in findings:
        k = _key(f)
        ref = f.get("refs", "").strip().lower()
        if k not in merged and ref and ref in by_ref:
            k = by_ref[ref]
        if k in merged:
            if _SEVERITY_RANK[f["severity"]] < _SEVERITY_RANK[merged[k]["severity"]]:
                merged[k] = {**merged[k], "severity": f["severity"]}
            continue
        merged[k] = f
        if ref:
            by_ref[ref] = k
    return list(merged.values())


async def build_fact_sheet(session: AsyncSession, book_id: UUID) -> str:
    """The whole Canon plus the book's plot state, fenced as story material."""
    from app.core.orm_models import Chapter, PlotThread, PlotThreadEvent
    from app.planning.canon import build_canon

    canon = await build_canon(session, book_id)
    threads = (
        (
            await session.execute(
                select(PlotThread).where(
                    PlotThread.book_id == book_id, PlotThread.status == "open"
                )
            )
        )
        .scalars()
        .all()
    )
    chapters = (
        (
            await session.execute(
                _live(select(Chapter), Chapter)
                .where(Chapter.book_id == book_id)
                .order_by(Chapter.position)
            )
        )
        .scalars()
        .all()
    )
    parts: list[str] = []
    if threads:
        parts.append("Open plot threads:")
        for t in threads:
            parts.append(f"- {clean_for_llm(t.name)}: {clean_for_llm(t.description)}")
            events = (
                (
                    await session.execute(
                        select(PlotThreadEvent)
                        .where(PlotThreadEvent.thread_id == t.id)
                        .order_by(PlotThreadEvent.position)
                    )
                )
                .scalars()
                .all()
            )
            parts.extend(f"  · ({e.kind}) {clean_for_llm(e.note)}" for e in events)
    if chapters:
        parts.append("Chapter summaries so far:")
        parts.extend(
            f"- Ch{c.position + 1} {clean_for_llm(c.title)}: {clean_for_llm(c.summary)}"
            for c in chapters
        )
    plot_state = as_quoted_block("\n".join(parts), "plot_state")
    return "\n\n".join(b for b in (canon.full_block(), plot_state) if b)


def _prompt(
    chunk: str, fact_sheet: str, label: str, earlier: list[dict], carry: str
) -> str:
    earlier_block = as_quoted_block(
        "\n".join(f"- [{f['type']}] {f['detail']}" for f in earlier), "earlier_findings"
    )
    carry_block = as_quoted_block(carry, "prior")
    return f"""{STORY_MATERIAL_NOTICE}

{fact_sheet}

{earlier_block}

{carry_block}

You are a continuity editor. Compare the PROSE ({label}) below against the
established facts above, AND against the prose that came before it (the
<prior> tail and the earlier findings). Flag CONTRADICTIONS ONLY: names,
physical facts, timeline, objects appearing or vanishing, plot threads
violated. Do not flag style, and do not flag something merely absent from the
facts. If there are no contradictions, return an empty list.

{as_quoted_block(chunk, "prose")}

Return ONLY a JSON object:
{{"findings": [{{"type": "timeline|character|object|thread|other",
   "severity": "critical|major|minor",
   "detail": "what contradicts what",
   "refs": "quote or location hint"}}]}}

JSON:"""


async def check_chunk(
    chunk: str,
    fact_sheet: str,
    chunk_label: str,
    user_id: Optional[UUID] = None,
    *,
    earlier: Optional[list[dict]] = None,
    carry: str = "",
) -> tuple[list[dict], int, bool]:
    """Check one prose chunk. Returns (findings, tokens_used, checked).

    `checked` is False when the model's answer could not be parsed even after a
    retry in strict JSON mode — the chunk was NOT checked, which is different
    from "no contradictions" and must never be reported as clean.
    """
    prompt = _prompt(chunk, fact_sheet, chunk_label, earlier or [], carry)
    clean_for_llm(prompt, max_chars=MAX_CANON_CHARS * 2, label="continuity input")
    client = get_llm_client()
    tokens = 0
    for attempt, fmt in enumerate((None, {"type": "json_object"})):
        result = await client.generate(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=3000,
            user_id=user_id,
            purpose="continuity",
            response_format=fmt,
        )
        tokens += result.tokens_in + result.tokens_out
        parsed = _parse(result.text)
        if parsed is not None:
            return validate_findings(parsed), tokens, True
        logger.warning(
            "Continuity chunk returned unparseable JSON",
            extra_fields={
                "event": "continuity_parse_failed",
                "chunk": chunk_label,
                "attempt": attempt + 1,
            },
        )
    return [], tokens, False


def _parse(text: str):
    try:
        return extract_json_object(text)
    except ValueError:
        pass
    try:
        return extract_json_array(text)
    except ValueError:
        return None


@dataclass
class ContinuityResult:
    findings: list[dict]
    tokens: int
    chunks_total: int
    chunks_checked: int
    unchecked: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.chunks_total and self.chunks_checked == 0:
            return "failed"
        return "completed" if self.chunks_checked == self.chunks_total else "incomplete"

    @property
    def coverage(self) -> dict:
        return {
            "chunks_checked": self.chunks_checked,
            "chunks_total": self.chunks_total,
            "unchecked": self.unchecked,
        }


async def run_continuity_check(
    prose: str,
    fact_sheet: str,
    user_id: Optional[UUID] = None,
) -> ContinuityResult:
    """Map-reduce over the prose with a rolling carry between chunks."""
    all_findings: list[dict] = []
    total_tokens = 0
    chunks = chunk_prose(prose)
    checked, unchecked = 0, []
    carry = ""
    for i, chunk in enumerate(chunks):
        label = f"part {i + 1}/{len(chunks)}"
        findings, tokens, ok = await check_chunk(
            chunk, fact_sheet, label, user_id, earlier=all_findings, carry=carry
        )
        total_tokens += tokens
        if ok:
            checked += 1
            all_findings = dedupe_findings(all_findings + findings)
        else:
            unchecked.append(label)
        carry = " ".join(chunk.split()[-CARRY_WORDS:])
    return ContinuityResult(
        findings=all_findings,
        tokens=total_tokens,
        chunks_total=len(chunks),
        chunks_checked=checked,
        unchecked=unchecked,
    )
