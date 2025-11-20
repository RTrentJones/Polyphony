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
        r'ignore\s+previous\s+(instructions?|commands?)',  # Prompt injection attempts
        r'disregard\s+(previous|above)',  # Alternative injection phrases
        r'system:\s*',  # System role injection
        r'<\|.*?\|>',  # Special tokens
        r'\[INST\]|\[\/INST\]',  # Instruction tokens
        r'<s>|</s>',  # Special tokens
        r'```[^`]*ignore[^`]*```',  # Code block injection
        r'--',  # SQL comment markers (defense in depth)
        r'/\*.*?\*/',  # C-style comments
    ]

    for pattern in dangerous_patterns:
        text = re.sub(pattern, '[FILTERED]', text, flags=re.IGNORECASE)

    # Limit consecutive newlines
    text = re.sub(r'\n{4,}', '\n\n\n', text)

    # Truncate to max length
    if len(text) > max_length:
        text = text[:max_length]

    # Escape HTML to prevent XSS if output is displayed
    # This converts < > & " ' to HTML entities
    text = html.escape(text)

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


def sanitize_html(text: str, allowed_tags: list = None) -> str:
    """
    Sanitize HTML to prevent XSS attacks

    Args:
        text: Text that may contain HTML
        allowed_tags: List of allowed HTML tags (e.g., ['p', 'strong'])

    Returns:
        Text with HTML entities escaped or with only allowed tags
    """
    if not text:
        return ""

    # Remove script tags FIRST (before parsing) - defense in depth
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.IGNORECASE | re.DOTALL)

    # Remove javascript: and data: URLs
    text = re.sub(r'javascript:', '', text, flags=re.IGNORECASE)
    text = re.sub(r'data:', '', text, flags=re.IGNORECASE)

    # Remove event handlers
    text = re.sub(r'on\w+\s*=', '', text, flags=re.IGNORECASE)

    if allowed_tags is None:
        # No tags allowed - escape everything
        text = html.escape(text)
    else:
        # Remove all tags except allowed ones
        # For simplicity, we'll escape everything then unescape allowed tags
        # A production implementation should use a library like bleach
        from html.parser import HTMLParser

        class AllowedHTMLParser(HTMLParser):
            def __init__(self, allowed):
                super().__init__()
                self.allowed_tags = set(allowed)
                self.result = []
                self.skip_tag = None

            def handle_starttag(self, tag, attrs):
                # Skip dangerous tags
                if tag.lower() in ('script', 'style', 'iframe', 'object', 'embed'):
                    self.skip_tag = tag.lower()
                    return

                if tag in self.allowed_tags:
                    self.result.append(f'<{tag}>')

            def handle_endtag(self, tag):
                if self.skip_tag == tag.lower():
                    self.skip_tag = None
                    return

                if tag in self.allowed_tags:
                    self.result.append(f'</{tag}>')

            def handle_data(self, data):
                # Skip data inside dangerous tags
                if self.skip_tag:
                    return
                self.result.append(html.escape(data))

        parser = AllowedHTMLParser(allowed_tags)
        try:
            parser.feed(text)
            text = ''.join(parser.result)
        except:
            # If parsing fails, escape everything
            text = html.escape(text)

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


def sanitize_sql_string(text: str) -> str:
    """
    Sanitize string for use in SQL queries to prevent SQL injection

    Note: This is a basic implementation. In production, always use
    parameterized queries instead of string concatenation.

    Args:
        text: String to sanitize

    Returns:
        Sanitized string safe for SQL
    """
    if not text:
        return ""

    # Escape single quotes (most common SQL injection vector)
    text = text.replace("'", "''")

    # Remove SQL comment markers
    text = text.replace('--', '')
    text = text.replace('/*', '')
    text = text.replace('*/', '')

    # Remove semicolons (statement terminators)
    text = text.replace(';', '')

    # Filter dangerous SQL keywords (case-insensitive)
    dangerous_keywords = [
        'DROP TABLE', 'DROP DATABASE', 'DELETE FROM', 'TRUNCATE',
        'ALTER TABLE', 'CREATE TABLE', 'INSERT INTO', 'UPDATE ',
        'EXEC', 'EXECUTE', 'UNION SELECT', 'UNION ALL'
    ]

    text_upper = text.upper()
    for keyword in dangerous_keywords:
        if keyword in text_upper:
            # Replace with filtered marker
            # Find actual position in original text (case-insensitive)
            start = text_upper.find(keyword)
            while start != -1:
                text = text[:start] + '[FILTERED]' + text[start + len(keyword):]
                text_upper = text.upper()
                start = text_upper.find(keyword)

    return text


def sanitize_file_path(path: str) -> str:
    """
    Sanitize file path to prevent directory traversal attacks

    Args:
        path: File path to sanitize

    Returns:
        Safe file path or empty string if invalid
    """
    if not path:
        return ""

    # Remove null bytes
    path = path.replace('\x00', '')

    # Remove or block directory traversal sequences
    if '..' in path:
        return ""  # Reject paths with ..

    # Remove leading slashes (absolute paths)
    path = path.lstrip('/')
    path = path.lstrip('\\')

    # Remove dangerous characters
    dangerous_chars = ['<', '>', ':', '"', '|', '?', '*']
    for char in dangerous_chars:
        path = path.replace(char, '')

    # Normalize path separators
    path = path.replace('\\', '/')

    # Remove multiple consecutive slashes
    path = re.sub(r'/+', '/', path)

    # Limit length
    if len(path) > 255:
        return ""

    return path


def validate_file_upload(filename: str, allowed_types: list) -> bool:
    """
    Validate file upload based on extension

    Args:
        filename: Name of file being uploaded
        allowed_types: List of allowed file extensions (without dot)

    Returns:
        True if file type is allowed, False otherwise
    """
    if not filename or not allowed_types:
        return False

    # Get file extension
    if '.' not in filename:
        return False

    # Check for double extensions (e.g., file.php.jpg)
    parts = filename.split('.')
    if len(parts) > 2:
        return False  # Reject double extensions

    extension = filename.rsplit('.', 1)[-1].lower()

    # Check if extension is in allowed list (case-insensitive)
    allowed_types_lower = [ext.lower() for ext in allowed_types]

    return extension in allowed_types_lower


def is_safe_redirect_url(url: str, allowed_domains: list = None) -> bool:
    """
    Validate that redirect URL is safe to prevent open redirect attacks

    Args:
        url: URL to validate
        allowed_domains: Optional list of allowed domains

    Returns:
        True if URL is safe for redirect, False otherwise
    """
    if not url:
        return False

    # Block URLs with backslashes FIRST (often used in bypass attempts like /\evil.com)
    if '\\' in url:
        return False

    # Block URLs starting with // (protocol-relative)
    if url.startswith('//'):
        return False

    # Block dangerous protocols
    dangerous_protocols = [
        'javascript:',
        'data:',
        'vbscript:',
        'file:',
    ]

    url_lower = url.lower()
    for protocol in dangerous_protocols:
        if url_lower.startswith(protocol):
            return False

    # Allow relative URLs (starting with /) - checked AFTER backslash and // checks
    if url.startswith('/'):
        return True

    # If allowed domains specified, check against them
    if allowed_domains:
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
            domain = parsed.netloc

            # Check if domain is in allowed list
            for allowed in allowed_domains:
                if domain == allowed or domain.endswith('.' + allowed):
                    return True

            return False
        except:
            return False

    # If no allowed domains specified, only allow relative URLs
    # Reject any URL that looks like it's going to an external site
    if '://' in url:
        return False

    return True
