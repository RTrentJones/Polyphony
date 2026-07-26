"""The startup migrator's self-heal decision (app/migrate.py).

The destructive branch (DROP SCHEMA on an orphaned database) is verified against
real Postgres manually; here we lock the *decision inputs* so a healthy database is
never reset: the current head must be recognized, and a pre-squash revision must
read as orphaned. If a future rename made head unrecognized, this catches it before
it could nuke a live database on deploy.
"""

import pytest

pytestmark = pytest.mark.unit


def _known_revisions() -> set[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    return {s.revision for s in ScriptDirectory.from_config(cfg).walk_revisions()}


def test_current_head_is_recognized_so_a_healthy_db_is_never_reset():
    known = _known_revisions()
    assert "0007" in known  # current head -> migrate up, no reset
    assert "0001" in known  # frozen baseline


def test_a_pre_squash_revision_reads_as_orphaned():
    # This is exactly the stamp a beta/prod DB carries before the reset; it must
    # NOT resolve, so the migrator resets and rebuilds (docs migration 0007).
    assert "0006_old_presquash_gone" not in _known_revisions()
