"""D5 (W10-2 (a)): leave-one-out over the 22 cel gold lines, names masked, by
the unchanged `attribution.attribute`. References are TRAIN lines only: a test
line is scored against every train line, a train line against the others.
Validated at >= 18/22 with each principal >= 0.5; the W9a2 answers are scored
against every train line, reported.  `python -m evals.measure.voice_instrument`
"""

import asyncio
import json
from pathlib import Path

from evals.graders import attribution
from evals.measure.wilson import wilson

ANSWERS = Path(__file__).parents[1] / "corpora" / "cel" / "w9a2_answers.json"


def references(gold: dict, held: tuple[str, int] | None) -> dict[str, list[str]]:
    """Each speaker's train lines, less the held-out train line `held`."""
    return {
        w: [t for i, t in enumerate(v["train"]) if (w, i) != held]
        for w, v in gold.items()
    }


async def score(gold: dict, forms: dict, embed_many, answers: list) -> dict:
    lines = [(w, t, (w, i)) for w, v in gold.items() for i, t in enumerate(v["train"])]
    lines += [(w, t, None) for w, v in gold.items() for t in v["test"]]
    probe = [(a["intended"], a["answer"].strip().strip("\"'"), None) for a in answers]
    texts = sorted({t for _, t, _ in lines + probe})
    vecs = await embed_many([attribution.mask(t, forms) for t in texts])
    vec = dict(zip(texts, vecs))

    def attribute(who, line, held):
        refs = {w: [vec[t] for t in ts] for w, ts in references(gold, held).items()}
        return attribution.attribute(who, vec[line], refs)

    loo = [attribute(*x) for x in lines]
    per = {w: [r.correct for r in loo if r.intended == w] for w in gold}
    k = sum(r.correct for r in loo)
    return {
        "correct": k,
        "n": len(loo),
        "wilson95": wilson(k, len(loo)),
        "per_principal": {w: f"{sum(c)}/{len(c)}" for w, c in per.items()},
        "validated": k >= 18 and all(sum(c) >= 0.5 * len(c) for c in per.values()),
        "w9a2_answers": attribution.accuracy([attribute(*x) for x in probe]),
    }


if __name__ == "__main__":
    from evals.harness import embed
    from evals.steps.pipeline import _forms, load_corpus

    gt = load_corpus("cel")[1]
    answers = json.loads(ANSWERS.read_text())["answers"]
    forms = {n: [n] for n in gt["cast"]} | _forms(gt)  # step_attribution's
    result = score(gt["gold_lines"], forms, embed.embed_many, answers)
    print(json.dumps(asyncio.run(result), indent=2))
