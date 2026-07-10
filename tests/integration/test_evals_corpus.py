"""Corpus-quality guard: the renamed voices must actually separate.

Runs the app's real fastembed model over the committed Dracula gold lines and
asserts held-out lines attribute to their own character well above chance. This
protects the corpus itself — if a future edit homogenizes the gold voices, the
voice/attribution evals would silently go meaningless; this fails first.

Gated by RUN_EMBED_TESTS (needs the fastembed model, a few seconds).
"""

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("RUN_EMBED_TESTS"),
        reason="embedding corpus test only runs when RUN_EMBED_TESTS is set",
    ),
]


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
    # comfortably above the 1/N chance baseline (0.33 for three voices). Both the
    # training corpus (dracula) and the held-out corpus (frankenstein) must hold
    # separable gold voices or the voice/attribution evals go meaningless.
    assert agg["accuracy"] >= 0.6, f"{book} voices not separable enough: {agg}"
    assert agg["chance"] < agg["accuracy"]
