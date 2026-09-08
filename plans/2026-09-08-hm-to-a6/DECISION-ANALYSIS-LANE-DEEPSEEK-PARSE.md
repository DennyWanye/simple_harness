# 决定记录：记忆分析车道在 DeepSeek 上的 `ProviderProtocolError` 间歇失败（2026-09-08）

> 义务：`HM-TO-A6` 原生真实模型验收（`plans/2026-09-08-hm-to-a6/00-PLAN.md`）
> 症状：主对话车道正常，**记忆分析车道**（证据 → 结构化记忆提案）在 `deepseek-v4-pro` 上
> 间歇以 HTTP 200 + `ProviderProtocolError` 失败，用户事实没有落成记忆头。
> 结论：**Provider 序列化缺陷**（模型多吐一个 `}`），Host 侧做极窄规范化后修复；
> 同时补上**无载荷**的解析失败诊断字段。

---

## 1. 现象与证据

### 1.1 日志

`.local-test-evidence/2026-09-08/native-a6-7cec5249/primary-ui-cw3ifsxo/native.log`
（时间戳 UTC，对应本地 10:57–10:59）出现 6 次：

```
product_provider_http_response_received ... status_code=200 response_bytes=2342..7691
  payload_mapping=True choices_kind=list choice_count=1 message_mapping=True
  content_kind=str tool_calls_kind=list tool_call_count=1 usage_mapping=True
product_provider_response_parse_failed  ... error_type=ProviderProtocolError
product_provider_attempt_failed         ... stage=response_protocol retryable=False
```

把 6 个失败的 `request_ref` 反查 `product_provider_attempt_started`，**全部**是
`message_count=2 / tool_count=1 / tool_schema_bytes=6191 / max_output_tokens=6144`
——即分析车道（`HostMemoryAnalysisExecutor.build_request`），不是主对话车道。
同一日志中分析车道共 10 次 Provider 调用（`tool_schema_bytes=6191`），**失败率 6/10**。
另一份 `.local-test-evidence/2026-09-08/native-7cec5249/primary-ui-qcewdx7j/native.log`
为 2/4。

### 1.2 账本

`userdata/data/human_memory_v7.db`：`analysis_batches` 共 10 行，`state=failed` 7 行
（attempt 1→3 逐次重试后 dead_letter），`applied` 3 行。
后果在 `semantic_claims` 上直接可见——整轮 A6 只落了 **1 条**语义 claim：

```
subject_entity = user:self
predicate      = proofreading_python_version
object_json    = "Python 3.12"
```

第 2 轮的用户事实「我的校对结果一律存到「外接硬盘 / 校对归档」」**没有**落成记忆头。

### 1.3 请求原样重放

Host 在 `state.db.human_memory_evidence` 里持久化了每次分析尝试的 durable 输入信封
`analysis-attempt-input-*`（`sanitized_payload.provider_request` = 完整 `ProviderRequest`）。
取失败批次 `analysis-batch-40b8a53d…`（证据 `22280792-…`，即第 2 轮那条事实）的信封
`analysis-attempt-input-11435f90…`，按 `_ProductOpenAICompatibleProvider._request_payload`
的线格式（`tools[].function.strict=false`、`max_tokens=6144`、无 `temperature`、无 `tool_choice`）
原样重放到 `https://api.deepseek.com/v1/chat/completions`。

**39 次真实调用**（4 批），用**安装版 SDK 解析器**
（`simple_harness/providers/openai_compatible.py::_parse_response`，与
`.local-test-evidence/2026-09-07/installed-h0710-m0626-s0313/` 内的副本逐字节相同）判定：

| 批次 | 调用数 | 基线解析失败 |
|---|---|---|
| A | 5 | 0 |
| B | 12 | 5 |
| C | 10 | 3 |
| D | 12 | 4 |
| **合计** | **39** | **12（≈31%）** |

与实跑观测的 6/10 同一量级，**缺陷可稳定复现**。

---

## 2. 根因：模型多吐一个 `}`，SDK 解析器整包 fail-closed

失败的**确切检查**是 `_parse_tool_calls` 里的：

```python
if isinstance(arguments, str):
    try:
        arguments = json.loads(arguments)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ProviderProtocolError() from None     # ← 这一行
```

`tool_calls[0].function.arguments` 是**字符串**，且**不是**合法 JSON。实测两种形态，
本质同一个缺陷——模型多序列化了一个 `}`：

**形态一 `trailing_delimiter`（11/12）**——完整合法对象后面多一个 `}`：

```
json.JSONDecodeError: Extra data: line 1 column 410 (char 409)   # len(arguments)==410
…"object_value": "外接硬盘 / 校对归档"}}]}}
                                          ↑ 多出来的这个
```

**形态二 `early_object_close`（1/12）**——根对象提前一个 `}` 收尾，随后继续：

```
len=465, raw_decode 前缀止于 422，剩余 = ', "closure_reason": "记录用户关于校对结果存储目录的稳定偏好。"}'
…}}]}, "closure_reason": "…"}
     ↑ 这个 `}` 位置错了
```

**排除截断**：所有失败样本 `finish_reason="tool_calls"`（不是 `length`），
`completion_tokens` 约 540–1250，而 `analysis_batches.request_json.budget.max_output_tokens=6144`。
`arguments` 长度 380–489 字节，内容语义完整（`exact_quote` 逐字正确）。
因此**不是** `max_output_tokens` 不够，不需要调预算。

**为什么只打到分析车道**：分析车道是唯一强制模型必须发工具调用、且入参是一整个
嵌套 JSON 提案（`memory_analysis_proposal`，schema 6191 字节、`anyOf` 5 分支）的车道；
主对话车道多数轮次 `tool_calls` 为空或入参很浅，踩不到这个序列化缺陷。

**为什么后果这么重**：`ProviderProtocolError` 在 Host 的失败分级里是
`stage=response_protocol / retryable=False`（`_provider_failure_stage`），
一个 HTTP 200、内容完全可用的响应被整包丢弃；Memory 的有界重试 3 次后进 dead_letter，
该轮用户事实**永久**不进记忆。

---

## 3. 决定与实现

### 3.1 决定

在 Host 适配层 `_ProductOpenAICompatibleProvider` 中，**在交给 SDK 解析器之前**
删除这一个多余的 `}`，且仅此一种畸形。理由：

1. 这是可判定的 Provider 序列化缺陷，不是模型语义错误——修复后的入参与模型意图逐字一致；
2. 修复只在 `json.loads` **已经失败**的入参上尝试，合法响应走**恒等透传**，行为零变化；
3. 不改 SDK（安装版冻结），不放宽 SDK 的任何契约；
4. 不动 `max_output_tokens`（已证明不是截断），不加新的重试策略（重试也只是重掷 31% 的骰子）。

### 3.2 改动

`backend/deskpet/sdk_adapters/provider.py`

* `_repaired_tool_arguments(raw) -> (text, reason) | None`
  只尝试**两个确定位置**，都由字符串实际解出的 JSON 前缀推导，且都**信息保全**：
  - `trailing_delimiter`：前缀是完整 JSON **对象**，其后只有重复闭合分隔符/空白 → 取前缀
    （该字符集无法编码任何值——能起一个值的字符全被排除，所以丢弃它不丢信息）；
  - `early_object_close`：删除前缀末尾那个 `}` 后，**整串**必须解析成一个 JSON 对象，
    **且已解出前缀的每个键都必须逐值原样保留**（否则重复键会静默改写提案，见 §5 P0）。
  其余（截断 JSON、尾随正文、尾随第二个独立值、顶层非对象）一律返回 `None`，保持 fail-closed。
* `_ProductOpenAICompatibleProvider._normalized_tool_arguments(payload, request_ref)`
  只看 `choices[0]`（SDK 解析器也只消费它）；对每个 `arguments` 先 `json.loads`，
  **成功就跳过**；只有失败且可修时才复制出新的 `dict`（不就地改调用方对象），
  并打一行 `product_provider_tool_arguments_repaired`（只含 `request_ref`、
  `call_index`、两个整数长度、有界 `reason` 枚举）。无任何修复时**返回原对象**。
* `_parse_failure_diagnostic(payload, *, provider_request_id_header)`：按 SDK 解析器
  （含其构造函数内的检查：`CallId`、`validate_json_value`、`ProviderUsage`）的**同一顺序**
  逐项复核，给出 `check`（有界枚举，如 `tool_call_arguments_not_json` /
  `tool_call_id_not_identifier` / `tool_call_arguments_json_value_invalid` /
  `usage_values_rejected` / `content_not_string`）以及 `finish_reason`
  （复用 `_safe_finish_reason` 白名单）、`completion_tokens`、`arguments_length`、
  `json_error_kind`（`JSONDecodeError.msg` 模板 slug，正则剥到只剩 `a-z_` 并截断 48 字符）、
  `json_error_position`、`call_index`。
  **只有形状、有界枚举和整数**——不含正文、入参文本、工具调用 id、模型输出。
* `product_provider_response_parse_failed` 追加 `diagnostic=<json>`
  （`ensure_ascii=True` + `json.dumps`，与既有 `product_provider_http_rejected` 同规格，
  杜绝换行/控制字符注入日志行）。规范化与诊断**各自包在 `try` 里**：任何意外异常都不得
  取代 SDK 的原始 Provider 错误，也不得让错误绕过日志逸出。

### 3.3 修复效果（真实 API A/B）

同一 durable 请求，同一进程内对每个响应同时跑**基线 SDK 解析器**与**打补丁后的 Host 解析器**：

| | 调用数 | 基线失败 | 打补丁后失败 |
|---|---|---|---|
| 全部 4 批 | 39 | 12（31%） | **0** |

其中 12 个畸形样本：11 个 `trailing_delimiter`、1 个 `early_object_close`；
修复后 `tool_calls[0].arguments` 的 `outcome` / `operations[0].exact_quote` 与模型意图逐字一致。

---

## 4. 测试

新增 `backend/tests/sdk_adapters/test_provider_tool_arguments_repair.py`（33 个用例）：

* 实测两种畸形形态都解析成功，且 `exact_quote` / `closure_reason` 逐字保真，
  `finish_reason` 与 `reasoning_content` 元数据不受影响；
* **合法响应恒等透传**：`_normalized_tool_arguments(payload) is payload`；
  修复路径**写时复制**：调用方持有的 dict 仍是原始畸形串；
* fail-closed：截断 JSON、尾随第二个独立对象、尾随正文、拼接后仍不合法、顶层非对象、空串、
  **重复键拼接**（会反转 `outcome` / 清空 `operations`）一律不修，仍抛 `ProviderProtocolError`；
* **隐私断言**：两条新日志（解析失败 + 入参修复）整行都不得出现证据正文（`我的校对结果…`）、
  入参正文（`外接硬盘`）、谓词（`proofreading`）、工具调用 id；诊断字段值逐项断言；
* 诊断分类覆盖 `choices_not_list_or_empty` / `message_not_mapping` / `content_not_string` /
  `tool_call_arguments_not_object` / `tool_call_id_not_identifier` /
  `tool_call_arguments_json_value_invalid` / `usage_values_rejected` / `id_not_string`
  （含「有 `x-request-id` 头时不是失败」的负控）/ `unclassified`；
* 诊断与规范化对畸形输入不得自身抛异常或吞掉 SDK 原始错误
  （深嵌套入参 `RecursionError` 用例断言 `ProviderProtocolError` 之外的原始异常
  仍逸出、且两条 warning 都留下了）。

执行结果（`backend/.venv`，`PYTHONPATH=<worktree>/backend`）：

| 范围 | 结果 |
|---|---|
| `tests/sdk_adapters/test_provider_tool_arguments_repair.py` | 33 passed |
| `tests/sdk_adapters/` 中 Provider 适配相关 8 个文件（含 `test_provider_tool_call_continuation`、`test_provider_rejection_diagnostic`、`test_post_turn_invoker`、`test_context_route_nonstrict_wire`、`test_primary_provider_preflight`、`test_provider_projection_pump`、`test_provider_timeout_is_a_safety_net`） | 68 passed |
| `tests/memory/` 分析车道 7 个文件 | 41 passed / 5 failed |
| 已捕获的 39 个真实响应逐个跑打补丁后的解析器 | 39 ok / 0 fail |

那 5 个 failed（`test_analysis_episode_time.py` 4 个 + `test_analysis_proposal_v5.py`
`test_wire_schema_excludes_other_bodies_and_keeps_old_protocols`）为**既有红**：
把本次改动 `git stash` 后在同一基线上复跑，结果完全相同（5 failed / 3 passed），
与本决定无关（`test_analysis_proposal_v5` 断言 `PROMPT_VERSION == v5.1`，
而当前协议已升到 v6）。

---

## 5. 独立评审

由独立评审代理以严格评审者身份复审本次 diff（轴：隐私/日志注入、合法响应零行为变化、
过度修复风险、诊断对 SDK 检查顺序的保真度、测试质量）。

**首轮结论：FIX-THEN-SHIP**，1 个 P0 + 4 个 P1 + 5 个 P2。全部已修：

**P0 — `early_object_close` 可能被重复键静默改写提案（已修）。**
原实现对拼接结果只要求「整串解析成一个 dict」。JSON 后键覆盖前键，因此
`{"outcome":"mutate","operations":[…]}, "outcome":"no_mutation"}` 会被拼成
`{"outcome":"no_mutation", …}`、`…, "operations": []}` 会被拼成空操作，
两者都是 schema 合法值；`analysis_proposal.py` 直接读 `outcome`/`operations`，
Host 侧不再校验入参 schema → 「响亮的失败」变成「错误的成功」，恰好丢掉本修复要救的那份事实。
**修法**：拼接结果必须**逐键包含已解出前缀的全部键值**：

```python
if isinstance(spliced, dict) and all(
    key in spliced and spliced[key] == item for key, item in value.items()
):
```

实测 `early_object_close` 样本仍被修复（`closure_reason` 完整），两个反转样本改为 fail-closed，
已加 `test_duplicate_key_splice_is_refused`（含「无守卫时朴素拼接确实会改写语义」的反证断言）。

**P1-1 诊断只镜像了 SDK 三分之二的检查（已修）**：`CallId`（1–255 可打印 ASCII）、
`validate_json_value`（`json.loads` 接受 `NaN`/`Infinity`，SDK 契约不接受）、
`ProviderUsage.__post_init__`（负数、`total < input+output`、非整数 cache/reasoning）
三类拒绝原本都落到 `unclassified`。已补 `tool_call_id_not_identifier` /
`tool_call_arguments_json_value_invalid` / `usage_values_rejected`，
并加 5 个用例断言「SDK 确实抛 `ProviderProtocolError` 且诊断给出该码」。

**P1-2 诊断/规范化未加护栏（已修）**：同文件的 `_DiagnosticPostClient.post` 明确写着
「Diagnostics must never replace the original Provider error」，新代码却没照做。
其中规范化跑在 `try` **之前**，深嵌套入参的 `RecursionError`（不是 `ValueError`）
会绕过所有日志逸出——比改动前更差。已把规范化包进 `try`（异常时原样返回 payload 并记
`product_provider_tool_arguments_normalization_unavailable`），诊断包进 `try`
（异常时退化为 `{"check":"diagnostic_unavailable"}`），并加
`test_deeply_nested_arguments_still_fail_with_the_sdk_error` 钉死。

**P1-3 / P1-4 测试缺口（已修）**：新增 `test_repair_log_is_payload_free`
（修复日志本身的隐私属性）、`test_repair_never_mutates_the_callers_payload`
（修复路径的写时复制），以及上述重复键用例。

**P2 修正**：诊断的 usage 检查顺序改为与 SDK 一致（usage 在 model/finish_reason 之前）；
`id_not_string` 误报修正——SDK 只在没有 `x-request-id` 响应头时才回落到 `payload["id"]`，
诊断新增 `provider_request_id_header` 形参（`_parse_response` 传入真实响应头）并加用例；
`_repaired_tool_arguments` 与 `_json_error_kind` 的 docstring 按实情改写
（尾部容忍的是「重复闭合分隔符」而非严格一个 `}`，并说明该字符集无法编码任何值，
所以丢弃它是信息保全的；纯 Python `json` 回退分支的两个模板会嵌入一个 `repr` 字符，
已由 `[^a-z]→_` 过滤掉）；`assert "proofreading" not in line` 原本是死断言
（该词不在 200 字节截断内），改为尾部截断并先断言三段敏感文本确实在载荷里。

**评审判定为"干净"的轴**：隐私（两条日志只有 `_opaque_ref` 十六进制、有界枚举、整数；
`json.dumps(ensure_ascii=True)` 使日志注入结构上不可能）、合法响应零行为变化
（非修复路径全部返回**原对象**，修复路径纯写时复制，`reasoning_content` 提取不受影响，
happy path 仅多一次 `json.loads`）、失败闭合参数化用例真实有效。

修完后复跑：新测试 33 通过，Provider 适配相关 8 个文件 68 通过。**最终判定：SHIP。**

---

## 6. 仍属模型侧、本次不修的部分

1. **缺陷本身在 DeepSeek 侧**：`deepseek-v4-pro` 在这个 6191 字节、`anyOf` 5 分支的
   工具 schema 上，约 31% 的调用会多序列化一个 `}`。Host 只是把它规范化掉；
   若 DeepSeek 修复了序列化，本改动自动变成 no-op（合法响应恒等透传）。
2. **多吐两个及以上、或位置更离谱的 `}`**：不修，继续 fail-closed，
   现在会在日志里留下 `check=tool_call_arguments_not_json` +
   `json_error_kind` + `arguments_length` + `json_error_position`，下次可直接定位。
3. **真正的截断**（`finish_reason=length`）：不在本次范围。既有的
   `memory.analysis_response_unusable` 审计（`analysis_executor.py`）仍是那条路径的信号。
4. **`retryable=False` 的分级**：未改。协议错误不重试是正确的默认；本缺陷不该靠重试解决。

---

## 7. A6 实跑是否需要重跑

**需要重跑**。已失败的 7 个 `analysis_batches` 行是 durable 终态
（`state=failed`，attempt 已到 3 → dead_letter），Host/Memory 都不会再为它们发起分析；
本次修复只影响**新**的 Provider 响应解析，不会追溯地把旧证据补成记忆头。
因此 A6 的 HM-AC-2 / HM-AC-6 判定必须在带本修复的构建上**重新跑一遍**，
才能拿到第 2 轮及其余失败轮次的记忆头与 relation 证据。

---

## 附：复现与验证脚本

（scratch，未入库）
`/private/tmp/claude-501/.../scratchpad/analysis-diag/`：
`repro2.py`（durable 信封原样重放 + 安装版 SDK 解析器判定）、
`repro3.py`（同一响应上基线 vs 打补丁 A/B）、
`verify_fix.py`（对 39 个已捕获响应逐个跑打补丁后的解析器）。
凭据从 `.local-test-evidence/2026-09-07/credentials/deepseek.env` 读取，
脚本与本文档均不含密钥。
