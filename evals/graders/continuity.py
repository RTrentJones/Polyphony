"""Continuity grader with constructed ground truth — the cleanest eval.

We take clean renamed text and inject KNOWN contradictions (a respelled name, a
date flipped out of sequence, …). The continuity checker runs over the injected
text; because we authored the errors we know exactly what should be flagged.

- detection recall = APPLIED injected contradictions that produced a matching
  finding (`n_injected` is the applied count; a planned injection whose anchor
  is outside the graded window or missing is listed in the step's
  `unapplied_injections`, never silently dropped)
  (SPEC-94 S2c / D19-8: a finding matches when its quote overlaps the
  injected span, or its detail names the replacement where it occurs only
  once — either side of the contradiction is credited; quotes and names are
  compared with typography normalised)
- false positives (SPEC-94 S2b), from the CLEAN control texts (should be ~0):
  `control_fp_per_1k_words` = control findings per 1,000 control words, and
  `control_flagged_share` = the share of control texts with any finding.
  The continuity step has ONE control text (the clean window), so there
  `control_flagged_share` is a 0/1 flag, not a rate; it becomes a share
  only when a step passes several control texts.
  (The old `false_positive_rate` divided control findings by the number of
  INJECTED errors — a ratio of two unrelated counts — and is gone.)

`apply_injections` returns the mutated text + the expected findings (each with
the `span` of its injected text); `grade_continuity` scores a checker's actual
findings against them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_QUOTES = "\"'“”‘’"
# A quote shorter than this must cover the whole injected span to match: a
# bare word ("the", "May") overlapping one edge of it is a token mention.
_MIN_QUOTE = 8


def _changed(loc: str, rep: str) -> tuple[int, int]:
    """(start, end) within `rep` of what the injection actually changed: `rep`
    less the prefix and suffix it shares with `loc`, widened to whole words.
    A spec anchors its replacement with context ("1801—I have just returned"
    → "1901—I have just returned"); the injected span is "1901", not the
    anchor around it."""
    p = 0
    while p < min(len(loc), len(rep)) and loc[p] == rep[p]:
        p += 1
    q = 0
    while q < min(len(loc), len(rep)) - p and loc[-1 - q] == rep[-1 - q]:
        q += 1
    a, b = p, len(rep) - q
    if a >= b:  # a pure deletion or no change: the whole replacement
        return 0, len(rep)
    while a > 0 and rep[a - 1].isalnum():
        a -= 1
    while b < len(rep) and rep[b].isalnum():
        b += 1
    return a, b


def inject(
    clean_text: str, injections: list[dict], limit: int | None = None
) -> tuple[str, list[dict], list[dict]]:
    """(mutated_text, expected, skipped): `apply_injections`, plus the planned
    injections it did not apply because of the text it built, each with why
    (`unapplied` adds the ones whose anchor was never found):

    - an injection whose anchor overlaps an earlier injection's replacement is
      not applied ("overlaps an earlier injection"): editing inside it would
      leave the earlier expected span pointing at stale text;
    - with `limit` (the window the checker is shown), an applied injection
      whose span ends past it is dropped from `expected` ("injected text past
      the graded window"): a longer replacement can push it beyond the cut
      the checker sees, so it could never be caught."""
    text = clean_text
    expected: list[dict] = []
    skipped: list[dict] = []
    regions: list[list[int]] = []  # each applied replacement's [start, end)
    for inj in injections:
        loc, rep = inj["locate"], inj["replace"]
        idx = text.find(loc)
        if idx == -1:
            continue
        end = idx + len(loc)
        if any(idx < r_end and end > r_start for r_start, r_end in regions):
            skipped.append({"id": inj["id"], "reason": "overlaps an earlier injection"})
            continue
        text = text[:idx] + rep + text[end:]
        delta = len(rep) - len(loc)
        for e, r in zip(expected, regions):  # what lies after this edit moves
            if e["span"][0] >= end:
                e["span"] = [e["span"][0] + delta, e["span"][1] + delta]
            if r[0] >= end:
                r[0], r[1] = r[0] + delta, r[1] + delta
        a, b = _changed(loc, rep)
        regions.append([idx, idx + len(rep)])
        expected.append(
            {
                "id": inj["id"],
                "expect_type": inj.get("expect_type"),
                "replace": rep,
                "original": loc,
                "span": [idx + a, idx + b],
            }
        )
    if limit is not None:
        past = [e for e in expected if e["span"][1] > limit]
        skipped += [
            {"id": e["id"], "reason": "injected text past the graded window"}
            for e in past
        ]
        expected = [e for e in expected if e["span"][1] <= limit]
    for e in expected:
        e["injected"] = text[e["span"][0] : e["span"][1]]
    return text, expected, skipped


def apply_injections(clean_text: str, injections: list[dict]) -> tuple[str, list[dict]]:
    """Return (mutated_text, expected) where expected is the list of planted
    contradictions the checker should catch. Each injection replaces the FIRST
    occurrence of `locate` with `replace`, leaving other occurrences intact so a
    real contradiction (old vs new) exists in the text. An injection whose
    anchor overlaps an earlier one's replacement is not applied (`inject`
    reports why).

    Each expected entry carries `span`: [start, end) of the changed text in the
    FINAL mutated text (shifted by any later injection before it), and
    `injected`: that text."""
    text, expected, _ = inject(clean_text, injections)
    return text, expected


def unapplied(
    injections: list[dict],
    expected: list[dict],
    source: str | None = None,
    skipped: list[dict] | None = None,
) -> list[dict]:
    """The planned injections apply_injections did NOT apply, each with why:
    `inject`'s own reasons (`skipped`: overlapping an earlier injection, or
    pushed past the graded window), else its anchor lies in `source` but past
    the graded window, or is not found at all (SPEC-94 S2: never skipped
    silently). Recall is over the applied ones only, so a missing one must be
    visible beside it."""
    done = {e["id"] for e in expected}
    why = {s["id"]: s["reason"] for s in skipped or []}
    return [
        {
            "id": inj["id"],
            "reason": why.get(inj["id"])
            or (
                "anchor outside the graded window"
                if source is not None and inj["locate"] in source
                else "anchor not found"
            ),
        }
        for inj in injections
        if inj["id"] not in done
    ]


def _quote(finding: dict) -> str:
    return str(finding.get("quote") or "").strip().strip(_QUOTES).strip()


# SPEC-94 D19-8 (review B2): a checker quoting `O'Niel's` or `1901 — I have`
# means the corpus's `O’Niel’s` / `1901—I have`. Both sides are normalised
# before a quote is located or a name counted: curly quotes and apostrophes
# become straight, a dash (—, –, ‒, ―, -- …) becomes one "-", whitespace
# around a dash or before closing punctuation goes, other runs become one
# space, and case is folded.
_CHAR_MAP = {
    **dict.fromkeys("‘’‚‛′ʼ`", "'"),
    **dict.fromkeys("“”„‟″«»", '"'),
    **dict.fromkeys("—–‒―−", "-"),
}
_CLOSERS = ",.;:!?)]"


def normalise(text: str) -> tuple[str, list[int]]:
    """(normalised text, index map): `out[i]` came from `text[map[i]]`, and
    `map[len(out)]` is `len(text)`, so a span found in the normalised text
    maps back to [map[a], map[b - 1] + 1) in the original."""
    chars: list[str] = []
    where: list[int] = []
    for i, ch in enumerate(text):
        ch = _CHAR_MAP.get(ch, ch)
        if ch.isspace():
            if chars and chars[-1] in " -":
                continue  # a run of spaces, or a space after a dash
            chars.append(" ")
            where.append(i)
            continue
        if ch == "-" and chars and chars[-1] == "-":
            continue  # "--", "---": one dash
        if (ch == "-" or ch in _CLOSERS) and chars and chars[-1] == " ":
            chars.pop()  # no space before a dash or closing punctuation
            where.pop()
        low = ch.lower()
        chars.append(low if len(low) == 1 else ch)
        where.append(i)
    where.append(len(text))
    return "".join(chars), where


def _norm(text: str) -> str:
    return normalise(text)[0].strip()


def _quote_overlaps(quote: str, span: tuple[int, int], text: str) -> bool:
    """Whether some occurrence of `quote` in `text` (typography-, case- and
    whitespace-insensitive, see `normalise`) overlaps `span`; a quote under
    _MIN_QUOTE characters must cover the whole span."""
    q = _norm(quote)
    if not q:
        return False
    hay, where = normalise(text)
    a, b = span
    at = hay.find(q)
    while at != -1:
        start, end = where[at], where[at + len(q) - 1] + 1
        if start < b and end > a:
            if len(q) >= _MIN_QUOTE or (start <= a and end >= b):
                return True
        at = hay.find(q, at + 1)
    return False


def _norm_name(s: str) -> str:
    """Whitespace-normalised, outer quotation marks and punctuation stripped."""
    return " ".join(str(s).split()).strip(_QUOTES + " ,.;:")


def _names_only_here(name: str, detail: str, text: str) -> bool:
    """`detail` names `name`, and `name` occurs exactly once in `text` (at the
    injection), so naming it can only point there. All three are normalised
    (`normalise`) first."""
    name = _norm(name).strip("'\" ,.;:")
    if not name:
        return False
    pat = rf"(?<!\w){re.escape(name)}(?!\w)"
    return len(re.findall(pat, _norm(text))) == 1 and bool(
        re.search(pat, _norm(detail))
    )


def _finding_matches(expected: dict, finding: dict, text: str) -> bool:
    """A finding catches an injection (SPEC-94 S2c, D19-8) when its quote
    overlaps the injected span in the injected `text`, OR its detail names the
    replacement — the whole `replace`, or the changed words — and that string
    occurs nowhere in `text` but at the injection. A contradiction has two
    sides: a correct finding may quote the other, untouched side and name the
    injected one in its detail. A quote that locates nowhere falls back to
    the same detail rule. Merely mentioning a token (the original name, which
    stands all over the text; "before"; "1") is not a catch. Quotes and names
    are compared typography-normalised (`normalise`). Type-agnostic: checkers
    vary in how they label."""
    quote = _quote(finding)
    if quote and _quote_overlaps(quote, expected["span"], text):
        return True
    detail = str(finding.get("detail") or "")
    if not detail.strip():
        return False
    return any(
        _names_only_here(_norm_name(name), detail, text)
        for name in (expected["replace"], expected.get("injected") or "")
    )


@dataclass
class ContinuityScore:
    detection_recall: float | None  # over APPLIED injections; None with none
    control_fp_per_1k_words: float | None
    control_flagged_share: float | None
    detected: list[str]
    missed: list[str]
    n_injected: int
    n_control_findings: int
    n_control_words: int
    n_control_texts: int

    def as_dict(self) -> dict:
        return {
            "detection_recall": (
                None
                if self.detection_recall is None
                else round(self.detection_recall, 4)
            ),
            "control_fp_per_1k_words": self.control_fp_per_1k_words,
            "control_flagged_share": self.control_flagged_share,
            "detected": self.detected,
            "missed": self.missed,
            "n_injected": self.n_injected,
            "n_control_findings": self.n_control_findings,
            "n_control_words": self.n_control_words,
            "n_control_texts": self.n_control_texts,
        }


def grade_continuity(
    expected: list[dict],
    injected_findings: list[dict],
    controls: list[tuple[str, list[dict]]],
    injected_text: str,
) -> ContinuityScore:
    """
    expected:          from apply_injections.
    injected_findings: the checker's findings on the injected text.
    controls:          (clean control text, the checker's findings on it) per
                       control text; every finding there is a false positive.
    injected_text:     the mutated text from apply_injections, where a
                       finding's quote is located.
    """
    detected, missed = [], []
    for exp in expected:
        if any(_finding_matches(exp, f, injected_text) for f in injected_findings):
            detected.append(exp["id"])
        else:
            missed.append(exp["id"])
    n = len(expected)
    words = sum(len(text.split()) for text, _ in controls)
    n_fp = sum(len(found) for _, found in controls)
    return ContinuityScore(
        # No applied injection measures nothing: None, not a recall of 0.
        detection_recall=len(detected) / n if n else None,
        control_fp_per_1k_words=round(1000 * n_fp / words, 4) if words else None,
        control_flagged_share=(
            round(sum(bool(found) for _, found in controls) / len(controls), 4)
            if controls
            else None
        ),
        detected=detected,
        missed=missed,
        n_injected=n,
        n_control_findings=n_fp,
        n_control_words=words,
        n_control_texts=len(controls),
    )
