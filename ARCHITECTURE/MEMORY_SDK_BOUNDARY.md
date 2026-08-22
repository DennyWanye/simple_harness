# Memory SDK 边界与 Host 接口契约

> 最后更新：2026-08-23
> 验收基线：simple_harness `4e797ccd`；Harness `fbb156f` / 0.3.0 / wheel `cf629cee…`；
> Memory `3d4247b` / 0.4.0 / wheel `bfcd2506…`
> 发布标记：Harness `v0.3.0` → `fbb156f`；Memory `v0.4.0` → `3d4247b`；主分支与 tags 已推送；
> 本地冻结 wheel/sdist 已正式发布到对应 GitHub Release，并通过公开稳定 URL 下载回验

本文档是 simple_harness 的 Memory 生产边界事实源。2026-08-22 的官方一等集成已完成代码、自动化门禁
与真实 macOS Computer Use UI 验收；SH-M1～SH-M6、SH-SURFACE 均已在真实 DeepSeek provider 下通过。

## 1. 当前生产链路

```text
Tauri/React chat/chat_v2
  -> validated local HumanIdentity.identity_namespace_hash
  -> immutable deployment/household/actor/session binding
  -> root 或 continuation 独立 immutable Context source snapshot
  -> Harness ConversationTurnInput / ConversationContinuationInput
  -> SDK durable context claim
  -> SDK 调 read-only product Context provider + MemoryManager recall
  -> frozen Context stage（Memory 始终按 untrusted data）
  -> Provider / Tool / recovery 复用同一 stage
  -> completed terminal committed-turn outbox
  -> MemoryManager.record_committed_turn
```

产品不再调用 `prepare_consumer_conversation_context`，也不再构造 `ConversationMemoryAdapter`、manual recall
query 或 query/sink 双口。Harness 的正式 `AgentMemoryPort` 与 `ConversationContextProviderPort` 是前台唯一
自动 Context/Memory 组合。

## 2. 资源与身份 ownership

| 事实/资源 | owner | 当前边界 |
|---|---|---|
| Session/UI message、delivery、Provider usage、非 Harness outbox | `state.db` / SessionDB | 产品投影事实 |
| Run、Context stage、Provider invocation、committed-turn outbox | Harness execution v4 DB | SDK 执行事实 |
| Messages/Facts/Twin/recall snapshot/write fence | Memory SDK v4 DB | 长期 Memory 事实 |
| Persona/历史/Skill/附件/project/task source | simple_harness content-addressed repository | provider 只读；同 ref 同 bytes |
| MemoryManager 生命周期 | simple_harness process | production builder 构造一次；Runtime `BORROWED`；shutdown 先关 runtime borrowers，再由 SessionDB 有界 drain/关闭 manager 一次 |

身份只来自 `LocalAuthSnapshotProvider.current_snapshot()` 经
`validate_auth_snapshot(..., user_data_dir=...)` 得到的 `HumanIdentity.identity_namespace_hash`。
`deployment_id` 来自 `state_db_identity.instance_id`；首次 actor 获得随机稳定 household；同 session 不可换绑。
模型、payload、Provider 配置、API key 与 legacy `profile_id` 均不能提供或覆盖 actor；身份损坏在 LLM 前
fail closed。

## 3. Context source durability

- root 与每个 continuation 各自生成 content-addressed immutable source ref；continuation 不继承 root ref。
- ingress 原子创建带 lease 的 `PENDING` binding；SDK durable accept 后标 `CLAIMED`；stage/terminal 后进入
  `STAGED` / `CONSUMED`。
- terminal 释放 root 与全部 continuation refcount；共享 hash 不会被单个 binding 误删。
- orphan cleanup 只处理超过 horizon/lease 且 execution claim-inspector 证明无引用的记录；inspector 故障
  保留重试。
- provider 只读 source snapshot，校验 canonical hash、item/byte bounds，不写产品数据库。

## 4. 写入 authority 与工具面

- Harness foreground message 使用 execution committed-turn outbox；`FAILED` / `CANCELLED` 不生成长期 Turn。
- Companion/background/非 Harness message 保留 `product_memory_outbox`，经同一个 MemoryManager 的 explicit
  projection 写入。`memory_authority=harness|product|none` 保证同一消息不进两套 authority。
- ordinary foreground catalog 不再暴露可触发第二次 live recall 的 `memory_recall` / `memory_search`。
- simple_harness 现有显式 remember/read/forget 工具从 resolver 的完整 deployment/household/actor/session
  构造可信 `MemoryPrincipal`；write 调正式 `remember_fact` 并保留 salience/pinned/tier，返回准确 fact ID；
  read 调 `read_fact`，不再按 legacy user 扫描 facts。独立 event key 的同 payload 重试保持同 ID，元数据变化
  conflict，跨 principal 不可读/不可重放。forget 显式传 `source_event_id`，由 SDK canonicalize payload hash；
  首次 action 与重放返回同一结果，后续独立 action 对已删除 fact 稳定返回 false，receipt 跨重启保持且不复活；
  自然语言遗忘仍按安全例外关闭。
- `MemoryManager.share_fact(principal, fact_id)` 是 Memory SDK 正式授权分享接口；本轮不为 simple_harness
  新增 `memory_share` Tool/UI，供后续 K6/AgentOS、NovelTagSystem、AI Phone 消费。

## 5. 恢复、迁移与 DEV fault

- 产品 v4 coordinator 只调用两 SDK 的公开 migrator；先备份两库并写 owner-only journal，任一步失败恢复
  all-old pair，完整 hash 验证后才保留 all-new pair。
- recall timeout 按 SDK policy 降级为空 frozen stage；record transient 不回滚成功响应，由 durable outbox
  重试收敛。
- fault wrapper 仅在 `DESKPET_DEV_MODE=1` 且 user-data 位于仓库 `.local-test-evidence` 时允许装配；其他路径
  fail closed。

## 6. 当前验证状态

- exact wheel SHA/direct-url installed-origin 与 candidate conformance：PASS。
- Harness full：`1379 passed, 2 skipped`；Memory 默认 full：`200 passed, 7 skipped`，正式 candidate gate：
  `205 passed, 2 skipped`。
- 产品最终聚焦：backend `83 passed`；MemoryPanel `18 passed`；TypeScript typecheck PASS。
- 产品 full baseline：15 shards PASS、2 个实施前 known-red（root live fixture、ESLint 171 fingerprint），0 unexpected。
- SH-I01：同 user-data 重启稳定、Provider/API key/model/payload spoof 不影响、跨 user-data 隔离、损坏身份/
  错误 snapshot 在 LLM 前拒绝、legacy profile 排除：PASS。
- 真实 UI：SH-M1～SH-M6、SH-SURFACE 全 PASS。SH-M5 按冻结 exact oracle 跨进程新 Session 召回
  `Max`；`Aurora-R4` 只属于早期隔离 canary。SH-M6 以进程环境 attestation 直接证明 recall timeout
  fixture 已启用且主 Turn 不受阻断；record transient 在未写入时退出后由 startup recovery 唯一收敛，
  新 Session 回答“晚饭后”。SH-SURFACE 已在当前构建真实打开 macOS 附件选择器并以 Esc 安全取消。
- r7 独立审计因附件截图错配、recall fault 缺直接 attestation、S6-A8 状态文字与导入 custody 不完整而
  判 FAIL；这些证据/文档缺口已在继任 Gate 输入前修复，r7 不作为发布 receipt。原始截图、日志、进程
  attestation 和 Gate ledger 仅在 ignored `.local-test-evidence/2026-08-22/`，Git 只保存结论与 hash 索引。
