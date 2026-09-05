# S5c 接手准备：任务拆解与缺口

Current schema succession: [SCHEMA-48.md](SCHEMA-48.md). Older47 notes are historical.


2026-09-05 续接：T3 中独立 registration consumer 与回签恢复已实现并测试；原任务表完整完成条件不变，
真实 source resolver/调度与接线尚未交付。当前事实见 [T3-CONSUMER.md](T3-CONSUMER.md)。

2026-09-05；状态：**原准备稿保留；T1/T2独立Host基础已实现并自动验证，待独立review，生产未接线**。本轮用户已另授权隔离实现，当前定稿边界见 [T1-T2-INTERFACE.md](T1-T2-INTERFACE.md)。下文“本轮只准备/未实现”为首次准备交付历史，不代表新的整体完成声明。

工作树 `/Users/denny/projects/simple_harness-s5c-preparation`，分支 `feat/human-memory-s5c-preparation`，基于 Host 已提交 main `738c8aabd041716d10ca86858eb2a9584e08ec47`。未带入 main 正在修改的 Q1 episode 时间文件。S5b 候选仍冻结到 Q1 修复后重验，由主执行者协调；实施前接收其最终已提交候选，不能用本准备基线替代。

基线消费 Harness 0.7.2（source `2b8428465cbd41032ba024a0b7199183161f5ecd`）与 Memory 0.6.3（source `2f3d73814fe6a884e0458d87567b918c5863033e`）。Memory 文档 checkout 为 `2e78af9642b6dcea6f62452d7d967c7a7f1f158d`；代码与候选的差异须以 [来源表](SOURCES.md) 核对。历史 A11 的 0.6.1/0.6.2 与 0.7.1 冻结描述不是当前 pin。本轮不改 pin、不解冻 SDK、不启动 backend/provider、不运行测试、不修改 gate run。

## 已批准边界

事实源是 Memory 原 S5 Task 7、S5b acceptance AC-4/5、A7/A8/A11 裁决；原文摘录及 SHA-256 见 [SOURCES.md](SOURCES.md)。本文不是新 acceptance，不重编号/修改原 AC 或冻结 oracle。

- 承接全部 S5B-AC-4/5：唯一 Prospective scheduler、registration/invalidation、occurrence、五路 `prospective_ack`、即时 remember/correct/forget；两 authority ports、Host v47、prospective-occurrence fault lane、S5B-S5/S6 真实车道、S7 的 memory tool 载荷变异。
- A7：processed 取 durable ack；未调用/拒绝不能当 processed。同 Run 呈现一次，不同 Run 累计；第 3 次起 overdue，恰一条审计事件，不能因次数静默过期。Host terminal settle 与 Memory ack-turn analysis lifecycle mutation 分开。
- A8：Host 投影与 cursor 同事务；registration ack 从 outbox_id 派生固定 signal；claim 从 Memory inbox 派生；只有 time/event，递归要求作为一次性首次到期，递归留 backlog。
- A11：prospective 三表归 **v47**。AC-4 行内旧的 v46 字样由 A11 明确修正；不编辑 v45/v46 SQL/checksum。
- 不包含 S6 UI、外部通知/日历、发布/付款等动作执行器、递归调度、多 root 选择或 program 收口。TC-HM-04 的 Procedure 激活步骤不混入本次 Prospective lane。
- A15 下一轮 typed recall hit 的 S5c/S6 移交继续列账；AC-5 正向记住→召回必须真实验证，不以 S5b 物化替代。全局自动路由/召回策略不能借本准备扩大范围，S5b 仍如实 NOT_MEASURED。

## 代码现状与缺口

| 编号 | 已存在的接缝 | 要完成的差额 / 风险 |
|---|---|---|
| G1 | Memory `MemoryManager.read_outbox`、`apply_prospective_signal`、`read_occurrence_inbox`；Harness signal DTO/ref/resolve port | Host 没有 signal authority、registration consumer、canonical occurrence store；不能把 Memory pending cursor 推到末尾丢历史。 |
| G2 | `companion/reminders.py:ReminderScheduler` 有 claim/lease、claim 后 policy 双检、settle | 可复用设计；其 Companion DB、通知/draft dispatch、递归 next-occurrence 不能作为 S5c authority。新领域只有一个 Host scheduler，写 state.db，交付下一轮 Context。 |
| G3 | `sdk_adapters/context_authority.py:ProductRunContextAuthority.prepare_snapshot` 读 inbox；v45 `occurrence_presented`；`ProductRuntimeDecisionSink` 阻止 no_recall | 还没有 canonical presented/ack、与 snapshot 的原子写、overdue、终态结算；不能在第一次注入就写 v45 已离开 inbox 投影。 |
| G4 | `HumanMemoryV7Runtime.pending_occurrences` 按当前 lifecycle/privacy/suppression 过滤 | 未传当前 Run DisclosureContext，不能仅靠 privacy 类名证明第三方 recipient 安全；旧 Context/checkpoint/read view 的遗忘重验仍需逐路径证明。 |
| G5 | `analysis_proposal.py` 支持四类 CREATE；Prospective schema 只收 `trigger_at_iso/timezone` | S5B-S5 的“发布成功以后”需要 typed event；correct 的 REVISE/SUPERSEDE、ack 后 lifecycle mutation 当前不能表达。扩展 Host proposal/compiler，保留 exact quote/receipt grounding，不用关键词从终答推断事件或 ack。 |
| G6 | `MemoryAnalysisLane` 单 worker→runner；Memory `claim_analysis_batch` 按 oldest 分组；0.6.3 同 principal apply fence | **公开优先级契约缺口**：无 immediate 参数，不能仅让 Host 先 ingest 就声称优先于 Memory 中已 pending 普通 batch。需要最小 SDK 调度接口设计评审；不得绕开已有 fixed plan/base revision、claim、result fence。 |
| G7 | Memory `suppress(SuppressionRequest, principal=...)` 同步返回 durable decision；`MemoryActionAuthorityPort` 存在 | `suppress` 不接 action ref；ref 用在严格 mutation plan 的 existing-memory operation。Host 同步 suppression authority 与 SDK mutation resolver 应分清，不能伪装一次 `suppress` 调用已消费 SDK action authority。 |
| G8 | `tools/memory_tools.py` stable unavailable；manifest 仍 integer fact_id/tier/salience/pinned、opaque_manual | 新 Host-composed typed tools 需同时改 manifest/schema/权限分类/注册/dispatch/五路 exposure。不能把旧 integer ID 或无限 pinned 语义直接映射为 v7 memory authority。 |
| G9 | terminal transaction 追加 sanitized evidence+ingestion outbox；Host analysis executor durable 五态及 provider lineage | explicit intent 要在 tool 时 durable，terminal 后才允许 immediate job；普通 ingestion 与 immediate 不能重复分析同一 evidence。correct/ack plan authority 需在 durable result 固定后绑定，不能额外 LLM 调用补授权。 |

## 可执行任务顺序

三个独立风险面：跨库 scheduler/occurrence、即时 mutation/遗忘披露、Memory priority contract。以下 9 项是实施任务，不是本轮完成记录。每项提交必须包含实际入口接线，不能只交孤立 service。

| 任务 / 依赖 | 具体交付文件或接缝 | 先写的决定性验证 / 完成条件 |
|---|---|---|
| T0 候选接手 | 接收主执行者 Q1 后 Host SHA、三 SDK exact manifest；冻结本次输入 source index、复用原 oracle IDs | 不改 S5b r5 历史、不跨候选聚合 roots；每条迁移 AC 有原始定位。确认当前 v47 尚未被别的增量占用。 |
| T1 / T0 接口合同 | 按 INTERFACES.md 定稿 event signal、action service、priority DTO 与持久化布局；新增接口 conformance 测试草案 | G6 必须得出合法公开 API；若 current wheel 无法满足，按原计划接口缺口交主协调，不能本线程偷偷改 SDK/pin。G4 明确 disclosure 与 snapshot replay 失效规则。 |
| T2 / T1 v47 与 authority | 新 `memory/migrations/039_prospective_memory_actions_v47.sql`、`migrator.py`；新 `memory/prospective.py` 与 `memory/memory_actions.py` | v46→v47 fresh/reopen、原 evidence ID/hash 守恒、旧 runtime stable reject；删除任一 authority startup fail；同 ref 返回同 authority，变造 ref 拒绝。仅增加必要 action ledger，理由见接口文档。 |
| T3 / T2 registration/signal | `ProspectiveScheduler` + `HumanMemoryV7Runtime` ports + 唯一 lifecycle owner/main wiring | 历史 pending 从起点消费；registration commit/Memory ack lost-ACK；duplicate/late/reordered/invalidation-before-trigger；同 event/revision 一 occurrence，未接受 registration 不可 due。 |
| T4 / T3 occurrence/ack | `sdk_adapters/context_authority.py`、`foreground_queue.py` terminal、Host-composed `prospective_ack` + catalog/manifests | snapshot 同事务呈现；3 Run 不 ack，overdue 恰一；第4 Run ack、lost-ACK 同 receipt、终态 settle；未呈现/他人 key/未知字段拒绝。五路均可见且零文件副作用。 |
| T5 / T1,T2 即时写入 | `memory_actions.py`、`memory_tools.py`、manifest；`analysis_proposal.py`、`memory_ingestion_outbox.py` + 合法 SDK priority API | remember pending receipt≠已记住；terminal 前0analysis；immediate 优先普通 pending、当前 active fence 不被抢占；同 evidence 不双建 job；correct stale target 拒绝，已有 durable result 重放 provider calls 不增。 |
| T6 / T2,T4,T5 遗忘读屏障 | 同步 action service→public suppress→receipt；`human_memory_v7.py` + 必需的 Context/TaskScope public read 接缝 | TC-HM-07 rev2 步骤1–3逐项，包括 trace/旧 checkpoint；receipt 后读取不可见，raw 守恒；故障在 Memory suppression commit 后、Host receipt 前，重放同请求收敛；不扩大到撤销/受控 audit UI。 |
| T7 / T3–T6 fault/载荷 | 新聚焦 tests，沿用 `fixtures/fault-matrix.json` prospective-occurrence lane；S7 memory payload 子集 | registration/occurrence/snapshot-provider-send/ack 四 kill 点及 duplicate/late/reorder、refusal/no-tool/no_recall、suppression/第三方 recipient；时钟不变重放 hash相同；变造authority/target/quote/extra keys零越权。 |
| T8 / T7 真实及交付 | S5B-S5/S6 原真实 lanes，每项原矩阵2 distinct roots；文档 ARCHITECTURE/PROJECT_STATUS 对应事实 | 当前 candidate 的真实公开链、真实主模型；机械与语义质量分别review。provider budget继承6144/180s，不因失败盲重试；UI证据与A14协议入口区分。主协调资源、gate、合入；不宣告program complete。 |

执行时先跑 T3/T4 核心 register→inbox→ack 和 T5/T6 remember→recall→forget 的最小 oracle，再扩大 fault/真实矩阵。本准备没有运行这些验证，现存 S5b 两 roots 不能充当 S5c roots。

## 交接边界

本文及接口设计仅在独立 worktree。Host main、Memory repo、原 acceptance/oracle/gate ledger 均未由本准备修改。没有业务验收完成，因此不更新 ARCHITECTURE 的实现完成度；T8 实施交付时再按仓库纪律同步。

gate 只读结论另存 Host ignored `tools/gate-candidate-continuation-20260905T100858/HANDOFF.md`：当前 gate 的 stability 聚合没有 candidate 分区；import-evidence 不能修正该事实，r5 retire 还有 acceptance hash 不同的前置障碍。它不是已经执行的 ledger 修复，也不妨碍本次独立设计准备。
