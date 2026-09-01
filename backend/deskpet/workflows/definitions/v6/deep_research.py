"""DeepResearch v6 eleven-node production workflow definition."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping

from ...contracts import (
    ChannelSpec,
    JsonType,
    JsonValue,
    ReducerKind,
    WorkflowState,
    canonical_json,
)
from ...definition import (
    END_NODE,
    Edge,
    NodeDefinition,
    WorkflowDefinition,
    compile_workflow,
)
from ..deep_research_v6_contracts import parse_blob_ref
from ..deep_research_v6_evidence import GENESIS_EVIDENCE_HEAD
from ..deep_research_v6_production_nodes import (
    admit_facts_handler,
    assess_answer_handler,
    compile_spec_handler,
    extract_candidate_bundles_handler,
    load_pages_handler,
    plan_route_handler,
    register_inferences_handler,
    synthesize_inferences_handler,
)
from ..deep_research_v6_terminal_nodes import (
    integrity_handler,
    persist_manifest_handler,
    render_claims_handler,
)

WORKFLOW_NAME = "deep_research"
WORKFLOW_VERSION = "v6"
STATE_SCHEMA_VERSION = 6
CONTRACT_SCHEMA_VERSION = 1

_CONTINUATION_SNAPSHOT_KEYS = {
    "schema_version", "snapshot_id", "snapshot_hash", "run_id", "workflow_name",
    "workflow_version", "spec_ref", "spec_hash", "fact_batch_refs",
    "evidence_head_hash", "assessment_ref", "assessment_hash",
    "assessment_input_hash", "claim_batch_ref", "provenance_refs", "policy_refs",
    "closure_refs",
}
_CONTINUATION_POLICY_KEYS = {
    "compiler", "route", "extraction", "llm_extract", "llm_repair",
    "llm_inference", "admission", "inference", "assessment", "claim", "quality",
}


def decode_continuation_snapshot(value: object) -> dict[str, JsonValue]:
    """Decode the frozen v6 continuation snapshot without widening its closure."""

    if not isinstance(value, Mapping) or set(value) != _CONTINUATION_SNAPSHOT_KEYS:
        raise ValueError("v6 continuation snapshot keys differ")
    raw = copy.deepcopy(dict(value))
    if (
        raw["schema_version"] != 1
        or raw["workflow_name"] != WORKFLOW_NAME
        or raw["workflow_version"] != WORKFLOW_VERSION
    ):
        raise ValueError("v6 continuation snapshot identity differs")
    for name in (
        "spec_ref", "assessment_ref", "claim_batch_ref",
    ):
        parse_blob_ref(str(raw[name]))
    for name in ("spec_hash", "evidence_head_hash", "assessment_hash", "assessment_input_hash"):
        digest = str(raw[name])
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError(f"v6 continuation {name} is not a sha256 digest")
    for name in ("fact_batch_refs", "provenance_refs", "closure_refs"):
        refs = raw[name]
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            raise ValueError(f"v6 continuation {name} must be a ref array")
        for ref in refs:
            parse_blob_ref(ref)
    if len(set(raw["fact_batch_refs"])) != len(raw["fact_batch_refs"]):
        raise ValueError("v6 continuation fact batches must be unique")
    for name in ("provenance_refs", "closure_refs"):
        if raw[name] != sorted(set(raw[name])):
            raise ValueError(f"v6 continuation {name} must be sorted and unique")
    policies = raw["policy_refs"]
    if not isinstance(policies, Mapping) or set(policies) != _CONTINUATION_POLICY_KEYS:
        raise ValueError("v6 continuation policy refs differ")
    for ref in policies.values():
        parse_blob_ref(str(ref))
    expected_closure = {
        str(raw["spec_ref"]), str(raw["assessment_ref"]), str(raw["claim_batch_ref"]),
        *[str(ref) for ref in raw["fact_batch_refs"]],
        *[str(ref) for ref in raw["provenance_refs"]],
        *[str(ref) for ref in policies.values()],
    }
    if raw["closure_refs"] != sorted(expected_closure):
        raise ValueError("v6 continuation closure classification differs")
    identity = dict(raw)
    snapshot_id = str(identity.pop("snapshot_id"))
    snapshot_hash = str(identity.pop("snapshot_hash"))
    expected_hash = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    if snapshot_hash != expected_hash or snapshot_id != "rcs_" + expected_hash[:24]:
        raise ValueError("v6 continuation semantic identity differs")
    return raw  # type: ignore[return-value]


def build_continuation_start_payload(
    *, parent_run_id: str, source_snapshot_hash: str
) -> dict[str, JsonValue]:
    """Build the exact repository-owned v6 continuation start envelope."""

    if not str(parent_run_id).strip():
        raise ValueError("parent_run_id is required")
    digest = str(source_snapshot_hash)
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("source_snapshot_hash must be a sha256 digest")
    return {
        "schema_version": 1,
        "parent_run_id": str(parent_run_id),
        "source_snapshot_hash": digest,
    }

NODE_IDS = (
    "compile_spec",
    "plan_route",
    "load_pages",
    "extract_candidate_bundles",
    "admit_facts",
    "synthesize_inferences",
    "register_inferences",
    "assess_answer",
    "render_claims",
    "integrity",
    "persist_manifest",
)

_HANDLERS = {
    "compile_spec": compile_spec_handler,
    "plan_route": plan_route_handler,
    "load_pages": load_pages_handler,
    "extract_candidate_bundles": extract_candidate_bundles_handler,
    "admit_facts": admit_facts_handler,
    "synthesize_inferences": synthesize_inferences_handler,
    "register_inferences": register_inferences_handler,
    "assess_answer": assess_answer_handler,
    "render_claims": render_claims_handler,
    "integrity": integrity_handler,
    "persist_manifest": persist_manifest_handler,
}


DEEP_RESEARCH_V6_DEFINITION = WorkflowDefinition(
    name=WORKFLOW_NAME,
    version=WORKFLOW_VERSION,
    state_schema_version=STATE_SCHEMA_VERSION,
    entry_node="compile_spec",
    nodes=tuple(NodeDefinition(node_id, _HANDLERS[node_id]) for node_id in NODE_IDS),
    channels={
        "values": ChannelSpec(JsonType.OBJECT, ReducerKind.SINGLE_WRITER, frozenset(NODE_IDS)),
        "blob_refs": ChannelSpec(JsonType.ARRAY, ReducerKind.STABLE_LIST, frozenset(NODE_IDS)),
    },
    edges=tuple(
        Edge(source, target)
        for source, target in zip(NODE_IDS, (*NODE_IDS[1:], END_NODE), strict=True)
    ),
    recursion_limit=32,
    max_supersteps=16,
    prompt_manifest={
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "intent_types": [
            "official_exact_fact", "comparison", "top_n", "policy", "open_research",
        ],
        "llm_roles": [
            "evidence_candidate_extract",
            "evidence_inference_synthesize",
            "evidence_structured_repair",
        ],
    },
    policy_manifest={
        "implementation": "deep-research-v6-registered-evidence-graph-v1",
        "external_ports": [
            "blob", "retrieval", "semantic", "llm_extract", "llm_inference",
            "llm_repair", "deadline", "native_execution_policy",
        ],
        "ingress": ["topic", "session_id", "answer_locale"],
        "evidence_frontiers": ["facts", "inferences"],
        "answer_statuses": ["completed", "partial", "insufficient_evidence"],
    },
)

DEEP_RESEARCH_V6 = compile_workflow(DEEP_RESEARCH_V6_DEFINITION)


def initial_state(
    *,
    topic: str,
    run_id: str,
    thread_id: str | None = None,
    session_id: str = "",
    answer_locale: str = "zh-CN",
    schema_version: int | None = None,
    parent_run_id: str | None = None,
    source_snapshot_hash: str | None = None,
    continuation_snapshot: Mapping[str, JsonValue] | None = None,
) -> WorkflowState:
    if not topic.strip() or not run_id.strip():
        raise ValueError("topic and run_id are required")
    if schema_version is not None and schema_version != 1:
        raise ValueError("continuation start schema_version must be 1")
    continuation: dict[str, JsonValue] | None = None
    if continuation_snapshot is not None:
        continuation = decode_continuation_snapshot(continuation_snapshot)
        if not parent_run_id or continuation["run_id"] != parent_run_id:
            raise ValueError("continuation parent run differs from snapshot")
        if source_snapshot_hash != continuation["snapshot_hash"]:
            raise ValueError("continuation source snapshot hash differs")
    elif parent_run_id is not None or source_snapshot_hash is not None:
        raise ValueError("continuation snapshot hydration is required")

    values: dict[str, JsonValue] = {
            "topic": topic,
            "answer_locale": answer_locale,
            "contract_schema_version": CONTRACT_SCHEMA_VERSION,
            "stage": "pending",
            "spec_ref": None,
            "spec_hash": None,
            "route_policy_ref": None,
            "route_policy_hash": None,
            "route_decision_ref": None,
            "route_id": None,
            "page_result_refs": [],
            "candidate_slot_results": [],
            "fact_batch_refs": [],
            "evidence_head_hash": GENESIS_EVIDENCE_HEAD,
            "inference_slot_results": [],
            "assessment_ref": None,
            "assessment_hash": None,
            "assessment_input_hash": None,
            "claim_batch_ref": None,
            "quality_audit_ref": None,
            "answer_status": None,
            "final_assistant_ref": None,
            "report_ref": None,
            "safe_summary_ref": None,
            "policy_refs": {},
            "terminal_manifest_ref": None,
            "terminal_manifest_hash": None,
            "continuation_parent_run_id": None,
            "source_snapshot_hash": None,
            "inherited_fact_batch_count": 0,
            "inherited_provenance_refs": [],
        }
    inherited_refs: list[str] = []
    if continuation is not None:
        values.update({
            "stage": "continuation_hydrated",
            "spec_ref": continuation["spec_ref"],
            "spec_hash": continuation["spec_hash"],
            "fact_batch_refs": copy.deepcopy(continuation["fact_batch_refs"]),
            "evidence_head_hash": continuation["evidence_head_hash"],
            "assessment_ref": continuation["assessment_ref"],
            "assessment_hash": continuation["assessment_hash"],
            "assessment_input_hash": continuation["assessment_input_hash"],
            "claim_batch_ref": continuation["claim_batch_ref"],
            "policy_refs": copy.deepcopy(continuation["policy_refs"]),
            "continuation_parent_run_id": parent_run_id,
            "source_snapshot_hash": source_snapshot_hash,
            "inherited_fact_batch_count": len(continuation["fact_batch_refs"]),
            "inherited_provenance_refs": copy.deepcopy(continuation["provenance_refs"]),
        })
        inherited_refs = [str(ref) for ref in continuation["closure_refs"]]

    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "workflow_name": WORKFLOW_NAME,
        "workflow_version": WORKFLOW_VERSION,
        "thread_id": thread_id or run_id,
        "run_id": run_id,
        "session_id": session_id,
        "active_nodes": [],
        "active_step_id": None,
        "status": "pending",
        "values": values,
        "blob_refs": [
            {"id": ref[7:], "sha256": ref[7:]}
            for ref in inherited_refs
        ],
        "artifact_refs": [],
        "receipt_refs": [],
        "loop_counters": {},
        "budgets": {},
        "errors": [],
    }


__all__ = [
    "CONTRACT_SCHEMA_VERSION", "DEEP_RESEARCH_V6", "DEEP_RESEARCH_V6_DEFINITION",
    "NODE_IDS", "STATE_SCHEMA_VERSION", "WORKFLOW_NAME", "WORKFLOW_VERSION",
    "build_continuation_start_payload", "decode_continuation_snapshot", "initial_state",
]
