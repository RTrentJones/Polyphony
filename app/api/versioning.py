"""Version history + restore for canon entities (docs/ADR-002 §5).

Read the history of any versioned entity and restore an earlier state. Restore is
FORWARD-ONLY: it re-applies old content to the live row AND appends a new version
(reason='restored_from:N'), so max(version_no) still equals the live row and
nothing is ever deleted. Old content is re-validated through the SAME validator
the write path uses — an old shape today's validator rejects returns 409 rather
than corrupting the live row.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.orm_models import (
    Book as BookORM,
    BookPlan as BookPlanORM,
    Character as CharacterORM,
    User as UserORM,
)
from app.core.security import get_current_active_user
from app.planning.outline import validate_outline_nodes
from app.versioning import repository as versions_repo

router = APIRouter()

_ENTITY_TYPES = {"book_plan", "character"}
_CHARACTER_FIELDS = (
    "name",
    "description",
    "personality_traits",
    "voice_characteristics",
    "role",
    "goals",
    "arc",
    "relationships",
    "notes",
)


async def _owned_book(
    book_id: UUID, current_user: UserORM, db: AsyncSession
) -> BookORM:
    book = (
        await db.execute(
            select(BookORM).where(
                BookORM.id == book_id, BookORM.user_id == current_user.id
            )
        )
    ).scalar_one_or_none()
    if not book:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Book not found"
        )
    return book


def _check_entity_type(entity_type: str) -> None:
    if entity_type not in _ENTITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown entity_type '{entity_type}'",
        )


@router.get("/books/{book_id}/versions/{entity_type}/{entity_id}", response_model=dict)
async def list_entity_versions(
    book_id: UUID,
    entity_type: str,
    entity_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Version history for one entity, newest first (max version_no = live)."""
    await _owned_book(book_id, current_user, db)
    _check_entity_type(entity_type)
    versions = await versions_repo.list_versions(db, entity_type, entity_id)
    # book_id on the row is the tenant guard — never surface another book's log.
    versions = [v for v in versions if v.book_id == book_id]
    return {
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "versions": [
            {
                "version_no": v.version_no,
                "reason": v.reason,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                "created_by": str(v.created_by) if v.created_by else None,
            }
            for v in versions
        ],
    }


@router.get(
    "/books/{book_id}/versions/{entity_type}/{entity_id}/{version_no}",
    response_model=dict,
)
async def get_entity_version(
    book_id: UUID,
    entity_type: str,
    entity_id: UUID,
    version_no: int,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Full content of one version."""
    await _owned_book(book_id, current_user, db)
    _check_entity_type(entity_type)
    version = await versions_repo.get_version(db, entity_type, entity_id, version_no)
    if version is None or version.book_id != book_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Version not found"
        )
    return {
        "version_no": version.version_no,
        "reason": version.reason,
        "content": version.content,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }


@router.post(
    "/books/{book_id}/versions/{entity_type}/{entity_id}/restore/{version_no}",
    response_model=dict,
)
async def restore_entity_version(
    book_id: UUID,
    entity_type: str,
    entity_id: UUID,
    version_no: int,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Restore an entity to an earlier version (forward-only append)."""
    book = await _owned_book(book_id, current_user, db)
    _check_entity_type(entity_type)
    version = await versions_repo.get_version(db, entity_type, entity_id, version_no)
    if version is None or version.book_id != book_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Version not found"
        )

    reason = f"restored_from:{version_no}"

    if entity_type == "book_plan":
        plan = (
            await db.execute(
                select(BookPlanORM).where(
                    BookPlanORM.id == entity_id, BookPlanORM.book_id == book_id
                )
            )
        ).scalar_one_or_none()
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found"
            )
        payload = version.content or {}
        nodes = payload.get("content") if isinstance(payload, dict) else payload
        try:
            # Re-validate through the write path's validator — an old shape the
            # current validator rejects must 409, never corrupt the live row.
            validated = validate_outline_nodes(nodes)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Version {version_no} is not restorable: {e}",
            )
        plan.content = validated
        snapshot_content = {"kind": plan.kind, "content": validated}
        live_id = plan.id
    else:  # character
        character = (
            await db.execute(
                select(CharacterORM).where(
                    CharacterORM.id == entity_id, CharacterORM.book_id == book_id
                )
            )
        ).scalar_one_or_none()
        if character is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Character not found"
            )
        content = version.content or {}
        for field_name in _CHARACTER_FIELDS:
            if field_name in content:
                setattr(character, field_name, content[field_name])
        snapshot_content = {f: getattr(character, f) for f in _CHARACTER_FIELDS}
        live_id = character.id

    await versions_repo.snapshot(
        db,
        book_id=book.id,
        entity_type=entity_type,
        entity_id=live_id,
        content=snapshot_content,
        reason=reason,
        created_by=current_user.id,
    )
    try:
        await db.commit()
    except IntegrityError:
        # e.g. restoring a character name that now collides under (book_id, name).
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Restore conflicts with the current state (name already in use)",
        )
    return {
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "restored_from": version_no,
    }
