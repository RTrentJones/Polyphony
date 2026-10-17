"""D6 (W8-2 (b), B-1): the lines the voice probe shows at threshold 0 and 0.2,
in order, from the product's own `character_samples`, called with the probe's
arguments (`dialogue.generate_dialogue`, `api/characters.test_dialogue`).

    DATABASE_URL=...polyphony_w7r python -m evals.measure.lines_shown \\
        eval-attr-cel "Zara Okafor" "Edric Thane"
"""

import asyncio
import sys

from app.characters.context import SceneScope, character_samples

THRESHOLDS = (0, 0.2)  # today's (SPEC-55), and wave 6's


async def shown(character, canon, probe: str) -> dict:
    """{threshold: {"voice": [text], "movement": [text]}} for one character."""
    scope = SceneScope.of(canon, [character], [], (), {})  # the probe's scope
    out = {}
    for t in THRESHOLDS:
        words, moves = await character_samples(
            character, probe, 3, scope=scope, threshold=t
        )
        out[t] = {
            "voice": [s["text"] for s in words],
            "movement": [s["text"] for s in moves],
        }
    return out


async def main(title: str, names: list[str]) -> None:
    from sqlalchemy import select

    from app.core.database import get_session_factory
    from app.core.orm_models import Book, Character
    from app.planning.canon import build_canon
    from evals.steps.pipeline import load_corpus

    probes = load_corpus("cel")[1]["voice_probe"]
    async with get_session_factory()() as db:
        book = (await db.execute(select(Book).where(Book.title == title))).scalar_one()
        canon = await build_canon(db, book.id)
        for name in names:
            q = select(Character).where(
                Character.book_id == book.id, Character.name == name
            )
            got = await shown((await db.execute(q)).scalar_one(), canon, probes[name])
            for t, lines in got.items():
                print(f"## {name} @ threshold {t}")
                for kind, texts in lines.items():
                    print(
                        *(f"- {kind} {i}: {x}" for i, x in enumerate(texts, 1)),
                        sep="\n",
                    )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2:]))
