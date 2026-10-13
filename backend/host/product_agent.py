"""产品 Agent：上下文系统恢复、伴侣后台请求判断、产品 Agent 主循环构造"""

from __future__ import annotations

from collections.abc import Mapping

from host.agent_factory import build_agent
from host.llm_providers import cloud_llm
from host.provider_chain import _resolve_agent_provider_chain, _select_code_workflow_provider
from host.runtime import config, service_context
from host.state import host_state
from host.tools_bootstrap import _get_receipt_store, deskpet_tool_registry_v2


def _restore_product_context_os(request):
    """Rebuild one request-scoped tool authority from its durable snapshot."""

    if not bool(getattr(getattr(config, "features", None), "context_os_v1", False)):
        return None
    if _is_companion_background_request(request):
        # Companion reflection/build/evaluation Runs are deliberately
        # zero-tool.  Their trusted host extension is the authority; requiring
        # a foreground Context OS lease here would both invent tools and make
        # durable background recovery impossible.
        return None
    raw = request.request_payload.get("context_os")
    if not isinstance(raw, dict):
        raise RuntimeError("product recovery Context OS snapshot is unavailable")
    from deskpet.agent.assembler.bundle import PreparedContext
    from deskpet.capabilities.refresh import context_os_snapshot_ref
    from deskpet.tools.prepared_snapshot import load_context_os_snapshot

    prepared_tool_set, eligibility = load_context_os_snapshot(raw)
    context = request.run_context
    if context is None:
        raise RuntimeError("product recovery trusted RunContext is unavailable")
    if eligibility.session_id != context.session_id or eligibility.request_id != context.request_id:
        raise RuntimeError("product recovery Context OS identity mismatch")
    deskpet_tool_registry_v2.validate_prepared_tool_set(prepared_tool_set, eligibility=eligibility)
    exact_snapshot_ref = context_os_snapshot_ref(prepared_tool_set, eligibility)
    frozen_snapshot_ref = str(request.capability_snapshot.get("prepared_tool_set_ref") or "")
    payload_snapshot_ref = str(request.request_payload.get("tool_set_snapshot_ref") or "")
    if frozen_snapshot_ref or payload_snapshot_ref:
        if (
            not frozen_snapshot_ref
            or not payload_snapshot_ref
            or frozen_snapshot_ref != exact_snapshot_ref
            or payload_snapshot_ref != exact_snapshot_ref
        ):
            raise RuntimeError("product recovery capability snapshot mismatch")
    else:
        # Compatibility for runs created before the exact catalog-lease
        # snapshot became the production authority.
        expected_names = {
            capability.ref.name
            for capability in (
                *prepared_tool_set.direct,
                *prepared_tool_set.activated,
            )
        }
        raw_names = request.capability_snapshot.get(
            "tools", request.capability_snapshot.get("capabilities", ())
        )
        actual_names = {str(name) for name in raw_names}
        if expected_names != actual_names:
            raise RuntimeError("product recovery capability snapshot mismatch")
    scope_store = service_context.get("tool_capability_scope_store")
    if scope_store is None:
        raise RuntimeError("product recovery capability scope store unavailable")
    existing_scope = scope_store.get(
        prepared_tool_set.scope_id,
        session_id=context.session_id,
        request_id=context.request_id,
    )
    if existing_scope is None:
        try:
            scope_store.open(prepared_tool_set, eligibility)
        except ValueError:
            if (
                scope_store.get(
                    prepared_tool_set.scope_id,
                    session_id=context.session_id,
                    request_id=context.request_id,
                )
                is None
            ):
                raise
    elif (
        existing_scope.prepared.revision != prepared_tool_set.revision
        or existing_scope.prepared.registry_revision != prepared_tool_set.registry_revision
    ):
        scope_store.commit_prevalidated(prepared_tool_set)
    return PreparedContext(
        messages=[dict(message) for message in request.canonical_messages],
        tool_set=prepared_tool_set,
    )


def _is_companion_background_request(request) -> bool:
    context = getattr(request, "run_context", None)
    if (
        context is None
        or str(getattr(context, "venue", "") or "") != "background"
        or not str(getattr(context, "session_id", "") or "").startswith("companion:")
    ):
        return False
    payload = getattr(request, "request_payload", {})
    background = None
    if isinstance(payload, Mapping):
        background = payload.get("companion_background")
        if background is None and isinstance(payload.get("payload"), Mapping):
            # DriverStart recovery carries the sanitized request envelope,
            # whose product payload is nested one level below the request.
            background = payload["payload"].get("companion_background")
    return (
        isinstance(background, Mapping)
        and int(background.get("schema_version") or 0) == 1
        and str(background.get("purpose") or "")
        # Keep this in lockstep with companion.run_adapter._PURPOSES.
        # Candidate authoring is a delegated task whose product stage is
        # ``candidate_build``; treating that stage name as the purpose made
        # both fresh and recovered candidate Runs enter ordinary Context OS.
        in {"reflection", "evaluation", "delegated_task"}
    )


async def _build_product_agent_loop(request):
    """Rehydrate one AgentLoop from durable Run data and live host services."""

    from agent.context_manager import ContextManager
    from agent.tool_use_shim import OpenAICompatibleAgentLLM

    context = request.run_context
    if context is None:
        raise ValueError("product AgentLoop requires a trusted RunContext")
    from deskpet.execution.provider_workloads import workload_context

    def _run_workload_context(callsite_id: str):
        return workload_context(
            callsite_id,
            request_id=context.request_id,
            session_id=context.session_id,
            root_run_id=context.root_run_id,
            task_scope_id=context.turn_id,
        )

    session_db = service_context.get("session_db")
    providers = await _resolve_agent_provider_chain(
        context.session_id,
        session_db=session_db,
    )
    primary = (
        providers[0]
        if providers
        else _select_code_workflow_provider(
            local_llm=host_state.local_llm,
            cloud_llm=cloud_llm,
        )
    )
    if primary is None:
        raise RuntimeError("no LLM provider is available")
    workspace = context.workspace.get("root")
    manager_config = (config.raw.get("context") or {}).get("manager") or {}
    context_manager = ContextManager.for_session(
        model=str(getattr(primary, "model", "") or "_default"),
        project_root=workspace,
        v2_enabled=bool(manager_config.get("v2_enabled", True)),
        adaptive_compact_pct=bool(
            getattr(getattr(config, "features", None), "adaptive_compact_pct", False)
        ),
    )
    loop_options = dict(request.request_payload.get("loop") or {})
    companion_background = _is_companion_background_request(request)
    prepared_context = _restore_product_context_os(request)
    problem_type = loop_options.get("pipeline_problem_type")
    response_quality_gate = None
    response_quality = loop_options.get("response_quality")
    if (
        not companion_background
        and isinstance(response_quality, Mapping)
        and response_quality.get("mode") == "semantic_completeness"
        and response_quality.get("preference_key") == "response.detail"
        and response_quality.get("preference_value") == "brief"
    ):
        from deskpet.companion.response_quality import (
            ModelResponseCompletenessGate,
        )

        response_quality_gate = ModelResponseCompletenessGate()
    frozen_skill_resolver = service_context.get("frozen_skill_instruction_resolver")
    if frozen_skill_resolver is None and not companion_background:
        raise RuntimeError("frozen_skill_resolver_not_injected")
    run_tool_registry = deskpet_tool_registry_v2
    if companion_background:
        from deskpet.tools.registry import ToolRegistry

        # A fresh empty registry makes the zero-tool contract true in the
        # model prompt as well as at execution admission.
        run_tool_registry = ToolRegistry()
    agent = build_agent(
        config,
        llm_registry=OpenAICompatibleAgentLLM(primary),
        tool_registry=run_tool_registry,
        context_manager=context_manager,
        receipt_store_getter=_get_receipt_store,
        max_iterations=50,
        completion_probe=None,
        session_todo_getter=None,
        max_completion_nudges=2,
        signature_repeat_threshold=int(
            (config.raw.get("supervisor") or {}).get("tool_signature_repeat_threshold", 3)
        ),
        session_goal_store=service_context.get("session_goal_store"),
        goal_checker=service_context.get("goal_checker"),
        compressor=service_context.get("context_compressor"),
        skill_loader=(None if companion_background else frozen_skill_resolver),
        skill_matcher=(None if companion_background else service_context.get("skill_matcher")),
        memory_curator=(None if companion_background else service_context.get("memory_curator")),
        provider_workload_context_factory=_run_workload_context,
        provider_workload_router=service_context.get("provider_workload_router"),
        context_attempt_store=service_context.get("context_attempt_store"),
        evidence_gate=(
            service_context.get("pipeline_evidence_gate") if problem_type is not None else None
        ),
        pipeline_problem_type=problem_type,
        pipeline_needs_investigation=bool(loop_options.get("pipeline_needs_investigation", False)),
        pipeline_observability=bool(loop_options.get("pipeline_observability", False)),
        convergence_report_on_stop=bool(loop_options.get("convergence_report_on_stop", False)),
        response_quality_gate=response_quality_gate,
        # Companion reflection/candidate/evaluation Runs are deliberately
        # zero-tool and return host-validated structured JSON.  The ordinary
        # claim/receipt VerifyGate assumes an effect-capable ReAct answer and
        # can only manufacture unmatched claims here, so keep that gate on
        # the foreground/product path and let the Companion terminal
        # extensions plus evaluation pipeline validate background results.
        enable_verify_gate=not companion_background,
    )
    return (
        agent,
        {
            "provider_chain": providers,
            "loop_user_request": loop_options.get("user_request"),
            "is_sentinel_run": bool(loop_options.get("is_sentinel_run", False)),
            "host_validated_zero_tool_terminal": companion_background,
            "context_request_id": context.request_id,
            "prepared_context": prepared_context,
        },
    )
