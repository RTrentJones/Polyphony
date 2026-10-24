"""SPEC-02: continuity sees the whole canon, never cuts, and never reports an
unchecked section as clean."""

import json
import types
from pathlib import Path

import pytest

from app.core.llm_text import STORY_MATERIAL_NOTICE
from app.core.orm_models import CanonEntry, Chapter, Character, StyleGuide
from app.planning import continuity as cont

pytestmark = pytest.mark.unit


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.formats: list = []

    async def generate(self, messages, *, response_format=None, **kw):
        self.prompts.append(messages[0]["content"])
        self.formats.append(response_format)
        text = self.replies.pop(0) if self.replies else '{"findings": []}'
        return types.SimpleNamespace(text=text, tokens_in=10, tokens_out=5)


_TAIL = "TAIL-MARKER: the CEL is the Chief Executive Lich."


async def test_fact_sheet_is_the_whole_canon_plus_plot_state(async_session, test_book):
    test_book.synopsis = ("Attrition. " * 2500) + _TAIL  # ~27k chars
    async_session.add_all(
        [
            Character(
                book_id=test_book.id,
                user_id=test_book.user_id,
                name="Milo Voss",
                role="protagonist",
                arc="numb to awake",
                relationships={"Zara Okafor": "recruiter"},
            ),
            CanonEntry(book_id=test_book.id, name="Aeon Holdings", category="org"),
            StyleGuide(book_id=test_book.id, tense="present"),
            Chapter(
                book_id=test_book.id,
                title="Onboarding",
                position=0,
                summary="Milo is retained.",
            ),
        ]
    )
    await async_session.commit()
    sheet = await cont.build_fact_sheet(async_session, test_book.id)
    for needle in (
        _TAIL,
        "numb to awake",
        "recruiter",
        "Aeon Holdings",
        "Tense: present",
        "Milo is retained.",
    ):
        assert needle in sheet, needle
    assert "<plot_state>" in sheet


async def test_fact_sheet_indents_planned_beats_under_their_chapter(
    async_session, test_book
):
    """SPEC-23: a chapter heading, its summary, then its beats indented; no
    "- Ch" line inside a beat list."""
    async_session.add_all(
        [
            Chapter(
                book_id=test_book.id,
                title="Onboarding",
                position=0,
                summary="Milo is retained.",
                planned_beats=[
                    {"title": "Intake", "summary": "Milo signs"},
                    {"title": "Lunch", "summary": ""},
                ],
            ),
            Chapter(
                book_id=test_book.id,
                title="Overtime",
                position=1,
                summary="Zara stays late.",
                planned_beats=[{"title": "Memo", "summary": "a decree"}],
            ),
        ]
    )
    await async_session.commit()
    sheet = await cont.build_fact_sheet(async_session, test_book.id)
    plot = sheet[sheet.index("Chapter summaries so far:") :]
    lines = plot.split("\n")
    at = lines.index("Ch 1: Onboarding")
    assert lines[at + 1 : at + 4] == [
        "  Milo is retained.",
        "  - Intake: Milo signs",
        "  - Lunch:",
    ]
    at = lines.index("Ch 2: Overtime")
    assert lines[at + 1 : at + 3] == ["  Zara stays late.", "  - Memo: a decree"]
    assert "Planned beats:" not in plot
    assert not any(line.lstrip().startswith("- Ch") for line in lines)


def test_planning_never_imports_jobs():
    """Layering: app/planning sits below app/jobs (review S1)."""
    planning = Path(cont.__file__).parent
    assert not [p.name for p in planning.glob("*.py") if "app.jobs" in p.read_text()]


def _scene(n: int, text: str) -> "cont.ProseScene":
    return cont.ProseScene(f"s{n}", 1, "Onboarding", n, f"Scene {n}", text)


def _scenes(n_scenes: int, words: int) -> list:
    return [
        _scene(n, " ".join(f"s{n}w{i}" for i in range(words)))
        for n in range(1, n_scenes + 1)
    ]


def _reply(*findings) -> str:
    return json.dumps({"findings": list(findings)})


async def test_prompt_is_fenced_and_uncut(monkeypatch):
    fake = FakeLLM(['{"findings": []}'])
    monkeypatch.setattr(cont, "get_llm_client", lambda: fake)
    sheet = "<canon>\n" + ("x " * 10000) + _TAIL + "\n</canon>"
    result = await cont.run_continuity_check([_scene(1, "Some prose here.")], sheet)
    prompt = fake.prompts[0]
    assert prompt.startswith(STORY_MATERIAL_NOTICE)
    assert _TAIL in prompt and "<prose>" in prompt
    assert result.status == "completed"


def test_chunks_pack_whole_scenes_each_opening_with_its_marker():
    """(F) SPEC-31: a chunk holds whole scenes, each under its marker."""
    chunks = cont.chunk_scenes(_scenes(3, 1000))
    assert [[s.scene_id for s in c.scenes] for c in chunks] == [["s1", "s2"], ["s3"]]
    assert '\n\n[Ch 1 "Onboarding" › Scene 2 "Scene 2"]\ns2w0 ' in chunks[0].text
    assert chunks[1].text.startswith('[Ch 1 "Onboarding" › Scene 3 "Scene 3"]\n')


@pytest.mark.parametrize("para_words", [500, 6000])
def test_a_long_scene_splits_into_marked_pieces(para_words):
    """(F) A 6,000-word scene gives three pieces, each under the scene's
    marker, split at paragraph breaks (kept), by words only inside an
    over-long paragraph."""
    para = " ".join(["w"] * para_words)
    chunks = cont.chunk_scenes([_scene(1, "\n\n".join([para] * (6000 // para_words)))])
    assert len(chunks) == 3
    for c in chunks:
        assert c.text.startswith('[Ch 1 "Onboarding" › Scene 1 "Scene 1"]\nw w')
    assert sum(len(c.scenes[0].text.split()) for c in chunks) == 6000
    assert ("\n\n" in chunks[0].text.split("]\n", 1)[1]) == (para_words == 500)


def test_the_server_locates_the_quote_and_never_drops_a_finding():
    """(F) The model gives `quote`; the server sets `scene_id` from the scene
    whose prose contains it, whitespace-normalised and outer quotation marks
    stripped. A paraphrase, or no quote, is kept unlocated."""
    s2 = _scene(2, "Zara's eyes were green\nas the ledger\n\nlamp.")
    chunk = cont.chunk_scenes([_scene(1, "Milo signed."), s2])[0]
    found = cont.validate_findings(
        [
            {"detail": "Brown in ch 1.", "quote": '"green as the ledger lamp."'},
            {"detail": "Paraphrased.", "quote": "Zara has green eyes"},
            {"detail": "No quote."},
        ],
        chunk,
    )
    assert [(f["scene_id"], f["quote"]) for f in found] == [
        ("s2", '"green as the ledger lamp."'),
        (None, "Zara has green eyes"),
        (None, ""),
    ]
    assert all("refs" not in f and "located" not in f for f in found)


async def test_absent_blocks_are_never_mentioned(monkeypatch):
    """(F) The first chunk has no <prior> and no earlier findings, so its
    prompt neither carries nor mentions them; the second has both."""
    fake = FakeLLM([_reply({"detail": "The lamp vanishes."})])
    monkeypatch.setattr(cont, "get_llm_client", lambda: fake)
    await cont.run_continuity_check(_scenes(2, 2000), "<canon>f</canon>")
    one, two = fake.prompts
    for absent in ("prior", "earlier", "already reported", "previous section"):
        assert absent not in one.lower(), absent
    assert "<prior>" in two and "<earlier_findings>" in two
    assert "The lamp vanishes." in two


async def test_a_pre_spec31_checkpoint_restarts_at_chunk_zero(monkeypatch):
    """(F, S2) A check paused before the deploy saved the hash of the joined
    prose and a word-chunk index. The same prose, chunked by scene, must not
    match it: the check restarts at chunk 0 and drops the old findings."""
    import hashlib

    scenes = _scenes(3, 2000)
    prose = "\n\n".join(s.text for s in scenes)  # the pre-SPEC-31 input
    old_hash = hashlib.sha256(f"facts\x00{prose}".encode()).hexdigest()[:16]
    old = {"type": "other", "severity": "minor", "detail": "old", "refs": "w1"}
    checkpoint = {"input_hash": old_hash, "next": 2, "findings": [old], "checked": 2}
    fake = FakeLLM([])
    monkeypatch.setattr(cont, "get_llm_client", lambda: fake)
    result = await cont.run_continuity_check(scenes, "facts", checkpoint=checkpoint)
    assert len(fake.prompts) == result.chunks_total == 3
    assert result.findings == [] and result.chunks_checked == 3


async def test_unparseable_chunk_is_incomplete_never_clean(monkeypatch):
    # chunk 1 ok, chunk 2 garbage twice (retry in json mode), chunk 3 ok
    fake = FakeLLM(['{"findings": []}', "not json", "still not", '{"findings": []}'])
    monkeypatch.setattr(cont, "get_llm_client", lambda: fake)
    result = await cont.run_continuity_check(_scenes(3, 2000), "<canon>facts</canon>")
    assert result.chunks_total == 3 and result.chunks_checked == 2
    assert result.status == "incomplete"
    assert result.coverage["unchecked"] == ["part 2/3"]
    # (F) D11: JSON mode on both attempts; the retry stays a retry.
    assert fake.formats == [{"type": "json_object"}] * 4


async def test_all_unparseable_is_failed(monkeypatch):
    fake = FakeLLM(["nope", "nope"])
    monkeypatch.setattr(cont, "get_llm_client", lambda: fake)
    result = await cont.run_continuity_check([_scene(1, "short")], "<canon>f</canon>")
    assert result.status == "failed"


async def test_duplicates_merge_keeping_highest_severity(monkeypatch):
    """Same type and detail, or the same quote: one finding, most severe."""
    f = {"type": "character", "detail": "Milo's eyes change colour."}
    q = {"type": "object", "detail": "The ledger moves.", "quote": "s2w1 s2w2"}
    fake = FakeLLM(
        [
            _reply({**f, "severity": "minor"}),
            _reply({**f, "severity": "critical"}, q, {**q, "detail": "Reworded."}),
        ]
    )
    monkeypatch.setattr(cont, "get_llm_client", lambda: fake)
    result = await cont.run_continuity_check(_scenes(2, 2000), "<canon>f</canon>")
    assert [(x["detail"], x["severity"]) for x in result.findings] == [
        ("Milo's eyes change colour.", "critical"),
        ("The ledger moves.", "minor"),
    ]


def test_no_slice_of_the_fact_sheet_or_prose():
    import pathlib

    import ast

    tree = ast.parse(pathlib.Path("app/planning/continuity.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            target = ast.unparse(node.value)
            assert target not in ("fact_sheet", "chunk", "prose", "prompt"), target


async def test_a_paused_check_resumes_at_its_next_chunk(monkeypatch):
    """A quota pause mid-check keeps the chunks already paid for (SPEC-08):
    the resumed run calls the model only for the rest (Codex review on #34)."""
    from app.llm.errors import QuotaExhaustedError

    calls: list[str] = []

    class Fake:
        fail_at = 2

        async def generate(self, messages, **kw):
            n = len(calls) + 1
            if n == self.fail_at:
                self.fail_at = None
                raise QuotaExhaustedError("429", reset_after=30)
            calls.append(messages[0]["content"])
            return types.SimpleNamespace(
                text=json.dumps({"findings": [{"type": "other", "detail": f"f{n}"}]}),
                tokens_in=1,
                tokens_out=1,
            )

    fake = Fake()
    monkeypatch.setattr(cont, "get_llm_client", lambda: fake)
    scenes = _scenes(3, 2000)
    saved: dict = {}

    async def save(data):
        saved.clear()
        saved.update(json.loads(json.dumps(data)))

    with pytest.raises(QuotaExhaustedError):
        await cont.run_continuity_check(scenes, "facts", save_checkpoint=save)
    assert len(calls) == 1 and saved["next"] == 1

    result = await cont.run_continuity_check(
        scenes, "facts", checkpoint=saved, save_checkpoint=save
    )
    assert len(calls) == 3  # chunks 2 and 3 only; chunk 1 was not re-paid
    assert result.chunks_checked == 3 and result.chunks_total == 3
    assert [f["detail"] for f in result.findings] == ["f1", "f2", "f3"]

    # A checkpoint taken against other prose is discarded, not merged.
    calls.clear()
    edited = [scenes[0]._replace(text="x" + scenes[0].text), *scenes[1:]]
    await cont.run_continuity_check(edited, "facts", checkpoint=saved)
    assert len(calls) == 3


async def test_the_outline_check_reads_the_live_sources_oldest_first(
    async_session, test_book
):
    """(F) SPEC-73: live sources with text, by upload; trashed or empty ones never."""
    from datetime import datetime, timedelta, timezone

    from app.core.orm_models import Source

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [("later", 3, None), ("first", 0, None), ("gone", 1, t0), ("  ", 2, None)]
    async_session.add_all(
        Source(
            book_id=test_book.id,
            user_id=test_book.user_id,
            title=title,
            content_text=title,
            uploaded_at=t0 + timedelta(days=day),
            deleted_at=gone,
        )
        for title, day, gone in rows
    )
    await async_session.commit()
    assert await cont.source_texts(async_session, test_book.id) == ["first", "later"]


_LETTERS = "\n\n".join(
    f"{place}, {day}, 17—.\n\nI write to you, my sister."
    for place, day in [
        ("St. Novaskol", "Dec. 11th"),
        ("Arkosk", "28th March"),
        ("At sea", "July 7th"),
        ("Aboard", "August 5th"),
        ("Arkosk", "August 19th"),
    ]
)


def test_the_era_ledger_flags_a_date_a_century_off():
    """(F) W15-7: a date in the first chunk is checked against every other
    date in the prose, deterministically, with a verbatim quote."""
    flipped = _LETTERS.replace("Dec. 11th, 17—", "Dec. 11th, 18—")
    (finding,) = cont.era_findings([_scene(1, flipped)])
    assert finding["type"] == "timeline" and finding["scene_id"] == "s1"
    assert "Dec. 11th, 18—" in finding["quote"] and finding["quote"] in " ".join(
        flipped.split()
    )
    assert "1700s" in finding["detail"]
    dated = " ".join(
        f"On May {d}, {y}, I wrote home." for d, y in enumerate([1797] * 4 + [1897], 1)
    )
    assert len(cont.era_findings([_scene(1, dated)])) == 1  # "1797," still counts


@pytest.mark.parametrize(
    "text",
    [
        _LETTERS,  # one era throughout
        " ".join(f"March {d}, 2023." for d in range(1, 6)) + " Born in 1987.",
        _LETTERS + "\n\nIn May 1500 men marched; in March 2000 guests came.",
        _LETTERS.replace("17—", "1889") + " Built in the sixteenth century.",
        "Paris, 1889.\n\nMay 1790. June 1890.",  # too few dates to vote
        _LETTERS + '\n\n"In the year 1492 Columbus sailed," he said.',  # W16-5
        _LETTERS + "\n\nIn May 1500 soldiers marched.",
        _LETTERS + "\n\nIn June 1600 more came.",
    ],
)
def test_the_era_ledger_is_silent_without_a_clear_majority(text):
    assert cont.era_findings([_scene(1, text)]) == []


async def test_era_findings_reach_the_report_and_the_model(monkeypatch):
    fake = FakeLLM(['{"findings": []}'])
    monkeypatch.setattr(cont, "get_llm_client", lambda: fake)
    flipped = _LETTERS.replace("Arkosk, 28th March, 17—", "Arkosk, 28th March, 18—")
    result = await cont.run_continuity_check([_scene(1, flipped)], "<canon>f</canon>")
    assert [f["type"] for f in result.findings] == ["timeline"]
    assert "<earlier_findings>" in fake.prompts[0]
