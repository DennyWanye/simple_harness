# Slice A acceptance — SDK v0.1.1 candidate

> 状态：FROZEN（2026-08-16）  
> Parent acceptance：`../2026-08-13-simple-harness-sdk/acceptance.md`（内容不被本文件放宽）  
> 边界：只验收 SDK-owned candidate；产品 adapter、旧 authority 删除、数据库 reset、桌面 E2E、远端发布均不在本 slice。

| ID | Slice A 可验证出口 | 优先级 |
|---|---|---|
| SDK-AC-5-A | public durable Runtime 完成 ReAct、硬预算、durable authorization 与 SDK-owned delivery pump；真实 SQLite close/reopen/recover，物理 Tool 在 handoff fence 前调用数恒为 0。 | 必须 |
| SDK-AC-6-A | public `agent.general` composition 与 Host-owned Workflow registration 可用；同一主 Agent 能选择 official/host-owned Workflow，SDK 校验 catalog、ticket 与 frozen binding。 | 必须 |
| SDK-AC-7-A | `durable_task`、`personal_v1`、`capability_build` 三个 official Profile factory 经 public Runner 真启动；缺 optional Port 只使对应 Profile unavailable，完整 Ports 时默认开启。 | 必须 |
| SDK-AC-8-A | 四套 consumer conformance protocol 真执行；`0.1.1` wheel/sdist candidate 可 clean-install、可重复构建并带 BUILD_INFO/SBOM/NOTICE/SHA256SUMS；远端三平台与发布状态可保持 PENDING。 | 必须 |

## 不得用作 PASS 的证据

- SDK checkout、editable install、`PYTHONPATH` 或产品深层 import 不能替代 exact-wheel consumer test。
- 单元测试数量不能替代真实 SQLite restart、authorization temporal-fault、delivery crash/backfill 或四套 conformance。
- 本机平台结果不能冒充 Windows x64 / Linux ARM64 远端结果；未获 push/dispatch 授权时对应项必须明确 PENDING。
- 本 slice 不产生产品 cutover、数据库 reset、桌面真人 E2E 或远端 Release 已完成的声明。
