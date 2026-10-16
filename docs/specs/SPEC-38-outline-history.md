# SPEC-38 — An outline you can go back to, whose acts see the whole book

**Closes**: W4-5 · **Size**: S/M · **Depends on**: the wave-4 day-0 contracts (`OUTLINE_CHAPTERS_HEADER`, `Chapter.number`).

## History

- The Outline tab's **History** button (shown when a plan of the selected kind exists) opens the
  existing `VersionHistory` for `book_plan` with this plan's id: preview, and forward-only
  restore. After a restore the plans are refetched. Regenerating therefore never loses an outline.
- **Promoted chapters.** A node whose `node_id` is a live chapter's `plan_node_id` shows
  "Promoted · chapter N" (N is the chapter's server `number`) instead of the Promote button. This
  is one client lookup over the book's live chapter list; promote stays idempotent on the server.

## Whole-outline context

Each outline:beats prompt prints `staged_outline.outline_context(outline, act_chapters, earlier)`
between the act brief and the act's chapter JSON: `OUTLINE_CHAPTERS_HEADER`, every chapter in book
order (other acts' with their whole summary; this act's marked `→`, title only, since the JSON
below has them), the beat titles earlier acts already have (from the checkpoint's `done` on a
resumed job), and "Introduce each person no earlier than the chapter that introduces them." The
caller passes `act_chapters`, the act's chapters it already holds. Cost: 1.8-2.2k characters per
act on the run-W3 cel outline; the unit guard is ≤ 2,500 at 6 chapters.

`beats_see_whole_outline` checks each block against the titles of the latest outline:chapters
answer, not the union of every act's JSON: a repair pass or a regenerated outline has new chapters.
A resumed outline whose archive has no outline:chapters record checks only the header (SPEC-39).
