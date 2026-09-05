"""Approved validation-side oracles. No SDK imports and no candidate-derived gold.

SDK hashes follow the existing JSON domain envelope. The prior proposal's NUL
description was a factual error corrected under the user's alignment authority.
Only the validation-side protected-state commitment continues to use NUL.
"""
import hashlib
import json
import re
import copy


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def hash_json(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def domain_hash(domain, value):
    return hashlib.sha256(domain.encode() + b"\0" + canonical(value)).hexdigest()


def sdk_domain_hash(domain, value):
    return hash_json({"domain": domain, "payload": value})


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
    for root in roots:
        count = root["row_count"]
        if type(count) is not int or count < 0 or not re.fullmatch("[0-9a-f]{64}", str(root["root_hash"])):
            raise ValueError("invalid manifest root/count")
        for field in ("first_leaf_hash", "last_leaf_hash"):
            if (count == 0 and root[field] is not None) or (count > 0 and not re.fullmatch("[0-9a-f]{64}", str(root[field]))):
                raise ValueError("invalid manifest leaf binding")
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


def check_attack_inputs(fixture, row, observed, baseline):
    """Verify exact mutated arguments separately from exception observations.

    JSON-envelope context hashing here checks the documented candidate wire's
    companion binding only. It does not change approved NUL gold or admit a cell.
    """
    if baseline is None:
        raise ValueError("mutation baseline evidence missing")
    context, plan = copy.deepcopy(baseline["context"]), copy.deepcopy(baseline["plan"])
    original = fixture["request_hash_oracle"]["base_request"]
    if (context["subject"] != original["principal_id"] or context["run_id"] != original["run_id"]
            or context["context_revision"] != original["context_revision"]
            or context["budget"] != original["budget"] or plan["plan_id"] != original["plan_id"]):
        raise ValueError("mutation baseline differs from frozen inputs")
    actor, field, value = context["subject"], row["original_attack"], row["mutation"]
    if field == "principal_id":
        actor = value
    elif field == "context_hash":
        plan["context_hash"] = value
    elif field == "plan_id":
        plan["plan_id"] = value
    elif field == "plan_hash":
        plan["reason_codes"] = value
    else:
        if field == "run_id":
            context["run_id"] = value
            context["disclosure_context"]["run_id"] = value
        elif field == "context_revision":
            context["context_revision"] = value
        elif field.startswith("budget."):
            context["budget"][field.split(".")[1]] = value
        else:
            context["disclosure_context"]["authority_ref" if field == "disclosure_hash" else field] = value
        for key in ("run_id", "context_revision", "query", "disclosure_context", "budget"):
            plan[key] = copy.deepcopy(context[key])
        plan["context_hash"] = hash_json({"domain": "simple-harness/recall-context/v2", "payload": context})
    if observed.get("context") != context or observed.get("plan") != plan or observed.get("principal_actor_id") != actor:
        raise ValueError("attack or required companion inputs differ from frozen mapping")


def check_page(page, result, result_hash, result_item_hashes):
    if (page["result_id"] != result["result_id"] or page["result_hash"] != result_hash
            or page["page_ordinal"] != 1 or page["item_offset"] != 0 or page["complete"] is not True):
        raise ValueError("successful page header/result binding differs")
    expected = [{"binding_kind": "selected_item", "ordinal": item["selected_item"]["ordinal"],
        "item_id": item["selected_item"]["item_id"], "item_hash": item_hash}
        for item, item_hash in zip(result["items"], result_item_hashes, strict=True)]
    if not expected or page["bindings"] != expected or page["byte_count"] != sum(len(canonical(b)) for b in expected):
        raise ValueError("successful page empty/content/item binding differs")


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


def assess_cell(fixture, cell, baseline=None):
    """Evaluate independent business assertions, then retain remaining gates.

    Returning BLOCKED is deliberate: the approved domain preimage difference
    precludes a complete decision/result/request oracle even for sound business
    observations. A failed business assertion always takes precedence.
    """
    name, observed = cell["cell_id"], cell["observations"]
    if cell["status"] != "OBSERVED":
        return {"status": cell["status"], "reason": cell["reason"], "business_assertions": []}
    checks, blockers = [], ["DOMAIN_PREIMAGE_DIFFERENCE"]
    try:
        if name == "eligibility/valid-until-null-unbounded":
            vector = next(v for v in fixture["approved_oracle"]["semantic_source_vectors"] if v["id"] == "incumbent")
            items = observed["result"]["items"]
            if len(items) != 1 or items[0]["public_payload"] != vector["provider_payload"]:
                raise ValueError("recall payload differs from independent seed input")
            selected = items[0]["selected_item"]
            operation = observed["mutation_receipt"]["operations"][0]
            if (selected["source_content_hash"] != vector["source_content_hash"]
                    or selected["public_payload_hash"] != hash_json(vector["provider_payload"])
                    or selected["source_ref"] != operation["memory_id"]
                    or selected["source_revision"] != 1 or operation["revision"] != 1
                    or selected["source_kind"] != "cognitive_memory" or selected["memory_type"] != "semantic"):
                raise ValueError("source/content/projection/receipt binding differs")
            if observed["candidate_query_count"] != 1 or observed["replay_candidate_query_count"] != 0:
                raise ValueError("recall/replay candidate count differs")
            checks += ["input-derived source and projection hashes", "mutation receipt identity and revision", "candidate1/replay0"]
            if (observed["decision"]["outcome"] != "recall" or items[0]["score"] != round(.30 / 61, 12)
                    or items[0]["evidence_manifest_hash"] != hash_json(["evidence-relation-1"])
                    or observed["decision"]["selected_items"] != [selected]):
                raise ValueError("seed outcome/rank/evidence/decision item binding differs")
            if observed["page"].get("exception_reason") == "typed_recall_result_expired":
                blockers.append("PUBLIC_PAGE_CLOCK_NOT_INJECTABLE_AT_FROZEN_NOW")
            elif "exception_reason" in observed["page"]:
                raise ValueError("unexpected page rejection")
            else:
                check_page(observed["page"], observed["result"], observed["result_hash"], observed["result_item_hashes"])
                checks.append("successful page exact header/content/result-item bindings")
        elif name == "unsupported-replay/exact-replay":
            first, replay = observed["first"], observed["replay"]
            vector = next(v for v in fixture["approved_oracle"]["semantic_source_vectors"] if v["id"] == "incumbent")
            if (first["decision"]["outcome"] != "recall" or len(first["result"]["items"]) != 1
                    or first["result"]["items"][0]["public_payload"] != vector["provider_payload"]
                    or first["result"]["items"][0]["selected_item"]["source_content_hash"] != vector["source_content_hash"]):
                raise ValueError("replay no-fault baseline business content differs")
            if (canonical(first["decision"]) != canonical(replay["decision"])
                    or canonical(first["result"]) != canonical(replay["result"])
                    or replay["candidate_query_count"] != 0 or replay["candidate_query_started"]
                    or not replay["replayed"]):
                raise ValueError("exact replay bytes/query/fence differs")
            checks.append("actual decision/result byte-identical replay and zero candidate query")
        elif name.startswith("unsupported-replay/request-hash:"):
            row = next(row for row in fixture["approved_oracle"]["mutation_mapping"] if row["cell_id"] == name)
            judge_rejection(row, observed)
            check_attack_inputs(fixture, row, observed, baseline)
            for key in ("before_manifest", "after_manifest"):
                access = observed[key]
                observed_hash = protected_hash(access["manifest"], access["payload_hash"], sorted(set(MUTATION_TABLES + FINAL_TABLES)))
                if key == "before_manifest":
                    before = observed_hash
                elif before != observed_hash:
                    raise ValueError("rejected replay changed protected durable state")
            before_roots = {r["table_name"]: r for r in observed["before_manifest"]["manifest"]["table_roots"]}
            after_roots = {r["table_name"]: r for r in observed["after_manifest"]["manifest"]["table_roots"]}
            if set(before_roots) != set(after_roots) or any(before_roots[n] != after_roots[n]
                    for n in before_roots if n != "canonical_manifest_access_events"):
                raise ValueError("unknown/non-audit table changed during rejected replay")
            checks += ["frozen attack and companion inputs", "observed public exception class/message and Memory call",
                       "real manifest protected state unchanged"]
            blockers.append("PUBLIC_EXCEPTION_HAS_NO_PER_INVOCATION_CANDIDATE_READ_WITNESS")
            blockers.append("INTERNAL_REJECTION_LAYER_HAS_NO_PUBLIC_WITNESS")
        elif name == "unsupported-replay/conflicting-replay":
            if observed.get("exception_type") != "MemoryIdempotencyConflict" or observed.get("exception_reason") != "IDEMPOTENCY_CONFLICT":
                raise ValueError("conflicting replay failed to reject at idempotency")
            checks.append("actual idempotency rejection")
            blockers.append("PUBLIC_EXCEPTION_HAS_NO_PER_INVOCATION_CANDIDATE_READ_WITNESS")
        elif name.startswith("unsupported-replay/"):
            original = next(row for row in fixture["unsupported_cases"] if name.endswith("/" + row["id"]))
            # A supported mandatory carrier cannot erase/change attack reasons.
            reason_map = {"UNSUPPORTED_SELECTOR_EVENT": "selector:event",
                "UNSUPPORTED_SELECTOR_ENVIRONMENT": "selector:environment",
                "UNSUPPORTED_SELECTOR_TASK_PHASE": "selector:task_phase",
                "UNSUPPORTED_MODE_EXACT": "retrieval:exact", "UNSUPPORTED_MODE_TEMPORAL": "retrieval:temporal",
                "UNSUPPORTED_MODE_GRAPH": "retrieval:graph"}
            value = observed["execution"]
            if (value["unsupported_capabilities"] != [reason_map[r] for r in original["ordered_reasons"]]
                    or value["candidate_query_count"] != 0 or value["candidate_query_started"]
                    or value["decision"]["outcome"] != "rejected"
                    or value["decision"]["reason_codes"] != ["recall_invalid_plan"]
                    or value["result"]["items"] or value["result"]["confirmation_groups"]):
                raise ValueError("unsupported reason/order/zero-query/empty-payload assertion failed")
            checks.append("all original unsupported reasons in exact order; rejected invalid plan, zero query/payload")
        else:
            blockers.append("INDEPENDENT_CELL_ORACLE_NOT_IMPLEMENTED")
        return {"status": "BLOCKED", "reason": ";".join(blockers), "business_assertions": checks}
    except (ValueError, KeyError, IndexError, TypeError, StopIteration) as exc:
        return {"status": "FAIL", "reason": str(exc) or type(exc).__name__, "business_assertions": checks}
