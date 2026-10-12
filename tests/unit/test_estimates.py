"""SPEC-12: one estimator per action; the estimate endpoint and /usage."""

import pytest

from app.core import estimates
from app.core.config import settings

pytestmark = pytest.mark.unit


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


def test_outline_staged_vs_single():
    staged = estimates.outline(canon_chars=20_000)
    single = estimates.outline(canon_chars=20_000, staged=False)
    assert staged.calls == 6 and single.calls == 1
    assert staged.tokens > single.tokens


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
        r = await client.get("/api/v1/usage", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert "by_purpose_30d" in body and "requests_left_today" in body


async def _none():
    return None
