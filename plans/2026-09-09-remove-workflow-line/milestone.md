# 价值验证里程碑（Task 5a）· 2026-09-10 00:12

## 实测

| 步骤 | 结果 |
|---|---|
| 守卫自检 `load_tool_manifest` + `migrate_tool_schemas` | 通过：76 条工具、70 条迁移记录、`MANIFEST_SHA256 = df979c0e…` |
| 七文件测试门 | 2 failed / 89 passed，失败集合与 baseline 两条既有红逐条相同 |
| AC-4 两条 grep 等式 | `-w`：非 vendor 11 个白名单文件 + 21 个 wheel；`-i` 无 `-w`：非 vendor 12 个（多 `scripts/perf/x2_memory_lanes_growth.py`）；均与 acceptance 相等 |
| AC-5 边界 | `git status` 无 `main.py` / `deskpet/workflows/` / `tauri-app/` 改动；SDK 仓仍只有 ` M .gitignore` |
| 冷启动（全新 userdata，bundle `ec7b28c7p18120`，后端从 `--source` 起） | `startup complete`；native.log 无 `RuntimeError` / `Application startup failed` / `Traceback`；证据 `.local-test-evidence/2026-09-09/remove-workflow-slice1/primary-ui-0zda7ryu` |
| T1「记住成品目录」 | `COMPLETED`，11.0 s，一次发送；`context_route_decisions` 1、`typed_recall_requests` 3、`provider_invocations` 2 |
| AC-2③ provider 请求断言 | 2 次调用：`workflow_spawn` 0 次、catalog 提示词 0 次；tools 名字集合 12 个 ⊆ 交付版 14 个 |

## 给用户看的一句话

删掉 `workflow_spawn` 的定义、清单条目和提示词之后，同一个交付版 bundle 用新后端冷启动正常，第一轮对话正常完成，模型看到的工具集和交付版一致。

## 矛盾转化再分析

- 主要矛盾「删掉之后主流程还能不能原样跑通」在冷启动 + 一轮上已经成立；决定成败的问题转为**「10 轮 + 重启 + 旧 userdata 的完整路径上有没有回归」**（AC-1 深测）以及**「删得是否干净、有没有留半截」**（code review、AC-4/AC-7）。
- 剩余任务排序不变：Task 5b（深测）→ Task 6 已完成（测试对齐随 Task 1–4 同步做了，门已绿）→ Task 7（独立 code review）→ Task 8（文档 + 提交）。
- 没有发现需要回炉（A2）的 plan 层缺陷。
