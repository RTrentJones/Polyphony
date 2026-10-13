"""Document parsing utilities for various file formats.

Every format comes out in ONE paragraph shape: paragraphs separated by a blank
line (SPEC-09, eval-audit backlog item 2). Everything downstream — extraction
windows, voice chunking — splits on "\\n\\n", so an HTML upload that joined its
lines with a single "\\n" used to become one paragraph the size of the whole
manuscript (embedding truncated it to ~256 tokens and the voice was gone), and a
PDF became one paragraph per page.
"""

import os
import re
import statistics

import docx
import PyPDF2
from bs4 import BeautifulSoup, NavigableString

# Elements that end a paragraph. Inline elements (em, span, a…) do not.
_BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "blockquote",
    "li",
    "ul",
    "ol",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "pre",
    "table",
    "tr",
    "hr",
    "header",
    "footer",
    "aside",
    "figure",
    "figcaption",
    "dd",
    "dt",
    "address",
}
_PARA_MARK = "\x00PARA\x00"
_LINE_MARK = "\x00BR\x00"
_TERMINAL = tuple(".!?:;\"'”’)]—")


# A line that ends a sentence: . ! ? (or …), optionally inside closing quotes.
_SENTENCE_END = re.compile(r"[.!?\u2026][\"'\u201d\u2019)\]]*$")
# Shorter than this fraction of the document's typical line = a "short" line.
_SHORT = 0.75
# No verse line is longer than this.
_MAX_VERSE = 90


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _tidy(line: str, keep_indent: bool) -> str:
    """Collapse whitespace inside a line; keep its leading indentation only
    when it is verse-like (the indentation IS the layout there)."""
    body = " ".join(line.split())
    return (" " * _indent(line) + body) if keep_indent and body else body


class _LineStats:
    """Document-wide line widths: what counts as a short line, and how deep
    ordinary prose is indented (so verse must be indented deeper)."""

    def __init__(self, lines: list[str]):
        widths = [len(ln.strip()) for ln in lines if ln.strip()]
        # The PDF paragraph-end rule's "typical line" (unchanged: the median).
        self.typical = statistics.median(widths) if widths else 0
        # Verse detection measures against the prose line, so a poem that is a
        # large share of a short text can't drag the yardstick down to its own
        # width: the 80th percentile is the wrapped prose line when there is
        # any. Verse lines are also capped absolutely (an unwrapped paragraph
        # is never a verse line).
        wide = statistics.quantiles(widths, n=10)[7] if len(widths) > 1 else 0
        self.verse_limit = min(_SHORT * wide, _MAX_VERSE)
        self.prose_indent = max(
            (_indent(ln) for ln in lines if len(ln.strip()) >= self.verse_limit),
            default=0,
        )

    def short(self, line: str) -> bool:
        return 0 < len(line.strip()) < self.verse_limit


def _is_verse_run(run: list[str], stats: _LineStats) -> bool:
    """A run of >=2 consecutive short lines is verse-like when its lines carry
    the same indentation, deeper than prose's; or when most of them (the last
    line aside, which may close the stanza) end without sentence punctuation —
    a ragged prose paragraph ends ONE short line, a poem many."""
    if len(run) < 2:
        return False
    indents = {_indent(ln) for ln in run}
    if len(indents) == 1 and min(indents) > stats.prose_indent:
        return True
    body = run[:-1]
    open_lines = sum(not _SENTENCE_END.search(ln.strip()) for ln in body)
    return open_lines >= 1 and 2 * open_lines >= len(body)


def _verse_mask(lines: list[str], stats: _LineStats) -> list[bool]:
    """Per line: is it part of a verse-like run? `lines` are consecutive,
    non-blank lines of one block."""
    mask = [False] * len(lines)
    i = 0
    while i < len(lines):
        if not stats.short(lines[i]):
            i += 1
            continue
        j = i
        while j < len(lines) and stats.short(lines[j]):
            j += 1
        # A short line right after a full-width one is that paragraph's ragged
        # last line, not the poem's first — unless it is indented deeper than
        # prose (then the indent says verse). Leave the tail with its prose.
        tail = i > 0 and _indent(lines[i]) <= stats.prose_indent
        start = i + 1 if tail else i
        if _is_verse_run(lines[start:j], stats):
            mask[start:j] = [True] * (j - start)
        i = j
    return mask


def _prep(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)


def normalize_paragraphs(text: str) -> str:
    """Blank-line-separated paragraphs; whitespace inside a line collapsed,
    line breaks inside a paragraph kept (verse, letters' address blocks).

    Verse-like runs (SPEC-09) also keep their leading indentation: a poem
    embedded in prose is laid out by its indents, and flattening them lost
    the structure. Prose lines are collapsed exactly as before."""
    text = _prep(text)
    blocks = re.split(r"\n\s*\n", text)
    stats = _LineStats(text.split("\n"))
    paras = []
    for block in blocks:
        lines = [ln.rstrip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        mask = _verse_mask(lines, stats)
        paras.append("\n".join(_tidy(ln, v) for ln, v in zip(lines, mask)))
    return "\n\n".join(paras)


class DocumentParser:
    """Parse various document formats into plain text"""

    SUPPORTED_FORMATS = [".txt", ".docx", ".pdf", ".html", ".htm"]

    @staticmethod
    def format_of(file_path: str) -> str:
        """The source-format covariate recorded on Source.format."""
        ext = os.path.splitext(file_path)[1].lower().lstrip(".")
        return "html" if ext == "htm" else ext

    def parse_document(self, file_path: str) -> str:
        """
        Parse document based on extension

        Args:
            file_path: Path to the document file

        Returns:
            Full text content as string, paragraphs separated by blank lines

        Raises:
            ValueError: If file format is not supported
            FileNotFoundError: If file doesn't exist
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()

        if ext not in self.SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported file type: {ext}. "
                f"Supported formats: {', '.join(self.SUPPORTED_FORMATS)}"
            )

        if ext == ".docx":
            return self._parse_docx(file_path)
        elif ext == ".pdf":
            return self._parse_pdf(file_path)
        elif ext == ".txt":
            return self._parse_txt(file_path)
        elif ext in [".html", ".htm"]:
            return self._parse_html(file_path)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

    def _parse_docx(self, file_path: str) -> str:
        """Parse DOCX file: one Word paragraph = one paragraph."""
        try:
            doc = docx.Document(file_path)
            paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
            return normalize_paragraphs("\n\n".join(paragraphs))
        except Exception as e:
            raise ValueError(f"Error parsing DOCX file: {e}")

    def _parse_pdf(self, file_path: str) -> str:
        """Parse PDF file, re-joining hard-wrapped lines into paragraphs."""
        try:
            pages = []
            with open(file_path, "rb") as file:
                reader = PyPDF2.PdfReader(file)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        pages.append(page_text)

            if not pages:
                raise ValueError("No text could be extracted from PDF")

            return pdf_pages_to_paragraphs(pages)
        except Exception as e:
            raise ValueError(f"Error parsing PDF file: {e}")

    def _parse_txt(self, file_path: str) -> str:
        """Parse TXT file"""
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                return normalize_paragraphs(file.read())
        except UnicodeDecodeError:
            # Try with different encoding
            try:
                with open(file_path, "r", encoding="latin-1") as file:
                    return normalize_paragraphs(file.read())
            except Exception as e:
                raise ValueError(f"Error parsing TXT file: {e}")

    def _parse_html(self, file_path: str) -> str:
        """Parse HTML file"""
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                return html_to_paragraphs(file.read())
        except Exception as e:
            raise ValueError(f"Error parsing HTML file: {e}")

    def get_word_count(self, text: str) -> int:
        """Get word count from text"""
        return len(text.split())

    def get_paragraph_count(self, text: str) -> int:
        """Get paragraph count from text"""
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        return len(paragraphs)


def html_to_paragraphs(html: str) -> str:
    """Block elements become paragraphs; <br> a line break; source newlines
    (just HTML formatting) become spaces."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head", "title", "noscript"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with(NavigableString(_LINE_MARK))
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_before(NavigableString(_PARA_MARK))
        tag.insert_after(NavigableString(_PARA_MARK))
    text = soup.get_text()
    # Whitespace in HTML source is not structure.
    text = " ".join(text.split())
    text = text.replace(_LINE_MARK, "\n").replace(_PARA_MARK, "\n\n")
    return normalize_paragraphs(text)


def pdf_pages_to_paragraphs(pages: list[str]) -> str:
    """Line-joining heuristic for PDF text.

    PDF extraction yields hard-wrapped lines and page breaks with no paragraph
    marks. A line ends a paragraph when it is followed by a blank line, or when
    it ends a sentence AND is clearly shorter than the page's typical line (the
    ragged last line of a paragraph). A page break continues the paragraph
    unless the page's last line ended one. Hyphenated wraps are re-joined.

    Verse-like runs (see `_is_verse_run`) are NOT joined: a poem inside the
    prose becomes its own paragraph, one line per verse line, with its
    indentation (SPEC-09).
    """
    lines: list[str] = []
    for page in pages:
        page = _prep(page).strip("\n")
        lines.extend(ln.rstrip() for ln in page.split("\n"))
    stats = _LineStats(lines)
    # Verse runs are found per stretch of non-blank lines (a page break does
    # not end a stretch: the paragraph, or the poem, runs on).
    verse = [False] * len(lines)
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip():
            j += 1
        verse[i:j] = _verse_mask(lines[i:j], stats)
        i = j

    paras: list[str] = []
    cur = ""
    poem: list[str] = []

    def flush():
        nonlocal cur
        if cur.strip():
            paras.append(" ".join(cur.split()))
        cur = ""

    def flush_poem():
        if poem:
            paras.append("\n".join(poem))
            poem.clear()

    for raw, is_verse in zip(lines, verse):
        ln = raw.strip()
        if is_verse:
            flush()
            poem.append(_tidy(raw, True))
            continue
        flush_poem()
        if not ln:
            flush()
            continue
        if cur.endswith("-") and ln[:1].islower():
            cur = cur[:-1] + ln
        else:
            cur = f"{cur} {ln}" if cur else ln
        short = stats.typical and len(ln) < _SHORT * stats.typical
        if short and ln.endswith(_TERMINAL):
            flush()
    flush()
    flush_poem()
    return "\n\n".join(paras)
