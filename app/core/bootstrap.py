"""First-boot bootstrap: create the admin user when the users table is empty."""

from sqlalchemy import func, select

from .config import settings
from .database import get_async_session
from .logging_config import setup_logging
from .orm_models import User
from .security import get_password_hash

logger = setup_logging("core.bootstrap")


async def bootstrap_admin() -> None:
    """Create an admin user from ADMIN_EMAIL/ADMIN_PASSWORD on an empty database."""
    if not settings.ADMIN_EMAIL or not settings.ADMIN_PASSWORD:
        return
    try:
        async with get_async_session() as session:
            count = (
                await session.execute(select(func.count()).select_from(User))
            ).scalar_one()
            if count:
                return
            session.add(
                User(
                    email=settings.ADMIN_EMAIL,
                    hashed_password=get_password_hash(settings.ADMIN_PASSWORD),
                    full_name="Admin",
                    role="admin",
                    is_active=True,
                )
            )
        logger.info(
            f"Bootstrapped admin user {settings.ADMIN_EMAIL}",
            extra_fields={"event": "admin_bootstrapped"},
        )
    except Exception as e:
        logger.warning(
            f"Admin bootstrap failed: {e}",
            extra_fields={"event": "admin_bootstrap_failed"},
        )
