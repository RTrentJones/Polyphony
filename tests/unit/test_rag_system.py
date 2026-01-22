"""Comprehensive unit tests for RAG System"""

import pytest
import os
import sys
import importlib.util
from unittest.mock import AsyncMock, MagicMock, patch
import numpy as np

# Fix import path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

# Dynamically load the rag_system module from character-agent directory
_rag_module_path = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "services",
    "character-agent",
    "rag_system.py",
)
_spec = importlib.util.spec_from_file_location("rag_system", _rag_module_path)
if _spec is None or not os.path.exists(_rag_module_path):
    pytestmark = pytest.mark.skip("RAG system module not found")
    rag_system_module = None
else:
    try:
        rag_system_module = importlib.util.module_from_spec(_spec)
        # Register it in sys.modules so patch can find it
        sys.modules["rag_system"] = rag_system_module
        _spec.loader.exec_module(rag_system_module)
        CharacterRAG = rag_system_module.CharacterRAG
    except Exception as e:
        pytestmark = pytest.mark.skip(f"Cannot load RAG system module: {e}")
        rag_system_module = None
        CharacterRAG = None


@pytest.fixture
def mock_qdrant_client():
    """Create mock Qdrant client"""
    return AsyncMock()


@pytest.fixture
def mock_embedding_model():
    """Create mock embedding model"""
    mock = MagicMock()
    mock.get_sentence_embedding_dimension.return_value = 384
    mock.encode.return_value = np.random.rand(384)
    return mock


@pytest.fixture
def rag_system(mock_embedding_model):
    """Create CharacterRAG instance with mocked dependencies"""
    with patch("rag_system.AsyncQdrantClient") as mock_qdrant, patch(
        "rag_system.SentenceTransformer"
    ) as mock_st:
        mock_st.return_value = mock_embedding_model
        mock_client = AsyncMock()
        mock_qdrant.return_value = mock_client

        from rag_system import CharacterRAG

        rag = CharacterRAG(
            character_id="test-char-001",
            character_name="TestCharacter",
            qdrant_url="http://localhost:6333",
        )
        rag.qdrant = mock_client
        return rag


@pytest.mark.unit
class TestCharacterRAGInitialization:
    """Test CharacterRAG initialization"""

    def test_character_rag_initialization(self):
        """Test that CharacterRAG can be initialized"""
        with patch("rag_system.AsyncQdrantClient"), patch(
            "rag_system.SentenceTransformer"
        ) as mock_st:
            mock_model = MagicMock()
            mock_model.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value = mock_model

            from rag_system import CharacterRAG

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
        with patch("rag_system.AsyncQdrantClient"), patch(
            "rag_system.SentenceTransformer"
        ) as mock_st:
            mock_model = MagicMock()
            mock_model.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value = mock_model

            from rag_system import CharacterRAG

            rag = CharacterRAG(
                character_id="char-123-456",
                character_name="TestChar",
                qdrant_url="http://localhost:6333",
            )

            # Hyphens should be replaced with underscores
            assert rag.collection_name == "character_char_123_456"

    def test_embedding_model_loaded(self):
        """Test that embedding model is loaded"""
        with patch("rag_system.AsyncQdrantClient"), patch(
            "rag_system.SentenceTransformer"
        ) as mock_st:
            mock_model = MagicMock()
            mock_model.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value = mock_model

            from rag_system import CharacterRAG

            rag = CharacterRAG(
                character_id="test-001",
                character_name="TestChar",
                qdrant_url="http://localhost:6333",
            )

            assert rag.embedding_model is not None
            assert rag.vector_size == 384

    def test_custom_embedding_model(self):
        """Test using custom embedding model"""
        with patch("rag_system.AsyncQdrantClient"), patch(
            "rag_system.SentenceTransformer"
        ) as mock_st:
            mock_model = MagicMock()
            mock_model.get_sentence_embedding_dimension.return_value = 768
            mock_st.return_value = mock_model

            from rag_system import CharacterRAG

            rag = CharacterRAG(
                character_id="test-001",
                character_name="TestChar",
                qdrant_url="http://localhost:6333",
                embedding_model_name="custom-model",
            )

            mock_st.assert_called_with("custom-model")
            assert rag.vector_size == 768


@pytest.mark.unit
class TestCreateCollection:
    """Test collection creation"""

    @pytest.mark.asyncio
    async def test_create_collection_new(self, rag_system):
        """Test creating a new collection"""
        # Mock empty collections list
        mock_collections = MagicMock()
        mock_collections.collections = []
        rag_system.qdrant.get_collections.return_value = mock_collections
        rag_system.qdrant.create_collection.return_value = True

        result = await rag_system.create_collection()

        assert result is True
        rag_system.qdrant.create_collection.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_collection_already_exists(self, rag_system):
        """Test creating collection that already exists"""
        # Mock collections list with existing collection
        mock_collection = MagicMock()
        mock_collection.name = rag_system.collection_name
        mock_collections = MagicMock()
        mock_collections.collections = [mock_collection]
        rag_system.qdrant.get_collections.return_value = mock_collections

        result = await rag_system.create_collection()

        assert result is True
        rag_system.qdrant.create_collection.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_collection_error(self, rag_system):
        """Test collection creation error handling"""
        rag_system.qdrant.get_collections.side_effect = Exception("Connection error")

        result = await rag_system.create_collection()

        assert result is False

    @pytest.mark.asyncio
    async def test_create_collection_vector_params(self, rag_system):
        """Test that collection is created with correct vector parameters"""
        mock_collections = MagicMock()
        mock_collections.collections = []
        rag_system.qdrant.get_collections.return_value = mock_collections
        rag_system.qdrant.create_collection.return_value = True

        await rag_system.create_collection()

        call_args = rag_system.qdrant.create_collection.call_args
        assert call_args.kwargs["collection_name"] == rag_system.collection_name
        # Vector params should be configured
        vectors_config = call_args.kwargs["vectors_config"]
        assert vectors_config.size == 384  # all-MiniLM-L6-v2 dimension


@pytest.mark.unit
class TestIndexCharacterContent:
    """Test content indexing"""

    @pytest.mark.asyncio
    async def test_index_content_empty_chunks(self, rag_system):
        """Test indexing with empty chunks list"""
        result = await rag_system.index_character_content([])

        assert result == 0
        rag_system.qdrant.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_index_content_single_chunk(self, rag_system):
        """Test indexing a single chunk"""
        mock_collections = MagicMock()
        mock_collections.collections = []
        rag_system.qdrant.get_collections.return_value = mock_collections
        rag_system.qdrant.create_collection.return_value = True
        rag_system.qdrant.upsert.return_value = True

        chunks = [
            {
                "text": "Hello, this is test dialogue.",
                "chunk_type": "dialogue",
                "source_location": "paragraph_1",
            }
        ]

        result = await rag_system.index_character_content(chunks)

        assert result == 1
        rag_system.qdrant.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_index_content_multiple_chunks(self, rag_system):
        """Test indexing multiple chunks"""
        mock_collections = MagicMock()
        mock_collection = MagicMock()
        mock_collection.name = rag_system.collection_name
        mock_collections.collections = [mock_collection]
        rag_system.qdrant.get_collections.return_value = mock_collections
        rag_system.qdrant.upsert.return_value = True

        chunks = [
            {"text": "Chunk 1", "chunk_type": "dialogue", "source_location": "p1"},
            {"text": "Chunk 2", "chunk_type": "action", "source_location": "p2"},
            {"text": "Chunk 3", "chunk_type": "thought", "source_location": "p3"},
        ]

        result = await rag_system.index_character_content(chunks)

        assert result == 3

    @pytest.mark.asyncio
    async def test_index_content_batching(self, rag_system):
        """Test that large content is batched correctly"""
        mock_collections = MagicMock()
        mock_collection = MagicMock()
        mock_collection.name = rag_system.collection_name
        mock_collections.collections = [mock_collection]
        rag_system.qdrant.get_collections.return_value = mock_collections
        rag_system.qdrant.upsert.return_value = True

        # Create 150 chunks (batch_size default is 100)
        chunks = [
            {"text": f"Chunk {i}", "chunk_type": "dialogue", "source_location": f"p{i}"}
            for i in range(150)
        ]

        result = await rag_system.index_character_content(chunks, batch_size=100)

        assert result == 150
        # Should be called twice (100 + 50)
        assert rag_system.qdrant.upsert.call_count == 2

    @pytest.mark.asyncio
    async def test_index_content_payload_structure(self, rag_system):
        """Test that indexed points have correct payload structure"""
        mock_collections = MagicMock()
        mock_collection = MagicMock()
        mock_collection.name = rag_system.collection_name
        mock_collections.collections = [mock_collection]
        rag_system.qdrant.get_collections.return_value = mock_collections
        rag_system.qdrant.upsert.return_value = True

        chunks = [
            {
                "text": "Test content here",
                "chunk_type": "dialogue",
                "source_location": "paragraph_5",
            }
        ]

        await rag_system.index_character_content(chunks)

        # Get the points that were upserted
        call_args = rag_system.qdrant.upsert.call_args
        points = call_args.kwargs["points"]

        assert len(points) == 1
        payload = points[0].payload

        assert payload["character_id"] == rag_system.character_id
        assert payload["character_name"] == rag_system.character_name
        assert payload["chunk_type"] == "dialogue"
        assert payload["text"] == "Test content here"
        assert payload["source_location"] == "paragraph_5"
        assert payload["word_count"] == 3
        assert "timestamp" in payload

    @pytest.mark.asyncio
    async def test_index_content_error_handling(self, rag_system):
        """Test error handling during indexing"""
        mock_collections = MagicMock()
        mock_collection = MagicMock()
        mock_collection.name = rag_system.collection_name
        mock_collections.collections = [mock_collection]
        rag_system.qdrant.get_collections.return_value = mock_collections
        rag_system.qdrant.upsert.side_effect = Exception("Upload failed")

        chunks = [{"text": "Test", "chunk_type": "dialogue", "source_location": "p1"}]

        result = await rag_system.index_character_content(chunks)

        assert result == 0


@pytest.mark.unit
class TestRetrieveSimilarDialogue:
    """Test similarity search"""

    @pytest.mark.asyncio
    async def test_retrieve_similar_basic(self, rag_system):
        """Test basic similarity retrieval"""
        # Mock search results
        mock_hit = MagicMock()
        mock_hit.payload = {
            "text": "Similar dialogue here",
            "chunk_type": "dialogue",
            "source_location": "paragraph_1",
            "word_count": 3,
        }
        mock_hit.score = 0.85
        rag_system.qdrant.search.return_value = [mock_hit]

        results = await rag_system.retrieve_similar_dialogue(
            query="Test query",
            k=5,
        )

        assert len(results) == 1
        assert results[0]["text"] == "Similar dialogue here"
        assert results[0]["score"] == 0.85
        assert results[0]["chunk_type"] == "dialogue"

    @pytest.mark.asyncio
    async def test_retrieve_similar_with_chunk_filter(self, rag_system):
        """Test retrieval with chunk type filter"""
        mock_hit = MagicMock()
        mock_hit.payload = {
            "text": "Dialogue only",
            "chunk_type": "dialogue",
            "source_location": "p1",
            "word_count": 2,
        }
        mock_hit.score = 0.9
        rag_system.qdrant.search.return_value = [mock_hit]

        results = await rag_system.retrieve_similar_dialogue(
            query="Test",
            k=5,
            chunk_type="dialogue",
        )

        # Verify filter was applied
        call_args = rag_system.qdrant.search.call_args
        assert call_args.kwargs["query_filter"] is not None
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_retrieve_similar_with_score_threshold(self, rag_system):
        """Test retrieval with score threshold"""
        rag_system.qdrant.search.return_value = []

        await rag_system.retrieve_similar_dialogue(
            query="Test",
            k=5,
            score_threshold=0.8,
        )

        call_args = rag_system.qdrant.search.call_args
        assert call_args.kwargs["score_threshold"] == 0.8

    @pytest.mark.asyncio
    async def test_retrieve_similar_multiple_results(self, rag_system):
        """Test retrieval with multiple results"""
        mock_hits = []
        for i in range(3):
            hit = MagicMock()
            hit.payload = {
                "text": f"Result {i}",
                "chunk_type": "dialogue",
                "source_location": f"p{i}",
                "word_count": 2,
            }
            hit.score = 0.9 - (i * 0.1)
            mock_hits.append(hit)

        rag_system.qdrant.search.return_value = mock_hits

        results = await rag_system.retrieve_similar_dialogue(query="Test", k=3)

        assert len(results) == 3
        # Should be ordered by score (highest first)
        assert results[0]["score"] >= results[1]["score"]
        assert results[1]["score"] >= results[2]["score"]

    @pytest.mark.asyncio
    async def test_retrieve_similar_empty_results(self, rag_system):
        """Test retrieval with no matching results"""
        rag_system.qdrant.search.return_value = []

        results = await rag_system.retrieve_similar_dialogue(query="Test", k=5)

        assert results == []

    @pytest.mark.asyncio
    async def test_retrieve_similar_error_handling(self, rag_system):
        """Test error handling during retrieval"""
        rag_system.qdrant.search.side_effect = Exception("Search failed")

        results = await rag_system.retrieve_similar_dialogue(query="Test", k=5)

        assert results == []

    @pytest.mark.asyncio
    async def test_retrieve_respects_k_limit(self, rag_system):
        """Test that k limit is passed correctly"""
        rag_system.qdrant.search.return_value = []

        await rag_system.retrieve_similar_dialogue(query="Test", k=10)

        call_args = rag_system.qdrant.search.call_args
        assert call_args.kwargs["limit"] == 10


@pytest.mark.unit
class TestGetCharacterStatistics:
    """Test character statistics retrieval"""

    @pytest.mark.asyncio
    async def test_get_statistics_success(self, rag_system):
        """Test successful statistics retrieval"""
        # Mock collection info
        mock_collection_info = MagicMock()
        mock_collection_info.points_count = 100
        mock_collection_info.config.params.vectors.size = 384
        mock_collection_info.status = "green"
        rag_system.qdrant.get_collection.return_value = mock_collection_info

        # Mock scroll results
        mock_points = [
            MagicMock(payload={"chunk_type": "dialogue", "word_count": 10}),
            MagicMock(payload={"chunk_type": "dialogue", "word_count": 15}),
            MagicMock(payload={"chunk_type": "action", "word_count": 8}),
        ]
        rag_system.qdrant.scroll.return_value = (mock_points, None)

        stats = await rag_system.get_character_statistics()

        assert stats["character_id"] == rag_system.character_id
        assert stats["character_name"] == rag_system.character_name
        assert stats["total_chunks"] == 100
        assert stats["vector_size"] == 384
        assert stats["type_distribution"]["dialogue"] == 2
        assert stats["type_distribution"]["action"] == 1
        assert stats["total_words"] == 33
        assert stats["status"] == "green"

    @pytest.mark.asyncio
    async def test_get_statistics_empty_collection(self, rag_system):
        """Test statistics for empty collection"""
        mock_collection_info = MagicMock()
        mock_collection_info.points_count = 0
        mock_collection_info.config.params.vectors.size = 384
        mock_collection_info.status = "green"
        rag_system.qdrant.get_collection.return_value = mock_collection_info

        rag_system.qdrant.scroll.return_value = ([], None)

        stats = await rag_system.get_character_statistics()

        assert stats["total_chunks"] == 0
        assert stats["type_distribution"] == {}
        assert stats["total_words"] == 0

    @pytest.mark.asyncio
    async def test_get_statistics_error_handling(self, rag_system):
        """Test error handling in statistics retrieval"""
        rag_system.qdrant.get_collection.side_effect = Exception("Collection not found")

        stats = await rag_system.get_character_statistics()

        assert stats["character_id"] == rag_system.character_id
        assert stats["total_chunks"] == 0
        assert "error" in stats


@pytest.mark.unit
class TestDeleteCollection:
    """Test collection deletion"""

    @pytest.mark.asyncio
    async def test_delete_collection_success(self, rag_system):
        """Test successful collection deletion"""
        rag_system.qdrant.delete_collection.return_value = True

        result = await rag_system.delete_collection()

        assert result is True
        rag_system.qdrant.delete_collection.assert_called_once_with(
            collection_name=rag_system.collection_name
        )

    @pytest.mark.asyncio
    async def test_delete_collection_error(self, rag_system):
        """Test collection deletion error handling"""
        rag_system.qdrant.delete_collection.side_effect = Exception("Delete failed")

        result = await rag_system.delete_collection()

        assert result is False


@pytest.mark.unit
class TestCheckCollectionExists:
    """Test collection existence checking"""

    @pytest.mark.asyncio
    async def test_check_collection_exists_true(self, rag_system):
        """Test when collection exists"""
        mock_collection = MagicMock()
        mock_collection.name = rag_system.collection_name
        mock_collections = MagicMock()
        mock_collections.collections = [mock_collection]
        rag_system.qdrant.get_collections.return_value = mock_collections

        result = await rag_system.check_collection_exists()

        assert result is True

    @pytest.mark.asyncio
    async def test_check_collection_exists_false(self, rag_system):
        """Test when collection does not exist"""
        mock_collection = MagicMock()
        mock_collection.name = "other_collection"
        mock_collections = MagicMock()
        mock_collections.collections = [mock_collection]
        rag_system.qdrant.get_collections.return_value = mock_collections

        result = await rag_system.check_collection_exists()

        assert result is False

    @pytest.mark.asyncio
    async def test_check_collection_exists_empty(self, rag_system):
        """Test when no collections exist"""
        mock_collections = MagicMock()
        mock_collections.collections = []
        rag_system.qdrant.get_collections.return_value = mock_collections

        result = await rag_system.check_collection_exists()

        assert result is False

    @pytest.mark.asyncio
    async def test_check_collection_exists_error(self, rag_system):
        """Test error handling in existence check"""
        rag_system.qdrant.get_collections.side_effect = Exception("Connection error")

        result = await rag_system.check_collection_exists()

        assert result is False


@pytest.mark.unit
class TestEdgeCases:
    """Test edge cases and boundary conditions"""

    @pytest.mark.asyncio
    async def test_special_characters_in_character_id(self):
        """Test handling of special characters in character ID"""
        with patch("rag_system.AsyncQdrantClient"), patch(
            "rag_system.SentenceTransformer"
        ) as mock_st:
            mock_model = MagicMock()
            mock_model.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value = mock_model

            from rag_system import CharacterRAG

            rag = CharacterRAG(
                character_id="char-with-many-dashes-123",
                character_name="Test",
                qdrant_url="http://localhost:6333",
            )

            # All dashes should be replaced with underscores
            assert "-" not in rag.collection_name
            assert rag.collection_name == "character_char_with_many_dashes_123"

    @pytest.mark.asyncio
    async def test_unicode_in_content(self, rag_system):
        """Test handling of unicode content in chunks"""
        mock_collections = MagicMock()
        mock_collection = MagicMock()
        mock_collection.name = rag_system.collection_name
        mock_collections.collections = [mock_collection]
        rag_system.qdrant.get_collections.return_value = mock_collections
        rag_system.qdrant.upsert.return_value = True

        chunks = [
            {
                "text": "Unicode content: 你好世界 émoji 🎭",
                "chunk_type": "dialogue",
                "source_location": "p1",
            }
        ]

        result = await rag_system.index_character_content(chunks)

        assert result == 1

    @pytest.mark.asyncio
    async def test_very_long_text(self, rag_system):
        """Test handling of very long text content"""
        mock_collections = MagicMock()
        mock_collection = MagicMock()
        mock_collection.name = rag_system.collection_name
        mock_collections.collections = [mock_collection]
        rag_system.qdrant.get_collections.return_value = mock_collections
        rag_system.qdrant.upsert.return_value = True

        long_text = "Word " * 10000  # Very long text
        chunks = [
            {
                "text": long_text,
                "chunk_type": "description",
                "source_location": "p1",
            }
        ]

        result = await rag_system.index_character_content(chunks)

        assert result == 1

    @pytest.mark.asyncio
    async def test_empty_query(self, rag_system):
        """Test retrieval with empty query"""
        rag_system.qdrant.search.return_value = []

        await rag_system.retrieve_similar_dialogue(query="", k=5)

        # Should still attempt search
        rag_system.qdrant.search.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_optional_chunk_fields(self, rag_system):
        """Test handling of chunks with missing optional fields"""
        mock_collections = MagicMock()
        mock_collection = MagicMock()
        mock_collection.name = rag_system.collection_name
        mock_collections.collections = [mock_collection]
        rag_system.qdrant.get_collections.return_value = mock_collections
        rag_system.qdrant.upsert.return_value = True

        # Chunk with minimal required fields
        chunks = [
            {
                "text": "Minimal chunk",
            }
        ]

        result = await rag_system.index_character_content(chunks)

        assert result == 1
        # Verify defaults are used
        call_args = rag_system.qdrant.upsert.call_args
        payload = call_args.kwargs["points"][0].payload
        assert payload["chunk_type"] == "unknown"
        assert payload["source_location"] == ""


@pytest.mark.integration
class TestCharacterRAGIntegration:
    """Integration tests requiring actual Qdrant (mocked)"""

    @pytest.mark.asyncio
    async def test_full_indexing_and_retrieval_cycle(self, rag_system):
        """Test complete cycle: create -> index -> retrieve"""
        # Setup mocks for full cycle
        mock_collections = MagicMock()
        mock_collections.collections = []
        rag_system.qdrant.get_collections.return_value = mock_collections
        rag_system.qdrant.create_collection.return_value = True
        rag_system.qdrant.upsert.return_value = True

        # Create collection
        created = await rag_system.create_collection()
        assert created is True

        # Index content
        chunks = [
            {"text": "Hello there!", "chunk_type": "dialogue", "source_location": "p1"},
            {
                "text": "She walked away.",
                "chunk_type": "action",
                "source_location": "p2",
            },
        ]
        indexed = await rag_system.index_character_content(chunks)
        assert indexed == 2

        # Setup search mock
        mock_hit = MagicMock()
        mock_hit.payload = {
            "text": "Hello there!",
            "chunk_type": "dialogue",
            "source_location": "p1",
            "word_count": 2,
        }
        mock_hit.score = 0.95
        rag_system.qdrant.search.return_value = [mock_hit]

        # Retrieve similar
        results = await rag_system.retrieve_similar_dialogue(
            query="Greetings",
            k=5,
            chunk_type="dialogue",
        )
        assert len(results) == 1
        assert results[0]["text"] == "Hello there!"

    @pytest.mark.asyncio
    async def test_multiple_character_collections(self):
        """Test managing multiple character collections"""
        with patch("rag_system.AsyncQdrantClient") as mock_qdrant, patch(
            "rag_system.SentenceTransformer"
        ) as mock_st:
            mock_model = MagicMock()
            mock_model.get_sentence_embedding_dimension.return_value = 384
            mock_st.return_value = mock_model

            mock_client = AsyncMock()
            mock_qdrant.return_value = mock_client

            from rag_system import CharacterRAG

            # Create two character RAGs
            rag1 = CharacterRAG(
                character_id="char-001",
                character_name="Alice",
                qdrant_url="http://localhost:6333",
            )
            rag2 = CharacterRAG(
                character_id="char-002",
                character_name="Bob",
                qdrant_url="http://localhost:6333",
            )

            # They should have different collection names
            assert rag1.collection_name != rag2.collection_name
            assert "char_001" in rag1.collection_name
            assert "char_002" in rag2.collection_name
