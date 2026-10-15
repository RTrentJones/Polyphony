"""A clean manuscript export (B9 backend, SPEC-21).

The e2e book that motivated this exported headings like "## Chapter 2: Chapter 1:
Edric Thane", ran scenes together with no break, and printed bare headings for
six empty chapters.
"""

import io
import re
import zipfile

import pytest

from app.exports.builder import (
    SCENE_BREAK,
    BookExport,
    ChapterExport,
    SceneExport,
    chapter_heading,
    clean_chapter_title,
    select_chapters,
    to_docx,
    to_epub,
    to_markdown,
)

pytestmark = pytest.mark.unit


def _e2e_chapters() -> list[ChapterExport]:
    """Shaped like the e2e book: outline titles carry their own (stale) number,
    and the later planned chapters have no prose yet."""
    return [
        ChapterExport(
            title="Chapter 1: Edric Thane",
            scenes=[
                SceneExport("", "Edric reviews the quarter.\n\nHe approves."),
                SceneExport("", "A new analyst closes well."),
            ],
        ),
        ChapterExport(title="Chapter 2: Milo Voss", scenes=[]),
        ChapterExport(
            title="Chapter 2 - The Ledger",
            scenes=[SceneExport("", "The numbers do not reconcile.")],
        ),
        ChapterExport(title="Chapter 4: Signature Sheets", scenes=[]),
        ChapterExport(
            title="Quarterly Review",
            scenes=[
                SceneExport("", "Milo enters the corner office."),
                SceneExport("", "Edric files it properly."),
            ],
        ),
    ]


def _book(**kw) -> BookExport:
    return BookExport(
        title="Bored to Undeath",
        chapters=select_chapters(_e2e_chapters(), **kw),
    )


def _md_headings(md: str) -> list[str]:
    return [line for line in md.splitlines() if line.startswith("## ")]


class TestChapterTitles:
    @pytest.mark.parametrize(
        "raw, clean",
        [
            ("Chapter 1: Edric Thane", "Edric Thane"),
            ("Chapter 12 - The Ledger", "The Ledger"),
            ("chapter 3. Under", "Under"),
            ("Chapter 4 – Signature Sheets", "Signature Sheets"),
            ("Chapter Two: Retained", "Retained"),
            ("CHAPTER IV: The Office", "The Office"),
            ("Chapter One", ""),
            ("Chapter 7", ""),
            ("Chapter I Am Legend", "Chapter I Am Legend"),
            ("The Chapter 1 Problem", "The Chapter 1 Problem"),
            ("Quarterly Review", "Quarterly Review"),
            ("", ""),
        ],
    )
    def test_clean_chapter_title(self, raw, clean):
        assert clean_chapter_title(raw) == clean

    def test_heading_without_a_title_is_just_the_number(self):
        assert chapter_heading(3, "Chapter 3:") == "Chapter 3"
        assert chapter_heading(2, "Chapter 1: Edric") == "Chapter 2: Edric"


class TestMarkdown:
    def test_no_heading_carries_two_chapter_tokens(self):
        for heading in _md_headings(to_markdown(_book(skip_empty=False))):
            assert len(re.findall(r"\bChapter\b", heading)) == 1, heading

    def test_empty_chapters_are_skipped_by_default_numbered_by_position(self):
        assert _md_headings(to_markdown(_book())) == [
            "## Chapter 1: Edric Thane",
            "## Chapter 3: The Ledger",
            "## Chapter 5: Quarterly Review",
        ]

    def test_empty_chapters_are_kept_with_skip_empty_false(self):
        assert _md_headings(to_markdown(_book(skip_empty=False))) == [
            "## Chapter 1: Edric Thane",
            "## Chapter 2: Milo Voss",
            "## Chapter 3: The Ledger",
            "## Chapter 4: Signature Sheets",
            "## Chapter 5: Quarterly Review",
        ]

    def test_scene_break_between_scenes_of_a_chapter_only(self):
        md = to_markdown(_book())
        assert md.count(SCENE_BREAK) == 2  # chapters 1 and 5 have two scenes
        assert "He approves.\n\n* * *\n\nA new analyst closes well." in md
        # never before the first scene or after the last one of a chapter
        assert "## Chapter 3: The Ledger\n\n* * *" not in md
        assert "reconcile.\n\n* * *" not in md

    def test_a_range_keeps_book_numbering(self):
        md = to_markdown(_book(from_chapter=3, to_chapter=5))
        assert _md_headings(md) == [
            "## Chapter 3: The Ledger",
            "## Chapter 5: Quarterly Review",
        ]
        assert "Edric reviews" not in md

    def test_a_range_with_skip_empty_false_includes_planned_chapters(self):
        md = to_markdown(_book(skip_empty=False, from_chapter=2, to_chapter=2))
        assert _md_headings(md) == ["## Chapter 2: Milo Voss"]

    def test_select_does_not_mutate_its_input(self):
        chapters = _e2e_chapters()
        select_chapters(chapters, from_chapter=3)
        assert all(c.number is None for c in chapters)


class TestDocx:
    def _paragraphs(self, data: bytes):
        from docx import Document

        return Document(io.BytesIO(data)).paragraphs

    def test_centred_scene_break_and_clean_headings(self):
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        paras = self._paragraphs(to_docx(_book()))
        breaks = [p for p in paras if p.text == SCENE_BREAK]
        assert len(breaks) == 2
        assert all(p.alignment == WD_ALIGN_PARAGRAPH.CENTER for p in breaks)
        headings = [p.text for p in paras if p.style.name == "Heading 1"]
        assert headings == [
            "Chapter 1: Edric Thane",
            "Chapter 3: The Ledger",
            "Chapter 5: Quarterly Review",
        ]


class TestEpub:
    def _chapters(self, data: bytes) -> dict[str, str]:
        archive = zipfile.ZipFile(io.BytesIO(data))
        return {
            n: archive.read(n).decode()
            for n in archive.namelist()
            if re.search(r"chapter_\d+\.xhtml$", n)
        }

    def test_hr_between_scenes_and_book_numbered_files(self):
        files = self._chapters(to_epub(_book()))
        assert sorted(re.search(r"chapter_\d+", n).group(0) for n in files) == [
            "chapter_1",
            "chapter_3",
            "chapter_5",
        ]
        first = next(v for n, v in files.items() if n.endswith("chapter_1.xhtml"))
        assert first.count("<hr") == 1
        assert "Chapter 1: Edric Thane" in first
        third = next(v for n, v in files.items() if n.endswith("chapter_3.xhtml"))
        assert "<hr" not in third
