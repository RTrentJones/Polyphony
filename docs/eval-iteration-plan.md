# Polyphony Eval-Driven Iteration Plan

**Goal:** use the eval harness to iterate on prompts and architecture until there is a
**noticeable, defensible quality improvement visible in Tracer** — not eyeballed, not
overfit, not noise.

---

## 0. Success criteria (what "noticeable improvement" means)

A change is **kept** only if it beats the baseline **beyond the noise band** on the
graded dimension **and** does not regress any other dimension, on **two** corpora
(Dracula + one held-out book). The campaign is **done** when every dimension is at or
above target, or improvements have plateaued for two consecutive sprints.

| Dimension | Grader | Metric | Baseline (measured in P1) | Target |
|-----------|--------|--------|---------------------------|--------|
| extraction | deterministic | F1 vs gold cast | TBD (expected low: head-only + narrator-exclusion) | ≥ 0.85 |
| retrieval | deterministic | precision@3 | **0.818** (reproduced) | ≥ 0.80 (guardrail, don't regress) |
| attribution | deterministic | top-1 accuracy vs 0.33 chance | TBD (ref side ~0.82 locally) | ≥ 0.75 |
| outline | LLM judge + structural | judge 0..1, beat recall | TBD | judge ≥ 0.70, all canonical beats present |
| continuity | deterministic | detection recall / FPR | TBD | recall ≥ 0.60, FPR ≤ 0.20 |
| prose | LLM judge | judge 0..1 | TBD | ≥ 0.70 |

Baselines are filled in by the **first Tracer run (Phase 1)**; targets are provisional and
re-set once the true baseline is known.

---

## 1. Constraints that shape the plan (ground truth)

- **No LLM in the dev sandbox** (no `GEMINI_API_KEY`, no Docker, no Postgres). Only the
  in-process `retrieval` step runs locally (fastembed). Every other step needs a running
  Polyphony backed by Gemini.
- **Prompts are baked into the app**, so measuring a prompt change means running that
  code with a live LLM: either CI boot-mode (`evals.yml`) or a lightweight offline path.
- **Free-tier Gemini** ≈ 8 RPM / ~250 req/day, shared with muse/tracer. A full 6-step run
  ≈ 20–30 calls. Budget ≈ a few full runs/day → **cache aggressively, run deterministic
  steps first, batch experiments.**
- **Generation is stochastic** → single-run deltas can be noise. Rigor (below) is
  mandatory, not optional.
- **Overfitting risk:** tuning prompts against one book's ground truth. A held-out corpus
  is a first-class requirement, not a "later."

---

## 2. Accelerators to build first (they make every later sprint faster/cheaper)

These are one-time infra pieces that de-risk and speed the whole campaign.

### A2. Offline direct-eval harness for DB-free steps
`extraction` and `outline` are **pure LLM calls** — no DB, no server. Add
`evals/offline.py` that imports `app.parsing.character_extractor.CharacterExtractor` and
`app.planning.outline.generate_outline` and grades them directly against a corpus, needing
**only a Gemini key** (no Postgres, no uvicorn). This turns the two highest-deficit
dimensions into a **seconds-long local loop** the moment a key exists, instead of a
5–10-min CI cycle. RAG-dependent steps (attribution/prose/continuity) still need CI.

### A3. Determinism + caching for reproducibility
- Add an `EVAL_DETERMINISTIC=1` path that forces `temperature=0` on the steps under test
  (extraction, outline, continuity — generation-quality steps like prose keep some temp
  but are averaged instead). Removes most run-to-run jitter on the deterministic graders.
- Confirm the existing `Cache` (keyed by `app_sha`) is used on every API step so
  **re-grading never re-generates** — already true for outline/prose/attribution; extend
  to extraction/continuity so a repeated run is free.

### A4. Multi-run averaging + noise band
Add `--repeat N` (default 3 for judge/generation steps, 1 for deterministic). Report
**mean ± std** per step. Establish the **noise band** from the baseline's std; only trust
deltas that exceed it (≥ 1σ and ≥ a floor, e.g. 0.03 absolute).

### A5. Held-out second corpus
Add a second book as **validation** (data-only: `aliases.json` + `spec.json`, then
`rename`/`build_gt`). **Pride & Prejudice** (clear cast, strong distinct voices, third-
person — complements Dracula's epistolary form) or **Sherlock** (dialogue-heavy). Rule:
**an improvement counts only if it holds on both books.** Dracula is the training signal;
the second book is the honesty check.

### A6. Judge integrity
- Keep the **self-preference flag** (judge==model-under-test) surfaced on every run.
- When an `ANTHROPIC_API_KEY` (or other non-Gemini key) exists, set
  `EVAL_JUDGE_PROVIDER` to it so the judge isn't grading its own family. Until then, treat
  judge-scored steps (outline, prose) as **directional**, and lean on the **deterministic**
  proxies (outline: canonical-beat recall; prose: attribution accuracy on the generated
  scene) as the trustworthy signal.

---

## 3. The iteration loop (per-experiment protocol)

```
for each dimension, ordered by (deficit × cheapness):
  1. Pick ONE hypothesis from the backlog (§5). One variable at a time.
  2. Branch. Apply the change. Keep the diff isolated to that dimension.
  3. Measure:
       - extraction/outline  → offline harness (fast, key-only) ×N
       - attribution/prose/continuity → CI boot-mode evals.yml ×N
  4. Compare vs current baseline on Dracula AND the held-out book.
  5. Accept iff: Δ(target metric) > noise band  AND  no other dimension regresses
     beyond noise, on BOTH books.
  6. Accept → merge, ingest to Tracer (new "after" run, new baseline).
     Reject → revert, record the negative result in the experiment log.
  7. Repeat until dimension ≥ target or two dead experiments in a row (move on).
```

**Tracer is the ledger.** Every accepted change is one ingested run (`tool=polyphony`,
`git_sha`, per-step cases). The `/` dashboard's pass-rate-over-time and per-model trend
show the campaign as a rising line; `/runs/[id]` holds the judge rationale for the
narrative. A regression shows as a flagged run.

---

## 4. Phasing

| Phase | Work | Gate to next |
|-------|------|--------------|
| **P0 — Land the loop** | Merge the current branch → `main` (so `evals.yml` is dispatchable and improved prompts are testable). Add repo secrets `GEMINI_API_KEY`, `TRACER_INGEST_TOKEN` (+ `EVAL_ADMIN_*` for prod baseline). Build accelerators A2–A5. | `evals.yml` dispatch runs green; offline harness scores extraction+outline locally. |
| **P1 — Baseline** | Dispatch `evals.yml` **against prod** (old prompts, `base_url=…`) → Tracer "before". Dispatch **boot-mode** on `main` (current prompts incl. the 4 shipped changes) → second data point. Fill §0 baseline column; compute noise bands (×3). | Baselines + noise bands recorded in Tracer and this doc. |
| **P2 — Extraction sprint** | E-series (§5). Cheapest, largest expected deficit. | extraction F1 ≥ target or plateau. |
| **P3 — Voice/attribution sprint** | V-series. | attribution ≥ target or plateau. |
| **P4 — Outline sprint** | O-series. | beat recall + judge ≥ target or plateau. |
| **P5 — Continuity sprint** | C-series. | recall/FPR ≥ target or plateau. |
| **P6 — Prose sprint** | P-series. | prose judge + generated-scene attribution ≥ target or plateau. |
| **P7 — Consolidate & deploy** | Merge all accepted changes, deploy improved prompts to **prod**, run the suite against prod → Tracer, and confirm the deployed line matches the branch gains. Write the before/after summary. | Prod Tracer trend shows the cumulative lift. |

Order rationale: extraction feeds everything (bad cast → bad voice/prose); it's also the
cheapest (1 call) and has the clearest deficit. Voice/attribution next because prose
depends on it. Judge-scored steps (outline/prose) last, when a better judge may exist.

---

## 5. Experiment backlog (per dimension, ranked)

*Shipped* = already on the branch (`0ae7421`), to be measured in P1/P2.

### Extraction (E)
- **E1 — whole-manuscript stratified sampling** *(shipped)*. Windows across the text, not
  `text[:10000]`. Recovers late-introduced POVs. → recall.
- **E2 — stop excluding narrators** *(shipped)*. Epistolary diarists/letter-writers ARE
  the cast. → recall (large on Dracula).
- **E3 — two-pass canonicalization.** Pass 1: candidate names per window. Pass 2: merge
  aliases/honorifics ("Prof. Verhoeven"≈"Verhoeven"), drop one-off mentions. → precision.
- **E4 — role-tagged extraction.** Ask for `{name, role}` (narrator/protagonist/minor);
  keep majors + narrators, drop "minor". → precision without hurting recall.
- **E5 — frequency vote.** Extract per window; keep names appearing in ≥2 windows OR
  flagged narrator. → precision/recall balance on long texts.
- **E6 — `temperature=0`** for extraction under eval. → variance ↓ (reproducibility).

### Voice / attribution (V)
- **V1 — retrieve across all chunk types** *(shipped)*, dialogue-first, samples 3→5. Beats
  the brittle heuristic classifier starving dialogue-only retrieval. → attribution.
- **V2 — voice fingerprint.** At ingest, extract a one-line style descriptor per character
  (diction/cadence/tics) and inject it into the context block alongside raw samples. →
  attribution + prose distinctness.
- **V3 — MMR/diversity retrieval.** Penalize near-duplicate samples so the k samples span
  the voice, not one repeated line. → richer grounding.
- **V4 — targeted query.** Retrieve with `beat + character role`, not beat alone. → sample
  relevance.
- **V5 — chunking sweep.** Sentence-window vs paragraph, with overlap; re-measure the
  in-process `retrieval` p@3 as the cheap proxy (no LLM) before committing.

### Outline (O)
- **O1 — explicit arc scaffolding** *(shipped)*. Inciting→rising→midpoint→climax→
  resolution, causal ordering, summaries state what *changes*. → beat recall.
- **O2 — two-stage generation.** First a 5-beat spine/logline, then expand to chapters.
  Better global coherence than one-shot. → beat recall + judge.
- **O3 — self-critique pass.** Generate → model checks beat coverage & causal order →
  revise. Costs +1 call; measure ROI. → judge.
- **O4 — genre beat priors.** Feed the genre's conventional structure as a checklist. →
  beat recall.

### Continuity (C)
- **C1 — structured fact sheet.** Entities/dates/objects as a table, not prose bible. →
  recall.
- **C2 — few-shot contradiction vs absence.** Examples that distinguish a real
  contradiction from a mere omission. → FPR ↓ without recall loss.
- **C3 — claim-extraction two-pass.** Extract claims from prose, diff against the fact
  sheet. → recall.
- **C4 — finding dedup/merge** across chunks + `temperature=0`. → FPR ↓, stability.

### Prose (P)
- **P1 — distinct-voice instruction** *(shipped)*. → judge + generated-scene attribution.
- **P2 — per-character mini style-guide** in the beat prompt (from V2 fingerprint). →
  distinctness.
- **P3 — draft-then-revise-for-voice** two-pass. Costs calls; measure ROI. → judge.
- **P4 — better beat planning** (`plan_scene_beats` prompt): sharper beats → better prose.

---

## 6. Rigor: telling improvement from noise

- **Deterministic graders first** (extraction, retrieval, attribution, continuity): these
  are the trustworthy signal; judge steps are directional until a non-Gemini judge exists.
- **N=3 repetitions** for any step involving generation; report **mean ± std**; the
  **accept threshold = max(1σ_baseline, 0.03 absolute)**.
- **Two corpora**: accept only if the gain holds on Dracula **and** the held-out book.
- **Ablation discipline**: one variable per experiment; a combined change is only shipped
  after each part is individually justified.
- **Caching** by `app_sha` so re-grades are free and comparisons use identical generations
  where possible.
- **Regression guard**: the existing `RUN_EMBED_TESTS` corpus separability test stays in
  CI; add a soft check that no dimension's Tracer score drops > noise band vs the last
  accepted run.

---

## 7. Secrets & wiring checklist (who does what)

| Item | Where | Who | Needed for |
|------|-------|-----|-----------|
| Merge branch → `main` (via PR) | Polyphony | user approves | makes `evals.yml` dispatchable + improved prompts testable |
| `GEMINI_API_KEY` (Actions secret) | Polyphony repo | user | CI boot-mode eval (app under test) |
| `TRACER_INGEST_TOKEN` (Actions secret) | Polyphony repo | user | ingest runs to Tracer |
| `EVAL_ADMIN_EMAIL` / `EVAL_ADMIN_PASSWORD` (Actions secrets) | Polyphony repo | user | **prod baseline** via external `base_url` mode |
| `ANTHROPIC_API_KEY` (optional) | Polyphony repo | user, later | non-Gemini judge (removes self-preference caveat) |
| Dispatch `evals.yml` (before/after) | GitHub Actions | me or user | produce the Tracer runs |
| Provide a Gemini key **in-session** (optional) | dev sandbox | user | unlock the fast **offline** extraction/outline loop locally |

**Minimum to start P1:** the two Actions secrets + the merge. **To make iteration fast:**
an in-session Gemini key (offline loop) or accept CI-paced cycles.

---

## 8. Risk register

| Risk | Mitigation |
|------|-----------|
| Overfitting prompts to Dracula | Held-out second corpus (A5); accept only on both. |
| Judge self-preference (Gemini judging Gemini) | Flag surfaced; lean on deterministic proxies; swap judge when a key exists (A6). |
| Free-tier quota exhaustion | Cache by sha; deterministic-first; batch experiments; offline loop for extraction/outline; cap N. |
| Generation variance masking/faking gains | N=3 + noise band + deterministic graders (§6). |
| A prompt change helps one dim, hurts another | Full-suite compare every accept; regression guard. |
| CI boot flakiness (fastembed download, app boot) | Mirror greenlight-build's proven pgvector+fastembed setup; health-poll with timeout. |
| Prod pollution from eval user | Throwaway invite-registered user; eval never touches other users' data (built-in). |
| "Improvement" that doesn't reach users | P7 deploys the accepted prompts to prod and re-measures prod in Tracer. |

---

## 9. Exit / definition of done

- Every dimension ≥ its §0 target **or** two consecutive dead experiments (documented).
- Gains confirmed on **both** corpora and **ingested to Tracer** as a visible rising trend.
- Improved prompts **deployed to prod** (P7) and prod re-measured to prove the line the
  users actually get.
- A short **before/after write-up** (Tracer run links + the accepted-experiment log) so the
  improvement is legible, not just a number.

---

## 10. Immediate next actions (unblock order)

1. **(user)** add `GEMINI_API_KEY` + `TRACER_INGEST_TOKEN` to the Polyphony repo;
   optionally `EVAL_ADMIN_*` for a prod baseline; optionally paste a Gemini key in-session
   for the fast offline loop.
2. **(me)** open the PR for the current branch (loop + 4 shipped improvements + lint fix).
3. **(me, on merge + secrets)** build accelerators A2–A5 (offline harness, determinism,
   N-repeat, second corpus), dispatch P1 baselines → Tracer, fill §0.
4. **(me)** run P2→P6 sprints per the loop protocol, ingesting each accepted step.
5. **(me)** P7: deploy improved prompts to prod, re-measure, write the before/after.
