# Host 验收 runner：D2a/N2 签名 + N8/N9/N10 参数（2026-09-18）

- 角色：Host 验收 runner 实施者
- SDK：`d360750`（未改）
- 范围：只改 `.local-test-evidence/2026-09-16/htn-acceptance/` 下 runner/、`code-tasks/C{1,2,4}/construction.json`、`selftest.py`、`selftest_runner.py`、`JOURNAL.zh-CN.md`、`FREEZE-candidate.json`（重生成，未覆盖 `FREEZE.json`）与本记录
- 依据：同目录 `Grok验收-第4批L3诊断-2026-09-18.zh-CN.md` §4 N8、N9（runner 半边）、§9 Q3 修法 1、§11

## 指挥者已改（核过，未改正）

`git diff` 对 ignored 目录为空。按文件：

1. `budget-L2.json` `L3_L4.provisional_per_episode_budget`：calls 160 / seconds 3600 / total_tokens 8,000,000。仍是 provisional，H/F 同预算。
2. `run_h_arm.py` / `run_f_arm.py`：`max_model_calls_per_turn=16`、`max_tool_calls_per_turn=48`（N8，与局级 calls 解绑）。
3. 同上 `max_attempts=24`（N10b）。

## 本轮改动

- **D2a/N2**：C1/C2/C4 根目标 `code.implement-contract`，准则 `c-contract-tests-pass` + `c-change-explained`，不绑绿色 `failing_test`。C3 保持红基线 `code.fix-failing-test`。夹具未动。
- Host `ensure_host_root_type` 把该类型注册进 planning world（种子库没有此类型；`install_root` 未注册会 `ContractError`）。0 条种子方法 → `goals_needing_method` 直接资格合成，不走绿 `failing_test` 的 NEEDS_EVIDENCE 饱和。
- **N9**：`budget_conserved` 只看 `remaining+reserved+settled==pool`；`usage_fully_known=(unknown_usage_calls==0)`。两者进 result.json、mechanism-receipt、pair.py（守恒才 FAIL；用量全知是 NOTE）。
- RUNBOOK §8.6；runner JOURNAL §27。

## 自检

`selftest_runner.py A–G` 全 OK；`selftest.py` SELFTEST OK（21 次评分）；`freeze-candidate.py` 重生成候选；`--verify --strict-commit` MATCH at `d3607501e105`。

## 未做

未跑真实模型局、未改 SDK、未覆盖 FREEZE.json、未 git commit、未动 `runs*/`。
