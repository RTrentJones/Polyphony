"""G-S0 (SPEC-77): the movement lines the speech-beat rule releases, on the
product's own `_withheld_by`, over the signed cel archives with a store.

A line is released in a scene when today's rule (every plan line counted as
the scene's own) withholds it `restates` and the product's rule shows it. Each
release is a gain (the signed gold calls it manner) or a leak (`plot_reasons`).
Upper bound: every store line of each shown owner, no retrieval threshold or k.

    python -m evals.measure.manner_sweep [archive ...]
"""

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

from app.characters.context import (
    SceneScope,
    _withheld_by,
    later_plot_from,
    speech_paragraphs,
)
from app.core.prompt_markers import STORY_POSITION_RE
from app.parsing.character_extractor import source_paragraphs
from app.planning.canon import Canon
from evals.graders import plot
from evals.graders import prompt_contract as pc
from evals.steps.pipeline import load_excerpts

EVAL_OUT = Path("/tmp/claude-0/eval-out")
# The 17 signed archives whose report carries a movement store (G-S0).
ARCHIVES = (
    "w5bc w5bd w5c w5d w6 w6b w7 w7b w7br w7r w8b w8c w8r w9 w9b w10 w10b".split()
)
SOURCE_ID = "source"


def scenes_of(records: list[dict]) -> list[dict]:
    """The beat_prose prompts grouped by scene, in order: brief, beat lines,
    chapter, the owners each prompt shows, the corpus's canon text."""
    out: dict[str, dict] = {}
    for rec in records:
        if pc.purpose(rec) != "beat_prose":
            continue
        text = pc.prompt_text(rec)
        scene = pc.block(text, "scene") or ""
        brief = re.search(r"(?m)^Scene description:\s*(.*)$", scene)
        beats = (pc.block(text, pc.BEATS_FENCE) or "").splitlines()
        s = out.setdefault(
            scene,
            {
                "brief": brief.group(1).strip() if brief else "",
                "beats": [re.sub(r"^\W*\d+[.)]\s*", "", b) for b in beats if b.strip()],
                "chapter": int(STORY_POSITION_RE.search(text).group("n")),
                "canon": "\n".join(
                    filter(None, (pc.block(text, t) for t in ("synopsis", "canon")))
                ),
                "blocks": [],
            },
        )
        owners = [o for o, _ in pc.movement_blocks(pc.characters_section(text) or "")]
        s["blocks"].append({"request": rec.get("id"), "owners": owners})
    return list(out.values())


def releases(scenes, store, source, cast, plan) -> list[dict]:
    """Every (scene, owner, line) today's rule withholds `restates` and the
    product's rule shows, with the scope each was judged in."""
    people = {c["name"]: SimpleNamespace(**c) for c in cast}
    speech = {SOURCE_ID: speech_paragraphs(source)}
    paragraphs = source_paragraphs(source)
    book = [(n, {"title": "", "summary": ln}) for n, ln in plan]
    out, earlier = [], {}
    for s in scenes:
        cut = later_plot_from(paragraphs, s["chapter"], book)
        names = {o for b in s["blocks"] for o in b["owners"]}
        canon = Canon(title="", synopsis=s["canon"], characters=list(people.values()))
        lines = [s["brief"], *(ln for _, ln in plan), *earlier.get(s["chapter"], [])]
        scope = SceneScope.of(
            canon,
            [people[n] for n in sorted(names & people.keys())],
            sorted(names - people.keys()),
            dict.fromkeys([lines[0], *filter(None, lines[1:])]),
            {SOURCE_ID: cut} if cut is not None else {},
            [s["brief"]],
            speech,
        ).planned(s["beats"])
        today = scope._replace(own_plot=scope.plot)
        for owner in sorted(names & people.keys() & store.keys()):
            for text in store[owner]:
                at = plot.paragraph_of(text, paragraphs)
                row = {
                    "text": text,
                    "source": f"paragraph_{at}",
                    "source_id": SOURCE_ID,
                }
                if at is None:
                    row["source"] = ""
                before = _withheld_by(row, people[owner], today)
                after = _withheld_by(row, people[owner], scope)
                if before != after:
                    out.append(
                        {
                            "scene": s["brief"][:40],
                            "owner": owner,
                            "line": text,
                            "paragraph": at,
                            "chapter": s["chapter"],
                            "before": before,
                            "after": after,
                        }
                    )
        earlier.setdefault(s["chapter"], []).extend(s["beats"])
    return out


def sweep(name: str, gt: dict, cast: list[dict]) -> dict | None:
    """One archive's releases, each a gold-manner gain or a gold-plot leak."""
    base = EVAL_OUT / name
    report = next(base.glob("eval-report-*.json"), None)
    voice = json.loads(report.read_text())["steps"]["prose"]["voice"] if report else {}
    alias = {f.lower(): c["name"] for c in cast for f in [c["name"], *c["aliases"]]}
    store = {
        alias.get(o.lower(), o): v for o, v in (voice.get("movement") or {}).items()
    }
    records = pc.load_archive(base / "answered-archive/model/answered")
    signed = plot.placements(records, gt["excerpt_events"], plot.PLACEMENTS)
    if not store or not signed:
        return None
    source = load_excerpts("cel")
    gold = {"events": gt["excerpt_events"], "paragraphs": source.split("\n\n")}
    placed = plot.placed_chapters(signed)
    chapters = plot.outline_chapters(records)
    plan = [(n, ln) for n, ch in enumerate(chapters, 1) for ln in ch]
    found = releases(scenes_of(records), store, source.rstrip("\n"), cast, plan)
    for r in found:
        why, _, _ = plot.plot_reasons("movement", r["line"], r["chapter"], gold, placed)
        r["leak"] = why
    return {
        "archive": name,
        "released": found,
        "gains": sum(not r["leak"] for r in found),
        "leaks": sum(bool(r["leak"]) for r in found),
    }


def main(argv: list[str] | None = None) -> None:
    gt = json.loads((Path("evals/corpora/cel/ground_truth.json")).read_text())
    cast = [
        {"name": c["name"], "aliases": c.get("aliases") or []} for c in gt["characters"]
    ]
    total = {"released": 0, "gains": 0, "leaks": 0}
    for name in (argv if argv is not None else sys.argv[1:]) or ARCHIVES:
        row = sweep(name, gt, cast)
        if row is None:
            print(f"{name:6} no store or unsigned")
            continue
        n = len(row["released"])
        print(
            f"{name:6} released={n:3}  manner-gains={row['gains']:3}  plot-leaks={row['leaks']:3}"
        )
        for r in row["released"]:
            tag = f"LEAK {r['leak'][0]}" if r["leak"] else "gain"
            print(
                f"        {tag} p{r['paragraph']} {r['owner']} [{r['scene']}] {r['before']}->{r['after']}"
            )
        total = {
            "released": total["released"] + n,
            "gains": total["gains"] + row["gains"],
            "leaks": total["leaks"] + row["leaks"],
        }
    print("total", total)


if __name__ == "__main__":
    main()
