"""Assembling a book's Canon into prompt context.

Canon is the book's authored truth — synopsis, characters, canon entries, style
(docs/BRD.md §3). This module renders it for a prompt, in full.

"In full" is the entire point. The previous renderer emitted one line per
character —

    f"- {c.name}: {c.role or ''} {c.description or ''}"

— dropping `goals`, `arc`, `relationships`, `personality_traits`,
`voice_characteristics`, and `notes` outright, then the caller sliced the result
to `[:2000]`. The outline therefore received a strictly *thinner* cast than the
continuity checker did, and in practice received nothing at all: the query
feeding it filtered on `Character.book_id`, which no code path ever wrote, so it
always returned zero rows (docs/BRD.md §1). Both halves of that are fixed here
and in the book_id wiring.

There is no truncation in this module. A large canon is ~10k tokens against a
1M-token window; if one ever genuinely outgrows the window we summarise a
category and say so (Phase 5) — we never cut a string mid-sentence.
"""

from typing import Any, Iterable, Optional

from app.core.llm_text import MAX_CANON_CHARS, as_quoted_block, clean_for_llm

# Bible fields rendered for every character, in the order an author would want
# them read: who they are, what they want, how they change, how they sound.
_CHARACTER_FIELDS: list[tuple[str, str]] = [
    ("role", "Role"),
    ("description", "Description"),
    ("goals", "Goals"),
    ("arc", "Arc"),
    ("notes", "Notes"),
]

_CHARACTER_JSON_FIELDS: list[tuple[str, str]] = [
    ("personality_traits", "Personality"),
    ("voice_characteristics", "Voice"),
    ("relationships", "Relationships"),
]


def _render_json_field(value: Any) -> Optional[str]:
    """Render a JSON bible field as readable prose, not a Python repr.

    These columns are free-form JSON in practice: dicts from the manual create
    path, lists from extraction, occasionally a bare string. Feeding the model
    `{'wry': True}` teaches it Python; feeding it `wry` teaches it the character.
    """
    if not value:
        return None
    if isinstance(value, str):
        return clean_for_llm(value) or None
    if isinstance(value, dict):
        parts = [
            f"{k}: {v}" if not isinstance(v, bool) else str(k)
            for k, v in value.items()
            if v is not None and v is not False
        ]
        return "; ".join(parts) or None
    if isinstance(value, (list, tuple, set)):
        parts = [str(v) for v in value if v]
        return "; ".join(parts) or None
    return str(value)


def render_character(character: Any) -> str:
    """One character's full bible entry as markdown."""
    lines = [f"### {character.name}"]

    for attr, label in _CHARACTER_FIELDS:
        value = clean_for_llm(getattr(character, attr, None))
        if value:
            lines.append(f"{label}: {value}")

    for attr, label in _CHARACTER_JSON_FIELDS:
        rendered = _render_json_field(getattr(character, attr, None))
        if rendered:
            lines.append(f"{label}: {rendered}")

    return "\n".join(lines)


def render_characters(characters: Iterable[Any]) -> str:
    """The full cast, untruncated.

    Returns "" for an empty cast so callers can test truthiness — but note that
    an empty cast is nearly always a bug upstream, not a book without people.
    That exact silence is what produced "Elara".
    """
    entries = [render_character(c) for c in characters]
    return "\n\n".join(e for e in entries if e.strip())


def render_canon(
    *,
    title: str,
    genre: Optional[str] = None,
    synopsis: Optional[str] = None,
    characters: Optional[Iterable[Any]] = None,
) -> str:
    """The whole canon as one fenced prompt block.

    Untrusted content (synopsis text may originate in an uploaded .pdf/.html)
    is fenced with `as_quoted_block` rather than filtered — see
    docs/ADR-002-book-as-root.md §4.

    Raises:
        TextTooLargeError: if the assembled canon exceeds MAX_CANON_CHARS.
            Loud by design; callers decide (Phase 5 summarises a category).
    """
    blocks: list[str] = [f"Title: {clean_for_llm(title)}"]
    if genre:
        blocks.append(f"Genre: {clean_for_llm(genre)}")

    synopsis_block = as_quoted_block(synopsis, "synopsis")
    if synopsis_block:
        blocks.append(synopsis_block)

    cast = render_characters(characters or [])
    cast_block = as_quoted_block(cast, "characters")
    if cast_block:
        blocks.append(cast_block)

    canon = "\n\n".join(blocks)
    # Bound the assembled whole, not each part: this is the cost/DoS ceiling.
    clean_for_llm(canon, max_chars=MAX_CANON_CHARS, label="canon")
    return canon
