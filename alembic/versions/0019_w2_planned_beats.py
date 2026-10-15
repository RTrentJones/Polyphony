"""Planned beats as data on the chapter (SPEC-23, wave 2 W2-1).

`chapters.planned_beats` (JSONB, nullable) holds the chapter's planned scenes
as a list of PlannedBeat dicts (app/planning/planned_beat.py). Promote used to
write them as "Planned beats:" lines inside the chapter summary, so editing the
summary silently changed what generate-planned wrote.

upgrade: add the column; convert each promote-format "Planned beats:" block
(the `planned_beat.split_legacy_summary` rule) into beats parsed from its lines
only (the author may have edited them; new uuid4 ids, no cast/setting/tone),
leaving the rest of the summary; link each converted chapter's live scenes to
their beats once (`scene_request.planned_beat_id`, by occurrence on the line);
strip a leading "Chapter N:" from promoted titles unless that empties them; log
the counts. downgrade: re-append the lines and drop the column; scenes keep
their beat ids and titles stay stripped (SPEC-23).

postgres-only and guarded, like 0017/0018 (0001 runs `create_all`). Migrations
do not import app code, so the parser, the caps and the title regex are copied
inline (tests/integration/test_pg_planned_beats.py checks the parser copy).

Revision ID: w2_planned_beats
Revises: b3_scene_revise
Create Date: 2026-10-15

"""

import json
import logging
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


_BLOCK = re.compile(rf"(?:\A|\n\n){re.escape(MARKER)}\n")


def _split(summary):
    """Inline copy of app.planning.planned_beat.split_legacy_summary:
    (text, [(head, text)]) for a promote-format block, else None."""
    if not isinstance(summary, str):
        return None
    m = _BLOCK.search(summary)
    if m is None:
        return None
    rows = summary[m.end() :].split("\n")
    if not rows[0].strip().startswith("- "):
        return None
    end = next((i for i, row in enumerate(rows) if not row.strip()), len(rows))
    tail = "\n".join(rows[end + 1 :])
    beats = []
    current = None
    for row in rows[:end]:
        line = row.strip()
        if line.startswith("- "):
            head = line[2:].strip()
            current = [head] if head.rstrip(":").strip() else None
            if current is not None:
                beats.append(current)
        elif current is not None:
            current.append(line)
    text = (summary[: m.start()] + "\n\n" + tail).strip()
    return text, [(b[0], " ".join(b)) for b in beats]


def _beat(line: str):
    """normalize_planned_beats(beat_from_line(line)) for one line: a new beat
    with a uuid4 id, or None when it has no title."""
    if ": " in line:
        title, summary = line.split(": ", 1)
    else:
        title, summary = line.rstrip(":"), ""
    if not title.strip():
        return None
    return {
        "node_id": str(uuid.uuid4()),
        "title": title.strip()[:500],
        "summary": summary.strip()[:2000],
        "character_ids": [],
        "secondary_names": [],
        "setting": "",
        "tone": "",
    }


def _key(line) -> str:
    # "Lunch:" (a titled beat with no summary) and "Lunch" are one line.
    return (line or "").strip().rstrip(":").strip()


def _load(value):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return None


def _link_scenes(bind, chapter_id, open_beats: list) -> int:
    """Give each (line key, beat id) to the first live scene written from it."""
    linked = 0
    for scene_id, description, raw in bind.execute(
        sa.text(
            "SELECT id, scene_description, CAST(scene_request AS text) FROM scenes "
            "WHERE chapter_id = :c AND deleted_at IS NULL "
            "AND status NOT IN ('failed', 'cancelled') ORDER BY position"
        ),
        {"c": chapter_id},
    ).fetchall():
        request = r if isinstance(r := _load(raw), dict) else {}
        keys = [key for key, _ in open_beats]
        if request.get("planned_beat_id") or _key(description) not in keys:
            continue
        _, beat_id = open_beats.pop(keys.index(_key(description)))
        bind.execute(
            sa.text("UPDATE scenes SET scene_request = CAST(:r AS json) WHERE id = :i"),
            {"r": json.dumps({**request, "planned_beat_id": beat_id}), "i": scene_id},
        )
        linked += 1
    return linked


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE chapters ADD COLUMN IF NOT EXISTS planned_beats JSONB")

    converted = skipped = linked = beat_count = 0
    rows = bind.execute(
        sa.text(
            "SELECT id, summary FROM chapters "
            "WHERE summary LIKE :marker AND planned_beats IS NULL"
        ),
        {"marker": f"%{MARKER}%"},
    ).fetchall()
    for chapter_id, summary in rows:
        split = _split(summary)
        if split is None:
            skipped += 1
            continue
        text, lines = split
        pairs = [(h, b) for h, b in ((h, _beat(t)) for h, t in lines) if b][:MAX_BEATS]
        beats = [beat for _, beat in pairs]
        bind.execute(
            sa.text(
                "UPDATE chapters SET summary = :s, "
                "planned_beats = CAST(:b AS jsonb) WHERE id = :id"
            ),
            {"s": text, "b": json.dumps(beats), "id": chapter_id},
        )
        converted += 1
        beat_count += len(beats)
        linked += _link_scenes(
            bind, chapter_id, [(_key(head), b["node_id"]) for head, b in pairs]
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
    logging.getLogger("alembic.runtime.migration").info(
        f"0019 planned beats: converted={converted} skipped={skipped} "
        f"scenes_linked={linked} beats={beat_count}"
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
        beats = [
            b
            for b in (_load(raw) or [])
            if isinstance(b, dict) and str(b.get("title") or "").strip()
        ]
        if not beats:
            continue
        lines = "\n".join(
            f"- {str(b['title']).strip()}: {str(b.get('summary') or '').strip()}"
            for b in beats
        )
        bind.execute(
            sa.text("UPDATE chapters SET summary = :s WHERE id = :id"),
            {
                "s": f"{summary or ''}\n\n{MARKER}\n{lines}".strip(),
                "id": chapter_id,
            },
        )
    op.execute("ALTER TABLE chapters DROP COLUMN IF EXISTS planned_beats")
