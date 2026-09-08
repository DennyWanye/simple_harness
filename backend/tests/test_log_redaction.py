from __future__ import annotations

from observability.log_redaction import redact_log_event


def test_log_redaction_removes_sensitive_keys_and_nested_authorization() -> None:
    event = redact_log_event(
        None,
        "info",
        {
            "event": "provider request",
            "api_key": "sk-top-level-secret-1234567890",
            "request": {
                "Authorization": "Bearer nested-secret-token-1234567890",
                "safe": "kept",
            },
        },
    )

    assert event["api_key"] == "[REDACTED]"
    assert event["request"]["Authorization"] == "[REDACTED]"
    assert event["request"]["safe"] == "kept"


def test_log_redaction_removes_secrets_and_pii_embedded_in_messages() -> None:
    raw = (
        "token=plain-secret-value contact dev@example.com or 13800138000; "
        "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature"
    )

    event = redact_log_event(None, "error", {"event": raw})
    rendered = event["event"]

    assert "plain-secret-value" not in rendered
    assert "dev@example.com" not in rendered
    assert "13800138000" not in rendered
    assert "eyJhbGci" not in rendered
    assert "[REDACTED:" in rendered


def test_log_redaction_keeps_host_raised_error_types_diagnosable() -> None:
    """事故 J：诊断行不得被密钥模式跨词吞掉。

    线上实测 ``error_type=KeyError stable_code=unclassified`` 被压成
    ``error_type=[REDACTED]=unclassified``——旧的 ``[-_ A-Za-z0-9]{12,}``
    字符类含空格，从 ``Key`` 一路吃到 ``stable_code``。异常类型是无载荷的
    诊断信息，必须保留。
    """

    raw = (
        "sdk_tool_handler_failed tool=memory_forget "
        "error_type=KeyError stable_code=unclassified"
    )

    event = redact_log_event(None, "error", {"event": raw})

    assert event["event"] == raw


def test_log_redaction_still_removes_key_shaped_tokens_and_bearer_headers() -> None:
    event = redact_log_event(
        None,
        "error",
        {
            "event": (
                "upstream rejected key_ABCDEFGHIJKLMNOP with "
                "Bearer abcdefghijklmnopqrstuvwxyz"
            )
        },
    )
    rendered = event["event"]

    assert "key_ABCDEFGHIJKLMNOP" not in rendered
    assert "abcdefghijklmnopqrstuvwxyz" not in rendered
    assert rendered.count("[REDACTED]") == 2


def test_log_redaction_preserves_non_sensitive_correlation_fields() -> None:
    event = redact_log_event(
        None,
        "info",
        {
            "event": "workflow_node_completed",
            "request_id": "req-123",
            "run_id": "run-456",
            "node_id": "finalize",
            "elapsed_ms": 42,
        },
    )

    assert event == {
        "event": "workflow_node_completed",
        "request_id": "req-123",
        "run_id": "run-456",
        "node_id": "finalize",
        "elapsed_ms": 42,
    }
