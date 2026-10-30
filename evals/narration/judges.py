"""SPEC-96 M2 and M3: the judge measures, as two opt-in eval steps.

`narration_judges` reads this book's `chapters` runs named by from_runs
(EVAL_PRIOR_REPORTS, `generate.prior_reports`) and judges every chapter of
each, keyed by run id, ablation, app sha, position and replicate:

- M2, narrator attribution: RN-v2's forced choice (evals.validation.rn2: its
  `prompt`, `parse` and `voice_samples`, unchanged) on up to MAX_PASSAGES
  narration passages per chapter (a scene's `scene_narration`, scenes in
  order, those of at least MIN_PASSAGE_WORDS words). The two narrators are
  the chapter's planned narrator (its `pov`) and the book's drift donor
  (`narrator_pair`: the S7 donor of the S7 chapter that narrator tells, the
  S7 narrator when the planned narrator is that donor, else the book's first
  S7 donor), labelled 1 and 2 in a seeded order per passage. Correct = the
  planned narrator. A chapter whose POV has no voice source (its own
  narration in spec.json's `voice_sources`) is not judged on M2, counted.
- M3, speaker attribution: a scene's quoted lines whose speaker its own speech
  tags fix (`tagged_lines`: an explicit tag beside the span, "said Mina",
  "Verhoeven said", resolving by the app's `speaker_matches` to exactly one
  cast member; a split quote of the same paragraph credited by the app's rule
  to that tagged speaker joins the line). A pronoun tag ("he said", "I said"),
  an action beat, an untagged line or a tag naming no single cast member
  leaves the line out, counted (`excluded`). The judge sees the scene with
  every speech tag removed and each kept line numbered in place, and the
  scene's cast (the planned beat's characters plus the lines' speakers) in a
  seeded order; it names each line's speaker, one call per scene, JSON
  {"speakers": [...]}. A reply of the wrong length leaves the scene's lines
  unrated (a merged or split line would shift every later answer, W16-1).

Two judges, as chapters_compare: Sonnet and the free chain (`compare.judges`).
The answers are recorded beside each item (the key); the prompt never shows
them, a run id, an arm or an app sha. A refused (UnusableReplyError) or
unparseable reply leaves its item unrated; an unavailable judge
(JudgeUnavailable) leaves the rest of its items unrated and the other judge
carries on; a spend pause or spent cap (QuotaExhaustedError: the run's
MONTHLY_COST_CEILING_USD, claude_usd_cap) stops the step, its judgments so far
kept. Before any call the worst-case cost (every call priced as the paid
judge) is checked against that ceiling, as rn2_validation: over it, nothing
is asked. Chapters with a quota failure or no text are not judged (the
analysis leaves their pairs out). No resume: a stopped step is re-dispatched.

`narration_validation` (SPEC-96 Validation) judges the book's source:
- M3's ceiling on source scenes with dialogue (each source chapter of the
  corpus excerpts cut at paragraph boundaries into scenes of about
  SCENE_WORDS words; up to VALIDATION_SCENES spread over those with a tagged
  line; the cast: members the scene names plus the lines' speakers). The bar
  (`summarize_validation`): Sonnet's mean per-scene accuracy >= the mean
  per-scene chance (mean of 1/options per line) + 0.20; Groq's beside it.
- M2 re-checked on the source chapters each voice source names (two passages
  each, cut as M3's scenes are), accuracy reported, no bar.

python -m evals.narration.judges extract REPORT_OR_DIR...   (M3 yield, no judge)
python -m evals.narration.judges estimate BOOK REPORTS_ROOT
python -m evals.narration.judges validation REPORT_OR_DIR... [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
from collections import Counter
from pathlib import Path

from evals.chapters.generate import prior_reports
from evals.narration.contract import ablation, paragraphs, run_id
from evals.steps.base import StepContext, step
from evals.validation import rn2

STEP = "narration_judges"
VALIDATION_STEP = "narration_validation"
DEFAULT_SEED = 21
MAX_PASSAGES = 2
MIN_PASSAGE_WORDS = 60
SCENE_WORDS = 700
VALIDATION_SCENES = 12
CHANCE_MARGIN = 0.20
_OUT_MAX = 1024  # the judge's max_tokens (harness/judge.py)
_SYSTEM_CHARS = len("Reply ONLY with JSON.")

M3_ASK = (
    'Below is a SCENE from a novel. Its speech tags ("she said", "said '
    'Mina") were removed, and some lines of dialogue are numbered in place, '
    "[1], [2], .... For each numbered line, name its speaker: exactly one name "
    "from CAST, judged from the scene (who is present, who is addressed, turn "
    "order, what the speaker knows) and from how each character speaks."
)


# --- M2: narrator attribution ------------------------------------------------


def narrator_pair(book: str, pov: str | None) -> tuple[str, str] | None:
    """(planned narrator, donor) for a chapter whose POV is `pov`, from the
    S7 chapters (evals/validation/chapters.json): the donor of the S7
    chapter `pov` narrates; the S7 chapter's narrator when `pov` is its
    donor; else the book's first S7 donor. None when `pov` has no voice
    source (its narration is not separable) or the book has no S7 chapter."""
    from evals.steps.pipeline import CORPORA
    from evals.validation.build import load_config

    spec = CORPORA / book / "spec.json"
    sources = json.loads(spec.read_text("utf-8")).get("voice_sources") or {}
    if not pov or pov not in sources:
        return None
    s7 = [c for c in load_config() if c["book"] == book and c.get("donor")]
    for c in s7:
        if c["narrator"] == pov:
            return pov, c["donor"]["narrator"]
    for c in s7:
        if c["donor"]["narrator"] == pov:
            return pov, c["narrator"]
    for c in s7:
        if c["donor"]["narrator"] in sources:
            return pov, c["donor"]["narrator"]
    return None


def passages(scenes: list[str]) -> list[str]:
    """Up to MAX_PASSAGES narration passages: each scene's `scene_narration`,
    in order, those of MIN_PASSAGE_WORDS words or more."""
    from evals.steps.pipeline import scene_narration

    out = []
    for s in scenes:
        n = scene_narration(s)
        if len(n.split()) >= MIN_PASSAGE_WORDS:
            out.append(n)
        if len(out) == MAX_PASSAGES:
            break
    return out


def m2_items(book: str, pov: str | None, scenes: list[str], rng) -> tuple[list, dict]:
    """([(item, key)], skipped): RN-v2 items for one chapter. Each item is
    exactly rn2's shape (samples by label, passage); the key holds the answer
    label and the narrators by label."""
    pair = narrator_pair(book, pov)
    if pair is None:
        return [], {"skipped": f"no voice source for POV {pov!r}"}
    texts = passages(scenes)
    if not texts:
        return [], {"skipped": "no narration passage"}
    planned, donor = pair
    samples = {
        "planned": rn2.voice_samples(book, planned, texts),
        "donor": rn2.voice_samples(book, donor, texts),
    }
    out = []
    for text in texts:
        order = ["planned", "donor"]
        rng.shuffle(order)
        labels = {str(n): who for n, who in enumerate(order, 1)}
        item = {"samples": {n: samples[w] for n, w in labels.items()}, "passage": text}
        key = {
            "answer": order.index("planned") + 1,
            "labels": {
                n: (planned if w == "planned" else donor) for n, w in labels.items()
            },
        }
        out.append((item, key))
    return out, {"planned": planned, "donor": donor}


# --- M3: speaker attribution ---------------------------------------------------


def _members(cast: list[str], aliases: dict | None = None) -> list[dict]:
    aliases = aliases or {}
    return [{"name": n, "aliases": list(aliases.get(n) or [])} for n in cast]


def resolve(who: str | None, members: list[dict]) -> str | None:
    """The one cast member a tag's subject names (`speaker_matches`), else None."""
    from app.parsing.character_extractor import speaker_matches

    if not who:
        return None
    got = [
        m["name"]
        for m in members
        if speaker_matches(who, m["name"], m["aliases"], members)
    ]
    return got[0] if len(got) == 1 else None


def tagged_lines(text: str, members: list[dict]) -> dict:
    """{"paragraphs": [[span, ...]], "lines": [{speaker, line}], "excluded"}:
    per normalized paragraph, its quoted spans as (start, end, line number or
    None); the lines the scene's own tags fix (module docstring); the
    speech spans left out."""
    from app.parsing.character_extractor import (
        _attribute_paragraph,
        _canon_speaker,
        _tagged,
        is_speech,
        normalize_quotes,
    )

    lines: list[dict] = []
    paras, excluded = [], 0
    for raw in paragraphs(text):
        para = normalize_quotes(" ".join(raw.split()))
        tagged = list(_tagged(para))
        spans, _ = _attribute_paragraph(para)
        explicit = {}
        for i, (*_x, a_tag, b_tag) in enumerate(tagged):
            who = (a_tag and _canon_speaker(a_tag[2])) or (
                b_tag and _canon_speaker(b_tag[2])
            )
            if who:
                explicit[i] = resolve(who, members)
        named = set(explicit.values())
        only = next(iter(named)) if len(named) == 1 and None not in named else None
        row, prev, left = [], None, None
        for i, ((start, end, _closed), s) in enumerate(
            zip((t[0] for t in tagged), spans)
        ):
            name = explicit.get(i)
            if i not in explicit and only and resolve(s["speaker"], members) == only:
                name = only  # a split quote the app credits to the tagged speaker
            if name and s["text"]:
                if prev == name and row and row[-1][2] is not None:
                    lines[-1]["line"] += " " + s["text"]
                else:
                    lines.append({"speaker": name, "line": s["text"]})
                row.append((start, end, len(lines)))
                left = None
            else:
                # One left-out line: a split quote the app credits to one
                # (unresolvable) speaker counts once.
                same = left is not None and s["speaker"] and s["speaker"] == left
                if s["text"] and is_speech(s) and not same:
                    excluded += 1
                left = s["speaker"] if s["text"] else None
                row.append((start, end, None))
            prev = name
        paras.append((para, row))
    return {"paragraphs": paras, "lines": lines, "excluded": excluded}


def blinded_scene(found: dict) -> str:
    """The scene with every speech tag removed and each kept line numbered
    in place ([n] "..."); other quotes stay as written."""
    from app.parsing.character_extractor import tag_spans

    out = []
    for para, row in found["paragraphs"]:
        edits = [(a, b, "") for a, b in tag_spans(para)]
        for start, end, n in row:
            if n is not None:
                text = para[start:end].strip()
                edits.append((start - 1, min(len(para), end + 1), f'[{n}] "{text}"'))
        edits.sort()
        pieces, at = [], 0
        for a, b, new in edits:
            if a < at:  # a tag inside a replaced span: already gone
                continue
            pieces += [para[at:a], new]
            at = b
        pieces.append(para[at:])
        text = " ".join("".join(pieces).split())
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        text = re.sub(r"(?<=[\"\]])[,;:]+(?=\s|$)", "", text)
        text = re.sub(r'(?<=[.!?,]")[.,]', "", text)
        if re.search(r"\w", text):
            out.append(text)
    return "\n\n".join(out)


def m3_prompt(scene: str, cast: list[str], n: int) -> str:
    return (
        f"{M3_ASK}\n\nCAST: {', '.join(cast)}\n\nSCENE:\n{scene}\n\n"
        f'Reply ONLY with JSON: {{"speakers": [...]}}, exactly {n} names, the '
        "speaker of line 1 first."
    )


def m3_parse(reply, cast: list[str], n: int) -> list[str | None] | None:
    """Each line's named cast member (None: not exactly one of `cast`); None
    for the whole scene when the reply is not a list of `n` answers."""
    from app.parsing.names import match

    got = reply.get("speakers") if isinstance(reply, dict) else None
    if not isinstance(got, list) or len(got) != n:
        return None
    out = []
    for g in got:
        if not isinstance(g, str):
            out.append(None)
            continue
        exact = [c for c in cast if c.lower() == g.strip().lower()]
        hits = exact or [c for c in cast if match(g, c)]
        out.append(hits[0] if len(hits) == 1 else None)
    return out


def scene_cast(beat_cast: list[str], lines: list[dict], rng) -> list[str]:
    """The listed cast: the beat's characters, then any line's speaker not
    among them, in a seeded order."""
    cast = list(dict.fromkeys([*beat_cast, *(ln["speaker"] for ln in lines)]))
    rng.shuffle(cast)
    return cast


def m3_item(text: str, beat_cast: list[str], members: list[dict], rng) -> dict:
    """{prompt, cast, answers, excluded, outside_beat} for one scene (prompt
    None when it has no tagged line)."""
    found = tagged_lines(text, members)
    lines = found["lines"]
    if not lines:
        return {"prompt": None, "answers": [], "excluded": found["excluded"]}
    cast = scene_cast(beat_cast, lines, rng)
    return {
        "prompt": m3_prompt(blinded_scene(found), cast, len(lines)),
        "cast": cast,
        "answers": [ln["speaker"] for ln in lines],
        "excluded": found["excluded"],
        "outside_beat": sum(1 for ln in lines if ln["speaker"] not in beat_cast),
    }


# --- asking ------------------------------------------------------------------


class Stop(Exception):
    """The run's spend ceiling or a spent cap: stop the step."""


async def ask(judge, prompt: str) -> tuple[dict | None, str]:
    """(reply, status): `ok`, `refused` (UnusableReplyError) or `unavailable`
    (the judge's chain gave out: it is not asked again). A spend pause or
    spent cap raises Stop."""
    from app.llm.errors import QuotaExhaustedError, UnusableReplyError
    from evals.harness.judge import JudgeUnavailable

    if getattr(judge, "exhausted", False):
        return None, "unavailable"
    try:
        return await judge.ask_json(prompt), "ok"
    except JudgeUnavailable:
        return None, "unavailable"
    except UnusableReplyError:
        return None, "refused"
    except QuotaExhaustedError as e:
        raise Stop(f"{type(e).__name__}: {e}"[:300]) from e


def _tally() -> dict:
    return {"correct": 0, "rated": 0, "unparseable": 0, "refused": 0, "unavailable": 0}


async def judge_m2(judge, items: list) -> dict:
    t = _tally() | {"total": len(items), "provider": judge.provider_id, "choices": []}
    for item, key in items:
        reply, status = await ask(judge, rn2.prompt(item))
        choice = rn2.parse(reply) if status == "ok" else None
        if status != "ok":
            t[status] += 1
        elif choice is None:
            t["unparseable"] += 1
        else:
            t["rated"] += 1
            t["correct"] += choice == key["answer"]
        t["choices"].append(choice)
    return t


async def judge_m3(judge, scenes: list[dict]) -> dict:
    """Per chapter: correct among rated lines, eligible lines, unrated ones."""
    t = _tally() | {"eligible": 0, "provider": judge.provider_id, "named": []}
    for sc in scenes:
        if not sc["prompt"]:
            continue
        n = len(sc["answers"])
        t["eligible"] += n
        reply, status = await ask(judge, sc["prompt"])
        got = m3_parse(reply, sc["cast"], n) if status == "ok" else None
        if status != "ok":
            t[status] += n
            got = [None] * n
        elif got is None:
            t["unparseable"] += n
            got = [None] * n
        else:
            t["unparseable"] += sum(g is None for g in got)
            t["rated"] += sum(g is not None for g in got)
            t["correct"] += sum(g == a for g, a in zip(got, sc["answers"]))
        t["named"].append(got)
    return t


# --- cost --------------------------------------------------------------------


def estimate(prompts: list[str], judges_on: int = 1) -> dict:
    """Calls, prompt tokens and USD if every call went to the paid judge."""
    from app.llm.prices import PRICES

    tokens = sum((len(p) + _SYSTEM_CHARS) // 4 for p in prompts)
    calls = len(prompts)
    price = PRICES[rn2._PRICED_AS]
    return {
        "judge_calls": calls,
        "prompt_tokens": tokens,
        "usd_if_all_paid": round(price.cost(tokens, calls * 40), 2),
        "usd_worst": round(price.cost(tokens, calls * _OUT_MAX), 2),
        "judges": judges_on,
        "note": "one judge's calls; the free chain costs $0",
    }


def _ceiling() -> float | None:
    return rn2._ceiling()


def _over_ceiling(est: dict) -> str | None:
    ceiling = _ceiling()
    if ceiling is not None and est["usd_worst"] > ceiling:
        return f"worst-case ${est['usd_worst']} is over this run's ceiling ${ceiling}"
    return None


# --- the chapters to judge ---------------------------------------------------


def chapters_runs(book: str) -> list[dict]:
    """This book's `chapters` runs from from_runs: [{meta, rows}]."""
    out = []
    for r in prior_reports(book):
        res = (r.get("steps") or {}).get("chapters")
        if not isinstance(res, dict) or not res.get("chapters"):
            continue
        out.append(
            {
                "meta": {
                    "run": run_id(Path(r["_file"]), r),
                    "ablation": ablation(r),
                    "app_sha": r.get("app_sha"),
                    "outline_digest": res.get("outline_digest"),
                    "batch": res.get("batch"),
                },
                "rows": res["chapters"],
            }
        )
    runs = [o["meta"]["run"] for o in out]
    if len(set(runs)) != len(runs):
        raise SystemExit(f"{STEP}: two `chapters` reports of {book} share a run id")
    return out


def scene_texts(row: dict) -> list[tuple[str, list[str]]]:
    """[(scene text, its planned beat's characters)] with text, in order:
    scene k was written for planned beat k (generate_planned's order)."""
    from evals.baseline.blind import strip_markers

    beats = row.get("planned_beats") or []
    out = []
    for k, s in enumerate(row.get("scenes") or []):
        text = strip_markers(s.get("content") or "")
        if text.strip():
            cast = (beats[k] if k < len(beats) else {}).get("characters") or []
            out.append((text, list(cast)))
    return out


def plan(book: str, gt: dict, seed: int) -> list[dict]:
    """Every chapter's M2 items and M3 scenes, deterministically."""
    members = _members(gt.get("cast") or [])
    out = []
    for run in chapters_runs(book):
        m = run["meta"]
        for row in run["rows"]:
            rep, pos = row.get("replicate", 1), row.get("position")
            rng = random.Random(f"{seed}|{book}|{m['run']}|{rep}|{pos}")
            scenes = scene_texts(row)
            rec = m | {
                "book": book,
                "position": pos,
                "replicate": rep,
                "failure": row.get("failure"),
                "scenes": len(scenes),
                "pov": row.get("pov"),
            }
            if row.get("failure") == "quota" or not scenes:
                out.append(rec | {"judged": False, "m2_items": [], "m3_scenes": []})
                continue
            items, who = m2_items(book, row.get("pov"), [t for t, _ in scenes], rng)
            m3 = [m3_item(t, cast, members, rng) for t, cast in scenes]
            out.append(
                rec | {"judged": True, "m2": who, "m2_items": items, "m3_scenes": m3}
            )
    return out


def _prompts(planned: list[dict]) -> list[str]:
    return [rn2.prompt(i) for c in planned for i, _ in c["m2_items"]] + [
        s["prompt"] for c in planned for s in c["m3_scenes"] if s["prompt"]
    ]


async def judge_planned(planned: list[dict], js: dict) -> tuple[list[dict], str | None]:
    """Judge every planned chapter with each judge; (records, stopped)."""
    records, stopped = [], None
    for c in planned:
        rec = {k: v for k, v in c.items() if k not in ("m2_items", "m3_scenes")}
        rec["m2_key"] = [k for _, k in c["m2_items"]]
        rec["m3_key"] = [s["answers"] for s in c["m3_scenes"] if s["prompt"]]
        rec["m3_lines"] = sum(len(s["answers"]) for s in c["m3_scenes"])
        rec["m3_excluded"] = sum(s["excluded"] for s in c["m3_scenes"])
        rec["m3_outside_beat"] = sum(s.get("outside_beat", 0) for s in c["m3_scenes"])
        rec["m2"], rec["m3"] = rec.get("m2") or {}, {}
        rec["m2_judges"] = {}
        if c["judged"] and not stopped:
            try:
                for name, j in js.items():
                    if j is None:
                        continue
                    if c["m2_items"]:
                        rec["m2_judges"][name] = await judge_m2(j, c["m2_items"])
                    rec["m3"][name] = await judge_m3(j, c["m3_scenes"])
            except Stop as e:
                stopped = str(e)
        records.append(rec)
    return records, stopped


@step(STEP, needs_api=True, repeatable=False)
async def step_narration_judges(ctx: StepContext) -> dict:
    """M2 and M3 on this book's chapters runs (from_runs), both judges."""
    from evals.chapters.compare import judges

    seed = int(os.getenv("NARRATION_SEED") or DEFAULT_SEED)
    planned = plan(ctx.book, ctx.ground_truth, seed)
    if not planned:
        return {
            "skipped": True,
            "reason": f"no `chapters` run of {ctx.book} in from_runs",
        }
    est = estimate(_prompts(planned))
    print(f"{STEP} {ctx.book}: estimate {est}", flush=True)
    if why := _over_ceiling(est):
        return {"skipped": True, "reason": why, "estimate": est}
    js = judges()
    records, stopped = await judge_planned(planned, js)
    judged = sum(1 for r in records if r["judged"])
    return {
        "score": None,  # aggregate with evals.narration.analyze
        "judge_explanation": f"{STEP}: {judged}/{len(records)} chapters judged"
        + (f"; stopped: {stopped}" if stopped else ""),
        "narration": {
            "book": ctx.book,
            "seed": seed,
            "judges": {n: (j.provider_id if j else None) for n, j in js.items()},
            "estimate": est,
            "stopped": stopped,
            "chapters": records,
        },
    }


# --- validation on the source ------------------------------------------------


def source_scenes(book: str) -> list[dict]:
    """Each source chapter cut at paragraph boundaries into scenes of about
    SCENE_WORDS words: [{heading, index, text, framed}]."""
    from evals.narration.contract import source_chapters

    out = []
    for ch in source_chapters(book):
        cur, words, k = [], 0, 0
        for p in paragraphs(ch["text"]):
            if ch["framed"]:
                p = p.strip().strip('"“”').strip()
            cur.append(p)
            words += len(p.split())
            if words >= SCENE_WORDS:
                out.append(
                    {"heading": ch["heading"], "index": k, "text": "\n\n".join(cur)}
                )
                cur, words, k = [], 0, k + 1
        if cur:
            out.append({"heading": ch["heading"], "index": k, "text": "\n\n".join(cur)})
    return out


def _spread(xs: list, k: int) -> list:
    m = len(xs)
    if m <= k:
        return xs
    return [xs[i * (m - 1) // (k - 1)] for i in range(k)]


def validation_plan(book: str, gt: dict, seed: int) -> dict:
    """M3 on up to VALIDATION_SCENES source scenes with a tagged line, M2 on
    two passages of each voice-source chapter."""
    from evals.steps.pipeline import CORPORA, named_in

    cast = list(gt.get("cast") or [])
    members = _members(cast)
    scenes = source_scenes(book)
    with_lines = [s for s in scenes if tagged_lines(s["text"], members)["lines"]]
    m3 = []
    for s in _spread(with_lines, VALIDATION_SCENES):
        rng = random.Random(f"{seed}|{book}|m3|{s['heading']}|{s['index']}")
        present = [c for c in cast if named_in(s["text"], [c, c.split()[0]])]
        m3.append(
            {"heading": s["heading"], "index": s["index"]}
            | m3_item(s["text"], present, members, rng)
        )
    spec = json.loads((CORPORA / book / "spec.json").read_text("utf-8"))
    m2 = []
    for narrator, src in (spec.get("voice_sources") or {}).items():
        for heading in src.get("chapters") or []:
            chunks = [s["text"] for s in scenes if s["heading"] == heading]
            if src.get("quoted"):
                chunks = [
                    "\n\n".join(p.strip().strip('"“”').strip() for p in paragraphs(c))
                    for c in chunks
                ]
            rng = random.Random(f"{seed}|{book}|m2|{heading}")
            items, who = m2_items(
                book, narrator, _spread(chunks, MAX_PASSAGES * 2), rng
            )
            m2.append(
                {"heading": heading, "narrator": narrator, "m2": who, "items": items}
            )
    return {"m3": m3, "m2": m2}


@step(VALIDATION_STEP, needs_api=True, repeatable=False)
async def step_narration_validation(ctx: StepContext) -> dict:
    """M3's ceiling and M2's re-check on this book's source, both judges."""
    from evals.chapters.compare import judges

    seed = int(os.getenv("NARRATION_SEED") or DEFAULT_SEED)
    vp = validation_plan(ctx.book, ctx.ground_truth, seed)
    prompts = [s["prompt"] for s in vp["m3"] if s["prompt"]]
    prompts += [rn2.prompt(i) for c in vp["m2"] for i, _ in c["items"]]
    est = estimate(prompts)
    print(f"{VALIDATION_STEP} {ctx.book}: estimate {est}", flush=True)
    if why := _over_ceiling(est):
        return {"skipped": True, "reason": why, "estimate": est}
    js = judges()
    stopped, m3, m2 = None, [], []
    try:
        for s in vp["m3"]:
            rec = {
                k: s.get(k) for k in ("heading", "index", "cast", "answers", "excluded")
            }
            rec["judges"] = {}
            if not stopped:
                for name, j in js.items():
                    if j is not None:
                        rec["judges"][name] = await judge_m3(j, [s])
            m3.append(rec)
        for c in vp["m2"]:
            rec = {"heading": c["heading"], "narrator": c["narrator"], "m2": c["m2"]}
            rec["key"] = [k for _, k in c["items"]]
            rec["judges"] = {}
            if c["items"]:
                for name, j in js.items():
                    if j is not None:
                        rec["judges"][name] = await judge_m2(j, c["items"])
            m2.append(rec)
    except Stop as e:
        stopped = str(e)
    return {
        "score": None,
        "judge_explanation": f"{VALIDATION_STEP}: {len(m3)} scenes, {len(m2)} "
        "chapters" + (f"; stopped: {stopped}" if stopped else ""),
        "narration_validation": {
            "book": ctx.book,
            "seed": seed,
            "judges": {n: (j.provider_id if j else None) for n, j in js.items()},
            "estimate": est,
            "stopped": stopped,
            "m3": m3,
            "m2": m2,
        },
    }


def summarize_validation(paths: list[Path]) -> dict:
    """Per judge: M3 mean per-scene accuracy against mean per-scene chance
    (bar: accuracy >= chance + 0.20, read on Sonnet), M2 accuracy."""
    from evals.narration.contract import report_files

    m3: dict = {}
    m2: dict = {}
    for f in report_files(paths):
        v = (
            (json.loads(f.read_text("utf-8")).get("steps") or {}).get(VALIDATION_STEP)
            or {}
        ).get("narration_validation")
        if not v:
            continue
        for s in v["m3"]:
            for name, t in s["judges"].items():
                row = m3.setdefault(
                    name, {"acc": [], "chance": [], "providers": Counter()}
                )
                row["providers"][t["provider"]] += 1
                if t["rated"]:
                    row["acc"].append(t["correct"] / t["rated"])
                    row["chance"].append(1 / max(1, len(s["cast"] or [])))
        for c in v["m2"]:
            for name, t in c["judges"].items():
                row = m2.setdefault(name, {"correct": 0, "rated": 0, "chapters": 0})
                row["correct"] += t["correct"]
                row["rated"] += t["rated"]
                row["chapters"] += 1
    out: dict = {"m3": {}, "m2": {}}
    for name, r in m3.items():
        acc = sum(r["acc"]) / len(r["acc"]) if r["acc"] else None
        chance = sum(r["chance"]) / len(r["chance"]) if r["chance"] else None
        out["m3"][name] = {
            "scenes": len(r["acc"]),
            "accuracy": None if acc is None else round(acc, 4),
            "chance": None if chance is None else round(chance, 4),
            "passes": None if acc is None else acc >= chance + CHANCE_MARGIN,
            "providers": dict(r["providers"]),
        }
    for name, r in m2.items():
        out["m2"][name] = r | {
            "accuracy": round(r["correct"] / r["rated"], 4) if r["rated"] else None
        }
    sonnet = out["m3"].get("sonnet") or {}
    out["m3_validated"] = bool(sonnet.get("passes"))
    return out


# --- CLI ---------------------------------------------------------------------


def extract_yield(paths: list[Path]) -> list[dict]:
    """M3's tag extraction per chapter of every `chapters` report: lines kept
    and excluded (no judge)."""
    from evals.narration.contract import chapter_rows
    from evals.steps.pipeline import CORPORA

    out = []
    for meta, row in chapter_rows(paths):
        gt = json.loads(
            (CORPORA / meta["book"] / "ground_truth.json").read_text("utf-8")
        )
        members = _members(gt.get("cast") or [])
        found = [tagged_lines(t, members) for t, _ in scene_texts(row)]
        out.append(
            {
                "run": meta["run"],
                "book": meta["book"],
                "position": row.get("position"),
                "replicate": row.get("replicate", 1),
                "lines": sum(len(f["lines"]) for f in found),
                "excluded": sum(f["excluded"] for f in found),
                "per_scene": [len(f["lines"]) for f in found],
                "speakers": dict(
                    Counter(ln["speaker"] for f in found for ln in f["lines"])
                ),
            }
        )
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("reports", type=Path, nargs="+")
    s = sub.add_parser("estimate")
    s.add_argument("book")
    s.add_argument("root", type=Path, help="a from_runs-like directory of reports")
    v = sub.add_parser("validation")
    v.add_argument("reports", type=Path, nargs="+")
    v.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "extract":
        for r in extract_yield(a.reports):
            print(json.dumps(r))
        return
    if a.cmd == "estimate":
        from evals.steps.pipeline import CORPORA

        gt = json.loads((CORPORA / a.book / "ground_truth.json").read_text("utf-8"))
        os.environ["EVAL_PRIOR_REPORTS"] = str(a.root)
        print(json.dumps(estimate(_prompts(plan(a.book, gt, DEFAULT_SEED))), indent=1))
        return
    res = summarize_validation(a.reports)
    print(json.dumps(res, indent=1))
    if a.json:
        a.json.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
