<!-- plan-status: finalized -->
# Plan：Harness 与 Memory SDK Observability 完善

## 主要矛盾

决定成败的是：在不把 observability 变成新 authority、不读取正文来脱敏、也不让两个独立包产生协议漂移的前提下，建立一套能跨 Host/Harness/Memory/restart 重建因果链的安全事件协议。

## 关联验收

覆盖 AC-OBS-1～AC-OBS-12；验收唯一事实源为同目录 `acceptance.md`，保障边界为 `assurance-contract.json`。

## 最佳实践与项目适配

- 采用 OpenTelemetry Logs/Trace 的 event-time、severity、attributes、trace/span correlation 思路，但不引入 exporter/collector SDK；本项目是本地桌面端，使用更小的 immutable envelope、opaque IDs 与本地 sinks。
- 采用 OWASP “不得记录 token、密码、连接串和敏感个人数据”的排除原则，但加强为 SDK 源头字段 allowlist；末端 regex 只作纵深防御，不能作为正文保护主机制。
- 采用 JSON Lines 的逐事件独立编码与 rotating file 方式；为移动端增加固定容量 ring，不承诺无界历史。
- Python logging 仅保留为 adapter；不让 ambient Host processor 成为 SDK 隐私边界。

## 发布与依赖策略（用户 review 后定稿）

1. Harness SDK 最终发布 0.4.0：新增公共且 import-pure 的 `simple_harness.observability` wire contract/runtime/sinks；该子包不得导入 Harness runtime、execution、provider、tool 或 sqlite。
2. Memory SDK 最终发布 0.5.0：基础依赖明确加入 `simple-harness-sdk>=0.4,<0.5`，只导入 `simple_harness.observability`；独立 Memory consumer未配置 sink 时使用 Noop。
3. 三个 slice 只用 commit、gate receipt、内部 candidate build tag 与 wheel SHA 区分，不占用多个正式版本号；全部验收完成后按两个 SDK README 各自发布流程一次性发布 0.4.0/0.5.0。
4. Host 最后一次性 revendor 最终 exact wheel pair，更新 SHA、candidate identity 与 lockfile。

不新增第三个包，也不复制 schema。若 import-purity 测试证明该依赖会拉入 Harness runtime，则本轮在实现前 BLOCKED 并升级，而不是静默改为复制协议。

## 三个垂直 release slice

每个 slice 都跨协议、SDK、Host、制品和验证边界，拥有独立 acceptance 子集、gate manifest、exact-wheel candidate 与 rollback 点：

| Slice | MUST AC（≤8） | 可独立使用的交付物 |
|---|---|---|
| S1 安全事件与本地 sinks | AC-OBS-1/2/5/7/8/11 | 内部 candidate A：两 SDK 同协议、两 SDK public builders 均可注入、Host JSONL/ring、canary 契约 |
| S2 correlation/状态/快照 | AC-OBS-3/4/6/9/11/12 | 内部 candidate B：全链路重建、状态事件与两个 SDK snapshot；升级自 S1 |
| S3 bundle 与优化闭环 | AC-OBS-5/7/8/9/10/12 | 最终 0.4.0/0.5.0 candidate：Host bundle、安全聚合、故障定位与指标对比；升级自 S2 |

每个 slice 的 Task 数 ≤10、计划 <2000 行、高风险子系统 ≤3；每个 slice 完成后构建并验证 Harness/Memory 两个 exact wheel，再由 Host vendor 成对消费。后续 slice 不回写前一 slice 的 frozen oracle。

## 文件影响清单

| 仓库/文件 | 改动 |
|---|---|
| Harness `src/simple_harness/observability/{contracts,correlation,redaction,runtime,sinks,snapshot}.py` | wire schema、默认拒绝字段、sink、ring/JSONL、计数与快照 |
| Harness `contracts/events.py` | 与新 envelope 的兼容/退役桥接，避免双协议 |
| Harness `runtime/production.py`, `runtime/kernel.py`, `runtime/ports.py` | Host 注入、生命周期、run/recovery 事件与 snapshot |
| Harness `execution/{memory_outbox,context_staging,dispatch}.py`, `tools/executor.py` | authoritative transitions 后发射安全事件 |
| Memory `core/manager.py`, `core/fact_jobs.py`, `backends/{base,sqlite,mock}.py` | sink/correlation 注入、recall/write/job/recovery 事件、aggregate snapshot |
| Memory migrations | 如跨重启 correlation 必需，新增 nullable opaque correlation 列与迁移验证 |
| Host `backend/main.py` 与 `backend/observability/sdk.py` | 构造 JSONL+ring+logging composite sink并注入两个 SDK |
| Host `tauri-app/src-tauri/src/diagnostics.rs` | 收集有界 SDK export/snapshot、总量限制、canary 测试 |
| 三仓 `ARCHITECTURE/`、版本/release 文档 | 当前事实、版本、验证证据 |

## Assurance / 信任与失败边界

- 使用 `standard` profile 与 TRUST-OBS-1..3；Host/SDK/OS 可信，sink 实现可能失败。
- 入口链：Host ingress 生成/接纳 correlation → Harness run → Context/Memory recall → provider/tool → Harness outbox → Memory receipt/fact job → restart recovery → Host export。
- 敏感停止追踪点：在正文仍可见的业务函数内只计算长度/计数/枚举；正文、exception、headers、payload 不得传给 emitter。
- 事件不参与事务、CAS、授权、幂等 key 或 retry decision；sink 返回值永远不反馈业务链。
- 攻击面：恶意 nested attributes、异常 repr、超长 key/value、Host failing/reentrant sink、并发 emit/close、符号链接/不安全 JSONL 路径、bundle 超量、跨身份 correlation。

## Task 1 — 公共安全协议与 sinks（Slice 1）

- 在 Harness 新建 observability 包：定义 immutable `ObservabilityEventV1`、`CorrelationContext`、枚举、schema validator 和 canonical `to_dict()`。
- attributes 只能来自稳定 allowlist；拒绝 `content/body/message/prompt/response/query/token/key/authorization/cookie/password/exception/args/result` 及未知 key。字符串/集合深度、数量和长度硬限。
- 实现 `ObservabilitySink` Protocol、Noop、Recording/RingBuffer、Composite、Jsonl、Logging adapter；所有 sink 由 `SafeEmitter` 包裹并记录 emitted/dropped/sink_errors/overflow。
- JSONL 使用 0600 regular-file/no-symlink 检查、max_bytes/max_files 轮转；ring 使用 lock + deque(maxlen)。
- 同一 slice 在 Memory `MemoryManager.__init__/build/build_development/build_production` 与直接 backend constructor 增加 optional sink，并只完成 envelope 接纳、safe emitter、lifecycle/sink stats 接线；recall/job 的细粒度状态事件留到 S2。Host 同一 slice 注入 composite sink。因此 S1 的两个 exact wheels 与 Host 均能独立兑现 AC-OBS-2。
- 验证：schema golden、forward compatibility、nested canary、failing/reentrant sink、并发、轮转/权限/溢出。
- 覆盖：AC-OBS-1/2/5/7/8/11，TO-A1/A2/A5/A7/A8/A11、TO-R1/R2/R3/R5。

### Sink 调用与生命周期契约

- `emit()` 是同步、non-blocking API，只执行 schema/redaction、计数和有界 `put_nowait`；业务线程不直接调用文件或 Host sink。
- 每个异步/阻塞 adapter 由单个有界 worker + 固定容量队列承载；满队列执行 drop-newest 并增加 `overflow_dropped`，不创建额外线程。
- Composite 对每个 child 独立队列/计数；某 child 失败不回滚其他 child，`accepted_children/failed_children` 可诊断。
- `flush(timeout)`/`close(timeout)` 有固定默认 1 秒上限；超时转为 dropped/close_timeout 计数。close 幂等；emit-after-close 被丢弃并计数；emit/close 竞态由 lock + lifecycle `open/closing/closed` 归约。
- reentrant sink 不在 worker 内回调 emitter；检测到同 worker recursion 时丢弃并计数。

## Task 2 — Harness 全链路接入与快照（Slice 2）

- `ProductionRuntimeConfig` 新增 optional sink/correlation factory；构造单一 `ObservabilityRuntime` 并注入 RuntimePorts、MemoryOutboxDispatcher、Context staging、provider/tool coordinators。
- 在 run start/terminal、provider attempt、tool authorize/invoke/settle、context prepare/stage/consume/abandon、outbox claim/apply/retry/dead-letter、recovery blocker/resolution 后发射状态事件。
- 使用现有 durable identity 映射 root/parent/operation；attempt、replay、lease epoch 为安全 attributes。duration 用 monotonic clock。
- `Runtime.diagnostics_snapshot()` 聚合 lifecycle、active runs、context/outbox 状态计数/oldest age、recovery blockers、最近错误码和 sink stats，不查询 payload。
- 保持旧 logger 兼容，但从关键链移除 exception repr/自由正文，改为 LoggingSink 消费安全事件。
- 验证：状态表驱动、cross-async correlation、restart recovery、fault injection、diagnostic-only root-cause reconstruction。
- 覆盖：AC-OBS-2/3/4/6/9/10/11/12，TO-A2/A3/A4/A6/A9/A10/A11/A12、TO-R2/R4。

### 顺序与 crash-gap 契约

- `sequence` 仅保证同一进程 emitter 内单调，不伪装 durable 全序；authoritative row 的 `attempt/lease_epoch/state_version/updated_at` 构成跨进程偏序。
- transition event identity 由 `component + entity_kind + opaque entity_id + from/to + state_version/attempt` 确定；重复恢复投影允许 sink 去重。
- post-commit emit 前崩溃属于合法 crash gap。启动 reconcile 从只读 durable 状态发射 `recovery.observed_state`（`replayed=true`），而不是伪造缺失的历史 transition；诊断报告必须明确 `history_complete=false`。
- 可定位性验收依据“最后 durable 状态 + observed recovery + 后续 transition”，不要求 observability 与业务数据库原子提交。

### Snapshot 受限查询契约

- Harness 只允许查询 context/outbox/recovery 表的 `status, attempts, error_code, created_at, updated_at, next_retry_at, lease_expires_at` 与 COUNT/MIN/MAX；Memory 只允许 recall snapshot/receipt/fact-job 的同类状态列。测试用 SQL trace/AST denylist 证明未选择 payload/content/embedding/blob 列。
- 每个 group-by 返回固定枚举全集；最近错误最多 20 项；age 使用当前 wall clock 减最老状态时间并 clamp 到 `>=0`；空集合为 count=0/oldest_age_ms=null。
- 使用只读短事务、busy timeout ≤100ms、总 snapshot deadline ≤250ms；busy/closed/query/serialization 错误返回稳定 `health=degraded` section 与 error code，不抛入业务调用。
- snapshot 与 close 竞态通过 lifecycle snapshot 捕获；关闭后返回 `health=closed` 的稳定 schema。

## Task 3 — Memory 接入、durable correlation 与快照（Slice 3）

- `MemoryManager` 所有 builders/direct init 接受 optional sink/correlation；backend 和 worker 共享一个 observability runtime。
- recall 发射 accepted/started/replayed/degraded/succeeded/released/cleanup/failed；write 发射 receipt replay/rejected-erased/applied；fact job 发射 pending/claimed/recovered/retrying/dead-letter/applied/erased/lost-lease。
- 仅在需要跨进程的 recall snapshot/turn receipt/fact job 行保存 bounded opaque trace/root/parent/operation IDs；迁移为 nullable，新字段不进入约束/索引/业务 predicates。
- Memory `diagnostics_snapshot()` 以 aggregate SQL 返回 queue counts、oldest age、dead-letter/retry/recovery、recall degradation 和 sink stats；绝不 SELECT content/payload/embedding。
- 验证：recall/write/job 全状态矩阵、close/reopen correlation、erasure/lost lease、direct backend compatibility、正文/API key/exception canary。
- 覆盖：AC-OBS-2～12，TO-A2～A12、TO-R2～R5。

### Durable correlation 身份与保留契约

- ID 仅接受 SDK 生成的 32-byte lowercase hex，Host 输入先 hash/重新根化，最大 64 bytes；不持久化用户提供的原文 ID。
- 绑定 tuple 为 `principal_fingerprint + execution_session_id + trace_id`。continuation 仅在 principal 与 owner/session authority 全部相同才继承 root；否则创建新 trace/root 并发射 `correlation.rebased`。
- correlation 列随所属 recall snapshot/receipt/job 同生命周期删除；forget/erasure 清正文时同时清 parent/operation，仅可保留不可反查的 trace hash 与删除计数。不得建立跨 principal correlation 索引或查询。
- 旧 schema rollback：新列 nullable 且旧代码忽略；Host 回滚到旧 wheel pair 前停止新 worker，保留 DB 可读。若迁移需重建表，必须提供 forward-only compatibility test，不承诺降级写入。

## Task 4 — Host 组合、JSONL/ring 与 diagnostic bundle

- 新建 Host adapter 组合一个进程 ring、受控 JSONL 与安全 LoggingSink，并在 MemoryManager 与 Harness runtime 构建时注入同一 root correlation factory。
- 每个用户请求生成 correlation；continuation 继承 trace/root 并生成 parent/operation；禁止用 correlation 代替 principal。
- SDK export 写入用户日志目录的独立有界文件；Rust bundle 只复制明确文件名、最近有界文件与 snapshot JSON，增加每文件/总 bundle 大小上限。
- bundle 前做 canary scan；SDK export 缺失只标记 missing/degraded，不阻断 bundle。
- 验证：Host 冷启动、真实 composition、wheel-only consumer、bundle 内容/上限/缺失/failure 与敏感扫描。
- 覆盖：AC-OBS-2/3/5/6/7/8/9/10/12，TO-A2/A3/A5/A6/A7/A8/A9/A10/A12、TO-R3/R5/R6。

## Task 5 — 跨包契约、发布与 revendor

- 增加 shared golden fixtures 和 cross-wheel conformance，证明 Harness/Memory/Host 对 schema、版本、redaction 与 correlation 解释一致。
- 跑两个 SDK 全量测试、typing、build、wheel contents、exact-wheel smoke；更新版本与 release 文档。
- Host vendor 新 wheel，更新 `pyproject.toml`、`uv.lock`、candidate identity/SHA，并在 vendored-wheel 环境复验。
- 覆盖：AC-OBS-1/8/11，TO-R1/R6。

### Candidate matrix、最终版本与 rollback

- S1/S2 构建仅供 gate 使用的本地 candidate wheels，制品目录以 slice/commit/SHA 隔离且不上传、不进入 resolver cache；禁止覆盖同路径同文件名。Memory candidate 必须安装同 slice Harness candidate wheel，禁止源码 checkout 泄漏。
- S3 冻结最终 Harness 0.4.0 + Memory 0.5.0 wheel。正式仓库、release 与 Host vendor 只接收这一对；Host candidate identity 同时冻结 filename/version/SHA，只允许成对切换。
- Python 3.11/3.12/3.13 跑 schema/import/conformance；当前平台跑完整 suites。wheel contents 必须含 `py.typed`、observability 子包、迁移与 golden fixtures。
- 每阶段失败立即停止在上一对已绿 wheel；Host rollback 同时恢复 vendor pair、`pyproject.toml`、`uv.lock` 与 candidate SHA。JSONL `schema_version=1` 对旧 Host 是未引用旁路文件，可安全忽略。
- S2 migration 前由 Memory SDK 的既有 backup/restore authority 创建带 schema/version/SHA manifest 的 S1 DB 备份，路径由测试 userdata 管理、0600、保留到 S2 验收完成；迁移失败或旧代码兼容测试失败时先停止 worker、校验备份 SHA、原子 rename 恢复并 reopen 验证 S1 exact wheel。该 backup/restore fault matrix 是 S2 required gate；不能只回滚 wheel。成功验收后按既有 retention 清理测试备份。

## Task 6 — 验证、可定位性审计与文档回写

- 冻结 black-box testcase：recall timeout、outbox retry→dead-letter、context degraded、sink failure、fact-job recovery、bundle canary 六类。
- 对每类仅给 correlation ID 和 export，让独立审计者判断故障组件、最后成功阶段、错误码、retry/recovery；错误归类即失败。
- 建立固定 workload 指标基线，断言 latency/count/retry/queue 可聚合并可比较，不宣称业务质量指标等同 Memory 正文质量。
- 运行 critical + affected + full surface smoke；本轮无 UI 变化，真人 UI 门显式不适用。
- 同步三个仓库 `ARCHITECTURE/`、Host `ARCHITECTURE/PROJECT_STATUS.md` 与里程碑。
- 覆盖全部 AC/TO。

## 依赖与执行顺序

S1 执行 Task 1 + Host sink 子集 + candidate A 制品门；S2 执行 Task 2 + Task 3 的 correlation/transition/snapshot/DB backup 子集 + candidate B 制品门；S3 执行 bundle、定位/优化闭环、最终 exact-wheel/release/Host revendor 门。后续 slice 以前一已验收 commit 为 baseline；只有 S3 对外发布。验证准备轨按三个 slice 分别冻结 oracle。

## Challenger finding closure

- `release-slices-not-independent-release-units`：以三条跨仓垂直 release slice、独立 AC/gate/wheel/rollback 闭环。
- `shared-contract-dependency-boundary-unresolved`：明确 Memory 基础依赖 Harness 0.4 的 import-pure observability 子包，不再保留多方案。
- `sink-sync-async-failure-semantics-unspecified`：冻结 non-blocking emit、有界单 worker、overflow、flush/close/竞态语义。
- `durable-correlation-retention-and-identity-policy-missing`：冻结格式、身份 tuple、rebase、erasure 与 retention。
- `event-ordering-and-crash-gap-contract-missing`：定义进程 sequence、durable 偏序、recovery observed-state 与 history completeness。
- `diagnostics-snapshot-query-boundaries-not-executable`：冻结允许列、上限、deadline、busy/close 降级 schema。
- `cross-repo-exact-wheel-release-matrix-and-rollback-missing`：冻结版本组合、Python matrix、成对切换、DB 与 Host rollback。
