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

Wave 2 (SPEC-28; the strings are tpm-plan-wave2 §1.3 P1-P10):

* ``outline_secondaries_allowed``  outline:chapters/beats: the SECONDARY PEOPLE
                       line lists every ``fidelity.secondaries`` name, the old
                       "Every name ... MUST be one of" rule is gone (F5).
* ``beats_stage_fields``  outline:beats: the children template asks for
                       characters, secondary_names, setting, tone (F2).
* ``plan_cast_from_beat``  plan_beats: the "Available cast" equals the outline
                       beat's cast (else the chapter's) plus the chapter POV;
                       a prompt matching no beat is listed in ``unmatched`` (F2).
* ``plan_cast_available_not_exact``  plan_beats: P3 line, "unnamed functional
                       figures", no "cast is exactly" (F2).
* ``no_blank_setting`` / ``no_neutral_tone``  plan_beats, beat_prose (F3).
* ``pov_line_has_mode_and_character``  beat_prose: P5 POV line, the chapter
                       POV, and the "Narrate only what" limit (F9).
* ``last_beat_in_pov``  beat_prose, last beat: the P6 ending (F9).
* ``no_duplicate_bible``  beat_prose: no bible fields in CHARACTERS IN THIS
                       BEAT; each canon description exactly once (F4).
* ``no_doubled_voice_label``  every call: no "Voice: voice:" (F7).
* ``reduce_evidence_depth``  extraction_reduce: >= 3 evidence/quote lines per
                       principal (F1).

Each check reports ``{n, passed, rate, failures}``; a check with nothing to
check (no such call in the archive) reports ``n == 0`` and ``rate = None``, so
an archive from before a contract existed still grades.

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
    ("extraction_reduce", "Write the full character entries"),
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
    """The content of the first ``<label>...</label>`` fence, or None.

    A fence's tags sit on their own lines. The prose instructions also name
    the tag in running text ("everything in <prior>, begin with ..."), and
    matching from that mention graded the instruction, not the block."""
    m = re.search(
        rf"(?m)^<{label}>[ \t]*\n(.*?)\n?^</{label}>", text, re.DOTALL
    ) or re.search(rf"<{label}>\n?(.*?)\n?</{label}>", text, re.DOTALL)
    return m.group(1) if m else None


def _norm(text: str) -> str:
    t = (text or "").lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return " ".join(t.split())


def _squash(text: str) -> str:
    return " ".join((text or "").split())


# --- checks --------------------------------------------------------------------


def _is_last_beat(beats: str | None) -> bool:
    """The marked current beat (``→ N.``) is the final item: there is no beat
    N+1 to stop at, so the prompt rightly omits that instruction."""
    if not beats:
        return False
    items = re.findall(r"(?m)^\s*(→)?\s*\d+[.)]", beats)
    return bool(items) and items[-1] == "→"


def check_beats_block(prompt: str) -> list[str]:
    """B5: the whole scene's beats, numbered, and where to stop."""
    reasons = []
    beats = block(prompt, "beats")
    if beats is None:
        reasons.append("no <beats> block")
    elif not re.search(r"(?m)^\s*\d+[.)]", beats):
        reasons.append("<beats> is not a numbered list")
    if not re.search(
        r"stop where beat\s+(\d+|n\s*\+\s*1)", prompt, re.IGNORECASE
    ) and not _is_last_beat(beats):
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


# --- wave-2 contracts (tpm-plan-wave2 §1.3 P1-P10, checks §5.1) ----------------

_SECONDARY_LINE = re.compile(r"(?m)^SECONDARY PEOPLE \([^)\n]*\):[ \t]*(.*)$")
_OLD_CAST_RULE = "Every name you use for a character MUST be one of"
_PRINCIPALS_RULE = "Principal characters MUST be canon cast"
_AVAILABLE_CAST = re.compile(r"(?m)^Available cast \(not all need appear\): (.+)$")
_ALSO_NAMED = re.compile(r"(?m)^Also named \(secondary, not in the cast\): (.+)$")
_BLANK_SETTING = re.compile(r"(?m)^\s*Setting:\s*$")
_NEUTRAL_TONE = re.compile(r"(?im)^\s*Emotional tone:\s*neutral\s*$")
_POV_LINE = re.compile(
    r"(?m)^Point of view: (?P<mode>(first|second|third) person[^,\n]*), "
    r"(?P<who>[^.\n]+)\."
)
_POV_MODE_ONLY = re.compile(r"(?m)^Point of view: (first|second|third) person[^,\n]*\.")
LAST_BEAT_TEXT = "end the scene inside the moment, in POV; no summary of what follows"
_BIBLE_LABELS = re.compile(r"^(Role|Description|Goals|Arc|Voice):")
_DOUBLED_VOICE = re.compile(r"(?i)\bvoice:\s*voice:")
_EVIDENCE_LINE = re.compile(r"^\s*-\s*(\(part\s+\d+\)|[\"“])")
_SCENE_DESCRIPTION = re.compile(r"(?m)^Scene description: (.+)$")
_CONFIRMED_PREMISE = re.compile(r"(?m)^Premise \(confirmed by the author[^)]*\): (.+)$")


def _names(listing: str) -> list[str]:
    """A prompt's comma-separated name list ("(none)" is empty)."""
    s = listing.strip().rstrip(".")
    if not s or s.lower() in ("(none)", "none"):
        return []
    return [n.strip() for n in s.split(",") if n.strip()]


def _canonical(name: str, aliases: dict[str, str]) -> str:
    """A name resolved through the corpus aliases ({form: canonical name}),
    lower-cased for comparison."""
    key = " ".join(name.split()).lower()
    return aliases.get(key, key).lower()


def check_outline_secondaries(prompt: str, secondaries: list[str]) -> list[str]:
    """P1 / F5: the outline stages may name the synopsis' secondary people."""
    reasons = []
    m = _SECONDARY_LINE.search(prompt)
    if not m:
        reasons.append("no SECONDARY PEOPLE line")
    else:
        listed = {n.lower() for n in _names(m.group(1))}
        missing = [s for s in secondaries if s.lower() not in listed]
        if missing:
            reasons.append(f"SECONDARY PEOPLE misses {', '.join(missing)}")
    if _OLD_CAST_RULE in prompt:
        reasons.append(f"old cast rule present: {_OLD_CAST_RULE!r}")
    if _PRINCIPALS_RULE not in prompt:
        reasons.append(f"no principals rule ({_PRINCIPALS_RULE!r})")
    return reasons


_BEAT_FIELDS = ('"characters"', '"secondary_names"', '"setting"', '"tone"')


def check_beats_stage_fields(prompt: str) -> list[str]:
    """P2 / F2: the beats template asks each beat for its cast, secondaries,
    setting and tone."""
    children = re.findall(r'"children"\s*:\s*\[\s*\{(.*?)\}', prompt, re.DOTALL)
    if not children:
        return ["no children template"]
    best = max(children, key=lambda c: sum(f in c for f in _BEAT_FIELDS))
    missing = [f for f in _BEAT_FIELDS if f not in best]
    return [f"children template lacks {', '.join(missing)}"] if missing else []


def check_plan_cast_available(prompt: str) -> list[str]:
    """P3 / F2: the planner is told who is AVAILABLE, not the exact cast."""
    reasons = []
    if not _AVAILABLE_CAST.search(prompt):
        reasons.append('no "Available cast (not all need appear):" line')
    if "unnamed functional figures" not in prompt:
        reasons.append('no "unnamed functional figures" allowance')
    if "cast is exactly" in prompt:
        reasons.append('"cast is exactly" present')
    return reasons


def available_cast(prompt: str) -> list[str] | None:
    """The P3 cast list of a plan_beats prompt, or None when it has none."""
    m = _AVAILABLE_CAST.search(prompt)
    return _names(m.group(1)) if m else None


def check_no_blank_setting(prompt: str) -> list[str]:
    """P4 / F3: an empty setting is omitted, never printed as "Setting:"."""
    return ['blank "Setting:" line'] if _BLANK_SETTING.search(prompt) else []


def check_no_neutral_tone(prompt: str) -> list[str]:
    """P4 / F3: the style guide's tone governs; "neutral" is never injected."""
    return ['"Emotional tone: neutral" line'] if _NEUTRAL_TONE.search(prompt) else []


def _paragraph_from(text: str, start: int) -> str:
    end = text.find("\n\n", start)
    return text[start : end if end != -1 else len(text)]


def check_pov_line(prompt: str, expected_who: str | None) -> list[str]:
    """P5 / F9: "Point of view: <mode>, <who>." and a knowledge limit on who."""
    m = _POV_LINE.search(prompt)
    if not m:
        if expected_who is None and _POV_MODE_ONLY.search(prompt):
            return []
        return ['no "Point of view: <mode>, <character>." line']
    reasons = []
    who = m.group("who").strip()
    if expected_who and who.lower() != expected_who.lower():
        reasons.append(f"POV is {who!r}, not the chapter POV {expected_who!r}")
    para = _paragraph_from(prompt, m.start())
    if not re.search(
        rf"Narrate only what\s+{re.escape(who)}\b.*?perceives or knows", para, re.DOTALL
    ):
        reasons.append(f'no "Narrate only what {who} perceives or knows" after it')
    return reasons


def check_last_beat_in_pov(prompt: str) -> list[str]:
    """P6 / F9: the last beat ends inside the moment, in POV."""
    return [] if LAST_BEAT_TEXT in prompt else ["no in-POV ending for the last beat"]


def characters_section(prompt: str) -> str | None:
    """The "CHARACTERS IN THIS BEAT" section of a beat prompt (to BEAT TO WRITE)."""
    at = prompt.find("CHARACTERS IN THIS BEAT")
    if at == -1:
        return None
    end = prompt.find("BEAT TO WRITE", at)
    return prompt[at : end if end != -1 else len(prompt)]


def check_no_duplicate_bible(prompt: str, descriptions: list[str]) -> list[str]:
    """P8 / F4: the beat's character blocks carry voice, not a second bible;
    the canon block keeps each description, exactly once."""
    reasons = []
    section = characters_section(prompt)
    if section is not None:
        labels = sorted(
            {
                m.group(1)
                for line in section.splitlines()
                if (m := _BIBLE_LABELS.match(line.strip()))
            }
        )
        if labels:
            reasons.append(
                f"bible fields in CHARACTERS IN THIS BEAT: {', '.join(labels)}"
            )
    flat = _squash(prompt)
    for desc in descriptions:
        d = _squash(desc)
        if not d:
            continue
        n = flat.count(d)
        if n != 1:
            reasons.append(f"canon description appears {n}x: {d[:50]!r}")
    return reasons


def check_no_doubled_voice(prompt: str) -> list[str]:
    """P9 / F7: no "Voice: voice:" anywhere."""
    return ['doubled label "Voice: voice:"'] if _DOUBLED_VOICE.search(prompt) else []


def candidate_sections(prompt: str) -> dict[str, list[str]]:
    """{heading name: its lines} for every `### Name` in a reduce prompt."""
    out: dict[str, list[str]] = {}
    name = None
    for line in prompt.splitlines():
        s = line.strip()
        if s.startswith("### "):
            name = s[4:].strip()
            out[name] = []
        elif s.startswith("</") or s.startswith("<"):
            name = None
        elif name is not None:
            out[name].append(line)
    return out


def check_reduce_evidence(
    prompt: str, principals: list[str], aliases: dict[str, str], minimum: int = 3
) -> tuple[list[str], int]:
    """P10 / F1: each principal the reduce sees has >= `minimum` evidence or
    quoted lines. Returns (reasons, principals checked)."""
    reasons, checked = [], 0
    wanted = {p.lower() for p in principals}
    for heading, lines in candidate_sections(prompt).items():
        who = _canonical(heading, aliases)
        if who not in wanted:
            continue
        checked += 1
        n = sum(1 for ln in lines if _EVIDENCE_LINE.match(ln))
        if n < minimum:
            reasons.append(f"{heading}: {n} evidence/quote line(s) (< {minimum})")
    return reasons, checked


def _json_array(text: str) -> list:
    t = (text or "").strip().replace("```json", "").replace("```", "")
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        arr = json.loads(t[start : end + 1])
    except json.JSONDecodeError:
        return []
    return arr if isinstance(arr, list) else []


class OutlineIndex:
    """The outline:beats answers seen so far: each beat's scene description
    ("title: summary", as the app generates with) -> its cast, and each
    chapter's summary -> its POV, for matching later plan/prose prompts."""

    def __init__(self, aliases: dict[str, str]) -> None:
        self.aliases = aliases
        self.beats: dict[str, dict] = {}
        self.chapters: list[dict] = []

    def add(self, answer: str) -> None:
        for ch in _json_array(answer):
            if not isinstance(ch, dict):
                continue
            chapter = {
                "summary": _squash(str(ch.get("summary") or "")),
                "pov": str(ch.get("pov") or "").strip(),
                "characters": [str(c) for c in ch.get("characters") or []],
            }
            self.chapters.append(chapter)
            for child in ch.get("children") or []:
                if not isinstance(child, dict) or not child.get("title"):
                    continue
                desc = f"{child.get('title', '')}: {child.get('summary', '')}".strip()
                self.beats[_squash(desc)] = {
                    "characters": [str(c) for c in child.get("characters") or []],
                    "chapter": chapter,
                }

    def beat_for(self, prompt: str) -> dict | None:
        m = _SCENE_DESCRIPTION.search(prompt)
        return self.beats.get(_squash(m.group(1))) if m else None

    def chapter_for(self, prompt: str) -> dict | None:
        chapter = block(prompt, "chapter")
        if not chapter:
            return None
        flat = _squash(chapter)
        for ch in self.chapters:
            if ch["summary"] and ch["summary"] in flat:
                return ch
        return None

    def expected_cast(self, beat: dict) -> set[str]:
        """The beat's cast (the chapter's when the beat names none) plus the
        chapter POV, canonicalised."""
        names = beat["characters"] or beat["chapter"]["characters"]
        out = {_canonical(n, self.aliases) for n in names}
        if beat["chapter"]["pov"]:
            out.add(_canonical(beat["chapter"]["pov"], self.aliases))
        return out


def check_plan_cast_from_beat(prompt: str, index: OutlineIndex) -> list[str] | None:
    """F2: the planner's cast is its outline beat's cast plus the chapter POV.
    None when the prompt matches no outline beat (reported, not failed)."""
    beat = index.beat_for(prompt)
    if beat is None:
        return None
    listed = available_cast(prompt)
    if listed is None:
        return ['no "Available cast" line']
    got = {_canonical(n, index.aliases) for n in listed}
    want = index.expected_cast(beat)
    if got == want:
        return []
    return [f"cast {sorted(got)} != beat cast + POV {sorted(want)}"]


def pov_character(style_pov: str) -> str | None:
    """The POV character a style guide's POV names ("third person limited, on
    Milo Voss" -> "Milo Voss"), or None."""
    m = re.search(
        r"\b(?:on|through|of)\s+([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*)", style_pov or ""
    )
    return m.group(1) if m else None


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


CHECKS = (
    "beats_block",
    "prior_paragraphs",
    "voice_dialogue",
    "beats_stage",
    "outline_no_repr",
    "style_first",
    # wave 2 (§5.1)
    "outline_secondaries_allowed",
    "beats_stage_fields",
    "plan_cast_from_beat",
    "plan_cast_available_not_exact",
    "no_blank_setting",
    "no_neutral_tone",
    "pov_line_has_mode_and_character",
    "last_beat_in_pov",
    "no_duplicate_bible",
    "no_doubled_voice_label",
    "reduce_evidence_depth",
)


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def grade(
    records: list[dict],
    *,
    gold_lines: dict | None = None,
    other_lines: dict | None = None,
    sample_prose: str = "",
    premise_edit: dict | None = None,
    secondaries: list[str] | None = None,
    principals: list[dict] | None = None,
    style_tone: str = "",
    pov: str | None = None,
    descriptions: list[str] | None = None,
) -> dict:
    """Run every check over an archive (see the module docstring).

    `gold_lines` is the corpus' {speaker: {train, test}}; `other_lines` names
    non-canon speakers' lines ({speaker: [line]}) so a sample stolen from them
    counts as misattributed rather than as narration. The wave-2 inputs:
    `secondaries` (fidelity.secondaries), `principals` (fidelity.principals,
    {name, aliases}), the style guide's `style_tone` and POV character `pov`
    (the default when a prompt's chapter is not in the outline answers), and
    the canon `descriptions` that must appear exactly once per beat prompt."""
    gold = {
        who: [*(v.get("train") or []), *(v.get("test") or [])]
        for who, v in (gold_lines or {}).items()
    }
    other = {who: list(v) for who, v in (other_lines or {}).items()}
    aliases: dict[str, str] = {}
    for p in principals or []:
        for form in [p["name"], *(p.get("aliases") or [])]:
            aliases[" ".join(form.split()).lower()] = p["name"]
    principal_names = [p["name"] for p in principals or []]
    tallies = {k: _Tally() for k in CHECKS}
    voice_counts = {"samples": 0, "own": 0, "misattributed": 0, "not_dialogue": 0}
    prior_unverified = 0
    plan_unmatched: list[str] = []
    beat_prompt_chars: list[int] = []
    stream = ProseStream()
    outline = OutlineIndex(aliases)
    premises = [(premise_edit or {}).get("premise_restated") or ""]
    conflicts = [(premise_edit or {}).get("central_conflict") or ""]
    acts: list[dict] = []
    purposes: dict[str, int] = {}

    for rec in records:
        pur = purpose(rec)
        purposes[pur or "(none)"] = purposes.get(pur or "(none)", 0) + 1
        prompt = prompt_text(rec)
        tallies["no_doubled_voice_label"].add(rec, check_no_doubled_voice(prompt))
        if pur in ("outline:chapters", "outline:beats"):
            tallies["outline_secondaries_allowed"].add(
                rec, check_outline_secondaries(prompt, secondaries or [])
            )
        if pur == "outline:beats":
            tallies["beats_stage_fields"].add(rec, check_beats_stage_fields(prompt))
            outline.add(rec.get("answer") or "")
        if pur == "plan_beats":
            tallies["plan_cast_available_not_exact"].add(
                rec, check_plan_cast_available(prompt)
            )
            cast = check_plan_cast_from_beat(prompt, outline)
            if cast is None:
                plan_unmatched.append(str(rec.get("id")))
            else:
                tallies["plan_cast_from_beat"].add(rec, cast)
        if pur in ("plan_beats", "beat_prose"):
            tallies["no_blank_setting"].add(rec, check_no_blank_setting(prompt))
            if style_tone.strip():
                tallies["no_neutral_tone"].add(rec, check_no_neutral_tone(prompt))
        if pur == "beat_prose":
            beat_prompt_chars.append(len(prompt))
            chapter = outline.chapter_for(prompt)
            who = (chapter or {}).get("pov") or pov
            tallies["pov_line_has_mode_and_character"].add(
                rec, check_pov_line(prompt, who or None)
            )
            if _is_last_beat(block(prompt, "beats")):
                tallies["last_beat_in_pov"].add(rec, check_last_beat_in_pov(prompt))
            tallies["no_duplicate_bible"].add(
                rec, check_no_duplicate_bible(prompt, descriptions or [])
            )
        if pur == "extraction_reduce" and principal_names:
            reasons, checked = check_reduce_evidence(prompt, principal_names, aliases)
            if checked:
                tallies["reduce_evidence_depth"].add(rec, reasons)
        if pur == "outline:skeleton":
            sk = _json_object(rec.get("answer") or "")
            if sk.get("premise_restated") and not premise_edit:
                premises.append(str(sk["premise_restated"]))
            if sk.get("central_conflict") and not premise_edit:
                conflicts.append(str(sk["central_conflict"]))
            if isinstance(sk.get("acts"), list):
                acts = sk["acts"]
        if pur == "outline:chapters":
            # The premise the author confirmed for THIS run, as the chapters
            # stage received it: an archive from before a fixture edit (F8)
            # still checks that the beats stage got the same premise.
            m = _CONFIRMED_PREMISE.search(prompt)
            if m:
                premises.append(m.group(1))
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
    out["plan_cast_from_beat"]["unmatched"] = plan_unmatched
    # Reported, not gated (§5.1): the beat prompt's size after the bible dedupe.
    out["beat_prose_prompt_chars_median"] = _median(beat_prompt_chars)
    out["n_calls"] = len(records)
    out["purposes"] = purposes
    rates = [t["rate"] for t in out.values() if isinstance(t, dict) and "rate" in t]
    scored = [r for r in rates if r is not None]
    # The fraction of checked calls that met every contract, check by check.
    out["score"] = round(sum(scored) / len(scored), 4) if scored else None
    return out


def corpus_inputs(gt: dict, style: dict | None) -> dict:
    """grade()'s keyword arguments from a corpus' ground truth and style guide."""
    style = style or {}
    fid = gt.get("fidelity") or {}
    return {
        "gold_lines": gt.get("gold_lines"),
        "other_lines": gt.get("other_lines"),
        "sample_prose": style.get("sample_prose") or "",
        "premise_edit": gt.get("premise_edit"),
        "secondaries": list(fid.get("secondaries") or []),
        "principals": list(fid.get("principals") or []),
        "style_tone": style.get("tone") or "",
        "pov": pov_character(style.get("pov") or ""),
        "descriptions": [
            c["description"] for c in gt.get("characters") or [] if c.get("description")
        ],
    }


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
    result = grade(load_archive(args.archive, args.since), **corpus_inputs(gt, style))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
