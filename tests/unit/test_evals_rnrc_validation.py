"""W19 S7 (SPEC-94, D19-9): the RN/RC validation set, runner, analysis, export.

No LLM call: the step is driven with a fake judge."""

import json
import re
from collections import Counter
from types import SimpleNamespace

import pytest

from evals import run  # noqa: F401 — registers every step in the runner's order
from evals.steps import pipeline
from evals.validation import analyze, build, export
from evals.validation import step as vstep


@pytest.fixture(scope="module")
def built():
    return build.build(build.DEFAULT_SEED)


@pytest.fixture(scope="module")
def chapters():
    return {ch["id"]: (ch, build.variants(ch)) for ch in build.load_config()}


def _words(scenes):
    return sum(len(p.split()) for s in scenes for p in s)


# --- builder -----------------------------------------------------------------


def test_build_is_deterministic_and_seeded(built):
    again = build.build(build.DEFAULT_SEED)
    assert json.dumps(again, sort_keys=True) == json.dumps(built, sort_keys=True)
    other_items, other_key = build.build(build.DEFAULT_SEED + 1)
    assert [i["id"] for i in other_items["items"]] != [
        i["id"] for i in built[0]["items"]
    ]
    # the same items, reordered and renamed: one item per (chapter, variant)
    pairs = Counter((k["chapter"], k["variant"]) for k in built[1]["items"].values())
    assert pairs == Counter(
        (k["chapter"], k["variant"]) for k in other_key["items"].values()
    )
    assert set(pairs.values()) == {1}


def test_the_set_covers_every_damage_in_and_out_of_the_trimmed_region(built):
    key = built[1]["items"].values()
    assert {k["damage"] for k in key} == {None, *build.DAMAGES}
    trimmed = {k["chapter"] for k in key if k["rc_trimmed"]}
    assert 3 <= len(trimmed) < len({k["chapter"] for k in key})
    inside = {(k["damage"], k["location"]) for k in key if k["location"] == "in"}
    assert {"narrator_drift", "duplicated_event"} <= {d for d, _ in inside}
    assert {k["chapter"] for k in key if k["location"] == "in"} <= trimmed


def test_no_rn_scene_holds_a_narrator_sample(chapters):
    for ch, _ in chapters.values():
        assert build.sample_overlap(ch) == [], ch["id"]


def test_each_variant_differs_from_intact_in_its_stated_way(chapters):
    for cid, (ch, vs) in chapters.items():
        by = {v["variant"]: v for v in vs}
        intact = by["intact"]["scenes"]
        rn = ch["rn_scene"]
        n_intact = _words(intact)
        for name, v in by.items():
            s = v["scenes"]
            if name == "intact":
                continue
            assert s != intact, (cid, name)
            if name in ("narrator_drift", "narrator_drift_in"):
                assert [x for i, x in enumerate(s) if i != rn] == [
                    x for i, x in enumerate(intact) if i != rn
                ]
                donor = set(
                    build.chapter_paragraphs(
                        ch["donor"]["book"], ch["donor"]["heading"]
                    )
                )
                donor |= {p.strip('"“”').strip() for p in donor}
                new = [p for p in s[rn] if p not in intact[rn]]
                assert new and all(p in donor for p in new), (cid, name)
                before, after = v["detail"]["words"]
                assert abs(after - before) <= 0.3 * before, (cid, name)
                if name == "narrator_drift":
                    assert not set(s[rn]) & set(intact[rn])
                else:  # only a middle run is replaced
                    assert s[rn][0] == intact[rn][0] and s[rn][-1] == intact[rn][-1]
            elif name.startswith("duplicated_event"):
                to = v["detail"]["scene"]
                assert [x for i, x in enumerate(s) if i != to] == [
                    x for i, x in enumerate(intact) if i != to
                ]
                (added,) = list((Counter(s[to]) - Counter(intact[to])).elements())
                assert added in intact[v["detail"]["source_scene"]]
                assert to > v["detail"]["source_scene"]
                flat = [p for x in s for p in x]
                assert flat.count(added) == 2
            elif name == "broken_transitions":
                order = v["detail"]["order"]
                assert s == [intact[i] for i in order]
                assert all(b != a + 1 for a, b in zip(order, order[1:]))
                assert s[v["rn_scene"]] == intact[rn]
            elif name == "planned_change_removed":
                paras = build.chapter_paragraphs(ch["book"], ch["heading"])
                gone = {paras[i] for i in ch["change"]}
                kept = [p for x in intact for p in x if p not in gone]
                assert [p for x in s for p in x] == kept
                assert _words(s) < n_intact
                assert s[v["rn_scene"]] == intact[rn] or rn in {
                    i for i, x in enumerate(intact) if set(x) & gone
                }
            # the damage never flips whether RC trims (except a cut)
            if name != "planned_change_removed":
                assert v["rc_trimmed"] == by["intact"]["rc_trimmed"]


def test_in_region_damage_is_hidden_and_out_region_damage_shown(chapters):
    for cid, (_, vs) in chapters.items():
        for v in vs:
            if v["variant"].endswith("_in"):
                assert (v["location"], v["shown"]) == ("in", 0.0), cid
            elif v["variant"] == "duplicated_event":
                assert (v["location"], v["shown"]) == ("out", 1.0), cid
                assert v["detail"]["source_shown"] == 1.0, cid
        if not vs[0]["rc_trimmed"]:
            assert all(v["location"] in (None, "out") for v in vs), cid


def test_visibility_mirrors_what_rc_content_shows(built):
    """The located words are exactly the words rc_content keeps, in order."""
    for it in built[0]["items"]:
        scenes = [s.split("\n\n") for s in it["scenes"]]
        vis = build.visibility(scenes)
        rc, cut = pipeline.rc_content(vstep.plan_chapter(it["plan"]), it["scenes"])
        body = rc.split("scenes in order", 1)[1].split("\n", 1)[1]
        parts = re.split(r"\[Scene \d+\]\n", body)[1:]
        for n, (part, scene) in enumerate(zip(parts, scenes)):
            words = [w for p in scene for w in p.split()]
            shown = [w for w, ok in zip(words, (x for p in vis[n] for x in p)) if ok]
            assert [w for w in part.split() if w != pipeline._TRIM] == shown
        assert cut == build.trimmed(scenes)


def test_items_are_blind(built):
    items, key = built
    assert set(items) == {"schema", "n", "items"}  # no seed beside the items
    assert {i["id"] for i in items["items"]} == set(key["items"])
    labels = {k["variant"] for k in key["items"].values()} - {"intact"}
    labels |= {k["chapter"] for k in key["items"].values()}
    labels |= {d for d in build.DAMAGES}
    for it in items["items"]:
        assert tuple(it) == build.ITEM_FIELDS
        text = json.dumps(it, ensure_ascii=False)
        assert not [s for s in labels if s in text], it["id"]
    # a chapter's variants all show the same plan and narrator
    by = {}
    for it in items["items"]:
        ch = key["items"][it["id"]]["chapter"]
        by.setdefault(ch, set()).add(
            json.dumps([it["plan"], it["narrator"], it["book"]])
        )
    assert all(len(v) == 1 for v in by.values())


def test_a_held_out_corpus_is_refused(tmp_path, monkeypatch):
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "spec.json").write_text('{"split": "holdout"}')
    monkeypatch.setattr(build, "CORPORA", tmp_path)
    with pytest.raises(ValueError, match="holdout corpus"):
        build.chapter_paragraphs("secret", "CHAPTER I")


# --- runner ------------------------------------------------------------------


def test_runner_inputs_are_the_pipelines_own(built):
    items, key = built
    for it in items["items"]:
        k = key["items"][it["id"]]
        got = vstep.judge_inputs(it, k["rn_scene"])
        chapter = {
            "title": it["plan"]["title"],
            "summary": it["plan"]["summary"],
            "planned_beats": [{"title": b} for b in it["plan"]["beats"]],
        }
        assert got["RC"] == pipeline.rc_content(chapter, it["scenes"])
        assert got["RC"][1] == k["rc_trimmed"]
        samples, _ = pipeline.narrator_samples(it["book"], it["narrator"])
        narration = pipeline.scene_narration(it["scenes"][k["rn_scene"]])
        assert samples and narration
        assert got["RN"] == pipeline.rn_content(
            samples, narration, pipeline.load_style(it["book"])
        )


class _Judge:
    provider_id = "fake"
    exhausted = False

    def __init__(self):
        self.calls = []

    async def score(self, rubric, content):
        self.calls.append((rubric.split("\n", 1)[0], content))
        return SimpleNamespace(score=0.5, explanation="ok")


async def test_the_step_judges_its_books_items_by_id_only(built, monkeypatch):
    monkeypatch.delenv("MONTHLY_COST_CEILING_USD", raising=False)
    monkeypatch.delenv("RNRC_SEED", raising=False)
    judge = _Judge()
    res = await vstep.step_rnrc_validation(
        SimpleNamespace(book="frankenstein", judge=judge)
    )
    v = res["validation"]
    mine = [i for i in built[0]["items"] if i["book"] == "frankenstein"]
    assert list(v["judged"]) == [i["id"] for i in mine]  # the seeded order
    assert len(judge.calls) == v["estimate"]["judge_calls"] == 2 * len(mine)
    assert {c[0] for c in judge.calls} == {
        pipeline.rubrics.rubric(r).split("\n", 1)[0] for r in ("RC", "RN")
    }
    assert v["items_digest"] == built[1]["items_digest"]
    assert res["score"] is None and v["stopped"] is None
    labels = {k["variant"] for k in built[1]["items"].values()} - {"intact"}
    assert not [s for s in labels if s in json.dumps(res)]
    assert all(r["rc"] == 0.5 and r["rn"] == 0.5 for r in v["judged"].values())
    # a book with no validation chapter is a skip, not a call
    res = await vstep.step_rnrc_validation(SimpleNamespace(book="cel", judge=judge))
    assert res["skipped"]


async def test_the_step_refuses_an_estimate_over_the_runs_ceiling(monkeypatch):
    monkeypatch.setenv("MONTHLY_COST_CEILING_USD", "0.01")
    judge = _Judge()
    res = await vstep.step_rnrc_validation(
        SimpleNamespace(book="wuthering", judge=judge)
    )
    assert res["skipped"] and "ceiling" in res["reason"] and not judge.calls


async def test_the_step_stops_on_a_spend_pause(monkeypatch):
    from app.llm.errors import SpendPausedError

    monkeypatch.delenv("MONTHLY_COST_CEILING_USD", raising=False)

    class Paused(_Judge):
        async def score(self, rubric, content):
            if len(self.calls) == 3:
                raise SpendPausedError("monthly ceiling")
            return await super().score(rubric, content)

    judge = Paused()
    res = await vstep.step_rnrc_validation(SimpleNamespace(book="dracula", judge=judge))
    v = res["validation"]
    assert "SpendPausedError" in v["stopped"]
    assert len(v["judged"]) == 2 and len(judge.calls) == 3  # RC kept for the 2nd


# --- analysis ----------------------------------------------------------------


def test_summarize_counts_ties_apart_and_bootstraps_the_mean():
    rows = [{"drop": d} for d in (0.3, 0.3, 0.0, -0.1, 0.3)]
    s = analyze.summarize(rows, resamples=500, seed=1)
    assert (s["n"], s["below"], s["ties"], s["above"]) == (5, 3, 1, 1)
    assert s["share_below"] == 0.6
    assert s["mean_drop"] == pytest.approx(0.16)
    lo, hi = s["ci95"]
    assert -0.1 <= lo <= 0.16 <= hi <= 0.3
    assert analyze.summarize(rows, 500, 1) == s  # seeded
    assert analyze.bootstrap_ci([0.2], 100, 0) is None
    assert analyze.bootstrap_ci([0.2, 0.2], 100, 0) == (0.2, 0.2)


def test_pairs_and_table_split_by_location():
    key = {
        "a0": {"chapter": "A", "variant": "intact", "damage": None, "location": None},
        "a1": {"chapter": "A", "variant": "d", "damage": "dup", "location": "out"},
        "a2": {"chapter": "A", "variant": "d_in", "damage": "dup", "location": "in"},
        "b0": {"chapter": "B", "variant": "intact", "damage": None, "location": None},
        "b1": {"chapter": "B", "variant": "d", "damage": "dup", "location": "partial"},
        "c1": {"chapter": "C", "variant": "d", "damage": "dup", "location": "out"},
    }
    scores = {
        "a0": {"rc": [1.0, 0.8]},  # averaged first: 0.9
        "a1": {"rc": [0.4]},
        "a2": {"rc": [0.9]},
        "b0": {"rc": [0.7]},
        "b1": {"rc": [0.7]},
        "c1": {"rc": [0.1]},  # C's intact unscored: no pair
    }
    rows = analyze.pairs(key, scores, "rc")
    assert sorted((r["location"], round(r["drop"], 4)) for r in rows) == [
        ("in", 0.0),
        ("out", 0.5),
        ("partial", 0.0),
    ]
    t = analyze.table(rows, resamples=200)["dup"]
    assert t["all"]["n"] == 3 and t["all"]["ties"] == 2
    assert t["visible"]["n"] == 2 and t["visible"]["below"] == 1
    assert t["in"]["share_below"] == 0.0 and t["out"]["mean_drop"] == 0.5


def test_analyze_reads_reports_and_refuses_another_item_set(built, tmp_path):
    items, key = built
    judged = {}
    for i, k in key["items"].items():
        rc = 1.0 if k["damage"] is None or k["location"] == "in" else 0.4
        judged[i] = {"rc": rc, "rn": 0.2 if k["damage"] == "narrator_drift" else 0.9}
    v = {"seed": key["seed"], "items_digest": key["items_digest"], "judged": judged}
    (tmp_path / "eval-report-x.json").write_text(
        json.dumps({"steps": {"rnrc_validation": {"score": None, "validation": v}}})
    )
    out = analyze.analyze([tmp_path], resamples=200)
    assert out["items_judged"] == {"rc": 36, "rn": 36}
    assert out["RC"]["duplicated_event"]["out"]["share_below"] == 1.0
    assert out["RC"]["duplicated_event"]["in"]["share_below"] == 0.0
    assert out["RN"]["narrator_drift"]["all"]["mean_drop"] == pytest.approx(0.7)
    assert out["RN"]["broken_transitions"]["all"]["ties"] == 6
    text = analyze.render(out)
    assert "narrator_drift" in text and not [i for i in judged if i in text]
    v["items_digest"] = "0" * 64
    (tmp_path / "eval-report-x.json").write_text(
        json.dumps({"steps": {"rnrc_validation": {"validation": v}}})
    )
    with pytest.raises(SystemExit, match="different item set"):
        analyze.analyze([tmp_path], resamples=10)


# --- export ------------------------------------------------------------------


def test_export_is_a_blind_calibration_chapter_sample(built):
    items, key = export.export(seed=7, n=8)
    assert items["schema"] == "polyphony-calibration-items/1"
    assert items["n"] == 8 and items["counts"] == {"chapter": 8}
    assert export.leaks(items, key) == []
    assert all(
        set(it) == export.SHOWN and it["kind"] == "chapter" for it in items["items"]
    )
    ks = key["items"]
    assert sum(k["variant"] == "intact" for k in ks.values()) == 4
    assert {k["damage"] for k in ks.values()} == {None, *build.DAMAGES}
    for i, k in ks.items():
        assert ks[k["partner"]]["partner"] == i
        assert ks[k["partner"]]["chapter"] == k["chapter"]
    by_id = {it["id"]: it for it in built[0]["items"]}
    for it in items["items"]:
        src = by_id[ks[it["id"]]["validation_item"]]
        rc, _ = pipeline.rc_content(vstep.plan_chapter(src["plan"]), src["scenes"])
        assert it["plan"] + it["text"] == rc  # what the RC judge saw
    leaky = {**items, "items": [{**items["items"][0], "variant": "x"}]}
    assert export.leaks(leaky, key)


def test_chapter_items_pass_the_calibration_pages_leak_check(built):
    """S4's page whitelist (evals.calibration.export.leaks) accepts S7's
    chapter kind with exactly its shown fields, and still refuses an extra."""
    from evals.calibration import export as calibration

    items, key = export.export(seed=7, n=8)
    assert calibration.SHOWN["chapter"] == export.SHOWN
    assert calibration.leaks(items, key) == []
    leaky = {**items, "items": [{**items["items"][0], "damage": "x"}]}
    assert calibration.leaks(leaky, key)
