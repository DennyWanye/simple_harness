# 通用行动与能力包平台 — 自动化验证账本

> 验证日期：2026-07-24  
> 工作树：`F:\projects\deskpet`，当前 dirty master；未把无关改动纳入本次结论  
> 状态：严格 last-mile 复跑仍在执行；其余本轮自动化门禁已通过

## 1. 取消、恢复与单 Driver 所有权修复

最终聚焦套件：

```text
206 passed
```

直接覆盖：

- 活跃 Driver 阻塞在 `anext` 时，先取消并等待唯一 `LiveRun.task`，再调用
  `driver.cancel()`；
- running-root continuation 已入队/已预约与取消并发时，CAS 收敛为取消，
  FIFO 未绑定项明确失败结算；
- `recover()` 不得在取消收敛期间安装新的 owner；
- retiring owner 退出后，只有仍存在的 late effect 才启动 follow-up recovery；
- 崩溃停在 `LAUNCH_CLAIMED + CANCEL_REQUESTED` 时不得重新启动 provider；
- terminal、continuation reservation、cancel、recover 交错时仍只有一个 Driver
  和一个 `LiveRun.task` authority。

独立失败修复挑战共 5 轮，最终结论为 `PASS`。

## 2. 全套回归

### Backend

```text
5961 passed, 16 skipped, 9 deselected, 4 xfailed, 11 warnings
exit code: 0
duration: 23:55
```

执行边界：

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe `
  -m pytest backend/tests/ -q --ignore=backend/tests/integration
```

先前严格验收曾真实捕获
`test_runtime_keeps_mixed_batch_pending_when_one_physical_call_is_late[True]`
竞态失败；修复后该用例的 `False/True` 两个参数均通过，`False` 与双参数组合又分别连续
重复 5 轮通过。该失败没有被标记 flaky、skip 或 xfail。

### Frontend

从唯一正确工作目录 `F:\projects\deskpet\tauri-app` 执行：

```text
Vitest: 85 files / 820 tests, exit code 0
TypeScript: tsc -b, exit code 0
```

曾从仓库根误收集 `dist-portable` 与 `.claude/worktrees`；那次结果仅是调用范围错误，
不作为产品门禁。最终结论只使用 `tauri-app` 目录内的正确命令。

### Rust

使用隔离目标目录
`F:\projects\deskpet\.build\cargo-universal-action-validation`，避免污染或锁住现有
Tauri 实例：

```text
cargo test: 73 passed
cargo build: PASS
cargo check: PASS
```

仅出现既有 dead-code/linker warning，无测试或构建失败。

## 3. 能力平台与 Godot 样板

```text
scripts/e2e_capability_platform.py: PASS
Godot pack tests: 13 passed, 1 skipped
```

平台 smoke 真实确认：

- `godot` builtin 能力包 active version=`1.0.2`；
- registry revision=`8`；
- 控制工具与 `godot__detect` / `godot__project_check` 已注册；
- healthcheck 成功；
- 测试前机器确实没有可发现 Godot，可读错误为
  `godot_executable_not_found`，没有伪造“已安装”。

## 4. 构造与 authority 门禁

通用行动 construction gate：

```text
raw_total_loc = 69600
adjusted_total_loc = 69091
core_loc < 16950
kernel_loc = 1050
public operations = 6
result = PASS
```

authority audit：

```text
runtime DML owner = 1
transaction starter owner = 43
run map owner = 1
supervisor task owner = 1
presenter converter owner = 1
legacy production path = 0
result = PASS
```

## 5. 严格 last-mile

执行命令：

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe `
  scripts/acceptance/last_mile_smoke.py --strict
```

首轮报告：

```text
plans/2026-05-23-tool-last-mile-upgrade/
  manual-results-2026-07-24T111850Z/acceptance.json
summary: 6 pass / 0 fail / 1 skip / veto_failure=false
```

Backend、Cargo 和全部定向门通过；Vitest 因该验收进程没有继承 bundled Node PATH 被标为
`node not found`，所以严格模式只能得到 `SHIP-WITH-FOLLOWUP`，不能签字。精确首轮进程树
已全部自然退出。

第二轮通过 `DESKPET_NODE=<bundled node.exe>` 显式注入：

```text
plans/2026-05-23-tool-last-mile-upgrade/
  manual-results-2026-07-24T114311Z/acceptance.json
summary: 7 pass / 0 fail / 0 skip / veto_failure=false
decision: SHIP
```

分项：

```text
backend zero-regression: PASS (1,216,062 ms)
Vitest: PASS (9,125 ms)
Cargo check: PASS (407 ms)
MR-8 / MR-13 / MR-19 / Stage-2: PASS
```

最终状态：`PASS`。第二轮精确进程树全部自然退出，结束前记录的 private memory 合计
至少 `7246.3 MiB`，退出后对应 PID 全部归零。
