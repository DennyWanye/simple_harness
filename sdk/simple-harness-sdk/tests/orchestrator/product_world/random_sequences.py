# SPDX-License-Identifier: Apache-2.0
"""随机动作序列驱动（HTN 补齐 F1-6；原 TaskGraph 计划 §15 的随机状态机，小号版）。

在产品同形世界里，按固定种子的随机序列做七种动作，每一步之后对照一组不变式：

========  ==========================================================
新增       新建一个用户任务（规划器提出并采用一个带新步骤的做法）
细化       规划器选一个带子目标的做法，子目标随后被细化
接受       推进一轮：执行者交结果、审阅员判通过
撤回       用户经改要求删掉一条内容要求
取消       用户取消一个任务（概率低；之后可以再新建）
换方法     下一次最终审查打回，规划器提一个新做法并换上去
关库重开   退出产品同形世界，同一数据目录重开
========  ==========================================================

对照物：①执行图历史在临时副本上离线重建（``replay_taskgraph``）通过，且每个修订号的清单哈希与在线
一致；②关库重开前后，每个任务的快照逐字节一致；③不变式（见 :func:`check_invariants`）。

不装 Hypothesis：序列取自 ``random.Random(seed)``（与保证通道已有的随机序列用例同一规矩）。失败时
保存种子、动作序列，按"去掉一段动作再跑"缩小，留最小反例。
"""
from __future__ import annotations

import asyncio
import json
import random
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_orchestrator.observability.taskgraph_replay import replay_taskgraph
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.taskgraph_store import TaskGraphStore
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    decision,
    one_step_method,
    planner_reply,
    review_input,
    review_reply,
)

ACTIONS = ("new", "refine", "accept", "withdraw", "cancel", "replace", "restart")
WEIGHTS = {"new": 3, "refine": 2, "accept": 8, "withdraw": 1, "cancel": 1, "replace": 2, "restart": 2}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
SOURCE = {"kind": "MAIN_AGENT", "run_id": "random", "call_id": "random", "permission_mode": "auto"}


class InvariantBroken(AssertionError):
    pass


# ----------------------------------------------------------------------------- the scripted roles

def _sub_goal_method(context: dict[str, Any]) -> dict[str, Any] | None:
    """Root method: one sub-goal for the first requirement, one step for the rest (细化)."""
    request = context["request"]
    parts = [item for item in request.get("subgoal_types") or () if item["task_type_ref"]["id"] == "sub-goal-1"]
    names = [item["id"] for item in request["criterion_evidence"]]
    if not parts or len(names) < 2:
        return None
    method = one_step_method(context)
    [step] = method["steps"]
    method["steps"] = [
        {"local_id": "part", "task_type_ref": parts[0]["task_type_ref"], "form": "compound",
         "arguments": {"goal": {"op": "constant", "value": "完成第一份"}}, "required_capabilities": [],
         "obligation_relation": "refines_parent"},
        dict(step, local_id="rest")]
    method["ordering"] = [{"before": "part", "after": "rest"}]
    method["composition"]["criterion_links"] = (
        [{"parent_criterion_id": names[0], "child_step": "part", "child_criterion_id": names[0],
          "evidence_requirement": "子目标完成第一份"}]
        + [{"parent_criterion_id": name, "child_step": "rest", "child_criterion_id": name,
            "evidence_requirement": "rest 完成其余"} for name in names[1:]])
    method["composition"]["finalizer_step"] = "rest"
    return method


@dataclass
class Script:
    """The roles' answers; ``prefer_sub_goal`` and ``rework`` are set by the actions."""

    rng: random.Random
    prefer_sub_goal: set[str] = field(default_factory=set)  # idempotency keys
    rework: set[str] = field(default_factory=set)  # mission ids whose next final review sends it back
    proposed: dict[str, list[str]] = field(default_factory=dict)

    def planner(self, request: Any) -> Any:
        package = package_of(request)
        mission_id = str((package.get("mission") or {}).get("mission_id") or (package.get("mission") or {}).get("id"))
        contexts = package.get("method_proposal_contexts") or []
        if package.get("repair_requests"):
            return self._repair(package, mission_id) or planner_reply(request)
        if contexts and not (package.get("method_selection") or [{}])[0].get("applicable"):
            context = contexts[0]
            goal_type = str((context["request"].get("goal_type_ref") or {}).get("id"))
            method = None
            if goal_type == "user-goal" and mission_id in self.prefer_sub_goal:
                method = _sub_goal_method(context)
            method = method or one_step_method(context)
            self.proposed.setdefault(mission_id, []).append(method["method_id"])
            return decision(context["subject_key"], "PROPOSE_METHOD",
                            {"method_proposal": {"method": method, "rationale": "随机序列的做法。"}}, "提做法。")
        return planner_reply(request)

    def _repair(self, package: dict[str, Any], mission_id: str) -> Any:
        goals = [item for item in package["views"]["goals"] if item["form"] == "compound" and item.get("adopted_method")]
        if not goals:
            return None
        goal = goals[0]
        current = goal["adopted_method"]["method_ref"]
        fresh = [item["method_ref"] for item in package["views"]["methods"]
                 if item["method_ref"] != current and (item.get("review") or {}).get("outcome") == "PASSED"
                 and item["method_ref"]["id"] in self.proposed.get(mission_id, ())]
        if not fresh:
            context = next((item for item in package.get("method_proposal_contexts") or ()
                            if item["subject_key"] == goal["subject_key"]), None)
            if context is None:
                return None
            method = one_step_method(context)
            self.proposed.setdefault(mission_id, []).append(method["method_id"])
            return decision(goal["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": method, "rationale": "换一个做法。"}}, "先提替换的做法。")
        instance = next((item for item in package["visible_refs"] if item["kind"] == "method_instance"
                         and item["id"] == goal["adopted_method"]["method_instance_id"]), None)
        if instance is None:
            return None
        return decision(goal["subject_key"], "REPAIR", {
            "repair_kind": "REPLACE_METHOD", "rejected_method_instance": instance,
            "replacement_method_ref": dict(fresh[-1]), "bindings": goal["params"]}, "换上新做法。")

    def reviewer(self, request: Any) -> Any:
        data = review_input(request)
        if data is None:
            return None
        package = data.get("package") or {}
        mission_id = str((package.get("binding") or {}).get("mission_id") or "")
        if package.get("purpose") == "MISSION_FINAL" and mission_id in self.rework:
            self.rework.discard(mission_id)
            return review_reply(data, verdict="REWORK", grade="FAIL")
        return review_reply(data)


# ----------------------------------------------------------------------------- invariants

def _mission_ids(store: Any) -> list[str]:
    return [row[0] for row in store.connection.execute("SELECT mission_id FROM missions ORDER BY created_at")]


def check_invariants(store: Any, scratch: Path, step: int) -> None:
    htn = HtnStore(store)
    for mission_id in _mission_ids(store):
        events = list(store.list_events(mission_id))
        seqs = [event.seq for event in events]
        if seqs != sorted(seqs) or len(set(seqs)) != len(seqs):
            raise InvariantBroken(f"{mission_id}: event sequence numbers are not strictly increasing")
        active = store.connection.execute(
            "SELECT count(*) FROM plan_revisions WHERE mission_id=? AND state='ACTIVE'", (mission_id,)).fetchone()[0]
        if active > 1:
            raise InvariantBroken(f"{mission_id}: {active} active plan revisions")
        ended = next((event.seq for event in events
                      if event.type in {"MissionCompleted", "MissionFailed", "MissionCancelled"}), None)
        if ended is not None and any(event.type == "AttemptCreated" and event.seq > ended for event in events):
            raise InvariantBroken(f"{mission_id}: an attempt was created after the mission ended")
        for attempt_id, count in store.connection.execute(
                "SELECT attempt_id, count(*) FROM taskgraph_attempt_inputs WHERE mission_id=? GROUP BY attempt_id"
                " HAVING count(*) > 1", (mission_id,)):
            raise InvariantBroken(f"{mission_id}: attempt {attempt_id} froze {count} input sets")
        for row in store.connection.execute(
                "SELECT subject_id, reserved_tokens, settled_tokens, state FROM budget_reservations"
                " WHERE mission_id=? AND settled_tokens IS NOT NULL AND settled_tokens > reserved_tokens"
                "", (mission_id,)):
            raise InvariantBroken(f"{mission_id}: reservation {row[0]} settled above what it reserved")
        accepted = {acceptance.task_id: acceptance for acceptance in htn.list_acceptances(mission_id)}
        for task_id in accepted:
            records = [record for package in htn.list_review_packages(mission_id)
                       if str(package.binding.subject_ref.id) == str(task_id)
                       for record in [htn.official_review_record(str(package.package_id))] if record is not None]
            if not records:
                raise InvariantBroken(f"{mission_id}: task {task_id} accepted without an official review record")
        plan = htn.active_plan_revision(mission_id)
        if plan is not None and int(plan.revision) >= 1:
            target = scratch / f"replay-{step}-{mission_id}.db"
            report = replay_taskgraph(store, mission_id=mission_id, through_revision=int(plan.revision),
                                      target_path=target)
            if report.status != "GRAPH_PROJECTION_VERIFIED":
                raise InvariantBroken(f"{mission_id}: offline replay says {report.status}")
            import sqlite3

            with sqlite3.connect(target) as db:
                offline = dict(db.execute("SELECT revision, manifest_hash FROM revisions"))
            graphs = TaskGraphStore(store)
            for revision, digest in offline.items():
                online = graphs.read_revision(mission_id, int(revision)).record.manifest_hash
                if online != digest:
                    raise InvariantBroken(f"{mission_id}: revision {revision} replays to another manifest")
            target.unlink()


def snapshots(store: Any) -> str:
    return json.dumps({mission_id: store.snapshot(mission_id) for mission_id in _mission_ids(store)},
                      sort_keys=True, ensure_ascii=False, default=str)


# ----------------------------------------------------------------------------- the driver

def plan_actions(seed: int, steps: int) -> list[str]:
    rng = random.Random(seed)
    names = list(WEIGHTS)
    return ["new"] + rng.choices(names, weights=[WEIGHTS[name] for name in names], k=max(0, steps - 1))


async def run_actions(root: Path, seed: int, actions: Sequence[str]) -> list[str]:
    """Execute ``actions``; returns the log.  Raises :class:`InvariantBroken` on the first break."""
    rng = random.Random(seed * 7919 + 1)
    script = Script(rng)
    log: list[str] = []
    scratch = Path(tempfile.mkdtemp(prefix="random-replay-"))
    counter = 0
    world_cm = product_world(root, LayeredScriptedProvider(planner=script.planner, reviewer=script.reviewer))
    world = await world_cm.__aenter__()
    try:
        for step, action in enumerate(actions):
            live = [m for m in _mission_ids(world.store)
                    if str(world.store.get_mission(m).status.value) not in TERMINAL]
            if action in {"new", "refine"} or (not live and action not in {"restart"}):
                counter += 1
                key = f"random-{seed}-{counter}"
                count = 1 + rng.randrange(3) if action == "new" else 2 + rng.randrange(2)
                created = world.create({"goal": f"随机任务 {counter}", "idempotency_key": key,
                                        "success_criteria": [f"file:out/{counter}-{n}.md" for n in range(count)]})
                if action == "refine":
                    script.prefer_sub_goal.add(created["mission_id"])
                log.append(f"{step}:{action}:{created['mission_id']}")
            elif action == "accept":
                await world.drain(timeout=20)
                log.append(f"{step}:accept")
            elif action == "withdraw":
                candidates = []
                for mission_id in live:
                    latest = HtnStore(world.store).latest_requirements_revision(mission_id)
                    if latest is not None and len(latest.criteria) >= 2:
                        candidates.append((mission_id, latest))
                if candidates:
                    mission_id, latest = candidates[rng.randrange(len(candidates))]
                    try:
                        world.control.amend_requirements({
                            "mission_id": mission_id, "command_id": f"withdraw-{seed}-{step}",
                            "expected_requirements_ref": {"id": str(latest.revision_id),
                                                          "revision": int(latest.revision),
                                                          "content_hash": latest.content_hash()},
                            "changes": [{"op": "remove", "criterion_id": str(latest.criteria[-1].criterion_id)}],
                            "reason": "用户撤回一条要求", "source": SOURCE})
                        log.append(f"{step}:withdraw:{mission_id}")
                    except Exception as error:  # noqa: BLE001 - a refused amendment is an outcome, not a break
                        log.append(f"{step}:withdraw-refused:{type(error).__name__}:{getattr(error, 'code', '')}")
            elif action == "cancel" and live:
                mission_id = live[rng.randrange(len(live))]
                world.loop.commit.cancel_mission(mission_id)
                log.append(f"{step}:cancel:{mission_id}")
            elif action == "replace" and live:
                mission_id = live[rng.randrange(len(live))]
                script.rework.add(mission_id)
                log.append(f"{step}:replace:{mission_id}")
            elif action == "restart":
                await world.drain(timeout=20)
                before = snapshots(world.store)
                await world_cm.__aexit__(None, None, None)
                world_cm = product_world(root, LayeredScriptedProvider(planner=script.planner, reviewer=script.reviewer))
                world = await world_cm.__aenter__()
                after = snapshots(world.store)
                if after != before:
                    raise InvariantBroken(f"step {step}: a mission's snapshot changed across close and reopen")
                log.append(f"{step}:restart")
            check_invariants(world.store, scratch, step)
    except InvariantBroken:
        raise
    except Exception as error:  # 主循环崩了（异常冲出 drain）同样是反例，交给缩小与留档
        raise InvariantBroken(f"step {len(log)}: crashed: {type(error).__name__}: {error}; "
                              f"log={log}") from error
    finally:
        await world_cm.__aexit__(None, None, None)
    return log


def shrink(seed: int, actions: list[str], attempts: int = 16) -> list[str]:
    """Remove chunks of actions while the run still breaks an invariant (simple delta debugging)."""
    current = list(actions)
    chunk = max(1, len(current) // 2)
    while attempts > 0 and chunk >= 1 and len(current) > 1:
        changed = False
        for start in range(1, len(current), chunk):  # keep the first "new"
            candidate = current[:start] + current[start + chunk:]
            attempts -= 1
            try:
                asyncio.run(run_actions(Path(tempfile.mkdtemp(prefix="random-shrink-")) / "root", seed, candidate))
            except InvariantBroken:
                current, changed = candidate, True
                break
            except Exception:  # noqa: BLE001 - a different failure is not the same counterexample
                pass
            if attempts <= 0:
                break
        if not changed:
            chunk //= 2
    return current


__all__ = ("ACTIONS", "InvariantBroken", "check_invariants", "plan_actions", "run_actions", "shrink")
