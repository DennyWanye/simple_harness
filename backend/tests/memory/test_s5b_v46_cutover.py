# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b Task 6 / AC-6②：v46 cutover 断言用例集合（Task 8 以 ``record-run --exec`` 跑本文件）。

- 037（v45）回补进 ``_S4_HUMAN_MIGRATIONS``：新库有 marker / 迁移链行 / 恢复注册（fence 触发器）；
  S5a 期间在链外应用 037 的旧库在打开时被幂等回补；
- 迁移前置：非终态 foreground Run / WAITING SDK Run → 稳定拒绝且库不变；终态 Run 不阻塞；
- 前向迁移 v45→v46（含 Task 3/4 加列）与 rollback drill：evidence 行数/hash 守恒，旧 runtime 打开 v46 稳定拒绝；
- 旧 checkpoint（PROJECT_EFFECT policy 改变前）恢复 → 稳定隔离（``catalog_state_fingerprint_stale``）。
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.memory import migrator, schema
from deskpet.memory.migrator import (
    CONTEXT_ROUTE_MIGRATION,
    DEFAULT_MIGRATIONS_DIR,
    EFFECT_CLOSURE_MIGRATION,
    V46_CUTOVER_BLOCKED_FOREGROUND_RUN_ACTIVE,
    V46_CUTOVER_BLOCKED_SDK_RUN_WAITING,
    repair_context_route_registration,
)
from deskpet.memory.schema import (
    HumanMemoryProgramEpochError,
    InitializeError,
    StartupEpoch,
    initialize_human_memory_program_state_db,
    inspect_startup_epoch,
)

CONTEXT_ROUTE_TABLES = (
    "context_route_marker",
    "context_route_decisions",
    "run_context_snapshot_receipts",
    "context_route_tool_invocations",
    "occurrence_presented",
)
EVIDENCE_TABLES = ("human_memory_evidence", "human_memory_sanitization_receipts")


def _rows(db: Path, sql: str, *params):  # type: ignore[no-untyped-def]
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).fetchall()


def _user_version(db: Path) -> int:
    return int(_rows(db, "PRAGMA user_version")[0][0])


def _table_hash(db: Path, table: str) -> str:
    rows = _rows(db, f"SELECT * FROM {table} ORDER BY 1")
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


def _enter_legacy_v45_world(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """S5a 口径：target=45、无 038 文件、037 在链外应用、链校验不要求 037。"""

    monkeypatch.setattr(migrator, "MIGRATION_STEPS", {k: v for k, v in migrator.MIGRATION_STEPS.items() if v <= 45})
    monkeypatch.setattr(migrator, "HUMAN_MEMORY_TARGET_SCHEMA_VERSION", 45)
    monkeypatch.setattr(schema, "HUMAN_MEMORY_TARGET_SCHEMA_VERSION", 45)
    monkeypatch.setattr(
        migrator, "_S4_HUMAN_MIGRATIONS",
        migrator._S4_HUMAN_MIGRATIONS - {EFFECT_CLOSURE_MIGRATION, CONTEXT_ROUTE_MIGRATION},
    )
    monkeypatch.setattr(schema, "_validate_s4_migration_chain", lambda *a, **k: None)
    legacy_dir = tmp_path / "migrations-v45"
    if not legacy_dir.exists():
        legacy_dir.mkdir(parents=True)
        for source in sorted(DEFAULT_MIGRATIONS_DIR.glob("*.sql")):
            if source.name.startswith("038_"):
                continue
            shutil.copy2(source, legacy_dir / source.name)
    monkeypatch.setattr(migrator, "DEFAULT_MIGRATIONS_DIR", legacy_dir)


async def _legacy_v45_database(tmp_path: Path, monkeypatch) -> Path:  # type: ignore[no-untyped-def]
    db = tmp_path / "state.db"
    _enter_legacy_v45_world(monkeypatch, tmp_path)
    await initialize_human_memory_program_state_db(db)
    assert _user_version(db) == 45
    # S5a 遗留状态：037 无 marker / 无链行 / 无恢复注册。
    assert _rows(db, "SELECT COUNT(*) FROM context_route_marker") == [(0,)]
    assert _rows(db, "SELECT COUNT(*) FROM human_memory_migration_chain WHERE migration_id=?", CONTEXT_ROUTE_MIGRATION) == [(0,)]
    monkeypatch.undo()
    return db


def _assert_context_route_registered(db: Path) -> None:
    sha = hashlib.sha256((DEFAULT_MIGRATIONS_DIR / CONTEXT_ROUTE_MIGRATION).read_bytes()).hexdigest()
    assert _rows(db, "SELECT migration_id,migration_sha256 FROM context_route_marker WHERE singleton=1") == [(CONTEXT_ROUTE_MIGRATION, sha)]
    assert _rows(db, "SELECT schema_version,migration_sha256 FROM human_memory_migration_chain WHERE migration_id=?", CONTEXT_ROUTE_MIGRATION) == [(45, sha)]
    registry = dict(_rows(db, "SELECT table_name,taxonomy FROM human_memory_recovery_table_registry WHERE table_name IN (?,?,?,?,?)", *CONTEXT_ROUTE_TABLES))
    assert registry == {name: "A" for name in CONTEXT_ROUTE_TABLES}
    triggers = {row[0] for row in _rows(db, "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'hm_recovery_fence_%'")}
    for name in CONTEXT_ROUTE_TABLES:
        for op in ("insert", "update", "delete"):
            assert f"hm_recovery_fence_{name}_{op}" in triggers, (name, op)


# --- 037 回补 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v45_migration_registered_in_human_chain(tmp_path: Path, monkeypatch) -> None:
    # ① 新库：037 在链内应用 → marker / 链行 / 恢复注册 + fence 触发器齐全，链校验通过。
    fresh = tmp_path / "fresh" / "state.db"
    await initialize_human_memory_program_state_db(fresh)
    assert _user_version(fresh) == 46
    _assert_context_route_registered(fresh)
    chain = dict(_rows(fresh, "SELECT migration_id,schema_version FROM human_memory_migration_chain"))
    assert chain[CONTEXT_ROUTE_MIGRATION] == 45 and chain[EFFECT_CLOSURE_MIGRATION] == 46
    schema._validate_s4_migration_chain(fresh, expected_user_version=46)
    # fence 触发器体：非 OPEN 即拒（与 v42 注册表其它 A 类表同一口径）。
    [(body,)] = _rows(fresh, "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='hm_recovery_fence_context_route_decisions_insert'")
    assert "human_memory_ingress_fenced" in body and "<>'OPEN'" in body
    # ② S5a 旧库（037 链外）：打开即回补（幂等），随后前向到 v46 且链校验（含 037）通过。
    legacy = await _legacy_v45_database(tmp_path / "legacy", monkeypatch)
    assert await repair_context_route_registration(legacy) is True
    _assert_context_route_registered(legacy)
    assert await repair_context_route_registration(legacy) is False
    await initialize_human_memory_program_state_db(legacy)
    assert _user_version(legacy) == 46
    _assert_context_route_registered(legacy)
    schema._validate_s4_migration_chain(legacy, expected_user_version=46)
    # ③ 未回补的旧库直接走启动入口：initialize 自身先回补再校验（不是 stable reject）。
    legacy2 = await _legacy_v45_database(tmp_path / "legacy2", monkeypatch)
    await initialize_human_memory_program_state_db(legacy2)
    _assert_context_route_registered(legacy2)
    assert inspect_startup_epoch(legacy2, approved_fresh_lane=False).epoch is StartupEpoch.HUMAN_RESUME


# --- 迁移前置 ---------------------------------------------------------------------


async def _legacy_v45_with_run(tmp_path: Path, monkeypatch, *, waiting: bool):  # type: ignore[no-untyped-def]
    """v45 旧库 + 一个 RUNNING（或 WAITING 观察）的 foreground Run（真实 queue store，在 v45 世界里建）。"""

    from deskpet.execution.foreground_queue import ForegroundQueueStore
    from tests.execution import test_foreground_queue as fq

    _enter_legacy_v45_world(monkeypatch, tmp_path)
    queue_db, primary_id, clock = await fq._ready(tmp_path / "queue")
    assert _user_version(queue_db) == 45
    store = ForegroundQueueStore(queue_db, clock=clock)
    await fq._enqueue(store, primary_id, 1)
    admission = await fq._claim_and_bind(store, sdk_run_id="sdk-run-cutover")
    await store.record_sdk_started(
        host_run_id=admission.host_run_id, sdk_run_id="sdk-run-cutover", owner_id=admission.owner_id,
        generation=admission.generation, sdk_event_id="sdk-start-cutover", idempotency_key="sdk-start-cutover",
    )
    if waiting:
        await store.record_reconciliation(
            host_run_id=admission.host_run_id, sdk_run_id="sdk-run-cutover", owner_id=admission.owner_id,
            generation=admission.generation, observed_state="BOUND_WAITING", idempotency_key="reconcile-waiting",
        )
    monkeypatch.undo()
    assert _user_version(queue_db) == 45
    return queue_db, store, admission


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "waiting,code",
    [(False, V46_CUTOVER_BLOCKED_FOREGROUND_RUN_ACTIVE), (True, V46_CUTOVER_BLOCKED_SDK_RUN_WAITING)],
)
async def test_v46_cutover_blocked_until_runs_are_quiescent(tmp_path: Path, monkeypatch, waiting: bool, code: str) -> None:
    queue_db, _store, admission = await _legacy_v45_with_run(tmp_path, monkeypatch, waiting=waiting)
    before = queue_db.read_bytes()
    with pytest.raises(InitializeError, match=code):
        await initialize_human_memory_program_state_db(queue_db)
    # 库字节不变（前置在 BEGIN 之前判定，零写入）、仍 v45、无 v46 表。
    assert queue_db.read_bytes() == before
    assert _user_version(queue_db) == 45
    assert _rows(queue_db, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='task_scope_closure_receipts'") == [(0,)]
    # 提示携带 host_run_id。
    with pytest.raises(InitializeError) as blocked:
        await initialize_human_memory_program_state_db(queue_db)
    assert admission.host_run_id in str(blocked.value)


@pytest.mark.asyncio
async def test_v46_cutover_precondition_passes_for_terminal_runs(tmp_path: Path) -> None:
    """终态（COMPLETED/…）Run 与 BOUND_TERMINAL 观察不阻塞前置判定。"""

    import aiosqlite

    from tests.sdk_adapters import s5b_closure_harness as ch

    env = await ch.bound_run(tmp_path, "sdk-run-done")
    facts = ch.FakeRunFacts(env.run_id)
    observed = await ch.observe_terminal(env, facts)
    # 先 WAITING 观察再 TERMINAL 观察（最新一条才算数），然后 Host 终态。
    await env.store.record_reconciliation(
        host_run_id=env.admission.host_run_id, sdk_run_id=env.run_id, owner_id=env.admission.owner_id,
        generation=env.admission.generation, observed_state="BOUND_WAITING", idempotency_key="reconcile-waiting",
    )
    env.clock.now += 1.0
    await env.store.record_reconciliation(
        host_run_id=env.admission.host_run_id, sdk_run_id=env.run_id, owner_id=env.admission.owner_id,
        generation=env.admission.generation, observed_state="BOUND_TERMINAL", idempotency_key="reconcile-terminal",
    )
    await ch.record_terminal(env, observed)
    assert _rows(env.db_path, "SELECT current_state FROM foreground_run_heads") == [("COMPLETED",)]
    async with aiosqlite.connect(env.db_path) as db:
        await migrator._assert_effect_closure_cutover_preconditions(db)  # 不抛


# --- 前向迁移 + rollback drill ----------------------------------------------------


@pytest.mark.asyncio
async def test_v46_forward_migration_and_rollback_drill_keep_evidence(tmp_path: Path, monkeypatch) -> None:
    from deskpet.memory.human_memory_program import HumanMemoryProgramStore
    from deskpet.memory.human_memory_service import build_host_typed_evidence

    db = tmp_path / "state.db"
    _enter_legacy_v45_world(monkeypatch, tmp_path)
    await initialize_human_memory_program_state_db(db)
    # v45 上落一条 raw evidence（迁移守恒的对象）——仍在 v45 世界里写（store 初始化会走启动入口）。
    program = HumanMemoryProgramStore(db)
    primary = await program.initialize_subject("actor-1")
    envelope, receipt = build_host_typed_evidence(
        subject="actor-1", authority_ref="host:test:v1", payload={"note": "before v46"},
        idempotency_key="cutover-evidence-1", source_ref="test/cutover-1", run_id="run-cutover",
    )
    from deskpet.task_scope.store import CanonicalTaskScopeStore

    async with CanonicalTaskScopeStore(db)._connection() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        await program.append_evidence_tx(conn, envelope, receipt, primary_conversation_id=primary.primary_conversation_id, committed_at=1.0)
        await conn.commit()
    monkeypatch.undo()
    assert _user_version(db) == 45
    hashes_before = {table: _table_hash(db, table) for table in EVIDENCE_TABLES}
    assert _rows(db, "SELECT COUNT(*) FROM human_memory_evidence") == [(1,)]
    backup = tmp_path / "drill" / "state.db"
    backup.parent.mkdir()
    shutil.copy2(db, backup)
    assert _user_version(backup) == 45

    # 前向：v45 → v46（Task 3/4 加列在 038 内：tool_name / result_envelope_json）。
    await initialize_human_memory_program_state_db(db)
    assert _user_version(db) == 46
    columns = {row[1] for row in _rows(db, "PRAGMA table_info(harness_evidence_reservations)")}
    assert "tool_name" in columns
    columns = {row[1] for row in _rows(db, "PRAGMA table_info(post_turn_invocation_attempts)")}
    assert "result_envelope_json" in columns
    assert {table: _table_hash(db, table) for table in EVIDENCE_TABLES} == hashes_before
    _assert_context_route_registered(db)

    # 旧 runtime 打开 v46：稳定拒绝、字节不变。
    frozen = db.read_bytes()
    future = inspect_startup_epoch(db, approved_fresh_lane=True, maximum_human_schema_version=45)
    assert future.epoch is StartupEpoch.FUTURE and future.reason_code == "human_memory_future_epoch_unsupported"
    monkeypatch.setattr(schema, "HUMAN_MEMORY_TARGET_SCHEMA_VERSION", 45)
    with pytest.raises(HumanMemoryProgramEpochError, match="human_memory_program_future_database_unsupported"):
        await initialize_human_memory_program_state_db(db)
    monkeypatch.undo()
    assert db.read_bytes() == frozen

    # rollback drill：回退 = 用迁移前备份替换（v46 库本身不删 evidence）；备份里 evidence 守恒且仍可被 v46 前向。
    assert {table: _table_hash(backup, table) for table in EVIDENCE_TABLES} == hashes_before
    assert _user_version(backup) == 45
    await initialize_human_memory_program_state_db(backup)
    assert _user_version(backup) == 46
    assert {table: _table_hash(backup, table) for table in EVIDENCE_TABLES} == hashes_before


# --- 旧 checkpoint 稳定隔离 --------------------------------------------------------


def test_old_checkpoint_before_project_effect_policy_is_isolated(monkeypatch) -> None:
    from simple_harness import RunId
    from simple_harness.tools import RuntimeToolCatalogError

    import main
    from deskpet.sdk_adapters import tool_authority as ta
    from deskpet.sdk_adapters.context_authority import canonical_sha256
    from tests.sdk_adapters.test_s5b_acceptance_matrix import _Inventory

    specs = [
        {"name": "write_file", "description": "Write", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}},
        {"name": "read_file", "description": "Read", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}},
    ]
    catalog = {
        "generation": 1, "content_fingerprint": canonical_sha256(specs), "specs": specs,
        "schema_fingerprints": {item["name"]: canonical_sha256(item["input_schema"]) for item in specs},
    }
    inventory = (_Inventory("write_file", "async", "write_file"), _Inventory("read_file", "async", "read_file"))

    def prepare(registry: ta.SdkRunToolAuthorityRegistry) -> None:
        registry.prepare_run(
            run_id="run-old", session_id="s", request_id="r", root_run_id="root", task_scope_id="task",
            workspace_root=None, catalog=catalog, inventory=inventory,
        )

    run = RunId("run-old")
    # PROJECT_EFFECT policy 改变前的世界：write_file 没有覆盖（SDK 默认 non_project_effect）。
    monkeypatch.setattr(ta, "SDK_TOOL_EXECUTION_POLICY_OVERRIDES", {k: v for k, v in ta.SDK_TOOL_EXECUTION_POLICY_OVERRIDES.items() if k != "write_file"})
    old = ta.SdkRunToolAuthorityRegistry()
    prepare(old)
    old_exposure = old.resolve_exposure(run)
    old_exposure.restore(run, None)
    checkpoint = old_exposure.checkpoint(run)
    monkeypatch.undo()
    # 当前世界恢复旧 checkpoint：catalog fingerprint（含执行策略）不同 → 稳定隔离码，不是静默降级。
    current = ta.SdkRunToolAuthorityRegistry()
    prepare(current)
    exposure = current.resolve_exposure(run)
    with pytest.raises(RuntimeToolCatalogError) as stale:
        exposure.restore(run, checkpoint)
    assert stale.value.code == "catalog_state_fingerprint_stale"
    # Host 隔离：标记终态、登记不可用 Run，不伪造 authority。
    resolver = SimpleNamespace(mark_terminal=lambda *a, **k: None)
    saved = set(main._sdk_unavailable_tool_authority_runs)
    try:
        main._isolate_unrestorable_sdk_tool_authority(
            sdk_run_id=run.value, metadata={"root_run_id": "root"}, provider_binding_resolver=resolver,
            tool_authorities=current, error=stale.value,
        )
        assert run.value in main._sdk_unavailable_tool_authority_runs
        with pytest.raises(KeyError):
            current.resolve(run)
    finally:
        main._sdk_unavailable_tool_authority_runs.clear()
        main._sdk_unavailable_tool_authority_runs.update(saved)
