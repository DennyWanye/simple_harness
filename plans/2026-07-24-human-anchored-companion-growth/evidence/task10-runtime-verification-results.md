# Task 10 Personal Workflow / Skill Runtime 最终验证结果

> 日期：2026-07-25
> 工作树：`F:\projects\deskpet`
> 状态：**完成**。自动化门、主消息页真人 E2E、架构事实源与精确进程清理均已闭环。

## 当前生产事实

- Run selection、catalog/ToolSpec exact facts 与 Skill scope 使用同一次冻结 capture。
- `workflow.personal_v1` 使用冻结 DAG、稳定 call/effect identity 和 journaled effect；
  effect 已结算但 checkpoint 尚未写入时，恢复只复用 durable receipt，不重复物理调用。
- workflow schema v22 保存 Personal selection launch identity、immutable Skill scope
  activation，以及 `RunContext.owner_key/profile_generation/binding_epoch`；v21→v22
  migration 对历史行安全回填，幂等恢复不再因 owner identity 丢失触发
  `RunIdentityConflict`。
- `CompanionTurnAuthority → ProductVenue → RunKernel` 只传递 typed owner/generation/lease
  与同一次 Run capture，不重新查询 live identity。
- CapabilityPlatform 初始化后同时构造两种职责不同的 resolver：
  - `FirstPartyFrozenSkillResolver`：只服务带 exact `run_id` 的 `skill_invoke`；
  - Manager-backed `SkillPackSnapshotResolver`：注入生产 `AgentLoop` 与既有
    `ContextAssembler/SkillComponent`，从 exact version root 重验正文和资源。
  `ManagedSkillDiscoveryProjection` 只承担发现和 typed selection，不再是生产正文 authority。

## 最终收口

- 新增 fresh-v21 与 migrated-v21 的 Task 10 schema 对象等价测试，比较 launch ticket
  列、activation 表、partial-unique index 与 immutable/consistency triggers。
- 修复生产组合根仍把 `ManagedSkillDiscoveryProjection` 直接传给 AgentLoop/Assembler
  的接线缺口；`SkillComponent.bind_snapshot_resolver()` 与
  `ContextAssembler.bind_skill_snapshot_resolver()` 提供单次、Manager-backed 显式 seam，
  缺 resolver 时 product loop fail closed，生产组合不依赖私有字段。
- child 使用独立 pending pin、bound lease intent 与 `SnapshotLeaseReadyGate`；父 Run
  terminal 后 child 仍只读自己的 frozen snapshot，child terminal 后精确 release/unpin。
- execution fence 已贯通 provider launch、tool dispatch、terminal commit 与 delivery；
  forget/quarantine 后新 Run 不再可见，在途 Run 每一阶段都从同一 fence fail closed。
- 真实 E2E 发现并修复两个仅在恢复链可见的问题：
  1. `RunContext` owner identity 未持久化导致 idempotent replay 冲突；
  2. exact capability snapshot 已改用 content-addressed refs，但 product recovery 仍按
     legacy tool-name list 校验，导致 `product recovery capability snapshot mismatch`。
  recovery 现在校验 frozen `prepared_tool_set_ref`、请求 `tool_set_snapshot_ref` 与 exact
  Context OS snapshot ref，只有旧 snapshot 才走 legacy names fallback。
- Tool build identity 覆盖生产 `todo_write`、clarification、subagent 入口与
  `backend/main.py` 变更，checked-in manifest 由官方 generator 刷新并通过 `--check`。
- 前端输入栏状态改为选中 Run projection 优先；已完成 Run 不再被 stale 全局
  `session.status=running` 显示成“工具执行中”。

## 自动化与精确进程审计

所有命令均在 `F:\projects\deskpet` 执行；`released private` 按本次受监控进程树的
峰值私有内存记录，退出后逐个 tracked PID 复核。

| 范围 | 结果 | root PID / create-time | tracked PIDs | peak/released private | survivor |
|---|---:|---|---|---:|---:|
| Personal Workflow/schema（补测试前） | 6 passed | `24972` / `2026-07-25 11:57:41` | `24972,26604,9380` | `885391360` bytes | `0` |
| Profile/model-spawn/retired ToolSpec（规范复跑） | 26 passed | `5568` / `2026-07-25 11:59:15` | `3436,5568,23032` | `885170176` bytes | `0` |
| Authority/Venue/lease | 21 passed | `18572` / `2026-07-25 11:59:33` | `18572,19856,23508` | `895127552` bytes | `0` |
| Skill 全路径 | 83 passed | `10468` / `2026-07-25 11:59:55` | `3232,4452,10468` | `881733632` bytes | `0` |
| trusted selection/ToolExecutor | 35 passed | `27632` / `2026-07-25 12:00:11` | `536,21636,27632` | `895307776` bytes | `0` |
| Personal Workflow/schema（补测试后） | 7 passed | `20300` / `2026-07-25 12:00:56` | `8392,20300,26776` | `887488512` bytes | `0` |
| 生产 Skill resolver 首轮接线与相邻回归 | 62 passed | `18060` / `2026-07-25 12:04:58` | `15524,18060,23604` | `1083260928` bytes | `0` |
| 显式 bind seam 收口后复核 | 62 passed | `11964` / `2026-07-25 12:08:01` | `11964,23648,26076` | `1083592704` bytes | `0` |
| Task 10 首轮全组合门 | 442 passed / 1 stale-manifest failure | `16556` / `2026-07-25 14:06:30` | `16556,14436,26360` | `1457098752` bytes | `0` |
| Manifest + recovery 定向复核 | 32 passed | 同步命令 | 无持久子进程 | — | `0` |
| Task 10 最终隔离组合门 | **443 passed**（`212 + 62 + 8 + 9 + 1 + 5 + 146`） | 同步分组命令 | 每组自然退出 | 最终匹配审计 | `0` |
| 前端状态投影 | **11 passed + tsc pass** | 同步命令 | 无持久子进程 | — | `0` |
| Tauri 主消息页真人 E2E | **PASS** | Vite `25188` / `14:20:18`；Tauri `17876` / `14:20:36` | 19 PIDs | `9653006336` bytes released | `0` |

命令：

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest -q backend/tests/companion/test_personal_workflow.py
```

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest -q backend/tests/harness_simplification/test_product_workflow_profiles.py backend/tests/harness_simplification/test_model_workflow_spawn.py backend/tests/capabilities/test_retired_tool_specs.py
```

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest -q backend/tests/companion/test_turn_authority.py backend/tests/harness_simplification/test_product_venue_chain.py backend/tests/companion/test_run_catalog_lease.py
```

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest -q backend/tests/companion/test_skill_runtime_snapshot.py backend/tests/test_deskpet_skills_loader.py backend/tests/test_p4s20_skill_loader_v2.py backend/tests/test_deskpet_skill_auto_disclosure.py backend/tests/test_deskpet_skill_remount_after_compaction.py backend/tests/test_run_deskpet_skill.py backend/tests/harness_simplification/test_prepared_tool_snapshot.py
```

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest -q backend/tests/harness_simplification/test_trusted_selection_snapshot.py backend/tests/harness_simplification/test_wi2_tool_executor.py
```

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest -q backend/tests/test_agent_harness_build_agent_contract.py backend/tests/companion/test_skill_runtime_snapshot.py backend/tests/test_deskpet_skill_auto_disclosure.py backend/tests/test_deskpet_skill_remount_after_compaction.py
```

最终组合门还覆盖：

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest -q `
  backend/tests/companion/test_child_run_ready_gate.py `
  backend/tests/harness_simplification/test_execution_fences.py `
  backend/tests/harness_simplification/test_execution_schema.py `
  backend/tests/harness_simplification/test_execution_decisions.py `
  backend/tests/harness_simplification/test_run_kernel.py `
  backend/tests/test_execution_build_manifest.py `
  backend/tests/test_p4s22_todo_write.py `
  backend/tests/capabilities/test_capability_tools.py
```

`test_r6_cutover_audit_requires_complete_clean_live_stacks` 单例耗时约 58 秒，隔离运行通过；
`test_run_kernel.py` 的 5 条既有 aiosqlite event-loop-close warning 不影响 62 项结果。

## 真人主消息页 E2E

- 启动方式：唯一 Vite（`--mode relay --port 5173 --strictPort`）+
  `tauri dev --config .tmp/tauri-dev-nobefore.json`，没有重复启动 backend/Vite。
- 环境：`DESKPET_BACKEND_DIR=F:\projects\deskpet\backend`，
  `DESKPET_PYTHON=F:\projects\deskpet\backend\.venv\Scripts\python.exe`，
  backend `8100`，Vite `5173`。
- 日志确认：
  `[backend_launch] Dev python=... backend_dir=F:\projects\deskpet\backend`；
  `POST https://chinzy.com/v1/chat/completions` 返回 HTTP 200；
  `chat_v2_final_send_completed` 返回 2 字符。
- 真人步骤：点击桌宠“消息”→点击“新话题”→点击输入框→输入
  “请只回答数字：44减17等于多少？”→点击“发送”。
- UI 结果：回答 **27**，底部显示 **空闲**。
- durable 结果：
  - Run：`2a2f1e89ead057f4a853896d159be580`
  - request：`request-a072c047-7cb0-4732-afd3-03f6538ef273`
  - turn：`turn-a79c4619-60fd-4364-a23a-a6c07b9577e4`
  - status：`completed`
  - final：`{"text":"27"}` / `succeeded`
  - owner：`companion:relay_2ec0ae7233b2c99caa452c4f40a783cd:1`
  - profile generation / binding epoch：`1 / 1`
- 截图：[task10-main-message-e2e.jpg](./task10-main-message-e2e.jpg)
- 清理：19 个精确后代 PID，释放 private memory `9653006336` bytes；
  `8100/5173` 均释放，survivor=`0`。

## 结论

Task 10 的 single-capture authority、frozen Skill/Workflow、child independent ReadyGate、
execution fence、恢复一致性与主消息页状态投影已经形成同一生产链，并通过自动化和真人
E2E。没有独立 Code 模式，也没有新增第二套 Harness。
