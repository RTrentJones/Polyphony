"""Source ingestion pipeline.

Was the document-parser service + the TODO stub in the gateway's background
task. Now one in-process pipeline: validate/save the upload, parse it, extract
characters via the LLM, persist Character + CharacterChunk rows, and index the
chunks into the pgvector store (same database).

A Source is book-rooted (docs/ADR-002-book-as-root.md §2), so every character
and voice chunk it produces inherits the Source's `book_id`.
"""

import hashlib
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import aiofiles
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_async_session
from app.core.logging_config import log_business_event, log_error, setup_logging
from app.characters.serialize import character_snapshot
from app.core.orm_models import Character, CharacterChunk, Source
from app.llm.errors import QuotaExhaustedError
from app.rag.store import get_chunk_store
from app.versioning import repository as versions_repo

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


async def process_source(
    source_id: UUID, user_id: UUID, text: str | None = None
) -> None:
    """Background pipeline: extract characters, persist them, index their voices.

    Text is read from the source row (durable) — restart-safe, unlike a
    container-local file. Characters + chunks are COMMITTED before any pgvector
    indexing, so the voice_chunks FK to characters is satisfied on Postgres (the
    vector store commits on its own connection and can't see an uncommitted
    parent).

    Every character and voice chunk inherits the Source's `book_id`: the book is
    the root (docs/ADR-002-book-as-root.md §1).

    NON-DESTRUCTIVE (PR review #1): this NEVER deletes existing canon. Only names
    not already in the book are created — an author's manually enriched character
    (goals/relationships/voice) is preserved on reprocess, and every auto-created
    character is versioned so nothing here is an unrecoverable write. Authors who
    want a full review-then-commit flow use POST /books/{id}/sources/{sid}/extract.
    """
    try:
        # Load the parsed text + the book's existing cast (by name) in one txn.
        async with get_async_session() as session:
            source = await session.get(Source, source_id)
            if source is None:
                return
            book_id = source.book_id
            source_text = text if text is not None else (source.content_text or "")
            if not source_text:
                raise ValueError("source has no stored content to process")
            existing_names = set(
                (
                    await session.execute(
                        select(Character.name).where(Character.book_id == book_id)
                    )
                )
                .scalars()
                .all()
            )
            source.status = "processing"

        character_names = await char_extractor.extract_characters(
            source_text, user_id=user_id
        )
        # Only genuinely new names — never overwrite or delete an existing one.
        new_names = list(dict.fromkeys(character_names))
        new_names = [n for n in new_names if n not in existing_names]

        store = get_chunk_store()
        indexed_total = 0

        for name in new_names:
            chunks = char_extractor.extract_character_content(source_text, name)
            stats = char_extractor.get_character_statistics(chunks)

            # 1) Persist + COMMIT the character (and its chunk rows) first.
            async with get_async_session() as session:
                character = Character(
                    user_id=user_id,
                    book_id=book_id,
                    source_id=source_id,
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
                # Version the imported character (full snapshot) so an auto-created
                # entity is restorable like any other (PR review #1).
                await versions_repo.snapshot(
                    session,
                    book_id=book_id,
                    entity_type="character",
                    entity_id=character_id,
                    content=character_snapshot(character),
                    reason="imported",
                    created_by=user_id,
                )
            # 2) Index into pgvector now that the parent row is committed.
            indexed = await store.index_chunks(
                character_id=str(character_id),
                character_name=name,
                user_id=str(user_id),
                book_id=str(book_id),
                chunks=chunks,
            )
            if indexed:
                async with get_async_session() as session:
                    c = await session.get(Character, character_id)
                    if c is not None:
                        c.indexed_at = datetime.now(timezone.utc)
            indexed_total += indexed

        async with get_async_session() as session:
            source = await session.get(Source, source_id)
            if source is not None:
                source.status = "completed"
                source.processed_at = datetime.now(timezone.utc)

        log_business_event(
            logger,
            "source_processed",
            source_id=str(source_id),
            characters=len(character_names),
            chunks_indexed=indexed_total,
        )

    except QuotaExhaustedError:
        # Pause, don't fail — leave the source 'processing' for resume.
        raise
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "source_id": str(source_id),
                "event": "source_processing_failed",
            },
        )
        try:
            async with get_async_session() as session:
                source = await session.get(Source, source_id)
                if source is not None:
                    source.status = "failed"
        except Exception:
            pass
