from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from types import SimpleNamespace

import pytest

from deskpet.tools import registry as module_registry
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.registry import ToolRegistry, _normalize_with_parser, _schema_hash
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


def test_prepare_call_thaws_nested_provider_mapping_proxies() -> None:
    registry = ToolRegistry()
    registry.register(
        "probe",
        "test",
        {
            "name": "probe",
            "description": "probe",
            "parameters": {
                "type": "object",
                "properties": {
                    "schedule": {
                        "type": "object",
                        "properties": {"kind": {"type": "string"}},
                    }
                },
            },
        },
        lambda args, task_id: json.dumps({"ok": True}),
    )
    raw = MappingProxyType(
        {
            "schedule": MappingProxyType(
                {"kind": "weekly"}
            )
        }
    )

    prepared = registry.prepare_call(
        "probe",
        raw,
        "session",
        "stable-nested",
    )
    restored_snapshot = replace(
        prepared,
        catalog_snapshot_ref="a" * 64,
    )

    assert prepared.final_params["schedule"]["kind"] == "weekly"
    assert restored_snapshot.final_params["schedule"]["kind"] == "weekly"


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
async def test_graph_staged_file_prepared_path_gets_enabled_artifact_envelope() -> None:
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
    registry.set_tools_config_provider(
        lambda: SimpleNamespace(
            last_mile=SimpleNamespace(artifact_envelope=True)
        )
    )
    prepared = registry.prepare_call(
        "write_file", {"value": "content"}, "session", "stable-write"
    )

    outcome = await registry.execute_prepared(prepared, effect_id="effect-write")

    assert outcome.state is ToolOutcomeState.SUCCESS
    assert outcome.value["ok"] is True
    assert outcome.value["artifacts"][0]["path"] == "C:/workspace/result.txt"
    assert json.loads(outcome.value["result"])["bytes_written"] == 7


def test_prepared_relative_artifact_path_is_resolved_for_ui_actions(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.txt"
    output.write_text("ready", encoding="utf-8")
    registry = ToolRegistry()
    registry.register(
        "file_write",
        "file",
        _schema("file_write"),
        lambda args, task_id: json.dumps({"path": "result.txt", "bytes_written": 5}),
        permission_category="write_file",
    )
    registry.set_tools_config_provider(
        lambda: SimpleNamespace(last_mile=SimpleNamespace(artifact_envelope=True))
    )
    context = ToolExecutionContext(
        scope_id="scope",
        session_id="session",
        request_id="request",
        root_run_id="root-run",
        turn_id="turn",
        workspace=str(tmp_path),
        capability_hash="capability-hash",
        scope_hash="scope-hash",
        run_id="run",
        call_id="stable-write-relative",
        effect_id="effect-write-relative",
        trace_id="trace",
    )

    envelope = registry._prepared_artifact_envelope(
        registry.get("file_write"),
        json.dumps({"path": "result.txt", "bytes_written": 5}),
        context,
    )

    assert envelope["artifacts"][0]["path"] == str(output)
    assert json.loads(envelope["result"])["path"] == "result.txt"


def test_shell_timeout_envelope_is_a_structured_failure() -> None:
    outcome = _normalize_with_parser(
        "shell_exit_v1",
        json.dumps(
            {
                "ok": False,
                "error": "timeout",
                "hint": "command timed out",
                "timeout_s": 30,
            }
        ),
    )

    assert outcome.state is ToolOutcomeState.FAILURE
    assert outcome.error["code"] == "timeout"
    assert outcome.value["timeout_s"] == 30


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

    prepared = registry.prepare_call("mcp_demo_lookup", {}, "session", "task")
    outcome = await registry.execute_prepared(prepared, effect_id="effect-lookup")

    assert outcome.state is ToolOutcomeState.MALFORMED
    assert "manual reconciliation" in outcome.error["message"]


@pytest.mark.asyncio
async def test_builtin_opaque_effect_parses_concrete_json_result() -> None:
    registry = ToolRegistry()
    registry.register(
        "desktop_action",
        "builtin",
        _schema("desktop_action"),
        lambda args, task_id: json.dumps(
            {"ok": True, "process": {"pid": 42, "lease_id": "lease-1"}}
        ),
        permission_category="shell",
    )

    spec = registry.get("desktop_action")
    assert spec.outcome_parser_id == "json_error_envelope_v1"
    assert spec.effect_policy.kind is EffectKind.OPAQUE_MANUAL

    prepared = registry.prepare_call(
        "desktop_action", {}, "session", "task"
    )
    grant = {
        "effect_id": "effect-desktop",
        "tool_name": prepared.tool_name,
        "args_hash": prepared.args_hash,
        "permission_policy_version": prepared.permission_policy_version,
        "expires_at": time.time() + 60,
    }
    outcome = await registry.execute_prepared(
        prepared,
        effect_id="effect-desktop",
        authorization=grant,
    )

    assert outcome.state is ToolOutcomeState.SUCCESS
    assert outcome.value["process"]["lease_id"] == "lease-1"


@pytest.mark.asyncio
async def test_builtin_opaque_effect_parses_explicit_json_failure() -> None:
    registry = ToolRegistry()
    registry.register(
        "desktop_action",
        "builtin",
        _schema("desktop_action"),
        lambda args, task_id: json.dumps(
            {"ok": False, "error": {"code": "launch_failed", "message": "boom"}}
        ),
        permission_category="shell",
    )

    prepared = registry.prepare_call(
        "desktop_action", {}, "session", "task"
    )
    grant = {
        "effect_id": "effect-desktop-failure",
        "tool_name": prepared.tool_name,
        "args_hash": prepared.args_hash,
        "permission_policy_version": prepared.permission_policy_version,
        "expires_at": time.time() + 60,
    }
    outcome = await registry.execute_prepared(
        prepared,
        effect_id="effect-desktop-failure",
        authorization=grant,
    )

    assert outcome.state is ToolOutcomeState.FAILURE
    assert outcome.error["message"] == "boom"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_state"),
    [
        ({}, ToolOutcomeState.MALFORMED),
        ({"ok": None}, ToolOutcomeState.MALFORMED),
        ({"success": False}, ToolOutcomeState.FAILURE),
        ({"cancelled": True}, ToolOutcomeState.FAILURE),
        ({"timeout": True}, ToolOutcomeState.FAILURE),
        ({"status": "FAILED"}, ToolOutcomeState.FAILURE),
        ({"status": "Timed-Out"}, ToolOutcomeState.FAILURE),
        ({"status": "pending"}, ToolOutcomeState.MALFORMED),
        ({"state": "unknown"}, ToolOutcomeState.FAILURE),
    ],
)
async def test_builtin_json_parser_never_treats_empty_or_interrupted_as_success(
    payload: dict[str, object],
    expected_state: ToolOutcomeState,
) -> None:
    registry = ToolRegistry()
    registry.register(
        "desktop_action",
        "builtin",
        _schema("desktop_action"),
        lambda args, task_id: json.dumps(payload),
        permission_category="shell",
    )
    prepared = registry.prepare_call(
        "desktop_action", {}, "session", "task"
    )
    grant = {
        "effect_id": "effect-interrupted",
        "tool_name": prepared.tool_name,
        "args_hash": prepared.args_hash,
        "permission_policy_version": prepared.permission_policy_version,
        "expires_at": time.time() + 60,
    }

    outcome = await registry.execute_prepared(
        prepared,
        effect_id="effect-interrupted",
        authorization=grant,
    )

    assert outcome.state is expected_state


def test_tool_activation_parser_accepts_only_complete_control_proposal() -> None:
    function_schema = {
        "name": "doc_create",
        "parameters": {"type": "object", "properties": {}},
    }
    proposal = {
        "status": "activation_proposed",
        "__deskpet_control": {
            "kind": "tool_activation",
            "base_scope_revision": 3,
            "nonce": "nonce-1",
            "capability_id": "builtin:doc_create",
            "schema_hash": _schema_hash(function_schema),
            "schema": {
                "type": "function",
                "function": function_schema,
            },
        },
    }

    accepted = _normalize_with_parser("activation_proposed_v1", proposal)
    assert accepted.state is ToolOutcomeState.SUCCESS

    for malformed in (
        {"status": "activation_proposed"},
        {
            "status": "activation_proposed",
            "__deskpet_control": {"kind": "tool_activation"},
        },
        {
            **proposal,
            "__deskpet_control": {
                **proposal["__deskpet_control"],
                "schema": None,
            },
        },
        {
            **proposal,
            "__deskpet_control": {
                **proposal["__deskpet_control"],
                "schema_hash": "a" * 64,
            },
        },
        {"status": "activated", "__deskpet_control": proposal["__deskpet_control"]},
    ):
        outcome = _normalize_with_parser("activation_proposed_v1", malformed)
        assert outcome.state is ToolOutcomeState.MALFORMED

    failed = _normalize_with_parser(
        "activation_proposed_v1",
        {"error": "capability_denied", "retriable": False},
    )
    assert failed.state is ToolOutcomeState.FAILURE


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
