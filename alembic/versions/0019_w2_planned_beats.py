"""Planned beats as data on the chapter (SPEC-23, wave 2 W2-1).

`chapters.planned_beats` (JSONB, nullable) holds the chapter's planned scenes
as a list of PlannedBeat dicts (app/planning/planned_beat.py). Promote used to
write them as "Planned beats:" lines inside the chapter summary, so editing the
summary silently changed what generate-planned wrote.

upgrade:
  1. add the column;
  2. backfill every chapter whose summary holds "Planned beats:": from its
     outline node's children when `plan_node_id` still matches a node of the
     book's outline (node_ids and any new fields kept), else by parsing the
     "- title: summary" lines (new uuid4 node_ids). The summary keeps only the
     text before "\\n\\nPlanned beats:";
  3. strip a leading "Chapter N:" from promoted chapters' titles (the export
     heading numbers chapters by position), unless that would empty it.
downgrade: re-append the beats to the summary as "Planned beats:" lines and
drop the column. The title strip is NOT reversed (SPEC-23).

postgres-only and guarded, like 0017/0018: 0001 runs `create_all`, so a fresh
database already has the column when this runs. Migrations do not import app
code, so the beat normalization and the title regex are copied inline.

Revision ID: w2_planned_beats
Revises: b3_scene_revise
Create Date: 2026-10-15

"""

import json
import re
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w2_planned_beats"
down_revision: Union[str, None] = "b3_scene_revise"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MARKER = "Planned beats:"
MAX_BEATS = 12
MAX_SECONDARY_NAMES = 12

# Same as app.exports.builder._LEADING_CHAPTER (clean_chapter_title).
_NUMBER_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    "fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty"
)
_LEADING_CHAPTER = re.compile(
    r"^\s*chapter\s+(?:\d+(?:\s*[:.\-–—]\s*|\s+|\s*$)"
    rf"|(?:{_NUMBER_WORDS}|[ivxlc]+)(?:\s*[:.\-–—]\s*|\s*$))",
    re.IGNORECASE,
)


def _uuid_str(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _beat(item: dict) -> dict:
    """normalize_planned_beats for one item (the caller drops untitled ones)."""
    return {
        "node_id": _uuid_str(item.get("node_id")) or str(uuid.uuid4()),
        "title": str(item.get("title") or "").strip()[:500],
        "summary": str(item.get("summary") or "").strip()[:2000],
        "character_ids": [
            c for c in (_uuid_str(x) for x in item.get("character_ids") or []) if c
        ],
        "secondary_names": [
            str(n).strip()[:100]
            for n in (item.get("secondary_names") or [])
            if str(n).strip()
        ][:MAX_SECONDARY_NAMES],
        "setting": str(item.get("setting") or "").strip()[:500],
        "tone": str(item.get("tone") or "").strip()[:200],
    }


def _beats(items) -> list:
    return [
        _beat(i)
        for i in (items if isinstance(items, list) else [])
        if isinstance(i, dict) and str(i.get("title") or "").strip()
    ][:MAX_BEATS]


def _split_summary(summary: str):
    """(summary before the marker, the "- " lines after it), or None."""
    at = summary.find(MARKER)
    if at < 0:
        return None
    head = summary[:at]
    lines = []
    for line in summary[at + len(MARKER) :].split("\n"):
        line = line.strip()
        if line.startswith("- ") and line[2:].strip().rstrip(":").strip():
            lines.append(line[2:].strip())
    return head.strip(), lines


def _parse_line(line: str) -> dict:
    """ "title: summary" (split on the first ": "); "title:" or a bare line is a
    title with an empty summary."""
    if ": " in line:
        title, summary = line.split(": ", 1)
    else:
        title, summary = line.rstrip(":"), ""
    return {"title": title.strip(), "summary": summary.strip()}


def _load(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE chapters ADD COLUMN IF NOT EXISTS planned_beats JSONB")

    outlines: dict = {}
    for book_id, content in bind.execute(
        sa.text("SELECT book_id, content FROM book_plans WHERE kind = 'outline'")
    ).fetchall():
        nodes = _load(content)
        outlines[str(book_id)] = {
            str(n.get("node_id")): n
            for n in (nodes if isinstance(nodes, list) else [])
            if isinstance(n, dict) and n.get("node_id")
        }

    rows = bind.execute(
        sa.text(
            "SELECT id, book_id, summary, plan_node_id FROM chapters "
            "WHERE summary LIKE :marker AND planned_beats IS NULL"
        ),
        {"marker": f"%{MARKER}%"},
    ).fetchall()
    for chapter_id, book_id, summary, plan_node_id in rows:
        split = _split_summary(summary or "")
        if split is None:
            continue
        head, lines = split
        node = (
            outlines.get(str(book_id), {}).get(str(plan_node_id))
            if plan_node_id is not None
            else None
        )
        beats = _beats(node.get("children")) if node else []
        if not beats:
            beats = _beats([_parse_line(line) for line in lines])
        bind.execute(
            sa.text(
                "UPDATE chapters SET summary = :s, "
                "planned_beats = CAST(:b AS jsonb) WHERE id = :id"
            ),
            {"s": head, "b": json.dumps(beats), "id": chapter_id},
        )

    for chapter_id, title in bind.execute(
        sa.text("SELECT id, title FROM chapters WHERE plan_node_id IS NOT NULL")
    ).fetchall():
        clean = _LEADING_CHAPTER.sub("", title or "", count=1).strip()
        if clean and clean != title:
            bind.execute(
                sa.text("UPDATE chapters SET title = :t WHERE id = :id"),
                {"t": clean, "id": chapter_id},
            )


def downgrade() -> None:
    # Promoted titles keep their stripped form (the "Chapter N:" prefix is not
    # recoverable and older code does not need it).
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    rows = bind.execute(
        sa.text(
            "SELECT id, summary, planned_beats FROM chapters "
            "WHERE planned_beats IS NOT NULL"
        )
    ).fetchall()
    for chapter_id, summary, raw in rows:
        beats = _beats(_load(raw))
        if not beats:
            continue
        lines = "\n".join(f"- {b['title']}: {b['summary']}" for b in beats)
        bind.execute(
            sa.text("UPDATE chapters SET summary = :s WHERE id = :id"),
            {
                "s": f"{summary or ''}\n\n{MARKER}\n{lines}".strip(),
                "id": chapter_id,
            },
        )
    op.execute("ALTER TABLE chapters DROP COLUMN IF EXISTS planned_beats")
