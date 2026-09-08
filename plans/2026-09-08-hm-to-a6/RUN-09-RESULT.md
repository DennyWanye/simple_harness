# HM-TO-A6 第 9 次结果（2026-09-09 06:20，Host dbf967fc，Memory 0.6.34，deepseek-v4-flash，窗口 32000）

24 轮全部走完（T16/T23/T24 面板操作，T24 手动重发）；`a6_verify.py`（已让 A6-2 同时认 `primary-effect-page:v1:` 效果页引用）判定 **PASS 9 / FAIL 6 / INCONCLUSIVE 3**（`RUN-09-a6-verify.json`；证据 `.local-test-evidence/2026-09-09/native-a6-run9/primary-ui-8whts2lo`）。

| 项 | 第 8 次 | 第 9 次 | 归因 / 下一步 |
|---|---|---|---|
| A6-1 | PASS | INCONCLUSIVE | T17 超时（事件 U 循环）；修复后可判 |
| A6-2 | FAIL | FAIL | 分页引用 64 个、翻页 22 次（`read_file` 强制生效），但 ANCHOR 行未在回复中出现——T11/T13 因预算超限失败（F-E3） |
| A6-3 | FAIL | FAIL | 预算超限 4 次（T6/T8/T13/T21，均 `full_trim=True`、open_group 18–19 K）→ F-E3 分页摘要固定成本；另 T17 出现一次 provider 计 **76 708** input token 的请求未被预算拦下（事件 W，待诊断） |
| A6-4 | PASS | PASS | — |
| A6-5 | INCONCLUSIVE | INCONCLUSIVE | 事件 U（refs_outside_scope 循环） |
| A6-6 | INCONCLUSIVE | FAIL | 关系行 2 条均为 evolution（amends/contests），无 `applies_to`；事件 T（v8 策略无流程候选时不提关系） |
| A6-7 | INCONCLUSIVE | **PASS** | 更正落成 revision 2 + amends 边（召回超时 0 次，0.6.33 生效） |
| A6-8 | INCONCLUSIVE | FAIL | 争议组已建立（1）、revision contested（1），但 T22 的 `context_route` 结果无 `conflict_notice`、回复未要求确认（事件 V，待诊断：争议在 T21 落库后 T22 召回为何未带通知） |
| A6-9 | PASS | PASS | 遗忘前 9 节点 1 边（contests），遗忘后 7 节点 0 边，与 DB 复算一致 |
| A6-10 | INCONCLUSIVE | **PASS** | 关系行 append-only，遗忘后 2 节点离图，关表重开逐字相同 |
| A6-11 | PASS | PASS | 134 次请求 0 命中 |
| A6-12 | PASS | INCONCLUSIVE | 134/134 重放全等；两个 Run 的调用数与回执数差 1（`ef57d663` 11≠10、`765be600` 5≠4）——事件 W 一并查 |
| NC-1 | PASS | FAIL | T3 改写走了 `context_tool` 路由（模型行为差异；第 8 次为 no_recall） |
| NC-2/3/5/6 | PASS | PASS | — |
| NC-4 | FAIL | FAIL | 同 A6-8 |

## 本次确认已修好的

- 召回超时：第 8 次 6 次 → **0 次**（Memory 0.6.33 `sqlite_tx.begin_transaction` 假设成立）。
- 事件 S：`analysis_relation_candidates_unavailable` 21 → **0**，改为具名 `relation_procedure_applicability_absent`。
- 事件 R、F-E2：无 scope-source 误判；控制类结果压桩生效（`control_results_stubbed` 5–6/Run）。
- A6-7、A6-10 首次 PASS。

## 仍开的主流程缺陷（均已派独立 Opus 子代理）

| 事件 | 内容 | 影响项 |
|---|---|---|
| F-E3 | 分页摘要固定 2 KB 成本，13–16 次 `tool_search` 即挤爆 32 K 窗口 | A6-2/A6-3 |
| 事件 T | v8 策略在无流程候选时不提关系 | A6-6 |
| 事件 U | `refs_outside_scope` 拒绝不披露范围内 evidence id | A6-5/A6-1 |
| 事件 V | T22 争议期召回无 `conflict_notice` | A6-8/NC-4 |
| 事件 W | 76 K token 请求绕过预算；调用/回执计数差 1 | A6-3/A6-12 |
| F-S1b | Procedure 端点在 `check_history_visibility` 恒 stale（SDK 0.6.36 + Host） | A6-6 Procedure 形态 |

## 合入进度（2026-09-09 07:05）

- 已合入 main：事件 U（`db587430`）、F-E3（`1314a6bd`，四个失败 Run 离线复算全部回到预算内，摘要 2.0 KB → 566 B）。
- 事件 T：实现完成，评审 3 条 MUST-FIX（验证器 A6-6 须要求 target 端点为本 plan 新建的流程节点；A6-6 验证器补测试；备忘录如实引用 HM-S12/HM-TO-A2 的「两个 canonical node」原文并记 F-T6）→ 收尾子代理处理中。
- 事件 V：评审 4 条 MUST-FIX（争议记忆槽位文本无中文且无向量世代，纯中文提问在 SDK 词法门不可达 → Host 只能保证「一定会问」，SDK 侧 F-V-2 升为阻塞项；同轮两次 `context_route` 的幂等键冲突；被拒路由丢弃探测结果；非记忆路由的通知无结果哈希）→ 收尾子代理处理中；F-V-2 已派 SDK 子代理（分支 `m0637`）。
- 事件 W、F-S1b（SDK 0.6.36）进行中。

## 事故：2026-09-09 07:30 整机内存耗尽

macOS 弹出「系统的应用程序内存不足」：Claude 桌面端 17.38 GB（6–7 个 Opus 子代理 + 监视器并行）、Verify 应用家族 17.38 GB（Manual 旅程跑到 T3、C04 语料批同时在跑）。Claude Code 进程退出，所有子代理/监视器/驱动丢失（worktree 未提交文件保留：rel-form 11、contest-notice 8、budget-bypass 9、f-s1b 7；SDK m0636 已提交 `dc70e2b`，m0637 未开始）。空闲时应用家族实测仅 ~940 MB，17 GB 是运行期增长，尚未定位。处置：并发子代理上限 2–3、子代理不再自派评审、原生旅程与语料批不同时跑、RSS 看门狗（>3 GB / 空闲 <15% / +1 GB 报警）、`caffeinate` 重启；Manual 旅程从 T1 重跑（run3）。

## 合入进度（2026-09-09 07:50）

- 已合入 main：事件 T（`7e2b4fd2`）、事件 V（`1e7043e9`）、F-S1b Host 侧（`8b764e29`）。SDK main：0.6.35（`b82187e`）、0.6.36（`b1f9492`，wheel SHA `0114f3d2…`，`pin-0636.sh` 已备）。
- 进行中：事件 W 收尾（预算旁路 3 条 MUST-FIX）、SDK 0.6.37（F-V-2 争议组词法准入基础）。
- 第 10 次前置：W 合入 → 钉 0.6.36/0.6.37 → 重建 bundle。按新方法，先做逐轮离线重放/短旅程验证，再整跑一次。
