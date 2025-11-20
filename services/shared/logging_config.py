"""
Structured Logging Configuration for Polyphony

This module provides structured logging with JSON output, correlation IDs,
and proper context management across the application.
"""

import logging
import sys
from typing import Any, Dict
from datetime import datetime
import json


class JSONFormatter(logging.Formatter):
    """Format logs as JSON for easier parsing and aggregation"""

    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add correlation ID if present
        if hasattr(record, "correlation_id"):
            log_data["correlation_id"] = record.correlation_id

        # Add service name
        if hasattr(record, "service"):
            log_data["service"] = record.service

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }

        # Add any custom fields
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)

        return json.dumps(log_data)


class ContextLogger(logging.LoggerAdapter):
    """Logger adapter that adds context to all log messages"""

    def __init__(self, logger: logging.Logger, service_name: str):
        super().__init__(logger, {})
        self.service_name = service_name
        self.correlation_id: str | None = None

    def set_correlation_id(self, correlation_id: str):
        """Set correlation ID for request tracing"""
        self.correlation_id = correlation_id

    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        """Add context to log record"""
        extra = kwargs.get("extra", {})

        if self.correlation_id:
            extra["correlation_id"] = self.correlation_id

        extra["service"] = self.service_name

        # Merge any additional fields
        if "extra_fields" in kwargs:
            extra["extra_fields"] = kwargs.pop("extra_fields")

        kwargs["extra"] = extra
        return msg, kwargs


def setup_logging(service_name: str, level: str = "INFO") -> ContextLogger:
    """
    Set up structured logging for the service

    Args:
        service_name: Name of the service (e.g., "api-gateway", "orchestrator")
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

    Returns:
        ContextLogger instance for the service
    """
    # Create root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers
    root_logger.handlers = []

    # Create console handler with JSON formatting
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(console_handler)

    # Create service-specific logger
    service_logger = logging.getLogger(service_name)

    # Return context-aware logger
    return ContextLogger(service_logger, service_name)


def sanitize_log_message(message: str) -> str:
    """
    Sanitize log messages to prevent logging sensitive data

    Redacts common sensitive fields like passwords, tokens, and API keys
    """
    import re

    patterns = [
        (
            r"(password|passwd|pwd)['\"]?\s*[:=]\s*['\"]?([^'\"\s]+)",
            r"\1=***REDACTED***",
        ),
        (
            r"(api_key|apikey|token|secret)['\"]?\s*[:=]\s*['\"]?([^'\"\s]+)",
            r"\1=***REDACTED***",
        ),
        (r"(authorization:\s*bearer\s+)(\S+)", r"\1***REDACTED***"),
    ]

    for pattern, replacement in patterns:
        message = re.sub(pattern, replacement, message, flags=re.IGNORECASE)

    return message


# Example usage functions
def log_request_start(logger: ContextLogger, method: str, path: str, **kwargs):
    """Log the start of an HTTP request"""
    logger.info(
        f"Request started: {method} {path}",
        extra_fields={
            "event": "request_start",
            "method": method,
            "path": path,
            **kwargs,
        },
    )


def log_request_end(
    logger: ContextLogger, method: str, path: str, status_code: int, duration_ms: float
):
    """Log the end of an HTTP request"""
    logger.info(
        f"Request completed: {method} {path} - {status_code}",
        extra_fields={
            "event": "request_end",
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
        },
    )


def log_error(logger: ContextLogger, error: Exception, context: Dict[str, Any] = None):
    """Log an error with context"""
    logger.error(
        f"Error occurred: {str(error)}",
        exc_info=True,
        extra_fields={
            "event": "error",
            "error_type": type(error).__name__,
            **(context or {}),
        },
    )


def log_business_event(logger: ContextLogger, event_name: str, **kwargs):
    """Log a business/domain event"""
    logger.info(
        f"Business event: {event_name}", extra_fields={"event": event_name, **kwargs}
    )


def redact_sensitive_data(data: Any) -> Any:
    """
    Redact sensitive information from data before logging

    Args:
        data: Data that may contain sensitive information

    Returns:
        Data with sensitive fields redacted
    """
    if data is None:
        return None

    # List of sensitive field names to redact
    sensitive_fields = {
        "password",
        "passwd",
        "pwd",
        "secret",
        "api_key",
        "apikey",
        "api-key",
        "token",
        "access_token",
        "refresh_token",
        "auth_token",
        "private_key",
        "privatekey",
        "credit_card",
        "creditcard",
        "card_number",
        "cvv",
        "cvc",
        "ssn",
        "social_security",
        "authorization",
        "auth",
    }

    if isinstance(data, dict):
        # Recursively redact dictionary
        redacted = {}
        for key, value in data.items():
            key_lower = str(key).lower()

            # Check if key matches any sensitive field
            if any(sensitive in key_lower for sensitive in sensitive_fields):
                redacted[key] = "[REDACTED]"
            elif isinstance(value, (dict, list)):
                # Recursively redact nested structures
                redacted[key] = redact_sensitive_data(value)
            else:
                redacted[key] = value

        return redacted

    elif isinstance(data, list):
        # Recursively redact list
        return [redact_sensitive_data(item) for item in data]

    elif isinstance(data, str):
        # Redact strings that look like tokens or keys
        # Check if it looks like a JWT (three base64 segments)
        if data.count(".") == 2 and len(data) > 20:
            return "[REDACTED]"

        # Check if it looks like an API key (long alphanumeric string)
        if len(data) > 20 and data.replace("-", "").replace("_", "").isalnum():
            # Could be an API key
            if any(
                prefix in data.lower() for prefix in ["sk-", "pk-", "key_", "token_"]
            ):
                return "[REDACTED]"

        return data

    else:
        # Return other types as-is
        return data
