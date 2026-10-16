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
        assert cx.extract_dialogue_only(SOURCE, "Zara Okafor") == ["Don't thank her."]
        lines = [
            c["text"]
            for c in _dialogue(cx.extract_character_content(SOURCE, "Zara Okafor"))
        ]
        assert lines == ["Don't thank her."]

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
        assert [c["text"] for c in _dialogue(zara)] == ["Don't thank her."]

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

    def test_pronoun_tag_credits_nobody(self):
        # Review B2: a pronoun tag fell back to the beat's subject, which in a
        # mixed-gender two-hander is usually the LISTENER.
        assert _speakers('"Fine," she said.') == [("Fine,", "she")]
        assert _speakers('Zara shrugged. "Fine," she said.') == [("Fine,", "she")]
        text = (
            'Milo looked at her across the desk. "Sit down before you fall '
            'down," she said.'
        )
        assert _speakers(text) == [("Sit down before you fall down,", "she")]
        assert CharacterExtractor().extract_dialogue_only(text, "Milo") == []
        cx = CharacterExtractor()
        for text in ('Milo slammed the folder down. "Sit," she said.', text):
            for c in cx.extract_character_content(text, "Milo"):
                assert "Sit" not in c["text"]  # not even as his narration
        for who in ("he", "she", "they", "i", "you"):
            assert not speaker_matches(who, "Zara Okafor", cast=E2E_CAST)
        # "I said" is the narrator: it never resolves to the beat's subject
        # (Dracula: 'Dressing-gown and dress were both in their places.
        # "Thank God," I said').
        assert _speakers('Zara shrugged. "Fine," I said.') == [("Fine,", "i")]
        # A capitalised opener is not a subject ("For he grew pale. '…'").
        assert _speakers('For he grew pale. "You are to help her."') == [
            ("You are to help her.", None)
        ]

    def test_said_x_to_y_is_xs_line(self):
        # Review B1: `said the auditor to Milo` captured "the auditor to Milo",
        # and a two-way subset match gave the line to Milo.
        text = '"Read the appendices first," said the auditor to Milo.'
        assert _speakers(text) == [("Read the appendices first,", "the auditor")]
        cast = [{**c, "aliases": [c["name"].split()[0]]} for c in E2E_CAST]
        cx = CharacterExtractor()
        got = cx.extract_character_content(
            text, "Milo Voss", aliases=["Milo"], cast=cast
        )
        assert _dialogue(got) == []
        assert _speakers('"Then go," said Corin to the porter.') == [
            ("Then go,", "Corin")
        ]
        assert _speakers('"Sign it," Ada said to the clerk.') == [("Sign it,", "Ada")]
        assert _speakers('"Not today," the old man told Zara.') == [
            ("Not today,", "the old man")
        ]
        assert _speakers('"Is it far?" the porter asked Zara.') == [
            ("Is it far?", "the porter")
        ]
        assert _speakers('"Zara, run," Milo said.') == [("Zara, run,", "Milo")]

    def test_speaker_must_lie_within_the_name(self):
        # One direction only: every word of the speaker is in one name form.
        assert not speaker_matches("the auditor to Milo", "Milo Voss", ["Milo"])
        assert not speaker_matches("Milo Voss", "Milo")  # cast says "Milo" only
        assert speaker_matches("Milo Voss", "Milo", ["Milo Voss"])
        assert speaker_matches("Ms. Okafor", "Zara Okafor")
        assert speaker_matches("Count", "Count Vasska")
        assert speaker_matches("the Countess", "Vasska", ["the Countess"])
        assert not speaker_matches("the caseworker", "Milo Voss")

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
        # A pronoun-tagged turn names nobody, so it ends the exchange too.
        mixed = '"Ready?" asked Milo.\n\n"Always," she said.\n\n"Then go."'
        assert _speakers(mixed)[-1] == ("Then go.", None)

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
        assert cx.extract_dialogue_only(text, "Mina Harker") == ["Enough."]
        assert cx.extract_dialogue_only('"Enough," said Mina.', "Mina Harker") == [
            "Enough."
        ]
        assert cx.extract_dialogue_only(
            '"Enough," said the Countess.', "Vasska", aliases=["the Countess"]
        ) == ["Enough."]


# --- the hand-labelled precision gate (gates CI) --------------------------------


def _fixture_predictions(sample):
    cast = FIXTURE["cast"]
    spans = [s for para in attribute_speech(sample["text"]) for s in para]
    assert [s["text"] for s in spans] == [ln["quote"] for ln in sample["lines"]]
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


def test_held_out_lines_cover_every_speaker():
    # Written before the B1/B2 rule changes and never used to tune them; the
    # last two (N1, a reply after another person's beat) came from the
    # principal's re-review, before its fix.
    held_out = FIXTURE["held_out"]["lines"]
    assert len(held_out) == 19
    assert {ln["speaker"] for ln in held_out} - {None} == {
        c["name"] for c in FIXTURE["cast"]
    }


@pytest.mark.parametrize("sample", ["in-sample", "held-out"])
def test_attribution_precision_gate(sample):
    # Reported separately: the 20 in-sample lines were written alongside the
    # rules; the held-out lines were not.
    data = FIXTURE if sample == "in-sample" else FIXTURE["held_out"]
    predicted = _fixture_predictions(data)
    truth = [ln["speaker"] for ln in data["lines"]]
    attributed = [(p, t) for p, t in zip(predicted, truth) if p is not None]
    correct = sum(1 for p, t in attributed if p == t)
    precision = correct / len(attributed)
    labelled = [t for t in truth if t is not None]
    recall = correct / len(labelled)
    print(f"{sample}: precision={precision:.3f} recall={recall:.3f}")
    assert precision >= PRECISION_FLOOR, list(zip(predicted, truth))
    # Recall is reported, not gated (precision over recall) — but a regression
    # that attributes almost nothing would pass the precision gate vacuously.
    assert recall >= 0.5


# --- chunk classification -------------------------------------------------------


class TestChunking:
    def test_paragraph_gives_its_speaker_their_line_and_no_one_else_anything(self):
        text = '"Go on without me," said Zara. Milo walked.'
        cx = CharacterExtractor()
        got = cx.extract_character_content(text, "Zara")
        assert [(c["chunk_type"], c["text"]) for c in got] == [
            ("dialogue", "Go on without me.")
        ]
        assert cx.extract_character_content(text, "Milo") == []

    def test_a_voice_sample_is_at_least_three_words(self):
        # A bare vocative or exclamation says nothing about a voice (after-eval).
        text = (
            '"Zara," Milo said.\n\n"So!" said Milo.\n\n'
            '"Read it all first," said Milo.'
        )
        chunks = CharacterExtractor().extract_character_content(text, "Milo")
        assert [(c["chunk_type"], c["text"]) for c in chunks] == [
            ("dialogue", "Read it all first.")
        ]

    def test_paragraph_with_someone_elses_speech_is_not_indexed_for_them(self):
        text = 'The clerk stood. "Sign here, please," she said. Milo took the pen.'
        assert CharacterExtractor().extract_character_content(text, "Milo") == []

    def test_scare_quotes_do_not_drop_a_paragraph(self):
        text = "Milo hated the word 'synergy' and walked out."
        chunks = CharacterExtractor().extract_character_content(text, "Milo")
        assert [c["chunk_type"] for c in chunks] == ["action"]

    def test_their_line_and_movement_are_separate_chunks(self):
        text = 'Zara stood. "That is enough," said Zara.\n\nZara walked to the door.'
        chunks = CharacterExtractor().extract_character_content(text, "Zara")
        assert [(c["chunk_type"], c["text"]) for c in chunks] == [
            ("dialogue", "That is enough."),
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


def _scope(*cast):
    from app.characters.context import SceneScope

    return SceneScope(tuple(cast), tuple(cast), (), frozenset(), "", "")


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
            _char(), "Zara Okafor", "the desk", scope=_scope(), grounding=grounding
        )
        assert ctx.VOICE_SAMPLES_LABEL in out
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
        out = await ctx.build_character_context(
            _char(), "Zara Okafor", "the desk", scope=_scope()
        )
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
            cast[1], "Zara Okafor", "the desk", scope=_scope(*cast)
        )
        _, move = _blocks(out)
        assert "did not look up" in move
        assert "read the appendices" not in out
        # ...and the same passage DOES centre Milo.
        out = await ctx.build_character_context(
            cast[0], "Milo Voss", "the desk", scope=_scope(*cast)
        )
        assert "read the appendices" in _blocks(out)[1]

    async def test_movement_is_judged_against_the_scene_not_the_beat(self, monkeypatch):
        """B-3: Zara is in the scene but not in Milo's beat; her narration is
        still not how Milo moves."""
        import app.characters.context as ctx

        text = "Zara watched Milo read the appendices."
        store = TypedStore([_s("a1", text, "description")])
        monkeypatch.setattr(ctx, "get_chunk_store", lambda: store)
        milo, zara = _char("Milo Voss"), _char("Zara Okafor")
        out = await ctx.build_cast_context([milo], "x", scope=_scope(milo, zara))
        assert "### Milo Voss" in out and "read the appendices" not in out
        # Non-vacuous: alone in the scene, the same passage is his.
        out = await ctx.build_cast_context([milo], "x", scope=_scope(milo))
        assert "read the appendices" in _blocks(out)[1]

    async def test_first_person_narration_is_their_own_words(self, monkeypatch):
        import app.characters.context as ctx

        store = TypedStore(
            [_s("n1", "I wrote by the lamp until Bistritz.", "action", "narration_4")]
        )
        monkeypatch.setattr(ctx, "get_chunk_store", lambda: store)
        out = await ctx.build_character_context(
            _char("Jonathan Harker"), "Jonathan Harker", "the castle", scope=_scope()
        )
        voice, move = _blocks(out)
        assert "Bistritz" in voice and "Bistritz" not in move

    async def test_cast_context_passes_the_scope(self, monkeypatch):
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
        cast = [_char("Milo Voss"), _char("Zara Okafor")]
        out = await ctx.build_cast_context(cast, "the desk", scope=_scope(*cast))
        milo, zara = out.split("### Zara Okafor")
        assert "read the appendices" in milo and "read the appendices" not in zara

    async def test_agent_context_filters_by_the_cast(self, monkeypatch):
        import app.characters.context as ctx

        store = TypedStore([_s("a1", "Milo Voss filed the form.", "action")])
        monkeypatch.setattr(ctx, "get_chunk_store", lambda: store)
        cast = [_char("Milo Voss"), _char("Zara Okafor")]
        scope = _scope(*cast)
        out = await ctx.build_agent_context(
            cast[1], "Zara Okafor", "the desk", scope=scope
        )
        assert "filed the form" not in out
        out = await ctx.build_agent_context(
            cast[0], "Milo Voss", "the desk", scope=scope
        )
        assert "filed the form" in out


def test_centres():
    from app.characters.context import _centres

    cast = [{"name": "Milo Voss"}, {"name": "Zara Okafor"}]
    text = "Milo Voss read the appendices. Zara Okafor watched him read them."
    assert _centres(text, "Milo Voss", cast)
    assert not _centres(text, "Zara Okafor", cast)
    assert _centres("Then Zara left the room.", "Zara Okafor", cast)
    assert _centres("He hummed while filing.", "Zara Okafor", cast)  # names nobody
    assert not _centres("The ledger was Milo's.", "Zara Okafor", cast)
    # Wave-1 review S2: a named place is not someone else.
    milo = "Milo walked the Marrow District at dusk."
    assert _centres(milo, "Milo Voss", cast, ["Milo"])
    assert _centres("At dusk he walked the Marrow District.", "Milo Voss", cast)
    # Another cast member's passage is not his, lead or not.
    assert not _centres("Zara crossed the floor.", "Milo Voss", cast, ["Milo"])
    assert not _centres("At dusk, Zara crossed the floor.", "Milo Voss", cast)
    # The lead subject decides, even when the passage later names another.
    lead = "Milo stamped the form. Zara watched from the Aeon Holdings door."
    assert _centres(lead, "Milo Voss", cast, ["Milo"])
    assert not _centres(lead, "Zara Okafor", cast, ["Zara"])
    # No cast: nobody else is known, so only the lead can claim it.
    assert _centres(milo, "Milo Voss")


# --- SPEC-30: movement samples name no one outside the scene (cel cast) --------


def _cel(synopsis=True):
    """The real cel cast (Edric with "the CEL", "Chief Executive Lich", "the
    Lich") and its canon, by first name."""
    from app.planning.canon import Canon
    from evals.steps import pipeline

    _, gt = pipeline.load_corpus("cel")
    cast = [_char(p["name"], p["aliases"]) for p in pipeline._cast_with_roles(gt)]
    syn = gt["synopsis"] if synopsis else ""
    canon = Canon(title="Cel", synopsis=syn, characters=cast)
    return canon, {c.name.split()[0]: c for c in cast}


async def _milo_moves(monkeypatch, texts, scene, secondaries=(), synopsis=True):
    """Milo's movement lines in a scene of Milo + `scene` (first names)."""
    import app.characters.context as ctx

    canon, by = _cel(synopsis)
    store = TypedStore([_s(f"a{i}", t, "action") for i, t in enumerate(texts)])
    monkeypatch.setattr(ctx, "get_chunk_store", lambda: store)
    cast = [by["Milo"], *(by[n] for n in scene)]
    scope = ctx.SceneScope.of(canon, cast, secondaries, "")
    out = await ctx.build_character_context(by["Milo"], "Milo Voss", "x", scope=scope)
    return [ln[2:] for ln in _blocks(out)[1].splitlines() if ln.startswith("- ")]


class TestMovementInScene:
    WALK = "Milo walked the Marrow District at dusk."
    DUSK = "At dusk he walked the Marrow District."
    BELL = "On Tuesday the Bell account came across Milo's queue."
    EDRIC = "Milo carried the memo up to Edric's floor."
    FEN = "Milo fetched tea for Old Fen."

    async def test_a_common_word_is_not_a_name(self, monkeypatch):
        """B-2: "the" and "chief" are not Edric, in his scene or out of it."""
        from app.characters.context import SceneScope, _centres, names_outside_scene

        assert await _milo_moves(monkeypatch, [self.WALK], ["Zara"]) == [self.WALK]
        assert await _milo_moves(monkeypatch, [self.DUSK], ["Edric"]) == [self.DUSK]
        canon, by = _cel()
        chief = "The chief reason was the form."
        assert _centres(chief, "Milo Voss", [by["Milo"], by["Edric"]], ["Milo"])
        scope = SceneScope.of(canon, [by["Milo"], by["Zara"]], (), "")
        assert names_outside_scene(chief, by["Milo"], scope) == []
        # Edric is still seen by any of his forms, capitalised.
        lich = "Milo bowed to the Lich."
        assert names_outside_scene(lich, by["Milo"], scope) == ["Edric Thane"]

    # W3-2: Bell is no one here (two retrieved, one kept: no refill after the
    # filter); a cast member counts only outside the scene.
    @pytest.mark.parametrize(
        "texts, scene, kept",
        [([BELL, WALK], ["Zara"], [WALK]), ([EDRIC], ["Zara"], [])]
        + [([EDRIC], ["Edric"], [EDRIC])],
    )
    async def test_outside_names_drop(self, monkeypatch, texts, scene, kept):
        assert await _milo_moves(monkeypatch, texts, scene) == kept

    async def test_a_named_secondary_belongs_to_the_scene(self, monkeypatch):
        # Without the synopsis, nothing else makes Old Fen known.
        fen = await _milo_moves(monkeypatch, [self.FEN], [], synopsis=False)
        assert fen == []
        fen = await _milo_moves(monkeypatch, [self.FEN], [], ["Old Fen"], False)
        assert fen == [self.FEN]


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


def test_quote_halves_split_by_a_tag_read_as_speech():
    """The tag's comma closes to a full stop when the next half is a new
    sentence, and stays when the sentence carries on across the tag."""
    from app.parsing.character_extractor import _join_utterance

    assert (
        _join_utterance(["Don't thank her,", "Read the appendices first."])
        == "Don't thank her. Read the appendices first."
    )
    assert (
        _join_utterance(["You must,", "there is no other way."])
        == "You must, there is no other way."
    )
    assert _join_utterance(["I'm good, thanks,"]) == "I'm good, thanks."


def test_narration_between_quotes_stops_the_paragraph_speaker():
    """Principal re-review N1: the guard's reply is not Zara's because a new
    sentence of narration comes between; a tag-only gap still joins."""
    from app.parsing.character_extractor import attribute_speech

    (spans,) = attribute_speech(
        '"Go now," Zara told the guard. The guard laughed. "Make me, lady."'
    )
    assert [s["speaker"] for s in spans] == ["Zara", None]
    (spans,) = attribute_speech('"Hi," said Zara. "Come in."')
    assert [s["speaker"] for s in spans] == ["Zara", "Zara"]


# --- SPEC-36 (W4-3, B-1): a movement sample that narrates the brief drops ------
# The 11 distinct (brief, movement sample) pairs in the run-W3 relay archive's
# beat_prose prompts (/tmp/claude-0/q-w3/model/answered), deduplicated on the
# exact strings, with the content words each shares with the brief.

TRAINS = "Zara Okafor trains Milo and begins recruiting him"
RETAINED = (
    "Retained: Prava slides a laminated org chart across a laminate desk and tells "
    "Milo his skills are a strong culture fit; he says yes before anyone offers him "
    "a choice, and his death becomes, to his delight, a job."
)
NEXT_DESK = (
    "The Next Desk: In the long Reconciliations room Milo pitches trackers and "
    "low-hanging fruit across the partition, and Zara answers in flat imperatives: "
    "close your queue, read the appendices. He mistakes her courtesy for "
    "friendship, and she quietly marks him as the analyst she has been waiting for."
)
FOUR_MINUTES = (
    "Under Four Minutes: Milo zeroes a restructured soul's tenure and initials the "
    "form, proud of his speed; the soul's desk is cleared before the coffee station "
    "finishes dispensing. When he asks where the soul goes, Zara says only that it "
    "goes under, Old Fen remarks that nobody reads the appendices, and for the "
    "first time Milo's satisfaction has a crack in it."
)
DRAWER = (
    "The Drawer: That night Milo locks the ledger in his desk drawer, which no one "
    "asked him to do, and he cannot say why; it is his first unoptimized act."
)
LOCKED = (
    "Milo locked the ledger in his desk drawer that night, which no one had asked "
    "him to do, and he could not have said why he did it."
)
LOOKED = (
    "Zara looked at the column for a long time. Her grey eyes did not change at all."
)
INTAKE = (
    "Milo Voss opened his eyes in the intake atrium and a caseworker slid a "
    "laminated org chart across a laminate desk."
)


@pytest.mark.parametrize(
    "brief, sample, shared, drops",
    [
        (DRAWER, LOCKED, 6, True),  # asked desk drawer ledger milo night
        (RETAINED, INTAKE, 5, True),  # chart desk laminate laminated milo
        (RETAINED, LOCKED, 2, False),
        (NEXT_DESK, INTAKE, 2, False),
        (NEXT_DESK, LOOKED, 2, False),
        (NEXT_DESK, LOCKED, 2, False),
        (FOUR_MINUTES, LOCKED, 2, False),
        (FOUR_MINUTES, LOOKED, 2, False),
        (TRAINS, LOCKED, 1, False),
        (TRAINS, LOOKED, 1, False),
        (TRAINS, INTAKE, 1, False),
    ],
)
def test_the_eleven_archive_pairs_drop_exactly_the_two_events(
    brief, sample, shared, drops
):
    """(F) RESTATES_MIN = 4 sits midway between unrelated (<= 2) and event (5-6)."""
    from app.characters.context import RESTATES_MIN, restates_brief

    assert (shared >= RESTATES_MIN) is drops
    assert restates_brief(sample, brief) is drops
    assert restates_brief(sample, "") is False  # no brief: a probe, a manual scene


async def _moves(monkeypatch, who, texts, brief, query="x"):
    """(voice, movement) blocks for `who` in a cel scene with `brief`."""
    import app.characters.context as ctx

    canon, by = _cel()
    store = TypedStore(
        [_s("d0", "Is there a dashboard or a tracker?", "dialogue", "dialogue_1")]
        + [_s(f"a{i}", t, "action") for i, t in enumerate(texts)]
    )
    monkeypatch.setattr(ctx, "get_chunk_store", lambda: store)
    scope = ctx.SceneScope.of(canon, [by["Milo"], by["Zara"]], (), brief)
    out = await ctx.build_character_context(by[who], who, query, scope=scope)
    return _blocks(out)


async def test_the_drawer_sample_leaves_the_drawer_scene(monkeypatch):
    """(F) The brief carries the event; the sample that narrates it goes, with
    no refill, and the voice samples are untouched."""
    voice, move = await _moves(monkeypatch, "Milo", [LOCKED, INTAKE], DRAWER)
    assert LOCKED not in move and INTAKE in move
    assert "Is there a dashboard or a tracker?" in voice


async def test_a_revise_span_never_drops_a_gesture(monkeypatch):
    """(R) B-1: revise retrieves with the author's prose span (here the run-W3
    scene-1 beat, sharing 7 content words with Zara's gesture). The rule reads
    the brief only, so the gesture stays."""
    span = (
        "Tighten this.\nThe account came over the partition face down, pushed by "
        "two fingers. Zara did not say anything. She did not need to; it had "
        "RESTRUCTURED stamped across the back in red, and Milo had learned that "
        "much by Wednesday.\n\nHe flipped it, zeroed the tenure, initialled the box "
        "marked INITIAL HERE, and slid it back. Three minutes, maybe less. He had "
        "not timed it, but he had the sense of having been fast, the way you know "
        'you have parked well.\n\n"Done," he said. "And honestly, that one was '
        "super clean. Whoever set up the intake fields did a great job, so, kudos "
        'upstream."\n\nZara picked up the form. She looked at his initials for a '
        "long time. Her grey eyes did not change at all.\n\nMilo waited. Nothing "
        "came. He decided that, from her, nothing was probably a very good "
        "review.\n\nShe put the form in her out-tray and did not look up again."
    )
    _, move = await _moves(monkeypatch, "Zara", [LOOKED], FOUR_MINUTES, query=span)
    assert LOOKED in move
