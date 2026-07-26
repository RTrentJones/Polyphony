"""Unit tests for input sanitization and security utilities.

These cover the sinks `sanitization.py` legitimately guards: HTML rendering,
SQL, filesystem paths, uploads, redirects.

There is no LLM-prompt coverage here. `sanitize_for_llm` and its tests were
deleted together: the tests asserted the defect (silent truncation to a
general-purpose default, a bypassable phrase blocklist, HTML-escaping of prompt
text) and so kept it in place. Prompt text is now built by `app.core.llm_text`
and tested in `tests/unit/test_llm_text.py`, which asserts the opposite
properties. See docs/ADR-002-book-as-root.md §4.
"""

import pytest
from app.core.sanitization import (
    sanitize_html,
    sanitize_sql_string,
    sanitize_file_path,
    validate_file_upload,
    is_safe_redirect_url,
)


@pytest.mark.unit
class TestHTMLSanitization:
    """Test HTML/XSS prevention"""

    def test_sanitize_html_basic(self):
        """Test basic HTML sanitization"""
        html = "<p>Safe paragraph</p>"
        result = sanitize_html(html, allowed_tags=["p"])
        assert "<p>" in result
        assert "Safe paragraph" in result

    def test_sanitize_html_removes_script_tags(self):
        """Test removal of script tags"""
        html = "<div>Content<script>alert('xss')</script></div>"
        result = sanitize_html(html, allowed_tags=["div"])
        assert "<script>" not in result
        assert "alert" not in result
        assert "Content" in result

    def test_sanitize_html_removes_event_handlers(self):
        """Test removal of JavaScript event handlers"""
        html = "<div onclick=\"alert('xss')\">Click me</div>"
        result = sanitize_html(html, allowed_tags=["div"])
        assert "onclick" not in result.lower()
        assert "alert" not in result

    def test_sanitize_html_removes_dangerous_protocols(self):
        """Test removal of dangerous URL protocols"""
        html = "<a href=\"javascript:alert('xss')\">Link</a>"
        result = sanitize_html(html, allowed_tags=["a"])
        assert "javascript:" not in result.lower()

    def test_sanitize_html_allows_safe_tags(self):
        """Test that safe tags are preserved"""
        html = "<strong>Bold</strong> and <em>italic</em> text"
        result = sanitize_html(html, allowed_tags=["strong", "em"])
        assert "<strong>" in result
        assert "<em>" in result
        assert "Bold" in result
        assert "italic" in result

    def test_sanitize_html_empty_input(self):
        """Test sanitizing empty HTML"""
        result = sanitize_html("")
        assert result == ""


@pytest.mark.unit
class TestSQLSanitization:
    """Test SQL injection prevention"""

    def test_sanitize_sql_string_basic(self):
        """Test basic SQL string sanitization"""
        unsafe = "'; DROP TABLE users; --"
        result = sanitize_sql_string(unsafe)
        # Should escape single quotes
        assert "\\'" in result or "''" in result or "DROP TABLE" not in result

    def test_sanitize_sql_string_preserves_safe_text(self):
        """Test that safe text is preserved"""
        safe = "John Smith"
        result = sanitize_sql_string(safe)
        assert "John Smith" in result

    def test_sanitize_sql_removes_comments(self):
        """Test removal of SQL comments"""
        unsafe = "test' OR 1=1 --"
        result = sanitize_sql_string(unsafe)
        # Should remove or escape comment markers
        assert "--" not in result or "OR 1=1" not in result


@pytest.mark.unit
class TestFilePathSanitization:
    """Test path traversal prevention"""

    def test_sanitize_safe_filename(self):
        """Test sanitizing safe filename"""
        filename = "document.pdf"
        result = sanitize_file_path(filename)
        assert result == "document.pdf"

    def test_sanitize_path_traversal_attempts(self):
        """Test blocking path traversal"""
        dangerous_paths = [
            "../../../etc/passwd",
            "..\\..\\windows\\system32",
            "folder/../../../secret.txt",
            "./../../important.txt",
        ]

        for dangerous in dangerous_paths:
            result = sanitize_file_path(dangerous)
            # Should not contain .. after sanitization
            assert ".." not in result or result == ""

    def test_sanitize_absolute_paths(self):
        """Test handling of absolute paths"""
        absolute = "/etc/passwd"
        result = sanitize_file_path(absolute)
        # Should remove leading slash or reject
        assert not result.startswith("/")

    def test_sanitize_null_bytes(self):
        """Test removal of null bytes"""
        filename = "file\x00.txt"
        result = sanitize_file_path(filename)
        assert "\x00" not in result

    def test_sanitize_special_characters(self):
        """Test handling of special characters"""
        filename = 'file<>:"|?*.txt'
        result = sanitize_file_path(filename)
        # Should remove or replace dangerous characters
        for char in '<>:"|?*':
            assert char not in result


@pytest.mark.unit
class TestFileUploadValidation:
    """Test file upload validation"""

    def test_validate_safe_file_type(self):
        """Test validating safe file types"""
        allowed_types = ["pdf", "docx", "txt"]

        assert validate_file_upload("document.pdf", allowed_types) is True
        assert validate_file_upload("manuscript.docx", allowed_types) is True
        assert validate_file_upload("notes.txt", allowed_types) is True

    def test_validate_dangerous_file_type(self):
        """Test rejecting dangerous file types"""
        allowed_types = ["pdf", "docx"]

        dangerous_files = [
            "malware.exe",
            "script.sh",
            "payload.php",
            "hack.js",
        ]

        for dangerous in dangerous_files:
            assert validate_file_upload(dangerous, allowed_types) is False

    def test_validate_case_insensitive(self):
        """Test case-insensitive file type checking"""
        allowed_types = ["pdf", "docx"]

        assert validate_file_upload("document.PDF", allowed_types) is True
        assert validate_file_upload("file.DocX", allowed_types) is True

    def test_validate_double_extension(self):
        """Test handling of double extensions"""
        allowed_types = ["pdf"]

        # Should reject even if final extension is safe
        assert validate_file_upload("malware.exe.pdf", allowed_types) is False

    def test_validate_no_extension(self):
        """Test handling files without extension"""
        allowed_types = ["pdf"]

        assert validate_file_upload("filenoext", allowed_types) is False

    def test_validate_empty_filename(self):
        """Test handling empty filename"""
        allowed_types = ["pdf"]

        assert validate_file_upload("", allowed_types) is False


@pytest.mark.unit
class TestRedirectURLValidation:
    """Test safe redirect URL validation"""

    def test_safe_relative_url(self):
        """Test safe relative URLs"""
        safe_urls = [
            "/dashboard",
            "/manuscripts/123",
            "/scenes/generate",
        ]

        for url in safe_urls:
            assert is_safe_redirect_url(url) is True

    def test_safe_same_domain_url(self):
        """Test safe same-domain URLs"""
        # Would need base domain configuration
        # url = "https://polyphony.app/dashboard"
        # Implementation depends on domain checking
        # This is a placeholder for the test structure
        pass

    def test_dangerous_external_redirects(self):
        """Test blocking dangerous external redirects"""
        dangerous_urls = [
            "https://evil.com/phishing",
            "http://malware-site.com",
            "javascript:alert('xss')",
            "data:text/html,<script>alert('xss')</script>",
        ]

        for url in dangerous_urls:
            assert is_safe_redirect_url(url) is False

    def test_open_redirect_prevention(self):
        """Test preventing open redirect vulnerabilities"""
        # URLs that might trick users
        tricky_urls = [
            "//evil.com",
            "https:///evil.com",
            "/\\evil.com",
        ]

        for url in tricky_urls:
            assert is_safe_redirect_url(url) is False


@pytest.mark.unit
class TestSanitizationIntegration:
    """Test combined sanitization scenarios"""

    def test_user_input_pipeline(self):
        """User input reaching the HTML sink is encoded at that sink."""
        user_input = (
            "A dark & mysterious <script>alert('xss')</script> character appears..."
        )

        # Sanitize for HTML display — the sink these controls actually protect.
        html_safe = sanitize_html(user_input, allowed_tags=[])
        assert "alert" not in html_safe or "&lt;script&gt;" in html_safe

    def test_multilayer_security(self):
        """Each layer guards its own sink.

        Note what is NOT asserted: that prompt text is scrubbed. A prompt is not
        an HTML or SQL sink, and the same string bound for an LLM is left intact
        by design — `<script>` in a manuscript is prose, and mangling it is how
        this codebase lost 93.5% of a book. Its safety comes from capability
        containment, not filtering (docs/ADR-002-book-as-root.md §4).
        """
        malicious = "'; DROP TABLE scenes; --<script>alert(1)</script>"

        sql_safe = sanitize_sql_string(malicious)
        html_safe = sanitize_html(malicious, allowed_tags=[])

        assert "DROP TABLE" not in sql_safe or "\\'" in sql_safe
        assert "<script>" not in html_safe.lower()
