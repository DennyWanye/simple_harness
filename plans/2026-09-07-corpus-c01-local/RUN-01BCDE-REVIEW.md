# 语料 run-01b/c/d/e 复核记录（剩余 112 例支持集，Memory 0.6.23→0.6.25）

更新：2026-09-08 凌晨。性质：Opus 子代理逐条语义审查（`.local-test-evidence/2026-09-07/corpus-batch/run-01bcd.review-*`、`run-01e.review-*`）+ 主代理复核；**不是人工标注**。

## 结果

| 批次 | 例数 | PASS | FAIL | NOT_SCORED | 说明 |
|---|---|---|---|---|---|
| run-01b/c/d（C02 ×19 + C03-01） | 20 | 19 | 1 | 0 | 唯一 FAIL C02-04：模型未检索直接作答；无旧值泄漏、无隐私违规 |
| run-01e（C03/C04/C05/C06/C08/C11） | 92 | 42 | 27 | 23 | C03 17/17、C11 16/16、C08 7/7（已评分）通过；C04 1/15、C06 1/14 |

NOT_SCORED 23：中转 502/503/超时 5、跑道审批 `no_exact_tool_decision` 5（其中多为超时后触发）、F07 history 不可核验 9（8 例是 C08 标量 setup `host_history_primary_unverifiable`，1 例 C04-16 `context_page_in` 失败）、跑道 setup 2、followup 未满足 1（C05-10 模型未等确认即 resume）、模型循环 900s 1（C06-18）。

## 主代理复核裁定

1. **C04 的 14 个 FAIL 与 C06 的 13 个 FAIL 不是产品缺陷**（独立分析 `DECISION-PROSPECTIVE-PROCEDURE-RECALL.md`）：C04 是跑道在评分前关闭分析 lane 连带关掉 prospective 注册消费者，种子提醒从未 accepted（C04-12 通过只因其 prepare 有 S5c 特判）；C06 是 gold 用 `required_types=procedure` 度量了设计上注定为 0 的"按类型召回未绑定指纹的 procedure"，acceptance 只要求适用性召回 + `procedure_discover` 发现。两者按备忘修跑道与 oracle（进行中），修后重跑（run-01f，50 例）。
2. 90/90 `NO_ACTIVE_GENERATION` 同属跑道关 lane 的后果，非召回缺陷。
3. C08 标量 8 例 setup 失败已由跑道修复（`open_primary` 顺序）解决，随 run-01f 重跑。
4. 产品/模型侧真实问题：F07（`context_page_in` 失败致 Run 不可核验，模型把 recall-item id 当页引用）；空召回后重复重提同一路由（C04 平均 3.2 次，F03 范畴）；C04-09/11 未召回时编造"建议提醒"（模型行为，需提示约束）。
5. 隐私违规 0（本组 privacy_allowed 全 true）；多提类型率 15.7%（run-01e）/ 80%（C02 组，多为 episode），仍超 15% 门槛。

## 240 例累计（截至 run-01e，未含 run-01f）

- 已真实执行：C01 20、C02 19、C03 18、C04 20、C05 9、C06 20、C07 20、C08 16、C09 20、C11 17（约 179 例）；C10/C12/C09-13/C08-20/C05-12 等 60 例仍无跑道适配。
- 已评分通过：C01 20/20（run-02 后）、C02 19/20、C03 17/17、C05 4/4、C07 20/20、C08 11/11、C09 15/19、C11 16/16；C04、C06 待 run-01f。
