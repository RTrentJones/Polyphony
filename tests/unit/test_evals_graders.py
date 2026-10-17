"""Unit tests for the eval graders — pure logic, no network/LLM, CI-gated.

These verify the *measurement* is correct on synthetic data, so a grader can't
silently report a wrong score. The graders' real inputs (API + embedder) are
exercised in the local end-to-end dry run, not here.
"""

import pytest

from evals.graders import attribution, continuity, extraction, fidelity, retrieval

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
        from evals.steps import pipeline  # noqa: F401 — registers steps
        from evals.steps.base import all_steps, get_step

        assert all_steps() == [
            "extraction",
            "ingestion",
            "retrieval",
            "outline",
            "continuity",
            "prose",
            "revise",
            "ensemble",
            "attribution",  # last (S-3)
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

    def test_grade_detects_and_scores_fpr(self):
        _, expected = continuity.apply_injections(
            "Aldous Kerr on _5 May._ then Aldous Kerr.", self.INJ
        )
        injected_findings = [
            {
                "detail": "Name spelled Aldous Karr here but Aldous Kerr elsewhere",
                "refs": "",
            },
            # date contradiction NOT flagged -> should be 'missed'
        ]
        control_findings = []  # clean text: no false positives
        score = continuity.grade_continuity(
            expected, injected_findings, control_findings
        )
        assert "name" in score.detected
        assert "date" in score.missed
        assert score.detection_recall == 0.5
        assert score.false_positive_rate == 0.0

    def test_false_positives_counted(self):
        _, expected = continuity.apply_injections(
            "Aldous Kerr _5 May._ Aldous Kerr", self.INJ
        )
        score = continuity.grade_continuity(
            expected, [], [{"detail": "spurious"}, {"detail": "spurious2"}]
        )
        assert score.false_positive_rate > 0


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
        # (F) W8-2 (c): attribution pooled over W7, W7r and W8r, with its
        # interval; the tighten act rule fires on both wave-7 pairs only.
        assert lines[-3] == (
            "Attribution pooled from W7 (Wilson 95%): Milo 9/16 (0.33, 0.77); "
            "Zara 9/16 (0.33, 0.77); Edric 6/16 (0.18, 0.61)"
        )
        assert lines[-1].endswith("tighten RA (7), tighten RA (7r)")

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
