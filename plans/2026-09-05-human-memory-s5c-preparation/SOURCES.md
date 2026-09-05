# S5c 准备来源与原文定位

2026-09-05 只读快照。SHA 为读取时文件字节；原条款只引用，不修改。以下摘录并非新的 acceptance，未摘部分仍以完整原文件为准。

## 原批准合同

| 来源 | SHA-256 |
|---|---|
| [simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/slices/S5-host-context-integration.md](/Users/denny/projects/simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/slices/S5-host-context-integration.md) | `8be7828b889816bafea3385e397e9c2d5c9bcfd7a26028640cd34c20ccf45ff6` |
| [simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/increments/2026-09-02-s5b-effect-closure-memory/acceptance.md](/Users/denny/projects/simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/increments/2026-09-02-s5b-effect-closure-memory/acceptance.md) | `7ddef05dcbfc58e881e7520d51509ec059a654ab3ea13c6528d27ffab58c8c09` |
| [simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/increments/2026-09-02-s5b-effect-closure-memory/acceptance-amendments-proposed.md](/Users/denny/projects/simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/increments/2026-09-02-s5b-effect-closure-memory/acceptance-amendments-proposed.md) | `3763266aba4c3101231e8d0c7ca651a10bb642b95a3f710cec99755b01756b60` |
| [simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/increments/2026-09-02-s5b-effect-closure-memory/verification/plan-challenge/delegated-decisions-A6-A7-A11.md](/Users/denny/projects/simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/increments/2026-09-02-s5b-effect-closure-memory/verification/plan-challenge/delegated-decisions-A6-A7-A11.md) | `d7ca07d8cec362422485da0aa15dfe380c927e291da1566d0ef480d80bbdb8e3` |
| [simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/slices/V0-verification-and-spikes.md](/Users/denny/projects/simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/slices/V0-verification-and-spikes.md) | `8d16d048abc14ad4a9437ad57333edfb252086bad6324755a165b832f7177070` |
| [simple_harness-s5c-preparation/testcase/human-memory-program/TC-HM-04-procedure-prospective.md](/Users/denny/projects/simple_harness-s5c-preparation/testcase/human-memory-program/TC-HM-04-procedure-prospective.md) | `604db3d72b2770e5d70e25945eaef6d722eee99cb1784a7983789566c3730e12` |
| [simple_harness-s5c-preparation/testcase/human-memory-program/TC-HM-07-audit-forgetting.md](/Users/denny/projects/simple_harness-s5c-preparation/testcase/human-memory-program/TC-HM-07-audit-forgetting.md) | `e99fa71ff8a08efb87db03f86d2a08e6ecfd63a2234412c23c87fe021ea68c06` |
| [simple_harness-s5c-preparation/testcase/human-memory-program/fixtures/fault-matrix.json](/Users/denny/projects/simple_harness-s5c-preparation/testcase/human-memory-program/fixtures/fault-matrix.json) | `b4dcb2f39a2e5c2f7afeeb1dd496aa94587fe075c8772ea44fab880115bc74da` |

## 原 S5 Task 7

> ### Task 7 — 主模型 Memory analysis、Host↔Memory outbox 与即时操作 [HM-AC-2/7/8]
>
> - terminal commit 同事务写 Host sanitized raw evidence links + Memory ingestion outbox；调用 S3 exact wheel，receipt 回写 Host event。
> - 实现 `MemoryAnalysisExecutorPort`：按 job 绑定的 originating foreground Run provider/model/config 做独立 post-turn 主模型调用，
>   永久记录 reserved/handed_off/succeeded/failed/unknown attempt 与 request/result/validator receipt；不新建独立 extractor client。
> - Host executor 在任何 Provider execution 前按 `request_hash+attempt` 查询 durable attempt；已有成功/未知后确认成功的 result
>   必须返回同一 durable row，不能再次调用 Provider。`MemoryAnalysisDeliveryAuthorityPort` 必须从同一 store 暴露 exact
>   claim/request/attempt/envelope/result 事实给 Memory SDK；瞬时 authority 重试仍查同一行，构造新 attempt 只能遵守显式
>   unknown-call taxonomy，不能掩盖第二次 Provider 调用。
> - accepted Prospective registration/invalidation 由 Memory outbox 投影至现有 Host ReminderScheduler 衍生的唯一 scheduler；
>   event cursor 与 occurrence claim/presented/acknowledged/settled 分离，模型拒绝不标 processed。
> - explicit remember/correct/forget 标高优先 job，并确保 forget suppression receipt 在普通后续读取前生效；普通 batch 异步。
> - 按 V0 runtime/trigger spike 协议执行故障矩阵，证明 Host commit、analysis claim/result、Memory apply、registration/
>   occurrence/ack 与 Host receipt 各 kill 点可收敛。
> - 验证：crash/restart、duplicate payload、Memory unavailable/dead-letter、raw Host evidence 不丢。

## S5b 移交正文 AC-4/5 及 A11

> | S5B-AC-4（**移交 S5c**，本增量不验收、不计 MUST；措辞按 A7/A8 修订后原样带入 S5c） | Prospective 唯一 scheduler 与 occurrence 生命周期 | ① Host 从 `ReminderScheduler` 衍生唯一 `ProspectiveScheduler`（复用 claim/lease/双检/settle 设计），消费 Memory `read_outbox` 的 `memory.prospective.{registration,invalidation}.requested`，按 `outbox_id` 幂等投影到 Host state.db durable registrations；**outbox 消费与游标同一事务**；Host 以 outbox_id 派生的确定性 signal 身份回签 `REGISTRATION_ACCEPTED`（crash 重放同 ref）；claims 来源于 inbox；递归意图（『每周五』）在 wheel 只有 time/event trigger 下按**一次性到期**处理，递归留 backlog。（A8）② 到期/事件触发时 Host 以 `prospective_signal_authority` 调 `apply_prospective_signal`，同 trigger revision/event **恰一个** occurrence；invalidation 后不再触发。③ occurrence 状态 `claimed→presented→acknowledged→settled(acknowledged|suppressed|superseded|expired)` 与游标分离（canonical 生命周期表 = v46 `prospective_occurrences`）；**processed := 该 occurrence_key 存在 durable `prospective_ack` 凭证**（新 Host 工具 `prospective_ack{occurrence_key}`，五路可见，模型回显 Host 放进 inbox 消息的 64-hex key）；模型未调用/拒绝 → 不 processed，下一轮继续 pending；`occurrence_presented`（v45，S5a 门读源）只作投影，只在 occurrence 离开 mandatory inbox（acknowledged 或 settled superseded/expired）时同事务写入，snapshot 注入时不写；suppressed/FORGOTTEN occurrence settle 为 suppressed，永不写投影，内容零进入 Context。（A7）④ 按 V0 SPIKE-CROSS-DB-TRIGGER 协议在 registration/occurrence/snapshot/ack 四 kill 点收敛：pending 不丢、replay snapshot hash 相同、无重复 occurrence。⑤ 真实 provider：用户说"发布成功以后提醒我更新变更日志"→ Prospective 注册 → 触发 → 下一轮 no_recall 被拒且 summary 进 snapshot → 模型调用 `prospective_ack` → settled(acknowledged)；『终答提及』只作 quality_bar 人工检查。 | 次要 | 移交 S5c |
> | S5B-AC-5（**移交 S5c**） | 即时 remember / correct / forget | ① `memory_write`（remember/correct）与 `memory_forget` 从 stub 变为真实：remember/correct 产生高优先 immediate analysis job（同一 executor，优先于普通 batch，同一轮终态后立即执行）；forget 在工具内**同步**走 Host `memory_action_authority` → Memory `suppress`，suppression receipt durable 后工具才返回。② forget 生效顺序硬约束：suppression receipt 之后的任何普通读取（typed recall、five-day short-horizon、initial snapshot、occurrence inbox）不再返回该内容；原始证据行数/hash 不变。③ 验证：TC-HM-07 步骤 1–3 的自动化子集（记住→召回可见→忘掉→六路普通读取不可见→raw 守恒）+ 真实 provider 一例（自然语言"记住/忘掉"）。 | 次要 | 移交 S5c |
> - **D5 Release unit（用户委托裁决，2026-09-02，A11）**：S5b = S5B-AC-1/2/3/6，高风险子系统 ① workspace effect authority ② TaskScope closure/终态门 ③ Host↔Memory 终态 outbox + 主模型 analysis executor + Memory 0.6.1 cutover，计 3；Task 0,1,2,3,4a,4,5,6,7,8 共 10。**S5B-AC-4 与 S5B-AC-5 移入独立增量 S5c**（Prospective 唯一 scheduler/occurrence/`prospective_ack`、即时 remember/correct/forget、`prospective_signal_authority` 与 `memory_action_authority` 注入、v47 prospective 三表、`prospective-occurrence` fault lane、S5B-S5/S6 真实车道、S5B-S7 的 memory tool 载荷变异），措辞按 A7/A8 修订后原样带入 S5c acceptance；S5c 在 S6 之前完成，S6 program 验收前三链齐全的前置不变。S5b 边界不变量见「明确不包含」。

## A8 原修订

> | A8 | S5B-AC-4①② | — | 追加："outbox 消费与游标同一事务；Host 以 outbox_id 派生的确定性 signal 身份回签 `REGISTRATION_ACCEPTED`（crash 重放同 ref）；claims 来源于 inbox；递归意图（『每周五』）在 wheel 只有 time/event trigger 下按**一次性到期**处理，递归留 backlog" | `prospective-registration-accepted-signal-step-missing`、value specialist xref |

## A7 完整裁决

> ## 裁决三：A7 —— Prospective "已处理"定义：**采纳 ack 凭证定义 + 有界重现规则**
>
> **结论**：processed := 该 `occurrence_key` 存在 durable `prospective_ack` receipt（Host 工具，strict schema，key ∈ 本 principal 的 presented 集合，五路可见；receipt 以 `(sdk_run_id, occurrence_key)` 幂等，lost-ACK 重放返回同一 receipt）。模型未调用/拒绝/timeout/Run 失败 → 不 processed，occurrence 保持 presented，下一 Run 继续注入，`no_recall` 继续被拒。"终答提及"只作 S5B-S5 真实车道的 quality_bar 人工检查，不进机器门。
>
> **一致性核对**：
> - program"所有模型/工具/文件/测试事实由 Host 不经 LLM 追加…不用正则或后台总结模型"：ack 是模型显式工具调用形成的 Host 事实，与 `task_scope_update` 同构；不解析终答文本。
> - HM-AC-5"SDK 可靠地产生触发候选及状态审计，但不越权执行"：`prospective_ack` 零外部动作，只改 occurrence 状态并留审计；模型只能回显 Host 放进 inbox 的 64-hex key，无法凭空 ack 未呈现的 occurrence。
> - TC-HM-04 步骤 4"只产生一个 occurrence；pending→triggered/settled；pending 不丢"：Host 侧 settled(acknowledged) 由 ack 轮终态提交驱动（或下一个观察到 acknowledged 行的终态），Memory 侧 triggered→completed 只由 ack 轮的 analysis `MemoryMutationPlan` 产生（`llm_payload_driven`，经 Memory transition 表校验）；"pending 不丢"要求 Host **不得静默过期**（见下）。
> - HM-S4 期望"意图 pending；SDK 只产触发候选"：不变。
>
> **有界重现规则（裁决：按身份有界、按时间无界、不静默过期、有确定性升级信号）**：
> 1. 呈现幂等：同一 `(occurrence_key, sdk_run_id)` 至多一条 presented 记录（snapshot receipt 同事务）；同 Run 内 crash/replay 不产生第二次呈现；不同 Run 才计新一次呈现。`presented_count` durable 单调。
> 2. 无静默过期：Host 永不因"呈现了 N 次没人 ack"把 occurrence settle 掉。唯一出口：模型 `prospective_ack`（acknowledged）；用户显式 forget/suppress（`memory_forget` → suppression → settled(suppressed)，内容零进 Context）；Memory 生命周期退出（cancelled/expired/completed/superseded，由 tick 从 inbox/outbox 事实派生为 settled(superseded|expired)）。
> 3. 确定性升级信号：`presented_count ≥ 3`（N 在 v46/v47 设计冻结固定）时，inbox 消息带 `overdue=true` 布尔字段并追加恰一条 append-only `prospective_ack_overdue` 审计事件；occurrence 仍 mandatory（no_recall 仍被拒——program plan.md:102-103 口径）。不引入新表、不改门谓词。
> 4. 必测（deterministic 子 lane）：模型连续 3 个 Run 不 ack → 3 条 presented 记录、同一 occurrence_key、零重复 occurrence、overdue 事件恰一条、意图仍 pending；第 4 Run ack → acknowledged → 终态 settled(acknowledged)；ack 重放同 receipt；ack 一个未呈现/他人 key → 稳定拒绝零状态变化；用户 forget → settled(suppressed) 且六路普通读取不可见。
>
> **对 program 承诺的影响**：HM-AC-5/HM-S4 的兑现方式从"终答提及"（不可确定性验证）改为"结构化 ack 凭证"（可验证、可重放），是对承诺的**加严**而非放宽；用户可感知差异：AI 要明确"确认收到提醒"（工具调用），没确认下次还提醒，用户可随时说"忘掉这个提醒"关掉。随 A11 一并移入 S5c 验收。

## 源码核对

| 文件 | SHA-256 |
|---|---|
| [simple_harness-s5c-preparation/backend/deskpet/memory/human_memory_v7.py](/Users/denny/projects/simple_harness-s5c-preparation/backend/deskpet/memory/human_memory_v7.py) | `af1e3e73970f8dba763b3d7734bbdd7613ef55e1448b80a25dc273114778a873` |
| [simple_harness-s5c-preparation/backend/deskpet/memory/memory_ingestion_outbox.py](/Users/denny/projects/simple_harness-s5c-preparation/backend/deskpet/memory/memory_ingestion_outbox.py) | `dcf5d296b57e1921205e76d12b616d339dadadc089033028897b1d826d10dad0` |
| [simple_harness-s5c-preparation/backend/deskpet/memory/analysis_proposal.py](/Users/denny/projects/simple_harness-s5c-preparation/backend/deskpet/memory/analysis_proposal.py) | `1f986dbb4bd81f603a0b4feb72fe09873dd74869c2b2594cc050b5a08841ef78` |
| [simple_harness-s5c-preparation/backend/deskpet/sdk_adapters/context_authority.py](/Users/denny/projects/simple_harness-s5c-preparation/backend/deskpet/sdk_adapters/context_authority.py) | `0eb9a8e81ab8e587fcd8827d5e49fae73d1cab771a545625bf35c841c71b7251` |
| [simple_harness-s5c-preparation/backend/deskpet/companion/reminders.py](/Users/denny/projects/simple_harness-s5c-preparation/backend/deskpet/companion/reminders.py) | `99e1a8143b3f5aed3ab5ce8060242aa5f29580e516080fcd2001d5f03be9a886` |
| [simple_harness-s5c-preparation/backend/deskpet/execution/foreground_queue.py](/Users/denny/projects/simple_harness-s5c-preparation/backend/deskpet/execution/foreground_queue.py) | `e9b7bff54d87f3c3d4474d15f8a1ce436a115611a732cac27e1209005f803fb3` |
| [simple_harness-s5c-preparation/backend/deskpet/tools/memory_tools.py](/Users/denny/projects/simple_harness-s5c-preparation/backend/deskpet/tools/memory_tools.py) | `a0f886c076cc6e9aac529b25dc7515dc42242e88ec233ba2ddcb7a0885271b79` |
| [simple_harness-s5c-preparation/backend/deskpet/tool_catalog/real_tool_manifest.json](/Users/denny/projects/simple_harness-s5c-preparation/backend/deskpet/tool_catalog/real_tool_manifest.json) | `79f1019b5f46ba1f3cdb168193c1dd4289ea22e4c8eac0fd7ead6d243ec99a85` |
| [simple_harness-s5c-preparation/backend/deskpet/memory/migrations/037_context_route_ledger_v45.sql](/Users/denny/projects/simple_harness-s5c-preparation/backend/deskpet/memory/migrations/037_context_route_ledger_v45.sql) | `e3346d62a0468814db4f692df77d109232c6ed9f9aea1be10f68e8ce227eac52` |
| [simple_harness-s5c-preparation/backend/deskpet/memory/migrations/038_effect_closure_memory_v46.sql](/Users/denny/projects/simple_harness-s5c-preparation/backend/deskpet/memory/migrations/038_effect_closure_memory_v46.sql) | `bb6eb9001c9fc4b7d888e5be19cd7fe995847c18a8bef4ca7991f22c6f5d644d` |
| [simple-harness-memory-sdk/src/simple_harness_memory/core/jobs.py](/Users/denny/projects/simple-harness-memory-sdk/src/simple_harness_memory/core/jobs.py) | `d14caee50a11600d4b4e1262d3e828bdd7e05a281de9b166d95989a3ea540f21` |
| [simple-harness-memory-sdk/src/simple_harness_memory/core/manager.py](/Users/denny/projects/simple-harness-memory-sdk/src/simple_harness_memory/core/manager.py) | `52c9730fad4f8e687a5b45356d0a3d1b5d99e22efff9ff534ddd0c77ae9c32b6` |
| [simple-harness-memory-sdk/src/simple_harness_memory/backends/sqlite_v5.py](/Users/denny/projects/simple-harness-memory-sdk/src/simple_harness_memory/backends/sqlite_v5.py) | `ee0b275589912d52f083fb017bd80d26d598b6e2596886723f07dfbd42063703` |
| [simple-harness-sdk/src/simple_harness/runtime/memory_action_protocol.py](/Users/denny/projects/simple-harness-sdk/src/simple_harness/runtime/memory_action_protocol.py) | `a56f4e7e5741ef5ea68029aa82631851bab5878790e9b8b5f059851fe2b6d1fe` |
| [simple-harness-sdk/src/simple_harness/runtime/prospective_signal_protocol.py](/Users/denny/projects/simple-harness-sdk/src/simple_harness/runtime/prospective_signal_protocol.py) | `c4e569ab496c1e36b1c8fc0bb862458bd2127523668662a33cb0c24f9c977f82` |

## 候选源码一致性（只读 git show 比较）

- `simple-harness-memory-sdk/src/simple_harness_memory/core/jobs.py` 与 `2f3d73814fe6a884e0458d87567b918c5863033e`：字节一致。
- `simple-harness-memory-sdk/src/simple_harness_memory/core/manager.py` 与 `2f3d73814fe6a884e0458d87567b918c5863033e`：字节一致。
- `simple-harness-memory-sdk/src/simple_harness_memory/backends/sqlite_v5.py` 与 `2f3d73814fe6a884e0458d87567b918c5863033e`：字节一致。
- `simple-harness-sdk/src/simple_harness/runtime/memory_action_protocol.py` 与 `2b8428465cbd41032ba024a0b7199183161f5ecd`：字节一致。
- `simple-harness-sdk/src/simple_harness/runtime/prospective_signal_protocol.py` 与 `2b8428465cbd41032ba024a0b7199183161f5ecd`：字节一致。
