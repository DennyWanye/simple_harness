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


def test_exc_info_true_takes_current_exception_and_redacts_secret():
    try:
        raise KeyError("token sk-secret123456789")
    except KeyError:
        event = add_safe_exception_summary(None, "warning", {"event": "x_failed", "exc_info": True})

    redacted = redact_log_event(None, "warning", event)

    assert redacted["error_type"] == "KeyError"
    assert "sk-secret" not in redacted["error_message"]


def test_exc_info_instance_is_summarized():
    error = RuntimeError("boom")
    event = add_safe_exception_summary(None, "warning", {"exc_info": error})

    assert event["error_type"] == "RuntimeError"
    assert event["error_message"] == "boom"


def test_exc_info_true_outside_except_adds_nothing():
    event = add_safe_exception_summary(None, "warning", {"event": "x", "exc_info": True})

    assert "error_type" not in event
    assert "error_message" not in event


def test_structlog_warning_with_exc_info_renders_redacted_summary():
    import io
    import json
    import logging

    import structlog

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(processor=structlog.processors.JSONRenderer())
    )
    std = logging.getLogger("test_log_redaction_exc_info")
    std.handlers[:] = [handler]
    std.propagate = False
    std.setLevel(logging.DEBUG)
    logger = structlog.wrap_logger(
        std,
        processors=[
            add_safe_exception_summary,
            redact_log_event,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
    )
    try:
        raise ValueError("key sk-secret123456789")
    except ValueError:
        logger.warning("probe_failed", exc_info=True)

    line = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert line["error_type"] == "ValueError"
    assert "sk-secret" not in line["error_message"]
    assert "sk-secret" not in stream.getvalue()
