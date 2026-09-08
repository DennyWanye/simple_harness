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
