# Testcase：deepresearch source-pack 优化

## 范围

覆盖 deepresearch source-pack 查询增强、coverage 可观测、配置 kill-switch、skill 文案收口。

## 自动化用例

| ID | 步骤 | 预期 |
|---|---|---|
| TC-SP-1 | 调用 `research_run("请调研一下俄乌最近的局势")`，mock search/extract/LLM | 搜索 query 包含 ISW/UN/Reuters/AP/BBC/Al Jazeera 等 source-pack 定向来源。 |
| TC-SP-2 | monkeypatch `[research].source_packs=false` 后运行相同流程 | 不生成任何 source-pack query，普通搜索仍执行。 |
| TC-SP-3 | 检查 `ResearchReport.coverage` | route 中包含 `source_packs_enabled`、`source_packs_hit`、`source_pack_queries`。 |
| TC-SP-4 | 读取 deep-research `SKILL.md` | 文案包含 source packs、Scrapling-first、禁止外层手动 web_search/web_fetch 拼接。 |

## 手工验证建议

1. 在 DeskPet 新会话输入：`请调研一下俄乌最近的局势，给我带来源的简明结论`。
2. 期望只出现一次 `deepresearch` 工具调用，报告保存后外层直接整理答复，不继续调用 `web_search`。
3. 后端日志/报告 coverage 中应能看到 source-pack 路由命中；报告引用应优先包含权威媒体、国际组织或专业战况评估来源。

## 手工验证结果 - 2026-07-08

PASS。已用真实 DeskPet UI 完成一次回归：

- session: `a8204ba6-18df-4856-ae05-de3a8bdb5f9e`
- 真实坐标点击：主窗 `消息` -> 消息窗 `+ 新话题` -> 输入框 -> `发送`
- 中文输入方式：Windows 剪贴板 + Ctrl+V
- UI 结果：出现 `deepresearch` 工具卡片、报告文件卡片、最终整理回复
- 日志结果：`name='deepresearch'`、`deepresearch_finalize_queued`、ISW/UN/Reuters/AP/BBC/Al Jazeera source-pack 查询均出现；未出现后续独立 `name='web_search'` 工具调用
- 证据：`plans/manual-results-2026-07-08-deepresearch-source-packs/RESULTS.md`

备注：首轮手测发现 source-pack 查询质量问题，已将 `Chinese sub-question + site:...` 改为 standalone authority-directed queries 后重新跑真机验证。
