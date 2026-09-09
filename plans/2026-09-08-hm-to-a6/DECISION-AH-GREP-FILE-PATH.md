# 裁决 AH：`grep`/`glob` 必须接受文件路径，`tool_failed` 必须带得动原因

- 日期：2026-09-09
- 事件：AH（HM-TO-A6 第 12 次尝试 · turn 11，19:20）
- 证据：`.local-test-evidence/2026-09-09/native-a6-run12/primary-ui-z9j48osx/`
  —— `grep` ×4 `failed`，`error_code=tool_failed`、
  `public_message="Tool execution failed."`；`native.log` 只有
  `product_tool.failed tool=grep code=tool_failed`，**无 traceback**。
  参数形如 `{"context":2,"output_mode":"content",
  "path":"/Users/taiwan/SimpleHarnessWorkSpace/a6-fixture/qiufen-checklist-a.md",
  "pattern":"ANCHOR-ALPHA"}`
- 代码：`backend/deskpet/tools/code_tools/grep_tool.py`、
  `backend/deskpet/tools/code_tools/glob_tool.py`、
  `backend/deskpet/tools/code_tools/_search_scope.py`（新增）、
  `backend/deskpet/tools/code_tools/registration.py`、
  `backend/deskpet/sdk_adapters/tools.py`
- 测试：`backend/tests/sdk_adapters/test_grep_file_path_read_gate_ah.py`（新增 14 例）
- 前置：[DECISION-F-Z1-READ-TOOL-CALL-GATE](DECISION-F-Z1-READ-TOOL-CALL-GATE.md)、
  [DECISION-F-Z1B-READ-BINDING-PROPOSAL](DECISION-F-Z1B-READ-BINDING-PROPOSAL.md)、
  [DECISION-F-Z1C-READ-ORIGIN-STALE](DECISION-F-Z1C-READ-ORIGIN-STALE.md)

---

## 一、真因：**不是异常**，是一条守卫加一次信息蒸发

离线用真 handler、在读闸门投影根下复现，得到的就是事件里那四次的原样：

```
grep_tool({"context":2,"output_mode":"content","path":"<fixture>/qiufen-checklist-a.md",
           "pattern":"ANCHOR-ALPHA"})
→ {"error": "path is not a directory: <fixture>/qiufen-checklist-a.md"}
```

两段事实合起来才构成这次事故：

1. **守卫**：`grep_tool` / `glob_tool` 里唯一的目标判据是
   `root = Path(path).expanduser().resolve()` 之后 `if not root.is_dir(): return {"error": ...}`。
   目录是**唯一**合法目标，文件路径必拒。任务书里列的另外三个假设全部被排除：
   * 不是 `..` pattern 误伤 —— 参数里没有 `..`；
   * 不是相对/绝对 `execution_scope` 解析错 —— 传的是绝对路径；
   * 不是 F-Z1c `_ordered_root_hashes` 主根投影指向托管家目录 ——
     `read_target_violation` 对**显式 path** 返回的是
     `containing_root(value, roots)`，即「包含该路径的那个根」，与 `roots[0]` 无关；
     离线用真 `WorkspaceReadGate` 断言过投影根就是夹具根
     （`test_grep_on_a_file_path_is_admitted_and_searches_that_one_file`）。
   全程没有任何异常被抛出，所以「没有 traceback」不是日志丢了，是**本来就没有**。

2. **蒸发**：这个信封没有 `error_code`/`public_message`，
   `sdk_adapters/tools.py::_result` 因而走默认分支 →
   `ToolResult.failed(call_id, "tool_failed", "Tool execution failed.")`。
   而 `ToolResult.failed` **不携带 `value`**：handler payload 里那句
   "path is not a directory" 到不了模型；`product_tool.failed` 又只记 `code`，
   也到不了日志。模型两边都看不到拒因，四次重试后改用 `context_page_in`
   以 1 KiB 为单位硬读 40 KB 夹具，预算就此打光。

一句话：**目录-only 的守卫制造了失败，无码信封让这次失败变成不可归因的哑弹。**

## 二、裁决

### 2.1 目标可以是文件，也可以是目录（两个工具同一口径）

新增 `_search_scope.resolve_search_target`：解析（跟随符号链接）后按
`is_dir` / `is_file` 分流，其余情况给出不含路径的稳定码。

* `grep`：文件目标 → 该文件**就是**搜索集（`target_kind="file"`，`glob` 过滤退化为按
  文件名匹配）；目录目标 → 原样 `rglob` 遍历。
* `glob`：文件目标 → pattern 与该文件名匹配则返回它、否则**零结果**（不是错误，
  模型据此能区分「文件不在」与「文件在但不匹配」）；目录目标 → 原样遍历。

结果沿用既有的大结果分页：settled tool result 超过
`DEFAULT_LARGE_RESULT_BYTES` 即走 `primary_settled_effect_v1` + `context_page_in`
（`execution/current_tool_pages.py`），所以 handler 侧的 100 文件 / 250 行上限只约束
**扫描量**，不约束模型能否把回执读全；截断时另加 `next_action` 说明如何缩窄。

工具描述与 `path` 参数描述同步写明「目录走遍历、文件走单文件」——修好而模型不知道，
等于没修。

### 2.2 逐条结果复判包含性（F-Z1 遗留 3 就地结清）

`_search_scope.within_root` 与 `read_gate.path_within_root` 同语义（双侧
`resolve()` 后 `relative_to`，不可解析即判不在内）。`grep` 的候选文件与 `glob` 的
命中逐条复判，越根结果丢弃并计入 `escaped_results`。

CPython 3.12 实测的两条真实逃逸路径都堵上了：

| 形态 | `rglob` 行为 | 处置 |
|---|---|---|
| 根内**文件**链接指向根外 | `rglob('*')` / `rglob('**/*.md')` 直接产出它 | 丢弃，`escaped_results+1` |
| 根内**目录**链接指向根外 | `**` 不下钻，但显式 `escape/*.md` 会穿过去 | 丢弃，`escaped_results+1` |

闸门只校验入参 `path` 与 pattern 里的 `..`，这两条它都放行 —— 逐条复判是唯一补口。
这里选择在 `deskpet/tools/code_tools/` 内重写判据而不是 `import` 读闸门，是为了不让
`deskpet.tools` 反向依赖 `deskpet.sdk_adapters`（判据只有 8 行，且两侧各有用例钉住）。

### 2.3 稳定拒绝码（不含路径，可直接进日志）

沿用 `tools/file_tools._err` / `os_tools/edit_file._err` 已有约定：

| 码 | 触发 |
|---|---|
| `path_not_found` | `path` 指向的东西不存在 |
| `not_a_searchable_target` | 既不是普通文件也不是目录（socket / 设备 / 断链） |
| `path_unreadable` | 解析或打开时 `OSError`（带异常类名） |
| `pattern_required` | `pattern` 缺失或非字符串 |
| `pattern_invalid` | 正则编译失败（异常类名钉成 `re.error`，因为 3.12 的裸类名是 `error`） |
| `glob_invalid` | `glob` 不是字符串 |
| `search_root_missing` | 既没有 `path` 也没有投影根 |

`public_message` 一律「为什么 + 下一步」且不含路径，上界 512 字节。

### 2.4 `tool_failed` 不得再是一句空话

`_result` 的默认分支改为：拿不到 handler 自报的 `public_message` 时，用
「异常类名（handler 可选给 `error_type`）+ 净化后的自然语言」拼一条有界原因，
同时进 `public_message` 与 `product_tool.failed` 的 `reason=` 字段。

* 净化：先过仓库既有的 `redact_sensitive_text`（与
  `observability/log_redaction.redact_log_event` 同一套密钥/PII 规则），
  再把路径整体折叠成 `<path>`，最后压到 240 字节。
  「稳定码与日志字段永不含路径」这条既有口径不动
  （`tests/os_tools/test_file_tools_rejection_codes.py::test_codes_never_embed_a_path`）。
* 兜底：连一句话都拿不到时给
  `NO_REASON_FAILURE_MESSAGE`——明说「换一个参数再试一次，不要重复同一次调用」，
  而不是那句谁也无法据以自纠的 "Tool execution failed."。

这条是**全工具**生效的，不只 `grep`：任何返回无码错误信封的 handler 从此都能被归因。

## 三、验证

| 范围 | 结果 |
|---|---|
| `tests/sdk_adapters/test_grep_file_path_read_gate_ah.py`（新增） | 14 passed |
| `test_p4s22_glob_grep` + `test_p4s20_os_tools` + `tests/os_tools` + `test_execution_build_manifest` + `test_p4s22_todo_write` + `test_p4s22_web_search` + `test_search_gateway_production_wiring` + `test_wi7_clarify` + `test_computer_use_tool` | 140 passed / 1 skipped |
| `tests/sdk_adapters`（排除 `test_composition.py`） | 58 failed，与基线 `d1471525` 的一次性 worktree **逐行相同** |

新增用例用的是真装配：真 v45 `state.db`、真 `WorkspaceBindingAuthorityStore`、真路由
决定行、真 `WorkspaceReadGate`（复用 `test_read_tool_call_gate_f_z1.py` 的
`_build` / `_bound_scope` / `_record_route` / `_context`），覆盖：文件路径放行并投影出
包含它的那个根、目录路径照旧遍历、根外路径被闸门拒且 handler 拿不到根、两类符号链接
逃逸、七个稳定码、以及无码信封不再塌成 "Tool execution failed."。

`deskpet/tools/execution_build_manifest.json` 随源码变更用
`scripts/generate_execution_build_manifest.py --write` 重新生成。

## 四、遗留

1. **未跑原生旅程**（依约束不启动原生应用，本轮有旅程在跑）。真人复验就是
   HM-TO-A6 下一次 T11：`grep` 对 `a6-fixture/qiufen-checklist-a.md` 一次拿到
   `ANCHOR-ALPHA` 上下文，而不是 4 次 `tool_failed` + 分页硬读。
2. `deskpet/tool_catalog/real_tool_manifest.json` 是 cutover 前的**冻结快照**
   （`MANIFEST_SHA256` 钉死 + `schema_migrations.json`），本轮改的是活注册表的描述，
   没有动它；两者本就不做逐字比对。
3. `escaped_results` 目前只是回执里的一个计数，没有单独的审计行。要让「遍历时有结果
   被判越根」进 `host_pre_admission_audit`，需要 handler 侧拿到 Run/Call 身份，
   属独立改动。
4. `glob` 的文件目标按**文件名**匹配 pattern（`*.md` 与 `**/*.md` 都能匹配
   `x.md`）。带目录段的 pattern（如 `src/*.md`）对文件目标一律零结果 —— 语义清楚，
   但与目录目标的 `rglob` 语义不是同一套，模型若混用会拿到空列表而非拒绝。
5. 逐条复判给每个候选多加一次 `realpath`（macOS 上一次系统调用）。既有代码本来就对每个
   候选做 `is_file()` + `stat()`，量级同阶；正确性（这是一条边界）优先于这点开销，且
   100 文件 / 200 结果的上限本就约束着扫描量。真要优化可以按父目录缓存解析结果
   （`rglob` 每个目录产出多个文件），属独立改动。
