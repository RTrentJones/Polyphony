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
async def test_train_references_and_the_18_of_22_verdict(bt_side, ok):
    """(F) No leakage: train references, less the held line. (F) 18 with B at
    0/4 is unvalidated, B at 3/4 validates. (R) The scores are recorded."""
    assert vi.references(GOLD, ("B", 0)) == {"A": GOLD["A"]["train"], "B": []}

    async def embed(texts):
        sides = (t.replace("bt", bt_side)[0] for t in texts)
        return [[1.0, 0.0] if s == "a" else [0.0, 1.0] for s in sides]

    out = await vi.score(GOLD, {}, embed, [{"intended": "A", "answer": '"a9"\n'}])
    want = (22, 18 + 3 * ok, ok, f"{3 * ok}/4", 1)
    got = (out["n"], out["correct"], out["validated"], out["per_principal"]["B"])
    assert (*got, out["w9a2_answers"]["correct"]) == want


def test_the_w9a2_answers_are_read_from_the_corpora():
    """(F) Wave-11 day-0: the instrument's W9a2 answers live in evals/corpora,
    not tests/ (evals/ reads no tests/ path): 30, each for a gold speaker."""
    from evals.steps.pipeline import load_corpus

    evals_dir = Path(vi.__file__).resolve().parents[1]
    assert vi.ANSWERS.resolve().relative_to(evals_dir).parts[0] == "corpora"
    answers = json.loads(vi.ANSWERS.read_text())["answers"]
    assert len(answers) == 30
    assert {a["intended"] for a in answers} <= set(load_corpus("cel")[1]["gold_lines"])
