"""宿主进程的运行期单例：启动时创建，关闭时清空。

这些对象原先是 main.py 的模块级变量，在函数里用 ``global`` 重新绑定。函数搬出 main.py 后，
``from main import X`` 只拿到导入那一刻的对象，之后的重新绑定不会传过去。所以统一收在这里，
读写都经 ``host_state.<名字>``。不会被重新绑定的全局对象（服务注册表、配置等）不在这里。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm.provider_registry import LLMProviderRegistry


class HostState:
    """可变的运行期单例集合。默认值即"尚未创建"。"""

    # —— 模型与提供方 ——
    local_llm: Any = None
    current_cloud_api_key: str | None = None
    provider_registry: LLMProviderRegistry | None = None
    # —— 伴侣与成长 ——
    companion_store: Any = None
    growth_authority_router: Any = None
    companion_identity_gate: Any = None
    companion_profile_coordinator: Any = None
    companion_control_ingress: Any = None
    companion_runtime: Any = None
    companion_notification_service: Any = None
    companion_ingress_dispatcher: Any = None
    companion_growth_pipeline: Any = None
    companion_activation_dispatcher: Any = None
    growth_cutover_coordinator: Any = None
    growth_cutover_plan: Any = None
    legacy_growth_owner: tuple[str, int] = ("legacy_local_profile", 1)
    # —— 会话 ——
    session_creation_service: Any = None
    # —— SDK 运行时 ——
    sdk_runtime_stack: Any = None
    sdk_ingress: Any = None
    provider_reconciliation: Any = None
    runtime_reconciliation: Any = None
    foreground_effect_gate: Any = None
    run_fault_memo: Any = None
    run_route_state_memo: Any = None
    evidence_ingress: Any = None
    sdk_context_port: Any = None
    sdk_runtime_catalog: dict[str, Any] | None = None
    sdk_run_binding_registry: Any = None
    sdk_provider_binding_resolver: Any = None
    task_scope_update_service: Any = None
    sdk_tool_authority_registry: Any = None
    sdk_runtime_tool_inventory: Any = None
    sdk_desktop_bridge: Any = None


host_state = HostState()
