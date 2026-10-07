# SPDX-License-Identifier: Apache-2.0
"""时点使用证书（推后第 1 批 A26，原计划 ASSURANCE-EXEC-1.1 §8.4、C.1；AER §9.4、§12.2）。

规划（PLAN）、开工交接（START）、装上下文（CONTEXT）、恢复在途尝试（RECOVERY）在用证据的那一刻
签一张使用证书：证据此刻当前才签、才用；不当前就不签，原因具名（哪一项、为什么）。MAINTAIN 没有
持续监测，签发方直接挡住。时点证书在签发事务里就用掉了，不进有效性观察与到期唤醒。

剧本沿用 ``test_repair_staleness``：两步任务 → 结论入库、摘要核对属实 → 最终审查打回 → 换做法重做。
重做后旧做法的知识与摘要"已过时"，新做法的是当前的——同一个库里两种都有。

**改坏检验**（记录见车道 P1b 记录）：签发方不核某种证据 → 对应用途的拒绝支变绿为红；消费方不签
证书 → 产品同形支找不到证书。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.assurance.certificates import POINT_PURPOSES
from agent_orchestrator.assurance.codec import decode
from agent_orchestrator.assurance.expiry import AssuranceExpiry
from agent_orchestrator.context import knowledge_tools
from agent_orchestrator.memory.knowledge_standing import CURRENT, knowledge_standing
from agent_orchestrator.orchestrator.assurance_point_use import (
    ATTEMPT_CONSUMER,
    PLANNING_REQUEST_CONSUMER,
    EvidenceClaim,
    certify_point_use_locked,
    planning_evidence_stale,
    planning_claims,
)
from agent_orchestrator.orchestrator.assurance_recheck import live_usable_certificates
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import DEFAULT_TOOLS, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider
from test_repair_staleness import KNOWLEDGE_TOOLS, scenario


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _certificates(store: Any, mission_id: str, purpose: str) -> list[Any]:
    return store.connection.execute(
        "SELECT * FROM assurance_use_certificates WHERE mission_id=? AND purpose=? ORDER BY rowid",
        (mission_id, purpose)).fetchall()


def _receipt(store: Any, certificate_id: str) -> dict[str, Any]:
    [row] = store.connection.execute(
        "SELECT receipt_json FROM commit_receipts WHERE kind='AssuranceUseCertified' AND subject_id=?",
        (certificate_id,)).fetchall()
    return json.loads(row[0])


async def _run(world: Any, key: str, planner: Any = None) -> str:
    mission_id = world.create({"goal": "写两份笔记", "idempotency_key": key,
                               "success_criteria": ["file:notes/a.md", "file:notes/b.md"]})["mission_id"]
    mission = await world.run_until_settled(mission_id, rounds=40)
    assert str(mission.status.value) == "COMPLETED", mission.final_report
    return mission_id


def _scenario_provider(state: dict[str, Any], *, planner_wrap: Any = None) -> LayeredScriptedProvider:
    planner, worker, reviewer = scenario(state)
    return LayeredScriptedProvider(planner=planner if planner_wrap is None else planner_wrap(planner),
                                   worker=worker, reviewer=reviewer)


# ------------------------------------------------------------------- 产品同形（默认路径）
def test_planning_and_context_use_carry_a_certificate_on_the_default_path(tmp_path):
    """默认路径：每个看到已验证知识或核对过摘要的规划请求、每次带着它们开工的尝试，都有一张
    签发时 USABLE 的时点证书，回执里列着这次用的每一项证据。时点证书不进有效性观察与到期唤醒。"""
    state: dict[str, Any] = {"methods": []}

    async def case():
        async with product_world(tmp_path / "root", _scenario_provider(state),
                                 allowed_tools=DEFAULT_TOOLS + KNOWLEDGE_TOOLS) as world:
            store = world.store
            mission_id = await _run(world, "point-use-default")

            # PLAN：规划包里给了知识 / 摘要的每个新请求都签了证书，消费方就是请求编号
            plan_certs = {row["consumer_id"]: row for row in _certificates(store, mission_id, "PLAN")}
            asked = 0
            for row in store.connection.execute(
                    "SELECT intent_id, config_json FROM dispatch_intents WHERE mission_id=? AND kind='plan'",
                    (mission_id,)).fetchall():
                config = json.loads(row["config_json"])
                if "planning_package" not in config or config.get("planning_decision_attempt_ordinal"):
                    continue  # 不是规划请求；或是格式重试，沿用开头那次请求冻结的包
                claims = planning_claims(config["planning_package"])
                if not claims:
                    assert row["intent_id"] not in plan_certs
                    continue
                asked += 1
                certificate = plan_certs[row["intent_id"]]
                assert certificate["consumer_kind"] == PLANNING_REQUEST_CONSUMER
                assert decode(certificate["certificate_json"])["decision"] == "USABLE"
                assert _receipt(store, certificate["certificate_id"])["evidence"] == [
                    claim.to_json() for claim in claims]
            assert asked, "the rework round should have shown the Planner knowledge or summaries"

            # CONTEXT：每个冻结了知识的尝试都有一张证书，回执里含它冻结的每条知识
            context_certs = {row["consumer_id"]: row for row in _certificates(store, mission_id, "CONTEXT")}
            pushed = 0
            for row in store.connection.execute(
                    "SELECT subject_id, config_json FROM dispatch_intents WHERE mission_id=? AND kind='attempt'",
                    (mission_id,)).fetchall():
                frozen = json.loads(row["config_json"]).get("knowledge") or []
                if not frozen:
                    continue
                pushed += 1
                certificate = context_certs[row["subject_id"]]
                assert certificate["consumer_kind"] == ATTEMPT_CONSUMER
                evidence = _receipt(store, certificate["certificate_id"])["evidence"]
                assert {(item["id"], item["version"]) for item in frozen} <= {
                    (item["id"], item["version"]) for item in evidence if item["kind"] == "knowledge"}
            assert pushed and context_certs

            # 时点证书在签发事务里用掉了：不进有效性观察，也不排到期唤醒
            live = live_usable_certificates(store.connection, mission_id, limit=4096)
            assert not [row for row in live if row["purpose"] in POINT_PURPOSES]
            root = world.loop.commit._assurance_root_gate.require_execution().root_incarnation_id
            emitted = AssuranceExpiry(store).emit_due(mission_id, root_incarnation_id=root,
                                                      now_ms=int(store.now * 1000) + 10 ** 12)
            assert not [event for event in emitted if event.payload["purpose"] in POINT_PURPOSES]

    asyncio.run(case())


def test_a_planning_reply_whose_evidence_went_stale_is_asked_again(tmp_path, monkeypatch):
    """规划器作答期间，它看到的知识过时了：回复按"请求过期"退回（不扣次数），字段路径
    ``/planning_evidence``，说明里点名过时的那条；重问一次后照常完成。"""
    import agent_orchestrator.memory.knowledge_standing as standing

    state: dict[str, Any] = {"methods": []}
    original = standing.acceptance_is_current
    stale: dict[str, Any] = {}

    def wrap(planner: Any) -> Any:
        def ask(request: Any) -> Any:
            package = package_of(request)
            knowledge = [row for row in package["views"].get("knowledge") or () if row["layer"] == "verified"]
            if package.get("repair_requests") and knowledge and not stale:
                # 这一轮看到的第一条知识，在规划器作答期间过时（它所依据的验收不再撑着那一步）
                record = world_store["store"].get_knowledge(knowledge[0]["id"])
                stale["acceptance"] = record.support["acceptance_id"]
                stale["knowledge"] = record.id
                monkeypatch.setattr(standing, "acceptance_is_current", lambda store, mission, acceptance: (
                    (False, "test_stale") if acceptance == stale["acceptance"] and "done" not in stale
                    else original(store, mission, acceptance)))
            elif stale and "done" not in stale:
                stale["done"] = True  # 重问的那一次：恢复原判定
            return planner(request)
        return ask

    world_store: dict[str, Any] = {}

    async def case():
        async with product_world(tmp_path / "root", _scenario_provider(state, planner_wrap=wrap),
                                 allowed_tools=DEFAULT_TOOLS + KNOWLEDGE_TOOLS) as world:
            world_store["store"] = world.store
            mission_id = await _run(world, "point-use-plan-stale")
            assert stale.get("knowledge"), "the rework round showed no verified knowledge"
            refused = [event for event in world.store.iter_events(mission_id) if event.type == "PlanningRejected"
                       and any(problem.get("field_path") == "/planning_evidence"
                               for problem in (event.payload.get("detail") or {}).get("problems") or ())]
            assert refused, "the stale-evidence reply was not refused"
            [problem] = [problem for problem in refused[0].payload["detail"]["problems"]
                         if problem.get("field_path") == "/planning_evidence"]
            assert problem["code"] == "REQUEST_BINDING_STALE"
            assert stale["knowledge"] in json.dumps(problem, ensure_ascii=False)

    asyncio.run(case())


# --------------------------------------------------------------- 每种用途：放行 / 具名拒绝
def _knowledge(store: Any, mission_id: str) -> tuple[Any, Any]:
    records = sorted(store.list_knowledge(mission_id), key=lambda item: item.created_at)
    current = [record for record in records if knowledge_standing(store, record) == CURRENT]
    old = [record for record in records if knowledge_standing(store, record).startswith("STALE:")]
    assert current and old
    return current[-1], old[0]


def _certify(world: Any, mission_id: str, purpose: str, consumer: tuple[str, str],
             claims: list[EvidenceClaim], *, subject: tuple[str, str]) -> Any:
    store = world.store
    with store.transaction():
        return certify_point_use_locked(world.loop.commit, mission_id=mission_id, purpose=purpose,
                                        consumer_kind=consumer[0], consumer_id=consumer[1],
                                        claims=tuple(claims), subject=subject)


@pytest.mark.parametrize("purpose", ["PLAN", "CONTEXT", "RECOVERY"])
def test_knowledge_and_summaries_are_used_only_while_current(tmp_path, monkeypatch, purpose):
    """当前的知识与摘要：签出 USABLE 证书并落库。已过时的知识：不签，原因写明哪条、为什么；
    摘要换了文字：不签，写明哪条摘要变了。"""
    state: dict[str, Any] = {"methods": []}

    async def case():
        async with product_world(tmp_path / "root", _scenario_provider(state),
                                 allowed_tools=DEFAULT_TOOLS + KNOWLEDGE_TOOLS) as world:
            store = world.store
            mission_id = await _run(world, f"point-use-{purpose.lower()}")
            current, old = _knowledge(store, mission_id)
            [summary, *_] = knowledge_tools.step_summaries(store, mission_id)
            consumer = (PLANNING_REQUEST_CONSUMER, "request-under-test") if purpose == "PLAN" else (
                ATTEMPT_CONSUMER, "attempt-under-test")
            subject = ("requirements", mission_id) if purpose == "PLAN" else ("task", summary["source_task"])
            fresh = [EvidenceClaim.knowledge(current.id, current.version),
                     EvidenceClaim.summary(summary["id"], summary["summary_sha256"])]

            used = _certify(world, mission_id, purpose, consumer, fresh, subject=subject)
            assert used.usable and used.refusals == ()
            [row] = [row for row in _certificates(store, mission_id, purpose) if row["consumer_id"] == consumer[1]]
            body = decode(row["certificate_json"])
            assert body["decision"] == "USABLE" and body["purpose"] == purpose
            assert {item["channel"] for item in body["read_set"]} == {"OBJECT", "QUERY_SET", "ACCESS", "POLICY"}
            assert _receipt(store, row["certificate_id"])["evidence"] == [claim.to_json() for claim in fresh]

            refused = _certify(world, mission_id, purpose, (consumer[0], consumer[1] + "-2"),
                               [*fresh, EvidenceClaim.knowledge(old.id, old.version)], subject=subject)
            standing_now = knowledge_standing(store, old)
            assert not refused.usable
            assert refused.refusals == (f"knowledge:{old.id}@{old.version}:{standing_now}",)
            changed = _certify(world, mission_id, purpose, (consumer[0], consumer[1] + "-3"),
                               [EvidenceClaim.summary(summary["id"], "0" * 64)], subject=subject)
            assert changed.refusals == (f"summary:{summary['id']}:changed",)
            assert not [row for row in _certificates(store, mission_id, purpose)
                        if row["consumer_id"] in {consumer[1] + "-2", consumer[1] + "-3"}]

            if purpose == "PLAN":
                # 复核：签过证书的请求，证据仍当前 → 不拦；它所依据的验收不再撑着那一步 → 说出哪一项
                import agent_orchestrator.memory.knowledge_standing as standing

                assert planning_evidence_stale(world.loop.commit, mission_id, consumer[1]) is None
                monkeypatch.setattr(standing, "acceptance_is_current", lambda *args: (False, "test_gone"))
                reason = planning_evidence_stale(world.loop.commit, mission_id, consumer[1])
                assert reason is not None and f"knowledge:{current.id}@{current.version}" in reason, reason

    asyncio.run(case())


def test_start_handoff_ground_is_certified_and_names_what_went_stale(tmp_path, monkeypatch):
    """开工地基（这一步的有效性见证与它们背后的验收）当前 → 签 START 证书，钉住这一步与上游验收；
    上游验收不再撑着那一步、又没有新验收顶上 → 不签，原因与原交接核对同一个写法（"为什么:验收"）。"""
    import agent_orchestrator.memory.knowledge_standing as standing
    from agent_orchestrator.testing.scripted_replies import decision, planner_reply
    from test_input_revisions import relay

    def planner(request: Any) -> Any:
        package = package_of(request)
        contexts = package.get("method_proposal_contexts") or []
        if contexts and not (package.get("method_selection") or [{}])[0].get("applicable"):
            return decision(contexts[0]["subject_key"], "PROPOSE_METHOD", {"method_proposal": {
                "method": relay(contexts[0]), "rationale": "先写 a.md，再接着写 b.md。"}}, "两步接力。")
        return planner_reply(request)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=planner)) as world:
            store = world.store
            mission_id = await _run(world, "point-use-start")
            rows = store.connection.execute(
                "SELECT consumer_id, witness_json FROM validity_witnesses WHERE mission_id=? "
                "AND consumer_kind='task' ORDER BY as_of_ms", (mission_id,)).fetchall()
            grounded = [row["consumer_id"] for row in rows if any(
                ref["kind"] == "acceptance" for ref in json.loads(row["witness_json"]).get("support_refs") or ())]
            assert grounded, "the relay step holds no input witness"
            task_id = grounded[-1]
            used = _certify(world, mission_id, "START", ("ACTION", "action-under-test"),
                            [EvidenceClaim.step_ground(task_id)], subject=("task", task_id))
            assert used.usable, used.refusals
            body = decode(_certificates(store, mission_id, "START")[-1]["certificate_json"])
            kinds = {decode(item["key"])["kind"] for item in body["read_set"] if item["channel"] == "OBJECT"}
            assert {"task", "acceptance"} <= kinds

            monkeypatch.setattr(standing, "acceptance_is_current", lambda *args: (False, "test_gone"))
            refused = _certify(world, mission_id, "START", ("ACTION", "action-under-test-2"),
                               [EvidenceClaim.step_ground(task_id)], subject=("task", task_id))
            assert not refused.usable
            [reason] = refused.refusals
            assert reason.startswith("test_gone:acc-"), reason
            assert len(_certificates(store, mission_id, "START")) == 1

    asyncio.run(case())


def test_maintain_is_refused_without_a_continuous_monitor(tmp_path):
    """§8.4：MAINTAIN 要实际持续监测；产品没有，签发方直接挡住，什么都不写。"""
    state: dict[str, Any] = {"methods": []}

    async def case():
        async with product_world(tmp_path / "root", _scenario_provider(state),
                                 allowed_tools=DEFAULT_TOOLS + KNOWLEDGE_TOOLS) as world:
            store = world.store
            mission_id = await _run(world, "point-use-maintain")
            current, _ = _knowledge(store, mission_id)
            refused = _certify(world, mission_id, "MAINTAIN", (ATTEMPT_CONSUMER, "attempt-maintain"),
                               [EvidenceClaim.knowledge(current.id, current.version)],
                               subject=("task", current.source_task))
            assert refused.refusals == ("MAINTAIN_MONITOR_UNAVAILABLE",)
            assert _certificates(store, mission_id, "MAINTAIN") == []

    asyncio.run(case())


# ------------------------------------------------------------------ RECOVERY：崩溃重启
@pytest.mark.parametrize("ground", ["current", "stale"])
def test_an_attempt_cut_off_by_a_restart_resumes_only_on_current_evidence(tmp_path, monkeypatch, ground):
    """第二步带着第一步核对过的摘要开工，交给执行侧后进程没了。重开时恢复它之前按 RECOVERY 重签：

    * 摘要仍当前 → 签出 RECOVERY 证书，原来那次尝试接着跑，这一步始终只有一次尝试；
    * 摘要所依据的验收已不再当前 → 不恢复：这次尝试按"被打断"记丢失（不扣次数），失败明细点名
      过时的摘要；这一步拿当前上下文重做，任务照常完成。"""
    import agent_orchestrator.memory.knowledge_standing as standing
    from agent_orchestrator.orchestrator.failure_classes import INTERRUPTED, classify_failure
    from agent_orchestrator.storage.store import InjectedCrash
    from agent_orchestrator.testing.scripted_replies import review_input, review_reply
    from test_blackboard_tools import _two_step_planner

    def reviewer(request: Any) -> Any:
        package = review_input(request)
        if package is None:
            return None
        return review_reply(package, summary=lambda row: {"faithful": True, "reason": "与结果里的文件一致"})

    def provider() -> LayeredScriptedProvider:
        return LayeredScriptedProvider(planner=_two_step_planner, reviewer=reviewer)

    async def case():
        root = tmp_path / "root"
        async with product_world(root, provider()) as world:
            world.loop.arm_fault("after_submit", kind="attempt", skip=1)
            mission_id = world.create({"goal": "写两份笔记", "idempotency_key": f"recover-{ground}",
                                       "success_criteria": ["file:notes/a.md", "file:notes/b.md"]})["mission_id"]
            for _ in range(40):
                try:
                    await world.drain(timeout=10)
                except InjectedCrash:
                    break
                if "after_submit:attempt" in world.store.fired:
                    break
            assert "after_submit:attempt" in world.store.fired
            [cut] = [row for row in world.store.connection.execute(
                "SELECT subject_id FROM dispatch_intents WHERE mission_id=? AND kind='attempt' "
                "AND state IN ('AGENT_CREATED','SUBMITTED')", (mission_id,))]
            attempt_id = cut[0]
            [context] = [row for row in _certificates(world.store, mission_id, "CONTEXT")
                         if row["consumer_id"] == attempt_id]
            evidence = _receipt(world.store, context["certificate_id"])["evidence"]
            assert [item["kind"] for item in evidence] == ["summary"], evidence
        if ground == "stale":
            monkeypatch.setattr(standing, "acceptance_is_current", lambda *args: (False, "test_gone"))
        async with product_world(root, provider()) as world:
            store = world.store
            for _ in range(40):
                await world.drain(timeout=20)
                if str(store.get_mission(mission_id).status.value) in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
            assert str(store.get_mission(mission_id).status.value) == "COMPLETED"
            attempt = store.get_attempt(attempt_id)
            siblings = store.list_attempts(attempt.task_id)
            recovered = [row for row in _certificates(store, mission_id, "RECOVERY")
                         if row["consumer_id"] == attempt_id]
            if ground == "current":
                assert len(siblings) == 1 and len(recovered) == 1
                assert _receipt(store, recovered[0]["certificate_id"])["evidence"] == evidence
                return
            assert recovered == []
            assert str(attempt.status.value) == "LOST", attempt.status
            assert attempt.failure["reason"] == "recovery_use_refused"
            assert attempt.failure["refusals"] == [f"summary:{evidence[0]['id']}:not_current"]
            assert classify_failure(attempt.failure) == INTERRUPTED
            assert len(siblings) == 2

    asyncio.run(case())


# ---------------------------------------------- 运行中读知识（裁决 2026-10-07 第 5 件）
def test_a_worker_reading_knowledge_mid_run_goes_through_the_same_issuer(tmp_path, monkeypatch):
    """执行者运行中用 ``knowledge_read`` 读一条已验证知识，与装上下文同一道门：签 CONTEXT 证书
    （消费方是这次工具调用）。证据此刻不当前、时钟不可信 → 不给正文，原因具名。"""
    import agent_orchestrator.memory.knowledge_standing as standing
    from agent_orchestrator.orchestrator.assurance_point_use import TOOL_CALL_CONSUMER, knowledge_handover
    from test_repair_staleness import _find, _tool_results

    state: dict[str, Any] = {"methods": []}
    read: list[dict[str, Any]] = []
    planner, worker, reviewer = scenario(state)

    def reading_worker(request: Any) -> Any:
        package = package_of(request)
        if list(package.get("task_contract", {}).get("outputs") or []) != ["notes/b.md"]:
            return worker(request)
        results = _tool_results(request)
        write = ("workspace_write_file", {"path": "notes/b.md", "content": "# 第二份\n\n- 接着第一份\n"})
        if not results:
            return ("knowledge_list", {})
        if len(results) == 1:
            verified = [item for item in _find(json.loads(results[0]), "items") or ()
                        if item.get("layer") == "verified"]
            state["cited"] = [item["ref"] for item in verified]
            return ("knowledge_read", {"id": verified[0]["id"]}) if verified and not read else write
        if len(results) == 2 and not read:
            read.append(json.loads(results[1])["value"])
            return write
        return worker(request)

    async def case():
        provider = LayeredScriptedProvider(planner=planner, worker=reading_worker, reviewer=reviewer)
        async with product_world(tmp_path / "root", provider, allowed_tools=DEFAULT_TOOLS + KNOWLEDGE_TOOLS) as world:
            store = world.store
            mission_id = await _run(world, "point-use-tool-read")
            assert read and read[0]["content"], read
            certificates = [row for row in _certificates(store, mission_id, "CONTEXT")
                            if row["consumer_kind"] == TOOL_CALL_CONSUMER]
            assert len(certificates) == 1
            evidence = _receipt(store, certificates[0]["certificate_id"])["evidence"]
            assert evidence == [{"kind": "knowledge", "id": read[0]["id"], "version": read[0]["version"]}]

            # 任务完成后同一道门再读：当前 → 给正文并留证书；判定说过时 → 不给；时钟回拨 → 不给
            current, _ = _knowledge(store, mission_id)
            handover = knowledge_handover(world.loop.commit, mission_id=mission_id,
                                          consumer_id="tool-call-under-test", task_id=current.source_task)
            served = knowledge_tools.read_knowledge_tool(store, mission_id, "knowledge_read",
                                                         {"id": current.id}, handover=handover)
            assert served["content"] and len([row for row in _certificates(store, mission_id, "CONTEXT")
                                              if row["consumer_id"] == "tool-call-under-test"]) == 1
            def goes_stale_at_handover(claim: Any) -> Any:
                # 目录照常列出；正文取出后、交出去之前，它所依据的验收不再撑着那一步
                with monkeypatch.context() as patch:
                    patch.setattr(standing, "acceptance_is_current", lambda *args: (False, "test_gone"))
                    return handover(claim)

            stale = knowledge_tools.read_knowledge_tool(store, mission_id, "knowledge_read",
                                                        {"id": current.id}, handover=goes_stale_at_handover)
            assert stale["content"] is None
            assert stale["standing"] == f"knowledge:{current.id}@{current.version}:STALE:test_gone"
            with store.transaction():
                # 本机时钟回拨：高水位在"现在"之后，状态 ROLLBACK、代次加一
                store.connection.execute(
                    "UPDATE assurance_environment_state SET clock_state='ROLLBACK', "
                    "clock_generation=clock_generation+1, wall_high_ms=MAX(wall_high_ms, ?), "
                    "row_version=row_version+1 WHERE singleton=1", (int(store.now * 1000) + 10 ** 9,))
            rolled = knowledge_tools.read_knowledge_tool(store, mission_id, "knowledge_read",
                                                         {"id": current.id}, handover=handover)
            assert rolled["content"] is None and rolled["standing"] == "TIME_DISCONTINUITY"
            # 读知识签的证书是使用回执：按事件重建这件事仍一致（推后第 2 批 Q2 裁决第 3 件）
            from agent_orchestrator.observability.business_replay import CONSISTENT, verify_mission

            replay = verify_mission(store, mission_id)
            assert replay["status"] == CONSISTENT, replay["silent_changes"]

    asyncio.run(case())


# ------------------------------------- 签不出时开规划轮的三条路同样收（裁决 2026-10-07 建议 1）
def test_a_planning_round_that_cannot_be_certified_waits_instead_of_stopping(tmp_path, monkeypatch):
    """要交给规划器的证据此刻签不出 PLAN 证书（例如时钟回拨期间）：普通开轮、服务恢复、等待唤醒
    三条路一样处理——这一轮不开、记进度、不算一轮故障；能签了照常开，任务完成。

    剧本里返工那一轮由"服务恢复"开（修复请求）。连续拒绝次数超过一轮故障的上限，且把故障的最短
    时长调成 0：若这条路按一轮故障处理，任务会被按名停掉。"""
    import agent_orchestrator.orchestrator.assurance_point_use as point_use
    from agent_orchestrator.orchestrator import failure_classes

    monkeypatch.setattr(failure_classes, "ROUND_FAULT_MIN_SECONDS", 0.0)
    original = point_use.certify_point_use_locked
    refused = {"n": 0}

    def certify(commit: Any, **kwargs: Any) -> Any:
        if (kwargs["purpose"] == "PLAN" and kwargs.get("record", True) and kwargs["claims"]
                and refused["n"] < failure_classes.NON_MODEL_FAILURE_CAP + 4):
            refused["n"] += 1
            use = point_use.PointUse("PLAN", kwargs["mission_id"], kwargs["consumer_kind"],
                                     kwargs["consumer_id"], tuple(kwargs["claims"]))
            return point_use._refused(use, "TIME_DISCONTINUITY")
        return original(commit, **kwargs)

    monkeypatch.setattr(point_use, "certify_point_use_locked", certify)
    state: dict[str, Any] = {"methods": []}

    async def case():
        async with product_world(tmp_path / "root", _scenario_provider(state),
                                 allowed_tools=DEFAULT_TOOLS + KNOWLEDGE_TOOLS) as world:
            mission_id = await _run(world, "point-use-plan-waits")
            assert refused["n"] == failure_classes.NON_MODEL_FAILURE_CAP + 4
            faults = [event for event in world.store.iter_events(mission_id) if event.type == "MissionRoundFault"]
            assert faults == [], [event.payload for event in faults]

    asyncio.run(case())


def test_a_planning_round_that_can_never_be_certified_stops_by_name_after_the_wait_limit(tmp_path, monkeypatch):
    """opt.169 发版前评估建议 1：签不出 PLAN 证书的原因一直不消失（例如时钟一直不可信），规划轮不能
    无声地一直等。等待按与"规划池不可用"同一个上限（``profile_wait_seconds``，存储时钟）计时，到点按名
    停下，明细写清哪条路、哪几条拒绝原因、等了多久。

    **改坏检验**：去掉到点停下 → 任务一直挂着、等不到终态 → 变红。"""
    import dataclasses

    import agent_orchestrator.orchestrator.assurance_point_use as point_use

    original = point_use.certify_point_use_locked

    def certify(commit: Any, **kwargs: Any) -> Any:
        if kwargs["purpose"] == "PLAN" and kwargs.get("record", True) and kwargs["claims"]:
            use = point_use.PointUse("PLAN", kwargs["mission_id"], kwargs["consumer_kind"],
                                     kwargs["consumer_id"], tuple(kwargs["claims"]))
            return point_use._refused(use, "TIME_DISCONTINUITY")
        return original(commit, **kwargs)

    monkeypatch.setattr(point_use, "certify_point_use_locked", certify)
    state: dict[str, Any] = {"methods": []}

    async def case():
        async with product_world(tmp_path / "root", _scenario_provider(state),
                                 allowed_tools=DEFAULT_TOOLS + KNOWLEDGE_TOOLS) as world:
            # 上限要长于重试间隔（约 0.55 秒），否则每次都算"断开"重新计时
            world.loop._config = dataclasses.replace(world.loop._config, profile_wait_seconds=2.0)
            mission_id = world.create({"goal": "写两份笔记", "idempotency_key": "point-use-plan-never",
                                       "success_criteria": ["file:notes/a.md", "file:notes/b.md"]})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=60)
            assert str(mission.status.value) == "FAILED", (mission.status, mission.final_report)
            report = mission.final_report
            assert report["stop_reason"] == "runtime_unavailable", report
            detail = json.dumps(report, ensure_ascii=False)
            assert "planning_evidence_uncertifiable" in detail and "TIME_DISCONTINUITY" in detail, report
            assert not [e for e in world.store.iter_events(mission_id) if e.type == "MissionRoundFault"]

    asyncio.run(case())


# ------------------------------------- 读过知识后重启（opt.169 缺陷；推后第 2 批 Q2 裁决第 3 件）
def test_a_mission_whose_worker_read_knowledge_survives_a_restart(tmp_path, monkeypatch):
    """执行者运行中用 ``knowledge_read`` 读过一条已验证知识（签了一张没有领域事件的 CONTEXT 证书），
    交完结果时服务断了。重开后恢复第 3 步按事件重建这件事仍一致：不进隔离集合，任务照常完成。

    **改坏检验**：去掉重放清单里证书表的 ``silent_ok`` → 重建判"静默改动"、任务被隔离 → 红。"""
    from agent_orchestrator.storage.store import InjectedCrash
    from test_repair_staleness import _find, _tool_results

    state: dict[str, Any] = {"methods": []}
    read: list[dict[str, Any]] = []
    planner, worker, reviewer = scenario(state)
    holder: dict[str, Any] = {}

    def reading_worker(request: Any) -> Any:
        package = package_of(request)
        if list(package.get("task_contract", {}).get("outputs") or []) != ["notes/b.md"]:
            return worker(request)
        results = _tool_results(request)
        write = ("workspace_write_file", {"path": "notes/b.md", "content": "# 第二份\n\n- 接着第一份\n"})
        if not results:
            return ("knowledge_list", {})
        if len(results) == 1:
            verified = [item for item in _find(json.loads(results[0]), "items") or ()
                        if item.get("layer") == "verified"]
            return ("knowledge_read", {"id": verified[0]["id"]}) if verified and not read else write
        if len(results) == 2 and not read:
            read.append(json.loads(results[1])["value"])
            # 读完这一次：这次尝试交完结果时服务断
            holder["world"].loop.arm_fault("after_result_submitted", kind="attempt")
            return write
        return worker(request)

    def provider() -> LayeredScriptedProvider:
        return LayeredScriptedProvider(planner=planner, worker=reading_worker, reviewer=reviewer)

    async def case():
        root = tmp_path / "root"
        async with product_world(root, provider(), allowed_tools=DEFAULT_TOOLS + KNOWLEDGE_TOOLS) as world:
            holder["world"] = world
            mission_id = world.create({"goal": "写两份笔记", "idempotency_key": "point-use-read-restart",
                                       "success_criteria": ["file:notes/a.md", "file:notes/b.md"]})["mission_id"]
            for _ in range(40):
                try:
                    await world.drain(timeout=10)
                except InjectedCrash:
                    break
                if "after_result_submitted:attempt" in world.store.fired:
                    break
            assert read and read[0]["content"], read
            assert "after_result_submitted:attempt" in world.store.fired
            assert [row for row in _certificates(world.store, mission_id, "CONTEXT")
                    if row["consumer_kind"] == "TOOL_CALL"]
            assert str(world.store.get_mission(mission_id).status.value) == "ACTIVE"
        async with product_world(root, provider(), allowed_tools=DEFAULT_TOOLS + KNOWLEDGE_TOOLS) as world:
            holder["world"] = world
            store = world.store
            mission = await world.run_until_settled(mission_id, rounds=40)
            assert not world.loop.recovery_isolated(mission_id)
            assert str(mission.status.value) == "COMPLETED", mission.final_report

    asyncio.run(case())
