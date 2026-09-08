# 240 条语料累计结果（截至 2026-09-08 08:00，Memory 0.6.26 / Host main dae4670b）

性质：各批次由子代理逐条语义审查 + 主代理复核，**不是人工标注**。同一用例以最后一次可评分批次为准（run-01 → run-02/02b → run-01b/c/d → run-01e → run-01f/g）。原始证据在 gitignored `.local-test-evidence/2026-09-07/corpus-batch/`。

## 总表

| 类别 | 已执行 | PASS | FAIL | NOT_SCORED | 说明 |
|---|---|---|---|---|---|
| C01 精确召回 | 20 | 20 | 0 | 0 | 0.6.23 向量通道后全绿 |
| C02 偏好复用 | 19 | 18 | 1 | 0 | C02-04 模型未检索直接作答；C02-19 不在语料清单 |
| C03 冲突/多来源 | 19 | 18 | 0 | 1 | C03-03 环境重跑未纳入本轮 |
| C04 时间/提醒 | 20 | 16 | 4 | 0 | 0.6.26 后 prospective 召回 20/20；4 例 FAIL 为触发时刻换算错（payload 只有 epoch） |
| C05 任务恢复 | 9 | 6 | 0 | 3 | C05-10/20 模型未等确认即 resume；C05-12 需第二主体（跑道不支持） |
| C06 额外来源/流程 | 19 | 4 | 15 | 0 | 新口径 `required_procedure_access`：模型在 context_route 只拿回 semantic 后不再调 `procedure_discover`（12 例），另 3 例召回漏检 |
| C07 零召回 | 20 | 20 | 0 | 0 | |
| C08 保留/标量 | 19 | 19 | 0 | 0 | 跑道 `open_primary` 修复后全绿 |
| C09 硬触发 | 19 | 15 | 4 | 0 | 模型行为（把改写当文件任务 / 把输入事实当记忆查） |
| C11 过期资格 | 17 | 17 | 0 | 0 | |
| **合计** | **181** | **153** | **24** | **4** | 未执行 59 例：C10、C12、C09-13、C08-20、C05-12 等无跑道适配 |

HM-AC-8 三阈值（在已评分 177 例上）：required-type 召回率 ≈ 96%（≥90% ✅）；隐私违规 0（100% ✅）；多提类型率各批 15–70%，整体高于 15% ❌（模型选类型习惯，非召回缺陷）。

## 剩余根因（按修复优先级）

1. **C06：context_route 请求 procedure 无候选也无提示** → 模型不转 `procedure_discover`。方案：context_route 回执在 requested 含 procedure 且无 procedure 候选时返回显式降级码/提示 `use procedure_discover`，PERSONA 同步（Host，小改）。
2. **C04：prospective fragment 缺本地时间渲染** → 模型换算日期出错 4 例。方案：Host `project_recall_fragments` 为 prospective 追加 `trigger_local`（场景时区 ISO + 星期），不改 SDK 公开 payload。
3. **C06-19/06/18 语义/程序召回漏检**（需最小复现分析）。
4. **F07**（page_in 失败致 Run 不可核验）修复中；**F03**（空召回循环）与「未召回时编造建议」属模型行为，待提示词约束。
5. 多提类型率：提示词层面强调只请求 required 类型（不阻塞召回）。

## 今晚各批次修掉的根因（已合入 main）

向量通道缺失（0.6.23）、relation head 世代崩溃（0.6.24）、Procedure 发现面（0.6.25）、prospective 文本渲染（0.6.26）、任务搜索中文匹配、跑道 prospective 注册 tick、C08 标量 setup、C06 gold 口径、授权时钟接缝、`after` 必填、参数拒绝致不可核验、F06 provider 超时停摆（Host retry-once）。

## 更新（2026-09-08 10:20，含 run-01i/j：P1 `procedure_hint`、P2 `trigger_local`、F07 修复后重跑）

| 类别 | 已执行 | PASS | FAIL | NOT_SCORED |
|---|---|---|---|---|
| C01 | 20 | 20 | 0 | 0 |
| C02 | 19 | 19 | 0 | 0 |
| C03 | 19 | 19 | 0 | 0 |
| C04 | 20 | 17 | 3 | 0 |
| C05 | 9 | 6 | 0 | 3 |
| C06 | 20 | 13 | 6 | 1 |
| C07 | 20 | 20 | 0 | 0 |
| C08 | 19 | 19 | 0 | 0 |
| C09 | 19 | 15 | 4 | 0 |
| C11 | 17 | 17 | 0 | 0 |
| **合计** | **182** | **165** | **13** | **4** |

- C06：`procedure_hint` 使 18/18 例改调 `procedure_discover`，通过 4/19 → 13/20；剩余 FAIL：discover 返回 0 候选（C06-05/06/09/19，词项覆盖待查）、C06-18 走 create_new 跳过召回、C06-08 中转 502。
- C04：`trigger_local` 未真正进入工具回执（只在系统提示里），C04-12/13/19 仍错/拒答时刻，修复中（fragment 顶层字段）。
- F07 修复后 C04-16/C06-13/C03-03 重跑全部通过。
- 新发现：`procedure_use` 连续 `tool_handler_failed`（C06-14）、`task_scope_update` 多次被拒，拖长耗时；修复中。
- HM-AC-8：召回率 ≈ 97% ✅、隐私 100% ✅、多提类型率仍 >15% ❌。
