"""The startup migrator (app/migrate.py) is a plain `alembic upgrade head`.

The legacy-reset shim was retired (SPEC-15c): nothing in the entrypoint drops or
recreates the schema any more. The real upgrade is exercised against Postgres in
tests/integration/test_pg_pipeline.py.
"""

import pytest

import app.migrate as migrate_mod

pytestmark = pytest.mark.unit


def test_main_runs_alembic_upgrade_head(monkeypatch):
    calls = []
    monkeypatch.setattr(
        migrate_mod.command, "upgrade", lambda cfg, rev: calls.append((cfg, rev))
    )
    migrate_mod.main()
    assert len(calls) == 1
    cfg, rev = calls[0]
    assert rev == "head"
    assert cfg.get_main_option("script_location") == "alembic"


def test_reset_shim_is_gone():
    assert not hasattr(migrate_mod, "reset_if_legacy")
    assert not hasattr(migrate_mod, "_reset_reason")
