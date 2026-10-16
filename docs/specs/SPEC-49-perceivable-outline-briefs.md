# SPEC-49 — The outline writes briefs its POV character can perceive

**Closes**: W6-4 (a)(b)(c); W6-6 (a) for the outline and plan_beats; wave-5 code-gate Coulds 1
and 10a · **Size**: S · **Depends on**: wave-6 day-0 (`json_utils.JSON_OBJECT`).

## The outline:beats prompt (D9)

It adds "Write each scene summary as what the chapter's POV character witnesses; never what they
fail to notice or will later learn." and "Do not reuse a chapter title as a scene title." (the
whole-book block already lists every title). One call plans chapters with different POVs, so the
line names no one. The permission to plan another's hidden intent stays in `pov.py` alone.

## The audit (warn-only)

`audit_outline` adds one warning: `POV_LEAK_WARNING`, then `chapter N "scene title" ("phrase")`
for each scene (chapters numbered from 1 in outline order) whose summary matches, as whole words
and in any case, "does not see", "does not notice", "the reader", "will later", "would later" or
"cannot see". Chapter summaries are not checked (hidden intent belongs there). Fixed-input gate:
each of the six wave-5 outlines warns on its one leaky scene; W4 and W4b2 do not.

## People lists, JSON mode, the plan block

- `merge_structural` makes a chapter's `people` list the union of its own and its scenes' lists
  (W5b "Signature Sheet" gains Edric Thane; W4b2 is unchanged).
- Skeleton, chapters, beats and plan_beats send `response_format=JSON_OBJECT`; arrays are asked
  for as `{"items": [...]}`, which `extract_json_array` unwraps.
- An unplanned scene in a planned chapter gets the list under "This chapter's planned scenes, in
  order:", with no `→` (Could 1). `marked_plan` moved to `app/planning/planned_beat.py` (Could 10a).
