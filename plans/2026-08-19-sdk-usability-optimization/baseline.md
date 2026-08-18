# 绿色基线快照（phase-2 锁定）

> 锁定时间：2026-08-19（HEAD `5b781bf6df5319e2c13230bcee508fff0e470f5a`，工作树干净）
> 工具：`scripts/baseline_runner.py`（分片、心跳、超时杀进程树、既有失败签名比对）
> 命令：`python3 scripts/baseline_runner.py --run-dir plans/2026-08-19-sdk-usability-optimization/verification/baseline --known-failures baseline-known-failures.json --accept-current-failures`

## 结果摘要

- **17 个分片：6 passed / 11 known-failure**（全部为既有红，已记入 `baseline-known-failures.json` 签名）
- 通过分片：backend-b、backend-g-l、frontend-typecheck、frontend-build、rust-test、rust-check
- 既有红分片与代表失败：
  - `backend-a`：`test_pinned_agent_reach_channel_contract`——venv 中 agent-reach 的 `direct_url.json`
    缺 `archive_info.hash`（uv 安装行为，**环境性既有红，与本次改动无关**，已实测确认）
  - `backend-c / d-f / m-r / s-z / capabilities / companion`：与 2026-08-17 里程碑记载的
    81-failed 基线一致（已删 harness 关联测试、Windows-only 工具、deepresearch v6 wiring 等）
  - `backend-harness-simplification`：rc=4（目录已于 2026-08-17 删除，无测试可收集）
  - `root-tests`：`tests/e2e/test_chat_flow.py` 2 条（需活后端，环境性）
  - `frontend-vitest / frontend-lint`：tail 签名既有红

## 回归判据（Slice 1 Task 7 使用）

执行后复跑同一 runner 命令（不带 `--accept-current-failures`）：
- **任何分片出现签名之外的新失败 → 新红即停，阻断交付**；
- 既有红签名集合不得扩大（同分片新增 failure marker 也算新红）。

## 状态文件

- runner state：`verification/baseline/baseline-state.json`（含每分片 log 路径与指纹）
- 既有失败签名：`baseline-known-failures.json`（本次已随真实状态更新，将随 Slice 1 一并提交）
