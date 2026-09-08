# HM-TO-A6 尝试 4 结果（2026-09-08 16:52–17:26，Host 8e9b7c8d + Memory 0.6.28，DeepSeek，窗口 32000）

证据：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/native-a6-run4/primary-ui-7f_uv2a1`（单进程跑满 24 轮，未重启）；核对脚本输出 `a6-verify.json`。判定：**PASS 6 / FAIL 7 / INCONCLUSIVE 5**（人工核正后见下）。

## 与尝试 3 相比的进步（修复在真实环境生效）

- 第 5 轮后不再停摆；历史读取器无错；分析通道无解析失败；T10 记决定成功、T17 的 18 KB 目标逐字入档（canonical 第 3 版，7165 字）——事件 C、G、分析解析、可见性、历史读取器均验证通过。
- T20 明确纠正 → 新版本（Python 3.13，rev 2 + `amends` 关系）；T21 含糊矛盾 → 争议组 1 个（事件 H 生效）。
- T19 跨 15 轮召回正确；T16/T24 纯 UI 读图 provider 调用增量均为 0（脚本把 T24 的手动重发算进窗口，人工核为 PASS）。
- 同 Run 有界分页生效（`context_page_in` 4 次，页引用出现），Host 估算 token 峰值 18838 在预算 26752 内。

## 仍未通过的项与根因

| 项 | 判定 | 根因 / 处理 |
|---|---|---|
| A6-3 有界 | FAIL | provider 端 input_tokens 峰值 31154 > 预算，而 Host 估算 18838 在预算内：Host 分词估算比 DeepSeek 分词器低约 1.65×（事件 N，估算器校准） |
| A6-2 分页 | INCONCLUSIVE | T6/T8 读文件仍因空参数循环失败（事件 K：Host 把历史工具调用参数回放为 `{}`，修复中） |
| A6-5 视图超限 | INCONCLUSIVE | 目标已入档但 README/STATUS 视图未重生成——视图按读取时物化，T17 后无人读取视图；计划需在 T18 加一次视图读取（UI「任务」页或对话读 README） |
| A6-6 relation | FAIL | 关系提取把"前面说的 Python 环境"存成字面值，无 applies_to（事件 L，修复中）；出现的 amends/contests 属纠正/争议关系 |
| A6-7 supersede | FAIL（待核） | 新版本已生成，但脚本判"旧 revision 仍 active"；需核对 S3 契约中旧版本 lifecycle 的口径（脚本假设 vs SDK 语义） |
| A6-8 / NC-4 争议 | FAIL | 争议组已建立，但 T22 回答未要求确认、直接引用值：召回结果向模型披露争议态与"需确认"指引不足（事件 O） |
| A6-9/10 遗忘 | INCONCLUSIVE | 记忆列表页"认知记忆条目无效"（事件 M，修复中），遗忘按钮不可达 |
| T15/T22 重发 | Run FAILED | `recall_context_use_authority_stale`：前一轮分析恰在召回结算与使用之间落库；SDK 0.6.29（使用侧来源逐项复核通过即签发）进行中 |

## 结论

第 4 次已把"Host 结构性缺陷"层清完（停摆、解析、长消息、关闭规则、路由授权、Run 内有界），剩余为四类：模型上下文回放缺陷（K）、分析质量（L/O）、UI 列表校验（M）、估算器校准（N）与 SDK 使用侧争用（0.6.29）。这些合入后做第 5 次；届时按计划口径应能闭合 A6-1/2/4/5/6/7/11/12 与全部负控，A6-8/9/10 取决于 O/M。
