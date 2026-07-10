"""Manuscript ingestion pipeline.

Was the document-parser service + the TODO stub in the gateway's background
task. Now one in-process pipeline: validate/save the upload, parse it, extract
characters via the LLM, persist Character + CharacterChunk rows, and index the
chunks into the pgvector store (same database).
"""

import hashlib
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import aiofiles
import magic
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_async_session
from app.core.logging_config import log_business_event, log_error, setup_logging
from app.core.orm_models import Character, CharacterChunk, Manuscript
from app.rag.store import get_chunk_store

from .character_extractor import CharacterExtractor
from .parser import DocumentParser

logger = setup_logging("parsing.pipeline")

ALLOWED_MIME_TYPES = {
    "text/plain",
    "text/html",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}

doc_parser = DocumentParser()
char_extractor = CharacterExtractor()


class UploadValidationError(ValueError):
    """Upload failed validation (extension, size, or MIME sniff)."""


async def save_upload(filename: str, content: bytes) -> dict:
    """Validate and persist an uploaded manuscript file.

    Returns dict with file_id, file_path, content_hash, text, word_count.
    """
    file_ext = os.path.splitext(filename)[1].lower()
    if file_ext not in settings.ALLOWED_EXTENSIONS:
        raise UploadValidationError(
            f"Unsupported file format. Allowed: {settings.ALLOWED_EXTENSIONS}"
        )
    if len(content) > settings.MAX_UPLOAD_SIZE:
        raise UploadValidationError(
            f"File too large. Maximum size: {settings.MAX_UPLOAD_SIZE} bytes"
        )

    mime_type = magic.from_buffer(content, mime=True)
    if mime_type not in ALLOWED_MIME_TYPES:
        raise UploadValidationError(
            f"Invalid file content. Detected type: {mime_type}. "
            "File extension may not match content."
        )

    # Parse via a short-lived temp file (the parser reads a path), then delete
    # it — the durable copy is the parsed text, persisted to Postgres by the
    # caller. Nothing survives on local disk past this function.
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    file_id = str(uuid4())
    file_path = os.path.join(settings.UPLOAD_DIR, f"{file_id}{file_ext}")
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)
    try:
        text = doc_parser.parse_document(file_path)
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

    return {
        "file_id": file_id,
        "content_hash": hashlib.sha256(content).hexdigest(),
        "text": text,
        "word_count": doc_parser.get_word_count(text),
    }


async def process_manuscript(
    manuscript_id: UUID, user_id: UUID, text: str | None = None
) -> None:
    """Background pipeline: extract characters, persist them, index their voices.

    Text is read from the manuscript row (durable) — restart-safe, unlike a
    container-local file. Characters + chunks are COMMITTED before any pgvector
    indexing, so the voice_chunks FK to characters is satisfied on Postgres (the
    vector store commits on its own connection and can't see an uncommitted
    parent).
    """
    try:
        # Load the parsed text + reset prior characters (reprocess-safe) in one txn.
        async with get_async_session() as session:
            manuscript = await session.get(Manuscript, manuscript_id)
            if manuscript is None:
                return
            source_text = text if text is not None else (manuscript.content_text or "")
            if not source_text:
                raise ValueError("manuscript has no stored content to process")
            # Reprocess: drop existing characters (vectors cascade) so re-extraction
            # can't collide with the unique (manuscript_id, name) index.
            existing = (
                (
                    await session.execute(
                        select(Character).where(
                            Character.manuscript_id == manuscript_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for c in existing:
                await get_chunk_store().delete_character(str(c.id))
                await session.delete(c)
            manuscript.status = "processing"

        character_names = await char_extractor.extract_characters(
            source_text, user_id=user_id
        )

        store = get_chunk_store()
        indexed_total = 0

        for name in character_names:
            chunks = char_extractor.extract_character_content(source_text, name)
            stats = char_extractor.get_character_statistics(chunks)

            # 1) Persist + COMMIT the character (and its chunk rows) first.
            async with get_async_session() as session:
                character = Character(
                    user_id=user_id,
                    manuscript_id=manuscript_id,
                    name=name,
                    dialogue_count=stats["dialogue_count"],
                )
                session.add(character)
                await session.flush()
                character_id = character.id
                for chunk in chunks:
                    session.add(
                        CharacterChunk(
                            character_id=character_id,
                            chunk_type=chunk["chunk_type"],
                            content=chunk["text"],
                            source_location=chunk.get("source_location"),
                        )
                    )
            # 2) Index into pgvector now that the parent row is committed.
            indexed = await store.index_chunks(
                character_id=str(character_id),
                character_name=name,
                user_id=str(user_id),
                chunks=chunks,
            )
            if indexed:
                async with get_async_session() as session:
                    c = await session.get(Character, character_id)
                    if c is not None:
                        c.indexed_at = datetime.now(timezone.utc)
            indexed_total += indexed

        async with get_async_session() as session:
            manuscript = await session.get(Manuscript, manuscript_id)
            if manuscript is not None:
                manuscript.status = "completed"
                manuscript.processed_at = datetime.now(timezone.utc)

        log_business_event(
            logger,
            "manuscript_processed",
            manuscript_id=str(manuscript_id),
            characters=len(character_names),
            chunks_indexed=indexed_total,
        )

    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "manuscript_id": str(manuscript_id),
                "event": "manuscript_processing_failed",
            },
        )
        try:
            async with get_async_session() as session:
                manuscript = await session.get(Manuscript, manuscript_id)
                if manuscript is not None:
                    manuscript.status = "failed"
        except Exception:
            pass
