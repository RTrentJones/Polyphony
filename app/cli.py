"""Admin CLI: python -m app.cli <command>

Commands:
  create-invite [--uses N] [--days N]   Mint an invite code
  create-admin <email> <password>       Create an admin user
  reindex-voice --book <id>             Re-run voice extraction for a book's cast
"""

import argparse
import asyncio
import secrets
import sys
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional
from uuid import UUID


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


async def _voiced_pairs(book_id: UUID) -> set[tuple[UUID, UUID]]:
    """(source_id, character_id) for every voice chunk a source wrote for this
    book. voice_chunks is off the ORM (pgvector), so this is raw SQL."""
    from sqlalchemy import text

    from app.core.database import get_async_session

    async with get_async_session() as session:
        rows = await session.execute(
            text(
                "SELECT DISTINCT source_id, character_id FROM voice_chunks "
                "WHERE book_id = :book_id AND source_id IS NOT NULL"
            ),
            {"book_id": str(book_id)},
        )
        return {(UUID(str(s)), UUID(str(c))) for s, c in rows.all()}


async def _reindex_voice(
    book_id: UUID,
    *,
    index: Optional[Callable[..., Awaitable[None]]] = None,
    voiced: Optional[Callable[[UUID], Awaitable[set]]] = None,
) -> int:
    """Re-run voice extraction over each of a book's sources and replace the
    chunks those sources wrote (SPEC-17). Existing books keep voice samples
    from the old attributor — another speaker's lines, nearby narration —
    until this runs. Per source it re-indexes the characters that source
    already voiced, plus the ones extracted from or linked to it; manual samples and other
    sources' chunks are untouched (`index_characters_voice` clears only this
    source's chunks). Returns the number of sources re-indexed, or -1 when the
    book does not exist."""
    from sqlalchemy import select

    from app.core.database import get_async_session
    from app.core.orm_models import Book, Character, Source, source_characters

    if index is None:
        from app.parsing.pipeline import index_characters_voice as index
    voiced = voiced or _voiced_pairs

    async with get_async_session() as session:
        book = await session.get(Book, book_id)
        if book is None:
            return -1
        user_id = book.user_id
        sources = (
            (
                await session.execute(
                    select(Source).where(
                        Source.book_id == book_id, Source.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        cast = (
            (
                await session.execute(
                    select(Character).where(
                        Character.book_id == book_id, Character.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        source_ids = [s.id for s in sources]
        cast_ids = {c.id for c in cast}
        extracted_from = {(c.source_id, c.id) for c in cast if c.source_id}
        if source_ids:
            links = await session.execute(
                select(
                    source_characters.c.source_id, source_characters.c.character_id
                ).where(source_characters.c.source_id.in_(source_ids))
            )
            extracted_from |= {(s, c) for s, c in links.all()}

    pairs = await voiced(book_id) | extracted_from
    done = 0
    for source_id in source_ids:
        ids = sorted(str(c) for s, c in pairs if s == source_id and c in cast_ids)
        if not ids:
            continue
        await index(source_id, book_id, user_id, ids)
        done += 1
        print(f"Re-indexed voice for {len(ids)} character(s) from source {source_id}")
    return done


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    invite = sub.add_parser("create-invite", help="Mint an invite code")
    invite.add_argument("--uses", type=int, default=1)
    invite.add_argument("--days", type=int, default=30)

    admin = sub.add_parser("create-admin", help="Create an admin user")
    admin.add_argument("email")
    admin.add_argument("password")

    reindex = sub.add_parser(
        "reindex-voice", help="Re-run voice extraction for a book's characters"
    )
    reindex.add_argument("--book", required=True, type=UUID, help="Book id")

    args = parser.parse_args()
    if args.command == "create-invite":
        asyncio.run(_create_invite(args.uses, args.days))
    elif args.command == "create-admin":
        asyncio.run(_create_admin(args.email, args.password))
    elif args.command == "reindex-voice":
        done = asyncio.run(_reindex_voice(args.book))
        if done < 0:
            print(f"No book {args.book}", file=sys.stderr)
            return 1
        print(f"Re-indexed {done} source(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
