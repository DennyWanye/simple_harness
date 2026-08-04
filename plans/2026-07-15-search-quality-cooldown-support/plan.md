# DeskPet 搜索质量、cooldown 隔离与 DeepResearch 可解释进度实施计划
<!-- plan-status: finalized -->

> 日期：2026-07-15
> 验收基线：[acceptance.md](acceptance.md)
> 架构基线：[../../ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md](../../ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md)
> 发布策略：测试阶段完成即默认开启；`deep_research/v3` 服务新 run，v1/v2 只承担旧 run 恢复。

## 1. 目标和不可破坏边界

本轮同时解决三件事：provider cooldown 跨请求连坐、DeepResearch 低引用支持率、聊天 session 进度只显示“做到哪一步”而不显示“做了什么和结果”。

不可破坏项：

- `web_search` / `deepresearch` 工具名、13 个 public stage、artifact 和 final assistant 交付形状不变。
- `deep_research/v1`、`deep_research/v2` definition、handler、quality、report 及 manifest/hash 行为冻结，旧 checkpoint 必须可恢复。
- 新实现注册为 `deep_research/v3` 并成为新 run 默认；v3 继续发送 `schema_version=2` 的 13-stage progress contract。
- Search Gateway health 可按 provider 共享；deadline、cancel、budget、attempt diagnostics 必须 request-local。
- UI 只投影 durable outbox/SessionDB 事实，不建立第二份进度事实源。

## 2. 代码影响面

| 文件 | 改动 |
|---|---|
| `backend/deskpet/retrieval/cache.py` | provider circuit、generation/token permit、single-flight half-open、分类 outcome |
| `backend/deskpet/retrieval/contracts.py` | `RATE_LIMIT`、`HALF_OPEN_BUSY` 等稳定状态/错误码 |
| `backend/deskpet/retrieval/providers/base.py` 及 `baidu.py`、`duckduckgo.py`、`bing_http.py`、`searxng.py` | 403/429/CAPTCHA/timeout 分类，保留 `Retry-After` |
| `backend/deskpet/retrieval/search_gateway.py` | all-open 选择/有界等待、permit 提交、request-local 诊断 |
| `backend/config.py`、`config.toml` | 各 failure class 的 threshold/cooldown/backoff 默认 ON 配置 |
| `backend/deskpet/workflows/definitions/deep_research_v3_contracts.py` | `EvidencePassage`、`AtomicClaim`、`SupportDecision`、`PublishDecision` |
| `deep_research_v3_quality.py`、`deep_research_v3_nodes.py`、`deep_research_v3_report.py` | passage-first、原子 claim、支持判定、确定性 pruning、发布门禁 |
| `backend/deskpet/workflows/definitions/v3/deep_research.py`、`v3/__init__.py`、`workflows/bootstrap.py` | v3 immutable definition、默认版本与 v1/v2 兼容注册 |
| `backend/deskpet/workflows/progress.py`、`native.py`、`backend/main.py` | v3 stage projection 和安全 action/result 字段；默认启动 v3 |
| `tauri-app/src/stores/sessionsStore.ts` | v2/v3 通用进度识别、completed child + current summary projection |
| `tauri-app/src/components/workflow/WorkflowProgressGroup.tsx` | 默认可见紧凑时间线、逐阶段展开、状态视觉和 ARIA |
| 对应 backend/frontend tests、benchmark、testcase、ARCHITECTURE | 回归、真机证据和文档闭环 |
| `scripts/acceptance/compare_pytest_failures.py`、`deepresearch_quality_benchmark.py` | 全量失败集合比较与固定集机器判定 |

明确禁止修改 `deep_research_v2_nodes.py`、`deep_research_v2_quality.py`、`deep_research_v2_report.py`、`definitions/v2/deep_research.py`。

## 3. 分阶段任务

### Task 1 — 绿色基线和失败用例锁定

1. 把当前 focused pytest、vitest、tsc、全量已知红项写入 `baseline.md`；创建 `expected-backend-failures.txt`，按一行一个规范化 pytest node id 固定全量基线的 10 个既有失败，供 comparator 直接读取。
2. 先补可复现测试：
   - validated empty 不打开 circuit；timeout/403/429/CAPTCHA/invalid-response 使用独立策略。
   - 全 provider open 时按 `next_forced_at` 选择；安全时间在 deadline 内的首个 owner 必须 probe，并发只有一个持有 token。
   - policy→WebGPU 顺序运行且不重置 health，第二个请求不能仅因 cooldown 得到 `providers=0`。
   - 长文中段 passage、冲突数字、跨来源拼接、复合事实拆分、unsupported pruning 和 gate 边界。
   - compact timeline 默认可见 action/result；started/waiting/failed 当前行与 completed child 不重复。
3. 新测试不得用 skip/xfail 掩盖，修复前能命中缺陷，修复后全绿。

覆盖：AC-CD-01~06、AC-QA-01~06、AC-UI-01~05、AC-OBS-01、AC-BC-01/02。

### Task 2 — Per-provider circuit 与 half-open single-flight

1. `_Health` 保存 `state`、`generation`、分类连续失败计数、`next_forced_at`、`probe_failures`、`probe_token`；`CircuitPermit` 保存 kind/generation/token。
2. `acquire_permit()` 与 `complete_probe()` 在同一 async lock 内 CAS：旧 generation/token 不能关闭新 circuit；cancel/timeout 在 `finally` 只释放自己的 lease，重复完成幂等。
3. outcome 分类：
   - hit、partial hit、validated empty = transport success，关闭/保持 closed；empty 不累计失败。
   - timeout、blocked(403)、captcha、rate_limit(429)、HTTP、invalid-response 分别按配置 threshold、cooldown、backoff 处理。
   - 429 解析有上限的 `Retry-After`，但不能突破 request deadline。
   - 默认策略明确为：timeout/HTTP/invalid-response `threshold=2, open=30s, probe_base=2s, probe_max=30s`；blocked `1/300s/15s/120s`；captcha `1/600s/30s/300s`；rate-limit `1/60s`，且 `Retry-After` 是 probe 的硬下界。全部写入 `[search_gateway]` 可配置字段。
4. 正常 routing 跳过 open provider，但继续尝试健康 provider。若所有本次实际 available provider 均 open，按 `(next_forced_at, 配置序号)` 选择；每个 open generation 只武装一个 forced permit，首个安全时间在 deadline 内的 eligible owner 必须有界等待并原子申请，其他并发/已消费 generation 请求得到 `half_open_busy/cooldown` 后继续后续 wave。
5. probe 失败产生新 generation，但按上次 probe 时间递增 backoff/推进 `next_forced_at`，不得立即重新武装；同 provider 每个 interval 最多一次真实探测，避免“一请求一冲击”和固定第一个 provider 饥饿。
6. 指标只记 provider、state、transition、failure class、probe outcome、计数/时长，不含 query、URL、正文、token。
7. fake clock + blocking provider 覆盖 closed→open→half-open→closed/open、10 个顺序请求的调用上界、cancel release、多 provider、公平选择、cache hit 不改 circuit。

覆盖：AC-CD-01~06、AC-OBS-01、AC-BC-01/02。

### Task 3 — 新建 immutable `deep_research/v3` 质量链

1. 复制 v2 definition 结构到 v3，但所有 handler 只依赖新的 v3 模块；增加 fixture 测试证明 v2 definition hash、manifest 和 recovery 行为未变。
2. 内部稳定模型：
   - `EvidencePassage{passage_id=hash(source_content_hash+start+end), source_citation_id, canonical_url, question_id, text/ref, relevance}`。
   - `AtomicClaim{claim_id,text,kind=factual|inference|opinion,citation_ids}`。
   - `SupportDecision{claim_id,supported,winning_passage_ids,winning_source_citation_ids,reason_codes}`。
3. plan 生成 3~5 个不重叠、可独立核验子问题；passage selector 从全文按段落/句窗选稳定候选，不再固定正文开头 1200 字符。
4. synth 直接生成结构化 JSON atomic claims；每个 factual claim 只有一个事实关系。兼容 parser 若存在，只共享 tokenizer/span，不参与 v3 主链 repair。
5. support 对每个 cited passage 独立判断；数字、日期、货币和版本必须出现在同一 winning passage，禁止跨来源拼接。可选 semantic scorer 只能辅助候选排序，deterministic lexical/exact 仍是发布主判。
6. 唯一 repair 是可重放纯函数：按稳定 clause span 只保留原 claim 中被同一 cited passage 支持的原文 clause，不改写、不新增事实/引用，每 claim 最多一次；仍 unsupported 才 fail-closed pruning。cite 不调用第二次 LLM。support 分母固定为 repair 前 factual claims，分子只增加真正 repair 后支持的 claim，pruning 不改变分母。
7. `definitions/v3/__init__.py -> workflows/bootstrap.py::build_workflow_service() -> WorkflowRegistry` 注册 v3；`main.py` 新增 v3 state adapter，并让 v2/v3 复用同一 Search Gateway context factory/ports，避免行为漂移。`backend/config.py` 与 `config.toml` 默认 `deep_research_version="v3"`，launcher 接受 v2/v3 且 v1/v2 recovery adapter 保留。进度识别按 workflow name + schema version + stage allowlist，不硬编码 `workflow_version === v2`。

覆盖：AC-QA-01~03、AC-OBS-01、AC-BC-01/02。

### Task 4 — PublishDecision、正文计量与诚实降级

1. 先用纯函数 `render_body(published_claims, analysis, limitations) -> body_md` 产生唯一 canonical 正文，再以其 UTF-8 bytes 和固定分母产生 `PublishDecision`：
   - `support_rate = supported_factual_pre_prune / factual_claim_count_pre_prune >= 0.60`
   - 最终 published factual claims ≥ 8
   - 最终 published factual claims 实际使用的 unique canonical sources ≥ 8
   - 上述 sources 的 independent domains ≥ 4
   - 去重正文 UTF-8 bytes ≥ 1500
2. citation/domain 只来自 winning passages 对应且真正被 published factual claim 引用的 source；不能用 evidence pool 总数或未发布来源充数。inference/opinion 不计入 8 条 factual claim。
3. `render_report(body_md, decision, appendix, coverage)` 只组装一次完整报告，不再重渲染正文；body bytes 明确排除重复“直接回答/发现”、appendix、Coverage JSON 和 error summary。
4. 仅 gate pass 输出 `completed`。失败输出 `no_results + insufficient_evidence`、安全不足项与限制，不发布 unsupported assertive claim，不伪造 citation。
5. coverage/progress 记录安全 request_id/run_id 关联的 provider attempt/permit/probe 汇总，以及 candidates、passages、pre/post support、published/discarded/repaired、used citations/domains、body bytes、elapsed 和 reason-code histogram；不含 query/URL/正文。

覆盖：AC-QA-03~06、AC-OBS-01、AC-BC-01/02。

### Task 5 — 用户可读的 session 紧凑进度时间线

1. 后端为 13 个 stage 使用固定模板生成白名单字段：`action`、`result`、`result_code`、`diagnostic_codes`、`published`、`discarded`、`repaired`。禁止 raw dict、prompt、query、URL、正文和异常栈进入聊天消息。
2. 前端投影契约：
   - durable completed child 按 `(run_id, seq, stage-instance)` 排序；
   - 当前 summary 派生 started/waiting/failed/cancelled 行，key 为 `run_id:current:stage_id`；
   - 同 stage completed child 到达后替换 current 行，不重复；terminal failed 保留，late child 不覆盖 terminal summary；
   - `degraded = completed + warning`，不是新 terminal status。
3. 总体进度卡始终可见：当前阶段、完成/总数、耗时、状态和默认可见 compact timeline。每行展示状态图标、用户语言的动作、一行结果；cooldown/timeout/低质量丢弃/pruning 原因直接可见。
4. 每个阶段行为原生 disclosure button，点击可看耗时、指标、详细结果/错误和下一步；“查看/收起全部阶段”控制整组。详细 child bubble 默认隐藏，但紧凑行永远存在。
5. 使用原生 button 的 Enter/Space，不绑定重复 toggle；保持 `aria-expanded`、`aria-controls`、polite live status，失败/等待可读。覆盖 13 阶段、中文省略、历史恢复、乱序/重复、多 run。

覆盖：AC-UI-01~05、AC-OBS-01、AC-BC-01。

### Task 6 — 自动化、真实 benchmark、Windows 真 E2E 与文档闭环

1. 阶段门禁按 focused backend → focused frontend/tsc/scoped eslint → backend/frontend 全量 → production build 执行；结果不得劣于 `baseline.md`。
2. 固定真实集合写入 `benchmark-queries.json`，必须从 UI 依次输入且中间不重置 Gateway health：
   1. `Python 3.14 free-threaded mode versus Python 3.13`
   2. `Current regulatory requirements of China's Interim Measures for Generative AI Services`
   3. `Official browser support status for WebGPU`
3. 用 `scripts/acceptance/deepresearch_quality_benchmark.py` 从指定 workflow DB + 三个 run id 生成 `deepresearch-live-benchmark.json`，校验 query 顺序、3/3 completed、每份 gate、mean support ≥ 0.70、nearest-rank P95 ≤ 360s。外部全断只验诚实 no-results，不计成功。
4. 只启动 Tauri，让它自管 backend/vite；注入正确 `DESKPET_BACKEND_DIR`。在小米屏幕严格按固定 query 顺序真点击三次 DeepResearch，其中第 2→3 次即 policy→WebGPU 连续场景；验证实时追加 action/result/reason、逐阶段展开、最终 report/Artifact、历史恢复，并保存动作前坐标声明、截图、Tauri/backend/outbox 日志。
5. 更新 `testcase/2026-07-15-search-quality-cooldown-support/`、`testcase/index.md`、`ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md`、`ARCHITECTURE/ARCHITECTURE.md`、`ARCHITECTURE/PROJECT_STATUS.md`。`ARCHITECTURE/` 是唯一事实源；按当前仓库 AGENTS.md 禁止继续编辑 `STATUS/status.md` 的兼容跳转正文。

覆盖：全部 AC 和 DoD。

## 4. 精确阶段门禁

```powershell
# backend focused
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_search_gateway_routing.py `
  backend/tests/test_search_gateway_production_wiring.py `
  backend/tests/test_search_gateway_provider_statuses.py `
  backend/tests/test_deepresearch_v3_claim_support.py `
  backend/tests/test_workflow_deep_research_v3.py `
  backend/tests/test_workflow_v2_compat_fixtures.py `
  backend/tests/test_workflow_progress.py -q

# frontend: ambient npm/npx 不可用，固定使用 bundled Node
$node = 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
Set-Location tauri-app
& $node .\node_modules\typescript\bin\tsc -b --pretty false
& $node .\node_modules\vitest\vitest.mjs run `
  src/components/workflow/WorkflowProgressGroup.test.tsx `
  src/components/MessageStreamPanel.workflow.test.tsx `
  src/stores/sessionsStore.test.ts
& $node .\node_modules\eslint\bin\eslint.js `
  src/components/workflow/WorkflowProgressGroup.tsx `
  src/components/workflow/WorkflowProgressGroup.test.tsx `
  src/components/MessageStreamPanel.workflow.test.tsx `
  src/stores/sessionsStore.ts src/stores/sessionsStore.test.ts
& $node .\node_modules\vite\bin\vite.js build

# full regressions and failure-set comparison
Set-Location ..
backend\.venv\Scripts\python.exe -m pytest backend\tests -q `
  --junitxml=plans/2026-07-15-search-quality-cooldown-support/backend-full-junit.xml
backend\.venv\Scripts\python.exe scripts/acceptance/compare_pytest_failures.py `
  --junit plans/2026-07-15-search-quality-cooldown-support/backend-full-junit.xml `
  --expected plans/2026-07-15-search-quality-cooldown-support/expected-backend-failures.txt
Set-Location tauri-app
& $node .\node_modules\vitest\vitest.mjs run

# 真实 benchmark 汇总（run id 来自三次 UI 运行）
Set-Location ..
backend\.venv\Scripts\python.exe scripts/acceptance/deepresearch_quality_benchmark.py `
  --workflow-db '<user-data>\data\workflow.db' `
  --query-file plans/2026-07-15-search-quality-cooldown-support/benchmark-queries.json `
  --run-id '<tech-run>' --run-id '<policy-run>' --run-id '<webgpu-run>' `
  --output plans/2026-07-15-search-quality-cooldown-support/deepresearch-live-benchmark.json
```

全量 backend/frontend 命令和当前已知红项记录在 `baseline.md`；最终 `results.md` 必须逐条给出 AC → task → code → testcase → result 证据矩阵。

## 5. 幂等、失败与安全边界

- circuit permit 在 success/failure/empty/cancel 全路径释放；重复/旧 token 不能重复 transition。
- progress 继续使用 outbox stable event id + SessionDB `workflow_event_id` + frontend seq/event reducer；UI 不持久化第二份 timeline。
- report/artifact 继续使用现有 content/report hash 和 workflow intent 幂等；gate fail 不保存伪 completed draft。
- provider/LLM/fetch 任一失败只能形成结构化 degraded/no-results；不得补造来源、论断或成功状态。
- 所有新完成能力默认 ON；仅保留紧急 kill-switch 与 v1/v2 兼容读取。
