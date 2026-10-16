# SPEC-43 — A scene's planner sees the rest of its chapter

**Closes**: W5-4 · **Size**: S · **Depends on**: wave-5 day-0 (`prose.book_plan`, `prose.earlier_beats`, `SceneSetup.plan`/`.earlier`, `CHAPTER_SCENES_HEADER`, `EARLIER_BEATS`).

## The chapter-plan block

`plan_scene_beats(..., plan, earlier)` takes `SceneSetup`'s `plan` and `earlier` from prose and
ensemble. `workflow.chapter_plan_block` prints, between `chapter_context` and `<scene>`:
`CHAPTER_SCENES_HEADER`, this chapter's planned scenes (`n == story_position.chapter`) as
`beat_scene_description` lines, `→` on the one whose `node_id` is the request's `planned_beat_id`;
then, when earlier completed scenes exist, `EARLIER_BEATS.format(what="the earlier scenes of this
chapter")` with one `- title: beat lines` line each, and "Keep the days, times and objects these
establish; do not introduce again what an earlier scene already introduced." No plan for the
chapter (or no story position): no block. The canon block is unchanged (R1.1).

**One renderer** (S-5): `workflow.marked_plan(header, items, current, earlier_header, earlier)`
renders this block and `outline_context` (byte-identical, pinned). **Also named**: the "may appear
by name" sentence is printed only with an Also-named list (alone it read as a missing list).

## Grader

`prompt_contract.marked_block(prompt, header, marks)` is the one parser of a `marked_plan` block
(items, marked indices, whether an earlier list follows); `marks` is the expected `→` count
(`None`: one or more). `beats_see_whole_outline` and the new **`plan_sees_chapter`** use it. The
check runs on plan_beats prompts whose scene description is a beat of the latest outline:beats
answer: every title of that beat's chapter is listed, exactly one `→`, and an earlier list follows
when `→` is not on the first scene. Archived baseline: 0/2 (W4), 0/3 (W4b2). Out of scope:
earlier scenes' prose (O-1), an in-story calendar.
