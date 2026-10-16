# SPEC-53 — The narration around a line is movement

**Closes**: W7-1 (a), (d) · **Size**: M · **Depends on**: SPEC-17, -30, -40, -52.
## Design
- **Rule** (`_paragraph_indexer`, D1). A speech paragraph not indexed whole (the member speaks in it, or long uncredited speech blocks it) also gives the member a **`paragraph_{i}`** chunk of its **tag-less narration** (`_tagless_narration`: quoted spans and `tag_spans` clauses removed, lone punctuation dropped), typed by `_classify_chunk`, when (i) **no span is credited to someone else**; (i′) **with an uncredited span** (a pronoun tag included), only narration whose subject is the member: `_beat_subject` of its first sentence (review B-1; "she said, pushing the folder toward Milo" is not Milo's), while every span the member's own keeps it (p4's participle); (ii) it keeps **≥ `SPEECH_MIN_WORDS` + 1** words (a bare "Zara said." is no manner). Never `narration_{i}`; voice chunks and the speech rule (SPEC-17, wave-1 B2) are unchanged; the name gate still picks the paragraphs.
- **No index-time `_centres`** (review B-1): retrieval's `_centres`, `names_outside_scene`, `restates` and the later-plot cut decide who is shown a chunk. No gender rule (B-2). No dracula ground-truth change (S-2): the aliases below are stop-gate inputs, "Count" (an honorific) excluded.

## §1.5 stop gate: **pass** (chapter-1 scene filters | gold D3a, placed by `plot.locate`; `w6_archives.json`)
| Principal | W5d | W6 | W6b |
|---|---|---|---|
| Milo | p4 \| p4 | p4 \| p4 | p4 \| p4 |
| Zara | p2, p11 \| p2 | p2, p11 \| p2 | p2 \| p2 |
| Edric | — \| — | — \| — | — \| — (named in no ch-1 scene; past every cut) |

Cuts p14 / p14 / p9, equal to the gold tails. p11 narrates `bell` (gold, ch 1); on W6b it is past the cut. The day-0 store shows no principal anything. Ruling: all three engineers launch.

## Movement chunks indexed
| cel | Day-0 | D1 | New |
|---|---|---|---|
| Milo / Zara / Edric | 5 / 2 / 2 | 8 / 4 / 3 | p4, p15, p19 / p2, p11 / p20 |

| dracula | Eval cast (no aliases): day-0 → D1 | Script first-name aliases: day-0 → D1 |
|---|---|---|
| Cora Ellis / Count Vasska | 0 → 0 / 1 → 1 | 26 → 27 / 1 → 1 |
| Aldous Kerr / Elias Ward / Verhoeven / Nora Vance | 0 → 0 / 1 → 1 / 12 → 12 / 0 → 0 | 5 → 5 / 1 → 1 / 12 → 12 / 1 → 1 |

Backlog target Cora ≥ 20 met with the alias; Count stays 1 (S-2). **Plan risk 2** (the narration around another speaker's pronoun-tagged line: Aldous +14, Elias +20, Cora +12, Verhoeven +8 at `776cabf`) is closed by (i′). Voice chunks unchanged on both books. **Tests**: `test_dialogue_attribution.py`: D1's rows (p4, p11, p20, p2, p5) through `character_samples`; the stop-gate rows on W5d/W6/W6b (and W6b's p11 cut); a span credited to someone else blocks, and a pronoun tag's narration is not given to whom it names (the clerk, (F) the folder); the cel voice chunks pinned at 231087f.
