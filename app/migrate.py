"""Container startup migration: `alembic upgrade head`.

Migrations run at container start (see the Dockerfile CMD and scripts/e2e_stub.sh):
one container, so there is no migration race. This module is only the entrypoint
(`python -m app.migrate`); it no longer resets anything. The one-time self-heal for
the pre-squash `manuscripts` schema was retired once production and beta were
confirmed on the `sources` schema (SPEC-15c). A database stamped at an unknown
revision (a rollback to an older image) makes the upgrade fail loudly and the
operator decides — nothing is ever wiped automatically.
"""

from __future__ import annotations

import logging

from alembic import command
from alembic.config import Config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("polyphony.migrate")


def _alembic_config() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    return cfg


def main() -> None:
    log.info("Running alembic upgrade head.")
    command.upgrade(_alembic_config(), "head")


if __name__ == "__main__":
    main()
