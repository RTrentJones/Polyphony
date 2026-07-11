"""Unit tests for character content/dialogue extraction — the voice-indexing path.

Regression guards for the smart-quote fix: real prose uses curly quotes, and the
old straight-quote-only patterns found zero dialogue, so every character's spoken
voice was invisible to indexing.
"""

import pytest

from app.core.models import ChunkType
from app.parsing.character_extractor import CharacterExtractor

pytestmark = pytest.mark.unit

# Curly quotes + inverted attribution — the ubiquitous published-prose form.
SMART = (
    "The room was cold.\n\n"
    "“I will not stay here another night,” said Verhoeven, rising from his chair.\n\n"
    "“You must,” Nora replied, “there is no other way.”\n\n"
    "Verhoeven considered this for a long moment."
)


class TestDialogueExtraction:
    def test_smart_quote_inversion_is_extracted(self):
        ce = CharacterExtractor()
        lines = ce.extract_dialogue_only(SMART, "Verhoeven")
        assert any("another night" in ln for ln in lines)

    def test_straight_quotes_still_work(self):
        ce = CharacterExtractor()
        txt = 'Mary said, "I remember everything about that day."'
        lines = ce.extract_dialogue_only(txt, "Mary")
        assert any("remember everything" in ln for ln in lines)

    def test_no_false_dialogue_for_silent_character(self):
        ce = CharacterExtractor()
        assert ce.extract_dialogue_only(SMART, "Aldous") == []


class TestVoiceChunking:
    def test_spoken_lines_indexed_as_dialogue_chunks(self):
        # A speaking character's dialogue must reach the indexed chunks as
        # chunk_type=dialogue (previously 0 across whole corpora).
        ce = CharacterExtractor()
        chunks = ce.extract_character_content(SMART, "Verhoeven")
        stats = ce.get_character_statistics(chunks)
        assert stats["dialogue_count"] >= 1
        assert any(
            c["chunk_type"] == ChunkType.DIALOGUE.value and "another night" in c["text"]
            for c in chunks
        )

    def test_chunks_deduped(self):
        ce = CharacterExtractor()
        chunks = ce.extract_character_content(SMART, "Verhoeven")
        texts = [c["text"] for c in chunks]
        assert len(texts) == len(set(texts))
