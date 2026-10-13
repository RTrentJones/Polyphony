"""SPEC-09: extraction at manuscript scale — map-reduce, merge, preflight,
aliases, parser normalisation, narrator voice."""

from __future__ import annotations

import json
import re
import types

import pytest

import app.parsing.canon_extractor as ce
from app.llm.errors import QuotaExhaustedError

pytestmark = pytest.mark.unit

_NAMES = ["Mina Harker", "Jonathan Harker", "Abraham Verhoeven", "Lucy Westenra"]


def _block(prompt: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\n(.*?)\n</{tag}>", prompt, re.S)
    return m.group(1) if m else ""


class CountingLLM:
    """Answers only from the prompt, and counts calls per purpose."""

    def __init__(self, map_answers=None):
        self.calls: list[str] = []
        self.map_answers = list(map_answers or [])

    async def generate(self, messages, *, purpose="", **kw):
        prompt = messages[0]["content"]
        self.calls.append(purpose)
        if purpose == "extraction_map":
            if self.map_answers:
                text = self.map_answers.pop(0)
                if text is not None:
                    return self._r(text)
            src = _block(prompt, "source")
            found = [n for n in _NAMES if n in src]
            extra = re.findall(r"\bExtra(\d+) Person\b", src)
            chars = [
                {"name": n, "aliases": [n.split()[0]], "evidence": f"{n} appears"}
                for n in found
            ] + [
                {"name": f"Extra{i} Person", "aliases": [], "evidence": "once"}
                for i in sorted(set(extra))
            ]
            return self._r(
                json.dumps(
                    {
                        "characters": chars,
                        "canon_entries": [
                            {"name": "Whitby", "category": "loc", "content": "a town"}
                        ],
                        "style_signals": {"pov": "first", "tense": "past"},
                        "summary": "things happen",
                    }
                )
            )
        if purpose == "extraction_reduce":
            names = re.findall(r"^### (.+)$", _block(prompt, "characters"), re.M)
            return self._r(
                json.dumps(
                    {
                        "characters": [
                            {
                                "name": n,
                                "role": "villain" if "Verhoeven" in n else "supporting",
                                "description": f"{n}.",
                                "goals": "survive",
                                "arc": "grows",
                                "relationships": {"Lucy Westenra": "friend"},
                                "personality_traits": ["brave"],
                                "voice_characteristics": {"diction": "plain"},
                                "aliases": [],
                            }
                            for n in names
                        ]
                    }
                )
            )
        if purpose == "extraction_synopsis":
            return self._r(json.dumps({"synopsis": "A synopsis."}))
        raise AssertionError(f"unexpected purpose {purpose}")

    @staticmethod
    def _r(text):
        return types.SimpleNamespace(text=text, tokens_in=10, tokens_out=10)


def _novel(paragraphs: int = 1000) -> str:
    """~120k tokens: every paragraph names Mina; each window has extras."""
    paras = []
    for i in range(paragraphs):
        who = _NAMES[i % len(_NAMES)]
        filler = "The night was long and the road was longer still. " * 8
        paras.append(f"Mina Harker wrote of {who}. Extra{i // 100} Person. {filler}")
    return "\n\n".join(paras)


@pytest.fixture
def llm(monkeypatch):
    fake = CountingLLM()
    monkeypatch.setattr(ce, "get_llm_client", lambda: fake)
    return fake


class TestWindows:
    def test_windows_split_on_paragraphs_and_cover_everything(self):
        text = "\n\n".join(f"Paragraph {i} " + "word " * 200 for i in range(50))
        windows = ce.split_windows(text, window_tokens=1000)
        assert len(windows) > 1
        joined = "\n\n".join(windows)
        assert joined.split() == text.split()  # nothing cut, nothing added
        assert joined.count("Paragraph") == 50
        for w in windows:
            assert all(p.startswith("Paragraph") for p in w.split("\n\n"))

    def test_overlong_paragraph_splits_on_sentences_never_mid_word(self):
        para = " ".join(f"Sentence number {i} ends here." for i in range(400))
        windows = ce.split_windows(para, window_tokens=200)
        assert len(windows) > 1
        assert all(w.endswith(".") for w in windows)
        assert " ".join(windows).split() == para.split()


class TestMapReduceScale:
    async def test_manuscript_is_windowed_and_bounded(self, llm):
        """AC1: ~120k tokens -> ~10 map calls, bounded reduce calls, one entry per
        character even when a name appears in every window."""
        text = _novel()
        assert 110_000 <= ce.estimate_tokens(text) <= 130_000
        out = await ce.extract_canon(text)

        map_calls = llm.calls.count("extraction_map")
        reduce_calls = llm.calls.count("extraction_reduce")
        assert 9 <= map_calls <= 12
        assert map_calls == out["stats"]["windows"]
        n_chars = len(out["characters"])
        assert reduce_calls == -(-min(n_chars, ce.MAX_REDUCED_CHARACTERS) // 10)
        assert llm.calls.count("extraction_synopsis") == 1

        names = [c["name"] for c in out["characters"]]
        assert names.count("Mina Harker") == 1  # in every window, proposed once
        mina = next(c for c in out["characters"] if c["name"] == "Mina Harker")
        assert mina["windows"] == map_calls
        assert mina["mentions"] >= 1000
        # Ranked: the every-window characters before the one-window extras.
        assert out["characters"][0]["windows"] == map_calls
        assert out["characters"][-1]["windows"] == 1
        # E5 default: one-window names are proposed but pre-deselected.
        assert out["characters"][0]["selected"] is True
        assert out["characters"][-1]["selected"] is False
        # The full bible entry is proposed, in the editor's {key: value} shape.
        assert mina["goals"] == "survive" and mina["arc"] == "grows"
        assert mina["personality_traits"] == {"brave": ""}
        assert mina["voice_characteristics"] == {"diction": "plain"}
        assert mina["relationships"] == {"Lucy Westenra": "friend"}
        assert "Mina" in mina["aliases"]
        verhoeven = next(c for c in out["characters"] if "Verhoeven" in c["name"])
        assert verhoeven["role"] == "antagonist"  # closed vocabulary
        assert out["synopsis"] == "A synopsis."
        assert [e["name"] for e in out["canon_entries"]] == ["Whitby"]

    async def test_reduce_calls_are_bounded_for_a_huge_cast(self, monkeypatch, llm):
        text = "\n\n".join(
            f"Extra{i} Person met Mina Harker. " + "x " * 50 for i in range(150)
        )
        out = await ce.extract_canon(text)
        assert llm.calls.count("extraction_reduce") == ce.MAX_REDUCED_CHARACTERS // 10
        # The long tail is still proposed (map-level), never dropped.
        assert len(out["characters"]) == 151

    async def test_truncated_window_retries_smaller_and_never_fails(self, monkeypatch):
        """AC2: a truncated JSON answer is retried once as smaller windows."""
        truncated = '{"characters": [{"name": "Mina Harker", "aliases": ["Mi'
        fake = CountingLLM(map_answers=[truncated])
        monkeypatch.setattr(ce, "get_llm_client", lambda: fake)
        text = "\n\n".join(f"Mina Harker para {i}. " + "y " * 100 for i in range(20))
        out = await ce.extract_canon(text)
        assert fake.calls.count("extraction_map") >= 3  # 1 failed + 2 halves
        assert [c["name"] for c in out["characters"]] == ["Mina Harker"]
        assert out["warnings"] == []

    async def test_window_that_stays_unreadable_is_skipped_with_warning(
        self, monkeypatch
    ):
        bad = '{"characters": [{"name": "Mina'
        fake = CountingLLM(map_answers=[bad, bad, bad, bad, bad])
        monkeypatch.setattr(ce, "get_llm_client", lambda: fake)
        text = "\n\n".join(f"Mina Harker para {i}. " + "y " * 100 for i in range(20))
        out = await ce.extract_canon(text)  # does not raise
        assert out["warnings"]
        assert out["characters"] == []

    async def test_inner_object_of_truncated_answer_is_not_taken_as_the_window(self):
        with pytest.raises(ce._Unreadable):
            ce._normalize_window({"name": "Mina Harker", "aliases": []})

    async def test_resume_skips_finished_windows(self, monkeypatch):
        """A quota pause mid-map resumes at the next window (SPEC-08 shape)."""
        fake = CountingLLM()
        monkeypatch.setattr(ce, "get_llm_client", lambda: fake)
        text = "\n\n".join(f"Mina Harker para {i}. " + "z " * 400 for i in range(30))
        windows = ce.split_windows(text, window_tokens=1000)
        assert len(windows) >= 4
        saved: list[dict] = []

        async def save(cp):
            saved.append(json.loads(json.dumps(cp)))  # must be JSON-serialisable

        real = fake.generate
        state = {"n": 0}

        async def flaky(messages, **kw):
            state["n"] += 1
            if state["n"] == 3:
                raise QuotaExhaustedError("quota", reset_after=1)
            return await real(messages, **kw)

        fake.generate = flaky
        with pytest.raises(QuotaExhaustedError):
            await ce.extract_canon(text, save_checkpoint=save, window_tokens=1000)
        assert saved[-1]["windows_done"] == 2

        fake.generate = real
        fake.calls.clear()
        out = await ce.extract_canon(
            text, checkpoint=saved[-1], save_checkpoint=save, window_tokens=1000
        )
        assert fake.calls.count("extraction_map") == len(windows) - 2
        assert out["stats"]["windows"] == len(windows)

    async def test_checkpoint_over_different_text_is_discarded(self, llm):
        cp = {"text_hash": "other", "map": {"0": []}}
        out = await ce.extract_canon("Mina Harker walks.", checkpoint=cp)
        assert llm.calls.count("extraction_map") == 1
        assert [c["name"] for c in out["characters"]] == ["Mina Harker"]


def _cand(name, aliases=(), evidence="seen"):
    return {
        "name": name,
        "aliases": list(aliases),
        "role_hint": "",
        "evidence": evidence,
    }


class TestMerge:
    def test_mina_forms_merge_when_evidence_links_them(self):
        """AC3: "Mina", "Mrs. Harker", "Madam Mina" -> one proposal with aliases,
        and Jonathan Harker (same surname) stays separate."""
        answers = [
            (0, {"characters": [_cand("Mina Harker", ["Mina", "Mrs. Harker"])]}),
            (1, {"characters": [_cand("Madam Mina"), _cand("Jonathan Harker")]}),
            (2, {"characters": [_cand("Mrs. Harker"), _cand("Mina")]}),
        ]
        merged = ce.merge_characters(answers)
        by_name = {g["name"]: g for g in merged}
        assert set(by_name) == {"Mina Harker", "Jonathan Harker"}
        mina = by_name["Mina Harker"]
        assert set(mina["aliases"]) == {"Mina", "Mrs. Harker", "Madam Mina"}
        assert mina["windows"] == [0, 1, 2]

    def test_ambiguous_surname_does_not_fuse_two_people(self):
        answers = [
            (0, {"characters": [_cand("Mina Harker", ["Harker"])]}),
            (1, {"characters": [_cand("Jonathan Harker", ["Harker"])]}),
            (2, {"characters": [_cand("Harker")]}),
        ]
        names = sorted(g["name"] for g in ce.merge_characters(answers))
        assert names == ["Harker", "Jonathan Harker", "Mina Harker"]

    def test_honorifics_whitespace_and_titles(self):
        answers = [
            (0, {"characters": [_cand("Count\nVasska"), _cand("Dr. Ward")]}),
            (1, {"characters": [_cand("Count Vasska"), _cand("Elias Ward")]}),
            (2, {"characters": [_cand("Miss Nora Vance"), _cand("Nora Vance")]}),
            (3, {"characters": [_cand("Edric Thane", ["the CEL"]), _cand("The CEL")]}),
        ]
        merged = {g["name"]: g for g in ce.merge_characters(answers)}
        assert set(merged) == {
            "Count Vasska",
            "Elias Ward",
            "Nora Vance",
            "Edric Thane",
        }
        assert merged["Elias Ward"]["aliases"] == ["Dr. Ward"]
        assert merged["Edric Thane"]["aliases"] in (["the CEL"], ["The CEL"])

    def test_bare_honorific_is_not_an_alias(self):
        out = ce._normalize_window(
            {"characters": [{"name": "Count Vasska", "aliases": ["Count", "Vasska"]}]}
        )
        assert out["characters"][0]["aliases"] == ["Vasska"]

    def test_narrators_from_style_signals_become_candidates(self):
        answers = [
            (
                0,
                {
                    "characters": [_cand("Lucy Westenra")],
                    "style_signals": {"narrators": ["Jonathan Harker"]},
                },
            )
        ]
        merged = {g["name"]: g for g in ce.merge_characters(answers)}
        assert merged["Jonathan Harker"]["role_hints"] == ["narrator"]

    def test_mentions_count_the_source(self):
        text = "Mina spoke. Mrs. Harker slept. Mina Harker woke. mina is lower."
        answers = [(0, {"characters": [_cand("Mina Harker", ["Mina", "Mrs. Harker"])]})]
        (mina,) = ce.merge_characters(answers, text)
        assert mina["mentions"] == 3


class TestCategoriesAndRoles:
    def test_invalid_category_stored_as_concept(self):
        """AC4: "loc" (a substring of the old category string) -> concept."""
        out = ce._normalize_proposals(
            {
                "canon_entries": [
                    {"name": "A", "category": "loc"},
                    {"name": "B", "category": "on"},
                    {"name": "C", "category": "Location"},
                ]
            }
        )
        assert [e["category"] for e in out["canon_entries"]] == [
            "concept",
            "concept",
            "location",
        ]
        win = ce._normalize_window(
            {"canon_entries": [{"name": "A", "category": "loc"}]}
        )
        assert win["canon_entries"][0]["category"] == "concept"

    @pytest.mark.parametrize(
        "raw,role",
        [
            ("Protagonist", "protagonist"),
            ("villain", "antagonist"),
            ("main protagonist", "protagonist"),
            ("comic relief", "other: comic relief"),
            ("other: the town itself", "other: the town itself"),
            ("", ""),
        ],
    )
    def test_role_vocabulary(self, raw, role):
        assert ce._role(raw) == role

    async def test_commit_stores_invalid_category_as_concept(
        self, client, auth_headers, test_book, test_source, async_session
    ):
        from app.core.orm_models import CanonEntry, ExtractionRun
        from sqlalchemy import select

        run = ExtractionRun(
            book_id=test_book.id,
            source_id=test_source.id,
            user_id=test_book.user_id,
            status="ready",
            proposals={},
        )
        async_session.add(run)
        await async_session.commit()
        r = await client.post(
            f"/api/v1/books/{test_book.id}/extractions/{run.id}/commit",
            json={"canon_entries": [{"name": "Whitby", "category": "loc"}]},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        row = (
            await async_session.execute(
                select(CanonEntry).where(CanonEntry.name == "Whitby")
            )
        ).scalar_one()
        assert row.category == "concept"


class TestCommitFullProposals:
    async def _run(self, async_session, test_book, test_source):
        from app.core.orm_models import ExtractionRun

        run = ExtractionRun(
            book_id=test_book.id,
            source_id=test_source.id,
            user_id=test_book.user_id,
            status="ready",
            proposals={},
        )
        async_session.add(run)
        await async_session.commit()
        return run

    async def test_commit_persists_the_full_entry(
        self, client, auth_headers, test_book, test_source, async_session
    ):
        from app.core.orm_models import Character
        from sqlalchemy import select

        run = await self._run(async_session, test_book, test_source)
        proposal = {
            "name": "Mina Harker",
            "role": "protagonist",
            "description": "A schoolmistress.",
            "goals": "Save Jonathan",
            "arc": "From observer to hunter",
            "relationships": {"Jonathan Harker": "husband"},
            "personality_traits": {"methodical": ""},
            "voice_characteristics": {"diction": "precise"},
            "aliases": ["Mrs. Harker", "Madam Mina"],
            "mentions": 120,
            "windows": 9,
        }
        r = await client.post(
            f"/api/v1/books/{test_book.id}/extractions/{run.id}/commit",
            json={"characters": [proposal]},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        row = (
            await async_session.execute(
                select(Character).where(Character.name == "Mina Harker")
            )
        ).scalar_one()
        await async_session.refresh(row)
        assert row.goals == "Save Jonathan" and row.arc == "From observer to hunter"
        assert row.relationships == {"Jonathan Harker": "husband"}
        assert row.voice_characteristics == {"diction": "precise"}
        assert row.aliases == ["Mrs. Harker", "Madam Mina"]
        versions = (
            await client.get(
                f"/api/v1/books/{test_book.id}/versions/character/{row.id}",
                headers=auth_headers,
            )
        ).json()["versions"]
        v = versions[0]["version_no"]
        snap = (
            await client.get(
                f"/api/v1/books/{test_book.id}/versions/character/{row.id}/{v}",
                headers=auth_headers,
            )
        ).json()
        assert snap["content"]["aliases"] == ["Mrs. Harker", "Madam Mina"]

    async def test_commit_merges_by_alias_and_keeps_author_aliases(
        self, client, auth_headers, test_book, test_source, async_session
    ):
        from app.core.orm_models import Character

        existing = Character(
            book_id=test_book.id,
            user_id=test_book.user_id,
            name="Mina Harker",
            role="protagonist",
            aliases=["Madam Mina"],
            goals="kept",
        )
        jonathan = Character(
            book_id=test_book.id,
            user_id=test_book.user_id,
            name="Jonathan Harker",
            aliases=["Harker"],
        )
        async_session.add_all([existing, jonathan])
        run = await self._run(async_session, test_book, test_source)
        r = await client.post(
            f"/api/v1/books/{test_book.id}/extractions/{run.id}/commit",
            json={
                "characters": [
                    # Named by an existing character's alias -> merges into her.
                    {"name": "Madam Mina", "description": "teacher"},
                    # Lists an existing character's name as an alias -> merges.
                    {"name": "Mrs. Harker", "aliases": ["Mina Harker"]},
                    # Shares only the ambiguous "Harker" -> a new character.
                    {"name": "Quincey Harker", "aliases": ["Harker"]},
                ]
            },
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        result = r.json()["result"]["characters"]
        assert result["updated"] == [str(existing.id), str(existing.id)]
        assert len(result["created"]) == 1
        await async_session.refresh(existing)
        assert existing.description == "teacher"
        assert existing.goals == "kept"  # omitted -> untouched
        assert existing.aliases == ["Madam Mina", "Mrs. Harker"]  # union, kept

    async def test_supplied_null_aliases_clear(
        self, client, auth_headers, test_book, test_source, async_session
    ):
        from app.core.orm_models import Character

        row = Character(
            book_id=test_book.id,
            user_id=test_book.user_id,
            name="Lucy",
            aliases=["Miss Lucy"],
        )
        async_session.add(row)
        run = await self._run(async_session, test_book, test_source)
        r = await client.post(
            f"/api/v1/books/{test_book.id}/extractions/{run.id}/commit",
            json={"characters": [{"name": "Lucy", "aliases": None}]},
            headers=auth_headers,
        )
        assert r.status_code == 200
        await async_session.refresh(row)
        assert row.aliases is None


class TestAliasesHonoured:
    def test_canon_principals_terms_and_render_carry_aliases(self):
        from app.planning.canon import Canon, render_character

        edric = types.SimpleNamespace(
            name="Edric Thane", role="antagonist", aliases=["the CEL"]
        )
        canon = Canon(title="Bored to Undeath", characters=[edric])
        assert "the CEL" in canon.principals()[0]["aliases"]
        assert "the CEL" in canon.canon_terms()
        assert "Also known as: the CEL" in render_character(edric)

    def test_fidelity_counts_an_alias_as_presence(self):
        from app.planning.canon import Canon
        from app.planning.fidelity import principal_recall

        edric = types.SimpleNamespace(
            name="Edric Thane", role="antagonist", aliases=["the CEL"]
        )
        recall, present, _ = principal_recall(
            "The CEL signs the decree.",
            Canon(title="t", characters=[edric]).principals(),
        )
        assert recall == 1.0 and present == ["Edric Thane"]

    async def test_character_api_records_aliases(self, client, auth_headers, test_book):
        r = await client.post(
            "/api/v1/characters/",
            json={
                "name": "Edric Thane",
                "book_id": str(test_book.id),
                "aliases": ["the CEL"],
            },
            headers=auth_headers,
        )
        cid = r.json()["id"]
        got = (
            await client.get(f"/api/v1/characters/{cid}", headers=auth_headers)
        ).json()
        assert got["aliases"] == ["the CEL"]
        await client.patch(
            f"/api/v1/characters/{cid}",
            json={"aliases": ["the CEL", "Chief Executive Lich"]},
            headers=auth_headers,
        )
        got = (
            await client.get(f"/api/v1/characters/{cid}", headers=auth_headers)
        ).json()
        assert got["aliases"] == ["the CEL", "Chief Executive Lich"]


class TestPreflight:
    def test_first_chapters_cuts_on_a_chapter_boundary(self):
        from app.parsing.extraction_plan import chapter_starts, first_chapters

        text = "Preface.\n\nCHAPTER I\n\nOne.\n\nChapter 2: Two\n\nTwo.\n\nCHAPTER III\n\nThree."
        assert len(chapter_starts(text)) == 3
        assert (
            first_chapters(text, 2)
            == "Preface.\n\nCHAPTER I\n\nOne.\n\nChapter 2: Two\n\nTwo."
        )
        assert first_chapters(text, 5) == text

    def test_estimate_counts_windows_and_requests(self):
        est = ce.plan_estimate(_novel())
        assert 9 <= est["windows"] <= 12
        assert est["requests"] == est["windows"] + est["reduce_calls"] + 1
        assert est["estimated_tokens"] > 120_000

    async def _setup(self, monkeypatch, async_session, test_source, text, limit):
        from dataclasses import replace

        import app.llm.tier as tier_mod

        base = tier_mod.get_tier()
        monkeypatch.setattr(
            tier_mod, "get_tier", lambda: replace(base, daily_token_limit=limit)
        )
        test_source.content_text = text
        await async_session.commit()

    async def test_refuses_with_first_n_chapters_alternative(
        self, client, auth_headers, async_session, test_book, test_source, monkeypatch
    ):
        from sqlalchemy import select

        from app.core.orm_models import Job

        chapters = [
            f"CHAPTER {i}\n\n"
            + "\n\n".join("Mina Harker walks. " * 40 for _ in range(30))
            for i in range(1, 21)
        ]
        await self._setup(
            monkeypatch, async_session, test_source, "\n\n".join(chapters), 20_000
        )
        url = f"/api/v1/books/{test_book.id}/sources/{test_source.id}/extract"
        r = await client.post(url, headers=auth_headers)
        assert r.status_code == 429, r.text
        pre = r.json()["detail"]["preflight"]
        assert pre["fits"] is False and pre["max_chapters"]
        assert pre["estimated_tokens"] > pre["capacity_tokens"]
        assert "first" in r.json()["detail"]["message"]
        assert (
            not (
                await async_session.execute(
                    select(Job).where(Job.kind == "extract_canon")
                )
            )
            .scalars()
            .all()
        )

        r = await client.post(
            url, json={"max_chapters": pre["max_chapters"]}, headers=auth_headers
        )
        assert r.status_code == 202, r.text
        assert r.json()["preflight"]["fits"] is True
        job = (
            await async_session.execute(select(Job).where(Job.kind == "extract_canon"))
        ).scalar_one()
        assert job.payload["max_chapters"] == pre["max_chapters"]

    async def test_enqueue_returns_the_estimate(
        self, client, auth_headers, async_session, test_book, test_source, monkeypatch
    ):
        await self._setup(
            monkeypatch, async_session, test_source, "Mina Harker walks.", 200_000
        )
        r = await client.post(
            f"/api/v1/books/{test_book.id}/sources/{test_source.id}/extract",
            headers=auth_headers,
        )
        assert r.status_code == 202
        pre = r.json()["preflight"]
        assert pre["windows"] == 1 and pre["fits"] is True and pre["requests"] >= 2

    async def test_paste_too_large_keeps_the_source_and_offers_alternative(
        self, client, auth_headers, test_book, monkeypatch
    ):
        from dataclasses import replace

        import app.llm.tier as tier_mod

        base = tier_mod.get_tier()
        monkeypatch.setattr(
            tier_mod, "get_tier", lambda: replace(base, daily_token_limit=10_000)
        )
        text = "\n\n".join(
            f"CHAPTER {i}\n\n" + "Mina Harker walks. " * 800 for i in range(1, 11)
        )
        r = await client.post(
            "/api/v1/sources/paste",
            json={"title": "Big", "content_text": text, "book_id": str(test_book.id)},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["extraction_run_id"] is None
        assert body["preflight"]["fits"] is False
        assert body["format"] == "paste"


class TestHandler:
    async def test_handler_honours_max_chapters_and_records_progress(
        self, async_session, test_book, test_source, monkeypatch
    ):
        import app.jobs.handlers as handlers_mod
        from app.core.orm_models import ExtractionRun

        class _Ctx:
            async def __aenter__(self):
                return async_session

            async def __aexit__(self, *a):
                return False

        fake = CountingLLM()
        monkeypatch.setattr(ce, "get_llm_client", lambda: fake)
        monkeypatch.setattr(handlers_mod, "get_async_session", lambda: _Ctx())
        test_source.content_text = (
            "CHAPTER I\n\nMina Harker arrives.\n\nCHAPTER II\n\nLucy Westenra waits."
        )
        run = ExtractionRun(
            book_id=test_book.id,
            source_id=test_source.id,
            user_id=test_book.user_id,
            status="pending",
        )
        async_session.add(run)
        await async_session.commit()
        await handlers_mod._run_extract_canon(
            {
                "run_id": str(run.id),
                "source_id": str(test_source.id),
                "user_id": str(test_book.user_id),
                "max_chapters": 1,
            }
        )
        await async_session.refresh(run)
        assert run.status == "ready"
        assert [c["name"] for c in run.proposals["characters"]] == ["Mina Harker"]
        assert run.proposals["stats"]["max_chapters"] == 1
        assert run.progress["phase"] == "done"
        assert "checkpoint" not in run.progress


class TestParserNormalisation:
    def test_html_three_paragraphs(self, tmp_path):
        """AC6: an HTML upload of a three-paragraph document -> three paragraphs."""
        from app.parsing.parser import DocumentParser

        html = (
            "<html><head><title>T</title><style>p{}</style></head><body>\n"
            "<p>The first\nparagraph, wrapped\nin the source.</p>\n"
            "<p>The <em>second</em> one.</p>\n<div>The third.</div>\n</body></html>"
        )
        f = tmp_path / "doc.html"
        f.write_text(html, encoding="utf-8")
        parser = DocumentParser()
        text = parser.parse_document(str(f))
        assert text.split("\n\n") == [
            "The first paragraph, wrapped in the source.",
            "The second one.",
            "The third.",
        ]
        assert parser.get_paragraph_count(text) == 3
        assert parser.format_of(str(f)) == "html"
        assert parser.format_of("x.htm") == "html"

    def test_html_br_is_a_line_break_not_a_paragraph(self):
        from app.parsing.parser import html_to_paragraphs

        assert html_to_paragraphs("<p>Dear Lucy,<br>I write.</p><p>Mina</p>") == (
            "Dear Lucy,\nI write.\n\nMina"
        )

    def test_docx_paragraphs(self, tmp_path):
        import docx

        from app.parsing.parser import DocumentParser

        d = docx.Document()
        for t in ("One.", "", "Two  spaced.", "Three."):
            d.add_paragraph(t)
        f = tmp_path / "doc.docx"
        d.save(str(f))
        assert DocumentParser().parse_document(str(f)).split("\n\n") == [
            "One.",
            "Two spaced.",
            "Three.",
        ]

    def test_pdf_lines_rejoin_into_paragraphs(self):
        from app.parsing.parser import pdf_pages_to_paragraphs

        page1 = (
            "It was a dark and stormy night and the wind\n"
            "howled through the trees of the old forest where\n"
            "nobody went.\n"
            "The next morning Mina rose early and walked to\n"
            "the cliff above the har-\n"
        )
        page2 = "bour, where the ships lay at anchor in the grey\nlight.\n"
        paras = pdf_pages_to_paragraphs([page1, page2]).split("\n\n")
        assert len(paras) == 2
        assert paras[0].endswith("nobody went.")
        assert "harbour, where" in paras[1]


class TestNarratorVoice:
    TEXT = (
        "CHAPTER I\n\n"
        "JONATHAN HARKER’S JOURNAL\n\n"
        "_3 May._--I left Munich at 8:35 P. M. My journey was long.\n\n"
        "The castle stood on the crag.\n\n"
        "_Letter, Mina Murray to Lucy Westenra._\n\n"
        "My dearest Lucy, I have been so busy.\n\n"
        "CHAPTER II\n\n"
        "The Count rose from his coffin. I do not belong to him, said nobody."
    )
    CAST = [
        {"name": "Jonathan Harker", "aliases": []},
        {"name": "Mina Harker", "aliases": ["Mina Murray"]},
        {"name": "Lucy Westenra", "aliases": []},
    ]

    def test_diary_and_letter_narration_attributed(self):
        from app.parsing.character_extractor import CharacterExtractor

        cx = CharacterExtractor()
        jon = cx.extract_narration(self.TEXT, "Jonathan Harker", cast=self.CAST)
        assert [c["text"][:12] for c in jon] == ["_3 May._--I "]
        # The name gate alone found nothing for the diarist.
        assert cx.extract_character_content(self.TEXT, "Jonathan Harker") == []
        mina = cx.extract_narration(
            self.TEXT, "Mina Harker", aliases=["Mina Murray"], cast=self.CAST
        )
        texts = [c["text"] for c in mina]
        assert "My dearest Lucy, I have been so busy." in texts
        # The chapter heading ends the section; Lucy is the addressee, not the voice.
        assert not any("coffin" in t for t in texts)
        assert cx.extract_narration(self.TEXT, "Lucy Westenra", cast=self.CAST) == []

    def test_ambiguous_heading_is_not_attributed(self):
        from app.parsing.character_extractor import CharacterExtractor

        text = "DR. WARD’S DIARY\n\nI could not sleep."
        cast = [
            {"name": "Elias Ward", "aliases": []},
            {"name": "John Ward", "aliases": []},
        ]
        cx = CharacterExtractor()
        assert cx.extract_narration(text, "Elias Ward", cast=cast) == []
        assert len(cx.extract_narration(text, "Elias Ward", cast=cast[:1])) == 1

    def test_aliases_match_case_sensitively(self):
        from app.parsing.character_extractor import CharacterExtractor

        text = "Will went home.\n\nI will go.\n\nMrs. Harker smiled."
        got = CharacterExtractor().extract_narration(
            text, "William Dane", aliases=["Will", "Mrs. Harker"]
        )
        assert [c["text"] for c in got] == ["Will went home.", "Mrs. Harker smiled."]

    def test_single_narrator_novel_without_headings(self):
        from app.parsing.character_extractor import CharacterExtractor

        text = 'Call me Ishmael. I went to sea.\n\nThe whale was white.\n\n"I am here," said Ahab.'
        cx = CharacterExtractor()
        got = cx.extract_narration(text, "Ishmael", is_narrator=True)
        assert [c["text"] for c in got] == ["Call me Ishmael. I went to sea."]
        assert cx.extract_narration(text, "Ishmael") == []

    async def test_index_job_uses_narration(
        self, async_session, test_book, test_source, monkeypatch
    ):
        import app.parsing.pipeline as pipeline
        from app.core.orm_models import Character

        class _Ctx:
            async def __aenter__(self):
                return async_session

            async def __aexit__(self, *a):
                await async_session.commit()
                return False

        indexed: dict = {}

        class Store:
            async def delete_source_chunks(self, *a):
                return None

            async def index_chunks(self, *, character_name, chunks, **kw):
                indexed[character_name] = chunks

        test_source.content_text = self.TEXT
        jon = Character(
            book_id=test_book.id, user_id=test_book.user_id, name="Jonathan Harker"
        )
        async_session.add(jon)
        await async_session.commit()
        monkeypatch.setattr(pipeline, "get_async_session", lambda: _Ctx())
        monkeypatch.setattr(pipeline, "get_chunk_store", lambda: Store())
        await pipeline.index_characters_voice(
            test_source.id, test_book.id, test_book.user_id, [str(jon.id)]
        )
        assert len(indexed["Jonathan Harker"]) == 1
