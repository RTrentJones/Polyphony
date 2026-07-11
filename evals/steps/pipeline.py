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


def load_corpus(book: str) -> tuple[str, dict]:
    d = CORPORA / book
    return (
        (d / "excerpts.txt").read_text(encoding="utf-8"),
        json.loads((d / "ground_truth.json").read_text(encoding="utf-8")),
    )


# --- Step 1: character extraction -----------------------------------------
@step("extraction", needs_api=True)
async def step_extraction(ctx: StepContext) -> dict:
    up = await ctx.client.upload_manuscript(
        f"{ctx.book}.txt", ctx.corpus_text.encode("utf-8"), title=f"eval-{ctx.book}"
    )
    status = await ctx.client.wait_manuscript(up["id"])
    if status.get("status") != "completed":
        return {"error": f"manuscript status {status.get('status')}", "score": 0.0}
    predicted = [c["name"] for c in await ctx.client.manuscript_characters(up["id"])]
    score = extraction.grade_extraction(predicted, ctx.ground_truth["cast"])
    return {"predicted": predicted, **score.as_dict(), "score": score.f1}


# --- Step 1b: ingestion voice quality (closes the manuscript→voice gap) -----
# attribution/prose seed voice via the voice-samples API, bypassing the
# manuscript chunking — so nothing measured whether an UPLOAD actually produces
# usable per-character voice. This uploads the manuscript and checks how much
# voice each extracted character ends up grounded on (was ~0 before the smart-
# quote / threshold fixes).
_MIN_USABLE_CHUNKS = 3


@step("ingestion", needs_api=True)
async def step_ingestion(ctx: StepContext) -> dict:
    up = await ctx.client.upload_manuscript(
        f"{ctx.book}.txt", ctx.corpus_text.encode("utf-8"), title=f"eval-ing-{ctx.book}"
    )
    status = await ctx.client.wait_manuscript(up["id"])
    if status.get("status") != "completed":
        return {"error": f"manuscript status {status.get('status')}", "score": 0.0}
    chars = await ctx.client.manuscript_characters(up["id"])
    if not chars:
        return {"score": 0.0, "reason": "no characters extracted", "n_characters": 0}

    detail, grounded, total_chunks = [], 0, 0
    for c in chars:
        stats = (await ctx.client.get_character(c["id"])).get("voice_stats") or {}
        n = stats.get("total_chunks", 0)
        total_chunks += n
        grounded += n >= _MIN_USABLE_CHUNKS
        detail.append({"name": c["name"], "chunks": n})
    # score = fraction of the extracted cast that got a usable indexed voice.
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
    gold = ctx.ground_truth["gold_lines"]
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
    gold = ctx.ground_truth["gold_lines"]
    # reference centroids from held-out TEST lines (never the indexed train lines).
    ref_vecs = {c: await embed.embed_many(v["test"]) for c, v in gold.items()}

    char_ids = {}
    for char, v in gold.items():
        created = await ctx.client.create_character(
            char, description=f"Narrator: {char}"
        )
        await ctx.client.add_voice_samples(
            created["id"], v["train"], chunk_type="dialogue"
        )
        char_ids[char] = created["id"]

    results = []
    for char in gold:

        async def gen(_char=char):
            r = await ctx.client.test_dialogue(char_ids[_char], _KNOCK)
            return r.get("dialogue") or r.get("action") or ""

        line = await ctx.cache.memo("attribution", f"{char}|{_KNOCK}", gen)
        if not line:
            continue
        results.append(
            attribution.attribute(char, await embed.embed_one(line), ref_vecs)
        )

    agg = attribution.accuracy(results)
    return {**agg, "score": agg["accuracy"], "detail": [r.as_dict() for r in results]}


# --- Step 4: outline / plot coherence -------------------------------------
@step("outline", needs_api=True)
async def step_outline(ctx: StepContext) -> dict:
    gt = ctx.ground_truth
    book = await ctx.client.create_book(
        title=f"eval-{gt['book']}", synopsis=gt["synopsis"], genre="gothic"
    )

    async def gen():
        plan = await ctx.client.generate_outline(
            book["id"], chapters_target=len(gt["canonical_beats"])
        )
        return plan.get("content", [])

    nodes = await ctx.cache.memo("outline", gt["synopsis"], gen)
    structural = bool(nodes) and all(n.get("title") for n in nodes)
    beats = "\n".join(f"- {b['title']} ({b['kind']})" for b in gt["canonical_beats"])
    outline_txt = "\n".join(
        f"{i + 1}. {n.get('title')}: {n.get('summary', '')}"
        for i, n in enumerate(nodes)
    )
    rubric = (
        "Does this generated outline recover the shape of the reference story — a "
        "clear inciting incident, rising complications, a midpoint turn, and a "
        f"climax? Reference beats:\n{beats}\nScore high only if most reference "
        "beats are recognizably present and causally ordered."
    )
    j = await ctx.judge.score(rubric, outline_txt or "(empty outline)")
    return {
        "n_nodes": len(nodes),
        "structural_ok": structural,
        "score": j.score if structural else 0.0,
        "judge_explanation": j.explanation,
    }


# --- Step 5: continuity detection (constructed ground truth) ---------------
# The prose fed to the checker is truncated to this window, so injections MUST be
# applied over the SAME window (not the full text) or a mutation past the cut is
# counted in the denominator yet never seen — pinning recall at 0. Sized to hold
# each corpus's injection anchors AND a second occurrence for the contradiction.
_CONTINUITY_WINDOW = 12000


@step("continuity", needs_api=True)
async def step_continuity(ctx: StepContext) -> dict:
    gt = ctx.ground_truth
    window = ctx.corpus_text[:_CONTINUITY_WINDOW]
    injected_text, expected = continuity.apply_injections(
        window, gt["continuity_injections"]
    )

    async def run_on(prose: str, tag: str) -> list[dict]:
        book = await ctx.client.create_book(
            title=f"eval-cont-{tag}", synopsis="continuity eval"
        )
        chapter = await ctx.client.create_chapter(book["id"], "Chapter", summary="eval")
        scene = await ctx.client.generate_scene(
            chapter["id"],
            {
                "characters": [gt["cast"][0]],
                "scene_description": "placeholder scene for continuity eval content",
                "setting": "n/a",
                "emotional_tone": "neutral",
                "target_word_count": 100,
            },
        )
        await ctx.client.set_scene_content(scene["id"], prose[:_CONTINUITY_WINDOW])
        return await ctx.client.run_continuity(book["id"], chapter["id"])

    injected = await run_on(injected_text, "injected")
    control = await run_on(window, "control")
    score = continuity.grade_continuity(expected, injected, control)
    return {**score.as_dict(), "score": score.detection_recall}


# --- Step 6: end-to-end prose (judge) -------------------------------------
@step("prose", needs_api=True)
async def step_prose(ctx: StepContext) -> dict:
    gt = ctx.ground_truth
    cast = list(gt["gold_lines"])[:2]
    book = await ctx.client.create_book(
        title=f"eval-prose-{gt['book']}", synopsis=gt["synopsis"]
    )
    chapter = await ctx.client.create_chapter(
        book["id"], "An encounter", summary="two narrators meet"
    )
    body = {
        "characters": cast,
        "scene_description": "The two meet at night and argue about whether to press on.",
        "setting": "a fog-bound street",
        "emotional_tone": "tense",
        "target_word_count": 400,
    }

    async def gen():
        scene = await ctx.client.generate_scene(chapter["id"], body)
        return scene.get("content", "")

    prose = await ctx.cache.memo("prose", f"{gt['book']}|{'/'.join(cast)}", gen)
    rubric = (
        "Judge this generated scene on: (a) does it dramatize the requested beat "
        "(the two characters meet and argue about pressing on); (b) is it coherent, "
        "readable prose; (c) do the two named characters read as distinct voices. "
        f"Characters: {', '.join(cast)}."
    )
    j = await ctx.judge.score(rubric, prose or "(empty)")
    return {
        "score": j.score,
        "judge_explanation": j.explanation,
        "words": len(prose.split()),
    }
