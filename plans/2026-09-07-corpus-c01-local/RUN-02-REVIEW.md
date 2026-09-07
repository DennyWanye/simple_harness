# 语料 run-02 复核记录（Memory 0.6.23 向量通道 + 任务搜索中文修复后重跑）

更新：2026-09-07 晚。性质：子代理逐条语义审查（`.local-test-evidence/2026-09-07/corpus-batch/run-02.review-report.md` / `run-02.review-verdicts.json`）+ 主代理复核；**不是人工标注**。原始证据在 gitignored `run-02/`、`run-02b/`。

## 范围与版本

- 11 例：run-01 的 5 个 C01 FAIL（06/07/12/17/19）+ 回归控制 C01-10 + 审批拦截 C01-08 + C05-04/07/09 + 环境失败 C07-03。
- 组合：Host main（含 0.6.23 适配、任务搜索二字组合 + 单字兜底）、installed H0.7.10 / M0.6.23 / S0.3.13，gpt-5.6-luna 主通道，无回退。C01-12（run-02 中转 502）与 C05-04（run-02 跑在单字兜底修复前）以 run-02b 重跑为准。

## 结果

| 指标 | run-01（同组） | run-02 |
|---|---|---|
| 判定 | 2 PASS / 5 FAIL / 4 NOT_SCORED | **11 PASS / 0 FAIL / 0 NOT_SCORED** |
| C01 required-type 召回率 | 2/7 | **7/7** |
| C01 context_route 调用次数 | C01-06 11 次、C01-17 6 次 | 全部 1 次 |
| `cognitive_vector_unavailable` | 6 例出现 | 0 例；`cognitive_vector_generation.activated=true` |
| 隐私违规 | 0 | 0 |
| 多提类型（C01） | 52.6%（run-01 全组） | 5/7 = 71%（episode ×4、prospective ×2，未污染回答） |

## 主代理复核裁定

1. C01-12（run-02b）维持 PASS：回答绑定本人 09:00–17:00，同事时段是用户问句自带且被明确排除，不算混入 B。
2. C05-04（run-02b）维持 PASS：候选→确认→resume_existing 只读、无文件 effect；"已恢复"是措辞问题，记入提示优化。
3. `NO_ACTIVE_GENERATION` 是短时域 lane 的退化码（单轮新会话无短期世代），与认知向量通道无关；复核材料抽取应分列（待办）。
4. 多提类型率仍高于 15% 门槛：与召回无关的模型选型习惯，待与提示词一起处理（不阻塞本轮）。

## 结论

- 向量通道使 C01 同义查询类失败全部转绿，且消除了空召回循环（F03 场景在本组自然消失）。
- HM-AC-8 三阈值在本组：召回 100% ✅、隐私 100% ✅、多提类型 71% ❌。完整 240 例判定仍待 run-01b（剩余 112 例支持集）与 59 例适配。
- 0.6.23 在原生 r8 暴露 relation head 的向量世代缺陷（见 `../2026-09-07-native-main-journey/NATIVE-R8-RELATION-GRAPH.md`），语料跑道未触发（无 relation 记忆），0.6.24 修复中。
