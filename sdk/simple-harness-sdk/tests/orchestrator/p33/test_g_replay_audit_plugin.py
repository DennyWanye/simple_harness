"""O4 observer oracles: inspect every Mission, including deliberate bad fixtures.

No nested pytest invocation. These exercise the plugin against real Store and
CommitService; intentional findings stay in the report, never become exclusions.

HTN 补齐阶段 A′：被观察的任务都经产品组装建（产品同形部署：门面建任务，根与执行图在同一事务里
绑上；子进程里也是同一份部署）；坏数据仍用 SQL 改坏已存字节或手写探针事件造给观察器看（观察器
本身是被测对象）。

产品建任务时就会写 4 种回放折叠还不认识的事件（保证通道启用、证据变化、义务需求准入、执行图合同
启用；已作为观测缺口报告），所以产品任务的审计闸门现在总是 OPEN。原来断言 ``gate == "PASS"``
的地方改为 :func:`clean`："除了这 4 种建任务事件，什么问题都没有"；断言 OPEN 的负例改为
``not clean(...)``，仍然要求损坏本身被报出来。
"""

import importlib.util
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from p33_world import opened, request

from agent_orchestrator.contracts import Event
from agent_orchestrator.orchestrator.commit_service import CommitService
from agent_orchestrator.storage.store import Store

#: 产品建任务时写、回放折叠还不认识的事件类型（已报告）。
CREATION_ONLY = frozenset(
    {"AssuranceProfileActivated", "AssuranceEvidenceChanged", "ObligationDemandAdmitted",
     "TaskGraphContractEnabled"})

CHILD = """
import asyncio, json, sys
from pathlib import Path
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

async def main():
    async with product_world(Path(sys.argv[1]), LayeredScriptedProvider()) as world:
        created = world.control.create({"goal": "child", "success_criteria": ["file:x.md"],
                                        "idempotency_key": sys.argv[2]})
        if len(sys.argv) > 3:
            Path(sys.argv[3]).write_text(json.dumps({"mission_id": created["mission_id"]}))
            await asyncio.to_thread(sys.stdin.readline)
        else:
            print(created["mission_id"])

asyncio.run(main())
"""


def clean(report):
    """除了产品建任务的 4 种事件回放还不认识以外，审计报告里没有任何问题。"""

    if report["store_errors"] or not report["unknown_scan_complete"]:
        return False
    for row in report["observations"]:
        comparison = row["comparison"]
        if (row["errors"] or row["in_transaction"] or not row["event_scan_complete"] or row["gaps"]
                or set(row["unknown_event_types"]) - CREATION_ONLY or comparison is None
                or comparison["mismatches"] or comparison["not_covered"]):
            return False
    for stream in report["global_streams"]:
        if stream["registry_consistency"] or stream["unknown_event_types"] or stream["gaps"]:
            return False
    return bool(report["observations"])


def without_creation(counts):
    return {kind: n for kind, n in counts.items() if kind not in CREATION_ONLY}


@pytest.fixture
def audit():
    path = Path(__file__).parents[1] / "p33_replay_audit.py"
    spec = importlib.util.spec_from_file_location("p33_replay_audit_oracle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    observer = module.ReplayAudit()
    observer.install()
    observer.nodeid = "oracle::normal_and_negative"
    try:
        yield observer
    finally:
        observer.uninstall()


def mission(world, key):
    mission_id = world.control.create(request(key, goal="审计", success_criteria=["file:x.md"]))["mission_id"]
    return world.store.get_mission(mission_id)


def probe(store, mid, key="probe", kind="Probe"):
    store.append_event(
        Event(
            id=key,
            type=kind,
            trace_id="trace",
            mission_id=mid,
            task_id=None,
            attempt_id=None,
            actor_type="system",
            actor_id="oracle",
            payload={},
            idempotency_key=key,
            created_at=1.0,
        )
    )


def rows(audit, mid):
    return [row for row in audit.report()["observations"] if row["mission_id"] == mid]


def test_actual_sibling_execution_db_is_inventoried_without_hiding_mission_damage(tmp_path, audit):
    root = tmp_path / "root"
    with opened(root) as world:
        mission(world, "x")
    audit.discover([root], "closed")
    report = audit.report()
    assert clean(report)
    assert report["mission_count"] == 1
    # 产品的原生执行池库（每个上下文尺寸一个）就是 orchestrator.db 的兄弟库
    executions = report["execution_databases"]
    assert len(executions) == 2
    assert all(row["execution_replay_verified"] is False for row in executions)
    connection = sqlite3.connect(sorted(root.glob("execution-*.db"))[0])
    connection.execute("CREATE TABLE missions(broken TEXT)")
    connection.commit()
    connection.close()
    audit.discover([root], "mixed-damaged")
    assert not clean(audit.report()) and audit.report()["gate"] == "OPEN"


def test_close_and_unclosed_teardown_both_capture_clean_real_missions(tmp_path, audit):
    with opened(tmp_path / "first") as first:
        a = mission(first, "a")
    with opened(tmp_path / "second") as second:
        b = mission(second, "b")
        audit.sweep("teardown")
        assert {row["mission_id"] for row in audit.report()["observations"]} == {a.id, b.id}
        assert all(
            row["comparison"]["coverage"] == 1 for row in rows(audit, a.id) + rows(audit, b.id)
        )
        assert clean(audit.report())


def test_unknown_and_formal_drift_are_not_a_whole_test_exemption(tmp_path, audit):
    with opened(tmp_path / "root") as world:
        a, b = mission(world, "negative"), mission(world, "positive")
        probe(world.store, a.id)
        damaged = {**a.to_json(), "status": "PLANNING"}
        world.store.connection.execute(
            "UPDATE missions SET status='PLANNING', json=? WHERE mission_id=?",
            (json.dumps(damaged), a.id),
        )
    [bad], [good] = rows(audit, a.id), rows(audit, b.id)
    assert without_creation(bad["unknown_event_types"]) == {"Probe": 1}
    assert bad["comparison"]["mismatches"]
    assert without_creation(good["unknown_event_types"]) == {} and good["comparison"]["mismatches"] == []
    assert bad["nodeid"] == good["nodeid"] == "oracle::normal_and_negative"
    assert not clean(audit.report()) and audit.report()["gate"] == "OPEN"


def test_corrupt_mission_json_does_not_hide_its_events_or_other_missions(tmp_path, audit):
    with opened(tmp_path / "root") as world:
        a, b = mission(world, "bad-json"), mission(world, "good")
        probe(world.store, a.id)
        world.store.connection.execute("UPDATE missions SET json='{}' WHERE mission_id=?", (a.id,))
    [bad] = rows(audit, a.id)
    assert without_creation(bad["unknown_event_types"]) == {"Probe": 1}
    assert any(error["stage"] == "snapshot" for error in bad["errors"])
    assert rows(audit, b.id)[0]["comparison"]["coverage"] == 1
    assert not clean(audit.report())


def test_events_without_mission_row_remain_in_all_mission_inventory(tmp_path, audit):
    with opened(tmp_path / "root") as world:
        a = mission(world, "owner")
        world.store.connection.execute("PRAGMA foreign_keys=OFF")
        world.store.connection.execute("UPDATE events SET mission_id='orphan' WHERE mission_id=?", (a.id,))
    assert rows(audit, a.id) and rows(audit, "orphan")
    assert rows(audit, "orphan")[0]["errors"]
    assert not clean(audit.report())


def test_same_path_reopen_preserves_nodeid_and_does_not_double_count_unknowns(tmp_path, audit):
    # 产品的库总在磁盘上：原来的"内存库"一档没有产品同形的对应，删去（记偏离）。
    root = tmp_path / "root"
    with opened(root) as world:
        b = mission(world, "disk")
        probe(world.store, b.id)
        audit.sweep("teardown")
    audit.nodeid = "oracle::reopen"
    Store.open(root / "orchestrator.db").close()
    report = audit.report()
    assert {row["nodeid"] for row in rows(audit, b.id)} == {
        "oracle::normal_and_negative",
        "oracle::reopen",
    }
    assert without_creation(report["mission_unknown_event_types"]) == {"Probe": 1}
    assert without_creation(report["raw_unknown_event_types"]) == {"PolicySeeded": 1, "Probe": 1}


def test_deleted_export_does_not_exempt_the_complete_original_database(tmp_path, audit):
    with opened(tmp_path / "root") as world:
        a = mission(world, "complete")
        (tmp_path / "partial.jsonl").write_text("")
    [row] = rows(audit, a.id)
    assert row["event_count"] > 0 and row["comparison"]["coverage"] == 1
    assert clean(audit.report())


def test_scan_uses_all_events_beyond_store_default_page(tmp_path, audit):
    with opened(tmp_path / "root") as world:
        a = mission(world, "many")
        # Real persisted events, not a mock list_events that could mask truncation.
        with world.store.transaction():
            for ordinal in range(10_001):
                probe(world.store, a.id, f"probe-{ordinal}")
    [row] = rows(audit, a.id)
    assert row["event_count"] > 10_001
    assert without_creation(row["unknown_event_types"]) == {"Probe": 10_001}
    assert without_creation(audit.report()["mission_unknown_event_types"]) == {"Probe": 10_001}
    assert without_creation(audit.report()["raw_unknown_event_types"]) == {"PolicySeeded": 1, "Probe": 10_001}


def test_observer_does_not_persist_raw_event_payloads(tmp_path, audit):
    with opened(tmp_path / "root") as world:
        store = world.store
        a = mission(world, "payload")
        probe(store, a.id)
        marker = "private-payload-do-not-export"
        store.connection.execute(
            "UPDATE events SET payload_json=? WHERE type='Probe'", (json.dumps({"private": marker}),)
        )
        before = store.connection.total_changes
        audit.sweep("teardown")
        assert store.connection.total_changes == before
        assert marker not in json.dumps(audit.report())
        assert a.id in {row["mission_id"] for row in audit.report()["observations"]}


def test_deployment_stream_is_retained_and_checked_by_its_real_registry_scope(tmp_path, audit):
    with opened(tmp_path / "root") as world:
        a = mission(world, "normal")
    report = audit.report()
    assert {row["mission_id"] for row in report["observations"]} == {a.id}
    [global_stream] = report["global_streams"]
    assert global_stream["stream_id"] == "deployment"
    assert "mission_id" not in global_stream
    assert global_stream["event_types"] == {"PolicySeeded": 1}
    assert global_stream["registry_consistency"] == []
    assert global_stream["unknown_event_types"] == {}
    assert global_stream["gaps"] == []
    assert {gap["rule"] for gap in global_stream["raw_projection_gaps"]} == {
        "mission_created_missing"
    }
    assert without_creation(report["raw_unknown_event_types"]) == {"PolicySeeded": 1}
    assert without_creation(report["mission_unknown_event_types"]) == {}
    assert report["global_unknown_event_types"] == {}
    assert clean(report)


@pytest.mark.parametrize("damage", ["unknown_global", "policy_on_mission", "registry_drift"])
def test_scope_routing_does_not_hide_unknowns_or_global_formal_drift(tmp_path, audit, damage):
    with opened(tmp_path / "root") as world:
        store = world.store
        a = mission(world, "normal")
        if damage == "unknown_global":
            probe(store, "deployment")
        elif damage == "policy_on_mission":
            probe(store, a.id, kind="PolicySeeded")
        else:
            # 保证通道的完整性屏障不让人直接改策略版本表（要来源变更回执）；改坏部署时间线上
            # 那条已存事件的字节，同样造出"时间线与登记表对不上"
            store.connection.execute(
                "UPDATE events SET payload_json=json_set(payload_json,'$.version_id','forged-version') "
                "WHERE type='PolicySeeded'")
    report = audit.report()
    if damage == "unknown_global":
        assert report["global_unknown_event_types"] == {"Probe": 1}
        assert without_creation(report["raw_unknown_event_types"]) == {"PolicySeeded": 1, "Probe": 1}
    elif damage == "policy_on_mission":
        assert without_creation(report["mission_unknown_event_types"]) == {"PolicySeeded": 1}
        assert without_creation(report["raw_unknown_event_types"]) == {"PolicySeeded": 2}
    else:
        assert report["global_streams"][0]["registry_consistency"]
    assert not clean(report) and report["gate"] == "OPEN"


def test_discovery_finds_real_subprocess_mission_without_parent_store_open(tmp_path, audit):
    root = tmp_path / "child"
    child = subprocess.run(
        [sys.executable, "-c", CHILD, str(root), "child"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    mid = child.stdout.strip().splitlines()[-1]
    assert rows(audit, mid) == []  # no parent Store registration
    audit.discover([tmp_path], "after_teardown")
    [row] = rows(audit, mid)
    assert row["acquisition"] == "discovered"
    assert row["comparison"]["coverage"] == 1 and row["errors"] == []
    assert audit.report()["global_streams"]  # do not drop the child's policy timeline
    assert clean(audit.report())


def test_discovery_keeps_direct_sqlite_bad_mission_without_migrating_it(tmp_path, audit):
    path = tmp_path / "manual.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE missions(mission_id TEXT, json TEXT)")
        connection.execute("INSERT INTO missions VALUES ('manual', '{}')")
    before = path.read_bytes()
    audit.discover([tmp_path], "after_teardown")
    [row] = rows(audit, "manual")
    assert row["acquisition"] == "discovered" and row["errors"]
    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        assert [
            r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ] == ["missions"]
    assert audit.report()["gate"] == "OPEN"


def test_discovery_reports_previously_observed_database_deletion(tmp_path, audit):
    root = tmp_path / "root"
    with opened(root) as world:
        a = mission(world, "deleted")
    (root / "orchestrator.db").unlink()
    audit.discover([tmp_path], "after_teardown")
    report = audit.report()
    assert rows(audit, a.id)  # keep the earlier snapshot, too
    assert any(e["error"] == "deleted_database" for e in report["store_errors"])
    assert not clean(report) and report["gate"] == "OPEN"


def test_bad_event_json_keeps_raw_unknown_detection_and_decode_error(tmp_path, audit):
    with opened(tmp_path / "root") as world:
        a = mission(world, "bad-event")
        probe(world.store, a.id)
        world.store.connection.execute("UPDATE events SET payload_json='{' WHERE type='MissionCreated'")
    report = audit.report()
    [row] = rows(audit, a.id)
    assert row["event_types"]["Probe"] == 1
    assert row["event_scan_complete"] is False and row["errors"]
    assert without_creation(report["raw_unknown_event_types"]) == {"PolicySeeded": 1, "Probe": 1}
    assert without_creation(report["mission_unknown_event_types"]) == {"Probe": 1}
    assert report["unknown_scan_complete"] is False and not clean(report)


def test_global_finding_resolves_to_exact_exported_observation(tmp_path, audit):
    with opened(tmp_path / "root") as world:
        mission(world, "lookup")
        probe(world.store, "deployment")
    report = audit.report()
    [finding] = [f for f in report["findings"] if f["scope"] == "deployment"]
    [row] = [
        r for r in report["global_streams"] if r["observation_id"] == finding["observation_id"]
    ]
    assert row["stream_id"] == "deployment" and row["unknown_event_types"] == {"Probe": 1}


def test_discovery_reads_committed_wal_from_live_child_without_parent_store_open(tmp_path, audit):
    root, ready = tmp_path / "wal", tmp_path / "ready.json"
    path = root / "orchestrator.db"
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(root), "wal", str(ready)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 60
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "child did not create its committed Mission"
        mid = json.loads(ready.read_text())["mission_id"]
        assert Path(str(path) + "-wal").stat().st_size > 0
        assert rows(audit, mid) == []
        audit.discover([tmp_path], "after_teardown")
        [row] = rows(audit, mid)
        assert row["acquisition"] == "discovered" and row["comparison"]["coverage"] == 1
        assert clean(audit.report())
    finally:
        try:
            child.communicate("done\n", timeout=30)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=5)
            raise
    assert child.returncode == 0


def test_observed_capture_is_consistent_when_other_connection_commits_between_reads(
    tmp_path, audit, monkeypatch
):
    with opened(tmp_path / "root") as world:
        reader = world.store
        a = mission(world, "race")
        writer = Store.open(reader.path)
        namespace = audit.capture.__func__.__globals__
        original = namespace["events_from_store"]
        committed = []

        def commit_between_reads(store, mid):
            events = original(store, mid)
            if store is reader and mid == a.id and not committed:
                # A legitimate atomic commit from another connection after the event
                # read, before Store.snapshot (the loop's own CREATED → PLANNING write).
                CommitService(writer).begin_planning(a.id)
                committed.append(True)
            return events

        monkeypatch.setitem(namespace, "events_from_store", commit_between_reads)
        try:
            audit.capture(reader, "between_reads")
            assert committed == [True]
            [row] = [r for r in rows(audit, a.id) if "between_reads" in r["phases"]]
            assert row["comparison"]["coverage"] == 1 and row["comparison"]["mismatches"] == []
            assert row["observer_owned_read_snapshot"] is True
            assert row["in_transaction"] is False
            assert reader.connection.in_transaction is False
            assert str(reader.get_mission(a.id).status) == "PLANNING"
            assert clean(audit.report())
        finally:
            writer.close()
            monkeypatch.undo()


def test_observer_does_not_commit_or_hide_a_producer_open_transaction(tmp_path, audit):
    with opened(tmp_path / "root") as world:
        store = world.store
        a = mission(world, "transaction")
        with store.transaction():
            world.loop.commit.begin_planning(a.id)
            audit.capture(store, "producer_transaction")
            assert store.connection.in_transaction is True
            [row] = [r for r in rows(audit, a.id) if "producer_transaction" in r["phases"]]
            assert row["in_transaction"] is True
            assert row["observer_owned_read_snapshot"] is False
            assert not clean(audit.report()) and audit.report()["gate"] == "OPEN"
