# 绿色基线快照（phase-2 锁定）

> 锁定时间：2026-08-19（HEAD `5b781bf6df5319e2c13230bcee508fff0e470f5a`，工作树干净）
> 工具：`scripts/baseline_runner.py`（分片、心跳、超时杀进程树、既有失败签名比对）
> 命令：`python3 scripts/baseline_runner.py --run-dir plans/2026-08-19-sdk-usability-optimization/verification/baseline --known-failures baseline-known-failures.json --accept-current-failures`

## 结果摘要

- **18 个分片：6 passed / 12 known-failure**（全部为既有红，已记入 `baseline-known-failures.json` 签名）
  - 2026-08-19 增补第 18 分片 `backend-sdk-adapters`：原分片枚举只覆盖 `tests/test_*.py` 首字母，漏掉 `tests/sdk_adapters/` 子目录（本次切换主战场）；其 pre-change 基线通过 stash + venv 降级 0.1.1 实测取得，2 个既有失败（tool_catalog import-purity / closed-ingress parity，系 809c30b9 删除 memory_tools 的清单残留）已登记签名
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

- runner state：`verification/baseline/baseline-state.json`（18 分片，含每分片 log 路径与指纹）
- 2026-08-19 修复 `scripts/baseline_runner.py`：FAILURE_PATTERNS 新增 vitest `FAIL <file> > <case>` 稳定 marker——此前 vitest 分片回退到含 Duration 行的 tail-sha256，每次运行签名必漂移、runner 必停
- 既有失败签名：`baseline-known-failures.json`（本次已随真实状态更新，将随 Slice 1 一并提交）
