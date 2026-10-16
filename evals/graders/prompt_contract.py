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

* ``plan_cast_from_beat``  plan_beats: the "Available cast" line parses, and
                       equals the outline beat's ``people`` that are corpus cast
                       (else the chapter's) plus the chapter POV; a prompt
                       matching no beat is listed in ``unmatched`` (F2, S5).
* ``no_blank_setting`` / ``no_neutral_tone``  plan_beats, beat_prose (F3); a
                       "neutral" the outline beat chose is the author's (S7).
* ``pov_line_has_mode_and_character``  beat_prose, plan_beats, scene_revise:
                       ``POV_LINE_RE`` parses, it names the chapter POV (else
                       the corpus ``pov``), and in a one-head mode the "Narrate
                       only what" limit names them too (F9, S5, W3-3).
* ``no_duplicate_bible``  beat_prose: no bible fields in CHARACTERS IN THIS
                       BEAT; each canon description exactly once (F4).
* ``no_doubled_voice_label``  every call: no "Voice: voice:" (F7).
* ``reduce_evidence_depth``  extraction_reduce: >= 3 evidence/quote lines per
                       principal (F1).
* ``secondary_has_context``  beat_prose: each "Also named" name has context in
                       ``<secondary>`` (W3-1).
* ``story_position_line``  beat_prose, plan_beats, scene_revise with a
                       ``<chapter>``: one "chapter N of M", 1 <= N <= M (W3-2).

Wave 3: ``movement_names_in_scene``  beat_prose: no "How they move" line names
a principal or ``secondary_cast`` member outside the scene (SPEC-30); reports
``movement_coverage``, the share of principal blocks that move (B-2).

Wave 4: ``sample_lift``  beat_prose: the answer shares no word 8-gram with the
sample lines in its own prompt (voice and movement); reports ``max_shared`` and
``share_of_beats`` (SPEC-36). ``beats_see_whole_outline``  outline:beats: the
whole-outline block names every chapter title of the run's latest
outline:chapters answer (SPEC-38).

Wave 5: ``samples_not_plot``  beat_prose, scene_revise: no sample is plot by
the corpus gold (``evals.graders.plot``, SPEC-45); reports ``classified``
(``vacuous`` when 0), ``later_plot_slots`` and (wave 6) ``unplaced_events``.
``voice_dialogue.voice_coverage``: the share of beat_prose principal blocks with
a voice sample.

Wave 6 (SPEC-52): ``json_mode_where_json``  each ``JSON_PURPOSES`` call recorded
``response_format`` JSON_OBJECT. ``movement`` (D3a) and the reported
``hindsight`` and ``scripted_slots``: see ``grade``.

Each check reports ``{n, passed, rate, failures}``; a check with nothing to
check (no such call in the archive) reports ``n == 0`` and ``rate = None``, so
an archive from before a contract existed still grades.

    python -m evals.graders.prompt_contract <answered-dir> [--book cel]
"""

from __future__ import annotations

import argparse
import json
import re
from itertools import takewhile
from pathlib import Path

from app.core.prompt_markers import (
    ALSO_NAMED_LABEL,
    AVAILABLE_CAST_LABEL,
    BEAT_TO_WRITE_LABEL,
    BEATS_FENCE,
    CHAPTER_FENCE,
    CHAPTER_SCENES_HEADER,
    CHARACTERS_SECTION,
    CONFIRMED_PREMISE_NOTE,
    CURRENT_BEAT_MARK,
    EARLIER_BEATS,
    EMOTIONAL_TONE_LABEL,
    INTRODUCED_LABEL,
    MOVEMENT_LABEL,
    NAME_HEADING,
    OUTLINE_CHAPTERS_HEADER,
    POV_LIMIT_CLOSE,
    POV_LIMIT_OPEN,
    POV_LINE_RE,
    PRIOR_FENCE,
    SCENE_CHARACTERS_SECTION,
    SCENE_DESCRIPTION_LABEL,
    SECONDARY_FENCE,
    SETTING_LABEL,
    STOP_AT_NEXT_BEAT,
    STORY_POSITION_RE,
    labelled_line,
)
from app.llm.json_utils import JSON_OBJECT
from app.orchestration.pov import Pov
from app.parsing.names import NON_NAME_TOKENS, mentions, norm
from evals.graders import plot, seams

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


def purpose(rec: dict) -> str:
    """The archived purpose (the app's X-Polyphony-Purpose header)."""
    return str(rec.get("purpose") or "")


def block(text: str, label: str) -> str | None:
    """The content of the first ``<label>...</label>`` fence, or None.

    A fence's tags sit on their own lines. The prose instructions also name
    the tag in running text ("everything in <prior>, begin with ..."), and
    matching from that mention graded the instruction, not the block."""
    m = re.search(
        rf"(?m)^<{label}>[ \t]*\n(.*?)\n?^</{label}>", text, re.DOTALL
    ) or re.search(rf"<{label}>\n?(.*?)\n?</{label}>", text, re.DOTALL)
    return m.group(1) if m else None


def _squash(text: str) -> str:
    return " ".join((text or "").split())


# --- checks --------------------------------------------------------------------


def _current_beat(beats: str | None) -> tuple[int, int] | None:
    """(n, total): the beat marked current (``→ N.``) and the last item's
    number, parsed from the <beats> block; None when nothing is marked."""
    mark = re.escape(CURRENT_BEAT_MARK)
    items = re.findall(rf"(?m)^\s*({mark})?\s*(\d+)[.)]", beats or "")
    current = [int(n) for marked, n in items if marked]
    return (current[0], int(items[-1][1])) if current else None


def _is_last_beat(beats: str | None) -> bool:
    """The marked beat is the final item: there is no beat N+1 to stop at, so
    the prompt rightly omits that instruction."""
    current = _current_beat(beats)
    return bool(current) and current[0] == current[1]


# "Stop where beat <n + 1> begins" (STOP_AT_NEXT_BEAT), or a literal "N+1".
_STOP_AT_NEXT = re.compile(
    re.escape(STOP_AT_NEXT_BEAT.split("{next}")[0].rstrip()) + r"\s+(\d+|n\s*\+\s*1)",
    re.IGNORECASE,
)


def check_beats_block(prompt: str) -> list[str]:
    """B5: the whole scene's beats, numbered, and where to stop."""
    reasons = []
    beats = block(prompt, BEATS_FENCE)
    if beats is None:
        reasons.append("no <beats> block")
    elif not re.search(r"(?m)^\s*\d+[.)]", beats):
        reasons.append("<beats> is not a numbered list")
    if not _STOP_AT_NEXT.search(prompt) and not _is_last_beat(beats):
        reasons.append('no "stop where beat N+1 begins" instruction')
    return reasons


_OPENERS = "\"'“‘(—–-…*["


def _starts_a_sentence(text: str) -> bool:
    t = text.lstrip()
    while t and t[0] in _OPENERS:
        t = t[1:].lstrip()
    return bool(t) and (t[0].isupper() or t[0].isdigit())


# A paragraph of the house prose runs well under this many words (the wave-2
# archives' longest was 82), so a longer <prior> with no break lost them.
_ONE_PARAGRAPH_WORDS = 200


def check_prior(prior: str, openings: set[str]) -> list[str]:
    """B5 (W1-S11): `<prior>` keeps its paragraph breaks and starts a sentence
    (`prose_tail` cuts whole paragraphs, the first at a sentence start). A
    `<prior>` that starts with a scene's own opening (`openings`: the first 40
    squashed characters of each beat-1 answer) starts where the author's scene
    does, whatever that is (M15, S3)."""
    reasons = []
    if len(prior.split()) > _ONE_PARAGRAPH_WORDS and "\n\n" not in prior:
        reasons.append("<prior> lost its paragraph breaks")
    if not _starts_a_sentence(prior) and _squash(prior)[:40] not in openings:
        reasons.append("<prior> starts mid-sentence")
    return reasons


def voice_samples(prompt: str) -> dict[str, list[str]]:
    """{character name: [sample text]} for every "Voice samples" list in the
    prompt (the `### Name` heading above it names the character)."""
    out: dict[str, list[str]] = {}
    name = None
    collecting = False
    for line in prompt.splitlines():
        s = line.strip()
        if s.startswith(NAME_HEADING):
            name, collecting = s[len(NAME_HEADING) :].strip(), False
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
    s, g = norm(sample), norm(line)
    if not s or not g:
        return False
    # The sample is (part of) the line, or holds a whole line of >= 4 words.
    return s in g or (len(g.split()) >= 4 and g in s)


def classify_sample(
    owner: str, sample: str, gold: dict[str, list[str]], other: dict[str, list[str]]
) -> str:
    """'own' (a line the owner speaks), 'misattributed' (someone else's line)
    or 'not_dialogue' (narration, or a quoted term that nobody says). A
    secondary's own lines are in `other` (SPEC-29)."""
    own = [*gold.get(owner, []), *other.get(owner, [])]
    if any(_line_matches(sample, ln) for ln in own):
        return "own"
    for who, lines in {**gold, **other}.items():
        if who != owner and any(_line_matches(sample, ln) for ln in lines):
            return "misattributed"
    return "not_dialogue"


# A voice sample shorter than this carries no voice ("So!", "Zara,"): finding
# 5, the product's speech minimum. Reported as voice_dialogue.short_samples.
SHORT_SAMPLE_WORDS = 3


def check_voice(
    prompt: str, gold: dict[str, list[str]], other: dict[str, list[str]]
) -> tuple[list[str], dict[str, int]]:
    """B2: every voice sample is the character's own spoken line."""
    counts = dict.fromkeys(
        ("samples", "own", "misattributed", "not_dialogue", "short"), 0
    )
    reasons = []
    for owner, samples in voice_samples(prompt).items():
        for s in samples:
            kind = classify_sample(owner, s, gold, other)
            counts["samples"] += 1
            counts[kind] += 1
            counts["short"] += len(s.split()) < SHORT_SAMPLE_WORDS
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

_AVAILABLE_CAST = labelled_line(AVAILABLE_CAST_LABEL)
_BLANK_SETTING = re.compile(rf"(?m)^\s*{re.escape(SETTING_LABEL)}:\s*$")
_NEUTRAL_TONE = re.compile(
    rf"(?im)^\s*{re.escape(EMOTIONAL_TONE_LABEL)}:\s*neutral\s*$"
)
_BIBLE_LABELS = re.compile(r"^(Role|Description|Goals|Arc|Voice):")
_DOUBLED_VOICE = re.compile(r"(?i)\bvoice:\s*voice:")
_EVIDENCE_LINE = re.compile(r"^\s*-\s*(\(part\s+\d+\)|[\"“])")
_SCENE_DESCRIPTION = labelled_line(SCENE_DESCRIPTION_LABEL)
_BEAT_TO_WRITE = labelled_line(BEAT_TO_WRITE_LABEL)
_ALSO_NAMED = labelled_line(ALSO_NAMED_LABEL)
# "Premise (confirmed by the author...): ..." (CONFIRMED_PREMISE_NOTE).
_CONFIRMED_PREMISE = re.compile(
    rf"(?m)^Premise{re.escape(CONFIRMED_PREMISE_NOTE.split(':')[0])}[^)]*\): (.+)$"
)


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


def available_cast(prompt: str) -> list[str] | None:
    """The P3 cast list of a plan_beats prompt, or None when it has none."""
    m = _AVAILABLE_CAST.search(prompt)
    return _names(m.group(1)) if m else None


def check_no_blank_setting(prompt: str) -> list[str]:
    """P4 / F3: an empty setting is omitted, never printed as "Setting:"."""
    return ['blank "Setting:" line'] if _BLANK_SETTING.search(prompt) else []


def check_no_neutral_tone(prompt: str, chosen_tone: str = "") -> list[str]:
    """P4 / F3: the style guide's tone governs; "neutral" is never injected.
    A "neutral" the plan chose (`chosen_tone`, the outline beat's tone) is
    the author's and is kept (S7)."""
    if not _NEUTRAL_TONE.search(prompt) or chosen_tone.strip().lower() == "neutral":
        return []
    return ['"Emotional tone: neutral" line']


def check_pov_line(prompt: str, expected_who: str | None) -> list[str]:
    """F9: "Point of view: <mode>[, <who>]." (`POV_LINE_RE`) names the expected
    POV character, and, in a one-head mode, the knowledge limit after it names
    them too."""
    m = POV_LINE_RE.search(prompt)
    if not m:
        return ['no "Point of view: <mode>, <character>." line']
    who = (m.group("who") or "").strip()
    if not expected_who:
        return []
    if who.lower() != expected_who.lower():
        return [f"POV is {who or '(none)'!r}, not the chapter POV {expected_who!r}"]
    if not Pov(m.group("mode")).one_head:
        return []  # a wide mode is not held to one head (pov_instruction)
    if not re.search(
        rf"{re.escape(POV_LIMIT_OPEN)}\s+{re.escape(who)}\b[^\n]*?"
        rf"{re.escape(POV_LIMIT_CLOSE)}",
        prompt[m.start() :],
    ):
        return [f'no "{POV_LIMIT_OPEN} {who} {POV_LIMIT_CLOSE}" after it']
    return []


def check_secondary_has_context(prompt: str) -> list[str]:
    """W3-1: each "Also named" secondary has a `### name` in `<secondary>`,
    with an "Introduced:" line or a voice-samples list."""
    fence = block(prompt, SECONDARY_FENCE) or ""
    sections, samples = candidate_sections(fence), voice_samples(fence)
    reasons = []
    for name in _names(_ALSO_NAMED.search(prompt).group(1)):
        lines = sections.get(name)
        if lines is None:
            reasons.append(f"{name}: no '{NAME_HEADING}{name}' in <{SECONDARY_FENCE}>")
        elif not samples.get(name) and not any(
            ln.strip().startswith(INTRODUCED_LABEL) for ln in lines
        ):
            reasons.append(f"{name}: neither {INTRODUCED_LABEL} nor voice samples")
    return reasons


def check_story_position(prompt: str) -> list[str]:
    """W3-2: one "This scene is in chapter N of M." line, 1 <= N <= M."""
    found = list(STORY_POSITION_RE.finditer(prompt))
    if len(found) != 1:
        return [f"{len(found)} story-position lines"]
    n, m = int(found[0].group("n")), int(found[0].group("m"))
    return [] if 1 <= n <= m else [f"chapter {n} of {m}"]


def characters_section(prompt: str) -> str | None:
    """The "CHARACTERS IN THIS BEAT" section of a beat prompt (to BEAT TO WRITE),
    else the revise prompt's "IN THIS SCENE" one (`orchestration/revise.py`)."""
    at = prompt.find(CHARACTERS_SECTION)
    at = at if at != -1 else prompt.find(SCENE_CHARACTERS_SECTION)
    if at == -1:
        return None
    end = prompt.find(BEAT_TO_WRITE_LABEL, at)
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
        if s.startswith(NAME_HEADING):
            name = s[len(NAME_HEADING) :].strip()
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
    ("title: summary", as the app generates with) -> its people, and each
    chapter's summary -> its POV, for matching later plan/prose prompts.

    `aliases` maps every form of a corpus cast member to their name; only
    those count as cast (a secondary person is never in "Available cast")."""

    def __init__(self, aliases: dict[str, str]) -> None:
        self.aliases = aliases
        self.beats: dict[str, dict] = {}
        self.chapters: list[dict] = []

    @staticmethod
    def _people(node: dict) -> list[str]:
        """The node's `people` (SPEC-24 §1e), else its legacy `characters`."""
        refs = node.get("people")
        if refs is None:
            refs = node.get("characters")
        return [str(c) for c in refs or []]

    def add(self, answer: str) -> None:
        for ch in _json_array(answer):
            if not isinstance(ch, dict):
                continue
            chapter = {
                "summary": _squash(str(ch.get("summary") or "")),
                "pov": str(ch.get("pov") or "").strip(),
                "people": self._people(ch),
                "titles": [
                    str(c["title"])
                    for c in ch.get("children") or []
                    if isinstance(c, dict) and c.get("title")
                ],
            }
            self.chapters.append(chapter)
            for child in ch.get("children") or []:
                if not isinstance(child, dict) or not child.get("title"):
                    continue
                desc = f"{child.get('title', '')}: {child.get('summary', '')}".strip()
                self.beats[_squash(desc)] = {
                    "people": self._people(child),
                    "tone": str(child.get("tone") or ""),
                    "chapter": chapter,
                }

    def beat_for(self, prompt: str) -> dict | None:
        m = _SCENE_DESCRIPTION.search(prompt)
        return self.beats.get(_squash(m.group(1))) if m else None

    def chapter_for(self, prompt: str) -> dict | None:
        chapter = block(prompt, CHAPTER_FENCE)
        if not chapter:
            return None
        flat = _squash(chapter)
        for ch in self.chapters:
            if ch["summary"] and ch["summary"] in flat:
                return ch
        return None

    def _cast(self, names: list[str]) -> set[str]:
        keys = (" ".join(n.split()).lower() for n in names)
        return {self.aliases[k].lower() for k in keys if k in self.aliases}

    def expected_cast(self, beat: dict) -> set[str]:
        """The cast among the beat's people (the chapter's when it has none)
        plus the chapter POV, canonicalised."""
        out = self._cast(beat["people"]) or self._cast(beat["chapter"]["people"])
        if beat["chapter"]["pov"]:
            out.add(_canonical(beat["chapter"]["pov"], self.aliases))
        return out


def check_plan_cast_from_beat(prompt: str, index: OutlineIndex) -> list[str] | None:
    """F2 / S5: the planner's "Available cast" line parses to someone, and is
    its outline beat's cast plus the chapter POV. None when the line parses
    but the prompt matches no outline beat (reported, not failed)."""
    listed = available_cast(prompt)
    if not listed:
        return [f'no "{AVAILABLE_CAST_LABEL}" line, or it lists nobody']
    beat = index.beat_for(prompt)
    if beat is None:
        return None
    got = {_canonical(n, index.aliases) for n in listed}
    want = index.expected_cast(beat)
    if got == want:
        return []
    return [f"cast {sorted(got)} != beat cast + POV {sorted(want)}"]


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


def movement_blocks(section: str) -> list[tuple[str, list[str]]]:
    """(owner, [movement line]) for each ``### owner`` block, in order."""
    out = []
    for owner, ls in candidate_sections(section).items():
        run = ls[ls.index(MOVEMENT_LABEL) + 1 :] if MOVEMENT_LABEL in ls else []
        out.append((owner, [ln[2:] for ln in takewhile(lambda x: x[:2] == "- ", run)]))
    return out


def _named(text: str, person: dict) -> bool:
    """By a whole form, or a capitalised name token ("the Bell account")."""
    forms = [person["name"], *person.get("aliases", [])]
    tokens = [t for t in person["name"].split() if t.lower() not in NON_NAME_TOKENS]
    return any(mentions(norm(text), f) for f in forms) or any(
        re.search(rf"(?<!\w){re.escape(t)}(?!\w)", text) for t in tokens
    )


def _who(name: str, aliases: dict[str, str]) -> str:
    """A principal's canonical name, as cased in the corpus (else `name`)."""
    return aliases.get(" ".join(name.split()).lower(), name)


def check_movement_in_scene(
    prompt: str, people: list[dict], aliases: dict[str, str]
) -> tuple[list[str], list[tuple[str, int]]]:
    """SPEC-30, S7: (reasons, [(owner, movement lines)]). A line may name only
    people (ground truth) in the scene: a ``### `` block, the Also-named line,
    or a principal (an `aliases` value) the scene brief or beat plan names. A
    secondary is in the scene only through the Also-named line (NB-2)."""

    def who(name: str) -> str:
        return _who(name, aliases)

    blocks = [
        (who(o), ls) for o, ls in movement_blocks(characters_section(prompt) or "")
    ]
    m = _ALSO_NAMED.search(prompt)
    present = {o for o, _ in blocks} | {who(n) for n in _names(m.group(1) if m else "")}
    brief = f"{block(prompt, 'scene') or ''}\n{block(prompt, BEATS_FENCE) or ''}"
    principals = [p for p in people if p["name"] in aliases.values()]
    present |= {p["name"] for p in principals if _named(brief, p)}
    reasons = [
        f"{owner}: names {p['name']}: {line!r}"
        for owner, lines in blocks
        for line in lines
        for p in people
        if p["name"] not in present and _named(line, p)
    ]
    return reasons, [(o, len(ls)) for o, ls in blocks]


LIFT_N = 8  # a shared run this long is a copied phrase, not a shared idiom


def check_sample_lift(prompt: str, answer: str) -> tuple[list[str], int]:
    """W4-3: (reasons, shared) — the word 8-grams the answer shares with the
    sample lines in its own prompt: voice samples and movement (SPEC-36)."""
    lines = [s for ss in voice_samples(prompt).values() for s in ss]
    lines += [
        ln for _, ls in movement_blocks(characters_section(prompt) or "") for ln in ls
    ]
    ngrams = set().union(*(seams.grams(seams.words(ln), LIFT_N) for ln in lines))
    shared = sorted(seams.grams(seams.words(answer), LIFT_N) & ngrams)
    n = len(shared)
    return (
        ([f"{n} word 8-grams of its samples: {' '.join(shared[0])!r}"], n)
        if n
        else ([], 0)
    )


_MARKED_ITEM = re.compile(rf"^({re.escape(CURRENT_BEAT_MARK)}| ) \d+\. (.*)$")


def marked_block(prompt: str, header: str, marks: int | None):
    """S-5: the ONE parser of a `marked_plan` block. (items, marked indices,
    whether an EARLIER_BEATS list follows, reasons), or None without `header`;
    `marks` is the expected count of → (None: one or more)."""
    at = prompt.find(header)
    if at == -1:
        return None
    lines = prompt[at + len(header) :].split("\n\n", 1)[0].splitlines()[1:]
    found = list(takewhile(bool, map(_MARKED_ITEM.match, lines)))
    marked = [i for i, m in enumerate(found) if m.group(1) != " "]
    after = lines[len(found)] if len(found) < len(lines) else ""
    earlier = after.startswith(EARLIER_BEATS.split("{what}")[0])
    ok = len(marked) == marks if marks else bool(marked)
    reasons = [] if ok else [f"{len(marked)} {CURRENT_BEAT_MARK} marks"]
    return [m.group(2) for m in found], marked, earlier, reasons


def check_whole_outline(prompt: str, titles: set[str]) -> list[str]:
    """W4-5: the act's prompt shows the whole book — its OUTLINE_CHAPTERS_HEADER
    block names every chapter title, this act's marked (SPEC-38)."""
    parsed = marked_block(prompt, OUTLINE_CHAPTERS_HEADER, None)
    if parsed is None:
        return ["no whole-outline block"]
    items, _, _, reasons = parsed
    listing = "\n".join(items)
    return reasons + [
        f"{t!r} is not in the whole-outline block"
        for t in sorted(titles)
        if t not in listing
    ]


def check_plan_sees_chapter(prompt: str, titles: list[str]) -> list[str]:
    """W5-4: the planner sees its chapter's scenes, its own marked, and what
    the earlier ones planned (SPEC-43)."""
    parsed = marked_block(prompt, CHAPTER_SCENES_HEADER, 1)
    if parsed is None:
        return ["no chapter-scenes block"]
    items, marked, earlier, reasons = parsed
    if marked and marked[0] > 0 and not earlier:
        reasons.append("no earlier-beats list after a scene that is not the first")
    listing = "\n".join(items)
    return reasons + [
        f"{t!r} is not in the chapter-scenes block" for t in titles if t not in listing
    ]


# The redo's "Beat N" line (app/orchestration/revise.py, SPEC-41).
_REDO_BEAT = re.compile(r"(?m)^Beat (\d+) of this scene's plan:")


def check_redo_sees_plan(prompt: str) -> list[str]:
    """W5-2: a redo of beat N shows the scene's whole plan with item N marked."""
    n = int(_REDO_BEAT.search(prompt).group(1))
    current = _current_beat(block(prompt, BEATS_FENCE))
    if current is None:
        return [f"no <beats> block marking beat {n}"]
    return [] if current[0] == n else [f"<beats> marks beat {current[0]}, not beat {n}"]


def check_samples_not_plot(
    prompt: str, gold: dict, placed: dict[str, int], counts: dict[str, int]
) -> tuple[list[str], int | None]:
    """W5-1 (d): (reasons, the scene's chapter) over every sample shown; adds
    to `counts` (``classified``: placed in a gold paragraph)."""
    m = STORY_POSITION_RE.search(prompt)
    chapter = int(m.group("n")) if m else None
    moves = movement_blocks(characters_section(prompt) or "")
    reasons = []
    for kind, owner, sample in [
        *(("voice", o, s) for o, ss in voice_samples(prompt).items() for s in ss),
        *(("movement", o, s) for o, ss in moves for s in ss),
    ]:
        why, at, later = plot.plot_reasons(kind, sample, chapter, gold, placed)
        counts["classified"] += at is not None
        counts["later_plot_slots"] += later
        reasons += [f"{owner}: {r}" for r in why]
    return reasons, chapter


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
    # wave 2 (§5.1), reworked in wave 2.5 (S5)
    "plan_cast_from_beat",
    "no_blank_setting",
    "no_neutral_tone",
    "pov_line_has_mode_and_character",
    "no_duplicate_bible",
    "no_doubled_voice_label",
    "reduce_evidence_depth",
    # wave 3
    "movement_names_in_scene",
    "secondary_has_context",
    "story_position_line",
    # wave 4
    "sample_lift",
    "beats_see_whole_outline",
    # wave 5
    "plan_sees_chapter",
    "redo_sees_plan",
    "samples_not_plot",
    # wave 6
    "json_mode_where_json",
)

# The calls whose prompt asks for JSON only (W6-6): a literal list.
JSON_PURPOSES = (
    *("outline:skeleton", "outline:chapters", "outline:beats", "plan_beats"),
    *("continuity", "extraction_map", "extraction_reduce", "extraction_synopsis"),
)
_HINDSIGHT = re.compile(
    r"would later|much later|years later|would come to|did not yet know", re.I
)
_PRONOUNS = ("his her your my their", "he she you i they", "him me them")
_FOLD = {w: f"<{n}>" for n, ws in enumerate(_PRONOUNS) for w in ws.split()}


def scripted_slot(sample: str, ref: str) -> dict:
    """D4, reported (B-4: not a gate): a pronoun-folded 6-word run shared with
    `ref` (beat line + brief), and the share of its content words `ref` holds."""
    fold = [[_FOLD.get(w, w) for w in seams.words(t)] for t in (sample, ref)]
    own, theirs = ({w for w in seams.words(t) if len(w) > 3} for t in (sample, ref))
    six = bool(seams.grams(fold[0], 6) & seams.grams(fold[1], 6))
    share = round(len(own & theirs) / max(1, len(own)), 2)
    return {"folded6": six, "content_share": share}


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
    principals: list[dict] | None = None,
    style_tone: str = "",
    pov: str | None = None,
    descriptions: list[str] | None = None,
    secondary_cast: list | None = None,
    excerpt_events: list | None = None,
    excerpt: str = "",
    movement_store: dict[str, list[str]] | None = None,
) -> dict:
    """Run every check over an archive (see the module docstring).

    `gold_lines` is the corpus' {speaker: {train, test}}; `other_lines` names
    non-canon speakers' lines ({speaker: [line]}) so a sample stolen from them
    counts as misattributed rather than as narration. The wave-2 inputs:
    `principals` (fidelity.principals, {name, aliases}), the style guide's
    `style_tone`, the corpus POV character `pov` (the default when a prompt's
    chapter is not in the outline answers), and
    the canon `descriptions` that must appear exactly once per beat prompt.
    `secondary_cast` (names, or {name, aliases}) joins the principals as the
    people a movement sample may not name outside its scene (wave 3).
    `excerpt_events` and the `excerpt` they index are the plot gold (wave 5).
    `movement_store` is each principal's indexed movement chunks (the prose
    step's `voice.movement`): D6 reads it, else the lines shown in the run."""
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
    people = [*(principals or [])] + [
        s if isinstance(s, dict) else {"name": s} for s in secondary_cast or []
    ]
    movement = {n: 0 for n in principal_names}
    moving_blocks: list[bool] = []
    voiced_blocks: list[bool] = []  # principal blocks with a voice sample
    tallies = {k: _Tally() for k in CHECKS}
    voice_counts = dict.fromkeys(
        ("samples", "own", "misattributed", "not_dialogue", "short"), 0
    )
    plan_unmatched: list[str] = []
    beat_prompt_chars: list[int] = []
    outline = OutlineIndex(aliases)
    premises = [(premise_edit or {}).get("premise_restated") or ""]
    conflicts = [(premise_edit or {}).get("central_conflict") or ""]
    acts: list[dict] = []
    purposes: dict[str, int] = {}
    openings: set[str] = set()  # records come oldest first (load_archive)
    lifts: list[int] = []
    book_titles: set[str] = set()
    pg = {"events": excerpt_events or [], "paragraphs": excerpt.split("\n\n")}
    placed = plot.locate(pg["events"], plot.outline_chapters(records))
    plotted = dict.fromkeys(("classified", "later_plot_slots"), 0)
    # D3a: a principal is eligible in a beat when the store holds a line of
    # theirs that is not gold plot there; every eligible block must move.
    move = _Tally()
    ineligible: dict[str, set[int]] = {}  # owner: the gold paragraphs
    hindsight, scripted = [], []
    shown: dict[str, set[str]] = {}  # every movement line each owner is shown
    for rec in (r for r in records if purpose(r) == "beat_prose"):
        for o, ls in movement_blocks(characters_section(prompt_text(rec)) or ""):
            shown.setdefault(o, set()).update(ls)
    if movement_store is not None:  # D3a reads what the store holds
        shown = {_who(o, aliases): set(ls) for o, ls in movement_store.items()}

    for rec in records:
        pur = purpose(rec)
        purposes[pur or "(none)"] = purposes.get(pur or "(none)", 0) + 1
        prompt = prompt_text(rec)
        tallies["no_doubled_voice_label"].add(rec, check_no_doubled_voice(prompt))
        if pur in JSON_PURPOSES:
            fmt = rec.get("response_format")
            why = f"response_format {json.dumps(fmt)}, not JSON_OBJECT"
            tallies["json_mode_where_json"].add(rec, [why] * (fmt != JSON_OBJECT))
        if pur in ("beat_prose", "scene_revise"):
            hindsight += [
                {"id": rec.get("id"), "sentence": s}
                for s in re.split(r"(?<=[.!?])\s+", rec.get("answer") or "")
                if _HINDSIGHT.search(s)
            ]
        chapter = None
        if pur in ("beat_prose", "scene_revise") and pg["events"]:
            reasons, chapter = check_samples_not_plot(prompt, pg, placed, plotted)
            tallies["samples_not_plot"].add(rec, reasons)
        if pur == "outline:beats":
            outline.add(rec.get("answer") or "")
        if pur == "plan_beats":
            cast = check_plan_cast_from_beat(prompt, outline)
            if cast is None:
                plan_unmatched.append(str(rec.get("id")))
            else:
                tallies["plan_cast_from_beat"].add(rec, cast)
            if planned := outline.beat_for(prompt):
                tallies["plan_sees_chapter"].add(
                    rec, check_plan_sees_chapter(prompt, planned["chapter"]["titles"])
                )
        if pur in ("plan_beats", "beat_prose"):
            tallies["no_blank_setting"].add(rec, check_no_blank_setting(prompt))
            if style_tone.strip():
                planned = outline.beat_for(prompt) or {}
                tallies["no_neutral_tone"].add(
                    rec, check_no_neutral_tone(prompt, planned.get("tone") or "")
                )
        if pur in ("beat_prose", "plan_beats", "scene_revise"):
            who = (outline.chapter_for(prompt) or {}).get("pov") or pov
            tallies["pov_line_has_mode_and_character"].add(
                rec, check_pov_line(prompt, who or None)
            )
            if block(prompt, CHAPTER_FENCE) is not None:
                tallies["story_position_line"].add(rec, check_story_position(prompt))
        if pur == "beat_prose":
            beat_prompt_chars.append(len(prompt))
            if _ALSO_NAMED.search(prompt):
                tallies["secondary_has_context"].add(
                    rec, check_secondary_has_context(prompt)
                )
            tallies["no_duplicate_bible"].add(
                rec, check_no_duplicate_bible(prompt, descriptions or [])
            )
            reasons, blocks = check_movement_in_scene(prompt, people, aliases)
            tallies["movement_names_in_scene"].add(rec, reasons)
            said = {_who(o, aliases) for o, ss in voice_samples(prompt).items() if ss}
            voiced_blocks += [o in said for o, _ in blocks if o in movement]
            empty, eligible = [], 0
            for owner, n in ((o, n) for o, n in blocks if o in movement):
                movement[owner] += n
                moving_blocks.append(n > 0)
                lines = shown.get(owner) or ()
                why = plot.only_plot(lines, chapter, pg, placed)
                if lines and not why:  # D3a: eligible here
                    eligible += 1
                    empty += [f"{owner}: eligible, no movement line"] * (not n)
                else:
                    ineligible.setdefault(owner, set()).update(why)
            if eligible:
                move.add(rec, empty)
            found = (r.search(prompt) for r in (_BEAT_TO_WRITE, _SCENE_DESCRIPTION))
            ref = " ".join(m.group(1) for m in found if m)
            scripted += [
                {"id": rec.get("id"), "who": o, **scripted_slot(s, ref)}
                for o, ss in voice_samples(prompt).items()
                for s in ss
            ]
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
        if pur == "outline:chapters":  # the book the next acts' beats must see
            chapters = _json_array(rec.get("answer") or "")
            book_titles = {
                str(c["title"])
                for c in chapters
                if isinstance(c, dict) and c.get("title")
            }
        if pur == "outline:beats":
            tallies["beats_see_whole_outline"].add(
                rec, check_whole_outline(prompt, book_titles)
            )
            tallies["beats_stage"].add(
                rec, check_beats_stage(prompt, premises, conflicts, acts)
            )
        if pur == "beat_prose":
            tallies["beats_block"].add(rec, check_beats_block(prompt))
            prior = block(prompt, PRIOR_FENCE)
            if prior is not None:
                tallies["prior_paragraphs"].add(rec, check_prior(prior, openings))
            if (_current_beat(block(prompt, BEATS_FENCE)) or (0,))[0] == 1:
                openings.add(_squash(rec.get("answer") or "")[:40])
            if sample_prose:
                tallies["style_first"].add(rec, check_style_first(prompt, sample_prose))
            reasons, shared = check_sample_lift(prompt, rec.get("answer") or "")
            tallies["sample_lift"].add(rec, reasons)
            lifts.append(shared)
        if pur == "scene_revise" and _REDO_BEAT.search(prompt):
            tallies["redo_sees_plan"].add(rec, check_redo_sees_plan(prompt))
        if gold and re.search(r"(?im)^\s*voice samples\b", prompt):
            reasons, counts = check_voice(prompt, gold, other)
            for k, v in counts.items():
                voice_counts[k] += v
            tallies["voice_dialogue"].add(rec, reasons)

    out = {k: t.as_dict() for k, t in tallies.items()}
    short = voice_counts.pop("short")
    out["voice_dialogue"]["samples"] = voice_counts
    # Reported, not gated (§6.3): voice samples too short to carry a voice.
    out["voice_dialogue"]["short_samples"] = short
    # Gated >= 0.8 per run (§6.2): beat_prose principal blocks with a voice sample.
    out["voice_dialogue"]["voice_coverage"] = (
        round(sum(voiced_blocks) / len(voiced_blocks), 4) if voiced_blocks else None
    )
    out["plan_cast_from_beat"]["unmatched"] = plan_unmatched
    # Reported; gated >= 0.8 by the eval (B-2): principal blocks that move.
    out["movement_names_in_scene"]["movement_coverage"] = (
        round(sum(moving_blocks) / len(moving_blocks), 4) if moving_blocks else None
    )
    out["movement_names_in_scene"]["movement_lines"] = movement
    # Gated (§6.2, D3a): n = 0 passes only with every principal listed with gold.
    listed = {o: sorted(ps) for o, ps in sorted(ineligible.items())}
    out["movement"] = move.as_dict() | {"ineligible": listed}
    out["movement"]["ok"] = (
        move.passed == move.n if move.n else bool(listed) and all(listed.values())
    )
    out["hindsight"] = hindsight  # reported (W6-3)
    out["scripted_slots"] = scripted  # reported (D4)
    unplaced = plot.unplaced(pg["events"], plot.outline_chapters(records))
    out["samples_not_plot"].update(plotted, placed=placed, unplaced_events=unplaced)
    # Gated with classified > 0 (§6.2): a run that showed no sample proves nothing.
    out["samples_not_plot"]["vacuous"] = plotted["classified"] == 0
    # Gated 0 / 0 by the eval (§6.2): the worst beat, and how many beats lift.
    out["sample_lift"]["max_shared"] = max(lifts, default=0)
    out["sample_lift"]["share_of_beats"] = (
        round(sum(n > 0 for n in lifts) / len(lifts), 4) if lifts else None
    )
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
        "principals": list(fid.get("principals") or []),
        "style_tone": style.get("tone") or "",
        "pov": gt.get("pov"),
        "descriptions": [
            c["description"] for c in gt.get("characters") or [] if c.get("description")
        ],
        "secondary_cast": list(gt.get("secondary_cast") or []),
        "excerpt_events": gt.get("excerpt_events"),
        "excerpt": _excerpt(gt.get("book") or ""),
    }


def _excerpt(book: str) -> str:
    path = Path(__file__).resolve().parent.parent / "corpora" / book / "excerpts.txt"
    return path.read_text(encoding="utf-8") if book and path.is_file() else ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("archive", help="the relay's answered/ directory")
    ap.add_argument("--book", default="cel", help="corpus for gold lines + style")
    ap.add_argument("--since", type=float, default=None)
    ap.add_argument("--against", nargs="*", default=[], help="archives: replayed")
    args = ap.parse_args()
    corpus = Path(__file__).resolve().parent.parent / "corpora" / args.book
    gt = json.loads((corpus / "ground_truth.json").read_text(encoding="utf-8"))
    style_path = corpus / "style.json"
    style = (
        json.loads(style_path.read_text(encoding="utf-8"))
        if style_path.exists()
        else {}
    )
    records = load_archive(args.archive, args.since)
    result = grade(records, **corpus_inputs(gt, style))
    if args.against:  # §6.1: answers another archive already gave
        old = [r for a in args.against for r in load_archive(a)]
        result["replayed"] = plot.replayed(records, old)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
