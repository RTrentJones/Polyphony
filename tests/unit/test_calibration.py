"""W19 S4 (SPEC-94 D19-5): the calibration export stays blind, its seed
reproduces it, and the agreement script's arithmetic."""

import csv
import json
import re
from pathlib import Path

import pytest
import yaml

from evals.baseline import blind, panel
from evals.calibration import agreement as ag
from evals.calibration import export as ex

pytestmark = pytest.mark.unit

GT = {
    "book": "fake",
    "synopsis": "A synopsis.",
    "cast": ["Ann Lee", "Bo Ray", "Cy Dorn"],
    "gold_lines": {
        "Ann Lee": {"train": ["I shall go."], "test": ["Never, sir."]},
        "Bo Ray": {"train": ["Aye, 'tis so."], "test": []},
    },
}
gt_at = lambda book, sha: (GT, "fake")  # noqa: E731


def _scene(sid, lines, source="tags", score=0.4, cast=("Ann Lee", "Bo Ray")):
    return {
        "scene_id": sid,
        "cast": list(cast),
        "r2_source": source,
        "spoken_lines": [{"speaker": s, "line": t} for s, t in lines],
        "rubrics": {"R2": {"score": score, "explanation": "why"}},
    }


def _report(root: Path, run: str, book: str, scenes: list, sha="h" * 40):
    p = root / run / f"eval-report-{run}" / f"eval-report-{book}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"book": book, "harness_sha": sha, "steps": {"prose": {"scenes": scenes}}}
    p.write_text(json.dumps(doc))


def _reports(tmp_path: Path) -> Path:
    root = tmp_path / "reports"
    scenes = [
        _scene(f"s{k}", [("Ann Lee", f"Bo, line {k}."), ("Bo Ray", f"Ann {k}!")])
        for k in range(6)
    ]
    scenes.append(_scene("j1", [("Ann Lee", "x"), ("Bo Ray", "y")], "judge"))
    scenes.append(_scene("na", [("Ann Lee", "x"), ("Bo Ray", "y")], score=None))
    scenes.append(_scene("one", [("Ann Lee", "x"), ("Ann Lee", "z")]))
    scenes.append(_scene("nogold", [("Ann Lee", "x"), ("Cy Dorn", "q")]))
    _report(root, "101", "fake", scenes)
    # The same scene re-graded by a second run: one candidate, two scores.
    again = [("Ann Lee", "Bo, line 0."), ("Bo Ray", "Ann 0!")]
    _report(root, "102", "fake", [_scene("s0b", again, score=1.0)])
    _report(root, "103", "other", [_scene("o1", [("Ann Lee", "m"), ("Bo Ray", "n")])])
    return root


def _sheet(path: Path, prefer: dict):
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(("task", "item", "other", "answer", "minutes"))
        w.writerows((blind.PREFER, a, c, won, "") for (a, c), won in prefer.items())
        w.writerow((blind.CONTINUITY, "iaaa", "", 2, ""))


def _baseline(tmp_path: Path, books=("fake", "other")):
    bundles, ratings, keys = (tmp_path / d for d in ("bundles", "ratings", "keys"))
    ratings.mkdir(parents=True)
    panel_sheet, m1, m2 = {}, {}, {}
    for n, book in enumerate(books):
        ids = [f"i{n}{k:011x}" for k in range(6)]
        items = {i: f"Passage {i[::-1]} of {book}." for i in ids}
        pairs = [[ids[k], ids[k + 1]] for k in range(0, 6, 2)]
        p = bundles / "900" / f"baseline-bundle-{book}" / "bundle.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"items": items, "pairs": pairs, "edit": []}))
        k = keys / "900" / f"baseline-key-{book}" / "key.json"
        k.parent.mkdir(parents=True)
        k.write_text(
            json.dumps(
                {
                    "seed": 1,
                    "items": {
                        i: [f"{book}-1", "ABC"[j % 3]] for j, i in enumerate(ids)
                    },
                }
            )
        )
        for j, (a, c) in enumerate(pairs):
            m1[(a, c)] = a
            m2[(a, c)] = a if j else c
            panel_sheet[(a, c)] = a if j else "tie"
    _sheet(ratings / "sheet-panel.csv", panel_sheet)
    _sheet(ratings / "sheet-groq_x.csv", m1)
    _sheet(ratings / "sheet-openrouter_y.csv", m2)
    members = ["groq:x", "openrouter:y"]
    (ratings / "kappa.json").write_text(json.dumps({"members": members, "unrated": 0}))
    return bundles, ratings, keys


def _export(tmp_path, seed=7, n_r2=4, n_pairs=4, with_keys=False):
    r2 = ex.r2_candidates(_reports(tmp_path), gt_at=gt_at)
    b, r, k = _baseline(tmp_path)
    pairs = ex.pair_candidates(b, r, k if with_keys else None, gt_at=gt_at)
    return ex.export(r2, pairs, seed, n_r2, n_pairs)


# --- blinding -------------------------------------------------------------------


def test_a_pre_s2_unparseable_r2_zero_is_not_a_candidate(tmp_path):
    """Review B3: before S2 an unparseable R2 reply scored 0.0 with the
    reason "unparseable judge reply"; it is no judge score to calibrate."""
    root = tmp_path / "reports"
    bad = _scene("u", [("Ann Lee", "x"), ("Bo Ray", "y")], score=0.0)
    bad["rubrics"]["R2"]["explanation"] = "unparseable judge reply"
    real_zero = _scene("z", [("Ann Lee", "p"), ("Bo Ray", "q")], score=0.0)
    _report(root, "201", "fake", [bad, real_zero])
    got = ex.r2_candidates(root, gt_at=gt_at)
    assert [c["scene_id"] for c in got] == ["z"]
    assert got[0]["judge_r2"] == [0.0]


@pytest.mark.parametrize("with_keys", [False, True])
def test_items_never_carry_a_label_a_verdict_or_a_source_id(tmp_path, with_keys):
    items, key = _export(tmp_path, with_keys=with_keys)
    assert ex.leaks(items, key) == []
    text = json.dumps(items)
    fields = set()

    def walk(o):
        if isinstance(o, dict):
            fields.update(o)
            [walk(v) for v in o.values()]
        elif isinstance(o, list):
            [walk(v) for v in o]

    walk(items)
    hidden = {"speakers", "panel", "members", "arms", "slot", "judge_r2", "seed"}
    hidden |= {"r2_source", "scene_id", "harness_sha", "sides", "bundle_pair"}
    assert not fields & hidden
    assert '"tie"' not in text
    # Every key-side value is absent: true speakers appear only as candidates.
    for k in key["items"].values():
        for v in (k.get("judge_explanations") or []) + [k.get("report")]:
            assert not v or v not in text
    assert not re.search(r"\bi[01][0-9a-f]{11}\b", text)  # no bundle id
    for it in items["items"]:
        assert set(it) == ex.SHOWN[it["kind"]]
    assert {k for k in key["items"]} == {it["id"] for it in items["items"]}


def test_the_leak_check_catches_a_field_and_an_id(tmp_path):
    items, key = _export(tmp_path)
    pair = next(it for it in items["items"] if it["kind"] == "pair")
    r2 = next(it for it in items["items"] if it["kind"] == "r2")
    bad = json.loads(json.dumps(items))
    next(i for i in bad["items"] if i["id"] == r2["id"])["speakers"] = ["Ann Lee"]
    assert any("speakers" in w for w in ex.leaks(bad, key))
    bad = json.loads(json.dumps(items))
    hidden = key["items"][pair["id"]]["bundle_pair"][0]
    next(i for i in bad["items"] if i["id"] == pair["id"])["text_a"] += hidden
    assert any("key-side id" in w for w in ex.leaks(bad, key))
    bad = dict(items, seed=1)
    assert ex.leaks(bad, key)


def test_the_seed_reproduces_the_export_and_another_seed_reshuffles(tmp_path):
    a = _export(tmp_path / "a", seed=11)
    b = _export(tmp_path / "b", seed=11)
    c = _export(tmp_path / "c", seed=12)
    assert json.dumps(a) == json.dumps(b)
    assert [i["id"] for i in a[0]["items"]] != [i["id"] for i in c[0]["items"]]
    assert a[1]["seed"] == 11


def test_sides_are_randomised_and_the_key_maps_them(tmp_path):
    seen_first = set()
    for seed in range(12):
        items, key = _export(tmp_path / str(seed), seed=seed, n_pairs=6)
        for it in (i for i in items["items"] if i["kind"] == "pair"):
            k = key["items"][it["id"]]
            assert it["text_a"] == f"Passage {k['sides']['A'][::-1]} of {it['book']}."
            assert it["text_b"] == f"Passage {k['sides']['B'][::-1]} of {it['book']}."
            seen_first.add(k["sides"]["A"] == k["bundle_pair"][0])
            if k["panel"] != "tie":  # the winner's side, from the bundle id
                assert k["sides"][k["panel"]] == k["bundle_pair"][0]
    assert seen_first == {True, False}


def test_r2_items_are_the_judges_input_and_the_truth_is_in_the_key(tmp_path):
    items, key = _export(tmp_path, n_r2=20)
    r2 = [i for i in items["items"] if i["kind"] == "r2"]
    # s0..s5 (s0 merged with its re-grade), j1, o1: not N/A, one-voice, no-gold.
    assert len(r2) == 8 and key["available"]["r2"] == 8
    for it in r2:
        k = key["items"][it["id"]]
        assert len(k["speakers"]) == len(it["lines"]) and set(it["candidates"]) == {
            "Ann Lee",
            "Bo Ray",
        }
        assert it["samples"]["Ann Lee"] == ["I shall go.", "Never, sir."]
        assert all(
            "Ann" not in ln["text"] and "Bo," not in ln["text"] for ln in it["lines"]
        )
    merged = [k for k in key["items"].values() if k.get("judge_r2") == [0.4, 1.0]]
    assert len(merged) == 1
    # Tag-sourced scenes first: with 7 tag scenes, n=7 never takes j1.
    items, key = _export(tmp_path / "t", n_r2=7)
    assert all(
        k["r2_source"] == "tags" for k in key["items"].values() if k["kind"] == "r2"
    )


def test_pairs_carry_panel_and_member_verdicts_and_arms_only_when_asked(tmp_path):
    items, key = _export(tmp_path, n_pairs=6)
    ks = [k for k in key["items"].values() if k["kind"] == "pair"]
    assert sum(k["panel"] == "tie" for k in ks) == 2
    assert all(set(k["members"]) == {"groq:x", "openrouter:y"} for k in ks)
    assert all(k["arms"] is None for k in ks)
    _, key = _export(tmp_path / "k", n_pairs=6, with_keys=True)
    ks = [k for k in key["items"].values() if k["kind"] == "pair"]
    assert all(set(k["arms"]) == {"A", "B"} and k["slot"] for k in ks)


def test_a_second_bundle_or_an_unrated_pair_is_refused(tmp_path):
    b, r, _ = _baseline(tmp_path)
    dup = b / "901" / "baseline-bundle-fake" / "bundle.json"
    dup.parent.mkdir(parents=True)
    dup.write_text((b / "900" / "baseline-bundle-fake" / "bundle.json").read_text())
    with pytest.raises(SystemExit, match="second bundle"):
        ex.pair_candidates(b, r, gt_at=gt_at)
    b, r, _ = _baseline(tmp_path / "u")
    (r / "sheet-panel.csv").write_text("task,item,other,answer,minutes\n")
    with pytest.raises(SystemExit, match="no panel verdict"):
        ex.pair_candidates(b, r, gt_at=gt_at)


def test_the_cli_writes_two_files_and_prints_counts_only(tmp_path, capsys, monkeypatch):
    reports = _reports(tmp_path)
    b, r, _ = _baseline(tmp_path)
    monkeypatch.setattr(ex, "ground_truth", gt_at)
    args = ["--reports", str(reports), "--bundles", str(b), "--ratings", str(r)]
    args += ["--seed", "5", "--n-r2", "3", "--n-pairs", "2"]
    args += ["--items", str(tmp_path / "o/items.json")]
    args += ["--key", str(tmp_path / "k/key.json")]
    ex.main(args)
    out = json.loads(capsys.readouterr().out)
    assert out == {"items": {"r2": 3, "pair": 2}, "available": {"r2": 8, "pair": 6}}
    items = json.loads((tmp_path / "o/items.json").read_text())
    key = json.loads((tmp_path / "k/key.json").read_text())
    assert items["n"] == 5 and ex.leaks(items, key) == [] and key["seed"] == 5


def test_the_pair_is_shown_as_the_panel_saw_it(tmp_path):
    assert ex.PANEL_PROMPT == panel.ASK["preferred"]
    assert ex.PANEL_PROMPT.startswith(ex.PAIR_QUESTION)
    assert ex.PREFER_TASK == blind.PREFER
    p = tmp_path / "baseline-bundle-cel" / "bundle.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"items": {}, "pairs": [], "edit": []}))
    assert panel.load(p)["canon"] == ex._canon(ex.ground_truth("cel", None)[0])


def test_the_corpus_is_read_at_the_reports_harness_sha():
    head = ex.subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    gt, where = ex.ground_truth("cel", head)
    assert where == head and gt["book"]
    assert ex.ground_truth("cel", "0" * 40)[1] == "checkout"


def test_the_workflow_pins_actions_and_reads_a_baseline_key_only_on_request():
    wf = yaml.safe_load(Path(".github/workflows/calibration.yml").read_text())
    steps = wf["jobs"]["export"]["steps"]
    for s in steps:
        if "uses" in s and not s["uses"].startswith("./"):
            assert re.fullmatch(r"[\w-]+/[\w-]+@[0-9a-f]{40}", s["uses"])
    script = "\n".join(s.get("run", "") for s in steps)
    key_lines = [ln for ln in script.splitlines() if "baseline-key" in ln]
    assert key_lines and all("in/keys" in ln for ln in key_lines)
    assert 'if [ "$WITH_ARMS" = yes ]' in script
    uploads = [s["with"] for s in steps if "upload-artifact" in s.get("uses", "")]
    assert [(u["name"], u["path"]) for u in uploads] == [
        ("calibration-items", "out/items"),
        ("calibration-key", "out/key"),
    ]
    assert "calibration" not in Path(".github/workflows/baseline.yml").read_text()


def test_the_workflow_can_load_the_app_settings_the_export_imports():
    """Run 40298444974: the export imports the pipeline's R2 helpers, which load
    the app's settings, and the job set none. Throwaway CI values, as evals.yml
    and test.yml use; the job still has no secret."""
    wf = yaml.safe_load(Path(".github/workflows/calibration.yml").read_text())
    env = wf["jobs"]["export"]["env"]
    assert env["SECRET_KEY"] == "ci_secret_key_minimum_32_characters_long"
    assert env["POSTGRES_PASSWORD"] == "ci_password_12345"
    assert "secrets." not in Path(".github/workflows/calibration.yml").read_text()


# --- agreement ------------------------------------------------------------------


def test_cohen_kappa_and_r2_bands():
    assert ag.cohen_kappa(list("AABB"), list("ABBB")) == pytest.approx(0.5)
    assert ag.cohen_kappa(list("AB"), list("AB")) == pytest.approx(1.0)
    assert ag.cohen_kappa(list("AB"), list("BA")) == pytest.approx(-1.0)
    assert ag.cohen_kappa(list("AA"), list("AA")) is None
    assert [ag.r2_band(a) for a in (1, 0.9, 0.89, 0.7, 0.5, 0.49)] == [
        1.0,
        1.0,
        0.7,
        0.7,
        0.4,
        0.0,
    ]


def _fixture():
    items = {
        "items": [
            {"id": "s1", "kind": "r2"},
            {"id": "s2", "kind": "r2"},
            {"id": "q1", "kind": "pair"},
            {"id": "q2", "kind": "pair"},
            {"id": "q3", "kind": "pair"},
            {"id": "q4", "kind": "pair"},
            {"id": "q5", "kind": "pair"},
        ]
    }
    r2 = lambda sp, j, src="tags", js=None: {  # noqa: E731
        "kind": "r2",
        "speakers": sp,
        "judge_r2": j,
        "r2_source": src,
        "judge_speakers": js,
    }
    pair = lambda p, m=None: {"kind": "pair", "panel": p, "members": m or {}}  # noqa
    key = {
        "items": {
            "s1": r2(["X", "Y", "X", "Y"], [0.4]),
            "s2": r2(["X", "Y"], [0.0, 1.0], "judge"),
            "q1": pair("A", {"m": "A"}),
            "q2": pair("B", {"m": "A"}),
            "q3": pair("A", {"m": "A"}),
            "q4": pair("B", {"m": "B"}),
            "q5": pair("tie", {"m": "tie"}),
        }
    }
    answers = {
        "s1": ["X", "Y", "Y", "Y"],  # 3/4 -> band 0.7 vs judge 0.4
        "s2": {"1": "X", "2": "Y"},  # 2/2 -> band 1.0 vs judge mean 0.5
        "q1": "A",
        "q2": "B",
        "q3": 2,
        "q4": "B",
        "q5": "A",
        "stray": "A",
    }
    return items, key, answers


def test_the_agreement_arithmetic():
    out = ag.agreement(*_fixture())
    r2, pairs = out["r2"], out["pairs"]
    assert r2["answered"] == 2 and r2["lines"] == 6
    assert r2["human_accuracy"] == pytest.approx(5 / 6, abs=1e-4)
    assert r2["human_accuracy_tags_only"] == {"items": 1, "accuracy": 0.75}
    assert r2["judge_accuracy"] is None and "rubric score" in r2["judge_accuracy_note"]
    assert r2["judge_r2_mean"] == pytest.approx(0.45)
    assert r2["human_r2_band_mean"] == pytest.approx(0.85)
    assert r2["band_agreement"] == 0.0
    assert r2["human_minus_judge_r2"] == pytest.approx(0.4)
    # Pairs: decided p1..p4, human agrees on p1, p2, p4 (p3 answered B).
    assert pairs["answered"] == 5 and pairs["panel_ties"] == 1
    assert pairs["human_panel_agreement"] == 0.75
    assert pairs["human_panel_agreement_ties_half"] == pytest.approx(0.7)
    # Human A,B,B,B vs panel A,B,A,B: po .75; pe .25*.5 + .75*.5 = .5 -> κ .5.
    assert pairs["cohen_kappa"] == pytest.approx(0.5)
    # Member m: A,A,A,B; po .5, pe .25*.75 + .75*.25 = .375 -> κ .2.
    assert pairs["members"]["m"] == {"pairs": 4, "agreement": 0.5, "cohen_kappa": 0.2}
    assert out["unknown_answer_ids"] == 1
    lo, hi = pairs["human_panel_agreement_ci"]
    assert lo <= 0.75 <= hi
    assert ag.agreement(*_fixture()) == out  # seeded bootstrap


def test_the_kappa_ci_reports_its_degenerate_resamples():
    """Nit: κ is undefined on a resample whose pairs are all one category
    for both raters; the CI drops those and says how many."""
    ps = [("A", "A")] * 4 + [("B", "B")]
    ci, dropped = ag.bootstrap_counted(ps, ag._kappa)
    # P(all five draws are one category) = .8^5 + .2^5 ≈ .33 of 2,000.
    assert 500 < dropped < 800 and ci == [1.0, 1.0]
    assert ag.bootstrap_counted(ps, ag._agree)[1] == 0
    items, key, answers = _fixture()
    pairs = ag.agreement(items, key, answers)["pairs"]
    assert isinstance(pairs["cohen_kappa_ci_degenerate_resamples"], int)


def test_per_line_judge_speakers_are_used_when_the_key_has_them():
    items, key, answers = _fixture()
    key["items"]["s1"]["judge_speakers"] = ["X", "X", "X", "Y"]
    r2 = ag.agreement(items, key, answers)["r2"]
    assert r2["judge_accuracy"] == 0.75 and r2["judge_lines_items"] == 1
    assert r2["human_judge_line_agreement"] == 0.5


def test_agreement_prints_aggregates_only(tmp_path, capsys):
    items, key, answers = _fixture()
    paths = []
    for name, doc in (("i", items), ("k", key), ("a", answers)):
        (p := tmp_path / f"{name}.json").write_text(json.dumps(doc))
        paths.append(str(p))
    ag.main(paths)
    text = capsys.readouterr().out
    assert not re.search(r'"(s1|s2|q[1-5])"\s*:\s*[\[{"]', text)
    assert '"q1"' not in text and '"s1"' not in text
    with pytest.raises(SystemExit, match="not one export"):
        ag.agreement({"items": [{"id": "z", "kind": "pair"}]}, key, answers)


def _chapters():
    """Two chapters' intact/damaged partner pairs (S7's export) and a pair."""
    items = {
        "items": [{"id": i, "kind": "chapter"} for i in ("c1", "c2", "c3", "c4", "c5")]
        + [{"id": "q1", "kind": "pair"}]
    }
    ch = lambda chapter, variant, partner, judge: {  # noqa: E731
        "kind": "chapter",
        "chapter": chapter,
        "variant": variant,
        "damage": None if variant == "intact" else variant,
        "partner": partner,
        "judge_rc": judge,
    }
    key = {
        "items": {
            "c1": ch("A", "intact", "c2", [1.0, 0.8]),  # judge mean 0.9
            "c2": ch("A", "duplicated_event", "c1", [0.4]),
            "c3": ch("B", "intact", "c4", [0.7]),
            "c4": ch("B", "narrator_drift", "c3", [0.7]),
            "c5": ch("C", "intact", "c6", None),  # partner unanswered/unjudged
            "q1": {"kind": "pair", "panel": "A", "members": {}},
        }
    }
    answers = {"c1": 1.0, "c2": "0.4", "c3": 0.7, "c4": 0.4, "c5": 0.9, "q1": "A"}
    return items, key, answers


def test_chapter_items_compare_the_humans_rc_with_the_judges():
    """Review: S7's chapter items were ignored; now RC score agreement and
    the damaged-below-intact ordering are reported, aggregates only."""
    out = ag.agreement(*_chapters())
    c = out["chapters"]
    assert (c["items"], c["answered"], c["judged"], c["chapters"]) == (5, 5, 4, 2)
    # |1-.9| + |.4-.4| + |.7-.7| + |.4-.7| = .4 over 4
    assert c["mean_abs_diff"] == pytest.approx(0.1)
    lo, hi = c["mean_abs_diff_ci"]
    assert lo <= 0.1 <= hi
    # anchors: 1.0/1.0, .4/.4, .7/.7, .4/.7 -> 3 of 4
    assert c["anchor_agreement"] == 0.75
    o = c["ordering"]
    # A: human .4 < 1.0 below, judge .4 < .9 below; B: human below, judge tie
    assert o == {
        "pairs": 2,
        "human_below": 1.0,
        "human_ties": 0.0,
        "judge_pairs": 2,
        "judge_below": 0.5,
        "judge_ties": 0.5,
        "human_judge_agreement": 0.5,
        "human_judge_agreement_ci": o["human_judge_agreement_ci"],
    }
    assert out["unknown_answer_ids"] == 0  # chapter ids are known
    assert out["pairs"]["answered"] == 1
    text = json.dumps(out)
    assert not re.search(r'"c[1-5]"', text)  # aggregates only
    items, key, answers = _chapters()
    answers.update({"c3": "high", "c9": 0.5})
    out = ag.agreement(items, key, answers)
    assert out["chapters"]["unreadable"] == 1 and out["unknown_answer_ids"] == 1
    assert out["chapters"]["ordering"]["pairs"] == 1
