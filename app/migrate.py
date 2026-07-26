"""Container startup migration with a one-time self-heal for the squashed baseline.

Migrations run at container start (see the Dockerfile CMD). The book-as-root work
squashed the migration history into a frozen `0001` baseline, so a database still
carrying the OLD, pre-squash schema would make `alembic upgrade head` apply the new
chain on top of an incompatible schema (or crash). Live data is disposable in this
phase (docs/ADR-002-book-as-root.md, migration 0007), so such a database is reset
ONCE — drop + recreate the `public` schema — then rebuilt from the current chain.
This is what previously had to be done by hand against beta/prod Neon; doing it
here removes the manual pre-merge gate.

Detection is by SCHEMA FINGERPRINT, not revision id. The old and new chains REUSE
revision identifiers (both have a `0006` off `0005`), so a legacy database stamped
at `0006` would falsely look up-to-date — the stamp is not a reliable signal. The
reliable signal is the shape: the pre-squash schema has the `manuscripts` table
that the new schema replaced with `sources`. We reset when `manuscripts` exists and
`sources` does not (or when the stamp is a genuinely unknown revision id). A fresh
database (neither table) and an up-to-date database (has `sources`) are never
touched, so normal deploys never reset anything.

Overrides (`POLYPHONY_FORCE_SCHEMA_RESET`): unset = auto-detect (default); `1` =
reset unconditionally (a clean-slate wipe); `0` = never reset (once the databases
hold data worth keeping — an incompatible schema then fails loudly instead). Once
past this transition, delete this shim and call `alembic upgrade head` directly.
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


async def _stored_revision(conn) -> str | None:
    if not await _table_exists(conn, "alembic_version"):
        return None
    return await conn.scalar(text("SELECT version_num FROM alembic_version"))


async def _reset_reason(conn, known: set[str]) -> str | None:
    """Why this database must be reset, or None if it is fresh/up-to-date."""
    # Legacy pre-squash schema. Revision ids were REUSED across the squash, so the
    # stamp can't be trusted — fingerprint the schema: the old `manuscripts` table
    # the new chain replaced with `sources`.
    if await _table_exists(conn, "manuscripts") and not await _table_exists(
        conn, "sources"
    ):
        return "legacy manuscript-based schema (manuscripts present, sources absent)"
    # A genuinely unknown revision id (not from either chain) is also disposable.
    rev = await _stored_revision(conn)
    if rev is not None and rev not in known:
        return f"orphaned alembic_version={rev!r}"
    return None


async def reset_if_legacy(cfg: Config) -> None:
    """Drop + recreate the schema iff this database is a legacy/disposable one."""
    force = os.getenv("POLYPHONY_FORCE_SCHEMA_RESET")
    if force == "0":
        log.info("POLYPHONY_FORCE_SCHEMA_RESET=0 — schema reset disabled.")
        return
    known = {s.revision for s in ScriptDirectory.from_config(cfg).walk_revisions()}
    engine = create_async_engine(get_async_db_url())
    try:
        if force == "1":
            reason = "POLYPHONY_FORCE_SCHEMA_RESET=1"
        else:
            async with engine.connect() as conn:
                reason = await _reset_reason(conn, known)
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
    asyncio.run(reset_if_legacy(cfg))
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    main()
