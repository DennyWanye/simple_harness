# 宿主入口结构（backend/main.py 与 backend/host/）

最后更新：2026-10-13 CST（代码质量整治阶段 2：拆主入口）

## 一句话

`backend/main.py` 只做组装：导入、生命周期按序调用启动/关闭步骤、创建应用与中间件、注册路由、`main()`。
其余代码按职责分在 `backend/host/` 包里。拆分前 main.py 17311 行，拆分后约 290 行，行为不变（启动快照、控制通道快照、后端全量回归、独立核验）。

**旧文档里 `main.py:<行号>` 形式的引用已失效**，按本文末尾的"函数与类所在模块"查新位置。逐条改写归代码质量整治阶段 10。

## 生命周期

`lifespan` 按顺序调用：

1. `_startup_core_services` → 返回提供方路由就绪状态、启动纪元
2. `_startup_provider_routing(提供方路由就绪状态)`
3. `_startup_search_and_billing`
4. `_startup_execution_ledger` → 返回会话库（执行账本独立构造并登记 `execution_uow`；后台旧工作流引擎 2026-10-18 起不再启动）
5. `_startup_restore_and_skill_prewarm`
6. `_startup_embedder_and_workload_audit` → 返回向量工作器
7. `_startup_vector_worker(会话库, 向量工作器)`
8. `_startup_curation_image_and_mcp`
9. `_startup_self_healing`（工具熔断与会话活动记录）
10. `_startup_supervisor`
11. `_startup_model_context`
12. `_startup_runtime_and_desktop_bridge(启动纪元)`
13. `yield`
14. `_shutdown_runtimes_and_tasks`、`_shutdown_services`

跨步骤共用的变量只有括号里这几个，用参数与返回值显式传递。

## 控制通道（/ws/control）

- 端点 `host/control_channel.py:control_channel`：握手（密钥校验、连接登记、启动状态帧、伴侣挑战）→ 收消息循环。
- 每条消息：伴侣特权帧走 `_handle_privileged_companion_frame`；伴侣详情查询；`host/control_messages.py:_handle_control_ws_message` 先处理一批；其余按 `_CONTROL_ROUTES` 有序路由表分发。
- 路由表每项是"消息类型集合"或"判断函数"（编排 `mission_`/`orchestration_`/`taskgraph.` 前缀、P4 进程间消息、`workflow_` 前缀），顺序与原 if/elif 链一致；都不匹配回"未知类型"。
- 处理函数签名统一为 `_cc_<类型>(conn, raw, msg_type)`，`conn` 是 `host/control_connection.py:_ControlConnection`（WebSocket、会话号、请求范围、伴侣挑战、记忆控制绑定）。
- 处理函数按领域分在 `control_memory`、`control_decisions`、`control_settings`、`control_capabilities`、`control_sessions`、`control_providers`、`control_chat`。

## 约束

- host 模块一律显式导入依赖，不用 `globals()` 查名字（有结构测试）。
- 被多个模块按名字导入的可变容器（连接表、SDK 运行登记表）只改内容、不重新绑定；会被重新绑定的运行期单例放在 `host/state.py:host_state`。
- `host/runtime.py` 最开头导入日志配置：后端标准输出交给外壳读共享密钥，导入主入口不许往标准输出写东西（有测试）。
- 状态库打不开时应用降级启动，主入口仍能导入（有测试）。

## 测试辅助

- `tests/host_source.py`：`host_source_text()`、`host_source_tree()`、`host_function_source(name)`、`host_source_files()`，读源码或扫描生产代码的测试用它们，把 host 包一并纳入。
- `tests/host_patch.py:patch_host_global`：同一个对象被多个模块按名字导入时，在主入口与所有 host 模块里一起打桩。
- `tests/conftest.py` 在任何测试模块导入前把用户数据、缓存、日志、深度研究目录指到临时目录，测试不碰用户真实数据。

## 特性化快照（scripts/code_quality/）

- `startup_snapshot.py`：真实启动流程的路由、导入期与生命周期服务登记顺序、启动后服务类型、关闭阶段登记顺序与日志事件。
- `control_snapshot.py`：83 类控制消息各开新连接发最小消息，记录回包类型、字段名、错误码。

## 模块

| 模块 | 行数 | 职责 |
|---|---|---|
| `host/agent_factory.py` | 376 | 构造 Agent：组装主循环、校验门、工具与上下文（整段搬家，拆函数在后面的阶段） |
| `host/auth.py` | 30 | 共享密钥校验：开发模式开关与 WebSocket 密钥校验 |
| `host/capability_runtime.py` | 402 | 能力平台运行时与授权模式：能力运行时初始化、授权自动模式切换与策略快照、冻结技能快照解析器绑定 |
| `host/chat_delivery.py` | 274 | 聊天投递与广播：控制通道广播、按对端分组投递最终回复与错误、新会话回显、聊天对端分组与会话解析。 |
| `host/companion_services.py` | 197 | 伴侣服务：投影服务、动作决策服务初始化与动作命令分发 |
| `host/connections.py` | 17 | 宿主进程的连接登记表：控制通道连接、按连接的检视查询任务、聊天对端分组。只改内容、不重新绑定。 |
| `host/context_projection.py` | 174 | 上下文压缩模型与任务投影：压缩模型选择、任务投影器、初始上下文快照与请求附带任务快照 |
| `host/context_usage.py` | 565 | 上下文用量与上下文明细：会话上下文状态缓存、快照元数据、用量的读取与广播、用量事件、上下文明细的读取与请求处理。 |
| `host/control_capabilities.py` | 537 | 控制消息：能力列表与操作、插件、技能安装与卸载 |
| `host/control_channel.py` | 701 | 控制通道：连接握手、伴侣特权帧、伴侣详情查询、有序路由表与分发、编排与工作流转发 |
| `host/control_chat.py` | 325 | 控制消息：对话 |
| `host/control_connection.py` | 19 | 控制连接状态对象 |
| `host/control_decisions.py` | 303 | 控制消息：运行状态查询、授权与受保护路径决定、外部等待与项目目录回复、澄清、计划确认、中断 |
| `host/control_memory.py` | 165 | 控制消息：记忆整理、点赞、归档列表、记忆管理转发、人类记忆请求 |
| `host/control_messages.py` | 544 | 控制通道消息处理：控制消息分发、未决决定重放、运行检查任务调度 |
| `host/control_providers.py` | 690 | 控制消息：模型列表、会话提供方与模型、设置页提供方管理与模型探测 |
| `host/control_sessions.py` | 479 | 控制消息：会话列表、删除、改名、加载消息、项目会话、上下文用量与明细、斜杠命令 |
| `host/control_settings.py` | 322 | 控制消息：授权模式、发布目录、对话超时、预算、监督开关与选择、调试、提供方连通测试 |
| `host/growth_authority.py` | 710 | 成长权威：初始化与切换（整段搬家，658 行的初始化函数拆步骤在后面的阶段） |
| `host/harness_chat.py` | 965 | 产品对话：项目目录解析、预检拦截提交、桌面 SDK 对话、产品对话主流程与超时、对话启动 |
| `host/harness_runs.py` | 546 | 产品运行控制：取消运行、权威运行投影、在途子运行判断、决定信号、执行工作流决定、续跑与续跑启动 |
| `host/history_events.py` | 281 | 历史事件与伴侣收件箱：工作流与伴侣历史事件附加、伴侣收件箱路由绑定与投影结算 |
| `host/human_memory_ports.py` | 326 | 人类记忆宿主端口与终端操作审计激活、未完成提供方对账 |
| `host/llm_calls.py` | 336 | 模型调用的构造与刷新：给事实抽取、深度研究、图像接口用的调用函数，以及设置变化后的重新接线。 |
| `host/llm_providers.py` | 207 | 模型提供方的启动装配：设置页的运行时覆盖、密钥解析、本地与云端提供方、计费账本、混合路由。 |
| `host/logging_setup.py` | 77 | 后端进程的日志配置：标准库日志写文件与控制台，structlog 输出 JSON，并做日志脱敏。导入本模块即完成配置。 |
| `host/memory_messages.py` | 203 | 控制通道的记忆管理消息与设置页技能安装适配 |
| `host/product_agent.py` | 248 | 产品 Agent：上下文系统恢复、伴侣后台请求判断、产品 Agent 主循环构造 |
| `host/provider_chain.py` | 150 | 按会话选模型提供方链：代码工作流的提供方选择与恢复、持久任务的提供方快照、Agent 的提供方链构造与解析。 |
| `host/routes_cloud_config.py` | 301 | HTTP 路由：云端模型配置（POST /config/cloud） |
| `host/routes_commands.py` | 266 | HTTP 路由：技能列表、命令帮助与命令参数结构 |
| `host/routes_health.py` | 105 | HTTP 路由：健康检查、前端指标上报与指标查询 |
| `host/routes_voice.py` | 72 | 语音 WebSocket：实时语音通道 |
| `host/runtime.py` | 54 | 宿主进程的全局单例：崩溃上报钩子、日志器、配置、共享密钥、服务注册表。这些对象在导入时创建、之后不再重新绑定。 |
| `host/sdk_activation.py` | 422 | SDK 运行时激活：提供方链检查、产品运行宿主签发、产品 SDK 运行时激活、伴侣运行适配器与入口开放 |
| `host/sdk_context.py` | 327 | SDK 上下文快照准备：意图提示、SDK 系统提示词、可信本地页面、历史截断与上下文快照 |
| `host/sdk_execution.py` | 605 | SDK 执行：单次运行执行、提供方授权冻结与绑定解析、上下文用量绑定快照、桌面测试桥、身份绑定后恢复、本机所有者认证 |
| `host/sdk_recovery.py` | 348 | SDK 运行恢复：保留运行的看护、恢复看护器、恢复后的投递通道、无法恢复的工具授权隔离、未决授权投影 |
| `host/sdk_registry.py` | 14 | SDK 运行的进程内登记表：根会话到运行、请求取消的运行、保留中的展示、恢复监视任务、工具授权不可用的运行。只改内容、不重新绑定。 |
| `host/sdk_runtime_stack.py` | 1426 | SDK 运行栈组装（整段搬家，拆函数在后面的阶段）与装配槽位检查 |
| `host/sdk_services.py` | 378 | SDK 运行服务：延迟单例、目录冻结与价格快照、提供方绑定解析、运行授权代理、运行上下文权威 |
| `host/settings_persistence.py` | 51 | 设置页触发的持久化与重启：重启编排服务、把监工开关写回 config.toml。 |
| `host/shutdown.py` | 254 | 关闭步骤：运行时与后台任务、各项服务 |
| `host/startup_background.py` | 351 | 启动步骤：恢复与技能预热、嵌入与负载审计、向量工作器、整理提示与图片工作器与 MCP |
| `host/startup_core.py` | 292 | 启动步骤：核心服务与记忆纪元、提供方路由、检索网关与计费账本、桌面测试桥与实时语音 |
| `host/startup_supervision.py` | 347 | 启动步骤：工具熔断与会话活跃度、监督 Agent、模型上下文窗口 |
| `host/startup_execution.py` | 130 | 启动步骤：会话库、执行账本、模型调用协调与负载路由、公开查看服务 |
| `host/state.py` | 60 | 宿主进程的运行期单例：启动时创建，关闭时清空。 |
| `host/stores_bootstrap.py` | 146 | 会话与存储的启动装配：会话账本、各存储的模块级占位、技能加载与托管技能投影、项目绑定与会话创建服务、基础 Agent。 |
| `host/summary_quality.py` | 41 | 会话摘要质量辅助函数（生产代码已无调用，归阶段 8 删除） |
| `host/tools_bootstrap.py` | 226 | 工具层的启动装配：基础工具注册表、v2 工具注册表、权限与自动模式、插件、能力范围存储与解析器。 |
| `host/window_control.py` | 21 | 窗口控制凭证的一次性引导：Rust 外壳只在启动时从标准输入传一行凭证。手动或测试启动没有这个标记，绝不读标准输入；此时伴侣的特权变更保持关闭，其他功能照常。 |

### 函数与类所在模块（原在 main.py）

| 名字 | 现在所在 |
|---|---|
| `_activate_companion_runtime_adapter_and_open_ingress` | `host/sdk_activation.py` |
| `_activate_human_memory_host_ports` | `host/human_memory_ports.py` |
| `_activate_product_sdk_runtime` | `host/sdk_activation.py` |
| `_activate_terminal_operation_audit` | `host/human_memory_ports.py` |
| `api_command_schema` | `host/routes_commands.py` |
| `api_commands_help` | `host/routes_commands.py` |
| `api_skills_list` | `host/routes_commands.py` |
| `_approx_tokens` | `host/context_usage.py` |
| `_assert_sdk_composition_slots` | `host/sdk_runtime_stack.py` |
| `_assert_sdk_provider_mutation_allowed` | `host/sdk_execution.py` |
| `_attach_companion_history_events` | `host/history_events.py` |
| `_attach_context_snapshot_metadata` | `host/context_usage.py` |
| `_attach_task_snapshot_to_request` | `host/context_projection.py` |
| `_attach_workflow_history_events` | `host/history_events.py` |
| `_authoritative_sdk_run_projection` | `host/harness_runs.py` |
| `_authorization_auto_mode` | `host/capability_runtime.py` |
| `_authorization_policy_snapshot` | `host/capability_runtime.py` |
| `_bind_companion_inbox_route` | `host/history_events.py` |
| `_bind_frozen_skill_snapshot_resolver` | `host/capability_runtime.py` |
| `_bounded_sdk_history` | `host/sdk_context.py` |
| `_broadcast_context_usage_snapshot` | `host/context_usage.py` |
| `_broadcast_control` | `host/chat_delivery.py` |
| `_broadcast_default_chat_peers` | `host/chat_delivery.py` |
| `build_agent` | `host/agent_factory.py` |
| `_build_agent_provider_chain` | `host/provider_chain.py` |
| `_build_context_task_projector` | `host/context_projection.py` |
| `_build_intent_hint` | `host/sdk_context.py` |
| `_build_product_agent_loop` | `host/product_agent.py` |
| `_build_product_sdk_runtime_stack` | `host/sdk_runtime_stack.py` |
| `_build_run_context_authority` | `host/sdk_services.py` |
| `_cache_context_usage_state` | `host/context_usage.py` |
| `_cancel_harness_inspector_tasks` | `host/control_messages.py` |
| `_cancel_product_harness_run` | `host/harness_runs.py` |
| `_cc_budget_status` | `host/control_settings.py` |
| `_cc_capability_install` | `host/control_capabilities.py` |
| `_cc_capability_list` | `host/control_capabilities.py` |
| `_cc_capability_operations_list` | `host/control_capabilities.py` |
| `_cc_chat` | `host/control_chat.py` |
| `_cc_chat_turn_timeout_get` | `host/control_settings.py` |
| `_cc_chat_turn_timeout_set` | `host/control_settings.py` |
| `_cc_chat_v2_interrupt` | `host/control_decisions.py` |
| `_cc_clarification_response` | `host/control_decisions.py` |
| `_cc_companion_detail_get` | `host/control_channel.py` |
| `_cc_context_breakdown_request` | `host/control_sessions.py` |
| `_cc_context_usage_request` | `host/control_sessions.py` |
| `_cc_debug_dump_session_activity` | `host/control_settings.py` |
| `_cc_debug_force_error_pending` | `host/control_settings.py` |
| `_cc_debug_inject_supervisor_alert` | `host/control_settings.py` |
| `_cc_external_wait_response` | `host/control_decisions.py` |
| `_cc_human_memory_request` | `host/control_memory.py` |
| `_cc_interrupt` | `host/control_decisions.py` |
| `_cc_memory_archive_list` | `host/control_memory.py` |
| `_cc_memory_list` | `host/control_memory.py` |
| `_cc_memory_summarize_now` | `host/control_memory.py` |
| `_cc_memory_thumbs_up` | `host/control_memory.py` |
| `_cc_models_list` | `host/control_providers.py` |
| `_cc_orchestration_publish_dir_get` | `host/control_settings.py` |
| `_cc_orchestration_routes` | `host/control_channel.py` |
| `_cc_p4_ipc` | `host/control_channel.py` |
| `_cc_permission_auto_mode_get` | `host/control_settings.py` |
| `_cc_permission_auto_mode_set` | `host/control_settings.py` |
| `_cc_permission_response` | `host/control_decisions.py` |
| `_cc_ping` | `host/control_channel.py` |
| `_cc_plan_confirm` | `host/control_decisions.py` |
| `_cc_plugin_disable` | `host/control_capabilities.py` |
| `_cc_plugin_enable` | `host/control_capabilities.py` |
| `_cc_plugin_list` | `host/control_capabilities.py` |
| `_cc_project_directory_response` | `host/control_decisions.py` |
| `_cc_project_preview_register` | `host/control_sessions.py` |
| `_cc_protected_path_decision` | `host/control_decisions.py` |
| `_cc_provider_test_connection` | `host/control_settings.py` |
| `_cc_session_delete` | `host/control_sessions.py` |
| `_cc_session_messages_load` | `host/control_sessions.py` |
| `_cc_session_provider_get` | `host/control_providers.py` |
| `_cc_session_rename` | `host/control_sessions.py` |
| `_cc_session_run_status_query` | `host/control_decisions.py` |
| `_cc_session_set_provider` | `host/control_providers.py` |
| `_cc_sessions_list` | `host/control_sessions.py` |
| `_cc_settings_providers_list_request` | `host/control_providers.py` |
| `_cc_settings_providers_probe_models` | `host/control_providers.py` |
| `_cc_skill_install_confirm` | `host/control_capabilities.py` |
| `_cc_skill_install_from_url` | `host/control_capabilities.py` |
| `_cc_skill_install_status` | `host/control_capabilities.py` |
| `_cc_skill_list_installed` | `host/control_capabilities.py` |
| `_cc_skill_marketplace_list` | `host/control_capabilities.py` |
| `_cc_skill_uninstall` | `host/control_capabilities.py` |
| `_cc_slash_command` | `host/control_sessions.py` |
| `_cc_supervisor_toggle` | `host/control_settings.py` |
| `_cc_supervisor_user_choice` | `host/control_settings.py` |
| `_cc_unknown` | `host/control_channel.py` |
| `_cc_workflow_routes` | `host/control_channel.py` |
| `_chat_turn_timeout_s` | `host/llm_providers.py` |
| `CloudConfigRequest` | `host/routes_cloud_config.py` |
| `_commit_product_preflight_block` | `host/harness_chat.py` |
| `_complete_growth_authority_cutover` | `host/growth_authority.py` |
| `_configured_context_compaction_model` | `host/context_projection.py` |
| `control_channel` | `host/control_channel.py` |
| `_ControlConnection` | `host/control_connection.py` |
| `_current_slash_skill_catalog` | `host/routes_commands.py` |
| `_dispatch_companion_action_command` | `host/companion_services.py` |
| `_dispatch_control_message` | `host/control_channel.py` |
| `_emit_context_usage` | `host/context_usage.py` |
| `_ensure_companion_inbox_route` | `host/history_events.py` |
| `_ensure_evidence_ingress` | `host/sdk_services.py` |
| `_ensure_foreground_effect_gate` | `host/sdk_services.py` |
| `_ensure_provider_reconciliation` | `host/sdk_services.py` |
| `_ensure_run_fault_memo` | `host/sdk_services.py` |
| `_ensure_run_route_state_memo` | `host/sdk_services.py` |
| `_ensure_sdk_recovery_watcher` | `host/sdk_recovery.py` |
| `_ensure_task_scope_update_service` | `host/sdk_services.py` |
| `_execute_sdk_run` | `host/sdk_execution.py` |
| `_freeze_current_session_owner` | `host/stores_bootstrap.py` |
| `_freeze_durable_task_provider_snapshot` | `host/provider_chain.py` |
| `_freeze_sdk_catalog` | `host/sdk_services.py` |
| `_freeze_sdk_provider_authority` | `host/sdk_execution.py` |
| `_handle_context_breakdown_request` | `host/context_usage.py` |
| `_handle_control_ws_message` | `host/control_messages.py` |
| `_handle_memory_message` | `host/memory_messages.py` |
| `_handle_privileged_companion_frame` | `host/control_channel.py` |
| `health` | `host/routes_health.py` |
| `HostState` | `host/state.py` |
| `_initial_chat_peer_group` | `host/chat_delivery.py` |
| `_initialize_capability_runtime` | `host/capability_runtime.py` |
| `_initialize_companion_action_decision_service` | `host/companion_services.py` |
| `_initialize_companion_projection_services` | `host/companion_services.py` |
| `_initialize_growth_authority` | `host/growth_authority.py` |
| `_initialize_sdk_desktop_test_bridge` | `host/sdk_execution.py` |
| `_intent_label_from_turn` | `host/sdk_context.py` |
| `_is_companion_background_request` | `host/product_agent.py` |
| `_is_companion_history_session_id` | `host/history_events.py` |
| `_isolate_unrestorable_sdk_tool_authority` | `host/sdk_recovery.py` |
| `_issue_product_harness_host` | `host/sdk_activation.py` |
| `_launch_product_harness_chat` | `host/harness_chat.py` |
| `_launch_product_harness_continuation` | `host/harness_runs.py` |
| `_legacy_growth_owner` | `host/growth_authority.py` |
| `_live_context_compaction_model` | `host/context_projection.py` |
| `_load_llm_runtime_overrides` | `host/llm_providers.py` |
| `_local_owner_auth` | `host/sdk_execution.py` |
| `_make_live_str_llm_call` | `host/llm_calls.py` |
| `_make_research_llm_call_v2` | `host/llm_calls.py` |
| `_make_session_aware_llm_call` | `host/llm_calls.py` |
| `_make_str_llm_call` | `host/llm_calls.py` |
| `metrics` | `host/routes_health.py` |
| `_persist_supervisor_enabled` | `host/settings_persistence.py` |
| `_prepare_sdk_context_snapshot` | `host/sdk_context.py` |
| `_primary_history_policy` | `host/sdk_services.py` |
| `_product_harness_has_live_attached_child` | `host/harness_runs.py` |
| `_ProductSdkProviderBindingResolver` | `host/sdk_services.py` |
| `_project_initial_context_snapshot` | `host/context_projection.py` |
| `_project_open_sdk_authorizations` | `host/sdk_recovery.py` |
| `_provider_chain_or_none` | `host/sdk_activation.py` |
| `_provider_display_name` | `host/routes_cloud_config.py` |
| `_provider_endpoint_identity_for_binding` | `host/sdk_execution.py` |
| `_public_context_section` | `host/context_usage.py` |
| `_read_context_breakdown_authority` | `host/context_usage.py` |
| `_read_context_usage_authority` | `host/context_usage.py` |
| `_reconcile_incomplete_providers` | `host/human_memory_ports.py` |
| `_recovered_sdk_noop_broadcast` | `host/sdk_recovery.py` |
| `_recovered_sdk_send_final` | `host/sdk_recovery.py` |
| `_RecoveredSdkWebSocket` | `host/sdk_recovery.py` |
| `_refresh_image_endpoint_resolver` | `host/llm_calls.py` |
| `_refresh_research_live_llm` | `host/llm_calls.py` |
| `_register_chat_peer` | `host/chat_delivery.py` |
| `_remap_chat_peer_group` | `host/chat_delivery.py` |
| `_replay_open_harness_decisions` | `host/control_messages.py` |
| `_resolve_agent_provider_chain` | `host/provider_chain.py` |
| `_resolve_chat_source_session` | `host/chat_delivery.py` |
| `_resolve_chat_task_scope` | `host/chat_delivery.py` |
| `_resolve_code_recovery_provider` | `host/provider_chain.py` |
| `_resolve_ephemeral_provider` | `host/llm_calls.py` |
| `_resolve_llm_api_key` | `host/llm_providers.py` |
| `_resolve_primary_registry_provider` | `host/llm_calls.py` |
| `_resolve_sdk_provider_binding_resolver` | `host/sdk_execution.py` |
| `_resolve_selected_project_directory` | `host/harness_chat.py` |
| `_restart_orchestration` | `host/settings_persistence.py` |
| `_restore_product_context_os` | `host/product_agent.py` |
| `_restore_sdk_delivery_route` | `host/sdk_recovery.py` |
| `_run_product_harness_chat` | `host/harness_chat.py` |
| `_run_product_harness_chat_with_timeout` | `host/harness_chat.py` |
| `_run_product_harness_continuation` | `host/harness_runs.py` |
| `_run_sdk_desktop_chat` | `host/harness_chat.py` |
| `_save_llm_runtime_overrides` | `host/llm_providers.py` |
| `_schedule_harness_inspector_task` | `host/control_messages.py` |
| `_sdk_context_usage_from_projection_only` | `host/sdk_context.py` |
| `_sdk_desktop_test_enabled` | `host/sdk_execution.py` |
| `_sdk_price_snapshot` | `host/sdk_services.py` |
| `_sdk_runtime_authority_bindings` | `host/sdk_services.py` |
| `_sdk_text_tokens` | `host/sdk_context.py` |
| `_SdkRunContextAuthorityProxy` | `host/sdk_services.py` |
| `_SdkRuntimeDecisionSinkProxy` | `host/sdk_services.py` |
| `_SdkTaskExecutionAuthorityProxy` | `host/sdk_services.py` |
| `_seed_registry_from_runtime_overrides` | `host/routes_cloud_config.py` |
| `_select_code_workflow_provider` | `host/provider_chain.py` |
| `_send_chat_error` | `host/chat_delivery.py` |
| `_send_chat_final` | `host/chat_delivery.py` |
| `_send_context_usage_authority` | `host/context_usage.py` |
| `_send_new_session_origin_user_echo` | `host/chat_delivery.py` |
| `_set_authorization_auto_mode` | `host/capability_runtime.py` |
| `_settings_skill_install_adapter` | `host/memory_messages.py` |
| `_settle_companion_bind_projection` | `host/history_events.py` |
| `_shutdown_runtimes_and_tasks` | `host/shutdown.py` |
| `_shutdown_services` | `host/shutdown.py` |
| `_signal_product_harness_decision` | `host/harness_runs.py` |
| `_skill_install_error_payload` | `host/memory_messages.py` |
| `_snapshot_context_usage_basis_for_run` | `host/sdk_execution.py` |
| `_snapshot_context_usage_binding_for_run` | `host/sdk_execution.py` |
| `_snapshot_context_usage_event` | `host/context_usage.py` |
| `_sql_build_reinject_msg` | `host/summary_quality.py` |
| `_sql_latest_task_snapshot` | `host/summary_quality.py` |
| `_sql_user_is_confused` | `host/summary_quality.py` |
| `_startup_core_services` | `host/startup_core.py` |
| `_startup_curation_image_and_mcp` | `host/startup_background.py` |
| `_startup_embedder_and_workload_audit` | `host/startup_background.py` |
| `_startup_model_context` | `host/startup_supervision.py` |
| `_startup_runtime_and_desktop_bridge` | `host/startup_core.py` |
| `_startup_provider_routing` | `host/startup_core.py` |
| `_startup_restore_and_skill_prewarm` | `host/startup_background.py` |
| `_startup_search_and_billing` | `host/startup_core.py` |
| `_startup_self_healing` | `host/startup_supervision.py` |
| `_startup_supervisor` | `host/startup_supervision.py` |
| `_startup_vector_worker` | `host/startup_background.py` |
| `_startup_execution_ledger` | `host/startup_execution.py` |
| `_trigger_harness_recovery_after_identity_bind` | `host/sdk_execution.py` |
| `_trusted_local_page_url_from_env` | `host/sdk_context.py` |
| `_try_resolve_execution_workflow_decision` | `host/harness_runs.py` |
| `update_cloud_config` | `host/routes_cloud_config.py` |
| `_validate_code_recovery_capability_subset` | `host/provider_chain.py` |
| `_validate_secret` | `host/auth.py` |
| `_watch_retained_sdk_run` | `host/sdk_recovery.py` |
