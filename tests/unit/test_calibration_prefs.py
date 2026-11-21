"""SPEC-98 P2: the blind preference set's builder."""

import json

import pytest

from evals.calibration import prefs

pytestmark = pytest.mark.unit

PARA = " ".join(["word"] * 80)  # 80 words


def _scene(tag):
    """Three 80-word paragraphs opening with `tag`, so each passage names
    its row and scene."""
    return "\n\n".join([f"{tag} " + " ".join(["word"] * 79)] + [PARA, PARA])


def _row(arm, book, day, position, writer="flash-lite", run=None):
    run = run or f"{arm}{book}{day}{position}{writer[0]}"
    return {
        "run": run,
        "day": day,
        "writer": writer,
        "arm": arm,
        "book": book,
        "digest": f"d-{book}",
        "position": position,
        "replicate": 1,
        "summary": f"{book} chapter {position}",
        "beats": ["opening", "second"],
        "scenes": [_scene(f"{run}-s0"), _scene(f"{run}-s1")],
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
    for it in items["items"]:
        v = k[it["id"]]
        for side in ("a", "b"):  # each side is its run's passage of the keyed scene
            assert it[side].split()[0] == f"{v[side]['run']}-s{v['scene']}"
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


def _report(root, run, ablation, content):
    d = root / run / "x"
    d.mkdir(parents=True)
    row = {"position": 1, "summary": "s", "scenes": [{"content": content}]}
    report = {"ablation": ablation, "book": "dracula"}
    report["steps"] = {"chapters": {"outline_digest": "d", "chapters": [row]}}
    (d / "eval-report-1.json").write_text(json.dumps(report))


def test_rows_strip_the_harness_markers_from_scenes(tmp_path):
    _report(tmp_path, "r1", None, "Beat 1: Arrival\n\n" + PARA + " [[eval-x]]")
    (row,) = prefs._rows(tmp_path, {"r1": "d1"}, "flash-lite")
    assert row["scenes"] == ["Arrival\n\n" + PARA]


def test_an_unknown_ablation_names_its_report(tmp_path):
    _report(tmp_path, "r1", "other_arm", PARA)
    with pytest.raises(SystemExit, match="r1.*unknown ablation 'other_arm'"):
        prefs._rows(tmp_path, {"r1": "d1"}, "flash-lite")


# --- the judges' items and the score (SPEC-98 P2) ---------------------------------


def test_judges_get_each_distinct_pair_in_both_orders_and_nothing_from_the_key():
    from evals.calibration import prefs_guard
    from evals.calibration.answer import ITEMS_SCHEMA, parse, prompt

    items, key = prefs.build(*_pool())
    j = prefs.judge_items(items, key)
    n = sum(prefs.PLAN.values())
    assert j["schema"] == ITEMS_SCHEMA and len(j["items"]) == 2 * n
    assert j["prefs_digest"] == prefs.digest(items)
    by = {it["id"]: it for it in j["items"]}
    for it in items["items"]:
        if key["key"][it["id"]]["repeat_of"]:
            assert f"{it['id']}-ab" not in by
            continue
        ab, ba = by[f"{it['id']}-ab"], by[f"{it['id']}-ba"]
        assert (ab["text_a"], ab["text_b"]) == (it["a"], it["b"])
        assert (ba["text_a"], ba["text_b"]) == (it["b"], it["a"])
        assert ab["book"] == key["key"][it["id"]]["book"]
    assert prefs_guard.leaks(j, key) == []
    one = dict(j["items"][0])
    assert '{"preferred": "none"}' in prompt(one)
    assert parse(one, {"preferred": "none"}) == "none"
    assert parse({**one, "allow_none": False}, {"preferred": "none"}) is None
    leaked = {**j, "items": [{**one, "arm": "N"}]}
    assert prefs_guard.leaks(leaked, key)


def test_the_score_counts_order_consistency_and_agreement_as_spec_98_says():
    items, key = prefs.build(*_pool())
    k = key["key"]
    pairs = [i for i, v in k.items() if not v["repeat_of"]]
    reader = {i: "A" for i in pairs}
    reps = [i for i, v in k.items() if v["repeat_of"]]
    reader[reps[0]] = "B"  # the repeat confirms its pair (sides swapped)
    reader[reps[1]] = "A"  # the repeat contradicts its pair: it leaves the set
    perfect = {f"{i}-ab": "A" for i in pairs} | {f"{i}-ba": "B" for i in pairs}
    flaky = dict(perfect) | {f"{i}-ba": "A" for i in pairs[:4]}  # 4 inconsistent
    s = prefs.score(items, key, reader, {"perfect": perfect, "flaky": flaky})
    n = len(pairs) - 1
    assert s["decisive"] == n
    assert [r["consistent"] for r in s["reader_repeats"]] == [True, False]
    assert s["agreement_needed"]["gate"] == -(-7 * n // 10)
    p, f = s["judges"]["perfect"], s["judges"]["flaky"]
    assert (p["order_consistent"], p["agree"], p["qualifies_gate"]) == (16, n, True)
    assert f["order_consistent"] == 12 and not f["qualifies_gate"]
    assert not f["qualifies_bulk"]  # below 13/16 consistency
    none = prefs.score(items, key, {}, {"perfect": perfect})
    assert none["decisive"] == 0 and not none["judges"]["perfect"]["qualifies_gate"]


def test_the_build_refuses_a_set_that_is_not_the_rated_one(tmp_path, monkeypatch):
    monkeypatch.setattr(
        prefs, "_rows", lambda *a: _pool()[0] if a[2] == "flash-lite" else _pool()[1]
    )
    days = tmp_path / "d.json"
    days.write_text("{}")
    argv = [
        "--w21",
        str(tmp_path),
        "--w21-days",
        str(days),
        "--w21c",
        str(tmp_path),
        "--w21c-days",
        str(days),
        "--items",
        str(tmp_path / "i.json"),
        "--key",
        str(tmp_path / "k.json"),
        "--expect-digest",
        "0" * 64,
    ]
    with pytest.raises(SystemExit, match="not the rated set"):
        prefs.main(argv)
    assert not (tmp_path / "k.json").exists()


def test_the_prefs_workflow_has_no_secret_pins_its_actions_and_checks_the_digest():
    import re
    from pathlib import Path

    import yaml

    text = (
        Path(__file__).resolve().parents[2] / ".github/workflows/calibration-prefs.yml"
    ).read_text()
    wf = yaml.safe_load(text)
    steps = wf["jobs"]["build"]["steps"]
    for s in steps:
        if "uses" in s:
            assert re.fullmatch(r"[\w-]+/[\w-]+@[0-9a-f]{40}", s["uses"])
    assert (
        "secrets." not in text and "--expect-digest" in text and "prefs_guard" in text
    )
    uploads = [s["with"] for s in steps if "upload-artifact" in s.get("uses", "")]
    assert [(u["name"], u["path"]) for u in uploads] == [
        ("calibration-items", "out/items"),
        ("calibration-key", "out/key"),
    ]
