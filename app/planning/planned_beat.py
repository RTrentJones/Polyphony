"""Planned-beat contract (wave 2: SPEC-23 persists it, SPEC-24 produces it).

A promoted chapter's planned beats are DATA (`chapters.planned_beats`), not
"Planned beats:" lines inside its summary. Frozen for the wave; changes go
through the orchestrator.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, NamedTuple, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

MAX_PLANNED_BEATS = 12
MAX_SECONDARY_NAMES = 12

# The block promote wrote into a chapter summary before SPEC-23.
LEGACY_MARKER = "Planned beats:"
_LEGACY_BLOCK = re.compile(rf"(?:\A|\n\n){re.escape(LEGACY_MARKER)}\n")


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
    before the migration were linked to their beats by migration 0019
    (scene_request.planned_beat_id)."""
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


def unwritten_beats(
    beats: list[dict], written_ids: Iterable[Optional[str]]
) -> list[dict]:
    """The beats, in order, whose node_id is not among `written_ids` (the
    planned_beat_id of each live, not failed/cancelled scene in the chapter;
    None entries are ignored). Identity is the id only: renaming or
    re-summarising a beat never makes its scene unwritten (review B3)."""
    written = {str(i) for i in written_ids if i is not None}
    return [b for b in beats if str(b.get("node_id")) not in written]


# --- legacy "Planned beats:" summaries (migration 0019 and chapter restore) ----


class LegacyLine(NamedTuple):
    head: (
        str  # the "- " line without "- ", stripped: what pre-wave-2 code generated from
    )
    text: str  # head plus its continuation lines, joined with single spaces


class LegacySummary(NamedTuple):
    text: str  # the summary with the block removed, stripped
    lines: list[LegacyLine]


def split_legacy_summary(summary: Optional[str]) -> Optional[LegacySummary]:
    """The "Planned beats:" block promote wrote before SPEC-23, or None.

    A block only in promote's format: "Planned beats:" alone on its line (a
    newline right after the colon) at the very start of the summary or after a
    blank line ("\\n\\n"); an empty node summary made promote's `.strip()` put it
    at offset 0. The block runs from that line to the first blank line or the
    end. Inside it, a line starting with "- " opens a beat; any other non-blank
    line continues the previous beat (a child summary with newlines, review
    PR-S10), so no beat is ever de-planned. A "- " line whose text is empty
    after rstrip(":") (e.g. "- :") is dropped. Text after the block's ending
    blank line is kept: `text` = head + "\\n\\n" + tail, stripped. A
    hand-written "Planned beats: TBD. Then..." is not a block. Used ONLY by
    migration 0019 (inline copy, parity-tested) and the chapter restore path.

    Promote always wrote a "- " line first, so a block whose first line is not
    one ("Planned beats:\\nTBD") is hand-written, not a block: None."""
    if not isinstance(summary, str):
        return None
    m = _LEGACY_BLOCK.search(summary)
    if m is None:
        return None
    rows = summary[m.end() :].split("\n")
    if not rows[0].strip().startswith("- "):
        return None
    end = next((i for i, row in enumerate(rows) if not row.strip()), len(rows))
    tail = "\n".join(rows[end + 1 :])
    beats: list[list[str]] = []
    current: Optional[list[str]] = None
    for row in rows[:end]:
        line = row.strip()
        if line.startswith("- "):
            head = line[2:].strip()
            current = [head] if head.rstrip(":").strip() else None
            if current is not None:
                beats.append(current)
        elif current is not None:
            current.append(line)
    return LegacySummary(
        text=(summary[: m.start()] + "\n\n" + tail).strip(),
        lines=[LegacyLine(head=b[0], text=" ".join(b)) for b in beats],
    )


def beat_from_line(line: str) -> dict:
    """{"title", "summary"} from "title: summary", split on the first ": "
    (beat_scene_description rebuilds the line exactly); "title:" or a bare
    line is a title with "" summary."""
    if ": " in line:
        title, summary = line.split(": ", 1)
    else:
        title, summary = line.rstrip(":"), ""
    return {"title": title.strip(), "summary": summary.strip()}


def _uuid_str(value: Any) -> Optional[str]:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None
