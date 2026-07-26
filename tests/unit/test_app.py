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

    def test_scene_generate_unauthenticated(self, api_client):
        response = api_client.post("/api/v1/scenes/generate", json={})
        assert response.status_code == 401

    def test_invites_require_admin(self, api_client):
        response = api_client.post("/api/v1/auth/invites", json={})
        assert response.status_code == 401
