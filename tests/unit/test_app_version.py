"""SPEC-15 15c: the app version comes from one place (settings.APP_VERSION), and
/health names the build (the short GREENLIGHT_SHA) when the image carries one."""

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.core.config import settings


@pytest.fixture
def api_client():
    return TestClient(main_mod.app)  # no lifespan: no DB/LLM in unit tests


@pytest.fixture
def healthy(monkeypatch):
    async def db_up():
        return True

    class _Store:
        async def healthy(self):
            return True

    monkeypatch.setattr(main_mod, "check_db_connection", db_up)
    monkeypatch.setattr("app.rag.store.get_chunk_store", lambda: _Store())
    main_mod._health_cache = None
    yield
    main_mod._health_cache = None


@pytest.mark.unit
class TestAppVersion:
    def test_no_hard_coded_version_literal_in_main(self):
        import inspect

        assert '"1.0.0"' not in inspect.getsource(main_mod)

    def test_surfaces_report_settings_version(self, api_client, healthy):
        assert main_mod.app.version == settings.APP_VERSION
        assert api_client.get("/health/live").json()["version"] == settings.APP_VERSION
        assert api_client.get("/health").json()["version"] == settings.APP_VERSION

    def test_health_reports_short_sha_when_set(self, api_client, healthy, monkeypatch):
        monkeypatch.setenv("GREENLIGHT_SHA", "0123456789abcdef0123456789abcdef01234567")
        body = api_client.get("/health").json()
        assert body["sha"] == "0123456"
        # /__version keeps the full sha: the SHA gate compares the whole thing.
        full = api_client.get("/__version").json()["sha"]
        assert full == "0123456789abcdef0123456789abcdef01234567"

    def test_health_omits_sha_when_unset(self, api_client, healthy, monkeypatch):
        monkeypatch.delenv("GREENLIGHT_SHA", raising=False)
        assert "sha" not in api_client.get("/health").json()
