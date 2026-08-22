# SDK Observability 黑盒验收用例

日期：2026-08-23  
状态：verification preparation only  
事实源：`acceptance.md`、`assurance-contract.json`、`plan.md`

## 1. 范围与判定原则

本用例集只通过公开 SDK composition API、公开 `diagnostics_snapshot()`、业务结果、持久化后的可观察行为、JSONL/ring/bundle 导出来验收。不得读取实现代码、私有对象、内部数据库正文列或 diff，也不得把事件流当作 workflow、outbox、Memory、Context 的 authority。

统一判定规则：

- 两 SDK 使用同一 `schema_version=1` envelope 语义；未知扩展字段可忽略，契约字段缺失、类型错误或关键异常仅有自由文本均失败。
- observability 是旁路。任何 sink、序列化、snapshot、export 或 bundle 故障不得改变业务返回、提交、幂等、重试、恢复或授权结果。
- 全部敏感 canary 在事件、snapshot、JSONL、ring、bundle、错误摘要和测试报告中必须零命中；只允许长度、计数、枚举、稳定错误码和不可逆 fingerprint 等安全代理。
- 资源上限必须以配置/公开契约给出的硬界限判定；不得以“测试期间未耗尽资源”代替边界证明。
- 原始证据只写入 `.local-test-evidence/2026-08-23/sdk-observability/<run-id>/`。Git 中只保留结论、命令、case/run ID、相对索引和 SHA-256。
- UI 真人测试不适用：本轮没有用户可见 UI 变化。

## 2. 证据与执行约定

每个 case 保存：`result.json`（PASS/FAIL/BLOCKED、candidate identity、环境）、`commands.txt`、脱敏后的证据索引、每个原始文件 SHA-256。原始事件、JSONL、bundle、数据库副本、长日志均留在 `.local-test-evidence`。

候选制品必须记录 Harness wheel filename/version/SHA、Memory wheel filename/version/SHA、Host candidate identity、slice、commit identity 与 Python/platform。Memory 必须安装同 slice Harness wheel；不得从源码 checkout 隐式导入。

严重失败条件：任一 canary 泄漏；业务结果因 observability 改变；资源无界；跨身份错误继承 correlation；恢复被阻塞；关键异常不能仅凭导出诊断归因。上述任一失败立即停止该 candidate 晋级。

## 3. Slice 1 — 安全协议与本地 sinks

### BB-S1-001 Schema golden 与跨包 parity

适用：AC-OBS-1/8/11；TO-A1/A8/A11、TO-R1。

用 Harness、Memory 公开 builder 分别产生同一语义的 lifecycle/success/failure 事件，并以 `fixtures.md` 的 canonical golden 校验字段名、类型、枚举、UTC event time、monotonic `duration_ms`、severity/component/operation/outcome/correlation/attributes。对 envelope 增加未知扩展字段后由 Harness、Memory、Host consumer 读取。

通过条件：canonical 结果与 golden 一致；两个 SDK 对共同字段解释一致；未知扩展字段被安全忽略；关键 failure 缺 event type、stage/operation、outcome、稳定 error code、duration 或 correlation 时 validator 明确拒绝。

### BB-S1-002 可选注入、Noop 与 composite 独立性

适用：AC-OBS-2；TO-A2、TO-R2/R6。

对两个 SDK 分别运行：未注入 sink、单 recording sink、两个 child 的 composite、一个成功 child 加一个 failing child。比较相同确定性业务输入的返回值、持久化结果和公开状态。

通过条件：未注入可正常运行；成功 child 收到事件；失败 child 不回滚成功 child；业务结果逐项相同；snapshot 中 emitted/dropped/sink_errors 与 accepted/failed child 计数吻合。

### BB-S1-003 Sink 故障、阻塞、序列化与重入

适用：AC-OBS-2/8；TO-A2/A8、TO-R2。

逐项注入 throw、超过 flush/close timeout 的阻塞、不可序列化输入、worker recursion/reentrant emit、队列满、emit/close race、emit-after-close 和重复 close。并行执行业务操作并记录耗时与结果。

通过条件：同步 `emit()` 不执行阻塞 I/O；无死锁和业务异常；固定 worker/固定队列，无额外线程膨胀；drop-newest、overflow、sink error、close timeout、reentrant drop、after-close drop 均有稳定计数；close 幂等且默认等待不超过 1 秒契约上限。

### BB-S1-004 Default-deny canary 对抗矩阵

适用：AC-OBS-5/8；TO-A5/A8、TO-R3。

将每类唯一 canary 放入 content/body/message/prompt/response/query/token/key/authorization/cookie/password/exception/args/result、未知 key、嵌套 dict/list/tuple、异常文本与 repr、超长 key/value、fallback 和序列化失败路径。扫描 recording、ring、JSONL、snapshot、logging adapter 输出和 bundle（bundle 在 S3 重跑）。

通过条件：所有原始 canary 零命中；未知字段默认拒绝；没有把原始值移入 `message`、exception、repr 或 fallback；允许的 length/count/fingerprint/reason/code 仍可用于区分场景。

### BB-S1-005 JSONL 正确性、权限与轮转

适用：AC-OBS-7/8；TO-A7/A8、TO-R3/R5。

写入超过 `max_bytes` 与 `max_files` 的事件量，覆盖并发 emit、flush、close、进程异常结束、非法/符号链接路径和无法写入路径。逐行解析保留文件。

通过条件：每个完整行都是独立有效 JSON；文件为 regular file、权限 0600、拒绝 symlink；保留文件数和单文件大小不超硬限（允许契约明确的单条原子写边界）；轮转顺序确定；路径/权限/写入失败只降级并计数，不影响业务。

### BB-S1-006 Ring 固定容量与确定溢出

适用：AC-OBS-7/8；TO-A7/A8、TO-R5。

容量 N 下顺序写 N+K 条带 sequence 的事件，并发 emit/snapshot/close；分别读取 ring export 与 stats。

通过条件：最多 N 条；保留集合与声明的确定策略一致；无部分/损坏事件；overflow 精确可解释；读取只读且不改变 ring；并发无死锁。

### BB-S1-007 Slice 1 candidate 与 critical/affected smoke

适用：S1 全部 MUST AC；TO-R1/R6。

在 Python 3.11/3.12/3.13 跑 schema/import/conformance；当前平台从两个 exact wheels 启动 wheel-only consumer；验证 wheel 含公开 observability 子包、typing 与 golden；Host 用成对 candidate 注入 JSONL+ring；执行 critical 与 affected smoke。

通过条件：import observability 不拉起 Harness runtime/provider/tool/sqlite 副作用；wheel pair identity/SHA 固定；无源码泄漏；critical 与 affected 全绿。S1 不要求细粒度 Memory 状态事件、跨重启完整 correlation 或 bundle 最终门。

## 4. Slice 2 — Correlation、状态与快照

### BB-S2-001 Host→Harness→Memory correlation 重建

适用：AC-OBS-3/11；TO-A3/A11、TO-R1。

由 Host 接受一个外部 correlation 输入，执行 root、continuation、provider attempt、tool、context、Memory recall/write/outbox；跨 async boundary 后仅按 correlation 字段重建时间线。

通过条件：SDK 生成 ID 为 32-byte lowercase hex；Host 原始输入不原样持久化而被 hash/重新根化且最长 64 bytes；trace/root/parent/operation identity 可连接全部阶段；同 principal+owner/session continuation 继承 root；不同身份/session 创建新 trace/root 并出现 `correlation.rebased`；correlation 不替代授权判定。

### BB-S2-002 Harness 状态转换表

适用：AC-OBS-4/11/12；TO-A4/A11/A12。

以 fixture 中 Harness 状态表运行 run、attempt、tool authorize/invoke/settle、context prepare/stage/consume/abandon、outbox claim/apply/retry/dead-letter、recovery blocker/resolution 的正常、失败、重试、降级和 terminal 路径；加入重复与非法转换。

通过条件：accepted/started/succeeded/failed/retrying/degraded/terminal 的适用转换有序且字段完整；非法转换有稳定 code；重复/replay 可辨认；同进程 sequence 单调，但报告不声称跨进程全序。

### BB-S2-003 Crash gap、冷重启与 durable 偏序

适用：AC-OBS-3/4/9；TO-A3/A4/A9、TO-R4。

在 authoritative commit 后、event emit 前制造 crash；冷启动新进程并触发 reconcile。另测重复 claim/settle、lease epoch/state version 增长、恢复后继续 transition。

通过条件：重启发出 `recovery.observed_state`、`replayed=true`；不伪造缺失历史；诊断明确 `history_complete=false`；以 attempt/lease_epoch/state_version/updated_at 建立偏序；后续状态与同一 durable identity/correlation 可关联，恢复业务结果正确。

### BB-S2-004 Snapshot 五态、schema 与只读性

适用：AC-OBS-6；TO-A6、TO-R2/R5。

在 idle、running、degraded、failed、closed 五态调用 Harness 与本 slice 可用的 Memory snapshot；调用前后比较业务状态与队列。重复 snapshot 并尝试修改返回对象。

通过条件：稳定 schema 含 schema/SDK version、health、组件/队列计数、oldest age、最近错误码、recovery/degradation、sink stats；空集合 count=0 且 oldest=null；返回对象修改不影响后续 snapshot；无正文；关闭后稳定 `health=closed`。

### BB-S2-005 Snapshot bounds、deadline 与 fault isolation

适用：AC-OBS-2/6；TO-A2/A6、TO-R2/R5。

构造超过 20 类最近错误、大量队列项、未来时间戳、DB busy、closed、query fault、serialization fault、snapshot/close race。用公开 SQL trace/审计 hook（若产品提供）或黑盒 canary 列证明 payload/content/embedding/blob 未被读取。

通过条件：最近错误最多 20；group-by 返回固定枚举全集；age clamp≥0；busy timeout≤100ms、总 deadline≤250ms；错误返回 degraded section 与稳定 code，不抛给业务；snapshot 大小有硬限；正文 canary 不进入输出。若无公开可用的查询审计接口，列级“不读取”证明标为 BLOCKED，不得用源码检查替代。

### BB-S2-006 六类无正文根因区分

适用：AC-OBS-9/12；TO-A9/A12。

分别制造空输入、recall 无结果、context 预算裁剪、sink 故障、outbox 卡住、重试耗尽/recovery 失败（后两者各保留独立 scenario）。仅向审计者提供 correlation ID、结构化导出和 snapshot。

通过条件：审计者能给出不同稳定 code/metric，并确定组件、最后成功阶段、失败 transition、attempt/retry、recovery 结果和 run/outbox identity；不得依赖正文或环境日志猜测。

### BB-S2-007 S1 DB backup、迁移与 rollback

适用：AC-OBS-3/4；TO-R4/R6。

用 S1 exact wheels 创建状态与带 schema/version/SHA manifest 的 0600 backup；升级 S2 candidate，覆盖成功迁移、迁移失败、旧代码读取兼容。失败路径停止 worker、验证 backup SHA、原子恢复并用 S1 exact pair reopen。

通过条件：nullable 新字段不破坏旧读取；失败后业务状态完整且 S1 可 reopen；恢复流程不依赖 observability authority；backup 原始文件仅在 `.local-test-evidence`，验收完成后按既有 retention 处理。

### BB-S2-008 Slice 2 candidate affected smoke

适用：S2 全部 MUST AC；TO-R1/R2/R4/R6。

从已绿 S1 candidate 升级到 S2 exact pair，运行 correlation、transition、snapshot、restart/rollback gates 与 Host affected smoke。

通过条件：S1 frozen oracle 不回写；新增能力默认开启；业务结果 parity、跨包 schema parity 和恢复门全绿。bundle/最终优化门留给 S3。

## 5. Slice 3 — Memory、bundle 与优化闭环

### BB-S3-001 Memory recall/write/job 全状态矩阵

适用：AC-OBS-3/4/11/12；TO-A3/A4/A11/A12、TO-R4。

覆盖 recall accepted/started/replayed/degraded/succeeded/released/cleanup/failed，write receipt replay/rejected-erased/applied，fact job pending/claimed/recovered/retrying/dead-letter/applied/erased/lost-lease，以及 direct backend 和所有公开 builders。

通过条件：状态、identity、attempt/replay/lease 与稳定 code 完整；无 sink 配置使用 Noop；direct backend 保持兼容；事件不改变业务状态机。

### BB-S3-002 Durable correlation、close/reopen、erasure 与身份边界

适用：AC-OBS-3/5/9；TO-A3/A5/A9、TO-R3/R4。

创建 recall snapshot/receipt/job 后 close/reopen；跨进程继续处理。执行 forget/erasure、lost lease、不同 principal/session continuation，并尝试超长或携带 canary 的 Host correlation。

通过条件：允许的 opaque identity 跨重启连续；原始 Host ID 不保存；绑定 tuple 生效且跨身份 rebase；erasure 同时清 parent/operation，只保留不可反查 trace hash 与删除计数；无跨 principal 查询/关联输出；correlation 随所属实体生命周期删除。

### BB-S3-003 Diagnostic bundle 内容、缺失、故障与上限

适用：AC-OBS-5/7/8；TO-A5/A7/A8、TO-R3/R5。

由 Host 显式请求 bundle，覆盖正常、SDK export 缺失、snapshot degraded、复制失败、单文件超限、总量超限、大量 rotated JSONL、ring overflow 和 bundle 前 canary scan。

通过条件：只包含明确允许且脱敏的 snapshot/事件文件；缺失标 missing/degraded 而不阻断其余 bundle；每文件与总量均不超硬限；无任意路径收集；canary 零命中；bundle 故障不影响业务。原始 bundle 留在 `.local-test-evidence`。

### BB-S3-004 冷启动 Host 真实 composition 与 wheel-only consumer

适用：AC-OBS-2/3/6/7；TO-A2/A3/A6/A7、TO-R6。

清洁 userdata 启动 Host，只使用最终 exact wheel pair；执行请求、continuation、Memory job、关闭和冷重启；检查 ring、JSONL、snapshot、bundle 和业务结果。

通过条件：Host 向两个 SDK 注入同一 root correlation factory 和 composite sink；安全内存观测默认开启；文件/bundle 仅在 Host 显式配置或请求时产生；冷启动无 sink 也可运行；重启关联与资源上限满足契约。

### BB-S3-005 Diagnostic-only 根因盲审

适用：AC-OBS-9/11/12；TO-A9/A11/A12、TO-R4。

冻结六类：recall timeout、outbox retry→dead-letter、context degraded、sink failure、fact-job recovery、bundle canary denial。独立审计者只收到 correlation ID、export 与 snapshot，不收到 scenario 名、正文或实现日志。

通过条件：每类正确指出故障组件、最后成功阶段、失败 transition、稳定 code、attempt/retry、恢复结果、关联 run/outbox/job identity；bundle canary denial 能与业务失败区分；错误归类或只能靠自由文本即 FAIL。

### BB-S3-006 固定 workload 指标基线与优化比较

适用：AC-OBS-10；TO-A10。

以固定种子、固定时钟/延迟注入和同一 workload 生成 baseline A 与 controlled variant B；聚合 latency、error、retry、queue、recall/context quality proxy。重复运行验证确定性容差。

通过条件：事件与 snapshot 可独立计算 count/rate/percentile 或契约指定聚合；预设 variant 只改变目标指标且方向与幅度满足 fixture；能比较前后，不把代理指标表述为正文质量。

### BB-S3-007 高负载并发与全资源上限

适用：AC-OBS-6/7/8；TO-A6/A7/A8、TO-R5。

高事件量下并发 emit/snapshot/export/flush/close，持续越过 ring、worker queue、JSONL retention、recent errors 和 bundle 限制。

通过条件：无损坏 JSON、死锁、未捕获业务异常或线程/文件无界增长；每个边界有确定 drop/overflow/degraded 计数；业务吞吐完成且结果与 Noop 对照一致。

### BB-S3-008 Final exact-wheel、critical/affected/full smoke

适用：AC-OBS-1～12；全部 TO。

冻结 Harness 0.4.0 + Memory 0.5.0 filename/version/SHA，Host 成对 revendor 并固定 lock/candidate identity。运行：critical smoke（隐私、旁路、authority、boundedness）、affected smoke（SDK composition、runtime/context/outbox/recovery、Host diagnostics）、full surface smoke（两个 SDK 全量、typing/build/wheel contents、Host 全表面）。

通过条件：所有 required case 全绿；Python 3.11/3.12/3.13 schema/import/conformance 全绿，当前平台完整 suites 全绿；只允许成对切换；无未解释差异。正式发布、提交、ARCHITECTURE 回写不属于本 preparation 任务，但正式完成门必须包含它们。

## 6. Smoke 分层

| 层级 | 最小集合 | 运行时机 | 失败影响 |
|---|---|---|---|
| critical | BB-S1-001/002/003/004、BB-S2-003/005、BB-S3-002/003 | 每个 candidate、任何 redaction/sink/correlation/storage 变更 | 立即停止晋级 |
| affected | 当前 slice 全部 case + 前序 critical；Host composition 与 exact-wheel consumer | 每个 slice gate | 回到上一已绿 pair |
| full | S1～S3 全部、两个 SDK 全套、Host full surface、冷重启、rollback、blind audit | S3 final gate | 不得发布/revendor |

## 7. AC / TO 覆盖矩阵

| AC / TO | 决定性 case |
|---|---|
| AC-OBS-1 / TO-A1 | S1-001, S1-007, S3-008 |
| AC-OBS-2 / TO-A2 | S1-002/003, S2-005, S3-004 |
| AC-OBS-3 / TO-A3 | S2-001/003, S3-001/002/004 |
| AC-OBS-4 / TO-A4 | S2-002/003, S3-001 |
| AC-OBS-5 / TO-A5 | S1-004, S3-002/003 |
| AC-OBS-6 / TO-A6 | S2-004/005, S3-003/004/007 |
| AC-OBS-7 / TO-A7 | S1-005/006, S3-003/004/007 |
| AC-OBS-8 / TO-A8 | S1-001/003/004/005/006, S3-003/007/008 |
| AC-OBS-9 / TO-A9 | S2-003/006, S3-002/005 |
| AC-OBS-10 / TO-A10 | S3-006/008 |
| AC-OBS-11 / TO-A11 | S1-001, S2-002, S3-001/005 |
| AC-OBS-12 / TO-A12 | S2-002/006, S3-001/005 |
| TO-R1 CONTRACT-DRIFT | S1-001/007, S2-001/008, S3-008 |
| TO-R2 OBSERVABILITY-BREAKS-BUSINESS | S1-002/003, S2-004/005/008, S3-007 |
| TO-R3 SECRET-EXFILTRATION | S1-004/005, S3-002/003 |
| TO-R4 RECOVERY-MISDIAGNOSIS | S2-003/006/007/008, S3-001/002/005 |
| TO-R5 UNBOUNDED-RESOURCE | S1-005/006, S2-004/005, S3-003/007 |
| TO-R6 HOST-REGRESSION | S1-002/007, S2-007/008, S3-004/008 |

覆盖结论：AC-OBS-1～12、TO-A1～A12、TO-R1～R6 均至少有一个直接黑盒决定性 case，并在 final gate 由 BB-S3-008 汇总。
