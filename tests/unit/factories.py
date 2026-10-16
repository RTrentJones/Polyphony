"""Shared test rows: a chapter, and a scene that may write a planned beat."""

from app.core.orm_models import Chapter, Scene


async def add_chapter(session, book, position=0, title="Intake") -> Chapter:
    chapter = Chapter(book_id=book.id, position=position, title=title)
    session.add(chapter)
    await session.flush()
    return chapter


async def add_scene(
    session, book, chapter=None, position=0, beat=None, status="completed", **kw
) -> Scene:
    """A scene in `chapter` (None: unfiled); `beat` is its planned_beat_id."""
    scene = Scene(
        user_id=book.user_id,
        book_id=book.id,
        chapter_id=chapter.id if chapter else None,
        position=position,
        status=status,
        scene_request={"planned_beat_id": beat},
        **kw,
    )
    session.add(scene)
    await session.flush()
    return scene
