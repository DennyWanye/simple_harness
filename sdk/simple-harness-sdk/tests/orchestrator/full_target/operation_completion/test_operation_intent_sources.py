"""OC3 operation-intent source input stays strict and authority-free on the wire."""

from __future__ import annotations

import dataclasses
import json

import pytest
from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.contracts.operation_intents import (
    OperationIntentSourceKind,
    SubmitOperationIntentV2,
)
from agent_orchestrator.contracts.semantic_base import Provenance, TypedRef, TypedRefKind

HASH_A = "a" * 64
HASH_B = "b" * 64


def _wire() -> dict[str, object]:
    return {
        "schema_version": 2,
        "mission_id": "mission-oc3",
        "idempotency_key": "submit-1",
        "intent_source": {
            "kind": "AUTHORIZED_SLOT",
            "origin_receipt_id": "receipt-1",
            "slot_key": "slot-1",
        },
        "candidate_artifact_ref": TypedRef(
            TypedRefKind.ARTIFACT, "artifact-1", 1, HASH_A, Provenance.TOOL
        ).to_json(),
        "prepared_acceptance_refs": [
            TypedRef(TypedRefKind.ACCEPTANCE, "acceptance-1", 1, HASH_B, Provenance.TOOL).to_json()
        ],
        "supersedes_intent_id": None,
        "completion_slot": {"spec_hash": HASH_A, "effect_key": "send-report"},
    }


def test_submit_operation_intent_v2_is_strict_and_contains_no_caller_authority() -> None:
    command = SubmitOperationIntentV2.from_json(_wire())

    assert command.intent_source.kind is OperationIntentSourceKind.AUTHORIZED_SLOT
    assert command.completion_slot.effect_key == "send-report"
    assert "principal" not in command.to_json()
    assert "tenant_id" not in command.to_json()

    for injected in ({"principal": "model"}, {"authority": True}, {"tenant_id": "other"}):
        with pytest.raises(ContractError):
            SubmitOperationIntentV2.from_json(_wire() | injected)


def test_only_a_complete_authorized_slot_is_a_source() -> None:
    """人提交申请单的来源已删（用户 2026-10-20）：旧种类与缺授权字段都是合同错。"""

    for source in (
        {"kind": "USER_COMMAND"},
        {"kind": "AUTHORIZED_SLOT"},
        {"kind": "AUTHORIZED_SLOT", "origin_receipt_id": "receipt-1"},
    ):
        broken = _wire()
        broken["intent_source"] = source
        with pytest.raises(ContractError):
            SubmitOperationIntentV2.from_json(broken)

    command = SubmitOperationIntentV2.from_json(_wire())
    assert command.intent_source.origin_receipt_id == "receipt-1"


def test_completion_slot_and_prepared_acceptance_are_mandatory() -> None:
    missing = _wire()
    missing.pop("completion_slot")
    with pytest.raises(ContractError):
        SubmitOperationIntentV2.from_json(missing)

    empty = _wire()
    empty["prepared_acceptance_refs"] = []
    with pytest.raises(ContractError):
        SubmitOperationIntentV2.from_json(empty)


@pytest.mark.parametrize("schema", [True, 2.0, "2"])
def test_intent_schema_requires_exact_integer(schema):
    with pytest.raises(ContractError):
        SubmitOperationIntentV2.from_json(_wire() | {"schema_version": schema})


@pytest.mark.parametrize("mutation", [None, "tenant", "acceptance-hash", "dirty", "slot"])
def test_sources_resolve_real_preparation_and_reject_invalid_authority(tmp_path, mutation):
    """2026-10-03 迁到产品同形世界：内容那一步由产品主循环真跑、真验收（准备好的 MIXED 范围数据），
    跑到系统备好申请单、等人批准那一刻。来源解析认这份真实的准备验收；租户不对、验收哈希不对、
    验收已被标脏（计划提交在依据变化时写的同一个标记）、完成槽位不是批准的那个，一律拒绝且不写。"""

    import asyncio
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from agent_orchestrator.contracts.semantic_base import content_hash_of
    from agent_orchestrator.orchestrator.operation_intent_sources import (
        OperationIntentSourceError,
        prepare_operation_intent_sources,
    )
    from agent_orchestrator.storage.htn_store import HtnStore
    from publish_world import TARGET, publishing

    async def run() -> None:
        async with publishing(tmp_path) as case:
            await case.until_approval()
            store = case.store
            htn = HtnStore(store)
            [acceptance] = htn.list_acceptances(case.mission_id)
            [artifact] = [
                a for a in store.list_mission_artifacts(case.mission_id) if a.path == TARGET
            ]
            spec_hash, approval_receipt = store.connection.execute(
                "SELECT spec_hash, approval_receipt_id FROM operation_completion_specs "
                "WHERE mission_id=?",
                (case.mission_id,),
            ).fetchone()
            wire = _wire() | {
                "mission_id": case.mission_id,
                "intent_source": {
                    "kind": "AUTHORIZED_SLOT",
                    "origin_receipt_id": approval_receipt,
                    "slot_key": "publish-weekly",
                },
                "candidate_artifact_ref": TypedRef(
                    TypedRefKind.ARTIFACT,
                    artifact.id,
                    artifact.version,
                    artifact.content_hash,
                    Provenance.TOOL,
                ).to_json(),
                "prepared_acceptance_refs": [
                    TypedRef(
                        TypedRefKind.ACCEPTANCE,
                        str(acceptance.acceptance_id),
                        1,
                        content_hash_of(acceptance.to_json()),
                        Provenance.TOOL,
                    ).to_json()
                ],
                "completion_slot": {"spec_hash": spec_hash, "effect_key": "publish-weekly"},
            }
            command = SubmitOperationIntentV2.from_json(wire)
            tenant = case.world.deployment.tenant_id
            if mutation == "tenant":
                tenant = "other-tenant"
            elif mutation == "acceptance-hash":
                command = dataclasses.replace(
                    command,
                    prepared_acceptance_refs=(
                        dataclasses.replace(
                            command.prepared_acceptance_refs[0], content_hash=HASH_A
                        ),
                    ),
                )
            elif mutation == "dirty":
                htn.mark_dirty(
                    case.mission_id,
                    subject_kind="acceptance",
                    subject_id=str(acceptance.acceptance_id),
                    scope_id="mission",
                    epoch=1,
                    reason="actual support changed",
                )
            elif mutation == "slot":
                command = dataclasses.replace(
                    command,
                    completion_slot=dataclasses.replace(
                        command.completion_slot, effect_key="unapproved-effect"
                    ),
                )
            cas = case.world.loop.assembled.workspaces.artifact_store
            principal = case.world.deployment.principal
            before = store.connection.total_changes
            if mutation is not None:
                with pytest.raises(OperationIntentSourceError):
                    prepare_operation_intent_sources(
                        store, cas, command, tenant_id=tenant, principal=principal
                    )
            else:
                sources = prepare_operation_intent_sources(
                    store, cas, command, tenant_id=tenant, principal=principal
                )
                [leaf] = [t for t in store.list_tasks(case.mission_id) if t.accepted_result_id]
                assert sources.producer_result.envelope.id == leaf.accepted_result_id
                assert sources.producer_scope.task_ref.id == leaf.id
                # 申请单由系统按已批准效果生成，不是产物字节本身
                candidate = json.loads(sources.raw_candidate_bytes)
                assert candidate["connector"] == "file_publish"
                assert candidate["params"]["artifact_path"] == TARGET
            assert store.connection.total_changes == before

    asyncio.run(run())
