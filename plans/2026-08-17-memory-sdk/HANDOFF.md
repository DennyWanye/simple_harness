# HANDOFF — Host 旧记忆实现清理（给下一个 agent）

> 状态：边界与接口契约已落地，并已按第二轮审查修正；本文件是给执行“删除 app 侧旧记忆代码”的 agent 的交接说明。
> 更新：2026-08-18

## 0. 一句话

旧记忆实现已删除，host 侧只保留接口契约与适配层。本任务只删**旧实现的死引用**，
**不删接口**。

## 0.1 修订记录（2026-08-18 第二轮，待确认）

清理 agent 复核发现一个架构级错误并已修正，**本结论待用户确认后再继续 SDK 代码**：

- **Block 1（架构边界）**：`session_db` 不是 `MemoryBackend` 的薄适配器。它是 host
  会话账本，public 方法面约 80 个（消息/交付状态/provider 绑定/context usage/目标与
  todo/companion ingress），而 `MemoryBackend` 仅约 18 个；两者只在
  `append_message` / `get_recent_messages` / `initialize` / `close` 相交。
  → `contracts.py` 已补独立的 `SessionDB` Protocol（80 方法全列），与 `MemoryBackend`
  分开；`MEMORY_SDK_BOUNDARY.md` 与本文档相应表述已修正。
- **Block 2（Embedder 契约）**：host 旧调用是 `await embedder.encode(texts)` +
  `embedder.is_mock()`；`contracts.py` 的 `Embedder` Protocol 已改为
  `dim` / `is_mock` / `async encode`，并注明 SDK 同步 `embed` 由 host 适配层做
  sync→async 包装。
- **Block 3（main.py 清理执行）**：死块是整段约 1150 行 `try`（约 2529–3684），
  删整段 + `except` 降级转无条件执行 + 约 40 个被引用全局变量统一置 `None`。
- **Block 4（Message 模型）**：补 `root_run_id` / `workflow_event_id` / `tool_call_id`，
  并给 `created_at` 默认值 `0.0`。

**当前状态**：contracts.py / 两份边界文档已改完，`py_compile` + import 通过；
**在用户确认清理 agent 侧无问题之前，不再继续 SDK 代码。**

## 1. 先读这两个事实源（接口已落地的依据）

- [`ARCHITECTURE/MEMORY_SDK_BOUNDARY.md`](../../ARCHITECTURE/MEMORY_SDK_BOUNDARY.md)
- [`backend/deskpet/memory/contracts.py`](../../backend/deskpet/memory/contracts.py)

## 2. 边界（已定）

- **进 SDK**：`MemoryBackend`、`Embedder`、`WorldModelPort`
- **留 host**：
  - `SessionDB` → `deskpet.memory`（host 会话账本，方法面远大于 MemoryBackend；
    **不是** MemoryBackend 的薄适配器，仅在消息存取处委托）
  - `ContextSnapshotStore` → `deskpet.agent`
  - `CompanionMessageProjection` → `deskpet.companion`
  - coverage 规划（`ContextSegmentStore` 等）→ `deskpet.agent`

## 3. 已完成的 re-home（不要删）

- `deskpet/memory/context_snapshot_store.py` → `deskpet/agent/context_snapshot_store.py`
- `deskpet/memory/context_segment_store.py` → `deskpet/agent/context_segment_store.py`
- `deskpet/memory/companion_message_projection.py` → `deskpet/companion/companion_message_projection.py`

引用方已同步为新路径（`main.py`、`agent_loop.py`、`tool_context_persistence.py`、
`session_history_planner.py`、`session_history_tools.py`、`notifications.py` 及
相关 `backend/tests/*`）。

## 4. 要清理的旧实现死引用

### 4.1 `backend/main.py`

这是一段约 **1150 行**的死 `try` 块（约 line 2529 起，到 line 3684 的 `except`）。
清理要**整段删除 try 块本身**（不是只删 import），并把 `except` 里的降级逻辑转成
无条件执行。该块内赋值了约 40 个仍被 `main.py` 别处引用的全局变量（
`_file_memory` / `_embedder` / `_vector_worker` / `_facts_store` / `_retriever` /
`_skill_loader` / `_managed_skill_projection` / `_session_db` / `_message_chunker` /
`_context_snapshot_store` / `_context_segment_store` / `_session_history_planner`
等）——删完后这些全局必须统一置 `None`，否则会 `NameError`。

死块内 import 的已删除模块（按模块名，不要按行号）：

```text
deskpet.memory.file_memory
deskpet.memory.manager
deskpet.memory.embedder
deskpet.memory.vector_worker
deskpet.memory.image_worker
deskpet.memory.retriever
deskpet.memory.facts
deskpet.memory.chunker
deskpet.memory.reflection
deskpet.memory.enhanced_retriever
deskpet.memory.reranker
deskpet.memory.query_rewriter
deskpet.memory.entity_extractor
deskpet.memory.workspace
deskpet.memory.summarizer
deskpet.memory.eval.feedback
deskpet.memory.curation
deskpet.memory.schema_v2_migrator
```

保留仍然有效、且属于 host 侧的接线：

- `deskpet.memory.session_db`（SessionDB，host 会话账本，**不删**，不是 MemoryBackend 薄适配器）
- `deskpet.agent.context_snapshot_store` / `deskpet.agent.context_segment_store`（新路径）
- `deskpet.agent.session_history_planner`、`deskpet.agent.context_request_planner`

### 4.2 `backend/deskpet/memory/embedder_worker.py`

删除该旧 embedder 子进程 stub，并同步：

- `backend/deskpet/frozen_worker_dispatch.py`：移除 `EMBEDDER_WORKER_MODULE` 与
  `dispatch_frozen_worker_if_requested` 中的 embedder 分支。
- `backend/scripts/debug_embedder_worker.py`：删除或改为不再引用
  `deskpet.memory.embedder_worker`。

### 4.3 `backend/tests/*`

删除或迁移引用已删除记忆模块的测试，涉及：

```text
deskpet.memory.migrator
deskpet.memory.memory_v2_schema
deskpet.memory.file_memory
deskpet.memory.manager
deskpet.memory.retriever
deskpet.memory.summarizer
deskpet.memory.eval
```

（`session_db` / `context_segment_store` / `context_snapshot_store` 仍存在或已
re-home，不在此删除范围。）

## 5. 红线（绝对不能删）

- `backend/deskpet/memory/contracts.py`
- `backend/deskpet/memory/__init__.py`
- `ARCHITECTURE/MEMORY_SDK_BOUNDARY.md`

## 6. 收尾验证

1. 残留检查（`*.bak*` 除外）：

   ```bash
   rg 'deskpet\.memory\.(file_memory|manager|embedder|vector_worker|image_worker|retriever|facts|chunker|reflection|enhanced_retriever|reranker|query_rewriter|entity_extractor|workspace|summarizer|eval|curation|schema_v2_migrator|migrator|memory_v2_schema)' backend
   ```

   应无结果（或仅剩 `*.bak*` 备份文件）。

2. 关键模块 `import` + `py_compile` 通过。
3. 按 `AGENTS.md` 更新 `ARCHITECTURE/` 对应事实源（模块完成度 / 边界变化），并更新
   `ARCHITECTURE/MEMORY_SDK_BOUNDARY.md` 第 5 节“已知待办”的状态。
