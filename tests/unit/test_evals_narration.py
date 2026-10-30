"""SPEC-96 (wave 21): M1 narration contract, M4 outline lint, the M2/M3 judge
steps and the pre-registered analysis. No LLM call: synthetic text, synthetic
reports and fake judges."""

import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals import run
from evals.narration import analyze, contract, judges, lint

# --- M1: narration and speech -------------------------------------------------


def test_split_keeps_narration_and_drops_quotes_and_tags():
    n, styles = contract.split_paragraph(
        "“Come in,” said Nora, and I followed her into the hall."
    )
    assert "Come in" not in n and "said Nora" not in n
    assert "I followed her into the hall." in n and styles == ["double"]
    n, styles = contract.split_paragraph("'Go,' he said. I went out alone.")
    assert n == "I went out alone." and styles == ["single"]
    # an unclosed double quote is a speech continued in the next paragraph
    n, _ = contract.split_paragraph("I said nothing. “And so we went on and on")
    assert n == "I said nothing."
    # W18: a paragraph opening a single quote it never closes is speech
    assert contract.split_paragraph("'We rode all night, and I was cold")[0] == ""
    # apostrophes neither open nor close a quote
    n, styles = contract.split_paragraph("I'm sure the dogs' bones were Hollis's.")
    assert n.startswith("I'm sure") and styles == []
    # a teller's frame quotes (Frankenstein's Creature) are narration
    assert contract.split_paragraph("“I wandered in the forest", framed=True)[0]


def test_sentence_person_and_tense():
    assert contract.sentence_person("He looked at me.") == "first"
    assert contract.sentence_person("I ran.") == "first"
    assert contract.sentence_person("She turned to the window.") == "third"
    assert contract.sentence_person("It was cold.") is None
    assert contract.sentence_person("Nora ran.") is None
    assert contract.sentence_tense("I walked to the door and she was there.") == "past"
    assert contract.sentence_tense("I walk to the door; she is there.") == "present"
    assert contract.sentence_tense("He goes out and takes the key.") == "present"
    assert contract.sentence_tense("The night, dark and cold.") is None
    # a participle after an auxiliary is not a past marker of its own
    assert contract.tense_markers("He has said so.") == (0, 1)


def test_windows_join_a_short_tail():
    assert contract.windows(["third"] * 5 + ["first"] * 2) == ["first"]
    assert contract.windows(["third"] * 5 + ["first"] * 5) == ["third", "first"]
    assert contract.windows([]) == []


FIRST_PAST = (
    "I walked to the castle door. He looked at me with cold eyes. "
    "I was afraid, and I said nothing.\n\n"
    "“Welcome,” said the Count. “Enter freely.”\n\n"
    "We went in together. I heard the bolt fall behind us."
)
PROF = {"person": "first", "tense": "past", "quotes": "double"}


def test_score_text_against_the_books_profile():
    ok = contract.score_text(FIRST_PAST, PROF)
    assert ok["person_mismatch_window"] == 0.0 and ok["tense_mismatch"] == 0.0
    assert ok["person_mismatch"] == 0.0 and ok["quote_mismatch"] == 0.0
    third = contract.to_third(FIRST_PAST)
    assert '"Welcome," said the Count.' in third  # speech untouched
    assert "he walked to the castle door" in third.lower()
    bad = contract.score_text(third, PROF)
    assert bad["person_mismatch"] == 1.0 and bad["person_mismatch_window"] == 1.0
    present = contract.to_present(FIRST_PAST)
    assert "I walk to the castle door" in present and "I am afraid" in present
    assert contract.score_text(present, PROF)["tense_mismatch"] == 1.0
    single = FIRST_PAST.replace("“", "'").replace("”", "'")
    assert contract.score_text(single, PROF)["quote_mismatch"] == 1.0
    empty = contract.score_text("", PROF)
    assert empty["person_mismatch"] is None and empty["tense_mismatch"] is None


def test_the_three_books_are_first_person_past_double_quoted():
    for book in ("dracula", "frankenstein", "wuthering"):
        p = contract.profile(book)
        assert (p["person"], p["tense"], p["quotes"]) == ("first", "past", "double")


def test_validation_summary_bars():
    def row(src, damaged):
        r = {"source": {m: src for m in contract.MEASURES}}
        for name, (measures, _) in contract._DAMAGES.items():
            r[name] = {
                m: {"detected": None if damaged is None else damaged - src >= 0.25}
                for m in measures
            }
        return r

    good = [{"chapters": [row(0.0, 1.0), row(0.05, 0.9)]}]
    s = contract.summarize_validation(good)
    assert s["validated person_mismatch"] and s["validated tense_mismatch"]
    bad = [{"chapters": [row(0.0, 1.0), row(0.2, 0.3)]}]
    s = contract.summarize_validation(bad)
    assert not s["false_positive person_mismatch"]["passes"]
    assert s["detection to_third person_mismatch"]["rate"] == 0.5
    assert not s["validated person_mismatch"]


def _report(path: Path, book: str, steps: dict, sha="a" * 40, **top) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"book": book, "app_sha": sha, "steps": steps} | top))
    return path


def _row(pos, text, rep=1, pov="Aldous Kerr", failure=None, beats=None):
    scenes = [{"status": "completed", "content": t} for t in text] if text else []
    return {
        "position": pos,
        "replicate": rep,
        "pov": pov,
        "failure": failure,
        "planned_beats": beats or [{"title": "b", "characters": [pov]}] * len(scenes),
        "scenes": scenes,
    }


def test_score_reports_keys_by_run_ablation_and_chapter(tmp_path):
    ch = {"outline_digest": "d", "chapters": [_row(1, [FIRST_PAST])]}
    _report(
        tmp_path / "eval-report-111/eval-report-dracula.json",
        "dracula",
        {"chapters": ch},
        ablation="pov_fix",
    )
    _report(
        tmp_path / "222/x/eval-report-dracula.json",
        "dracula",
        {"chapters": ch | {"chapters": [_row(1, [contract.to_third(FIRST_PAST)])]}},
        ablation=None,  # the shipped product, arm A
    )
    rows = {r["run"]: r for r in contract.score_reports([tmp_path])}
    assert rows["111"]["ablation"] == "pov_fix" and rows["222"]["ablation"] == ""
    assert rows["111"]["person_mismatch"] == 0.0
    assert rows["222"]["person_mismatch"] == 1.0
    assert rows["111"]["position"] == 1 and rows["111"]["replicate"] == 1
    # a report with no `ablation` key (E4's) is refused, never read as A
    _report(tmp_path / "333/x/eval-report-dracula.json", "dracula", {"chapters": ch})
    with pytest.raises(SystemExit, match="no `ablation` key"):
        contract.score_reports([tmp_path])


# --- M4: outline lint -----------------------------------------------------------


def test_lint_counts_empty_beats_missing_pov_and_invented_names():
    outline = [
        {
            "title": "One",
            "pov": "Aldous Kerr",
            "summary": "Aldous Kerr reaches the castle.",
            "characters": ["Aldous Kerr"],
            "children": [
                {"title": "a", "summary": "He waits.", "characters": ["Aldous Kerr"]},
                {"title": "b", "summary": "Nobody comes.", "characters": []},
            ],
        },
        {
            "title": "Two",
            "summary": "Bartholomew Quillfeather, a stranger, meets Nora Vance.",
            "characters": ["Nora Vance"],
            "secondary_names": ["Bartholomew Quillfeather"],
            "children": [{"title": "c", "summary": "x", "characters": ["Nora Vance"]}],
        },
    ]
    got = lint.lint(outline, "dracula")
    assert got["beats"] == 3 and got["beats_no_character"] == round(1 / 3, 4)
    assert got["pov_missing"] == 1 and got["pov_outside_cast"] == []
    assert got["cast_not_resolved"] == []
    assert got["secondary_unsourced"] == ["Bartholomew Quillfeather"]
    assert any("quillfeather" in n.lower() for n in got["invented"])


def test_lint_reports_one_row_per_distinct_outline(tmp_path):
    outline = [{"title": "One", "pov": "Nora Vance", "characters": ["Nora Vance"]}]
    res = {"outline": outline, "outline_digest": "dd", "outline_source": "generated"}
    _report(
        tmp_path / "1/eval-report-1/eval-report-dracula.json",
        "dracula",
        {"chapters": res},
    )
    _report(
        tmp_path / "2/eval-report-2/eval-report-dracula.json",
        "dracula",
        {"chapters": res},
    )
    out = lint.analyze([tmp_path])
    assert len(out["outlines"]) == 1
    assert [r["run"] for r in out["outlines"][0]["runs"]] == ["1", "2"]
    assert out["books"]["dracula"]["outlines"] == 1


# --- M2 / M3 ----------------------------------------------------------------------


def test_narrator_pair_follows_the_s7_donors():
    assert judges.narrator_pair("dracula", "Aldous Kerr") == (
        "Aldous Kerr",
        "Nora Vance",
    )
    assert judges.narrator_pair("dracula", "Nora Vance") == (
        "Nora Vance",
        "Aldous Kerr",
    )
    assert judges.narrator_pair("dracula", "Elias Ward") == ("Elias Ward", "Nora Vance")
    assert judges.narrator_pair("dracula", "Cora Ellis") is None  # no voice source
    assert judges.narrator_pair("dracula", None) is None
    assert judges.narrator_pair("frankenstein", "Emeric Vandel") == (
        "Emeric Vandel",
        "Rowan Merrow",
    )
    assert judges.narrator_pair("wuthering", "Martha Bryce") == (
        "Martha Bryce",
        "Pembury",
    )


CAST = ["Aldous Kerr", "Nora Vance", "Verhoeven", "Cora Ellis"]
SCENE = (
    "We waited in the hall while the clock struck.\n\n"
    "“Come here,” said Nora. “Now, before it is too late.”\n\n"
    "“He is coming,” he said.\n\n"
    "“We must hold the door,” Verhoeven said, raising the lamp.\n\n"
    "“I am not afraid,” said the doctor.\n\n"
    "Cora smiled. “Then let him come.”"
)


def test_tagged_lines_keep_only_tag_fixed_speakers():
    found = judges.tagged_lines(SCENE, judges._members(CAST))
    assert [ln["speaker"] for ln in found["lines"]] == ["Nora Vance", "Verhoeven"]
    # a split quote joins its tagged line
    assert found["lines"][0]["line"] == "Come here, Now, before it is too late."
    # pronoun tag, an epithet, an action beat: left out, counted
    assert found["excluded"] == 3


def test_blinded_scene_removes_tags_and_numbers_kept_lines():
    found = judges.tagged_lines(SCENE, judges._members(CAST))
    text = judges.blinded_scene(found)
    assert "said Nora" not in text and "Verhoeven said" not in text
    assert "he said" not in text and "said the doctor" not in text
    assert '[1] "Come here,"' in text and '[1] "Now, before it is too late."' in text
    assert '[2] "We must hold the door,"' in text and "raising the lamp" in text
    assert "Cora smiled." in text  # an action beat stays: it is not a tag


def test_m3_parse_needs_the_right_length_and_one_cast_name():
    cast = ["Nora Vance", "Verhoeven"]
    assert judges.m3_parse({"speakers": ["Nora Vance", "verhoeven"]}, cast, 2) == [
        "Nora Vance",
        "Verhoeven",
    ]
    assert judges.m3_parse({"speakers": ["Nora", "someone"]}, cast, 2) == [
        "Nora Vance",
        None,
    ]
    assert judges.m3_parse({"speakers": ["Nora Vance"]}, cast, 2) is None
    assert judges.m3_parse({}, cast, 1) is None
    assert judges.m3_parse({"speakers": [3]}, cast, 1) == [None]


NARRATION = (
    "I stood at the window for a long while and watched the road below, where "
    "the carts went by in the grey light and the drivers called to one another "
    "in a tongue I did not know. The wind came down from the hills and shook the "
    "shutters, and I thought of home, of the quiet streets and the lamps, and of "
    "her letters, which I had read so often that the paper had worn thin."
)


class FakeJudge:
    """Answers RN-v2 with label 1 and names the first CAST member for every
    line; can refuse or hit the spend ceiling after some calls."""

    exhausted, fell_back = False, False

    def __init__(self, provider_id, refuse_at=(), pause_after=None):
        self.provider_id, self.prompts = provider_id, []
        self.refuse_at, self.pause_after = set(refuse_at), pause_after

    async def ask_json(self, prompt):
        from app.llm.errors import SpendPausedError, UnusableReplyError

        if self.pause_after is not None and len(self.prompts) >= self.pause_after:
            raise SpendPausedError("monthly ceiling")
        self.prompts.append(prompt)
        if len(self.prompts) - 1 in self.refuse_at:
            raise UnusableReplyError("refused", spent=("", 0, 0, 0))
        if "SPEAKER" in prompt.upper() and "CAST:" in prompt:
            cast = prompt.split("CAST: ")[1].split("\n")[0].split(", ")
            n = int(prompt.rsplit("exactly ", 1)[1].split(" names")[0])
            return {"speakers": [cast[0]] * n}
        return {"narrator": 1}


def _chapters_run(tmp_path, run_id, rows, ablation=None, sha="a" * 40):
    res = {"outline_digest": "dig", "batch": "b", "chapters": rows}
    _report(
        tmp_path / f"{run_id}/eval-report-{run_id}/eval-report-dracula.json",
        "dracula",
        {"chapters": res},
        sha=sha,
        ablation=ablation,
    )


GT = {"cast": CAST + ["Count Vasska", "Elias Ward", "Cassidy Boone"]}


async def test_narration_judges_records_both_measures_blind(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    monkeypatch.delenv("MONTHLY_COST_CEILING_USD", raising=False)
    scene = NARRATION + "\n\n" + SCENE
    _chapters_run(
        tmp_path,
        "501",
        [
            _row(1, [scene, NARRATION + " Again."], beats=[{"characters": CAST}] * 2),
            _row(2, [scene], pov="Cora Ellis"),  # no voice source: M3 only
            _row(3, [], failure="quota"),  # not judged
        ],
        ablation="no_voices",
    )
    sonnet, free = FakeJudge("anthropic"), FakeJudge("groq")
    from evals.chapters import compare

    monkeypatch.setattr(compare, "judges", lambda: {"sonnet": sonnet, "free": free})
    res = await judges.step_narration_judges(
        SimpleNamespace(book="dracula", ground_truth=GT)
    )
    n = res["narration"]
    assert n["stopped"] is None and n["judges"] == {
        "sonnet": "anthropic",
        "free": "groq",
    }
    by = {c["position"]: c for c in n["chapters"]}
    one = by[1]
    assert one["run"] == "501" and one["ablation"] == "no_voices"
    assert one["m2"] == {"planned": "Aldous Kerr", "donor": "Nora Vance"}
    m2 = one["m2_judges"]["sonnet"]
    assert m2["total"] == 2 and m2["rated"] == 2
    assert m2["correct"] == sum(k["answer"] == 1 for k in one["m2_key"])
    m3 = one["m3"]["sonnet"]
    # "said the doctor": the one doctor of the scene (Verhoeven)
    assert m3["eligible"] == 3 and one["m3_excluded"] == 2
    assert "m2_judges" in by[2] and by[2]["m2_judges"] == {}
    assert by[2]["m2"]["skipped"].startswith("no voice source")
    assert by[3]["judged"] is False and by[3]["m3"] == {}
    for p in sonnet.prompts:  # the key, the run, the arm never reach the judge
        assert "501" not in p and "no_voices" not in p and '"answer"' not in p
        assert "said Nora" not in p
    assert len(sonnet.prompts) == len(free.prompts)


async def test_a_refusal_leaves_items_unrated_and_a_spend_pause_stops(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    monkeypatch.delenv("MONTHLY_COST_CEILING_USD", raising=False)
    scene = NARRATION + "\n\n" + SCENE
    _chapters_run(tmp_path, "601", [_row(1, [scene]), _row(2, [scene])])
    from evals.chapters import compare

    sonnet = FakeJudge("anthropic", refuse_at={0})
    monkeypatch.setattr(compare, "judges", lambda: {"sonnet": sonnet, "free": None})
    n = (
        await judges.step_narration_judges(
            SimpleNamespace(book="dracula", ground_truth=GT)
        )
    )["narration"]
    first = n["chapters"][0]["m2_judges"]["sonnet"]
    assert first["refused"] == 1 and first["rated"] == 0
    assert n["chapters"][1]["m2_judges"]["sonnet"]["rated"] == 1  # carried on

    paused = FakeJudge("anthropic", pause_after=2)
    monkeypatch.setattr(compare, "judges", lambda: {"sonnet": paused, "free": None})
    n = (
        await judges.step_narration_judges(
            SimpleNamespace(book="dracula", ground_truth=GT)
        )
    )["narration"]
    assert "SpendPausedError" in n["stopped"]
    assert n["chapters"][1]["m2_judges"] == {}  # nothing asked after the stop
    # Coverage counts completed judgments, never the plan's eligibility.
    assert n["chapters"][1]["eligible"] and not n["chapters"][1]["judged"]
    assert sum(c["judged"] for c in n["chapters"]) < len(n["chapters"])


async def test_the_worst_case_over_the_ceiling_asks_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_PRIOR_REPORTS", str(tmp_path))
    monkeypatch.setenv("MONTHLY_COST_CEILING_USD", "0.0001")
    _chapters_run(tmp_path, "701", [_row(1, [NARRATION + "\n\n" + SCENE])])
    from evals.chapters import compare

    sonnet = FakeJudge("anthropic")
    monkeypatch.setattr(compare, "judges", lambda: {"sonnet": sonnet, "free": None})
    res = await judges.step_narration_judges(
        SimpleNamespace(book="dracula", ground_truth=GT)
    )
    assert res["skipped"] and "ceiling" in res["reason"] and not sonnet.prompts


async def test_narration_validation_judges_the_source(monkeypatch, tmp_path):
    monkeypatch.delenv("MONTHLY_COST_CEILING_USD", raising=False)
    from evals.chapters import compare

    sonnet = FakeJudge("anthropic")
    monkeypatch.setattr(compare, "judges", lambda: {"sonnet": sonnet, "free": None})
    gt = json.loads(Path("evals/corpora/dracula/ground_truth.json").read_text("utf-8"))
    res = await judges.step_narration_validation(
        SimpleNamespace(book="dracula", ground_truth=gt)
    )
    v = res["narration_validation"]
    assert v["m3"] and v["m2"] and v["stopped"] is None
    assert {c["m2"]["planned"] for c in v["m2"]} == {
        "Aldous Kerr",
        "Nora Vance",
        "Elias Ward",
    }
    _report(
        tmp_path / "eval-report-9/eval-report-dracula.json",
        "dracula",
        {judges.VALIDATION_STEP: res},
    )
    s = judges.summarize_validation([tmp_path])
    assert s["m3"]["sonnet"]["scenes"] == len(v["m3"])
    assert 0 < s["m3"]["sonnet"]["chance"] <= 1
    assert s["m2"]["sonnet"]["rated"] > 0


def test_the_steps_are_opt_in():
    assert judges.STEP in run.OPT_IN and judges.VALIDATION_STEP in run.OPT_IN


# --- M3 widened (SPEC-96 decisions, 2026-10-30) -------------------------------


def _dracula():
    gt = json.loads(Path("evals/corpora/dracula/ground_truth.json").read_text("utf-8"))
    return judges.book_members("dracula", gt), judges.book_titles("dracula", gt)


def test_corpus_aliases_and_titles_come_from_the_corpus():
    members, titles = _dracula()
    aliases = {m["name"]: m["aliases"] for m in members}
    assert aliases["Elias Ward"] == ["Eli"] and aliases["Nora Vance"] == ["Winifred"]
    assert titles["professor"] == ["Verhoeven"]
    assert titles["doctor"] == ["Elias Ward", "Verhoeven"]
    assert "mrs" not in titles and "miss" not in titles  # courtesy titles
    gt = json.loads(
        Path("evals/corpora/wuthering/ground_truth.json").read_text("utf-8")
    )
    wm = {m["name"]: m["aliases"] for m in judges.book_members("wuthering", gt)}
    assert wm["Martha Bryce"] == ["Mattie"]  # "Martha Bryce (Mattie)"


def test_a_first_person_tag_is_the_narrator_only_when_one_is_given():
    members, titles = _dracula()
    text = "“Who is there?” I asked.\n\n“Open,” said I, and we waited."
    with_pov = judges.tagged_lines(
        text, members, ["Aldous Kerr"], "Aldous Kerr", titles
    )
    assert [ln["speaker"] for ln in with_pov["lines"]] == ["Aldous Kerr"] * 2
    without = judges.tagged_lines(text, members, ["Aldous Kerr"], None, titles)
    assert without["lines"] == [] and without["excluded"] == 2
    we = judges.tagged_lines("“Go,” we said.", members, ["Aldous Kerr"], "Aldous Kerr")
    assert we["lines"] == [] and we["excluded"] == 1


def test_titles_resolve_only_to_one_member_of_the_scene():
    members, titles = _dracula()
    text = "“It is the mark,” said the Professor.\n\n“Rest now,” the doctor said."
    one = judges.tagged_lines(text, members, ["Verhoeven", "Cora Ellis"], None, titles)
    assert [ln["speaker"] for ln in one["lines"]] == ["Verhoeven", "Verhoeven"]
    both = judges.tagged_lines(
        text, members, ["Verhoeven", "Elias Ward"], None, titles
    )  # "the doctor" is both of them
    assert [ln["speaker"] for ln in both["lines"]] == ["Verhoeven"]
    assert both["excluded"] == 1
    nobody = judges.tagged_lines(text, members, ["Cora Ellis"], None, titles)
    assert nobody["lines"] == []
    # an alias the corpus attests
    eli = judges.tagged_lines(
        "“Come,” said Eli.", members, ["Elias Ward"], None, titles
    )
    assert [ln["speaker"] for ln in eli["lines"]] == ["Elias Ward"]


def test_a_shared_name_resolves_within_the_scene_but_not_with_a_courtesy_title():
    members = judges._members(["Varrick", "Penhale Varrick", "Aubrey Penhale"])
    text = "“Get out,” Varrick said."
    assert judges.resolve_tag("Varrick", members) is None  # two members
    one = judges.tagged_lines(text, members, ["Penhale Varrick", "Aubrey Penhale"])
    assert [ln["speaker"] for ln in one["lines"]] == ["Penhale Varrick"]
    mrs = judges.tagged_lines(
        "“Sit,” said Mrs. Varrick.", members, ["Varrick", "Aubrey Penhale"]
    )
    assert mrs["lines"] == [] and mrs["excluded"] == 1
    assert judges.resolve_tag("the Mrs", members, ["Varrick"], titles={}) is None


def test_pronoun_tags_stay_left_out():
    members, titles = _dracula()
    got = judges.tagged_lines(
        "“Hush,” she said.", members, ["Cora Ellis"], "Aldous Kerr", titles
    )
    assert got["lines"] == [] and got["excluded"] == 1


def test_the_narrator_is_the_pov_of_a_first_person_chapter_only():
    members, _ = _dracula()
    first = [FIRST_PAST]
    third = [contract.to_third(FIRST_PAST)]
    row = {"pov": "Aldous Kerr"}
    assert judges.chapter_narrator(row, first, members) == "Aldous Kerr"
    assert judges.chapter_narrator(row, third, members) is None
    assert judges.chapter_narrator({"pov": "Stranger"}, first, members) is None
    spec = json.loads(Path("evals/corpora/dracula/spec.json").read_text("utf-8"))
    assert judges.source_narrators("dracula", spec, members) == {
        "CHAPTER II": "Aldous Kerr",
        "CHAPTER VIII": "Nora Vance",
        "CHAPTER X": "Elias Ward",
    }


def test_narrator_credited_lines_and_chance_are_recorded():
    members, titles = _dracula()
    text = "“Who is there?” I asked.\n\n“It is the mark,” said the Professor."
    item = judges.m3_item(
        text, ["Verhoeven"], members, random.Random(0), "Aldous Kerr", titles
    )
    assert item["answers"] == ["Aldous Kerr", "Verhoeven"]
    assert item["narrator_lines"] == [True, False]
    assert item["chance_sum"] == 2 / 2  # two lines, a cast of two


# --- analysis (principal review, 2026-10-30) ----------------------------------------


def test_sign_flip_exact_enumeration_matches_brute_force():
    import itertools

    means = [0.3, -0.1, 0.25, 0.05, 0.4, -0.2, 0.15]
    obs = abs(sum(means))
    brute = sum(
        abs(sum(s * m for s, m in zip(signs, means))) >= obs - 1e-12
        for signs in itertools.product((1, -1), repeat=len(means))
    ) / 2 ** len(means)
    assert analyze.sign_flip_p(means) == pytest.approx(brute)
    assert analyze.sign_flip_p([0.1] * 6) == pytest.approx(2 / 64)  # the two extremes
    assert analyze.sign_flip_p([0.2, -0.2]) == 1.0
    assert analyze.sign_flip_p([]) is None
    # beyond EXACT_MAX: seeded draws
    p = analyze.sign_flip_p([0.1] * 22, draws=2000)
    assert p == pytest.approx(1 / 2001)


def test_t_interval_uses_k_minus_one_df():
    assert analyze.t_crit(10) == 2.228 and analyze.t_crit(1000) < 1.97
    assert 2.000 < analyze.t_crit(45) < 2.021
    ci = analyze.t_interval([0.1, 0.2, 0.3])
    assert ci == [
        round(0.2 - 4.303 * 0.1 / 3**0.5, 4),
        round(0.2 + 4.303 * 0.1 / 3**0.5, 4),
    ]
    assert analyze.t_interval([0.1]) is None


def test_the_sign_flip_tests_null_rate_is_at_most_alpha():
    # 11 clusters x 3 pairs under the null, 300 simulated studies
    assert analyze.power(11, 0.0, 0.3, reps=3, alpha=0.05, sims=300) <= 0.05
    rng, rejected = random.Random(1), 0
    for _ in range(300):
        means = [sum(rng.gauss(0, 0.3) for _ in range(3)) / 3 for _ in range(11)]
        rejected += analyze.sign_flip_p(means) <= 0.05
    assert rejected / 300 <= 0.05


def test_power_rises_with_the_effect():
    small = analyze.power(11, -0.05, 0.3, reps=2, sims=100)
    large = analyze.power(11, -0.4, 0.3, reps=2, sims=100)
    assert large > small and large >= 0.8


def test_holm_step_down():
    got = analyze.holm({"P": 0.01, "N": 0.04, "V": 0.03})
    assert got["P"] == {"p_holm": 0.03, "reject": True}
    assert got["V"] == {"p_holm": 0.06, "reject": False}
    assert got["N"] == {"p_holm": 0.06, "reject": False}
    assert analyze.holm({"P": None, "N": 0.001})["P"]["reject"] is False


def test_dates_only_prints_created_at():
    got = analyze.dates(["1"], "o/r", gh=lambda rid: "2026-11-01T06:30:00Z")
    assert got == {"1": "2026-11-01T06:30:00Z"}


def _arm(
    run,
    arm,
    pos,
    day,
    person=0.0,
    m2=None,
    m3=None,
    failure=None,
    scenes=2,
    rep=1,
    book="dracula",
):
    row = {
        "run": run,
        "arm": arm,
        "book": book,
        "position": pos,
        "replicate": rep,
        "day": day,
        "outline_digest": "dig",
        "failure": failure,
        "scenes": scenes,
        "judged": True,
        "first_person": True,
        "sample_overlap": 0.0,
        "person_mismatch": person,
        "person_mismatch_window": person,
        "tense_mismatch": 0.0,
        "quote_mismatch": 0.0,
        "m3_lines": 2,
        "m3_narrator_lines": 1,
        "m3_chance_sum": 2 / 3,
    }
    for j in analyze.JUDGES:
        row[f"m2 {j}"], row[f"m3 {j}"], row[f"m3_own {j}"] = m2, m3, m3
    return row


def test_pairs_within_a_ledger_day_and_book():
    rows = [
        _arm("a1", "A", 1, "W21 day 1"),
        _arm("a1", "A", 2, "W21 day 1", failure="quota"),
        _arm("p1", "P", 1, "W21 day 1"),
        _arm("p1", "P", 2, "W21 day 1"),  # A's side failed for quota
        _arm("p1", "P", 3, "W21 day 1"),  # A never wrote it
        _arm("p2", "P", 1, "W21 day 2"),  # no A that day
    ]
    prs, counts = analyze.pairs(rows, "P")
    assert [(t["run"], c["run"]) for t, c in prs] == [("p1", "a1")]
    assert counts["unpaired"] == 2
    assert counts["excluded"] == {"treatment": {}, "A": {"quota": 1}}
    with pytest.raises(SystemExit, match="A runs"):
        analyze.pairs(rows + [_arm("a9", "A", 1, "W21 day 1")], "P")


def _study(p_person=0.1, n_m2=0.8, a_m2=0.8, a_m3=0.9, days=3):
    rows = []
    books = ("dracula", "wuthering", "frankenstein")
    for d in range(1, days + 1):
        day, b = f"W21 day {d}", {"book": books[(d - 1) % 3]}
        for pos in range(1, 7):
            rows.append(_arm(f"A{d}", "A", pos, day, 0.5, m2=a_m2, m3=a_m3, **b))
            rows.append(
                _arm(f"P{d}", "P", pos, day, p_person + pos / 100, m2=a_m2, **b)
            )
            rows.append(
                _arm(f"N{d}", "N", pos, day, 0.5, m2=n_m2 - pos / 100, m3=a_m3, **b)
            )
            rows.append(_arm(f"V{d}", "V", pos, day, 0.5, m3=a_m3 - 0.3, **b))
    return rows


def test_family_is_p_and_n_and_h3_is_descriptive():
    # six clusters: the smallest sign-flip p is 2/64 > Holm's 0.025
    six = analyze.analyze(_study(days=1), resamples=300)
    assert six["primary"]["P"]["p"] == pytest.approx(2 / 64)
    assert not six["primary"]["P"]["reject"]
    res = analyze.analyze(_study(), resamples=300)
    assert res["family"] == ["P", "N"]
    p = res["primary"]["P"]
    assert p["measure"] == "person_mismatch_window" and p["n_clusters"] == 18
    assert p["p"] == pytest.approx(2 / 2**18) and p["reject"]  # every sign negative
    assert p["t_ci"][1] < 0 and "bootstrap (descriptive)" in p
    assert res["decisions"][0].startswith("P: dropping the default POV mode lowers")
    assert res["primary"]["V"]["tested"] is False
    assert res["decisions"][2].startswith("V: descriptive only")
    assert res["contrasts"]["V"]["counts"]["m3_eligible_lines"] == {
        "treatment": 36,
        "A": 36,
    }
    # a measure that failed validation leaves the family
    res = analyze.analyze(_study(), {"P": False}, resamples=300)
    assert res["family"] == ["N"] and "failed validation" in res["decisions"][0]
    assert "decisions:" in analyze.render(res)


def test_sensitivities_are_reported_beside_the_primaries():
    res = analyze.analyze(_study(), resamples=300)
    s = res["sensitivities"]
    assert s["a: H1 on sentence-level person_mismatch"]["direction_agrees"] is True
    assert s["b: N vs A on person_mismatch_window"]["d"] == 0
    assert s["c: H2 on pairs both first person"]["pairs_first_person"] == 18
    assert s["d: M3 without narrator-credited lines"]["d"] == pytest.approx(-0.3)


def test_instrument_floor_makes_h2_and_m3_undetermined():
    res = analyze.analyze(_study(a_m2=0.5, n_m2=0.5, a_m3=0.5), resamples=300)
    assert res["floors"]["N"]["at_floor"] and res["floors"]["V"]["at_floor"]
    assert "undetermined: instrument at floor on generated text" in res["decisions"][1]
    assert "candidate for removal" not in res["decisions"][1]
    assert "undetermined" in res["decisions"][2]
    ok = analyze.analyze(_study(a_m3=0.95), resamples=300)  # chance 1/3 + 0.2 < 0.95
    assert not ok["floors"]["V"]["at_floor"] and not ok["floors"]["N"]["at_floor"]


def test_per_arm_reports_outcomes_and_sample_overlap():
    rows = _study(days=1) + [
        _arm("P1", "P", 9, "W21 day 1", failure="product", scenes=0)
    ]
    out = analyze.per_arm(rows)
    assert out["P"]["failure"] == {"none": 6, "product": 1}
    assert out["P"]["no_scenes"] == 1
    assert out["A"]["sample_overlap_8gram"]["chapters"] == 6


def test_chapters_refuse_other_apps_and_runs_without_a_day(tmp_path):
    _chapters_run(tmp_path, "801", [_row(1, [FIRST_PAST])])
    _chapters_run(
        tmp_path, "802", [_row(1, [contract.to_third(FIRST_PAST)])], "pov_fix"
    )
    rec = {
        "run": "801",
        "position": 1,
        "replicate": 1,
        "m2_judges": {"sonnet": {"correct": 1, "rated": 2}},
        "m3": {"sonnet": {"correct": 1, "rated": 2, "correct_own": 0, "rated_own": 1}},
        "m3_lines": 2,
    }
    _report(
        tmp_path / "900/eval-report-900/eval-report-dracula.json",
        "dracula",
        {judges.STEP: {"narration": {"chapters": [rec]}}},
    )
    days = {"801": "W21 day 1", 802: "W21 day 1"}
    rows = analyze.chapters([tmp_path], days, "a" * 40)
    by = {r["run"]: r for r in rows}
    assert by["801"]["arm"] == "A" and by["802"]["arm"] == "P"
    assert by["801"]["m2 sonnet"] == 0.5 and by["801"]["m3_own sonnet"] == 0.0
    assert by["801"]["first_person"] and not by["802"]["first_person"]
    assert by["802"]["judged"] is False
    assert len(analyze.pairs(rows, "P")[0]) == 1
    with pytest.raises(SystemExit, match="not " + "b" * 40):
        analyze.chapters([tmp_path], days, "b" * 40)
    with pytest.raises(SystemExit, match="no ledger day"):
        analyze.chapters([tmp_path], {"801": "W21 day 1"}, "a" * 40)


def _decide(contrast, d, reject, k=17, p_holm=0.01):
    s = {"d": d, "t_ci": [d - 0.1, d + 0.1], "n_pairs": 2 * k, "n_clusters": k}
    return analyze.decision(
        contrast, s, {"reject": reject, "p_holm": p_holm}, None, None
    )


def test_h2_decision_wording_matches_the_pre_registration():
    held = _decide("N", -0.2, True)
    assert (
        "without the book-narration excerpts, the judge attributes the narration "
        "to the planned narrator less often by 0.2" in held
    )
    assert "candidate for removal" not in held
    null = _decide("N", -0.05, False)
    assert (
        "no effect of the excerpts on narrator attribution detectable at n = 34" in null
    )
    assert "the lever becomes a candidate for removal, decided with E4's result" in null
    rev = _decide("N", 0.2, True)
    assert "the excerpts mislead attribution (d = 0.2)" in rev
    assert "excerpt selection is revisited in the next product wave" in rev
    assert "candidate for removal" not in rev
    floored = analyze.decision(
        "N",
        {"d": -0.2, "n_pairs": 34, "n_clusters": 17},
        {"reject": True},
        None,
        {"at_floor": True, "a_mean": 0.5, "t_ci": [0.4, 0.6]},
    )
    assert "undetermined: instrument at floor on generated text" in floored


def test_fewer_than_seven_clusters_is_undetermined():
    for c in ("P", "N"):
        line = _decide(c, -0.3, True, k=6)
        assert line.startswith(f"{c}: undetermined: too few clusters (k = 6 < 7")
    assert "lowers person mismatch" in _decide("P", -0.3, True, k=7)


def test_pairs_report_counts_per_book():
    _, counts = analyze.pairs(_study(days=1), "P")
    assert sum(counts["pairs_by_book"].values()) == counts["pairs"]


def test_judgments_are_keyed_by_book_within_one_run(tmp_path):
    rec = {"run": "950", "position": 1, "replicate": 1}
    for book, rated in (("dracula", 2), ("wuthering", 4)):
        _report(
            tmp_path / f"951/eval-report-951/eval-report-{book}.json",
            book,
            {
                judges.STEP: {
                    "narration": {
                        "chapters": [
                            rec
                            | {"m2_judges": {"sonnet": {"correct": 1, "rated": rated}}}
                        ]
                    }
                }
            },
        )
    js = analyze.judged([tmp_path])
    assert js[("950", "dracula", 1, 1)]["m2_judges"]["sonnet"]["rated"] == 2
    assert js[("950", "wuthering", 1, 1)]["m2_judges"]["sonnet"]["rated"] == 4
