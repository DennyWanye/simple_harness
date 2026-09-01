"""Production composition root for the DeepResearch v6 workflow.

Keeping this wiring outside ``main.lifespan`` makes the actual production port
graph importable and executable in integration tests.  The caller still owns
process-wide services (run store, gateway and provider call).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .. import WorkflowContext
from ..contracts import NodeExecutionIdentity, canonical_json
from ..definitions.deep_research_v6_retrieval_contracts import PageExtractionResultV1
from ..definitions.research_core import ResearchLLMPortV2
from ..effects import EffectJournal
from ..native import NativeExecutionPolicy
from ..store import RegisteredBlobStore
from .deep_research_v6_evidence_runtime import DeepResearchV6EvidenceRuntime
from .deep_research_v6_retrieval_runtime import (
    DurableV6DeadlinePort,
    DurableV6PageReadEffectAdapter,
)
from .deep_research_v6_semantic_runtime import DeepResearchV6SemanticRuntime
from .research_runtime import (
    BoundResearchEffectContext,
    DurableResearchCallEffectAdapter,
    DurableV5ControlPort,
    DurableV6ResearchLLMStagePort,
    V6_RESEARCH_RESPONSE_FORMATS,
    WorkflowControlSignalHub,
    build_v6_research_llm_profiles,
    research_response_format_hash,
)


EffectContextResolver = Callable[[NodeExecutionIdentity], Awaitable[BoundResearchEffectContext]]


def _blob_items(refs: Any) -> list[dict[str, str]]:
    return [
        {"id": ref.removeprefix("sha256:"), "sha256": ref.removeprefix("sha256:")}
        for ref in sorted(set(refs))
    ]


class DeepResearchV6ProductionRetrievalPort:
    """Persisted route/page projection over the real evidence runtime."""

    def __init__(
        self,
        *,
        evidence_runtime: DeepResearchV6EvidenceRuntime,
        blobs: RegisteredBlobStore,
        journal: EffectJournal,
        deadline_port: DurableV6DeadlinePort,
    ) -> None:
        self.evidence_runtime = evidence_runtime
        self.blobs = blobs
        self.journal = journal
        self.deadline_port = deadline_port

    async def _put_json(
        self,
        value: Mapping[str, Any],
        identity: NodeExecutionIdentity,
        media_type: str,
    ) -> str:
        ref = await self.blobs.put(
            canonical_json(dict(value)).encode("utf-8"),
            identity,
            media_type=media_type,
        )
        return f"sha256:{ref.sha256}"

    async def plan_route(self, *, spec, identity):
        decision = self.evidence_runtime.plan_route(spec, identity=identity)
        policy_ref = await self._put_json(
            self.evidence_runtime.route_policy,
            identity,
            "application/vnd.deskpet.deepresearch-v6-route-policy+json",
        )
        decision_value = decision.to_json()
        if decision_value["policy_ref"] != policy_ref:
            raise ValueError("v6 retrieval policy bytes differ from route identity")
        decision_ref = await self._put_json(
            decision_value,
            identity,
            "application/vnd.deskpet.deepresearch-v6-route-decision+json",
        )
        await self.deadline_port.prepare_route(
            identity=identity,
            route_id=str(decision_value["route_id"]),
            policy_hash=str(decision_value["policy_hash"]),
            budgets=dict(decision_value["budget"]),
        )
        return {
            "route_policy_ref": policy_ref,
            "route_policy_hash": decision_value["policy_hash"],
            "route_decision_ref": decision_ref,
            "route_id": decision_value["route_id"],
            "blob_refs": _blob_items((policy_ref, decision_ref)),
        }

    async def load_pages(self, *, spec, identity, route_decision):
        retrieval = await self.evidence_runtime.retrieve(
            spec=copy.deepcopy(dict(spec)),
            route_decision=copy.deepcopy(dict(route_decision)),
            identity=identity,
        )
        page_result_refs = list(retrieval.page_result_refs)
        closure: set[str] = set()
        for result_ref in page_result_refs:
            await self.journal.assert_canonical_v6_read_result_owner(
                run_id=identity.run_id,
                canonical_result_ref=result_ref,
                result_kind="page_extraction",
            )
            raw = await self.blobs.get(result_ref.removeprefix("sha256:"))
            typed = PageExtractionResultV1.from_json(json.loads(raw.decode("utf-8")))
            closure.update({result_ref, *typed.dependency_refs()})
        return {
            "page_result_refs": page_result_refs,
            "blob_refs": _blob_items(closure),
        }


def build_deep_research_v6_context(
    *,
    blobs: RegisteredBlobStore,
    journal: EffectJournal,
    resolve_effect_context: EffectContextResolver,
    search_gateway: object,
    llm_call: Callable[..., Awaitable[Any]],
    control_signals: WorkflowControlSignalHub | None = None,
    request_id: str = "",
    turn_id: str = "",
    max_parallel_tasks: int = 3,
    dispatch_fence_acquirer: Callable[[str], Awaitable[Any]] | None = None,
    provider_invocation_coordinator: Any | None = None,
) -> WorkflowContext:
    """Build the exact v6 production ports with injectable external edges."""

    signals = control_signals or WorkflowControlSignalHub()
    deadline_port = DurableV6DeadlinePort(
        journal=journal,
        resolve_effect_context=resolve_effect_context,
    )
    read_effects = DurableV6PageReadEffectAdapter(
        journal=journal,
        blobs=blobs,
        resolve_effect_context=resolve_effect_context,
        deadline_port=deadline_port,
    )
    evidence_runtime = DeepResearchV6EvidenceRuntime(
        search_gateway,
        blobs=blobs,
        durable_reads=read_effects,
        control_signals=signals,
        dispatch_fence_acquirer=dispatch_fence_acquirer,
    )
    retrieval = DeepResearchV6ProductionRetrievalPort(
        evidence_runtime=evidence_runtime,
        blobs=blobs,
        journal=journal,
        deadline_port=deadline_port,
    )

    profiles = build_v6_research_llm_profiles({
        role: research_response_format_hash(response_format)
        for role, response_format in V6_RESEARCH_RESPONSE_FORMATS.items()
    })
    stage_ports: dict[str, DurableV6ResearchLLMStagePort] = {}
    for role, profile in profiles.items():
        effect = DurableResearchCallEffectAdapter(
            journal=journal,
            blobs=blobs,
            llm=ResearchLLMPortV2(llm_call),
            resolve_effect_context=resolve_effect_context,
            reserve_cost_micros=lambda _role, _input, output: max(0, output * 20),
            actual_cost_micros=lambda result: max(
                0,
                int((result.input_tokens or 0) * 3 + (result.output_tokens or 0) * 15),
            ),
            control_signals=signals,
            profile=profile,
            response_format=V6_RESEARCH_RESPONSE_FORMATS[role],
            deadline_observer=deadline_port.allow_dispatch,
            route_resource_budget_kind="llm",
            dispatch_fence_acquirer=dispatch_fence_acquirer,
            provider_invocation_coordinator=provider_invocation_coordinator,
        )
        stage_ports[role] = DurableV6ResearchLLMStagePort(blobs=blobs, effect=effect)

    semantic = DeepResearchV6SemanticRuntime(
        blobs=blobs,
        llm_extract=stage_ports["evidence_candidate_extract"],
        llm_inference=stage_ports["evidence_inference_synthesize"],
        llm_repair=stage_ports["evidence_structured_repair"],
        control_signals=signals,
    )
    ports: dict[str, object] = {
            "blob": blobs,
            "retrieval": retrieval,
            "semantic": semantic,
            "llm_extract": stage_ports["evidence_candidate_extract"],
            "llm_inference": stage_ports["evidence_inference_synthesize"],
            "llm_repair": stage_ports["evidence_structured_repair"],
            "deadline": deadline_port,
            "native_execution_policy": NativeExecutionPolicy(
                max_parallel_tasks=max(2, min(6, int(max_parallel_tasks)))
            ),
        }
    if signals.repository is not None:
        # v6 deliberately reuses the existing accepted/observed/settled/
        # consumed command journal; only its deadline policy differs.
        ports["control"] = DurableV5ControlPort(
            signals.repository, signal_hub=signals
        )
    return WorkflowContext(
        ports=ports,
        request_id=request_id,
        turn_id=turn_id,
    )


__all__ = [
    "DeepResearchV6ProductionRetrievalPort",
    "build_deep_research_v6_context",
]
