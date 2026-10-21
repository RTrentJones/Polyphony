"""SPEC-12: one estimator per action; the estimate endpoint and /usage."""

import pytest

from app.core import estimates
from app.core.config import settings

pytestmark = pytest.mark.unit
FLASH, LITE, STUB = "gemini-3.6-flash", "gemini-3.5-flash-lite", "stub-default"


def test_free_tier_ensemble_estimate_reports_the_mode_that_runs(monkeypatch):
    monkeypatch.setattr(settings, "LLM_TIER", "free")
    est = estimates.scene(
        canon_chars=20_000,
        cast_context_chars=4_000,
        target_words=800,
        mode="ensemble",
        cast_size=3,
    )
    assert est.mode == "prose" and est.degraded_from == "ensemble"
    assert est.calls == 1 + 5 and est.notes


def test_paid_ensemble_costs_more_calls_than_prose(monkeypatch):
    monkeypatch.setattr(settings, "LLM_TIER", "paid")
    prose = estimates.scene(
        canon_chars=20_000,
        cast_context_chars=4_000,
        target_words=800,
        mode="prose",
        cast_size=3,
    )
    ens = estimates.scene(
        canon_chars=20_000,
        cast_context_chars=4_000,
        target_words=800,
        mode="ensemble",
        cast_size=3,
    )
    assert ens.mode == "ensemble" and ens.calls > prose.calls


def test_affordable_is_the_longest_prefix_that_fits():
    """SPEC-34: the batch queues the scenes that fit and defers the rest."""
    four = [estimates.Estimate("scene", "prose", 1, 30, 10)] * 4
    assert estimates.affordable(four, 130) == 3
    assert estimates.affordable(four, 39) == 0
    assert estimates.affordable(four, 160) == 4


def test_outline_staged_vs_single():
    staged = estimates.outline(canon_chars=20_000, source_chars=0)
    single = estimates.outline(canon_chars=20_000, source_chars=0, staged=False)
    assert staged.calls == 6 and single.calls == 1
    assert staged.tokens > single.tokens


def test_the_outline_estimate_counts_the_source_check():
    """(F) SPEC-73: a sourced book's staged outline is one call more, reading
    the sources; single mode makes no check."""
    bare = estimates.outline(canon_chars=20_000, source_chars=0)
    sourced = estimates.outline(canon_chars=20_000, source_chars=40_000)
    assert sourced.calls == bare.calls + 1
    assert sourced.input_tokens - bare.input_tokens > 10_000
    single = dict(canon_chars=20_000, staged=False)
    assert estimates.outline(**single, source_chars=40_000).calls == 1


def test_continuity_scales_with_prose():
    small = estimates.continuity(fact_sheet_chars=10_000, prose_words=1_000)
    big = estimates.continuity(fact_sheet_chars=10_000, prose_words=10_000)
    assert small.calls == 1 and big.calls == 4 and big.tokens > small.tokens


class TestEndpoints:
    async def test_estimate_scene_for_a_book(self, client, auth_headers, monkeypatch):
        monkeypatch.setattr(estimates, "requests_left_today", _none)
        book = (
            await client.post(
                "/api/v1/books/",
                json={"title": "B", "synopsis": "s" * 4000},
                headers=auth_headers,
            )
        ).json()
        c = (
            await client.post(
                "/api/v1/characters/",
                json={"name": "Milo", "book_id": book["id"]},
                headers=auth_headers,
            )
        ).json()
        r = await client.post(
            "/api/v1/estimate",
            json={"action": "scene", "book_id": book["id"], "character_ids": [c["id"]]},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["calls"] >= 2 and body["tokens"] > 1000
        assert body["allowed"] is True and body["remaining_tokens_24h"] > 0

    async def test_estimate_refuses_over_budget(
        self, client, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(estimates, "requests_left_today", _none)
        monkeypatch.setattr(settings, "USER_DAILY_TOKEN_LIMIT", 100)
        book = (
            await client.post(
                "/api/v1/books/",
                json={"title": "B", "synopsis": "s" * 4000},
                headers=auth_headers,
            )
        ).json()
        r = await client.post(
            "/api/v1/estimate",
            json={"action": "outline", "book_id": book["id"]},
            headers=auth_headers,
        )
        assert r.json()["allowed"] is False and "budget" in r.json()["reason"]

    async def test_other_users_book_is_404(self, client, auth_headers):
        import uuid

        r = await client.post(
            "/api/v1/estimate",
            json={"action": "outline", "book_id": str(uuid.uuid4())},
            headers=auth_headers,
        )
        assert r.status_code == 404

    async def test_usage_reports_requests_and_purposes(
        self, client, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(estimates, "requests_left_today", _none)
        monkeypatch.setattr(settings, "LLM_MAX_RPD", None)
        r = await client.get("/api/v1/usage", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert "by_purpose_30d" in body
        per_day = {m: v["per_day"] for m, v in body["models"].items()}
        assert list(per_day.items()) == [(LITE, 500), (FLASH, 20)]  # U1 (F)


async def _none(*_):
    return None


class TestSceneModeReporting:
    async def _chapter_and_cast(self, client, auth_headers):
        book = (
            await client.post(
                "/api/v1/books/", json={"title": "B"}, headers=auth_headers
            )
        ).json()
        ch = (
            await client.post(
                f"/api/v1/books/{book['id']}/chapters",
                json={"title": "C"},
                headers=auth_headers,
            )
        ).json()
        c = (
            await client.post(
                "/api/v1/characters/",
                json={"name": "Milo", "book_id": book["id"]},
                headers=auth_headers,
            )
        ).json()
        return ch, c

    async def test_free_tier_reports_the_prose_fallback(
        self, client, auth_headers, monkeypatch
    ):
        monkeypatch.setattr(settings, "LLM_TIER", "free")
        ch, c = await self._chapter_and_cast(client, auth_headers)
        r = await client.post(
            f"/api/v1/books/chapters/{ch['id']}/scenes/generate",
            json={
                "character_ids": [c["id"]],
                "scene_description": "A quiet moment here.",
                "setting": "office",
                "emotional_tone": "wry",
                "mode": "ensemble",
            },
            headers=auth_headers,
        )
        body = r.json()
        assert body["mode_requested"] == "ensemble" and body["mode_used"] == "prose"
        assert "free tier" in body["mode_reason"]

    async def test_the_scene_records_its_model_and_unknown_is_422(
        self, client, auth_headers, monkeypatch
    ):
        ch, c = await self._chapter_and_cast(client, auth_headers)
        url = f"/api/v1/books/chapters/{ch['id']}/scenes/generate"
        body = {"character_ids": [c["id"]], "setting": "s", "emotional_tone": "t"}
        body["scene_description"] = "A quiet moment here."
        client.headers.update(auth_headers)
        cases = ("nope", None), (FLASH, FLASH), (None, LITE), (STUB, STUB)
        for m, want in cases:  # STUB: a provider without rpd allows its default
            monkeypatch.setattr(settings, "LLM_PROVIDER", ("gemini", "stub")[m == STUB])
            r = await client.post(url, json={**body, "model": m})
            assert r.status_code == (422 if want is None else 200), r.text
            if want:  # U1 (F): the scene payload exposes the model that wrote it
                s = await client.get(f"/api/v1/scenes/{r.json()['scene_id']}")
                assert s.json()["scene_request"]["model"] == want


async def test_active_jobs_list_is_owner_scoped(
    client, auth_headers, async_session, test_user
):
    from app.jobs import repository as jobs_repo

    await jobs_repo.enqueue(async_session, kind="k", payload={}, user_id=test_user.id)
    await async_session.commit()
    r = await client.get("/api/v1/jobs", headers=auth_headers)
    assert r.status_code == 200 and len(r.json()["jobs"]) == 1
    assert r.json()["jobs"][0]["status"] == "queued"
