"""Ensemble scene loop (Phase 8): actions-first, convergence, tiering."""

import types

import pytest

from app.core.config import settings
from app.orchestration import ensemble as ens

pytestmark = pytest.mark.unit


class FakeLLM:
    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[str] = []

    async def generate(self, messages, *, purpose, **kw):
        self.calls.append(purpose)
        return types.SimpleNamespace(
            text=self.responses.get(purpose, "{}"), tokens_in=1, tokens_out=1
        )


class TestProposalContract:
    def test_actions_required_nonempty(self):
        p = ens._normalize_proposal({"intent": "x", "actions": [], "lines": []}, "Milo")
        assert p["actions"]  # synthesized rather than left inert
        assert p["character"] == "Milo"

    def test_actions_preserved(self):
        p = ens._normalize_proposal(
            {"actions": ["nods slowly", ""], "lines": ["Hi"], "interiority": "wary"},
            "Zara",
        )
        assert p["actions"] == ["nods slowly"]
        assert p["lines"] == ["Hi"] and p["interiority"] == "wary"


class TestEditorEnforcesActing:
    async def test_present_but_not_acting_becomes_blocking(self, monkeypatch):
        # editor says satisfied but lists NO one acting -> both present chars get
        # a blocking objection (the actions-first contract is enforced in code).
        fake = FakeLLM(
            {
                "ensemble:review": '{"satisfied":true,"arc_fit":0.9,"beat_coverage":0.9,'
                '"characters_acting":[],"objections":[]}'
            }
        )
        monkeypatch.setattr(ens, "get_llm_client", lambda: fake)
        review = await ens.editor_review("draft", "", "beat", ["Milo", "Zara"], None)
        blocking = ens._blocking(review["objections"])
        assert {o["character"] for o in blocking} == {"Milo", "Zara"}
        assert ens._converged(review) is False  # blocking objections => not converged


class TestConvergence:
    def test_converged_requires_all_three(self):
        assert (
            ens._converged({"satisfied": True, "arc_fit": 0.8, "objections": []})
            is True
        )
        assert (
            ens._converged({"satisfied": False, "arc_fit": 0.9, "objections": []})
            is False
        )
        assert (
            ens._converged({"satisfied": True, "arc_fit": 0.5, "objections": []})
            is False
        )
        assert (
            ens._converged(
                {
                    "satisfied": True,
                    "arc_fit": 0.9,
                    "objections": [{"severity": "blocking"}],
                }
            )
            is False
        )


class TestRunEnsemble:
    def _wire(self, monkeypatch, review_json, tier_name="free"):
        monkeypatch.setattr(settings, "LLM_TIER", tier_name)
        fake = FakeLLM(
            {
                "ensemble:proposal": '{"intent":"i","actions":["nods"],"lines":["Hi"]}',
                "ensemble:narration": 'Milo nods. "Hi," he says. Zara watches him.',
                "ensemble:review": review_json,
            }
        )
        monkeypatch.setattr(ens, "get_llm_client", lambda: fake)

        async def _no_chars(names, **kw):
            return {}  # character=None path -> no vector store needed

        monkeypatch.setattr(ens, "load_characters_for_book", _no_chars)
        return fake

    async def test_converges_in_one_round_free(self, monkeypatch):
        fake = self._wire(
            monkeypatch,
            '{"satisfied":true,"arc_fit":0.9,"beat_coverage":0.9,'
            '"characters_acting":["Milo","Zara"],"objections":[]}',
        )
        out = await ens.run_ensemble(
            scene_request={"characters": ["Milo", "Zara"], "setting": "office"},
            beat_description="They meet.",
            user_id=None,
            book_id=None,
            canon_context="",
        )
        assert out["rounds"] == 1
        assert out["evaluation"]["converged"] is True
        assert fake.calls.count("ensemble:proposal") == 2  # one per character

    async def test_tier_caps_agent_count(self, monkeypatch):
        fake = self._wire(
            monkeypatch,
            '{"satisfied":true,"arc_fit":0.9,"beat_coverage":0.9,'
            '"characters_acting":["A","B","C"],"objections":[]}',
        )
        await ens.run_ensemble(
            scene_request={"characters": ["A", "B", "C", "D", "E"], "setting": "x"},
            beat_description="crowd",
            user_id=None,
            book_id=None,
            canon_context="",
        )
        # free tier max_agents == 3 -> only 3 proposals, rest are context-only
        assert fake.calls.count("ensemble:proposal") == 3

    async def test_anti_oscillation_stops_and_keeps_best(self, monkeypatch):
        # paid tier (2 rounds). Editor never satisfied and objections don't shrink
        # -> stop after round 2, keep best, converged False (never hard-fail).
        self._wire(
            monkeypatch,
            '{"satisfied":false,"arc_fit":0.5,"beat_coverage":0.5,'
            '"characters_acting":[],"objections":[]}',
            tier_name="paid",
        )
        out = await ens.run_ensemble(
            scene_request={"characters": ["Milo", "Zara"], "setting": "x"},
            beat_description="tense",
            user_id=None,
            book_id=None,
            canon_context="",
        )
        assert out["rounds"] == 2  # ran the second round, then stopped
        assert out["evaluation"]["converged"] is False
        assert out["prose"]  # kept the best draft anyway


class TestModeGating:
    async def _book_chapter(self, client, auth_headers):
        book = (
            await client.post(
                "/api/v1/books/",
                json={"title": "B", "synopsis": "s"},
                headers=auth_headers,
            )
        ).json()
        chapter = (
            await client.post(
                f"/api/v1/books/{book['id']}/chapters",
                json={"title": "C1"},
                headers=auth_headers,
            )
        ).json()
        return chapter["id"]

    async def _enqueued_kind(self, async_session):
        from sqlalchemy import select

        from app.core.orm_models import Job

        jobs = (
            (
                await async_session.execute(
                    select(Job).where(Job.kind.like("generate_%_scene"))
                )
            )
            .scalars()
            .all()
        )
        return [j.kind for j in jobs]

    async def test_free_tier_falls_back_to_prose(
        self, client, auth_headers, async_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "LLM_TIER", "free")
        chapter_id = await self._book_chapter(client, auth_headers)
        r = await client.post(
            f"/api/v1/books/chapters/{chapter_id}/scenes/generate",
            json={
                "characters": ["Milo"],
                "scene_description": "A quiet moment at the desk.",
                "setting": "office",
                "emotional_tone": "wry",
                "mode": "ensemble",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        assert await self._enqueued_kind(async_session) == ["generate_prose_scene"]

    async def test_paid_tier_uses_ensemble(
        self, client, auth_headers, async_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "LLM_TIER", "paid")
        chapter_id = await self._book_chapter(client, auth_headers)
        r = await client.post(
            f"/api/v1/books/chapters/{chapter_id}/scenes/generate",
            json={
                "characters": ["Milo"],
                "scene_description": "A quiet moment at the desk.",
                "setting": "office",
                "emotional_tone": "wry",
                "mode": "ensemble",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        assert await self._enqueued_kind(async_session) == ["generate_ensemble_scene"]
