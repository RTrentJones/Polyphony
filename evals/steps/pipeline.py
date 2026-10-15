"""The eval steps, each registered against the step registry.

Every step is `async def (ctx: StepContext) -> dict`. Steps 2 (retrieval
separability) and the reference side of 3 run in-process with the app's own
embedder — free, no LLM. The rest drive the real API and are cached.

The book an eval writes into is the book an author would have: the corpus'
real cast entries (roles, descriptions, goals, voice), its style guide
(`style.json`), and, for the prose step, the voice-bearing excerpt uploaded as a
source so voice indexing runs. Generation goes through the product's own path
(outline -> confirm premise -> promote -> generate the planned scenes), and the
judge grades each artifact on the R1-R6 rubrics (evals/graders/rubrics.py) after
the deterministic pre-checks (cast recall, seam overlap).

Importing this module registers the steps (import side effect); `evals.run`
imports it so the registry is populated.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

from evals.graders import attribution, continuity, extraction, retrieval, rubrics
from evals.harness import embed
from evals.harness.vecmath import cosine
from evals.steps.base import StepContext, step

CORPORA = Path(__file__).resolve().parent.parent / "corpora"

_KNOCK = "Say a few sentences reacting to an unexpected knock at the door, at night."

# The fallback scene when the promoted outline gives no chapter to generate
# from: the corpus' second canonical beat, set where the corpus says it happens.
_FALLBACK_SETTINGS = {"cel": "Reconciliations floor, Aeon Holdings, Marrow District"}


def _cast_with_roles(gt: dict) -> list[dict]:
    """The corpus cast as character payloads.

    A corpus with `characters` entries (cel) supplies the real canon: role,
    description, goals, voice and aliases, as an author would have written
    them. Otherwise principals (when the corpus declares fidelity principals)
    are marked protagonist so the app's principal-cast gate has a real cast to
    enforce; everyone else is a supporting role, with a placeholder
    description."""
    entries = {c["name"]: c for c in gt.get("characters") or [] if c.get("name")}
    principals = {
        p["name"] for p in (gt.get("fidelity") or {}).get("principals", [])
    } or set(gt.get("cast", [])[:3])
    out = []
    for name in gt.get("cast", []):
        entry = entries.get(name)
        if entry is None:
            out.append(
                {
                    "name": name,
                    "role": "protagonist" if name in principals else "supporting",
                    "description": f"{name}, from the corpus.",
                }
            )
            continue
        payload = {
            k: entry[k]
            for k in ("name", "role", "description", "goals", "arc", "aliases")
            if entry.get(k)
        }
        if entry.get("voice"):
            payload["voice_characteristics"] = {"voice": entry["voice"]}
        out.append(payload)
    return out


def load_style(book: str) -> dict | None:
    """The corpus' style guide (`style.json`), or None when it has none."""
    path = CORPORA / book / "style.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_excerpts(book: str) -> str:
    return (CORPORA / book / "excerpts.txt").read_text(encoding="utf-8")


async def _book_with_cast(
    ctx: StepContext, title: str, genre: str = "", *, voice: bool = False
) -> dict:
    """A book carrying the corpus synopsis, its cast and its style guide: the
    canon an author would have. An eval book with no characters makes the
    fidelity gate vacuous.

    `voice=True` also uploads the corpus excerpt to the book as a source and
    commits the cast from it, so the voice-indexing job runs over the author's
    own prose (instead of voice samples injected around the product)."""
    gt = ctx.ground_truth
    book = await ctx.client.create_book(
        title=title, synopsis=gt["synopsis"], genre=genre
    )
    ids = {}
    for c in _cast_with_roles(gt):
        fields = {k: v for k, v in c.items() if k != "name"}
        created = await ctx.client.create_character(c["name"], book["id"], **fields)
        ids[c["name"]] = created["id"]
    book["character_ids"] = ids
    style = load_style(ctx.book)
    if style:
        await ctx.client.set_style(book["id"], style)
    book["style"] = style
    if voice and gt.get("gold_lines"):
        book["voice"] = await _index_voice(ctx, book["id"], list(ids))
    return book


async def _index_voice(ctx: StepContext, book_id: str, names: list[str]) -> dict:
    """Upload the excerpt into the book, commit the canon cast from it (names
    only, so the curated entries are not overwritten) and wait for voice
    indexing. Returns {source_id, chunks: {name: n}}."""
    up = await ctx.client.upload_source(
        f"{ctx.book}-voice.txt",
        load_excerpts(ctx.book).encode("utf-8"),
        title=f"eval-voice-{ctx.book}",
        book_id=book_id,
    )
    run = await ctx.client.wait_extraction(book_id, up["extraction_run_id"])
    if run.get("status") != "ready":
        return {"source_id": up["id"], "error": f"extraction {run.get('status')}"}
    await ctx.client.commit_extraction(
        book_id,
        up["extraction_run_id"],
        {"characters": [{"name": n} for n in names], "canon_entries": []},
    )
    chars = await ctx.client.wait_voice_indexed(up["id"])
    chunks = {}
    for c in chars:
        stats = (await ctx.client.get_character(c["id"])).get("voice_stats") or {}
        chunks[c["name"]] = stats.get("total_chunks", 0)
    return {"source_id": up["id"], "chunks": chunks}


def load_corpus(book: str) -> tuple[str, dict]:
    """The manuscript an author would upload, plus the ground truth.

    A corpus whose prose lives mostly in `synopsis.txt` (cel: a 389-char
    excerpt, with the cast in the 17k synopsis) uploads synopsis + excerpt.
    Uploading the excerpt alone graded extraction against a cast the text
    never names (Edric Thane is not in it), so the score was 0 whatever the
    model did."""
    d = CORPORA / book
    text = (d / "excerpts.txt").read_text(encoding="utf-8")
    synopsis = d / "synopsis.txt"
    if synopsis.exists():
        text = synopsis.read_text(encoding="utf-8").rstrip() + "\n\n" + text
    return (
        text,
        json.loads((d / "ground_truth.json").read_text(encoding="utf-8")),
    )


def proposed_characters(run: dict) -> list[dict]:
    """The character proposals of an extraction run, as the review screen sees
    them (GET /books/{id}/extractions/{run} → `proposals.characters`).

    Extraction PROPOSES; nothing is on the source until commit. Grading the
    source's committed cast instead (what the harness did before the review
    step shipped) read an empty list and pinned extraction at 0."""
    props = run.get("proposals") or {}
    out = []
    for c in props.get("characters") or []:
        if isinstance(c, str):
            c = {"name": c}
        if isinstance(c, dict) and (c.get("name") or "").strip():
            out.append(c)
    return out


async def _judge(ctx, rubric: str, content: str) -> tuple[float | None, str]:
    """(score, explanation) from the judge, or (None, why) when every judge in
    the chain is unavailable — so a step still reports its deterministic
    metrics (cast fidelity, word counts) and the gates on them still run."""
    from evals.harness.judge import JudgeUnavailable

    try:
        j = await ctx.judge.score(rubric, content)
    except JudgeUnavailable as e:
        return None, f"judge unavailable: {e}"[:300]
    return j.score, j.explanation


async def _judge_rubrics(
    ctx, contents: dict[str, str], caps: dict[str, str] | None = None
) -> dict[str, dict]:
    """One judge call per rubric: {rid: {score, explanation[, capped]}}.

    A rubric capped by a deterministic failure is still judged (its
    explanation is useful) but scores 0 (rubrics.apply_caps)."""
    caps = caps or {}
    out: dict[str, dict] = {}
    for rid, content in contents.items():
        score, why = await _judge(ctx, rubrics.rubric(rid), content)
        out[rid] = {"score": score, "explanation": why}
        if rid in caps:
            out[rid].update(score=0.0, capped=caps[rid])
    return out


def _scores(judged: dict[str, dict]) -> dict[str, float | None]:
    return {rid: j.get("score") for rid, j in judged.items()}


async def _canon_block(ctx: StepContext, book_id: str) -> str:
    """The book's canon exactly as the app renders it for its own prompts
    (Canon.full_block over GET /books, /characters and /canon), so the judge
    grades against the canon the model was given."""
    from app.planning.canon import Canon

    book = await ctx.client.get_book(book_id)
    listed = await ctx.client.list_characters(book_id)
    chars = [SimpleNamespace(**await ctx.client.get_character(c["id"])) for c in listed]
    canon = await ctx.client.get_canon(book_id)
    entries = [SimpleNamespace(**e) for e in canon.get("entries") or []]
    style = SimpleNamespace(**canon["style"]) if canon.get("style") else None
    return Canon(
        title=book.get("title") or "",
        genre=book.get("genre") or "",
        synopsis=book.get("synopsis") or "",
        characters=chars,
        canon_entries=entries,
        style=style,
    ).full_block()


def _style_text(style: dict | None) -> str:
    if not style:
        return "(no style guide)"
    return "\n".join(
        f"{k}: {v}" for k, v in style.items() if v and not str(k).startswith("_")
    )


def _forms(gt: dict) -> dict[str, list[str]]:
    """{principal: [name, aliases...]} from the corpus' fidelity block (else
    the first three cast names, first names as aliases)."""
    principals = (gt.get("fidelity") or {}).get("principals") or [
        {"name": n, "aliases": [n.split()[0]] if " " in n else []}
        for n in gt.get("cast", [])[:3]
    ]
    return {p["name"]: [p["name"], *p.get("aliases", [])] for p in principals}


def named_in(text: str, forms: list[str]) -> bool:
    """A name or any alias appears in `text` (word-bounded, normalised)."""
    from evals.graders.fidelity import _present
    from evals.graders.extraction import _norm

    hay = _norm(text)
    return any(_present(f, hay) for f in forms)


_QUOTE = re.compile(r"\"([^\"]+)\"|“([^”]+)”")


def strip_tags(text: str, names: list[str]) -> list[str]:
    """The scene's spoken lines with speaker tags removed: every quoted span,
    in order, with any cast name or alias inside it masked as "[name]".

    This is what the R2 judge attributes: no narration, so no "said Zara", no
    action beat naming the speaker, and no vocative giving the answer away."""
    forms = sorted({n for n in names if n}, key=len, reverse=True)
    lines = []
    for m in _QUOTE.finditer(text or ""):
        line = (m.group(1) or m.group(2) or "").strip()
        if not line:
            continue
        for f in forms:
            line = re.sub(rf"(?<!\w){re.escape(f)}(?!\w)", "[name]", line)
        lines.append(line)
    return lines


def _unknown_canon(text: str, gt: dict, extra: list[str]) -> tuple[float, list[str]]:
    """Name invention against everything the author gave the app: the corpus'
    known terms, the cast with aliases, AND every proper noun of the synopsis
    (a minor named in the synopsis, like Prava, is not invented)."""
    from evals.graders.fidelity import _PROPER, unknown_rate

    allow = set((gt.get("fidelity") or {}).get("known") or [])
    for forms in _forms(gt).values():
        allow.update(forms)
    allow.update(gt.get("cast") or [])
    allow.update(extra)
    allow.update(m.group(0) for m in _PROPER.finditer(gt.get("synopsis") or ""))
    # A capitalised word the text also uses in lower case ("We", "Below") is a
    # sentence start, not a name: prose has hundreds of them.
    lower = set(re.findall(r"\b[a-z][a-z']+\b", text or ""))
    allow.update(
        w for w in re.findall(r"\b[A-Z][a-z']+\b", text or "") if w.lower() in lower
    )
    allow.update(_SENTENCE_WORDS)
    return unknown_rate(text, allow)


# Capitalised at a sentence start far more often than as a name.
_SENTENCE_WORDS = {
    "I",
    "We",
    "You",
    "Yes",
    "No",
    "Not",
    "What",
    "Why",
    "How",
    "Who",
    "Where",
    "If",
    "So",
    "Then",
    "Still",
    "Nothing",
    "Something",
    "Everyone",
    "Nobody",
}


# --- Step 1: character extraction -----------------------------------------
# Extraction PROPOSES canon for the author to review (docs/BRD.md R4.4), so the
# thing to grade is the proposal set; the step then approves everything (as an
# "approve all" author would) and checks the commit wrote exactly what was
# proposed — the reviewed-commit contract.
@step("extraction", needs_api=True)
async def step_extraction(ctx: StepContext) -> dict:
    up = await ctx.client.upload_source(
        f"{ctx.book}.txt", ctx.corpus_text.encode("utf-8"), title=f"eval-{ctx.book}"
    )
    run = await ctx.client.wait_extraction(up["book_id"], up["extraction_run_id"])
    if run.get("status") != "ready":
        return {
            "error": f"extraction {run.get('status')}: {run.get('error') or ''}",
            "score": 0.0,
        }
    proposals = run.get("proposals") or {}
    chars = proposed_characters(run)
    predicted = [c["name"] for c in chars]
    score = extraction.grade_extraction(predicted, ctx.ground_truth["cast"])
    # The review's default selection (SPEC-09: one-window names pre-deselected,
    # eval-plan E5) — what an author who accepts the defaults commits.
    selected = [c["name"] for c in chars if c.get("selected", True)]
    sel = extraction.grade_extraction(selected, ctx.ground_truth["cast"])
    await ctx.client.commit_extraction(
        up["book_id"], up["extraction_run_id"], proposals
    )
    committed = sorted(c["name"] for c in await ctx.client.source_characters(up["id"]))
    return {
        "predicted": predicted,
        **score.as_dict(),
        "score": score.f1,
        "selected": selected,
        "f1_selected": round(sel.f1, 4),
        "precision_selected": round(sel.precision, 4),
        "recall_selected": round(sel.recall, 4),
        "windows": (proposals.get("stats") or {}).get("windows"),
        "commit_faithful": committed == sorted(set(predicted)),
    }


# --- Step 1b: ingestion voice quality (closes the manuscript→voice gap) -----
# attribution/prose seed voice via the voice-samples API, bypassing the
# manuscript chunking — so nothing measured whether an UPLOAD actually produces
# usable per-character voice. This drives the whole author path: upload →
# proposals → approve all → voice indexing job → per-character chunk counts.
_MIN_USABLE_CHUNKS = 3


@step("ingestion", needs_api=True)
async def step_ingestion(ctx: StepContext) -> dict:
    # Append a marker so the content hash differs from the extraction step's
    # upload of the same corpus. Doesn't affect the chunking measured here.
    content = (ctx.corpus_text + "\n\n[[eval-ingestion-copy]]\n").encode("utf-8")
    up, _run = await ctx.client.extract_and_commit(
        f"{ctx.book}-ing.txt", content, f"eval-ing-{ctx.book}"
    )
    chars = await ctx.client.wait_voice_indexed(up["id"])
    if not chars:
        return {"score": 0.0, "reason": "no characters committed", "n_characters": 0}

    detail, grounded, total_chunks = [], 0, 0
    for c in chars:
        stats = (await ctx.client.get_character(c["id"])).get("voice_stats") or {}
        n = stats.get("total_chunks", 0)
        total_chunks += n
        grounded += n >= _MIN_USABLE_CHUNKS
        detail.append({"name": c["name"], "chunks": n})
    # score = fraction of the committed cast that got a usable indexed voice.
    return {
        "score": round(grounded / len(chars), 4),
        "grounded": grounded,
        "n_characters": len(chars),
        "avg_chunks": round(total_chunks / len(chars), 2),
        "detail": detail,
    }


# --- Step 2: retrieval / voice separability (in-harness embeddings) --------
@step("retrieval", needs_api=False)
async def step_retrieval(ctx: StepContext) -> dict:
    gold = ctx.ground_truth.get("gold_lines") or {}
    if not gold:
        return {"skipped": True, "reason": "corpus has no gold voice lines"}
    pool_texts, pool_owner = [], []
    for char, v in gold.items():
        for ln in v["train"]:
            pool_texts.append(ln)
            pool_owner.append(char)
    pool_vecs = await embed.embed_many(pool_texts)

    queries = [(c, ln) for c, v in gold.items() for ln in v["test"]]
    query_vecs = await embed.embed_many([q for _, q in queries])
    owner_by_text = dict(zip(pool_texts, pool_owner))
    qvec_by_text = {q: v for (_, q), v in zip(queries, query_vecs)}

    def retrieve_fn(character, query, k):
        qv = qvec_by_text[query]
        ranked = sorted(
            range(len(pool_texts)), key=lambda i: cosine(qv, pool_vecs[i]), reverse=True
        )
        return [pool_texts[i] for i in ranked[:k]]

    score = retrieval.grade_retrieval(
        queries, retrieve_fn, lambda t: owner_by_text[t], k=3
    )
    return {**score.as_dict(), "score": score.precision_at_k, "characters": list(gold)}


# --- Step 3: voice attribution (real generation + embedding) ---------------
@step("attribution", needs_api=True)
async def step_attribution(ctx: StepContext) -> dict:
    gold = ctx.ground_truth.get("gold_lines") or {}
    if not gold:
        return {"skipped": True, "reason": "corpus has no gold voice lines"}
    # reference centroids from held-out TEST lines (never the indexed train lines).
    ref_vecs = {c: await embed.embed_many(v["test"]) for c, v in gold.items()}

    book = await ctx.client.create_book(
        title=f"eval-attr-{ctx.book}", synopsis=ctx.ground_truth.get("synopsis", "")
    )
    char_ids = {}
    for char, v in gold.items():
        created = await ctx.client.create_character(
            char, book["id"], description=f"Narrator: {char}"
        )
        await ctx.client.add_voice_samples(
            created["id"], v["train"], chunk_type="dialogue"
        )
        char_ids[char] = created["id"]

    results = []
    empty = 0
    for char in gold:

        async def gen(_char=char):
            r = await ctx.client.test_dialogue(char_ids[_char], _KNOCK)
            return r.get("dialogue") or r.get("action") or ""

        line = await ctx.cache.memo("attribution", f"{char}|{_KNOCK}", gen)
        if not line:
            empty += 1
            continue
        results.append(
            attribution.attribute(char, await embed.embed_one(line), ref_vecs)
        )

    agg = attribution.accuracy(results)
    # Surface how many voices actually produced a line: accuracy=0 with
    # generated=0 means generation failed/blocked (not a voice-quality signal);
    # accuracy=0 with generated=N means real misattribution (homogenized voice).
    return {
        **agg,
        "score": agg["accuracy"],
        "generated": len(results),
        "empty": empty,
        "detail": [r.as_dict() for r in results],
    }


# --- Step 4: outline / plot coherence + fidelity ---------------------------
# The book carries the corpus synopsis AND cast (principals marked) AND style,
# so the app's own principal-cast gate is live, and the score is gated on
# fidelity: a beautiful outline about the wrong story scores ~0 (docs/BRD.md
# R1.4, §7). A corpus with `premise_edit` runs the author-edited-premise path:
# the outline stops at the premise checkpoint and is confirmed with the edit.
# Judged on R1 (cast), R6 (structure + premise) and R3 (beats vs chapters):
# score = R1 x mean(R6, R3), R1 capped at 0 when a principal is missing.
def outline_text(nodes: list[dict]) -> str:
    """The outline as the judge reads it: chapter, act, summary, beats."""
    out = []
    for i, n in enumerate(nodes):
        act = n.get("act_index")
        act_txt = f" [act {act + 1}]" if isinstance(act, int) else ""
        out.append(f"{i + 1}. {n.get('title')}{act_txt}: {n.get('summary', '')}")
        for c in n.get("children", []) or []:
            out.append(f"   - {c.get('title')}: {c.get('summary', '')}")
    return "\n".join(out)


def premise_text(premise: dict | None, edit: dict | None = None) -> str:
    """The confirmed premise as plain lines (the author's edit wins). Plain
    text, not the JSON keys: the judge reads it, and so does the e2e stub,
    which would take a "premise_restated" key for a skeleton request."""
    p = {**(premise or {}), **(edit or {})}
    lines = []
    if p.get("premise_restated"):
        lines.append(f"Premise: {p['premise_restated']}")
    if p.get("central_conflict"):
        lines.append(f"Central conflict: {p['central_conflict']}")
    for i, act in enumerate(p.get("acts") or []):
        if isinstance(act, dict):
            lines.append(
                f"Act {i + 1}: {act.get('title', '')}. Goal: {act.get('goal', '')}."
                f" Turn: {act.get('turn', '')}."
            )
    return "\n".join(lines) or "(none)"


async def _generate_outline(ctx: StepContext, book: dict) -> dict:
    """Generate the book's outline through the product (with the author's
    premise edit when the corpus has one). `portable` is the content with the
    structural cast as NAMES, so it can be stored into another book of the
    same cast (PUT /plans resolves names; this book's ids would 400)."""
    gt = ctx.ground_truth
    plan = await ctx.client.generate_outline(
        book["id"],
        chapters_target=len(gt["canonical_beats"]),
        premise=gt.get("premise_edit"),
    )
    content = plan.get("content", [])
    return {
        "content": content,
        "portable": portable_outline(content, book.get("character_ids") or {}),
        "status": plan.get("status"),
        "warnings": plan.get("warnings", []),
        "premise": plan.get("premise"),
    }


def portable_outline(nodes: list, character_ids: dict[str, str]) -> list:
    """Outline nodes with `character_ids` swapped for `characters` names (ids
    the map does not know are dropped)."""
    names = {cid: name for name, cid in character_ids.items()}
    out = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        node = dict(node)
        ids = node.pop("character_ids", None)
        if isinstance(ids, list):
            node["characters"] = [names[c] for c in ids if c in names]
        out.append(node)
    return out


@step("outline", needs_api=True)
async def step_outline(ctx: StepContext) -> dict:
    from evals.graders.fidelity import flatten_outline, grade_fidelity

    gt = ctx.ground_truth
    book = await _book_with_cast(ctx, f"eval-{gt['book']}", genre="")
    edit = gt.get("premise_edit")
    chapters_target = len(gt["canonical_beats"])

    out = await ctx.cache.memo(
        "outline", gt["synopsis"], lambda: _generate_outline(ctx, book)
    )
    if not isinstance(out, dict):  # back-compat: a bare content list
        out = {"content": out or []}
    nodes = out.get("content") or []
    structural = bool(nodes) and all(n.get("title") for n in nodes)
    result: dict = {
        "n_nodes": len(nodes),
        "chapters_target": chapters_target,
        "structural_ok": structural,
        "plan_status": out.get("status"),
        "app_warnings": out.get("warnings"),
        "premise_edited": bool(edit),
    }

    caps: dict[str, str] = {}
    fid = gt.get("fidelity")
    if fid:
        fs = grade_fidelity(nodes, fid["principals"], set(fid["known"]))
        result["cast_fidelity"] = fs.principal_recall
        result["unknown_rate"] = fs.unknown_rate
        result["missing_principals"] = fs.missing
        if fs.principal_recall < 1.0:
            caps["R1"] = f"principal(s) missing: {', '.join(fs.missing)}"
    # Name invention against canon + synopsis: what the app's warning list
    # should be measured by (a synopsis minor is not an invention).
    rate, unknown = _unknown_canon(flatten_outline(nodes), gt, [])
    result["unknown_rate_canon"] = rate
    result["unknown_canon"] = unknown

    canon = await _canon_block(ctx, book["id"])
    body = (
        f"CANON (what the author gave the app):\n{canon}\n\n"
        f"CONFIRMED PREMISE{' (edited by the author)' if edit else ''}:\n"
        f"{premise_text(out.get('premise'), edit)}"
        f"\n\nCHAPTERS REQUESTED: {chapters_target}\n\n"
        f"OUTLINE TO SCORE:\n{outline_text(nodes) or '(empty outline)'}"
    )
    judged = await _judge_rubrics(
        ctx, {rid: body for rid in rubrics.OUTLINE_RUBRICS}, caps
    )
    result["rubrics"] = judged
    result["deterministic_failures"] = caps
    score = rubrics.outline_score(_scores(judged))
    judged_vals = [j["score"] for j in judged.values() if j["score"] is not None]
    # `judge` keeps its old meaning (the judge's view, before the R1 product).
    result["judge"] = (
        round(sum(judged_vals) / len(judged_vals), 4) if judged_vals else None
    )
    result["judge_explanation"] = " | ".join(
        f"{rid}: {j['explanation']}" for rid, j in judged.items()
    )[:2000]
    result["score"] = None if score is None else score if structural else 0.0
    return result


# --- Step 5: continuity detection (constructed ground truth) ---------------
# The prose fed to the checker is truncated to this window, so injections MUST be
# applied over the SAME window (not the full text) or a mutation past the cut is
# counted in the denominator yet never seen — pinning recall at 0. Sized to hold
# each corpus's injection anchors AND a second occurrence for the contradiction.
# A corpus whose synopsis leads its text (cel) plants its contradictions in the
# excerpt (`continuity_source: "excerpts"`): a synopsis is not prose to check.
_CONTINUITY_WINDOW = 12000


def _finding_quote(finding: dict) -> str:
    """The text a finding quotes: an explicit `quote`, else `refs` with any
    surrounding quotation marks stripped."""
    q = finding.get("quote") or finding.get("refs") or ""
    return str(q).strip().strip("\"'“”‘’").strip()


def quote_verbatim_rate(findings: list[dict], prose: str) -> float | None:
    """Fraction of findings whose quote appears verbatim in the checked prose
    (whitespace-normalised); None when no finding quotes anything."""
    hay = " ".join(prose.split())
    quoted = [q for q in (_finding_quote(f) for f in findings) if len(q) >= 8]
    if not quoted:
        return None
    return round(sum(" ".join(q.split()) in hay for q in quoted) / len(quoted), 4)


@step("continuity", needs_api=True)
async def step_continuity(ctx: StepContext) -> dict:
    gt = ctx.ground_truth
    if not gt.get("continuity_injections"):
        return {"skipped": True, "reason": "corpus has no continuity injections"}
    source = (
        load_excerpts(ctx.book)
        if gt.get("continuity_source") == "excerpts"
        else ctx.corpus_text
    )
    window = source[:_CONTINUITY_WINDOW]
    injected_text, expected = continuity.apply_injections(
        window, gt["continuity_injections"]
    )

    async def run_on(prose: str, tag: str) -> list[dict]:
        book = await ctx.client.create_book(
            title=f"eval-cont-{tag}", synopsis="continuity eval"
        )
        chapter = await ctx.client.create_chapter(book["id"], "Chapter", summary="eval")
        # Scaffold the scene with our injected/control prose directly — no
        # generation. Generating a placeholder scene only to overwrite it burned
        # ~4-6 LLM calls PER continuity run (x2 for injected+control) against the
        # free-tier daily budget, for prose we immediately discard.
        await ctx.client.create_scene(
            chapter["id"],
            content=prose[:_CONTINUITY_WINDOW],
            characters=[gt["cast"][0]],
        )
        return await ctx.client.run_continuity(book["id"], chapter["id"])

    injected = await run_on(injected_text, "injected")
    control = await run_on(window, "control")
    score = continuity.grade_continuity(expected, injected, control)
    # Precision on the injected run: findings that point at a planted error.
    on_target = sum(
        1 for f in injected if any(continuity._finding_matches(e, f) for e in expected)
    )
    return {
        **score.as_dict(),
        "score": score.detection_recall,
        "source": gt.get("continuity_source") or "corpus",
        "n_findings": len(injected),
        "precision": round(on_target / len(injected), 4) if injected else None,
        "quote_verbatim": quote_verbatim_rate(injected, injected_text),
    }


# --- Step 6: end-to-end prose through the product path (judge) ---------------
# What an author does: outline (memoised from the outline step) -> confirm the
# premise -> promote -> generate the planned scenes of the first chapter that
# casts at least two principals. Each scene is checked deterministically (cast
# recall caps R1; a repeated 6-gram across a beat or scene seam caps R4) and
# judged on R1-R5 with the canon, style guide, beats and prior text in view:
# scene score = R1 x mean(R2..R5). When the outline gives no such chapter, one
# scene is written from the corpus' second canonical beat instead.
_PRIOR_WORDS = 250


def _judged_scene_cap() -> int:
    """How many scenes the judge grades (5 calls each); the rest get the
    deterministic checks only. EVAL_PROSE_JUDGE_SCENES, default 3."""
    try:
        return max(0, int(os.getenv("EVAL_PROSE_JUDGE_SCENES", "3")))
    except ValueError:
        return 3


async def _collect_scene(ctx: StepContext, scene_id: str) -> dict:
    from evals.harness.config import load as load_config

    scene = await ctx.client.get_scene(scene_id)
    beats, source = await ctx.client.scene_beats(scene_id, load_config().database_url)
    return {
        "id": scene_id,
        "status": scene.get("status"),
        "content": scene.get("content") or "",
        "characters": scene.get("characters") or [],
        "description": (scene.get("scene_request") or {}).get("scene_description"),
        "beats": beats,
        "beats_source": source,
    }


async def _outline_into(ctx: StepContext, book: dict) -> list:
    """The book's outline: the outline step's (memoised) result stored into
    this book, or generated here when the cache has none."""
    generated_here = False

    async def gen():
        nonlocal generated_here
        out = await _generate_outline(ctx, book)
        generated_here = True
        return out

    out = await ctx.cache.memo("outline", ctx.ground_truth["synopsis"], gen)
    if not isinstance(out, dict):  # back-compat: a bare content list
        out = {"content": out or []}
    content = out.get("content") or []
    if content and not generated_here:
        # An older cache entry has no `portable`: drop its foreign ids.
        await ctx.client.put_outline(
            book["id"], out.get("portable") or portable_outline(content, {})
        )
    return content


async def _planned_scenes(ctx: StepContext, book: dict) -> dict:
    """Generate the planned scenes of the first promoted chapter that casts
    two principals. Raises EvalClientError when the product path is not
    available (no outline, nothing to promote, no such chapter)."""
    from evals.harness.client import EvalClientError

    forms = _forms(ctx.ground_truth)
    names = {cid: name for name, cid in book["character_ids"].items()}
    if not await _outline_into(ctx, book):
        raise EvalClientError("no outline to promote")
    promoted = await ctx.client.promote_all(book["id"])
    chapters = sorted(
        promoted.get("chapters") or [], key=lambda c: c.get("position") or 0
    )
    tried = []
    for ch in chapters:
        detail = await ctx.client.get_chapter(ch["chapter_id"])
        cast = [names[c] for c in detail.get("character_ids") or [] if c in names]
        if sum(1 for n in cast if n in forms) < 2:
            continue
        try:
            started = await ctx.client.generate_planned(ch["chapter_id"], {})
        except EvalClientError as e:  # e.g. 422: the chapter has no planned beats
            tried.append(f"{detail.get('title')}: {e}"[:200])
            continue
        scenes = [await _collect_scene(ctx, s["id"]) for s in started["scenes"]]
        return {
            "path": "planned",
            "chapter": {
                "id": ch["chapter_id"],
                "title": detail.get("title"),
                "summary": detail.get("summary"),
                "cast": cast,
            },
            "total": started.get("total"),
            "scenes": scenes,
        }
    raise EvalClientError(
        f"no promoted chapter casts two principals with planned beats "
        f"({len(chapters)} chapters; tried: {tried})"
    )


async def _fallback_scene(ctx: StepContext, book: dict, why: str) -> dict:
    """One scene from canonical beat 2, set where the corpus says it happens."""
    gt = ctx.ground_truth
    beat = gt["canonical_beats"][min(1, len(gt["canonical_beats"]) - 1)]["title"]
    cast = [n for n in _forms(gt) if n in book["character_ids"]][:2] or list(
        book["character_ids"]
    )[:2]
    chapter = await ctx.client.create_chapter(book["id"], beat, summary=beat)
    scene = await ctx.client.generate_scene(
        chapter["id"],
        {
            "character_ids": [book["character_ids"][c] for c in cast],
            "scene_description": beat,
            "setting": _FALLBACK_SETTINGS.get(ctx.book, ""),
            "emotional_tone": "tense",
            "target_word_count": 600,
        },
    )
    return {
        "path": "fallback",
        "fallback_reason": why[:300],
        "chapter": {"id": chapter["id"], "title": beat, "summary": beat, "cast": cast},
        "scenes": [await _collect_scene(ctx, scene["id"])],
    }


def _beats_text(scene: dict) -> str:
    rows = [b for b in scene.get("beats") or [] if b.get("description")]
    if rows:
        return "\n".join(f"{i + 1}. {b['description']}" for i, b in enumerate(rows))
    return f"1. {scene.get('description') or '(no beats recorded)'}"


def _voice_block(gt: dict, cast: list[str]) -> str:
    gold = gt.get("gold_lines") or {}
    parts = []
    for name in cast:
        lines = [*(gold.get(name) or {}).get("train", [])]
        lines += (gold.get(name) or {}).get("test", [])
        if lines:
            parts.append(f"{name}:\n" + "\n".join(f'- "{ln}"' for ln in lines))
    return "\n\n".join(parts) or "(the corpus has no voice samples)"


def _grade_scene_checks(scene: dict, prev_text: str, forms: dict) -> dict:
    """Deterministic pre-checks: {caps, cast_recall, seams...}."""
    from evals.graders import seams

    text = scene["content"]
    cast = [n for n in scene.get("characters") or [] if n in forms]
    present = [n for n in cast if named_in(text, forms[n])]
    # "Appears where the plan says" (R1): a principal the scene's own plan
    # (brief + beats) names must be on the page. A scene inherits its whole
    # chapter's cast, so a cast member the plan never mentions is not required;
    # but a scene with a cast and none of it on the page fails.
    plan = " ".join(
        [scene.get("description") or ""]
        + [b.get("description") or "" for b in scene.get("beats") or []]
    )
    required = [n for n in cast if named_in(plan, forms[n])]
    missing = [n for n in required if n not in present]
    caps: dict[str, str] = {}
    if missing:
        caps["R1"] = f"planned principal(s) absent from the prose: {', '.join(missing)}"
    elif cast and not present:
        caps["R1"] = "none of the scene's cast is on the page"
    contents = [b["content"] for b in scene.get("beats") or [] if b.get("content")]
    beat_seams = seams.seam_report(contents)
    scene_seam = len(seams.shared(prev_text, text)) if prev_text else 0
    if beat_seams["max_overlap"]:
        caps["R4"] = f"repeated text across a beat seam ({beat_seams['max_overlap']})"
    elif scene_seam:
        caps["R4"] = f"repeated text across the scene seam ({scene_seam})"
    return {
        "caps": caps,
        "cast": cast,
        "cast_recall": round(len(present) / len(cast), 4) if cast else None,
        "beat_seams": beat_seams,
        "scene_seam_overlap": scene_seam,
    }


@step("prose", needs_api=True)
async def step_prose(ctx: StepContext) -> dict:
    from evals.harness.client import EvalClientError

    gt = ctx.ground_truth
    forms = _forms(gt)
    book = await _book_with_cast(ctx, f"eval-prose-{gt['book']}")

    async def gen():
        # Voice indexing costs an extraction run, so only a real generation
        # pays for it (a cache hit re-grades without uploading).
        voice = (
            await _index_voice(ctx, book["id"], list(book["character_ids"]))
            if gt.get("gold_lines")
            else None
        )
        try:
            out = await _planned_scenes(ctx, book)
        except EvalClientError as e:
            out = await _fallback_scene(ctx, book, str(e))
        return {**out, "voice": voice}

    produced = await ctx.cache.memo("prose", f"{gt['book']}|planned-v2", gen)
    scenes = [s for s in produced.get("scenes") or [] if s.get("content", "").strip()]
    result: dict = {
        "path": produced.get("path"),
        "fallback_reason": produced.get("fallback_reason"),
        "chapter": (produced.get("chapter") or {}).get("title"),
        "voice": produced.get("voice"),
        "n_scenes": len(produced.get("scenes") or []),
        "scene_status": [s.get("status") for s in produced.get("scenes") or []],
    }
    if not scenes:
        # Generation failed/blocked: score 0 rather than crash or judge "(empty)".
        return {
            **result,
            "score": 0.0,
            "judge_explanation": "no prose generated (terminal status="
            f"{result['scene_status']}: failed, blocked, or quota-exhausted)",
            "words": 0,
            "cast_named": 0.0,
        }

    canon = await _canon_block(ctx, book["id"])
    style = _style_text(book.get("style"))
    chapter = produced.get("chapter") or {}
    alias_forms = [f for fs in forms.values() for f in fs]
    detail, prev_text = [], ""
    for i, sc in enumerate(scenes):
        checks = _grade_scene_checks(sc, prev_text, forms)
        rate, unknown = _unknown_canon(sc["content"], gt, [])
        row = {
            "scene_id": sc["id"],
            "words": len(sc["content"].split()),
            "beats_source": sc.get("beats_source"),
            "n_beats": len(sc.get("beats") or []),
            "cast": checks["cast"],
            "cast_recall": checks["cast_recall"],
            "beat_seam_max_overlap": checks["beat_seams"]["max_overlap"],
            "beat_seams_dirty": checks["beat_seams"]["dirty_seams"],
            "scene_seam_overlap": checks["scene_seam_overlap"],
            "unknown_rate_canon": rate,
            "unknown_canon": unknown,
            "deterministic_failures": checks["caps"],
        }
        if i < _judged_scene_cap():
            prior = " ".join(prev_text.split()[-_PRIOR_WORDS:]) or "(the scene opens)"
            base = (
                f"CANON:\n{canon}\n\nSTYLE GUIDE:\n{style}\n\n"
                f"CHAPTER: {chapter.get('title')}\n{chapter.get('summary') or ''}\n\n"
                f"SCENE BRIEF: {sc.get('description') or ''}\n"
                f"CHARACTERS IN THE SCENE: {', '.join(sc.get('characters') or [])}\n\n"
                f"PLANNED BEATS (in order):\n{_beats_text(sc)}\n\n"
                f"PRIOR TEXT (end of the previous scene):\n{prior}\n\n"
                f"SCENE TO SCORE:\n{sc['content']}"
            )
            lines = strip_tags(sc["content"], alias_forms)
            voice = (
                f"VOICE SAMPLES (each character's own lines from the book):\n"
                f"{_voice_block(gt, sc.get('characters') or [])}\n\n"
                f"CHARACTERS IN THE SCENE: {', '.join(sc.get('characters') or [])}\n\n"
                "SPOKEN LINES (speaker tags and names removed; attribute each):\n"
                + (
                    "\n".join(f"{n + 1}. {ln}" for n, ln in enumerate(lines))
                    or "(no dialogue)"
                )
            )
            contents = {rid: base for rid in rubrics.SCENE_RUBRICS}
            contents["R2"] = voice
            judged = await _judge_rubrics(ctx, contents, checks["caps"])
            row["rubrics"] = judged
            row["score"] = rubrics.scene_score(_scores(judged))
        detail.append(row)
        prev_text = sc["content"]

    prose = "\n\n".join(s["content"] for s in scenes)
    cast = chapter.get("cast") or list(forms)[:2]
    principals = [n for n in cast if n in forms] or list(forms)[:2]
    named = [n for n in principals if named_in(prose, forms.get(n, [n]))]
    scored = [r["score"] for r in detail if r.get("score") is not None]
    means = {}
    for rid in rubrics.SCENE_RUBRICS:
        vals = [
            r["rubrics"][rid]["score"]
            for r in detail
            if r.get("rubrics") and r["rubrics"][rid]["score"] is not None
        ]
        means[rid] = round(sum(vals) / len(vals), 4) if vals else None
    explanations = [
        f"scene {n + 1} {rid}: {j['explanation']}"
        for n, r in enumerate(detail)
        for rid, j in (r.get("rubrics") or {}).items()
    ]
    return {
        **result,
        "score": round(sum(scored) / len(scored), 4) if scored else None,
        "rubric_means": means,
        "words": len(prose.split()),
        "cast_named": round(len(named) / max(1, len(principals)), 4),
        "beat_seam_max_overlap": max(r["beat_seam_max_overlap"] for r in detail),
        "scene_seam_max_overlap": max(r["scene_seam_overlap"] for r in detail),
        "beats_source": sorted({str(r["beats_source"]) for r in detail}),
        "scenes": detail,
        "judge_explanation": " | ".join(explanations)[:4000],
    }


# --- Step 7: ensemble vs prose (SPEC-14) -------------------------------------
# The same two-character scene in both modes, graded on deterministic action
# coverage (every present character visibly acts) and the judge. Reports the
# delta, which is what decides whether the ensemble's cost is worth gating on
# the paid tier (docs/BRD.md §6.1). Skips when the tier runs ensemble as prose.
@step("ensemble", needs_api=True)
async def step_ensemble(ctx: StepContext) -> dict:
    from evals.graders.ensemble import action_coverage

    gt = ctx.ground_truth
    cast = (list(gt.get("gold_lines") or {}) or list(gt.get("cast", [])))[:2]
    book = await _book_with_cast(ctx, f"eval-ens-{gt['book']}")
    cast = [c for c in cast if c in book["character_ids"]] or list(
        book["character_ids"]
    )[:2]
    chapter = await ctx.client.create_chapter(
        book["id"], "An encounter", summary="two of the leads meet and disagree"
    )
    base = {
        "character_ids": [book["character_ids"][c] for c in cast],
        "scene_description": "The two meet at night and argue about whether to press on.",
        "setting": "a fog-bound street",
        "emotional_tone": "tense",
        "target_word_count": 400,
    }
    rubric = (
        "Judge this scene: do BOTH named characters visibly act (not only speak), "
        "and do their voices read as distinct? Characters: " + ", ".join(cast)
    )
    out: dict = {"characters": cast}
    for mode in ("prose", "ensemble"):
        scene = await ctx.client.generate_scene(chapter["id"], {**base, "mode": mode})
        if mode == "ensemble" and scene.get("mode_used") != "ensemble":
            return {
                "skipped": True,
                "reason": "the tier ran ensemble as prose (set LLM_TIER=paid)",
            }
        prose = scene.get("content") or ""
        judge, why = await _judge(ctx, rubric, prose or "(empty)")
        out[mode] = {
            "action_coverage": action_coverage(prose, cast),
            "judge": judge,
            "words": len(prose.split()),
        }
        if judge is None:
            out["judge_explanation"] = why
    out["action_coverage_delta"] = round(
        out["ensemble"]["action_coverage"] - out["prose"]["action_coverage"], 4
    )
    judged = out["ensemble"]["judge"] is not None and out["prose"]["judge"] is not None
    # Without a judge the deterministic action-coverage metrics still report.
    out["judge_delta"] = (
        round(out["ensemble"]["judge"] - out["prose"]["judge"], 4) if judged else None
    )
    out["score"] = (
        out["ensemble"]["action_coverage"] * out["ensemble"]["judge"]
        if judged
        else None
    )
    return out


# --- Prompt contract over the relay archive ------------------------------------
# Deterministic checks of what the app SENT the model during this run (the
# relay's answered/ archive): beats block, prior paragraphs, dialogue-only voice
# samples, beats-stage plan, style precedence. Independent of model variance,
# so it is the strongest before/after signal. Not a registered step: it grades
# the whole run, so `evals.run` adds it after every pass when
# EVAL_RELAY_ARCHIVE / EVAL_RELAY_DIR is set (reported as steps.prompt_contract).
async def prompt_contract_result(ctx: StepContext) -> dict:
    from evals.graders import prompt_contract
    from evals.harness.config import load as load_config

    archive = load_config().relay_archive
    if not archive:
        return {
            "skipped": True,
            "reason": "no relay archive (set EVAL_RELAY_ARCHIVE or EVAL_RELAY_DIR)",
        }
    records = prompt_contract.load_archive(
        archive, since=ctx.__dict__.get("run_started")
    )
    if not records:
        return {"skipped": True, "reason": f"no exchanges archived in {archive}"}
    gt = ctx.ground_truth
    return {
        "archive": archive,
        **prompt_contract.grade(
            records,
            gold_lines=gt.get("gold_lines"),
            other_lines=gt.get("other_lines"),
            sample_prose=(load_style(ctx.book) or {}).get("sample_prose") or "",
            premise_edit=gt.get("premise_edit"),
        ),
    }
