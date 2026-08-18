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

## Slice 2 追加验收条款（接入 MemoryBackend）
| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| AC-5 | 可注入 backend | `SessionDB(db_path, memory_backend=...)` 支持注入 SDK `MemoryBackend`，默认 None 行为不变 | 必须 |
| AC-6 | 双写喂入 | 有 `memory_backend` 时，`append_message` 把消息喂给 SDK；`initialize`/`close` 联动 backend 生命周期 | 必须 |
| AC-7 | 认知能力委托 | `SessionDB.recall/get_facts/get_digital_twin` 委托 SDK；无 backend 时返回空/None | 必须 |
| AC-8 | 后端 smoke | `SessionDB + SQLiteMemoryBackend(auto_extract_facts=True)` 一起跑，append 后 facts/recall/twin 正确 | 必须 |

## Slice 3 追加验收条款（main.py 接线 + 后端 smoke）
| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| AC-9 | 真实接线 | `main.py` 构造 `SessionDB(memory_backend=SQLiteMemoryBackend(memory.db, auto_extract_facts=True))` 并注册 `service_context`，失败降级 `None` 不阻断启动 | 必须 |
| AC-10 | 编译通过 | `main.py` 经 `py_compile` 无语法/import 破坏 | 必须 |
| AC-11 | 后端 smoke | 该构造模式的 initialize→append→facts/recall/twin 后端级跑通（用临时 `data/state.db`+`data/memory.db` 模拟 `user_data_dir`） | 必须 |

## Slice 4 追加验收条款（真实后端启动 smoke）
| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| AC-12 | 真实 import | `import main` 成功，模块级 `_session_db`/`_memory_backend` 非 None 且 `service_context` 注册 SessionDB | 必须 |
| AC-13 | SessionDB 初始化 | 真实 `_session_db.initialize()` 迁移成功，`append_message`+`get_recent_messages` 往返一致 | 必须 |
| AC-14 | SDK 认知记忆生效 | 走一条用户消息后，真实后端实例的 facts/recall/twin 正确 | 必须 |
