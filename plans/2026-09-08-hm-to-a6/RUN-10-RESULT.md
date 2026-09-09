# HM-TO-A6 第 10 次整跑结果（2026-09-09 11:20，Host 43a8f835 源码 + bundle 60ab03a1，Memory 0.6.37，flash 关闭 thinking，窗口 32000）

24 轮走完（T16/T23/T24 面板 + T24 手动重发）；`a6_verify.py`：**PASS 14 / FAIL 2 / INCONCLUSIVE 2 / BLOCKED 0**（事件 AB 复算后；原判 13/3/2）（`RUN-10-a6-verify.json`；证据 `.local-test-evidence/2026-09-09/native-a6-run10/primary-ui-j5yjctfj`）。历次：第 5 次 13/1/1/3 → 第 8 次 10/3/0/5 → 第 9 次 9/6/0/3 → **第 10 次 13/3/0/2**。

| 项 | 第 9 次 | 第 10 次 | 归因 / 下一步 |
|---|---|---|---|
| A6-1 | INCONCLUSIVE | **PASS** | 22 Run 全终态、receipt ordinal 连续 |
| A6-2 | FAIL | FAIL | 分页引用 35、翻页成功 10；ANCHOR 行未在回复出现——T6 失败（事件 Z） |
| A6-3 | FAIL | FAIL | 组装超限 **1 次**（T6，第 9 次 4 次）；峰值 input 19849 < 26752；线上门拦下 T17（W-b） |
| A6-4 | PASS | **PASS**（事件 AB 修验证器后复算） | 2 处「消失的组」是 supersede/争议撤销披露，非裁剪；A6-4 改为只管裁剪 |
| A6-5 | INCONCLUSIVE | INCONCLUSIVE | T17（W-b）/T18（事件 AA）失败，无视图修订 |
| A6-6 | FAIL | **PASS** | applies_to 知识边 + 本 plan 新建 procedure 节点；T16 UI 显示 7 记忆 1 关系 |
| A6-7 | PASS | PASS | — |
| A6-8 | FAIL | INCONCLUSIVE | T21 本次未产生争议组（短旅程 ④ 曾产生并 PASS）；模型/分析变异，需再跑复核 |
| A6-9 | PASS | PASS | 遗忘 procedure 端点后 8→7 节点，与复算一致 |
| A6-10 | PASS | PASS | 关系行 append-only，遗忘后离图，关表重开逐字相同 |
| A6-11 / A6-12 | PASS / INCONCLUSIVE | PASS / **PASS** | 89/89 重放全等，双射成立（事件 W） |
| NC-1 | FAIL | **PASS** | T3 no_recall（本次未含 F-NC1 的 PERSONA 改动，属模型变异；改动已合入） |
| NC-2/3/5/6 | PASS | PASS | — |
| NC-4 | FAIL | **PASS** | T22 要求确认 |

## 本次失败的 Run 与归因

| 轮 | 原因 | 事件 |
|---|---|---|
| T6 | `tool_activate read_file` 被拒 `workspace_unscoped`（文案自相矛盾）→ 16 次 tool_search → 组装超限 | Z（修中） |
| T17 | 关闭 thinking 时携带量回落 `output_tokens` 重复计数，线上门超 105 token 拦下 | W-b（修中） |
| T18 | `recall_context_use_authority_stale` 整 Run 失败（Run 内权威 epoch 推进未重收集） | AA（修中） |

## 观察

- 关闭 thinking 后普通轮 8–28 s，全程约 55 min（第 9 次 75 min）。
- T20 更正后，applies_to 边因 source 端点 revision 前进而按「exact revision」口径隐藏（8 记忆 0 关系）：契约行为，但产品上值得记为 followup F-A6-EDGE（是否应把知识边迁移到新 revision 或标记待复核）。
- 后端 RSS 逐轮增长仍在（事件 X 剖析中）。
