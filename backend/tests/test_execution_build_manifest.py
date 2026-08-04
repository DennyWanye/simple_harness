from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from deskpet.tools.build_identity import (
    EffectClass,
    IdempotencyClass,
    ManifestValidationError,
    authority_handler_ids,
    core_authority_for_tool,
    load_core_handler_authorities,
    validate_core_registry_handler_set,
)
from deskpet.tools.registry import ToolSpec, tool_spec_fingerprint


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "backend" / "deskpet" / "tools"
GENERATOR = ROOT / "scripts" / "generate_execution_build_manifest.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GENERATOR), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_checked_manifest_is_canonical_and_current() -> None:
    result = _run("--check")
    assert result.returncode == 0, result.stderr
    manifest = TOOLS.joinpath("execution_build_manifest.json").read_bytes()
    assert manifest.endswith(b"\n")
    assert b"\r\n" not in manifest


def test_source_effect_build_handler_sets_are_strictly_equal() -> None:
    sources = json.loads(
        TOOLS.joinpath("execution_build_sources.json").read_text(encoding="utf-8")
    )
    effects = json.loads(
        TOOLS.joinpath("tool_effect_policy_manifest.json").read_text(encoding="utf-8")
    )
    build = json.loads(
        TOOLS.joinpath("execution_build_manifest.json").read_text(encoding="utf-8")
    )
    sets = [
        {item["handler_id"] for item in manifest["handlers"]}
        for manifest in (sources, effects, build)
    ]
    assert sets[0] == sets[1] == sets[2]
    authorities = load_core_handler_authorities()
    assert sets[0] == {item.handler_id for item in authorities}
    assert len(authorities) == len(sets[0])

    reminder_v2 = {
        "core.reminder_create.v2",
        "core.reminder_list.v2",
        "core.reminder_cancel.v2",
    }
    legacy = authority_handler_ids(phase="legacy", include_planned=True)
    companion = authority_handler_ids(phase="companion", include_planned=True)
    assert "legacy.list_reminders.v1" in legacy
    assert reminder_v2.isdisjoint(legacy)
    assert "legacy.list_reminders.v1" not in companion
    assert reminder_v2.issubset(companion)


def test_phase_registry_validation_rejects_build_or_handler_drift() -> None:
    authorities = [
        item
        for item in load_core_handler_authorities()
        if not item.planned and item.authority_phase in {"both", "legacy"}
    ]
    specs = [
        ToolSpec(
            name=item.tool_name,
            toolset="fixture",
            schema={"name": item.tool_name, "parameters": {"type": "object"}},
            handler=lambda _args: "{}",
            source="builtin",
            stable_handler_id=item.handler_id,
            effect_class=item.effect.effect_class,
            idempotency=item.effect.idempotency,
            target_normalizer_version=item.effect.target_normalizer_version,
            execution_build_identity=item.build,
        )
        for item in authorities
    ]
    validate_core_registry_handler_set(
        specs,
        phase="legacy",
        include_planned=False,
    )
    specs[0] = replace(
        specs[0],
        execution_build_identity=replace(
            specs[0].execution_build_identity,
            build_digest="f" * 64,
        ),
    )
    with pytest.raises(ManifestValidationError, match="metadata mismatch"):
        validate_core_registry_handler_set(
            specs,
            phase="legacy",
            include_planned=False,
        )


def test_memory_recall_is_active_authority_and_not_import_time_callable() -> None:
    authority = core_authority_for_tool("memory_recall")
    assert authority is not None
    assert authority.handler_id == "core.memory_recall.v1"
    assert authority.planned is False
    assert authority.lifecycle == "active"
    assert authority.effect.effect_class is EffectClass.READ_ONLY
    assert authority.effect.idempotency is IdempotencyClass.IDEMPOTENT

    from deskpet.tools import registry

    assert registry.get("memory_recall") is None


def test_production_builtin_receives_checked_metadata() -> None:
    from deskpet.tools import registry

    spec = registry.get("web_fetch")
    assert spec is not None
    assert spec.stable_handler_id == "core.web_fetch.v1"
    assert spec.effect_class is EffectClass.READ_ONLY
    assert spec.execution_build_identity is not None
    assert spec.execution_build_identity.handler_id == spec.stable_handler_id
    assert len(spec.dispatch_adapter_fingerprint) == 64


def test_runtime_bound_general_tools_keep_checked_metadata() -> None:
    from types import SimpleNamespace

    from deskpet.tools.code_tools import register_code_tools
    from deskpet.tools.code_tools.clarify_tool import (
        _SCHEMA as clarification_schema,
        durable_clarification_boundary_only,
    )
    from deskpet.tools.code_tools.spawn_subagents_tool import (
        build_await_subagents_tool,
        product_delegation_tool_catalog,
    )
    from deskpet.tools.code_tools.todo_write_tool import (
        build_todo_write_tool,
    )
    from deskpet.tools.registry import ToolRegistry

    registry = ToolRegistry()
    delegates = product_delegation_tool_catalog()
    todo_handler, todo_schema = build_todo_write_tool(
        SimpleNamespace(),
        session_id_resolver=lambda: "session-1",
    )
    await_handler, await_schema = build_await_subagents_tool(
        lambda: SimpleNamespace(execution_uow=None)
    )
    register_code_tools(
        registry,
        todo_write_handler=todo_handler,
        todo_write_schema=todo_schema,
        agent_handler=delegates["agent"][0],
        agent_schema=delegates["agent"][1],
        agent_parallel_handler=delegates["agent_parallel"][0],
        agent_parallel_schema=delegates["agent_parallel"][1],
        spawn_team_handler=delegates["spawn_team"][0],
        spawn_team_schema=delegates["spawn_team"][1],
        spawn_subagents_handler=delegates["spawn_subagents"][0],
        spawn_subagents_schema=delegates["spawn_subagents"][1],
        await_subagents_handler=await_handler,
        await_subagents_schema=await_schema,
    )
    registry.register(
        "ask_clarification",
        "code",
        clarification_schema,
        durable_clarification_boundary_only,
    )

    expected = {
        "agent",
        "agent_parallel",
        "ask_clarification",
        "await_subagents",
        "fetch_tool_result",
        "glob",
        "grep",
        "spawn_subagents",
        "spawn_team",
        "todo_write",
        "web_search",
    }
    assert expected.issubset(set(registry.list_tools()))
    for name in expected:
        spec = registry.get(name)
        assert spec is not None
        assert spec.stable_handler_id == f"core.{name}.v1"
        assert spec.execution_build_identity is not None
        assert spec.execution_build_identity.handler_id == spec.stable_handler_id


def test_generator_detects_stale_output_and_rejects_escape(
    tmp_path: Path,
) -> None:
    sources = json.loads(
        TOOLS.joinpath("execution_build_sources.json").read_text(encoding="utf-8")
    )
    copied_sources = tmp_path / "sources.json"
    copied_effects = tmp_path / "effects.json"
    output = tmp_path / "manifest.json"
    copied_sources.write_text(json.dumps(sources), encoding="utf-8")
    copied_effects.write_bytes(
        TOOLS.joinpath("tool_effect_policy_manifest.json").read_bytes()
    )
    written = _run(
        "--write",
        "--sources",
        str(copied_sources),
        "--effects",
        str(copied_effects),
        "--output",
        str(output),
    )
    assert written.returncode == 0, written.stderr
    output.write_bytes(output.read_bytes() + b" ")
    stale = _run(
        "--check",
        "--sources",
        str(copied_sources),
        "--effects",
        str(copied_effects),
        "--output",
        str(output),
    )
    assert stale.returncode == 1

    sources["handlers"][0]["artifacts"] = ["../outside.py"]
    copied_sources.write_text(json.dumps(sources), encoding="utf-8")
    escaped = _run(
        "--write",
        "--sources",
        str(copied_sources),
        "--effects",
        str(copied_effects),
        "--output",
        str(output),
    )
    assert escaped.returncode == 2
    assert "outside repo" in escaped.stderr or "escapes repo" in escaped.stderr


def test_tool_spec_fingerprint_covers_effect_build_and_dispatch_identity() -> None:
    authority = core_authority_for_tool("web_fetch")
    assert authority is not None
    base = ToolSpec(
        name="web_fetch",
        toolset="web",
        schema={
            "name": "web_fetch",
            "description": "fetch",
            "parameters": {"type": "object"},
        },
        handler=lambda _args, _task_id: "{}",
        stable_handler_id=authority.handler_id,
        effect_class=authority.effect.effect_class,
        idempotency=authority.effect.idempotency,
        target_normalizer_version=authority.effect.target_normalizer_version,
        execution_build_identity=authority.build,
        dispatch_adapter_id="builtin.function",
        dispatch_adapter_version="v1",
        dispatch_adapter_fingerprint="a" * 64,
    )
    changed_effect = replace(base, effect_class=EffectClass.EXTERNAL_SEND)
    changed_dispatch = replace(base, dispatch_adapter_fingerprint="b" * 64)
    changed_build = replace(
        base,
        execution_build_identity=replace(
            authority.build, build_digest="c" * 64
        ),
    )
    assert len(
        {
            tool_spec_fingerprint(base),
            tool_spec_fingerprint(changed_effect),
            tool_spec_fingerprint(changed_dispatch),
            tool_spec_fingerprint(changed_build),
        }
    ) == 4


def test_known_name_with_unhashed_callable_is_not_blessed() -> None:
    from deskpet.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(
        "web_fetch",
        "web",
        {
            "name": "web_fetch",
            "description": "fake",
            "parameters": {"type": "object"},
        },
        lambda _args, _task_id: "{}",
    )
    spec = registry.get("web_fetch")
    assert spec is not None
    assert spec.stable_handler_id == ""
    assert spec.execution_build_identity is None
    assert spec.effect_class is EffectClass.UNKNOWN
