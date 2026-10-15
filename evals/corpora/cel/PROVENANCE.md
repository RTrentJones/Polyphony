# Provenance — CEL (synthetic fidelity corpus)

## Source work
**None.** *Bored to Undeath* is a **fully synthetic** work written for this
repository. There is no underlying novel, no public-domain source, and therefore
no licensing question and no rename step (contrast `dracula` / `frankenstein`,
which are renamed derivatives of public-domain texts). Every word in
`synopsis.txt` and `excerpts.txt` was authored here and is released under the same
terms as the rest of this repository.

## Why this corpus exists
It reproduces, deliberately and minimally, the **"Elara" incident** (docs/BRD.md
§1). A 20,001-char synopsis about Milo Voss and Zara Okafor once produced a
12-chapter outline starring an invented protagonist, "Elara", because the model
received only the first 2,000 characters and an empty cast. This corpus is
purpose-built to make that failure *visible to an eval*:

- **`synopsis.txt`** — ~17k chars. The three leads (**Milo Voss**, **Zara
  Okafor**, **Edric Thane**) first appear at **char ~4,965**, so any truncation to
  2,000 chars loses the entire cast — exactly the original defect. The opening
  4,000+ chars are pure worldbuilding with no named protagonist, so a truncating
  pipeline produces a "protagonist-shaped hole" for the model to fill with an
  invented hero.
- **The in-world term "the CEL"** = **Chief Executive Lich**. It reads like a
  company or department, and the original incident misread it as "Corporeal
  Energy Logistics". A faithful outline understands it as the antagonist Edric
  Thane; the fidelity grader treats the bare acronym as *allowlisted but
  non-identifying* (see `evals/graders/fidelity.py`).

## What lives here
- **`synopsis.txt`** — the editable prose source of the synopsis.
- **`ground_truth.json`** — generated from `synopsis.txt` + hand-authored
  metadata: `cast`, `canonical_beats`, and the `fidelity` block (`principals` +
  `known`) that the fidelity grader consumes. This is what `load_corpus("cel")`
  reads.
- **`spec.json`** — the authoring spec (synthetic flag, cast, beats, fidelity).
- **`aliases.json`** — the human-readable canon reference / allowlist.
- **`excerpts.txt`** — ~4.7k chars of synthetic *Bored to Undeath* prose (SPEC-22),
  written here like the synopsis. It opens with the original three-paragraph
  intake scene and continues through Milo's first week: at least six tagged lines
  for each principal (Milo, Zara, Edric), inverted tags ("said Zara"), action
  beats before a line, an untagged multi-paragraph exchange, and two non-canon
  speakers (the caseworker, Old Fen) whose lines must be attributed to no canon
  character. It is the voice-bearing source the prose eval uploads, and the
  window the continuity eval plants its five contradictions in. The speaker of
  every quoted line is recorded in `ground_truth.json` (`gold_lines`,
  `other_lines`).
- **`style.json`** — the book's style guide (third person limited on Milo, past
  tense, dry deadpan tone, dialogue-forward, two paragraphs of sample prose),
  also synthetic, set on every eval book through `PUT /books/{id}/canon/style`.
- **`ground_truth.json`** also carries the canon cast entries (`characters`: role,
  description, goals, voice, aliases), the author's premise edit
  (`premise_edit`) and the planted `continuity_injections`; all hand-authored.

## The acceptance test this corpus anchors
Load *Bored to Undeath*, generate a 12-chapter outline, and assert it stars
**Milo Voss** and **Zara Okafor** against **Edric Thane** in **Aeon Holdings**,
with no invented protagonist and "the CEL" understood as the **Chief Executive
Lich** — cast fidelity 1.0, no "Elara".
