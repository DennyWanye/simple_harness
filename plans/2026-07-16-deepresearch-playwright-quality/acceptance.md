# 验收标准：DeepResearch 内置 Playwright、证据质量门与稳定交付

> 状态：已确认（2026-07-16）；同日经用户确认将本轮平台范围修订为仅 Windows 11 x64。
> 日期：2026-07-16。
> 典型失败样本：Session `87bd0c52-6221-42ea-a99a-f383f4b59c3b`，run `1454d11734354b5fae10db8c565646e9`。
> 本文件是本计划的唯一验收事实源；实现完成后，生产事实回写 `ARCHITECTURE/`，不写 `STATUS/` 正文。

## 主要矛盾

当前 DeepResearch 可以在核心研究维度完全缺失时，仍凭借事实条数、引用数、独立域数和逐句支持率发布 `completed` 报告。系统证明了句子“有出处”，却没有证明报告完整、直接、有结论地回答了用户问题。

本计划的成败标准不是新增多少搜索或浏览器能力，而是建立一条**以用户答案质量为终点、以研究维度覆盖为调度依据、以有界补证和诚实降级保证稳定交付**的生产链路。Playwright 是动态页面和高价值抓取失败的受控补证能力，不是替代质量门的万能抓取器。

## 已确认的方案选择

- 内置完整 Chromium new-headless，由 Python Playwright 驱动，随 PyInstaller backend 和 Tauri 安装包交付；用户首次运行不下载浏览器。
- 本轮仅以当前 Windows 11 x64 主机作为发布硬门；Windows 10 兼容与安装包认证延期到后续独立计划，不阻塞本轮实现、默认开启或交付。不得把本轮 Windows 11 证据外推为 Windows 10 已兼容。
- 普通抓取优先，Playwright 仅用于 JS 空壳、正文不足或已知需要交互的高价值 URL；不让 `browser-use` LLM Agent 成为 DeepResearch 默认抓取路径。
- 完整质量不足时自动继续补证。300 秒是首次**软预算检查点**，不是强制终止线；只要覆盖度、第一方来源或报告质量仍有可量化进展，就按 120 秒租约自动续期，默认自动研究安全上限为 900 秒。到达自动上限后安全 checkpoint 并交付 `partial` 或 `insufficient_evidence`，用户可从原证据继续研究，不得伪装成 `completed`。
- 补证不使用固定“最多 3 轮”一刀切，而以缺口工作队列、研究租约、upstream/浏览器预算和连续无增益检测共同收敛；连续两个补证周期没有覆盖或来源质量增益时应提前停止空转。
- 生成式 LLM 不设置“最多 3 次”的固定产品上限；改为按研究 profile、核心维度、证据规模和模型能力分配总 input/output token 与成本预算。每次调用必须属于问题建模、查询策略、维度分析、报告综合、质量审计或定向修复之一，并以质量增益/预算决定是否继续。
- 用户可随时选择“立即用现有证据生成”，安全终止后续补证并生成当前可达到的 `completed`、`partial` 或 `insufficient_evidence` 结果。
- 所有主题遵守统一质量契约；在此之上提供 `technology_intelligence`、`policy_education`、`generic_research` 三个报告 profile，不能继续让非 AI 技术问题完整回退到旧 V3 报告。
- 内置 SearXNG-like Search Gateway 继续作为默认搜索能力；真正的 SearXNG 只保留为可选 HTTP provider，不打包 SearXNG 服务或 Docker。

## 范围

### 包含

- 研究问题结构化、核心维度覆盖矩阵和缺口状态；
- 中文友好的查询生成、相关性评分、官方来源优先和跨转载血缘去重；
- Search Gateway 通过缺口工作队列和自适应研究租约执行有界补证；
- Playwright + 完整 Chromium 的依赖固定、离线打包、运行时定位、进程池、隔离上下文、取消和崩溃恢复；
- CAPTCHA、登录墙、搜索导流、导航页、空壳、正文错配和重复转载的拒绝策略；
- `completed / partial / insufficient_evidence` 三态交付与可继续研究；
- 通用专业报告契约和技术、政策教育、通用研究 profile；
- 结论相关性、覆盖度、来源质量、综合推理、时效/不确定性和可读性质量门；
- “立即用现有证据生成”、继续补充调研和历史恢复；
- 面向用户的覆盖进度、补证原因、阶段结果和最终质量状态；
- 自动化、真实网络、冻结 backend、安装包和 Windows 桌面真机 E2E；
- `ARCHITECTURE/`、plan、testcase 和结果证据回写。

### 明确不包含

- 破解、代答或规避 CAPTCHA；
- 默认复用用户个人 Chrome/Edge 登录态、Cookie 或扩展；
- 让 Playwright 替换全部 Scrapling/httpx 抓取；
- 让 `browser-use` LLM Agent逐页操作普通研究网页；
- 打包完整 SearXNG、Docker、代理池或付费搜索 API；
- 没有覆盖/来源/质量增益仍无限重试，超过自动研究安全上限后继续无人值守消耗，或用无关事实凑报告；
- macOS/Linux 安装包支持；
- 重写普通 ReAct AgentLoop、ToolRegistry 或与 DeepResearch 无关的浏览器自动化平台；
- 自动发布、评论、登录或执行其他外部写操作。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|---|---|---|---|
| AC-INTENT-01 | 结构化研究维度 | plan 阶段为每个问题生成稳定 `dimension_id`、问题、`core/supporting` 重要性、期望来源类型与状态；固定教育题必须至少覆盖现状、规模/趋势、城乡或区域均衡、教师/财政、双减/课后服务/负担、国家下一步计划，不能只保存空的 `requested_dimensions` | 必须 |
| AC-INTENT-02 | 核心覆盖矩阵 | 每个核心维度在证据、结论和最终交付中均可追踪为 `covered/partially_covered/uncovered/not_applicable`；任一核心维度 `uncovered` 且未被明确披露时，发布门强制失败 | 必须 |
| AC-QUERY-01 | 中文查询改写 | 不再只搜索完整自然语言问题和追加英文 `official`；每个核心维度生成通用、官方、时间/统计和对比查询，中文关键词可分词并参与相关性计算；教育题必须产生可审计的教育部/政府站点定向查询 | 必须 |
| AC-QUERY-02 | 补证只针对缺口 | 每轮 rescue 只为 `uncovered/partially_covered` 的核心维度生成和执行查询，不重复搜索已充分覆盖维度；trace 可证明每轮缺口输入、查询数、结果数和覆盖变化 | 必须 |
| AC-SG-01 | 维度优先候选分配 | Search Gateway 与 DeepResearch rerank 在固定 passage 预算下先保障核心维度，再考虑来源权威、相关性、时效、域名多样性；教育题不得再出现“规模师资占 6/12、双减占 0/12”而仍发布完整报告 | 必须 |
| AC-SG-02 | 中文相关性非退化 | 对固定中文查询和相关/无关候选，相关候选得分严格高于无关候选；真实教育 run 的全部有效文档不得因按空格拆词而统一得到 `relevance=0.0` | 必须 |
| AC-SG-03 | 官方来源优先 | 已检索到国务院、教育部、财政部等原始政策或统计页面时，百度百科、新闻转载和聚合页不得取代其在对应核心结论中的主引用；来源选择理由可审计 | 必须 |
| AC-SG-04 | 来源血缘去重 | 同一原始公报或政策的官网、百科、新闻和镜像转载归入同一 source family；转载不重复贡献独立来源、维度覆盖或质量分，保留链接仅作为补充路径 | 必须 |
| AC-SG-05 | 自适应有界补证 | 首轮覆盖不足后，以缺失核心维度为工作队列自动补证；300 秒到达软检查点时，若新覆盖维度、第一方 source family 或质量分仍有增长，则每次续租 120 秒，默认自动上限 900 秒；达到覆盖线立即停止，连续两个补证周期无可量化增益时提前停止空转 | 必须 |
| AC-SG-06 | 查询策略 LLM 受控使用 | rescue 默认先使用确定性查询模板；确定性策略无增益且预算允许时，可调用一次有明确缺口输入和结构化输出的查询策略 LLM，调用必须记录 role、触发原因、token 与覆盖变化；不得让逐网页 browser-use agent或无职责自由循环进入补证 | 必须 |
| AC-PW-01 | 固定依赖与浏览器版本 | Playwright 使用精确版本并绑定对应 Chromium revision；锁文件、构建日志和运行时诊断能报告相同版本，升级 Playwright 未同步浏览器时构建失败 | 必须 |
| AC-PW-02 | 浏览器随安装包交付 | PyInstaller `COLLECT` 与 Tauri resources 包含完整 Chromium new-headless、Playwright driver 和所需资源；干净机器断网安装后可成功启动和渲染固定动态页面，运行期间不访问 Playwright CDN、不提示下载浏览器 | 必须 |
| AC-PW-03 | 受控渲染顺序 | 生产抓取顺序为静态 FetchExtractService → bundled Playwright → 系统 Edge CDP 兼容回退 → 明确失败；仅当内容质量策略判定 JS 空壳/正文不足/需要有限交互时调用 Playwright，普通静态页面不启动浏览器 | 必须 |
| AC-PW-04 | 浏览器生命周期 | backend 复用有界 Chromium 实例，每个任务使用隔离 BrowserContext；并发不超过 2，任务成功、失败、超时、取消、workflow 重启和 backend 退出均关闭 page/context，最终无孤儿 Chromium/Playwright driver | 必须 |
| AC-PW-05 | 崩溃恢复 | 注入 Chromium 或 driver 崩溃后，当前抓取返回结构化可重试错误；服务最多自动重启一次且不影响已完成证据，后续任务可继续；不得造成 backend 崩溃或无限重启 | 必须 |
| AC-PW-06 | 有限页面交互 | Playwright 只执行白名单动作：导航、等待正文、受限滚动、点击明确的展开正文/下一页控件；动作数、导航域和总时间有硬上限，不执行登录、上传、提交表单或其他写操作 | 必须 |
| AC-EVID-01 | 无效页面拒绝 | CAPTCHA、安全验证、登录墙、搜索导流、导航页、空白/空壳、乱码、正文与标题明显错配的页面不得进入 ranked evidence、citation_sources 或 coverage；固定百度安全验证 fixture 必须被拒绝 | 必须 |
| AC-EVID-02 | 证据与维度绑定 | 每个 EvidencePassage 保留 source family、来源类型、第一方标记、对应维度、相关性、抓取器、正文 hash 和质量标记；最终结论可反查到 passage 与原始 URL | 必须 |
| AC-EVID-03 | 证据准入门 | 综合前检查每个核心维度的有效证据数、第一方证据、重复率和页面质量；严重不足时不调用完整综合，直接进入 `insufficient_evidence`；部分覆盖进入 `partial` 综合 | 必须 |
| AC-REPORT-01 | 通用质量契约 | `technology_intelligence`、`policy_education`、`generic_research` 共用执行摘要、逐维度回答、事实/推断、限制和就近引用契约；非技术问题不得完整委托旧 V3 原子事实报告 | 必须 |
| AC-REPORT-02 | 结论先行 | `completed` 和 `partial` 报告首屏包含 3～7 条直接回答用户的关键判断；不得以公报发布日期、国家周年、术语定义、文件标题或政策口号充当核心结论 | 必须 |
| AC-REPORT-03 | 政策教育 profile | 教育题正文至少按教育现状、主要问题/趋势、国家已发布计划与时间节点、影响与不确定性组织；明确区分官方已发布任务、基于多源证据的分析和无法确认的未来推断 | 必须 |
| AC-REPORT-04 | 就近引用与支持 | 每条关键事实和外部可验证判断具有就近引用；引用 passage 实际支持该句且属于对应维度；引用支持率仍为 100%，但不能单独替代相关性、覆盖度和来源质量门 | 必须 |
| AC-REPORT-05 | 质量评分与硬失败 | 报告按相关性 25、覆盖度 25、来源质量 20、综合推理 15、时效/不确定性 10、可读性 5 形成可审计评分；`completed` 要求总分 ≥80 且无核心维度缺失、无 unsupported key claim、无无效页面、无官方原文被次级来源替代等硬失败 | 必须 |
| AC-REPORT-06 | 用户正文与诊断分离 | 用户报告不包含 raw Coverage JSON、provider attempts、内部 query、原始错误列表或 prompt；精简方法、证据缺口和用户可理解限制保留，完整诊断只进入本地 trace/evidence | 必须 |
| AC-DELIVERY-01 | 三态终态 | 工作流稳定区分 `completed/partial/insufficient_evidence`：完整通过所有门才是 completed；有可用结论但未过完整门为 partial；不足以形成可靠结论为 insufficient，不创建伪完整报告 | 必须 |
| AC-DELIVERY-02 | 软预算、自动续租与安全上限 | workflow 使用单调时钟：300 秒触发质量/进展检查而非强杀；有可量化进展则按 120 秒自动续租，默认自动上限 900 秒。续租只允许启动仍有价值的缺口工作；到上限后不再启动新 upstream，给在途原子操作有界收敛时间并写安全 checkpoint，再进入当前证据交付 | 必须 |
| AC-DELIVERY-03 | 生成式 LLM Token 预算 | 不以固定调用次数限制复杂研究；存在版本化 `LLMBudgetLedger`，按 profile、核心维度、证据规模和模型 context/cost 分配 input/output token 预算。每次调用记录 role、模型、input/output tokens、耗时、质量或覆盖增益，不含 prompt；无 role、预算不足或连续无增益的调用被拒绝 | 必须 |
| AC-DELIVERY-04 | 立即生成现有结果 | running/rescue 阶段 UI 提供“立即用现有证据生成”；真点击后 durable decision 幂等消费，停止新补证、保留已完成证据并进入三态交付；重复点击、重连或恢复不生成两个报告 | 必须 |
| AC-DELIVERY-05 | 质量驱动的审计与定向修复 | 首稿不通过但证据足够时，质量审计输出结构化缺陷并只重写受影响章节；修复不得引入未引用知识。修复循环在质量通过、预算耗尽或连续两次未提升总分/硬失败集合时结束，不能用固定一次修复限制可恢复质量，也不能无增益重写整份报告 | 必须 |
| AC-DELIVERY-06 | 可继续研究 | partial/insufficient 结果提供“继续补充调研”；新 operation 复用原 run 的维度状态、有效证据、source family、缓存和安全 checkpoint，仅研究未覆盖维度，原 run 和原报告保持不可变 | 必须 |
| AC-UI-01 | 覆盖与质量进度 | 总体进度持续显示已覆盖核心维度/总维度、有效来源、第一方来源、当前缺口工作、已用时、软预算检查点、自动续租原因和预计交付状态；不能只显示“做到了哪一步”，也不能把 300 秒后的健康续租显示成卡死 | 必须 |
| AC-UI-02 | 阶段结果可理解 | 每个完成阶段的默认折叠气泡说明做了什么、得到什么、舍弃什么、仍缺什么和下一步；正常完成默认隐藏，waiting/用户操作/最终三态保持主流可见 | 必须 |
| AC-UI-03 | 安全诊断投影 | 用户层只显示 provider 命中/空/超时/cooldown 的聚合计数和可理解原因；不显示 raw query、URL、正文、Cookie、token、异常栈或模型推理 | 必须 |
| AC-UI-04 | 恢复与幂等 | 应用重启、WebSocket 重连和历史加载后，覆盖矩阵、补证轮、立即生成 decision、三态终态和阶段气泡一致恢复；重复 event 不刷屏、不回退状态 | 必须 |
| AC-COMPAT-01 | 历史与默认开启 | 新 run 默认进入升级后的 V4+ 生产路径且完成能力默认 ON；v1/v2/v3/v4 历史 checkpoint、Session、Artifact 和进度仍可读/恢复，旧 run 不被新质量状态误执行 | 必须 |

## 非功能与边界

- **时间**：300 秒是首次软检查点；默认自动研究安全上限 900 秒，按 120 秒租约续期。只有覆盖、第一方来源或报告质量有可量化增益才可续租；到上限后允许有界完成当前原子操作并 checkpoint，不在提交中间强杀。点击“立即生成”后目标 30 秒内进入收敛/终态，否则显示具体收敛阶段和可取消状态。
- **Token**：只统计远程生成式 LLM调用；本地 embedding/确定性评分另记计算成本。预算必须基于真实 benchmark、模型 context 与 profile 配置，不能用固定 3 次调用替代；也不得以静默越过 ledger 换取更高完整率。
- **并发**：Search Gateway 沿用 provider 有界并发；Playwright page/context 并发 ≤2；不同 run 共享浏览器进程但不共享 BrowserContext、Cookie、localStorage 或页面状态。
- **资源**：记录安装包和冻结 backend 的体积增量、冷启动、首个 Playwright 页面启动时间、峰值 RSS 和退出清理时间；不得因浏览器空闲常驻导致 DeskPet 正常聊天明显退化。
- **可靠性**：外部网络、provider、Playwright 或单个页面失败时保留已完成证据；任何 fallback 都受 run deadline/cancel 控制。
- **安全/隐私**：使用临时或 DeskPet 专用 profile，不读取用户浏览器 profile；不保存 Cookie/凭据；只执行只读页面动作；诊断按既有脱敏规则落本机。
- **兼容**：普通聊天、快速搜索工具名、现有 Search Gateway provider、Artifact 操作和旧 workflow 历史保持兼容。
- **平台**：本轮发布与验收范围仅 Windows 11 x64；Windows 10、macOS、Linux 明确不在本轮范围，后续需独立补做兼容性认证。
- **许可证**：记录 Playwright、Chromium 和随包第三方许可证；安装包随附所需 notices。
- **测试阶段策略**：所有完成能力默认开启，只保留紧急 kill-switch 和旧 run 兼容读取，不做 shadow、灰度或默认 OFF。

## 固定验收场景

| ID | 场景 | 必须结果 |
|---|---|---|
| SC-EDU-01 | “帮我调研一下，现在中国小学现在的教育现状和国家下一步计划” | 不得发布原失败样本式事实清单；若完成，首屏 3～7 条结论并覆盖全部核心维度，政策引用优先国务院/教育部/财政部原文；若双减等维度补证后仍缺失，必须 partial 且明确缺口 |
| SC-AI-01 | “可以帮我调研一下，现在 AI 相关的最新最有价值的技术相关的信息吗？” | technology profile 输出价值排序、近期变化、成熟度/采用判断与证据，不混成原子事实平铺 |
| SC-PW-01 | 静态抓取只返回 JS 空壳、Playwright 可渲染正文的固定动态页 | 只对该页调用 bundled Playwright，获得有效正文并进入证据；断网机器无需下载浏览器 |
| SC-BLOCK-01 | 页面返回 CAPTCHA/安全验证 | Playwright可打开页面但质量门拒绝证据，报告和引用不出现验证页文本 |
| SC-RESCUE-01 | 一个核心维度的确定性查询无结果，后续查询策略获得官方来源 | 只对缺失维度执行 rescue；获得有效官方来源并覆盖后立即停止该缺口，不重复健康维度；trace 显示查询策略调用的必要性和覆盖增益 |
| SC-LEASE-01 | 研究在 300 秒时仍持续获得新的第一方证据和核心维度覆盖 | 300 秒不得强制取消或降级；系统记录进展并自动续租，最终在自动安全上限内形成质量合格报告 |
| SC-PLATEAU-01 | provider 可响应但连续两个补证周期没有覆盖、第一方来源或质量增益 | 在 900 秒前提前停止空转并交付已有 partial/insufficient；无多余 LLM、搜索或浏览器循环 |
| SC-CAP-01 | 复杂研究持续有进展但到达 900 秒自动安全上限 | 不再启动新 upstream，当前原子操作有界收敛并 checkpoint，交付当前成果；点击继续调研后复用原证据获得新租约，不从零开始 |
| SC-NOW-01 | 用户在第二轮 rescue 真点击“立即用现有证据生成” | 停止补证并在目标时间内三态交付；重连/重复点击不重复生成 |
| SC-CONTINUE-01 | 用户从 partial 点击“继续补充调研” | 复用旧证据，只处理缺失维度；旧报告不被覆盖，形成可追踪的新 operation/run |

## 测试与证据要求

- 单元：中文分词/相关性、查询模板、维度覆盖、source family、页面质量、质量评分、三态决策、预算和版本定位。
- 集成：Search Gateway → FetchExtractService → Playwright → EvidencePassage → synthesis → quality gate → delivery 的真实调用栈；不得以单独调用评分函数冒充 E2E。
- 故障注入：provider timeout/cooldown、Playwright启动失败/崩溃/超时、取消、重复 decision、backend 重启和 checkpoint 恢复。
- 冻结构建：PyInstaller smoke 验证 Playwright import、driver、Chromium路径和动态页渲染；Tauri安装包内路径与开发环境不得混淆。
- 真实网络：教育题和 AI 题都必须在真实 provider 上运行并保存脱敏 trace、最终报告和质量审计；网络失败不能用 mock 结果替代宣告通过。
- Windows 真机：遵守仓库 AGENTS 的手工测试纪律，在 Xiaomi 屏幕上执行真实输入、点击立即生成/继续调研、展开进度、重启恢复、查看最终报告；每个 case 留动作声明、截图和 Tauri/backend 日志。
- Windows 11 安装验证：在当前 Windows 11 x64 主机上使用隔离安装目录、隔离用户数据和清空开发 browser cache 的方式完成断网安装、首次启动、Playwright 健康检查、动态页调研、更新/卸载；不得依赖系统 Python、Node 或首次下载 Chromium。该证据不代表 Windows 10 已认证。
- 回归：后端相关测试、前端全量、TypeScript、production build、冻结 backend smoke 和 installer smoke 全绿；无新增 P0/P1。

## 完成的定义（DoD）

- 所有“必须”AC 建立 `AC → plan task → code → testcase → result/evidence` 追溯并通过。
- SC-EDU-01 与 SC-AI-01 的最终 Markdown 通过结构化质量审计和人工专业可读性 review；不能仅以支持率、引用数或 PASS 数量宣告完成。
- SC-PW/SC-BLOCK/SC-RESCUE/SC-LEASE/SC-PLATEAU/SC-CAP/SC-NOW/SC-CONTINUE 全部通过真实栈验证。
- Windows 11 隔离安装与真实桌面 E2E 通过；Chromium 随包、断网可用、无孤儿进程有直接证据。Windows 10 认证不属于本轮 DoD。
- 完成能力出厂默认 ON；用户无需配置 Playwright、Chromium、SearXNG、Docker 或付费 API。
- `ARCHITECTURE/DeepResearch.md`、`ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md`、`ARCHITECTURE/PROJECT_STATUS.md`、本 plan 目录、`testcase/` 索引和测试结果同步；`STATUS/` 不写新正文。
- 最终完成度审计与测试审计均为 `VERDICT: PASS`；任何已知 P0/P1、未覆盖必须 AC 或缺失架构回写都视为未完成。
