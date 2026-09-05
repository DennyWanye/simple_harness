"""Approved validation-side oracles. No SDK imports and no candidate-derived gold.

The approved NUL preimage is intentionally NOT silently replaced with the SDK's
JSON domain envelope. That newly discovered contract difference blocks admission.
"""
import hashlib
import json


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def hash_json(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def domain_hash(domain, value):
    return hashlib.sha256(domain.encode() + b"\0" + canonical(value)).hexdigest()


def semantic_source(subject_entity, predicate, object_value, qualifiers):
    if not isinstance(qualifiers, list):
        raise ValueError("source qualifiers must be an ordered array")
    return {"memory_type": "semantic", "semantic_kind": "claim", "subject_entity": subject_entity,
            "predicate": predicate, "object_value": object_value,
            "object_value_hash": hash_json(object_value), "qualifiers": qualifiers}


FINAL_TABLES = sorted(["typed_recall_decisions", "typed_recall_decision_items", "typed_recall_results",
                      "typed_recall_result_items", "typed_recall_confirmation_groups",
                      "typed_recall_confirmation_members", "typed_recall_terminals"])
MUTATION_TABLES = sorted(["cognitive_apply_heads", "cognitive_memory_heads", "cognitive_memory_revisions",
    "cognitive_evidence_spans", "cognitive_revision_task_scope_origins", "cognitive_relations",
    "cognitive_conflict_groups", "cognitive_conflict_members", "cognitive_conflict_resolutions",
    "cognitive_classification_decisions", "cognitive_classification_evidence_authorities",
    "recall_authority_heads", "recall_authority_events"])
ROOT_FIELDS = {"category", "table_name", "row_count", "root_hash", "first_leaf_hash", "last_leaf_hash"}
MANIFEST_FIELDS = {"schema_version", "storage_schema_version", "schema_checksum",
    "initialization_receipt_hash", "principal_ref_hash", "table_roots", "total_row_count"}


def protected_hash(manifest, payload_hash, protected_tables):
    if set(manifest) != MANIFEST_FIELDS or hash_json(manifest) != payload_hash:
        raise ValueError("manifest fields/hash differ")
    roots = manifest["table_roots"]
    names = [root["table_name"] for root in roots]
    if len(names) != len(set(names)) or any(set(root) != ROOT_FIELDS for root in roots):
        raise ValueError("duplicate table or invalid root fields")
    if roots != sorted(roots, key=lambda r: (r["category"], r["table_name"])):
        raise ValueError("noncanonical root order")
    if protected_tables != sorted(set(protected_tables)) or not set(protected_tables) <= set(names):
        raise ValueError("missing or noncanonical protected table set")
    if sum(root["row_count"] for root in roots) != manifest["total_row_count"]:
        raise ValueError("manifest row count differs")
    return domain_hash("tc-hm-13/protected-state/v1", {
        "schema_version": 1, "storage_schema_version": manifest["storage_schema_version"],
        "schema_checksum": manifest["schema_checksum"], "principal_ref_hash": manifest["principal_ref_hash"],
        "protected_tables": protected_tables,
        "table_roots": [root for root in roots if root["table_name"] in protected_tables]})


def mutation_map():
    # Fixed before consumers execute. Reasons come from the public validation
    # contract/source inspection, never from a caught candidate exception.
    attacks = [
        ("protocol_version", "request.harness_protocol", "recall-v5"),
        ("principal_id", "principal.actor_id", "principal-2"),
        ("run_id", "context.run_id", "run-2"),
        ("context_hash", "plan.context_hash", "e1b5609251224d45c0b3a0d5b5cb2b02506881bfebb37aaf40101767a63652ca"),
        ("context_revision", "context.context_revision", 8),
        ("plan_id", "plan.plan_id", "plan-2"),
        ("plan_hash", "plan.reason_codes", ["recall_user_preference_dependency"]),
        ("disclosure_hash", "context.disclosure_context.authority_ref", "disclosure-2"),
        ("recipient", "context.disclosure_context.recipient", "task_collaborator"),
        ("purpose", "context.disclosure_context.purpose", "task_resume"),
        ("budget.max_items", "context.budget.max_items", 5),
        ("budget.max_bytes", "context.budget.max_bytes", 4095),
        ("budget.max_tokens", "context.budget.max_tokens", 1023),
        ("budget.deadline_ms", "context.budget.deadline_ms", 1999),
    ]
    rows = []
    for attack, path, mutation in attacks:
        row = {"cell_id": "unsupported-replay/request-hash:" + attack, "original_attack": attack,
               "public_path": path, "mutation": mutation,
               "companion_bindings": "Keep idempotency_key; sync context/plan run, revision, disclosure, budget and derived context_hash; SDK derives plan_hash",
               "reject_layer": "Memory.execute_typed_recall.idempotency",
               "exception_type": "MemoryIdempotencyConflict", "exact_reason": "IDEMPOTENCY_CONFLICT",
               "memory_call": True, "candidate_query_count": 0,
               "precondition": "Same isolated database; baseline request already durably terminal; both context and plan valid at fixed recall now"}
        if attack == "protocol_version":
            row.update(gap="NO_PUBLIC_REQUEST_PROTOCOL_VERSION_INPUT", memory_call=False,
                       reject_layer="Host strict request parser (unavailable)", exception_type=None,
                       exact_reason=None, companion_bindings="Do not substitute Context schema_version or result parser version")
        elif attack == "principal_id":
            row.update(reject_layer="Memory.execute_typed_recall.ownership",
                       exception_type="MemoryOwnershipConflict", exact_reason="typed_recall_subject_not_owned",
                       companion_bindings="Keep context.subject and plan.subject principal-1; change trusted principal only")
        elif attack == "context_hash":
            row.update(reject_layer="RecallPlan.validate_narrowing", exception_type="ValueError",
                       exact_reason="RecallPlan context_hash differs",
                       companion_bindings="Keep legitimate context and its hash; replace only plan.context_hash intentionally")
        elif attack in {"plan_id", "plan_hash"}:
            row["companion_bindings"] = "Keep entire context and idempotency_key; change specified plan input only; SDK derives plan_hash"
        rows.append(row)
    return rows


def judge_rejection(row, observed):
    if row.get("gap"):
        return "BLOCKED"
    for key in ("exception_type", "exception_reason"):
        expected = row["exact_reason" if key == "exception_reason" else key]
        if observed.get(key) != expected:
            raise ValueError("wrong rejection layer/reason: " + row["original_attack"])
    if observed.get("memory_called") != row["memory_call"]:
        raise ValueError("wrong public call boundary")
    # The exception has no public per-invocation read counter. A manifest proves
    # writes only; do not turn absence of writes into absence of candidate reads.
    return "BLOCKED"


def check_fault(phase, *, control_business_valid, before, immediate, control_after,
                replay_exact, replay_query_count):
    if not control_business_valid:
        raise ValueError("no-fault business oracle failed; reference prohibited")
    if phase == "pre_commit":
        if immediate != before:
            raise ValueError("pre-commit final tables changed")
    elif phase == "post_commit_ack_loss":
        if immediate != control_after or not replay_exact or replay_query_count != 0:
            raise ValueError("post-commit terminal or exact replay differs")
    else:
        raise ValueError("fault phase must be fixed before execution")
