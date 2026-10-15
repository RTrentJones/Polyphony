"""Day-0 contract (wave 2.5 §1a): the prompt markers in app/core/prompt_markers.py.

The round trip (test_prompt_roundtrip.py) grades the markers on the app's real
entry points. What stays here is what it does not exercise: the markers module
stays app-free, the POV line regex reads a dotted name whole, and the
confirmed-premise note plus the stub's `_act_chapters` parse on the outline
stages.
"""

import types

import pytest

from app.core import prompt_markers as pm
from app.core.prompt_markers import POV_LINE_RE
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


def test_pov_line_re_mode_and_who():
    """S5: a POV character may contain a period ("Dr. Ada Vance")."""
    from app.orchestration.prose import Pov, pov_instruction

    m = POV_LINE_RE.search(pov_instruction(Pov("first person", "Dr. Ada Vance")))
    assert m.group("mode") == "first person"
    assert m.group("who") == "Dr. Ada Vance"
    m = POV_LINE_RE.search(pov_instruction(Pov("third person limited")))
    assert m.group("mode") == "third person limited"
    assert m.group("who") is None
    assert POV_LINE_RE.search("Point of view: first person, Milo.") is None


def test_story_position_re_reads_n_and_m():
    line = pm.STORY_POSITION.format(n=2, m=7)
    (m,) = pm.STORY_POSITION_RE.finditer(f"Intro.\n{line}\nMore.")
    assert (m.group("n"), m.group("m")) == ("2", "7")
    assert pm.STORY_POSITION_RE.search("This scene is in chapter 3 of many.") is None


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
