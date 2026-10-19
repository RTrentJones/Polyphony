# SPEC-79 — Waves 10+11 combined merge gate record (§6.2 draws W11 and W11b)
**Closes**: wave-11 §6, the wave-10 hold (SPEC-76) · **Size**: docs · **Depends on**: SPEC-69, -73 to -77.
- **Instrument (SPEC-74).** (1) Pre-registered: seed 11, 200 shuffles, p 0.1194, beats 88.5%: **not validated**, so run-W11a is skipped.
  (2) Post hoc, not the gate: other seeds p 0.114, 0.035, 0.090, 0.050, 0.070; pooled ≈ 92.5% (p ≈ 0.075). (3) Positive 22/30, Wilson
  0.56–0.86: pass. (4) Self-split, reported: Milo 4/11, Zara 1/6, Edric 2/5. (5) LOO 12/22, Wilson 0.35–0.73. (6) Condition 4 needs only
  the train-reference fix, which is in. (7) Wave 12: pre-register 2000 shuffles; the real bottleneck is 22 gold lines.
- **Draws on 875e129**. W11 `gated-full`: extraction 0.741, ingestion 0.833, retrieval 0.381, outline 0.827, continuity 1.000, prose 0.873
  (R2 0.895), revise 0.909, contract 1.000. W11b `gated-scenes`: outline 0.765, prose 0.916 (R2 0.925), revise 0.894, contract 1.000.
  `exercised` rc 0. **Signed regrade, both:** movement 16/16; `samples_not_plot` 20/20, classified 84 / 74; `bell_sent_under` not
  contradicted. W11b's Zara: carried by own-scene `restates` in 0019–0022, shown in 0024–0027. `manner_sweep`: W11 0/0, W11b 1 gain / 0
  leaks; no `restates`-withheld speech beat that only another scene restates. P1 flips W10, W10b.
- **PM conditions.** (1) Sweep: G-S0 met (5 gains, 0 leaks, W8c credited, W10b unchanged); the 20-archive `samples_not_plot` reading
  **awaits the principal's eval re-check**. (2) Chain: met as worded (all four W10 blocks). (3) Two draws: every row above passes;
  **awaits the principal's eval re-check**. (4) Train references: in. **G-D: read, not met** (0.50, n = 2, thin; ruling (b)-narrow).
- **Act rule (§6.3):** tighten RA means 0.82, 0.817, both < 0.85: **tighten is P0 in wave 12**; R2 (0.895, 0.925) does not fire (pair 10's
  R2 does: 0.837, 0.850). **Size** (net, §0 pathspecs): wave 11 vs edcd7ef +278 product, +310 tests, +96 docs; vs dea853d +599, +644, +165.
- **Wave 12:** ch ≥ 2 placement (rule, `placement_sweep`); `withheld_more` as its own field; sentence-adjacent beats; the own-narration
  exemption (PM's dracula events); narrator-path `paragraph_{i}`; a 0.85-bar third-person corpus sweep before non-cel gating; tighten P0;
  relay: cross-stage cast mismatch, non-cumulative revisions, briefs quoting the source against the no-quote rule, unmarked revise-context
  truncation, beat-summary POV leak, no synopsis in the outline source check; judge self-correction (W11b "Colour of Old Teeth" R5).
