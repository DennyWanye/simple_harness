# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""2026-09-08 HM-TO-A6 事件 C：脏闸是 effect-closure 闸，不是档案写入授权。

实跑证据（`.local-test-evidence/2026-09-08/native-a6-b3682fe1/primary-ui-xmqudtzt/
userdata/data/simple-harness-sdk/execution-v6.sqlite3`，Run
`product-sdk-26c66feb…`，HM-TO-A6 第 10 轮）：用户在活动 TaskScope「秋分资料整理」
里说「记一个决定：以主清单 A 为准，参照件 B 只作参照。」，模型连发 7 次格式完全正确的
`task_scope_update(decision.record)`（`outcome`/`base_revision`/`evidence_refs`=该用户
消息的 evidence id/`idempotency_key`/`operations` 齐备），**每一次**都被
`task_scope_update_nothing_to_close` 拒绝——那一轮没有任何 material 事件
（design-freeze §2 把 `host.turn` 定为 trivial），Run 随后耗尽 max turns 而失败。
第 7 轮的 `goal.set` 与第 17 轮的 18 KiB 逐字 `goal.set`（A6-5 依赖它把 README/STATUS
顶过上限）同病。

裁决与修法：`require_dirty` 只对 `outcome=no_mutation` 生效。`mutate` 计划本身就是
material 变更——它在同一事务里追加 decision + revision 并受 `base_revision` CAS 约束，
干净档案上照样受理；其余守卫（scope_unbound / payload_invalid / refs_outside_scope /
迁移表 / CAS / 幂等）一条不改。

本文件直接驱动 `TaskScopeUpdateService.apply_closure`（Tool 路径与兜底路径共用的核心，
`require_dirty=True` 即 Tool 路径口径），基座是 `s5b_closure_harness`：真实 state.db、
真实 ForegroundQueueStore/ExecutionEvidenceIngress/CanonicalTaskScopeStore。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskpet.execution.semantic_closure import dirty_state, pending_receipts_tx
from deskpet.sdk_adapters.task_scope_mutation import (
    ClosureRejected,
    TaskScopeUpdateService,
    derive_plan_id,
)
from deskpet.task_scope.store import CanonicalTaskScopeStore
from tests.sdk_adapters import s5b_closure_harness as h


def _service(env) -> TaskScopeUpdateService:  # type: ignore[no-untyped-def]
    return TaskScopeUpdateService(
        env.db_path, tool_context_getter=lambda: None, route_ledger=None, clock=env.clock
    )


async def _apply(service: TaskScopeUpdateService, env, arguments: dict):  # type: ignore[no-untyped-def]
    return await service.apply_closure(
        arguments,
        run_id=env.run_id,
        host_run_id=env.admission.host_run_id,
        task_scope_id=h.SCOPE,
        subject=h.SUBJECT,
        source_turn_id=f"sdk-run:{env.run_id}:turn:1",
        reason_code="model_closure",
    )


def _operation(refs: list[str], *, key: str, kind: str, value: str) -> dict:
    return {
        "operation_id": f"op-{key}",
        "kind": kind,
        "value": value,
        "reason_code": "explicit_user_statement",
        "evidence_refs": list(refs),
    }


def _mutate(refs: list[str], *, base_revision: int, key: str, kind: str, value: str) -> dict:
    return {
        "outcome": "mutate",
        "base_revision": base_revision,
        "evidence_refs": list(refs),
        "idempotency_key": key,
        "operations": [_operation(refs, key=key, kind=kind, value=value)],
    }


def _state(db_path: Path) -> dict:
    [(state_json,)] = h.rows(
        db_path,
        "SELECT r.state_json FROM task_scope_heads t JOIN task_scope_canonical_revisions r "
        "ON r.task_scope_id=t.task_scope_id AND r.revision=t.current_revision WHERE t.task_scope_id=?",
        h.SCOPE,
    )
    return json.loads(str(state_json))


async def _clean_scope(tmp_path: Path, run_id: str):  # type: ignore[no-untyped-def]
    """一个已收口、因此完全干净、但已有 evidence 链接的 TaskScope（= 第 10 轮的前置状态）。"""

    env = await h.bound_run(tmp_path, run_id)
    service = _service(env)
    await h.material_write(env, "effect-1")
    refs = h.scope_evidence_ids(env.db_path)
    assert refs, "harness 必须已给该 scope 链上证据"
    revision, _ = h.head(env.db_path)
    closed = await _apply(service, env, h.mutate_arguments(refs, base_revision=revision))
    assert closed.accepted, closed.error_code

    store = CanonicalTaskScopeStore(env.db_path)
    assert not (await dirty_state(store, h.SCOPE)).is_dirty
    async with store._connection() as db:
        assert await pending_receipts_tx(db, h.SCOPE) == ()
    return env, service, store, refs


@pytest.mark.asyncio
async def test_user_stated_decision_and_goal_are_admitted_on_a_clean_scope(tmp_path: Path) -> None:
    env, service, store, refs = await _clean_scope(tmp_path, "sdk-run-clean-decision")
    revision, _ = h.head(env.db_path)

    # 第 10 轮：纯对话的决定，零 material 事件、只引用会话证据 → 必须落 canonical。
    decision = await _apply(
        service,
        env,
        _mutate(refs, base_revision=revision, key="decision-1", kind="decision.record",
                value="以主清单 A 为准，参照件 B 只作参照"),
    )
    assert decision.accepted, decision.error_code
    assert decision.committed_revision == revision + 1
    assert decision.receipt.outcome == "mutate"
    assert decision.receipt.plan_id == derive_plan_id("decision-1", h.SCOPE)
    assert decision.replayed is False
    state = _state(env.db_path)
    assert state["operations"][-1]["kind"] == "decision.record"
    assert state["operations"][-1]["value"] == "以主清单 A 为准，参照件 B 只作参照"

    # 第 7 / 17 轮：goal.set（含超长逐字目标）同样在干净档案上受理。
    verbatim = "秋分资料整理目标：" + "逐字保留不得概括。" * 1200
    assert len(verbatim.encode("utf-8")) > 16_384  # 越过 README 视图上限，A6-5 需要它
    goal = await _apply(
        service,
        env,
        _mutate(refs, base_revision=revision + 1, key="goal-1", kind="goal.set", value=verbatim),
    )
    assert goal.accepted, goal.error_code
    assert _state(env.db_path)["goal"] == verbatim
    assert h.head(env.db_path)[0] == revision + 2
    assert not (await dirty_state(store, h.SCOPE)).is_dirty


@pytest.mark.asyncio
async def test_no_mutation_on_a_clean_scope_is_rejected_and_says_what_would_count(tmp_path: Path) -> None:
    env, service, store, refs = await _clean_scope(tmp_path, "sdk-run-clean-nomutation")
    revision, _ = h.head(env.db_path)
    before = h.receipts(env.db_path)

    with pytest.raises(ClosureRejected) as rejected:
        await _apply(service, env, h.no_mutation_arguments(refs, base_revision=revision))

    assert rejected.value.code == "task_scope_update_nothing_to_close"
    assert rejected.value.retryable is False
    guidance = str(rejected.value.detail["accepts"])
    # 拒绝理由必须说清：缺的是什么，以及什么载荷才算数。
    assert "outcome=no_mutation" in guidance and "outcome=mutate" in guidance
    for kind in ("goal.set", "decision.record", "plan.step.", "resume.update"):
        assert kind in guidance
    # 不递增 revision、不写 receipt（模型的 base_revision 不会漂移）。
    assert h.head(env.db_path)[0] == revision
    assert h.receipts(env.db_path) == before


@pytest.mark.asyncio
async def test_no_mutation_still_accepted_while_dirt_or_pending_exists(tmp_path: Path) -> None:
    """反向守卫：脏闸本身没被拆掉——有 material 事件时 no_mutation 仍然受理。"""

    env, service, store, refs = await _clean_scope(tmp_path, "sdk-run-dirty-nomutation")
    await h.material_write(env, "effect-2", path="b.txt")
    assert (await dirty_state(store, h.SCOPE)).is_dirty
    revision, _ = h.head(env.db_path)

    closed = await _apply(
        service, env, h.no_mutation_arguments(h.scope_evidence_ids(env.db_path), base_revision=revision)
    )
    assert closed.accepted, closed.error_code
    assert closed.receipt.outcome == "no_mutation"
    assert not (await dirty_state(store, h.SCOPE)).is_dirty


@pytest.mark.asyncio
async def test_clean_scope_keeps_every_other_guard(tmp_path: Path) -> None:
    """放开的只有脏闸这一条：其余拒绝码在干净档案上逐条原样生效。"""

    env, service, store, refs = await _clean_scope(tmp_path, "sdk-run-clean-guards")
    revision, _ = h.head(env.db_path)

    # payload_invalid：未知 operation kind。
    with pytest.raises(ClosureRejected) as invalid:
        await _apply(service, env, _mutate(refs, base_revision=revision, key="bad",
                                           kind="status.update", value="x"))
    assert invalid.value.code == "task_scope_update_payload_invalid"

    # refs_outside_scope：引用未链接到本 scope 的证据。
    with pytest.raises(ClosureRejected) as outside:
        await _apply(service, env, _mutate(["not-in-scope"], base_revision=revision, key="outside",
                                           kind="decision.record", value="x"))
    assert outside.value.code == "task_scope_update_refs_outside_scope"

    # base_revision 漂移：CAS 冲突原样透传且可重试。
    with pytest.raises(ClosureRejected) as stale:
        await _apply(service, env, _mutate(refs, base_revision=revision + 7, key="stale",
                                           kind="decision.record", value="x"))
    assert stale.value.code == "mutation_base_revision_conflict" and stale.value.retryable
    assert h.head(env.db_path)[0] == revision

    # 幂等：同 key 同载荷重放 → 同一 receipt，不写第二行。
    arguments = _mutate(refs, base_revision=revision, key="once", kind="decision.record", value="只记一次")
    once = await _apply(service, env, arguments)
    assert once.accepted and once.replayed is False
    again = await _apply(service, env, arguments)
    assert again.accepted and again.replayed is True
    assert again.receipt.receipt_id == once.receipt.receipt_id
    assert len([row for row in h.receipts(env.db_path) if row[3] == once.receipt.plan_id]) == 1

    # 迁移表：complete 之后禁 plan.*（干净档案同样适用）。
    done = await _apply(service, env, _mutate(refs, base_revision=h.head(env.db_path)[0], key="done",
                                              kind="task.complete", value="全部核对完毕"))
    assert done.accepted, done.error_code
    with pytest.raises(ClosureRejected) as after:
        await _apply(service, env, _mutate(refs, base_revision=h.head(env.db_path)[0], key="after",
                                           kind="plan.step.add", value="再补一步"))
    assert after.value.code == "task_scope_update_after_complete"
