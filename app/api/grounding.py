"""Scene grounding provenance: which voice samples a generated scene was written from.

Prose and ensemble generation store, per beat, the voice chunks retrieved for
each character and their similarity (`Scene.evaluation_scores["grounding"]`, as
`{beat_index: [{character_id, chunk_id, score, excerpt}]}`). This resolves the
ids to the chunks' CURRENT text: an edited sample shows its edit, and a deleted
one is reported as deleted rather than silently dropped (docs/BRD.md R5.3). The
bounded `excerpt` stored at generation time is returned too, so a deleted
sample still shows the wording that grounded the scene (scenes generated before
excerpts were recorded have none).

A character no sample grounded (none indexed, or an ensemble context-only
character) is stored as an entry with no chunk and a `reason`; it is listed
with no samples and that reason, so the view names everyone in the beat. A
movement line the product withheld is stored with its rule (`withheld`, one of
WITHHELD_RULES) and listed under the group's `withheld`, which is omitted when
empty (SPEC-66).
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.books import owned_scene
from app.core.database import get_db
from app.core.orm_models import (
    Character as CharacterORM,
    SceneBeat as SceneBeatORM,
    User as UserORM,
)
from app.core.security import get_current_active_user
from app.rag.store import get_chunk_store

router = APIRouter()


def _beat_key(key) -> int:
    try:
        return int(key)
    except (TypeError, ValueError):
        return 1 << 30  # an unexpected key sorts last rather than failing the read


@router.get("/scenes/{scene_id}/grounding", response_model=dict)
async def get_scene_grounding(
    scene_id: UUID,
    current_user: UserORM = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Per beat, per character: the samples that grounded this scene, scored."""
    # Authorised through its book (SPEC-04); 404 for anyone else's scene.
    scene = await owned_scene(scene_id, current_user, db)

    meta = scene.evaluation_scores or {}
    stored = meta.get("grounding") or {}
    entries = [(key, e) for key, beat in stored.items() for e in (beat or []) if e]

    char_ids = {e.get("character_id") for _, e in entries if e.get("character_id")}
    names: dict[str, str] = {}
    if char_ids:
        valid = []
        for cid in char_ids:
            try:
                valid.append(UUID(str(cid)))
            except ValueError:
                continue
        rows = (
            await db.execute(
                select(CharacterORM.id, CharacterORM.name).where(
                    CharacterORM.id.in_(valid),
                    CharacterORM.user_id == current_user.id,
                )
            )
        ).all()
        names = {str(r.id): r.name for r in rows}

    # Resolve only chunks of characters this user owns.
    chunk_ids = [
        str(e["chunk_id"])
        for _, e in entries
        if e.get("chunk_id") and str(e.get("character_id")) in names
    ]
    live = await get_chunk_store().get_chunks(chunk_ids) if chunk_ids else {}

    descriptions = {
        b.beat_number: b.description
        for b in (
            await db.execute(
                select(SceneBeatORM).where(SceneBeatORM.scene_id == scene.id)
            )
        )
        .scalars()
        .all()
    }

    beats = []
    for key in sorted(stored, key=_beat_key):
        by_character: dict[str, dict] = {}
        for e in stored[key] or []:
            if not e:
                continue
            cid = str(e.get("character_id"))
            group = by_character.setdefault(
                cid,
                {
                    "character_id": cid,
                    "name": names.get(cid),
                    "samples": [],
                    "reason": None,
                },
            )
            if not e.get("chunk_id"):
                # No sample grounded this character in this beat; say why.
                group["reason"] = e.get("reason") or "no indexed voice samples"
                continue
            chunk_id = str(e["chunk_id"])
            chunk = live.get(chunk_id) if chunk_id else None
            if chunk is not None and chunk.get("character_id") != cid:
                chunk = None  # an id that is not this character's is not grounding
            text = chunk["text"] if chunk else None
            if e.get("withheld"):
                group.setdefault("withheld", []).append(
                    {
                        "chunk_id": chunk_id,
                        "rule": e["withheld"],
                        "text": text,
                        "excerpt": e.get("excerpt"),
                    }
                )
                continue
            group["samples"].append(
                {
                    "chunk_id": chunk_id,
                    "score": e.get("score"),
                    "deleted": chunk is None,
                    "text": text,
                    "chunk_type": chunk["chunk_type"] if chunk else None,
                    "source": chunk["source"] if chunk else None,
                    # What the sample said when the scene was generated.
                    "excerpt": e.get("excerpt"),
                }
            )
        index = _beat_key(key)
        beats.append(
            {
                "beat_index": index,
                "description": descriptions.get(index),
                "characters": list(by_character.values()),
            }
        )
    # A beat stored without recorded grounding still exists, and the editor's
    # "Redo beat N" (SPEC-25) needs it listed.
    listed = {b["beat_index"] for b in beats}
    beats.extend(
        {"beat_index": n, "description": d, "characters": []}
        for n, d in descriptions.items()
        if n not in listed
    )
    beats.sort(key=lambda b: b["beat_index"])

    return {
        "scene_id": str(scene.id),
        "mode": meta.get("mode"),
        "recorded": "grounding" in meta,
        "beats": beats,
    }
