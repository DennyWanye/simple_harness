"""Final-boundary redaction shared by stdlib and structlog JSON logs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from deskpet.security.redaction import TraceRedactor
from deskpet.security.sensitive_text import redact_sensitive_text


_TRACE_REDACTOR = TraceRedactor()


def add_safe_exception_summary(
    _logger: object,
    _method_name: str,
    event_dict: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep a diagnosable exception type/message before traceback redaction.

    ``exc_info`` itself remains subject to the final deny-by-default trace
    redactor. Third-party stdlib loggers commonly provide the real exception
    only inside that tuple, so extract the bounded public summary first; the
    message still passes through ``redact_log_event`` immediately afterward.
    """

    event = dict(event_dict)
    exc_info = event.get("exc_info")
    if (
        isinstance(exc_info, tuple)
        and len(exc_info) >= 2
        and isinstance(exc_info[1], BaseException)
    ):
        error = exc_info[1]
        event.setdefault("error_type", type(error).__name__)
        event.setdefault("error_message", str(error)[:2000])
    return event


def _redact_text_values(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _redact_text_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_text_values(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_text_values(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def redact_log_event(
    _logger: object,
    _method_name: str,
    event_dict: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove secrets and common PII immediately before JSON rendering.

    This processor is deliberately installed on both structlog's processor
    chain and ProcessorFormatter's foreign pre-chain.  Therefore third-party
    stdlib records and first-party structured records cross the same final
    privacy boundary before they reach stderr or ``backend.log``.
    """

    key_redacted = _TRACE_REDACTOR.redact(dict(event_dict))
    if not isinstance(key_redacted, dict):  # pragma: no cover - defensive
        return {"event": "[REDACTED]"}
    return _redact_text_values(key_redacted)


__all__ = ["add_safe_exception_summary", "redact_log_event"]
