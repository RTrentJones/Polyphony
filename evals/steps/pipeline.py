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

from app.parsing.names import match
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
            # A named facet, as the extraction schema and an author's
            # KeyValueEditor use. {"voice": ...} rendered "Voice: voice: ..."
            # in every canon block (F7).
            payload["voice_characteristics"] = {"diction": entry["voice"]}
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

    A corpus whose prose lives mostly in `synopsis.txt` (cel: a ~4.7k-char
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


_CAPITALISED_RUN = re.compile(r"[A-Z][\w'’-]*\.?(?:[ \t]+[A-Z][\w'’-]*\.?)*")


def named_in(text: str, forms: list[str]) -> bool:
    """A capitalised run of `text` ("Ms. Okafor", "Zara's") names one of
    `forms` by `app.parsing.names.match` (honorifics and surnames count: S4)."""
    for m in _CAPITALISED_RUN.finditer(text or ""):
        run = re.sub(r"['’]s\b", "", m.group(0))
        if any(match(run, f) for f in forms if f):
            return True
    return False


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


def _unknown_canon(text: str, gt: dict, context: str = "") -> tuple[float, list[str]]:
    """Name invention measured as the product warns about it (W1-S7):
    `fidelity.unknown_proper_nouns` with the corpus' known terms and cast as
    `known` and the synopsis (plus any `context`, e.g. a revised scene's
    draft) as `canon_text`, so a synopsis minor like Prava is not invented.
    Returns (unknown / distinct proper nouns, unknown)."""
    from app.planning.fidelity import proper_nouns, unknown_proper_nouns

    known = set((gt.get("fidelity") or {}).get("known") or [])
    for forms in _forms(gt).values():
        known.update(forms)
    known.update(gt.get("cast") or [])
    canon_text = f"{gt.get('synopsis') or ''}\n{context}"
    unknown = unknown_proper_nouns(text or "", known, canon_text=canon_text)
    found = proper_nouns(text or "")
    return (round(len(unknown) / len(found), 4) if found else 0.0), unknown


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
        **extraction_depth(chars, _forms(ctx.ground_truth)),
    }


_ENTRY_FIELDS = ("description", "goals", "voice_characteristics")


def _filled(value) -> bool:
    if isinstance(value, dict):
        return any(str(v).strip() for v in value.values())
    if isinstance(value, list):
        return any(str(v).strip() for v in value)
    return bool(str(value or "").strip())


def _proposal_for(chars: list[dict], forms: list[str]) -> dict | None:
    """The proposal whose name or an alias is one of a principal's forms."""
    wanted = {f.lower() for f in forms}
    for c in chars:
        names = [c.get("name") or "", *(c.get("aliases") or [])]
        if any(str(n).strip().lower() in wanted for n in names):
            return c
    return None


def extraction_depth(chars: list[dict], forms: dict[str, list[str]]) -> dict:
    """§5.2 extraction checks (F1): every principal's proposal has a
    description, goals and voice; and mentions rank them in the corpus'
    principal order (cel: Milo >= Zara >= Edric).

    `principal_fields` is "filled/principals"; a principal with no proposal
    counts as unfilled. `mention_order_ok` is None when a principal is
    missing (nothing to order)."""
    filled, missing_fields, mentions = 0, {}, {}
    for name, fs in forms.items():
        c = _proposal_for(chars, fs)
        if c is None:
            missing_fields[name] = list(_ENTRY_FIELDS)
            continue
        empty = [f for f in _ENTRY_FIELDS if not _filled(c.get(f))]
        if empty:
            missing_fields[name] = empty
        else:
            filled += 1
        mentions[name] = int(c.get("mentions") or 0)
    order = list(forms)
    ok = None
    if len(mentions) == len(order):
        ok = all(mentions[a] >= mentions[b] for a, b in zip(order, order[1:]))
    # Finding 4: a principal with a title alias ("Chief Executive Lich") is
    # proposed under their name, not the title. Reported, not gated.
    titled = [n for n, fs in forms.items() if any(not match(f, n) for f in fs[1:])]
    canonical = [
        n
        for n in titled
        if match((_proposal_for(chars, forms[n]) or {}).get("name") or "", n)
    ]
    return {
        "canonical_names_ok": f"{len(canonical)}/{len(titled)}" if titled else None,
        "principal_fields": f"{filled}/{len(forms)}",
        "principal_fields_rate": round(filled / len(forms), 4) if forms else None,
        "principal_fields_missing": missing_fields,
        "principal_mentions": mentions,
        "mention_order_ok": ok,
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


def outline_secondaries(nodes: list, secondaries: list[str]) -> dict:
    """§5.2 `outline_names_synopsis_secondaries` (F5): which of the corpus'
    synopsis-only people the outline names, in its text or in any node's
    `secondary_names`. Target: all of them; the floor is one."""
    from app.planning.fidelity import flatten_outline

    titles, body = flatten_outline(nodes or [])
    hay = "\n".join([*titles, *body])
    named = [s for s in secondaries if named_in(hay, [s])]
    return {
        "outline_names_synopsis_secondaries": (
            f"{len(named)}/{len(secondaries)}" if secondaries else None
        ),
        "outline_secondaries_named": named,
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
        # Beat children carry their own cast since wave 2 (SPEC-24).
        if isinstance(node.get("children"), list):
            node["children"] = portable_outline(node["children"], character_ids)
        out.append(node)
    return out


@step("outline", needs_api=True)
async def step_outline(ctx: StepContext) -> dict:
    from app.planning.fidelity import flatten_outline
    from evals.graders.fidelity import grade_fidelity

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
    # should be measured by (a synopsis minor is not an invention). Titles
    # are left out, as the app audit leaves them out.
    rate, unknown = _unknown_canon("  ".join(flatten_outline(nodes)[1]), gt)
    result["unknown_rate_canon"] = rate
    result["unknown_canon"] = unknown
    result.update(outline_secondaries(nodes, (fid or {}).get("secondaries") or []))

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
    scene = await ctx.client.get_scene(scene_id)
    req = scene.get("scene_request") or {}
    return {
        "id": scene_id,
        "status": scene.get("status"),
        "content": scene.get("content") or "",
        "characters": scene.get("characters") or [],
        "description": req.get("scene_description"),
        # The planned beat the scene was written from (SPEC-23/24 request keys).
        "planned_beat_id": req.get("planned_beat_id"),
        "setting": req.get("setting"),
        "emotional_tone": req.get("emotional_tone"),
        "pov_character": req.get("pov_character"),
        # Each inner beat's prose, from GET /scenes/{id} (W1-S9).
        "beats": scene.get("beats") or [],
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
                "pov": names.get(detail.get("pov_character_id") or ""),
                # SPEC-23: the beats as data, with their own cast, setting and
                # tone (cast as names, so the report reads without the ids).
                "planned_beats": [
                    {
                        **b,
                        "characters": [
                            names[c] for c in b.get("character_ids") or [] if c in names
                        ],
                    }
                    for b in detail.get("planned_beats") or []
                    if isinstance(b, dict)
                ],
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


# Bumped when what a generation records changes (v3: planned beats, each
# inner beat's cast), so a stale cache entry is regenerated, not regraded.
PROSE_CACHE_KEY = "planned-v3"


def _planned_beat_text(scene: dict, beat: dict | None) -> str:
    """The planned beat's cast, setting and tone, as the judge reads them
    (what the scene was asked for; empty fields are left out)."""
    lines = []
    if beat is not None:
        lines.append(f"PLANNED BEAT: {beat.get('title', '')}")
        if beat.get("characters"):
            lines.append(f"BEAT CAST: {', '.join(beat['characters'])}")
        if beat.get("secondary_names"):
            lines.append(f"BEAT SECONDARY PEOPLE: {', '.join(beat['secondary_names'])}")
    setting = scene.get("setting") or (beat or {}).get("setting")
    tone = scene.get("emotional_tone") or (beat or {}).get("tone")
    if setting:
        lines.append(f"SETTING: {setting}")
    if tone:
        lines.append(f"TONE: {tone}")
    if scene.get("pov_character"):
        lines.append(f"POV CHARACTER: {scene['pov_character']}")
    return "".join(f"{ln}\n" for ln in lines)


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
    """Deterministic pre-checks: {caps, warnings, cast_recall, seams...}.

    R1 (S4): the cap fires only when the scene has a cast and none of it is
    named on the page. A principal the scene's own plan (brief + beats) names
    who is never named on the page is a warning; the judge's R1 stands."""
    from evals.graders import seams

    text = scene["content"]
    cast = [n for n in scene.get("characters") or [] if n in forms]
    named = [n for n in cast if named_in(text, forms[n])]
    plan = " ".join(
        [scene.get("description") or ""]
        + [b.get("description") or "" for b in scene.get("beats") or []]
    )
    caps: dict[str, str] = {}
    if cast and not named:
        caps["R1"] = "none of the scene's cast is on the page"
    warnings = [
        f"{n} is in the plan but never named"
        for n in cast
        if n not in named and named_in(plan, forms[n])
    ]
    contents = [b["content"] for b in scene.get("beats") or [] if b.get("content")]
    beat_seams = seams.seam_report(contents)
    scene_seam = len(seams.shared(prev_text, text)) if prev_text else 0
    if beat_seams["max_overlap"]:
        caps["R4"] = f"repeated text across a beat seam ({beat_seams['max_overlap']})"
    elif scene_seam:
        caps["R4"] = f"repeated text across the scene seam ({scene_seam})"
    return {
        "caps": caps,
        "warnings": warnings,
        "cast": cast,
        "cast_recall": round(len(named) / len(cast), 4) if cast else None,
        "beat_seams": beat_seams,
        "scene_seam_overlap": scene_seam,
    }


def planned_beat_of(scene: dict, beats: list[dict]) -> dict | None:
    """The chapter's planned beat this scene was written from: by the
    request's `planned_beat_id`, else by its scene description."""
    from app.planning.planned_beat import beat_scene_description

    pid = scene.get("planned_beat_id")
    if pid:
        for b in beats:
            if b.get("node_id") == pid:
                return b
    desc = " ".join((scene.get("description") or "").split())
    for b in beats:
        if desc and " ".join(beat_scene_description(b).split()) == desc:
            return b
    return None


def cast_from_beat(scene: dict, beat: dict | None, chapter: dict) -> bool | None:
    """§5.2 `cast_from_beat` (F2): the scene's cast is its beat's cast (the
    chapter's when the beat names none; a secondary name now in the cast
    counts, by `classify_people`) plus the chapter POV. None when no planned
    beat matches."""
    from app.planning.outline import CastIndex, classify_people

    if beat is None:
        return None
    chapter_cast = list(chapter.get("cast") or [])
    pool = chapter_cast + list(scene.get("characters") or [])
    index = CastIndex(SimpleNamespace(id=n, name=n) for n in pool)
    refs = [*(beat.get("characters") or []), *(beat.get("secondary_names") or [])]
    want = set(classify_people(refs, index, None).ids or chapter_cast)
    if chapter.get("pov"):
        want.add(chapter["pov"])
    return set(scene.get("characters") or []) == want


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

    produced = await ctx.cache.memo("prose", f"{gt['book']}|{PROSE_CACHE_KEY}", gen)
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
    planned = chapter.get("planned_beats") or []
    alias_forms = [f for fs in forms.values() for f in fs]
    detail, prev_text = [], ""
    for i, sc in enumerate(scenes):
        checks = _grade_scene_checks(sc, prev_text, forms)
        rate, unknown = _unknown_canon(sc["content"], gt)
        beat = planned_beat_of(sc, planned) if planned else None
        row = {
            "scene_id": sc["id"],
            "words": len(sc["content"].split()),
            "n_beats": len(sc.get("beats") or []),
            "cast": checks["cast"],
            "cast_recall": checks["cast_recall"],
            "planned_beat": beat,
            "cast_from_beat": cast_from_beat(sc, beat, chapter),
            "beat_seam_max_overlap": checks["beat_seams"]["max_overlap"],
            "beat_seams_dirty": checks["beat_seams"]["dirty_seams"],
            "scene_seam_overlap": checks["scene_seam_overlap"],
            "unknown_rate_canon": rate,
            "unknown_canon": unknown,
            "deterministic_failures": checks["caps"],
            "deterministic_warnings": checks["warnings"],
        }
        if i < _judged_scene_cap():
            prior = " ".join(prev_text.split()[-_PRIOR_WORDS:]) or "(the scene opens)"
            base = (
                f"CANON:\n{canon}\n\nSTYLE GUIDE:\n{style}\n\n"
                f"CHAPTER: {chapter.get('title')}\n{chapter.get('summary') or ''}\n\n"
                f"SCENE BRIEF: {sc.get('description') or ''}\n"
                f"{_planned_beat_text(sc, beat)}"
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
    matched = [r["cast_from_beat"] for r in detail if r["cast_from_beat"] is not None]
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
        # §5.2: the R1 cap fires on 0 scenes; every scene's cast is its beat's.
        "r1_capped_scenes": sum(
            1 for r in detail if "R1" in r["deterministic_failures"]
        ),
        "cast_from_beat": (round(sum(matched) / len(matched), 4) if matched else None),
        "planned_beats": planned,
        "scenes": detail,
        "judge_explanation": " | ".join(explanations)[:4000],
    }


# --- Step 6b: AI revision of a scene (SPEC-19 / SPEC-25; tpm-plan §3.4) -------
# Three revisions of one generated cel scene, through the API, each as the
# author would ask: a range ("tighten paragraph 2"), an exchange ("make Zara
# colder") and a beat ("redo beat 2"). Proposals are discarded, never accepted,
# so every revision is written against the same draft. Deterministic: the text
# outside the range is byte-identical and the span names nobody new outside the
# canon. Judged on RA (instruction adherence) and R5 (the span's continuity with
# its surroundings): score = mean over revisions of mean(RA, R5).
_REVISE_FALLBACK_PARAGRAPHS = 8


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of each non-blank paragraph of `text`."""
    out, at = [], 0
    for para in text.split("\n\n"):
        stripped = para.strip()
        if stripped:
            start = at + para.index(stripped)
            out.append((start, start + len(stripped)))
        at += len(para) + 2
    return out


def revise_requests(draft: str, has_beats: bool, colder: str = "Zara") -> list[dict]:
    """The revise eval's three requests against `draft` (§3.4); the beat one
    only when the scene has beats to address."""
    paras = _paragraph_spans(draft)
    out = []
    if len(paras) >= 2:
        start, end = paras[1]
        out.append(
            {
                "id": "tighten",
                "instruction": "Tighten this paragraph: cut every redundant word.",
                "range": {"start": start, "end": end},
            }
        )
    talk = [
        i
        for i, (a, b) in enumerate(paras)
        if colder in draft[a:b] and ('"' in draft[a:b] or "“" in draft[a:b])
    ]
    if talk:
        i = talk[0]
        j = min(i + 1, len(paras) - 1)
        out.append(
            {
                "id": "colder",
                "instruction": f"Make {colder} colder in this exchange.",
                "range": {"start": paras[i][0], "end": paras[j][1]},
            }
        )
    if has_beats:
        out.append({"id": "redo_beat", "instruction": "Redo beat 2.", "beat_index": 1})
    return out


def revision_checks(
    draft: str, proposal: str, start: int, end: int, gt: dict
) -> tuple[dict, dict[str, str]]:
    """{outside_identical, new_names, revised}, and the caps they imply."""
    tail = len(draft) - end
    outside = (
        len(proposal) >= start + tail
        and proposal[:start] == draft[:start]
        and proposal[len(proposal) - tail :] == draft[end:]
    )
    revised = proposal[start : len(proposal) - tail] if outside else proposal
    _, unknown = _unknown_canon(revised, gt, draft)
    caps: dict[str, str] = {}
    if not outside:
        caps["RA"] = "text outside the range changed"
    if unknown:
        caps["R5"] = f"new name(s) outside the canon: {', '.join(unknown)}"
    return {
        "outside_identical": outside,
        "new_names": unknown,
        "revised": revised,
    }, caps


async def _revise_scene(ctx: StepContext) -> tuple[str | None, str]:
    """A scene to revise: the prose step's first scene when it lives in this
    app (it has beats), else a scene created from its prose (or the corpus
    excerpt) with no beats. Returns (scene_id, how)."""
    from evals.harness.client import EvalClientError

    gt = ctx.ground_truth
    produced = ctx.cache.get("prose", f"{gt['book']}|{PROSE_CACHE_KEY}") or {}
    first = next(
        (s for s in produced.get("scenes") or [] if (s.get("content") or "").strip()),
        None,
    )
    if first is not None:
        try:
            live = await ctx.client.get_scene(first["id"])
            if (live.get("content") or "").strip():
                return first["id"], "prose step scene"
        except Exception:  # noqa: BLE001 - another app's scene: create one
            pass
    text = (first or {}).get("content") or "\n\n".join(
        load_excerpts(ctx.book).split("\n\n")[:_REVISE_FALLBACK_PARAGRAPHS]
    )
    book = await _book_with_cast(ctx, f"eval-revise-{gt['book']}")
    chapter = await ctx.client.create_chapter(
        book["id"], "Revise", summary="revise eval"
    )
    try:
        scene = await ctx.client.create_scene(
            chapter["id"], content=text, characters=list(book["character_ids"])[:2]
        )
    except EvalClientError:
        return None, "could not create a scene"
    return scene.get("id"), "created scene (no beats)"


@step("revise", needs_api=True)
async def step_revise(ctx: StepContext) -> dict:
    gt = ctx.ground_truth
    scene_id, how = await _revise_scene(ctx)
    if not scene_id:
        return {"skipped": True, "reason": how}
    beats = await ctx.client.scene_beats(scene_id)
    scene = await ctx.client.get_scene(scene_id)
    # The draft the revise API counts offsets against (edited, else generated).
    draft = (await ctx.client.revise_draft(scene_id)) or (
        scene.get("content") or ""
    ).strip()
    # "Make Zara colder": the corpus' second principal, by first name.
    second = list(_forms(gt))[1:2]
    colder = second[0].split()[0] if second else "Zara"
    requests = revise_requests(draft, bool(beats), colder)
    canon = await _canon_block(ctx, scene["book_id"]) if scene.get("book_id") else ""

    async def run(req: dict) -> dict:
        body = {k: v for k, v in req.items() if k != "id"}
        return await ctx.client.revise(scene_id, body)

    from evals.harness.client import EvalClientError

    rows = []
    for req in requests:
        row: dict = {"id": req["id"], "instruction": req["instruction"]}
        try:
            out = await ctx.cache.memo(
                "revise", f"{gt['book']}|{req['id']}|{draft}", lambda: run(req)
            )
        except EvalClientError as e:
            # The API refused (e.g. 409 "The beat was edited"): what the author
            # would have seen; the revision scores 0 and the others still run.
            row.update(error=str(e)[:300], score=0.0)
            rows.append(row)
            continue
        prop = out.get("proposal") or {}
        if not prop.get("content") or prop.get("status") not in (
            "proposed",
            "discarded",
        ):
            row.update(
                error=f"no proposal (job {(out.get('job') or {}).get('status')}: "
                f"{(out.get('job') or {}).get('error') or ''})"[:300],
                score=0.0,
            )
            rows.append(row)
            continue
        start, end = prop.get("range") or [0, len(draft)]
        base = out.get("draft") or draft
        checks, caps = revision_checks(base, prop["content"], start, end, gt)
        row.update(
            range=[start, end],
            outside_identical=checks["outside_identical"],
            new_names=checks["new_names"],
            warnings=prop.get("warnings") or [],
            deterministic_failures=caps,
        )
        before = base[max(0, start - 1200) : start]
        after = base[end : end + 1200]
        content = (
            f"CANON:\n{canon or '(see the passage)'}\n\n"
            f"INSTRUCTION: {req['instruction']}\n\n"
            f"BEFORE THE PASSAGE (unchanged):\n{before or '(the scene opens)'}\n\n"
            f"PASSAGE BEFORE REVISION:\n{base[start:end]}\n\n"
            f"PASSAGE AFTER REVISION:\n{checks['revised']}\n\n"
            f"AFTER THE PASSAGE (unchanged):\n{after or '(the scene ends)'}"
        )
        judged = await _judge_rubrics(
            ctx, {rid: content for rid in rubrics.REVISE_RUBRICS}, caps
        )
        row["rubrics"] = judged
        row["score"] = rubrics.revise_score(_scores(judged))
        if prop.get("id") and prop.get("status") == "proposed":
            try:
                await ctx.client.discard_proposal(scene_id, prop["id"])
            except Exception:  # noqa: BLE001 - tidy-up only
                pass
        rows.append(row)

    scored = [r["score"] for r in rows if r.get("score") is not None]
    graded = [r for r in rows if "outside_identical" in r]
    return {
        "scene": how,
        "n_revisions": len(rows),
        "outside_identical": (
            f"{sum(r['outside_identical'] for r in graded)}/{len(graded)}"
        ),
        "new_names": sorted({n for r in graded for n in r["new_names"]}),
        "rubric_means": {
            rid: _mean_or_none(
                [
                    r["rubrics"][rid]["score"]
                    for r in rows
                    if r.get("rubrics") and r["rubrics"][rid]["score"] is not None
                ]
            )
            for rid in rubrics.REVISE_RUBRICS
        },
        "revisions": rows,
        "score": round(sum(scored) / len(scored), 4) if scored else None,
    }


def _mean_or_none(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 4) if vals else None


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
    return {
        "archive": archive,
        **prompt_contract.grade(
            records,
            **prompt_contract.corpus_inputs(ctx.ground_truth, load_style(ctx.book)),
        ),
    }
