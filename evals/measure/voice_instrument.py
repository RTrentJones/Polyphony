"""D5 (W10-2 (a)): leave-one-out over the 22 cel gold lines, names masked, by
the unchanged `attribution.attribute`. References are TRAIN lines only: a test
line is scored against every train line, a train line against the others.
Wave 11, D4 (b), the controls (SPEC-74): positive, the W9a2 answers against
every train line; negative, the leave-one-out over SHUFFLES seeded label
shuffles; the self-split, reported. Validated when the positive Wilson lower
bound is > 0.33 and the true score beats 95% of the shuffles.
`python -m evals.measure.voice_instrument`
"""

import asyncio
import json
import random
from pathlib import Path

from evals.graders import attribution
from evals.measure.wilson import wilson

ANSWERS = Path(__file__).parents[1] / "corpora" / "cel" / "w9a2_answers.json"
SHUFFLES, SEED = 200, 11


def references(gold: dict, held: tuple[str, int] | None) -> dict[str, list[str]]:
    """Each speaker's train lines, less the held-out train line `held`."""
    return {
        w: [t for i, t in enumerate(v["train"]) if (w, i) != held]
        for w, v in gold.items()
    }


def relabel(gold: dict, rng: random.Random) -> dict:
    """The gold with its speaker labels shuffled over every line; each line
    keeps its train/test side and each speaker its number of lines."""
    rows = [(s, t) for v in gold.values() for s in ("train", "test") for t in v[s]]
    who = [w for w, v in gold.items() for s in ("train", "test") for _ in v[s]]
    rng.shuffle(who)
    out = {w: {"train": [], "test": []} for w in gold}
    for w, (s, t) in zip(who, rows):
        out[w][s].append(t)
    return out


def halves(lines: list[str]) -> dict:
    """The self-split: one speaker's lines, alternately, as two train-only speakers."""
    return {h: {"train": lines[i::2], "test": []} for i, h in enumerate("ab")}


def loo(gold: dict, vec: dict) -> list:
    """Every gold line attributed against `references(gold, held)`."""
    lines = [(w, t, (w, i)) for w, v in gold.items() for i, t in enumerate(v["train"])]
    lines += [(w, t, None) for w, v in gold.items() for t in v["test"]]
    return [attribute(gold, vec, *x) for x in lines]


def attribute(gold, vec, who, line, held):
    refs = {w: [vec[t] for t in ts] for w, ts in references(gold, held).items()}
    return attribution.attribute(who, vec[line], refs)


async def score(gold: dict, forms: dict, embed_many, answers: list) -> dict:
    probe = [(a["intended"], a["answer"].strip().strip("\"'"), None) for a in answers]
    lines = (t for v in gold.values() for s in v.values() for t in s)
    texts = sorted({*lines, *(t for _, t, _ in probe)})
    vecs = await embed_many([attribution.mask(t, forms) for t in texts])
    vec = dict(zip(texts, vecs))

    true = loo(gold, vec)
    per = {w: [r.correct for r in true if r.intended == w] for w in gold}
    k = sum(r.correct for r in true)
    rng = random.Random(SEED)
    null = [
        sum(r.correct for r in loo(relabel(gold, rng), vec)) for _ in range(SHUFFLES)
    ]
    positive = attribution.accuracy([attribute(gold, vec, *x) for x in probe])
    positive["wilson95"] = wilson(positive["correct"], positive["n"])
    split = {w: loo(halves([*v["train"], *v["test"]]), vec) for w, v in gold.items()}
    beats = sum(n < k for n in null) / SHUFFLES
    return {
        "correct": k,
        "n": len(true),
        "wilson95": wilson(k, len(true)),
        "per_principal": {w: f"{sum(c)}/{len(c)}" for w, c in per.items()},
        "instrument": {
            "positive": positive,
            "permutation_p": round((1 + sum(n >= k for n in null)) / (1 + SHUFFLES), 4),
            "beats_shuffles": beats,
            "self_split": {
                w: f"{sum(r.correct for r in rs)}/{len(rs)}" for w, rs in split.items()
            },
            "validated": (positive["wilson95"] or (0, 0))[0] > 0.33 and beats >= 0.95,
        },
    }


if __name__ == "__main__":
    from evals.harness import embed
    from evals.steps.pipeline import _forms, load_corpus

    gt = load_corpus("cel")[1]
    answers = json.loads(ANSWERS.read_text())["answers"]
    forms = {n: [n] for n in gt["cast"]} | _forms(gt)  # step_attribution's
    result = score(gt["gold_lines"], forms, embed.embed_many, answers)
    print(json.dumps(asyncio.run(result), indent=2))
