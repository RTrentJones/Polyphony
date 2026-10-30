"""SPEC-96 (wave 21, part A): the eval-only `PROSE_ABLATION` switch.

Empty (the default) leaves every prompt byte-identical to the shipped product:
the snapshot in tests/fixtures/w21_default_prompts.json was written by
`_prompts()` from the code before the switch existed. Each flag turns off one
wave-18 lever (`no_excerpts`: W18-5, `no_voices`: W18-1) or applies the POV
fix (`pov_fix`); an unknown flag fails at settings load. evals.yml's
`ablation` input is validated in its own step and reaches the app through
PROSE_ABLATION."""

import json
import subprocess
import types
from pathlib import Path

import pytest
import yaml

import app.orchestration.workflow as wf
from app.core import config
from app.core.config import Settings, parse_prose_ablation, prose_ablation
from app.core.llm_text import as_quoted_block
from app.core.prompt_markers import NARRATION_LABEL, VOICE_CONTRAST_NOTE
from app.core.prompt_markers import VOICE_SAMPLES_LABEL
from app.orchestration import prose as prose_module
from app.orchestration.pov import Pov, pov_instruction
from app.orchestration.prose import (
    SceneSetup,
    _canon_hash,
    person_from_narration,
    write_beat_prose,
)
from app.orchestration.revise import DRAFT_PERSON_CLAUSE, build_revise_prompt
from app.orchestration.workflow import StoryPosition, plan_scene_beats
from app.planning.canon import Canon, render_style

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]
SNAPSHOT = ROOT / "tests" / "fixtures" / "w21_default_prompts.json"

# A wave-18 scene of a book like Dracula: the style guide names no POV mode,
# the book's narration excerpts are the style reference, and two speakers
# show samples (so the VOICES rule is printed).
_CAST = (
    f'<character>\n### Jonathan Harker\n{VOICE_SAMPLES_LABEL}\n- "I must go."\n'
    f"{NARRATION_LABEL}\n- I slept but little.\n</character>\n\n"
    f'<character>\n### Count Dracula\n{VOICE_SAMPLES_LABEL}\n- "Welcome to my '
    'house!"\n</character>\n\n'
    f"{VOICE_CONTRAST_NOTE}\n<contrast>\n- Jonathan Harker: plain\n</contrast>"
)
_EXCERPTS = (
    "I had all sorts of queer dreams. There was a dog howling all night under "
    "my window, which may have had something to do with it.",
    "The road was rugged, but still we seemed to fly over it with a feverish "
    "haste. I could not understand then what the haste meant.",
)
_BEATS = [
    {
        "description": "Harker reaches the castle door.",
        "characters": ["Jonathan Harker"],
    },
    {
        "description": "The Count greets him.",
        "characters": ["Jonathan Harker", "Count Dracula"],
    },
]
_REQUEST = {
    "scene_description": "Harker arrives at the castle.",
    "setting": "Castle Dracula",
    "characters": ["Jonathan Harker", "Count Dracula"],
}
_WHO = "Jonathan Harker"


def _style(**kw):
    base = dict(pov=None, tense=None, tone="grave, Gothic", comps=None)
    return types.SimpleNamespace(**{**base, "sample_prose": None, **kw})


class _Fake:
    def __init__(self, answer="Prose."):
        self.answer, self.prompts = answer, []

    async def generate(self, messages, **kw):
        self.prompts.append(messages[0]["content"])
        return types.SimpleNamespace(text=self.answer, tokens_in=1, tokens_out=1)


async def _beat(monkeypatch, pov, style, narration=_EXCERPTS) -> str:
    fake = _Fake()
    monkeypatch.setattr(prose_module, "get_llm_client", lambda: fake)
    await write_beat_prose(
        _BEATS[1],
        _REQUEST,
        _CAST,
        "I knocked twice.",
        pov=pov,
        story_position=StoryPosition(1, 4),
        secondary_block="",
        chapter_summary="Harker travels to the castle.",
        canon_block=as_quoted_block(render_style(style), "canon"),
        all_beats=_BEATS,
        beat_index=1,
        style=style,
        narration=narration,
    )
    return fake.prompts[0]


async def _plan(monkeypatch, pov) -> str:
    fake = _Fake("[]")
    monkeypatch.setattr(wf, "get_llm_client", lambda: fake)
    await plan_scene_beats(
        _REQUEST,
        canon_block="",
        chapter_summary="Harker travels to the castle.",
        pov=pov,
        story_position=StoryPosition(1, 4),
        secondary_block="",
        plan=[],
        earlier=[],
    )
    return fake.prompts[0]


def _revise(pov, person_from_draft=True) -> str:
    return build_revise_prompt(
        canon_block="",
        chapter_summary="Harker travels to the castle.",
        cast_context=_CAST,
        prior="I knocked twice.",
        target="The door opened.",
        after="He bowed.",
        instruction="Slower.",
        pov=pov,
        story_position=StoryPosition(1, 4),
        secondary_block="",
        person_from_draft=person_from_draft,
    )


async def _prompts(monkeypatch) -> dict:
    """The representative prompts, as the product builds them by default."""
    pov = Pov("third person limited", _WHO)  # resolve_pov's default mode
    return {
        "beat": await _beat(monkeypatch, pov, _style()),
        "beat_tense_set": await _beat(monkeypatch, pov, _style(tense="past")),
        "beat_pov_set": await _beat(
            monkeypatch, Pov("first person", _WHO), _style(pov="first person")
        ),
        "beat_no_excerpts": await _beat(monkeypatch, pov, _style(), narration=()),
        "plan": await _plan(monkeypatch, pov),
        "revise": _revise(pov),
    }


@pytest.fixture
def ablate(monkeypatch):
    def set_(value: str):
        monkeypatch.setattr(config.settings, "PROSE_ABLATION", value)

    set_("")
    return set_


# --- the default is today's product -------------------------------------------


async def test_the_default_prompts_are_byte_identical_to_before_the_switch(
    monkeypatch, ablate
):
    assert await _prompts(monkeypatch) == json.loads(SNAPSHOT.read_text("utf-8"))


def test_the_default_switch_is_empty():
    assert Settings.model_fields["PROSE_ABLATION"].default == ""
    assert prose_ablation() == frozenset()


# --- settings -------------------------------------------------------------------


def test_the_switch_parses_a_comma_set_and_rejects_anything_else(monkeypatch):
    assert parse_prose_ablation("") == frozenset()
    assert parse_prose_ablation(" no_voices, pov_fix ,no_voices") == {
        "no_voices",
        "pov_fix",
    }
    with pytest.raises(ValueError, match="no_vioces"):
        parse_prose_ablation("pov_fix,no_vioces")
    env = dict(SECRET_KEY="k" * 40, DATABASE_URL="postgresql+asyncpg://u@h/d")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PROSE_ABLATION", "no_excerpts,pov_fix")
    assert Settings().PROSE_ABLATION == "no_excerpts,pov_fix"
    monkeypatch.setenv("PROSE_ABLATION", "pov_fix,everything")
    with pytest.raises(ValueError, match="everything"):
        Settings()  # fails fast at settings load


def test_the_switch_is_read_at_call_time(ablate):
    ablate("no_voices")
    assert prose_ablation() == {"no_voices"}
    ablate("")
    assert prose_ablation() == frozenset()


# --- no_voices --------------------------------------------------------------------


async def test_no_voices_drops_only_the_voices_rule(monkeypatch, ablate):
    pov = Pov("third person limited", _WHO)
    on = await _beat(monkeypatch, pov, _style())
    ablate("no_voices")
    off = await _beat(monkeypatch, pov, _style())
    assert "VOICES — " in on and "VOICES — " not in off
    rule = on.split("VOICES — ")[1].split("\n\n")[0]
    assert off == on.replace(f"VOICES — {rule}\n\n", "")


# --- no_excerpts --------------------------------------------------------------------


def test_no_excerpts_leaves_person_to_the_mode_as_before_w18_5():
    """A setup with no excerpts is a pre-W18-5 book without sample prose:
    revise is not told to keep the draft's person."""
    setup = SceneSetup(Canon("c"), [], None, None, "", [], [], "", None)
    assert person_from_narration(setup._replace(narration=_EXCERPTS))
    assert not person_from_narration(setup)


async def test_scene_setup_honours_no_excerpts_and_pov_fix(
    monkeypatch, ablate, async_session, test_book, test_source
):
    """scene_setup reads the switch per call: no_excerpts empties the
    narration; pov_fix marks a mode-less POV as taken from it."""
    from app.orchestration.prose import scene_setup
    from tests.unit.factories import add_planned_chapter, add_scene

    para = (
        "I walked along the shore of the grey lake that evening. The wind "
        "came cold off the water and the reeds bent low beside the path. I "
        "thought of nothing at all for a long while, and the light went."
    )
    test_source.content_text = "\n\n".join([para] * 3)
    chapter = await add_planned_chapter(async_session, test_book, [], 0)
    scene = await add_scene(async_session, test_book, chapter)
    await async_session.commit()
    request = {"scene_description": "A meeting at the inn."}

    async def setup():
        return await scene_setup(async_session, test_book.id, request, scene.id)

    base = await setup()
    assert base.narration and base.pov.from_narration == ""
    ablate("no_excerpts")
    assert (await setup()).narration == ()
    ablate("pov_fix")
    fixed = await setup()
    assert fixed.narration == base.narration
    assert fixed.pov == base.pov._replace(from_narration="person and tense")
    ablate("pov_fix,no_excerpts")  # nothing to take person from: unchanged
    both = await setup()
    assert both.narration == () and both.pov == base.pov


# --- pov_fix ------------------------------------------------------------------------


def test_pov_fix_states_no_mode():
    fixed = Pov("third person limited", _WHO, "person and tense")
    text = pov_instruction(fixed)
    assert text.startswith(
        f"Point of view: {_WHO}; person and tense from the book's narration "
        f"passages below. Narrate only what {_WHO} perceives or knows:"
    )
    assert "third person" not in text
    nobody = pov_instruction(Pov("third person limited", None, "person and tense"))
    assert nobody.startswith(
        "Point of view: person and tense from the book's narration passages "
        "below. Narrate only what the point-of-view character perceives"
    )
    # Everything after the head is the default's.
    plain = pov_instruction(Pov("third person limited", _WHO))
    assert text.split(". ", 1)[1] == plain.split(". ", 1)[1]


async def test_pov_fix_keeps_the_pov_line_and_style_section_consistent(
    monkeypatch, ablate
):
    fixed = Pov("third person limited", _WHO, "person and tense")
    prompt = await _beat(monkeypatch, fixed, _style())
    assert "third person limited" not in prompt
    assert "the mode named above" not in prompt
    assert (
        "Match its narrative voice — person and tense from these passages (the "
        "book sets no point of view); its diction" in prompt
    )
    tensed = Pov("third person limited", _WHO, "person")
    prompt = await _beat(monkeypatch, tensed, _style(tense="past"))
    assert f"Point of view: {_WHO}; person from the book's narration" in prompt
    assert (
        "tense as the style guide above says, person from these passages (the "
        "book sets no point of view); its diction" in prompt
    )
    assert "the mode named above" not in prompt


async def test_pov_fix_reaches_the_planner_and_revise(monkeypatch, ablate):
    fixed = Pov("third person limited", _WHO, "person and tense")
    plan = await _plan(monkeypatch, fixed)
    assert f"Point of view: {_WHO}; person and tense from the book's own " in plan
    assert "third person" not in plan and f"Plan only what {_WHO}" in plan
    revise = _revise(fixed)
    assert f"Point of view: {_WHO}; person and tense from the draft's own " in revise
    assert DRAFT_PERSON_CLAUSE not in revise  # it names a mode "here"
    assert "third person" not in revise


# --- the checkpoint fingerprint --------------------------------------------------


def test_the_fingerprint_is_unchanged_by_default_and_differs_per_flag(ablate):
    setup = SceneSetup(
        Canon("c"), [], None, None, "", [], [], "", None, narration=_EXCERPTS
    )
    hashes = {}
    for value in ("", "pov_fix", "no_excerpts", "no_voices", "no_voices,pov_fix"):
        ablate(value)
        hashes[value] = _canon_hash(setup, "ch", None)
    # Pinned before the switch existed: a default checkpoint stays valid.
    assert hashes[""] == "6e101991705b30d5"
    assert len(set(hashes.values())) == len(hashes)
    ablate("pov_fix,no_voices")  # a set: order does not matter
    assert _canon_hash(setup, "ch", None) == hashes["no_voices,pov_fix"]


# --- evals.yml -----------------------------------------------------------------


def _wf() -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / "evals.yml").read_text())


def _step(name: str) -> dict:
    steps = _wf()["jobs"]["eval"]["steps"]
    return next(s for s in steps if s.get("name", "").startswith(name))


def _run_step(tmp_path, app_has_switch=True, **env):
    step = _step("Prose ablation")
    assert "${{" not in step["run"]  # inputs reach the shell through env only
    (tmp_path / "app" / "core").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app" / "core" / "config.py").write_text(
        "PROSE_ABLATION: str = ''\n" if app_has_switch else "DEBUG = False\n"
    )
    genv = tmp_path / "env"
    genv.write_text("")
    out = subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "GITHUB_ENV": str(genv)} | env,
        capture_output=True,
        text=True,
    )
    return out, genv.read_text()


def test_the_ablation_input_defaults_empty_and_reaches_the_step_by_env():
    wf_ = _wf()
    inputs = wf_[True]["workflow_dispatch"]["inputs"]
    assert inputs["ablation"]["default"] == ""
    step = _step("Prose ablation")
    assert step["env"]["ABLATION"] == "${{ github.event.inputs.ablation || '' }}"
    names = [s.get("name", "") for s in wf_["jobs"]["eval"]["steps"]]
    # After the app_sha overlay (it checks that tree), before the app boots.
    assert names.index("App under test (app_sha)") < names.index(step["name"])
    boot = next(
        i
        for i, s in enumerate(wf_["jobs"]["eval"]["steps"])
        if s.get("uses") == "./.github/actions/boot-app"
    )
    assert names.index(step["name"]) < boot


@pytest.mark.parametrize(
    "value, exported",
    [
        ("", ""),
        ("pov_fix", "PROSE_ABLATION=pov_fix\n"),
        ("no_excerpts,no_voices", "PROSE_ABLATION=no_excerpts,no_voices\n"),
    ],
)
def test_an_allowed_ablation_is_exported(tmp_path, value, exported):
    out, genv = _run_step(tmp_path, ABLATION=value)
    assert out.returncode == 0, out.stdout + out.stderr
    assert genv == exported


@pytest.mark.parametrize(
    "value",
    [
        "pov",
        "pov_fix,",
        "pov_fix no_voices",
        "POV_FIX",
        "pov_fix\nLLM_MODEL=x",  # never a second GITHUB_ENV line
        "pov_fix;rm -rf /",
    ],
)
def test_anything_else_is_refused(tmp_path, value):
    out, genv = _run_step(tmp_path, ABLATION=value)
    assert out.returncode == 1 and "ablation must be" in out.stdout
    assert genv == ""


def test_an_app_without_the_switch_refuses_an_ablation(tmp_path):
    """E4's W17 runs overlay app_sha=5bb2df2, which has no switch: an ablation
    there would be silently ignored, so it is refused; empty is fine."""
    sha = "5bb2df2" + "0" * 33
    out, genv = _run_step(tmp_path, False, ABLATION="pov_fix", APP_SHA=sha)
    assert out.returncode == 1 and genv == ""
    assert "has no PROSE_ABLATION switch" in out.stdout and sha in out.stdout
    out, genv = _run_step(tmp_path, False, ABLATION="", APP_SHA=sha)
    assert out.returncode == 0 and genv == ""
    out, genv = _run_step(tmp_path, True, ABLATION="no_voices", APP_SHA=sha)
    assert out.returncode == 0 and genv == "PROSE_ABLATION=no_voices\n"


def test_an_external_app_refuses_an_ablation(tmp_path):
    out, genv = _run_step(tmp_path, ABLATION="pov_fix", BASE_URL="https://x.dev")
    assert out.returncode == 1 and "base_url" in out.stdout and genv == ""


def test_the_baseline_workflow_is_untouched():
    blob = subprocess.run(
        ["git", "hash-object", ".github/workflows/baseline.yml"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert blob.startswith("6d26176")


# --- the eval report ------------------------------------------------------------


def test_the_report_records_the_ablation_and_salts_only_an_ablated_cache(
    monkeypatch,
):
    from evals import run

    monkeypatch.delenv("PROSE_ABLATION", raising=False)
    assert run._ablation() == ""
    # The default cache keys are the ones before the switch existed.
    assert (run._cache_salt(0, 1), run._cache_salt(2, 3)) == ("", "2")
    monkeypatch.setenv("PROSE_ABLATION", "pov_fix, no_voices")
    assert run._ablation() == "no_voices,pov_fix"
    assert run._cache_salt(0, 1) == "|no_voices,pov_fix"
    assert run._cache_salt(2, 3) == "2|no_voices,pov_fix"


@pytest.mark.parametrize("value, want", [("", None), ("pov_fix", "pov_fix")])
async def test_a_report_records_its_ablation(tmp_path, monkeypatch, value, want):
    """Each eval report names the arm beside its generator and judge."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import evals.run as r
    from tests.unit.test_evals_preflight import _cfg

    monkeypatch.setenv("PROSE_ABLATION", value)
    cfg = _cfg(tmp_path)
    judge = SimpleNamespace(provider_id="groq", is_self=False, graded_by=[])
    judge.model_override = judge.fell_back = judge.endpoint = None
    monkeypatch.setattr(r, "load_config", lambda: cfg)
    monkeypatch.setattr(r, "Judge", lambda _cfg: judge)
    monkeypatch.setattr(r.get_step("retrieval"), "run", AsyncMock(return_value={}))
    out = await r.run("cel", ["retrieval"], str(tmp_path / "r.json"), 1, None)
    assert out["ablation"] == want
    assert json.loads((tmp_path / "r.json").read_text())["ablation"] == want


def test_pov_fix_keeps_the_mode_when_sample_prose_outranks_the_excerpts():
    from types import SimpleNamespace

    from app.orchestration.prose import narrated_person

    narration = ("I walked to the window.",)
    plain = SimpleNamespace(
        style=SimpleNamespace(pov=None, tense=None, sample_prose=None)
    )
    assert narrated_person(plain, narration) == "person and tense"
    sampled = SimpleNamespace(
        style=SimpleNamespace(pov=None, tense=None, sample_prose="She ran.")
    )
    assert narrated_person(sampled, narration) == ""
