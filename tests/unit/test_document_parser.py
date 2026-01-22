"""Comprehensive unit tests for Document Parser"""

import pytest
import os
import tempfile
from unittest.mock import patch, MagicMock
import sys

# Add services to path
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "services", "document-parser"),
)

from parser import DocumentParser


@pytest.fixture
def parser():
    """Create document parser instance"""
    return DocumentParser()


@pytest.fixture
def temp_txt_file():
    """Create temporary text file for testing"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write("This is the first paragraph with some text.\n\n")
        f.write(
            "This is the second paragraph. It has multiple sentences. Each one matters.\n\n"
        )
        f.write("And this is the third paragraph for testing.\n")
        temp_path = f.name
    yield temp_path
    os.unlink(temp_path)


@pytest.fixture
def temp_html_file():
    """Create temporary HTML file for testing"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Test Document</title>
        <script>alert('ignored');</script>
        <style>body { color: black; }</style>
    </head>
    <body>
        <h1>Main Title</h1>
        <p>First paragraph of content.</p>
        <p>Second paragraph with <strong>bold text</strong>.</p>
        <div>
            <p>Nested content here.</p>
        </div>
    </body>
    </html>
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as f:
        f.write(html_content)
        temp_path = f.name
    yield temp_path
    os.unlink(temp_path)


@pytest.fixture
def temp_unicode_txt_file():
    """Create temporary text file with unicode content"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write("English text with some unicode: café, naïve, résumé.\n\n")
        f.write("Chinese characters: 你好世界\n\n")
        f.write("Japanese: こんにちは\n\n")
        f.write("Russian: Привет мир\n\n")
        f.write("Emoji support: 📚✍️🎭\n")
        temp_path = f.name
    yield temp_path
    os.unlink(temp_path)


@pytest.mark.unit
class TestDocumentParserInitialization:
    """Test DocumentParser initialization"""

    def test_document_parser_initialization(self, parser):
        """Test that DocumentParser can be initialized"""
        assert parser is not None
        assert parser.SUPPORTED_FORMATS == [".txt", ".docx", ".pdf", ".html", ".htm"]

    def test_supported_formats_are_lowercase(self, parser):
        """Test all supported formats are lowercase"""
        for fmt in parser.SUPPORTED_FORMATS:
            assert fmt == fmt.lower()
            assert fmt.startswith(".")


@pytest.mark.unit
class TestTextParsing:
    """Test text file parsing"""

    def test_parse_txt_file(self, parser, temp_txt_file):
        """Test parsing a text file"""
        content = parser.parse_document(temp_txt_file)

        assert "first paragraph" in content
        assert "second paragraph" in content
        assert "third paragraph" in content

    def test_parse_unicode_txt_file(self, parser, temp_unicode_txt_file):
        """Test parsing a text file with unicode content"""
        content = parser.parse_document(temp_unicode_txt_file)

        assert "café" in content
        assert "你好世界" in content
        assert "こんにちは" in content
        assert "Привет мир" in content
        assert "📚" in content

    def test_parse_empty_txt_file(self, parser):
        """Test parsing an empty text file"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            content = parser.parse_document(temp_path)
            assert content == ""
        finally:
            os.unlink(temp_path)

    def test_parse_txt_with_special_characters(self, parser):
        """Test parsing text with special characters"""
        special_content = "He said, \"Hello!\" then added: 'quotes within quotes'.\n\n"
        special_content += "Special chars: @#$%^&*()_+-=[]{}|;:,.<>?/~`\n"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(special_content)
            temp_path = f.name

        try:
            content = parser.parse_document(temp_path)
            assert '"Hello!"' in content
            assert "@#$%^&*" in content
        finally:
            os.unlink(temp_path)


@pytest.mark.unit
class TestHTMLParsing:
    """Test HTML file parsing"""

    def test_parse_html_file(self, parser, temp_html_file):
        """Test parsing an HTML file"""
        content = parser.parse_document(temp_html_file)

        assert "Main Title" in content
        assert "First paragraph" in content
        assert "bold text" in content
        assert "Nested content" in content

    def test_html_strips_script_tags(self, parser, temp_html_file):
        """Test that script tags are removed from HTML"""
        content = parser.parse_document(temp_html_file)

        assert "alert" not in content.lower()
        assert "<script>" not in content.lower()

    def test_html_strips_style_tags(self, parser, temp_html_file):
        """Test that style tags are removed from HTML"""
        content = parser.parse_document(temp_html_file)

        assert "color: black" not in content.lower()
        assert "<style>" not in content.lower()

    def test_html_preserves_text_content(self, parser):
        """Test that HTML text content is preserved"""
        html = """<html><body>
        <p>Paragraph one.</p>
        <p>Paragraph two.</p>
        <span>Inline text.</span>
        </body></html>"""

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            temp_path = f.name

        try:
            content = parser.parse_document(temp_path)
            assert "Paragraph one" in content
            assert "Paragraph two" in content
            assert "Inline text" in content
        finally:
            os.unlink(temp_path)

    def test_htm_extension_works(self, parser):
        """Test that .htm extension is handled same as .html"""
        html = "<html><body><p>Test content</p></body></html>"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".htm", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            temp_path = f.name

        try:
            content = parser.parse_document(temp_path)
            assert "Test content" in content
        finally:
            os.unlink(temp_path)


@pytest.mark.unit
class TestDocxParsing:
    """Test DOCX file parsing (mocked)"""

    @patch("parser.docx.Document")
    def test_parse_docx_basic(self, mock_docx_class, parser):
        """Test basic DOCX parsing with mock"""
        # Setup mock document
        mock_doc = MagicMock()
        mock_para1 = MagicMock()
        mock_para1.text = "First paragraph."
        mock_para2 = MagicMock()
        mock_para2.text = "Second paragraph."
        mock_para3 = MagicMock()
        mock_para3.text = ""  # Empty paragraph should be filtered

        mock_doc.paragraphs = [mock_para1, mock_para2, mock_para3]
        mock_docx_class.return_value = mock_doc

        # Create a fake docx file
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            f.write(b"PK")  # DOCX files are ZIP-based
            temp_path = f.name

        try:
            content = parser._parse_docx(temp_path)
            assert "First paragraph" in content
            assert "Second paragraph" in content
        finally:
            os.unlink(temp_path)

    @patch("parser.docx.Document")
    def test_parse_docx_filters_empty_paragraphs(self, mock_docx_class, parser):
        """Test that empty paragraphs are filtered"""
        mock_doc = MagicMock()
        mock_paragraphs = [
            MagicMock(text="Content 1"),
            MagicMock(text="   "),  # Whitespace only
            MagicMock(text=""),  # Empty
            MagicMock(text="Content 2"),
        ]
        mock_doc.paragraphs = mock_paragraphs
        mock_docx_class.return_value = mock_doc

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            f.write(b"PK")
            temp_path = f.name

        try:
            content = parser._parse_docx(temp_path)
            # Should have both content paragraphs joined
            assert "Content 1" in content
            assert "Content 2" in content
            # But not just whitespace
            parts = content.split("\n\n")
            assert all(p.strip() for p in parts if p)
        finally:
            os.unlink(temp_path)


@pytest.mark.unit
class TestPDFParsing:
    """Test PDF file parsing (mocked)"""

    @patch("parser.PyPDF2.PdfReader")
    def test_parse_pdf_basic(self, mock_reader_class, parser):
        """Test basic PDF parsing with mock"""
        # Setup mock reader
        mock_page1 = MagicMock()
        mock_page1.extract_text.return_value = "Page 1 content"
        mock_page2 = MagicMock()
        mock_page2.extract_text.return_value = "Page 2 content"

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page1, mock_page2]
        mock_reader_class.return_value = mock_reader

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4")
            temp_path = f.name

        try:
            content = parser._parse_pdf(temp_path)
            assert "Page 1 content" in content
            assert "Page 2 content" in content
        finally:
            os.unlink(temp_path)

    @patch("parser.PyPDF2.PdfReader")
    def test_parse_pdf_empty_pages_skipped(self, mock_reader_class, parser):
        """Test that empty PDF pages are skipped"""
        mock_page1 = MagicMock()
        mock_page1.extract_text.return_value = "Content"
        mock_page2 = MagicMock()
        mock_page2.extract_text.return_value = ""  # Empty page
        mock_page3 = MagicMock()
        mock_page3.extract_text.return_value = None  # None

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page1, mock_page2, mock_page3]
        mock_reader_class.return_value = mock_reader

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4")
            temp_path = f.name

        try:
            content = parser._parse_pdf(temp_path)
            assert "Content" in content
            # Should only have one paragraph (one valid page)
        finally:
            os.unlink(temp_path)

    @patch("parser.PyPDF2.PdfReader")
    def test_parse_pdf_no_text_raises_error(self, mock_reader_class, parser):
        """Test that PDF with no extractable text raises error"""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]
        mock_reader_class.return_value = mock_reader

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4")
            temp_path = f.name

        try:
            with pytest.raises(ValueError) as excinfo:
                parser._parse_pdf(temp_path)
            assert "No text could be extracted" in str(excinfo.value)
        finally:
            os.unlink(temp_path)


@pytest.mark.unit
class TestUnsupportedFormats:
    """Test handling of unsupported file formats"""

    def test_unsupported_extension_raises_error(self, parser):
        """Test that unsupported extensions raise ValueError"""
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            temp_path = f.name

        try:
            with pytest.raises(ValueError) as excinfo:
                parser.parse_document(temp_path)
            assert "Unsupported file type" in str(excinfo.value)
            assert ".xyz" in str(excinfo.value)
        finally:
            os.unlink(temp_path)

    def test_exe_file_rejected(self, parser):
        """Test that executable files are rejected"""
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            temp_path = f.name

        try:
            with pytest.raises(ValueError):
                parser.parse_document(temp_path)
        finally:
            os.unlink(temp_path)

    def test_case_insensitive_extension(self, parser, temp_txt_file):
        """Test that extensions are case insensitive"""
        # Rename to uppercase
        new_path = temp_txt_file.replace(".txt", ".TXT")
        os.rename(temp_txt_file, new_path)

        try:
            content = parser.parse_document(new_path)
            assert content is not None
        finally:
            # Cleanup - move back to original name for fixture cleanup
            os.rename(new_path, temp_txt_file)


@pytest.mark.unit
class TestFileNotFound:
    """Test handling of missing files"""

    def test_nonexistent_file_raises_error(self, parser):
        """Test that non-existent file raises FileNotFoundError"""
        with pytest.raises(FileNotFoundError) as excinfo:
            parser.parse_document("/nonexistent/path/to/file.txt")
        assert "not found" in str(excinfo.value).lower()

    def test_empty_path_raises_error(self, parser):
        """Test that empty path is handled"""
        with pytest.raises(FileNotFoundError):
            parser.parse_document("")


@pytest.mark.unit
class TestWordCount:
    """Test word counting functionality"""

    def test_word_count_basic(self, parser):
        """Test basic word count"""
        text = "This is a test sentence with eight words."
        count = parser.get_word_count(text)
        assert count == 8

    def test_word_count_empty_string(self, parser):
        """Test word count on empty string"""
        count = parser.get_word_count("")
        assert count == 0

    def test_word_count_whitespace_only(self, parser):
        """Test word count on whitespace only"""
        count = parser.get_word_count("   \n\t  \n  ")
        assert count == 0

    def test_word_count_multiple_spaces(self, parser):
        """Test word count with multiple spaces between words"""
        text = "Word1    Word2    Word3"
        count = parser.get_word_count(text)
        assert count == 3

    def test_word_count_with_punctuation(self, parser):
        """Test word count with punctuation"""
        text = "Hello, world! How are you?"
        count = parser.get_word_count(text)
        assert count == 5

    def test_word_count_multiline(self, parser):
        """Test word count across multiple lines"""
        text = "Line one.\nLine two.\nLine three."
        count = parser.get_word_count(text)
        assert count == 6

    def test_word_count_hyphenated_words(self, parser):
        """Test word count with hyphenated words"""
        text = "This is a well-known fact about twenty-first century."
        count = parser.get_word_count(text)
        # split() treats hyphenated words as single words
        assert count >= 7


@pytest.mark.unit
class TestParagraphCount:
    """Test paragraph counting functionality"""

    def test_paragraph_count_basic(self, parser):
        """Test basic paragraph count"""
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        count = parser.get_paragraph_count(text)
        assert count == 3

    def test_paragraph_count_empty_string(self, parser):
        """Test paragraph count on empty string"""
        count = parser.get_paragraph_count("")
        assert count == 0

    def test_paragraph_count_single_paragraph(self, parser):
        """Test paragraph count with single paragraph"""
        text = "This is just one paragraph with multiple sentences. Yes, really."
        count = parser.get_paragraph_count(text)
        assert count == 1

    def test_paragraph_count_filters_empty(self, parser):
        """Test that empty paragraphs are filtered"""
        text = "Para 1.\n\n\n\n\n\nPara 2."  # Multiple blank lines
        count = parser.get_paragraph_count(text)
        assert count == 2

    def test_paragraph_count_whitespace_only_paragraphs(self, parser):
        """Test that whitespace-only paragraphs are filtered"""
        text = "Real paragraph.\n\n   \n\n  \t  \n\nAnother real one."
        count = parser.get_paragraph_count(text)
        assert count == 2


@pytest.mark.unit
class TestEncodingHandling:
    """Test file encoding handling"""

    def test_utf8_encoding(self, parser, temp_unicode_txt_file):
        """Test UTF-8 encoded files"""
        content = parser.parse_document(temp_unicode_txt_file)
        assert len(content) > 0
        assert "café" in content

    def test_fallback_to_latin1(self, parser):
        """Test fallback to latin-1 encoding"""
        # Create a file with latin-1 encoding
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".txt", delete=False) as f:
            # Write some latin-1 specific characters
            f.write("Test with latin-1: é è ê ë".encode("latin-1"))
            temp_path = f.name

        try:
            content = parser.parse_document(temp_path)
            # Should not raise an error
            assert len(content) > 0
        finally:
            os.unlink(temp_path)


@pytest.mark.unit
class TestEdgeCases:
    """Test edge cases and boundary conditions"""

    def test_very_long_file(self, parser):
        """Test handling of very long files"""
        long_content = "Word " * 100000  # 100k words

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(long_content)
            temp_path = f.name

        try:
            content = parser.parse_document(temp_path)
            count = parser.get_word_count(content)
            assert count == 100000
        finally:
            os.unlink(temp_path)

    def test_single_character_file(self, parser):
        """Test file with single character"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("X")
            temp_path = f.name

        try:
            content = parser.parse_document(temp_path)
            assert content == "X"
            assert parser.get_word_count(content) == 1
        finally:
            os.unlink(temp_path)

    def test_newlines_only_file(self, parser):
        """Test file with only newlines"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("\n\n\n\n\n")
            temp_path = f.name

        try:
            content = parser.parse_document(temp_path)
            assert parser.get_word_count(content) == 0
            assert parser.get_paragraph_count(content) == 0
        finally:
            os.unlink(temp_path)


@pytest.mark.unit
class TestDialoguePatterns:
    """Test parsing of dialogue-heavy content"""

    def test_dialogue_quotes_preserved(self, parser):
        """Test that dialogue quotes are preserved"""
        dialogue = '"Hello," she said. "How are you today?"\n\n'
        dialogue += '"I am fine," he replied.\n\n'
        dialogue += "She nodded thoughtfully."

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(dialogue)
            temp_path = f.name

        try:
            content = parser.parse_document(temp_path)
            assert '"Hello,"' in content
            assert '"I am fine,"' in content
        finally:
            os.unlink(temp_path)

    def test_mixed_quote_styles(self, parser):
        """Test handling of different quote styles"""
        text = "He said \"hello\" and she said 'goodbye'.\n\n"
        text += '"This is speech," he noted.\n\n'
        text += "'Single quotes work too,' she added."

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(text)
            temp_path = f.name

        try:
            content = parser.parse_document(temp_path)
            assert '"hello"' in content
            assert "'goodbye'" in content
        finally:
            os.unlink(temp_path)
