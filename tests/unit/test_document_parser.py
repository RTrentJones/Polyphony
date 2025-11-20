"""Unit tests for Document Parser"""

from services.document_parser.parser import DocumentParser


def test_document_parser_initialization():
    """Test that DocumentParser can be initialized"""
    parser = DocumentParser()
    assert parser is not None
    assert parser.SUPPORTED_FORMATS == [".txt", ".docx", ".pdf", ".html", ".htm"]


def test_word_count():
    """Test word count functionality"""
    parser = DocumentParser()
    text = "This is a test sentence with seven words."
    count = parser.get_word_count(text)
    assert count == 8


def test_paragraph_count():
    """Test paragraph count functionality"""
    parser = DocumentParser()
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    count = parser.get_paragraph_count(text)
    assert count == 3


# Add more tests as needed
