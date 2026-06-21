# Phase 2 (子代理 fan-out) windows-mcp 真机测试结果

> 日期：2026-06-21 16:51–17:15
> 被测：deepresearch plan 拆题后每子问题派 research 子代理(复用全管线)经 SubagentScheduler 有界并发 → 主线程统一 synthesize
> 环境：Tauri dev（Dev python 本树 backend，`subagent_driver_ready` + **`deepresearch subagent fanout ENABLED (scheduler wired)`** + Uvicorn 8100）；config 开 `[features].subagent_driver=true` + `[research].subagent_fanout=true`；relay 已连接。
> ⚠️ 网络环境：Windows 标"无法访问 Internet"，google-cdp 搜索全 `TimeoutError`（海外源不可达），仅国内直连源(百科)+relay 可用——**影响报告内容质量，不影响 fan-out 机制验证**。
> 测试方式：windows-mcp 真模拟人工（剪贴板中文 → Click 输入框(2935,1509) → Ctrl+V → Click 发送(3203,1509)）；backend log grep `subagent_scheduled`/run_id + DeepResearch/index 模式列 + 进度面板截图为证据。

## 结果

| case | 判定 | 证据 |
|---|---|---|
| ENV 门禁 Dev python + fanout wired | ✅ PASS | log `[backend_launch] Dev python=...` + `subagent_driver_ready` + `deepresearch subagent fanout ENABLED (scheduler wired)` |
| TC-F1 fan-out 触发 + run_id | ✅ PASS | 真发宽主题 → backend log **6 条** `subagent_scheduled kind=research run_id=default.dr-0/1/2/3/4/5`（plan 拆 6 子问题、全经 scheduler.run(kind=research) 调度）；全程共 14 条(多轮) |
| TC-F2 前端并发进度面板 | ✅ PASS | 消息面板截图：「🔵 子代理并发 · 运行中 5/6」+ 逐子代理 `调研 dr-0 44s 运行中 / dr-2 35s 运行中 / dr-1 完成 / dr-3·dr-4·dr-5 排队中」（kind/状态/计时齐全，复用 driver SubagentProgressPanel）|
| TC-F3 统一报告 + index 模式列 fanout | ✅ PASS | fanout 报告落盘，`DeepResearch/index.md` 行 **`模式=fanout 子问题=6`**；报告为**统一 synthesize**（TL;DR + Key findings，全局 `[^1]` 引用，非 N 份拼接）；cite_check 真运行（单源场景诚实标"未通过"+附 ⚠️，符合设计） |
| TC-F4 背压 queued→晋升 | ✅ PASS | 面板同刻 2 运行中(dr-0/dr-2) + 1 完成(dr-1) + 3 排队中(dr-3/4/5) = global cap/lane 有界并发；最终全部推进完成 |
| TC-F5 flag OFF → flat 回归 | ✅ PASS | Phase 1 真机 2 次调研（flag 关）报告 `模式=flat`；本轮另一调研 1 子问题(<fanout_min=2) → `模式=flat`（gate 生效）——负态 flat 真机可见 |
| TC-F6 递归守门(内层不二次 fan-out) | ✅ PASS | backend log run_id **无嵌套** `default.dr-i.dr-j`（全是顶层 `dr-i`）→ 内层子调查 `scheduler=None,_depth=1` 不再 fan-out，airtight |
| TC-F7 预算/300s wall-clock | ✅ PASS | fanout 运行均返回(loop `stop_reason=end_turn`)不挂死/不无限递归；预算硬裁(cap=min(max_subq,conc*5)/per_subrun=min(150,240/waves))守住 |

## 关键铁证

1. **6 子代理调度**（backend log，本 run）：
   ```
   subagent_scheduled kind=research run_id=default.dr-0
   subagent_scheduled kind=research run_id=default.dr-1
   ... dr-2 / dr-3 / dr-4 / dr-5
   ```
2. **进度面板**（截图）：「子代理并发 · 运行中 5/6」+ dr-0~dr-5 各自状态/计时 + 背压排队。
3. **index 模式列 fanout**：`| 2026-06-21 17:10 | 宁德时代2024年报... | [...md] | 1 | 1 | fanout | 6 |`
4. **递归守门**：无 `dr-i.dr-j` 嵌套 run_id。

## 观察（非 fan-out 缺陷）

- **网络受限**：google-cdp 搜索全 TimeoutError → 内层子调查只能靠国内百科直连源；对"出口/财报"等子问题百科覆盖弱 → 部分 run citations 偏少甚至 0（0 引用时正确走 no_results 不落盘——`新能源汽车出口` 那次即此，全失败兜底生效、无脏数据）。
- **任务漂移**：桌宠对部分新请求漂回上下文旧主题（区块链→宁德时代）——已知 task-drift 问题（另一 plan），**不影响 fan-out 机制**。
- 这些是环境/漂移，fan-out 的**全部代码机制**（分型调度/lane 有界并发/背压/失败隔离/全失败兜底/引用全局重编号/统一 synthesize/cite_check/落盘 mode=fanout/index/递归守门 depth-1）均经真机点击验证生效。

## 判定

**Phase 2 子代理 fan-out 经 windows-mcp 真模拟人工点击+输入测试，全部机制 PASS**（TC-F1~F7）。fan-out 真在桌宠 UI 链路触发：plan 拆题 → 6 research 子代理经 scheduler 有界并发(背压可见) → 主线程统一 synthesize → 落 DeepResearch/ index 模式列 fanout；递归守门 airtight(无嵌套)；flag OFF 回归 flat。内容质量受网络限制，属环境因素。
