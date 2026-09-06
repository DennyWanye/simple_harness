"""New input API call capture using actual Manager and the existing sidecar."""
import asyncio
import json
import sqlite3
from dataclasses import replace

import pytest

from deskpet.operation_audit.current_inputs import CurrentInputJournal
from tests.memory.test_current_input_consumer import env, setup, manager


@pytest.mark.asyncio
async def test_actual_input_audit_success_rejection_reopen_and_missing_capability(env):
    principal, context, binding = await setup(env)
    sdk = await manager(env)
    journal = CurrentInputJournal(env.path.with_name("operation-audit.db"))
    kwargs = dict(principal=principal, disclosure_context=context, binding=binding)
    try:
        result = await journal.check_current_input_visibility(sdk, **kwargs)
        assert result.invocation_input_allowed
        with pytest.raises(ValueError):
            await journal.check_current_input_visibility(sdk, **kwargs, bindings=())
        with pytest.raises(AttributeError):
            await journal.check_current_input_visibility(object(), **kwargs)
        page = await CurrentInputJournal(journal.path).page(principal=principal)
        assert page["coverage"] == ("check_current_input_visibility",) and not page["all_operations_recorded"]
        rows = {r["state"]: r for r in page["items"]}
        assert rows["returned"]["observation_status"] == rows["raised"]["observation_status"] == "captured_bound"
        assert rows["capability_missing"]["observation_status"] == "not_invoked"
        assert json.loads(rows["returned"]["observation_json"])["persistence_status"] == "host_persistence_unverified"
        assert binding.evidence.envelope.sanitized_payload["text"] not in str(page)
        with sqlite3.connect(journal.path) as db:
            findings = db.execute("SELECT operation_ref,owner_component FROM memory_call_findings").fetchall()
        assert findings == [(rows["raised"]["attempt_ref"], "memory_sdk")]
    finally:
        await sdk.close()


@pytest.mark.asyncio
async def test_actual_task_cancel_is_captured_and_writer_joined(env):
    principal, context, binding = await setup(env)
    entered = asyncio.Event()
    class SlowAuthority:
        async def resolve_current_input(self, **kwargs):
            entered.set()
            await asyncio.Event().wait()
    sdk = await manager(env, SlowAuthority())
    journal = CurrentInputJournal(env.path.with_name("operation-audit.db"))
    try:
        task = asyncio.create_task(journal.check_current_input_visibility(sdk,
            principal=principal, disclosure_context=context, binding=binding))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.done()
        row, = (await journal.page(principal=principal))["items"]
        assert row["state"] == "cancelled" and row["observation_status"] == "captured_bound"
        assert json.loads(row["observation_json"])["outcome"] == "cancelled"
    finally:
        await sdk.close()


@pytest.mark.asyncio
async def test_input_audit_settlement_failure_preserves_actual_result_and_pending(env):
    principal, context, binding = await setup(env)
    sdk = await manager(env)
    def fault(point):
        if point == "memory_audit.before_settled":
            raise OSError("private-error-not-persisted")
    journal = CurrentInputJournal(env.path.with_name("operation-audit.db"), fault=fault)
    try:
        result = await journal.check_current_input_visibility(sdk, principal=principal,
            disclosure_context=context, binding=binding)
        assert result.invocation_input_allowed  # audit failure never replays the SDK operation
        assert journal.last_code is not None
        row, = (await CurrentInputJournal(journal.path).page(principal=principal))["items"]
        assert row["state"] == "started" and row["settlement_hash"] is None
        assert "private-error" not in str(row)
    finally:
        await sdk.close()
