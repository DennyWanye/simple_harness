"""Installed public Memory producer with real Host recorder; no SDK tests/private SQL."""

import asyncio
import json
import sqlite3

import pytest
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
from deskpet.operation_audit.memory_attempts import MemoryAttemptJournal


async def real_context(tmp_path, monkeypatch):
    runtime = HumanMemoryV7Runtime(tmp_path / "memory.db")
    manager = await runtime.manager()
    original = manager.execute_typed_recall
    captured = {}

    async def capture(*, principal, context, plan, now, observation_context=None):
        captured.update(principal=principal, context=context, plan=plan, now=now)
        with sqlite3.connect(tmp_path / "operation-audit.db") as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM memory_call_attempts WHERE state='started'"
                ).fetchone()[0]
                == 1
            )
        return await original(
            principal=principal,
            context=context,
            plan=plan,
            now=now,
            observation_context=observation_context,
        )

    monkeypatch.setattr(manager, "execute_typed_recall", capture)
    await runtime.typed_recall(
        query="tea", run_id="actual-context-ref", turn_ordinal=1, now=20.0
    )
    monkeypatch.setattr(manager, "execute_typed_recall", original)
    return runtime, manager, captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protocol,reason",
    [
        (5, "typed_recall_protocol_unsupported"),
        ("5", "typed_recall_protocol_invalid"),
        (True, "typed_recall_protocol_invalid"),
    ],
)
async def test_installed_sdk_rejection_binds_durable_started_exact_attempt(
    tmp_path, monkeypatch, protocol, reason
):
    runtime, manager, kwargs = await real_context(tmp_path, monkeypatch)
    try:
        journal = runtime.operation_audit
        with pytest.raises(Exception) as raised:
            await journal.execute_typed_recall(
                manager, **kwargs, caller="foreground_recall", harness_protocol=protocol
            )
        observation = raised.value.operation_observation
        assert observation.reason == reason
        assert observation.persistence_status == "host_persistence_unverified"
        rows = (await journal.page(principal=runtime.principal()))["items"]
        assert len(rows) == 2
        row = next(row for row in rows if row["state"] == "raised")
        assert row["observation_status"] == "verified"
        assert row["observation_hash"] == observation.observation_hash
        assert json.loads(row["observation_json"]) == observation.to_json()
        assert "host_persistence_unverified" in row["observation_json"]
        assert row["context_run_ref_hash"] and "sdk_run_id" not in row
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["ownership", "narrowing", "idempotency"])
async def test_actual_local_rejection_scopes_keep_original_error_and_safe_carrier(
    tmp_path, monkeypatch, stage
):
    from dataclasses import replace

    runtime, manager, kwargs = await real_context(tmp_path, monkeypatch)
    try:
        if stage == "ownership":
            kwargs["principal"] = replace(kwargs["principal"], actor_id="foreign-actor")
        elif stage == "narrowing":
            kwargs["plan"] = replace(kwargs["plan"], context_hash="e" * 64)
        else:
            kwargs["plan"] = replace(
                kwargs["plan"], plan_id="different-plan-same-idempotency"
            )
        with pytest.raises(Exception) as caught:
            await runtime.operation_audit.execute_typed_recall(
                manager, **kwargs, caller="foreground_recall"
            )
        observation = caught.value.operation_observation
        assert observation.stage == stage
        rows = (await runtime.operation_audit.page(principal=kwargs["principal"]))[
            "items"
        ]
        recorded = next(row for row in rows if row["state"] == "raised")
        assert recorded["observation_status"] == "verified"
        assert json.loads(recorded["observation_json"]) == observation.to_json()
        if stage == "narrowing":
            assert observation.reason == "typed_recall_narrowing_rejected"
            assert str(caught.value) not in recorded["observation_json"]
        if stage == "ownership":
            assert len(rows) == 1
            assert all(
                row["state"] == "returned"
                for row in (
                    await runtime.operation_audit.page(principal=runtime.principal())
                )["items"]
            )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_started_storage_failure_calls_once_without_claiming_durable_observation(
    tmp_path, monkeypatch
):
    runtime, manager, kwargs = await real_context(tmp_path, monkeypatch)
    journal = MemoryAttemptJournal(tmp_path / "unavailable-audit.db")

    class Calls:
        count = 0

        async def execute_typed_recall(
            self, *, principal, context, plan, now, observation_context=None
        ):
            self.count += 1
            assert observation_context is None
            return await manager.execute_typed_recall(
                principal=principal, context=context, plan=plan, now=now
            )

    calls = Calls()

    def unavailable():
        raise OSError("secret location")

    connect = journal._connect
    monkeypatch.setattr(journal, "_connect", unavailable)
    try:
        assert (
            await journal.execute_typed_recall(
                calls, **kwargs, caller="foreground_recall"
            )
        ).result.result_hash
        assert (
            calls.count == 1 and journal.last_code == "memory_audit_storage_unavailable"
        )
        monkeypatch.setattr(journal, "_connect", connect)
        assert (await journal.page(principal=runtime.principal()))["items"] == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_actual_semantic_production_factory_records_candidate_and_foreground_calls(
    tmp_path, monkeypatch
):
    from tests.memory.test_semantic_correction import (
        test_actual_semantic_revise_and_reopen,
    )

    # Run the existing real Host S1 / installed SQLite SDK / deterministic
    # Harness adapter combination once, and inspect only our added sidecar.
    await test_actual_semantic_revise_and_reopen(tmp_path, "valid")
    rows = []
    for path in tmp_path.rglob("operation-audit.db"):
        with sqlite3.connect(path) as db:
            rows.extend(
                db.execute(
                    "SELECT caller,state,result_hash FROM memory_call_attempts"
                ).fetchall()
            )
    assert {row[0] for row in rows} == {"analysis_candidates", "foreground_recall"}
    assert all(row[1] == "returned" and row[2] for row in rows)


@pytest.mark.asyncio
async def test_unwitnessed_foreign_carrier_and_cancel_are_not_query_zero(
    tmp_path, monkeypatch
):
    runtime, manager, kwargs = await real_context(tmp_path, monkeypatch)
    journal = runtime.operation_audit
    try:
        with pytest.raises(Exception) as prior:
            await journal.execute_typed_recall(
                manager, **kwargs, caller="foreground_recall", harness_protocol=5
            )
        borrowed = prior.value.operation_observation
        secret = "Authorization: Bearer sk-test-never-store-me"
        error = RuntimeError(secret)
        error.operation_observation = borrowed

        class Foreign:
            calls = 0

            async def execute_typed_recall(
                self, *, principal, context, plan, now, observation_context
            ):
                self.calls += 1
                raise error

        foreign = Foreign()
        with pytest.raises(RuntimeError) as result:
            await journal.execute_typed_recall(
                foreign, **kwargs, caller="foreground_recall"
            )
        assert result.value is error and foreign.calls == 1
        rows = (await journal.page(principal=runtime.principal()))["items"]
        assert any(
            row["observation_status"] == "unverifiable"
            and row["observation_json"] is None
            for row in rows
        )
        assert secret not in str(rows)

        class Cancel:
            async def execute_typed_recall(self, *, principal, context, plan, now):
                raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await journal.execute_typed_recall(
                Cancel(), **kwargs, caller="foreground_recall"
            )
        reopened = MemoryAttemptJournal(journal.path)
        rows = (await reopened.page(principal=runtime.principal()))["items"]
        assert sum(row["state"] == "started" for row in rows) == 1
        assert all(
            row["observation_json"] is None for row in rows if row["state"] == "started"
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_old_public_signature_called_once_and_recording_fault_cannot_retry(
    tmp_path, monkeypatch
):
    runtime, manager, kwargs = await real_context(tmp_path, monkeypatch)

    class Legacy:
        calls = 0

        async def execute_typed_recall(self, *, principal, context, plan, now):
            self.calls += 1
            return await manager.execute_typed_recall(
                principal=principal, context=context, plan=plan, now=now
            )

    try:
        legacy = Legacy()
        result = await runtime.operation_audit.execute_typed_recall(
            legacy, **kwargs, caller="foreground_recall"
        )
        assert result.result.result_hash and legacy.calls == 1

        def fail(point):
            if point == "memory_audit.before_settled":
                raise OSError("private filename sk-secret")

        broken = MemoryAttemptJournal(runtime.operation_audit.path, fault=fail)
        await broken.execute_typed_recall(legacy, **kwargs, caller="foreground_recall")
        assert (
            legacy.calls == 2
            and broken.last_code == "memory_audit_settlement_unavailable"
        )
        rows = (await broken.page(principal=runtime.principal()))["items"]
        assert any(row["state"] == "started" for row in rows)
        assert any(
            row["observation_status"] == "capability_unavailable" for row in rows
        )
        assert "private filename" not in str(rows)
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["reason", "context", "plan"])
async def test_real_error_with_self_consistent_but_mismatched_carrier_is_unverifiable(
    tmp_path, monkeypatch, damage
):
    from dataclasses import replace

    runtime, manager, kwargs = await real_context(tmp_path, monkeypatch)
    original = manager.execute_typed_recall
    caught = []

    async def altered(
        *, principal, context, plan, now, harness_protocol, observation_context
    ):
        try:
            return await original(
                principal=principal,
                context=context,
                plan=plan,
                now=now,
                harness_protocol=harness_protocol,
                observation_context=observation_context,
            )
        except Exception as error:
            if damage == "reason":
                error.operation_observation = replace(
                    error.operation_observation, reason="typed_recall_protocol_invalid"
                )
            elif damage == "context":
                error.rejection_receipt = replace(
                    error.rejection_receipt, context_hash="a" * 64
                )
            else:
                error.rejection_receipt = replace(
                    error.rejection_receipt, plan_hash="b" * 64
                )
            caught.append(error)
            raise

    monkeypatch.setattr(manager, "execute_typed_recall", altered)
    try:
        with pytest.raises(Exception) as raised:
            await runtime.operation_audit.execute_typed_recall(
                manager, **kwargs, caller="foreground_recall", harness_protocol=5
            )
        assert raised.value is caught[0]
        rows = (await runtime.operation_audit.page(principal=runtime.principal()))[
            "items"
        ]
        row = next(row for row in rows if row["state"] == "raised")
        assert (
            row["observation_status"] == "unverifiable"
            and row["observation_json"] is None
        )
    finally:
        await runtime.close()
