# T3 独立 consumer 实现边界

2026-09-05。继续用户原授权，代码仅修改自己的 `s5c_store.py`，新增 `s5c_consumer.py` 与必要测试。未改 Carver 的 terminal/runtime/ingestion 文件，未改 SDK、pin、v45/v46/v47 SQL、默认 schema、原 AC 或 oracle。T4/T5 接线由主协调；本文不是 T3 全部交付或 S5c 完成声明。

## 实际接口与耐久事实

- `ProspectiveRegistrationConsumer(store, memory, authority_source).run_once(page_size=100, max_pages=4)`：从 principal-bound public `read_outbox` 消费 registration/invalidation。先恢复最老 prepared，再读取新行；按公开 `(created_at,outbox_id)` 严格顺序校验分页，未知 prospective topic、malformed source、缺来源或错误回签显式失败。
- `RegistrationMemoryPort` 只要求现有 `read_outbox` / `apply_prospective_signal`，没有 LLM、Memory SQL、claim/dead-letter/任意 mutation 接口。读取所有 outbox 状态避免把可变状态当新身份；只处理两个既有 topic。其他 topic 仅跨页扫描，不确认其业务。无关 topic 的扫描位置只在内存优化，重开从耐久 registration cursor 重读，不会伪造其持久消费记录。
- `RegistrationAuthoritySource.prepare_registration(principal, entry)` 是 trusted composition 的显式来源接缝，返回绑定原 scope/lifecycle/Run/operation/outbox 的固定 typed authority。**生产实现尚缺**：outbox v1 不携带完整来源。消费者不推断或生成 Run，不改变 v1 payload，不自行延长 expiry。测试 source 明确是 fixture，不是生产授权证明。
- `S5cStore.registration(outbox_id)`、`pending_registrations(limit)` 读取原 prepared 来源/ref；恢复不重新调用 source。`commit_registration_result(ref, result)` 只接受 exact signal/memory/base revision、相同 committed revision/lifecycle 的 `acknowledged` 回执，验证决定时间、固定 ref、完整 hash；同回执幂等、不同回执冲突。
- v47 已有 `phase=applied` 现在存放明确的 v1 receipt envelope：prepared record ID/hash + 原 Memory result/result_hash。与 prepared 行保持 append-only，authority 列仍为原值，不修改原来源或 cursor，不新增表/列。此 phase 仅表示 Host 已耐久记录回签；**不是 occurrence processed**，也不表示 Memory outbox 原行状态变成 applied（实测原行仍可 pending）。
- 进入 pass / 发出 Memory apply 前检查原 recovery fence；本地 receipt 仍由原 SQL fence 保护。生产唯一 lifecycle owner 的 quiescence/drain 接线仍必需：入口检查不被描述成覆盖整个跨库 await 的原子锁。

## 已验证与仍缺的闭环

新增 **21 passed**：18 个 consumer 单元/Host 实库测试，3 个已安装 Memory SDK 实库集成。先红为缺 consumer 模块；注册提交/Memory 已提交后断联/Host 回执提交前后中断、同 ref 重开/并发、跨 owner/错误回签、missing source、乱序页、无关 topic、invalidation 顺序、recovery关闭不出站都有决定性断言。

三个 SDK 用例经真实 Host evidence admission + CREATE plan + Memory public mutation/registration API；Memory 回签丢失后关闭并重开两库，原 authority 过期仍取回原 result（仅一个 SDK result），未消费的过期 authority 连续拒绝并保持 prepared。它们不启动 backend/provider，不调用模型；测试安装为 Harness0.7.2 / Memory0.6.3 / Service0.3.12。本分支未触碰主线程冻结的 Memory0.6.5 candidate。

既有 66 项相关回归通过。合并首跑 86 passed / 1 failed：新 recovery fixture 直接 UPDATE fence 被原约束拒绝，改用既有 coordinator.begin_close 后新增21项全部通过。前两次 SDK fixture 分别误用 proposal.payload 键、filter policy，均已修正并保留原失败日志。Ruff E/F/I、diff whitespace 通过。自身实现独立 review 仍 **NOT_RUN，交主协调**，不能用执行者检查代替。

以下仍未实现，不减少原 A7/A8/A11 判据：

1. 真实来源 resolver、唯一 scheduler lifecycle/main wiring 与恢复排空；未消费且已过期 authority 的合法续接协议。
2. 固定首次 due/event observation、可解析的 Host 成功事件 receipt、先失效阻止 trigger、同 event/revision occurrence 唯一性及 claim 的完整来源/当前披露复核。当前没有 timer、event bridge、due writer 或新 scheduler lease。
3. T4 snapshot/present/ack/terminal，T5 action/priority，T6 suppression/旧快照披露，以及各自真实两根/gate；本切不会从服务存在推定这些完成。

证据均在本 worktree ignored `.local-test-evidence/2026-09-05/s5c-t3-consumer/`，详见 journal。Carver `29902ea4` 的独立审查单独存于 `.local-test-evidence/2026-09-05/s6-29902ea4-review/HANDOFF.md`；其未提交 WIP 未纳入结论、未被本线程修改。
