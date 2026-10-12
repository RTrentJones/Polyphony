"""The six eval steps, each registered against the step registry.

Every step is `async def (ctx: StepContext) -> dict`. Steps 2 (retrieval
separability) and the reference side of 3 run in-process with the app's own
embedder — free, no LLM. The rest drive the real API and are cached.

Importing this module registers the steps (import side effect); `evals.run`
imports it so the registry is populated.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.graders import attribution, continuity, extraction, retrieval
from evals.harness import embed
from evals.harness.vecmath import cosine
from evals.steps.base import StepContext, step

CORPORA = Path(__file__).resolve().parent.parent / "corpora"

_KNOCK = "Say a few sentences reacting to an unexpected knock at the door, at night."


def _cast_with_roles(gt: dict) -> list[dict]:
    """The corpus cast as character payloads. Principals (when the corpus
    declares fidelity principals) are marked protagonist so the app's
    principal-cast gate has a real cast to enforce; everyone else is a
    supporting role."""
    principals = {
        p["name"] for p in (gt.get("fidelity") or {}).get("principals", [])
    } or set(gt.get("cast", [])[:3])
    return [
        {
            "name": name,
            "role": "protagonist" if name in principals else "supporting",
            "description": f"{name}, from the corpus.",
        }
        for name in gt.get("cast", [])
    ]


async def _book_with_cast(ctx: StepContext, title: str, genre: str = "") -> dict:
    """A book carrying the corpus synopsis AND its cast — the canon an author
    would have. An eval book with no characters makes the fidelity gate vacuous."""
    gt = ctx.ground_truth
    book = await ctx.client.create_book(
        title=title, synopsis=gt["synopsis"], genre=genre
    )
    ids = {}
    for c in _cast_with_roles(gt):
        created = await ctx.client.create_character(
            c["name"], book["id"], role=c["role"], description=c["description"]
        )
        ids[c["name"]] = created["id"]
    book["character_ids"] = ids
    return book


def load_corpus(book: str) -> tuple[str, dict]:
    d = CORPORA / book
    return (
        (d / "excerpts.txt").read_text(encoding="utf-8"),
        json.loads((d / "ground_truth.json").read_text(encoding="utf-8")),
    )


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
    predicted = [c["name"] for c in proposals.get("characters", [])]
    score = extraction.grade_extraction(predicted, ctx.ground_truth["cast"])
    await ctx.client.commit_extraction(
        up["book_id"], up["extraction_run_id"], proposals
    )
    committed = sorted(c["name"] for c in await ctx.client.source_characters(up["id"]))
    return {
        "predicted": predicted,
        **score.as_dict(),
        "score": score.f1,
        "commit_faithful": committed == sorted(set(predicted)),
    }


# --- Step 1b: ingestion voice quality (closes the manuscript→voice gap) -----
# attribution/prose seed voice via the voice-samples API, bypassing the
# manuscript chunking — so nothing measured whether an UPLOAD actually produces
# usable per-character voice. This uploads the manuscript and checks how much
# voice each extracted character ends up grounded on (was ~0 before the smart-
# quote / threshold fixes).
_MIN_USABLE_CHUNKS = 3


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
# The book carries the corpus synopsis AND cast (principals marked), so the
# app's own principal-cast gate is live, and the score is gated on fidelity
# when the corpus declares principals: a beautiful outline about the wrong story
# scores ~0 (docs/BRD.md R1.4, §7).
@step("outline", needs_api=True)
async def step_outline(ctx: StepContext) -> dict:
    from evals.graders.fidelity import grade_fidelity

    gt = ctx.ground_truth
    book = await _book_with_cast(ctx, f"eval-{gt['book']}", genre="")

    async def gen():
        plan = await ctx.client.generate_outline(
            book["id"], chapters_target=len(gt["canonical_beats"])
        )
        return {
            "content": plan.get("content", []),
            "status": plan.get("status"),
            "warnings": plan.get("warnings", []),
        }

    out = await ctx.cache.memo("outline", gt["synopsis"], gen)
    nodes = out.get("content", []) if isinstance(out, dict) else (out or [])
    structural = bool(nodes) and all(n.get("title") for n in nodes)
    beats = "\n".join(f"- {b['title']} ({b['kind']})" for b in gt["canonical_beats"])
    outline_txt = "\n".join(
        f"{i + 1}. {n.get('title')}: {n.get('summary', '')}"
        + "".join(
            f"\n   - {c.get('title')}: {c.get('summary', '')}"
            for c in n.get("children", [])
        )
        for i, n in enumerate(nodes)
    )
    rubric = (
        "Does this generated outline recover the shape of the reference story — a "
        "clear inciting incident, rising complications, a midpoint turn, and a "
        f"climax? Reference beats:\n{beats}\nScore high only if most reference "
        "beats are recognizably present and causally ordered, AND the outline is "
        "about the reference story's own characters (an invented protagonist "
        "scores 0)."
    )
    j = await ctx.judge.score(rubric, outline_txt or "(empty outline)")
    result: dict = {
        "n_nodes": len(nodes),
        "structural_ok": structural,
        "judge": j.score,
        "judge_explanation": j.explanation,
        "plan_status": out.get("status") if isinstance(out, dict) else None,
        "app_warnings": out.get("warnings") if isinstance(out, dict) else None,
    }
    fid = gt.get("fidelity")
    if fid:
        fs = grade_fidelity(nodes, fid["principals"], set(fid["known"]))
        result["cast_fidelity"] = fs.principal_recall
        result["unknown_rate"] = fs.unknown_rate
        result["missing_principals"] = fs.missing
        result["score"] = round(fs.principal_recall * j.score, 4) if structural else 0.0
    else:
        result["score"] = j.score if structural else 0.0
    return result


# --- Step 5: continuity detection (constructed ground truth) ---------------
# The prose fed to the checker is truncated to this window, so injections MUST be
# applied over the SAME window (not the full text) or a mutation past the cut is
# counted in the denominator yet never seen — pinning recall at 0. Sized to hold
# each corpus's injection anchors AND a second occurrence for the contradiction.
_CONTINUITY_WINDOW = 12000


@step("continuity", needs_api=True)
async def step_continuity(ctx: StepContext) -> dict:
    gt = ctx.ground_truth
    if not gt.get("continuity_injections"):
        return {"skipped": True, "reason": "corpus has no continuity injections"}
    window = ctx.corpus_text[:_CONTINUITY_WINDOW]
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
    return {**score.as_dict(), "score": score.detection_recall}


# --- Step 6: end-to-end prose (judge) -------------------------------------
@step("prose", needs_api=True)
async def step_prose(ctx: StepContext) -> dict:
    gt = ctx.ground_truth
    cast = (list(gt.get("gold_lines") or {}) or list(gt.get("cast", [])))[:2]
    book = await _book_with_cast(ctx, f"eval-prose-{gt['book']}")
    chapter = await ctx.client.create_chapter(
        book["id"], "An encounter", summary="two of the leads meet"
    )
    # Two leads that exist in the book's cast (names the API resolves to ids).
    cast = [c for c in cast if c in book["character_ids"]] or list(
        book["character_ids"]
    )[:2]
    body = {
        "character_ids": [book["character_ids"][c] for c in cast],
        "scene_description": "The two meet at night and argue about whether to press on.",
        "setting": "a fog-bound street",
        "emotional_tone": "tense",
        "target_word_count": 400,
    }

    async def gen():
        scene = await ctx.client.generate_scene(chapter["id"], body)
        # Keep the terminal status alongside the prose so an empty result is
        # self-diagnosing (status=failed vs completed-but-empty vs blocked)
        # instead of a guess in the scorecard.
        return {"content": scene.get("content") or "", "status": scene.get("status")}

    gen_out = await ctx.cache.memo("prose", f"{gt['book']}|{'/'.join(cast)}", gen)
    # Back-compat: older cache entries memoized a bare content string.
    if isinstance(gen_out, str):
        prose, gen_status = gen_out, "unknown"
    else:
        prose = (gen_out or {}).get("content") or ""
        gen_status = (gen_out or {}).get("status") or "unknown"
    if not prose.strip():
        # Generation failed/blocked — score 0 rather than crash or judge "(empty)".
        return {
            "score": 0.0,
            "judge_explanation": f"empty scene (terminal status={gen_status}: "
            "generation failed, blocked, or quota-exhausted)",
            "scene_status": gen_status,
            "words": 0,
        }
    rubric = (
        "Judge this generated scene on: (a) does it dramatize the requested beat "
        "(the two characters meet and argue about pressing on); (b) is it coherent, "
        "readable prose; (c) do the two named characters read as distinct voices. "
        f"Characters: {', '.join(cast)}."
    )
    j = await ctx.judge.score(rubric, prose)
    named = [c for c in cast if c.split()[0] in prose]
    return {
        "score": j.score,
        "judge_explanation": j.explanation,
        "words": len(prose.split()),
        "cast_named": len(named) / max(1, len(cast)),
    }
