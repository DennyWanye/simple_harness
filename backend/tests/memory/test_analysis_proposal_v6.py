"""v6：模型可提出 applies_to 语义关系；端点必须是同一提案内的 semantic claim，SDK 校验依赖与自环。"""
from dataclasses import replace

import pytest

from deskpet.memory import analysis_proposal as v3, analysis_proposal_v5_1 as v5_1
from deskpet.memory import analysis_proposal_v6 as v6, analysis_protocol
from tests.memory.test_procedure_adoption import compilation

TEXT = "以后整理文件就按这两步：先列清单，再复制到备份目录。整理文件时备份目录用外接硬盘。"
from tests.memory.test_procedure_adoption import operation as _procedure_op


def _request(case, protocol):
    return replace(case.request, prompt_version=protocol.PROMPT_VERSION,
        result_schema_version=protocol.RESULT_SCHEMA_VERSION, policy_version=protocol.POLICY_VERSION)


def _claim(op_id, item_id, quote, predicate, value):
    return {"operation_id": op_id, "memory_type": "semantic", "action": "create", "candidate_key": "",
            "evidence_item_id": item_id, "exact_quote": quote, "reason_code": "explicit_user_statement",
            "semantic": {"subject_entity": "user:self", "predicate": predicate, "object_value": value}}


def _relation(op_id, item_id, quote, source, target):
    return {"operation_id": op_id, "memory_type": "semantic_relation", "action": "create",
            "evidence_item_id": item_id, "exact_quote": quote, "reason_code": "explicit_user_statement",
            "semantic_relation": {"relation_kind": "applies_to", "source_operation_id": source,
                                  "target_operation_id": target}}


def _proposal(item_id, relation=None):
    procedure = _procedure_op(item_id, TEXT, intent="adoption")
    ops = [_claim("claim-lang", item_id, "备份目录用外接硬盘", "backup_target", "外接硬盘"),
           procedure]
    if relation is not None:
        ops.append(relation)
    return {"outcome": "mutate", "operations": ops}


def test_v6_wire_is_frozen_and_schema_has_relation_branch():
    # v6 wire stays frozen and resolvable after the v7 bump.
    assert analysis_protocol.protocol_for_request(_request(compilation(), v6)) is v6
    branches = v6.PROPOSAL_TOOL_SCHEMA["properties"]["operations"]["items"]["anyOf"]
    kinds = [b["properties"]["memory_type"]["enum"] for b in branches]
    assert ["semantic_relation"] in kinds and len(kinds) == 5
    # v5.1 wire is untouched.
    assert len(v5_1.PROPOSAL_TOOL_SCHEMA["properties"]["operations"]["items"]["anyOf"]) == 4
    assert "applies_to" in v6.ANALYSIS_SYSTEM_INSTRUCTION


def test_relation_compiles_to_sdk_payload_with_dependencies():
    case = compilation(TEXT)
    item = case.items[0]
    proposal = _proposal(item.item_id, _relation("rel-1", item.item_id, "整理文件时备份目录用外接硬盘", "claim-lang", "procedure-1"))
    compiled = v6.compile_proposal(proposal, request=_request(case, v6), items=case.items,
        base_revision=1, plan_id="host-v6-unit", now=1.0)
    assert compiled.outcome == "mutate" and compiled.rejected == ()
    ops = {op.operation_id: op for op in compiled.plan.operations}
    rel = ops["rel-1"]
    assert rel.memory_type.value == "semantic" and rel.payload.semantic_kind.value == "relation"
    assert rel.payload.relation_kind.value == "applies_to"
    assert rel.payload.source_endpoint.operation_id == "claim-lang"
    assert rel.payload.target_endpoint.operation_id == "procedure-1"
    assert set(rel.depends_on_operation_ids) == {"claim-lang", "procedure-1"}
    assert rel.evidence_spans[0].exact_quote == "整理文件时备份目录用外接硬盘"
    # Structured result round-trips through the canonical plan wire.
    assert compiled.structured_result["operations"][2]["payload"]["semantic_kind"] == "relation"


@pytest.mark.parametrize("source,target,code", [
    ("claim-lang", "claim-lang", v6.RELATION_SELF_LOOP),
    ("claim-lang", "claim-missing", v6.RELATION_ENDPOINT_UNKNOWN),
    ("procedure-1", "claim-lang", v6.RELATION_ENDPOINT_UNKNOWN),
    ("rel-1", "procedure-1", v6.RELATION_ENDPOINT_UNKNOWN),
])
def test_relation_endpoint_rules_reject_only_the_relation(source, target, code):
    case = compilation(TEXT)
    item = case.items[0]
    proposal = _proposal(item.item_id, _relation("rel-1", item.item_id, "先列清单", source, target))
    compiled = v6.compile_proposal(proposal, request=_request(case, v6), items=case.items,
        base_revision=1, plan_id="host-v6-unit", now=1.0)
    assert compiled.outcome == "mutate"
    assert [r.operation_id for r in compiled.rejected] == ["rel-1"]
    assert compiled.rejected[0].code == code
    assert [op.operation_id for op in compiled.plan.operations] == ["claim-lang", "procedure-1"]


def test_relation_with_rejected_endpoint_claim_is_rejected_by_dependency():
    case = compilation(TEXT)
    item = case.items[0]
    proposal = _proposal(item.item_id, _relation("rel-1", item.item_id, "先列清单", "claim-lang", "procedure-1"))
    proposal["operations"][0]["exact_quote"] = "这句话不在证据里"  # claim-lang rejected → relation dependency unsatisfied
    with pytest.raises(Exception, match="dependency|unknown|depends"):
        v6.compile_proposal(proposal, request=_request(case, v6), items=case.items,
            base_revision=1, plan_id="host-v6-unit", now=1.0)


def test_persisted_v5_1_request_keeps_v5_1_compiler():
    case = compilation(TEXT)
    item = case.items[0]
    proposal = _proposal(item.item_id, _relation("rel-1", item.item_id, "先列清单", "claim-lang", "procedure-1"))
    assert analysis_protocol.protocol_for_request(_request(case, v5_1)) is v5_1
    compiled = v5_1.compile_proposal(proposal, request=_request(case, v5_1), items=case.items,
        base_revision=1, plan_id="host-v51-unit", now=1.0)
    assert [r.operation_id for r in compiled.rejected] == ["rel-1"]
