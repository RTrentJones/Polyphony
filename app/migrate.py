"""Container startup migration with a one-time self-heal for the squashed baseline.

Migrations run at container start (see the Dockerfile CMD). The book-as-root work
squashed the migration history into a frozen `0001` baseline, so a database still
carrying the OLD, pre-squash schema would make `alembic upgrade head` apply the new
chain on top of an incompatible schema (or crash). Live data is disposable in this
phase (docs/ADR-002-book-as-root.md, migration 0007), so such a database is reset
ONCE — drop + recreate the `public` schema — then rebuilt from the current chain.
This is what previously had to be done by hand against beta/prod Neon; doing it
here removes the manual pre-merge gate.

Detection is by SCHEMA FINGERPRINT, and ONLY the legacy pre-squash schema is an
automatic destructive trigger: the old `manuscripts` table that the new chain
replaced with `sources`. Reset fires when `manuscripts` exists and `sources` does
not. Everything else is left untouched:

- A fresh database (neither table) just migrates up.
- A valid current database (`sources` present) is never reset — even if it is
  stamped at an UNKNOWN revision. That is the ordinary rollback case: a later
  release migrated the DB ahead (e.g. to `0008`) and deployment rolled back to
  this older immutable image, which knows only through `0007`. Wiping a valid
  database on rollback would be catastrophic, so instead the upgrade below fails
  loudly ("Can't locate revision") and the operator decides. Revision ids alone
  can't be trusted anyway — the old and new chains reuse them (both have a `0006`).

Overrides (`POLYPHONY_FORCE_SCHEMA_RESET`): unset = auto (legacy fingerprint only);
`1` = reset unconditionally (an explicit clean-slate wipe, e.g. to erase an unknown
schema on purpose); `0` = never reset. Once past this transition, delete this shim
and call `alembic upgrade head` directly.
"""

from __future__ import annotations

import asyncio
import logging
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.database import get_async_db_url

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("polyphony.migrate")


def _alembic_config() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    return cfg


async def _table_exists(conn, name: str) -> bool:
    return bool(
        await conn.scalar(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :n"
            ),
            {"n": name},
        )
    )


async def _reset_reason(conn) -> str | None:
    """Why this database must be AUTO-reset, or None to leave it untouched.

    The ONLY automatic destructive trigger is the legacy pre-squash schema — the
    old `manuscripts` table the new chain replaced with `sources`. A valid current
    schema (`sources` present) is never wiped, even when stamped at an unknown
    revision (a rollback to an older image): the upgrade should fail loudly rather
    than erase a valid database. `POLYPHONY_FORCE_SCHEMA_RESET=1` is required to
    wipe anything that isn't the legacy schema.
    """
    if await _table_exists(conn, "manuscripts") and not await _table_exists(
        conn, "sources"
    ):
        return "legacy manuscript-based schema (manuscripts present, sources absent)"
    return None


async def reset_if_legacy() -> None:
    """Drop + recreate the schema iff this database is the legacy/disposable one."""
    force = os.getenv("POLYPHONY_FORCE_SCHEMA_RESET")
    if force == "0":
        log.info("POLYPHONY_FORCE_SCHEMA_RESET=0 — schema reset disabled.")
        return
    engine = create_async_engine(get_async_db_url())
    try:
        if force == "1":
            reason = "POLYPHONY_FORCE_SCHEMA_RESET=1"
        else:
            async with engine.connect() as conn:
                reason = await _reset_reason(conn)
        if not reason:
            log.info("Schema is fresh or up-to-date — migrating up.")
            return
        log.warning("Resetting the disposable database: %s.", reason)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


def main() -> None:
    cfg = _alembic_config()
    asyncio.run(reset_if_legacy())
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    main()
