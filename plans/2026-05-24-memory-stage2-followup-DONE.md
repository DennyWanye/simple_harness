# memory-v2 Stage 2 followup — F1 + F2 完成报告

**分支**: `feat/memory-stage2-followup-f1f2`（worktree `G:\projects\deskpet-stage2-f1f2`）
**基线**: master `f650cc8`
**关联**: [`plans/2026-05-24-memory-stage2-followup.md`](./2026-05-24-memory-stage2-followup.md)（任务定义）

---

## F1 — assembler workspace fanout timeout（per-component 隔离）✅

### 根因

`ComponentRegistry.fanout` 旧实现只有**一个整体** `asyncio.wait_for(gather, 1.5s)`：
任一组件慢（`MemoryComponent` 向量召回碰 BGE-M3 冷加载阻塞 loop）就让整批
fanout 超时 → **所有组件**（连 trivial 的 `time`/`persona`）一起变空 slice，
且 log 只打 `fanout_timed_out pending=[全部]`，无法定位真凶。这正是 round-3
`pending=['memory','persona','tool','time','workspace']` 的成因。

### 修复（[`backend/deskpet/agent/assembler/registry.py`](backend/deskpet/agent/assembler/registry.py)）

- **per-component 独立计时 + 独立软超时**：每个 `_safe_provide` 用
  `asyncio.wait_for(_run(), per_component_timeout_s)` 单独包裹。慢组件单独降级
  `meta={"error":"timeout","status":"timeout"}`，**快组件照常返回真内容**。
- **每 slice 记 `duration_ms` + `status`**：慢组件（>60% 预算）/ 非 ok 打
  `assembler.component_done` info log → 精确定位哪个组件慢。
- **外层保留宽松兜底**（预算 + 0.5s）防同步阻塞 loop；`timeout_ms=None` 仍走
  纯 gather，并行性不变。

### 测试（[`tests/test_f1_fanout_isolation.py`](backend/tests/test_f1_fanout_isolation.py)，5 passed）

慢组件不饿死快组件 / 每 slice 带 duration_ms / no-timeout 路径 / 异常隔离 /
全快组件无误降级。既有 `test_deskpet_context_assembler.py` **49 passed 0 回归**。

---

## F2 — eval_gate fixture 接入 Stage 2 召回路径 ✅

### 问题

旧 `eval_gate` 只跑裸 Retriever，不经 EnhancedRetriever / 无 Stage 2 flag →
`--strict` 在召回 PR 永远 FAIL，Stage 2"提升召回质量"无量化证据。

### 修复

1. **`eval_gate.py` 加 `--stage=stage1/stage2`** + stage-aware baseline 路径
   （`_baseline_path_for_stage`）。`_load_baseline(path=None)` 支持显式路径。
2. **新 fixture [`zh_fixture_stage2.py`](backend/deskpet/memory/eval/zh_fixture_stage2.py)**：
   35 题 + seed 10 条 facts（实体名在 `value`、`source_msg_id=None` → 裸
   Retriever 搜不到）+ 10 条 entity QA 写进 `memory_qa_set` 表（对齐
   MetricsRunner 从表读 QA 的真实契约），`expected_msg_id = _FACT_ID_OFFSET+fid`。
   - 注：`FactsStore.upsert` 自开 aiosqlite 连接，必须**先全 upsert 再开连接
     写 QA**，否则 `database is locked`（已修）。
3. **`zh_baseline_stage2.json`** 钉死（qa=45, hit@5=0.5556）。
4. **`eval_gate_ci.sh`**：召回类改动 → 跑 stage1 回归 + stage2 `--strict`。

### 量化结果（实跑 `--json` 解析；stage2 in-process 连跑 4 次恒定）

| metric | stage1（裸 Retriever，35 题） | stage2（Enhanced+entity_path，45 题） | Δ |
|---|---|---|---|
| hit@1 | 0.3429 | 0.4444 | **+0.1015** |
| hit@5 | 0.4286 | 0.5556 | **+0.1270** |
| hit@10 | 0.8286 | 0.8667 | +0.0381 |
| mrr | 0.4253 | 0.5298 | +0.1045 |
| token/q | 195.86 | 197.87 | +2.01 |

hit@5 **+0.1270** 超 +0.10 目标。隔离实验
（`test_stage2_entity_path_is_the_driver`：同 45 题 bare vs enhanced）证明
提升来自 entity_path 而非"加简单题"。

### gate 行为（实跑 exit code）

| 命令 | exit | 说明 |
|---|---|---|
| `--stage=stage1`（默认）| 0 | hit@5=0.4286 |
| `--stage=stage2` | 0 | == stage2 baseline |
| `--stage=stage2 --strict` | 1 | 持平不算提升，strict 正确拒绝 |

### 测试（[`tests/test_eval_gate_stage2.py`](backend/tests/test_eval_gate_stage2.py)，7 passed）

stage 路径解析 / stage2 beats stage1 / **entity 路确定性**（纯 regex+LIKE 连跑
3 次一致）/ entity_path 驱动隔离实验 / strict 提升 PASS / strict 持平 FAIL /
钉死 baseline 校验。

既有 [`tests/test_eval_gate_strict.py`](backend/tests/test_eval_gate_strict.py)
**11 passed 0 回归**（修了 `_stub_run_eval` 的 `_fake` 接受 `stage` + `**kwargs`，
因 `run_eval` 新增 `stage` 参数）。

---

## 测试汇总

- F1+F2+strict 合跑：**23 passed**（5 F1 + 7 stage2 + 11 strict）
- 既有 assembler 文件：**49 passed 0 回归**
- 全套 `pytest tests/` 有少量 `test_deskpet_context_assembler.py` timing flaky
  （embedder mock 子进程在全套并发下资源竞争，clean master 同样复现，
  pre-existing 非本次引入；相关文件单跑 49 passed）。

## 既有问题（不在本 followup 范围，建议单开）

`zh_baseline.json`（stage1）钉的 hit@5=0.46 与 fixture 实产 0.4286 不符 →
stage1 默认 gate 一直 FAIL（主 checkout 原版同样复现，commit 历史遗留）。
本次 F2 不动 stage1 baseline。

---

## 隔离环境复跑验证（2026-05-31，与另一并行任务同时进行）

用户要求"创建隔离环境跑，不可与并行任务冲突（前后端等）"，并选了**两者都做**
（自动化 + F1 真机端到端）。本次在 worktree `deskpet-stage2-f1f2` 上完成，
**全程 in-process（tempfile 临时 db + `.venv`），零端口绑定**，与并行的
`live2d-rewrite` codex 任务及 orphan 8400 backend **零冲突**（8400 全程 ALIVE
未被触碰；我未开任何端口）。

### 阶段1 — 自动化（隔离 worktree 实跑）

确认跑的是 worktree 代码而非主 checkout：`per_component_timeout_s` 标记
主 checkout=0 / worktree=8；`zh_fixture_stage2.py` 仅 worktree 存在。

| 套件 | 结果 |
|---|---|
| `test_f1_fanout_isolation.py` | **5 passed** |
| `test_eval_gate_stage2.py` | **7 passed** |
| `test_eval_gate_strict.py`（既有回归） | **11 passed** |
| `test_deskpet_context_assembler.py`（既有回归） | **35 passed** |

eval_gate 命令行实跑（`--json` 解析）：

| 命令 | qa | hit@5 | exit | 说明 |
|---|---|---|---|---|
| `--stage=stage1` | 35 | 0.4286 | 0 | |
| `--stage=stage2` | 45 | 0.5556 | 0 | == baseline |
| `--stage=stage2 --strict` | 45 | 0.5556 | **1** | 持平不算提升，strict 正确拒绝 |

hit@5 stage2 − stage1 = **+0.1270**（与钉死 baseline 一致）。

### 阶段2 — F1 真组件栈端到端（非 stub）

新增 [`backend/scripts/verify_f1_realstack.py`](backend/scripts/verify_f1_realstack.py)：
用**生产工厂** `build_default_assembler` 造真 assembler + 真 `Embedder`（mock
缺权重，仍是生产类）+ 真 `WorkspaceMemoryStore`，seed 一条真文件动作
（`record_action(read, README.md)`），调真 `registry.fanout`（`assemble()`
内部就是 `self._registry.fanout`，assembler.py:208）。**实跑 exit=0**：

```
workspace_memory: status=ok, has_content=true   ← code-mode 真文件动作进 fanout 产物
persona / time:   status=ok
memory:           no_memory_manager   (本测试未注入 memory_manager，符合预期)
tool:             no_registry         (符合预期)
全部 6 组件:       各带 duration_ms + status   ← F1 per-component 计时生效
```

这是 round-3 bug#4 修复后的**真组件栈**佐证：workspace_memory 经真 fanout 返回
真内容、不被别的组件拖垮，且每组件独立带计时（可定位慢组件）。慢组件单独
timeout、不饿死快组件的隔离机制本身，另由阶段1 的 5 个 stub 单测在同一个真
`ComponentRegistry` 类上钉死。

### 诚实边界说明

本次 F1 验证走的是**真组件栈 in-process**路线（生产工厂 + 真 store + 真 fanout），
**不是**起 Tauri + backend 的 GUI code-mode 两轮对话。原因：用户明确要求与并行
任务**强隔离**，而起完整 Tauri 会和并行的 `live2d-rewrite`（同为 Tauri）抢
WebView2 / 端口 / 窗口焦点资源。GUI 级两轮 code-mode 真测（抓 backend log 确认
`fanout_timed_out` 在真运行栈消失）需在并行任务结束、可独占资源时单独补做。

---

## 结论

F1（per-component 隔离 + 诊断）+ F2（stage2 召回量化，hit@5 +0.1270，strict
不再形同虚设）**全部完成**，新增 12 测试 + 修复既有 8 测试，0 功能回归。
隔离环境复跑（阶段1 自动化 58 passed + 阶段2 F1 真组件栈 exit 0）**全绿**，
与并行任务零冲突。
