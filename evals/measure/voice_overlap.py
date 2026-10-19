"""D7 (W8-3 (c), B-3): the stemmed content share of every shown voice slot
against its beat line, and the no-refill lower bound on `voice_coverage` per
threshold: every slot at or above it is removed, nothing refills, and coverage
is recomputed by the grader's own function. No ranking claim, no replay.

    python -m evals.measure.voice_overlap [--book cel] /tmp/claude-0/eval-out/w7 ...
"""

import sys
from pathlib import Path

from evals.graders import prompt_contract as pc

THRESHOLDS = (0.2, 0.25, 0.29, 0.33, 0.5)


def overlap(records: list[dict], aliases: dict) -> dict:
    shares = sorted(s["content_share"] for s in pc.grade(records)["scripted_slots"])

    def bound(t: float) -> float | None:
        return pc.voice_coverage(
            records, aliases, lambda s, ref: pc.content_share(s, ref) >= t
        )

    return {
        "slots": len(shares),
        "shares": shares,
        "at_or_above": {t: sum(x >= t for x in shares) for t in THRESHOLDS},
        "coverage": pc.voice_coverage(records, aliases),
        "no_refill_bound": {t: bound(t) for t in THRESHOLDS},
    }


def main(runs: list[str], book: str = "cel") -> None:
    principals = pc.book_inputs(book)["principals"]
    aliases = {
        f.lower(): p["name"]
        for p in principals
        for f in [p["name"], *p.get("aliases", [])]
    }  # noqa: E501
    print(
        "run | slots | >=0.25 | >=0.29 | coverage | bound "
        + " ".join(map(str, THRESHOLDS))
    )  # noqa: E501
    for run in runs:
        records = pc.load_archive(Path(run) / "answered-archive" / "model" / "answered")
        o = overlap(records, aliases)
        bound = " ".join(str(o["no_refill_bound"][t]) for t in THRESHOLDS)
        print(
            f"{Path(run).name} | {o['slots']} | {o['at_or_above'][0.25]} | "
            f"{o['at_or_above'][0.29]} | {o['coverage']} | {bound}"
        )


if __name__ == "__main__":
    args = sys.argv[1:]  # the archives' corpus decides the aliases (W13-SL (ii))
    book = args.pop(args.index("--book") + 1) if "--book" in args else "cel"
    main([a for a in args if a != "--book"], book)
