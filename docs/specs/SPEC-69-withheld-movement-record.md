# SPEC-69 — The withheld movement record and the plan-carried gate (W9-1)
**Closes**: W9-1 (a)–(d), the W8c misfire · **Size**: M · **Depends on**: SPEC-45, -54, -62, -66.
- **Record (a, E2).** A movement line `context._withheld_by` rejects is stored in grounding with its rule, and the grounding
  API lists it under the character group's `withheld` (`text` from the live chunk; omitted when empty).
- **Export (b).** The prose step reads each scene's grounding body into `steps.prose.voice.withheld` =
  `[{beat, owner, lines: [{text, rule}]}]`. The grader matches `beat` to the one beat_prose record whose BEAT TO WRITE line
  equals it after collapsing whitespace, and `owner` through the principals' aliases. Two matches raise; none is counted in
  `movement.unmatched_withheld`. Credit is never inferred: no row, no credit.
- **Carried (c).** An eligible block of owner O with no line shown is `carried` when the matched row holds **every**
  grader-eligible store line of O (a line `plot.only_plot` does not call plot here) with the product's "restates the plan"
  rule, and none of those lines is in an event's `narrated` list. Carried also needs the line's stemmed content to be at
  least half in this prompt's beat line and brief (`CARRIED_SHARE`, B-1), because `restates` runs over the whole book's plan.
- **Gate (`ok`).** (i) each principal cast in a chapter-1 block has a block shown or carried; (ii) ≥ 0.8 of the remaining
  eligible blocks move. All carried: `rate` null, `all_carried` true, (i) decides. n = 0 with nothing carried fails.
- **Reasons (d).** A starved block reads "O: eligible, withheld (<rule>)" when the row holds an eligible line, else "O: eligible,
  no movement line". `movement.per_principal` = `{name: {shown, carried, starved}}`; the scorecard prints it on one line.
- **W8c chain (D10).** E2's product test writes `w8c_withheld`; read as the export, Zara's four blocks are carried, `ok` true.
- **16-archive regrade (G3)**, 841c98b vs after (W8r from its no-ensemble copy): every value identical but the three new
  reported keys. Movement: W4b2, W5, W5b null (no store); W5bc–W6b 0/0 false; W7 0/9 false; W7b 1/12 false; W7br 12/12,
  W7r 8/8, W8r 12/12 true; W8b 0/12 false; **W8c 10/14 false** (no signed archive holds an export; the flip is the fixture's).
