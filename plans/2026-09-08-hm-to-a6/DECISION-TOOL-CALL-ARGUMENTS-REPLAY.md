# 决策：跨轮重放的 assistant.tool_calls 必须带回真实入参（HM-TO-A6 事件 K）

日期：2026-09-08
工作树：`.claude/worktrees/toolcall-args`（分支 `worktree-toolcall-args`，基线 `8e9b7c8d`）
证据：`.local-test-evidence/2026-09-08/native-a6-run4/`、`native-7cec5249/`（r14）、`native-r15-188394b1/`（r15）

---

## 1. 现象与根因

`backend/deskpet/sdk_adapters/provider.py::_ProductOpenAICompatibleProvider._wire_messages`
在 `metadata[provider_tool_calls]` 缺失时，从其后紧邻的 `tool` 消息重建
assistant 的 `tool_calls`，`arguments` 一律写字面量 `"{}"`。原注释已把这条记为
「已知降级 + 上游义务：re-attach real arguments」。

SDK 0.7.1 的冻结契约
（`simple_harness/execution/provider_invocations.py:484`
"stored public provider message metadata must be empty"）强制清空 provider
assistant 消息的 metadata，所以这条降级不是偶发，而是**必然且全量**的。

## 2. 证据侧相关性（任务 1）

三份 durable `execution-v6.sqlite3` 的 `provider_invocations`
（请求 = `request_json`，响应 = `response_json`）逐条复现 `_wire_messages`
的重建逻辑后统计：

**结构性事实：129 条 invocation 中，`metadata[provider_tool_calls]` 存活数 = 0。**
即线上出现过的每一条 assistant `tool_calls` 都是 `"{}"` 重建，且重建条数随轮深
单调累积。

### 2.1 逐库分桶

| 库 | invocation | 重建=0 的请求 | 其响应空参率 | 重建>0 的请求 | 其响应空参率 | Pearson r（条数 / 比率） |
|---|---|---|---|---|---|---|
| native-a6-run4（attempt 4） | 64 | 6 请求 / 7 调用 | **0/7 = 0.0%** | 52 请求 / 64 调用 | **25/64 = 39.1%** | 0.422 / 0.521 |
| native-7cec5249（r14） | 31 | 3 请求 / 4 调用 | **0/4 = 0.0%** | 24 请求 / 32 调用 | **9/32 = 28.1%** | 0.547 / 0.608 |
| native-r15-188394b1（r15） | 34 | 4 请求 / 5 调用 | **0/5 = 0.0%** | 25 请求 / 30 调用 | **11/30 = 36.7%** | 0.585 / 0.585 |

### 2.2 合并剂量反应（三库汇总，只算产出了 ≥1 次工具调用的请求）

| 该请求内 `{}` 重建条数 | 请求数 | 响应工具调用 | 其中空参 | 空参率 |
|---|---|---|---|---|
| 0 | 13 | 16 | 0 | **0.000** |
| 1–4 | 20 | 25 | 3 | 0.120 |
| 5–9 | 17 | 28 | 4 | 0.143 |
| 10–14 | 13 | 18 | 6 | 0.333 |
| 15–19 | 18 | 20 | 10 | 0.500 |
| ≥20 | 33 | 35 | 22 | **0.629** |

合并 Pearson r = **0.484**（条数）/ **0.546**（比率），分桶严格单调。五个曾出现空参
调用的 Run，**在重建条数达到 4–17 之前一次空参都没有**（a6run4 分别是 4/7/4，
r14 是 17，r15 是 11）；13 个零重建请求一次空参都没产生。

**结论：模型在照抄自己被污染的 transcript。** 这不是无害的形状降级，而是**自我强化**
——适配器每往线上多写一个 `{}`，模型下一次自己写出 `{}` 的概率就更高一档。

### 2.3 与 `execution_effects` 对账

| 库 | effects | 空 `arguments_json` | `missing_required_argument` | 响应侧空参调用 | 对上 |
|---|---|---|---|---|---|
| a6run4 | 71 | 25 | 25 | 25 | 25/25 |
| r14 | 33 | 9 | 8 | 9 | 9/9 |
| r15 | 31 | 7 | 6 | 11 | 7/11（+4 未入账本） |

- 带 `missing_required_argument` 的 effect **无一例外**入参为空，映射精确。
- r14/r15 各有 1 条「空参但未被拒」是 `workspace_prepare`——它没有必填入参，`{}` 合法。
- r15 的 4 条差额是 turn 13/14/16/17 的 `write_file`：能力未激活，在进账本前就被拒，
  连 `execution_effects` 行都没有。**真实空参率只会被账本低估，不会高估。**

### 2.4 关键确认：耐久记录一直握着被丢掉的数据

| 库 | 线上被重建为 `{}` 的调用 | 账本里能找到 | 账本里是**真实非空**入参 | 该调用本身就是空参调用 | 无 effect 行 |
|---|---|---|---|---|---|
| a6run4 | 69 | 69 | **46（66.7%）** | 23 | 0 |
| r14 | 34 | 32 | **23（71.9%）** | 9 | 2 |
| r15 | 35 | 31 | **24（77.4%）** | 7 | 4 |

**132 条被重建为 `{}` 的调用里，93 条的完整、未脱敏入参一直躺在
`execution_effects.arguments_json` 里。** 例：
`tool_search {"query":"read_file open file text contents"}`、
`tool_describe {"capability_id":"builtin:read_file"}`。

## 3. 数据源选型（任务 2 的裁决）

用户授权由本代理裁决技术取舍，不回问。三个候选逐条评估：

### (a) Host 可达的耐久 effect 审计 — **否决**

存在合法读路：`ProductSdkRuntimeStack.read_primary_tool_causal_sources`
（`backend/deskpet/sdk_adapters/composition.py:864`）→
`deskpet/memory/primary_tool_causality.py:24 read_tool_causal_sources` →
`uow.read_effect(EffectId)`，这是 SDK 的公共 port，不违反
`ARCHITECTURE/MEMORY_SDK_BOUNDARY.md` / `AGENT_HARNESS.md` 的边界口径
（适配器不直接开 SDK 表）。但它作为本修复的数据源不成立：

1. **没有 call_id → effect_id 的公共索引。** `uow.read_effect` 只接受 `effect_id`；
   Host 的 `primary_effect_identities` 只有 `effect_id`/`tool_name`，没有 `call_id`。
2. **热路径不可同步读库。** `_wire_messages` 在 `_request_payload` 里同步执行，位于
   provider 请求组装路径上；在这里开 SQLite 事务既要新持有 UoW 句柄，又要在真实
   Run 进行中读同一 DB（本机已知：`tests/sdk_adapters/test_composition.py` 会在机器
   全局数据目录上死锁）。
3. **覆盖有洞，且洞正好在故障面上。** §2.3 已实测：能力未激活等「派发前拒绝」压根
   不写 `execution_effects`（`AGENT_HARNESS.md` 记为 `effectless_denial`），r15 就有 4 条。
4. 账本里的 `call_id` 是 harness 规范化的 `call-<64hex>`，线上用的是 `raw_call_id`，
   还要多一层映射。

### (b) tool 结果消息自身的 metadata — **不可用**

tool 结果 `Message` 由**冻结的 SDK** 构造
（`simple_harness/runtime/drivers/react_loop.py:832`，以及 workflow_spawn 的
`execution/sqlite/uow.py:13249`），Host 无从在其中写入入参，改不动。

### (c) Host 侧按 `call_id` 的进程内备忘 — **采纳**

新增 `backend/deskpet/sdk_adapters/tool_call_arguments.py::ToolCallArgumentsMemo`，
形制对齐既有的 `run_route_state.py::RunRouteStateMemo`。

**为什么它足够：** §2 已证 `provider_tool_calls` 存活数为 0，即从一个 Run 的第 2 轮起
每一跳都在丢入参；而这些跳全部发生在同一进程内（一次 Run 的 ReAct 循环）。跨 Run 的
历史**不会**以裸 assistant/tool 消息进入新 Context——它是被引号包进一条 user 消息的
`historical_causal_group`（见 §5），所以进程内备忘覆盖了事件 K 的全部发生面。

**备忘的存活范围与安全性的关系（澄清）：** 备忘按上一段的口径**不需要**跨 Run 存活；
它事实上跨 Run 存活，只是因为它是一个进程级有界 LRU（这是实现上的简化，不是设计意图，
更不是「因为跨 Run 需要」）。跨 Run 的安全性完全由 §3.1 的三重失败关闭保证，
而不是由「备忘活多久」保证。

**与「Run 终态释放」的偏离及理由：** 任务建议按 Run 分组并在终态释放。实测发现
provider 请求身份在当前 SDK 下是 `provider-turn:N`（**不含 run id**，见
`_capture_public_tool_narration` 里为此做的单 Run 兜底），在 provider 模块内解析 run id
本身是脆弱的、要依赖 `_delivery_adapters` 只有一个活跃 Run。因此改为进程级有界 LRU：
4096 条 **且** 总计 8 MiB，单条上限 256 KiB；内存上界由容量本身保证，不依赖终态钩子。
生产不注入备忘，所以全进程共用模块级单例；保留 `release_call` / `clear` 供显式清理，
测试侧由 `tests/sdk_adapters/conftest.py` 的 autouse fixture 每例清空（本仓库跑
`pytest-randomly`，共享可变单例会造成顺序相关的偶发红）。

### 3.1 追加裁决（独立评审 F1）：原始 `call_id` **不是**全局唯一，必须失败关闭

初版把「call id 由 provider 生成、全局唯一」当作前提，键只有 `call_id`、只用
`tool_name` 兜底。独立评审推翻了这个前提，且**已在本工作树复现**：

```
turn1 记 call_0 -> {"path":"A.md"}；turn2 记 call_0 -> {"path":"B.md"}
wire[1]（turn-1 assistant）: {"path":"B.md"}  | 其后紧跟的 tool 结果: contents of A
```

证据链：
- SDK 自己就不信任原始 id——`runtime/drivers/react_loop.py::_internal_effect_identity`
  把 `{run_id, turn_ordinal, raw_provider_call_id, call_ordinal}` **一起哈希**才得到内部
  `CallId`，即原始 id 只在一个 `(run, turn, ordinal)` 内唯一；
- `execution_effects` 也因此单列 `raw_call_id`（§2.3 的对账正是靠它做的 join）；
- DeepSeek 发的是长随机 id（`call_00_IKphh2J3…`），但产品 registry 能接到的
  vLLM / llama.cpp / LM Studio / 本地网关普遍发 `call_0`/`call_1` 这种**每轮从 0 重排**
  的序号 id。

**为什么这比不修更坏：** `{}` 只是信息缺失，模型看得出「参数没了」；而错贴的入参是一条
**自洽但虚假**的「调用 → 结果」配对（"我调了 `read_file {"path":"B.md"}`" 紧跟 A.md 的正文），
恰恰是本文件认定的那条模仿通道，而且它被计为 `restored`、一声不吭。

**裁决：三重失败关闭，宁可退回 `{}`。**

1. 读取时 `tool_name` 必须一致；
2. **毒化**：同一 `call_id` 以不同入参（或不同工具）再次留存 → 该键永久不可读
   （直到淘汰/释放），`record` 返回 `False`；**同参数重复留存幂等**，所以协议重采样
   对同一轮再记一次不会误伤；
3. **同请求内重复即歧义**：`_wire_messages` 先统计整份消息里各 `call_id` 的出现次数，
   出现 >1 次的一律对其**全部出现位**退化，并单列 `ambiguous_call_ids` 计数。

第 2 条防「某一跳只看得到一处出现、但备忘里已被后轮覆盖」；第 3 条防「毒化条目被 LRU
淘汰后又被重新写入」。序号 id 端点由此整体退回修复前的 `{}` 行为（安全），随机 id 端点
（DeepSeek、relay，即本次证据里的真实链路）拿到完整修复。有 5 个专测锁定，其中
`test_unique_ids_still_get_their_arguments_across_turns` 是防守卫误伤的反向控。

## 4. 保存与隐私（任务 2 后半）

- **精确重序列化**：备忘存的是 `canonical_tool_arguments_json`（`sort_keys=True`、
  `separators=(",", ":")`、默认 `ensure_ascii`）。该函数现在是**唯一**一处序列化，
  `_message_payload` 的同进程 metadata 路径也改用它——两条路径逐字节相同，有专测锁定。
- **存的是哪一份**：`_retain_tool_calls_in_message` 在 `_extract_public_progress`
  **之后**执行，所以备忘里的入参已剥离 Host 内部叙述字段 `deskpet_public_progress`
  （有专测）。这与耐久 effect 账本记录的**是同一个对象**：SDK
  `tools/executor.py:336` 对同一个 `ToolCall.arguments` 做 `thaw_json`，再
  `canonical_json` 写进 `execution_effects.arguments_json`。两处只有 JSON 转义口径不同
  （账本用 SDK `canonical_json`，非 ASCII 不转义；线上沿用既有的 `ensure_ascii`），
  对象内容完全相同。
- **隐私结论：没有任何「从耐久记录里被脱敏掉的东西」被重新暴露。** 这份内容是模型自己
  写的、刚刚由同一个 provider 端点发过来、并且在 `execution_effects.arguments_json`
  里本来就是原文存储（证据侧实测：三库 0 处脱敏标记）。备忘只是把它原样发回同一端点。
- **降级日志无载荷**：未命中只打 `rebuilt/restored/fallback_empty/ambiguous_call_ids`
  四个计数与 `request_ref`（不透明摘要），不含 call id、工具名或任何入参值；有专测断言
  这些字符串不出现在日志里。
- **请求指纹不受影响**：SDK 的 `provider_request_fingerprint` 基于 `ProviderRequest.messages`
  计算，本修复只改物理线体 payload，不改 SDK 请求身份；且无重建的请求线体逐字节不变（有专测）。

## 5. 历史投影（任务 3）：确认丢失，**裁决为不修**

`historical_causal_group` 里的 assistant 条目**确实不带入参**：
`deskpet/sdk_adapters/composition.py:1170 project_primary_transcript` 只产出
`{"role", "content"}`（仅 tool 角色额外带 `call_id`/`name`），
`primary_context_pages.py::project_history_group` 原样引用。

**这是同类信息丢失，但不在本次可修范围，理由：**

1. 该组不是线上的 assistant/tool 消息，而是被引号包起来塞进**一条 user 消息**的
   JSON 记录，走不到 `_wire_messages`；它不参与事件 K 的因果链。
2. 要加入参，必须同时改：`project_primary_transcript`（不在任务给定的
   `primary_context_pages.py` / `primary_history.py` 文件范围内）、
   `deskpet/memory/primary_message_v2.py::representable`
   （第 51 行硬性要求 assistant 条目的键集恰为 `{"role","content"}`）与 `item_ordinals`，
   以及**已归档终态 evidence 的 envelope hash**——历史组会整体变成不可验证。
3. 任务明确禁止改动 `primary_history.py::transcript_matches` 语义，而组消息形状一变，
   `transcript_matches` 与 `_source_group` 的逐条比对必然要跟着改。

**记为 followup（F-K1）**：历史因果组补入参，需与终态 evidence 契约版本升级
（`primary_message_v3` 一类）一起做，不可单独打补丁。
→ 2026-09-08 同日裁决为**不改契约、不动 envelope**，改用 Host state 的内容寻址旁路记录
（v55）在投影时 join，见 [DECISION-HISTORY-TOOL-CALL-ARGS.md](DECISION-HISTORY-TOOL-CALL-ARGS.md)。

**同批观察到的相邻缺陷（不在本次范围，记 F-K2）**：
`metadata[provider_reasoning_content]`（DeepSeek 思考模式要求逐字回显）走的是同一条
「durable 往返即清空」的路，跨轮同样丢失。修法与本次同构（同一备忘再存一列），但
reasoning 是 provider 私有内容，回灌口径需单独裁决，不并入本次。

## 6. 实现

| 文件 | 变更 |
|---|---|
| `backend/deskpet/sdk_adapters/tool_call_arguments.py` | 新增。`ToolCallArgumentsMemo`（有界 LRU + 字节预算 + 线程安全）、`canonical_tool_arguments_json`、`default_tool_call_arguments_memo` |
| `backend/deskpet/sdk_adapters/provider.py` | `_retain_tool_calls_in_message` 解析响应时写备忘；`_wire_messages` 重建时读回真实入参、未命中退化 `{}` 并记无载荷计数；`_message_payload` 改用共享序列化；`_ProductOpenAICompatibleProvider` / `ProductProviderAdapter` 新增可注入 `tool_call_arguments_memo` |
| `backend/tests/sdk_adapters/test_provider_tool_call_arguments_replay.py` | 新增 17 个用例 |

上游义务（S5b）不变且已在 docstring 中保留：正解是 SDK 把 assistant 的 `tool_calls`
当成一等公共 transcript 字段。备忘是进程内修复，**冷启动跨进程续聊仍会退化**（有计数日志），
SDK 落地后本备忘与降级分支一并删除。

## 7. 测试

新增 25 例全绿，覆盖：durable 真实往返（`provider_response_json` →
`provider_response_from_json`，契约确实清空 metadata）后 memo 补回入参；真实 HTTP
（`httpx.MockTransport` + 生产 `ProductProviderAdapter`）跨 continuation 跳的线体断言，
并在该 fixture 里带上 `deskpet_public_progress` 金丝雀，从而**锁定 `invoke` 里
`_extract_public_progress` 必须在 `_retain_tool_calls_in_message` 之前**这一顺序；
并行多调用各归各位；同 call_id 异工具名不借用；未命中退化 + 无载荷计数日志 + 全命中不告警；
memo 复原与同进程 metadata 路径逐字节相同；**共享序列化的黄金串**（含非 ASCII、嵌套、
null/bool/float，按修复前的 `json.dumps(sort_keys, separators, ensure_ascii=True)` 写死）；
无重建请求逐字节稳定；以及 §3.1 的五个失败关闭控（序号 id 复用不张冠李戴、冲突毒化跨请求
生效、同参数重复留存幂等、异工具名毒化、**唯一 id 仍正常复原的反向控**）；
memo 本体的边界、容量淘汰、LRU 刷新、字节预算（含 tool_name）、超大条跳过、
非法/异常输入不外抛、拒绝留存不留旧答案、poison/release/clear。

回归（全部与 `git stash` 基线逐条比对）：

| 套件 | 修复后 | 基线 | 结论 |
|---|---|---|---|
| `tests/sdk_adapters`（除 `test_composition.py`） | 547 passed / 58 failed | 522 passed / 58 failed | 失败集合 `diff` 完全相同；+25 全为新增用例 |
| `tests/execution`（`test_scope_disclosure_runtime.py` 定向、`-p no:randomly`） | 13 passed / 3 failed | 13 passed / 3 failed | 同参数、同条目 |
| `tests/execution` 全量 | 43 failed / 211 passed / 10 errors | 40 failed / 214 passed / 10 errors | 差额 3 条全在 `test_scope_disclosure_runtime` 的**随机顺序**干扰下；定向复跑两边一致 |
| provider importers（`tests/memory` 8 文件） | 31 passed / 10 failed | 31 passed / 10 failed | 逐条相同 |
| provider 定向 7 文件 | 100 passed / 0 failed | — | 全绿 |

已知既有红（与任务给定清单一致）：short-index embedder、s5b_acceptance_matrix /
effect_gate / s5a_milestone_route_loop、scope_disclosure、typed_context_use_primary、
`/Users/denny` 路径、timing `CancelledError` 抖动
（`test_primary_none_routes_to_exact_task_and_writes_real_file[True]` 定向复跑即绿）、
`test_late_history_denial…[sent_unknown]`、
`test_real_memory_only_forget_cold_user_and_terminal_no_host_rewrite`、
`test_final_candidate_rejects_every_superseded_wheel_hash`。

**未做真人 UI 复跑**：桌面 app 有活跃运行（端口 18120），按任务约束不动它；本轮以
durable 证据相关性 + 真实 HTTP 传输集成测试作为验收面。原生复跑记为交付后的验证项。

## 8. 独立评审与回应（2026-09-08）

按用户「技术取舍交子代理裁决」的口径，本修复提交后交由独立评审代理做对抗性审查。
verdict：**CHANGES REQUIRED**，一条真缺陷 + 若干文档/测试问题。逐条处置：

| 编号 | 级别 | 结论 | 处置 |
|---|---|---|---|
| F1 原始 call_id 复用导致张冠李戴 | major | **成立，已在本工作树复现** | 见 §3.1：三重失败关闭 + 5 个专测 |
| F2 「call id 全局唯一」是未经验证的前提 | minor | 成立 | §3.1 已推翻并写明证据链 |
| F3 模块 docstring 与本备忘对「跨 Run 存活」口径自相矛盾；`见 §4` 交叉引用错 | minor | 成立 | §3(c) 增「存活范围与安全性的关系」澄清；引用改为 §5 |
| F4 「与 effect 账本逐字节相同」不成立（`ensure_ascii` 口径不同） | minor | 成立 | 评审读到的是修订前版本；代码注释与 §4 已改为「同一个对象，仅转义不同」 |
| F5 「One memo per adapter」注释与生产拓扑不符 | minor | 成立 | 注释改写为「生产是模块级单例；安全性由守卫而非拓扑保证」 |
| F6 默认全局单例造成测试间共享可变状态 | minor | 成立 | 新增 `tests/sdk_adapters/conftest.py` autouse 清空 |
| F7 字节稳定测试是空转（消息里根本没有 tool_calls） | minor | 成立 | 新增 `test_live_metadata_serialisation_is_byte_frozen` 黄金串（含非 ASCII/嵌套/null/bool/float） |
| F8 剥离顺序未被锁定（换行序不会红） | minor | 成立 | HTTP 跨跳 fixture 加 `NARRATION_CANARY`，断言不得出现在 `tool_calls` 内 |
| F9 `record` 可能把异常抛进 Provider 路径 | nit | 成立 | `record` 整体 try/except → 返回 `False` |
| F10 拒绝留存时留下旧答案可读 / 非 dict 静默变 `{}` | nit | 成立 | 拒绝路径统一毒化既有键；新增 `test_rejected_record_never_leaves_a_stale_answer` |
| F11 字节预算未计 `tool_name` | nit | 成立 | 抽出 `_entry_bytes(key, name, payload)`，四处共用 |
| F12 ARCHITECTURE 里日志名被换行截断 | nit | 成立 | 已改写该段 |

评审确认无误的部分：SDK 边界合规（不开 SDK 表）、既有 7 个 continuation 测试全绿、
请求指纹口径未动、降级日志无泄漏、内存有界、`read` 的 `move_to_end` 承重、
以及「tool 结果消息携带的正是原始 provider `call_id` 与 `call.name`」——所以生产命中
是真的，不是手搭消息的假象。

修订后复验：定向 provider 8 文件 **125 PASS**；`tests/sdk_adapters` **547 PASS / 58 既有红**
（失败集合与基线 `diff` 仍完全相同）；`tests/execution` 定向 3 文件 48 PASS / 4 既有红；
provider importers 31 PASS / 10 既有红——均与基线逐条一致。反向验证：把
`ToolCallArgumentsMemo.read` 强制打成永远未命中后，25 例中 **12 例转红**。
F1 复现脚本在修订后输出 `{}`/`{}`（并记 `ambiguous_call_ids=2`），缺陷关闭。
8 线程 × 5000 次混合读写/毒化/释放压测：字节账目零漂移，容量与字节上界均未越界。
