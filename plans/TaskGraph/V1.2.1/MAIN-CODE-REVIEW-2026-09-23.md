# TaskGraph 主体代码审查（候选19）

最后更新：2026-09-23 CST。主代理完成主体编码，两次独立 Sol 只读审查当前隔离 working 源码；另一次 Sol 窄审启动恢复改动。未发现可证明的 P0/P1/P2 主体缺口。此结论允许开始本阶段少量真实关键链路测试，不等于原42场景、稳定性或产品全门通过。

源码根：`.local-test-evidence/2026-09-22/taskgraph/htn1-integration/working/`。实际运行：不可变 `candidates/taskgraph-19/` wheel。正式上游为最终 HTN `102ad3dfa2db38d575ea929d39ec5ed1561a71da` 加捕获的 dirty 源码；不是纯该HEAD内容。

| 范围 | 已核对的实际调用链 | 静态结论 |
|---|---|---|
| TG-A/启用与历史 | taskgraph_policy:103–151 → 固定tenant/principal、实际deployment acceptance、原planning委派、一次性policy绑定；taskgraph_store:208–360逐代校验parent/snapshot/requirements/certificate/event/pins/source | 主体已有；ACTIVE baseline同事务，Attempt历史不以latest替代 |
| TG-B/来源和preview | taskgraph_plan_sources:215–273 的12条完整来源 → taskgraph_preview:91–171 的真实request/decision/requirements/binding/history重读与冻结 | 不构造空来源或人工APPLIED；缺来源拒绝 |
| TG-C/原提交和派发 | taskgraph_plan_commit:58–157 → 原PlanCommit/APPLIED/receipt/history/event/followup；commit_service:3206–3403 + taskgraph_dispatch:263–354 → 原Attempt/intent/manifest/immutable input/receipt同事务 | 真实DATA resolver及overlay/Selection精确集合；handoff再核fence/当前权利/冻结输入 |
| TG-C/物化和恢复 | event_handler:5535–5586 → manifest DATA物化 + overlay/Selection分层；taskgraph_attempt_inputs:203–254冻结历史；startup_assembly早于workspace/runtime恢复 | 保留mission/task/hash/路径冲突检查；启动变更已做具名强退/Host重建窄验证 |
| TG-D/动态收敛 | begin_convergence先VerifiedConvergenceAuthority验证后写fence/job/targets/followup；runtime observation读取Attempt/intent/reserve/provider UNKNOWN/tool/全部Operation历史；原cancel/reconcile证明后resume原Commit | READY仍fenced；真实ACTIVE revision/APPLIED/check/command一致才mark_applied；abandon受认证及安全边界约束 |
| TG-D/通知 | EventConsumer游标+派生followup同事务；pump先持久下游receipt后ACK；ACK重读receipt与CAS；强退复用command key | 无丢责任或以lease到期当Worker已停止的静态旁路 |
| TG-E/Host和UI | start/rebuild固定装配 → 原认证Mission读口 → SDK四只读API → TaskGraph store/parser → MissionTaskGraph组件 | tenant/principal不从payload取；身份/断链清屏，request ID关联、事件变化标stale、旧水位拒绝 |

独立审查边界：没有运行测试/服务、没有编码；没有重新审全部原H1 admission/codec/migration。完整负控/并发/统计与规模验证按本阶段用户指令后置，不能把NOT_COVERED写成PASS。一个防御性观察：内部TaskGraphSources.read_structure自身不鉴权，但当前生产装配只通过固定tenant/principal的内部reader调用，公开API另行校验；没有找到可达越权路径，不计P2。

当前主体代码阶段：CODE_PATHS_PRESENT；本阶段实际完成需再满足 MAIN-ACCEPTANCE-2026-09-23.md 的关键链路及必要修复。原全门保留开放，package acceptance NOT_RUN。

## 真实验证暴露后的增量审查（candidate22）

最初静态审查未发现根评审缺少原pytest回执，不能据此替代真实链路验收。candidate19 seeded场景暴露该缺口后新增原VerificationPassed证据读取。Sol只读挑战先后指出整体摘要无界、Acceptance来源闭包不足、缺少result-bound scope仍展示PASS、合法target=None不兼容；均已在工作源码修正，candidate20/21保留为未验收历史候选，不继承PASS。

最终局部复核：无新增已证实P0/P1/P2。正式ACCEPT记录/package/Acceptance身份对齐，原结果DONE/PASS，同Mission/task/attempt/result唯一事件；固定验证层集合及去重、固定字段和数量上限，原事件哈希保留；未绑定scope只输出UNBOUND/计数/固定解释，无PASS或runs。非TaskGraph路径不变。复核为只读代码挑战，未运行回归；主代理只做原库3Acceptance只读回读及null target/UNBOUND两个定点检查。新candidate22才承载本修复的真实模型复验。

## Mission judge身份修复（candidate23）

candidate22根正式评审通过后，真实Mission judge在TaskGraph handoff误挡（view_id非Attempt）。独立Sol仅审此改动：无Attempt例外需原critic、注册同Mission ACTIVE judge workspace、原唯一BudgetReserved无Task/Attempt、原artifact producer再核fence；真实Attempt路径不放宽。按P2建议将judge subject尾段收紧为正数ASCII十进制ordinal，与原mission_tail规则一致。新candidate23复验，不在候选22覆盖已冻结manifest。
