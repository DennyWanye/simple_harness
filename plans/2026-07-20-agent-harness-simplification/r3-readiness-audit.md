# R3 就绪审查

日期：2026-07-20

## 硬性前置

- R2 的 `ProductTurnPreparer` 和 `RunPresenter` 已通过等价性门禁。
- 生产 owner 在 R3 期间保持 `legacy/0`。
- Kernel 只保留六个公开生命周期操作，不能吸收产品逻辑。
- ReAct 与 Workflow 必须通过同一个 tagged Driver 协议接入。
- 恢复相关写入必须在各自事务内验证租约 fence。

## 推荐切片

1. 建立 recovery claim / renew / release 和 epoch fence。
2. 从 Kernel 分离 contracts、唯一 `BoundedLiveIndex`、无状态 `DriverRuntime` 和 launch factory。
3. ReAct 统一使用 `append response -> save boundary -> resume`。
4. 为 tool、decision、child 覆盖同一个 continuation 契约。
5. 引入精确匹配 run / turn / call / effect / artifact 的 `EvidenceContext`。
6. 把 AgentLoop 的内部 dispatch、异步 handoff 和全局 Subagent drain 收进明确的旧兼容协作者，R6 前不提前删除生产语义。

## 预算

- Kernel / ports：预计净减少 `180～275 LOC`
- recovery lease：预计新增 `180～260 LOC`
- scoped evidence：预计新增 `80～130 LOC`
- ReAct 修正和 AgentLoop 抽离后，总体应持平或下降

早期 `combined core/UoW <=2,800` 目标已被真实 spike 证伪，后续改用 R4.5 的 core、authority 和 typed-transaction 门禁。

## R6 前保留的生产 owner

以下机制在 R3 不切换：`main._run_chat`、Code / DeepResearch / PPT 旧启动器、审批和 clarification waiter、AutoResume task map、Subagent completion queue、Voice 旧 AgentLoop bridge、AgentLoop legacy dispatch，以及 legacy workflow recovery / delivery。
