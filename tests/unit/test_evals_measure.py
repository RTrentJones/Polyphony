"""wave 8: evals/measure (tpm-plan-wave8 §3 E1). Measurements call graders and
product functions and restate neither."""

import pytest

from evals.measure.wilson import wilson

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "k, n, interval",
    # The principal's k = 10 table (principal-review-wave8-plan, "What I checked").
    [(3, 10, (0.11, 0.6)), (5, 10, (0.24, 0.76)), (7, 10, (0.4, 0.89))]
    + [(9, 10, (0.6, 0.98)), (0, 10, (0.0, 0.28)), (10, 10, (0.72, 1.0))],
)
def test_wilson_reproduces_the_principals_intervals(k, n, interval):
    assert wilson(k, n) == interval


def test_wilson_of_no_draws_is_none():
    assert wilson(0, 0) is None


def test_voice_overlap_no_refill_bound_on_two_blocks():
    """(F) D7: two principal blocks, one voiced by a sample that restates its
    beat line. Removing every slot at or above a threshold (no refill) drops
    coverage from 1.0 to 0.5 there, and not below it."""
    from evals.graders import prompt_contract as pc
    from evals.measure.voice_overlap import overlap

    def beat(owner, sample, line):
        prompt = (
            f"CHARACTERS IN THIS BEAT\n### {owner}\n"
            f'Voice samples (their own words):\n- "{sample}"\n'
            f"BEAT TO WRITE: {line}"
        )
        return {"id": owner, "purpose": "beat_prose", "created": 1.0}, prompt

    recs = []
    for owner, sample, line in (
        (
            "Milo Voss",
            "Dashboards and throughput trackers, mostly.",
            "Milo asks about dashboards and throughput trackers.",
        ),  # noqa: E501
        ("Zara Okafor", "Read the appendices first.", "Zara files a ledger."),
    ):
        rec, prompt = beat(owner, sample, line)
        recs.append(rec | {"messages": [{"role": "user", "content": prompt}]})
    aliases = {"milo voss": "Milo Voss", "zara okafor": "Zara Okafor"}
    out = overlap(recs, aliases)
    assert out["coverage"] == 1.0 and out["shares"] == [0.0, 0.75]
    assert out["no_refill_bound"][0.5] == 0.5 and out["at_or_above"][0.5] == 1
    assert pc.voice_coverage(recs, aliases) == 1.0  # the grader's own function
