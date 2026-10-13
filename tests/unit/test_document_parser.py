"""Unit tests for Document Parser"""

from app.parsing.parser import (
    DocumentParser,
    normalize_paragraphs,
    pdf_pages_to_paragraphs,
)


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


# --- SPEC-09: verse survives normalisation ---------------------------------

PROSE_A = (
    "The old man set his cup down on the table and looked out at the rain for\n"
    "a long while before he spoke, and when he did it was in the voice of a man\n"
    "reciting something learned long ago.\n"
)
POEM = (
    "    The fog comes in on silent feet,\n"
    "    and sits upon the harbour wall,\n"
    "        it watches ships it will not meet\n"
    "    and leaves before the evening fall.\n"
)
PROSE_B = (
    "Nobody at the table said anything for a moment, and then the girl laughed\n"
    "and asked him where he had learned such a thing, and he would not tell her.\n"
)


def test_plain_text_poem_keeps_its_indentation_and_line_breaks():
    out = normalize_paragraphs(PROSE_A + "\n" + POEM + "\n" + PROSE_B)
    paras = out.split("\n\n")
    assert len(paras) == 3
    assert paras[1].split("\n") == [
        "    The fog comes in on silent feet,",
        "    and sits upon the harbour wall,",
        "        it watches ships it will not meet",
        "    and leaves before the evening fall.",
    ]


def test_plain_text_prose_around_the_poem_is_unchanged():
    out = normalize_paragraphs(PROSE_A + "\n" + POEM + "\n" + PROSE_B)
    paras = out.split("\n\n")
    # Prose: whitespace collapsed, no indentation added, line breaks as before.
    assert paras[0] == "\n".join(ln.strip() for ln in PROSE_A.strip().split("\n"))
    assert paras[2] == "\n".join(ln.strip() for ln in PROSE_B.strip().split("\n"))


def test_unindented_verse_is_detected_by_open_line_endings():
    poem = (
        "the fog comes in\non little cat feet\nit sits looking\nover harbor and city\n"
    )
    out = normalize_paragraphs(PROSE_A + "\n" + poem + "\n" + PROSE_B)
    assert out.split("\n\n")[1] == poem.strip()


def test_indented_prose_paragraphs_are_not_mistaken_for_verse():
    # First-line-indented prose and short indented dialogue: the dialogue is
    # indented no deeper than the prose, and ends its sentences — prose.
    text = (
        "    The rain had not stopped since morning, and the street outside was a\n"
        "river of grey water that nobody in the house wanted to cross today.\n"
        '    "Yes," she said.\n'
        '    "No," he said.\n'
        '    "Why not?"\n'
    )
    assert normalize_paragraphs(text).split("\n")[2:] == [
        '"Yes," she said.',
        '"No," he said.',
        '"Why not?"',
    ]


def test_txt_upload_keeps_the_poem(tmp_path):
    f = tmp_path / "book.txt"
    f.write_text(PROSE_A + "\n" + POEM + "\n" + PROSE_B, encoding="utf-8")
    out = DocumentParser().parse_document(str(f))
    assert "\n        it watches ships it will not meet\n" in out


def test_tabs_are_indentation_too():
    text = PROSE_A + "\n\tline one of a song,\n\tline two of a song\n\n" + PROSE_B
    assert "    line one of a song,\n    line two of a song" in normalize_paragraphs(
        text
    )


def test_pdf_poem_inside_prose_is_not_joined():
    page1 = (
        "It was a dark and stormy night and the wind howled through\n"
        "the trees of the old forest where nobody went, and the old\n"
        "man began to recite.\n"
        "  The fog comes in on silent feet,\n"
        "  and sits upon the harbour wall,\n"
    )
    page2 = (
        "    it watches ships it will not meet\n"
        "  and leaves before the evening fall.\n"
        "Nobody said anything for a moment, and then the girl laughed\n"
        "and asked him where he had learned such a thing, and he\n"
        "would not tell her.\n"
    )
    paras = pdf_pages_to_paragraphs([page1, page2]).split("\n\n")
    assert paras == [
        "It was a dark and stormy night and the wind howled through the trees of "
        "the old forest where nobody went, and the old man began to recite.",
        "  The fog comes in on silent feet,\n"
        "  and sits upon the harbour wall,\n"
        "    it watches ships it will not meet\n"
        "  and leaves before the evening fall.",
        "Nobody said anything for a moment, and then the girl laughed and asked "
        "him where he had learned such a thing, and he would not tell her.",
    ]


def test_pdf_unindented_verse_is_kept_line_by_line():
    page = (
        "It was a dark and stormy night and the wind howled through\n"
        "the trees of the old forest where the wind always blew and\n"
        "nobody went.\n"
        "the fog comes in\n"
        "on little cat feet\n"
        "it sits looking\n"
        "over harbor and city.\n"
        "Nobody said anything for a moment, and then the girl laughed\n"
        "and asked him where he had learned such a thing, and he\n"
        "would not tell her.\n"
    )
    paras = pdf_pages_to_paragraphs([page]).split("\n\n")
    assert len(paras) == 3
    assert paras[1].split("\n") == [
        "the fog comes in",
        "on little cat feet",
        "it sits looking",
        "over harbor and city.",
    ]
    # The ragged prose tail stays with its paragraph, not the poem.
    assert paras[0].endswith("blew and nobody went.")


def test_pdf_prose_reflow_is_unchanged():
    # Same expectation as before the verse rule: wrapped lines and a page
    # break re-join; the ragged short last line ends the paragraph.
    page1 = (
        "It was a dark and stormy night and the wind\n"
        "howled through the trees of the old forest where\n"
        "nobody went.\n"
        "The next morning Mina rose early and walked to\n"
        "the cliff above the har-\n"
    )
    page2 = "bour, where the ships lay at anchor in the grey\nlight.\n"
    assert pdf_pages_to_paragraphs([page1, page2]).split("\n\n") == [
        "It was a dark and stormy night and the wind howled through the trees "
        "of the old forest where nobody went.",
        "The next morning Mina rose early and walked to the cliff above the "
        "harbour, where the ships lay at anchor in the grey light.",
    ]
