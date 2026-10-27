"""SPEC-95 E5: a judge answers the human calibration page; the agreement
script compares several answer files. No LLM call: a fake judge answers."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals import run  # noqa: F401 — registers every step in the runner's order
from evals.calibration import agreement as ag
from evals.calibration import answer
from evals.calibration.export import PAIR_QUESTION, R2_QUESTION
from evals.validation.export import CHAPTER_QUESTION


def _items():
    return {
        "schema": answer.ITEMS_SCHEMA,
        "n": 4,
        "items": [
            {
                "id": "c00000001",
                "kind": "r2",
                "book": "dracula",
                "question": R2_QUESTION,
                "candidates": ["Ann Lee", "Bo Ray"],
                "samples": {"Ann Lee": ["I shall go."], "Bo Ray": ["Aye, then."]},
                "lines": [{"n": 1, "text": "I shall."}, {"n": 2, "text": "Aye."}],
            },
            {
                "id": "c00000002",
                "kind": "pair",
                "book": "dracula",
                "question": PAIR_QUESTION,
                "context": "<canon>\nA book\nCast: Ann Lee\n</canon>",
                "text_a": "First passage.",
                "text_b": "Second passage.",
            },
            {
                "id": "c00000003",
                "kind": "chapter",
                "book": "dracula",
                "question": CHAPTER_QUESTION,
                "plan": "PLAN: the plan",
                "text": "\nCHAPTER TO SCORE\nthe text",
            },
            {
                "id": "c00000004",
                "kind": "pair",
                "book": "frankenstein",
                "question": PAIR_QUESTION,
                "context": "<canon>x</canon>",
                "text_a": "a",
                "text_b": "b",
            },
        ],
    }


def test_only_the_pages_items_are_read_never_a_key(tmp_path):
    (p := tmp_path / "items.json").write_text(json.dumps(_items()))
    assert answer.load_items(p)["n"] == 4
    key = {"schema": "polyphony-calibration-key/1", "items": {}}
    (k := tmp_path / "key.json").write_text(json.dumps(key))
    with pytest.raises(ValueError, match="never read"):
        answer.load_items(k)


def test_each_kind_is_asked_as_the_page_asks():
    r2, pair, chapter, _ = _items()["items"]
    p = answer.prompt(r2)
    assert R2_QUESTION in p and "1. I shall." in p and "- Aye, then." in p
    assert "exactly 2 entries" in p and "Ann Lee, Bo Ray" in p
    p = answer.prompt(pair)
    assert p.index("PASSAGE A:\nFirst") < p.index("PASSAGE B:\nSecond")
    assert PAIR_QUESTION in p and '{"preferred": "A"}' in p and pair["context"] in p
    p = answer.prompt(chapter)
    assert CHAPTER_QUESTION in p and "PLAN: the plan\nCHAPTER TO SCORE" in p


@pytest.mark.parametrize(
    "kind, reply, want",
    [
        ("r2", {"speakers": ["Ann Lee", "bo ray "]}, ["Ann Lee", "Bo Ray"]),
        ("r2", {"speakers": ["Ann Lee"]}, None),  # wrong length
        ("r2", {"speakers": ["Ann Lee", "Cy"]}, None),  # not a candidate
        ("r2", {"speakers": "Ann Lee, Bo Ray"}, None),
        ("pair", {"preferred": "a"}, "A"),
        ("pair", {"preferred": 2}, "B"),
        ("pair", {"preferred": "tie"}, None),
        ("chapter", {"score": 0.7}, 0.7),
        ("chapter", {"score": "0.4"}, 0.4),
        ("chapter", {"score": 1.4}, None),
        ("chapter", {"score": True}, None),
        ("chapter", {}, None),
        ("pair", "A", None),
    ],
)
def test_parse_keeps_page_shaped_answers_and_nothing_else(kind, reply, want):
    it = next(i for i in _items()["items"] if i["kind"] == kind)
    assert answer.parse(it, reply) == want


class _Judge:
    provider_id = "anthropic"
    exhausted = False

    def __init__(self, replies):
        self.calls, self.replies = [], list(replies)

    async def ask_json(self, prompt):
        self.calls.append(prompt)
        return self.replies.pop(0)


async def test_the_step_answers_its_books_items_and_reads_no_key(tmp_path, monkeypatch):
    (p := tmp_path / "items.json").write_text(json.dumps(_items()))
    monkeypatch.setenv(answer.ITEMS_ENV, str(p))
    monkeypatch.delenv("MONTHLY_COST_CEILING_USD", raising=False)
    read = []
    orig = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *a, **k: read.append(self.name) or orig(self, *a, **k),
    )
    judge = _Judge(
        [{"speakers": ["Bo Ray", "Ann Lee"]}, {"preferred": "x"}, {"score": 0.4}]
    )
    res = await answer.step_calibration_answers(
        SimpleNamespace(book="dracula", judge=judge)
    )
    c = res["calibration"]
    assert c["answers"] == {
        "c00000001": ["Bo Ray", "Ann Lee"],
        "c00000002": None,
        "c00000003": 0.4,
    }
    assert c["unparseable"] == 1 and len(judge.calls) == 3
    assert set(c["judges"].values()) == {"anthropic"}
    assert c["items_digest"] == answer.digest(_items())
    assert res["score"] is None and c["estimate"]["judge_calls"] == 3
    assert not [n for n in read if "key" in n]
    res = await answer.step_calibration_answers(
        SimpleNamespace(book="cel", judge=judge)
    )
    assert res["skipped"]
    monkeypatch.delenv(answer.ITEMS_ENV)
    res = await answer.step_calibration_answers(
        SimpleNamespace(book="dracula", judge=judge)
    )
    assert res["skipped"] and answer.ITEMS_ENV in res["reason"]


def _report(tmp_path, name, answers, judges, digest=None):
    c = {
        "items_digest": digest or answer.digest(_items()),
        "answers": answers,
        "judges": judges,
    }
    path = tmp_path / f"eval-report-{name}.json"
    path.write_text(
        json.dumps({"steps": {answer.STEP: {"score": None, "calibration": c}}})
    )
    return path


def test_collect_merges_the_books_into_the_agreement_shape(tmp_path):
    a = _report(
        tmp_path,
        "dracula",
        {"c00000001": ["Ann Lee", "Bo Ray"], "c00000002": None, "c00000003": 0.7},
        {"c00000001": "anthropic", "c00000002": "anthropic", "c00000003": "groq"},
    )
    b = _report(
        tmp_path, "frankenstein", {"c00000004": "B"}, {"c00000004": "anthropic"}
    )
    got, counts = answer.collect([a, b])
    assert got == {
        "c00000001": ["Ann Lee", "Bo Ray"],
        "c00000003": 0.7,
        "c00000004": "B",
    }
    assert counts == {
        "answered": 3,
        "unparseable": 1,
        "by_judge": {"anthropic": 2, "groq": 1},
    }
    got, counts = answer.collect([tmp_path], judge="anthropic")
    assert set(got) == {"c00000001", "c00000004"} and counts["other_judge"] == 1
    (tmp_path / "x").mkdir()
    c = _report(tmp_path / "x", "c", {}, {}, digest="0" * 64)
    with pytest.raises(SystemExit, match="exactly one items.json"):
        answer.collect([a, c])


def test_collect_cli_prints_counts_only(tmp_path, capsys):
    a = _report(tmp_path, "d", {"c00000004": "A"}, {"c00000004": "anthropic"})
    answer.main(["collect", str(a), "--out", str(tmp_path / "out.json")])
    assert json.loads((tmp_path / "out.json").read_text()) == {"c00000004": "A"}
    assert '"A"' not in capsys.readouterr().out


# --- several answer files ------------------------------------------------------


def _three():
    items = {
        "items": [
            {"id": "s1", "kind": "r2", "lines": [1, 2, 3, 4]},
            {"id": "q1", "kind": "pair"},
            {"id": "q2", "kind": "pair"},
            {"id": "q3", "kind": "pair"},
            {"id": "c1", "kind": "chapter"},
            {"id": "c2", "kind": "chapter"},
        ]
    }
    key = {
        "items": {
            "s1": {"kind": "r2", "speakers": ["X", "Y", "X", "Y"], "judge_r2": [0.4]},
            "q1": {"kind": "pair", "panel": "A", "members": {}},
            "q2": {"kind": "pair", "panel": "B", "members": {}},
            "q3": {"kind": "pair", "panel": "A", "members": {}},
            "c1": {"kind": "chapter", "chapter": "A", "variant": "intact"},
            "c2": {"kind": "chapter", "chapter": "A", "variant": "x", "damage": "x"},
        }
    }
    human = {
        "s1": ["X", "Y", "Y", "Y"],
        "q1": "A",
        "q2": "B",
        "q3": "B",
        "c1": 1.0,
        "c2": 0.4,
    }
    sonnet = {
        "s1": ["X", "Y", "X", "X"],
        "q1": "A",
        "q2": "A",
        "q3": "B",
        "c1": 0.7,
        "c2": 0.4,
    }
    free = {"q1": "B", "c1": "high"}
    return items, key, {"human": human, "sonnet": sonnet, "free": free}


def test_pairwise_agreement_between_answer_files():
    items, key, raters = _three()
    out = ag.pairwise(items, key, raters["human"], raters["sonnet"])
    # lines X/X Y/Y Y/X Y/X: 2 of 4
    assert out["r2"] == {
        "items": 1,
        "lines": 4,
        "line_agreement": 0.5,
        "line_agreement_ci": None,  # one scene: no interval
    }
    # A/A B/A B/B: 2 of 3; kappa: po 2/3, pe (1/3*2/3 + 2/3*1/3) = 4/9 -> 0.4
    assert out["pairs"]["items"] == 3
    assert out["pairs"]["agreement"] == pytest.approx(2 / 3, abs=1e-4)
    assert out["pairs"]["cohen_kappa"] == pytest.approx(0.4)
    # |1.0-0.7| + |0.4-0.4| over 2; anchors 1.0/0.7 differ, 0.4/0.4 agree
    assert out["chapters"]["items"] == 2
    assert out["chapters"]["mean_abs_diff"] == pytest.approx(0.15)
    assert out["chapters"]["anchor_agreement"] == 0.5
    sparse = ag.pairwise(items, key, raters["human"], raters["free"])
    assert sparse["r2"] == {"items": 0, "lines": 0}
    assert sparse["pairs"]["items"] == 1 and sparse["pairs"]["agreement"] == 0.0
    assert sparse["chapters"] == {"items": 0}  # "high" is unreadable


def test_agreement_takes_several_named_answer_files(tmp_path, capsys):
    items, key, raters = _three()
    args = []
    for name, doc in (("items", items), ("key", key)):
        (p := tmp_path / f"{name}.json").write_text(json.dumps(doc))
        args.append(str(p))
    for name, doc in raters.items():
        (p := tmp_path / f"{name}-answers.json").write_text(json.dumps(doc))
        args.append(f"{name}={p}")
    ag.main(args)
    out = json.loads(capsys.readouterr().out)
    assert list(out["raters"]) == ["human", "sonnet", "free"]
    assert list(out["pairwise"]) == [
        "human vs sonnet",
        "human vs free",
        "sonnet vs free",
    ]
    assert out["raters"]["human"] == ag.agreement(items, key, raters["human"])
    text = json.dumps(out)
    assert '"s1"' not in text and '"q1"' not in text  # aggregates only
    # one file keeps W19 S4's output shape; a name repeated is refused
    ag.main(args[:3])
    assert "raters" not in json.loads(capsys.readouterr().out)
    with pytest.raises(SystemExit, match="two answer files"):
        ag.main(args[:2] + [f"x={args[-1].split('=')[1]}"] * 2)
