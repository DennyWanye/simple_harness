from __future__ import annotations

import pytest


class _FakeWs:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_json(self, frame: dict) -> None:
        self.frames.append(frame)


def test_public_section_drops_sensitive_or_private_preview() -> None:
    import main

    section = main._public_context_section({
        "kind": "system",
        "label": "System",
        "estimated_tokens": 4,
        "ref": "Authorization: Bearer sk-private /Users/private/file",
    })
    assert section is not None
    assert section["public_preview"] is None


@pytest.mark.asyncio
async def test_usage_hydration_joins_snapshot_metadata_on_its_own_version_axis():
    import main

    class _SessionDB:
        async def get_context_usage_state(self, session_id: str):
            return {
                "session_id": session_id,
                "version": 9,
                "source": "measured",
                "model": "deepseek-v4",
            }

        async def get_latest_sdk_context_public_snapshot(self, session_id: str):
            return {
                "session_id": session_id,
                "snapshot_id": "snapshot-3",
                "snapshot_version": 3,
                "snapshot_fingerprint": "sha256:snapshot-3",
            }

    previous_db = main.service_context.get("session_db")
    main._session_context_state.pop("session-joined", None)
    try:
        main.service_context.register("session_db", _SessionDB())
        result = await main._read_context_usage_authority("session-joined")
    finally:
        main.service_context.register("session_db", previous_db)

    assert result["version"] == 9
    assert result["snapshot_version"] == 3
    assert result["snapshot_id"] == "snapshot-3"


@pytest.mark.asyncio
async def test_breakdown_reads_only_durable_public_snapshot_and_usage(monkeypatch):
    import main

    calls: list[tuple[str, str]] = []

    class _SessionDB:
        async def get_latest_sdk_context_public_snapshot(self, session_id: str):
            calls.append(("snapshot", session_id))
            return {
                "snapshot_id": "snapshot-7",
                "snapshot_version": 3,
                "snapshot_fingerprint": "sha256:public-7",
                "session_id": session_id,
                "provider_binding": {"model_id": "deepseek-v4", "context_window": 128_000},
                "sections": [{
                    "kind": "history",
                    "label": "Conversation history",
                    "count": 2,
                    "estimated_tokens": 18,
                    "availability": "available",
                    "ref": "2 条已脱敏历史消息",
                }],
                "budget": {
                    "estimated_prompt_tokens": 20,
                    "context_window": 128_000,
                    "effective_ceiling": 115_200,
                    "compact_at": 102_400,
                },
            }

        async def get_context_usage_state(self, session_id: str):
            calls.append(("usage", session_id))
            return {
                "schema_version": 2,
                "session_id": session_id,
                "source": "measured",
                "sample_id": "sample-9",
                "version": 9,
                "availability": "available",
                "model": "deepseek-v4",
                "prompt_tokens": 21,
                "completion_tokens": 3,
                "cached_tokens": 1,
                "context_window": 128_000,
                "effective_ceiling": 115_200,
                "compact_at": 102_400,
                "has_measurement": True,
                "updated_at": 123.0,
            }

        async def get_recent(self, *_args, **_kwargs):
            raise AssertionError("legacy history probe must be unreachable")

        async def list_context_usage_history(self, *_args, **_kwargs):
            raise AssertionError("dynamic history probe must be unreachable")

    previous_db = main.service_context.get("session_db")
    previous_facts = main.service_context.get("facts_store")
    try:
        main.service_context.register("session_db", _SessionDB())
        main.service_context.register("facts_store", object())
        result = await main._read_context_breakdown_authority("session-a")
    finally:
        main.service_context.register("session_db", previous_db)
        main.service_context.register("facts_store", previous_facts)

    assert calls == [("snapshot", "session-a"), ("usage", "session-a")]
    assert result["snapshot_id"] == "snapshot-7"
    assert result["snapshot_version"] == 3
    assert result["usage_version"] == 9
    assert result["sample_id"] == "sample-9"
    assert result["model"] == "deepseek-v4"
    assert result["sections"] == [{
        "kind": "history",
        "label": "Conversation history",
        "count": 2,
        "tokens": 18,
        "token_source": "estimated",
        "availability": "available",
        "public_preview": "2 条已脱敏历史消息",
        "preview_truncated": False,
    }]
    assert "preview" not in result["sections"][0]
    assert "history" not in result


@pytest.mark.asyncio
async def test_breakdown_without_snapshot_is_explicitly_unavailable_not_zero():
    import main

    class _SessionDB:
        async def get_latest_sdk_context_public_snapshot(self, _session_id: str):
            return None

        async def get_context_usage_state(self, session_id: str):
            return {
                "schema_version": 2,
                "session_id": session_id,
                "source": "binding_only",
                "sample_id": None,
                "version": 1,
                "availability": "available",
                "model": "bound-model",
                "prompt_tokens": 0,
                "context_window": 0,
                "effective_ceiling": 0,
                "has_measurement": False,
            }

    previous_db = main.service_context.get("session_db")
    try:
        main.service_context.register("session_db", _SessionDB())
        result = await main._read_context_breakdown_authority("session-empty")
    finally:
        main.service_context.register("session_db", previous_db)

    assert result["availability"] == "unavailable"
    assert result["context_state"] == "binding_only"
    assert result["snapshot_id"] is None
    assert result["snapshot_version"] is None
    assert result["last_usage_prompt_tokens"] is None
    assert result["total_estimated_tokens"] is None
    assert result["sections"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {},
    {"session_id": "session-a"},
    {"request_id": "request-a"},
    {"session_id": "session-b", "request_id": "request-a"},
])
async def test_breakdown_handler_fails_closed_without_exact_session_and_correlation(
    monkeypatch, payload,
):
    import main

    ws = _FakeWs()
    reader_calls: list[str] = []

    async def _reader(session_id: str):
        reader_calls.append(session_id)
        return {"session_id": session_id}

    monkeypatch.setattr(main, "_read_context_breakdown_authority", _reader)
    accepted = await main._handle_context_breakdown_request(
        ws, payload, owner_session_id="session-a"
    )
    assert accepted is False
    assert reader_calls == []
    assert ws.frames[-1]["type"] == "error"
    assert ws.frames[-1]["payload"]["code"] == "context_breakdown_request_invalid"


@pytest.mark.asyncio
async def test_breakdown_handler_echoes_correlation_and_current_identities(monkeypatch):
    import main

    ws = _FakeWs()

    async def _reader(session_id: str):
        return {
            "session_id": session_id,
            "snapshot_id": "snapshot-current",
            "snapshot_version": 4,
            "sample_id": "sample-current",
            "usage_version": 8,
        }

    monkeypatch.setattr(main, "_read_context_breakdown_authority", _reader)
    accepted = await main._handle_context_breakdown_request(
        ws,
        {"session_id": "session-a", "request_id": "request-current"},
        owner_session_id="session-a",
    )
    assert accepted is True
    assert ws.frames == [{
        "type": "context_breakdown_response",
        "payload": {
            "session_id": "session-a",
            "snapshot_id": "snapshot-current",
            "snapshot_version": 4,
            "sample_id": "sample-current",
            "usage_version": 8,
            "correlation_id": "request-current",
        },
    }]
