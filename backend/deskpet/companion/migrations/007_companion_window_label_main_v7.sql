-- 007 (2026-08-04 Workbench UI 改版, behavior-contract B5):
-- message-panel 窗口并入主窗后, companion_action 特权租约的合法窗口标签由
-- 'message-panel' 迁移为 'main'。001 建表 CHECK 把允许对写死在
-- profile_control_leases 上, 且 schema.py 按 user_version 永不重跑已应用
-- 迁移 —— SQLite 不能 ALTER CHECK, 按官方 12 步规范重建表:
-- CREATE 新表(临时名) → INSERT SELECT 拷数据 → DROP 旧表 → RENAME 临时名
-- → 重建部分唯一索引。
--
-- 顺序说明(与 006 的 rename-old-first 模式**不同**, 刻意为之): 本表有留存
-- 的外键子表 profile_control_commands 以复合外键 (device_scope,
-- connection_id) 引用本表(001:88)。若先 RENAME 旧表, SQLite(非 legacy
-- alter 语义)会把子表 REFERENCES 子句连带改写到临时名上, 旧表 DROP 后子表
-- 悬空引用。12 步顺序下子表 REFERENCES 始终按名指向 profile_control_leases,
-- 重建完成即指向新表。schema.py 的迁移包裹(PRAGMA foreign_keys=OFF + 事后
-- PRAGMA foreign_key_check :132)使流程安全; 全行拷贝保证引用完整, 007 的
-- 验证项显式断言 foreign_key_check 零违例。

-- 全列照抄 001:39-63, 仅 CHECK 的允许对改变。双臂结构必须保留:
-- challenged 臂("challenged 行必须 NULL 标签"的不变量)照抄不动,
-- 非 challenged 臂由 (main,identity_bind)|(message-panel,companion_action)
-- 收敛为 window_label='main' + scope 二选一。
CREATE TABLE profile_control_leases_v7 (
    device_scope TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    requested_window_label TEXT NOT NULL,
    requested_scope TEXT NOT NULL,
    window_label TEXT,
    scope TEXT,
    control_epoch INTEGER NOT NULL CHECK(control_epoch >= 1),
    challenge_hash TEXT NOT NULL,
    last_seq INTEGER NOT NULL DEFAULT 0 CHECK(last_seq >= 0),
    backend_process_instance_id TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('challenged','active','revoked','expired')),
    reason_code TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version = 1),
    CHECK (
      (status = 'challenged' AND window_label IS NULL AND scope IS NULL) OR
      (status <> 'challenged' AND window_label = 'main' AND
       scope IN ('identity_bind','companion_action'))
    ),
    PRIMARY KEY(device_scope, connection_id)
) WITHOUT ROWID;

-- 拷数据: 仅改写非 challenged 行的 window_label('message-panel'→'main');
-- challenged 行标签本为 NULL(旧 CHECK 不变量), 无需也不得改写。
-- requested_window_label 审计列**故意不改写**: 该列无 CHECK、仅审计语义
-- (记录连接建立时声明的原始标签), 保留历史原值防过度清洗。
INSERT INTO profile_control_leases_v7
SELECT
    device_scope,
    connection_id,
    requested_window_label,
    requested_scope,
    CASE
      WHEN status <> 'challenged' AND window_label = 'message-panel' THEN 'main'
      ELSE window_label
    END,
    scope,
    control_epoch,
    challenge_hash,
    last_seq,
    backend_process_instance_id,
    issued_at,
    expires_at,
    revoked_at,
    status,
    reason_code,
    schema_version
FROM profile_control_leases;

DROP TABLE profile_control_leases;
ALTER TABLE profile_control_leases_v7 RENAME TO profile_control_leases;

-- 部分唯一索引重建(随旧表 DROP 一并消失, 001:64-66 原样)。
CREATE UNIQUE INDEX uq_profile_control_active
ON profile_control_leases(device_scope, window_label, scope)
WHERE status = 'active';
