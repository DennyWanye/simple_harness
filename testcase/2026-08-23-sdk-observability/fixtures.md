# SDK Observability 黑盒 fixture specification

日期：2026-08-23  
用途：为 `testcases.md` 提供稳定输入、oracle、故障注入和证据布局；本文件不定义实现 API。

## 1. Fixture 总则

- 每次运行生成唯一 `run_id`，证据根为 `.local-test-evidence/2026-08-23/sdk-observability/<run_id>/`。
- 使用确定性 clock：UTC event clock 与 monotonic duration clock 分离；可注入倒退 wall clock 验证 age clamp，但 monotonic duration 不倒退。
- 使用固定 seed、固定 workload 与 opaque entity IDs。除专门的 Host-input case 外，合法 correlation fixture 为 64 个 lowercase hex 字符。
- 业务 oracle 独立于 observability：每个场景保存 Noop baseline 的返回、状态摘要与 durable identity，再与 faulted sink/snapshot/export 运行比较。
- 任何 fixture 中的正文与凭据仅使用虚构 canary，不使用真实账号、token、cookie 或用户内容。

## 2. Canonical envelope golden

Golden 只固定 wire 语义，不固定实现类名。canonical serialization 必须含：

```json
{
  "schema_version": 1,
  "event_name": "harness.run.succeeded",
  "event_time": "<UTC epoch or canonical UTC representation>",
  "sequence": 7,
  "severity": "info",
  "component": "harness",
  "operation": "run",
  "outcome": "succeeded",
  "correlation": {
    "trace_id": "1111111111111111111111111111111111111111111111111111111111111111",
    "root_id": "2222222222222222222222222222222222222222222222222222222222222222",
    "parent_id": "3333333333333333333333333333333333333333333333333333333333333333",
    "operation_id": "4444444444444444444444444444444444444444444444444444444444444444"
  },
  "duration_ms": 12,
  "attributes": {
    "attempt": 1,
    "replayed": false
  }
}
```

Oracle variants：success、failure（带稳定 `error_code`）、degraded、retrying、terminal；Harness 与 Memory 各产生一份。Forward-compatible variant 在顶层和扩展容器加入 consumer 未知字段，预期忽略且已知字段不变。Invalid variants 分别删除 event_name、operation、outcome、duration、correlation、failure error_code，或提供错误类型，预期稳定拒绝。

Golden 不要求所有 optional identity 非空；要求相同缺省/null 规则在 Harness、Memory、Host 一致。`sequence` 只作为单 emitter 进程内单调 oracle。

## 3. Canary corpus

每个 canary 使用随机 suffix，防止扫描误匹配；示例前缀：

| 类别 | Canary 前缀 | 注入位置 |
|---|---|---|
| Memory/用户正文 | `CANARY_MEMORY_BODY_` | content/body/query/result、nested containers |
| prompt | `CANARY_PROMPT_` | prompt/message/args/exception |
| provider response | `CANARY_RESPONSE_` | response/result/repr/fallback |
| API key | `sk-CANARY_API_KEY_` | key/token/unknown key/exception |
| Authorization | `Bearer CANARY_AUTH_` | authorization/header/message |
| cookie/session | `CANARY_COOKIE_` | cookie/nested list/repr |
| password | `CANARY_PASSWORD_` | password/unknown key/serialization error |
| attachment/tool body | `CANARY_ATTACHMENT_`, `CANARY_TOOL_BODY_` | attachment/tool result/fallback |
| Host correlation input | `CANARY_EXTERNAL_CORRELATION_` | ingress correlation only |

扫描对象：recording export、ring export、全部 JSONL、snapshot、logging output、bundle 展开内容、错误摘要、result/report。扫描按原始 bytes 与 UTF-8 text 两次执行，并检查 JSON escaped 表示。通过标准为全部零命中；fixture 自身 canary corpus 文件不得复制进 bundle 或产品输出扫描目录。

## 4. Sink fault fixtures

| ID | 行为 | Oracle |
|---|---|---|
| SF-THROW | child 每次 emit 抛固定异常 | sink_errors 增长，业务 parity |
| SF-SERIALIZE | attributes 触发不可序列化输入 | dropped/serialization code，canary 零泄漏 |
| SF-BLOCK | worker 超过 flush/close 1s | timeout 计数，无业务阻塞 |
| SF-FULL | capacity N，写入 N+K | drop-newest，overflow=K（扣除并发允许的已消费量后由 accepted ledger 核对） |
| SF-REENTRANT | child 尝试回调同 emitter | recursion drop，无死锁 |
| SF-PARTIAL | composite 一 child 成功、一 child 失败 | 成功 child 保留，accepted/failed child 可解释 |
| SF-CLOSE-RACE | emit/snapshot/close 并发 | lifecycle 归约为 open/closing/closed，无损坏 |
| SF-AFTER-CLOSE | close 后 emit，随后重复 close | emit 被丢弃计数，close 幂等 |

每个 sink fault 与 Noop baseline 使用同一业务输入、固定 seed 和状态摘要比较。

## 5. 状态转换 fixtures

Harness fixture：run start/terminal；provider attempt success/failure/retry；tool authorize/invoke/settle；context prepare/stage/consume/abandon/degraded；outbox claim/apply/retry/dead-letter；recovery blocker/resolution。

Memory fixture：recall accepted/started/replayed/degraded/succeeded/released/cleanup/failed；write receipt replay/rejected-erased/applied；fact job pending/claimed/recovered/retrying/dead-letter/applied/erased/lost-lease。

每行状态 case 定义：`entity_kind`、opaque entity ID、from/to、expected event name、outcome、error code（适用时）、attempt、lease_epoch、state_version、replayed、terminal、预期后继集合。非法边输入预期稳定错误码且 attributes 中无载荷。重复恢复投影预期 identity tuple 相同、`replayed=true`，允许 consumer 去重。

Crash-gap fixture 在 durable state commit 后终止进程，禁止补写历史事件；重启 oracle 为 `recovery.observed_state`、`replayed=true`、`history_complete=false` 与之后的合法 transition。

## 6. Correlation fixtures

| 场景 | 输入 | Oracle |
|---|---|---|
| root | 无 Host ID | 生成合法 trace/root/operation |
| external | 可读 Host canary ID | 输出不含原文；hash/重新根化 |
| continuation-same-authority | 相同 principal fingerprint、owner/session | trace/root 继承，parent/operation 更新 |
| continuation-new-principal | principal 改变 | 新 trace/root + correlation.rebased |
| continuation-new-session | owner/session authority 改变 | 新 trace/root + correlation.rebased |
| async | task/worker boundary | identity 连续 |
| restart | close/reopen + durable job | bounded opaque identity 连续 |
| erasure | forget/erased job | parent/operation 清除；仅不可反查 trace hash/删除计数可留 |
| malformed/oversized | 非 hex、超过 64 bytes | 拒绝原文持久化，安全重新根化或稳定拒绝 |

盲审时间线采用节点表：event time、sequence、component、operation、outcome、identity、attempt/state_version；跨进程排序只使用 durable 偏序，不按 sequence 建立伪全序。

## 7. Snapshot fixtures

状态：idle、running、degraded、failed、closed。数据规模：empty、one、超过 recent-error 20 项、超过所有公开返回集合上限。时间：正常、未来 created_at、极老 created_at。故障：busy、closed、query error、serialization error、snapshot-close race。

Snapshot oracle：

- schema/SDK version、health、component health、queue counts、oldest age、recent error-code counts、recovery/degradation、sink stats 均存在且类型稳定；
- 固定枚举 group 即使 count=0 仍存在；empty oldest 为 null；未来时间 age clamp 为 0；recent errors≤20；
- busy timeout≤100ms、总 deadline≤250ms；失败 section degraded 且不抛出；closed schema 稳定；
- 返回值只读：修改调用方副本后再次读取不变化；
- 只允许状态/attempt/error/time 的安全聚合。通过正文列 canary 与公开 query audit hook（若有）验证未读取 payload/content/embedding/blob；没有黑盒审计能力时该子义务必须 BLOCKED。

## 8. Storage fixtures

JSONL：`max_bytes=B`、`max_files=F`、事件数足以触发至少 F+2 次轮转；普通安全目录、symlink target、不可写目录、已有非 regular path。核对逐行 JSON、0600、文件数、大小、轮转顺序和失败计数。

Ring：capacity N，写 N+K；记录 accepted ledger 和 sequence，核对固定容量与确定 overflow。并发 case 使用 barrier 同时 emit/read/close。

Bundle：单文件上限 P、总量 T、JSONL 数超过 retention、snapshot 正常/缺失/degraded、export copy failure、内置 canary。解包后只允许 manifest 声明的诊断文件；核对 P/T、missing/degraded 标记、canary 零命中和 SHA。

所有 storage roots 位于 `.local-test-evidence/2026-08-23/sdk-observability/<run-id>/storage/`，不得写入 plans/testcase 下的 Git 路径。

## 9. Root-cause blind audit fixtures

六个匿名 scenario 包：

1. recall timeout；
2. outbox retry→dead-letter；
3. context degraded/budget trim；
4. sink failure（业务成功）；
5. fact-job crash/recovery；
6. bundle canary denial（业务与已生成安全诊断保持可用）。

每包只含 correlation ID、事件 export、snapshot、文件 SHA，不含 scenario label、正文、原始 runtime log。审计答案 schema：component、last_success_stage、failed_transition、error_code、attempt/retry、recovery_result、run/outbox/job identity、history_complete、confidence。与密封 oracle 全字段比较；核心字段误判即 FAIL。

另有无正文诊断 discriminators：empty input、recall no result、budget trim、sink fault、outbox stuck、retry exhausted、recovery failed。每类必须有不同稳定 code 或安全 metric 组合。

## 10. Metrics comparison fixture

固定 workload W：固定 seed、请求数、并发度、attempt 分布、queue 初始深度与可控 latency。Baseline A 使用标准延迟/错误注入；Variant B 仅将指定 stage 延迟增加固定量、或将指定 retry 数增加固定值。

从事件/snapshot独立聚合：operation count、success/error count、retry count、queue depth/oldest age、duration distribution、recall candidate/selected count、context budget before/after/drop reason。Oracle 要求 B 的目标 metric 按预设方向变化，非目标 count 在确定性容差内相同。报告必须称 recall/context 字段为 quality proxy，不称正文质量。

## 11. Candidate、restart 与 rollback fixtures

每 slice manifest 保存 exact Harness/Memory wheel filename/version/SHA、Host identity、Python、platform、slice 与 commit。S1/S2 candidate 放独立本地 evidence 子目录，不上传、不进入 resolver cache、不覆盖同名路径。S3 只接受 Harness 0.4.0 + Memory 0.5.0 exact pair。

冷重启使用全新进程和同一测试 userdata；不得复用内存对象模拟。S2 migration 前备份文件与 schema/version/SHA manifest 置于 evidence root、权限 0600。rollback 故障场景先停 worker、验 SHA、原子恢复、用 S1 exact pair reopen 并验证业务状态摘要。

## 12. 证据目录规范

```text
.local-test-evidence/2026-08-23/sdk-observability/<run-id>/
  result.json
  commands.txt
  candidate.json
  cases/<case-id>/events/
  cases/<case-id>/snapshots/
  cases/<case-id>/jsonl/
  cases/<case-id>/ring/
  cases/<case-id>/bundles/
  cases/<case-id>/restart/
  cases/<case-id>/audit/
  sha256sums.txt
```

Git 可保存的后续小型结论只能引用上述相对路径、run/scenario ID 与 SHA-256；不得复制原始日志、bundle、截图、数据库或 canary payload。
