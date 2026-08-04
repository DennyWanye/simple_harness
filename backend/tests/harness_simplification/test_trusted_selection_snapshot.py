from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from deskpet.execution.contracts import (
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunRecord,
    RunStatus,
    canonical_json,
    fingerprint_json,
)
from deskpet.harness.contracts import (
    HostContext,
    HostExtensionRefV1,
    PreparedRunContextV1,
    RunRequest,
)
from deskpet.harness.context import HostContextFactory
from deskpet.harness.drivers.react import (
    AgentLoopCollaborator,
    ReActDriver,
    ReactCommandBoundary,
)
from deskpet.harness.admission_launch import build_driver_start
from deskpet.harness.ports import DriverStart
from deskpet.harness.ports import ExecuteTools
from deskpet.harness.start_snapshot import build_run_start_snapshot
from deskpet.harness.tool_executor import EffectBatchExecutor
from deskpet.harness.adapters.venues import (
    _enrich_companion_selection_exact_tools,
)
from deskpet.harness.skill_scope import (
    skill_tool_intersection_from_snapshot,
)
from deskpet.capabilities.refresh import context_os_snapshot_ref
from deskpet.tools.capabilities import (
    PreparedToolCapability,
    PreparedToolSet,
    ToolCapabilityRef,
    ToolEligibilityContext,
    ToolExecutionContext,
    canonical_hash,
)
from deskpet.tools.prepared_snapshot import dump_context_os_snapshot
from deskpet.workflows.effects import (
    NormalizedToolOutcome,
    PreparedToolCall,
)


def _context_os() -> tuple[dict, str]:
    capabilities = []
    for name in ("memory_recall", "send_email"):
        schema = {"name": name}
        capabilities.append(
            PreparedToolCapability(
                ToolCapabilityRef(
                    capability_id=f"tool:{name}",
                    name=name,
                    toolset="fixture",
                    source="fixture",
                    description=name,
                    schema_hash=canonical_hash(schema),
                ),
                schema,
            )
        )
    prepared = PreparedToolSet.create(
        scope_id="context-scope-1",
        revision=1,
        registry_revision=1,
        direct=capabilities,
        deferred=(),
        activated=(),
        denied_names=(),
        policy_fingerprint="policy-1",
        decisions=(),
    )
    eligibility = ToolEligibilityContext(
        session_id="session-1",
        request_id="request-1",
        task_type="general",
    )
    return (
        dump_context_os_snapshot(prepared, eligibility),
        context_os_snapshot_ref(prepared, eligibility),
    )


def _context_os_with_token_like_schema() -> tuple[dict, str]:
    schema = {
        "type": "function",
        "function": {
            "name": "workflow_spawn",
            "description": (
                "A model-spawnable key from this exact catalog generation."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    }
    prepared = PreparedToolSet.create(
        scope_id="context-scope-redaction",
        revision=1,
        registry_revision=1,
        direct=(
            PreparedToolCapability(
                ToolCapabilityRef(
                    capability_id="tool:workflow_spawn",
                    name="workflow_spawn",
                    toolset="fixture",
                    source="fixture",
                    description="workflow_spawn",
                    schema_hash=canonical_hash(schema["function"]),
                ),
                schema,
            ),
        ),
        deferred=(),
        activated=(),
        denied_names=(),
        policy_fingerprint="policy-redaction",
        decisions=(),
    )
    eligibility = ToolEligibilityContext(
        session_id="session-1",
        request_id="request-1",
        task_type="general",
    )
    return (
        dump_context_os_snapshot(prepared, eligibility),
        context_os_snapshot_ref(prepared, eligibility),
    )


def _exact_tool(name: str) -> dict:
    schema = {"name": name}
    return {
        "name": name,
        "stable_handler_id": f"fixture.{name}",
        "tool_spec_fingerprint": (
            "4" * 64 if name == "memory_recall" else "5" * 64
        ),
        "schema_hash": canonical_hash(schema),
        "execution_build_identity": {
            "kind": "fixture",
            "fingerprint": "6" * 64,
        },
        "dispatch_adapter_id": "fixture.adapter",
        "dispatch_adapter_version": "v1",
        "dispatch_adapter_fingerprint": "7" * 64,
        "effect_policy": {
            "policy_id": "fixture.read",
            "version": "v1",
            "kind": "read_only",
            "max_attempts": 1,
            "reusable_across_branches": True,
        },
        "idempotency": "idempotent",
    }


def _selection() -> dict:
    return {
        "companion_snapshot_ref": "growth-snapshot-1",
        "owner_key": "companion:alice:1",
        "selected_instruction_refs": [
            {
                "pack_id": "skill-summarize-day",
                "version": "1.0.0",
                "manifest_hash": "manifest-1",
            }
        ],
        "skill_invocation_scopes": [
            {
                "scope_id": "skill-scope:" + "1" * 64,
                "scope_hash": "1" * 64,
                "owner_key": "companion:alice:1",
                "pack_id": "skill-summarize-day",
                "skill_id": "summarize-day",
                "version": "1.0.0",
                "manifest_hash": "2" * 64,
                "content_hash": "3" * 64,
                "allowed_tools": ["memory_recall"],
                "allowed_tool_refs": [_exact_tool("memory_recall")],
            }
        ],
        "active_skill_scope_ids": ["skill-scope:" + "1" * 64],
        "personal_workflow_selection": None,
    }


def _prepared() -> PreparedRunContextV1:
    _, prepared_ref = _context_os()
    selection = _selection()
    selection_hash = fingerprint_json(selection)
    selection_kind = "deskpet.companion.selection.v1"
    snapshot = {
        "run_catalog_content_stamp": "run-catalog-stamp-1",
        "process_catalog_stamp": "process-catalog-stamp-1",
        "catalog_snapshot_ref": "a" * 64,
        "capability_lease_intent_ref": "lease-intent-1",
        "prepared_tool_set_ref": prepared_ref,
        "capability_hash": "c" * 64,
        "product_snapshot_ref": "growth-snapshot-1",
        "host_extensions": {selection_kind: selection},
    }
    return PreparedRunContextV1(
        persistence_required=True,
        prepared_tool_ref=prepared_ref,
        prepared_tool_hash=prepared_ref,
        product_snapshot_ref="growth-snapshot-1",
        product_snapshot_hash="d" * 64,
        capability_lease_intent_ref="lease-intent-1",
        capability_lease_intent_hash="e" * 64,
        owner_key="companion:alice:1",
        profile_generation=1,
        binding_epoch=2,
        prepared_tool_names=("memory_recall", "send_email"),
        capability_snapshot=snapshot,
        host_extensions={
            selection_kind: HostExtensionRefV1(
                selection_kind,
                f"companion-selection:{selection_hash}",
                selection_hash,
            )
        },
        host_extension_payloads={selection_kind: selection},
    )


def _run_context() -> RunContext:
    return RunContext(
        session_id="session-1",
        root_run_id="run-1",
        parent_run_id=None,
        request_id="request-1",
        turn_id="turn-1",
        venue="text",
        workspace={"scope_hash": "scope-1"},
        capability_hash="c" * 64,
        provider_plan={"providers": ["fixture"]},
        trace_id="trace-1",
        principal_id="principal-1",
        auth_epoch=1,
        owner_key="companion:alice:1",
        profile_generation=1,
        binding_epoch=2,
    )


def _spec() -> RunCreate:
    return RunCreate(
        run_id="run-1",
        idempotency_key="root:idempotency-1",
        context=_run_context(),
        payload_fingerprint="f" * 64,
        capability_fingerprint="c" * 64,
        driver_kind="react",
        profile_key="agent.general",
        persistence_level=PersistenceLevel.DURABLE,
        status=RunStatus.CREATED,
    )


def _host() -> HostContext:
    return HostContext(
        session_id="session-1",
        principal_id="principal-1",
        auth_epoch=1,
        capability_hash="c" * 64,
        available_capabilities=frozenset({"malicious_extra_tool"}),
        provider_plan=("fixture",),
        trace_id="trace-1",
    )


def test_run_start_uses_only_host_prepared_capability_selection() -> None:
    prepared = _prepared()
    malicious = {
        "execution_capabilities": ["send_email", "delete_everything"],
        "owner_key": "companion:mallory:99",
        "version": "999",
        "graph": {"nodes": [{"type": "shell"}]},
        "host_extensions": {
            "deskpet.companion.selection.v1": {
                "active_skill_scope_ids": ["forged"]
            }
        },
        "capability_snapshot": {"capabilities": ["delete_everything"]},
    }
    snapshot = build_run_start_snapshot(
        spec=_spec(),
        request=RunRequest(
            text="summarize my day",
            request_id="request-1",
            turn_id="turn-1",
            payload=malicious,
        ),
        host=_host(),
        prepared=prepared,
        created_at=1.0,
    )

    frozen = json.loads(snapshot.capability_snapshot_json)
    assert frozen == dict(prepared.capability_snapshot)
    assert frozen["host_extensions"][
        "deskpet.companion.selection.v1"
    ]["owner_key"] == "companion:alice:1"
    assert "delete_everything" not in json.dumps(frozen)


def test_venue_enriches_skill_scope_only_from_single_capture() -> None:
    selection = _selection()
    selection["skill_invocation_scopes"][0].pop("allowed_tool_refs")
    enriched = _enrich_companion_selection_exact_tools(
        selection,
        (_exact_tool("memory_recall"),),
    )
    assert enriched["skill_invocation_scopes"][0][
        "allowed_tool_refs"
    ] == [_exact_tool("memory_recall")]
    narrowed = _enrich_companion_selection_exact_tools(selection, ())
    assert narrowed["skill_invocation_scopes"][0]["allowed_tools"] == []
    assert narrowed["skill_invocation_scopes"][0]["allowed_tool_refs"] == []


def test_driver_rebuilds_full_snapshot_from_durable_start_without_memory() -> None:
    prepared = _prepared()
    request = RunRequest(
        text="summarize my day",
        request_id="request-1",
        turn_id="turn-1",
        payload={"capability_snapshot": {"capabilities": ["forged"]}},
    )
    snapshot = build_run_start_snapshot(
        spec=_spec(),
        request=request,
        host=_host(),
        prepared=prepared,
        created_at=1.0,
    )

    start = build_driver_start(
        _spec(),
        text=request.text,
        payload=request.payload,
        capabilities=("forged",),
        run_start_snapshot=snapshot,
        prepared_run_context=None,
    )

    assert dict(start.capability_snapshot) == dict(
        prepared.capability_snapshot
    )

    corrupt = replace(
        snapshot,
        capability_snapshot_json=json.dumps(
            {"capability_hash": "c" * 64},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        build_driver_start(
            _spec(),
            text=request.text,
            payload=request.payload,
            run_start_snapshot=corrupt,
        )


def test_start_snapshot_preserves_hash_covered_context_os_during_redaction() -> None:
    context_os, prepared_ref = _context_os_with_token_like_schema()
    base_prepared = _prepared()
    capability_snapshot = dict(base_prepared.capability_snapshot)
    capability_snapshot["prepared_tool_set_ref"] = prepared_ref
    prepared = replace(
        base_prepared,
        prepared_tool_ref=prepared_ref,
        prepared_tool_hash=prepared_ref,
        prepared_tool_names=("workflow_spawn",),
        capability_snapshot=capability_snapshot,
    )
    snapshot = build_run_start_snapshot(
        spec=_spec(),
        request=RunRequest(
            text="summarize my day with sk-abcdefghijklmnop",
            request_id="request-1",
            turn_id="turn-1",
            payload={
                "api_key": "secret-value",
                "context_os": context_os,
            },
        ),
        host=_host(),
        prepared=prepared,
        created_at=1.0,
    )

    stored = json.loads(snapshot.sanitized_request_json)
    assert stored["payload"]["api_key"] == "[REDACTED]"
    assert "sk-abcdefghijklmnop" not in stored["text"]
    assert stored["payload"]["context_os"] == context_os
    assert (
        "model-spawnable key from this exact catalog generation"
        in json.dumps(context_os)
    )


def test_active_skill_scope_intersects_base_tools_and_ignores_payload() -> None:
    context_os, _ = _context_os()
    snapshot = dict(_prepared().capability_snapshot)
    start = DriverStart(
        run_id="run-1",
        session_id="session-1",
        canonical_messages=({"role": "user", "content": "summarize"},),
        capability_snapshot=snapshot,
        request_payload={
            "context_os": context_os,
            "execution_capabilities": [
                "memory_recall",
                "send_email",
                "delete_everything",
            ]
        },
        run_context=_run_context(),
        run_spec=_spec(),
    )

    assert AgentLoopCollaborator._allowed_tool_names(start) == (
        "memory_recall",
    )


def test_boundary_and_tool_context_share_catalog_snapshot_ref() -> None:
    prepared = _prepared()
    context_os, _ = _context_os()
    intersection = skill_tool_intersection_from_snapshot(
        prepared.capability_snapshot,
        {"context_os": context_os},
    )
    assert intersection is not None
    boundary = ReactCommandBoundary(
        run_id="run-1",
        session_id="session-1",
        command_id="root-start:run-1",
        command_kind="provider_start",
        canonical_messages=({"role": "user", "content": "summarize"},),
        session_projection_cursor=0,
        prepared_context_ref=None,
        tool_set_snapshot_ref=prepared.prepared_tool_ref,
        pending_calls=(),
        tool_contexts=(),
        outcomes=(),
        provider_state={},
        iteration=0,
        completion_state={},
        capability_snapshot=prepared.capability_snapshot,
        request_payload={"context_os": context_os},
        run_context=_run_context(),
        run_spec=_spec(),
    )
    context = HostContextFactory().create_tool_context(
        _run_context(),
        run_id="run-1",
        command_id="command-1",
        call_id="call-1",
        effect_id="effect-1",
        capability_snapshot_ref=str(boundary.capability_snapshot_ref),
        active_skill_scope_ids=boundary.active_skill_scope_ids,
        effective_skill_tool_ref_hashes=(
            intersection.effective_tool_ref_hashes
        ),
        effective_skill_tool_refs_hash=(
            boundary.effective_skill_tool_refs_hash or ""
        ),
    )

    assert boundary.capability_snapshot_ref == "a" * 64
    assert boundary.active_skill_scope_ids == (
        "skill-scope:" + "1" * 64,
    )
    assert boundary.effective_skill_tool_refs_hash
    assert context.capability_snapshot_ref == boundary.capability_snapshot_ref


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forgery",
    ("tool_spec_fingerprint", "schema_hash", "build_identity", "effect_policy"),
)
async def test_executor_rejects_forged_exact_tool_before_claim(
    forgery: str,
) -> None:
    prepared = _prepared()
    context_os, _ = _context_os()
    snapshot = build_run_start_snapshot(
        spec=_spec(),
        request=RunRequest(
            text="summarize my day",
            request_id="request-1",
            turn_id="turn-1",
            payload={"context_os": context_os},
        ),
        host=_host(),
        prepared=prepared,
        created_at=1.0,
    )
    intersection = skill_tool_intersection_from_snapshot(
        prepared.capability_snapshot,
        {"context_os": context_os},
    )
    assert intersection is not None
    context = HostContextFactory().create_tool_context(
        _run_context(),
        run_id="run-1",
        command_id="command-1",
        call_id="call-1",
        effect_id="effect-1",
        capability_snapshot_ref="a" * 64,
        active_skill_scope_ids=intersection.active_scope_ids,
        effective_skill_tool_ref_hashes=(
            intersection.effective_tool_ref_hashes
        ),
        effective_skill_tool_refs_hash=(
            intersection.effective_tool_refs_hash
        ),
    )
    call = PreparedToolCall.prepare(
        tool_name="memory_recall",
        stable_call_id="call-1",
        final_params={},
        tool_spec_version="v1",
        schema_hash=canonical_hash({"name": "memory_recall"}),
        permission_policy_version="v1",
        effect_type="read_only",
        effect_policy_version="v1",
        tool_spec_fingerprint="4" * 64,
        catalog_snapshot_ref="a" * 64,
    )
    if forgery == "tool_spec_fingerprint":
        call = replace(call, tool_spec_fingerprint="8" * 64)
    elif forgery == "schema_hash":
        call = replace(call, schema_hash="8" * 64)
    elif forgery == "effect_policy":
        call = replace(call, effect_type="opaque_manual")
    else:
        capabilities = json.loads(snapshot.capability_snapshot_json)
        exact = capabilities["host_extensions"][
            "deskpet.companion.selection.v1"
        ]["skill_invocation_scopes"][0]["allowed_tool_refs"][0]
        exact["execution_build_identity"]["fingerprint"] = "8" * 64
        snapshot = replace(
            snapshot,
            capability_snapshot_json=canonical_json(capabilities),
            capability_snapshot_hash=fingerprint_json(capabilities),
        )

    class Uow:
        claim_count = 0

        async def read_run_start_snapshot(self, run_id: str):
            assert run_id == "run-1"
            return snapshot

        async def claim_tool_call(self, *args, **kwargs):
            self.claim_count += 1
            raise AssertionError("claim must not be reached")

    uow = Uow()
    executor = EffectBatchExecutor(uow, object())  # type: ignore[arg-type]
    record = RunRecord(
        spec=_spec(),
        status=RunStatus.RUNNING,
        persistence_level=PersistenceLevel.DURABLE,
        version=1,
        durable_seq=1,
        terminal_event_id=None,
        created_at=1.0,
        updated_at=1.0,
        started_at=1.0,
    )
    command = ExecuteTools(
        "run-1",
        "command-1",
        (call,),
        (context,),
        (0,),
        effectful=(False,),
    )

    with pytest.raises(ValueError):
        await executor.execute(record, _host().actor(), command)
    assert uow.claim_count == 0


@pytest.mark.asyncio
async def test_dynamic_skill_activation_recovers_commit_unknown_atomically(
) -> None:
    prepared = _prepared()
    context_os, _ = _context_os()
    initial = skill_tool_intersection_from_snapshot(
        prepared.capability_snapshot,
        {"context_os": context_os},
    )
    assert initial is not None
    scope_hash = "8" * 64
    dynamic_scope = {
        "owner_key": "companion:alice:1",
        "pack_id": "skill-send-summary",
        "skill_id": "send-summary",
        "version": "1.0.0",
        "manifest_hash": "9" * 64,
        "content_hash": "b" * 64,
        "allowed_tools": ["send_email"],
        "scope_id": "skill-scope:" + scope_hash,
        "scope_hash": scope_hash,
        "allowed_tool_refs": [_exact_tool("send_email")],
    }
    dynamic = skill_tool_intersection_from_snapshot(
        prepared.capability_snapshot,
        {"context_os": context_os},
        active_scope_ids=(dynamic_scope["scope_id"],),
        activated_scopes=(dynamic_scope,),
    )
    assert dynamic is not None
    instruction = "Send the reviewed summary."
    instruction_hash = __import__(
        "hashlib"
    ).sha256(instruction.encode("utf-8")).hexdigest()
    activation_identity = {
        "schema": "skill-scope-activation/v1",
        "run_id": "run-1",
        "scope_id": dynamic_scope["scope_id"],
        "scope_hash": scope_hash,
        "capability_snapshot_ref": "a" * 64,
        "instruction_content_hash": instruction_hash,
        "effective_tool_refs_hash": dynamic.effective_tool_refs_hash,
    }
    activation_id = (
        "skill-scope-activation:"
        + fingerprint_json(activation_identity)
    )
    activation = {
        "activation_id": activation_id,
        "run_id": "run-1",
        "root_run_id": "run-1",
        "scope_id": dynamic_scope["scope_id"],
        "scope_hash": scope_hash,
        "capability_snapshot_ref": "a" * 64,
        "run_catalog_content_stamp": "run-catalog-stamp-1",
        "allowed_tool_names": ["send_email"],
        "allowed_tool_refs": [_exact_tool("send_email")],
        "effective_tool_ref_hashes": sorted(
            dynamic.effective_tool_ref_hashes
        ),
        "effective_tool_refs_hash": dynamic.effective_tool_refs_hash,
        "instruction_content_hash": instruction_hash,
    }
    value = {
        "ok": True,
        **dynamic_scope,
        "instruction": instruction,
        "scope_activation": activation,
    }
    outcome = NormalizedToolOutcome.success(value)
    call = PreparedToolCall.prepare(
        tool_name="skill_invoke",
        stable_call_id="call-skill",
        final_params={"skill_name": "send-summary"},
        tool_spec_version="v1",
        schema_hash="schema",
        permission_policy_version="v1",
        effect_type="idempotent_read",
        catalog_snapshot_ref="a" * 64,
    )
    context = ToolExecutionContext(
        scope_id="context-scope-1",
        session_id="session-1",
        request_id="request-1",
        origin="agent",
        root_run_id="run-1",
        parent_run_id=None,
        turn_id="turn-1",
        venue="text",
        workspace="F:/workspace",
        write_scope_root="F:/workspace",
        capability_hash="c" * 64,
        scope_hash="scope-1",
        provider_plan=("fixture",),
        run_id="run-1",
        command_id="command-1",
        call_id="call-skill",
        effect_id="effect-skill",
        trace_id="trace-1",
        owner_key="companion:alice:1",
        profile_generation=1,
        binding_epoch=2,
        capability_snapshot_ref="a" * 64,
        active_skill_scope_ids=initial.active_scope_ids,
        effective_skill_tool_ref_hashes=(
            initial.effective_tool_ref_hashes
        ),
        effective_skill_tool_refs_hash=(
            initial.effective_tool_refs_hash
        ),
    )
    boundary = ReactCommandBoundary(
        run_id="run-1",
        session_id="session-1",
        command_id="command-1",
        command_kind="execute_tools",
        canonical_messages=(),
        session_projection_cursor=0,
        prepared_context_ref=None,
        tool_set_snapshot_ref=prepared.prepared_tool_ref,
        pending_calls=(call,),
        tool_contexts=(context,),
        outcomes=(outcome,),
        provider_state={},
        iteration=1,
        completion_state={},
        capability_snapshot=prepared.capability_snapshot,
        request_payload={"context_os": context_os},
        run_context=_run_context(),
        run_spec=_spec(),
        version=2,
    )

    class CommitUnknownUow:
        row: dict[str, Any] | None = None
        record: Any = None

        async def commit_skill_scope_activation(
            self,
            run_id: str,
            *,
            expected_continuation_version: int,
            continuation_payload: Mapping[str, Any],
            activation_values: Mapping[str, Any],
        ):
            assert run_id == "run-1"
            assert expected_continuation_version == 2
            values = dict(activation_values)
            self.row = {
                **values,
                "allowed_tool_names_json": canonical_json(
                    values["allowed_tool_names"]
                ),
                "allowed_tool_refs_json": canonical_json(
                    values["allowed_tool_refs"]
                ),
                "effective_tool_ref_hashes_json": canonical_json(
                    values["effective_tool_ref_hashes"]
                ),
            }
            self.record = SimpleNamespace(
                run_id=run_id,
                version=3,
                payload=dict(continuation_payload),
            )
            raise RuntimeError("commit result unknown")

        async def get_skill_scope_activation(self, requested: str):
            assert requested == activation_id
            return self.row

        async def load_continuation(self, run_id: str):
            assert run_id == "run-1"
            return self.record

    uow = CommitUnknownUow()
    driver = ReActDriver(
        SimpleNamespace(), uow, SimpleNamespace()
    )
    prepared_activation = driver._activation_scope_from_outcome(
        boundary, outcome
    )
    assert prepared_activation is not None
    scope, receipt = prepared_activation
    recovered = await driver._commit_skill_scope_activation(
        boundary,
        expected_continuation_version=2,
        scope=scope,
        activation=receipt,
    )

    assert recovered.version == 3
    assert recovered.active_skill_scope_ids == (
        "skill-scope:" + "1" * 64,
        dynamic_scope["scope_id"],
    )
    assert AgentLoopCollaborator._allowed_tool_names(
        recovered.to_start()
    ) == ("memory_recall", "send_email")
