"""SPEC-98 P1: evals.yml's input rules as one validated object."""

from pathlib import Path

import pytest
import yaml

from evals import config

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
HOLDOUT = config.splits.corpora("holdout")


def _workflow():
    return yaml.safe_load((ROOT / ".github" / "workflows" / "evals.yml").read_text())


def test_the_defaults_and_the_recorded_dispatch_shapes_are_valid():
    assert config.validate({}) == []
    # Wave 21's writer, Sonnet judge and strict judge dispatches.
    writer = {"book": "dracula", "steps": "chapters", "app_sha": SHA, "chapters": "6"}
    assert config.validate({**writer, "ablation": "no_excerpts,no_voices"}) == []
    judged = {"claude": "yes", "claude_generate": "no", "from_runs": "1,2"}
    assert config.validate({**judged, "judge": "claude", "claude_usd_cap": "0.5"}) == []
    strict = {**judged, "judge": "strict", "steps": "narration_judges"}
    assert config.validate(strict) == []
    assert config.validate({"holdout": "yes"}, ref_type="tag") == []
    assert config.validate({"experiment": "w22"}, ref_type="tag") == []
    assert config.validate({"experiment": "smoke", "tier": "gate"}) == []
    assert config.validate({"holdout": "yes", "book": HOLDOUT[0]}, "tag") == []


@pytest.mark.parametrize(
    ("inputs", "ref_type", "needle"),
    [
        ({"judge": "claude"}, "branch", "judge=claude needs claude=yes"),
        (
            {"judge": "claude", "claude": "yes", "base_url": "https://x"},
            "branch",
            "booted app",
        ),
        ({"judge": "strict", "claude": "yes"}, "branch", "narration_judges alone"),
        ({"judge": "opus"}, "branch", "judge must be one of"),
        ({"ablation": "pov_fix, no_voices"}, "branch", "ablation must be"),
        ({"ablation": "pov_fix", "base_url": "https://x"}, "branch", "booted app"),
        ({"app_sha": "abc123"}, "branch", "40-character"),
        ({"from_runs": "12,x"}, "branch", "run ids"),
        ({"steps": "retrieval,calibration_answers"}, "branch", "calibration_run"),
        ({"claude_usd_cap": "0"}, "branch", "claude_usd_cap"),
        ({"claude_monthly_usd": "-3"}, "branch", "claude_monthly_usd"),
        ({"tier": "smoke"}, "branch", "tier must be one of"),
        ({"repeat": "0"}, "branch", "repeat"),
        ({"chapters": "13"}, "branch", "chapters"),
        ({"replicates": "7"}, "branch", "replicates"),
        ({"holdout": "yes"}, "branch", "only from a tag"),
        ({"holdout": "yes", "book": "dracula"}, "tag", "not dracula"),
        ({"book": f"dracula {HOLDOUT[1]}"}, "branch", "dispatch with holdout=yes"),
        ({"bok": "dracula"}, "branch", "unknown input"),
        ({"experiment": "w22"}, "branch", "harness tag"),
    ],
)
def test_a_bad_dispatch_is_refused_with_its_reason(inputs, ref_type, needle):
    errors = config.validate(inputs, ref_type)
    assert len(errors) == 1 and needle in errors[0], errors


def test_every_problem_is_reported_at_once(capsys):
    env = {"INPUT_JUDGE": "strict", "INPUT_ABLATION": "x", "INPUT_BOOK": ""}
    assert config.main(env) == 1
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 3 and all(line.startswith("::error") for line in out)
    assert config.main({"GITHUB_REF_TYPE": "branch"}) == 0


def test_the_rules_defaults_are_the_workflows_and_the_step_passes_each_input():
    wf = _workflow()
    declared = wf[True]["workflow_dispatch"]["inputs"]
    for name, default in config.DEFAULTS.items():
        assert str(declared[name].get("default", "")) == default, name
    assert declared["judge"]["options"] == list(config.JUDGES)
    assert sorted(declared["tier"]["options"]) == sorted(config.LINES)
    steps = wf["jobs"]["eval"]["steps"]
    step = next(s for s in steps if s.get("run") == "python3 -m evals.config")
    assert steps.index(step) == 1  # straight after the checkout
    assert step["env"] == {
        f"INPUT_{name.upper()}": f"${{{{ github.event.inputs.{name} || '' }}}}"
        for name in config.DEFAULTS
    }
