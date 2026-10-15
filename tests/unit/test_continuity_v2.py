"""SPEC-02: continuity sees the whole canon, never cuts, and never reports an
unchecked section as clean."""

import json
import types

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
    "- Ch" line inside a beat list, for data beats and legacy summary text."""
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
                summary="Zara stays late.\n\nPlanned beats:\n- Memo: a decree",
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


async def test_prompt_is_fenced_and_uncut(monkeypatch):
    fake = FakeLLM(['{"findings": []}'])
    monkeypatch.setattr(cont, "get_llm_client", lambda: fake)
    sheet = "<canon>\n" + ("x " * 10000) + _TAIL + "\n</canon>"
    result = await cont.run_continuity_check("Some prose here.", sheet)
    prompt = fake.prompts[0]
    assert prompt.startswith(STORY_MATERIAL_NOTICE)
    assert _TAIL in prompt and "<prose>" in prompt
    assert result.status == "completed"


async def test_unparseable_chunk_is_incomplete_never_clean(monkeypatch):
    words = " ".join(f"w{i}" for i in range(cont.CHUNK_WORDS * 3))
    # chunk 1 ok, chunk 2 garbage twice (retry in json mode), chunk 3 ok
    fake = FakeLLM(['{"findings": []}', "not json", "still not", '{"findings": []}'])
    monkeypatch.setattr(cont, "get_llm_client", lambda: fake)
    result = await cont.run_continuity_check(words, "<canon>facts</canon>")
    assert result.chunks_total == 3 and result.chunks_checked == 2
    assert result.status == "incomplete"
    assert result.coverage["unchecked"] == ["part 2/3"]
    assert fake.formats[2] == {"type": "json_object"}  # the strict retry


async def test_all_unparseable_is_failed(monkeypatch):
    fake = FakeLLM(["nope", "nope"])
    monkeypatch.setattr(cont, "get_llm_client", lambda: fake)
    result = await cont.run_continuity_check("short prose", "<canon>f</canon>")
    assert result.status == "failed"


async def test_duplicates_merge_keeping_highest_severity(monkeypatch):
    words = " ".join(f"w{i}" for i in range(cont.CHUNK_WORDS * 2))
    f = {"type": "character", "detail": "Milo's eyes change colour.", "refs": "ch2"}
    fake = FakeLLM(
        [
            json.dumps({"findings": [{**f, "severity": "minor"}]}),
            json.dumps({"findings": [{**f, "severity": "critical"}]}),
        ]
    )
    monkeypatch.setattr(cont, "get_llm_client", lambda: fake)
    result = await cont.run_continuity_check(words, "<canon>f</canon>")
    assert len(result.findings) == 1 and result.findings[0]["severity"] == "critical"
    # the second chunk saw the first chunk's findings and its tail
    assert "<earlier_findings>" in fake.prompts[1] and "<prior>" in fake.prompts[1]


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
    prose = " ".join(f"w{i}" for i in range(cont.CHUNK_WORDS * 3))
    saved: dict = {}

    async def save(data):
        saved.clear()
        saved.update(json.loads(json.dumps(data)))

    with pytest.raises(QuotaExhaustedError):
        await cont.run_continuity_check(prose, "facts", save_checkpoint=save)
    assert len(calls) == 1 and saved["next"] == 1

    result = await cont.run_continuity_check(
        prose, "facts", checkpoint=saved, save_checkpoint=save
    )
    assert len(calls) == 3  # chunks 2 and 3 only; chunk 1 was not re-paid
    assert result.chunks_checked == 3 and result.chunks_total == 3
    assert [f["detail"] for f in result.findings] == ["f1", "f2", "f3"]

    # A checkpoint taken against other prose is discarded, not merged.
    calls.clear()
    await cont.run_continuity_check(
        prose.replace("w0 ", "x0 ", 1), "facts", checkpoint=saved
    )
    assert len(calls) == 3
