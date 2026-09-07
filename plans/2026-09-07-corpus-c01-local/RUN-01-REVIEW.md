# 语料批次 run-01 复核记录（子代理审查 + 主代理复核）

更新：2026-09-07。审查性质：子代理逐条语义审查（`.local-test-evidence/2026-09-07/corpus-batch/run-01.review-report.md` / `run-01.review-verdicts.json`），主代理复核并裁定边界；**不是人工标注**。原始证据在 gitignored `.local-test-evidence/2026-09-07/corpus-batch/run-01/`。

## 范围与版本

- 复核材料由 `scripts/corpus_review_extract.py` 抽取，覆盖 run-01 前 44 例（C01 ×20、C07 ×20、C05 ×4）；批次仍在后台续跑（C05/C08/C09…），后续用例另出记录。
- 版本：C01 全部与 C07-01..03 在 installed M0.6.20 上运行，其余在 M0.6.22（见 run-01/PROVENANCE-NOTE.txt）。主用 gpt-5.6-luna，本 44 例无 DeepSeek 回退、无 502/429。

## 统计（可评分 39 例）

| 类别 | 例数 | PASS | FAIL | NOT_SCORED |
|---|---|---|---|---|
| C01 精确召回 | 20 | 14 | 5 | 1（C01-08 审批拦截） |
| C05 任务恢复 | 4 | 1 | 0 | 3（C05-04/09 followup 未满足；C05-07 历史依赖校验拒绝） |
| C07 零召回 | 20 | 19 | 0 | 1（C07-03 SDK 版本不匹配） |

- required-type 召回率：C01 14/19 = 73.7%（门槛 ≥90%，**未达标**）；C07 零召回正确率 19/19。
- 隐私违规：0（门槛 100%，达标；但本 44 例全为 privacy_allowed=true，不能外推到 C10/C12 隐私类）。
- 多提类型：C01 10/19 = 52.6%（全为多提 episode），全体 10/39 = 25.6%（门槛 ≤15%，**未达标**）。

## 主代理复核裁定

1. C01-07 维持 FAIL：只召回 B，A（禁用收尾语）未召回，回答碰巧合规；gold 明确"不能以输出冒充召回"。
2. C01-14 维持 PASS：服务器 UTC 项被一并召回但未污染回答；计入多提。
3. C01-08 维持 NOT_SCORED：审批白名单 `not_whitelisted_or_already_attempted` 属评分跑道限制，触发原因是模型第三次路由；不计产品缺陷。
4. C05-04/C05-09：task_scope_search 对已种子化任务返回 0 候选，模型未编造——行为正确，产品搜索缺陷；C05-09 在复核材料中字段全 None，是 `corpus_review_extract.py` 未覆盖 FOLLOWUP_UNMET 路径（待修）。
5. C05-07 `PrimaryHistoryDisclosureRejected`：需单独复现（active 任务 C + 旧任务 A 接线）。
6. C07-03 环境失败：批次中途 0.6.20→0.6.22 切换导致；重跑即可。

## 根因聚合（按用户"先跑完再批量修"）

| 根因 | 影响 | 处置 |
|---|---|---|
| SDK 长期认知记忆无向量通道：`sqlite_v5.py` 对请求 vector 的 typed 计划硬编码 `cognitive_vector_unavailable`，召回退化为词面（0.6.20 已修 CJK 二字组合，但同义词仍不命中） | C01 全部 5 个 FAIL | 独立子代理裁决方案（DECISION-2026-09-07-cognitive-vector-lane.md，Memory SDK plans 目录），主代理复核后实施 |
| 空召回后同质路由循环（C01-06 11 次/270s，C01-17 6 次/440s） | 耗时与费用 | 用户已定为 F03 followup，本轮不处理 |
| task_scope_search 词面匹配过窄（"排版"子串都不命中"家谱排版"） | C05-04/09 | 批次结束后修 |
| 多提 episode 过多 | 多提率 52.6% | 与召回修复一起看提示词/路由类型选择 |

## 结论

C01 子集在当前 H0.7.10/M0.6.20-22 组合上未通过 HM-AC-8 门槛；C07 子集通过。整批 181 例支持集结果待批次结束后汇总（run-02 重跑环境失败与修复后受影响条目）。
