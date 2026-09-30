# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5a value-milestone lane: five-route adjudication through the real SDK loop.

Deterministic-provider milestone scope (closure S5A-CR-F1): route adjudication
+ receipt convergence with the REAL product pieces — ProductRunContextAuthority
/ ProductRuntimeDecisionSink / ProductTaskExecutionAuthority, the real
ContextRouteToolService over real S4 stores (facade create/search/open,
workspace binding store), the real v45 ledger — driven by the installed
Harness SDK's actual ReActLoop with a scripted provider.  Only transport
plumbing (context/checkpoint ports, provider script, manual-grant ceremony)
is test-local, mirroring the SDK's own integration harness.

Oracle sources: TC-HM-10 rev2, TC-HM-01 rev1, TC-HM-02 rev3,
s5a-context-route-verification-spec.json (S5A-S1/S2-deterministic/S4 subsets).
"""

from __future__ import annotations

import contextvars
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from simple_harness import (
    WorkspaceBindingAuthorizationChannel,
    WorkspaceBindingAuthorizationDecision,
    WorkspaceBindingProposal,
)
from simple_harness.contracts import CallId, RequestId, RunId, freeze_json, thaw_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.execution.budget import BudgetSnapshot
from simple_harness.execution.fences import RunFenceLease
from simple_harness.execution.uow import ExecutionLease, WorkflowCheckpoint
from simple_harness.providers import (
    CancelToken,
    ProviderResponse,
    ProviderToolCall,
    ProviderToolSpec,
)
from simple_harness.runtime.context import ContextSnapshot
from simple_harness.runtime.drivers.react_loop import (
    AgentLoopCollaborator,
    EffectBatchExecutor,
    ReActLoop,
    ReActRunInput,
)
from simple_harness.runtime.kernel import RuntimeServices
from simple_harness.runtime.termination import TerminationLimits
from simple_harness.tools import ToolResult
from simple_harness.tools.executor import EffectExecution
from simple_harness.tools.runtime_catalog import (
    ToolEffectClass,
    ToolExecutionPolicy,
    ToolRouteRequirement,
    ToolTaskScopeRequirement,
)

from deskpet.memory.human_memory_service import (
    AuthenticatedHostSnapshot,
    CreateTaskScopeRequest,
    HumanMemoryHostService,
)
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_authority import (
    ContextRouteLedgerStore,
    ProductRunContextAuthority,
    ProductRuntimeDecisionSink,
)
from deskpet.sdk_adapters.context_route import (
    CONTEXT_ROUTE_SCHEMA,
    TASK_SCOPE_SEARCH_SCHEMA,
    ContextRouteToolService,
)
from deskpet.sdk_adapters.task_execution import ProductTaskExecutionAuthority
from deskpet.task_scope.workspace_bindings import (
    ManualWorkspaceChallengeAuthorityCheck,
    ManualWorkspaceDecisionAuthorityCheck,
    WorkspaceBindingAuthorityStore,
    WorkspaceBindingError,
    canonical_workspace_root,
)

RUN = RunId("run-milestone-1")
LEASE = ExecutionLease(RUN.value, "runtime.kernel", "worker-1", 1, 100.0)
FENCE = RunFenceLease(RUN, 1, "worker-1", 1)

_tool_context_var: contextvars.ContextVar = contextvars.ContextVar(
    "s5a_milestone_tool_context", default=None
)


# --- harness ports (transport only; mirror the SDK integration fakes) -------


class HarnessContext:
    def __init__(self, first_message: str) -> None:
        self.messages = [Message(role=MessageRole.USER, content=first_message)]
        self.revision = 1

    def load(self, run_id: RunId) -> ContextSnapshot:
        del run_id
        return ContextSnapshot(self.revision, tuple(self.messages))

    def append(self, run_id, lease, expected_revision, append_id, entries):
        del run_id, lease, append_id
        assert expected_revision == self.revision
        self.messages.extend(entries)
        self.revision += 1
        return ContextSnapshot(self.revision, tuple(self.messages))


class HarnessCheckpoint:
    def __init__(self) -> None:
        self.value = None
        self.initial = None

    def read_initial_react_checkpoint(self, run_id):
        del run_id
        return self.initial

    def read_react_checkpoint(self, run_id):
        del run_id
        return self.value

    def cas_react_checkpoint(
        self, *, run_id, lease, expected_version, checkpoint, checkpoint_hash,
        now, fault=None,
    ):
        del fault, now
        current = None if self.value is None else self.value.version
        assert expected_version == current
        version = 0 if current is None else current + 1
        self.value = WorkflowCheckpoint(
            run_id,
            "react.termination.v1",
            freeze_json(checkpoint),
            checkpoint_hash,
            lease.epoch,
            version,
        )
        if self.initial is None:
            self.initial = self.value
        return self.value


class ScriptedProvider:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = []

    def read_provider_budget(self, run_id: RunId) -> BudgetSnapshot:
        del run_id
        return BudgetSnapshot()

    async def invoke(self, run_id, request, *, cancel, execution_lease):
        self.calls.append(request)
        template = self.responses.pop(0)
        if isinstance(template, BaseException):
            raise template
        return ProviderResponse(
            request_id=request.request_id,
            message=template.message,
            tool_calls=template.tool_calls,
            model=template.model,
            finish_reason=template.finish_reason,
        )


class RouteExposure:
    """Real Host tool specs/policies for the two S5a tools."""

    def restore(self, run_id, checkpoint) -> None:
        del run_id, checkpoint

    def provider_specs(self, run_id) -> tuple[ProviderToolSpec, ...]:
        del run_id
        return (
            ProviderToolSpec("context_route", "Route Context", CONTEXT_ROUTE_SCHEMA),
            ProviderToolSpec(
                "task_scope_search", "Search task scopes", TASK_SCOPE_SEARCH_SCHEMA
            ),
        )

    def execution_policy(self, run_id, provider_name) -> ToolExecutionPolicy:
        del run_id
        if provider_name == "context_route":
            return ToolExecutionPolicy(
                "builtin:context_route",
                "a" * 64,
                ToolEffectClass.CONTEXT_CONTROL,
                ToolRouteRequirement.FORBIDDEN,
                ToolTaskScopeRequirement.FORBIDDEN,
            )
        return ToolExecutionPolicy(
            "builtin:task_scope_search",
            "b" * 64,
            ToolEffectClass.NON_PROJECT_EFFECT,
            ToolRouteRequirement.OPTIONAL,
            ToolTaskScopeRequirement.OPTIONAL,
        )

    def observe_tool_result(self, run_id, tool_name, result) -> None:
        del run_id, tool_name, result

    def checkpoint(self, run_id):
        del run_id
        return {"catalog_fingerprint": "c" * 64}


class RouteToolEffects:
    """Bridge the loop's effect execution onto the real route tool service."""

    def __init__(self, service: ContextRouteToolService) -> None:
        self.service = service
        self.calls: list[str] = []

    async def execute(self, **values):
        call = values["call"]
        context = values["context"]
        self.calls.append(call.name)
        token = _tool_context_var.set(context)
        try:
            if call.name == "context_route":
                raw = await self.service.handle_context_route(call.arguments)
            elif call.name == "task_scope_search":
                raw = await self.service.handle_task_scope_search(call.arguments)
            else:
                raise AssertionError(f"unexpected tool {call.name}")
        finally:
            _tool_context_var.reset(token)
        # Same conversion rule as deskpet.sdk_adapters.tools._result.
        if isinstance(raw, dict) and (raw.get("ok") is False or raw.get("error")):
            error = raw.get("error") or {}
            result = ToolResult.failed(
                call.call_id,
                str(error.get("code") or "tool_failed"),
                str(error.get("code") or "tool failed"),
            )
        else:
            result = ToolResult.succeeded(call.call_id, raw)
        return EffectExecution(effect=None, result=result)


class _NoopReconciliation:
    async def observe(self, invocation):
        raise AssertionError("provider reconciliation must not run in this lane")


def _loop() -> ReActLoop:
    return ReActLoop(
        collaborator=AgentLoopCollaborator(
            limits=TerminationLimits(6, 12, 60, 10000, 3)
        ),
        effects=EffectBatchExecutor(),
        clock=lambda: 1.0,
    )


def _answer(content: str) -> ProviderResponse:
    return ProviderResponse(
        request_id=RequestId("fixture"),
        message=Message(role=MessageRole.ASSISTANT, content=content),
        model="model-1",
        finish_reason="stop",
    )


def _tool_call(name: str, arguments: dict, raw_id: str = "raw-1") -> ProviderResponse:
    return ProviderResponse(
        request_id=RequestId("fixture"),
        message=Message(role=MessageRole.ASSISTANT, content="calling"),
        tool_calls=(ProviderToolCall(CallId(raw_id), name, arguments),),
        model="model-1",
        finish_reason="tool_calls",
    )


AUTH = AuthenticatedHostSnapshot(
    subject="deskpet-local-owner-v1",
    principal_id="local-control-channel",
    authority_ref="host:validated-control-channel:v1",
)


def harness_scope_disclosure(db_path: Path):
    """生产装配（main.py）给 ContextRouteToolService 接 ScopeDisclosureReader；缺它时
    resume_existing/continue 以 scope_disclosure_reader_missing 失败，Run 停在
    routed_standalone（2026-09-05 起）。本车道没有前台 Run 行（生产读取器靠它查主体），
    直接用同一个渲染函数、固定本车道主体。"""

    from deskpet.task_scope.disclosure import render_scope_disclosure

    async def read(run_id, package, effect_id):
        del run_id, effect_id
        return await render_scope_disclosure(
            db_path=db_path, package=package, subject=AUTH.subject, stack=None,
        )

    return read


class _ManualAuthority:
    def __init__(self) -> None:
        self.evidence: dict = {}
        self.interactions: dict = {}

    def trust_challenge(self, check) -> None:
        self.evidence[check.authorization_evidence_id] = check

    def trust_decision(self, check) -> None:
        self.interactions[check.challenge.interaction_event_id] = check

    async def verify_manual_challenge(self, check) -> None:
        if self.evidence.get(check.authorization_evidence_id) != check:
            raise WorkspaceBindingError(
                "workspace_binding_manual_evidence_not_durable"
            )

    async def verify_manual_decision(self, check) -> None:
        if self.interactions.get(check.challenge.interaction_event_id) != check:
            raise WorkspaceBindingError(
                "workspace_binding_manual_interaction_not_durable"
            )


async def _bind_scope_root(db_path: Path, scope_id: str, root: Path) -> None:
    """Seed a real binding head via the store's manual-grant path."""

    authority = _ManualAuthority()
    store = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=root.parent,
        clock_millis=lambda: 1500,
        manual_authorization_authority=authority,
    )
    proposal = WorkspaceBindingProposal(
        f"proposal-{scope_id}",
        RUN.value,
        AUTH.subject,
        scope_id,
        canonical_workspace_root(root, root_id=f"root-{scope_id}"),
        0,
        f"append-{scope_id}",
    )
    check = ManualWorkspaceChallengeAuthorityCheck(
        proposal,
        f"nonce-{scope_id}",
        WorkspaceBindingAuthorizationChannel.USER_CONFIRMATION,
        f"evidence-{scope_id}",
        "a" * 64,
        f"interaction-{scope_id}",
        1000,
        1100,
        2000,
    )
    authority.trust_challenge(check)
    challenge = await store.issue_manual_challenge(
        proposal,
        authorization_nonce=check.authorization_nonce,
        authorization_channel=check.authorization_channel,
        authorization_evidence_id=check.authorization_evidence_id,
        authorization_evidence_hash=check.authorization_evidence_hash,
        interaction_event_id=check.interaction_event_id,
        issued_at_millis=check.issued_at_millis,
        not_before_millis=check.not_before_millis,
        expires_at_millis=check.expires_at_millis,
    )
    decision_check = ManualWorkspaceDecisionAuthorityCheck(
        challenge,
        AUTH.subject,
        WorkspaceBindingAuthorizationDecision.ALLOW,
        1200,
    )
    authority.trust_decision(decision_check)
    decision = await store.record_manual_decision(
        challenge,
        decided_by_actor_id=decision_check.decided_by_actor_id,
        decision=decision_check.decision,
        decided_at_millis=decision_check.decided_at_millis,
    )
    grant = await store.verify_manual_authorization(proposal, challenge, decision)
    await store.append_binding(proposal, grant)


@pytest_asyncio.fixture()
async def milestone(tmp_path: Path):
    db_path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(db_path, approved_fresh_lane=True)
    service = HumanMemoryHostService(db_path, auth=AUTH, startup=startup)
    factory = SimpleNamespace(bind=lambda auth, **kw: service)

    context = HarnessContext("继续以前的 A")
    checkpoint = HarnessCheckpoint()
    ledger = ContextRouteLedgerStore(db_path)
    exposure = RouteExposure()
    ports = SimpleNamespace(
        context=context,
        react_checkpoint=SimpleNamespace(
            read_start_snapshot=lambda run_id: {
                "input": {
                    "context_metadata": {"budget": {"context_window": 32768}}
                }
            }
        ),
    )
    route_service = ContextRouteToolService(
        service_factory_getter=lambda: factory,
        binding_store_factory=lambda: WorkspaceBindingAuthorityStore(db_path),
        binding_append_getter=lambda: None,
        ledger=ledger,
        tool_context_getter=lambda: _tool_context_var.get(),
        scope_disclosure_reader=harness_scope_disclosure(db_path),
    )
    effects = RouteToolEffects(route_service)
    authority = ProductRunContextAuthority(
        ports_resolver=lambda: ports,
        exposure_resolver=lambda run_id: exposure,
        ledger=ledger,
    )
    sink = ProductRuntimeDecisionSink(ledger=ledger)
    return SimpleNamespace(
        db_path=db_path,
        ledger=ledger,
        service=service,
        context=context,
        checkpoint=checkpoint,
        effects=effects,
        exposure=exposure,
        authority=authority,
        sink=sink,
    )


def _services(milestone, provider) -> RuntimeServices:
    noop = object()
    return RuntimeServices(
        provider=provider,
        tools=milestone.effects,
        authorization=noop,
        context=milestone.context,
        delivery=noop,
        tool_reconciliation=noop,
        reconciliation=noop,
        provider_reconciliation=_NoopReconciliation(),
        react_checkpoint=milestone.checkpoint,
        run_context_authority=milestone.authority,
        runtime_decision_sink=milestone.sink,
        task_execution_authority=ProductTaskExecutionAuthority(),
    )


async def _run(milestone, provider) -> object:
    return await _loop().run(
        ReActRunInput(RUN, RequestId("request-1"), tool_exposure=milestone.exposure),
        services=_services(milestone, provider),
        execution_lease=LEASE,
        run_fence=FENCE,
        cancel=CancelToken(),
        initial_messages=(),
    )


def _rows(db_path: Path, sql: str, *params):
    with sqlite3.connect(db_path) as db:
        return db.execute(sql, params).fetchall()


@pytest.mark.asyncio
async def test_milestone_direct_standalone_route_and_per_turn_snapshots(
    milestone,
) -> None:
    provider = ScriptedProvider(
        [
            _tool_call("context_route", {"route": "direct_standalone"}),
            _answer("rewritten answer"),
        ]
    )
    result = await _run(milestone, provider)

    assert result.termination.route_state == "routed_standalone"
    decisions = _rows(
        milestone.db_path,
        "SELECT route,origin FROM context_route_decisions WHERE sdk_run_id=?",
        RUN.value,
    )
    assert decisions == [("direct_standalone", "context_tool")]

    snapshots = _rows(
        milestone.db_path,
        "SELECT snapshot_revision,provider_turn_ordinal,payload_hash,"
        "expected_request_fingerprint FROM run_context_snapshot_receipts "
        "WHERE sdk_run_id=? ORDER BY snapshot_revision",
        RUN.value,
    )
    assert [row[0] for row in snapshots] == [1, 2]
    assert [row[1] for row in snapshots] == [1, 2]
    assert all(row[2] == row[3] for row in snapshots)

    checkpoint = dict(thaw_json(milestone.checkpoint.value.checkpoint))
    receipt = dict(checkpoint["context_authority_receipt"])
    assert receipt["payload_hash"] == snapshots[-1][2]
    assert receipt["snapshot_revision"] == 2


@pytest.mark.asyncio
async def test_milestone_resume_existing_same_run_continuation(milestone, tmp_path) -> None:
    created = await milestone.service.create_task_scope(
        CreateTaskScopeRequest(
            "task-a", "任务 A", "写完 A 的季度报告，下一步补图表", "create-a"
        )
    )
    scope_id = str(created["scope_ref"])
    await milestone.service.rebuild_derived(scope_id)
    workspace = tmp_path / "workspace" / "root-a"
    workspace.mkdir(parents=True)
    await _bind_scope_root(milestone.db_path, scope_id, workspace)

    provider = ScriptedProvider(
        [
            _tool_call("task_scope_search", {"query": "以前的 A"}, raw_id="raw-search"),
            _tool_call(
                "context_route",
                {"route": "resume_existing", "task_scope_id": scope_id},
                raw_id="raw-route",
            ),
            _answer("继续 A：下一步补图表"),
        ]
    )
    result = await _run(milestone, provider)

    assert result.termination.route_state == "routed_task"
    assert milestone.effects.calls == ["task_scope_search", "context_route"]

    head = await WorkspaceBindingAuthorityStore(
        milestone.db_path
    ).current_receipt(scope_id)
    checkpoint = dict(thaw_json(milestone.checkpoint.value.checkpoint))
    route_receipt = dict(checkpoint["route_receipt"])
    assert route_receipt["route"] == "resume_existing"
    assert route_receipt["task_scope_id"] == scope_id
    assert route_receipt["binding_set_revision"] == head.binding_set_revision
    assert route_receipt["binding_set_receipt_id"] == head.receipt_id
    assert route_receipt["binding_set_receipt_hash"] == head.receipt_hash

    decisions = _rows(
        milestone.db_path,
        "SELECT route,origin,task_scope_id FROM context_route_decisions "
        "WHERE sdk_run_id=?",
        RUN.value,
    )
    assert decisions == [("resume_existing", "context_tool", scope_id)]

    # Same-Run continuation: the final provider turn's snapshot (payload the
    # model actually answered from) contains the exact ResumePackage content.
    # 2026-09-05 起恢复包经 ScopeDisclosureReader 投影：标题/目标只有经生产
    # create_new 路由证明过才披露（disclosure.py），本用例的 scope 由服务直接创建，
    # 标题/目标按 original_sources_unavailable 不披露（同轮 task_scope_search 的候选
    # 也一样）。因此核对的是本轮 context_route 实际返回的那份恢复包（披露清单哈希）。
    final_request = provider.calls[-1]
    joined = "".join(str(message.content) for message in final_request.messages)
    [route_message] = [
        str(message.content) for message in final_request.messages
        if message.role is MessageRole.TOOL and message.name == "context_route"
    ]
    package = json.loads(route_message)["value"]["resume_package"]
    assert package["task_scope_id"] == scope_id and package["status"] == "active"
    assert package["disclosure_manifest"]["manifest_hash"] in joined
    snapshots = _rows(
        milestone.db_path,
        "SELECT snapshot_revision FROM run_context_snapshot_receipts "
        "WHERE sdk_run_id=? ORDER BY snapshot_revision",
        RUN.value,
    )
    assert [row[0] for row in snapshots] == [1, 2, 3]


@pytest.mark.asyncio
async def test_milestone_unrouted_terminal_records_no_recall(milestone) -> None:
    provider = ScriptedProvider([_answer("direct answer, no memory needed")])
    result = await _run(milestone, provider)

    assert result.termination.route_state == "routed_standalone"
    decisions = _rows(
        milestone.db_path,
        "SELECT route,origin FROM context_route_decisions WHERE sdk_run_id=?",
        RUN.value,
    )
    assert decisions == [("direct_standalone", "no_recall")]
    # TC-HM-01: no Memory query happened — no route-tool invocation at all.
    assert milestone.effects.calls == []


# 2026-09-10 removed with the Memory SDK: test_milestone_memory_standalone_stays_blocked_then_no_recall
