"""Day-0 contract (wave 2.5 §1a): the prompt markers in app/core/prompt_markers.py.

Each marker is written by the app and parsed by the prompt_contract grader or
the stub LLM. These tests drive the real emitters (scene_block,
plan_scene_beats, a beat prompt through run_prose_scene_workflow, the staged
outline stages, the extraction reduce and the revise prompt) and assert that
every parser built from a marker finds what it should. They pin markers, not
the prose around them, so prompt wording may change freely.
"""

import json
import types

import pytest

from app.core import prompt_markers as pm
from app.core.orm_models import CanonEntry, Chapter, Character, Scene, StyleGuide
from app.core.prompt_markers import POV_LINE_RE, labelled_line
from evals import stub_llm
from evals.graders import prompt_contract as pc

pytestmark = pytest.mark.unit


class FakeLLM:
    def __init__(self, responses: dict):
        self.responses = responses
        self.prompts: dict[str, list[str]] = {}

    async def generate(self, messages, *, purpose, **kw):
        self.prompts.setdefault(purpose, []).append(messages[0]["content"])
        return types.SimpleNamespace(
            text=self.responses.get(purpose, "Prose. " * 20), tokens_in=1, tokens_out=1
        )


def _person(name, aliases=()):
    return types.SimpleNamespace(
        id="00000000-0000-4000-8000-000000000001",
        name=name,
        aliases=list(aliases),
        role="protagonist",
        description="",
        goals="",
        arc="",
        notes="",
        personality_traits=None,
        voice_characteristics=None,
        relationships=None,
    )


REQUEST = {
    "scene_description": "Milo meets Prava at the desk.",
    "setting": "Reconciliations",
    "emotional_tone": "wry",
    "secondary_names": ["Prava"],
    "characters": ["Milo Voss", "Zara Okafor"],
}


# --- the markers themselves ----------------------------------------------------


def test_marker_text_is_exact():
    """The text the archived prompts carry; a change here is a contract change."""
    assert pm.SCENE_DESCRIPTION_LABEL == "Scene description"
    assert pm.SETTING_LABEL == "Setting"
    assert pm.EMOTIONAL_TONE_LABEL == "Emotional tone"
    assert pm.AVAILABLE_CAST_LABEL == "Available cast (not all need appear)"
    assert pm.POV_LABEL == "Point of view"
    assert pm.POV_LIMIT_OPEN == "Narrate only what"
    assert pm.POV_LIMIT_CLOSE == "perceives or knows"
    assert pm.CHARACTERS_SECTION == "CHARACTERS IN THIS BEAT"
    assert pm.BEAT_TO_WRITE_LABEL == "BEAT TO WRITE"
    assert (pm.BEATS_FENCE, pm.PRIOR_FENCE, pm.CHAPTER_FENCE) == (
        "beats",
        "prior",
        "chapter",
    )
    assert pm.CURRENT_BEAT_MARK == "→"
    assert pm.STOP_AT_NEXT_BEAT.format(next=3) == "Stop where beat 3 begins"
    assert pm.NAME_HEADING == "### "
    assert pm.VOICE_SAMPLES_LABEL == (
        "Voice samples (their own words — match cadence, diction, syntax):"
    )
    assert pm.EVIDENCE_LABEL == "Evidence:"
    assert pm.EVIDENCE_ITEM.format(part=2, quote="q") == "- (part 2) q"
    assert pm.QUOTED_LINES_LABEL == "Quoted lines (their own words):"
    assert pm.CONFIRMED_PREMISE_NOTE == (
        " (confirmed by the author: treat it as authoritative)"
    )
    assert pm.ACT_CHAPTERS_HEADER.format(number=2) == (
        "Here are the chapters of act 2, in order:"
    )


def test_markers_module_imports_nothing_from_app():
    import ast
    import inspect

    modules = set()
    for node in ast.walk(ast.parse(inspect.getsource(pm))):
        if isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
        elif isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
    assert modules == {"__future__", "re"}


def test_labelled_line():
    rx = labelled_line(pm.AVAILABLE_CAST_LABEL)
    text = "x\nAvailable cast (not all need appear): Milo, Zara\ny"
    assert rx.search(text).group(1) == "Milo, Zara"
    assert rx.search("Available cast: Milo") is None  # the label is exact


def test_pov_line_re_mode_and_who():
    """S5: a POV character may contain a period ("Dr. Ada Vance")."""
    from app.orchestration.prose import pov_instruction

    m = POV_LINE_RE.search(pov_instruction("first person, Dr. Ada Vance"))
    assert m.group("mode") == "first person"
    assert m.group("who") == "Dr. Ada Vance"
    m = POV_LINE_RE.search(pov_instruction("third person limited"))
    assert m.group("mode") == "third person limited"
    assert m.group("who") is None
    assert POV_LINE_RE.search("Point of view: first person, Milo.") is None


# --- scene_block and plan_scene_beats -------------------------------------------


def test_scene_block_markers():
    from app.orchestration.workflow import scene_block

    block = scene_block(REQUEST)
    assert labelled_line(pm.SCENE_DESCRIPTION_LABEL).search(block).group(1) == (
        "Milo meets Prava at the desk."
    )
    assert labelled_line(pm.SETTING_LABEL).search(block).group(1) == "Reconciliations"
    assert labelled_line(pm.EMOTIONAL_TONE_LABEL).search(block).group(1) == "wry"
    assert pc._SCENE_DESCRIPTION.search(block).group(1) == (
        "Milo meets Prava at the desk."
    )
    assert pc.check_no_blank_setting(block) == []
    blank = scene_block({"scene_description": "x", "emotional_tone": "neutral"})
    assert labelled_line(pm.SETTING_LABEL).search(blank) is None
    assert pc.check_no_neutral_tone(blank) == []


async def test_plan_scene_beats_prompt_markers(monkeypatch):
    from app.orchestration import workflow as wf

    fake = FakeLLM({"plan_beats": "[]"})
    monkeypatch.setattr(wf, "get_llm_client", lambda: fake)
    await wf.plan_scene_beats(
        REQUEST, canon_block="<canon>\nC\n</canon>", chapter_summary="Ch one."
    )
    prompt = fake.prompts["plan_beats"][0]
    assert labelled_line(pm.AVAILABLE_CAST_LABEL).search(prompt).group(1) == (
        "Milo Voss, Zara Okafor"
    )
    assert pc.available_cast(prompt) == ["Milo Voss", "Zara Okafor"]
    assert pc._SCENE_DESCRIPTION.search(prompt).group(1) == (
        "Milo meets Prava at the desk."
    )
    assert pc.block(prompt, pm.CHAPTER_FENCE) == "Ch one."
    # B1: the planner's "Unnamed functional figures" sentence is found
    assert pc.check_plan_cast_available(prompt) == []


def test_b1_unnamed_figures_is_case_insensitive():
    cast = "Available cast (not all need appear): Milo\n"
    assert pc.check_plan_cast_available(cast + "Unnamed functional figures") == []
    assert pc.check_plan_cast_available(cast + "unnamed functional figures") == []
    assert pc.check_plan_cast_available(cast) == [
        'no "unnamed functional figures" allowance'
    ]


# --- a beat prompt through run_prose_scene_workflow ------------------------------


class _Ctx:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *a):
        return False


class _Store:
    async def retrieve_similar(self, **kw):
        return [
            {
                "id": "chunk-1",
                "text": "I'll not be hurried.",
                "score": 0.4,
                "chunk_type": "dialogue",
            }
        ]


async def test_beat_prompt_markers(async_session, test_book, monkeypatch):
    import app.characters.context as ctx_mod
    import app.orchestration.prose as prose_mod
    import app.orchestration.workflow as wf_mod

    milo = Character(book_id=test_book.id, user_id=test_book.user_id, name="Milo Voss")
    zara = Character(
        book_id=test_book.id, user_id=test_book.user_id, name="Zara Okafor"
    )
    chapter = Chapter(
        book_id=test_book.id, title="One", position=0, summary="Milo's first day."
    )
    async_session.add_all(
        [
            milo,
            zara,
            chapter,
            CanonEntry(
                book_id=test_book.id,
                name="Aeon Holdings",
                category="org",
                content="The firm.",
            ),
            StyleGuide(book_id=test_book.id, pov="first person, Milo"),
        ]
    )
    await async_session.commit()
    scene = Scene(
        user_id=test_book.user_id,
        chapter_id=chapter.id,
        position=0,
        status="processing",
    )
    async_session.add(scene)
    await async_session.commit()

    plan = [
        {"description": "They meet.", "characters": ["Milo Voss"]},
        {"description": "They part.", "characters": ["Milo Voss", "Zara Okafor"]},
    ]
    fake = FakeLLM({"plan_beats": json.dumps(plan)})
    monkeypatch.setattr(prose_mod, "get_async_session", lambda: _Ctx(async_session))
    monkeypatch.setattr(prose_mod, "get_llm_client", lambda: fake)
    monkeypatch.setattr(wf_mod, "get_llm_client", lambda: fake)
    monkeypatch.setattr(ctx_mod, "get_chunk_store", lambda: _Store())
    request = {
        "character_ids": [str(milo.id), str(zara.id)],
        "characters": [],
        "scene_description": "Milo meets Zara at the desk.",
        "setting": "Reconciliations",
        "emotional_tone": "wry",
        "target_word_count": 300,
    }
    result = await prose_mod.run_prose_scene_workflow(
        scene.id,
        request,
        test_book.user_id,
        chapter_summary="Milo's first day.",
        book_id=test_book.id,
    )
    assert result["status"] == "completed", result
    first, last = fake.prompts["beat_prose"]

    for prompt in (first, last):
        m = POV_LINE_RE.search(prompt)
        assert (m.group("mode"), m.group("who")) == ("first person", "Milo Voss")
        assert pc.check_pov_line(prompt, "Milo Voss") == []
        assert labelled_line(pm.SCENE_DESCRIPTION_LABEL).search(prompt).group(1) == (
            "Milo meets Zara at the desk."
        )
        assert pc.check_beats_block(prompt) == []
        assert pc.block(prompt, pm.CHAPTER_FENCE) == "Milo's first day."
        section = pc.characters_section(prompt)
        assert section is not None
        assert f"{pm.NAME_HEADING}Milo Voss" in section
        assert pm.BEAT_TO_WRITE_LABEL not in section
        assert pc.voice_samples(prompt)["Milo Voss"] == ["I'll not be hurried."]
        assert stub_llm._HEADING.findall(section)[0] == "Milo Voss"

    # beat 1: marked first, told where to stop; beat 2: marked last, has <prior>
    assert not pc._is_last_beat(pc.block(first, pm.BEATS_FENCE))
    assert pm.STOP_AT_NEXT_BEAT.format(next=2) in first
    assert pc._STOP_AT_NEXT.search(first)
    assert pc.block(first, pm.PRIOR_FENCE) is None
    assert pc._is_last_beat(pc.block(last, pm.BEATS_FENCE))
    assert pc.block(last, pm.PRIOR_FENCE).startswith("Prose.")

    # the grader matches the beat prompt to its outline chapter by the fence
    index = pc.OutlineIndex({})
    index.add(json.dumps([{"title": "One", "summary": "Milo's first day."}]))
    assert index.chapter_for(first)["summary"] == "Milo's first day."


# --- the staged outline, the extraction reduce and the revise prompt ------------


async def test_outline_prompt_markers(monkeypatch):
    from app.planning import staged_outline as so
    from app.planning.canon import Canon

    canon = Canon(
        title="T", synopsis="Milo Voss works.", characters=[_person("Milo Voss")]
    )
    fake = FakeLLM({"outline:chapters": "[]", "outline:beats": "[]"})
    monkeypatch.setattr(so, "get_llm_client", lambda: fake)
    skeleton = {
        "confirmed": True,
        "premise_restated": "Milo wakes up.",
        "central_conflict": "C.",
        "acts": [{"title": "A"}],
    }
    await so._stage_chapters(canon, skeleton, None)
    chapters = [{"title": "Intake", "act_index": 0, "characters": ["Milo Voss"]}]
    await so._stage_beats_for_act(canon, 0, chapters, None, skeleton=skeleton)
    for prompt in (
        fake.prompts["outline:chapters"][0],
        fake.prompts["outline:beats"][0],
    ):
        assert pc._CONFIRMED_PREMISE.search(prompt).group(1) == "Milo wakes up."
    number, act_chapters = stub_llm._act_chapters(fake.prompts["outline:beats"][0])
    assert number == 1 and act_chapters == chapters


def test_reduce_prompt_markers():
    from app.parsing import canon_extractor as ce

    g = {
        "name": "Milo Voss",
        "aliases": ["Milo"],
        "mentions": 4,
        "windows": [0, 1],
        "role_hints": [],
        "evidence": [(0, "Milo files."), (1, "Milo signs.")],
        "quotes": ["I'll not be hurried."],
    }
    prompt = ce._reduce_prompt([g], 2)
    assert pm.EVIDENCE_LABEL in prompt and pm.QUOTED_LINES_LABEL in prompt
    assert pm.EVIDENCE_ITEM.format(part=2, quote="Milo signs.") in prompt
    sections = pc.candidate_sections(prompt)
    assert list(sections) == ["Milo Voss"]
    assert pc.check_reduce_evidence(prompt, ["Milo Voss"], {}) == ([], 1)
    assert stub_llm._HEADING.findall(prompt) == ["Milo Voss"]


def test_revise_prompt_pov_line():
    from app.orchestration.revise import build_revise_prompt

    prompt = build_revise_prompt(
        canon_block="<canon>C</canon>",
        chapter_summary="Ch.",
        cast_context="",
        prior="",
        target="Target.",
        after="",
        instruction="Tighten.",
        pov="first person, Milo Voss",
    )
    assert labelled_line(pm.POV_LABEL).search(prompt).group(1) == (
        "first person, Milo Voss"
    )
