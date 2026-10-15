"""Character extraction and content chunking for RAG"""

from typing import List, Dict, Optional
from uuid import UUID
import re
import json

from app.core.models import ChunkType
from app.core.logging_config import setup_logging
from app.llm.client import get_llm_client
from app.llm.json_utils import extract_json_array

logger = setup_logging("parsing.character_extractor")

# Real prose (Gutenberg, Standard Ebooks, Word, most published text) uses smart
# quotes. Dialogue detection matched only straight ASCII quotes, so it found
# ZERO dialogue in curly-quoted manuscripts — every character's spoken voice was
# invisible to indexing. Normalize smart→straight before matching.
_SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "″": '"'})
# Speech tags vary widely; the old set was just said/asked/replied.
_SPEECH_VERBS = (
    "said|asked|replied|responded|shouted|whispered|muttered|cried|exclaimed|"
    "answered|continued|added|declared|murmured|remarked|returned|observed|"
    "demanded|inquired|repeated|began|went on"
)


def _normalize_quotes(text: str) -> str:
    return text.translate(_SMART_QUOTES)


# A section heading that names its first-person narrator: "Jonathan Harker's
# Journal", "_Dr. Seward's Diary._", "Letter, Mina Murray to Lucy Westenra",
# "Telegram from X to Y". Matched on short, heading-like paragraphs only.
_NARRATOR_HEAD = re.compile(
    r"^[\s_*]*(?:"
    r"(?P<owner>[A-Za-z][\w.' -]{1,60}?)'s\s+(?:phonograph\s+)?"
    r"(?:journal|diary|memorandum|memoranda|log|notebook|narrative|statement|"
    r"account|papers|confession)\b"
    r"|(?:letter|telegram|note|postcard)[,:]?\s+(?:from\s+)?"
    r"(?P<writer>[A-Za-z][\w.' -]{1,60}?)[,\s]+to\s"
    r")",
    re.I,
)
_CHAPTER_HEAD = re.compile(r"^[\s_*]*(?:chapter|book|part)\b[\s\w.:-]{0,60}$", re.I)
_FIRST_PERSON = re.compile(r"\b(?:I|[Mm]y|[Mm]e|[Mm]yself|[Ww]e|[Oo]ur|[Uu]s)\b")
_HEADING_MAX_CHARS = 140


# Single-quoted speech ('My name is Hollis,' he said). An opening quote is not
# preceded by a word character and a closing one is not followed by one, so
# apostrophes (I'm, Hollis's, the dogs' bones) neither open nor close a span.
# DOTALL: hard-wrapped speech crosses line breaks inside one paragraph.
_SINGLE_QUOTED = re.compile(r"(?<!\w)'(?=\S).*?(?<=\S)'(?!\w)", re.DOTALL)


def _narration_only(paragraph: str) -> str:
    """The paragraph minus quoted speech — a narrator's "I" is outside quotes.
    Both dialogue styles are stripped: double and single quotation marks."""
    text = re.sub(r'"[^"]*"', " ", _normalize_quotes(paragraph))
    return _SINGLE_QUOTED.sub(" ", text)


def _is_narrator_heading(para: str) -> bool:
    return _heading(para)[0] is not None


# A first-person narrator naming themselves in narration (outside quotes):
# "My name is Rowan Merrow", "I, Rowan Merrow, …", "Call me Rowan". Up to an
# honorific plus three capitalised words; a possessive ends the name.
_NAME = r"((?:[A-Z][a-z]*\.?\s+)?[A-Z][\w-]*(?:\s+[A-Z][\w-]*){0,2})"
_SELF_NAMING = [
    re.compile(r"\b[Mm]y name (?:is|was) " + _NAME),
    re.compile(r"(?:^|[.!?]\s+)I,\s+" + _NAME + r","),
    re.compile(
        r"\b(?:[Cc]all me|I am called|I was called|I am named|I was named|"
        r"I was christened|they call me|[Pp]eople call me) " + _NAME
    ),
]


def self_named_narrators(text: str) -> list[str]:
    """Names a first-person narrator gives themselves in NARRATION, in order of
    first appearance. Quoted speech is excluded: a character saying "My name is
    X" to someone is a speaker, not the narrator."""
    out: list[str] = []
    for para in text.split("\n\n"):
        narration = _narration_only(para)
        for rx in _SELF_NAMING:
            for m in rx.finditer(narration):
                name = re.sub(r"'s$", "", m.group(1).strip()).strip()
                if name and name not in out:
                    out.append(name)
    return out


def _heading(para: str):
    """(narrator heading match | None, is a chapter heading) for a short,
    heading-like paragraph. A paragraph that keeps its line breaks (a PDF's
    heading block: "CHAPTER VIII\nNORA VANCE'S JOURNAL") is also read line by
    line; the last narrator line wins."""
    flat = " ".join(_normalize_quotes(para).split())
    if len(flat) > _HEADING_MAX_CHARS:
        return None, False
    head = _NARRATOR_HEAD.match(flat)
    chapter = bool(_CHAPTER_HEAD.match(flat))
    if head is None and "\n" in para:
        for line in para.split("\n"):
            line = " ".join(_normalize_quotes(line).split())
            head = _NARRATOR_HEAD.match(line) or head
            chapter = chapter or bool(_CHAPTER_HEAD.match(line))
    return head, chapter


def narrator_sections(text: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """[(narrator as headed, [(paragraph index, paragraph)])] for every section
    whose heading names a first-person narrator. A section runs until the next
    narrator or chapter heading (SPEC-09, eval-audit backlog item 1)."""
    sections: list[tuple[str, list[tuple[int, str]]]] = []
    current: Optional[tuple[str, list[tuple[int, str]]]] = None
    for i, para in enumerate(text.split("\n\n")):
        para = para.strip()
        if not para:
            continue
        head, chapter = _heading(para)
        if head:
            who = (head.group("owner") or head.group("writer") or "").strip(" _*.,")
            current = (who, [])
            sections.append(current)
            continue
        if chapter:
            current = None
            continue
        if current is not None:
            current[1].append((i, para))
    return [s for s in sections if s[0]]


def stratified_sample(text: str, budget: int = 14000, windows: int = 4) -> str:
    """Evenly-spaced windows across the WHOLE manuscript, not just the head.

    Character identification off `text[:10000]` misses anyone introduced later
    (a POV/narrator who first appears in a late chapter is invisible). Sampling
    a few windows spread across the text surfaces the full cast while keeping
    the prompt within a fixed character budget.
    """
    if len(text) <= budget:
        return text
    win = max(1, budget // windows)
    # Anchor the first window at the start; distribute the rest across the span
    # so the final window ends at the text's tail.
    span = len(text) - win
    starts = [round(i * span / (windows - 1)) for i in range(windows)]
    parts, seen_end = [], 0
    for s in starts:
        s = max(s, seen_end)  # never overlap the previous window
        if s >= len(text):
            break
        parts.append(text[s : s + win])
        seen_end = s + win
    return "\n\n[...]\n\n".join(parts)


# ---------------------------------------------------------------------------
# Speech attribution (B2, SPEC-17).
#
# The old extractor matched `Name <anything> "quote"` over the whole text, so
# `[^"]*` ran across sentences and paragraphs to the next quotation and handed
# one character's voice samples to whoever was named nearby. The attributor
# below works on quote SPANS inside one paragraph and credits a span only from
# a tag adjacent to it in that paragraph. When it cannot tell, it credits
# nobody: fewer samples beat wrong samples.
# ---------------------------------------------------------------------------

_TAG_VERBS = (
    _SPEECH_VERBS + "|snapped|growled|sighed|laughed|called|told|insisted|admitted|"
    "agreed|warned|explained|offered|pleaded|hissed|breathed|protested|"
    "conceded|interrupted|corrected|snarled|yelled|screamed|barked|grumbled|"
    "mumbled|stammered|stuttered|sobbed|teased|joked|promised|pressed|"
    "prompted|countered|retorted|objected|suggested|noted|announced|scoffed"
)
_VERB = rf"(?:{_TAG_VERBS})\b"
_HONOR = r"(?:(?:Mr|Mrs|Ms|Dr|Prof|St)\.[ \t]+)"
_CAP = r"[A-Z][\w'-]*"
_PROPER = rf"{_HONOR}?{_CAP}(?:[ \t]+{_CAP}){{0,3}}"
_DETERMINERS = {"the", "a", "an", "his", "her", "their", "our", "my", "its"}
# A preposition ends the speaker's noun phrase: in `said the auditor to Milo`
# the speaker is "the auditor"; Milo is the listener.
_PREP = r"(?:to|at|toward|towards|from|with|of|in|on|into|across|behind|over)"
_WORD = rf"(?!{_PREP}\b)[A-Za-z][\w'-]*"
_NOUN = (
    r"(?:[Tt]he|[Aa]n?|[Hh]is|[Hh]er|[Tt]heir|[Oo]ur|[Mm]y|[Ii]ts)"
    # "the caseworker", "her mother", "the Countess" (a title can be an alias)
    rf"\s+(?:{_WORD}[ \t]+){{0,2}}{_WORD}"
)
# A pronoun tag says someone spoke, never who. It is kept as written, so it
# credits no cast member and still blocks an action beat: in a mixed-gender
# exchange the beat's subject is as often the listener (`Milo looked at her.
# "Sit," she said.` is not Milo's line).
_PRONOUNS = {"he", "she", "they", "it", "i", "we", "you"}
_PRON = r"(?:[Hh]e|[Ss]he|[Tt]hey|I|[Ww]e|[Yy]ou|[Ii]t)"
_WHO = rf"(?P<who>{_NOUN}|{_PRON}|{_PROPER})"
_ADV = r"(?:[a-z]+ly\s+)?"
# After a span: `"…," said X [to Y]` / `"…," X said` — the tag opens the narration.
_AFTER_INVERTED = re.compile(
    rf"^\s*[,—-]?\s*{_VERB}\s+{_WHO}(?:\s+[a-z]+ly)?(?:\s+{_PREP}\s+[^,.;:!?—-]+?)?"
    r"(?=\s*[,.;:!?—-]|\s*$)"
)
_AFTER_PLAIN = re.compile(
    rf"^\s*[,—-]?\s*{_WHO}\s+{_ADV}{_VERB}(?!\s+(?:nothing|no|not)\b)"
)
# Before a span: `X said, "…"` / `said X, "…"` / `X: "…"` — the tag closes it.
_OBJ = r"(?:\s+(?:to\s+)?(?:him|her|them|me|us|it|[a-z]+ly))?"
_BEFORE_PLAIN = re.compile(rf"(?<![\w'-]){_WHO}\s+{_ADV}{_VERB}{_OBJ}\s*[,:]\s*$")
_BEFORE_INVERTED = re.compile(rf"{_VERB}\s+{_WHO}\s*[,:]\s*$")
_BEFORE_SCRIPT = re.compile(rf"(?:^|[.!?]\s+|\n)\s*(?P<who>{_PROPER})\s*:\s*$")
# Capitalised sentence-openers that are not part of a speaker's name.
_OPENERS = {
    "then",
    "but",
    "and",
    "so",
    "now",
    "finally",
    "still",
    "yet",
    "here",
    "there",
    "suddenly",
    "again",
    "instead",
    "later",
    "when",
    "while",
    "for",
    "as",
    "if",
    "with",
    "in",
    "on",
    "at",
    "after",
    "before",
    "by",
    "from",
    "once",
    "though",
    "although",
    "because",
    "since",
    "until",
    "yes",
    "no",
    "oh",
    "well",
}
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_SUBJECT = re.compile(rf"^\s*(?P<who>{_PROPER})(?=,|\s+[a-z])")
# Quoted words this long are someone's speech, not a scare quote or a bare
# vocative: an unattributed span this long keeps a paragraph out of a
# character's chunks, and a voice sample must be at least this long ("So!",
# "Zara," say nothing about a voice).
SPEECH_MIN_WORDS = 3


def _canon_speaker(who: str) -> Optional[str]:
    """A tag's subject as a speaker key: leading openers dropped ("Then Zara" →
    "Zara"); a pronoun lowercased (it never names a cast member); a bare
    determiner or opener is nobody."""
    words = who.split()
    while len(words) > 1 and words[0].lower() in _OPENERS:
        words = words[1:]
    if len(words) == 1:
        low = words[0].lower()
        if low in _PRONOUNS:
            return low
        if low in _OPENERS or low in _DETERMINERS:
            return None
    return " ".join(words)


def _quote_spans(para: str) -> list[tuple[int, int, bool]]:
    """(start, end, closed) of each quoted span of a normalized paragraph — the
    text INSIDE the marks. Double quotes win; a paragraph with none falls back
    to single-quoted speech. An odd final mark opens a span that runs to the
    paragraph's end: a speech continued in the next paragraph."""
    marks = [m.start() for m in re.finditer('"', para)]
    if marks:
        spans = [(a + 1, b, True) for a, b in zip(marks[0::2], marks[1::2])]
        if len(marks) % 2:
            spans.append((marks[-1] + 1, len(para), False))
        return spans
    return [(m.start() + 1, m.end() - 1, True) for m in _SINGLE_QUOTED.finditer(para)]


def _beat_subject(narration: str) -> Optional[str]:
    """The proper-name subject of the sentence immediately before a quote, when
    that sentence names nobody else (`Zara shrugged. "…"`)."""
    stripped = narration.strip()
    if not stripped or stripped[-1] not in ".!?":
        return None
    sentence = _SENTENCE_END.split(stripped)[-1]
    m = _SUBJECT.match(sentence)
    if not m:
        return None
    who = _canon_speaker(m.group("who"))
    # Any other capitalised word is another name (or a place): ambiguous.
    if any(w != "I" for w in re.findall(r"\b[A-Z][\w'-]*", sentence[m.end() :])):
        return None
    return who


def _tag(narration: str, rules, find) -> Optional[str]:
    """The speaker of the first rule that `find`s a tag in the narration."""
    for rx in rules:
        m = find(rx, narration)
        if m:
            return _canon_speaker(m.group("who"))
    return None


_NEW_SENTENCE = re.compile(r"[.!?]\s+\S")


def _attribute_paragraph(para: str) -> tuple[list[dict], str]:
    """The quoted spans of one normalized paragraph with their speakers (a
    speaker key or None), and the narration left once the spans are removed.

    Each span takes the tag after it, else the tag before it, else the action
    beat before it. A span with none of these is the paragraph's one named
    speaker going on (`"Hi," said Zara. "Come in."`, a split quote) when no
    new sentence starts between it and a neighbouring quote — and nobody's
    when the paragraph names two speakers or none, or when narration comes
    between (`"Go," Zara told the guard. The guard laughed. "Make me."`)."""
    spans = _quote_spans(para)
    out: list[dict] = []
    outside: list[str] = []
    prev_end = 0
    for k, (start, end, closed) in enumerate(spans):
        before = para[prev_end : max(prev_end, start - 1)]
        outside.append(before)
        next_open = spans[k + 1][0] - 1 if k + 1 < len(spans) else len(para)
        after = para[end + 1 : next_open] if closed else ""
        speaker = (
            _tag(after, (_AFTER_INVERTED, _AFTER_PLAIN), re.match)
            or _tag(
                before, (_BEFORE_PLAIN, _BEFORE_INVERTED, _BEFORE_SCRIPT), re.search
            )
            or _beat_subject(before)
        )
        after_gap = after if k + 1 < len(spans) else None
        out.append(
            {
                "text": para[start:end].strip(),
                "speaker": speaker,
                "closed": closed,
                # Joined to a neighbouring quote by no more than a tag.
                "joined": (k > 0 and not _NEW_SENTENCE.search(before))
                or (after_gap is not None and not _NEW_SENTENCE.search(after_gap)),
            }
        )
        prev_end = end + 1 if closed else len(para)
    outside.append(para[prev_end:])
    named = {s["speaker"] for s in out} - {None}
    if len(named) == 1:
        for s in out:
            if s["speaker"] is None and s["joined"]:
                s["speaker"] = next(iter(named))
    for s in out:
        del s["joined"]
    return out, " ".join(outside)


def attribute_speech(text: str) -> list[list[dict]]:
    """Per paragraph (``text.split("\\n\\n")`` indices), each quoted span and the
    speaker it is credited to: ``{"text", "speaker"}``. ``speaker`` is the tag's
    subject as written ("Zara Okafor", "the caseworker") or None when nothing
    adjacent says who spoke. Resolve it to a cast member with
    :func:`speaker_matches`.

    Rules (docs/specs/SPEC-17-voice-attribution.md): a tag counts only next to
    its span in the same paragraph, and a pronoun tag credits no one; an action
    beat counts only when its name is the subject of the sentence right before
    the quote and that sentence names nobody else; an untagged span inherits
    the paragraph's one named speaker; an untagged paragraph of pure speech
    inherits a speaker only when it continues an open quote, or when the
    exchange so far strictly alternates between two named speakers. Nothing
    else crosses a blank line or another quotation.
    """
    result: list[list[dict]] = []
    exchange: list[str] = []  # speakers of the current back-and-forth
    carry: Optional[str] = None  # speaker of the last paragraph's open quote
    for raw in text.split("\n\n"):
        para = _normalize_quotes(raw.strip())
        if not para:
            result.append([])
            continue
        spans, narration = _attribute_paragraph(para)
        if not spans:
            exchange, carry = [], None
            result.append([])
            continue
        continued = False
        if spans[0]["speaker"] is None and para[0] in "\"'":
            if carry:
                spans[0]["speaker"] = carry
                continued = True
            elif not re.search(r"\w", narration) and len(exchange) >= 2:
                alternates = all(a != b for a, b in zip(exchange, exchange[1:]))
                if alternates and len(set(exchange)) == 2:
                    for s in spans:
                        s["speaker"] = exchange[-2]
        speakers = {s["speaker"] for s in spans}
        who = speakers.pop() if len(speakers) == 1 else None
        if who and who not in _PRONOUNS:
            if not (continued and exchange and exchange[-1] == who):
                exchange.append(who)
        else:
            exchange = []  # unknown, mixed or pronoun-only: not a named exchange
        carry = None if spans[-1]["closed"] else spans[-1]["speaker"]
        result.append([{"text": s["text"], "speaker": s["speaker"]} for s in spans])
    return result


def speaker_matches(
    speaker: Optional[str],
    character_name: str,
    aliases: Optional[List[str]] = None,
    cast: Optional[List[Dict]] = None,
) -> bool:
    """Is this tag subject the character? ONE direction only: the speaker's
    words (minus honorifics and determiners) all appear in one of the
    character's name forms, so "Zara" and "Ms. Okafor" are Zara Okafor, but
    "the auditor to Milo" is not Milo. With a cast, a speaker within more than
    one member's names ("Ward" when there are two Wards) is credited to nobody."""
    from app.parsing.names import HONORIFICS, tokens

    def within(form: str) -> bool:
        f = tokens(form) - _DETERMINERS
        return sig <= f - HONORIFICS if sig else bool(said) and said <= f

    said = tokens(speaker or "") - _DETERMINERS
    sig = said - HONORIFICS  # "Count" alone is matched on its raw word
    members = cast or [{"name": character_name, "aliases": aliases}]
    owners = {
        c["name"]
        for c in members
        if any(within(f) for f in [c["name"], *(c.get("aliases") or [])] if f)
    }
    mine = any(within(f) for f in [character_name, *(aliases or [])] if f)
    return mine and len(owners - {character_name}) == 0


def spoken_lines(
    text: str,
    character_name: str,
    *,
    aliases: Optional[List[str]] = None,
    cast: Optional[List[Dict]] = None,
    attributed: Optional[list[list[dict]]] = None,
) -> list[tuple[int, str]]:
    """(paragraph index, line) for everything the character says. Consecutive
    spans of theirs in one paragraph join into one line (`"You must," Nora
    replied, "there is no other way."` → one utterance); a span by someone else
    in between splits them."""
    attributed = attribute_speech(text) if attributed is None else attributed
    out: list[tuple[int, str]] = []
    for i, spans in enumerate(attributed):
        run: list[str] = []
        for s in [*spans, None]:
            if s is not None and speaker_matches(
                s["speaker"], character_name, aliases, cast
            ):
                run.append(s["text"])
                continue
            if run:
                out.append((i, _join_utterance(run)))
                run = []
    return out


def _join_utterance(spans: list[str]) -> str:
    """Join one speaker's quote halves into a line that reads as speech.

    A quote broken by its tag keeps the comma the tag needed ("Don't thank
    her," she said. "Read the appendices first."). Joined as-is that reads
    "Don't thank her, Read the appendices first." so the comma closes to a
    full stop when the next half starts a sentence, and a line that ends on
    the tag's comma ends on a full stop instead."""
    line = spans[0].strip()
    for nxt in spans[1:]:
        nxt = nxt.strip()
        if line.endswith(",") and nxt[:1].isupper():
            line = line[:-1] + "."
        line = f"{line} {nxt}"
    return line[:-1] + "." if line.endswith(",") else line


class CharacterExtractor:
    """Extract character-specific content from manuscript"""

    async def extract_characters(
        self,
        text: str,
        max_characters: int = 10,
        user_id: Optional[UUID] = None,
    ) -> List[str]:
        """
        Use LLM to identify main characters in text

        Args:
            text: Full manuscript text
            max_characters: Maximum number of characters to extract

        Returns:
            List of character names
        """
        # Sample across the whole manuscript so late-introduced characters
        # (and epistolary narrators) are seen, not just those in the opening.
        sample_text = stratified_sample(text)

        prompt = f"""Analyze this manuscript and identify the main characters.

Return ONLY a JSON array of character names (limit to top {max_characters} by importance).

Text (sampled across the whole manuscript, windows separated by [...]):
{sample_text}

Important:
- Include first-person narrators, diarists, and letter-writers — in epistolary
  or multi-POV works THEY are the main characters, even if rarely named in
  third person.
- Include anyone who speaks or drives action; exclude groups and figures
  mentioned only once in passing.
- Use each character's most common name (e.g., "Harry" not "Harry Potter").
- Return valid JSON only — no prose, no explanation.

JSON array:"""

        content = ""
        try:
            result = await get_llm_client().generate(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1000,
                user_id=user_id,
                purpose="extract_characters",
            )
            content = result.text
            characters = extract_json_array(content)

            return [str(name) for name in characters[:max_characters]]

        except json.JSONDecodeError as e:
            logger.warning(
                f"Error parsing LLM response as JSON: {e}",
                extra_fields={"event": "character_extraction_parse_failed"},
            )
            return []
        except Exception as e:
            logger.warning(
                f"Error extracting characters: {e}",
                extra_fields={"event": "character_extraction_failed"},
            )
            return []

    def extract_character_content(
        self,
        text: str,
        character_name: str,
        *,
        aliases: Optional[List[str]] = None,
        cast: Optional[List[Dict]] = None,
    ) -> List[Dict[str, str]]:
        """
        Extract all content related to specific character

        Args:
            text: Full manuscript text
            character_name: Name of character to extract
            aliases: Other names the speech tags may use for them
            cast: The book's cast ({name, aliases}); a speaker tag that could be
                two members is then credited to nobody

        Returns:
            List of chunks with metadata:
            [{
                'text': str,
                'chunk_type': str,
                'source_location': str,
                'character_name': str
            }]

        A paragraph where the character speaks yields a `dialogue` chunk of
        THEIR words only (SPEC-17): the narration around a line is not their
        voice. A paragraph that carries anyone else's speech is not indexed for
        them at all, so another speaker's words can never become their sample.
        Other paragraphs that name them are indexed as action / thought /
        description ("how they move").
        """
        index, speaks = self._paragraph_indexer(text, character_name, aliases, cast)
        chunks: List[Dict[str, str]] = []
        for i, para in enumerate(text.split("\n\n")):
            para = para.strip()
            # Paragraphs where they speak, or that name them ("X's Journal" is
            # a heading, not voice — SPEC-09 attributes the section beneath it).
            if para and (
                i in speaks
                or (
                    self._character_in_paragraph(character_name, para)
                    and not _is_narrator_heading(para)
                )
            ):
                chunks += index(i, para, "paragraph")
        return chunks

    def _paragraph_indexer(self, text, character_name, aliases, cast):
        """(`index`, the paragraphs where they speak). `index(i, para, where)`
        → the chunks one paragraph gives the character, deduplicated
        case-insensitively across calls (SPEC-17):
        - where they speak: their own lines, as `dialogue` — each at least
          `SPEECH_MIN_WORDS` words, so a lone vocative is no voice sample;
        - where anyone else speaks (a credited span, or an uncredited one of
          `SPEECH_MIN_WORDS`+ words): nothing — in their own narration
          (`where == "narration"`) "said I" is them, not someone else;
        - otherwise the paragraph, typed as action / thought / description."""
        attributed = attribute_speech(text)
        own: Dict[int, List[str]] = {}
        for i, line in spoken_lines(
            text, character_name, aliases=aliases, cast=cast, attributed=attributed
        ):
            own.setdefault(i, []).append(line)
        seen: set = set()

        def index(i: int, para: str, where: str) -> List[Dict[str, str]]:
            themselves = ("i", "we") if where == "narration" else ()
            if i in own:
                found = [
                    (line, ChunkType.DIALOGUE.value, f"dialogue_{i}")
                    for line in own[i]
                    if len(line.split()) >= SPEECH_MIN_WORDS
                ]
            elif any(
                s["speaker"] not in themselves
                and (s["speaker"] or len(s["text"].split()) >= SPEECH_MIN_WORDS)
                for s in attributed[i]
            ):
                return []
            else:
                found = [(para, self._classify_chunk(para), f"{where}_{i}")]
            out = []
            for chunk_text, kind, location in found:
                if chunk_text.lower() not in seen:
                    seen.add(chunk_text.lower())
                    out.append(
                        {
                            "text": chunk_text,
                            "chunk_type": kind,
                            "source_location": location,
                            "character_name": character_name,
                        }
                    )
            return out

        return index, own

    def extract_narration(
        self,
        text: str,
        character_name: str,
        *,
        aliases: Optional[List[str]] = None,
        cast: Optional[List[Dict]] = None,
        is_narrator: bool = False,
    ) -> List[Dict[str, str]]:
        """Voice the name-gate cannot see (SPEC-09, eval-audit backlog item 1).

        A diarist rarely names themselves, so their narration — their actual
        voice — was never indexed. This returns:
        - paragraphs naming the character by an ALIAS ("Mrs. Harker");
        - first-person paragraphs in sections whose heading names them as the
          narrator ("Jonathan Harker's Journal", "Letter, Mina Murray to …"),
          resolved honorific-aware against the whole cast and skipped when the
          heading could be two characters;
        - when the source has no such headings and this character's role is
          narrator — or the narration names its "I" as this character ("my
          name is X", "I, X,", "call me X") and names no one else that way —
          every first-person narration paragraph.
        """
        from app.parsing.names import match

        forms = [character_name, *(aliases or [])]
        cast = cast or [{"name": character_name, "aliases": aliases or []}]
        # The same rule as extract_character_content (SPEC-17): a paragraph
        # where they speak gives their words only, as dialogue; a paragraph
        # carrying anyone else's speech is not theirs to index — not even a
        # diarist's, or the other speaker's lines would reach "Voice samples".
        index, _ = self._paragraph_indexer(text, character_name, aliases, cast)
        chunks: List[Dict[str, str]] = []

        # Aliases match CASE-SENSITIVELY: an alias is a proper noun, and "Will"
        # or "Hope" must not claim every "will" and "hope" in the book.
        alias_res = [
            re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, a.split())) + r"(?!\w)")
            for a in aliases or []
            if a.strip()
        ]
        for i, para in enumerate(text.split("\n\n")):
            para = para.strip()
            if (
                para
                and not _is_narrator_heading(para)
                and any(r.search(para) for r in alias_res)
            ):
                chunks += index(i, para, "paragraph")

        def owner_of(headed: str) -> Optional[str]:
            hits = [
                c["name"]
                for c in cast
                if any(match(headed, f) for f in [c["name"], *(c.get("aliases") or [])])
            ]
            return hits[0] if len(set(hits)) == 1 else None

        sections = narrator_sections(text)
        for headed, paras in sections:
            if owner_of(headed) != character_name and not (
                len(cast) == 1 and any(match(headed, f) for f in forms)
            ):
                continue
            for i, para in paras:
                if _FIRST_PERSON.search(_narration_only(para)):
                    chunks += index(i, para, "narration")

        # No headings: the narrator may name themselves once ("my name is X",
        # "I, X,"). When every self-naming in the narration resolves to ONE
        # cast member, that member is the lone first-person narrator — a
        # frame narrative with two self-named narrators is ambiguous and left
        # to the headings / the narrator role.
        self_named = None
        if not sections:
            owners = {owner_of(n) for n in self_named_narrators(text)}
            if len(owners) == 1:
                self_named = owners.pop()

        if not sections and (is_narrator or self_named == character_name):
            for i, para in enumerate(text.split("\n\n")):
                para = para.strip()
                if para and _FIRST_PERSON.search(_narration_only(para)):
                    chunks += index(i, para, "narration")
        return chunks

    def extract_dialogue_only(
        self,
        text: str,
        character_name: str,
        *,
        aliases: Optional[List[str]] = None,
        cast: Optional[List[Dict]] = None,
    ) -> List[str]:
        """
        Extract only dialogue lines for this character

        Args:
            text: Full manuscript text
            character_name: Name of character

        Returns:
            List of dialogue strings, in order, deduped case-insensitively.
            Only spans the attributor credits to them (see attribute_speech).
        """
        out: List[str] = []
        seen: set = set()
        for _, line in spoken_lines(text, character_name, aliases=aliases, cast=cast):
            if len(line) > 3 and line.lower() not in seen:
                seen.add(line.lower())
                out.append(line)
        return out

    def _character_in_paragraph(self, character_name: str, paragraph: str) -> bool:
        """
        Check if character name appears in paragraph

        Uses word boundary matching to avoid false positives
        """
        # Create pattern with word boundaries
        pattern = rf"\b{re.escape(character_name)}\b"
        return bool(re.search(pattern, paragraph, re.IGNORECASE))

    def _classify_chunk(self, text: str) -> str:
        """Classify a narration paragraph as thought, action or description.
        (Dialogue chunks are only ever the character's own lines — see
        `_paragraph_indexer`.)"""
        # Check for thought indicators
        thought_keywords = [
            "thought",
            "wondered",
            "pondered",
            "realized",
            "remembered",
            "considered",
            "imagined",
            "knew",
            "believed",
            "felt that",
        ]
        if any(keyword in text.lower() for keyword in thought_keywords):
            return ChunkType.THOUGHT.value

        # Check for action verbs
        action_verbs = [
            "walked",
            "ran",
            "jumped",
            "grabbed",
            "took",
            "held",
            "moved",
            "turned",
            "looked",
            "went",
            "came",
            "stood",
            "sat",
            "opened",
            "closed",
            "pulled",
            "pushed",
        ]
        if any(verb in text.lower() for verb in action_verbs):
            return ChunkType.ACTION.value

        # Default to description
        return ChunkType.DESCRIPTION.value

    def get_character_statistics(self, chunks: List[Dict[str, str]]) -> Dict:
        """
        Get statistics about extracted character content

        Args:
            chunks: List of character chunks

        Returns:
            Dictionary with statistics
        """
        if not chunks:
            return {
                "total_chunks": 0,
                "dialogue_count": 0,
                "action_count": 0,
                "thought_count": 0,
                "description_count": 0,
                "total_words": 0,
            }

        stats = {
            "total_chunks": len(chunks),
            "dialogue_count": sum(
                1 for c in chunks if c["chunk_type"] == ChunkType.DIALOGUE.value
            ),
            "action_count": sum(
                1 for c in chunks if c["chunk_type"] == ChunkType.ACTION.value
            ),
            "thought_count": sum(
                1 for c in chunks if c["chunk_type"] == ChunkType.THOUGHT.value
            ),
            "description_count": sum(
                1 for c in chunks if c["chunk_type"] == ChunkType.DESCRIPTION.value
            ),
            "total_words": sum(len(c["text"].split()) for c in chunks),
        }

        return stats
