"""Unit tests for RAG System"""

import pytest
import os
import sys

# Fix import path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from services.character_agent.rag_system import CharacterRAG


@pytest.mark.unit
class TestCharacterRAG:
    """Test Character RAG system"""

    def test_character_rag_initialization(self):
        """Test that CharacterRAG can be initialized"""
        rag = CharacterRAG(
            character_id="test-001",
            character_name="TestChar",
            qdrant_url="http://localhost:6333",
        )

        assert rag is not None
        assert rag.character_id == "test-001"
        assert rag.character_name == "TestChar"
        assert rag.collection_name == "character_test_001"

    def test_collection_name_format(self):
        """Test collection name formatting"""
        rag = CharacterRAG(
            character_id="char-123-456",
            character_name="TestChar",
            qdrant_url="http://localhost:6333",
        )

        # Hyphens should be replaced with underscores
        assert rag.collection_name == "character_char_123_456"

    def test_embedding_model_loaded(self):
        """Test that embedding model is loaded"""
        rag = CharacterRAG(
            character_id="test-001",
            character_name="TestChar",
            qdrant_url="http://localhost:6333",
        )

        assert rag.embedding_model is not None
        assert rag.vector_size == 384  # all-MiniLM-L6-v2 dimension


# Integration tests requiring Qdrant are marked separately
@pytest.mark.integration
@pytest.mark.database
class TestCharacterRAGIntegration:
    """Integration tests for RAG (require Qdrant running)"""

    @pytest.mark.asyncio
    async def test_create_collection(self):
        """Test creating Qdrant collection"""
        # This test requires actual Qdrant instance
        # Skip if not available
        pytest.skip("Requires Qdrant instance running")

    @pytest.mark.asyncio
    async def test_index_content(self):
        """Test indexing content"""
        pytest.skip("Requires Qdrant instance running")
