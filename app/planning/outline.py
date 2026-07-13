"""LLM-drafted outlines / beat sheets.

An outline is a JSON list of nodes: {"title", "summary", "children": [...]}.
Top-level nodes map naturally onto chapters ("promote" endpoint).
"""

from typing import Optional
from uuid import UUID

from app.core.logging_config import setup_logging
from app.core.sanitization import sanitize_for_llm
from app.llm.client import get_llm_client
from app.llm.json_utils import extract_json_array

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

Develop THIS premise faithfully — this is the author's story, not a prompt to
riff on. Use the characters, setting, and central conflict the premise (and
character bible) name; expand and dramatize what is given. Do NOT introduce
major new characters or plot lines the premise doesn't imply, and do not swap
its conflict for a different one. Recover the shape the premise is reaching for
rather than inventing a new story around the same title.

Within that fidelity, shape the arc so the through-line is unmistakable and
causally ordered:
- open with an inciting incident that sets the premise in motion,
- build rising complications with escalating stakes,
- turn on a midpoint reversal or revelation,
- drive to a climax that pays off the central conflict,
- close with a resolution / new equilibrium.
Each node's summary must say what CHANGES (a decision, reversal, or consequence),
not just what happens. Order nodes so each follows causally from the last.

Return ONLY valid JSON — an array of nodes:
[
  {{"title": "Chapter/beat title", "summary": "1-3 sentences of what changes",
    "children": [{{"title": "scene beat", "summary": "...", "children": []}}]}}
]

JSON:"""

    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=8192,
        user_id=user_id,
        purpose="outline",
    )
    try:
        nodes = extract_json_array(result.text)
    except ValueError as e:
        logger.warning(
            f"Outline JSON parse failed: {e}",
            extra_fields={"event": "outline_parse_failed"},
        )
        raise ValueError("The model returned an unparseable outline; try again")
    return validate_outline_nodes(nodes)
