"""Unit tests for RAG System"""

import pytest
from services.character_agent.rag_system import CharacterRAG


def test_character_rag_initialization():
    """Test that CharacterRAG can be initialized"""
    rag = CharacterRAG(
        character_id="test-001",
        character_name="TestChar",
        qdrant_url="http://localhost:6333"
    )

    assert rag is not None
    assert rag.character_id == "test-001"
    assert rag.character_name == "TestChar"
    assert rag.collection_name == "character_test_001"


# More comprehensive tests would require a running Qdrant instance
# These are placeholders for future integration tests
