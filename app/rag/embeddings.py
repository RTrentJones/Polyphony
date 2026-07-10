"""Text embeddings via fastembed (ONNX Runtime).

Replaces sentence-transformers/torch: same all-MiniLM-L6-v2 model and 384-dim
geometry at a fraction of the image size and RSS (see docs/ADR-001). The model
is baked into the container image at build time (Dockerfile) so restarts don't
re-download.
"""

import asyncio
from typing import Iterable, Optional

from app.core.config import settings


class Embedder:
    """Lazy fastembed wrapper; encode work runs in a thread executor."""

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or settings.EMBEDDING_MODEL
        self.dimension = settings.EMBEDDING_DIMENSION
        self._model = None

    def _get_model(self):
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def encode(self, texts: Iterable[str]) -> list[list[float]]:
        """Embed texts synchronously (fastembed batches internally)."""
        model = self._get_model()
        return [vector.tolist() for vector in model.embed(list(texts))]

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]

    async def aencode(self, texts: list[str]) -> list[list[float]]:
        """Embed without blocking the event loop."""
        return await asyncio.to_thread(self.encode, texts)

    async def aencode_one(self, text: str) -> list[float]:
        return (await self.aencode([text]))[0]


_embedder: Optional[Embedder] = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity without numpy/torch."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)
