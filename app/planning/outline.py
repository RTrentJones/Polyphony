"""LLM-drafted outlines / beat sheets.

An outline is a JSON list of nodes: {"title", "summary", "children": [...]}.
Top-level nodes map naturally onto chapters ("promote" endpoint).
"""

import json
from typing import Optional
from uuid import UUID

from app.core.logging_config import setup_logging
from app.core.sanitization import sanitize_for_llm
from app.llm.client import get_llm_client

logger = setup_logging("planning.outline")


def validate_outline_nodes(nodes) -> list[dict]:
    """Normalize/validate outline JSON into [{title, summary, children}]."""
    if not isinstance(nodes, list):
        raise ValueError("Outline must be a list of nodes")
    normalized = []
    for node in nodes:
        if not isinstance(node, dict) or not str(node.get("title", "")).strip():
            continue
        normalized.append(
            {
                "title": str(node.get("title", "")).strip()[:500],
                "summary": str(node.get("summary", "")).strip()[:2000],
                "children": validate_outline_nodes(node.get("children", []) or []),
            }
        )
    return normalized


async def generate_outline(
    *,
    title: str,
    synopsis: str,
    genre: str = "",
    character_bible: str = "",
    kind: str = "outline",
    chapters_target: int = 12,
    user_id: Optional[UUID] = None,
) -> list[dict]:
    """Draft a chapter-level outline (or beat sheet) from the book's premise."""
    safe_synopsis = sanitize_for_llm(synopsis or "", max_length=2000)
    shape = (
        f"about {chapters_target} chapter nodes, each with 2-4 child scene beats"
        if kind == "outline"
        else "a beat sheet of 12-18 top-level beats (Save the Cat style), no children"
    )

    prompt = f"""You are a story architect. Draft {shape} for this book.

Title: {title}
Genre: {genre or "unspecified"}
Premise/synopsis: {safe_synopsis}

{f"Character bible: {character_bible[:2000]}" if character_bible else ""}

Return ONLY valid JSON — an array of nodes:
[
  {{"title": "Chapter/beat title", "summary": "1-3 sentences of what happens",
    "children": [{{"title": "scene beat", "summary": "...", "children": []}}]}}
]

JSON:"""

    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=3000,
        user_id=user_id,
        purpose="outline",
    )
    text = result.text.replace("```json", "").replace("```", "").strip()
    try:
        nodes = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(
            f"Outline JSON parse failed: {e}",
            extra_fields={"event": "outline_parse_failed"},
        )
        raise ValueError("The model returned an unparseable outline; try again")
    return validate_outline_nodes(nodes)
