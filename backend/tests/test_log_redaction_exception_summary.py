from __future__ import annotations

from observability.log_redaction import (
    add_safe_exception_summary,
    redact_log_event,
)


def test_exception_summary_survives_traceback_redaction_without_secret():
    try:
        raise ValueError("bad request with sk-secret123456789")
    except ValueError as error:
        event = add_safe_exception_summary(None, "error", {"exc_info": (ValueError, error, None)})

    redacted = redact_log_event(None, "error", event)

    assert redacted["error_type"] == "ValueError"
    assert "sk-secret" not in redacted["error_message"]
    assert "[REDACTED" in redacted["error_message"]
