"""SPEC-18 (B5): the beat-level prose prompt.

Pins what reaches the model for one beat: the scene's whole beat plan with the
current beat marked and a stop line, a `<prior>` that keeps paragraph breaks
and never opens mid-sentence, the author's style ahead of any house style, and
the deterministic seam trim.
"""

import json
import re
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.llm_text import as_quoted_block
from app.core.prompt_markers import VOICE_CONTRAST_NOTE, VOICE_SAMPLES_LABEL
from app.orchestration import prose as prose_module
from app.orchestration.pov import NAMING_RULE, Pov, pov_instruction
from app.orchestration.revise import build_revise_prompt
from app.orchestration.prose import (
    GENERIC_CRAFT_GUIDANCE,
    PREV_TAIL_WORDS,
    SceneSetup,
    _canon_hash,
    narration_excerpts,
    prose_tail,
    scene_chapter,
    scene_name_warnings,
    trim_seam_repeat,
    write_beat_prose,
)
from app.orchestration.workflow import StoryPosition
from app.planning.canon import Canon, render_style

pytestmark = pytest.mark.unit


class FakeClient:
    def __init__(self, text="Prose."):
        self.text = text
        self.prompts: list[str] = []

    async def generate(self, messages, **kw):
        self.prompts.append(messages[0]["content"])
        return types.SimpleNamespace(text=self.text, tokens_in=1, tokens_out=1)


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(prose_module, "get_llm_client", lambda: client)
    return client


_BEATS = [
    {"description": "Milo finds the ledger.", "characters": ["Milo Voss"]},
    {"description": "Zara reads it aloud.", "characters": ["Zara Okafor"]},
    {"description": "The lights go out.", "characters": []},
]
_REQUEST = {"scene_description": "The audit.", "setting": "Archive"}


def _write(beat, prior="", **kw):
    kw = {
        "pov": Pov("first person"),
        "story_position": None,
        "secondary_block": "",
        **kw,
    }
    return write_beat_prose(beat, _REQUEST, "cast", prior, **kw)


def _sentence(i: int) -> str:
    return f"Sentence number {i} has exactly seven words."


# --- prose_tail -------------------------------------------------------------


class TestProseTail:
    def test_keeps_paragraph_breaks(self):
        text = "First paragraph here.\n\nSecond one, longer.\n\nThird and last."
        assert prose_tail(text, words=500) == text

    def test_takes_whole_paragraphs_from_the_end(self):
        paras = [" ".join(_sentence(p * 10 + i) for i in range(3)) for p in range(4)]
        text = "\n\n".join(paras)
        # 21 words per paragraph: a 42-word budget is exactly two paragraphs.
        assert prose_tail(text, words=42).split("\n\n") == paras[-2:]
        # 30 words: the earlier paragraph opens at the latest sentence that
        # still meets the budget (14 + 21 >= 30; 7 + 21 would not).
        kept = prose_tail(text, words=30).split("\n\n")
        assert kept[1] == paras[-1]
        assert kept[0] == f"{_sentence(21)} {_sentence(22)}"

    def test_long_first_paragraph_starts_at_a_sentence(self):
        text = " ".join(_sentence(i) for i in range(200))  # one 1400-word paragraph
        tail = prose_tail(text, words=PREV_TAIL_WORDS)
        assert tail.startswith("Sentence number ")
        assert len(tail.split()) >= PREV_TAIL_WORDS
        # The latest boundary that still meets the budget: one sentence fewer
        # would fall short.
        assert len(tail.split()) - 7 < PREV_TAIL_WORDS
        assert text.endswith(tail)

    def test_never_opens_mid_sentence_after_a_quote(self):
        sentences = [
            f'"Line {i} is spoken here," she said. He nodded.' for i in range(80)
        ]
        tail = prose_tail(" ".join(sentences), words=100)
        assert re.match(r'^("Line \d+|He nodded)', tail), tail[:40]

    def test_abbreviation_is_not_a_sentence_end(self):
        text = "Filler words go here. " * 3 + "Then Mr. Voss signed the last form."
        tail = prose_tail(text, words=5)
        assert tail == "Then Mr. Voss signed the last form."

    def test_short_text_returned_whole(self):
        assert prose_tail("Just this.") == "Just this."
        assert prose_tail("") == ""

    def test_last_words_match_the_old_flat_tail(self):
        """Old checkpoints fingerprinted the flat last-500-words tail; the new
        tail ends with exactly those words, so `_canon_hash` still matches."""
        text = "\n\n".join(
            " ".join(_sentence(p * 9 + i) for i in range(9)) for p in range(20)
        )
        legacy = " ".join(text.split()[-PREV_TAIL_WORDS:])
        tail = prose_tail(text)
        assert "\n\n" in tail
        assert " ".join(tail.split()[-PREV_TAIL_WORDS:]) == legacy
        setup = SceneSetup(Canon("c"), [], None, None, "", [], [], "", None)
        assert _canon_hash(setup, "ch", None, tail) == _canon_hash(
            setup, "ch", None, legacy
        )

    def test_an_edited_plan_changes_the_fingerprint(self):
        """A paused scene restarts when the plan its planner saw is edited
        (SPEC-43); with no plan the fingerprint is the old one."""
        bare = SceneSetup(Canon("c"), [], None, None, "", [], [], "", None)
        planned = bare._replace(plan=[(1, {"title": "Intake", "summary": "a"})])
        edited = bare._replace(plan=[(1, {"title": "Intake", "summary": "b"})])
        hashes = {_canon_hash(s, "ch", None) for s in (bare, planned, edited)}
        assert len(hashes) == 3
        earlier = bare._replace(earlier=[("Retained", ["Milo signs."])])
        assert _canon_hash(earlier, "ch", None) != _canon_hash(bare, "ch", None)


# --- trim_seam_repeat ---------------------------------------------------------


class TestSeamTrim:
    def test_drops_an_opening_sentence_that_repeats_the_prior(self):
        prior = "Earlier things.\n\nThe door closed behind them with a click."
        beat = "The door closed behind them, with a click! Milo turned.\n\nMore."
        assert trim_seam_repeat(beat, prior) == "Milo turned.\n\nMore."

    def test_keeps_a_different_opening(self):
        prior = "The door closed behind them with a click."
        beat = "The door stayed shut. Milo turned."
        assert trim_seam_repeat(beat, prior) == beat

    def test_short_sentences_are_left_alone(self):
        assert trim_seam_repeat("He waited. Then left.", "He waited.") == (
            "He waited. Then left."
        )

    def test_never_empties_a_beat(self):
        prior = "The door closed behind them with a click."
        assert trim_seam_repeat(prior, prior) == prior

    def test_no_prior_is_a_no_op(self):
        assert trim_seam_repeat("Anything at all here.", "") == "Anything at all here."


# --- the beat prompt ----------------------------------------------------------


class TestBeatPrompt:
    async def test_beats_block_marks_the_current_beat_and_stop_line(self, fake):
        await _write(_BEATS[1], all_beats=_BEATS, beat_index=1)
        prompt = fake.prompts[0]
        block = prompt.split("<beats>")[1].split("</beats>")[0]
        assert block.strip().splitlines() == [
            "1. Milo finds the ledger.",
            "→ 2. Zara reads it aloud.",
            "3. The lights go out.",
        ]
        assert "Write ONLY beat 2. Stop where beat 3 begins; do not anticipate it." in (
            prompt
        )
        # the stub's markers survive (evals/stub_llm.py)
        assert "CHARACTERS IN THIS BEAT" in prompt and "BEAT TO WRITE" in prompt
        # the plan precedes the beat's cast (it is not part of the cast block)
        assert prompt.index("</beats>") < prompt.index("CHARACTERS IN THIS BEAT")

    async def test_last_beat_ends_the_scene(self, fake):
        await _write(_BEATS[2], all_beats=_BEATS, beat_index=2)
        assert (
            "Write ONLY beat 3, the last beat: end the scene inside the moment, "
            "in POV; no summary of what follows." in fake.prompts[0]
        )
        assert "Stop where beat" not in fake.prompts[0]

    async def test_prior_keeps_paragraph_breaks(self, fake):
        prior = "He set the ledger down.\n\nShe did not look at it."
        await _write(_BEATS[1], prior)
        prompt = fake.prompts[0]
        assert (
            "<prior>\nHe set the ledger down.\n\nShe did not look at it.\n</prior>"
            in (prompt)
        )
        assert "do not restate anything in <prior>" in prompt

    async def test_sample_prose_leads_once_and_house_style_is_absent(self, fake):
        """W1-S3: the sample is in the canon's style guide; the style section
        refers to it rather than repeating it."""
        from evals.graders.prompt_contract import check_style_first

        sample = '"No," she said. "Not today."'
        style = types.SimpleNamespace(
            pov=None, tense=None, tone="clipped", comps=None, sample_prose=sample
        )
        canon_block = as_quoted_block(render_style(style), "canon")
        await _write(_BEATS[0], canon_block=canon_block, style=style)
        prompt = fake.prompts[0]
        assert prompt.count(sample) == 1 and prompt.count("clipped") == 1
        assert "<sample_prose>" not in prompt
        assert "primary style reference. Match its voice and rhythm" in prompt
        assert "PUNCTUATES" not in prompt and GENERIC_CRAFT_GUIDANCE not in prompt
        assert check_style_first(prompt, sample) == []

    async def test_tone_alone_replaces_house_style(self, fake):
        style = types.SimpleNamespace(tone="banter-heavy, dialogue first")
        canon_block = as_quoted_block(render_style(style), "canon")
        await _write(_BEATS[0], canon_block=canon_block, style=style)
        prompt = fake.prompts[0]
        assert prompt.count("banter-heavy, dialogue first") == 1
        assert "follow the tone in the canon's style guide" in prompt
        assert "PUNCTUATES" not in prompt

    async def test_house_style_only_without_tone_or_sample(self, fake):
        await _write(_BEATS[0])
        prompt = fake.prompts[0]
        assert GENERIC_CRAFT_GUIDANCE in prompt
        assert prompt.index(GENERIC_CRAFT_GUIDANCE) < prompt.index(
            "Write approximately"
        )
        assert "style guide, above" not in prompt

    async def test_house_style_governs_the_narration_not_the_speakers(self, fake):
        """(F) W18-1: R2's "terse, plain, unlike the long ornate samples"."""
        await _write(_BEATS[0])
        assert "PUNCTUATES" in GENERIC_CRAFT_GUIDANCE
        assert "not how a character speaks" in GENERIC_CRAFT_GUIDANCE
        assert "speaker whose samples run long keeps long lines" in fake.prompts[0]

    async def test_each_beat_speaker_is_tied_to_their_own_block(self, fake):
        """(F) W18-1: both speakers named, with their block and the contrast
        line, ahead of the style section; (R) a name with no block is not."""
        cast = (
            "<character>\n### Victor\nVoice samples\n</character>\n\n"
            "<character>\n### Elizabeth\n</character>\n\n"
            f"{VOICE_CONTRAST_NOTE}\n<contrast>\n- Victor: …\n</contrast>"
        )
        beat = {"description": "They talk.", "characters": ["Victor", "Elizabeth"]}
        kw = dict(pov=Pov("first person"), story_position=None, secondary_block="")
        await write_beat_prose(beat, _REQUEST, cast, "", speakers=None, **kw)
        await write_beat_prose(beat, _REQUEST, cast, "", speakers=["Walton"], **kw)
        voices, unnamed = fake.prompts
        rule = voices.split("VOICES — ")[1].split("\n\n")[0]
        assert "THEIR OWN <character> block and the <contrast> line" in rule
        assert "Victor's lines follow Victor's block; Elizabeth's lines" in rule
        assert "not a shared narrative register" in rule
        assert "long and periodic" in rule
        assert voices.index("VOICES — ") < voices.index(GENERIC_CRAFT_GUIDANCE)
        assert "VOICES — " not in unnamed  # Walton has no block here
        await _write(_BEATS[0])  # the stub cast "cast" shows no block
        assert "VOICES — " not in fake.prompts[-1]

    async def test_book_narration_is_the_style_reference_without_sample(self, fake):
        """(F) W18-5: no sample prose → the book's own narration, fenced and
        labelled, ahead of the tone; (R) sample prose keeps today's section."""
        excerpts = ("I arrived here yesterday. All is well.", "The ice closed in.")
        await _write(_BEATS[0], narration=excerpts)
        tone = types.SimpleNamespace(tone="grave")
        await _write(_BEATS[0], narration=excerpts, style=tone)
        sample = types.SimpleNamespace(tone="grave", sample_prose="She ran.")
        await _write(_BEATS[0], narration=excerpts, style=sample)
        bare, toned, sampled = fake.prompts
        block = as_quoted_block("\n\n".join(excerpts), "book_narration")
        assert f"write new prose in this voice:\n{block}\n\nWrite approx" in bare
        assert "STYLE — the book's own narration" in bare
        assert "person and tense" in bare and "Never copy their words" in bare
        assert GENERIC_CRAFT_GUIDANCE not in bare
        assert f"{block}\nFollow the tone in the canon's style guide" in toned
        assert "<book_narration>" not in sampled
        assert "the author's sample prose (the canon's style guide" in sampled

    async def test_no_plan_given_none_rendered(self, fake):
        assert await _write(_BEATS[0]) == "Prose."
        assert "<beats>" not in fake.prompts[0]
        assert "BEAT TO WRITE: Milo finds the ledger." in fake.prompts[0]

    async def test_the_naming_rule_follows_the_pov_rule(self, fake):
        """(F) SPEC-48 (D10): the words the author keeps get the naming rule."""
        await _write(_BEATS[0])
        pov = pov_instruction(Pov("first person"))
        assert f"{pov}\n{NAMING_RULE}\n" in fake.prompts[0]


@pytest.mark.parametrize("voiced", [False, True])
async def test_the_voice_header_claims_samples_only_when_shown(fake, voiced):
    """(F) SPEC-70: W8b's empty store, canon clause; (R) a sample, W8b's header."""
    fixture = Path(__file__).parents[1] / "fixtures/w8_archives.json"
    section = json.loads(fixture.read_text())["w8b_empty_store"]["characters_section"]
    head, cast = section.split("\nmatching their voice samples:\n\n")
    if voiced:
        cast = cast.replace("Okafor\n", f"Okafor\n{VOICE_SAMPLES_LABEL}\n")
    clause = "matching their voice samples" if voiced else "as the canon describes it"
    kw = dict(pov=Pov("first person"), story_position=None, secondary_block="")
    await write_beat_prose(_BEATS[0], _REQUEST, cast, "", **kw)
    assert f"{head}\n{clause}:\n\n{cast}\n\n" in fake.prompts[0]
    args = dict(canon_block="", chapter_summary="", prior="", after="", target="")
    revise = build_revise_prompt(**args, **kw, cast_context=cast, instruction="")
    assert f"THEIR OWN voice, {clause}:\n\n{cast}\n" in revise


def test_scene_name_warnings_on_the_archived_scenes():
    """(F) SPEC-48 (D8): the fixture's rows; (R) known people unlisted."""
    w5 = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "w5_archives.json").read_text("utf-8")
    )
    terms = set(w5["canon"]["terms"])
    synopsis = (Path(__file__).parents[2] / w5["canon"]["synopsis"]).read_text("utf-8")
    canon = types.SimpleNamespace(synopsis=synopsis, canon_terms=lambda: terms)
    for r in w5["name_warnings"]["rows"]:
        assert scene_name_warnings(r["prose"], canon, {}, "") == r["names"], r["run"]
    text = "Halvard Bell rang twice. Milo nodded to Imre Kasza, who said nothing."
    also = {"secondary_names": ["Imre Kasza"]}
    assert scene_name_warnings(text, canon, also, "") == []
    brief = {"scene_description": "Milo nods to Imre Kasza."}
    assert scene_name_warnings(text, canon, brief, "") == []  # (R) the author's


def test_narration_excerpts_are_whole_unquoted_paragraphs_in_scope():
    """(F) W18-5: quoted, short, long, later-plot and brief-restating paragraphs
    are skipped; the rest are picked evenly, whole, in upload order."""
    sentence = "The wind rose over the dark lake that night. "
    para = [(f"Para {i}. " + sentence * 6).strip() for i in range(8)]  # 51 words
    text = "\n\n".join(
        [
            para[0],
            '"Come," said he. ' + sentence * 6,  # quotes someone
            "Too short. Yes.",
            sentence * 30,  # 270 words: skipped, never cut
            para[1],
            para[2],
            ledger := "Para x. The ledger burned in the archive grate. "
            + (sentence * 5).strip(),
            para[3],
            para[4],
        ]
    )
    picks = narration_excerpts([("s", text), ("t", para[5])], {}, "")
    assert picks == (para[0], ledger, para[5])  # 7 eligible: 0, 3, 6
    assert narration_excerpts([("s", text)], {"s": 5}, "") == (para[0], para[1])
    brief = "The ledger burned slowly in the archive grate."
    assert narration_excerpts([("s", text)], {}, brief) == (para[0], para[2], para[4])
    assert narration_excerpts([], {}, "") == ()


# --- scene_chapter (SPEC-30) ----------------------------------------------------


async def test_scene_chapter_counts_live_chapters_only(async_session, test_book):
    from app.core.orm_models import Chapter, Scene

    def chapter(pos, trashed=False):
        return Chapter(
            book_id=test_book.id,
            title=f"c{pos}",
            position=pos,
            summary=f"s{pos}",
            deleted_at=datetime.now(timezone.utc) if trashed else None,
        )

    chapters = [chapter(i) for i in range(5)] + [chapter(5, trashed=True)]
    async_session.add_all(chapters)
    await async_session.flush()
    second, trashed = (
        Scene(user_id=test_book.user_id, chapter_id=c.id, status="processing")
        for c in (chapters[1], chapters[5])
    )
    loose = Scene(user_id=test_book.user_id, book_id=test_book.id, status="processing")
    async_session.add_all([second, trashed, loose])
    await async_session.commit()
    assert await scene_chapter(async_session, second) == ("s1", StoryPosition(2, 5))
    # R-3: a trashed chapter keeps its summary but has no place in the book.
    assert await scene_chapter(async_session, trashed) == ("s5", None)
    assert await scene_chapter(async_session, loose) == ("", None)
