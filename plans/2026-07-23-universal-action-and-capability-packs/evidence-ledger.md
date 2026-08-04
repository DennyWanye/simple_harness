# 通用行动与能力包平台 — AC 证据账本

> 日期：2026-07-24  
> 规则：`AUTO PASS / MANUAL PENDING` 不是最终通过。只有对应 required 真人场景有真实
> 输入、点击、应用窗口、截图、日志/只读 DB 对账和业务质量结论后才能改为 `PASS`。

| AC | 生产代码事实源 | 自动化证据 | 真人场景 | 当前 |
|---|---|---|---|---|
| AC-1 单入口通用 Agent | `harness/adapters/product_composition.py`、`product_profiles.py` | product composition/profile/no-mode tests | VS-1、B-5 | VS-1 PASS / B-5 PENDING |
| AC-2 工具事实接地 | `agent/turn_preparer.py`、`tools/orchestration_controls.py` | turn preparer、capability search/schema tests | VS-1 | AUTO + MANUAL PASS |
| AC-3 动态发现激活 | `capabilities/search.py`、`capabilities/hub.py`、control tools | capability search/hub/tools tests | VS-1、S-4、S-5 | VS-1 PASS / S-4、S-5 PENDING |
| AC-4 通用行动原语 | `tools/os_tools/*`、browser/desktop providers | OS registration + file/download/process/app tests | VS-2、S-1～S-3 | VS-2 PASS / S-1～S-3 PENDING |
| AC-5 任务级授权 | `permissions/policy.py`、`runtime.py`、`task_grants.py`、`execution_uow.py` | task grant + authorization UoW/runtime tests | B-2、VS-2 | AUTO PASS / MANUAL PARTIAL |
| AC-6 Auto 直行 | `permissions/policy.py`、`legacy_auto_mode.py`、settings projection | authorization policy/UoW + frontend cards/settings tests | B-3 | AUTO PASS / MANUAL PARTIAL |
| AC-7 环境准备 | environment/app/download tools + Godot pack | app/download + Godot detect/install contracts | S-1、S-2 | AUTO PASS / MANUAL PENDING |
| AC-8 UAC 边界 | external-wait contracts/UoW + `ExternalWaitDialog.tsx` | same-Attempt wait/cancel/restart + frontend dialog tests | B-4A、B-4B | AUTO PASS / MANUAL PENDING |
| AC-9 能力包清单 | `capabilities/manifest.py`、`store.py` | manifest/contract/hash/path tests | B-1、S-4 | AUTO PASS / MANUAL PENDING |
| AC-10 可执行能力包 | `local_runtime.py`、managed MCP/runtime lease adapters | local runtime, runtime lease, platform tests | S-5 | AUTO PASS / MANUAL PENDING |
| AC-11 来源与更新 | `capabilities/source.py`、`manager.py`、`store.py` | install/update/rollback/uninstall/crash-reconcile tests | B-1、S-4、S-5 | AUTO PASS / MANUAL PENDING |
| AC-12 Godot 样板 | `capability-packs/builtin/godot` + managed proxy | Godot pack `13 passed, 1 skipped`; platform smoke PASS | S-1、S-6 | AUTO PASS / MANUAL PENDING |
| AC-13 跨工具连续执行 | ReAct Driver + EffectBatchExecutor + catalog refresh | brokered effect/refresh/child integration tests | S-1～S-3、S-5 | AUTO PASS / MANUAL PENDING |
| AC-14 真实完成守门 | Receipt/Artifact/VerifyGate + capability receipts | receipt/failure/verify/outcome tests | VS-2、S-1～S-3 | VS-2 PASS / S-1～S-3 PENDING |
| AC-15 模型驱动有界修复 | `harness/attempts.py`、ReAct failure collection | task-attempt foundation + loop guard + failure trust tests | S-4、S-6 | VS-2 旁证 PASS / required MANUAL PENDING |
| AC-16 取消与恢复 | `harness/kernel.py`、`user_continuations.py`、process leases | cancel/recover/late-effect/process/runtime-lease suites | B-3、B-5、S-6 | AUTO PASS / MANUAL PENDING |
| AC-17 过程可见 | Presenter + workflow/capability cards + task windows | presenter/frontend workflow/capability operation tests | B-1、B-3、S-1～S-6 | AUTO PASS / MANUAL PENDING |
| AC-18 默认启用与兼容 | product composition + production reference gates | default-on/no legacy/no semantic route/LOC/authority gates | VS-1、B-7 | VS-1 PASS / B-7 PENDING |
| AC-19 Godot 塔防 E2E | 通用根 + Godot pack + OS primitives | 组件自动化不替代业务价值 | S-1 | MANUAL PENDING |
| AC-20 Blender E2E | 通用根 + OS/file/Shell/app primitives | 组件自动化不替代 `.blend`/渲染质量 | S-2 | MANUAL PENDING |
| AC-21 Web E2E | 通用根 + file/Shell/process/browser primitives | 组件自动化不替代浏览器 CRUD/刷新 | S-3 | MANUAL PENDING |
| AC-22 缺能力诚实失败 | pack integrity/failure receipts + model feedback | bad-hash fixture/pack manager/failure receipt tests | S-4 | AUTO PASS / MANUAL PENDING |
| AC-23 缺能力自动自建 | `capabilities/builder.py` + builder Profile | builder admission/search evidence tests | S-5 | AUTO PASS / MANUAL PENDING |
| AC-24 生成工具验证门 | builder staging validator/managed worker policy | happy/error/health/effect path + unsafe worker rejection | S-5 | AUTO PASS / MANUAL PENDING |
| AC-25 作用域持久化 | capability revision/binding/store + scope resolver | run/project/user scope + restart rehydrate tests | S-5 | AUTO PASS / MANUAL PENDING |
| AC-26 版本升级回滚 | immutable version + binding CAS + pack manager | update/rollback/publish crash/reconcile tests | S-5、B-1 | AUTO PASS / MANUAL PENDING |
| AC-27 同任务目录刷新 | `capabilities/refresh.py` + ReAct boundary refresh | atomic refresh/forged ref/crash rollback tests | S-5 | AUTO PASS / MANUAL PENDING |
| AC-28 自修复闭环 | derived revision/repair inputs + Attempt loop | pack update/rollback + failure/strategy guard tests | S-5、S-6 | AUTO PASS / MANUAL PENDING |
| AC-29 核心/生成代码隔离 | builder managed JSON worker/MCP proxy policy | native/unsafe generated worker rejection + crash/timeout tests | S-5 | AUTO PASS / MANUAL PENDING |
| AC-30 单 Session 并行隔离 | `BoundedLiveIndex`、task windows、task_scope/workspace | kernel capacity/isolation/cascade cancel + frontend store tests | B-5、B-6 | B-6 cleanup PARTIAL / B-5 PENDING |
| AC-31 模型选择 Profile | `turn_preparer.py`、`subagent_registry.py`、profile ticket UoW | exact catalog/no semantic tags/one-shot crash recovery tests | B-7 | AUTO PASS / MANUAL PENDING |
| AC-32 Attempt 失败重规划 | TaskGoal/PlanVersion/Attempt/FailureSet UoW + ReAct Driver | ordered failures/restart/replay/loop budget tests | S-6 | VS-2 旁证 PASS / S-6 PENDING |
| AC-33 运行中续聊恢复 | schema v16 `execution_user_continuations` + coordinator + React marker | FIFO/restart/terminal race/cancel race/history-id tests | B-5 | AUTO PASS / MANUAL PENDING |

## 自动化总门

- 聚焦：`206 passed`
- backend：`5961 passed, 16 skipped, 9 deselected, 4 xfailed`
- Frontend：`85 files / 820 tests`，TypeScript PASS
- Rust：`73 passed`，build/check PASS
- Godot pack：`13 passed, 1 skipped`
- capability platform、construction、authority：PASS
- strict last-mile：7/7 PASS，0 fail，0 skip，`DECISION: SHIP`

## 真人结果入口

真人结果将写入：

- `testcase/2026-07-24-universal-action-capability-platform/manual-test.md` 结果账本；
- `plans/2026-07-23-universal-action-and-capability-packs/manual-results-2026-07-24/`
  的截图、日志、hash、只读 SQL、应用产物与结果报告。
