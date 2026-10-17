"""The prompt-contract grader asserts what the app sent the model, per purpose,
from the relay's answered/ archive (cycle 2026-10 eval plan §3.3).

Passing prompts come from the app itself (`test_prompt_roundtrip.py`); this
file keeps one FAILING fixture per check, so each check is shown to bite."""

import copy
import json
from pathlib import Path

import pytest

from app.core.prompt_markers import (
    BEAT_TO_WRITE_LABEL,
    ALSO_NAMED_LABEL,
    MOVEMENT_LABEL,
    OUTLINE_CHAPTERS_HEADER,
    SCENE_CHARACTERS_SECTION,
    TIGHTEN_CLAUSE,
    VOICE_SAMPLES_LABEL,
)
from app.llm.json_utils import JSON_OBJECT
from evals.graders import plot
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


@pytest.fixture(autouse=True)
def _signed_dir(tmp_path_factory, monkeypatch):
    """Every test reads signed placements from its own empty dir, never the
    committed evals/placements (D4)."""
    monkeypatch.setattr(plot, "PLACEMENTS", tmp_path_factory.mktemp("placements"))


def _sign(records, placed=None, signed_by="PM"):
    """A signed placement file for `records`' outline: the draft's reading,
    with `placed` ({event: chapter}) as the signer's corrections."""
    cells = plot.draft_placements(CEL["excerpt_events"], plot.outline_chapters(records))
    cells |= {
        e: {"chapter": c, "line": c and "signed"} for e, c in (placed or {}).items()
    }
    key = plot.outline_hash(records)
    doc = {"run": "t", "outline_hash": key, "signed_by": signed_by}
    doc |= {"signed_at": "2026-10-16", "placements": cells}
    (plot.PLACEMENTS / f"{key[:12]}.json").write_text(json.dumps(doc))
    return doc


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

TIGHTEN = "Tighten this paragraph: cut every redundant word."

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
    (  # C2, W4b2 0025: the revise's cast block is graded too
        "scene_revise",
        f"{SCENE_CHARACTERS_SECTION} — x\n### Milo Voss\n{MOVEMENT_LABEL}\n- {PARAS[0]}\n",
        "",
    ),
    ("outline:chapters", "", json.dumps([{"title": "Intake"}, {"title": "Drawer"}])),
    (
        "outline:beats",
        f"{OUTLINE_CHAPTERS_HEADER}\n→ 1. Intake\n\n"
        "Here are the chapters of act 1, in order:\n[]",
        "",
    ),
    # W4 record 0031: a redo shown one plan line and no <beats> (SPEC-41).
    (
        "scene_revise",
        "Beat 2 of this scene's plan: Prava slides an org chart across.\nRedo beat 2.",
        "",
    ),
    ("scene_revise", f"<instruction>\n{TIGHTEN}\n</instruction>", ""),  # wave 8
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
    "plan_sees_chapter": "no chapter-scenes block",
    "redo_sees_plan": "no <beats> block marking beat 2",
    "json_mode_where_json": "response_format null, not JSON_OBJECT",
    "evidence_verbatim": "not in the source: 'one line'",
    "tighten_clause": "no tighten clause",
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
    _sign(pc.load_archive(tmp_path))
    out = pc.grade(
        pc.load_archive(tmp_path),
        source="\n\n".join(PARAS),
        gold_lines=GOLD,
        other_lines=OTHER,
        sample_prose=SAMPLE,
        principals=PRINCIPALS,
        style_tone="Dry, deadpan",
        descriptions=[MILO_DESC],
        revise_rows=[{"id": "tighten", "kind": "tighten", "instruction": TIGHTEN}],
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


def test_redo_sees_plan_wants_the_redone_beat_marked():
    """(F) SPEC-41: the marked item is the beat the redo names."""
    wrong = "Beat 2 of this scene's plan: b\n<beats>\n→ 1. a\n2. b\n</beats>"
    assert pc.check_redo_sees_plan(wrong) == ["<beats> marks beat 1, not beat 2"]


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


@pytest.mark.parametrize(
    "outline, placed",
    [  # partition: chapter 1 on all five, by "partition" (W4, W5), "lanyard"
        # (W4b2, W5b) or "next desk" (W5d's "Read the Appendices")
        ("w4", [1, 1, 1, 1, 2, 2, 3]),
        # "Reply All" (ch 5) summons Milo to the corner office: that line places
        # the summons, so the meeting is "The Corner Office" (ch 6).
        ("w4b2", [1, 1, 2, None, 5, 6, 4]),
        # (F) W7-2 (b): "memory" no longer places W5's summons in ch 1
        ("w5", [1, 1, 2, 4, 2, 2, 2]),
        ("w5b", [1, 1, 2, None, 6, 6, 4]),
        ("w5d", [1, 1, 1, 1, 2, 2, 2]),
    ],
)
def test_the_draft_places_each_event_by_the_first_line_naming_it(outline, placed):
    events = ["intake", "partition", "bell", "drawer", "summons", "corner office"]
    events += ["discrepancy"]
    chapters = plot.outline_chapters([_outline(outline)])
    got = plot.draft_placements(CEL["excerpt_events"], chapters)
    assert [got[e]["chapter"] for e in events] == placed
    assert got["training"] == {"chapter": None, "line": None}  # a span, no terms
    assert all(c["line"] in sum(chapters, []) for c in got.values() if c["chapter"])


def test_an_event_whose_lines_are_all_taken_still_places():
    """C1: an unplaced event could never be later plot, so it would pass."""
    events = [{"event": e, "terms": ["drawer"]} for e in ("drawer", "column")]
    chapters = [[], ["The Column: Milo opens the drawer."]]
    got = plot.draft_placements(events, chapters)
    assert {e: c["chapter"] for e, c in got.items()} == {"drawer": 2, "column": 2}


@pytest.mark.parametrize(
    "line, chapter",
    [  # (F) W7-2 (b): W5's "memory" placed the summons (term "memo") in ch 1
        ("Retained: Milo has no memory of dying.", None),
        ("The Memo: Milo is summoned.", 1),
        ("Upstairs: Milo is Summoned.", 1),  # a term names its inflections
        ("The Numbers: a discrepancy surfaces.", 1),  # "discrepanc" is a stem
        ("Memorandum: Milo files.", None),
    ],
)
def test_the_draft_matches_terms_as_words(line, chapter):
    events = [{"event": "summons", "terms": ["memo", "summon", "discrepanc"]}]
    assert plot.draft_placements(events, [[line]])["summons"]["chapter"] == chapter


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
    _sign(pc.load_archive(tmp_path))
    out = pc.grade(pc.load_archive(tmp_path), **PLOT_GOLD)["samples_not_plot"]
    got = [r.split(": ", 1)[1] for f in out["failures"] for r in f["reasons"]]
    assert (out["n"], got, out["later_plot_slots"]) == (1, reasons, later)
    assert out["classified"] == sum(len(v) + len(m) for v, m in blocks.values())


def test_a_run_that_shows_no_sample_is_vacuous(tmp_path):
    """(F) W5b's shape: every block holds only its heading. Nothing measured
    voice presence, so the empty cards passed; now voice_coverage is 0 and
    samples_not_plot's 1.0 is reported vacuous (classified == 0)."""
    _write(tmp_path, _outline("w5b"))
    _sign(pc.load_archive(tmp_path))
    cast = {"Milo Voss": ([], []), "Zara Okafor": ([], [])}
    for i in range(3):
        _write(tmp_path, _rec(f"b{i}", "beat_prose", _beat(1, cast)))
    _write(tmp_path, _rec("v", "beat_prose", _beat(1, {"Zara Okafor": ([CLOSE], [])})))
    out = pc.grade(pc.load_archive(tmp_path), principals=PRINCIPALS, **PLOT_GOLD)
    assert out["voice_dialogue"]["voice_coverage"] == round(1 / 7, 4)
    assert (out["samples_not_plot"]["rate"], out["samples_not_plot"]["vacuous"]) == (
        1.0,
        False,
    )
    (tmp_path / "v.json").unlink()
    out = pc.grade(pc.load_archive(tmp_path), principals=PRINCIPALS, **PLOT_GOLD)
    assert out["voice_dialogue"]["voice_coverage"] == 0
    assert out["samples_not_plot"]["rate"] == 1.0
    assert (
        out["samples_not_plot"]["classified"],
        out["samples_not_plot"]["vacuous"],
    ) == (
        0,
        True,
    )


# --- wave 6 (SPEC-51, SPEC-52) ---------------------------------------------------

W5 = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "w5_archives.json").read_text()
)


def test_unplaced_events_are_the_events_signed_null(tmp_path):
    """(F) D4: W5d's draft places the partition by "next desk"; the signer
    read it as not planned, so it is listed. A draft miss alone is never
    unplaced: it shows only as a disagreement cell."""
    _write(tmp_path, _outline("w5d"))
    records = pc.load_archive(tmp_path)
    _sign(records, {"partition": None})
    out = pc.grade(records, **PLOT_GOLD)
    assert out["samples_not_plot"]["unplaced_events"] == ["partition"]
    assert out["placement_disagreements"] == [
        {"event": "partition", "draft": 1, "signed": None}
    ]


def test_without_a_signed_file_every_plot_value_is_null(tmp_path):
    """(F) S-4: the draft places W4b2's summons in chapter 5, so its chapter-1
    voice sample would be later plot; unsigned, nothing is graded from it.
    A draft file (no signer) is not a signature, and the hash names the run."""
    _write(tmp_path, _outline("w4b2"))
    _write(tmp_path, _rec("b", "beat_prose", _beat(1, {"Milo Voss": ([SUMMONS], [])})))
    records = pc.load_archive(tmp_path)
    for signer in (None, "PM"):
        _sign(records, signed_by=signer)
        out = pc.grade(records, principals=PRINCIPALS, **PLOT_GOLD)
        if signer is None:
            assert out["placement_signed"] is False
            assert out["outline_hash"] == plot.outline_hash(records)
            assert (out["samples_not_plot"], out["movement"]) == (None, None)
            assert out["placement_disagreements"] is None
    assert out["placement_signed"] is True and out["placement_disagreements"] == []
    assert out["samples_not_plot"]["later_plot_slots"] == 1


def test_a_signed_file_missing_a_cell_is_an_error(tmp_path):
    _write(tmp_path, _outline("w5d"))
    records = pc.load_archive(tmp_path)
    doc = _sign(records)  # the fixture's fresh dir holds only this file
    del doc["placements"]["drawer"]
    (plot.PLACEMENTS / f"{doc['outline_hash'][:12]}.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="drawer"):
        pc.grade(records, **PLOT_GOLD)


def test_two_signed_files_for_one_outline_are_an_error(tmp_path):
    _write(tmp_path, _outline("w5d"))
    records = pc.load_archive(tmp_path)
    doc = _sign(records)
    (plot.PLACEMENTS / "again.json").write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="all sign"):
        pc.grade(records, **PLOT_GOLD)


def _plan_record(plan: list[list[str]]) -> dict:
    """An outline:beats record holding `plan`, each chapter's "title: summary"."""
    chapters = [
        {"children": [dict(zip(("title", "summary"), ln.split(": ", 1))) for ln in ch]}
        for ch in plan
    ]
    prompt = "Here are the chapters of act 1, in order:\n[]"
    return _rec("o", "outline:beats", prompt, json.dumps(chapters), created=0.0)


def test_movement_lists_every_ineligible_principal_on_the_w5_stores(tmp_path):
    """(F) D3a on W5d's and W5bd's stores and plans: in chapter 1 every store
    line is gold plot or past the gold tail, so no principal is eligible. Each
    is listed with its gold paragraphs and, under D4 (wave 8), the gate fails:
    the principals cast in chapter 1 are not eligible."""
    for row in W5["movement"]["rows"]:
        (run := tmp_path / row["run"]).mkdir()
        _write(run, _plan_record(row["plan"]))
        blocks = {who: ([], []) for who in row["store"]}
        _write(run, _rec("b", "beat_prose", _beat(row["chapter"], blocks)))
        _sign(pc.load_archive(run))
        out = pc.grade(
            pc.load_archive(run),
            principals=PRINCIPALS,
            movement_store=row["store"],
            **PLOT_GOLD,
        )
        assert out["samples_not_plot"]["placed"] == row["placed"]
        assert out["movement"] == {
            **{"n": 0, "passed": 0, "rate": None, "failures": []},
            **{"ineligible": row["ineligible"], "ok": False, "not_cast": []},
            "not_eligible": sorted(row["store"]),
        }


def test_an_eligible_principal_with_an_empty_block_fails(tmp_path):
    """(F) D3a: in W4's chapter 3 Zara's look (29) is manner (no gold tail),
    so she is eligible and her empty block fails; Milo's store is all plot,
    so he is listed, not counted. Without the store nothing is eligible and
    nobody has a gold reason: that empty result fails too."""
    _write(tmp_path, _outline("w4"))
    shapes = [
        {"Zara Okafor": ([], [PARAS[29]]), "Milo Voss": ([], [])},
        {"Zara Okafor": ([], [])},
    ]
    for i, blocks in enumerate(shapes):
        _write(tmp_path, _rec(f"b{i}", "beat_prose", _beat(3, blocks), created=1.0 + i))
    _sign(pc.load_archive(tmp_path))
    store = {"Zara Okafor": [PARAS[29]], "Milo Voss": [PARAS[0], PARAS[13]]}
    grade = lambda **kw: pc.grade(  # noqa: E731
        pc.load_archive(tmp_path), principals=PRINCIPALS, **PLOT_GOLD, **kw
    )["movement"]
    move = grade(movement_store=store)
    assert (move["n"], move["passed"], move["ok"]) == (2, 1, False)
    assert move["failures"] == [
        {"id": "b1", "reasons": ["Zara Okafor: eligible, no movement line"]}
    ]
    assert move["ineligible"] == {"Milo Voss": [0, 13]}
    assert (grade(movement_store={})["n"], grade(movement_store={})["ok"]) == (0, False)
    move = grade(movement_store={"Milo Voss": store["Milo Voss"]})  # (R) S-5
    assert move["ineligible"]["Zara Okafor"] == ["none indexed"]
    # D4: nobody is cast in chapter 1, but no eligible block is no gate (n = 0).
    assert (move["n"], move["ok"], move["not_cast"]) == (
        0,
        False,
        ["Edric Thane", "Milo Voss", "Zara Okafor"],
    )


def test_json_mode_where_json_reads_the_recorded_response_format(tmp_path):
    """(F) W6-6: W5d sent its outline, plan_beats and continuity calls with
    response_format null; (R) its extraction calls carry JSON_OBJECT. A prose
    call is not a JSON call and is not counted."""
    formats = {
        "outline:skeleton": None,
        "outline:beats": None,
        "plan_beats": None,
        "continuity": None,
        "extraction_map": JSON_OBJECT,
        "extraction_reduce": JSON_OBJECT,
        "beat_prose": None,
    }
    for i, (purpose, fmt) in enumerate(formats.items()):
        _write(tmp_path, {**_rec(str(i), purpose, "x", "{}"), "response_format": fmt})
    out = pc.grade(pc.load_archive(tmp_path))["json_mode_where_json"]
    assert (out["n"], out["passed"]) == (6, 2)
    assert [f["id"] for f in out["failures"]] == ["0", "1", "2", "3"]


# The three archived hindsight sentences: W4b2 0010, W5bc 0025, W5d 0022.
HINDSIGHT = [
    "It was a Tuesday, which Milo would later feel was the least he deserved.",
    "Milo had not noticed her at first, which he would later think said something "
    "about her, or about him.",
    "Milo looked at it, warmed by her kindness, and only much later would it occur "
    "to him to ask how she had known his name.",
]


def test_hindsight_is_reported_per_answer(tmp_path):
    """(F) W6-3, reported: each archived prolepsis is listed with its answer;
    (R) a plain "later" is not hindsight."""
    for i, line in enumerate([*HINDSIGHT, "He would sign it later."]):
        _write(tmp_path, _rec(f"h{i}", "beat_prose", "x", f"He sat. {line} He rose."))
    out = pc.grade(pc.load_archive(tmp_path))["hindsight"]
    assert out == [{"id": f"h{i}", "sentence": s} for i, s in enumerate(HINDSIGHT)]


def test_scripted_slots_are_reported_from_the_fixture(tmp_path):
    """(F) D4, reported not gated: the folded 6-run hit of each archived slot
    is the table's `drop`; each slot also reports its content-word share."""
    for i, row in enumerate(W5["scripted"]["rows"]):
        prompt = (
            f"Scene description: {row['brief']}\n"
            + _beat(1, {row["who"]: ([row["sample"]], [])})
            + f"{BEAT_TO_WRITE_LABEL}: {row['beat']}"
        )
        _write(tmp_path, _rec(f"s{i}", "beat_prose", prompt, created=float(i)))
    slots = pc.grade(pc.load_archive(tmp_path))["scripted_slots"]
    assert [s["folded6"] for s in slots] == [r["drop"] for r in W5["scripted"]["rows"]]
    assert all(0 <= s["content_share"] <= 1 for s in slots)


# --- wave 7 (SPEC-54, SPEC-58) ---------------------------------------------------

W6_ARCHIVES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "w6_archives.json").read_text()
)


def test_evidence_verbatim_on_the_archived_reduce_prompts(tmp_path):
    """(F) W7-4 (b): the reduce prompts' evidence, rebuilt from the fixture's
    quotes (never its labels), against the manuscript the eval uploads.
    W6b's "Mr. Voss, do sit." joins two of Edric's sentences across "the Lich
    said", so it is not the author's sentence."""
    from app.core.prompt_markers import EVIDENCE_ITEM, EVIDENCE_LABEL, NAME_HEADING

    requests: dict[tuple, dict[str, list[str]]] = {}
    for row in W6_ARCHIVES["evidence"]["rows"]:
        who = requests.setdefault((row["run"], row["request"]), {})
        item = EVIDENCE_ITEM.format(part=row["part"], quote=row["quote"])
        who.setdefault(row["character"], []).append(item)
    for i, ((run, _), cast) in enumerate(requests.items()):
        prompt = "".join(
            f"{NAME_HEADING}{who}\n{EVIDENCE_LABEL}\n" + "\n".join(items) + "\n"
            for who, items in cast.items()
        )
        (tmp_path / run).mkdir(exist_ok=True)
        _write(tmp_path / run, _rec(str(i), "extraction_reduce", prompt))
    source = pc.corpus_inputs(CEL, None)["source"]
    got = {}
    for run in ("W5d", "W5bd", "W6", "W6b"):
        out = pc.grade(pc.load_archive(tmp_path / run), source=source)
        got[run] = out["evidence_verbatim"]["lines"]
        if run == "W6b":
            reasons = [
                r for f in out["evidence_verbatim"]["failures"] for r in f["reasons"]
            ]
            assert "not in the source: '\"Mr. Voss, do sit.\"'" in reasons
    assert {r: (g["verbatim"], g["n"]) for r, g in got.items()} == {
        "W5d": (18, 28),
        "W5bd": (12, 12),
        "W6": (22, 28),
        "W6b": (8, 12),
    }


def test_evidence_verbatim_normalises_marks_and_skips_the_stand_in():
    source = pc.verbatim_text("“Sit,” said Edric.\n  Milo   sat down.")
    prompt = (
        '- (part 1) "Sit," said Edric.\n- (part 1) Milo sat down.\n'
        "- (part 2) narrates part 2\n- (part 1) Milo sat.\n"
        "- (part 1) no verbatim quote in part 1"  # wave 8: either marker
    )
    assert pc.check_evidence_verbatim(prompt, source) == (
        ["not in the source: 'Milo sat.'"],
        3,
        2,
    )


@pytest.mark.parametrize(
    "answer, counted",
    [  # (F) D9: the clean control's findings are empty one level down
        ('{"findings": []}', False),
        ('{"items": [], "satisfied": ""}', False),
        ('{"findings": [{"quote": "x"}]}', True),
        ('{"findings": [[]]}', True),  # nothing deeper than one level
        ("[]", False),
        ("Milo signed.", True),
    ],
)
def test_replayed_skips_all_empty_json(answer, counted):
    rec = _rec("a", "continuity", "p", answer)
    assert ("continuity" in plot.replayed([rec], [rec])) is counted


# --- wave 8 day-0: the regrade contract (tpm-plan-wave8 §1(a)) -------------------

W7_ARCHIVES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "w7_archives.json").read_text()
)


def _w7_run(run, store) -> Path:
    """W7 as an eval-out run dir: its outline, the nine beat prompts that cast
    Milo (the movement each owner was shown) and a report holding `store`."""
    fx = W7_ARCHIVES["w7_movement"]
    answered = run / "answered-archive" / "model" / "answered"
    answered.mkdir(parents=True)
    _write(answered, _plan_record(fx["plan"]))
    for i, b in enumerate(fx["beats"]):
        blocks = {who: ([], moves) for who, moves in b["movement"].items()}
        _write(
            answered,
            _rec(
                b["request"], "beat_prose", _beat(b["chapter"], blocks), created=1.0 + i
            ),
        )
    report = {"book": "cel", "steps": {"prose": {"voice": {"movement": store}}}}
    (run / "eval-report-cel.json").write_text(json.dumps(report))
    signed = Path(pc.__file__).parents[1] / "placements" / "W7.json"
    (plot.PLACEMENTS / "W7.json").write_text(signed.read_text())
    return run


def test_the_regrade_reads_the_runs_own_store(tmp_path, capsys):
    """(F) W7 was shown no movement line for Milo, but its store holds eight:
    the regrade CLI reads the report's store, so Milo is eligible (and empty)
    in all nine blocks, never "none indexed"."""
    run = _w7_run(tmp_path / "w7", W7_ARCHIVES["w7_movement"]["store"])
    plot.main(["regrade", str(run)])
    move = json.loads(capsys.readouterr().out)["movement"]
    assert (move["n"], move["passed"], move["ineligible"]) == (9, 0, {})
    assert move["ok"] is False and move["not_cast"] == ["Edric Thane"]  # D4
    assert {r for f in move["failures"] for r in f["reasons"]} == {
        "Milo Voss: eligible, no movement line"
    }


def test_a_contradicted_none_indexed_is_an_error(tmp_path, monkeypatch):
    """(F) A grade that lists Milo "none indexed" while the run's store holds
    his lines raises; it is never a result."""
    run = _w7_run(tmp_path / "w7", W7_ARCHIVES["w7_movement"]["store"])
    movement = {"ineligible": {"Milo Voss": [pc.NONE_INDEXED]}}
    monkeypatch.setattr(pc, "grade", lambda *a, **kw: {"movement": movement})
    with pytest.raises(ValueError, match="Milo Voss: read as none indexed"):
        plot.regrade(run)


def test_without_a_store_movement_is_null(tmp_path):
    """(F) No fallback to the lines the prompts show: without the run's store
    the movement result is null, with its reason."""
    run = _w7_run(tmp_path / "w7", None)
    out = plot.regrade(run)
    assert (out["movement"], out["movement_reason"]) == (None, "no movement_store")
    assert out["placement_signed"] is True


# --- wave 8 (tpm-plan-wave8 §3 E1) ------------------------------------------------


@pytest.mark.parametrize("row", W7_ARCHIVES["tighten"]["rows"], ids=lambda r: r["run"])
def test_tighten_cut_and_content_kept_on_the_eight_targets(row):
    """(F) W8-1 (c): the grader's own stems reproduce the PM's counts (cut to
    0.01, kept to 0.05; the PM's function-word list is not the grader's)."""
    target, answer = row["target"], row["answer"]
    assert abs(pc.tighten_cut(target, answer) - row["words_cut"]) <= 0.01 + 1e-9
    assert abs(pc.content_share(target, answer) - row["content_kept"]) <= 0.05 + 1e-9
    if row["run"] == "W7b":  # the worst draw: RA 0.62
        assert pc.content_share(target, answer) == 0.65


def test_tighten_clause_eligibility_is_the_report_rows_kind(tmp_path):
    """(F) W8-1 (b): a scene_revise prompt is graded when the revise step's row
    for its instruction says `kind: "tighten"`, never by the instruction's
    words: "Trim the ending" without a tighten row is not eligible."""
    for i, ask in enumerate((TIGHTEN, "Trim the ending.")):
        clause = TIGHTEN_CLAUSE if i == 0 else ""
        prompt = f"<instruction>\n{ask}\n</instruction>\n{clause}"
        _write(tmp_path, _rec(str(i), "scene_revise", prompt, created=float(i)))
    rows = [{"id": "tighten", "kind": "tighten", "instruction": TIGHTEN}]
    graded = lambda r: pc.grade(pc.load_archive(tmp_path), revise_rows=r)  # noqa: E731
    assert graded(rows)["tighten_clause"] | {"failures": []} == {
        **{"n": 1, "passed": 1, "rate": 1.0, "failures": []}
    }
    assert graded([{**rows[0], "kind": None}])["tighten_clause"]["n"] == 0
    assert graded(None)["tighten_clause"]["n"] == 0
    # A short scene's row asks nothing (no instruction): no eligible prompt.
    assert graded([{"kind": "tighten", "error": "short"}])["tighten_clause"]["n"] == 0
    assert "tighten_clause" in graded(None)["empty_checks"]


def test_plan_quotes_source_reproduces_10_of_85(tmp_path):
    """(F) W8-3 (b), S-6: source dialogue is cel's gold lines plus every quoted
    span of the excerpt; a beat line counts when it shares a pronoun-folded
    6-word run with it. The 85 plan_beats lines of W5d to W7br give the
    baseline's 10, line for line."""
    fx = W7_ARCHIVES["plan_beats"]
    answers: dict[str, list[str]] = {}
    for line in fx["lines"]:
        answers.setdefault(f"{line['run']}/{line['request']}", []).append(line["line"])
    for i, (rid, lines) in enumerate(answers.items()):
        items = json.dumps({"items": [{"description": d} for d in lines]})
        _write(tmp_path, _rec(rid.replace("/", "_"), "plan_beats", "p", items, i))
    inputs = pc.book_inputs("cel")
    out = pc.grade(
        pc.load_archive(tmp_path),
        gold_lines=inputs["gold_lines"],
        excerpt=inputs["excerpt"],
    )["plan_quotes_source"]
    assert (out["n"], out["quoting"]) == (85, 10)
    assert sorted(i.replace("_", "/", 1) for i in out["ids"]) == sorted(
        fx["quotes_source"]
    )


def test_movement_ok_is_the_gate(tmp_path, monkeypatch):
    """(F) D4: ok needs every principal cast in a chapter-1 block eligible and
    >= 0.8 of the eligible blocks moving. W7 with Milo's lines shown reads true
    (W7 itself reads false, above). Edric is cast in no block: listed
    `not cast`, not required."""
    fx = copy.deepcopy(W7_ARCHIVES["w7_movement"])
    for b in fx["beats"]:  # W7r's shape: Milo's stored lines reach the prose
        b["movement"] = {**b["movement"], "Milo Voss": fx["store"]["Milo Voss"][:1]}
    monkeypatch.setitem(W7_ARCHIVES, "w7_movement", fx)
    out = plot.regrade(_w7_run(tmp_path / "w7r", fx["store"]))["movement"]
    assert (out["ok"], out["n"], out["passed"], out["not_cast"]) == (
        True,
        9,
        9,
        ["Edric Thane"],
    )
