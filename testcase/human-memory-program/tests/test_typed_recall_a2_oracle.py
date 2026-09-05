"""Independent oracle tests: these are never product acceptance receipts."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("a2", ROOT / "runners/typed_recall_a2_oracle.py")
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)


def test_canonical_source_known_answer_and_domain_distinction():
    value = oracle.semantic_source("user", "preferred_python", "3.11", [])
    assert value["object_value_hash"] == hashlib.sha256(b'"3.11"').hexdigest()
    assert set(value) == {"memory_type", "semantic_kind", "subject_entity", "predicate",
                          "object_value", "object_value_hash", "qualifiers"}
    assert oracle.canonical({"z": None, "a": ["中", 1]}) == '{"a":["中",1],"z":null}'.encode()
    assert oracle.domain_hash("d", {"a": 1}) == hashlib.sha256(b'd\x00{"a":1}').hexdigest()
    assert oracle.domain_hash("d", {"a": 1}) != oracle.hash_json({"domain": "d", "payload": {"a": 1}})
    with pytest.raises(ValueError):
        oracle.canonical(float("nan"))


def test_frozen_full_public_vectors_use_json_domain_and_state_keeps_nul():
    fixture = json.loads((ROOT / "fixtures/typed-recall-v3.json").read_text())
    answers = ["79cf1d1cd36907e9840d0d231444ae9289c90d694220d94a3ad962ec0bc33a47",
               "b2a130ca51bedfe98d88bf9616d5988d872f698b6b6e9ad9632c33516e2903df",
               "49f834c86095bb51f6c7cdfc5989fc33f5c1297b463daf4efd7851cc656ac379",
               "4941b2effff255ba3889d7bedd3e32aa0c9acb2a8df3aceb90663ab988b2b0f4"]
    for vector, answer in zip(fixture["approved_oracle"]["public_hash_vectors"], answers, strict=True):
        assert oracle.sdk_domain_hash(vector["domain"], vector["payload"]) == answer == vector["expected_sha256"]
        assert hashlib.sha256(vector["canonical_preimage"].encode()).hexdigest() == answer
        assert oracle.domain_hash(vector["domain"], vector["payload"]) != answer


def manifest():
    return {"schema_version": 1, "storage_schema_version": 7, "schema_checksum": "a" * 64,
            "initialization_receipt_hash": "b" * 64, "principal_ref_hash": "c" * 64,
            "total_row_count": 1, "table_roots": [
                {"category": "test", "table_name": "protected", "row_count": 1,
                 "root_hash": "d" * 64, "first_leaf_hash": "e" * 64, "last_leaf_hash": "e" * 64}]}


def test_manifest_projection_rejects_missing_duplicate_and_payload_tamper():
    value = manifest()
    original = oracle.protected_hash(value, oracle.hash_json(value), ["protected"])
    changed = copy.deepcopy(value)
    changed["table_roots"][0]["root_hash"] = "f" * 64
    assert oracle.protected_hash(changed, oracle.hash_json(changed), ["protected"]) != original
    with pytest.raises(ValueError):
        oracle.protected_hash(changed, oracle.hash_json(value), ["protected"])
    with pytest.raises(ValueError):
        oracle.protected_hash(value, oracle.hash_json(value), ["missing"])
    value["table_roots"].append(copy.deepcopy(value["table_roots"][0]))
    with pytest.raises(ValueError):
        oracle.protected_hash(value, oracle.hash_json(value), ["protected"])


@pytest.mark.parametrize("phase", ["pre_commit", "post_commit_ack_loss"])
def test_control_must_pass_business_oracle_before_it_can_be_reference(phase):
    with pytest.raises(ValueError, match="business"):
        oracle.check_fault(phase, control_business_valid=False, before="old", immediate="new",
                           control_after="new", replay_exact=True, replay_query_count=0)


def test_fault_phase_cannot_choose_old_or_new():
    args = dict(control_business_valid=True, before="old", control_after="new",
                replay_exact=True, replay_query_count=0)
    oracle.check_fault("pre_commit", immediate="old", **args)
    oracle.check_fault("post_commit_ack_loss", immediate="new", **args)
    with pytest.raises(ValueError):
        oracle.check_fault("pre_commit", immediate="new", **args)
    with pytest.raises(ValueError):
        oracle.check_fault("post_commit_ack_loss", immediate="old", **args)
    args["replay_query_count"] = 1
    with pytest.raises(ValueError):
        oracle.check_fault("post_commit_ack_loss", immediate="new", **args)


def test_fourteen_attacks_are_preserved_and_fail_closed_without_read_witness():
    mappings = oracle.mutation_map()
    assert len(mappings) == 14
    assert len({row["original_attack"] for row in mappings}) == 14
    for row in mappings:
        assert {"public_path", "mutation", "companion_bindings", "reject_layer", "exact_reason",
                "memory_call", "candidate_query_count", "precondition"} <= set(row)
    assert type(mappings[0]["mutation"]) is int and mappings[0]["mutation"]==5
    assert mappings[0]["exact_reason"]=="typed_recall_protocol_unsupported"
    row = next(row for row in mappings if row["original_attack"] == "plan_id")
    assert oracle.judge_rejection(row, {"exception_type": "MemoryIdempotencyConflict",
               "exception_reason": "IDEMPOTENCY_CONFLICT", "memory_called": True}) == "BLOCKED"
    with pytest.raises(ValueError):
        oracle.judge_rejection(row, {"exception_type": "ValueError", "exception_reason": "bad binding",
                                   "memory_called": True})


def test_revision_preserves_all_original_scenarios_thresholds_and_negatives():
    fixture = json.loads((ROOT / "fixtures/typed-recall-v3.json").read_text())
    revised = fixture["approved_oracle"]
    for key, frozen_hash in revised["unchanged_sections_sha256"].items():
        assert oracle.hash_json(fixture[key]) == frozen_hash, key
    assert revised["mutation_mapping"] == oracle.mutation_map()
    assert {row["original_attack"] for row in revised["mutation_mapping"]} == {
        row["path"] for row in fixture["request_hash_oracle"]["one_field_mutations"]}
    for vector in revised["semantic_source_vectors"]:
        assert oracle.hash_json(vector["source"]) == vector["source_content_hash"]
    assert revised["state"]["old_new_tags_are_state_evidence"] is False


def test_replay_of_wrong_business_result_is_not_a_valid_control():
    fixture = json.loads((ROOT / "fixtures/typed-recall-v3.json").read_text())
    value = {"decision": {"outcome": "recall"}, "result": {"items": []},
             "candidate_query_count": 0, "candidate_query_started": False, "replayed": True}
    row = {"cell_id": "unsupported-replay/exact-replay", "status": "OBSERVED",
           "reason": "", "observations": {"first": value, "replay": copy.deepcopy(value)}}
    result = oracle.assess_cell(fixture, row)
    assert result["status"] == "FAIL"
    assert "baseline business" in result["reason"]


def test_early_parser_rejection_cannot_verify_replay_attack():
    fixture = json.loads((ROOT / "fixtures/typed-recall-v3.json").read_text())
    row = {"cell_id": "unsupported-replay/request-hash:run_id", "status": "OBSERVED", "reason": "",
        "observations": {"memory_called": False, "exception_type": "ValueError",
                         "exception_reason": "disclosure_context run_id differs"}}
    result = oracle.assess_cell(fixture, row)
    assert result["status"] == "FAIL"
    assert "wrong rejection" in result["reason"]


def test_noop_attack_and_changed_idempotency_key_are_rejected():
    fixture = json.loads((ROOT / "fixtures/typed-recall-v3.json").read_text())
    original = fixture["request_hash_oracle"]["base_request"]
    baseline = {"context": {"subject": original["principal_id"], "run_id": original["run_id"],
        "context_revision": original["context_revision"], "budget": original["budget"]},
        "plan": {"plan_id": "plan-1", "idempotency_key": "same-key"}}
    row = next(r for r in oracle.mutation_map() if r["original_attack"] == "plan_id")
    observed = {**copy.deepcopy(baseline), "principal_actor_id": "principal-1"}
    with pytest.raises(ValueError, match="attack or required"):
        oracle.check_attack_inputs(fixture, row, observed, baseline)
    observed["plan"]["plan_id"] = "plan-2"
    oracle.check_attack_inputs(fixture, row, observed, baseline)
    observed["plan"]["idempotency_key"] = "different-key"
    with pytest.raises(ValueError, match="attack or required"):
        oracle.check_attack_inputs(fixture, row, observed, baseline)


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(result_id="other-result"),
    lambda p: p.update(bindings=[]),
    lambda p: p["bindings"][0].update(item_hash="b" * 64),
    lambda p: p.update(byte_count=0),
])
def test_successful_but_misbound_or_empty_page_fails(mutation):
    result = {"result_id": "result-1", "items": [{"selected_item": {"ordinal": 1, "item_id": "item-1"}}]}
    binding = {"binding_kind": "selected_item", "ordinal": 1, "item_id": "item-1", "item_hash": "a" * 64}
    page = {"result_id": "result-1", "result_hash": "c" * 64, "page_ordinal": 1,
            "item_offset": 0, "complete": True, "bindings": [binding], "byte_count": len(oracle.canonical(binding))}
    oracle.check_page(page, result, "c" * 64, ["a" * 64])
    mutation(page)
    with pytest.raises(ValueError, match="successful page"):
        oracle.check_page(page, result, "c" * 64, ["a" * 64])


def rejection_observation():
    fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
    vector=next(v for v in fixture['approved_oracle']['public_hash_vectors'] if v['domain']=='simple-harness-memory/typed-recall-request/v1')
    request=vector['payload']
    row=next(r for r in oracle.mutation_map() if r['original_attack']=='plan_id')
    observed=dict(context=request['context'],plan=request['plan'],principal_actor_id=request['principal_id'],
        exception_type=row['exception_type'],exception_reason=row['exact_reason'],memory_called=True,
        rejection_receipt_is_public_type=True,rejection_receipt_is_frozen=True,
        rejection_receipt=dict(schema_version=1,invocation_id='71b79f0a-b86b-4972-a6a5-0c80c9d98ace',
            request_hash=vector['expected_sha256'],
            context_hash=next(v['expected_sha256'] for v in fixture['approved_oracle']['public_hash_vectors'] if v['domain']=='simple-harness/recall-context/v2'),
            plan_hash=next(v['expected_sha256'] for v in fixture['approved_oracle']['public_hash_vectors'] if v['domain']=='simple-harness/recall-plan/v2'),
            stage='idempotency',reason='IDEMPOTENCY_CONFLICT',candidate_query_started=False,candidate_query_count=0))
    return row,observed


@pytest.mark.parametrize('field,value',[('request_hash','f'*64),('context_hash','f'*64),('plan_hash','f'*64),
    ('stage','protocol'),('reason','typed_recall_protocol_invalid'),('candidate_query_count',1),
    ('candidate_query_count',False),('candidate_query_started',True),('invocation_id','not-a-uuid')])
def test_rejection_witness_rejects_cross_call_or_wrong_scope(field,value):
    row,observed=rejection_observation()
    assert oracle.judge_rejection(row,observed)=='PASS'
    observed['rejection_receipt'][field]=value
    with pytest.raises(ValueError):oracle.judge_rejection(row,observed)


def test_protocol_integer5_cannot_be_replaced_by_type_rejection():
    row=oracle.mutation_map()[0]
    assert row['original_runner_label']=='recall-v5' and type(row['mutation']) is int and row['mutation']==5
    with pytest.raises(ValueError):
        oracle.judge_rejection(row,dict(exception_type='MemoryValidationError',exception_reason='typed_recall_protocol_invalid',memory_called=True))


def test_rejection_control_cannot_use_empty_baseline():
    fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
    empty={'decision':{'outcome':'recall'},'result':{'items':[]}}
    with pytest.raises(ValueError,match='no-fault control failed'):
        oracle.check_rejection_control(fixture,{'first':empty,'replay':empty},{})
