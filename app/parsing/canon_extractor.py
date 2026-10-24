"""Source text -> proposed Canon, at manuscript scale (docs/BRD.md R4.4, SPEC-09).

Reads a Source and PROPOSES typed canon candidates — characters, canon entries,
a style guess, and a synopsis — for the author to review before anything is
committed. Nothing here writes to the DB.

This used to be one call over the whole source with a 4,096-token answer. A real
novel (~120k input tokens) cost over half a day's budget and its cast did not fit
in the answer, so the JSON truncated, parsing failed, the job retried and paid
again. It is now a map-reduce:

1. **Map** — the source is split into ~12k-token windows on paragraph
   boundaries; each window gets one fast-model JSON call listing who and what it
   contains, with evidence. A window whose answer is truncated is retried ONCE as
   two smaller windows, and a piece that still fails is skipped with a warning —
   it never fails the run.
2. **Merge** — deterministic, alias-aware, honorific-aware (`app/parsing/names`):
   "Mina", "Mrs. Harker" and "Madam Mina" become one candidate when the windows'
   evidence links them, without fusing two people who share a surname.
3. **Reduce** — one default-model call per ~10 merged characters writes the full
   proposal (role, description, goals, arc, relationships, traits, voice,
   aliases) from the gathered evidence (up to 3 quotes per window), each
   candidate's own attributed dialogue (SPEC-17), and — for a one-window
   source — the window itself (SPEC-26).
4. **Synopsis** — generated last, from the merged canon.

The source is untrusted (it may be an uploaded .pdf/.html), so each window is
fenced with as_quoted_block, never filtered (docs/ADR-002-book-as-root.md §4).
Windows are whole paragraphs and together cover the whole source — nothing is
cut. Every model call is checkpointed through `save_checkpoint` so a quota pause
resumes at the next window instead of re-spending finished ones (SPEC-08 shape).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from typing import Awaitable, Callable, Optional
from uuid import UUID

from app.core.llm_text import (
    MAX_SOURCE_CHARS,
    STORY_MATERIAL_NOTICE,
    as_quoted_block,
    clean_for_llm,
)
from app.core.logging_config import setup_logging
from app.core.prompt_markers import (
    EVIDENCE_ITEM,
    EVIDENCE_STAND_IN,
    EVIDENCE_LABEL,
    EVIDENCE_NO_QUOTE,
    NAME_HEADING,
    QUOTED_LINES_LABEL,
)
from app.llm.client import get_llm_client
from app.llm.json_utils import JSON_OBJECT, extract_json_object
from app.parsing.character_extractor import normalize_quotes, tag_spans
from app.parsing.names import (
    HONORIFICS,
    NON_NAME_TOKENS,
    PARTICLES,
    PRONOUNS,
    name_core,
    norm,
    significant,
    strip_notes,
    tokens,
)

logger = setup_logging("parsing.canon_extractor")

# A real set — `cat in "world | location | …"` was a substring test, so "on" and
# "loc" passed validation and were stored (REVIEW-2026-10 F8).
CATEGORIES = frozenset({"world", "location", "faction", "item", "concept", "org"})
_CATEGORY_HINT = " | ".join(sorted(CATEGORIES))

# Closed role vocabulary; anything else is kept as "other: …" so nothing the
# model saw is lost, but the principal-role match (Canon.principals) stays exact.
ROLES = (
    "protagonist",
    "antagonist",
    "deuteragonist",
    "narrator",
    "mentor",
    "love interest",
    "foil",
    "supporting",
    "minor",
)
_ROLE_HINT = " | ".join(ROLES) + " | other: <short>"

CHARS_PER_TOKEN = 4
WINDOW_TOKENS = 12_000
REDUCE_BATCH = 10
# Reduce writes full entries for the best-attested characters; the long tail of
# once-mentioned names keeps its map-level proposal (still reviewable, and
# pre-deselected in the UI) so the number of reduce calls stays bounded.
MAX_REDUCED_CHARACTERS = 60
MAP_MAX_TOKENS_CAP = 8192
_MAX_EVIDENCE = 8
# Each window may give up to this many evidence quotes per character (SPEC-26):
# one string per window left a single-window source (any short manuscript) with
# one evidence line, and reduce wrote empty goals and voice from it.
_MAX_MAP_EVIDENCE = 3
_MAX_EVIDENCE_CHARS = 600
# The candidate's own attributed dialogue handed to reduce (SPEC-17 attributor):
# up to six lines, preferring ones of four or more words ("I'm good, thanks,"
# says little about a voice).
_MAX_QUOTED_LINES = 6
_QUOTE_MIN_WORDS = 4

SaveCheckpoint = Optional[Callable[[dict], Awaitable[None]]]


class _Unreadable(ValueError):
    """The model's answer was not the JSON object we asked for (usually truncated)."""


def estimate_tokens(text_or_chars) -> int:
    n = text_or_chars if isinstance(text_or_chars, int) else len(text_or_chars)
    return max(1, n // CHARS_PER_TOKEN)


# --- windows ---------------------------------------------------------------


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def _split_long(paragraph: str, budget_chars: int) -> list[str]:
    """An over-long paragraph splits on sentences (then words) — never mid-word."""
    pieces: list[str] = []
    for sentence in re.split(r"(?<=[.!?…])\s+", paragraph):
        if len(sentence) <= budget_chars:
            pieces.append(sentence)
            continue
        words, cur = sentence.split(" "), ""
        for w in words:
            if cur and len(cur) + 1 + len(w) > budget_chars:
                pieces.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}" if cur else w
        if cur:
            pieces.append(cur)
    out, cur = [], ""
    for s in pieces:
        if cur and len(cur) + 1 + len(s) > budget_chars:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}" if cur else s
    if cur:
        out.append(cur)
    return out


def split_windows(text: str, window_tokens: int = WINDOW_TOKENS) -> list[str]:
    """~window_tokens windows on paragraph boundaries covering the whole text."""
    budget = max(1, window_tokens * CHARS_PER_TOKEN)
    units: list[str] = []
    for p in _paragraphs(text):
        units.extend([p] if len(p) <= budget else _split_long(p, budget))
    windows: list[str] = []
    cur: list[str] = []
    size = 0
    for u in units:
        if cur and size + len(u) + 2 > budget:
            windows.append("\n\n".join(cur))
            cur, size = [], 0
        cur.append(u)
        size += len(u) + 2
    if cur:
        windows.append("\n\n".join(cur))
    return windows


def _map_max_tokens(window: str) -> int:
    """Answer budget sized to the window: a denser window names more people,
    each with up to three evidence quotes (SPEC-26)."""
    return min(MAP_MAX_TOKENS_CAP, max(1024, estimate_tokens(window) // 4))


# --- normalisation ---------------------------------------------------------


def _s(value) -> str:
    return str(value or "").strip()


def _category(value) -> str:
    cat = _s(value).lower() or "concept"
    return cat if cat in CATEGORIES else "concept"


def _role(value) -> str:
    """Closed vocabulary, else "other: …"."""
    r = _s(value).lower()
    if not r:
        return ""
    if r.startswith("other"):
        rest = r[5:].lstrip(" :-").strip()
        return f"other: {rest}"[:100] if rest else "other"
    for known in ROLES:
        if r == known or r.startswith(known):
            return known
    for known in ROLES:  # "main protagonist", "secondary antagonist"
        if known in r:
            return known
    if r in ("main", "lead", "hero", "heroine"):
        return "protagonist"
    if r in ("villain",):
        return "antagonist"
    return f"other: {r}"[:100]


def _as_map(value) -> dict:
    """A JSON bible field as {key: text} — the shape the key-value editor edits
    (R5.4). A list of traits becomes {trait: ""}."""
    if isinstance(value, dict):
        return {
            _s(k): _s(v) if not isinstance(v, (dict, list)) else json.dumps(v)
            for k, v in value.items()
            if _s(k)
        }
    if isinstance(value, (list, tuple)):
        return {_s(v): "" for v in value if _s(v) and not isinstance(v, (dict, list))}
    if isinstance(value, str) and value.strip():
        return {value.strip(): ""}
    return {}


def _evidence_list(value, limit: int = _MAX_MAP_EVIDENCE) -> list[str]:
    """Map evidence as a list of distinct short quotes. The model (and the eval
    stub, and a checkpoint written before SPEC-26) may send one string."""
    items = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    for item in items:
        if isinstance(item, (dict, list, tuple)):
            continue
        # One line each: an evidence line is one "- (part N) ..." prompt line.
        text = " ".join(_s(item).split())[:_MAX_EVIDENCE_CHARS]
        if text and text not in out:
            out.append(text)
    return out[:limit]


_WORD = re.compile(r"[^\W_]+")
_CLOSE = re.compile(r'[.!?…]*"?')


def verbatim_evidence(quotes: list[str], window: str) -> list[str]:
    """Each map quote as the author wrote it, or not at all (SPEC-56): kept if a
    run of `window` (normalised, outer marks aside); else, if its words are a
    run of the window's once `tag_spans` are left out, the window's span from
    its first word to its last, tags included; else dropped."""
    text = normalize_quotes(window)
    flat, tagged, at = " ".join(text.split()), set(), 0
    for para in text.split("\n\n"):
        tagged.update(i for a, b in tag_spans(para) for i in range(at + a, at + b))
        at += len(para) + 2
    words = [m for m in _WORD.finditer(text) if m.start() not in tagged]
    keys = [m.group().casefold() for m in words]
    out: list[str] = []
    for quote in quotes:
        q = " ".join(normalize_quotes(quote).split())
        if (core := q.strip("\"' ")) and core in flat:
            out.append(quote)
            continue
        want = [w.casefold() for w in _WORD.findall(q)]
        n = len(want)
        i = next((i for i in range(len(keys) - n + 1) if keys[i : i + n] == want), -1)
        if n and i >= 0:
            a, b = words[i].start(), words[i + n - 1].end()
            a -= text[a - 1 : a] == '"'
            b += len(_CLOSE.match(text, b).group())
            out.append(" ".join(text[a:b].split())[:_MAX_EVIDENCE_CHARS])
    return list(dict.fromkeys(out))


def _names_someone(name: str) -> bool:
    """A bare "Count" or "Dr." names no one, and neither does a pronoun."""
    return bool(significant(name)) and norm(name) not in PRONOUNS


def _clean_aliases(name: str, aliases) -> list[str]:
    """Distinct aliases that name somebody, without parenthetical notes."""
    out: list[str] = []
    seen = {norm(name)}
    for a in aliases if isinstance(aliases, (list, tuple)) else []:
        a = strip_notes(_s(a))
        if a and _names_someone(a) and norm(a) not in seen:
            seen.add(norm(a))
            out.append(a[:255])
    return out


def _normalize_character(c: dict) -> dict:
    name = strip_notes(_s(c.get("name")))[:255]
    return {
        "name": name,
        "role": _role(c.get("role")),
        "description": _s(c.get("description")),
        "goals": _s(c.get("goals")),
        "arc": _s(c.get("arc")),
        "relationships": _as_map(c.get("relationships")),
        "personality_traits": _as_map(c.get("personality_traits")),
        "voice_characteristics": _as_map(c.get("voice_characteristics")),
        "aliases": _clean_aliases(name, c.get("aliases")),
    }


def _normalize_proposals(raw: dict) -> dict:
    """Coerce a model object into the reviewed-proposal shape, defensively."""
    characters = [
        _normalize_character(c)
        for c in raw.get("characters") or []
        if isinstance(c, dict) and _s(c.get("name"))
    ]
    entries = [
        {
            "name": _s(e.get("name"))[:255],
            "category": _category(e.get("category")),
            "content": _s(e.get("content")),
        }
        for e in raw.get("canon_entries") or []
        if isinstance(e, dict) and _s(e.get("name"))
    ]
    style = raw.get("style") if isinstance(raw.get("style"), dict) else {}
    style = {k: _s(style.get(k)) for k in ("pov", "tense", "tone", "comps")}
    return {
        "characters": characters,
        "canon_entries": entries,
        "style": style,
        "synopsis": _s(raw.get("synopsis")),
    }


def _normalize_window(raw: dict) -> dict:
    """One map answer -> {characters, canon_entries, style_signals, summary}."""
    if not isinstance(raw, dict) or not (
        isinstance(raw.get("characters"), list)
        or isinstance(raw.get("canon_entries"), list)
    ):
        # A truncated answer can still contain a complete INNER object; treating
        # it as the window's answer would silently drop the window.
        raise _Unreadable("not a window answer")
    chars = []
    for c in raw.get("characters") or []:
        if not isinstance(c, dict):
            continue
        name = strip_notes(_s(c.get("name")))[:255]
        if not name or not _names_someone(name):
            continue
        chars.append(
            {
                "name": name,
                "aliases": _clean_aliases(name, c.get("aliases")),
                "role_hint": _s(c.get("role_hint") or c.get("role"))[:100],
                "evidence": _evidence_list(c.get("evidence") or c.get("description")),
            }
        )
    entries = [
        {
            "name": _s(e.get("name"))[:255],
            "category": _category(e.get("category")),
            "content": _s(e.get("content")),
        }
        for e in raw.get("canon_entries") or []
        if isinstance(e, dict) and _s(e.get("name"))
    ]
    signals = raw.get("style_signals")
    if not isinstance(signals, dict) or not any(signals.values()):
        signals = raw.get("style") if isinstance(raw.get("style"), dict) else {}
    narrators = signals.get("narrators") if isinstance(signals, dict) else None
    signals = {
        "pov": _s(signals.get("pov")),
        "tense": _s(signals.get("tense")),
        "tone": _s(signals.get("tone")),
        "comps": _s(signals.get("comps")),
        "narration": _s(signals.get("narration")),
        "narrators": [
            strip_notes(_s(n))
            for n in (narrators if isinstance(narrators, list) else [])
            if _names_someone(strip_notes(_s(n)))
        ],
    }
    return {
        "characters": chars,
        "canon_entries": entries,
        "style_signals": signals,
        "summary": _s(raw.get("summary") or raw.get("synopsis")),
    }


# --- model calls -------------------------------------------------------------


def _map_prompt(window: str, label: str, total: int) -> str:
    return f"""{STORY_MATERIAL_NOTICE}

This is part {label} of {total} of the source.

{as_quoted_block(window, "source")}

List who and what appears in THIS part of the source. Do NOT invent — only what
this part's text supports.
- characters: every named person (or named being) who acts, speaks, narrates or
  is discussed. "name" is their proper name exactly as this text writes it,
  never a description or a note in parentheses; a being the text never names
  goes under the label the text uses for it most (do not invent a name).
  "aliases" are the other names and titles this text uses for the SAME person
  (e.g. a married name, "Mrs. X", a nickname), as written: no pronouns, no
  notes. "evidence" is up to
  {_MAX_MAP_EVIDENCE} short quotes from this part (one sentence each) showing who
  they are, what they want, and how they talk or act.
- canon_entries: named places, organisations, factions, objects and ideas.
- style_signals.narrators: when this part is first-person narration (a diary,
  journal or letter, or an "I" narrator), the narrator's name if the text says it
  (a heading like "X's Journal", a letter's signature).

Return ONLY a JSON object:
{{
  "characters": [{{"name": "...", "aliases": ["..."], "role_hint": "{_ROLE_HINT}",
     "evidence": ["...", "..."]}}],
  "canon_entries": [{{"name": "...", "category": "{_CATEGORY_HINT}",
     "content": "the fact, from the text"}}],
  "style_signals": {{"pov": "", "tense": "", "tone": "",
     "narration": "first-person | third-person | epistolary | mixed",
     "narrators": []}},
  "summary": "one or two sentences: what happens in this part"
}}

JSON:"""


async def _json_call(
    prompt: str,
    *,
    fast: bool,
    max_tokens: int,
    user_id: Optional[UUID],
    purpose: str,
    stats: dict,
) -> dict:
    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        fast=fast,
        temperature=0.2,
        max_tokens=max_tokens,
        user_id=user_id,
        purpose=purpose,
        response_format=JSON_OBJECT,
    )
    stats["calls"] = stats.get("calls", 0) + 1
    stats["tokens"] = (
        stats.get("tokens", 0)
        + (getattr(result, "tokens_in", 0) or 0)
        + (getattr(result, "tokens_out", 0) or 0)
    )
    try:
        return extract_json_object(result.text)
    except ValueError as e:
        raise _Unreadable(str(e)) from e


async def _map_one(
    window: str, label: str, total: int, user_id, stats: dict
) -> tuple[list[dict], list[str]]:
    """One window -> its answers. Truncated output is retried ONCE as smaller
    windows; a piece that still fails is skipped with a warning, never raised."""

    async def call(text: str, lab: str) -> dict:
        raw = await _json_call(
            _map_prompt(text, lab, total),
            fast=True,
            max_tokens=_map_max_tokens(text),
            user_id=user_id,
            purpose="extraction_map",
            stats=stats,
        )
        window = _normalize_window(raw)
        for c in window["characters"]:
            c["evidence"] = verbatim_evidence(c["evidence"], text)
        return window

    try:
        return [await call(window, label)], []
    except _Unreadable:
        logger.warning(
            f"Extraction window {label} unreadable; retrying smaller",
            extra_fields={"event": "extraction_window_retry", "window": label},
        )
    pieces = split_windows(window, max(1, estimate_tokens(window) // 2 + 1))
    if len(pieces) < 2:
        pieces = _split_long(window, max(1, len(window) // 2 + 1))
    results, warnings = [], []
    for j, piece in enumerate(pieces):
        lab = f"{label}.{j + 1}"
        try:
            results.append(await call(piece, lab))
        except _Unreadable:
            warnings.append(
                f"Part {lab} of the source could not be read by the model and "
                "was skipped; re-run extraction or add anything missing by hand."
            )
    return results, warnings


# --- deterministic merge -------------------------------------------------------


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, i: int) -> int:
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _compatible(a: str, b: str) -> bool:
    """Two primary names can be one person: one's significant tokens contain the
    other's ("Mina" ~ "Mina Harker"), never mere overlap ("Jonathan Harker" !~
    "Mina Harker")."""
    sa, sb = significant(a), significant(b)
    return bool(sa and sb) and (sa <= sb or sb <= sa)


def _all_compatible(names: set[str]) -> bool:
    names = list(names)
    return all(
        _compatible(names[i], names[j])
        for i in range(len(names))
        for j in range(i + 1, len(names))
    )


def _count_mentions(text: str, forms: list[str]) -> int:
    """Occurrences of any of a character's names in the source (case-sensitive
    on the surface form, so "Will" is not "will"; longest form first)."""
    if not text or not forms:
        return 0
    alts = sorted({f for f in forms if f}, key=len, reverse=True)
    pattern = "|".join(r"\s+".join(map(re.escape, f.split())) for f in alts)
    return len(re.findall(rf"(?<!\w)(?:{pattern})(?!\w)", text))


def merge_characters(window_answers: list[tuple[int, dict]], text: str = "") -> list:
    """Merge every window's character candidates into one entry per person.

    Linking rules, in order (union-find, so links compose):
    1. the same name (normalised: case, punctuation, whitespace);
    2. a candidate whose name is another candidate's alias — unless two
       incompatible people claim that alias ("Harker" for Jonathan and Mina);
    3. a shared alias, when it is a proper name of two or more significant
       words or its owners are compatible ("the stranger" links no one);
    4. names differing only by honorifics, when at least two significant
       tokens remain ("Miss Nora Vance" ~ "Nora Vance");
    5. a group whose every name is contained in a fuller name of exactly ONE
       other group joins it ("Mina", "Madam Mina" -> "Mina Harker"; "Dr. Ward"
       -> "Elias Ward") — and stays separate when that is ambiguous ("Harker"
       with both Jonathan and Mina present).
    """
    cands: list[dict] = []
    for w, ans in window_answers:
        for c in ans.get("characters", []):
            ev = c["evidence"] or [EVIDENCE_NO_QUOTE.format(part=w + 1)]  # SPEC-64
            cands.append({**c, "evidence": ev, "window": w})
        for n in (ans.get("style_signals") or {}).get("narrators", []):
            cands.append(
                {
                    "name": n,
                    "aliases": [],
                    "role_hint": "narrator",
                    "evidence": [EVIDENCE_STAND_IN.format(part=w + 1)],
                    "window": w,
                }
            )
    if not cands:
        return []
    uf = _UF(len(cands))

    by_primary: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(cands):
        by_primary[norm(c["name"])].append(i)
    for idxs in by_primary.values():
        for i in idxs[1:]:
            uf.union(idxs[0], i)

    alias_owners: dict[str, set[str]] = defaultdict(set)
    alias_cands: dict[str, list[int]] = defaultdict(list)
    alias_named: dict[str, bool] = defaultdict(bool)
    particled: dict[str, set] = {}  # a particle alias's proper words
    for i, c in enumerate(cands):
        for a in c["aliases"]:
            alias_owners[norm(a)].add(c["name"])
            alias_cands[norm(a)].append(i)
            # Two name words, a particle counting ("Van Helsing", "Mr. de
            # Winter"); "the young Englishman" is one.
            particles = sum(norm(w) in PARTICLES for w in name_core(a).split())
            alias_named[norm(a)] |= _proper(a) >= 1 and _proper(a) + particles >= 2
            if particles:
                particled[norm(a)] = {norm(t) for t in _name_tokens(name_core(a))}

    def shares(key: str, x: int, y: int) -> bool:
        """A particle alias ("M. de Morcerf") links two candidates only when its
        proper word is in one's name and the other's too or a descriptor:
        Fernand Mondego and Albert de Morcerf stay two (W16-5)."""
        words, names = particled.get(key), (cands[x]["name"], cands[y]["name"])
        has = [words is None or words <= set(norm(n).split()) for n in names]
        loose = [name_core(n) != strip_notes(n) or not _proper(n) for n in names]
        return any(has) and all(h or d for h, d in zip(has, loose))

    for key, owners in alias_owners.items():
        owners_ok = _all_compatible(owners)
        if key in by_primary and owners_ok:  # rule 2
            for i in alias_cands[key]:
                uf.union(by_primary[key][0], i)
        if len(alias_cands[key]) > 1 and (alias_named[key] or owners_ok):  # rule 3
            idx = alias_cands[key]
            for k, x in enumerate(idx):
                for y in idx[k + 1 :]:
                    if owners_ok or shares(key, x, y):
                        uf.union(x, y)

    # Rule 4: same significant tokens (>= 2), honorifics aside.
    by_sig: dict[frozenset, list[int]] = defaultdict(list)
    for i, c in enumerate(cands):
        sig = significant(c["name"])
        if len(sig) >= 2:
            by_sig[frozenset(sig)].append(i)
    for idxs in by_sig.values():
        for i in idxs[1:]:
            uf.union(idxs[0], i)

    def groups() -> dict[int, list[int]]:
        g: dict[int, list[int]] = defaultdict(list)
        for i in range(len(cands)):
            g[uf.find(i)].append(i)
        return g

    # Rule 5: a group contained in exactly one fuller group joins it.
    sigs = {
        root: [
            significant(f)
            for i in members
            for f in (cands[i]["name"], *cands[i]["aliases"])
            if significant(f)
        ]
        for root, members in groups().items()
    }
    width = {root: max((len(x) for x in sl), default=0) for root, sl in sigs.items()}
    joins: list[tuple[int, int]] = []
    for root, sig_list in sigs.items():
        if not sig_list:
            continue
        hosts = [
            other
            for other, other_sigs in sigs.items()
            if other != root
            and width[other] > width[root]
            and all(any(s <= o for o in other_sigs) for s in sig_list)
        ]
        if len(hosts) == 1:
            joins.append((hosts[0], root))
    for host, root in joins:
        uf.union(host, root)

    # One source scan per distinct surface, however many groups rank it (W-5).
    titles: dict[str, bool] = {}

    def is_title(form: str) -> bool:
        if form not in titles:
            titles[form] = _used_as_title(form, text)
        return titles[form]

    merged = [
        _fold_group([cands[i] for i in members], is_title)
        for members in groups().values()
    ]
    _count_group_mentions(merged, text)
    merged.sort(key=lambda g: (-len(g["windows"]), -g["mentions"], g["name"]))
    return merged


def _name_tokens(form: str) -> list[str]:
    """The capitalised, significant surface tokens of a name form ("Mr. Voss"
    -> ["Voss"]; "Milo Voss" -> ["Milo", "Voss"]). An elision prefix is not
    the case test ("d'Artagnan", "al-Rashid"), and a caseless script is."""
    out = []
    for raw in form.split():
        tok = raw.strip(".,;:!?\"'()[]")
        key = norm(tok)
        head = re.sub(r"^(?:[dl]['’]|al-)", "", tok, flags=re.I)[:1]
        if (
            len(tok) >= 2
            and (head.isupper() or head.isalpha() and not head.islower())
            and key
            and key not in HONORIFICS
            and key not in NON_NAME_TOKENS
        ):
            out.append(tok)
    return out


def _proper(form: str) -> int:
    """How many proper-name words a form holds once notes and a leading
    determiner or possessive are gone: 0 for a descriptor ("my dear sister",
    "Fenwick's father")."""
    return sum(not _possessive(t) for t in _name_tokens(name_core(form)))


def _plain(form: str) -> int:
    """The core's other words, honorifics and particles aside ("dear sister")."""
    words = name_core(form).split()
    skip = HONORIFICS | NON_NAME_TOKENS
    return sum(norm(w) not in skip for w in words) - _proper(form)


def _possessive(word: str) -> bool:
    return word.endswith(("'s", "’s"))


def _count_group_mentions(groups: list[dict], text: str) -> None:
    """Set each merged candidate's `mentions` from the source (SPEC-26).

    A name is counted in every form the candidates use, plus each given name or
    surname of those forms that no OTHER candidate shares: "Milo" counts for
    Milo Voss even when the map never listed it as an alias, while "Harker"
    with both Jonathan and Mina present counts for neither. Pronouns are never
    counted. The candidate's own appearances stay the floor.
    """
    owners: dict[str, set[int]] = defaultdict(set)
    group_tokens: list[dict[str, str]] = []
    for i, g in enumerate(groups):
        toks: dict[str, str] = {}
        for form in (g["name"], *g["aliases"]):
            for tok in _name_tokens(form):
                toks.setdefault(norm(tok), tok)
        group_tokens.append(toks)
        for key in toks:
            owners[key].add(i)
    for i, g in enumerate(groups):
        own = [tok for key, tok in group_tokens[i].items() if owners[key] == {i}]
        if text:
            g["mentions"] = max(
                _count_mentions(text, [g["name"], *g["aliases"], *own]),
                g["mentions"],
            )


def _used_as_title(form: str, text: str) -> bool:
    """Does the source use `form` after a determiner in most of its
    occurrences? "the Chief Executive Lich" is a title, not a name (SPEC-26)."""
    if not text or not form.strip():
        return False
    # The form's own determiner is the one the test looks for (W12-4).
    form = re.sub(r"^\s*(?i:the|a|an|this|that)\s+", "", form)
    pattern = r"\s+".join(map(re.escape, form.split()))
    hits = re.findall(rf"(?<!\w)((?i:the|a|an|this|that)\s+)?{pattern}(?!\w)", text)
    return sum(1 for det in hits if det) * 2 > len(hits)


def _fold_group(members: list[dict], is_title: Callable[[str], bool]) -> dict:
    """One merged candidate: best name, the rest as aliases, windows, and its
    appearances as the mention floor (`_count_group_mentions` counts the text).
    A form the source mostly uses as a title (`is_title`, a memoised
    _used_as_title) ranks below every other form; a one-form group is never
    checked, since it has nothing to rank."""
    primary_counts = Counter(norm(m["name"]) for m in members)
    surfaces: dict[str, Counter] = defaultdict(Counter)
    for m in members:
        name = " ".join(m["name"].split())
        surfaces[norm(name)][name] += 2  # a primary use outranks an alias
        for a in m["aliases"]:
            surfaces[norm(a)][a] += 1

    def surface(key: str) -> str:
        # The most capitalised spelling, then the most used ("The Creature"
        # over a primary "The creature").
        return max(surfaces[key], key=lambda s: (_proper(s), surfaces[key][s]))

    def rank(key: str):
        # Not a title, then most proper-name words (a name always beats a
        # descriptor: "Isolde Sarrow" over "my dear sister"), then fewest
        # other words, then most significant tokens, then most often used
        # as the name. A one-token name keeps its honorific ("Count Vasska"
        # over "Vasska"); a full name drops it ("Nora Vance" over "Miss Nora
        # Vance").
        form = surface(key)
        sig = len(significant(name_core(form)) or significant(key))
        n_tok = len(tokens(key))
        title = len(surfaces) > 1 and is_title(form)
        return (
            not title,
            _proper(form),
            -_plain(form),
            sig,
            primary_counts.get(key, 0),
            n_tok if sig == 1 else -n_tok,
        )

    best_key = max(sorted(surfaces), key=rank)
    name = surface(best_key)
    aliases = [
        surface(k)
        for k in sorted(surfaces, key=lambda k: (-sum(surfaces[k].values()), k))
        if k != best_key
    ]
    hints = Counter(_role(m["role_hint"]) for m in members if m["role_hint"])
    per_window: dict[int, list[str]] = defaultdict(list)
    for m in sorted(members, key=lambda m: m["window"]):
        # A checkpoint written before SPEC-26 holds one string per window.
        for quote in _evidence_list(m["evidence"]):
            if quote not in per_window[m["window"]]:
                per_window[m["window"]].append(quote)
    # Keep up to _MAX_EVIDENCE, spread across windows (each window's first
    # quote before any window's second), then read in source order.
    picked: list[tuple[int, int, str]] = []
    depth = max((len(q) for q in per_window.values()), default=0)
    for k in range(depth):
        for w in sorted(per_window):
            if k < len(per_window[w]) and len(picked) < _MAX_EVIDENCE:
                picked.append((w, k, per_window[w][k]))
    evidence = [(w, q) for w, _k, q in sorted(picked)]
    windows = sorted({m["window"] for m in members})
    # A model may normalise a name the text never spells out; the candidate's
    # own appearances are the floor.
    mentions = len(members)
    return {
        "name": name,
        "aliases": aliases,
        "role_hints": [h for h, _ in hints.most_common()],
        "evidence": evidence,
        "windows": windows,
        "mentions": mentions,
    }


def merge_canon_entries(window_answers: list[tuple[int, dict]]) -> list[dict]:
    """Canon entries merge by normalised name; category by vote; distinct
    contents kept (in source order) rather than one picked."""
    order: list[str] = []
    by_key: dict[str, dict] = {}
    for w, ans in window_answers:
        for e in ans.get("canon_entries", []):
            key = norm(e["name"])
            if not key:
                continue
            if key not in by_key:
                order.append(key)
                by_key[key] = {
                    "names": Counter(),
                    "cats": Counter(),
                    "contents": [],
                    "windows": set(),
                }
            g = by_key[key]
            g["names"][e["name"]] += 1
            g["cats"][e["category"]] += 1
            g["windows"].add(w)
            if e["content"] and e["content"] not in g["contents"]:
                g["contents"].append(e["content"])
    out = []
    for key in order:
        g = by_key[key]
        out.append(
            {
                "name": g["names"].most_common(1)[0][0],
                "category": g["cats"].most_common(1)[0][0],
                "content": "\n\n".join(g["contents"]),
                "windows": len(g["windows"]),
            }
        )
    out.sort(key=lambda e: -e["windows"])
    return out


def merge_style(window_answers: list[tuple[int, dict]]) -> dict:
    votes: dict[str, Counter] = defaultdict(Counter)
    for _w, ans in window_answers:
        sig = ans.get("style_signals") or {}
        for k in ("pov", "tense", "tone", "comps", "narration"):
            if sig.get(k):
                votes[k][sig[k]] += 1

    def top(k: str) -> str:
        return votes[k].most_common(1)[0][0] if votes[k] else ""

    pov = top("pov")
    narration = top("narration")
    if narration and narration.lower() not in (pov or "").lower():
        pov = f"{pov} ({narration})" if pov else narration
    return {
        "pov": pov,
        "tense": top("tense"),
        "tone": top("tone"),
        "comps": top("comps"),
    }


# --- reduce --------------------------------------------------------------------


def _render_candidate(g: dict, total_windows: int) -> str:
    lines = [f"{NAME_HEADING}{g['name']}"]
    if g["aliases"]:
        lines.append("Also called: " + ", ".join(g["aliases"]))
    lines.append(
        f"Mentions: {g['mentions']} across {len(g['windows'])} of "
        f"{total_windows} parts"
    )
    if g["role_hints"]:
        lines.append("Role hints: " + ", ".join(g["role_hints"]))
    if g["evidence"]:
        lines.append(EVIDENCE_LABEL)
        lines.extend(
            EVIDENCE_ITEM.format(part=w + 1, quote=e)
            for w, e in g["evidence"][:_MAX_EVIDENCE]
        )
    quotes = g.get("quotes") or []
    if quotes:
        lines.append(QUOTED_LINES_LABEL)
        lines.extend(f'- "{q}"' for q in quotes[:_MAX_QUOTED_LINES])
    return "\n".join(lines)


def _pick_quotes(lines: list[str]) -> list[str]:
    """Up to _MAX_QUOTED_LINES distinct lines, those of _QUOTE_MIN_WORDS+ words
    first, kept in source order."""
    seen: list[str] = []
    for line in lines:
        line = " ".join(line.split())
        if line and line not in seen:
            seen.append(line)
    ranked = sorted(
        range(len(seen)), key=lambda i: (len(seen[i].split()) < _QUOTE_MIN_WORDS, i)
    )
    return [seen[i] for i in sorted(ranked[:_MAX_QUOTED_LINES])]


def attach_quotes(groups: list[dict], text: str) -> None:
    """Give each merged candidate its own attributed dialogue (`quotes`), via the
    SPEC-17 attributor: only lines a tag or beat credits to them, never a line
    whose speaker could be two candidates ("Harker" with Jonathan and Mina)."""
    from app.parsing.character_extractor import attribute_speech, spoken_lines

    if not text or not groups:
        return
    attributed = attribute_speech(text)
    cast = [{"name": g["name"], "aliases": g["aliases"]} for g in groups]
    for g in groups:
        lines = spoken_lines(
            text,
            g["name"],
            aliases=g["aliases"],
            cast=cast,
            attributed=attributed,
        )
        g["quotes"] = _pick_quotes([line for _i, line in lines])


def _reduce_prompt(
    batch: list[dict], total_windows: int, source: Optional[str] = None
) -> str:
    """The reduce prompt: per candidate, its evidence and its own quoted lines
    (SPEC-26 P10). A single-window source also sends that window whole — it is
    small, and the evidence alone rarely says what anyone wants."""
    cast = "\n\n".join(_render_candidate(g, total_windows) for g in batch)
    source_block = f"{as_quoted_block(source, 'source')}\n\n" if source else ""
    return f"""{STORY_MATERIAL_NOTICE}

{source_block}{as_quoted_block(cast, "characters")}

Write the full character entries for each character headed above, from the
evidence, the quoted lines{" and the source text" if source else ""} above.
Read what each one does and says: description is who they are; goals is what
they want or are trying to do; voice_characteristics is how THEIR quoted lines
sound (diction, rhythm, tics). Do NOT invent anything the material does not
support, and leave a field empty ("", {{}}) only when nothing in it bears on that
field. Keep each name exactly as headed.

Return ONLY a JSON object:
{{"characters": [{{"name": "exactly as headed",
   "role": "{_ROLE_HINT}",
   "description": "who they are",
   "goals": "what they want",
   "arc": "how they change",
   "relationships": {{"Other Name": "how they relate"}},
   "personality_traits": {{"trait": "how it shows"}},
   "voice_characteristics": {{"diction": "", "rhythm": "", "tics": ""}},
   "aliases": ["other names the text uses for them"]}}]}}

JSON:"""


def _fallback_entry(g: dict) -> dict:
    """The map-level proposal for a character reduce did not (or could not) write."""
    return {
        "name": g["name"],
        "role": g["role_hints"][0] if g["role_hints"] else "",
        "description": " ".join(e for _w, e in g["evidence"][:2]),
        "goals": "",
        "arc": "",
        "relationships": {},
        "personality_traits": {},
        "voice_characteristics": {},
        "aliases": list(g["aliases"]),
    }


def _attach(batch: list[dict], raw: dict) -> dict[str, dict]:
    """Map the reduce answer back onto the batch by name (keeps OUR names)."""
    out: dict[str, dict] = {}
    entries = [
        _normalize_character(c)
        for c in (raw.get("characters") if isinstance(raw, dict) else None) or []
        if isinstance(c, dict) and _s(c.get("name"))
    ]
    forms = {norm(f) for g in batch for f in (g["name"], *g["aliases"])}
    for g in batch:
        keys = {norm(g["name"]), *(norm(a) for a in g["aliases"])}
        hit = next((e for e in entries if norm(e["name"]) in keys), None)
        if hit is None:
            continue
        entry = {**hit, "name": g["name"]}
        # A reduce alias that is another candidate's name is not theirs.
        extra = [a for a in hit["aliases"] if norm(a) not in forms - keys]
        entry["aliases"] = _clean_aliases(g["name"], [*g["aliases"], *extra])
        if not entry["role"] and g["role_hints"]:
            entry["role"] = g["role_hints"][0]
        out[g["name"]] = entry
    return out


async def _reduce_batch(
    batch: list[dict],
    total_windows: int,
    user_id,
    stats: dict,
    source: Optional[str] = None,
) -> tuple[dict[str, dict], list[str]]:
    async def call(part: list[dict]) -> dict[str, dict]:
        raw = await _json_call(
            _reduce_prompt(part, total_windows, source),
            fast=False,
            max_tokens=min(8192, 400 * len(part) + 512),
            user_id=user_id,
            purpose="extraction_reduce",
            stats=stats,
        )
        return _attach(part, raw)

    try:
        return await call(batch), []
    except _Unreadable:
        pass
    written: dict[str, dict] = {}
    warnings: list[str] = []
    mid = max(1, len(batch) // 2)
    for part in (batch[:mid], batch[mid:]):
        if not part:
            continue
        try:
            written.update(await call(part))
        except _Unreadable:
            warnings.append(
                "Full entries could not be written for: "
                + ", ".join(g["name"] for g in part)
                + " — their proposals carry only what the source scan found."
            )
    return written, warnings


async def _synopsis(
    characters: list[dict],
    entries: list[dict],
    summaries: list[str],
    user_id,
    stats: dict,
) -> str:
    cast = "\n\n".join(
        "\n".join(
            x
            for x in (
                f"{NAME_HEADING}{c['name']}",
                f"Role: {c['role']}" if c["role"] else "",
                c["description"],
            )
            if x
        )
        for c in characters
    )
    world = "\n".join(
        f"- {e['name']} ({e['category']}): {e['content']}".rstrip(": ") for e in entries
    )
    story = "\n".join(f"- {s}" for s in summaries if s)
    prompt = f"""{STORY_MATERIAL_NOTICE}

{as_quoted_block(cast, "characters")}

{as_quoted_block(world, "world")}

{as_quoted_block(story, "summaries")}

Write the book's synopsis: 2-4 sentences grounded in the material above (the
cast, the world, and the part-by-part summaries, in order). Do NOT invent.

Return ONLY a JSON object: {{"synopsis": "..."}}

JSON:"""
    try:
        raw = await _json_call(
            prompt,
            fast=False,
            max_tokens=1024,
            user_id=user_id,
            purpose="extraction_synopsis",
            stats=stats,
        )
    except _Unreadable:
        return ""
    return _s(raw.get("synopsis")) if isinstance(raw, dict) else ""


# --- entrypoint ----------------------------------------------------------------


def _text_hash(windows: list[str]) -> str:
    h = hashlib.sha256()
    for w in windows:
        h.update(w.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


async def _save(save_checkpoint: SaveCheckpoint, data: dict) -> None:
    if save_checkpoint is not None:
        await save_checkpoint(data)


async def run_map(
    windows: list[str],
    *,
    user_id: Optional[UUID] = None,
    checkpoint: Optional[dict] = None,
    save_checkpoint: SaveCheckpoint = None,
) -> dict:
    """The map loop: one call per window, checkpointed after each window.

    `checkpoint`/`save_checkpoint` have the shape `run_prose_scene_workflow`
    uses (SPEC-08): a resumed job skips every window already answered. A
    checkpoint taken over different text is discarded.
    """
    checkpoint = dict(checkpoint or {})
    text_hash = _text_hash(windows)
    if checkpoint.get("text_hash") != text_hash:
        checkpoint = {"text_hash": text_hash}
    checkpoint.setdefault("map", {})
    checkpoint.setdefault("warnings", [])
    checkpoint.setdefault("stats", {"calls": 0, "tokens": 0})
    checkpoint["phase"] = "map"
    checkpoint["windows_total"] = len(windows)
    done: dict = dict(checkpoint["map"])
    for i, window in enumerate(windows):
        if str(i) in done:
            continue
        stats = dict(checkpoint["stats"])
        answers, warnings = await _map_one(
            window, str(i + 1), len(windows), user_id, stats
        )
        done[str(i)] = answers
        checkpoint.update(
            map=dict(done),
            stats=stats,
            warnings=[*checkpoint["warnings"], *warnings],
            windows_done=len(done),
        )
        await _save(save_checkpoint, checkpoint)
    checkpoint["windows_done"] = len(done)
    return checkpoint


async def extract_canon(
    text: str,
    *,
    user_id: Optional[UUID] = None,
    checkpoint: Optional[dict] = None,
    save_checkpoint: SaveCheckpoint = None,
    window_tokens: int = WINDOW_TOKENS,
) -> dict:
    """Propose typed canon candidates from source text, map-reduce. Never writes.

    Returns the reviewed-proposal shape plus `stats` and `warnings`; every
    character proposal carries `mentions` and `windows` so the review UI can
    rank them and pre-deselect one-window names.
    """
    text = clean_for_llm(text, max_chars=MAX_SOURCE_CHARS, label="source")
    windows = split_windows(text, window_tokens)
    if not windows:
        return {**_normalize_proposals({}), "stats": {"windows": 0}, "warnings": []}

    checkpoint = await run_map(
        windows,
        user_id=user_id,
        checkpoint=checkpoint,
        save_checkpoint=save_checkpoint,
    )
    answers: list[tuple[int, dict]] = [
        (int(i), a)
        for i, lst in sorted(checkpoint["map"].items(), key=lambda kv: int(kv[0]))
        for a in lst
    ]
    groups = merge_characters(answers, text)
    entries = merge_canon_entries(answers)
    style = merge_style(answers)
    total = len(windows)

    # Reduce: full entries for the best-attested characters, ~10 per call,
    # each with its own attributed dialogue (and, for a one-window source, the
    # window itself).
    reduced = groups[:MAX_REDUCED_CHARACTERS]
    attach_quotes(reduced, text)
    single_source = windows[0] if total == 1 else None
    batches = [
        reduced[i : i + REDUCE_BATCH] for i in range(0, len(reduced), REDUCE_BATCH)
    ]
    done_reduce: dict = dict(checkpoint.get("reduce") or {})
    checkpoint.update(phase="reduce", reduce_total=len(batches))
    for b, batch in enumerate(batches):
        if str(b) in done_reduce:
            continue
        stats = dict(checkpoint["stats"])
        written, warnings = await _reduce_batch(
            batch, total, user_id, stats, single_source
        )
        done_reduce[str(b)] = written
        checkpoint.update(
            reduce=dict(done_reduce),
            reduce_done=len(done_reduce),
            stats=stats,
            warnings=[*checkpoint["warnings"], *warnings],
        )
        await _save(save_checkpoint, checkpoint)
    written_all: dict[str, dict] = {}
    for part in done_reduce.values():
        written_all.update(part)

    characters = []
    for g in groups:
        entry = written_all.get(g["name"]) or _fallback_entry(g)
        characters.append(
            {
                **entry,
                "mentions": g["mentions"],
                "windows": len(g["windows"]),
                # The review's default (eval-plan E5): seen in 2+ windows, or a
                # narrator. One-window names are proposed but pre-deselected.
                "selected": total == 1
                or len(g["windows"]) >= 2
                or entry.get("role") == "narrator"
                or "narrator" in g["role_hints"],
            }
        )

    # Synopsis last, from the merged canon.
    if "synopsis" not in checkpoint:
        checkpoint["phase"] = "synopsis"
        stats = dict(checkpoint["stats"])
        summaries = [a.get("summary", "") for _w, a in answers]
        checkpoint["synopsis"] = await _synopsis(
            characters[:MAX_REDUCED_CHARACTERS], entries, summaries, user_id, stats
        )
        checkpoint["stats"] = stats
        await _save(save_checkpoint, checkpoint)

    stats = checkpoint["stats"]
    return {
        "characters": characters,
        "canon_entries": [
            {k: e[k] for k in ("name", "category", "content")} for e in entries
        ],
        "style": style,
        "synopsis": checkpoint["synopsis"],
        "stats": {
            "windows": total,
            "calls": stats.get("calls", 0),
            "tokens": stats.get("tokens", 0),
            "reduce_calls": len(batches),
        },
        "warnings": list(checkpoint["warnings"]),
    }


def plan_estimate(text: str, window_tokens: int = WINDOW_TOKENS) -> dict:
    """What an extraction of `text` will cost, before it is enqueued (R7.3/R7.5).

    Upper-bound-ish: every window's full input plus its answer budget, a reduce
    call per ~10 expected characters (each carrying a one-window source whole),
    and the synopsis.
    """
    windows = split_windows(text, window_tokens)
    n = len(windows)
    map_tokens = sum(
        estimate_tokens(w) + 600 + _map_max_tokens(w) // 2 for w in windows
    )
    expected_chars = min(MAX_REDUCED_CHARACTERS, 8 + 6 * n)
    reduce_calls = math.ceil(expected_chars / REDUCE_BATCH) if n else 0
    # A one-window source is sent whole with every reduce call (single_source).
    source = estimate_tokens(windows[0]) if n == 1 else 0
    reduce_tokens = reduce_calls * (2_500 + 2_500 + source)
    synopsis_tokens = 3_000 if n else 0
    return {
        "windows": n,
        "map_calls": n,
        "reduce_calls": reduce_calls,
        "requests": n + reduce_calls + (1 if n else 0),
        "estimated_tokens": map_tokens + reduce_tokens + synopsis_tokens,
    }
