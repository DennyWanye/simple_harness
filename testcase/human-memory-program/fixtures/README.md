# Human Memory Program fixtures

这些小型文件冻结模型评估的类别、分母、失败计分、阈值和两轮真实主模型 journey。实际逐项 prediction、
Provider 原始日志、截图和大型生成 corpus 写入 `.local-test-evidence/`，不得提交 Git。

- `model-eval-corpus-spec.json`：固定 seed、10 个语义不等价类别、每类 20 条，共 200 条；四种表达形式轮换。
- `metric-formulas.json`：明确 timeout/refusal/invalid plan 的计分方式、五项质量指标和 cold/warm 性能口径。
- `program-journey.json`：一轮不少于 20 committed turns 与第二个独立 root 的最低要求。

V0 只冻结 authority。真实主模型语义质量和 Provider token usage 必须在 S3/S5 与最终 program gate 当前执行，
不得用 Phase 2 synthetic vector 或离线 token oracle 代替。
