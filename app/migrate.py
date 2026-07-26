"""Container startup migration with a one-time self-heal for the squashed baseline.

Migrations run at container start (see the Dockerfile CMD). The book-as-root work
squashed the migration history into a frozen `0001` baseline, so a database still
stamped at a PRE-squash revision would make `alembic upgrade head` fail
("Can't locate revision identified by …") and crash-loop the container. Live data
is disposable in this phase (docs/ADR-002-book-as-root.md, migration 0007), so
such a database is reset ONCE — drop + recreate the `public` schema — then rebuilt
from the current chain. This is what previously had to be done by hand against the
beta/prod Neon databases; doing it here removes the manual pre-merge gate.

The reset fires ONLY when the stored revision is *orphaned*: present in
`alembic_version` but absent from the local script directory. A fresh database
(no `alembic_version`) and an up-to-date database are never touched, so normal
deploys never reset anything. In forward-only migration discipline an orphaned
revision only arises from an intentional squash like this one.

Safety valve: set `POLYPHONY_ORPHAN_RESET=0` to disable the reset (an orphaned
revision then fails loudly instead of dropping data). Once the databases hold data
worth keeping, delete this shim and call `alembic upgrade head` directly again.
"""

from __future__ import annotations

import asyncio
import logging
import os

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.database import get_async_db_url

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("polyphony.migrate")


def _alembic_config() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    return cfg


async def _stored_revision(engine) -> str | None:
    """The revision the database is stamped at, or None if it was never migrated."""
    async with engine.connect() as conn:
        has_table = await conn.scalar(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'alembic_version'"
            )
        )
        if not has_table:
            return None
        return await conn.scalar(text("SELECT version_num FROM alembic_version"))


async def _reset_if_orphaned(cfg: Config) -> None:
    script = ScriptDirectory.from_config(cfg)
    engine = create_async_engine(get_async_db_url())
    try:
        rev = await _stored_revision(engine)
        if rev is None:
            log.info("Fresh database (no alembic_version) — migrating up.")
            return
        # Membership, not script.get_revision(): alembic wraps an unknown-revision
        # lookup in CommandError, so a type-based catch is fragile.
        known = {s.revision for s in script.walk_revisions()}
        if rev in known:
            log.info("Database at revision %s — migrating up.", rev)
            return
        if os.getenv("POLYPHONY_ORPHAN_RESET", "1") != "1":
            log.error(
                "alembic_version=%s is orphaned but POLYPHONY_ORPHAN_RESET is "
                "disabled — refusing to reset; the upgrade below will fail.",
                rev,
            )
            return
        log.warning(
            "alembic_version=%s is not in the migration history (pre-squash "
            "baseline). Resetting the disposable database and rebuilding.",
            rev,
        )
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


def main() -> None:
    cfg = _alembic_config()
    asyncio.run(_reset_if_orphaned(cfg))
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    main()
