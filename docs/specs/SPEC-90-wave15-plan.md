# SPEC-90 — Wave-15 plan: an honest Claude eval, then the extraction and ingestion it exposes
**Depends on**: SPEC-84, SPEC-89, BRD R7.9. **Approved** by the user on 2026-10-23 ("Approved, separate budget, and your
recommendation"). Results and counts land here as the wave closes.

## Why
Run 39920544701 (Frankenstein, `e1aba3d`, generator `claude-opus-5-5`, judge Groq) was the first eval to score every step;
the Gemini free tier never finished one. Two read-only investigations split its low scores in two:

- **Harness.** The Groq judge sometimes answers "no artifact/dialogue was provided" on input it was given (outline R3 = 0.0
  while R1 and R6 scored 1.0 from the same body; two prose scenes whose quoted lines `_QUOTE` does match). R2 also grades
  every quoted line, crew included, against the scene cast's voice only, and has no not-applicable path. Attribution's one
  probe is on two characters' topic, so The Creature ranks last on all 30 probes, its own included.
- **Product.** Extraction ranks a canonical name by token count (`canon_extractor.py` `_fold_group.rank`), so
  "Robert (not used)" outranks "Rowan Merrow" and "my dear sister" outranks "Isolde Sarrow"; nothing strips parentheticals;
  merge rule 3 lets "the stranger" join Emeric and Soraya. Ingestion reads a Victorian nested narrative (each paragraph
  opens a quote, only the last closes) as one uncredited speech and withholds it, so Fenwick and Alina get 0 chunks; the
  pronoun alias "I" inflates The Creature. Continuity misses an era flip in the first chunk (each chunk sees only its
  predecessor's tail; the fact sheet has no era).

## Items
| # | Item | PR |
|---|---|---|
| W15-0 | Eval budget guard: a `claude=yes` run sums this month's eval spend from earlier runs' report artifacts and caps itself at what is left of the monthly budget | A |
| W15-1 | Pre-fix Claude draw, all three books, at `a52817c` (`app/` = `e1aba3d`) | — |
| W15-2 | A judge answer claiming nothing was provided is retried once, then recorded as `None`, never 0; outline R3 gets a body that labels PLAN and TEXT | A |
| W15-3 | R2 grades only lines it can attribute to cast speakers; not applicable (`None`) under two cast speakers with samples; the report keeps the spoken lines | A |
| W15-4 | Attribution scores normalised by each centroid's own baseline similarity (topic control) | A |
| W15-9 | The panel-memo (F) row owed since wave 14 | A |
| W15-5 | Extraction names: `name_core()` (parentheticals and leading determiners/possessives stripped; no capitalised token = descriptor), rank on proper-name tokens, rule 3 links only on proper-name tokens, map prompt tightened; BRD R4.4 | B |
| W15-6 | Nested-narration ingestion; pronoun aliases rejected | B |
| W15-7 | Continuity era ledger: a deterministic pass over the whole source flags date outliers | B |
| W15-8 | Post-fix Claude draws, two per book, against W15-1 regraded under PR A | — |

## Decisions
- **D1 Budget.** Evals get **$40/month**, separate from the $50 prod ceiling (the user's choice). Each eval run boots an empty
  database, so the app's ceiling cannot see the month; W15-0 does. One book costs about $2.60 with Opus. The Anthropic
  console's spend limit is the hard backstop. Per-run caps are no longer the control; the monthly total is.
- **D2 Measure before fixing.** W15-1 runs before any change, so a better score after can be attributed.
- **D3 Harness before product.** PR A merges first and W15-1 is regraded under it, so PR B is judged by an instrument that
  does not zero scores at random.
- **D4 Groq stays the judge.** A Claude judge would grade Claude's own writing (self-grading, which the harness already
  labels). If "nothing provided" persists after W15-2, raise Groq's reasoning effort to medium (free) next.
- **D5 Opus stays the generator.** Sonnet halves the cost but changes the model under test; a Sonnet comparison is its own draw.
- **D6 Comparability.** R2 and the outline score change meaning; `evals/history.csv` rows before PR A are marked as graded
  by the old harness. The act rule's `r2_mean` is reported, never gated (SPEC-58). Nothing frozen by SPEC-75 or SPEC-84
  changes, and the W12-9 panel (P⁗) is untouched.
- **D7 Names in code, not only the prompt.** The descriptor ranking would win against any model that emits one descriptor.
- **D8 Still deferred:** W14-9, W14-12 (N1/N2), W14-14. None shows in the Claude scorecard.
- **D9 Counts.** Test backstop **300** lines for the wave (the user took the recommendation): PR A ≈ 140, PR B ≈ 160.
  Product cap **160** lines (PR B's three fixes; PR A changes `evals/` and YAML only).
- **D10 Codex.** Per the user (2026-10-23), small targeted fixes merge on a green gate and CI without waiting for a
  Codex re-review; findings that arrive later are still answered.

## PR A review (coordinator's decisions, 2026-10-23)
- **B1.** "Nothing provided" is a claim only at a score <= 0.1 with the tight, word-bounded phrase; three false-positive sentences are test rows.
- **B2.** Each attempt uploads its own report artifact with `eval-ceiling.json`; a run killed before usage counts at its ceiling; Claude runs share one never-cancelled concurrency group.
- **N1.** Reports record `mode` (boot | external); the budget counts boot-mode reports only.
- **N2.** Attribution's score stays the raw accuracy; `normalised` is reported beside it, baseline = mean similarity to the other characters' generated lines.
- **N3.** Prose reports `r2_applicable` and `r2_lines` beside `rubric_means`.
- **N4.** Consecutive spans by one speaker in a paragraph join into one R2 line.
- **N5.** The guard refuses under $3 left (one book); report artifacts are kept 90 days.

## W15-1 (run 40030061261, `a52817c`, old harness) and what it changed in PR A
| book | extraction | ingestion | retrieval | outline | continuity | prose (R2) | revise | attribution |
|---|---|---|---|---|---|---|---|---|
| dracula | 0.480 | 0.750 | 0.818 | 0.500 | 1.000 (fpr 0.5) | 0.766 (0.10) | 0.838 | 0.533 |
| frankenstein | 0.571 | 0.429 | 0.815 | 0.200 | 0.500 | 0.781 (0.175) | 0.875 | 0.567 |
| cel (outline only) | — | — | — | error | — | — | — | — |

Spend **$5.66** (deployment-wide). Two more PR A fixes from it:
- **The guard read the wrong field.** `month_cost_usd` is the report's own user, and each book registers a new one
  (this run: 2.52 of 5.66); the guard now reads `deployment_cost_30d_usd`, which is what the app's ceiling counts.
- **cel's outline judge got a 413** (Groq free tier: one request over its 8,000 tokens-per-minute limit; Claude's outline
  is longer). A 413 now fails over to the next judge in the chain, as a retired model's 404 does; the report labels
  the judges that graded.
W15-1 is regraded under PR A by re-running it at PR A's merge with `app/` unchanged (a judge regrade of a stored
artifact is not possible: the report does not keep the scene text before PR A).

## PR B review (coordinator's decisions, 2026-10-23)
- **B1.** Rule 3 links two candidates on a shared alias with at least one proper word and two proper-or-particle words, so
  "Van Helsing" and "Mr. de Winter" link and "the stranger" / "the young Englishman" do not (the reviewer's significant-word
  rule would have fused Harker and Holmwood).
- **B2.** An open-quote run is embedded narration only after a heading or chapter start, a letter or document header, or a
  paragraph ending in ":" or a dash; otherwise it stays speech carried to its setup speaker. A narrated span with an inner
  ‘…’ quote of 3+ words stays speech. Dracula's manner-sweep pin moves 145 → 111 speech paragraphs (its letters).
- **B3.** The era ledger counts a year only in a date context, skips counts and currency, drops ordinal centuries, needs
  ≥5 dates at ≥75%, and reports `minor`. It cannot fire in the eval's 12,000-character continuity window (two dates); it
  is unmeasured by the eval (D11).
- **N1/N2/N5** fixed (elided and caseless names count as names; "The Doctor" keeps its article; only a paragraph-initial
  unclosed quote is open). **Known limits:** "bell hooks" loses to a capitalised descriptor; a being named "It" is dropped
  as a pronoun; a bracketed nickname is stripped.
- **Wave 16:** Rowan's 60 chunks (the narrator role takes other narrators' first-person paragraphs) and an embedded
  narrator's tale credited to nobody (D12).
- **D9 amendment.** Product cap for PR B raised 160 → 200 (it measures +189); tests +171 for PR B.
- **D9 second amendment (Codex on PR B).** Two regressions Codex found are fixed with a row each: an embedded narrative
  now sets later-plot boundaries (`narrations_of`), and "May 1, 1797," counts as a date. Tests measure **+323** net for the
  wave; the user raised the backstop to **330** on 2026-10-24 ("Raise to 330"). Product **+202** added (cap 200 → 202).
- **D14 Judge fallback.** Per the user (2026-10-24): "Use anthropic for judge after others are consumed." Run
  40042289933 died when Groq's free daily token cap hit (a `QuotaExhaustedError`, 429 tokens per day). A spent daily cap
  (the ledger's `DailyRequestLimitError`, or a 429 naming a per-day limit) now fails over to the next judge, as a 404/413
  does; a per-minute 429 keeps its retries. With claude=yes the chain is `groq,openrouter,anthropic`: Claude judges last,
  with its fast model `claude-sonnet-5-5`, and is labelled self-grading (`self`/`mixed`) when Claude is also under test.
  The judge's spend (`judge_cost_usd`, summed over its graded calls) is reported; it already counts toward the $40 eval
  budget (D1), because the harness shares the run's `DATABASE_URL` and records each call in `api_usage`, which
  `deployment_cost_30d_usd` sums (Codex P2 on PR #58: adding it again in `evals/spend.py` double-counted).
- **D9 third amendment (D14).** The Claude-judge fallback adds 27 net test lines (25 once the double count came out); the
  wave measures **+348** and the user
  raised the backstop to **350** on 2026-10-24 ("Raise to 350").
