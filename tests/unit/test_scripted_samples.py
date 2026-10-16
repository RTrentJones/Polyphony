"""SPEC-46: a voice sample that scripts the beat line or the brief is not
eligible, so it goes before the threshold and `k` (gated on the fixture)."""

import json
import types
import uuid
from pathlib import Path

import app.characters.context as ctx
from tests.unit.factories import make_scope

FIXTURE = Path(__file__).parent.parent / "fixtures" / "w5_archives.json"
SCRIPTED = json.loads(FIXTURE.read_text())["scripted"]["rows"]


class Store:  # nearest first (as given), then `keep`, then `k`
    def __init__(self, texts):
        self.rows = [{"id": i, "text": t, "chunk_type": "dialogue"} for i, t in texts]

    async def retrieve_similar(self, *, chunk_type=None, k=5, keep=None, **kw):
        return [s for s in self.rows if keep is None or keep(s)][:k]


async def _shown(monkeypatch, texts, row, k=3):
    monkeypatch.setattr(ctx, "get_chunk_store", lambda: Store(enumerate(texts)))
    who = types.SimpleNamespace(id=uuid.uuid4(), name="Zara", user_id=None)
    who.book_id = None
    scope = make_scope(plot=(row["brief"], "the scene's next beat line"))
    words, _ = await ctx.character_samples(
        who, row["beat"], k, scope=scope, threshold=ctx.settings.RAG_SCORE_THRESHOLD
    )
    return [s["text"] for s in words]


async def test_a_sample_that_scripts_the_beat_line_or_brief_is_dropped(monkeypatch):
    """(F) The seven folded-6 hits drop; (R) w5c 0027/0029 keep Zara's queue
    line, which only the scene's next beat scripts."""
    for row in SCRIPTED:
        shown = await _shown(monkeypatch, [row["sample"]], row)
        assert (shown == []) is row["drop"], (row["run"], row["record"], row["who"])


async def test_after_a_drop_the_next_eligible_line_is_shown(monkeypatch):
    """(F) The scripted line is nearest, yet the next line fills the one slot."""
    row, other = SCRIPTED[-1], "Read the appendices. Thank no one."
    assert await _shown(monkeypatch, [row["sample"], other], row, k=1) == [other]
