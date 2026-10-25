"""Corpus-quality guard: the renamed voices must actually separate.

Runs the app's real fastembed model over the committed Dracula gold lines and
asserts held-out lines attribute to their own character well above chance. This
protects the corpus itself — if a future edit homogenizes the gold voices, the
voice/attribution evals would silently go meaningless; this fails first.

Gated by RUN_EMBED_TESTS (needs the fastembed model, a few seconds).
"""

import os

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.getenv("RUN_EMBED_TESTS"),
    reason="embedding corpus test only runs when RUN_EMBED_TESTS is set",
)
@pytest.mark.parametrize("book", ["dracula", "frankenstein"])
async def test_corpus_voices_separate(book):
    from evals.graders import attribution
    from evals.harness import embed
    from evals.steps import pipeline

    _, gt = pipeline.load_corpus(book)
    gold = gt["gold_lines"]
    assert len(gold) >= 3, "need >=3 voices for a meaningful attribution baseline"

    refs = {c: await embed.embed_many(v["train"]) for c, v in gold.items()}
    results = [
        attribution.attribute(c, await embed.embed_one(ln), refs)
        for c, v in gold.items()
        for ln in v["test"]
    ]
    agg = attribution.accuracy(results)
    # comfortably above the 1/N chance baseline (0.33 for three voices). Both
    # development corpora (dracula and frankenstein) must hold
    # separable gold voices or the voice/attribution evals go meaningless.
    assert agg["accuracy"] >= 0.6, f"{book} voices not separable enough: {agg}"
    assert agg["chance"] < agg["accuracy"]


def test_gold_lines_are_whole_utterances():
    """M4 (SPEC-39): a quote broken by its speech tag is one line, joined as
    the extractor joins it, and never split between `train` and `test`."""
    from evals.steps import pipeline

    for book in ("cel", "dracula", "frankenstein"):
        for who, v in pipeline.load_corpus(book)[1]["gold_lines"].items():
            for line in v["train"] + v["test"]:
                assert not line.rstrip().endswith(","), (book, who, line)
    zara = pipeline.load_corpus("cel")[1]["gold_lines"]["Zara Okafor"]
    joined = "Close the accounts in your queue. Don't open the ones that aren't."
    assert joined in zara["train"]
    assert "Don't open the ones that aren't." not in zara["train"] + zara["test"]


def test_excerpt_events_cover_the_excerpt_in_order():
    """SPEC-45 gold: the event spans start at paragraph 0 and run in order to
    the last paragraph, each narrated paragraph lies in its own span, and
    every owned phrase is in the excerpt."""
    from evals.steps import pipeline

    _, gt = pipeline.load_corpus("cel")
    excerpt = pipeline.load_excerpts("cel")
    events = gt["excerpt_events"]
    starts = [e["from"] for e in events] + [len(excerpt.split("\n\n"))]
    assert starts[0] == 0 and starts == sorted(set(starts)) and starts[-1] == 31
    for e, end in zip(events, starts[1:]):
        assert all(e["from"] <= i < end for i in e["narrated"]), e
    assert all(p in excerpt for ps in gt["owned_phrases"].values() for p in ps)
