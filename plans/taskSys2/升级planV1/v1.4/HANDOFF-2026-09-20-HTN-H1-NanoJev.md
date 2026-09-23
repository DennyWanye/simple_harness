# Handoff — HTN / H1-H / NanoJev 当前状态

日期：2026-09-20（Asia/Shanghai）  
用途：交给下一 Codex session 继续编排与开发。  
当前角色约束：主 session 只编排、审核和验收；不要在主 session 直接运行测试。用户已明确：**不再使用 Claude CLI 子代理，也不使用 Grok**；只可使用 `gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna` 子代理。当前 5.6 服务曾多次返回 capacity error，应缩小任务、避免空等；不要把模型容量错误写成代码 blocker。

## 1. 双仓与当前状态

### Host

- 路径：`/Users/denny/projects/simple_harness`
- 分支：`main`
- HEAD：`6c457908e49757035c21c6dc2b252415107a494c`
- 工作树有未提交改动，包含 PR-7 Host 接线、候选 wheel/manifest、专项测试与架构文档。**不要 `git reset --hard`、不要清理工作树、不要直接合并候选 worktree。**

### SDK 主线

- 路径：`/Users/denny/projects/simple-harness-sdk`
- 分支：`main`
- HEAD：`51dbed2eaf81d3225bcb15911c33838a79c1fcd3`
- 工作树有未提交 NanoJev/PR-7 相关改动，包括 `decision/`、`event_handler.py`、real runner 和测试。

### H1-H 候选 worktree

- 路径：`/Users/denny/projects/simple-harness-sdk-h1h-impl`
- 分支：`codex/h1h-impl`
- HEAD：`102ad3dfa2db38d575ea929d39ec5ed1561a71da`
- 有未提交改动：
  - `src/agent_orchestrator/orchestrator/event_handler.py`
  - `src/agent_orchestrator/storage/planning_decision_store.py`
  - `tests/orchestrator/full_target/test_h1h_wiring.py`
  - `tests/orchestrator/full_target/test_planning_decision_store_rebind.py`
- H1-H 候选不是 SDK 主线，任何候选结论都必须注明 worktree 和 SHA；未经独立核验不得 cherry-pick。

## 2. 原始计划与权威文档

按以下优先级读取，不要只看旧报告：

1. `plans/taskSys2/升级planV1/v1.4/simpleharness-llm-native-htn-execution-plan-v2.zh-CN.md`
2. `plans/taskSys2/升级planV1/v1.4/LLM-native-HTN计划V2-裁定补遗-2026-09-18.zh-CN.md`
3. `plans/taskSys2/升级planV1/v1.4/执行workplan-HTN-H1-NanoJev-2026-09-19.zh-CN.md`
4. H1-H 任务书目录：`plans/taskSys2/升级planV1/v1.4/任务书-H1-2026-09-19/`
5. PR-7 任务书：`plans/taskSys2/升级planV1/v1.4/任务书-PR7-2026-09-20/pr7-impl.md`
6. NanoJev 增量计划：`plans/taskSys2/升级planV1/v1.4/NanoJevAdd.md`
7. 用户专项测试原文归档：`plans/taskSys2/升级planV1/v1.4/HTN-Jev专项测试需求-2026-09-19.md`
8. Host 用户提供原始测试文档：`/Users/denny/Downloads/simpleHarness_HTN_Jev_专项测试需求.md`

专项文档的测试终点是局部 HTN + Jev + Shadow 行为，不能替代完整 H1-H 门禁；Primary、完整 TaskGraph、RETRY_OR_ESCALATE 后续语义不是本轮自动完成项。

## 3. 已完成且有证据的部分

### HTN + Jev 专项

证据根目录：`/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-20/`

- T01：已修复 vacuous 计数断言。测试侧 profile hook 确实包住真实 `refine(...)`，`profiled_calls > 0`，Jev/Worker/business 调用均为 0；focused 结果 `1 passed`。证据：
  - `htn-jev-special/t01-call-counter-sol.md`
  - `htn-jev-special/t01-call-counter-junit.xml`
  - `backend/tests/htn_jev_focus/test_t01_htn_leaf.py`
- T02/T03/T04：已有 focused PASS，分别覆盖 grounding/准入、recursion fuel、Jev adapter/Shadow contract。
- T05/T06/T07：focused PASS，但只属于 DecisionService/Host seam，使用临时 provider，不能写成真实模型质量或完整 Mission 验收。
- T08：本轮 focused rerun `4 passed, 27 deselected`，覆盖乱序、候选隔离、replay/晚到结果不覆盖正式结果。证据：
  - `htn-jev-special/t08-current-luna.md`
  - `htn-jev-special/t08-current-luna-junit.xml`
- T09：双 ready Task 真实 Host Mission 已完成，SQLite 写入 `DecisionRequested=1`、`ShadowDecisionProduced=1`，两事件同一 `mission_id`/`njr-* decision_id`；正式生产仍完成两个 task，Shadow 没有裁剪生产 grants。证据：
  - `htn-jev-special/t09-live-two-task-luna.md`
  - `htn-jev-special/t09-live-two-task-terra.md`
  - `htn-jev-special/t09-live-two-task-raw.json` / `t09-live-two-task-raw-terra.json`
- T09 grants 独立 probe：在同一完整 allocator 输入下 off/on 两个 `AllocationPlanV2.to_json()` 完全相等；grants、scores、eligible、slots、open_attempts、refusals 六项不变性均为 true。Shadow provider 被控制为不同候选，避免用 `agreement=true` 代替不变性。证据：
  - `htn-jev-special/t09-grants-closure-terra.md`
  - `htn-jev-special/t09-grants-closure-terra.raw.json`
- 限制：该 grants probe 的 Shadow observation 与 `allocate_v2` 仍是两个 API 入口，不能宣称 Shadow 参与 v2 排序；probe-level invariance 不是完整产品级证明。

### NanoJev R02

- checkpoint 已确认身份：`/Users/denny/.cache/nanojev-install/checkpoint`，权重与 NanoJev release README 的 SHA 一致；`Qwen/Qwen3-0.6B` 是 backbone，不是“普通 Qwen 替代 NanoJev”。详细审计：
  - SDK：`/Users/denny/projects/simple-harness-sdk/.local-test-evidence/2026-09-20/nanojev-checkpoint-audit-deepseek.md`
- 真实 R02 runner 已执行：12/12 有效、0 adapter error、MPS、warm p50 约 129.9 ms；明确答案命中 2/8，ambiguous 4/4 落在合法集合；候选换序共 6/6 稳定（原 4 + 补充 2）。详细证据：
  - `htn-jev-special/r02-real-deepseek.md`
  - `htn-jev-special/r02-extra-reorder-deepseek.md`
- 解释：2/8 是当前 checkpoint 的小样本质量测量，不是自动工程 FAIL，也不支持 Primary promotion。不要调 gate、不要据此下载/替换模型。

### PR-7 candidate/runtime

当前权威本地 candidate 不是临时 `fa33...` wheel，而是：

- 版本：`0.13.0.dev20260920`
- wheel：`backend/vendor/simple_harness_sdk-0.13.0.dev20260920-py3-none-any.whl`
- wheel SHA-256：`9687d023c3fc9bc00c990c35c2827e035c3e56fbda49659e91acefc7fcfd2ef1`
- manifest SHA-256：`0fe8c0b5692e3d841d7ddcc5ab1e20fcf4d6cb4b2f54e2c39a87e5d203dad2a8`
- source commit recorded in manifest：`51dbed2e...`
- Host `.venv` 的 `direct_url.json` 精确指向 vendor wheel，无 editable install、无 `PYTHONPATH` 依赖。
- candidate/Host/SDK 定向检查：`71 passed` + `42 passed`；`uv lock --check` 通过。
- 证据：`htn-jev-special/candidate-closure-terra.md`

## 4. 未完成的真实门禁

### H1-H：当前主 blocker

H1-H 候选 worktree 不是主线，且仍未完成：

- `AdmissionContext.authorization`：没有 planning-lane 权威 authorization snapshot producer。不能用 method-level `GateDecision` 冒充 mission authorization，不能填 `True`/空 tuple/环境变量/模型 raw output。
- `AdmissionContext.operations`：没有 operation occurrence 到 UNKNOWN/reconciled 状态的权威映射；`operation_bindings` 只有 envelope，legacy actions 状态没有可靠 occurrence 关联。
- `AdmissionContext.plan_shape`：没有 candidate proposal → `PlanShapeView` 的准入前纯预检。不能把已编译 delta 的 `validate_delta()` 事后结果冒充准入前检查。
- H1-H focused 近期结果为 `19 passed / 13 failed`；不能进入独立核验。
- M14 非法 `package_version` focused 已有 `3 passed`，但只是局部收口，不是 H1-H 完成。
- 历史 `full_target 3699 passed / 5 skipped / 0 failed` 来自未提交候选 worktree，不能视为主线证据。
- legacy 曾为 `1960 passed / 7 failed / 20 skipped`；7 项在 clean baseline 也复现（6 缺 `tiktoken`，1 AST hash baseline 漂移），但当前候选仍需记录“0 新失败”而不能只引用旧收据。
- sentinel、当前 SHA 独立核验、旧模式 560、mutation 完整门禁未闭合。历史 mutation `20 killed / 4 survived` 不能自动代替当前 H1-H 验收。
- H1-H blocker 事实收据：
  - `simple_harness/.local-test-evidence/2026-09-20/h1/h1-gate-closure-sol.md`
  - `simple_harness/.local-test-evidence/2026-09-20/h1/h1-admission-sources-terra.md`
  - `simple_harness/.local-test-evidence/2026-09-20/h1/auth-producer-sol-final.md`

### 其他边界

- `RETRY_OR_ESCALATE` 当前不接线，除非用户/计划新增明确裁定。
- Primary 不实现、不切换、不做 promotion。
- 不下载新 checkpoint，不把 2/8 质量结果当作模型替换依据。
- `.local-test-evidence/` 永久 ignored；原始 JSON/XML/log 不得 `git add -f`，Git 只保存小型结论和索引。

## 5. 下一 session 的执行顺序

不要从报告重新开始调查。按以下顺序执行：

1. **锁定 H1-H 候选 worktree并做单字段实现**：先为 authorization snapshot 找到真实 governance/approval producer；如果仓库确实没有该 producer，先扩展 H1-H 任务书和 allowlist，新增最小 typed producer/store contract，再实现；不得伪造默认授权。
2. **补 operations producer**：只读同一事务中的 operation binding + explicit occurrence/reconciliation 状态；无法一一映射时 fail-closed，并写 focused tests。
3. **补 plan-shape precheck**：对可构造的 REFINE/REPAIR candidate 做无副作用 compile/validate 预演；WAIT/NO_CHANGE/DECL_BLOCKED 不得伪造 shape 通过。
4. 每完成一个字段，先跑受影响 focused tests，记录候选 worktree SHA 和 diff；不要立刻跑全量。
5. 三字段和 H1-H focused 绿后，再由独立 5.6 reviewer 审核新旧协议、双事件、ordinal/retry、WAIT/NO_CHANGE、仅解码决定、replay/idempotency。
6. 最后才按原计划运行必要完整门禁：SDK `full_target`、旧模式 560、0 新失败、sentinel ≤22、mutation ≥12 killed、独立核验；若环境失败，分开记录 baseline/environment，不要把它们抹掉。
7. H1-H 主线同步必须逐文件审核候选 diff；先不要 commit/push，除非用户明确要求。
8. 完成任一 slice 后同步 SDK/Host `ARCHITECTURE/` 事实源与 PROJECT_STATUS；未通过验收的不要标完成。

## 6. 编排记录

- 已使用的子代理：GPT-5.6-sol / terra / luna；用户现已禁止 Claude CLI 和 Grok。
- 5.6 曾因 capacity error 多次失败；任务应保持小、单文件/单字段、明确输出，不要派大而泛的 36+ 轮任务。
- 主 session 没有直接运行测试；测试由子代理/已记录的 runner 产生。
- 任何下一次报告必须区分：主线 vs 候选 worktree、focused vs full gate、seam vs real Mission、identity vs quality、事实 blocker vs 服务容量问题。

## 7. 本 handoff 的交接要求

接手后第一步：读取本文件、上述原始计划、`ARCHITECTURE/index.md`，再执行 `git status`/`git rev-parse` 只读核对；不要重跑已经有收据的专项测试。  
接手后第二步：在 H1-H 候选 worktree 派发单字段实现任务，不要等待用户再次确认。  
接手后第三步：每次进度报告必须给三张表：总体进度、当前部分细节、距上次报告增量。

## 2026-09-21 范围变更补记

用户已批准将 NanoJev 相关工作全部移出 V1.4 当前交付。NanoJev runtime/checkpoint、PR-7、Shadow、Primary、HTN+Jev 专项和 `RETRY_OR_ESCALATE` 均延期，不再派发新任务，也不计入 H1-H、H1-I 或完整 H1 门禁。

原本文档中的 NanoJev/PR-7 记录是历史事实，保留不改；不要据此重新加载 checkpoint、构建候选 wheel 或清理工作树。当前执行入口改为：

`plans/taskSys2/升级planV1/v1.4/V1.4范围变更-2026-09-21-移出NanoJev.zh-CN.md`

H1-H I08 改为“无外部 Shadow 独立性”：没有 NanoJev/Shadow provider、配置和 checkpoint 时，H1 三个 producer 仍须真实运行，Commit 权限不得依赖外部 Shadow。H1-H、H1-I 和完整 H1 的未完成状态保持不变。
