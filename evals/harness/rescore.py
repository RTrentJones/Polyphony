"""Re-score a finished run's prose step with the current deterministic checks.

A cap change (SPEC-28's H1: the R1 cap fires only when a planned principal is
neither named nor speaking) changes what an old report means. This re-grades
an existing run without generating or judging anything, from what the run left
behind:

* the eval report (``eval-report-<book>.json``): the scenes, their cast and the
  judge's per-rubric scores (a capped rubric's raw score is lost there);
* the judge relay archive (``judge/answered``): each scene rubric prompt
  carries the scene text ("SCENE TO SCORE:") and its answer the raw score;
* the model relay archive (``model/answered``): each ``beat_prose`` exchange is
  one inner beat, with its cast (the CHARACTERS IN THIS BEAT headings), its
  planned description (the marked ``<beats>`` item) and its prose (the answer).

It also re-grades the model archive with the current prompt-contract grader.

    python -m evals.harness.rescore REPORT --relay MODEL_DIR --judge JUDGE_DIR \\
        [--book cel] [--out rescored.json]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from evals.graders import prompt_contract as pc
from evals.graders import rubrics

_RUBRIC_ID = re.compile(r"### (R\d)\.")
_SCENE_MARK = "SCENE TO SCORE:\n"
_MARKED_BEAT = re.compile(r"(?m)^\s*→\s*(\d+)[.)]\s*(.+)$")


def _answer_score(answer: str) -> float | None:
    obj = pc._json_object(answer)
    try:
        return float(obj["score"])
    except (KeyError, TypeError, ValueError):
        return None


def judged_scenes(judge_records: list[dict]) -> list[dict]:
    """[{content, scores: {rid: raw score}}] in the order the judge saw them
    (one entry per scene; R2's prompt has no scene text and joins the scene
    judged just before it)."""
    out: list[dict] = []
    for rec in judge_records:
        text = pc.prompt_text(rec)
        m = _RUBRIC_ID.search(text)
        if not m:
            continue
        rid = m.group(1)
        at = text.find(_SCENE_MARK)
        if at != -1:
            content = text[at + len(_SCENE_MARK) :].strip()
            if not out or out[-1]["content"] != content:
                out.append({"content": content, "scores": {}})
        elif rid != "R2" or not out:
            continue  # an outline rubric
        out[-1]["scores"][rid] = _answer_score(rec.get("answer") or "")
    return out


def _beat_cast(prompt: str) -> list[str]:
    section = pc.characters_section(prompt) or ""
    return [
        line.strip()[4:].strip()
        for line in section.splitlines()
        if line.strip().startswith("### ")
    ]


def archived_scenes(model_records: list[dict]) -> list[dict]:
    """The run's scenes rebuilt from its beat_prose exchanges: a scene starts
    at each beat 1. [{description, beats: [{beat_number, description,
    characters, content}]}]."""
    scenes: list[dict] = []
    for rec in model_records:
        if pc.purpose(rec) != "beat_prose":
            continue
        prompt = pc.prompt_text(rec)
        m = _MARKED_BEAT.search(pc.block(prompt, "beats") or "")
        n = int(m.group(1)) if m else 1
        if n == 1 or not scenes:
            desc = re.search(r"(?m)^Scene description: (.+)$", prompt)
            scenes.append({"description": desc.group(1) if desc else "", "beats": []})
        scenes[-1]["beats"].append(
            {
                "beat_number": n - 1,
                "description": m.group(2).strip() if m else "",
                "characters": _beat_cast(prompt),
                "content": (rec.get("answer") or "").strip(),
            }
        )
    return scenes


def rescore_prose(
    report: dict, model_records: list[dict], judge_records: list[dict]
) -> dict:
    """The prose step re-scored with the current `_grade_scene_checks`."""
    from evals.steps import pipeline

    gt = pipeline.load_corpus(report["book"])[1]
    forms = pipeline._forms(gt)
    prose = report["steps"]["prose"]
    rows = prose.get("scenes") or []
    judged = judged_scenes(judge_records)
    built = archived_scenes(model_records)
    out_rows, prev_text = [], ""
    for i, row in enumerate(rows):
        content = judged[i]["content"] if i < len(judged) else ""
        arch = built[i] if i < len(built) else {"description": "", "beats": []}
        scene = {
            "content": content,
            "characters": row.get("cast") or [],
            "description": arch["description"],
            "beats": arch["beats"],
        }
        checks = pipeline._grade_scene_checks(scene, prev_text, forms)
        raw = {
            rid: (judged[i]["scores"].get(rid) if i < len(judged) else None)
            for rid in rubrics.SCENE_RUBRICS
        }
        for rid, j in (row.get("rubrics") or {}).items():
            if raw.get(rid) is None and not j.get("capped"):
                raw[rid] = j.get("score")
        capped = rubrics.apply_caps(raw, checks["caps"])
        out_rows.append(
            {
                "scene_id": row.get("scene_id"),
                "old_caps": row.get("deterministic_failures") or {},
                "old_score": row.get("score"),
                "caps": checks["caps"],
                "warnings": checks["warnings"],
                "speaking": checks["speaking"],
                "cast_present": checks["cast_present"],
                "judge_raw": raw,
                "rubrics": capped,
                "score": rubrics.scene_score(capped),
            }
        )
        prev_text = content
    scored = [r["score"] for r in out_rows if r["score"] is not None]
    means = {}
    for rid in rubrics.SCENE_RUBRICS:
        vals = [
            r["rubrics"][rid] for r in out_rows if r["rubrics"].get(rid) is not None
        ]
        means[rid] = round(sum(vals) / len(vals), 4) if vals else None
    return {
        "old_score": prose.get("score"),
        "old_rubric_means": prose.get("rubric_means"),
        "score": round(sum(scored) / len(scored), 4) if scored else None,
        "rubric_means": means,
        "r1_capped_scenes": sum(1 for r in out_rows if "R1" in r["caps"]),
        "scenes": out_rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("report")
    ap.add_argument("--relay", required=True, help="the model relay's answered/")
    ap.add_argument("--judge", required=True, help="the judge relay's answered/")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    from evals.steps import pipeline

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    model = pc.load_archive(args.relay)
    judge = pc.load_archive(args.judge)
    gt = pipeline.load_corpus(report["book"])[1]
    result = {
        "report": args.report,
        "prose": rescore_prose(report, model, judge),
        "prompt_contract": pc.grade(
            model, **pc.corpus_inputs(gt, pipeline.load_style(report["book"]))
        ),
    }
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
