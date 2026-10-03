# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""G1 oracle before implementation: a Host never publishes a partial source batch.

The real deployment door and Facade create the Mission. Faults after the first
source, during events, and at the batch receipt must roll back all SQLite state.
Only immutable unreferenced CAS blobs may survive. Concurrent commits and reordered
retries return the original receipt; an ordinary create cannot be adopted later.

HTN 补齐阶段 A′：建任务走产品组装（``deployment.create_mission_with_sources``：任务、来源、根、
执行图绑定在同一个事务里），不跑主循环。故障注入改为数据库触发器让真实事务里的那次写入失败
（事件、回执）和 CAS 落盘失败（磁盘写不进）；"新连接看持久化回执"改为同一根目录上重启。
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from p33_world import TENANT, opened

from agent_orchestrator.api.facade import FacadeError, MissionControlV1
from agent_orchestrator.governance.domains import CODE_DOMAIN
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.observability.business_replay import verify_library, verify_mission
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector


def command():
    return {
        "mission": {
            "goal": "比较原始资料",
            "success_criteria": ["file:REPORT.md"],
            "domain": CODE_DOMAIN,
            "idempotency_key": "g1-batch",
            "budget": {"max_tokens": 30000, "max_attempts": 4},
        },
        "sources": [
            {"path": "sources/a.md", "content": "甲材料。\r\n", "kind": "markdown"},
            {"path": "sources/b.md", "content": "乙材料。\n", "kind": "markdown"},
        ],
    }


def world_at(tmp_path, *, publish=False):
    root = tmp_path / "evidence"
    if not publish:
        return opened(root)
    # 发布目录落在证据存储里：来源批次在门口就该被拒
    return opened(root, deployment_policy=DeploymentPolicy(enabled_connectors=("file_publish",)),
                  connectors={"file_publish": FilePublishConnector(root / "artifacts", tmp_path / "publish-ledger")})


def create(world, value):
    return world.deployment.create_mission_with_sources(world.loop, world.control, value)


def database_state(store):
    with store.read_view() as connection:
        return tuple(connection.iterdump())


def fail_on(store, name, table, condition):
    """让真实事务里的那一次写入失败（模拟写库出错），由 SQLite 自己回滚整个事务。"""

    store.connection.execute(
        f"CREATE TEMP TRIGGER {name} BEFORE INSERT ON {table} WHEN {condition} "
        "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END")


def test_batch_reordering_reopen_returns_original_receipt_and_replays(tmp_path):
    value = command()
    with world_at(tmp_path) as world:
        receipt = create(world, value)
        mid = receipt["mission_id"]
        assert receipt["created"] is True and receipt["status"] == "CREATED"
        assert receipt["spec_hash"] == world.store.find_mission(TENANT, "g1-batch")[1]
        assert set(receipt["source_versions"]) == {s["path"] for s in value["sources"]}
        assert len(receipt["sources"]) == 2 and len(receipt["batch_hash"]) == 64
        # 根与执行图绑定在同一个事务里落下
        assert world.store.connection.execute(
            "SELECT COUNT(*) FROM taskgraph_policy_bindings WHERE mission_id=?", (mid,)).fetchone()[0] == 1
        for item in value["sources"]:
            row = world.store.get_source(mid, item["path"])
            assert row["trust"] == "untrusted_external"
            assert (
                world.loop.assembled.workspaces.artifact_store.read(row["version_hash"])
                == item["content"].encode()
            )
        before = database_state(world.store)
        assert create(world, {**value, "sources": value["sources"][::-1]}) == receipt
        assert database_state(world.store) == before
    # A restarted deployment exercises persisted receipt identity, not facade memory.
    with world_at(tmp_path) as reopened:
        before = database_state(reopened.store)
        assert create(reopened, value) == receipt
        assert database_state(reopened.store) == before
        # 全业务重放 v3：没有不一致的表，全库每行恰好被点名一次
        report = verify_mission(reopened.store, mid)
        assert "INCONSISTENT" not in {item["status"] for item in report["tables"].values()}, report
        assert verify_library(reopened.store)["status"] == "CONSISTENT"


@pytest.mark.parametrize("damage", ["content", "kind", "goal"])
def test_batch_changed_body_conflicts_without_mutation(tmp_path, damage):
    with world_at(tmp_path) as world:
        value = command()
        create(world, value)
        changed = deepcopy(value)
        if damage == "goal":
            changed["mission"]["goal"] += "不同目标"
        else:
            changed["sources"][0][damage] += "不同"
        before = database_state(world.store)
        with pytest.raises(FacadeError) as raised:
            create(world, changed)
        assert raised.value.code == "conflict"
        assert database_state(world.store) == before


def test_ordinary_create_cannot_be_adopted_as_an_atomic_batch(tmp_path):
    with world_at(tmp_path) as world:
        world.deployment.create_mission(world.loop, world.control, command()["mission"])
        before = database_state(world.store)
        with pytest.raises(FacadeError) as raised:
            create(world, command())
        assert raised.value.code == "conflict"
        assert database_state(world.store) == before


@pytest.mark.parametrize("point", ["second_cas", "source_event", "batch_receipt"])
def test_batch_fault_rolls_back_mission_budget_binding_sources_receipts_and_events(
    tmp_path, monkeypatch, point
):
    with world_at(tmp_path) as world:
        before = database_state(world.store)
        if point == "second_cas":
            # 第二份来源原文落盘失败（磁盘写不进）
            cas = world.loop.assembled.workspaces.artifact_store
            original = cas.put_bytes
            calls = []

            def fail(data):
                calls.append(data)
                if len(calls) == 2:
                    raise OSError("second source cannot persist")
                return original(data)

            monkeypatch.setattr(cas, "put_bytes", fail)
            # Commit may hold a distinct ArtifactStore object for the same real CAS root.
            monkeypatch.setattr(world.loop.commit._source_cas(), "put_bytes", fail)
        elif point == "source_event":
            fail_on(world.store, "fail_source_event", "events",
                    "NEW.type = 'SourceRegistered' AND NEW.payload_json LIKE '%b.md%'")
        else:
            fail_on(world.store, "fail_batch_receipt", "commit_receipts", "NEW.kind = 'mission_source_batch'")
        # 写库出错时门面不翻译 SQLite 的异常，原样抛出（与原用例注入 RuntimeError 同）
        with pytest.raises((FacadeError, RuntimeError, sqlite3.Error)):
            create(world, command())
        assert database_state(world.store) == before
    with world_at(tmp_path) as reopened:
        assert reopened.store.list_missions() == []


@pytest.mark.parametrize(
    "other",
    [
        "sources/A.md",
        "sources/a.md/child",
        "sources/a.md",
        "sources/Dir/b.md",
        "sources/é/b.md",
    ],
)
def test_batch_active_aliases_and_file_ancestors_refuse_atomically(tmp_path, other):
    with world_at(tmp_path) as world:
        value = command()
        if "Dir" in other:
            value["sources"][0]["path"] = "sources/dir/a.md"
        if "é" in other:
            value["sources"][0]["path"] = "sources/é/a.md"
        value["sources"][1]["path"] = other
        before = database_state(world.store)
        with pytest.raises(FacadeError):
            create(world, value)
        assert database_state(world.store) == before


@pytest.mark.parametrize("field", ["principal", "tenant_id", "trust", "origin", "idempotency_key"])
def test_batch_source_items_reject_caller_authority_fields(tmp_path, field):
    with world_at(tmp_path) as world:
        value = command()
        value["sources"][0][field] = "caller"
        before = database_state(world.store)
        with pytest.raises(FacadeError) as raised:
            create(world, value)
        assert raised.value.code == "invalid_request"
        assert database_state(world.store) == before


def test_batch_runs_real_source_publish_storage_guard(tmp_path):
    with world_at(tmp_path, publish=True) as world:
        before = database_state(world.store)
        with pytest.raises(FacadeError, match="source_publish_root_overlap"):
            create(world, command())
        assert database_state(world.store) == before


def test_two_concurrent_requests_commit_one_source_batch(tmp_path):
    """Host 的两个请求线程同时提交同一批：只建一个任务、两份回执相同。"""

    with world_at(tmp_path) as world:
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(lambda _: create(world, command()), range(2)))
        assert receipts[0] == receipts[1]  # 批次回执原样重放（含 created）
        assert len(world.store.list_missions()) == 1
        assert len(world.store.list_sources(receipts[0]["mission_id"])) == 2


def test_same_batch_key_is_tenant_scoped(tmp_path):
    with world_at(tmp_path) as world:
        value = command()
        original = create(world, value)
        foreign = MissionControlV1(world.loop, tenant_id="foreign", principal=Principal("other-host"))
        before = database_state(world.store)
        # 别的租户拿同一个批次键，拿不到本租户的回执；它在这个部署里也建不了任务、什么都不留下
        with pytest.raises(FacadeError):
            foreign.create_with_sources(value)
        assert database_state(world.store) == before
        assert create(world, value) == original
        rows = world.store.list_sources(original["mission_id"])
        assert len(rows) == 2 and {row["tenant_id"] for row in rows} == {TENANT}
