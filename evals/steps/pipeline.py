"""The six eval steps, each driving a running Polyphony and/or the local embedder.

Design notes:
- Steps 2 (retrieval separability) and the embedding half of 3 run in-process
  with the app's own embedder — free, no LLM, so they run anywhere.
- Steps 1, 3(gen), 4, 5, 6 spend LLM quota via the real API and are cached.
Every step returns a plain dict for the report.
"""

from __future__ import annotations

from pathlib import Path

from evals.graders import attribution, continuity, extraction, retrieval
from evals.harness import embed
from evals.harness.cache import Cache
from evals.harness.client import PolyphonyClient
from evals.harness.judge import Judge

CORPORA = Path(__file__).resolve().parent.parent / "corpora"


def load_corpus(book: str) -> tuple[str, dict]:
    import json

    d = CORPORA / book
    return (d / "excerpts.txt").read_text(encoding="utf-8"), json.loads(
        (d / "ground_truth.json").read_text(encoding="utf-8")
    )


# --- Step 1: character extraction -----------------------------------------
async def step_extraction(
    client: PolyphonyClient, book: str, gt: dict, text: str
) -> dict:
    up = await client.upload_manuscript(
        f"{book}.txt", text.encode("utf-8"), title=f"eval-{book}"
    )
    status = await client.wait_manuscript(up["id"])
    if status.get("status") != "completed":
        return {"error": f"manuscript status {status.get('status')}", "score": 0.0}
    chars = await client.manuscript_characters(up["id"])
    predicted = [c["name"] for c in chars]
    score = extraction.grade_extraction(predicted, gt["cast"])
    return {
        "predicted": predicted,
        **score.as_dict(),
        "score": score.f1,
        "manuscript_id": up["id"],
    }


# --- Step 2: retrieval / voice separability (in-harness embeddings) --------
async def step_retrieval(gt: dict) -> dict:
    gold = gt["gold_lines"]
    # pool all train lines with their owner; embed once.
    pool_texts, pool_owner = [], []
    for char, v in gold.items():
        for ln in v["train"]:
            pool_texts.append(ln)
            pool_owner.append(char)
    pool_vecs = await embed.embed_many(pool_texts)

    def cos(a, b):
        num = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return num / (na * nb) if na and nb else 0.0

    queries = [(c, ln) for c, v in gold.items() for ln in v["test"]]
    query_vecs = await embed.embed_many([q for _, q in queries])
    owner_by_text = {t: o for t, o in zip(pool_texts, pool_owner)}
    # grade_retrieval calls retrieve(character, query, k); we look the query's
    # precomputed vector up by its text.
    qvec_by_text = {q: v for (_, q), v in zip(queries, query_vecs)}

    def retrieve_fn(character, query, k):
        qv = qvec_by_text[query]
        ranked = sorted(
            range(len(pool_texts)), key=lambda i: cos(qv, pool_vecs[i]), reverse=True
        )
        return [pool_texts[i] for i in ranked[:k]]

    score = retrieval.grade_retrieval(
        queries, retrieve_fn, lambda t: owner_by_text[t], k=3
    )
    # top-1 accuracy is the headline separability signal
    return {**score.as_dict(), "score": score.precision_at_k, "characters": list(gold)}


# --- Step 3: voice attribution (real generation + embedding) ---------------
async def step_attribution(client: PolyphonyClient, gt: dict, cache: Cache) -> dict:
    gold = gt["gold_lines"]
    # reference centroids from held-out TEST lines (never the indexed train lines).
    ref_vecs = {c: await embed.embed_many(v["test"]) for c, v in gold.items()}

    # index each character's TRAIN lines so the pipeline can ground on them.
    char_ids = {}
    for char, v in gold.items():
        created = await client.create_character(char, description=f"Narrator: {char}")
        await client.add_voice_samples(created["id"], v["train"], chunk_type="dialogue")
        char_ids[char] = created["id"]

    prompt = (
        "Say a few sentences reacting to an unexpected knock at the door, at night."
    )
    results = []
    for char in gold:
        key = f"{char}|{prompt}"

        async def gen(_char=char):
            r = await client.test_dialogue(char_ids[_char], prompt)
            return r.get("dialogue") or r.get("action") or ""

        line = await cache.memo("attribution", key, gen)
        if not line:
            continue
        vec = await embed.embed_one(line)
        results.append(attribution.attribute(char, vec, ref_vecs))

    agg = attribution.accuracy(results)
    return {**agg, "score": agg["accuracy"], "detail": [r.as_dict() for r in results]}


# --- Step 4: outline / plot coherence -------------------------------------
async def step_outline(
    client: PolyphonyClient, gt: dict, judge: Judge, cache: Cache
) -> dict:
    book = await client.create_book(
        title=f"eval-{gt['book']}", synopsis=gt["synopsis"], genre="gothic"
    )

    async def gen():
        plan = await client.generate_outline(
            book["id"], chapters_target=len(gt["canonical_beats"])
        )
        return plan.get("content", [])

    nodes = await cache.memo("outline", gt["synopsis"], gen)
    structural = bool(nodes) and all(n.get("title") for n in nodes)
    beats = "\n".join(f"- {b['title']} ({b['kind']})" for b in gt["canonical_beats"])
    outline_txt = "\n".join(
        f"{i+1}. {n.get('title')}: {n.get('summary','')}" for i, n in enumerate(nodes)
    )
    rubric = (
        "Does this generated outline recover the shape of the reference story — a "
        "clear inciting incident, rising complications, a midpoint turn, and a "
        f"climax? Reference beats:\n{beats}\nScore high only if most reference "
        "beats are recognizably present and causally ordered."
    )
    j = await judge.score(rubric, outline_txt or "(empty outline)")
    return {
        "n_nodes": len(nodes),
        "structural_ok": structural,
        "score": j.score if structural else 0.0,
        "judge_explanation": j.explanation,
        "book_id": book["id"],
    }


# --- Step 5: continuity detection (constructed ground truth) ---------------
async def step_continuity(
    client: PolyphonyClient, gt: dict, text: str, cache: Cache
) -> dict:
    injected_text, expected = continuity.apply_injections(
        text, gt["continuity_injections"]
    )

    async def run_on(prose: str, tag: str) -> list[dict]:
        book = await client.create_book(
            title=f"eval-cont-{tag}", synopsis="continuity eval"
        )
        chapter = await client.create_chapter(book["id"], "Chapter", summary="eval")
        # mint a scene, then overwrite its content with the eval prose.
        scene = await client.generate_scene(
            chapter["id"],
            {
                "characters": [gt["cast"][0]],
                "scene_description": "placeholder scene for continuity eval content",
                "setting": "n/a",
                "emotional_tone": "neutral",
                "target_word_count": 100,
            },
        )
        # PUT the real (injected/clean) prose as the scene content.
        await client._post(  # noqa: SLF001 — internal helper is fine here
            f"/api/v1/books/scenes/{scene['id']}/content",
            json={"content": prose[:8000]},
        )
        return await client.run_continuity(book["id"], chapter["id"])

    injected_findings = await run_on(injected_text[:8000], "injected")
    control_findings = await run_on(text[:8000], "control")
    score = continuity.grade_continuity(expected, injected_findings, control_findings)
    return {**score.as_dict(), "score": score.detection_recall}


# --- Step 6: end-to-end prose (judge) -------------------------------------
async def step_prose(
    client: PolyphonyClient, gt: dict, judge: Judge, cache: Cache, char_book=None
) -> dict:
    cast = list(gt["gold_lines"])[:2]
    book = await client.create_book(
        title=f"eval-prose-{gt['book']}", synopsis=gt["synopsis"]
    )
    chapter = await client.create_chapter(
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
        scene = await client.generate_scene(chapter["id"], body)
        return scene.get("content", "")

    prose = await cache.memo("prose", f"{gt['book']}|{'/'.join(cast)}", gen)
    rubric = (
        "Judge this generated scene on: (a) does it dramatize the requested beat "
        "(the two characters meet and argue about pressing on); (b) is it coherent, "
        "readable prose; (c) do the two named characters read as distinct voices. "
        f"Characters: {', '.join(cast)}."
    )
    j = await judge.score(rubric, prose or "(empty)")
    return {
        "score": j.score,
        "judge_explanation": j.explanation,
        "words": len(prose.split()),
    }
