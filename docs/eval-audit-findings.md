# Pipeline audit findings (eval-driven)

A three-front adversarial audit of the code that determines the eval scores —
the manuscript→voice RAG path, the LLM generation paths, and the eval harness's
own measurement integrity. Findings were verified firsthand against the code and,
where possible, quantified on the committed corpora with the real fastembed model
(no LLM key needed). Fixes shipped on `claude/polyphony-rtrentjones-integration-8j594n`.

## Why this matters
Several bugs **compounded to null out voice grounding** for real manuscript
uploads, while the eval was structurally **blind** to them (it seeded voice via
the API, bypassing the broken ingestion). And two harness bugs made a whole
metric a **false constant**. So the numbers could have looked "fine" while the
product produced ungrounded prose — exactly what the campaign must not miss.

---

## Fixed — voice grounding (commit "restore voice grounding")

| # | Defect | Evidence (measured) | Fix |
|---|--------|---------------------|-----|
| V1 | `RAG_SCORE_THRESHOLD=0.5` dropped **all** retrieved voice samples (applied to the top-k, all-or-nothing) | beat-description→voice-line cosine on all-MiniLM tops out at **0.235–0.451** (mean 0.345); every sample filtered → generation ran voice-blind | floor → 0.2 **and** `retrieve_similar` never returns empty for a character with samples (returns its closest) |
| V2 | Dialogue detection matched only straight ASCII quotes; real prose uses curly quotes | `extract_dialogue_only` returned **0 lines for every character**, `dialogue_count=0` across both whole corpora | normalize smart→straight, broaden speech verbs, handle inverted attribution; **wire spoken lines into indexed voice**. Verhoeven 0→19 lines |
| V3 | `dialogue.py` hard-filtered `chunk_type="dialogue"` (always empty) | dialogue path grounded on nothing | retrieve across all chunk types (mirrors `context.py`) |

## Fixed — generation reliability (commit "robust JSON parsing, clean beat planning…")

| # | Defect | Fix |
|---|--------|-----|
| G4 | `extract_json_array` raised on a valid array preceded by prose containing `[`; and returned a lone object's inner `children` as the whole outline | scan past prose brackets; a single object is one node, a single-key `{"outline":[…]}` wrapper is unwrapped; truncation-repair ordered before later-bracket scan so a truncated outer array isn't mistaken for its empty inner `[]` |
| G1 | `plan_scene_beats` asked for sub-numbered fields per beat; `parse_beats` turned them into junk beats, each firing a wasted generation call | prompt asks for one whole beat per line, with an example |
| G3 | Prose workflow stored blank output (safety-blocked beats) as a `completed` editable draft | fail the scene instead of persisting a blank as success |

## Fixed — measurement integrity (commit "fix bugs that made the reported numbers lie")

| # | Defect | Evidence | Fix |
|---|--------|----------|-----|
| M1 | Dracula continuity recall was a **false constant ~0** | injection anchor `Aldous Kerr` sat at char 19296, outside the graded `[:8000]` window, yet counted in the denominator; date anchor occurred once (no contradiction) | inject over the **same** window graded (12000 chars); re-anchor Dracula injections on tokens repeated inside it. Both corpora now 2/2 detectable |
| M2 | Extraction grader counted **any** shared token as a hit | `John Ward` scored as gold `Elias Ward` → inflates P **and** R | subset matching (honorific-aware): shared surname no longer matches; `Mr. Kerr`/`Aldous Kerr`, bare `Count`/`Count Vasska` still do |
| M3 | `_aggregate` carried a last-pass `error` key onto a valid `--repeat` mean → export dropped the whole step | — | seed the merged result from the last **scored** pass |
| M4 | the `--repeat` noise band never reached Tracer; scores unclamped | — | fold `score ±std (n)` into the case output; clamp to `[0,1]` |

**Verified sound by the audit (no change needed):** attribution chance `1/N`,
retrieval precision@k / MRR math, and the corpus rename leak-guard + train/test
disjointness — all correct.

## Closed the measurement gap
New **`ingestion`** eval step (`needs_api`): uploads the manuscript and measures
what fraction of the extracted cast ends up with a *usable* indexed voice
(`total_chunks ≥ 3`) — the ingestion→voice path the attribution/prose steps
bypassed. Before the V-fixes this scores ~0; after, it should climb. This is what
makes the voice fixes provable in Tracer.

---

## Backlog (deeper / lower-confidence — not yet fixed)

1. **First-person / epistolary narrators get little indexed voice** (architectural).
   Voice chunking gates on paragraphs containing the character's *name*; a diarist
   rarely names themselves, so their narration — their actual voice — isn't
   captured. Data: Aldous 3 desc chunks (0 dialogue); Victor/Emeric, Cosima,
   Cassidy 0 chunks. Needs narrator-section attribution (LLM segmentation or a
   heuristic), not just name-mention.
2. **Format-fragile paragraph splitting.** HTML joins lines with a single `\n`, so
   `split("\n\n")` yields one chunk = the whole manuscript (embedding truncates to
   ~256 tokens → voice destroyed); PDF is one-chunk-per-page. Normalize paragraphs
   in the parser, or record upload format as an eval covariate. (Corpora are TXT,
   so unaffected today.)
3. **Continuity: no cross-chunk comparison, `fact_sheet[:4000]` truncation, no
   finding dedup.** A contradiction between two prose chunks (not vs the bible) is
   undetectable; a large bible is truncated out of the window; duplicates inflate.
4. **`sanitize_for_llm` mangles voice samples** in the prose path (`--`→`[FILTERED]`,
   `html.escape` turns `don't`→`don&#x27;t`). Voice samples need injection
   sanitization but not HTML-escaping.
5. **Per-beat casting discarded.** `parse_beats` assigns the full cast to every
   beat; the planning call's per-beat character lists are thrown away. Needs
   structured (JSON) beat output to preserve.
6. **Thinking-token starvation is latent under a model override.** `reasoning_effort:
   none` disables thinking on `gemini-2.5-flash` (default is safe), but
   `LLM_MODEL=gemini-2.5-pro` reinstates reasoning tokens against small `max_tokens`
   caps. Pin the model or refuse non-flash Gemini.
7. **Continuity parse-failure vs clean.** G4 removed the dominant parse-failure
   causes; a residual safety-block still yields `[]` (indistinguishable from a
   clean check). A retry or an explicit "check incomplete" status would close it.
8. **Judge self-grading on by default** (Gemini judging Gemini). Surfaced in the
   scorecard; swap `EVAL_JUDGE_PROVIDER` once a non-Gemini key exists.
9. **`voice_chunks.book_id` is never populated** — inert plumbing (harmless).
