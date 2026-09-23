最后更新：2026-09-21 CST。V1.4（去除 NanoJev）仍未完成。后继修复 operation/action link 同身份重放假冲突（原反例 1 FAIL / 4 PASS，修复相关 28 PASS；旧 action 相邻回归 74 PASS / 1 原有条件 SKIP），并保持重复重放零写与 alias 原子拒绝。新增取消 Task 的真实在途/lease 检查，与原 repair 套件共 8 PASS；两个真实 SQLite 写事务交错与真实方法退役后的 UNKNOWN 读取均已通过（后续组合首轮另有 cycle 夹具失败，已修正）。compiler 拒绝保留 typed report，collector 不再将非四类缺陷归为 COVERAGE_GAP；未知 producer code 强制 INTERNAL_CONTRACT_ERROR。最新相关 181 PASS / 3 既有 codec SKIP（2.00 秒），3 个 preview/collector 源文件 mypy 与定向 Ruff 通过。此前 full_target 3891/5 与 H1-H 20/8/8 是前一源码检查点，尚未重新全量/矩阵汇总。Operation 上游 producer、延期恢复合同、H1-I/完整 H1、H2–H8 与 Host UI 仍待，候选未合并。架构裁定问题见 Host plan 的 PLAN-AGENT-架构裁定请求-2026-09-21.md。

最后更新：2026-09-21 CST。**V1.4（去除 NanoJev）整体未完成。** 候选 `codex/h1h-impl` / HEAD `102ad3dfa2db38d575ea929d39ec5ed1561a71da` 加保留的未提交改动，最新固定源码 full_target **3891 PASS / 5 SKIP / 137.29 秒**，1045 个 Python 源码/测试 hash 前后不变；5 个变更源码文件 mypy 通过。当前实际 H1-H matrix 为 **20 PASS / 8 PARTIAL / 8 NOT_COVERED / 0 FAIL**，exit 2，整门仍 OPEN。已完成本地修复：提交/最终decision原子恢复、UNKNOWN action保留预算、授权issuer/tenant/Mission隔离、原始reply CAS留存、两种固定decode-only先解码后拒绝；补齐A01/A03/I01/I04/P05/P08/P10等真实断言。历史8个旧fixture失败保留，补真实ArtifactStore后25定向及本次全量通过。真实 DeepSeek v4.1 Flash WAIT场景已完成（早于后继raw/授权修复）：190.609秒、40次物理串行调用全succeeded、4次WAIT注册/唤醒、4件accepted artifacts、所有reserved字段0；带测试调度/签发器，不代表Host UI或完整H1-I。Operation上游冻结身份/参数引用/物化链、其余门禁及H2–H8仍待；候选未合并、Host wheel未重装、原生UI未验。当前事实与原始证据索引见Host `plans/taskSys2/升级planV1/v1.4/WAIT复验-2026-09-21.md`，后文旧数字仅为历史。

# Handoff — V1.4 H1-H / H1-I（NanoJev 已移出）

**当前执行入口补充（2026-09-21）**：先读 [WAIT 复验](WAIT复验-2026-09-21.md)。最新候选 full_target **3812 passed / 5 skipped，138.38 秒**，545 个源码/测试 hash 不变；WAIT 事务、重启、终态请求包 v6/int5 和并发快照已修。旧 H1-H“36/36”规格映射错误，不能视为整门通过；仍需正确矩阵、强杀恢复、mutation 和独立核验。当前真实 WAIT 诊断采用逻辑调度 2 槽、物理 Provider 串行 1 槽，未签署 H1-I gate。整体任务仍 H1–H8（去除 NanoJev），不是只做 H1；保留脏树，未合并/重装/原生 UI 验收。Provider 历史空回复计数和 legacy 卡住归因已更正。

日期：2026-09-21（CST）  
当前范围：H1-H AdmissionContext → H1-I 真实模型 → 完整 H1 门禁

## 1. 接手入口

先读：

1. 本文件；
2. `ARCHITECTURE/index.md`；
3. `V1.4范围变更-2026-09-21-移出NanoJev.zh-CN.md`；
4. 原始历史 handoff：`HANDOFF-2026-09-20-HTN-H1-NanoJev.md`；
5. `simpleharness-v14-h1h-admission-amendment.zh-CN.md` 及 `h1h_admission_source-map.json`。

不要 `git reset --hard`、`git clean`、回滚、cherry-pick 或清理现有工作树。

## 2. 当前 checkout 身份

- Host：`/Users/denny/projects/simple_harness`，`main`，基线 `6c457908e49757035c21c6dc2b252415107a494c`，有未提交改动。
- SDK 主线：`/Users/denny/projects/simple-harness-sdk`，`main`，基线 `51dbed2eaf81d3225bcb15911c33838a79c1fcd3`，有未提交/历史 NanoJev 改动；本范围变更不要求清理。
- H1-H 候选：`/Users/denny/projects/simple-harness-sdk-h1h-impl`，分支 `codex/h1h-impl`，基线 `102ad3dfa2db38d575ea929d39ec5ed1561a71da`，有未提交 H1-H 改动。

未经独立核验，不得把候选结论写成主线或合并结论。

## 3. 已完成的 H1-H 实现进展

候选 worktree 已实现 H1-H authorization、operation identity/state、pure preview、commit guard、H3-H8 接线；H1-H stage matrix A01–A08/O01–O10/P01–P10/I01–I08 为 **36/36 PASS**，H3-H8 focused/adapter 共 **109 PASS**，SDK `full_target` 初始回归为 **3779 passed / 5 skipped**，生产接线修复后的最新回归为 **3784 passed / 5 skipped**。这些是候选 worktree 的当前证据，仍不等于最终 H1 gate。

H1-I 已按当前用户指定的 DeepSeek v4.1 Flash 跑完四类决定的真实模型→codec→Admission→Store→Adapter 流水线：REFINE、REPAIR/REPLACE_METHOD、DECLARE_BLOCKED、WAIT **4/4 PASS**。证据索引：SDK `.local-test-evidence/2026-09-21/v1.4-no-nanojev/h1-i-real-deepseek-v41-flash/four-decisions/`。

## 4. V1.4 当前剩余顺序

1. 保留并审阅 H1-H 36/36、full_target 最新 3784/5 skip 和 H3-H8 109 的证据索引；不要重置当前工作树；
2. 对 H1-I 四类 DeepSeek 结果补充真实 Orchestrator 最终 Plan Commit、运行中兄弟对账、synthesis/wakeup 与 usage 守恒；当前四类结果只关闭 decision seam，不关闭完整 H1-I；
3. 复核完整 hierarchical smoke 的真实失败：只读 `facts` 叶改写已有 `kvlib.py`，被 `read_only_existing_file` 拒绝，8 次后 `max_attempts_reached`；不得把它改记为 PASS；
4. 最后重跑或审阅 SDK `full_target`、旧模式固定回归、sentinel、mutation、recovery、legacy 和审计包；
5. 只有真实 Orchestrator H1-I 和完整 H1 门禁首次通过后，才可标记 `H1_COMPLETE`。当前用户已把模型约束改为 DeepSeek v4.1 Flash；不要自行切换到 GPT-5.6、Claude、Grok、NanoJev 或 Shadow。

## 5. NanoJev 范围裁定

以下全部为 `DEFERRED / OUT_OF_SCOPE_V1.4`：

- NanoJev runtime、checkpoint、真实模型和质量评估；
- PR-7 Host/SDK Shadow 接入；
- Shadow、Primary、promotion；
- HTN+Jev 专项测试作为 V1.4 门；
- `RETRY_OR_ESCALATE`。

已有 NanoJev 代码、候选 wheel、checkpoint 审计和专项收据保留为历史材料，不进入 H1 证据，不触发新实现。I08 当前替换为“无外部 Shadow 独立性”：没有 NanoJev/Shadow provider、配置和 checkpoint 时，H1 三个 producer 仍须真实运行，Commit 权限不依赖外部 Shadow。

## 6. 报告口径

每次报告必须分开说明：

- 主线 vs H1-H 候选 worktree；
- focused vs full gate；
- seam vs real Mission；
- H1-H vs H1-I；
- 事实 blocker vs 模型服务容量问题。

不得把历史 NanoJev 数字、fake/shadow 结果或候选 worktree 的 full_target 数字写成当前 H1 完成证据。

## 7. 2026-09-21 最新进展

- H1-I 生产入口新增回归 `tests/orchestrator/full_target/test_h1i_production_entry.py`：4/4 PASS，覆盖 V8 选择、retry identity、collector→Admission→preview→commit 拒绝与成功提交。
- 候选 worktree 最新 `full_target`：`3784 passed, 5 skipped`，退出码 0，126.93s。
- 真实 DeepSeek v4.1 Flash Orchestrator 已实际走到 `planning_decision → authorization/admission → preview/commit → Worker tool calls`；随后 provider 连续 HTTP 429，完整 Mission 未闭环。调用收据：`.local-test-evidence/2026-09-21/v1.4-no-nanojev/h1-i-real-deepseek-v41-flash/trace/new-protocol-authorized-commit2/calls.jsonl`，SHA-256 `c06039900bbefb53c9f08e2deb5ff7ccf21c246f832b2828fe3b522dfe7ac9d4`。
- 后续重跑已明确记录 `DECODED → ADMITTED → COMPILED → COMMITTED`，真实 Plan Commit 通过并产生 `plan_revisions=1`；Worker 侧累计 48 请求中 40 个 HTTP 200、8 个 HTTP 429，最终 runtime `runtime_unavailable`，`accepted_outputs=[]`。最新收据同一路径，SHA-256 `7d38ed572f507e3f96c1ad0c856d99de829bdc12e02fcdb9a1fd9bf0999307ec`。
- 当前 H1 gate 仍 OPEN。下一步只在 DeepSeek Worker runtime 稳定后继续真实 Orchestrator 四类决定，并验收 runtime sibling reconcile、synthesis/wakeup、usage conservation；不得把 Plan Commit 成功误记成完整 H1-I PASS。

## 8. 新 Provider 复验

- V1.4 real-provider 解析器已修复：优先级为 `SH_*` → Host `.env` 的 `DEEPSEEKER_*` → legacy `BASEURL/APIKEY`。此前旧解析器误用了 `ai.svtun.cn/v1`，造成 404。
- 新 Provider `api.qlsjs.xin/v1` / `deepseek-v4.1-flash` 短探针文本与工具调用均 PASS，Provider 回归 47 passed。
- 真实 hierarchical smoke 已通过：`1 passed in 250.22s`，`COMPLETED` / `verification_passed`，`plan_revisions=1`、`accepted_outputs=4`。收据：`.local-test-evidence/2026-09-21/v1.4-no-nanojev/h1-i-real-deepseek-v41-flash/trace/new-provider-smoke/report.json`，SHA-256 `2992f9af6d5454dc5ddb2992af42b3ffd4fed0813d64ec13d8ea5bc37bf28d15`。
- 这证明新 Provider 可以完成真实 Planner→Plan Commit→Worker→accepted outputs；完整 H1 gate 仍需按原四类决定和最终审计条件关闭。

## 9. 当前复验补充（2026-09-21）

- 新 Provider `api.qlsjs.xin/v1` / `deepseek-v4.1-flash` 短探针：文本与工具请求均 HTTP 200；Provider 回归 `47 passed`。
- H1-H stage runner：`36/36 PASS`，manifest `.local-test-evidence/2026-09-21/v1.4-no-nanojev/h1h-focused/h1h-run.json`，SHA-256 `fcc5d9f67a2deaf2b47c103df5c8be316b7c8bef5c55b2d2ff02bdf1b5319340`。
- 当前 `full_target`：`3784 passed, 5 skipped`，126.60s；旧模式固定集合：`560 passed, 13 skipped`，193.72s；sentinel `self._new_mode(mission)=19`（上限 22）。
- 当前 hierarchical smoke 命令返回 `1 passed in 144.33s`，但该次 Mission receipt 为 `FAILED/max_attempts_reached` 且 `accepted_outputs=[]`；pytest 的通过条件是结构化诚实停止，不是 Mission 成功。报告 SHA-256 `63046d5ec134b56186dbd5ac748d744e478eb612318a6f5cd89a702557ac17cc`。
- 结论：新 Provider 已可用，真实 Planner/Plan Commit 可进入；Worker 运行态仍未稳定收敛，H1-I 四类真实 Orchestrator gate、recovery/mutation/independent 审计仍未关闭。

## 10. Provider v4 提示修复后的真实复验（2026-09-21）

- Root Reviewer 的失败原因已定位为 DeepSeek 输出 `<cricit_verdict>` 的标签拼写错误；新增 `root-reviewer-v4` 提示版本，显式要求逐字输出 `<critic_verdict>`，严格解析器保持不变，v3 原始提示仍可回放。
- 模板冻结/回归：`19 passed`，Ruff 通过。
- 修复后真实 hierarchical smoke：`1 passed in 140.80s`，Mission `COMPLETED` / `verification_passed`，`plan_revisions=1`，`accepted_outputs=4`，Root Review `MISSION_FINAL / ACCEPT`，无 unreadable/unknown outcome。
- 收据：`.local-test-evidence/2026-09-21/v1.4-no-nanojev/h1-i-real-deepseek-v41-flash/provider-refresh-v4-123112/report.json`，SHA-256 `1271ae58b7fc038821df12185b9aa77a4e4c38aed865e5062ab90a9f30111bb6`；事件收据 SHA-256 `d997ca497576d2100919d086731ce20101d00da34b54427c693ad6615965ce05`。

本次只证明 Provider/Root Review 运行态已经可用；H1-H/H2-H8 的生产 caller、mutation、recovery 和独立审计仍按前述 PARTIAL/OPEN 状态处理。
