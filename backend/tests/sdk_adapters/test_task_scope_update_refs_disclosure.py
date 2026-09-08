# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""HM-TO-A6 事件 U：``task_scope_update_refs_outside_scope`` 必须可执行。

复现第 9 次尝试 T17 的现场：Run 内新建的 TaskScope 在整个 Run 里 ``task_scope_evidence_links``
为空（证据链接只在 material 事件或 Run 终态时才写），而 ``evidence_refs`` 是 minItems=1，
于是**任何**载荷都必然被 ``task_scope_update_refs_outside_scope`` 拒绝；拒绝回执只回显违规
ref，模型只能连猜 7 次直到烧完整轮。

本文件断言修复后的口径：
1. 拒绝回执披露有界的可引用 evidence id（``allowed_evidence_refs``，仅 id）+ ``next_step``；
2. 本轮 USER 证据（``foreground_turns.evidence_id``）可引用，且以 ``current_turn_evidence_ref``
   直接点名——A6-5 的 18 KiB 逐字 goal.set 因此能在 Run 内落库（revision 1→2，原文逐字）；
3. 仍然 fail-closed：范围外 ref 照旧拒绝，拒绝点照旧写 ``host_pre_admission_audit``；
4. 同码重复拒绝达上限后有界升级提示（不放宽守卫，只告诉模型停手），升级次数来自审计行本身。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from simple_harness import CallId, EffectId, RequestId, RunId
from simple_harness.tools import ToolContext
from simple_harness.tools.contracts import CancellationToken

from deskpet.sdk_adapters import task_scope_mutation as tsm
from deskpet.sdk_adapters.task_scope_mutation import (
    ClosureRejected,
    TaskScopeUpdateService,
)
from tests.sdk_adapters import s5b_closure_harness as ch

# T17 的 18 KiB 逐字目标（A6-5 唯一杠杆：> README 16384 且 > STATUS 12288，< value 上限 32768）。
GOAL_TEXT = "".join(f"条款 {index:04d}：把秋分参考资料逐字保留，不概括、不省略。\n" for index in range(320))

# 第 9 次尝试里模型真实猜过的形状：记忆 id、内容哈希、call id、run id、前缀形式。
GUESSED_REFS = [
    "6b9fffd5-9a5a-5d7f-82b9-c21727cc5de6",
    "9cd8f80a8ee7bc8b9b6f386c9fc2a7a53ce0a19054431a2f44e2ce30f8341b71",
    "call_00_lDxgGXbjGnrqjldyfIVC6557",
    "foreground-execution-49080fcab9938b0775ba22be882fe99acc98bcc021068c800fc9eafdd98c1a94",
    "evidence:20f23807-8bf5-5e20-8c62-8935818b7d58",
]

RUN = "sdk-run-refs-u"


def _goal_arguments(refs: list[str], *, key: str, base_revision: int = 1) -> dict:
    return {
        "outcome": "mutate",
        "base_revision": base_revision,
        "evidence_refs": list(refs),
        "idempotency_key": key,
        "operations": [
            {
                "operation_id": "op-1-goal-verbatim",
                "kind": "goal.set",
                "value": GOAL_TEXT,
                "reason_code": "user_requests_verbatim_goal",
                "evidence_refs": list(refs),
            }
        ],
    }


async def _incident_env(tmp_path: Path):  # type: ignore[no-untyped-def]
    """第 9 次尝试 T17 的现场：真实前台 Run + 全新 scope，证据链接为空。"""

    env = await ch.bound_run(tmp_path, RUN)
    assert ch.scope_evidence_ids(env.db_path) == [], "现场前提：scope 上还没有任何已链接证据"
    service = TaskScopeUpdateService(env.db_path, tool_context_getter=lambda: None, route_ledger=None)
    return env, service


async def _apply(service: TaskScopeUpdateService, env, arguments: dict):  # type: ignore[no-untyped-def]
    return await service.apply_closure(
        arguments,
        run_id=env.run_id,
        host_run_id=env.admission.host_run_id,
        task_scope_id=ch.SCOPE,
        subject=ch.SUBJECT,
        source_turn_id=f"sdk-run:{env.run_id}:turn:17",
        reason_code=tsm.MODEL_CLOSURE_REASON_CODE,
    )


@pytest.mark.asyncio
async def test_refs_outside_scope_discloses_admissible_ids_and_next_step(tmp_path: Path) -> None:
    """事件 U 主属性：拒绝必须告诉模型「哪些 id 能引用」和「下一步怎么发」。"""

    env, service = await _incident_env(tmp_path)
    with pytest.raises(ClosureRejected) as rejected:
        await _apply(service, env, _goal_arguments(GUESSED_REFS, key="goal-set-verbatim-1"))
    detail = rejected.value.detail
    assert rejected.value.code == "task_scope_update_refs_outside_scope"
    assert rejected.value.retryable is False
    # 旧口径保留：仍回显违规 ref，且有界。
    assert set(detail["refs"]) <= set(GUESSED_REFS) and len(detail["refs"]) <= 8
    # 新口径：可引用 id + 可执行下一步。
    [(turn_evidence_id,)] = ch.rows(
        env.db_path, "SELECT evidence_id FROM foreground_turns WHERE subject=? ORDER BY enqueue_sequence DESC LIMIT 1", ch.SUBJECT
    )
    assert detail["allowed_evidence_refs"] == [turn_evidence_id]
    assert detail["current_turn_evidence_ref"] == turn_evidence_id
    assert detail["allowed_evidence_refs_total"] == 1
    assert "next_step" in detail and detail["next_step"].strip()
    # 只有 id：证据的 content_hash / 信封摘要一律不过境（"content_hash" 这个词只出现在
    # 「你永远不用发 content_hash」这句指引里，值本身不出现）。
    rendered = json.dumps(detail, ensure_ascii=False)
    [(turn_hash,)] = ch.rows(
        env.db_path, "SELECT evidence_hash FROM foreground_turns WHERE evidence_id=?", turn_evidence_id
    )
    assert turn_hash not in rendered
    for (envelope_hash,) in ch.rows(env.db_path, "SELECT envelope_sha256 FROM human_memory_evidence"):
        assert envelope_hash not in rendered


@pytest.mark.asyncio
async def test_disclosed_ref_lets_mid_run_verbatim_goal_set_apply(tmp_path: Path) -> None:
    """A6-5：拿到披露的 id 后，Run 内的 18 KiB 逐字 goal.set 直接落库（revision 1→2）。"""

    env, service = await _incident_env(tmp_path)
    with pytest.raises(ClosureRejected) as rejected:
        await _apply(service, env, _goal_arguments(GUESSED_REFS, key="goal-set-verbatim-1"))
    disclosed = list(rejected.value.detail["allowed_evidence_refs"])
    assert len(GOAL_TEXT.encode("utf-8")) > 16384
    # 关键：这条 id 此刻**还不是** scope 已链接证据（链接只在 material 事件/Run 终态时才写）。
    # 主干上因此没有任何可用载荷；本轮 USER 证据被纳入可引用集合，才让 goal.set 有解。
    assert set(disclosed) & set(ch.scope_evidence_ids(env.db_path)) == set()

    revision_before, _ = ch.head(env.db_path)
    result = await _apply(service, env, _goal_arguments(disclosed, key="goal-set-verbatim-2"))
    assert result.accepted and result.error_code is None
    assert result.committed_revision == revision_before + 1 == 2
    [(state_json,)] = ch.rows(
        env.db_path,
        "SELECT state_json FROM task_scope_canonical_revisions WHERE task_scope_id=? AND revision=?",
        ch.SCOPE,
        result.committed_revision,
    )
    assert json.loads(state_json)["goal"] == GOAL_TEXT, "目标必须逐字，不得概括/截断"
    # 落库后该证据确实成为 scope 已链接证据（守卫的后置条件被恢复）。
    assert set(disclosed) <= set(ch.scope_evidence_ids(env.db_path))


@pytest.mark.asyncio
async def test_gate_stays_fail_closed_when_only_some_refs_are_admissible(tmp_path: Path) -> None:
    """披露不等于放宽：夹带一个范围外 ref，整条载荷照旧拒绝。"""

    env, service = await _incident_env(tmp_path)
    with pytest.raises(ClosureRejected) as first:
        await _apply(service, env, _goal_arguments(GUESSED_REFS, key="mixed-1"))
    disclosed = list(first.value.detail["allowed_evidence_refs"])
    with pytest.raises(ClosureRejected) as mixed:
        await _apply(service, env, _goal_arguments([*disclosed, "evidence:not-in-scope"], key="mixed-2"))
    assert mixed.value.code == "task_scope_update_refs_outside_scope"
    assert mixed.value.detail["refs"] == ["evidence:not-in-scope"]
    assert ch.head(env.db_path)[0] == 1, "被拒绝的载荷不得推进 revision"


@pytest.mark.asyncio
async def test_empty_admissible_set_names_the_terminus(tmp_path: Path) -> None:
    """可引用集合为空时，拒绝必须明说本 Run 无解、不要再猜（模型循环有终点）。"""

    env, service = await _incident_env(tmp_path)
    with pytest.raises(ClosureRejected) as rejected:
        # host_run_id 不匹配 → 本轮 USER 证据不可引用，scope 也还没有链接证据。
        await service.apply_closure(
            _goal_arguments(GUESSED_REFS, key="empty-1"),
            run_id=env.run_id,
            host_run_id=f"unbound:{env.run_id}",
            task_scope_id=ch.SCOPE,
            subject=ch.SUBJECT,
            source_turn_id=f"sdk-run:{env.run_id}:turn:17",
            reason_code=tsm.MODEL_CLOSURE_REASON_CODE,
        )
    detail = rejected.value.detail
    assert detail["allowed_evidence_refs"] == []
    assert "current_turn_evidence_ref" not in detail
    assert detail["next_step"] == tsm._REFS_OUTSIDE_SCOPE_EMPTY


@pytest.mark.asyncio
async def test_tool_path_audits_every_rejection_and_escalates_after_bound(tmp_path: Path) -> None:
    """Tool 路径：每次拒绝写审计行；同码第 N+1 次起附带有界升级提示。"""

    env = await ch.bound_run(tmp_path, RUN)
    context = ToolContext(
        RunId(env.run_id),
        RequestId("request-refs-u"),
        CancellationToken(),
        call_id=CallId("call:refs-u"),
        effect_id=EffectId("effect:refs-u"),
    )
    ledger = SimpleNamespace(
        latest_route_decision_for_run=lambda run_id: _resolved({"task_scope_id": ch.SCOPE})
    )
    service = TaskScopeUpdateService(
        env.db_path, tool_context_getter=lambda: context, route_ledger=ledger
    )

    messages = []
    for attempt in range(1, tsm._REFS_ESCALATION_AFTER + 2):
        result = await service.handle_task_scope_update(_goal_arguments(GUESSED_REFS, key=f"tool-{attempt}"))
        assert result.error_code == "task_scope_update_refs_outside_scope"
        messages.append(result.public_message)

    audited = ch.rows(
        env.db_path,
        "SELECT COUNT(*) FROM host_pre_admission_audit WHERE sdk_run_id=? AND payload_kind='task_scope_update' AND reason_code=?",
        env.run_id,
        "task_scope_update_refs_outside_scope",
    )
    assert audited[0][0] == tsm._REFS_ESCALATION_AFTER + 1
    for message in messages[: tsm._REFS_ESCALATION_AFTER]:
        assert "escalation" not in message
        assert "allowed_evidence_refs" in message and "next_step" in message
    assert "escalation" in messages[-1]
    assert tsm._REFS_OUTSIDE_SCOPE_ESCALATION.format(count=tsm._REFS_ESCALATION_AFTER + 1) in messages[-1]


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.parametrize("drop", ["run_id", "host_run_id"])
async def test_turn_evidence_needs_both_run_bindings(tmp_path: Path, drop: str) -> None:
    """加宽来源的两条绑定各自必需：任一条对不上，本轮 USER 证据既不披露也不可引用。

    去掉任一条谓词都会让本用例转绿失败——这正是「别的 Run 不能引用本轮尚未链接的
    USER 证据」这条权威属性的守卫。
    """

    env, service = await _incident_env(tmp_path)
    with pytest.raises(ClosureRejected) as mine:
        await _apply(service, env, _goal_arguments(GUESSED_REFS, key=f"own-{drop}"))
    [my_ref] = mine.value.detail["allowed_evidence_refs"]

    foreign = {
        "run_id": env.run_id,
        "host_run_id": env.admission.host_run_id,
        **{drop: f"other-{drop}"},
    }
    with pytest.raises(ClosureRejected) as other:
        # 换一个 Run 身份来问：可引用集合必须塌成空集，且直接引用那条 id 照旧被拒。
        await service.apply_closure(
            _goal_arguments([my_ref], key=f"foreign-{drop}"),
            task_scope_id=ch.SCOPE,
            subject=ch.SUBJECT,
            source_turn_id=f"sdk-run:{foreign['run_id']}:turn:17",
            reason_code=tsm.MODEL_CLOSURE_REASON_CODE,
            **foreign,
        )
    assert other.value.code == "task_scope_update_refs_outside_scope"
    assert other.value.detail["refs"] == [my_ref]
    assert other.value.detail["allowed_evidence_refs"] == []
    assert "current_turn_evidence_ref" not in other.value.detail


async def _resolved(value):  # type: ignore[no-untyped-def]
    return value
