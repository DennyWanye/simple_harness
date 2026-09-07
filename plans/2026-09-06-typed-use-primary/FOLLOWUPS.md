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

## F06 provider 传输超时后 Run 停摆（2026-09-07 原生 r11 发现，待修）

luna 一次请求 240s 传输超时后，SDK 将该 provider 调用 `settle_unknown`（`reconcile.unknown_settled`、`provider_attempt.degraded`），Host 前台运行时随后既不重试也不终止，Run 停在 RUNNING（20+ 分钟零事件）；同 userdata 冷启动后 `reconcile.recovered` 但仍不续推。证据 `plans/2026-09-07-native-main-journey/NATIVE-R11-PROCEDURE-CHAIN.md`。需要：Host 的 `_ProviderReconciliation` 对 unknown 调用给出可判定结果（重发或按失败收尾）并让前台循环续推；单测 + 原生 r12。

## F07 `context_page_in` 处理器失败后整个 Run 被判 history 不可核验（2026-09-08 语料 run-01e C04-16 发现，待修）

模型调用 `context_page_in` 失败（`product_tool.failed code=tool_failed`，value 为空）后，下一次 provider 调用被 `PrimaryHistoryDisclosureRejected` 拒绝并终止 Run。`primary_dependencies.py` 只对 `primary_page_hash_mismatch` 做确定性复算、对参数拒绝（a8734fbf）跳过，其余失败 carrier 一律视为不可核验。需要：区分"处理器未返回任何内容"的失败（value 为空且有错误码）与真正不可核验的情况；同时查该次 page_in 失败的根因（页引用是否由模型编造）。与 F06 一起在 r12 前后处理。
