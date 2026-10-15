"""The prompt-contract grader asserts what the app sent the model, per purpose,
from the relay's answered/ archive (cycle 2026-10 eval plan §3.3).

Passing prompts come from the app itself (`test_prompt_roundtrip.py`); this
file keeps one FAILING fixture per check, so each check is shown to bite."""

import json

import pytest

from evals.graders import prompt_contract as pc

pytestmark = pytest.mark.unit

GOLD = {
    "Zara Okafor": {"train": ["Don't thank her,"], "test": ["Close the accounts."]},
    "Milo Voss": {"train": ["Is there a dashboard or a tracker for that?"]},
}
OTHER = {"the caseworker": ["You've been retained,"]}
PRINCIPALS = [
    {"name": "Milo Voss", "aliases": ["Milo"]},
    {"name": "Zara Okafor", "aliases": ["Zara"]},
    {"name": "Edric Thane", "aliases": ["Edric", "the Lich"]},
]
MILO_DESC = "A newly dead mid-level operations analyst."
SAMPLE = "Milo initialled the form in the box marked INITIAL HERE."
CHAPTER = {
    "title": "Strong Culture Fit",
    "summary": "Milo dies and is onboarded.",
    "pov": "Milo Voss",
    "people": ["Milo Voss", "Zara Okafor", "Prava"],
    "children": [{"title": "Intake", "summary": "x", "people": ["Milo", "Prava"]}],
}

# One record per purpose, each breaking every check that reads it.
FAILING = [
    (
        "outline:chapters",
        "Voice: voice: flat\nActs: [{'goal': 'x'}]",
        "",
    ),
    (
        "outline:beats",
        "Here are the chapters of act 0, in order:\n[{'title': 'One'}]",
        json.dumps([CHAPTER]),
    ),
    (
        "plan_beats",
        "Scene description: Intake: x\nSetting: \nEmotional tone: neutral\n"
        "Available cast (not all need appear): Milo Voss, Zara Okafor, Prava",
        "",
    ),
    (
        "beat_prose",
        "<chapter>\nMilo dies and is onboarded.\n</chapter>\n"
        "This scene is in chapter 5 of 4.\n"
        "Also named (secondary, not in the cast): Prava, Old Fen\n"
        "<beats>\n1. He arrives.\n→ 2. He signs.\n</beats>\n"
        "Point of view: third person limited, Zara Okafor. Narrate only what "
        "Zara Okafor perceives or knows.\n"
        "<canon>\n### Milo Voss\n" + f"Description: {MILO_DESC}\n</canon>\n"
        "CHARACTERS IN THIS BEAT\n### Milo Voss\n" + f"Description: {MILO_DESC}\n"
        'Voice samples (their own words):\n- "Don\'t thank her,"\n'
        "<secondary>\n### Old Fen\nIntroduced: Old Fen files.\n</secondary>\n"
        "BEAT TO WRITE: the last beat.\n"
        "<prior>\nand then he left.\n</prior>\n"
        "description and action carry the scene.",
        "He signed.",
    ),
    (
        "beat_prose",
        "<chapter>\nMilo dies and is onboarded.\n</chapter>\nWrite beat 1.",
        "",
    ),
    (
        "extraction_reduce",
        "### Milo Voss\nEvidence:\n- (part 1) one line\n</characters>",
        "",
    ),
]

REASONS = {
    "beats_block": "no <beats> block",
    "prior_paragraphs": "<prior> starts mid-sentence",
    "voice_dialogue": 'Milo Voss: misattributed: "Don\'t thank her,"',
    "beats_stage": 'acts numbered from 0 ("act 0")',
    "outline_no_repr": "Python repr ({') in the prompt",
    "style_first": "generic craft text present: 'description and action carry the scene'",
    "plan_cast_from_beat": "cast ['milo voss', 'prava', 'zara okafor'] != beat cast + "
    "POV ['milo voss']",
    "no_blank_setting": 'blank "Setting:" line',
    "no_neutral_tone": '"Emotional tone: neutral" line',
    "pov_line_has_mode_and_character": "POV is 'Zara Okafor', not the chapter POV "
    "'Milo Voss'",
    "secondary_has_context": "Prava: no '### Prava' in <secondary>",
    "story_position_line": "chapter 5 of 4",
    "no_duplicate_bible": "bible fields in CHARACTERS IN THIS BEAT: Description",
    "no_doubled_voice_label": 'doubled label "Voice: voice:"',
    "reduce_evidence_depth": "Milo Voss: 1 evidence/quote line(s) (< 3)",
}


def _rec(rid, purpose, prompt, answer="", created=100.0):
    return {
        "id": rid,
        "purpose": purpose,
        "created": created,
        "messages": [{"role": "user", "content": prompt}],
        "answer": answer,
    }


def _write(dirpath, rec):
    (dirpath / f"{rec['id']}.json").write_text(json.dumps(rec))


def test_every_check_fails_its_failing_fixture(tmp_path):
    for i, (purpose, prompt, answer) in enumerate(FAILING):
        _write(tmp_path, _rec(str(i), purpose, prompt, answer, created=float(i)))
    out = pc.grade(
        pc.load_archive(tmp_path),
        gold_lines=GOLD,
        other_lines=OTHER,
        sample_prose=SAMPLE,
        principals=PRINCIPALS,
        style_tone="Dry, deadpan",
        descriptions=[MILO_DESC],
    )
    assert sorted(REASONS) == sorted(pc.CHECKS)
    for check, reason in REASONS.items():
        reasons = [r for f in out[check]["failures"] for r in f["reasons"]]
        assert reason in reasons, (check, out[check])
    assert out["n_calls"] == len(FAILING)
    # W3-3: the planner's POV line is graded too (record 2 has none).
    pov = out["pov_line_has_mode_and_character"]
    assert pov["n"] == 3 and "2" in [f["id"] for f in pov["failures"]]


def test_prior_keeps_its_paragraphs_and_starts_a_sentence():
    para = " ".join(["He read the ledger line by line."] * 20)  # 140 words
    assert pc.check_prior(para) == []  # one paragraph needs no break
    assert pc.check_prior(f"{para}\n\n{para}") == []
    assert pc.check_prior(f"{para} {para}") == ["<prior> lost its paragraph breaks"]
    assert pc.check_prior("and then he left.") == ["<prior> starts mid-sentence"]
    assert pc.check_prior("“Read it,” she said.") == []


def test_a_neutral_tone_the_plan_chose_is_kept():
    """S7: only a blank tone is omitted; the outline beat's own "neutral" is
    the author's choice, not an injected default."""
    prompt = "Scene description: Intake: x\nEmotional tone: neutral"
    assert pc.check_no_neutral_tone(prompt, "neutral") == []
    assert pc.check_no_neutral_tone(prompt, "wry") == ['"Emotional tone: neutral" line']


def test_the_last_beat_needs_no_stop_instruction():
    middle = "<beats>\n→ 1. He arrives.\n2. She leaves.\n</beats>\nWrite beat 1."
    assert pc.check_beats_block(middle) == [
        'no "stop where beat N+1 begins" instruction'
    ]


def test_a_block_is_read_from_its_fence_not_a_mention_in_the_instructions():
    prompt = "Continue from everything in <prior>, begin:\n<prior>\nIt was.\n</prior>\n"
    assert pc.block(prompt, "prior") == "It was."


def test_a_plan_prompt_without_an_available_cast_fails_even_unmatched():
    index = pc.OutlineIndex({})
    no_line = "Scene description: Other: y\nThe scene's cast is exactly: Milo."
    assert pc.check_plan_cast_from_beat(no_line, index) == [
        'no "Available cast (not all need appear)" line, or it lists nobody'
    ]
    nobody = "Scene description: Other: y\nAvailable cast (not all need appear): (none)"
    assert pc.check_plan_cast_from_beat(nobody, index) == [
        'no "Available cast (not all need appear)" line, or it lists nobody'
    ]
    listed = "Scene description: Other: y\nAvailable cast (not all need appear): Milo"
    assert pc.check_plan_cast_from_beat(listed, index) is None  # reported, not failed


def test_short_voice_samples_are_reported(tmp_path):
    prompt = '### Milo Voss\nVoice samples (their own words):\n- "So!"\n- "Zara,"'
    _write(tmp_path, _rec("1", "dialogue", prompt))
    out = pc.grade(pc.load_archive(tmp_path), gold_lines=GOLD)
    assert out["voice_dialogue"]["short_samples"] == 2


def test_load_archive_drops_an_earlier_run_and_purpose_is_the_header(tmp_path):
    _write(tmp_path, _rec("0", "beat_prose", "an earlier run", created=1.0))
    _write(tmp_path, _rec("1", "outline:beats", "now", created=100.0))
    records = pc.load_archive(tmp_path, since=50.0)
    assert [r["id"] for r in records] == ["1"]
    assert pc.purpose(records[0]) == "outline:beats"
    assert pc.load_archive(tmp_path / "missing") == []
    assert pc.grade([])["score"] is None


def test_corpus_inputs_take_the_pov_from_the_ground_truth():
    from evals.steps import pipeline

    _, gt = pipeline.load_corpus("cel")
    kw = pc.corpus_inputs(gt, pipeline.load_style("cel"))
    assert kw["pov"] == "Milo Voss" and kw["style_tone"].startswith("Dry, deadpan")
    assert len(kw["principals"]) == 3 and len(kw["descriptions"]) == 3
