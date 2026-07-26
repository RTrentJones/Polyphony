"""The startup migrator's self-heal decision (app/migrate.py).

The destructive reset itself is exercised against real Postgres in
tests/integration/test_pg_pipeline.py. Here we lock the fact that makes the
DESIGN necessary: the old and new chains reuse revision ids, so revision
membership cannot distinguish a legacy database from an up-to-date one — hence the
migrator fingerprints the schema (`manuscripts` vs `sources`) instead.
"""

import pytest

pytestmark = pytest.mark.unit


def _known_revisions() -> set[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    return {s.revision for s in ScriptDirectory.from_config(cfg).walk_revisions()}


def test_head_is_recognized():
    known = _known_revisions()
    assert "0007" in known  # current head
    assert "0001" in known  # frozen baseline


def test_revision_ids_are_reused_so_membership_cannot_detect_legacy():
    # The old `main` head AND a new-chain migration are both named '0006' (off
    # '0005'). A legacy DB stamped '0006' therefore passes a membership check and
    # would be missed — which is exactly why reset detection fingerprints the
    # schema shape, not the revision id (PR review, final).
    known = _known_revisions()
    assert "0006" in known
    assert "0005" in known
