# SPDX-License-Identifier: Apache-2.0
"""C group (consistency / concurrency / recovery / migration): plan cases C01–C06.

2026-10-03（HTN 补齐阶段 A′）迁到产品同形世界：任务经产品那一份部署组装建出（保证通道、执行图
建任务时绑定、原生执行池），模型回复是脚本。C01 在主循环真跑到内容验收时注入外界的写入故障
（数据库写失败、进程在验收事务里崩溃）；C02 / C04 在一个刚建好的任务上直接读写库，"另一个写入方"
是同一个库的另一条连接，推动任务事件头的是真实的产品事件（人在任务上留言）。C03–C05 跑迁到产品
同形世界的第 6/7/8 项接缝脚本并断言它们的报告。C06 迁移一个真实的旧库。不用 Host（C07 见
test_c07_host_api.py）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from _review_world import ReviewScript, reviewed_mission

from agent_orchestrator.assurance.certificates import UseCertificate
from agent_orchestrator.assurance.codec import AssuranceError
from agent_orchestrator.assurance.evidence import ReadItem
from agent_orchestrator.assurance.refs import AssuranceRef, Pin
from agent_orchestrator.contracts.models import Budget, Event, Mission, MissionStatus
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.storage import schema
from agent_orchestrator.storage.assurance_reads import read_epochs_locked
from agent_orchestrator.storage.assurance_store import AssuranceStore
from agent_orchestrator.storage.assurance_work import AssuranceWorkStore, WorkTarget
from agent_orchestrator.storage.store import (
    InjectedCrash,
    SchemaIncompatible,
    Store,
    StoreConflict,
    StoreError,
)
from agent_orchestrator.testing.fixtures import RoleScriptedProvider
from agent_orchestrator.testing.product_world import product_world

SDK_ROOT = Path(__file__).resolve().parents[4]
SEAMS = SDK_ROOT / "scripts/assurance_seams"
PRINCIPAL = Principal("exec-current-user")
HASH = "a" * 64
NOW = 1_700_000_000_000


def _count(store, sql, *params):
    return store.connection.execute(sql, params).fetchone()[0]


def _seam(name):
    completed = subprocess.run([sys.executable, str(SEAMS / name)], capture_output=True, text=True, timeout=900,
                               cwd=str(SDK_ROOT))
    assert completed.returncode == 0, (name, completed.stderr[-4000:])
    summary = json.loads(completed.stdout.strip().splitlines()[-1])
    assert summary["status"] == "PASS", summary
    return json.loads(Path(summary["evidence"]).read_text())


# --------------------------------------------------------------------------- C01
@pytest.mark.parametrize("fault", ("certificate", "event", "crash"))
def test_acceptance_atomic_faults(tmp_path, fault):
    """内容验收那一次写入里，许可证写失败 / "已验收"事件写失败 / 进程在验收事务里崩溃：验收、
    贡献、许可证、回执、事件一个都不留（现状是错误逃出本轮主循环，见迁移裁决 B4）；故障排除后照常
    验收一次，不再调审阅模型。"""

    provider = ReviewScript()

    async def body():
        async with reviewed_mission(tmp_path, provider) as case:
            store, mission_id = case.store, case.mission_id
            connection = store.connection

            def counts():
                return (_count(store, "SELECT COUNT(*) FROM acceptances WHERE mission_id=?", mission_id),
                        _count(store, "SELECT COUNT(*) FROM assurance_use_certificates WHERE mission_id=? "
                                      "AND consumer_kind='ACCEPTANCE'", mission_id),
                        _count(store, "SELECT COUNT(*) FROM operation_acceptance_scopes WHERE mission_id=?", mission_id),
                        len(case.events("AcceptanceCommitted")))

            if fault == "certificate":
                connection.execute("CREATE TRIGGER cut_c01 BEFORE INSERT ON assurance_use_certificates "
                                   "BEGIN SELECT RAISE(ABORT,'disk write failed'); END;")
                expected = sqlite3.IntegrityError
            elif fault == "event":
                connection.execute("CREATE TRIGGER cut_c01 BEFORE INSERT ON events WHEN NEW.type='AcceptanceCommitted' "
                                   "BEGIN SELECT RAISE(ABORT,'disk write failed'); END;")
                expected = sqlite3.IntegrityError
            else:
                store.arm("after_accept_before_supersede")
                expected = InjectedCrash
            with pytest.raises(expected):
                await case.run_until(lambda: case.status() == "COMPLETED", timeout=30)
            assert counts() == (0, 0, 0, 0) and not connection.in_transaction
            if fault == "crash":
                assert store.fired == ["after_accept_before_supersede"]
            else:
                connection.execute("DROP TRIGGER cut_c01")
            mission = await case.settle()
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            assert counts() == (1, 1, 1, 1)
            assert provider.review_calls["TASK_CONTENT"] == 1  # 重做验收不重新审阅

    asyncio.run(body())


# --------------------------------------------------------------------------- C02
def test_two_connection_concurrency(tmp_path):
    async def checks(product, assured):
        first = product.store
        second = Store.open(first.path)  # an independent connection to the same library
        try:
            with second.read_view() as connection:
                epochs = read_epochs_locked(connection, assured.id)
            cert = UseCertificate(
                mission_id=assured.id, consumer_kind="result", consumer_id="result-c02", scope_id="scope-c02",
                principal_id=PRINCIPAL.principal_id, purpose="ACCEPT", truth="TRUE", freshness="CURRENT",
                availability="READABLE", decision="USABLE", coverage="COMPLETE", policy_ref=Pin("policy-1", 1, HASH),
                read_set=(ReadItem("OBJECT", "o", HASH), ReadItem("QUERY_SET", "q", HASH), ReadItem("ACCESS", "a", HASH),
                          ReadItem("POLICY", "p", HASH)),
                clean_support_refs=(AssuranceRef("review", Pin("rec", 1, HASH)),), issued_at_ms=NOW, not_after_ms=None,
                reasons=("fixture",), mission_epoch=epochs.mission, environment_epoch=epochs.environment,
                clock_generation=epochs.clock_generation, root_incarnation_id="root-c02")
            other_body = UseCertificate.from_json({**cert.to_json(), "consumer_id": "result-other"})
            # Same command id from two writers: one adoption, an identical replay is a
            # read, a different body under the same id is a conflict (never REPLACE).
            assert AssuranceStore(second).record_certificate("cert-c02", cert) is True
            assert AssuranceStore(first).record_certificate("cert-c02", cert) is False
            with pytest.raises(AssuranceError) as raised:
                AssuranceStore(first).record_certificate("cert-c02", other_body)
            assert raised.value.code == "IMMUTABLE_IDENTITY_CONFLICT"
            assert _count(first, "SELECT COUNT(*) FROM assurance_use_certificates WHERE mission_id=?", assured.id) == 1
            # Two consumers ingesting the same page: the cursor CAS admits one.
            product.control.comment(assured.id, "看一下进度")
            version = first.connection.execute(
                "SELECT row_version FROM assurance_event_cursors WHERE mission_id=? AND consumer='REVIEW'",
                (assured.id,)).fetchone()["row_version"]
            target = WorkTarget("review-c02", HASH)
            now = int(first.now * 1000)
            AssuranceWorkStore(first).ingest(assured.id, "REVIEW", expected_version=version,
                                             classify=lambda e, c: (target,), now_ms=now)
            with pytest.raises(StoreConflict):
                AssuranceWorkStore(second).ingest(assured.id, "REVIEW", expected_version=version,
                                                  classify=lambda e, c: (target,), now_ms=now)
            assert _count(second, "SELECT COUNT(*) FROM assurance_pending_work WHERE mission_id=? AND consumer='REVIEW'",
                          assured.id) == 1
            # A held write lock on one connection makes the other wait, then both writes land (no lost update).
            second.connection.execute("BEGIN IMMEDIATE")
            second.connection.execute("INSERT INTO assurance_pending_work(mission_id,consumer,work_key,trigger_event_id,"
                                      "target_epoch,target_fingerprint,state,row_version,tries,not_before_ms) "
                                      "SELECT mission_id,'CLOSEOUT','lock-c02',trigger_event_id,target_epoch,"
                                      "target_fingerprint,'PENDING',1,0,not_before_ms FROM assurance_pending_work "
                                      "WHERE mission_id=? AND consumer='REVIEW'", (assured.id,))
            outcome = {}
            validity_version = first.connection.execute(
                "SELECT row_version FROM assurance_event_cursors WHERE mission_id=? AND consumer='VALIDITY'",
                (assured.id,)).fetchone()["row_version"]

            def contended():
                started = time.monotonic()
                try:
                    AssuranceWorkStore(first).ingest(assured.id, "VALIDITY", expected_version=validity_version,
                                                     classify=lambda e, c: (target,), now_ms=now)
                    outcome["result"] = "ok"
                except (StoreError, sqlite3.OperationalError) as error:
                    outcome["result"] = type(error).__name__
                outcome["waited"] = time.monotonic() - started

            worker = threading.Thread(target=contended)
            worker.start()
            time.sleep(0.3)
            assert "result" not in outcome  # blocked behind the other writer
            second.connection.execute("COMMIT")
            worker.join(timeout=10)
            assert outcome["result"] == "ok" and outcome["waited"] >= 0.25
            rows = second.connection.execute(
                "SELECT consumer,work_key FROM assurance_pending_work WHERE mission_id=? AND work_key IN "
                "('lock-c02','review-c02') ORDER BY consumer", (assured.id,)).fetchall()
            assert [tuple(r) for r in rows] == [("CLOSEOUT", "lock-c02"), ("REVIEW", "review-c02"),
                                               ("VALIDITY", "review-c02")]
            assert product.notices == []  # no side effect left the process
        finally:
            second.close()

    async def body():
        async with product_world(tmp_path / "root", RoleScriptedProvider({}), auto=False) as product:
            created = product.create({"goal": "assured c02", "success_criteria": ["file:NOTES.md"],
                                      "idempotency_key": "assured-c02"})
            await checks(product, product.store.get_mission(created["mission_id"]))

    asyncio.run(body())


# --------------------------------------------------------------------------- C03
def test_review_cold_resume():
    """产品同形恢复接缝：内容审阅那一层记下后进程被杀，重启沿用原来那次审阅调用、不再问模型；
    重启遇到时钟回拨（持久化的高水位比墙钟晚），回拨期间工作入库不认领，追上后恢复且通知只发一次；
    无业务事件的证书到期在重启时补发、不重复补发。"""
    report = _seam("recovery-seam.py")
    cold = report["cold_resume"]
    assert cold["fault"] == "after_layer_pass:attempt" and cold["mission"] == "COMPLETED"
    assert cold["review_calls_before_crash"]["TASK_CONTENT"] == 1
    assert cold["review_calls_after_restart"].get("TASK_CONTENT", 0) == 0  # 原来那次调用被沿用
    assert cold["ordinals_after"] == [1] and cold["official_records"]["TASK_CONTENT"] == 1
    rollback = report["restart_rollback"]
    assert rollback["startup"]["clock_state"] == "ROLLBACK"
    assert rollback["startup"]["clock_generation"] == rollback["first_environment"]["clock_generation"] + 1
    assert rollback["discontinuity"]["clock_state"] == "ROLLBACK"
    assert rollback["work_during_rollback"] and all(w["state"] != "RUNNING" for w in rollback["work_during_rollback"])
    assert rollback["environment_after"]["clock_state"] == "STABLE" and rollback["sent"] == 1
    expiry = report["eventless_expiry"]
    assert expiry["startup_emitted"] >= 1 and expiry["expired_observations"] >= 1 and expiry["second_restart_emitted"] == 0


# --------------------------------------------------------------------------- C04
def test_event_cursor_atomicity(tmp_path):
    async def body():
        async with product_world(tmp_path / "root", RoleScriptedProvider({}), auto=False) as product:
            created = product.create({"goal": "assured c04", "success_criteria": ["file:NOTES.md"],
                                      "idempotency_key": "assured-c04"})
            assured = product.store.get_mission(created["mission_id"])
            store = product.store
            work = AssuranceWorkStore(store)
            product.control.comment(assured.id, "第一条留言")
            product.control.comment(assured.id, "第二条留言")
            cursor = store.connection.execute(
                "SELECT last_event_seq,row_version FROM assurance_event_cursors WHERE mission_id=? AND consumer='CLOSEOUT'",
                (assured.id,)).fetchone()
            seen = []

            def classify(event, consumer):
                seen.append(event.seq)
                if len(seen) == 2:
                    raise OSError("exit before ACK")
                return (WorkTarget("closeout-c04", HASH),)

            with pytest.raises(OSError):
                work.ingest(assured.id, "CLOSEOUT", expected_version=cursor["row_version"], classify=classify,
                            now_ms=int(store.now * 1000))
            after = store.connection.execute(
                "SELECT last_event_seq,row_version FROM assurance_event_cursors WHERE mission_id=? AND consumer='CLOSEOUT'",
                (assured.id,)).fetchone()
            assert tuple(after) == tuple(cursor)  # nothing ACKed before the work was durable
            assert _count(store, "SELECT COUNT(*) FROM assurance_pending_work WHERE mission_id=? AND consumer='CLOSEOUT'",
                          assured.id) == 0
            # A lost cursor is rebuilt explicitly at startup, never guessed at ingest.
            with pytest.raises(AssuranceError) as raised:
                AssuranceWorkStore(store).ingest("mission-none", "CLOSEOUT", expected_version=1,
                                                 classify=lambda e, c: (), now_ms=int(store.now * 1000))
            assert raised.value.code == "CURSOR_UNINITIALIZED"
    asyncio.run(body())
    # 产品部署上的四个消费者：启动安装、一个任务跑完、证书到期、通知游标丢了重启后重建且不重发。
    report = _seam("four-consumer-seam.py")
    install = report["production_install"]
    assert install["idle_rounds"] == 0  # 什么都没变时不自己触发
    assert install["second_install"] == "ASSURANCE_ALREADY_INSTALLED"
    assert set(install["cursor_seq"]) == {"REVIEW", "VALIDITY", "CLOSEOUT", "NOTIFY"}
    assert report["run"]["closeout_row"] == "FINALIZED" and report["run"]["notices"] == 1
    assert report["run"]["method_plan_official"] >= 1 and report["run"]["method_plan_scope"] is None
    assert report["expiry"]["due_events"] >= 1 and report["expiry"]["expired_observations"] >= report["expiry"]["due_events"]
    assert report["restart"]["cursor_rebuild"]["missions"] == 1 and len(report["restart"]["cursor_rebuild"]["cursors_rebuilt"]) == 1
    assert report["restart"]["notices_after_restart"] == 0


# --------------------------------------------------------------------------- C05
def test_closeout_and_notification():
    """产品主循环真推出 READY：根结论凭当前终审使用证书写下 → 收尾 READY → 唯一定稿写入 → 通知一次；
    用户取消的那次写入同时请求通知，不走定稿写入。"""
    report = _seam("final-writer-seam.py")
    assert report["root_resolution"]["witness_is_mission_final_use"] is True
    states = report["closeout"]["states"]
    assert "ROOT_RESOLUTION_MISSING" in report["closeout"]["not_ready_reasons"]
    assert states[-2:] == ["READY", "FINALIZED"] and report["closeout"]["row"]["state"] == "FINALIZED"
    final = report["final_writer"]
    assert final["mission_status"] == "COMPLETED" and final["sent"] == 1
    assert [r["final_event_type"] for r in final["notification_requests"]] == ["MissionCompleted"]
    assert final["refusal_after_finalized"] == "CLOSEOUT_NOT_READY"
    cancel = report["cancel"]
    assert cancel["sent"] == 1 and cancel["closeout_row"] is None
    assert [r["final_event_type"] for r in cancel["notification_requests"]] == ["MissionCancelled"]


# --------------------------------------------------------------------------- C06
def _legacy_library(path, versions):
    connection = sqlite3.connect(path, isolation_level=None, timeout=5.0, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    store = Store(connection, Path(path), time.time)
    with store.transaction() as tx:
        for migration in schema.MIGRATIONS[:versions]:
            store._apply_migration(tx, migration)
    mission = Mission("mission-legacy", "legacy goal", ("c",), (), (), "low", Budget(), "tenant-legacy",
                      MissionStatus.CREATED, 1.0, 1, "legacy-key")
    store.insert_mission(mission, spec_hash="b" * 64)
    for index in range(3):
        store.append_event(Event(f"event-legacy-{index}", "MissionCreated" if index == 0 else "MissionNote",
                                 "trace-legacy", mission.id, None, None, "system", "legacy",
                                 {"index": index}, f"legacy-{index}", 1.0 + index))
    return store


def _dump(connection, tables):
    return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in tables}


def _ddl_hash(connection):
    rows = connection.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name").fetchall()
    return hashlib.sha256(json.dumps([list(row) for row in rows], sort_keys=True).encode()).hexdigest()


def test_real_migration_and_legacy(tmp_path):
    # The Assurance migration and every later one apply on top of a pre-Assurance
    # library (migration 27 re-keys the live review pin guard per object).
    assurance = next(m for m in schema.MIGRATIONS if m.name == "orchestrator-assurance-exec-v1.1")
    later = [m for m in schema.MIGRATIONS if m.version > assurance.version]
    legacy_path = tmp_path / "legacy.db"
    legacy = _legacy_library(str(legacy_path), assurance.version - 1)
    legacy_tables = ("missions", "events", "orch_schema_migrations")
    before = _dump(legacy.connection, legacy_tables)
    ddl_before = {row["name"]: row["sql"] for row in legacy.connection.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL")}
    assert not legacy.has_table("assurance_use_certificates")
    legacy.close()
    # Upgrade: the real migration chain applies the Assurance migration on top, with a backup.
    upgraded = Store.open(legacy_path)
    assert upgraded.has_table("assurance_use_certificates") and upgraded.has_table("assurance_environment_state")
    after = _dump(upgraded.connection, ("missions", "events"))
    assert after["missions"] == before["missions"] and after["events"] == before["events"]
    migrations = [tuple(row) for row in upgraded.connection.execute(
        "SELECT version,name,checksum FROM orch_schema_migrations ORDER BY version")]
    applied = len(before["orch_schema_migrations"])
    assert migrations[:applied] == [row[:3] for row in before["orch_schema_migrations"]]
    assert migrations[applied:] == [(m.version, m.name, m.checksum) for m in (assurance, *later)]
    # Migrations 31, 32 and 33 (2026-10-02) drop the candidate-comparison, fragment,
    # conflict, graph-change, system-pool and criterion-assessment tables;
    # every other legacy table keeps its DDL.
    # Migrations 35 / 36 (2026-10-03) drop the TaskGraph waiting table and the deferred-repair
    # continuation table with the mechanisms they served.
    dropped = {"selection_candidates", "selection_rounds", "search_bindings", "fragment_validations",
               "conflicts", "graph_changes", "mission_system_tail_pools", "mission_system_tail_tasks",
               "criterion_assessments", "taskgraph_requirements", "planning_repair_continuations"}
    for name, ddl in ddl_before.items():
        current = upgraded.connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        if name in dropped:
            assert current is None, name
            continue
        assert current is not None and current[0] == ddl, name  # legacy DDL untouched
    backup = legacy_path.with_name(f"{legacy_path.name}.pre-schema-{schema.SCHEMA_VERSION}.backup")
    assert backup.is_file()
    # The legacy Mission keeps its lane; reopen is idempotent; a full new library matches.
    # A pre-Assurance Mission has no creation contract: it is never adopted into a
    # lane by the migration itself (deployment reconciliation classifies it LEGACY).
    with pytest.raises(AssuranceError) as raised:
        AssuranceStore(upgraded).lane("mission-legacy")
    assert raised.value.code == "CREATION_CONTRACT_UNRESOLVED"
    ddl_after = _ddl_hash(upgraded.connection)
    upgraded.close()
    reopened = Store.open(legacy_path)
    assert _ddl_hash(reopened.connection) == ddl_after
    assert _dump(reopened.connection, ("missions", "events")) == after
    fresh = Store.open(tmp_path / "fresh.db")
    assert _ddl_hash(fresh.connection) == ddl_after
    # Triggers: an inventoried source table refuses REPLACE-style rewrites of Assurance events.
    from agent_orchestrator.assurance.event_kinds import EVENT_REF_KINDS
    source_kind = sorted(EVENT_REF_KINDS.values())[0]
    with pytest.raises(sqlite3.IntegrityError):
        reopened.connection.execute("UPDATE events SET type=? WHERE event_id='event-legacy-1'", (source_kind,))
    # FK: Assurance rows cannot reference a Mission that does not exist.
    with pytest.raises(sqlite3.IntegrityError):
        reopened.connection.execute(
            "INSERT INTO assurance_use_certificates(certificate_id,mission_id,consumer_kind,consumer_id,purpose,scope_id,"
            "read_set_hash,certificate_hash,certificate_json,issued_at_ms,not_after_ms) VALUES('c','mission-none','r','r',"
            "'ACCEPT','s',?,?,'{}',1,NULL)", (HASH, HASH))
    reopened.close()
    fresh.close()
    # Wrong checksum: the library is refused, not silently re-migrated.
    tampered = sqlite3.connect(legacy_path)
    tampered.execute("UPDATE orch_schema_migrations SET checksum='0' WHERE version=?", (assurance.version,))
    tampered.commit()
    tampered.close()
    with pytest.raises(SchemaIncompatible):
        Store.open(legacy_path)
    # A failing migration rolls the whole upgrade back: the legacy library stays at its version.
    second = tmp_path / "legacy-2.db"
    _legacy_library(str(second), assurance.version - 1).close()
    broken = schema.Migration(assurance.version, assurance.name,
                              assurance.ddl + "\nCREATE TABLE broken(x INTEGER REFERENCES nope(y));\nINSERT INTO broken VALUES(1);")
    with patch.object(schema, "MIGRATIONS", (*schema.MIGRATIONS[:assurance.version - 1], broken, *later)):
        with pytest.raises(sqlite3.Error):
            Store.open(second)
    rows = sqlite3.connect(second).execute("SELECT MAX(version) FROM orch_schema_migrations").fetchone()[0]
    assert rows == assurance.version - 1
    tables = {row[0] for row in sqlite3.connect(second).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "assurance_use_certificates" not in tables and "broken" not in tables
    again = Store.open(second)
    assert again.has_table("assurance_use_certificates")
    again.close()
    # Kill: a writer killed inside an open transaction leaves nothing half-written.
    third = tmp_path / "legacy-3.db"
    _legacy_library(str(third), len(schema.MIGRATIONS)).close()
    script = (
        "import sqlite3,sys,time,os\n"
        f"c=sqlite3.connect({str(third)!r},isolation_level=None)\n"
        "c.execute('BEGIN IMMEDIATE')\n"
        "for i in range(2000): c.execute(\"INSERT INTO events(event_id,idempotency_key,type,trace_id,mission_id,task_id,attempt_id,actor_type,actor_id,payload_json,created_at,schema_version) VALUES(?,?,'MissionNote','t','mission-legacy',NULL,NULL,'system','kill','{}',1.0,1)\",(f'kill-{i}',f'kill-{i}'))\n"
        "sys.stdout.write('ready\\n'); sys.stdout.flush(); time.sleep(30)\n")
    child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    assert child.stdout.readline().strip() == "ready"
    os.kill(child.pid, signal.SIGKILL)
    child.wait(timeout=10)
    survivor = Store.open(third)
    assert survivor.count_events("mission-legacy") == 3
    assert _ddl_hash(survivor.connection) == ddl_after
    survivor.close()
