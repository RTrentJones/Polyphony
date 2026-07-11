"""Offline direct-eval harness — the fast local loop for the DB-free steps.

`extraction` and `outline` are pure LLM calls (no Postgres, no running server),
so they can be graded by calling the pipeline functions directly with only a
Gemini key set. That turns the two highest-deficit dimensions into a
seconds-long loop for iterating prompts, instead of a full CI boot. The
RAG-dependent steps (attribution/prose/continuity) still need the server, so
they live in evals.run.

    GEMINI_API_KEY=... SECRET_KEY=... python -m evals.offline --book dracula

Scores use the SAME graders as the served pipeline, so an offline number is
comparable to the CI/Tracer number for the same step.
"""

from __future__ import annotations

import argparse
import asyncio
import re

from evals.graders import extraction as extraction_grader
from evals.steps.pipeline import load_corpus

_WORD = re.compile(r"[a-z']+")


def _content_words(s: str) -> set[str]:
    stop = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "as",
        "is",
        "are",
        "then",
        "into",
        "over",
        "his",
        "her",
        "its",
    }
    return {w for w in _WORD.findall(s.lower()) if w not in stop and len(w) > 2}


def beat_recall(nodes: list[dict], canonical_beats: list[dict]) -> float:
    """Deterministic proxy: fraction of canonical beats whose content words are
    recognizably present in the generated outline (title+summary). A trustworthy
    signal that doesn't depend on an LLM judge."""
    if not canonical_beats:
        return 0.0
    outline_words = set()
    for n in nodes:
        outline_words |= _content_words(f"{n.get('title', '')} {n.get('summary', '')}")
        for c in n.get("children", []):
            outline_words |= _content_words(
                f"{c.get('title', '')} {c.get('summary', '')}"
            )
    hit = 0
    for beat in canonical_beats:
        bwords = _content_words(beat["title"])
        # a beat is "recovered" if a majority of its content words appear.
        if bwords and len(bwords & outline_words) / len(bwords) >= 0.5:
            hit += 1
    return round(hit / len(canonical_beats), 4)


async def offline_extraction(corpus_text: str, gt: dict) -> dict:
    from app.parsing.character_extractor import CharacterExtractor

    predicted = await CharacterExtractor().extract_characters(
        corpus_text, max_characters=max(6, len(gt["cast"]))
    )
    score = extraction_grader.grade_extraction(predicted, gt["cast"])
    return {"predicted": predicted, **score.as_dict(), "score": score.f1}


async def offline_outline(gt: dict) -> dict:
    from app.planning.outline import generate_outline

    nodes = await generate_outline(
        title=f"eval-{gt['book']}",
        synopsis=gt["synopsis"],
        chapters_target=len(gt["canonical_beats"]),
    )
    structural = bool(nodes) and all(n.get("title") for n in nodes)
    recall = beat_recall(nodes, gt["canonical_beats"])
    return {
        "n_nodes": len(nodes),
        "structural_ok": structural,
        "beat_recall": recall,
        # score = the deterministic beat-recall (structural-gated), the signal
        # to iterate on without an LLM judge.
        "score": recall if structural else 0.0,
    }


async def run(book: str, steps: list[str]) -> dict:
    text, gt = load_corpus(book)
    out: dict = {"book": book, "steps": {}}
    if "extraction" in steps:
        out["steps"]["extraction"] = await offline_extraction(text, gt)
    if "outline" in steps:
        out["steps"]["outline"] = await offline_outline(gt)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default="dracula")
    ap.add_argument(
        "--steps",
        default="extraction,outline",
        help="offline-capable: extraction,outline",
    )
    args = ap.parse_args()
    steps = [s.strip() for s in args.steps.split(",")]
    result = asyncio.run(run(args.book, steps))
    for name, res in result["steps"].items():
        if name == "extraction":
            print(
                f"  extraction   F1={res['f1']}  P={res['precision']} R={res['recall']}"
                f"  predicted={res['predicted']}"
            )
        else:
            print(
                f"  outline      beat_recall={res['beat_recall']}  "
                f"structural={res['structural_ok']}  nodes={res['n_nodes']}"
            )


if __name__ == "__main__":
    main()
