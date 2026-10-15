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

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm_text import MAX_CANON_CHARS, as_quoted_block, clean_for_llm

# Roles that make a character a PRINCIPAL — the hard-gate cast that must appear
# in a faithful outline (docs/BRD.md R1.4).
_PRINCIPAL_ROLES = {
    "protagonist",
    "antagonist",
    "deuteragonist",
    "main",
    "lead",
    "villain",
    "hero",
    "narrator",
}

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


def _render_json_field(value: Any, label: Optional[str] = None) -> Optional[str]:
    """Render a JSON bible field as readable prose, not a Python repr.

    These columns are free-form JSON in practice: dicts from the manual create
    path, lists from extraction, occasionally a bare string. Feeding the model
    `{'wry': True}` teaches it Python; feeding it `wry` teaches it the character.

    `label` is the line's own label ("Voice"). A dict key equal to it, or a
    string value that repeats it as a prefix, would print "Voice: voice: dry"
    (SPEC-26 P9), so that key/prefix is dropped and only its value is kept.
    """
    if not value:
        return None
    if isinstance(value, str):
        return clean_for_llm(_strip_label(value, label)) or None
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            if v is None or v is False:
                continue
            if _is_label(k, label):
                # {"voice": "dry"} under "Voice:" is just "dry".
                text = _strip_label(str(v), label) if not isinstance(v, bool) else ""
                if text.strip():
                    parts.append(text.strip())
                continue
            # A bare key ({"wry": ""} / {"wry": True}) renders as just the key.
            parts.append(f"{k}: {v}" if not isinstance(v, bool) and v != "" else str(k))
        return "; ".join(parts) or None
    if isinstance(value, (list, tuple, set)):
        parts = [_strip_label(str(v), label) for v in value if v]
        return "; ".join(p for p in parts if p.strip()) or None
    return str(value)


def _is_label(key: Any, label: Optional[str]) -> bool:
    return bool(label) and str(key).strip().casefold() == label.strip().casefold()


def _strip_label(text: str, label: Optional[str]) -> str:
    """Drop a leading `label:` (repeatedly, case-insensitive): "voice: dry" -> "dry"."""
    if not label:
        return text
    rx = re.compile(rf"^\s*{re.escape(label.strip())}\s*:\s*", re.IGNORECASE)
    while True:
        stripped = rx.sub("", text, count=1)
        if stripped == text:
            return text
        text = stripped


def _aliases(character: Any) -> list[str]:
    return [
        a.strip()
        for a in (getattr(character, "aliases", None) or [])
        if isinstance(a, str) and a.strip()
    ]


def render_character(character: Any) -> str:
    """One character's full bible entry as markdown."""
    lines = [f"### {character.name}"]
    aliases = _aliases(character)
    if aliases:
        # "the CEL" read as a company name because nothing told the model it
        # was Edric Thane's title (BRD §7).
        lines.append("Also known as: " + "; ".join(clean_for_llm(a) for a in aliases))

    for attr, label in _CHARACTER_FIELDS:
        value = clean_for_llm(getattr(character, attr, None))
        if value:
            lines.append(f"{label}: {value}")

    for attr, label in _CHARACTER_JSON_FIELDS:
        rendered = _render_json_field(getattr(character, attr, None), label)
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


# Canon-entry categories, ordered as an author reads a world: the big shape
# first (world), then places, powers, things, ideas, institutions.
_CATEGORY_LABELS: list[tuple[str, str]] = [
    ("world", "World"),
    ("location", "Locations"),
    ("faction", "Factions"),
    ("item", "Items"),
    ("concept", "Concepts"),
    ("org", "Organizations"),
]


def render_canon_entries(entries: Iterable[Any]) -> str:
    """Canon entries grouped by category, in full. "" when there are none."""
    entries = list(entries or [])
    if not entries:
        return ""
    by_cat: dict[str, list[Any]] = {}
    for e in entries:
        by_cat.setdefault(e.category or "concept", []).append(e)

    parts: list[str] = []
    seen: set[str] = set()
    for cat, label in _CATEGORY_LABELS:
        items = by_cat.get(cat)
        if not items:
            continue
        seen.add(cat)
        parts.append(f"## {label}")
        for e in items:
            body = clean_for_llm(getattr(e, "content", None)) or ""
            parts.append(f"### {clean_for_llm(e.name)}\n{body}".rstrip())
    # Any unknown categories still render, so nothing is silently dropped.
    for cat, items in by_cat.items():
        if cat in seen:
            continue
        parts.append(f"## {cat.title()}")
        for e in items:
            body = clean_for_llm(getattr(e, "content", None)) or ""
            parts.append(f"### {clean_for_llm(e.name)}\n{body}".rstrip())
    return "\n\n".join(parts)


def render_style(style: Any) -> str:
    """The book's style guide as readable lines. "" when unset."""
    if style is None:
        return ""
    fields = [
        ("POV", getattr(style, "pov", None)),
        ("Tense", getattr(style, "tense", None)),
        ("Tone", getattr(style, "tone", None)),
        ("Comps", getattr(style, "comps", None)),
        ("Sample prose", getattr(style, "sample_prose", None)),
    ]
    lines = [f"{label}: {clean_for_llm(v)}" for label, v in fields if v]
    return "\n".join(lines)


def render_bible(
    characters: Optional[Iterable[Any]] = None,
    canon_entries: Optional[Iterable[Any]] = None,
    style: Any = None,
) -> str:
    """The non-synopsis canon (cast + worldbuilding + style) as one bible block.

    This is what the outline's `character_bible` argument should carry: the FULL
    cast (every field, via render_characters) plus canon entries and style —
    never the old one-line `name: role description` slice that dropped goals,
    arc, relationships, and the entire world.
    """
    sections: list[str] = []
    cast = render_characters(characters or [])
    if cast:
        sections.append("# Characters\n\n" + cast)
    entries = render_canon_entries(canon_entries or [])
    if entries:
        sections.append("# Canon\n\n" + entries)
    style_text = render_style(style)
    if style_text:
        sections.append("# Style\n\n" + style_text)
    return "\n\n".join(sections)


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


def _name_aliases(name: str) -> list[str]:
    """A name plus its individual tokens ("Milo Voss" -> Milo, Voss) as aliases,
    so a chapter that refers to a character by first name still counts as present.
    Single-letter tokens are dropped to avoid spurious matches."""
    toks = [t for t in (name or "").split() if len(t) > 1]
    return [name, *toks] if len(toks) > 1 else [name]


@dataclass
class Canon:
    """A book's assembled Canon — S0 of the staged outline (docs/BRD.md R5).

    Holds the whole authored truth (title, genre, synopsis, cast, canon entries,
    style) plus the derived grounding a fidelity check needs: canon_terms (the
    known-proper-noun allowlist) and principals (the hard-gate cast).
    """

    title: str
    genre: str = ""
    synopsis: str = ""
    characters: list = field(default_factory=list)
    canon_entries: list = field(default_factory=list)
    style: Any = None

    def bible(self) -> str:
        return render_bible(self.characters, self.canon_entries, self.style)

    def full_block(self) -> str:
        """The whole canon as one fenced prompt block — untruncated.

        Raises TextTooLargeError above MAX_CANON_CHARS (loud by design); callers
        may summarize a category and retry (staged degrade).
        """
        blocks: list[str] = [f"Title: {clean_for_llm(self.title)}"]
        if self.genre:
            blocks.append(f"Genre: {clean_for_llm(self.genre)}")
        syn = as_quoted_block(self.synopsis, "synopsis")
        if syn:
            blocks.append(syn)
        bible_block = as_quoted_block(self.bible(), "canon")
        if bible_block:
            blocks.append(bible_block)
        canon = "\n\n".join(blocks)
        clean_for_llm(canon, max_chars=MAX_CANON_CHARS, label="canon")
        return canon

    def canon_terms(self) -> set[str]:
        """Known proper nouns: character names (+ tokens) + canon-entry names."""
        terms: set[str] = set()
        for c in self.characters:
            if getattr(c, "name", None):
                terms.update(_name_aliases(c.name))
                terms.update(_aliases(c))
        for e in self.canon_entries:
            if getattr(e, "name", None):
                terms.add(e.name)
        return terms

    def principals(self) -> list[dict]:
        """The hard-gate cast as [{name, aliases}]. Characters whose role marks
        them principal; if none are marked, the whole (hand-authored) cast."""
        marked = [
            c
            for c in self.characters
            if any(
                r in (getattr(c, "role", "") or "").lower() for r in _PRINCIPAL_ROLES
            )
        ]
        chosen = marked or list(self.characters)
        # Recorded aliases count as presence: an outline that says "the CEL"
        # has cast Edric Thane (SPEC-09).
        return [
            {"name": c.name, "aliases": [*_name_aliases(c.name), *_aliases(c)]}
            for c in chosen
        ]


async def build_canon(session: AsyncSession, book_id: UUID) -> Canon:
    """S0: assemble a book's whole Canon from the DB (no LLM). Book is the root,
    so every piece is queried by book_id."""
    from app.core.orm_models import Book, CanonEntry, Character, StyleGuide
    from app.versioning.trash import live

    # Trashed characters and entries never reach a prompt (SPEC-13).

    book = await session.get(Book, book_id)
    if book is None:
        raise ValueError(f"book {book_id} not found")
    characters = list(
        (
            await session.execute(
                live(select(Character), Character)
                .where(Character.book_id == book_id)
                .order_by(Character.name)
            )
        )
        .scalars()
        .all()
    )
    canon_entries = list(
        (
            await session.execute(
                live(select(CanonEntry), CanonEntry)
                .where(CanonEntry.book_id == book_id)
                .order_by(CanonEntry.position)
            )
        )
        .scalars()
        .all()
    )
    style = (
        await session.execute(select(StyleGuide).where(StyleGuide.book_id == book_id))
    ).scalar_one_or_none()
    return Canon(
        title=book.title,
        genre=book.genre or "",
        synopsis=book.synopsis or "",
        characters=characters,
        canon_entries=canon_entries,
        style=style,
    )
