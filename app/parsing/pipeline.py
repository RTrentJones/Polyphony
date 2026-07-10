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

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    file_id = str(uuid4())
    file_path = os.path.join(settings.UPLOAD_DIR, f"{file_id}{file_ext}")
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    try:
        text = doc_parser.parse_document(file_path)
    except Exception:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise

    return {
        "file_id": file_id,
        "file_path": file_path,
        "content_hash": hashlib.sha256(content).hexdigest(),
        "text": text,
        "word_count": doc_parser.get_word_count(text),
    }


async def process_manuscript(
    manuscript_id: UUID, file_path: str, user_id: UUID
) -> None:
    """Background pipeline: extract characters, persist them, index their voices."""
    try:
        text = doc_parser.parse_document(file_path)
        character_names = await char_extractor.extract_characters(text, user_id=user_id)

        store = get_chunk_store()
        indexed_total = 0

        async with get_async_session() as session:
            manuscript = await session.get(Manuscript, manuscript_id)
            if manuscript is None:
                return

            for name in character_names:
                chunks = char_extractor.extract_character_content(text, name)
                stats = char_extractor.get_character_statistics(chunks)

                character = Character(
                    manuscript_id=manuscript_id,
                    name=name,
                    dialogue_count=stats["dialogue_count"],
                )
                session.add(character)
                await session.flush()  # assign character.id

                for chunk in chunks:
                    session.add(
                        CharacterChunk(
                            character_id=character.id,
                            chunk_type=chunk["chunk_type"],
                            content=chunk["text"],
                            source_location=chunk.get("source_location"),
                        )
                    )

                indexed = await store.index_chunks(
                    character_id=str(character.id),
                    character_name=name,
                    user_id=str(user_id),
                    chunks=chunks,
                )
                if indexed:
                    character.indexed_at = datetime.now(timezone.utc)
                indexed_total += indexed

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
