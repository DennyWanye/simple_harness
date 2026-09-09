# 事件 Y：Run 内 reasoning 回传的质量——精确记账 + 有界回传

- 日期：2026-09-09
- 触发：HM-TO-A6 定向短旅程 ⑩a（`RUN-10A-ATTEMPT.md`）T6 被事件 W 的线上输入闸门终局拦下
- 涉及：`backend/deskpet/sdk_adapters/wire_input_budget.py`、
  `backend/deskpet/sdk_adapters/provider.py`、`backend/llm/model_info.py`
- 前序：[DECISION-W-BUDGET-BYPASS](DECISION-W-BUDGET-BYPASS.md)（实测输入下界）、
  [DECISION-TOKEN-ESTIMATOR](DECISION-TOKEN-ESTIMATOR.md)（Incident N/P 的拟合倍率）

---

## 0. 结论先行

事件 W 把「Host 量不到的输入质量」记成 **隐藏 carry** 去猜。事件 Y 证明：
那块质量的绝大部分**从来就在 Host 手里**——是 Host 自己
（`_message_payload`，`provider.py` ~696 行）把消息元数据
`provider_reasoning_content` 逐字写回 wire payload 的 `reasoning_content`
字段。落库的 canonical `request_json` 里没有它，所以之前只能在「计费 − wire」
的残差里看见它的影子，误判成中转站回灌。

真机探针（§1）进一步证明这块质量**既量得到、也裁得掉**：省略它不是协议错误。
于是本次做三件事：

1. **精确记账**：`wire_message_tokens` 直接把 `reasoning_content` 计进 wire；
   carry 只留给真正看不见的残差。事故那一轮的 `hidden` 从 **14 740 塌到 1 863**。
2. **有界回传**：按型号声明的 `preserve_reasoning` 契约收敛回传范围——
   工具循环之外的无条件不发；循环内的只在越界时**由老到新**丢，装得下就停手。
   事故那一轮因此**发得出去**，而不是终局失败。
3. **per-model thinking 开关**：`model_overrides.toml` 新增 `reasoning_mode`
   （`default` / `thinking` / `fast`），默认关闭；旅程/测试可以按型号钉
   `fast` → 请求带 `thinking={"type":"disabled"}`。

**没有**采用「终局优雅退出 + 稳定原因码」这条备选路线：探针证明契约不要求
全量回传，所以「丢一部分回传」严格优于「结束这条 Run」（§4 权衡）。回传全丢完
仍越界时，行为与事件 W 逐字相同（`sdk_provider_wire_input_budget_exceeded`）。

---

## 1. 契约取证（真机探针，2026-09-09，`deepseek-v4-flash`）

凭据 `.local-test-evidence/2026-09-07/credentials/deepseek.env`，全部是 tiny 请求
（`max_tokens` 30–600），脚本用后即弃（scratchpad，未入库）。基线是一条真实跑出来的
5 步工具循环（`get_num` 取 5 个槽位再求和），12 条消息。

### 1.1 回传是**按普通输入文本计费**的

同一条 payload，把 ~772 token 的文本分别挂到 5 条 assistant 的
`reasoning_content` 与 `content` 上：

| 变体 | `prompt_tokens` | 每块增量 |
| --- | ---: | ---: |
| 基线（无回传） | 718 | — |
| 5 × `reasoning_content` 大块 | 4 580 | **+772.4** |
| 5 × `content` 大块（对照） | 4 618 | +780.0 |
| 1 × `reasoning_content` 大块 | 1 456 | +738 |

阶梯（同一条 assistant，75 字符/单位，×1/2/4/8/16）：
689 / 702 / 728 / 780 / 884 → 斜率 **≈13.0 token/单位**，与上表的
12.87 一致。（`reasoning_content` 键本身存在会换掉 ~43 token 的信封，
所以 ×1 反而比基线低 29——量级无关紧要，**斜率**才是结论：回传与正文同一量纲。）

**这直接坐实了事故的归因**：那条 Run 15 轮 assistant 的
`reasoning_tokens` 逐轮相加 = **15 280**（见 §2 表），前 14 轮 = 12 877；
第 15 次调用 Host 量到 wire 10 078、provider 计费 24 818 →
10 078 + 12 877 = 22 955，残差 1 863（7.5%，tokenizer 密度 + 消息信封）。

### 1.2 省略回传**不是协议错误**

同一条工具循环，改写 assistant 的回传后重发：

| 变体 | 结果 |
| --- | --- |
| A 全量回传（对照） | 200，答案正确 |
| B **丢掉全部** `reasoning_content` | **200**，答案正确 |
| C 只留最后一条 | 200，答案正确 |
| D `reasoning_content=""` | 200，答案正确 |

再把三种策略各驱动一条完整的 5 步工具循环（每轮重新装配 wire）：

| 策略 | 是否走完 | 逐轮 `prompt_tokens` |
| --- | --- | --- |
| 全量回传 | 6 轮完成，答案 165 | 390/500/557/614/671/728 |
| 只留最后一条 | 6 轮完成，答案 165 | 390/503/563/620/677/734 |
| **一条不回传** | 6 轮完成，答案 165 | 390/534/599/659/719/779 |
| `thinking` 关闭 | 6 轮完成，答案 165 | 390 → 311/367/423/479/535/591 |

**结论**：既不 400、也不退化。声明表 `llm/provider_capabilities.py` 把
`deepseek-v4-*` 记作 `preserve_reasoning="tool_loop"`，探针不与之矛盾——
它只是证明「tool_loop」是**上界**而不是硬约束，所以循环内的老回传可以在
预算压力下丢。

### 1.3 `thinking` 关闭确实被这个端点支持

| 参数形 | 结果 |
| --- | --- |
| `thinking={"type":"disabled"}` | **200**，`usage` 里 `reasoning_tokens` **消失**，prompt 4 580 → **591**（输入侧的 `reasoning_content` 连计费都不进），工具调用照常 |
| `thinking={"type":"enabled"}` | 200，行为同默认 |
| `enable_thinking=false` | 200，但**无效**（仍产 reasoning，prompt 不变）——被静默忽略 |
| `reasoning_effort="low"` | 200，prompt 639，仍产 reasoning |
| 随手编的未知参数 | 200，**静默忽略** |

最后一行很重要：这个中转站对未知参数一律静默吞掉，所以「某个参数有效」必须由
**可观测的效果**证明。`thinking={"type":"disabled"}` 有两个独立效果
（`reasoning_tokens` 消失 + prompt 大幅下降），`enable_thinking` 一个都没有。

现有代码正好已经能发出这一形：`provider_capabilities.reasoning_wire_fields`
在 `reasoning_mode="fast"` + `request_style="thinking_object"` 下就产
`{"thinking": {"type": "disabled"}}`。缺的只是**怎么把这个模式钉给某个型号**（§3.3）。

> 旁注：`llm/code_params.py` 的 `code_params_to_request` 里 `thinking=False`
> 只是**不写** reasoning 字段，并不是「关闭」。它服务的是 `main.py` /
> `llm/resolution.py` 的旧 chat 车道，不在 SDK provider 路径上，本次不动它
> （followup F-Y-3）。

---

## 2. 事故复算

证据：`.local-test-evidence/2026-09-09/native-a6-run10a/primary-ui-gd0661ss/`
（`native.log` + `userdata/data/simple-harness-sdk/execution-v6.sqlite3`）。
失败的 Run `product-sdk-d1078cf4…` 共 16 次 provider 调用，工具集
tool_search ×8、tool_describe ×3、context_page_in、context_route、
task_scope_search、tool_activate、workspace_prepare。

| ordinal | `input_tokens` | `reasoning_tokens` | 累计 reasoning |
| ---: | ---: | ---: | ---: |
| 1 | 4 920 | 2 512 | 2 512 |
| 2 | 8 915 | 739 | 3 251 |
| 3 | 10 002 | 230 | 3 481 |
| 4 | 10 627 | 79 | 3 560 |
| 5 | 11 344 | 492 | 4 052 |
| 6 | 13 782 | 460 | 4 512 |
| 7 | 14 398 | 458 | 4 970 |
| 8 | 15 380 | 106 | 5 076 |
| 9 | 15 732 | 26 | 5 102 |
| 10 | 16 375 | 201 | 5 303 |
| 11 | 16 285 | 967 | 6 270 |
| 12 | 17 892 | 1 692 | 7 962 |
| 13 | 19 082 | 4 761 | 12 723 |
| 14 | 24 308 | 154 | 12 877 |
| 15 | 24 818 | 2 403 | **15 280** |
| 16 | — | — | 被闸门拦下 |

闸门那一行：`floor=27874 effective=26752 wire=10731 carry=17143
observed_input=24818 observed_output=2592 observed_hidden=14740
observed_wire=10078 window=32000`。

`observed_hidden = 24818 − 10078 = 14740` ≈ 前 14 轮累计 reasoning 12 877 +
残差 1 863。**这不是中转站回灌，是 Host 自己回灌。**

窗口 32 000、模型每步思考 ~1 K token 时，15 步工具循环光回传就吃掉 **15 K**，
分页/裁史（F-E2/F-E3）碰不到这块——它们只管工具结果。

---

## 3. 设计与实现

### 3.1 精确记账（`wire_input_budget.py`）

- `wire_message_tokens` 现在把消息上的 `reasoning_content` 一起计进 wire
  （新函数 `wire_reasoning_tokens` / `message_reasoning_tokens` 单独把这块量出来）。
- `ObservedProviderTurn` 新增 `reasoning_relay_tokens`：**那一轮 payload 上
  Host 自己量到的回传**。`ObservedInputCarryLedger.record_wire` 把它与 wire 一起
  记进 pending，`observe_usage` 配对时落进观测。
- carry 的第二项由「上一轮 `reasoning_tokens`」改成
  **`unmeasured_new_mass = max(0, new_mass − accounted)`**，其中

  ```
  accounted = max(0, 本轮 wire 上的回传 − 上一轮 wire 上的回传)   # 已经计进 wire
            + 本轮按契约主动丢掉的回传                            # 根本不在 prompt 里
  ```

  两条来源都不该再进 carry：前者会被算两遍，后者不存在。全扣完还剩的，才是
  「计了费、谁也量不到」的那一块——端点**自己**回灌时就落在这里，保守方向不变。

- 回执/日志新增 `reasoning_relay_tokens`、`reasoning_relay_dropped`、
  `reasoning_relay_dropped_messages`、`reasoning_relay_contract_dropped_messages`、
  `observed_reasoning_relay_tokens`。溢出那一行的**前缀不变**
  （`… floor=%d effective=%d …`），`scripts/native/a6_verify.py` 的正则照旧命中。

**代数上的重要性质**：把回传从 carry 挪进 wire **不改 floor 的总数**——
事故那一轮仍是 27 874（10 731 + 15 280 + 1 863 = 26 011 + 1 863）。
精确记账**只改归属**，不改结论；真正解锁那一轮的是 §3.2。这一点值得写死：
它说明「把估算做准」本身救不了这次事故，必须动 payload。

**兼容性**：payload 上一个 `reasoning_content` 都没有时（事件 W 的 239 组夹具
回放、非 thinking 端点），`accounted = 0`、`reasoning_relay_tokens = 0`，
这条线的算术与事件 W **逐 token 相同**——`test_wire_input_budget.py` 的 29 例
群体判据一条没改、全绿。

### 3.2 有界回传（`enforce_wire_input_budget`）

新入口 `enforce_wire_input_budget`（`_request_payload` 改调它）先裁后判，
两步共用**同一条**算术（`_measured_floor` 与 `check_wire_input_budget`），
所以「丢到刚好装得下就停」是确定性的：

1. **契约上无条件不需要的先丢干净**。`reasoning_relay_order` 按型号的
   `preserve_reasoning` 分两档：
   - `tool_loop`（`deepseek-v4-*`，以及**所有未声明的型号**——适配器对
     `behavior=="unknown"` 一律按 `tool_loop` 传，不因「未声明」就更激进）：
     最后一条 `user` 消息**之前**的回传契约上从来不需要 → 无条件丢；
   - `all_turns`（kimi 系）：没有无条件可丢的；
   - `not_required`：全部无条件可丢。
2. **还越界就在当前工具循环内由老到新丢**，floor 一落回
   `effective_input_budget` 以内立刻停手。最近一轮的思考留到最后——
   thinking 模式对**当前这一步**的连贯性依赖的正是它。
3. **全丢完仍越界** → 回到事件 W 的终局行为
   `sdk_provider_wire_input_budget_exceeded`，一个字节都不发。那时超的已经
   不是回传而是受保护的正文，只能由装配期（强制分页/裁史）去解决。

`payload["messages"]` 是**就地**改写（只摘 `reasoning_content` 键）：它是
`_request_payload` 刚装配出来的本地对象，改的就是真正要发出去的那份，
不存在「量的和发的不是同一份」的缝——测试直接断言 transport 上的 JSON。

裁剪落一行**只有计数、没有 payload** 的 INFO：
`sdk_provider_reasoning_relay_trimmed dropped_messages=… contract_dropped_messages=…
dropped_tokens=… kept_tokens=… ordinal=… preserve=…`。

**事故那一轮的结果**：缺口 27 874 − 26 752 = 1 122；每条回传 ~1 000 token →
丢**最老的 2 条**，剩 13 条原封不动，请求发得出去。

### 3.3 per-model `thinking` 开关（`model_info.py` + `provider.py`）

- `ModelContextInfo` 新增 `reasoning_mode: str = "default"`，并进
  `_OVERRIDABLE_FIELDS`，所以走的是既有的三层链（内置 ← 全局
  `model_overrides.toml` ← 项目 `.deskpet/context.toml`）。
- 新函数 `resolve_reasoning_mode(model)`：总函数，非法值 → `"default"`
  （手写错的 TOML 不该让 provider 起不来）。
- `ProductProviderAdapter` 里新增 `_reasoning_params`：**只在会话完全没有表态时**
  （既没 `reasoning_mode`、也没 `thinking`/`fast`）才把文件里的默认填进
  `model_params`，所以用户在「模型与参数」面板选的东西永远赢。
- 默认 `"default"` = 一个 reasoning 字段都不写，行为与本次之前逐 token 相同。

**A6 驱动怎么用**（`%APPDATA%/deskpet/model_overrides.toml`，
测试里由 `DESKPET_USER_DATA_DIR` 钉到 tmp）：

```toml
[models."deepseek-v4-flash"]
context_window = 32000
reasoning_mode = "fast"      # → 请求带 thinking={"type":"disabled"}
```

窗口那一档本来就是这么钉的（事故当场就是），所以这是**同一个文件、同一个段**，
驱动不需要新的配置面。

---

## 4. 权衡记录

| 备选 | 取舍 |
| --- | --- |
| **A. 有界回传（采用）** | 探针证明契约不要求全量回传，丢老回传既合法又不退化；代价是模型看不到更早几步的思考。事故形态下只丢 2/15。 |
| B. 终局优雅退出 + 稳定原因码 | 只有在「契约要求全量回传」时才是必要的。探针证伪了那个前提，此时选 B 等于**主动**结束一条本来能继续的 Run。不采用。 |
| C. 只做精确记账 | §3.1 的代数证明它**不改 floor**，救不了事故。必要但不充分。 |
| D. 无条件只回传最后一条 | 最省，但对 `preserve_reasoning="tool_loop"` 的声明不忠实，且在预算充裕时白白丢连贯性。改成压力驱动。 |
| E. 全局关掉 thinking | 是**运行配置**不是修复：flash 关掉 thinking 会改变整条旅程的模型行为，不能当默认。做成 §3.3 的 per-model 开关、默认关闭。 |
| F. 重拟 `input_estimate_ratio` 三元组 | 事件 W 已证明同型号同 ordinal 上「判出超预算」与「不误伤」对倍率无交集。不动。 |

**保守方向的守卫**：如果某个端点**自己**回灌（Host 手里没有原文），
`accounted` 里的 relay 增量为 0、`dropped` 也为 0，`unmeasured_new_mass`
退回整个 `reasoning_tokens`——与事件 W 逐 token 相同。这条退化路径由
`test_a_payload_without_any_relay_is_byte_identical_to_event_w` 钉住。

---

## 5. 测试

`backend/tests/sdk_adapters/test_reasoning_relay_budget.py`（新增，**17 绿**）：

| 组 | 例数 | 钉住什么 |
| --- | ---: | --- |
| 1 精确记账 | 3 | 回传按文本计量；wire 估算 vs 合成计费残差 <3%（修复前残差 = 整块回传）；hidden 从 14 740 塌到 1 863 |
| 2 有界回传 | 4 | 事故形态**不裁就仍然 27 874 > 26 752**（红）；裁后装得下、只丢最老 2 条、剩 13 条（绿）；装得下时一条不丢；循环外无条件丢；`all_turns` 型号不无条件丢 |
| 3 终局不变 | 2 | 回传全丢完仍越界 → `sdk_provider_wire_input_budget_exceeded`，回执留证；无回传 payload 与事件 W 逐 token 相同 |
| 4 接线 | 3 | 端到端断言**真正发到 transport 上的 JSON**：回传真的上了 wire、循环外的真的没上、压力下真的丢最老的（第二轮不再终局失败） |
| 5 thinking 开关 | 4 | 默认关闭；`model_overrides.toml` 钉 `fast` → `{"thinking":{"type":"disabled"}}`；会话 `model_params` 永远赢；非法值退回 `default` |

「事故形态」用例：1 条 user + 15 组（assistant tool-call + tool result），
每条 assistant 背 1 000 token 回传，非 reasoning 文本合计钉在 **10 731**
（与事故日志的 `wire=` 逐 token 相同），上一轮观测灌真实的
(10 078+12 877, 24 818, 2 592, 2 403)。

**回归**（named files，一次一个 pytest 进程）：

- `test_wire_input_budget.py` 29 绿（一条没改）、`test_token_estimator_calibration.py` 54 绿、
  `test_model_info.py` / `test_model_catalog.py` / `test_code_params_mapper.py` /
  `test_provider_reasoning_capabilities.py` / `test_a6_verify_budget_bypass.py`
  合计 **160 绿**。
- provider 面：`test_provider_tool_call_continuation.py`、
  `test_provider_tool_call_arguments_replay.py`、`test_provider_rejection_diagnostic.py`、
  `test_provider_tool_arguments_repair.py`、`test_provider_timeout_is_a_safety_net.py`、
  `test_primary_provider_preflight.py`、`test_context_route_nonstrict_wire.py`、
  `test_agent_provider.py` 合计 **89 绿**。
- `test_typed_context_use_primary.py` / `test_primary_history_outbound.py` 等 **10 例失败，
  与 main 基线（`cf61431f`，一次性 worktree 复跑）FAILED 集合逐条相同**，与本次无关。

---

## 6. Followup

- **F-Y-1（A6 驱动，下一次短旅程）**：窗口不必再钉 32 000。钉窗口是为了在小窗口下
  暴露预算路径，但 15 步工具循环 + 每步 ~1 K 思考在 32 K 下**必然**耗尽预算，
  再跑一次仍会撞同一堵墙（只是撞得晚一点）。建议二选一：
  (a) 窗口放回 128 000 让旅程跑完，用回执里的
  `reasoning_relay_tokens` / `reasoning_relay_dropped` 观察真实回传质量；
  (b) 保持 32 000 但同时钉 `reasoning_mode = "fast"`，把回传这条变量整体拿掉，
  专测分页/裁史（F-E2/F-E3）。**不要**两个都不改。
- **F-Y-2（SDK 上游）**：`reasoning_content` 与 `tool_calls` 一样，是**公共 transcript
  字段**而不是 provider 私有元数据。上游把它当一等公民后，`_message_payload` 的回灌与
  `ToolCallArgumentsMemo` 都可以撤掉，装配期的预算闸门也就第一次能看见这块质量。
- **F-Y-3（旧 chat 车道）**：`llm/code_params.py` 的 `thinking=False` 只是不写字段，
  不是关闭。要么按 §1.3 补上 `thinking={"type":"disabled"}`，要么在文档里写明它
  只影响 `reasoning_effort`。
- **F-Y-4（校准量）**：`deepseek-v4-pro` 的 `input_estimate_ratio*` 三元组是在
  「回传是隐藏质量」的前提下拟的（Incident N/P）。精确记账之后 pro 的残差
  也应该塌回 tokenizer 密度那一档，值得用新证据重测——但**这次不动**，
  没有 pro 的新配对。
