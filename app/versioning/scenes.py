"""Keep a deleted scene's prose (SPEC-04; invariant 3, nothing is destroyed).

Scenes version through `scene_revisions` while they live (ADR-002 §5), but those
rows cascade with the scene. Before a scene is deleted — on its own, or with its
chapter when the author chooses `scenes=delete` — its draft and revision history
are appended to `entity_versions` (entity_type='scene', reason='deleted'), which
lives as long as the book. SPEC-13's Trash restores from there.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm_models import Scene, SceneRevision
from app.versioning import repository as versions_repo

ENTITY = "scene"


async def snapshot_before_delete(
    db: AsyncSession, scene: Scene, *, reason: str = "deleted", created_by=None
) -> None:
    """Append the scene's full state to entity_versions. Does NOT commit."""
    revisions = (
        (
            await db.execute(
                select(SceneRevision)
                .where(SceneRevision.scene_id == scene.id)
                .order_by(SceneRevision.created_at)
            )
        )
        .scalars()
        .all()
    )
    await versions_repo.snapshot(
        db,
        book_id=scene.book_id,
        entity_type=ENTITY,
        entity_id=scene.id,
        content={
            "chapter_id": str(scene.chapter_id) if scene.chapter_id else None,
            "position": scene.position,
            "title": scene.title,
            "setting": scene.setting,
            "emotional_tone": scene.emotional_tone,
            "characters": scene.characters,
            "character_ids": scene.character_ids,
            "scene_description": scene.scene_description,
            "generated_content": scene.generated_content,
            "content": scene.content,
            "status": scene.status,
            "revisions": [
                {
                    "content": r.content,
                    "source": r.source,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in revisions
            ],
        },
        reason=reason,
        created_by=created_by,
    )
