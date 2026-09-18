# 交接：Grok 验收第 6 批收尾 → 0.12.2 → LLM-native HTN 升级（2026-09-18 16:25 写）

写这份的原因：本会话额度（Fable 周额度）只剩约 9%，周二 22:00 重置。任何人接手都按本文继续，不需要读会话记录。

## 1. 现在的位置

- SDK 主干 `simple-harness-sdk` main = **c0e13a4**（0.12.2 候选第 6 版 = P2.3e–P2.3v，已推送）。工作树干净。临时 worktree：`simple-harness-sdk-p23v`（分支 p2.3v-repeated-failure-early-stop，已全部 ff 进 main，可 `git worktree remove`）。
- Host 主干 `simple_harness` main = 8ec2d2c0 之后本文提交。
- Grok 验收证据（gitignored）：`.local-test-evidence/2026-09-16/htn-acceptance/`
  - `FREEZE.json` = c0e13a4；`runs/h-arm/` = 第 6 批（9 局：并发采样器 C1×2、结算流水线 C2×2、分页 C4×2、先取证再选方法 M2-r1、父子共读 M3×2）；`runs-f2dfa64-batch5/episodes/` = 第 5 批 5 局 COMPLETED（C3×2、M1×2、M2-r0，未重跑）；其余批次归档 `runs-*`。
  - 流水线：`logs/pipeline-batch6.sh`（回归+重冻结，已跑完）→ `logs/pipeline-batch6b.sh`（只做题，日志 `logs/pipeline-batch6b.log`，结束戳「BATCH6 RERUN COMPLETE」）。runner 有身份保护：换 SDK 提交必须新 `--out` 目录（JOURNAL §31）。
  - 密钥看门狗 `logs/token-watchdog.sh` v4（pid 见 `pgrep -fl token-watchdog`），日志 `logs/token-watchdog.log`。**收尾最后必须** `uv run --project backend python backend/scripts/grok_build_runtime.py restore` 并 kill 看门狗（按 PID）。
  - runner 日志册 `runner/JOURNAL.zh-CN.md` §25–§31。
- 第 6 批已落 6 局（第三层）：C2×2、C4×2 真完成（官方 PASS + COMPLETED）；C1×2 官方 FAIL 但 COMPLETED（raw false_completion=2），专项诊断 `impl/Grok验收-第6批C1r1假完成诊断-2026-09-18.zh-CN.md` 判定：模型漏做题面未明示的边界 + 题目 hidden 与 construction 不对齐，非编排缺陷；验收报告口径拆两档「编排假完成（阻塞）/ 隐藏测试未覆盖（不阻塞）」。第四层 3 局跑着。

## 2. 用户 2026-09-18 指令

1. 第 6 批跑完即停，不再起新批。
2. 做好记录和修复，保证以后能从这里重新起跑。
3. 代码提交主分支。
4. 然后做 HTN 标准化输入输出升级：`plans/taskSys2/升级planV1/v1.4/simpleharness-llm-native-htn-execution-plan.zh-CN.md`（先只做 Phase H1）。
5. 报告规则：中文、大白话、零代号（不写 N14/C3/r0/P2.3t/英文字段），题目按内容称呼，每列首次出现要解释。

## 3. 收尾步骤（按序）

1. 等 `logs/pipeline-batch6b.log` 出现「BATCH6 RERUN COMPLETE」。
2. Grok 整批诊断：任务书 `scratchpad/grok-prompts/diagnose-batch6.md`（内容也可按第 5 批诊断 `impl/Grok验收-第5批诊断-2026-09-18.zh-CN.md` 的格式重写），用 `grok_task.sh diagnose-batch6 <Host仓库> <任务书> high 80`；产物 `impl/Grok验收-第6批诊断-2026-09-18.zh-CN.md`。
3. 若诊断有确定性编排 P0/P1：在 SDK 新 worktree 分支修（测试先行）→ 独立核验（分离副本、≥3 变异、full_target 全量 ≥2960/2、旧模式 560/13/0）→ 归档核验报告到 `plans/2026-09-16-full-target/P2.3c/reviews/` → ff main → 推送。无则跳过。
4. 记录：Grok 写验收报告（§21.5 口径：14 局 = 9 局 c0e13a4 + 5 局 f2dfa64，分别标来源；硬不变量逐局；false_completion 两档；shared_reuse「机制未覆盖」）到 `impl/`，以及 P0–P2 总报告 §6；runner JOURNAL 加「第 6 批收官」一节；本文更新「已完成」。
5. 提交推送 Host main、SDK main。
6. 发布 0.12.2（沿用 0.12.1 流程：SDK `docs/release/v0.12.2.md` + CHANGELOG 发布段 + tag；Host vendor 钉版 / manifest / `sdk_candidate.py` / uv.lock；中文发布说明放 `impl/`；把 `impl/sdk-docs-release-v0.12.1.md` 移进 SDK docs/release/）。若诊断有编排 P0 未修，不发布，只记录。
7. `grok_build_runtime.py restore`；kill 看门狗；`git worktree remove simple-harness-sdk-p23v`。

## 4. 以后重新起跑验收的方法

- 新 SDK 提交 → `logs/refreeze-prep.sh`（自检 → freeze-candidate → 密钥写入）→ `cp FREEZE-candidate.json FREEZE.json` → 新建 `--out runs/h-arm-<sha>`（或先把旧 `runs/h-arm` 改名归档）→ `run_h_arm.py --layers … --tasks … --reps … --workers 2 --out …` → `pair.py` → 诊断。预算 `budget-L2.json` L3_L4 = 320 次 / 7200 s / 16M（只是保险丝），runner attempts 48、per-turn 32/96、max_root_review_repairs 2。
- 起跑前起看门狗（密钥 6 小时寿命，`grok_build_runtime.py refresh` 续期）。

## 5. LLM-native HTN 升级怎么开始

- 先 H0：记录当时主干 HEAD、full_target 数量、旧模式数量、真实模型场景清单，不改功能。
- 再 H1（计划 §83 范围）：`contracts/planning_decisions.py`（Envelope/Type/Subject/ReasonRefs/AssumptionHint/RefineDecision/EvidenceRequestDecision）、`planning/decision_codec.py`、`planning/decision_admission.py`、旧 PlanProposal / MethodProposal 两个适配器、Planner prompt v1（旧 pin 保留）、反例测试（malformed、引用包外对象、系统字段不可由模型设置、round-trip）。验收：旧行为不变、新信封往返一致。
- 工作方式不变：Grok 实施 / 另一 Grok 会话核验 / 报告归档 / fable 只做升级。计划文件写的基线是 f2dfa64，实际以当时主干为准。

## 6. 已完成（接手者更新）

- [ ] 第 6 批跑完
- [ ] 整批诊断
- [ ] 修复（如有）
- [ ] 记录与验收报告
- [ ] 提交推送
- [ ] 0.12.2 发布
- [ ] restore + 看门狗关闭 + worktree 清理
