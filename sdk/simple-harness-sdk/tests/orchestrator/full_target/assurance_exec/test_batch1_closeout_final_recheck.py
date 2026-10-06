# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""第 1 批车道 B：收尾最终事务的三项核对（A01）、尾部预留与结果不明动作（A07）、closeout-v1 记录（A17）。

原计划 §7.2："最终事务重读当前 epoch/权限/effect/运行集合才 READY→FINALIZED"；"原 operational 责任…
UNKNOWN 必须真实收敛"；收尾记录按 ``contracts/closeout-v1.schema.json`` 的字段写并钉住要求版本。

世界：产品同形部署 + 脚本化模型回复，一个一步任务跑到完成约 6 秒。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import pytest

from agent_orchestrator.assurance.codec import AssuranceError, decode
from agent_orchestrator.orchestrator.assurance_consumers import AssuranceCloseoutConsumer
from agent_orchestrator.orchestrator.assurance_recheck import EVIDENCE_STALE, stale_certificates
from agent_orchestrator.orchestrator.assurance_tick import PreparedAssuranceWork
from agent_orchestrator.orchestrator.completion_status import read_occurrence_completion
from agent_orchestrator.orchestrator.requirements_amendment import requirements_ref
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

HEX64 = re.compile(r"^[0-9a-f]{64}$")
GOAL = {"goal": "写一份 NOTES.md，列出三条要点。", "success_criteria": ["file:NOTES.md"]}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _closeout(store: Any, mission_id: str) -> tuple[Any, dict[str, Any]]:
    row = store.connection.execute(
        "SELECT * FROM assurance_closeouts WHERE mission_id=?", (mission_id,)).fetchone()
    assert row is not None, "no closeout row"
    return row, decode(row["check_body_json"])


def _consumer(world: Any) -> AssuranceCloseoutConsumer:
    return world.deployment.assurance.consumers["CLOSEOUT"]


async def _completed(world: Any, key: str) -> str:
    mission_id = world.create({**GOAL, "idempotency_key": key})["mission_id"]
    done = await world.run_until_settled(mission_id, timeout=60)
    assert str(done.status.value) == "COMPLETED", done.final_report
    return mission_id


# ------------------------------------------------------------------ A01：最终事务三项核对
def test_a01_final_consistency_rule_rechecks_epoch_and_clock_only_when_ready():
    """定稿那一次事务：纪元动了 / 时钟回拨了 → RECHECK_REQUIRED；没 READY 的评估只是投影，不比。"""
    epochs = {"mission": 7, "environment": 1, "clock_generation": 0, "wall_high_ms": 100, "clock_state": "STABLE"}
    preview = {"state": "READY", "epochs": epochs, "as_of_ms": 1000}
    same = {"state": "READY", "epochs": dict(epochs), "as_of_ms": 1005}
    AssuranceCloseoutConsumer.require_final_consistency(preview, same)  # 一致：放行
    moved = {"state": "READY", "epochs": {**epochs, "mission": 8}, "as_of_ms": 1005}
    with pytest.raises(AssuranceError) as raised:
        AssuranceCloseoutConsumer.require_final_consistency(preview, moved)
    assert raised.value.code == "RECHECK_REQUIRED"
    rolled_back = {"state": "READY", "epochs": dict(epochs), "as_of_ms": 999}
    with pytest.raises(AssuranceError) as raised:
        AssuranceCloseoutConsumer.require_final_consistency(preview, rolled_back)
    assert raised.value.code == "RECHECK_REQUIRED"
    generation = {"state": "READY", "epochs": {**epochs, "clock_generation": 1}, "as_of_ms": 1005}
    with pytest.raises(AssuranceError):
        AssuranceCloseoutConsumer.require_final_consistency(preview, generation)
    # 没到 READY：纪元照常在动，不拦
    AssuranceCloseoutConsumer.require_final_consistency(
        {**preview, "state": "DRAINING"}, {**moved, "state": "DRAINING"})


def test_a01_expired_certificate_and_clock_rollback_block_the_closeout(tmp_path):
    """根结论依赖的使用证书到期 → EVIDENCE_STALE（现有重查路径，不新造原因）；
    现在 < 时钟高水位（回拨）→ TIME_DISCONTINUITY；两者都让收尾不定稿。"""
    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            store, tenant = world.store, world.deployment.tenant_id
            mission_id = await _completed(world, "a01-expiry")
            mission = store.get_mission(mission_id)
            consumer = _consumer(world)
            rows = store.connection.execute(
                "SELECT certificate_id, consumer_kind, not_after_ms FROM assurance_use_certificates "
                "WHERE mission_id=? ORDER BY rowid", (mission_id,)).fetchall()
            assert {r["consumer_kind"] for r in rows} >= {"ACCEPTANCE", "ROOT_RESOLUTION"}
            far = max(int(r["not_after_ms"]) for r in rows) + 1
            now_ms = int(store.now * 1000)
            with store.read_view():
                current = stale_certificates(store, tenant_id=tenant, mission_id=mission_id, now_ms=now_ms)
                expired = stale_certificates(store, tenant_id=tenant, mission_id=mission_id, now_ms=far)
            assert current == []
            assert {item["certificate_id"] for item in expired} == {r["certificate_id"] for r in rows}
            assert all({"channel": "VALIDITY", "key": item["certificate_id"], "reason": "EXPIRED"}
                       in item["changed_items"] for item in expired)
            with store.read_view():
                body_far = consumer._evaluate_locked(mission, now_ms=far)
                body_now = consumer._evaluate_locked(mission, now_ms=now_ms)
                high = store.connection.execute(
                    "SELECT wall_high_ms FROM assurance_environment_state WHERE singleton=1").fetchone()[0]
                body_past = consumer._evaluate_locked(mission, now_ms=int(high) - 1)
            assert EVIDENCE_STALE in body_far["reasons"] and body_far["state"] == "NOT_READY"
            assert {item["certificate_id"] for item in body_far["stale_certificates"]} == {
                r["certificate_id"] for r in rows}
            assert EVIDENCE_STALE not in body_now["reasons"]
            assert "TIME_DISCONTINUITY" in body_past["reasons"] and body_past["state"] == "NOT_READY"
            assert "TIME_DISCONTINUITY" not in body_now["reasons"]
    asyncio.run(case())


def test_a01_epoch_moved_between_preview_and_final_commit_is_rechecked_not_finalized(tmp_path, monkeypatch):
    """READY 预览与定稿事务之间任务纪元动了一次：定稿事务按 RECHECK_REQUIRED 退回重算，下一轮再定稿。"""
    seen: dict[str, Any] = {"last_state": None, "bumped": False, "errors": [], "mission": None}
    evaluate = AssuranceCloseoutConsumer._evaluate_locked
    prepare = AssuranceCloseoutConsumer.prepare

    def record_evaluate(self, mission, *, now_ms):
        body = evaluate(self, mission, now_ms=now_ms)
        seen["last_state"] = body["state"]
        return body

    async def bump_once(self, claim):
        prepared = await prepare(self, claim)
        if not isinstance(prepared, PreparedAssuranceWork):
            return prepared
        inner, store = prepared.commit, self.store

        def commit():
            if seen["last_state"] == "READY" and not seen["bumped"]:
                seen["bumped"] = True
                with store.transaction():
                    HtnStore(store).bump_epoch(claim.mission_id, "mission", bumped_by="batch1-a01-test")
            try:
                return inner()
            except AssuranceError as error:
                seen["errors"].append(error.code)
                raise

        return PreparedAssuranceWork(commit)

    monkeypatch.setattr(AssuranceCloseoutConsumer, "_evaluate_locked", record_evaluate)
    monkeypatch.setattr(AssuranceCloseoutConsumer, "prepare", bump_once)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            mission_id = await _completed(world, "a01-epoch")
            row, body = _closeout(world.store, mission_id)
            assert row["state"] == "FINALIZED"
            assert seen["bumped"] is True
            # 纪元动了的那次 READY 没有定稿，而是退回重算
            assert "RECHECK_REQUIRED" in seen["errors"], seen
            assert len(world.store.connection.execute(
                "SELECT 1 FROM events WHERE mission_id=? AND type='MissionCompleted'", (mission_id,)).fetchall()) == 1
    asyncio.run(case())


# ------------------------------------------------------------------ A07：尾部预留与结果不明动作
def _columns(store: Any, table: str) -> list[str]:
    return [row[1] for row in store.connection.execute(f"PRAGMA table_info({table})").fetchall()]


def _insert(store: Any, table: str, values: dict[str, Any]) -> None:
    names = [name for name in _columns(store, table) if name in values]
    store.connection.execute(
        f"INSERT INTO {table}({','.join(names)}) VALUES({','.join('?' for _ in names)})",
        [values[name] for name in names])


def test_a07_a_held_reviewer_tail_blocks_the_closeout_even_when_its_reservation_row_is_settled(tmp_path):
    """审阅员尾部预留还 HELD（预留表那一行却已 SETTLED，两张表不一致）：收尾读预留本身，判未收敛（DRAINING /
    OPEN_RESERVATIONS），记录里点名这条尾部预留；任务不完成。"""
    async def case():
        provider = LayeredScriptedProvider()
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            store = world.store
            mission_id = world.create({**GOAL, "idempotency_key": "a07-tail"})["mission_id"]
            for _ in range(20):
                await world.drain(timeout=5)
                if provider.entered.is_set():
                    break
            assert provider.entered.is_set()
            # 一个真实步骤任务的预算账户（释放钩子要读到它的原任务）
            account = next(
                row[0] for row in store.connection.execute(
                    "SELECT account_id FROM budget_accounts WHERE mission_id=? ORDER BY account_id", (mission_id,))
                if store.get_task(str(row[0]).removeprefix("budget:")) is not None)
            subject = "tail:batch1-held-tail"
            now = store.now
            with store.transaction():
                _insert(store, "budget_reservations", {
                    "reservation_id": "res-batch1-held-tail", "account_id": account, "mission_id": mission_id,
                    "subject_id": subject, "state": "SETTLED", "reserved_tokens": 0, "reserved_tool_calls": 0,
                    "settled_tokens": 0, "settled_tool_calls": 0, "reserved_cost_micros": 0,
                    "settled_cost_micros": 0, "unpriced": 0, "created_at": now, "updated_at": now})
                _insert(store, "budget_tail_holds", {
                    "hold_id": "batch1-held-tail", "mission_id": mission_id, "account_id": account,
                    "subject_id": subject, "task_revision": "r0", "purpose": "critic", "request_json": "{}",
                    "remaining_attempts": 1, "state": "HELD", "release_reason": None,
                    "created_at": now, "updated_at": now})
            provider.release.set()
            mission = await world.run_until_settled(mission_id, rounds=8, timeout=20)
            assert str(mission.status.value) == "ACTIVE", mission.status
            row, body = _closeout(store, mission_id)
            assert row["state"] == "DRAINING" and body["reasons"] == ["OPEN_RESERVATIONS"], body
            assert subject in {ref["pin"]["id"] for ref in body["accounting_pending_refs"]}, body
            assert all(ref["kind"] == "reservation_fact" for ref in body["accounting_pending_refs"])
    asyncio.run(case())


def test_a07_a_handed_off_action_with_unknown_outcome_blocks_the_closeout_outside_root_scope(tmp_path):
    """任务下有一条已交出、结果不明（UNKNOWN）的动作，不在根范围的必需效果里：收尾 BLOCKED_UNKNOWN，
    记录的 unsettled_operation_refs / dangerous_work_refs 点名它；任务不完成。

    动作在步骤跑着的时候交出（任务已在执行，不是规划期——规划期的主循环本来就等核对）。"""
    async def case():
        provider = LayeredScriptedProvider()
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            store = world.store
            mission_id = world.create({**GOAL, "idempotency_key": "a07-action"})["mission_id"]
            for _ in range(20):
                await world.drain(timeout=5)
                if provider.entered.is_set():
                    break
            assert provider.entered.is_set()
            action_key = "action-batch1-unknown:v1"
            record = {"action_key": action_key, "action_id": "action-batch1-unknown", "version": 1,
                      "mission_id": mission_id, "state": "UNKNOWN", "connector": "batch1-none",
                      "operation": "publish", "target": "out-of-scope", "params": {}, "params_hash": "0" * 64,
                      "reservation_subject": None, "handoffs": 1}
            now = store.now
            with store.transaction():
                store.connection.execute(
                    "INSERT INTO actions(action_key,action_id,version,mission_id,state,json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (action_key, record["action_id"], 1, mission_id, "UNKNOWN", json.dumps(record), now, now))
            provider.release.set()
            mission = await world.run_until_settled(mission_id, rounds=8, timeout=20)
            assert str(mission.status.value) == "ACTIVE", mission.status
            row, body = _closeout(store, mission_id)
            assert row["state"] == "BLOCKED_UNKNOWN" and body["reasons"] == ["EFFECT_UNKNOWN"], body
            assert [ref["pin"]["id"] for ref in body["unsettled_operation_refs"]] == [action_key]
            assert [ref["pin"]["id"] for ref in body["dangerous_work_refs"]] == [action_key]
            assert body["unsettled_operation_refs"][0]["kind"] == "operation"
            assert body["pending_effect_keys"] == []
    asyncio.run(case())


# ------------------------------------------------------------------ A17：closeout-v1 记录
def test_a17_the_closeout_record_is_written_as_closeout_v1_and_pins_the_requirements(tmp_path):
    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            store = world.store
            mission_id = await _completed(world, "a17")
            row, body = _closeout(store, mission_id)
            assert row["state"] == "FINALIZED"
            fields = ("schema_version", "mission_id", "root_resolution_ref", "requirements_ref",
                      "completion_spec_hash", "state", "pending_effect_keys", "unsettled_operation_refs",
                      "accounting_pending_refs", "dangerous_work_refs", "report_ref", "as_of_ms", "reasons")
            assert all(name in body for name in fields), sorted(body)
            # 旧的 id 列表 / id 外键不再出现
            assert not {"resolution_id", "unknown_effects", "open_reservations", "evaluated_at_ms"} & set(body)
            assert body["schema_version"] == 1 and body["mission_id"] == mission_id
            htn = HtnStore(store)
            resolution = htn.get_goal_resolution(row["resolution_id"])
            assert body["root_resolution_ref"]["kind"] == "resolution"
            assert body["root_resolution_ref"]["pin"]["id"] == row["resolution_id"]
            assert HEX64.match(body["root_resolution_ref"]["pin"]["content_hash"])
            expected = requirements_ref(htn.get_requirements_revision(mission_id, int(resolution.requirements_version)))
            assert body["requirements_ref"] == expected  # 与改要求同一种引用：revision + content_hash
            [root] = body["root_occurrences"]
            assert body["completion_spec_hash"] == read_occurrence_completion(store, mission_id, root).scope.spec_hash
            assert (body["pending_effect_keys"], body["unsettled_operation_refs"],
                    body["accounting_pending_refs"], body["dangerous_work_refs"]) == ([], [], [], [])
            receipt_id = "assurance-finalized:" + mission_id
            assert body["report_ref"]["kind"] == "commit_receipt" and body["report_ref"]["pin"]["id"] == receipt_id
            assert isinstance(body["as_of_ms"], int) and body["reasons"] == []
            # 任务最终报告里的收尾记录与收尾行同一份结构
            report = store.get_mission(mission_id).final_report["assurance_closeout"]
            assert report["root_resolution_ref"] == body["root_resolution_ref"]
            assert report["requirements_ref"] == expected and report["report_ref"] == body["report_ref"]
            assert report["state"] == "FINALIZED" and "resolution_id" not in report
            # 定稿回执正文就是 closeout-v1 文档
            receipt = store.get_receipt(receipt_id)
            assert receipt["requirements_ref"] == expected and receipt["state"] == "FINALIZED"
            assert receipt["root_resolution_ref"] == body["root_resolution_ref"]
            # 收尾事件也用引用，不用裸 id
            [event] = [e for e in store.iter_events(mission_id)
                       if e.type == "AssuranceCloseoutEvaluated" and e.payload.get("state") == "READY"]
            assert event.payload["root_resolution_ref"] == body["root_resolution_ref"]
            assert "resolution_id" not in event.payload
    asyncio.run(case())
