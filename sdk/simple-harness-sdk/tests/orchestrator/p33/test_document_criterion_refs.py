"""Doc9 submission references preserve exact frozen IDs, never repair bad hashes.

Ordinal references are a versioned input spelling only. They do not grant evidence,
relax criteria, mutate the original Provider output, or change persisted Claim shape.
"""

import copy

import pytest
from doc5_helpers import graph_service, node

from agent_orchestrator.contracts import ContractError
from agent_orchestrator.governance import domains
from agent_orchestrator.verification.assessments import (
    criterion_id,
    mission_contract_revision,
    mission_criterion_catalog,
    task_contract_revision,
)
from agent_orchestrator.verification.document_refs import expand_document_claim_refs


@pytest.fixture
def frozen(tmp_path):
    service, mission, tasks = graph_service(
        tmp_path, domain=domains.DOC_DOMAIN,
        nodes=[node("A", outputs=["report.md"], success_criteria=["file:report.md"])],
    )
    task = next(iter(tasks.values())) if isinstance(tasks, dict) else tasks[0]
    contract = {**task.to_json(), "task_id": task.id}
    config = {"task_contract": contract,
              "mission_contract_revision": mission_contract_revision(mission),
              "mission_criteria": list(mission_criterion_catalog(mission))}
    yield mission, config, criterion_id(task_contract_revision(contract), 1, "file:report.md")
    service.store.close()


def test_exact_frozen_refs_leave_input_and_evidence_unchanged(frozen):
    mission, config, task_id = frozen
    raw = {"task_id": config["task_contract"]["task_id"], "claims": [
        {"content": "not evidence", "criterion_refs": [1], "mission_criterion_refs": [1],
         "citations": [{"quote": "unchanged"}]}]}
    before = copy.deepcopy(raw)
    result = expand_document_claim_refs(raw, intent_config=config, mission=mission)
    assert raw == before
    assert result["claims"] == [{"content": "not evidence",
        "criterion_ids": [task_id],
        "mission_criterion_ids": [config["mission_criteria"][0]["criterion_id"]],
        "citations": [{"quote": "unchanged"}]}]
    assert task_id != config["mission_criteria"][0]["criterion_id"]


@pytest.mark.parametrize("refs", [[0], [-1], [True], [1.0], ["1"], [99], [1, 1], None, "1"])
@pytest.mark.parametrize("field", ["criterion_refs", "mission_criterion_refs"])
def test_invalid_refs_fail_without_guessing(frozen, refs, field):
    mission, config, _ = frozen
    with pytest.raises(ContractError):
        expand_document_claim_refs({"task_id": config["task_contract"]["task_id"],
            "claims": [{field: refs}]}, intent_config=config, mission=mission)


@pytest.mark.parametrize("field", ["criterion", "mission_criterion"])
def test_full_ids_and_refs_cannot_ambiguously_coexist(frozen, field):
    mission, config, _ = frozen
    with pytest.raises(ContractError):
        expand_document_claim_refs({"task_id": config["task_contract"]["task_id"],
            "claims": [{field + "_refs": [1], field + "_ids": []}]},
            intent_config=config, mission=mission)


@pytest.mark.parametrize("damage", ["revision", "catalog", "task"])
def test_different_frozen_identity_is_rejected(frozen, damage):
    mission, config, _ = frozen
    changed = copy.deepcopy(config)
    if damage == "revision":
        changed["mission_contract_revision"] = "a" * 64
    elif damage == "catalog":
        changed["mission_criteria"][0]["criterion_id"] = "criterion-" + "a" * 64
    else:
        changed["task_contract"]["task_id"] = "another-task"
    with pytest.raises(ContractError):
        expand_document_claim_refs({"task_id": config["task_contract"]["task_id"],
            "claims": [{"criterion_refs": [1]}]}, intent_config=changed, mission=mission)
