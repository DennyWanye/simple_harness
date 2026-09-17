# Grok 验收 · runner 侧修正（FULL-TARGET-1.4 §21.5）

- 日期：2026-09-17
- 范围：**只改 runner 与题目的根目标声明**。未跑 Grok、未烧 token、未动 SDK、未动题目夹具。
- 输入：`impl/Grok验收-H臂故障诊断-2026-09-17.zh-CN.md`（D2a / S1 两条归属 runner）
- 证据包：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-16/htn-acceptance/`
- 详细过程：`runner/JOURNAL.zh-CN.md` §14–§20；重跑程序：`runner/RUNBOOK.zh-CN.md` §8

---

## 0. 一句话

D2a 的修法是「让每道 C 题像 L4 一样自己声明根目标」而不是「换一个目标类型」，
因为种子库里唯一前置一定成立的 `code.assess-regression` **不产出补丁**，用它会制造
一个比现状更坏的 `false_completion`；S1 的修法是「改名说实话 + 把编排的判定另存一份」
而不是「只导出 Acceptance 认可的产物」，因为实测 F 臂 legacy 的 `acceptances` /
`acceptance_outputs` **三张表全是 0 行**，那样改会让 F 臂 delivery 全空、配对失效。

---

## 1. 交付项与决定理由

| # | 交付 | 做法 | 理由（一句话） |
|---|---|---|---|
| 1 | **D2a** L3 根目标 | 新增 `code-tasks/C{1..4}/construction.json#root_goal`，`run_h_arm.root_goal_for` 对 L3/L4 统一逐字采用；删掉 `failing_test="tests"` 硬编码 | 夹具冻结不可改；`construction.json` 在 `repo/` 旁边，不进候选工作区，题目身份不变 |
| 2 | **D2c** runner 半边 | 两臂 `OrchestratorConfig` 都显式 `max_planning_attempts=3` | `_planning_rejected` 是 legacy 也走的同一条路，且该字段在配置摘要里是 `include`，只改一臂会破坏「两臂配置逐字节一致」 |
| 3 | **S1** 口径 | `_accepted_files` → `_model_changed_files`（改名 + 如实 docstring，行为不变）；新增 `result["accepted_outputs"]`；`pair.py` 增 `completed_rate` 硬性门槛与 §5b 四列并排表；机制回执加 `official_pass_basis` / `accepted_output_rows` | F 臂 legacy 的 Acceptance 表实测为空，改成「只导出已验收产物」会让 F 全线 delivery 为空 |
| 4 | **离线自检** | `selftest_runner.py` 新增 E 段 + `EXPECTED_RED` / `--strict` | 用 SDK 自己的函数断言 `appworld_execute` 暴露；D1 未修时预期红且署名，`--strict` 下判死 |
| 5 | **RUNBOOK** | 新增 §8「重冻结与全量重跑」（顺序 / token 看门狗 / restore / 阈值待确认）；文首加跳转提示；前置表加 P5a | SDK 修复提交之前 §1/§2 一局都不能开 |
| 6 | **作废收据** | `runs/` → `runs-a8e902a-invalid/`（**已执行**，未删任何文件），附 README | 诊断附录 A 逐条引用其中路径，是 D1–D5 的一手证据 |

**没有生成新的 `FREEZE.json`**（按要求等 SDK 提交）。因此现在
`freeze-candidate.py --verify` 打印 MISMATCH 是**正确状态**：C1..C4 的 `tree_sha256`
因为多了 `construction.json` 必然变化，重冻结顺序见 RUNBOOK §8.1。

---

## 2. D2a：四道 C 题声明了什么（现场实测，未调模型）

用 `run_h_arm.first_evidence_round` + 真实 `code` 观察器跑一遍证据轮的结果：

| 题 | `failing_test` | `repo-checked-out` | `test-is-failing` | `regression-commit-known` | 结论 |
|---|---|---|---|---|---|
| C1 | `tests/test_public_collector.py` | true | false | false | NEEDS_EVIDENCE → 依赖 SDK **D2b** |
| C2 | `tests/test_public_pipeline.py` | true | false | false | 依赖 **D2b** |
| C3 | `tests/test_public_window.py::test_window_sum_covers_the_whole_requested_range` | true | **true** | false | **`code.fix-by-patch` 立即适用，不依赖任何 SDK 修复** |
| C4 | `tests/test_public_pager.py` | true | false | false | 依赖 **D2b** |

- C3 是四题里唯一题面写明「现在其中一条是红的」的，所以能指到真失败的 nodeid
  （实测 `assert 5 == 9`，1 failed）。**重跑时 C3 若仍 `planning_failed`，说明问题不在 runner。**
- C1/C2/C4 的可见套件实测全绿（4 / 6 / 3 passed），这是用户审过的题目身份，不改。
  它们的失败路径交给 SDK 的 D2b「证据饱和后准入合成轮」，每个 `construction.json` 的
  `depends_on_sdk` 字段写着这条依赖。
- 备选方案 `code.assess-regression`（前置只要 `code.repo-checked-out`，一定成立）**被否**：
  它的 steps 只有 `facts → reproduce`、`finalizer_step = reproduce`、coverage 只有
  `c-failure-reproduced`，根本不产出补丁 → Mission 可以在没修任何东西的情况下 COMPLETED，
  隐藏评分器必然红 → `false_completion = 1`，正撞 §21.5 的硬门槛；同时 F 臂拿到的是题面，
  两臂在做不同的题。

---

## 3. `completed_rate` 门槛（**阈值待用户确认**）

> H 臂 `mission_status == COMPLETED` 的局数 ÷ H 臂总局数 ≥ **0.80**

与「L1 不退步」同级，任一不成立整份报告 FAIL。**0.80 是 runner 侧的提议，尚未经用户确认**：
不变量的名字里自带「阈值待用户确认」字样，`pair.py --completed-floor 0.9` 可覆盖，
确认后把 `pair.py` 顶部的 `COMPLETED_RATE_FLOOR` / `COMPLETED_RATE_FLOOR_STATUS` 一起改。
说明写在 `RUNBOOK.zh-CN.md` §8.4。

同时 `错误宣布完成 = 0` 这条在 `COMPLETED = 0` 的批次上会自动追加一句
「本批 COMPLETED = 0，这条门槛是平凡成立的，不构成正面证据」。

---

## 4. 自测结果（未调模型）

```
$PY selftest_runner.py A C E      → RUNNER SELFTEST OK（E 两条 EXPECTED RED：D1 未修）
$PY selftest_runner.py B          → RUNNER SELFTEST OK（C1 新根目标 + M3 走完真实 run_episode）
$PY selftest_runner.py E --strict → RUNNER SELFTEST FAILED: 2 problem(s)（预期）
```

E 段实测输出（SDK HEAD = e53395c（`main`，在 a8e902a 之上只有一个 docs 提交，`src/` 与 a8e902a 逐字节相同））：

```
worker template worker-appworld-v3 -> worker-hierarchical-v1
暴露工具 ['workspace_read_file', 'workspace_write_file', 'workspace_list']
EXPECTED RED (D1 未修复): appworld_execute is missing
EXPECTED RED (D1 未修复): 提示词也被换成了代码域的
```

`pair.py` 的五份合成报告（pass / blocked / stray / fail / never-completed）判定全部符合预期，
其中 never-completed 一份专门验证：official 有、COMPLETED 全 0 的批次必须被
`completed_rate` 判死，**而 `错误宣布完成` 那条仍然是绿的**。

---

## 5. 改动文件清单与 SHA-256

根目录：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-16/htn-acceptance/`

| 文件 | 字节 | SHA-256 |
|---|---|---|
| `runner/run_h_arm.py` | 53182 | `ae98e3a6ae83e20fa00dc66f13cde49b0afedf3ec65a57482a29b5565679db38` |
| `runner/run_f_arm.py` | 26912 | `d988da7e3ed6a913f308e3c292bc10ee53a00f773f1459c5fde6e5e00ae9fa2b` |
| `runner/common.py` | 21716 | `5489523822463c700efc41ba44838e75b2c95515fb132957fc41c4178c8dffb9` |
| `runner/pair.py` | 37067 | `c90d46fc76881a2903be198f93f749f44f6fa88ae6715680ee9294b5438e550f` |
| `runner/mechanism.py` | 38019 | `ac21e27828fb35a6fb7c0c6ff5d15d7b539dd8d494dd4dd02c5fb2c260c86f10` |
| `runner/selftest_runner.py` | 52219 | `edd78db6363ebbc7c8bad5c667e32af814b43c8df6f703fc2da9ce26ef4fe54b` |
| `runner/RUNBOOK.zh-CN.md` | 26742 | `74ff7a837f762b31fe3c6d0659063ae5bb1a232efcb3313d032611b363afd1f5` |
| `runner/JOURNAL.zh-CN.md` | 51121 | `06d4bba076625489e5eb7ad0ac08afe831f67ee71e3d81c88ee6ed5e81981d06` |
| `code-tasks/C1/construction.json`（新增） | 2869 | `bb3514d8a07b9ea19364b3cd5eb40464a6aca0008390823239249f9cc5fcbb46` |
| `code-tasks/C2/construction.json`（新增） | 2051 | `ba7a12d55896448f9be87d39e2f20dafbe90f34c39b28c6c73d6be176e8d435e` |
| `code-tasks/C3/construction.json`（新增） | 2553 | `c18a98f4ed3280a695be00b32df2cbe33a3c3c8fa917c94738635de4fe2971e3` |
| `code-tasks/C4/construction.json`（新增） | 1919 | `2d73448a585b08e1adec198496c82ea567a42784b5d1854da4a7f4d6e94fbb66` |
| `runs-a8e902a-invalid/INVALID-README.zh-CN.txt`（新增） | 1825 | `75f910502bb5b0e48680d955e98beb96893c0c4000ff925f7cda0f7f011e8769` |

目录移动（无内容改动）：`runs/` → `runs-a8e902a-invalid/`（含 `h-arm/`、`f-arm/`、
`f-arm-invalid-401/`、`report-batch-1a/`；一个文件都没删）。

**未改动**：`code-tasks/C*/repo|hidden|reference|grade.py`、`code-tasks/gradelib.py`、
`mechanism-tasks/**`、`taskset-L1.json`、`taskset-L2.json`、`budget-L2.json`、
`freeze-candidate.py`、`FREEZE.json`、`FREEZE-candidate.json`、`selftest.py`、
以及 `simple-harness-sdk/` 全树。

---

## 6. 纪律说明

- 全程未调用任何模型，未跑任何 Mission（自测 B/E 段用的是脚本化 provider 与纯离线函数调用）。
- 未打印、未读取任何 `llm_runtime*.json` / `~/.grok` / `*.env`；本文件不含任何凭据。
- SDK 工作树未改动（核对时 `git status --porcelain` 为空，`HEAD = e53395c`），
  D1 / D2b / D2c / D3 / D4 / D5-A / D5-B 由 `p2.3d-fix` 分支的另一位代理负责。
  本轮的一切实测都是在这个提交上做的：D1 仍然开着（E 段实证），所以 §2 表里 C1/C2/C4
  的 NEEDS_EVIDENCE 与 C3 的 `code.fix-by-patch` 适用，都是修复前的真实读数。
- 临时脚本只存在于 scratchpad，未进入项目目录。
