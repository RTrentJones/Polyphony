# SPEC-02 — Continuity v2: whole canon, no silent cuts, honest status

**Closes**: F2, eval-audit backlog items 3 and 7 · **Size**: M · **Depends on**: —

## Why

`check_chunk` sends `fact_sheet[:4000]` (`app/planning/continuity.py:61`), which with a real
synopsis cuts the cast and every chapter summary. `_build_fact_sheet` (`app/api/plans.py:521`) is a
second, thinner canon renderer that omits canon entries, style, arc, relationships, traits and
voice. Prose is not fenced. A parse failure becomes `[]`, so a check that never ran reports clean.
Findings are not deduplicated and contradictions between two prose chunks are invisible.

## Outcome

Continuity checks the prose against the same `Canon` every other generator uses, plus the
book's plot state (open threads, chapter summaries), with nothing truncated. A chunk that could
not be checked is reported as such, and the report's status says whether the check was complete.

## Scope

**In**: `app/planning/continuity.py`, `_build_fact_sheet`, the continuity job handler, the
`ContinuityReport` status vocabulary, the continuity panel's status display.
**Out**: a claims database or structured fact extraction (eval plan C1/C3) — possible follow-up.

## Design

1. **Fact sheet = Canon + plot state.** Replace `_build_fact_sheet` with
   `build_fact_sheet(session, book_id) -> str` in `app/planning/continuity.py` that renders
   `canon.full_block()` followed by a fenced `<plot_state>` block (open threads with their events,
   chapter summaries in order). Delete the copy in `plans.py`.
2. **No truncation.** Remove `[:4000]`. If `len(fact_sheet) + len(chunk)` exceeds
   `MAX_CANON_CHARS`, raise `TextTooLargeError`; the job records the error on the report (it does
   not silently pass).
3. **Fence.** The prompt opens with `STORY_MATERIAL_NOTICE`; prose is `as_quoted_block(chunk,
   "prose")`; the fact sheet is already fenced.
4. **Rolling context across chunks.** Each chunk's prompt also carries a short fenced
   `<earlier_findings>` list and the last ~300 words of the previous chunk, so a contradiction
   that straddles a chunk boundary or contradicts earlier prose is detectable. (This is the
   minimal form of cross-chunk comparison; it keeps the map-reduce shape.)
5. **Honest status.** `check_chunk` returns `(findings, tokens, ok: bool)`. A parse failure retries
   once with `response_format={"type": "json_object"}` wrapping the array as `{"findings": [...]}`;
   if that fails, `ok=False`. The report gets `status = "completed" | "incomplete" | "failed"` and a
   `chunks_checked / chunks_total` pair in a new `coverage` JSON column. The UI shows "Checked 7 of
   8 sections" rather than a clean bill of health.
6. **Dedup.** Merge findings whose `(type, normalized detail)` match, or whose `refs` overlap,
   keeping the highest severity.
7. **Pause visibility.** Register `on_pause` for `continuity_check` (status `paused`), per SPEC-07.
8. **Preflight.** Estimate `chunks × tokens(fact_sheet + chunk)` before enqueue; refuse with the
   estimate when it exceeds the remaining budget, suggesting a chapter-scoped check.

## Acceptance

1. With a 20k-char synopsis and five characters, the assembled continuity prompt contains the
   synopsis tail and every character's arc and relationships. *(unit, stubbed client)*
2. A stubbed client returning unparseable JSON twice for one of three chunks yields a report with
   `status="incomplete"` and coverage `2/3`, never `completed` with `[]`. *(unit)*
3. Two identical findings from adjacent chunks appear once in the report. *(unit)*
4. No slice of author content remains in `continuity.py`. *(unit grep check)*
5. The SPEC-06 continuity eval step keeps detection recall at or above baseline and its
   false-positive rate within the noise band, on both corpora.

## Docs

Feature set §3 "Continuity checking" note updated; eval-audit backlog items 3 and 7 marked closed.
