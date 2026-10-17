"""SPEC-26 (W2-5): extraction writes a real character bible.

Mention counts credit an unambiguous given name or surname; map evidence is a
list (a string still works); reduce gets each candidate's own attributed lines
and, for a one-window source, the window itself (prompt block P10).
"""

from __future__ import annotations

import json
import re
import types
from pathlib import Path

import pytest

import app.parsing.canon_extractor as ce

pytestmark = pytest.mark.unit

CEL = (
    Path(__file__).resolve().parents[2] / "evals" / "corpora" / "cel" / "excerpts.txt"
).read_text(encoding="utf-8")
PRINCIPALS = ["Milo Voss", "Zara Okafor", "Edric Thane"]


def _cand(name, aliases=(), evidence="seen"):
    return {
        "name": name,
        "aliases": list(aliases),
        "role_hint": "",
        "evidence": evidence,
    }


def _block(prompt: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", prompt, re.S)
    return m.group(1) if m else ""


def _sections(block: str) -> dict[str, str]:
    """{name: its ### section} of a reduce prompt's <characters> block."""
    parts = re.split(r"^### (.+)$", block, flags=re.M)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


class EchoLLM:
    """Map: names the principals with evidence as a LIST. Reduce: writes each
    field only from what the prompt gives that character — description from
    its evidence, goals and voice from its quoted lines — so an empty field
    means reduce was not given the material."""

    def __init__(self, evidence_as_list: bool = True):
        self.prompts: dict[str, list[str]] = {}
        self.evidence_as_list = evidence_as_list

    async def generate(self, messages, *, purpose="", **kw):
        prompt = messages[0]["content"]
        self.prompts.setdefault(purpose, []).append(prompt)
        if purpose == "extraction_map":
            src = _block(prompt, "source")
            chars = []
            for name in PRINCIPALS:
                if name.split()[0] not in src:
                    continue
                quotes = [
                    s.strip()
                    for s in re.split(r"(?<=[.!?])\s+", src)
                    if name.split()[0] in s
                ][:4]
                chars.append(
                    {
                        "name": name,
                        "aliases": [],
                        "evidence": quotes if self.evidence_as_list else quotes[0],
                    }
                )
            return self._r({"characters": chars, "canon_entries": [], "summary": "x"})
        if purpose == "extraction_reduce":
            out = []
            for name, section in _sections(_block(prompt, "characters")).items():
                evidence = re.findall(r"^- \(part \d+\) (.+)$", section, re.M)
                quoted = re.findall(r'^- "(.+)"$', section, re.M)
                out.append(
                    {
                        "name": name,
                        "role": "protagonist",
                        "description": evidence[0] if evidence else "",
                        "goals": quoted[0] if quoted else "",
                        "voice_characteristics": (
                            {"sample": quoted[-1]} if quoted else {}
                        ),
                    }
                )
            return self._r({"characters": out})
        if purpose == "extraction_synopsis":
            return self._r({"synopsis": "A synopsis."})
        raise AssertionError(purpose)

    @staticmethod
    def _r(obj):
        return types.SimpleNamespace(text=json.dumps(obj), tokens_in=1, tokens_out=1)


@pytest.fixture
def echo(monkeypatch):
    fake = EchoLLM()
    monkeypatch.setattr(ce, "get_llm_client", lambda: fake)
    return fake


# --- mention counts ------------------------------------------------------------


class TestMentions:
    def test_cel_ranks_milo_first(self):
        answers = [(0, {"characters": [_cand(n) for n in PRINCIPALS]})]
        merged = {g["name"]: g["mentions"] for g in ce.merge_characters(answers, CEL)}
        milo, zara, edric = (merged[n] for n in PRINCIPALS)
        assert milo >= zara >= edric
        assert milo >= len(re.findall(r"\bMilo\b", CEL))
        # Counted once per mention: "Milo Voss" is one mention, not two.
        assert milo == len(re.findall(r"\bMilo\b", CEL)) + len(
            re.findall(r"\bVoss\b", CEL)
        ) - len(re.findall(r"\bMilo Voss\b", CEL))

    def test_shared_surname_counts_for_neither(self):
        text = "Harker slept. Mina wrote. Jonathan rode. Mina Harker woke."
        answers = [
            (0, {"characters": [_cand("Mina Harker"), _cand("Jonathan Harker")]})
        ]
        merged = {g["name"]: g["mentions"] for g in ce.merge_characters(answers, text)}
        assert merged == {"Mina Harker": 2, "Jonathan Harker": 1}

    def test_words_and_honorifics_are_not_name_tokens(self):
        text = (
            "Old habits. Old Fen filed. Fen sighed. The Lich spoke. The end. Mr. Fen."
        )
        answers = [(0, {"characters": [_cand("Old Fen"), _cand("The Lich")]})]
        merged = {g["name"]: g["mentions"] for g in ce.merge_characters(answers, text)}
        assert merged["Old Fen"] == 3  # "Old Fen", "Fen", "Fen" of "Mr. Fen"
        assert merged["The Lich"] == 1  # "The end" is not the Lich

    def test_pronouns_and_lowercase_not_counted(self):
        text = "Milo Voss sat. He waited. milo is a word. Milo left."
        answers = [(0, {"characters": [_cand("Milo Voss")]})]
        (milo,) = ce.merge_characters(answers, text)
        assert milo["mentions"] == 2


# --- evidence ------------------------------------------------------------------


class TestEvidence:
    def test_map_accepts_list_or_string(self):
        win = ce._normalize_window(
            {
                "characters": [
                    {"name": "Milo Voss", "evidence": ["a", "b", "a", "c", "d"]},
                    {"name": "Zara Okafor", "evidence": "Don't thank her."},
                    {"name": "Edric Thane", "description": "the CEL"},
                    {"name": "Old Fen", "evidence": [{"x": 1}, "", "files"]},
                ]
            }
        )
        ev = {c["name"]: c["evidence"] for c in win["characters"]}
        assert ev == {
            "Milo Voss": ["a", "b", "c"],
            "Zara Okafor": ["Don't thank her."],
            "Edric Thane": ["the CEL"],
            "Old Fen": ["files"],
        }

    def test_evidence_is_one_line_each(self):
        assert ce._evidence_list(['up."\n\nZara  sat.', "x" * 900]) == [
            'up." Zara sat.',
            "x" * ce._MAX_EVIDENCE_CHARS,
        ]

    def test_list_and_string_windows_merge(self):
        answers = [
            (0, {"characters": [_cand("Milo Voss", evidence=["one", "two"])]}),
            # A checkpoint written before SPEC-26 still holds one string.
            (1, {"characters": [_cand("Milo Voss", evidence="three")]}),
        ]
        (milo,) = ce.merge_characters(answers)
        assert milo["evidence"] == [(0, "one"), (0, "two"), (1, "three")]

    def test_merged_evidence_capped_and_spread_across_windows(self):
        answers = [
            (w, {"characters": [_cand("Milo Voss", evidence=[f"{w}a", f"{w}b"])]})
            for w in range(6)
        ]
        (milo,) = ce.merge_characters(answers)
        assert len(milo["evidence"]) == ce._MAX_EVIDENCE == 8
        # Every window's first quote is in before any window's second.
        assert {e for _w, e in milo["evidence"] if e.endswith("a")} == {
            f"{w}a" for w in range(6)
        }
        assert milo["evidence"] == sorted(milo["evidence"])

    def test_map_prompt_asks_for_a_list(self):
        prompt = ce._map_prompt("text", "1", 1)
        assert '"evidence": [' in prompt
        assert re.search(r"up to\s+3 short quotes", prompt)


# --- SPEC-56 (W7-4 (a)): map evidence is the author's text, or nothing ---------------

W6 = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "w6_archives.json").read_text()
)
VOSS = '"Mr. Voss," the Lich said. "Do sit.'


def test_every_archived_evidence_line_is_kept_repaired_or_dropped():
    """(F) The 80 W5d/W5bd/W6/W6b evidence lines against their windows: verbatim
    is kept as written, a splice across a tag becomes the author's span (tag
    included), and what remains is a run of the window. ", warmly" is no tag
    clause (`tag_spans`), so that one splice is dropped."""
    from collections import Counter

    from app.parsing.character_extractor import normalize_quotes

    seen = Counter()
    for row in W6["evidence"]["rows"]:
        window = W6["windows"][row["window"]]
        out = ce.verbatim_evidence([row["quote"]], window)
        flat = " ".join(normalize_quotes(window).split())
        assert all(e.strip("\"' ") in flat for e in out), row
        kind = "kept" if out == [row["quote"]] else "repaired" if out else "dropped"
        seen[kind] += 1
        adverb = "adverb" in row.get("note", "")
        expect = {"verbatim": "kept", "tag_split": "dropped" if adverb else "repaired"}
        assert kind == expect.get(row["label"], kind), row
        assert row["label"] != "tag_split" or adverb or " said. " in out[0], row
    assert seen == {"kept": 60, "repaired": 14, "dropped": 6}
    assert ce.verbatim_evidence(
        ["Mr. Voss, do sit."], W6["windows"]["cel_excerpt"]
    ) == [VOSS]


async def test_the_map_keeps_only_the_authors_text(monkeypatch):
    """(F) Through the map call; a member left with nothing has no verbatim
    quote in its part (SPEC-64), never "narrates"."""
    raw = {
        "characters": [
            {"name": "Edric Thane", "evidence": ["Mr. Voss, do sit.", "I am kind."]},
            {"name": "Old Fen", "evidence": ["Fen was never here."]},
        ]
    }

    async def answer(*a, **kw):
        return raw

    monkeypatch.setattr(ce, "_json_call", answer)
    (win,), _ = await ce._map_one(W6["windows"]["cel_excerpt"], "1", 1, None, {})
    merged = {g["name"]: g["evidence"] for g in ce.merge_characters([(0, win)])}
    assert merged == {
        "Edric Thane": [(0, VOSS)],
        "Old Fen": [(0, "no verbatim quote in part 1")],
    }


def test_narrates_part_n_is_the_narrator_paths_alone():
    """(R) SPEC-64: a `style_signals.narrators` member keeps "narrates part N";
    (F) a quoteless character in the same window does not."""
    ans = {
        "characters": [_cand("Lucy", evidence=[])],
        "style_signals": {"narrators": ["Jon"]},
    }
    merged = {g["name"]: g["evidence"] for g in ce.merge_characters([(2, ans)])}
    assert merged == {
        "Jon": [(2, "narrates part 3")],
        "Lucy": [(2, "no verbatim quote in part 3")],
    }


# --- reduce input (P10) --------------------------------------------------------


class TestReduceInput:
    async def test_cel_reduce_has_depth_and_source(self, echo):
        await ce.extract_canon(CEL)
        (prompt,) = echo.prompts["extraction_reduce"]
        assert _block(prompt, "source").startswith("Milo Voss opened his eyes")
        sections = _sections(_block(prompt, "characters"))
        for name in PRINCIPALS:
            lines = re.findall(r'^- (?:\(part \d+\) .+|".+")$', sections[name], re.M)
            assert len(lines) >= 3, (name, sections[name])
            assert "Quoted lines (their own words):" in sections[name]
        # Own words only: Zara's section never carries Milo's lines.
        assert "hit-the-ground-running" in sections["Milo Voss"]
        assert "hit-the-ground-running" not in sections["Zara Okafor"]
        assert "Bring nothing. Sign nothing." in sections["Zara Okafor"]

    async def test_string_evidence_from_the_stub_shape_still_works(self, monkeypatch):
        fake = EchoLLM(evidence_as_list=False)
        monkeypatch.setattr(ce, "get_llm_client", lambda: fake)
        out = await ce.extract_canon(CEL)
        assert {c["name"] for c in out["characters"]} >= set(PRINCIPALS)

    async def test_multi_window_source_sends_no_source_block(self, echo):
        await ce.extract_canon(CEL, window_tokens=300)
        assert len(echo.prompts["extraction_map"]) > 1
        for prompt in echo.prompts["extraction_reduce"]:
            assert "<source>" not in prompt

    def test_quotes_prefer_four_words_in_source_order(self):
        lines = ["So!", "I'm good, thanks,", "a b c d", "one two three four five"]
        lines += [f"line number {i} here" for i in range(6)]
        picked = ce._pick_quotes(lines)
        assert len(picked) == ce._MAX_QUOTED_LINES
        assert "So!" not in picked and "I'm good, thanks," not in picked
        assert picked[0] == "a b c d"
        # Short lines still fill in when there are not enough long ones.
        assert ce._pick_quotes(["Yes.", "No.", "Close the accounts now."]) == [
            "Yes.",
            "No.",
            "Close the accounts now.",
        ]

    def test_ambiguous_speaker_is_nobodys_quote(self):
        text = '"We ride at dawn," said Harker.\n\n"I will write it down," said Mina.'
        groups = [
            {"name": "Mina Harker", "aliases": []},
            {"name": "Jonathan Harker", "aliases": []},
        ]
        ce.attach_quotes(groups, text)
        assert groups[0]["quotes"] == ["I will write it down."]
        assert groups[1]["quotes"] == []

    def test_render_candidate_p10_format(self):
        g = {
            "name": "Zara Okafor",
            "aliases": [],
            "mentions": 8,
            "windows": [0],
            "role_hints": [],
            "evidence": [(0, "did not look up")],
            "quotes": ["Read the appendices first."],
        }
        out = ce._render_candidate(g, 1)
        assert out.splitlines()[-4:] == [
            "Evidence:",
            "- (part 1) did not look up",
            "Quoted lines (their own words):",
            '- "Read the appendices first."',
        ]
