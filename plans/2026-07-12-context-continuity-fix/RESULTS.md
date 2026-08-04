# Results：上下文连续性与图片误触发修复

## 自动化

- 聚焦/相邻回归：`139 passed`。
- 第二组 main/image/harness 回归：`63 passed, 1 failed`；失败为既有环境污染：`test_image_config_section.py::test_llm_base_url_fallback_is_read` 期望临时 URL，实际读取当前开发配置 `https://chinzy.com/v1`，不在本次改动路径。
- `py_compile`：本次 Python 改动文件全部通过。
- 五轮独立 plan challenge：最终 `VERDICT: PASS`。

## Windows Computer Use 真机 E2E

- 日期：2026-07-12。
- 第一轮真实输入后，UI 回复围绕《命运2》Apex Predator/Last Wish 与武器词条，没有生图。
- 第二轮真实输入 `是一个脉冲步枪` 后，UI 回复：`你说的是脉冲步枪，不是火箭筒`，继续给出 PvE/PvP 词条建议。
- UI 无图片、无图片卡、无“已生成图片”声明。
- `state.db`：第二轮为 messages `5120 user` → `5121 assistant`，两行 `tool_call_id/tool_calls` 均为空。
- 日志在两轮均出现 `memory_manager.l3_timed_out timeout_s=0.75`，但 L2 连续历史仍正确进入模型；这是真实失败隔离证据。
- receipt 搜索没有本次时间段的 `generate_image` 记录；命中项均为 2026-06-21/22 历史记录。

## 结论

`DECISION: SHIP`

## Follow-up：default session 搜索承接

- 现场证据：用户在脉冲步枪问答后输入“你能自己去查一下吗？中文网站里面之类的……”，该轮被分类为 `web_search`，日志显示 `l2_count_in=0/l2_count_out=0`，模型因而反问“它是什么”。
- 根因：`task/web_search.l2_page_in=followup` 依赖词法代词启发式，误把自然承接句判为新话题并主动关闭 L2。
- 第一轮修复仅把 page-in 改为 always 后，真机日志变为 `l2_count_in=1`，仍只召回紧邻的错误 assistant，真正对象在 4～6 条之前。
- 最终修复：`task` 与 `web_search` 改为 `l2_page_in=always, l2_top_k=8`，覆盖最近 3～4 个完整问答；仍只取同 session 连续尾部，跨 session L3 保持独立可降级。

## Follow-up 2：检索工具注册表漂移

- 扩大 L2 后，真机已能识别“它”是高阶暴君，但模型仍错误声称没有网页搜索工具。
- 根因：ContextAssembler 收到的是 legacy `tool_router`（仅 3 个 bootstrap 工具），AgentLoop 执行时使用的却是 `deskpet_tool_registry_v2`；因此策略白名单中的 `web_search` 在组装期被静默筛空。
- 修复：`main.py` 让 ContextAssembler 与 AgentLoop 共用 `deskpet_tool_registry_v2`；ToolComponent 根据实际筛选出的 schema 注入检索工具可用性约束，禁止有工具时声称不能联网。
- 自动回归：`test_agent_harness_main_callsite_contract.py + test_deskpet_context_assembler.py + test_l2_page_in.py` 共 `65 passed`。
- Computer Use 真机：default session 输入原追问后出现 `✅ web_search 结果`；日志记录实际查询 `命运2 高阶暴君 脉冲步枪`、`"高阶暴君" "命运2"`、`命运2 高阶暴君 小黑盒` 等，最终回答明确围绕高阶暴君，不再反问“它是什么”或声称没有联网工具。
