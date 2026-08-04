# Search Gateway cooldown 隔离与 DeepResearch v3 引用质量优化结果

> 日期：2026-07-15
> 最终结论：**PASS / SHIP**
> 架构事实源：[`ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md`](../../ARCHITECTURE/SEARCH_GATEWAY_DEEPRESEARCH.md)

## 1. 交付结果

### 1.1 cooldown 不再跨请求连坐

- provider 健康状态改为 `closed/open/half_open` 原子状态机；请求预算和 diagnostics 继续 request-local。
- 按失败类型区分 threshold、open interval 与 probe backoff；429 尊重有上限的 `Retry-After`，403 不再误作 rate-limit。
- 每个 provider/open generation 只有一个 single-flight probe；并发 loser 得到 `half_open_busy` 并继续其他 provider。
- 所有可用 provider 都 open 时，只选择 deadline 内最早 eligible provider 做受控探针；连续 10 个请求只产生 1 次真实上游调用的回归已锁定。
- probe 取消、失败和旧 token 提交都由 generation/token CAS 收口；cache hit 不改变 circuit 状态。
- metrics/coverage 只记录 request/run id、provider、permit、probe outcome、计数和耗时，不泄露 query、URL、正文或凭据。

### 1.2 引用支持率由“引用数量”升级为“逐 claim 有证据”

- 新 run 默认进入 immutable `deep_research/v3`；v1/v2 definition、registry 与恢复兼容保留。
- 先选择 `EvidencePassage`，再生成结构化 `AtomicClaim`；support 判断记录真正支持它的 passage/source。
- 同一 canonical URL 只计一个 citation source，但会保留该 URL 内最强的独立支持 passage。
- rerank 先保证独立域，再保证唯一 canonical URL，最后才补重复 passage，避免单一 URL 挤掉有效引用。
- repair 仅允许从原 claim 中确定性保留被同一 passage 支持的 clause；不改写、不新增事实或引用。仍不支持的 factual claim 被剪除。
- support rate 分母固定为 repair 前 factual claims，剪除不能把支持率“洗”成 100%。
- fallback 先覆盖不同来源，再补足独立支持 claim；镜像来源陈述同一事实时会合并事实并保留支持它的多个 source id。
- 只有支持率、事实数、引用数、独立域与正文 bytes 全部过门，才发布 completed 报告；否则诚实输出 `no_results/insufficient_evidence`。

### 1.3 Session 进度从“到了哪一步”升级为“做了什么、结果如何”

- 总体进度卡始终可见，默认紧凑 timeline 逐行显示阶段状态、动作和一行结果。
- cooldown、timeout、低质量丢弃、claim repair/prune 等白名单诊断在折叠态可见。
- completed child 详情仍默认隐藏；用户点击某一步或“查看全部阶段”才展开指标、耗时、诊断和下一步。
- timeline 完全由 durable stage messages + 当前 summary 派生，不新增第二份前端事实源；历史恢复、乱序、重复和多 run 隔离沿用现有 reducer 契约。
- disclosure 使用原生 button、`aria-expanded`、`aria-controls` 与 live region。

## 2. 真实连续 benchmark

测试期间不重启应用/backend，固定顺序连续运行三类调研：

| 类别 | run_id | 状态 | elapsed | support | factual claims | citations | domains | body bytes | attempts / probes |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 官方技术文档 | `904c4117c8a848aaa395df1792301185` | completed | 59065 ms | 1.0 | 13 | 9 | 6 | 2175 | 30 / 0 |
| 中国政策 | `6621f93a890e443eb0e5d69f10f0173c` | completed | 66409 ms | 1.0 | 12 | 10 | 7 | 4143 | 22 / 2 |
| Web 标准动态状态 | `bb09339bc2dc44dba8477e1ff44ec98a` | completed | 60055 ms | 1.0 | 14 | 8 | 8 | 2442 | 22 / 2 |

聚合结果：3/3 completed，mean support rate `1.0`，nearest-rank P95 `66409 ms`，政策 run 后的 WebGPU run 仍有真实 provider attempts/probes，判定 **PASS**。机器可读结果见 [`benchmark-live-final.json`](./benchmark-live-final.json)。

## 3. Windows 真机 UI 结果

- 设备：Xiaomi 1920×1080 屏幕；应用处于已登录状态。
- run `61364802154a444db6dcf193d9f1ab0d`：总体卡显示 13 阶段；在 `2/13` 时已完成行直接显示“理解任务”“规划调研”的动作与结果。
- 点击“检查证据缺口”后，仅该行展开，显示 round、新 queries/evidence、elapsed 和下一步 `rerank`；其他阶段保持紧凑。
- 最终报告和 Markdown ArtifactCard 均在 Session 中可见；`workflow.artifact_card`、Session 和 websocket delivery 均为 delivered。
- 最终 WebGPU 产物：`DeepResearch/请对-Official-browser-support-status-for-W-bb09339bc2dc44dba8477e1ff44ec98a.md`，13441 bytes。
- 截图与 backend/durable 联合证据索引：[`evidence/README.md`](./evidence/README.md)。核心图包括 [compact timeline + Artifact](./evidence/03-xiaomi-compact-timeline.png)、[逐步动作/结果](./evidence/05-xiaomi-timeline-actions-results.png) 和 [单步展开详情](./evidence/06-xiaomi-expanded-plan-stage.png)。
- 可复跑步骤与坐标见 [`testcase/2026-07-15-search-quality-cooldown-support/search-quality-cooldown-support-manual-test.md`](../../testcase/2026-07-15-search-quality-cooldown-support/search-quality-cooldown-support-manual-test.md)。

## 4. 自动化与构建证据

- 最终后端集成聚焦：`127 passed in 10.80s`。
- 后端全量：`4461 passed, 10 known failures, 14 skipped, 9 deselected`；失败集合比较 `expected=10 actual=10`，无新增失败。JUnit：[`backend-full-final-junit.xml`](./backend-full-final-junit.xml)。
- 前端全量：`77 files / 806 tests passed`。
- `tsc -b`：PASS。
- Vite production build：PASS；仅保留既有 chunk size / dynamic import warning。
- scoped ESLint：PASS。
- 本轮相关路径 `git diff --check`：PASS；全仓检查仅命中用户已有的 6 月运行日志尾随空格，本轮未改该文件。
- benchmark 机器输出隐私门：仅保留 `category/query_fingerprint/run_id` 等安全标识，不包含原始 query、URL、正文或 secret；回归 `2 passed`。

## 5. 真测暴露并补修的问题

1. 初次政策样本 quality gate 未过：修复 rerank 被同一 URL 多 passage 挤占、fallback claim 数不足，以及镜像来源支持关系丢失。
2. 修复后重放该 checkpoint：`claims=9`，quality objective `(1,9,9,8,1.0,2469)`，gate PASS。
3. 测试 userdata 残留 `deep_research_version="v2"` 曾使真机未进入新链路；产品默认原本已是 v3，测试环境也已纠正并重新真测。

## 6. DoD

- [x] cooldown 连坐有机制修复、并发/fake-clock/真实连续 benchmark 证据。
- [x] 引用支持率有结构化 claim/passage gate，三类真测均为 1.0。
- [x] Session 折叠态能直观看见每一步的动作与结果，展开态提供详细诊断。
- [x] 默认功能立即开启，不做灰度。
- [x] 自动化、全量回归、类型检查、生产构建、真机 E2E 均完成。
- [x] testcase 与 `ARCHITECTURE` 事实源同步完成；未向 `STATUS/status.md` 写入新事实。
