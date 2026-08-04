# R-T6 Shadow 模式 Go/No-Go 标准（§15.5）

> 版本：v1.0 — 2026-06-05
> 目的：量化"何时可以把 verify_gate 从 shadow 翻到 strict"的决策门。

---

## 背景

`verify_gate` 出厂为 `mode="shadow"`（P0-2 落地后），shadow 意味着：
- 门控逻辑**运行**（提取 claim、对账 ledger、调用 evaluator）
- 判定结果**不阻断** dispatch（总是放行）
- 判定结果写入 `receipt.shadow_verdict`（"would_block" | "would_pass"）
- 用户实际行为弱信号写入 `receipt.actual_outcome`（后续迭代收集）

只有满足以下全部量化门，才能将 `mode` 改为 `"strict"`。

---

## Go/No-Go 量化门（全部达标才可翻 strict）

| 指标 | 达标阈值 | 测量窗口 | 计算方式 |
|---|---|---|---|
| **误杀率**（false-block：shadow 说 would_block 但用户实际接受产物） | < 2% | 连续 N ≥ 200 次高后果判定 | `false_blocks / total_high_consequence_decisions` |
| **漏放率**（false-pass：shadow 说 would_pass 但用户投诉/redo） | < 5% | 同上 | `false_passes / total_high_consequence_decisions` |
| **LLM 降级率**（GoalChecker skipped + evaluator skipped + reflection parse failed） | < 10% | 同窗口 | `degraded_decisions / total_decisions` |
| **verify_latency_p95** | < 3000 ms | 同窗口 | `percentile(verify_latency_ms, 95)` |

**任一指标不达标 → 保持 shadow**，不翻 strict。

---

## 指标来源

所有指标通过 `receipt.shadow_verdict` / `receipt.actual_outcome` / `receipt.degradation_flags` / `receipt.verify_latency_ms` 字段聚合，配合 `observability/metrics_sink.py` 中的事件：

| 事件名 | 含义 |
|---|---|
| `goal_check_skipped` | GoalChecker 超时/失败 → 降级信号（计入降级率） |
| `reflection_parse_failed` | 结构化反思 JSON 畸形 → 机械 nudge（计入降级率） |
| `evaluator_skipped` | ExternalEvaluator 超时/失败（计入降级率） |
| `evaluator_conservative_block` | 高后果 + evaluator 失败 → 保守拦截（计入降级率，但非误判） |

---

## 误杀/漏放弱信号采集机制

`actual_outcome` 目前为占位字段（默认 `None`），后续通过以下弱信号填充：

1. **用户 redo（重新提相同目标）**：`= "user_redid_goal"` — 漏放信号
2. **用户主动点"完成"确认**：`= "user_accepted"` — 非误杀确认
3. **用户投诉 / 反馈按钮**：`= "user_complained"` — 漏放或误杀信号（需人工分类）
4. **超过 24h 无 redo 且用户继续正常使用**：`= "implicit_accepted"` — 弱非误杀信号

弱信号采集由 P1 阶段 WI-3.x（用户行为埋点）实现，R-T6 当前只预留字段。

---

## 实施检查清单

在翻 strict 前，必须：

- [ ] 收集 ≥ 200 次高后果判定（含有效 shadow_verdict）
- [ ] 误杀率、漏放率从 `receipts/*.jsonl` 中计算（需 `scripts/shadow_metrics_report.py`）
- [ ] verify_latency_p95 从 `receipt.verify_latency_ms` 聚合
- [ ] LLM 降级率从 `metrics.jsonl` 中的 degradation 事件计算
- [ ] 四项全部 ✅ → PR "flip verify_gate default to strict"
- [ ] 保留 shadow 能力（可随时 rollback）

---

## 参考

- §15.4 LLM 失败降级矩阵：`00-PLAN.md §15.4`
- §15.5 shadow 可观测方案：`00-PLAN.md §15.5`
- Receipt shadow 字段实现：`backend/deskpet/tools/receipt.py`
- 降级事件实现：`backend/deskpet/agent/goal_checker.py`, `reflection.py`, `external_evaluator.py`
