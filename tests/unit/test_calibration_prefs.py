"""SPEC-98 P2: the blind preference set's builder."""

import json

import pytest

from evals.calibration import prefs

pytestmark = pytest.mark.unit

PARA = " ".join(["word"] * 80)  # 80 words


def _row(arm, book, day, position, writer="flash-lite", run=None):
    scene = "\n\n".join([PARA, PARA, PARA])
    return {
        "run": run or f"{arm}{book}{day}{position}",
        "day": day,
        "writer": writer,
        "arm": arm,
        "book": book,
        "digest": f"d-{book}",
        "position": position,
        "replicate": 1,
        "summary": f"{book} chapter {position}",
        "beats": ["opening", "second"],
        "scenes": [scene, scene],
    }


def _pool():
    w21, w21c = [], []
    for book in ("dracula", "wuthering", "frankenstein"):
        for pos in range(1, 7):
            for day, arm in (
                (f"{book}{pos}n", "N"),
                (f"{book}{pos}v", "V"),
                (f"{book}{pos}p", "P"),
            ):
                w21 += [_row("A", book, day, pos), _row(arm, book, day, pos)]
            w21c.append(_row("A", book, "c", pos, writer="claude"))
    return w21, w21c


def test_a_passage_is_whole_paragraphs_within_its_bounds():
    assert prefs.passage("\n\n".join([PARA] * 3)) == "\n\n".join([PARA] * 2)
    assert prefs.passage(PARA) is None  # too short
    assert prefs.passage(" ".join(["w"] * 400)) is None  # one paragraph too long


def test_the_set_follows_the_plan_blind_with_swapped_repeats():
    items, key = prefs.build(*_pool())
    n = sum(prefs.PLAN.values())
    assert len(items["items"]) == n + prefs.REPEATS
    shown = json.dumps(items)
    for word in ('"arm"', "flash-lite", "claude", "run", "pov_fix", "no_excerpts"):
        assert word not in shown
    k = key["key"]
    kinds = [v["kind"] for v in k.values() if v["repeat_of"] is None]
    assert {kind: kinds.count(kind) for kind in prefs.PLAN} == prefs.PLAN
    clusters = [
        (v["book"], v["position"]) for v in k.values() if v["repeat_of"] is None
    ]
    assert len(set(clusters)) == n  # one pair per outline chapter
    by_id = {it["id"]: it for it in items["items"]}
    for item_id, v in k.items():
        if v["repeat_of"]:
            src, rep = by_id[v["repeat_of"]], by_id[item_id]
            assert (rep["a"], rep["b"]) == (src["b"], src["a"])
            assert v["a"] == k[v["repeat_of"]]["b"]
    for v in k.values():
        if v["kind"] == "C":
            assert {v["a"]["writer"], v["b"]["writer"]} == {"claude", "flash-lite"}
        else:
            assert {v["a"]["arm"], v["b"]["arm"]} == {"A", v["kind"]}
            assert v["a"]["day"] == v["b"]["day"]
    assert prefs.build(*_pool()) == (items, key)  # seeded


def test_a_draw_too_large_for_its_pool_is_refused():
    w21, w21c = _pool()
    with pytest.raises(SystemExit, match="usable pairs"):
        prefs.build(w21, w21c[:1])
