# Plan Challenge Round 6

> 结果：`VERDICT: FAIL`  
> 状态：2026-07-18 已逐项闭环，供下一轮只挑战新增/未闭环问题。

| Round 6 blocker | 闭环位置 |
|---|---|
| 删除 launcher adapters 会破坏 code/PPT，DeepResearch registry 构造时序不可达 | T1/T10：唯一 generic `WorkflowRuntimeAdapterRegistry` 覆盖 deep/code/PPT；DeepResearch 只是 typed view；prepare→register/seal→activate/recovery 两阶段启动 |
| effect 只 owner 顶层 result，locator/body 会悬空 | `v6-contracts.md` §4.1、T4/T5：search/page exact transitive effect closure，同事务 owner并写 auditable `artifact_refs_json` |
| snapshot DB 列写 wire ref 与 bare digest 规则冲突 | §1/§6.1、T12：历史 `manifest_ref` 列物理保存 bare snapshot blob digest；domain 才 format；移除旧 equality 假设 |
| assessment_hash 没有派生规则 | §6/T4：semantic hash/ID、完整 blob ref与双重验证 exact contract |
| public view 没有持久化出口 | §7/T10：移除 projection 顶层 public_raw；V6TerminalPublic 只嵌 final-status intent payload并随 outbox event提交；projection hash进入 frontier operation identity |
| unverified locator identity 可漂移 | §3：unverified final URL/hash固定等于 canonical，redirect chain为空 |

本轮确认 Round 5 的 fenced identity、typed outcome、delivery CAS、v19、snapshot方向均成立；新增修订保留 v1～v5 sync terminal 与所有非 DeepResearch workflow runtime adapters。
