"""SDK 运行服务：延迟单例、目录冻结与价格快照、提供方绑定解析、运行授权代理、运行上下文权威"""

from __future__ import annotations

from pathlib import Path

from deskpet.execution.current_tool_pages import CurrentToolProjector
from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress
from deskpet.execution.foreground_runtime import ForegroundEffectAdmissionGate
from deskpet.execution.primary_dependencies import check_runtime_dependencies
from deskpet.execution.semantic_closure import closure_instruction_for_run
from deskpet.memory.primary_visibility import PrimaryHistoryPolicy
from deskpet.sdk_adapters.context_authority import (
    ContextRouteLedgerStore,
    ProductRunContextAuthority,
    canonical_sha256,
)
from deskpet.sdk_adapters.context_partitions import tool_schema_tokens
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from deskpet.sdk_adapters.reconciliation import ProductProviderRetryOnceReconciliation
from deskpet.sdk_adapters.run_bindings import SdkRunBindingRegistry, SdkRunBindingV1
from deskpet.sdk_adapters.run_faults import RunFaultMemo
from deskpet.sdk_adapters.run_route_state import RunRouteStateMemo
from deskpet.sdk_adapters.task_scope_mutation import TaskScopeUpdateService
from deskpet.sdk_adapters.tools import active_product_tool_context
from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore
from deskpet.tools.code_tools.spawn_subagents_tool import UNWIRED_DELEGATION_TOOL_NAMES
from llm.pricing import get_price
from simple_harness import thaw_json
from simple_harness.execution import ProviderBinding
from simple_harness.execution.budget import BudgetPolicy, FrozenPriceEstimator

from host.runtime import logger, service_context
from host.state import host_state
from host.stores_bootstrap import _state_db_path


def _ensure_provider_reconciliation():
    if host_state.provider_reconciliation is None:
        host_state.provider_reconciliation = ProductProviderRetryOnceReconciliation()
    return host_state.provider_reconciliation


def _ensure_foreground_effect_gate():  # type: ignore[no-untyped-def]
    if host_state.foreground_effect_gate is None:
        host_state.foreground_effect_gate = ForegroundEffectAdmissionGate()
    return host_state.foreground_effect_gate


def _ensure_run_fault_memo():  # type: ignore[no-untyped-def]
    if host_state.run_fault_memo is None:
        host_state.run_fault_memo = RunFaultMemo()
    return host_state.run_fault_memo


def _ensure_run_route_state_memo():  # type: ignore[no-untyped-def]
    if host_state.run_route_state_memo is None:
        host_state.run_route_state_memo = RunRouteStateMemo()
    return host_state.run_route_state_memo


def _ensure_evidence_ingress():  # type: ignore[no-untyped-def]
    if host_state.evidence_ingress is None:
        host_state.evidence_ingress = ExecutionEvidenceIngress(_state_db_path)
    return host_state.evidence_ingress


def _ensure_task_scope_update_service():  # type: ignore[no-untyped-def]
    if host_state.task_scope_update_service is None:
        host_state.task_scope_update_service = TaskScopeUpdateService(
            _state_db_path,
            tool_context_getter=active_product_tool_context,
            route_ledger=ContextRouteLedgerStore(_state_db_path),
        )
    return host_state.task_scope_update_service


def _sdk_price_snapshot(provider_id: str, model_id: str) -> tuple[int, int, str]:
    """Freeze the product pricing table for one physical Provider binding.

    The SDK budget authority is expressed as micro-dollars per million
    tokens.  The old stack passed ``(0, 0)`` for every model, which converted
    unknown charge into a trusted zero.  Product pricing deliberately has a
    pessimistic entry for unknown models, so a missing table row remains
    bounded without becoming free.
    """

    price = get_price(provider_id, model_id)
    payload = {
        "provider_id": str(provider_id),
        "model_id": str(model_id),
        "input_per_m": price.input_per_m,
        "output_per_m": price.output_per_m,
        "cache_read_per_m": price.cache_read_per_m,
        "cache_write_per_m": price.cache_write_per_m,
    }
    return (
        max(0, round(price.input_per_m * 1_000_000)),
        max(0, round(price.output_per_m * 1_000_000)),
        f"deskpet-pricing:{canonical_sha256(payload)}",
    )


def _freeze_sdk_catalog(
    tools_adapter: Any,
    generation: int,
    *,
    visibility_registry: Any | None = None,
) -> dict[str, Any]:
    """Return the exact immutable Tool catalog used by Provider and executor."""

    specs: list[dict[str, Any]] = []
    schema_fingerprints: dict[str, str] = {}
    for spec in tools_adapter.specs:
        if str(spec.name) in UNWIRED_DELEGATION_TOOL_NAMES:
            logger.info(
                "sdk_tool_excluded_from_catalog name=%s reason=delegation_not_wired", spec.name
            )
            continue
        live_spec = (
            visibility_registry.get(str(spec.name)) if visibility_registry is not None else None
        )
        if (
            live_spec is not None
            and getattr(live_spec, "visible_when", None) is not None
            and str(getattr(live_spec, "visibility_scope", "global")) == "global"
            and not live_spec.is_visible(None)
        ):
            logger.info(
                "sdk_tool_excluded_from_catalog name=%s reason=global_visibility_not_ready",
                spec.name,
            )
            continue
        # SDK ToolSpec recursively freezes schemas with MappingProxyType.  A
        # shallow dict() only unwraps the root and leaves nested properties
        # non-JSON, which makes canonical hashing fail during cold startup.
        schema = thaw_json(spec.input_schema)
        if not isinstance(schema, dict):
            raise TypeError(f"{spec.name} Tool schema must be a JSON object")
        record = {
            "name": str(spec.name),
            "description": str(spec.description),
            "input_schema": schema,
        }
        specs.append(record)
        schema_fingerprints[str(spec.name)] = canonical_sha256(schema)
    # Incident N: one wire-shaped tool-schema estimator for every lane.  The
    # old ``len(repr(schema)) // 4`` dropped the tool name and description and
    # counted a repr rather than the JSON actually sent.
    schema_token_count = tool_schema_tokens(specs)
    fingerprint = canonical_sha256(specs)
    return {
        "generation": int(generation),
        "content_fingerprint": fingerprint,
        "tool_names": [item["name"] for item in specs],
        "tool_count": len(specs),
        "schema_token_count": schema_token_count,
        "schema_fingerprints": schema_fingerprints,
        "specs": specs,
    }


def _primary_history_policy(subject):
    """Host dependency-proof policy.

    2026-09-10：原来的第三个参数是公开 Memory 历史可见性 checker，随认知记忆
    SDK 一并移除；没有记忆系统就没有抑制权威，遍历证明通过即可见。
    """

    return PrimaryHistoryPolicy(_state_db_path, subject)


class _ProductSdkProviderBindingResolver:
    """SDK resolver backed only by immutable per-Run product bindings."""

    def __init__(self, provider_registry: Any, client: Any) -> None:

        self.registry = SdkRunBindingRegistry()
        self._provider_registry = provider_registry
        self._client = client
        self._authorities: dict[str, Any] = {}

    def build_authority(self, binding: Any, *, request_guard: Any = ...) -> Any:

        entry = self._provider_registry.get_entry(binding.provider_id)
        if entry is None:
            raise RuntimeError("SDK bound Provider incarnation is unavailable")
        if (
            str(getattr(entry, "incarnation_id", "")) != binding.provider_incarnation_id
            or int(getattr(entry, "config_revision", 0) or 0) != binding.provider_config_revision
        ):
            raise RuntimeError("SDK bound Provider incarnation changed")

        async def primary_guard(request):

            await check_runtime_dependencies(
                db_path=_state_db_path,
                stack=host_state.sdk_runtime_stack,
                sdk_run_id=binding.run_id,
                request=request,
                policy_factory=_primary_history_policy,
                typed_use_authority=service_context.get("sdk_typed_context_use_authority"),
            )

        if request_guard is ...:
            request_guard = primary_guard
        elif not callable(request_guard):
            raise TypeError("SDK provider request guard must be callable")
        provider = ProductProviderAdapter(
            self._provider_registry,
            provider_id=binding.provider_id,
            client=self._client,
            price_resolver=_sdk_price_snapshot,
            model=binding.model_id,
            model_params=thaw_json(binding.model_params),
            pre_invoke_guard=request_guard,
        )
        price = provider.price_snapshot
        estimator = FrozenPriceEstimator(
            price.version,
            provider.target.pricing_key,
            price.input_micros_per_million,
            price.output_micros_per_million,
        )
        return ProviderBinding(provider, estimator, BudgetPolicy())

    def create_binding(self, **raw: Any) -> Any:
        """Build both sides without allowing their budget identity to drift."""

        provisional = SdkRunBindingV1.build(
            **raw,
            budget_fingerprint="pending-provider-budget",
        )
        authority = self.build_authority(provisional)
        binding = SdkRunBindingV1.build(
            **raw,
            budget_fingerprint=authority.budget_fingerprint,
        )
        self.registry.register(binding)
        self._authorities[binding.run_id] = authority
        return binding

    def register(self, binding: Any) -> Any:
        current = self.registry.register(binding)
        authority = self._authorities.get(current.run_id)
        if authority is None:
            authority = self.build_authority(current)
            if authority.budget_fingerprint != current.budget_fingerprint:
                raise RuntimeError("SDK Run budget binding fingerprint mismatch")
            self._authorities[current.run_id] = authority
        return authority

    def resolve(self, run_id: Any) -> Any:
        value = str(getattr(run_id, "value", run_id))
        binding = self.registry.resolve(value)
        authority = self._authorities.get(value)
        if binding is None or authority is None:
            raise KeyError(value)
        return authority

    def mark_waiting(self, run_id: str) -> None:
        self.registry.mark_waiting(run_id)

    def mark_terminal(self, run_id: str, state: str) -> None:
        if str(state).lower() not in {"completed", "failed", "cancelled", "stopped"}:
            raise ValueError("terminal_state must be completed, failed, cancelled, or stopped")
        # Cold terminal recovery never binds a Provider in this process. Only
        # an entirely absent registration may skip process-local cleanup;
        # inconsistent caches and errors for existing bindings still surface.
        if self.registry.resolve(run_id) is None and str(run_id) not in self._authorities:
            return
        self.registry.mark_terminal(run_id, state)
        self._authorities.pop(str(run_id), None)

    def active_provider_ids(self) -> frozenset[str]:
        return frozenset(
            binding.provider_id
            for run_id in tuple(self._authorities)
            if (binding := self.registry.resolve(run_id)) is not None
        )


class _SdkRunContextAuthorityProxy:
    async def prepare_snapshot(self, request):  # type: ignore[no-untyped-def]
        target = service_context.get("sdk_run_context_authority")
        operation = getattr(target, "prepare_snapshot", None)
        if not callable(operation):
            raise RuntimeError("sdk_run_context_authority_unavailable")
        return await operation(request)


class _SdkRuntimeDecisionSinkProxy:
    async def check_mandatory_context_actions(self, **values):
        target = service_context.get("sdk_runtime_decision_sink")
        operation = getattr(target, "check_mandatory_context_actions", None)
        if not callable(operation):
            raise RuntimeError("sdk_mandatory_context_action_recheck_unavailable")
        return await operation(**values)

    async def record_no_recall(self, **values):  # type: ignore[no-untyped-def]
        target = service_context.get("sdk_runtime_decision_sink")
        operation = getattr(target, "record_no_recall", None)
        if not callable(operation):
            raise RuntimeError("sdk_runtime_decision_sink_unavailable")
        return await operation(**values)


class _SdkTaskExecutionAuthorityProxy:
    async def issue_envelope(self, request):  # type: ignore[no-untyped-def]
        target = service_context.get("sdk_task_execution_authority")
        operation = getattr(target, "issue_envelope", None)
        if not callable(operation):
            raise RuntimeError("sdk_task_execution_authority_unavailable")
        return await operation(request)


def _sdk_runtime_authority_bindings() -> dict[str, object]:
    """Bind SDK 0.7 required ports without granting fallback authority."""

    return {
        "run_context_authority": _SdkRunContextAuthorityProxy(),
        "runtime_decision_sink": _SdkRuntimeDecisionSinkProxy(),
        "task_execution_authority": _SdkTaskExecutionAuthorityProxy(),
    }


def _build_run_context_authority(  # type: ignore[no-untyped-def]
    *,
    state_db_path,
    ports_resolver,
    exposure_resolver,
    ledger,
    reconcile,
    typed_use_authority=None,
    occurrence_coordinator=None,
    configured_workspace_root=None,
):
    """Production ``ProductRunContextAuthority`` with the real semantic-closure reader (F-2)."""

    db_path = Path(state_db_path)

    async def closure_reader(run_id):  # type: ignore[no-untyped-def]
        return await closure_instruction_for_run(db_path, getattr(run_id, "value", str(run_id)))

    service_context.register("sdk_closure_instruction_reader", closure_reader)

    return ProductRunContextAuthority(
        ports_resolver=ports_resolver,
        exposure_resolver=exposure_resolver,
        ledger=ledger,
        reconcile=reconcile,
        typed_use_authority=typed_use_authority,
        occurrence_coordinator=occurrence_coordinator,
        current_tool_projector=CurrentToolProjector(db_path, lambda: host_state.sdk_runtime_stack),
        closure_reader=closure_reader,
        # HM-TO-A6 incident A: publish this turn's route state to the
        # capability-discovery surface (same value that shrinks the specs).
        route_state_memo=_ensure_run_route_state_memo(),
        # S5b Task 6 (AC-3⑥): ≥2-root scopes never see PROJECT_EFFECT Tools.
        binding_store=WorkspaceBindingAuthorityStore(
            db_path, configured_workspace_root=configured_workspace_root
        ),
    )
