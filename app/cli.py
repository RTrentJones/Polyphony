"""Admin CLI: python -m app.cli <command>

Commands:
  create-invite [--uses N] [--days N]   Mint an invite code
  create-admin <email> <password>       Create an admin user
"""

import argparse
import asyncio
import secrets
import sys
from datetime import datetime, timedelta, timezone


async def _create_invite(uses: int, days: int) -> None:
    from app.core.database import get_async_session
    from app.core.orm_models import InviteCode

    code = secrets.token_urlsafe(12)
    async with get_async_session() as session:
        session.add(
            InviteCode(
                code=code,
                max_uses=uses,
                expires_at=datetime.now(timezone.utc) + timedelta(days=days),
            )
        )
    print(f"Invite code: {code} (uses: {uses}, expires in {days} days)")


async def _create_admin(email: str, password: str) -> None:
    from sqlalchemy import select

    from app.core.database import get_async_session
    from app.core.orm_models import User
    from app.core.security import get_password_hash

    async with get_async_session() as session:
        existing = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing:
            existing.role = "admin"
            existing.is_active = True
            print(f"Promoted existing user {email} to admin")
            return
        session.add(
            User(
                email=email,
                hashed_password=get_password_hash(password),
                full_name="Admin",
                role="admin",
                is_active=True,
            )
        )
    print(f"Created admin user {email}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    invite = sub.add_parser("create-invite", help="Mint an invite code")
    invite.add_argument("--uses", type=int, default=1)
    invite.add_argument("--days", type=int, default=30)

    admin = sub.add_parser("create-admin", help="Create an admin user")
    admin.add_argument("email")
    admin.add_argument("password")

    args = parser.parse_args()
    if args.command == "create-invite":
        asyncio.run(_create_invite(args.uses, args.days))
    elif args.command == "create-admin":
        asyncio.run(_create_admin(args.email, args.password))
    return 0


if __name__ == "__main__":
    sys.exit(main())
