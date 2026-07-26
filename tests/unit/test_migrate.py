"""The startup migrator's reset decision (app/migrate.py::_reset_reason).

The destructive reset itself is exercised against real Postgres in
tests/integration/test_pg_pipeline.py. Here we lock the decision logic DB-free:
ONLY the legacy `manuscripts` schema is an automatic destructive trigger — a valid
current schema is never wiped, even when stamped at an unknown (rolled-back)
revision.
"""

import pytest

from app.migrate import _reset_reason

pytestmark = pytest.mark.unit


class _FakeConn:
    """Answers _table_exists() from a fixed set of present table names."""

    def __init__(self, tables: set[str]):
        self._tables = tables

    async def scalar(self, _stmt, params=None):
        return 1 if params and params.get("n") in self._tables else None


async def test_legacy_manuscript_schema_is_reset():
    reason = await _reset_reason(_FakeConn({"manuscripts", "alembic_version"}))
    assert reason is not None


async def test_valid_current_schema_is_never_reset_even_if_stamp_unknown():
    # `sources` present -> a valid new-schema DB. A rollback that leaves it stamped
    # at an unknown future revision must NOT trigger a wipe.
    assert await _reset_reason(_FakeConn({"sources", "alembic_version"})) is None


async def test_fresh_database_is_not_reset():
    assert await _reset_reason(_FakeConn(set())) is None


async def test_mixed_schema_is_not_reset_because_sources_present():
    # If both exist (a half-migrated oddity), `sources` present means "not the
    # legacy schema" — do not auto-wipe.
    assert await _reset_reason(_FakeConn({"manuscripts", "sources"})) is None
