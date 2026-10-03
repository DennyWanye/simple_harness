"""确认页"完成映射"的写入、身份与回滚（OCC-02；2026-10-03 迁到产品同形世界，HTN 补齐阶段 A′）。

任务经产品那一份部署组装建出：根义务、要求书第 1 版（``c-user-<n>``）、执行图都在建任务事务里
由部署写好。本文件只做"人在确认页点确认"这一件事（真实的 ``OperationCompletionApi`` /
门面命令），断言回执、完成映射行、事件与读侧的精确身份。确认之前主循环不开工，所以这里不跑
主循环；要"已提交第一版计划"的那一条用产品主循环真跑到叶子派发。

要求书第 2 版在产品上没有写入方（只有部署建任务时写第 1 版），"要求修订后旧映射过期"那条
随删，等阶段 E 接上要求修订再写（分诊裁决①c）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.api.operation_completion import OperationCompletionApi
from agent_orchestrator.contracts.operation_completion import (
    OccurrenceCompletionScopeV1,
    OperationCompletionRequirementsV1,
)
from agent_orchestrator.contracts.resolution import RequirementsRevision
from agent_orchestrator.contracts.semantic_base import TypedRef, TypedRefKind
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.operation_completion import (
    OperationCompletionError,
    OperationCompletionReader,
)
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.operation_completion_store import OperationCompletionStore
from agent_orchestrator.storage.store import InjectedCrash, StoreConflict
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from publish_world import Publishing, publishing, workspace  # noqa: E402

HASH_D = "d" * 64
CONFIRMING = "product-world-user"  # 产品同形世界里登录的那个人


def _requirements(case: Publishing) -> RequirementsRevision:
    requirements = HtnStore(case.store).latest_requirements_revision(case.mission_id)
    assert requirements is not None and requirements.revision == 1
    return requirements


def _requirements_ref(requirements: RequirementsRevision) -> TypedRef:
    return TypedRef(kind=TypedRefKind.REQUIREMENTS, id=str(requirements.revision_id),
                    revision=int(requirements.revision), content_hash=requirements.content_hash())


def _command(case: Publishing, *, command_id: str = "confirm-completion-1",
             milestone: str = "CONTENT_HASH_VERIFIED", mode: str = "REQUIRED_EFFECTS",
             **fields: Any) -> dict[str, Any]:
    """确认页会发出的那条命令：内容要求照单确认，``action:`` 要求作为必须完成的效果挂在根义务上。"""

    page = workspace(case.world, case.mission_id)
    requirements = _requirements(case)
    actions = [c["id"] for c in page["criteria"] if c["statement"].startswith("action:")]
    content = [c["id"] for c in page["criteria"] if c["id"] not in actions]
    [obligation] = page["obligations"]
    policy = next(m for m in page["milestones"] if m["id"] == "CONTENT_HASH_VERIFIED")
    effects = [{
        "effect_key": "publish-weekly", "source_slot_key": "publish-weekly",
        "obligation_id": obligation["id"], "criterion_ids": actions, "required_milestone": milestone,
        "milestone_policy_ref": policy["milestone_policy_ref"], "evidence_policy_ref": policy["evidence_policy_ref"],
    }]
    if mode == "CONTENT_ONLY":
        content, effects = [c["id"] for c in page["criteria"]], []
    return {
        "mission_id": case.mission_id, "command_id": command_id,
        "expected_requirements_ref": _requirements_ref(requirements).to_json(),
        "proposal": {
            "schema_version": 1, "mission_id": case.mission_id,
            "requirements_ref": {"id": str(requirements.revision_id), "revision": int(requirements.revision),
                                 "content_hash": requirements.content_hash()},
            "mode": mode, "content_criterion_ids": content, "effects": effects,
        },
        **fields,
    }


def _api(case: Publishing, principal_id: str = CONFIRMING) -> OperationCompletionApi:
    return OperationCompletionApi(case.world.loop.commit, tenant_id=case.world.deployment.tenant_id,
                                  principal=Principal(principal_id))


def _counts(case: Publishing) -> tuple[int, int, int]:
    connection = case.store.connection
    return (int(connection.execute("SELECT count(*) FROM commit_receipts").fetchone()[0]),
            int(connection.execute("SELECT count(*) FROM operation_completion_specs").fetchone()[0]),
            len(case.store.list_events(case.mission_id)))


def _confirming(tmp_path: Path, case_body, **options: Any) -> None:
    async def run() -> None:
        async with publishing(tmp_path, confirm=False, **options) as case:
            await case_body(case)

    asyncio.run(run())


def test_occ02_user_confirmation_persists_exact_spec_and_source_identity(tmp_path) -> None:
    """确认命令本身是唯一的 USER_CONFIRMED 来源；回执、完成映射与事件都对得上要求书第 1 版。"""

    async def body(case: Publishing) -> None:
        requirements = _requirements(case)
        command = _command(case)
        receipt = _api(case).approve(command)
        assert receipt.mission_id == case.mission_id
        assert receipt.requirements_ref == _requirements_ref(requirements)
        assert receipt.authority.kind == "USER_CONFIRMED"
        assert receipt.authority.issuer_id == CONFIRMING
        assert receipt.authority.requirements_ref == _requirements_ref(requirements)
        stored = OperationCompletionReader(case.store).read_requirements(
            case.mission_id, _requirements_ref(requirements))
        assert stored.to_json() == OperationCompletionRequirementsV1.from_json(command["proposal"]).to_json()
        assert case.store.get_receipt(command["command_id"]) == receipt.to_json()
        assert case.events()[-1].type == "OperationCompletionSpecApproved"

    _confirming(tmp_path, body)


@pytest.mark.parametrize(
    "mutation,expected_code",
    (
        (lambda command: {**command, "expected_requirements_ref": {
            **command["expected_requirements_ref"], "kind": "task"}}, "OP_REF_KIND_UNSUPPORTED"),
        (lambda command: {**command, "expected_requirements_ref": {
            **command["expected_requirements_ref"], "content_hash": HASH_D}}, "OP_PAYLOAD_HASH_MISMATCH"),
        (lambda command: {**command, "proposal": {**command["proposal"], "mission_id": "other-mission"}},
         "OP_EFFECT_SCOPE_STALE"),
        (lambda command: {**command, "proposal": {**command["proposal"], "effects": [
            {**command["proposal"]["effects"][0], "obligation_id": "unregistered-obligation"}]}},
         "OP_COMPLETION_SCOPE_UNRESOLVED"),
    ),
)
def test_occ02_approval_rejects_wrong_reference_or_unapproved_scope(tmp_path, mutation, expected_code) -> None:
    """确认页递来的身份或映射不对（这是入口真会收到的输入）：按名拒绝，什么都不写。"""

    async def body(case: Publishing) -> None:
        before = _counts(case)
        with pytest.raises(OperationCompletionError) as caught:
            _api(case).approve(mutation(_command(case)))
        assert caught.value.code == expected_code
        assert _counts(case) == before

    _confirming(tmp_path, body)


def test_occ02_same_command_is_a_receipt_replay_but_changed_spec_or_issuer_conflicts(tmp_path) -> None:
    async def body(case: Publishing) -> None:
        command = _command(case)
        first = _api(case).approve(command)
        before = _counts(case)
        assert _api(case).approve(command).to_json() == first.to_json()
        assert _counts(case) == before
        with pytest.raises(OperationCompletionError, match="command identity or caller differs"):
            _api(case).approve(_command(case, milestone="FILE_PUBLISHED"))
        with pytest.raises(OperationCompletionError, match="command identity or caller differs"):
            _api(case, principal_id="another-authenticated-human").approve(command)
        assert _counts(case) == before

    _confirming(tmp_path, body)


@pytest.mark.parametrize("fault", ("completion_spec_after_receipt", "completion_spec_after_spec",
                                   "completion_spec_after_event"))
def test_occ02_approval_faults_roll_back_receipt_spec_and_event_together(tmp_path, fault: str) -> None:
    """确认写到一半进程崩了（产品自带的崩溃点）：回执、映射行、事件一起回滚，读侧仍说"没确认"。"""

    async def body(case: Publishing) -> None:
        requirements = _requirements(case)
        command = _command(case)
        before = _counts(case)
        case.store.arm(f"{fault}:operation_completion")
        with pytest.raises(InjectedCrash, match=fault):
            _api(case).approve(command)
        assert _counts(case) == before
        assert case.store.get_receipt(command["command_id"]) is None
        with pytest.raises(OperationCompletionError) as missing:
            OperationCompletionReader(case.store).read_requirements(case.mission_id, _requirements_ref(requirements))
        assert missing.value.code == "OP_REQUIREMENT_MAPPING_MISSING"
        # 崩溃之后同一条命令照常能确认（没有留下半截记录挡路）。
        assert _api(case).approve(command).authority.kind == "USER_CONFIRMED"

    _confirming(tmp_path, body)


def test_occ02_cross_tenant_approval_cannot_read_or_create_a_spec(tmp_path) -> None:
    async def body(case: Publishing) -> None:
        before = _counts(case)
        foreign = OperationCompletionApi(case.world.loop.commit, tenant_id="different-tenant",
                                         principal=Principal(CONFIRMING))
        with pytest.raises(OperationCompletionError) as caught:
            foreign.approve(_command(case))
        assert caught.value.code == "not_found"
        assert _counts(case) == before

    _confirming(tmp_path, body)


@pytest.mark.parametrize("kind", ("model", "provider"))
def test_user_confirmation_boundary_rejects_nonhuman_principal(tmp_path, kind: str) -> None:
    from agent_orchestrator.api.operation_completion import bind_requirement_authority

    async def body(case: Publishing) -> None:
        requirements = _requirements(case)
        ref = _requirements_ref(requirements)
        proposal = OperationCompletionRequirementsV1.from_json(_command(case)["proposal"])
        principal = Principal(CONFIRMING)
        authority = bind_requirement_authority(
            principal=principal, tenant_id=case.world.deployment.tenant_id, command_id="confirm-malformed-caller",
            mission_id=case.mission_id, requirements_ref=ref, normalized_spec=proposal)
        # 正常构造的 Principal 已经拒绝非人类型；这里再用一个非法的内部实例敲提交层的信任边界。
        malformed = object.__new__(Principal)
        object.__setattr__(malformed, "principal_id", principal.principal_id)
        object.__setattr__(malformed, "display", principal.display)
        object.__setattr__(malformed, "kind", kind)
        before = _counts(case)
        with pytest.raises(OperationCompletionError) as api_refusal:
            OperationCompletionApi(case.world.loop.commit, tenant_id=case.world.deployment.tenant_id,
                                   principal=malformed)
        assert api_refusal.value.code == "OP_REQUIREMENT_MAPPING_UNAPPROVED"
        with pytest.raises(OperationCompletionError) as commit_refusal:
            case.world.loop.commit.approve_operation_completion_spec(
                mission_id=case.mission_id, command_id="confirm-malformed-caller", expected_requirements_ref=ref,
                proposal=proposal, requirement_authority=authority, principal=malformed)
        assert commit_refusal.value.code == "OP_REQUIREMENT_MAPPING_UNAPPROVED"
        assert _counts(case) == before

    _confirming(tmp_path, body)


def test_occ02_different_command_cannot_replace_spec_for_same_requirements(tmp_path) -> None:
    """同一版要求书只有一份完成映射：换一条命令改里程碑被拒，原回执与映射逐字节不变。"""

    async def body(case: Publishing) -> None:
        requirements = _requirements(case)
        first_command = _command(case)
        first = _api(case).approve(first_command)
        before = _counts(case)
        with pytest.raises((OperationCompletionError, StoreConflict)):
            _api(case).approve(_command(case, command_id="confirm-completion-2", milestone="FILE_PUBLISHED"))
        assert _counts(case) == before
        assert case.store.get_receipt(first_command["command_id"]) == first.to_json()
        assert OperationCompletionReader(case.store).read_requirements(
            case.mission_id, _requirements_ref(requirements)).content_hash() == first.spec_hash

    _confirming(tmp_path, body)


def test_occ02_scope_store_revalidates_exact_spec_plan_task_and_unique_identity(tmp_path) -> None:
    """第一版计划由产品主循环真提交（提做法 → 独立审阅 → 采用），根的完成范围随之冻结。

    范围表的写入口对产品写下的那一行做精确复核：要求书钉错、任务合同哈希填成整份语义绑定的哈希、
    调用方自选范围编号，都按冲突拒绝；同一行原样重放不写任何东西。"""

    provider = LayeredScriptedProvider()
    provider.held.add("worker")

    async def run() -> None:
        try:
            async with publishing(tmp_path, provider=provider) as case:
                await case.run_until(provider.entered.is_set)
                completion = OperationCompletionStore(case.store)
                # 产品上根是复合目标（AGGREGATE，承担发布效果），叶子只管内容（CONTENT）。
                rows = case.store.connection.execute(
                    "SELECT plan_revision, occurrence_id, plan_receipt_id FROM operation_completion_scopes "
                    "WHERE mission_id=? AND json_extract(document_json,'$.role')='AGGREGATE'",
                    (case.mission_id,)).fetchall()
                assert len(rows) == 1
                [row] = rows
                stored = completion.get_scope_exact(case.mission_id, row["plan_revision"], row["occurrence_id"])
                assert stored is not None
                document = stored["document"]
                assert isinstance(document, OccurrenceCompletionScopeV1)
                receipt_id = row["plan_receipt_id"]

                stale = document.to_json()
                stale["requirements_ref"] = {"id": "requirements-after-amendment", "revision": 2, "content_hash": HASH_D}
                stale_scope = OccurrenceCompletionScopeV1.from_json(stale)
                with case.store.transaction(), pytest.raises(StoreConflict):
                    completion.insert_scope(stale_scope.scope_id, stale_scope, plan_receipt_id=receipt_id)

                binding = HtnStore(case.store).task_semantics_of(case.mission_id, document.task_ref.id)
                assert binding is not None and binding.content_hash() != binding.contract_hash
                wrong = document.to_json()
                wrong["task_ref"]["content_hash"] = binding.content_hash()
                wrong_scope = OccurrenceCompletionScopeV1.from_json(wrong)
                with case.store.transaction(), pytest.raises(StoreConflict):
                    completion.insert_scope(wrong_scope.scope_id, wrong_scope, plan_receipt_id=receipt_id)

                before = case.store.connection.total_changes
                with case.store.transaction():
                    replay = completion.insert_scope(document.scope_id, document, plan_receipt_id=receipt_id)
                assert replay == stored
                assert case.store.connection.total_changes == before
                with case.store.transaction(), pytest.raises(StoreConflict):
                    completion.insert_scope("caller-selected-scope-id", document, plan_receipt_id=receipt_id)
        finally:
            provider.release.set()

    asyncio.run(run())


def test_occ02_real_migration_installs_completion_tables_and_immutability_triggers(tmp_path) -> None:
    async def body(case: Publishing) -> None:
        receipt = _api(case).approve(_command(case))
        names = {row[0] for row in case.store.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"operation_completion_specs", "operation_completion_scopes", "operation_outcome_review_bindings",
                "operation_acceptance_scopes"} <= names
        assert case.store.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        sql = "SELECT document_json FROM operation_completion_specs WHERE spec_id=?"
        original = case.store.connection.execute(sql, (receipt.spec_id,)).fetchone()[0]
        with pytest.raises(Exception, match="immutable completion spec"):
            case.store.connection.execute(
                "UPDATE operation_completion_specs SET document_json='{}' WHERE spec_id=?", (receipt.spec_id,))
        assert case.store.connection.execute(sql, (receipt.spec_id,)).fetchone()[0] == original

    _confirming(tmp_path, body)


# --------------------------------------------------------------------------------------
# 用户 2026-09-26 决定：自动模式下，纯内容的要求由部署代为确认；回执一样，事件写明是系统做的。
# --------------------------------------------------------------------------------------


def test_host_auto_confirmation_is_recorded_as_the_system(tmp_path) -> None:
    """产品自动模式：没有 action: 要求的任务，部署职责照确认页的样子代签，记为系统代办。"""

    async def run() -> None:
        async with publishing(tmp_path, confirm=False, criteria=("file:" + "NOTES.md",), key="auto-1") as case:
            assert case.world.deployment.duties.auto_confirm_content_completion(auto=True) == 1
            [event] = case.events("OperationCompletionSpecApproved")
            assert event.actor_type == "system" and event.actor_id == "host:auto-permission-completion"
            assert event.payload["approval_source"] == "HOST_AUTO_PERMISSION"
            assert event.payload["on_behalf_of_principal_id"] == CONFIRMING

    asyncio.run(run())


def test_a_person_confirming_is_still_recorded_as_the_person(tmp_path) -> None:
    async def run() -> None:
        async with publishing(tmp_path, confirm=False, criteria=("file:" + "NOTES.md",), key="person-1") as case:
            _api(case).approve(_command(case, mode="CONTENT_ONLY"))
            [event] = case.events("OperationCompletionSpecApproved")
            assert event.actor_type == "human" and event.actor_id == CONFIRMING
            assert "approval_source" not in event.payload

    asyncio.run(run())


def test_the_host_never_confirms_an_operation_effect(tmp_path) -> None:
    async def body(case: Publishing) -> None:
        # 部署职责本身就跳过带 action: 的任务。
        assert case.world.deployment.duties.auto_confirm_content_completion(auto=True) == 0
        with pytest.raises(OperationCompletionError) as refused:
            _api(case).approve(_command(case, approval_source="HOST_AUTO_PERMISSION"))
        assert refused.value.code == "OP_REQUIREMENT_MAPPING_UNAPPROVED"
        with pytest.raises(OperationCompletionError) as unknown:
            _api(case).approve(_command(case, mode="CONTENT_ONLY", approval_source="MODEL"))
        assert unknown.value.code == "invalid_request"
        assert not case.events("OperationCompletionSpecApproved")

    _confirming(tmp_path, body)
