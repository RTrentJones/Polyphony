"""Shared test rows: a chapter (planned or not), a scene that may write a
planned beat, its beat rows, and a scene scope."""

from app.characters.context import SceneScope
from app.core.orm_models import Chapter, Scene, SceneBeat


async def add_chapter(session, book, position=0, title="Intake", **kw) -> Chapter:
    chapter = Chapter(book_id=book.id, position=position, title=title, **kw)
    session.add(chapter)
    await session.flush()
    return chapter


async def add_planned_chapter(session, book, beats, position=0) -> Chapter:
    """A chapter whose planned scenes are `beats` (PlannedBeat dicts)."""
    return await add_chapter(session, book, position, planned_beats=beats)


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


async def add_scene_beats(session, scene, lines: list[str]) -> list[SceneBeat]:
    """The scene's written beats, numbered from 0 as the scene jobs store them."""
    beats = [
        SceneBeat(scene_id=scene.id, beat_number=i, description=line)
        for i, line in enumerate(lines)
    ]
    session.add_all(beats)
    await session.flush()
    return beats


def make_scope(**fields) -> SceneScope:
    """A SceneScope with every field empty unless given."""
    empty = {
        "cast": (),
        "book_cast": (),
        "secondary_names": (),
        "known": frozenset(),
        "known_text": "",
        "plot": (),
        "later_from": {},
        "own_plot": (),
        "speech_paragraphs": {},
    }
    return SceneScope(**{**empty, **fields})
