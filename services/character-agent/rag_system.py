"""Character-specific RAG system using Qdrant"""

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    CollectionStatus
)
from sentence_transformers import SentenceTransformer
from typing import List, Dict, Optional
import uuid
from datetime import datetime
import asyncio


class CharacterRAG:
    """RAG system for individual character voice consistency"""

    def __init__(
        self,
        character_id: str,
        character_name: str,
        qdrant_url: str,
        embedding_model_name: str = "all-MiniLM-L6-v2"
    ):
        """
        Initialize Character RAG system

        Args:
            character_id: Unique character identifier
            character_name: Character name
            qdrant_url: Qdrant server URL
            embedding_model_name: Sentence transformer model name
        """
        self.character_id = character_id
        self.character_name = character_name
        self.collection_name = f"character_{character_id.replace('-', '_')}"

        self.qdrant = AsyncQdrantClient(url=qdrant_url)
        self.embedding_model = SentenceTransformer(embedding_model_name)

        # Model dimension (all-MiniLM-L6-v2 = 384)
        self.vector_size = self.embedding_model.get_sentence_embedding_dimension()

    async def create_collection(self) -> bool:
        """
        Create Qdrant collection for this character

        Returns:
            True if created successfully
        """
        try:
            # Check if collection already exists
            collections = await self.qdrant.get_collections()
            existing_names = [c.name for c in collections.collections]

            if self.collection_name in existing_names:
                print(f"Collection {self.collection_name} already exists")
                return True

            # Create new collection
            await self.qdrant.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=self.vector_size,
                    distance=Distance.COSINE
                )
            )

            print(f"Created collection: {self.collection_name}")
            return True

        except Exception as e:
            print(f"Error creating collection: {e}")
            return False

    async def index_character_content(
        self,
        chunks: List[Dict[str, str]],
        batch_size: int = 100
    ) -> int:
        """
        Index character content chunks into vector database

        Args:
            chunks: List of dicts with 'text', 'chunk_type', 'source_location'
            batch_size: Number of chunks to upload per batch

        Returns:
            Number of chunks indexed
        """
        if not chunks:
            return 0

        try:
            # Ensure collection exists
            await self.create_collection()

            # Process in batches
            total_indexed = 0

            for i in range(0, len(chunks), batch_size):
                batch = chunks[i:i + batch_size]
                points = []

                for chunk in batch:
                    # Generate embedding
                    embedding = self.embedding_model.encode(chunk['text'])

                    # Create point with metadata
                    point = PointStruct(
                        id=str(uuid.uuid4()),
                        vector=embedding.tolist(),
                        payload={
                            'character_id': self.character_id,
                            'character_name': self.character_name,
                            'chunk_type': chunk.get('chunk_type', 'unknown'),
                            'text': chunk['text'],
                            'source_location': chunk.get('source_location', ''),
                            'word_count': len(chunk['text'].split()),
                            'timestamp': datetime.utcnow().timestamp()
                        }
                    )
                    points.append(point)

                # Batch upload
                await self.qdrant.upsert(
                    collection_name=self.collection_name,
                    points=points
                )

                total_indexed += len(points)
                print(f"Indexed {total_indexed}/{len(chunks)} chunks for {self.character_name}")

            return total_indexed

        except Exception as e:
            print(f"Error indexing content: {e}")
            return 0

    async def retrieve_similar_dialogue(
        self,
        query: str,
        k: int = 5,
        chunk_type: Optional[str] = None,
        score_threshold: float = 0.0
    ) -> List[Dict]:
        """
        Retrieve similar past content for voice consistency

        Args:
            query: Query text to find similar content
            k: Number of results to return
            chunk_type: Filter by chunk type (dialogue, action, etc.)
            score_threshold: Minimum similarity score

        Returns:
            List of similar chunks with scores
        """
        try:
            # Generate query embedding
            query_vector = self.embedding_model.encode(query).tolist()

            # Build filter if chunk_type specified
            query_filter = None
            if chunk_type:
                query_filter = Filter(
                    must=[
                        FieldCondition(
                            key="chunk_type",
                            match=MatchValue(value=chunk_type)
                        )
                    ]
                )

            # Search
            results = await self.qdrant.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                limit=k,
                query_filter=query_filter,
                score_threshold=score_threshold
            )

            # Format results
            similar_chunks = [
                {
                    'text': hit.payload['text'],
                    'score': hit.score,
                    'chunk_type': hit.payload.get('chunk_type', 'unknown'),
                    'source': hit.payload.get('source_location', ''),
                    'word_count': hit.payload.get('word_count', 0)
                }
                for hit in results
            ]

            return similar_chunks

        except Exception as e:
            print(f"Error retrieving similar content: {e}")
            return []

    async def get_character_statistics(self) -> Dict:
        """
        Get statistics about character's indexed content

        Returns:
            Dictionary with collection statistics
        """
        try:
            collection_info = await self.qdrant.get_collection(
                collection_name=self.collection_name
            )

            # Get sample of payloads to calculate type distribution
            scroll_result = await self.qdrant.scroll(
                collection_name=self.collection_name,
                limit=1000,
                with_payload=True,
                with_vectors=False
            )

            points = scroll_result[0]

            # Count by type
            type_counts = {}
            total_words = 0

            for point in points:
                chunk_type = point.payload.get('chunk_type', 'unknown')
                type_counts[chunk_type] = type_counts.get(chunk_type, 0) + 1
                total_words += point.payload.get('word_count', 0)

            return {
                'character_id': self.character_id,
                'character_name': self.character_name,
                'collection_name': self.collection_name,
                'total_chunks': collection_info.points_count,
                'vector_size': collection_info.config.params.vectors.size,
                'type_distribution': type_counts,
                'total_words': total_words,
                'status': collection_info.status
            }

        except Exception as e:
            print(f"Error getting statistics: {e}")
            return {
                'character_id': self.character_id,
                'character_name': self.character_name,
                'collection_name': self.collection_name,
                'total_chunks': 0,
                'error': str(e)
            }

    async def delete_collection(self) -> bool:
        """
        Delete this character's collection

        Returns:
            True if deleted successfully
        """
        try:
            await self.qdrant.delete_collection(
                collection_name=self.collection_name
            )
            print(f"Deleted collection: {self.collection_name}")
            return True
        except Exception as e:
            print(f"Error deleting collection: {e}")
            return False

    async def check_collection_exists(self) -> bool:
        """Check if collection exists"""
        try:
            collections = await self.qdrant.get_collections()
            existing_names = [c.name for c in collections.collections]
            return self.collection_name in existing_names
        except Exception as e:
            print(f"Error checking collection: {e}")
            return False
