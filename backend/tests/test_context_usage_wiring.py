from __future__ import annotations

from typing import Any

import pytest

from agent.agent_loop import ContextCompactedEvent
from deskpet.agent.run_presenter import (
    PresentationState,
    RunPresentationContext,
    _present_compacted,
)


class _WS:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send_json(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)


@pytest.mark.asyncio
async def test_binding_commit_pushes_binding_only_context_authority() -> None:
    import main

    class _SessionDB:
        async def get_context_usage_state(self, session_id: str):
            return {
                "schema_version": 2,
                "session_id": session_id,
                "source": "binding_only",
                "version": 7,
                "binding_epoch": 7,
                "availability": "available",
                "provider_id": None,
                "model": "kimi-k3",
                "model_id": "kimi-k3",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_tokens": 0,
                "context_window": 0,
                "effective_ceiling": 0,
                "compact_at": 0,
                "recall_sweet": 0,
                "has_measurement": False,
                "updated_at": 700.0,
            }

    ws = _WS()
    main._session_context_state.pop("session-binding-only", None)
    payload = await main._send_context_usage_authority(
        ws, _SessionDB(), "session-binding-only"
    )

    assert payload["model"] == "kimi-k3"
    assert payload["source"] == "binding_only"
    assert payload["has_measurement"] is False
    assert ws.frames == [{"type": "context_usage", "payload": payload}]


@pytest.mark.asyncio
async def test_chat_launcher_emits_terminal_error_when_failure_precedes_run_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main

    delivered: list[tuple[dict[str, Any], str, str]] = []

    async def fail_before_open(*_args, **_kwargs) -> None:
        raise TypeError("pre-open contract mismatch")

    async def capture_error(
        _ws,
        frame: dict[str, Any],
        *,
        session_id: str,
        request_id: str = "",
    ) -> None:
        delivered.append((frame, session_id, request_id))

    monkeypatch.setattr(main, "_run_product_harness_chat_with_timeout", fail_before_open)
    monkeypatch.setattr(main, "_send_chat_error", capture_error)

    task = main._launch_product_harness_chat(
        _WS(),
        "hello",
        "session-pre-open",
        client_request_id="request-pre-open",
        client_turn_id="turn-pre-open",
    )
    with pytest.raises(TypeError, match="pre-open contract mismatch"):
        await task

    assert len(delivered) == 1
    frame, session_id, request_id = delivered[0]
    assert session_id == "session-pre-open"
    assert request_id == "request-pre-open"
    assert frame["type"] == "chat_v2_error"
    assert frame["payload"] == {
        "error": "pre-open contract mismatch",
        "detail": "TypeError",
        "session_id": "session-pre-open",
        "run_id": frame["payload"]["run_id"],
        "task_scope_id": frame["payload"]["task_scope_id"],
        "request_id": "request-pre-open",
    }
    assert frame["payload"]["run_id"]
    assert frame["payload"]["task_scope_id"]


@pytest.mark.asyncio
async def test_provider_producer_persists_frozen_identity_and_sends_authority():
    import main

    class _AttemptStore:
        def public_for_session(self, session_id: str):  # noqa: ANN001
            return [{
                "session_id": session_id,
                "request_id": "request-7",
                "attempt_id": "attempt-2",
                "purpose": "agent_response",
                "state": "succeeded",
                "provider_id": "kimi",
                "model_id": "kimi-k3",
                "context_window": 131_072,
                "actual_input_tokens": 700,
                "actual_output_tokens": 20,
                "actual_cache_read_tokens": 4,
            }]

    class _SessionDB:
        def __init__(self) -> None:
            self.sample: dict[str, Any] | None = None
            self.binding_read = False

        async def get_session_provider_binding(self, _session_id: str):
            self.binding_read = True
            return {"provider_id": "glm", "binding_epoch": 10}

        async def record_context_usage_sample(self, sample):  # noqa: ANN001
            self.sample = sample

        async def get_context_usage_state(self, session_id: str):
            return {
                "schema_version": 2,
                "session_id": session_id,
                "source": "measured",
                "version": 11,
                "binding_epoch": 9,
                "availability": "available",
                "provider_id": "kimi",
                "model": "kimi-k3",
                "model_id": "kimi-k3",
                "prompt_tokens": 700,
                "completion_tokens": 20,
                "cached_tokens": 4,
                "context_window": 131_072,
                "effective_ceiling": 124_518,
                "compact_at": 91_750,
                "recall_sweet": 16_000,
                "has_measurement": True,
                "updated_at": 100.0,
            }

    db = _SessionDB()
    ws = _WS()
    previous_attempts = main.service_context.get("context_attempt_store")
    previous_db = main.service_context.get("session_db")
    main._session_context_state.pop("session-7", None)
    try:
        main.service_context.register("context_attempt_store", _AttemptStore())
        main.service_context.register("session_db", db)
        await main._emit_context_usage(
            ws,
            "session-7",
            provider_chain=[],
            root_run_id="root-7",
            request_id="request-7",
            binding_epoch=9,
            frozen_provider_id="kimi",
            frozen_model_id="kimi-k3",
        )
    finally:
        main.service_context.register("context_attempt_store", previous_attempts)
        main.service_context.register("session_db", previous_db)

    assert db.sample is not None
    assert db.binding_read is False
    assert db.sample["source_event_id"] == (
        "context-attempt:root-7:request-7:attempt-2"
    )
    assert db.sample["binding_epoch"] == 9
    assert db.sample["provider_id"] == "kimi"
    assert db.sample["model_id"] == "kimi-k3"
    assert len(ws.frames) == 1
    assert ws.frames[0]["payload"]["version"] == 11
    assert ws.frames[0]["payload"]["source"] == "measured"


def test_context_cache_rejects_late_older_version():
    import main

    sid = "cache-version-session"
    main._session_context_state.pop(sid, None)
    newer = {"session_id": sid, "version": 8, "source": "measured"}
    older = {"session_id": sid, "version": 7, "source": "binding_only"}

    assert main._cache_context_usage_state(newer) == newer
    assert main._cache_context_usage_state(older) == newer
    assert main._session_context_state[sid] == newer


def test_run_context_usage_identity_comes_from_frozen_host_snapshot():
    import main

    class _Host:
        provider_plan = ("kimi",)
        provider_bindings = (("kimi", "kimi-k3", "inc-1", 7, 11),)

    assert main._snapshot_context_usage_binding_for_run(_Host()) == {
        "provider_id": "kimi",
        "preferred_model": "kimi-k3",
        "binding_epoch": 11,
    }


def test_run_context_usage_identity_rejects_untyped_or_legacy_bindings():
    import main

    class _LegacyPlanMapping:
        provider_plan = {
            "bindings": [{"provider_id": "wrong", "model_id": "wrong"}]
        }

    class _LegacyPair:
        provider_plan = ("kimi",)
        provider_bindings = (("kimi", "kimi-k3"),)

    assert main._snapshot_context_usage_binding_for_run(_LegacyPlanMapping()) == {}
    assert main._snapshot_context_usage_binding_for_run(_LegacyPair()) == {}


@pytest.mark.asyncio
async def test_run_context_usage_basis_freezes_exact_measured_sample():
    import main

    class _SessionDB:
        async def get_context_usage_state(self, session_id: str):
            assert session_id == "session-exact"
            return {
                "sample_id": "sample-before",
                "has_measurement": True,
            }

    assert await main._snapshot_context_usage_basis_for_run(
        _SessionDB(), "session-exact"
    ) == "sample-before"


@pytest.mark.asyncio
async def test_context_usage_request_reader_uses_only_durable_authority():
    import main

    class _SessionDB:
        async def get_context_usage_state(self, session_id: str):
            return {
                "schema_version": 2,
                "session_id": session_id,
                "source": "binding_only",
                "version": 4,
                "availability": "available",
                "model": "kimi-k3",
                "prompt_tokens": 0,
                "context_window": 0,
                "effective_ceiling": 0,
                "has_measurement": False,
            }

    previous_db = main.service_context.get("session_db")
    main._session_context_state.pop("cold-session", None)
    try:
        main.service_context.register("session_db", _SessionDB())
        state = await main._read_context_usage_authority("cold-session")
    finally:
        main.service_context.register("session_db", previous_db)

    assert state["source"] == "binding_only"
    assert state["model"] == "kimi-k3"
    assert state["context_window"] == 0
    assert state["has_measurement"] is False


@pytest.mark.asyncio
async def test_compaction_producer_uses_exact_lineage_and_real_window():
    class _SessionDB:
        def __init__(self) -> None:
            self.sample: dict[str, Any] | None = None
            self.compacted = False

        async def get_context_usage_sample(self, _session_id, sample_id):
            assert sample_id == "measured-1"
            return {
                "sample_id": "measured-1",
                "event_type": "provider_attempt",
                "binding_epoch": 4,
                "provider_id": "kimi",
                "model_id": "kimi-k3",
                "context_window": 131_072,
                "effective_ceiling": 124_518,
                "created_at": 10.0,
            }

        async def list_context_usage_history(self, *_args, **_kwargs):
            raise AssertionError("presenter must not infer lineage by time")

        async def get_context_usage_state(self, session_id: str):
            if self.compacted:
                return {
                    "schema_version": 2,
                    "session_id": session_id,
                    "source": "compacted",
                    "sample_id": "compacted-1",
                    "version": 3,
                    "binding_epoch": 4,
                    "provider_id": "kimi",
                    "model": "kimi-k3",
                    "model_id": "kimi-k3",
                    "context_window": 131_072,
                    "effective_ceiling": 124_518,
                    "has_measurement": True,
                }
            return {
                "schema_version": 2,
                "session_id": session_id,
                "source": "measured",
                "sample_id": "measured-1",
                "version": 2,
                "binding_epoch": 4,
                "provider_id": "kimi",
                "model": "kimi-k3",
                "model_id": "kimi-k3",
                "context_window": 131_072,
                "effective_ceiling": 124_518,
                "has_measurement": True,
            }

        async def record_context_usage_sample(self, sample):  # noqa: ANN001
            self.sample = sample
            self.compacted = True

    async def _noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    ws = _WS()
    db = _SessionDB()
    context = RunPresentationContext(
        session_id="session-c",
        text="",
        websocket=ws,
        services={},
        config=object(),
        messages=[],
        session_db=db,
        vector_worker=None,
        activity_store=None,
        provider_chain=None,
        fallback_provider=None,
        request_id="request-c",
        max_iterations=4,
        is_sentinel=False,
        broadcast=_noop,
        send_final=_noop,
        emit_context_usage=_noop,
        intent_label_from_turn=lambda _used: "ask",
        run_id="root-c",
    )
    event = ContextCompactedEvent(
        iteration=3,
        reduction=0.5,
        tokens_in=8_000,
        tokens_out=2_000,
        model="kimi-k3",
        source_event_id="context-compaction:session-c:root-c:3",
        based_on_sample_id="measured-1",
        occurred_at=20.0,
    )

    await _present_compacted(event, context, PresentationState())

    assert db.sample is not None
    assert db.sample["based_on_sample_id"] == "measured-1"
    assert db.sample["binding_epoch"] == 4
    assert db.sample["context_window"] == 131_072
    assert db.sample["effective_ceiling"] == 124_518
    assert [frame["type"] for frame in ws.frames] == [
        "context_usage",
        "context_compacted",
    ]
