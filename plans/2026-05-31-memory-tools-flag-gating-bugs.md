# memory 工具 flag 门控缺陷（F3 / F4）

## gen-1 基线定性（2026-05-31 查实，决定"是 bug 还是功能没接"）

| | 定性 | git 依据 |
|---|---|---|
| **F3 = Bug A** `memory_search not bound` | ✅ **真 bug（误连坐），必须修** | `memory_search` 在 `0390e3a`（WI-T3.1，**独立 tools/v3 线**）就是真实现（docstring "(query,top_k)→facts.search"），设计上就要 `_facts_store`；却在 `6cfbe0f`（Stage 2）被错误塞进 `memory_forget` flag 的 if 块连坐 |
| **F4 = Bug B** `workspace_state` 0 行 | ⚠️ **功能默认关（非回归），属产品决策** | `workspace_memory`（WI-M1.6）自 `3228e24` 引入起**一直** flag-gated 默认 False，`set_workspace_store` 从没在 flag 关时跑过 → gen-1 本就没接通 code 工作记忆 |

**结论**：
- **F3 必须修** —— 基础只读工具被无关 flag 误关。改法 = bind 解耦（见下 Bug A 修法）。
- **F4 是决策不是 bug** —— "code 工作记忆要不要默认开"是产品选择；若要开，须重测
  Strangler-Fig byte-level consistency（flag 关时字节级等同 gen-1）。**不能当 bug 顺手改。**

---


**发现来源**：2026-05-31 F1 真机 windows-mcp GUI 真测，用户手工在 Code 模式
（项目 `G:\projects\test-research-helper` / ResearchFlow）跑两轮对话时暴露。
**数据库铁证**：`G:\projects\deskpet-stage2-f1f2\.dev-userdata\data\state.db`
`messages` 表 id=10/11（`memory_search not bound`）+ `workspace_state` 表 0 行。

> 真测实录：第一轮桌宠真 `read_file` 读了 ResearchFlow README 并准确总结；
> 第二轮（"不用再读，README 还提到哪些已知问题"）桌宠调 `memory_search` →
> 返回 `{"ok": false, "error": "memory_search not bound"}` → 桌宠**诚实拒答**
> （没编造，说"已知问题那节我没完整保留，可重新读取"）。功能层面体验不佳，
> 根因是下面两个 flag 门控缺陷。

---

## Bug A — `memory_search` / `memory_write` / `memory_read` 默认全部 `not bound`

### 根因
`backend/main.py:1121`：
```python
if config.memory.v2.memory_forget and _facts_store is not None:
    ...
    _memory_tools.bind(facts_store=_facts_store, embedder=..., llm_call=...)
```
`memory_tools.bind()` 注入的模块级 `_facts_store` 是 **4 个工具共享**的
（`memory_forget` / `memory_search` / `memory_write` / `memory_read`，全在
`tools/memory_tools.py` 模块级共用同一个 `_facts_store` 全局）。但 `bind()`
整段被 **`memory_forget` flag** 门控。

### 影响
`config.memory.v2.memory_forget` 默认 **False**（已实测确认）→ `bind()` 不跑
→ `_facts_store=None` → `memory_search` / `memory_write` / `memory_read`
**全部返回 `not bound`**，哪怕它们与 forget 功能无关。

`memory_search`（只读 LIKE 检索）属于 Stage 1 就该可用的基础能力，被 Stage 2
的 forget 开关连坐关掉。

### 修法（建议）
把 `bind()`（注入共享 `_facts_store`）从 `memory_forget` 门控里**提出来**，
改成 `_facts_store is not None` 就 bind；`memory_forget` flag 只控制
**forget 工具是否 register/暴露**，不控制整个模块的 store 注入。

---

## Bug B — code 模式 `read_file` 不进 `workspace_state`（工作记忆失效）

### 根因
`backend/main.py:1080`：
```python
if config.memory.v2.workspace_memory:
    _workspace_mem_store = WorkspaceMemoryStore(_state_db_path)
    set_workspace_store(_workspace_mem_store)   # ← 注入 file_tools._workspace_store
```
`os_tools/read_file._notify_workspace`（line 64-83，round-3 bug#4 修复加的
hook）从 `file_tools._workspace_store` 取 store 记 `record_action`。但该 store
只在 **`workspace_memory` flag** 开时才被 `set_workspace_store()` 注入。

`lifespan` line 1403 的 `rebind_loop()` **无条件**跑、日志 `ok=True`，但它只
重绑 loop 引用，**store 本身在 flag 关时从没注入** → rebind 了个空。

### 影响
`config.memory.v2.workspace_memory` 默认 **False**（已实测）→ store=None →
os_tools/read_file 的 hook 取到 None 不记录 → `workspace_state` 表 **0 行** →
"读过的文件" 进不了工作记忆 → 第二轮 assembler 的 workspace_memory 组件无内容
可带 → 跨轮文件上下文失效。

> 这正是 round-3 bug#4「os_tools read_file 加 workspace hook」想修的问题，
> hook 代码确实加了，但**被 flag 门控卡在上游**（store 没注入），所以真机
> code 模式下依然失效。

### 修法（建议）
同 Bug A 思路：workspace_store 的**创建+注入**应在 Stage 2 默认路径就做
（或至少 code 模式可用），不被 `workspace_memory` flag 连坐；flag 只控制
assembler 是否**消费** workspace_memory 组件，不控制底层记录是否发生。

---

## 共同病理 & 注意

**Strangler-Fig flag 把"功能开关"和"基础设施注入"耦合了。** flag 默认 OFF
本意是字节级回退、新功能默认关；但 `memory_search`（只读检索）、workspace
文件动作记录这类**基础能力**被 Stage 2 的 forget/workspace_memory flag 连坐
关掉，导致默认配置下 code 模式记忆链路两处断裂。

⚠️ **改动须谨慎**：涉及 flag 语义边界（哪些是"功能"、哪些是"基础设施"），
且关系到 Strangler-Fig 的"flag 关时字节级等同 gen-1"契约。建议：
1. 先确认 gen-1（Stage 0）时 `memory_search` / workspace 记录**本来**是否可用
   —— 若 gen-1 也没有，则当前行为可能是"刻意的未启用"，那就不是 bug 而是
   "功能没接完"，改法变成"接通"而非"解耦门控"。
2. 改后必须重测 byte-level consistency（flag 关时与 gen-1 一致）。

**与 F1/F2 关系**：这两个 bug **不在 F1/F2 范围**，是 F1 GUI 真测的副产物。
F1（fanout per-component 隔离）本身在该轮真测中仍成立（全程 `fanout_timed_out`
0 行、无全体塌陷）。
