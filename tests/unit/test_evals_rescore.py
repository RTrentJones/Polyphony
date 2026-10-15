"""Re-scoring a finished run with the current deterministic checks (SPEC-28):
the after-wave-1 run's scene 1 was zeroed by the old R1 cap although Zara
speaks in it, unnamed. The re-score rebuilds the scenes from the relay
archives and applies the H1 cap to the judge's raw scores."""

import json

import pytest

from evals.harness import rescore

pytestmark = pytest.mark.unit

INTAKE = '"Take your time," the caseworker said. Milo blinked.'
COUNTER = '"Don\'t thank her," the woman at the next counter said. Milo laughed.'
SCENE = f"{INTAKE}\n\n{COUNTER}"


def _rec(i, purpose, prompt, answer):
    return {
        "id": str(i),
        "purpose": purpose,
        "created": float(i),
        "messages": [{"role": "user", "content": prompt}],
        "answer": answer,
    }


def _beat_prompt(n, total, cast):
    items = "\n".join(
        f"{'→ ' if k == n else ''}{k}. beat {k} plan" for k in range(1, total + 1)
    )
    chars = "\n".join(f"<character>\n### {c}\n</character>" for c in cast)
    return (
        "You are a skilled novelist writing one beat of a scene in THIS book.\n"
        "<scene>\nScene description: Intake: Milo is onboarded.\n</scene>\n"
        f"<beats>\n{items}\n</beats>\n"
        f"CHARACTERS IN THIS BEAT — voices:\n\n{chars}\n\nBEAT TO WRITE: x"
    )


MODEL = [
    _rec(1, "beat_prose", _beat_prompt(1, 2, ["Milo Voss"]), INTAKE),
    _rec(2, "beat_prose", _beat_prompt(2, 2, ["Milo Voss", "Zara Okafor"]), COUNTER),
]


def _judge_prompt(rid, scene=True):
    head = f"### {rid}. Rubric\n\nCANON: x\n\n"
    return head + (f"SCENE TO SCORE:\n{SCENE}" if scene else "SPOKEN LINES: x")


JUDGE = [
    _rec(
        1, "eval_judge", "### R1. Cast fidelity\nOUTLINE TO SCORE: x", '{"score": 0.9}'
    ),
    _rec(2, "eval_judge", _judge_prompt("R1"), '{"score": 0.88, "explanation": "x"}'),
    _rec(3, "eval_judge", _judge_prompt("R2", scene=False), '{"score": 0.9}'),
    _rec(4, "eval_judge", _judge_prompt("R3"), '{"score": 0.9}'),
    _rec(5, "eval_judge", _judge_prompt("R4"), '{"score": 0.8}'),
    _rec(6, "eval_judge", _judge_prompt("R5"), '{"score": 0.8}'),
]

REPORT = {
    "book": "cel",
    "steps": {
        "prose": {
            "score": 0.0,
            "rubric_means": {"R1": 0.0},
            "scenes": [
                {
                    "scene_id": "s1",
                    "cast": ["Milo Voss", "Zara Okafor"],
                    "score": 0.0,
                    "deterministic_failures": {"R1": "absent: Zara Okafor"},
                    "rubrics": {"R1": {"score": 0.0, "capped": "absent"}},
                }
            ],
        }
    },
}


def test_archived_scenes_rebuild_each_beat_with_its_cast_and_prose():
    (scene,) = rescore.archived_scenes(MODEL)
    assert scene["description"] == "Intake: Milo is onboarded."
    assert [b["characters"] for b in scene["beats"]] == [
        ["Milo Voss"],
        ["Milo Voss", "Zara Okafor"],
    ]
    assert scene["beats"][1] == {
        "beat_number": 1,
        "description": "beat 2 plan",
        "characters": ["Milo Voss", "Zara Okafor"],
        "content": COUNTER,
    }


def test_judged_scenes_keep_the_raw_scores_and_skip_outline_rubrics():
    (scene,) = rescore.judged_scenes(JUDGE)
    assert scene["content"] == SCENE
    assert scene["scores"] == {"R1": 0.88, "R2": 0.9, "R3": 0.9, "R4": 0.8, "R5": 0.8}


def test_rescore_lifts_the_r1_cap_on_a_speaking_unnamed_principal():
    # The planned description names Zara, so she is required.
    model = json.loads(json.dumps(MODEL))
    model[1]["messages"][0]["content"] = model[1]["messages"][0]["content"].replace(
        "beat 2 plan", "Zara Okafor warns him"
    )
    out = rescore.rescore_prose(REPORT, model, JUDGE)
    (row,) = out["scenes"]
    assert row["old_caps"] and row["caps"] == {}
    assert row["warnings"] == ["Zara Okafor speaks (unnamed tag) but is never named"]
    assert row["rubrics"]["R1"] == 0.88
    assert row["score"] == pytest.approx(0.88 * (0.9 + 0.9 + 0.8 + 0.8) / 4, abs=1e-4)
    assert out["r1_capped_scenes"] == 0 and out["old_score"] == 0.0
