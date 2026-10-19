"""Wave-5 gold graders (SPEC-45): the corpus' hand-annotated `excerpt_events`
(first paragraph, plan-line terms, `narrated` paragraphs; indices of
`text.split("\\n\\n")`) placed in chapters by a run's outline, and
`owned_phrases`. Nothing here imports or re-implements a product rule."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from app.core.prompt_markers import ACT_CHAPTERS_HEADER, OUTLINE_SOURCE_WARNING

_ACT = re.compile(re.escape(ACT_CHAPTERS_HEADER).replace(r"\{number\}", r"(\d+)"))


def outline_chapters(records: list[dict]) -> list[list[str]]:
    """Each chapter's scene lines ("title: summary"), chapter 1 first, from the
    run's latest outline:beats answer for each act."""
    from evals.graders.prompt_contract import _json_array, prompt_text, purpose

    acts: dict[int, list] = {}
    for rec in (r for r in records if purpose(r) == "outline:beats"):
        m = _ACT.search(prompt_text(rec))
        acts[int(m.group(1)) if m else len(acts) + 1] = _json_array(rec["answer"])
    return [
        [
            f"{s.get('title', '')}: {s.get('summary', '')}"
            for s in ch.get("children") or []
        ]
        for a in sorted(acts)
        for ch in acts[a]
        if isinstance(ch, dict)
    ]


def outline_hash(records: list[dict]) -> str:
    """SHA-256 of the parsed outline, the key of a signed placement file
    (evals/README.md): an answer re-archived with other whitespace matches."""
    text = json.dumps(outline_chapters(records), sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


_SUFFIX = r"(?:s|es|ed|ing|y|ies)?"  # "summon" names "Summoned", not "memory"


def draft_placements(events: list[dict], chapters: list[list[str]]) -> dict:
    """W7-2 (b): the term match as a PROPOSAL, {event: {chapter, line}} for every
    event, by the first scene line naming one of its terms as a word, in source
    order. A line places one event (W4b2's summons scene mentions the corner
    office it summons Milo to), unless every line naming it is taken. Only the
    CLI and `disagreements` read it; no gate does (SPEC-54)."""
    lines = [(n, ln) for n, ch in enumerate(chapters, 1) for ln in ch]
    taken: set[int] = set()
    out = {}
    for ev in events:
        terms = [re.compile(rf"\b{re.escape(t)}{_SUFFIX}\b", re.I) for t in ev["terms"]]
        hits = [
            i for i, (_, ln) in enumerate(lines) if any(t.search(ln) for t in terms)
        ]
        cell = (None, None)
        if hits:
            i = next((i for i in hits if i not in taken), hits[0])
            taken.add(i)
            cell = lines[i]
        out[ev["event"]] = {"chapter": cell[0], "line": cell[1]}
    return out


PLACEMENTS = Path(__file__).resolve().parent.parent / "placements"
_CORPORA = PLACEMENTS.parent / "corpora"


def placements(
    records: list[dict], events: list[dict], signed_dir: Path
) -> dict | None:
    """D4: the signed file (evals/README.md) whose `outline_hash` is this run's,
    else None. A draft (no `signed_by`) is not signed; two signed files for one
    outline, or a signed file missing an event's cell, is an error, never a
    guess. Its optional `source_facts` cell ({fact id: {contradicted, line}})
    reads as None when the file has none (wave 10)."""
    key = outline_hash(records)
    docs = {
        p.name: json.loads(p.read_text(encoding="utf-8"))
        for p in Path(signed_dir).glob("*.json")
    }
    found = [
        n
        for n, d in sorted(docs.items())
        if d.get("outline_hash") == key and d.get("signed_by")
    ]
    if len(found) > 1:
        raise ValueError(f"{found} all sign outline {key[:12]}")
    if not found:
        return None
    doc = docs[found[0]]
    if missing := [e["event"] for e in events if e["event"] not in doc["placements"]]:
        raise ValueError(f"{found[0]} has no cell for {missing}")
    return {"source_facts": None} | doc


# W10-1 (c): SPEC-73's located (` ("<quote>")`) and unlocated warning endings.
_QUOTED = re.compile(
    r' \((?:could not locate )?"(?P<quote>.*)"(?P<lost> in the outline)?\)$'
)


def outline_source_conflicts(warnings: list[str] | None) -> list[dict]:
    """D1: the warnings carrying OUTLINE_SOURCE_WARNING as {detail, quote,
    located}; a warning with no quote keeps its whole text as the detail."""
    n = len(OUTLINE_SOURCE_WARNING)
    found = [w[n:] for w in warnings or [] if w.startswith(OUTLINE_SOURCE_WARNING)]
    return [
        {"detail": b[: m.start()] if m else b, "quote": m and m["quote"]}
        | {"located": bool(m) and not m["lost"]}
        for b in found
        for m in [_QUOTED.search(b)]
    ]


def source_fact_hits(gt: dict, signed: dict | None, warnings) -> dict:
    """D1, reported: per gold `source_facts` row, the signed `contradicted`
    (None unsigned) and whether a conflict's detail names the fact's subject,
    by a full name or listed alias (wave 11, D7: "the Bell account" is not Bell)."""
    from evals.graders.prompt_contract import _named_whole

    people = [*gt["fidelity"]["principals"], *gt.get("secondary_cast", [])]
    people = [p if isinstance(p, dict) else {"name": p} for p in people]
    details = [c["detail"] for c in outline_source_conflicts(warnings)]
    cells = (signed or {}).get("source_facts") or {}
    return {
        f["id"]: {"contradicted": (cells.get(f["id"]) or {}).get("contradicted")}
        | {"names_the_fact": any(_named_whole(d, p) for d in details for p in subject)}
        for f in gt.get("source_facts") or []
        for subject in [[p for p in people if _named_whole(f["fact"], p)]]
    }


def placed_chapters(signed: dict) -> dict[str, int]:
    """{event: chapter} of a signed file's placed cells."""
    cells = signed["placements"].items()
    return {e: c["chapter"] for e, c in cells if c["chapter"] is not None}


def unplaced(events: list[dict], signed: dict) -> list[str]:
    """D4: the events the signer read as not planned in this outline (a span
    without terms, such as `training`, is never placed)."""
    placed = placed_chapters(signed)
    return [e["event"] for e in events if e["terms"] and e["event"] not in placed]


def disagreements(records: list[dict], events: list[dict], signed: dict | None):
    """Reported (S-4): every cell where the term-match draft and the signed file
    give different chapters; None without a signed file."""
    if signed is None:
        return None
    draft = draft_placements(events, outline_chapters(records))
    cells = signed["placements"]
    return [
        {"event": e, "draft": d["chapter"], "signed": cells[e]["chapter"]}
        for e, d in draft.items()
        if d["chapter"] != cells[e]["chapter"]
    ]


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower().replace("’", "'")))


def paragraph_of(sample: str, paragraphs: list[str]) -> int | None:
    """The first paragraph holding every clause of `sample` as a word run (a
    quote split around its speech tag is one sample)."""
    pieces = [f" {w} " for w in map(_words, re.split(r"[.,!?;:—–…]", sample)) if w]
    flats = (f" {_words(p)} " for p in paragraphs)
    return next((i for i, f in enumerate(flats) if all(p in f for p in pieces)), None)


def plot_reasons(kind, sample, chapter, gold: dict, placed: dict) -> tuple:
    """(reasons, paragraph, later) for one shown sample. Part 1: a movement
    sample narrating a placed event. Part 2 (needs the scene's `chapter`): a
    voice sample in, or a movement sample narrating, a later chapter's event."""
    i = paragraph_of(sample, gold["paragraphs"]) if gold["events"] else None
    if i is None:
        return [], None, False
    ev = [e for e in gold["events"] if e["from"] <= i][-1]
    at = placed.get(ev["event"])
    narrates = at is not None and i in ev["narrated"]
    later = bool(chapter and at and at > chapter and (kind == "voice" or narrates))
    reasons = [f"narrates {ev['event']} (ch {at})"] * (narrates and kind != "voice")
    reasons += [f"{kind} sample is ch-{at} plot in ch {chapter}"] * later
    return [f"paragraph {i}: {r}" for r in reasons], i, later


def gold_tail(chapter, gold: dict, placed: dict) -> int | None:
    """The first paragraph of the first event, in source order, placed after
    `chapter`: the gold form of the later-plot cut (W5-1 (b))."""
    later = (
        e for e in gold["events"] if chapter and placed.get(e["event"], 0) > chapter
    )
    return next((e["from"] for e in later), None)


def only_plot(lines, chapter, gold: dict, placed: dict) -> list[int]:
    """D6: the paragraphs of an owner's movement lines when every one is plot
    here (part 1, or at or past the gold tail), else []."""
    tail = gold_tail(chapter, gold, placed)
    hits = [plot_reasons("movement", m, chapter, gold, placed) for m in lines or ()]
    plot = [r or (tail is not None and i is not None and i >= tail) for r, i, _ in hits]
    return sorted({i for _, i, _ in hits}) if hits and all(plot) else []


def borrowed_phrase(text: str, cast: list[str], owned: dict[str, list[str]]) -> list:
    """Owned phrases spoken (inside quotes) while their owner is not in `cast`."""
    spoken = "\n".join(re.findall(r"[\"“]([^\"”]*)[\"”]", text))
    return [
        f"{o}: {p}"
        for o, ps in owned.items()
        if o not in cast
        for p in ps
        if p in spoken
    ]


def _empty(answer: str | None) -> bool:
    """A missing answer, or an empty JSON value: `[]`, `""`, `null`, or an object
    whose values are all empty one level down (`{"findings": []}`, D9)."""
    try:
        value = json.loads(answer or "null")
    except json.JSONDecodeError:
        return not (answer or "").strip()
    if isinstance(value, dict):
        return all(v in ("", [], {}, None) for v in value.values())
    return value in ("", [], None)


def replayed(records: list[dict], against: list[dict]) -> dict[str, dict]:
    """By purpose, the share of this run's answers byte-identical to one in
    `against`; a prompt this run already sent is not counted again, and neither
    is an empty answer (`_empty`: continuity's `[]` and `{"findings": []}`)."""
    from evals.graders.prompt_contract import prompt_text, purpose

    old, seen, out = {r.get("answer") for r in against}, set(), {}
    for rec in records:
        if _empty(rec.get("answer")):
            continue
        if (prompt := prompt_text(rec)) not in seen:
            seen.add(prompt)
            row = out.setdefault(purpose(rec), {"n": 0, "replayed": 0})
            row["n"] += 1
            row["replayed"] += rec.get("answer") in old
    return {p: {**r, "rate": round(r["replayed"] / r["n"], 4)} for p, r in out.items()}


def regrade(run_dir: str | Path) -> dict:
    """The signed regrade of one eval run (a dir holding its
    eval-report-<book>.json and answered-archive/): its model archive graded
    with the movement store and withheld export its own report holds
    (`steps.prose.voice.movement`, `.withheld`).
    A principal read as "none indexed" while that store holds lines for them is
    an error, not a result (wave 8)."""
    from evals.graders import prompt_contract as pc

    run = Path(run_dir)
    reports = sorted(run.glob("eval-report-*.json"))
    if len(reports) != 1:
        raise ValueError(f"{run}: want one eval-report-*.json, found {len(reports)}")
    doc = json.loads(reports[0].read_text(encoding="utf-8"))
    voice = (doc["steps"].get("prose") or {}).get("voice") or {}
    store = voice.get("movement")
    records = pc.load_archive(run / "answered-archive" / "model" / "answered")
    rows = (doc["steps"].get("revise") or {}).get("revisions")  # tighten_clause
    inputs = pc.book_inputs(doc["book"])
    out = pc.grade(
        records,
        **inputs,
        **{"movement_store": store, "revise_rows": rows},
        withheld=voice.get("withheld"),  # W9-1: the run's export, or None
    )
    # W10-1 (c), reported: the outline step's warnings, read with the signed cell.
    warnings = (doc["steps"].get("outline") or {}).get("app_warnings")
    signed = placements(records, inputs["excerpt_events"] or [], PLACEMENTS)
    gt = json.loads((_CORPORA / doc["book"] / "ground_truth.json").read_text())
    out["outline_source_conflicts"] = outline_source_conflicts(warnings)
    out["source_fact_hits"] = source_fact_hits(gt, signed, warnings)
    for who, why in ((out.get("movement") or {}).get("ineligible") or {}).items():
        if (store or {}).get(who) and pc.NONE_INDEXED in why:
            raise ValueError(f"{who}: read as none indexed; the store holds lines")
    return out


def main(argv: list[str] | None = None) -> None:
    """`python -m evals.graders.plot draft <answered-dir> [--run W7] [--book cel]`:
    an unsigned placement file for the PM to read and sign (evals/README.md).
    `python -m evals.graders.plot regrade <run-dir>`: the run's signed regrade
    with its own movement store (`regrade`)."""
    import argparse

    from evals.graders.prompt_contract import load_archive, purpose

    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("command", choices=["draft", "regrade"])
    ap.add_argument("archive", help="the relay's answered/ dir (regrade: the run's)")
    ap.add_argument("--run", default=None, help="the run's name, e.g. W7")
    ap.add_argument("--book", default="cel")
    args = ap.parse_args(argv)
    if args.command == "regrade":
        print(json.dumps(regrade(args.archive), indent=2, ensure_ascii=False))
        return
    corpus = _CORPORA / args.book
    gt = json.loads((corpus / "ground_truth.json").read_text(encoding="utf-8"))
    records = load_archive(args.archive)
    if not any(purpose(r) == "outline:beats" for r in records):  # SPEC-68
        raise SystemExit(f"no outline:beats records in {args.archive}")
    cells = draft_placements(gt["excerpt_events"], outline_chapters(records))
    doc = {"run": args.run, "outline_hash": outline_hash(records)}
    doc |= {"signed_by": None, "signed_at": None, "placements": cells}
    print(json.dumps(doc, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
