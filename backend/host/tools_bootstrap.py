"""工具层的启动装配：基础工具注册表、v2 工具注册表、权限与自动模式、插件、能力范围存储与解析器。"""

from __future__ import annotations

from pathlib import Path as _Path
from pathlib import Path as _PathRS

import paths as _paths
from tools.clipboard import read_clipboard_tool
from tools.get_time import get_time_tool
from tools.registry import ToolRegistry

from host.llm_providers import _context_os_e2e_boot_hooks
from host.runtime import config, logger, service_context

# P4-S17: SessionDB is the single conversation source of truth.
# The P4 wire-in below wraps it in RedactingMemoryStore, then agent_engine
# is constructed with that final memory_store.
memory_store = None

# V5 §2.3: agent_engine 与 llm_engine 分层。
# 组装栈:ToolUsingAgent(S3) 包装 SimpleLLMAgent(S2 + S0), memory 在内层。
# 工具调用的结果是 inline 注入 user-facing stream,不走 memory 持久化。
tool_registry = ToolRegistry()
tool_registry.register(get_time_tool)
tool_registry.register(read_clipboard_tool)
service_context.register("tool_router", tool_registry)

# P4-S20: v2 deskpet.tools.registry singleton — full schema-aware
# registry used by the new tool_use agent loop. Hosts the 7 OS tools
# (read/write/edit/list/shell/web/desktop_create_file) plus the
# auto-discovered file/web/memory tools from earlier slices.
# PermissionGate is wired with the control-WS responder so user
# popups appear before any sensitive op runs.
_tool_capability_scope_store = None
_tool_capability_resolver = None
_automode_path = _Path(_paths.user_data_dir()) / "permissions_auto_mode.json"
try:
    from deskpet.permissions.gate import (
        PermissionGate as _PermissionGate,
    )
    from deskpet.permissions.gate import (
        PermissionGateConfig as _PermissionGateConfig,
    )
    from deskpet.tools import registry as deskpet_tool_registry_v2
    from deskpet.tools.os_tools import register_os_tools as _register_os_tools_v2

    _register_os_tools_v2(deskpet_tool_registry_v2)
    # General-agent tools (glob, grep, web_search) registered now;
    # todo_write + agent need closures over runtime objects (SessionDB,
    # LLM shim) and are wired later, after those are constructed.
    from deskpet.tools.code_tools import register_code_tools as _register_code_tools

    _register_code_tools(deskpet_tool_registry_v2)
    _shell_deny = list(
        getattr(getattr(config, "permissions", None), "deny", {}).get("shell_patterns", [])
        or [
            "rm -rf /",
            "format c:",
            "del /f /s /q c:",
        ]
    )
    permission_gate_v2 = _PermissionGate(
        config=_PermissionGateConfig(
            timeout_s=60.0,
            shell_deny_patterns=_shell_deny,
        )
    )
    deskpet_tool_registry_v2.set_permission_gate(permission_gate_v2)
    # WI-T3.1 last-mile wiring: provider 模式注入 ToolsConfig 到 registry。
    # 2026-05-23 测试阶段决策：config.toml 默认全 ON（不走灰度），
    # 用户/管理员可在 config.toml 改 flag 个别关闭。
    deskpet_tool_registry_v2.set_tools_config_provider(lambda: getattr(config, "tools", None))
    from deskpet.tools.capabilities import (
        ToolCapabilityBridgeService as _ToolCapabilityBridgeService,
    )
    from deskpet.tools.capabilities import (
        ToolCapabilityResolver as _ToolCapabilityResolver,
    )
    from deskpet.tools.capabilities import (
        ToolCapabilityScopeStore as _ToolCapabilityScopeStore,
    )

    _tool_capability_scope_store = _ToolCapabilityScopeStore()
    _tool_capability_resolver = _ToolCapabilityResolver(deskpet_tool_registry_v2)
    deskpet_tool_registry_v2.set_capability_scope_store(_tool_capability_scope_store)
    deskpet_tool_registry_v2.set_context_os_enabled_provider(
        lambda: bool(getattr(getattr(config, "features", None), "context_os_v1", False))
    )
    # Test-only Context OS E2E seam. ``from_env`` returns None unless dev
    # mode is explicit and the configured control/data planes are loopback.
    _context_os_e2e_hooks = _context_os_e2e_boot_hooks
    deskpet_tool_registry_v2.set_context_os_e2e_hooks(_context_os_e2e_hooks)
    if bool(getattr(getattr(config, "features", None), "context_os_v1", False)):
        from deskpet.tools.tool_search import register_capability_bridge_tools

        register_capability_bridge_tools(
            deskpet_tool_registry_v2,
            _ToolCapabilityBridgeService(deskpet_tool_registry_v2, _tool_capability_scope_store),
        )

    # WI-T1.5 last-mile: 解析 default_artifact_dir 空字符串 → 默认
    # <user_data>/artifacts/。这样 config.toml 用户留空也能拿到合理默认；
    # 显式填路径则按用户配置走（office_paths.artifact_default_path 直接读）。
    if hasattr(config, "tools") and config.tools.last_mile.default_artifact_dir == "":
        from pathlib import Path as _PathLM

        config.tools.last_mile.default_artifact_dir = str(
            _PathLM(_paths.user_data_dir()) / "artifacts"
        )
        logger.info(
            "last_mile.default_artifact_dir resolved to %s",
            config.tools.last_mile.default_artifact_dir,
        )
    # WI-T2.2 P0 修：按 cfg.tools.verifier.emit_receipts 构造 ReceiptStore
    # 并通过 provider 注入。flag OFF 时 box[0]=None 永不产 receipt（BC）；
    # flag ON 时每次 execute_tool 都 emit + 写盘。
    # 注：用 list[Any] 容器避免 nonlocal (此处在 module 顶层 try-block)。
    _receipt_store_box: list[Any] = [None]

    def _get_receipt_store() -> Any:
        cfg = getattr(config, "tools", None)
        emit = bool(getattr(getattr(cfg, "verifier", None), "emit_receipts", False))
        if not emit:
            return None
        if _receipt_store_box[0] is None:
            try:
                from deskpet.tools.receipt_store import ReceiptStore

                # WI-T2.2 v3 P0 修：取消 min(retention, 7) 截断（last-mile
                # round2 P0-2）。原代码把用户配的 30 天硬压成 7 天，回放窗口
                # 残缺。按 ToolsLastMileConfig.artifact_dir_retention_days
                # 默认 30 + 用户 cfg 真值。
                retention = int(
                    getattr(
                        getattr(cfg, "last_mile", None),
                        "artifact_dir_retention_days",
                        30,
                    )
                )
                _receipt_store_box[0] = ReceiptStore(
                    _PathRS(_paths.user_data_dir()),
                    retention_days=retention,
                )
                # 启动期自清理 (PRD D5)
                deleted = _receipt_store_box[0].cleanup_expired()
                if deleted > 0:
                    logger.info("receipt_store: cleaned %d expired files", deleted)
            except Exception as _rs_exc:  # noqa: BLE001
                logger.warning("ReceiptStore init failed: %s", _rs_exc)
                return None
        return _receipt_store_box[0]

    deskpet_tool_registry_v2.set_receipt_store_provider(_get_receipt_store)
    # The legacy JSON is consumed exactly once into the execution SQLite
    # during lifespan startup.  It is never rebound as a live authority.
    _automode_path = _Path(_paths.user_data_dir()) / "permissions_auto_mode.json"
    # Module-level globals (service_context has a pre-declared key list
    # we don't want to extend just for this).
    # Accessed by the chat handler when it instantiates AgentLoop.

    # P4-S20 Stage C — marketplace installer + registry client.
    from deskpet.skills.marketplace import (
        RegistryClient as _RegistryClient,
    )
    from deskpet.skills.marketplace import (
        SkillInstaller as _SkillInstaller,
    )

    _user_skills_dir = _paths.user_data_dir() / "skills"
    _staging_dir = _paths.user_data_dir() / "_skill_staging"
    _user_skills_dir.mkdir(parents=True, exist_ok=True)
    _staging_dir.mkdir(parents=True, exist_ok=True)
    _registry_url = (
        getattr(getattr(config, "marketplace", None), "registry_url", None)
        or "https://raw.githubusercontent.com/DennyWanye/deskpet/master/docs/skills-registry.json"
    )
    skill_registry_client = _RegistryClient(
        url=_registry_url,
        cache_ttl_s=3600.0,
    )
    skill_installer = _SkillInstaller(
        skills_dir=_user_skills_dir,
        staging_dir=_staging_dir,
        known_tools=set(deskpet_tool_registry_v2.list_tools()),
    )
    # P4-S20 Stage D — plugin system.
    from deskpet.plugins import PluginManager as _PluginManager

    _plugins_dir = _paths.user_data_dir() / "plugins"
    _plugins_dir.mkdir(parents=True, exist_ok=True)
    _enabled_plugins = list(getattr(getattr(config, "plugins", None), "enabled", []) or [])
    plugin_manager = _PluginManager(
        plugins_dir=_plugins_dir,
        enabled=_enabled_plugins or None,  # None → default-enable all
    )
    plugin_manager.discover()
    logger.info(
        "p4_s20_plugins_discovered",
        plugins_dir=str(_plugins_dir),
        plugins=[p["name"] for p in plugin_manager.list_plugins()],
    )
    # WI-TG-2: register the gate as a read-only service so the p4_ipc
    # `permissions_pending_list` handler can surface in-flight requests to
    # the ApprovalCenterPanel. Read-only — the panel never mutates the gate.
    try:
        service_context.register("permission_gate", permission_gate_v2)
    except Exception as _pg_reg_exc:  # noqa: BLE001 — non-fatal
        logger.warning("permission_gate_register_failed", error=str(_pg_reg_exc))

    logger.info(
        "p4_s20_tool_registry_v2_ready",
        os_tools=len(deskpet_tool_registry_v2.list_tools(source="builtin")),
    )
except Exception as _v2_exc:  # noqa: BLE001 — non-fatal, log + degrade
    logger.warning("p4_s20_v2_init_failed", error=str(_v2_exc))
    deskpet_tool_registry_v2 = None
    permission_gate_v2 = None
    skill_registry_client = None
    skill_installer = None
    plugin_manager = None
    _get_receipt_store = None

service_context.register("tool_capability_scope_store", _tool_capability_scope_store)
service_context.register("tool_capability_resolver", _tool_capability_resolver)
