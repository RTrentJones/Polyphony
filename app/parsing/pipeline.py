"""Source ingestion: upload validation/parsing + voice indexing.

Two responsibilities, deliberately split (docs/BRD.md R4.4, PR review #1):
- `save_upload` validates and parses an uploaded file into durable text.
- `index_source_voices` indexes voice chunks for a source's ALREADY-COMMITTED
  characters. It does NOT write canon — canon is created only by the reviewed
  extraction commit (app/api/extraction.py). Voice indexing is a separate,
  retryable job.
"""

import hashlib
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import aiofiles
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_async_session
from app.core.logging_config import log_business_event, setup_logging
from app.core.orm_models import Character, Source
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


def _sniff_mime(content: bytes) -> str:
    """MIME-sniff upload content via libmagic.

    Imported lazily: the native libmagic library is only required when an
    upload is actually validated, so importing the app (and collecting tests)
    works on machines without it.
    """
    import magic

    return magic.from_buffer(content, mime=True)


async def save_upload(filename: str, content: bytes) -> dict:
    """Validate and persist an uploaded source file.

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

    mime_type = _sniff_mime(content)
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


async def index_source_voices(source_id: UUID, book_id: UUID, user_id: UUID) -> None:
    """Index voice chunks for a source's committed characters — RETRYABLE.

    Canon is written only by the reviewed extraction commit (docs/BRD.md R4.4);
    voice indexing is a separate job over the source-linked characters that are
    not yet indexed (`indexed_at IS NULL`). That predicate makes it idempotent
    and retryable: if a transient vector failure leaves a character unindexed, the
    job re-queues and re-processes ONLY the incomplete ones — a character can
    never end up permanently voice-blind while its source reports success
    (PR review #3). Each character's vectors are cleared before (re)indexing, so a
    retry after a partial write never duplicates chunks.
    """
    async with get_async_session() as session:
        source = await session.get(Source, source_id)
        source_text = source.content_text if source is not None else ""
        if not source_text:
            return
        targets = [
            (c.id, c.name)
            for c in (
                await session.execute(
                    select(Character).where(
                        Character.book_id == book_id,
                        Character.source_id == source_id,
                        Character.indexed_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        ]

    store = get_chunk_store()
    for character_id, name in targets:
        chunks = char_extractor.extract_character_content(source_text, name)
        stats = char_extractor.get_character_statistics(chunks)
        # Clear any partial vectors from a prior failed attempt (idempotent retry).
        await store.delete_character(str(character_id))
        if chunks:
            # QuotaExhaustedError here propagates -> the worker pauses (free tier).
            await store.index_chunks(
                character_id=str(character_id),
                character_name=name,
                user_id=str(user_id),
                book_id=str(book_id),
                chunks=chunks,
            )
        # Mark indexed LAST — only now is this character excluded from retries.
        async with get_async_session() as session:
            c = await session.get(Character, character_id)
            if c is not None:
                c.indexed_at = datetime.now(timezone.utc)
                if stats.get("dialogue_count"):
                    c.dialogue_count = stats["dialogue_count"]

    log_business_event(
        logger,
        "source_voices_indexed",
        source_id=str(source_id),
        characters=len(targets),
    )
