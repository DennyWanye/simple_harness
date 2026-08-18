# Plan：记忆 SDK 接入 slice 1 — 恢复 host SessionDB 会话账本

## 主要矛盾
接入 `simple-harness-memory-sdk` 前，host 的会话账本（SessionDB + state.db schema +
migrations）在 `809c30b9` 被删成了 stub。本 slice 先把会话账本**机械恢复**，并把
re-home 后的 `companion_message_projection` 引用修正；SDK MemoryBackend 消息委托放下一个 slice。

## 关联验收标准
覆盖 AC-1、AC-2、AC-3、AC-4。

## 任务清单
### Task 1 — 机械恢复会话账本文件 [AC-1]
- 从 `809c30b9^` 恢复 `deskpet/memory/{session_db,schema,memory_v2_schema,migrator,schema_v2_migrator}.py` + `migrations/`。

### Task 2 — 修正 re-home 引用 [AC-2]
- `session_db.py`、`migrator.py` 的 `companion_message_projection` import 改为 `deskpet.companion.companion_message_projection`。
- 恢复 `deskpet.companion.companion_message_projection.py` 为完整版（含 `OwnerMemoryReadScopeV1` + migrate）。
- `backend/pyproject.toml` 重新声明 `deskpet.memory.migrations` 包与 package-data。

### Task 3 — 验证 [AC-3, AC-4]
- import + `SessionDB` 对临时 state.db 的 initialize/ensure_session/append_message/get_recent_messages 往返。
