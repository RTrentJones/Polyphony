"""
Input sanitization utilities

Prevents prompt injection, XSS, and other injection attacks
by sanitizing user input before use in LLMs, databases, and APIs.
"""

import re
import html
from typing import Optional


def sanitize_for_llm(text: str, max_length: int = 2000) -> str:
    """
    Sanitize user input for use in LLM prompts (P2-7 fix)

    Prevents prompt injection attacks by:
    - Removing control characters
    - Limiting length
    - Escaping special characters
    - Removing potentially malicious patterns

    Args:
        text: User input to sanitize
        max_length: Maximum allowed length

    Returns:
        Sanitized text safe for LLM prompts
    """
    if not text:
        return ""

    # Remove null bytes and control characters (except newline, tab, carriage return)
    text = ''.join(char for char in text if char.isprintable() or char in '\n\r\t')

    # Remove potentially malicious patterns
    # Remove things that look like they're trying to break out of prompts
    dangerous_patterns = [
        r'```\s*\n\s*ignore\s+previous',
        r'system:\s*',
        r'<\|.*?\|>',  # Special tokens
        r'\[INST\]|\[\/INST\]',  # Instruction tokens
        r'<s>|</s>',  # Special tokens
    ]

    for pattern in dangerous_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)

    # Limit consecutive newlines
    text = re.sub(r'\n{4,}', '\n\n\n', text)

    # Truncate to max length
    if len(text) > max_length:
        text = text[:max_length]

    # Escape HTML to prevent XSS if output is displayed
    text = html.escape(text)

    # Unescape common punctuation for readability
    text = html.unescape(text)

    return text.strip()


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent directory traversal attacks

    Args:
        filename: Original filename

    Returns:
        Safe filename
    """
    if not filename:
        return "unnamed_file"

    # Remove path separators
    filename = filename.replace('/', '_').replace('\\', '_')

    # Remove dots at start (hidden files)
    filename = filename.lstrip('.')

    # Keep only safe characters
    filename = re.sub(r'[^\w\s\-\.]', '_', filename)

    # Limit length
    if len(filename) > 255:
        name, ext = filename.rsplit('.', 1) if '.' in filename else (filename, '')
        filename = name[:250] + ('.' + ext if ext else '')

    return filename or "unnamed_file"


def sanitize_html(text: str) -> str:
    """
    Sanitize HTML to prevent XSS attacks

    Args:
        text: Text that may contain HTML

    Returns:
        Text with HTML entities escaped
    """
    if not text:
        return ""

    # Escape HTML entities
    text = html.escape(text)

    # Remove any remaining script tags (defense in depth)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.IGNORECASE | re.DOTALL)

    return text


def validate_uuid(uuid_str: str) -> bool:
    """
    Validate that string is a valid UUID

    Args:
        uuid_str: String to validate

    Returns:
        True if valid UUID, False otherwise
    """
    uuid_pattern = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE
    )
    return bool(uuid_pattern.match(uuid_str))


def sanitize_email(email: str) -> Optional[str]:
    """
    Validate and sanitize email address

    Args:
        email: Email to validate

    Returns:
        Sanitized email or None if invalid
    """
    if not email:
        return None

    # Basic email validation
    email = email.strip().lower()
    email_pattern = re.compile(r'^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$')

    if email_pattern.match(email):
        return email

    return None


def sanitize_sql_like(pattern: str) -> str:
    """
    Escape special characters in SQL LIKE patterns

    Args:
        pattern: LIKE pattern string

    Returns:
        Escaped pattern safe for SQL LIKE
    """
    if not pattern:
        return ""

    # Escape SQL LIKE special characters
    pattern = pattern.replace('\\', '\\\\')
    pattern = pattern.replace('%', '\\%')
    pattern = pattern.replace('_', '\\_')

    return pattern


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncate text to maximum length with suffix

    Args:
        text: Text to truncate
        max_length: Maximum length including suffix
        suffix: Suffix to append if truncated

    Returns:
        Truncated text
    """
    if not text or len(text) <= max_length:
        return text

    return text[:max_length - len(suffix)] + suffix


def remove_extra_whitespace(text: str) -> str:
    """
    Remove extra whitespace while preserving paragraph breaks

    Args:
        text: Text with potential extra whitespace

    Returns:
        Cleaned text
    """
    if not text:
        return ""

    # Replace multiple spaces with single space
    text = re.sub(r' +', ' ', text)

    # Replace multiple tabs with single tab
    text = re.sub(r'\t+', '\t', text)

    # Limit to max 2 consecutive newlines
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()
