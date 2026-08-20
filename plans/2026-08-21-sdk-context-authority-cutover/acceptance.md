# Acceptance：SDK Context 单一事实源与旧链路退役

> 来源：仓库根 `acceptance.md` 的 2026-08-21 已批准增量；本文件仅定义本 release unit，未缩减或改写原验收。

## 主要矛盾与范围

当前生产文字入口已切到 SDK Runtime，但实际 Provider Context、旧弹窗估算和 durable usage 使用三套
互相矛盾的事实源。本 release unit 建立一次准备的冻结快照，恢复 Persona、Memory、Skill、附件、
项目/任务和过滤后历史，使 Provider、executor、Inspector、usage 共享可追溯 lineage，并封闭旧读路径。

不包含：重新启用 Voice、改变 Memory 权限/召回策略、公开隐藏 CoT、把 UI activity/artifact/exclude
投影加入模型上下文、修改 Provider 价格或外部计费 API。

## Required AC

| ID | 功能点 | 验收条件 | 优先级 |
|---|---|---|---|
| AC-10 | Context 单一准备 authority | 每个 fresh SDK Run 只 prepare 一次；同一快照驱动 RunStart、首轮 Provider request 与 Inspector，不再入口重组 | 必须 |
| AC-11 | Context 内容完整 | Persona/system、Memory、Skill、Session history、current user、structured attachments、project/task 按既有策略进入真实 Provider request；可选未命中明确为空 | 必须 |
| AC-12 | Context 隔离与同 Run 协议 | fresh Run 只读 conversation allowlist；activity/summary/artifact/技术投影不回灌；同 Run tool call、必要 private reasoning、tool result 仅按 Provider 协议续传 | 必须 |
| AC-13 | 真实 Provider 用量权威 | 每个成功 physical SDK attempt 的实际 input/output/cache usage、provider/model、root/request/attempt identity 幂等持久化；missing/failed/cancelled/unknown 不伪造，重启可恢复 | 必须 |
| AC-14 | 工具目录一致 | Provider、SDK executor、Inspector 使用同一 immutable generation 的完整 product schema；名称、数量、schema token 构成一致，不读 legacy V2 或 `count*60` | 必须 |
| AC-15 | Context UI 如实展示 | ring 显示最近真实 measurement/actual model；modal 显示冻结请求构成及 estimated/measured 差异；无测量/旧记录/缺 usage 明确 unavailable，不显示 `(no model yet)` | 必须 |
| AC-16 | 旧链路封闭 | chat、ingress、Inspector、usage、hydration、catalog、attachment 的生产引用图中，旧 AgentLoop/legacy registry/facts probe 均切走或证明不可达兼容 | 必须 |
| AC-17 | 恢复与多 Session 隔离 | 普通问答、工具多轮、附件/项目、长历史、取消、缺 usage、Session switch、WAITING resume 和完整重启正确；snapshot/usage/provider/catalog 不串线 | 必须 |

## 非功能与数据边界

- Context preparation 只执行一次；Inspector 禁止重新 recall/probe。
- stable invocation/source identity；重复 settle 不重复，晚到旧 attempt 不覆盖新 authority。
- Inspector 仅有界分类、计数和脱敏 preview；secret/cookie/private reasoning/完整工具参数结果/附件 body 不公开。
- Provider payload 不因 public 双写膨胀；工具 schema tokens 从冻结 schema 缓存。
- 无 usage、binding-only 和 legacy history 可打开但必须标记 unavailable/legacy incomplete。
- `deskpet_public_progress` 是 optional presentation field；missing/blank/wrong-type 不阻断业务工具。
- WAITING 为可恢复非终态；durable provider/catalog binding 只在 completed/failed/cancelled 后释放。

## LLM 行为变异

| 变异 | 端侧断言 |
|---|---|
| 乱序 | 旧 attempt 晚到仍保留历史，但 current authority 不倒退 |
| 重复 | 同 invocation settle/replay 只生成一个 attempt/sample，不重复计数 |
| Schema 违约 | 缺 usage/model/public progress 或 usage 非整数时主回复/工具安全收束；usage unavailable，不制造 0；progress 缺失不阻断工具 |
| 超长载荷 | 长 history/schema/attachment/result 走预算与 hard bounds；Provider/Inspector/UI 不崩溃 |
| 拒不调用工具 | 只记录一次真实 text attempt usage；不伪造 tool card，Context 仍显示真实构成 |

## 场景矩阵

| scenario_id | input_class | exact_input | gate_type | required | manual | min_root_runs | cold_start | expectation |
|---|---|---|---|---:|---:|---:|---:|---|
| CTX-1 | persona-history | 根据我们刚才聊过的内容，用两句话告诉我你记得我的哪项偏好；不需要调用工具。 | positive-value | 是 | 是 | 2 | 否 | completed、非空、真实 usage；回答只引用本 Session 事实 |
| CTX-2 | memory-long-history | ≥10 轮后：我之前说过住在哪里、喜欢喝什么？不确定就直说。 | positive-value | 是 | 是 | 2 | 否 | history/memory 正确，无 activity/tool UI 回灌 |
| CTX-3 | attachment-project-tool | 读取我刚附上的这个公开文本文件，并告诉我第一行；需要的话使用文件工具。 | positive-value | 是 | 是 | 2 | 否 | Provider 首轮收到 structured attachment；工具多轮 usage/catalog lineage 一致 |
| CTX-4 | negative-tool-failure | 读取 `/definitely-not-present/context-canary.txt`；找不到就明确说明，不要猜。 | negative-safety | 是 | 是 | 1 | 否 | 不虚构；失败可解释；下一 fresh Run 不含失败卡/公开摘要 |
| CTX-5 | cold-start-restart | 隔离 userdata 首次配置 Provider，执行 CTX-1，完整退出重启后打开 Context。 | stateful-init | 是 | 是 | 1 | 是 | 无需二次设置；snapshot/usage/model 恢复且不串 Session |

至少执行两个独立完整真实 LLM root runs；其中一条位于 ≥10 轮历史 Session。CTX-5 使用隔离 userdata，
不得破坏用户已有 Provider 配置。

## Obligation mapping

| obligation | AC | 最小决定性证据 |
|---|---|---|
| TO-A10 | AC-10 | 同 snapshot id 出现在 RunStart、physical request、Inspector |
| TO-A11 | AC-11 | 六类 Context 参数化 request JSON + CTX-1/2/3 |
| TO-A12 | AC-12 | conversation allowlist/exclude canary/same-Run tool reasoning fixture |
| TO-A13 | AC-13 | success/cache/missing/failed/cancelled/unknown + cross-DB crash/reconcile |
| TO-A14 | AC-14 | generation/fingerprint/full schema 三方 exact equality |
| TO-A15 | AC-15 | ring/modal/hydration + CTX-1/3/5 真人点击 |
| TO-A16 | AC-16 | production reference graph + legacy symbol denylist |
| TO-A17 | AC-17 | multi-Session/concurrency/WAITING/restart/stop matrix |
| TO-R7 | AC-10,13 | request fingerprint、attempt、sample、UI lineage 可关联 |
| TO-R8 | AC-11,12 | sensitive/exclude/other-Session canary 不在真实 request_json |
| TO-R9 | AC-13,17 | duplicate/out-of-order/cross-DB fault reducer |
| TO-R10 | AC-14,17 | generation change + active/waiting/restart old catalog lease |
| TO-R11 | AC-15,17 | switch/reconnect/restart authority equality |

## 完成定义

八条 AC 与全部 obligations 均有 required PASS；实际 request、SDK ledger、Session authority、UI 共用
lineage；旧 production read 无未解释可达分支；自动化、类型/构建、critical/affected/full smoke、真实 LLM、
长历史、冷启动和 Computer Use 全通过；ARCHITECTURE 同步；最终状态只取 plan-test gate receipt。
