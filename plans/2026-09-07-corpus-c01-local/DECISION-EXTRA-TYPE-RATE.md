# 决策记录：多提类型率 24.2% 超阈（HM-AC-8）

> 义务：`HM-AC-8` —— 主模型 required-memory-type 召回 ≥90%、隐私禁止项 100%、**额外类型率 ≤15%**
> 现象：`CORPUS-CUMULATIVE-2026-09-08.md`（23:40 节）在 223 条可评分用例上机械重算 **54/223 = 24.2%**
> 分支：`worktree-extra-type-rate`（基线 `a6c8b7c7`）
> 语料记录不在本轮改写（由后续批次重跑回写）。

---

## 0. 结论先行

1. **该指标与记忆分析车道（analysis_proposal / analysis_protocol / analysis_executor）无关**。
   它统计的是**主模型在 `context_route(route="memory_standalone")` 里请求的 `memory_types`**，
   即**读侧的类型化召回请求**，不是分析车道抽取记忆时提出的记忆类型。任务派发时的假设
   （「分析车道给每个任务型 user turn 都造一条 episode」「必需 episode 又镜像出一条 semantic」）
   在证据上不成立——那是写侧行为，不进入本阈值的任何分子分母。判据见 §1。
2. 54 例的形态高度规整：**episode 38 次、procedure 20 次、prospective 8 次、semantic 6 次**，
   四种句式对应四条可判定规则（§2）。
3. 唯一能移动该指标的杠杆是**模型在调用前看到的文本**（Host 事后归一化不改变 provider 参数）。
   PERSONA 由另一车道持有，故本轮改**工具 schema 里 `memory_types` 的 description**，
   正文常量落在 `deskpet/memory/recall_selection.py`（§3）。
4. **不动分析协议**，因此**不升 v9**；v8 的指代/解析规则与 v6 的关系规则一字未改（§3.4）。
5. DeepSeek 成对回放 10 例 / 20 次调用：多提用例 **10/10 → 1/10**，多提次数 **14 → 1**，
   required 类型（semantic）召回 **10/10 → 10/10**，无召回损失（§4）。

---

## 1. 判据：这个指标到底数什么

`backend/deskpet/quality/corpus_scoring.py`：

```python
# review_packet()
types = sorted({t for o in (observations or []) for t in (o["proposed_strings"] or [])})
required = oracle["labels"]["required_types"]
extra_proposed_types_lower_bound=len(set(types) - set(required))
```

`types` 来自同文件的 `type_observations(trace)`：

```python
if call["name"] != "context_route" or args.get("route") != "memory_standalone":
    continue
proposed = args.get("memory_types")
```

即：**只读 provider 响应里 `context_route` 且 `route=memory_standalone` 的 `memory_types` 实参**，
按整条 trace 取并集，减去 gold 的 `required_types`。
acceptance.md 第 73 行的措辞也是「**主模型** required-memory-type recall …、额外类型率 ≤15%」。

由此产生三条硬性推论：

- **分析车道（写侧）无论提多少类型，都不进入该指标**。
- **Host 侧归一化/裁剪无效**：指标读的是模型输出的实参，Host 把 `procedure` 从生效集合里删掉
  也不会让它从 `response_json` 里消失。
- **回执提示（如 `procedure_hint`）对本指标无效**：并集口径下，
  「先提 procedure → 收到提示 → 改调 procedure_discover」在本轮内已经把 `procedure` 记进分子了。
  订正一处易错推断：C06 请求 `procedure` 的比例在**提示上线前后完全相同**
  （run-01g 17/19、run-01j 17/19），所以这个习惯**不是** `procedure_hint` 的副产物；
  但反过来，**提示的触发条件依赖这次请求**，构成本轮改动的主要风险，见 §3.4。

复算复现（232 份 packet，每例取最后一次可评分批次）与记录一致：多提 54 例，
分类分布 C01 11/20、C02 15/19、C03 3/19、C04 3/20、C05 2/16、C06 17/19、C09 2/20、C10 1/19，
C07/C08/C11/C12 全 0。

---

## 2. 54 例的形态：按额外类型 × 句式

全语料 240 例的 gold `required_types` 只有四种取值：`[]`（135）、`['semantic']`（58）、
`['episode','prospective']`（20，全部 C04）、`['episode','semantic']`（19，C03）。
**`procedure` 从未出现在任何一条 gold 的 `required_types` 里**——这是下面 R4 零代价的依据。

| # | 额外类型 | 句式模式 | 典型原文 | 例数 | 涉及类别 |
|---|---|---|---|---|---|
| P1 | `episode` | 「我**之前/以前/一贯/平时/习惯/定下**的 X 是什么」——指向**长期成立的偏好或约定**，模型把「之前」读成「过去发生的事」 | C01-09「我之前要求日常距离显示用什么单位？」／C02-05「按我平时的习惯……」 | 26 例（C01 11 + C02 15）；另 C06 有 8 例与 P2 同句共现 | C01 / C02 / C06 |
| P2 | `procedure` | 「沿用我的 X 要求**和检查步骤/整理顺序/核对流程/排程做法**」——句中出现流程名词 | C06-03「按我自己的金额格式**和检查习惯**，先列规则」／C06-12「沿用我的出处要求**与整理程序**」 | 20 次 / 18 例 | C06 17、C02-07、C03-01、C10-14 |
| P3 | `prospective` | 句中出现日常时间词（今晚/明天下午/上下午/这周），但问的是**偏好**不是提醒 | C02-12「今晚练鼓还是明天下午练更合适？」／C02-09「把独立写作和团队对齐分配到上下午」 | 8 次 | C01-08、C02-09/12、C03-02/14、C06-09、C05-05、C09-16 |
| P4 | `semantic` | 纯「回顾+提醒」问句，`semantic` 被当作兜底加上 | C04-09「这周问题是什么，下周对应提醒是什么？」 | 3 例 | C04-09/15/20 |
| P5 | 任意 | **gold `required_types=[]`**（本应零召回或走任务路由），模型却发起了类型化召回 | C05-05「恢复“读书节”，先给我不同年份的候选」／C10-14 | 5 例 | C05-05/20、C09-16/17、C10-14 |

P5 与本阈值同分子但**根因不同**：它是路由判断错误（no_recall / task_scope 分流），
已分别记在 C05「任务范围恢复轮次的工具面选择」与 C09/C10 的模型行为条目下，
本轮不由类型选择策略解决。P1–P4 覆盖 49/54。

补充观察：54 例中 50 例只发生 **1 次** `memory_standalone` 调用，
即多提是**首次调用时一次性选宽**，不是多轮累积。这再次说明只有「调用前的文本」有效。

---

## 3. 策略与实现

### 3.1 采用的四条规则（gold-free，可由问句本身判定）

- **R1（semantic 单选）**：问「用户的习惯/偏好/格式/单位/约定**是什么**」时只选 `semantic`，
  即使用户说成「我之前说过」「我一贯」「我定下的」——**长期值存在 semantic 里，
  当初说这话的场合对答案没有贡献**。→ 消 P1。
- **R2（episode 收窄）**：只有当答案需要**那次事件本身**（发生了什么、何时、与谁、结果如何）才加
  `episode`；「提到过去」本身不构成这样的问题。→ 保住 C03/C04 的必需 episode，消 P1 的误加。
- **R3（prospective 收窄）**：只有请求涉及**未来意图、提醒、截止或调度触发**才加；
  偏好问句里的日常时间词不算。→ 保住 C04 的必需 prospective，消 P3。
- **R4（procedure 不走类型化召回）**：类型化召回只返回**已绑定且适用于当前任务**的 Procedure；
  用户存了但还没用过的流程**永远不会**出现在 fragments 里（`DECISION-PROSPECTIVE-PROCEDURE-RECALL.md` 3.2
  已确立的 SDK type-authority gate 语义）。找已存流程用 `procedure_discover`；
  **不要因为请求里出现「步骤/顺序/清单」就把 `procedure` 加进来**。→ 消 P2。
  代价为零：全语料没有任何 gold 要求 `procedure`。
  这条与 PERSONA 现有那句「A stored Procedure stays outside typed recall until it has actually
  been used once」同源，只是把它移到**模型选参数的那一刻**，而不是收到空 fragments 之后。
- 总纲：**只请求"答案真的可能在里面"的最小类型集合；当兜底加的类型什么也返回不了，
  还要占用共享的召回预算**。→ 消 P4。

### 3.2 落点（为什么不是 PERSONA、不是分析协议）

| 候选落点 | 是否有效 | 采用 |
|---|---|---|
| `analysis_proposal*.py` / `analysis_protocol.py`（分析车道提示词） | **无效**——不进入指标（§1） | 否 |
| Host 侧归一化 / 拒绝 / 重写选择 | **无效**——指标读模型实参；且拒绝会让模型重提，并集只增不减 | 否 |
| 回执提示（`procedure_hint` 式） | **无效**——事后到达，本轮已计入并集 | 否 |
| `primary_context.py` PERSONA | 有效，但**本轮由 `worktree-termination-persona` 持有** | 否（避让） |
| `context_route` 工具 schema 的 `memory_types.description` | **有效**——与 PERSONA 同批送达 provider，且紧贴参数本身 | **是** |

正文常量 `MEMORY_TYPE_SELECTION_POLICY` 放在
`backend/deskpet/memory/recall_selection.py`（解析器旁边，单一事实源），
由 `backend/deskpet/sdk_adapters/context_route.py` 拼进 schema description。

**Token 成本（实测，非估算）**：用 `context_partitions.tool_schema_tokens` 量 `context_route`
整个 schema 的 wire token：**494 → 632（+138）**。该开销进入每次请求的 protected 分区。

初稿曾写到 762 token（+268），**直接把 `tests/execution/test_current_tool_megabyte.py` 的
`[8192]` 参数从绿变红**（`ContextBudgetExceeded: planned=5444 effective=5325 protected=3376
tool_schemas=1509`）。据此把正文压掉一半，逐条保留四条规则的判别词后复测：

| 参数 | 基线（a6c8b7c7） | 初稿 762 token | 定稿 632 token |
|---|---|---|---|
| `[8192]` | PASS | **FAIL（新增）** | **PASS（与基线一致）** |
| `[4096]` | FAIL（既有红，`wire_count 2 != 3`） | FAIL | FAIL（既有红，`wire_count 1 != 3`）|

`[4096]` 本就是既有红（该窗口下 protected 早已超限），但**多出的 138 token 让它更早触发预算停机
（第 2 次 wire 请求 → 第 1 次）**——这是本改动真实的、已知的代价，记录在案。
新增 `test_policy_keeps_the_route_schema_inside_its_measured_token_cost` 把 643 token 钉成上限，
今后要再加字必须是一次有度量的、有意识的决定。

### 3.3 确定性咨询码（只观测，不设门）

`selection_policy_departures(memory_types)` 返回：

- `procedure_not_served_by_typed_recall`——选择里含 `procedure`；
- `all_types_requested`——四类全选（构造上就是兜底）。

只有**类型内在**的规则在 Host 侧可判定（R1/R2/R3 需要问句语义，Host 不做语义判断），
所以只给这两码。它写进 `context_route_tool_invocations.detail_json.recall_selection`，
**不拒绝、不过滤、不改写任何召回**，也不携带任何 per-case 期望答案。
目的是让下一轮批次不必再翻 provider 响应就能看到多提的 Host 侧痕迹。

### 3.4 已知风险：R4 会关掉 `procedure_hint` 的触发路径（必须在重跑中核验）

`backend/deskpet/sdk_adapters/context_route.py` 的提示触发条件是：

```python
**({"procedure_hint": dict(_PROCEDURE_HINT)}
   if "procedure" in memory_types and not any(
       fragment["memory_type"] == "procedure" for fragment in fragments)
   else {}),
```

即**只有模型请求了 `procedure`，提示才会出现**。而这条提示正是 run-01i/j 把 C06 从
4/19 抬到 13/20 的原因（模型收到提示后改调 `procedure_discover`）。
R4 让模型不再请求 `procedure`——多提消失的同时，**这条提示也不会再出现**。

采用 R4 而非放弃，理由有三：

1. 不采用 R4 时 C06 的 17 例多提保留，54→37，**37/223 = 16.6% 仍然超阈**，问题解决不了；
2. R4 的文本把提示的**内容前移到选参数的那一刻**（「Call procedure_discover instead to find a
   saved workflow」），PERSONA 另有三处同义句，模型拿到 `procedure_discover` 的路径没有被删除，
   只是从「事后纠正」变成「事前指引」；
3. 该风险**可观测且有确定的回退**（下条）。

**重跑时必须同时核验**：C06 的 `procedure_discover` 实际调用率与
`required_procedure_access.status=SATISFIED` 的例数。若较 run-01j（18/18 调用、13/20 PASS）下降，
**正确的修法是把 `procedure_hint` 与 `memory_types` 解耦**（Host 小改：改由「库中确实存在未绑定
Procedure 而本次 fragments 为空」触发），**而不是回退 R4**——因为 R4 是唯一能把本阈值压到 15% 以下的规则。

### 3.5 明确未改动

- **不升分析协议 v9**：`analysis_protocol.py` / `analysis_proposal_v8.py` 一字未改，
  v8 的指代字面值硬拒绝、`object_value` 解析到候选、关系端点可为既有记忆全部保持；
  v6 的关系规则同样未触。旧版本仍可解析。
- 不改 `REQUESTABLE_MEMORY_TYPES`（`procedure` 仍是合法请求项——已绑定 Procedure 的召回是
  HM-AC-4 的正当路径），不改 `HOST_DEFAULT_MEMORY_TYPES`（隐式选择的 Host 回退是另一份契约）。
- 不改 PERSONA、`main.py`、输入守卫、审计面、`testcase/`。

---

## 4. 回放：DeepSeek 成对 A/B（10 例 / 20 次调用）

方法：从每例已存 trace 取**当时真实的 provider 请求**（system + user + 完整 tools），
A 臂原样重放（旧 description），B 臂**只把 `context_route` 的 parameters 换成新 schema**，
其余（messages / temperature / 其它 11 个工具）逐字节相同。同一模型 `deepseek-v4-pro`，
故差异只来自策略文本。样本覆盖三个最差类别与全部四种额外类型。

| 用例 | required | 原批次（luna） | A 臂（旧 schema） | B 臂（新 schema） | 额外数 A→B |
|---|---|---|---|---|---|
| C01-01 | semantic | episode+semantic | episode, semantic | **semantic** | 1 → 0 |
| C01-08 | semantic | episode+semantic | episode, prospective, semantic | episode, semantic | 2 → **1** |
| C01-09 | semantic | episode+semantic | episode, semantic | **semantic** | 1 → 0 |
| C02-01 | semantic | episode+semantic | episode, semantic | **semantic** | 1 → 0 |
| C02-07 | semantic | episode+procedure+semantic | episode, semantic | **semantic** | 1 → 0 |
| C02-09 | semantic | episode+prospective+semantic | episode, semantic | **semantic** | 1 → 0 |
| C06-02 | semantic | episode+procedure+semantic | procedure, prospective, semantic | **semantic** | 2 → 0 |
| C06-03 | semantic | procedure+semantic | procedure, semantic | **semantic** | 1 → 0 |
| C06-07 | semantic | episode+procedure+semantic | procedure, prospective, semantic | **semantic** | 2 → 0 |
| C06-12 | semantic | procedure+semantic | episode, procedure, semantic | **semantic** | 2 → 0 |

| 指标（本样本） | A 臂 | B 臂 |
|---|---|---|
| 多提用例数 | **10 / 10 = 100%** | **1 / 10 = 10%** |
| 多提类型出现次数 | **14** | **1** |
| required 类型（semantic）召回 | 10 / 10 = 100% | **10 / 10 = 100%** |
| `procedure` 被请求 | 4 例 | **0 例** |

残留一例 C06-12 的 P2、C01-08 的 P3 均已消除；唯一残留是 C01-08 的 `episode`
（「把任务甲完成、任务乙待确认整理给我」确实提到两件事的状态，属 R2 的边界，可接受）。

**限制 1（重要）**：回放用的是**压缩前的 342-token 初稿**，而**定稿是压缩到 138-token 增量的版本**。
四条规则一条不少、每个判别词都由测试钉死（§5），但**定稿措辞本身没有再回放过**
（DeepSeek 调用预算 20 次已用尽）。重跑时须以定稿文本为准复核。

**限制 2**：DeepSeek 单次采样、无 seed，非确定性；A 臂本身也与原 luna 批次不完全一致
（C02-07/09 的 A 臂没复现原来的 procedure/prospective），这正说明**跨模型的绝对数不可外推**，
只有同模型同请求的 A/B 差值可信。真正的阈值重测必须走批次重跑（§6）。

---

## 5. 测试

新增 `backend/tests/memory/test_recall_selection_policy.py`（11 项）：

- 策略正文逐字进入 provider 工具 schema，且原有两条不变量（空列表需 `include_short_horizon`、
  选择不授予披露/执行权限）仍在；
- 四个可请求类型各自都有规则句（参数化 4 项）；
- 四条判别句（P1/P2/P3/P4 的判据）逐条钉死；
- 策略正文**不得**出现 `c01`/`c02`/`gold`/`required_types`/`corpus` 等任何 per-case 口径；
- 咨询码：最小选择无码、`episode`/`prospective` 不判departure（需语义，Host 不判）、
  `procedure` 命中、全选命中两码、空选择无码、**咨询码不改变解析器接受的输入**、
  `HOST_DEFAULT_MEMORY_TYPES` 未被策略收窄。

同步更新两处原本逐字断言 `detail_json` 的既有测试
（`tests/sdk_adapters/test_model_recall_selection.py`、`tests/sdk_adapters/test_recall_selection_failure_audit.py`）
以容纳新增的 `selection_policy_departures` 字段。

---

## 6. 需要重跑哪些类别才能重测阈值

多提集中在**需要召回的类别**，零召回类别本来就是 0：

- **必须重跑**：**C01（20）、C02（19）、C03（19）、C04（20）、C06（20）** ——
  其中 **C06 除阈值外还要单独核 `procedure_discover` 调用率与 `required_procedure_access` 状态**（§3.4）。
  这五类是 98/223 的分母，
  且 49/54 的分子都在这里。这五类同时是 required-type 召回率的**全部分母**（136 条），
  所以重跑同时重测「≥90% 召回」与「≤15% 多提」两个阈值。
- **建议重跑**：C05（16）、C09（20）、C10（19）—— 各含 P5 例，但其根因是路由不是类型选择，
  重跑用于确认策略文本没有把它们变差。
- **可不重跑**：C07 / C08 / C11 / C12（79 例）—— 现记录 0 多提，且新策略只可能收窄选择。

若只做最小重测，跑 C01+C02+C06（59 例）即可覆盖 43/54 的分子。

---

## 7. 遗留（followup）

- **F-ETR-1**：P5 的 5 例（C05-05/20、C09-16/17、C10-14）在 `required_types=[]` 上仍发起类型化召回。
  属路由分流，建议与既有「`task_scope_hint`」条目合并处理。
- **F-ETR-2**：C06 gold 的口径张力——用例本身就是「跨来源 + 流程」，却把 `procedure` 记为额外类型。
  本轮按 acceptance 的字面定义执行（`procedure` 由 `required_procedure_access` 单独计量，
  不进 `required_types`），未改 gold。若后续认为该口径失真，应走 gold 变更而不是放宽阈值。
- **F-ETR-4**：`[4096]` 窗口下 protected 分区本就超限（基线即红）。本改动使其更紧。
  若要恢复该窗口，应压缩 `context_route` schema 里与本轮无关的长描述
  （`reuse_workspace_of` 400+ 字符、`expected_source_hash` 230+ 字符），属任务路由车道，本轮未动。
- **F-ETR-3**：写侧（分析车道）是否也存在「任务型 turn 一律生成通用 episode」「必需 episode
  又镜像 semantic」的多提，本轮**未验证也未处理**——它不影响 HM-AC-8，需要另立度量才能评估。
