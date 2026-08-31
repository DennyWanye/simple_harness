# Human Memory Program fixtures

这些小型文件冻结模型评估的类别、分母、失败计分、阈值和两轮真实主模型 journey。实际逐项 prediction、
Provider 原始日志、截图和大型生成 corpus 写入 `.local-test-evidence/`，不得提交 Git。

- `model-eval-corpus-spec.json`：固定 seed、12 个语义不等价类别、每类 20 条，共 240 条；四种表达形式轮换。旧
  `contested` 类仅改名为 `contested-not-required`，其 20 条 query 与 no-recall gold 不变。
- `typed-recall-v1.json`：rev1 历史 oracle，保留用于审查 lineage，不再被当前 testcase 选择。
- `typed-recall-v2.json`：rev2 历史 oracle，保留用于 challenge lineage，不再被当前 testcase 选择。
- `typed-recall-v3.json`：当前 revision 3 oracle；冻结 Recall v4/TypedRecallResult v1 的 source binding、CONTEST
  create/reject/resolve、逐轴资格/披露、result-bound page-in、authority-event/current-use 线性化、request-hash mutation、
  exact receipt hashes、全 tie-break/vector degrade、rich-to-minimal projection、replay/fault 与独立预计算的 cap/RRF/dedupe/budget/deadline 值。依赖冲突事实的 complete/partial 类只存在于此
  fixture，不进入真实模型质量分母。
- `runners/run_typed_recall_public_consumer.py`：官方 public-wheel runner；self-check 验证 fixture、artifact parser/tamper rejection、零状态变化与 fault all-old/all-new terminal hash 关系，以及“已执行 callable 不得降级为 BLOCKED”；自身不能产生产品 PASS。
- `twin-graph-v1.json`：Task 6 display-only graph DTO revision 1 oracle；冻结 active/contested/inferred
  node/edge、ordinary-view 状态过滤、敏感 canary、correction/forget/rebuild 与 payload known-answer hash。其
  `canonical_setup_oracle` 只描述应经 public manager/evidence/mutation/suppression API 建立的 durable state，
  永远不得作为 `get_twin_graph_view` 输入。
- `runners/run_twin_graph_public_consumer.py`：Task 6 fail-closed runner skeleton；当前 exact Memory candidate
  与 package-root public seed API 尚未冻结，因此正式执行固定为 `NOT_RUN/BLOCKED`。self-check 只验证 DTO/hash/
  canary known answers，不能证明 canonical DB、桌面 UI 或 Agent 零影响。
- `metric-formulas.json`：明确 timeout/refusal/invalid plan 的计分方式、六项质量指标、cold/warm 性能与 Context 预算口径。
- `program-journey.json`：一轮不少于 20 committed turns 与第二个独立 root 的最低要求。
- `fault-matrix.json`：冻结状态机故障 seam、runner command、seed 和终态 oracle；不存在 runner 时必须如实保持 NOT_RUN。

V0 只冻结 authority。真实主模型语义质量和 Provider token usage 必须在 S3/S5 与最终 program gate 当前执行，
不得用 Phase 2 synthetic vector 或离线 token oracle 代替。
