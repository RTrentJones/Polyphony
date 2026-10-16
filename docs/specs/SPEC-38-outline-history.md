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
