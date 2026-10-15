"""SPEC-17 (B2): voice samples are the character's own words.

The old extractor matched `Name <anything> "quote"`, so `[^"]*` ran across
sentences and paragraphs to the next quotation: Milo Voss was credited with
Zara's line and the caseworker's. These tests pin the quote-span attributor,
the hand-labelled precision gate, the prose prompt's split between "Voice
samples" and "How they move", and the `reindex-voice` command.
"""

import json
import types
import uuid
from pathlib import Path

import pytest

from app.core.models import ChunkType
from app.parsing.character_extractor import (
    CharacterExtractor,
    attribute_speech,
    speaker_matches,
)

pytestmark = pytest.mark.unit

# Copied from scripts/e2e_browser.py (SOURCE) — the journey that exposed B2.
SOURCE = """Milo Voss opened his eyes in the intake atrium.

"You've been retained," the caseworker said. Milo Voss nodded slowly.

Zara Okafor, at the next desk, did not look up. "Don't thank her," Zara Okafor said.

Milo Voss read the appendices. Zara Okafor watched him read them.

Edric Thane signed the memo. Edric Thane had signed ten thousand memos.
"""
E2E_CAST = [
    {"name": "Milo Voss", "aliases": []},
    {"name": "Zara Okafor", "aliases": []},
    {"name": "Edric Thane", "aliases": []},
]

FIXTURE = json.loads(
    (
        Path(__file__).parent.parent / "fixtures" / "dialogue_attribution.json"
    ).read_text()
)
PRECISION_FLOOR = 0.95


def _dialogue(chunks):
    return [c for c in chunks if c["chunk_type"] == ChunkType.DIALOGUE.value]


# --- the e2e SOURCE ----------------------------------------------------------


class TestE2ESource:
    def test_milo_says_nothing(self):
        cx = CharacterExtractor()
        assert cx.extract_dialogue_only(SOURCE, "Milo Voss") == []
        chunks = cx.extract_character_content(SOURCE, "Milo Voss")
        assert _dialogue(chunks) == []
        for c in chunks:  # no one else's words in ANY of Milo's chunks
            assert "Don't thank her" not in c["text"]
            assert "You've been retained" not in c["text"]

    def test_zara_says_exactly_her_line(self):
        cx = CharacterExtractor()
        assert cx.extract_dialogue_only(SOURCE, "Zara Okafor") == ["Don't thank her,"]
        lines = [
            c["text"]
            for c in _dialogue(cx.extract_character_content(SOURCE, "Zara Okafor"))
        ]
        assert lines == ["Don't thank her,"]

    def test_alias_paragraphs_follow_the_same_rule(self):
        # The real journey's cast carries aliases ("Milo", "Zara"), and the
        # alias pass (SPEC-09) indexed whole paragraphs: the caseworker's line
        # reached Milo, and Zara's paragraph went in as "dialogue" narration.
        cx = CharacterExtractor()
        cast = [{**c, "aliases": [c["name"].split()[0]]} for c in E2E_CAST]
        milo = cx.extract_narration(SOURCE, "Milo Voss", aliases=["Milo"], cast=cast)
        for c in milo:
            assert "You've been retained" not in c["text"]
            assert "Don't thank her" not in c["text"]
            assert c["chunk_type"] != "dialogue"
        zara = cx.extract_narration(SOURCE, "Zara Okafor", aliases=["Zara"], cast=cast)
        assert [c["text"] for c in _dialogue(zara)] == ["Don't thank her,"]

    def test_caseworker_line_belongs_to_no_canon_character(self):
        spans = [s for para in attribute_speech(SOURCE) for s in para]
        line = next(s for s in spans if s["text"].startswith("You've been retained"))
        assert line["speaker"] == "the caseworker"
        for member in E2E_CAST:
            assert not speaker_matches(line["speaker"], member["name"], cast=E2E_CAST)
            assert (
                "You've been retained,"
                not in CharacterExtractor().extract_dialogue_only(
                    SOURCE, member["name"]
                )
            )


# --- the attributor's rules --------------------------------------------------


def _speakers(text):
    return [(s["text"], s["speaker"]) for para in attribute_speech(text) for s in para]


class TestAttributionRules:
    def test_never_crosses_a_paragraph(self):
        text = 'Milo stood by the window.\n\n"Is anyone there?"'
        assert _speakers(text) == [("Is anyone there?", None)]
        assert CharacterExtractor().extract_dialogue_only(text, "Milo") == []

    def test_never_crosses_another_quote(self):
        # The old `Name [^"]*"…"` pattern gave Milo the second line.
        text = 'Milo looked up. "Hello," said Zara. "Who is asking?"'
        got = dict(_speakers(text))
        assert got["Hello,"] == "Zara"
        assert got["Who is asking?"] == "Zara"  # the tag sentence alone sits between
        assert CharacterExtractor().extract_dialogue_only(text, "Milo") == []

    def test_tag_forms(self):
        assert _speakers('"Go," said Zara.') == [("Go,", "Zara")]
        assert _speakers('"Go," Zara said.') == [("Go,", "Zara")]
        assert _speakers('Zara said, "Go."') == [("Go.", "Zara")]
        assert _speakers('Zara: "Go."') == [("Go.", "Zara")]
        assert _speakers('"Go," Zara whispered quietly.') == [("Go,", "Zara")]

    def test_said_nothing_is_not_a_tag(self):
        assert _speakers('"Well?" Milo said nothing.') == [("Well?", None)]

    def test_action_beat_needs_a_lone_subject(self):
        assert _speakers('Zara shrugged. "Fine."') == [("Fine.", "Zara")]
        # Another name in the beat sentence: ambiguous, credited to nobody.
        assert _speakers('Zara looked at Milo. "Fine."') == [("Fine.", None)]
        # A beat AFTER the quote is not a tag.
        assert _speakers('"Fine." Zara shrugged.') == [("Fine.", None)]

    def test_explicit_tag_beats_an_action_beat(self):
        assert _speakers('Zara shrugged. "Fine," said Milo.') == [("Fine,", "Milo")]
        # A non-cast tag blocks the beat too.
        assert _speakers('Zara shrugged. "Fine," the clerk said.') == [
            ("Fine,", "the clerk")
        ]

    def test_pronoun_tag_resolves_only_through_a_beat(self):
        assert _speakers('"Fine," she said.') == [("Fine,", None)]
        assert _speakers('Zara shrugged. "Fine," she said.') == [("Fine,", "Zara")]
        # "I said" is the narrator: it never resolves to the beat's subject
        # (Dracula: 'Dressing-gown and dress were both in their places.
        # "Thank God," I said').
        assert _speakers('Zara shrugged. "Fine," I said.') == [("Fine,", "i")]
        # A capitalised opener is not a subject ("For he grew pale. '…'").
        assert _speakers('For he grew pale. "You are to help her."') == [
            ("You are to help her.", None)
        ]

    def test_split_quote_is_one_utterance(self):
        text = '"You must," Nora replied, "there is no other way."'
        assert CharacterExtractor().extract_dialogue_only(text, "Nora") == [
            "You must, there is no other way."
        ]

    def test_alternating_exchange_is_followed_only_while_unambiguous(self):
        text = "\n\n".join(
            [
                '"Ready?" asked Milo.',
                '"Always," said Zara.',
                '"Then go."',
                '"Going."',
            ]
        )
        got = _speakers(text)
        assert got[2] == ("Then go.", "Milo") and got[3] == ("Going.", "Zara")
        # A narration paragraph ends the exchange: the next bare line is unknown.
        broken = text + '\n\nThe lift chimed.\n\n"Now?"'
        assert _speakers(broken)[-1] == ("Now?", None)
        # One known speaker is not an exchange.
        assert _speakers('"Ready?" asked Milo.\n\n"Yes."')[-1] == ("Yes.", None)

    def test_open_quote_continues_into_the_next_paragraph(self):
        text = 'Bram began. "It was long ago.\n\n"And nobody remembers."'
        assert [s for _, s in _speakers(text)] == ["Bram", "Bram"]

    def test_single_quoted_speech(self):
        text = "'I will not,' said Hollis. The dogs' bones lay about."
        assert _speakers(text) == [("I will not,", "Hollis")]

    def test_smart_quotes(self):
        assert _speakers("“Go,” said Zara.") == [("Go,", "Zara")]

    def test_ambiguous_speaker_is_nobody_with_a_cast(self):
        cast = [{"name": "John Ward"}, {"name": "Elias Ward"}]
        assert speaker_matches("Ward", "John Ward") is True  # no cast: can't tell
        assert speaker_matches("Ward", "John Ward", cast=cast) is False
        assert speaker_matches("Mr. Ward", "Elias Ward", cast=cast) is False
        assert speaker_matches("Elias", "Elias Ward", cast=cast) is True

    def test_aliases_and_honorifics(self):
        text = '"Enough," said Mrs. Harker.'
        cx = CharacterExtractor()
        assert cx.extract_dialogue_only(text, "Mina Harker") == ["Enough,"]
        assert cx.extract_dialogue_only('"Enough," said Mina.', "Mina Harker") == [
            "Enough,"
        ]
        assert cx.extract_dialogue_only(
            '"Enough," said the Countess.', "Vasska", aliases=["the Countess"]
        ) == ["Enough,"]


# --- the hand-labelled precision gate (gates CI) --------------------------------


def _fixture_predictions():
    cast = FIXTURE["cast"]
    spans = [s for para in attribute_speech(FIXTURE["text"]) for s in para]
    assert [s["text"] for s in spans] == [ln["quote"] for ln in FIXTURE["lines"]]
    predicted = []
    for s in spans:
        owners = [
            c["name"]
            for c in cast
            if speaker_matches(s["speaker"], c["name"], c.get("aliases"), cast)
        ]
        predicted.append(owners[0] if len(owners) == 1 else None)
    return predicted


def test_fixture_is_twenty_lines_three_speakers():
    lines = FIXTURE["lines"]
    assert len(lines) == 20
    assert {ln["speaker"] for ln in lines} - {None} == {
        c["name"] for c in FIXTURE["cast"]
    }


def test_attribution_precision_gate():
    predicted = _fixture_predictions()
    truth = [ln["speaker"] for ln in FIXTURE["lines"]]
    attributed = [(p, t) for p, t in zip(predicted, truth) if p is not None]
    correct = sum(1 for p, t in attributed if p == t)
    precision = correct / len(attributed)
    labelled = [t for t in truth if t is not None]
    recall = correct / len(labelled)
    print(f"dialogue attribution: precision={precision:.3f} recall={recall:.3f}")
    assert precision >= PRECISION_FLOOR, list(zip(predicted, truth))
    # Recall is reported, not gated (precision over recall) — but a regression
    # that attributes almost nothing would pass the precision gate vacuously.
    assert recall >= 0.5


# --- chunk classification -------------------------------------------------------


class TestChunking:
    def test_paragraph_is_dialogue_only_when_they_speak_in_it(self):
        cx = CharacterExtractor()
        assert cx._classify_chunk('"Go," said Zara. Milo walked.', "Zara") == "dialogue"
        assert cx._classify_chunk('"Go," said Zara. Milo walked.', "Milo") != "dialogue"

    def test_paragraph_with_someone_elses_speech_is_not_indexed_for_them(self):
        text = 'The clerk stood. "Sign here, please," she said. Milo took the pen.'
        assert CharacterExtractor().extract_character_content(text, "Milo") == []

    def test_scare_quotes_do_not_drop_a_paragraph(self):
        text = "Milo hated the word 'synergy' and walked out."
        chunks = CharacterExtractor().extract_character_content(text, "Milo")
        assert [c["chunk_type"] for c in chunks] == ["action"]

    def test_their_line_and_movement_are_separate_chunks(self):
        text = 'Zara stood. "Enough," said Zara.\n\nZara walked to the door.'
        chunks = CharacterExtractor().extract_character_content(text, "Zara")
        assert [(c["chunk_type"], c["text"]) for c in chunks] == [
            ("dialogue", "Enough,"),
            ("action", "Zara walked to the door."),
        ]


# --- the prose prompt: voice vs movement -----------------------------------------


class TypedStore:
    """Returns samples by chunk_type, like the real store's filter."""

    def __init__(self, samples):
        self.samples = samples
        self.calls = []

    async def retrieve_similar(self, *, chunk_type=None, k=5, **kw):
        self.calls.append(chunk_type)
        pool = [s for s in self.samples if chunk_type in (None, s["chunk_type"])]
        return pool[:k]


def _char(name="Zara Okafor", aliases=None):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        aliases=aliases or [],
        role="deuteragonist",
        description="",
        goals="",
        arc="",
        voice_characteristics=None,
        user_id=uuid.uuid4(),
        book_id=uuid.uuid4(),
    )


def _s(i, text, chunk_type, source="paragraph_1", score=0.5):
    return {
        "id": i,
        "text": text,
        "chunk_type": chunk_type,
        "source": source,
        "score": score,
    }


def _blocks(out):
    from app.characters.context import MOVEMENT_LABEL, VOICE_SAMPLES_LABEL

    voice, move = "", ""
    if VOICE_SAMPLES_LABEL in out:
        voice = out.split(VOICE_SAMPLES_LABEL)[1].split(MOVEMENT_LABEL)[0]
    if MOVEMENT_LABEL in out:
        move = out.split(MOVEMENT_LABEL)[1]
    return voice, move


class TestCharacterContext:
    async def test_only_dialogue_under_voice_samples(self, monkeypatch):
        import app.characters.context as ctx

        store = TypedStore(
            [
                _s("d1", "Don't thank her,", "dialogue", "dialogue_2"),
                _s("a1", "Zara Okafor stood and filed the memo.", "action"),
                _s("t1", "Zara Okafor wondered about the lich.", "thought"),
            ]
        )
        monkeypatch.setattr(ctx, "get_chunk_store", lambda: store)
        grounding: list = []
        out = await ctx.build_character_context(
            _char(), "Zara Okafor", "the desk", grounding=grounding
        )
        assert (
            "Voice samples (their own words — match cadence, diction, syntax):" in out
        )
        voice, move = _blocks(out)
        assert "Don't thank her" in voice
        assert "filed the memo" not in voice and "wondered" not in voice
        assert "filed the memo" in move and "wondered" in move
        assert "Don't thank her" not in move
        assert {g["chunk_id"] for g in grounding} == {"d1", "a1", "t1"}

    async def test_voice_never_falls_back_to_narration(self, monkeypatch):
        import app.characters.context as ctx

        store = TypedStore(
            [
                _s("u1", "Zara Okafor read the file.", "unknown"),
                _s("u2", "Zara Okafor closed the drawer.", "unknown"),
            ]
        )
        monkeypatch.setattr(ctx, "get_chunk_store", lambda: store)
        out = await ctx.build_character_context(_char(), "Zara Okafor", "the desk")
        voice, move = _blocks(out)
        assert ctx.VOICE_SAMPLES_LABEL not in out  # no words of hers: no block
        # The movement block may fall back to untyped hits — labelled as such.
        assert "read the file" in move and "closed the drawer" in move

    async def test_narration_centred_on_someone_else_is_left_out(self, monkeypatch):
        import app.characters.context as ctx

        store = TypedStore(
            [
                _s(
                    "a1",
                    "Milo Voss read the appendices. Zara Okafor watched him read them.",
                    "description",
                ),
                _s("a2", "Zara Okafor did not look up.", "action"),
            ]
        )
        monkeypatch.setattr(ctx, "get_chunk_store", lambda: store)
        cast = [_char("Milo Voss"), _char("Zara Okafor")]
        out = await ctx.build_character_context(
            cast[1], "Zara Okafor", "the desk", cast=cast
        )
        _, move = _blocks(out)
        assert "did not look up" in move
        assert "read the appendices" not in out
        # ...and the same passage DOES centre Milo.
        out = await ctx.build_character_context(
            cast[0], "Milo Voss", "the desk", cast=cast
        )
        assert "read the appendices" in _blocks(out)[1]

    async def test_first_person_narration_is_their_own_words(self, monkeypatch):
        import app.characters.context as ctx

        store = TypedStore(
            [_s("n1", "I wrote by the lamp until Bistritz.", "action", "narration_4")]
        )
        monkeypatch.setattr(ctx, "get_chunk_store", lambda: store)
        out = await ctx.build_character_context(
            _char("Jonathan Harker"), "Jonathan Harker", "the castle"
        )
        voice, move = _blocks(out)
        assert "Bistritz" in voice and "Bistritz" not in move

    async def test_cast_context_passes_the_cast(self, monkeypatch):
        import app.characters.context as ctx

        store = TypedStore(
            [
                _s(
                    "a1",
                    "Milo Voss read the appendices. Zara Okafor watched him read them.",
                    "description",
                )
            ]
        )
        monkeypatch.setattr(ctx, "get_chunk_store", lambda: store)
        out = await ctx.build_cast_context(
            [_char("Milo Voss"), _char("Zara Okafor")], "the desk"
        )
        milo, zara = out.split("### Zara Okafor")
        assert "read the appendices" in milo and "read the appendices" not in zara


def test_centres():
    from app.characters.context import _centres

    cast = [{"name": "Milo Voss"}, {"name": "Zara Okafor"}]
    text = "Milo Voss read the appendices. Zara Okafor watched him read them."
    assert _centres(text, "Milo Voss", cast)
    assert not _centres(text, "Zara Okafor", cast)
    assert _centres("Then Zara left the room.", "Zara Okafor", cast)
    assert _centres("He hummed while filing.", "Zara Okafor", cast)  # names nobody
    assert not _centres("The ledger was Milo's.", "Zara Okafor", cast)
    # No cast given: another proper name still disqualifies it.
    assert not _centres(text, "Zara Okafor")


# --- reindex-voice ----------------------------------------------------------------


class _Ctx:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *a):
        return False


async def test_reindex_voice_reindexes_each_source_for_its_cast(
    async_session, test_book, test_source, test_character, monkeypatch
):
    import app.core.database as db_mod
    from app.cli import _reindex_voice
    from app.core.orm_models import Character, Source

    monkeypatch.setattr(db_mod, "get_async_session", lambda: _Ctx(async_session))
    other = Source(
        book_id=test_book.id,
        user_id=test_book.user_id,
        kind="paste",
        title="Notes",
    )
    unvoiced = Source(
        book_id=test_book.id,
        user_id=test_book.user_id,
        kind="paste",
        title="Empty",
    )
    merged = Character(
        book_id=test_book.id, user_id=test_book.user_id, name="Merged", source_id=None
    )
    async_session.add_all([other, unvoiced, merged])
    await async_session.commit()

    calls = []

    async def fake_index(source_id, book_id, user_id, ids):
        calls.append((source_id, book_id, user_id, sorted(ids)))

    async def fake_voiced(book_id):
        # `other` voiced the merged character before; a stale pair for a
        # character no longer in the book is ignored.
        return {(other.id, merged.id), (other.id, uuid.uuid4())}

    done = await _reindex_voice(test_book.id, index=fake_index, voiced=fake_voiced)
    assert done == 2
    assert sorted(calls, key=lambda c: str(c[0])) == sorted(
        [
            (test_source.id, test_book.id, test_book.user_id, [str(test_character.id)]),
            (other.id, test_book.id, test_book.user_id, [str(merged.id)]),
        ],
        key=lambda c: str(c[0]),
    )


async def test_reindex_voice_unknown_book(async_session, monkeypatch):
    import app.core.database as db_mod
    from app.cli import _reindex_voice

    monkeypatch.setattr(db_mod, "get_async_session", lambda: _Ctx(async_session))

    async def never(*a, **k):
        raise AssertionError("must not index")

    assert await _reindex_voice(uuid.uuid4(), index=never, voiced=never) == -1


def test_reindex_voice_is_a_cli_command(monkeypatch):
    import app.cli as cli

    seen = {}

    async def fake(book_id):
        seen["book"] = book_id
        return 1

    book = uuid.uuid4()
    monkeypatch.setattr(cli, "_reindex_voice", fake)
    monkeypatch.setattr("sys.argv", ["app.cli", "reindex-voice", "--book", str(book)])
    assert cli.main() == 0 and seen["book"] == book
