"""W10-2 (a), D5: the leave-one-out voice instrument (SPEC-74)."""

import json
from pathlib import Path

import pytest

from evals.measure import voice_instrument as vi

pytestmark = pytest.mark.unit
GOLD = {  # 18 lines of A; B's one train line has no reference once held out
    "A": {"train": [f"a{i}" for i in range(16)], "test": ["at0", "at1"]},
    "B": {"train": ["b0"], "test": ["bt0", "bt1", "bt2"]},
}


@pytest.mark.parametrize("bt_side, ok", [("a", False), ("b", True)])
async def test_the_leave_one_out_is_reported_on_train_references(bt_side, ok):
    """(F) Train references, less the held line. (R) The leave-one-out is
    reported, total and per principal, whatever the verdict."""
    assert vi.references(GOLD, ("B", 0)) == {"A": GOLD["A"]["train"], "B": []}

    async def embed(texts):
        sides = (t.replace("bt", bt_side)[0] for t in texts)
        return [[1.0, 0.0] if s == "a" else [0.0, 1.0] for s in sides]

    out = await vi.score(GOLD, {}, embed, [])
    got = (out["n"], out["correct"], out["per_principal"]["B"], out["wilson95"])
    assert got == (22, 18 + 3 * ok, f"{3 * ok}/4", vi.wilson(18 + 3 * ok, 22))


BAL = {w: {"train": [f"{w}{i}" for i in range(5)], "test": [f"{w}t0"]} for w in "ab"}
ODD = {f"{w}{i}" for w in "ab" for i in ("1", "3", "t0")}  # each self-split's half b


def _answers(right: int) -> list[dict]:  # ten for a, `right` on a's side
    return [{"intended": "a", "answer": "a" if i < right else "b"} for i in range(10)]


async def _separable(texts):
    return [[1.0, 0.0] if t[0] == "a" else [0.0, 1.0] for t in texts]


async def _flat(texts):  # one vector for every line: no voice at all
    return [[1.0, 0.0] for _ in texts]


async def _topic(texts):  # each speaker's second half reads as another topic
    return [[0.0, 1.0] if t in ODD else [1.0, 0.0] for t in texts]


@pytest.mark.parametrize("embed, p, won", [(_separable, 0.01, 0.995), (_flat, 1.0, 0)])
async def test_the_permutation_control_scores_200_seeded_shuffles(embed, p, won):
    """(F) D4 (b): 200 seeded shuffles; separable beats all but the draw that keeps
    or swaps every label (p = 2/201), no voice beats none (p = 1)."""
    assert vi.SHUFFLES == 200
    one = (await vi.score(BAL, {}, embed, []))["instrument"]
    assert (one["permutation_p"], one["beats_shuffles"]) == (p, won)
    assert (await vi.score(BAL, {}, embed, []))["instrument"] == one  # seeded


@pytest.mark.parametrize(
    "embed, right, ok",
    [(_separable, 10, True), (_separable, 3, False), (_flat, 10, False)],
)
async def test_validated_needs_both_controls(embed, right, ok):
    """(F) D4: the positive control (answers vs train refs) has a lower bound
    > 0.33 AND the true score beats 95% of shuffles; either alone fails. The
    self-split (3/6 throughout) is not read."""
    out = await vi.score(BAL, {}, embed, _answers(right))
    assert out["instrument"]["positive"]["correct"] == right
    assert out["instrument"]["validated"] is ok
    assert out["instrument"]["self_split"] == {"a": "3/6", "b": "3/6"}


async def test_the_self_split_is_reported_never_gated():
    """(F) D4 (b): each speaker against itself, reported (here a topic split, 6/6);
    validation does not read it (the separable case validates at 3/6)."""
    out = (await vi.score(BAL, {}, _topic, _answers(10)))["instrument"]
    assert out["self_split"] == {"a": "6/6", "b": "6/6"}


def test_the_w9a2_answers_are_read_from_the_corpora():
    """(F) Wave-11 day-0: the instrument's W9a2 answers live in evals/corpora,
    not tests/ (evals/ reads no tests/ path): 30, each for a gold speaker."""
    from evals.steps.pipeline import load_corpus

    evals_dir = Path(vi.__file__).resolve().parents[1]
    assert vi.ANSWERS.resolve().relative_to(evals_dir).parts[0] == "corpora"
    answers = json.loads(vi.ANSWERS.read_text())["answers"]
    assert len(answers) == 30
    assert {a["intended"] for a in answers} <= set(load_corpus("cel")[1]["gold_lines"])
