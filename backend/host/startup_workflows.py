"""启动步骤：会话库检查与工作流服务接线"""

from __future__ import annotations

import asyncio
import hashlib
import hashlib as _hashlib
import json
import re
from pathlib import Path

import paths as _paths
from providers.openai_compatible import OpenAICompatibleProvider

from host.connections import _control_connections
from host.llm_providers import _sanitize_cot_dsml, cloud_llm
from host.ppt_outline import _configure_ppt_production
from host.provider_chain import (
    _resolve_code_recovery_provider,
    _validate_code_recovery_capability_subset,
)
from host.runtime import SHARED_SECRET, config, logger, service_context
from host.state import host_state
from host.stores_bootstrap import (
    _project_binding_service,
    _provider_workload_maintenance_tasks,
    _session_db,
    _state_db_path,
)
from host.tools_bootstrap import _get_receipt_store, deskpet_tool_registry_v2


async def _startup_workflow_services() -> Any:
    """启动：会话库检查与工作流服务接线（单条 1421 行的语句整段搬家，拆分在后面的阶段）。"""
    # P4-S13: async initialisers for the P4 read-only stack. Each failure is
    # isolated — MemoryManager needing a base dir doesn't prevent SkillLoader
    # from scanning the built-in dir, etc.
    _sdb = service_context.get("session_db")
    if _sdb is not None:
        try:
            await _sdb.initialize()
            logger.info("p4_session_db_ready", path=str(_sdb._db_path))
        except Exception as exc:
            logger.warning("p4_session_db_init_failed", error=str(exc))
    try:
        from deskpet.workflows.bootstrap import build_workflow_service
        from deskpet.workflows.retention import RetentionPolicy
        from deskpet.workflows.store import RegisteredBlobStore

        _workflow_delivery_blobs = RegisteredBlobStore(
            _paths.user_data_dir() / "workflows" / "blobs",
            _paths.user_data_dir() / "data" / "workflow.db",
        )

        async def _resolve_workflow_content(ref: str) -> bytes:
            wire_ref = str(ref or "").strip()
            digest = wire_ref[7:] if wire_ref.startswith("sha256:") else wire_ref
            return await _workflow_delivery_blobs.get(digest)

        async def _workflow_delivery_epoch(event, delivery) -> int | None:
            if _sdb is None or _workflow_service is None:
                return None
            run_id = str(event.get("run_id") or "")
            target_sid = str(delivery.get("target_id") or "default")
            ref = await _workflow_service.run_store.get_session_ref(run_id, "delivery")
            if ref is None or str(ref["session_id"]) != target_sid:
                return None
            state = await _sdb.get_session_delivery_state(target_sid)
            if state.get("deleted_at") is not None:
                return None
            expected_epoch = int(ref["session_epoch"])
            if int(state.get("epoch", 0)) != expected_epoch:
                return None
            return expected_epoch

        async def _workflow_session_delivery(event, delivery):
            from deskpet.workflows.delivery import DeliveryAttemptResultV1

            target_sid = str(delivery.get("target_id") or "default")
            expected_epoch = await _workflow_delivery_epoch(event, delivery)
            if expected_epoch is None:
                return DeliveryAttemptResultV1.discarded_fenced("session_epoch_mismatch")
            payload = dict(event.get("payload") or {})
            nested = payload.get("payload")
            nested = dict(nested) if isinstance(nested, dict) else {}
            text = ""
            from deskpet.workflows.adapters.product_delivery import (
                resolve_workflow_content_text,
                workflow_message_projection,
            )

            content_payload = nested if nested.get("content_ref") else payload
            resolved = await resolve_workflow_content_text(
                content_payload, _resolve_workflow_content
            )
            if resolved is not None:
                text = resolved
            if event.get("event_type") == "workflow.progress":
                v2_payload = payload if payload.get("schema_version") == 2 else nested
                if v2_payload.get("schema_version") == 2:
                    from deskpet.workflows.progress import validated_v2_stage_text

                    text = validated_v2_stage_text(v2_payload).strip()
            if not text:
                text = str(nested.get("text") or payload.get("text") or "").strip()
            if not text:
                if event.get("event_type") == "workflow.accepted":
                    text = f"{payload.get('workflow_name', '任务')} 已开始"
                elif event.get("event_type") == "workflow.decision":
                    text = "Workflow decision is waiting for your confirmation."
                elif event.get("event_type") == "workflow.final":
                    text = f"任务已结束：{payload.get('status', 'unknown')}"
                else:
                    return DeliveryAttemptResultV1.retryable_failure("projection_content_missing")
            if _sdb is not None:
                projection = workflow_message_projection(str(event.get("event_type") or ""))
                message_id = await _sdb.append_message_if_epoch(
                    target_sid,
                    projection.role,
                    text,
                    expected_epoch=expected_epoch,
                    workflow_event_id=str(event["event_id"]),
                    projection_kind=projection.projection_kind,
                    context_visibility=projection.context_visibility,
                    skip_embed=projection.skip_embed,
                    root_run_id=(str(event.get("root_run_id") or "").strip() or None),
                )
                if message_id is None:
                    return DeliveryAttemptResultV1.discarded_fenced("session_epoch_mismatch")
                return DeliveryAttemptResultV1.delivered()
            return DeliveryAttemptResultV1.retryable_failure("session_db_unavailable")

        async def _workflow_websocket_delivery(event, delivery):
            from deskpet.workflows.delivery import DeliveryAttemptResultV1

            if await _workflow_delivery_epoch(event, delivery) is None:
                return DeliveryAttemptResultV1.discarded_fenced("session_epoch_mismatch")
            from deskpet.workflows.adapters.product_delivery import (
                resolve_workflow_content_text,
            )

            outbound_event = dict(event)
            outbound_payload = dict(event.get("payload") or {})
            nested = outbound_payload.get("payload")
            content_payload = (
                dict(nested)
                if isinstance(nested, dict) and nested.get("content_ref")
                else outbound_payload
            )
            resolved = await resolve_workflow_content_text(
                content_payload, _resolve_workflow_content
            )
            if resolved is not None:
                # Resolved bytes are transport-only.  The immutable outbox row
                # remains ref-only and therefore replay-safe.
                outbound_payload["text"] = resolved
            outbound_event["payload"] = outbound_payload
            envelope_type = (
                "workflow_final"
                if event.get("event_type") == "workflow.final"
                else "workflow_event"
            )
            envelope = {
                "type": envelope_type,
                "payload": {
                    **outbound_event,
                    "session_id": str(delivery.get("target_id") or "default"),
                },
            }
            from deskpet.workflows.delivery import broadcast_websocket_best_effort

            return await broadcast_websocket_best_effort(
                envelope, list(_control_connections.values())
            )

        async def _workflow_artifact_publisher(payload):
            envelope = {"type": "tool_result", "payload": dict(payload)}
            seen: set[int] = set()
            for target_ws in list(_control_connections.values()):
                marker = id(target_ws)
                if marker in seen:
                    continue
                seen.add(marker)
                await asyncio.wait_for(target_ws.send_json(envelope), timeout=1.0)

        product_handlers = {}
        if _sdb is not None:
            from deskpet.workflows.adapters.product_delivery import ProductDeliveryAdapter
            from deskpet.workflows.store import WorkflowRunStore

            product_delivery = ProductDeliveryAdapter(
                session_db=_sdb,
                receipt_store=_get_receipt_store(),
                workflow_store=WorkflowRunStore(_paths.user_data_dir() / "data" / "workflow.db"),
                artifact_publisher=_workflow_artifact_publisher,
                content_resolver=_resolve_workflow_content,
            )
            product_handlers = product_delivery.handlers()

        _workflow_service = await build_workflow_service(
            _paths.user_data_dir(),
            delivery_handlers={
                "session_message": _workflow_session_delivery,
                "websocket": _workflow_websocket_delivery,
                **product_handlers,
            },
            retention_policy=RetentionPolicy.from_days(
                terminal_days=config.workflows.terminal_retention_days,
                evaluation_tombstone_days=config.workflows.evaluation_retention_days,
                orphan_grace_hours=config.workflows.orphan_grace_hours,
            ),
            session_delivery_state_reader=(
                _sdb.get_session_delivery_state if _sdb is not None else None
            ),
            activate=False,
        )
        from deskpet.tools import research_tools as _workflow_research_tools
        from deskpet.workflows.contracts import WorkflowContext
        from deskpet.workflows.definitions.research_core import (
            FetchPort,
            ResearchArtifactPort,
            ResearchSearchPort,
            ResearchSearchResults,
            legacy_ports,
        )
        from deskpet.workflows.definitions.v1 import deep_research_initial_state
        from deskpet.workflows.definitions.v2 import (
            deep_research_initial_state as deep_research_v2_initial_state,
        )
        from deskpet.workflows.definitions.v3 import (
            deep_research_initial_state as deep_research_v3_initial_state,
        )
        from deskpet.workflows.definitions.v4 import (
            deep_research_initial_state as deep_research_v4_initial_state,
        )
        from deskpet.workflows.definitions.v5 import (
            deep_research_initial_state as deep_research_v5_initial_state,
        )
        from deskpet.workflows.definitions.v6 import (
            build_continuation_start_payload as build_deep_research_v6_continuation_payload,
        )
        from deskpet.workflows.definitions.v6 import (
            decode_continuation_snapshot as decode_deep_research_v6_continuation_snapshot,
        )
        from deskpet.workflows.definitions.v6 import (
            deep_research_initial_state as deep_research_v6_initial_state,
        )
        from deskpet.workflows.definitions.v7 import (
            deep_research_initial_state as deep_research_v7_initial_state,
        )
        from deskpet.workflows.launcher import WorkflowLauncher
        from deskpet.workflows.native import NativeExecutionPolicy
        from deskpet.workflows.runtime_adapters import (
            DEEP_RESEARCH_EXTENSION,
            DeepResearchContinuationAdapter,
            DeepResearchRuntimeExtension,
        )
        from deskpet.workflows.terminal_projection import TERMINAL_COMMIT_CAPABILITY

        _workflow_launcher = WorkflowLauncher(_workflow_service)
        _workflow_service.launcher = _workflow_launcher
        # Runtime adapters must all be registered before the production
        # registry is sealed and any durable recovery is allowed to run.
        service_context.register("workflow_service", _workflow_service)
        from deskpet.execution.harness_public_read_service import (
            HarnessPublicReadService,
        )

        _harness_public_read_service = HarnessPublicReadService(
            workflow_db_path=_paths.user_data_dir() / "data" / "workflow.db",
            state_db_path=_state_db_path,
            cursor_secret=hashlib.sha256(f"harness-inspector-v3:{SHARED_SECRET}".encode()).digest(),
        )
        service_context.register("harness_public_read_service", _harness_public_read_service)
        from deskpet.execution.fences import UnboundRunExecutionFence
        from deskpet.execution.provider_fault_script import ProviderFaultScriptV1
        from deskpet.execution.provider_invocations import (
            ProviderInvocationCoordinator,
        )

        _execution_fence = UnboundRunExecutionFence()
        _provider_fault_script = ProviderFaultScriptV1.from_environment()
        from deskpet.execution.tool_completion_latch_script import (
            ToolCompletionLatchScriptV1,
        )
        from deskpet.tools import registry as _runtime_tool_registry

        _runtime_tool_registry.set_tool_completion_latch(
            ToolCompletionLatchScriptV1.from_environment()
        )
        _provider_invocation_coordinator = ProviderInvocationCoordinator(
            _workflow_service.execution_uow,
            fence_reacquirer=_execution_fence.acquire,
            fault_script=_provider_fault_script,
        )
        service_context.register("run_execution_fence_acquirer", _execution_fence.acquire)
        service_context.register(
            "provider_invocation_coordinator",
            _provider_invocation_coordinator,
        )
        from deskpet.execution.provider_workload_audit import (
            ProviderWorkloadAuditStore,
        )
        from deskpet.execution.provider_workloads import (
            BackgroundModelPolicy,
            ProviderWorkloadBreaker,
            ProviderWorkloadRoutePolicy,
            ProviderWorkloadRouter,
            SessionProviderWorkloadTargetResolver,
        )

        _provider_registry_for_workloads = service_context.get("provider_registry")

        def _provider_workload_factory(entry, model):
            if _provider_registry_for_workloads is None:
                raise RuntimeError("provider registry is unavailable")
            provider = OpenAICompatibleProvider(
                base_url=entry.base_url,
                api_key=(_provider_registry_for_workloads.resolve_api_key(entry.id) or "ollama"),
                model=model,
                temperature=getattr(entry, "temperature", 0.7),
                sanitize_inline_cot_dsml=_sanitize_cot_dsml,
                code_params=getattr(entry, "code_params", None),
                is_relay=(getattr(entry, "source", "") == "relay"),
            )
            provider.provider_id = str(entry.id)
            return provider

        _provider_workload_audit = ProviderWorkloadAuditStore(_state_db_path)
        _session_workload_resolver = SessionProviderWorkloadTargetResolver(
            registry=_provider_registry_for_workloads,
            session_db=_session_db,
            readiness=getattr(service_context, "provider_routing_readiness", None),
            provider_factory=_provider_workload_factory,
            start_snapshot_reader=_workflow_service.execution_uow,
        )
        _background_model_policy = BackgroundModelPolicy(
            registry=_provider_registry_for_workloads,
            readiness=getattr(service_context, "provider_routing_readiness", None),
            provider_factory=_provider_workload_factory,
        )
        _provider_workload_router = ProviderWorkloadRouter(
            ProviderWorkloadRoutePolicy(
                session=_session_workload_resolver,
                background=_background_model_policy,
            ),
            breaker=ProviderWorkloadBreaker(),
            audit=_provider_workload_audit,
            fault_script=_provider_fault_script,
        )
        service_context.register("provider_workload_audit", _provider_workload_audit)
        service_context.register("provider_workload_router", _provider_workload_router)
        if _provider_fault_script is not None:
            from deskpet.execution.provider_fault_scenario_runner import (
                ProviderFaultScenarioRunner,
            )

            _provider_fault_scenario_runner = ProviderFaultScenarioRunner(
                _provider_fault_script,
                _provider_workload_router,
                task_owner=_provider_workload_maintenance_tasks,
            )
            await _provider_fault_scenario_runner.start()

        async def _deep_context_factory(row, start_payload) -> WorkflowContext:
            llm_call = await _workflow_research_tools._resolve_default_llm_call()
            ports = legacy_ports(llm_call=llm_call, search=None, extract=None)
            return WorkflowContext(
                ports={"llm": ports.llm, "search": ports.search, "fetch": ports.fetch},
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
            )

        def _deep_state_factory(**values):
            return deep_research_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=dict(values.get("research_config") or {}),
                blob_root=str(values.get("blob_root") or ""),
            )

        async def _deep_v2_context_factory(row, start_payload) -> WorkflowContext:
            from deskpet.retrieval.contracts import FetchRequest, SearchRequest
            from deskpet.retrieval.runtime import get_default_gateway

            llm_call = await _workflow_research_tools._resolve_default_llm_call()
            legacy = legacy_ports(llm_call=llm_call, search=None, extract=None)
            gateway = get_default_gateway()
            run_id = str(row.get("run_id") or "")

            async def _search(query: str, *, max_results: int) -> list[dict]:
                response = await gateway.search(
                    SearchRequest(
                        query=query,
                        max_results=max_results,
                        mode="research",
                        run_id=run_id,
                    )
                )
                payload = response.to_dict()
                attempts = list(payload.get("attempts") or [])
                public_codes = [
                    str(value.get("public_error_code"))
                    for value in attempts
                    if isinstance(value, dict) and value.get("public_error_code")
                ]
                coverage = dict(response.coverage())
                safe_attempts = [
                    {
                        "provider": str(value.get("provider") or ""),
                        "status": str(value.get("status") or ""),
                        "permit": str(value.get("permit") or "closed"),
                        "probe_outcome": (
                            str(value.get("probe_outcome")) if value.get("probe_outcome") else None
                        ),
                        "upstream_called": bool(value.get("upstream_called", False)),
                        "is_rescue": bool(value.get("is_rescue", False)),
                    }
                    for value in attempts
                    if isinstance(value, dict)
                ]
                return ResearchSearchResults(
                    list(payload["results"]),
                    observation={
                        "request_id": str(payload.get("request_id") or ""),
                        "run_id": str(payload.get("run_id") or run_id),
                        "degraded": bool(response.degraded),
                        "reason_code": public_codes[-1] if public_codes else None,
                        "engines_tried": list(response.engines_tried),
                        "engines_hit": list(response.engines_hit),
                        "provider_attempt_count": int(coverage["actual_requests"]),
                        "provider_probe_count": int(coverage["probes"]),
                        "provider_attempts": safe_attempts,
                        "rescue_status": str(response.rescue_status),
                        "rescue_error_code": (
                            response.rescue_error_code.value
                            if response.rescue_error_code is not None
                            else None
                        ),
                        "rescue_upstream_called": bool(response.rescue_upstream_called),
                        "coverage": coverage,
                    },
                )

            async def _extract(url: str) -> dict:
                if gateway.fetch_service is None:
                    return await legacy.fetch.extract(url)
                document = await gateway.fetch_service.fetch(
                    FetchRequest(url=url, timeout=15.0, render_policy="auto")
                )
                return document.to_dict()

            async def _save_artifact(
                *, topic: str, report_md: str, report_hash: str, run_id: str
            ) -> dict[str, object]:
                return await asyncio.to_thread(
                    _workflow_research_tools.save_workflow_report,
                    topic=topic,
                    report_md=report_md,
                    report_hash=report_hash,
                    run_id=run_id,
                )

            blob_root = _paths.user_data_dir() / "workflows" / "blobs"
            return WorkflowContext(
                ports={
                    "llm": legacy.llm,
                    "search": ResearchSearchPort(
                        search_call=_search,
                        direct_call=legacy.search.direct_call,
                        reset_runtime=legacy.search.reset_runtime,
                    ),
                    "fetch": FetchPort(_extract),
                    "artifact": ResearchArtifactPort(_save_artifact),
                    "blob": RegisteredBlobStore(
                        blob_root,
                        _workflow_service.run_store.path,
                    ),
                    "native_execution_policy": NativeExecutionPolicy(
                        max_parallel_tasks=max(
                            2,
                            min(
                                6,
                                int(config.workflows.deep_research_max_parallel_tasks),
                            ),
                        )
                    ),
                },
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
            )

        async def _deep_v7_context_factory(row, start_payload) -> WorkflowContext:
            base = await _deep_v2_context_factory(row, start_payload)
            scheduler = service_context.get("subagent_scheduler")
            if scheduler is None:
                raise RuntimeError("subagent_scheduler_unavailable")
            return WorkflowContext(
                ports={**base.ports, "subagent_scheduler": scheduler},
                request_id=base.request_id,
                turn_id=base.turn_id,
            )

        async def _deep_v5_context_factory(row, start_payload) -> WorkflowContext:

            from deskpet.retrieval.contracts import (
                DimensionSearchRequest,
                FetchRequest,
                SearchRequest,
            )
            from deskpet.retrieval.query_terms import extract_query_terms
            from deskpet.retrieval.runtime import get_default_gateway
            from deskpet.workflows.adapters.deep_research_v5_evidence_runtime import (
                candidate_from_document,
                candidate_from_json,
                evaluate_runtime_evidence,
                select_dimension_fair_rows,
            )
            from deskpet.workflows.adapters.research_runtime import (
                BoundResearchEffectContext,
                DurableResearchCallEffectAdapter,
                DurableResearchLLMStagePort,
                DurableResearchReadEffectAdapter,
                DurableResearchReadStagePort,
                DurableResearchSnapshotPort,
                DurableV5ControlPort,
                MonotonicResearchClock,
                WorkflowControlSignalHub,
            )
            from deskpet.workflows.contracts import canonical_json
            from deskpet.workflows.definitions.deep_research_v5_contracts import ResearchBrief
            from deskpet.workflows.definitions.research_core import ResearchLLMPortV2
            from deskpet.workflows.effects import EffectExecutionContext, EffectJournal
            from deskpet.workflows.store import RunFence

            run_id = str(row.get("run_id") or "")
            session_id = str(row.get("session_id") or "")
            db_path = _workflow_service.run_store.path
            blobs = RegisteredBlobStore(_paths.user_data_dir() / "workflows" / "blobs", db_path)
            journal = EffectJournal(db_path)
            repository = _workflow_service.research_repository
            signals = WorkflowControlSignalHub(repository)

            async def _effect_context(identity):
                current = await _workflow_service.run_store.get_run(identity.run_id)
                if current is None or str(current.get("status")) != "running":
                    raise RuntimeError("v5 research effect run is not active")
                owner = str(current.get("lease_owner") or "")
                if not owner:
                    raise RuntimeError("v5 research effect run has no lease owner")
                digest = _hashlib.sha256(
                    f"{identity.run_id}:{identity.checkpoint_ns}:{identity.checkpoint_id}:"
                    f"{identity.task_id}:{identity.attempt}".encode()
                ).hexdigest()
                context = EffectExecutionContext(
                    journal=journal,
                    fence=RunFence(
                        identity.run_id,
                        owner,
                        int(current.get("lease_epoch") or 0),
                        int(current.get("run_version") or 0),
                    ),
                    node_execution_id=f"research-v5-{digest[:32]}",
                    workflow_name="deep_research",
                    workflow_version="v5",
                    node_id=identity.node_id,
                )
                return BoundResearchEffectContext(identity, context)

            llm_call = await _workflow_research_tools._resolve_default_llm_call_v2()
            llm_effect = DurableResearchCallEffectAdapter(
                journal=journal,
                blobs=blobs,
                llm=ResearchLLMPortV2(llm_call),
                resolve_effect_context=_effect_context,
                reserve_cost_micros=lambda _role, _input, output: max(0, output * 20),
                actual_cost_micros=lambda result: max(
                    0, int((result.input_tokens or 0) * 3 + (result.output_tokens or 0) * 15)
                ),
                control_signals=signals,
            )

            gateway = get_default_gateway()

            async def _search_transport(payload, identity):
                if payload.get("_stage") == "direct":
                    return {"results": [], "executed_query_fingerprints": []}
                brief_raw = payload.get("research_brief")
                brief = ResearchBrief.from_json(brief_raw) if isinstance(brief_raw, dict) else None
                dimensions = (
                    {item.dimension_id: item for item in brief.dimensions}
                    if brief is not None
                    else {}
                )
                raw_queries = (
                    payload.get("initial_queries") or payload.get("query_strategy_queries") or []
                )
                query_rows = (
                    [item for item in raw_queries if isinstance(item, dict)]
                    if isinstance(raw_queries, list)
                    else []
                )
                work = payload.get("work_item")
                if isinstance(work, dict) and work.get("query"):
                    query_rows.append(
                        {
                            "dimension_id": work.get("dimension_id"),
                            "query": work.get("query"),
                            "source_target": work.get("source_target"),
                            "fingerprint": payload.get("query_fingerprint"),
                        }
                    )
                requests = []
                metadata = {}
                for index, item in enumerate(query_rows):
                    query = str(item.get("query") or "").strip()
                    dimension_id = str(item.get("dimension_id") or "").strip()
                    if not query or not dimension_id or dimension_id not in dimensions:
                        continue
                    fingerprint = (
                        str(item.get("fingerprint") or "").strip()
                        or _hashlib.sha256(query.casefold().encode("utf-8")).hexdigest()
                    )
                    dimension = dimensions[dimension_id]
                    semantic_context = " ".join(
                        (
                            brief.user_question if brief is not None else "",
                            dimension.question,
                            *(brief.subjects if brief is not None else ()),
                            *dimension.query_targets,
                            query,
                        )
                    )
                    semantic_terms = extract_query_terms(semantic_context)
                    request = SearchRequest(
                        query=query,
                        max_results=10,
                        mode="research",
                        run_id=identity.run_id,
                        dimension_id=dimension_id,
                        query_terms=semantic_terms,
                    )
                    requests.append(
                        DimensionSearchRequest(
                            dimension_id=dimension_id,
                            request=request,
                            core=dimension.importance == "core",
                            priority=100 - index,
                            query_terms=semantic_terms,
                            query_fingerprint=fingerprint,
                        )
                    )
                    metadata[(dimension_id, query)] = {
                        "query": query,
                        "query_fingerprint": fingerprint,
                        "source_target": str(item.get("source_target") or ""),
                    }
                batch = await gateway.search_dimension_batch(
                    requests,
                    min_results_per_core=1,
                )
                results = []
                for dimension_result in batch.results:
                    response = dimension_result.response
                    meta = metadata.get(
                        (dimension_result.dimension_id, response.query),
                        {"query": response.query, "query_fingerprint": "", "source_target": ""},
                    )
                    for candidate in response.to_dict().get("results") or []:
                        if isinstance(candidate, dict):
                            results.append(
                                {
                                    **candidate,
                                    "dimension_id": dimension_result.dimension_id,
                                    **meta,
                                }
                            )
                output = {
                    "results": results,
                    "executed_query_fingerprints": sorted(
                        {request.budget_query_key for request in requests}
                    ),
                    # The allocator keeps immutable tuples internally, while
                    # durable effect outcomes require strict JSON.  Reuse the
                    # retrieval contract's canonical projection instead of
                    # leaking the internal budget snapshot into workflow state.
                    "budget": batch.to_dict()["budget"],
                }
                # A gap unit is atomic search+fetch+admit under this durable
                # effect fence.  Returning only URLs would make gap_join claim
                # progress without adding any usable evidence.
                if payload.get("_stage") == "gap_work":
                    return await _fetch_transport({**payload, "gap_work_result": output}, identity)
                return output

            search_effect = DurableResearchReadEffectAdapter(
                journal=journal,
                blobs=blobs,
                resolve_effect_context=_effect_context,
                transport=_search_transport,
                control_signals=signals,
            )

            async def _fetch_transport(payload, identity):
                rows = []
                for key in ("search_result", "direct_result", "gap_work_result"):
                    value = payload.get(key)
                    if isinstance(value, dict) and isinstance(value.get("results"), list):
                        rows.extend(item for item in value["results"] if isinstance(item, dict))
                brief_raw = payload.get("research_brief")
                if not isinstance(brief_raw, dict):
                    raise ValueError("v5 evidence fetch requires research_brief")
                brief = ResearchBrief.from_json(brief_raw)
                dimension_by_id = {item.dimension_id: item for item in brief.dimensions}
                documents = []
                current_candidates = []
                fetch_rows = select_dimension_fair_rows(
                    rows,
                    tuple(dimension_by_id),
                    limit=24,
                )
                for raw in fetch_rows:
                    url = str(raw.get("url") or "")
                    dimension_id = str(raw.get("dimension_id") or "")
                    if not url or dimension_id not in dimension_by_id:
                        continue
                    try:
                        document = (
                            await gateway.fetch_service.fetch(
                                FetchRequest(
                                    url=url,
                                    timeout=20.0,
                                    render_policy="auto",
                                    run_id=identity.run_id,
                                )
                            )
                            if gateway.fetch_service is not None
                            else None
                        )
                    except Exception:
                        continue
                    if document is not None:
                        value = document.to_dict()
                        documents.append(value)
                        dimension = dimension_by_id[dimension_id]
                        year_anchors = (
                            tuple(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", brief.user_question))
                            if "official_statistics" in dimension.expected_source_types
                            else ()
                        )
                        current_candidates.append(
                            candidate_from_document(
                                dimension_id=dimension_id,
                                dimension_text=" ".join(
                                    (
                                        dimension.question,
                                        *dimension.query_targets,
                                    )
                                ),
                                query=str(raw.get("query") or ""),
                                document=value,
                                subjects=brief.subjects,
                                required_anchors=year_anchors,
                            )
                        )
                prior_candidates = []
                for value in payload.get("evidence_candidates", []):
                    if isinstance(value, dict):
                        prior_candidates.append(candidate_from_json(value))
                evaluated = evaluate_runtime_evidence(
                    brief=brief,
                    candidates=(*prior_candidates, *current_candidates),
                )
                ref_by_candidate = {}
                current_by_id = {item.candidate_id: item for item in current_candidates}
                for candidate_value in evaluated["evidence_candidates"]:
                    if not candidate_value.get("admitted"):
                        continue
                    candidate_id = str(candidate_value["candidate_id"])
                    candidate = current_by_id.get(candidate_id)
                    if candidate is None:
                        continue
                    document = next(
                        (
                            item
                            for item in documents
                            if str(item.get("canonical_url") or item.get("url") or "")
                            == str(candidate.canonical_url or candidate.url)
                        ),
                        None,
                    )
                    if document is None:
                        continue
                    encoded = canonical_json(document).encode("utf-8")
                    ref = await blobs.put(
                        encoded,
                        identity,
                        media_type="application/vnd.deskpet.research-evidence+json",
                    )
                    ref_by_candidate[candidate_id] = ref.sha256
                passage_refs = sorted(
                    set(
                        [str(item) for item in payload.get("passage_blob_refs", [])]
                        + list(ref_by_candidate.values())
                    )
                )
                executed = sorted(
                    set(
                        [str(item) for item in payload.get("executed_query_fingerprints", [])]
                        + [
                            str(item)
                            for key in ("search_result", "gap_work_result")
                            for item in (
                                payload.get(key, {}).get("executed_query_fingerprints", [])
                                if isinstance(payload.get(key), dict)
                                else []
                            )
                        ]
                    )
                )
                return {
                    "documents": documents,
                    **evaluated,
                    "passage_blob_refs": passage_refs,
                    "executed_query_fingerprints": executed,
                    "budget_summary": {"io_documents": len(documents)},
                }

            fetch_effect = DurableResearchReadEffectAdapter(
                journal=journal,
                blobs=blobs,
                resolve_effect_context=_effect_context,
                transport=_fetch_transport,
                control_signals=signals,
            )

            class _ArtifactStagePort:
                async def execute(self, *, stage, payload, identity):
                    synthesis = payload.get("synthesis_result")
                    synthesis = synthesis if isinstance(synthesis, dict) else {}
                    report_md = str(synthesis.get("report_md") or synthesis.get("summary") or "")
                    if not report_md:
                        raise ValueError("v5 report synthesis produced no report markdown")
                    report_hash = _hashlib.sha256(report_md.encode("utf-8")).hexdigest()
                    saved = await asyncio.to_thread(
                        _workflow_research_tools.save_workflow_report,
                        topic=str(payload.get("topic") or "research"),
                        report_md=report_md,
                        report_hash=report_hash,
                        run_id=run_id,
                    )
                    return {**saved, "report_ref": f"sha256:{saved['sha256']}"}

            return WorkflowContext(
                ports={
                    "llm": DurableResearchLLMStagePort(blobs=blobs, effect=llm_effect),
                    "search": DurableResearchReadStagePort(
                        effect=search_effect, effect_name="search"
                    ),
                    "fetch": DurableResearchReadStagePort(
                        effect=fetch_effect, effect_name="static_fetch"
                    ),
                    "artifact": _ArtifactStagePort(),
                    "control": DurableV5ControlPort(repository, signal_hub=signals),
                    "snapshot": DurableResearchSnapshotPort(blobs=blobs, repository=repository),
                    "clock": MonotonicResearchClock(),
                    "native_execution_policy": NativeExecutionPolicy(
                        max_parallel_tasks=max(
                            2, min(6, int(config.workflows.deep_research_max_parallel_tasks))
                        )
                    ),
                },
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
            )

        async def _deep_v6_context_factory(row, start_payload) -> WorkflowContext:

            from deskpet.retrieval.runtime import get_default_gateway
            from deskpet.workflows.adapters.deep_research_v6_bootstrap import (
                build_deep_research_v6_context,
            )
            from deskpet.workflows.adapters.research_runtime import (
                BoundResearchEffectContext,
                WorkflowControlSignalHub,
            )
            from deskpet.workflows.effects import EffectExecutionContext, EffectJournal
            from deskpet.workflows.store import RunFence

            run_id = str(row.get("run_id") or "")
            db_path = _workflow_service.run_store.path
            blobs = RegisteredBlobStore(
                _paths.user_data_dir() / "workflows" / "blobs",
                db_path,
            )
            journal = EffectJournal(db_path)
            repository = _workflow_service.research_repository
            signals = WorkflowControlSignalHub(repository)
            await repository.ensure_snapshot_lineage(
                run_id=run_id,
                operation_id=f"research:{run_id}",
                budget_lease_id=_hashlib.sha256(
                    f"deep-research-v6-root-budget|{run_id}".encode()
                ).hexdigest(),
            )

            async def _effect_context(identity):
                current = await _workflow_service.run_store.get_run(identity.run_id)
                if current is None or str(current.get("status")) != "running":
                    raise RuntimeError("v6 research effect run is not active")
                owner = str(current.get("lease_owner") or "")
                if not owner:
                    raise RuntimeError("v6 research effect run has no lease owner")
                digest = _hashlib.sha256(
                    f"{identity.run_id}:{identity.checkpoint_ns}:{identity.checkpoint_id}:"
                    f"{identity.task_id}:{identity.attempt}".encode()
                ).hexdigest()
                context = EffectExecutionContext(
                    journal=journal,
                    fence=RunFence(
                        identity.run_id,
                        owner,
                        int(current.get("lease_epoch") or 0),
                        int(current.get("run_version") or 0),
                    ),
                    node_execution_id=f"research-v6-{digest[:32]}",
                    workflow_name="deep_research",
                    workflow_version="v6",
                    node_id=identity.node_id,
                )
                return BoundResearchEffectContext(identity, context)

            llm_call = await _workflow_research_tools._resolve_default_llm_call_v2()
            return build_deep_research_v6_context(
                blobs=blobs,
                journal=journal,
                resolve_effect_context=_effect_context,
                search_gateway=get_default_gateway(),
                llm_call=llm_call,
                control_signals=signals,
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
                max_parallel_tasks=int(config.workflows.deep_research_max_parallel_tasks),
                dispatch_fence_acquirer=service_context.get("run_execution_fence_acquirer"),
                provider_invocation_coordinator=service_context.get(
                    "provider_invocation_coordinator"
                ),
            )

        def _deep_v2_state_factory(**values):
            return deep_research_v2_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=dict(values.get("research_config") or {}),
                blob_root=str(values.get("blob_root") or ""),
            )

        def _deep_v3_state_factory(**values):
            return deep_research_v3_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=dict(values.get("research_config") or {}),
                blob_root=str(values.get("blob_root") or ""),
            )

        def _deep_v4_state_factory(**values):
            return deep_research_v4_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=dict(values.get("research_config") or {}),
                blob_root=str(values.get("blob_root") or ""),
            )

        def _deep_v5_state_factory(**values):
            runtime = {
                **dict(values.get("research_config") or {}),
                "soft_checkpoint_seconds": config.research_v5.soft_checkpoint_seconds,
                "lease_seconds": config.research_v5.lease_seconds,
                "auto_cap_seconds": config.research_v5.auto_cap_seconds,
                "plateau_rounds": config.research_v5.plateau_rounds,
            }
            return deep_research_v5_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=runtime,
                blob_root=str(values.get("blob_root") or ""),
                operation_id=str(values.get("operation_id") or values["run_id"]),
                continuation_snapshot=(
                    dict(values["continuation_snapshot"])
                    if isinstance(values.get("continuation_snapshot"), dict)
                    else None
                ),
                parent_operation_id=(
                    str(values["parent_operation_id"])
                    if values.get("parent_operation_id") is not None
                    else None
                ),
                only_gaps=bool(values.get("only_gaps", False)),
            )

        async def _deep_v6_state_factory(**values):
            continuation_snapshot = None
            topic = str(values.get("topic") or "")
            answer_locale = str(values.get("answer_locale") or "zh-CN")
            if values.get("source_snapshot_hash") is not None:
                loader = getattr(
                    _workflow_service.research_repository,
                    "load_continuation_snapshot_v6",
                    None,
                )
                if not callable(loader):
                    raise RuntimeError("v6 continuation snapshot loader is unavailable")
                continuation_snapshot = decode_deep_research_v6_continuation_snapshot(
                    await loader(str(values["run_id"]))
                )
                blob_loader = getattr(_workflow_service, "_research_snapshot_loader", None)
                if not callable(blob_loader):
                    raise RuntimeError("v6 continuation registered blob loader is unavailable")
                spec_bytes = await blob_loader(str(continuation_snapshot["spec_ref"])[7:])
                spec_value = (
                    json.loads(spec_bytes.decode("utf-8"))
                    if isinstance(spec_bytes, bytes)
                    else spec_bytes
                )
                if not isinstance(spec_value, dict):
                    raise RuntimeError("v6 continuation spec blob is invalid")
                topic = str(spec_value.get("normalized_question") or "")
                answer_locale = str(spec_value.get("answer_locale") or "zh-CN")
            return deep_research_v6_initial_state(
                topic=topic,
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                answer_locale=answer_locale,
                schema_version=(
                    int(values["schema_version"])
                    if values.get("schema_version") is not None
                    else None
                ),
                parent_run_id=(
                    str(values["parent_run_id"])
                    if values.get("parent_run_id") is not None
                    else None
                ),
                source_snapshot_hash=(
                    str(values["source_snapshot_hash"])
                    if values.get("source_snapshot_hash") is not None
                    else None
                ),
                continuation_snapshot=continuation_snapshot,
            )

        def _deep_v7_state_factory(**values):
            return deep_research_v7_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=dict(values.get("research_config") or {}),
                blob_root=str(values.get("blob_root") or ""),
            )

        _workflow_launcher.register_adapter(
            "deep_research",
            "v1",
            state_factory=_deep_state_factory,
            context_factory=_deep_context_factory,
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v2",
            state_factory=_deep_v2_state_factory,
            context_factory=_deep_v2_context_factory,
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v3",
            state_factory=_deep_v3_state_factory,
            context_factory=_deep_v2_context_factory,
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v4",
            state_factory=_deep_v4_state_factory,
            context_factory=_deep_v2_context_factory,
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v5",
            state_factory=_deep_v5_state_factory,
            context_factory=_deep_v5_context_factory,
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v6",
            state_factory=_deep_v6_state_factory,
            context_factory=_deep_v6_context_factory,
            extensions={
                DEEP_RESEARCH_EXTENSION: DeepResearchRuntimeExtension(
                    new_runs_enabled=True,
                    action_ids=("generate_now", "continue_research", "cancel_settle"),
                    continuation=DeepResearchContinuationAdapter(
                        decode_snapshot=decode_deep_research_v6_continuation_snapshot,
                        build_child_payload=build_deep_research_v6_continuation_payload,
                    ),
                    terminal_commit_capability=TERMINAL_COMMIT_CAPABILITY,
                )
            },
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v7",
            state_factory=_deep_v7_state_factory,
            context_factory=_deep_v7_context_factory,
        )

        from agent.tool_use_shim import OpenAICompatibleAgentLLM as _RecoveryShim
        from deskpet.workflows.adapters.code_runtime import (
            DurableToolRuntimeState as _RecoveryDurableToolRuntimeState,
        )
        from deskpet.workflows.adapters.code_runtime import (
            ProposalPort as _RecoveryProposalPort,
        )
        from deskpet.workflows.adapters.code_runtime import (
            ToolDispatchPort as _RecoveryToolDispatchPort,
        )
        from deskpet.workflows.definitions.v1 import (
            code_complex_initial_state as _code_recovery_initial_state,
        )
        from deskpet.workflows.definitions.v1 import (
            durable_task_initial_state as _durable_task_initial_state,
        )
        from deskpet.workflows.output_contract import (
            TaskOutputContractPort as _TaskOutputContractPort,
        )
        from deskpet.workflows.output_contract import (
            TaskOutputContractV1 as _TaskOutputContractV1,
        )

        def _code_recovery_state_factory(**values):
            return _code_recovery_initial_state(
                request=str(values["request"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                session_ref=dict(values["session_ref"]),
                capability_snapshot=list(values["capability_snapshot"]),
                messages=list(values["messages"]),
                plan_steps=list(values["plan_steps"]),
                approval_required=bool(values["approval_required"]),
                started_at=float(values["started_at"]),
                request_id=str(values["request_id"]),
                turn_id=str(values["turn_id"]),
                provider_snapshot=dict(values["provider_snapshot"]),
                model_snapshot=dict(values["model_snapshot"]),
                output_contract=dict(values.get("output_contract") or {}),
            )

        def _durable_task_state_factory(**values):
            return _durable_task_initial_state(
                request=str(values["request"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                session_ref=dict(values["session_ref"]),
                capability_snapshot=list(values["capability_snapshot"]),
                messages=list(values["messages"]),
                plan_steps=list(values["plan_steps"]),
                approval_required=bool(values["approval_required"]),
                started_at=float(values["started_at"]),
                request_id=str(values["request_id"]),
                turn_id=str(values["turn_id"]),
                provider_snapshot=dict(values["provider_snapshot"]),
                model_snapshot=dict(values["model_snapshot"]),
                output_contract=dict(values.get("output_contract") or {}),
            )

        async def _code_recovery_context_factory(row, start_payload):
            provider_snapshot = dict(start_payload.get("provider_snapshot") or {})
            model_snapshot = dict(start_payload.get("model_snapshot") or {})
            provider = _resolve_code_recovery_provider(
                provider_snapshot=provider_snapshot,
                model_snapshot=model_snapshot,
                registry=service_context.get("provider_registry"),
                fallback=host_state.local_llm or cloud_llm,
            )
            shim = _RecoveryShim(provider=provider)
            session_id = str(row.get("session_id") or "default")
            workflow_name = str(row.get("workflow_name") or "code_complex")
            from deskpet.harness.adapters.legacy_execution_migration import (
                resolve_frozen_legacy_workspace,
            )

            workspace = await resolve_frozen_legacy_workspace(
                row=row,
                start_payload=start_payload,
                session_db=service_context.get("session_db"),
            )
            provider_name = str(
                provider_snapshot.get("provider") or provider_snapshot.get("provider_id") or ""
            )
            model_name = str(model_snapshot.get("model") or "")
            allowed_tools = tuple(
                str(item.get("tool_name") or "")
                for item in (start_payload.get("capability_snapshot") or ())
                if isinstance(item, dict) and item.get("tool_name")
            )
            raw_context_os = start_payload.get("context_os")
            prepared_tool_set = None
            eligibility = None
            execution_context = None
            context_os_enabled = bool(
                getattr(getattr(config, "features", None), "context_os_v1", False)
            )
            if context_os_enabled:
                if not isinstance(raw_context_os, dict):
                    raise RuntimeError("code recovery Context OS snapshot is unavailable")
                from deskpet.tools.capabilities import ToolExecutionContext
                from deskpet.tools.prepared_snapshot import load_context_os_snapshot

                prepared_tool_set, eligibility = load_context_os_snapshot(raw_context_os)
                request_id = str(start_payload.get("request_id") or row.get("request_id") or "")
                turn_id = str(start_payload.get("turn_id") or row.get("turn_id") or "")
                if eligibility.session_id != session_id or eligibility.request_id != request_id:
                    raise RuntimeError("code recovery Context OS identity mismatch")
                deskpet_tool_registry_v2.validate_prepared_tool_set(
                    prepared_tool_set, eligibility=eligibility
                )
                prepared_names = {
                    capability.ref.name
                    for capability in (
                        *prepared_tool_set.direct,
                        *prepared_tool_set.activated,
                    )
                }
                _validate_code_recovery_capability_subset(
                    prepared_names=prepared_names,
                    allowed_tools=allowed_tools,
                )
                scope_store = service_context.get("tool_capability_scope_store")
                if scope_store is None:
                    raise RuntimeError("code recovery capability scope store unavailable")
                if (
                    scope_store.get(
                        prepared_tool_set.scope_id,
                        session_id=session_id,
                        request_id=request_id,
                    )
                    is None
                ):
                    try:
                        scope_store.open(prepared_tool_set, eligibility)
                    except ValueError:
                        if (
                            scope_store.get(
                                prepared_tool_set.scope_id,
                                session_id=session_id,
                                request_id=request_id,
                            )
                            is None
                        ):
                            raise
                from deskpet.tools.capabilities import canonical_hash

                scope_hash = canonical_hash(
                    {
                        "session_id": session_id,
                        "workspace": workspace,
                        "write_scope_root": workspace,
                        "venue": ("code" if workflow_name == "code_complex" else "text"),
                    }
                )
                execution_snapshot = await _workflow_service.execution_uow.read_run_start_snapshot(
                    str(row.get("run_id") or "")
                )
                frozen_run_context = (
                    json.loads(execution_snapshot.run_context_json)
                    if execution_snapshot is not None
                    else {}
                )
                root_run_id = str(
                    frozen_run_context.get("root_run_id")
                    or row.get("parent_run_id")
                    or row.get("run_id")
                    or ""
                )
                execution_context = ToolExecutionContext(
                    scope_id=prepared_tool_set.scope_id,
                    session_id=session_id,
                    request_id=request_id,
                    origin="agent",
                    root_run_id=root_run_id,
                    parent_run_id=(str(row.get("parent_run_id") or "").strip() or None),
                    turn_id=turn_id,
                    venue=("code" if workflow_name == "code_complex" else "text"),
                    workspace=workspace,
                    write_scope_root=workspace,
                    capability_hash=prepared_tool_set.schema_fingerprint,
                    scope_hash=scope_hash,
                    provider_plan=tuple(item for item in (provider_name, model_name) if item),
                    run_id=str(row.get("run_id") or ""),
                    trace_id=str(row.get("trace_id") or ""),
                )
            runtime_tool_state = (
                _RecoveryDurableToolRuntimeState(prepared_tool_set)
                if prepared_tool_set is not None
                else None
            )
            output_contract = None
            output_contract_port = None
            if workflow_name == "durable_task":
                raw_output_contract = start_payload.get("output_contract")
                if isinstance(raw_output_contract, dict):
                    output_contract = _TaskOutputContractV1.from_dict(raw_output_contract)
                    if Path(output_contract.workspace_root) != Path(workspace):
                        raise RuntimeError("durable task output contract workspace mismatch")
                    output_contract_port = _TaskOutputContractPort(output_contract)
                elif start_payload.get("capability_builder") is None:
                    # Compatibility read for Runs created before
                    # task-output-contract-v1 shipped. New durable-task launches
                    # are rejected by the host unless they carry a frozen
                    # contract, but making an old checkpoint unrecoverable would
                    # turn a safety upgrade into data loss.
                    logger.warning(
                        "durable_task_legacy_output_contract_missing",
                        run_id=str(row.get("run_id") or ""),
                        root_run_id=str(row.get("root_run_id") or ""),
                    )
            ports = {
                "llm": _RecoveryProposalPort(
                    shim,
                    deskpet_tool_registry_v2,
                    session_id=session_id,
                    provider_name=provider_name,
                    model_name=model_name,
                    profile_key=f"workflow.{workflow_name}",
                    allowed_tools=allowed_tools,
                    context_os_v1=context_os_enabled,
                    prepared_tool_set=prepared_tool_set,
                    eligibility=eligibility,
                    execution_context=execution_context,
                    capability_scope_store=service_context.get("tool_capability_scope_store"),
                    runtime_tool_state=runtime_tool_state,
                    dispatch_fence_acquirer=service_context.get("run_execution_fence_acquirer"),
                    provider_invocation_coordinator=service_context.get(
                        "provider_invocation_coordinator"
                    ),
                    context_compressor=service_context.get("context_compressor"),
                    output_contract=output_contract,
                ),
                "tool": _RecoveryToolDispatchPort(
                    deskpet_tool_registry_v2,
                    session_id=session_id,
                    execution_context=execution_context,
                    runtime_tool_state=runtime_tool_state,
                    workflow_name=workflow_name,
                    dispatch_fence_acquirer=service_context.get("run_execution_fence_acquirer"),
                ),
            }
            if output_contract_port is not None:
                ports["output_contract"] = output_contract_port
            return WorkflowContext(
                ports=ports,
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
            )

        _workflow_launcher.register_adapter(
            "code_complex",
            "v1",
            state_factory=_code_recovery_state_factory,
            context_factory=_code_recovery_context_factory,
        )
        _workflow_launcher.register_adapter(
            "durable_task",
            "v1",
            state_factory=_durable_task_state_factory,
            context_factory=_code_recovery_context_factory,
        )
        # Import the actual ToolRegistry instance for personal_runtime_adapter
        from deskpet.tools.registry import registry as _actual_tool_registry
        from deskpet.workflows.adapters.personal_runtime import (
            build_personal_runtime_adapter,
        )
        from deskpet.workflows.effects import EffectJournal as _PersonalEffectJournal

        _workflow_service.runtime_adapters.register(
            build_personal_runtime_adapter(
                journal=_PersonalEffectJournal(_workflow_service.run_store.path),
                tool_registry=_actual_tool_registry,
                run_start_snapshot_reader=(_workflow_service.execution_uow.read_run_start_snapshot),
                dispatch_fence_acquirer=service_context.get("run_execution_fence_acquirer"),
            )
        )

        # New product workflow starts are owned exclusively by RunKernel.
        # The legacy tool handlers remain library-compatible but are not wired
        # as production launch ingress.
        _workflow_research_tools.set_deepresearch_workflow_starter(None)
        try:
            _configure_ppt_production(recover=False)
            logger.info("ppt_pro_services_wired")
        except Exception as exc:  # noqa: BLE001
            logger.warning("ppt_pro_services_wire_failed", error=str(exc))
        from deskpet.workflows.startup_recovery import activate_and_recover_workflows

        _workflow_recovery = await activate_and_recover_workflows(
            project_bindings=_project_binding_service,
            workflow_service=_workflow_service,
            workflow_launcher=_workflow_launcher,
            required_runtime_identities=_workflow_service.runtime_adapters.identities(),
        )
        _delete_reconcile = _workflow_recovery["delete_reconcile"]
        startup_recoveries = _workflow_recovery["startup_recoveries"]
        recovered_decisions = _workflow_recovery["recovered_decisions"]
        recovered_deliveries = _workflow_recovery["recovered_deliveries"]
        recovered_runs = _workflow_recovery["recovered_runs"]
        if _delete_reconcile["failed"]:
            logger.warning(
                "session_delete_cancel_reconcile_incomplete",
                **_delete_reconcile,
            )
        logger.info(
            "workflow_service_ready",
            db_path=str(_workflow_service.run_store.path),
            versions=_workflow_service.runner.registry.versions(),
            recovered_runs=len(recovered_runs),
            startup_recoveries=len(startup_recoveries),
            recovered_decisions=len(recovered_decisions),
            recovered_deliveries=len(recovered_deliveries),
        )
    except Exception as exc:  # noqa: BLE001 - old ReAct stays available
        service_context.register("workflow_service", None)
        service_context.register("run_execution_fence_acquirer", None)
        service_context.register("provider_invocation_coordinator", None)
        try:
            from deskpet.tools import research_tools as _workflow_research_tools

            _workflow_research_tools.set_deepresearch_workflow_starter(None)
        except Exception:
            pass
        logger.warning("workflow_service_init_failed", error=str(exc))
    return _sdb
