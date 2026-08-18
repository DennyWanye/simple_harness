# Plan：记忆 SDK 接入 slice 2 — 接入 MemoryBackend

## 主要矛盾
SessionDB 是 host 会话账本（保留自己的 messages 表 + FTS5/sqlite-vec），SDK
`MemoryBackend` 是认知记忆。本 slice 做**双写 + 委托**：append 时把消息喂给 SDK，
认知能力（recall/facts/twin）经 SessionDB 委托给 SDK；未接入时零开销降级。

## 关联验收标准
覆盖 AC-5、AC-6、AC-7、AC-8。

## 任务清单
### Task 1 — SessionDB 支持注入 backend [AC-5, AC-6]
- 文件：`backend/deskpet/memory/session_db.py`
- 修改：`__init__` 加 `memory_backend`；`initialize`/`close` 联动；`append_message` 双写喂 SDK（失败只 warn）。

### Task 2 — 认知能力委托 [AC-7]
- 文件：`backend/deskpet/memory/session_db.py`
- 修改：新增 `recall`/`get_facts`/`get_digital_twin`，无 backend 降级。

### Task 3 — 测试 [AC-5..AC-8]
- 文件：`backend/tests/test_memory_sdk_integration.py`
- 修改：降级 + 接线双写 smoke。
