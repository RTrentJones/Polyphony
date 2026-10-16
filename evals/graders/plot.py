"""Wave-5 gold graders (SPEC-45): the corpus' hand-annotated `excerpt_events`
(first paragraph, plan-line terms, `narrated` paragraphs; indices of
`text.split("\\n\\n")`) placed in chapters by a run's outline, and
`owned_phrases`. Nothing here imports or re-implements a product rule."""

from __future__ import annotations

import re

from app.core.prompt_markers import ACT_CHAPTERS_HEADER

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


def locate(events: list[dict], chapters: list[list[str]]) -> dict[str, int]:
    """{event: chapter} by the first scene line naming one of its terms, in
    source order. A line places one event (W4b2's summons scene mentions the
    corner office it summons Milo to), unless every line naming it is taken."""
    lines = [(n, ln.lower()) for n, ch in enumerate(chapters, 1) for ln in ch]
    taken: set[int] = set()
    out: dict[str, int] = {}
    for ev in events:
        hits = [
            i
            for i, (_, ln) in enumerate(lines)
            if any(t.lower() in ln for t in ev["terms"])
        ]
        if hits:
            i = next((i for i in hits if i not in taken), hits[0])
            taken.add(i)
            out[ev["event"]] = lines[i][0]
    return out


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


def replayed(records: list[dict], against: list[dict]) -> dict[str, dict]:
    """By purpose, the share of this run's answers byte-identical to one in
    `against`; a prompt this run already sent is not counted again."""
    from evals.graders.prompt_contract import prompt_text, purpose

    old, seen, out = {r.get("answer") for r in against}, set(), {}
    for rec in records:
        if (prompt := prompt_text(rec)) not in seen:
            seen.add(prompt)
            row = out.setdefault(purpose(rec), {"n": 0, "replayed": 0})
            row["n"] += 1
            row["replayed"] += rec.get("answer") in old
    return {p: {**r, "rate": round(r["replayed"] / r["n"], 4)} for p, r in out.items()}
