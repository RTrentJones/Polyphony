# SPEC-13 — Versioning completeness and a trash

**Closes**: F16 · **Size**: M · **Depends on**: SPEC-04 (scenes carry `book_id`)

## Why

Invariant 3 says nothing the author wrote is destroyed, and four paths still destroy it. Source
content edits overwrite in place. Chapter titles and summaries are edited without history.
Deleting a character deletes its voice chunks before the row, so a restore brings back the bible
without the voice. And a deleted entity can be restored only by someone who already knows its ID,
because nothing lists deleted entities.

## Outcome

Sources and chapters are versioned like other canon. Deleting a character, canon entry, source,
chapter or scene moves it to a per-book Trash from which it can be restored with everything it
owned, including voice. Permanent deletion is an explicit, separate action.

## Scope

**In**: `entity_versions` types `source` and `chapter`; soft delete for characters, canon entries,
sources, chapters and scenes; a Trash view; voice-chunk preservation on character delete.
**Out**: pruning (BRD §8); versioning individual chunks.

## Design

1. **New versioned types.** `source` (title, kind, content_text, content_hash) and `chapter`
   (title, summary, status, position, plan_node_id). Snapshots on create, edit and restore through
   the existing repository and `_FIELD_ENTITIES` table. Restoring a source's content offers to
   re-run extraction rather than doing it automatically.
2. **Soft delete.** Add `deleted_at` to `characters`, `canon_entries`, `sources`, `chapters` and
   `scenes`. All list and generation queries filter `deleted_at IS NULL` (centralise this in the
   `_owned_*` helpers and `build_canon`). Delete endpoints set `deleted_at` and append a version with
   `reason="deleted"`. Unique constraints on `(book_id, name)` become partial on
   `deleted_at IS NULL` so a deleted name can be reused.
3. **Voice survives soft delete.** Character soft delete leaves `voice_chunks` in place; retrieval
   already scopes by `character_id`, and the deleted character is excluded from casting. Permanent
   deletion removes chunks inside the same transaction as the row (fixing the cross-session order
   noted in F20).
4. **Trash.** `GET /books/{id}/trash` lists soft-deleted entities with type, name, deleted_at and
   what restoring will bring back ("12 voice samples", "3 scenes"). `POST .../restore` clears
   `deleted_at` and appends a `restored` version. `DELETE .../purge` deletes permanently after a
   typed confirmation in the UI. A deleted chapter's scenes are soft-deleted with it and restored
   with it.
5. **Retention.** Purge happens only on explicit action. A setting for automatic purge after N days
   may be added later and is not part of this spec.

## Acceptance

1. Editing a source's content twice yields versions 1–3 and restoring v1 appends v4 with v1's text.
   *(unit)*
2. Deleting and restoring a character brings back its bible fields and its voice chunks, and
   retrieval for it returns the same results as before deletion. *(Postgres integration)*
3. A soft-deleted character is absent from `build_canon`, the cast picker and fidelity principals.
   *(unit)*
4. A new character can take the name of a soft-deleted one; restoring the old one then returns 409
   naming the conflict. *(unit)*
5. Purging a character removes its chunks and row in one transaction. *(integration)*

## Docs

BRD R4.1 lists sources and chapters; a new R4.9 "deletion is soft and restorable from a Trash until
explicitly purged". ADR-002 §5 note on soft delete. Feature set §5 rows updated.
