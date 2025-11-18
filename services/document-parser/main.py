"""Document Parser Service - Main API"""

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import os
import hashlib
from datetime import datetime
from uuid import UUID, uuid4
import aiofiles

from services.shared.config import settings
from services.shared.models import ManuscriptStatus
from .parser import DocumentParser
from .character_extractor import CharacterExtractor


app = FastAPI(
    title="Polyphony Document Parser",
    version="1.0.0",
    description="Document parsing and character extraction service"
)

# Initialize parsers
doc_parser = DocumentParser()
char_extractor = CharacterExtractor(settings.GROQ_API_KEY)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "document-parser",
        "version": "1.0.0",
        "supported_formats": doc_parser.SUPPORTED_FORMATS
    }


@app.post("/parse")
async def parse_document(
    file: UploadFile = File(...),
    extract_characters: bool = True
):
    """
    Parse uploaded document and optionally extract characters

    Args:
        file: Uploaded document file
        extract_characters: Whether to extract character list

    Returns:
        Parsed text and metadata
    """
    # Validate file extension
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format. Allowed: {settings.ALLOWED_EXTENSIONS}"
        )

    try:
        # Create upload directory if it doesn't exist
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

        # Generate unique filename
        file_id = str(uuid4())
        file_path = os.path.join(settings.UPLOAD_DIR, f"{file_id}{file_ext}")

        # Save file
        async with aiofiles.open(file_path, 'wb') as f:
            content = await file.read()
            await f.write(content)

        # Calculate content hash
        content_hash = hashlib.sha256(content).hexdigest()

        # Parse document
        text = doc_parser.parse_document(file_path)

        # Get word count
        word_count = doc_parser.get_word_count(text)

        # Extract characters if requested
        characters = []
        if extract_characters and text:
            characters = await char_extractor.extract_characters(text)

        result = {
            "file_id": file_id,
            "filename": file.filename,
            "file_path": file_path,
            "content_hash": content_hash,
            "word_count": word_count,
            "paragraph_count": doc_parser.get_paragraph_count(text),
            "text_preview": text[:500] if text else "",
            "full_text_length": len(text),
            "characters": characters,
            "status": "success"
        }

        return JSONResponse(content=result)

    except Exception as e:
        # Clean up file if parsing failed
        if os.path.exists(file_path):
            os.remove(file_path)

        raise HTTPException(
            status_code=500,
            detail=f"Error parsing document: {str(e)}"
        )


@app.post("/extract-character-content")
async def extract_character_content(
    file_id: str,
    character_name: str,
    dialogue_only: bool = False
):
    """
    Extract content for specific character from parsed document

    Args:
        file_id: File ID from parse endpoint
        character_name: Name of character to extract
        dialogue_only: If True, only extract dialogue

    Returns:
        Character-specific content chunks
    """
    # Find file
    file_path = None
    for ext in settings.ALLOWED_EXTENSIONS:
        potential_path = os.path.join(settings.UPLOAD_DIR, f"{file_id}{ext}")
        if os.path.exists(potential_path):
            file_path = potential_path
            break

    if not file_path:
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {file_id}"
        )

    try:
        # Parse document
        text = doc_parser.parse_document(file_path)

        if dialogue_only:
            # Extract only dialogue
            dialogues = char_extractor.extract_dialogue_only(text, character_name)
            return {
                "character_name": character_name,
                "dialogue_count": len(dialogues),
                "dialogues": dialogues
            }
        else:
            # Extract all content
            chunks = char_extractor.extract_character_content(text, character_name)
            stats = char_extractor.get_character_statistics(chunks)

            return {
                "character_name": character_name,
                "chunks": chunks,
                "statistics": stats
            }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error extracting character content: {str(e)}"
        )


@app.delete("/delete/{file_id}")
async def delete_file(file_id: str):
    """
    Delete uploaded file

    Args:
        file_id: File ID to delete
    """
    deleted = False

    # Try all possible extensions
    for ext in settings.ALLOWED_EXTENSIONS:
        file_path = os.path.join(settings.UPLOAD_DIR, f"{file_id}{ext}")
        if os.path.exists(file_path):
            os.remove(file_path)
            deleted = True

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {file_id}"
        )

    return {"status": "deleted", "file_id": file_id}


@app.get("/stats")
async def get_stats():
    """Get service statistics"""
    upload_dir = settings.UPLOAD_DIR

    if not os.path.exists(upload_dir):
        return {
            "total_files": 0,
            "total_size_mb": 0
        }

    files = os.listdir(upload_dir)
    total_size = sum(
        os.path.getsize(os.path.join(upload_dir, f))
        for f in files
        if os.path.isfile(os.path.join(upload_dir, f))
    )

    return {
        "total_files": len(files),
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "upload_dir": upload_dir
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("SERVICE_PORT", "8005"))
    uvicorn.run(app, host="0.0.0.0", port=port)
