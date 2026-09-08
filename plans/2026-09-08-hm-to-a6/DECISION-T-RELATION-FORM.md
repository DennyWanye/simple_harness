# 事件 T 决策记录：A6-6 要的那条 `applies_to` 边，形态到底是什么

> 义务：`HM-TO-A6` / 验收 A6-6（同一 plan 创建节点 + relation memory）
> 现象：分析协议 v8 上线后，turn 15「记住：秋分资料整理这套校对流程，就按我前面说的 Python 环境执行。」
> 仍然产不出关系：第 8、9 次原生跑 `cognitive_relations` 恒为 0；候选通道每一轮都记
> `relation_candidate_reasons … codes=relation_procedure_applicability_absent`。
> 分支：`worktree-rel-form`（基线 `eb9f4449`）
> 证据：`.local-test-evidence/2026-09-09/native-a6-run9/primary-ui-8whts2lo/`

---

## 0. 先把第 9 次 turn 15 到底发生了什么说准

备忘录此前的描述是「模型把回指解析成一条语义声明，关系没提」。第 9 次的证据比这更糟：

| 环节 | 证据 | 取值 |
|---|---|---|
| 请求 | `state.db.human_memory_evidence` → `analysis-attempt-input-5c2f2d1b…` | v8 system prompt；`semantic_candidates` 3 条（含 `proofreading_script_python_version = "Python 3.12"`）；**`procedure_candidates: []`** |
| 响应处理 | `native.log:2138` | `memory.analysis_operations_rejected rejected=1 kept=0 codes=[('op-1','analysis_operation_payload_invalid',{'reason':'ValueError'})]` |
| 结果 | `human_memory_v7.db.analysis_batches`（`cf414828…`）、`llm_invocations` seq 15 | `{"closure_reason":"analysis_all_operations_rejected","operations":[],"outcome":"no_mutation"}` |

也就是说 turn 15 **一条记忆都没落**，不是「落错了形状」。那个 `ValueError` 的内容在 §3 的复算里被逐字复现出来了：
`reason_code must be non-blank, bounded, and contain no NUL`——DeepSeek 把整句中文解释写进了 `reason_code`，
SDK 的 `disclosure_protocol._identifier` 上限是 **256 UTF-8 字节**。这条和协议版本无关，v8 复算 11 次里命中 2 次。

`procedure_candidates: []` 则是 F-L1 的原样复现：SDK 只让 `active`/`reinforced` 且适用性指纹命中的 Procedure
参与召回，本旅程从没「用过」任何流程，所以 v8 有序策略的分支①（唯一能产出关系的分支）在生产里永远不成立，
每次都掉到分支②，产出一条 semantic。

---

## 1. 裁定：形态选 A，A6-6 那一行按 acceptance 原文放宽

先回答任务问的第一个问题：**v8 的 schema 本来就表达得出所需形状，缺的只是策略。**
`semantic_relation` 的两端各自二选一（`_endpoint`），所以下面三种都编译得过：

| 形态 | source | target | 本 plan 新建了什么 |
|---|---|---|---|
| **A（本轮采用）** | `source_candidate_key` = T1 那条已有事实 → `ExistingMemoryTarget` | `target_operation_id` = 本轮新建的流程节点 → `CreatedByOperationTarget` | 流程节点 + relation memory |
| B | `source_operation_id` = 本轮**再造**一条同值 semantic | 同上 | semantic + 流程节点 + relation memory |
| C（v8 分支①） | `source_candidate_key` | `target_candidate_key` = 已有流程 | 只有 relation memory |

A6-6 表格那一行的字面判据「`source_memory_id`/`target_memory_id` 均为本 plan 的 exact revision」**只有 B 满足**。
裁定**不按 B 做，改写 plan 行**，三条理由：

1. **B 会造出第二个槽位**。T1 的记忆已经是 `user:self · proofreading_script_python_version · "Python 3.12"`；
   B 要求本轮再写一条同值的 semantic（复算里模型给的是 `秋分资料整理校对流程 · 执行环境 · "Python 3.12"`）。
   这正是事件 L 立案要消灭的东西（F-L4 槽位不稳定）：T20「改成 Python 3.13」只会 supersede 其中一条，
   另一条永远停在 3.12，而图谱边指向的恰恰可能是停住的那条。
2. **义务原文没有这么紧**。逐字引用 SDK 仓 `simple-harness-memory-sdk` 的
   `plans/2026-08-29-human-memory-digital-twin/acceptance.md`（下同）：

   | 出处 | 行 | 逐字原文（节选） |
   |---|--:|---|
   | 「测试义务矩阵」**HM-TO-A6** 行（本项所属义务） | 162 | 「clean-wheel public API 在同一 plan 创建节点与 relation memory，验证 edge 更新/纠正/争议/ordinary projection policy 过滤/relation 或 endpoint 遗忘/close-reopen」 |
   | 场景表 **HM-S12** 行 | 135 | 「clean-wheel public API 创建**两个** canonical nodes + 一条 relation memory；普通图谱显示一条可追溯 edge」 |
   | 「测试义务矩阵」**HM-TO-A2** 行 | 158 | 「clean-wheel public API 在同一原子 plan 正向创建**两个端点**及一条 `applies_to` Semantic relation，并核验 evidence/classification/epistemic/exact endpoint refs」 |

   本项（A6-6）落在 HM-TO-A6 行，它只要求「创建节点与 relation memory」——形态 A 在同一个 plan 里
   既建了节点（流程），又建了 relation memory，满足原文。
   HM-S12 与 HM-TO-A2 那两句更紧的「两个 canonical node / 两个端点」**不是本次原生跑的义务**：
   HM-TO-A2 明写它由 **clean-wheel public API** 的正向 delivery oracle 承担（SDK 公共 API 直接造两个端点，
   不经 Host 分析车道、不经真实模型），HM-S12 是那条 oracle 的场景描述。本轮不在原生 A6 跑里重新证明它，
   也**不因为**形态 A 只新建一个端点就认为那条义务失守——它由另一条测试路径履行。
   这条「HM-TO-A2 的 clean-wheel oracle 当前是否真的覆盖了两端点正向创建」需要单独核对，记为 **F-T6**。
   HM-S12/S3 的「知识边」意图也是把**已有知识**连到流程上，而不是连到它自己的一份副本上。
3. **有先例**。A6-9/A6-10 就是「计划里的那句转录与契约不符 → 改转录、不改产品代码」
   （`DECISION-GRAPH-PROJECTION-POLICY.md` §4）。本轮同样只改 `00-PLAN.md` 与 `scripts/native/a6_verify.py`。

C 不是选项：F-L1 决定了 `procedure_candidates` 在这条旅程里恒空，而且事件 S 已经量到 SDK 0.6.34 解析不了
**已有** Procedure 端点（观测提交的 revision 没有分类行），Host 因此具名扣留它。形态 A 用的是**本 plan 新建**
的 Procedure，分类行由 mutation-apply 在同一个事务里写下（§2），不受那条限制。

已按此改写：`00-PLAN.md` 的 A6-6 行、turn 15 行、turn 16 行，以及 `a6_verify.py::item_a6_6`。
判据（2026-09-09 评审后收紧，见 §评审 4a）：

* 只看 `relation_kind='applies_to'` 的知识边（evolution 血缘边不算）；
* relation memory 自身由本 plan 新建（其 revision 行的 `plan_id`/`plan_hash` 与关系行一致，
  且 `relation_memory_id` 在 `cognitive_memory_heads` 中存在）；
* **target 端点**由本 plan 新建，且它是流程节点（`cognitive_memory_heads.memory_type='procedure'`）；
* 两端 exact revision 均能在 `cognitive_memory_revisions` 里解析。

第三条不能松成「至少一个端点」：那样一来「本轮新造一条同值 semantic 当 source + 连一条**旧**流程当 target」
（形态 B 的半成品）也会 PASS，而那正是本节判掉的重复槽位形状，且与改写后的 A6-6 计划行相反。

---

## 2. SDK 0.6.34 契约实测（不改 SDK，形态 A 必须踩准的四条）

以**已安装的** `simple_harness_memory 0.6.34` 与 `simple_harness 0.7.10`（`runtime/memory_protocol.py`）为准。

1. **已有 SEMANTIC 端点不需要适用性指纹。**
   `sqlite_v5.py::_resolve_semantic_relation_payload_unlocked` 全程不引用
   `procedure_applicability_fingerprints`，也不调用 `_cognitive_recall_type_authority_allowed_unlocked`；
   而 `backends/history_visibility.py::_recall()` 虽然刻意传空指纹集，那条类型授权门对 semantic **claim**
   直接 `return True`（只有 `procedure` 需要指纹命中，`semantic_kind=="relation"` 恒 `False`）。
   所以 T1 那条事实当 source 端点没有任何指纹问题。它要过的是另外 13 项：所属 principal 一致、
   `current_revision` 与给定 revision 相等、`uncontested`、`lifecycle=active` 且 epistemic/verification 组合合法、
   valid time 内、content hash 自洽、方向类型（source 必须是 `semantic_kind != "relation"`）、
   `semantic_claims` 有 typed 行、`cognitive_evidence_spans` 非空、
   **`cognitive_classification_decisions` 恰好 1 行且 `decision_hash` 非空**、非 `restricted`、
   记忆级与逐证据级 suppression 均未命中。
2. **分类行只有 mutation-apply 会写**（`sqlite_v5.py:8680` 是全包唯一的
   `INSERT INTO cognitive_classification_decisions`），每个 operation 一行。T1 由一个 accepted plan 创建，
   因此 revision 1 上有分类行、证据行与 typed 行——它当端点是成立的。
   （对照：Procedure 靠 `record_procedure_observation` 前进到 `active` 时走 `_copy_cognitive_revision_unlocked`，
   那条路径**不复制**分类行，于是新 head 没有分类行 → 事件 S 的 `MemoryCorruptionError`。仓库里的 0.6.35
   把查询改成「最近的已分类祖先」修掉了它；本轮按已安装的 0.6.34 契约行事。）
3. **本 plan 新建的 Procedure 端点必须是 ACTIVE。**
   端点解析同样走 `_cognitive_recall_state_allowed`，procedure 只允许 `{active, reinforced}`；
   而 `analysis_proposal_v4` 只在 `intent_kind="adoption"` 时给 ACTIVE，其余是 DRAFT。
   所以 v9 分支②的**前置条件**写成「用户本句就是在决定今后照此执行」，而不是叫模型给所有被点名的流程都贴
   adoption——分类口径一个字没动，不成立就继续往下掉分支。
4. **本 plan 内的端点必须显式依赖且排在关系之前。**
   `memory_protocol` 的 `RELATION_ENDPOINT_DEPENDENCY_REQUIRED` 与 `INVALID_DEPENDENCY_ORDER`
   要求端点 operation 在 `operations` 元组里索引严格更小，且出现在 `depends_on_operation_ids` 里
   （拓扑排序在这之后才跑，救不了顺序）。Host 自己推依赖，提示词负责讲顺序。

另外记两条与 A6-7/A6-9 相关、但**不属于本轮**的事实（两种形态一视同仁，不构成选 A 或选 B 的理由）：
`cognitive_relations` 行不可变（schema 触发器禁 UPDATE/DELETE），端点 revision 是 apply 当时的 head；
`twin_builder` 只在两端 `revision == head_revision` 且未 contested 时投影这条边。
因此 T20 supersede 之后这条边会**停止投影**，而不是「指向新 revision」——A6-7 那一行若要 PASS，
需要纠正那一轮重新建一条关系，或改判据。记为 F-T3。

---

## 3. 真实模型复算（复原请求，DeepSeek）

复原请求 = 第 9 次 `analysis-attempt-input-5c2f2d1b…` 的 `provider_request` 逐字重放
（system 换成对应臂的提示词、tools 换成对应臂的 schema，`[analysis evidence]` 体、`max_output_tokens=6144` 一字不改）。
每个响应都离线过一遍 Host 编译器（`compile_proposal`，带同样的三条候选）才算「验证器接受」。
驱动脚本已提交为 `scripts/native/a6_replay_t15.py`：凭据路径由 `--credentials`（或环境变量
`A6_REPLAY_CREDENTIALS`）给出，脚本里不硬编码任何路径或密钥，密钥只用于 Authorization 头，
**不打印、不写入输出 JSON、不入库**；离线判定那半段（`Replay` + `classify`）本轮已用录制响应跑通。
被重放的持久化请求 `t15_attempt_input.json` 与 12 份原始样本 JSON 留在 scratchpad，未提交
（它们是整份证据体的拷贝，不是凭据）。

| 臂 | 模型 | n | `finish=length` / 无可用提案 | 指代字面值 | 提出关系 | 关系被 Host 接受 | 整条提案被接受 |
|---|---|--:|--:|--:|--:|--:|--:|
| v8（现行，对照） | `deepseek-v4-flash` | 8 | **1/8** | 0/8 | **0/8** | 0/8 | 5/8 |
| v8（现行，对照） | `deepseek-v4-pro` | 3 | 1/3 | 0/3 | **0/3** | 0/3 | 2/3 |
| v9 初稿（分支②未点明优先级） | `deepseek-v4-flash` | 4 | **4/4** | 0/4 | 0/4 | 0/4 | 0/4 |
| v9 二稿（点明优先级与终止） | `deepseek-v4-flash` | 9 | 1/9 | 0/9 | **8/9** | 6/9 | 6/9 |
| **v9 定稿（+ `reason_code` 规则）** | `deepseek-v4-flash` | **9** | **1/9** | **0/9** | **8/9** | **8/9** | **8/9** |
| **v9 定稿** | `deepseek-v4-pro` | **3** | 0/3 | 0/3 | **3/3** | 3/3 | 3/3 |

v8 的 9/11 次产出的是分支②那条 semantic（`object_value` 解析成 `Python 3.12`，没有一次抄回指），
形状正确但没有边——这就是第 8/9 次原生跑 `cognitive_relations=0` 的机制。

三条必须记住的实测结论：

### 3.1 初稿的 4/4 `finish=length` 是与继承规则打架，不是话太长

抓到的推理原文（`reasoning_tokens=6144` 顶格）反复在问同两件事：
「本句根本没有步骤，建 Procedure 算不算 `补造`？」「该走②还是③？」——
分支②与继承自 v4 的「没有可直接绑定的实际步骤则不提 Procedure，不能补造」正面冲突，模型就在中间来回权衡。
定稿在分支②里加了两句就回到基线：

* 「这一条成立就直接照下面产出，**不用再和③比较**」——给判定一个终点；
* 「**本分支不适用**上面『没有可直接绑定的实际步骤就不提 Procedure』那一条，因为这些字全部来自本句，不是补造」——
  直接宣告优先级，而不是让模型自己推。

这和事件 L §5.1 的结论同构：起作用的是消掉规则冲突、给出可选的终局，不是把话说短。

### 3.2 `reason_code` 超长是当前最大的单点损失，与协议无关

二稿 9 次里有 3 次、v8 11 次里有 2 次因为 `reason_code` 写成整句中文（>256 UTF-8 字节）被拒。
在 v8 上它只毁掉那一条 operation；在分支②下被毁的往往是**关系的端点**，于是关系跟着一起没。
定稿加了一句「`reason_code` 只写简短的英文小写标识符……超过 256 字节 Host 会整条拒收」，
flash 的整体接受率从 6/9 抬到 8/9。这条也是第 9 次 turn 15 全轮丢失的直接原因（§0）。

Host 侧还应该在下发前就用 schema `maxLength` 或具名理由码挡住它（现在只能靠 SDK 抛 `ValueError`
再被兜底成 `analysis_operation_payload_invalid`），但那要动 v8 也在用的 schema 对象，记为 F-T1。

### 3.3 端点被拒时，v6/v7/v8 会连累整轮——v9 加了唯一一条新准入规则

二稿那 3 次里，procedure 被拒、关系活了下来，于是 `MemoryMutationPlan` 以
`operation has unknown dependencies` 抛在 `legacy.compile_proposal` 内部，**整轮记忆全丢**。
根因是 v6 起在线内端点校验用的是**原始**提案里的 operation id，不是真正编译成功的那些。
v8 里几乎碰不到（它基本不提本轮端点），分支②每次都提，于是变成常态。

v9 因此把 `host-analysis-validator/v4` 抬到 **`host-analysis-validator/v5`**：
线内关系端点必须是**已经编译成功**的 operation（`memory_protocol` 本来就要求端点排在关系之前，
「已编译」正好是等价判据）。不满足时只拒关系一条，理由码 `analysis_relation_endpoint_unknown`，
本轮其余记忆照常落库。**v8 保持原样**——持久化的 v8 请求必须重放成它当初的结果。

---

## 4. 改动

### 4.1 新协议 `host-analysis-prompt/v9`（不是就地改 v8）

提示体与 system 文本会进 `bind_attempt` 的哈希，就地改 v8 会让持久化请求重放报
`analysis_attempt_input_conflict`。所以新增 `backend/deskpet/memory/analysis_proposal_v9.py`：
`host-analysis-prompt/v9` / `memory-analysis-proposal/v9` / `host-analysis-policy/v9` /
`host-analysis-validator/v5`，在 `analysis_protocol` 里注册为当前协议，v3/v4/v5/v5.1/v6/v7/v8 逐字保留、
仍可 `protocol_for_request` 解析出来。

线格式**不变**（实测：v3/v4/v5/v5.1/v6/v7/v8 各自 `ANALYSIS_SYSTEM_INSTRUCTION + 工具 description +
canonical 化的 `PROPOSAL_TOOL_SCHEMA` 三者拼起来的 sha256，在工作树与基线 `eb9f4449` 上逐条相同）：
`PROPOSAL_TOOL_SCHEMA` 直接复用 v8 的**同一个对象**（用例 `is` 断言钉死），
编译器复用 v8 抽出来的 `_compile_validated_proposal`（纯重构，v8 行为逐字不变）。
v9 只改策略文本：

* 有序判定从三分支变四分支，新增的 ②见 §3.1；
* ①③④沿用 v8 定稿二的逐字措辞（那是把 `finish=length` 从 6/8 压回 0/8 的措辞，不动）；
* 末尾追加 `reason_code` 规则（§3.2）。

### 4.2 唯一一条新准入规则

`_compile_validated_proposal(..., created_endpoint_must_survive=True)`，见 §3.3。默认 `False`，v8 走默认。

### 4.3 计划与验证脚本

`00-PLAN.md` A6-6 / turn 15 / turn 16 三行按 §1 改写；
`scripts/native/a6_verify.py::item_a6_6` 判据同步（`--selftest` 18 项通过），
自检夹具的 `cognitive_memory_heads` 补上 `memory_type` 列，好让 A6-6 在自检里仍然可达。
`ARCHITECTURE/MEMORY_SDK_BOUNDARY.md` 追加本轮条目（口径改写 + 引文出处 + F-T6）。
新增 `scripts/native/a6_replay_t15.py`：§3 那份真实模型复算驱动，凭据路径参数化（见 §5d）。
`scripts/native/a6_driver.sh` **不需要改**：turn 15 的原话本来就点名了流程并作了决定，正是分支②的触发条件。

---

## 5. 测试

* `backend/tests/memory/test_analysis_proposal_v9.py`（27 项）：协议身份（v3..v9 一条不丢、持久化 v8 仍渲染
  v8 自己的提示词）、schema/理由码与 v8 同一对象、有序四分支的策略文本（含 §3.1 三句与 §3.2 一句）、
  turn-15 目标形状的编译结果（ACTIVE 流程节点、source 是已有事实的 exact revision、
  `depends_on_operation_ids == ("op-flow",)`、端点排在关系之前、本轮不新增第二条 semantic）、
  DRAFT 仍是 DRAFT、逐字步骤规则没有豁免、`host-analysis-validator/v5` 的正反面
  （v9 只拒关系并保住本轮其余记忆；同一份提案在 v8 上抛 `unknown dependencies`）、
  事件 L 的字面值仍被拒、分支③的解析仍然可用、v9 与 v8 编译同一份提案结果逐字相同；
  另加评审补的两组（§评审 1b/2c）：**关系排在端点之前**时 v9 只拒关系、v8 被 SDK 准入整轮抛掉，
  以及 v3/v4/v5/v5.1/v6/v7/v8 各自线格式（system 指令 ‖ 工具 description ‖ canonical schema）的
  **sha256 字面金值** `WIRE_GOLDENS` —— 任何一条变了都意味着已持久化的请求重放会失败。
* `backend/tests/native/test_a6_verify_a6_6.py`（11 项，评审 4b 补）：给 `item_a6_6` 的判据造带
  `relation_memory_revision` 的夹具库，逐一钉住形态 A PASS / 两端皆旧、evolution 行、悬空端点、
  relation memory 属别的 plan、relation memory 不在 heads、只新建 source、新建 target 但不是流程节点
  一律 FAIL、无关系行 INCONCLUSIVE，以及缺 `relation_memory_revision` 列时判据照常成立。
* `backend/tests/memory/test_analysis_v9_named_workflow_relation.py`（1 项，端到端）：
  第一批建 T1 事实；第二批把**录制的真实模型响应**（`deepseek-v4-pro`、v9 臂、第 3 个样本）经真实 outbox、
  真实分析车道、真实候选通道、真实 Memory SDK 0.6.34 apply 落库，断言
  `cognitive_relations` 恰好 1 行 `applies_to`/`knowledge`；source 是 T1 记忆的 current revision（且 `semantic_claims` 全表只有 T1 那一条，本轮没造副本）；target 与 relation memory 的 revision 行 `plan_id`/`plan_hash` 与关系行相同；
  source 的 plan 不是本 plan；`build_twin_graph_view` 显示 1 条边、semantic → procedure，relation memory 不是节点。

### 回归（同一 venv，Memory SDK 0.6.34，pin 文件未动）

| 集合 | 工作树 | 基线 `eb9f4449`（一次性 worktree） | 失败集合 |
|---|---|---|---|
| `tests/memory/test_analysis_*.py` + `test_semantic_*.py` + `test_procedure_adoption.py` | 190 passed / 4 failed | 171 passed / 4 failed | **逐条相同**（`test_analysis_episode_time.py` 的 4 个既有红） |
| `tests/sdk_adapters/test_s5b_acceptance_matrix.py` + `tests/memory/test_memory_ingestion_outbox.py` | 14 passed / 17 failed | 14 passed / 17 failed | **逐条相同**（`diff` 为空） |
| `tests/operation_audit` + `tests/faults/test_memory_mutation_plan.py` | 104 passed / 10 failed / 2 errors | 104 passed / 10 failed / 2 errors | **逐条相同**（只有耗时行不同） |

新增的 19 个用例全绿；`test_analysis_proposal_v8.py` 里两处「v8 是当前协议」的断言按 v9 上线改写为
「v8 仍可解析」，`test_analysis_v8_existing_relation.py` 显式把 config 钉到 v8（它考的就是 v8 的装配）。

---

## 6. Followups

* **F-T1**：`reason_code` 的 256 字节上限只由 SDK 抛 `ValueError` 兜底。应在 Host 侧用 schema `maxLength`
  + 具名理由码在下发/编译前挡住。要动 v8 也在用的 schema 对象，需要再开一个协议版本。
* **F-T2**：F-L1 未解。`procedure_candidates` 在生产里仍恒空，分支①（连两条已有记忆）依旧不可达；
  一旦 SDK 放宽（或 0.6.35 的分类行祖先查询上线），分支①会优先于②命中，届时要重跑 §3。
* **F-T3**：A6-7 那一行「图谱只显示 1 条 active edge，端点为新 revision」与 SDK 契约不符：
  `cognitive_relations` 不可变，supersede 之后边**停止投影**而不是改指。要么纠正那一轮重建关系，要么改判据。
  两种关系形态在这一点上表现相同，不影响 §1 的裁定。
* **F-T4**：分支②建出的流程节点，步骤是本句里那段带回指的原文（`就按我前面说的 Python 环境执行`）。
  它是 source-bound 的、每个字都来自用户，但单看步骤仍然指代不明；真正的绑定在 `applies_to` 边上。
  若以后要让这类节点单独可执行，需要一条「渲染时沿边解析步骤里的回指」的读路径。
* **F-T5**：`finish=length` 在 flash 上 v8 1/8、v9 1/9，是这条提示词的常驻噪声（事件 L 的 F-L3 同源）。
  任何改动这三段策略文本的人都必须重跑 §3 的复算（`scripts/native/a6_replay_t15.py`）。
* **F-T6**：本轮把 HM-S12 第 135 行「创建**两个** canonical nodes」与 HM-TO-A2 第 158 行
  「同一原子 plan 正向创建**两个端点**」这条更紧的义务，归给 **HM-TO-A2 的 clean-wheel oracle**
  （SDK 公共 API 直接造两个端点，不经分析车道），A6-6 不重复证明（§1 理由 2）。
  尚未核对 HM-TO-A2 现有的交付测试是否真的覆盖了「同一原子 plan 正向创建两个端点 + 一条
  `applies_to`」这条正向路径。**必须单独核对**：若没有，这条义务在整个矩阵里就是空的，
  届时要么补 HM-TO-A2 的 oracle，要么把它加回 A6 的某一项。

---

## 评审（2026-09-09，只读复核 → 逐条处置）

| 编号 | 结论 | 处置 |
|---|---|---|
| **4a**（MUST）`a6_verify.py::item_a6_6` 的 `created_here` 接受任一端 | 采纳 | 判据收紧为「`"target" in created_here` **且** target 在 `cognitive_memory_heads` 里 `memory_type='procedure'`」；§1 的判据描述、§1 注释块（`a6_verify.py`）与 FAIL/PASS 理由串同步 |
| **4c**（MUST，随 4a）缺 `relation_kind='applies_to'` 过滤 | 采纳 | 循环开头显式跳过非 `applies_to` 行；`it.numbers` 增 `applies_to_rows` |
| **4b**（MUST）改写后的 `item_a6_6` 无测试（`--selftest` 零关系行） | 采纳 | 新增 `backend/tests/native/test_a6_verify_a6_6.py`（11 项）：形态 A PASS、两端皆旧 FAIL、evolution 行 FAIL、悬空端点 FAIL、relation memory 属别的 plan / 不在 heads FAIL、只新建 source FAIL、新建 target 但不是流程节点 FAIL、无关系行 INCONCLUSIVE、缺 `relation_memory_revision` 列仍 PASS、`--selftest` 仍 18 项。自检夹具的 heads 表补 `memory_type` 列，好让 A6-6 在自检里可达 |
| **5a**（MUST）义务原文引用不逐字、未说明 HM-S12/HM-TO-A2 的更紧措辞归谁 | 采纳 | §1 理由 2 改为三行引文表（HM-TO-A6 第 162 行、HM-S12 第 135 行、HM-TO-A2 第 158 行，逐字），并写明「两个 canonical node」由 HM-TO-A2 的 clean-wheel oracle 履行、本项不重复证明；新增 **F-T6** 追踪那条 oracle 是否真的覆盖 |
| **5b**（NICE）「acceptance.md 第 162 行」是裸引用 | 采纳 | `00-PLAN.md` 抬头与 A6-6 行、`a6_verify.py` 注释、`ARCHITECTURE/MEMORY_SDK_BOUNDARY.md` 条目、`analysis_proposal_v9.py` 模块文档一律改成「SDK 仓 `simple-harness-memory-sdk` 的 `plans/2026-08-29-human-memory-digital-twin/acceptance.md`「测试义务矩阵」HM-TO-A6 行」 |
| **5c**（NICE）§4.3 没列 ARCHITECTURE 文件 | 采纳 | §4.3 补上 `ARCHITECTURE/MEMORY_SDK_BOUNDARY.md`、自检夹具改动与新提交的复算脚本 |
| **5d**（NICE）复算驱动只在 scratchpad | 采纳 | 提交为 `scripts/native/a6_replay_t15.py`，凭据路径参数化（`--credentials` / `A6_REPLAY_CREDENTIALS`），密钥不打印不入库；§3 改写记录了脚本位置与「样本仍留 scratchpad」这一事实 |
| **1a**（NICE）v9 复用 v8 的 schema **对象**，没写「不许就地改」 | 采纳 | `PROPOSAL_TOOL_SCHEMA` 上加 WARNING 注释（要改必须 `copy.deepcopy` + 新协议 id），并指向下面的金值用例 |
| **1b**（NICE）没有 v3..v8 线格式的金值 | 采纳 | `test_analysis_proposal_v9.py` 新增 `WIRE_GOLDENS`：v3/v4/v5/v5.1/v6/v7/v8 各自 `sha256(system 指令 ‖ 工具 description ‖ canonical schema)` 的字面 hex，外加「导入 v9 之后 v8 金值不变」一项 |
| **2a**（NICE）`survived` 注释写「in order」，实为 set | 采纳 | 注释改写：顺序保证来自 `legacy.compile_proposal` 的遍历顺序 + `memory_protocol` 的 `INVALID_DEPENDENCY_ORDER`，此处只需要成员判定 |
| **2b**（NICE）`_compile_non_relation` 定义在使用点之后 | 采纳 | 移到 `compile_operation` 之前（纯位置调整，行为不变） |
| **2c**（NICE）没有「关系排在端点之前」的用例 | 采纳 | `test_a_relation_listed_before_its_created_endpoint_is_refused_not_fatal`：v9 只拒关系并保住 procedure；同一份提案在 v8 上被 SDK 准入以 `created semantic relation endpoint must precede the relation` 整轮抛掉 |

未采纳项：无。

处置后一次性复验（同一 venv，Memory SDK 0.6.34，pin 文件未动；本机 24GB，全程只跑一个 pytest 进程）：

```
backend/.venv/bin/python -m pytest \
  tests/memory/test_analysis_proposal_v8.py tests/memory/test_analysis_proposal_v9.py \
  tests/memory/test_analysis_v8_existing_relation.py \
  tests/memory/test_analysis_v9_named_workflow_relation.py \
  tests/memory/test_analysis_relation_applied.py \
  tests/memory/test_analysis_relation_candidate_audit.py \
  tests/memory/test_analysis_relation_prospective_applied.py \
  tests/native -q -p no:randomly
→ 125 passed
scripts/native/a6_verify.py --selftest → OK，18 个判定项全部可执行
```

§5「回归」那三张与基线 `eb9f4449` 的对照表是处置前测的；本轮处置只动了
`item_a6_6` 的判据、v8 里两个内部函数的**位置与注释**（行为不变，由上面 125 项覆盖）、
新增用例与文档，没有再跑一次全量基线对照（避免重复占用内存）。
