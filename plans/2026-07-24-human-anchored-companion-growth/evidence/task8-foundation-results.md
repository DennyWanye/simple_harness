# Task 8 Candidate foundation 执行结果

> 日期：2026-07-25
> 状态：foundation 已通过自动化；后续生产组合已完成，见
> [Task 8 生产集成结果](./task8-production-integration-results.md)

## 已完成

- `EvidenceClusterer` 按 owner/capability/root intent/time window 计算独立场景，显式
  retry link 折叠，不用模型重复文本凑证据。
- 严格 `StructuredGrowthProposalV1`、trusted root admission、host-only build permit 与
  immutable candidate draft receipt。
- Companion schema v3：显式用户 evidence、target、proposal bytes/hash、binding fences
  与 proposed build 同事务写入；durable build 状态机支持跨库崩溃恢复。
- workflow schema v19：child terminal event 与 immutable draft receipt 在同一 UoW
  commit；UPDATE/DELETE trigger 禁止修改 receipt。
- 确定性 candidate seed/version/manifest/archive/package identity，exact collision
  fail closed。
- Task 0 固定 package limits、Windows 路径/collision policy、Zip central index 与
  streaming bytes/CRC/hash 验证，以及 host-only validated package ref。

## 自动化证据

- Task 8 proposal/identity/coordinator/receipt/package-limit/schema/execution 邻接：
  `140 passed in 11.50s`。
- package 安全四套组合：`90 passed in 1.43s`。
- 关键模块 `py_compile`：通过。
- monitored package pytest：PID 16728，exit=0，survivor=0，
  released_private_bytes=811008。
- 140 项组合结束后按 exact venv Python + pytest command line 查询：
  survivor=0。

## 本阶段结束时尚未完成（现已由生产集成分片关闭）

- `CapabilityBuilderHost.finalize_child_completion()` 的 candidate-only output 与
  general-install guard。
- reserved builder child 的真实 execution precreate/start、固定 ToolSet 和生产 Runtime
  scheduler 注入。
- first-party/local/configured/git/companion-growth 四类 SourceResolver 与 Manager
  统一消费 `ValidatedCapabilityPackageRefV1`，以及安全 materializer 的 reparse/
  final-handle/nlink 检查。
- `create_candidate_from_builder_receipt()` 把 receipt、current fences、package blobs、
  attempt/source provenance 在一个 Companion transaction 提交。
- 上述生产接线的完整 crash/fuzz/S-7 与真人主消息页验收。
