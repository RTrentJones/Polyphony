"""Source text -> proposed Canon (Phase 6, docs/BRD.md R6).

Reads a Source and PROPOSES typed canon candidates — characters, canon entries,
a style guess, and a synopsis — for the author to review before anything is
committed. This is what should have happened to the Bored to Undeath storyboard:
its ## CHARACTERS and ## WORLDBUILDING sections become real entities instead of
dying in a truncated synopsis field.

The source is untrusted (it may be an uploaded .pdf/.html), so it is fenced with
as_quoted_block, never filtered (docs/ADR-002-book-as-root.md §4). Nothing here
writes to the DB — the caller reviews, then commits.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.core.llm_text import (
    MAX_SOURCE_CHARS,
    STORY_MATERIAL_NOTICE,
    as_quoted_block,
    clean_for_llm,
)
from app.core.logging_config import setup_logging
from app.llm.client import get_llm_client
from app.llm.json_utils import extract_json_object

logger = setup_logging("parsing.canon_extractor")

_CATEGORIES = "world | location | faction | item | concept | org"


def _normalize_proposals(raw: dict) -> dict:
    """Coerce the model's object into the reviewed-proposal shape, defensively."""
    characters = []
    for c in raw.get("characters") or []:
        if isinstance(c, dict) and str(c.get("name", "")).strip():
            characters.append(
                {
                    "name": str(c.get("name", "")).strip()[:255],
                    "role": str(c.get("role", "") or "").strip()[:100],
                    "description": str(c.get("description", "") or "").strip(),
                }
            )
    entries = []
    for e in raw.get("canon_entries") or []:
        if isinstance(e, dict) and str(e.get("name", "")).strip():
            cat = str(e.get("category", "concept") or "concept").strip().lower()
            entries.append(
                {
                    "name": str(e.get("name", "")).strip()[:255],
                    "category": cat if cat in _CATEGORIES else "concept",
                    "content": str(e.get("content", "") or "").strip(),
                }
            )
    style = raw.get("style") if isinstance(raw.get("style"), dict) else {}
    style = {
        k: str(style.get(k, "") or "").strip()
        for k in ("pov", "tense", "tone", "comps")
    }
    synopsis = str(raw.get("synopsis", "") or "").strip()
    return {
        "characters": characters,
        "canon_entries": entries,
        "style": style,
        "synopsis": synopsis,
    }


async def extract_canon(text: str, *, user_id: Optional[UUID] = None) -> dict:
    """Propose typed canon candidates from source text. Never writes."""
    source_block = as_quoted_block(
        clean_for_llm(text, max_chars=MAX_SOURCE_CHARS, label="source"), "source"
    )
    prompt = f"""{STORY_MATERIAL_NOTICE}

{source_block}

Extract the book's canon from the source above. Do NOT invent — only propose what
the text supports. These are PROPOSALS the author will review.

Return ONLY a JSON object:
{{
  "characters": [{{"name": "...", "role": "protagonist|antagonist|supporting|...",
     "description": "who they are, from the text"}}],
  "canon_entries": [{{"name": "...", "category": "{_CATEGORIES}",
     "content": "the worldbuilding fact, from the text"}}],
  "style": {{"pov": "", "tense": "", "tone": "", "comps": ""}},
  "synopsis": "2-4 sentence synopsis grounded in the source"
}}

JSON:"""
    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=4096,
        user_id=user_id,
        purpose="extraction",
    )
    try:
        raw = extract_json_object(result.text)
    except ValueError as e:
        logger.warning(
            f"Extraction JSON parse failed: {e}",
            extra_fields={"event": "extraction_parse_failed"},
        )
        raise ValueError("The model returned unparseable extraction output")
    return _normalize_proposals(raw)
