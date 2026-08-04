from __future__ import annotations


def test_context_usage_prefers_latest_successful_context_os_attempt() -> None:
    import main

    class _Provider:
        model = "legacy-configured-model"
        last_usage = {
            "prompt_tokens": 999,
            "completion_tokens": 99,
            "cached_tokens": 9,
        }

    class _AttemptStore:
        def public_for_session(self, session_id: str):  # noqa: ANN001
            assert session_id == "actual-session"
            return [
                {
                    "session_id": session_id,
                    "request_id": "request-1",
                    "attempt_id": "attempt-1",
                    "purpose": "agent_response",
                    "state": "succeeded",
                    "model_id": "ctx-primary",
                    "context_window": 8192,
                    "actual_input_tokens": 588,
                    "actual_output_tokens": 34,
                    "actual_cache_read_tokens": 12,
                }
            ]

    previous = main.service_context.get("context_attempt_store")
    try:
        main.service_context.register("context_attempt_store", _AttemptStore())
        event = main._snapshot_context_usage_event(
            "actual-session", provider_chain=[_Provider()]
        )
    finally:
        main.service_context.register("context_attempt_store", previous)

    assert event is not None
    payload = event["payload"]
    assert payload["model"] == "ctx-primary"
    assert payload["context_window"] == 8192
    assert payload["prompt_tokens"] == 588
    assert payload["completion_tokens"] == 34
    assert payload["cached_tokens"] == 12
    assert payload["compact_at"] == int(8192 * 0.70)
    assert payload["attempts"][0]["attempt_id"] == "attempt-1"


def test_context_usage_works_without_legacy_provider_last_usage() -> None:
    import main

    class _AttemptStore:
        def public_for_session(self, session_id: str):  # noqa: ANN001
            return [
                {
                    "session_id": session_id,
                    "request_id": "request-2",
                    "attempt_id": "attempt-2",
                    "purpose": "agent_response",
                    "state": "succeeded",
                    "model_id": "ctx-primary",
                    "context_window": 8192,
                    "actual_input_tokens": 610,
                    "actual_output_tokens": 31,
                    "actual_cache_read_tokens": 0,
                }
            ]

    previous = main.service_context.get("context_attempt_store")
    try:
        main.service_context.register("context_attempt_store", _AttemptStore())
        event = main._snapshot_context_usage_event(
            "attempt-only-session", provider_chain=[], fallback_provider=None
        )
    finally:
        main.service_context.register("context_attempt_store", previous)

    assert event is not None
    assert event["payload"]["model"] == "ctx-primary"
    assert event["payload"]["context_window"] == 8192
    assert event["payload"]["prompt_tokens"] == 610
