from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest

from deskpet.execution.contracts import OutcomeStatus
from deskpet.harness.context import HostContextFactory
from deskpet.harness.tool_executor import EffectBatchExecutor
from deskpet.tools.registry import (
    PreparedToolCallStale,
    ToolRegistry,
    tool_spec_fingerprint,
)
from deskpet.workflows.effects import NormalizedToolOutcome, ToolOutcomeState


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _schema(name: str, version: str) -> dict:
    return {
        "name": name,
        "description": f"{name} {version}",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
        },
    }


def _context(call, effect_id: str):
    factory = HostContextFactory()
    run = factory.create_run_context(
        session_id="session",
        root_run_id="root",
        request_id="request",
        turn_id="turn",
        venue="text",
        workspace=".",
        write_scope_root=".",
        capability_hash=_hash("capability"),
        scope_hash=_hash("scope"),
        provider_plan=("primary",),
        trace_id="trace",
        principal_id="principal",
    )
    return factory.create_tool_context(
        run,
        run_id="run",
        call_id=call.stable_call_id,
        effect_id=effect_id,
    )


@pytest.mark.asyncio
async def test_retired_spec_is_used_by_every_prepared_execution_path() -> None:
    registry = ToolRegistry()
    running = 0
    max_running = 0
    invoked: list[str] = []

    async def old_handler(args, context):
        nonlocal running, max_running
        del args, context
        running += 1
        max_running = max(max_running, running)
        await asyncio.sleep(0.01)
        running -= 1
        invoked.append("old")
        return json.dumps({"ok": True, "runtime": "old"})

    registry.register(
        "godot__check",
        "capability:godot",
        _schema("godot__check", "v1"),
        lambda _args, _task: "{}",
        context_handler=old_handler,
        source="capability:godot:1.0.0",
        spec_version="v1",
        runtime_provenance_ref=_hash("runtime-v1"),
        concurrency_safe=False,
        completion_semantics="accepted_async",
    )
    before = registry.catalog_snapshot()
    old_spec = before.specs[0]
    old_fingerprint = tool_spec_fingerprint(old_spec)
    snapshot_ref = _hash("snapshot-v1")
    registry.lease_catalog_snapshot(
        snapshot_ref=snapshot_ref,
        run_id="run",
        tool_spec_fingerprints=(old_fingerprint,),
    )
    old_calls = tuple(
        registry.prepare_call(
            "godot__check",
            {"value": index},
            "session",
            f"call-{index}",
            catalog_snapshot_ref=snapshot_ref,
        )
        for index in (1, 2)
    )

    candidate = ToolRegistry()
    candidate.register(
        "godot__check",
        "capability:godot",
        _schema("godot__check", "v2"),
        lambda _args, _task: json.dumps({"ok": True, "runtime": "new"}),
        source="capability:godot:2.0.0",
        spec_version="v2",
        runtime_provenance_ref=_hash("runtime-v2"),
        concurrency_safe=True,
        completion_semantics="sync",
    )
    new_spec = candidate.catalog_snapshot().specs[0]
    registry.compare_and_swap_catalog(
        expected_revision=before.revision,
        expected_fingerprints={"godot__check": old_fingerprint},
        replacements=(new_spec,),
    )
    frozen_context = replace(
        _context(old_calls[0], "effect-frozen"),
        call_id="call-4",
    )
    frozen_after_activation = registry.prepare_frozen_call(
        "godot__check",
        {"value": 4},
        "session",
        "call-4",
        expected_tool_spec_fingerprint=old_fingerprint,
        catalog_snapshot_ref=snapshot_ref,
        execution_context=frozen_context,
    )
    rogue_context = _context(old_calls[0], "effect-rogue")
    rogue_context = replace(
        rogue_context,
        run_id="another-run",
        call_id="call-rogue",
    )
    with pytest.raises(PreparedToolCallStale):
        registry.prepare_frozen_call(
            "godot__check",
            {"value": 5},
            "session",
            "call-rogue",
            expected_tool_spec_fingerprint=old_fingerprint,
            catalog_snapshot_ref=snapshot_ref,
            execution_context=rogue_context,
        )
    new_call = registry.prepare_call(
        "godot__check", {"value": 3}, "session", "call-3"
    )

    assert old_fingerprint in registry.retired_spec_fingerprints()
    assert frozen_after_activation.tool_spec_fingerprint == old_fingerprint
    assert frozen_after_activation.tool_spec_version == "v1"
    resolved_old = registry.resolve_prepared_spec(old_calls[0])
    assert tool_spec_fingerprint(resolved_old) == old_fingerprint
    assert resolved_old.runtime_provenance_ref == old_spec.runtime_provenance_ref
    assert registry.prepared_execution_policy(old_calls[0]) == (False, False)
    assert registry.is_concurrency_safe(old_calls[0]) is False
    assert registry.is_concurrency_safe(new_call) is True
    assert (
        registry.prepared_outcome_status(
            old_calls[0], NormalizedToolOutcome.success({"ok": True})
        )
        is OutcomeStatus.ACCEPTED
    )

    executor = EffectBatchExecutor(None, registry)  # type: ignore[arg-type]
    contexts = tuple(
        _context(call, f"effect-{index}")
        for index, call in enumerate(old_calls, start=1)
    )
    outcomes = await executor._execute_segmented(
        old_calls,
        contexts,
        (None, None),
        (None, None),
    )
    assert all(outcome.state is ToolOutcomeState.SUCCESS for outcome in outcomes)
    assert invoked == ["old", "old"]
    assert max_running == 1

    new_outcome = await registry.execute_prepared(
        new_call, effect_id="effect-new"
    )
    assert new_outcome.state is ToolOutcomeState.SUCCESS
    assert new_outcome.value["runtime"] == "new"

    assert registry.release_catalog_snapshot(
        snapshot_ref=snapshot_ref, run_id="run"
    )
    assert registry.retired_spec_fingerprints() == frozenset()
    with pytest.raises(PreparedToolCallStale):
        registry.prepared_execution_policy(old_calls[0])
    with pytest.raises(PreparedToolCallStale):
        registry.prepared_outcome_status(
            old_calls[0], NormalizedToolOutcome.success({"ok": True})
        )
    with pytest.raises(PreparedToolCallStale):
        registry.is_concurrency_safe(old_calls[0])
    stale = await registry.execute_prepared(
        old_calls[0], effect_id="effect-stale"
    )
    assert stale.state is ToolOutcomeState.FAILURE
    assert stale.error["code"] == "prepared_call_stale"


def test_prepared_call_runtime_and_snapshot_refs_round_trip() -> None:
    registry = ToolRegistry()
    runtime_ref = _hash("runtime")
    snapshot_ref = _hash("snapshot")
    registry.register(
        "godot__check",
        "capability:godot",
        _schema("godot__check", "v1"),
        lambda _args, _task: "{}",
        source="capability:godot:1.0.0",
        runtime_provenance_ref=runtime_ref,
    )
    fingerprint = tool_spec_fingerprint(registry.catalog_snapshot().specs[0])
    prepared = registry.prepare_call(
        "godot__check",
        {"value": 1},
        "session",
        "call",
        catalog_snapshot_ref=snapshot_ref,
    )
    restored = type(prepared).from_dict(prepared.to_dict())
    assert restored == prepared
    assert prepared.tool_spec_fingerprint == fingerprint
    assert prepared.runtime_provenance_ref == runtime_ref
    assert prepared.catalog_snapshot_ref == snapshot_ref
