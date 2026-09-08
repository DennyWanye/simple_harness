# 事故 H 裁决与修复：显式纠正必须产生新 revision；含糊矛盾必须进入 contested

2026-09-08。范围：HM-TO-A6 第 20/21 轮（A6-7 / A6-8）。
证据前缀 `.local-test-evidence/2026-09-08/native-a6-b3682fe1/`。
本备忘录只记录裁决与实现，**不修改** `00-PLAN.md`。

---

## 1. 复原：第 20 轮到底发生了什么

用户第 20 轮原话（`state.db` → `human_memory_evidence`，`evidence_id`
`d0ea1731-0e1a-584a-8300-e34629150d95`，`delivery_key`
`16dbecba-45ea-492f-b764-68fe94b6df4e`）：

> 更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12。

对应记忆（`human_memory_v7.db` → `cognitive_memory_revisions`）：
`cognitive-memory-04bd08c1…`，revision 1，`subject_entity=user:self`、
`predicate=material_proofreading_python_version`、`object_value=Python 3.12`、
`qualifiers=[]`、`epistemic_status=explicit_user`、`conflict_status=uncontested`。

**模型的提案是完好的**。从
`state.db` → `post_turn_invocation_attempts.result_envelope_json`
（attempt `ecf03baf-1d0f-565e-a873-0fb1a0abb480`，provider 响应
`b24db323-0451-4a9a-b8e0-db9e616d7c8a`，deepseek-v4-pro）复原出的唯一 tool call：

```json
{"outcome":"mutate","operations":[{
  "operation_id":"op-1","action":"revise_semantic",
  "candidate_key":"semantic-candidate-0da7c3cba004aad92b573bea6291c9d123552692b5ddca6c717dde7659f6b712",
  "evidence_item_id":"16dbecba-45ea-492f-b764-68fe94b6df4e",
  "exact_quote":"Python 3.13","memory_type":"semantic",
  "semantic":{"subject_entity":"user:self",
              "predicate":"material_proofreading_python_version",
              "object_value":"Python 3.13","qualifiers":[]}}]}
```

即：选中了 Host 实际签发的候选键、保留了原 subject/predicate/qualifiers、
`exact_quote` 是该用户消息的逐字子串、新值就在引文里。Host 能机械校验的每一项都通过。

**唯一的拒绝原因**（`state.db` → `host_pre_admission_audit` 第 8 行，13:54:22，
`sdk_run_id=product-sdk-af844673…`）：

```
analysis_explicit_correction_intent_missing
```

逐条对应关系：`analysis_batches.result_json.structured_result` =
`{"outcome":"no_mutation","operations":[],"closure_reason":"analysis_all_operations_rejected"}`；
`llm_invocations.output_reason_code = analysis_validator_accepted`（验证器接受了响应，
是 Host 编译阶段丢弃了这条 operation）；`cognitive_memory_revisions` 仍只有 revision 1。

### 为什么是这个原因

`backend/deskpet/memory/semantic_correction.py::explicit_correction_intent`（v2）只承认两种
**整句 fullmatch** 文法：

1. `Correct my <predicate 下划线换空格> from <旧值> to <新值>.`
2. `请把记忆里的<Host 中文别名>从<旧值>改成<新值>。`——而且别名表
   `_CHINESE_SLOT_ALIASES` 只覆盖饮品偏好那一组 predicate。

`material_proofreading_python_version` 没有中文别名，中文模板根本不会生成；英文模板要求整句
fullmatch，也不成立。于是该轮两个候选的 `correction_intent` 都是 `None`
（`state.db` → `human_memory_evidence`，`analysis-candidates-89b8f42f…` 快照实测
`intent= None`、`intent= None`），编译器与签发器都抛
`analysis_explicit_correction_intent_missing`。

### 分类

**Host 校验器缺陷（policy/grammar 缺陷），不是模型问题，也不是 SDK 规则问题。**

- 不是 prompt/schema 不匹配：模型完全能从工具面产出合法的纠正 op，实测已产出。
- 不是 SDK 规则：SDK 侧根本没被调用到（plan 在 Host 编译阶段就退化成 no_mutation）。
- 是 Host：`plans/2026-09-05-semantic-correction/CONTRACT.md` 明确把这套文法定为
  "deliberately bounded explicit language support, not general NL completion"，并把
  "Model compliance still requires the future native loop" 留作未完项。A6 就是那个 native loop，
  它证明了这套 bounded 文法无法承担 HM-AC-2/A6-7。

### 附带缺陷：拒绝原因不可见

`analysis_executor._derive_envelope` 只把 `RejectedOperation` 写成
`host_pre_admission_audit(reason_code, payload_hash)`，**没有任何日志**，也没有把
"整批被拒" 这一事实本身记成审计行。结果是：一次"模型提了变更但全部被拒"的轮次，在日志和
产品面上与"确实无可记"完全不可区分——正是 2026-09-05 `CREATE-SLOT-V3.md` 已经踩过一次、
这次又踩的同一个坑。

---

## 2. 裁决一：显式纠正的授权边界怎么放宽（A6-7 / HM-AC-2）

### 原设计的意图必须保留

`CONTRACT.md`：「A model choosing revise plus a true quote is insufficient authority.」
Host 独立推导意图，是为了挡住引述、第三方转述、假设、否定、历史回顾这五类"句子里出现了纠正
措辞、但当前用户并没有在纠正"的情形。这个职责不能交给模型。

### 裁决

把原来**捆在一起的两件事拆开**：

| 事项 | 谁负责 | 理由 |
|---|---|---|
| 「当前用户是否在纠正*这一条* slot」 | **Host 独立推导**（不看模型输出） | 这是授权判断，误判会导致未经授权的覆盖 |
| 「新值是什么」 | 模型给出，Host 逐字校验 | 误判只会写错值（用户可再纠正一次），不会造成未授权覆盖 |

新增第二条 Host 文法 `cue-anchor/v1`，与旧模板并存（旧模板保持**字节级不变**，
因为已持久化的候选快照必须能重新推导出同一个 `intent_hash`）：

**授予条件（全部满足）**
1. 候选 `subject_entity == user:self`；证据项是当前轮的 `user_message`。
2. 整句以 Host 闭集里的纠正提示词**开头**：更正一下 / 更正 / 纠正一下 / 纠正 / 改正 / 修正 /
   订正 / 我说错了 / 说错了 / correction / i misspoke / correcting myself，后接分隔符。
   —— "开头"这一条同时挡掉了 "昨天我说更正一下…"（历史）与 "有人说更正一下…"（转述）。
3. 整句不含任何失格标记：引号、如果/假如/假设/万一、不要/别把、疑问词（吗/呢/吧/？）、
   以及**含糊词**（好像/也许/可能/似乎/印象里/我记得/不确定 及英文对应词）。
   含糊句被排除是有意的——它属于 contested 分支（见裁决二）。
4. **旧值锚点**：候选 `object_value` 分词后，至少有一个长度 ≥2 的 token 逐字出现在句子里，
   且该 token **不出现在任何其它候选的 object_value 中**（取最长的那个作为 anchor）。
   这条替代了原来的 per-predicate 中文别名表：它对任意 predicate 都成立，
   同时保留了 CONTRACT 的「omitted old values do not authorize」，
   并让"两个候选共享被引用的 token"直接退化成无意图（歧义即拒绝）。

**编译器 + 签发器仍然双重校验的部分**
- 模型的 `exact_quote` 必须落在 Host 认定的那**同一句**里（旧模板路径仍要求整句相等）；
- 新值必须逐字出现在该 quote 内；
- 新值与旧值**互不为子串**（挡住"把 Python 3.12 改成 3.12"这类假纠正）；
- subject/predicate/qualifiers/privacy_class/information_attributes 一律沿用候选，模型不得改；
- 目标必须是 Host 实际签发的候选、精确 revision；同 slot 多候选、同句多意图一律拒绝；
- 发 Provider 前与授权前各做一次新鲜可见性检查；REVISE 仍然需要精确的
  `MemoryActionAuthorityRef`（缺失 → `NEEDS_USER_CONFIRMATION`）。

**已知的保守取舍（明确记录）**

- 把 `Python 3.12` 改成 `Python 3.12.1` 这类"新值包含旧值"的纠正会被拒。
  这是 fail-closed 的刻意选择，宁可拒也不放宽子串规则。
- 失格词表里包含英文单引号与 `不用`、`可能` 等；含这些字的纠正句会被拒。同样是宁拒不放。
- 既有规则 `analysis_correction_cannot_fallback_create`（同一证据上存在纠正意图时，
  semantic CREATE 一律拒绝，防止用 create 绕过纠正）会因为意图现在更容易成立而触发得更频繁：
  同一句纠正话里想**新建**另一条 semantic 会被拒。这仍是 fail-closed，不产生错误写入，
  且 episode/procedure/prospective 不受影响。

**旧数据不被追认**：第 20 轮的持久化候选快照里 `correction_intent` 是 `None`，
`authorize_plan` 比较的是快照里存的那个值，因此那次真实失败的 Run 与回执不会被本次修改改写。

---

## 3. 裁决二：含糊矛盾该不该 contested（A6-8）

### 实测现状

第 21 轮 batch `analysis-batch-62d4ac43…`（13:56:12）由**模型自己**收束为 no_mutation，
closure_reason 原文：「用户的话只是对上周 Python 版本的模糊回忆（"印象里""好像""你说呢？"），
表达的是不确定和求证，并未给出确定的新事实、版本变更或修改决定，无法据此创建或纠正语义记忆。」
`cognitive_conflict_groups` 0 行。注意这里 Host 没有拒绝任何东西——工具面**根本没有** contest 这个动作，
模型只能在「创建/纠正」与「什么都不做」之间二选一，于是选了后者。

### 依据（Memory SDK 程序契约，只读）

- `plans/2026-08-29-human-memory-digital-twin/acceptance.md:126`（HM-S3）场景就是本例：
  「事实变化与模糊冲突 … 随后又出现**含糊**相反说法 … 明确更新 supersede；**含糊冲突 contested**」，
  质量线是「含糊时不选边，依赖该值的任务要求确认」。
- `plan.md:107`：「**歧义为 contested**」。
- `slices/S3-cognitive-systems-recall.md:34`：「明确纠正 supersede、**含糊 contested**」；
  `:55`：「ambiguous target 或 contradictory evidence 转 contested 并要求确认」。

**裁决：契约要求 contested。当前的 `no_mutation` 收束是缺陷，`00-PLAN.md` 的 T21 不需要改写。**
判别轴不是"够不够格写入"，而是"该走哪个 operation"：语气明确 → supersede（Host 的 REVISE）；
语气含糊 → CONTEST。协议里根本没有 hedged/uncertain 这一档 epistemic status
（`EXPLICIT_USER / VERIFIED_EXTERNAL / OBSERVED_BEHAVIOR / LLM_INFERENCE / UNKNOWN`），
而 `LLM_INFERENCE`/`UNKNOWN` 被钉死在 candidate/draft，结构上不可能去 contest 一条 active claim。
所以 challenger 只能是 `EXPLICIT_USER` + `SOURCE_BOUND` + `ACTIVE`——含糊只改变**动作**，
不降低该发言的证据资格。

### 为什么 CONTEST 可以让模型选目标，而 REVISE 不行

SDK 把 CONTEST 明确排除在 protected action 之外（`protected_action = {REVISE, SUPERSEDE, SUPPRESS}`，
CONTEST 携带 `action_authority_ref` 反而被拒），理由写在
`ARCHITECTURE/ARCHITECTURE.md:297-298`：「CONTEST 不是 action-authority 旁路：target payload、
lifecycle、epistemic、verification 与 valid-time 必须完全不变，只允许 conflict flag 进入
CONTESTED；否则原子拒绝。」再加上 `mutation_contest_distinct_evidence_required`
（challenger 必须带一条 incumbent 没有的 evidence span）。

也就是说 CONTEST **不可能销毁或改写任何值**，最坏结果是一个待确认的 head。
因此 Host 对 CONTEST 的把关设为：
1. 当前轮 `user_message` 里有 Host 闭集里的**含糊标记**（印象里/好像/似乎/大概/也许/可能/
   我记得/记不太清/不确定 + 英文对应词）；
2. 该句**不是**已被识别的显式纠正（明确纠正只能走 REVISE，两条路径互斥）；
3. 该句不是引述/转述/假设（引号、如果/假如/假设、他说/她说/有人说/听说/据说）；
   —— 疑问收尾（「你说呢？」）在这里**允许**，那正是 HM-S3 的典型形态；
4. 目标必须是 Host 实际签发的候选与精确 revision；
5. challenger 的 subject/predicate/qualifiers/privacy_class/information_attributes 与 incumbent 完全一致；
6. challenger 值非空、与现值不同、且逐字出现在该句里；
7. operation 不得携带 `action_authority_ref`。

不由 Host 独立从句子里解析出"用户在质疑哪一条 slot"，是因为含糊句在词面上与目标 slot 没有
可靠的锚（第 21 轮 head 值是 `Python 3.13`，句子里只有 `3.12`）。把这一步交给模型，
在 CONTEST 非破坏性的前提下是可接受的风险，且 SDK 的 slot 冻结校验是最终裁判。

**已知取舍**：Host 构造 CONTEST 时按本 Host 自己创建/修订语义记忆时恒定使用的
`ACTIVE / EXPLICIT_USER / SOURCE_BOUND / valid_time=(None,None)` 填充；
若某条记忆由本路径之外产生且这些字段不同，SDK 会以
`mutation_contest_exact_slot_required` 原子拒绝——fail-closed，并且现在会被日志与审计点名。

---

## 4. 实现

新增 `host-analysis-prompt/v7` / `memory-analysis-proposal/v7` / `host-analysis-policy/v7`
（validator 仍为 `host-analysis-validator/v3`），v3/v4/v5/v5.1/v6 的 wire 与编译器逐字冻结、
仍可解析回放。

| 文件 | 改动 |
|---|---|
| `backend/deskpet/memory/semantic_correction.py` | 拆出 `_template_correction_intent`（冻结）与新增 `_cue_anchor_correction_intent`；新增 `hedged_contradiction_marker`；候选构建改两趟（锚点判别需要完整候选集）；`authorize_plan` 支持 `new_value=None` 分支与 CONTEST 校验 `_verify_contest` |
| `backend/deskpet/memory/analysis_proposal.py` | `revise_semantic` 编译按 `new_value is None` 分支校验（quote 落在同句内、新值与旧值互不为子串） |
| `backend/deskpet/memory/analysis_proposal_v7.py` | 新增：`contest_semantic` 动作、schema 分支、编译成 `MemoryMutationKind.CONTEST` |
| `backend/deskpet/memory/analysis_protocol.py` | 注册 v7 为当前协议 |
| `backend/deskpet/memory/analysis_executor.py` | CONTEST 也走 `authorize_plan`；逐条拒绝 + 整批拒绝的 payload-free 日志；`analysis_all_operations_rejected` 本身写入审计行 |

日志字段白名单：只输出 `reason / memory_type / role / item_id / operation_id` 与 reason code，
异常 `message`（可能回显证据文本）只进哈希、不进日志。

---

## 5. 验收对照

- **A6-7**：第 20 轮原句 + 部分引文的 `revise_semantic` → 同 `memory_id` 的 revision 2，
  新值可召回、旧值退出 active，签发恰好 1 条 `semantic-action` 授权。
- **A6-8**：第 21 轮原句 + `contest_semantic` → `cognitive_conflict_groups` +1、
  `cognitive_conflict_members` 恰好 2 条（incumbent r1 / challenger r2）、head revision 2
  的 `conflict_status = contested`、incumbent r1 内容不变、**普通召回不再返回该值**（不选边）。
  零 `MemoryActionAuthority`。
- **T22（争议期依赖该值要求确认）**属于回答侧行为，由 recall 过滤实现（contested 不进普通选择），
  本次不改回答策略。

`00-PLAN.md` 的 T21 措辞维持原样，不改成断言式矛盾。

---

## 6. 测试

新增：
- `backend/tests/memory/test_semantic_correction_cue_and_contest.py`（23 项）：
  文法单测（真实事故原句授予意图；引述/转述/假设/含糊/缺旧值/历史六种失格；共享锚点歧义；
  非 user:self 主语与非 user_message 来源；hedge 标记识别）＋ 真实公开 SQLite 全链
  （事故原句部分引文纠正 → revision 2；6 种拒绝路径 head 不变且零授权；含糊矛盾 → contested；
  4 种 contest 拒绝路径；整批被拒的日志与审计可见性）。
- `backend/tests/memory/test_analysis_proposal_v7.py`（3 项）：v7 为当前协议、v3–v6 仍可解析、
  `contest_semantic` 只出现在 v7 semantic 分支、v7 编译器拒绝他版请求。

回归：`tests/memory/test_semantic_correction.py`（22 参数）、`test_semantic_create_slot.py`（3）、
`test_analysis_proposal_v5/v6/v6_public_relation`、`test_procedure_adoption`、
`test_procedure_draft_classification`、`test_memory_display_producers` 全绿。

`tests/memory` + `tests/operation_audit` 全量改动前后逐项对比（`-p no:randomly`，分两批）：
- A 批：改动前 40 failed / 359 passed → 改动后 38 failed / 364 passed；
  新增失败 **0**；减少 2 项＝本次修好的 `test_analysis_proposal_v5.py`（v6 上线时遗留的过期断言）
  与一次 `test_procedure_scope_runtime` 抖动。
- B 批：改动前 40 failed + 2 errors / 314 passed → 改动后 40 failed + 2 errors / 337 passed；
  失败集合**逐项完全一致**，新增 23 项通过即本次新测试。

其余失败均为环境既有红（短索引 embedder、s5b/v46 迁移、prospective ack、s5c、
`test_preparation_audit` errors 等），与本次改动无关。
`tests/faults/test_memory_mutation_plan.py` 10 项失败同样是既有红：夹具
`s5b_memory_harness` 给 `SQLiteHumanMemoryBackend` 传 `clock=`，装机版签名不接受
（`TypeError: unexpected keyword argument 'clock'`），与本次改动无关。

**未完成项（诚实记录）**：`tests/sdk_adapters` 未能跑完。该目录的 `test_composition.py`
在 import 期就加载产品 `context.ServiceContext`，会打开机器全局的
`~/Library/Application Support/com.dennywanye.simpleharness/`（`lsof` 实测持有该目录下的
`backend.log` 句柄）。本次执行期间另一个工作树的 agent 正在跑同一套件、且桌面 App 在 18120 端口
运行，三个 pytest 进程全部停在 0% CPU 互相死锁。已终止本方进程，不与实时 App 争抢该共享目录。
这两个 importer（`test_composition.py` 只断言
`sdk_memory_analysis_executor is v7.analysis_authority`，`test_post_turn_invoker.py` 只用
`AnalysisLeaseFence` 与 `proposal_call`）都不触及本次改动的版本常量或编译路径，
但仍应在共享目录空闲时补跑。
