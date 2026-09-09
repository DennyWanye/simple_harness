-- 2026-09-09 HM-TO-A6 事件 AI：把 `context_use_recollection` 收进归档证据词表。
--
-- 事件 AA 的有界重采回执（v56 `context_use_recollections`）在 ledger 写事务里以
-- `kind='context_use_recollection'` 走 `ingest_ledger_fact_tx`，但这个种类当时既
-- 没进 `RESERVATION_KINDS`、没进协议侧 `_EXECUTION_KINDS`，也没进本表的 CHECK。
-- 于是第 12 次 A6 第 18 轮：三次 `glob` 读成功入档、turn 13 快照回执落库，随后
-- 组装 provider 请求时租约进入余量触发重采，回执入档被词表拒绝，抛出
-- `execution_evidence_kind_rejected`（裸 ValueError）打死整个 Run。
--
-- 词表有三份拷贝，必须同时前进；本步只动 SQL 这一份。SQLite 无法就地改 CHECK，
-- 所以按官方 12 步做法重建表：新表 -> 拷贝 -> 换名 -> 重建索引与全部触发器。
-- 列定义逐字不变，因此 `human_memory_recovery_table_registry.columns_json`
-- 仍然成立，不需要重新登记（重新登记反而会与 v46 的行冲突）。
-- 重建会连带丢掉表上的触发器，包括 `_register_recovery_tables` 建的三个恢复围栏，
-- 所以它们在下面按原文逐字重建。

CREATE TABLE harness_evidence_reservations_v57 (
    reservation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_scope_id TEXT NOT NULL,
    source_sequence INTEGER NOT NULL CHECK(source_sequence > 0),
    source_event_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN (
        'provider_invocation','tool_invocation','context_snapshot','route_decision',
        'context_use_recollection','run_terminal'
    )),
    status TEXT NOT NULL CHECK(status IN ('reserved','ingested','abandoned')),
    reserved_at REAL NOT NULL,
    resolved_at REAL,
    -- Task 2 review F-2: the Tool name is recorded at reservation time so an
    -- abandoned PROJECT_EFFECT (SDK ledger non-terminal at Run terminal) can be
    -- tombstoned with its effect class and still count as material dirt.
    tool_name TEXT,
    UNIQUE(run_id, source_sequence),
    CHECK((status='reserved') = (resolved_at IS NULL)),
    FOREIGN KEY(task_scope_id) REFERENCES task_scopes(task_scope_id)
);

INSERT INTO harness_evidence_reservations_v57(
    reservation_id,run_id,task_scope_id,source_sequence,source_event_id,kind,
    status,reserved_at,resolved_at,tool_name)
SELECT reservation_id,run_id,task_scope_id,source_sequence,source_event_id,kind,
       status,reserved_at,resolved_at,tool_name
FROM harness_evidence_reservations;

DROP TABLE harness_evidence_reservations;

ALTER TABLE harness_evidence_reservations_v57 RENAME TO harness_evidence_reservations;

CREATE INDEX idx_harness_evidence_reservations_run
ON harness_evidence_reservations(run_id, status, source_sequence);

-- v46 的只追加触发器（逐字重建）。
CREATE TRIGGER harness_evidence_reservations_no_delete BEFORE DELETE ON harness_evidence_reservations BEGIN SELECT RAISE(ABORT,'effect_closure_append_only'); END;

-- v46 的单调状态守卫（逐字重建）。
CREATE TRIGGER harness_evidence_reservations_guard BEFORE UPDATE ON harness_evidence_reservations
BEGIN
    SELECT CASE
        WHEN NEW.reservation_id <> OLD.reservation_id
             OR NEW.run_id <> OLD.run_id
             OR NEW.task_scope_id <> OLD.task_scope_id
             OR NEW.source_sequence <> OLD.source_sequence
             OR NEW.source_event_id <> OLD.source_event_id
             OR NEW.kind <> OLD.kind
             OR NEW.reserved_at <> OLD.reserved_at
             OR NEW.tool_name IS NOT OLD.tool_name
        THEN RAISE(ABORT,'harness_evidence_reservation_identity_immutable')
        WHEN OLD.status <> 'reserved' AND (
             NEW.status <> OLD.status OR NEW.resolved_at IS NOT OLD.resolved_at)
        THEN RAISE(ABORT,'harness_evidence_reservation_monotonic')
        WHEN OLD.status = 'reserved' AND NEW.status NOT IN ('ingested','abandoned')
        THEN RAISE(ABORT,'harness_evidence_reservation_monotonic')
    END;
END;

-- v42 恢复围栏（`migrator._register_recovery_tables` 的原文，逐字重建）。
CREATE TRIGGER "hm_recovery_fence_harness_evidence_reservations_insert" BEFORE INSERT ON "harness_evidence_reservations" WHEN (SELECT state FROM human_memory_recovery_fence WHERE singleton=1)<>'OPEN' BEGIN SELECT RAISE(ABORT,'human_memory_ingress_fenced'); END;
CREATE TRIGGER "hm_recovery_fence_harness_evidence_reservations_update" BEFORE UPDATE ON "harness_evidence_reservations" WHEN (SELECT state FROM human_memory_recovery_fence WHERE singleton=1)<>'OPEN' BEGIN SELECT RAISE(ABORT,'human_memory_ingress_fenced'); END;
CREATE TRIGGER "hm_recovery_fence_harness_evidence_reservations_delete" BEFORE DELETE ON "harness_evidence_reservations" WHEN (SELECT state FROM human_memory_recovery_fence WHERE singleton=1)<>'OPEN' BEGIN SELECT RAISE(ABORT,'human_memory_ingress_fenced'); END;
