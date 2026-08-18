# 验收标准：记忆 SDK 接入 slice 1 — 恢复 host SessionDB 会话账本

## 功能验收条款
| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| AC-1 | 恢复会话账本 | `deskpet/memory/{session_db,schema,memory_v2_schema,migrator,schema_v2_migrator}.py` 与 `migrations/` 已从 `809c30b9^` 恢复 | 必须 |
| AC-2 | re-home 引用修正 | `session_db.py`、`migrator.py` 的 `companion_message_projection` 指向 `deskpet.companion`，`pyproject.toml` 重新声明 `deskpet.memory.migrations` | 必须 |
| AC-3 | 可导入 | `import deskpet.memory.session_db` 成功，`SessionDB` 含 `append_message`/`get_recent_messages`/`ensure_session` | 必须 |
| AC-4 | 可运行 | `SessionDB` 对临时 state.db 完成 initialize→ensure_session→append_message→get_recent_messages，消息内容往返一致 | 必须 |

## 非功能 / 边界
- 本 slice 只恢复会话账本，不接 SDK `MemoryBackend`（下一个 slice）。
- 迁移必须幂等、可降级（sqlite-vec 失败不阻断启动）。
