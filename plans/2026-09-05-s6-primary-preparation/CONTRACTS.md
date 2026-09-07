# S6 Task 1/2 — 待实现的公共契约

状态：§1最小连接复用和queue.enqueue无scope admission已实现；其它DTO/operation为后续设计，尚未接线。见IMPLEMENTATION的实测状态。不改Harness/Memory已发布wire，UI保持未接线。

## 1. 认证、连接和 authority（主审修订：最小复用）

使用现 `/ws/control` default transport，先完成既有Rust签名 `companion_profile_bind`，再发送现 `human_memory_request` / `human_memory_response`。不新增URL、scope、账户、primary.attach或每读重签。`primary.open`仍返回稳定primary receipt；连接不能把primary_ref作为legacy session_id。

`HumanMemoryControlBinding`保存该socket已验证bind的FrozenOwnerIdentity。每次dispatch前验证gate owner+binding_epoch和既有durable lease的active/process/connection/control_epoch/challenge/main/identity_bind事实。另一socket的identity-ready广播不算本连接授权；重连需沿既有bind，旧lease/rechallenge/unbind失败。固定local subject和authority_ref保持原语义；连接epoch只用于admission，不进入每次delivery内容hash。

原bind primitive负责签名/nonce/序列/Host auth snapshot；HUMAN API现递归拒绝subject/mode/authority/allowed_set。普通读取不产生新签名请求，消息写入按稳定delivery_key冲突检查和持久receipt重放，effect照旧exact授权。鉴权失败返回现human_memory_response error envelope，稳定code为human_memory_connection_unbound或human_memory_connection_stale，不调用service。

以下消息投影接口仍是下一步设计，尚未接入；不能以新增DTO代替真实history。

## 2. 消息 admission 与 queue

现 `queue.enqueue` 已允许省略scope_ref；下列扩展intent为后续拟定request，附件/resume尚未实现：

```typescript
type PrimaryTurnIntent = {
  delivery_key: string;
  text: string;
  attachment_refs: readonly string[];
  resume_intent?: {
    scope_ref: string;
    source_ref: string;
    source_sha256: string;
    open_receipt_ref: string;
  };
  expected_primary_ref: string;
};
```

- Host 解析附件 refs，复用上传所有权、类型、尺寸和 sanitizer；客户端不能提交任意本地路径作为已授权附件。模型选择走经验证的 primary settings 操作，在 admission 时冻结选择；不允许直接传 provider URL/key/config。
- 不传 scope 即普通 turn；不自动继承当前 active task 的项目执行权限。`resume_intent`是用户要求继续 exact task 的受验证意图，不能直接充当 ExecutionEnvelope/route receipt。Host 检查 open receipt 与当前 source/owner，随后交既有 context_route 收敛。
- delivery hash 包含规范化 text、附件身份/内容 hash、resume intent、冻结的会影响执行的选项；幂等索引限定 subject+primary+delivery_key。相同 key/hash 返回同一 receipt；不同 hash 明确冲突。重连完成既有profile bind后的 request_id 可以变，delivery_key 不变。
- 验证失败不写半条 user evidence/queue；不可跨库原子时用 durable admission/outbox/reconciliation 保证最终同一条、可重放，不声称跨库事务。ACK 只在可恢复的 admission receipt 已落盘后发送。
- 返回 `{turn_ref, receipt_ref, delivery_key, content_sha256, enqueue_sequence, state:'queued'|'running'|'terminal'}`；同一时刻只能一个 foreground Run。UI 清 draft 依据 durable ACK，非 WebSocket.send=true；丢 ACK 重发不二次执行。
- queued turn 不等于 current Run 的 follow-up input；不通过 legacy chat_v2/target_root_run_id 旁路队列。暂停、取消、停止是独立 control，不从普通文本正则识别。

无scope执行：先以现 Harness `initial_route=None` 的未裁决路径进入 context_route；以集成回归证明 standalone/no-recall 可完成、memory standalone 走允许召回、task outcomes仍必须取得 Host route/binding receipt。如当前 SDK public入口不接受这种组合，应交还该入口的具体 contract gap，不能 mock route 校验或创建假 scope。最近10个完整因果 turn groups来自 durable primary history、按原 Context budgets组装，当前 query 不重复、不同 task信息不隐式混权。继续保留 SDK session FK 的隐藏 execution 身份。

`primary.queue.control`：

```typescript
type ExactRunControl = {
  request_id: string;
  expected_run_ref: string;
  expected_generation: string;
  control: 'pause' | 'stop' | 'cancel';
  reason?: string;
};
```

Host 在同一 queue transaction 内 compare expected identity，再使用既有 control 状态机；不存在/已换Run返回 stale_target，不操作新的当前Run。相同 control request 重放返回原结果；离线不排队盲发。control 枚举先核对现支持值，只暴露已接通的状态转换；不能因 UI 有按钮而虚构 resume。`resume_intent`启动新的 task continuation，与恢复原 PAUSED Run 区分。

## 3. 普通消息 read model 与实时恢复

新增 `primary.messages.page`、`primary.messages.subscribe`、`primary.state`，均从连接 identity推导 primary；不是 `list_evidence` 的直接转发。

```typescript
type PrimaryMessage = {
  message_ref: string;
  sequence: string;
  turn_ref: string;
  host_run_ref?: string;
  delivery_key?: string;
  role: 'user' | 'assistant' | 'tool';
  parts: readonly PublicMessagePart[];
  state: 'queued' | 'running' | 'complete' | 'failed' | 'cancelled';
  detail_ref?: string;
};
// PublicMessagePart显式复用现安全文本/Tool/Artifact DTO的allowlist，
// 不用 Record<string, unknown> 暴露 SDK ContentBlock 或 provider metadata。
```

SDK事实必须经 Host 验证 `primary→turn→host_run→sdk_run→execution_session` 对应。`composition.py`新增 typed public reader；检查 SDK public API 可读的稳定 message/part ID与分页能力，缺失时显式列 SDK port 交付，不读取私有checkpoint blob代替。`read_closure_run_facts`的字符串化正文不是合格消息API。UI只接Host opaque refs，不以provider session ID选择会话。

拟定 page request `{cursor?, direction:'older'|'newer', limit?}`，默认50/上限200；response `{items,next_cursor,has_more,snapshot_revision,privacy_revision}`。这是新UI设计上限，仍受原 Context/TaskScope budgets约束；不能冒称原 AC 给了200。排序键使用durable monotonic sequence+stable message_ref，不能仅靠相同时间戳；cursor绑定subject、primary、query方向、snapshot/privacy revision并校验完整性。

响应建议64KiB UTF-8硬上限：达上限前切页；单条超限用公开detail_ref分块读取且有完整长度/hash，不静默截断原消息，不丢canonical facts。detail页同样权限过滤/byte上限，任意ref与旧privacy revision不能绕过过滤。first page给一致水位；订阅请求以该水位开始，持久化后发 `primary.changed {revision,privacy_revision}`，UI补page/state。先subscribe还是先page不应造成空窗：服务器注册与high-water检查需同一有序屏障，缺revision则重新取snapshot。

流式增量复用现public projector，标成暂态并以稳定part ID去重；完整内容以durable snapshot为准。SDK已terminal但Host closure/projection待追赶时，分开返回 `execution_state` 与 `projection_state:'pending'|'caught_up'`，不要渲染假完成或再次发起Run。重启/worker重放补齐投影，不重发Provider/tool。

普通投影应用当前suppression/recipient policy，不能因原evidence永久保留而永久可见；privacy revision变化立即清旧可见cache/搜索/详情，再安全补读。sealed audit需原Task4的purpose-bound decision，不能进入这些API、默认trace、tooltip或离线缓存。测试 credential/hidden-reasoning canary应覆盖消息parts、errors与详情。

`primary.state` 返回同一水位下的 `{active_task?, current_run?, queued_turns, queue_cursor?, projection_revision, privacy_revision}`；所有列表有界，不把queue表全集发送UI。active_task从Host已接受route事实派生，不从最近点击候选或任一SDK内部session推算。模型/usage/permission/Artifact关联使用Host运行映射；Tool授权仍沿现exact nonce/decision/version权威，不能由primary凭据替代用户具体授权。

## 4. TaskScope inspect/resume 与 binding

| 操作 | 契约与 UI 行为 |
|---|---|
| 新 task_scope.list | permission-first，recent order稳定keyset；返回scope_ref/title/status/revision/roots摘要/next/blocker/continuation可用性，默认20/上限50，禁止列全库再前端过滤 |
| 现 task_scope.search | query/cursor/max_candidates；只返回候选和可见摘要，不修改active、不授root、不加载全archive |
| 现 task_scope.open_exact | exact scope_ref + expected_source_hash；Host真实filesystem probe产生drift。UI不得自报live_probe作为freshness证明；返回source/ref/hash、open receipt、ResumePackage/版本，stale明确失败或显式标记只读stale |
| 现 task_scope.view/evidence_groups/evidence_page | README/STATUS首次概括；PLAN/DECISIONS/RESUME/EVIDENCE按需page-in。所有页显示source revision/hash/refs，沿现limits；不同revision的cursor不可拼成同一快照 |
| 未来 queue.enqueue.resume_intent | 来自exact-open结果；点击继续需重新验证source/ownership；排队后到执行前还要route/binding校验；不设activeSid，不调旧session create，不直接改scope状态 |
| 现 binding.manual.propose/decide | native picker只是候选；Host canonical identity/roots校验→challenge→用户看清exact root/revision后明确确认；取消/过期/重放/错误challenge不追加 |
| Auto provenance | 只读mode/来源/receipt/binding revision；不提供切Auto控制。执行权限来自可信Run mode+Host验证，UI不能传mode获得授权 |

只读inspect不写新Run、task mutation或binding；必要的访问审计receipt不属于任务状态变化。wrong candidate、他人scope、revoked root、missing/identity drift都不能得到effect权限；查看失败不可自动选另一个候选。README与STATUS是阅读投影，不是执行authority。

## 5. 接线前接口清单

必须先交付：primary认证dispatcher、stable attach、standalone admission/runtime/context/terminal、durable public reader/projection、exact run control、active/recent list、可信drift与resume intent、primary模型/附件/Skill/permission/usage映射。每项需正负回归；未交付接口显示明确不可用仅用于开发状态，不能以此通过保留功能的cutover验收。

可原样复用但需新identity映射：Settings/凭据/onboarding、全局Skill目录、Artifact阅读、现permission decision、独立Service SDK通话。后续原任务继续：Memory graph、sealed audit完整UI、S5c Dirac语义与质量、原S6 Tasks5～8全量和真人门；这些不因Task1/2文档完成而获得PASS。
