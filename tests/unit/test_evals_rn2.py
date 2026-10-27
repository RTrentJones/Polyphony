"""SPEC-95 E2: RN-v2, the narrator forced choice on the S7 chapters.

No LLM call: the step is driven with a fake judge."""

import json
from collections import Counter
from types import SimpleNamespace

import pytest

from evals import run  # noqa: F401 — registers every step in the runner's order
from evals.steps import pipeline
from evals.validation import build as s7
from evals.validation import rn2

# The item set SPEC-94's S7 result was judged on (runs 40303087945, 40425791780).
S7_DIGEST = "d8cc3434d6f94fdfac3f0ff84867fd18a6039ef1cc700fa145e46ef374ef3198"


@pytest.fixture(scope="module")
def built():
    return rn2.build(rn2.DEFAULT_SEED)


def test_the_s7_item_set_is_untouched(built):
    assert s7.build(s7.DEFAULT_SEED)[1]["items_digest"] == S7_DIGEST
    assert run.OPT_IN[:2] == ("baseline", "rnrc_validation")
    assert rn2.STEP in run.OPT_IN


def test_two_items_per_s7_chapter_answered_by_real_narrator_and_donor(built):
    items, key = built
    chapters = {ch["id"]: ch for ch in s7.load_config()}
    assert Counter(k["chapter"] for k in key["items"].values()) == {
        c: 2 for c in chapters
    }
    for it in items["items"]:
        k = key["items"][it["id"]]
        ch = chapters[k["chapter"]]
        wrote = ch["narrator"] if k["kind"] == "intact" else ch["donor"]["narrator"]
        assert k["labels"][str(k["answer"])] == wrote
        assert set(k["labels"].values()) == {ch["narrator"], ch["donor"]["narrator"]}
        # the passage is the S7 variant's RN scene, through scene_narration
        v = {x["variant"]: x for x in s7.variants(ch)}[
            "intact" if k["kind"] == "intact" else "narrator_drift"
        ]
        scene = s7.scene_text(v["scenes"][v["rn_scene"]])
        assert it["passage"] == pipeline.scene_narration(scene)


def test_items_are_blind_and_the_answer_lives_in_the_key(built):
    items, key = built
    for it in items["items"]:
        assert tuple(it) == rn2.ITEM_FIELDS
        assert set(it["samples"]) == {"1", "2"}
    text = json.dumps(items)
    for k in key["items"].values():
        assert k["chapter"] not in text
    assert '"answer":' not in text and '"labels":' not in text
    # labels are shuffled: the real narrator is sometimes 1, sometimes 2, and
    # the answer is not the passage kind
    real_at = Counter(
        k["answer"] if k["kind"] == "intact" else 3 - k["answer"]
        for k in key["items"].values()
    )
    assert set(real_at) == {1, 2}
    assert {(k["kind"], k["answer"]) for k in key["items"].values()} == {
        ("intact", 1),
        ("intact", 2),
        ("drift", 1),
        ("drift", 2),
    }


def test_the_build_is_seed_reproducible(built):
    again = rn2.build(rn2.DEFAULT_SEED)
    assert json.dumps(again, sort_keys=True) == json.dumps(built, sort_keys=True)
    other = rn2.build(rn2.DEFAULT_SEED + 1)
    assert [i["id"] for i in other[0]["items"]] != [i["id"] for i in built[0]["items"]]
    assert other[1]["items_digest"] != built[1]["items_digest"]
    pairs = Counter((k["chapter"], k["kind"]) for k in other[1]["items"].values())
    assert pairs == Counter(
        (k["chapter"], k["kind"]) for k in built[1]["items"].values()
    )


def test_samples_are_the_pipelines_and_never_quote_the_passage(built):
    items, key = built
    for ch in s7.load_config():
        mat = rn2.chapter_material(ch)
        real, _ = pipeline.narrator_samples(ch["book"], ch["narrator"])
        assert mat["samples"]["real"] == real  # nothing to leave out
        donor, _ = pipeline.narrator_samples(
            ch["donor"]["book"], ch["donor"]["narrator"]
        )
        quoted = any(s[:80] in p for s in donor for p in mat["passages"].values())
        assert (mat["samples"]["donor"] == donor) is not quoted
        assert len(mat["samples"]["donor"]) == len(donor)
    for it in items["items"]:
        for ss in it["samples"].values():
            assert ss and not any(s[:80] in it["passage"] for s in ss)
            assert all(len(s.split()) <= 150 for s in ss)


@pytest.mark.parametrize(
    "reply, want",
    [
        ({"narrator": 1}, 1),
        ({"narrator": "2"}, 2),
        ({"narrator": " 1 "}, 1),
        ({"narrator": 3}, None),
        ({"narrator": True}, None),
        ({"narrator": "Pembury"}, None),
        ({}, None),
        ("1", None),
    ],
)
def test_parse_reads_one_or_two_and_nothing_else(reply, want):
    assert rn2.parse(reply) == want


def test_the_prompt_shows_both_labels_and_the_passage(built):
    it = built[0]["items"][0]
    p = rn2.prompt(it)
    assert "NARRATOR 1 SAMPLES" in p and "NARRATOR 2 SAMPLES" in p
    assert p.index("NARRATOR 1") < p.index("NARRATOR 2") < p.index("PASSAGE:\n")
    assert it["passage"] in p and '{"narrator": 1}' in p
    for s in it["samples"]["1"] + it["samples"]["2"]:
        assert s in p


class _Judge:
    provider_id = "fake"
    exhausted = False

    def __init__(self, replies=None):
        self.calls, self.replies = [], list(replies or [])

    async def ask_json(self, prompt):
        self.calls.append(prompt)
        return self.replies.pop(0) if self.replies else {"narrator": 1}


async def test_the_step_records_choices_by_id_only(built, monkeypatch):
    monkeypatch.delenv("MONTHLY_COST_CEILING_USD", raising=False)
    monkeypatch.delenv("RN2_SEED", raising=False)
    judge = _Judge([{"narrator": 2}, {"oops": 1}])
    res = await rn2.step_rn2_validation(SimpleNamespace(book="wuthering", judge=judge))
    v = res["rn2"]
    mine = [i for i in built[0]["items"] if i["book"] == "wuthering"]
    assert list(v["judged"]) == [i["id"] for i in mine] and len(mine) == 6
    assert len(judge.calls) == v["estimate"]["judge_calls"] == 6
    first, second = (v["judged"][i["id"]] for i in mine[:2])
    assert first == {"choice": 2, "unparseable": False, "judge": "fake"}
    assert second == {"choice": None, "unparseable": True, "judge": "fake"}
    assert "1 unparseable" in res["judge_explanation"]
    assert res["score"] is None and v["items_digest"] == built[1]["items_digest"]
    text = json.dumps(res)
    assert '"answer":' not in text and '"labels":' not in text
    for k in built[1]["items"].values():
        assert k["chapter"] not in text
    res = await rn2.step_rn2_validation(SimpleNamespace(book="cel", judge=judge))
    assert res["skipped"]


async def test_the_step_stops_when_the_chain_gives_out(monkeypatch):
    from evals.harness.judge import JudgeUnavailable

    monkeypatch.delenv("MONTHLY_COST_CEILING_USD", raising=False)

    class Out(_Judge):
        async def ask_json(self, prompt):
            if len(self.calls) == 1:
                raise JudgeUnavailable("none left")
            return await super().ask_json(prompt)

    res = await rn2.step_rn2_validation(SimpleNamespace(book="dracula", judge=Out()))
    assert "JudgeUnavailable" in res["rn2"]["stopped"]
    assert len(res["rn2"]["judged"]) == 1


async def test_the_step_refuses_an_estimate_over_the_ceiling(monkeypatch):
    monkeypatch.setenv("MONTHLY_COST_CEILING_USD", "0.001")
    judge = _Judge()
    res = await rn2.step_rn2_validation(SimpleNamespace(book="wuthering", judge=judge))
    assert res["skipped"] and "ceiling" in res["reason"] and not judge.calls


def test_the_estimate_prices_every_call_as_sonnet(built):
    est = rn2.estimate(built[0]["items"])
    assert est["judge_calls"] == 12 and est["priced_as"] == "claude-sonnet-5-5"
    assert 0 < est["usd_if_all_paid"] <= est["usd_worst"] < 1


# --- analysis ----------------------------------------------------------------


def _report(tmp_path, name, key, choices, judge="groq", digest=None):
    judged = {
        i: {"choice": c, "unparseable": c is None, "judge": judge}
        for i, c in choices.items()
    }
    v = {
        "seed": key["seed"],
        "items_digest": digest or key["items_digest"],
        "judged": judged,
    }
    path = tmp_path / f"eval-report-{name}.json"
    path.write_text(json.dumps({"steps": {rn2.STEP: {"score": None, "rn2": v}}}))
    return path


def _right(k):
    return k["answer"]


def _wrong(k):
    return 3 - k["answer"]


def test_analysis_scores_against_the_key_with_chapters_as_clusters(built, tmp_path):
    key = built[1]
    ks = key["items"]
    chapters = sorted({k["chapter"] for k in ks.values()})
    # groq: right on every intact item, wrong on every drift item, one
    # unparseable reply; sonnet: right everywhere.
    groq = {
        i: (_right(k) if k["kind"] == "intact" else _wrong(k)) for i, k in ks.items()
    }
    some = next(i for i, k in ks.items() if k["kind"] == "drift")
    groq[some] = None
    sonnet = {i: _right(k) for i, k in ks.items()}
    paths = [
        _report(tmp_path, "g", key, groq, "groq"),
        _report(tmp_path, "s1", key, sonnet, "anthropic"),
        _report(tmp_path, "s2", key, sonnet, "anthropic"),
    ]
    out = rn2.analyze(paths, resamples=200)
    g, s = out["judges"]["groq"], out["judges"]["anthropic"]
    assert g["all"]["unparseable"] == 1 and g["all"]["judgments"] == 12
    assert g["all"]["items"] == 11 and g["all"]["n_chapters"] == len(chapters)
    # the unparseable chapter rests on its intact item alone (1.0); the other
    # five average 0.5, so each chapter weighs once
    want = (1.0 + 0.5 * (len(chapters) - 1)) / len(chapters)
    assert g["all"]["accuracy"] == pytest.approx(want, abs=1e-4)
    assert g["intact"]["accuracy"] == 1.0 and g["drift"]["accuracy"] == 0.0
    assert g["drift"]["n_chapters"] == len(chapters) - 1
    assert g["intact"]["ci95"] == (1.0, 1.0) and g["intact"]["above_chance"]
    assert not g["drift"]["above_chance"]
    assert s["all"]["accuracy"] == 1.0 and s["all"]["judgments"] == 24
    assert s["all"]["items"] == 12 and s["all"]["unparseable"] == 0
    chose_1 = sum(c == 1 for c in sonnet.values()) / 12
    assert s["all"]["chose_1"] == pytest.approx(chose_1, abs=1e-4)
    assert "anthropic" in rn2.render(out)


def test_analysis_averages_an_items_repeats_before_its_chapter(built, tmp_path):
    key = built[1]
    ks = key["items"]
    one = next(i for i, k in ks.items() if k["kind"] == "intact")
    ch = ks[one]["chapter"]
    partner = next(i for i, k in ks.items() if k["chapter"] == ch and i != one)
    a = _report(
        tmp_path, "a", key, {one: _right(ks[one]), partner: _right(ks[partner])}
    )
    b = _report(tmp_path, "b", key, {one: _wrong(ks[one])})
    out = rn2.analyze([a, b], resamples=100)["judges"]["groq"]["all"]
    # item `one` is 0.5 over its two judgments, its partner 1.0: chapter 0.75
    assert out["accuracy"] == 0.75 and out["n_chapters"] == 1 and out["ci95"] is None


def test_analysis_refuses_a_different_item_set(built, tmp_path):
    key = built[1]
    a = _report(tmp_path, "a", key, {}, digest="0" * 64)
    with pytest.raises(SystemExit, match="different item set"):
        rn2.analyze([a])
    b = _report(tmp_path, "b", key, {})
    with pytest.raises(SystemExit, match="exactly one item set"):
        rn2.analyze([a, b])
