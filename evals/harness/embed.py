"""Embedding helper for the attribution grader — reuses the app's own embedder.

Uses the SAME fastembed model the pipeline indexes with, so attribution measures
the geometry the store actually sees. Runs in-process (local, free, no LLM).
"""

from __future__ import annotations


async def embed_many(texts: list[str]) -> list[list[float]]:
    from app.rag.embeddings import get_embedder

    return await get_embedder().aencode(list(texts))


async def embed_one(text: str) -> list[float]:
    from app.rag.embeddings import get_embedder

    return await get_embedder().aencode_one(text)
