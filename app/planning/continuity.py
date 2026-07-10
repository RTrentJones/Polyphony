"""Continuity checking: flag contradictions against the bible and prior prose.

Token-hungry by nature, so it is chapter-scoped by default, runs through the
same paced LLM client, and is charged against the user's daily budget.
Map-reduce shape: each chunk of prose is checked against the fact sheet
(bible + open threads + chapter summaries); findings are merged.
"""

from typing import Optional
from uuid import UUID

from app.core.logging_config import setup_logging
from app.llm.client import get_llm_client
from app.llm.json_utils import extract_json_array

logger = setup_logging("planning.continuity")

CHUNK_WORDS = 2500
FINDING_TYPES = {"timeline", "character", "object", "thread", "other"}


def chunk_prose(text: str, chunk_words: int = CHUNK_WORDS) -> list[str]:
    words = text.split()
    return [
        " ".join(words[i : i + chunk_words]) for i in range(0, len(words), chunk_words)
    ]


def validate_findings(raw) -> list[dict]:
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
                "severity": str(item.get("severity", "minor")).lower()[:20],
                "detail": str(item.get("detail", "")).strip()[:1000],
                "refs": str(item.get("refs", "")).strip()[:300],
            }
        )
    return [f for f in findings if f["detail"]]


async def check_chunk(
    chunk: str,
    fact_sheet: str,
    chunk_label: str,
    user_id: Optional[UUID] = None,
) -> tuple[list[dict], int]:
    """Check one prose chunk; returns (findings, tokens_used)."""
    prompt = f"""You are a continuity editor. Compare this prose against the
established facts and flag CONTRADICTIONS ONLY (names, physical facts,
timeline, objects appearing/vanishing, plot threads violated). Do not flag
style. If there are no contradictions, return [].

ESTABLISHED FACTS:
{fact_sheet[:4000]}

PROSE ({chunk_label}):
{chunk}

Return ONLY valid JSON:
[{{"type": "timeline|character|object|thread|other",
   "severity": "critical|major|minor",
   "detail": "what contradicts what",
   "refs": "quote or location hint"}}]

JSON:"""
    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=3000,
        user_id=user_id,
        purpose="continuity",
    )
    try:
        findings = validate_findings(extract_json_array(result.text))
    except ValueError:
        logger.warning(
            "Continuity chunk returned unparseable JSON",
            extra_fields={"event": "continuity_parse_failed", "chunk": chunk_label},
        )
        findings = []
    return findings, result.tokens_in + result.tokens_out


async def run_continuity_check(
    prose: str,
    fact_sheet: str,
    user_id: Optional[UUID] = None,
) -> tuple[list[dict], int]:
    """Map-reduce over the prose; returns (all findings, total tokens)."""
    all_findings: list[dict] = []
    total_tokens = 0
    chunks = chunk_prose(prose)
    for i, chunk in enumerate(chunks):
        findings, tokens = await check_chunk(
            chunk, fact_sheet, f"part {i + 1}/{len(chunks)}", user_id
        )
        all_findings.extend(findings)
        total_tokens += tokens
    return all_findings, total_tokens
