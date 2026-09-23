# V1.4（去除 NanoJev）执行基线

- 日期：2026-09-21 CST
- 候选 SDK：`/Users/denny/projects/simple-harness-sdk-h1h-impl`
- 分支：`codex/h1h-impl`
- 基线提交：`102ad3dfa2db38d575ea929d39ec5ed1561a71da`
- 工作树：已有 V1.4 H1-H/H2-H8 未提交改动；本记录不重置、不清理。
- 全量命令：`PYTHONPATH=src .venv/bin/python -m pytest -q tests/orchestrator/full_target -p no:cacheprovider`
- 本轮回归：`3788 passed, 5 skipped, 128.15s`
- 条件 skip：PANDA parser 未配置 1；codec-level refusal 已有专项覆盖 3；real-provider opt-in 未带开关 1。
- 结论：回归基线通过；H1 gate 仍 OPEN，不能将此数字解释为 H1-H8 完成。
