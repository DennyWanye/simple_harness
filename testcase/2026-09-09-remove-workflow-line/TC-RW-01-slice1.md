# TC-RW-01 删 workflow 线 Slice 1：守卫自检 + 主流程冒烟 + provider 请求断言

**绑定**: AC-1（决定性）、AC-2（决定性）、AC-3、AC-5（`plans/2026-09-09-remove-workflow-line/acceptance.md`）
**方式**: 脚本驱动的原生 App 冒烟（AX 发送）+ 脚本断言
**复用**: 交付版冒烟 `plans/2026-09-09-two-flow-journey/SMOKE-7e64dab0-MAINFLOW.md` 的 oracle（同一 `TF_TURNS` 子集）；当前 run 重新执行取证

## 步骤与预期

1. 守卫自检：`backend/.venv/bin/python -c "from deskpet.tool_catalog.manifest import load_tool_manifest, migrate_tool_schemas; m=load_tool_manifest(); mig,rec=migrate_tool_schemas(m); assert len(m.tools)==76 and len(rec)==70 and m.workflows=={}"`
   预期：退出 0。
2. 静态集合断言：`SDK_DIRECT_TOOL_KERNEL` 19 且无 `workflow_spawn`；`PRODUCT_TOOL_NAMES` 83 且 == 清单名 ∪ `HOST_COMPOSED_TOOL_NAMES`；`orchestration_controls` 无 `WORKFLOW_SPAWN`。
   预期：全部成立。
3. 冷启动（全新 userdata）：`launch_native_candidate.py --launch --source $PWD --bundle "…SimpleHarness Memory Verify ec7b28c7p18120.app" --installed-target …installed-h0710-m0638-s0313 --evidence-root .local-test-evidence/<日期>/<目录> --port 18120 --model deepseek-v4-flash --env-file …deepseek.env`；startup complete 后写 `<E1>/userdata/model_overrides.toml`（`[models."deepseek-v4-flash"] context_window = 32000 / reasoning_mode = "fast"`）。
   预期：native.log 有 `startup complete`，无 `RuntimeError` / `Application startup failed` / `Traceback`；AX 状态「空闲 · 排队 0」。
4. flow1：`A6_SEND=… A6_AXDUMP=… TF_TURNS="1 2 3 5 9 11" bash scripts/native/twoflow_driver.sh <bundle-id> <E1>/userdata <E1> flow1`
   预期：6 轮 `last_run_state == COMPLETED` 且每轮一次发送；任务目录出现 `clips.md`（A-01/A-02/A-03）。
5. 同 userdata 重启：三步杀干净（`pkill -f launch_native_candidate.py`、`pkill -f '<bundle>.app/Contents/MacOS'`、清 18120 监听）→ 同命令加 `--userdata <E1>/userdata` 重启。
   预期：`startup complete`，AX「空闲 · 排队 0」。
6. flow2：`TF_TURNS="12 14 16 22" … flow2`（evidence 仍传 `<E1>`）。
   预期：4 轮 COMPLETED；T12 答含「霜降素材 / 成片」；T14 绑定任务并复述决定；已知偏差：T16 可能不落 `inventory.md`、T22 因 T7 未跑而无字幕校对任务。
7. provider 请求断言：`backend/.venv/bin/python plans/2026-09-09-remove-workflow-line/assert_provider_requests.py <E1>/userdata/data`
   预期：`workflow_spawn` 0、catalog 提示词 0、tools 名字集合 ⊆ 交付版 14 个，打印 `AC-2(3) PASS`。
8. 旧 userdata 段：复制交付版 `native-smoke-7e64dab0/primary-ui-ncw5jyyg/userdata` 为 `<E>/old-userdata-stage/userdata`，`--userdata` 启动 → `TF_TURNS="12" … flow2`。
   预期：就绪 + T12 COMPLETED；步骤 7 对该目录同样 PASS。

## 结果回写

写在 `plans/2026-09-09-remove-workflow-line/journal.md` 的兑现表；本文件不记 PASS/FAIL。
