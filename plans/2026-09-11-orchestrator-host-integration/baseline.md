# 回归基线（改动前，Host `49466560`，SDK 钉 0.8.0）

- 采集时间：2026-09-11
- 环境：`backend/.venv`（simple_harness 0.8.0），App 未运行（18120 / 8100 端口没有监听，没有 `main.py` 进程）

## 后端

命令（在 `backend/` 下执行）：

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider -rfE tests/sdk_adapters tests/execution tests/permissions
```

结果：68 failed / 943 passed / 4 deselected / 1 error（376 s）。失败与错误共 69 条，清单见 `baseline-backend-failures.txt`（格式为 `FAILED|ERROR <nodeid>`）。

| 文件 | 条数 |
|---|---|
| tests/execution/test_primary_decisions.py | 18 |
| tests/execution/test_foreground_permission_lease.py | 10 |
| tests/sdk_adapters/test_s5b_acceptance_matrix.py | 8 |
| tests/sdk_adapters/test_effect_gate_hardening.py | 8 |
| tests/sdk_adapters/test_effect_gate.py | 8 |
| tests/execution/test_foreground_runtime.py | 4 |
| tests/sdk_adapters/test_task_scope_update_tool.py | 3 |
| tests/sdk_adapters/test_s5a_acceptance_matrix.py | 3 |
| tests/sdk_adapters/test_objective_events.py | 2 |
| 另外 5 个文件 | 各 1（test_start_mode_driver_composition、test_s5a_milestone_route_loop、test_task_grant_clock_seam、test_primary_history_outbound，以及 ERROR test_expiry_terminal_public_recovery） |

补充（plan review P1-6）：控制通道、启动、context 相关的 5 个文件：

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider -rfE tests/test_context.py tests/test_health_startup_errors.py tests/test_startup_errors.py tests/test_websocket.py tests/test_message_routing_context.py
```

结果：52 passed / 1 skipped，0 失败。判定口径：改动后保持全绿。另确认 Host 里没有 `agent_orchestrator` 同名模块或目录（wheel 新增的顶层包不会冲突）。

说明：第一阶段记录的"`tests/sdk_adapters` 34 红"只统计了 sdk_adapters 一个目录；这次多了 execution / permissions 两个目录，所以总数不同。判定口径：改动后的失败集合必须是这 69 条的子集。

## 前端（`tauri-app/`）

| 命令 | 结果 |
|---|---|
| `npm run typecheck` | 通过（exit 0） |
| `npm run lint` | exit 1：161 个问题（155 error / 6 warning），都是已有问题；签名清单 `baseline-frontend-lint.tsv`（文件、级别、规则、条数） |
| `npm test` | 94 个文件，723 条用例全部通过 |

判定口径：typecheck 与 vitest 保持全绿；lint 不得出现基线签名以外的新问题（新文件必须 0 问题）。
