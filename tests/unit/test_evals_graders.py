"""Unit tests for the eval graders — pure logic, no network/LLM, CI-gated.

These verify the *measurement* is correct on synthetic data, so a grader can't
silently report a wrong score. The graders' real inputs (API + embedder) are
exercised in the local end-to-end dry run, not here.
"""

import pytest

from evals.graders import attribution, continuity, extraction, retrieval

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
            "retrieval",
            "attribution",
            "outline",
            "continuity",
            "prose",
        ]
        # embedding-only steps must not require the API (so they run keyless)
        assert get_step("retrieval").needs_api is False
        assert get_step("extraction").needs_api is True

    def test_unknown_step_raises(self):
        from evals.steps.base import get_step

        with pytest.raises(KeyError):
            get_step("nope")


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

    def test_empty_prediction(self):
        s = extraction.grade_extraction([], ["Nora Vance"])
        assert s.precision == 0.0 and s.recall == 0.0 and s.f1 == 0.0


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
