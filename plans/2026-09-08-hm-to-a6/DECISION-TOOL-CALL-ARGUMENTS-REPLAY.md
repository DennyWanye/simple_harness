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
历史不会以裸 assistant/tool 消息进入新 Context（见 §4），所以进程内备忘覆盖了事件 K
的全部发生面。

**与「Run 终态释放」的偏离及理由：** 任务建议按 Run 分组并在终态释放。实测发现
provider 请求身份在当前 SDK 下是 `provider-turn:N`（**不含 run id**，见
`_capture_public_tool_narration` 里为此做的单 Run 兜底），在 provider 模块内解析 run id
本身是脆弱的、要依赖 `_delivery_adapters` 只有一个活跃 Run。因此改为：

- **键 = `call_id`，读取时校验 `tool_name` 必须一致**（call id 由 provider 生成、全局唯一；
  名字不符即退化，杜绝张冠李戴）；
- **有界 LRU**：4096 条 **且** 总计 8 MiB，单条上限 256 KiB；超大单条直接不存（退化计数），
  不为它清空整个备忘。内存上界由容量本身保证，不依赖终态钩子。
- 保留 `release_call` / `clear` 供调用方与测试显式清理。

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
- **降级日志无载荷**：未命中只打 `rebuilt/restored/fallback_empty` 三个计数与
  `request_ref`（不透明摘要），不含 call id、工具名或任何入参值；有专测断言这些字符串
  不出现在日志里。
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

新增 17 例全绿，覆盖：durable 真实往返（`provider_response_json` →
`provider_response_from_json`，契约确实清空 metadata）后 memo 补回入参；真实 HTTP
（`httpx.MockTransport` + 生产 `ProductProviderAdapter`）跨 continuation 跳的线体断言；
并行多调用各归各位；同 call_id 异工具名不借用；未命中退化 + 无载荷计数日志 + 全命中不告警；
memo 复原与同进程 metadata 路径逐字节相同；无重建请求逐字节稳定（空 memo / 热 memo 两种）；
`deskpet_public_progress` 剥离后才留存；以及 memo 本体的边界、容量淘汰、LRU 刷新、
字节预算、超大条跳过、非法输入、last-write-wins/release/clear。

回归（全部与 `git stash` 基线逐条比对）：

| 套件 | 修复后 | 基线 | 结论 |
|---|---|---|---|
| `tests/sdk_adapters`（除 `test_composition.py`） | 539 passed / 58 failed | 522 passed / 58 failed | 失败集合 `diff` 完全相同；+17 全为新增用例 |
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
