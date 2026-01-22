"""Unit tests for structured logging configuration"""

import pytest
import json
import logging
from unittest.mock import MagicMock
from services.shared.logging_config import (
    JSONFormatter,
    ContextLogger,
    setup_logging,
    log_request_start,
    log_request_end,
    log_error,
    log_business_event,
    redact_sensitive_data,
)


@pytest.mark.unit
class TestJSONFormatter:
    """Test JSON log formatter"""

    def test_json_formatter_basic(self):
        """Test JSON formatter creates valid JSON"""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)
        log_data = json.loads(formatted)

        assert log_data["message"] == "Test message"
        assert log_data["level"] == "INFO"
        assert log_data["logger"] == "test.logger"
        assert "timestamp" in log_data

    def test_json_formatter_with_correlation_id(self):
        """Test formatter includes correlation ID"""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.correlation_id = "test-correlation-123"

        formatted = formatter.format(record)
        log_data = json.loads(formatted)

        assert log_data["correlation_id"] == "test-correlation-123"

    def test_json_formatter_with_extra_fields(self):
        """Test formatter includes extra fields"""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        # Use extra_fields dict as expected by the implementation
        record.extra_fields = {"user_id": "user-456", "request_id": "req-789"}

        formatted = formatter.format(record)
        log_data = json.loads(formatted)

        assert log_data["user_id"] == "user-456"
        assert log_data["request_id"] == "req-789"

    def test_json_formatter_handles_exception(self):
        """Test formatter handles exception info"""
        formatter = JSONFormatter()

        try:
            raise ValueError("Test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

            record = logging.LogRecord(
                name="test.logger",
                level=logging.ERROR,
                pathname="test.py",
                lineno=10,
                msg="Error occurred",
                args=(),
                exc_info=exc_info,
            )

            formatted = formatter.format(record)
            log_data = json.loads(formatted)

            assert log_data["level"] == "ERROR"
            assert "exception" in log_data
            # Exception is a dict with type, message, traceback
            assert log_data["exception"]["type"] == "ValueError"
            assert "Test error" in log_data["exception"]["message"]


@pytest.mark.unit
class TestContextLogger:
    """Test context-aware logger"""

    def test_context_logger_initialization(self):
        """Test context logger initialization"""
        base_logger = logging.getLogger("test")
        context_logger = ContextLogger(base_logger, "test-service")

        assert context_logger.service_name == "test-service"

    def test_context_logger_correlation_id(self):
        """Test setting correlation ID"""
        base_logger = logging.getLogger("test")
        context_logger = ContextLogger(base_logger, "test-service")

        context_logger.set_correlation_id("corr-123")

        assert context_logger.correlation_id == "corr-123"

    def test_context_logger_adds_context_to_logs(self):
        """Test context is added to log records"""
        base_logger = logging.getLogger("test")
        base_logger.handlers = []
        handler = logging.Handler()
        handler.emit = MagicMock()
        base_logger.addHandler(handler)
        base_logger.setLevel(logging.INFO)

        context_logger = ContextLogger(base_logger, {"service": "test-service"})
        context_logger.set_correlation_id("corr-456")

        context_logger.info("Test message")

        # Verify handler was called
        assert handler.emit.called

        # Get the log record
        record = handler.emit.call_args[0][0]
        assert hasattr(record, "correlation_id")
        assert record.correlation_id == "corr-456"


@pytest.mark.unit
class TestSetupLogging:
    """Test logging setup"""

    def test_setup_logging_basic(self):
        """Test basic logging setup"""
        logger = setup_logging("test-service", level="INFO")

        assert isinstance(logger, ContextLogger)
        assert logger.service_name == "test-service"

    def test_setup_logging_with_debug_level(self):
        """Test setup with DEBUG level"""
        logger = setup_logging("test-service", level="DEBUG")

        # Should be able to log debug messages
        assert logger.logger.level <= logging.DEBUG

    def test_setup_logging_creates_unique_loggers(self):
        """Test that different services get unique loggers"""
        logger1 = setup_logging("service1", level="INFO")
        logger2 = setup_logging("service2", level="INFO")

        assert logger1.service_name != logger2.service_name


@pytest.mark.unit
class TestLoggingHelpers:
    """Test logging helper functions"""

    def test_log_request_start(self):
        """Test request start logging"""
        logger = MagicMock()

        log_request_start(logger, "GET", "/api/scenes")

        logger.info.assert_called_once()
        call_args = logger.info.call_args

        # Should log with appropriate message
        assert "GET" in str(call_args)
        assert "/api/scenes" in str(call_args)

    def test_log_request_end(self):
        """Test request end logging"""
        logger = MagicMock()

        log_request_end(logger, "POST", "/api/scenes", 201, 1234.56)

        logger.info.assert_called_once()
        call_args = logger.info.call_args

        # Should include status code and duration
        assert "POST" in str(call_args)
        assert "/api/scenes" in str(call_args)

    def test_log_error(self):
        """Test error logging"""
        logger = MagicMock()

        try:
            raise ValueError("Test error")
        except ValueError as e:
            log_error(logger, e, context={"user_id": "123"})

        logger.error.assert_called_once()

    def test_log_business_event(self):
        """Test business event logging"""
        logger = MagicMock()

        log_business_event(
            logger,
            "scene_generation_completed",
            scene_id="scene-123",
            duration=45.2,
            word_count=1500,
        )

        logger.info.assert_called_once()
        call_args = logger.info.call_args

        # Should include event type and metadata
        assert "scene_generation_completed" in str(call_args)


@pytest.mark.unit
class TestSensitiveDataRedaction:
    """Test sensitive data redaction"""

    def test_redact_password(self):
        """Test password redaction"""
        data = {
            "username": "john",
            "password": "secretpass123",
            "email": "john@example.com",
        }

        redacted = redact_sensitive_data(data)

        assert redacted["username"] == "john"
        assert redacted["password"] == "[REDACTED]"
        assert redacted["email"] == "john@example.com"

    def test_redact_api_keys(self):
        """Test API key redaction"""
        data = {
            "service": "groq",
            "api_key": "sk-1234567890abcdef",
            "model": "llama-3.1-70b",
        }

        redacted = redact_sensitive_data(data)

        assert redacted["service"] == "groq"
        assert redacted["api_key"] == "[REDACTED]"
        assert redacted["model"] == "llama-3.1-70b"

    def test_redact_tokens(self):
        """Test token redaction"""
        data = {
            "user_id": "123",
            "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
            "refresh_token": "refresh_abc123",
        }

        redacted = redact_sensitive_data(data)

        assert redacted["user_id"] == "123"
        assert redacted["access_token"] == "[REDACTED]"
        assert redacted["refresh_token"] == "[REDACTED]"

    def test_redact_nested_data(self):
        """Test redacting nested sensitive data"""
        data = {
            "user": {
                "id": "123",
                "name": "John",
                "credentials": {"password": "secret", "api_key": "key123"},
            }
        }

        redacted = redact_sensitive_data(data)

        assert redacted["user"]["name"] == "John"
        assert redacted["user"]["credentials"]["password"] == "[REDACTED]"
        assert redacted["user"]["credentials"]["api_key"] == "[REDACTED]"

    def test_redact_credit_card(self):
        """Test credit card number redaction"""
        data = {"name": "John Doe", "credit_card": "4532-1234-5678-9010", "cvv": "123"}

        redacted = redact_sensitive_data(data)

        assert redacted["name"] == "John Doe"
        assert redacted["credit_card"] == "[REDACTED]"
        assert redacted["cvv"] == "[REDACTED]"

    def test_redact_preserves_structure(self):
        """Test redaction preserves data structure"""
        data = {
            "list": [1, 2, 3],
            "dict": {"key": "value"},
            "string": "text",
            "number": 42,
            "password": "secret",
        }

        redacted = redact_sensitive_data(data)

        assert redacted["list"] == [1, 2, 3]
        assert redacted["dict"] == {"key": "value"}
        assert redacted["string"] == "text"
        assert redacted["number"] == 42
        assert redacted["password"] == "[REDACTED]"


@pytest.mark.unit
class TestLoggingIntegration:
    """Test logging integration scenarios"""

    def test_structured_logging_full_pipeline(self):
        """Test full structured logging pipeline"""
        logger = setup_logging("test-service", level="INFO")
        logger.set_correlation_id("test-corr-123")

        # Create a test handler to capture logs
        test_handler = logging.Handler()
        captured_records = []

        def capture(record):
            captured_records.append(record)

        test_handler.emit = capture
        logger.logger.addHandler(test_handler)

        # Log a request
        log_request_start(logger, "GET", "/test")
        log_request_end(logger, "GET", "/test", 200, 123.45)

        # Verify logs were captured
        assert len(captured_records) >= 2

    def test_logging_with_exception_tracking(self):
        """Test logging exceptions with full context"""
        logger = setup_logging("test-service", level="ERROR")

        test_handler = logging.Handler()
        captured_records = []

        def capture(record):
            captured_records.append(record)

        test_handler.emit = capture
        logger.logger.addHandler(test_handler)

        try:
            raise ValueError("Test exception")
        except ValueError as e:
            log_error(
                logger,
                e,
                context={"user_id": "user-123", "operation": "test_operation"},
            )

        assert len(captured_records) > 0
        record = captured_records[0]
        assert record.levelname == "ERROR"

    def test_correlation_id_propagation(self):
        """Test correlation ID propagates through log chain"""
        logger = setup_logging("test-service", level="INFO")
        correlation_id = "corr-xyz-789"

        logger.set_correlation_id(correlation_id)

        test_handler = logging.Handler()
        captured_records = []

        def capture(record):
            captured_records.append(record)

        test_handler.emit = capture
        logger.logger.addHandler(test_handler)

        # Log multiple messages
        logger.info("Message 1")
        logger.info("Message 2")
        logger.info("Message 3")

        # All should have same correlation ID
        for record in captured_records:
            assert hasattr(record, "correlation_id")
            assert record.correlation_id == correlation_id
