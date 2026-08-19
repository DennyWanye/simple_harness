# Program Plan: 两个 SDK 生产化（P0 + 云端 embedding）

> 创建日期：2026-08-19
> 状态：已与用户确认范围与顺序
> 覆盖仓库：`simple-harness-sdk`、`simple-harness-memory-sdk`、`simple_harness`（消费侧）
> 首个消费者：AIPhone（Sony Xperia 5 / Droidian ARM64）

## 目标

把 `simple-harness-sdk`（Agent Runtime 大脑）与 `simple-harness-memory-sdk`（长期记忆）从
"单元测试能过、但不足以安全存真实用户数据"推进到"P0 发布阻断全部闭环"。记忆侧的向量策略
按用户决定采用**云端 embedding**（手机不跑本地大模型），首版先以 Hash 离线向量跑通全管道。

## 范围边界

- **做**：评审文档的 P0（harness v0.1.4 发布前 8 项 + memory shadow-write 前门禁）+ 云端 embedding adapter。
- **挂起**：P1（模型开始使用 recall 前的门禁：资源上限/注入安全/损坏隔离/privacy canary/批量 salience）、
  P2（LLM facts / World Model / 副作用工具 / 自动恢复 / 本地 BGE-M3）。

## Slice 分解

| Slice | 仓库 | 内容 | 风险 | 依赖 |
|-------|------|------|------|------|
| **H1** | `simple-harness-sdk` | v0.1.4 发布阻断收尾（8 条 MUST AC，见 `simple-harness-sdk/plans/2026-08-19-sdk-v0.1.4-hardening/acceptance.md`） | 中低 | — | **✅ 完成**（finalize PASS，receipt `c5f546cd`，1226 passed） |
| **M1** | `simple-harness-memory-sdk` | 只读召回拆分、隐私日志、fail-open twin 修正、定 async `Embedder` 协议 + Hash 默认实现 | 中 | — | **✅ 完成**（finalize PASS，receipt `bf594aa2`，61 passed） |
| **M2** | `simple-harness-memory-sdk` | schema version / migration / checksum、append→facts→supersede 原子事务、幂等键 + 唯一约束 | 高 | M1 | **✅ 完成**（finalize PASS，receipt `eac81d14`，66 passed，version 0.2.0） |
| **M3** | `simple-harness-memory-sdk` | 级联删除、embedding lineage + reindex、单条/DB 增长上限、retention | 高 | M2 | **✅ 完成**（finalize PASS，receipt `dcef5d80`，75 passed） |
| **M4** | `simple-harness-memory-sdk` | 云端 embedding adapter：provider 配置 + 凭证、async embed、批量 + 缓存、超时/重试、离线回退 Hash | 中高 | M1 | **✅ 完成**（finalize PASS，receipt `f7a3ee2b`，83 passed） |
| **C1** | `simple_harness` | 消 `verify_sdk_wheel.py` 的 `0.1.1` 硬编码 SHA、重新 vendor 两个 SDK 成品 wheel | 低 | H1 + M 系列 | **✅ 完成**（finalize PASS，receipt `3ebe0ac0`） |

## 顺序

H1 先；M1 → M2 → M3 串行；M4 依赖 M1（放在 M2/M3 之后）；C1 最后（等 H1 + M 系列出制品）。

## 执行约定

- 每个 slice 在**所属仓库**内独立跑一遍 plan-test（full 路径），独立验收、独立 `finalize`。
- 各 slice 的 `acceptance.md` / `plan.md` / `verification/` 落在该仓库 `plans/<date>-*/` 下。
- 本文件是"地图"；每个 slice 的 `acceptance.md` 是"唯一真相来源"，两者冲突以 slice acceptance 为准。
