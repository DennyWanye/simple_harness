# 决策备忘：Host 输入 token 估算低估 provider tokenizer（Incident N）

- 日期：2026-09-08
- 分支：`worktree-token-estimator`（基线 `68755c78`）
- 证据：`.local-test-evidence/2026-09-08/native-a6-run4/`（124 组）与
  `.local-test-evidence/2026-09-08/native-a6-b3682fe1/`（182 组），
  合计 **306 组** `provider_invocations.request_json` × `usage_json` 真机配对；
  模型 `deepseek-v4-pro`，窗口钉在 32000 → `budget_tier=8192`、
  `effective_input_budget=26752`。
- 相关前案：`DECISION-SAME-RUN-CONTEXT-BOUND.md` §8 F-E4（已指出
  `_plan_turn_messages` 漏算 tool schema，且估算比 provider tokenizer 低约 1.9×）。

---

## 1. 现象与量测结论

冻结口径（`testcase/human-memory-program/fixtures/metric-formulas.json`
的 `context_budget`）要求
`actual_provider_input_tokens <= effective_input_budget`，且
`token_underestimate_allowed: false`。修复前**每一组**证据都违反了这个方向。

| 口径 | provider ÷ Host 估算 |||| 低估条数 |
|---|---|---|---|---|---|
| | p50 | p95 | max | | |
| 修复前 `_plan_turn_messages`（只数 message 文本） | 1.98× | 3.97× | 4.87× | | 306 / 306 |
| 修复前 `primary_context.prepare`（文本 + `repr//4` 的 schema） | 1.45× | 2.11× | 2.92× | | 306 / 306 |
| **修复后**（wire 口径 × per-model 校准） | **0.66×** | **0.89×** | **0.99×** | | **0 / 306** |

绝对量：provider `input_tokens` min 4593 / p50 14877 / **max 44378**；
其中 **27 / 306** 条真实超过 `effective_input_budget=26752`，
修复前的估算**一条都没看出来**（全部被判为"在预算内"），修复后 **27 条全部被判为超预算**
——即在装配阶段就会先触发裁剪/分页，而不是照原样发给 provider。

### 1.1 低估的四块来源（按贡献排序）

1. **provider 侧隐形注入（最大一块）**。
   中转站把上一轮 assistant 的 `reasoning_content` 重新塞回下一轮 prompt。
   Host 完全看不见它：`ProviderRequest.messages` 里 assistant 消息的 `content` 是空串，
   `reasoning_content` 只存在于 SDK 私有 metadata，且主对话链路重建 Context 时会被剥掉。
   证据：把 `cum_reason`（本 Run 之前各轮 `usage.reasoning_tokens` 累加）作为特征做
   非负最小二乘，`R²` 从 0.925 升到 **0.993**，其系数 **1.10**（≈1.0，符合"原样加回"），
   同时"每条消息的固定开销"从 104 tokens 掉到 14 tokens。
   单轮 `reasoning_tokens` p50=103、**max=9849**，累计最高 **14716**——
   在 44378 的最大 prompt 里占约 33%~40%。
2. **tool schema 完全没算（F-E4）**。`_plan_turn_messages` 的 `protected_tokens`
   只累加 message 文本；证据里 tools 数组按 wire 口径是 **3423~3819 tokens**，
   `primary_context.prepare` 虽然算了，但用的是 catalog 的
   `schema_token_count = len(repr(input_schema)) // 4`——**丢掉了工具名与
   description，且数的是 `repr` 而不是真正发出去的 JSON**，只报 2277~2487，
   约低估 **35%**。
3. **JSON 比散文密**。工具回执正文里全是 hash / id / 转义引号，
   实测 **~3.1 非 CJK 字符 / token**；而固定的 persona + tool schema 是 **~4.0**。
   共享的 `/4` 常量对后者正确、对前者低估约 30%。
4. **wire 框架**。每条消息的 role / name / tool_call_id，以及适配器为每条
   tool 回执**合成**的 assistant `tool_calls` 数组
   （`sdk_adapters/provider.py::_wire_messages`，因为主对话链路重建时
   `provider_tool_calls` metadata 不存在，会退化成 `arguments:"{}"`）。

补充观察（与 CJK 无关的反直觉点）：证据里 CJK 占比其实很低——
工具回执文本 CJK 仅 2.0%，用户/助手文本 6.7%。**低估的主因不是中文，而是
JSON 密度 + 漏算 tool schema + provider 隐形注入**。原始事故描述里的
"CJK-heavy" 只是表象。

---

## 2. 被否决的方案（与理由）

- **A. 内置真实 tokenizer（tiktoken / DeepSeek BPE）**。否决。
  `backend/deskpet/agent/tokens.py` 已记录既有取舍：BPE 权重 ~30MB、首次使用要
  从 openaipublic 下载（中国大陆常被墙）、且 OpenAI 的 BPE 跨 provider 本来就不准。
  更要命的是：**再准的 tokenizer 也看不见 provider 自己塞回去的 reasoning_content**，
  它解决不了第 1 块（最大的一块）。
- **B. 全局把 `text_tokens` 的 `/4` 收紧到 `/3` 或 `/3.5`**。否决。
  这会让两个**本来真的装得下**的既有绿场景直接 fail-close：
  `test_current_tool_pages::test_same_run_settled_results_...`（36 条同 Run 回执，
  32768 窗口）和 8192 档的 megabyte 场景。而且"密度"本身是 **tokenizer 属性**，
  不同 provider 不同，塞进一个全局常量既不准也不可按型号调。
  → 密度归入 per-model 校准（见 §3）。
- **C. 闭环反馈：把本 Run 上一轮的 `usage.input_tokens` 作为 floor**
  （即 `agent/token_budget.check_budget(real_prompt_tokens_floor=)` 在聊天链路的做法）。
  **技术上这是最准的**：实测 `max(static, prev_input + prev_output + Δstatic)`
  中位比值 0.993、最大 1.072，中位只多估 1%（对比本次采用方案的 1.52×）。
  但**本轮不做**：它要求在同步的冻结装配器里读到跨进程的 durable usage
  （`sdk_provider_attempt_audit` / `record_context_usage_sample` 在 Host state.db，
  且投递靠异步 pump，存在滞后与一致性问题），改动面横跨 SDK ledger 与
  `main.py` 装配线，与本轮"有界修复"不匹配，也会撞上其他 agent 正在改的
  `provider.py` / 投影链路。**记为 followup（F-TOK-1，见 §7）**。
- **D. 不分型号的固定倍率**（例如全局 ×1.75）。否决。
  会让所有 provider（包括不做隐形注入的）平白损失 40%+ 可用窗口。

---

## 3. 采用的方案

### 3.1 估算量 = "Host 真的发出去的东西" + "按型号校准的看不见的东西"

```
planned_input_tokens
  = ceil( ratio(ordinal) × Σ text_tokens(message_i) )
  + ceil( ratio(ordinal) × tool_schema_tokens(tools) )

ratio(ordinal) = clamp( base + per_turn × provider_turn_ordinal , 1.0 , max )
```

- `text_tokens` **口径不变**（CJK 1 token/字，其余 `/4` 向上取整），
  保证所有既有场景 byte 级兼容；
- `tool_schema_tokens(specs)` 新增：按真正发出的 JSON 数
  （工具名 + description + `canonical_json(parameters)`，再加每条 8 tokens 的
  `{"type":"function","function":{…}}` 外壳）。在证据的 307 个带 tools 的请求上
  与"把同一个 `/4` 公式套在精确 wire 数组文本上"对比（中位，按工具数分组）：

  | 工具数 | 请求数 | `tool_schema_tokens` | 精确 wire 文本 | 比值 | 旧 `repr//4` |
  |---|---|---|---|---|---|
  | 12 | 255 | 3423 | 3687 | 0.928 | 2277 |
  | 13 | 39 | 3593 | 3874 | 0.928 | 2409 |
  | 14 | 13 | 3819 | 4116 | 0.928 | 2487 |

  即仍比精确 wire 文本低约 **7.2%**（差额是数组分隔符与每条 spec 的键名，
  8 tokens 的固定外壳没吃满）。这块残差对**已校准**型号由 `ratio` 吸收；
  对未校准型号，它相对旧口径仍是从"完全不算"（`_plan_turn_messages`）
  或"低 38%"（`primary_context` 的 `repr//4`）大幅收敛。刻意不把 8 调大：
  没有证据支持一个更大的常数，而 per-model 校准是可按实测调的那一档；
- `ratio` 覆盖三件 Host 结构上看不见的事：tokenizer 密度、每条消息的
  chat-template 框架、以及 provider 自己塞回的 reasoning。三者都是
  **provider 属性**，所以统一放在一处，按型号配置。
- **默认 1.0 / 0.0 / 1.0 = 恒等**：未校准型号（含 `_default` 兜底）
  的估算 == 旧估算 + tool schema，**不会多 fail-close 任何 Run**。

### 3.2 per-model 取值（`backend/llm/model_info.py`）

`ModelContextInfo` 新增三个字段，并加入 `_OVERRIDABLE_FIELDS`
（中转站行为各不相同，允许用户按自己观测到的 `usage.input_tokens` 用
`model_overrides.toml` / `.deskpet/context.toml` 调）：

| 字段 | 默认 | `deepseek-v4-pro` / `deepseek-v4-flash` |
|---|---|---|
| `input_estimate_ratio` | 1.0 | **1.35** |
| `input_estimate_ratio_per_turn` | 0.0 | **0.11** |
| `input_estimate_ratio_max` | 1.0 | **2.5** |

取值方法：在 306 组证据上网格搜索，**约束"零低估"**，目标"中位多估最小"。
`min(1.35 + 0.11·ordinal, 2.5)` 落在最优附近（中位多估 1.52×、p95 1.93×、
最大 2.09×）。用 `provider_turn_ordinal` 而不是消息条数做自变量，是因为
隐形注入的量正比于**本 Run 已经产生过的 assistant 轮数**，而历史引用里的
assistant 消息并不携带 reasoning。

**代价必须说清楚**：校准后中位多估 1.52×，即 DeepSeek 上可用上下文按估算口径
少约 1/3。在生产窗口（1M）下这没有实际影响；只有像本次证据那样把窗口
**钉到 32000** 时才会明显更早裁剪——而那正是我们要的方向
（旧口径在那里把 44378 tokens 的 prompt 当成 18838 发了出去）。

### 3.3 改点（file:line，基于本分支提交后）

| 文件 | 位置 | 改动 |
|---|---|---|
| `backend/deskpet/sdk_adapters/context_partitions.py` | `:68-98` | Incident N 口径说明 + `NON_CJK_CHARS_PER_TOKEN` / `WIRE_TOOL_SPEC_OVERHEAD_TOKENS` |
| 同上 | `:101-115` `text_tokens` | 常量化，数值口径与旧版**完全一致** |
| 同上 | `:118-144` `ProviderTokenCalibration` | 新增；`ratio()` 下限恒为 1.0（误配 <1 的 override 不会变成低估） |
| 同上 | `:147-176` `calibration_for_model` | 走 `llm.model_info.resolve()`（让全局 `model_overrides.toml` 真的能到这条链路），任何异常回落恒等 |
| 同上 | `:177-204` `window_tokens_for` | 新增；缺 window 时先回落到**该型号自己的 window**（`resolve()`，含 override），型号未知才回落最小档 |
| 同上 | `:207-252` `tool_schema_tokens` | 新增；同时吃 catalog dict 与 `ProviderToolSpec`（先 `thaw_json` 再 `canonical_json`），序列化失败时退化为 `repr(thawed)` 而不是抛 |
| 同上 | `:210-228` `turn_token_estimator` | 返回带校准的 `token_estimator`，让 `trim_causal_groups` / `assemble_partitions` 形状不变 |
| `backend/deskpet/sdk_adapters/context_authority.py` | `_plan_turn_messages` `:941-1102` | 新增 `tools` / `provider_turn_ordinal` / `model_id` 参数；**tool schema 计入 `protected_tokens`**（F-E4）；裁剪与 fail-close 都改用带校准的 estimator；facts 增加 `tool_schema_tokens` / `planned_input_tokens` |
| 同上 | `:1050-1058` | fail-close 前落一条带全部数字的 warning（此前完全没有可诊断信息） |
| 同上 | `_resolve_model_id` `:897-909` | 从 `context_metadata.run_binding.model_id` 解析型号 |
| 同上 | `prepare_snapshot` `:1318-1329` | 把 tools / ordinal / model_id 传进去 |
| `backend/deskpet/execution/primary_context.py` | `prepare` `:169-194` | 改用同一套 estimator + `tool_schema_tokens`（保留 `schema_token_count` 兜底，绝不静默变 0） |
| `backend/main.py` | `_freeze_sdk_catalog` `:7749,7790` | `schema_token_count` 改用共享 `tool_schema_tokens` |
| `backend/deskpet/sdk_adapters/tools.py` | `filter_sdk_catalog_for_workspace` `:1142-1144` | 同上 |
| `backend/deskpet/memory/s4_value_adapter.py` | `:510,521` | 同上 |
| `backend/deskpet/execution/current_tool_pages.py` | `current_tool_allowance` `:214-235`、`:264-267` | 同 Run 上界的额度同样除以校准倍率——否则校准型号会「过了上界、却爆预算」，在还能分页时 fail-close |
| `backend/llm/model_info.py` | `:79-94,114-141,217-219` | 三个校准字段 + DeepSeek v4 取值 + 可 override |

**request fingerprint 不受影响**：`provider_request_fingerprint` 只对
messages/tools 内容取值，本次没有改动任何消息内容或工具 schema；
改的只是"装配阶段用多少预算"。`safety_margin` / `GENERATION_RESERVE` /
`PARTITION_CAPS` / `effective_input_budget` 语义与数值**一字未动**
（`test_frozen_constants_match_metric_formulas_oracle` 仍绿）。

---

## 4. 前后对照（306 组证据）

| | 修复前 | 修复后 |
|---|---|---|
| provider ÷ 估算 最大比值 | **4.873×**（只数消息）/ 2.920×（primary_context 口径） | **0.993×** |
| provider ÷ 估算 中位比值 | 1.979× / 1.454× | 0.659× |
| 低估条数（估算 < provider） | **306 / 306** | **0 / 306** |
| 真实超 `effective_input_budget` 的请求 | 27 条 | 27 条（历史事实不变） |
| 其中被 Host 判为超预算（会触发裁剪/分页） | **0 条** | **27 条** |
| 估算 ÷ provider（多估幅度） | — | p50 1.52× / p95 1.93× / max 2.09× |

不变式：装配器保证 `估算 <= effective_input_budget`；本次保证
`估算 >= provider_input_tokens`（306/306）。两者复合即冻结口径要求的
`provider_input_tokens <= effective_input_budget`。

---

## 5. 有意接受的行为变化（只有 megabyte 场景的 4096 档）

tool schema 是**真实存在、不可裁剪**的请求负载，一旦如实计入，刻在预算
边缘的极限场景会改判。方向是**更安全**的（安全停机，而不是把 Host 自己
没量准的请求发出去），已在测试里写明原因：

1. `tests/execution/test_current_tool_megabyte.py` 的 **4096 档**：修复前
   就已经是"预算安全停机"，但停在写完两个 >1MB 结果之后；现在**提前一个
   provider turn**——停在第 4 次装配，也就是本该下发写盘批次的那一轮，
   因此不再产生 megabyte 结果，也来不及路由 TaskScope（无 closure receipt）。
   这不是新增的限制：它前一次真的发出去的请求已经是 **11 559 wire bytes**，
   而 4096 档的 `effective_input_budget` 只有 **2663 tokens**——旧口径把
   1198 tokens 的 tool schema 当成 0 才让它过关。
   参数化为 `budget_stop = context_window == 4096`，并断言
   `wire_count == len(wire_sizes) == 3` 与 `sizes == []`。
2. `tests/execution/test_current_tool_pages.py`：`expected_budget_stop`
   分支里 `task_scope_closure_receipts` 断言收紧为"4096 档为空、其余为
   `[pending]`"，并注明"绝不允许出现已结算/已关闭的 receipt"。

**8192 档不改判**（本轮中途修正）。首版提交 `de82597c` 里 8192 档被判为
"planned 5342 vs budget 5325、差 17 tokens 超预算"，根因是 `tool_schema_tokens`
拿到 `ProviderToolSpec` 冻结出的**递归 `mappingproxy`** 时 `canonical_json`
抛 `TaskScopeProtocolError`（它是 `ValueError` 子类，被
`except (TypeError, ValueError)` 静默吃掉），退化成 `repr()`——每层嵌套都带
一个 `mappingproxy(...)` 外壳，在本证据的 12/13/14 工具 catalog 上实测比
真正发出的 JSON **多算 13.0%**（3867 vs 3423 / 12 工具），schema 越深差得越多。
**这恰好会让本次修复本来要统一的两条链路继续各算各的**：S5a 拿冻结 spec、
primary 拿 catalog dict。改为先 `thaw_json` 再 `canonical_json` 后，两条链路
在同一份 catalog 上**逐位相等**（3423 == 3423），8192 档回到"分页并完成"，
32768 档（36 条同 Run 回执 + 分页）同样**未回归**。

另外一处不属于"接受的变化"、而是这次如实计量**必须**同时修的：
`current_tool_allowance` 用未校准的 `text_tokens` 量同 Run 回执，而
`_plan_turn_messages` 改用带校准的 estimator 判预算。两者不同步的话，
校准型号会出现"过了同 Run 上界所以不分页 → 紧接着爆预算 fail-close"，
在明明还能分页时停机。因此该额度同样除以 `ratio(provider_turn_ordinal)`，
并与 `_plan_turn_messages` 共用 `window_tokens_for` 解析窗口档位。

---

## 6. 验证

- §1 / §4 两张表是用**最终**估算器（含 `thaw_json` 修正与 `window_tokens_for`）
  在两份证据库上重跑 306 组得到的，数值与首版提交一致——证据里的 `tools`
  本来就是 JSON 解出的 dict，冻结 spec 的 `repr` 退化只影响运行期的 S5a 链路。
- 新增 `backend/tests/sdk_adapters/test_token_estimator_calibration.py`（32 项，全绿），
  其中 7 项覆盖本轮补齐的两块：`window_tokens_for` 的三条回落（绑定窗口 /
  已知型号自身窗口（含 override）/ 未知型号回最小档）、元数据异常时
  `window_tokens_for` 与 `calibration_for_model` 双双退化而不抛，以及
  `tool_schema_tokens` 在 schema 无法序列化时退化计数而不抛。
  夹具 `backend/tests/fixtures/hm_to_a6_token_estimator_samples.json`：
  从 306 组证据里挑 12 条代表样本（最紧的 5 条 + provider tokens 最大的 3 条
  + 最松的 2 条 + 最小的 2 条），**只保留每条消息/每个工具的
  「CJK 字符数 / 非 CJK 字符数」与 provider 自报的 `input_tokens`**。
  估算器是纯字符类函数，所以用 `"汉"×N + "x"×M` 复原出的估算值与原文
  **逐位相等**（生成脚本对每条样本做了 round-trip 断言），既是等价证据，
  又不带出任何原文。
- 触及套件（`-p no:randomly`，`tests/execution` + `tests/sdk_adapters`，
  排除 `test_composition.py`）：基线 `68755c78` **111** 项 FAILED/ERROR，
  首版提交后 **110** 项，**本轮最终 107 项**（97 failed + 10 errors，
  769 passed / 1 skipped，538 s）。逐条 diff：**没有任何新增红条**，
  相对首版提交反而恢复了 3 条——
  `test_primary_foreground_runtime::test_primary_none_routes_to_exact_task_and_writes_real_file[True]`
  与 `test_scope_disclosure_runtime::…legacy_gap_with_actual_file_terminal[True-True] / [True-search]`。
  其余为既有红：short-index embedder / s5b / effect_gate / s5a /
  scope_disclosure / typed_context_use_primary / `/Users/denny` 路径 /
  `test_late_history_denial…[sent_unknown]` 等。
- 定向绿（最终态一次跑完，**107 passed**）：
  `test_token_estimator_calibration.py`、`test_current_tool_megabyte.py`、
  `test_current_tool_pages.py`、`test_context_partitions.py`、
  `test_context_preparation.py`、`test_context_authority_primitives.py`、
  `test_model_info.py`、`test_p5s2_token_budget.py`、
  `test_token_budget_per_model.py`。
- importer 冒烟：`import main` 与 `context_partitions` / `context_authority` /
  `current_tool_pages` / `primary_context` 均正常。
- `ruff check`（触及文件）：本轮改动未引入新条目；仓库既有的
  I001 / C408 / RUF046 噪声未处理。

---

## 7. Followup

- **F-TOK-1（高价值）**：闭环 usage 反馈。把本 Run 上一轮的
  `usage.input_tokens + output_tokens` 作为估算下界注入装配器
  （`max(static, prev_input + prev_output + Δstatic)`）。
  实测中位比值 0.993、最大 1.072，可把当前 1.52× 的多估压到 ~1.05×，
  同时对**任何** provider 的隐形注入天然免疫，不再依赖手工校准表。
  需要：装配链路能同步读到本 Run 的上一次 attempt usage（现为异步 pump
  投递到 Host state.db），是一次跨 SDK ledger 的改动。
- **F-TOK-2**：`sdk_provider_attempt_audit.context_window` / `effective_ceiling`
  在本次证据里记的是 0（`run_context_snapshot_receipts` 里的
  `budget_tier=8192` 才是对的）。审计口径与装配口径不一致，独立于本次修复。
- **F-TOK-3**：`_wire_messages` 在主对话链路上把 assistant `tool_calls` 的
  `arguments` 退化成 `"{}"`（原文其实在 SDK 的 provider-invocation 记录里）。
  上游把 `tool_calls` 提升为一等公共 transcript 字段后即可去掉该降级，
  届时 wire 与估算都会更贴近真实。

---

## 附录（2026-09-09）：三条并行车道合流后的 8192 档预算复原

分支 `worktree-token-budget-reconcile`，基线 `26247ea8`。
用户授权「不问、自行裁定并记录」，本节即裁定记录。

### 附.1 现象：各自都绿，合起来红

`tests/execution/test_current_tool_megabyte.py::…[8192]` 在
`26247ea8` 上转红，而当晚合入的三条车道**每一条单独都保持它是绿的**：

| # | 提交 | 改动 | 对该场景峰值轮的增量 |
|---|---|---|---|
| N | `8e39f018` | tool schema 计入 `protected_tokens`（本备忘 §3） | **+1379**（该车道原本按 0 计） |
| — | `d5c72465` | `MEMORY_TYPE_SELECTION_POLICY` 拼进 `memory_types` 的 schema description | **+136** |
| Q | `26247ea8` | PERSONA 补五路由段（481 字符） | **+103** |

峰值轮实测（`_plan_turn_messages` 的 warning，8192 档）：

```
planned=5418 effective=5325 protected=3350 tool_schemas=1379 groups=1 ratio=1.00
```

拆解：PERSONA + Host 可信时钟 **1260**、语义收口 system 指令 **711**、
tool schema **1379**（合计 protected 3350），未收口因果组 **2068**。
`effective_input_budget(8192) = 8192 − 2048（生成预留）− 819（10% 安全余量） = 5325`。
**超 93 token。**

任意去掉其中一条即可回到预算内（5315 / 5282 / 4039），
所以三条车道各自的单测结论都成立——**合流本身才是缺陷**。
特别地：N + d5c 合流后 8192 档只剩 **10 token** 余量，
Q 的 103 token 落在这条只剩 10 token 的线上，必然溢出。

### 附.2 裁定一：不是校准倍率的问题，megabyte 夹具的预算假设不需要重新推导

该场景绑定的型号是夹具的 `model`，`llm.model_info` 认识它但**没有配校准三元组**，
所以 `ProviderTokenCalibration` 退化为恒等式，日志里的 `ratio=1.00` 就是证据。
`5418` 是**未经任何倍率放大**的 wire 口径数值：
夹具里 1 MiB 结果本身从未整体上过线（它以 `primary_settled_effect_v1` 摘要 +
`context_page_in` 引用travel，两条摘要各 399 token），
`effective` 也自 V0 冻结以来未变（`metric-formulas.json` 逐字节钉死）。

→ **夹具的预算假设不 predates calibration，重新推导它是错的**：
真正变了的是「同一请求里模型可见文本的总量」，该改的是文本，不是预算。

### 附.3 裁定二：压缩措辞 + 去重，两份备忘点名的负载条款一字不改

改动只有两个文件（外加一条新守卫测试）：

**A. `backend/deskpet/execution/primary_context.py` 的 PERSONA：1208 → 1083 token（4829 → 4330 字符）**

- **一字未动**：事件 Q 的五路由整段（`DECISION-TERMINATION-AND-PERSONA-ROUTES.md`
  §三.3 明确「a year」与「typed recall never returns task scopes」是这段里唯一
  起作用的部分，别动它们）；历史引文封框段；`REMINDER_CAPABILITY`。
  测试钉死的判别词（五个路由名、`an active scope needs no search`、
  `task_scope_search first`、`rewriting` + `not a new project task`、
  `conflict_notice` / `contested` / `ask the user which one applies`、
  `procedure_hint` / `procedure_discover` / `trigger_local`）全部仍在。
- **同义压缩**（条款一条不少，只并句去冗）：记忆检索段、Procedure 段、
  `trigger_local` 段、`conflict_notice` 段、`create_new` 段、`context_page_in` 段。
- **跨面去重**（删掉的话，同一请求里另一处仍原样送达模型）：
  1. 「A stored Procedure stays outside typed recall until it has actually been
     used once」——`MEMORY_TYPE_SELECTION_POLICY` 的 R4 就是这句话，
     且 `DECISION-EXTRA-TYPE-RATE.md` §3.1 自己写明二者「同源」，
     只是 R4 落在**模型选参数的那一刻**，比 PERSONA 更早也更贴近现场。
     **保留**了 R4 没有的那半句推断护栏：「never conclude from the absence …
     that you saved no such workflow」。
  2. 「The ref of a recall fragment … is not a page reference; never pass it as
     reference_id」——`CONTEXT_PAGE_IN_SCHEMA.reference_id` 的 description
     已逐字带着同一个例子（`"recall-item:<id>:1"`）。
  3. 「choosing the needed memory_types from the question」——
     `memory_types` 的 description 现在把选型规则讲得细得多。

**B. `backend/deskpet/sdk_adapters/context_route.py` 的 schema description：632 → 600 token**

正是 `DECISION-EXTRA-TYPE-RATE.md` 的 **F-ETR-4** 点名的杠杆
（「压缩 `context_route` schema 里与本轮无关的长描述」），本轮消费掉它：

- `reuse_workspace_of` 105 → 92：四条契约（仅 create_new / 从公开
  search·resume 抄 `task_scope_id` 与 `source_hash` / 新绑定授权到唯一已验证
  root 且绝不重开旧任务 / 否则两个字段都省略或 JSON null、绝不用占位串或伪造
  hash）**一条不少**，只是并句。
- `expected_source_hash` 49 → 35：省略/JSON null/不得伪造这条规则原本在两个
  字段里各写一遍，现在只在 `reuse_workspace_of` 里说一次。
- `include_short_horizon` 50 → 46：纯并句。
- **`MEMORY_TYPE_SELECTION_POLICY` 一字未动**（178 token），
  `test_policy_keeps_the_route_schema_inside_its_measured_token_cost` 的
  643 上限仍然成立（现为 600）。

**C. 新增守卫（`tests/sdk_adapters/test_token_estimator_calibration.py`）**

`test_persona_and_route_schema_still_fit_the_8192_tier_megabyte_turn`：
把该场景峰值轮的**可变部分**（Host 收口指令 + 未收口因果组 + 夹具自己的能力
发现 schema）按实测值 `5261 − 2223 = 3038` 固定住，只断言两条车道真正会去改的
**固定文本**（PERSONA + 四个产品 schema）加上它仍 ≤ 5325。
本次缺陷的本质是「只有一条 ~10 s 的集成用例能看见这堵墙，而且要等合流之后」；
现在多加一句话会在**毫秒级**触发失败，并在断言信息里给出该压缩还是该重推的选项。
这也正是 `DECISION-TERMINATION-AND-PERSONA-ROUTES.md` §四遗留 3 要的东西。

### 附.4 复原后各档余量（实测峰值轮，`ratio=1.00`）

| 档位 | `effective` | 峰值 `planned` | 余量 | 结论 |
|---|---|---|---|---|
| 4096 | 2663 | 2723 | **−60** | 仍红（既有红，见 §5.1） |
| 8192 | 5325 | **5261** | **+64（1.2%）** | **绿：分页并完成** |
| 32768 | 25396 | 8554 | +16842（66%） | 绿 |

对照：修复前 8192 档为 `5418 / 5325 = −93`；
只有 N + d5c 时余量 **+10**。本轮把它抬到 **+64**，并加了守卫。

**4096 档的形态又变了一次，必须记录**：`26247ea8` 上它红在
`primary_context.prepare` 的硬校验（`protected_tokens > budget` 直接抛
`ContextBudgetExceeded`，连一次装配都到不了，`_plan_turn_messages` 的诊断日志
一行都没有）；压缩后 `prepare` 重新装得下（protected 2439 vs 2663，+224），
Run 因此能跑到装配器，改为在**第 2 次** wire 请求上安全停机
（`wire_count = 1`，而用例断言 3）。
**没有把断言改成 1**：那是把期望迁就现状，不是修好它。
要让 4096 档真的走到第 4 次装配，还需要约 640 token 的 protected 空间——
那是 4096 档预算本身的问题（tool schema 1304 + PERSONA 1135 已占 2439/2663），
不是措辞问题，仍挂在 F-ETR-4 / 本备忘 §5.1 名下。

### 附.5 验证

- 定向绿：`test_current_tool_megabyte.py`（`[8192]` `[32768]` 绿，`[4096]` 既有红）、
  `test_current_tool_pages.py` 3 绿、
  `test_token_estimator_calibration.py` 33 绿（含新守卫）、
  `test_context_route_tool.py` + `test_recall_selection_policy.py` +
  `test_p5s2_token_budget.py` + `test_token_budget_per_model.py` 合计 108 绿。
- 回归对照（同 venv，逐条 test id 比对基线 `26247ea8`）：
  `tests/sdk_adapters`（排除 `test_composition.py`）58 红 → 58 红，逐条相同；
  `tests/execution` 与基线相同（唯一差异是 megabyte `[4096]` 的失败**形态**，
  见 §附.4；该用例前后都红）。

### 附.6 Followup

- **F-TOK-4**：语义收口 system 指令（本场景 **711 token**）是 protected 分区里
  第二大的一块，其中 `material_events` 把同一个 effect 的
  `source_event_id`（`effect:effect-<64 hex>`，各约 20 token）写两遍
  （`host.file` + `harness.tool_invocation`）。它是 Host 自己生成的文本，
  压缩它不涉及任何模型契约用词，是 4096 档唯一还剩的大杠杆。属 S5b 收口车道。
- **F-TOK-5**：`primary_settled_effect_v1` 摘要固定带 1 KiB excerpt
  （本场景两条各 399 token）。按窗口档位缩放 excerpt 会**破坏**
  `verify_request` 的逐字节重算（校验端拿不到窗口），所以不能简单改；
  若要做，需要把窗口档位写进 descriptor 本身。

---

## 预算超限的降级顺序与 flash 校准（2026-09-09，Incident O）

- 分支：`worktree-token-budget-reconcile`，基线 `26247ea8`（含上一节的措辞压缩）。
- 证据：`.local-test-evidence/2026-09-09/native-a6-run6/primary-ui-0mv7ur1p/`
  的 `userdata/data/simple-harness-sdk/execution-v6.sqlite3` ——
  **17 条** `provider_invocations.request_json`（其中 **16 条**带 `usage_json`），
  全部 `deepseek-v4-flash`、窗口由 `model_overrides.toml` 钉在 32000
  （→ `budget_tier=8192`、`effective_input_budget=26752`）。
  按上一次的做法只把**字符类计数**落进
  `backend/tests/fixtures/hm_to_a6_flash_run6_samples.json`
  （`text_tokens` / `tool_schema_tokens` 都是纯字符类函数，计数与原文在它们之下逐 token 等价，证据正文不出仓）。
- 对照：`.local-test-evidence/2026-09-08/native-a6-run5/`（`deepseek-v4-pro`，117 组）。
- 用户授权「不问、自行裁定并记录」，本节即裁定记录。

### O.1 现象

HM-TO-A6 attempt 6 第 5 轮「在这个任务里，先找一下你有没有能读本地文件的工具。」
在第 6 次 `tool_search` 之后整轮 fail-close：

```
sdk_run_driver_failed error_type=ContextBudgetExceeded error_message=sdk_context_budget_exceeded
sdk_context_budget_exceeded planned=28519 effective=26752 protected=9782 tool_schemas=7249 groups=2 ratio=2.01
```

而 provider 对那一轮之前 6 次调用真实报的 `input_tokens` 是 7875 → 19491。
**估算 28519 对真实 19491，多估约 1.46×，然后把 Run 打死。**

这里有**两个独立缺陷**，必须分开修：

1. **倍率配错了型号**。`deepseek-v4-flash` 当时逐字沿用 `deepseek-v4-pro` 的三元组
   `min(1.35+0.11·ordinal, 2.5)`，第 7 次装配 `ordinal=6` → `ratio=2.01`。
2. **超限的处理是 fail-close，而不是降级**。日志自己写着 `groups=2`：
   请求里还留着 2 个因果组、还留着 3370 token 未分页的同 Run 工具回执，
   Host 明明有东西可以让，却选择了抛异常。

### O.2 裁定一：flash 用自己的证据校准，取 `min(1.25+0.35·ordinal, 1.65)`

`usage.input_tokens` 就是 provider 的 `prompt_tokens`，`cache_tokens` 是它的**子集**
（`sdk_adapters/provider.py::_sdk_provider_usage`：分别取 `prompt_tokens` 与
`prompt_tokens_details.cached_tokens`），所以直接用 `input_tokens ÷ Host wire 估算`。

**按轮次实测所需倍率（16 组，逐条最大值）**：

| ordinal | n | 实测最大所需 | 选定 `ratio` | 余量 |
|---|---|---|---|---|
| 0 | 6 | 1.118 | **1.25** | +11.8% |
| 1 | 5 | 1.436 | **1.60** | +11.4% |
| 2 | 2 | 1.503 | **1.65** | +9.8% |
| 3 | 2 | 1.449 | 1.65 | +13.9% |
| 4 | 1 | 1.422 | 1.65 | +16.0% |

**为什么不能沿用 pro**：两者的残差成分根本不同。
pro 是 thinking 模型，残差主体是中转站把上一轮 `reasoning_content` 加回 prompt，
这块**随轮次线性增长**（run5 实测单轮 `reasoning_tokens` 最高 19579、累计 57423），
所以它的 ratio 必须一路爬到 2.5。
flash 几乎不产 reasoning（run6 单轮最高 1813、累计仅 **1922**，差两个数量级），
残差只剩「JSON 工具回执比散文密」这一块，而这块随 JSON 占比升高会**饱和**——
实测第 2 轮之后就稳在 ~1.50，不再增长。沿用 pro 的 `0.11/turn` 就是把一条
**本 Run 不存在的隐形注入**一直计费到 2.5，这正是事故的直接成因。

**pro 的证据用不用**：只用它的 `ordinal=0`（`cum_reason==0`）那一档做**密度交叉校验**。
pro 在这 23 个请求上实测比值 中位 1.022 / 最高 1.148，flash 是 1.072 / 1.118，
同一档——说明 **tokenizer 密度确实共享**，`base` 的量级可以互证。
但随轮次增长的那一块是 per-endpoint 行为，**不共享**，
所以 pro 的高轮次样本**不并入** flash 的拟合。

**为什么不取网格搜索的最优解**：纯粹「零低估 + 中位多估最小」的最优是
`base=1.12 / per=0.32 / max=1.55`，但它在 ordinal 0 只留 0.2% 余量、
在饱和段只留 3.1%。这是 16 个点的拟合、不是定律，所以统一按「对每个 ordinal 的
实测上界留约 10%」定（与 `safety_margin` 同量级的工程余量）。

**16 组上的效果**（`ratio_max=1.65` 与旧的 2.5 相比）：

| | 零低估条数 | 估算/实测 中位 | 最大 | ordinal 6 的 ratio |
|---|---|---|---|---|
| 沿用 pro 三元组 | 16/16 | 1.224 | 1.333 | 2.01 |
| **本次 flash 三元组** | **16/16** | **1.162** | 1.433 | **1.65** |

注意浅轮次上旧三元组反而更"紧"（它从 1.35 起步，而 flash 只需要 1.12），
两者的分岔只发生在**深轮次**——也正是事故发生的地方：
`ordinal ≥ 3` 的样本上本次三元组逐条更紧，测试
`test_flash_calibration_is_tighter_than_the_one_it_replaces` 就是按这个口径断言的。

**复现事故那次装配**（用 fixture 回放 `46536e24-t4` 的 15 条消息 + 该轮自己的
`tool_search` 回执，且刻意取证据里**最大**的一条 `tool_search` 正文，比真实更难）：

| 三元组 | ratio(6) | planned | effective | 结论 |
|---|---|---|---|---|
| 沿用 pro | 2.01 | **28030** | 26752 | 抛 `ContextBudgetExceeded`（复现事故；`protected=9782`、`tool_schemas=7249` 与真机日志**逐字相同**） |
| **本次 flash** | **1.65** | **25229** | 26752 | **装得下，余量 1523（5.7%）**，且未裁一个组、未强制分页一条 |

### O.3 裁定二：超限改为**有序降级**，`ContextBudgetExceeded` 只在真的无路可走时抛

新的顺序（`sdk_adapters/context_authority.py::prepare_snapshot`）：

```
① 常规装配（有界分页 + 冻结裁剪，保留最后一个已闭合组） —— 装得下就结束
② 强制分页：把本 Run **所有**可分页的已结算工具回执换成
   primary_settled_effect_v1 摘要 + context_page_in 引用
   （不再只分页到 current_tool_allowance，也不再豁免最新一批）
③ 历史裁到 0：把剩下的已闭合因果组按最旧优先全部丢掉，只留 open 组
④ 仍不够 → 抛 ContextBudgetExceeded，并给出 protected 拆解
```

要点：

- **① 用「问」而不是「抛」**。`_plan_turn_messages` 新增
  `raise_on_overflow=False`，第一趟返回 `facts["budget_headroom"]`（负数即超限），
  不抛异常。否则一次失败的轮次会让观察者（终局投影、megabyte 夹具的计数器）
  看见同一个 Run "失败两次"；`ContextBudgetExceeded` 必须只表示**一件事**：
  已经没东西可让了。
- **②「最新一批也分页」是刻意的**。有界分页故意保护最新一批
  （那是模型正在处理的结果），但到了这一步，保护它的代价是关掉整个 Run。
  模型可以用 `context_page_in` 逐字取回，代价有界。
  `CONTROL_TOOLS`（`context_route` / `context_page_in` / `task_scope_search` /
  `task_scope_update`）**任何情况下都不分页**——它们是可执行的上下文/召回凭证；
  非 `succeeded` 的效果也仍然不分页，因为它根本没有可分页的公开正文，
  这半条规则不是策略选择。
- **③ 冻结口径没动**。`assemble_partitions` / `trim_causal_groups`
  （即 `metric-formulas.json` 钉死的那条 V0 预言机）**一行未改**，
  "裁到 0" 是 `_plan_turn_messages` 在它们之后、由调用方显式打开
  `allow_full_group_trim=True` 才走的一步。常规路径的行为与此前**完全一致**。
- **④ 的诊断**。`str(error)` 仍是 `sdk_context_budget_exceeded`（终局投影和
  megabyte 夹具都按它取值，不能改），拆解放在 `error.diagnostics` 与日志里：
  `planned / effective / protected / protected_messages / tool_schemas /
  open_group / groups`。
- **receipt 记录每一步降级**：`source_revisions` 新增
  `pages_forced`（本轮被强制分页的条数，未降级时为 0）、
  `groups_trimmed_for_budget`（因预算而非因帽子丢掉的组数）、
  `budget_headroom`（最终装配剩余额度）。
  `trimmed_groups` 语义不变（仍含 planner 自己的帽子丢弃），
  所以「历史被限流」和「历史被拿去换这一轮装得下」现在能分开读。
- **`primary_context.prepare` 不需要改顺序**：它是 Run 的第一轮，没有已结算的
  工具回执可分页，而它的 `while complete and over_cap(): complete.pop(0)`
  本来就把完整组裁到 0，抛异常的条件本来就是「protected 单独超预算」——
  已经符合本节要求。本轮只给它的抛点补上同样的拆解。

### O.4 裁定三：protected 成本——`_visible_provider_specs` 并没有多暴露工具

事故日志里 `tool_schemas=7249` 看着吓人，但拆开看 **7249 = 3606（wire）× 2.01（倍率）**，
**一多半是倍率、不是目录**。事故轮真实暴露的就是 12 个工具、3606 wire token：

| tokens | 工具 | | tokens | 工具 |
|---|---|---|---|---|
| 847 | `context_route` | | 228 | `todo_write` |
| 635 | `task_scope_update` | | 157 | `tool_describe` |
| 423 | `procedure_use` | | 153 | `prospective_ack` |
| 303 | `task_scope_search` | | 136 | `tool_activate` |
| 246 | `procedure_discover` | | 131 | `context_page_in` |
| 238 | `tool_search` | | 109 | `todo_complete` |

**结论：`_visible_provider_specs` 没有把该延迟的工具塞进 spec 列表。**
目录里已经带着 `tool_search` / `tool_describe` / `tool_activate` 三件套
（合计仅 531 token），其余可延迟的能力本来就在它们后面按需发现——
run6 那一轮连打 6 次 `tool_search` 正是这条链路在工作。
`_visible_provider_specs` 现有的收缩（未进入 `ROUTED_TASK` 前不暴露
PROJECT_EFFECT 工具）也仍然生效。**所以这一条没有可做的改动，只有结论**。

真正能压的是文本，上一节（§附.3）已经做掉，对同一条 wire 的实测收益：

| | run6 实际发出 | 本分支 | 收益 |
|---|---|---|---|
| system（PERSONA + Host 可信时钟） | 1260 | 1083 | **−177** |
| `context_route` schema | 847 | 765 | **−82** |
| `task_scope_search` schema | 303 | 253 | **−50** |
| **合计（wire）** | | | **−309** |

在 flash 饱和倍率 1.65 下，这 309 wire token 相当于 **−510 估算 token**。
`MEMORY_TYPE_SELECTION_POLICY`（178 token）按上一节的裁定**一字未动**：
它落在「模型选 `memory_types` 参数的那一刻」，比 PERSONA 更贴近现场，
`test_policy_keeps_the_route_schema_inside_its_measured_token_cost` 的上限仍成立。
被测试钉死的五个路由名与判别词全部在位（§附.3 A 已逐条列出）。

### O.5 各档余量

**megabyte 夹具**（型号未校准，`ratio=1.00`，与上一节同一组实测，本轮未改动其口径）：

| 档位 | `effective` | 峰值 `planned` | 余量 | 结论 |
|---|---|---|---|---|
| 4096 | 2663 | 2723 | **−60** | 仍红（既有红） |
| 8192 | 5325 | 5261 | +64（1.2%） | 绿 |
| 32768 | 25396 | 8554 | +16842（66%） | 绿 |

**flash 真机窗口 32000（tier 8192，`effective=26752`）**，回放事故那次装配：

| | planned | 余量 |
|---|---|---|
| 事故当时（沿用 pro，ratio 2.01） | 28519（回放 28030） | **−1767（−1278）** |
| 本次（flash 1.65） | **25229** | **+1523（5.7%）** |
| 本次 + §附.3 的文本压缩（生产实际值） | 24719 | **+2033（7.6%）** |

回放刻意取了证据里最大的一条 `tool_search` 正文，比真实那次更难；
真实第 7 次装配比回放还小约 490 token。
**并且**，即使某一轮真的再超了，现在也不会再打死 Run：
先强制分页（该轮两条 `tool_search` 正文 1629 + 1741 = 3370 wire token，
换成两条约 399 token 的摘要后让出 2572 wire ≈ **4244 估算 token**），
再把历史裁到 0（该轮 2 个组），最后才抛。

**4096 档为什么仍红、且现在是"证明过的红"**：新顺序下它先被问「装得下吗」，
答案是否；强制分页找不到任何可分页正文，历史裁剪找不到任何已闭合组；
于是抛出的拆解是
`protected=2439（PERSONA 1135 + tool schema 1304）+ open 组 284 = 2723 vs effective 2663`。
即该档在**第一个工具轮**上就差约 60 token 的 protected 空间，且**无路可让**。
用例断言 `wire_count == 3`（要跑到第 4 次装配）则还差约 640 token，
仍是 4096 档预算本身的问题，挂在 F-ETR-4 / §5.1 名下，**没有把断言迁就现状**。

### O.6 改点

| 文件 | 改动 |
|---|---|
| `backend/llm/model_info.py` | `deepseek-v4-flash` 独立三元组 `1.25 / 0.35 / 1.65` + 取值依据 |
| `backend/deskpet/sdk_adapters/context_partitions.py` | `ContextBudgetExceeded` 携带 `diagnostics`（`str()` 不变） |
| `backend/deskpet/sdk_adapters/context_authority.py` | `_plan_turn_messages` 新增 `allow_full_group_trim` / `raise_on_overflow`，facts 新增 `groups_trimmed_for_budget` / `budget_headroom`；`prepare_snapshot` 改为有序降级并记 `pages_forced` |
| `backend/deskpet/execution/current_tool_pages.py` | `CurrentToolProjector.__call__(..., force_all=)`：忽略 allowance、不豁免最新一批；控制类工具与非 succeeded 效果仍不分页 |
| `backend/deskpet/execution/primary_context.py` | `prepare` 的抛点补上同样的 protected 拆解 |
| `backend/tests/fixtures/hm_to_a6_flash_run6_samples.json` | 新增（run6 的 17 条请求，仅字符类计数） |
| `backend/tests/sdk_adapters/test_token_estimator_calibration.py` | 新增 6 条（flash 校准 3 + 事故回放 1 + 降级顺序 2） |
| `backend/tests/execution/test_current_tool_megabyte.py` | 4096 既有红的注释补上"已证明无路可让"的拆解 |

### O.7 验证

- 定向：**165 例收集，164 绿 / 1 既有红**（逐文件）——
  `test_token_estimator_calibration.py` 41（新增 6）、`test_context_partitions.py` 8、
  `test_context_preparation.py` 14、`test_context_authority_primitives.py` 5、
  `test_context_route_tool.py` 29、`test_recall_selection_policy.py` 15、
  `test_model_info.py` 11、`test_token_budget_per_model.py` 14、
  `test_p5s2_token_budget.py` 17、`test_current_tool_pages.py` 4、
  `test_primary_context_pages.py` 4，以上全绿；
  `test_current_tool_megabyte.py` 3 例中 `[8192]` `[32768]` 绿、
  `[4096]` **既有红**（形态见 §O.5）。
- 事故回放用例本身带对照：同一组消息在旧三元组下**必须**抛
  `ContextBudgetExceeded`，在新三元组下必须装得下——它证明的是修复，不只是绿。

### O.8 Followup

- **F-TOK-6（新，量化）**：`deepseek-v4-pro` 现行的
  `min(1.35+0.11·ordinal, 2.5)` 在 **run5 的 117 组新证据上会低估**——
  22/117 条低估，最差 `估算 22272 对真实 54683`（0.407×），
  且其中 **5 条**真实超 `effective_input_budget` 却被判为"装得下"。
  原因是 run5 有一条 Run（`6154747d49`）累计 `reasoning_tokens` 达 57423，
  远超当初 306 组证据的量级，所需倍率实测到 **5.25**。
  本轮**不改 pro**：一是本轮的主要矛盾是 flash 主链路，
  二是 pro 的正确解多半不是把 max 调到 5+（那会让浅轮次白白损失一半窗口），
  而是 **F-TOK-1 的闭环 usage 反馈**（用上一轮真实 `input_tokens` 做 floor，
  实测中位比值 0.993）。低估的后果与本次相反：不是 fail-close，
  而是请求真的超窗被 provider 拒——需要单独一轮处理。
- **F-TOK-7（新）**：强制分页目前只在装配超限时触发，且是"全分页"。
  更好的形态是按需分页到刚好装下（二分或按体积排序逐条分页），
  能少让出一些模型正在用的正文。本轮取"全分页"是因为它**可证明终止**、
  且此刻已经是最后一道防线，复杂度不值得。
- §附.6 的 **F-TOK-4 / F-TOK-5** 仍然有效（语义收口 system 指令的去重、
  `primary_settled_effect_v1` 摘要的 excerpt 按窗口档位缩放）。
