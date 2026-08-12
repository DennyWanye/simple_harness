"""Final-boundary redaction shared by stdlib and structlog JSON logs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from deskpet.security.redaction import TraceRedactor
from deskpet.security.sensitive_text import redact_sensitive_text


_TRACE_REDACTOR = TraceRedactor()


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


__all__ = ["redact_log_event"]
