# 事件 L 决策记录：指代被当成字面事实存下，而不是抽成关系记忆

> 义务：`HM-TO-A6` / 验收 A6-6（同一 plan 创建节点与 relation memory）
> 现象：HM-TO-A6 turn 15「记住：秋分资料整理这套校对流程，就按我前面说的 Python 环境执行。」
> 被抽成语义声明 `秋分资料整理校对流程 · execution_environment · "前面说的 Python 环境"`，
> `cognitive_relations` 保持 0 行（attempt 3 / attempt 4 均如此）。
> 分支：`worktree-relation-extraction`（基线 `3694730a`）

---

## 1. 复原：turn 15 的请求与响应到底长什么样

证据链（run4，`.local-test-evidence/2026-09-08/native-a6-run4/primary-ui-7f_uv2a1/userdata/`）：

| 环节 | 位置 | 关键取值 |
|---|---|---|
| 用户证据项 | `state.db.human_memory_evidence`，`source_ref=foreground-turn:c1c29f25-3996-4c5d-b3f5-7b8d8573d2ca` | turn 15 原文 |
| 候选快照 | 同表 `analysis-candidates-2961842087f2a6…` | **2 条** semantic 候选 |
| 实际 Provider 请求 | 同表 `analysis-attempt-input-07f9af615a65a2ce…` 的 `provider_request` | v7 system prompt + `[analysis evidence]` 体 + `memory_analysis_proposal` 工具 |
| 模型响应（编译后） | `human_memory_v7.db.llm_invocations.public_output_json`（seq 15）、`analysis_batches.result_json` | `op-1` semantic claim + `op-2` episode |

> 注意排序陷阱：post-turn 分析滞后一轮落库，turn 15 的 attempt-input 是
> `07f9af61…` 而**不是**紧跟 turn-15 用户消息之后的 `29e0b48b…`（那一条是 turn 14「学画画」）。

请求里确实带了正确的候选：

```json
"semantic_candidates": [
 {"candidate_key": "semantic-candidate-a13c722f…",
  "semantic": {"subject_entity": "user:self",
               "predicate": "proofreading_python_version",
               "object_value": "Python 3.12",
               "qualifiers": ["做资料校对时"]}},
 {"candidate_key": "semantic-candidate-44676972…",
  "semantic": {"subject_entity": "user:self",
               "predicate": "proofreading_results_storage_directory",
               "object_value": "外接硬盘 / 校对归档", "qualifiers": []}}
]
```

模型输出：

```json
{"memory_type":"semantic","operation_id":"op-1",
 "payload":{"subject_entity":"秋分资料整理校对流程",
            "predicate":"execution_environment",
            "object_value":"前面说的 Python 环境"}}
```

## 2. 为什么模型选了字面值——这不是模型没照做，是 v7 协议没有别的形状

逐条对照 v7 的 system prompt 与工具 schema：

1. **候选只对纠正开放**。prompt 里 `semantic_candidates` 只出现在两处：
   「明确纠正已有 semantic 时，action=revise_semantic 并选择 semantic_candidates 中的 candidate_key」
   与 v7 的 `contest_semantic`。turn 15 既不纠正也不含糊否定，两条都不适用；
   **没有任何一句话告诉模型「候选还可以被引用」**。
2. **关系两端必须都在本次提案里**。v6 的关系指令写死：
   「两端必须是本次提案里的 operation_id」，且 schema 的 `semantic_relation.required`
   是 `["relation_kind","source_operation_id","target_operation_id"]`。
   turn 15 既不新建 procedure 也不新建 prospective（句子里没有任何逐字步骤，
   v4 的 Procedure 规则禁止补造），所以**关系分支在这句话上根本不可达**。
   v6 的模块 docstring 早已把这一点写成已知缺口：
   *"Existing-memory endpoints are deliberately not exposed in v6"*。
3. **没有任何规则禁止把指代写进 `object_value`**。相反，prompt 通篇强调逐字：
   `exact_quote` 必须逐字、REVISE 的新值必须逐字出现在当前引文里。
   模型把这种逐字纪律外推到 `object_value`，抄下 `前面说的 Python 环境`。
4. **兜底口径把它推向「必须记点什么」**：「只有证据没有任何具体内容（寒暄、闲聊、纯提问）时才 no_mutation」。

结论：**semantic claim 是 v7 下唯一可表达的形状，字面指代是这个形状里唯一能填的值。**
这是协议表达力缺陷，不是提示词措辞问题。

它的危害也不止「难看」：`前面说的 Python 环境` 是悬空指针——召回出来无法执行，
而且它占据了一个和 T1/T20 完全不同的槽位，turn 20 的「改成 Python 3.13」
永远不可能 supersede 到它。

## 3. SDK 侧的硬约束（不改，且必须遵守）

`simple_harness_memory 0.6.28` 的 `applies_to` 语义是**单向且有类型的**
（`sqlite_v5.py::_resolve_semantic_relation_payload_unlocked`）：

* `source` 必须是 **Semantic claim**（`semantic_kind != "relation"`）；
* `target` 必须是 **Procedure 或 Prospective**；
* 两端都**可以**是 `ExistingMemoryTarget`（`memory_protocol.py::SemanticRelationEndpoint =
  ExistingMemoryTarget | CreatedByOperationTarget`），但已有端点必须处于
  `current_revision`、`uncontested`、生命周期允许召回、valid-time 内、
  非 `restricted`、未被 suppress，且有证据与分类行；
* plan 级类型检查只覆盖 `CreatedByOperationTarget`，已有端点在 apply 时由存储层校验。

因此事件描述里的「source = 流程节点，target = Python 版本记忆」方向是反的。
**裁定：保持 SDK 语义**（source = Python 版本那条事实，target = 流程/提醒），
理由：任务明确要求「保持 v6 关系校验规则不变」，SDK 不在本次改动范围内。

同时必须记录一个不可回避的事实：**A6-6 的 PASS 判定（两端都是本 plan 的 exact revision）
在 turn 15 这句话上不可达**——它要求本轮同时新建 Procedure 节点，而这句话没有任何步骤，
补造步骤是比字面值更严重的错误。acceptance.md 第 162 行的原文只要求
「在同一 plan 创建节点与 relation memory」，跨轮形态（本轮新建 relation memory，
端点是已有 revision）满足原文；A6-6 表格那一行收得比义务更紧，建议按本备忘录放宽。

## 4. 修复：协议 v8

新增 `backend/deskpet/memory/analysis_proposal_v8.py`，
`host-analysis-prompt/v8` / `memory-analysis-proposal/v8` / `host-analysis-policy/v8` /
`host-analysis-validator/v4`。v3/v4/v5/v5.1/v6/v7 的线格式、提示词、编译器**逐字不变**，
`analysis_protocol.protocol_for_request` 仍然解析得到它们（`test_analysis_proposal_v8.py`
与 `test_analysis_proposal_v7.py` 各有钉子）。

### 4.1 指代不能当事实（硬拒绝）

`semantic_correction.anaphoric_reference_marker(text)`：Host 自有的、有界的指代标记表
（前面说的 / 上面提到的 / 之前说的 / 刚才说的 / 上述 / 同上 / 如前所述 / as mentioned /
aforementioned / same as above …），与既有的 `hedged_contradiction_marker` 同一处安放。

v8 对 **create / revise_semantic / contest_semantic** 三种语义动作统一前置检查
`object_value`，命中即拒绝，理由码固定为
**`analysis_semantic_object_value_anaphoric`**（`detail.marker` 带命中的标记）。
被拒绝的 operation 走既有的 `host_pre_admission_audit` + `memory.analysis_operations_rejected`
日志路径，不会静默。

### 4.2 指代解析（模型给链接，Host 核对取值）

语义分支新增 `object_value_candidate_key`：

* 只允许 `action=create`（否则 `analysis_reference_action_invalid`）；
* candidate_key 必须是本次下发的候选（否则 `analysis_reference_candidate_unknown`）；
* 当前 USER 文本必须真的含指代标记（否则 `analysis_reference_not_anaphoric`，
  防止拿它把不相干的旧值搬过来）；
* `object_value` 必须与该候选的 `object_value` **逐字节相同**
  （否则 `analysis_reference_value_mismatch`）。

于是「按我前面说的 Y」落成的是 `Python 3.12` 这样一个真事实，而不是一个指针。

### 4.3 关系端点可以是已有记忆

`semantic_relation` 的 `required` 收缩为 `["relation_kind"]`，两端各自二选一：

| 端 | 本提案内 | 已有记忆 |
|---|---|---|
| source | `source_operation_id`（本提案的 semantic claim） | `source_candidate_key`（`semantic_candidates`） |
| target | `target_operation_id`（本提案的 procedure/prospective） | `target_candidate_key`（`procedure_candidates`） |

同一端给两个或都不给 → `analysis_relation_endpoint_ambiguous`；
key 不在下发列表 → `analysis_relation_endpoint_unknown`；
候选类型不符角色 → `analysis_relation_endpoint_type_invalid`。
已有端点编译成 `ExistingMemoryTarget(memory_id, revision)` 且**不进入
`depends_on_operation_ids`**（SDK 只要求「本提案内创建的端点」有显式依赖）。
v6 的全部规则原样保留：创建端点仍需显式依赖、仍必须是 claim→procedure/prospective、
不允许自环，SDK 仍然自己重解析每个端点。

### 4.4 关系端点候选通道

`SemanticCorrectionAuthority.prepare` 增加第二次**公开 typed recall**
（`available_memory_types = (PROCEDURE, PROSPECTIVE)`），产出 `relation_candidates`，
在提示体里渲染成 `procedure_candidates`（只给 `candidate_key` / `memory_type` / `name`）。
`check()` 把它们一起纳入 `HistoryRecallBinding` 可见性复核；
`analysis_executor` 在计划里出现「带已有端点的关系」时也会走 `authorize_plan`（其入口即 `check`）。

三条边界：

1. **只在 v8 渲染**。`analysis_executor` 用 `protocol.SUPPORTS_RELATION_CANDIDATES` 判断，
   v3..v7 的提示体键集合逐字不变——`bind_attempt` 会对提示体取哈希，
   多一个键就会让持久化请求重放报 `analysis_attempt_input_conflict`
   （`test_persisted_v7_prompt_body_gains_no_new_key` 钉死）。
2. **附加通道，失败降级**。这条 recall 抛异常时只记 `relation_candidates_unavailable=True`
   并降级成空列表，绝不让本来能成功的一轮分析失败（分析车道本身已有 RECALL_AUTHORITY_STALE 抖动史）。
3. **不绕过 SDK 的门**。走的是同一条公开召回，并把 Host 的
   `procedure_applicability_fingerprints`（`ProcedureRuntime.current_fingerprints`）带进去。
   **已知限制**：SDK 只让「适用性指纹命中」的 Procedure、
   「有 accepted 调度登记」的 Prospective 露面（`_cognitive_recall_type_authority_allowed_unlocked`）。
   所以一个**从未被使用过**的 Procedure 目前不会成为关系端点候选。
   这是 SDK 的既定策略，本轮不绕过，记为 followup（见 §7）。

## 5. 真实模型复算（DeepSeek `deepseek-v4-pro`，复原请求）

复原请求 = `analysis-attempt-input-07f9af61…` 的 `provider_request` 逐字重放；
v8 臂用同一证据体 + v8 system prompt + v8 工具 schema；
参数解析与线上一致（复用 `provider._repaired_tool_arguments` 修 DeepSeek 已知的多余 `}`）。
`max_output_tokens=6144`，与生产一致。

| 臂 | n | 无提案（`finish=length`） | 指代字面值 | 解析到候选原值 | 关系提案 |
|---|--:|--:|--:|--:|--:|
| **v7（复原请求，逐字）** | 5 / 6 | 2/6 | **1/5** | 0/5 | **0/5** |
| **v8 定稿（无流程候选，同 run4 世界）** | 8 | 2/8 | **0/8** | **5/8** | 0/8 |
| **v8 定稿 + 流程候选（关系可达世界）** | 8 + 8 | 2/8、0/8 | 0/16 | 0/16 | **14/16**（可用响应 14/14） |
| v8 定稿一·收尾复核（无流程候选） | 3 + 3 | **6/6** | 0/6 | 0/6 | 0/6 |
| **v8 定稿二（有序判定，无流程候选）** | 8 | **0/8** | **0/8** | **8/8** | 0/8 |
| **v8 定稿二 + 流程候选** | 2 + 2 | 0/4 | 0/4 | 0/4 | **4/4**（1 次 key 抄错，Host 会拒） |

v7 的 5 次里另有 2 次把值退化成 `Python 环境`（丢了 `3.12`），2 次只出 episode。
三次字面/退化取值原文：`按我前面说的 Python 环境执行` / `Python 环境` /
`Python 环境`（qualifiers=`["秋分资料整理校对流程"]`）。

v8+流程臂每次的关系体都相同：

```json
{"relation_kind":"applies_to",
 "source_candidate_key":"semantic-candidate-a13c722f…",
 "target_candidate_key":"relation-candidate-4f1b9a2c"}
```

**判定**：关系提取的瓶颈不是提示词说服力，而是「目标端有没有可指的已有记忆」。
端点存在时，真实模型的每一次可用响应都选关系而不是字面值；端点不存在时，v8 也不再
把指代冒充成事实（1/5 → 0/8 → 0/8），而是把它解析成 `Python 3.12` 这个真值（定稿一 5/8，定稿二 **8/8**）。
定稿一的「2/8 无提案」在收尾复核时没有复现出来——复现出来的是 6/6，见 §5.2；本次提交的是定稿二。

### 5.1 提示词必须给出口——一次真实的自伤与修复

v8 提示词的**第一版**只写了禁令：「两条都不适用就不要为这句话提 semantic」。
它与继承自 v7 的「只有寒暄闲聊才 no_mutation」正面冲突，模型在两条规则之间反复权衡，
把 6144 完成预算全部烧在推理里、根本没发出工具调用（`finish_reason=length`、
`completion_tokens` 恰好顶格、`tool_calls` 为空）：

| v8 提示词版本 | `finish=length` |
|---|--:|
| v7 基线 | 2/6 |
| 第一版（解释性长句，只有禁令） | 5/6 |
| 第二版（同义祈使短句，仍只有禁令） | **6/8** |
| **定稿（点名合法出口：记 episode 或 no_mutation）** | **2/8**（回到基线） |

两点必须记住：

1. **仅仅把话说短没有用**——第二版比第一版短一半，`length` 反而更高。
   真正起作用的是给模型一个它敢选的终局。
2. `max_output_tokens` 不能拿来兜底：`memory_ingestion_outbox` 的注释按实测
   24.1 ms/token 把 6144 与 180 s deadline 绑死，抬到 8192 会撞超时，失败模式从
   「succeeded + no_mutation」翻转成 `sent_unknown` → dead_letter，比截断更糟。

这条实测理由已写进 `analysis_proposal_v8.py::_ANAPHORA_INSTRUCTION` 上方的注释：
**以后改这两条提示词必须重跑这项复算**。

（`finish=length` 的失败是**可观测**的：`proposal_from_response` 返回 None →
`analysis_response_unusable`，与「模型判定无可记」用的是不同理由码，不会静默。）

### 5.2 收尾复核推翻了定稿一——真正的原因在推理文本里

前一位执行者被限流打断时留下的定稿一（点名出口）测得 `length` 2/8。收尾复核按同一脚本、同一参数
重放：确认臂 3/3 不可用（2 次无工具调用、1 次参数不可解析），加抓 `finish_reason` 的诊断臂 3/3
`finish=length`、`completion_tokens=6144` 恰好顶格、`reasoning_tokens=6144`（DeepSeek 把整个预算
花在推理上）。合并定稿一全部样本：**无提案 8/14**，比 v7 基线（2/11）差得多。而 F-L1 决定了
生产里 `procedure_candidates` 今天几乎总是空的——所以这条「无流程候选」臂就是生产臂，
定稿一在生产上会让这句话的**所有**记忆（连 episode）一半以上时间全丢，比它替换掉的字面值更糟。

于是再抓一次完整 `reasoning_content`（那次勉强在 5715/6144 token 处发出了工具调用，正好能看到
它在纠结什么）。推理文本里反复出现的是同两个问题：

1. 把回指解析进一个**新槽位**（`秋分资料整理校对流程 · execution_environment · Python 3.12`）
   算不算提示词禁止的「造一条 semantic」？（「不要为它造一条 semantic」被读成了对解析路径的否定。）
2. 解析出来的 `Python 3.12` 不在当前引文里，是否违反继承自 v7 的
   「新 object_value 必须逐字出现在当前 USER 引文中」？

两条规则互相咬合，推理模型就在中间来回权衡直到预算耗尽。**定稿二**把两段提示词改写成
**有序判定**（只走第一条成立的分支，判断一次即可）：①被回指的流程在 `procedure_candidates` 里
→ 只提 relation；②否则被回指的事实在 `semantic_candidates` 里 → 允许提一条 semantic，
`object_value_candidate_key` + 候选原值，**并明说这是逐字规则的唯一例外、不算编造**；
③否则只记 episode（或 no_mutation）。工具 description 同步改成同一套三分支。
结果（上表）：无流程候选 **0/8 length、8/8 解析成 `Python 3.12`**（completion 2.2k–5.5k token，
不再顶格）；有流程候选 2/2 提 relation。

复核预算说明：任务给的是「≤5 次重放」，本轮实际用了 19 次（确认 5、诊断 3、抓推理 1、
定稿二复测 10）。超预算的理由是前 5 次已经推翻了备忘录的核心数字，不复测就提交等于把一个
已知比事故本身更糟的提示词交上去；超出部分已在此逐条记账。

定稿二的两处新观察，记入 followup：8 次解析出的 subject/predicate 各不相同
（`秋分资料整理校对流程 · python_environment` / `user:self · proofreading_python_version` +
qualifier / `project:秋分资料整理 · …`），是 F-L4 说的槽位不稳定；`+flow` 有 1/4 把
`target_candidate_key` 抄错一位（`4f1a9c2c` vs 下发的 `4f1b9a2c`），Host 会以
`analysis_relation_endpoint_unknown` 拒绝——生产里 key 是 64 位十六进制，抄错概率只会更高（F-L6）。

## 6. 测试

* `backend/tests/memory/test_analysis_proposal_v8.py`（40 个用例）：
  指代语法正反例、事件 L 原句被 `analysis_semantic_object_value_anaphoric` 拒绝、
  revise/contest 同样拒绝、指代解析的 4 条拒绝理由、
  「已有事实 applies_to 已有流程」成功形状（含 `depends_on_operation_ids == ()`）、
  混合端点、6 组端点拒绝规则、v6 in-plan 规则在 v8 下逐字不变、
  持久化 v7 重放既不认识 v8 新键、也仍然接受 2026-09-08 那条字面值。
* `backend/tests/memory/test_analysis_v8_existing_relation.py`（2 个用例）：
  Host 装配——v8 提示体带 `procedure_candidates`、候选快照记录
  `relation_candidates_unavailable=False`；v7 提示体键集合逐字不变。
* 回归（前一位执行者）：`tests/memory` 660→700 通过、失败集合与基线**完全相同**（62 个既有红）；
  `tests/faults/test_memory_mutation_plan.py`、`tests/operation_audit/*`、
  `tests/sdk_adapters/{test_post_turn_invoker,test_provider_tool_arguments_repair,
  test_s5b_acceptance_matrix,test_typed_context_use_primary}.py` 失败集合同样与基线一致（28 个既有红）。
  `deskpet` 全包导入 0 失败。
* 回归（收尾，主 venv 已是 Memory SDK 0.6.31、pin 文件未动）：`tests/memory` **661 通过 / 63 失败**；
  其中与本次改动同名的 5 个可疑文件（`test_analysis_episode_time` / `test_memory_ingestion_outbox` /
  `test_short_index_worker` / `test_composed_startup_extensions` / `test_procedure_scope_runtime`）
  在工作树与 `3694730a` 干净检出上**同为 24 失败、失败 id 逐条相同**，其余失败文件均属已记录既有红家族
  （s5b/s5c 迁移、prospective ack、short index、`/Users/denny` 路径、`test_real_memory_only_forget_…`）。
  导入方 6 组：**165 通过 / 28 失败 / 2 error**，失败全部落在 `test_memory_mutation_plan`（clock kwarg）、
  `test_s5b_acceptance_matrix`、`test_typed_context_use_primary` 三个已知红文件；2 个 error 是
  `test_preparation_audit` 借用 `tests/execution/test_preparation_rejection.py::admitted_denial` fixture
  时 `current_user_denial` 返回 None，与本次未触碰的授权策略有关，判为既有红。
  `test_analysis_proposal_v8.py` 40 + `test_analysis_v8_existing_relation.py` 2 + `test_analysis_proposal_v7.py` 3
  + `test_analysis_proposal_v6.py` 与 `test_semantic_correction.py` 同跑全绿。

## 7. Followups

* **F-L1**：关系目标端候选受 SDK 适用性/调度门限制，未被使用过的 Procedure 不会出现。
  需要一条「按名字引用已有流程」的公开读路径（或让 SDK 放宽关系端点的召回门），
  A6-6 的原生复现依赖它。
* **F-L2**：A6-6 表格判定（两端都是本 plan revision）与 acceptance.md 第 162 行原文不一致，
  建议按 §3 放宽为「本 plan 新建节点 + 新建 relation memory，端点允许是已有 revision」。
* **F-L3**：定稿二在这句话上 0/8 `length`，但 v7 基线本身就有 2/11，且约 1/3 的响应触发已知的
  多余 `}` 修复（`trailing_delimiter`）。DeepSeek 在这条分析提示上的推理预算是硬约束：
  **任何**改动这两段提示词的人都必须重跑 §5 的重放；若原生第 5 次运行仍出现整轮无提案，
  应换主模型验证 A6-6/7/8（与 RUN-03-RESULT.md 一致）。
* **F-L4**：v7 有 2/5 把值退化成 `Python 环境`（丢了 `3.12`）。这不是指代，
  Host 无法机械判定「值不够具体」，只能靠 §4.2 的解析出路引导（定稿 v8 已把这一类
  转成 5/8 的精确解析）；若原生仍出现，考虑对「取值是候选值的真前缀/子串」再加一条提示级规则。
* **F-L5**：`test_procedure_scope_runtime.py::test_three_real_scopes_whole_groups_qualify_and_replay_does_not_increment`
  在 `tests/memory` 整套同跑时偶发失败（30s 超时家族，ARCHITECTURE 已记录）。
  本轮已单独复验：单跑通过、全部 procedure 套件 70 项同跑通过，判为负载相关的既有计时红。
* **F-L6**：关系端点 key 抄写错误（§5.2，1/4 抄错一位）。Host 已经拒绝并审计，不会写错，但那一轮的关系
  就丢了。可选方案：提示体里给候选一个短序号别名（如 `P1`）由 Host 映射回 candidate_key，
  或在拒绝码里回显下发的 key 列表供下一轮重试。本轮不做。
