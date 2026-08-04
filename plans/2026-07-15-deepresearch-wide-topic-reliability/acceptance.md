# 验收标准：DeepResearch 宽主题技术情报可靠性与失败交付

> 唯一验收事实源。固定回归来源：Session `16bbb4ce-c282-4b25-b630-7400b6be25c1`，run `ed254c0673d04771bbb2901fed462741`。
> 用户已于 2026-07-15 接受上一轮诊断提出的四项修复方向。

## 范围

- 包含：Search Gateway provider 级背压、permit 入队后复验、empty-aware 零候选救援、DeepResearch 宽主题技术情报规划/排序、真实 attempt 诊断、`no_results` 提前终止与失败 UI/重试、自动化与 Xiaomi 屏幕真机回归。
- 保持：Search Gateway 进程内默认 ON；新启动 run 默认进入 `deep_research/v4`；v1/v2/v3 immutable 恢复兼容；引用 support/publish gate 不降级。
- 明确不包含：部署外部 SearXNG 服务、购买收费搜索 API、降低引用/事实/独立域/正文门槛、把 provider health 改成 request-local、伪造搜索结果。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|---|---|---|---|
| AC-SRCH-01 | provider 级背压 | 4～6 个 branch 并发搜索时，每个 provider 的真实 in-flight 调用数不超过其配置上限；排队任务在获得 slot 后重新获取/复验 circuit permit，已开路 provider 不被旧 closed permit 继续冲击 | 必须 |
| AC-SRCH-02 | 排队预算与取消 | 等待 provider slot 受 request deadline/cancel 控制；超时或取消释放 slot/lease，不累计 provider failure，不遗留 probe busy | 必须 |
| AC-SRCH-03 | empty-aware 救援 | 当 closed provider 均返回 validated empty、其余 provider 为 open/half-open，且 deadline 内存在 eligible probe 时，当前请求最多等待并执行一个 single-flight 救援 probe；empty 仍不记为 failure，并发 loser 不形成 probe storm | 必须 |
| AC-SRCH-04 | 宽主题突发回归 | 用 5 个并行 branch、13 条查询和一个可返回结果的慢 provider 模拟原 Session：至少一个 branch 获得候选，最大 in-flight 受限，provider 调用数有上界，不出现“可用 provider 被同 run 自己打进 cooldown 后全 run 0 候选” | 必须 |
| AC-SRCH-05 | 默认配置 | provider 并发上限、queue wait/救援行为在 `config.py` 与根 `config.toml` 默认开启；测试阶段不留默认 OFF | 必须 |
| AC-INTEL-01 | 技术情报意图 | “最新/最有价值/技术趋势 + AI”类宽主题确定性进入 `technology_intelligence` profile；不把监管、商业案例、泛行业应用混入技术 Top 列表，除非用户明确要求 | 必须 |
| AC-INTEL-02 | 可搜索规划 | profile 默认采用明确时间窗并拆成 3～5 个可独立核验的技术主题；每个主题生成短 discovery query、官方/论文 query，避免把整句长问题原样重复轰击 provider | 必须 |
| AC-INTEL-03 | 一手源补强 | Agent、基础模型、多模态、推理/训练效率、开源系统等主题有可审计 source-pack/direct seed；普通主题和旧 source-pack 行为保持兼容 | 必须 |
| AC-INTEL-04 | 价值优先报告 | 成功报告开头说明时间窗和排序口径，并输出按近期性、技术影响、成熟度/可采用性、证据质量排序的 Top 技术；每项包含“发生了什么、为什么重要、成熟度、至少一个实际支持它的引用” | 必须 |
| AC-INTEL-05 | 不牺牲真实性 | “为什么重要”和成熟度若不能由 evidence passage 支持，必须标为推断/未知或不发布；原有 support 分母、canonical URL 去重和 fail-closed publish gate不降低 | 必须 |
| AC-REPORT-01 | 专业可读结构 | 中文宽主题请求输出专业中文报告；首屏有执行摘要和 3～5 条关键判断，正文按技术主题而不是原子 claim 平铺；每项包含核心变化、价值判断、成熟度/采用建议、风险或不确定性与引用 | 必须 |
| AC-REPORT-02 | 实体级去重与质量优先 | 同一技术/产品/论文的多个受支持 claim 合并为一个 finding；Top 列表发布 3～8 个互不重复且逐段过门的技术主题，少于 5 项时必须明确说明没有用低质量候选补齐数量；不允许用同源近义句凑满条目 | 必须 |
| AC-REPORT-03 | 可解释排序 | 排名必须能区分近期性、技术影响、成熟度与证据质量；不允许全部同分、全部近期性为 0 或全部成熟度未知。缺证据的维度必须明确降权，不得用统一占位句冒充分析 | 必须 |
| AC-REPORT-04 | 用户正文与诊断分离 | 用户报告只保留精简方法、局限和引用；`provider_attempts`、`published_claims`、内部 coverage JSON、raw query/URL/正文片段不得整段进入报告正文，可审计明细只保存在本地运行证据 | 必须 |
| AC-FAIL-01 | 提前终止 | search + direct + 一轮救援后仍为 0 candidates 时，图不再执行 fetch/score/synth/cite/persist 的伪完成链；后续阶段明确标为 skipped/not-run，terminal 为 `deep_research_no_results` | 必须 |
| AC-FAIL-02 | 不生成假 Artifact | `no_results/insufficient_evidence` 不创建 `research_report` ArtifactCard，不发送像完整报告的 Markdown；Session 只显示结构化失败说明、实际 provider 汇总和可操作的重试入口 | 必须 |
| AC-FAIL-03 | 一键重试 | 失败卡提供“重新调研”按钮；点击后复用原 run 的安全服务端请求引用创建新 run，不把原始 query 放进 progress/diagnostics，不重复旧 run delivery | 必须 |
| AC-UI-01 | 计数语义 | 搜索阶段区分并显示“真实请求 / 命中 / 空结果 / 超时 / cooldown 跳过 / busy 跳过 / probe”；不得再把 19、25、76 三种口径混成“尝试来源” | 必须 |
| AC-UI-02 | 失败可见性 | Xiaomi 屏幕上，0 候选时搜索阶段显示失败原因和“后续步骤未执行”，总体卡终态为失败而非完成；默认折叠仍能看懂发生了什么 | 必须 |
| AC-OBS-01 | 安全可观测 | progress、coverage、benchmark 和脱敏证据只含 run/request/provider/status/count/elapsed/reason code；不含 raw query、URL、正文、secret/token | 必须 |
| AC-BC-01 | 兼容恢复 | v1/v2/v3 fixture/hash/recovery 全绿；升级前在途 run 可恢复；非 technology-intelligence 主题规划与报告行为不变 | 必须 |

## 非功能与边界

- 性能：provider queue 不阻塞事件循环；原 Session 规模在正常网络下 360 秒内终态；单次 provider upstream timeout 仍受原配置约束。
- 并发：同 provider 上限跨 quick search 与 DeepResearch branch 共享；不同 provider 可并行。
- 安全：原始运行日志只留本机；提交证据必须脱敏且非 gitignored。
- 失败诚实性：外网全断允许 FAIL/no-results，但不得生成假报告；真实网络 E2E 失败必须保留证据，不用脚本注入替代。
- UI：原生 button、键盘 Enter/Space、`aria-expanded`/status live region；重试按钮必须防重复点击。

## 完成的定义（DoD）

- 全部“必须”条款有自动化或 Windows 真机证据并通过。
- 最终 Markdown 需人工阅读验收：没有重复技术、英文证据句直出、统一占位分析、垂直应用串题或大段内部 diagnostics；首屏可在一分钟内看懂关键结论与采用建议。
- 固定原始题目在同一 Tauri/backend 进程至少连续运行 2 次：不得复现 run 内 cooldown 连坐；成功报告满足 AC-INTEL-04/05。若外部网络全断，诚实失败 UI 满足 AC-FAIL/UI 条款，但不能据此宣告成功报告质量通过。
- 后端全量失败集合无新增；前端全量、tsc、production build 通过；scoped ESLint 相对固定 `0117ad...` 基线无新增错误，既有 36 errors 单独记录而不冒充全绿。
- testcase/index 与 `ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md`、`ARCHITECTURE/PROJECT_STATUS.md` 回写；不写 `STATUS/status.md`。
- 最终完成度审计 `VERDICT: PASS`。
