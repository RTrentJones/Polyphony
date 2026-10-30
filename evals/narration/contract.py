"""SPEC-96 M1: the narration contract, by script ($0, no LLM).

Narration. A scene's text is split into narration and speech with the app's
own quote rules: curly quotes are normalized (`normalize_quotes`), a
paragraph's quoted spans are the extractor's `_quote_spans` (double quotes
win; a paragraph with none falls back to single-quoted speech; an odd final
double quote opens a speech that runs to the paragraph's end, a quote
continued in the next paragraph), and, as wave 18's narration excerpts rule it
(W18-5, `prose.narration_excerpts`), a paragraph that opens with a single
quote it does not close at once is a single-quoted speech running over
paragraphs: the whole paragraph is speech. The speech-tag clauses beside the
quotes (`tag_spans`: "he said", "said Mina") are removed with the quotes, as
RN's `scene_narration` removes them: a tag says who spoke, not who narrates
("I said" and "he said" alike). The rest is narration, split into sentences by
the app's `_sentences`.

Per narration sentence (`sentence_person`, `sentence_tense`):
- person: `first` when it has a first-person pronoun (I, me, my, mine, myself,
  we, us, our, ours, ourselves); `third` when it has a third-person subject
  pronoun (he, she, they) and no first-person one; None (unclassifiable)
  otherwise ("It was cold.", "Nora ran."). Object and possessive first-person
  forms count: a third-person narration never says "me" outside quotes,
  while a first-person one says "He looked at me".
- tense: a lexical heuristic over finite verbs. Past markers: was, were, had,
  did; an irregular past form (`IRREGULAR`) not after an auxiliary ("had
  said", "was told" are participles); a regular -ed form right after a
  subject pronoun (an -ly adverb between allowed). Present markers: is, are,
  am, has, does; the -s form of a listed verb; have/do or a listed base form
  right after I/we/you/they; an -s form right after he/she/it. `past` when
  past markers outnumber present ones, `present` when the reverse, None when
  tied (none at all included).
- quote style: each quoted span is `double` or `single`.

The book's expected profile (`profile`) is the majority over its source
narration (every section of its corpus excerpts): person and tense over the
classifiable sentences, quote style over the quoted spans.

Per chapter (`score_text`): `person_mismatch` = the share of its classifiable
narration sentences whose person is not the book's (SPEC-96's wording);
`tense_mismatch` the same for tense; `quote_mismatch` = the share of its
quoted spans not in the book's style; each None when nothing is
classifiable. A first-person narrator tells other people's actions too ("He
turned to the window."), so the sentence unit counts those as third person:
`person_mismatch_window` is the same share over runs of WINDOW (5)
consecutive classifiable sentences, a run being first person when any of its
sentences is (a shorter tail joins the run before it). Both are reported, with
the counts. H1 reads `person_mismatch_window` (the pre-registration's choice,
after this validation); `person_mismatch`, `tense_mismatch` and
`quote_mismatch` are descriptive only.

Validation (SPEC-96, `validate`): on each source chapter (an excerpt section;
a `quoted` teller's frame quotes removed, as narrator_samples does) the
false-positive rate of a measure is that chapter's own mismatch; the bar is
every source chapter <= 0.05. Each source chapter is then damaged by rule in
its narration only (quoted speech untouched): `to_third` rewrites
first-person pronouns to third person (I -> he, me -> him, my -> his, we ->
they, us -> them, our -> their, ...; am -> is), `to_present` rewrites past
finite verbs to the present (was -> is/am, were -> are, had -> has/have, did
-> does/do, an irregular past form not after an auxiliary to its present, a
regular -ed form after a subject pronoun to its base or -s form). A damaged
chapter is DETECTED on a measure when its mismatch exceeds the same source
chapter's mismatch on that measure by at least DELTA = 0.25 (damaged - source
>= 0.25); person measures are tested on `to_third`, tense on `to_present`.
Detection is the share of damaged chapters detected, over the chapters where
both sides have a classifiable sentence (bar: >= 0.90). A measure is
`validated` when it passes both bars; otherwise it is reported descriptively
only. The rewrite is by rule and so imperfect (agreement is not repaired);
it tests the detector, not prose.

python -m evals.narration.contract profile BOOK
python -m evals.narration.contract score REPORT_OR_DIR... [--json OUT]
python -m evals.narration.contract validate BOOK... [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

WINDOW = 5  # classifiable sentences per run (person_mismatch_window)
DELTA = 0.25  # detection: damaged mismatch - source mismatch >= DELTA
FP_BAR, DETECT_BAR = 0.05, 0.90

_FIRST = {"me", "my", "mine", "myself", "we", "us", "our", "ours", "ourselves"}
_THIRD_SUBJ = {"he", "she", "they"}
_SUBJ = {"i", "we", "you", "they", "he", "she", "it"}
_PLURAL_SUBJ = {"i", "we", "you", "they"}
_SINGULAR_SUBJ = {"he", "she", "it"}
_AUX = {"had", "has", "have", "having", "was", "were", "be", "been", "being", "is"}
_AUX |= {"are", "am", "get", "got"}
_PAST_AUX = {"was", "were", "had", "did"}
_PRESENT_AUX = {"is", "are", "am", "has", "does"}
_SKIP = {
    "never", "also", "only", "then", "still", "just", "even", "soon", "often",
    "always", "now", "again", "almost", "not", "too", "all", "both", "once",
    "quite", "hardly", "scarcely", "nearly", "rather", "already", "ever",
}  # fmt: skip
_MODALS = {"could", "would", "should", "might", "must", "can", "will", "shall"}
_MODALS |= {"may", "ought"}

# base: past (irregular). A past form equal to its participle is skipped
# after an auxiliary ("had said", "was told").
IRREGULAR = {
    "be": "was", "begin": "began", "bring": "brought", "buy": "bought",
    "catch": "caught", "choose": "chose", "come": "came", "cut": "cut",
    "draw": "drew", "drink": "drank", "drive": "drove", "eat": "ate",
    "fall": "fell", "feel": "felt", "fight": "fought", "find": "found",
    "fly": "flew", "forget": "forgot", "get": "got", "give": "gave",
    "go": "went", "grow": "grew", "hang": "hung", "hear": "heard",
    "hide": "hid", "hold": "held", "keep": "kept", "know": "knew",
    "lay": "laid", "lead": "led", "leave": "left", "lie": "lay",
    "lose": "lost", "make": "made", "mean": "meant", "meet": "met",
    "pay": "paid", "rise": "rose", "ride": "rode", "ring": "rang",
    "run": "ran", "say": "said", "see": "saw", "seek": "sought",
    "sell": "sold", "send": "sent", "shake": "shook", "shine": "shone",
    "shoot": "shot", "sing": "sang", "sink": "sank", "sit": "sat",
    "sleep": "slept", "speak": "spoke", "spend": "spent", "stand": "stood",
    "steal": "stole", "strike": "struck", "swear": "swore", "swim": "swam",
    "swing": "swung", "take": "took", "teach": "taught", "tear": "tore",
    "tell": "told", "think": "thought", "throw": "threw", "wake": "woke",
    "wear": "wore", "weep": "wept", "win": "won", "write": "wrote",
    "awake": "awoke", "bear": "bore", "bend": "bent", "bind": "bound",
    "bite": "bit", "blow": "blew", "break": "broke", "build": "built",
    "burst": "burst", "creep": "crept", "deal": "dealt", "dig": "dug",
    "dream": "dreamt", "feed": "fed", "flee": "fled", "freeze": "froze",
    "grind": "ground", "kneel": "knelt", "lend": "lent", "light": "lit",
    "slide": "slid", "spring": "sprang", "stick": "stuck", "sting": "stung",
    "stride": "strode", "strive": "strove", "sweep": "swept", "wind": "wound",
}  # fmt: skip
_PAST_OF = {p: b for b, p in IRREGULAR.items() if b != "be"}
# Forms that are also a base or a common non-verb: never a past marker alone.
_AMBIGUOUS = {"cut", "burst", "lay", "lit", "bound", "ground", "wound", "left"}
_AMBIGUOUS |= {"felt", "bit", "lead", "set", "put", "read", "hit", "let", "shut"}
_PAST_FORMS = set(_PAST_OF) - _AMBIGUOUS
_BASES = set(IRREGULAR) - {"be"}
# Regular verbs common in narration, for "I/we/they <base>" as present.
_BASES |= {
    "look", "turn", "walk", "watch", "want", "wait", "try", "open", "close",
    "call", "ask", "answer", "hope", "fear", "seem", "remember", "notice",
    "follow", "listen", "move", "pass", "reach", "start", "stop", "stay",
    "touch", "climb", "cross", "enter", "return", "pause", "lean", "glance",
    "step", "pull", "push", "press", "rest", "smile", "nod", "shiver", "breathe",
    "believe", "wonder", "love", "like", "need", "feel", "realise", "realize",
    "listen", "knock", "draw", "carry", "hurry", "raise", "place", "lift",
}  # fmt: skip
_NOT_ED_VERB = {"hundred", "bed", "red", "need", "speed", "seed", "deed", "shed"}
_NOT_ED_VERB |= {"feed", "bleed", "breed", "naked", "sacred", "wicked", "rugged"}
_NOT_ED_VERB |= {"indeed", "weed", "proceed", "succeed", "exceed", "steed"}
_WORD = re.compile(r"[A-Za-z]+")


def _s_form(base: str) -> str:
    """The third-person singular present of a base form."""
    special = {"be": "is", "have": "has", "do": "does", "go": "goes", "say": "says"}
    if base in special:
        return special[base]
    if re.search(r"(s|sh|ch|x|z|o)$", base):
        return base + "es"
    if re.search(r"[^aeiou]y$", base):
        return base[:-1] + "ies"
    return base + "s"


_PRESENT_S = {_s_form(b) for b in _BASES} - {"is", "has", "does"}


def _ed_base(word: str) -> str:
    """A regular -ed form's base, by spelling rule ("walked" -> "walk",
    "loved" -> "love", "stopped" -> "stop", "tried" -> "try")."""
    stem = word[:-2]
    if word.endswith("ied"):
        return word[:-3] + "y"
    if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in "lsz":
        return stem[:-1]
    if re.search(r"(?:[^aeiou][aeiou][^aeiouwxy]|[cgsvz]|[^aeiou]l|u|ur|rs)$", stem):
        return stem + "e" if not stem.endswith("e") else stem
    return stem


def _is_ed(word: str) -> bool:
    return len(word) > 4 and word.endswith("ed") and word not in _NOT_ED_VERB


# --- narration and speech -------------------------------------------------


def split_paragraph(paragraph: str, framed: bool = False) -> tuple[str, list[str]]:
    """(narration, [quote style per quoted span]) of one paragraph, by the
    app's quote rules (module docstring). `framed`: a teller's frame quotes
    (a `quoted` voice source, as narrator_samples strips them) are removed
    first: the whole paragraph is that teller's narration."""
    from app.parsing.character_extractor import (
        _SINGLE_QUOTED,
        _quote_spans,
        normalize_quotes,
        tag_spans,
    )

    p = normalize_quotes(" ".join(paragraph.split()))
    if framed:
        p = p.strip('"').strip()
    if not p:
        return "", []
    if p.startswith("'"):
        first = _SINGLE_QUOTED.match(p)
        if '"' not in p and not first:  # W18: an opening quote it never closes
            return "", ["single"]
    spans = _quote_spans(p)
    style = "double" if '"' in p else "single"
    cut = [(s - 1, e + 1) for s, e, _ in spans] + tag_spans(p)
    kept = "".join(c for k, c in enumerate(p) if not any(a <= k < b for a, b in cut))
    # punctuation left on its own once a tag is cut goes (`_tagless_narration`)
    words = [w for w in kept.split() if re.search(r"\w", w)]
    return " ".join(words), [style] * len(spans)


def paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def narration_sentences(text: str, framed: bool = False) -> tuple[list[str], list]:
    """(narration sentences, quote styles) of a scene or chapter."""
    from app.orchestration.prose import _sentences

    sents, styles = [], []
    for p in paragraphs(text):
        n, st = split_paragraph(p, framed)
        styles += st
        sents += [s for s in _sentences(n) if _WORD.search(s)]
    return sents, styles


# --- per sentence -----------------------------------------------------------


def _tokens(sentence: str) -> list[str]:
    return _WORD.findall(sentence)


def sentence_person(sentence: str) -> str | None:
    toks = _tokens(sentence)
    if any(t == "I" or t.lower() in _FIRST for t in toks):
        return "first"
    if any(t.lower() in _THIRD_SUBJ for t in toks):
        return "third"
    return None


def _after_subject(low: list[str], k: int) -> int | None:
    """Index of the first word after subject pronoun `k`, -ly adverbs and
    the `_SKIP` words passed over."""
    j = k + 1
    while j < len(low) and (low[j] in _SKIP or low[j].endswith("ly")):
        j += 1
    return j if j < len(low) else None


def tense_markers(sentence: str) -> tuple[int, int]:
    """(past markers, present markers) of a narration sentence."""
    toks = _tokens(sentence)
    low = [t.lower() for t in toks]
    past = present = 0
    seen: set[int] = set()
    for k, w in enumerate(low):
        if w in _PAST_AUX:
            past += 1
            seen.add(k)
        elif w in _PRESENT_AUX:
            present += 1
            seen.add(k)
        elif w in _PAST_FORMS and not (k and low[k - 1] in _AUX):
            past += 1
            seen.add(k)
        elif w in _PRESENT_S:
            present += 1
            seen.add(k)
    for k, w in enumerate(low):
        if w not in _SUBJ or (w == "i" and toks[k] != "I"):
            continue
        j = _after_subject(low, k)
        if j is None or j in seen:
            continue
        v = low[j]
        if v in _MODALS:
            continue
        if _is_ed(v):
            past += 1
        elif w in _PLURAL_SUBJ and (v in _BASES or v in ("have", "do")):
            present += 1
        elif w in _SINGULAR_SUBJ and v.endswith("s") and v not in ("was", "his"):
            present += 1
    return past, present


def sentence_tense(sentence: str) -> str | None:
    past, present = tense_markers(sentence)
    if past > present:
        return "past"
    if present > past:
        return "present"
    return None


# --- per text, per book -----------------------------------------------------


def counts(text: str, framed: bool = False) -> dict:
    """Counts of classified narration sentences and quoted spans."""
    sents, styles = narration_sentences(text, framed)
    persons = [sentence_person(s) for s in sents]
    person = Counter(persons)
    window = Counter(windows([x for x in persons if x]))
    tense = Counter(sentence_tense(s) for s in sents)
    return {
        "sentences": len(sents),
        "person": {k: person.get(k, 0) for k in ("first", "third")},
        "person_window": {k: window.get(k, 0) for k in ("first", "third")},
        "tense": {k: tense.get(k, 0) for k in ("past", "present")},
        "quotes": {k: styles.count(k) for k in ("double", "single")},
    }


def windows(persons: list[str], k: int = WINDOW) -> list[str]:
    """The person of each run of `k` consecutive classifiable sentences
    (a shorter tail joins the run before it): `first` when any of its
    sentences is first person, else `third`."""
    runs = [persons[i : i + k] for i in range(0, len(persons), k)]
    if len(runs) > 1 and len(runs[-1]) < k:
        tail = runs.pop()
        runs[-1] += tail
    return ["first" if "first" in r else "third" for r in runs]


def _add(a: dict, b: dict) -> dict:
    out = {"sentences": a["sentences"] + b["sentences"]}
    for f in ("person", "person_window", "tense", "quotes"):
        out[f] = {k: a[f][k] + b[f][k] for k in a[f]}
    return out


def _majority(c: dict) -> str | None:
    best = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))
    if not best or best[0][1] == 0 or (len(best) > 1 and best[0][1] == best[1][1]):
        return None
    return best[0][0]


def source_chapters(book: str) -> list[dict]:
    """[{heading, text, framed}] for every section of the book's excerpts;
    `framed` when spec.json's voice_sources marks the section's teller as
    `quoted` (Frankenstein's Creature)."""
    from evals.steps.pipeline import CORPORA, _chapter_body, load_excerpts
    from evals.tools.headings import HEADING_ALONE

    text = load_excerpts(book)
    spec = CORPORA / book / "spec.json"
    sources = json.loads(spec.read_text("utf-8")).get("voice_sources") or {}
    framed = {h for s in sources.values() if s.get("quoted") for h in s["chapters"]}
    out = []
    for m in re.finditer(HEADING_ALONE, text):
        h = m.group(0).strip()
        out.append(
            {"heading": h, "text": _chapter_body(text, h), "framed": h in framed}
        )
    return out


def profile(book: str) -> dict:
    """The book's expected narration: the majority over its source narration."""
    total = None
    for ch in source_chapters(book):
        c = counts(ch["text"], ch["framed"])
        total = c if total is None else _add(total, c)
    total = total or counts("")
    return {
        "book": book,
        "person": _majority(total["person"]),
        "tense": _majority(total["tense"]),
        "quotes": _majority(total["quotes"]),
        "counts": total,
    }


def _share(c: dict, expected: str | None) -> float | None:
    n = sum(c.values())
    if not n or expected is None:
        return None
    return round(1 - c.get(expected, 0) / n, 4)


def mismatch(c: dict, prof: dict) -> dict:
    """The chapter's mismatch against the book's profile (module docstring)."""
    return {
        "person_mismatch": _share(c["person"], prof["person"]),
        "person_mismatch_window": _share(c["person_window"], prof["person"]),
        "tense_mismatch": _share(c["tense"], prof["tense"]),
        "quote_mismatch": _share(c["quotes"], prof["quotes"]),
        "counts": c,
    }


def score_text(text: str, prof: dict, framed: bool = False) -> dict:
    return mismatch(counts(text, framed), prof)


# --- rule damage (validation) ----------------------------------------------

_TO_THIRD = {
    "I": "he", "me": "him", "my": "his", "mine": "his", "myself": "himself",
    "we": "they", "us": "them", "our": "their", "ours": "theirs",
    "ourselves": "themselves", "am": "is",
}  # fmt: skip
_PRESENT_AUX_OF = {"was": "is", "were": "are", "did": "does"}


def _case(src: str, new: str) -> str:
    return new[:1].upper() + new[1:] if src[:1].isupper() else new


def _rewrite_narration(text: str, fn, framed: bool = False) -> str:
    """`text` with `fn` applied to every narration stretch of every paragraph,
    the quoted spans (and their marks) kept verbatim."""
    from app.parsing.character_extractor import (
        _SINGLE_QUOTED,
        _quote_spans,
        normalize_quotes,
    )

    out = []
    for raw in paragraphs(text):
        p = normalize_quotes(" ".join(raw.split()))
        if framed:
            p = p.strip('"').strip()
        if p.startswith("'") and '"' not in p and not _SINGLE_QUOTED.match(p):
            out.append(p)
            continue
        parts, at = [], 0
        for start, end, _ in _quote_spans(p):
            parts.append(fn(p[at : max(at, start - 1)]))
            parts.append(p[max(at, start - 1) : min(len(p), end + 1)])
            at = end + 1
        parts.append(fn(p[at:]))
        out.append("".join(parts))
    return "\n\n".join(out)


def _to_third(stretch: str) -> str:
    def sub(m: re.Match) -> str:
        w = m.group(0)
        if w == "I":
            opening = not stretch[: m.start()].strip() or re.search(
                r"[.!?][\"')\]]*\s*$", stretch[: m.start()]
            )
            return "He" if opening else "he"
        low = w.lower()
        if low in _TO_THIRD and low != "i":
            return _case(w, _TO_THIRD[low])
        return w

    return _WORD.sub(sub, stretch)


def to_third(text: str, framed: bool = False) -> str:
    """First person -> third, in narration only (validation damage)."""
    return _rewrite_narration(text, _to_third, framed)


def _to_present(stretch: str) -> str:
    words = list(_WORD.finditer(stretch))
    low = [m.group(0).lower() for m in words]
    new: dict[int, str] = {}
    for k, w in enumerate(low):
        prev = low[k - 1] if k else ""
        plural = prev in _PLURAL_SUBJ or prev.endswith("s") and prev not in ("his",)
        if w == "was":
            new[k] = "am" if words[k - 1].group(0) == "I" and k else "is"
        elif w in ("were", "did"):
            new[k] = _PRESENT_AUX_OF[w] if w == "were" else ("do" if plural else "does")
        elif w == "had":
            new[k] = "have" if prev in _PLURAL_SUBJ else "has"
        elif w in _PAST_OF and prev not in _AUX:
            b = _PAST_OF[w]
            new[k] = b if prev in _PLURAL_SUBJ else _s_form(b)
    for k, w in enumerate(low):
        if w not in _SUBJ:
            continue
        j = _after_subject(low, k)
        if j is None or j in new or not _is_ed(low[j]):
            continue
        b = _ed_base(low[j])
        new[j] = b if w in _PLURAL_SUBJ else _s_form(b)
    out, at = [], 0
    for k, m in enumerate(words):
        if k in new:
            out += [stretch[at : m.start()], _case(m.group(0), new[k])]
            at = m.end()
    out.append(stretch[at:])
    return "".join(out)


def to_present(text: str, framed: bool = False) -> str:
    """Past -> present, in narration only (validation damage)."""
    return _rewrite_narration(text, _to_present, framed)


# damage: (measures it should move, the rewrite)
_DAMAGES = {"to_third": (("person_mismatch", "person_mismatch_window"), to_third)}
_DAMAGES["to_present"] = (("tense_mismatch",), to_present)
MEASURES = ("person_mismatch", "person_mismatch_window", "tense_mismatch")


def validate_book(book: str) -> dict:
    """M1's validation on one book's source chapters (module docstring)."""
    prof = profile(book)
    rows = []
    for ch in source_chapters(book):
        src = score_text(ch["text"], prof, ch["framed"])
        row = {
            "heading": ch["heading"],
            "framed": ch["framed"],
            "source": {k: v for k, v in src.items() if k != "counts"},
            "counts": src["counts"],
        }
        for name, (measures, fn) in _DAMAGES.items():
            # The damaged text's frame quotes are already gone (framed=False).
            dmg = score_text(fn(ch["text"], ch["framed"]), prof)
            row[name] = {}
            for measure in measures:
                before, after = src[measure], dmg[measure]
                none = None in (before, after)
                row[name][measure] = {
                    "damaged": after,
                    "delta": None if none else round(after - before, 4),
                    "detected": None if none else after - before >= DELTA - 1e-9,
                }
        rows.append(row)
    return {"book": book, "profile": prof, "chapters": rows}


def _rate(vals: list) -> dict:
    vals = [v for v in vals if v is not None]
    return {
        "n": len(vals),
        "max": max(vals) if vals else None,
        "mean": round(sum(vals) / len(vals), 4) if vals else None,
    }


def summarize_validation(books: list[dict]) -> dict:
    """False positives (per measure: mean and max source-chapter mismatch, and
    whether every chapter is <= FP_BAR) and detection per damage."""
    rows = [r for b in books for r in b["chapters"]]
    out: dict = {"chapters": len(rows), "delta": DELTA}
    for measure in MEASURES:
        vals = [r["source"][measure] for r in rows]
        r = _rate(vals)
        r["passes"] = bool(r["n"]) and all(v <= FP_BAR for v in vals if v is not None)
        out[f"false_positive {measure}"] = r
    for name, (measures, _) in _DAMAGES.items():
        for measure in measures:
            got = [r[name][measure]["detected"] for r in rows]
            hits = [h for h in got if h is not None]
            rate = round(sum(hits) / len(hits), 4) if hits else None
            out[f"detection {name} {measure}"] = {
                "detected": sum(hits),
                "n": len(hits),
                "rate": rate,
                "passes": rate is not None and rate >= DETECT_BAR,
            }
    for measure in MEASURES:
        fp = out[f"false_positive {measure}"]["passes"]
        det = [
            v["passes"]
            for k, v in out.items()
            if k.endswith(f" {measure}") and k.startswith("detection")
        ]
        out[f"validated {measure}"] = bool(fp and det and all(det))
    return out


# --- scoring chapters-step reports -----------------------------------------


def run_id(path: Path, report: dict) -> str | None:
    """The evals run id: the `eval-report-<id>` artifact directory (gh run
    download's layout), else a numeric directory (from_runs' prior-reports/
    <id>), else the report's `run`."""
    for part in reversed(path.parts):
        m = re.fullmatch(r"eval-report-(\d+)", part)
        if m:
            return m.group(1)
    for part in reversed(path.parts[:-1]):
        if part.isdigit():
            return part
    return report.get("run")


def ablation(report: dict) -> str:
    """The run's PROSE_ABLATION switch as its report records it ("" = the
    shipped product, arm A)."""
    for where in (report, (report.get("steps") or {}).get("chapters") or {}):
        v = where.get("ablation")
        if isinstance(v, str):
            return v.strip()
    return ""


def report_files(paths: list[Path]) -> list[Path]:
    files: dict[Path, None] = {}
    for p in paths:
        for f in sorted(p.rglob("eval-report-*.json")) if p.is_dir() else [p]:
            files[f.resolve()] = None
    return list(files)


def chapter_text(row: dict) -> str:
    """A chapter row's scenes in order, eval markers stripped (as compare)."""
    from evals.baseline.blind import strip_markers

    return "\n\n".join(
        strip_markers(s["content"])
        for s in row.get("scenes") or []
        if (s.get("content") or "").strip()
    )


def chapter_rows(paths: list[Path]):
    """(meta, row) for every chapter of every `chapters` report."""
    for f in report_files(paths):
        r = json.loads(f.read_text("utf-8"))
        res = (r.get("steps") or {}).get("chapters")
        if not isinstance(res, dict):
            continue
        meta = {
            "run": run_id(f, r),
            "ablation": ablation(r),
            "app_sha": r.get("app_sha"),
            "book": r.get("book"),
            "outline_digest": res.get("outline_digest"),
            "started_at": res.get("started_at"),
        }
        for row in res.get("chapters") or []:
            yield meta, row


def score_reports(paths: list[Path]) -> list[dict]:
    """M1 per chapter of every `chapters` report under `paths`."""
    profiles: dict[str, dict] = {}
    out = []
    for meta, row in chapter_rows(paths):
        book = meta["book"]
        if book not in profiles:
            profiles[book] = profile(book)
        text = chapter_text(row)
        m = score_text(text, profiles[book])
        out.append(
            meta
            | {
                "position": row.get("position"),
                "replicate": row.get("replicate", 1),
                "failure": row.get("failure"),
                "scenes": sum(
                    1
                    for s in row.get("scenes") or []
                    if (s.get("content") or "").strip()
                ),
            }
            | m
        )
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("profile")
    p.add_argument("book")
    s = sub.add_parser("score")
    s.add_argument("reports", type=Path, nargs="+")
    s.add_argument("--json", type=Path)
    v = sub.add_parser("validate")
    v.add_argument("books", nargs="+")
    v.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "profile":
        print(json.dumps(profile(a.book), indent=1))
        return
    if a.cmd == "score":
        rows = score_reports(a.reports)
        for r in rows:
            print(
                f"{r['run']} {r['ablation'] or 'A':12s} {r['book']:12s} "
                f"ch{r['position']} r{r['replicate']}  person "
                f"{r['person_mismatch']} window {r['person_mismatch_window']} "
                f"tense {r['tense_mismatch']} quotes {r['quote_mismatch']}"
            )
        if a.json:
            a.json.write_text(json.dumps(rows, indent=1))
        return
    books = [validate_book(b) for b in a.books]
    res = {"books": books, "summary": summarize_validation(books)}
    for b in books:
        prof = b["profile"]
        print(
            f"{b['book']}: person {prof['person']}, tense {prof['tense']}, quotes {prof['quotes']}"
        )
        for r in b["chapters"]:
            src, t3, tp = r["source"], r["to_third"], r["to_present"]
            print(
                f"  {r['heading']:12s} source: person {src['person_mismatch']} "
                f"window {src['person_mismatch_window']} tense "
                f"{src['tense_mismatch']} | to_third: person "
                f"{t3['person_mismatch']['damaged']} window "
                f"{t3['person_mismatch_window']['damaged']} | to_present: tense "
                f"{tp['tense_mismatch']['damaged']} "
                f"({'detected' if tp['tense_mismatch']['detected'] else 'missed'})"
            )
    print(json.dumps(res["summary"], indent=1))
    if a.json:
        a.json.write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
