# Human Memory 后续任务

更新：2026-09-06。

## F01：发布成功事件的实际来源接入与端到端验收

状态：用户明确延期，下次处理。本次不再推进，不计为通过，也不阻挡其他当前交付。

用户决定：“那就暂时不处理这个，允许你记为一个followup，下次再处理”。对应的是原计划“发布成功后提醒我更新变更日志”所缺的实际发布平台/项目来源。

当前项目没有已配置的项目发布操作或目标；不能把模型终答、Run完成、本地工具目录更新或模拟回执当作发布成功。事件协议/存储部分的已实现源码与有限测试按各自证据保留，不能替代真实发布闭环。

下次处理：

1. 确定实际发布平台及项目，接入真实确认读取来源。
2. 完成注册ACK与事件顺序的生产绑定，保留重放、失效和未确证状态。
3. 通过真实模型/产品流程验证：注册→实际发布确认→唯一occurrence→下一轮提醒→ACK→settle。

时间提醒、其他已授权功能、原生长对话及240条质量评测继续处理。此条是待办记录，没有建立定时通知或自动发布操作。

## F02 聊天区渲染原始工具回执 JSON（2026-09-07 用户决定：后续优化，本次不处理）

原生 r3（`plans/2026-09-07-native-main-journey/NATIVE-R1-R3.md`）中，context_route 成功后聊天区把完整回执 JSON（约 1400 字符）作为「工具」消息渲染。建议后续改为折叠摘要（route、召回条数、片段标题），原始 JSON 放详情。

## F03 召回为空后模型循环重提同一路由（2026-09-07 用户决定：后续优化，本次不处理）

遗忘后再问同一问题，模型连续 5 次提出相同的 memory_standalone 提案；语料 C01-10 修复前同样 7 次循环。auto 模式取消授权提示后不再需要人工逐次点击，但仍浪费 Provider 调用。建议 Host 对同一 Run 内重复且结果为空的 route 提案返回明确"无相关记忆"的工具结果并设上限。

## F04 同一轮多条提醒到期时的卡片内部排序（2026-09-07 记录，后续处理）

当前 `prospective_notice.py` 以 notice_id 哈希决定同轮多张卡片的相对顺序（稳定但与到期时间无关）。裁决 DECISION-REMINDER-CARD-ORDER.md 备查项：改为按到期时间排序只需改索引一行；本轮不处理。

## F05 任务面板 README/STATUS 概览截断时的展示（2026-09-07 记录，后续处理）

`task_scope.open_exact` 的 resume_package 视图超 4096 字节会被截断加「…」，STATUS JSON 截断后前端 `statusSummary` 返回 null，状态/目标/水位提示整段消失（独审 REVIEW-TASK-PANEL.md F-2）。建议显示「STATUS 概览已截断」并允许 README/STATUS 点击页入（后端 `task_scope.view` 已支持）。契约只要求首次概括，不阻塞 Task 2。

## F06 provider 传输超时后 Run 停摆（2026-09-07 原生 r11 发现；2026-09-07 已修（Host 侧），待 r12 原生验证）

luna 一次请求 240s 传输超时后，SDK 将该 provider 调用 `settle_unknown`（`reconcile.unknown_settled`、`provider_attempt.degraded`），Host 前台运行时随后既不重试也不终止，Run 停在 RUNNING（20+ 分钟零事件）；同 userdata 冷启动后 `reconcile.recovered` 但仍不续推。证据 `plans/2026-09-07-native-main-journey/NATIVE-R11-PROCEDURE-CHAIN.md`。需要：Host 的 `_ProviderReconciliation` 对 unknown 调用给出可判定结果（重发或按失败收尾）并让前台循环续推；单测 + 原生 r12。

**已修（2026-09-07，工作树 m0623-adopt）**：按 `plans/2026-09-07-native-main-journey/DECISION-PROVIDER-TIMEOUT-STALL.md`——`backend/deskpet/sdk_adapters/reconciliation.py` 新增 `ProviderUnknownRetryOncePolicy`/`ProductProviderRetryOnceReconciliation`（同一 request 首次未知 → `CONFIRMED_NOT_STARTED` 授权重发一次；再次未知 → `STILL_UNKNOWN` + `exhausted_runs`）与 `ProductRuntimeReconciliation`（`RuntimeReconciliationPort` → `coordinator.reconcile_incomplete`）；`backend/main.py` 三处 `_NoopReconciliation` 替换为真实实现，并在 `ports_factory` 恢复被 UNKNOWN provider 调用挂住的 waiting Run 的工具授权（`waiting_runs_blocked_on_provider`）；`backend/deskpet/execution/foreground_runtime.py` `_finish_bound` 观察到 waiting 后跑 Host reconcile 步骤并重新观察，二次未知 cancel 收尾。与分析的偏离：前台不调 `kernel.reconcile()`（其 `recover()`/统一 drain 与工具 continuation 交付互扰，dynamic 路由用例卡死），改为直接 `reconcile_incomplete` + 有界轮询 kernel wake-drain；重启路径额外需要恢复 waiting Run 的工具授权（分析未覆盖）。单测 `backend/tests/execution/test_primary_provider_timeout_reconciliation.py` T1–T4 通过。**待 r12 原生验证**（步 5 复跑，比对 SDK 库 `reconciliation_resolutions` 非空、Host `foreground_execution_reconciliations` 出现 `BOUND_TERMINAL`）。可选 Harness 后续：`_settle_unknown` 保留原始 `exc.code`（区分超时与其他异常）、允许 N 次 rehandoff。

## F07 `context_page_in` 处理器失败后整个 Run 被判 history 不可核验（2026-09-08 语料 run-01e C04-16 / run-01h C06-13 发现，**已修，待语料复跑**）

模型调用 `context_page_in` 失败（`product_tool.failed code=tool_failed`，value 为空）后，下一次 provider 调用被 `PrimaryHistoryDisclosureRejected` 拒绝并终止 Run。`primary_dependencies.py` 只对 `primary_page_hash_mismatch` 做确定性复算、对参数拒绝（a8734fbf）跳过，其余失败 carrier 一律视为不可核验。需要：区分"处理器未返回任何内容"的失败（value 为空且有错误码）与真正不可核验的情况；根因已查：模型把 typed 召回项 id `recall-item:…:1` 当作 `reference_id` 传给 `context_page_in`（该工具只接受本次请求准备好的精确页引用），处理器拒绝；属工具描述/提示引导不足 + 校验器过严的组合。与 F06 一起在 r12 前后处理。

**已修（工作树 m0623-adopt，未并入 main）**：两层同时收口。①校验器：`primary_dependencies.py` 对 `context_page_in` 的失败 carrier（`state=failed`、`outcome=failed`、`value is None`）不再简单跳过，而是用同一 arguments 重放页处理器做**确定性复核**——引用前缀是 `primary-effect-page:v1:` 走 `admitted_current_page`，否则走 `admitted_page`；同样拒绝才 `continue`，重放反而成功（失败非确定性、可能已读到内容）仍抛 `scope_search_result_unverified`。对 primary 页引用（`primary-tool-page:v1:` / `primary-effect-page:v1:`）额外要求重放拒因与记录的 `error_code` 逐字一致，否则 `primary_page_rejection_mismatch`；原 `primary_page_hash_mismatch` 专用分支并入该统一分支。未泛化到 `task_scope_search` / `procedure_discover`。②引导：`CONTEXT_PAGE_IN_SCHEMA` 的 description 与 `reference_id`/`source_hash` 字段说明、以及 `primary_context.py` 的 PERSONA 都写明 `reference_id` 只能逐字取自本次请求已备好的页引用——带 `page_tool="context_page_in"` 的截断标记里的 `reference_id`/`source_hash`，或 `[Context page-in reference: id=… hash=…]` 行；`context_route` 返回的召回片段 `fragments[].ref`（形如 `recall-item:<id>:1`）是记忆项 id，不是页引用，且无页引用时不要调用该工具。

**测试**：`tests/execution/test_scope_disclosure_runtime.py::test_recall_item_reference_page_in_failure_keeps_the_run_verifiable`（复现：模型传 recall-item id → page_in 失败 → 后续 provider 调用不再被拒、Run 正常 COMPLETED 并写出文件；只回滚 `primary_dependencies.py` 时该控为红）与 `::test_non_deterministic_page_in_failure_still_rejects_the_next_request`（负控：同一请求未打补丁时通过，把 `admitted_page` 换成必然成功的重放后仍抛 `PrimaryHistoryDisclosureRejected`，私有拒因 `scope_search_result_unverified`）。`tests/execution` + `tests/sdk_adapters/test_typed_context_use_primary.py` + `test_no_recall_gate.py` 的失败集与改动前完全一致（52 失败 / 10 错误，均为环境既有）。

**待办**：语料 run-01e C04-16 与 run-01h C06-13 复跑确认现场消失。

## F08 `procedure_discover` 词项匹配覆盖不足：模型换个说法就零候选（2026-09-08 语料 run-01j 发现，本轮只记录，不改 SDK）

**现象**：run-01j 中 18/18 例 C06 都调用了 `procedure_discover`（`procedure_hint` 生效），但 C06-05/06/09/19 四例返回 **0 候选**，其中 C06-19 因此彻底判负。

**已排除「种子未落库」**：同一批同一机制的 C06-03 / C06-13 命中非空，且四例的 setup 与通过例结构一致（`backend/deskpet/quality/corpus_c06.py` SPECS 均已映射）。

**根因**：SDK 0.6.25 `simple_harness_memory/backends/procedure_discovery.py::match_score` 只做**字面词项命中**——把候选的 `name + applicability + steps` 拼成一段文本，用 `typed_recall_query_terms`（`\w` 词 + CJK 二元组）逐个做 `in text` 子串判断，再加一分整串命中。没有向量/同义/词干车道。于是命中与否完全取决于模型是否恰好复用了种子里的原词：

| 用例 | 模型 discover 查询 | 种子 name / steps | 结果 |
|---|---|---|---|
| C06-03（PASS） | `预算 金额 检查` | `预算草表` / `先分固定/浮动`、`再核总额` | `预算` 命中 → 1 候选 |
| C06-13（PASS） | `书签 标签 去重` | `书签重复候选检查` / `先按URL对照`… | `书签` 命中 → 1 候选 |
| C06-09（FAIL） | `兴趣周 排程` | `排日程` / `先列固定项`、`再放可移动项` | **`排程` 与 `排日程` 二元组零交集**（查询出 `排程`，候选只含 `排日`、`日程`）→ 0 |
| C06-05（FAIL） | `外出素材 摄影作业` | `压缩前检查描述` / `核原件副本`、`再核可读性` | 无共同词 → 0 |
| C06-06（FAIL） | `可访问性 培训` | `文档检查` / `标题层级`、`阅读顺序`、`替代文本` | 无共同词（`可访问性` vs `替代文本`）→ 0 |
| C06-19（FAIL） | `资料 整理 格式` | `通用手工清单核对` / `清单核对` | 无共同词 → 0 |

C06-09 是最干净的证据：一个字的插入（排程 → 排日程）就让 CJK 二元组集合完全不相交，与既有 CJK typed-recall 缺陷同源。

**给 SDK 侧的建议（本仓不改）**：
1. 给 `discover_procedure_drafts` 接上与 typed recall 同源的向量召回车道，或至少在 `match_score` 里补 CJK 一元组/前后缀（子序列）匹配，使 `排程` 能召回 `排日程`；
2. 若维持纯词法，则返回体应在零候选时带上「本主体确有 N 条已保存 Procedure，但无一条与查询词重合」的计数提示，让模型知道该换词重查而不是判定「没有保存过流程」——这与 Host 侧 `procedure_hint` 的思路一致；
3. 候选文本目前是 `name + applicability + steps` 直接拼接，可考虑把 name 的权重与匹配面单列，避免长 steps 稀释。

**Host 侧本轮不做**：`procedure_discover` 的查询词由模型决定，Host 无权改写；工具描述已写明「用名称/适用条件/步骤里的一两个特征词，不要整句」，run-01j 中模型基本遵守，问题不在提示。

## F09 模型连续空参数调用的止损（2026-09-08 r15）

- 现象：DeepSeek 在同一 Run 内多次发出 `{}` 参数的工具调用（r14 9 次、r15 13 次、A6 多轮），每次都被 `missing_required_argument` 拒绝，直至 `react_max_turns_exceeded`。
- 建议：Host 在同一 Run 内连续 N（如 3）次同一工具空参数被拒后，把该轮结束为"向用户报告当前进展并请求下一步"的确定性回复，而不是继续消耗轮次；记录 audit。不改变契约。
- 状态：待做。

## F01 更新（2026-09-08 用户决定）

- 用户决定：本轮**不做**。事件来源为本地，首选方案 B（绑定工作区内的本地 git release tag 作为事件源），作为下一轮 followup；方案备忘见 `plans/2026-09-07-native-main-journey/DECISION-F01-LOCAL-PUBLISH-EVENT.md`。

## F10 通过对话遗忘认知记忆（模型可见记忆视图，2026-09-08）

- 现象：模型面 `memory_forget` 只能按 `fact_id` 操作旧事实库，无法忘记认知记忆（事件 J 记录）；UI 面板遗忘正常。
- 用户决定（2026-09-08）：同意暂不提前做 S5c 的模型可见记忆视图；A6 第 23/24 轮改用 UI 面板遗忘验证 close/reopen 语义，对话遗忘随 S5c 一起做。
- release tag：用户决定待全部任务完成并**真人验收后**再授权。

## F09 更新（2026-09-08：根因已修，止损降级为可选）

- 根因查明并已修：不是模型自发的坏习惯，而是 Host 自己把污染喂了回去。
  `_wire_messages` 跨轮重建 assistant `tool_calls` 时 arguments 写字面量 `"{}"`，
  而 SDK 契约保证 `metadata[provider_tool_calls]` 必然不存活（三份原生证据库
  129 条 provider_invocations 存活数为 0），于是模型照抄自己被污染的 transcript：
  重建条数为 0 的请求 0/16 条空参调用，≥20 条的请求 22/35（严格单调剂量反应）。
- 修复见 `plans/2026-09-08-hm-to-a6/DECISION-TOOL-CALL-ARGUMENTS-REPLAY.md`
  （`ToolCallArgumentsMemo` 按 call_id 留存并回贴真实入参）。
- F09 原提的「连续 N 次空参即止损收尾」仍可作为纵深防御保留，但**优先级下调**：
  在根因修复后它防的是模型自发的空参，而非 Host 制造的空参。待原生复跑观察后再定。

## F-K1 历史因果组的 assistant 条目不带工具入参（2026-09-08）

- 现象：`project_primary_transcript`（`sdk_adapters/composition.py:1170`）只产出
  `{"role","content"}`，`historical_causal_group` 里模型能看到「调了哪个工具、
  结果是什么」，看不到「用什么参数调的」。
- 与事件 K 同类，但**不经 `_wire_messages`**（它是被引号包进一条 user 消息的记录）。
- 为何本轮不修：补入参须同时改 `project_primary_transcript`、
  `memory/primary_message_v2.py::representable`（硬性要求 assistant 键集恰为
  `{"role","content"}`）与 `item_ordinals`，并使**已归档终态 evidence 的
  envelope hash 全部失效**，还会牵动 `primary_history.transcript_matches` 语义。
  须与终态 evidence 契约版本升级（`primary_message_v3` 一类）一起做。

## F-K2 provider_reasoning_content 跨轮同样丢失（2026-09-08）

- `metadata[provider_reasoning_content]`（DeepSeek 思考模式要求逐字回显）走的是
  与事件 K 完全相同的「durable 往返即清空」路径，跨轮同样丢。
- 修法与 F-K 同构（同一备忘再存一列），但 reasoning 是 provider 私有内容，
  回灌口径（是否、以及在什么条件下把它放回线上）需单独裁决，未并入本轮。
