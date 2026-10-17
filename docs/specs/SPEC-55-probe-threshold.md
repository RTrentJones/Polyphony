# SPEC-55 — The voice probe sees each speaker's own best lines

**Closes**: W7-3 (Q9, Q14) · **Size**: S · **Depends on**: SPEC-17, -40, -52.

## Design
- The probe (`dialogue.generate_dialogue` → `character_samples`) passes `threshold=0`. Beat prose,
  revise and the ensemble agent keep `settings.RAG_SCORE_THRESHOLD` (0.2), which is unchanged.
- **0 means "score ≥ 0", not "no threshold"** (D5). A row with a negative cosine still drops, which
  is intended for an off-topic line. The store's fallback (the closest eligible rows when none
  pass, SPEC-40) is unchanged.
- **Why:** the probe asks for a voice, not a moment, so a speaker's own lines score low against it.
  At 0.2, Edric saw only "Every clerk likes a clean ledger…", a line the classifier reads as Milo
  (SPEC-52).

**Row** (fixed input: the attribution store's cosines, `e1-edric-probe.md`): the probe shows Edric
3 of 3 lines and Zara 3 of 4 (k = 3 caps `k_voice`); beat prose, at 0.2, still shows each of them 1.
**Tests**: `test_dialogue_probe.py`, `test_wave7_contracts.py` (the builders pass the setting; the
probe passes 0).
