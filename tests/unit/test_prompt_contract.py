"""The prompt-contract grader asserts what the app sent the model, per purpose,
from the relay's answered/ archive (cycle 2026-10 eval plan §3.3).

Passing prompts come from the app itself (`test_prompt_roundtrip.py`); this
file keeps one FAILING fixture per check, so each check is shown to bite."""

import json
from pathlib import Path

import pytest

from app.core.prompt_markers import (
    ALSO_NAMED_LABEL,
    MOVEMENT_LABEL,
    OUTLINE_CHAPTERS_HEADER,
    VOICE_SAMPLES_LABEL,
)
from evals.graders import prompt_contract as pc
from evals.steps import pipeline

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
    "children": [
        {"title": "Intake", "summary": "x", "people": ["Milo", "Prava"]},
        {"title": "Retained", "summary": "Milo is retained.", "people": ["Milo"]},
    ],
}
CEL = pipeline.load_corpus("cel")[1]
PARAS = pipeline.load_excerpts("cel").split("\n\n")
PLOT_GOLD = {"excerpt_events": CEL["excerpt_events"], "excerpt": "\n\n".join(PARAS)}

# Run-W3 record 1792100839-0017: Milo's sample, and the answer that lifts it.
LIFT_PROMPT = (
    f"CHARACTERS IN THIS BEAT\n### Milo Voss\n{VOICE_SAMPLES_LABEL}\n"
    '- "Totally, totally. And is there a dashboard, or a tracker, or some kind of '
    "shared doc where we log throughput? I only ask because in my last role I built "
    "a tracker that cut our cycle time by eleven percent, which, honestly, was the "
    'best week of my life."\nBEAT TO WRITE: x'
)
LIFT_ANSWER = (
    '"And is there a dashboard?" he asked. "Or a tracker, or some kind of shared '
    "doc where we log throughput? I only ask because in my last role I built a "
    "tracker, just a spreadsheet really, color-coded, with a little macro, and it "
    "cut our cycle time by eleven percent. Eleven. Which, honestly, was the best "
    'week of my life."'
)

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
    (
        "beat_prose",
        f"CHARACTERS IN THIS BEAT\n### Milo Voss\n{MOVEMENT_LABEL}\n"
        "- Milo carried the memo up to Edric.\nBEAT TO WRITE: x",
        "",
    ),
    ("beat_prose", LIFT_PROMPT, LIFT_ANSWER),
    (
        "scene_revise",
        f"CHARACTERS IN THIS BEAT\n### Milo Voss\n{MOVEMENT_LABEL}\n- {PARAS[0]}\n",
        "",
    ),
    ("outline:chapters", "", json.dumps([{"title": "Intake"}, {"title": "Drawer"}])),
    (
        "outline:beats",
        f"{OUTLINE_CHAPTERS_HEADER}\n→ 1. Intake\n\n"
        "Here are the chapters of act 1, in order:\n[]",
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
    "movement_names_in_scene": "Milo Voss: names Edric Thane: "
    "'Milo carried the memo up to Edric.'",
    "sample_lift": "20 word 8-grams of its samples: 'a tracker or some kind of shared doc'",
    "beats_see_whole_outline": "'Drawer' is not in the whole-outline block",
    "samples_not_plot": "Milo Voss: paragraph 0: narrates intake (ch 1)",
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
        **PLOT_GOLD,
    )
    assert sorted(REASONS) == sorted(pc.CHECKS)
    for check, reason in REASONS.items():
        reasons = [r for f in out[check]["failures"] for r in f["reasons"]]
        assert reason in reasons, (check, out[check])
    assert out["n_calls"] == len(FAILING)
    lift = out["sample_lift"]  # the worst beat, and the share of beats that lift
    assert (lift["max_shared"], lift["share_of_beats"]) == (20, 0.25)
    # W3-3: the planner's POV line is graded too (record 2 has none).
    pov = out["pov_line_has_mode_and_character"]
    assert "2" in [f["id"] for f in pov["failures"]]


def test_prior_keeps_its_paragraphs_and_starts_a_sentence():
    para = " ".join(["He read the ledger line by line."] * 20)  # 140 words
    none: set[str] = set()
    assert pc.check_prior(para, none) == []  # one paragraph needs no break
    assert pc.check_prior(f"{para}\n\n{para}", none) == []
    assert pc.check_prior(f"{para} {para}", none) == [
        "<prior> lost its paragraph breaks"
    ]
    assert pc.check_prior("and then he left.", none) == ["<prior> starts mid-sentence"]
    assert pc.check_prior("“Read it,” she said.", none) == []


# W3b: scene 1 opens in medias res, mid-sentence by design.
OPENING = (
    '"—and honestly, this could have been an email," Milo said, and opened his '
    "eyes.\n\nHe was not in the conference room."
)


def _beat_prompt(n: int, prior: str | None) -> str:
    fence = f"<prior>\n{prior}\n</prior>\n" if prior else "This beat opens the scene.\n"
    return (
        f"The scene's beats, in order (→ marks beat {n}, the one you are writing):\n"
        f"<beats>\n→ {n}. x\n</beats>\n{fence}"
    )


def _prior_failures(tmp_path, records) -> list[str]:
    tmp_path.mkdir()
    for i, (prompt, answer) in enumerate(records):
        _write(tmp_path, _rec(str(i), "beat_prose", prompt, answer, created=float(i)))
    out = pc.grade(pc.load_archive(tmp_path))
    return [f["id"] for f in out["prior_paragraphs"]["failures"]]


def test_a_prior_that_is_the_scenes_own_opening_passes(tmp_path):
    """M15/S3: a scene that opens mid-sentence is the author's choice; the
    beat-2 <prior> that starts with it is that opening, not a cut tail."""
    scene_1 = [(_beat_prompt(1, None), OPENING), (_beat_prompt(2, OPENING), "")]
    assert _prior_failures(tmp_path / "w3b", scene_1) == []
    # Scene 2's beat 1 HAS a <prior> (scene 1's tail); its opening counts too.
    tail = "Milo signed the form."
    scene_2 = [
        (_beat_prompt(1, tail), "—and honestly, nobody reads these."),
        (_beat_prompt(2, "—and honestly, nobody reads these."), ""),
    ]
    assert _prior_failures(tmp_path / "s2", scene_2) == []


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


@pytest.mark.parametrize("brief", ["Zara audits.", "Zara audits Halvard Bell."])
def test_movement_counts_the_scene_and_reports_coverage(tmp_path, brief):
    """SPEC-30: a secondary on the Also-named line, or a principal the scene
    names, may appear; Bell may not, even when the brief names him (NB-2).
    Coverage counts principal blocks with a movement line (B-2: an emptied
    block must not score 1.0 unseen)."""
    prompt = (
        f"<scene>\nScene description: {brief}\n{ALSO_NAMED_LABEL}: Prava\n"
        "</scene>\nCHARACTERS IN THIS BEAT\n"
        f"### Milo Voss\n{MOVEMENT_LABEL}\n- Milo nodded at Zara and Prava.\n"
        f"- Milo closed the Halvard Bell file.\n### Zara Okafor\n{MOVEMENT_LABEL}\n"
        "### Edric Thane\nBEAT TO WRITE: x"
    )
    _write(tmp_path, _rec("1", "beat_prose", prompt))
    out = pc.grade(
        pc.load_archive(tmp_path),
        principals=PRINCIPALS,
        secondary_cast=["Prava", "Old Fen", "Halvard Bell"],
    )["movement_names_in_scene"]
    assert [r for f in out["failures"] for r in f["reasons"]] == [
        "Milo Voss: names Halvard Bell: 'Milo closed the Halvard Bell file.'"
    ]
    assert out["movement_coverage"] == round(1 / 3, 4)
    assert out["movement_lines"] == {"Milo Voss": 2, "Zara Okafor": 0, "Edric Thane": 0}


def _outline(name: str) -> dict:
    """One outline:beats record holding an archived outline's six chapters."""
    plan = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "w4_outlines.json").read_text()
    )[name]
    chapters = [
        {"title": f"Chapter {n}", "children": [s for s in plan if s["chapter"] == n]}
        for n in range(1, 7)
    ]
    prompt = "Here are the chapters of act 1, in order:\n[]"
    return _rec("o", "outline:beats", prompt, json.dumps(chapters), created=0.0)


def _beat(chapter: int, blocks: dict[str, tuple[list, list]]) -> str:
    """A beat prompt: per owner, (voice samples, movement samples)."""
    body = "".join(
        f"### {who}\n{VOICE_SAMPLES_LABEL}\n"
        + "".join(f'- "{v}"\n' for v in voice)
        + f"{MOVEMENT_LABEL}\n"
        + "".join(f"- {m}\n" for m in moves)
        for who, (voice, moves) in blocks.items()
    )
    return f"This scene is in chapter {chapter} of 6.\nCHARACTERS IN THIS BEAT\n{body}"


SUMMONS = "He wants to meet me. Personally. Is that normal?"
CLOSE = "Close the accounts in your queue. Don't open the ones that aren't."


@pytest.mark.parametrize(
    "outline, blocks, reasons, later",
    [  # (F) W4 "Retained", beat 2 (the org-chart beat): the intake narrated
        (
            "w4",
            {"Milo Voss": ([], [PARAS[0]])},
            ["paragraph 0: narrates intake (ch 1)"],
            0,
        ),
        # (F) W4b2 0021: the intake again; the drawer is not planned in W4b2
        (
            "w4b2",
            {"Milo Voss": ([], [PARAS[0], PARAS[13]])},
            ["paragraph 0: narrates intake (ch 1)"],
            0,
        ),
        # (F) the summons line in a chapter-1 scene of W4b2: chapter-5 plot
        (
            "w4b2",
            {"Milo Voss": ([SUMMONS], [])},
            ["paragraph 15: voice sample is ch-5 plot in ch 1"],
            1,
        ),
        # (R) Zara's look at the column and her training lines are manner
        ("w4b2", {"Zara Okafor": ([CLOSE], [PARAS[29]])}, [], 0),
    ],
)
def test_samples_not_plot_grades_against_the_gold(
    tmp_path, outline, blocks, reasons, later
):
    for rec in (_outline(outline), _rec("b", "beat_prose", _beat(1, blocks))):
        _write(tmp_path, rec)
    out = pc.grade(pc.load_archive(tmp_path), **PLOT_GOLD)["samples_not_plot"]
    got = [r.split(": ", 1)[1] for f in out["failures"] for r in f["reasons"]]
    assert (out["n"], got, out["later_plot_slots"]) == (1, reasons, later)
    assert out["classified"] == sum(len(v) + len(m) for v, m in blocks.values())


def test_blocks_only_plot_could_fill_are_emptied_by_rule(tmp_path):
    """D6 on W4b's block shape: Milo is only ever shown the intake and the
    drawer, both planned, so his five empty blocks (four in "The Locked
    Drawer", one in "The Bell Account") leave coverage; Zara's look is not
    plot, so her empty block counts against it."""
    milo, zara = ([], [PARAS[13]]), ([], [PARAS[29]])
    shapes = [{"Milo Voss": ([], [PARAS[0]])}] * 4 + [
        {"Milo Voss": milo, "Zara Okafor": zara}
    ] * 4
    shapes += [{"Milo Voss": milo, "Zara Okafor": ([], [])}, {"Milo Voss": milo}]
    shapes += [{"Milo Voss": ([], []), "Zara Okafor": zara}, {"Milo Voss": milo}]
    shapes += [{"Milo Voss": ([], [])}] * 4
    _write(tmp_path, _outline("w4"))
    for i, blocks in enumerate(shapes):
        _write(tmp_path, _rec(f"b{i}", "beat_prose", _beat(1, blocks), created=1.0 + i))
    out = pc.grade(pc.load_archive(tmp_path), principals=PRINCIPALS, **PLOT_GOLD)
    move = out["movement_names_in_scene"]
    assert move["emptied_by_rule"] == {
        "Milo Voss": 5,
        "Zara Okafor": 0,
        "Edric Thane": 0,
    }
    assert move["movement_coverage"] == round(16 / 17, 4)
