# SPEC-83 — Retrieval bound: a 200-row pool with a total order, and a reason when it runs out
**Closes**: W12-3 (B7, P4, N4) · **Size**: XS · **Depends on**: SPEC-40, -53, -77.
- **Change.** `_POOL = 200` (was 50) and `ORDER BY embedding <=> q, id` in `ChunkStore.retrieve_similar`. Eligibility stays the Python
  filter after the query (no paging, no eligibility in SQL). With the total order, the first k eligible rows of the top 200 are the
  same rows, in the same order, as before wherever the top 50 already held k: prompts change only where a block was starved (or the
  typed-movement fallback fired), which is the fix. `withheld` counts may rise; that is reporting only.
- **Failure-only record (P4).** The store reports "bound hit" (it returned its full `k`) through an out-sink, as `withheld` does. When a
  block is still short of its k **and** its retrieval hit the bound, `character_samples` appends
  `unsampled_entry(character, "retrieval exhausted (200-row bound)")` to the caller's `withheld` list (scenes and revise; the ensemble
  passes none). A retrieval that fills its block never changes its record.
- **HNSW.** Before 0021 an HNSW plan *could* have returned at most 40 rows (`ef_search` default) before the character filter; the planner
  may already have chosen `idx_voice_chunks_character`. That claim is "possible", not observed. After this change nothing reads the
  index, and 0021 drops it (N4).
- **Round trip (§5.1, dev).** The 24-check round trip is byte-identical to day-0 (27 prompt records), as are the revise prompts, cel and
  dracula chunks, the grounding API over 33 stored scenes and the 22 regrades. **No cel block was starved at 50, so no block is
  replenished and the before/after list is empty.**
- **p95 (dev container, 1,000-row store, 20 trials, exact sort):** `LIMIT 50` p50 2.16 / p95 2.59 ms; `LIMIT 200, id` p50 2.27 / p95
  3.05 ms.
