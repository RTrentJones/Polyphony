"""SPEC-05: rate limits keyed on the real client and the user, not the tunnel."""

import uuid
from unittest.mock import patch

import httpx
import pytest
from starlette.requests import Request

from app.core import rate_limit
from app.core.config import settings
from app.core.rate_limit import (
    ROUTE_CLASSES,
    class_limit,
    client_ip,
    limiter,
    user_or_ip,
)
from app.core.security import create_access_token, get_password_hash

TUNNEL = "127.0.0.1"  # a trusted peer (loopback is in the default CIDRs)
STRANGER = "203.0.113.7"  # an untrusted peer (TEST-NET-3)


def _request(peer: str, headers: dict | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": raw,
            "client": (peer, 5555),
        }
    )


@pytest.fixture
def make_client(async_session):
    """Factory: an in-loop ASGI client whose TCP peer is `peer`."""
    from app.core.database import get_db
    from app.main import app

    async def override_get_db():
        yield async_session
        await async_session.commit()

    app.dependency_overrides[get_db] = override_get_db

    def _make(peer: str) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app, client=(peer, 5555))
        c = httpx.AsyncClient(transport=transport, base_url="http://test")
        return c

    yield _make
    app.dependency_overrides.pop(get_db, None)


async def _close(*clients):
    for c in clients:
        await c.aclose()


def _login(client, email, password="wrong-password-123", cf_ip=None):
    headers = {"CF-Connecting-IP": cf_ip} if cf_ip else {}
    return client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
        headers=headers,
    )


# --- key functions -----------------------------------------------------------


class TestClientIp:
    def test_trusted_peer_header_is_the_client(self):
        req = _request(TUNNEL, {"CF-Connecting-IP": "198.51.100.1"})
        assert client_ip(req) == "198.51.100.1"

    def test_docker_bridge_peer_is_trusted(self):
        req = _request("172.18.0.5", {"CF-Connecting-IP": "198.51.100.1"})
        assert client_ip(req) == "198.51.100.1"

    def test_untrusted_peer_header_is_ignored(self):
        req = _request(STRANGER, {"CF-Connecting-IP": "198.51.100.1"})
        assert client_ip(req) == STRANGER

    def test_malformed_header_falls_back_to_peer(self):
        req = _request(TUNNEL, {"CF-Connecting-IP": "not-an-ip"})
        assert client_ip(req) == TUNNEL

    def test_no_header_is_the_peer(self):
        assert client_ip(_request(TUNNEL)) == TUNNEL


class TestUserOrIp:
    def test_valid_token_keys_on_the_user(self):
        uid = str(uuid.uuid4())
        token = create_access_token({"sub": uid})
        req = _request(TUNNEL, {"Authorization": f"Bearer {token}"})
        assert user_or_ip(req) == f"user:{uid}"

    def test_forged_token_keys_on_the_ip(self):
        req = _request(STRANGER, {"Authorization": "Bearer not.a.jwt"})
        assert user_or_ip(req) == f"ip:{STRANGER}"

    def test_anonymous_keys_on_the_real_client_ip(self):
        req = _request(TUNNEL, {"CF-Connecting-IP": "198.51.100.9"})
        assert user_or_ip(req) == "ip:198.51.100.9"


# --- acceptance 1: login buckets per real client IP --------------------------


class TestLoginPerClientIp:
    async def test_distinct_cf_ips_from_the_tunnel_get_distinct_buckets(
        self, make_client
    ):
        c = make_client(TUNNEL)
        try:
            for _ in range(5):
                assert (
                    await _login(c, "a@example.com", cf_ip="198.51.100.1")
                ).status_code == 401
            blocked = await _login(c, "a@example.com", cf_ip="198.51.100.1")
            assert blocked.status_code == 429
            assert int(blocked.headers["Retry-After"]) >= 1
            # Another real client behind the same tunnel is not locked out.
            assert (
                await _login(c, "a@example.com", cf_ip="198.51.100.2")
            ).status_code == 401
        finally:
            await _close(c)

    async def test_header_from_an_untrusted_peer_is_ignored(self, make_client):
        c = make_client(STRANGER)
        try:
            for i in range(5):
                r = await _login(c, "b@example.com", cf_ip=f"198.51.100.{i + 10}")
                assert r.status_code == 401
            # Rotating the header does not mint new buckets: the peer is the key.
            r = await _login(c, "b@example.com", cf_ip="198.51.100.99")
            assert r.status_code == 429
            assert "Retry-After" in r.headers
        finally:
            await _close(c)


# --- acceptance 2: authenticated users each get their own default bucket -----


class TestPerUserDefaultBucket:
    async def test_two_users_do_not_share_the_default_limit(
        self, make_client, async_session, test_user
    ):
        from app.core.orm_models import User

        other = User(
            email="other@example.com",
            hashed_password=get_password_hash("otherpassword123"),
        )
        async_session.add(other)
        await async_session.commit()
        await async_session.refresh(other)

        a = {
            "Authorization": f"Bearer {create_access_token({'sub': str(test_user.id)})}"
        }
        b = {"Authorization": f"Bearer {create_access_token({'sub': str(other.id)})}"}

        c = make_client(TUNNEL)  # both arrive through the same tunnel
        try:
            per_minute = settings.RATE_LIMIT_PER_MINUTE
            for _ in range(per_minute):
                assert (await c.get("/api/v1/books/", headers=a)).status_code == 200
            blocked = await c.get("/api/v1/books/", headers=a)
            assert blocked.status_code == 429
            assert int(blocked.headers["Retry-After"]) >= 1
            # The other user, same peer, same route: untouched.
            assert (await c.get("/api/v1/books/", headers=b)).status_code == 200
        finally:
            await _close(c)


# --- acceptance 3: per-email login limit -------------------------------------


class TestPerEmailLoginLimit:
    async def test_failed_logins_from_many_ips_trip_only_that_email(
        self, make_client, monkeypatch
    ):
        # Six failures against a five-per-hour budget (production: 20/hour).
        monkeypatch.setattr(settings, "LOGIN_EMAIL_RATE_LIMIT", "5/hour")
        c = make_client(TUNNEL)
        try:
            for i in range(5):
                r = await _login(c, "victim@example.com", cf_ip=f"198.51.100.{i + 1}")
                assert r.status_code == 401
            r = await _login(c, "Victim@Example.com", cf_ip="198.51.100.6")
            assert r.status_code == 429
            assert int(r.headers["Retry-After"]) >= 1
            # Another account from yet another IP is unaffected.
            r = await _login(c, "bystander@example.com", cf_ip="198.51.100.7")
            assert r.status_code == 401
        finally:
            await _close(c)

    async def test_attacker_cannot_lock_the_owner_out_of_a_known_address(
        self, make_client, monkeypatch, test_user
    ):
        monkeypatch.setattr(settings, "LOGIN_EMAIL_RATE_LIMIT", "3/hour")
        c = make_client(TUNNEL)
        try:
            home = "203.0.113.10"
            r = await _login(c, test_user.email, "testpassword123", cf_ip=home)
            assert r.status_code == 200
            for i in range(3):  # the attacker spends the account's budget
                r = await _login(
                    c, test_user.email, "wrong", cf_ip=f"198.51.100.{i + 1}"
                )
                assert r.status_code == 401
            r = await _login(c, test_user.email, "wrong", cf_ip="198.51.100.9")
            assert r.status_code == 429  # still blocks guessing from new addresses
            r = await _login(c, test_user.email, "testpassword123", cf_ip=home)
            assert r.status_code == 200  # the owner is not locked out
        finally:
            await _close(c)

    async def test_successful_logins_do_not_spend_the_budget(
        self, make_client, monkeypatch, test_user
    ):
        monkeypatch.setattr(settings, "LOGIN_EMAIL_RATE_LIMIT", "2/hour")
        c = make_client(TUNNEL)
        try:
            for i in range(4):
                r = await _login(
                    c, test_user.email, "testpassword123", cf_ip=f"198.51.100.{i + 1}"
                )
                assert r.status_code == 200
        finally:
            await _close(c)


class TestPerEmailLockoutDecays:
    """The per-email lockout is short, grows per repeated breach, caps low, and
    forgets — it cannot hold an owner out indefinitely (SPEC-05)."""

    def _trip(self, email, n):
        for _ in range(n):
            rate_limit.login_email_record_failure(email)

    def test_lockout_doubles_and_caps(self, monkeypatch):
        monkeypatch.setattr(settings, "LOGIN_EMAIL_LOCKOUT_SECONDS", 60)
        monkeypatch.setattr(settings, "LOGIN_EMAIL_LOCKOUT_MAX_SECONDS", 900)
        assert [rate_limit.lockout_seconds(n) for n in range(1, 7)] == [
            60,
            120,
            240,
            480,
            900,
            900,
        ]
        assert rate_limit.lockout_seconds(10_000) == 900

    def test_lockout_expires_and_the_budget_refills(self, monkeypatch):
        clock = [1_000_000.0]
        monkeypatch.setattr(rate_limit.time, "time", lambda: clock[0])
        monkeypatch.setattr(settings, "LOGIN_EMAIL_RATE_LIMIT", "3/hour")
        monkeypatch.setattr(settings, "LOGIN_EMAIL_LOCKOUT_SECONDS", 60)
        monkeypatch.setattr(settings, "LOGIN_EMAIL_LOCKOUT_MAX_SECONDS", 300)
        email = "victim@example.com"

        self._trip(email, 2)
        assert rate_limit.login_email_check(email) is None  # budget left
        self._trip(email, 1)
        assert rate_limit.login_email_check(email) == 60  # first breach: short

        clock[0] += 61
        assert rate_limit.login_email_check(email) is None  # over, not an hour
        self._trip(email, 2)
        assert rate_limit.login_email_check(email) is None  # a fresh budget
        self._trip(email, 1)
        assert rate_limit.login_email_check(email) == 120  # repeat: doubled

        # an attacker re-tripping forever is held at the cap, never beyond
        for _ in range(10):
            clock[0] += 400
            self._trip(email, 3)
            assert rate_limit.login_email_check(email) <= 300

    def test_lockout_table_is_bounded_against_an_email_spray(self, monkeypatch):
        clock = [3_000_000.0]
        monkeypatch.setattr(rate_limit.time, "time", lambda: clock[0])
        monkeypatch.setattr(settings, "LOGIN_EMAIL_RATE_LIMIT", "1/hour")
        monkeypatch.setattr(settings, "LOGIN_EMAIL_LOCKOUT_SECONDS", 60)
        monkeypatch.setattr(settings, "LOGIN_EMAIL_BREACH_RESET_SECONDS", 3600)
        monkeypatch.setattr(rate_limit, "_LOGIN_LOCKOUTS_MAX", 5)
        for i in range(20):  # every made-up address breaches at once
            self._trip(f"spray{i}@example.com", 1)
            clock[0] += 1
            assert len(rate_limit._LOGIN_LOCKOUTS) <= 5
        # At the cap the entries ending soonest went first: the newest remain.
        assert set(rate_limit._LOGIN_LOCKOUTS) == {
            f"spray{i}@example.com" for i in range(15, 20)
        }
        # Past the reset window every old entry is stale and evicted.
        clock[0] += 3600 + 61
        self._trip("victim@example.com", 1)
        assert set(rate_limit._LOGIN_LOCKOUTS) == {"victim@example.com"}
        assert rate_limit.login_email_check("victim@example.com") == 60

    def test_breach_count_forgets_after_a_quiet_period(self, monkeypatch):
        clock = [2_000_000.0]
        monkeypatch.setattr(rate_limit.time, "time", lambda: clock[0])
        monkeypatch.setattr(settings, "LOGIN_EMAIL_RATE_LIMIT", "2/hour")
        monkeypatch.setattr(settings, "LOGIN_EMAIL_LOCKOUT_SECONDS", 60)
        monkeypatch.setattr(settings, "LOGIN_EMAIL_BREACH_RESET_SECONDS", 3600)
        email = "victim@example.com"
        for _ in range(3):
            self._trip(email, 2)
            clock[0] += 1000
        assert rate_limit._LOGIN_LOCKOUTS[email].breaches == 3
        clock[0] += 3601
        self._trip(email, 2)
        assert rate_limit.login_email_check(email) == 60  # back to the base

    def test_email_key_is_case_insensitive(self, monkeypatch):
        monkeypatch.setattr(settings, "LOGIN_EMAIL_RATE_LIMIT", "1/hour")
        self._trip("Victim@Example.com ", 1)
        assert rate_limit.login_email_check("victim@example.com") is not None


# --- acceptance 4: polling does not consume the generation limit -------------


class TestRouteClasses:
    async def test_polling_does_not_consume_the_generation_limit(
        self, make_client, auth_headers, test_book, test_user
    ):
        c = make_client(TUNNEL)
        try:
            # A minute of 1/s polling — and more: past the 60/minute default,
            # which polling routes are exempt from.
            for _ in range(settings.RATE_LIMIT_PER_MINUTE + 10):
                r = await c.get(
                    f"/api/v1/books/{test_book.id}/plans", headers=auth_headers
                )
                assert r.status_code == 200

            llm = class_limit("llm")
            key = f"user:{test_user.id}"
            _, remaining = limiter.limiter.get_window_stats(llm, "class", "llm", key)
            assert remaining == llm.amount

            # The generation class still has its whole budget, and no more.
            missing = uuid.uuid4()
            for _ in range(llm.amount):
                r = await c.post(
                    f"/api/v1/books/{missing}/plans/generate",
                    json={"kind": "outline"},
                    headers=auth_headers,
                )
                assert r.status_code != 429
            r = await c.post(
                f"/api/v1/books/{missing}/plans/generate",
                json={"kind": "outline"},
                headers=auth_headers,
            )
            assert r.status_code == 429
            assert int(r.headers["Retry-After"]) >= 1
            # ...and the polling class is still open.
            r = await c.get(f"/api/v1/books/{test_book.id}/plans", headers=auth_headers)
            assert r.status_code == 200
        finally:
            await _close(c)

    def test_every_classed_route_exists(self):
        """The class table names route templates; a renamed route must not
        silently fall back to the default limits."""
        from app.main import app

        served = {
            (m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or ())
        }
        missing = [k for k in ROUTE_CLASSES if k not in served]
        assert missing == []


# --- confirm-in-production log field ------------------------------------------


async def test_request_log_records_peer_and_cf_header(make_client):
    c = make_client(TUNNEL)
    try:
        with patch("app.main.log_request_start") as log:
            await c.get("/health/live", headers={"CF-Connecting-IP": "198.51.100.1"})
            await c.get("/health/live")
    finally:
        await _close(c)
    first, second = log.call_args_list
    assert first.kwargs == {"peer": TUNNEL, "cf_connecting_ip": True}
    assert second.kwargs == {"peer": TUNNEL, "cf_connecting_ip": False}


def test_every_route_that_enqueues_llm_work_is_in_the_llm_class():
    """Confirming a premise and editing a source's text both enqueue multi-call
    jobs, so they share the tight LLM bucket (Codex review on #34)."""
    for key in (
        ("POST", "/api/v1/books/{book_id}/plans/{plan_id}/confirm-premise"),
        ("PATCH", "/api/v1/sources/{source_id}"),
    ):
        assert ROUTE_CLASSES.get(key) == "llm", key
