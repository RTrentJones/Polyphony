# SPEC-89 — Wave-14 record: the rating panel, Flash-Lite default (U1), measured provider fixes
**Closes**: W14-0 to W14-8, W14-10, W14-11 · **Depends on**: SPEC-84, SPEC-88. Rating results land in SPEC-84 once `unrated` = 0.
- **Counts** (net against `e428c77`; vitest now counted as tests, which wave 13 did not): product **+120** (cap 115 → 120 at the code
  gate), tests **+370** (= backstop), YAML **+40**. One baseline cost ≈ 946 test lines over waves 13–14 (panel 178 deferred from 13).
- **Rating at P** = `9312365` (`app/` = `e428c77`); every rate dispatch checks out P. **After P:** `panel.py:94` rewritten without a
  backslash in an f-string (Python 3.11 SyntaxError; output identical), so main's `panel.py` blob differs from the rating's.
- **Deferred to wave 15:** W14-9 (estimate gate; PM-signed, no author hits it), W14-12 (N1/N2 rows), the client's two rules for the
  book's model (N2/N4), `_reason`'s split on `" - "` (N1), `run.py`'s label for external apps (N2), W14-14 outline-JSON diagnosis.
- **Deploy check (N5):** before PR-14's prod deploy, prod had no queued or paused scene-writing job (recorded at deploy).
