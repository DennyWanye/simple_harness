# Task 7 第一批集成结果（Capability / Skill foundation）

> 日期：2026-07-25
> 状态：第一批代码已集成并通过自动化；Task 7 仍在继续，不能视为完整 DoD。

## 本批已经落地

- workflow schema 由 17 升至 18，并在同一 migration owner 内把 Capability schema
  由 1 升至 2。
- Capability binding 增加 owner 与 management policy；新增 owner detail token、
  OwnerBindingSetStamp、RunCatalogContentStamp、ProcessCatalogStamp、run-catalog
  snapshot 与 lease-intent 基础表。
- 新增 CatalogGate、prepared runtime-set / managed-process Job 协议，以及 ProductVenue
  唯一 `prepare_run_catalog_lease()` 入口。
- 17 个 shipped Skill 分别迁移为独立 v2 Capability Pack；旧 builtin SKILL.md 内容副本
  已删除，Loader 的 script 执行面已关闭。
- `/<skill>` 不再直接执行 Loader，而是改写成主消息线程的 instruction activation 请求并
  重新进入 ProductVenue/Harness。
- 新增 v2 Skill/Workflow manifest parser、Personal Workflow 声明式 DAG parser、
  固定 Workflow adapter allow-list 与 first-party frozen Skill resolver foundation。
- `memory_recall` 从 planned authority 转为生产 active authority，并重生 checked execution
  build manifest。
- 修复 Windows 深层 immutable pack 目录超过 260 字符时 `SKILL.md` 已存在却被误判缺失的
  校验故障；校验器使用 long-path-safe I/O view，持久 identity 仍保留 canonical 普通路径。

## 自动化证据

- `backend/tests/companion`: `210 passed`
- `backend/tests/capabilities`: `165 passed`
- Skill / slash 联合回归：`83 passed`
- execution build manifest：`7 passed`
- Windows long-path + 17 first-party pack install 聚焦：`2 passed`
- `main` production composition import：通过
- `git diff --check`：通过（仅既有 CRLF 转换提示）
- 上述 pytest 命令退出后，按精确命令行复核 Python survivor：`0`
- 端口 `8100/5173` listener：`0`

## 本批没有宣称完成的边界

独立完整性审计确认本批仍只是 Task 7 foundation。以下内容继续阻塞完整 Task 7：

1. managed Skill 与 legacy SkillLoader 的生产投影仍需收敛为一个 frozen instruction truth；
2. slash、auto-disclosure、`skill_invoke` 仍需统一 `PreparedSkillInvocationScopeV1`；
3. run-catalog snapshot、intent、exact lease rows、projection receipt 仍需同一 Store
   transaction 与完整 release/recovery receipt；
4. Store-backed runtime-set / owner activation / lease rehydrate ledger 和 cold recovery尚未完成；
5. Personal Workflow 仍需接入正式 interpreter、Effect/UoW checkpoint 和 exactly-once recovery；
6. Task 6B 的 CurrentExecutionScope production authority 与三段物理 dispatch 仍未接入
   EffectBatchExecutor。

本轮因此只提交可独立回归的 foundation，不把 Task 7 状态改成 completed，也不进入
Task 8 的 Candidate 生产切换。
