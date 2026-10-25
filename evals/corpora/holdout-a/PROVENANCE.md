# Provenance — holdout-a: The Woman in White (renamed HOLDOUT corpus)

## Status: frozen holdout (SPEC-94 S5)
This is a **holdout corpus** (`spec.json` → `"split": "holdout"`), frozen at tag
**`holdout-v1`** (the corpus files and the build script). It is never read by a
development run: the corpus readers refuse it unless a run asks for it by name
(`python -m evals.run --book holdout-a --holdout`), and it runs **once per release,
on that release's frozen code** (D19-3, D19-4). After the build's mechanical checks
(names renamed, no original name left, word counts, structure) nobody opens the
excerpts, and a holdout result is never tuned for: that would make this a
development corpus. The directory name is opaque because the eval book's title
(`eval-<corpus>`) reaches the model.

## Source work
- **Title:** *The Woman in White*
- **Author:** Wilkie Collins
- **First published:** 1859–60 (serial), 1860 (volume)
- **Copyright status:** **Public domain in the United States** (published before 1929).
- **Text source:** Project Gutenberg eBook #583 (`gutenberg.org/cache/epub/583/pg583.txt`,
  read 2026-10-25). `evals/tools/rename.py` strips the Project Gutenberg header, license and
  trademark references (`strip_gutenberg`) before any text is used.

## What lives here
- **`excerpts.txt`** — a **renamed derivative** of five single-narrator sections (about
  131k characters, 23.6k words), not the whole novel:
  `python -m evals.tools.rename --book holdout-a --source <pg583.txt>`.
  Collins restarts his numbering per narrator and heads sections with bare numerals,
  so `aliases.json` cuts explicit `spans` and heads each with its own excerpt chapter:

  | excerpt | source section | narrator (renamed) |
  |---|---|---|
  | CHAPTER I | First Epoch, Hartright's story, IV | the drawing-master, Edwin Calloway |
  | CHAPTER II | First Epoch, Hartright's story, VI | Edwin Calloway |
  | CHAPTER III | First Epoch, Gilmore's story, I | the solicitor, Matthew Hollins |
  | CHAPTER IV | Second Epoch, Marian Halcombe's diary, II | Harriet Lisle |
  | CHAPTER V | Second Epoch, Frederick Fairlie's narrative (whole) | Lionel Wendover |

  The narrators' title lines ("THE STORY CONTINUED BY …") and the editor's footnotes
  are not copied; each narrator is known from the prose.
- **`aliases.json`** — the rename map (42 entries), the `spans` and the `sections`.
- **`spec.json`** / **`ground_truth.json`** — cast (9), a whole-novel synopsis, six
  canonical beats, two continuity injections inside the 12,000-character window, and
  gold voice lines for the four narrators (8 train / 3 test each, `max_lines` 11):
  `python -m evals.tools.build_gt --book holdout-a`.

## Names
Every character, house and home county is renamed (the drawing-master → Edwin Calloway,
the half-sisters → Harriet Lisle and Clara Wendover, the baronet → Sir Roderick Ashby, the
Count → Count Morandi, the asylum fugitive → Ellen Pryor, the uncle → Lionel Wendover, the
two houses → Thornholm and Greymere, …). Real places, people and nations (London's
streets, Italy, Henry the Eighth, Napoleon) keep their names. Two cast first names
(Lionel, Matthew) never occur in these sections; the gold cast uses them and the grader
matches on the surname. Renaming reduces name-cued recall; it does not defeat
memorization of a famous novel (SPEC-94 S3), which is why this corpus measures the
*gap* between development and holdout books of the same kind, not memorization.

## Legal summary
The underlying work is US public domain; the Project Gutenberg wrapper is stripped; only a
renamed derivative of five sections is committed — never the raw source, never the whole
novel.

## Ground truth checked against the full source (wave 19, before the freeze)
Every claim in `spec.json` (synopsis, beats, chapter sources, narrators, injection notes)
was located in the full Project Gutenberg text by phrase search, not from memory: the
asylum escape and the midnight walk, the half-sisters and their uncle, the likeness,
the engagement and the expedition abroad, the settlement, Greymere in its county, the
Count's wife as the heroine's aunt, the signature scene, the death certificate and the
tombstone, the asylum, the nurse bribed, the parents never married, the vestry fire,
the Paris morgue, the marriage and the return home. Each span's start lies inside its
narrator's section of the source. One correction: in the signature scene the baronet
demands the signature and the Count persuades him to postpone it, so the third beat
and the synopsis no longer say the two of them press her together. The synopsis and
first beat also stopped quoting the novel's title phrase (SPEC-94 item 2).
