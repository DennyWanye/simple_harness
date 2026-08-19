<!-- plan-status: draft -->

# Plan：宿主 re-vendor simple-harness-sdk 0.1.3

> 唯一真相：`plans/2026-08-19-host-revendor-0.1.3/acceptance.md` + `assurance-contract.json`
> 仓库：`simple_harness`（宿主）
> 档位：M（单垂直切面——依赖切换；改启动装配常量 → full-surface smoke）

## 主要矛盾

上一轮（0.1.2）已把 wheel 身份收敛为 `sdk_candidate.py` 单一事实源。本次切换的成败不取决于
"改对多少处"，而取决于**验证 SSOT 设计真的兑现**——切 0.1.3 应当只改 `sdk_candidate.py` 一个文件的
三行常量，其余 6 个消费点（main.py / desktop_runtime / conformance / 2 测试 / verify_sdk_wheel.py）
零改动。若还要改别处，说明 SSOT 收敛不彻底。

## 关联验收标准
V-AC-1..5，每 Task 标注覆盖。

## 已核实事实（2026-08-19）
- 0.1.3 wheel SHA = `81025b2ccf08a0f49e272416176f8fdeead994e088e5d7a44e103ed5e902a7b9`
  （simple-harness-sdk/dist/，已由其 release gate 验证）。
- 宿主 SSOT：`sdk_candidate.py:15-17` 三行常量（SDK_VERSION / FILENAME / SHA256），消费点均 import。
- 0.1.3 对 0.1.2 纯新增（consumer adapter 加 2 字段 + 默认值；宿主 10-Port 用法不触碰）。
- vendor 现有 0.1.0/0.1.1/0.1.2 三个历史 wheel。

## 任务清单

### Task 1 — vendor 0.1.3 wheel + SSOT 三行常量  [V-AC-1, V-AC-2]
- `cp simple-harness-sdk/dist/0.1.3 wheel → backend/vendor/`；`shasum` 核对 == SSOT 常量
- `sdk_candidate.py` 三行：`SDK_VERSION="0.1.3"`、FILENAME、SHA256=81025b2c…
- 验证：`grep -rn "0\.1\.2" backend scripts --include="*.py"` 仅剩注释/historical 条目（SSOT 单点切换的证据）
- 依赖：无

### Task 2 — pyproject + uv.lock  [V-AC-1]
- `pyproject.toml` `>=0.1.2`→`>=0.1.3` + path 0.1.3 wheel；`uv sync --extra dev` 重生成 lock + 装 0.1.3
- 验证：`.venv/bin/python -c "import importlib.metadata; print(version('simple-harness-sdk'))"` == 0.1.3
- 依赖：Task 1

### Task 3 — 兼容机器校验 + fail-closed 测试  [V-AC-3, FAIL-3]
- `testcase/2026-08-19-sdk-usability-optimization/api_compat_check.py` 跑 0.1.2 vs 0.1.3（三维无删除）
- `test_sdk_candidate.py` 复跑（fail-closed 负向仍绿）
- 依赖：Task 1

### Task 4 — 回归 + conformance  [V-AC-4]
- baseline_runner 18 分片（0 新增红）；`python -m simple_harness.testing`（22/22）
- 依赖：Task 2

### Task 5 — 真机 V-COLD + full-surface smoke  [V-AC-5, TO-R1]
- 干净 userdata → 启动 → `sdk_runtime_ready 0.1.3` → 主聊天真 DeepSeek 回复（computer-use）
- e2e_smoke.py 全表面冒烟
- 依赖：Task 2

### Task 6 — 文档回写 + 提交  [DoD]
- vendor/README.md active candidate=0.1.3；ARCHITECTURE/SDK_EXTRACTION + PROJECT_STATUS 回写
- 依赖：Task 4、5

## 回滚
`git revert` 宿主提交即可（旧 wheel 保留在 vendor/，SSOT 三行回退）。
