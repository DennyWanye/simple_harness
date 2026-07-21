from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.execution.contracts import (
    AuthorizationError,
    PersistenceLevel,
    RunContext,
    RunCreate,
    fingerprint_json,
)
from deskpet.harness.adapters import product_composition
from deskpet.harness.adapters.subagent_registry import build_harness_subagent_registry
from deskpet.harness.ports import DriverStart
from deskpet.tools.capabilities import ToolExecutionContext
from scripts.acceptance import legacy_cutover_audit


ROOT = Path(__file__).resolve().parents[3]


def _driver_start() -> DriverStart:
    capability = fingerprint_json({"tools": ["read"]})
    context = RunContext(
        session_id="session-a",
        root_run_id="run-a",
        parent_run_id=None,
        request_id="request-a",
        turn_id="turn-a",
        venue="text",
        workspace={},
        capability_hash=capability,
        provider_plan={},
        trace_id="trace-a",
        principal_id="principal-a",
    )
    spec = RunCreate(
        run_id="run-a",
        idempotency_key="root:session-a:request-a:turn-a",
        context=context,
        payload_fingerprint=fingerprint_json({"text": "delegate"}),
        capability_fingerprint=capability,
        driver_kind="react",
        profile_key="react.default",
        persistence_level=PersistenceLevel.DURABLE,
    )
    return DriverStart(
        run_id="run-a",
        session_id="session-a",
        canonical_messages=({"role": "user", "content": "delegate"},),
        run_context=context,
        run_spec=spec,
        capability_snapshot={"tools": ["read"]},
    )


def _tool_context(session_id: str = "session-a") -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id="scope-a",
        session_id=session_id,
        request_id="request-a",
        root_run_id="run-a",
        capability_hash=_driver_start().run_context.capability_hash,
        run_id="run-a",
    )


@pytest.mark.asyncio
async def test_dormant_subagent_registry_uses_typed_durable_delegates() -> None:
    request = _driver_start()
    submitted = []
    awaited = []

    async def resolve(_context):
        return request

    async def submit(_request, command, context):
        submitted.append((command, context))
        return json.dumps({"ok": True, "run_ids": ["child-a"]})

    async def await_runs(_request, args, context):
        awaited.append((args, context))
        return json.dumps({"ok": True, "results": []})

    registry = await build_harness_subagent_registry(
        parent_request=resolve,
        submit_delegate=submit,
        await_delegate=await_runs,
    )
    spawn, await_tool = registry.tools
    result = await spawn[0](
        {"subagents": [{"prompt": "inspect", "tools": ["read"]}]},
        execution_context=_tool_context(),
    )
    assert json.loads(result)["run_ids"] == ["child-a"]
    assert submitted[0][0].kind == "delegate_run"
    assert json.loads(await await_tool[0]({}, execution_context=_tool_context()))["ok"]
    assert len(awaited) == 1

    with pytest.raises(AuthorizationError):
        await await_tool[0]({}, execution_context=_tool_context("session-b"))
    assert len(awaited) == 1


@pytest.mark.asyncio
async def test_product_composition_builds_only_through_shared_runtime(monkeypatch) -> None:
    calls = []
    runtime = SimpleNamespace(run_client=object())

    async def build_runtime(**kwargs):
        calls.append(kwargs)
        return runtime

    monkeypatch.setattr(product_composition, "build_harness_runtime", build_runtime)
    composition = await product_composition.build_product_harness_composition(
        uow=object(),
        classifier=object(),
        profiles=object(),
        drivers=(),
        resolver=object(),
    )
    assert composition.runtime is runtime
    assert composition.venue._run_client is runtime.run_client
    assert len(calls) == 1


def test_dormant_factories_are_unreachable_and_production_remains_legacy() -> None:
    result = legacy_cutover_audit.audit_dormant_factories(repo=ROOT)
    assert result["passed"] is True
    assert result["production_owner"] == "legacy/0"


def test_cutover_manifest_generation_and_readiness_cover_static_and_dynamic(tmp_path) -> None:
    commit = legacy_cutover_audit._full_commit("HEAD", repo=ROOT)
    dynamic = tmp_path / "dynamic.json"
    dynamic.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "captured_commit": commit,
                "complete": True,
                "stacks": [
                    {
                        "stack_id": "text-legacy",
                        "legacy_frames": [
                            {
                                "path": "backend/main.py",
                                "qualname": "control_channel._run_chat",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = legacy_cutover_audit.generate_manifest(
        source_commit=commit, dynamic_stacks=dynamic, repo=ROOT
    )
    assert manifest["owner_count"] == 15
    assert len(manifest["roots"]) == len(legacy_cutover_audit.ROOT_SPECS)
    readiness = legacy_cutover_audit.audit_readiness(manifest, repo=ROOT)
    assert readiness["passed"] is True

    manifest["dynamic_evidence"]["complete"] = False
    assert legacy_cutover_audit.audit_readiness(manifest, repo=ROOT)["passed"] is False


def test_manifest_generation_fails_closed_without_complete_dynamic_evidence(tmp_path) -> None:
    evidence = tmp_path / "dynamic.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "captured_commit": legacy_cutover_audit._full_commit("HEAD", repo=ROOT),
                "complete": False,
                "stacks": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(legacy_cutover_audit.CutoverAuditError, match="complete=true"):
        legacy_cutover_audit.generate_manifest(
            source_commit="HEAD", dynamic_stacks=evidence, repo=ROOT
        )
