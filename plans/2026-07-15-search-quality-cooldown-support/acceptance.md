# 验收标准：Search Gateway cooldown 隔离与 DeepResearch 引用支持质量

> 状态：用户已确认；本文件是本轮唯一验收事实源。
> 日期：2026-07-15

## 范围

- 包含：Search Gateway provider 健康状态、cooldown/half-open、并发 single-flight、request-local diagnostics。
- 包含：DeepResearch query decomposition、证据 passage 选择、claim 粒度、引用核验、一次纯确定性有界 repair/pruning 与最终报告发布门。
- 包含：自动化、真实网络固定多类别 benchmark、Windows DeskPet 真机消息流与 Artifact 验收。
- 明确不包含：新增付费搜索 API、打包完整 SearXNG 服务、绕过验证码、降低 13 阶段 durable/restart/Artifact 契约。
- 明确不包含：辅助 FactExtractor 旧 relay key 401；该问题作为独立 provider-refresh 工作项处理。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|---|---|---|---|
| AC-CD-01 | cooldown 按 provider 隔离 | 一个 provider 进入 cooldown 后，同批和后续请求仍会尝试其他 healthy provider；不得因为任意单 provider cooldown 直接返回 0 candidates | 必须 |
| AC-CD-02 | 全 cooled half-open | 所有实际 available provider 均 open 时，按 `(next_forced_at, 配置序号)` 选择；首个 `next_forced_at` 能在本请求 deadline 内到达的 eligible owner 必须有界等待并 probe。并发 loser、该 generation 已消费或安全时间晚于 deadline 时可返回明确 `half_open_busy/cooldown/budget`，不得制造一请求一探测的上游风暴 | 必须 |
| AC-CD-03 | 防惊群 | 同一 provider 的并发 half-open 只能有一个 owner；其他请求继续走其他 provider 或得到明确 `half_open_busy` 诊断，不重复轰击上游 | 必须 |
| AC-CD-04 | failure-class 语义 | timeout、blocked/captcha、rate-limit 与普通 empty 使用独立退避策略；成功或有效 partial result 能按契约恢复健康，empty 不得被错误放大成全局熔断 | 必须 |
| AC-CD-05 | 请求隔离 | diagnostics、deadline、cancel、budget、engines tried/hit 均为 request-local；并发 DeepResearch run 不串线，已有 TTL/LRU cache 语义保持 | 必须 |
| AC-CD-06 | 连续异主题压力 | 固定执行“政策调研 → WebGPU 调研”，第二个请求的 search 阶段必须真实尝试至少一个可用 provider，且不得以 `providers=0/candidates=0` 仅因前一请求 cooldown 结束 | 必须 |
| AC-QA-01 | 证据优先写作 | synth 只能基于选中的 evidence passages 组织可核验事实；每个发布 claim 必须保留可追溯 evidence/citation 关联 | 必须 |
| AC-QA-02 | claim 原子化 | 复合论断应拆成可独立核验的原子 claim；数值、版本、日期、支持范围等高风险事实不得与无关判断捆绑后共享一条弱引用 | 必须 |
| AC-QA-03 | 发布门 | 未达到支持阈值的事实不得作为确定性结论留在正文；每个 claim 最多经过一次可重放的纯确定性收缩 repair（只保留原 claim 中被同一 cited passage 支持的 clause，不改写、不新增事实/引用），repair 后仍不支持则 pruning 或移到“未证实/局限”区 | 必须 |
| AC-QA-04 | 防指标投机 | successful 报告必须同时满足：`support_rate >= 0.60`、`supported_claim_count >= 8`、`citations >= 8`、`independent_domains >= 4`、Markdown 正文 >= 1500 bytes；不能靠删成空报告提高 support rate | 必须 |
| AC-QA-05 | 固定集质量 | 官方技术文档、中国现行政策、Web 标准动态信息三类真实固定集应 3/3 产出 successful 报告；全体平均 support rate >= 0.70，nearest-rank P95 <= 360s | 必须 |
| AC-QA-06 | 诚实失败 | 若真实外部网络/relay 全不可用，必须交付 no-results + degraded/error summary，不得用模型先验伪造；该轮标 BLOCKED/外部失败，不能冒充 AC-QA-05 PASS | 必须 |
| AC-BC-01 | 兼容性 | `web_search` 工具名、主要参数/返回 shape、DeepResearch 13 阶段、v1/v2 历史恢复、Markdown Artifact 与 final-assistant 投递契约不变；v2 definition/implementation hash 固定，升级前在途 v2 fixture 可恢复 | 必须 |
| AC-BC-02 | 默认启用 | 完成后的 cooldown 隔离与质量发布门出厂默认 ON；测试阶段不留 shadow/default OFF | 必须 |
| AC-OBS-01 | 可观测 | progress/benchmark 按安全 request_id/run_id 关联记录 provider attempt/cooldown/half-open permit/probe outcome 汇总，以及 candidates、passages、published/discarded/repaired claims、support、domains、citations 与 elapsed；不写 query、URL、secret/正文 blob | 必须 |
| AC-UI-01 | 总体卡直观可读 | Session 中每个 DeepResearch run 的总体卡始终可见，展示当前阶段、完成数/总数、elapsed、总体状态，以及已完成阶段的紧凑时间线；不能只显示“做到第几步” | 必须 |
| AC-UI-02 | 每步做了什么 | 紧凑时间线中每个已开始/已完成阶段至少显示人类可读的“动作 + 一句话结果”，例如搜索了多少候选、抓取成功/丢弃多少、保留多少证据、引用支持率；不得直接展示原始 JSON key/value 堆 | 必须 |
| AC-UI-03 | 结果与降级显著 | success、running、degraded、failed、waiting 使用可区分的图标/颜色/文案；阶段存在 cooldown、timeout、低质量证据、deterministic repair 或 pruning 时，对应行直接可见简短原因，不要求用户翻最终报告才知道 | 必须 |
| AC-UI-04 | 渐进披露 | 每完成一步仍新增 durable child bubble，但默认隐藏；用户点击总体卡中的阶段行或“查看阶段”后可展开该步详情，包含动作说明、关键指标、结果/错误和下一步，且支持键盘与 ARIA | 必须 |
| AC-UI-05 | 恢复与隔离 | 历史加载、乱序/重复事件、运行中重启后，紧凑时间线与展开详情不重不丢；同 Session 多 run 独立显示，失败/降级不覆盖另一 run | 必须 |
| AC-UI-06 | 真机表现 | Windows DeskPet 真输入连续两类调研；观察 compact timeline 实时追加、展开/收起阶段详情、终态报告与 Artifact；截图和 backend 日志共同判定 | 必须 |

## 非功能与边界

- 并发：保留现有 quick/research parallel cap；half-open 必须 single-flight，不能制造上游请求风暴。
- 幂等/恢复：checkpoint 重放、dispatcher 重试、运行中重启不得重复 stage、重复 claim repair 或重复 Artifact；v2 manifest/hash 与在途恢复 fixture 保持冻结。
- 性能：固定真实三类集 P95 不超过 360 秒；quick search 仍受既有 5 秒 deadline 约束并允许返回 partial。
- 安全：不绕过 CAPTCHA/robots/rate-limit，不记录 token、cookie、原始完整网页或 prompt secret。
- 兼容：旧 v1/v2 run 只读/恢复；旧 Session 历史与 Artifact 继续可见。
- 诚实性：外部依赖失败与产品缺陷分开记录，失败样本保留在 benchmark 分母。

## 完成的定义（DoD）

- 全部必须条款都有自动化或真实运行证据。
- 新增/相邻测试 0 failure；全量失败集合不得新增任务范围失败。
- 固定三类别真实 benchmark 达到 AC-QA-05；Windows Computer Use 完成 AC-UI-01～AC-UI-06。
- testcase、results、`ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md` 与 `ARCHITECTURE/PROJECT_STATUS.md` 同步。
- v2 manifest/hash 固定 fixture 与 v1/v2 recovery 回归通过；新 run 默认注册并启动 v3。
- 完成能力默认 ON，最终挑战审计 `VERDICT: PASS`。
