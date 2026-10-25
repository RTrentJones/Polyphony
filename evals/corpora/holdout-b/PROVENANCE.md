# Provenance — holdout-b: The Moonstone (renamed HOLDOUT corpus)

## Status: frozen holdout (SPEC-94 S5)
This is a **holdout corpus** (`spec.json` → `"split": "holdout"`), frozen at tag
**`holdout-v1`** (the corpus files and the build script). It is never read by a
development run: the corpus readers refuse it unless a run asks for it by name
(`python -m evals.run --book holdout-b --holdout`), and it runs **once per release,
on that release's frozen code** (D19-3, D19-4). After the build's mechanical checks
(names renamed, no original name left, word counts, structure) nobody opens the
excerpts, and a holdout result is never tuned for: that would make this a
development corpus. The directory name is opaque because the eval book's title
(`eval-<corpus>`) reaches the model.

## Source work
- **Title:** *The Moonstone*
- **Author:** Wilkie Collins
- **First published:** 1868
- **Copyright status:** **Public domain in the United States** (published before 1929).
- **Text source:** Project Gutenberg eBook #155 (`gutenberg.org/cache/epub/155/pg155.txt`,
  read 2026-10-25). `evals/tools/rename.py` strips the Project Gutenberg header, license and
  trademark references (`strip_gutenberg`) before any text is used.

## What lives here
- **`excerpts.txt`** — a **renamed derivative** of six single-narrator chapters (about
  112k characters, 20.2k words), not the whole novel:
  `python -m evals.tools.rename --book holdout-b --source <pg155.txt>`.
  Each narrative restarts at CHAPTER I, so `aliases.json` cuts explicit `spans` and
  heads each with its own excerpt chapter:

  | excerpt | source section | narrator (renamed) |
  |---|---|---|
  | CHAPTER I | First Period, CHAPTER II | the house-steward, Amos Pennock |
  | CHAPTER II | First Period, CHAPTER X | Amos Pennock |
  | CHAPTER III | Second Period, First Narrative, CHAPTER I | the pious cousin, Miss Sneyd |
  | CHAPTER IV | Second Period, Second Narrative, CHAPTER I | the solicitor, Mr. Hedley |
  | CHAPTER V | Second Period, Third Narrative, CHAPTER I | the cousin, Lewis Tremayne |
  | CHAPTER VI | Second Period, Third Narrative, CHAPTER II | Lewis Tremayne |

  The narratives' "Contributed by …" lines are not copied; each narrator is known from
  the prose.
- **`aliases.json`** — the rename map (56 entries), the `spans` and the `sections`.
- **`spec.json`** / **`ground_truth.json`** — cast (10), a whole-novel synopsis, six
  canonical beats, two continuity injections inside the 12,000-character window, and
  gold voice lines for the four narrators (8 train / 3 test each, `max_lines` 11):
  `python -m evals.tools.build_gt --book holdout-b`.

## Names
Every character, house, place of the story and the home county is renamed (the
house-steward → Amos Pennock, the heiress → Isabel Castlemere, her cousins → Lewis
Tremayne and Cecil Mortlake, the detective → Sergeant Pellow, …). So are the gem's title
name (→ the Larkstone), the siege and shrine it came from, and the book the house-steward
reads for guidance (→ *Jonas Harrow*), since each is a strong cue. Real places, people and
nations (London's streets, India, William Cobbett) keep their names. Two cast first names
(Tabitha, Josiah) never occur in these chapters; the gold cast uses them and the grader
matches on the surname. Renaming reduces name-cued recall; it does not defeat
memorization of a famous novel (SPEC-94 S3), which is why this corpus measures the *gap*
between development and holdout books of the same kind, not memorization.

## Legal summary
The underlying work is US public domain; the Project Gutenberg wrapper is stripped; only a
renamed derivative of six chapters is committed — never the raw source, never the whole
novel.
