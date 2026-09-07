# 决策：原生 r10 步 5 `PrimaryHistoryDisclosureRejected` 根因与最小修复

> 独立子代理分析，主代理复核后执行。只读分析（未改代码、未运行原生应用）；数据库均复制到 scratchpad 后查询，原始证据未触碰。
> 对象：Host main 23b6c823（r10 bundle），Memory 0.6.25。日期 2026-09-07。

## 0. 结论（一句话）

不是 TaskScope / 冷重启 / `create_new` 历史依赖问题，而是 **Host 历史依赖读取器对"包装层参数拒绝"产生的失败效果零容忍**：`procedure_discover` 缺参被 `sdk_adapters/tools.py` 包装层在**进入处理器之前**以 `ToolResult.failed(value=None)` 拒绝，该效果照常被索引进 `primary_effect_identities`，下一次 provider attempt 前 `read_run_dependencies` 扫描该索引时把 `value=None` 判为 `scope_search_result_unverified`，整体包装成 `PrimaryHistoryDisclosureRejected` 杀掉整个 Run。与语料 run-01 C05-07（`task_scope_search` 缺 `query`）**完全同类**。产品缺陷，修读取器 6 行即可；`after` 改可选（main a95dced9）只消除本次触发点，不消除缺陷类。

## 1. 触发链（附文件:行）

### 1.1 本次运行的事实（第二进程 `primary-ui-_7uaiasu/native.log`，sdk_run_id `product-sdk-825ba4bd…`）

| 时间 (UTC) | 事件 | 证据 |
|---|---|---|
| 14:20:56 | 步 5 run.start；provider attempt #1 通过依赖校验 | native.log:151-157 |
| 14:22:29.9 | `context_route(create_new)` 效果成功落地 | Host `state.db` `primary_effect_identities` sequence=4 `context_route`；`context_route_decisions` 1 行 origin=`context_tool`, route=`create_new`；`task_scopes` 1 行「松柏九月」 |
| 14:22:30.1 → 14:23:04 | **provider attempt #2 成功**（message_count=10，含 route 结果） | native.log:165-170。此 attempt 前 `check_runtime_dependencies` 已经把 create_new 路由回执、冷重启后的 scope 状态全部校验通过 —— 这直接排除"冷重启 / create_new / TaskScope 绑定不可核验"假设 |
| 14:23:04.9 | 模型调 `procedure_discover {"query":"松柏记录"}`（无 `after`）→ SDK schema 校验失败 `tool_arguments.invalid` → 包装层 `tool_arguments.missing missing=after` → `tool_attempt.failed` | native.log:171-178；SDK `execution-v6.sqlite3` `execution_effects` effect-668903a8…：`state=failed`, `result_json={"error_code":"missing_required_argument","outcome":"failed","value":null,…}`, `arguments_json={"query":"松柏记录"}` |
| 同时 | 该失败效果**照常**写入 Host 索引 | `primary_effect_identities` sequence=5 `procedure_discover`（`tools.py:342` 在 `super().invoke` 之前调用 `primary_effect_index.record_effect`，`primary_effect_index.py:4-5` 注明"handler 进入前即记录"） |
| 14:23:05.14 | provider attempt #3 启动 → 15ms 后 `provider_attempt.failed` → `PrimaryHistoryDisclosureRejected` → `run.fail` | native.log:179-182；`main.py:7793` 每次 provider attempt 前调用 `check_runtime_dependencies` |

r10 bundle 的 schema 确为 `required:["query","after"]`（`git show a95dced9` 的 `-` 行），主干 a95dced9 才改为可选。

### 1.2 哪个校验抛错（静态追踪，`primary_dependencies.py`）

`check_runtime_dependencies`（:388）→ `read_run_dependencies`（:73）：

1. :79-90 找到 foreground run；:145-150 start 元数据与 `visibility_dependencies` 通过（attempt #2 已证明）。
2. :204-256 routes 循环：仅 sequence=4 的 `context_route`，attempt #2 已证明通过。
3. **:259-299 `primary_effect_identities` 扫描**：SQL :262 只挑 `task_scope_search / context_page_in / procedure_discover` 三个工具。对 sequence=5 行：
   - :276-277 `fact is None / not terminal / result is None` → 均不成立（失败 carrier 是一个存在的 `ToolResult`），不抛 `scope_search_effect_unverified`；
   - :278 `value = thaw_json(fact.result.value)` → **`None`**；
   - :279-293 仅对 `context_page_in` + `primary_page_hash_mismatch` 做独立重建，本行工具名不符，跳过；
   - :296 不是 str；**:298-299 `not isinstance(value, Mapping)` → `raise ValueError("scope_search_result_unverified")`**；
   - :300 `if "error" in value: continue`（"errors contain no candidate content"）永远走不到——包装层拒绝根本不是 Mapping。
4. :428-429 `except Exception` → `PrimaryHistoryDisclosureRejected(private_cause=exc)`，对外只剩 `default_message`（:16），`exc_info` 被脱敏，所以日志看不到稳定码。

因此：**不能核验的不是任何 history binding / recall / procedure draft binding / scope 依赖，而是一条"没有内容的失败效果"**。与前一步 `procedure_discover` 参数无效的失败调用是**直接因果**：attempt #2 与 #3 之间唯一新增的索引行就是它。

### 1.3 与 C05-07 是否同类：是

run-01 C05-07 `worker.log:274-284`：`tool_arguments.invalid tool=task_scope_search` → `tool_arguments.missing missing=query` → `tool_attempt.failed` → 下一 attempt `PrimaryHistoryDisclosureRejected`。无冷重启、无 create_new，共同因子只有"三个被索引工具之一的包装层缺参拒绝"。

反例证明边界（run-01 全量扫描）：C09-01、C09-04 各有 1 次 `tool_activate` 缺参，Run **存活**（`tool_activate` 不在 :262 的三工具集合）。run-02 未复现只是模型这次把参数填全了（非确定性），不是修复。

## 2. 定性：产品缺陷（两处），非测试设计问题

| # | 缺陷 | 状态 |
|---|---|---|
| A | `procedure_discovery.py` 首页游标 `after` 设为 required，模型自然省略 | 已修 main a95dced9（schema 可选 + `procedure_runtime.py:34` `setdefault("after","")` + 单测 1 项） |
| B | `primary_dependencies.py:259-299` 把三个被索引工具的**任何** `value=None` 失败 carrier 判为不可核验并杀 Run | **未修**，本文件给最小修复 |

B 为什么是缺陷而非安全设计：`tools.py:729-735` 的既定产品决策是"缺参从『杀 Run』变成『模型可见且可重试』"（gpt-5.6-luna 已多次因此打掉整个 Run）；包装层在 :788-806 于 `registration.handler` 调用（:822）**之前**返回失败，处理器从未执行、没有读取任何记忆内容，SDK 失败 carrier 也不携带 value（`simple_harness/tools/contracts.py:200-214` `failed()` 无 value 参数）。读取器把"没有来源"当"来源不可核验"，把产品决策在三个工具上又变回了杀 Run。修索引端（不记录被拒调用）不可取：`primary_dependencies.py:257-258` 与 `primary_effect_index.py:1-5` 要求每个实际身份都进索引且在 handler 前记录以抗崩溃。

残余（不在本次修复内，需另立证据后决定）：这三个工具的**处理器级**错误（`{"error":…}` 映射经 `tools.py:679-696` `_result` 转成 `ToolResult.failed(value=None)`，或处理器抛异常）走的是同一条 carrier，会触发同一处 :299。现有 :300 的 `"error" in value` 分支实际上已被 `_result` 架空。r10/C05-07 均未观察到该路径，本决策不扩大范围。

### 2.1 可复现的最小单测（红→绿）

文件：`backend/tests/memory/test_procedure_discovery_runtime.py`（现 127 行，复用 `session(tmp_path, provider, with_discovery=True)`、`DiscoveryProvider`、`bind_scope_root`）。新增 1 个参数化测试：

```
@pytest.mark.parametrize('bad_call', [
    ('procedure_discover', {}),                 # r10 形态：缺必填（a95dced9 后 after 已可选，故缺 query）
    ('task_scope_search', {}),                  # C05-07 形态
    ('procedure_discover', {'query': 5}),       # 类型违规 → invalid_tool_arguments 稳定码
])
```
Provider 子类：stage 1 **第一次**返回 `bad_call`；**第二次**断言最后一条 tool 消息为 `{"outcome":"failed","error_code":"missing_required_argument"|"invalid_tool_arguments",…}`（消息形状见 `current_tool_pages.py:87`），然后返回原来的合法 `procedure_discover {'query':'记录'}`，其余沿用现有 happy path。用现有 `guarded_invoke` 模式（:60-70）在每次 provider 调用前跑 `check_runtime_dependencies`，收集 `source_rejections`。
断言：`source_rejections == []`、`ctx.runtime.last_error is None`、`len(provider.requests)` 比现有 happy path 多 1、`record-1.txt/backup-1.txt` 写成、`provider.selected['memory_id']==ctx.memory_id`。
修前预期：`source_rejections == ['primary_history_disclosure_rejected']` 且 drain 后 `last_error` 非空（与 r10 / C05-07 同码）；修后全绿。无需模型、无需原生。

## 3. 唯一推荐的最小修复

**文件** `backend/deskpet/execution/primary_dependencies.py`，插入点在 :277（`scope_search_effect_unverified`）之后、:278 `value = thaw_json(...)` 之前，约 6 行：

```python
        if (fact.state.value == "failed" and fact.result.outcome.value == "failed"
                and fact.result.value is None
                and fact.result.error_code in {MISSING_ARGUMENT_ERROR_CODE, INVALID_ARGUMENTS_ERROR_CODE}):
            # Host wrapper (sdk_adapters/tools.py:788-822) rejected the call before
            # handler entry: the SDK failed carrier has no value and read no source.
            continue
```
两个常量从 `deskpet.sdk_adapters.tools` 做函数内局部导入（本文件风格；`tools.py` 顶层不导入本模块，无环）。

**控制条件（为什么够窄、够安全）**：四个条件缺一不可——SDK 效果状态 failed、结果 outcome failed、`value is None`、错误码限定为包装层两个稳定码（`tools.py:737-738`）。它们只匹配"处理器从未进入"的调用；任何携带 value 的失败、任何处理器级错误码、`rejected`/`unknown` outcome 仍按原逻辑 raise（保持 :279-293 对 `context_page_in` 的独立重建先例不变）。处理器是 Host 代码，声明同名错误码不在本读取器威胁模型内。

**不建议的替代方案**：(a) 泛化为"所有 `value=None` 失败都跳过"——扩大到未观察的处理器级路径，需要另立证据；(b) 在 `tools.py:342` 对被拒调用不记索引——破坏"每个身份都进索引、handler 前记录"的不变量。

**回归**：`uv run pytest backend/tests/memory/test_procedure_discovery_runtime.py backend/tests/execution/test_current_tool_pages.py backend/tests/execution/test_primary_context_pages.py backend/tests/memory/test_cognitive_typed_barrier.py`（含 `PrimaryHistoryDisclosureRejected` 的既有用例必须仍红在该红处：`test_procedure_discovery_runtime.py` 的 forget 分支仍应得到 `['primary_history_disclosure_rejected']`）。

## 4. r11 重跑脚本需要改什么

1. **只改 schema（a95dced9）不够。** 它消除的是本次触发点；缺陷 B 让三个工具上的任何一次参数失误都致命，而 luna 在 run-01 约 60 例里 3 例出现缺参。r11 必须先合入 §3 的读取器修复再构建，否则步 5 是掷硬币。
2. **重建 bundle**：Host 代码在 bundle 内，`.local-test-evidence/2026-09-07/native-build-r8/build.py` 按含 a95dced9 + §3 修复的新 sha 重建；installed 目标（H0.7.10 / M0.6.25 / S0.3.13）不动。注意 `after` 可选改变了工具 schema hash（`tools.py` 执行身份按 schema hash 冻结），旧 bundle 不能混用。
3. **必须用全新隔离 userdata 从步 1 重跑**，不能在 r10 的 `primary-ui-idyatccf/userdata` 上续跑：该库 `task_scopes` 已有「松柏九月」，步 5 会走 `resume_existing` 而非 `create_new`，且 `procedure_discover` 索引行已污染。步 4 仍需在冷重启后的第二进程执行（§3.4 设计不变）。
4. **脚本判定新增两条**（写进 `NATIVE-R11-*.md` 的 PASS/负控）：
   - `native.log` 步 5 段不得出现 `PrimaryHistoryDisclosureRejected`；
   - 若出现任何 `tool_arguments.missing` / `product_tool.invalid_arguments`（任一工具），下一条必须是 `provider_attempt.succeeded` 而非 `provider_attempt.failed`——这是缺陷 B 修复的现场验收信号；出现缺参本身记为提示词/schema 观察项，不判 FAIL。
5. 步 5 的既有预期证据（`procedure_uses` 1 行、`procedure_use_reservations` 2、`procedure_use_effects` 2、`operation-audit.db` `discover_procedure_drafts`→`read_procedure_use_target`、`record.txt`/`backup.txt` SHA 一致）不变。

## 5. 证据清单（本机只读实查）

- 第二进程日志 `.local-test-evidence/2026-09-07/native-23b6c823/primary-ui-_7uaiasu/native.log`:151-182
- Host `state.db`（复制后查询）：`primary_effect_identities` sdk_run `product-sdk-825ba4bd…` 仅 2 行（seq4 context_route、seq5 procedure_discover）；`context_route_decisions` 1 行 create_new；`task_scopes` 1 行「松柏九月」；`procedure_uses` 0 行
- SDK `simple-harness-sdk/execution-v6.sqlite3` `execution_effects` effect-668903a8…（state failed、value null、`missing_required_argument`、arguments `{"query":"松柏记录"}`）
- C05-07 `…/corpus-batch/run-01/C05-07/scoring/C05-07/worker.log`:274-284；run-01 全量扫描：C09-01/C09-04 `tool_activate` 缺参存活
- 代码：`primary_dependencies.py` :73-90, :204-256, :257-300, :388-429；`sdk_adapters/tools.py` :255, :342, :679-696, :729-822；`primary_effect_index.py` :1-42；`procedure_discovery.py` :4-6（HEAD）与 a95dced9 diff；`memory/procedure_runtime.py` :28-36；`main.py` :7791-7795；`simple_harness/tools/contracts.py` :200-214
