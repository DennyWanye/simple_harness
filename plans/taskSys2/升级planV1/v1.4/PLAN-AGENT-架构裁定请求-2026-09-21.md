# V1.4（去除 NanoJev）架构裁定请求

日期：2026-09-21。用户已授权整个 V1.4 实施；本文请 Plan Agent 收敛架构合同，不申请缩减范围，不要求重复批准一般实现选择。

## 目标与当前边界

目标仍是 H1–H8，NanoJev/Shadow/PR-7 不进入本轮验收。SDK 候选：`/Users/denny/projects/simple-harness-sdk-h1h-impl`，`codex/h1h-impl`，HEAD `102ad3dfa2db38d575ea929d39ec5ed1561a71da` 加保留的未提交改动。Host：`/Users/denny/projects/simple_harness`。禁止 reset/clean；候选未合并、Host 未重装、当前源码原生 UI 未验。

此前完整回归为 3891 PASS / 5 SKIP；后继仍在修改，不能把该数字当作当前最终源码证据。此前 H1-H 实际矩阵为 20 PASS / 8 PARTIAL / 8 NOT_COVERED；新专项尚未重新汇总。Provider 已有真实成功 Mission，当前所述缺口不是额度问题。应用模型继续 DeepSeek v4.1 Flash；子代理按会话约束仅 GPT-5.6 系列。

## 请优先裁定 D1 + D2（一个可执行补遗即可）

### D1：Operation 的生产入口与原子物化边界

已知原文：AER §6.3 规定报告 Acceptance → ACTION_PROPOSAL Review → 用户/政策授权 → 实际执行；§14.1 分为 A 编排授权事务、B 执行准入冻结请求并先记 HANDED_OFF、C 结果记录。原文**没有要求在 Worker 产出之前冻结 envelope**。此前本地设计添加过该要求，已经纠正。

实际缺口：

- `contracts/resolution.py::OperationEnvelope` 和 `storage/htn_store.py::bind_operation` 已有，但没有生产调用方创建并绑定 envelope。
- `orchestrator/commit_service.py::accept_result` 当前在接受 candidate artifact 的事务里调用 `propose_action`，不传 `planning_origin`。
- `BoundPlanningOperationOrigin.provenance_receipt_id` 没有真实 producer。
- `ACTION_PROPOSAL` 是已有 ReviewPurpose，但缺生产发起链；现有 Review/Acceptance 存储不是操作来源的完整替代。

建议方向（待裁定，不作为已批准规格）：在固定 Principal/tenant 的显式操作意图入口，核验正式 Review 和来源后，系统创建 Operation 身份；在一条编排事务中冻结 envelope、来源 receipt、action 和 link。沿用原 action ledger、预算与 outbox，不建立第二份执行事实账本。

请给出：

1. 真正调用入口及所有者：Host 的哪个认证入口 / SDK 的哪个生产方法？谁提交明确操作意图，谁组织 ACTION_PROPOSAL Review？
2. `accept_result()` 是先只接受 candidate、稍后物化 action，还是由带完整来源的统一 accept command 一次物化？必须指定唯一默认路径。
3. 身份签发、Review、授权、action/link/receipt 的准确先后与事务边界；重送、冲突和中途崩溃的状态。
4. 需要改动的实际文件，以及从入口到 handoff 的最小决定性测试。

不得用 task/target/connector/latest/hash 反推 Operation 身份；不得以测试签发器冒充 Host 生产入口。保留旧模式行为。

### D2：冻结 payload 与权威引用的最小合同

现有 `OperationEnvelope` 包含 parameters、effect contract、Review、Acceptance 的 TypedRef，但缺 parameters/effect-contract 的生产合同和 resolver。现有 `EvidenceResolver` 只处理 SourceCitation；不能视作任意 TypedRef 的通用解析器。

`runtime/connectors.py::OperationSpec` 只有 name/level/required_params/mutates/kind/cost ceiling；它不能独自证明 connector version、外部效果里程碑、条件写、幂等保留期或迟到执行条件。

请给出：

- parameters/effect contract 的最小字段、canonical 编码、持久介质（复用 artifact/event/既有表优先）、唯一 producer 与读取 API；区分真实 connector 声明、系统决定和模型候选。
- Review/Acceptance 的精确引用和验证方式，何时属于当前有效、官方审阅，以及撤销/陈旧时的拒绝规则。
- 明确三个不同 hash：参数 artifact 整体字节 hash、现有 `params_hash(candidate.params)`、candidate artifact 整文件 hash；不得互相替代。
- Task contract revision、Requirements revision、Plan revision 是三个独立轴，分别从哪里取得和验证。
- 对“未应用且不会迟到执行”的证据：实际 connector adapter 必须提交哪些事实；现有 connector 无法证明时继续 UNKNOWN。不能靠三个 true 布尔值代替权威回执。

不需要重写全套通用执行平台；请固定满足当前 V1.4 MUST 的最小合同，并明确能力不足时的具名拒绝。

## D3：REPAIR 延期与冷恢复（其次）

补遗 §5.4 已要求：先纯 preview；结构合法后，真实兄弟工作未收敛则延期；撤销后续派发权、请求停止、核对实际在途；收敛后复用同一个 decision/raw/request 重读当前状态，不新增模型回复、不一般化 rebase。

当前公开 `PlanningDecisionStatus` 无 DEFERRED；已有 ADMITTED/COMPILED 等状态。旧 `_retry_deferred_repair()` 读取 stored proposal text 并调用 `apply_planner_reply()`，不能直接假定它可以恢复新 PlanningDecision 协议。

建议方向（待裁定）：保留公开 decision 状态合同，用既有持久事件/待处理记录表达内部 convergence 阶段；新协议恢复必须重新进入原 decision admission/preview/commit 管线，并从 CAS 取原始回复。旧协议保持原路径。

请明确：持久延期记录字段及归属、唯一唤醒者、暂停/重启恢复入口、停止 gate 与 generation/lease 检查、超时终态，以及 grant/requirements/plan 变化时拒绝还是继续的精确条件。不得新建模型重试来掩盖恢复缺口。

## 不必交给 Plan Agent 的事情

Operation link 幂等比较、alias 拒绝、typed compiler error 丢失、SQLite 并发测试、在途工作 reader 和回归测试属于已明确合同内的实现工作，执行 Agent 继续修复。没有待裁定项的任务不暂停。

## 交付要求与权威入口

请输出一份小型补遗：每项有“采用方案 / 与原文一致之处 / 需要修订处 / 接口或 schema / 事务或状态机 / 生产 caller / 验收断言”。只列原则或“实现一个 producer”不能消除当前缺口。不将 focused PASS 当整个 H1 或 V1.4 完成。

权威原文：

- `simpleharness-v14-h1h-admission-amendment.zh-CN.md` §4、§5.4、§6、§7。
- `simpleharness-full-target-1.4/annex/aer-1.0/design.zh-CN.md` §6.3、§12–§14。
- `simpleharness-llm-native-htn-execution-plan.zh-CN.md`（H1–H8）。
- 当前调查与待实现方案：`Operation物化接线设计-2026-09-21.md`。其下半部旧接口草案仍需按 D1 收敛，不能把设计文本当生产事实。

JOURNAL_VERDICT: PARTIAL — 架构裁定请求已准备；上述链路尚未完成，未降低原验收要求。
