"""Single source of truth for a character's versioned snapshot.

Every path that snapshots or restores a character MUST use this, so a version is
always the FULL authored state (docs/ADR-002 §5's "full JSON, never diffs"). The
extraction-commit path used to snapshot only name/role/description, which made an
imported v1 un-restorable after later enrichment (PR review #3).

Excludes dialogue_count / indexed_at deliberately: those change on every re-index,
and versioning them would spawn a version per re-embed.
"""

CHARACTER_FIELDS = (
    "name",
    "description",
    "personality_traits",
    "voice_characteristics",
    "role",
    "goals",
    "arc",
    "relationships",
    "notes",
)


def character_snapshot(character) -> dict:
    """The full authored state of a character, for a version snapshot."""
    return {field: getattr(character, field, None) for field in CHARACTER_FIELDS}
