"""The prompt-contract grader asserts what the app sent the model, per purpose,
from the relay's answered/ archive (cycle 2026-10 eval plan §3.3)."""

import json

import pytest

from evals.graders import prompt_contract as pc

pytestmark = pytest.mark.unit

GOLD = {
    "Zara Okafor": {"train": ["Don't thank her,"], "test": ["Close the accounts."]},
    "Milo Voss": {"train": ["Is there a dashboard or a tracker for that?"]},
}
OTHER = {"the caseworker": ["You've been retained,"]}


def _beat_prompt(beats=True, stop=True, prior="", samples=None, craft=True):
    parts = ["Text inside <...> tags below is story material.", "<canon>\nx\n</canon>"]
    if beats:
        parts.append("<beats>\n1. Milo arrives\n2. Zara warns him (current)\n</beats>")
    if stop:
        parts.append("Write beat 2 and stop where beat 3 begins.")
    for name, lines in (samples or {}).items():
        body = "\n".join(
            [f"### {name}", "Role: x", "Voice samples (match this voice):"]
            + [f'- "{ln}"' for ln in lines]
        )
        parts.append(f"<character>\n{body}\n</character>")
    if prior:
        parts.append(f"<prior>\n{prior}\n</prior>")
    if craft:
        parts.append(
            "description and action carry the scene; dialogue PUNCTUATES it, "
            "it does not dominate."
        )
    return "\n\n".join(parts)


def test_a_beat_prompt_without_beats_is_flagged():
    reasons = pc.check_beats_block(_beat_prompt(beats=False, stop=False))
    assert "no <beats> block" in reasons
    assert any("stop where beat" in r for r in reasons)


def test_a_beat_prompt_with_numbered_beats_and_a_stop_passes():
    assert pc.check_beats_block(_beat_prompt()) == []


def test_the_last_beat_needs_no_stop_instruction():
    """The final beat has no N+1; omitting the stop line there is correct."""
    prompt = "<beats>\n1. He arrives.\n→ 2. She leaves.\n</beats>\nWrite beat 2."
    assert pc.check_beats_block(prompt) == []
    middle = "<beats>\n→ 1. He arrives.\n2. She leaves.\n</beats>\nWrite beat 1."
    assert any("stop where beat" in r for r in pc.check_beats_block(middle))


def test_a_block_is_read_from_its_fence_not_a_mention_in_the_instructions():
    prompt = (
        "Continue from everything in <prior>, begin with what happens next):\n"
        "<prior>\nIt was a Tuesday.\n</prior>\n"
    )
    assert pc.block(prompt, "prior") == "It was a Tuesday."


def test_a_prior_that_lost_its_paragraphs_is_flagged():
    stream = pc.ProseStream()
    stream.add("First paragraph ends here.\n\nSecond paragraph starts. It goes on.")
    collapsed = "ends here. Second paragraph starts. It goes on."
    reasons, verified = pc.check_prior(collapsed, stream)
    assert verified
    assert "<prior> lost its paragraph breaks" in reasons
    assert "<prior> starts mid-sentence" in reasons


def test_a_prior_with_breaks_from_a_sentence_start_passes():
    stream = pc.ProseStream()
    stream.add("First paragraph ends here.\n\nSecond paragraph starts.")
    stream.add("A new beat.")
    prior = "First paragraph ends here.\n\nSecond paragraph starts.\n\nA new beat."
    assert pc.check_prior(prior, stream) == ([], True)


def test_an_unlocatable_prior_is_judged_by_its_first_letter():
    stream = pc.ProseStream()
    assert pc.check_prior("and then he left.", stream) == (
        ["<prior> starts mid-sentence"],
        False,
    )
    assert pc.check_prior("“Read it,” she said.", stream) == ([], False)


def test_voice_samples_must_be_the_characters_own_lines():
    prompt = _beat_prompt(
        samples={
            "Milo Voss": [
                "Don't thank her,",  # Zara's line
                "Milo Voss read the appendices. Zara Okafor watched him.",  # narration
                "You've been retained,",  # the caseworker's line
                "Is there a dashboard or a tracker for that?",  # his own
            ],
            "Zara Okafor": ["Don't thank her,"],
        }
    )
    gold = {k: v.get("train", []) + v.get("test", []) for k, v in GOLD.items()}
    reasons, counts = pc.check_voice(prompt, gold, OTHER)
    assert counts == {
        "samples": 5,
        "own": 2,
        "misattributed": 2,
        "not_dialogue": 1,
    }
    assert len(reasons) == 3


def test_voice_samples_stop_at_the_next_heading():
    prompt = (
        '### Zara Okafor\nVoice samples (match):\n- "Don\'t thank her,"\n'
        'How they move:\n- "Zara set down her pen."'
    )
    assert pc.voice_samples(prompt) == {"Zara Okafor": ["Don't thank her,"]}


def test_the_beats_stage_needs_the_plan_it_details():
    acts = [{"title": "The Firm", "goal": "Milo joins", "turn": "He finds the gap"}]
    bad = "Here are the chapters of act 0, in order:\n[{'title': 'One'}]"
    reasons = pc.check_beats_stage(bad, ["Milo is retained."], [], acts)
    assert 'acts numbered from 0 ("act 0")' in reasons
    assert "Python repr ({') in the prompt" in reasons
    assert "confirmed premise missing" in reasons
    assert "no act's goal and turn in the prompt" in reasons

    good = (
        "Premise (confirmed): Milo is retained.\nAct 1: The Firm\n"
        'Goal: Milo joins\nTurn: He finds the gap\n[{"title": "One"}]'
    )
    assert pc.check_beats_stage(good, ["Milo is retained."], [], acts) == []


def test_sample_prose_replaces_generic_craft_text():
    sample = "Milo initialled the form in the box marked INITIAL HERE."
    with_generic = _beat_prompt() + "\n\nSample prose: " + sample
    assert pc.check_style_first(with_generic, sample) == [
        "generic craft text present: 'description and action carry the scene'"
    ]
    assert pc.check_style_first(_beat_prompt(craft=False), sample) == [
        "style sample prose not in the prompt"
    ]
    clean = _beat_prompt(craft=False) + "\n\nSample prose: " + sample
    assert pc.check_style_first(clean, sample) == []


def _write(dirpath, rid, purpose, prompt, answer="", created=100.0):
    (dirpath / f"{rid}.json").write_text(
        json.dumps(
            {
                "id": rid,
                "purpose": purpose,
                "created": created,
                "messages": [{"role": "user", "content": prompt}],
                "answer": answer,
            }
        )
    )


def test_grade_reads_an_archive_and_reports_per_check(tmp_path):
    _write(tmp_path, "1", "beat_prose", _beat_prompt(beats=False), "First.\n\nSecond.")
    _write(
        tmp_path,
        "2",
        "beat_prose",
        _beat_prompt(prior="First.\n\nSecond.", craft=False),
        "Third.",
        created=101.0,
    )
    _write(tmp_path, "0", "beat_prose", "an earlier run", created=1.0)
    _write(tmp_path, "3", "outline:chapters", "Acts: [{'goal': 'x'}]", created=102.0)
    records = pc.load_archive(tmp_path, since=50.0)
    assert [r["id"] for r in records] == ["1", "2", "3"]

    out = pc.grade(records, gold_lines=GOLD, other_lines=OTHER)
    assert out["beats_block"]["n"] == 2 and out["beats_block"]["passed"] == 1
    assert out["prior_paragraphs"] == {
        "n": 1,
        "passed": 1,
        "rate": 1.0,
        "failures": [],
        "unverified": 0,
    }
    assert out["outline_no_repr"]["rate"] == 0.0
    assert out["beats_stage"]["n"] == 0 and out["beats_stage"]["rate"] is None
    assert out["purposes"] == {"beat_prose": 2, "outline:chapters": 1}
    assert 0.0 <= out["score"] <= 1.0


def test_an_empty_or_missing_archive_grades_nothing(tmp_path):
    assert pc.load_archive(tmp_path / "missing") == []
    out = pc.grade([])
    assert out["n_calls"] == 0 and out["score"] is None


def test_purpose_is_inferred_for_an_archive_without_the_header():
    def rec(purpose, prompt):
        return {"purpose": purpose, "messages": [{"role": "user", "content": prompt}]}

    beat = "You are a skilled novelist writing one beat of a scene in THIS book."
    assert pc.purpose(rec("", beat)) == "beat_prose"
    assert pc.purpose(rec("", "For EACH chapter, write 2-4 scene beats.")) == (
        "outline:beats"
    )
    # The header wins; a system line (with spaces) does not.
    assert pc.purpose(rec("outline:chapters", beat)) == "outline:chapters"
    assert pc.purpose(rec("You are a strict judge.", "score")) == (
        "You are a strict judge."
    )


# --- wave 2 (SPEC-28; tpm-plan-wave2 §1.3 P1-P10, §5.1) --------------------------

SECONDARIES = ["Prava", "Old Fen"]
PRINCIPALS = [
    {"name": "Milo Voss", "aliases": ["Milo"]},
    {"name": "Zara Okafor", "aliases": ["Zara"]},
    {"name": "Edric Thane", "aliases": ["Edric", "the Lich"]},
]
ALIASES = {
    form.lower(): p["name"] for p in PRINCIPALS for form in [p["name"], *p["aliases"]]
}
MILO_DESC = "A newly dead mid-level operations analyst."
ZARA_DESC = "Dead for two hundred and six years; Milo's trainer."

OLD_CHAPTERS = (
    "Break the acts into chapters. Every name you use for a character MUST be one of\n"
    "the canon cast: Edric Thane, Milo Voss, Zara Okafor."
)
NEW_CHAPTERS = (
    "Break the acts into chapters. Principal characters MUST be canon cast.\n"
    "SECONDARY PEOPLE (named in the synopsis or canon, not in the cast; you may name "
    "them, never invent others): Prava, Old Fen"
)
OLD_BEATS_TEMPLATE = (
    'For EACH chapter, write 2-4 scene beats.\n[{"title": <t>, "children": '
    '[{"title": <beat title>, "summary": <what changes>, "children": []}]}]'
)
NEW_BEATS_TEMPLATE = (
    'For EACH chapter, write 2-4 scene beats.\n[{"title": <t>, "children": '
    '[{"title": <beat title>, "summary": <what changes>, "characters": [<names>], '
    '"secondary_names": [<names>], "setting": <where>, "tone": <tone>, '
    '"children": []}]}]'
)


def test_outline_secondaries_line_must_list_every_secondary():
    reasons = pc.check_outline_secondaries(OLD_CHAPTERS, SECONDARIES)
    assert "no SECONDARY PEOPLE line" in reasons
    assert any("old cast rule present" in r for r in reasons)
    assert any("no principals rule" in r for r in reasons)
    assert pc.check_outline_secondaries(NEW_CHAPTERS, SECONDARIES) == []
    partial = NEW_CHAPTERS.replace("Prava, Old Fen", "Prava")
    assert pc.check_outline_secondaries(partial, SECONDARIES) == [
        "SECONDARY PEOPLE misses Old Fen"
    ]
    none = NEW_CHAPTERS.replace("Prava, Old Fen", "(none)")
    assert pc.check_outline_secondaries(none, []) == []


def test_beats_template_asks_each_beat_for_cast_setting_and_tone():
    assert pc.check_beats_stage_fields(OLD_BEATS_TEMPLATE) == [
        'children template lacks "characters", "secondary_names", "setting", "tone"'
    ]
    assert pc.check_beats_stage_fields(NEW_BEATS_TEMPLATE) == []
    assert pc.check_beats_stage_fields("no template") == ["no children template"]


def _plan_prompt(cast="Milo Voss", setting="", tone="", desc="Intake: x"):
    """A plan_beats prompt in the wave-2 shape (P3/P4); `cast=None` gives the
    wave-1 shape ("cast is exactly", blank setting, neutral tone)."""
    lines = ["You are a narrative planner.", "<scene>", f"Scene description: {desc}"]
    if cast is None:
        lines += ["Setting: ", "Emotional tone: neutral", "</scene>"]
        lines.append("The scene's cast is exactly:\nMilo Voss, Zara Okafor.")
        return "\n".join(lines)
    if setting:
        lines.append(f"Setting: {setting}")
    if tone:
        lines.append(f"Emotional tone: {tone}")
    lines += [
        "</scene>",
        f"Available cast (not all need appear): {cast}",
        "Also named (secondary, not in the cast): Prava",
        "Any unnamed functional figures (a clerk) may appear.",
    ]
    return "\n".join(lines)


def test_plan_prompt_lists_available_cast_not_exact_cast():
    assert pc.check_plan_cast_available(_plan_prompt()) == []
    assert pc.check_plan_cast_available(_plan_prompt(cast=None)) == [
        'no "Available cast (not all need appear):" line',
        'no "unnamed functional figures" allowance',
        '"cast is exactly" present',
    ]
    assert pc.available_cast(_plan_prompt()) == ["Milo Voss"]
    assert pc.available_cast(_plan_prompt(cast=None)) is None


def test_blank_setting_and_neutral_tone_are_flagged():
    old = _plan_prompt(cast=None)
    assert pc.check_no_blank_setting(old) == ['blank "Setting:" line']
    assert pc.check_no_neutral_tone(old) == ['"Emotional tone: neutral" line']
    new = _plan_prompt(setting="Intake Atrium", tone="bureaucratic dread")
    assert pc.check_no_blank_setting(new) == [] and pc.check_no_neutral_tone(new) == []
    # An empty setting is simply left out.
    assert pc.check_no_blank_setting(_plan_prompt()) == []


POV_OK = (
    "Point of view: third person limited, Milo Voss. Narrate only what Milo Voss "
    "perceives or knows; nothing he cannot see."
)


def test_pov_line_needs_mode_character_and_knowledge_limit():
    assert pc.check_pov_line(POV_OK, "Milo Voss") == []
    assert pc.check_pov_line("Point of view: Milo Voss\n", "Milo Voss") == [
        'no "Point of view: <mode>, <character>." line'
    ]
    no_limit = "Point of view: third person limited, Milo Voss.\n\nNarrate freely."
    assert pc.check_pov_line(no_limit, "Milo Voss") == [
        'no "Narrate only what Milo Voss perceives or knows" after it'
    ]
    wrong = POV_OK.replace("Milo Voss", "Zara Okafor")
    assert "not the chapter POV" in pc.check_pov_line(wrong, "Milo Voss")[0]
    # With no POV character the line carries the mode alone.
    assert pc.check_pov_line("Point of view: third person limited.\n", None) == []


def test_last_beat_ends_inside_the_moment():
    ok = "Write ONLY beat 3, the last beat: " + pc.LAST_BEAT_TEXT + "."
    assert pc.check_last_beat_in_pov(ok) == []
    old = "Write ONLY beat 3, the last beat: end the scene here."
    assert pc.check_last_beat_in_pov(old) == ["no in-POV ending for the last beat"]


def _bible_prompt(dup=True):
    canon = (
        "<canon>\n### Milo Voss\nRole: protagonist\n"
        f"Description: {MILO_DESC}\nVoice: diction: upbeat\n</canon>"
    )
    char = ["<character>", "### Milo Voss"]
    if dup:
        char += ["Role: protagonist", f"Description: {MILO_DESC}", "Voice: diction: x"]
    char += ["Voice samples (their own words):", '- "Totally."', "</character>"]
    return (
        f"{canon}\n\nCHARACTERS IN THIS BEAT — voices:\n\n"
        + "\n".join(char)
        + "\n\nBEAT TO WRITE: x"
    )


def test_beat_prompt_carries_one_bible_and_voice_only_character_blocks():
    reasons = pc.check_no_duplicate_bible(_bible_prompt(), [MILO_DESC])
    assert reasons[0] == (
        "bible fields in CHARACTERS IN THIS BEAT: Description, Role, Voice"
    )
    assert "canon description appears 2x" in reasons[1]
    assert pc.check_no_duplicate_bible(_bible_prompt(dup=False), [MILO_DESC]) == []
    # The canon block stays whole: a missing description fails too.
    missing = pc.check_no_duplicate_bible(_bible_prompt(dup=False), [ZARA_DESC])
    assert "appears 0x" in missing[0]


def test_doubled_voice_label_is_flagged():
    assert pc.check_no_doubled_voice("Voice: voice: Terse and flat") == [
        'doubled label "Voice: voice:"'
    ]
    assert pc.check_no_doubled_voice("Voice: diction: Terse and flat") == []


def _reduce_prompt(depth=3):
    def section(name, n):
        ev = [f"- (part 1) evidence {i}" for i in range(n)]
        return "\n".join([f"### {name}", "Mentions: 8", "Evidence:", *ev])

    zara = section("Zara", depth - 1) + '\nQuoted lines (their own words):\n- "Read."'
    body = "\n\n".join([section("Milo Voss", depth), zara, section("Halvard Bell", 1)])
    return (
        "Write the full character entries for each character headed above.\n"
        f"<characters>\n{body}\n</characters>"
    )


def test_reduce_gives_every_principal_three_evidence_or_quote_lines():
    names = [p["name"] for p in PRINCIPALS]
    assert pc.check_reduce_evidence(_reduce_prompt(3), names, ALIASES) == ([], 2)
    reasons, checked = pc.check_reduce_evidence(_reduce_prompt(1), names, ALIASES)
    assert checked == 2  # Halvard Bell is no principal; Edric is not in this one
    assert reasons == [
        "Milo Voss: 1 evidence/quote line(s) (< 3)",
        "Zara: 1 evidence/quote line(s) (< 3)",
    ]


def _beats_answer(characters):
    child = {"title": "Intake", "summary": "x", "children": []}
    if characters is not None:
        child["characters"] = characters
    chapter = {
        "title": "Strong Culture Fit",
        "summary": "Milo dies and is onboarded.",
        "pov": "Milo Voss",
        "characters": ["Milo Voss", "Zara Okafor"],
        "children": [child],
    }
    return json.dumps([chapter])


def test_plan_cast_is_the_outline_beats_cast_plus_the_pov():
    index = pc.OutlineIndex(ALIASES)
    index.add(_beats_answer(["Milo"]))
    # The beat's own cast (alias resolved) plus the POV, who is Milo anyway.
    assert pc.check_plan_cast_from_beat(_plan_prompt(), index) == []
    chapter_cast = _plan_prompt(cast="Milo Voss, Zara Okafor")
    assert "!= beat cast + POV" in pc.check_plan_cast_from_beat(chapter_cast, index)[0]
    assert pc.check_plan_cast_from_beat(_plan_prompt(cast=None), index) == [
        'no "Available cast" line'
    ]
    # A beat with no cast of its own takes the chapter's.
    index = pc.OutlineIndex(ALIASES)
    index.add(_beats_answer(None))
    assert pc.check_plan_cast_from_beat(chapter_cast, index) == []
    # A prompt that matches no outline beat is not graded.
    assert pc.check_plan_cast_from_beat(_plan_prompt(desc="Other: y"), index) is None


def test_pov_character_comes_from_the_style_guide():
    assert pc.pov_character("third person limited, on Milo Voss") == "Milo Voss"
    assert pc.pov_character("first person") is None


def test_corpus_inputs_carry_the_cel_wave2_fixtures():
    from evals.steps import pipeline

    _, gt = pipeline.load_corpus("cel")
    kw = pc.corpus_inputs(gt, pipeline.load_style("cel"))
    assert kw["secondaries"] == ["Prava", "Old Fen"]
    assert kw["pov"] == "Milo Voss" and kw["style_tone"].startswith("Dry, deadpan")
    assert [p["name"] for p in kw["principals"]] == [
        "Milo Voss",
        "Zara Okafor",
        "Edric Thane",
    ]
    assert len(kw["descriptions"]) == 3


def test_wave2_checks_report_none_on_an_archive_without_their_purposes(tmp_path):
    """The before-archive still grades: a check with no matching call is
    rate None, never a failure."""
    _write(tmp_path, "1", "extraction_map", "Find the characters.", "{}")
    out = pc.grade(
        pc.load_archive(tmp_path),
        secondaries=SECONDARIES,
        principals=PRINCIPALS,
        style_tone="Dry, deadpan",
        pov="Milo Voss",
        descriptions=[MILO_DESC],
    )
    for check in pc.CHECKS:
        if check == "no_doubled_voice_label":  # every purpose: it ran, and passed
            assert out[check]["rate"] == 1.0
        else:
            assert out[check]["n"] == 0 and out[check]["rate"] is None, check
    assert out["score"] == 1.0


def test_beats_stage_accepts_the_premise_the_run_confirmed(tmp_path):
    """After the F8 fixture edit, an older archive is graded against the
    premise its own chapters stage was given, not the new fixture text."""
    old = "Milo and Zara end him."
    chapters = f"Premise (confirmed by the author: treat it as authoritative): {old}"
    beats = f"Premise (confirmed by the author: treat it as authoritative): {old}"
    _write(tmp_path, "1", "outline:chapters", chapters, created=1.0)
    _write(tmp_path, "2", "outline:beats", beats + "\nGoal: g\nTurn: t", created=2.0)
    out = pc.grade(
        pc.load_archive(tmp_path),
        premise_edit={"premise_restated": "Edric files it correctly."},
    )
    assert out["beats_stage"]["rate"] == 1.0


def test_grade_runs_the_wave2_checks_over_an_archive(tmp_path):
    _write(tmp_path, "1", "outline:chapters", NEW_CHAPTERS, created=1.0)
    _write(
        tmp_path,
        "2",
        "outline:beats",
        NEW_CHAPTERS + "\n" + NEW_BEATS_TEMPLATE,
        _beats_answer(["Milo Voss"]),
        created=2.0,
    )
    plan = _plan_prompt(setting="Intake Atrium", tone="dread")
    _write(tmp_path, "3", "plan_beats", plan, created=3.0)
    _write(tmp_path, "4", "plan_beats", _plan_prompt(desc="Unknown: z"), created=4.0)
    beat = (
        "<chapter>\nMilo dies and is onboarded.\n</chapter>\n"
        "<beats>\n1. He arrives.\n→ 2. He signs.\n</beats>\n"
        + POV_OK
        + "\n\n"
        + _bible_prompt(dup=False)
        + "\nWrite ONLY beat 2, the last beat: "
        + pc.LAST_BEAT_TEXT
    )
    _write(tmp_path, "5", "beat_prose", beat, "He signed.", created=5.0)
    _write(tmp_path, "6", "extraction_reduce", _reduce_prompt(3), created=6.0)
    out = pc.grade(
        pc.load_archive(tmp_path),
        secondaries=SECONDARIES,
        principals=PRINCIPALS,
        style_tone="Dry, deadpan",
        pov="Zara Okafor",  # the chapter's own POV (Milo) wins over the default
        descriptions=[MILO_DESC],
    )
    for check in pc.CHECKS[6:]:
        assert out[check]["rate"] == 1.0, (check, out[check])
    assert out["outline_secondaries_allowed"]["n"] == 2
    assert out["plan_cast_from_beat"]["n"] == 1
    assert out["plan_cast_from_beat"]["unmatched"] == ["4"]
    assert out["no_blank_setting"]["n"] == 3  # 2 plan + 1 beat
    assert out["beat_prose_prompt_chars_median"] == float(len(beat))
