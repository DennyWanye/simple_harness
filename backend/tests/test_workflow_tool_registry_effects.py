from __future__ import annotations

import json
import time

import pytest

from deskpet.tools import registry as module_registry
from deskpet.tools.registry import ToolRegistry
from deskpet.workflows import EffectKind
from deskpet.workflows.effects import ToolOutcomeState


def _schema(name: str, *, default: str | None = None) -> dict:
    value: dict[str, object] = {"type": "string"}
    if default is not None:
        value["default"] = default
    return {
        "name": name,
        "description": name,
        "parameters": {
            "type": "object",
            "properties": {"value": value},
        },
    }


@pytest.mark.asyncio
async def test_prepare_call_freezes_context_defaults_and_executes_exact_params() -> None:
    registry = ToolRegistry()
    seen: list[dict[str, object]] = []

    def handler(args, task_id):
        seen.append(dict(args))
        return json.dumps({"ok": True, "value": args["value"]})

    registry.register("probe", "test", _schema("probe", default="default"), handler)
    registry.set_session_context("session", {"_project_root": "A"})
    prepared = registry.prepare_call("probe", {}, "session", "stable-1")
    registry.set_session_context("session", {"_project_root": "B"})

    outcome = await registry.execute_prepared(prepared, effect_id="effect-1")

    assert outcome.state is ToolOutcomeState.SUCCESS
    assert seen == [{"_project_root": "A", "value": "default"}]
    with pytest.raises(TypeError):
        prepared.final_params["value"] = "changed"


@pytest.mark.asyncio
async def test_graph_staged_file_legacy_path_result_is_explicit_success() -> None:
    registry = ToolRegistry()
    registry.register(
        "write_file",
        "os",
        _schema("write_file"),
        lambda args, task_id: json.dumps(
            {"path": "C:/workspace/result.txt", "bytes_written": 7}
        ),
        permission_category="write_file",
    )
    prepared = registry.prepare_call(
        "write_file", {"value": "content"}, "session", "stable-write"
    )

    outcome = await registry.execute_prepared(prepared, effect_id="effect-write")

    assert registry.get("write_file").outcome_parser_id == "artifact_envelope_v1"
    assert outcome.state is ToolOutcomeState.SUCCESS
    assert outcome.value["path"] == "C:/workspace/result.txt"


@pytest.mark.asyncio
async def test_policy_drift_blocks_before_handler_execution() -> None:
    registry = ToolRegistry()
    calls = 0

    def handler(args, task_id):
        nonlocal calls
        calls += 1
        return json.dumps({"ok": True})

    registry.register(
        "probe",
        "test",
        _schema("probe"),
        handler,
        permission_policy_version="permission-v1",
        replace_allowed=True,
    )
    prepared = registry.prepare_call("probe", {}, "session", "stable-1")
    registry.register(
        "probe",
        "test",
        _schema("probe"),
        handler,
        permission_policy_version="permission-v2",
        replace_allowed=True,
    )

    outcome = await registry.execute_prepared(prepared, effect_id="effect-1")

    assert outcome.state is ToolOutcomeState.FAILURE
    assert outcome.error["code"] == "prepared_call_stale"
    assert calls == 0


@pytest.mark.asyncio
async def test_schema_drift_and_mismatched_grant_never_reach_handler() -> None:
    registry = ToolRegistry()
    calls = 0

    def handler(args, task_id):
        nonlocal calls
        calls += 1
        return json.dumps({"ok": True})

    registry.register(
        "probe", "test", _schema("probe"), handler, replace_allowed=True
    )
    prepared = registry.prepare_call("probe", {}, "session", "stable-1")
    bad_grant = {
        "effect_id": "effect-1",
        "tool_name": "probe",
        "args_hash": "wrong",
        "permission_policy_version": prepared.permission_policy_version,
        "expires_at": time.time() + 60,
        "session_id": "session",
    }
    denied = await registry.execute_prepared(
        prepared, effect_id="effect-1", authorization=bad_grant
    )
    assert denied.error["code"] == "authorization_args_hash_mismatch"
    assert calls == 0

    changed_schema = _schema("probe")
    changed_schema["description"] = "changed"
    registry.register(
        "probe", "test", changed_schema, handler, replace_allowed=True
    )
    stale = await registry.execute_prepared(prepared, effect_id="effect-1")

    assert stale.error["code"] == "prepared_call_stale"
    assert calls == 0


@pytest.mark.asyncio
async def test_outcome_parser_fail_closed_for_unknown_dynamic_tool() -> None:
    registry = ToolRegistry()
    registry.register(
        "lookup",
        "dynamic",
        _schema("lookup"),
        lambda args, task_id: json.dumps({"value": 1}),
        source="mcp:demo",
    )

    outcome = await registry.execute_tool_outcome(
        "mcp_demo_lookup", {}, "session", "task"
    )

    assert outcome.state is ToolOutcomeState.MALFORMED
    assert "manual reconciliation" in outcome.error["message"]


def test_inventory_is_complete_and_write_classification_is_safe() -> None:
    registry = ToolRegistry()
    noop = lambda args, task_id: json.dumps({"ok": True})
    registry.register(
        "write_file",
        "os",
        _schema("write_file"),
        noop,
        permission_category="write_file",
    )
    registry.register(
        "run_shell",
        "os",
        _schema("run_shell"),
        noop,
        permission_category="shell",
    )
    registry.register("read", "os", _schema("read"), noop)

    inventory = registry.tool_inventory()
    by_name = {item.name: item for item in inventory}

    assert set(by_name) == set(registry.list_tools())
    assert by_name["write_file"].effect_policy.kind is EffectKind.STAGED_FILE
    assert by_name["run_shell"].effect_policy.kind is EffectKind.OPAQUE_MANUAL
    assert by_name["read"].effect_policy.kind is EffectKind.IDEMPOTENT_READ
    assert all(item.outcome_parser_hash for item in inventory)
    assert {item.name for item in registry.graph_write_inventory()} == {
        "run_shell",
        "write_file",
    }


def test_discovered_registry_inventory_has_exactly_one_versioned_row_per_tool() -> None:
    inventory = module_registry.tool_inventory()

    assert len(inventory) == len(module_registry.list_tools())
    assert len({item.name for item in inventory}) == len(inventory)
    assert all(
        item.spec_version
        and item.schema_hash
        and item.outcome_parser_id
        and item.outcome_parser_version
        and item.outcome_parser_hash
        for item in inventory
    )
