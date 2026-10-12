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


def normalize_paragraphs(text: str) -> str:
    """Blank-line-separated paragraphs; whitespace inside a line collapsed,
    line breaks inside a paragraph kept (verse, letters' address blocks)."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    paras = []
    for block in re.split(r"\n\s*\n", text):
        lines = [" ".join(ln.split()) for ln in block.split("\n")]
        lines = [ln for ln in lines if ln]
        if lines:
            paras.append("\n".join(lines))
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
    """
    lines: list[str] = []
    for page in pages:
        page = page.replace("\r", "").strip("\n")
        page_lines = [ln.rstrip() for ln in page.split("\n")]
        lines.extend(page_lines)
        lines.append("\x01")  # page boundary
    widths = [len(ln.strip()) for ln in lines if ln.strip() and ln != "\x01"]
    typical = statistics.median(widths) if widths else 0

    paras: list[str] = []
    cur = ""

    def flush():
        nonlocal cur
        if cur.strip():
            paras.append(cur.strip())
        cur = ""

    for raw in lines:
        if raw == "\x01":
            continue
        ln = raw.strip()
        if not ln:
            flush()
            continue
        if cur.endswith("-") and ln[:1].islower():
            cur = cur[:-1] + ln
        else:
            cur = f"{cur} {ln}" if cur else ln
        short = typical and len(ln) < 0.75 * typical
        if short and ln.endswith(_TERMINAL):
            flush()
    flush()
    return normalize_paragraphs("\n\n".join(paras))
