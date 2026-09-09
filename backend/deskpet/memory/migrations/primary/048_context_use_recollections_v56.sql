-- 2026-09-09 HM-TO-A6 事件 AA：用途围栏的有界重采回执。
--
-- 被绑定的定型召回带一段有限的用途授权租约（`RecallContext.expires_at`），
-- 权威 epoch 也会在一轮之内前进。当这一轮已经交给模型的绑定不再能被授权时，
-- Host 在 snapshot 边界用**同一个召回计划、自己的幂等用途**重采一次，改绑到
-- Memory 现在肯签发收据的结果上。一行 = 一次重采回执，
-- (sdk_run_id, effect_id, generation) 唯一，只追加、不可改删。
--
-- 这行不授予任何东西：它只记录「原绑定 -> 重采绑定」。bindings_json 逐条带
-- public_payload_hash 与 source_ref/source_revision，重放与校验路径据此证明
-- **模型已经看到的那串字节逐字未变**——变了就 fail closed，绝不改绑。
CREATE TABLE context_use_recollections (
    recollection_id   TEXT PRIMARY KEY,
    sdk_run_id        TEXT NOT NULL,
    effect_id         TEXT NOT NULL,
    generation        INTEGER NOT NULL CHECK(generation >= 1),
    reason_code       TEXT NOT NULL,
    bound_result_id   TEXT NOT NULL,
    bound_result_hash TEXT NOT NULL CHECK(length(bound_result_hash)=64),
    decision_id       TEXT NOT NULL,
    decision_hash     TEXT NOT NULL CHECK(length(decision_hash)=64),
    result_id         TEXT NOT NULL,
    result_hash       TEXT NOT NULL CHECK(length(result_hash)=64),
    authority_epoch   INTEGER NOT NULL CHECK(authority_epoch >= 1),
    expires_at        REAL NOT NULL CHECK(expires_at >= 0),
    bindings_json     TEXT NOT NULL,
    recollection_hash TEXT NOT NULL CHECK(length(recollection_hash)=64),
    recorded_at       REAL NOT NULL CHECK(recorded_at >= 0),
    UNIQUE(sdk_run_id, effect_id, generation)
);
CREATE INDEX context_use_recollections_by_effect
    ON context_use_recollections(sdk_run_id, effect_id, generation);
CREATE INDEX context_use_recollections_by_result
    ON context_use_recollections(sdk_run_id, result_id);
CREATE TRIGGER context_use_recollections_no_update BEFORE UPDATE ON context_use_recollections
BEGIN SELECT RAISE(ABORT,'context_use_recollections_append_only'); END;
CREATE TRIGGER context_use_recollections_no_delete BEFORE DELETE ON context_use_recollections
BEGIN SELECT RAISE(ABORT,'context_use_recollections_append_only'); END;
