"""Unit tests for the eval graders — pure logic, no network/LLM, CI-gated.

These verify the *measurement* is correct on synthetic data, so a grader can't
silently report a wrong score. The graders' real inputs (API + embedder) are
exercised in the local end-to-end dry run, not here.
"""

import pytest

from evals.graders import attribution, continuity, extraction, fidelity, retrieval
from evals.graders import plot
from evals.steps import pipeline

pytestmark = pytest.mark.unit


class TestVecMath:
    def test_cosine_and_centroid(self):
        from evals.harness.vecmath import centroid, cosine

        assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
        assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
        assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0  # zero-norm safe
        assert centroid([[0.0, 2.0], [2.0, 0.0]]) == [1.0, 1.0]
        assert centroid([]) == []


class TestStepRegistry:
    def test_all_six_steps_registered_in_order(self):
        from evals.run import all_steps, get_step  # registers every step

        assert all_steps() == [
            "extraction",
            "ingestion",
            "retrieval",
            "outline",
            "continuity",
            "prose",
            "revise",
            "ensemble",
            "attribution",  # last of the pipeline's (S-3)
            "baseline",  # W12-5, never in a gated run
            "rnrc_validation",  # W19 S7, asked for by name only
            "rn2_validation",  # SPEC-95 E2, asked for by name only
        ]
        # embedding-only steps must not require the API (so they run keyless)
        assert get_step("retrieval").needs_api is False
        assert get_step("extraction").needs_api is True
        assert get_step("ingestion").needs_api is True

    def test_unknown_step_raises(self):
        from evals.steps.base import get_step

        with pytest.raises(KeyError):
            get_step("nope")


class TestTracerExport:
    def _run(self):
        return {
            "book": "dracula",
            "app_sha": "abc123",
            "model": "gemini-2.5-flash",
            "steps": {
                "extraction": {
                    "score": 0.8,
                    "f1": 0.8,
                    "precision": 1.0,
                    "recall": 0.67,
                },
                "retrieval": {"score": 0.4, "precision_at_k": 0.4, "mrr": 0.5},
                "outline": {"score": 0.9, "judge_explanation": "recovers the arc"},
                "skipped_step": {"skipped": True, "reason": "no creds"},
                "errored_step": {"error": "boom"},
            },
        }

    def test_maps_to_tracer_run_input(self):
        from evals.harness import report

        out = report.tracer_export(self._run(), model="gemini-2.5-flash", env="prod")
        assert out["tool"] == "polyphony"
        assert out["model"] == "gemini-2.5-flash"
        assert out["mode"] == "eval" and out["env"] == "prod"
        assert out["git_sha"] == "abc123"
        # skipped + errored steps are dropped; 3 scored steps remain.
        names = {c["name"] for c in out["cases"]}
        assert names == {"eval:extraction", "eval:retrieval", "eval:outline"}
        # pass/fail per the 0.5 floor: extraction(.8)✓ outline(.9)✓ retrieval(.4)✗
        by = {c["name"]: c for c in out["cases"]}
        assert by["eval:extraction"]["passed"] and by["eval:outline"]["passed"]
        assert not by["eval:retrieval"]["passed"]
        assert out["pass_rate"] == pytest.approx(2 / 3, abs=1e-3)
        assert out["passed"] is False  # not all cases passed
        # judge rationale rides through on the case that has one.
        assert by["eval:outline"]["judge_rationale"] == "recovers the arc"

    def test_all_pass_sets_run_passed_true(self):
        from evals.harness import report

        run = {
            "app_sha": "s",
            "steps": {"outline": {"score": 0.9}, "prose": {"score": 0.7, "words": 400}},
        }
        out = report.tracer_export(run, model="m")
        assert out["passed"] is True and out["pass_rate"] == 1.0
        # A reported step is trended as a case but never fails the run (SPEC-60).
        run["steps"]["attribution"] = {"score": 0.4, "status": "reported"}
        out = report.tracer_export(run, model="m")
        assert len(out["cases"]) == 3 and out["passed"] is True
        assert out["pass_rate"] == 1.0
        assert "eval:attribution" not in {
            c["name"] for c in report.greenlight_export(run | {"book": "b"})["checks"]
        }

    def test_a_reported_only_run_does_not_pass(self):
        from evals.harness import report

        run = {"steps": {"attribution": {"score": 0.9, "status": "reported"}}}
        out = report.tracer_export(run, model="m")
        assert (out["passed"], out["pass_rate"], len(out["cases"])) == (False, 0.0, 1)

    def test_scores_bounded_0_1_for_zod(self):
        # Tracer's zod caps case.score at [0,1]; the export must clamp so an
        # out-of-range score can't 422 the whole payload.
        from evals.harness import report

        out = report.tracer_export(
            {"steps": {"hi": {"score": 1.4}, "lo": {"score": -0.2}}}, model="m"
        )
        by = {c["name"]: c["score"] for c in out["cases"]}
        assert by["eval:hi"] == 1.0 and by["eval:lo"] == 0.0

    def test_noise_band_folded_into_case_output(self):
        # score_std has no Tracer field, so it must ride in the case output text.
        from evals.harness import report

        run = {
            "app_sha": "s",
            "steps": {"outline": {"score": 0.7, "score_std": 0.05, "repeats": 3}},
        }
        out = report.tracer_export(run, model="m")
        assert "±0.05" in out["cases"][0]["output"]
        assert "n=3" in out["cases"][0]["output"]


class TestAggregate:
    def test_mean_std_over_passes(self):
        from evals.run import _aggregate

        passes = [
            {"outline": {"score": 0.8, "n_nodes": 6}},
            {"outline": {"score": 0.6, "n_nodes": 6}},
            {"outline": {"score": 0.7, "n_nodes": 6}},
        ]
        out = _aggregate(passes)
        assert out["outline"]["score"] == pytest.approx(0.7, abs=1e-3)
        assert out["outline"]["score_std"] > 0
        assert out["outline"]["repeats"] == 3
        assert out["outline"]["score_samples"] == [0.8, 0.6, 0.7]
        # non-score fields survive from the last pass.
        assert out["outline"]["n_nodes"] == 6

    def test_single_pass_unchanged(self):
        from evals.run import _aggregate

        passes = [{"retrieval": {"score": 0.82, "mrr": 0.9}}]
        out = _aggregate(passes)
        assert out["retrieval"] == {"score": 0.82, "mrr": 0.9}
        assert "score_std" not in out["retrieval"]

    def test_skipped_and_errored_steps_pass_through(self):
        from evals.run import _aggregate

        passes = [
            {"a": {"skipped": True}, "b": {"error": "x"}},
            {"a": {"skipped": True}, "b": {"error": "x"}},
        ]
        out = _aggregate(passes)
        assert out["a"] == {"skipped": True} and out["b"] == {"error": "x"}

    def test_error_on_last_pass_does_not_drop_valid_mean(self):
        # A transient error on the FINAL repeat must not carry an 'error' key onto
        # the aggregated result (which would drop the whole step from export).
        from evals.run import _aggregate

        passes = [
            {"outline": {"score": 0.8, "n_nodes": 6}},
            {"outline": {"score": 0.6, "n_nodes": 6}},
            {"outline": {"error": "TimeoutError: boom"}},
        ]
        out = _aggregate(passes)
        assert "error" not in out["outline"]
        assert out["outline"]["score"] == pytest.approx(0.7, abs=1e-3)
        assert out["outline"]["repeats"] == 2
        assert out["outline"]["n_nodes"] == 6


class TestExtraction:
    def test_perfect(self):
        s = extraction.grade_extraction(
            ["Nora Vance", "Verhoeven"], ["Nora Vance", "Verhoeven"]
        )
        assert s.precision == 1.0 and s.recall == 1.0 and s.f1 == 1.0

    def test_honorifics_and_partial_names_match(self):
        s = extraction.grade_extraction(
            ["Prof. Verhoeven", "Nora"], ["Verhoeven", "Nora Vance"]
        )
        assert s.recall == 1.0  # both gold names hit despite honorific / partial

    def test_missed_and_spurious(self):
        s = extraction.grade_extraction(
            ["Nora Vance", "Ghost"], ["Nora Vance", "Verhoeven"]
        )
        assert "Verhoeven" in s.missed
        assert "Ghost" in s.spurious
        assert s.recall == 0.5
        assert s.precision == 0.5

    def test_shared_surname_is_not_a_match(self):
        # 'John Ward' must NOT match gold 'Elias Ward' (subset, not overlap) —
        # else a wrong prediction inflates both precision and recall.
        assert extraction.match("John Ward", "Elias Ward") is False
        s = extraction.grade_extraction(["John Ward"], ["Elias Ward"])
        assert s.precision == 0.0 and s.recall == 0.0

    def test_bare_honorific_and_title_prefix_match(self):
        assert extraction.match("Count", "Count Vasska") is True  # bare honorific
        assert extraction.match("Mr. Kerr", "Aldous Kerr") is True  # honorific+surname
        assert extraction.match("Prof. Verhoeven", "Verhoeven") is True

    def test_empty_prediction(self):
        s = extraction.grade_extraction([], ["Nora Vance"])
        assert s.precision == 0.0 and s.recall == 0.0 and s.f1 == 0.0

    def test_a_real_secondary_is_not_spurious(self):
        """W3-7: run-W25's six names, with the corpus' secondary cast."""
        cast = ["Milo Voss", "Zara Okafor", "Edric Thane"]
        predicted = [*cast, "Halvard Bell", "Old Fen", "Prava"]
        s = extraction.grade_extraction(
            predicted, cast, secondary=["Prava", "Old Fen", "Halvard Bell"]
        )
        assert s.precision == 1.0 and s.recall == 1.0 and s.spurious == []
        assert s.secondary_found == ["Halvard Bell", "Old Fen", "Prava"]


class TestAttribution:
    def _refs(self):
        # two separable "voices" in 2-D: character A along x, B along y.
        return {"A": [[1.0, 0.0], [0.9, 0.1]], "B": [[0.0, 1.0], [0.1, 0.9]]}

    def test_correct_attribution(self):
        r = attribution.attribute("A", [0.95, 0.05], self._refs())
        assert r.correct and r.predicted == "A"

    def test_wrong_attribution_is_marked(self):
        r = attribution.attribute("A", [0.0, 1.0], self._refs())  # clearly B
        assert not r.correct and r.predicted == "B"
        assert r.margin > 0

    def test_accuracy_aggregate_and_chance(self):
        refs = self._refs()
        results = [
            attribution.attribute("A", [1.0, 0.0], refs),
            attribution.attribute("B", [0.0, 1.0], refs),
            attribution.attribute("A", [0.0, 1.0], refs),  # wrong
        ]
        agg = attribution.accuracy(results)
        assert agg["n"] == 3 and agg["correct"] == 2
        assert agg["accuracy"] == pytest.approx(2 / 3, abs=1e-3)
        assert agg["chance"] == 0.5

    def test_gap_is_top1_minus_top2_and_mean_gap_averages_it(self):
        """W3-7: run-W25's Milo line ranked Milo 0.1907 over Edric 0.1878."""
        milo = attribution.AttributionResult(
            "Milo", "Milo", True, 0.0, [("Milo", 0.1907), ("Edric", 0.1878)]
        )
        zara = attribution.AttributionResult(
            "Zara", "Zara", True, 0.0, [("Zara", 0.2346), ("Edric", 0.1166)]
        )
        assert milo.gap == pytest.approx(0.003, abs=5e-4)
        assert milo.as_dict()["gap"] == 0.0029
        mean = attribution.accuracy([milo, zara])["mean_gap"]
        assert mean == pytest.approx((0.0029 + 0.118) / 2, abs=1e-4)

    def test_topic_baseline_lets_an_off_topic_voice_win_its_own_line(self):
        # (F) W15-4: A and B share the probe's topic (dim 4), C does not. Each
        # baseline is the centroid's mean similarity to the OTHER characters'
        # generated lines; C's own line ranks C last raw, first less baselines.
        refs = {"A": [[1, 0, 0, 1]], "B": [[0, 1, 0, 1]], "C": [[0, 0, 1, 0]]}
        gen = [("A", [1, 0, 0, 2]), ("B", [0, 1, 0, 2]), ("C", [0, 0, 1, 2])]
        base = attribution.baselines(refs, gen)
        assert base == pytest.approx({"A": 0.6325, "B": 0.6325, "C": 0.0}, abs=1e-4)
        assert attribution.attribute("C", gen[2][1], refs).ranking[-1][0] == "C"
        assert all(attribution.attribute(c, v, refs, base).correct for c, v in gen)


class TestRetrieval:
    def test_precision_and_mrr(self):
        # owner map: each chunk text -> character.
        owner = {"a1": "A", "a2": "A", "b1": "B"}

        def retrieve(character, query, k):
            # A's query returns [a1(correct), b1(wrong)] -> p=0.5, rr=1.0
            # B's query returns [b1(correct)]            -> p=1.0, rr=1.0
            return {"A": ["a1", "b1"], "B": ["b1"]}[character][:k]

        score = retrieval.grade_retrieval(
            [("A", "qa"), ("B", "qb")], retrieve, lambda c: owner[c], k=3
        )
        assert score.precision_at_k == pytest.approx(0.75)  # (0.5 + 1.0)/2
        assert score.mrr == pytest.approx(1.0)

    def test_miss_gives_zero_rr(self):
        owner = {"x": "OTHER"}
        score = retrieval.grade_retrieval(
            [("A", "q")], lambda c, q, k: ["x"], lambda c: owner[c], k=3
        )
        assert score.precision_at_k == 0.0 and score.mrr == 0.0


class TestContinuity:
    INJ = [
        {
            "id": "name",
            "locate": "Aldous Kerr",
            "replace": "Aldous Karr",
            "expect_type": "character",
        },
        {
            "id": "date",
            "locate": "_5 May._",
            "replace": "_5 August._",
            "expect_type": "timeline",
        },
    ]

    def test_apply_injections_mutates_and_expects(self):
        text = "Aldous Kerr wrote on _5 May._ ... Aldous Kerr again."
        mutated, expected = continuity.apply_injections(text, self.INJ)
        assert "Aldous Karr" in mutated  # first occurrence changed
        assert mutated.count("Aldous Kerr") == 1  # the contradiction remains
        assert {e["id"] for e in expected} == {"name", "date"}

    def test_grade_detects_and_counts_no_false_positives_on_a_clean_control(self):
        clean = "Aldous Kerr on _5 May._ then Aldous Kerr."
        injected, expected = continuity.apply_injections(clean, self.INJ)
        injected_findings = [
            {
                "detail": "Name spelled Aldous Karr here but Aldous Kerr elsewhere",
                "refs": "",
            },
            # date contradiction NOT flagged -> should be 'missed'
        ]
        score = continuity.grade_continuity(
            expected, injected_findings, [(clean, [])], injected
        )
        assert "name" in score.detected
        assert "date" in score.missed
        assert score.detection_recall == 0.5
        assert score.control_fp_per_1k_words == 0.0
        assert score.control_flagged_share == 0.0

    # --- SPEC-94 S2b: false positives per 1,000 control words ----------------

    def test_false_positives_are_per_1000_control_words(self):
        # Old: min(1, 2 control findings / 2 injected errors) = 1.0 — a ratio
        # of two unrelated counts, and the same for a 10-word or 10k-word text.
        clean = " ".join(["word"] * 500)
        injected, expected = continuity.apply_injections(
            "Aldous Kerr _5 May._ Aldous Kerr", self.INJ
        )
        spurious = [{"detail": "spurious"}, {"detail": "spurious2"}]
        score = continuity.grade_continuity(expected, [], [(clean, spurious)], injected)
        assert score.control_fp_per_1k_words == 4.0  # 2 findings / 500 words
        assert score.control_flagged_share == 1.0
        out = score.as_dict()
        assert "false_positive_rate" not in out
        assert out["n_control_words"] == 500 and out["n_control_findings"] == 2

    def test_flagged_share_is_over_control_texts(self):
        texts = [(" ".join(["a"] * 100), [{"detail": "x"}]), ("b " * 300, [])]
        score = continuity.grade_continuity([], [], texts, "")
        assert score.control_flagged_share == 0.5
        assert score.control_fp_per_1k_words == 2.5  # 1 / 400 words
        assert score.n_control_texts == 2

    def test_no_control_text_reports_none_not_zero(self):
        score = continuity.grade_continuity([], [], [], "")
        assert score.control_fp_per_1k_words is None
        assert score.control_flagged_share is None

    # --- SPEC-94 S2c: a finding matches only the injected span --------------

    TEXT = "Aldous Kerr wrote on _5 May._ to his sister. Later Aldous Kerr left."

    def _graded(self, finding):
        injected, expected = continuity.apply_injections(self.TEXT, self.INJ)
        return continuity.grade_continuity(
            expected, [finding], [(self.TEXT, [])], injected
        ).detected

    def test_expected_spans_point_at_the_injected_text(self):
        injected, expected = continuity.apply_injections(self.TEXT, self.INJ)
        by_id = {e["id"]: e for e in expected}
        # Only the changed word: "Kerr" -> "Karr"; "May" -> "August".
        assert by_id["name"]["injected"] == "Karr"
        assert by_id["date"]["injected"] == "August"
        for e in expected:
            a, b = e["span"]
            assert injected[a:b] == e["injected"]

    def test_spans_shift_with_a_later_injection_before_them(self):
        inj = [
            {"id": "late", "locate": "left", "replace": "departed"},
            {"id": "early", "locate": "wrote", "replace": "scribbled"},
        ]
        injected, expected = continuity.apply_injections(self.TEXT, inj)
        for e in expected:
            a, b = e["span"]
            assert injected[a:b] == e["injected"], e
        assert [e["injected"] for e in expected] == ["departed", "scribbled"]

    def test_a_quote_of_the_original_elsewhere_is_not_a_catch(self):
        # Old: "Aldous Kerr" (the original) in the quote matched.
        assert (
            self._graded({"detail": "name clash", "quote": "Later Aldous Kerr left"})
            == []
        )

    def test_a_finding_quoting_the_other_side_is_credited_by_its_detail(self):
        # Review S2: a contradiction has two sides. A correct finding may quote
        # the untouched one ("Later Aldous Kerr left") and name the injected
        # one in its detail; "Aldous Karr" occurs once (at the injection).
        finding = {
            "detail": "Aldous Karr here vs Aldous Kerr later",
            "quote": "Later Aldous Kerr left",
        }
        assert self._graded(finding) == ["name"]
        # Naming only the original (all over the text) is still a token mention.
        finding = {"detail": "Aldous Kerr is named", "quote": "Later Aldous Kerr left"}
        assert self._graded(finding) == []

    def test_a_quote_overlapping_the_injected_span_is_a_catch(self):
        assert self._graded({"detail": "x", "quote": "“Aldous Karr wrote”"}) == ["name"]
        # Whitespace and case differences still locate the quote.
        assert self._graded({"detail": "x", "quote": "on  _5 AUGUST._"}) == ["date"]

    def test_a_short_quote_must_cover_the_whole_span(self):
        # "Aldous" (6 chars) sits beside "Karr" but not on it.
        assert self._graded({"detail": "x", "quote": "Aldous"}) == []
        assert self._graded({"detail": "x", "quote": "Karr"}) == ["name"]

    def test_without_a_quote_the_detail_must_name_the_replacement(self):
        assert self._graded({"detail": "first spelled Aldous Karr"}) == ["name"]
        assert self._graded({"detail": "Karr is a respelling"}) == ["name"]
        # Old: naming the original (all over the text) matched.
        assert self._graded({"detail": "Aldous Kerr is spelled two ways"}) == []

    # --- Review B2: typography normalised on both sides ----------------------

    TYPO = "We came to Mr. O’Niel’s house on 5 May 1801—I have just returned."
    TYPO_INJ = [
        {"id": "name", "locate": "O’Niel’s house", "replace": "O’Neil’s house"},
        {"id": "year", "locate": "1801—I have", "replace": "1901—I have"},
    ]

    def _typo(self, finding):
        injected, expected = continuity.apply_injections(self.TYPO, self.TYPO_INJ)
        return continuity.grade_continuity(
            expected, [finding], [(self.TYPO, [])], injected
        ).detected

    def test_straight_quotes_locate_a_curly_apostrophe(self):
        assert self._typo({"detail": "x", "quote": "Mr. O'Neil's house"}) == ["name"]

    def test_a_spaced_or_double_dash_locates_an_em_dash(self):
        assert self._typo({"detail": "x", "quote": "1901 — I have"}) == ["year"]
        assert self._typo({"detail": "x", "quote": "May 1901 -- I have"}) == ["year"]
        assert self._typo({"detail": "x", "quote": "1901 – I HAVE"}) == ["year"]

    def test_a_detail_with_straight_apostrophes_names_the_curly_replacement(self):
        assert self._typo({"detail": "it says O'Neil's house"}) == ["name"]

    def test_a_quote_that_locates_nowhere_falls_back_to_the_detail(self):
        finding = {"detail": "the year 1901 is out of order", "quote": "in 1901, I"}
        assert self._typo(finding) == ["year"]
        assert self._typo({"detail": "a date", "quote": "in 1901, I"}) == []

    # --- Review S6 + nit: overlapping injections, spans past the window ----

    def test_an_injection_overlapping_an_earlier_one_is_unapplied(self):
        inj = [
            {"id": "a", "locate": "Aldous Kerr wrote", "replace": "Aldous Karr wrote"},
            {"id": "b", "locate": "Karr wrote on", "replace": "Karr scribbled on"},
        ]
        text, expected, skipped = continuity.inject(self.TEXT, inj)
        assert [e["id"] for e in expected] == ["a"]
        assert skipped == [{"id": "b", "reason": "overlaps an earlier injection"}]
        e = expected[0]
        assert text[e["span"][0] : e["span"][1]] == e["injected"] == "Karr"
        why = continuity.unapplied(inj, expected, self.TEXT, skipped)
        assert why == [{"id": "b", "reason": "overlaps an earlier injection"}]

    def test_an_applied_span_past_the_window_is_unapplied(self):
        text = "aaa bbb ccc ddd"
        inj = [
            {"id": "grow", "locate": "aaa", "replace": "aaaaaaaaaa"},
            {"id": "end", "locate": "ddd", "replace": "eee"},
        ]
        mutated, expected, skipped = continuity.inject(text, inj, limit=len(text))
        assert len(mutated) > len(text)
        assert [e["id"] for e in expected] == ["grow"]
        assert skipped == [
            {"id": "end", "reason": "injected text past the graded window"}
        ]
        assert all(e["span"][1] <= len(text) for e in expected)

    def test_a_replacement_found_elsewhere_in_the_text_is_not_named_by_detail(self):
        text = "Two days after the audit, Milo slept. Two days before the trial."
        inj = [{"id": "t", "locate": "Two days after", "replace": "Two days before"}]
        injected, expected = continuity.apply_injections(text, inj)
        grade = continuity.grade_continuity
        named = [{"detail": "it says Two days before the audit"}]
        assert grade(expected, named, [(text, [])], injected).detected == []
        quoted = [{"detail": "x", "quote": "Two days before the audit"}]
        assert grade(expected, quoted, [(text, [])], injected).detected == ["t"]


# The canon of *Bored to Undeath*: leads Milo Voss & Zara Okafor vs the
# antagonist Edric Thane — "the CEL", the Chief Executive Lich — at Aeon Holdings.
# Edric's aliases are the DISAMBIGUATED forms only. The bare acronym "CEL" is
# deliberately NOT here: it is the ambiguous token the bug misread as a company
# ("Corporeal Energy Logistics"), so its mere presence must not count as the
# antagonist being present. "CEL" lives in the allowlist below (so it isn't
# flagged as an unknown invention), not in the principal's identity.
_PRINCIPALS = [
    {"name": "Milo Voss", "aliases": ["Milo"]},
    {"name": "Zara Okafor", "aliases": ["Zara"]},
    {"name": "Edric Thane", "aliases": ["Edric", "Chief Executive Lich", "the Lich"]},
]
_KNOWN = {"Aeon Holdings", "Aeon", "Undeath Pipeline", "Marrow District", "CEL"}

# The outline the model actually produced: a generic chosen-one rebellion whose
# hero, "Elara", is nowhere in the source, reading "the CEL" as a company,
# "Corporeal Energy Logistics".
_ELARA_OUTLINE = [
    {"title": f"Chapter {i}: {t}", "summary": s}
    for i, (t, s) in enumerate(
        [
            (
                "The Awakening",
                "Elara discovers her hidden power in the village of Ashford.",
            ),
            (
                "The Call",
                "Elara leaves home to join the Resistance against the Dark Lord.",
            ),
            (
                "Corporeal Energy Logistics",
                "The Resistance uncovers the CEL, the corporation draining the realm.",
            ),
            ("Allies", "Elara meets the rogue Kael and the mage Lyra."),
            ("The First Trial", "Elara's powers are tested in the Whispering Woods."),
            ("Betrayal", "A member of the Resistance sells Elara to the Dark Lord."),
            (
                "Captured",
                "Elara is imprisoned in the fortress of Corporeal Energy Logistics.",
            ),
            ("Escape", "Kael and Lyra break Elara free."),
            ("The Prophecy", "An oracle reveals Elara is the Chosen One."),
            ("Gathering the Army", "Elara rallies the free peoples of the realm."),
            ("The Siege", "The Resistance storms the Dark Lord's citadel."),
            ("The Chosen One", "Elara defeats the Dark Lord and frees the realm."),
        ],
        start=1,
    )
]

# A faithful outline of the real book: quiet corporate-satire, right people.
_FAITHFUL_OUTLINE = [
    {
        "title": "Act I: The Onboarding",
        "summary": "Milo Voss, newly dead, is processed into the Undeath Pipeline at Aeon Holdings.",
        "children": [
            {
                "title": "Chapter 1: Exit Interview",
                "summary": "Milo Voss meets his afterlife caseworker.",
            },
            {
                "title": "Chapter 2: The Cubicle",
                "summary": "Zara Okafor shows Milo the ropes of undead labor.",
            },
        ],
    },
    {
        "title": "Act II: The CEL",
        "summary": "Milo and Zara realize the Chief Executive Lich, Edric Thane, runs Aeon Holdings.",
        "children": [
            {
                "title": "Chapter 3: Quarterly Review",
                "summary": "Edric Thane addresses the workforce of the Marrow District.",
            },
            {
                "title": "Chapter 4: The Pipeline",
                "summary": "Zara Okafor uncovers what the Undeath Pipeline really does.",
            },
        ],
    },
    {
        "title": "Act III: Resignation",
        "summary": "Milo Voss and Zara Okafor confront the CEL and unionize the dead against Edric Thane.",
        "children": [
            {
                "title": "Chapter 5: Notice",
                "summary": "Milo hands the Chief Executive Lich his resignation.",
            },
        ],
    },
]


class TestFidelityElaraDetector:
    """The regression test for the actual incident, at zero API cost."""

    def test_elara_outline_scores_near_zero(self):
        fs = fidelity.grade_fidelity(_ELARA_OUTLINE, _PRINCIPALS, _KNOWN)
        assert fs.principal_recall == 0.0  # none of the three real leads appear
        assert fs.score == 0.0
        assert set(fs.missing) == {"Milo Voss", "Zara Okafor", "Edric Thane"}

    def test_faithful_outline_scores_high(self):
        fs = fidelity.grade_fidelity(_FAITHFUL_OUTLINE, _PRINCIPALS, _KNOWN)
        assert fs.principal_recall == 1.0
        assert fs.missing == []
        assert fs.score >= 0.8, fs.as_dict()

    def test_faithful_beats_elara_decisively(self):
        faithful = fidelity.grade_fidelity(_FAITHFUL_OUTLINE, _PRINCIPALS, _KNOWN).score
        elara = fidelity.grade_fidelity(_ELARA_OUTLINE, _PRINCIPALS, _KNOWN).score
        assert faithful - elara >= 0.8


def _text(nodes: list[dict]) -> str:
    titles, body = fidelity.flatten_outline(nodes)
    return "  ".join([*titles, *body])


class TestPrincipalRecall:
    def test_alias_counts_as_present(self):
        nodes = [{"title": "T", "summary": "Milo and Zara flee; Edric laughs."}]
        recall, _, missing = fidelity.principal_recall(_text(nodes), _PRINCIPALS)
        assert recall == 1.0 and missing == []

    def test_word_boundary_no_false_positive(self):
        # "Frederick" contains "edric"; "Zarahemla" contains "zara" — but not as
        # whole words, so neither counts.
        nodes = [
            {"title": "T", "summary": "Frederick walked with Milomir to Zarahemla."}
        ]
        recall, _, missing = fidelity.principal_recall(_text(nodes), _PRINCIPALS)
        assert recall == 0.0
        assert set(missing) == {"Milo Voss", "Zara Okafor", "Edric Thane"}

    def test_the_cel_read_as_lich_is_a_hit(self):
        nodes = [
            {"title": "The CEL", "summary": "The Chief Executive Lich signs the memo."}
        ]
        _, present, _ = fidelity.principal_recall(_text(nodes), _PRINCIPALS)
        assert "Edric Thane" in present


class TestUnknownRate:
    def test_invented_names_raise_the_rate(self):
        rate, unknown = fidelity.unknown_rate(
            "Milo Voss met Grognak in Xanadu.", {"Milo Voss", "Milo"}
        )
        assert rate > 0.0
        assert any("Grognak" in u or "Xanadu" in u for u in unknown)

    def test_all_known_is_zero(self):
        rate, unknown = fidelity.unknown_rate(
            "Milo Voss and Zara Okafor work at Aeon Holdings.",
            {"Milo Voss", "Zara Okafor", "Aeon Holdings"},
        )
        assert rate == 0.0 and unknown == []


class TestPlotGold:
    """Wave-5 gold graders (SPEC-45) on the archived outlines' plan lines."""

    @pytest.mark.parametrize("cast, n", [(["Milo Voss"], 1), (["Edric Thane"], 0)])
    def test_borrowed_phrase(self, cast, n):
        from evals.graders import plot

        owned = {"Edric Thane": ["Mr. Voss"]}
        text = 'Prava smiled. "Welcome, Mr. Voss. Sign here." Mr. Voss signed.'
        assert len(plot.borrowed_phrase(text, cast, owned)) == n

    def test_replayed_counts_answers_another_archive_gave(self):
        from evals.graders import plot

        def rec(prompt, answer, purpose="beat_prose"):
            msgs = [{"role": "user", "content": prompt}]
            return {"purpose": purpose, "messages": msgs, "answer": answer}

        run = [rec("a", "same"), rec("b", "new"), rec("a", "same"), rec("c", "x")]
        out = plot.replayed(run + [rec("d", "same", "plan_beats")], [rec("z", "same")])
        # The repeat of prompt "a" is this run's own determinism, not a replay.
        assert out["beat_prose"] == {"n": 3, "replayed": 1, "rate": 0.3333}
        assert out["plan_beats"]["rate"] == 1.0
        # (F) D12: an empty JSON value or a missing answer is not a replay.
        blank = [
            rec("e", "[]", "continuity"),
            {"purpose": "continuity", "messages": []},
        ]
        assert "continuity" not in plot.replayed(blank, [rec("z", "[]")])


class TestHistory:
    """W7-6 (a): the history table is reported, never gated (S-5)."""

    def test_the_checked_in_history_renders_with_the_act_rule(self):
        from evals.harness import report

        lines = report.history(report.HISTORY).splitlines()
        assert lines[0].split() == [
            *("run", "pair", "redo_ra", "untouched_share", "rewording_note"),
            *("r2_mean", "r2_min", "attribution"),
            *("tighten_ra_1", "tighten_ra_2", "tighten_ra_3"),  # W8-1, Could 4
        ]
        assert [ln.split()[0] for ln in lines[1:14]] == [
            *("W4b2", "W5", "W5b", "W5c", "W5bc", "W5d", "W5bd", "W6", "W6b"),
            *("W7", "W7b", "W7r", "W7br"),
        ]
        assert lines[-2] == report.ACT_RULE
        # (F) W8-2 (c): attribution pooled over W7, W7r, W8r and W9, with its
        # interval; tighten fires on both wave-7 pairs and 11, R2 on 9 and 10.
        assert lines[-3] == (
            "Attribution pooled from W7 (Wilson 95%): Milo 14/26 (0.35, 0.71); "
            "Zara 14/26 (0.35, 0.71); Edric 7/26 (0.14, 0.46)"
        )
        assert lines[-1].endswith("(7r), R2 mean (9), R2 mean (10), tighten RA (11)")

    def test_the_act_rule_fires_only_on_both_draws_of_a_pair(self, tmp_path):
        from evals.harness import report

        rows = [
            "run,pair,redo_ra,untouched_share,rewording_note,r2_mean,r2_min,attribution",
            "A,7,0.9,0.25,yes,0.95,0.9,",
            "Ab,7,0.9,0.40,yes,0.89,0.85,",
            "B,8,0.9,,no,0.95,0.9,",
            "Bb,8,0.9,0.9,no,0.85,0.8,",
        ]
        (path := tmp_path / "h.csv").write_text("\n".join(rows) + "\n")
        assert report.history(path).splitlines()[-1] == "Fires on: untouched_share (7)"

    def test_a_neutral_probe_row_stays_out_of_the_pool(self, tmp_path):
        from evals.harness import report  # (F) W16-3 N6

        rows = [
            "run,pair,redo_ra,untouched_share,rewording_note,r2_mean,r2_min,attribution"
        ]
        rows += [
            "W7,7,0.9,,no,0.95,0.9,Rowan 2/3",
            "N,8,0.9,,no,0.95,0.9,neutral: Rowan 1/3",
        ]
        (path := tmp_path / "h.csv").write_text("\n".join(rows) + "\n")
        assert "Rowan 2/3 " in report.history(path)


def _vec(text):  # a fake embedder: the same text, the same vector
    return [float(len(text)), 1.0]


def test_masking_hides_each_name_form_but_not_the_word_chief():
    forms = {"Milo Voss": ["Milo Voss", "Milo"], "Zara Okafor": ["Zara Okafor"]}
    forms["Edric Thane"] = ["Edric Thane", "Chief Executive Lich"]
    voss, zara = (
        attribution.mask(t, forms) for t in ("Mr. Voss, do sit.", "Zara, do sit.")
    )
    assert voss == zara == "⟨NAME⟩, do sit."  # (F) W9-2 (b), honorific included
    refs = {"Milo Voss": [_vec("x")], "Zara Okafor": [_vec("a longer line")]}
    # (F) a line differing only by its vocative classifies identically.
    assert attribution.attribute(
        "Milo Voss", _vec(voss), refs
    ) == attribution.attribute("Milo Voss", _vec(zara), refs)
    # (R) "chief" is no name, and a title alias is masked whole, never by token.
    line = "The chief signs. Chief Executive Lich waits. Chief Okafor?"
    assert (
        attribution.mask(line, forms) == "The chief signs. ⟨NAME⟩ waits. Chief ⟨NAME⟩?"
    )


def test_old_is_masked_only_within_old_fen():
    """(F) W10-5: "Old habits" is unmasked; (R) "Old Fen said" is masked."""
    forms = {"Old Fen": ["Old Fen"]}
    assert attribution.mask("Old habits. Fen?", forms) == "Old habits. ⟨NAME⟩?"
    assert attribution.mask("Old Fen said so.", forms) == "⟨NAME⟩ said so."


W10 = "Outline vs your source: "
CONFLICTS = [  # SPEC-73's located and unlocated forms, a plain warning, none
    f'{W10}Your source has Halvard Bell sent under. ("Bell files his report")',
    f'{W10}Bell is alive. (could not locate "Bell waves" in the outline)',
    "Outline not checked against your source",
]


def test_outline_source_conflicts_read_both_quote_forms():
    """(F) W10-1 (c): each prefixed warning, its detail and its outline quote."""
    assert plot.outline_source_conflicts(CONFLICTS) == [
        {"detail": "Your source has Halvard Bell sent under."}
        | {"quote": "Bell files his report", "located": True},
        {"detail": "Bell is alive.", "quote": "Bell waves", "located": False},
    ]


@pytest.mark.parametrize("warnings, named", [(CONFLICTS, True), (CONFLICTS[2:], False)])
def test_source_fact_hits_read_the_signed_cell_and_the_detail(warnings, named):
    """(F) W10-1 (c): the signed cell says Bell is contradicted; a conflict
    names the fact when its detail names Bell (model output, reported)."""
    gt = pipeline.load_corpus("cel")[1]
    signed = {"source_facts": {"bell_sent_under": {"contradicted": True, "line": "x"}}}
    hits = plot.source_fact_hits(gt, signed, warnings)
    assert hits == {"bell_sent_under": {"contradicted": True, "names_the_fact": named}}
    unsigned = plot.source_fact_hits(gt, None, [])["bell_sent_under"]
    assert unsigned["contradicted"] is None


# W10b's outline step warning (its report's app_warnings[1]), first sentence.
OLD_FEN = (
    f"{W10}In Chapter 1 of the source, Old Fen is already present while Milo works"
    " the Bell account in Reconciliations."
)


def test_names_the_fact_needs_a_full_name_or_alias():
    """(F) D7: W10b's Old Fen finding names "the Bell account": one word of a name
    no longer counts for Bell. (R) "Halvard Bell" in full still does."""
    gt = pipeline.load_corpus("cel")[1]
    hits = plot.source_fact_hits(gt, None, [OLD_FEN])["bell_sent_under"]
    assert hits["names_the_fact"] is False
    hits = plot.source_fact_hits(gt, None, [OLD_FEN, CONFLICTS[0]])["bell_sent_under"]
    assert hits["names_the_fact"] is True


class TestTracerExportProvenance:
    def _run(self):
        return {
            "book": "frankenstein",
            "app_sha": "abc1234",
            "harness_sha": "def5678",
            "model": "gemini-3.5-flash-lite",
            "run": None,
            "repeat": 1,
            "judge": {"provider": "groq", "model": "llama-x", "self": False},
            "steps": {
                "extraction": {
                    "score": 0.8,
                    "f1": 0.8,
                    "precision": 1.0,
                    "recall": 0.67,
                    "ok": True,
                },
                "retrieval": {
                    "score": 0.6,
                    "precision_at_k": 0.6,
                    "mrr": 0.75,
                    "note": "x",
                },
                "attribution": {"score": 0.4, "accuracy": 0.4, "status": "reported"},
                "prose": {"skipped": True, "reason": "quota"},
                "continuity": {"error": "boom"},
            },
        }

    def test_book_is_the_suite_and_metrics_ride_along(self):
        from evals.harness import report

        out = report.tracer_export(self._run(), model="gemini-3.5-flash-lite", env="ci")
        assert out["suite"] == "frankenstein" and out["env"] == "ci"
        by = {c["name"]: c for c in out["cases"]}
        # numeric fields only (bools and strings dropped) become case metrics
        assert by["eval:extraction"]["metrics"] == {
            "score": 0.8,
            "f1": 0.8,
            "precision": 1.0,
            "recall": 0.67,
        }
        assert by["eval:attribution"]["category"] == "reported"
        assert by["eval:retrieval"]["category"] == "gated"
        assert out["metrics"]["extraction.f1"] == 0.8
        assert out["metrics"]["retrieval.mrr"] == 0.75
        assert out["metrics"]["attribution.accuracy"] == 0.4

    def test_labels_name_judge_and_missing_steps(self):
        from evals.harness import report

        out = report.tracer_export(self._run(), model="m")
        assert out["labels"]["judge_provider"] == "groq"
        assert out["labels"]["judge_self_graded"] == "False"
        assert out["labels"]["harness_sha"] == "def5678"
        assert out["labels"]["skipped"] == "prose"
        assert out["labels"]["errored"] == "continuity"
        assert "declared_run" not in out["labels"]  # None values are omitted

    def test_provenance_from_github_env(self):
        from evals.harness import report

        prov = report.github_provenance(
            {
                "GITHUB_REPOSITORY": "RTrentJones/Polyphony",
                "GITHUB_RUN_ID": "7",
                "GITHUB_REF_NAME": "main",
                "GITHUB_EVENT_NAME": "workflow_dispatch",
            }
        )
        assert (
            prov["source_url"]
            == "https://github.com/RTrentJones/Polyphony/actions/runs/7"
        )
        out = report.tracer_export(self._run(), model="m", provenance=prov)
        assert out["repo"] == "RTrentJones/Polyphony" and out["branch"] == "main"
        assert out["labels"]["trigger"] == "workflow_dispatch"
        assert report.github_provenance({}) == {}

    def test_runs_are_labelled_on_demand(self, monkeypatch):
        # Dispatched, not scheduled: Tracer must not read a quiet week as stale.
        from evals.harness import report

        monkeypatch.delenv("EVAL_EXPERIMENT", raising=False)
        out = report.tracer_export(self._run(), model="m")
        assert out["labels"]["cadence"] == "on-demand"
        assert "experiment" not in out["labels"]

    def test_experiment_is_a_label_not_the_env(self, monkeypatch):
        from evals.harness import report

        monkeypatch.setenv("EVAL_EXPERIMENT", "spec-merge-" + "x" * 300)
        out = report.tracer_export(self._run(), model="m", env="ci")
        assert out["env"] == "ci"
        assert out["labels"]["experiment"].startswith("spec-merge-")
        assert len(out["labels"]["experiment"]) == 255

    def test_metric_keys_are_ascii(self):
        # Python's \w is Unicode by default; Tracer's metric keys are ASCII-only.
        from evals.harness import report

        out = report.tracer_export(
            {"steps": {"outline": {"score": 0.9, "fidélité": 0.8}}}, model="m"
        )
        assert out["cases"][0]["metrics"] == {"score": 0.9, "fid_lit_": 0.8}


class TestTracerIngestOutcome:
    """`ingest_to_tracer` never raises, and its outcome is always visible."""

    RESULT = {"book": "dracula", "model": "m", "steps": {"outline": {"score": 0.9}}}

    def _ingest(self, handler, monkeypatch, tmp_path, token="tok"):
        import asyncio

        import httpx

        from evals.run import ingest_to_tracer

        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        ok = asyncio.run(
            ingest_to_tracer(
                self.RESULT,
                "https://tracer.test/",
                token,
                "ci",
                transport=httpx.MockTransport(handler),
            )
        )
        return ok, summary.read_text() if summary.exists() else ""

    def test_created(self, monkeypatch, tmp_path, capsys):
        import httpx

        seen = {}

        def handler(req):
            seen["url"] = str(req.url)
            seen["auth"] = req.headers["authorization"]
            return httpx.Response(201, json={"id": "r1", "cases": 1})

        ok, summary = self._ingest(handler, monkeypatch, tmp_path)
        out = capsys.readouterr().out
        assert ok is True
        assert seen == {
            "url": "https://tracer.test/api/ingest",
            "auth": "Bearer tok",
        }
        assert "tracer ingest -> 201" in out and "::warning" not in out
        assert "`dracula`: ok" in summary

    def test_unauthorized_warns(self, monkeypatch, tmp_path, capsys):
        import httpx

        ok, summary = self._ingest(
            lambda req: httpx.Response(401, json={"error": "unauthorized"}),
            monkeypatch,
            tmp_path,
        )
        out = capsys.readouterr().out
        assert ok is False
        assert "tracer ingest -> 401" in out
        assert "::warning title=Tracer ingest::dracula: 401" in out
        assert "NOT ingested" in summary and "401" in summary

    def test_non_json_body_and_transport_error(self, monkeypatch, tmp_path, capsys):
        import httpx

        ok, _ = self._ingest(
            lambda req: httpx.Response(502, text="<html>bad gateway</html>"),
            monkeypatch,
            tmp_path,
        )
        assert ok is False and "502 <html>bad gateway</html>" in capsys.readouterr().out

        def boom(req):
            raise httpx.ConnectError("down")

        ok, _ = self._ingest(boom, monkeypatch, tmp_path)
        assert ok is False
        assert "failed: ConnectError: down" in capsys.readouterr().out

    def test_no_token(self, monkeypatch, tmp_path, capsys):
        def handler(req):  # pragma: no cover - must not be called
            raise AssertionError("posted without a token")

        ok, summary = self._ingest(handler, monkeypatch, tmp_path, token="")
        out = capsys.readouterr().out
        assert ok is False
        assert "skipped (TRACER_INGEST_TOKEN unset)" in out
        assert "::warning title=Tracer ingest::" in out
        assert "NOT ingested" in summary


class TestTracerExportCompleteness:
    def test_unscored_step_marks_the_run_incomplete(self):
        # A judge outage leaves score=None with no error/skip key; it must not
        # vanish and leave a run that reads complete and all-green.
        from evals.harness import report

        run = {
            "book": "dracula",
            "judge": {"provider": "groq", "partial": True},
            "steps": {
                "retrieval": {"score": 0.8, "precision_at_k": 0.8},
                "outline": {"score": None, "n_nodes": 12},
            },
        }
        out = report.tracer_export(run, model="m")
        assert [c["name"] for c in out["cases"]] == ["eval:retrieval"]
        assert out["labels"]["skipped"] == "outline"
        assert out["labels"]["unscored"] == "outline"
        assert out["labels"]["judge_partial"] == "True"
        assert "judge_unavailable" not in out["labels"]

    def test_repeated_step_publishes_only_its_averaged_score(self):
        # _aggregate averages `score` only; f1 here is the last draw's and must
        # not be charted as the repeated experiment's.
        from evals.harness import report

        run = {
            "book": "dracula",
            "steps": {
                "extraction": {"score": 0.6, "score_std": 0.4, "repeats": 2, "f1": 1.0},
                "retrieval": {"score": 0.8, "mrr": 0.9},
            },
        }
        out = report.tracer_export(run, model="m")
        by = {c["name"]: c for c in out["cases"]}
        assert by["eval:extraction"]["metrics"] == {
            "score": 0.6,
            "score_std": 0.4,
            "repeats": 2,
        }
        assert "extraction.f1" not in out["metrics"]
        assert out["metrics"]["extraction.score"] == 0.6
        assert (
            out["metrics"]["retrieval.mrr"] == 0.9
        )  # single-draw steps keep every metric
