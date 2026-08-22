# 验收标准：Harness 与 Memory SDK Observability 完善

## 范围

### 包含

- 为 Harness SDK 与 Memory SDK 建立统一、版本化、可兼容演进的结构化事件协议。
- 支持 Host 注入 observability sink，并提供安全默认实现与 sink 故障隔离。
- 将 Host 请求、Harness run/attempt/tool/context 与 Memory recall/write/outbox/recovery 串入同一 correlation 链。
- 为 outbox、recall、context、recovery 的关键状态转换产生可排序、可聚合的安全事件。
- 提供只读、有界、隐私安全的 `diagnostics_snapshot()`。
- 适配 JSONL 文件、移动端 ring buffer 与 diagnostic bundle。
- 用日志契约测试保证 Memory 正文、prompt/response 正文和 API key 等敏感值不被输出。
- 让一次故障可仅凭 correlation ID、结构化事件与健康快照定位组件、阶段、错误类别和恢复结果，并支持基于聚合指标做优化前后对比。

### 明确不包含

- 不引入远程 telemetry collector、自动上传或新的云端依赖。
- 不把 observability 事件作为 workflow、outbox、Memory 或 Context 的 durable authority。
- 不记录 Memory 正文、完整 prompt/response、附件正文、工具正文或认证材料。
- 不改变现有业务状态机、重试、恢复和一致性语义。
- 不在本轮新增用户可见的诊断 UI。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|---|---|---|---|
| AC-OBS-1 | 统一事件协议 | 两个 SDK 发出的事件使用同一版本化 envelope；事件名、时间、severity、component、operation、outcome、correlation 与 attributes 类型受 schema/构造器约束，未知扩展字段可安全忽略 | 必须 |
| AC-OBS-2 | Host sink 注入 | Host 可在两个 SDK 的公开 composition API 注入一个或多个 sink；未注入时业务可运行；sink 抛错、阻塞保护或序列化失败只增加观测丢弃/错误计数，不改变业务结果 | 必须 |
| AC-OBS-3 | 全链路 correlation | Host 可注入或由入口生成 correlation ID；Harness root/continuation/attempt/tool/context 与 Memory recall/write/outbox/recovery 均保留 trace/root/parent/operation identity，跨异步与恢复边界仍可关联 | 必须 |
| AC-OBS-4 | 状态转换事件 | outbox、recall、context、recovery 的关键 accepted/started/succeeded/failed/retrying/degraded/terminal 转换产生有序事件；重复/恢复重放可辨认，非法转换有稳定错误码且不泄漏载荷 | 必须 |
| AC-OBS-5 | 隐私脱敏 | default-deny redactor 只允许契约字段；Memory 正文、prompt/response 正文、API key、Authorization、cookie、token、密码、附件正文及异常中夹带的 canary 在所有 sink、snapshot 和 bundle 中均不可出现 | 必须 |
| AC-OBS-6 | 健康快照 | 两个 SDK 均提供稳定、只读、有界的 `diagnostics_snapshot()`，包含 schema/SDK 版本、组件健康、队列统计、最老等待时长、最近错误码计数、恢复/降级状态与 sink 丢弃统计，不含业务正文 | 必须 |
| AC-OBS-7 | 存储与导出适配 | JSONL sink 支持有效逐行 JSON、有界轮转/保留和安全文件权限；移动端 ring buffer 有固定容量和确定溢出策略；diagnostic bundle 只导出脱敏 snapshot/事件并遵守大小上限 | 必须 |
| AC-OBS-8 | 自动化日志契约 | 自动化测试覆盖 schema、correlation、状态转换、sink 故障隔离、快照、容量/轮转、并发安全与 bundle；敏感 canary 扫描明确断言不得输出 Memory 正文/API key | 必须 |
| AC-OBS-9 | Bug 可定位性 | 给定一次失败的 correlation ID，仅使用结构化事件和 snapshot 即可确定故障组件、最后成功阶段、失败转换、稳定错误码、attempt/retry、恢复结果及关联 run/outbox identity | 必须 |
| AC-OBS-10 | 优化闭环 | 事件和 snapshot 提供可聚合的 latency、error、retry、queue、recall/context 质量代理指标，并能用确定性测试建立基线、比较优化前后差异 | 必须 |
| AC-OBS-11 | 诊断完整性 | 关键链路异常不得只有自由文本；必须具备稳定 event type、stage/operation、outcome、error code、duration 和 correlation 字段，缺失时契约测试失败 | 必须 |
| AC-OBS-12 | 隐私下可诊断 | 在不输出正文的前提下，安全元数据足以区分空输入、召回无结果、预算裁剪、sink 故障、outbox 卡住、重试耗尽与 recovery 失败等根因 | 必须 |

## 非功能 / 边界

- **失败隔离**：observability 永远是旁路；sink/快照失败不能改变 SDK 对外结果或事务提交。
- **有界性**：事件 attributes、错误摘要、ring buffer、JSONL 文件数量/大小、snapshot 最近错误集合与 bundle 总量均有硬上限。
- **并发安全**：并发 emit、snapshot 与关闭不产生损坏 JSON、死锁或业务异常；关闭/重复关闭行为明确。
- **性能**：事件路径不得读取或复制 Memory 正文来“先记录再脱敏”；计时使用 monotonic duration，排序时间使用 UTC epoch。
- **兼容性**：新参数为可选；现有 Host 不注入 sink 时保持源兼容；schema 版本与未知事件的处理规则固定。
- **身份边界**：correlation ID 是诊断标识而非授权凭证，不能替代 principal/owner/session 校验。
- **默认开启**：已完成的安全内存观测能力默认开启；需要文件路径或 bundle 请求的适配由 Host 显式配置。
- **证据纪律**：测试原始日志和 bundle 写入 `.local-test-evidence/2026-08-23/...`，Git 只保存小型结论、命令、状态、索引和 SHA-256。

## 安全诊断字段基线

允许的内容代理包括 `content_length`、`token_count`、不可逆/进程加盐 fingerprint、`candidate_count`、`selected_count`、`drop_reason`、`budget_before/after`、`duration_ms`、`retry_count`、队列计数和稳定错误码。禁止把原始输入包装进 `message`、exception、repr、attributes 或 fallback 字段绕过 redactor。

## 适用性声明

- `input_sensitive=false`：本轮验收对象是确定性的 SDK 事件/存储契约，不以 LLM 回答语义质量判断。
- `llm_payload_driven=false`：结构化 observability 不由 LLM 生成 payload 驱动；provider/tool 正文仅作为必须被拒绝的敏感输入 canary。
- `stateful_init=true`：Host sink 装配、JSONL/ring buffer 生命周期、durable outbox 与 recovery 跨进程恢复需要冷启动和重启测试。
- UI 真人测试不适用：本轮无新增或修改用户可见 UI；库/管道通过自动化集成测试与 diagnostic bundle 黑盒检查验收。

## 测试义务矩阵

| obligation_id | type | ac_id | risk | min_decisive_test | required_reason |
|---|---|---|---|---|---|
| TO-A1 | delivery | AC-OBS-1 | — | 两个 SDK 对同一 envelope schema 的契约/兼容测试 | 直接证明协议统一 |
| TO-A2 | delivery | AC-OBS-2 | — | 注入 recording/composite/failing sink 并断言业务结果不变 | 证明 Host 扩展点与故障隔离 |
| TO-A3 | delivery | AC-OBS-3 | — | 从 Host 入口运行 Harness→Memory 链并按 correlation 重建时间线 | 证明跨 SDK 关联 |
| TO-A4 | delivery | AC-OBS-4 | — | outbox/recall/context/recovery 正常、失败、重试、恢复状态表驱动测试 | 证明状态转换覆盖 |
| TO-A5 | delivery | AC-OBS-5 | — | 多位置敏感 canary 注入后扫描事件、snapshot、JSONL、ring 与 bundle | 证明隐私不泄漏 |
| TO-A6 | delivery | AC-OBS-6 | — | 空闲、运行、降级、失败和关闭状态 snapshot schema 测试 | 证明快照完整、有界、只读 |
| TO-A7 | delivery | AC-OBS-7 | — | JSONL 轮转/权限、ring 溢出、bundle 大小与导出内容测试 | 证明三类适配可用 |
| TO-A8 | delivery | AC-OBS-8 | — | 聚合日志契约测试套件及 canary deny assertions | 防止后续调用点回归 |
| TO-A9 | delivery | AC-OBS-9 | — | 注入 recall/outbox/recovery 故障，仅从导出诊断重建根因报告 | 证明实际可定位 Bug |
| TO-A10 | delivery | AC-OBS-10 | — | 对固定 workload 比较两组 latency/queue/retry 聚合 | 证明数据可用于优化 |
| TO-A11 | delivery | AC-OBS-11 | — | 对关键异常事件运行 required-fields validator | 防止自由文本日志成为唯一证据 |
| TO-A12 | delivery | AC-OBS-12 | — | 六类无正文故障产生不同稳定 code/metric | 证明隐私与诊断性同时成立 |
| TO-R1 | change-risk | AC-OBS-1, AC-OBS-3 | CONTRACT-DRIFT | Harness/Memory/Host 跨包 schema parity 测试 | 多包发布易产生字段漂移 |
| TO-R2 | change-risk | AC-OBS-2, AC-OBS-6 | OBSERVABILITY-BREAKS-BUSINESS | sink/snapshot fault injection 与事务结果对比 | 旁路代码不得影响 authority |
| TO-R3 | change-risk | AC-OBS-5, AC-OBS-7 | SECRET-EXFILTRATION | exception/repr/nested container/unknown key 对抗扫描 | 脱敏绕过是本轮最高风险 |
| TO-R4 | change-risk | AC-OBS-4, AC-OBS-9 | RECOVERY-MISDIAGNOSIS | 崩溃后重启、重复 claim/settle 与 correlation 连续性测试 | 恢复链最易丢失因果关系 |
| TO-R5 | change-risk | AC-OBS-7 | UNBOUNDED-RESOURCE | 高事件量下文件、ring、snapshot 和 bundle 上限测试 | 防止移动端内存和本地磁盘无界增长 |
| TO-R6 | change-risk | AC-OBS-1, AC-OBS-2 | HOST-REGRESSION | simple_harness Host 启动及 affected/full surface smoke | 公共 composition 改动影响现有消费者 |

## Assurance contract 摘要

- Profile：`standard`。
- 受保护资产：Memory/用户正文、provider 与 Host 凭据、业务状态和事务结果、设备存储资源、诊断因果链。
- 可信假设：本地 OS/开发者账户可信；SDK 与 Host 进程内代码可信；调用方按公开 API 注入 sink。
- 范围内失败：字段漂移、correlation 丢失、状态事件遗漏/乱序、sink 故障、敏感数据进入异常或 fallback、无界存储、恢复后因果链断裂。
- 范围外：宿主 OS 被攻陷、恶意修改 SDK 二进制、远程 telemetry/collector 的安全与可用性。
- 最大可接受影响：观测可丢弃并明确计数或降级；不得改变业务结果、阻塞恢复、泄漏正文/凭据或造成无界资源增长。

## 分片与完成定义

实施拆为三个垂直 slice：①共享协议与隐私；②Harness 接入；③Memory 与 Host/存储适配。每个 slice 独立通过相关自动化和 affected-surface smoke；最终完成要求 AC-OBS-1～12、全部 required obligation、跨 SDK 集成、冷启动/恢复、full-surface smoke、独立审计和架构事实源更新全部通过。
