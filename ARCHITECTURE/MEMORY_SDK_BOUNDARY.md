# Memory SDK 边界与 Host 接口契约

> 最后更新：2026-08-20
> 设计来源：`plans/2026-08-17-memory-sdk/00-ARCHITECTURE.md`
> SDK 独立仓库：`simple-harness-memory-sdk`

本文档是记忆系统拆分后的 **边界事实源**：哪些能力进 SDK，哪些留在 host，以及
host 唯一依赖的接口契约（Port）。

## 0. 一句话

host 依赖接口（Port），不依赖 SDK 的具体实现。旧实现已删除，host 侧只保留
接口契约与适配层；SDK 上线时是“换个 MemoryBackend 实现”，host 其它代码不动。

## 1. SDK 边界

**进 SDK**（独立仓库 `simple-harness-memory-sdk`）：

| 能力 | 说明 |
|---|---|
| `MemoryBackend` | L1/L2 消息、L3 facts、digital twin、RRF recall、summarize、decay |
| `Embedder` | 文本向量化（BGE-M3，带 mock 降级） |
| `WorldModelPort` | 时间 / 事件 / 地理 / 知识边界 |

**留 host**：

| 模块 | 归处 | 说明 |
|---|---|---|
| `SessionDB`（会话账本） | `deskpet.memory`（host） | 消息/交付状态/provider 绑定/context usage/目标与 todo/companion ingress；仅“消息存取”4 个方法委托 `MemoryBackend` |
| `ContextSnapshotStore` | `deskpet.agent` | 上下文快照 CAS，agent 关注 |
| `CompanionMessageProjection` | `deskpet.companion` | companion 通知投影 |
| coverage 规划（`ContextSegmentStore` 等） | `deskpet.agent` | coverage / 段存储 |

## 2. Host 侧接口契约

host 唯一依赖的契约定义在：

- `backend/deskpet/memory/contracts.py`

包含四个 `Protocol`：

- `MemoryBackend`（核心：append/get_recent/recall/get_facts/get_digital_twin/
  summarize_old_sessions/daily_decay 等）
- `SessionDB`（host 会话账本，方法面远大于 MemoryBackend，**不是**其薄适配器）
- `Embedder`（dim / is_mock / async encode；SDK 同步 embed 由适配层做 sync→async 包装）
- `WorldModelPort`（temporal / events / weather / knowledge boundary）

契约中同时定义宿主自有的最小数据模型 `Message` / `Fact` / `Hit`，刻意不 import
SDK 类型，避免反向耦合。

## 3. 已完成的 re-home

- `deskpet.memory.context_snapshot_store.py` → `deskpet.agent.context_snapshot_store.py`
- `deskpet.memory.context_segment_store.py` → `deskpet.agent.context_segment_store.py`
- `deskpet.memory.companion_message_projection.py` → `deskpet.companion.companion_message_projection.py`

引用方已同步改为新路径（`agent_loop.py`、`tool_context_persistence.py`、
`session_history_planner.py`、`session_history_tools.py`、`notifications.py`、
`main.py`）。

## 4. `deskpet.memory` 当前结构

```
backend/deskpet/memory/
├── __init__.py           # 导出契约
├── contracts.py          # MemoryBackend / SessionDB / Embedder / WorldModelPort Protocol
├── session_db.py         # host 会话账本（留 host，消息存取双写委托 MemoryBackend）
├── recall_adapter.py     # SDK 认知记忆 -> product SDK memory tool 的 host 适配层
├── schema.py             # host 账本 SQLite schema
├── schema_v2_migrator.py # schema v2 迁移
├── memory_v2_schema.py   # v2 schema 定义
├── migrator.py           # 迁移器
├── migrations/           # 迁移脚本
└── eval/                 # 评测脚本
```

## 5. 已知待办 / 边界说明

- **已接入（2026-08-19）**：`main.py` 构造 `SQLiteMemoryBackend` 并传入
  `SessionDB(memory_backend=...)`；`SessionDB.append_message` 与
  `append_user_message_with_growth_outbox` 均在用户消息落盘后双写 `MemoryBackend`。
  `recall_adapter.py` 把 `MemoryBackend.recall` 翻译成 product SDK 的 owner-scoped
  `recall_readonly`，`CompanionRunMemoryScopeResolver` 提供 `resolve_for_run`。
- **旧记忆装配死代码已清理（2026-08-19，HEAD `5b781bf6`）**：`main.py` 内原约 1150 行
  引用已删除模块（`file_memory`/`manager`/`vector_worker` 等）的 best-effort 死 `try`
  已整段移除，约 40 个相关全局统一置 `None`（现 `main.py:2531-2553` 为紧凑的
  SessionDB+MemoryBackend 接线块）；`embedder_worker.py` 已随旧栈删除。
- **产品 SDK memory tools 已接通（2026-08-20）**：`memory_recall` 与 `memory_search` 都从显式
  product catalog 动态绑定 `OwnerMemoryRecallQueryAdapter -> MemoryBackend.recall`。Companion Run
  有唯一 generation-0 owner scope 时继续使用其 session affinity；普通单用户 SDK Run 仅在 resolver
  抛精确 `owner_memory_scope_missing_or_ambiguous` 时走全局只读 recall。resolver 返回 None、错误类型
  或其他 scope 异常均 fail closed，避免权限边界误降级。真实 SQLite backend → catalog → SDK
  dispatcher canary 与 DeepSeek Computer Use 均验证两工具成功。
- **curation 未接新 SDK**：`oh4_curation_skipped reason=no_facts_store` —— curation 仍在找旧
  `service_context["facts_store"]`，未指向 SDK 的 `MemoryBackend.get_facts`。
- `session_db.py` 是 **host 会话账本**（约 80 个 public 方法），
  不是 `MemoryBackend` 的薄适配器。二者只在 `append_message` / `get_recent_messages`
  / `initialize` / `close` 相交，其余方法由 host 自行实现。
- `context_segment_store` / `context_snapshot_store` 仍是 stub；其中 coverage 段
  存储的真实语义（`get`、`status` 等）待重实现，属"coverage 规划"宿主要求，不在
  SDK 边界内。
