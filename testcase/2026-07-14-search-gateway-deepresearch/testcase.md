# Search Gateway 与 DeepResearch v2 验收用例

> 对应计划：`plans/2026-07-14-search-gateway-deepresearch/plan.md`
> 验收基线：`plans/2026-07-14-search-gateway-deepresearch/acceptance.md`
> 原则：自动化、真实网络和 Windows 真机证据分层记录；脚本不能替代真机点击。

## 自动化用例

| TC | 覆盖 AC | 操作 | 通过条件 |
|---|---|---|---|
| TC-A01 Gateway provider 合同 | SG-01/02/04/05/06/09/10 | 运行 Search Gateway config/provider/routing/ranking 测试 | 默认 ON；各 provider 可独立测试；降级、去重、缓存、冷却、取消和 SearXNG 可选路径全绿 |
| TC-A02 生产工具接线 | SG-01/03/07/08/10、DR-01 | 运行 web_search 与 production wiring 测试 | `web_search` 和 v2 通用搜索均 await 默认 Gateway；结果与诊断契约稳定；无共享 last-result 竞争 |
| TC-A03 共享抓取层 | FE-01/02/03、SG-08 | 运行 FetchExtractService 与上层 adapter 测试 | Scrapling→httpx→CDP→Jina 顺序正确；Jina 双开关；所有生产 adapter 共用服务；所有入口执行 2 MiB 硬门 |
| TC-A04 v1 不变性 | TEST-01、兼容性 | 校验 LF/CRLF manifest golden 与六阶段 checkpoint fixture | v1 manifest、完整 state、report、artifact、final payload 与 fixture 一致 |
| TC-A05 v2 固定图与 fan-out | DR-02/03/04、TEST-01 | 运行 v2 graph/native/recovery 测试 | 固定 6 slot × 5 branch stages；有界并发；inactive slot no-op；不同完成顺序不改变报告 hash/引用 |
| TC-A06 v2 证据质量 | DR-05/06 | 运行 source packs、分层评分、recency、deep 两轮 gap、semantic/diversity rerank、claim support、no-results 测试 | 补证走同一评分；deep 最多两轮；发布时间保留；全局重排兼顾语义与域名；不支持论断不交付；失败报告保留错误摘要 |
| TC-A07 durable progress | UI-01/02/03/04/05/06/07、OBS-01 | 运行 workflow progress、recovery、sessionsStore、MessageStreamPanel、WorkflowProgressGroup 测试 | 13 阶段各一条持久化 completed intent；默认折叠；去重/乱序/终态/恢复正确；运行中 elapsed 实时增长；固定 schema metrics 落盘 |
| TC-A08 完整回归 | TEST-01 | 运行 backend 全量 pytest、frontend 全量 vitest、tsc、build | 新增功能无失败；若存在基线旧失败，必须逐项与 baseline 对照并记录 |

## 真实网络用例

| TC | 覆盖 AC | 操作 | 通过条件 |
|---|---|---|---|
| TC-N01 中英文快速搜索 | SG-03/04/07、TEST-02 | 在无 SearXNG、无付费 API 的环境运行中文和英文查询 | 两类查询均有真实结果；记录 engines tried/hit、错误、耗时；首选失败后可降级 |
| TC-N02 动态/正文抓取 | FE-02/03、TEST-02 | 抓取一页真实文档并记录 fetcher/extractor | 得到非空正文、canonical URL、hash、质量字段；无 mock |
| TC-N03 DeepResearch 真实报告 | DR-01/02/05/06、OBS-02、TEST-02 | 通过真实 LLM 与真实网络完成一次 standard/deep 调研 | 13 阶段完成；有真实引用、独立域名、claim support、Markdown Artifact；记录完成率与耗时 |

## Windows 真机用例

每个用例必须遵循：截图/窗口状态 → 声明坐标与预期 → 真点击/真输入 → 截图 → backend 日志判定。

| TC | 覆盖 AC | 操作 | 通过条件 |
|---|---|---|---|
| TC-W01 快速搜索 | SG-03/07、TEST-03 | 在普通 Session 输入一个中文时效查询，允许一次抓取权限 | 消息流展示真实搜索答案和来源；日志确认 `web_search` 走 Gateway |
| TC-W02 DeepResearch 逐步可视化 | UI-01/02/03/04/07、TEST-03 | 输入明确的深度调研请求；观察总体卡；展开/收起阶段 | 总体进度始终可见；运行中耗时增长；完成阶段新增隐藏气泡；展开后按序可审计；键盘与 ARIA 可用 |
| TC-W03 重启恢复 | UI-05/06、TEST-03 | DeepResearch 运行中关闭并重启 DeskPet，重新打开原 Session | 后端继续/恢复 run；已交付阶段不重复；总体卡和阶段历史重建；日志记录 recovered run/delivery |
| TC-W04 最终交付 | DR-06、UI-04/06、TEST-03 | 等待同一 run 结束并打开 Artifact | 最终状态明确；报告有真实引用、错误摘要和 Markdown 文件卡；历史重载后不重复 |
| TC-W05 并发隔离 | DR-03/04、UI-07、TEST-03 | 同一会话先后发起两个 DeepResearch run | 两个 run 独立分组；进度、诊断、引用和终态不互相覆盖 |

## 证据与判定规则

- 自动化原始输出和 benchmark/smoke JSON：`plans/2026-07-14-search-gateway-deepresearch/`。
- Windows 真机日志和截图说明：`plans/manual-results-2026-07-14-search-gateway-deepresearch/`。
- 结果总表：`plans/2026-07-14-search-gateway-deepresearch/results.md`。
- 遇到登录、relay、网络或验证码等外部阻断时，记录为 `BLOCKED`，不得写成 `PASS`；已完成的下层证据仍单独保留。
