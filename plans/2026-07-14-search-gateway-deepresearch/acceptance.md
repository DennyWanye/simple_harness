# 验收标准：DeskPet Search Gateway 与 DeepResearch 可视化升级

> 状态：已确认并执行；技术实现与自动化完成，完整真实 DeepResearch/Windows DoD 因 relay 401 保持 PARTIAL。
> 日期：2026-07-14

## 主要矛盾

在不依赖付费搜索 API、不要求用户部署服务器、Docker 或完整 SearXNG 的前提下，DeskPet 需要以纯桌面应用形态同时获得：

1. 开箱即用、失败可降级的快速搜索；
2. 多来源、可恢复、可验证的深度研究；
3. 用户在聊天消息流里能看见 DeepResearch 的总体进度，以及展开后能审计每一步做了什么。

## 已确认的方案方向

- 默认内置的是 **SearXNG-like Search Gateway**，不是把完整 SearXNG 服务打进安装包。
- Search Gateway 位于现有 Python backend 内，统一提供异步搜索、引擎编排、聚合去重、排序、缓存、失败冷却、降级和诊断。
- 真正的 SearXNG 保留为可选 provider；用户配置 URL 后可加入 Gateway 路由，但不成为安装或运行前提。
- `web_search` 与 `deepresearch` 共用同一个 Search Gateway，不再各自维护不同的搜索主路径。
- DeepResearch 使用原生 durable workflow；子问题并行必须进入原生图及 checkpoint/recovery 语义，不只存在于 legacy/direct core 路径。
- DeepResearch 每完成一个用户可理解的阶段，都产生一条持久化聊天进度事件；阶段气泡默认折叠隐藏，总体进度始终可见。
- 重启应用、WebSocket 重连、切换会话后，进度历史和总体状态必须完整恢复，后台任务可继续推进。

## 范围

### 包含

- Search Gateway 统一异步接口与 provider adapter；
- Google/Bing Edge CDP、百度、DuckDuckGo、可选 SearXNG、一手直连源的路由和降级；
- `web_search` 默认路径修复、结果契约与可观测诊断；
- DeepResearch 原生图的子问题并行、确定性合并、补证、精排、引用自检和报告交付；
- `web_fetch`、DeepResearch、Scrapling 抓取和 Trafilatura 抽取的共享抓取/抽取服务；
- DeepResearch 总体进度、阶段气泡、折叠交互、历史恢复和错误展示；
- 自动化测试、真实网络 smoke、Windows 桌面真机点击 E2E 和质量基准集；
- 配置、诊断、用户文档和 `STATUS/status.md` 同步。

### 明确不包含

- 不把完整 SearXNG、Docker、Valkey 或第二套 Python 服务默认打进 DeskPet 安装包；
- 不要求用户拥有云端 relay 或自行部署服务；
- 不以自动破解 CAPTCHA、代理池或规避上游服务限制作为可靠性方案；
- 不引入付费搜索 API 作为默认必需依赖；
- 不用 Crawl4AI 等新重型爬虫替换已经可用的 Scrapling + Trafilatura + Edge CDP 主链；
- 不在普通聊天中展示原始 prompt、工具参数、token、内部异常栈等 Trace 细节；
- 不重写普通 ReAct AgentLoop、ToolRegistry 或整个聊天消息系统。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|---|---|---|---|
| AC-SG-01 | 统一异步 Search Gateway | 存在单一异步入口，`web_search` 和 DeepResearch 都通过它获得标准化搜索结果；生产路径不再让 `web_search` 调用会跳过默认 CDP/SearXNG 引擎的同步实现。 | 必须 |
| AC-SG-02 | Provider adapter 契约 | Google CDP、Bing CDP、百度 HTML、DuckDuckGo HTML、可选 SearXNG 均实现同一输入/输出/错误契约；单个 adapter 可独立测试、禁用和替换。 | 必须 |
| AC-SG-03 | 桌面默认可用 | 未配置 SearXNG、API Key、Docker 或云服务时，安装后的 DeskPet 仍能执行快速搜索；默认配置至少有一个本地可运行 provider，并在不可达时返回可理解诊断而非伪造答案。 | 必须 |
| AC-SG-04 | 动态路由与降级 | Gateway 根据查询语言、引擎可达性、冷却状态和配置决定顺序；首选引擎超时、空结果、403、429、CAPTCHA 或解析失败时，在总预算内自动尝试下一 provider。 | 必须 |
| AC-SG-05 | 聚合、去重与排序 | Gateway 可并发或分批聚合多 provider 结果；按 canonical URL 和内容/标题指纹去重，保留来源引擎和原始 rank，并应用相关性、时效性、域名质量与来源多样性排序。 | 必须 |
| AC-SG-06 | 缓存、冷却与有界资源 | 相同规范化查询在 TTL 内复用缓存；连续失败的 provider 进入有界冷却；并发、每引擎超时、总超时和 CDP 次数均有配置上限，取消请求后不遗留浏览器/协程任务。 | 必须 |
| AC-SG-07 | 标准结果与诊断 | `web_search` 返回 query、results、count、engine/rank/searched_at，以及 engines_tried、engines_hit、errors、elapsed_ms；诊断不包含凭据、Cookie 或敏感请求头。 | 必须 |
| AC-SG-08 | 快速搜索正文增强 | `web_search` 支持有界 `hydrate_top`（默认关闭或小值）；开启时仅通过共享抓取层补全前 N 条正文摘要，失败不影响其余搜索结果。 | 应有 |
| AC-SG-09 | 可选 SearXNG | 配置合法 `/search` URL 后，SearXNG adapter 可请求 JSON、解析结果并加入 Gateway；未配置、JSON 被禁用或服务不可达时自动跳过，不影响默认桌面搜索。 | 必须 |
| AC-SG-10 | 默认开启与兼容 | Search Gateway 作为测试阶段默认 ON；现有 `web_search` 工具名、主要参数和上层调用契约保持兼容，必要的新字段只做向后兼容扩展。 | 必须 |
| AC-FE-01 | 共享 FetchExtractService | `web_fetch`、`scrapling_fetch`、快速搜索 hydrate 和 DeepResearch 共用一个抓取/抽取服务，消除 `web_tools.py` 与 `research_tools.py` 的重复 Scrapling 包装和漂移。 | 必须 |
| AC-FE-02 | 抓取降级顺序 | 默认顺序为 URL 规范化/安全检查与缓存 → Scrapling → httpx → 符合空壳判定时 Edge CDP → 显式启用时 Jina → Trafilatura/结构化抽取；每次返回实际 fetcher/extractor。 | 必须 |
| AC-FE-03 | 内容质量与复用 | 抓取结果包含 canonical_url、title、text、published_at、fetched_at、content_hash、quality_flags；重复内容、乱码、AI 声明页、空正文和异常 Content-Type 有确定处理。 | 必须 |
| AC-DR-01 | 共用搜索底座 | 原生 DeepResearch 的所有通用检索节点通过 Search Gateway；一手源直连仍保留独立权威路径，但输出进入同一候选与证据契约。 | 必须 |
| AC-DR-02 | 原生子问题 fan-out | plan 产生达到阈值的子问题后，原生 durable graph 按有界并发创建可 checkpoint 的研究分支；默认不再只做扁平批量搜索，也不依赖 legacy `_run_subagent_fanout()` 才能并行。 | 必须 |
| AC-DR-03 | 确定性 join | 子问题分支以稳定 ID 合并候选、passage、引用和错误；不同完成顺序、重试或恢复不得改变去重结果及最终引用编号。 | 必须 |
| AC-DR-04 | 分支预算与部分失败 | 每个分支有 URL、抓取、LLM、超时和重试预算；单分支失败不取消健康分支，最终报告明确标出未覆盖子问题和降级原因。 | 必须 |
| AC-DR-05 | 证据质量 | DeepResearch 保留拆题、查询扩展、source packs、一手源、抓取、分层评分、深度模式补证、全局精排、来源多样性和引用编号检查；新增或强化 claim-evidence 支持检查，不能仅验证脚注编号存在。 | 必须 |
| AC-DR-06 | 可交付报告 | 成功报告包含直接回答、来源支撑发现、分析/推断区分、局限与反证条件、引用 appendix、覆盖度、错误摘要及 Markdown Artifact；无可用来源时明确失败，不以模型预训练知识伪造引用。 | 必须 |
| AC-UI-01 | 总体进度可见 | 调研开始后，聊天消息框始终显示一条与 run_id 绑定的总体进度入口，至少包含当前阶段、已完成/总阶段、已用时和 running/waiting/completed/failed/cancelled 状态。 | 必须 |
| AC-UI-02 | 每阶段新增隐藏气泡 | normalize/plan/expand/search/direct/fetch/score/gap/rerank/synth/cite/persist/finalize 中每个用户可理解阶段完成时，新增一条持久化阶段气泡；正常完成气泡默认折叠隐藏，用户展开总体进度后能按时间查看。 | 必须 |
| AC-UI-03 | 阶段内容可理解 | 每条阶段气泡只显示用户安全信息：阶段名称、结果摘要、数量、耗时、是否降级和下一步；不泄露 prompt、工具参数、内部路径、token、Cookie、密钥或 Python traceback。 | 必须 |
| AC-UI-04 | 重要状态不隐藏 | waiting/需要用户操作、不可恢复失败、取消和最终完成必须在主消息流可见；阶段局部失败若已自动降级，显示在折叠详情和总体警告计数中。 | 必须 |
| AC-UI-05 | 去重与有序投影 | 每个阶段事件有稳定 event_id、run_id、stage、sequence、transition 和时间；重试、WebSocket 重连、重复 delivery 不生成重复气泡，乱序事件不能把 UI 状态回退。 | 必须 |
| AC-UI-06 | 重启与历史恢复 | 应用重启、会话切换或历史重新加载后，总体进度与全部阶段气泡从持久化事件重建；未完成 run 继续接收进展，已完成 run 保持相同历史和最终状态。 | 必须 |
| AC-UI-07 | 并发与可访问性 | 同一会话多个 DeepResearch run 各自独立分组；折叠/展开、状态和进度条具备键盘操作及 ARIA 属性；历史回放更新不得强制把正在向上阅读的用户拉回底部。 | 必须 |
| AC-OBS-01 | 搜索可观测性 | Trace/诊断记录 provider 尝试、命中、缓存、冷却、超时、候选/去重/抓取数量和阶段耗时；用户层只投影白名单摘要。 | 必须 |
| AC-OBS-02 | 质量基准 | 建立固定中英文查询集，覆盖事实、最新、官方文档、学术、财报/政策、JS 页面、转载重复和冲突来源；输出 web_search 成功率/空结果率/Recall@5/P95，以及 DeepResearch 引用覆盖、引用支持、独立域名、完成率/P95。 | 必须 |
| AC-TEST-01 | 自动化闭环 | provider、Gateway、默认配置 wrapper、抓取层、原生 fan-out、deterministic join、进度事件、恢复和 UI reducer 均有单元/集成测试；必须包含能复现当前同步 `web_search` + 默认 `google-cdp` 空结果问题的回归测试。 | 必须 |
| AC-TEST-02 | 真实网络 smoke | 在可控网络环境分别验证中文和英文快速搜索、引擎降级、至少一个动态页面抓取及一份真实 DeepResearch；记录真实引擎、耗时、错误和来源，不用 mock 结果代替。 | 必须 |
| AC-TEST-03 | Windows 真机 E2E | 按项目纪律用 Computer Use/Windows MCP 真正输入快速搜索与 DeepResearch 请求，验证总体进度、逐步隐藏气泡、展开详情、后台继续、重启恢复、最终带引用报告和 Artifact；每个 case 留截图与 backend 日志。 | 必须 |

## 非功能与边界

- **性能**：快速搜索每个 provider 有独立超时且总预算有硬上限；正常可达网络目标 P95 首批可用结果不超过 5 秒，达不到时必须在基准报告说明瓶颈。DeepResearch 默认允许 2–5 分钟，但必须持续产生阶段进展，不能表现为无反馈挂起。
- **并发**：Gateway 和 DeepResearch fan-out 均使用现有有界调度/信号量；不得为每个 URL 无界创建浏览器或线程。
- **可靠性**：外部引擎全失败时返回结构化失败与重试建议；不得用模型常识填充成“搜索成功”。
- **隐私**：搜索词只发送给实际启用的上游；诊断和进度事件不记录密钥、Cookie、Authorization 或完整敏感 URL 参数。
- **兼容**：普通聊天、Code 模式、现有工具名、旧 Session 消息、历史 DeepResearch Artifact 和旧 workflow run 保持可读。
- **默认策略**：测试阶段完成的 Gateway、原生 fan-out、进度事件和 UI 默认开启；只允许未完成、危险或兼容回退路径保持关闭并标注原因。
- **维护性**：新增 provider 不需要修改上层 Agent、DeepResearch 图和 UI；只实现 provider 契约并注册。
- **许可证**：真正的 SearXNG仅作为外部可选 HTTP provider；本计划不分发或修改 AGPL SearXNG 源码。

## 完成定义（DoD）

- 所有“必须”AC 都有 `AC → plan task → code → testcase → result` 可追溯证据；
- 自动化聚焦测试和相关回归全绿；
- 真实网络 smoke 有可复核输出；
- Windows 真机完成快速搜索、DeepResearch 进度/展开/重启恢复/报告交付闭环；
- 所有完成能力默认 ON；
- `ARCHITECTURE/`、本 plan 目录、`testcase/`、用户文档和 `STATUS/status.md` 同步；
- 没有已知 P0/P1 搜索断点、重复进度消息、伪造来源或无法恢复的活跃 DeepResearch run。
