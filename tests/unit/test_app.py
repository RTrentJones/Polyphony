"""Unit tests for the consolidated FastAPI app surface."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def api_client():
    # Don't run lifespan: no DB/LLM in unit tests.
    return TestClient(app)


@pytest.mark.unit
class TestAppSurface:
    def test_version_endpoint(self, api_client):
        response = api_client.get("/__version")
        assert response.status_code == 200
        assert "sha" in response.json()

    def test_root_endpoint(self, api_client):
        response = api_client.get("/")
        assert response.status_code == 200

    def test_docs_available(self, api_client):
        response = api_client.get("/openapi.json")
        assert response.status_code == 200

    def test_mcp_alias_surface(self, api_client):
        """Greenlight's mcp lane probes <host>/mcp and /mcp/__version."""
        assert api_client.get("/mcp").status_code == 200
        assert "sha" in api_client.get("/mcp/__version").json()


@pytest.mark.unit
class TestLivenessIsDatabaseFree:
    """The container HEALTHCHECK and the keepalive Worker probe on a timer.

    Neon's compute stays awake ~5 minutes after any query and is billed by the
    hour, so a scheduled probe that reaches Postgres is a 24/7 database bill.
    /health/live must therefore answer without touching the database at all.
    """

    def test_liveness_answers_without_a_database(self, api_client, monkeypatch):
        import app.main as main_mod

        async def explode():  # any DB access here is the bug this guards
            raise AssertionError("liveness must not touch the database")

        monkeypatch.setattr(main_mod, "check_db_connection", explode)
        response = api_client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_mcp_liveness_alias(self, api_client):
        assert api_client.get("/mcp/health/live").status_code == 200

    def test_deep_health_round_trip_is_cached(self, api_client, monkeypatch):
        """verify retries /health while a container settles; collapse the burst."""
        import app.main as main_mod

        main_mod._health_cache = None
        calls = {"n": 0}

        async def counting_check():
            calls["n"] += 1
            return True

        class _Store:
            async def healthy(self):
                return True

        monkeypatch.setattr(main_mod, "check_db_connection", counting_check)
        monkeypatch.setattr("app.rag.store.get_chunk_store", lambda: _Store())
        try:
            for _ in range(6):
                assert api_client.get("/health").status_code == 200
            assert calls["n"] == 1
        finally:
            main_mod._health_cache = None

    def test_deep_health_skips_vector_probe_when_db_is_down(
        self, api_client, monkeypatch
    ):
        import app.main as main_mod

        main_mod._health_cache = None
        probed = {"vector": False}

        async def db_down():
            return False

        class _Store:
            async def healthy(self):
                probed["vector"] = True
                return True

        monkeypatch.setattr(main_mod, "check_db_connection", db_down)
        monkeypatch.setattr("app.rag.store.get_chunk_store", lambda: _Store())
        try:
            body = api_client.get("/health").json()
            assert body["status"] == "degraded"
            assert body["checks"]["vector_search"] == "unhealthy"
            assert probed["vector"] is False  # no second round trip
        finally:
            main_mod._health_cache = None


@pytest.mark.unit
class TestSecurityHeaders:
    def test_security_headers_present(self, api_client):
        response = api_client.get("/__version")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "Strict-Transport-Security" in response.headers


@pytest.mark.unit
class TestAuthRequired:
    """Every data route must reject unauthenticated requests."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/sources/",
            "/api/v1/scenes/",
            "/api/v1/auth/me",
        ],
    )
    def test_unauthenticated_401(self, api_client, path):
        response = api_client.get(path)
        assert response.status_code == 401

    def test_invites_require_admin(self, api_client):
        response = api_client.post("/api/v1/auth/invites", json={})
        assert response.status_code == 401
