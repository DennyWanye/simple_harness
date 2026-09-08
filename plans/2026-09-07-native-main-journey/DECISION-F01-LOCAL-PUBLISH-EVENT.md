# F01「发布成功 → 提醒更新变更日志」本地事件来源裁决

**独立子代理裁决，待用户确认后实施。** 2026-09-08，只读调查，未改动任何代码。

对象：`plans/2026-09-06-typed-use-primary/FOLLOWUPS.md` F01。用户 09-08 决定：**事件来源在本地**。
结论摘要：推荐 **B — 观察绑定工作区内的本地 git release tag**，作为唯一「发布成功」事件来源；A（Host 执行/观察发布命令）与 C（UI 人工点「发布完成」）不推荐，理由见第 2 节。

---

## 1. 现状核对（已实现 / 缺口）

### 1.1 事件协议：SDK 侧已经完整，缺口 100% 在 Host

| 事实 | 位置 |
|---|---|
| 信号种类含 `EVENT_OCCURRED = "event_occurred"` | `simple-harness-sdk/src/simple_harness/runtime/prospective_signal_protocol.py:47` |
| `event_occurred` 必须携带 `ProspectiveEventTrigger` 且只允许 `pending → triggered` | `prospective_signal_protocol.py:158-162` |
| 事件触发 DTO 三字段：`event_authority_ref` / `condition` / `condition_hash`（`condition_hash` 必须绑定 `condition`） | `simple-harness-sdk/src/simple_harness/runtime/memory_protocol.py:2409-2427` |
| `occurrence_key` 由 subject+memory+revision+signal_id+signal_kind+**signal_receipt_hash**+registration_ref+registration_revision 派生 | `prospective_signal_protocol.py:184-199` |
| Memory SDK 落库支持 `trigger_kind='event'`（`due_at` 为 NULL） | `simple-harness-memory-sdk/src/simple_harness_memory/backends/sqlite_v5.py:10351-10353`；`backends/schema_v5.py:653` |
| `apply_prospective_signal` 的审计核验已接受 `event_occurred`（outcome=`matched`，reason=`prospective_trigger_matched`） | `backends/prospective_sources_v2.py:107-131`；`backends/sqlite_v5.py:18427-18432` |

即：**注册 ACK → 信号 → occurrence → 呈现 → ACK → settle 全链对事件类型已经打通，SDK 零改动。**

### 1.2 Host 侧的三个硬缺口

1. **调度器只做时间**：`backend/deskpet/memory/prospective_scheduler.py:1-6` 文档明写「Event triggers … deliberately outside this one-shot time-trigger executor」；`prospective_scheduler.py:125-127` 对非 `time_due` 直接 `raise prospective_timer_requires_time_due`。
2. **信号 journal 写死时间语义**：`backend/deskpet/memory/prospective_signal_store.py:83-92`（要求 `signal_kind=='time_due'`、`trigger_kind=='time'`、`observed_at>=trigger_at`）与 `:93-95`（signal_id 域名固定 `host:prospective-time/v1`）。
3. **模型根本无法提出事件触发**：提案工具 schema 的 `prospective` 只有 `action`/`trigger_at_iso`/`timezone`（`backend/deskpet/memory/analysis_proposal.py:112-124`），编译器只构造 `ProspectiveTimeTrigger`（`analysis_proposal.py:393-400`）。当前全仓唯一的 `ProspectiveEventTrigger` 使用点是质量语料里的**永不触发**负控 `corpus:unobserved-event:`（`backend/deskpet/quality/corpus_c04.py:82-86`）。

### 1.3 与事件源无关、可直接复用的部分（重要，决定了工作量）

- 注册消费链 `PublicRegistrationAuthoritySource` 对 trigger 类型**不敏感**，原样透传 `source.trigger`（`backend/deskpet/memory/prospective_registration_source.py:130-141`），事件注册可零改动走完 ACK。
- 时间源 `PublicTimeAuthoritySource` 只过滤 `trigger_kind=='time'`（`backend/deskpet/memory/prospective_time_source.py:55`），事件注册会被自然跳过，互不干扰。
- occurrence 协调、呈现、`prospective_ack` 工具、settle 全部按 Memory inbox 条目工作，与触发类型无关：`backend/deskpet/memory/prospective_occurrence.py:41-56`、`backend/deskpet/sdk_adapters/prospective_ack.py:16-34`。
- 「只提醒一次」天然成立：`prospective_time_source.py:26-45` 的 `registration_is_live` 要求 `transition_to ∈ {pending, rescheduled}`，首次 matched 后 lifecycle 变 `triggered`，同一注册不会因第二个 tag 再次触发。

### 1.4 INTERFACES.md 的约束（必须继续满足）

`plans/2026-09-05-human-memory-s5c-preparation/INTERFACES.md:40` 的 `await scheduler.observe_event(event_receipt_ref)  # 只接受 Host durable event`，并在同文件第 2 节收口：
- 「event signal 绑定 Host exact event receipt+condition hash」「Host 不从终答『发布成功』几个字推断外部效果成功」（同节第 5 条）；
- 「禁止任意模型字符串成为可信事件」「不能以整 Run COMPLETED 等同发布成功**或新增外部发布能力来凑验收**」（同节末段）。

**该文件里没有 `HostEvent` 类型，也没有任何 durable event 的具体定义**——全仓 grep `durable event` / `HostEvent` 无源码命中。因此 F01 的第一件事就是**定义** Host durable event receipt 的形状（第 3 节给出）。

---

## 2. 三个候选的比较

Host 现有可利用的「本地事实」台账（子代理复核）：
- `run_shell` 存在且是 PROJECT_EFFECT 门控工具：`backend/deskpet/tools/os_tools/run_shell.py:584`、注册 `backend/deskpet/tools/os_tools/registration.py:188-225`（`dangerous=True`）、权限 `backend/deskpet/sdk_adapters/tool_authority.py:76`。
- 效果闭包会把已结算的 PROJECT_EFFECT 变成 durable 客观事件：`backend/deskpet/sdk_adapters/effect_gate.py:619-668`，收据链 `backend/deskpet/execution/evidence_ingress.py:739-996`（`payload_hash=canonical_hash(payload)`）。
- Host 已有 git 集成，但**只解析仓库根**：`backend/deskpet/session/project_binding.py:171-197`（`git rev-parse --show-toplevel`，`root_kind='git'`）。读 tag / commit sha 全仓无实现。
- 已有成熟的「UI 点击 → durable 确认收据」模板：`backend/deskpet/task_scope/workspace_bindings.py:325-370` + 表 `task_workspace_manual_decisions`（`backend/deskpet/memory/migrations/030_task_workspace_bindings_v38.sql:39-48`）。（`backend/deskpet/capabilities/skill_install_ui.py:434-458` 那条 `ui_decision_event_ref` 只在进程内传递，**没有落库**，不能当模板。）

| 维度 | A 可配置发布命令（`npm run build` / `make release`） | **B 本地 git release tag** | C UI「发布完成」按钮 |
|---|---|---|---|
| 真实可观测性 | 弱。**致命点**：客观事件对非白名单命令 `command_head` 直接为 `None`（`effect_gate.py:508-517` 白名单只有 pytest/npm test/cargo test 等 8 条；`_test_runner_head` 非命中返回 `None`，`effect_gate.py:555-566`），stdout/stderr 按设计**不留存**（`effect_gate.py:630-633`）。台账里区分不出「发布命令成功」与「任意一次 shell 调用」。要用它必须扩容那份冻结的脱敏白名单——属隐私面变更 | 强。tag 名 + tag object sha + commit sha + tagger 时间是仓库内既有、可独立复算的事实；只读，可重放且字节稳定 | 最弱。本质是人的断言，不是外部事实；与「用户直接说提醒我」几乎等价 |
| 不可伪造（不能靠终答/Run 完成冒充） | 弱。模型可自行 `run_shell` 跑那条命令来「自证发布」，且 auto 模式无提示 → 等于**授权模型自动执行真实发布副作用**（发布属 `procedure_applicability.py:56` 明列的 publish/delete/payment 危害类），产品上不可接受 | 中强。tag 由用户自己的发布流程产生；模型要伪造须经 `run_shell`（PROJECT_EFFECT 门 + `effect_gate_rejections`/客观事件留痕），可与客观事件台账交叉核对并把同 Run 内 agent 产生的 tag 标 `agent_attributable=true` 后拒绝（见 3.3） | 最强（只有人能点），但代价是把「事件触发」退化成人工触发 |
| 与 TaskScope / workspace effect gate 对接成本 | 中。需扩客观事件 payload 白名单 + 新增「已注册发布命令」概念 + 读客观事件台账；且 Host 变成发布的**执行者**，直接撞上 FOLLOWUPS/INTERFACES「不新增外部发布能力」 | 低-中。只读，不经 effect gate（不产生 PROJECT_EFFECT），复用 `project_binding.py:171-197` 已解析的 git root 与 `task_workspace_binding_heads` 绑定身份 | 中。需新增控制 WS 消息 + 落库表；`workspace_bindings.py:325-370` 模板可抄 |
| 审计链 | 部分复用现成收据，但语义不足（见上） | 需新增一张 append-only 观察表（形状照抄 `043_prospective_time_signals_v51.sql`），链完整 | 收据链完整，但事实本身不可复算 |
| auto 模式零提示一致性 | 冲突。要么模型无提示地自动发布，要么必须重新引入授权提示 | 完全一致（纯后台观察，零提示） | 一致（用户主动动作，不是授权弹窗） |
| 原生验收可行性 | 难做强负控：Host 自己触发的事件无法证明「不是模型冒充」 | 强。用户在自己终端 `git tag -a v0.1.0`，Host 全程未参与；负控「模型说已发布但没有 tag → 无 occurrence」干净 | 可行但弱：只能证明按钮链路，证明不了「发布」 |
| 产品价值 | 中 | 高（release tag 是本地发布的规范锚点，CHANGELOG 与 tag 天然同一次提交） | 低 |

**裁决：推荐 B。** A 被否的关键不是工作量，而是它要求 Host 执行发布命令（越过 FOLLOWUPS 的红线）且现有客观事件根本记不住是哪条命令；C 被否是因为它把「事件触发」降级为人工提醒，没有兑现 F01 的产品意图。C 可作为**将来**的补充来源（同一 event source 接口的第二个实现），不进本次范围。

---

## 3. 推荐方案 B 的实施要点

### 3.1 需要改动的 Host 文件与量级（SDK / Memory SDK 零改动）

| # | 文件 | 性质 | 量级 |
|---|---|---|---|
| 1 | `backend/deskpet/memory/migrations/s5c/047_prospective_publish_events_v55.sql` | 新增。两张 append-only 表：`prospective_publish_sources`（发布源注册）、`prospective_publish_observations`（tag 观察，带 `prior_hash`/`record_hash` 链）。DDL 形状照抄 `043_prospective_time_signals_v51.sql`（含 no_update/no_delete 触发器与唯一索引） | ~90 行 |
| 2 | `backend/deskpet/memory/s5c_publish_schema.py` | 新增校验器，照抄 `s5c_timer_schema.py:31-63`（迁移身份、schema 对象逐条比对、`human_memory_recovery_table_registry` taxonomy=A、三个 `hm_recovery_fence_*` 触发器） | ~85 行 |
| 3 | 版本元组统一 51-54 → 51-55 | 改动。`backend/deskpet/memory/s5c_timer_schema.py:31,44,66-80`、`backend/deskpet/memory/prospective_signal_store.py:41-46`、`backend/deskpet/memory/schema.py:517` 附近及 `s5c_schema.py` 的同类判断，约 6-8 处 | ~20 行 diff |
| 4 | `backend/deskpet/memory/prospective_publish_observer.py` | 新增。只读 git 观察器：在已绑定工作区根上跑 `git for-each-ref --format=... refs/tags`（照 `session/project_binding.py:171-197` 的 subprocess 用法），按注册的 `ref_pattern` 过滤，首次见到的 tag 写入观察表（幂等：`UNIQUE(owner_key, source_id, tag_object_sha)`），并做 3.3 的归因交叉核对 | ~150 行 |
| 5 | `backend/deskpet/memory/prospective_publish_source.py` | 新增 `PublicPublishAuthoritySource`，**逐段对照** `prospective_time_source.py:26-95` 改写：`registration_is_live` 相同（只把 `trigger_kind=='time'` 换成 `'event'` 且 `trigger.event_authority_ref` 必须等于已注册发布源的 ref）；`prepare_due` 换成 `prepare_observed` | ~120 行 |
| 6 | `backend/deskpet/memory/prospective_signal_store.py` | 改动。把 `prepare` 的时间专用校验（`:83-95`）与 `_check_observation`（`:105-115`）抽成按 `signal_kind` 分派的小策略，新增 `event_occurred` 分支。**复用同一张 `prospective_timer_events` 表**（表结构通用，signal_id 域名不同即可），避免第二套 journal | ~50 行 diff |
| 7 | `backend/deskpet/memory/prospective_scheduler.py` | 改动。`:125-127` 的 `prospective_timer_requires_time_due` 放宽为 `{time_due, event_occurred}`，其余 claim/handoff/apply/lease 逻辑原样复用 | ~10 行 diff |
| 8 | `backend/deskpet/memory/analysis_proposal.py` | 改动。`prospective` schema（`:112-124`）改为二选一：时间分支不变，新增事件分支 `{action, event_source, condition}`，`event_source` 用 **enum**（枚举当前已注册的发布源 id，无注册源时该分支不出现在 schema 里）；编译器（`:393-400`）新增 `ProspectiveEventTrigger(event_authority_ref=已注册源的 ref, condition, condition_hash=fingerprint_json(condition))`。模型只能选已注册源，不能自造字符串 | ~55 行 |
| 9 | `backend/deskpet/memory/prospective_runtime.py` | 改动。`_components()`（`:63-77`）追加 observer + event source + 第二个 `ProspectiveScheduler`；`tick()`（`:79-100`）追加一段 try/except 与 `prospective_runtime_event_applied` 日志；`RuntimeProspectiveSignalAuthority.resolve_*`（`:30-41`）新增前缀 `host:publish-authority:` 路由 | ~35 行 diff |
| 10 | 发布源注册入口 | 新增。最小实现：绑定工作区后由用户在设置里登记「仓库 + tag 前缀」，走控制 WS，落库到表 1，收据形状照 `task_scope/workspace_bindings.py:325-370` 的 manual decision（`challenge_id` 唯一 → 重放幂等）。**这是唯一需要 UI 的部分**（一次性配置，不是每次发布的确认） | ~110 行 + 一处前端设置项 |

合计约 **600-700 行、9 个后端文件 + 1 处前端设置**，新增一个 schema 版本 v55。UI 的提醒卡片、ACK、settle 全部零改动。

### 3.2 事件 receipt 形状（建议冻结为下列 canonical JSON）

发布源 ref（写入注册表，作为 `ProspectiveEventTrigger.event_authority_ref`）：

```
event_authority_ref = 'host:publish-source:' + canonical_hash(
    [owner_key, source_id, workspace_root_hash, ref_pattern])
```

单次观察（append-only 表一行，且是 signal 的 `signal_receipt_*` 来源）：

```json
{"schema_version": 1,
 "kind": "host_publish_observation",
 "owner_key": "<canonical_hash(deployment,household,actor)>",
 "subject": "<principal.actor_id>",
 "source_id": "<已注册发布源 id>",
 "event_authority_ref": "host:publish-source:<hash>",
 "workspace_root_hash": "<canonical_workspace_root 的 hash>",
 "binding_head_revision": 7,
 "ref_name": "refs/tags/v0.1.0",
 "tag_object_sha": "<40/64 hex>",
 "commit_sha": "<40/64 hex>",
 "tagged_at": 1788773700.0,
 "observed_at": 1788773702.0,
 "agent_attributable": false}
```

```
signal_receipt_id   = 'host:publish-observation:' + canonical_hash(observation)
signal_receipt_hash = canonical_hash(observation)
signal_id           = canonical_hash(['host:prospective-publish/v1', owner_key,
                       scheduler_registration_ref, registration_revision,
                       target_memory_id, target_revision, trigger_hash,
                       signal_receipt_id])
```

Intent 其余字段与时间路径同构（`prospective_time_source.py:71-79`）：`signal_kind='event_occurred'`、`trigger=` 注册时的 `ProspectiveEventTrigger`、`transition_from='pending'`→`transition_to='triggered'`、`outbox_id=None`、`run_id`/`operation_id` 取注册来源 lineage（不得凭空造前台 Run）。`observed_at` 固定为**首次观察**时刻，重放不得改写（这是 `prospective_signal_store.get_prepared` 已有的语义，`:151-155`）。

三条不变量：
1. `observed_at >= tagged_at`（事件不能先于事实）；
2. 同一 `tag_object_sha` 只产生一个 `signal_id` → SDK 侧 `occurrence_key` 唯一 → **唯一 occurrence**；
3. 第二个 tag 不会二次触发同一注册：`registration_is_live` 要求 `transition_to ∈ {pending, rescheduled}`，首次 matched 后为 `triggered`（`prospective_time_source.py:38-41` 同款检查）。

### 3.3 防伪造：与客观事件台账交叉核对

写观察行之前，用 `commit_sha`/`tagged_at` 的时间窗查该工作区在同一 Run 内的 PROJECT_EFFECT 客观事件（`evidence_ingress.py:739-996` 写入、`effect_gate.py:619-668` 分类）。若窗口内存在本 Host 发起的 `run_shell`/`process_start` 效果，则该行落 `agent_attributable=true` 并**不生成 signal**，只留审计。原生验收必须包含这条负控（步 7）。这是「不能靠模型终答或 Run 完成冒充」的可执行落点。

### 3.4 原生验收步骤与预期证据（写法对齐 `NATIVE-R4-R5-REMINDER.md`）

前置：全新隔离 userdata；真实 provider（`-m real_provider`，gpt-5.6-luna）；auto 模式；后端由源码加载；准备一个**测试用本地 git 仓库**并绑定为工作区。

| 步骤 | 操作 | 预期结果 / 证据 |
|---|---|---|
| 1 注册发布源 | 设置里登记「仓库=<repo>、tag 前缀=`v`」 | state.db `prospective_publish_sources` 1 行，`event_authority_ref=host:publish-source:<hash>`；收据可重放（重复登记不新增行） |
| 2 创建事件提醒 | 用户：「以后这个项目发布成功了，提醒我更新变更日志。」 | 助手措辞只说「可以处理」，**不得**声称已排程（沿用 `prospective_runtime.py:15-23` 的 REMINDER_CAPABILITY 口径）。后台真实模型提案：human_memory_v7.db `prospective_records` 1 行 `trigger_kind='event'`、`due_at IS NULL`；state.db `prospective_scheduler_registrations` prepared→applied；`user_version=55` |
| 3 未发布不触发 | 再随便聊两轮 | 无 occurrence、无提醒卡片；`prospective_timer_events` 中无该 registration 的 event signal 行 |
| 4 用户自行发布 | **在 Host 之外的终端**执行 `git tag -a v0.1.0 -m "release"` | Host 未参与（native.log 无对应 `run_shell` 效果） |
| 5 观察 → 信号 | 等 1-2 个 lane tick（`poll_seconds=2.0`） | `prospective_publish_observations` 新增 1 行（`agent_attributable=false`，含 tag/commit sha）；`prospective_timer_events` 出现该 signal 的 prepared→claimed→handed_off→applied；日志 `prospective_runtime_event_applied count=1`；human_memory_v7.db `prospective_trigger_events` 新增 1 行 `signal_kind='event_occurred'`、`outcome='matched'`、`reason_code='prospective_trigger_matched'` |
| 6 唯一 occurrence + 提醒 + ACK + settle | 下一轮用户发一条无关消息（如「38 加 19 等于多少？」） | occurrence claimed→presented，历史出现独立「提醒」卡片（「更新变更日志」）；模型调用 `prospective_ack`，**auto 模式零授权提示**（`product_policy_user_confirmation` 计数 0）；`prospective_occurrences` presented→acknowledged→settled=`acknowledged`；重复 tick 不产生第二个 occurrence_key |
| 7 负控 A（终答冒充） | 新建同类提醒后，诱导模型在终答里写「已经发布成功了」，但不打 tag | 无观察行、无 signal、无 occurrence |
| 8 负控 B（agent 归因） | 让模型自己经 `run_shell` 打一个 `v0.1.1` tag | 观察行 `agent_attributable=true`，**不生成 signal**，无提醒 |
| 9 负控 C（不匹配前缀） | 打 `nightly-2026-09-08` tag | 无观察行（`ref_pattern` 不匹配） |
| 10 冷重启 | 正常退出 → 同 userdata 重开 → 发一条新消息 | 历史与已 settle 的提醒卡片原位保留，不重复呈现，无新 occurrence |

证据留存（照 `NATIVE-R4-R5-REMINDER.md` 的「原始证据（ignored）」表）：`.local-test-evidence/<date>/native-<bundle>/` 下 `launch.json`、`native.log`、`resource-*.json`，以及退出后 `userdata/data/state.db`、`userdata/data/human_memory_v7.db` 快照的 SHA-256；另附测试仓库 `git for-each-ref refs/tags` 输出与 `.git` 目录快照 hash。

### 3.5 单测最小集（原生之前）

1. `prospective_signal_store` 的 `event_occurred` prepare/replay/身份不符三条（照 `backend/tests/memory/test_s5c_store.py` 风格）；
2. `PublicPublishAuthoritySource`：同一 tag 幂等、第二个 tag 不二次触发（lifecycle 已 `triggered`）、注册失效后不触发；
3. observer：`ref_pattern` 过滤、`agent_attributable` 归因拒绝、`observed_at < tagged_at` 拒绝；
4. `analysis_proposal`：无注册源时事件分支不出现在 schema、模型给未注册 `event_source` 被拒。

---

## 4. 明确不做 / 遗留

- 不新增任何 Host 主动发布能力（不执行 `npm run build` / `git push` / `git tag`）。
- 不扩容 `effect_gate.py:508-517` 的客观事件命令白名单（那是 A 方案才需要的隐私面变更）。
- 递归/重复提醒（同一注册多次发布）仍是 backlog，本次维持「只提醒一次」。
- C（UI「发布完成」按钮）作为同一 event source 接口的第二实现，留作后续可选补充。
