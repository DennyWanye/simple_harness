# SPDX-License-Identifier: Apache-2.0
"""Assurance 原计划 1.1 审查意见 F01～F15 的决定性反例（计划包 ``findings-sdk-tests.json``）。

2026-10-05 对照原计划后补写。已有用例守着的意见不在这里重写，对应关系登记在
``acceptance_assets/assurance_findings.json``；这里只放此前没有用例的那几条：

* F03 证据标签：同一编号的两个版本、不同种类各有各的标签；伪造的标签、没给审阅员看过的标签都被拒。
* F04 检查的替代组：第一组没过、第二组过了 → 通过，只消费第二组的回执。
* F11 绕过接口直接改库：收尾行一出生就写"已定稿"、删掉 / 整行替换已定稿的收尾行、给没有审阅的钉子
  标"已绑定"——库自己拒绝。

2026-10-06 第 1 批补齐（V02 / 保证 C-31）补上此前缺的关键注入与断言：
* F01：带子目标的任务跑全程，组合审阅也在"每类一次"里；每个"切审阅"调用原样重送一遍，仍是同一次调用。
* F02：已结清的预留开不了新调用；同一回合换个采集身份认领被拒。
* F03：曝光批次同号异体被库拒，链不连续冷读拒。
* F08：换版本资料、改要求、确认完成映射三类真实写方各推一次纪元、旧证明被拒。
"""
from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

import pytest

from agent_orchestrator.assurance.checks import CheckResult, CriterionPolicy, Grade, evaluate_check_gate
from agent_orchestrator.assurance.codec import AssuranceError
from agent_orchestrator.assurance.evidence import (
    CatalogueEntry,
    build_catalogue,
    evidence_label,
    resolve_evidence_ids,
)
from agent_orchestrator.assurance.refs import AssuranceRef, Pin
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

HASH, OTHER_HASH = "a" * 64, "b" * 64


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _refused(call, code: str) -> None:
    with pytest.raises(AssuranceError) as raised:
        call()
    assert raised.value.code == code, raised.value.code


# --------------------------------------------------------------------------- F03
def test_labels_exact_disclosure():
    """**改坏检验**：标签只按编号算（AS-FM01）→ 两个版本得到同一个标签 → 变红。"""
    v1 = AssuranceRef("artifact", Pin("doc-1", 1, HASH))
    v2 = AssuranceRef("artifact", Pin("doc-1", 2, OTHER_HASH))
    as_source = AssuranceRef("source", Pin("doc-1", 1, HASH))
    labels = {evidence_label("review-a", ref) for ref in (v1, v2, as_source)}
    assert len(labels) == 3  # 同一编号：两个版本、另一种类，各有各的标签
    assert evidence_label("review-a", v1) == evidence_label("review-a", v1)  # 冷恢复后算出来还是它
    assert evidence_label("review-b", v1) != evidence_label("review-a", v1)  # 换一次审阅就是另一个标签

    catalogue = build_catalogue("review-a", (v1, v2, as_source, v1))
    assert [entry.ref for entry in catalogue].count(v1) == 1 and len(catalogue) == 3
    by_ref = {entry.ref: entry.label for entry in catalogue}
    shown = frozenset({by_ref[v1]})
    # 审阅员引用标签 → 找回的是当时那个版本的精确引用
    assert resolve_evidence_ids("review-a", (by_ref[v1],), catalogue, shown) == (v1,)
    # 目录里有、但没给审阅员看过的那个版本：不能引用
    _refused(lambda: resolve_evidence_ids("review-a", (by_ref[v2],), catalogue, shown), "UNEXPOSED_EVIDENCE")
    # 目录之外的标签：不能引用
    _refused(lambda: resolve_evidence_ids("review-a", ("ev-" + "0" * 64,), catalogue, shown), "UNEXPOSED_EVIDENCE")
    # 把第 1 版的标签挂到第 2 版上（伪造目录）：按名拒绝
    forged = (CatalogueEntry(by_ref[v1], v2),)
    _refused(lambda: resolve_evidence_ids("review-a", (by_ref[v1],), forged, shown), "CATALOGUE_LABEL_BINDING")
    # 别的审阅的目录拿到这次审阅来用：标签对不上
    _refused(lambda: resolve_evidence_ids("review-b", (by_ref[v1],), catalogue, shown), "CATALOGUE_LABEL_BINDING")
    _refused(lambda: resolve_evidence_ids("review-a", (by_ref[v1], by_ref[v1]), catalogue, shown),
             "DUPLICATE_EVIDENCE_LABEL")


# --------------------------------------------------------------------------- F04
def test_checked_policy_truth_table():
    """替代组这一格（其余各格在 ``test_a_assurance.py`` 的 A02 / A04 / A05）。"""

    def spec(name: str) -> AssuranceRef:
        return AssuranceRef("check_spec", Pin(name, 1, HASH))

    def receipt(name: str) -> AssuranceRef:
        return AssuranceRef("local_check_receipt", Pin("rcpt-" + name, 0, HASH))

    def result(name: str, grade: Grade = Grade.PASS, state: str = "SUCCEEDED") -> CheckResult:
        return CheckResult(spec(name), receipt(name), state, grade, True)

    policy = CriterionPolicy("c", "CHECKED", ((spec("fmt"), spec("rule")), (spec("alt"),)))
    # 第一组有一项没过、第二组过了：通过，只消费第二组的回执
    gate = evaluate_check_gate(policy, {spec("fmt"): result("fmt", Grade.FAIL), spec("rule"): result("rule"),
                                        spec("alt"): result("alt")})
    assert (gate.grade, gate.reason, gate.consumed) == (Grade.PASS, "CHECK_GROUP_SATISFIED", (receipt("alt"),))
    # 第一组缺一张回执（不知道）、第二组过了：同样通过
    gate = evaluate_check_gate(policy, {spec("fmt"): result("fmt"), spec("alt"): result("alt")})
    assert (gate.grade, gate.consumed) == (Grade.PASS, (receipt("alt"),))
    # 两组都过：按冻结的顺序取第一组
    gate = evaluate_check_gate(policy, {spec("fmt"): result("fmt"), spec("rule"): result("rule"),
                                        spec("alt"): result("alt")})
    assert set(gate.consumed) == {receipt("fmt"), receipt("rule")}
    # 第一组没过、第二组跑出错：不知道，不是通过也不是没过
    gate = evaluate_check_gate(policy, {spec("fmt"): result("fmt", Grade.FAIL), spec("rule"): result("rule"),
                                        spec("alt"): result("alt", state="ERROR")})
    assert (gate.grade, gate.reason) == (Grade.UNKNOWN, "CHECK_EVIDENCE_INCOMPLETE")
    # 两组都没过：没过
    gate = evaluate_check_gate(policy, {spec("fmt"): result("fmt", Grade.FAIL), spec("rule"): result("rule"),
                                        spec("alt"): result("alt", Grade.FAIL)})
    assert (gate.grade, gate.reason) == (Grade.FAIL, "CHECK_GROUPS_FAILED")


# --------------------------------------------------------------------------- F11
def _closeout_row(connection: sqlite3.Connection, mission_id: str) -> dict[str, Any] | None:
    cursor = connection.execute("SELECT * FROM assurance_closeouts WHERE mission_id=?", (mission_id,))
    row = cursor.fetchone()
    return None if row is None else dict(zip([c[0] for c in cursor.description], tuple(row)))


def _insert_closeout(connection: sqlite3.Connection, row: dict[str, Any], verb: str = "INSERT") -> None:
    connection.execute(f"{verb} INTO assurance_closeouts ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                       tuple(row.values()))


def test_sql_bypass_initial_and_delete(tmp_path, monkeypatch):
    """**改坏检验**：放开"收尾行必须从未就绪第 1 版开始"（AS-FM09）、放开删除（AS-FM10A）、放开整行替换
    （AS-FM10B）、放开"已绑定的钉子要有同任务的审阅"（AS-FM11）→ 各自对应的那一句变红。"""
    from agent_orchestrator.orchestrator.resolution_commits import ResolutionCommitsMixin

    seen: dict[str, Any] = {"world": None, "born_finalized": None}
    original = ResolutionCommitsMixin.commit_goal_resolution

    def commit_then_forge(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        out = original(self, *args, **kwargs)
        world = seen["world"]
        connection = world.store.connection
        resolved = connection.execute(
            "SELECT resolution_id, mission_id FROM goal_resolutions ORDER BY rowid DESC LIMIT 1").fetchone()
        if seen["born_finalized"] is None and _closeout_row(connection, resolved["mission_id"]) is None:
            # 根结论刚写下、收尾行还没出生：直接写一行"已定稿"
            receipt = connection.execute("SELECT commit_id FROM commit_receipts LIMIT 1").fetchone()[0]
            forged = {"mission_id": resolved["mission_id"], "resolution_id": resolved["resolution_id"],
                      "state": "FINALIZED", "row_version": 1, "check_body_hash": HASH, "check_body_json": "{}",
                      "last_receipt_id": receipt, "updated_at_ms": 0}
            try:
                _insert_closeout(connection, forged)
                seen["born_finalized"] = "written"
            except sqlite3.IntegrityError as error:
                seen["born_finalized"] = str(error)
        return out

    monkeypatch.setattr(ResolutionCommitsMixin, "commit_goal_resolution", commit_then_forge)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            seen["world"] = world
            ids = [world.create({"goal": f"写一份笔记 {name}", "idempotency_key": "findings-f11-" + name,
                                 "success_criteria": [f"file:notes/{name}.md"]})["mission_id"] for name in "ab"]
            for mission_id in ids:
                for _ in range(30):
                    try:
                        await world.drain(timeout=20)
                    except AssertionError:
                        pass
                    if str(world.store.get_mission(mission_id).status.value) in {"COMPLETED", "FAILED", "CANCELLED"}:
                        break
            assert seen["born_finalized"] == "closeout must begin NOT_READY v1"
            mine, other = ids
            connection = world.store.connection
            assert [str(world.store.get_mission(m).status.value) for m in ids] == ["COMPLETED", "COMPLETED"]
            row = _closeout_row(connection, mine)
            assert row is not None and row["state"] == "FINALIZED"

            with pytest.raises(sqlite3.IntegrityError, match="retain closeout history"):
                connection.execute("DELETE FROM assurance_closeouts WHERE mission_id=?", (mine,))
            reborn = dict(row, state="NOT_READY", row_version=1)
            with pytest.raises(sqlite3.IntegrityError, match="duplicate identity; use original receipt"):
                _insert_closeout(connection, reborn, "INSERT OR REPLACE")
            with pytest.raises(sqlite3.IntegrityError, match="invalid closeout update"):
                connection.execute("UPDATE assurance_closeouts SET state='READY', row_version=row_version+1 "
                                   "WHERE mission_id=?", (mine,))
            assert _closeout_row(connection, mine) == row

            # 钉子：新钉一份（合法的出生状态），审阅键是另一个任务的 → 标"已绑定"被拒
            foreign_key = connection.execute(
                "SELECT review_key FROM assurance_review_bindings WHERE mission_id=? LIMIT 1", (other,)).fetchone()[0]
            receipt = row["last_receipt_id"]
            connection.execute("SAVEPOINT forged_pin")
            try:
                for name, review_key in (("ghost", "no-such-review"), ("foreign", foreign_key)):
                    connection.execute(
                        "INSERT INTO assurance_blob_pins (pin_id, mission_id, review_key, blob_hash, object_ref_json, "
                        "state, row_version, created_at_ms, released_at_ms, source_receipt_id, last_receipt_id) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        ("pin-" + name, mine, review_key, OTHER_HASH, "{}", "PREPARING", 1, 0, None, receipt, receipt))
                    with pytest.raises(sqlite3.IntegrityError, match="BOUND pin lacks same-mission review"):
                        connection.execute("UPDATE assurance_blob_pins SET state='BOUND', row_version=row_version+1 "
                                           "WHERE pin_id=?", ("pin-" + name,))
            finally:
                connection.execute("ROLLBACK TO forged_pin")
                connection.execute("RELEASE forged_pin")

    asyncio.run(case())


# --------------------------------------------------------------------------- F08
def test_all_writers_barrier_and_expiry(tmp_path):
    """登记一份资料（产品写方）→ 同一事务里保证通道纪元加一、记一条"证据变了"，按旧纪元拿的证明在
    最后一道锁里被拒；清单上每张依据表在库里都有增、改、删三道屏障。观察这一类写方在
    ``product_world/test_desktop_preconditions.py`` 的真实任务里核；完成映射、时钟回拨、到期在
    ``test_v_assurance.py`` / ``test_c_assurance.py``。

    **改坏检验**：登记资料不推纪元（AS-FM07）→ 变红。"""
    from agent_orchestrator.storage.assurance_reads import read_epochs_locked, require_epochs_locked
    from agent_orchestrator.storage.assurance_source_inventory import GLOBAL_TABLES, MISSION_TABLES

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), auto=False) as world:
            mission_id = world.create({"goal": "按规格写一份说明", "success_criteria": ["file:notes/a.md"],
                                       "idempotency_key": "findings-f08"})["mission_id"]
            store = world.store

            def epochs():
                with store.read_view() as connection:
                    return read_epochs_locked(connection, mission_id)

            def changed():
                return [e for e in store.list_events(mission_id) if e.type == "AssuranceEvidenceChanged"]

            captured, before = epochs(), len(changed())
            world.control.register_source({"mission_id": mission_id, "path": "sources/spec.md",
                                           "content": "# 规格\n", "kind": "markdown",
                                           "idempotency_key": "findings-f08-source"})
            current = epochs()
            assert current.mission > captured.mission and current.environment == captured.environment
            events = changed()[before:]
            assert events and "sources" in {e.payload["source_table"] for e in events}
            assert events[-1].payload["epoch"] == current.mission
            now_ms = int(store.now * 1000) + 1
            with store.read_view() as connection:
                with pytest.raises(AssuranceError) as raised:
                    require_epochs_locked(connection, mission_id, captured, now_ms=now_ms)
                assert raised.value.code == "RECHECK_REQUIRED"
                require_epochs_locked(connection, mission_id, current, now_ms=now_ms)

            triggers = {row[0] for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'assurance_source_%'")}
            missing = sorted(f"assurance_source_{table}_{verb}" for table in (*MISSION_TABLES, *GLOBAL_TABLES)
                             for verb in ("insert", "update", "delete")
                             if f"assurance_source_{table}_{verb}" not in triggers)
            assert missing == []

    asyncio.run(case())


# --------------------------------------------------------------------------- F02
def test_internal_ref_resolvers(tmp_path):
    """精确引用的读取器：对的那一条读得回来；种类不认识、版本号不对、正文哈希不对、别的任务的、
    别的租户的、不是系统写的事件——各按各的名字拒绝。已结清的预留不能拿去开新调用在
    ``test_review_turn_retry_e2e.py``（每次调用各有自己的预留）。"""
    from agent_orchestrator.assurance.codec import decode, fingerprint
    from agent_orchestrator.assurance.event_kinds import EVENT_REF_KINDS
    from agent_orchestrator.storage.assurance_reads import AssuranceReader
    from agent_orchestrator.testing.product_world import TENANT

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(), auto=False) as world:
            mine, other = (world.create({"goal": f"写一份笔记 {name}", "idempotency_key": "findings-f02-" + name,
                                         "success_criteria": [f"file:notes/{name}.md"]})["mission_id"] for name in "ab")
            store = world.store
            reader = AssuranceReader(store, tenant_id=TENANT, mission_id=mine)

            def requirements(mission_id: str) -> AssuranceRef:
                row = store.connection.execute(
                    "SELECT revision_id, revision, revision_json FROM requirements_revisions WHERE mission_id=?",
                    (mission_id,)).fetchone()
                return AssuranceRef("requirements", Pin(row[0], int(row[1]), fingerprint(decode(row[2]))))

            good = requirements(mine)
            assert decode(reader.read_exact_metadata(good).body_json)["mission_id"] == mine
            _refused(lambda: reader.read_exact_metadata(
                AssuranceRef("requirements", Pin(good.pin.id, good.pin.revision, OTHER_HASH))), "REF_BODY_CONFLICT")
            _refused(lambda: reader.read_exact_metadata(
                AssuranceRef("requirements", Pin(good.pin.id, good.pin.revision + 1, good.pin.content_hash))),
                "SOURCE_UNAVAILABLE")
            # 别的任务的要求书：这个任务的读取器读不到
            _refused(lambda: reader.read_exact_metadata(requirements(other)), "SOURCE_UNAVAILABLE")
            # 不可变的种类带了版本号
            _refused(lambda: reader.read_exact_metadata(AssuranceRef("review", Pin("rec-x", 1, HASH))),
                     "REF_REVISION_MISMATCH")
            # 读取器不认识的种类
            _refused(lambda: reader.read_exact_metadata(AssuranceRef("tool_receipt", Pin("t-1", 0, HASH))),
                     "REF_KIND_UNSUPPORTED")
            # 别的租户
            foreign = AssuranceReader(store, tenant_id="another-tenant", mission_id=mine)
            _refused(lambda: foreign.read_exact_metadata(good), "REF_SCOPE_MISMATCH")

            # 事件类引用：别的任务的事件、不是这一类的事件
            kind = sorted(EVENT_REF_KINDS)[0]
            created = {m: next(e for e in store.list_events(m) if e.type == "MissionCreated") for m in (mine, other)}
            _refused(lambda: reader.read_exact_metadata(
                AssuranceRef(kind, Pin(created[other].id, 0, fingerprint(created[other].to_json())))),
                "REF_SCOPE_MISMATCH")
            _refused(lambda: reader.read_exact_metadata(
                AssuranceRef(kind, Pin(created[mine].id, 0, fingerprint(created[mine].to_json())))),
                "REF_ISSUER_MISMATCH")
            # 回执：别的任务的回执
            row = store.connection.execute(
                "SELECT commit_id, receipt_json FROM commit_receipts WHERE json_extract(receipt_json,'$.mission_id')=?"
                " LIMIT 1", (other,)).fetchone()
            assert row is not None
            _refused(lambda: reader.read_exact_metadata(
                AssuranceRef("commit_receipt", Pin(row[0], 0, fingerprint(decode(row[1]))))), "REF_SCOPE_MISMATCH")

    asyncio.run(case())


# --------------------------------------------------------------------------- F06
def test_direct_judge_waits_closeout(tmp_path, monkeypatch):
    """绕过主循环直接调判定：根结论还没有 → 拒绝，什么都不写；判定过了、收尾还没定稿 → 再调一次
    任务仍在进行，没有"任务完成"事件，预留一行不动；放行后只由唯一完成写方完成一次。
    全包只有一个地方写"完成"由 ``test_terminal_unknown_release.py`` 守（改坏 FIN-01）。"""
    import agent_orchestrator.orchestrator.assurance_assembly as assembly
    import agent_orchestrator.orchestrator.assurance_final_writer as final_writer
    from agent_orchestrator.orchestrator.commit_service import CommitRejected

    real = final_writer.finalize_assured_mission
    held = {"on": True}

    def finalize(*args: Any, **kwargs: Any) -> Any:
        if held["on"]:
            raise AssuranceError("RECHECK_REQUIRED", "held by the test")
        return real(*args, **kwargs)

    monkeypatch.setattr(final_writer, "finalize_assured_mission", finalize)
    monkeypatch.setattr(assembly, "finalize_assured_mission", finalize)

    async def case():
        provider = LayeredScriptedProvider()
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写一份笔记", "idempotency_key": "findings-f06",
                                       "success_criteria": ["file:notes.md"]})["mission_id"]
            store, commit = world.store, world.loop.commit

            def events(kind: str) -> list[Any]:
                return [e for e in store.list_events(mission_id) if e.type == kind]

            def reservations() -> list[tuple[Any, ...]]:
                return [tuple(row) for row in store.connection.execute(
                    "SELECT subject_id, state FROM budget_reservations WHERE mission_id=? ORDER BY subject_id",
                    (mission_id,))]

            for _ in range(20):
                await world.drain(timeout=5)
                if provider.entered.is_set():
                    break
            assert provider.entered.is_set()
            # 执行者还在跑：直接判定被拒，任务状态、预留都不动
            before = (reservations(), store.get_mission(mission_id).version)
            with pytest.raises(CommitRejected):
                commit.judge_mission(mission_id, judgments=[{"criterion": "file:notes.md", "met": True}], summary="x")
            assert (reservations(), store.get_mission(mission_id).version) == before
            assert not events("MissionSuccessJudged") and not events("MissionCompleted")

            provider.release.set()
            for _ in range(30):
                try:
                    await world.drain(timeout=20)
                except AssertionError:
                    pass
                if events("MissionSuccessJudged"):
                    break
            [judged] = events("MissionSuccessJudged")
            assert str(store.get_mission(mission_id).status.value) == "ACTIVE" and commit.assured_closeout_pending(mission_id)
            # 判定过了、收尾被挡着：再直接判一次，仍不完成
            before = reservations()
            again = commit.judge_mission(mission_id, judgments=judged.payload["judgments"], summary="again")
            assert str(again.status.value) == "ACTIVE" and reservations() == before
            assert not events("MissionCompleted")

            held["on"] = False
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert str(mission.status.value) == "COMPLETED" and len(events("MissionCompleted")) == 1

    asyncio.run(case())


# --------------------------------------------------------------------------- F01
def test_no_parallel_scoped_review(tmp_path):
    """一个带发布的任务把各类审阅都走一遍（做法、内容、申请单、发布结果、终审）：每个审查包只有一次
    审阅、最多一条正式记录，每次调用恰好一笔预留；任务结束后主循环再空转几轮，一样都不多。
    内容审阅与终审在随机序列里的同一组不变量见 ``test_stateful_assurance.py``。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "operation_completion"))
    from publish_world import publishing

    async def case():
        async with publishing(tmp_path) as world:
            world.approve(await world.until_approval())
            mission = await world.world.run_until_settled(world.mission_id, rounds=20)
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            connection, mission_id = world.store.connection, world.mission_id

            def snapshot() -> dict[str, Any]:
                bindings = connection.execute(
                    "SELECT review_key, package_id FROM assurance_review_bindings WHERE mission_id=?",
                    (mission_id,)).fetchall()
                invocations = connection.execute(
                    "SELECT review_key, ordinal FROM assurance_review_invocations WHERE mission_id=?",
                    (mission_id,)).fetchall()
                return {
                    "purposes": sorted(str(row[0]).split(":")[0] for row in bindings),
                    "packages": sorted(row[1] for row in bindings),
                    "invocations": sorted((row[0], row[1]) for row in invocations),
                    "official": dict(connection.execute(
                        "SELECT package_id, count(*) FROM review_records WHERE mission_id=? AND official=1 "
                        "GROUP BY package_id", (mission_id,)).fetchall()),
                    "reservations": dict(connection.execute(
                        "SELECT subject_id, count(*) FROM budget_reservations WHERE mission_id=? "
                        "AND subject_id LIKE ? GROUP BY subject_id", (mission_id, mission_id + ":assurance:%")).fetchall()),
                }

            done = snapshot()
            assert done["purposes"] == ["assurance-action-proposal", "assurance-content", "assurance-method-plan",
                                        "assurance-mission-final", "assurance-operation-outcome"]
            assert len(done["packages"]) == len(set(done["packages"])) == len(done["purposes"])
            assert len(done["purposes"]) == len(set(done["purposes"]))  # 这个任务里每类审阅只发生一次
            assert all(count == 1 for count in done["official"].values())
            assert set(done["official"]) <= set(done["packages"])
            assert [ordinal for _, ordinal in done["invocations"]] == [1] * len(done["purposes"])
            assert done["reservations"] == {f"{mission_id}:assurance:{key}:{ordinal}": 1
                                            for key, ordinal in done["invocations"]}
            for _ in range(3):
                await world.world.drain()
            assert snapshot() == done

    asyncio.run(case())


# --------------------------------------------------------------------------- F01（补：组合审阅 + 同通知重送）
def test_every_purpose_including_composition_is_cut_once_even_when_its_notice_is_resent(tmp_path, monkeypatch):
    """F01 具名反例"同一通知派两次审阅"：把每个用途"切审阅"的那一步（通知到达时的 ensure）原样再送一遍，
    带子目标的任务（多出**组合审阅**这一类）从头跑到完成——每个审查包只有一次调用、一笔预留、至多一条
    正式记录；重送回来的是同一次调用。``test_no_parallel_scoped_review`` 只覆盖带发布任务的五类，没有组合
    审阅，也没有重送注入。

    **改坏检验**：``prepare_purpose_review`` 不认已有调用（跳过 ``existing`` 分支）→ 重送造出第二次调用 /
    第二笔预留，或按名冲突 → 变红。"""
    import sys
    from pathlib import Path

    from agent_orchestrator.orchestrator.assurance_review_runtime import AssuranceReviewRuntime

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "product_world"))
    from test_sub_goal import planner as sub_goal_planner

    original = AssuranceReviewRuntime._ensure_purpose
    resent: list[tuple[str, bool]] = []

    def ensure_twice(self, mission, **kwargs):  # type: ignore[no-untyped-def]
        first = original(self, mission, **kwargs)
        second = original(self, mission, **kwargs)  # 同一通知重送：同样的包、同样的命令号
        resent.append((kwargs["name"], first.to_json() == second.to_json()))
        return second

    monkeypatch.setattr(AssuranceReviewRuntime, "_ensure_purpose", ensure_twice)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(planner=sub_goal_planner)) as world:
            mission_id = world.create({"goal": "写两份笔记", "idempotency_key": "findings-f01-composition",
                                       "success_criteria": ["file:notes/a.md", "file:NOTES.md"]})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert str(mission.status.value) == "COMPLETED", (mission.status, mission.final_report)
            connection = world.store.connection

            def snapshot() -> dict[str, Any]:
                bindings = connection.execute(
                    "SELECT review_key, package_id FROM assurance_review_bindings WHERE mission_id=?",
                    (mission_id,)).fetchall()
                invocations = connection.execute(
                    "SELECT review_key, ordinal FROM assurance_review_invocations WHERE mission_id=?",
                    (mission_id,)).fetchall()
                return {
                    "keys": sorted(row[0] for row in bindings),
                    "packages": sorted(row[1] for row in bindings),
                    "invocations": sorted((row[0], row[1]) for row in invocations),
                    "official": dict(connection.execute(
                        "SELECT package_id, count(*) FROM review_records WHERE mission_id=? AND official=1 "
                        "GROUP BY package_id", (mission_id,)).fetchall()),
                    "reservations": dict(connection.execute(
                        "SELECT subject_id, count(*) FROM budget_reservations WHERE mission_id=? "
                        "AND subject_id LIKE ? GROUP BY subject_id", (mission_id, mission_id + ":assurance:%")).fetchall()),
                }

            done = snapshot()
            purposes = {key.split(":")[0] for key in done["keys"]}
            assert {"assurance-composition", "assurance-content", "assurance-method-plan",
                    "assurance-mission-final"} <= purposes, purposes
            assert len(done["keys"]) == len(set(done["keys"])) == len(set(done["packages"]))  # 一包一审阅
            assert [ordinal for _, ordinal in done["invocations"]] == [1] * len(done["keys"])  # 每审阅一次调用
            assert sorted(key for key, _ in done["invocations"]) == done["keys"]
            assert all(count == 1 for count in done["official"].values()) and set(done["official"]) <= set(done["packages"])
            assert done["reservations"] == {f"{mission_id}:assurance:{key}:{ordinal}": 1
                                            for key, ordinal in done["invocations"]}  # 每次调用恰好一笔预留
            # 重送确实发生在每一类上，且每次回来的都是同一次调用
            assert {name for name, _ in resent} >= {"assurance-composition-review", "assurance-method-plan-review",
                                                     "assurance-mission-final-review"}, resent
            assert all(same for _, same in resent), [name for name, same in resent if not same]
            for _ in range(3):
                await world.drain()
            assert snapshot() == done

    asyncio.run(case())


# --------------------------------------------------------------------------- F02（补：已结清预留 + 错采集身份）
def test_a_settled_reservation_cannot_fund_a_new_review_call_and_a_foreign_collector_is_refused(tmp_path):
    """F02 具名反例"已结算 reserve 被用于新调用"与"错 collector"。做法审阅已经结清、执行者还在跑（任务
    ACTIVE）：拿这个已结清的审阅调用再过一次派发闸门 → 按名拒（``REVIEW_RESERVATION_NOT_ACTIVE``），预留
    行仍是 SETTLED、费用照记；把它的回合交给另一个采集身份去读（创建键 / 输入哈希与产出这回合的代理不符）
    → 按名拒（``REVIEW_RUNTIME_SOURCE_MISMATCH``）。``test_review_turn_retry_e2e.py`` 只证明每次调用各有预留，
    不尝试复用。

    **改坏检验**：派发闸门不查预留仍是 RESERVED（去掉 ``REVIEW_RESERVATION_NOT_ACTIVE`` 那一句）→ 变红。"""
    from dataclasses import replace

    from agent_orchestrator.orchestrator.assurance_review_transport import require_review_handoff
    from agent_orchestrator.runtime.assurance_turn_sources import read_actual_review_turn

    async def case():
        provider = LayeredScriptedProvider()
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写一份笔记", "idempotency_key": "findings-f02-reserve",
                                       "success_criteria": ["file:notes.md"]})["mission_id"]
            store, commit = world.store, world.loop.commit
            for _ in range(20):
                await world.drain(timeout=5)
                if provider.entered.is_set():
                    break
            assert provider.entered.is_set()
            assert str(store.get_mission(mission_id).status.value) == "ACTIVE"
            reviews = [store.get_intent(row[0]) for row in store.connection.execute(
                "SELECT intent_id FROM dispatch_intents WHERE mission_id=? ORDER BY created_at", (mission_id,))]
            reviews = [i for i in reviews if i.config.get("assurance_protocol") == "assurance-exec-v1.1"]
            settled = [i for i in reviews if (commit.ledger.reservation(i.subject_id) or {}).get("state") == "SETTLED"]
            assert settled, [(i.kind, i.state) for i in reviews]  # 做法审阅已结清
            intent = settled[0]
            before = dict(commit.ledger.reservation(intent.subject_id))
            # 已结清的预留开不了新调用
            _refused(lambda: require_review_handoff(commit, intent), "REVIEW_RESERVATION_NOT_ACTIVE")
            after = dict(commit.ledger.reservation(intent.subject_id))
            assert after == before and after["state"] == "SETTLED"  # 旧费用照入，不动
            assert not [row for row in store.connection.execute(
                "SELECT 1 FROM assurance_review_invocations WHERE mission_id=? AND ordinal>1", (mission_id,))]
            # 错采集身份：同一回合，换个创建键 / 输入哈希来认领
            bridge = world.loop.bridge_for(intent)
            assert intent.agent_id and intent.expected_turn_id
            read_actual_review_turn(bridge, intent)  # 本来的身份读得回来
            _refused(lambda: read_actual_review_turn(bridge, replace(intent, creation_key=intent.creation_key + ":other")),
                     "REVIEW_RUNTIME_SOURCE_MISMATCH")
            _refused(lambda: read_actual_review_turn(bridge, replace(intent, input_hash=OTHER_HASH)),
                     "REVIEW_RUNTIME_SOURCE_MISMATCH")
            _refused(lambda: read_actual_review_turn(bridge, replace(intent, expected_turn_id="turn-of-nobody")),
                     "REVIEW_TURN_UNAVAILABLE")

    asyncio.run(case())


# --------------------------------------------------------------------------- F03（补：追加批次冲突）
def test_an_appended_disclosure_batch_that_conflicts_is_refused(tmp_path):
    """F03 具名反例"冲突追加"：同一审阅的曝光批次按号追加。同号异体（同一批号、不同条目）被库按名拒
    （``IMMUTABLE_IDENTITY_CONFLICT``），链上还是原来的环；同体重放不新增；前序哈希对不上 / 批号重复 /
    链不从 0 起，冷读（纯函数）按名拒（``DISCLOSURE_CHAIN_INVALID``）。

    **改坏检验**：``AssuranceStore._insert`` 不比正文、同号就当重放 → 同号异体被吞 → 变红。"""
    from agent_orchestrator.assurance.codec import fingerprint
    from agent_orchestrator.assurance.disclosure import DisclosureBatch, disclosed_to_turn
    from agent_orchestrator.storage.assurance_store import AssuranceStore

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            mission_id = world.create({"goal": "写一份笔记", "idempotency_key": "findings-f03-append",
                                       "success_criteria": ["file:notes.md"]})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=20)
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            store, commit = world.store, world.loop.commit
            # 曝光批次挂在真实的审阅上（外键）：取这个任务内容审阅的审阅键，接在它已有的链后面追加
            [review_key] = [row[0] for row in store.connection.execute(
                "SELECT review_key FROM assurance_review_bindings WHERE mission_id=? AND review_key LIKE 'assurance-content:%'",
                (mission_id,))]
            side = AssuranceStore(store)
            existing = side.disclosure_chain(mission_id, review_key)
            start, previous = len(existing), (existing[-1].content_hash if existing else None)
            agent, turn = "reviewer-f03", AssuranceRef("agent_turn_receipt", Pin("turn-f03", 0, HASH))
            a, b, c = (AssuranceRef("artifact", Pin("doc-1", 1, HASH)), AssuranceRef("artifact", Pin("doc-2", 1, OTHER_HASH)),
                       AssuranceRef("source", Pin("spec.md", 1, HASH)))

            def entry(ref: AssuranceRef) -> CatalogueEntry:
                return CatalogueEntry(evidence_label(review_key, ref), ref)

            def batch(no: int, prior: str | None, refs: tuple[AssuranceRef, ...], *, delivered: bool = True,
                      suffix: str = "") -> DisclosureBatch:
                ordered = tuple(sorted((entry(ref) for ref in refs), key=lambda e: e.label))
                if not delivered:  # 只给冷读用，不落事件
                    ref = AssuranceRef("disclosure_receipt", Pin("ev-" + str(no) + suffix, 0, HASH))
                else:
                    payload = {"review_key": review_key, "batch_no": no, "previous_batch_hash": prior,
                               "delta_hash": fingerprint([e.to_json() for e in ordered]), "reviewer_agent_id": agent,
                               "turn_receipt_ref": turn.to_json(), "provider_input_hash": HASH, "visible_message_ids": ["m-1"]}
                    with store.transaction():
                        event = commit._emit("AssuranceEvidenceDisclosed", mission_id, key=f"{review_key}:{no}{suffix}",
                                             payload=payload)
                    ref = AssuranceRef("disclosure_receipt", Pin(event.id, 0, fingerprint(event.to_json())))
                return DisclosureBatch(mission_id, review_key, no, prior, ordered, agent, turn, HASH, ("m-1",), ref)

            first = batch(start, previous, (a,))
            assert side.record_disclosure(first) is True
            assert side.record_disclosure(first) is False  # 同体重放：不新增
            second = batch(start + 1, first.content_hash, (b,))
            assert side.record_disclosure(second) is True
            conflicting = batch(start + 1, first.content_hash, (c,), suffix=":alt")  # 同号异体
            _refused(lambda: side.record_disclosure(conflicting), "IMMUTABLE_IDENTITY_CONFLICT")
            chain = side.disclosure_chain(mission_id, review_key)
            assert [x.content_hash for x in chain] == [*(x.content_hash for x in existing), first.content_hash,
                                                       second.content_hash]

            identity = dict(mission_id=mission_id, review_key=review_key, agent_id=agent,
                            turn_receipt_ref=turn, provider_input_hash=HASH)
            assert disclosed_to_turn(chain, **identity) == {entry(a).label, entry(b).label}
            # 另一位代理 / 另一回合看不到为本回合曝光的标签
            assert disclosed_to_turn(chain, **{**identity, "agent_id": "reviewer-else"}) == frozenset()
            # 冷读：前序哈希对不上、批号重复、链不从 0 起，都按名拒
            _refused(lambda: disclosed_to_turn((*existing, first, batch(start + 1, OTHER_HASH, (b,), delivered=False)),
                                               **identity), "DISCLOSURE_CHAIN_INVALID")
            _refused(lambda: disclosed_to_turn((*existing, first, first), **identity), "DISCLOSURE_CHAIN_INVALID")
            _refused(lambda: disclosed_to_turn((second,), **identity), "DISCLOSURE_CHAIN_INVALID")

    asyncio.run(case())


# --------------------------------------------------------------------------- F08（补：每类真实写方）
def test_each_product_writer_class_moves_the_barrier_in_its_own_transaction(tmp_path):
    """F08"每类 writer / 新增反证同事务挡旧证书"：``test_all_writers_barrier_and_expiry`` 只用"登记资料"一类。
    这里把产品里人会走到的其余写方各做一次真实写入——换版本资料（``sources``，提出 → 批准）、改要求
    （``requirements_revisions`` / ``obligations``）、确认完成映射（``operation_completion_specs``）——每一次：
    任务纪元加一、同事务里留下"证据变了"事件且点名那张表、按旧纪元拿的证明在最后一道锁里被拒；环境纪元不动。

    **改坏检验**：删掉 ``requirements_revisions`` 的插入屏障触发器（AS-M09 一类）→ 改要求那一步变红。"""
    import sys
    from pathlib import Path

    from agent_orchestrator.storage.assurance_reads import read_epochs_locked, require_epochs_locked

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "operation_completion"))
    from publish_world import confirm_completion, publishing

    async def case():
        async with publishing(tmp_path, confirm=False, key="findings-f08-writers") as case:
            world, mission_id, store = case.world, case.mission_id, case.store
            await world.drain()  # 自动模式不代签带 action: 的任务：停在确认页
            assert case.status() == "CREATED"

            def epochs():
                with store.read_view() as connection:
                    return read_epochs_locked(connection, mission_id)

            def changed() -> list[Any]:
                return [e for e in store.list_events(mission_id) if e.type == "AssuranceEvidenceChanged"]

            def write_and_check(name: str, write: Any, *tables: str) -> None:
                captured, seen = epochs(), len(changed())
                write()
                current = epochs()
                assert current.mission > captured.mission, name
                assert current.environment == captured.environment, name
                fresh = changed()[seen:]
                touched = {e.payload["source_table"] for e in fresh}
                assert set(tables) <= touched, (name, touched)
                assert fresh[-1].payload["epoch"] == current.mission, name
                now_ms = int(store.now * 1000) + 1
                with store.read_view() as connection:
                    with pytest.raises(AssuranceError) as raised:
                        require_epochs_locked(connection, mission_id, captured, now_ms=now_ms)
                    assert raised.value.code == "RECHECK_REQUIRED", name
                    require_epochs_locked(connection, mission_id, current, now_ms=now_ms)

            def latest_ref() -> dict[str, Any]:
                from agent_orchestrator.storage.htn_store import HtnStore
                latest = HtnStore(store).latest_requirements_revision(mission_id)
                return {"id": str(latest.revision_id), "revision": int(latest.revision),
                        "content_hash": latest.content_hash()}

            registered: dict[str, Any] = {}

            def register() -> None:
                registered.update(world.control.register_source({
                    "mission_id": mission_id, "path": "sources/spec.md", "content": "# 规格 v1\n", "kind": "markdown",
                    "idempotency_key": "f08-src-1"}))

            def supersede() -> None:  # 换版本是两步：提出 → 人批准，写入发生在批准那一步
                proposal = world.control.supersede_source({
                    "mission_id": mission_id, "path": "sources/spec.md", "content": "# 规格 v2\n", "kind": "markdown",
                    "idempotency_key": "f08-src-2", "expected_version_hash": registered["version_hash"]})
                world.control.decide(proposal["request_id"], "approve", nonce="approve-f08-src-2")

            write_and_check("register_source", register, "sources")
            write_and_check("supersede_source", supersede, "sources")
            write_and_check("amend_requirements", lambda: world.control.amend_requirements({
                "mission_id": mission_id, "command_id": "f08-amend-1", "expected_requirements_ref": latest_ref(),
                "changes": [{"op": "add", "statement": "file:reports/appendix.md"}], "reason": "用户补充了要求",
                "source": {"kind": "MAIN_AGENT", "run_id": "run-1", "call_id": "call-1", "permission_mode": "auto"}}),
                "requirements_revisions", "obligations")
            write_and_check("approve_operation_completion_spec", lambda: confirm_completion(world, mission_id),
                            "operation_completion_specs")

    asyncio.run(case())
