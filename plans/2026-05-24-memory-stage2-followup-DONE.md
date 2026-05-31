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

## 结论

F1（per-component 隔离 + 诊断）+ F2（stage2 召回量化，hit@5 +0.1270，strict
不再形同虚设）**全部完成**，新增 12 测试 + 修复既有 8 测试，0 功能回归。
