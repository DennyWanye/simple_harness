# 任务：生成 Phase 2（子代理 fan-out）的 windows-mcp 手工测试文档

为已实现的 fan-out（plan WI-1~6/§5）写一份详尽手工测试文档，供 windows-mcp **真模拟人工点击+输入**执行。把文档**写到** `G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/exec/manual_test_phase2_draft.md`。

## 先读
- plan §0/§3/§4/§5/§7 WI-1~6 + §10.2 真机 E2E（TC-F1~F5）：`G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md`
- 实现真相：`G:/projects/deskpet/backend/deskpet/tools/research_tools.py`（`_run_subagent_fanout`/分叉/scheduler.run run_id=`<sid>.dr-i`/coverage.subagent_fanout）、`subagent_scheduler.py`（日志锚点 `subagent_scheduled kind=%s run_id=%s`）
- 参考写法/详尽度：`G:/projects/deskpet/testcase/2026-06-21-deepresearch-subagent-fanout/manual-test-phase1-wi8.md`（同目录 Phase 1 文档）+ `testcase/2026-06-20-deepresearch-search-reliability/manual-test.md`

## 被测行为（要全覆盖）
1. 前置 flag：`features.subagent_driver=true`（构造 scheduler）+ `[research].subagent_fanout=true`（开 fan-out）。两者缺一不可（否则走扁平）。
2. **TC-F1 fan-out 触发**：真发一个会拆 ≥2 子问题的深度调研 → backend log（tauri-dev.log）出现 **N 条** `subagent_scheduled kind=research run_id=<sid>.dr-0/1/2...` 并发；最终报告 `coverage.mode=="fanout"` + `coverage.subagent_fanout`（n_subagents/n_completed/waves）。
3. **TC-F2 进度面板**：桌宠前端出现子代理并发进度（复用 driver 的 SubagentProgressPanel，主消息面板「🤖子代理并发」running/queued）。
4. **TC-F3 统一报告**：最终报告是**跨子问题统一分析**（不是 N 份拼接）+ 引用全局连续 + 引用自检通过 + 落 DeepResearch/（index 模式列=`fanout`）。
5. **TC-F4 背压**：子问题数 > 并发 cap 时，log 证部分 running 部分 queued 后晋升，全部完成不丢（best-effort，取决于实际拆题数）。
6. **TC-F5 flag OFF 回归**：`subagent_fanout=false` 重启 → 同问题走扁平、**无** `subagent_scheduled` log、报告正常、index 模式列=`flat`。
7. **递归守门（§5）**：fan-out 内层子调查**不再触发二次 fan-out**（log 中 dr-* 的内层无新的 subagent_scheduled 嵌套）；可作 TC-F6。
8. depth-1 / 预算：观测最坏 wall-clock 不破 300s tool 超时（报告正常返回不 tool_timeout）。

## 文档要求（项目手测纪律）
- 环境前置：开 flag 的方式（改 dev config.toml `[features] subagent_driver=true` + `[research] subagent_fanout=true`，给出 config.toml 真实路径探测方法：backend 读 `resolve_config_path()`，dev 下通常 `%APPDATA%\deskpet\config.toml` 或 `DESKPET_CONFIG` env）+ 重启 Tauri dev（DESKPET_BACKEND_DIR/DESKPET_PYTHON 注入，确认 Dev python）+ dev relay 自动登录。
- 每 TC：`坐标=(x,y)|动作=click/type|期望=...` declare 行 + 真模拟点击/剪贴板中文输入 + **判定证据**（截图 + tauri-dev.log grep `subagent_scheduled kind=research`/`fanout`/coverage + DeepResearch/index.md 模式列 + 报告正文）+ PASS/FAIL。
- 禁脚本回放/import/WebSocket 当 UI 证据。flag-on/off 两态都要真机验。
- 文档头部元信息（被测/范围/目的/用例数/是否需 windows-mcp）。

## 产出
完整 Markdown 写到 `exec/manual_test_phase2_draft.md`；末尾简述 TC 数 + 覆盖边界。
