# R3 Readiness Audit

> 日期：2026-07-20  
> 状态：设计可执行；必须先让 R2 parity 转绿。

## 硬前置

- `RunKernel.recover()` 目前只靠进程内 `_active`，没有跨进程 recovery lease/fence；`owner_generation` 是部署 fence，不能复用为单 run 租约。
- live index 只限制 run 数，per-run events 与 subscriber queue 无界；durable observe 未从 UoW hydrate events。
- ReAct child accepted/terminal response 未可靠写回 canonical boundary；legacy collaborator resume 忽略 response。
- child capability subset 目前只进入 hash/row，没有真正限制 AgentLoop schema 与 prepare_call。
- completion evidence 仍以 session 为范围，旧 turn 同名 receipt 可能让新请求假完成。

## 推荐切片

1. `ports.py` 收敛为单一 tagged `DriverEvent` / `DriverSignal`，删除长期双 taxonomy。
2. additive schema + UoW 增加 recovery claim/renew/release 与 epoch fence；所有恢复写校验 fence。
3. 把 contracts、唯一 `BoundedLiveIndex`、无状态 `DriverRuntime`、launch factory 从 Kernel 分离；Kernel 只保留六操作、一次 route、生命周期和租约协调，目标 500–620 LOC。
4. ReAct 使用统一 `append response → save boundary → resume`，tool/decision/child 全覆盖；capability snapshot 同时限制 schema 与 prepare。
5. 新路径引入 run/turn/call/effect/target/artifact 全匹配的 `EvidenceContext`；不匹配或缺失返回 unknown。
6. 最后把 AgentLoop 的 internal dispatch、accepted_async/product handoff、全局 subagent drain 移到明确的 legacy collaborators；R6 前生产仍调用这些兼容 owner，不能提前删除语义。

## 预算

- Kernel/ports：预计净减 180–275 LOC。
- recovery lease：预计新增 180–260 LOC。
- scoped evidence：预计新增 80–130 LOC。
- ReAct 修正与 AgentLoop 抽离：总量应基本持平或下降。
- 当前 R1 余量只有 462 LOC，每个切片都必须跑机械 LOC；R5 前 combined core/UoW 仍须达到 `<= 2,800`。

## R6 前保留的生产 owner

`main._run_chat`、Code/DR/PPT 前置 workflow 启动、plan/permission/clarification waiters、AutoResume task maps、Subagent completion queue/cancel_all、Voice legacy AgentLoop bridge、AgentLoop legacy dispatch/accepted_async handoff、legacy workflow recovery/delivery 均保持生产独占，直到 R6 单次 activation commit。
