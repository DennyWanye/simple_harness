# 零散语料 run-02 复核记录（C05 六例 + C11 三例，Memory 0.6.31）

更新：2026-09-08 夜。性质：Opus 子代理逐条语义审查 + 主代理复核裁定，**不是人工标注**。
原始证据（gitignored）：`.local-test-evidence/2026-09-08/corpus-rest/run-02/`；
复核材料 `run-02.review-material.md`、子代理报告 `run-02.review-report.md`、裁定 `run-02.review-verdicts.json`。

## 一、范围与组合

- 9 例：C05-02/03/05/06/15/19、C11-12/16/19。跑道适配见 `RUNWAY-REST.md` §二。
- Host = main `f7b14325`（Memory SDK 0.6.31）；provider = **primary `gpt-5.6-luna`**。
- `batch-summary.jsonl` 中 C05-03/19、C11-12/16 的 `rc=75` 记录是 `run_resource_bounded` 锁冲突产生的空 stub，非失败；以同 case 的后续记录为准。

## 二、逐例裁定

| 例 | 裁定 | followup | 工具序列 | 依据 |
|---|---|---|---|---|
| C05-02 | **PASS** | f1 SATISFIED（`task_candidates_visible` visible_count=1） | `task_scope_search` → f1 `task_scope_search` + `resume_existing` | 首轮只列 A 候选与已记录的下一步「校对访谈名」，明确未恢复；f1 恢复真实 A 后如实说明缺文件路径并请用户补材料 |
| C05-03 | **PASS** | f1 SATISFIED（visible_count=2） | `task_scope_search` → f1 `resume_existing` | 首轮列「秋季小展（曾用名 展览准备）/冬季展览」两候选并反问确认；f1 恢复 A 并报当前状态「核标签」，声明部分原始记录不可用——**别名以 goal 承载的适配起到了 gold 要求的旧名可检索作用** |
| C05-05 | **FAIL** | f1 **UNMET**，无候选预览事件 | `context_route memory_standalone` | 首轮未调 `task_scope_search`，改走 `memory_standalone`（`memory_types=[episode,semantic,prospective]`、`include_short_horizon=true`），召回 `fragments=[]` 后直接宣告「没有检索到读书节的历史记录」；gold 要求的「列不同年份候选 + 按年份恢复 B」均未发生 |
| C05-06 | **FAIL** | f1 **UNMET**，无候选预览事件 | 零工具调用 | 首轮 `route_audit=[]`、`route_effects=[]`、provider `tool_calls=[]`，直接反问用户提供「两件工作」，把已存在于 TaskScope 的两个工作范围当成用户尚未给出的信息 |
| C05-15 | **FAIL** | f1 **UNMET**，无候选预览事件 | `procedure_discover` | 误把 TaskScope 恢复当成 Procedure 发现，`procedure_discover{query:'相册 校对'}` 得 `candidates=[]` 后即宣告未找到；全程未调 `task_scope_search`，标题「相册校对」与公开日期「2026年8月」从未进入模型视野 |
| C05-19 | **NOT_SCORED** | f1 NOT_REACHED | 零 | 首轮 provider 调用被中转以 `HTTP 502` 拒绝，Run 直接 fail；transcript 只有 user 一条、无任何 assistant 回合，`followup_events=[]`、`trace=null` |
| C11-12 | **PASS** | — | 零 | 「合计：25元」（5+20），未出现免邮/包邮/优惠；模型可见载荷（system 4623 字符 + user 24 字符）不含任何过期值 |
| C11-16 | **PASS** | — | 零 | 「今晚把书放回书架」逐字复述，无门票/领取；`prospective_registration.registered=[]`、`ticks=0`（过期提醒确实未注册） |
| C11-19 | **PASS** | — | 零 | 「3 × 12 + 4 = 40元」，未套用旧费率/旧数量上限/旧运费优惠三条 marker |

合计：**9 执行 / 5 PASS / 3 FAIL / 1 NOT_SCORED**；隐私违规 **0**；多提类型 **1 例**（C05-05，提 episode/prospective/semantic 三类而 required 为空）。

## 三、失败归类

| 例 | 主类 | 根因 |
|---|---|---|
| C05-05 | **模型行为** | 用 `memory_standalone` 代替 `task_scope_search` 找任务范围——用错工具面，非跑道缺陷。跑道侧两 scope 已 CONFIRMED，候选可见性契约按约执行 |
| C05-06 | **模型行为** | 零检索直接反问；与 run-01 的同例同因（`RUNWAY-REST.md` §三记为 `FOLLOWUP_UNMET`），本轮复现 |
| C05-15 | **模型行为** | 把任务恢复误判为流程发现，走 `procedure_discover`；`RUNWAY-REST.md` 记录的 `unproven_note_hidden` 跑道缺口本轮**未被触及**（模型根本没走到披露那一步），因此不构成跑道归因 |
| C05-19 | **跑道/环境** | 中转上游 502，与 C10-15 同类基础设施失败 |

跑道缺陷 0（除 502 环境项）、gold 缺陷 0、**Host 缺陷 0**（本批未发现新 Host 缺陷）。

## 四、主代理裁定

1. **FOLLOWUP_UNMET 的判定口径统一为 FAIL/模型行为，不再记 NOT_SCORED。**
   理由：跑道按契约执行（`task_phase_status=CONFIRMED`、`after_event` 语义正确、`record_unmet_and_stop_no_rescue` 按约停止），gold 要求的行为**可观测地没有发生**，这是被测对象的失败而非「无法评分」。
   **连带修订**：run-01e 的 C05-10、C05-20（模型未等确认即 resume）原记 NOT_SCORED，按同一口径改记 **FAIL/模型行为**；C05-12（SETUP_BLOCKED，需第二认证主体）与 C05-19（502）仍为 NOT_SCORED。
   口径变更对 C05 的影响：旧口径 9 PASS / 0 FAIL / 7 NOT_SCORED → **新口径 9 PASS / 5 FAIL / 2 NOT_SCORED**（分母 16 不变）。该修订已同步到 `CORPUS-CUMULATIVE-2026-09-08.md`。
2. C05 三例 FAIL 呈同一形态：**模型在需要 `task_scope_search` 的轮次选了别的工具面**（memory_standalone / procedure_discover / 什么都不调）。这与 C06 修好前的形态同源（模型不知道该转哪个发现面），建议与 C06 的 `procedure_hint` 一并处理——为 TaskScope 恢复类轮次补一条等价的 `task_scope_hint`。记为 followup，不在本轮修。
3. C11 三例全 PASS，且都是零查询下的正确行为：过期项确实未进入模型可见载荷（C11-16 的 `registered=[]`/`ticks=0` 是结构证据，不靠语义判断）。C11 全类 20/20 通过。
4. C05-05 是本批唯一多提类型例；`required_types=[]` 而模型提了三类，属选型习惯，与召回无关。
