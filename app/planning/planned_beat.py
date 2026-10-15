"""Planned-beat contract (wave 2: SPEC-23 persists it, SPEC-24 produces it).

A promoted chapter's planned beats are DATA (`chapters.planned_beats`), not
"Planned beats:" lines inside its summary. Frozen for the wave; changes go
through the orchestrator.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

MAX_PLANNED_BEATS = 12
MAX_SECONDARY_NAMES = 12


class PlannedBeat(BaseModel):
    """One planned scene of a chapter, stored in `chapters.planned_beats`."""

    # Stable id: the outline child's node_id when promoted, else a new uuid4.
    node_id: str
    title: str = Field(..., min_length=1, max_length=500)
    summary: str = Field("", max_length=2000)
    # This book's character ids (uuid strings). [] means "use the chapter cast".
    character_ids: list[str] = Field(default_factory=list)
    # People named in the synopsis or canon who are NOT in the cast (e.g. "Prava").
    # Resolved against the cast again at generate time, so adding one makes them castable.
    secondary_names: list[str] = Field(
        default_factory=list, max_length=MAX_SECONDARY_NAMES
    )
    setting: str = Field("", max_length=500)
    tone: str = Field("", max_length=200)


def beat_scene_description(beat: dict) -> str:
    """The scene_description a planned beat generates with. It is byte-identical
    to the legacy "- title: summary" line without its "- ", so scenes written
    before the migration still count as written (scene_batch.written_beats)."""
    return f"{beat.get('title', '')}: {beat.get('summary', '')}".strip()


def normalize_planned_beats(value: Any) -> list[dict]:
    """Tolerant read of a stored or posted list: drops non-dicts and untitled
    beats, fills node_id, trims strings, caps the list. Never raises."""
    out: list[dict] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            continue
        node_id = _uuid_str(item.get("node_id")) or str(uuid4())
        out.append(
            PlannedBeat(
                node_id=node_id,
                title=str(item["title"]).strip()[:500],
                summary=str(item.get("summary") or "").strip()[:2000],
                character_ids=[
                    c
                    for c in (_uuid_str(x) for x in item.get("character_ids") or [])
                    if c
                ],
                secondary_names=[
                    str(n).strip()[:100]
                    for n in (item.get("secondary_names") or [])
                    if str(n).strip()
                ][:MAX_SECONDARY_NAMES],
                setting=str(item.get("setting") or "").strip()[:500],
                tone=str(item.get("tone") or "").strip()[:200],
            ).model_dump()
        )
    return out[:MAX_PLANNED_BEATS]


def _uuid_str(value: Any) -> Optional[str]:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None
