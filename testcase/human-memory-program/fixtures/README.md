# Human Memory Program fixtures

这些小型文件冻结模型评估的类别、分母、失败计分、阈值和两轮真实主模型 journey。实际逐项 prediction、
Provider 原始日志、截图和大型生成 corpus 写入 `.local-test-evidence/`，不得提交 Git。

- `model-eval-corpus-spec.json`：固定 seed、12 个语义不等价类别、每类 20 条，共 240 条；四种表达形式轮换。旧
  `contested` 类仅改名为 `contested-not-required`，其 20 条 query 与 no-recall gold 不变。
- `typed-recall-v1.json`：rev1 历史 oracle，保留用于审查 lineage，不再被当前 testcase 选择。
- `typed-recall-v2.json`：当前 revision 2 oracle；冻结 Recall v4/TypedRecallResult v1 的 source binding、CONTEST
  create/reject/resolve、逐轴资格/披露、result-bound page-in、authority-event/current-use 线性化、request-hash mutation、
  replay/fault 与独立预计算的 cap/RRF/dedupe/projection/budget/deadline 值。依赖冲突事实的 complete/partial 类只存在于此
  fixture，不进入真实模型质量分母。
- `runners/run_typed_recall_public_consumer.py`：官方 public-wheel runner；self-check 只验证 fixture，自身不能产生产品 PASS。
- `metric-formulas.json`：明确 timeout/refusal/invalid plan 的计分方式、六项质量指标、cold/warm 性能与 Context 预算口径。
- `program-journey.json`：一轮不少于 20 committed turns 与第二个独立 root 的最低要求。
- `fault-matrix.json`：冻结状态机故障 seam、runner command、seed 和终态 oracle；不存在 runner 时必须如实保持 NOT_RUN。

V0 只冻结 authority。真实主模型语义质量和 Provider token usage 必须在 S3/S5 与最终 program gate 当前执行，
不得用 Phase 2 synthetic vector 或离线 token oracle 代替。
