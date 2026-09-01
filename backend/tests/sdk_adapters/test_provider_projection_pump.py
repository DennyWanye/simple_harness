from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest
from simple_harness import freeze_json, thaw_json

from deskpet.memory.session_db import SessionDB
from deskpet.sdk_adapters.context_authority import (
    SnapshotContractConflict,
    canonical_sha256,
)
from deskpet.sdk_adapters.delivery import ProductDeliveryAdapter
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from deskpet.sdk_adapters.provider_projection_pump import (
    ProviderProjectionContextV1,
    SdkProviderProjectionPump,
)


@dataclass(frozen=True)
class _Receipt:
    sequence: int
    invocation_id: str
    invocation_version: int
    run_id: str
    execution_session_id: str
    request_id: str
    payload: object
    payload_hash: str
    created_at: float


class _Source:
    def __init__(self, receipts=()):
        self.receipts = list(receipts)

    def list_provider_projection_receipts(self, *, after_sequence=0, limit=256):
        return tuple(
            item for item in self.receipts if item.sequence > after_sequence
        )[:limit]


def _receipt(
    sequence: int,
    state: str,
    *,
    usage: dict | None = None,
    error_code: str | None = None,
    invocation_id: str | None = None,
    invocation_version: int = 3,
) -> _Receipt:
    stable_invocation_id = invocation_id or f"inv-{sequence}"
    payload = {
        "invocation_id": stable_invocation_id,
        "invocation_version": invocation_version,
        "run_id": "run-a",
        "execution_session_id": "execution-a",
        "request_id": f"provider-turn:{sequence}",
        "handoff_attempt": 1,
        "state": state,
        "target": {
            "provider_id": "deepseek",
            "model": "deepseek-v4",
            "pricing_key": "price",
            "endpoint_identity": "endpoint",
            "adapter_key": "openai-compatible:v1",
        },
        "request_fingerprint": "a" * 64,
        "usage": None if usage is None else {"usage": usage, "budget": {}},
        "error_code": error_code,
        "settled_at": 100.0 + sequence,
    }
    return _Receipt(
        sequence,
        stable_invocation_id,
        invocation_version,
        "run-a",
        "execution-a",
        f"provider-turn:{sequence}",
        freeze_json(payload),
        canonical_sha256(payload),
        100.0 + sequence,
    )


def _context(_receipt: object) -> ProviderProjectionContextV1:
    return ProviderProjectionContextV1(
        session_id="session-a",
        root_run_id="run-a",
        request_id="request-a",
        snapshot_id="snapshot-a",
        binding_epoch=2,
        context_window=128_000,
        effective_ceiling=100_000,
    )


@pytest.mark.asyncio
async def test_pump_projects_every_terminal_attempt_but_only_trusted_usage(tmp_path) -> None:
    source = _Source([
        _receipt(1, "succeeded", usage={
            "input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
            "cache_tokens": 3, "reasoning_tokens": 1,
        }),
        _receipt(2, "succeeded"),
        _receipt(3, "failed", error_code="provider_protocol_error"),
        _receipt(4, "cancelled", error_code="provider_cancelled"),
        _receipt(5, "unknown", error_code="provider_cancelled_after_handoff"),
    ])
    db = SessionDB(tmp_path / "state.db")
    pump = SdkProviderProjectionPump(source, db, context_resolver=_context)

    assert await pump.run_until_idle() == 5
    assert await pump.run_until_idle() == 0
    attempts = await db.list_sdk_provider_attempts("session-a")
    assert [item["state"] for item in attempts] == [
        "succeeded", "succeeded", "failed", "cancelled", "unknown"
    ]
    assert attempts[2]["error_code"] == "provider_protocol_error"
    assert attempts[4]["error_code"] == "provider_cancelled_after_handoff"
    history = await db.list_context_usage_history("session-a")
    assert len(history) == 1
    assert history[0]["prompt_tokens"] == 10
    assert history[0]["cached_tokens"] == 3
    cursor = await db.get_sdk_provider_projection_cursor(
        "sdk-provider-projection-v1"
    )
    assert cursor is not None and cursor["source_sequence"] == 5
    await db.close()


@pytest.mark.asyncio
async def test_budget_only_unknown_receipt_advances_without_usage_measurement(
    tmp_path,
) -> None:
    receipt = _receipt(
        1,
        "unknown",
        error_code="provider_cancelled_after_handoff",
    )
    payload = thaw_json(receipt.payload)
    payload["usage"] = {
        "budget": {
            "kind": "unknown",
            "estimated": False,
        }
    }
    receipt = _Receipt(
        receipt.sequence,
        receipt.invocation_id,
        receipt.invocation_version,
        receipt.run_id,
        receipt.execution_session_id,
        receipt.request_id,
        freeze_json(payload),
        canonical_sha256(payload),
        receipt.created_at,
    )
    db = SessionDB(tmp_path / "state.db")
    pump = SdkProviderProjectionPump(
        _Source([receipt]), db, context_resolver=_context
    )

    assert await pump.run_until_idle() == 1
    attempts = await db.list_sdk_provider_attempts("session-a")
    assert len(attempts) == 1
    assert attempts[0]["state"] == "unknown"
    assert attempts[0]["usage"] is None
    assert await db.list_context_usage_history("session-a") == []
    cursor = await db.get_sdk_provider_projection_cursor(
        "sdk-provider-projection-v1"
    )
    assert cursor is not None and cursor["source_sequence"] == 1
    await db.close()


@pytest.mark.asyncio
async def test_non_usage_terminal_receipt_does_not_broadcast_unchanged_usage(
    tmp_path,
) -> None:
    source = _Source([
        _receipt(
            1,
            "unknown",
            error_code="provider_cancelled_after_handoff",
        )
    ])
    db = SessionDB(tmp_path / "state.db")
    notifications: list[str] = []

    async def committed(context: ProviderProjectionContextV1) -> None:
        notifications.append(context.session_id)

    pump = SdkProviderProjectionPump(
        source,
        db,
        context_resolver=_context,
        on_committed=committed,
    )

    assert await pump.run_until_idle() == 1
    assert notifications == []
    attempts = await db.list_sdk_provider_attempts("session-a")
    assert len(attempts) == 1
    assert attempts[0]["state"] == "unknown"
    cursor = await db.get_sdk_provider_projection_cursor(
        "sdk-provider-projection-v1"
    )
    assert cursor is not None and cursor["source_sequence"] == 1
    await db.close()


@pytest.mark.asyncio
async def test_pump_notifies_after_durable_projection_before_cursor_commit(tmp_path) -> None:
    source = _Source([_receipt(1, "succeeded", usage={
        "input_tokens": 10,
        "output_tokens": 2,
        "total_tokens": 12,
        "cache_tokens": None,
        "reasoning_tokens": None,
    })])
    db = SessionDB(tmp_path / "state.db")
    notifications: list[tuple[str, int]] = []

    async def committed(context: ProviderProjectionContextV1) -> None:
        attempts = await db.list_sdk_provider_attempts(context.session_id)
        assert len(attempts) == 1
        cursor = await db.get_sdk_provider_projection_cursor(
            "sdk-provider-projection-v1"
        )
        assert cursor is None
        notifications.append((context.session_id, 1))

    pump = SdkProviderProjectionPump(
        source,
        db,
        context_resolver=_context,
        on_committed=committed,
    )

    assert await pump.run_until_idle() == 1
    assert notifications == [("session-a", 1)]
    assert await pump.run_until_idle() == 0
    assert notifications == [("session-a", 1)]
    await db.close()


@pytest.mark.asyncio
async def test_callback_failure_keeps_receipt_for_idempotent_retry(tmp_path) -> None:
    source = _Source([_receipt(1, "succeeded", usage={
        "input_tokens": 10,
        "output_tokens": 2,
        "total_tokens": 12,
        "cache_tokens": None,
        "reasoning_tokens": None,
    })])
    db = SessionDB(tmp_path / "state.db")
    calls = 0

    async def flaky(_context: ProviderProjectionContextV1) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("live delivery unavailable")

    pump = SdkProviderProjectionPump(
        source, db, context_resolver=_context, on_committed=flaky
    )
    with pytest.raises(RuntimeError, match="delivery unavailable"):
        await pump.run_once()
    assert await db.get_sdk_provider_projection_cursor(
        "sdk-provider-projection-v1"
    ) is None
    assert len(await db.list_sdk_provider_attempts("session-a")) == 1

    assert await pump.run_once() == 1
    assert calls == 2
    cursor = await db.get_sdk_provider_projection_cursor(
        "sdk-provider-projection-v1"
    )
    assert cursor is not None and cursor["source_sequence"] == 1
    assert len(await db.list_sdk_provider_attempts("session-a")) == 1
    await db.close()


@pytest.mark.asyncio
async def test_succeeded_usage_with_unreported_cache_remains_measured(tmp_path) -> None:
    source = _Source([_receipt(1, "succeeded", usage={
        "input_tokens": 13,
        "output_tokens": 5,
        "total_tokens": 18,
        "cache_tokens": None,
        "reasoning_tokens": 2,
    })])
    db = SessionDB(tmp_path / "state.db")
    await db.set_context_usage_binding_state(
        "session-a",
        binding_epoch=2,
        provider_id="deepseek",
        model_id="deepseek-v4",
    )
    pump = SdkProviderProjectionPump(source, db, context_resolver=_context)

    assert await pump.run_until_idle() == 1
    history = await db.list_context_usage_history("session-a")
    assert len(history) == 1
    assert history[0]["prompt_tokens"] == 13
    assert history[0]["cached_tokens"] == 0
    assert history[0]["metadata"]["cache_tokens_available"] is False
    state = await db.get_context_usage_state("session-a")
    assert state["has_measurement"] is True
    assert state["prompt_tokens"] == 13
    assert state["cache_tokens_available"] is False
    await db.close()


@pytest.mark.asyncio
async def test_sp3_fault_after_outbox_read_leaves_session_and_cursor_untouched(tmp_path) -> None:
    db = SessionDB(tmp_path / "state.db")
    source = _Source([_receipt(1, "failed", error_code="definite")])

    def fault(point: str, _receipt: object) -> None:
        if point == "provider_projection.outbox.after_read":
            raise RuntimeError("fault-after-outbox")

    pump = SdkProviderProjectionPump(source, db, context_resolver=_context, fault=fault)
    with pytest.raises(RuntimeError, match="fault-after-outbox"):
        await pump.run_once()
    assert await db.list_sdk_provider_attempts("session-a") == []
    assert await db.get_sdk_provider_projection_cursor(
        "sdk-provider-projection-v1"
    ) is None

    clean = SdkProviderProjectionPump(source, db, context_resolver=_context)
    assert await clean.run_once() == 1
    await db.close()


@pytest.mark.asyncio
async def test_sp3_fault_after_session_write_replays_without_duplicate_measurement(tmp_path) -> None:
    db = SessionDB(tmp_path / "state.db")
    source = _Source([_receipt(1, "succeeded", usage={
        "input_tokens": 7, "output_tokens": 2, "total_tokens": 9,
        "cache_tokens": 0, "reasoning_tokens": None,
    })])

    def fault(point: str, _receipt: object) -> None:
        if point == "provider_projection.session.after_write":
            raise RuntimeError("fault-before-cursor")

    broken = SdkProviderProjectionPump(source, db, context_resolver=_context, fault=fault)
    with pytest.raises(RuntimeError, match="fault-before-cursor"):
        await broken.run_once()
    assert len(await db.list_sdk_provider_attempts("session-a")) == 1
    assert len(await db.list_context_usage_history("session-a")) == 1
    assert await db.get_sdk_provider_projection_cursor(
        "sdk-provider-projection-v1"
    ) is None

    restarted = SdkProviderProjectionPump(source, db, context_resolver=_context)
    assert await restarted.run_once() == 1
    assert len(await db.list_sdk_provider_attempts("session-a")) == 1
    assert len(await db.list_context_usage_history("session-a")) == 1
    cursor = await db.get_sdk_provider_projection_cursor(
        "sdk-provider-projection-v1"
    )
    assert cursor is not None and cursor["source_sequence"] == 1
    await db.close()


@pytest.mark.asyncio
async def test_unknown_receipt_can_reconcile_to_newer_succeeded_version(tmp_path) -> None:
    source = _Source([
        _receipt(
            1, "unknown", error_code="provider_error_after_handoff",
            invocation_id="inv-reconciled", invocation_version=3,
        ),
        _receipt(
            2, "succeeded", invocation_id="inv-reconciled", invocation_version=4,
            usage={
                "input_tokens": 11, "output_tokens": 4, "total_tokens": 15,
                "cache_tokens": 2, "reasoning_tokens": None,
            },
        ),
    ])
    db = SessionDB(tmp_path / "state.db")
    pump = SdkProviderProjectionPump(source, db, context_resolver=_context)
    assert await pump.run_until_idle() == 2
    attempts = await db.list_sdk_provider_attempts("session-a")
    assert len(attempts) == 1
    assert attempts[0]["state"] == "succeeded"
    assert attempts[0]["settlement_version"] == 4
    assert len(await db.list_context_usage_history("session-a")) == 1
    await db.close()


@pytest.mark.asyncio
async def test_missing_outbox_is_noop_and_hash_conflict_fails_closed(tmp_path) -> None:
    db = SessionDB(tmp_path / "state.db")
    source = _Source()
    pump = SdkProviderProjectionPump(source, db, context_resolver=_context)
    assert await pump.run_once() == 0

    valid = _receipt(1, "failed")
    source.receipts.append(
        _Receipt(**{**valid.__dict__, "payload_hash": "0" * 64})
    )
    with pytest.raises(SnapshotContractConflict, match="hash mismatch"):
        await pump.run_once()
    assert await db.list_sdk_provider_attempts("session-a") == []
    assert await db.get_sdk_provider_projection_cursor(
        "sdk-provider-projection-v1"
    ) is None
    await db.close()


@pytest.mark.asyncio
async def test_sdk_delivery_finish_does_not_read_legacy_presenter_usage() -> None:
    class Presenter:
        def __init__(self) -> None:
            self.finish_calls = 0

        async def finish_turn(self, *_args) -> None:
            self.finish_calls += 1

    presenter = Presenter()
    delivery = ProductDeliveryAdapter(
        session_id="session-a",
        request_id="request-a",
        run_id="run-a",
        presenter=presenter,
        adapter=object(),
        context=object(),
        state=object(),
    )
    await delivery.finish()
    assert presenter.finish_calls == 0


@pytest.mark.asyncio
async def test_product_provider_rejects_placeholder_zero_pricing() -> None:
    class Entry:
        enabled = True
        model = "deepseek-v4"
        models = ("deepseek-v4",)
        base_url = "https://provider.example/v1"
        config_revision = 1
        incarnation_id = "inc-1"

    class Registry:
        def get_entry(self, _provider_id):
            return Entry()

        def resolve_api_key(self, _provider_id):
            return "test-key-not-a-production-secret"

    client = httpx.AsyncClient()
    try:
        with pytest.raises(ValueError, match="zero"):
            ProductProviderAdapter(
                Registry(),
                provider_id="deepseek",
                client=client,
                price_resolver=lambda _provider, _model: (0, 0, "placeholder"),
            )
    finally:
        await client.aclose()
