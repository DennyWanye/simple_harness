from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from deskpet.memory.session_db import SessionDB
from deskpet.sdk_adapters.context_authority import (
    DefaultDenySnapshotRedactor,
    PreparedSdkContextSnapshotV1,
    SnapshotContractConflict,
)
from deskpet.sdk_adapters.provider_projection import (
    ProviderProjectionEnvelopeV1,
    SdkProviderSettlementReconciler,
)
from deskpet.sdk_adapters.run_bindings import (
    RunBindingConflict,
    SdkRunBindingRegistry,
    SdkRunBindingV1,
)


def _snapshot() -> PreparedSdkContextSnapshotV1:
    return PreparedSdkContextSnapshotV1.build(
        session_id="session-a",
        request_id="request-a",
        root_run_id="root-a",
        sdk_run_id="sdk-a",
        turn_id="turn-a",
        provider_binding={
            "provider_id": "deepseek",
            "model_id": "deepseek-v4",
            "binding_epoch": 3,
            "api_key": "must-not-leak",
            "reasoning_content": "private-cot",
        },
        provider_messages=(
            {"role": "system", "content": "persona"},
            {"role": "user", "content": "hello"},
        ),
        catalog={
            "generation": 7,
            "content_fingerprint": "catalog-fp",
            "tool_names": ["file_read", "/Users/private/tool"],
        },
        attachments=(
            {
                "kind": "text",
                "size": 12,
                "path": "/Users/private/secret.txt",
                "body": "attachment body",
            },
        ),
        sections={
            "persona": {"count": 1, "estimated_tokens": 4},
            "history": {"count": 2, "estimated_tokens": 8},
        },
        budget={"estimated_prompt_tokens": 20, "context_window": 128_000},
    )


def test_prepared_snapshot_is_canonical_immutable_and_redacted() -> None:
    first = _snapshot()
    second = _snapshot()
    assert first.snapshot_fingerprint == second.snapshot_fingerprint
    assert first.snapshot_id == second.snapshot_id
    assert first.canonical_json() == second.canonical_json()

    with pytest.raises((TypeError, FrozenInstanceError)):
        first.session_id = "changed"  # type: ignore[misc]

    public = DefaultDenySnapshotRedactor().redact(first)
    encoded = json.dumps(public, ensure_ascii=False, sort_keys=True)
    assert public["snapshot_id"] == first.snapshot_id
    assert public["catalog"]["generation"] == 7
    assert public["catalog"]["tool_names"] == ["file_read"]
    assert public["attachments"] == [{"kind": "text", "size": 12}]
    for canary in (
        "must-not-leak",
        "private-cot",
        "/Users/private",
        "attachment body",
        "api_key",
        "reasoning_content",
    ):
        assert canary not in encoded


@pytest.mark.asyncio
async def test_session_db_public_snapshot_is_idempotent_versioned_and_scoped(tmp_path) -> None:
    db = SessionDB(tmp_path / "state.db")
    snapshot = _snapshot()
    public = DefaultDenySnapshotRedactor().redact(snapshot)

    assert await db.put_sdk_context_public_snapshot(public) == "inserted"
    assert await db.put_sdk_context_public_snapshot(public) == "duplicate"
    loaded = await db.get_sdk_context_public_snapshot(snapshot.snapshot_id)
    latest = await db.get_latest_sdk_context_public_snapshot("session-a")
    assert loaded == latest
    assert loaded is not None and loaded["snapshot_fingerprint"] == snapshot.snapshot_fingerprint
    assert await db.get_latest_sdk_context_public_snapshot("session-b") is None

    conflicting = dict(public)
    conflicting["snapshot_fingerprint"] = "different"
    with pytest.raises(SnapshotContractConflict):
        await db.put_sdk_context_public_snapshot(conflicting)
    await db.close()


def _binding(run_id: str = "run-a") -> SdkRunBindingV1:
    return SdkRunBindingV1.build(
        run_id=run_id,
        session_id="session-a",
        request_id=f"request-{run_id}",
        snapshot_id=f"snapshot-{run_id}",
        provider_id="deepseek",
        provider_incarnation_id="inc-1",
        provider_config_revision=4,
        binding_epoch=2,
        model_id="deepseek-v4",
        model_params={"reasoning_mode": "thinking"},
        context_window=128_000,
        catalog_generation=7,
        catalog_fingerprint="catalog-fp",
        budget_fingerprint="budget-fp",
    )


def test_run_binding_registry_retains_waiting_reconstructs_and_releases_terminal() -> None:
    registry = SdkRunBindingRegistry()
    original = registry.register(_binding())
    assert registry.register(_binding()) is original
    with pytest.raises(RunBindingConflict):
        registry.register(_binding("run-a").replace(model_id="other"))

    waiting = registry.mark_waiting("run-a")
    assert waiting.lease_state == "waiting"
    restored = SdkRunBindingRegistry.reconstruct([waiting.to_record()])
    assert restored.resolve("run-a") == waiting
    released = restored.mark_terminal("run-a", "completed")
    assert released == waiting
    assert restored.resolve("run-a") is None


@pytest.mark.asyncio
async def test_provider_projection_audits_all_states_but_measures_only_trusted_success(tmp_path) -> None:
    db = SessionDB(tmp_path / "state.db")
    reconciler = SdkProviderSettlementReconciler(db, consumer_id="sdk-context-v1")
    base = dict(
        settlement_version=1,
        settled_at=100.0,
        session_id="session-a",
        root_run_id="root-a",
        request_id="request-a",
        snapshot_id="snapshot-a",
        provider_id="deepseek",
        model_id="deepseek-v4",
        binding_epoch=2,
        context_window=128_000,
        effective_ceiling=100_000,
    )
    success = ProviderProjectionEnvelopeV1.from_value({
        **base,
        "invocation_id": "inv-success",
        "state": "succeeded",
        "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13, "cache_tokens": 2},
    })
    missing = ProviderProjectionEnvelopeV1.from_value({
        **base, "invocation_id": "inv-missing", "settled_at": 101.0,
        "state": "succeeded", "usage": None,
    })
    failed = ProviderProjectionEnvelopeV1.from_value({
        **base, "invocation_id": "inv-failed", "settled_at": 102.0,
        "state": "failed", "usage": {"input_tokens": 99, "output_tokens": 99, "total_tokens": 198, "cache_tokens": 0},
    })
    unknown = ProviderProjectionEnvelopeV1.from_value({
        **base, "invocation_id": "inv-unknown", "settled_at": 103.0,
        "state": "unknown", "usage": None,
    })

    for envelope in (success, missing, failed, unknown):
        await reconciler.project(envelope)
    await reconciler.project(success)

    attempts = await db.list_sdk_provider_attempts("session-a")
    assert [item["invocation_id"] for item in attempts] == [
        "inv-success", "inv-missing", "inv-failed", "inv-unknown"
    ]
    assert attempts[0]["usage"] == {
        "input_tokens": 10, "output_tokens": 3, "total_tokens": 13,
        "cache_tokens": 2, "reasoning_tokens": None,
    }
    assert attempts[1]["usage"] is None
    assert attempts[2]["state"] == "failed"
    history = await db.list_context_usage_history("session-a")
    assert len(history) == 1
    assert history[0]["source_event_id"] == "sdk-provider-invocation:inv-success"
    cursor = await db.get_sdk_provider_projection_cursor("sdk-context-v1")
    assert cursor is not None and cursor["invocation_id"] == "inv-unknown"
    await db.close()


@pytest.mark.asyncio
async def test_provider_projection_conflict_does_not_advance_cursor(tmp_path) -> None:
    db = SessionDB(tmp_path / "state.db")
    reconciler = SdkProviderSettlementReconciler(db, consumer_id="consumer")
    raw = {
        "invocation_id": "inv-a", "settlement_version": 1, "settled_at": 1.0,
        "session_id": "s", "root_run_id": "r", "request_id": "q",
        "snapshot_id": "snap", "state": "succeeded", "provider_id": "p",
        "model_id": "m", "binding_epoch": 1, "context_window": 100,
        "effective_ceiling": 90,
        "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3, "cache_tokens": 0},
    }
    await reconciler.project(ProviderProjectionEnvelopeV1.from_value(raw))
    with pytest.raises(SnapshotContractConflict):
        await reconciler.project(ProviderProjectionEnvelopeV1.from_value({
            **raw,
            "usage": {"input_tokens": 8, "output_tokens": 1, "total_tokens": 9, "cache_tokens": 0},
        }))
    cursor = await db.get_sdk_provider_projection_cursor("consumer")
    assert cursor is not None and cursor["version"] == 1
    await db.close()
