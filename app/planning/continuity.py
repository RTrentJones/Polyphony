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

Located findings (SPEC-31): chunks hold whole scenes under `SCENE_MARKER`s;
the model quotes the prose and the server finds the quote's scene.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import NamedTuple, Optional, TypedDict
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
from app.exports.builder import scene_text
from app.llm.client import get_llm_client
from app.llm.json_utils import extract_json_array, extract_json_object
from app.versioning.trash import live as _live  # the one soft-delete filter

logger = setup_logging("planning.continuity")

CHUNK_WORDS = 2500
CARRY_WORDS = 300
FINDING_TYPES = {"timeline", "character", "object", "thread", "other"}
_SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2}


class Finding(TypedDict):
    """One continuity finding, as stored in ContinuityReport.findings (SPEC-31)."""

    type: str  # timeline | character | object | thread | other
    severity: str  # critical | major | minor
    detail: str  # <= 1000 chars
    quote: str  # the model's quote, <= 200 chars, as given ("" if none)
    scene_id: Optional[
        str
    ]  # the scene whose prose contains `quote`; None = not located


SCENE_MARKER = '[Ch {chapter} "{chapter_title}" › Scene {scene} "{scene_title}"]'


class ProseScene(NamedTuple):
    """One live scene's prose and its place in the book (SPEC-31)."""

    scene_id: str
    chapter: int  # Chapter.number
    chapter_title: str
    scene: int  # position + 1 (live positions are contiguous)
    scene_title: str
    text: str


class Chunk(NamedTuple):
    text: str  # each scene (or piece of one) under its SCENE_MARKER line
    scenes: list[ProseScene]  # each holding the text of its piece


async def collect_scenes(
    session: AsyncSession, book_id: UUID, chapter_id: Optional[UUID]
) -> list[ProseScene]:
    """The live scenes with prose of a chapter (or the whole book), in book
    order: the continuity input, read at run time, never put in a payload."""
    from app.core.orm_models import Chapter, Scene

    query = (
        _live(select(Scene, Chapter), Scene, Chapter)
        .join(Chapter, Scene.chapter_id == Chapter.id)
        .where(Chapter.book_id == book_id)
        .order_by(Chapter.position, Scene.position)
    )
    if chapter_id:
        query = query.where(Scene.chapter_id == chapter_id)
    rows = (await session.execute(query)).all()
    return [
        ProseScene(str(s.id), c.number, c.title, s.position + 1, s.title or "", text)
        for s, c in rows
        if (text := scene_text(s))
    ]


def _pack(items: list, words, limit: int) -> list[list]:
    """Greedy: consecutive items in groups of at most `limit` words."""
    groups: list[list] = [[]]
    size = 0
    for item in items:
        if groups[-1] and size + words(item) > limit:
            groups.append([])
            size = 0
        groups[-1].append(item)
        size += words(item)
    return [g for g in groups if g]


def chunk_scenes(
    scenes: list[ProseScene], chunk_words: int = CHUNK_WORDS
) -> list[Chunk]:
    """Pack whole scenes into chunks of at most `chunk_words` words. A longer
    scene splits at paragraph breaks (kept), by words only inside an
    over-long paragraph, and each piece opens with the scene's marker."""
    pieces = []
    for scene in scenes:
        paragraphs = []
        for para in re.split(r"\n\s*\n", scene.text):
            w = para.split()
            paragraphs += [
                " ".join(w[i : i + chunk_words]) for i in range(0, len(w), chunk_words)
            ]
        pieces += [
            scene._replace(text="\n\n".join(group))
            for group in _pack(paragraphs, lambda p: len(p.split()), chunk_words)
        ]
    return [
        Chunk(
            "\n\n".join(f"{SCENE_MARKER.format(**s._asdict())}\n{s.text}" for s in g),
            g,
        )
        for g in _pack(pieces, lambda s: len(s.text.split()), chunk_words)
    ]


def _found(quote: str, text: str) -> bool:
    return bool(_normal(quote)) and _normal(quote) in _normal(text)


def _normal(text: str) -> str:
    """Whitespace-normalised, outer quotation marks stripped: how a quote is
    matched against the prose."""
    return " ".join(text.split()).strip("\"'“”‘’").strip()


def validate_findings(raw, chunk: Chunk) -> list[Finding]:
    """Model output is untrusted until validated (BRD R2.4). Output fields are
    bounded for display — this is the model's text, not the author's. The
    server, not the model, says which scene a quote is in; a finding whose
    quote is not in the chunk is kept with `scene_id` None (SPEC-31)."""
    if isinstance(raw, dict):
        raw = raw.get("findings")
    if not isinstance(raw, list):
        return []
    findings: list[Finding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        finding_type = str(item.get("type", "other")).lower()
        quote = str(item.get("quote") or "").strip()[:200]
        findings.append(
            {
                "type": (finding_type if finding_type in FINDING_TYPES else "other"),
                "severity": _norm_severity(item.get("severity")),
                "detail": _bounded(item.get("detail"), 1000),
                "quote": quote,
                "scene_id": next(
                    (s.scene_id for s in chunk.scenes if _found(quote, s.text)), None
                ),
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
    the same non-empty quote), keeping the most severe."""
    merged: dict[str, dict] = {}
    by_quote: dict[str, str] = {}
    for f in findings:
        k = _key(f)
        quote = _normal(f.get("quote") or "").lower()
        k = by_quote.get(quote, k) if k not in merged and quote else k
        if k in merged:
            if _SEVERITY_RANK[f["severity"]] < _SEVERITY_RANK[merged[k]["severity"]]:
                merged[k] = {**merged[k], "severity": f["severity"]}
            continue
        merged[k] = f
        if quote:
            by_quote[quote] = k
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
        # One heading per chapter, its summary, then its planned beats indented
        # under it (SPEC-23): a beat list never holds a "- Ch" line.
        from app.planning.planned_beat import normalize_planned_beats

        parts.append("Chapter summaries so far:")
        for c in chapters:
            parts.append(f"Ch {c.position + 1}: {clean_for_llm(c.title)}")
            summary = (c.summary or "").strip()
            if summary:
                parts.append("  " + clean_for_llm(summary).replace("\n", "\n  "))
            parts.extend(
                f"  - {clean_for_llm(b['title'])}: {clean_for_llm(b['summary'])}".rstrip()
                for b in normalize_planned_beats(c.planned_beats)
            )
    plot_state = as_quoted_block("\n".join(parts), "plot_state")
    return "\n\n".join(b for b in (canon.full_block(), plot_state) if b)


def _optional(sentence: str, text: str, label: str) -> str:
    """An optional fence and the one sentence that mentions it, or nothing: a
    prompt never points at a block it does not carry (SPEC-31)."""
    return f"{sentence}\n{as_quoted_block(text, label)}" if text else ""


def _prompt(
    chunk: Chunk, fact_sheet: str, label: str, earlier: list[dict], carry: str
) -> str:
    earlier_block = _optional(
        "Findings already reported (do not repeat them):",
        "\n".join(f"- [{f['type']}] {f['detail']}" for f in earlier),
        "earlier_findings",
    )
    carry_block = _optional(
        "The end of the previous section, for contradictions across the boundary:",
        carry,
        "prior",
    )
    blocks = (STORY_MATERIAL_NOTICE, fact_sheet, earlier_block, carry_block)
    head = "\n\n".join(b for b in blocks if b)
    return f"""{head}

You are a continuity editor. Compare the PROSE ({label}) below against the
established facts above. Each scene opens with a marker line
[Ch N "chapter" › Scene N "scene"]; quote the prose, never a marker line.
Flag CONTRADICTIONS ONLY: names, physical facts, timeline, objects
appearing or vanishing, plot threads violated. Do not flag style, and do not
flag something merely absent from the facts. If there are no contradictions,
return an empty list.

{as_quoted_block(chunk.text, "prose")}

Return ONLY a JSON object:
{{"findings": [{{"type": "timeline|character|object|thread|other",
   "severity": "critical|major|minor",
   "detail": "what contradicts what",
   "quote": "the contradicting words, copied exactly from the prose, at most 200 characters"}}]}}

JSON:"""


async def check_chunk(
    chunk: Chunk,
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
            return validate_findings(parsed, chunk), tokens, True
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


def _input_hash(chunks: list[Chunk], fact_sheet: str) -> str:
    """Over the chunk texts, markers included: a checkpoint from another
    chunking (the pre-SPEC-31 word chunks) never matches, so it restarts."""
    import hashlib

    texts = "\x00".join(c.text for c in chunks)
    return hashlib.sha256(f"{fact_sheet}\x00{texts}".encode("utf-8")).hexdigest()[:16]


async def run_continuity_check(
    scenes: list[ProseScene],
    fact_sheet: str,
    user_id: Optional[UUID] = None,
    *,
    between_chunks=None,
    checkpoint: Optional[dict] = None,
    save_checkpoint=None,
) -> ContinuityResult:
    """Map-reduce over the prose with a rolling carry between chunks.

    `between_chunks`, when given, is awaited before every chunk after the first
    — the job's cancellation seam, so a cancelled check stops instead of
    spending the rest of the book (SPEC-07).

    `checkpoint`/`save_checkpoint` make a paused check resume at its next
    chunk instead of re-paying for the ones it finished (SPEC-08). A checkpoint
    taken against different chunks or facts is discarded: findings from two
    versions of the book must not be merged."""
    all_findings: list[dict] = []
    total_tokens = 0
    chunks = chunk_scenes(scenes)
    checked, unchecked = 0, []
    carry = ""
    start = 0
    input_hash = _input_hash(chunks, fact_sheet)
    ck = checkpoint or {}
    if ck.get("input_hash") == input_hash:
        start = int(ck.get("next") or 0)
        all_findings = list(ck.get("findings") or [])
        checked = int(ck.get("checked") or 0)
        unchecked = list(ck.get("unchecked") or [])
        carry = ck.get("carry") or ""
        total_tokens = int(ck.get("tokens") or 0)
    for i, chunk in enumerate(chunks):
        if i < start:
            continue
        if i and between_chunks is not None:
            await between_chunks()
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
        carry = " ".join(chunk.text.split()[-CARRY_WORDS:])
        if save_checkpoint is not None and i + 1 < len(chunks):
            await save_checkpoint(
                {
                    "input_hash": input_hash,
                    "next": i + 1,
                    "findings": all_findings,
                    "checked": checked,
                    "unchecked": unchecked,
                    "carry": carry,
                    "tokens": total_tokens,
                    "progress": {
                        "stage": "chunks",
                        "done": i + 1,
                        "total": len(chunks),
                    },
                }
            )
    return ContinuityResult(
        findings=all_findings,
        tokens=total_tokens,
        chunks_total=len(chunks),
        chunks_checked=checked,
        unchecked=unchecked,
    )
