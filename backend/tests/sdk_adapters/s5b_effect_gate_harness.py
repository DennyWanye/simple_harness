# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b effect-gate 测试基座（Task 1；真 ReActLoop + 真三 authority + v45 ledger + S4 binding store）。

蓝本：``tests/sdk_adapters/test_s5a_milestone_route_loop.py`` 与增量 spike A3 / spec-effect。
只有传输层（context/checkpoint port、脚本化 provider、Manual grant 仪式、物理 write_file
处理器）是测试本地的；route 裁决、envelope 签发、S4 重验、EffectGate 与 ``ProductEffectExecutor``
的前置门全部走生产代码。本模块不含用例（文件名不以 ``test_`` 开头）。
"""

from __future__ import annotations

import contextvars
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from simple_harness import (
    WorkspaceBindingAuthorizationChannel,
    WorkspaceBindingAuthorizationDecision,
    WorkspaceBindingProposal,
)
from simple_harness.contracts import CallId, RequestId, RunId, thaw_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.execution.budget import BudgetSnapshot
from simple_harness.execution.effects import (
    EffectRecord,
    EffectState,
    effect_request_hash,
)
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
from simple_harness.tools.executor import EffectExecution, EffectExecutor
from simple_harness.tools.runtime_catalog import (
    ToolEffectClass,
    ToolExecutionPolicy,
    ToolRouteRequirement,
    ToolTaskScopeRequirement,
)

from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress
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
from deskpet.sdk_adapters.effect_gate import EffectGate
from deskpet.sdk_adapters.run_faults import RunFaultMemo
from deskpet.sdk_adapters.run_route_state import RunRouteStateMemo
from deskpet.sdk_adapters.task_execution import (
    BindingRootResolver,
    ProductTaskExecutionAuthority,
)
from deskpet.sdk_adapters.task_scope_mutation import (
    TASK_SCOPE_UPDATE_SCHEMA,
    TaskScopeUpdateService,
)
from deskpet.sdk_adapters.tool_authority import (
    PROJECT_EFFECT_TOOL_NAMES,
    SDK_TOOL_EXECUTION_POLICY_OVERRIDES,
)
from deskpet.sdk_adapters.tools import ProductEffectExecutor
from deskpet.task_scope.store import CanonicalTaskScopeStore
from deskpet.task_scope.workspace_bindings import (
    ManualWorkspaceChallengeAuthorityCheck,
    ManualWorkspaceDecisionAuthorityCheck,
    WorkspaceBindingAuthorityStore,
    WorkspaceBindingError,
    canonical_workspace_root,
)
from deskpet.types.task_work_context import TaskWorkContext

RUN = RunId("run-s5b-gate-1")
REQUEST = RequestId("request-1")
LEASE = ExecutionLease(RUN.value, "runtime.kernel", "worker-1", 1, 100.0)
FENCE = RunFenceLease(RUN, 1, "worker-1", 1)

tool_context_var: contextvars.ContextVar = contextvars.ContextVar(
    "s5b_gate_tool_context", default=None
)

WRITE_FILE_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
    "required": ["path", "content"],
}
READ_FILE_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
}

AUTH = AuthenticatedHostSnapshot(
    subject="deskpet-local-owner-v1",
    principal_id="local-control-channel",
    authority_ref="host:validated-control-channel:v1",
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
        from simple_harness.contracts import freeze_json

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
    """Host tool specs/policies: the two S5a tools, a PROJECT_EFFECT write_file
    and a NON_PROJECT_EFFECT read_file."""

    def restore(self, run_id, checkpoint) -> None:
        del run_id, checkpoint

    def provider_specs(self, run_id) -> tuple[ProviderToolSpec, ...]:
        del run_id
        return (
            ProviderToolSpec("context_route", "Route Context", CONTEXT_ROUTE_SCHEMA),
            ProviderToolSpec(
                "task_scope_search", "Search task scopes", TASK_SCOPE_SEARCH_SCHEMA
            ),
            ProviderToolSpec(
                "task_scope_update", "Close the TaskScope", TASK_SCOPE_UPDATE_SCHEMA
            ),
            ProviderToolSpec("write_file", "Write a project file", WRITE_FILE_SCHEMA),
            ProviderToolSpec("read_file", "Read a project file", READ_FILE_SCHEMA),
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
        if provider_name == "task_scope_update":
            effect, route, scope = SDK_TOOL_EXECUTION_POLICY_OVERRIDES["task_scope_update"]
            return ToolExecutionPolicy(
                "builtin:task_scope_update",
                "f" * 64,
                ToolEffectClass(effect),
                ToolRouteRequirement(route),
                ToolTaskScopeRequirement(scope),
            )
        if provider_name in PROJECT_EFFECT_TOOL_NAMES:
            return ToolExecutionPolicy(
                f"builtin:{provider_name}",
                "d" * 64,
                ToolEffectClass.PROJECT_EFFECT,
                ToolRouteRequirement.REQUIRED,
                ToolTaskScopeRequirement.REQUIRED,
            )
        if provider_name == "read_file":
            return ToolExecutionPolicy(
                "builtin:read_file",
                "e" * 64,
                ToolEffectClass.NON_PROJECT_EFFECT,
                ToolRouteRequirement.OPTIONAL,
                ToolTaskScopeRequirement.OPTIONAL,
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


class PhysicalToolBridge(EffectExecutor):
    """The physical dispatch layer that ``ProductEffectExecutor.execute``
    reaches through ``super().execute`` once the Host gate admitted the call.

    ``write_file`` really writes under the harness workspace so "零写入" is
    a filesystem fact, not a counter.  A settled call returns a real SDK
    ``EffectRecord`` (Task 2: the Host objective-event hook keys on the
    settled effect state, exactly as with the production SDK ledger).
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.service: ContextRouteToolService | None = None
        self.closure_service: TaskScopeUpdateService | None = None
        self.workspace: Path | None = None
        self.calls: list[str] = []
        self.write_file_calls: list[dict] = []

    async def execute(self, **values):
        call = values["call"]
        context = values["context"]
        self.calls.append(call.name)
        token = tool_context_var.set(context)
        try:
            if call.name == "context_route":
                raw = await self.service.handle_context_route(call.arguments)
            elif call.name == "task_scope_search":
                raw = await self.service.handle_task_scope_search(call.arguments)
            elif call.name == "task_scope_update":
                assert self.closure_service is not None
                raw = await self.closure_service.handle_task_scope_update(call.arguments)
            elif call.name == "write_file":
                assert self.workspace is not None
                target = self.workspace / str(call.arguments["path"])
                target.write_text(str(call.arguments["content"]), encoding="utf-8")
                self.write_file_calls.append(
                    {
                        "arguments": dict(call.arguments),
                        "context": context,
                        "envelope": context.task_execution_envelope,
                    }
                )
                raw = {"ok": True, "written": str(target)}
            elif call.name == "read_file":
                raw = {"ok": True, "content": ""}
            else:
                raise AssertionError(f"unexpected tool {call.name}")
        finally:
            tool_context_var.reset(token)
        if isinstance(raw, ToolResult):
            # Host handlers may return the SDK ToolResult directly (rejected /
            # retryable failed); ``sdk_adapters.tools._result`` passes it through.
            # A rejection is the SDK authorization-deny shape: no effect record.
            if raw.outcome.value == "rejected":
                return EffectExecution(effect=None, result=raw)
            result = raw
            state = EffectState.SUCCEEDED if result.outcome.value == "succeeded" else EffectState.FAILED
        elif isinstance(raw, dict) and (raw.get("ok") is False or raw.get("error")):
            error = raw.get("error") or {}
            result = ToolResult.failed(
                call.call_id,
                str(error.get("code") or "tool_failed"),
                str(error.get("code") or "tool failed"),
            )
            state = EffectState.FAILED
        else:
            result = ToolResult.succeeded(call.call_id, raw)
            state = EffectState.SUCCEEDED
        # SDK ToolCall arguments are frozen (lists → tuples); the ledger record
        # and its request hash take the thawed JSON exactly like the SDK executor.
        thawed_arguments = thaw_json(call.arguments)
        record = EffectRecord(
            effect_id=values["effect_id"],
            run_id=context.run_id,
            call_id=call.call_id,
            tool_name=call.name,
            request_hash=effect_request_hash(
                tool_name=call.name, arguments=thawed_arguments
            ),
            arguments=thawed_arguments,
            state=state,
            version=2,
            fence_epoch=1,
            authorization_receipt_ref="harness:allow",
            handoff_receipt_ref="harness:handoff",
            result=result,
            raw_call_id=values.get("raw_call_id"),
            turn_ordinal=int(values.get("turn_ordinal", 0)),
            call_ordinal=int(values.get("call_ordinal", 0)),
            task_execution_envelope=context.task_execution_envelope,
        )
        return EffectExecution(effect=record, result=result)


class GatedEffects(ProductEffectExecutor, PhysicalToolBridge):
    """Production ``ProductEffectExecutor`` (gate + workspace + admission
    front) over the harness physical bridge."""


class _Registry:
    def assert_workspace_current(self, run_id) -> None:
        del run_id

    def run_session_and_root(self, run_id):
        # 生产 ProductToolRegistry.run_session_and_root（受保护路径检查点，2026-09-26
        # 权限改造）对未知 Run 返回空会话、无根目录；本替身没有 Run 权限表，同样返回。
        del run_id
        return "", None


def _harness_disclosure(db_path: Path):
    from deskpet.task_scope.disclosure import render_scope_disclosure

    async def read(run_id, package, effect_id):
        del run_id, effect_id
        return await render_scope_disclosure(
            db_path=db_path, package=package, subject=AUTH.subject, stack=None,
        )

    return read


class _NoEffectLedger:
    """SDK effect ledger stand-in: no durable record ever exists, so the
    production step-0 replay check always runs the gate (first occurrence)."""

    def read_effect(self, effect_id):
        del effect_id


class _NoopReconciliation:
    async def observe(self, invocation):
        raise AssertionError("provider reconciliation must not run in this lane")


class RecordingTaskExecutionAuthority:
    """Wrap the real Host authority to record issue_envelope calls/errors."""

    def __init__(self, inner: ProductTaskExecutionAuthority) -> None:
        self.inner = inner
        self.requests: list = []
        self.envelopes: list = []
        self.errors: list = []

    async def issue_envelope(self, request):
        self.requests.append(request)
        try:
            envelope = await self.inner.issue_envelope(request)
        except Exception as exc:
            self.errors.append(exc)
            raise
        self.envelopes.append(envelope)
        return envelope


def loop() -> ReActLoop:
    return ReActLoop(
        collaborator=AgentLoopCollaborator(
            limits=TerminationLimits(8, 16, 60, 10000, 3)
        ),
        effects=EffectBatchExecutor(),
        clock=lambda: 1.0,
    )


def answer(content: str) -> ProviderResponse:
    return ProviderResponse(
        request_id=RequestId("fixture"),
        message=Message(role=MessageRole.ASSISTANT, content=content),
        model="model-1",
        finish_reason="stop",
    )


def tool_call(name: str, arguments: dict, raw_id: str = "raw-1") -> ProviderResponse:
    return ProviderResponse(
        request_id=RequestId("fixture"),
        message=Message(role=MessageRole.ASSISTANT, content="calling"),
        tool_calls=(ProviderToolCall(CallId(raw_id), name, arguments),),
        model="model-1",
        finish_reason="tool_calls",
    )


def tool_calls(*calls: tuple[str, dict, str]) -> ProviderResponse:
    return ProviderResponse(
        request_id=RequestId("fixture"),
        message=Message(role=MessageRole.ASSISTANT, content="calling"),
        tool_calls=tuple(
            ProviderToolCall(CallId(raw_id), name, arguments)
            for name, arguments, raw_id in calls
        ),
        model="model-1",
        finish_reason="tool_calls",
    )


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


async def bind_scope_root(
    db_path: Path,
    scope_id: str,
    root: Path,
    *,
    base_revision: int = 0,
    tag: str = "",
) -> None:
    """Seed / append a real binding revision via the store's Manual-grant path."""

    suffix = f"{scope_id}{'-' + tag if tag else ''}"
    authority = _ManualAuthority()
    store = WorkspaceBindingAuthorityStore(
        db_path,
        configured_workspace_root=root.parent,
        clock_millis=lambda: 1500,
        manual_authorization_authority=authority,
    )
    proposal = WorkspaceBindingProposal(
        f"proposal-{suffix}",
        RUN.value,
        AUTH.subject,
        scope_id,
        canonical_workspace_root(root, root_id=f"root-{suffix}"),
        base_revision,
        f"append-{suffix}",
    )
    check = ManualWorkspaceChallengeAuthorityCheck(
        proposal,
        f"nonce-{suffix}",
        WorkspaceBindingAuthorizationChannel.USER_CONFIRMATION,
        f"evidence-{suffix}",
        "a" * 64,
        f"interaction-{suffix}",
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


def frozen_authority(
    *,
    task_scope_id: str,
    workspace_root: Path | None,
) -> SimpleNamespace:
    """Run-admission frozen Tool authority as ``registry.resolve(run_id)`` sees it.

    Mirrors ``ProductForegroundToolPort.freeze``: an exact single root freezes
    ``workspace_root`` = canonical path (resolution kind ``legacy`` with the
    exact effective root); zero/multi root freezes ``projectless`` with no root.
    """

    if workspace_root is None:
        work = TaskWorkContext(
            "session-1", "host-run-1", task_scope_id, None, "none", 1
        )
        resolution = {"kind": "projectless", "effective_root": None, "binding_version": 1}
    else:
        canonical = canonical_workspace_root(workspace_root, root_id="frozen").canonical_path
        work = TaskWorkContext(
            "session-1", "host-run-1", task_scope_id, canonical, "existing", 1
        )
        resolution = {"kind": "legacy", "effective_root": canonical, "binding_version": 1}
    return SimpleNamespace(
        run_id=RUN.value,
        request_id=REQUEST.value,
        task_work_context=work,
        workspace_resolution=resolution,
    )


class GateEnv(SimpleNamespace):
    pass


async def build_env(tmp_path: Path, *, first_message: str = "继续以前的 A") -> GateEnv:
    """Real product pieces over one fresh v45+ state.db."""

    db_path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(db_path, approved_fresh_lane=True)
    service = HumanMemoryHostService(db_path, auth=AUTH, startup=startup)
    factory = SimpleNamespace(bind=lambda auth, **kw: service)

    context = HarnessContext(first_message)
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
        tool_context_getter=lambda: tool_context_var.get(),
        # 生产装配（main.py）接 ScopeDisclosureReader；resume_existing 缺它会以
        # scope_disclosure_reader_missing 失败（2026-09-05 起）。本车道没有前台 Run
        # 行（生产读取器靠它查主体），直接用同一个渲染函数、固定本车道主体。
        scope_disclosure_reader=_harness_disclosure(db_path),
    )
    from deskpet.execution.semantic_closure import closure_instruction_for_run

    binding_store = WorkspaceBindingAuthorityStore(db_path)
    route_memo = RunRouteStateMemo()
    authority = ProductRunContextAuthority(
        ports_resolver=lambda: ports,
        exposure_resolver=lambda run_id: exposure,
        ledger=ledger,
        closure_reader=lambda run_id: closure_instruction_for_run(db_path, run_id.value),
        # Task 6 (AC-3⑥)：≥2 root scope 的 snapshot 不暴露 PROJECT_EFFECT 工具（与生产装配一致）。
        binding_store=binding_store,
        # HM-TO-A6 incident A：同一个 route_state 既收窄本轮 provider specs，
        # 也发布给能力披露面（与生产装配一致）。
        route_state_memo=route_memo,
    )
    closure_service = TaskScopeUpdateService(
        db_path,
        tool_context_getter=lambda: tool_context_var.get(),
        route_ledger=ledger,
    )
    sink = ProductRuntimeDecisionSink(ledger=ledger)
    memo = RunFaultMemo()
    task_authority = RecordingTaskExecutionAuthority(
        ProductTaskExecutionAuthority(
            root_resolver=BindingRootResolver(binding_store),
            fault_sink=memo,
        )
    )
    frozen: dict[str, object] = {"authority": None}
    scope_store = CanonicalTaskScopeStore(db_path)
    gate = EffectGate(
        binding_store=binding_store,
        route_ledger=ledger,
        scope_store=scope_store,
        authority_resolver=lambda run_id: frozen["authority"],
        exposure_resolver=lambda run_id: exposure,
    )
    evidence_ingress = ExecutionEvidenceIngress(db_path)
    effects = GatedEffects(
        uow=_NoEffectLedger(),
        registry=_Registry(),
        authorization=object(),
        reconciliation=object(),
        effect_gate=gate,
        evidence_ingress=evidence_ingress,
    )
    effects.service = route_service
    effects.closure_service = closure_service
    return GateEnv(
        closure_service=closure_service,
        db_path=db_path,
        evidence_ingress=evidence_ingress,
        ledger=ledger,
        service=service,
        context=context,
        checkpoint=checkpoint,
        effects=effects,
        exposure=exposure,
        authority=authority,
        sink=sink,
        gate=gate,
        memo=memo,
        route_memo=route_memo,
        binding_store=binding_store,
        scope_store=scope_store,  # test_effect_gate 读规范头状态（a189ec4e 起引用，替身此前缺）
        task_authority=task_authority,
        frozen=frozen,
        workspace_base=tmp_path / "workspace",
    )


def freeze_run(env: GateEnv, *, task_scope_id: str, workspace_root: Path | None) -> None:
    """Freeze the Run admission authority (scope + exact root) for this Run."""

    env.frozen["authority"] = frozen_authority(
        task_scope_id=task_scope_id, workspace_root=workspace_root
    )
    env.effects.workspace = workspace_root


async def make_bound_scope(env: GateEnv, name: str, root_dir: str) -> tuple[str, Path]:
    created = await env.service.create_task_scope(
        CreateTaskScopeRequest(
            f"task-{name}", f"任务 {name}", f"写完 {name} 的季度报告，下一步补图表", f"create-{name}"
        )
    )
    scope_id = str(created["scope_ref"])
    await env.service.rebuild_derived(scope_id)
    workspace = env.workspace_base / root_dir
    workspace.mkdir(parents=True)
    await bind_scope_root(env.db_path, scope_id, workspace)
    return scope_id, workspace


def services(env: GateEnv, provider) -> RuntimeServices:
    noop = object()
    return RuntimeServices(
        provider=provider,
        tools=env.effects,
        authorization=noop,
        context=env.context,
        delivery=noop,
        tool_reconciliation=noop,
        reconciliation=noop,
        provider_reconciliation=_NoopReconciliation(),
        react_checkpoint=env.checkpoint,
        run_context_authority=env.authority,
        runtime_decision_sink=env.sink,
        task_execution_authority=env.task_authority,
    )


async def run(env: GateEnv, provider) -> object:
    return await loop().run(
        ReActRunInput(RUN, REQUEST, tool_exposure=env.exposure),
        services=services(env, provider),
        execution_lease=LEASE,
        run_fence=FENCE,
        cancel=CancelToken(),
        initial_messages=(),
    )


async def run_capture(env: GateEnv, provider) -> dict:
    out: dict = {"exception": None, "result": None}
    try:
        out["result"] = await run(env, provider)
    except Exception as exc:  # noqa: BLE001 - the lane records Run faults
        out["exception"] = exc
    return out


def rows(db_path: Path, sql: str, *params):
    with sqlite3.connect(db_path) as db:
        return db.execute(sql, params).fetchall()


def tool_messages(env: GateEnv, name: str) -> list[str]:
    return [
        str(m.content)
        for m in env.context.messages
        if m.role is MessageRole.TOOL and m.name == name
    ]


def checkpoint(env: GateEnv) -> dict:
    return dict(thaw_json(env.checkpoint.value.checkpoint)) if env.checkpoint.value else {}


def provider_tool_names(provider: ScriptedProvider) -> list[list[str]]:
    """Per provider turn: the exact tool specs the snapshot exposed to the model."""

    return [[spec.name for spec in request.tools] for request in provider.calls]
