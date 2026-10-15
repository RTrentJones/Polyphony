"""Prompt-contract grader: what the app actually SENT the model, per purpose.

The relay LLM (evals/relay_llm.py) archives every exchange it served under
`answered/<id>.json` ({id, purpose, messages, created, answer, ...}). These
checks read that archive and assert properties of the prompts, so a prompt
regression shows up as a deterministic number, independent of how well the
model answered (cycle 2026-10 eval plan §3.3):

* ``beats_block``      beat_prose: a numbered ``<beats>`` list for the whole
                       scene and "stop where beat N+1 begins" (B5).
* ``prior_paragraphs`` beat_prose: ``<prior>`` keeps its paragraph breaks and
                       does not start mid-sentence (B5, prior tail).
* ``voice_dialogue``   any prompt with "Voice samples": every sample under it
                       is a line the character SPEAKS in the source, checked
                       against the corpus' speaker-labelled ``gold_lines`` (B2).
* ``beats_stage``      outline:beats: the confirmed premise, central conflict
                       and the act's goal and turn are in the prompt; acts are
                       numbered from 1; no Python repr (``{'``) (B11).
* ``outline_no_repr``  every outline:* prompt: no ``{'`` (B11).
* ``style_first``      beat_prose, when the book's style guide has sample prose:
                       the sample is in the prompt and the generic craft text
                       is not (B5).

Each check reports ``{n, passed, rate, failures}``; a check with nothing to
check (no such call in the archive) reports ``n == 0`` and ``rate = None``.

    python -m evals.graders.prompt_contract <answered-dir> [--book cel]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# The house-style paragraph the beat prompt hard-codes today; it must not
# appear when the author's style guide supplies sample prose.
GENERIC_CRAFT_MARKERS = (
    "description and action carry the scene",
    "dialogue punctuates it",
)

_MAX_FAILURES = 12  # per check, in the report


# --- archive -------------------------------------------------------------------


def load_archive(path: str | Path, since: float | None = None) -> list[dict]:
    """The archived exchanges, oldest first. `since` (epoch seconds) drops
    exchanges created before a run started, so a reused relay dir does not
    grade an earlier run's prompts."""
    base = Path(path)
    if not base.is_dir():
        return []
    out = []
    for p in base.glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if since is not None and float(rec.get("created") or 0) < since:
            continue
        out.append(rec)
    out.sort(key=lambda r: (float(r.get("created") or 0), str(r.get("id", ""))))
    return out


def prompt_text(rec: dict) -> str:
    """Every message's content, in order (the beat prompt is one user turn)."""
    return "\n\n".join(str(m.get("content") or "") for m in rec.get("messages") or [])


# Recognisable lines of the app's prompts, for exchanges archived before the
# X-Polyphony-Purpose header existed (their `purpose` is empty).
_PURPOSE_MARKERS = (
    ("outline:skeleton", '"premise_restated"'),
    ("outline:chapters", "Break the acts into chapters"),
    ("outline:beats", "write 2-4 scene beats"),
    ("beat_prose", "writing one beat of a scene"),
    ("plan_beats", "narrative planner"),
    ("continuity", "continuity editor"),
)


def purpose(rec: dict) -> str:
    """The archived purpose (the app's header); when it is empty or only the
    first line of a system prompt, inferred from the prompt's own wording."""
    archived = str(rec.get("purpose") or "")
    if archived and " " not in archived:  # a header value, not a prompt line
        return archived
    text = prompt_text(rec)
    for name, marker in _PURPOSE_MARKERS:
        if marker in text:
            return name
    return archived


def block(text: str, label: str) -> str | None:
    """The content of the first ``<label>...</label>`` fence, or None."""
    m = re.search(rf"<{label}>\n?(.*?)\n?</{label}>", text, re.DOTALL)
    return m.group(1) if m else None


def _norm(text: str) -> str:
    t = (text or "").lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return " ".join(t.split())


def _squash(text: str) -> str:
    return " ".join((text or "").split())


# --- checks --------------------------------------------------------------------


def check_beats_block(prompt: str) -> list[str]:
    """B5: the whole scene's beats, numbered, and where to stop."""
    reasons = []
    beats = block(prompt, "beats")
    if beats is None:
        reasons.append("no <beats> block")
    elif not re.search(r"(?m)^\s*\d+[.)]", beats):
        reasons.append("<beats> is not a numbered list")
    if not re.search(r"stop where beat\s+(\d+|n\s*\+\s*1)", prompt, re.IGNORECASE):
        reasons.append('no "stop where beat N+1 begins" instruction')
    return reasons


_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*$")
_OPENERS = "\"'“‘(—–-…*["


def _starts_a_sentence(text: str) -> bool:
    t = text.lstrip()
    while t and t[0] in _OPENERS:
        t = t[1:].lstrip()
    return bool(t) and (t[0].isupper() or t[0].isdigit())


class ProseStream:
    """Every beat_prose answer so far, joined as the app joins beats, so a
    `<prior>` can be located in what was actually written and its paragraph
    breaks and sentence start checked against the source."""

    def __init__(self) -> None:
        self.words: list[str] = []
        self.breaks: set[int] = set()  # word indices a paragraph break precedes

    def add(self, text: str) -> None:
        for i, para in enumerate(re.split(r"\n\s*\n", text or "")):
            ws = para.split()
            if not ws:
                continue
            if self.words:
                self.breaks.add(len(self.words))
            self.words.extend(ws)

    def locate(self, prior: str) -> int | None:
        """Start index of `prior`'s words in the stream (last match), or None."""
        pw = prior.split()
        if not pw:
            return None
        head = pw[: min(8, len(pw))]
        for start in range(len(self.words) - len(pw), -1, -1):
            if self.words[start : start + len(head)] == head and (
                self.words[start : start + len(pw)] == pw
            ):
                return start
        return None


def check_prior(prior: str, stream: ProseStream) -> tuple[list[str], bool]:
    """B5: `<prior>` keeps paragraph breaks and starts at a sentence.

    Returns (reasons, verifiable). When the prior is found in the stream of
    earlier answers, "should have a break" and "starts mid-sentence" are read
    off the source; otherwise a capital-letter start is the only test."""
    reasons: list[str] = []
    start = stream.locate(prior)
    if start is None:
        if not _starts_a_sentence(prior):
            reasons.append("<prior> starts mid-sentence")
        return reasons, False
    n = len(prior.split())
    spans_break = any(start < b < start + n for b in stream.breaks)
    if spans_break and "\n\n" not in prior:
        reasons.append("<prior> lost its paragraph breaks")
    at_boundary = start == 0 or start in stream.breaks
    if not at_boundary and not _SENTENCE_END.search(stream.words[start - 1]):
        reasons.append("<prior> starts mid-sentence")
    return reasons, True


def voice_samples(prompt: str) -> dict[str, list[str]]:
    """{character name: [sample text]} for every "Voice samples" list in the
    prompt (the `### Name` heading above it names the character)."""
    out: dict[str, list[str]] = {}
    name = None
    collecting = False
    for line in prompt.splitlines():
        s = line.strip()
        if s.startswith("### "):
            name, collecting = s[4:].strip(), False
            continue
        if re.match(r"(?i)^voice samples\b", s):
            collecting = name is not None
            if collecting:
                out.setdefault(name, [])
            continue
        if collecting:
            m = re.match(r'^-\s+"(.*)"\s*$', s) or re.match(r"^-\s+(.*)$", s)
            if m:
                out[name].append(m.group(1))
                continue
            collecting = False
    return out


def _line_matches(sample: str, line: str) -> bool:
    s, g = _norm(sample), _norm(line)
    if not s or not g:
        return False
    # The sample is (part of) the line, or holds a whole line of >= 4 words.
    return s in g or (len(g.split()) >= 4 and g in s)


def classify_sample(
    owner: str, sample: str, gold: dict[str, list[str]], other: dict[str, list[str]]
) -> str:
    """'own' (a line the owner speaks), 'misattributed' (someone else's line)
    or 'not_dialogue' (narration, or a quoted term that nobody says)."""
    if any(_line_matches(sample, ln) for ln in gold.get(owner, [])):
        return "own"
    for who, lines in {**gold, **other}.items():
        if who != owner and any(_line_matches(sample, ln) for ln in lines):
            return "misattributed"
    return "not_dialogue"


def check_voice(
    prompt: str, gold: dict[str, list[str]], other: dict[str, list[str]]
) -> tuple[list[str], dict[str, int]]:
    """B2: every voice sample is the character's own spoken line."""
    counts = {"samples": 0, "own": 0, "misattributed": 0, "not_dialogue": 0}
    reasons = []
    for owner, samples in voice_samples(prompt).items():
        for s in samples:
            kind = classify_sample(owner, s, gold, other)
            counts["samples"] += 1
            counts[kind] += 1
            if kind != "own":
                reasons.append(f"{owner}: {kind}: {s[:80]!r}")
    return reasons, counts


def check_beats_stage(
    prompt: str, premises: list[str], conflicts: list[str], acts: list[dict]
) -> list[str]:
    """B11: the beats stage sees the plan it is detailing."""
    reasons = []
    p = _squash(prompt)
    if "{'" in prompt:
        reasons.append("Python repr ({') in the prompt")
    if re.search(r"\bact 0\b", prompt, re.IGNORECASE):
        reasons.append('acts numbered from 0 ("act 0")')
    known = [_squash(x) for x in premises if x and x.strip()]
    if known:
        if not any(k in p for k in known):
            reasons.append("confirmed premise missing")
    elif not re.search(r"(?i)\bpremise\b", prompt):
        reasons.append("no premise")
    known_c = [_squash(x) for x in conflicts if x and x.strip()]
    if known_c and not any(k in p for k in known_c):
        reasons.append("central conflict missing")
    turns = [
        a
        for a in acts
        if isinstance(a, dict) and (a.get("goal") or "").strip() and a.get("turn")
    ]
    if turns:
        if not any(
            _squash(a["goal"]) in p and _squash(str(a["turn"])) in p for a in turns
        ):
            reasons.append("no act's goal and turn in the prompt")
    elif not (
        re.search(r"(?i)\bgoal\b", prompt) and re.search(r"(?i)\bturn\b", prompt)
    ):
        reasons.append("no act goal/turn")
    return reasons


def check_style_first(prompt: str, sample_prose: str) -> list[str]:
    """B5: with sample prose in the style guide, it is the style reference and
    the generic craft text is gone."""
    reasons = []
    lowered = prompt.lower()
    first = _squash(sample_prose)[:60].lower()
    at = _squash(prompt).lower().find(first) if first else -1
    if at == -1:
        reasons.append("style sample prose not in the prompt")
    generic = [m for m in GENERIC_CRAFT_MARKERS if m in lowered]
    if generic:
        reasons.append(f"generic craft text present: {generic[0]!r}")
    return reasons


# --- the grade -----------------------------------------------------------------


class _Tally:
    def __init__(self) -> None:
        self.n = 0
        self.passed = 0
        self.failures: list[dict] = []

    def add(self, rec: dict, reasons: list[str]) -> None:
        self.n += 1
        if not reasons:
            self.passed += 1
        elif len(self.failures) < _MAX_FAILURES:
            self.failures.append({"id": rec.get("id"), "reasons": reasons})

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "passed": self.passed,
            "rate": round(self.passed / self.n, 4) if self.n else None,
            "failures": self.failures,
        }


def _json_object(text: str) -> dict:
    t = (text or "").strip().replace("```json", "").replace("```", "")
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        obj = json.loads(t[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def grade(
    records: list[dict],
    *,
    gold_lines: dict | None = None,
    other_lines: dict | None = None,
    sample_prose: str = "",
    premise_edit: dict | None = None,
) -> dict:
    """Run every check over an archive (see the module docstring).

    `gold_lines` is the corpus' {speaker: {train, test}}; `other_lines` names
    non-canon speakers' lines ({speaker: [line]}) so a sample stolen from them
    counts as misattributed rather than as narration."""
    gold = {
        who: [*(v.get("train") or []), *(v.get("test") or [])]
        for who, v in (gold_lines or {}).items()
    }
    other = {who: list(v) for who, v in (other_lines or {}).items()}
    tallies = {
        k: _Tally()
        for k in (
            "beats_block",
            "prior_paragraphs",
            "voice_dialogue",
            "beats_stage",
            "outline_no_repr",
            "style_first",
        )
    }
    voice_counts = {"samples": 0, "own": 0, "misattributed": 0, "not_dialogue": 0}
    prior_unverified = 0
    stream = ProseStream()
    premises = [(premise_edit or {}).get("premise_restated") or ""]
    conflicts = [(premise_edit or {}).get("central_conflict") or ""]
    acts: list[dict] = []
    purposes: dict[str, int] = {}

    for rec in records:
        pur = purpose(rec)
        purposes[pur or "(none)"] = purposes.get(pur or "(none)", 0) + 1
        prompt = prompt_text(rec)
        if pur == "outline:skeleton":
            sk = _json_object(rec.get("answer") or "")
            if sk.get("premise_restated") and not premise_edit:
                premises.append(str(sk["premise_restated"]))
            if sk.get("central_conflict") and not premise_edit:
                conflicts.append(str(sk["central_conflict"]))
            if isinstance(sk.get("acts"), list):
                acts = sk["acts"]
        if pur.startswith("outline:"):
            tallies["outline_no_repr"].add(
                rec, ["Python repr ({') in the prompt"] if "{'" in prompt else []
            )
        if pur == "outline:beats":
            tallies["beats_stage"].add(
                rec, check_beats_stage(prompt, premises, conflicts, acts)
            )
        if pur == "beat_prose":
            tallies["beats_block"].add(rec, check_beats_block(prompt))
            prior = block(prompt, "prior")
            if prior is not None:
                reasons, verified = check_prior(prior, stream)
                prior_unverified += not verified
                tallies["prior_paragraphs"].add(rec, reasons)
            if sample_prose:
                tallies["style_first"].add(rec, check_style_first(prompt, sample_prose))
        if gold and re.search(r"(?im)^\s*voice samples\b", prompt):
            reasons, counts = check_voice(prompt, gold, other)
            for k, v in counts.items():
                voice_counts[k] += v
            tallies["voice_dialogue"].add(rec, reasons)
        if pur == "beat_prose":
            stream.add(rec.get("answer") or "")

    out = {k: t.as_dict() for k, t in tallies.items()}
    out["voice_dialogue"]["samples"] = voice_counts
    out["prior_paragraphs"]["unverified"] = prior_unverified
    out["n_calls"] = len(records)
    out["purposes"] = purposes
    rates = [t["rate"] for t in out.values() if isinstance(t, dict) and "rate" in t]
    scored = [r for r in rates if r is not None]
    # The fraction of checked calls that met every contract, check by check.
    out["score"] = round(sum(scored) / len(scored), 4) if scored else None
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("archive", help="the relay's answered/ directory")
    ap.add_argument("--book", default="cel", help="corpus for gold lines + style")
    ap.add_argument("--since", type=float, default=None)
    args = ap.parse_args()
    corpus = Path(__file__).resolve().parent.parent / "corpora" / args.book
    gt = json.loads((corpus / "ground_truth.json").read_text(encoding="utf-8"))
    style_path = corpus / "style.json"
    style = (
        json.loads(style_path.read_text(encoding="utf-8"))
        if style_path.exists()
        else {}
    )
    result = grade(
        load_archive(args.archive, args.since),
        gold_lines=gt.get("gold_lines"),
        other_lines=gt.get("other_lines"),
        sample_prose=style.get("sample_prose") or "",
        premise_edit=gt.get("premise_edit"),
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
