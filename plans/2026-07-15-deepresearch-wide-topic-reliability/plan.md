# DeskPet DeepResearch 宽主题技术情报可靠性修复计划

<!-- plan-status: finalized -->
<!-- finalized-by: plan-test; challenge-rounds: 4; verdict: PASS -->

> 日期：2026-07-15
> 验收事实源：[acceptance.md](acceptance.md)
> 架构事实源：[../../ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md](../../ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md) §15
> 固定失败样本：Session `16bbb4ce-c282-4b25-b630-7400b6be25c1` / run `ed254c0673d04771bbb2901fed462741`

## 1. 结果与主要矛盾

交付后，原始问题“现在 AI 相关的最新最有价值的技术信息”会确定性进入 `technology_intelligence` profile，由 `deep_research/v4` 按时间窗、技术影响、成熟度和证据质量生成价值优先报告。Search Gateway 会在进程级按 provider 做背压，排队后重新获取 circuit permit，并在“健康 provider 本请求只返回 empty、其他 provider 已 open”时执行一次有界 single-flight 救援。若仍无结果，工作流会诚实失败、显示未执行步骤和真实搜索计数，不再生成假 Artifact，并允许从失败卡安全地一键创建新 run。

决定成败的核心问题不是“再调一次 prompt”，而是同一个进程中的职责错位：workflow 只限制 branch task 并发，Search Gateway 却没有 provider 级 bulkhead；同时 Gateway 把“closed”误等同于“当前请求仍有产出路径”。宽 fan-out 因而可以自行制造 provider timeout/cooldown 连坐，而 closed-empty Baidu 又阻断救援。内容 profile、失败 delivery 和 UI 是必须一并闭环的第二层问题，但不能替代 provider 级结构修复。

## 2. 边界

- 不部署或打包外部 SearXNG，不购买收费搜索 API，不破解 CAPTCHA。
- 不降低 support rate、事实数、引用数、独立域和正文门槛；不让 LLM 无证据评价“价值”。
- 不把 provider health 改成 request-local；shared circuit 继续保护上游。
- 不原地修改 v1/v2/v3 definition、node handlers、manifest 或 checkpoint 语义。需要改变图路由/state/delivery 的能力进入 `deep_research/v4`；旧版本只做恢复。
- 不写 `STATUS/status.md`。完成状态、验收证据只回写 `ARCHITECTURE/`、本 plan 和 `testcase/`。
- 所有完成能力默认 ON；只保留现有总 kill-switch，不做 shadow/灰度。

## 3. 外部实践与 DeskPet 适配

- Python `asyncio.Semaphore` 是事件循环友好的共享计数器，但不承诺业务级公平；本项目 provider 数固定、只要求 hard cap 与可取消等待，因此使用“每 provider 一个 semaphore + request deadline 包裹 acquire”即可，不额外引入队列依赖。[Python synchronization primitives](https://docs.python.org/3/library/asyncio-sync.html#asyncio.Semaphore)
- Resilience4j 的 circuit breaker 明确指出熔断器本身不限制并发调用；限制并发应由 Bulkhead 承担。DeskPet 因此保留现有 `ProviderHealth` 管 closed/open/half-open，新增独立 `ProviderLimiter`，不把两者揉成一个锁。[Resilience4j CircuitBreaker](https://resilience4j.readme.io/docs/circuitbreaker)
- OpenTelemetry 的 HTTP metrics 将请求结果与错误类型作为可聚合维度；DeskPet 不引入新 telemetry SDK，只借鉴“互斥结果桶 + 正交属性”的口径，使 `actual_requests`、skip、probe 可自动核对，且不记录 query/URL/body。[OpenTelemetry HTTP metrics](https://opentelemetry.io/docs/specs/semconv/http/http-metrics/)
- WAI disclosure 使用原生 button 与 `aria-expanded`/`aria-controls`；DeskPet 保留现有折叠阶段交互，并把 retry 也做成原生 button、pending 时 disabled，避免重复键盘/点击处理。[WAI Disclosure pattern](https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/)

## 4. 冻结契约

### 4.1 Provider limiter 与调用顺序

新增 `ProviderLimiter`，由唯一进程级 `SearchGateway` 构造并持有：

```text
await slot.acquire(deadline)
  -> ProviderHealth.acquire_permit(provider, allow_probe)
  -> permit=closed/half_open 才进入 provider.search
  -> record outcome / complete probe
  -> slot.release() in finally
```

slot 等待时间为 `min(request budget.remaining_s, provider_queue_max_wait_s)`；没有获得 slot 时返回 `queue_timeout` decision，不累计 provider failure。外部 `CancelledError` 必须释放等待/已持有 slot；若已持有 half-open lease，调用 `complete_probe(..., "cancel")` 后再向上抛出。permit 绝不在排队前预取。

默认配置：`provider_max_concurrency=1`、`provider_queue_max_wait_s=8.0`、`empty_rescue_enabled=true`、`empty_rescue_max_per_request=1`。上限跨 quick search、不同 DeepResearch run 和所有 branch 共用；不同 provider 仍可并行。

### 4.2 Attempt 与计数

`AttemptStatus` 新增 `QUEUE_TIMEOUT`。外部任务取消不伪造成 SearchResponse；只发隐私安全 cancellation metric 并 re-raise。完成的 SearchResponse 使用以下等式：

```text
actual_requests = hits + empty + timeouts + blocked + captcha + rate_limits + errors
routing_decisions = actual_requests + cooldown_skips + busy_skips + queue_timeouts
                    + unavailable_skips + budget_skips + cache_hits
0 <= probes <= actual_requests
```

`errors` 以 `public_error_code` 细分 `http_error/invalid_response/parse_error/other`，细分不重复计数。`AttemptStatus/PublicErrorCode` 同时新增 `queue_timeout`；`ProviderAttempt.upstream_called: bool` 与 `is_rescue: bool` 明确区分真实 transport/Gateway decision/救援；`permit=half_open` 且 `upstream_called=true` 才计 probe。外部 task cancellation 必须 re-raise，所以没有 SearchResponse，也不进入上述等式；limiter 只发独立 `search_gateway_cancelled_wait` metric，取消测试直接断言 slot/lease/metric。`SearchResponse` 新增 `rescue_status`、`rescue_error_code`、`rescue_upstream_called`，由唯一 rescue attempt 冻结；`SearchResponse.coverage()` 是唯一聚合函数。main、DeepResearch、progress 和 UI 不再各自维护状态集合。旧 `provider_attempt_count` 保留，但严格等于 `actual_requests`；`routing_decisions` 在 UI 标为“Gateway 决策”，不标为来源数。

### 4.3 Empty-aware rescue

每个请求维护 provider 最新 outcome。所有 wave 执行后仍为 0 result，且：

1. 本请求所有 closed provider 均已 `validated_empty`；
2. 其余 available provider 为 open/half-open-busy/failure；
3. request 尚未 rescue，deadline 内至少一个 provider 的 `eligible_at` 可达；

则按 `(eligible_at, configured_order)` 选择一个 provider，等待后在 provider slot 内重新 acquire permit。每个 SearchRequest 最多一次 rescue；generation/token 继续由 `ProviderHealth` 保证 single-flight。loser 只得到 busy/cooldown，不循环救援。empty 仍调用 `record_success` 或完成 empty probe，不累计 failure。

每个返回 response 冻结 `rescue_status=not_needed|hit|empty|failed|ineligible|deadline_exhausted|lost_race` 与可选 `rescue_error_code`（只允许既有 PublicErrorCode）；status 进入 `SearchResponse.coverage()`，再由 `main.py::_deep_v2_context_factory` 写入 `ResearchSearchResults.observation`，`_safe_search_observation()` 只保留该枚举/错误码。branch search event 与 join coverage 分别聚合 `rescue_considered_count`（非 `not_needed`）和 `rescue_executed_count`：后者以 rescue attempt 的 `upstream_called=true` 计数，包含 hit/empty/timeout/blocked/captcha/rate-limit/error，绝不以结果成功与否代替“是否执行”。v4 早停不依赖一个额外悬空的 `rescue_completed` flag：到达 direct join 已证明所有 SearchGateway response 返回，因而所有 rescue 都已完成或得到不可执行终态。

### 4.4 Technology intelligence v4 state

新增 v4-only contract：

- `IntentProfile={kind: generic|technology_intelligence, as_of_date, window_start, window_end, requested_dimensions}`；识别完全由本地关键词/日期规则完成。
- `TechnologyTopic={topic_id, label, discovery_query, official_query, scholarly_query, source_seeds}`；最多 5 个，单项只描述一个可核验技术主题。
- `TechnologyFinding={finding_id, claim_ids, recency_score, impact_score, maturity: adopted|emerging|experimental|unknown, evidence_quality, winning_citation_ids}`；所有 score 只能从 evidence metadata/passage 与确定性 rubric 得到。
- generic profile 继续调用 v3-compatible plan/quality/report adapter，golden fixture 输出保持不变；technology profile 才走新 planner、source seeds、ranking 和 renderer。

意图判定冻结为三组词的 AND：

- AI：`ai/人工智能/大模型/llm/基础模型/agent/智能体/多模态`；
- 新近或价值：`最新/近期/前沿/趋势/突破/价值/值得关注/latest/recent/frontier/trend/breakthrough/valuable`；
- 技术：`技术/模型/推理/训练/框架/系统/开源/基础设施/technology/model/inference/training/framework/system/open source/infrastructure`。

三组各命中至少一个才进入 technology profile。`监管/政策/法律/治理/商业案例/行业应用/regulation/policy/law/governance/business case/industry application` 只把相应维度加入 `requested_dimensions`，不能让非技术题单独进入 profile；命中技术 profile 时也只有用户文本显式含这些词才允许 planner 增加对应 topic。显式时间解析仅支持 `过去/最近 N 天|周|个月|年`、`last/past N days|weeks|months|years`、`YYYY 年以来/since YYYY` 和明确 `YYYY-MM-DD..YYYY-MM-DD`；天/周/月/年分别按固定 `1/7/30/365` 天换算，换算后只接受 `1..3650` 天。end 晚于 as-of 时截到 as-of；start 晚于 end、N<=0、N 超界、未来 since-year 或非法日期均视为解析失败，统一回退 `[as_of_date-180d, as_of_date]`。范围端点按 UTC date 持久化。`as_of_date` 在 normalize 首次执行时取 UTC date 并写 state，恢复不重算；测试通过 clock port 固定日期。

价值排序完全确定：

- `recency`：发布日期距 as-of `<=30/90/180/365/>365` 天分别为 `1.0/0.8/0.6/0.3/0.0`，无可核验日期为 `0.0`；
- `impact`：winning passage 中有可精确匹配的 benchmark/成本/延迟/吞吐/上下文长度/能力增量数值且另有独立来源支持为 `3`；只有一个可精确匹配量化变化为 `2`；只有被 passage 支持的新增能力或互操作性变化为 `1`；否则 `0`；
- `maturity`：`adopted=3` 需要官方 GA/stable 与一个独立采用/实现来源；`emerging=2` 需要官方 beta/preview/release 或两个独立论文/实现来源；`experimental=1` 仅有 preprint/research prototype；其余 `unknown=0`；
- `evidence_quality`：至少一个一手来源且一个独立来源=`3`，只有一手来源=`2`，只有二手来源=`1`，否则 `0`。

总分为 `0.35*recency + 0.30*(impact/3) + 0.20*(maturity/3) + 0.15*(evidence_quality/3)`；稳定排序依次为总分降序、evidence_quality 降序、recency 降序、`finding_id` 升序。所有量化 token、GA/beta/preprint 标记、来源类别和独立域必须来自已通过 support 的 winning passages/citations；模型可提出候选 finding，不得直接给最终分。默认技术桶只从模型/推理、Agent、原生多模态、训练与推理系统、开源基础设施中选择 3～5 项。

marker 同样冻结：量化 token 使用 `\d+(?:\.\d+)?\s*(%|x|ms|s|tokens?|k|m|b|gb|tb)` 且同句命中 `benchmark/accuracy/score/cost/latency/throughput/context/参数/准确率/得分/成本/延迟/吞吐/上下文`；adopted=`ga/generally available/stable/production/正式发布/稳定版/生产可用`，emerging=`beta/preview/released/release/公测/预览/发布`，experimental=`preprint/prototype/research preview/实验/原型/预印本`。一手域静态 allowlist 为 `arxiv.org/openreview.net/github.com/openai.com/anthropic.com/deepmind.google/ai.google.dev/microsoft.com/nvidia.com/meta.com/huggingface.co`；未命中 allowlist 的 SERP 结果保守算二手，不能由模型升级为一手。独立来源按 canonical host 去重。

`SourceSeed={seed_id, topic_kind, channel:gateway|direct, query_kind:official|scholarly|repository, query_template, allowed_domains, direct_source:null|arxiv|agent_reach}`。静态表至少包含：scholarly=`allowed_domains=[arxiv.org]`、`channel=direct`、`direct_source=arxiv`，调用真实接口 `ResearchSearchPort.direct(rendered_query, "arxiv")`；repository=`allowed_domains=[github.com]`、`channel=gateway`、query 追加 `site:github.com`，若另建 direct seed 则必须显式 `direct_source=agent_reach`；official 不硬编码单一厂商，使用实体名 + `official release/docs/blog` Gateway query，结果仍须通过 authority/domain 分类。`channel=direct` 而 direct_source 为空是 contract error。每 topic 最多三 seed，按 `seed_id` 排序、去重后由 expand 写 query_specs；direct 按 `direct_source` 明确调用，Gateway 只消费 `channel=gateway`，普通 profile 继续原 source-pack 路径。

### 4.5 v4 失败与 retry

`direct_join` 后新增条件路由：canonical search + direct 候选总数为 0 时进入 `no_results_finalize`，否则进入 fetch。到达该 join 已证明所有 active branch 的 SearchGateway 调用完成；每个 Gateway 调用在返回前都已完成或判定不可执行其一次 rescue，因此 selector 不再等待或重复发起 rescue。SearchResponse coverage 额外记录 `rescue_considered/rescue_executed`，失败摘要聚合为 `rescue_considered_count/rescue_executed_count`。失败节点写：

- `terminal_status=error`
- `terminal_error={code: deep_research_no_results, user_message, recovery_action: retry_from_start}`
- `skipped_stage_ids=[fetch,score,gap,rerank,synth,cite,persist]`
- 真实 coverage 与安全 diagnostic codes
- `delivery_intents=[]`

因此只产生 durable `workflow.final`，不产生 workflow_report、artifact_card 或 final_assistant。`insufficient_evidence` 也不得产生 artifact/final Markdown，只保留结构化 failure final。成功 v4 才生成三类 delivery intent。

`workflow_run_retry_from_start` IPC 请求固定为 `{run_id, action_id:"retry_from_start", retry_key}`，其中 `retry_key` 是小写 UUID v4 文本（36 字符，服务端正则校验）。UI 一次点击生成一次并在 pending 周期内保持。服务端从 `workflow_start_requests + workflow_capabilities.snapshot_json` 读取 `_workflow_start`，只取其中的 `start_payload` 与 `identity`；重新传给 `start_workflow` 的 capability snapshot 必须是持久化 snapshot 去掉保留键 `_workflow_start` 后的原始 capabilities，绝不能把保留键嵌套重传。新 identity 使用 `request_id=retry:<retry_key>`、`logical_slot=retry:<source_run_id>:<retry_key>`，同 key 网络重发返回同一新 run，不重复旧 delivery。只允许 terminal failed 的 `deep_research/v4` 执行该 action。

`WorkflowService.retry_run_from_start` 是唯一编排入口：先读取 source run 得到 delivery session id，再进入 `service.session_lock(session_id)`；锁内重新读取 source snapshot、校验 terminal/name/version/action、读取当前 SessionDB delivery state 并拒绝 deleted/epoch 不匹配，然后调用 Launcher 的已注册 v4 adapter 调度。新 identity 逐字段复制 source 的 `venue/base_session_id/code_session_id/delivery_session_id/base_epoch/code_epoch/turn_id/workflow_name`，只覆盖 `request_id` 与 `logical_slot`；workflow version 固定为已校验的 v4。普通/Code session 删除已使用同一 session lock，因此“校验后、创建前删除”不可穿透；race 测试让 delete 与 retry 竞争同一锁，最终只允许“新 run 已绑定后删除流程取消它”或“删除先完成则 retry 拒绝”，不得产生未绑定 orphan run。

## 5. 工作项

### WI-0 — 绿色基线与固定失败夹具

**新增/修改**

- 新增 `plans/2026-07-15-deepresearch-wide-topic-reliability/baseline.md`
- 新增 `backend/tests/fixtures/deep_research_wide_topic_burst.json`
- 只读验证 v1/v2/v3 manifest/checkpoint fixture

**步骤**

1. 记录当前 commit、dirty scope、Python/Node 版本和聚焦测试结果；旧失败必须与本计划隔离。
2. 将原 run 的安全统计固化为夹具：5 branch、13 query、Baidu empty、一个慢可命中 provider、其他 provider timeout/open；只保存 fingerprint/计数，不保存 raw query/URL/body。
3. 固化 v3 definition/implementation hash 和至少一个 terminal recovery fixture，后续禁止更新 golden 来掩盖兼容漂移。

**Gate G0**

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_search_gateway_provider_statuses.py backend/tests/test_search_gateway_routing.py backend/tests/test_workflow_deep_research_v3.py backend/tests/test_workflow_recovery.py -q
Set-Location tauri-app
$node='C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
& $node node_modules/vitest/vitest.mjs run src/stores/sessionsStore.test.ts src/components/workflow/WorkflowProgressGroup.test.tsx src/components/MessageStreamPanel.workflow.test.tsx
```

### WI-1 — ProviderLimiter、排队复验与统一 coverage

**新增**

- `backend/deskpet/retrieval/limiter.py`
- `backend/tests/test_search_gateway_provider_limiter.py`

**修改**

- `backend/config.py`
- `config.toml`
- `backend/deskpet/retrieval/contracts.py`
- `backend/deskpet/retrieval/search_gateway.py`
- `backend/main.py`
- `backend/tests/test_search_gateway_config.py`
- `backend/tests/test_search_gateway_provider_statuses.py`
- `backend/tests/test_metrics_sink.py`

**步骤**

1. 实现 `ProviderLimiter(slot_for(provider), acquire(provider, deadline))`；只管理并发/等待，不读取或修改 circuit。
2. SearchGateway 初始化时按已注册 provider 建 slot；`_run_provider` 先等待 slot，再 snapshot/acquire permit，再 transport。所有 exit path 以 `finally` 释放 slot。
3. queue timeout 返回 `ProviderAttempt(QUEUE_TIMEOUT, upstream_called=false)`；budget/unavailable/cooldown/busy/cache 同样显式 `upstream_called=false`；所有 provider.search outcome 为 true。
4. 把 `SearchResponse.coverage()` 接到 `_deep_v2_context_factory` 的 observation，移除 main 中 `actual_statuses` 的重复集合和手写求和。
5. 配置 loader 校验 concurrency `1..6`、queue wait `>0`；代码默认与根 TOML 同为 ON。

**测试**：同 provider 10 并发最大 in-flight=1、不同 provider overlap、队列中第一个调用开路后后续不进入 transport、queue timeout 不增加 failure、等待取消/transport 取消/half-open 取消无 slot/lease 泄漏、coverage 等式与隐私字段。

**覆盖 AC**：SRCH-01、SRCH-02、SRCH-05、UI-01、OBS-01。

### WI-2 — Empty-aware rescue 与 5×13 burst 回归

**修改**

- `backend/deskpet/retrieval/cache.py`
- `backend/deskpet/retrieval/search_gateway.py`
- `backend/tests/test_search_gateway_provider_statuses.py`
- `backend/tests/test_search_gateway_routing.py`
- `backend/tests/test_search_gateway_provider_limiter.py`

**新增**

- `backend/tests/test_search_gateway_wide_topic_burst.py`

**步骤**

1. `ProviderHealth` 新增只读、锁内原子 `select_probe_candidate(names, deadline)`，返回 provider + generation + eligible_at，不发 permit；真正 permit 仍在 slot 内 acquire。
2. Gateway 用本请求 attempts 计算 closed-empty、open/busy/failure 集合；仅 0 result 且满足 §4.3 时执行一次 `_run_rescue`。
3. rescue 等待同时受 budget deadline 和 task cancellation；等待结束后重新 snapshot/acquire，generation 已改变则诚实返回 busy/cooldown。
4. 运行 5 个 branch/13 query fixture，共享同一 Gateway；配置 4 providers、每 request 每 provider 至多一次普通调用、每 request 至多一次 rescue，因此硬上界为 `13*4 + 13*1 = 65` 次 transport。断言至少一 branch 命中、总 transport `<=65`、每 provider max in-flight=1、同 `(provider,generation)` probe owner `<=1`、每 response rescue executed `<=1`，无 probe storm。

**覆盖 AC**：SRCH-03、SRCH-04、SRCH-05、OBS-01。

### WI-3 — DeepResearch v4 immutable graph 与 technology profile

**新增**

- `backend/deskpet/workflows/definitions/v4/__init__.py`
- `backend/deskpet/workflows/definitions/v4/deep_research.py`
- `backend/deskpet/workflows/definitions/deep_research_v4_contracts.py`
- `backend/deskpet/workflows/definitions/deep_research_v4_intelligence.py`
- `backend/deskpet/workflows/definitions/deep_research_v4_nodes.py`
- `backend/deskpet/workflows/definitions/deep_research_v4_report.py`
- `backend/tests/test_workflow_deep_research_v4.py`
- `backend/tests/test_deepresearch_v4_intelligence.py`
- `backend/tests/fixtures/deep_research_v4_intelligence.json`

**修改**

- `backend/deskpet/workflows/bootstrap.py`
- `backend/main.py`
- `backend/config.py`
- `config.toml`
- `backend/tests/test_deepresearch_v2_default_wiring.py`
- `backend/tests/test_workflow_bootstrap.py`

**步骤**

1. 新建 v4 graph definition 而不修改 v3 文件，但禁止复制 `deep_research_v3_nodes.py` 的整份实现。v4 state 保持 v3 branch channel 的超集；generic profile 的 plan/branch/gap/rerank/synth/cite/persist 通过公开 handler 或稳定 pure function 委托给 v3，technology profile 只覆盖 normalize、plan、expand/direct、intelligence score/rerank/synth/cite/report，failure finalize 单独实现。若某段 v3 helper 不是公开可复用接口，先在 v4 写窄 adapter 调现有 public handler，不把 v3 helper 搬抄后分叉。注册 v4 definition/adapter/state factory，默认 `deep_research_version="v4"`；启动 allowlist 为 v2/v3/v4，fallback 为 v4；同时把 `progress.DEEP_RESEARCH_STAGE_VERSIONS` 与 `native._uses_deep_research_stage_contract` 的兼容集合扩为 v2/v3/v4，否则 v4 不会产生 durable completed stage。
2. normalize 按 §4.4 的三组词、冲突优先级与时间 grammar 生成 `IntentProfile`。测试 clock port 固定 as-of date，覆盖原始中文题、英文等价题、普通历史/法律/产品题、显式日期和用户明确要求监管的反例。
3. technology planner 先用确定性 taxonomy 限定候选桶，再让 LLM 输出严格 JSON topic label/entity；parser 失败时使用 deterministic 3～5 topic fallback。每 topic 生成三条短 query，不能重复整句用户 prompt。
4. `source_seeds` 严格使用 §4.4 schema/static table；扩展阶段按 channel 路由到 Gateway 或现有 direct adapter，最多三 seed、稳定排序去重，仍须实际检索和 passage support。v4 必须读取与 v3 相同的 `ResearchConfig.direct_sources/source_packs`：direct_sources=false 时所有 direct seed 明确投影为 skipped，不调用 `ResearchSearchPort.direct`；source_packs=false 时不追加 generic pack。
5. score/rerank 严格使用 §4.4 的离散等级、总分公式和 tie-breaker；未知值不补齐。fixture 对每一档 recency/impact/maturity/evidence quality 和同分排序写精确断言。report 首部显示 as-of/window/rubric，Top 项逐条渲染“变化/重要性/成熟度/引用”。重要性和成熟度必须绑定 winning passage，否则降为 inference/unknown 或剔除。
6. generic profile 调 v3-compatible adapter，并以 fixture 比较 plan、claims、report hash，不发生行为变化。

**覆盖 AC**：INTEL-01～05、BC-01。

### WI-4 — v4 早停、诚实失败和成功专属 Artifact

**修改**

- `backend/deskpet/workflows/definitions/v4/deep_research.py`
- `backend/deskpet/workflows/definitions/deep_research_v4_nodes.py`
- `backend/deskpet/workflows/progress.py`
- `backend/deskpet/workflows/native.py`
- `backend/tests/test_workflow_deep_research_v4.py`
- `backend/tests/test_workflow_progress.py`

**新增**

- `backend/tests/test_workflow_delivery_pipeline.py`

**步骤**

1. v4 在 direct join 后用 `ConditionalEdge` 路由 `research|no_results`；selector 只读 canonical search + direct candidate count。branch search 已完成是图拓扑前置条件，selector 不做外部调用，也不重复救援。
2. `no_results_finalize` 是 v4 私有 terminal node，不加入 `DEEP_RESEARCH_V2_STAGES`、不伪造一个 completed stage；它写安全 coverage、`skipped_stage_ids`、terminal error/action，delivery intents 为空。扩展 `NativeWorkflowExecutable._terminal_intents` 只读取 `values.terminal_public` 的固定 schema：`metrics` 仅允许 `actual_requests/hits/empty/timeouts/cooldown_skips/busy_skips/queue_timeouts/probes/rescue_considered_count/rescue_executed_count/candidates`，值为 `0..1_000_000` 非负整数；`diagnostic_codes` 最多 16 项且每项必须在 progress diagnostic allowlist；`skipped_stage_ids` 是 `fetch/score/gap/rerank/synth/cite/persist` 的去重子集、最多 7 项；`retry_action_id` 只能是 `retry_from_start` 或缺省。任何额外 key/错误类型都拒绝 patch，不能透传任意 state。校验后的字段合入 workflow.final；旧版本没有该对象时 payload 字节语义不变。验证计算 handler invocation 全为 0，最后一个可见 current row 来自 failed workflow.final。
3. cite gate 未通过时进入 `insufficient_evidence_finalize`，同样不生成 Artifact/final Markdown；成功路径才进入 persist + success finalize。
4. progress allowlist 增加计数桶、`skipped_stage_ids` 和 `retry_from_start` action id；搜索模板逐项输出“真实请求 X / 命中 X / 空结果 X / 超时 X / cooldown 跳过 X / busy 跳过 X / 排队超时 X / probe X”，不得把 cooldown 与 busy 合成一个“跳过”，也不透传 attempt 原文。
5. outbox/SessionDB 断言失败仅有 progress + workflow.final；没有 `workflow.report/workflow.artifact_card/workflow.final_assistant`，旧 run intents 不被改写。

**覆盖 AC**：FAIL-01、FAIL-02、UI-01、UI-02、OBS-01。

### WI-5 — 服务端 start-reference retry 与 IPC 幂等

**修改**

- `backend/deskpet/workflows/service.py`
- `backend/deskpet/workflows/launcher.py`
- `backend/deskpet/workflows/ipc.py`
- `backend/deskpet/workflows/store/run_store.py`
- `backend/tests/test_workflow_service.py`
- `backend/tests/test_workflow_launcher.py`
- `backend/tests/test_workflow_ipc.py`

**步骤**

1. RunStore 增加只读 `get_start_snapshot(run_id)`，以 run capability hash join snapshot，验证 `_workflow_start`、identity 和 start_payload 形状，并返回 `original_capabilities={snapshot 除 _workflow_start}`。
2. `build_workflow_service`/`WorkflowService` 注入 async `session_delivery_state_reader(session_id)`；main 传 `_sdb.get_session_delivery_state`，纯单测使用固定 reader。`WorkflowLauncher.retry_from_start_locked` 读取 v4 adapter/state/context，沿用原 start_payload 和拆出的 original capabilities，用 retry identity 创建并调度新 run；它只允许由已持有 session lock 的 Service 调用。
3. `WorkflowService.retry_run_from_start` 按 §4.5 在同一 session lock 内二次读取/校验 source snapshot、delivery state 和 epoch，再调用 launcher；校验 UUID v4，同 source/action/key 通过 start identity 原子幂等。
4. IPC 注册 `workflow_run_retry_from_start` / response；handler 要求 payload key 集合严格等于 `run_id/action_id/retry_key`，topic/query/URL 或任何额外字段都直接 `invalid_payload`。
5. 测试双击/网络重发同 key 返回同 run、不同 key 创建不同 run、保留 `_workflow_start` 不被重传、source identity 的 venue/session ids/epochs/turn_id 全部保持且只覆盖 request_id/logical_slot、非 terminal/v3/错误 action/删除 session 拒绝、delete/retry 同锁 race 两种顺序、旧 run delivery/event/state 不变且无 orphan。

**覆盖 AC**：FAIL-03、OBS-01、BC-01。

### WI-6 — Session UI 计数、skipped、失败态和一键重试

**修改**

- `tauri-app/src/types/messages.ts`
- `tauri-app/src/stores/sessionsStore.ts`
- `tauri-app/src/stores/sessionsStore.test.ts`
- `tauri-app/src/components/workflow/WorkflowProgressGroup.tsx`
- `tauri-app/src/components/workflow/WorkflowProgressGroup.test.tsx`
- `tauri-app/src/components/MessageStreamPanel.tsx`
- `tauri-app/src/components/MessageStreamPanel.workflow.test.tsx`
- `tauri-app/src/message-panel/MessagePanelRoot.tsx`
- `tauri-app/src/code-panel/ws.ts`

**步骤**

1. Message/store 增加 allowlisted coverage 字段、`workflow_skipped_stage_ids`、`workflow_retry_action_id`；只识别 v4 的 known action，历史自由文本 recovery_action 只显示文字，不变按钮。
2. `WorkflowProgressGroup` 将 search 结果行显示为互不混淆的指标；skipped stages 显示一个默认可见的“后续 7 步未执行”行，展开列阶段中文名，不能显示绿色完成图标。
3. terminal failed 总体卡固定红色失败；0 candidate 默认折叠态显示原因、实际请求/empty/timeout/cooldown/probe 摘要和“重新调研”。
4. `CodePanelWS` 新增 `WorkflowCommand={type:string,request_id:string,payload:Record<string,unknown>}` 与 `send_command(msg:WorkflowCommand): boolean`：只有 `state()==connected && ws.readyState==OPEN` 才同步发送，失败返回 false，绝不写普通 `_outbox`。`MessageStreamPanel` 增加可选 `onWorkflowRetry(runId, actionId, retryKey): Promise<{run_id:string}>` 并下传到 progress group。每个 `WorkflowProgressGroup` 以本卡 `runId` 持有唯一 `{retryKey,pending|ambiguous|resolved|rejected,result,error}` command state，retryKey 由 `crypto.randomUUID()` 生成一次；除服务端明确 rejected 外，同一卡不生成新 key。`MessagePanelRoot` 维护唯一 `request_id -> deferred Promise` map 和一个全局 response listener；首次 connected 发送时创建 deferred，重复调用同 request_id 复用同一 deferred 并再次 `send_command`。组件自己的 15 秒计时只把显示切到 ambiguous，不 settle Promise、不注销 root listener；“确认重试状态”在 connected 后用同 key 重发。迟到 response 仍由 root map resolve 原 Promise；definitive response/error 后才删除 map，窗口 unmount 时统一 reject/清理。未连接的首次发送不创建 map、不入 outbox，组件显示“连接后重试”并保留同 key。测试断线不入 outbox、15 秒后同 key 重发、迟到 response、两卡乱序和 reconnect。
5. 原生 button 保留 Enter/Space；stage disclosure 保持 `aria-expanded/aria-controls`，失败 live region 为 assertive。历史 replay、乱序、重复 final 不产生第二按钮或第二 skipped 行。

**覆盖 AC**：FAIL-03、UI-01、UI-02、OBS-01。

### WI-7 — 集成、真实网络、Xiaomi 真机与文档收尾

**新增**

- `scripts/acceptance/deepresearch_wide_topic_reliability.py`
- `scripts/e2e/launch_deepresearch_failure.ps1`
- `scripts/e2e/stop_deepresearch_failure.ps1`
- `plans/2026-07-15-deepresearch-wide-topic-reliability/results.md`
- `plans/2026-07-15-deepresearch-wide-topic-reliability/evidence/README.md`
- `testcase/2026-07-15-deepresearch-wide-topic-reliability/deepresearch-wide-topic-manual-test.md`

**修改**

- `testcase/README.md` 或现有 testcase 索引
- `ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md`
- `ARCHITECTURE/PROJECT_STATUS.md`

**步骤**

1. 验收脚本只接受题目 fingerprint，输出 run/provider/status/count/elapsed/reason code；任何 raw query/URL/body/token 检测到即失败。
2. 在同一 Tauri/backend 进程连续提交原始题目两次。必须得到两份成功 v4 报告，均满足时间窗/Top 技术/价值/成熟度/winning citations 和既有 publish gate，且不存在 run 内 cooldown 连坐。若外网断开，只能验收诚实失败 UI，不能据此通过报告质量 DoD。
3. Xiaomi 成功用例使用主配置。失败用例使用独立 `evidence/failure-config.toml`（由根 config 复制但 `[search_gateway] providers=["searxng"]`、`searxng_url="http://127.0.0.1:9/search"`、`per_provider_timeout_s=0.5`、`research_total_timeout_s=8.0`，并设 `[research] direct_sources=false`、`source_packs=false`，不含 secret），固定 userdata=`evidence/failure-userdata`、backend port=`8116`、Vite port=`5186`、日志=`evidence/failure-tauri.stdout.log|failure-tauri.stderr.log`。`launch_deepresearch_failure.ps1` 固定 `WorkingDirectory=F:\projects\deskpet\tauri-app`，注入 `DESKPET_CONFIG/DESKPET_USER_DATA_DIR/DESKPET_BACKEND_DIR/DESKPET_PYTHON/DESKPET_BACKEND_PORT=8116/DESKPET_VITE_PORT=5186/DESKPET_DEV_MODE=1`，构造 `$before='"'+$node+'" node_modules/vite/bin/vite.js --mode relay'` 与 `$override=@{build=@{beforeDevCommand=$before}}|ConvertTo-Json -Compress`，使用 `Start-Process -WorkingDirectory ... -WindowStyle Hidden -PassThru -RedirectStandardOutput ... -RedirectStandardError ...` 运行 `node_modules/@tauri-apps/cli/tauri.js dev --config $override`。启动前记录全部 `deskpet.exe` PID，启动后轮询只接受“新 PID、StartTime>=脚本 launch time、Path 精确 resolve 为 `tauri-app\src-tauri\target\debug\deskpet.exe`”的测试 app；把 CLI PID/path/start、app PID/path/start、ports 与日志写入 `failure-processes.json`。启动前用 `Get-NetTCPConnection` 断言 8116/5186 空闲。`stop_deepresearch_failure.ps1` 读取 manifest，先逐个用 PID + resolved executable path + StartTime 校验 CLI/app 身份，再终止测试 app、CLI、8116/5186 owning PID；端口 owner 同样先校验为本次 start time 之后的 Python/Node 子进程。最后断言 manifest 中 app/CLI PID 不存在、两端口无 listener，再删除进程 env/manifest；不按进程名误杀其他 DeskPet。不得手动启动 backend/Vite；日志必须出现 `[backend_launch] Dev python=... backend_dir=...` 和仅注册 searxng。真坐标输入原题后确认失败卡、无 Artifact、点击 retry；清理后主配置文件不改。每个 case 保存动作前后截图和安全日志摘录。
4. 跑聚焦、后端全量、前端全量、tsc/build/scoped ESLint；已知失败必须与 G0 对比无新增。
5. 更新 testcase 与 ARCHITECTURE，实现事实替换 §15 的“目标态”；不更新 `STATUS/status.md`。

## 6. 阶段门禁

### G1 — Search Gateway

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_search_gateway_provider_limiter.py backend/tests/test_search_gateway_provider_statuses.py backend/tests/test_search_gateway_routing.py backend/tests/test_search_gateway_wide_topic_burst.py backend/tests/test_search_gateway_config.py backend/tests/test_metrics_sink.py -q
```

通过条件：SRCH-01～05、UI-01、OBS-01 全部有确定性断言，5×13 fixture 至少一 branch 命中且 max in-flight/调用上界/probe 上界同时满足。

### G2 — v4 + retry + UI

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_workflow_deep_research_v4.py backend/tests/test_deepresearch_v4_intelligence.py backend/tests/test_workflow_progress.py backend/tests/test_workflow_delivery_pipeline.py backend/tests/test_workflow_service.py backend/tests/test_workflow_launcher.py backend/tests/test_workflow_ipc.py -q
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_workflow_deep_research_graph.py backend/tests/test_workflow_deep_research_v2.py backend/tests/test_workflow_deep_research_v2_recovery.py backend/tests/test_workflow_deep_research_v3.py backend/tests/test_workflow_recovery.py backend/tests/test_deepresearch_v2_default_wiring.py backend/tests/test_workflow_bootstrap.py -q
Set-Location tauri-app
$node='C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
& $node node_modules/vitest/vitest.mjs run src/stores/sessionsStore.test.ts src/components/workflow/WorkflowProgressGroup.test.tsx src/components/MessageStreamPanel.workflow.test.tsx
& $node node_modules/typescript/bin/tsc -b
& $node node_modules/vite/bin/vite.js build
```

通过条件：INTEL/FAIL/UI/BC 条款全覆盖；失败无 Artifact/final Markdown；same retry key 幂等；generic/v1/v2/v3 fixture 无漂移。

### G3 — 最终 DoD

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
Set-Location tauri-app
$node='C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
& $node node_modules/vitest/vitest.mjs run
& $node node_modules/typescript/bin/tsc -b
& $node node_modules/vite/bin/vite.js build
& $node node_modules/eslint/bin/eslint.js src/stores/sessionsStore.ts src/components/workflow/WorkflowProgressGroup.tsx src/components/MessageStreamPanel.tsx src/message-panel/MessagePanelRoot.tsx src/code-panel/ws.ts
```

另行执行 `scripts/acceptance/deepresearch_wide_topic_reliability.py`、同进程两次真实原题和 Xiaomi 真机用例。只有 acceptance 全部“必须”条款有证据、完成度审计 PASS、ARCHITECTURE/testcase 回写完毕才可结束。

## 7. AC 映射

| AC | 工作项 | 主要证据 |
|---|---|---|
| SRCH-01/02/05 | WI-1 | limiter/cancel/config tests |
| SRCH-03/04 | WI-2 | rescue + 5×13 burst fixture |
| INTEL-01～05 | WI-3 | intent/planner/ranking/report fixtures + live reports |
| FAIL-01/02 | WI-4 | graph invocation/outbox negative assertions |
| FAIL-03 | WI-5～6 | service/IPC idempotency + UI click |
| UI-01/02 | WI-1/4/6 | coverage equality + reducer/component + Xiaomi |
| OBS-01 | WI-1/4/5/7 | schema/privacy scan + safe evidence |
| BC-01 | WI-0/3/5/7 | v1/v2/v3 hash/checkpoint/generic fixtures |

## 8. 完成定义

- `acceptance.md` 每条必须 AC 有自动化或真机证据，没有“部分完成”。
- 原题在同一真实进程连续两次成功，报告内容与引用质量均达标。
- 无新增全量失败；build、隐私扫描通过；scoped lint 相对固定 G0 基线无新增错误，既有 lint 债不冒充本轮回归。
- testcase、`ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md`、`ARCHITECTURE/PROJECT_STATUS.md` 同次更新；`STATUS/status.md` 保持不动。
- 最终完成度子代理输出 `VERDICT: PASS`。
