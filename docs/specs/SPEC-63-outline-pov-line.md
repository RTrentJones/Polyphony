# SPEC-63 — One POV line for the whole outline

**Closes**: W8-5 (a) (B-2) · **Size**: S · **Replaces**: SPEC-49's scene-summary sentence.

`staged_outline.OUTLINE_POV_LINE` — "Write each summary as what the chapter's POV character
witnesses or learns; never what they fail to notice or will later learn." — is printed by the
chapters stage (after the cast rules) and the beats stage (where SPEC-49's sentence was). It names
no one: each call plans several POVs. `pov.py` stays the one place that says what a brief may carry
about other people; `_POV_LEAK` and `audit_outline` are unchanged. Written against W7br ch 3
("in the corner office the Lich notices a query"). **Tests**: `test_staged_outline.py`.
