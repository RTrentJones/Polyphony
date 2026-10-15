"""Assemble a book's current draft state into Markdown / DOCX / EPUB.

Exports use each scene's editable `content` (falling back to
`generated_content`), never raw generation output when an edit exists.

The output is a manuscript a beta reader can open as-is (SPEC-21): one
"Chapter N: Title" heading per chapter, where N is the chapter's position in the
book and any outline number already baked into the title is dropped; a
scene-break marker between scenes; and, by default, no headings for chapters
that have no prose yet.
"""

import html
import io
import re
from dataclasses import dataclass, field, replace
from typing import Optional

# The scene break between two scenes of one chapter (MD, DOCX; EPUB uses <hr/>).
SCENE_BREAK = "* * *"

_NUMBER_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    "fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty"
)
# A leading outline number: "Chapter 1: ", "Chapter 12 - ", "Chapter 3. ",
# "Chapter Two – ", "CHAPTER IV:". A digit may be followed by plain whitespace;
# a spelled-out or roman number needs punctuation or the end of the title, so
# "Chapter I Am Legend" is left alone.
_LEADING_CHAPTER = re.compile(
    r"^\s*chapter\s+(?:\d+(?:\s*[:.\-–—]\s*|\s+|\s*$)"
    rf"|(?:{_NUMBER_WORDS}|[ivxlc]+)(?:\s*[:.\-–—]\s*|\s*$))",
    re.IGNORECASE,
)


@dataclass
class SceneExport:
    title: str
    content: str


@dataclass
class ChapterExport:
    title: str
    summary: str = ""
    scenes: list[SceneExport] = field(default_factory=list)
    # 1-based position in the book. None = numbered by order in the export.
    number: Optional[int] = None


@dataclass
class BookExport:
    title: str
    author: str = ""
    synopsis: str = ""
    chapters: list[ChapterExport] = field(default_factory=list)


def scene_text(scene) -> str:
    """The draft state: edited content wins over raw generation output."""
    return (scene.content or scene.generated_content or "").strip()


def clean_chapter_title(title: str) -> str:
    """The title without a leading outline number ("Chapter 1: Edric Thane"
    -> "Edric Thane"), so the heading's number is always the book position."""
    return _LEADING_CHAPTER.sub("", title or "", count=1).strip()


def chapter_heading(number: int, title: str) -> str:
    clean = clean_chapter_title(title)
    return f"Chapter {number}: {clean}" if clean else f"Chapter {number}"


def select_chapters(
    chapters: list[ChapterExport],
    *,
    skip_empty: bool = True,
    from_chapter: Optional[int] = None,
    to_chapter: Optional[int] = None,
) -> list[ChapterExport]:
    """Number chapters by book position (1-based), then keep the requested
    inclusive range and, with `skip_empty`, only chapters that have prose."""
    picked: list[ChapterExport] = []
    for i, chapter in enumerate(chapters, 1):
        if from_chapter is not None and i < from_chapter:
            continue
        if to_chapter is not None and i > to_chapter:
            continue
        if skip_empty and not any(s.content.strip() for s in chapter.scenes):
            continue
        picked.append(replace(chapter, number=i))
    return picked


def _numbered(book: BookExport):
    for i, chapter in enumerate(book.chapters, 1):
        yield chapter.number or i, chapter


def _scene_texts(chapter: ChapterExport) -> list[str]:
    return [s.content.strip() for s in chapter.scenes if s.content.strip()]


def to_markdown(book: BookExport) -> str:
    parts = [f"# {book.title}"]
    if book.author:
        parts.append(f"*by {book.author}*")
    if book.synopsis:
        parts.append(f"> {book.synopsis}")
    parts.append("")
    for number, chapter in _numbered(book):
        parts.append(f"## {chapter_heading(number, chapter.title)}")
        parts.append("")
        for j, text in enumerate(_scene_texts(chapter)):
            if j:
                parts.append(SCENE_BREAK)
                parts.append("")
            parts.append(text)
            parts.append("")
        parts.append("")
    return "\n".join(parts).strip() + "\n"


def to_docx(book: BookExport) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    doc.add_heading(book.title, level=0)
    if book.author:
        doc.add_paragraph(f"by {book.author}")
    if book.synopsis:
        doc.add_paragraph(book.synopsis)
    for number, chapter in _numbered(book):
        doc.add_heading(chapter_heading(number, chapter.title), level=1)
        for j, text in enumerate(_scene_texts(chapter)):
            if j:
                brk = doc.add_paragraph(SCENE_BREAK)
                brk.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for para in text.split("\n\n"):
                para = para.strip()
                if para:
                    doc.add_paragraph(para)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def to_epub(book: BookExport) -> bytes:
    from ebooklib import epub

    e_book = epub.EpubBook()
    e_book.set_identifier(f"polyphony-{book.title[:40]}")
    e_book.set_title(book.title)
    e_book.set_language("en")
    if book.author:
        e_book.add_author(book.author)

    spine: list = ["nav"]
    toc = []
    for number, chapter in _numbered(book):
        chapter_title = chapter_heading(number, chapter.title)
        body = [f"<h1>{html.escape(chapter_title)}</h1>"]
        for j, text in enumerate(_scene_texts(chapter)):
            if j:
                body.append("<hr/>")
            for para in text.split("\n\n"):
                para = para.strip()
                if para:
                    body.append(f"<p>{html.escape(para)}</p>")
        item = epub.EpubHtml(
            title=chapter_title,
            file_name=f"chapter_{number}.xhtml",
            lang="en",
        )
        item.content = "\n".join(body)
        e_book.add_item(item)
        spine.append(item)
        toc.append(item)

    e_book.toc = toc
    e_book.spine = spine
    e_book.add_item(epub.EpubNcx())
    e_book.add_item(epub.EpubNav())

    buffer = io.BytesIO()
    epub.write_epub(buffer, e_book)
    return buffer.getvalue()


CONTENT_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "epub": "application/epub+zip",
}
