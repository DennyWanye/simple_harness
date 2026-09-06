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
        ("protocol_version", "request.harness_protocol", 5),
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
            row.update(memory_call=True, reject_layer="MemoryManager.execute_typed_recall.protocol",
                       exception_type="MemoryValidationError",exact_reason="typed_recall_protocol_unsupported",
                       original_runner_label="recall-v5",companion_bindings="Keep context/plan and idempotency_key; pass exact int harness_protocol=5; not string/type rejection")
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
        row["public_witness_stage"] = "protocol" if attack=="protocol_version" else "ownership" if attack=="principal_id" else "narrowing" if attack=="context_hash" else "idempotency"
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
    return "PASS" if check_rejection_witness(row,observed) else "BLOCKED"


def check_attack_inputs(fixture, row, observed, baseline):
    """Verify exact mutated arguments separately from exception observations.

    JSON-envelope hashing checks the existing public context companion binding.
    The separate validation-state commitment retains its NUL preimage.
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
    if field == "protocol_version":
        if type(observed.get("harness_protocol")) is not int or observed["harness_protocol"]!=5:
            raise ValueError("protocol attack must be integer5, not bad type")
    elif field == "principal_id":
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


def check_rejection_control(fixture, baseline, before_manifest):
    control=assess_cell(fixture,{'cell_id':'unsupported-replay/exact-replay','status':'OBSERVED',
        'reason':'','observations':baseline})
    if control['status']=='FAIL' or not control['business_assertions']:
        raise ValueError('rejection no-fault control failed: '+control['reason'])
    first,replay=baseline['first'],baseline['replay']
    check_execution_wire(first,baseline['context'],baseline['plan'])
    check_execution_wire(replay,baseline['context'],baseline['plan'])
    item=first['result']['items'][0]
    if (first['candidate_query_count']!=1 or not first['candidate_query_started'] or first['replayed']
            or item['score']!=round(.30/61,12)
            or item['evidence_manifest_hash']!=hash_json(['evidence-relation-1'])
            or first['result']['confirmation_groups']):
        raise ValueError('rejection no-fault candidate/evidence/rank differs')
    protected_hash(before_manifest['manifest'],before_manifest['payload_hash'],FINAL_TABLES)
    roots={r['table_name']:r for r in before_manifest['manifest']['table_roots']}
    controls=[]
    for label in ('before_control','after_control'):
        state=baseline[label]
        protected_hash(state['manifest'],state['payload_hash'],FINAL_TABLES)
        controls.append({r['table_name']:r for r in state['manifest']['table_roots']})
    for name in FINAL_TABLES:
        delta=1 if name in {'typed_recall_decisions','typed_recall_decision_items','typed_recall_results',
            'typed_recall_result_items','typed_recall_terminals'} else 0
        if (controls[1][name]['row_count']-controls[0][name]['row_count']!=delta
                or roots[name]!=controls[1][name]):
            raise ValueError('rejection terminal control delta/binding differs '+name)
    for name in ('typed_recall_decisions','typed_recall_decision_items','typed_recall_results',
                 'typed_recall_result_items','typed_recall_terminals'):
        if roots[name]['row_count']<1:
            raise ValueError('rejection durable baseline missing '+name)


def assess_cell(fixture, cell, baseline=None):
    """Evaluate independent business assertions, then retain remaining gates.

    Only complete independent business and binding checks admit a cell.
    Missing witnesses stay BLOCKED; a failed assertion takes precedence.
    """
    name, observed = cell["cell_id"], cell["observations"]
    if cell["status"] != "OBSERVED":
        return {"status": cell["status"], "reason": cell["reason"], "business_assertions": []}
    if "context_use_cell" in observed:
        return assess_context_use(fixture,cell)
    if "short_recipe" in observed:
        return assess_short(fixture,cell)
    if "state_cell" in observed:
        return assess_state(fixture,cell)
    if "return_recipe" in observed:
        return assess_return(fixture,cell)
    if "conflict_recipe" in observed:
        return assess_conflict(fixture,cell)
    if "recipe" in observed:
        return assess_normal(fixture, cell)
    checks, blockers = [], []
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
            rejection_status=judge_rejection(row, observed)
            check_attack_inputs(fixture, row, observed, baseline)
            check_rejection_control(fixture, baseline, observed["before_manifest"])
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
            if rejection_status=="BLOCKED":
                blockers += ["PUBLIC_EXCEPTION_HAS_NO_PER_INVOCATION_CANDIDATE_READ_WITNESS","INTERNAL_REJECTION_LAYER_HAS_NO_PUBLIC_WITNESS"]
            else:
                checks.append("public frozen invocation-bound pre-candidate receipt with exact inputs/stage/reason/zero reads")
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
        if not blockers and name.startswith("unsupported-replay/request-hash:"):
            return {"status":"PASS","reason":"","business_assertions":checks}
        if not blockers:
            blockers.append("FULL_ORIGINAL_CELL_ADMISSION_PENDING")
        return {"status": "BLOCKED", "reason": ";".join(blockers), "business_assertions": checks}
    except (ValueError, KeyError, IndexError, TypeError, StopIteration) as exc:
        return {"status": "FAIL", "reason": str(exc) or type(exc).__name__, "business_assertions": checks}


def check_execution_wire(value, context, plan):
    decision, result = value['decision'], value['result']
    if (value['decision_hash'] != sdk_domain_hash('simple-harness/recall-decision/v4',decision)
            or value['result_hash'] != sdk_domain_hash('simple-harness/typed-recall-result/v1',result)
            or result['decision_hash'] != value['decision_hash']
            or result['decision_id'] != decision['decision_id']
            or decision['context_hash'] != sdk_domain_hash('simple-harness/recall-context/v2',context)
            or decision['plan_hash'] != sdk_domain_hash('simple-harness/recall-plan/v2',plan)
            or decision['subject'] != context['subject'] or decision['run_id'] != context['run_id']
            or decision['plan_id'] != plan['plan_id'] or decision['context_revision'] != context['context_revision']
            or decision['disclosure_context'] != context['disclosure_context']):
        raise ValueError('full public hash/identity/context binding differs')
    if decision['selected_items'] != [r['selected_item'] for r in result['items']]:
        raise ValueError('decision/result item bindings differ')
    if value['result_item_hashes'] != [sdk_domain_hash('simple-harness/typed-recall-result-item/v1',r) for r in result['items']]:
        raise ValueError('result item hash differs')
    if (result['evaluated_at'] != decision['decided_at'] or result['authority_expires_at'] > context['expires_at']
            or result['authority_expires_at'] <= result['evaluated_at']):
        raise ValueError('result authority interval differs')
    if [r['selected_item']['ordinal'] for r in result['items']] != list(range(1,len(result['items'])+1)):
        raise ValueError('selected ordinals differ')


def normal_expected(fixture, recipe):
    name, spec = recipe['cell_id'],recipe['seed']
    if recipe['family'] in {'validity','lifecycle'}:
        row = next(r for r in fixture['eligibility_cases']+fixture['lifecycle_cases'] if name=='eligibility/'+r['id'])
        return row['expected']=='ELIGIBLE'
    if recipe['family']=='epistemic':
        return any(spec['memory_type'] in r['memory_types'] and spec['epistemic']==r['epistemic']
            and spec['verification']==r['verification'] and r['expected']=='ELIGIBLE'
            for r in fixture['epistemic_verification_cases'])
    if recipe['family']=='disclosure':
        return any(r['recipient']==recipe['recipient'] and r['purpose']==recipe['purpose']
            and recipe['privacy'] in r['allowed'] for r in fixture['disclosure_cases'])
    if recipe['family']=='attribute':
        return False
    return recipe.get('modes') != ['vector']


def normal_projection(spec):
    payload = copy.deepcopy(spec['payload'])
    if spec['memory_type']=='semantic':
        payload['qualifiers'] = []
    elif spec['memory_type']=='episode':
        from datetime import datetime
        interval=payload.pop('occurred_interval')
        payload.update(occurred_start=datetime.fromisoformat(interval['start'].replace('Z','+00:00')).timestamp(),
                       occurred_end=datetime.fromisoformat(interval['end'].replace('Z','+00:00')).timestamp())
    return payload


def assess_normal(fixture, cell):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('normal_inputs',Path(__file__).with_name('typed_recall_normal_inputs.py'))
    compiler=importlib.util.module_from_spec(spec);spec.loader.exec_module(compiler)
    recipe=next(r for r in compiler.recipes(fixture) if r['cell_id']==cell['cell_id'])
    observed=cell['observations'];checks=[];blockers=[]
    try:
        if observed['recipe']!=recipe or not observed['calls']:
            raise ValueError('consumer recipe/input identity differs')
        if observed.get('exception'):
            # Seed/DTO refusal is a real observation, never a recall PASS.
            return dict(status='BLOCKED',reason='PUBLIC_CASE_PRECONDITION_REJECTED:'+observed['exception']['type']+':'+observed['exception']['reason'],
                business_assertions=['actual public operation and exact rejection recorded'])
        if recipe['seed']['memory_type'] in {'procedure','prospective'}:
            blockers.append('REQUIRED_APPLICABILITY_OR_SIGNAL_AUTHORITY_NOT_ESTABLISHED')
        if recipe['family']=='projection':
            blockers.append('FULL_SOURCE_RECORD_CANARY_AND_CROSS_SCOPE_SETUP_NOT_ESTABLISHED')
            if recipe['seed']['memory_type']=='episode':
                blockers.append('ORIGINAL_EPISODE_OCCURRED_INTERVAL_DIFFERS_FROM_PUBLIC_PROJECTION')
        if not observed['sources'] or len(observed['recalls']) != (2 if recipe['family']=='budget' else 1):
            raise ValueError('missing actual source or recall')
        source=observed['sources'][-1]
        op=source['receipt']['operations'][0]
        projection=normal_projection(recipe['seed'])
        kind=recipe['seed']['memory_type']
        full_source = (semantic_source(**projection) if kind=='semantic' else
            {'memory_type':'episode',**projection,'thread_ref':None} if kind=='episode' else None)
        if source['input']!=recipe['seed'] or (full_source is not None and source['source_wire']!=full_source):
            raise ValueError('source DTO differs from independent inputs')
        history=recipe.get('lifecycle_path',[recipe['seed']])
        if len(observed['sources'])!=len(history):raise ValueError('lifecycle history length differs')
        evidence_refs=[]
        for ordinal,(step,entry) in enumerate(zip(history,observed['sources'],strict=True)):
            if entry['input']!=step:raise ValueError('lifecycle history inputs differ')
            check_seed_authority(observed,{**recipe,'seed':step},full_source,source_index=ordinal,check_recall_refs=False)
            event=next(e for e in observed['calls'] if e['call']=='apply_memory_mutation_plan' and e['plan']['plan_id']==entry['receipt']['plan_id'])
            operation=event['plan']['operations'][0];actual=entry['receipt']['operations'][0]
            if (operation['payload']!=entry['source_wire']
                    or operation['epistemic_status']!=step.get('epistemic','explicit_user')
                    or operation['verification_state']!=step.get('verification','source_bound')
                    or operation['proposed_privacy_class']!=recipe.get('privacy','personal').lower()
                    or operation['proposed_information_attributes']!=recipe.get('attributes',[])):
                raise ValueError('intermediate lifecycle payload/frozen mutation arguments differ')
            if ordinal:check_action_grant(observed,event['plan'])
            expected_kind='create' if ordinal==0 else 'supersede' if step['state']=='superseded' else 'revise'
            target=None if ordinal==0 else {'memory_id':observed['sources'][ordinal-1]['receipt']['operations'][0]['memory_id'],
                'revision':ordinal,'target_kind':'existing_memory'}
            if (operation['kind']!=expected_kind or actual['revision']!=ordinal+1
                    or operation['lifecycle_state']!=step.get('state','pending' if kind=='prospective' else 'active')
                    or (ordinal and (operation['target']!=target or actual['memory_id']!=target['memory_id']
                                     or operation['action_authority_ref'] is None))):
                raise ValueError('lifecycle actual kind/target/revision/authority differs')
            for span in operation['evidence_spans']:
                evidence_refs.append({'evidence_id':span['evidence_id'],
                    'content_hash':span['envelope_hash'],'ordinal':len(evidence_refs)+1})
        if any(r['context']['evidence_refs']!=evidence_refs or r['plan']['evidence_refs']!=evidence_refs for r in observed['recalls']):
            raise ValueError('recall does not bind complete lifecycle evidence history')
        for index, recall in enumerate(observed['recalls']):
            value=recall['execution'];decision=value['decision'];result=value['result'];items=result['items']
            context,plan=recall['context'],recall['plan']
            payload_input=recipe['seed']['payload']
            query=str(payload_input.get('object_value',payload_input.get('title',payload_input.get('name',payload_input.get('action')))))
            if (context['query']!=query or plan['query']!=query
                    or context['available_memory_types']!=[kind] or plan['requested_memory_types']!=[kind]
                    or context['allowed_retrieval_modes']!=recipe.get('modes',['full_text'])
                    or plan['retrieval_modes']!=context['allowed_retrieval_modes']
                    or context['disclosure_context']['recipient']!=recipe.get('recipient','user_self').lower()
                    or context['disclosure_context']['purpose']!=recipe.get('purpose','personalization').lower()
                    or context['subject']!='principal-1' or not context['evidence_refs']):
                raise ValueError('actual recall arguments differ from frozen recipe')
            mutation=next(event['plan'] for event in reversed(observed['calls']) if event['call']=='apply_memory_mutation_plan')
            operation=mutation['operations'][0]
            if (operation['payload']!=source['source_wire'] or operation['kind']!=('create' if len(history)==1 else 'supersede' if recipe['seed']['state']=='superseded' else 'revise')
                    or operation['epistemic_status']!=recipe['seed'].get('epistemic','explicit_user')
                    or operation['verification_state']!=recipe['seed'].get('verification','source_bound')
                    or operation['lifecycle_state']!=recipe['seed'].get('state','pending' if kind=='prospective' else 'active')
                    or operation['proposed_privacy_class']!=recipe.get('privacy','personal').lower()
                    or operation['proposed_information_attributes']!=recipe.get('attributes',[])):
                raise ValueError('actual mutation arguments differ from frozen recipe')
            from datetime import datetime
            frozen_time=recipe.get('now',1788170400.0)
            if isinstance(frozen_time,str):frozen_time=datetime.fromisoformat(frozen_time.replace('Z','+00:00')).timestamp()
            if recall['now']!=frozen_time or decision['decided_at']!=frozen_time or result['evaluated_at']!=frozen_time:
                raise ValueError('actual recall/evaluation clock differs from frozen input')
            if recipe['family']=='validity':
                interval={key: None if recipe['seed'].get(key) is None else datetime.fromisoformat(recipe['seed'][key].replace('Z','+00:00')).timestamp() for key in ('valid_from','valid_until')}
                if operation['valid_time_interval']!=interval:
                    raise ValueError('actual validity interval differs from frozen boundary')
            check_execution_wire(value,recall['context'],recall['plan'])
            replay=recall['replay']
            if (replay['decision']!=decision or replay['result']!=result or not replay['replayed']
                    or replay['candidate_query_count']!=0 or replay['candidate_query_started']):
                raise ValueError('durable exact replay differs')
            selected=normal_expected(fixture,recipe) and not (recipe['family']=='budget' and index==1)
            if not blockers or kind not in {'procedure','prospective'}:
                if len(items)!=int(selected) or result['confirmation_groups'] or decision['confirmation_groups']:
                    raise ValueError('original inclusion/exclusion oracle differs')
                globally_denied=(recipe.get('recipient','user_self').lower() not in {'user_self','household','task_collaborator'}
                    or recipe.get('purpose','personalization').lower() not in {'task_execution','personalization','task_resume','user_review'})
                expected_outcome='recall' if selected else 'rejected' if globally_denied else 'no_recall'
                expected_reason=['recall_user_fact_dependency'] if selected else ['recall_disclosure_denied'] if globally_denied else ['recall_budget_exhausted'] if recipe['family']=='budget' and index==1 else ['recall_no_eligible_memory']
                if decision['outcome']!=expected_outcome or decision['reason_codes']!=expected_reason:
                    raise ValueError('eligibility execution outcome/reason differs; invalid-plan rejection cannot pass')
                if value['candidate_query_count']!=int(not globally_denied) or value['candidate_query_started']!=bool(not globally_denied):
                    raise ValueError('actual eligibility execution witness differs')
                filtered=int(selected)
                if decision['filtered_candidate_count']!=filtered or decision['candidate_count_stage']!='after_all_eligibility_gates':
                    raise ValueError('filtered candidate count/gate stage differs')
            for item in items:
                selected_item=item['selected_item']
                if (item['public_payload']!=projection or selected_item['public_payload_hash']!=hash_json(projection)
                        or selected_item['source_ref']!=op['memory_id'] or selected_item['source_revision']!=op['revision']
                        or op['revision']!=len(history) or selected_item['source_kind']!='cognitive_memory'
                        or selected_item['memory_type']!=kind or item['evidence_manifest_hash']!=hash_json(sorted(source.get('evidence_ids',[source['evidence_id']])))
                        or full_source is None or selected_item['source_content_hash']!=hash_json(full_source)
                        or item['score']!=round(.30/61,12) or item['cross_scope'] or item['source_task_scope_ids']
                        or item['effective_privacy_class']!=recipe.get('privacy','personal').lower()
                        or item['information_attributes']!=recipe.get('attributes',[])):
                    raise ValueError('input-derived source/projection/rank/classification/evidence binding differs')
            if recipe['family']=='budget':
                limits={**recipe['limits'][0],**(recipe['limits'][1] if index else {})}
                if any(recall['plan']['budget'][key]!=value for key,value in limits.items()):
                    raise ValueError('literal limits changed')
                envelope=[dict(memory_type=kind,payload=projection,source_kind='cognitive_memory')]
                raw=canonical(envelope);tokens=max(1,len(raw.decode()),(len(raw)+2)//3)
                original=next(r for r in fixture['budget_oracle']['literal_cases'] if cell['cell_id'].endswith(r['id']))
                if len(raw)!=original['utf8_bytes'] or tokens!=original['token_estimate']:
                    raise ValueError('independent literal units differ; no threshold migration allowed')
                if index==1 and (not result['truncated'] or decision['outcome']!='no_recall' or decision['reason_codes']!=['recall_budget_exhausted']):
                    raise ValueError('budget rejection lacks truncation signal')
            if recipe['family']=='vector':
                codes=['cognitive_vector_unavailable'] if 'vector' in recipe['modes'] else []
                if value['degradation_codes']!=codes:
                    raise ValueError('ordered vector degradation reason differs')
                blockers.append('PUBLIC_EXECUTED_LANE_WITNESS_UNAVAILABLE')
        checks += ['original eligibility/whole-item selection assertions','independent full source/projection/evidence/rank bindings',
                   'full public hash and result identity bindings','actual durable exact replay with zero candidate reads']
        return dict(status='BLOCKED' if blockers else 'PASS',reason=';'.join(sorted(set(blockers))),business_assertions=checks)
    except (ValueError,KeyError,TypeError,IndexError,StopIteration) as exc:
        return dict(status='FAIL',reason=str(exc),business_assertions=checks)


def check_two_source_control(control, sources):
    """Must succeed before any source-side injected fault runs."""
    value=control['execution'];check_execution_wire(value,control['context'],control['plan'])
    items=value['result']['items'];decision=value['decision']
    if len(sources)!=2 or len(items)!=2 or decision['filtered_candidate_count']!=2 or decision['outcome']!='recall':
        raise ValueError('no-fault control must independently recall two seeded sources')
    if value['result']['confirmation_groups'] or value['result']['truncated'] or value['replayed']:
        raise ValueError('no-fault control carrier/budget differs')
    ordered=sorted(sources,key=lambda s:s['receipt']['operations'][0]['memory_id'])
    for rank,(source,item) in enumerate(zip(ordered,items,strict=True),1):
        payload=source['input']['payload'];op=source['receipt']['operations'][0]
        wire=semantic_source(**payload);binding=item['selected_item']
        if (source['source_wire']!=wire or item['public_payload']!=payload or binding['source_content_hash']!=hash_json(wire)
                or binding['public_payload_hash']!=hash_json(payload) or binding['source_ref']!=op['memory_id']
                or binding['source_revision']!=1 or op['revision']!=1 or binding['memory_type']!='semantic'
                or item['evidence_manifest_hash']!=hash_json([source['evidence_id']])
                or item['score']!=round(.30/(60+rank),12)):
            raise ValueError('no-fault source/content/evidence/order oracle differs')
    return True


def assess_source(cell):
    o=cell['observations'];checks=[]
    if cell['status']!='OBSERVED':return dict(status=cell['status'],reason=cell['reason'],business_assertions=[])
    try:
        import importlib.util
        from pathlib import Path
        path=Path(__file__).with_name('typed_recall_source_oracle.py')
        spec=importlib.util.spec_from_file_location('source_state_oracle',path)
        state=importlib.util.module_from_spec(spec);spec.loader.exec_module(state)
        if o.get('corruption'):
            control=o['control'];check_execution_wire(control['execution'],control['context'],control['plan'])
            members=control['execution']['decision']['confirmation_groups'][0]['members']
            if [m['source_revision'] for m in members]!=[7,8] or len(o['before_members'])!=2:
                raise ValueError('corruption no-fault source pair differs')
            expected_count=1 if 'one-member' in cell['cell_id'] else 3 if 'three-members' in cell['cell_id'] else 2
            if len(o['after_members'])!=expected_count or 'recalled' in o or not o.get('exception'):
                raise ValueError('actual corrupted member state did not reject reopen')
            state.check_corruption_state(o,cell['cell_id'],check_seed_authority)
            return dict(status='PASS',reason='',
                business_assertions=['real revision7/8 no-fault confirmation control','actual member corruption followed by reopen rejection'])
        check_two_source_control(o['control'],o['sources'])
        checks.append('independent two-source no-fault business control before injection')
        seam=cell['cell_id'].split('fault:',1)[1]
        mapping={'decision-header':('typed_recall.after_decision_header',1,'pre_commit'),
            'between-decision-items':('typed_recall.after_decision_item',1,'pre_commit'),
            'before-result-header':('typed_recall.after_decision_item',2,'pre_commit'),
            'between-result-items':('typed_recall.after_result_item',1,'pre_commit'),
            'before-terminal-fence':('typed_recall.after_result_item',2,'pre_commit'),
            'commit-before-ack':('typed_recall.after_commit',1,'post_commit_ack_loss')}
        recovered=o['recovery']['execution'];control=o['control']['execution']
        check_execution_wire(recovered,o['recovery']['context'],o['recovery']['plan'])
        exact=recovered['decision']==control['decision'] and recovered['result']==control['result']
        if seam=='restart-open-rebuild':
            if not exact or not recovered['replayed'] or recovered['candidate_query_count']!=0 or o['immediate']!=o['control_after']:
                raise ValueError('restart exact terminal replay differs')
        else:
            point,ordinal,phase=mapping[seam]
            if (o['fault_point'],o['fault_ordinal'],o['phase'])!=(point,ordinal,phase):
                raise ValueError('wrong source fault location/ordinal')
            if o['exception']!={'type':'InjectedFault','reason':point} or o['hits'].count(point)!=ordinal or o['hits'][-1]!=point:
                raise ValueError('fault did not execute at the frozen transaction seam')
            if set(o['before'])!=set(FINAL_TABLES) or set(o['immediate'])!=set(FINAL_TABLES) or set(o['control_after'])!=set(FINAL_TABLES):
                raise ValueError('source final-table projection incomplete')
            check_fault(phase,control_business_valid=True,before=o['before'],immediate=o['immediate'],
                control_after=o['control_after'],replay_exact=exact and recovered['replayed'],replay_query_count=recovered['candidate_query_count'])
            if not exact:raise ValueError('recovery business result differs from valid control')
        checks += ['exact injected seam/ordinal or restart','fixed pre-commit old / post-ACK exact committed state','reopened real recovery and hash-identical business result']
        state.check_fault_state(o,seam,FINAL_TABLES)
        checks.append('complete source schema/PK/request/attempt/terminal and unchanged nonfinal roots')
        return dict(status='PASS',reason='',business_assertions=checks)
    except (ValueError,KeyError,TypeError,IndexError,StopIteration) as exc:
        return dict(status='FAIL',reason=str(exc),business_assertions=checks)


def assess_conflict(fixture,cell):
    o=cell['observations'];checks=[]
    try:
        op=o['revision7']
        if op['revision']!=7 or op['evidence_ids']!=['evidence-user-python-311']:
            raise ValueError('original revision7/incumbent evidence setup differs')
        sources=o['sources']
        if [s['receipt']['operations'][0]['revision'] for s in sources[:7]]!=list(range(1,8)):
            raise ValueError('revision7 is not backed by actual append history')
        checks.append('real authorized append history 1..7 with frozen incumbent evidence')
        if o.get('exception') or o.get('rejection'):
            return dict(status='BLOCKED',reason='CONFLICT_REJECTION_OR_PRECONDITION_REQUIRES_FULL_ORACLE',business_assertions=checks)
        value=o['confirmation']['execution'];check_execution_wire(value,o['confirmation']['context'],o['confirmation']['plan'])
        decision=value['decision'];groups=decision['confirmation_groups']
        if len(groups)!=1 or decision['selected_items'] or decision['filtered_candidate_count']!=2 or decision['outcome']!='needs_user_confirmation':
            raise ValueError('complete contested pair not one atomic confirmation carrier')
        members=groups[0]['members']
        if [m['source_revision'] for m in members]!=[7,8] or any(m['source_ref']!=op['memory_id'] for m in members):
            raise ValueError('confirmation exact revision/identity binding differs')
        for member,name in zip(members,('incumbent','challenger'),strict=True):
            payload={**fixture['conflict_write_oracle']['canonical_payloads'][name],'qualifiers':[]}
            if member['source_content_hash']!=hash_json(semantic_source(**payload)) or member['public_payload_hash']!=hash_json(payload):
                raise ValueError('conflict source and projection hashes differ')
        checks.append('one real atomic confirmation group with exact r7/r8 and distinct independent hashes')
        if 'after_resolution' in o:
            after=o['after_resolution']['execution'];check_execution_wire(after,o['after_resolution']['context'],o['after_resolution']['plan'])
            if o['revision9']['revision']!=9 or after['decision']['confirmation_groups'] or after['result']['confirmation_groups']:
                raise ValueError('resolution must append revision9 and remove active group')
            checks.append('real authorized revision9; resolved group absent from new recall')
        return dict(status='BLOCKED',reason='CONFLICT_DURABLE_GROUP_MEMBER_RESOLUTION_HASH_ORACLE_PENDING',business_assertions=checks)
    except (ValueError,KeyError,TypeError,IndexError) as exc:
        return dict(status='FAIL',reason=str(exc),business_assertions=checks)


def check_seed_authority(observed,recipe,full_source,*,source_index=0,check_recall_refs=True):
    source=observed['sources'][source_index];view=source['receipt'];op=view['operations'][0]
    event=next(e for e in observed['calls'] if e['call']=='apply_memory_mutation_plan' and e['plan']['plan_id']==view['plan_id'])
    plan=event['plan'];operation=plan['operations'][0];result=event['result']
    plan_hash=sdk_domain_hash('simple-harness/memory-mutation-plan/v5',plan)
    if (event['result_hash']!=sdk_domain_hash('simple-harness/memory-mutation-apply-result/v4',result)
            or result['outcome']!='committed' or result['confirmation_items']
            or result['plan_hash']!=plan_hash or view['plan_hash']!=plan_hash
            or result['plan_id']!=plan['plan_id'] or view['plan_id']!=plan['plan_id']
            or result['subject']!=plan['subject'] or result['run_id']!=plan['run_id']
            or result['turn_id']!=plan['turn_id'] or view['apply_mode']!=plan['apply_mode']
            or result['receipt_ref']!={'receipt_id':view['receipt_id'],'receipt_hash':view['receipt_hash']}
            or len(view['operations'])!=1 or op['operation_id']!=operation['operation_id']
            or op['memory_type']!=recipe['seed']['memory_type']
            or op['epistemic_status']!=operation['epistemic_status']
            or op['content_hash']!=hash_json(source['source_wire'])
            or (full_source is not None and op['content_hash']!=hash_json(full_source))):
        raise ValueError('mutation result/ref/view/source/plan binding differs')
    spans=operation['evidence_spans'];eid=source['evidence_id']
    dual=recipe['seed'].get('epistemic','explicit_user')=='explicit_user' and recipe['seed'].get('verification') in {'source_verified','repeated_observation'}
    expected_ids=[eid,eid+'-verification'] if dual else [eid]
    if [s['evidence_id'] for s in spans]!=expected_ids or op['evidence_ids']!=sorted(expected_ids):
        raise ValueError('operation spans/receipt evidence membership differs')
    refs=[]
    for index,span in enumerate(spans):
        prefix='User memory assertion: ' if index==0 else 'Fixture tool observation: '
        eh=check_admitted_span(observed,recipe,span,expected_ids[index],prefix)
        refs.append({'evidence_id':expected_ids[index],'content_hash':eh,'ordinal':index+1})
    if dual and (spans[0]['actor_role']!='user' or spans[0]['provenance']!='authenticated_user'
            or spans[0]['support_kind'] not in {'explicit_user_assertion','explicit_user_correction'}
            or spans[0]['typed_observation'] is not None or spans[1]['typed_observation'] is None):
        raise ValueError('verified user assertion lost independent USER plus TOOL support')
    if plan['evidence_refs']!=refs or (check_recall_refs and any(r['context']['evidence_refs']!=refs or r['plan']['evidence_refs']!=refs for r in observed['recalls'])):
        raise ValueError('mutation/recall evidence refs differ from admitted source')


def check_admitted_span(observed,recipe,span,eid,prefix):
    admitted=next(e for e in observed['calls'] if e['call']=='ingest_committed_evidence' and e['evidence_id']==eid)
    envelope,admission=admitted['envelope'],admitted['admission']
    eh=sdk_domain_hash('simple-harness/sanitized-evidence-envelope/v2',envelope)
    ah=sdk_domain_hash('simple-harness/sanitized-evidence-receipt/v2',admission)
    text=prefix+canonical(recipe['seed']['payload']).decode()
    source_hash=hashlib.sha256(text.encode()).hexdigest()
    if (envelope['sanitized_payload']!={'item_id':eid+'-item','public_text':text}
            or envelope['sanitized_hash']!=hash_json(envelope['sanitized_payload'])
            or envelope['source_hash']!=source_hash or admitted['envelope_hash']!=eh
            or admission['envelope_hash']!=eh or not admission['accepted'] or admitted['admission_hash']!=ah
            or admission['evidence_id']!=eid or admission['source_hash']!=source_hash
            or admission['sanitized_hash']!=envelope['sanitized_hash']
            or span['admission_receipt_hash']!=ah or span['admission_receipt_id']!=admission['receipt_id']
            or span['envelope_hash']!=eh or span['sanitized_hash']!=envelope['sanitized_hash']
            or span['source_hash']!=source_hash or span['quote_hash']!=source_hash
            or span['exact_quote']!=text or span['start_byte']!=0 or span['end_byte']!=len(text.encode())):
        raise ValueError('admitted evidence/envelope/span authority chain differs')
    if span.get('typed_observation') is not None:
        typed=next(e for e in observed['calls'] if e['call']=='resolve_typed_observation' and e['receipt']['evidence_id']==eid)
        receipt=typed['receipt'];ref=span['typed_observation']
        schema={'type':'string','description':'Admitted public memory assertion text'}
        expected_ref=dict(schema_id='observation/typed-recall-public-text',schema_version=1,
            registered_schema_hash=hash_json(schema),observation_receipt_id=eid+'-typed',
            observation_receipt_hash=sdk_domain_hash('simple-harness/typed-observation-authority-receipt/v2',receipt),
            authority_issuer_id='host-typed-observation-authority',json_pointer='/public_text',value_hash=hash_json(text))
        if (typed['schema']!=schema or ref!=expected_ref or typed['reference']!=ref
                or typed['receipt_hash']!=ref['observation_receipt_hash']
                or receipt!={**{k:ref[k] for k in ('schema_id','schema_version','registered_schema_hash','json_pointer','value_hash')},
                    'receipt_id':eid+'-typed','evidence_id':eid,'envelope_hash':eh,'sanitized_hash':envelope['sanitized_hash'],
                    'admission_receipt_id':admission['receipt_id'],'admission_receipt_hash':ah,'item_ordinal':1,
                    'item_id':eid+'-item','item_json_pointer':'/public_text','accepted':True,'issuer_ref':ref['authority_issuer_id']}):
            raise ValueError('typed authority source/schema/value/receipt binding differs')
        external=recipe['seed'].get('epistemic')=='verified_external'
        if (envelope['source_kind']!=('provider_record' if external else 'tool_result')
                or span['actor_role']!=('external' if external else 'tool')
                or span['provenance']!=('external_source' if external else 'trusted_tool')
                or span['support_kind']!='typed_observation'):
            raise ValueError('typed authority provenance differs')
    return eh


def assess_return(fixture,cell):
    o=cell['observations'];name=cell['cell_id'].split('/',1)[1];checks=[]
    try:
        baseline=o['baseline'];value=baseline['execution'];check_execution_wire(value,baseline['context'],baseline['plan'])
        payload=next(r['provider_payload'] for r in fixture['approved_oracle']['semantic_source_vectors'] if r['id']=='incumbent')
        if len(value['result']['items'])!=1 or value['result']['items'][0]['public_payload']!=payload:
            raise ValueError('return-path no-fault baseline business content differs')
        expected={
            'strict-v3-rejected':('ValueError','unsupported RecallDecisionV4 schema_version'),
            'invalid-source-discriminant':('ValueError',"'unknown_source' is not a valid RecallSourceKind"),
            'cognitive-missing-revision':('ValueError','cognitive memory requires memory_type and exact revision'),
            'short-fake-revision':('ValueError','short-horizon item cannot carry memory_type or revision'),
            'naked-source-ref':('TypeError','request must use RecallResultPageRequestV1'),
            'page-wrong-result-hash':('MemoryValidationError','typed_recall_result_binding_invalid'),
            'page-wrong-coordinate':('MemoryValidationError','typed_recall_page_offset_invalid'),
            'page-expired-result':('MemoryValidationError','typed_recall_result_expired')}
        if name=='page-correct-binding':
            if o.get('exception')=={'type':'MemoryLimitError','reason':'typed_recall_page_budget_too_small'}:
                if o['page_input']['max_bytes']!=128:raise ValueError('original page bound changed')
                return dict(status='BLOCKED',reason='FROZEN_128_BYTE_PAGE_BOUND_CANNOT_FIT_PUBLIC_BINDING',
                    business_assertions=['actual result-bound page attempted at unchanged 128-byte bound'])
            check_page(o['returned'],value['result'],value['result_hash'],value['result_item_hashes'])
        else:
            if o.get('exception')!={'type':expected[name][0],'reason':expected[name][1]} or 'returned' in o:
                raise ValueError('public parser/page did not reject at exact contract reason')
        for access in ('before','after'):
            bound=o[access];digest=protected_hash(bound['manifest'],bound['payload_hash'],sorted(set(MUTATION_TABLES+FINAL_TABLES)))
            if access=='before':before=digest
            elif digest!=before:raise ValueError('parser/page changed protected state')
        checks += ['real no-fault public result before attack','exact public parser/page rejection or page binding',
                   'independent protected manifest unchanged']
        return dict(status='BLOCKED',reason='RETURN_CELL_COMPLETE_ATTACK_AND_READ_WITNESS_ADMISSION_PENDING',business_assertions=checks)
    except (ValueError,KeyError,TypeError,IndexError) as exc:
        return dict(status='FAIL',reason=str(exc),business_assertions=checks)


def assess_state(fixture,cell):
    o=cell['observations'];checks=[];name=cell['cell_id']
    try:
        if o.get('exception'):
            return dict(status='BLOCKED',reason='STATE_PUBLIC_PRECONDITION:'+o['exception']['reason'],business_assertions=[])
        recall=o['recall'];value=recall['execution'];check_execution_wire(value,recall['context'],recall['plan'])
        replay=o['replay']
        if replay['result']!=value['result'] or replay['decision']!=value['decision'] or not replay['replayed'] or replay['candidate_query_count']!=0:
            raise ValueError('state-path exact replay differs')
        decision=value['decision'];items=value['result']['items'];groups=decision['confirmation_groups']
        if name.endswith('/ordinary-contested'):
            if items or len(groups)!=1 or [m['source_revision'] for m in groups[0]['members']]!=[7,8]:
                raise ValueError('ordinary contested escaped atomic confirmation carrier')
        elif name.endswith('/current-head') or name.endswith('/ordinary-resolved'):
            revision=8 if name.endswith('/current-head') else 9
            if len(items)!=1 or groups or items[0]['selected_item']['source_revision']!=revision:
                raise ValueError('current/resolved exact head binding differs')
            payload_name='challenger' if revision==8 else 'incumbent'
            payload={**fixture['conflict_write_oracle']['canonical_payloads'][payload_name],'qualifiers':[]}
            if items[0]['public_payload']!=payload or items[0]['selected_item']['source_content_hash']!=hash_json(semantic_source(**payload)):
                raise ValueError('head payload/content differs from independent input')
        else:
            if items or groups or decision['filtered_candidate_count']!=0 or decision['outcome']!='no_recall' or decision['reason_codes']!=['recall_no_eligible_memory']:
                raise ValueError('stale/suppressed/partial source leaked public content')
            if 'suppressed' in name or 'partial' in name:
                event=next(e for e in o['calls'] if e['call']=='suppress')
                target='evidence-user-python-312' if 'partial' in name else o['sources'][0]['receipt']['operations'][0]['memory_id']
                if event['request']['scope_ref']!=target or event['decision']['scope_ref']!=target or event['decision']['action']!='directive':
                    raise ValueError('suppression actual target differs')
        checks=['actual authorized state transition and original business disclosure assertion','independent current payload/hash or zero disclosure','durable exact replay']
        return dict(status='BLOCKED',reason='STATE_COMPLETE_RECEIPT_AND_PROTECTED_TRANSITION_BINDING_PENDING',business_assertions=checks)
    except (ValueError,KeyError,TypeError,IndexError) as exc:
        return dict(status='FAIL',reason=str(exc),business_assertions=checks)


def check_rejection_witness(row,observed):
    import uuid
    witness=observed.get('rejection_receipt')
    if witness is None:return False
    fields={'schema_version','invocation_id','request_hash','context_hash','plan_hash','stage','reason',
            'candidate_query_started','candidate_query_count'}
    if set(witness)!=fields or witness['schema_version']!=1 or type(witness['schema_version']) is not int:
        raise ValueError('rejection receipt fields/schema differ')
    if observed.get('rejection_receipt_is_public_type') is not True or observed.get('rejection_receipt_is_frozen') is not True:
        raise ValueError('rejection receipt is not the immutable public DTO')
    if type(witness["invocation_id"]) is not str:
        raise ValueError("rejection invocation id invalid")
    identity=uuid.UUID(witness['invocation_id'])
    if identity.version!=4 or str(identity)!=witness['invocation_id']:
        raise ValueError('rejection invocation id invalid')
    if witness['stage']!=row['public_witness_stage'] or witness['reason']!=row['exact_reason']:
        raise ValueError('rejection receipt stage/reason differs')
    if witness['candidate_query_started'] is not False or type(witness['candidate_query_count']) is not int or witness['candidate_query_count']!=0:
        raise ValueError('rejection receipt does not witness zero candidate access')
    context,plan=observed['context'],observed['plan']
    expected_request=None if row['original_attack']=='protocol_version' else sdk_domain_hash(
        'simple-harness-memory/typed-recall-request/v1',dict(harness_protocol='recall-v4',memory_protocol='typed-recall-v1',
            principal_id=observed['principal_actor_id'],context=context,plan=plan))
    if (witness['context_hash']!=sdk_domain_hash('simple-harness/recall-context/v2',context)
            or witness['plan_hash']!=sdk_domain_hash('simple-harness/recall-plan/v2',plan)
            or witness['request_hash']!=expected_request):
        raise ValueError('rejection receipt does not bind full actual request inputs')
    return True


def assess_short(fixture,cell):
    o=cell['observations'];name=cell['cell_id'];checks=[]
    try:
        if o.get('exception'):
            return dict(status='BLOCKED',reason='SHORT_PUBLIC_PRECONDITION:'+o['exception']['reason'],
                business_assertions=['actual public registration/projection/recall calls recorded'])
        registrations=[e for e in o['calls'] if e['call']=='register_conversation_evidence']
        if len(registrations)!=11:raise ValueError('short history does not leave target outside recent10')
        value=o['recall']['execution'];context=o['recall']['context'];plan=o['recall']['plan']
        check_execution_wire(value,context,plan)
        replay=o['replay']
        if replay['decision']!=value['decision'] or replay['result']!=value['result'] or not replay['replayed'] or replay['candidate_query_count']!=0:
            raise ValueError('short exact replay differs')
        if not context['short_horizon_allowed'] or not plan['include_short_horizon']:
            raise ValueError('short selector not invoked')
        text=next(r for r in fixture['minimal_projection_oracle'] if r['memory_type']=='short_horizon')['source_record']['content']
        expected='user: '+text
        items=value['result']['items'];short=[r for r in items if r['selected_item']['source_kind']=='short_horizon']
        excluded=name in {'eligibility/short-future','eligibility/short-source-suppressed'}
        if len(short)!=int(not excluded):raise ValueError('short independent inclusion/suppression assertion differs')
        for item in short:
            selected=item['selected_item']
            if (item['public_payload']['content']!=expected or set(item['public_payload'])!={'content','occurred_at'}
                    or selected['memory_type'] is not None or selected['source_revision'] is not None
                    or selected['public_payload_hash']!=hash_json(item['public_payload'])):
                raise ValueError('short source discriminant/minimal payload binding differs')
        if name=='protocol/mixed-long-short':
            cognitive=[r for r in items if r['selected_item']['source_kind']=='cognitive_memory']
            source=next(v for v in fixture['approved_oracle']['semantic_source_vectors'] if v['id']=='incumbent')
            if len(items)!=2 or len(cognitive)!=1 or cognitive[0]['selected_item']['source_revision']!=3 or cognitive[0]['public_payload']!=source['provider_payload']:
                raise ValueError('mixed actual r3 cognitive and short result differs')
        elif len(items)!=len(short):raise ValueError('short-only fabricated cognitive source')
        if name=='eligibility/short-source-suppressed':
            before=o['before'];check_execution_wire(before['execution'],before['context'],before['plan'])
            if len(before['execution']['result']['items'])!=1 or o['suppression']['request']['scope_ref']!='conversation-evidence-1':
                raise ValueError('suppression has no successful independent source control')
        checks+=['11 genuine public registration groups and oldest target projection',
            'actual short/mixed source discriminants and independent input content','exact durable replay and zero candidate reads']
        return dict(status='BLOCKED',reason='SHORT_COMPLETE_REGISTRATION_CLASSIFICATION_TIME_AND_ORIGINAL_HASH_BINDINGS_PENDING',business_assertions=checks)
    except (ValueError,KeyError,TypeError,IndexError,StopIteration) as exc:
        return dict(status='FAIL',reason=str(exc),business_assertions=checks)


def check_action_grant(observed,plan):
    operation=plan['operations'][0];ref=operation['action_authority_ref']
    event=next(e for e in observed['calls'] if e['call']=='resolve_memory_action_authority' and e['reference']==ref)
    grant=event['grant']
    operation_input={k:v for k,v in operation.items() if k not in {'action_authority_ref','operation_intent_hash'}}
    operation_hash=sdk_domain_hash('simple-harness/memory-mutation-operation-intent/v4',operation_input)
    plan_input={k:v for k,v in plan.items() if k!='plan_intent_hash'}
    plan_input['operations']=[{**operation_input,'operation_intent_hash':operation_hash}]
    plan_hash=sdk_domain_hash('simple-harness/memory-mutation-plan-intent/v5',plan_input)
    intent=dict(schema_version=2,subject=plan['subject'],action=operation['kind'],
        target_memory_id=operation['target']['memory_id'],target_revision=operation['target']['revision'],
        evidence_refs=plan['evidence_refs'],evidence_span_hashes=sorted(sdk_domain_hash('simple-harness/evidence-span-ref/v2',span) for span in operation['evidence_spans']),
        run_id=plan['run_id'],turn_id=plan['turn_id'],plan_id=plan['plan_id'],plan_intent_hash=plan_hash,
        operation_id=operation['operation_id'],canonical_operation_index=1,operation_intent_hash=operation_hash)
    intent_hash=sdk_domain_hash('simple-harness/memory-action-intent/v2',intent)
    replay=sdk_domain_hash('simple-harness/memory-action-replay-identity/v2',dict(
        authority_id=grant['authority_id'],intent_hash=intent_hash,nonce=grant['nonce'],issuer_ref=grant['issuer_ref']))
    authority_hash=sdk_domain_hash('simple-harness/memory-action-authority/v2',grant)
    expected_ref=dict(schema_version=2,authority_id=grant['authority_id'],authority_hash=authority_hash,
        issuer_ref='host-case-actions',replay_identity=replay)
    if (operation['operation_intent_hash']!=operation_hash or plan['plan_intent_hash']!=plan_hash
            or grant['intent']!=intent or grant['intent_hash']!=intent_hash or grant['replay_identity']!=replay
            or grant['schema_version']!=2 or grant['issuer_ref']!='host-case-actions'
            or event['authority_hash']!=authority_hash or ref!=expected_ref
            or not grant['issued_at']<=event['now']<grant['expires_at']):
        raise ValueError('resolved memory action grant/intent/target/hash/time binding differs')


def assess_context_use(fixture,cell):
    if cell['observations'].get('executor_version') == 2:
        return _full_context_use_oracle().assess(fixture, cell, globals())
    o=cell['observations'];checks=[]
    try:
        if o.get('exception'):
            return dict(status='FAIL',reason='PUBLIC_LOOP_EXECUTION_FAILED:'+o['phase']+':'+o['exception']['reason'],
                business_assertions=[],failure_phase=o['phase'])
        payloads=[{**fixture['conflict_write_oracle']['canonical_payloads'][name],'qualifiers':[]}
                  for name in ('incumbent','challenger')]
        memory_id=o['sources'][0]['receipt']['operations'][0]['memory_id']
        for index,(key,payload) in enumerate(zip(('initial','corrected'),payloads,strict=True)):
            source=o['sources'][index];op=source['receipt']['operations'][0]
            check_seed_authority(o,{'seed':{'memory_type':'semantic','payload':payload}},semantic_source(**payload),
                source_index=index,check_recall_refs=False)
            mutation=next(e['plan'] for e in o['calls'] if e['call']=='apply_memory_mutation_plan' and e['plan']['plan_id']==source['receipt']['plan_id'])
            operation=mutation['operations'][0]
            if (op['memory_id']!=memory_id or op['revision']!=index+1 or operation['payload']!=semantic_source(**payload)
                    or operation['kind']!=('create' if index==0 else 'revise')):
                raise ValueError('product loop mutation input/head differs')
            if index:
                if operation['target']!={'target_kind':'existing_memory','memory_id':memory_id,'revision':1}:
                    raise ValueError('product loop correction did not target exact original head')
                check_action_grant(o,mutation)
            recall=o[key];value=recall['execution'];check_execution_wire(value,recall['context'],recall['plan'])
            items=value['result']['items']
            if (len(items)!=1 or items[0]['public_payload']!=payload or value['decision']['confirmation_groups']
                    or items[0]['selected_item']['source_ref']!=memory_id
                    or items[0]['selected_item']['source_revision']!=index+1
                    or items[0]['selected_item']['source_content_hash']!=hash_json(semantic_source(**payload))
                    or items[0]['selected_item']['public_payload_hash']!=hash_json(payload)):
                raise ValueError('product loop recall exposed wrong value/head/hash')
        checks.append('public remember and authorized exact-head correction return independent3.11 then3.12')
        expected_uses={
            'initial_use':('initial','attempt-initial'),
            'initial_duplicate':('initial','attempt-initial'),
            'initial_new_attempt':('initial','attempt-initial-new'),
            'old_after_correction':('initial','attempt-old-after-correction'),
            'corrected_use':('corrected','attempt-corrected'),
            'corrected_replay_after_reopen':('corrected','attempt-corrected'),
            'new_after_forget':('corrected','attempt-new-after-forget'),
        }
        if cell['cell_id']=='current-use/context:suppression-first':
            expected_uses={k:v for k,v in expected_uses.items() if k in {'old_after_correction','new_after_forget'}}
        if set(o['uses'])!=set(expected_uses):raise ValueError('current-use phase coverage differs')
        for key,(recall_key,attempt) in expected_uses.items():
            bundle=o['uses'][key]
            if (bundle['recall']!=o[recall_key] or bundle['request']['provider_attempt_id']!=attempt
                    or bundle['request']['requested_at']!=o[recall_key]['now']
                    or bundle['fragment']['fragment_id']!='fragment-'+recall_key):
                raise ValueError('current-use bundle substituted across recall phase or attempt: '+key)
            check_context_use_bundle(bundle)
        if cell['cell_id']!='current-use/context:suppression-first':
            for key in ('initial_use','initial_duplicate','initial_new_attempt','corrected_use','corrected_replay_after_reopen'):
                if 'receipt' not in o['uses'][key]:raise ValueError('legal current use rejected: '+key)
            if o['uses']['initial_use']['receipt']!=o['uses']['initial_duplicate']['receipt']:
                raise ValueError('same attempt receipt replay differs')
            if o['uses']['corrected_use']['receipt']!=o['uses']['corrected_replay_after_reopen']['receipt']:
                raise ValueError('durable identical use receipt replay differs')
            if o['uses']['initial_use']['receipt']['receipt_id']==o['uses']['initial_new_attempt']['receipt']['receipt_id']:
                raise ValueError('new provider attempt reused old use receipt')
        for key in ('old_after_correction','new_after_forget'):
            event=o['uses'][key]
            if event.get('exception')!={'type':'MemoryValidationError','reason':'RECALL_AUTHORITY_STALE'}:
                raise ValueError('fresh use of stale result was not rejected: '+key)
        if cell['cell_id']=='current-use/context:suppression-first' and any('receipt' in e for e in o['uses'].values()):
            raise ValueError('suppression-first obtained an earlier use receipt')
        if cell['cell_id']=='current-use/context:wrong-snapshot':
            attack=o['wrong_snapshot']
            base=o['uses']['corrected_use']['request']
            if attack['input']!={**base,'snapshot_manifest_hash':'f'*64} or attack.get('exception')!={
                    'type':'ValueError','reason':'snapshot_manifest_hash differs from fragment bindings'}:
                raise ValueError('wrong snapshot did not reject exact independent mutation')
        suppression=o['suppression'];request=suppression['request'];decision=suppression['decision']
        if (request['scope_kind']!='memory' or request['scope_ref']!=memory_id or request['purpose'] is not None
                or request['subject']!='principal-1' or request['reason_code']!='user_forget'
                or decision['scope_ref']!=memory_id or decision['action']!='directive'):
            raise ValueError('forget was not all-purpose exact-memory public suppression')
        fresh=o['after_reopen'];check_execution_wire(fresh['execution'],fresh['context'],fresh['plan'])
        if (fresh['execution']['result']['items'] or fresh['execution']['decision']['confirmation_groups']
                or fresh['execution']['decision']['outcome']!='no_recall'):
            raise ValueError('forgotten memory leaked after reopen')
        old=o['corrected']['execution'];replay=o['old_recall_replay']
        if replay['result']!=old['result'] or replay['decision']!=old['decision'] or not replay['replayed'] or replay['candidate_query_count']!=0:
            raise ValueError('historical recall replay lost its exact durable binding')
        checks+=['real page/fragment/request/receipt hashes and invocation bindings',
            'fresh attempts reject stale result after correction and all-purpose forget',
            'reopen has zero fresh disclosure, historical identical replay remains separate']
        return dict(status='BLOCKED',reason='CURRENT_USE_ORIGINAL_TWO_ITEM_EPOCH_CONTINUATION_ORACLE_PENDING',business_assertions=checks)
    except (ValueError,KeyError,TypeError,IndexError,StopIteration) as exc:
        return dict(status='FAIL',reason=str(exc),business_assertions=checks)


def check_context_use_bundle(bundle):
    if bundle.get('executor_version') == 2:
        return _full_context_use_oracle().check_bundle(bundle, globals())
    recall=bundle['recall'];value=recall['execution'];check_execution_wire(value,recall['context'],recall['plan'])
    result=value['result'];decision=value['decision'];page=bundle['page'];fragment=bundle['fragment'];request=bundle['request']
    check_page(page,result,value['result_hash'],value['result_item_hashes'])
    page_hash=sdk_domain_hash('simple-harness/recall-result-page/v1',page)
    item=result['items'][0];selected=item['selected_item']
    binding=dict(decision_id=decision['decision_id'],decision_hash=value['decision_hash'],result_id=result['result_id'],
        result_hash=value['result_hash'],item_id=selected['item_id'],item_hash=value['result_item_hashes'][0],
        conflict_group_id=None,confirmation_hash=None,result_group_hash=None,page_id=page['page_id'],page_hash=page_hash,
        use_receipt_id=None,use_receipt_hash=None,public_payload_hash=hash_json(item['public_payload']))
    if (bundle['page_hash']!=page_hash or fragment['recall_binding']!=binding or fragment['public_payload']!=item['public_payload']
            or fragment['public_payload_hash']!=binding['public_payload_hash'] or fragment['subject']!=recall['context']['subject']
            or fragment['run_id']!=recall['context']['run_id'] or fragment['source_ref']!=selected['source_ref']
            or fragment['source_revision']!=selected['source_revision'] or fragment['fragment_type']!='recalled_memory'
            or fragment['disclosure_context']!=recall['context']['disclosure_context']
            or fragment['evidence_refs']!=recall['context']['evidence_refs']):
        raise ValueError('actual page-to-context fragment binding differs')
    fragment_hash=sdk_domain_hash('simple-harness/context-fragment/v2',fragment)
    refs=[{'fragment_id':fragment['fragment_id'],'fragment_hash':fragment_hash}]
    if (bundle['fragment_hash']!=fragment_hash or request['snapshot_fragment_bindings']!=refs
            or request['snapshot_manifest_hash']!=hash_json(refs)
            or request['item_bindings']!=[{'item_id':selected['item_id'],'item_hash':value['result_item_hashes'][0]}]
            or request['decision_id']!=decision['decision_id'] or request['decision_hash']!=value['decision_hash']
            or request['result_id']!=result['result_id'] or request['result_hash']!=value['result_hash']
            or any(request[k]!=recall['context'][k] for k in ('subject','run_id','turn_id'))):
        raise ValueError('current-use request fragment/result/invocation binding differs')
    if 'receipt' in bundle:
        receipt=bundle['receipt']
        if (bundle['receipt_hash']!=sdk_domain_hash('simple-harness/recall-context-use-receipt/v1',receipt)
                or receipt['request_hash']!=sdk_domain_hash('simple-harness/recall-context-use-request/v1',request)
                or any(receipt[k]!=request[k] for k in ('subject','run_id','turn_id','provider_attempt_id','decision_id',
                    'decision_hash','result_id','result_hash','item_bindings','snapshot_manifest_hash'))
                or not request['requested_at']<=receipt['authorized_at']<receipt['expires_at']
                or receipt['expires_at']>result['authority_expires_at']):
            raise ValueError('current-use receipt request/hash/time binding differs')


def _full_context_use_oracle():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).with_name('typed_recall_context_use_oracle.py')
    spec = importlib.util.spec_from_file_location('typed_recall_context_use_oracle', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
