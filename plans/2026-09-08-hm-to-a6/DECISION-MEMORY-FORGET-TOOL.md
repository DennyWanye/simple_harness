# 事故 J 决策备忘：模型可见的 `memory_forget` 工具崩溃

> 日期：2026-09-08　范围：`HM-TO-A6` turn 23（真实 DeepSeek `deepseek-v4-pro`）
> 证据：`.local-test-evidence/2026-09-08/native-a6-b3682fe1/`
> （`primary-ui-xmqudtzt/userdata/data/simple-harness-sdk/execution-v6.sqlite3`、
> `primary-ui-hv9k7ncq/native.log`）

---

## 1. 现场

用户第 23 轮说「把「这套流程按那个 Python 环境执行」这条关系忘掉。」

模型做了 `tool_search` → `tool_describe` → `tool_activate`，随后连打 **18 次** `memory_forget`：

| 参数形状 | 次数 | 结算 | 模型看到的 |
|---|---|---|---|
| `{"query": "秋分资料整理这套校对流程…这条关系"}` | 6 | `tool_failed` | `Tool execution failed.` |
| `{}` | 12 | `tool_handler_failed` | `Tool execution failed.` |

Host 日志（`simple_harness.tools.registry`，06:00–06:02Z）：
`sdk_tool_handler_failed tool=memory_forget error_type=[REDACTED]=unclassified`。

Run 最终死于 `react_repeated_tool_exceeded`。

## 2. 真实异常与根因（离线复现确认）

离线用真实处理器闭包 + 真实依赖装配复现（`_dynamic_handlers(deps)["memory_forget"]`）：

```
ARGS {}                     -> RAISED KeyError   KeyError('fact_id')
ARGS {"query": "…"}         -> OK     {'ok': False, 'error': 'natural_language_forget_disabled'}
ARGS {"fact_id": "abc"}     -> RAISED ValueError invalid literal for int() with base 10: 'abc'
ARGS {"fact_id": None}      -> RAISED TypeError  int() argument must be … not 'NoneType'
```

三条互相独立的缺陷：

**J-1（崩溃）** `backend/deskpet/tool_catalog/providers.py:408`（修复前）
`int(arguments["fact_id"])` 直接下标取值。manifest 里 `memory_forget` 的 schema
**没有 `required`**，所以 `deskpet/sdk_adapters/tools.py` 的 Host 必填清单
（`_missing_required_arguments`）空转，SDK 的 schema 校验也放行 `{}`；
处理器于是抛 `KeyError('fact_id')`。
SDK 的处理器边界（`simple_harness/tools/registry.py:165-179`）刻意把任何 Host 异常压成
`ToolResult.failed(call_id, "tool_handler_failed", "Tool execution failed.")`——
这是正确的隐私边界，但意味着**处理器抛异常 = 模型零可行动信息**。

**J-2（静默拒绝）** 同文件（修复前）`return {"ok": False, "error": "natural_language_forget_disabled"}`。
产品包装层 `_result()`（`sdk_adapters/tools.py:666-696`）只在返回体带
`error_code` + `public_message` 时才把稳定码透给模型；只有 `error` 键时统一降级成
`tool_failed` / `Tool execution failed.`。所以「自然语言遗忘已禁用」这条**本来就想告诉模型的话，模型一个字也没看到**。

**J-3（契约本身不可用）** manifest 描述写着
「Prefer fact_id when known (via an earlier memory_facts_list call)」——
`memory_facts_list` 是 **UI 的 WebSocket 路由**（`backend/p4_ipc.py`），**不是工具**。
模型没有任何工具能拿到 `fact_id`：`memory_recall` / `memory_search` 返回的是
`message_id`（会话消息），只有模型自己在本轮调过 `memory_write` 才会拿到 `memory_id`。
本次事故的库里 `memory.db` 的 `facts` 表 **0 行**，`memory_forget` 在这条 journey 上
**根本不可能成功**。模型空手调用不是模型的错，是契约的错。

**J-4（诊断被吞）** `backend/deskpet/security/redaction.py:22`（修复前）
`_TOKEN = re.compile(r"\b(?:sk|tsk|key|Bearer)[-_ A-Za-z0-9]{12,}\b", re.I)`——
字符类里含**空格**，于是 SDK 渲染出的
`error_type=KeyError stable_code=unclassified` 中 `KeyError stable_code` 整段被当成密钥，
压成 `error_type=[REDACTED]=unclassified`。异常类型是无载荷的诊断信息，不该丢。
（`stable_code=unclassified` 本身是对的：`str(KeyError('fact_id'))` 是 `"'fact_id'"`，
不符合 SDK 的稳定码字母表。）

## 3. 决策

### D-1 本轮**不**把工具接进认知记忆抑制，走「确定性拒绝」分支

用户要忘的那条内容在 `human_memory_v7.db`（11 个 memory head、0 条 relation——
他指的那条关系压根没被抽取出来）。UI 的认知遗忘走
`primary_cognitive_controls.forget(memory_id, expected_revision, expected_content_hash)`
→ `CognitiveActionEvidenceStore` → Memory `SuppressionRequest`，是一次
**compare-and-set**：调用方必须先看到公开记忆视图的 revision 与 content hash。

模型侧没有任何工具暴露这个视图。要让 `memory_forget` 真能抑制认知记忆，必须新增
一条模型可见的记忆视图读面 + 同步 suppression receipt——这正是
`plans/2026-09-05-human-memory-s5c-preparation/SOURCES.md` 里 **S5B-AC-5 明确移交 S5c**
的工作项（"forget 在工具内**同步**走 Host `memory_action_authority` → Memory `suppress`，
suppression receipt durable 后工具才返回"）。在事故修复里顺手做完 S5c 是越界且高风险。

因此按任务书允许的第二条路：**确定性的工具级拒绝 + 稳定码 + 可行动文案**。
按 id 遗忘的既有能力（`memory_write` 写出来的 fact）原样保留并加固。

### D-2 自然语言遗忘保持禁用，但拒绝必须可行动

`plans/2026-05-23-memory-system-stage2/03-architect-review-round1.md` D-RISK-5 的结论
（自然语言 forget 是提示注入面，二次确认仍是同一个被注入的 LLM）依然成立。
**授权/抑制规则一条不改**：没有 `fact_id` 就不遗忘。改的只是拒绝的**呈现**——
从静默失败改成带稳定码 + 候选 id 列表 + 明确的「别再重试，让用户去记忆面板删」。

### D-3 拒绝文案里列出候选 id（`list_facts`）

列举用的是 UI facts 面板同一条 identity-safe 只读面
（`MemoryManager.list_facts(principal)`，personal scope），不放宽任何授权；
上限 20 条、每条 label ≤48 字符、整条消息 ≤2048 字节（与
`_MAX_HANDLER_PUBLIC_MESSAGE` 对齐）。列举失败一律降级成空列表，
绝不把存储异常升级成第二次崩溃。

注入面评估：文案里会回显 `key=value` 的短标签。这些内容本来就通过
`memory_recall` / 主上下文对同一个模型可见，且限定在同一 principal 的 personal scope、
每条截断到 48 字符、以「候选 id 列表」的形式呈现，不新增可被利用的注入面。

候选为空时（本次事故的真实情况）文案直接劝停：
> Do not call memory_forget again; tell the user you cannot remove it yourself and
> that they can delete it in the app's memory panel.

这条「劝停」是本次修复对 `react_repeated_tool_exceeded` 的直接对策。

### D-4 描述在构建期投影，不改写冻结 manifest

沿用 `tool_search` / `tool_describe` 的既有做法，在
`build_explicit_product_tool_catalog` 里投影 `memory_forget` 的 description 与
`fact_id` / `query` 的字段说明，指向真实可得的 `memory_write` 返回值，删掉不存在的
`memory_facts_list`。manifest 存档字节与 `manifest_sha256` 不动；
只有 `memory_forget` 自己的 per-tool execution identity 会变（设计上就是逐工具隔离的）。

### D-5 修 redaction 的跨词误伤，口径对齐权威规则文件

`resources/diagnostic-redaction.json` 里**已经**是拆开的正确两条：

```
"(?i)\\b(?:sk|tsk|key)[-_A-Za-z0-9]{12,}\\b"
"(?i)\\bBearer\\s+[-._~+/A-Za-z0-9=]{12,}\\b"
```

而 `observability/log_redaction.py` 用的是 `TraceRedactor()` 默认值（代码里那条陈旧的、
含空格的单模式）——`from_file` 在生产代码里从未被调用。把代码默认值对齐到这份权威规则即可：
真实凭据不含空白，覆盖面不变；`Bearer` 后的空白由独立一条保留。
`from_file` 的 fail-closed 语义不动。

## 4. 改动

| 文件 | 改动 |
|---|---|
| `backend/deskpet/tool_catalog/providers.py` | 5 个稳定码常量 + 3 个模块级纯函数（`_memory_forget_fact_id` / `_memory_forget_candidate_label` / `_memory_forget_rejection`）+ `_memory_forget_candidates`；重写 `memory_forget` 处理器；构建期投影描述与字段说明 |
| `backend/deskpet/security/redaction.py` | `_TOKEN` 去掉空格并拆出 `_BEARER`，默认 `value_patterns` 变两条 |
| `backend/tests/sdk_adapters/test_memory_forget_tool_rejections.py` | 新增，18 例 |
| `backend/tests/test_log_redaction.py` | 新增 2 例（诊断行保留 / 真密钥仍被删） |

修复后的稳定码（全部落在 `_SAFE_HANDLER_ERROR_CODE` 字母表内，无路径/栈帧/密钥）：

| 稳定码 | 触发 |
|---|---|
| `memory_forget_target_required` | 没给 `fact_id`（含 `{}`、`fact_id: null`、只有 `deskpet_public_progress`） |
| `memory_forget_natural_language_disabled` | 只给了非空 `query` |
| `memory_forget_invalid_fact_id` | `fact_id` 无法收敛成整数（字符串/布尔/小数/数组） |
| `memory_forget_unknown_fact_id` | id 合法但不是该 principal 的活跃记忆 |
| `memory_forget_store_unavailable` | Memory 侧抛异常（所有权/幂等冲突、存储故障），只记异常类型名 |
| `memory_forget_identity_unavailable` | Host 不变量失败（session/run/call 身份或 principal 解析不出），仍然什么都不遗忘 |

行为变更一处需要留意：`forget_fact` 返回假值时，旧实现回
`{"ok": True, "forgotten": False, "receipt": "already_forgotten"}`——
一个让模型误以为成功的假回执。`source_event_id` 由 `root_run_id/call_id` 派生、每次调用唯一，
replay 只会发生在同一 call 的重投，所以假值在实践中就是「这个 id 不存在」。
现在改为 `memory_forget_unknown_fact_id` 拒绝。全仓无第二处引用 `already_forgotten`。

## 5. 测试

`pytest -q -p no:randomly`（`backend/`，`PYTHONPATH=.`）：

| 批次 | 结果 |
|---|---|
| 两个改动文件的套件 + 全部 importer（新增 18 例 + `test_log_redaction` + `sdk_adapters/test_tool_catalog` + `sdk_adapters/test_official_memory_product_integration` + `test_execution_build_manifest` + `test_tool_capability_hydration` + `test_security` + `test_workflow_trace_store` + `test_workflow_retention` + `test_log_redaction_exception_summary` + `test_b_routing_and_receipt` + `sdk_adapters/test_product_host_ports` + `capabilities/test_resource_scope_resolvers` + `test_partition_dispatch` + `test_memory_g4_flag_matrix` + `companion/test_candidate_draft_receipts` + `memory/test_analysis_output_budget`） | **205 passed, 1 failed** |
| `test_sdk_observability_host` / `test_relay_provider_errors` / `test_deskpet_llm_adapters` | 41 passed |

唯一失败 `test_final_candidate_rejects_every_superseded_wheel_hash`（SDK wheel SHA-256 钉死值
对不上当前安装的 0.7.10）与本次改动无关：`git stash` 掉两个源文件后**同样失败**，已实测确认。

`tests/sdk_adapters/test_composition.py` 在本 worktree 里整片失败/挂起，原因是
`verify_sdk_candidate` 的 `SDK candidate installed origin mismatch`——已安装的
`simple-harness-sdk` 的 `direct_url.json` 指向主 checkout 的 wheel，而不是本 worktree 的
`backend/vendor/…`。同样经 `git stash` 实测确认为环境性、与本次改动无关。

## 6. 不变量（自审逐条核对）

- 授权入参逐字未变：`forget_fact(fact_id, reason="", principal=<trusted>, source_event_id="explicit-memory-action/v1/{root_run_id}/{call_id}", payload_hash=None)`。
- `trusted_principal` / `trusted_memory_execution` 未改；身份不可解析时仍然**什么都不遗忘**，只是失败从崩溃变成 `memory_forget_identity_unavailable` 拒绝。
- 自然语言遗忘仍然**不**执行任何遗忘。
- 遗忘只针对记忆（CLAUDE.md 用户产品决定 2）：处理器只碰 `memory_manager.forget_fact`，不触碰任何会话/历史面；测试断言成功路径上 `list_facts` / `read_fact` / `remember_fact` 均未被调用。
- redaction 只收窄了「跨空白吞词」这一种误伤，真实密钥形状（`sk-…` / `key_…` / `Bearer <token>`）覆盖不变，`from_file` fail-closed 不变。
