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
