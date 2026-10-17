"""Per-character voice-context assembly for scene generation.

Prose mode writes a whole beat in ONE LLM call, so each involved character's
voice grounding (retrieved voice samples; the bible itself rides in the canon
block, SPEC-26) is assembled into a compact context block instead of driving
per-turn calls.

The cast arrives as Character ROWS, already resolved and validated against the
book at the API (SPEC-01). It used to arrive as free-text names, and a name that
matched nothing became "(No bible entry — infer a consistent voice.)": a typo
silently produced voice-blind prose. That branch is gone because it is now
unreachable.
"""

import re
from typing import Iterable, Mapping, NamedTuple, Optional
from uuid import UUID

from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_async_session
from app.core.llm_text import as_quoted_block, clean_for_llm
from app.core.orm_models import Character
from app.core.prompt_markers import MOVEMENT_LABEL, NAME_HEADING, VOICE_SAMPLES_LABEL
from app.parsing.character_extractor import SPEECH_MIN_WORDS, source_location
from app.rag.store import get_chunk_store

# A grounding record keeps a bounded excerpt of each sample's text as it was at
# generation time, so a sample deleted since still shows what grounded the
# scene (R5.3). Bounded: the record lives in Scene.evaluation_scores and is
# provenance, not a second copy of the voice index.
GROUNDING_EXCERPT_CHARS = 300
NO_SAMPLES_REASON = "no indexed voice samples"
CONTEXT_ONLY_REASON = "context only"


class SceneScope(NamedTuple):
    """What the book plans, and who a scene may mention (SPEC-30, -36, -40).
    Built once per scene (or per probe), never per beat; the scene's own beat
    lines join `plot` once planned."""

    cast: tuple  # the SCENE's Character rows: _centres' "others"
    book_cast: tuple  # every live Character of the book
    secondary_names: tuple[str, ...]  # the scene request's secondary_names
    known: frozenset[str]  # canon.canon_terms()
    known_text: str  # canon_known_text(canon), raw
    # Every line of the plan a movement sample may not restate: the book's
    # planned scenes (the brief is one), the chapter's earlier written beat
    # lines, and this scene's beat lines once planned. Positional: a scene's
    # plot[0] is its brief, kept even when empty (the scripted rule reads it).
    plot: tuple[str, ...]
    later_from: Mapping[str, int]  # source_id -> first paragraph of later plot

    @classmethod
    def of(
        cls,
        canon,
        scene_cast: Iterable,
        secondary_names: Iterable[str],
        plot: Iterable[str],
        later_from: Mapping[str, int],
    ) -> "SceneScope":
        from app.planning.fidelity import canon_known_text

        return cls(
            tuple(scene_cast),
            tuple(canon.characters),
            tuple(secondary_names),
            frozenset(canon.canon_terms()),
            canon_known_text(canon),
            tuple(plot),
            later_from,
        )


def grounding_excerpt(text_value: Optional[str]) -> Optional[str]:
    """The first ~300 chars of a sample, cut at a word boundary when possible."""
    if not text_value:
        return None
    text_value = text_value.strip()
    if len(text_value) <= GROUNDING_EXCERPT_CHARS:
        return text_value
    cut = text_value[:GROUNDING_EXCERPT_CHARS]
    space = cut.rfind(" ")
    if space > GROUNDING_EXCERPT_CHARS // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


def unsampled_entry(character: Character, reason: str) -> dict:
    """A grounding entry for a character no sample grounded, and why."""
    return {
        "character_id": str(character.id),
        "chunk_id": None,
        "score": None,
        "reason": reason,
    }


def grounding_entries(character: Character, samples: list[dict]) -> list[dict]:
    """One character's grounding-record entries for one beat.

    A character with no samples still gets an entry (no chunk, with a reason),
    so the grounding view lists every character in the scene instead of
    silently omitting the ones written without voice samples.
    """
    if not samples:
        return [unsampled_entry(character, NO_SAMPLES_REASON)]
    return [
        {
            "character_id": str(character.id),
            "chunk_id": s.get("id"),
            "score": s.get("score"),
            "excerpt": grounding_excerpt(s.get("text")),
        }
        for s in samples
    ]


async def load_characters_for_book(
    names: list[str],
    user_id: UUID,
    book_id: UUID,
) -> dict[str, Character]:
    """Character rows by name, scoped to one book and the requesting user.

    The book filter is strict equality. It used to read

        (Character.book_id == book_id) | (Character.book_id.is_(None))

    which "worked" only because the IS NULL branch matched everything —
    `book_id` was never written, so every character was NULL-scoped and the
    filter was a no-op wearing a filter's clothes (docs/ADR-002-book-as-root.md
    §1). `user_id` stays as defence in depth — the tenant guard from 0005.
    """
    async with get_async_session() as session:
        query = select(Character).where(
            Character.book_id == book_id,
            Character.user_id == user_id,
            Character.deleted_at.is_(None),  # trashed: not castable (SPEC-13)
        )
        rows = (await session.execute(query)).scalars().all()
    by_name = {c.name: c for c in rows}
    return {name: by_name[name] for name in names if name in by_name}


async def build_character_context(
    character: Optional[Character],
    name: str,
    beat_description: str,
    max_samples: int = 5,
    *,
    scope: SceneScope,
    chunk_types: Optional[Iterable[str]] = None,
    grounding: Optional[list] = None,
    bible: bool = False,
) -> str:
    """One character's context block: their name, then their own words
    ("Voice samples") and how they move ("How they move"), retrieved separately
    (SPEC-17).

    `bible=True` also renders the bible summary (role, description, goals, arc,
    voice) under the name. Beat prose and revise leave it off: their prompts
    already carry the whole canon (`canon.full_block()`, BRD R1.1), so a second
    copy per beat character only spent tokens (SPEC-26 P8). The ensemble agent
    has no canon block and asks for it.

    `grounding`, when given, collects the retrieved chunks (id, score and a
    bounded text excerpt) so the caller can record which samples grounded this
    generation (R5.3); a character with none gets a reason entry instead.
    `scope.cast` is the scene's cast: a narration sample that centres another
    of them rather than this character is left out (SPEC-30). `chunk_types`,
    when given, limits both blocks to those types.
    """
    if character is None:
        return render_character(name, [], [])
    bio = _bible_lines(character) if bible else []
    words, movement = [], []
    if max_samples > 0:
        words, movement = await character_samples(
            character,
            beat_description,
            max_samples,
            scope=scope,
            threshold=settings.RAG_SCORE_THRESHOLD,
            chunk_types=chunk_types,
        )
        if grounding is not None:
            grounding.extend(grounding_entries(character, [*words, *movement]))
    return render_character(name, words, movement, bio)


def render_character(name: str, words: list, movement: list, bio=()) -> str:
    """The one character-block renderer (SPEC-17): `### name`, the `bio` lines,
    their own words under VOICE_SAMPLES_LABEL, how they move under
    MOVEMENT_LABEL. Beat prose, revise, the ensemble agent and the voice probe
    all print a character through it."""
    lines = [f"{NAME_HEADING}{name}", *bio]
    if words:
        lines.append(VOICE_SAMPLES_LABEL)
        # These samples ARE the voice grounding — the product's whole premise.
        # They used to be cut to 200 chars, which severed most of them
        # mid-sentence and taught the model half a cadence.
        lines.extend(f'- "{clean_for_llm(s["text"])}"' for s in words)
    if movement:
        lines.append(MOVEMENT_LABEL)
        lines.extend(f"- {clean_for_llm(s['text'])}" for s in movement)
    return "\n".join(lines)


def _bible_lines(character: Character) -> list[str]:
    """Role / Description / Goals / Arc / Voice lines, whole.

    The bible arrives whole. These fields used to be sliced to 300/200/200
    chars — the same silent-truncation habit that cut the synopsis to 6.5%
    (docs/BRD.md §1). A full cast entry is ~500 tokens against a 1M window.
    """
    lines: list[str] = []
    for attr, label in (
        ("role", "Role"),
        ("description", "Description"),
        ("goals", "Goals"),
        ("arc", "Arc"),
    ):
        value = getattr(character, attr, None)
        if value:
            lines.append(f"{label}: {clean_for_llm(value)}")
    voice = getattr(character, "voice_characteristics", None)
    if voice:
        from app.planning.canon import _render_json_field

        rendered = _render_json_field(voice, "Voice")
        if rendered:
            lines.append(f"Voice: {rendered}")
    return lines


# The prompt labels are markers (SPEC-17; app.core.prompt_markers). Only the
# character's own words sit under VOICE_SAMPLES_LABEL — dialogue the attributor
# credited to them, or first-person narration of their own section (SPEC-09).
# Everything else is movement, under MOVEMENT_LABEL.
_MOVEMENT_SAMPLES = 3
# Voice and movement each fetch the character's pool, so the nearest rows cannot
# all be ineligible; eligibility, then the threshold, then k (SPEC-40, SPEC-53).
_POOL = 50
# Ensemble agents are grounded on how they SPEAK and how they MOVE separately
# (BRD R6.2): one mixed retrieval lets dialogue crowd action out. The ingest
# classifier under-labels, so a movement block with fewer than this many hits
# falls back to untyped retrieval rather than leaving the agent ungrounded. The
# fallback NEVER feeds the voice block: narration is not a character's words.
_MIN_TYPED_HITS = 2
_ACTION_TYPES = ("action", "description", "thought")
_DIALOGUE = "dialogue"
# Content words a movement sample shares with a plan line: it narrates a planned
# event, which the plan already carries (SPEC-36, SPEC-40). Unrelated pairs
# share at most 2-3, event pairs 5-6 (the run-W3, W4 and W4b2 archives).
RESTATES_MIN = 4
# Within this many words of its best line, the earlier chapter takes it (SPEC-40).
TIE_WORDS = 1


# A voice sample sharing a run of this many words, pronouns folded, with the beat
# line or the brief scripts the line the beat asks for (SPEC-46).
SCRIPTED_RUN = 6
_FOLD = dict.fromkeys(("his", "her", "your", "my", "their"), "P")
_FOLD |= dict.fromkeys(("he", "she", "you", "i", "they"), "S")
_FOLD |= dict.fromkeys(("him", "me", "them"), "O")


def word_runs(text: str) -> set[tuple[str, ...]]:
    """Every SCRIPTED_RUN-word run of `text`, pronouns folded."""
    w = re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)*", text.lower().replace("’", "'"))
    w = [_FOLD.get(t, t) for t in w]
    return {tuple(w[i : i + SCRIPTED_RUN]) for i in range(len(w) - SCRIPTED_RUN + 1)}


def _words(text: str, drop: Iterable[str] = ()) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 3} - set(drop)


def restates(text: str, line: str) -> bool:
    """Does `text` narrate one plan line's event (a planned scene, a beat line,
    the brief)? The ONE event rule (SPEC-36, SPEC-40): RESTATES_MIN shared
    content words (`[a-z]+` longer than 3 letters, not function words). Plan
    lines only, never prose; "" makes it inert."""
    from app.planning.fidelity import FUNCTION_WORDS

    return len(_words(text) & _words(line, FUNCTION_WORDS)) >= RESTATES_MIN


def later_plot_from(
    paragraphs: list[str], chapter: int, plan: list[tuple[int, dict]]
) -> Optional[int]:
    """The first paragraph of a source whose event the plan places after
    `chapter`, or None (SPEC-40). A paragraph's chapter is that of the plan line
    its narration (speech left out: one speech rule) shares the most content
    words with, at RESTATES_MIN or more; within TIE_WORDS of its best line, the
    earliest chapter takes it, so a paragraph's chapter does not depend on the
    scope (monotone)."""
    from app.parsing.character_extractor import narration_of
    from app.planning.fidelity import FUNCTION_WORDS
    from app.planning.planned_beat import beat_scene_description

    lines = [(n, _words(beat_scene_description(b), FUNCTION_WORDS)) for n, b in plan]
    for i, paragraph in enumerate(paragraphs):
        words = _words(narration_of(paragraph))
        scores = [(len(words & w), n) for n, w in lines]
        best = max((s for s, _ in scores), default=0)
        floor = max(RESTATES_MIN, best - TIE_WORDS)
        placed = min((n for s, n in scores if s >= floor), default=0)
        if best >= RESTATES_MIN and placed > chapter:
            return i
    return None


def _location(sample: dict) -> tuple[str, int] | tuple[None, None]:
    """A chunk's (kind, paragraph), read by the module that writes the format."""
    return source_location(str(sample.get("source") or "")) or (None, None)


def _own_narration(sample: dict) -> bool:
    """First-person narration indexed for its narrator (SPEC-09): their words."""
    return _location(sample)[0] == "narration"


def _later_plot(sample: dict, scope: SceneScope) -> bool:
    """Is the sample at or past its source's later plot (SPEC-40)?"""
    at = _location(sample)[1]
    return at is not None and at >= scope.later_from.get(
        sample.get("source_id"), at + 1
    )


_QUOTED = re.compile(r"(?<!\w)['‘\"“]([^'‘’\"“”]+)['’\"”](?!\w)")


# Not a marker (nothing parses it): the sentence above the `<owned>` block.
OWNED_PHRASES_NOTE = "Phrases only their owner says (no one in this beat may say them):"


def owned_phrases(character) -> list[str]:
    """The quoted spans of two or more words in a character's voice field: the
    phrases the author wrote as theirs alone (SPEC-42). Nothing is inferred."""
    from app.planning.canon import _render_json_field

    voice = getattr(character, "voice_characteristics", None)
    spans = _QUOTED.findall(_render_json_field(voice, "Voice") or "")
    return [p for p in dict.fromkeys(spans) if len(p.split()) >= 2]


def _forms(member) -> list[str]:
    """A cast member's name forms — full name, aliases, and each significant
    name token (a book calls "Zara Okafor" "Zara"). Words that are not names
    ("the", "old", "van") are never a form on their own: "the" is not "the
    Lich" (wave-3 review B-2)."""
    from app.parsing.names import NON_NAME_TOKENS, significant

    if isinstance(member, dict):
        name, aliases = member.get("name") or "", member.get("aliases") or []
    else:
        name = getattr(member, "name", "") or ""
        aliases = getattr(member, "aliases", None) or []
    forms = [name, *aliases]
    forms += [
        t for f in list(forms) for t in significant(f) - NON_NAME_TOKENS if len(t) > 2
    ]
    return [f for f in forms if f and f.strip()]


def _names_in(text: str, form: str) -> bool:
    """Does `text` name `form`? A multi-word form matches in any case and
    spacing; a one-word form only where the text capitalises it, so "the chief
    reason" does not name the Chief Executive Lich. Residual: a sentence-initial
    "Will" still names a Will."""
    words = form.split()
    pattern = r"(?<!\w)" + r"\s+".join(map(re.escape, words)) + r"(?!\w)"
    hits = re.finditer(pattern, text, re.IGNORECASE)
    return any(len(words) > 1 or m.group(0)[0].isupper() for m in hits)


def names_outside_scene(text: str, character, scope: SceneScope) -> list[str]:
    """The people `text` names who are not in this scene (SPEC-30): the ONE
    "this sample is about someone else's story" rule. A person is in the scene
    when they are `character`, in `scope.cast` or in `scope.secondary_names`.
    Outside it are (a) any other member of the book's cast, and (b) any
    capitalised name the canon does not know ("the Bell account", chapters
    later). A canon-known non-cast name (Prava, in the synopsis) is not caught."""
    from app.planning.fidelity import unknown_proper_nouns

    inside = [character, *scope.cast]
    names = {getattr(c, "name", None) for c in inside}
    own = [f for c in inside for f in _forms(c)]
    own_low = {f.lower() for f in own}
    outside = [
        m.name
        for m in scope.book_cast
        if m.name not in names
        and any(_names_in(text, f) for f in _forms(m) if f.lower() not in own_low)
    ]
    known = {*scope.known, *own, *scope.secondary_names}
    return outside + unknown_proper_nouns(text, known, canon_text=scope.known_text)


_LEAD_NAME = re.compile(
    r"^\W*((?:(?:Mr|Mrs|Ms|Dr|Prof|St)\.\s+)?[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,3})"
)


def _centres(
    text: str, name: str, cast: Optional[Iterable] = None, aliases=None
) -> bool:
    """Does this narration centre the character (SPEC-17)? When its first
    sentence opens with them, or with another cast member, that subject
    decides: "Milo Voss read the appendices. Zara Okafor watched him read
    them." centres Milo, not Zara. Otherwise it centres them unless it names
    another cast member. Places and other proper names never disqualify it:
    "Milo walked the Marrow District at dusk" is Milo's (wave-1 review S2)."""
    from app.parsing.names import NON_NAME_TOKENS, match, norm

    own = _forms({"name": name, "aliases": aliases or []})
    own_low = {f.lower() for f in own}
    others = [
        f
        for member in cast or []
        if (member.get("name") if isinstance(member, dict) else member.name) != name
        for f in _forms(member)
        if f.lower() not in own_low
    ]
    first = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]
    lead = _LEAD_NAME.match(first)
    if lead:
        words = lead.group(1).split()
        # "Then Milo …": try the name with leading words dropped as well.
        tails = [" ".join(words[i:]) for i in range(len(words))]
        # "The chief reason …" opens with no one: "The" is not "the Lich".
        tails = [t for t in tails if set(norm(t).split()) - NON_NAME_TOKENS]
        for forms, centred in ((own, True), (others, False)):
            if any(match(t, f) for t in tails for f in forms):
                return centred
    return not any(_names_in(text, f) for f in others)


async def character_samples(
    character: Character,
    query: str,
    k: int,
    *,
    scope: SceneScope,
    threshold: float,
    chunk_types: Optional[Iterable[str]] = None,
) -> tuple[list[dict], list[dict]]:
    """(their own words, how they move) for one character, retrieved separately:
    up to `k` words and min(k, 3) movement samples. The one voice retrieval
    (SPEC-17): scenes, revise, the ensemble and the voice probe all use it.

    Voice is dialogue chunks plus first-person narration of their own section,
    lines of 3+ words, none scripted (SPEC-46); no untyped fallback. Movement
    is action / description / thought, falling back to untyped retrieval (minus
    dialogue) when the classifier under-labelled. It keeps only narration that
    centres them, names no one outside the scene (`names_outside_scene`) and
    restates no line of `scope.plot` (`restates`): a block shows only eligible
    lines, judged before the limit (SPEC-30, SPEC-36, SPEC-40, SPEC-53).
    Neither block shows a sample from its source's later plot
    (`scope.later_from`). `threshold` is
    the store's score floor (`score_threshold`), which every caller passes.
    """
    k_voice, k_move = k, min(_MOVEMENT_SAMPLES, k)
    allowed = set(chunk_types) if chunk_types else None
    voice_types = [_DIALOGUE] if allowed is None or _DIALOGUE in allowed else []
    move_types = [t for t in _ACTION_TYPES if allowed is None or t in allowed]

    # The beat line (`query`) and the brief (scene_setup's first `plot` line).
    script = word_runs(query) | word_runs(scope.plot[0] if scope.plot else "")

    def scripted(s: dict) -> bool:
        return bool(word_runs(s.get("text") or "") & script)

    def spoken(s: dict) -> bool:  # voice eligibility, before the threshold
        return (
            (s.get("chunk_type") or _DIALOGUE) == _DIALOGUE
            # "So!" and "Zara," carry no voice (W3-5): the speech minimum.
            and len((s.get("text") or "").split()) >= SPEECH_MIN_WORDS
            and not _later_plot(s, scope)
            and not scripted(s)
        )

    words = []
    if voice_types:
        words = await _retrieve(character, query, _POOL, voice_types, threshold, spoken)
        words = words[:k_voice]
    aliases = getattr(character, "aliases", None) or []
    name = getattr(character, "name", "") or ""

    voiced = {s.get("id") for s in words}

    def moves(s: dict) -> bool:  # movement eligibility, before the threshold
        text = s.get("text") or ""
        return (
            s.get("id") not in voiced  # an untyped row can be a voice sample
            and s.get("chunk_type") != _DIALOGUE
            and (allowed is None or s.get("chunk_type") in allowed)
            and not _later_plot(s, scope)
            # First-person narration of their own section is their voice: it
            # is admitted here and split off below.
            and (
                _own_narration(s)
                or (
                    (
                        _location(s)[0] != "paragraph"
                        or _centres(text, name, scope.cast, aliases)
                    )
                    and not names_outside_scene(text, character, scope)
                    and not any(restates(text, p) for p in scope.plot)
                )
            )
        )

    body = (
        await _retrieve(character, query, _POOL, move_types, threshold, moves)
        if move_types
        else []
    )
    if move_types and len(body) < _MIN_TYPED_HITS:
        typed = {s.get("id") for s in body}
        body += [
            s
            for s in await _retrieve(character, query, _POOL, None, threshold, moves)
            if s.get("id") not in typed
        ]
    own = [s for s in body if _own_narration(s) and not scripted(s)]
    words += own[: max(0, k_voice - len(words))]
    return words, [s for s in body if not _own_narration(s)][:k_move]


async def _retrieve(
    character: Character,
    query: str,
    k: int,
    chunk_types: Optional[Iterable[str]],
    threshold: float,
    keep=None,
) -> list[dict]:
    """Voice samples for one character. Retrieve across ALL chunk types unless a
    caller asks for specific ones: the ingest-time classifier under-labels, so a
    type filter alone can starve grounding to nothing. Dialogue ranks first."""
    store = get_chunk_store()
    common = dict(
        character_id=str(character.id),
        query=query,
        keep=keep,
        score_threshold=threshold,
        user_id=str(character.user_id) if character.user_id else None,
        book_id=str(character.book_id) if character.book_id else None,
    )
    if chunk_types:
        samples: list[dict] = []
        for t in chunk_types:
            samples.extend(await store.retrieve_similar(k=k, chunk_type=t, **common))
        unique: dict = {}
        for s in samples:  # a store may return one chunk under several types
            unique.setdefault(s.get("id") or id(s), s)
        samples = sorted(unique.values(), key=lambda s: -(s.get("score") or 0))[:k]
    else:
        samples = await store.retrieve_similar(k=k, **common)
    samples.sort(key=lambda s: s.get("chunk_type") != "dialogue")
    return samples


async def build_cast_context(
    characters: list[Character],
    beat_description: str,
    *,
    scope: SceneScope,
    grounding: Optional[list] = None,
    bible: bool = False,
) -> str:
    """Fenced context blocks for every character in a beat: name, voice samples,
    movement. No bible fields by default — beat prose and revise send the whole
    canon alongside this, and it already holds them (SPEC-26 P8).

    Each block is story material (bible text and voice samples can originate in
    an uploaded file), so it is fenced with `as_quoted_block`
    (docs/ADR-002-book-as-root.md §4). Then, once, `OWNED_PHRASES_NOTE` and an
    `<owned>` block: "{name}: '{phrase}'" for each book cast member who is not
    in the beat and owns a phrase (SPEC-42).
    """
    blocks = []
    for character in characters:
        block = await build_character_context(
            character,
            character.name,
            beat_description,
            scope=scope,
            grounding=grounding,
            bible=bible,
        )
        blocks.append(as_quoted_block(block, "character"))
    beat = {c.name for c in characters}
    owned = [
        clean_for_llm(f"{m.name}: " + ", ".join(f"'{p}'" for p in phrases))
        for m in scope.book_cast
        if m.name not in beat and (phrases := owned_phrases(m))
    ]
    fenced = as_quoted_block("\n".join(owned), "owned")
    blocks.append(fenced and f"{OWNED_PHRASES_NOTE}\n{fenced}")
    return "\n\n".join(b for b in blocks if b)


async def build_agent_context(
    character: Optional[Character],
    name: str,
    beat_description: str,
    *,
    scope: SceneScope,
    k: int = 3,
    grounding: Optional[list] = None,
) -> str:
    """A character agent's fenced context: bible, then <voice> (their own words)
    and <physicality> (action/description) samples retrieved separately
    (SPEC-14, SPEC-17). <voice> never falls back to narration. Physicality that
    centres another member of `scope.cast` is left out."""
    # The ensemble prompt has no canon block, so the agent keeps its bible.
    bio = _bible_lines(character) if character else []
    blocks = [as_quoted_block(render_character(name, [], [], bio), "character")]
    if character is None:
        return "\n\n".join(b for b in blocks if b)

    voice, body = await character_samples(
        character,
        beat_description,
        k,
        scope=scope,
        threshold=settings.RAG_SCORE_THRESHOLD,
    )
    if grounding is not None:
        grounding.extend(grounding_entries(character, [*voice, *body]))
    if voice:
        blocks.append(
            as_quoted_block(
                "\n".join(f'- "{clean_for_llm(s["text"])}"' for s in voice), "voice"
            )
        )
    if body:
        blocks.append(
            as_quoted_block(
                "\n".join(f"- {clean_for_llm(s['text'])}" for s in body),
                "physicality",
            )
        )
    return "\n\n".join(b for b in blocks if b)
