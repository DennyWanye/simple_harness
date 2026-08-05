# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import sys
import time
# Force UTF-8 stdout on Windows (default GBK chokes on emoji in LLM output)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

from deskpet.frozen_worker_dispatch import dispatch_frozen_worker_if_requested

dispatch_frozen_worker_if_requested()

import asyncio
import hashlib
import inspect
import json
import os
import re
import secrets
import traceback
import uuid
import weakref
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from deskpet.companion.control_credentials import (
    WindowControlBootstrap,
    WindowControlCredentialError,
)

# Rust marks only its one-shot stdin bootstrap. Manual/pytest launches omit
# the marker and must never block on stdin; privileged Companion mutations
# then remain fail-closed while all ordinary backend features keep working.
_WINDOW_CONTROL_BOOTSTRAP: WindowControlBootstrap | None = None
if os.environ.get("DESKPET_WINDOW_CONTROL_BOOTSTRAP") == "stdin-v1":
    try:
        _WINDOW_CONTROL_BOOTSTRAP = WindowControlBootstrap.parse_line(
            sys.stdin.readline().strip()
        )
    except WindowControlCredentialError:
        _WINDOW_CONTROL_BOOTSTRAP = None

import logging
from pathlib import Path as _Path

import structlog
import uvicorn
from deskpet.session.task_scope import (
    TaskScopeDecision,
    task_session_manager,
)

# P2-2 debug (2026-04-20): Rust supervisor drains child stdout/stderr after
# the SHARED_SECRET handshake, so structlog's console output vanishes once
# the frontend is driving the backend. Mirror everything into
# logs/backend.log via the stdlib logging root so we can tail pipeline
# events (asr_result / vad / lip_sync) without bouncing through the
# supervisor. structlog defaults to using stdlib logging under the hood,
# so configuring the root handler is enough.
_log_dir = _Path(__file__).parent.parent / "logs"
_log_dir.mkdir(exist_ok=True)
_log_file = _log_dir / "backend.log"
_file_handler = logging.FileHandler(_log_file, encoding="utf-8")
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
)
_stream_handler = logging.StreamHandler()
logging.basicConfig(
    level=logging.INFO,
    handlers=[_stream_handler, _file_handler],
    force=True,  # override anything uvicorn may have installed earlier
)

# structlog defaults to its own PrintLogger (stdout only). Point it at
# stdlib logging so the FileHandler above actually receives events.
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.KeyValueRenderer(
            key_order=["event", "level", "timestamp"],
            sort_keys=False,
        ),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from pathlib import Path
from pydantic import BaseModel, field_validator, model_validator

from config import (
    effective_llm_model,
    load_config,
    resolve_config_path,
    resolve_deep_research_workflow_version,
)
import paths as _paths
from context import ServiceContext
from deskpet.agent.product_domain_sink import LegacyProductDomainSink
from deskpet.agent.run_presenter import (
    PresentationState,
    RunPresentationContext,
)
from deskpet.agent.turn_preparer import (
    PRODUCT_ROUTE_OWNED_TOOL_NAMES,
    ProductTurnPreparer,
    TurnInput,
)
import p4_ipc  # P4-S11 MemoryPanel + ContextTrace IPC handlers
from observability.crash_reports import install_crash_reporter
from observability.metrics import render as render_metrics
from observability.startup import registry as startup_errors  # P3-S2

# Install the uncaught-exception hook as early as possible so import-time
# failures later in this file still land in crash_reports/.
install_crash_reporter()

logger = structlog.get_logger()

# P3-S7: ensure user data / cache / models directories exist before any
# subsystem tries to write into them. Also seeds <user_data>/config.toml
# from the bundle default on first run (via resolve_config_path).
_paths.ensure_user_dirs()

_CONFIG_PATH = resolve_config_path()
config = load_config(_CONFIG_PATH)
logger.info(
    "config_loaded",
    path=str(_CONFIG_PATH),
    exists=_CONFIG_PATH.is_file(),
    user_data_dir=str(_paths.user_data_dir()),
    user_models_dir=str(_paths.user_models_dir()),
    model_root=str(_paths.model_root()),
    # 2026-06-28 路径绑定可观测：portable=是否走 <install>/userdata；
    # env_pinned=Tauri 是否注入了 DESKPET_USER_DATA_DIR（单一事实源生效）。
    # 装机版路径漂移诊断就看这两个 + path 跨会话是否恒定。
    portable=_paths.is_portable_mode(),
    env_pinned=bool(os.environ.get("DESKPET_USER_DATA_DIR")),
)
PROJECT_ROOT = _CONFIG_PATH.parent
SHARED_SECRET = secrets.token_hex(16)

service_context = ServiceContext()
service_context.register("workflow_service", None)
service_context.register("run_execution_fence_acquirer", None)
service_context.register("provider_invocation_coordinator", None)
service_context.register("harness_public_read_service", None)


def _persist_supervisor_enabled(enabled: bool) -> bool:
    """Persist the runtime supervisor toggle into config.toml."""
    try:
        import tomlkit

        path = _CONFIG_PATH
        try:
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
        except Exception:
            doc = tomlkit.document()
        sup = doc.get("supervisor")
        if not hasattr(sup, "__setitem__"):
            sup = tomlkit.table()
            doc["supervisor"] = sup
        sup["enabled"] = bool(enabled)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tomlkit.dumps(doc), encoding="utf-8")
        try:
            config.raw.setdefault("supervisor", {})["enabled"] = bool(enabled)
        except Exception:
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("supervisor_toggle_persist_failed enabled=%s err=%s", enabled, exc)
        return False

# --- Register providers ---
from providers.openai_compatible import OpenAICompatibleProvider
from agent.providers.simple_llm import SimpleLLMAgent
from agent.providers.tool_using import ToolUsingAgent
from memory.sensitive_filter import RedactingMemoryStore
from tools.registry import ToolRegistry
from tools.get_time import get_time_tool
from tools.clipboard import read_clipboard_tool
from deskpet.tools.public_projection import (
    project_public_tool_arguments,
    project_public_tool_calls,
    project_public_tool_result,
)
from observability.vram import classify_tier
from router.hybrid_router import HybridRouter, LLMUnavailableError, RoutingStrategy
from billing.ledger import BillingLedger

from config import resolve_cloud_api_key as _resolve_cloud_api_key  # P2-1-S3
from deskpet.tools import ppt_tools
from deskpet.tools.ppt_outline_store import (
    expire_dangling_proposed,
    list_history,
    mark_status,
    save_outline,
)
from deskpet.tools.ppt_tools import _ppt_pro_cfg

# P4-S20-LLM-Unified: runtime overrides — settings panel 里改的会写到这个文件，
# 启动时读它覆盖 config.toml [llm] 段（只 base_url / model / temperature；
# api_key 走 keychain 不进文件）。允许用户改完不动 config.toml 也保留。
LLM_RUNTIME_PATH = _paths.user_data_dir() / "llm_runtime.json"


def _load_llm_runtime_overrides() -> dict:
    """Read llm_runtime.json — empty/missing/invalid → {}."""
    if not LLM_RUNTIME_PATH.exists():
        return {}
    try:
        import json as _json
        return _json.loads(LLM_RUNTIME_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_runtime_overrides_load_failed: %s", exc)
        return {}


def _save_llm_runtime_overrides(data: dict) -> None:
    """Write llm_runtime.json — best-effort, never raise."""
    import json as _json
    try:
        LLM_RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
        LLM_RUNTIME_PATH.write_text(
            _json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        logger.info("llm_runtime_overrides_saved path=%s", LLM_RUNTIME_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_runtime_overrides_save_failed: %s", exc)


# Agent 有效执行预算（默认 15 分钟，可在设置里调整）。等待用户确认、选择目录
# 或完成外部操作时由 durable Run 预算暂停，不再按整轮墙钟时间误取消。
def _chat_turn_timeout_s() -> float:
    try:
        m = float(_load_llm_runtime_overrides().get("chat_turn_timeout_minutes", 15))
    except Exception:  # noqa: BLE001
        m = 15.0
    return max(60.0, min(3600.0, m * 60.0))


# P4-S25 (2026-05-09): cross-endpoint Ollama fallback removed at
# user request. Single-endpoint mode — errors surface directly
# instead of auto-swapping to a different model with a different
# voice mid-turn.

# Apply runtime overrides BEFORE creating local_llm so the very first
# request after a restart uses the user's saved config.
_llm_overrides = _load_llm_runtime_overrides()
if _llm_overrides:
    if "base_url" in _llm_overrides:
        config.llm.local.base_url = _llm_overrides["base_url"]
    if "model" in _llm_overrides:
        config.llm.local.model = _llm_overrides["model"]
        # P-B 修复(同步 raw): 覆盖只写 dataclass,但有代码读 config.raw["llm"]["model"]
        # (压缩窗口 :1384 / stub :4268 / breakdown 探针 :2944)会拿到旧种子 gemma → 这里
        # 同步原始 dict,使所有 in-process raw 读取也读到有效模型。配合下方 effective_llm_model
        # 访问器(WI-3)双保险。见 plans/2026-06-16-effective-llm-model-resolution。
        try:
            config.raw.setdefault("llm", {})["model"] = _llm_overrides["model"]
        except Exception:  # noqa: BLE001
            pass
    if "base_url" in _llm_overrides:
        # base_url 也同步 raw(让 breakdown 探针的 base_url 预览也对;真出站本就用 dataclass)
        try:
            config.raw.setdefault("llm", {})["base_url"] = _llm_overrides["base_url"]
        except Exception:  # noqa: BLE001
            pass
    if "temperature" in _llm_overrides:
        config.llm.local.temperature = float(_llm_overrides["temperature"])
    if "api_key" in _llm_overrides and _llm_overrides["api_key"]:
        # plaintext stored in runtime json — usable but not recommended;
        # the proper place is OS keychain via SettingsPanel.
        config.llm.local.api_key = _llm_overrides["api_key"]
    logger.info(
        "llm_runtime_overrides_applied base_url=%s model=%s",
        config.llm.local.base_url, config.llm.local.model,
    )

# P4-S20-LLM-Unified: 统一 LLM endpoint。api_key 解析三档优先级：
#   1. 配置值如果以 "$" 开头 → 视作环境变量名，从 env 读 (e.g. "$OPENAI_API_KEY")
#   2. config.llm.local.api_key 是 "ollama" / "from-keychain" / 空 → 尝试从
#      keychain 通过 DESKPET_CLOUD_API_KEY env 读（向后兼容 P2-1-S3 的 Tauri
#      keychain 注入路径）；拿不到就用配置原值
#   3. 其它情况（用户写了真 key 在 config）→ 直接用配置值
def _resolve_llm_api_key(configured: str) -> str:
    if configured.startswith("$"):
        env_name = configured[1:]
        val = os.environ.get(env_name, "")
        if val:
            logger.info("llm_api_key_from_env env=%s", env_name)
            return val
        logger.warning(
            "llm_api_key_env_not_set env=%s — falling back to placeholder",
            env_name,
        )
        return configured  # placeholder; provider call will 401 if used
    # 占位符触发 keychain 读取
    placeholders = {"ollama", "from-keychain", "from-env", "", "your-key-here"}
    if configured in placeholders:
        keychain_key = _resolve_cloud_api_key()
        if keychain_key:
            logger.info(
                "llm_api_key_from_keychain",
                key_fp=hashlib.sha256(keychain_key.encode("utf-8")).hexdigest()[:8],
            )
            return keychain_key
        # 占位符 + 没 env → 保持占位符（Ollama 会接受任何值；云端会 401）
    return configured


_resolved_api_key = _resolve_llm_api_key(config.llm.local.api_key)
from deskpet.context_os_e2e_hooks import ContextOSE2EHooks as _ContextOSE2EHooks
_context_os_e2e_boot_hooks = _ContextOSE2EHooks.from_env()
_e2e_provider_base = (
    _context_os_e2e_boot_hooks.provider_url
    if _context_os_e2e_boot_hooks is not None
    and _context_os_e2e_boot_hooks.provider_url
    else ""
)

# 2026-05-17 deepseek-inline-cot-dsml-sanitize Strangler-Fig flag (default
# on). Read once; passed to every OpenAICompatibleProvider so setting
# [llm] sanitize_inline_cot_dsml = false instantly restores legacy raw
# passthrough after a restart (demo rollback).
_sanitize_cot_dsml = bool(
    (config.raw.get("llm") or {}).get("sanitize_inline_cot_dsml", True)
)
logger.info("sanitize_inline_cot_dsml_flag", enabled=_sanitize_cot_dsml)

local_llm = OpenAICompatibleProvider(
    base_url=_e2e_provider_base or config.llm.local.base_url,
    api_key="ctx-e2e-local-key" if _e2e_provider_base else _resolved_api_key,
    model="ctx-primary" if _e2e_provider_base else config.llm.local.model,
    temperature=config.llm.local.temperature,
    sanitize_inline_cot_dsml=_sanitize_cot_dsml,
)

# P4-S25 (2026-05-09): cross-endpoint Ollama fallback removed at user
# request — they don't want auto-swap to gemma4:e4b mid-turn because
# the model-style mismatch produces ugly mixed responses. Errors from
# the configured LLM endpoint now surface directly to the user.

# Keep cloud_llm symbol around for legacy code paths but it's None now —
# config.llm.cloud is no longer set under the unified [llm] schema.
cloud_llm = None
_current_cloud_api_key: str | None = (
    _resolved_api_key if "localhost" not in config.llm.local.base_url else None
)
if config.llm.cloud is not None:
    # 兼容旧 [llm.cloud] schema — 新部署不会走这里。
    _cloud_key = _resolve_cloud_api_key()
    if _cloud_key:
        _current_cloud_api_key = _cloud_key
        cloud_llm = OpenAICompatibleProvider(
            base_url=config.llm.cloud.base_url,
            api_key=_cloud_key,
            model=config.llm.cloud.model,
            temperature=config.llm.cloud.temperature,
            sanitize_inline_cot_dsml=_sanitize_cot_dsml,
        )

# P2-1-S8: BillingLedger — SQLite ledger of every chat_stream call + its
# cost in CNY. Its .create_hook() becomes the BudgetHook HybridRouter gates
# cloud calls through. Local calls bypass the hook entirely (they're free).
billing_ledger = BillingLedger(
    db_path=config.billing.db_path,
    pricing=config.billing.pricing,
    unknown_model_price_cny_per_m_tokens=config.billing.unknown_model_price_cny_per_m_tokens,
    daily_budget_cny=config.billing.daily_budget_cny,
    tz=ZoneInfo(config.billing.tz),
)
service_context.register("billing_ledger", billing_ledger)

# P5-S2 multi-provider-management Phase 2: LLMProviderRegistry
# 单一 source of truth for [[llm.providers]] 列表。Phase 0/1 已实现底层,
# 这里在启动时执行 legacy migration + 构造 registry,放到 service_context
# 供 ws handler (`settings_providers_*`) 读写。
from llm.provider_registry import (
    LLMProviderRegistry,
    ProviderMutationConflict,
    _migrate_legacy_provider_config,
)
from llm.relay_provider_ops import ensure_relay_provider, relay_logout

# Provider authority is created only after state.db migrations complete in
# lifespan. Importing main must never make a pre-migration registry usable.
_provider_registry: LLMProviderRegistry | None = None
service_context.register("provider_registry", None)
service_context.register("provider_routing_readiness", None)

llm = HybridRouter(
    local=local_llm,
    cloud=cloud_llm,
    strategy=RoutingStrategy(config.llm.strategy),
    # P2-1-S8: BillingLedger's hook debits cloud spend and denies cloud
    # calls once daily_budget_cny is exhausted. Local calls bypass the
    # hook entirely (free). See spec §1.1 / §2.4.
    budget_hook=billing_ledger.create_hook(),
)
service_context.register("llm_engine", llm)

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
    from deskpet.tools.registry import registry as deskpet_tool_registry_v2
    from deskpet.tools.os_tools import register_os_tools as _register_os_tools_v2
    from deskpet.permissions.gate import (
        PermissionGate as _PermissionGate,
        PermissionGateConfig as _PermissionGateConfig,
    )
    _register_os_tools_v2(deskpet_tool_registry_v2)
    # General-agent tools (glob, grep, web_search) registered now;
    # todo_write + agent need closures over runtime objects (SessionDB,
    # LLM shim) and are wired later, after those are constructed.
    from deskpet.tools.code_tools import register_code_tools as _register_code_tools
    _register_code_tools(deskpet_tool_registry_v2)
    _shell_deny = list(getattr(getattr(config, "permissions", None), "deny", {}).get("shell_patterns", []) or [
        "rm -rf /",
        "format c:",
        "del /f /s /q c:",
    ])
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
    deskpet_tool_registry_v2.set_tools_config_provider(
        lambda: getattr(config, "tools", None)
    )
    from deskpet.tools.capabilities import (
        ToolCapabilityBridgeService as _ToolCapabilityBridgeService,
        ToolCapabilityResolver as _ToolCapabilityResolver,
        ToolCapabilityScopeStore as _ToolCapabilityScopeStore,
    )
    _tool_capability_scope_store = _ToolCapabilityScopeStore()
    _tool_capability_resolver = _ToolCapabilityResolver(deskpet_tool_registry_v2)
    deskpet_tool_registry_v2.set_capability_scope_store(
        _tool_capability_scope_store
    )
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
            _ToolCapabilityBridgeService(
                deskpet_tool_registry_v2, _tool_capability_scope_store
            ),
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
        emit = bool(getattr(getattr(cfg, "verifier", None),
                            "emit_receipts", False))
        if not emit:
            return None
        if _receipt_store_box[0] is None:
            try:
                from deskpet.tools.receipt_store import ReceiptStore
                from pathlib import Path as _PathRS
                # WI-T2.2 v3 P0 修：取消 min(retention, 7) 截断（last-mile
                # round2 P0-2）。原代码把用户配的 30 天硬压成 7 天，回放窗口
                # 残缺。按 ToolsLastMileConfig.artifact_dir_retention_days
                # 默认 30 + 用户 cfg 真值。
                retention = int(getattr(
                    getattr(cfg, "last_mile", None),
                    "artifact_dir_retention_days", 30,
                ))
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
    _automode_path = (
        _Path(_paths.user_data_dir()) / "permissions_auto_mode.json"
    )
    # Module-level globals (service_context has a pre-declared key list
    # we don't want to extend just for this).
    # Accessed by the chat handler when it instantiates AgentLoop.

    # P4-S20 Stage C — marketplace installer + registry client.
    from deskpet.skills.marketplace import (
        RegistryClient as _RegistryClient,
        SkillInstaller as _SkillInstaller,
        SafetyError as _MarketplaceSafetyError,
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
    # In-memory pending stage map — UI confirms by staging_id.
    _skill_staged: dict[str, "Any"] = {}

    # P4-S20 Stage D — plugin system.
    from deskpet.plugins import PluginManager as _PluginManager
    _plugins_dir = _paths.user_data_dir() / "plugins"
    _plugins_dir.mkdir(parents=True, exist_ok=True)
    _enabled_plugins = list(
        getattr(getattr(config, "plugins", None), "enabled", []) or []
    )
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
    _skill_staged = {}
    plugin_manager = None

service_context.register(
    "tool_capability_scope_store", _tool_capability_scope_store
)
service_context.register("tool_capability_resolver", _tool_capability_resolver)

# S8 (R9): log the current hardware tier once so the dispatch decision is
# visible in the startup banner. The tier itself doesn't force provider
# swaps yet — that's Phase 2 work when we ship multiple LLM/TTS binaries.
_tier = classify_tier()
logger.info(
    "hardware_tier",
    tier=_tier.tier,
    recommended_llm=_tier.llm_model,
    recommended_tts=_tier.tts_model,
    recommended_asr=_tier.asr_model,
)

from deskpet.voice_runtime import configure_legacy_voice_runtime

_voice_runtime = configure_legacy_voice_runtime(
    config=config,
    service_context=service_context,
    permission_gate=globals().get("permission_gate_v2"),
    logger=logger,
)

# 记忆系统升级 WI-M1.2/M1.7: facts 抽取 / reflection 需要一个
# (prompt:str)->str 的 LLMCall —— summarizer.make_llm_call 走的是
# messages->dict 形状，不匹配。这里把 provider 适配成纯字符串形状。
def _make_str_llm_call(
    provider,
    *,
    max_tokens: int = 512,
    response_format=None,
    purpose: str = "auxiliary_unknown",
):
    """Adapt an OpenAICompatibleProvider into a ``(prompt: str) -> str``
    async callable. provider=None → return None so callers can skip
    (用户未配置 / 离线时 facts/reflection 静默跳过，不报错)。

    response_format（M-3，plans/2026-06-24-...）：可选 json_schema structured-output
    透传给 chat_with_tools。留 None = 旧行为（不传，字节级 BC）；非 None 时供七步流水线
    intent/contradiction 合并预分析绑定 schema（中转站 thinking-model 仍可能 400，调用方各自 safe-fail）。
    """
    if provider is None:
        return None

    async def _call(prompt: str) -> str:
        from agent.context_messages import provider_purpose_scope

        # WI-4-C（2026-06-25 真测）：relay 对 `stream:True + json_schema strict` 偶发慢(9-20s,
        # 偶尔超 analysis_timeout_s) 或上游瞬时错返空 body → 预分析被 safe-fail 误降级。直连实测
        # **非流式裸补全（无 strict schema）稳定 5-6s**（launcher 已 NO_PROXY）。故带 schema（=七步
        # 预分析专用路径）时**非流式裸补全优先**（模型仍按提示出 JSON，调用方 _extract_json 3 级容错
        # +控制字符净化接住），空/异常才回退流式+schema；relay 整体坏窗两条都空时由上层 safe-fail 兜。
        _msgs = [{"role": "user", "content": prompt}]
        if response_format is not None:
            try:
                # 非流式**保留 strict schema**：probe 实测 deepseek 非流式+schema 4-6s 稳，
                # 且 schema 的 enum 约束保证 problem_type 分类准（丢 schema 会让 deepseek 把
                # 清晰 debug 误判 chitchat → 变相重现 BUG-B，真测 2026-06-25 实证）。
                with provider_purpose_scope(purpose):
                    result = await provider._legacy_chat_with_tools_nonstream(
                        messages=_msgs, max_tokens=max_tokens, temperature=0.2,
                        response_format=response_format,
                    )
                content = (result or {}).get("content") or ""
                if content.strip():
                    return content
            except Exception:  # noqa: BLE001 — 非流式失败回退流式
                pass
            with provider_purpose_scope(purpose):
                result = await provider.chat_with_tools(
                    messages=_msgs, max_tokens=max_tokens, temperature=0.2,
                    response_format=response_format,
                )
            return (result or {}).get("content") or ""
        # 无 schema（facts/reflection 等）：保持流式，字节级 BC。
        with provider_purpose_scope(purpose):
            result = await provider.chat_with_tools(
                messages=_msgs, max_tokens=max_tokens, temperature=0.2,
            )
        return (result or {}).get("content") or ""

    return _call


def _make_live_str_llm_call(
    provider_resolver,
    *,
    max_tokens: int = 512,
    response_format=None,
    purpose: str = "auxiliary_unknown",
):
    """Build an auxiliary LLM callable that follows provider hot-swaps.

    Relay login updates the module-level ``local_llm`` after boot. Long-lived
    services such as FactExtractor and IntentTriage must therefore resolve the
    provider for every invocation instead of retaining the placeholder provider
    captured during composition.
    """

    async def _call(prompt: str) -> str:
        provider = provider_resolver()
        delegate = _make_str_llm_call(
            provider,
            max_tokens=max_tokens,
            response_format=response_format,
            purpose=purpose,
        )
        if delegate is None:
            raise RuntimeError("live LLM provider is unavailable")
        return await delegate(prompt)

    return _call


def _make_session_aware_llm_call(
    *,
    max_tokens: int = 512,
    response_format=None,
):
    """Create the explicit auxiliary protocol without capturing a provider."""

    from deskpet.execution.provider_workloads import SessionAwareLLMCall

    return SessionAwareLLMCall(
        lambda: service_context.get("provider_workload_router"),
        max_tokens=max_tokens,
        response_format=response_format,
    )


def _make_research_llm_call_v2(provider):
    """Adapt the active provider to the usage-bearing v5 research contract."""
    if provider is None:
        return None

    async def _call(
        prompt: str,
        *,
        max_output_tokens: int,
        stable_call_id: str,
        response_format=None,
    ):
        if not stable_call_id:
            raise ValueError("stable_call_id is required")
        from agent.context_messages import provider_purpose_scope
        from deskpet.tools.research_tools import _research_llm_result_from_provider_response

        with provider_purpose_scope("research"):
            result = await provider.chat_with_tools_at_most_once(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=max_output_tokens,
                response_format=response_format,
            )
        return _research_llm_result_from_provider_response(
            result,
            fallback_model=str(getattr(provider, "model", "") or "unknown"),
        )

    return _call


def _resolve_ephemeral_provider(base_provider, model_name: str):
    """Clone ``base_provider`` with its model overridden to ``model_name``.

    接 ``[tools.verifier].ephemeral_subagent_model``（D6 第 3 次失败救援模型）。
    中转站按 model id 路由，故只换 model、复用 base_provider 的
    base_url/api_key/temperature/sanitize（与 ``effective_llm_model``
    同思路：配置里写啥模型就出站啥模型，不做隐式改写）。

    回退 ``base_provider``（即旧行为 = 复用主 LLM）的情形：
      * ``base_provider is None`` —— 调用方整体跳过 ephemeral；
      * ``model_name`` 空 / 仅空白 —— 缺省，回退主 LLM（保持 BC）；
      * ``model_name == base_provider.model`` —— 已是目标模型，免重复构造；
      * 克隆失败（任意异常）—— 兜底回退主 LLM，绝不让 verify-gate 接电崩。
    """
    if base_provider is None:
        return None
    # Context OS E2E uses a strict loopback provider catalog. Auxiliary
    # classifiers/verifiers must stay on ctx-primary; cloning an arbitrary
    # configured model here would bypass the hermetic provider-chain override.
    if _e2e_provider_base:
        return base_provider
    name = (model_name or "").strip()
    if not name:
        return base_provider
    try:
        if getattr(base_provider, "model", None) == name:
            return base_provider
        return OpenAICompatibleProvider(
            base_url=base_provider.base_url,
            api_key=base_provider.api_key,
            model=name,
            temperature=getattr(base_provider, "temperature", 0.2),
            sanitize_inline_cot_dsml=getattr(
                base_provider, "sanitize_inline_cot_dsml", _sanitize_cot_dsml
            ),
        )
    except Exception as exc:  # noqa: BLE001 — 克隆失败兜底回退主 LLM（保 BC）
        logger.warning(
            "ephemeral provider clone failed (model=%s): %s — falling back to base LLM",
            name, exc,
        )
        return base_provider


# 把运行中的 local_llm(relay base_url + keychain key) 注入 deep-research，
# 让 deepresearch 的 plan/synthesize/reflection 走和聊天 agent 同一个 live
# relay（修旧 _resolve_default_llm_call 读 providers[0] 丢 key 的隐患）。
try:
    from deskpet.tools import research_tools as _research_tools
    _research_tools.set_live_llm_call(
        _make_str_llm_call(local_llm, max_tokens=4096, purpose="research")
    )
    _research_tools.set_live_llm_call_v2(_make_research_llm_call_v2(local_llm))

    # LLM 重排桥: 用【廉价模型】(默认 gpt-4.1-mini,中转站有)做 research 召回后的
    # cross-encoder 式精排 —— 免下载本地 bge-reranker、免占本地内存,复用 relay。
    # 同 base_url + keychain key,只换 model。[research].reranker_model 可覆盖。
    # 仅在云端/relay base_url 下注入: 本地 ollama(localhost) 通常没有 gpt-4.1-mini,
    # 会每次研究都失败白调,故 localhost 端点不注入(research 自动跳过精排)。
    try:
        _r_base = str(config.llm.local.base_url or "")
        if _r_base and not _research_tools._is_loopback_url(_r_base):
            _rerank_model = str(
                (config.raw.get("research") or {}).get("reranker_model", "gpt-4.1-mini")
            )
            _rerank_provider = OpenAICompatibleProvider(
                base_url=_r_base,
                api_key=_resolved_api_key,
                model=_rerank_model,
            )
            _research_tools.set_rerank_llm_call(
                _make_str_llm_call(
                    _rerank_provider, max_tokens=2048, purpose="research"
                )
            )
    except Exception as _exc2:  # noqa: BLE001 — 未注入则 research 跳过精排
        logger.debug("research_rerank_wiring_skipped", error=str(_exc2))
except Exception as _exc:  # noqa: BLE001 — research 仍可回退 config 重建
    logger.debug("research_live_llm_wiring_skipped", error=str(_exc))


# ─── superpowers Layer 1A/1B 决策2：plan-confirm 硬门 ────────────────
# 任务计划确认统一由 Harness durable admission decision 处理。
def _resolve_relay_registry_provider():
    """Return the current relay provider from the provider registry."""
    try:
        _reg = service_context.get("provider_registry")
        if _reg is None:
            return None
        from llm.relay_provider_ops import RELAY_PROVIDER_ID

        _entry = _reg.get_entry(RELAY_PROVIDER_ID)
        if _entry is None or not bool(getattr(_entry, "enabled", False)):
            return None
        _api_key = _reg.resolve_api_key(_entry.id)
        if not _api_key:
            return None
        return OpenAICompatibleProvider(
            base_url=_entry.base_url,
            api_key=_api_key,
            model=_entry.model,
            temperature=getattr(_entry, "temperature", 0.7),
            sanitize_inline_cot_dsml=_sanitize_cot_dsml,
            is_relay=(getattr(_entry, "source", "") == "relay"),
        )
    except Exception as _exc:  # noqa: BLE001
        logger.debug("research_relay_provider_resolve_skipped", error=str(_exc))
    return None


def _refresh_image_endpoint_resolver() -> None:
    """Keep image/PPT generation aligned with the active relay provider."""
    try:
        from deskpet.tools import image_tools as _image_tools

        def _resolver() -> tuple[str | None, str | None] | None:
            _provider = _resolve_relay_registry_provider()
            if _provider is None:
                return None
            return (
                str(getattr(_provider, "base_url", "") or ""),
                str(getattr(_provider, "api_key", "") or ""),
            )

        _image_tools.set_endpoint_resolver(_resolver)
        logger.info("image_endpoint_resolver_wired")
    except Exception as _exc:  # noqa: BLE001
        logger.debug("image_endpoint_resolver_wiring_skipped", error=str(_exc))


def _refresh_research_live_llm(provider=None) -> None:
    """Keep deep-research/PPT background LLM aligned with chat routing."""
    try:
        from deskpet.tools import research_tools as _research_tools

        _provider = provider or _resolve_relay_registry_provider() or local_llm
        _research_tools.set_live_llm_call(
            _make_str_llm_call(_provider, max_tokens=4096, purpose="research")
        )
        _research_tools.set_live_llm_call_v2(_make_research_llm_call_v2(_provider))

        try:
            _r_base = str(getattr(_provider, "base_url", "") or "")
            if _r_base and not _research_tools._is_loopback_url(_r_base):
                _rerank_model = str(
                    (config.raw.get("research") or {}).get("reranker_model", "gpt-4.1-mini")
                )
                _rerank_provider = OpenAICompatibleProvider(
                    base_url=_r_base,
                    api_key=getattr(_provider, "api_key", ""),
                    model=_rerank_model,
                    sanitize_inline_cot_dsml=_sanitize_cot_dsml,
                )
                _research_tools.set_rerank_llm_call(
                    _make_str_llm_call(
                        _rerank_provider, max_tokens=2048, purpose="research"
                    )
                )
        except Exception as _exc2:  # noqa: BLE001
            logger.debug("research_rerank_wiring_skipped", error=str(_exc2))

        logger.info(
            "research_live_llm_refreshed base_url=%s model=%s",
            getattr(_provider, "base_url", ""),
            getattr(_provider, "model", ""),
        )
    except Exception as _exc:  # noqa: BLE001
        logger.debug("research_live_llm_wiring_skipped", error=str(_exc))


_refresh_image_endpoint_resolver()
_refresh_research_live_llm()


# ─── WI-4.3 技能自创闭环 hook（方案 B 抽 helper）─────────────────────
# 2026-06-06：原 codify hook 只 inline 在 chat handler 的 FinalEvent 分支 →
# turn 经 ErrorEvent（relay ReadError）/ 迭代上限 / 被 stop 中止结束时整段不跑
# → 多工具 turn 跑了 ≥5 工具但卡不弹（真机诊断）。抽成模块级 helper，在
# FinalEvent + ErrorEvent 两处都调，让 turn 任意路径结束都能触发技能自创。
# safe-fail：任何异常只 debug log，不中断正常 turn。flag OFF → 整段跳过（BC）。
_companion_store = None
_growth_authority_router = None
_companion_identity_gate = None
_companion_profile_coordinator = None
_companion_control_ingress = None
_companion_runtime = None
_companion_notification_service = None
_companion_detail_query = None
_companion_ingress_dispatcher = None
_companion_growth_pipeline = None
_companion_activation_dispatcher = None
_companion_ingress_tasks: set[asyncio.Task] = set()
_growth_cutover_coordinator = None
_growth_cutover_plan = None
_LEGACY_GROWTH_OWNER = ("legacy_local_profile", 1)


def _legacy_growth_owner() -> tuple[str, int]:
    return _LEGACY_GROWTH_OWNER


async def _initialize_growth_authority() -> None:
    """Compose the one durable Companion authority without opening ingress."""

    global _companion_store, _growth_authority_router
    global _companion_identity_gate, _companion_profile_coordinator
    global _companion_control_ingress
    global _companion_runtime, _companion_ingress_dispatcher
    global _companion_growth_pipeline, _companion_activation_dispatcher
    global _growth_cutover_coordinator, _growth_cutover_plan
    global _LEGACY_GROWTH_OWNER
    from deskpet.companion.authority import (
        GrowthAuthorityPhase,
        GrowthAuthorityRouter,
        GrowthIngressKind,
    )
    from deskpet.companion.control_credentials import (
        WindowControlCredentialVerifier,
    )
    from deskpet.companion.control_ingress import (
        CompanionControlIngress,
        RegistryRelayAuthSnapshotProvider,
    )
    from deskpet.companion.identity import (
        ProfileBindingCoordinator,
        load_or_create_local_identity,
    )
    from deskpet.companion.identity_gate import IdentityReadyGate
    from deskpet.companion.contracts import OwnerRef
    from deskpet.companion.cutover import (
        CompanionGrowthAuthorityAdapter,
        DEFAULT_POST_MARKER_STEPS,
        DEFAULT_PRE_MARKER_STEPS,
        GrowthAuthorityCutoverCoordinator,
        GrowthCutoverPlan,
    )
    from deskpet.companion.cutover_production import (
        ProductionGrowthCutoverStepExecutor,
        RetiredLegacyGrowthAuthority,
        import_legacy_growth_snapshot,
        read_capability_owner_cutover_snapshot,
        read_legacy_growth_snapshot,
        require_legacy_skill_immutable_migration,
    )
    from deskpet.companion.preferences import PreferencePolicy, PreferenceResolver
    from deskpet.companion.clock import select_companion_clock
    from deskpet.companion.runtime import (
        CompanionJobResult,
        CompanionRuntime,
        CompanionRuntimePolicy,
        ForegroundActivityGate,
    )
    from deskpet.companion.reminder_tools import (
        LEGACY_REMINDER_LIST_TOOL_NAME,
        REMINDER_CANCEL_TOOL_NAME,
        REMINDER_CREATE_TOOL_NAME,
        REMINDER_LIST_TOOL_NAME,
        ensure_companion_reminder_tools,
        register_legacy_reminder_tool,
        restore_durable_companion_reminder_tools,
    )
    from deskpet.companion.reminders import (
        ReminderRunAuthority,
        ReminderScheduler,
        ReminderSchedulerPolicy,
        ReminderService,
    )
    from deskpet.companion.store import CompanionStore, canonical_hash

    companion_clock = select_companion_clock(
        warning_sink=lambda reason: logger.warning(
            "companion_clock_selection_warning", reason=reason
        )
    )
    store = CompanionStore(
        _paths.user_data_dir() / "data" / "companion.db",
        clock=companion_clock.now_utc,
    )
    memory_recall_query = service_context.get("memory_recall_query")
    if memory_recall_query is not None and deskpet_tool_registry_v2 is not None:
        from deskpet.tools.memory_recall import (
            CompanionRunMemoryScopeResolver,
            register_memory_recall,
        )

        if deskpet_tool_registry_v2.get("memory_recall") is None:
            register_memory_recall(
                deskpet_tool_registry_v2,
                memory_recall_query,
                CompanionRunMemoryScopeResolver(store),
            )
        service_context.register(
            "memory_recall_scope_resolver",
            CompanionRunMemoryScopeResolver(store),
        )
    preference_resolver = PreferenceResolver(
        store,
        policy=PreferencePolicy.from_growth_config(config.companion.growth),
    )
    service_context.register(
        "companion_preference_resolver_dormant",
        preference_resolver,
    )
    local_identity = load_or_create_local_identity(_paths.user_data_dir())
    store.adopt_legacy_local_identity(
        identity_namespace_hash=local_identity.identity_namespace_hash
    )
    local_owner = store.resolve_active_profile(
        identity_namespace_hash=local_identity.identity_namespace_hash
    )
    if local_owner is None:
        local_owner = store.create_profile(
            profile_id=_LEGACY_GROWTH_OWNER[0],
            generation=store.next_profile_generation(
                profile_id=_LEGACY_GROWTH_OWNER[0]
            ),
            identity_namespace_hash=local_identity.identity_namespace_hash,
            reason_code="task1_legacy_authority_bootstrap",
        )
    _LEGACY_GROWTH_OWNER = (
        local_owner.profile_id,
        local_owner.profile_generation,
    )
    store.cleanup_expired_control_challenges()
    reminder_service = ReminderService(store, clock=companion_clock)

    class _ReminderPolicyProvider:
        def snapshot(self, _owner):
            return ReminderSchedulerPolicy(
                proactive_enabled=bool(
                    config.companion.growth.proactive_enabled
                ),
                paused=bool(config.companion.growth.paused),
                timezone=str(config.companion.growth.timezone),
                quiet_hours_start=str(
                    config.companion.growth.quiet_hours_start
                ),
                quiet_hours_end=str(
                    config.companion.growth.quiet_hours_end
                ),
            )

    reminder_scheduler = ReminderScheduler(
        store,
        policy_provider=_ReminderPolicyProvider(),
        clock=companion_clock,
    )
    pending_reminder_dispatches = {}

    async def _settle_ready_reminder(owner, dispatch):
        if dispatch.draft_job_id is not None:
            job = store.get_job(owner, job_id=dispatch.draft_job_id)
            if str(job.get("status") or "") != "succeeded":
                return False
        outbox_claim = reminder_scheduler.claim_delivery_outbox(
            owner,
            claim_owner=(
                f"reminder-delivery:{owner.profile_id}:"
                f"{owner.profile_generation}"
            ),
        )
        if outbox_claim is None:
            return False
        if outbox_claim.item_id != dispatch.outbox_id:
            store.retry_outbox(
                owner,
                outbox_id=outbox_claim.item_id,
                claim_owner=outbox_claim.claim_owner,
                claim_epoch=outbox_claim.claim_epoch,
                retry_at=companion_clock.now_utc().isoformat(),
                reason_code="reminder_delivery_order_retry",
            )
            return False
        reminder_scheduler.settle(
            owner,
            dispatch=dispatch,
            outbox_claim=outbox_claim,
            result_hash=canonical_hash(
                ["reminder_local_projection", dispatch.outbox_id]
            ),
        )
        pending_reminder_dispatches.pop(dispatch.outbox_id, None)
        if _companion_notification_service is not None:
            frozen = identity_gate.freeze()
            if frozen.owner == owner:
                await _companion_notification_service.bind_and_drain(
                    frozen
                )
        return True

    async def _run_reminder_lane(owner):
        for dispatch in tuple(pending_reminder_dispatches.values()):
            if await _settle_ready_reminder(owner, dispatch):
                return
        dispatch = await reminder_scheduler.tick(
            owner,
            claim_owner=(
                f"reminder-scheduler:{owner.profile_id}:"
                f"{owner.profile_generation}"
            ),
        )
        if dispatch is None:
            return
        pending_reminder_dispatches[dispatch.outbox_id] = dispatch
        await _settle_ready_reminder(owner, dispatch)

    def _request_owner(request) -> OwnerRef:
        return OwnerRef(request.profile_id, request.profile_generation)

    def _read_preferences(request):
        relevant = request.payload.get("relevant_keys")
        relevant_keys = (
            tuple(str(item) for item in relevant)
            if isinstance(relevant, (list, tuple))
            else None
        )
        return preference_resolver.resolve(
            _request_owner(request),
            request_id=str(request.payload.get("request_id") or "growth-router"),
            relevant_keys=relevant_keys,
        )

    def _read_reminders(request):
        return reminder_service.list(
            ReminderRunAuthority(
                owner=_request_owner(request),
                timezone=str(config.companion.growth.timezone),
                quiet_policy={"enabled": False},
            ),
            args=dict(request.payload.get("args") or {}),
        )

    companion = CompanionGrowthAuthorityAdapter(
        readers={
            GrowthIngressKind.PREFERENCE_READ: _read_preferences,
            GrowthIngressKind.REMINDER_READ: _read_reminders,
        },
        # Preference evidence, candidate creation and Reminder mutation enter
        # through their typed Task-4/8/11 services. There is deliberately no
        # generic payload-driven writer behind this compatibility router.
        writers={},
    )
    router = GrowthAuthorityRouter(
        store=store,
        legacy=RetiredLegacyGrowthAuthority(),
        companion=companion,
    )
    await router.start()
    _companion_store = store
    _growth_authority_router = router
    identity_gate = IdentityReadyGate()
    workflow_service = service_context.get("workflow_service")
    execution_state = (
        None if workflow_service is None else workflow_service.execution_uow
    )
    # A missing port keeps the dormant gate fail-closed. Task 13 must rebind
    # the live execution projection before enabling Companion authority.

    async def _dormant_job_handler(_owner, _claim):
        # Task 13 replaces this only after the durable authority cutover.
        return CompanionJobResult(
            status="failed",
            reason_code="companion_authority_not_cut_over",
        )

    growth = config.companion.growth
    companion_runtime = CompanionRuntime(
        store=store,
        identity_gate=identity_gate,
        foreground_gate=ForegroundActivityGate(
            execution_state,
            clock=companion_clock,
        ),
        handler=_dormant_job_handler,
        clock=companion_clock,
        policy=CompanionRuntimePolicy(
            enabled=bool(growth.enabled),
            paused=bool(growth.paused),
            max_children=max(
                1,
                int(growth.max_concurrent_reflections),
                int(growth.max_concurrent_evaluations),
            ),
            max_retries=int(growth.max_retries),
            max_job_tokens=max(
                int(growth.reflection_token_budget),
                int(growth.evaluation_token_budget),
            ),
            max_job_ms=max(
                int(growth.reflection_timeout_seconds),
                int(growth.evaluation_timeout_seconds),
            )
            * 1000,
            daily_token_budget=(
                int(growth.reflection_token_budget)
                + int(growth.evaluation_token_budget)
            ),
            daily_time_budget_ms=(
                int(growth.reflection_timeout_seconds)
                + int(growth.evaluation_timeout_seconds)
            )
            * 1000,
            quiet_hours_start=str(growth.quiet_hours_start),
            quiet_hours_end=str(growth.quiet_hours_end),
            timezone=str(growth.timezone),
            claim_kinds=(
                "reflection",
                "candidate_build",
                "evaluation",
                "delegated_task",
            ),
            retryable_kinds=(
                "reflection",
                "candidate_build",
                "evaluation",
                "delegated_task",
            ),
        ),
        authority_active=lambda: (
            router.current.phase is GrowthAuthorityPhase.COMPANION
        ),
        scheduled_work_hook=_run_reminder_lane,
    )
    profile_coordinator = ProfileBindingCoordinator(
        store=store,
        gate=identity_gate,
        runtime=companion_runtime,
        runtime_start_enabled=True,
    )
    _companion_runtime = companion_runtime
    _companion_identity_gate = identity_gate
    _companion_profile_coordinator = profile_coordinator
    capability_platform = service_context.get("capability_platform")
    if capability_platform is None:
        raise RuntimeError(
            "capability platform is unavailable for Companion identity binding"
        )
    capability_platform.bind_execution_identity_gate(identity_gate)
    from deskpet.companion.activation import ActivationDispatcher
    from deskpet.companion.activation_platform import (
        CapabilityManagerStaticLifecyclePort,
        CompanionCapabilityMutationPlatform,
        CompanionStoreActivationDecisionProofResolver,
        CompanionStoreLifecycleCandidateSourceResolver,
    )
    from deskpet.companion.production_pipeline import (
        FirstPartyGrowthTargetResolver,
        GrowthProductionPipeline,
    )

    if capability_platform.lifecycle_static_port is not None:
        raise RuntimeError(
            "capability lifecycle static port is already bound"
        )
    candidate_sources = CompanionStoreLifecycleCandidateSourceResolver(
        store,
        cache_root=(
            _paths.user_data_dir()
            / "capabilities"
            / "companion-growth-candidates"
        ),
    )
    capability_platform.lifecycle_static_port = (
        CapabilityManagerStaticLifecyclePort(
            manager=capability_platform.manager,
            candidate_sources=candidate_sources,
            management_policy="user_managed",
        )
    )
    decision_proofs = CompanionStoreActivationDecisionProofResolver(store)
    mutation_platform = CompanionCapabilityMutationPlatform(
        platform=capability_platform,
        decision_proofs=decision_proofs,
    )
    activation_dispatcher = ActivationDispatcher(
        store=store,
        platform=mutation_platform,
        barrier=capability_platform.revocation_barrier,
    )
    _companion_activation_dispatcher = activation_dispatcher
    managed_projection = service_context.get(
        "managed_skill_discovery_projection"
    )
    if managed_projection is None or execution_state is None:
        raise RuntimeError(
            "Companion growth pipeline dependencies are unavailable"
        )
    target_resolver = FirstPartyGrowthTargetResolver(
        projection=managed_projection,
        capability_platform=capability_platform,
    )
    companion_session_db = service_context.get("session_db")
    if companion_session_db is None:
        raise RuntimeError("Companion reminder grounding requires SessionDB")

    async def _load_reminder_source_messages(_owner, source_refs):
        loaded = {}
        for source_ref in source_refs:
            prefix, session_id, message_id = str(source_ref).rsplit(":", 2)
            if prefix != "message" or not session_id:
                raise ValueError("reminder_source_message_ref_invalid")
            loaded[str(source_ref)] = (
                await companion_session_db.read_companion_ingress_message_content(
                    session_id=session_id,
                    message_id=int(message_id),
                )
            )
        return loaded

    _companion_growth_pipeline = GrowthProductionPipeline(
        store=store,
        target_resolver=target_resolver,
        execution_database_path=execution_state.path,
        tool_registry=deskpet_tool_registry_v2,
        revocation_barrier=capability_platform.revocation_barrier,
        activation_dispatcher=activation_dispatcher,
        reminder_source_loader=_load_reminder_source_messages,
        clock=companion_clock,
        task_workspace=(
            _paths.user_data_dir() / "companion" / "candidate-workspace"
        ),
    )
    service_context.register("companion_identity_gate", identity_gate)
    service_context.register(
        "companion_profile_coordinator", profile_coordinator
    )
    service_context.register("companion_clock", companion_clock)
    service_context.register("companion_runtime", companion_runtime)
    service_context.register(
        "companion_growth_pipeline",
        _companion_growth_pipeline,
    )
    session_db = companion_session_db
    if session_db is None:
        raise RuntimeError("Companion ingress requires SessionDB")
    from deskpet.companion.signals import CompanionIngressOutboxDispatcher

    _companion_ingress_dispatcher = CompanionIngressOutboxDispatcher(
        session_db=session_db,
        store=store,
        runtime=companion_runtime,
        reflection_target_catalog=(
            _companion_growth_pipeline.target_catalog()
        ),
    )
    service_context.register(
        "companion_ingress_dispatcher", _companion_ingress_dispatcher
    )
    if _WINDOW_CONTROL_BOOTSTRAP is not None:
        verifier = WindowControlCredentialVerifier(
            public_key_hex=_WINDOW_CONTROL_BOOTSTRAP.public_key_hex,
            backend_process_instance_id=(
                _WINDOW_CONTROL_BOOTSTRAP.backend_process_instance_id
            ),
        )
        _companion_control_ingress = CompanionControlIngress(
            store=store,
            coordinator=profile_coordinator,
            identity_gate=identity_gate,
            verifier=verifier,
            user_data_dir=str(_paths.user_data_dir()),
            trusted_auth_provider=RegistryRelayAuthSnapshotProvider(
                lambda: service_context.get("provider_registry")
            ),
        )
        service_context.register(
            "window_control_credential_verifier", verifier
        )
    else:
        _companion_control_ingress = None
        service_context.register("window_control_credential_verifier", None)
    # ServiceContext Task-2 typing is intentionally not pulled forward. The
    # preparer reads this host-only attribute with getattr.
    service_context.growth_authority_router = router

    class _ReminderAuthoritySelector:
        async def resolve(self, context):
            frozen = identity_gate.freeze()
            if (
                context.owner_key
                and context.owner_key != frozen.owner_key
            ) or (
                context.profile_generation
                and context.profile_generation
                != frozen.owner.profile_generation
            ):
                raise RuntimeError("reminder_owner_context_stale")
            # The model cannot supply grounding references. Read the three
            # latest prior user messages from the trusted session ledger and
            # exclude the current reminder Run by its host-owned root id.
            rows = await session_db.get_recent_messages(
                context.session_id,
                limit=100,
            )
            prior_user_rows = [
                row
                for row in rows
                if str(row.get("role") or "") == "user"
                and str(row.get("root_run_id") or "")
                != str(context.root_run_id or "")
            ]
            source_refs = tuple(
                f"message:{context.session_id}:{int(row['id'])}"
                for row in prior_user_rows[-3:]
            )
            return ReminderRunAuthority(
                owner=frozen.owner,
                timezone=str(config.companion.growth.timezone),
                quiet_policy={
                    "enabled": True,
                    "mode": "defer",
                    "start": str(config.companion.growth.quiet_hours_start),
                    "end": str(config.companion.growth.quiet_hours_end),
                    "timezone": str(config.companion.growth.timezone),
                },
                source_message_refs=source_refs,
            )

    reminder_selector = _ReminderAuthoritySelector()
    service_context.register("companion_reminder_service", reminder_service)
    service_context.register(
        "companion_reminder_authority_selector", reminder_selector
    )

    registry = deskpet_tool_registry_v2
    if registry is None:
        raise RuntimeError("growth cutover requires ToolRegistry V2")
    if registry.authority_phase == "legacy":
        if registry.get(LEGACY_REMINDER_LIST_TOOL_NAME) is None:
            class _RetiredLegacyReminderProjection:
                async def invoke(self, **_kwargs):
                    return ""

            register_legacy_reminder_tool(
                registry,
                _RetiredLegacyReminderProjection(),
            )
    elif registry.authority_phase != "companion":
        raise RuntimeError("unknown reminder registry authority phase")

    capability_platform = service_context.get("capability_platform")
    if capability_platform is None:
        raise RuntimeError("growth cutover requires CapabilityPlatform")
    capability_owner_key = profile_coordinator.owner_key(local_owner)
    capability_owner_scope_key = local_owner.profile_id
    capability_snapshot = await read_capability_owner_cutover_snapshot(
        capability_store=capability_platform.store,
        owner_key=capability_owner_key,
        scope_key=capability_owner_scope_key,
    )

    state_db_path = globals().get(
        "_state_db_path",
        _paths.user_data_dir() / "data" / "state.db",
    )
    legacy_snapshot = read_legacy_growth_snapshot(
        state_db_path=state_db_path,
        preference_path=_paths.user_data_dir() / "preference_memory.json",
        skill_roots=(_paths.user_data_dir() / "skills" / "user",),
    )
    recovered_state = router.current
    migration_hash = (
        str(recovered_state.migration_hash)
        if recovered_state.migration_hash
        else legacy_snapshot.migration_hash
    )
    operation_id = (
        str(recovered_state.cutover_operation_id)
        if recovered_state.cutover_operation_id
        else f"growth-cutover-v1:{migration_hash[:24]}"
    )
    all_steps = (*DEFAULT_PRE_MARKER_STEPS, *DEFAULT_POST_MARKER_STEPS)
    step_hashes = {
        step: canonical_hash(
            {
                "schema_version": 1,
                "operation_id": operation_id,
                "migration_hash": migration_hash,
                "step": step,
            }
        )
        for step in all_steps
    }
    cutover_plan = GrowthCutoverPlan(
        cutover_operation_id=operation_id,
        migration_version=1,
        migration_hash=migration_hash,
        legacy_owner_profile_id=_LEGACY_GROWTH_OWNER[0],
        legacy_owner_generation=_LEGACY_GROWTH_OWNER[1],
        # This cutover does not publish a Capability.  With no legacy Skill
        # mutation to perform, old and new facts are the same real Store
        # snapshot.  A non-empty legacy Skill is rejected before the marker.
        old_binding_generation=capability_snapshot.binding_generation,
        new_binding_generation=capability_snapshot.binding_generation,
        old_owner_binding_set_stamp=(
            capability_snapshot.owner_binding_set_stamp
        ),
        new_owner_binding_set_stamp=(
            capability_snapshot.owner_binding_set_stamp
        ),
        step_hashes=step_hashes,
    )

    async def _require_unchanged_capability_snapshot():
        current = await read_capability_owner_cutover_snapshot(
            capability_store=capability_platform.store,
            owner_key=capability_owner_key,
            scope_key=capability_owner_scope_key,
        )
        if current != capability_snapshot:
            raise RuntimeError("growth_cutover_capability_snapshot_drift")
        return current

    def _legacy_import(_authorization):
        return import_legacy_growth_snapshot(
            store=store,
            preference_resolver=preference_resolver,
            owner=OwnerRef(*_LEGACY_GROWTH_OWNER),
            snapshot=legacy_snapshot,
        )

    def _inactive_stage(_authorization):
        proof = require_legacy_skill_immutable_migration(legacy_snapshot)
        return {
            **proof,
            "activation_allowed": False,
            "migration_hash": migration_hash,
        }

    async def _preflight(_authorization):
        current = await _require_unchanged_capability_snapshot()
        return {
            "registry_phase": registry.authority_phase,
            "legacy_handler_present": (
                registry.get(LEGACY_REMINDER_LIST_TOOL_NAME) is not None
            ),
            "companion_runtime_closed": not identity_gate.ready,
            "binding_generation": current.binding_generation,
            "owner_binding_set_stamp": current.owner_binding_set_stamp,
        }

    async def _capability_publish_intent(_authorization):
        current = await _require_unchanged_capability_snapshot()
        return {
            "capability_mutation": "none",
            "reason_code": "no_legacy_skill_mutation",
            "binding_generation": current.binding_generation,
            "owner_binding_set_stamp": current.owner_binding_set_stamp,
        }

    async def _owner_projection_reconcile(_authorization):
        current = await _require_unchanged_capability_snapshot()
        return {
            "identity_gate_ready": identity_gate.ready,
            "projection": type(profile_coordinator.projection).__name__,
            "binding_generation": current.binding_generation,
            "owner_binding_set_stamp": current.owner_binding_set_stamp,
        }

    def _handler_switch(_authorization):
        return ensure_companion_reminder_tools(
            registry,
            service=reminder_service,
            authority_selector=reminder_selector,
        )

    def _companion_services_ready(_authorization):
        return {
            "preference_resolver": True,
            "reminder_service": True,
            "runtime_authority_bound": True,
        }

    async def _final_integrity_verify(_authorization):
        current = await _require_unchanged_capability_snapshot()
        return {
            "registry_phase": registry.authority_phase,
            "legacy_callback_recoverable": False,
            "source_owner_policy": "legacy_local_only",
            "capability_mutation": "none",
            "binding_generation": current.binding_generation,
            "owner_binding_set_stamp": current.owner_binding_set_stamp,
        }

    executor = ProductionGrowthCutoverStepExecutor(
        {
            "legacy_import": _legacy_import,
            "inactive_stage": _inactive_stage,
            "preflight": _preflight,
            "capability_publish_intent": _capability_publish_intent,
            "owner_projection_reconcile": _owner_projection_reconcile,
            "handler_switch": _handler_switch,
            "companion_services_ready": _companion_services_ready,
            "final_integrity_verify": _final_integrity_verify,
        }
    )
    coordinator = GrowthAuthorityCutoverCoordinator(
        router=router,
        store=store,
        executor=executor,
    )
    _growth_cutover_coordinator = coordinator
    _growth_cutover_plan = cutover_plan
    service_context.register("growth_authority_cutover", coordinator)
    service_context.register(
        "companion_reminder_registry_reconciler",
        lambda: ensure_companion_reminder_tools(
            registry,
            service=reminder_service,
            authority_selector=reminder_selector,
        ),
    )
    # A completed durable cutover is loaded before Harness recovery, but the
    # process-local Registry starts empty on every boot.  Reinstall the active
    # reminder ToolSpecs now so durable Run catalog leases can be mirrored
    # while Harness reconciles queued children.  The later cutover completion
    # keeps the same idempotent reconciliation for fresh migrations.
    if router.current.phase.value == "companion":
        restore_durable_companion_reminder_tools(
            registry,
            service=reminder_service,
            authority_selector=reminder_selector,
        )
    logger.info(
        "growth_authority_composed phase=%s generation=%d db=%s",
        router.current.phase.value,
        router.current.generation,
        store.path,
    )


async def _complete_growth_authority_cutover() -> None:
    """Complete cutover after Harness has registered the full core catalog."""

    from deskpet.companion.authority import GrowthAuthorityPhase

    coordinator = _growth_cutover_coordinator
    cutover_plan = _growth_cutover_plan
    router = _growth_authority_router
    store = _companion_store
    if (
        coordinator is None
        or cutover_plan is None
        or router is None
        or store is None
    ):
        raise RuntimeError("growth authority composition is unavailable")
    try:
        await coordinator.execute(cutover_plan)
        # A fresh process reconstructs the module-level Registry in legacy
        # phase.  If the durable cutover was completed by an earlier process,
        # coordinator.execute() correctly replays no steps; reconcile this
        # process-local projection before any product ingress opens.
        if router.current.phase is GrowthAuthorityPhase.COMPANION:
            reconciler = service_context.get(
                "companion_reminder_registry_reconciler"
            )
            if not callable(reconciler):
                raise RuntimeError(
                    "reminder_registry_reconciler_unavailable"
                )
            reconciler()
    except Exception:
        logger.exception(
            "growth_authority_cutover_failed",
            phase=router.current.phase.value,
            operation_id=cutover_plan.cutover_operation_id,
        )
        # Growth remains visibly paused/retired; ordinary conversation stays
        # available and must not resurrect a legacy writer.
    logger.info(
        "growth_authority_ready phase=%s generation=%d db=%s",
        router.current.phase.value,
        router.current.generation,
        store.path,
    )


async def _initialize_companion_projection_services() -> None:
    """Compose Task-12 services onto the existing SessionDB/control roots."""

    global _companion_notification_service, _companion_detail_query
    from deskpet.companion.detail_query import CompanionDetailQueryPort
    from deskpet.companion.notifications import CompanionNotificationService

    session_db = service_context.get("session_db")
    if _companion_store is None or session_db is None:
        raise RuntimeError("companion_projection_dependencies_unavailable")
    detail_query = CompanionDetailQueryPort(
        store=_companion_store,
        platform_store=service_context.get("capability_platform"),
        cursor_secret=secrets.token_bytes(32),
    )
    notifications = CompanionNotificationService(
        store=_companion_store,
        session_db=session_db,
        live_sink=_broadcast_control,
        detail_query=detail_query,
    )
    _companion_detail_query = detail_query
    _companion_notification_service = notifications
    service_context.register("companion_detail_query", detail_query)
    service_context.register("companion_notification_service", notifications)
    service_context.register(
        "companion_projection_visibility", notifications.visibility
    )
    try:
        frozen = _companion_identity_gate.freeze()
    except Exception:
        logger.info("companion_projection_history_closed_identity_unready")
    else:
        try:
            await notifications.bind_and_drain(frozen)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "companion_projection_startup_drain_failed",
                error=type(exc).__name__,
            )


def _initialize_companion_action_decision_service() -> None:
    """Bind all five trusted card actions to explicit production services."""

    from deskpet.companion.action_decisions import (
        CompanionActionDecisionService,
        SqliteExecutionDecisionRecovery,
    )
    from deskpet.companion.activation import ActivationDecisionCoordinator
    from deskpet.companion.control_ingress import (
        CompanionActivationDecisionService,
        CompanionEvaluationDecisionService,
        CompanionForgetDecisionService,
        CompanionRollbackDecisionService,
        UnavailableCompanionGrowthControlService,
    )

    workflow_service = service_context.get("workflow_service")
    uow = getattr(workflow_service, "execution_uow", None)
    unavailable = {
        "companion_growth_action_decision_service":
            "companion_action_decision_dependencies_unavailable",
        "companion_growth_evaluation_decision_service":
            "companion_evaluation_decision_dependencies_unavailable",
        "companion_growth_activation_decision_service":
            "companion_activation_decision_dependencies_unavailable",
        "companion_rollback_service":
            "companion_rollback_dependencies_unavailable",
        "companion_forget_service":
            "companion_forget_dependencies_unavailable",
    }
    for service_key, error_code in unavailable.items():
        service_context.register(
            service_key,
            UnavailableCompanionGrowthControlService(error_code),
        )
    if _companion_store is None or _companion_identity_gate is None:
        return
    service_context.register(
        "companion_growth_evaluation_decision_service",
        CompanionEvaluationDecisionService(_companion_store),
    )
    service_context.register(
        "companion_growth_activation_decision_service",
        CompanionActivationDecisionService(
            _companion_store,
            ActivationDecisionCoordinator(_companion_store),
            dispatcher=_companion_activation_dispatcher,
        ),
    )
    if _companion_detail_query is not None:
        service_context.register(
            "companion_rollback_service",
            CompanionRollbackDecisionService(
                _companion_store,
                _companion_detail_query,
                dispatcher=_companion_activation_dispatcher,
            ),
        )
        service_context.register(
            "companion_forget_service",
            CompanionForgetDecisionService(
                _companion_store, _companion_detail_query
            ),
        )
    if uow is not None and _harness_venue is not None:
        service_context.register(
            "companion_growth_action_decision_service",
            CompanionActionDecisionService(
                identity_gate=_companion_identity_gate,
                companion_store=_companion_store,
                execution=SqliteExecutionDecisionRecovery(uow.path),
                kernel_client=_harness_venue,
            ),
        )


async def _dispatch_companion_action_command(
    command_kind: str,
    message: dict[str, Any],
) -> None:
    """Dispatch the five exact UI action names after trusted ingress validation."""

    payload = message.get("payload")
    body = payload.get("body") if isinstance(payload, dict) else None
    if not isinstance(body, dict):
        raise RuntimeError("companion_action_body_invalid")
    frozen = _companion_identity_gate.freeze()
    if command_kind == "companion_growth_action_decision":
        from deskpet.companion.action_decisions import (
            TrustedCompanionControlIdentity,
        )

        if set(body) != {"decision_id", "allow"}:
            raise RuntimeError("companion_action_decision_fields_invalid")
        service = service_context.get(
            "companion_growth_action_decision_service"
        )
        if service is None:
            raise RuntimeError("companion_action_decision_unavailable")
        await service.decide(
            decision_id=str(body["decision_id"]),
            allow=body["allow"],
            control_identity=TrustedCompanionControlIdentity.from_frozen(frozen),
        )
        return
    service_key = {
        "companion_growth_evaluation_decision": (
            "companion_growth_evaluation_decision_service"
        ),
        "companion_growth_activation_decision": (
            "companion_growth_activation_decision_service"
        ),
        "companion_rollback": "companion_rollback_service",
        "companion_forget": "companion_forget_service",
    }.get(command_kind)
    if service_key is None:
        raise RuntimeError("companion_action_kind_unsupported")
    service = service_context.get(service_key)
    if service is None:
        raise RuntimeError(f"{service_key}_unavailable")
    handler = getattr(service, "decide", None)
    if handler is None:
        raise RuntimeError(f"{service_key}_invalid")
    result = handler(frozen_identity=frozen, **body)
    if inspect.isawaitable(result):
        await result
    if (
        command_kind in {"companion_rollback", "companion_forget"}
        and _companion_notification_service is not None
    ):
        await _companion_notification_service.bind_and_drain(frozen)


# ─── WI-T2.1 v3 build_agent 工厂（接电 VerifyGate）─────────────────
#
# Testability refactor: AgentLoop 构造逻辑从 chat handler inline 抽出，
# 让 wiring 测试可直接 `from main import build_agent; agent = build_agent(cfg, ...)`
# 断言 `agent.verify_gate is not None`，**不依赖** import main 全跑（v1 评审 P1-1
# 反驳 `import main; reload` 在 monolithic main.py 99% 翻车）。
#
# 修 last-mile P0-1：last-mile 已写好 VerifyGate / ReceiptStore / agent_loop
# 内部 verify check，但 main.py:_AgentLoop(...) 构造调用没传 `verify_gate=` /
# `receipt_store=` / `max_verify_nudges=`，导致生产抓获率 0%。本工厂兜底所有
# 接电点 — 调用方只需替换 `_AgentLoop(...)` 为 `build_agent(cfg, ...)`。
def _configured_context_compaction_model(cfg) -> str:
    context_cfg = getattr(cfg, "context", None)
    compaction_cfg = getattr(context_cfg, "compaction", None)
    return str(getattr(compaction_cfg, "model", "follow_session") or "follow_session")


def _live_context_compaction_model() -> str:
    """Read the mtime-cached user config for the next compaction cycle."""

    try:
        # The settings IPC persists the user override under the active
        # DESKPET_USER_DATA_DIR.  Read that exact source first; consulting only
        # the boot DESKPET_CONFIG path made the UI acknowledge a save that the
        # next AgentLoop compaction never observed.
        from p4_ipc import (
            _context_compaction_config_path,
            _read_context_compaction_model,
        )

        if _context_compaction_config_path().is_file():
            return _read_context_compaction_model()
        return _configured_context_compaction_model(load_config(_CONFIG_PATH))
    except Exception as exc:  # noqa: BLE001 - preserve the last startup value
        logger.warning("context_compaction_model_reload_failed err=%s", str(exc)[:160])
        return _configured_context_compaction_model(config)


def _build_context_task_projector(
    *,
    session_goal_store=None,
    receipt_store=None,
):
    """Build the shared read-only projection owner from live authority stores."""

    from deskpet.agent.context_task import (
        GoalProjectionAdapter,
        ReceiptProjectionAdapter,
        TaskContextProjector,
        WorkflowProjectionAdapter,
    )

    services = globals().get("service_context")
    task_graph = None
    workflow_service = None
    if services is not None:
        try:
            task_graph = services.get("task_graph_store")
        except Exception:
            pass
        try:
            workflow_service = services.get("workflow_service")
        except Exception:
            pass
        if session_goal_store is None:
            try:
                session_goal_store = services.get("session_goal_store")
            except Exception:
                pass
    if receipt_store is None:
        getter = globals().get("_get_receipt_store")
        if callable(getter):
            receipt_store = getter()
    return TaskContextProjector(
        workflow_source=(
            WorkflowProjectionAdapter(workflow_service)
            if workflow_service is not None
            else None
        ),
        goal_source=(
            GoalProjectionAdapter(session_goal_store, task_graph)
            if session_goal_store is not None
            else None
        ),
        receipt_source=(
            ReceiptProjectionAdapter(receipt_store)
            if receipt_store is not None
            else None
        ),
    )


async def _project_initial_context_snapshot(
    *,
    session_id: str,
    request_id: str,
    task_scope_id: str = "",
    user_text: str,
    explicit_new: bool = False,
):
    """Project active authorities plus typed evidence for a new long task."""

    from deskpet.agent.context_task import TaskFact

    evidence = (
        (
            TaskFact(
                fact_id=f"request:{request_id}",
                text=str(user_text).strip(),
                source="session_request",
                status="pending",
            ),
        )
        if explicit_new and str(user_text).strip()
        else ()
    )
    return await _build_context_task_projector().project(
        effective_sid=session_id,
        request_id=request_id,
        task_scope_id=task_scope_id,
        user_text=user_text,
        explicit_new=explicit_new,
        structured_evidence=evidence,
    )


def _attach_task_snapshot_to_request(bundle, messages, snapshot) -> None:
    """Mount one protected task fragment into bundle facts and wire messages."""

    if snapshot is None:
        return
    from agent.context_messages import ContextMessageMeta, tag_message
    from deskpet.agent.assembler.bundle import ContextFragment

    task = snapshot.to_protected_fragment()
    if not any(fragment.fragment_id == task.fragment_id for fragment in bundle.fragments):
        bundle.fragments.append(
            ContextFragment(
                fragment_id=task.fragment_id,
                source=task.source,
                role="system",
                content=task.content,
                lifetime="task",
                placement="prefix",
                priority=task.priority,
                trim_policy="never",
                protected=True,
                reason=task.reason,
            )
        )
    tagged = tag_message(
        {"role": "system", "content": task.content},
        ContextMessageMeta(
            placement="prefix",
            lifetime="task",
            source=task.source,
            protected=True,
            trim_policy="never",
            fragment_id=task.fragment_id,
            priority=task.priority,
            reason=task.reason,
        ),
    )
    insert_at = next(
        (index for index, message in enumerate(messages) if message.get("role") != "system"),
        len(messages),
    )
    messages.insert(insert_at, tagged)


def build_agent(
    cfg,
    *,
    llm_registry,
    tool_registry,
    context_manager,
    receipt_store_getter,
    # ★v3 round2 P0-6 补 4 个参数（main.py:4161 现场调用需要）
    max_iterations: int = 16,
    completion_probe=None,
    session_todo_getter=None,
    max_completion_nudges: int = 2,
    signature_repeat_threshold=None,
    # Companion+Code v1 — WI-B3 goal_mode 接电
    session_goal_store=None,
    goal_checker=None,
    # WI-4.0 compaction — pre-built ContextCompressor or None (flag off = BC)
    compressor=None,
    # FP-5 缺口 5a (2026-06-06) — WI-4.2 remount + 自动披露接电。两者 None (默认)
    # → _AgentLoop 跳过 _remount_skills + auto-disclosure（字节级 BC）。
    skill_loader=None,
    skill_matcher=None,
    # WI-OH-4 — 记忆 self-curation nudge。None (默认) → agent_loop 不调 nudge
    # （字节级 BC）。非 None 时每 N 回合 fire-and-forget 触发一次。
    memory_curator=None,
    provider_workload_context_factory=None,
    provider_workload_router=None,
    # ─── 七步问题处理流水线 IN-LOOP 闸透传（plans/2026-06-24-...）。全默认 None/False = BC。
    # self_check_gate / convergence_controller 不由 caller 传——前者 build_agent 内构造（B3，
    # 依赖本函数的 verify_gate/external_evaluator），后者 AgentLoop 内自建（依赖 self._gate）。
    evidence_gate=None,
    pipeline_problem_type=None,
    pipeline_needs_investigation=False,
    pipeline_observability=False,
    convergence_report_on_stop=False,
    response_quality_gate=None,
    trace_store=None,
    context_attempt_store=None,
    enable_verify_gate: bool = True,
):
    """Build a wired _AgentLoop with optional VerifyGate + ReceiptStore.

    Behavior matrix:
      - cfg.tools.verifier.verify_gate_mode == "off" (default): verify_gate=None,
        agent_loop 跳过 verify check（BC，与现状字节级一致）
      - mode in ("shadow", "strict"): 构造 RegexExtractor(load_claim_patterns(yaml))
        + VerifyGate(extractor, mode)；patterns yaml 缺失或 parse 失败 → 退化
        verify_gate=None + warn（**不抛**，避免 backend 启动崩）
      - receipt_store_getter() 失败 → receipt_store=None + warn

    Args:
        cfg: AppConfig 实例（需有 cfg.tools.verifier 子段）
        llm_registry / tool_registry / context_manager: AgentLoop 必需
        receipt_store_getter: 0-arg callable，返回 ReceiptStore 或 None
        max_iterations: AgentLoop 迭代上限
        completion_probe: 完成探针 (P5-S2 Hook A)
        session_todo_getter: session todo 快照读取器 (WI-4 Focus Chain)
        max_completion_nudges: 完成探针 nudge 上限
        signature_repeat_threshold: 死循环抑制阈值 (P5-S2 Phase 6)

    Returns:
        _AgentLoop 实例（带 verify_gate / receipt_store / max_verify_nudges 接电）
    """
    from agent.agent_loop import AgentLoop as _AgentLoop

    # Build VerifyGate from cfg (mode != "off" only — flag OFF stays BC).
    verify_gate = None
    try:
        verifier_cfg = getattr(getattr(cfg, "tools", None), "verifier", None)
        if (
            enable_verify_gate
            and verifier_cfg is not None
            and verifier_cfg.verify_gate_mode != "off"
        ):
            from pathlib import Path as _Path
            from deskpet.agent.verify_gate import (
                RegexExtractor,
                VerifyGate,
                load_claim_patterns,
                make_ephemeral_verifier,
            )
            patterns_path = _Path(verifier_cfg.claim_patterns_file)
            if not patterns_path.is_absolute():
                # Resolve relative to backend/ (where the default yaml lives)
                patterns_path = _Path(__file__).parent / patterns_path
            patterns = load_claim_patterns(patterns_path)
            # The verifier is part of this Root, so its model authority is the
            # same frozen Session/Root route as every other auxiliary call.
            _ephemeral_llm = _make_session_aware_llm_call(max_tokens=256)
            _ephemeral_subagent = make_ephemeral_verifier(_ephemeral_llm)
            verify_gate = VerifyGate(
                extractor=RegexExtractor(patterns),
                mode=verifier_cfg.verify_gate_mode,
                ephemeral_subagent=_ephemeral_subagent,
            )
            logger.info(
                "verify_gate_init mode=%s patterns=%d path=%s",
                verifier_cfg.verify_gate_mode, len(patterns), patterns_path,
            )
            try:
                from observability.metrics_sink import record as _verify_metric
                _verify_metric("verify_gate_init", {
                    "mode": verifier_cfg.verify_gate_mode,
                    "patterns_loaded": len(patterns),
                })
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("verify_gate init failed: %s — disabled", exc)
        verify_gate = None

    # Pull receipt_store (None when emit_receipts=False).
    receipt_store = None
    try:
        receipt_store = receipt_store_getter()
    except Exception as exc:  # noqa: BLE001
        logger.warning("receipt_store getter failed: %s — verify_gate disabled", exc)
        receipt_store = None
        verify_gate = None  # gate without ledger = blocks every end_turn

    nudges = 2
    try:
        nudges = int(getattr(verifier_cfg, "max_verify_nudges", 2)) if verifier_cfg else 2
    except Exception:  # noqa: BLE001
        nudges = 2

    # WI-2.1: read structured_reflection flag from verifier config (default False = BC)
    use_structured_reflection = (
        bool(getattr(verifier_cfg, "structured_reflection", False))
        if verifier_cfg else False
    )

    # WI-1B-2 压缩可观测: 从 cfg.features.ctx_observability 读 flag (默认 False = BC)。
    # OFF 时 _AgentLoop 压缩路径不 emit metrics / 不 yield ContextCompactedEvent。
    _features_cfg = getattr(cfg, "features", None)
    _ctx_observability = (
        bool(getattr(_features_cfg, "ctx_observability", False))
        if _features_cfg else False
    )

    # WI-2.4: construct ExternalEvaluator when flag on + provider available.
    # flag off (default) OR provider=None → None (BC, 0 extra LLM calls).
    _external_evaluator = None
    _use_external_evaluator = (
        bool(getattr(verifier_cfg, "external_evaluator", False))
        if verifier_cfg else False
    )
    if _use_external_evaluator:
        try:
            from deskpet.agent.external_evaluator import ExternalEvaluator as _EE  # noqa: PLC0415
            _ev_llm_call = _make_session_aware_llm_call(max_tokens=512)
            # FP-3 R-T3 接线：evaluator 仅对高后果目标触发（is_high_consequence_goal
            # 门控），故超时/错误时应保守拦截（返 revise）而非放行 —— 高后果场景
            # 漏放代价远大于误拦。生产构造启用 conservative_on_error（之前默认 False
            # → R-T3「高后果 evaluator 超时保守拦」分支生产从不触发）。
            _external_evaluator = _EE(
                llm_call=_ev_llm_call, conservative_on_error=True,
            )
        except Exception as exc:  # noqa: BLE001 — safe-fail
            import logging as _log
            _log.getLogger(__name__).warning(
                "external_evaluator construction failed: %s — skipping", exc
            )

    _cfg_raw = getattr(cfg, "raw", None)
    _agent_cfg = _cfg_raw.get("agent", {}) if isinstance(_cfg_raw, dict) else {}
    _ff = bool(_agent_cfg.get("force_finish_tool_choice", True))
    # WI-2: 用 iteration_trace_enabled（而非 trace_enabled）避免与
    # [context.assembler].trace_enabled（Context Trace UI / P4-S11）命名碰撞。
    _trace_enabled = bool(_agent_cfg.get("iteration_trace_enabled", False))
    _tracer = None
    if _trace_enabled:
        try:
            from agent.trace import IterationTracer
            _tracer = IterationTracer(
                trace_dir=_paths.user_data_dir() / "traces",
                session_id="",
                task_id=str(uuid.uuid4()),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("iteration_tracer_init_failed: %s", exc)
            _tracer = None

    # ─── Step6 SelfCheckGate 装配（plans/2026-06-24-..., 红队 B3）。
    # 关键：SelfCheckGate **不依赖** tools.verifier 两 flag——用户单开 problem_pipeline.self_check
    # 不开 tools.verifier 时，强制自建一套 verify_gate(mode=strict) + external_evaluator(异体 fresh-context)
    # 供 SelfCheckGate，避免"自检空门"。二者皆不可得 → 降级 pass **但启动期 warning**，不静默空门。
    def _build_pipeline_verify_gate(_scm: str):
        """为 SelfCheckGate 自建 strict 对账门（抽自上方 :979 构造逻辑；不绑 verifier_cfg.mode）。"""
        try:
            from pathlib import Path as _Path
            from deskpet.agent.verify_gate import (
                RegexExtractor, VerifyGate, load_claim_patterns, make_ephemeral_verifier,
            )
            _vc = getattr(getattr(cfg, "tools", None), "verifier", None)
            _pf = _Path(getattr(_vc, "claim_patterns_file", "verify/claim_patterns.yaml")
                        if _vc else "verify/claim_patterns.yaml")
            if not _pf.is_absolute():
                _pf = _Path(__file__).parent / _pf
            _patterns = load_claim_patterns(_pf)
            _sub = make_ephemeral_verifier(
                _make_session_aware_llm_call(max_tokens=256)
            )
            return VerifyGate(extractor=RegexExtractor(_patterns), mode="strict", ephemeral_subagent=_sub)
        except Exception as _e:  # noqa: BLE001
            logger.warning("pipeline_build_verify_gate_failed err=%s", str(_e)[:200])
            return None

    def _build_pipeline_external_evaluator(_scm: str):
        """异体评分子代理：复用 frozen Root route，但保持独立 evaluator persona。"""
        try:
            from deskpet.agent.external_evaluator import ExternalEvaluator as _EE  # noqa: PLC0415
            _evc = _make_session_aware_llm_call(max_tokens=512)
            return _EE(llm_call=_evc, conservative_on_error=True)
        except Exception as _e:  # noqa: BLE001
            logger.warning("pipeline_build_external_evaluator_failed err=%s", str(_e)[:200])
            return None

    _self_check_gate = None
    _pp = getattr(getattr(cfg, "features", None), "problem_pipeline", None)
    if (
        enable_verify_gate
        and _pp is not None
        and getattr(_pp, "enabled", False)
        and getattr(_pp, "self_check", False)
    ):
        try:
            from deskpet.agent.self_check_gate import SelfCheckGate as _SCG  # noqa: PLC0415
            _scm = getattr(_pp, "self_check_model", "") or ""
            _sc_hetero = bool(getattr(_pp, "self_check_heterogeneous", True))
            _sc_verify_gate = verify_gate                    # tools.verifier 已开 → 复用
            if _sc_verify_gate is None and receipt_store is not None:
                _sc_verify_gate = _build_pipeline_verify_gate(_scm)
            _sc_external = _external_evaluator                # tools.verifier.external_evaluator 已开 → 复用
            if _sc_external is None and _sc_hetero:
                _sc_external = _build_pipeline_external_evaluator(_scm)
            if _sc_verify_gate is None and _sc_external is None:
                logger.warning("self_check_degraded reason=no_verify_no_evaluator sid_scope=build_agent")
            _self_check_gate = _SCG(
                verify_gate=_sc_verify_gate,
                external_evaluator=_sc_external,
                heterogeneous_enabled=_sc_hetero,
            )
        except Exception as _exc:  # noqa: BLE001
            logger.warning("self_check_gate build failed: %s", _exc)
            _self_check_gate = None

    if trace_store is None and getattr(getattr(cfg, "workflows", None), "trace_enabled", False):
        workflow_service = service_context.get("workflow_service")
        trace_store = getattr(workflow_service, "trace_store", None)

    _context_projector = None
    _context_snapshot_store_for_loop = None
    _context_segment_store_for_loop = None
    _compression_model_resolver_for_loop = None
    _compression_model = "follow_session"
    _compression_model_provider = None
    if bool(getattr(getattr(cfg, "features", None), "context_os_v1", False)):
        try:
            _services = globals().get("service_context")
            _context_snapshot_store_for_loop = _services.get(
                "context_snapshot_store"
            )
            _context_segment_store_for_loop = _services.get(
                "context_segment_store"
            )
            _compression_model_resolver_for_loop = _services.get(
                "compression_model_resolver"
            )
            _context_projector = _build_context_task_projector(
                session_goal_store=session_goal_store,
                receipt_store=receipt_store,
            )
            _compression_model = _configured_context_compaction_model(cfg)
            _compression_model_provider = _live_context_compaction_model
        except Exception as _context_os_exc:  # noqa: BLE001
            raise RuntimeError(
                f"context_os_compaction_wiring_failed:{_context_os_exc}"
            ) from _context_os_exc

    return _AgentLoop(
        llm_registry=llm_registry,
        tool_registry=tool_registry,
        max_iterations=max_iterations,
        completion_probe=completion_probe,
        session_todo_getter=session_todo_getter,
        max_completion_nudges=max_completion_nudges,
        signature_repeat_threshold=signature_repeat_threshold,
        context_manager=context_manager,
        # ─── WI-T2.1 v3 真接电（修 last-mile P0-1）───
        verify_gate=verify_gate,
        receipt_store=receipt_store,
        max_verify_nudges=nudges,
        # ─── Companion+Code v1 WI-B3 真接电 ───
        # flag OFF 时 store/checker=None → agent_loop 跳过 goal-check（BC）
        session_goal_store=session_goal_store,
        goal_checker=goal_checker,
        # ─── WI-2.1 结构化反思（flag off = BC）───
        structured_reflection=use_structured_reflection,
        # ─── WI-2.4 外部评估器（flag off = BC, None = 0 calls）───
        external_evaluator=_external_evaluator,
        # ─── WI-4.0 compaction（flag off = compressor=None = BC）───
        compressor=compressor,
        context_projector=_context_projector,
        context_snapshot_store=_context_snapshot_store_for_loop,
        context_segment_store=_context_segment_store_for_loop,
        compression_model_resolver=_compression_model_resolver_for_loop,
        compression_model=_compression_model,
        compression_model_provider=_compression_model_provider,
        # ─── WI-1B-2 压缩可观测（flag off = False = BC，压缩路径零额外行为）───
        ctx_observability=_ctx_observability,
        # ─── FP-5 缺口 5a：WI-4.2 remount + 自动披露（flag off = None = BC）───
        skill_loader=skill_loader,
        skill_matcher=skill_matcher,
        # ─── WI-OH-4：记忆 self-curation nudge（curator None = BC，不调 nudge）───
        memory_curator=memory_curator,
        provider_workload_context_factory=provider_workload_context_factory,
        provider_workload_router=provider_workload_router,
        trace_store=trace_store,
        context_attempt_store=context_attempt_store,
        curation_nudge_every_n_turns=int(
            getattr(getattr(getattr(cfg, "memory", None), "v2", None),
                    "curation_nudge_every_n_turns", 8) or 8
        ),
        # ─── WI-4b pre-flush：压缩前把任务态落 L1(跨 session 记任务)。模块级
        # _file_memory(L1048)在 build_agent 调用时已就绪；try 失败则 None(BC)。───
        file_memory=globals().get("_file_memory"),
        force_finish_via_tool_choice=_ff,
        tracer=_tracer,
        # ─── 子代理并发驱动 WI-3.3：非阻塞子代理 completion queue（回合边界 drain）。
        # 从 module-global service_context 取（flag subagent_nonblocking OFF 时槽为
        # None → agent_loop 不 drain，BC）。───
        # ─── 七步问题处理流水线 IN-LOOP 闸（plans/2026-06-24-...）。flag off → 全 None/False = BC。
        evidence_gate=evidence_gate,
        self_check_gate=_self_check_gate,
        convergence_report_on_stop=convergence_report_on_stop,
        pipeline_problem_type=pipeline_problem_type,
        pipeline_needs_investigation=pipeline_needs_investigation,
        pipeline_observability=pipeline_observability,
        response_quality_gate=response_quality_gate,
    )


# 记忆系统升级 WI-M1.2: facts shadow 抽取器 + FactsStore。模块级默认
# None —— p4 服务块构造失败时 lifespan 的 fanout 仍能安全引用（同
# _summarizer_*）。_facts_store 还被 WI-M1.4 的 EnhancedRetriever 复用。
_fact_extractor = None  # type: ignore[assignment]
_facts_store = None  # type: ignore[assignment]
# Stage 2 / WI-S2.4 (D12 v2 / D-RISK-4)：episodic→semantic 异步抽 fact 的
# task 必须收进 set + add_done_callback(discard)；裸 asyncio.create_task
# 会被 Python 3.11+ GC 静默吞掉，引发"Task was destroyed but it is pending"
# warning + 数据丢失。lifespan shutdown 时 gather 等所有 task 完成。
_episodic_background_tasks: set = set()
_workflow_ipc_background_tasks: set = set()
_legacy_reflection_tasks: set = set()
_provider_workload_maintenance_tasks: set = set()
# 记忆系统升级 WI-M1.5: 长消息切块器。fanout 在 chunking flag 开时用它
# 切块 + embed 进 messages_chunks；EnhancedRetriever 读侧复用做向量召回。
_message_chunker = None  # type: ignore[assignment]
# 记忆系统升级 WI-M1.7b: procedural memory（反复问题→解法）存储。
_skill_memory_store = None  # type: ignore[assignment]

# --- P4-S13: read-only P4 services (FileMemory + SkillLoader + MemoryManager) ---
#
# We construct the three "safe" components at module top-level so p4_ipc.py
# handlers (skills_list / memory_l1_list / memory_l1_delete) return real data
# instead of the pre-S13 graceful-empty stub. ContextAssembler + MCPManager
# stay deferred to a later slice because they require deeper hooks into the
# chat stream and external processes respectively.
#
# Everything is best-effort: any failure logs a warning + leaves the slot
# empty. p4_ipc.py's graceful fallback then surfaces `reason: *_not_registered`
# to the UI, which keeps the panels usable.
try:
    from deskpet.memory.file_memory import FileMemory as _FileMemory
    from deskpet.memory.manager import MemoryManager as _MemoryManager
    from deskpet.memory.session_db import SessionDB as _SessionDB
    from deskpet.memory.embedder import Embedder as _Embedder
    from deskpet.memory.vector_worker import VectorWorker as _VectorWorker
    from deskpet.memory.image_worker import (
        ImageGenerationWorker as _ImageWorker,
    )
    from deskpet.memory.retriever import Retriever as _Retriever
    from deskpet.skills.loader import SkillLoader as _SkillLoader

    # L1 lives under the same data dir as memory.db → already resolved by
    # load_config() into an absolute path. paths.user_data_dir() is the
    # canonical root when memory.db_path was blank.
    _l1_dir = Path(config.memory.db_path).resolve().parent if config.memory.db_path else _paths.user_data_dir() / "data"
    _file_memory = _FileMemory(base_dir=_l1_dir)
    service_context.register("file_memory", _file_memory)

    # P4-S15: Embedder — BGE-M3 INT8 with mock fallback when the model dir
    # is absent. Mock embedder hits is_ready=True instantly so cold-start
    # isn't blocked even on a fresh install. Real model loads in the
    # background via lifespan.warmup() so prompt cache stays hot.
    try:
        _bge_dir = resolve_model_dir("bge-m3-int8")
    except Exception:
        _bge_dir = None
    _embedder = _Embedder(
        model_path=_bge_dir,
        use_mock_when_missing=True,
    )

    # Wire BGE-M3 semantic relevance into deep-research (WI-2.2). The
    # scorer returns cosine(topic, passage) ∈ [0,1] per passage so
    # deepresearch blends it into relevance. Skipped when the embedder is
    # the mock (hash vectors carry no semantics) → keyword-only fallback.
    try:
        import numpy as _np
        from deskpet.tools import research_tools as _rt_sem

        async def _research_semantic(query: str, passages: list[str]) -> list[float]:
            if _embedder.is_mock() or not passages:
                return []
            vecs = await _embedder.encode([query] + list(passages))
            arr = _np.asarray(vecs, dtype="float32")
            norms = _np.linalg.norm(arr, axis=1, keepdims=True)
            arr = arr / _np.clip(norms, 1e-8, None)
            qv, pv = arr[0], arr[1:]
            return [float(x) for x in pv @ qv]

        _rt_sem.set_semantic_scorer(_research_semantic)
    except Exception as _exc:  # noqa: BLE001 — research degrades to keyword
        logger.debug("research_semantic_wiring_skipped", error=str(_exc))

    # P4-S15: SessionDB at <data>/state.db, side-by-side with the legacy
    # memory.db. on_message_written hook will be wired to VectorWorker.enqueue
    # in lifespan once the worker has started, so **new** turns auto-embed as
    # they hit the DB. NOTE: live enqueue only covers new turns — it does NOT
    # backfill gaps (worker drop / encode failure / historical NULL rows). The
    # lifespan fires an explicit backfill_missing() task after worker start to
    # close those gaps (2026-06-02 audit FATAL-A); see _vector_backfill_bg.
    _state_db_path = _l1_dir / "state.db"
    _session_db = _SessionDB(db_path=_state_db_path)
    from deskpet.agent.compression_model_resolver import (
        CompressionModelResolver as _CompressionModelResolver,
    )
    from deskpet.agent.context_request_planner import (
        ContextRequestPlanner as _ContextRequestPlanner,
    )
    from deskpet.memory.context_snapshot_store import (
        ContextSnapshotStore as _ContextSnapshotStore,
    )
    from deskpet.memory.context_segment_store import (
        ContextSegmentStore as _ContextSegmentStore,
    )
    from deskpet.agent.session_history_planner import (
        SessionHistoryPlanner as _SessionHistoryPlanner,
    )
    from agent.context_report import (
        ContextAttemptStore as _ContextAttemptStore,
        set_context_attempt_store as _set_context_attempt_store,
    )

    _context_os_enabled = bool(
        getattr(getattr(config, "features", None), "context_os_v1", False)
    )
    _context_snapshot_store = _ContextSnapshotStore(
        _state_db_path, enabled=_context_os_enabled
    )
    _context_segment_store = _ContextSegmentStore(
        _state_db_path, enabled=_context_os_enabled
    )
    _session_history_planner = _SessionHistoryPlanner(
        _session_db, _context_segment_store
    )
    from deskpet.tools.capabilities import current_tool_execution_context
    from deskpet.tools.session_history_tools import (
        register_run_details_inspect,
    )

    register_run_details_inspect(
        deskpet_tool_registry_v2,
        _session_db,
        session_id_getter=current_tool_execution_context,
    )
    _context_page_in_store = None
    if _context_os_enabled:
        from deskpet.tools.context_page_in_tools import (
            ContextPageInStore,
            register_context_page_in,
        )
        from deskpet.tools.session_history_tools import (
            register_session_history_page_in,
        )

        register_session_history_page_in(
            deskpet_tool_registry_v2,
            _context_segment_store,
            _session_db,
            session_id_getter=current_tool_execution_context,
            # Page-in is itself included in the next request budget. Keep one
            # causal group below the normal generation reserve.
            budget_getter=lambda: 8192,
        )
        _context_page_in_store = ContextPageInStore()

        def _read_page_in_skill(name: str) -> str:
            loader = (
                service_context.get("managed_skill_discovery_projection")
                or service_context.get("skill_loader")
            )
            if loader is None:
                raise KeyError(name)
            return loader.read_body(name)

        register_context_page_in(
            deskpet_tool_registry_v2,
            _context_page_in_store,
            execution_context_getter=current_tool_execution_context,
            skill_body_getter=_read_page_in_skill,
        )
    _context_request_planner = (
        _ContextRequestPlanner(
            _tool_capability_resolver,
            history_planner=_session_history_planner,
            snapshot_store=_context_snapshot_store,
            page_in_store=_context_page_in_store,
        )
        if _tool_capability_resolver is not None
        else None
    )
    _compression_model_resolver = _CompressionModelResolver()
    _context_attempt_store = (
        _ContextAttemptStore() if _context_os_enabled else None
    )
    _set_context_attempt_store(_context_attempt_store)
    service_context.register("context_snapshot_store", _context_snapshot_store)
    service_context.register("context_segment_store", _context_segment_store)
    service_context.register("context_page_in_store", _context_page_in_store)
    service_context.register("context_request_planner", _context_request_planner)
    service_context.register(
        "project_initial_context_snapshot", _project_initial_context_snapshot
    )
    service_context.register(
        "attach_task_snapshot_to_request", _attach_task_snapshot_to_request
    )
    service_context.register(
        "compression_model_resolver", _compression_model_resolver
    )
    logger.info(
        "context_os_v1 %s owner=context_request_planner+agent_loop "
        "resolved_window=per_session_model migration_version=18 "
        "compaction_model=%s registry_revision=%d",
        "ACTIVE" if _context_os_enabled else "ROLLBACK",
        getattr(getattr(config, "context", None), "compaction", None).model
        if getattr(getattr(config, "context", None), "compaction", None) is not None
        else "follow_session",
        deskpet_tool_registry_v2.catalog_snapshot().revision,
    )
    service_context.register("context_attempt_store", _context_attempt_store)

    # P4-S15: VectorWorker — drains a queue of (msg_id, text) into the
    # vec0 virtual table on a 1s interval. Stays empty until SessionDB
    # actually receives writes, so the cold-start cost is essentially nil.
    _vector_worker = _VectorWorker(
        session_db=_session_db,
        embedder=_embedder,
    )

    # P4-S15: Retriever — RRF fusion of vec / fts / recency / salience.
    # Embedder may still be loading; Retriever skips the vec route until
    # embedder.is_ready becomes True. Other routes work immediately.
    _retriever = _Retriever(
        session_db=_session_db,
        embedder=_embedder,
    )

    # 记忆系统升级 WI-M1.2: facts 抽取器（shadow 模式）。是否真抽取由
    # lifespan 的 _on_message_written fanout 按 config.memory.v2.facts_extract
    # 决定（Strangler-Fig：flag 关 → fanout 不调它 → ensure_memory_v2_tables
    # 不触发 → facts 表不存在）。无可用 LLM 时不构造（_fact_extractor=None
    # → fanout 跳过，不报错）。WI-M1.4 的 EnhancedRetriever 也复用 _facts_store。
    try:
        from deskpet.memory.facts import (
            FactsStore as _FactsStore,
            FactExtractor as _FactExtractor,
        )
        # embedder 注入 → upsert 时对 "key: value" 算 BGE-M3 向量存
        # embedding 列（WI-M1.4 facts 向量召回）。mock embedder 时 _embed_fact
        # 自动返回 None（不写向量），召回端降级 LIKE。
        _facts_store = _FactsStore(_state_db_path, embedder=_embedder)
        _facts_llm = _make_session_aware_llm_call()
        if _facts_llm is not None:
            # Stage 2 / WI-S2.1a — cross_key_merge 需要 forgotten_at + superseded_by
            # 列可用；R8 v2 / D17 v2：列 ALTER 失败时强制关 flag。
            _v2_cfg = config.memory.v2
            _cross_key_enabled = bool(_v2_cfg.cross_key_merge)
            if _cross_key_enabled:
                try:
                    from deskpet.memory.schema_v2_migrator import (
                        alter_failures as _alter_failures,
                    )
                    _failed = _alter_failures()
                    if _failed.get("superseded_by"):
                        logger.warning(
                            "p4_cross_key_merge_force_disabled",
                            reason="superseded_by column unavailable",
                        )
                        _cross_key_enabled = False
                except Exception:  # noqa: BLE001
                    pass

            async def _observe_companion_preference_fact(fact, message_id):
                """Quarantine legacy fact output behind the Companion writer."""

                return {
                    "status": "quarantined_legacy_preference_output",
                    "message_id": int(message_id),
                    "preference_key": str(fact.key),
                }

            _fact_extractor = _FactExtractor(
                _facts_store,
                extract_llm=_facts_llm,
                min_chars=_v2_cfg.facts.min_user_chars,
                cross_key_merge=_cross_key_enabled,
                cross_key_llm=_facts_llm,
                embedder=_embedder,
                goal_facts=_v2_cfg.goal_facts,  # FP-4 WI-3.1
                content_dedup=_v2_cfg.extract_content_dedup,  # 2026-06-27 内容哈希幂等去重
                content_ttl_s=_v2_cfg.facts.content_dedup_ttl_s,
                content_cache_max=_v2_cfg.facts.content_dedup_cache_max,
                companion_preference_evidence_only=True,
                preference_observer=_observe_companion_preference_fact,
            )
            logger.info(
                "p4_fact_extractor_ready",
                min_chars=_v2_cfg.facts.min_user_chars,
                cross_key_merge=_cross_key_enabled,
                goal_facts=_v2_cfg.goal_facts,
                content_dedup=_v2_cfg.extract_content_dedup,  # boot 实证 flag 真传进去
            )
        else:
            logger.info("p4_fact_extractor_skipped", reason="no_llm_provider")
    except Exception as _fe_exc:  # noqa: BLE001
        logger.warning("p4_fact_extractor_init_failed", error=str(_fe_exc))
        _fact_extractor = None

    # 记忆系统升级 WI-M1.5: 长消息切块器。无条件构造（轻量），是否真切
    # 由 fanout 按 config.memory.v2.chunking 决定。embedder 注入 → chunk
    # 带向量；mock embedder → chunk 无向量、读侧跳过。
    try:
        from deskpet.memory.chunker import MessageChunker as _MessageChunker
        _message_chunker = _MessageChunker(_state_db_path, embedder=_embedder)
    except Exception as _ck_exc:  # noqa: BLE001
        logger.warning("p4_message_chunker_init_failed", error=str(_ck_exc))
        _message_chunker = None

    # 记忆系统升级 WI-M1.7b: SkillMemoryStore（procedural memory）。reflection
    # flag 开时构造 —— 与 ReflectionWorker 同 flag、相互独立（无调用关系）。
    if config.memory.v2.reflection:
        try:
            from deskpet.memory.reflection import (
                SkillMemoryStore as _SkillMemoryStore,
            )
            _skill_memory_store = _SkillMemoryStore(_state_db_path)
            logger.info("p4_skill_memory_store_ready")
        except Exception as _sm_exc:  # noqa: BLE001
            logger.warning(
                "p4_skill_memory_store_init_failed", error=str(_sm_exc),
            )
            _skill_memory_store = None

    # 记忆系统升级 WI-M1.3/M1.4/M1.5 + Stage 2 WI-S2.2: EnhancedRetriever
    # 非侵入 wrapper。rerank / enhanced_retriever / query_rewrite / chunking
    # / entity_path 任一开 → 用它包住老 Retriever；全关 → MemoryManager 直
    # 接持裸 Retriever（recall 与第一代逐字节一致，Strangler-Fig）。
    _retriever_for_mm = _retriever
    _v2 = config.memory.v2
    if (
        _v2.rerank or _v2.enhanced_retriever or _v2.query_rewrite
        or _v2.chunking or _v2.entity_path
    ):
        try:
            from deskpet.memory.enhanced_retriever import (
                build_recall_retriever as _build_recall_retriever,
            )
            # WI-M1.3: reranker —— 模型缺失 → BGEReranker 内部降级 mock，
            # EnhancedRetriever._apply_reranker 检测到 mock 会自动 bypass。
            _reranker = None
            if _v2.rerank:
                from deskpet.memory.reranker import BGEReranker as _BGEReranker
                try:
                    _rr_dir = resolve_model_dir("bge-reranker-v2-m3")
                except Exception:  # noqa: BLE001
                    _rr_dir = None
                _reranker = _BGEReranker(
                    model_path=_rr_dir, use_mock_when_missing=True,
                )
            # WI-M1.5: 短 query 改写器。无 LLM → None（不改写）。
            _query_rewriter = None
            if _v2.query_rewrite:
                from deskpet.memory.query_rewriter import (
                    LLMQueryRewriter as _LLMQueryRewriter,
                )
                _qr_llm = _make_session_aware_llm_call(max_tokens=128)
                if _qr_llm is not None:
                    _query_rewriter = _LLMQueryRewriter(_qr_llm)
            # WI-M1.4: enhanced_retriever 依赖 facts_extract —— 单独开
            # facts 表不会被写入，召回路长期为空，warn 一次。
            if _v2.enhanced_retriever and not _v2.facts_extract:
                logger.warning(
                    "memory.v2.enhanced_retriever=true 但 facts_extract"
                    "=false —— facts 表不会被写入，facts 路召回将长期为空。"
                )
            # Stage 2 WI-S2.2: entity_path 同样依赖 facts_extract —— 没事实
            # 抽进 facts 表，entity LIKE 永远空召。和 enhanced_retriever 一
            # 样只 warn 不挡 boot（Strangler-Fig：先把 wire 接上）。
            if _v2.entity_path and not _v2.facts_extract:
                logger.warning(
                    "memory.v2.entity_path=true 但 facts_extract=false "
                    "—— facts 表不会被写入，entity 路召回将长期为空。"
                )
            # Stage 2 WI-S2.2: 构造 CompositeEntityExtractor —— LLM 降级 regex。
            # 离线 / 无 LLM 时退化为 RegexEntityExtractor + NoopLLM（永远抽空），
            # Composite 自动走 regex 分支。
            _entity_extractor = None
            if _v2.entity_path:
                try:
                    from deskpet.memory.entity_extractor import (
                        CompositeEntityExtractor as _Composite,
                        LLMEntityExtractor as _LLMEx,
                        NoopEntityExtractor as _NoopEx,
                        RegexEntityExtractor as _RegexEx,
                    )
                    _ent_llm = _make_session_aware_llm_call(max_tokens=128)
                    _llm_ex = _LLMEx(_ent_llm) if _ent_llm is not None else _NoopEx()
                    _entity_extractor = _Composite(_llm_ex, _RegexEx())
                    logger.info(
                        "p4_entity_path_enabled",
                        llm_extractor=_ent_llm is not None,
                        entity_weight=0.10,
                    )
                except Exception as _ent_exc:  # noqa: BLE001
                    logger.warning(
                        "p4_entity_extractor_init_failed",
                        error=str(_ent_exc),
                    )
                    _entity_extractor = None
            _retriever_for_mm = _build_recall_retriever(
                _retriever,
                rerank=_v2.rerank,
                enhanced_retriever=_v2.enhanced_retriever,
                query_rewrite=_v2.query_rewrite,
                chunking=_v2.chunking,
                facts_store=_facts_store,
                facts_weight=_v2.facts.facts_weight,
                reranker=_reranker,
                query_rewriter=_query_rewriter,
                embedder=_embedder,
                chunk_store=_message_chunker,
                entity_extractor=_entity_extractor,
                entity_weight=0.10,
            )
            logger.info(
                "p4_enhanced_retriever_ready",
                rerank=_v2.rerank,
                enhanced_retriever=_v2.enhanced_retriever,
                query_rewrite=_v2.query_rewrite,
                chunking=_v2.chunking,
                entity_path=_v2.entity_path,
            )
        except Exception as _er_exc:  # noqa: BLE001
            logger.warning(
                "p4_enhanced_retriever_init_failed", error=str(_er_exc),
            )
            _retriever_for_mm = _retriever

    # P4-S17: MemoryManager and agent memory share SessionDB as the
    # canonical L2/conversation store.
    # OpenSpec 2026-05-16-companion-context-isolation §D1/D4: inject the
    # companion cross-session decay so the retriever down-weights an
    # unrelated code-session project memory when serving a companion
    # request. Absent / =1.0 → legacy behaviour (Strangler-Fig).
    _comp_cfg = (config.raw.get("companion") if hasattr(config, "raw") else None) or {}
    _xsess_decay = _comp_cfg.get("memory_cross_session_decay")
    # WI-2 (2026-06-19 task-scope-context-isolation): 同 session 时近性降权
    # 半衰期（天）。**默认开启 = 7 天** —— 根治单一 `default` 会话里陈旧旧任务
    # (如一个月前 CATL 年报) 满权召回把新任务带偏。config.toml 显式设 0 / 负数
    # / 极大值 → 关闭（退回旧行为）。
    _DEFAULT_RECENCY_HALF_LIFE_DAYS = 7.0
    _recency_hl_cfg = _comp_cfg.get("memory_intra_session_recency_half_life_days")
    _recency_hl = (
        float(_recency_hl_cfg)
        if _recency_hl_cfg is not None
        else _DEFAULT_RECENCY_HALF_LIFE_DAYS
    )
    _memory_manager = _MemoryManager(
        file_memory=_file_memory,
        session_db=_session_db,
        retriever=_retriever_for_mm,
        cross_session_decay=(
            float(_xsess_decay) if _xsess_decay is not None else None
        ),
        recency_half_life_days=_recency_hl,
    )
    service_context.register("memory_manager", _memory_manager)
    service_context.register("memory_recall_query", _retriever_for_mm)

    # P4-S17: RedactingMemoryStore remains the only write path exposed to
    # the agent/admin API, but the inner store is now the canonical state.db.
    memory_store = RedactingMemoryStore(_session_db)
    service_context.register("memory_store", memory_store)

    # The generic loader no longer mounts the legacy writable user directory.
    # User Skills are imported by the authority cutover and thereafter become
    # immutable Capability Pack versions projected through the managed root.
    # enable_watch=False in rc1 to avoid the watchdog thread on cold boot;
    # UI's refresh button triggers a manual reload via list_skills() anyway.
    from deskpet.companion.skills import (
        ManagedSkillDiscoveryProjection as _ManagedSkillDiscoveryProjection,
        inventory_first_party_skill_packs as _inventory_first_party_skill_packs,
    )

    _first_party_skill_inventory = _inventory_first_party_skill_packs(
        _paths.first_party_capability_pack_roots()
    )
    _managed_skill_projection = _ManagedSkillDiscoveryProjection(
        _first_party_skill_inventory
    )
    _skill_loader = _SkillLoader(
        skill_dirs=[],
        skill_scopes=[],
        enable_watch=False,
        # WI-5：触发式知识注入的真正接电点。知识片段（user-invocable:false）
        # 只有 loader 的 knowledge_enabled 打开才会进 reload() 快照（loader.py:308）。
        # 之前漏传 → loader 恒 False → 3 个知识片段从未进 all() → matcher 永远
        # 匹配不到（真机 total=12 而非 15）。assembler/SkillComponent 的 d7da6e5
        # 配置流通修复是在「过滤一个本就为空的子集」，必须从源头 loader 放行。
        # flag 默认 False 保 BC（desc list / /help / skill_invoke 不泄露知识片段）。
        knowledge_enabled=bool(
            getattr(getattr(config, "skills", None), "knowledge_enabled", False)
        ),
    )
    service_context.register("skill_loader", _skill_loader)
    service_context.register(
        "managed_skill_discovery_projection", _managed_skill_projection
    )

    # WI-B + Companion+Code v1：SessionGoalStore + GoalChecker 接电.
    # flag OFF（默认）→ store/checker = None → AgentLoop 接电 BC（不调）.
    # flag ON → 真构造；build_agent 工厂在 chat handler 处取用并传给 AgentLoop.
    _session_goal_store = None
    _goal_checker = None
    if bool(getattr(getattr(config, "features", None), "goal_mode", False)):
        try:
            from deskpet.agent.goal_store import SessionGoalStore as _GS
            from deskpet.agent.goal_checker import GoalChecker as _GC
            _session_goal_store = _GS()
            _goal_checker = _GC(
                llm_call=_make_session_aware_llm_call(max_tokens=512)
            )
            # R-T1：接电持久化。_session_db 在上方已构造。
            try:
                _session_goal_store.bind_persistence(_session_db)
                logger.info("goal_store_bound_persistence")
            except Exception as _bp_exc:  # noqa: BLE001
                logger.warning("goal_store_bind_persistence_failed: %s", _bp_exc)
            # WI-TG-1 方案A：把 goal_task_{list,update,create,get} 从「仅
            # teammate 可见」提升为主 agent 全局可调。全局 handler 签名
            # (args, corr_id) 无 goal/session 上下文，故用 goal_resolver 闭包
            # 在调用时反查 SessionGoalStore 的活跃目标取 (goal_id, session_id)；
            # 无活跃目标 → 工具返回「请先用 /goal 设定目标」友好错误。
            # 门控：仅 goal_mode ON 时注册（OFF → registry 无此工具名 → BC）。
            try:
                if deskpet_tool_registry_v2 is not None:
                    from deskpet.agent.task_graph import (
                        TaskGraphStore as _TGS_global,
                    )
                    from deskpet.tools.task_graph_tools import (
                        build_global_goal_task_tools as _build_global_gt,
                    )
                    from deskpet.tools.capabilities import (
                        current_tool_execution_context as _current_tool_execution_context,
                    )
                    _global_tg_store = _TGS_global(db=_session_db)
                    service_context.register(
                        "task_graph_store", _global_tg_store
                    )
                    def _gt_resolver():
                        _exec_ctx = _current_tool_execution_context()
                        if _exec_ctx is not None:
                            return _session_goal_store.get_active_goal_context_for_session(
                                _exec_ctx.session_id
                            )
                        return _session_goal_store.get_active_goal_context()
                    # 工具可见性门控：仅当存在活跃 goal 时才把 goal_task_* 暴露
                    # 给 LLM。否则普通聊天里 LLM 看不到这些工具，不会被「拆解
                    # 任务」类描述诱导误调（误触发 goal 流程）。handler 仍有
                    # 「请先 /goal」兜底，门控只是从源头不让它进 prompt。
                    def _gt_visible(_eligibility_ctx=None):
                        if _eligibility_ctx is not None:
                            return (
                                _session_goal_store.get_active_goal_context_for_session(
                                    _eligibility_ctx.session_id
                                )
                                is not None
                            )
                        return _session_goal_store.get_active_goal_context() is not None
                    for _gt_name, _gt_schema, _gt_handler in _build_global_gt(
                        task_graph_store=_global_tg_store,
                        goal_resolver=_gt_resolver,
                    ):
                        deskpet_tool_registry_v2.register(
                            name=_gt_name,
                            toolset="goal",
                            schema=_gt_schema,
                            handler=_gt_handler,
                            permission_category="read_file",
                            source="builtin",
                            # create/update mutate the goal_tasks table →
                            # not concurrency-safe; list/get are read-only.
                            concurrency_safe=_gt_name in (
                                "goal_task_list", "goal_task_get",
                            ),
                            replace_allowed=True,
                            visible_when=_gt_visible,
                            visibility_scope="session",
                        )
                    logger.info("goal_task_tools_registered_global count=4")
            except Exception as _gt_exc:  # noqa: BLE001
                logger.warning("goal_task_tools_register_failed: %s", _gt_exc)
            logger.info("companion_code_v1_goal_mode_ready")
        except Exception as _gm_exc:  # noqa: BLE001
            logger.warning("companion_code_v1_goal_mode_init_failed: %s", _gm_exc)
            _session_goal_store = None
            _goal_checker = None
    service_context.register("session_goal_store", _session_goal_store)
    service_context.register("goal_checker", _goal_checker)

    # ─── 七步问题处理流水线资产构造（plans/2026-06-24-problem-handling-pipeline-maoxuan）───
    # 决策1：测试环境出厂 enabled 默认 true → 默认进入构造分支。flag off → 全 register(None) 占位
    # （B1：否则后续 service_context.get("problem_pipeline") 因 name∉_VALID_SERVICES 抛 ValueError）。
    _pp_cfg = getattr(getattr(config, "features", None), "problem_pipeline", None)
    if _pp_cfg is not None and getattr(_pp_cfg, "enabled", False):
        try:
            from deskpet.agent.intent_triage import IntentTriage, _PRE_ANALYSIS_SCHEMA
            from deskpet.agent.evidence_gate import EvidenceGate
            from deskpet.agent.problem_pipeline import ProblemHandlingPipeline
            # 决策3：analysis_model 留空 → 每次调用复用当前主 LLM；非空 →
            # 从当前主 LLM 克隆独立 model。不能在 boot 时冻结 provider，因为
            # Relay bridge 会在登录后热切换 module-level local_llm。
            # 决策4：Step1+3 合并 → 单一绑定合并 schema 的 callable（_make_str_llm_call 透传 response_format）。
            if getattr(_pp_cfg, "analysis_model", ""):
                _pre_llm = _make_session_aware_llm_call(
                    max_tokens=1536,
                    response_format=_PRE_ANALYSIS_SCHEMA,
                )
            else:
                _pre_llm = _make_session_aware_llm_call(
                    max_tokens=1536,
                    response_format=_PRE_ANALYSIS_SCHEMA,
                )
            service_context.register("problem_pipeline", ProblemHandlingPipeline(
                enabled=True,
                intent_triage=(IntentTriage(
                                   _pre_llm,
                                   clarify_threshold=_pp_cfg.intent_clarify_threshold,
                                   timeout_s=getattr(_pp_cfg, "analysis_timeout_s", 30.0),
                               ) if _pp_cfg.intent_triage else None),
                observability_events=_pp_cfg.observability_events,
            ))
            if _pp_cfg.evidence_gate:
                service_context.register("pipeline_evidence_gate", EvidenceGate(
                    investigative_tools=_pp_cfg.evidence_investigative_tools or None,
                    max_nudges=_pp_cfg.evidence_max_nudges,
                ))
            else:
                service_context.register("pipeline_evidence_gate", None)
            # self_check / convergence 在 build_agent / AgentLoop 内构造，service 仅占位 None：
            service_context.register("pipeline_self_check_gate", None)
            service_context.register("pipeline_convergence_controller", None)
            logger.info("problem_pipeline_init enabled=true intent=%s evidence=%s self_check=%s",
                        _pp_cfg.intent_triage, _pp_cfg.evidence_gate, _pp_cfg.self_check)
        except Exception as _pp_exc:  # noqa: BLE001 — 构造失败不崩 boot，退回 None 占位（kill-switch）
            logger.warning("problem_pipeline_init_failed: %s — disabled", _pp_exc)
            service_context.register("problem_pipeline", None)
            service_context.register("pipeline_evidence_gate", None)
            service_context.register("pipeline_self_check_gate", None)
            service_context.register("pipeline_convergence_controller", None)
    else:
        # flag off：register(None) 占位（B1 硬要求）。
        service_context.register("problem_pipeline", None)
        service_context.register("pipeline_evidence_gate", None)
        service_context.register("pipeline_self_check_gate", None)
        service_context.register("pipeline_convergence_controller", None)

    # WI-4.0 compaction：ContextCompressor 构造 + 注入 build_agent。
    # flag OFF（默认）→ _context_compressor = None → AgentLoop BC（不调）。
    # flag ON → 真构造；build_agent 工厂从 service_context 取并传给 AgentLoop。
    _context_compressor = None
    _compaction_enabled = bool(
        getattr(getattr(config, "features", None), "compaction_enabled", False)
    )
    if _compaction_enabled:
        try:
            from deskpet.agent.context_compressor import (
                ContextCompressor as _CtxCompressor,
            )
            # Context compression must start no later than 70% of the selected
            # model window.  A model-specific value below 70% remains valid.
            # Use the already-constructed local_llm (haiku-scale) for the
            # summary call so we don't spin up a new connection.
            # FP-2 真机修复: compressor 调 chat_with_fallback,而裸 provider
            # (OpenAICompatibleProvider) 没有该方法 → AttributeError 被
            # safe-fail 吞掉,压缩永远失败(should_compress 过了也白过)。
            # 复用 codify 同款 shim 包装(见下方 _CodifyShim 注释,同一个坑)。
            _compactor_llm = _make_session_aware_llm_call(
                max_tokens=1024
            )
            # 2026-06-12: 压缩窗口不再 hardcode 32000 —— 按当前主模型经
            # model_info 三层解析(BUILTIN ← 用户档位 override)。用户在
            # 「模型与参数」面板选 1M 档后,重启即对压缩阈值生效。
            _cmp_window, _cmp_threshold, _cmp_eff_pct = 32000, 0.70, 0.95
            try:
                from llm import model_info as _mi_cmp
                # P-B 修复: 用有效出站模型(运行时覆盖后,如 gpt-5.5)解析压缩窗口,而非
                # config.raw 旧种子(gemma 32K)→ 否则按错模型阈值过早压缩浪费大窗口。
                _cmp_model = effective_llm_model(config).strip()
                if _cmp_model:
                    _cmp_info = _mi_cmp.resolve(_cmp_model)
                    _cmp_window = int(_cmp_info.context_window)
                    _cmp_threshold = min(
                        float(_cmp_info.compact_at_pct),
                        0.70,
                    )
                    # WI-1: effective_pct 供 buffer 触发公式(剩余 token buffer)。
                    _cmp_eff_pct = float(_cmp_info.effective_pct)
            except Exception as _cmp_exc:  # noqa: BLE001
                logger.warning(
                    "compaction_window_resolve_failed err=%s — fallback 32000",
                    str(_cmp_exc)[:120],
                )
            # WI-1B-5: microcompact size-aware flag(默认 OFF=BC,保护仍按"最近 N 条")。
            _micro_size_aware = bool(
                getattr(
                    getattr(config, "features", None),
                    "microcompact_size_aware",
                    False,
                )
            )
            _context_compressor = _CtxCompressor(
                llm_registry=_compactor_llm,
                context_window=_cmp_window,
                threshold_percent=_cmp_threshold,
                effective_pct=_cmp_eff_pct,
                microcompact_size_aware=_micro_size_aware,
            )
            logger.info(
                "wi4_0_compaction_enabled context_window=%d threshold=%.2f "
                "trigger_tokens=%d eff_pct=%.2f"
                % (
                    _cmp_window,
                    _cmp_threshold,
                    _context_compressor.trigger_tokens(),
                    _cmp_eff_pct,
                )
            )
        except Exception as _cmp_init_exc:  # noqa: BLE001
            logger.warning(
                "wi4_0_compaction_init_failed err=%s — disabled",
                _cmp_init_exc,
            )
            _context_compressor = None
    service_context.register("context_compressor", _context_compressor)

    # 缺口 5c — SkillMatcher (WI-4.1)：embedding 相似度披露 + remount。
    # auto_disclosure flag ON 才构造 SkillMatcher(embedder).build(loader.all())
    # 并注入给 SkillComponent（下方 build_default_assembler 调用）+ build_agent (缺口 5a/5b)。
    # flag OFF → matcher=None → SkillComponent / agent_loop 全程降级 desc-only (BC)。
    _skill_matcher = None
    _auto_disclosure_flag = bool(
        getattr(
            getattr(getattr(config, "skills", None), "auto_disclosure", None),
            "enabled",
            False,
        )
    )
    if _auto_disclosure_flag:
        try:
            from deskpet.skills.skill_matcher import SkillMatcher as _SkillMatcher
            _skill_matcher = _SkillMatcher(embedder=_embedder)
            # TC-5.1 修复 (2026-06-11)：生产 embedder.encode 是 async，原 sync
            # build() 是静默 no-op（缓存恒空）。这里是 module-level（无运行
            # loop），真正的 build_async 预热在 lifespan 里 create_task
            # （锚点 fp5_skill_matcher_prewarmed）。
            logger.info("fp5_auto_disclosure_wiring_ready matcher=%s", True)
        except Exception as _ad_wire_exc:  # noqa: BLE001
            logger.warning(
                "fp5_auto_disclosure_wiring_failed err=%s — disabled", _ad_wire_exc
            )
            _skill_matcher = None
    # matcher/loader 注入 SkillComponent 在下方 build_default_assembler 调用处
    # （缺口 5c）；注入 build_agent 在 chat handler 处取用（缺口 5a/5b）。
    service_context.register("skill_matcher", _skill_matcher)

    # FP-4 B-10：goal→facts 双写钩接电。
    # 条件：goal_mode ON + goal_facts_hook flag ON + 两个 store 都已构造。
    # goal_store 不 import facts（§1.7 冻结约束）→ 通过 bind_on_goal_set 注入 callback。
    _goal_facts_hook_flag = bool(getattr(_v2_cfg, "goal_facts_hook", False))
    if (
        _session_goal_store is not None
        and _facts_store is not None
        and _goal_facts_hook_flag
    ):
        try:
            _fs_ref = _facts_store  # closure capture

            async def _goal_to_facts(sid: str, text: str) -> None:
                """单向钩：goal set → facts upsert(category=goal, scope=session)。

                TC-4.5 修复: 用 upsert_replacing 而非裸 upsert —— 后者是纯
                INSERT,真机同 key 堆了 15 行全 active;replacing 版保证
                goal_<sid> 始终单条 active,旧目标走 supersede 链。
                """
                try:
                    await _fs_ref.upsert_replacing(
                        category="goal",
                        subject="user",
                        key=f"goal_{sid}",
                        value=text,
                        confidence=0.9,
                        source_msg_id=None,
                        evidence=f"goal set for session {sid}",
                        scope="session",
                    )
                except Exception as _upsert_exc:  # noqa: BLE001 — safe-fail
                    logger.warning(
                        "goal_to_facts_upsert_failed sid=%s: %s", sid, _upsert_exc
                    )

            _session_goal_store.bind_on_goal_set(_goal_to_facts)
            logger.info("b10_goal_facts_hook_bound")
        except Exception as _b10_exc:  # noqa: BLE001 — safe-fail
            logger.warning("b10_goal_facts_hook_bind_failed: %s", _b10_exc)

    # P4-S14 + S15: ContextAssembler — pass embedder so TaskClassifier can
    # use the embed-tier route (rule → embed → llm cascade). When BGE-M3
    # isn't loaded yet, the embed path silently falls through to default —
    # graceful degradation already implemented in the classifier.
    # 记忆系统升级 WI-M1.6: workspace 工作记忆。flag 开 → 构造
    # WorkspaceMemoryStore，注入 file 工具（file_read/file_write 记动作 +
    # workspace_recall 查回）和 assembler 组件。flag 关 → store=None，
    # 工具不记、组件空转、workspace_state 表不会被建（Strangler-Fig）。
    _workspace_mem_store = None
    if config.memory.v2.workspace_memory:
        try:
            from deskpet.memory.workspace import (
                WorkspaceMemoryStore as _WorkspaceMemoryStore,
            )
            from deskpet.tools.file_tools import (
                set_workspace_store as _set_workspace_store,
            )
            _workspace_mem_store = _WorkspaceMemoryStore(_state_db_path)
            _set_workspace_store(_workspace_mem_store)
            logger.info("p4_workspace_memory_ready")
        except Exception as _ws_exc:  # noqa: BLE001
            logger.warning(
                "p4_workspace_memory_init_failed", error=str(_ws_exc),
            )
            _workspace_mem_store = None

    from deskpet.agent.assembler import build_default_assembler as _build_assembler
    _persona_inject_flag = bool(getattr(_v2_cfg, "persona_inject", False))
    # BUG-B Phase 2 WI-5：复活组装期 classifier llm 层。原 llm_registry=None → classifier
    # 跳过 llm tier，embed 撞锁时直接 default='chat'（真 code/debug 问题拿到 chat persona/工具/skill
    # bundle，P2 组装质量缺陷）。注入现成 shim（范式同 codify main.py:1868）让 llm tier 真跑。
    # R2 命门：shim 忽略 classifier 传入的 model（tool_use_shim.py:45），实际跑 local_llm.model
    # (relay 主模型 gpt-5.5)；timeout 经 build_default_assembler 调到 ≥6s 适配 thinking 4-6s。
    _classifier_llm = None
    try:
        from agent.tool_use_shim import OpenAICompatibleAgentLLM as _ClsShim
        _cls_provider = local_llm or cloud_llm
        if _cls_provider is not None:
            _classifier_llm = _ClsShim(provider=_cls_provider)
            logger.info("assembler_classifier_llm_injected provider=%s", type(_cls_provider).__name__)
    except Exception as _cls_exc:  # noqa: BLE001 — 注入失败退回 None（classifier 降级 WI-6 词法地板，不崩 boot）
        logger.warning("assembler_classifier_llm_inject_failed: %s — fallback lexical floor", _cls_exc)
        _classifier_llm = None
    _assembler = _build_assembler(
        embedder=_embedder,
        llm_registry=_classifier_llm,
        llm_timeout_s=8.0,
        enabled=True,
        context_window=32_000,
        budget_ratio=0.6,
        workspace_memory_store=_workspace_mem_store,
        # FP-4 WI-3.2：人格画像注入（flag 关 → component 空转，BC）。
        facts_store=_facts_store if _persona_inject_flag else None,
        persona_inject=_persona_inject_flag,
        # FP-5 缺口 5c (2026-06-06)：auto_disclosure flag ON 时注入 matcher/loader
        # → SkillComponent 真做 embedding 披露；flag OFF → 两者 None → desc-only (BC)。
        skill_matcher=_skill_matcher,
        skill_loader=_managed_skill_projection,
        # FP-5 缺口 5j (2026-06-06 子代理复评)：把 auto_disclosure 配置交给
        # assembler 当 assemble() 的 default_config → 任何 venue（文字 Harness /
        # 语音 voice_pipeline / 未来新增）都自动拿到 skills 配置，根治 venue-miss
        # （原靠各调用方各自传 → 语音 venue 漏传，第 8 处同根 bug）。
        auto_disclosure_config=(
            {
                "enabled": bool(config.skills.auto_disclosure.enabled),
                "strong_threshold": float(config.skills.auto_disclosure.strong_threshold),
                "budget_tokens": int(config.skills.auto_disclosure.budget_tokens),
                "per_skill_max_tokens": int(config.skills.auto_disclosure.per_skill_max_tokens),
            }
            if getattr(getattr(config, "skills", None), "auto_disclosure", None)
            else None
        ),
        # WI-5：把 knowledge_enabled 也喂进 assembler 的 default_config，使
        # assemble() 的 per-turn config（SkillComponent 过滤 + assembler prefer
        # 注入都读它）拿得到该 flag。否则触发式知识注入恒不生效（真机暴露）。
        knowledge_enabled=bool(
            getattr(getattr(config, "skills", None), "knowledge_enabled", False)
        ),
    )
    service_context.register("context_assembler", _assembler)

    # P4-S16: 这三个 handle 升级成正式注册服务（之前挂私有 _p4_* 属性）。
    # context.py 已把名字加进 _VALID_SERVICES。lifespan 通过 sc.get(name) 拉。
    service_context.register("session_db", _session_db)
    service_context.register("vector_worker", _vector_worker)
    service_context.register("embedder", _embedder)
    # Stage 2 WI-S2.1a / E3 v2：facts_store 注册供 p4_ipc.py 的
    # memory_facts_list / memory_forget / memory_forget_undo handlers 使用。
    if _facts_store is not None:
        service_context.register("facts_store", _facts_store)

    # F3 (2026-05-31)：memory_tools.bind() 注入的 _facts_store 是
    # memory_search / memory_write / memory_read / memory_forget **四个工具
    # 共用**的（都在 tools/memory_tools.py 模块级共享同一 _facts_store）。
    # 旧实现把整段 bind 包在 `if config.memory.v2.memory_forget` 里 →
    # forget flag 默认 False 时，连只读的 memory_search（WI-T3.1 基础工具，
    # 与 forget 无关）也 not bound。这是 flag 误连坐 bug。
    #
    # 修法：facts_store 在就 bind（让 4 个工具都可用）；forget 的**危险性**
    # 单独由两道闸控制，不靠这个 flag：
    #   1. enable_natural_language —— 仅 memory_forget flag 开 + forget 配置
    #      开时才允许自然语言模式（query=...）；否则 _forget_by_query 返 skipped。
    #   2. forget 工具 register 时的 dangerous=True + 用户显式确认。
    #   fact_id 模式（最直接、最少歧义）本就永远开放（见 memory_tools._forget_by_id）。
    # R8 v2：forgotten_at 列 ALTER 失败 → 关掉自然语言 forget（fact_id 仍可用）。
    if _facts_store is not None:
        _forget_nl = bool(
            config.memory.v2.memory_forget
            and config.memory.v2.forget.enable_natural_language
        )
        try:
            from deskpet.memory.schema_v2_migrator import (
                alter_failures as _alter_failures2,
            )
            if _alter_failures2().get("forgotten_at"):
                _forget_nl = False
                logger.warning(
                    "p4_memory_forget_nl_force_disabled",
                    reason="forgotten_at column unavailable",
                )
        except Exception:  # noqa: BLE001
            pass
        try:
            from deskpet.tools import memory_tools as _memory_tools
            _memory_tools.bind(
                facts_store=_facts_store,
                embedder=_embedder,
                llm_call=_make_session_aware_llm_call(),
                enable_natural_language=_forget_nl,
            )
            logger.info(
                "p4_memory_tools_bound",
                forget_natural_language=_forget_nl,
                memory_forget_flag=config.memory.v2.memory_forget,
            )
        except Exception as _mf_exc:  # noqa: BLE001
            logger.warning(
                "p4_memory_tools_bind_failed", error=str(_mf_exc),
            )

    # OpenSpec 2026-05-16-async-image-gen: ImageGenerationWorker —
    # generate_image submits a job and returns instantly; this worker
    # does the slow the relay POST + retry + save + open in the background
    # and pushes the result back to the pet via _image_notifier (reuses
    # the control-ws chat_v2_final path → zero frontend change, petText
    # cleaning applies). async_enabled=false → not started (tool falls
    # back to legacy sync blocking).
    _img_cfg = (config.raw.get("image") if hasattr(config, "raw") else None) or {}

    async def _image_notifier(_sid: str, _text: str) -> None:
        # 1) push as chat_v2_final to the originating session's control
        #    conn (fallback: default / broadcast all, mirrors auto_resume).
        _payload = {"type": "chat_v2_final", "payload": {"text": _text}}
        _wsobj = (
            _control_connections.get(_sid)
            or _control_connections.get("default")
        )
        try:
            if _wsobj is not None:
                await _wsobj.send_json(_payload)
            else:
                for _w in list(_control_connections.values()):
                    try:
                        await _w.send_json(_payload)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as _nex:  # noqa: BLE001
            logger.debug("image_notifier_ws_failed sid=%s err=%s", _sid, _nex)
        # 2) persist to SessionDB as an assistant message so
        #    ChatHistoryPanel / L2 recall have it (same as a normal reply).
        try:
            _sdb_n = service_context.get("session_db")
            if _sdb_n is not None:
                await _sdb_n.append_message(
                    session_id=_sid, role="assistant", content=_text,
                )
        except Exception as _pex:  # noqa: BLE001
            logger.debug("image_notifier_persist_failed err=%s", _pex)

    _image_worker = _ImageWorker(
        notifier=_image_notifier,
        max_concurrent=int(_img_cfg.get("max_concurrent", 2)),
    )
    service_context.register("image_worker", _image_worker)

    # P5-S1: supervisor watchdog infrastructure. SessionActivity tracks
    # AgentEvent timestamps + tool-call signature windows so the watchdog
    # can detect stuck general-agent tasks. Watchdog itself is started
    # later in lifespan() with a 30s grace period; here we just register
    # the data structures so the WS forwarder can bump them.
    from agent.session_activity import SessionActivityStore as _SessionActivityStore
    _session_activity_store = _SessionActivityStore()
    service_context.register("session_activity", _session_activity_store)

    # P5-S4: NudgeQueue is created here (not in lifespan) so the message
    # build path can reach it without nullable plumbing.
    from agent.nudge_queue import NudgeQueue as _NudgeQueue
    _nudge_queue = _NudgeQueue(
        cap=int((config.raw.get("supervisor") or {}).get("max_hints_per_session", 3))
    )
    service_context.register("nudge_queue", _nudge_queue)

    # Register general-agent tools that need closures over runtime objects
    # (SessionDB for todo_write and durable child delegation).
    if deskpet_tool_registry_v2 is not None:
        try:
            from deskpet.tools.code_tools import (
                build_todo_write_tool as _build_todo_write_tool,
            )

            # todo_write needs SessionDB plus a broadcaster.
            # Wire a real broadcaster that pushes
            # `session_todo_update` to whichever control WebSocket owns
            # the active task session. Without this, todos write to
            # DB but the frontend's TodoListPanel doesn't update until
            # the user manually closes / reopens the panel (or sends a
            # new chat turn that re-pulls). Now they update live.
            async def _todo_broadcaster(msg: dict) -> None:
                """Best-effort task-list projection to connected clients."""
                # Snapshot to avoid mutation during iteration when a
                # ws closes mid-broadcast.
                targets = list(_control_connections.values())
                for ws in targets:
                    try:
                        await ws.send_json(msg)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("task_todo_broadcast_failed", error=str(exc))

            _todo_handler, _todo_schema = _build_todo_write_tool(
                session_db=_session_db,
                session_id_resolver=lambda: "default",
                broadcaster=_todo_broadcaster,
            )

            # ===== 子代理并发驱动 (plans/2026-06-21-subagent-concurrency-driver/) =====
            # WI-1.4/1.5：构造 SubagentScheduler（lane-aware 有界调度）+ 进度出口
            # （metrics 盘 + WS 广播）。flag subagent_driver OFF 时全不构造（BC）。
            _subagent_scheduler = None
            _kind_overrides = None

            def _subagent_progress_sink(payload: dict) -> None:
                # 1) metrics（盘，复用 VALID_EVENTS 的 subagent_progress）
                try:
                    from observability.metrics_sink import record as _rec
                    _rec("subagent_progress", {
                        k: payload.get(k)
                        for k in ("run_id", "kind", "task_id", "status", "duration_ms")
                    })
                except Exception:  # noqa: BLE001 — 进度永不阻断
                    pass
                # 2) WS 广播给所有 control 连接（fire-and-forget，非阻塞）
                try:
                    _msg = {"type": "subagent_progress", "payload": payload}
                    for _ws in list(_control_connections.values()):
                        try:
                            asyncio.create_task(_ws.send_json(_msg))
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001
                    pass

            if bool(getattr(
                getattr(config, "features", None), "subagent_driver", False,
            )):
                try:
                    from deskpet.agent.subagent_scheduler import (
                        SubagentScheduler as _SubSched,
                    )
                    from deskpet.agent.task_kinds import (
                        load_kind_overrides as _load_kinds,
                    )
                    from config import get_subagent_concurrency as _get_conc

                    _glob_cap, _lane_caps = _get_conc(config)
                    _kind_overrides = _load_kinds(
                        (getattr(config, "raw", None) or {}).get("agent")
                    )
                    _subagent_scheduler = _SubSched(
                        global_concurrency=_glob_cap,
                        lane_caps=_lane_caps,
                        progress_sink=_subagent_progress_sink,
                    )
                    service_context.register("subagent_scheduler", _subagent_scheduler)
                    logger.info(
                        "subagent_driver_ready global=%d lanes=%s",
                        _glob_cap, _lane_caps,
                    )
                except Exception as _sd_exc:  # noqa: BLE001
                    logger.warning("subagent_driver_init_failed: %s", _sd_exc)

            _sched = service_context.get("subagent_scheduler")
            from deskpet.tools import research_tools as _rt
            if _sched is not None and _rt._fanout_enabled():
                _rt.set_subagent_scheduler(_sched)
                logger.info("deepresearch subagent fanout ENABLED (scheduler wired)")

            # Stable public names, one durable owner.  The ReAct collaborator
            # intercepts these calls and emits DelegateRun; registered handlers
            # are fail-closed guards and never create nested AgentLoop objects.
            from deskpet.tools.code_tools.spawn_subagents_tool import (
                build_await_subagents_tool as _build_await_subagents_tool,
                product_delegation_tool_catalog as _delegation_catalog,
            )

            _delegates = _delegation_catalog()
            _agent_handler, _agent_schema = _delegates["agent"]
            _feat = getattr(config, "features", None)
            _parallel_handler = _parallel_schema = None
            if bool(getattr(_feat, "agent_parallel", False)) or bool(
                getattr(_feat, "subagent_driver", False)
            ):
                _parallel_handler, _parallel_schema = _delegates["agent_parallel"]
            _spawn_team_handler = _spawn_team_schema = None
            if bool(getattr(_feat, "agent_team", False)):
                _spawn_team_handler, _spawn_team_schema = _delegates["spawn_team"]
            _spawn_subs_handler = _spawn_subs_schema = None
            _await_subs_handler = _await_subs_schema = None
            if bool(getattr(_feat, "subagent_nonblocking", False)):
                _spawn_subs_handler, _spawn_subs_schema = _delegates["spawn_subagents"]
                (
                    _await_subs_handler,
                    _await_subs_schema,
                ) = _build_await_subagents_tool(
                    lambda: service_context.get("workflow_service")
                )
            logger.info("harness_child_delegation_tools_ready")

            # Re-register the general tool set including runtime closures.
            from deskpet.tools.code_tools import (
                register_code_tools as _register_code_tools_full,
            )
            _register_code_tools_full(
                deskpet_tool_registry_v2,
                todo_write_handler=_todo_handler,
                todo_write_schema=_todo_schema,
                agent_handler=_agent_handler,
                agent_schema=_agent_schema,
                agent_parallel_handler=_parallel_handler,
                agent_parallel_schema=_parallel_schema,
                spawn_team_handler=_spawn_team_handler,
                spawn_team_schema=_spawn_team_schema,
                spawn_subagents_handler=_spawn_subs_handler,
                spawn_subagents_schema=_spawn_subs_schema,
                await_subagents_handler=_await_subs_handler,
                await_subagents_schema=_await_subs_schema,
            )
            from deskpet.tools.code_tools.clarify_tool import (
                _SCHEMA as _clarify_schema,
                durable_clarification_boundary_only as _clarify_boundary_only,
            )

            deskpet_tool_registry_v2.register(
                name="ask_clarification",
                toolset="code",
                schema=_clarify_schema,
                handler=_clarify_boundary_only,
                context_handler=lambda args, context: _clarify_boundary_only(
                    args, context.call_id, execution_context=context,
                ),
                permission_category="read_file",
                source="builtin",
                timeout_seconds=130.0,
                replace_allowed=True,
            )
            logger.info("general_agent_tools_registered", count=6)
        except Exception as _ct_exc:  # noqa: BLE001
            logger.warning(
                "p4_s22_code_tools_register_failed",
                error=str(_ct_exc),
            )

    logger.info(
        "p4_services_registered",
        l1_dir=str(_l1_dir),
        state_db=str(_state_db_path),
        memory_manager=True,
        skill_loader=True,
        context_assembler=True,
        embedder_mock_when_missing=True,
        vector_worker=True,
        retriever=True,
    )

    # P4-S20-D: 把 _state_db_path 暴露给 IPC handler 用 (memory_summarize_now)
    _summarizer_state_db_path = _state_db_path
except Exception as _p4_exc:
    # S13 stay-alive guarantee: ANY P4 import/init failure must not block the
    # legacy chat path. p4_ipc.py already handles None services gracefully.
    logger.warning(
        "p4_services_registration_failed",
        error=str(_p4_exc),
        error_type=type(_p4_exc).__name__,
    )
    _summarizer_state_db_path = None  # type: ignore[assignment]


base_agent = SimpleLLMAgent(llm, memory=memory_store)
agent = ToolUsingAgent(base=base_agent, registry=tool_registry)
service_context.register("agent_engine", agent)


async def _initialize_capability_runtime() -> None:
    """Compose capability packs on the existing execution UoW."""

    if deskpet_tool_registry_v2 is None:
        raise RuntimeError("ToolRegistry V2 is unavailable")
    workflow_service = service_context.get("workflow_service")
    if workflow_service is None:
        raise RuntimeError("workflow execution UoW is unavailable")

    from deskpet.capabilities.contracts import CapabilityScope
    from deskpet.capabilities.builder import CapabilityBuilderHost
    from deskpet.capabilities.platform import (
        BrokeredInvocationAuthority,
        CapabilityPlatform,
    )
    from deskpet.capabilities.execution_scope_authority import (
        SqliteCurrentExecutionScopeAuthority,
    )
    from deskpet.capabilities.refresh import (
        CapabilityRefreshService,
        CapabilityRefreshStagingService,
        RegistryExposureToolSetRebuilder,
        SqliteCapabilityRefreshSnapshotRepository,
    )
    from deskpet.capabilities.store import CapabilityStore
    from deskpet.capabilities.runtime_ledger import (
        CapabilityStoreRuntimeSetLedger,
    )
    from deskpet.capabilities.ui_service import CapabilityCenterService
    from deskpet.capabilities.configured_catalog import (
        parse_configured_pack_sources,
    )
    from deskpet.tools.capabilities import ToolCapabilityResolver
    from deskpet.permissions.legacy_auto_mode import (
        import_legacy_auto_mode_once,
    )
    from deskpet.permissions.runtime import PreparedAuthorizationRuntime
    from deskpet.permissions.admission import AdmissionTaskGrantRuntime
    from deskpet.types.task_grants import (
        ResourceSelector,
        TaskGrant,
        selector_covers,
    )

    uow = workflow_service.execution_uow
    store = CapabilityStore(uow)
    await store.initialize()
    imported = await import_legacy_auto_mode_once(store, _automode_path)
    if permission_gate_v2 is not None:
        # Compatibility mirror for legacy direct handlers only. SQLite is the
        # authority and the gate has no persistence path bound.
        permission_gate_v2.set_auto_mode(
            imported.policy_state.mode == "auto"
        )

    authority_cache: dict[str, tuple[TaskGrant, str, int]] = {}
    platform_box: dict[str, CapabilityPlatform] = {}

    async def resolve_brokered_authority(context):
        provenance = await uow.get_prepared_authorization_provenance(
            run_id=context.run_id,
            call_id=context.call_id,
            effect_id=context.effect_id,
        )
        if provenance is None:
            raise ValueError("prepared TaskGrant provenance is unavailable")
        grant = TaskGrant.from_dict(dict(provenance["task_grant"]))
        policy = dict(provenance["policy_state"])
        if grant.root_run_id != context.root_run_id or not grant.is_current(
            now=time.time(),
            policy_mode=str(policy["mode"]),
            policy_generation=int(policy["generation"]),
        ):
            raise ValueError("prepared TaskGrant is stale or belongs to another root")
        platform = platform_box.get("platform")
        if platform is None:
            raise RuntimeError("capability platform is not initialized")
        snapshot = await platform.hub.snapshot(
            CapabilityScope.for_run(
                context.root_run_id,
                project_root=context.workspace or context.write_scope_root,
            )
        )
        authority_cache[grant.task_grant_id] = (
            grant,
            str(policy["mode"]),
            int(policy["generation"]),
        )
        return BrokeredInvocationAuthority(
            grant.task_grant_id,
            snapshot.stamp.to_dict(),
        )

    def authorize_brokered_paths(
        source: str,
        destination: str,
        task_grant_id: str,
    ) -> bool:
        cached = authority_cache.get(task_grant_id)
        if cached is None:
            return False
        grant, mode, generation = cached
        if not grant.is_current(
            now=time.time(),
            policy_mode=mode,
            policy_generation=generation,
        ):
            return False
        requested = (
            ResourceSelector.filesystem(source, "read"),
            ResourceSelector.filesystem(destination, "write"),
        )
        return all(
            any(
                selector_covers(granted, item)
                for granted in grant.resource_selectors
            )
            for item in requested
        )

    capability_config = dict(
        (getattr(config, "raw", {}).get("capabilities") or {})
    )
    configured_sources_payload = dict(
        capability_config.get("sources") or {}
    )
    configured_sources_env = os.environ.get(
        "DESKPET_CAPABILITY_SOURCES_JSON", ""
    ).strip()
    if configured_sources_env:
        env_payload = json.loads(configured_sources_env)
        if not isinstance(env_payload, dict):
            raise ValueError(
                "DESKPET_CAPABILITY_SOURCES_JSON must encode an object"
            )
        configured_sources_payload.update(env_payload)
    configured_sources = parse_configured_pack_sources(
        configured_sources_payload
    )

    platform = CapabilityPlatform(
        registry=deskpet_tool_registry_v2,
        store=store,
        user_data_root=_paths.user_data_dir(),
        resource_authorizer=authorize_brokered_paths,
        brokered_authority_resolver=resolve_brokered_authority,
        skill_loader=service_context.get("skill_loader"),
        mcp_manager=service_context.get("mcp_manager"),
        plugin_manager=globals().get("plugin_manager"),
        configured_sources=configured_sources,
        first_party_pack_roots=tuple(
            pack_dir
            for root in _paths.first_party_capability_pack_roots()
            if root.is_dir()
            for pack_dir in sorted(root.iterdir())
            if pack_dir.is_dir()
            and (pack_dir / "deskpet-pack.json").is_file()
        ),
        identity_gate=service_context.get("companion_identity_gate"),
        execution_scope_authority=SqliteCurrentExecutionScopeAuthority(store),
        runtime_set_ledger=CapabilityStoreRuntimeSetLedger(store),
    )
    from deskpet.capabilities.run_catalog import (
        FirstPartyFrozenSkillResolver,
        SqliteRunCatalogLeasePreparer,
    )
    from deskpet.companion.skills import inventory_first_party_skill_packs

    platform.run_catalog_lease_preparer = SqliteRunCatalogLeasePreparer(
        store=store,
        registry=deskpet_tool_registry_v2,
        hub=platform.hub,
    )
    first_party_skill_inventory = tuple(
        getattr(
            service_context.get("managed_skill_discovery_projection"),
            "inventory",
            (),
        )
    ) or inventory_first_party_skill_packs(
        _paths.first_party_capability_pack_roots()
    )
    frozen_skill_resolver = FirstPartyFrozenSkillResolver(
        store=store,
        inventory=first_party_skill_inventory,
    )
    from deskpet.skills.loader import SkillPackSnapshotResolver

    skill_pack_snapshot_resolver = SkillPackSnapshotResolver(
        version_store=store,
        selection_source=service_context.get(
            "managed_skill_discovery_projection"
        ),
    )
    from deskpet.tools import skill_tools as _skill_tools

    _skill_tools.bind(skill_resolver=frozen_skill_resolver)
    _bind_frozen_skill_snapshot_resolver(skill_pack_snapshot_resolver)
    platform_box["platform"] = platform
    initialized = await platform.initialize()
    builder_host = CapabilityBuilderHost(
        uow,
        deskpet_tool_registry_v2,
        environment=platform.environment,
        runtime=platform.runtime,
        staging_base=platform.manager.layout.root / "builder",
        tool_service=platform.tool_service,
    )
    authorization_runtime = PreparedAuthorizationRuntime(store)
    admission_task_grant_runtime = AdmissionTaskGrantRuntime(
        store,
        capability_managed_root=platform.manager.layout.root,
    )
    refresh_snapshots = SqliteCapabilityRefreshSnapshotRepository(store)
    refresh_staging = CapabilityRefreshStagingService(store)
    refresh_service = CapabilityRefreshService(
        store=store,
        hub=platform.hub,
        snapshots=refresh_snapshots,
        rebuilder=RegistryExposureToolSetRebuilder(
            ToolCapabilityResolver(deskpet_tool_registry_v2)
        ),
    )
    service_context.register("capability_store", store)
    service_context.register("capability_platform", platform)
    service_context.register(
        "capability_center",
        CapabilityCenterService(
            store=store,
            manager=platform.manager,
            hub=platform.hub,
            notifier=_broadcast_control,
        ),
    )
    service_context.register("capability_builder_host", builder_host)
    service_context.register("authorization_runtime", authorization_runtime)
    service_context.register(
        "admission_task_grant_runtime",
        admission_task_grant_runtime,
    )
    service_context.register(
        "capability_refresh_snapshots", refresh_snapshots
    )
    service_context.register(
        "capability_refresh_staging", refresh_staging
    )
    service_context.register(
        "capability_refresh_service", refresh_service
    )
    logger.info(
        "capability_platform_ready",
        recovered_operations=len(initialized.recovered_operations),
        first_party_installs=len(initialized.first_party_installs),
        control_tools_registered=initialized.control_tools_registered,
        authorization_mode=imported.policy_state.mode,
        authorization_generation=imported.policy_state.generation,
        legacy_policy_consumed=imported.consumed_now,
    )


async def _set_authorization_auto_mode(enabled: bool) -> bool:
    store = service_context.get("capability_store")
    if store is None:
        raise RuntimeError("authorization policy store is unavailable")
    requested = "auto" if enabled else "manual"
    for _ in range(3):
        current = await store.get_policy_state()
        if current.mode == requested:
            state = current
            break
        try:
            state = await store.compare_and_set_policy_mode(
                requested,
                expected_generation=current.generation,
            )
            break
        except Exception as exc:  # concurrent Settings writers retry CAS
            if getattr(exc, "code", "") != "policy_generation_conflict":
                raise
    else:
        raise RuntimeError("authorization policy changed repeatedly")
    if permission_gate_v2 is not None:
        permission_gate_v2.set_auto_mode(state.mode == "auto")
    return state.mode == "auto"


async def _authorization_auto_mode() -> bool:
    store = service_context.get("capability_store")
    if store is None:
        return bool(
            permission_gate_v2 is not None
            and getattr(permission_gate_v2, "auto_mode", False)
        )
    return (await store.get_policy_state()).mode == "auto"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Preload models on startup (best-effort — failures logged but don't block)."""
    logger.info("preloading models...")
    from llm.resolution import ProviderRoutingReadiness

    global _provider_registry
    _provider_readiness = ProviderRoutingReadiness()
    service_context.register("provider_routing_readiness", _provider_readiness)
    try:
        # SessionDB owns the migration/backup recovery path. Registry identity
        # is loaded only after v23 exists, then legacy bindings are reconciled
        # in one SQLite transaction before ingress opens.
        await _session_db.initialize()
        _migrate_legacy_provider_config(_CONFIG_PATH)
        _provider_registry = LLMProviderRegistry(_CONFIG_PATH)
        await _session_db.reconcile_provider_bindings(
            {
                entry["id"]: (
                    str(entry["incarnation_id"]),
                    int(entry["config_revision"]),
                )
                for entry in _provider_registry.list_providers()
            },
            registry_digest=_provider_registry.snapshot_digest(),
        )
        service_context.register("provider_registry", _provider_registry)
        _provider_readiness.mark_ready()
        logger.info(
            "provider_routing_ready providers=%d",
            len(_provider_registry.list_providers()),
        )
    except Exception as _provider_start_exc:  # noqa: BLE001
        _provider_readiness.mark_failed(type(_provider_start_exc).__name__)
        service_context.register("provider_registry", None)
        logger.exception("provider_routing_startup_failed: %s", _provider_start_exc)
    from deskpet.retrieval.runtime import build_search_gateway, set_default_gateway
    _search_gateway = build_search_gateway(config)
    set_default_gateway(_search_gateway)
    service_context.register("search_gateway", _search_gateway)
    logger.info(
        "search_gateway_ready",
        enabled=config.search_gateway.enabled,
        providers=config.search_gateway.providers,
    )
    # Stage 2 round 2 fix：workspace_memory hook (os_tools/read_file 等
    # sync handler) 依赖 file_tools._workspace_loop 引用主 loop。set_
    # workspace_store 在 module top-level 跑时拿不到 loop —— 这里补绑。
    try:
        from deskpet.tools.file_tools import rebind_loop as _rebind_ws_loop
        ok = _rebind_ws_loop()
        logger.info("p4_workspace_memory_loop_rebound", ok=ok)
    except Exception as _exc:  # noqa: BLE001
        logger.debug("workspace loop rebind failed: %s", _exc)
    # P2-1-S8: billing DB must exist before the first chat call. Failure
    # here is logged but doesn't block startup — the ledger simply won't
    # record anything until the DB is reachable on a future boot.
    try:
        await billing_ledger.init()
        logger.info("billing_ledger_ready", db_path=str(config.billing.db_path))
    except Exception as exc:
        logger.warning("billing_ledger_init_failed", error=str(exc))
    for name in ("vad_engine", "asr_engine", "tts_engine"):
        engine = service_context.get(name)
        if engine and hasattr(engine, "load"):
            try:
                await engine.load()
                logger.info("loaded", engine=name)
            except Exception as exc:
                logger.warning("failed_to_load", engine=name, error=str(exc))
                # P3-S2: persist structured error so /health + WS startup_status
                # can surface "degraded" state instead of silently accepting
                # requests that will later 500.
                startup_errors.record(name, exc)
    # P4-S13: async initialisers for the P4 read-only stack. Each failure is
    # isolated — MemoryManager needing a base dir doesn't prevent SkillLoader
    # from scanning the built-in dir, etc.
    _sdb = service_context.get("session_db")
    if _sdb is not None:
        try:
            await _sdb.initialize()
            logger.info("p4_session_db_ready", path=str(_sdb._db_path))
        except Exception as exc:
            logger.warning("p4_session_db_init_failed", error=str(exc))
    try:
        from deskpet.workflows.bootstrap import build_workflow_service
        from deskpet.workflows.retention import RetentionPolicy
        from deskpet.workflows.store import RegisteredBlobStore

        _workflow_delivery_blobs = RegisteredBlobStore(
            _paths.user_data_dir() / "workflows" / "blobs",
            _paths.user_data_dir() / "data" / "workflow.db",
        )

        async def _resolve_workflow_content(ref: str) -> bytes:
            wire_ref = str(ref or "").strip()
            digest = wire_ref[7:] if wire_ref.startswith("sha256:") else wire_ref
            return await _workflow_delivery_blobs.get(digest)

        async def _workflow_delivery_epoch(event, delivery) -> int | None:
            if _sdb is None or _workflow_service is None:
                return None
            run_id = str(event.get("run_id") or "")
            target_sid = str(delivery.get("target_id") or "default")
            ref = await _workflow_service.run_store.get_session_ref(run_id, "delivery")
            if ref is None or str(ref["session_id"]) != target_sid:
                return None
            state = await _sdb.get_session_delivery_state(target_sid)
            if state.get("deleted_at") is not None:
                return None
            expected_epoch = int(ref["session_epoch"])
            if int(state.get("epoch", 0)) != expected_epoch:
                return None
            return expected_epoch

        async def _workflow_session_delivery(event, delivery):
            from deskpet.workflows.delivery import DeliveryAttemptResultV1

            target_sid = str(delivery.get("target_id") or "default")
            expected_epoch = await _workflow_delivery_epoch(event, delivery)
            if expected_epoch is None:
                return DeliveryAttemptResultV1.discarded_fenced(
                    "session_epoch_mismatch"
                )
            payload = dict(event.get("payload") or {})
            nested = payload.get("payload")
            nested = dict(nested) if isinstance(nested, dict) else {}
            text = ""
            from deskpet.workflows.adapters.product_delivery import (
                resolve_workflow_content_text,
                workflow_message_projection,
            )

            content_payload = nested if nested.get("content_ref") else payload
            resolved = await resolve_workflow_content_text(
                content_payload, _resolve_workflow_content
            )
            if resolved is not None:
                text = resolved
            if event.get("event_type") == "workflow.progress":
                v2_payload = payload if payload.get("schema_version") == 2 else nested
                if v2_payload.get("schema_version") == 2:
                    from deskpet.workflows.progress import validated_v2_stage_text

                    text = validated_v2_stage_text(v2_payload).strip()
            if not text:
                text = str(nested.get("text") or payload.get("text") or "").strip()
            if not text:
                if event.get("event_type") == "workflow.accepted":
                    text = f"{payload.get('workflow_name', '任务')} 已开始"
                elif event.get("event_type") == "workflow.decision":
                    text = "Workflow decision is waiting for your confirmation."
                elif event.get("event_type") == "workflow.final":
                    text = f"任务已结束：{payload.get('status', 'unknown')}"
                else:
                    return DeliveryAttemptResultV1.retryable_failure(
                        "projection_content_missing"
                    )
            if _sdb is not None:
                projection = workflow_message_projection(str(event.get("event_type") or ""))
                message_id = await _sdb.append_message_if_epoch(
                    target_sid,
                    projection.role,
                    text,
                    expected_epoch=expected_epoch,
                    workflow_event_id=str(event["event_id"]),
                    projection_kind=projection.projection_kind,
                    context_visibility=projection.context_visibility,
                    skip_embed=projection.skip_embed,
                    root_run_id=(
                        str(event.get("root_run_id") or "").strip() or None
                    ),
                )
                if message_id is None:
                    return DeliveryAttemptResultV1.discarded_fenced(
                        "session_epoch_mismatch"
                    )
                return DeliveryAttemptResultV1.delivered()
            return DeliveryAttemptResultV1.retryable_failure(
                "session_db_unavailable"
            )

        async def _workflow_websocket_delivery(event, delivery):
            from deskpet.workflows.delivery import DeliveryAttemptResultV1

            if await _workflow_delivery_epoch(event, delivery) is None:
                return DeliveryAttemptResultV1.discarded_fenced(
                    "session_epoch_mismatch"
                )
            from deskpet.workflows.adapters.product_delivery import (
                resolve_workflow_content_text,
            )

            outbound_event = dict(event)
            outbound_payload = dict(event.get("payload") or {})
            nested = outbound_payload.get("payload")
            content_payload = (
                dict(nested)
                if isinstance(nested, dict) and nested.get("content_ref")
                else outbound_payload
            )
            resolved = await resolve_workflow_content_text(
                content_payload, _resolve_workflow_content
            )
            if resolved is not None:
                # Resolved bytes are transport-only.  The immutable outbox row
                # remains ref-only and therefore replay-safe.
                outbound_payload["text"] = resolved
            outbound_event["payload"] = outbound_payload
            envelope_type = (
                "workflow_final"
                if event.get("event_type") == "workflow.final"
                else "workflow_event"
            )
            envelope = {
                "type": envelope_type,
                "payload": {
                    **outbound_event,
                    "session_id": str(delivery.get("target_id") or "default"),
                },
            }
            from deskpet.workflows.delivery import broadcast_websocket_best_effort

            return await broadcast_websocket_best_effort(
                envelope, list(_control_connections.values())
            )

        async def _workflow_artifact_publisher(payload):
            envelope = {"type": "tool_result", "payload": dict(payload)}
            seen: set[int] = set()
            for target_ws in list(_control_connections.values()):
                marker = id(target_ws)
                if marker in seen:
                    continue
                seen.add(marker)
                await asyncio.wait_for(target_ws.send_json(envelope), timeout=1.0)

        product_handlers = {}
        if _sdb is not None:
            from deskpet.workflows.adapters.product_delivery import ProductDeliveryAdapter
            from deskpet.workflows.store import WorkflowRunStore

            product_delivery = ProductDeliveryAdapter(
                session_db=_sdb,
                receipt_store=_get_receipt_store(),
                workflow_store=WorkflowRunStore(
                    _paths.user_data_dir() / "data" / "workflow.db"
                ),
                artifact_publisher=_workflow_artifact_publisher,
                content_resolver=_resolve_workflow_content,
            )
            product_handlers = product_delivery.handlers()

        _workflow_service = await build_workflow_service(
            _paths.user_data_dir(),
            delivery_handlers={
                "session_message": _workflow_session_delivery,
                "websocket": _workflow_websocket_delivery,
                **product_handlers,
            },
            retention_policy=RetentionPolicy.from_days(
                terminal_days=config.workflows.terminal_retention_days,
                evaluation_tombstone_days=config.workflows.evaluation_retention_days,
                orphan_grace_hours=config.workflows.orphan_grace_hours,
            ),
            session_delivery_state_reader=(
                _sdb.get_session_delivery_state if _sdb is not None else None
            ),
            activate=False,
        )
        from deskpet.workflows.contracts import WorkflowContext
        from deskpet.workflows.definitions.research_core import (
            FetchPort,
            ResearchArtifactPort,
            ResearchSearchPort,
            ResearchSearchResults,
            legacy_ports,
        )
        from deskpet.workflows.definitions.v1 import deep_research_initial_state
        from deskpet.workflows.definitions.v2 import (
            deep_research_initial_state as deep_research_v2_initial_state,
        )
        from deskpet.workflows.definitions.v3 import (
            deep_research_initial_state as deep_research_v3_initial_state,
        )
        from deskpet.workflows.definitions.v4 import (
            deep_research_initial_state as deep_research_v4_initial_state,
        )
        from deskpet.workflows.definitions.v5 import (
            deep_research_initial_state as deep_research_v5_initial_state,
        )
        from deskpet.workflows.definitions.v6 import (
            build_continuation_start_payload as build_deep_research_v6_continuation_payload,
            decode_continuation_snapshot as decode_deep_research_v6_continuation_snapshot,
            deep_research_initial_state as deep_research_v6_initial_state,
        )
        from deskpet.workflows.definitions.v7 import (
            deep_research_initial_state as deep_research_v7_initial_state,
        )
        from deskpet.workflows.launcher import WorkflowLauncher
        from deskpet.workflows.native import NativeExecutionPolicy
        from deskpet.workflows.runtime_adapters import (
            DEEP_RESEARCH_EXTENSION,
            DeepResearchContinuationAdapter,
            DeepResearchRuntimeExtension,
        )
        from deskpet.workflows.terminal_projection import TERMINAL_COMMIT_CAPABILITY
        from deskpet.tools import research_tools as _workflow_research_tools

        _workflow_launcher = WorkflowLauncher(_workflow_service)
        setattr(_workflow_service, "launcher", _workflow_launcher)
        # Runtime adapters must all be registered before the production
        # registry is sealed and any durable recovery is allowed to run.
        service_context.register("workflow_service", _workflow_service)
        from deskpet.execution.harness_public_read_service import (
            HarnessPublicReadService,
        )

        _harness_public_read_service = HarnessPublicReadService(
            workflow_db_path=_paths.user_data_dir() / "data" / "workflow.db",
            state_db_path=_state_db_path,
            cursor_secret=hashlib.sha256(
                f"harness-inspector-v3:{SHARED_SECRET}".encode("utf-8")
            ).digest(),
        )
        service_context.register(
            "harness_public_read_service", _harness_public_read_service
        )
        from deskpet.execution.fences import UnboundRunExecutionFence
        from deskpet.execution.provider_invocations import (
            ProviderInvocationCoordinator,
        )
        from deskpet.execution.provider_fault_script import ProviderFaultScriptV1

        _execution_fence = UnboundRunExecutionFence()
        _provider_fault_script = ProviderFaultScriptV1.from_environment()
        from deskpet.execution.tool_completion_latch_script import (
            ToolCompletionLatchScriptV1,
        )
        from deskpet.tools.registry import registry as _runtime_tool_registry

        _runtime_tool_registry.set_tool_completion_latch(
            ToolCompletionLatchScriptV1.from_environment()
        )
        _provider_invocation_coordinator = ProviderInvocationCoordinator(
            _workflow_service.execution_uow,
            fence_reacquirer=_execution_fence.acquire,
            fault_script=_provider_fault_script,
        )
        service_context.register(
            "run_execution_fence_acquirer", _execution_fence.acquire
        )
        service_context.register(
            "provider_invocation_coordinator",
            _provider_invocation_coordinator,
        )
        from deskpet.execution.provider_workload_audit import (
            ProviderWorkloadAuditStore,
        )
        from deskpet.execution.provider_workloads import (
            BackgroundModelPolicy,
            ProviderWorkloadBreaker,
            ProviderWorkloadRoutePolicy,
            ProviderWorkloadRouter,
            SessionProviderWorkloadTargetResolver,
        )

        _provider_registry_for_workloads = service_context.get(
            "provider_registry"
        )

        def _provider_workload_factory(entry, model):
            if _provider_registry_for_workloads is None:
                raise RuntimeError("provider registry is unavailable")
            provider = OpenAICompatibleProvider(
                base_url=entry.base_url,
                api_key=(
                    _provider_registry_for_workloads.resolve_api_key(entry.id)
                    or "ollama"
                ),
                model=model,
                temperature=getattr(entry, "temperature", 0.7),
                sanitize_inline_cot_dsml=_sanitize_cot_dsml,
                code_params=getattr(entry, "code_params", None),
                is_relay=(getattr(entry, "source", "") == "relay"),
            )
            provider.provider_id = str(entry.id)
            return provider

        _provider_workload_audit = ProviderWorkloadAuditStore(_state_db_path)
        _session_workload_resolver = SessionProviderWorkloadTargetResolver(
            registry=_provider_registry_for_workloads,
            session_db=_session_db,
            readiness=getattr(
                service_context, "provider_routing_readiness", None
            ),
            provider_factory=_provider_workload_factory,
            start_snapshot_reader=_workflow_service.execution_uow,
        )
        _background_model_policy = BackgroundModelPolicy(
            registry=_provider_registry_for_workloads,
            readiness=getattr(
                service_context, "provider_routing_readiness", None
            ),
            provider_factory=_provider_workload_factory,
        )
        _provider_workload_router = ProviderWorkloadRouter(
            ProviderWorkloadRoutePolicy(
                session=_session_workload_resolver,
                background=_background_model_policy,
            ),
            breaker=ProviderWorkloadBreaker(),
            audit=_provider_workload_audit,
            fault_script=_provider_fault_script,
        )
        service_context.register(
            "provider_workload_audit", _provider_workload_audit
        )
        service_context.register(
            "provider_workload_router", _provider_workload_router
        )
        if _provider_fault_script is not None:
            from deskpet.execution.provider_fault_scenario_runner import (
                ProviderFaultScenarioRunner,
            )

            _provider_fault_scenario_runner = ProviderFaultScenarioRunner(
                _provider_fault_script,
                _provider_workload_router,
                task_owner=_provider_workload_maintenance_tasks,
            )
            await _provider_fault_scenario_runner.start()

        async def _deep_context_factory(row, start_payload) -> WorkflowContext:
            llm_call = await _workflow_research_tools._resolve_default_llm_call()
            ports = legacy_ports(llm_call=llm_call, search=None, extract=None)
            return WorkflowContext(
                ports={"llm": ports.llm, "search": ports.search, "fetch": ports.fetch},
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
            )

        def _deep_state_factory(**values):
            return deep_research_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=dict(values.get("research_config") or {}),
                blob_root=str(values.get("blob_root") or ""),
            )

        async def _deep_v2_context_factory(row, start_payload) -> WorkflowContext:
            from deskpet.retrieval.contracts import FetchRequest, SearchRequest
            from deskpet.retrieval.runtime import get_default_gateway
            from deskpet.retrieval.query_terms import extract_query_terms

            llm_call = await _workflow_research_tools._resolve_default_llm_call()
            legacy = legacy_ports(llm_call=llm_call, search=None, extract=None)
            gateway = get_default_gateway()
            run_id = str(row.get("run_id") or "")
            async def _search(query: str, *, max_results: int) -> list[dict]:
                response = await gateway.search(
                    SearchRequest(
                        query=query,
                        max_results=max_results,
                        mode="research",
                        run_id=run_id,
                    )
                )
                payload = response.to_dict()
                attempts = list(payload.get("attempts") or [])
                public_codes = [
                    str(value.get("public_error_code"))
                    for value in attempts
                    if isinstance(value, dict) and value.get("public_error_code")
                ]
                coverage = dict(response.coverage())
                safe_attempts = [
                    {
                        "provider": str(value.get("provider") or ""),
                        "status": str(value.get("status") or ""),
                        "permit": str(value.get("permit") or "closed"),
                        "probe_outcome": (
                            str(value.get("probe_outcome"))
                            if value.get("probe_outcome")
                            else None
                        ),
                        "upstream_called": bool(value.get("upstream_called", False)),
                        "is_rescue": bool(value.get("is_rescue", False)),
                    }
                    for value in attempts
                    if isinstance(value, dict)
                ]
                return ResearchSearchResults(
                    list(payload["results"]),
                    observation={
                        "request_id": str(payload.get("request_id") or ""),
                        "run_id": str(payload.get("run_id") or run_id),
                        "degraded": bool(response.degraded),
                        "reason_code": public_codes[-1] if public_codes else None,
                        "engines_tried": list(response.engines_tried),
                        "engines_hit": list(response.engines_hit),
                        "provider_attempt_count": int(coverage["actual_requests"]),
                        "provider_probe_count": int(coverage["probes"]),
                        "provider_attempts": safe_attempts,
                        "rescue_status": str(response.rescue_status),
                        "rescue_error_code": (
                            response.rescue_error_code.value
                            if response.rescue_error_code is not None
                            else None
                        ),
                        "rescue_upstream_called": bool(response.rescue_upstream_called),
                        "coverage": coverage,
                    },
                )

            async def _extract(url: str) -> dict:
                if gateway.fetch_service is None:
                    return await legacy.fetch.extract(url)
                document = await gateway.fetch_service.fetch(
                    FetchRequest(url=url, timeout=15.0, render_policy="auto")
                )
                return document.to_dict()

            async def _save_artifact(
                *, topic: str, report_md: str, report_hash: str, run_id: str
            ) -> dict[str, object]:
                return await asyncio.to_thread(
                    _workflow_research_tools.save_workflow_report,
                    topic=topic,
                    report_md=report_md,
                    report_hash=report_hash,
                    run_id=run_id,
                )

            blob_root = _paths.user_data_dir() / "workflows" / "blobs"
            return WorkflowContext(
                ports={
                    "llm": legacy.llm,
                    "search": ResearchSearchPort(
                        search_call=_search,
                        direct_call=legacy.search.direct_call,
                        reset_runtime=legacy.search.reset_runtime,
                    ),
                    "fetch": FetchPort(_extract),
                    "artifact": ResearchArtifactPort(_save_artifact),
                    "blob": RegisteredBlobStore(
                        blob_root,
                        _workflow_service.run_store.path,
                    ),
                    "native_execution_policy": NativeExecutionPolicy(
                        max_parallel_tasks=max(
                            2,
                            min(
                                6,
                                int(config.workflows.deep_research_max_parallel_tasks),
                            ),
                        )
                    ),
                },
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
            )

        async def _deep_v7_context_factory(row, start_payload) -> WorkflowContext:
            base = await _deep_v2_context_factory(row, start_payload)
            scheduler = service_context.get("subagent_scheduler")
            if scheduler is None:
                raise RuntimeError("subagent_scheduler_unavailable")
            return WorkflowContext(
                ports={**base.ports, "subagent_scheduler": scheduler},
                request_id=base.request_id,
                turn_id=base.turn_id,
            )

        async def _deep_v5_context_factory(row, start_payload) -> WorkflowContext:
            import hashlib as _hashlib
            import json as _json
            import time as _time
            from datetime import datetime as _datetime, timezone as _timezone
            from deskpet.retrieval.contracts import (
                DimensionSearchRequest,
                FetchRequest,
                SearchRequest,
            )
            from deskpet.retrieval.query_terms import extract_query_terms
            from deskpet.retrieval.runtime import get_default_gateway
            from deskpet.workflows.adapters.research_runtime import (
                BoundResearchEffectContext,
                DurableResearchCallEffectAdapter,
                DurableResearchLLMStagePort,
                MonotonicResearchClock,
                DurableResearchReadEffectAdapter,
                DurableResearchReadStagePort,
                DurableResearchSnapshotPort,
                DurableV5ControlPort,
                WorkflowControlSignalHub,
            )
            from deskpet.workflows.adapters.deep_research_v5_evidence_runtime import (
                candidate_from_document,
                candidate_from_json,
                evaluate_runtime_evidence,
                select_dimension_fair_rows,
            )
            from deskpet.workflows.contracts import canonical_json
            from deskpet.workflows.definitions.deep_research_v5_contracts import ResearchBrief
            from deskpet.workflows.definitions.research_core import ResearchLLMPortV2
            from deskpet.workflows.effects import EffectExecutionContext, EffectJournal
            from deskpet.workflows.store import RunFence

            run_id = str(row.get("run_id") or "")
            session_id = str(row.get("session_id") or "")
            db_path = _workflow_service.run_store.path
            blobs = RegisteredBlobStore(
                _paths.user_data_dir() / "workflows" / "blobs", db_path
            )
            journal = EffectJournal(db_path)
            repository = _workflow_service.research_repository
            signals = WorkflowControlSignalHub(repository)
            async def _effect_context(identity):
                current = await _workflow_service.run_store.get_run(identity.run_id)
                if current is None or str(current.get("status")) != "running":
                    raise RuntimeError("v5 research effect run is not active")
                owner = str(current.get("lease_owner") or "")
                if not owner:
                    raise RuntimeError("v5 research effect run has no lease owner")
                digest = _hashlib.sha256(
                    f"{identity.run_id}:{identity.checkpoint_ns}:{identity.checkpoint_id}:"
                    f"{identity.task_id}:{identity.attempt}".encode("utf-8")
                ).hexdigest()
                context = EffectExecutionContext(
                    journal=journal,
                    fence=RunFence(
                        identity.run_id,
                        owner,
                        int(current.get("lease_epoch") or 0),
                        int(current.get("run_version") or 0),
                    ),
                    node_execution_id=f"research-v5-{digest[:32]}",
                    workflow_name="deep_research",
                    workflow_version="v5",
                    node_id=identity.node_id,
                )
                return BoundResearchEffectContext(identity, context)

            llm_call = await _workflow_research_tools._resolve_default_llm_call_v2()
            llm_effect = DurableResearchCallEffectAdapter(
                journal=journal,
                blobs=blobs,
                llm=ResearchLLMPortV2(llm_call),
                resolve_effect_context=_effect_context,
                reserve_cost_micros=lambda _role, _input, output: max(0, output * 20),
                actual_cost_micros=lambda result: max(
                    0, int((result.input_tokens or 0) * 3 + (result.output_tokens or 0) * 15)
                ),
                control_signals=signals,
            )

            gateway = get_default_gateway()

            async def _search_transport(payload, identity):
                if payload.get("_stage") == "direct":
                    return {"results": [], "executed_query_fingerprints": []}
                brief_raw = payload.get("research_brief")
                brief = ResearchBrief.from_json(brief_raw) if isinstance(brief_raw, dict) else None
                dimensions = {
                    item.dimension_id: item for item in brief.dimensions
                } if brief is not None else {}
                raw_queries = payload.get("initial_queries") or payload.get("query_strategy_queries") or []
                query_rows = [item for item in raw_queries if isinstance(item, dict)] if isinstance(raw_queries, list) else []
                work = payload.get("work_item")
                if isinstance(work, dict) and work.get("query"):
                    query_rows.append({
                        "dimension_id": work.get("dimension_id"),
                        "query": work.get("query"),
                        "source_target": work.get("source_target"),
                        "fingerprint": payload.get("query_fingerprint"),
                    })
                requests = []
                metadata = {}
                for index, item in enumerate(query_rows):
                    query = str(item.get("query") or "").strip()
                    dimension_id = str(item.get("dimension_id") or "").strip()
                    if not query or not dimension_id or dimension_id not in dimensions:
                        continue
                    fingerprint = str(item.get("fingerprint") or "").strip() or _hashlib.sha256(
                        query.casefold().encode("utf-8")
                    ).hexdigest()
                    dimension = dimensions[dimension_id]
                    semantic_context = " ".join(
                        (
                            brief.user_question if brief is not None else "",
                            dimension.question,
                            *(brief.subjects if brief is not None else ()),
                            *dimension.query_targets,
                            query,
                        )
                    )
                    semantic_terms = extract_query_terms(semantic_context)
                    request = SearchRequest(
                        query=query,
                        max_results=10,
                        mode="research",
                        run_id=identity.run_id,
                        dimension_id=dimension_id,
                        query_terms=semantic_terms,
                    )
                    requests.append(DimensionSearchRequest(
                        dimension_id=dimension_id,
                        request=request,
                        core=dimension.importance == "core",
                        priority=100 - index,
                        query_terms=semantic_terms,
                        query_fingerprint=fingerprint,
                    ))
                    metadata[(dimension_id, query)] = {
                        "query": query,
                        "query_fingerprint": fingerprint,
                        "source_target": str(item.get("source_target") or ""),
                    }
                batch = await gateway.search_dimension_batch(
                    requests,
                    min_results_per_core=1,
                )
                results = []
                for dimension_result in batch.results:
                    response = dimension_result.response
                    meta = metadata.get(
                        (dimension_result.dimension_id, response.query),
                        {"query": response.query, "query_fingerprint": "", "source_target": ""},
                    )
                    for candidate in response.to_dict().get("results") or []:
                        if isinstance(candidate, dict):
                            results.append({
                                **candidate,
                                "dimension_id": dimension_result.dimension_id,
                                **meta,
                            })
                output = {
                    "results": results,
                    "executed_query_fingerprints": sorted({
                        request.budget_query_key for request in requests
                    }),
                    # The allocator keeps immutable tuples internally, while
                    # durable effect outcomes require strict JSON.  Reuse the
                    # retrieval contract's canonical projection instead of
                    # leaking the internal budget snapshot into workflow state.
                    "budget": batch.to_dict()["budget"],
                }
                # A gap unit is atomic search+fetch+admit under this durable
                # effect fence.  Returning only URLs would make gap_join claim
                # progress without adding any usable evidence.
                if payload.get("_stage") == "gap_work":
                    return await _fetch_transport(
                        {**payload, "gap_work_result": output}, identity
                    )
                return output

            search_effect = DurableResearchReadEffectAdapter(
                journal=journal,
                blobs=blobs,
                resolve_effect_context=_effect_context,
                transport=_search_transport,
                control_signals=signals,
            )

            async def _fetch_transport(payload, identity):
                rows = []
                for key in ("search_result", "direct_result", "gap_work_result"):
                    value = payload.get(key)
                    if isinstance(value, dict) and isinstance(value.get("results"), list):
                        rows.extend(item for item in value["results"] if isinstance(item, dict))
                brief_raw = payload.get("research_brief")
                if not isinstance(brief_raw, dict):
                    raise ValueError("v5 evidence fetch requires research_brief")
                brief = ResearchBrief.from_json(brief_raw)
                dimension_by_id = {item.dimension_id: item for item in brief.dimensions}
                documents = []
                current_candidates = []
                fetch_rows = select_dimension_fair_rows(
                    rows,
                    tuple(dimension_by_id),
                    limit=24,
                )
                for raw in fetch_rows:
                    url = str(raw.get("url") or "")
                    dimension_id = str(raw.get("dimension_id") or "")
                    if not url or dimension_id not in dimension_by_id:
                        continue
                    try:
                        document = await gateway.fetch_service.fetch(
                            FetchRequest(
                                url=url,
                                timeout=20.0,
                                render_policy="auto",
                                run_id=identity.run_id,
                            )
                        ) if gateway.fetch_service is not None else None
                    except Exception:
                        continue
                    if document is not None:
                        value = document.to_dict()
                        documents.append(value)
                        dimension = dimension_by_id[dimension_id]
                        year_anchors = (
                            tuple(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", brief.user_question))
                            if "official_statistics" in dimension.expected_source_types
                            else ()
                        )
                        current_candidates.append(candidate_from_document(
                            dimension_id=dimension_id,
                            dimension_text=" ".join(
                                (
                                    dimension.question,
                                    *dimension.query_targets,
                                )
                            ),
                            query=str(raw.get("query") or ""),
                            document=value,
                            subjects=brief.subjects,
                            required_anchors=year_anchors,
                        ))
                prior_candidates = []
                for value in payload.get("evidence_candidates", []):
                    if isinstance(value, dict):
                        prior_candidates.append(candidate_from_json(value))
                evaluated = evaluate_runtime_evidence(
                    brief=brief,
                    candidates=(*prior_candidates, *current_candidates),
                )
                ref_by_candidate = {}
                current_by_id = {item.candidate_id: item for item in current_candidates}
                for candidate_value in evaluated["evidence_candidates"]:
                    if not candidate_value.get("admitted"):
                        continue
                    candidate_id = str(candidate_value["candidate_id"])
                    candidate = current_by_id.get(candidate_id)
                    if candidate is None:
                        continue
                    document = next(
                        (item for item in documents if str(item.get("canonical_url") or item.get("url") or "") == str(candidate.canonical_url or candidate.url)),
                        None,
                    )
                    if document is None:
                        continue
                    encoded = canonical_json(document).encode("utf-8")
                    ref = await blobs.put(
                        encoded, identity,
                        media_type="application/vnd.deskpet.research-evidence+json",
                    )
                    ref_by_candidate[candidate_id] = ref.sha256
                passage_refs = sorted(set(
                    [str(item) for item in payload.get("passage_blob_refs", [])]
                    + list(ref_by_candidate.values())
                ))
                executed = sorted(set(
                    [str(item) for item in payload.get("executed_query_fingerprints", [])]
                    + [
                        str(item)
                        for key in ("search_result", "gap_work_result")
                        for item in (
                            payload.get(key, {}).get("executed_query_fingerprints", [])
                            if isinstance(payload.get(key), dict) else []
                        )
                    ]
                ))
                return {
                    "documents": documents,
                    **evaluated,
                    "passage_blob_refs": passage_refs,
                    "executed_query_fingerprints": executed,
                    "budget_summary": {"io_documents": len(documents)},
                }

            fetch_effect = DurableResearchReadEffectAdapter(
                journal=journal,
                blobs=blobs,
                resolve_effect_context=_effect_context,
                transport=_fetch_transport,
                control_signals=signals,
            )

            class _ArtifactStagePort:
                async def execute(self, *, stage, payload, identity):
                    synthesis = payload.get("synthesis_result")
                    synthesis = synthesis if isinstance(synthesis, dict) else {}
                    report_md = str(synthesis.get("report_md") or synthesis.get("summary") or "")
                    if not report_md:
                        raise ValueError("v5 report synthesis produced no report markdown")
                    report_hash = _hashlib.sha256(report_md.encode("utf-8")).hexdigest()
                    saved = await asyncio.to_thread(
                        _workflow_research_tools.save_workflow_report,
                        topic=str(payload.get("topic") or "research"),
                        report_md=report_md,
                        report_hash=report_hash,
                        run_id=run_id,
                    )
                    return {**saved, "report_ref": f"sha256:{saved['sha256']}"}

            return WorkflowContext(
                ports={
                    "llm": DurableResearchLLMStagePort(blobs=blobs, effect=llm_effect),
                    "search": DurableResearchReadStagePort(effect=search_effect, effect_name="search"),
                    "fetch": DurableResearchReadStagePort(effect=fetch_effect, effect_name="static_fetch"),
                    "artifact": _ArtifactStagePort(),
                    "control": DurableV5ControlPort(repository, signal_hub=signals),
                    "snapshot": DurableResearchSnapshotPort(blobs=blobs, repository=repository),
                    "clock": MonotonicResearchClock(),
                    "native_execution_policy": NativeExecutionPolicy(
                        max_parallel_tasks=max(2, min(6, int(config.workflows.deep_research_max_parallel_tasks)))
                    ),
                },
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
            )

        async def _deep_v6_context_factory(row, start_payload) -> WorkflowContext:
            import hashlib as _hashlib

            from deskpet.retrieval.runtime import get_default_gateway
            from deskpet.workflows.adapters.deep_research_v6_bootstrap import (
                build_deep_research_v6_context,
            )
            from deskpet.workflows.adapters.research_runtime import (
                BoundResearchEffectContext,
                WorkflowControlSignalHub,
            )
            from deskpet.workflows.effects import EffectExecutionContext, EffectJournal
            from deskpet.workflows.store import RunFence

            run_id = str(row.get("run_id") or "")
            db_path = _workflow_service.run_store.path
            blobs = RegisteredBlobStore(
                _paths.user_data_dir() / "workflows" / "blobs",
                db_path,
            )
            journal = EffectJournal(db_path)
            repository = _workflow_service.research_repository
            signals = WorkflowControlSignalHub(repository)
            await repository.ensure_snapshot_lineage(
                run_id=run_id,
                operation_id=f"research:{run_id}",
                budget_lease_id=_hashlib.sha256(
                    f"deep-research-v6-root-budget|{run_id}".encode("utf-8")
                ).hexdigest(),
            )

            async def _effect_context(identity):
                current = await _workflow_service.run_store.get_run(identity.run_id)
                if current is None or str(current.get("status")) != "running":
                    raise RuntimeError("v6 research effect run is not active")
                owner = str(current.get("lease_owner") or "")
                if not owner:
                    raise RuntimeError("v6 research effect run has no lease owner")
                digest = _hashlib.sha256(
                    f"{identity.run_id}:{identity.checkpoint_ns}:{identity.checkpoint_id}:"
                    f"{identity.task_id}:{identity.attempt}".encode("utf-8")
                ).hexdigest()
                context = EffectExecutionContext(
                    journal=journal,
                    fence=RunFence(
                        identity.run_id,
                        owner,
                        int(current.get("lease_epoch") or 0),
                        int(current.get("run_version") or 0),
                    ),
                    node_execution_id=f"research-v6-{digest[:32]}",
                    workflow_name="deep_research",
                    workflow_version="v6",
                    node_id=identity.node_id,
                )
                return BoundResearchEffectContext(identity, context)

            llm_call = await _workflow_research_tools._resolve_default_llm_call_v2()
            return build_deep_research_v6_context(
                blobs=blobs,
                journal=journal,
                resolve_effect_context=_effect_context,
                search_gateway=get_default_gateway(),
                llm_call=llm_call,
                control_signals=signals,
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
                max_parallel_tasks=int(
                    config.workflows.deep_research_max_parallel_tasks
                ),
                dispatch_fence_acquirer=service_context.get(
                    "run_execution_fence_acquirer"
                ),
                provider_invocation_coordinator=service_context.get(
                    "provider_invocation_coordinator"
                ),
            )

        def _deep_v2_state_factory(**values):
            return deep_research_v2_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=dict(values.get("research_config") or {}),
                blob_root=str(values.get("blob_root") or ""),
            )

        def _deep_v3_state_factory(**values):
            return deep_research_v3_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=dict(values.get("research_config") or {}),
                blob_root=str(values.get("blob_root") or ""),
            )

        def _deep_v4_state_factory(**values):
            return deep_research_v4_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=dict(values.get("research_config") or {}),
                blob_root=str(values.get("blob_root") or ""),
            )

        def _deep_v5_state_factory(**values):
            runtime = {
                **dict(values.get("research_config") or {}),
                "soft_checkpoint_seconds": config.research_v5.soft_checkpoint_seconds,
                "lease_seconds": config.research_v5.lease_seconds,
                "auto_cap_seconds": config.research_v5.auto_cap_seconds,
                "plateau_rounds": config.research_v5.plateau_rounds,
            }
            return deep_research_v5_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=runtime,
                blob_root=str(values.get("blob_root") or ""),
                operation_id=str(values.get("operation_id") or values["run_id"]),
                continuation_snapshot=(
                    dict(values["continuation_snapshot"])
                    if isinstance(values.get("continuation_snapshot"), dict)
                    else None
                ),
                parent_operation_id=(
                    str(values["parent_operation_id"])
                    if values.get("parent_operation_id") is not None
                    else None
                ),
                only_gaps=bool(values.get("only_gaps", False)),
            )

        async def _deep_v6_state_factory(**values):
            continuation_snapshot = None
            topic = str(values.get("topic") or "")
            answer_locale = str(values.get("answer_locale") or "zh-CN")
            if values.get("source_snapshot_hash") is not None:
                loader = getattr(
                    _workflow_service.research_repository,
                    "load_continuation_snapshot_v6",
                    None,
                )
                if not callable(loader):
                    raise RuntimeError("v6 continuation snapshot loader is unavailable")
                continuation_snapshot = decode_deep_research_v6_continuation_snapshot(
                    await loader(str(values["run_id"]))
                )
                blob_loader = getattr(_workflow_service, "_research_snapshot_loader", None)
                if not callable(blob_loader):
                    raise RuntimeError("v6 continuation registered blob loader is unavailable")
                spec_bytes = await blob_loader(str(continuation_snapshot["spec_ref"])[7:])
                spec_value = (
                    json.loads(spec_bytes.decode("utf-8"))
                    if isinstance(spec_bytes, bytes)
                    else spec_bytes
                )
                if not isinstance(spec_value, dict):
                    raise RuntimeError("v6 continuation spec blob is invalid")
                topic = str(spec_value.get("normalized_question") or "")
                answer_locale = str(spec_value.get("answer_locale") or "zh-CN")
            return deep_research_v6_initial_state(
                topic=topic,
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                answer_locale=answer_locale,
                schema_version=(
                    int(values["schema_version"])
                    if values.get("schema_version") is not None
                    else None
                ),
                parent_run_id=(
                    str(values["parent_run_id"])
                    if values.get("parent_run_id") is not None
                    else None
                ),
                source_snapshot_hash=(
                    str(values["source_snapshot_hash"])
                    if values.get("source_snapshot_hash") is not None
                    else None
                ),
                continuation_snapshot=continuation_snapshot,
            )

        def _deep_v7_state_factory(**values):
            return deep_research_v7_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                mode=str(values.get("mode") or "standard"),
                research_config=dict(values.get("research_config") or {}),
                blob_root=str(values.get("blob_root") or ""),
            )

        _workflow_launcher.register_adapter(
            "deep_research",
            "v1",
            state_factory=_deep_state_factory,
            context_factory=_deep_context_factory,
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v2",
            state_factory=_deep_v2_state_factory,
            context_factory=_deep_v2_context_factory,
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v3",
            state_factory=_deep_v3_state_factory,
            context_factory=_deep_v2_context_factory,
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v4",
            state_factory=_deep_v4_state_factory,
            context_factory=_deep_v2_context_factory,
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v5",
            state_factory=_deep_v5_state_factory,
            context_factory=_deep_v5_context_factory,
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v6",
            state_factory=_deep_v6_state_factory,
            context_factory=_deep_v6_context_factory,
            extensions={
                DEEP_RESEARCH_EXTENSION: DeepResearchRuntimeExtension(
                    new_runs_enabled=True,
                    action_ids=("generate_now", "continue_research", "cancel_settle"),
                    continuation=DeepResearchContinuationAdapter(
                        decode_snapshot=decode_deep_research_v6_continuation_snapshot,
                        build_child_payload=build_deep_research_v6_continuation_payload,
                    ),
                    terminal_commit_capability=TERMINAL_COMMIT_CAPABILITY,
                )
            },
        )
        _workflow_launcher.register_adapter(
            "deep_research",
            "v7",
            state_factory=_deep_v7_state_factory,
            context_factory=_deep_v7_context_factory,
        )

        from agent.tool_use_shim import OpenAICompatibleAgentLLM as _RecoveryShim
        from deskpet.workflows.adapters.code_runtime import (
            DurableToolRuntimeState as _RecoveryDurableToolRuntimeState,
            ProposalPort as _RecoveryProposalPort,
            ToolDispatchPort as _RecoveryToolDispatchPort,
        )
        from deskpet.workflows.definitions.v1 import (
            code_complex_initial_state as _code_recovery_initial_state,
            durable_task_initial_state as _durable_task_initial_state,
        )

        def _code_recovery_state_factory(**values):
            return _code_recovery_initial_state(
                request=str(values["request"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                session_ref=dict(values["session_ref"]),
                capability_snapshot=list(values["capability_snapshot"]),
                messages=list(values["messages"]),
                plan_steps=list(values["plan_steps"]),
                approval_required=bool(values["approval_required"]),
                started_at=float(values["started_at"]),
                request_id=str(values["request_id"]),
                turn_id=str(values["turn_id"]),
                provider_snapshot=dict(values["provider_snapshot"]),
                model_snapshot=dict(values["model_snapshot"]),
            )

        def _durable_task_state_factory(**values):
            return _durable_task_initial_state(
                request=str(values["request"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                session_ref=dict(values["session_ref"]),
                capability_snapshot=list(values["capability_snapshot"]),
                messages=list(values["messages"]),
                plan_steps=list(values["plan_steps"]),
                approval_required=bool(values["approval_required"]),
                started_at=float(values["started_at"]),
                request_id=str(values["request_id"]),
                turn_id=str(values["turn_id"]),
                provider_snapshot=dict(values["provider_snapshot"]),
                model_snapshot=dict(values["model_snapshot"]),
            )

        async def _code_recovery_context_factory(row, start_payload):
            provider_snapshot = dict(start_payload.get("provider_snapshot") or {})
            model_snapshot = dict(start_payload.get("model_snapshot") or {})
            provider = _resolve_code_recovery_provider(
                provider_snapshot=provider_snapshot,
                model_snapshot=model_snapshot,
                registry=service_context.get("provider_registry"),
                fallback=local_llm or cloud_llm,
            )
            shim = _RecoveryShim(provider=provider)
            session_id = str(row.get("session_id") or "default")
            provider_name = str(
                provider_snapshot.get("provider")
                or provider_snapshot.get("provider_id")
                or ""
            )
            model_name = str(
                model_snapshot.get("model") or ""
            )
            allowed_tools = tuple(
                str(item.get("tool_name") or "")
                for item in (start_payload.get("capability_snapshot") or ())
                if isinstance(item, dict) and item.get("tool_name")
            )
            raw_context_os = start_payload.get("context_os")
            prepared_tool_set = None
            eligibility = None
            execution_context = None
            context_os_enabled = bool(
                getattr(getattr(config, "features", None), "context_os_v1", False)
            )
            if context_os_enabled:
                if not isinstance(raw_context_os, dict):
                    raise RuntimeError("code recovery Context OS snapshot is unavailable")
                from deskpet.tools.capabilities import ToolExecutionContext
                from deskpet.tools.prepared_snapshot import load_context_os_snapshot

                prepared_tool_set, eligibility = load_context_os_snapshot(
                    raw_context_os
                )
                request_id = str(start_payload.get("request_id") or row.get("request_id") or "")
                turn_id = str(start_payload.get("turn_id") or row.get("turn_id") or "")
                if (
                    eligibility.session_id != session_id
                    or eligibility.request_id != request_id
                ):
                    raise RuntimeError("code recovery Context OS identity mismatch")
                deskpet_tool_registry_v2.validate_prepared_tool_set(
                    prepared_tool_set, eligibility=eligibility
                )
                prepared_names = {
                    capability.ref.name
                    for capability in (
                        *prepared_tool_set.direct,
                        *prepared_tool_set.activated,
                    )
                }
                _validate_code_recovery_capability_subset(
                    prepared_names=prepared_names,
                    allowed_tools=allowed_tools,
                )
                scope_store = service_context.get("tool_capability_scope_store")
                if scope_store is None:
                    raise RuntimeError("code recovery capability scope store unavailable")
                if scope_store.get(
                    prepared_tool_set.scope_id,
                    session_id=session_id,
                    request_id=request_id,
                ) is None:
                    try:
                        scope_store.open(prepared_tool_set, eligibility)
                    except ValueError:
                        if scope_store.get(
                            prepared_tool_set.scope_id,
                            session_id=session_id,
                            request_id=request_id,
                        ) is None:
                            raise
                workflow_name = str(row.get("workflow_name") or "code_complex")
                from deskpet.harness.adapters.legacy_execution_migration import (
                    resolve_frozen_legacy_workspace,
                )
                workspace = await resolve_frozen_legacy_workspace(
                    row=row,
                    start_payload=start_payload,
                    session_db=service_context.get("session_db"),
                )
                from deskpet.tools.capabilities import canonical_hash

                scope_hash = canonical_hash(
                    {
                        "session_id": session_id,
                        "workspace": workspace,
                        "write_scope_root": workspace,
                        "venue": (
                            "code" if workflow_name == "code_complex" else "text"
                        ),
                    }
                )
                execution_snapshot = await (
                    _workflow_service.execution_uow.read_run_start_snapshot(
                        str(row.get("run_id") or "")
                    )
                )
                frozen_run_context = (
                    json.loads(execution_snapshot.run_context_json)
                    if execution_snapshot is not None
                    else {}
                )
                root_run_id = str(
                    frozen_run_context.get("root_run_id")
                    or row.get("parent_run_id")
                    or row.get("run_id")
                    or ""
                )
                execution_context = ToolExecutionContext(
                    scope_id=prepared_tool_set.scope_id,
                    session_id=session_id,
                    request_id=request_id,
                    origin="agent",
                    root_run_id=root_run_id,
                    parent_run_id=(
                        str(row.get("parent_run_id") or "").strip() or None
                    ),
                    turn_id=turn_id,
                    venue=(
                        "code" if workflow_name == "code_complex" else "text"
                    ),
                    workspace=workspace,
                    write_scope_root=workspace,
                    capability_hash=prepared_tool_set.schema_fingerprint,
                    scope_hash=scope_hash,
                    provider_plan=tuple(
                        item
                        for item in (provider_name, model_name)
                        if item
                    ),
                    run_id=str(row.get("run_id") or ""),
                    trace_id=str(row.get("trace_id") or ""),
                )
            runtime_tool_state = (
                _RecoveryDurableToolRuntimeState(prepared_tool_set)
                if prepared_tool_set is not None
                else None
            )
            return WorkflowContext(
                ports={
                    "llm": _RecoveryProposalPort(
                        shim,
                        deskpet_tool_registry_v2,
                        session_id=session_id,
                        provider_name=provider_name,
                        model_name=model_name,
                        profile_key=(
                            f"workflow.{str(row.get('workflow_name') or 'code_complex')}"
                        ),
                        allowed_tools=allowed_tools,
                        context_os_v1=context_os_enabled,
                        prepared_tool_set=prepared_tool_set,
                        eligibility=eligibility,
                        execution_context=execution_context,
                        capability_scope_store=service_context.get(
                            "tool_capability_scope_store"
                        ),
                        runtime_tool_state=runtime_tool_state,
                        dispatch_fence_acquirer=service_context.get(
                            "run_execution_fence_acquirer"
                        ),
                        provider_invocation_coordinator=service_context.get(
                            "provider_invocation_coordinator"
                        ),
                        context_compressor=service_context.get(
                            "context_compressor"
                        ),
                    ),
                    "tool": _RecoveryToolDispatchPort(
                        deskpet_tool_registry_v2,
                        session_id=session_id,
                        execution_context=execution_context,
                        runtime_tool_state=runtime_tool_state,
                        workflow_name=str(
                            row.get("workflow_name") or "code_complex"
                        ),
                        dispatch_fence_acquirer=service_context.get(
                            "run_execution_fence_acquirer"
                        ),
                    ),
                },
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
            )

        _workflow_launcher.register_adapter(
            "code_complex",
            "v1",
            state_factory=_code_recovery_state_factory,
            context_factory=_code_recovery_context_factory,
        )
        _workflow_launcher.register_adapter(
            "durable_task",
            "v1",
            state_factory=_durable_task_state_factory,
            context_factory=_code_recovery_context_factory,
        )
        from deskpet.workflows.adapters.personal_runtime import (
            build_personal_runtime_adapter,
        )
        from deskpet.workflows.effects import EffectJournal as _PersonalEffectJournal

        _workflow_service.runtime_adapters.register(
            build_personal_runtime_adapter(
                journal=_PersonalEffectJournal(
                    _workflow_service.run_store.path
                ),
                tool_registry=deskpet_tool_registry_v2,
                run_start_snapshot_reader=(
                    _workflow_service.execution_uow.read_run_start_snapshot
                ),
                dispatch_fence_acquirer=service_context.get(
                    "run_execution_fence_acquirer"
                ),
            )
        )

        # New product workflow starts are owned exclusively by RunKernel.
        # The legacy tool handlers remain library-compatible but are not wired
        # as production launch ingress.
        _workflow_research_tools.set_deepresearch_workflow_starter(None)
        try:
            _configure_ppt_production(recover=False)
            logger.info("ppt_pro_services_wired")
        except Exception as exc:  # noqa: BLE001
            logger.warning("ppt_pro_services_wire_failed", error=str(exc))
        await _workflow_service.activate_runtime(
            required_runtime_identities=_workflow_service.runtime_adapters.identities()
        )
        startup_recoveries = await _workflow_service.runner.recover_expired()
        recovered_decisions = await _workflow_launcher.recover_open_decision_events()
        recovered_deliveries = await _workflow_launcher.recover_due_deliveries(
            recover_claimed=True
        )
        recovered_runs = await _workflow_launcher.recover_pending()
        _workflow_launcher.start_dispatcher()
        logger.info(
            "workflow_service_ready",
            db_path=str(_workflow_service.run_store.path),
            versions=_workflow_service.runner.registry.versions(),
            recovered_runs=len(recovered_runs),
            startup_recoveries=len(startup_recoveries),
            recovered_decisions=len(recovered_decisions),
            recovered_deliveries=len(recovered_deliveries),
        )
    except Exception as exc:  # noqa: BLE001 - old ReAct stays available
        service_context.register("workflow_service", None)
        service_context.register("run_execution_fence_acquirer", None)
        service_context.register("provider_invocation_coordinator", None)
        try:
            from deskpet.tools import research_tools as _workflow_research_tools

            _workflow_research_tools.set_deepresearch_workflow_starter(None)
        except Exception:
            pass
        logger.warning("workflow_service_init_failed", error=str(exc))
    _expire_ppt_outline_dangling_for_startup()
    # R-T1：goal_store 启动恢复（重启仍在的核心路径）。
    _gs_for_restore = service_context.get("session_goal_store")
    if _gs_for_restore is not None:
        try:
            _n = await _gs_for_restore.load_persisted()
            logger.info("goal_store_load_persisted restored=%d", _n)
        except Exception as _lp_exc:  # noqa: BLE001
            logger.warning("goal_store_load_persisted_failed: %s", _lp_exc)
    _mm = service_context.get("memory_manager")
    if _mm is not None:
        try:
            await _mm.initialize()
            logger.info("p4_memory_manager_ready")
        except Exception as exc:
            logger.warning("p4_memory_manager_init_failed", error=str(exc))
    # FP-4 WI-3.3 ★ FIX BUG: FactsStore.daily_decay() was never called in prod.
    # Apply once at startup (mirrors retriever's "run once at startup" convention).
    # Pinned facts are skipped (WI-3.3). Failure is non-fatal — only logs.
    _fs_for_decay = service_context.get("facts_store")
    if _fs_for_decay is not None:
        try:
            _decay_n = await _fs_for_decay.daily_decay()
            logger.info("p4_facts_daily_decay_startup", mutated=_decay_n)
        except Exception as _dd_exc:  # noqa: BLE001
            logger.warning("p4_facts_daily_decay_failed", error=str(_dd_exc))
    _legacy_sl = service_context.get("skill_loader")
    _managed_sl = service_context.get("managed_skill_discovery_projection")
    for _skill_surface, _skill_surface_name in (
        (_legacy_sl, "legacy_user"),
        (_managed_sl, "managed_first_party"),
    ):
        if _skill_surface is None:
            continue
        try:
            await _skill_surface.start()
            logger.info(
                "p4_skill_surface_ready",
                surface=_skill_surface_name,
                count=len(_skill_surface.list_skills()),
            )
        except Exception as exc:
            logger.warning(
                "p4_skill_surface_start_failed",
                surface=_skill_surface_name,
                error=str(exc),
            )
    # TC-5.1 修复 (2026-06-11)：SkillMatcher 缓存预热。module-level 的 sync
    # build() 对 async 生产 embedder 是静默 no-op → 这里 loader 启动后台跑
    # build_async（encode 内部自动 warmup embedder，不阻塞启动）。失败无害——
    # match_async 仍会逐 turn 惰性建缓存。
    _sm_for_prewarm = service_context.get("skill_matcher")
    _managed_skills_for_prewarm = service_context.get(
        "managed_skill_discovery_projection"
    )
    if _sm_for_prewarm is not None and _managed_skills_for_prewarm is not None:
        async def _skill_matcher_prewarm_bg() -> None:
            try:
                _n = await _sm_for_prewarm.build_async(
                    _managed_skills_for_prewarm.all()
                )
                logger.info("fp5_skill_matcher_prewarmed", cached=_n)
            except Exception as _pw_exc:  # noqa: BLE001
                logger.warning("fp5_skill_matcher_prewarm_failed", error=str(_pw_exc))

        # fire-and-forget; we deliberately don't await (same as embedder warmup)
        asyncio.create_task(_skill_matcher_prewarm_bg())
    # P4-S15: Embedder warmup runs in the background so cold-start isn't
    # blocked by 286 MB of BGE-M3 weights. Mock fallback returns instantly.
    _emb = service_context.get("embedder")
    if _emb is not None:
        async def _embedder_warmup_bg() -> None:
            try:
                await _emb.warmup()
                logger.info("p4_embedder_ready", is_mock=_emb.is_mock())
            except Exception as exc:
                logger.warning("p4_embedder_warmup_failed", error=str(exc))
        # fire-and-forget; we deliberately don't await
        asyncio.create_task(_embedder_warmup_bg())
    # Option A (2026-06-05): 首启模型下载。瘦包(DESKPET_BUNDLE_MODELS=0)不内嵌
    # 模型 → 后台 daemon 线程从 hf-mirror 拉缺失的 bge-m3 / faster-whisper。
    # 注册到 service_context 供 p4_ipc 的 model_provision_status 探针读取；
    # start_background 幂等。失败不阻塞启动（ASR/记忆各自有降级）。
    try:
        from deskpet.model_provisioner import ModelProvisioner
        _provisioner = ModelProvisioner()
        service_context.register("model_provisioner", _provisioner)
        _provisioner.start_background()
        logger.info("model_provisioner_started")
    except Exception as exc:  # noqa: BLE001
        logger.warning("model_provisioner_start_failed", error=str(exc))
    # Task 13 retires the JSON PreferenceMemory writer. The existing
    # preference_memory.json file is imported once by the authority cutover
    # reader and remains read-only upgrade residue.
    # P4-S15: VectorWorker — starts after SessionDB is initialised so the
    # vec0 schema is in place. After start, wire its enqueue() onto the
    # SessionDB write-hook so new chat turns auto-embed.
    _vw = service_context.get("vector_worker")
    _provider_workload_audit = service_context.get("provider_workload_audit")
    if _provider_workload_audit is not None:
        from deskpet.execution.provider_workload_audit import (
            provider_workload_audit_retention_loop,
        )
        _provider_audit_retention_task = asyncio.create_task(
            provider_workload_audit_retention_loop(_provider_workload_audit),
            name="provider-workload-audit-retention",
        )
        _provider_workload_maintenance_tasks.add(
            _provider_audit_retention_task
        )
        _provider_audit_retention_task.add_done_callback(
            _provider_workload_maintenance_tasks.discard
        )
    if _vw is not None and _sdb is not None:
        try:
            await _vw.start()
            # 记忆系统升级 WI-M1.2: _on_message_written 从「只触发
            # VectorWorker」升级为组合 fanout —— VectorWorker 永远跑，
            # facts 抽取按 config.memory.v2.facts_extract 决定。hook 仍是
            # 2 参 (mid, text)（不动既有调用点）；facts 需要的 role 由
            # SessionDB.get_message_role 按 mid 反查。facts 抽取走
            # asyncio.create_task → 不阻塞 append_message（shadow 模式）。
            async def _on_message_fanout(mid: int, text: str) -> None:
                _routing = await _sdb.get_message_routing_context(mid)
                if _routing is None:
                    logger.warning(
                        "memory_fanout_missing_message_routing_context",
                        message_id=mid,
                    )
                    return
                _role = _routing["role"]
                if _role not in {"user", "assistant"}:
                    logger.debug(
                        "memory_fanout_skipped_non_conversation_role",
                        message_id=mid,
                        role=_role,
                    )
                    return
                await _vw.enqueue(mid, text)
                if (
                    config.memory.v2.facts_extract
                    and _fact_extractor is not None
                ):
                    _root_run_id = str(
                        _routing.get("root_run_id") or ""
                    )
                    if not _root_run_id:
                        logger.info(
                            "facts_extract_skipped_unscoped_message",
                            message_id=mid,
                        )
                    else:
                        from deskpet.execution.provider_workloads import (
                            workload_context as _workload_context,
                        )
                        _facts_workload_context = _workload_context(
                            "memory.fact_extract",
                            request_id=f"message:{mid}",
                            session_id=str(_routing["session_id"]),
                            root_run_id=_root_run_id,
                            task_scope_id=_routing.get("task_scope_id"),
                        )

                    async def _extract_facts_bg(
                        _mid: int,
                        _text: str,
                        _workload=None,
                    ) -> None:
                        try:
                            await _fact_extractor.process_message(
                                message_id=_mid,
                                content=_text,
                                role=_role,
                                workload_context=_workload,
                            )
                        except Exception as _ex:  # noqa: BLE001
                            logger.warning(
                                "facts_extract_bg_failed mid=%s err=%s",
                                _mid, _ex,
                            )
                    if _root_run_id:
                        asyncio.create_task(
                            _extract_facts_bg(
                                mid, text, _facts_workload_context
                            )
                        )
                # WI-M1.5: chunking —— 长消息切块 + embed 进
                # messages_chunks。异步不阻塞 append_message（shadow）。
                if config.memory.v2.chunking and _message_chunker is not None:
                    async def _chunk_bg(_mid: int, _text: str) -> None:
                        try:
                            await _message_chunker.chunk_message(
                                message_id=_mid, content=_text,
                            )
                        except Exception as _ex:  # noqa: BLE001
                            logger.warning(
                                "chunk_bg_failed mid=%s err=%s", _mid, _ex,
                            )
                    asyncio.create_task(_chunk_bg(mid, text))

            _sdb._on_message_written = _on_message_fanout  # type: ignore[attr-defined]
            logger.info(
                "p4_vector_worker_ready",
                facts_extract=config.memory.v2.facts_extract,
            )
            # 2026-06-02 记忆审计 FATAL-A 修复：自动 backfill 安全网。
            # 在此之前 backfill_missing() 只在手动脚本（scripts/backfill_vectors）
            # 里调，lifespan 从不调用 —— 任何 embedding 缺口（worker 关机丢最后
            # 一批 / encode 失败 / 子进程崩）都会让 messages.embedding 永久 IS
            # NULL 且无声，向量召回永远漏掉这些消息（"刚说的话下次不记得"）。
            # 桌宠用户永远不会手动跑脚本。这里在 worker 起来后 fire-and-forget
            # 跑一次回填：backfill_missing 内部自带 embedder warmup + 分批 sleep
            # 礼让实时 enqueue（vector_worker.py:198-208），不阻塞冷启动。
            async def _vector_backfill_bg() -> None:
                try:
                    # 等 embedder 暖机 + 冷启动峰值过去再回填，别和首轮抢资源。
                    await asyncio.sleep(60.0)
                    _n = await _vw.backfill_missing()
                    if _n:
                        logger.info("p4_vector_backfill_done", processed=_n)
                except Exception as _bf_exc:  # noqa: BLE001
                    logger.warning(
                        "p4_vector_backfill_failed", error=str(_bf_exc)
                    )
            asyncio.create_task(_vector_backfill_bg())
        except Exception as exc:
            logger.warning("p4_vector_worker_start_failed", error=str(exc))
    # 记忆系统升级 WI-M1.7: reflection 低频定时任务。flag reflection 开 →
    # 注册一个后台 task，每 _REFLECTION_INTERVAL_H 小时调 ReflectionWorker
    # .run_once()（产物写进 facts 表 category=reflection）。flag 关 → 不注册
    # → 空闲零开销（R11）。无可用 LLM → run_once 内部跳过本次、不报错。
    if (
        config.memory.v2.reflection
        and _fact_extractor is not None
        and _facts_store is not None
        and _summarizer_state_db_path is not None
    ):
        _reflection_llm = _make_session_aware_llm_call(max_tokens=256)
        if _reflection_llm is None:
            logger.info("p4_reflection_skipped", reason="no_llm_provider")
        else:
            async def _reflection_loop() -> None:
                from deskpet.memory.reflection import ReflectionWorker
                _interval_s = 6.0 * 3600.0  # 每 6h 跑一次
                _worker = ReflectionWorker(
                    _summarizer_state_db_path,
                    _facts_store,
                    _reflection_llm,
                )
                # 启动后先等一会，避开冷启动高峰。
                await asyncio.sleep(180.0)
                while True:
                    try:
                        from deskpet.execution.provider_workloads import (
                            BackgroundModelPolicy,
                        )
                        _fid = await _worker.run_once(
                            workload_context=BackgroundModelPolicy.context(
                                "memory.reflection"
                            )
                        )
                        logger.info("reflection_run_once", fact_id=_fid)
                    except Exception as _rex:  # noqa: BLE001
                        logger.warning("reflection_run_failed err=%s", _rex)
                    await asyncio.sleep(_interval_s)

            _reflection_task = asyncio.create_task(
                _reflection_loop(),
                name="legacy-memory-reflection",
            )
            _legacy_reflection_tasks.add(_reflection_task)
            _reflection_task.add_done_callback(_legacy_reflection_tasks.discard)
            logger.info("p4_reflection_worker_scheduled")

    # WI-OH-4: 记忆 self-curation nudge — flag memory.v2.curation_nudge 开 +
    # facts store + LLM 可用 → 构造 MemoryCurator 存进 service_context，由
    # build_agent 接电到 agent_loop（每 N 回合 fire-and-forget nudge）。
    # flag 关 / 无 facts / 无 LLM → curator=None → agent_loop 不调 nudge（BC）。
    # 2026-06-23 修生产死链：旧代码 flag=False 时**整个 if 不进、零日志** → 真测时
    # 无法区分「flag 没开」vs「facts_store 没接上」vs「LLM 缺失」，曾误诊为接线 bug。
    # 改成显式三分支 skip-log（reason=flag_off / no_facts_store / no_llm_provider），
    # 任何「curator 没接上」都在启动日志留痕，杜绝静默死链复发。facts_store 用
    # module-global 优先、service_context 兜底（两者在 startup 均已就绪，见
    # p4_services_registered + memory_tools.bind 日志）。
    _cur_facts = _facts_store if _facts_store is not None else service_context.get("facts_store")
    if not config.memory.v2.curation_nudge:
        logger.info("oh4_curation_skipped", reason="flag_off")
    elif _cur_facts is None:
        logger.info("oh4_curation_skipped", reason="no_facts_store")
    else:
        _curation_llm = _make_session_aware_llm_call(max_tokens=512)
        if _curation_llm is None:
            logger.info("oh4_curation_skipped", reason="no_llm_provider")
        else:
            from deskpet.memory.curation import MemoryCurator as _MemoryCurator
            # WI-CC-5: 把 auto_learnings 真传进去，否则 curator 恒 allow_learnings
            # =False → learnings 提示词/类别永不启用（旧构造漏传，CC-5 暗装）。
            _curator = _MemoryCurator(
                _cur_facts,
                _curation_llm,
                allow_learnings=bool(config.memory.v2.auto_learnings),
            )
            service_context.register("memory_curator", _curator)
            logger.info(
                "oh4_curation_nudge_wired",
                every_n=config.memory.v2.curation_nudge_every_n_turns,
                auto_learnings=bool(config.memory.v2.auto_learnings),
            )
    # OpenSpec 2026-05-16-async-image-gen: start the ImageGenerationWorker
    # unless async disabled (then generate_image runs legacy sync).
    _iw = service_context.get("image_worker")
    _img_async = bool(
        ((config.raw.get("image") if hasattr(config, "raw") else None) or {})
        .get("async_enabled", True)
    )
    if _iw is not None and _img_async:
        try:
            await _iw.start()
            logger.info("image_worker_ready")
        except Exception as exc:  # noqa: BLE001
            logger.warning("image_worker_start_failed", error=str(exc))
    # P4-S15: MCPManager — bootstrap from raw [mcp] section. start() is
    # tolerant: missing section / disabled servers / spawn failures are all
    # logged but don't raise. Only the manager handle is registered; the
    # actual server states are inspectable via manager.server_state().
    try:
        # P4-S18: ensure workspace dir exists before spawning filesystem MCP
        # server. Without this, npx @modelcontextprotocol/server-filesystem
        # spawns OK but its first stat() fails with ENOENT, MCP transport
        # closes, and our manager spins in reconnect loop forever (logged
        # every few seconds, polluting startup output). Touching the dir
        # is idempotent and cheap; agents are still scoped to it.
        try:
            _ws_dir = _paths.user_data_dir() / "workspace"
            _ws_dir.mkdir(parents=True, exist_ok=True)
        except Exception as _ws_exc:  # pragma: no cover — best-effort
            logger.warning("workspace_mkdir_failed", error=str(_ws_exc))

        from deskpet.mcp.bootstrap import create_and_start_from_config as _mcp_bootstrap
        # P6 bugfix 2026-05-13: MCP manager 用的是 deskpet.tools.registry.ToolRegistry
        # 的 keyword API (register(name=..., toolset=..., schema=..., handler=...))，
        # 不是老的 tools.registry.ToolRegistry (register(tool) 单参)。传错 registry
        # 会导致每个 MCP 工具都 TypeError("unexpected keyword 'name'") 注册失败。
        _mcp_app_config = config.raw
        if (
            _context_os_e2e_boot_hooks is not None
            and os.environ.get(
                "DESKPET_CONTEXT_OS_E2E_FIXTURE_CATALOG", "1"
            ).strip() != "0"
        ):
            import copy as _copy_e2e_mcp

            _mcp_app_config = _copy_e2e_mcp.deepcopy(config.raw)
            _mcp_app_config["mcp"] = {
                "enabled": True,
                "servers": [
                    {
                        "name": "context-os-e2e",
                        "enabled": True,
                        "transport": "stdio",
                        "command": sys.executable,
                        "args": [
                            str(
                                Path(__file__).resolve().parents[1]
                                / "scripts"
                                / "e2e"
                                / "context_os_mcp_fixture.py"
                            )
                        ],
                        "env": {
                            "DESKPET_DEV_MODE": "1",
                            "DESKPET_CONTEXT_OS_E2E_DAEMON_URL": (
                                _context_os_e2e_boot_hooks.daemon_url
                            ),
                        },
                    }
                ],
            }
        _mcp_manager = await _mcp_bootstrap(
            app_config=_mcp_app_config,
            tool_registry=deskpet_tool_registry_v2,
        )
        deskpet_tool_registry_v2.set_mcp_catalog_stale_callback(
            _mcp_manager.handle_catalog_stale
        )
        service_context.register("mcp_manager", _mcp_manager)
        logger.info("p4_mcp_manager_ready", states=_mcp_manager.server_state())
    except Exception as exc:
        logger.warning("p4_mcp_manager_bootstrap_failed", error=str(exc))

    # P4-S20-D: 启动时把"老对话总结"任务 fire-and-forget。不阻塞 startup
    # — 后台慢慢跑。第一次启动 4881 条历史时只处理 max_per_run 个 session
    # 防止打爆 LLM。
    if _summarizer_state_db_path is not None and local_llm is not None:
        async def _bg_summarize() -> None:
            try:
                from deskpet.memory.summarizer import (
                    summarize_old_sessions, make_llm_call,
                )
                # 多等 8s 让其它启动任务先就位（embedder warmup、MCP 启动等）
                await asyncio.sleep(8.0)
                logger.info("summarizer_starting age_days=30 min_messages=20 max_per_run=10")
                result = await summarize_old_sessions(
                    db_path=_summarizer_state_db_path,
                    llm_call=make_llm_call(local_llm),
                    vector_worker=service_context.get("vector_worker"),
                    # Stage 2 / WI-S2.4 — episodic→semantic 固化
                    fact_extractor=_fact_extractor,
                    episodic_to_semantic=bool(
                        config.memory.v2.episodic_to_semantic
                    ),
                    background_tasks=_episodic_background_tasks,
                )
                logger.info(
                    "summarizer_done scanned=%d summarized=%d archived=%d errors=%d",
                    result.sessions_scanned,
                    result.sessions_summarized,
                    result.messages_archived,
                    len(result.errors),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "summarizer_bg_failed: %s",
                    exc,
                )
        asyncio.create_task(_bg_summarize())

    # P5-S1/S2: supervisor watchdog + LLM agent. Starts after the rest of
    # startup is done so the 30s grace can run while normal startup races
    # finish. Disabled when [supervisor].enabled = false; in that case we
    # skip construction entirely so the asyncio.Task isn't even created.
    try:
        _sup_cfg = (config.raw.get("supervisor") if hasattr(config, "raw") else None) or {}
        if bool(_sup_cfg.get("enabled", False)):
            from agent.watchdog import WatchdogLoop as _WatchdogLoop
            from agent.supervisor import (
                SupervisorAgent as _SupAgent,
                build_supervisor_hook as _build_sup_hook,
            )
            from agent.snapshot import build_snapshot as _build_snap_func

            # P5-S2 Phase 6: wire per-(sid, tool) circuit breaker into
            # the v2 tool registry. The registry checks ``can_call``
            # before every dispatch and ``record_call`` after, so the
            # breaker doesn't need to be reachable from chat handlers
            # directly. Knobs come from [supervisor] so they live next
            # to the rest of the self-healing config.
            try:
                from agent.circuit_breaker import ToolCircuitBreaker as _ToolBreaker
                if deskpet_tool_registry_v2 is not None:
                    _breaker = _ToolBreaker(
                        threshold=int(_sup_cfg.get("circuit_breaker_threshold", 3)),
                        cooldown_seconds=float(
                            _sup_cfg.get("circuit_breaker_cooldown_seconds", 60)
                        ),
                    )
                    deskpet_tool_registry_v2.set_circuit_breaker(_breaker)
                    service_context.register("tool_circuit_breaker", _breaker)
                    logger.info(
                        "p5s2_circuit_breaker_wired threshold=%d cooldown=%.0fs",
                        int(_sup_cfg.get("circuit_breaker_threshold", 3)),
                        float(_sup_cfg.get("circuit_breaker_cooldown_seconds", 60)),
                    )
            except Exception as _exc:  # noqa: BLE001
                logger.warning("p5s2_circuit_breaker_wire_failed err=%s", _exc)

            # Snapshot builder closure — pulls services lazily so each
            # tick reads fresh state.
            async def _snap_builder(sid: str):
                _ctx_window = int(
                    (config.raw.get("agent") or {}).get("context_window_tokens", 200_000)
                )
                return await _build_snap_func(
                    sid,
                    session_activity=service_context.get("session_activity"),
                    session_db=service_context.get("session_db"),
                    context_window_tokens=_ctx_window,
                )

            # Audit closure — persists every non-wait SupervisorAction
            async def _audit_action(action, sid):
                _sdb_for_audit = service_context.get("session_db")
                if _sdb_for_audit is None:
                    return
                hint_text = action.hint_for_main_agent or action.user_message or action.diagnosis
                await _sdb_for_audit.append_supervisor_hint(
                    session_id=sid,
                    alert_id=action.alert_id,
                    hint_text=hint_text,
                    action="cancel_coerced" if action.raw_action == "cancel" else action.action,
                    severity=action.severity,
                    diagnosis=action.diagnosis,
                )

            # Nudge push closure — wraps SupervisorAction → Hint and
            # forwards to the queue.
            async def _push_hint(sid: str, action):
                from agent.nudge_queue import Hint as _Hint
                _nq = service_context.get("nudge_queue")
                if _nq is None:
                    return
                await _nq.push(
                    sid,
                    _Hint(
                        text=action.hint_for_main_agent or action.user_message or "",
                        alert_id=action.alert_id,
                        severity=action.severity,
                    ),
                )

            # Broadcast closure — sends supervisor_alert to all control WS.
            async def _broadcast_supervisor_alert(typ: str, payload: dict):
                # Use the same multi-WS fan-out the todo broadcaster uses.
                # _control_connections is the canonical mapping (sid → ws).
                if not _control_connections:
                    return
                msg = {"type": typ, "payload": payload}
                for _sid_key, _ws_obj in list(_control_connections.items()):
                    try:
                        await _ws_obj.send_json(msg)
                    except Exception as _bex:
                        logger.debug("supervisor_alert_broadcast_failed sid=%s error=%s", _sid_key, _bex)

            # Build agent. Provider resolution (multi-provider-management):
            # 1) `[supervisor].provider_id = "<id>"` in config.toml — explicit pin
            # 2) Else first enabled provider from LLMProviderRegistry.get_chain()
            # 3) Else legacy `local_llm` (single-provider mode, pre-multi-provider)
            #
            # Previously hardcoded `local_llm` which on a multi-provider deploy
            # without Ollama running produced "supervisor_unavailable: ConnectError"
            # — confusing because the main chat works fine via the relay. Now
            # supervisor follows the user's chain by default.
            _sup_provider = None
            try:
                _reg_for_sup = service_context.get("provider_registry")
                if _reg_for_sup is not None:
                    _sup_pinned_id = (_sup_cfg.get("provider_id") or "").strip()
                    _sup_entry = None
                    if _sup_pinned_id:
                        _sup_entry = _reg_for_sup.get_entry(_sup_pinned_id)
                        if _sup_entry is None or not getattr(_sup_entry, "enabled", True):
                            logger.warning(
                                "supervisor_pinned_provider_missing pid=%s — falling back to chain",
                                _sup_pinned_id,
                            )
                            _sup_entry = None
                    if _sup_entry is None:
                        _chain = []
                        try:
                            _chain = _reg_for_sup.get_chain()
                        except Exception:
                            _chain = []
                        if _chain:
                            _sup_entry = _reg_for_sup.get_entry(_chain[0]["id"])
                    if _sup_entry is not None:
                        _sup_api_key = (
                            _reg_for_sup.resolve_api_key(_sup_entry.id) or "ollama"
                        )
                        # Optional [supervisor].model override (defaults to entry's default_model)
                        _sup_model_override = (_sup_cfg.get("model") or "").strip()
                        _sup_model = _sup_model_override or _sup_entry.model
                        _sup_provider = OpenAICompatibleProvider(
                            base_url=_sup_entry.base_url,
                            api_key=_sup_api_key,
                            model=_sup_model,
                            temperature=0.1,
                            sanitize_inline_cot_dsml=_sanitize_cot_dsml,
                        )
                        logger.info(
                            "supervisor_provider_resolved id=%s base_url=%s model=%s",
                            _sup_entry.id, _sup_entry.base_url, _sup_model,
                        )
            except Exception as _exc:  # noqa: BLE001
                logger.warning(
                    "supervisor_provider_resolve_failed err=%s — falling back to local_llm",
                    str(_exc)[:200],
                )
            if _sup_provider is None:
                # Last-resort fallback: legacy single-provider mode.
                _sup_provider = local_llm
                logger.info(
                    "supervisor_provider_resolved id=legacy base_url=%s — "
                    "registry empty/unavailable; consider adding a provider via Settings",
                    getattr(local_llm, "base_url", "?"),
                )
            # P6 bugfix 2026-05-14 (live-test): auto-mode bypass for
            # supervisor. When permission_gate.auto_mode is ON, the user
            # has delegated "decide for me" semantics — supervisor must
            # NOT block on UI buttons (ask_user). Below callbacks let
            # SupervisorAgent self-drive: check auto state + spawn a
            # follow-up chat task with "<<supervisor_followup>>" trigger.
            def _supervisor_auto_mode_check() -> bool:
                try:
                    return bool(
                        permission_gate_v2 is not None
                        and getattr(permission_gate_v2, "auto_mode", False)
                    )
                except Exception:
                    return False

            async def _supervisor_auto_followup(sid: str, trigger_text: str) -> None:
                # Spawn a chat task with the synthetic trigger text. We
                # look up the most recent control WS for this sid and
                # use the registered re-dispatcher closure (set by chat
                # handler each user turn through the durable Harness ingress).
                # If no dispatcher is registered yet, we silently no-op
                # — the queued hint will be consumed when the user (or
                # auto_resume) next pokes the agent.
                _target_ws = _control_connections.get(sid)
                if _target_ws is None:
                    # Fall back to ANY control WS — supervisor alerts are
                    # already broadcast to all of them.
                    for _k, _w in _control_connections.items():
                        _target_ws = _w
                        break
                if _target_ws is None:
                    logger.info(
                        "supervisor_auto_followup_noop sid=%s reason=no_ws",
                        sid,
                    )
                    return
                try:
                    _launch_product_harness_chat(_target_ws, trigger_text, sid)
                except Exception as _ex:
                    logger.warning(
                        "supervisor_auto_followup_dispatch_failed sid=%s err=%s",
                        sid, _ex,
                    )

            _supervisor_agent = _SupAgent(
                provider=_sup_provider,
                snapshot_builder=_snap_builder,
                nudge_queue_push=_push_hint,
                broadcast=_broadcast_supervisor_alert,
                audit=_audit_action,
                auto_mode_check=_supervisor_auto_mode_check,
                auto_followup=_supervisor_auto_followup,
                # P5-S2 G2 (2026-05-12): 30s→120s. supervisor was timing out
                # on deepseek-v4-pro thinking-mode calls — the model takes
                # 30-60s just thinking before emitting the 300-token JSON
                # spec. 120s covers reasoning + transit + the relay 15s SSE
                # keep-alive. config knob name unchanged for backward compat.
                timeout_seconds=float(_sup_cfg.get("llm_timeout_seconds", 120.0)),
            )
            service_context.register("supervisor", _supervisor_agent)

            # P5-S2 Hook B probe: same logic as the agent_loop probe but
            # captured at a different scope (lifespan vs per-chat). Maps
            # session id → SessionDB.get_session_todos → filter incomplete.
            # Returned to the watchdog so the (c) trigger
            # rule (idle with todos) can fire even when there's no
            # active chat task.
            # P6 bugfix 2026-05-14 (用户反馈): 长时间未操作时不要骚扰用户。
            # 即使 todos 未完成、agent 状态 idle，也判断用户是否还在用 deskpet
            # （以最近 user 消息时间为准）。30 分钟无 user 活动 → 视为用户
            # 离开，不再主动弹 "Agent seems stuck" 提醒。
            _user_idle_grace_s = float(
                _sup_cfg.get("user_idle_grace_seconds", 1800)
            )

            async def _watchdog_incomplete_todos_probe(_base_sid: str) -> list[dict]:
                try:
                    sdb_local = service_context.get("session_db")
                    if sdb_local is None:
                        return []
                    # P6 bugfix 2026-05-14: check user activity first.
                    # 如果用户 > N 分钟没发消息 → 视为离开，不报警。
                    # 即使 agent 自己拆了 todos，用户没主动让它做 →
                    # 我们也不该自动催 agent 干。
                    try:
                        import time as _t
                        last_user_ts = await sdb_local.last_message_ts(
                            session_id=_base_sid, role="user"
                        ) if hasattr(sdb_local, "last_message_ts") else None
                        if last_user_ts is None:
                            # Fallback: scan messages directly
                            _recent = await sdb_local.get_messages(_base_sid, limit=50)
                            _user_ts = [
                                float(r.get("created_at") or 0)
                                for r in _recent
                                if (r.get("role") or "") == "user"
                            ]
                            last_user_ts = max(_user_ts) if _user_ts else 0.0
                        if last_user_ts <= 0:
                            # No user message ever for this sid → never auto-poke
                            logger.debug(
                                "p6_watchdog_skip_no_user_history sid=%s", _base_sid,
                            )
                            return []
                        user_idle_age = _t.time() - last_user_ts
                        if user_idle_age > _user_idle_grace_s:
                            logger.info(
                                "p6_watchdog_skip_user_away sid=%s user_idle_age=%.0fs grace=%.0fs",
                                _base_sid, user_idle_age, _user_idle_grace_s,
                            )
                            return []
                    except Exception as _ue:  # noqa: BLE001
                        # 探测失败保守起见仍按"用户活跃"处理（行为退化到 pre-fix）
                        logger.debug(
                            "p6_watchdog_user_activity_probe_failed sid=%s err=%s",
                            _base_sid, str(_ue)[:200],
                        )
                    rows = await sdb_local.get_session_todos(_base_sid)
                    return [
                        r for r in rows
                        if (r.get("status") or "").lower() not in ("completed", "cancelled")
                    ]
                except Exception as _e:  # noqa: BLE001
                    logger.warning(
                        "p5s2_watchdog_probe_lookup_failed sid=%s err=%s",
                        _base_sid, str(_e)[:200],
                    )
                    return []

            _watchdog = _WatchdogLoop(
                session_activity=service_context.get("session_activity"),
                hook=_build_sup_hook(_supervisor_agent),
                scan_interval_seconds=float(_sup_cfg.get("scan_interval_seconds", 60)),
                stuck_threshold_seconds=float(_sup_cfg.get("stuck_threshold_seconds", 900)),
                dedup_seconds=float(_sup_cfg.get("dedup_seconds", 720)),
                startup_grace_seconds=float(_sup_cfg.get("startup_grace_seconds", 30)),
                idle_with_todos_threshold_seconds=float(
                    _sup_cfg.get("idle_with_todos_threshold_seconds", 60)
                ),
                incomplete_todos_probe=_watchdog_incomplete_todos_probe,
                # P5-S2 Phase 6 rule (d): proactive death-loop trigger.
                tool_signature_repeat_threshold=int(
                    _sup_cfg.get("tool_signature_repeat_threshold", 3)
                ),
            )
            _watchdog.start()
            service_context.register("watchdog", _watchdog)
            logger.info("p5_supervisor_watchdog_started")

            # P5-S2 Phase 4: AutoResumeOrchestrator — closes the
            # supervisor → main-agent loop. When the chat handler hits
            # max_iterations / circuit_open / permanent_tool_error /
            # hallucination, it forwards to ``orchestrator.handle_failure``;
            # the orchestrator asks supervisor for a hint and (if action
            # is ``nudge``) automatically spawns a fresh chat task on the
            # same sid via the per-sid re-dispatcher closure populated
            # by the chat handler itself.
            try:
                from agent.auto_resume import AutoResumeOrchestrator as _AROrch

                # Dispatcher closure: orchestrator passes (sid, msgs)
                # where ``msgs`` ends in a system msg with the supervisor
                # hint. Production trampoline:
                #   1. Extract hint text from the injected system msg.
                #   2. Push it to nudge_queue (pop_all picks it up at the
                #      top of the next chat task — uniform with P5-S1
                #      injection path).
                #   3. Call the per-sid re-dispatcher (registered by chat
                #      handler each user turn) with the synthetic trigger
                #      ``<<auto_resume>>`` so a fresh AgentLoop runs.
                async def _resume_through_harness(_sid: str, _msgs: list[dict]) -> None:
                    await _enqueue_auto_resume_hint(_sid, _msgs)
                    _ws_for_sid = _control_connections.get(_sid) or _control_connections.get("default")
                    if _ws_for_sid is None:
                        logger.warning("auto_resume_no_transport sid=%s", _sid)
                        return
                    try:
                        _launch_product_harness_chat(_ws_for_sid, "<<auto_resume>>", _sid)
                    except Exception as _ex:  # noqa: BLE001
                        logger.warning("auto_resume_redispatch_failed sid=%s err=%s", _sid, _ex)

                # ws emitter — broadcast auto_resume_* events to all control conns.
                async def _auto_resume_emit(_typ: str, _payload: dict) -> None:
                    if not _control_connections:
                        return
                    _msg = {"type": _typ, "payload": _payload}
                    for _sid_key, _ws_obj in list(_control_connections.items()):
                        try:
                            await _ws_obj.send_json(_msg)
                        except Exception as _bex:
                            logger.debug("auto_resume_emit_failed sid=%s err=%s", _sid_key, _bex)

                # Audit writer — bridge to SessionDB.append_supervisor_hint.
                async def _auto_resume_audit(_record: dict) -> None:
                    _sdb = service_context.get("session_db")
                    if _sdb is None:
                        return
                    try:
                        await _sdb.append_supervisor_hint(
                            session_id=_record.get("session_id", ""),
                            alert_id=_record.get("alert_id", ""),
                            hint_text=_record.get("hint_text", ""),
                            action=_record.get("action", "auto_resumed"),
                            severity=_record.get("severity", "yellow"),
                            diagnosis=_record.get("diagnosis", ""),
                        )
                    except Exception as _ex:  # noqa: BLE001
                        logger.debug("auto_resume_audit_failed err=%s", _ex)

                # WI-1.5：resume 注入原 goal_text（窄版）。从 service_context
                # 取活跃 goal store（goal_mode OFF → None → getter 返 None → BC）。
                def _goal_text_getter_for_resume(_sid: str):
                    _gs = service_context.get("session_goal_store")
                    try:
                        return _gs.get_goal_text(_sid) if _gs is not None else None
                    except Exception:  # noqa: BLE001 — safe-fail, 不阻 resume
                        return None

                _orch = _AROrch(
                    supervisor=_supervisor_agent,
                    chat_dispatcher=_resume_through_harness,
                    activity_store=service_context.get("session_activity"),
                    max_attempts=int(_sup_cfg.get("max_auto_resume_attempts", 2)),
                    enabled=bool(_sup_cfg.get("auto_resume_enabled", True)),
                    ws_emitter=_auto_resume_emit,
                    audit_writer=_auto_resume_audit,
                    goal_text_getter=_goal_text_getter_for_resume,
                )
                service_context.register("auto_resume", _orch)
                logger.info(
                    "p5s2_auto_resume_started enabled=%s max_attempts=%d",
                    _orch._enabled, _orch.max_attempts,
                )
            except Exception as _exc:  # noqa: BLE001
                logger.warning("p5s2_auto_resume_start_failed err=%s", _exc)
        else:
            logger.info("p5_supervisor_disabled_via_config")
    except Exception as exc:  # noqa: BLE001
        logger.warning("p5_supervisor_watchdog_start_failed", error=str(exc))

    # Phase 1.1.5 — 启动落一行 model_context_resolved，让用户/日志一眼
    # 看到当前默认模型解析出的有效窗口 + 来源链。每次 chat 会话另会按
    # session 的实际 model + project_root 再 resolve（resolve() 自带日志）。
    try:
        from llm.model_info import resolve as _resolve_mi

        _startup_v2 = bool(
            ((config.raw.get("context") or {}).get("manager") or {})
            .get("v2_enabled", True)
        )
        if _startup_v2:
            # resolve() 自身落 model_context_resolved INFO 日志。
            _resolve_mi(config.llm.local.model, project_root=None)
        else:
            logger.info(
                "model_context_resolved model=%s window=legacy source=v1_rollback",
                config.llm.local.model,
            )
    except Exception as _mi_exc:  # noqa: BLE001
        logger.warning("model_context_startup_resolve_failed err=%s", _mi_exc)

    # Task 13 startup order is fail-closed: Platform foundation exists before
    # authority composition, while user catalog and Companion ingress remain
    # closed until Harness registers the full core catalog and the durable
    # cutover reconciler has proved every receipt.
    await _initialize_capability_runtime()
    await _initialize_growth_authority()
    await _activate_product_harness()
    await _complete_growth_authority_cutover()
    await _initialize_companion_projection_services()
    await _activate_companion_runtime_adapter_and_open_ingress()
    _initialize_companion_action_decision_service()
    logger.info("startup complete")
    yield
    from deskpet.retrieval.runtime import shutdown_default_gateway
    await shutdown_default_gateway()
    global _harness_accepting, _harness_runtime, _harness_venue
    _harness_accepting = False
    _pending_ingress = tuple(_companion_ingress_tasks)
    for _task in _pending_ingress:
        _task.cancel()
    if _pending_ingress:
        await asyncio.gather(*_pending_ingress, return_exceptions=True)
    _companion_ingress_tasks.clear()
    _companion_runtime_service = service_context.get("companion_runtime")
    if _companion_runtime_service is not None:
        try:
            await _companion_runtime_service.close(timeout=5.0)
            _runtime_diagnostics = _companion_runtime_service.diagnostics()
            if (
                _runtime_diagnostics["scheduler_count"] != 0
                or _runtime_diagnostics["child_count"] != 0
            ):
                raise RuntimeError("companion_runtime_shutdown_incomplete")
            logger.info(
                "companion_runtime_stopped",
                scheduler_count=0,
                child_count=0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("companion_runtime_shutdown_failed", error=str(exc))
    _legacy_reflection_pending = tuple(_legacy_reflection_tasks)
    for _task in _legacy_reflection_pending:
        _task.cancel()
    if _legacy_reflection_pending:
        await asyncio.gather(
            *_legacy_reflection_pending,
            return_exceptions=True,
        )
    _provider_maintenance_pending = tuple(
        _provider_workload_maintenance_tasks
    )
    for _task in _provider_maintenance_pending:
        _task.cancel()
    if _provider_maintenance_pending:
        await asyncio.gather(
            *_provider_maintenance_pending,
            return_exceptions=True,
        )
    _provider_workload_maintenance_tasks.clear()
    if _harness_runtime is not None:
        try:
            await _harness_runtime.close(timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("product_harness_shutdown_failed", error=str(exc))
        finally:
            _harness_runtime = None
            _harness_venue = None
    _capability_platform = service_context.get("capability_platform")
    _capability_center = service_context.get("capability_center")
    if _capability_center is not None:
        try:
            await _capability_center.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning("capability_center_shutdown_failed", error=str(exc))
        finally:
            service_context.register("capability_center", None)
    if _capability_platform is not None:
        try:
            cleanup_reports = await _capability_platform.shutdown()
            logger.info(
                "capability_platform_stopped",
                cleanup_reports=len(cleanup_reports),
                remaining_pids=sum(
                    len(report.remaining_pids) for report in cleanup_reports
                ),
                released_private_memory_bytes=sum(
                    report.released_private_memory_bytes
                    for report in cleanup_reports
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("capability_platform_shutdown_failed", error=str(exc))
        finally:
            service_context.register("capability_platform", None)
    _workflow_service = service_context.get("workflow_service")
    _workflow_launcher = getattr(_workflow_service, "launcher", None)
    if _workflow_launcher is not None:
        try:
            await _workflow_launcher.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning("workflow_launcher_shutdown_failed", error=str(exc))
    _execution_uow = getattr(_workflow_service, "execution_uow", None)
    if _execution_uow is not None:
        try:
            await asyncio.wait_for(_execution_uow.close(), timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("execution_uow_shutdown_failed", error=str(exc))
    # P5-S1: stop the watchdog cleanly so its task doesn't dangle past
    # shutdown and produce "Task was destroyed but it is pending!" noise.
    _wd = service_context.get("watchdog")
    if _wd is not None:
        try:
            await _wd.stop()
        except Exception as exc:
            logger.warning("p5_supervisor_watchdog_stop_failed", error=str(exc))
    # P4-S15: stop in reverse-dependency order — MCP servers first (so they
    # don't keep firing tool_invoke writes), then VectorWorker (drain
    # outstanding embeds), then SkillLoader's watchdog thread.
    _mcp = service_context.get("mcp_manager")
    if _mcp is not None:
        try:
            await _mcp.stop()
        except Exception as exc:
            logger.warning("p4_mcp_manager_stop_failed", error=str(exc))
    _vw = service_context.get("vector_worker")
    if _vw is not None:
        try:
            await _vw.stop()
        except Exception as exc:
            logger.warning("p4_vector_worker_stop_failed", error=str(exc))
    _iw = service_context.get("image_worker")
    if _iw is not None:
        try:
            await _iw.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("image_worker_stop_failed", error=str(exc))
    for _skill_surface in (
        service_context.get("managed_skill_discovery_projection"),
        service_context.get("skill_loader"),
    ):
        if _skill_surface is not None:
            try:
                await _skill_surface.stop()
            except Exception as exc:
                logger.warning("p4_skill_surface_stop_failed", error=str(exc))

    # Stage 2 / WI-S2.4 (D12 v2)：等所有 episodic fact 抽取 task 完成。
    # 防 "Task was destroyed but it is pending" warning + 数据丢失。
    pending = list(_episodic_background_tasks)
    if pending:
        logger.info(
            "episodic_background_tasks_draining count=%d", len(pending),
        )
        try:
            await asyncio.gather(*pending, return_exceptions=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "episodic_background_tasks_drain_failed: %s", exc,
            )

    logger.info("shutting down")


app = FastAPI(title="Desktop Pet Backend", version="0.2.0", lifespan=lifespan)

# CORS: Tauri WebView2 runs on tauri://localhost (or https://tauri.localhost).
# fetch() to http://127.0.0.1:8100 is cross-origin and blocked without this.
# WebSocket connections are NOT subject to CORS, only HTTP (POST /config/cloud).
from fastapi.middleware.cors import CORSMiddleware
# v2 fix: regex 模式支持任意 vite dev port (worktree-aware 隔离场景).
# 默认 5173；v2 worktree 用 5473；其他 worktree 用 5273/5373/5573 等.
# allow_origin_regex 覆盖所有 localhost / 127.0.0.1 + 任意端口 + Tauri.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(tauri://localhost|https://tauri\.localhost|http://(localhost|127\.0\.0\.1):\d+)$",
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "X-Shared-Secret"],
)

# Track control channel connections for lip-sync forwarding
_control_connections: dict[str, WebSocket] = {}
# One independently cancellable Inspector query per connection/kind/root.
_harness_inspector_tasks: dict[tuple[int, str, str], asyncio.Task] = {}
# Transport websocket sid -> effective chat sid shown by that peer.
_chat_peer_groups: dict[str, str] = {
    "default": "default",
    "message-panel-main": "default",
}


def _cancel_harness_inspector_tasks(
    ws: WebSocket,
    *,
    root_run_id: str | None = None,
    request_kind: str | None = None,
) -> int:
    cancelled = 0
    connection_id = id(ws)
    for key, task in tuple(_harness_inspector_tasks.items()):
        owner, kind, root = key
        if owner != connection_id:
            continue
        if root_run_id is not None and root != root_run_id:
            continue
        if request_kind is not None and kind != request_kind:
            continue
        if not task.done():
            task.cancel()
            cancelled += 1
        _harness_inspector_tasks.pop(key, None)
    return cancelled


def _schedule_harness_inspector_task(
    ws: WebSocket,
    *,
    request_kind: str,
    root_run_id: str,
    coroutine: Any,
) -> None:
    key = (id(ws), request_kind, root_run_id)
    previous = _harness_inspector_tasks.pop(key, None)
    if previous is not None and not previous.done():
        previous.cancel()
    task = asyncio.create_task(
        coroutine,
        name=f"harness-inspector:{request_kind}:{root_run_id}",
    )
    _harness_inspector_tasks[key] = task

    def _done(completed: asyncio.Task) -> None:
        if _harness_inspector_tasks.get(key) is completed:
            _harness_inspector_tasks.pop(key, None)

    task.add_done_callback(_done)


async def _broadcast_control(msg: dict) -> None:
    """Best-effort fan-out to every control websocket."""
    for _sid, _ws in list(_control_connections.items()):
        try:
            await _ws.send_json(msg)
        except Exception as exc:  # noqa: BLE001
            logger.debug("control_broadcast_failed sid=%s err=%s", _sid, exc)


async def _ppt_workflow_outline_notify(payload: dict) -> None:
    """Project a durable PPT decision into the existing session card UI."""

    sid = str(payload.get("session_id") or "default")
    oid = str(payload.get("outline_id") or "")
    topic = str(payload.get("topic") or "")
    outline_md = str(payload.get("outline_md") or "")
    card = {
        "outline_id": oid,
        "topic": topic,
        "outline_md": outline_md,
        "session_id": sid,
        "sources_count": int(payload.get("sources_count") or 0),
        "no_research": bool(payload.get("no_research")),
        "history": list_history(sid, 20),
    }
    await _broadcast_control({"type": "ppt_outline_proposed", "payload": card})

    session_db = service_context.get("session_db")
    if session_db is not None and oid:
        text = f"PPT 大纲确认 · {topic}\n\n{outline_md}".strip()
        try:
            await session_db.append_message(
                session_id=sid,
                role="assistant",
                content=text,
                workflow_event_id=f"ppt-outline:{oid}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ppt_workflow_outline_persist_failed sid=%s err=%s", sid, exc)


async def _resume_workflow_ppt_outline(service, decision, response: dict, sid: str) -> None:
    try:
        await service.resolve_decision(
            decision.decision_id,
            nonce=decision.nonce,
            response=response,
            expected_version=decision.version,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ppt_workflow_outline_resume_failed run_id=%s error=%s",
            decision.run_id,
            exc,
        )
        await _ppt_notify_chat_bubble(sid, f"PPT 大纲确认失败：{exc}")


async def _resolve_workflow_ppt_outline(oid: str, response: dict) -> str | None:
    from deskpet.workflows.adapters.ppt_runtime import (
        open_ppt_outline_decision, workflow_outline_run_id, workflow_run_session,
    )

    run_id = workflow_outline_run_id(oid)
    service = service_context.get("workflow_service")
    if service is None or run_id is None:
        return None
    decision = await open_ppt_outline_decision(service, run_id, oid)
    if decision is None:
        return None
    sid = await workflow_run_session(service, run_id)
    await _resume_workflow_ppt_outline(service, decision, response, sid)
    return sid


# ─── WI-1B-4 摘要质量回路 — 纯检测/构造 helper（main.py 闭包 + 单测共用）──────
import re as _re_sql_module

_SUMMARY_CONFUSED_RE = _re_sql_module.compile(r"刚才|之前说的|你忘了|我们在弄|上一个")
_TASK_STATE_SNAPSHOT_MARKER = "[任务态快照"


def _sql_user_is_confused(text: str) -> bool:
    """用户消息是否用"困惑措辞"问起被压掉的上下文（词法匹配）。"""
    return bool(_SUMMARY_CONFUSED_RE.search(text or ""))


def _sql_latest_task_snapshot(entries: "list[dict]") -> "str | None":
    """从 L1 list_entries 结果里取最近一条 pre-flush 任务态快照（无则 None）。

    任务态快照由 agent_loop wi4b_preflush 在压缩触发时写入 MEMORY.md，
    形如 ``[任务态快照/task-state] 目标: ...; 最近请求: ...``。它的存在
    即"本 session 发生过压缩"的标志（不依赖 ctx_observability flag）。
    """
    snaps = [
        str(e.get("text", ""))
        for e in (entries or [])
        if _TASK_STATE_SNAPSHOT_MARKER in str(e.get("text", ""))
    ]
    return snaps[-1] if snaps else None


def _sql_build_reinject_msg(snapshot: str) -> dict:
    """据任务态快照构造一条回灌 system 消息。"""
    return {
        "role": "system",
        "content": (
            "[任务态回灌/summary-quality-loop] 用户提起了之前的上下文，但相关"
            "历史已被压缩。以下是压缩前保存的任务态快照，请据此续接，不要重新"
            "自我介绍或反问用户想做什么：\n" + snapshot
        ),
        "_is_summary_reinject": True,
    }


async def _replay_open_harness_decisions(
    ws: WebSocket,
    *,
    execution_uow: Any,
    session_id: str,
) -> None:
    """Rebuild blocking UI cards from durable open decisions after reconnect."""

    if not hasattr(execution_uow, "list_open_decision_projections"):
        return
    from agent.agent_loop import PipelineEvent
    from deskpet.agent.run_presenter import CanonicalRunEventPresentationAdapter
    from deskpet.execution.contracts import (
        OutcomeStatus,
        RunEvent,
        RunEventCandidate,
        thaw_json,
    )

    adapter = CanonicalRunEventPresentationAdapter()
    for projection in await execution_uow.list_open_decision_projections(
        session_id
    ):
        decision = projection["decision"]
        request = decision.request
        prompt = thaw_json(request.prompt)
        restored_parent = ""
        restored_root = str(projection.get("workspace_root") or "").strip()
        restored_folder = str(prompt.get("folder_name") or "").strip()
        if projection.get("workspace_source") == "user_path" and restored_root:
            if str(prompt.get("directory_mode") or "") == "use_existing":
                restored_parent = restored_root
            elif (
                restored_folder
                and Path(restored_root).name.casefold()
                == restored_folder.casefold()
            ):
                restored_parent = str(Path(restored_root).parent)
        candidate = RunEventCandidate(
            event_key=(
                f"decision-replay:{request.decision_id}:"
                f"v{decision.decision_version}"
            ),
            kind="decision",
            status=OutcomeStatus.WAITING,
            driver_kind=str(projection["driver_kind"]),
            correlation={"decision_id": request.decision_id},
            payload={
                "run_id": request.run_id,
                "request_id": request.decision_id,
                "decision_id": request.decision_id,
                "nonce": request.nonce,
                "version": decision.decision_version,
                "kind": request.kind.value,
                "prompt": prompt,
                "prompt_schema_version": request.prompt_schema_version,
                "expires_at": request.expires_at,
                "domain_kind": request.domain_kind,
                "domain_id": request.domain_id,
                "call_id": request.call_id,
                "effect_id": request.effect_id,
                "tool_name": request.tool_name,
                "args_hash": request.args_hash,
                "capability_hash": request.capability_hash,
                "scope_hash": request.scope_hash,
                "parent_directory": restored_parent or None,
            },
        )
        replay = RunEvent(
            event_id=candidate.event_key,
            run_id=request.run_id,
            root_run_id=str(projection["root_run_id"]),
            session_id=session_id,
            durable_seq=1,
            candidate=candidate,
            created_at=decision.created_at,
        )
        for event in adapter.to_presentation_events(replay):
            if not isinstance(event, PipelineEvent):
                continue
            await ws.send_json(
                {
                    "type": event.type,
                    "payload": {
                        **event.payload,
                        "session_id": session_id,
                        "run_id": request.run_id,
                        "root_run_id": str(projection["root_run_id"]),
                        "request_id": request.decision_id,
                        "turn_id": str(projection["turn_id"]),
                        "rehydrated": True,
                    },
                }
            )


async def _handle_control_ws_message(raw: dict, *, session_id: str, ws: WebSocket) -> bool:  # noqa: ARG001
    msg_type = raw.get("type", "")
    if msg_type == "harness_inspector_snapshot_request":
        payload = raw.get("payload", raw) or {}
        request_id = str(raw.get("request_id") or payload.get("request_id") or "")
        target_session_id = str(payload.get("session_id") or session_id).strip()
        root_run_id = str(
            payload.get("root_run_id") or payload.get("run_id") or ""
        ).strip()
        schema_version = str(payload.get("schema_version") or "3")

        async def _create_public_snapshot() -> None:
            service = service_context.get("harness_public_read_service")
            try:
                if schema_version != "3":
                    from deskpet.execution.run_read_model import PublicReadError

                    raise PublicReadError(
                        "unsupported_schema",
                        "only Harness Inspector schema V3 is supported",
                    )
                if service is None:
                    raise RuntimeError("harness_public_read_service_unavailable")
                manifest = await service.create_manifest(
                    session_id=target_session_id,
                    root_run_id=root_run_id,
                )
                await ws.send_json(
                    {
                        "type": "harness_inspector_snapshot_response",
                        "request_id": request_id,
                        "ok": True,
                        "snapshot": service.snapshot(manifest).to_dict(),
                    }
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                await ws.send_json(
                    {
                        "type": "harness_inspector_error",
                        "request_id": request_id,
                        "ok": False,
                        "code": getattr(exc, "code", type(exc).__name__),
                        "message": str(exc),
                        "session_id": target_session_id,
                        "root_run_id": root_run_id,
                    }
                )

        if not root_run_id:
            await ws.send_json(
                {
                    "type": "harness_inspector_error",
                    "request_id": request_id,
                    "ok": False,
                    "code": "invalid_request",
                    "message": "root_run_id is required",
                }
            )
            return True
        _schedule_harness_inspector_task(
            ws,
            request_kind="snapshot",
            root_run_id=root_run_id,
            coroutine=_create_public_snapshot(),
        )
        return True
    if msg_type == "harness_inspector_details_request":
        payload = raw.get("payload", raw) or {}
        request_id = str(raw.get("request_id") or payload.get("request_id") or "")
        target_session_id = str(payload.get("session_id") or session_id).strip()
        root_run_id = str(payload.get("root_run_id") or "").strip()

        async def _query_public_details() -> None:
            service = service_context.get("harness_public_read_service")
            try:
                if service is None:
                    raise RuntimeError("harness_public_read_service_unavailable")
                page = await service.query_details(
                    projection_id=str(payload.get("projection_id") or ""),
                    session_id=target_session_id,
                    root_run_id=root_run_id,
                    query_kind=str(payload.get("query_kind") or ""),
                    page_size=int(payload.get("page_size") or 50),
                    cursor=(str(payload["cursor"]) if payload.get("cursor") else None),
                    schema_version=str(payload.get("schema_version") or "3"),
                )
                await ws.send_json(
                    {
                        "type": "harness_inspector_details_response",
                        "request_id": request_id,
                        "ok": True,
                        **page.to_dict(),
                    }
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                await ws.send_json(
                    {
                        "type": "harness_inspector_error",
                        "request_id": request_id,
                        "ok": False,
                        "code": getattr(exc, "code", type(exc).__name__),
                        "message": str(exc),
                        "session_id": target_session_id,
                        "root_run_id": root_run_id,
                    }
                )

        if not root_run_id:
            await ws.send_json(
                {
                    "type": "harness_inspector_error",
                    "request_id": request_id,
                    "ok": False,
                    "code": "invalid_request",
                    "message": "root_run_id is required",
                }
            )
            return True
        _schedule_harness_inspector_task(
            ws,
            request_kind="details",
            root_run_id=root_run_id,
            coroutine=_query_public_details(),
        )
        return True
    if msg_type == "harness_inspector_cancel":
        payload = raw.get("payload", raw) or {}
        root_run_id = str(payload.get("root_run_id") or "").strip() or None
        kind = str(payload.get("request_kind") or "all").strip()
        request_kind = None if kind == "all" else kind
        cancelled = _cancel_harness_inspector_tasks(
            ws,
            root_run_id=root_run_id,
            request_kind=request_kind,
        )
        await ws.send_json(
            {
                "type": "harness_inspector_cancelled",
                "request_id": str(raw.get("request_id") or ""),
                "cancelled": cancelled,
                "root_run_id": root_run_id,
            }
        )
        return True
    if msg_type == "harness_inspector_snapshot":
        payload = raw.get("payload", {}) or {}
        request_id = str(raw.get("request_id") or "")
        target_session_id = (
            str(payload.get("session_id") or session_id).strip() or session_id
        )
        run_id = str(payload.get("run_id") or "").strip()
        workflow_service = service_context.get("workflow_service")
        execution_uow = getattr(workflow_service, "execution_uow", None)
        try:
            if execution_uow is None:
                raise RuntimeError("execution_uow_unavailable")
            if not run_id:
                raise ValueError("harness inspector requires run_id")
            snapshot = await execution_uow.inspect_harness_run(
                run_id,
                expected_session_id=target_session_id,
            )
            if snapshot is None:
                raise ValueError(
                    "run does not belong to the requested session"
                )
            await ws.send_json(
                {
                    "type": "harness_inspector_snapshot_response",
                    "request_id": request_id,
                    "ok": True,
                    "payload": snapshot,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "harness_inspector_snapshot_failed",
                session_id=target_session_id,
                run_id=run_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            await ws.send_json(
                {
                    "type": "harness_inspector_error",
                    "request_id": request_id,
                    "ok": False,
                    "payload": {
                        "session_id": target_session_id,
                        "run_id": run_id,
                        "code": getattr(
                            exc, "code", type(exc).__name__
                        ),
                        "detail": str(exc),
                    },
                }
            )
        return True
    if msg_type in {"task_projections_list", "task_projection_update"}:
        payload = raw.get("payload", {}) or {}
        target_session_id = (
            str(payload.get("session_id") or session_id).strip() or session_id
        )
        workflow_service = service_context.get("workflow_service")
        execution_uow = getattr(workflow_service, "execution_uow", None)
        if execution_uow is None:
            await ws.send_json(
                {
                    "type": "task_projection_error",
                    "payload": {
                        "session_id": target_session_id,
                        "code": "execution_uow_unavailable",
                        "detail": "Task projection storage is unavailable",
                    },
                }
            )
            return True
        try:
            if msg_type == "task_projections_list":
                records = await execution_uow.list_task_run_projections(
                    target_session_id,
                    include_closed=bool(payload.get("include_closed", False)),
                )
                projections = []
                for projection in records:
                    boundary = await execution_uow.get_conversation_boundary(
                        projection.root_run_id
                    )
                    work_context = await execution_uow.get_task_work_context(
                        projection.root_run_id
                    )
                    lifecycle = await execution_uow.get_task_run_lifecycle(
                        projection.root_run_id,
                        expected_session_id=target_session_id,
                    )
                    projections.append(
                        {
                            "projection_id": projection.projection_id,
                            "session_id": projection.session_id,
                            "run_id": projection.root_run_id,
                            "task_scope_id": projection.task_scope_id,
                            "ui_state": projection.ui_state,
                            "version": projection.version,
                            "conversation_boundary_ref": (
                                None
                                if boundary is None
                                else boundary.boundary_ref
                            ),
                            "conversation_boundary_version": (
                                None if boundary is None else boundary.version
                            ),
                            "workspace_root": (
                                None
                                if work_context is None
                                else work_context.workspace_root
                            ),
                            "workspace_source": (
                                None
                                if work_context is None
                                else work_context.workspace_source
                            ),
                            "status": (
                                None
                                if lifecycle is None
                                else lifecycle["status"]
                            ),
                            "started_at": (
                                None
                                if lifecycle is None
                                else lifecycle["started_at"]
                            ),
                            "updated_at": (
                                None
                                if lifecycle is None
                                else lifecycle["updated_at"]
                            ),
                            "ended_at": (
                                None
                                if lifecycle is None
                                else lifecycle["ended_at"]
                            ),
                        }
                    )
                await ws.send_json(
                    {
                        "type": "task_projections_response",
                        "payload": {
                            "session_id": target_session_id,
                            "projections": projections,
                        },
                    }
                )
                await _replay_open_harness_decisions(
                    ws,
                    execution_uow=execution_uow,
                    session_id=target_session_id,
                )
                return True

            run_id = str(payload.get("run_id") or "").strip()
            ui_state = str(payload.get("ui_state") or "").strip()
            expected_version = payload.get("expected_version")
            if not run_id or expected_version is None:
                raise ValueError(
                    "task projection update requires run_id/expected_version"
                )
            current = await execution_uow.get_task_run_projection(run_id)
            if current is None or current.session_id != target_session_id:
                raise ValueError(
                    "task projection does not belong to the requested session"
                )
            projection = await execution_uow.set_task_run_projection_state(
                run_id,
                ui_state,
                expected_version=int(expected_version),
            )
            await ws.send_json(
                {
                    "type": "task_projection_updated",
                    "payload": {
                        "projection_id": projection.projection_id,
                        "session_id": projection.session_id,
                        "run_id": projection.root_run_id,
                        "task_scope_id": projection.task_scope_id,
                        "ui_state": projection.ui_state,
                        "version": projection.version,
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "task_projection_request_failed",
                request_type=msg_type,
                session_id=target_session_id,
                error=str(exc),
            )
            await ws.send_json(
                {
                    "type": "task_projection_error",
                    "payload": {
                        "session_id": target_session_id,
                        "run_id": str(payload.get("run_id") or ""),
                        "code": getattr(exc, "code", type(exc).__name__),
                        "detail": str(exc),
                    },
                }
            )
        return True
    if msg_type != "ppt_outline_decision":
        return False
    payload = raw.get("payload", {}) or {}
    oid = str(payload.get("outline_id") or "")
    decision = {
        "action": str(payload.get("action") or "").strip().lower(),
        "feedback": str(payload.get("feedback") or "").strip(),
        "reuse_id": payload.get("reuse_id") or None,
    }
    if decision["action"] == "modify" and not decision["feedback"]:
        await _ppt_notify_chat_bubble(
            session_id,
            "请先填写需要修改的大纲内容，再提交修改。",
        )
        logger.info("ppt_outline_decision_invalid_feedback", outline_id=oid)
        return True
    has_durable_fence = all(
        (
            str(payload.get("run_id") or "").strip(),
            str(payload.get("decision_id") or "").strip(),
            str(payload.get("nonce") or "").strip(),
            payload.get("version") is not None,
        )
    )
    target_session_id = str(payload.get("session_id") or session_id).strip() or session_id
    if has_durable_fence:
        try:
            receipt = await _signal_product_harness_decision(
                target_session_id,
                payload,
                decision,
            )
        except Exception as exc:
            logger.warning(
                "ppt_outline_decision_rejected",
                outline_id=oid,
                session_id=target_session_id,
                error=str(exc),
            )
            return True
        resolved = bool(
            receipt.get("accepted")
            if isinstance(receipt, dict)
            else getattr(receipt, "accepted", False)
        )
        workflow_sid = target_session_id if resolved else None
    else:
        workflow_sid = await _resolve_workflow_ppt_outline(oid, decision) if oid else None
        resolved = workflow_sid is not None
    if resolved:
        await _broadcast_control({
            "type": "ppt_outline_resolved",
            "payload": {
                "outline_id": oid,
                "session_id": workflow_sid,
                "decision_status": _ppt_outline_decision_notice(decision["action"]),
            },
        })
        if workflow_sid is not None:
            await _ppt_notify_chat_bubble(
                workflow_sid,
                _ppt_outline_decision_notice(decision["action"]),
            )
        logger.info("ppt_outline_decision_resolved", outline_id=oid)
    else:
        logger.info("ppt_outline_decision_no_pending", outline_id=oid)
    return True


def _ppt_outline_decision_notice(action: str) -> str:
    return {
        "modify": (
            "收到修改意见，正在修改 PPT 大纲。"
            "修改完成后会在当前会话展示新版本，请再次确认。"
        ),
        "reuse": "已采用历史大纲，正在生成 PPT。完成后文件会发送到当前会话。",
        "cancel": "已取消本次 PPT 生成。",
    }.get(
        str(action or "").strip().lower(),
        "大纲已确认，正在生成 PPT。完成后文件会发送到当前会话。",
    )


async def _ppt_notify_chat_bubble(sid: str, text: str) -> None:
    target_sid = sid or "default"
    msg = {
        "type": "chat_response",
        "payload": {
            "text": str(text or ""),
            "provider": "ppt_pro",
            "session_id": target_sid,
        },
    }
    sdb = service_context.get("session_db")
    if sdb is not None and str(text or "").strip():
        try:
            msg_id = await sdb.append_message(
                session_id=target_sid,
                role="assistant",
                content=str(text or ""),
            )
            vw = service_context.get("vector_worker")
            if vw is not None and msg_id is not None:
                await vw.enqueue(msg_id, str(text or ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("ppt_notify_persist_failed sid=%s err=%s", target_sid, exc)

    await _broadcast_control(msg)


def _expire_ppt_outline_dangling_for_startup() -> int:
    try:
        expired = int(expire_dangling_proposed() or 0)
        if expired:
            logger.info("ppt_outline_expired_dangling", count=expired)
        return expired
    except Exception as exc:  # noqa: BLE001
        logger.warning("ppt_outline_expire_startup_failed", error=str(exc))
        return 0


# ppt_pro 渲染专用线程池：真机 E2E 发现，若用默认共享 executor(None)，render 任务会
# 排在 BGE-M3 embedder / vector-worker / summarizer 等大量 run_in_executor 工作后面、
# 长时间不执行(表现为 deck 永不落盘的"假 hang")。给 render 独立池根治排队饿死。
import concurrent.futures as _cf  # noqa: E402
_PPT_PRO_RENDER_EXECUTOR = _cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="ppt_pro_render")


def _configure_ppt_workflow_adapter(launcher):
    from dataclasses import replace

    from deskpet.workflows.adapters.ppt_runtime import PptRuntime
    from deskpet.workflows.contracts import WorkflowContext
    from deskpet.workflows.definitions.research_core import legacy_ports
    from deskpet.workflows.definitions.v1 import ppt_pro_initial_state

    runtime = PptRuntime(
        executor=_PPT_PRO_RENDER_EXECUTOR,
        dispatch_fence_acquirer=service_context.get(
            "run_execution_fence_acquirer"
        ),
    )

    async def frozen_llm_call(run_id: str):
        workflow_service = service_context.get("workflow_service")
        execution_uow = getattr(workflow_service, "execution_uow", None)
        registry = service_context.get("provider_registry")
        if execution_uow is None or registry is None:
            raise RuntimeError("ppt frozen provider authority unavailable")
        record = await execution_uow.read_run(run_id)
        if record is None:
            raise RuntimeError(f"ppt execution run not found:{run_id}")
        provider_plan = record.spec.context.provider_plan
        provider_ids = tuple(
            str(item).strip()
            for item in provider_plan.get("providers", ())
            if str(item).strip()
        )
        if not provider_ids:
            raise RuntimeError("ppt frozen provider plan is empty")
        entries = tuple(registry.get_entry(provider_id) for provider_id in provider_ids)
        if any(entry is None for entry in entries):
            missing = ",".join(
                provider_id
                for provider_id, entry in zip(provider_ids, entries)
                if entry is None
            )
            raise RuntimeError(f"ppt frozen provider unavailable:{missing}")
        providers = _build_agent_provider_chain(entries=entries, registry=registry)
        return _make_str_llm_call(
            providers[0], max_tokens=4096, purpose="research"
        )

    async def context_factory(row, _start_payload) -> WorkflowContext:
        llm_call = await frozen_llm_call(str(row.get("run_id") or ""))
        ports = legacy_ports(llm_call=llm_call, search=None, extract=None)
        # The legacy Research port can carry a process-global cloud reranker.
        # A durable child must not escape its frozen provider plan through that
        # auxiliary callback.
        ports = replace(ports, llm=replace(ports.llm, rerank=None))
        return WorkflowContext(
            ports={
                "llm": ports.llm, "search": ports.search, "fetch": ports.fetch,
                "effect": runtime, "evaluator": runtime,
                "notifier": _ppt_workflow_outline_notify,
            },
            request_id=str(row.get("request_id") or ""),
            turn_id=str(row.get("turn_id") or ""),
        )

    def state_factory(**values):
        return ppt_pro_initial_state(
            topic=str(values["topic"]), run_id=str(values["run_id"]),
            thread_id=str(values["thread_id"]), session_id=str(values["session_id"]),
            pages=int(values.get("pages") or 8), depth=str(values.get("depth") or "deep"),
            theme=str(values.get("theme") or "minimal"),
            image_mode=bool(values.get("image_mode", True)),
            editable_required=bool(values.get("editable_required", True)),
            full_page_images=bool(values.get("full_page_images", False)),
            title=str(values.get("title") or values["topic"]),
            author=str(values.get("author") or "DeskPet"),
            output_path=values.get("output_path"),
            blob_root=str(values.get("blob_root") or ""),
        )

    launcher.register_adapter(
        "ppt_pro", "v1", state_factory=state_factory, context_factory=context_factory,
    )
    return state_factory, context_factory


def _configure_ppt_production(*, recover: bool = True) -> None:
    workflow_service = service_context.get("workflow_service")
    launcher = getattr(workflow_service, "launcher", None)
    if launcher is not None:
        _configure_ppt_workflow_adapter(launcher)
    # Recovery is owned by Harness bootstrap/supervisor; never spawn an
    # untracked PPT recovery task here.
    ppt_tools.set_ppt_pro_services(workflow_starter=None)


def _initial_chat_peer_group(transport_sid: str) -> str:
    if transport_sid in {"default", "message-panel-main"}:
        return task_session_manager.active_sid("default")
    return task_session_manager.active_sid(transport_sid)


def _register_chat_peer(transport_sid: str) -> str:
    sid = transport_sid or "default"
    task_session_manager.register_peer(sid)
    group = _chat_peer_groups.get(sid)
    if group is None:
        group = _initial_chat_peer_group(sid)
        _chat_peer_groups[sid] = group
    return group


def _remap_chat_peer_group(transport_sid: str, effective_sid: str) -> None:
    sid = transport_sid or "default"
    old_group = _chat_peer_groups.get(sid, _initial_chat_peer_group(sid))
    _chat_peer_groups.setdefault(sid, old_group)
    for peer_sid, group in list(_chat_peer_groups.items()):
        if group == old_group:
            _chat_peer_groups[peer_sid] = effective_sid
    task_session_manager.remap_peer_group(sid, effective_sid)


def _resolve_chat_task_scope(
    *,
    base_sid: str,
    text: str,
    payload: dict,
) -> TaskScopeDecision:
    payload = payload or {}
    force_l2 = bool(payload.get("force_l2")) or (text or "").startswith("/continue")
    decision = task_session_manager.resolve(
        base_sid,
        text or "",
        explicit_new=bool(payload.get("new_session")),
        force_l2=force_l2,
    )
    return decision


def _select_code_workflow_provider(*, local_llm, cloud_llm):
    """Select the legacy fallback when no session provider chain resolves."""

    return local_llm or cloud_llm


def _validate_code_recovery_capability_subset(
    *,
    prepared_names,
    allowed_tools,
) -> None:
    """Require child workflow tools to be a narrowing of frozen Context OS."""

    # Durable children intentionally remove widening controls such as
    # workflow_spawn. ProposalPort exposes only ``allowed_tools`` and rejects
    # every call outside that immutable child snapshot, so exact equality
    # would reject the valid least-authority child. Still fail closed if the
    # child names anything absent from the trusted prepared ToolSet.
    if not set(allowed_tools).issubset(set(prepared_names)):
        raise RuntimeError("code recovery capability snapshot mismatch")


def _resolve_code_recovery_provider(*, provider_snapshot, model_snapshot, registry, fallback):
    """Rebuild the exact frozen Code provider or fail recovery closed."""

    provider_id = str(
        provider_snapshot.get("provider_id")
        or provider_snapshot.get("provider")
        or ""
    )
    model = str(model_snapshot.get("model") or "")
    if not provider_id or not model:
        raise RuntimeError("code recovery provider/model snapshot is incomplete")
    entry = registry.get_entry(provider_id) if registry is not None else None
    if entry is not None:
        api_key = registry.resolve_api_key(entry.id) or "ollama"
        provider = OpenAICompatibleProvider(
            base_url=entry.base_url,
            api_key=api_key,
            model=model,
            temperature=getattr(entry, "temperature", 0.7),
            sanitize_inline_cot_dsml=_sanitize_cot_dsml,
            code_params=getattr(entry, "code_params", None),
            is_relay=(entry.source == "relay"),
        )
        provider.provider_id = str(entry.id)
        return provider
    fallback_id = str(
        getattr(fallback, "provider_id", "")
        or getattr(fallback, "id", "")
        or (type(fallback).__name__ if fallback is not None else "")
    )
    fallback_model = str(getattr(fallback, "model", "") or "")
    if fallback is None or fallback_id != provider_id or fallback_model != model:
        raise RuntimeError(
            f"frozen code provider unavailable:{provider_id}:{model}"
        )
    return fallback


def _freeze_durable_task_provider_snapshot(
    provider_id: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Freeze the exact provider/model used by a new durable child."""

    registry = service_context.get("provider_registry")
    entry = registry.get_entry(provider_id) if registry is not None else None
    if entry is None or not str(getattr(entry, "model", "") or "").strip():
        raise RuntimeError(
            f"durable task provider unavailable:{provider_id}"
        )
    return (
        {"provider_id": str(entry.id)},
        {"model": str(entry.model)},
    )


def _build_agent_provider_chain(*, entries, registry):
    """Build every provider resolved for a general-agent Run, in order."""

    providers = []
    for entry in entries:
        api_key = registry.resolve_api_key(entry.id) or "ollama"
        provider = OpenAICompatibleProvider(
                base_url=entry.base_url,
                api_key=api_key,
                model=entry.model,
                temperature=getattr(entry, "temperature", 0.7),
                sanitize_inline_cot_dsml=_sanitize_cot_dsml,
                code_params=getattr(entry, "code_params", None),
                is_relay=(entry.source == "relay"),
            )
        provider.provider_id = str(entry.id)
        provider.incarnation_id = str(getattr(entry, "incarnation_id", "") or "")
        provider.config_revision = int(getattr(entry, "config_revision", 0) or 0)
        provider.binding_epoch = int(getattr(entry, "binding_epoch", 0) or 0)
        providers.append(provider)
    return providers


async def _resolve_agent_provider_chain(
    session_id: str,
    *,
    session_db: Any,
) -> list[Any] | None:
    """Resolve the current authenticated provider chain for one Run."""

    if _e2e_provider_base:
        return [
            OpenAICompatibleProvider(
                base_url=_e2e_provider_base,
                api_key="ctx-e2e-local-key",
                model=model,
                temperature=config.llm.local.temperature,
                sanitize_inline_cot_dsml=_sanitize_cot_dsml,
            )
            for model in ("ctx-primary", "ctx-fallback", "ctx-compact-fixture")
        ]
    registry = service_context.get("provider_registry")
    readiness = service_context.get("provider_routing_readiness")
    from llm.resolution import (
        SessionProviderUnavailable,
        resolve_session_provider_chain,
    )

    if readiness is None:
        raise SessionProviderUnavailable("provider_routing_initializing")
    readiness.require_ready()
    if registry is None:
        raise SessionProviderUnavailable("provider_registry_unavailable")
    if session_db is None:
        raise SessionProviderUnavailable("provider_session_db_unavailable")

    entries = await resolve_session_provider_chain(
        session_id,
        registry=registry,
        session_db=session_db,
        readiness=readiness,
    )
    logger.info(
        "agent_provider_chain_resolved sid=%s n=%d models=%s",
        session_id,
        len(entries or ()),
        [getattr(entry, "model", "?") for entry in (entries or ())],
    )
    return (
        _build_agent_provider_chain(entries=entries, registry=registry)
        if entries
        else None
    )


def _restore_product_context_os(request):
    """Rebuild one request-scoped tool authority from its durable snapshot."""

    if not bool(getattr(getattr(config, "features", None), "context_os_v1", False)):
        return None
    if _is_companion_background_request(request):
        # Companion reflection/build/evaluation Runs are deliberately
        # zero-tool.  Their trusted host extension is the authority; requiring
        # a foreground Context OS lease here would both invent tools and make
        # durable background recovery impossible.
        return None
    raw = request.request_payload.get("context_os")
    if not isinstance(raw, dict):
        raise RuntimeError("product recovery Context OS snapshot is unavailable")
    from deskpet.agent.assembler.bundle import PreparedContext
    from deskpet.capabilities.refresh import context_os_snapshot_ref
    from deskpet.tools.prepared_snapshot import load_context_os_snapshot

    prepared_tool_set, eligibility = load_context_os_snapshot(raw)
    context = request.run_context
    if context is None:
        raise RuntimeError("product recovery trusted RunContext is unavailable")
    if (
        eligibility.session_id != context.session_id
        or eligibility.request_id != context.request_id
    ):
        raise RuntimeError("product recovery Context OS identity mismatch")
    deskpet_tool_registry_v2.validate_prepared_tool_set(
        prepared_tool_set, eligibility=eligibility
    )
    exact_snapshot_ref = context_os_snapshot_ref(prepared_tool_set, eligibility)
    frozen_snapshot_ref = str(
        request.capability_snapshot.get("prepared_tool_set_ref") or ""
    )
    payload_snapshot_ref = str(
        request.request_payload.get("tool_set_snapshot_ref") or ""
    )
    if frozen_snapshot_ref or payload_snapshot_ref:
        if (
            not frozen_snapshot_ref
            or not payload_snapshot_ref
            or frozen_snapshot_ref != exact_snapshot_ref
            or payload_snapshot_ref != exact_snapshot_ref
        ):
            raise RuntimeError("product recovery capability snapshot mismatch")
    else:
        # Compatibility for runs created before the exact catalog-lease
        # snapshot became the production authority.
        expected_names = {
            capability.ref.name
            for capability in (
                *prepared_tool_set.direct,
                *prepared_tool_set.activated,
            )
        }
        raw_names = request.capability_snapshot.get(
            "tools", request.capability_snapshot.get("capabilities", ())
        )
        actual_names = {str(name) for name in raw_names}
        if expected_names != actual_names:
            raise RuntimeError("product recovery capability snapshot mismatch")
    scope_store = service_context.get("tool_capability_scope_store")
    if scope_store is None:
        raise RuntimeError("product recovery capability scope store unavailable")
    existing_scope = scope_store.get(
        prepared_tool_set.scope_id,
        session_id=context.session_id,
        request_id=context.request_id,
    )
    if existing_scope is None:
        try:
            scope_store.open(prepared_tool_set, eligibility)
        except ValueError:
            if scope_store.get(
                prepared_tool_set.scope_id,
                session_id=context.session_id,
                request_id=context.request_id,
            ) is None:
                raise
    elif (
        existing_scope.prepared.revision != prepared_tool_set.revision
        or existing_scope.prepared.registry_revision
        != prepared_tool_set.registry_revision
    ):
        scope_store.commit_prevalidated(prepared_tool_set)
    return PreparedContext(
        messages=[dict(message) for message in request.canonical_messages],
        tool_set=prepared_tool_set,
    )


def _is_companion_background_request(request) -> bool:
    context = getattr(request, "run_context", None)
    if (
        context is None
        or str(getattr(context, "venue", "") or "") != "background"
        or not str(getattr(context, "session_id", "") or "").startswith(
            "companion:"
        )
    ):
        return False
    payload = getattr(request, "request_payload", {})
    background = None
    if isinstance(payload, Mapping):
        background = payload.get("companion_background")
        if background is None and isinstance(payload.get("payload"), Mapping):
            # DriverStart recovery carries the sanitized request envelope,
            # whose product payload is nested one level below the request.
            background = payload["payload"].get("companion_background")
    return (
        isinstance(background, Mapping)
        and int(background.get("schema_version") or 0) == 1
        and str(background.get("purpose") or "")
        # Keep this in lockstep with companion.run_adapter._PURPOSES.
        # Candidate authoring is a delegated task whose product stage is
        # ``candidate_build``; treating that stage name as the purpose made
        # both fresh and recovered candidate Runs enter ordinary Context OS.
        in {"reflection", "evaluation", "delegated_task"}
    )


def _bind_frozen_skill_snapshot_resolver(resolver) -> None:
    """Bind one Manager-backed resolver to every production Skill reader."""

    if resolver is None or not getattr(resolver, "manager_backed", False):
        raise RuntimeError("manager_skill_snapshot_resolver_required")
    assembler = service_context.get("context_assembler")
    bind = getattr(assembler, "bind_skill_snapshot_resolver", None)
    if not callable(bind):
        raise RuntimeError("assembler_skill_component_unavailable")
    bind(resolver)
    service_context.register("frozen_skill_instruction_resolver", resolver)


async def _build_product_agent_loop(request):
    """Rehydrate one AgentLoop from durable Run data and live host services."""

    from agent.context_manager import ContextManager
    from agent.tool_use_shim import OpenAICompatibleAgentLLM

    context = request.run_context
    if context is None:
        raise ValueError("product AgentLoop requires a trusted RunContext")
    from deskpet.execution.provider_workloads import workload_context

    def _run_workload_context(callsite_id: str):
        return workload_context(
            callsite_id,
            request_id=context.request_id,
            session_id=context.session_id,
            root_run_id=context.root_run_id,
            task_scope_id=context.turn_id,
        )
    session_db = service_context.get("session_db")
    providers = await _resolve_agent_provider_chain(
        context.session_id,
        session_db=session_db,
    )
    primary = providers[0] if providers else _select_code_workflow_provider(
        local_llm=local_llm,
        cloud_llm=cloud_llm,
    )
    if primary is None:
        raise RuntimeError("no LLM provider is available")
    workspace = context.workspace.get("root")
    manager_config = ((config.raw.get("context") or {}).get("manager") or {})
    context_manager = ContextManager.for_session(
        model=str(getattr(primary, "model", "") or "_default"),
        project_root=workspace,
        v2_enabled=bool(manager_config.get("v2_enabled", True)),
        adaptive_compact_pct=bool(
            getattr(getattr(config, "features", None), "adaptive_compact_pct", False)
        ),
    )
    loop_options = dict(request.request_payload.get("loop") or {})
    companion_background = _is_companion_background_request(request)
    prepared_context = _restore_product_context_os(request)
    problem_type = loop_options.get("pipeline_problem_type")
    response_quality_gate = None
    response_quality = loop_options.get("response_quality")
    if (
        not companion_background
        and isinstance(response_quality, Mapping)
        and response_quality.get("mode") == "semantic_completeness"
        and response_quality.get("preference_key") == "response.detail"
        and response_quality.get("preference_value") == "brief"
    ):
        from deskpet.companion.response_quality import (
            ModelResponseCompletenessGate,
        )

        response_quality_gate = ModelResponseCompletenessGate()
    frozen_skill_resolver = service_context.get(
        "frozen_skill_instruction_resolver"
    )
    if frozen_skill_resolver is None and not companion_background:
        raise RuntimeError("frozen_skill_resolver_not_injected")
    run_tool_registry = deskpet_tool_registry_v2
    if companion_background:
        from deskpet.tools.registry import ToolRegistry

        # A fresh empty registry makes the zero-tool contract true in the
        # model prompt as well as at execution admission.
        run_tool_registry = ToolRegistry()
    agent = build_agent(
        config,
        llm_registry=OpenAICompatibleAgentLLM(primary),
        tool_registry=run_tool_registry,
        context_manager=context_manager,
        receipt_store_getter=_get_receipt_store,
        max_iterations=50,
        completion_probe=None,
        session_todo_getter=None,
        max_completion_nudges=2,
        signature_repeat_threshold=int(
            (config.raw.get("supervisor") or {}).get(
                "tool_signature_repeat_threshold", 3
            )
        ),
        session_goal_store=service_context.get("session_goal_store"),
        goal_checker=service_context.get("goal_checker"),
        compressor=service_context.get("context_compressor"),
        skill_loader=(
            None if companion_background else frozen_skill_resolver
        ),
        skill_matcher=(
            None
            if companion_background
            else service_context.get("skill_matcher")
        ),
        memory_curator=(
            None
            if companion_background
            else service_context.get("memory_curator")
        ),
        provider_workload_context_factory=_run_workload_context,
        provider_workload_router=service_context.get(
            "provider_workload_router"
        ),
        context_attempt_store=service_context.get("context_attempt_store"),
        evidence_gate=(
            service_context.get("pipeline_evidence_gate")
            if problem_type is not None
            else None
        ),
        pipeline_problem_type=problem_type,
        pipeline_needs_investigation=bool(
            loop_options.get("pipeline_needs_investigation", False)
        ),
        pipeline_observability=bool(
            loop_options.get("pipeline_observability", False)
        ),
        convergence_report_on_stop=bool(
            loop_options.get("convergence_report_on_stop", False)
        ),
        response_quality_gate=response_quality_gate,
        # Companion reflection/candidate/evaluation Runs are deliberately
        # zero-tool and return host-validated structured JSON.  The ordinary
        # claim/receipt VerifyGate assumes an effect-capable ReAct answer and
        # can only manufacture unmatched claims here, so keep that gate on
        # the foreground/product path and let the Companion terminal
        # extensions plus evaluation pipeline validate background results.
        enable_verify_gate=not companion_background,
    )
    return (
        agent,
        {
            "provider_chain": providers,
            "loop_user_request": loop_options.get("user_request"),
            "is_sentinel_run": bool(loop_options.get("is_sentinel_run", False)),
            "host_validated_zero_tool_terminal": companion_background,
            "context_request_id": context.request_id,
            "prepared_context": prepared_context,
        },
    )


_harness_runtime = None
_harness_venue = None
_harness_accepting = False


def _trigger_harness_recovery_after_identity_bind() -> bool:
    """Retry durable Runs only after the process identity gate is open."""

    runtime = _harness_runtime
    reconciler = getattr(runtime, "reconciler", None)
    trigger = getattr(reconciler, "trigger", None)
    if not callable(trigger):
        return False
    trigger()
    return True


async def _build_product_harness_stack(generation: int):
    from deskpet.capabilities.contracts import CapabilityScope
    from deskpet.companion.authority import GrowthAuthorityPhase
    from deskpet.companion.turn_authority import (
        CompanionTurnAuthority,
        HubStorePersonalWorkflowCandidateSource,
        ModelPersonalWorkflowMatcher,
    )
    from deskpet.companion.preferences import (
        ModelPreferenceTurnInterpreter,
        PREFERENCE_TURN_DECISION_SCHEMA,
    )
    from deskpet.companion.signals import (
        GrowthTerminalDeliveryContributor,
        GrowthTerminalDeliverySink,
    )
    from deskpet.agent.session_terminal_delivery import (
        SessionTerminalDeliveryContributor,
        SessionTerminalDeliverySink,
        SessionTerminalProjectionConsistencyGate,
    )
    from deskpet.harness.adapters.product_composition import build_product_harness_composition
    from deskpet.harness.adapters.product_profiles import build_product_profile_registry
    from deskpet.harness.projector import SinkRegistration

    workflow_service = service_context.get("workflow_service")
    launcher = getattr(workflow_service, "launcher", None)
    if workflow_service is None or launcher is None:
        raise RuntimeError("workflow service is unavailable for Harness activation")
    uow = workflow_service.execution_uow
    profiles = build_product_profile_registry(
        workflow_service.runtime_adapters,
        blob_root=_paths.user_data_dir() / "workflows" / "blobs",
    )
    capability_platform = service_context.get("capability_platform")
    identity_gate = service_context.get("companion_identity_gate")
    preference_resolver = service_context.get(
        "companion_preference_resolver_dormant"
    )
    session_db = service_context.get("session_db")
    if (
        capability_platform is None
        or identity_gate is None
        or preference_resolver is None
        or session_db is None
    ):
        raise RuntimeError(
            "Companion turn authority dependencies are unavailable"
        )

    def personal_workflow_scope(request, identity):
        turn = request.turn
        root_run_id = str(getattr(turn, "root_run_id", "") or "")
        if not root_run_id:
            raise RuntimeError("personal workflow scope requires root Run identity")
        return CapabilityScope.for_run(
            root_run_id,
            project_root=getattr(turn, "workspace_ref", None),
            user_key=identity.owner_key,
        )

    async def capture_owner_memory_scope(turn, _services, identity):
        del turn
        # Task 10 installs the future Companion turn authority while the
        # GrowthAuthorityRouter still routes production reads/writes to the
        # legacy branch.  Do not manufacture a Companion owner binding until
        # Task 13 has atomically moved the sole authority to COMPANION.
        router = _growth_authority_router
        if (
            router is None
            or router.current.phase is not GrowthAuthorityPhase.COMPANION
        ):
            return None
        if session_db is None:
            raise RuntimeError("owner memory scope store is unavailable")
        return await session_db.capture_owner_memory_read_scope(
            identity.owner.profile_id,
            identity.owner.profile_generation,
            identity.binding_epoch,
        )

    companion_turn_authority = CompanionTurnAuthority(
        identity_gate=identity_gate,
        preference_resolver=preference_resolver,
        preference_interpreter=ModelPreferenceTurnInterpreter(
            _make_session_aware_llm_call(
                max_tokens=384,
                response_format=PREFERENCE_TURN_DECISION_SCHEMA,
            )
        ),
        personal_workflow_source=HubStorePersonalWorkflowCandidateSource(
            capability_platform.store,
            scope_resolver=personal_workflow_scope,
        ),
        personal_workflow_matcher=ModelPersonalWorkflowMatcher(
            _make_session_aware_llm_call(max_tokens=128)
        ),
        owner_memory_read_scope=capture_owner_memory_scope,
    )
    admission_runtime = service_context.get(
        "admission_task_grant_runtime"
    )

    async def authorize_admission(record, boundary, actor):
        if admission_runtime is None:
            return None
        workspace_payload = dict(record.context.workspace)
        workspace = (
            workspace_payload.get("write_scope_root")
            or workspace_payload.get("root")
        )
        return await admission_runtime.authorize(
            root_run_id=record.context.root_run_id,
            principal_id=actor.principal_id,
            workspace=(
                None if workspace is None else str(workspace)
            ),
            prompt=boundary.admission.prompt,
        )

    async def project_user_continuation_status(
        record, signal, status: str, error: str | None,
    ) -> None:
        request_id = str(signal.message_ref or "")
        if request_id.startswith("request:"):
            request_id = request_id[len("request:"):]
        await _broadcast_default_chat_peers(
            None,
            {
                "type": "chat_v2_continuation_status",
                "payload": {
                    "session_id": record.context.session_id,
                    "request_id": request_id,
                    "run_id": record.run_id,
                    "task_scope_id": signal.task_scope_id,
                    "status": status,
                    "error": error,
                },
            },
        )

    growth_sink = GrowthTerminalDeliverySink(_companion_store)
    session_terminal_sink = SessionTerminalDeliverySink(session_db)
    service_context.register(
        "session_terminal_projection_gate",
        SessionTerminalProjectionConsistencyGate(
            uow, session_db, session_terminal_sink
        ),
    )
    return await build_product_harness_composition(
        uow=uow,
        profiles=profiles,
        workflow_launcher=launcher,
        tool_registry=deskpet_tool_registry_v2,
        loop_factory=_build_product_agent_loop,
        auto_mode_check=lambda: bool(
            permission_gate_v2 is not None
            and getattr(permission_gate_v2, "auto_mode", False)
        ),
        authorization_runtime=service_context.get("authorization_runtime"),
        capability_refresh_staging=service_context.get(
            "capability_refresh_staging"
        ),
        capability_refresh_service=service_context.get(
            "capability_refresh_service"
        ),
        capability_scope_store=service_context.get(
            "tool_capability_scope_store"
        ),
        brokered_planner=getattr(
            service_context.get("capability_platform"),
            "brokered_planner",
            None,
        ),
        capability_builder_host=service_context.get(
            "capability_builder_host"
        ),
        provider_snapshot_resolver=_freeze_durable_task_provider_snapshot,
        capability_hub=getattr(
            service_context.get("capability_platform"),
            "hub",
            None,
        ),
        capability_platform=capability_platform,
        companion_turn_authority=companion_turn_authority,
        admission_authorizer=authorize_admission,
        provider_invocation_coordinator=service_context.get(
            "provider_invocation_coordinator"
        ),
        provider_fence_acquirer=service_context.get(
            "run_execution_fence_acquirer"
        ),
        delivery_handlers=workflow_service.delivery_handlers,
        delivery_bound_readers={
            "websocket": lambda _target_id: bool(_control_connections),
        },
        extra_delivery_registrations=(
            SinkRegistration(
                "companion_growth",
                "growth-events-v1",
                growth_sink,
            ),
            SinkRegistration(
                "session_terminal",
                "session-transcript-v1",
                session_terminal_sink,
            ),
        ),
        terminal_delivery_contributors=(
            GrowthTerminalDeliveryContributor(identity_gate),
            SessionTerminalDeliveryContributor(),
        ),
        continuation_observer=project_user_continuation_status,
        goal_store=service_context.get("session_goal_store"),
        owner_generation=generation,
    )


async def _issue_product_harness_host(
    session_id: str,
    *,
    workspace: str | None,
    allow_provider_unavailable: bool = False,
    allow_workspace_unavailable: bool = False,
):
    from deskpet.execution.contracts import ProviderLaunchSnapshot, fingerprint_json
    from deskpet.harness.contracts import HostContext

    session_db = service_context.get("session_db")
    try:
        providers = await _resolve_agent_provider_chain(
            session_id,
            session_db=session_db,
        )
    except Exception as exc:
        from llm.resolution import SessionProviderUnavailable

        if not allow_provider_unavailable or not isinstance(
            exc, SessionProviderUnavailable
        ):
            raise
        providers = []
    provider = (
        providers[0]
        if providers
        else (
            None
            if allow_provider_unavailable
            else _select_code_workflow_provider(
                local_llm=local_llm,
                cloud_llm=cloud_llm,
            )
        )
    )
    if provider is None and not allow_provider_unavailable:
        from llm.resolution import SessionProviderUnavailable

        raise SessionProviderUnavailable("global_provider_chain_empty")
    provider_id = str(
        getattr(provider, "provider_id", "")
        or getattr(provider, "id", "")
        or ("unavailable" if provider is None else type(provider).__name__)
    )
    adapter_id = str(getattr(provider, "adapter_id", "") or "")
    adapter_version = str(getattr(provider, "adapter_version", "") or "")
    provider_snapshot = (
        None
        if provider is None
        else ProviderLaunchSnapshot(
            provider_id,
            adapter_id,
            adapter_version,
            False,
            None,
        )
    )
    route_owned_tools = set(PRODUCT_ROUTE_OWNED_TOOL_NAMES)
    capabilities = frozenset(
        {
            *(set(deskpet_tool_registry_v2.list_tools()) - route_owned_tools),
            "workflow",
            "deep_research",
            "ppt_workflow",
            "durable_task",
        }
    )
    capability_hash = fingerprint_json(
        {
            "catalog_revision": deskpet_tool_registry_v2.catalog_snapshot().revision,
            "capabilities": sorted(capabilities),
        }
    )
    auth_epoch = 0
    if session_db is not None:
        auth_epoch = int(
            (await session_db.get_session_delivery_state(session_id)).get("epoch", 0)
        )
    write_scope_root = workspace
    companion = config.raw.get("companion") or {}
    if write_scope_root is None and bool(companion.get("write_scope_enforced", True)):
        from agent.write_scope import resolve_workspace_root
        from deskpet.execution.run_block_signals import (
            PreflightBlocked,
            RootBlockReasonV1,
        )

        try:
            write_scope_root = str(
                resolve_workspace_root(
                    configured=str(companion.get("workspace_root", ""))
                )
            )
        except (OSError, RuntimeError) as exc:
            if allow_workspace_unavailable:
                write_scope_root = None
            else:
                raise PreflightBlocked(
                    RootBlockReasonV1.WORKSPACE_UNAVAILABLE,
                    (
                        "workspace-host:"
                        + str(getattr(exc, "code", type(exc).__name__)),
                    ),
                ) from exc
    resolved_providers = tuple(
        providers or (() if provider is None else (provider,))
    )
    provider_bindings = tuple(
        (
            str(
                getattr(item, "provider_id", "")
                or getattr(item, "id", "")
                or type(item).__name__
            ),
            str(getattr(item, "model", "") or ""),
            str(getattr(item, "incarnation_id", "") or ""),
            int(getattr(item, "config_revision", 0) or 0),
            int(getattr(item, "binding_epoch", 0) or 0),
        )
        for item in resolved_providers
    )
    host = HostContext(
        session_id=session_id,
        principal_id=f"local:{session_id}",
        auth_epoch=auth_epoch,
        capability_hash=capability_hash,
        available_capabilities=capabilities,
        provider_plan=tuple(
            str(
                getattr(item, "provider_id", "")
                or getattr(item, "id", "")
                or type(item).__name__
            )
            for item in resolved_providers
        ),
        provider_bindings=provider_bindings,
        trace_id=f"chat:{uuid.uuid4().hex}",
        workspace=workspace,
        write_scope_root=write_scope_root,
    )
    return host, provider, providers, provider_snapshot


def _snapshot_context_usage_binding_for_run(host: Any) -> dict[str, Any]:
    """Read Context provenance from the immutable provider start snapshot."""

    bindings = tuple(getattr(host, "provider_bindings", ()) or ())
    if not bindings:
        return {}
    binding = bindings[0]
    if not isinstance(binding, (tuple, list)) or len(binding) < 5:
        # Context provenance must come from the typed frozen HostContext
        # binding.  Do not guess from provider_plan names or legacy pairs.
        return {}
    provider_id, model_id, _incarnation_id, _revision, binding_epoch = (
        binding[:5]
    )
    provider_id = str(provider_id or "").strip()
    model_id = str(model_id or "").strip()
    if not provider_id or not model_id:
        return {}
    return {
        "provider_id": provider_id,
        "preferred_model": model_id,
        "binding_epoch": int(binding_epoch or 0),
    }


async def _snapshot_context_usage_basis_for_run(
    session_db: Any, session_id: str
) -> str | None:
    """Freeze the exact measured sample a new durable Run may compact."""

    if session_db is None or not hasattr(
        session_db, "get_context_usage_state"
    ):
        return None
    authority = await session_db.get_context_usage_state(session_id)
    if not bool(authority.get("has_measurement")):
        return None
    return str(authority.get("sample_id") or "").strip() or None


async def _voice_run_host(session_id: str):
    host, _provider, _chain, _snapshot = await _issue_product_harness_host(
        session_id, workspace=None
    )
    return host


async def _cancel_product_harness_run(
    session_id: str,
    run_id: str,
    *,
    reason: str,
) -> bool:
    """Cancel one authenticated Run without consulting a process-local task map."""

    if not _harness_accepting or _harness_runtime is None or not run_id:
        return False
    host, _provider, _chain, _snapshot = await _issue_product_harness_host(
        session_id,
        workspace=None,
    )
    await _harness_runtime.run_client.cancel(
        {"run_id": run_id, "expected_session_id": session_id},
        host,
        reason,
    )
    provider_workload_router = service_context.get(
        "provider_workload_router"
    )
    if provider_workload_router is not None:
        await provider_workload_router.cancel_root(run_id)
    session_db = service_context.get("session_db")
    if session_db is not None:
        try:
            await session_db.clear_session_plan_awaiting(session_id)
        except Exception as exc:  # sidecar cleanup must not undo Run cancel
            logger.warning(
                "cancelled_run_plan_sidecar_clear_failed",
                session_id=session_id,
                run_id=run_id,
                error=str(exc),
            )
    return True


async def _product_harness_has_live_attached_child(
    session_id: str,
    run_id: str,
) -> bool:
    """Legacy diagnostic: report a non-terminal attached child Run.

    Active-budget enforcement no longer consults this presentation-layer
    helper. Durable waiting transitions pause the root budget directly, and
    RunKernel owns cancellation when the active budget is actually exhausted.
    """

    if not _harness_accepting or _harness_runtime is None or not run_id:
        return False
    from deskpet.execution.contracts import (
        AttachmentPolicy,
        RunRef,
        TERMINAL_RUN_STATUSES,
    )

    host, _provider, _chain, _snapshot = await _issue_product_harness_host(
        session_id,
        workspace=None,
    )
    actor = host.actor(root_run_id=run_id)
    parent_ref = RunRef(run_id, session_id)
    kernel = _harness_runtime.kernel
    links = await kernel._uow.list_child_links(parent_ref, actor)
    for link in links:
        if link.attachment_policy is AttachmentPolicy.DETACHED:
            continue
        child = await kernel._query(
            RunRef(link.child_run_id, session_id),
            actor,
        )
        if child.status not in TERMINAL_RUN_STATUSES:
            return True
    return False


async def _signal_product_harness_decision(
    session_id: str,
    payload: dict[str, Any],
    response: dict[str, Any],
):
    """Apply one fenced UI decision to the durable execution authority."""

    if not _harness_accepting or _harness_runtime is None:
        raise RuntimeError("Harness decision ingress is closed")
    run_id = str(payload.get("run_id") or "").strip()
    decision_id = str(
        payload.get("decision_id") or payload.get("request_id") or ""
    ).strip()
    nonce = str(payload.get("nonce") or "").strip()
    version = payload.get("version")
    if not run_id or not decision_id or not nonce or version is None:
        raise ValueError("decision response requires run_id/decision_id/nonce/version")
    host, _provider, _chain, _snapshot = await _issue_product_harness_host(
        session_id,
        workspace=None,
    )
    receipt = await _harness_runtime.run_client.signal(
        {"run_id": run_id, "expected_session_id": session_id},
        host,
        {
            "decision_id": decision_id,
            "nonce": nonce,
            "version": int(version),
            "response": response,
        },
    )
    _harness_runtime.reconciler.trigger()
    return receipt


async def _run_product_harness_continuation(
    websocket,
    text: str,
    session_id: str,
    *,
    root_run_id: str,
    task_scope_id: str,
    expected_boundary_version: int,
    request_id: str,
) -> None:
    """Append one user message to an existing nonterminal root Run."""

    workflow_service = service_context.get("workflow_service")
    execution_uow = getattr(workflow_service, "execution_uow", None)
    if (
        not _harness_accepting
        or _harness_runtime is None
        or execution_uow is None
    ):
        raise RuntimeError("Harness continuation ingress is closed")
    projection = await execution_uow.get_task_run_projection(root_run_id)
    boundary = await execution_uow.get_conversation_boundary(root_run_id)
    if (
        projection is None
        or boundary is None
        or projection.session_id != session_id
        or projection.task_scope_id != task_scope_id
        or boundary.session_id != session_id
        or boundary.task_scope_id != task_scope_id
    ):
        raise ValueError(
            "continuation root/task/session identity does not match"
        )
    message_ref = f"request:{request_id}"
    host, _provider, _chain, _snapshot = await _issue_product_harness_host(
        session_id,
        workspace=None,
    )
    receipt = await _harness_runtime.run_client.signal(
        {
            "run_id": root_run_id,
            "expected_session_id": session_id,
        },
        host,
        {
            "kind": "user_continuation",
            "task_scope_id": task_scope_id,
            "message_ref": message_ref,
            "content": text,
            "expected_boundary_version": expected_boundary_version,
        },
    )
    accepted = bool(
        receipt.get("accepted")
        if isinstance(receipt, dict)
        else getattr(receipt, "accepted", False)
    )
    if not accepted:
        reason = (
            receipt.get("reason")
            if isinstance(receipt, dict)
            else getattr(receipt, "reason", None)
        )
        raise RuntimeError(
            str(reason or "continuation was not accepted")
        )
    receipt_reason = (
        receipt.get("reason")
        if isinstance(receipt, dict)
        else getattr(receipt, "reason", "")
    )
    updated = await execution_uow.get_conversation_boundary(root_run_id)
    if updated is None:
        raise RuntimeError("accepted continuation lost its boundary")
    session_db = service_context.get("session_db")
    vector_worker = service_context.get("vector_worker")
    try:
        if session_db is not None:
            message_id = await session_db.append_message(
                session_id=session_id,
                role="user",
                content=text,
                workflow_event_id=(
                    f"user-continuation:{root_run_id}:{message_ref}"
                ),
                root_run_id=root_run_id,
                task_scope_id=task_scope_id,
            )
            if vector_worker is not None:
                await vector_worker.enqueue(message_id, text)
    except Exception as exc:  # noqa: BLE001
        # The execution boundary is canonical and was already committed.
        # The stable workflow_event_id lets any client retry rebuild this
        # derived history projection without duplicating the message.
        logger.warning(
            "continuation_history_projection_failed",
            root_run_id=root_run_id,
            task_scope_id=task_scope_id,
            error=str(exc),
        )
    await _broadcast_default_chat_peers(
        websocket,
        {
            "type": "chat_v2_user_echo",
            "payload": {
                "session_id": session_id,
                "text": text,
                "request_id": request_id,
                "run_id": root_run_id,
                "task_scope_id": task_scope_id,
                "conversation_boundary_ref": updated.boundary_ref,
                "conversation_boundary_version": updated.version,
            },
        },
    )
    accepted_event = {
        "type": "chat_v2_continuation_accepted",
        "payload": {
            "session_id": session_id,
            "request_id": request_id,
            "run_id": root_run_id,
            "task_scope_id": task_scope_id,
            "conversation_boundary_ref": updated.boundary_ref,
            "conversation_boundary_version": updated.version,
            "queued": receipt_reason == "continuation_queued",
        },
    }
    await websocket.send_json(accepted_event)
    await _broadcast_default_chat_peers(websocket, accepted_event)


def _launch_product_harness_continuation(*args, **kwargs) -> asyncio.Task:
    async def guarded() -> None:
        try:
            await _run_product_harness_continuation(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            websocket = args[0]
            session_id = str(
                args[2]
                if len(args) > 2
                else kwargs.get("session_id") or "default"
            )
            try:
                request_id = str(kwargs.get("request_id") or "")
                await _send_chat_error(
                    websocket,
                    {
                        "type": "chat_v2_error",
                        "payload": {
                            "error": str(exc) or type(exc).__name__,
                            "detail": type(exc).__name__,
                            "session_id": session_id,
                            "run_id": str(
                                kwargs.get("root_run_id") or ""
                            ),
                            "task_scope_id": str(
                                kwargs.get("task_scope_id") or ""
                            ),
                            "request_id": request_id,
                        },
                    },
                    session_id=session_id,
                    request_id=request_id,
                )
            except Exception:
                pass
            raise

    task = asyncio.create_task(guarded())

    def report_failure(done: asyncio.Task) -> None:
        if done.cancelled():
            return
        try:
            error = done.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.warning(
                "product_harness_continuation_failed", error=str(error)
            )

    task.add_done_callback(report_failure)
    return task


_WINDOWS_RESERVED_PROJECT_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _resolve_selected_project_directory(
    parent_directory: object,
    folder_name: object,
    *,
    allow_existing: bool = False,
) -> str:
    """Resolve a native-picker selection for a new or existing project."""

    parent_raw = str(parent_directory or "").strip()
    if not parent_raw:
        raise ValueError("project_directory_required")
    parent = Path(parent_raw).expanduser()
    if not parent.is_absolute():
        raise ValueError("project_parent_must_be_absolute")
    parent = parent.resolve(strict=False)
    if not parent.is_dir():
        raise ValueError("project_parent_not_directory")
    # In existing-project mode the native picker selects the project root
    # itself. The suggested folder name is presentation metadata only.
    if allow_existing:
        return str(parent)

    child = str(folder_name or "").strip()
    if not child:
        raise ValueError("project_directory_required")
    if (
        child in {".", ".."}
        or child.endswith((".", " "))
        or any(char in child for char in '<>:"/\\|?*')
        or any(ord(char) < 32 for char in child)
        or child.split(".", 1)[0].upper() in _WINDOWS_RESERVED_PROJECT_NAMES
    ):
        raise ValueError("project_folder_name_invalid")
    project_root = (parent / child).resolve(strict=False)
    try:
        project_root.relative_to(parent)
    except ValueError as exc:
        raise ValueError("project_directory_escaped_parent") from exc
    if project_root.exists():
        if not project_root.is_dir():
            raise ValueError("project_directory_is_file")
        try:
            if any(project_root.iterdir()):
                raise ValueError("project_directory_not_empty")
        except OSError as exc:
            raise ValueError("project_directory_unreadable") from exc
    return str(project_root)


async def _commit_product_preflight_block(
    *,
    websocket: Any,
    text: str,
    session_id: str,
    request_id: str,
    turn_id: str,
    task_scope_id: str,
    host: Any,
    block: Any,
) -> None:
    """Route a typed preflight failure through the trusted Kernel builder."""

    from deskpet.harness.kernel import root_run_identity

    if _harness_runtime is None:
        raise RuntimeError("Harness runtime is unavailable")
    handle = await _harness_runtime.run_client.start_blocked(
        {
            "text": text,
            "request_id": request_id,
            "turn_id": turn_id,
            "venue": "text",
            "payload": {
                "root_run_id": root_run_identity(
                    session_id, request_id, turn_id
                )[1].run_id,
                "task_scope_id": task_scope_id,
                "route_availability": "unavailable",
            },
        },
        host,
        reason_code=block.reason_code.value,
        evidence_refs=block.evidence_refs,
    )
    started = {
        "type": "chat_v2_run_started",
        "payload": {
            "session_id": session_id,
            "run_id": handle.run_id,
            "request_id": request_id,
            "turn_id": turn_id,
            "task_scope_id": task_scope_id,
            "projection_version": 0,
        },
    }
    await websocket.send_json(started)
    await _broadcast_default_chat_peers(websocket, started)
    _harness_runtime.reconciler.trigger()
    async for _event in handle.events:
        pass


async def _run_product_harness_chat(
    websocket,
    text: str,
    session_id: str,
    memory_policy_override=None,
    task_scope_explicit_new: bool = False,
    user_attachment_blocks=(),
    client_request_id: str | None = None,
    client_turn_id: str | None = None,
) -> None:
    """The single production chat ingress into ProductVenue/RunKernel."""

    from deskpet.execution.run_block_signals import (
        PreflightBlocked,
        RootBlockReasonV1,
    )

    if not _harness_accepting or _harness_venue is None:
        raise RuntimeError("Harness ingress is closed")
    session_db = service_context.get("session_db")
    vector_worker = service_context.get("vector_worker")
    user_message_id = None
    frozen_owner = None
    is_sentinel = (text or "").startswith("<<") and (text or "").endswith(">>")
    request_id = str(client_request_id or "").strip() or uuid.uuid4().hex
    turn_id = (
        str(client_turn_id or client_request_id or "").strip()
        or uuid.uuid4().hex
    )
    from deskpet.types.task_work_context import TaskWorkContextResolver
    from deskpet.harness.kernel import root_run_identity

    _, root_ref = root_run_identity(session_id, request_id, turn_id)
    task_scope_id = TaskWorkContextResolver.task_scope_id(
        session_id, request_id, turn_id
    )

    # Bind the client turn to its deterministic root Run before provider,
    # host, or Context preparation can block. The UI uses this reservation
    # to keep the new task visible and queues later steering until the
    # conversation boundary is ready.
    reserved = {
        "type": "chat_v2_run_reserved",
        "payload": {
            "session_id": session_id,
            "run_id": root_ref.run_id,
            "request_id": request_id,
            "turn_id": turn_id,
            "task_scope_id": task_scope_id,
            "projection_version": 0,
        },
    }
    reserved_send = websocket.send_json(reserved)
    if inspect.isawaitable(reserved_send):
        await reserved_send
    await _broadcast_default_chat_peers(websocket, reserved)

    venue = "text"
    workspace = None
    workflow_service = service_context.get("workflow_service")
    execution_uow = getattr(workflow_service, "execution_uow", None)
    if execution_uow is not None and hasattr(
        execution_uow, "get_latest_session_project_context"
    ):
        try:
            project_context = await execution_uow.get_latest_session_project_context(
                session_id
            )
            inherited_root = str(project_context.get("project_root") or "").strip()
            workspace = inherited_root or None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "session_project_context_resolve_failed",
                session_id=session_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
    try:
        host, provider, provider_chain, provider_snapshot = (
            await _issue_product_harness_host(
                session_id,
                workspace=workspace,
            )
        )
    except Exception as exc:
        from llm.resolution import SessionProviderUnavailable

        if isinstance(exc, PreflightBlocked):
            host, _provider, _chain, _snapshot = await _issue_product_harness_host(
                session_id,
                workspace=workspace,
                allow_workspace_unavailable=True,
            )
            await _commit_product_preflight_block(
                websocket=websocket,
                text=text,
                session_id=session_id,
                request_id=request_id,
                turn_id=turn_id,
                task_scope_id=task_scope_id,
                host=host,
                block=exc,
            )
            return
        if not isinstance(exc, SessionProviderUnavailable):
            raise
        host, _provider, _chain, _snapshot = await _issue_product_harness_host(
            session_id,
            workspace=workspace,
            allow_provider_unavailable=True,
        )
        await _commit_product_preflight_block(
            websocket=websocket,
            text=text,
            session_id=session_id,
            request_id=request_id,
            turn_id=turn_id,
            task_scope_id=task_scope_id,
            host=host,
            block=PreflightBlocked(
                RootBlockReasonV1.PROVIDER_BINDING_UNAVAILABLE,
                (
                    "provider-binding:"
                    + str(getattr(exc, "reason", type(exc).__name__)),
                ),
            ),
        )
        return
    frozen_session_binding = _snapshot_context_usage_binding_for_run(host)
    retry_handle = (
        await _harness_runtime.run_client.resume(request_id, turn_id, host)
        if turn_id and _harness_runtime is not None else None
    )
    frozen_context_usage_basis_sample_id = (
        None
        if retry_handle is not None
        else await _snapshot_context_usage_basis_for_run(
            session_db, session_id
        )
    )
    if retry_handle is None and session_db is not None and not is_sentinel:
        frozen_owner = _companion_identity_gate.freeze()
        # Semantic growth intent is interpreted by the active model inside
        # ProductTurnPreparer.  Ingress scheduling must not recreate a second
        # keyword/regex classifier.
        priority = "normal"
        user_message_id = (
            await session_db.append_user_message_with_growth_outbox(
                session_id,
                text,
                trusted_owner={
                    "profile_id": frozen_owner.owner.profile_id,
                    "profile_generation": (
                        frozen_owner.owner.profile_generation
                    ),
                    "binding_epoch": frozen_owner.binding_epoch,
                    "owner_kind": "companion_profile",
                },
                request_id=request_id,
                turn_id=turn_id,
                run_id=root_ref.run_id,
                priority=priority,
            )
        )
        if _companion_ingress_dispatcher is None:
            raise RuntimeError("Companion ingress dispatcher is unavailable")
        if vector_worker is not None:
            await vector_worker.enqueue(user_message_id, text)
        await _broadcast_default_chat_peers(
            websocket,
            {
                "type": "chat_v2_user_echo",
                "payload": {
                    "session_id": session_id, "text": text,
                    "request_id": request_id,
                    "turn_id": turn_id,
                    "run_id": root_ref.run_id,
                    "task_scope_id": task_scope_id,
                },
            },
        )
    session_context: dict[str, Any] = {"_session_id": session_id}
    if workspace is not None:
        session_context.update(
            _project_root=workspace,
            _write_scope_root=workspace,
        )
    else:
        session_context["_image_worker"] = service_context.get("image_worker")
        write_scope_root = host.write_scope_root
        if write_scope_root:
            session_context["_write_scope_root"] = write_scope_root
    deskpet_tool_registry_v2.set_session_context(session_id, session_context)

    turn = TurnInput(
        text=text,
        session_id=session_id,
        request_id=request_id,
        turn_id=turn_id,
        venue=venue,
        memory_policy=memory_policy_override,
        explicit_new=bool(task_scope_explicit_new),
        attachment_blocks=tuple(user_attachment_blocks),
        provider_ref=str(getattr(provider, "model", "") or "") or None,
        capability_ref=f"catalog:{deskpet_tool_registry_v2.catalog_snapshot().revision}",
        # ``workspace`` above is inherited from the Session's last confirmed
        # project.  Keep it on the trusted HostContext only.  Passing it as a
        # TurnInput workspace_ref incorrectly labels the inherited directory
        # as a fresh user selection, which prevents the native project picker
        # from rebinding this new root Run to another explicitly chosen path.
        workspace_ref=None,
        root_run_id=root_ref.run_id,
        task_scope_id=task_scope_id,
        active_execution_budget_seconds=_chat_turn_timeout_s(),
        context_usage_basis_sample_id=(
            frozen_context_usage_basis_sample_id
        ),
    )
    from deskpet.agent.product_domain_sink import LegacyProductDomainSink
    from deskpet.agent.run_presenter import RunPresentationContext

    activity = None
    context = RunPresentationContext(
        session_id=session_id,
        text=text,
        websocket=websocket,
        services=service_context,
        config=config,
        messages=[],
        session_db=session_db,
        vector_worker=vector_worker,
        activity_store=activity,
        provider_chain=provider_chain,
        fallback_provider=provider,
        request_id=request_id,
        max_iterations=50,
        is_sentinel=is_sentinel,
        broadcast=_broadcast_default_chat_peers,
        send_final=_send_chat_final,
        emit_context_usage=_emit_context_usage,
        intent_label_from_turn=_intent_label_from_turn,
        billing_ledger=billing_ledger,
        provider=provider,
        run_id=root_ref.run_id,
        task_scope_id=task_scope_id,
        provider_binding_epoch=int(
            frozen_session_binding.get("binding_epoch") or 0
        ),
        provider_binding_provider_id=(
            str(frozen_session_binding.get("provider_id") or "") or None
        ),
        provider_binding_model_id=(
            str(frozen_session_binding.get("preferred_model") or "") or None
        ),
    )
    sink = LegacyProductDomainSink(
        websocket=websocket,
        services=service_context,
        session_db=session_db,
        broadcast=_broadcast_default_chat_peers,
        plan_waiters={},
        tool_registry=deskpet_tool_registry_v2,
    )
    session = None
    try:
        outcome = await _harness_venue.open(
            turn,
            host,
            services=service_context.snapshot(),
            config=config,
            local_llm=local_llm,
            tool_registry=deskpet_tool_registry_v2,
            provider=provider,
            current_message_id=user_message_id,
            companion_ingress_owner=(
                frozen_owner if user_message_id is not None else None
            ),
            summary_user_is_confused=_sql_user_is_confused,
            summary_latest_task_snapshot=_sql_latest_task_snapshot,
            summary_build_reinject_msg=_sql_build_reinject_msg,
            presentation_context=context,
            domain_sink=sink,
            provider_launch_snapshot=provider_snapshot,
        )
        if _harness_runtime is not None:
            _harness_runtime.reconciler.trigger()
        from deskpet.harness.adapters.venues import ProductVenueRunResult

        if isinstance(outcome, ProductVenueRunResult):
            return
        session = outcome
        started = {
            "type": "chat_v2_run_started",
            "payload": {
                "session_id": session_id, "run_id": session.run_id,
                "request_id": request_id, "turn_id": turn_id,
                "task_scope_id": task_scope_id,
                "conversation_boundary_ref": (
                    context.conversation_boundary_ref
                ),
                "conversation_boundary_version": 1,
                "projection_version": 0,
            },
        }
        await websocket.send_json(started)
        await _broadcast_default_chat_peers(websocket, started)
        async for _event in session.events:
            pass
    except PreflightBlocked as block:
        await _commit_product_preflight_block(
            websocket=websocket,
            text=text,
            session_id=session_id,
            request_id=request_id,
            turn_id=turn_id,
            task_scope_id=task_scope_id,
            host=host,
            block=block,
        )
        return
    except asyncio.CancelledError:
        # A WebSocket disconnect only detaches presentation. The durable Run
        # remains owned by Kernel and can be resumed with the same client ids.
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "product_harness_chat_failed sid=%s error=%s",
            session_id,
            str(exc),
            exc_info=True,
            traceback=traceback.format_exc(),
        )
        try:
            await _send_chat_error(
                websocket,
                {
                    "type": "chat_v2_error",
                    "payload": {
                        "error": str(exc) or type(exc).__name__,
                        "detail": type(exc).__name__,
                        "session_id": session_id,
                        "run_id": root_ref.run_id,
                        "task_scope_id": task_scope_id,
                        "request_id": request_id,
                    },
                },
                session_id=session_id,
                request_id=request_id,
            )
        except Exception:
            pass
    finally:
        if session is not None:
            await session.close()
        if (
            user_message_id is not None
            and frozen_owner is not None
            and _companion_ingress_dispatcher is not None
        ):
            try:
                # Idempotent failure fallback. A prior model promotion is
                # monotonic and cannot be downgraded by this "none" settle.
                await _companion_ingress_dispatcher.settle_semantic_intent(
                    session_id=session_id,
                    message_id=user_message_id,
                    request_id=request_id,
                    turn_id=turn_id,
                    owner=frozen_owner,
                    growth_signal_kind="none",
                )
            except Exception as exc:
                logger.warning(
                    "companion_ingress_fallback_settle_failed",
                    session_id=session_id,
                    message_id=user_message_id,
                    error=str(exc),
                )


async def _run_product_harness_chat_with_timeout(*args, **kwargs) -> None:
    """Compatibility wrapper; durable Run authority owns active-time expiry."""

    bound = inspect.signature(_run_product_harness_chat).bind(*args, **kwargs)
    bound.apply_defaults()
    request_id = str(
        bound.arguments.get("client_request_id") or ""
    ).strip() or uuid.uuid4().hex
    turn_id = str(
        bound.arguments.get("client_turn_id") or ""
    ).strip() or uuid.uuid4().hex
    bound.arguments["client_request_id"] = request_id
    bound.arguments["client_turn_id"] = turn_id
    await _run_product_harness_chat(*bound.args, **bound.kwargs)


def _launch_product_harness_chat(*args, **kwargs) -> asyncio.Task:
    """Launch one presentation consumer; RunKernel owns run/task cancellation."""

    async def guarded() -> None:
        try:
            await _run_product_harness_chat_with_timeout(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            websocket = args[0]
            session_id = str(
                args[2]
                if len(args) > 2
                else kwargs.get("session_id") or "default"
            )
            request_id = str(kwargs.get("client_request_id") or "")
            turn_id = str(
                kwargs.get("client_turn_id")
                or kwargs.get("client_request_id")
                or ""
            )
            run_id = ""
            task_scope_id = ""
            if request_id and turn_id:
                try:
                    from deskpet.harness.kernel import root_run_identity
                    from deskpet.types.task_work_context import TaskWorkContextResolver

                    _, root_ref = root_run_identity(session_id, request_id, turn_id)
                    run_id = root_ref.run_id
                    task_scope_id = TaskWorkContextResolver.task_scope_id(
                        session_id, request_id, turn_id
                    )
                except Exception:  # pragma: no cover - terminal fallback only
                    pass
            try:
                await _send_chat_error(
                    websocket,
                    {
                        "type": "chat_v2_error",
                        "payload": {
                            "error": str(exc) or type(exc).__name__,
                            "detail": type(exc).__name__,
                            "session_id": session_id,
                            "run_id": run_id,
                            "task_scope_id": task_scope_id,
                            "request_id": request_id,
                        },
                    },
                    session_id=session_id,
                    request_id=request_id,
                )
            except Exception:
                pass
            raise

    task = asyncio.create_task(guarded())

    def report_failure(done: asyncio.Task) -> None:
        if done.cancelled():
            return
        try:
            error = done.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.warning("product_harness_transport_task_failed", error=str(error))

    task.add_done_callback(report_failure)
    return task


async def _enqueue_auto_resume_hint(sid: str, messages: list[dict]) -> None:
    content = next((str(item.get("content") or "") for item in reversed(messages)
                    if item.get("_is_supervisor_hint")), "")
    text = content.removeprefix("[Supervisor Hint] ")
    queue = service_context.get("nudge_queue")
    if not text or queue is None:
        return
    try:
        from agent.nudge_queue import Hint
        await queue.push(sid, Hint(text=text, alert_id="auto_resume", severity="yellow"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("auto_resume_hint_push_failed sid=%s err=%s", sid, exc)


async def _activate_product_harness() -> None:
    global _harness_accepting, _harness_runtime, _harness_venue

    from deskpet.workflows.store import RuntimeActivationCommand

    workflow_service = service_context.get("workflow_service")
    if workflow_service is None:
        raise RuntimeError("workflow service is required for Harness activation")
    uow = workflow_service.execution_uow

    _harness_accepting = False
    await uow.initialize()
    state = await uow.get_runtime_state()
    if state.phase == "legacy":
        state = await uow.activate_runtime(command=RuntimeActivationCommand.advance())
    if state.phase == "draining":
        while lease := await uow.activate_runtime(command=RuntimeActivationCommand.claim(
            f"deskpet:{os.getpid()}", lease_seconds=30.0
        )):
            try:
                ref = lease.ref
                launcher = getattr(workflow_service, "launcher", None)
                if ref.source_kind == "workflow_run" and launcher is not None:
                    await launcher.recover_pending(only_run_ids={ref.source_run_id})
                if ref in await uow.scan_legacy_drain_manifest():
                    raise RuntimeError(
                        f"legacy durable run is still active: {ref.source_kind}/{ref.source_run_id}"
                    )
            except BaseException as exc:
                await uow.activate_runtime(command=RuntimeActivationCommand.settle(
                    lease, error=f"{type(exc).__name__}: {exc}"
                ))
                raise
            if await uow.activate_runtime(
                command=RuntimeActivationCommand.settle(lease)
            ) is not True:
                raise RuntimeError("legacy drain lease was lost before settlement")
        state = await uow.activate_runtime(command=RuntimeActivationCommand.advance())
    if state.phase not in {"activated", "open"}:
        raise RuntimeError(f"unsupported activation phase: {state.phase}")
    stack = await _build_product_harness_stack(state.generation)
    if state.phase == "activated":
        state = await uow.activate_runtime(command=RuntimeActivationCommand.advance())
    _harness_runtime, _harness_venue = stack
    workflow_service.bind_execution_delivery_wakeup(
        _harness_runtime.reconciler.trigger
    )
    logger.info(
        "product_harness_ready_ingress_closed generation=%d phase=%s",
        state.generation,
        state.phase,
    )


async def _activate_companion_runtime_adapter_and_open_ingress() -> None:
    """Bind background Runs before opening the sole product ingress."""

    global _harness_accepting
    if _harness_runtime is None:
        raise RuntimeError("product Harness is unavailable")
    runtime = service_context.get("companion_runtime")
    growth_pipeline = service_context.get("companion_growth_pipeline")
    store = globals().get("_companion_store")
    if runtime is None or store is None or growth_pipeline is None:
        raise RuntimeError("Companion runtime composition is unavailable")

    from deskpet.companion.run_adapter import BackgroundRunAdapter

    async def _background_host(owner, claim):
        from dataclasses import replace
        from deskpet.execution.contracts import fingerprint_json

        session_id = (
            f"companion:{owner.profile_id}:{owner.profile_generation}:"
            f"{claim.item_id}"
        )
        host, _provider, _chain, _snapshot = await _issue_product_harness_host(
            session_id,
            workspace=None,
        )
        zero_capability_hash = fingerprint_json(
            {
                "catalog_revision": (
                    deskpet_tool_registry_v2.catalog_snapshot().revision
                ),
                "capabilities": [],
            }
        )
        return replace(
            host,
            capability_hash=zero_capability_hash,
            available_capabilities=frozenset(),
        )

    runtime.handler = BackgroundRunAdapter(
        client=_harness_runtime.run_client,
        store=store,
        host_factory=_background_host,
        prepared_context_factory=growth_pipeline.prepared_context,
        text_factory=growth_pipeline.prompt,
        result_postprocessor=growth_pipeline.postprocess,
    )
    _harness_accepting = True
    logger.info(
        "companion_runtime_adapter_ready product_ingress=open phase=%s",
        _growth_authority_router.current.phase.value,
    )


async def _attach_workflow_history_events(
    *,
    rows: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    session_id: str,
    session_db: Any,
    workflow_service: Any,
) -> None:
    """Attach fenced durable envelopes to SessionDB history messages in place."""

    event_ids = list(
        dict.fromkeys(
            str(row.get("workflow_event_id") or "").strip()
            for row in rows
            if str(row.get("workflow_event_id") or "").strip()
        )
    )
    if not event_ids or workflow_service is None:
        return
    delivery_state = await session_db.get_session_delivery_state(session_id)
    if delivery_state.get("deleted_at") is not None:
        return

    # New production rows live only in execution_events.  Hydrate those first
    # and use the legacy workflow service solely as a compatibility fallback
    # for old SessionDB references that predate Kernel activation.
    from deskpet.execution.contracts import EventNotFound
    from deskpet.workflows.harness_delivery import workflow_event_envelope

    events_by_id: dict[str, dict[str, Any]] = {}
    missing_event_ids: list[str] = []
    execution_uow = getattr(workflow_service, "execution_uow", None)
    for event_id in event_ids:
        if execution_uow is None:
            missing_event_ids.append(event_id)
            continue
        try:
            canonical_event = await execution_uow.get_event(event_id)
        except EventNotFound:
            missing_event_ids.append(event_id)
            continue
        if canonical_event.session_id != session_id:
            continue
        envelope = workflow_event_envelope(canonical_event)
        if envelope.get("event_type") == "workflow.decision":
            payload = envelope.get("payload")
            decision_id = str((payload or {}).get("decision_id") or "")
            if decision_id:
                decision = await execution_uow.get_decision_projection(
                    decision_id,
                    expected_run_id=canonical_event.run_id,
                    expected_session_id=session_id,
                )
                if isinstance(payload, dict):
                    payload["status"] = decision.status.value
                    payload["version"] = decision.decision_version
        events_by_id[event_id] = envelope

    # The service deliberately caps one hydration request at 100 ids.  Chat
    # sessions can legitimately contain more than that, especially now that a
    # DeepResearch run publishes one durable event per visible stage.  Hydrate
    # in bounded batches instead of rejecting the entire session history.
    hydrated_events: list[dict[str, Any]] = []
    for offset in range(0, len(missing_event_ids), 100):
        hydrated_page = await workflow_service.hydrate_session_history_event_ids(
            session_id,
            missing_event_ids[offset : offset + 100],
            current_session_epoch=int(delivery_state.get("epoch", 0)),
            session_deleted=False,
        )
        hydrated_events.extend(hydrated_page.get("events", []))
    events_by_id.update({
        str(event.get("event_id") or ""): event
        for event in hydrated_events
    })
    human_store = getattr(workflow_service, "human_store", None)
    for event in events_by_id.values():
        if event.get("event_type") != "workflow.decision" or human_store is None:
            continue
        payload = event.get("payload")
        decision_id = str((payload or {}).get("decision_id") or "")
        if not decision_id:
            continue
        decision = await human_store.get_decision(decision_id)
        if decision is not None and isinstance(payload, dict):
            run = await workflow_service.run_store.get_run(decision.run_id)
            payload["status"] = (
                decision.status.value
                if run is not None and str(run.get("status") or "") == "waiting"
                else str((run or {}).get("status") or "unavailable")
            )
    for message in messages:
        event = events_by_id.get(str(message.get("id") or ""))
        if event is not None:
            message["workflow_event"] = event


async def _attach_companion_history_events(
    *,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply the current CompanionStore owner overlay before exposing history."""

    if _companion_notification_service is None:
        return []
    try:
        frozen = _companion_identity_gate.freeze()
    except Exception:
        return []
    try:
        if not _companion_notification_service.history_ready(frozen):
            await _companion_notification_service.bind_and_drain(frozen)
        return await _companion_notification_service.visibility.project_history(
            frozen, rows
        )
    except Exception as exc:  # noqa: BLE001
        # Companion is fail-closed, while the caller still returns ordinary
        # conversation/workflow messages from SessionDB.
        logger.warning(
            "companion_history_overlay_closed",
            error=type(exc).__name__,
        )
        return []


async def _bind_companion_inbox_route(
    session_id: str,
    frozen_identity: Any,
    *,
    create_if_absent: bool,
) -> bool:
    """Bind a new empty task session and make it the current profile inbox."""

    session_db = service_context.get("session_db")
    if session_db is None:
        return False
    from deskpet.memory.companion_message_projection import (
        TrustedCompanionOwner,
    )

    trusted_owner = TrustedCompanionOwner(
        profile_id=frozen_identity.owner.profile_id,
        profile_generation=frozen_identity.owner.profile_generation,
        binding_epoch=frozen_identity.binding_epoch,
    )
    try:
        if create_if_absent:
            await session_db.ensure_session(session_id)
            await session_db.bind_session_owner_if_absent(
                session_id, trusted_owner
            )
        await session_db.set_companion_default_route(
            trusted_owner.profile_id,
            trusted_owner.profile_generation,
            trusted_owner.binding_epoch,
            session_id,
        )
    except RuntimeError as exc:
        if str(exc) in {
            "companion_session_missing",
            "companion_legacy_session_cannot_be_claimed",
            "companion_route_target_fence_mismatch",
        }:
            return False
        raise
    if _companion_notification_service is not None:
        await _companion_notification_service.bind_and_drain(frozen_identity)
    return True


async def _ensure_companion_inbox_route(
    frozen_identity: Any,
) -> str:
    """Return one owner-fenced inbox without claiming legacy history."""

    session_db = service_context.get("session_db")
    if session_db is None:
        raise RuntimeError("companion_session_store_unavailable")
    owner = frozen_identity.owner
    route = await session_db.get_companion_projection_route(
        owner.profile_id,
        owner.profile_generation,
        frozen_identity.binding_epoch,
    )
    if route is not None:
        session_id = str(route.get("target_session_id") or "")
        if session_id and await _bind_companion_inbox_route(
            session_id,
            frozen_identity,
            create_if_absent=False,
        ):
            return session_id
    for _attempt in range(3):
        session_id = str(uuid.uuid4())
        if await _bind_companion_inbox_route(
            session_id,
            frozen_identity,
            create_if_absent=True,
        ):
            return session_id
    raise RuntimeError("companion_inbox_route_unavailable")


def _is_companion_history_session_id(
    session_id: str,
) -> bool:
    """Return whether a session id should be shown in the message panel list."""
    sid = (session_id or "").strip()
    if sid == "default":
        return True
    # Legacy task ids remain visible so old history can still be renamed,
    # copied, or deleted. Newly generated task sessions are opaque UUIDs.
    if sid.startswith("task-"):
        return True
    try:
        uuid.UUID(sid)
        return True
    except (TypeError, ValueError, AttributeError):
        return False


# 2026-05-28 — per-session context-usage snapshot for the frontend Claude-Code-
# style ring gauge. Keyed by chat session_id (e.g. "default", "code-XXX").
# Updated on every successful LLM turn; pushed via ``context_usage`` ws event.
_session_context_state: dict[str, dict[str, Any]] = {}


def _cache_context_usage_state(payload: dict[str, Any]) -> dict[str, Any]:
    """Cache only a newer durable ContextUsageStateV2 version."""

    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return payload
    if int(payload.get("version") or 0) <= 0:
        return _session_context_state.get(session_id) or payload
    previous = _session_context_state.get(session_id)
    incoming_version = int(payload.get("version") or 0)
    previous_version = int((previous or {}).get("version") or 0)
    if previous is None or incoming_version >= previous_version:
        _session_context_state[session_id] = dict(payload)
        return payload
    return previous


async def _read_context_usage_authority(session_id: str) -> dict[str, Any]:
    """Read the durable Session authority; never fabricate a global stub."""

    context_sdb = service_context.get("session_db")
    if context_sdb is None or not hasattr(
        context_sdb, "get_context_usage_state"
    ):
        raise RuntimeError("context_usage_authority_unavailable")
    authoritative = await context_sdb.get_context_usage_state(session_id)
    return _cache_context_usage_state(authoritative)


async def _send_context_usage_authority(
    websocket: Any,
    session_db: Any,
    session_id: str,
) -> dict[str, Any]:
    """Send the latest durable Context Usage authority to one UI peer."""

    authoritative = await session_db.get_context_usage_state(session_id)
    authoritative = _cache_context_usage_state(authoritative)
    await websocket.send_json({
        "type": "context_usage",
        "payload": authoritative,
    })
    return authoritative


def _snapshot_context_usage_event(
    session_id: str,
    *,
    provider_chain: Any | None,
    fallback_provider: Any | None = None,
) -> dict[str, Any] | None:
    """Pick the latest ``last_usage`` off the provider chain that actually
    served the last turn and combine it with the resolved ModelContextInfo
    (window / effective ceiling / compact threshold) into a single payload
    the frontend renders as a ring gauge.

    Returns ``None`` when no usage is available yet (don't emit).
    """
    try:
        from llm.model_info import resolve as _resolve_model
    except Exception:  # noqa: BLE001
        _resolve_model = None  # type: ignore[assignment]

    # Read Context OS attempts before inspecting legacy provider state.  Some
    # adapters (including the strict E2E seam) intentionally do not expose a
    # mutable ``last_usage`` attribute, while still reporting authoritative
    # usage through ContextAttemptStore.
    try:
        _attempt_store = service_context.get("context_attempt_store")
    except Exception:
        _attempt_store = None
    _attempts: list[dict[str, Any]] = []
    _latest_attempt: dict[str, Any] | None = None
    if _attempt_store is not None:
        try:
            _attempts = _attempt_store.public_for_session(session_id)
            _latest_attempt = next(
                (
                    attempt
                    for attempt in reversed(_attempts)
                    if attempt.get("purpose") == "agent_response"
                    and attempt.get("state") == "succeeded"
                ),
                None,
            )
        except Exception:  # noqa: BLE001
            _attempts = []
            _latest_attempt = None

    # Pick first provider with non-None last_usage.
    _picked = None
    _candidates: list[Any] = []
    if provider_chain:
        try:
            _candidates.extend(list(provider_chain))
        except Exception:  # noqa: BLE001
            pass
    if fallback_provider is not None:
        _candidates.append(fallback_provider)
    for _p in _candidates:
        _u = getattr(_p, "last_usage", None)
        if _u:
            _picked = _p
            break
    if _picked is None and _latest_attempt is None:
        return None
    usage = getattr(_picked, "last_usage", None) or {} if _picked is not None else {}
    model = getattr(_picked, "model", "") or "" if _picked is not None else ""
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    cached_tokens = 0
    # cached_tokens may be nested under prompt_tokens_details (OpenAI) or
    # at the top level (deepseek/some relays).
    _details = usage.get("prompt_tokens_details") or {}
    if isinstance(_details, dict):
        cached_tokens = int(_details.get("cached_tokens") or 0)
    if not cached_tokens:
        cached_tokens = int(usage.get("cached_tokens") or 0)

    # Context OS is the authoritative source for the actual attempt.  A
    # provider chain may retain ``last_usage`` from an older/fallback provider,
    # which previously made the header ring disagree with ContextTrace (for
    # example, showing the configured 380k model while the request actually ran
    # against an 8k fixture model).  Prefer the latest successful agent attempt
    # for model/window/usage while retaining the provider snapshot as a legacy
    # fallback when Context OS is disabled.
    if _latest_attempt is not None:
        model = str(_latest_attempt.get("model_id") or model)
        if _latest_attempt.get("actual_input_tokens") is not None:
            prompt_tokens = int(_latest_attempt["actual_input_tokens"])
        if _latest_attempt.get("actual_output_tokens") is not None:
            completion_tokens = int(_latest_attempt["actual_output_tokens"])
        if _latest_attempt.get("actual_cache_read_tokens") is not None:
            cached_tokens = int(_latest_attempt["actual_cache_read_tokens"])

    # Resolve model context window + thresholds.
    context_window = 32_000
    effective_pct = 0.95
    compact_at_pct = 0.70
    recall_sweet = 16_000
    if _resolve_model is not None and model:
        try:
            _info = _resolve_model(model)
            context_window = int(_info.context_window)
            effective_pct = float(_info.effective_pct)
            compact_at_pct = min(float(_info.compact_at_pct), 0.70)
            recall_sweet = int(_info.recall_sweet_tokens)
        except Exception:  # noqa: BLE001
            pass
    if _latest_attempt is not None and int(_latest_attempt.get("context_window") or 0) > 0:
        context_window = int(_latest_attempt["context_window"])
    payload = {
        "session_id": session_id,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
        "context_window": context_window,
        "effective_ceiling": int(context_window * effective_pct),
        "compact_at": int(context_window * compact_at_pct),
        "recall_sweet": recall_sweet,
        "updated_at": time.time(),
    }
    if _attempts:
        payload["attempts"] = _attempts
    return {"type": "context_usage", "payload": payload}


async def _emit_context_usage(
    originator_ws: WebSocket,
    session_id: str,
    *,
    provider_chain: Any | None,
    fallback_provider: Any | None = None,
    root_run_id: str | None = None,
    request_id: str | None = None,
    binding_epoch: int | None = None,
    frozen_provider_id: str | None = None,
    frozen_model_id: str | None = None,
) -> None:
    """Snapshot usage and push to originator + (for "default" session) peers.
    Best-effort: any error is logged at debug and swallowed."""
    try:
        msg = _snapshot_context_usage_event(
            session_id,
            provider_chain=provider_chain,
            fallback_provider=fallback_provider,
        )
        if msg is None:
            return
        payload = dict(msg.get("payload") or {})
        session_db = service_context.get("session_db")
        if session_db is None or not hasattr(
            session_db, "record_context_usage_sample"
        ):
            return
        if session_db is not None:
            attempts = payload.get("attempts") or []
            latest_attempt = (
                attempts[-1]
                if isinstance(attempts, list)
                and attempts
                and isinstance(attempts[-1], dict)
                else {}
            )
            try:
                binding = (
                    await session_db.get_session_provider_binding(session_id)
                    if binding_epoch is None
                    else {}
                )
                stable_request_id = str(
                    latest_attempt.get("request_id") or request_id or ""
                ).strip()
                stable_attempt_id = str(
                    latest_attempt.get("attempt_id") or "legacy"
                ).strip()
                if not stable_request_id:
                    raise ValueError("context usage provider sample lacks request identity")
                await session_db.record_context_usage_sample(
                    {
                        "source_event_id": (
                            "context-attempt:"
                            f"{root_run_id or stable_request_id}:"
                            f"{stable_request_id}:{stable_attempt_id}"
                        ),
                        "session_id": session_id,
                        "run_id": root_run_id,
                        "request_id": stable_request_id,
                        "attempt_id": stable_attempt_id,
                        "event_type": "provider_attempt",
                        "binding_epoch": (
                            binding.get("binding_epoch", 0)
                            if binding_epoch is None
                            else binding_epoch
                        ),
                        "provider_id": (
                            latest_attempt.get("provider_id")
                            or frozen_provider_id
                            or binding.get("provider_id")
                        ),
                        "model_id": (
                            payload.get("model", "") or frozen_model_id
                        ),
                        "tokens_after": payload.get("prompt_tokens", 0),
                        "prompt_tokens": payload.get("prompt_tokens", 0),
                        "completion_tokens": payload.get("completion_tokens", 0),
                        "cached_tokens": payload.get("cached_tokens", 0),
                        "context_window": payload.get("context_window", 0),
                        "effective_ceiling": payload.get(
                            "effective_ceiling", 0
                        ),
                        "estimate_method": (
                            "provider_usage"
                            if latest_attempt.get("actual_input_tokens")
                            is not None
                            else "legacy_provider_usage"
                        ),
                        "completed_at": payload.get("updated_at", time.time()),
                        "created_at": payload.get("updated_at", time.time()),
                        "metadata": {
                            "model": payload.get("model", ""),
                            "cached_tokens": payload.get("cached_tokens", 0),
                            "completion_tokens": payload.get(
                                "completion_tokens", 0
                            ),
                            "compression_source": latest_attempt.get(
                                "compression_source", ""
                            ),
                            "compression_model": latest_attempt.get(
                                "actual_compression_model", ""
                            ),
                        },
                    }
                )
                payload = await session_db.get_context_usage_state(session_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "context_usage_history_persist_failed sid=%s err=%s",
                    session_id,
                    exc,
                )
                try:
                    payload = await session_db.get_context_usage_state(session_id)
                except Exception as read_exc:  # noqa: BLE001
                    logger.debug(
                        "context_usage_authority_read_failed sid=%s err=%s",
                        session_id,
                        read_exc,
                    )
                    return
        payload = _cache_context_usage_state(payload)
        msg = {"type": "context_usage", "payload": payload}
        try:
            await originator_ws.send_json(msg)
        except Exception as exc:  # noqa: BLE001
            logger.debug("context_usage_send_originator_failed err=%s", exc)
        await _broadcast_default_chat_peers(originator_ws, msg)
    except Exception as exc:  # noqa: BLE001
        logger.debug("context_usage_emit_failed sid=%s err=%s", session_id, exc)


# superpowers Layer 1B WI-A1 — 意图记忆纯函数（module 顶层，便于单测直接 import）。
# 注入侧 hint 文案 + record 侧 label 判定从主循环抽出，使得行为可被
# ``from main import _build_intent_hint, _intent_label_from_turn`` 单测。
_INTENT_HINT_ASK = (
    "[偏好记忆] 用户以往把这类消息当纯提问处理——"
    "直接回答，不要调用工具修改任何东西。"
)
_INTENT_HINT_TASK = (
    "[偏好记忆] 用户以往把这类消息当派活——"
    "按工作流先澄清/计划再执行。"
)


def _build_intent_hint(label: str) -> str | None:
    """意图记忆注入侧：把匹配到的意图 label 映射成 system hint 文案。

    - ``"ask"`` → 纯提问 hint（直接回答别动手）。
    - ``"task"`` → 派活 hint（进工作流先澄清/计划）。
    - 其它（含 None/未知）→ None（不注入，守"出厂字节级不变"）。
    """
    if label == "ask":
        return _INTENT_HINT_ASK
    if label == "task":
        return _INTENT_HINT_TASK
    return None


def _intent_label_from_turn(had_tool_call: bool) -> str:
    """意图记忆 record 侧：本轮真调过工具 → "task"，否则纯回答 → "ask"。"""
    return "task" if had_tool_call else "ask"


def _approx_tokens(text: str | None) -> int:
    """委托 tokens.count_text_tokens（CJK-aware）做统一口径估算。

    全后端 token 计数走唯一入口 ``deskpet.agent.tokens.count_text_tokens``
    （CJK×4 加权，可选 tiktoken，启发式回落），消除散落口径不一致。
    用于驱动 "where did my context go" 饼图；权威值仍是 LLM 自己的
    ``prompt_tokens``（已在 ring 里）。Returns 0 on falsy input."""
    if not text:
        return 0
    from deskpet.agent.tokens import count_text_tokens
    return count_text_tokens(text)


async def _compute_context_breakdown(session_id: str) -> dict[str, Any]:
    """Estimate the breakdown of "what's in my context right now" for the
    given session_id. Drives the ContextBreakdownModal that opens when the
    user clicks the ring gauge.

    Returns ``{session_id, sections: [{kind, label, tokens, preview, count?}],
    total_estimated_tokens, last_usage_prompt_tokens, model, ts}``.

    Sections (Claude-Code parity):
      * ``system``      — system prompt boilerplate
      * ``memory``      — recalled facts / persona (RAG injection)
      * ``tools``       — tool definitions in context
      * ``history``     — persisted user/assistant turns (recent N)
      * ``current``     — none for now (the next user input lands at turn-time)
    """
    sections: list[dict[str, Any]] = []

    # 1) Persona / system prompt — pull from the same _resolve_persona the
    # ContextAssembler uses. This is the frozen header injected at the top
    # of every turn.
    persona_text = ""
    try:
        from deskpet.agent.assembler.components.persona import _resolve_persona  # type: ignore[attr-defined]
        persona_text = _resolve_persona(config.raw if hasattr(config, "raw") else {}) or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("breakdown_persona_probe_failed err=%s", exc)
    sections.append({
        "kind": "system",
        "label": "Persona / system",
        "tokens": _approx_tokens(persona_text),
        "preview": persona_text[:400],
    })

    # 2) Memory — recalled facts (subset of what ContextAssembler injects).
    mem_preview = ""
    mem_count = 0
    mem_total_tokens = 0
    try:
        fs = service_context.get("facts_store")
        if fs is not None and hasattr(fs, "list_active"):
            _facts = await fs.list_active(limit=50)  # returns list[dict]
            mem_count = len(_facts)
            preview_lines = []
            for f in _facts:
                cat = f.get("category", "?") or "?"
                subj = f.get("subject", "") or ""
                val = f.get("value", "") or ""
                mem_total_tokens += _approx_tokens(cat) + _approx_tokens(subj) + _approx_tokens(val)
                if len(preview_lines) < 8:
                    preview_lines.append(f"- [{cat}] {subj}: {val[:60]}")
            mem_preview = "\n".join(preview_lines)
    except Exception as exc:  # noqa: BLE001
        logger.debug("breakdown_memory_probe_failed err=%s", exc)
    sections.append({
        "kind": "memory",
        "label": "Memory / facts",
        "tokens": mem_total_tokens,
        "preview": mem_preview[:400],
        "count": mem_count,
    })

    # 3) Tools — count + names from the v2 schema-aware registry (the
    # one the chat_v2 agent loop actually uses). Singleton imported
    # directly; the legacy ``tool_router`` only carries 3 hello-world tools.
    tool_preview = ""
    tool_count = 0
    try:
        from deskpet.tools.registry import registry as _v2reg
        if _v2reg is not None and hasattr(_v2reg, "list_tools"):
            try:
                _names = _v2reg.list_tools()
            except Exception:  # noqa: BLE001
                _names = []
            tool_count = len(_names)
            tool_preview = ", ".join(str(n) for n in _names[:40])
    except Exception as exc:  # noqa: BLE001
        logger.debug("breakdown_tools_probe_failed err=%s", exc)
    sections.append({
        "kind": "tools",
        "label": "Tool definitions",
        "tokens": tool_count * 60,  # ~60 tokens per tool schema, rough
        "preview": tool_preview[:400],
        "count": tool_count,
    })

    # 4) History — recent user/assistant messages from SessionDB.
    # SessionDB API: get_recent(session_id, limit=N) → list[ConversationTurn].
    hist_text = ""
    hist_count = 0
    hist_tokens = 0
    try:
        sdb = service_context.get("session_db")
        if sdb is not None and hasattr(sdb, "get_recent"):
            try:
                turns = await sdb.get_recent(session_id, limit=200)
            except Exception:  # noqa: BLE001
                turns = []
            hist_count = len(turns)
            parts = []
            for t in turns:
                role = getattr(t, "role", "?")
                content = getattr(t, "content", "") or ""
                hist_tokens += _approx_tokens(content)
                if len(parts) < 12:
                    parts.append(f"[{role}] {str(content)[:80]}")
            hist_text = "\n".join(parts)
    except Exception as exc:  # noqa: BLE001
        logger.debug("breakdown_history_probe_failed err=%s", exc)
    sections.append({
        "kind": "history",
        "label": "Conversation history",
        "tokens": hist_tokens,
        "preview": hist_text[:400],
        "count": hist_count,
    })

    # 5) Durable size/compaction history for the Context chart.
    history: list[dict[str, Any]] = []
    try:
        sdb = service_context.get("session_db")
        if sdb is not None and hasattr(sdb, "list_context_usage_history"):
            history = await sdb.list_context_usage_history(
                session_id, limit=512
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("breakdown_history_samples_failed err=%s", exc)

    # 6) Resolve the latest durable project/workspace identity. Historical
    # chat sessions may predate project_name/project_root session metadata.
    project_context: dict[str, Any] = {}
    try:
        workflow_service = service_context.get("workflow_service")
        execution_uow = getattr(workflow_service, "execution_uow", None)
        if execution_uow is not None and hasattr(
            execution_uow, "get_latest_session_project_context"
        ):
            project_context = dict(
                await execution_uow.get_latest_session_project_context(
                    session_id
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("breakdown_project_context_failed err=%s", exc)

    # 7) Total + durable ContextUsageStateV2 authority for cross-check.
    last: dict[str, Any] = {}
    try:
        last = await _read_context_usage_authority(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("breakdown_context_usage_state_failed err=%s", exc)
    return {
        "session_id": session_id,
        "project_name": project_context.get("project_name"),
        "project_root": project_context.get("project_root"),
        "model": last.get("model", ""),
        "sections": sections,
        "total_estimated_tokens": sum(s["tokens"] for s in sections),
        "last_usage_prompt_tokens": last.get("prompt_tokens", 0),
        "context_window": last.get("context_window", 0),
        "effective_ceiling": last.get("effective_ceiling", 0),
        "compact_at": last.get("compact_at", 0),
        "updated_at": last.get("updated_at", 0),
        "history": history,
        "ts": time.time(),
    }


_PEER_BROADCAST_TIMEOUT_S = 1.0
_GLOBAL_BLOCKING_UI_EVENTS = frozenset({
    "permission_request",
    "external_wait_request",
    "project_directory_request",
    "clarification_request",
})


async def _broadcast_default_chat_peers(originator_ws: WebSocket | None, msg: dict) -> None:
    """2026-05-28 — 多窗口共享 "default" 会话同步广播。

    主桌宠窗口 (`session_id=default`) 和左侧消息面板窗口
    (`session_id=message-panel-main`) 是两条独立的控制通道，但都展示
    同一个 "default" 聊天会话。原有 chat_v2_* 事件只发回给原始 WS，
    导致 panel 看不到主桌宠发出的对话。

    本 helper 在事件 `payload.session_id == "default"` 时把消息 fan-out
    给所有 *其它* 控制通道（跳过 originator 避免重复 push）。
    其它会话（per-tile code-*）保持单发不变。
    Best-effort：peer 关闭/出错只 swallow。
    """
    if not isinstance(msg, dict):
        return
    payload = msg.get("payload") or {}
    if not isinstance(payload, dict):
        return
    payload_sid = payload.get("session_id") or payload.get("new_sid")
    if not payload_sid:
        return
    # Blocking decisions are owned by the product shell, not by whichever
    # conversation tile happened to launch the Run.  The pet window hosts the
    # permission/clarification/external-wait overlays, while a task can be
    # launched from the separate message-panel websocket.  Deliver these
    # events to every other product control peer so the user can always act on
    # them.  The originator already received the frame in ``_send_both``.
    global_blocking_event = str(msg.get("type") or "") in (
        _GLOBAL_BLOCKING_UI_EVENTS
    )
    for _peer_sid, _peer_ws in list(_control_connections.items()):
        if _peer_ws is originator_ws:
            continue
        if not global_blocking_event:
            _main_group = _chat_peer_groups.get(
                _peer_sid, _initial_chat_peer_group(_peer_sid)
            )
            _manager_group = task_session_manager.peer_group(_peer_sid)
            if _main_group != payload_sid and _manager_group != payload_sid:
                continue
        try:
            await asyncio.wait_for(
                _peer_ws.send_json(msg),
                timeout=_PEER_BROADCAST_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.debug(
                "default_chat_peer_broadcast_timeout sid=%s timeout_s=%.1f",
                _peer_sid,
                _PEER_BROADCAST_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "default_chat_peer_broadcast_failed sid=%s err=%s",
                _peer_sid, exc,
            )


async def _send_chat_final(
    originator_ws: WebSocket,
    msg: dict,
    *,
    session_id: str,
    request_id: str = "",
) -> None:
    """Deliver a terminal frame without one stale socket blocking its peers."""
    payload = msg.get("payload") if isinstance(msg, dict) else {}
    text_len = len(str((payload or {}).get("text") or ""))
    logger.info(
        "chat_v2_final_send_started sid=%s request_id=%s chars=%d",
        session_id, request_id, text_len,
    )

    async def _send_originator() -> None:
        try:
            await asyncio.wait_for(
                originator_ws.send_json(msg),
                timeout=_PEER_BROADCAST_TIMEOUT_S,
            )
            logger.info(
                "chat_v2_final_send_completed sid=%s request_id=%s chars=%d",
                session_id, request_id, text_len,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "chat_v2_final_send_timeout sid=%s request_id=%s timeout_s=%.1f chars=%d",
                session_id, request_id, _PEER_BROADCAST_TIMEOUT_S, text_len,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "chat_v2_final_send_failed sid=%s request_id=%s err=%s",
                session_id, request_id, exc,
            )

    await asyncio.gather(
        _send_originator(),
        _broadcast_default_chat_peers(originator_ws, msg),
    )


async def _send_chat_error(
    originator_ws: WebSocket,
    msg: dict,
    *,
    session_id: str,
    request_id: str = "",
) -> None:
    """Deliver a failed terminal frame to every UI peer for the session."""
    logger.info(
        "chat_v2_error_send_started sid=%s request_id=%s",
        session_id,
        request_id,
    )

    async def _send_originator() -> None:
        try:
            await asyncio.wait_for(
                originator_ws.send_json(msg),
                timeout=_PEER_BROADCAST_TIMEOUT_S,
            )
            logger.info(
                "chat_v2_error_send_completed sid=%s request_id=%s",
                session_id,
                request_id,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "chat_v2_error_send_timeout sid=%s request_id=%s timeout_s=%.1f",
                session_id,
                request_id,
                _PEER_BROADCAST_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "chat_v2_error_send_failed sid=%s request_id=%s err=%s",
                session_id,
                request_id,
                exc,
            )

    await asyncio.gather(
        _send_originator(),
        _broadcast_default_chat_peers(originator_ws, msg),
    )


async def _send_new_session_origin_user_echo(
    originator_ws: WebSocket,
    session_id: str,
    text: str,
) -> None:
    """Echo the first draft message back after a new session id is known."""
    if not (text or "").strip():
        return
    try:
        await originator_ws.send_json({
            "type": "chat_v2_user_echo",
            "payload": {"session_id": session_id, "text": text},
        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("new_session_origin_echo_failed sid=%s err=%s", session_id, exc)


# Track active voice pipelines by session so that a control-channel `interrupt`
# message can reach the audio-channel pipeline (they are separate WebSockets).
_voice_transports: weakref.WeakValueDictionary[str, object] = (
    weakref.WeakValueDictionary()
)


# Opt-in dev mode: set DESKPET_DEV_MODE=1 to bypass shared-secret auth.
# Defaults to strict (secret required) so prod deployments are safe.
DEV_MODE = os.getenv("DESKPET_DEV_MODE", "0") == "1"
if DEV_MODE:
    # Surfaced loudly so a prod deployment accidentally booted with
    # DESKPET_DEV_MODE=1 doesn't silently leak /metrics + WS auth.
    logger.warning(
        "metrics_auth_bypassed_dev_mode",
        note="DESKPET_DEV_MODE=1 — /metrics and WS auth are OPEN. Set DESKPET_DEV_MODE=0 in production.",
    )

def _validate_secret(ws: WebSocket) -> bool:
    if DEV_MODE:
        return True
    secret = ws.headers.get("x-shared-secret", "")
    if not secret:
        secret = ws.query_params.get("secret", "")
    return secrets.compare_digest(secret, SHARED_SECRET)


class CloudConfigRequest(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None   # absent or empty = keep current key
    strategy: str | None = None  # absent = keep current strategy
    # WI-R2 (beta-100 relay): when False, the api_key is applied to the
    # live in-memory provider but NEVER written to llm_runtime.json.
    # The relay edition uses this for the rotating `tsk_xxx` device key
    # — it must not be persisted in plaintext. Defaults True so manual
    # edition / every existing caller is byte-identical.
    persist_key: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        if not re.match(r'^https?://[^\s]+$', v):
            raise ValueError("base_url must start with http:// or https:// and contain no whitespace")
        return v

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("model must not be empty")
        if "\n" in v:
            raise ValueError("model must not contain newlines")
        if len(v) > 128:
            raise ValueError("model must not exceed 128 characters")
        return v

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str | None) -> str | None:
        # P4-S20-LLM-Unified: 接受短占位符如 "ollama"（本地 Ollama 不验 key）
        # 同时排除常见的 placeholder 值（避免被误当真 key 入库）。
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if len(v) > 256:
            raise ValueError("api_key must not exceed 256 characters")
        # 1-256 字符任意字符串都接受。本地 Ollama "ollama" 6 字符 OK；
        # 真云端 key (sk-..., tsk-..., 几十字符) 也 OK。
        return v

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str | None) -> str | None:
        if v is None:
            return None
        valid_values = {s.value for s in RoutingStrategy}
        if v not in valid_values:
            raise ValueError(f"strategy must be one of: {', '.join(sorted(valid_values))}")
        return v


@app.post("/config/cloud")
async def update_cloud_config(body: CloudConfigRequest, request: Request):
    """P4-S20-LLM-Unified: hot-swap the unified LLM provider.

    URL keeps the legacy /config/cloud name for frontend compat, but
    semantics now: replace the single `local_llm` (which serves all
    chat traffic) and persist to llm_runtime.json so the new config
    survives restart.

    Auth: same shared-secret gate as /metrics. DEV_MODE bypasses.
    """
    global _current_cloud_api_key, local_llm

    if not DEV_MODE:
        secret = request.headers.get("x-shared-secret", "")
        if not secret or not secrets.compare_digest(secret, SHARED_SECRET):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="config"'},
            )

    # The relay bridge pushes the signed-in account provider shortly after
    # the UI connects.  In the isolated Context OS E2E runtime that would
    # silently replace the loopback fixture and invalidate every oracle.
    # The boot hook is already restricted to DEV_MODE + loopback URLs.
    if _e2e_provider_base:
        logger.info(
            "context_os_e2e_provider_override_ignored requested_model=%s",
            body.model,
        )
        return {
            "ok": True,
            "cloud_configured": True,
            "base_url": _e2e_provider_base,
            "model": "ctx-primary",
            "has_api_key": True,
            "strategy": llm._strategy.value,
            "e2e_locked": True,
        }

    # Resolve api_key: explicit body value > current in-process key >
    # config.llm.local.api_key (Ollama default "ollama" is OK).
    resolved_key: str | None = body.api_key
    if not resolved_key:
        if _current_cloud_api_key:
            resolved_key = _current_cloud_api_key
        elif local_llm is not None:
            resolved_key = getattr(local_llm, "api_key", None) or "ollama"
        else:
            resolved_key = "ollama"

    current_temperature = 0.7
    if local_llm is not None:
        current_temperature = getattr(local_llm, "temperature", 0.7)

    new_provider = OpenAICompatibleProvider(
        base_url=body.base_url,
        api_key=resolved_key,
        model=body.model,
        temperature=current_temperature,
        sanitize_inline_cot_dsml=_sanitize_cot_dsml,
    )

    # Hot-swap: replace module-level local_llm. The chat handler reads
    # this name fresh each request, so the next chat uses the new
    # provider — no restart needed.
    local_llm = new_provider
    _current_cloud_api_key = resolved_key
    _refresh_research_live_llm(new_provider)

    # Persist so next backend restart picks it up. api_key only stored
    # if user explicitly typed one (otherwise stays in keychain via
    # _resolve_cloud_api_key path).
    overrides_to_save: dict = {
        "base_url": body.base_url,
        "model": body.model,
        "temperature": current_temperature,
    }
    if body.persist_key and body.api_key and body.api_key.strip():
        # Persist key to runtime json. (Keychain is the more secure path
        # but storing here makes it work without a Tauri shell — useful
        # for headless dev.)
        #
        # WI-R2: `persist_key=False` (relay edition) skips this — the
        # rotating `tsk_xxx` device key is applied to the live provider
        # above but MUST NOT land in plaintext on disk. The frontend
        # `relayProviderBridge` re-pushes it after every restart /
        # rotation, so losing it from the persisted file is fine.
        overrides_to_save["api_key"] = body.api_key.strip()
    # 合并保存:保留其它运行时键(如 chat_turn_timeout_minutes),不被 provider 设置冲掉。
    _merged_overrides = _load_llm_runtime_overrides()
    _merged_overrides.update(overrides_to_save)
    _save_llm_runtime_overrides(_merged_overrides)

    # Strategy field deprecated under unified schema — silently accepted
    # but ignored. (HybridRouter still has the API; we just don't drive
    # it any more.)

    logger.info(
        "llm_config_updated base_url=%s model=%s",
        body.base_url, body.model,
        # api_key intentionally NOT logged
    )

    return {
        "ok": True,
        "cloud_configured": True,  # always true under unified schema
        "base_url": body.base_url,
        "model": body.model,
        "has_api_key": bool(resolved_key) and resolved_key != "ollama",
        "strategy": llm._strategy.value,
    }


@app.get("/api/skills/list")
async def api_skills_list():
    """WI-A4 v1 — InputBar 拉 skill 列表给 / autocomplete.

    Returns:
        {
          "feature_enabled": bool,    # [features] slash_commands flag
          "builtins": [{name, description}, ...],
          "skills": [{name, description}, ...],
        }
    """
    enabled = bool(getattr(
        getattr(config, "features", None), "slash_commands", False,
    ))
    if not enabled:
        return {"feature_enabled": False, "builtins": [], "skills": []}
    builtins = [
        {"name": "help", "description": "列出所有可用命令"},
        {"name": "goal <text>", "description": "设置 session 级长期目标"},
        {"name": "goal clear", "description": "清除当前 goal"},
    ]
    skills: list[dict[str, str]] = []
    _sl = (
        service_context.get("managed_skill_discovery_projection")
        or service_context.get("skill_loader")
    )
    if _sl is not None:
        try:
            for s in _sl.list_skills():
                skills.append({
                    "name": s.get("name") or "",
                    "description": (s.get("description") or "")[:120],
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("/api/skills/list failed: %s", exc)
    return {
        "feature_enabled": True,
        "builtins": builtins,
        "skills": skills,
    }


@app.get("/api/commands/help")
async def api_commands_help():
    """WI-T2-B5 v2 — InputBar autocomplete 拉所有可用 slash command 列表.

    Returns:
        {
          "feature_enabled": bool,
          "commands": [
            {name, description, args_schema: [{name, type, description, required}]},
            ...
          ]
        }
    """
    enabled = bool(getattr(
        getattr(config, "features", None), "slash_commands", False,
    ))
    if not enabled:
        return {"feature_enabled": False, "commands": []}

    # Builtin commands (hardcoded args schema)
    commands: list[dict[str, Any]] = [
        {
            "name": "help",
            "description": "列出所有可用命令 + skill",
            "args_schema": [],
        },
        {
            "name": "goal",
            "description": "设置 session 级长期目标 (空参或 clear 清除/显状态)",
            "args_schema": [
                {"name": "text", "type": "string",
                 "description": "目标描述; 'clear' 清除", "required": False},
            ],
        },
        {
            # superpowers A2 — 让 /prefs 出现在 InputBar 的 `/` 自动补全下拉里
            # (此前只在 dispatcher + /help 文本里，下拉候选漏了它 → 不可发现)。
            "name": "prefs",
            "description": "查看/清除偏好记忆 (空参列出; clear [intent|plan] 清除)",
            "args_schema": [
                {"name": "subcommand", "type": "string",
                 "description": "'clear' 或 'clear intent' / 'clear plan'", "required": False},
            ],
        },
    ]

    # Skills — list_skills 返回 SkillMeta 的 dict 形式；args_schema 从
    # frontmatter 解析（如果有），否则给空 list（用户自由输入参数）
    _sl = (
        service_context.get("managed_skill_discovery_projection")
        or service_context.get("skill_loader")
    )
    if _sl is not None:
        try:
            for s in _sl.list_skills():
                meta_args = s.get("args") or s.get("arguments") or []
                arg_schema: list[dict[str, Any]] = []
                if isinstance(meta_args, list):
                    for a in meta_args:
                        if isinstance(a, str):
                            arg_schema.append({
                                "name": a, "type": "string",
                                "description": "", "required": False,
                            })
                        elif isinstance(a, dict):
                            arg_schema.append({
                                "name": str(a.get("name", "")),
                                "type": str(a.get("type", "string")),
                                "description": str(a.get("description", ""))[:120],
                                "required": bool(a.get("required", False)),
                            })
                commands.append({
                    "name": s.get("name") or "",
                    "description": (s.get("description") or "")[:120],
                    "args_schema": arg_schema,
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("/api/commands/help failed: %s", exc)

    return {"feature_enabled": True, "commands": commands}


@app.get("/api/commands/{name}/schema")
async def api_command_schema(name: str):
    """WI-T2-B5 v2 — 单命令 arg schema 详情（autocomplete arg-hint 用）.

    Returns:
        {name, description, args_schema} 或 404
    """
    enabled = bool(getattr(
        getattr(config, "features", None), "slash_commands", False,
    ))
    if not enabled:
        return Response(status_code=403, content="feature disabled")

    # Builtin lookup
    name_lower = name.strip("/ ").lower()
    builtin_map = {
        "help": {"description": "列出所有可用命令", "args_schema": []},
        "goal": {
            "description": "设置 session 级长期目标",
            "args_schema": [
                {"name": "text", "type": "string", "description": "目标描述",
                 "required": False},
            ],
        },
    }
    if name_lower in builtin_map:
        b = builtin_map[name_lower]
        return {"name": name_lower, **b}

    # Skill lookup
    _sl = (
        service_context.get("managed_skill_discovery_projection")
        or service_context.get("skill_loader")
    )
    if _sl is not None:
        try:
            for s in _sl.list_skills():
                if (s.get("name") or "").lower() == name_lower:
                    meta_args = s.get("args") or s.get("arguments") or []
                    arg_schema = []
                    if isinstance(meta_args, list):
                        for a in meta_args:
                            if isinstance(a, str):
                                arg_schema.append({
                                    "name": a, "type": "string",
                                    "description": "", "required": False,
                                })
                            elif isinstance(a, dict):
                                arg_schema.append({
                                    "name": str(a.get("name", "")),
                                    "type": str(a.get("type", "string")),
                                    "description": str(a.get("description", ""))[:120],
                                    "required": bool(a.get("required", False)),
                                })
                    return {
                        "name": name_lower,
                        "description": (s.get("description") or "")[:120],
                        "args_schema": arg_schema,
                    }
        except Exception as exc:  # noqa: BLE001
            logger.warning("/api/commands/%s/schema failed: %s", name_lower, exc)

    return Response(status_code=404, content=f"unknown command: {name_lower}")


@app.get("/health")
async def health():
    # P3-S2: surface startup failures (esp. CUDA unavailable / model dir
    # missing) so the Rust supervisor and future frontend banner can
    # react instead of treating a crippled backend as "ready".
    errors = startup_errors.snapshot()
    return {
        "status": "degraded" if errors else "ok",
        "secret_hint": SHARED_SECRET[:4] + "...",
        "strategy": llm._strategy.value,
        # P4-S20-LLM-Unified 修: 旧 `llm._cloud` 是 HybridRouter 的废弃字段,
        # unified schema 重构后恒为 None → /health 永远误报 cloud_configured=false
        # (即便 backend 实际有云端 key)。改为读真实生效的 local_llm: base_url
        # 非本地 + 有真 key(非 ollama 占位) = 已配云端。
        "cloud_configured": bool(
            local_llm is not None
            and "localhost" not in str(getattr(local_llm, "base_url", ""))
            and "127.0.0.1" not in str(getattr(local_llm, "base_url", ""))
            and getattr(local_llm, "api_key", None)
            and getattr(local_llm, "api_key", "") != "ollama"
        ),
        "startup_errors": errors,
        "voice": _voice_runtime.health_payload(),
    }


@app.post("/metrics/event")
async def post_metrics_event(request: Request):
    """WI-T1.7 last-mile: 前端 ArtifactCard 按钮点击 → 此端点 → metrics.jsonl。

    Body: {event: str, detail: dict}
    Auth: 同 /metrics — SHARED_SECRET 在 DEV_MODE 下放行。
    PRD §5 健康区间 metric (artifact_action click rate) 的入口。
    """
    if not DEV_MODE:
        secret = request.headers.get("x-shared-secret", "")
        if not secret or not secrets.compare_digest(secret, SHARED_SECRET):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="metrics"'},
            )
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400, content="invalid json")
    event = body.get("event")
    detail = body.get("detail") or {}
    if not isinstance(event, str) or not event:
        return Response(status_code=400, content="missing 'event' field")
    if not isinstance(detail, dict):
        return Response(status_code=400, content="'detail' must be dict")
    try:
        from observability.metrics_sink import record as _metric_record
        _metric_record(event, detail)
    except Exception as exc:  # noqa: BLE001
        logger.warning("metrics event drop: %s", exc)
        return Response(status_code=500, content="sink error")
    return Response(status_code=204)


@app.get("/metrics")
async def metrics(request: Request):
    """Prometheus scrape endpoint (P2-1-S6).

    Gated by the same shared secret that protects WS connections. In
    DEV_MODE the gate is open so local `curl` / smoke scripts can hit it
    without juggling headers.
    """
    if not DEV_MODE:
        secret = request.headers.get("x-shared-secret", "")
        if not secret or not secrets.compare_digest(secret, SHARED_SECRET):
            # RFC 7235 §3.1: a 401 MUST carry WWW-Authenticate so clients
            # know which scheme/realm to retry with. Prometheus scrapers
            # and curl both surface the header to the operator.
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="metrics"'},
            )
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


# --- S14 memory management dispatch -----------------------------------------
# Handled on the control WS (same auth gate as chat/interrupt) so we don't
# expose a second unauthenticated HTTP surface. The four verbs are the minimum
# needed for the "delete-my-data" affordance V5 §6 requires.

async def _handle_memory_message(
    ws: "WebSocket", session_id: str, msg_type: str, payload: dict
) -> None:
    store = service_context.get("memory_store")
    if store is None:
        await ws.send_json({
            "type": "error",
            "payload": {"message": "memory store not registered"},
        })
        return

    try:
        if msg_type == "memory_list":
            # Scope defaults to the current session; ``scope: "all"`` returns
            # every session's turns (export-style). The UI asks per-session.
            scope = payload.get("scope") or "session"
            target_session = None if scope == "all" else payload.get(
                "session_id", session_id
            )
            limit = payload.get("limit")
            turns = await store.list_turns(target_session, limit)
            await ws.send_json({
                "type": "memory_list_response",
                "payload": {
                    "scope": scope,
                    "session_id": target_session,
                    "turns": [
                        {
                            "id": t.id,
                            "session_id": t.session_id,
                            "role": t.role,
                            "content": t.content,
                            "created_at": t.created_at,
                        }
                        for t in turns
                    ],
                },
            })

        elif msg_type == "memory_delete":
            turn_id = payload.get("id")
            if not isinstance(turn_id, int):
                await ws.send_json({
                    "type": "error",
                    "payload": {"message": "memory_delete requires integer id"},
                })
                return
            deleted = await store.delete_turn(turn_id)
            await ws.send_json({
                "type": "memory_delete_ack",
                "payload": {"id": turn_id, "deleted": deleted},
            })

        elif msg_type == "memory_clear":
            scope = payload.get("scope") or "session"
            if scope == "all":
                removed = await store.clear_all()
                await ws.send_json({
                    "type": "memory_clear_ack",
                    "payload": {"scope": "all", "removed": removed},
                })
            else:
                target_session = payload.get("session_id", session_id)
                await store.clear(target_session)
                await ws.send_json({
                    "type": "memory_clear_ack",
                    "payload": {"scope": "session", "session_id": target_session},
                })

        elif msg_type == "memory_export":
            # Dump everything — user asked for their data, they get all of it.
            turns = await store.list_turns(None, None)
            sessions = await store.list_sessions()
            await ws.send_json({
                "type": "memory_export_response",
                "payload": {
                    "exported_at": __import__("time").time(),
                    "sessions": [
                        {
                            "session_id": s.session_id,
                            "turn_count": s.turn_count,
                            "last_message_at": s.last_message_at,
                        }
                        for s in sessions
                    ],
                    "turns": [
                        {
                            "id": t.id,
                            "session_id": t.session_id,
                            "role": t.role,
                            "content": t.content,
                            "created_at": t.created_at,
                        }
                        for t in turns
                    ],
                },
            })
    except AttributeError as exc:
        # Inner store without list_turns/delete_turn/list_sessions/clear_all —
        # surface a clean error instead of a 500 on the wire.
        logger.warning("memory_admin_unsupported", error=str(exc), type=msg_type)
        await ws.send_json({
            "type": "error",
            "payload": {"message": f"{msg_type} not supported by active memory store"},
        })


@app.websocket("/ws/control")
async def control_channel(ws: WebSocket):
    await ws.accept()
    if not _validate_secret(ws):
        try:
            await ws.close(code=4001, reason="invalid secret")
        except Exception:
            pass
        return

    session_id = ws.query_params.get("session_id", "default")
    requested_scope = ws.query_params.get("requested_scope", "")
    # The main-window identity bridge must coexist with the message-panel
    # chat socket.  Its requested scope is not mutation authority (the first
    # Rust-signed command still proves the real label/scope), but it is safe to
    # keep this transport in a unique auxiliary slot because it never owns
    # chat delivery.  The message-panel remains the canonical session socket.
    is_companion_aux = requested_scope == "identity_bind"
    if not is_companion_aux:
        _register_chat_peer(session_id)
    connection_registry_key = (
        f"companion-aux:{session_id}:{uuid.uuid4()}"
        if is_companion_aux
        else session_id
    )
    # P4-S20: gracefully kick the previous holder of this session_id
    # (e.g. an old E2E client) so its disconnect callback doesn't
    # later pop OUR entry. Don't kill ourselves if we're the holder.
    if not is_companion_aux:
        _prev_ws = _control_connections.get(session_id)
        if _prev_ws is not None and _prev_ws is not ws:
            try:
                await _prev_ws.close(code=4002, reason="session replaced")
            except Exception:
                pass
    _control_connections[connection_registry_key] = ws
    _chat_peer_groups[connection_registry_key] = _initial_chat_peer_group(
        session_id
    )
    logger.info("control channel connected", session_id=session_id)
    # P3-S2: first frame after handshake reports startup-error state so the
    # UI can render "CUDA 缺失" / "模型缺失" banners without polling /health.
    try:
        await ws.send_json({
            "type": "startup_status",
            "degraded": startup_errors.is_degraded(),
            "errors": startup_errors.snapshot(),
        })
    except Exception as _e:
        logger.warning("startup_status_send_failed", error=str(_e))
    companion_challenge = None
    if _companion_control_ingress is not None:
        try:
            companion_challenge = (
                _companion_control_ingress.open_challenge(
                    requested_window_label=ws.query_params.get(
                        "requested_window_label", ""
                    ),
                    requested_scope=ws.query_params.get(
                        "requested_scope", ""
                    ),
                )
            )
            await ws.send_json(
                {
                    "type": "companion_control_challenge",
                    "payload": companion_challenge.public_payload(),
                }
            )
            if requested_scope == "companion_action":
                try:
                    frozen_identity = _companion_identity_gate.freeze()
                    await ws.send_json(
                        {
                            "type": "companion_identity_status",
                            "payload": {
                                "ready": True,
                                "status": "ready",
                                "profile_id": frozen_identity.owner.profile_id,
                                "profile_generation": (
                                    frozen_identity.owner.profile_generation
                                ),
                                "binding_epoch": str(
                                    frozen_identity.binding_epoch
                                ),
                            },
                        }
                    )
                except Exception:  # noqa: BLE001
                    await ws.send_json(
                        {
                            "type": "companion_identity_status",
                            "payload": {
                                "ready": False,
                                "status": "unready",
                            },
                        }
                    )
        except Exception as _e:  # noqa: BLE001
            logger.warning(
                "companion_control_challenge_failed", error=type(_e).__name__
            )
    try:
        while True:
            wire_text = await ws.receive_text()
            try:
                raw = json.loads(wire_text)
            except (json.JSONDecodeError, UnicodeError):
                await ws.send_json(
                    {
                        "type": "error",
                        "payload": {"message": "invalid control JSON"},
                    }
                )
                continue
            if not isinstance(raw, dict):
                await ws.send_json(
                    {
                        "type": "error",
                        "payload": {"message": "control frame must be an object"},
                    }
                )
                continue
            msg_type = raw.get("type", "")

            if _companion_control_ingress is not None:
                from deskpet.companion.control_ingress import (
                    CompanionControlIngressError,
                    is_privileged_control_kind,
                )

            if (
                _companion_control_ingress is not None
                and is_privileged_control_kind(msg_type)
            ):
                try:
                    strict_raw = (
                        _companion_control_ingress.parse_privileged_frame(
                            wire_text
                        )
                    )
                    if strict_raw is None:
                        raise CompanionControlIngressError(
                            "privileged_control_parse_failed"
                        )
                    if companion_challenge is None:
                        raise CompanionControlIngressError(
                            "companion_control_challenge_missing"
                        )
                    response = await _companion_control_ingress.execute(
                        strict_raw,
                        challenge=companion_challenge,
                    )
                    if msg_type in {
                        "companion_growth_evaluation_decision",
                        "companion_growth_activation_decision",
                        "companion_growth_action_decision",
                        "companion_rollback",
                        "companion_forget",
                    }:
                        await _dispatch_companion_action_command(
                            msg_type, strict_raw
                        )
                    if msg_type == "companion_profile_bind":
                        try:
                            _bound_identity = _companion_identity_gate.freeze()
                            _inbox_session_id = (
                                await _ensure_companion_inbox_route(
                                    _bound_identity
                                )
                            )
                            response = {
                                **response,
                                "payload": {
                                    **dict(response.get("payload", {})),
                                    "session_id": _inbox_session_id,
                                },
                            }
                            if _companion_notification_service is not None:
                                await _companion_notification_service.bind_and_drain(
                                    _bound_identity
                                )
                        except Exception as _drain_exc:  # noqa: BLE001
                            logger.warning(
                                "companion_projection_bind_drain_failed",
                                error=type(_drain_exc).__name__,
                                code=getattr(
                                    _drain_exc,
                                    "code",
                                    str(_drain_exc),
                                ),
                            )
                        # Harness startup can discover recoverable Runs before
                        # the main window finishes its signed identity bind.
                        # That attempt correctly fails closed; wake the
                        # event-driven reconciler now that the same process
                        # identity gate is ready so waiting work cannot remain
                        # stranded until an unrelated durable event arrives.
                        if _trigger_harness_recovery_after_identity_bind():
                            # 2026-08-05 fork 前既有 bug 修复：此处原引用局部变量
                            # `payload`，但它在本 try 块更下方才赋值——recovery
                            # 触发时即 UnboundLocalError，bind 被拒且前端无限
                            # 重试（真机测试实锤）。改读 response 的 payload。
                            _resp_payload = (
                                response.get("payload", {})
                                if isinstance(response, Mapping)
                                else {}
                            )
                            logger.info(
                                "harness_recovery_triggered_identity_ready",
                                profile_id=_resp_payload.get("profile_id"),
                                profile_generation=_resp_payload.get(
                                    "profile_generation"
                                ),
                            )
                    elif msg_type == "companion_profile_unbind":
                        if _companion_notification_service is not None:
                            _companion_notification_service.close_history()
                    await ws.send_json(response)
                    payload = response.get("payload", {})
                    companion_challenge = companion_challenge.advance(
                        request_seq=int(
                            payload.get("next_request_seq")
                            or payload["nextRequestSeq"]
                        ),
                        binding_epoch=int(
                            payload.get("binding_epoch")
                            or payload["bindingEpoch"]
                        ),
                    )
                    if msg_type in {
                        "companion_profile_bind",
                        "companion_profile_unbind",
                    }:
                        identity_status = {
                            "type": "companion_identity_status",
                            "payload": {
                                "ready": (
                                    msg_type == "companion_profile_bind"
                                ),
                                "status": (
                                    "ready"
                                    if msg_type
                                    == "companion_profile_bind"
                                    else "unready"
                                ),
                                "binding_epoch": str(
                                    companion_challenge.binding_epoch
                                ),
                                **(
                                    {
                                        "profile_id": payload.get("profile_id"),
                                        "profile_generation": payload.get(
                                            "profile_generation"
                                        ),
                                        "session_id": payload.get(
                                            "session_id"
                                        ),
                                    }
                                    if msg_type == "companion_profile_bind"
                                    else {}
                                ),
                            },
                        }
                        for status_ws in list(
                            _control_connections.values()
                        ):
                            try:
                                await status_ws.send_json(identity_status)
                            except Exception:
                                pass
                except Exception as _e:  # noqa: BLE001
                    code = getattr(_e, "code", str(_e))
                    rechallenge = bool(
                        getattr(_e, "rechallenge", True)
                    )
                    logger.warning(
                        "companion_control_rejected",
                        command_kind=msg_type,
                        code=code,
                        rechallenge=rechallenge,
                        connection_id=(
                            companion_challenge.connection_id
                            if companion_challenge is not None
                            else None
                        ),
                    )
                    await ws.send_json(
                        {
                            "type": (
                                "companion_control_rechallenge"
                                if rechallenge
                                else "companion_control_error"
                            ),
                            "payload": {
                                "code": code,
                                "retryable": True,
                            },
                        }
                    )
                    # A rejected mutation never mutates the old active lease.
                    # Rechallenge via a fresh bounded challenged row so stale
                    # seq/epoch clients can recover without backend restart.
                    if rechallenge:
                        try:
                            companion_challenge = (
                                _companion_control_ingress.open_challenge(
                                    requested_window_label=ws.query_params.get(
                                        "requested_window_label", ""
                                    ),
                                    requested_scope=ws.query_params.get(
                                        "requested_scope", ""
                                    ),
                                )
                            )
                            await ws.send_json(
                                {
                                    "type": "companion_control_challenge",
                                    "payload": (
                                        companion_challenge.public_payload()
                                    ),
                                }
                            )
                        except Exception as challenge_error:  # noqa: BLE001
                            logger.warning(
                                "companion_rechallenge_failed",
                                error=type(challenge_error).__name__,
                            )
                continue

            if msg_type == "companion_detail_get":
                _detail_request_id = str(raw.get("request_id") or "")
                try:
                    from deskpet.companion.detail_query import (
                        CompanionDetailQueryError,
                    )

                    if (
                        _companion_detail_query is None
                        or companion_challenge is None
                        or requested_scope != "companion_action"
                    ):
                        raise CompanionDetailQueryError("unavailable")
                    _detail_identity = _companion_identity_gate.freeze()
                    if (
                        int(companion_challenge.binding_epoch)
                        != int(_detail_identity.binding_epoch)
                    ):
                        raise CompanionDetailQueryError("cursor_invalid")
                    _detail_payload = raw.get("payload")
                    if not isinstance(_detail_payload, dict):
                        raise CompanionDetailQueryError("unavailable")
                    _detail_response = await _companion_detail_query.query(
                        frozen_identity=_detail_identity,
                        control_epoch=int(companion_challenge.binding_epoch),
                        request=_detail_payload,
                    )
                    await ws.send_json(
                        {
                            "type": "companion_detail_response",
                            "request_id": _detail_request_id,
                            "payload": _detail_response,
                        }
                    )
                except Exception as _detail_exc:  # noqa: BLE001
                    _detail_code = getattr(_detail_exc, "code", "unavailable")
                    if _detail_code not in {
                        "unavailable",
                        "detail_changed",
                        "cursor_invalid",
                    }:
                        _detail_code = "unavailable"
                    await ws.send_json(
                        {
                            "type": "companion_detail_error",
                            "request_id": _detail_request_id,
                            "payload": {
                                "code": _detail_code,
                                **(
                                    {"message": str(_detail_exc)}
                                    if str(_detail_exc) != _detail_code
                                    else {}
                                ),
                            },
                        }
                    )
                continue

            if await _handle_control_ws_message(raw, session_id=session_id, ws=ws):
                continue

            if msg_type == "ping":
                await ws.send_json({"type": "pong"})

            elif msg_type == "memory_summarize_now":
                # P4-S20-D: 手动触发 — 用户可在 SettingsPanel / MemoryPanel
                # 点 "立即总结老对话" 按钮。参数支持 age_days / min_messages
                # / max_per_run override。
                payload = raw.get("payload", {}) or {}
                if (
                    _summarizer_state_db_path is None
                    or local_llm is None
                ):
                    await ws.send_json({
                        "type": "memory_summarize_response",
                        "payload": {
                            "ok": False,
                            "error": "summarizer not initialized (state_db or local_llm missing)",
                        },
                    })
                    continue
                try:
                    from deskpet.memory.summarizer import (
                        summarize_old_sessions, make_llm_call,
                    )
                    _result = await summarize_old_sessions(
                        db_path=_summarizer_state_db_path,
                        llm_call=make_llm_call(local_llm),
                        age_days=float(payload.get("age_days", 30)),
                        min_messages=int(payload.get("min_messages", 20)),
                        max_per_run=int(payload.get("max_per_run", 10)),
                        vector_worker=service_context.get("vector_worker"),
                        # Stage 2 / WI-S2.4 — episodic→semantic 固化
                        fact_extractor=_fact_extractor,
                        episodic_to_semantic=bool(
                            config.memory.v2.episodic_to_semantic
                        ),
                        background_tasks=_episodic_background_tasks,
                    )
                    await ws.send_json({
                        "type": "memory_summarize_response",
                        "payload": {
                            "ok": True,
                            "sessions_scanned": _result.sessions_scanned,
                            "sessions_summarized": _result.sessions_summarized,
                            "messages_archived": _result.messages_archived,
                            "summary_ids": _result.summaries_created,
                            "errors": _result.errors,
                        },
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.warning("memory_summarize_failed: %s", exc)
                    await ws.send_json({
                        "type": "memory_summarize_response",
                        "payload": {"ok": False, "error": str(exc)},
                    })

            elif msg_type == "memory_thumbs_up":
                # 记忆系统升级 WI-M1.1：评估反馈回路。前端历史/消息面板
                # 点 👍/👎 → 这里把一行落进 memory_user_feedback，供 eval
                # CLI 离线分析「召回质量差的 query」。
                # payload: { msg_id:int, query:str, helpful:bool }
                # flag feedback_loop 关 → 不落库、不建 v2 表（Strangler-Fig：
                # MR-0 要求 flag 全关时 state.db 无任何 v2 表）。
                payload = raw.get("payload", {}) or {}
                if not config.memory.v2.feedback_loop:
                    await ws.send_json({
                        "type": "memory_thumbs_up_response",
                        "payload": {"ok": False, "reason": "feedback_loop_disabled"},
                    })
                    continue
                if _summarizer_state_db_path is None:
                    await ws.send_json({
                        "type": "memory_thumbs_up_response",
                        "payload": {"ok": False, "reason": "state_db_unavailable"},
                    })
                    continue
                try:
                    from deskpet.memory.eval.feedback import FeedbackStore
                    _msg_id = int(payload.get("msg_id"))
                    _helpful = bool(payload.get("helpful", True))
                    _store = FeedbackStore(_summarizer_state_db_path)
                    _row_id = await _store.record(
                        source_msg_id=_msg_id,
                        value=1 if _helpful else -1,
                        context_query=payload.get("query") or None,
                        session_id=session_id,
                    )
                    await ws.send_json({
                        "type": "memory_thumbs_up_response",
                        "payload": {"ok": True, "feedback_id": _row_id},
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.warning("memory_thumbs_up_failed: %s", exc)
                    await ws.send_json({
                        "type": "memory_thumbs_up_response",
                        "payload": {"ok": False, "reason": str(exc)},
                    })

            elif msg_type == "memory_archive_list":
                # P4-S20-D: 列出 archive 内容供用户查看 / 决定是否恢复或彻底删
                # 参数：limit (default 100), session_id (optional filter)
                payload = raw.get("payload", {}) or {}
                if _summarizer_state_db_path is None:
                    await ws.send_json({
                        "type": "memory_archive_list_response",
                        "payload": {"ok": False, "error": "state_db not initialized"},
                    })
                    continue
                _limit = int(payload.get("limit", 100))
                _filter_sid = payload.get("session_id")
                try:
                    import aiosqlite
                    rows = []
                    async with aiosqlite.connect(_summarizer_state_db_path) as _db:
                        if _filter_sid:
                            cur = await _db.execute(
                                "SELECT id, session_id, role, content, created_at, "
                                "archived_at, archived_into_id "
                                "FROM messages_archive WHERE session_id = ? "
                                "ORDER BY archived_at DESC, created_at DESC LIMIT ?",
                                (_filter_sid, _limit),
                            )
                        else:
                            cur = await _db.execute(
                                "SELECT id, session_id, role, content, created_at, "
                                "archived_at, archived_into_id "
                                "FROM messages_archive "
                                "ORDER BY archived_at DESC, created_at DESC LIMIT ?",
                                (_limit,),
                            )
                        async for r in cur:
                            rows.append({
                                "id": r[0], "session_id": r[1], "role": r[2],
                                "content": r[3], "created_at": r[4],
                                "archived_at": r[5], "archived_into_id": r[6],
                            })
                        await cur.close()
                    await ws.send_json({
                        "type": "memory_archive_list_response",
                        "payload": {"ok": True, "rows": rows, "count": len(rows)},
                    })
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json({
                        "type": "memory_archive_list_response",
                        "payload": {"ok": False, "error": str(exc)},
                    })

            elif msg_type == "permission_response":
                payload = raw.get("payload", {}) or {}
                target_sid = str(payload.get("session_id") or session_id)
                receipt = await _signal_product_harness_decision(
                    target_sid,
                    payload,
                    {"decision": str(payload.get("decision") or "deny")},
                )
                logger.info(
                    "permission_response_applied",
                    decision_id=payload.get("decision_id") or payload.get("request_id"),
                    duplicate=receipt.duplicate,
                )

            elif msg_type == "external_wait_response":
                payload = raw.get("payload", {}) or {}
                target_sid = str(payload.get("session_id") or session_id)
                receipt = await _signal_product_harness_decision(
                    target_sid,
                    payload,
                    {
                        "decision": "complete",
                        "completed": True,
                    },
                )
                logger.info(
                    "external_wait_response_applied",
                    decision_id=(
                        payload.get("decision_id")
                        or payload.get("request_id")
                    ),
                    wait_ref=payload.get("wait_ref"),
                    duplicate=receipt.duplicate,
                )

            elif msg_type == "project_directory_response":
                payload = raw.get("payload", {}) or {}
                target_sid = str(payload.get("session_id") or session_id)
                try:
                    project_root = _resolve_selected_project_directory(
                        payload.get("parent_directory"),
                        payload.get("folder_name"),
                        allow_existing=(
                            payload.get("directory_mode") == "use_existing"
                        ),
                    )
                except ValueError as exc:
                    await ws.send_json(
                        {
                            "type": "project_directory_error",
                            "payload": {
                                "decision_id": (
                                    payload.get("decision_id")
                                    or payload.get("request_id")
                                ),
                                "code": str(exc),
                                "error": (
                                    "这个位置不能安全创建项目，请重新选择空目录或修改文件夹名。"
                                ),
                            },
                        }
                    )
                    continue
                try:
                    receipt = await _signal_product_harness_decision(
                        target_sid,
                        payload,
                        {
                            "decision": "complete",
                            "completed": True,
                            "project_root": project_root,
                            "parent_directory": str(
                                payload.get("parent_directory") or ""
                            ),
                            "folder_name": str(
                                payload.get("folder_name") or ""
                            ),
                            "directory_mode": str(
                                payload.get("directory_mode") or "create_new"
                            ),
                        },
                    )
                except Exception as exc:  # execution contracts are user-retryable
                    from deskpet.execution.contracts import ExecutionError

                    if not isinstance(exc, ExecutionError):
                        raise
                    logger.warning(
                        "project_directory_response_rejected",
                        session_id=target_sid,
                        decision_id=(
                            payload.get("decision_id")
                            or payload.get("request_id")
                        ),
                        code=exc.code,
                        error=str(exc),
                    )
                    await ws.send_json(
                        {
                            "type": "project_directory_error",
                            "payload": {
                                "decision_id": (
                                    payload.get("decision_id")
                                    or payload.get("request_id")
                                ),
                                "code": exc.code,
                                "error": (
                                    "项目位置未能应用，请重新选择；若任务状态已经变化，"
                                    "请停止当前任务后重试。"
                                ),
                            },
                        }
                    )
                    continue
                logger.info(
                    "project_directory_response_applied",
                    decision_id=(
                        payload.get("decision_id")
                        or payload.get("request_id")
                    ),
                    project_root=project_root,
                    duplicate=receipt.duplicate,
                )

            elif msg_type == "clarification_response":
                payload = raw.get("payload", {}) or {}
                target_sid = str(payload.get("session_id") or session_id)
                receipt = await _signal_product_harness_decision(
                    target_sid,
                    payload,
                    {"answer": str(payload.get("answer") or "")},
                )
                logger.info(
                    "clarification_response_applied",
                    decision_id=payload.get("decision_id") or payload.get("request_id"),
                    duplicate=receipt.duplicate,
                )

            elif msg_type == "permission_auto_mode_set":
                # P4-S21 #13: toggle "yes-to-all" mode. Settings panel
                # sends {enabled: bool}; we flip the flag on the live
                # PermissionGate instance. Default is OFF — has to be
                # explicitly opted in. Reverts on backend restart.
                payload = raw.get("payload", {}) or {}
                enabled = await _set_authorization_auto_mode(
                    bool(payload.get("enabled", False))
                )
                if permission_gate_v2 is not None:
                    # P4-S25: route through set_auto_mode so the choice
                    # persists across backend restart (was process-only).
                    logger.info(
                        "permission_auto_mode_set", enabled=enabled,
                    )
                await ws.send_json({
                    "type": "permission_auto_mode_response",
                    "payload": {"enabled": enabled},
                })

            elif msg_type == "permission_auto_mode_get":
                enabled = await _authorization_auto_mode()
                await ws.send_json({
                    "type": "permission_auto_mode_response",
                    "payload": {"enabled": enabled},
                })

            elif msg_type == "capability_list":
                center = service_context.get("capability_center")
                if center is None:
                    await ws.send_json({
                        "type": "capability_list_response",
                        "payload": {
                            "capabilities": [],
                            "error": "capability center is unavailable",
                        },
                    })
                    continue
                try:
                    capabilities = await center.list_capabilities()
                    await ws.send_json({
                        "type": "capability_list_response",
                        "payload": {"capabilities": capabilities},
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "capability_center_list_failed", error=str(exc)
                    )
                    await ws.send_json({
                        "type": "capability_list_response",
                        "payload": {
                            "capabilities": [],
                            "error": str(exc),
                        },
                    })

            elif msg_type == "capability_operations_list":
                center = service_context.get("capability_center")
                if center is None:
                    await ws.send_json({
                        "type": "capability_operations_response",
                        "payload": {
                            "operations": [],
                            "error": "capability center is unavailable",
                        },
                    })
                    continue
                payload = raw.get("payload", {}) or {}
                try:
                    limit = max(1, min(int(payload.get("limit", 100)), 500))
                except (TypeError, ValueError):
                    limit = 100
                try:
                    operations = await center.list_operations(limit=limit)
                    await ws.send_json({
                        "type": "capability_operations_response",
                        "payload": {"operations": operations},
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "capability_center_operations_failed", error=str(exc)
                    )
                    await ws.send_json({
                        "type": "capability_operations_response",
                        "payload": {
                            "operations": [],
                            "error": str(exc),
                        },
                    })

            elif msg_type in {
                "capability_install",
                "capability_activate",
                "capability_repair",
                "capability_uninstall",
                "capability_rollback",
                "capability_operation_cancel",
                "capability_operation_retry",
            }:
                center = service_context.get("capability_center")
                payload = raw.get("payload", {}) or {}
                action = msg_type.removeprefix("capability_").removeprefix(
                    "operation_"
                )
                if center is None:
                    await ws.send_json({
                        "type": "capability_action_response",
                        "payload": {
                            "ok": False,
                            "action": action,
                            "error_code": "capability_center_unavailable",
                            "error": "capability center is unavailable",
                        },
                    })
                    continue
                try:
                    if msg_type in {
                        "capability_install",
                        "capability_activate",
                        "capability_repair",
                    }:
                        from deskpet.capabilities.ui_service import (
                            CapabilityCenterError,
                        )

                        raise CapabilityCenterError(
                            "model_driven_action_required",
                            "Install, activation, and repair require the main "
                            "agent to resolve a trusted source or failure receipt.",
                        )
                    if msg_type == "capability_uninstall":
                        operation = await center.request_uninstall(
                            capability_id=str(
                                payload.get("capability_id") or ""
                            ),
                            source_operation_id=(
                                str(payload["operation_id"])
                                if payload.get("operation_id")
                                else None
                            ),
                        )
                    elif msg_type == "capability_rollback":
                        operation = await center.request_rollback(
                            operation_id=str(
                                payload.get("operation_id") or ""
                            ),
                            capability_id=(
                                str(payload["capability_id"])
                                if payload.get("capability_id")
                                else None
                            ),
                        )
                    elif msg_type == "capability_operation_retry":
                        operation = await center.request_retry(
                            operation_id=str(
                                payload.get("operation_id") or ""
                            )
                        )
                    else:
                        operation = await center.request_cancel(
                            operation_id=str(
                                payload.get("operation_id") or ""
                            )
                        )
                    await ws.send_json({
                        "type": "capability_action_response",
                        "payload": {
                            "ok": True,
                            "action": action,
                            "operation_id": operation.operation_id,
                            "status": operation.status,
                            "phase": operation.phase,
                        },
                    })
                except Exception as exc:  # noqa: BLE001
                    error_code = str(
                        getattr(exc, "code", "capability_action_failed")
                    )
                    logger.warning(
                        "capability_center_action_failed",
                        action=action,
                        error_code=error_code,
                        error=str(exc),
                    )
                    await ws.send_json({
                        "type": "capability_action_response",
                        "payload": {
                            "ok": False,
                            "action": action,
                            "error_code": error_code,
                            "error": str(exc),
                        },
                    })

            elif msg_type == "chat_turn_timeout_set":
                # 设置面板写「对话超时(分钟)」。持久化到 llm_runtime.json,夹 1~60 分钟。
                payload = raw.get("payload", {}) or {}
                try:
                    _mins = int(round(float(payload.get("minutes", 15))))
                except Exception:  # noqa: BLE001
                    _mins = 15
                _mins = max(1, min(60, _mins))
                _ov = _load_llm_runtime_overrides()
                _ov["chat_turn_timeout_minutes"] = _mins
                _save_llm_runtime_overrides(_ov)
                logger.info("chat_turn_timeout_set minutes=%d", _mins)
                await ws.send_json({
                    "type": "chat_turn_timeout_response",
                    "payload": {"minutes": _mins},
                })

            elif msg_type == "chat_turn_timeout_get":
                # 设置面板读当前值(展示)。默认 15。
                try:
                    _cur = int(round(float(
                        _load_llm_runtime_overrides().get("chat_turn_timeout_minutes", 15)
                    )))
                except Exception:  # noqa: BLE001
                    _cur = 15
                await ws.send_json({
                    "type": "chat_turn_timeout_response",
                    "payload": {"minutes": max(1, min(60, _cur))},
                })

            elif msg_type == "plugin_list":
                # P4-S20 Stage D — list all discovered plugins with enabled status.
                if plugin_manager is None:
                    await ws.send_json({
                        "type": "plugin_list_response",
                        "payload": {"plugins": [], "error": "plugin manager not initialized"},
                    })
                    continue
                await ws.send_json({
                    "type": "plugin_list_response",
                    "payload": {"plugins": plugin_manager.list_plugins()},
                })

            elif msg_type == "plugin_enable":
                payload = raw.get("payload", {}) or {}
                name = payload.get("name", "")
                if plugin_manager is None or not plugin_manager.enable(name):
                    await ws.send_json({
                        "type": "plugin_enable_response",
                        "payload": {"ok": False, "name": name, "error": "unknown plugin"},
                    })
                    continue
                # Best-effort SkillLoader hot-reload so the plugin's skills appear.
                try:
                    loader = service_context.get("skill_loader")
                    if loader is not None and hasattr(loader, "reload"):
                        loader.reload()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("skill_loader_reload_failed", error=str(exc))
                await ws.send_json({
                    "type": "plugin_enable_response",
                    "payload": {"ok": True, "name": name},
                })

            elif msg_type == "plugin_disable":
                payload = raw.get("payload", {}) or {}
                name = payload.get("name", "")
                if plugin_manager is None:
                    await ws.send_json({
                        "type": "plugin_disable_response",
                        "payload": {"ok": False, "name": name, "error": "plugin manager not initialized"},
                    })
                    continue
                plugin_manager.disable(name)
                try:
                    loader = service_context.get("skill_loader")
                    if loader is not None and hasattr(loader, "reload"):
                        loader.reload()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("skill_loader_reload_failed", error=str(exc))
                await ws.send_json({
                    "type": "plugin_disable_response",
                    "payload": {"ok": True, "name": name},
                })

            elif msg_type == "skill_marketplace_list":
                # P4-S20 Stage C — fetch + cache the official registry.
                if skill_registry_client is None:
                    await ws.send_json({
                        "type": "skill_marketplace_list_response",
                        "payload": {"skills": [], "error": "marketplace not initialized"},
                    })
                    continue
                _data = await skill_registry_client.fetch()
                await ws.send_json({
                    "type": "skill_marketplace_list_response",
                    "payload": _data,
                })

            elif msg_type == "skill_list_installed":
                # P4-S20 Stage C — list user-installed skills.
                if skill_installer is None:
                    await ws.send_json({
                        "type": "skill_list_installed_response",
                        "payload": {"skills": [], "error": "marketplace not initialized"},
                    })
                    continue
                _installed = []
                for sk in skill_installer.skills_dir.iterdir():
                    if not sk.is_dir():
                        continue
                    sm = sk / "SKILL.md"
                    if not sm.exists():
                        continue
                    try:
                        from deskpet.skills.parser import parse_skill_md
                        meta = parse_skill_md(sm)
                        _installed.append({
                            "name": meta.name,
                            "description": meta.description,
                            "version": meta.version,
                            "path": str(sk),
                            "allowed_tools": list(meta.allowed_tools),
                        })
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("skill_list_parse_failed", path=str(sm), error=str(exc))
                await ws.send_json({
                    "type": "skill_list_installed_response",
                    "payload": {"skills": _installed},
                })

            elif msg_type == "skill_install_from_url":
                # P4-S20 Stage C + post-ship: support both
                #   (a) single-skill mode (root has SKILL.md / manifest.json,
                #       or URL has a subpath) → original pending+confirm flow
                #   (b) multi-skill mode (root has NEITHER → recursive
                #       discovery of every SKILL.md, batch install all,
                #       skip per-skill confirm because the user explicitly
                #       asked to "装能找到的所有 skill")
                payload = raw.get("payload", {}) or {}
                url = payload.get("url", "")
                if skill_installer is None:
                    await ws.send_json({
                        "type": "skill_install_pending",
                        "payload": {"ok": False, "error": "marketplace not initialized"},
                    })
                    continue
                try:
                    batch = await skill_installer.stage_recursive(url)
                    if not batch.multi:
                        # Single-skill mode → preserve original UX (user
                        # sees the manifest, clicks "确认安装").
                        staged = batch.staged[0]
                        _skill_staged[staged.staging_id] = staged
                        await ws.send_json({
                            "type": "skill_install_pending",
                            "payload": {
                                "ok": True,
                                "staging_id": staged.staging_id,
                                "name": staged.name,
                                "manifest": staged.manifest,
                                "permission_categories": list(staged.permission_categories),
                            },
                        })
                    else:
                        # Multi-skill mode → finalize EVERY staged sub-skill
                        # immediately. Per-skill failures (staging or
                        # finalize) come back in the errors list.
                        result = skill_installer.finalize_batch(batch)
                        try:
                            loader = service_context.get("skill_loader")
                            if loader is not None and hasattr(loader, "reload"):
                                loader.reload()
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "skill_loader_reload_failed_batch",
                                error=str(exc),
                            )
                        await ws.send_json({
                            "type": "skill_install_batch_completed",
                            "payload": {
                                "ok": True,
                                "installed": result["installed"],
                                "errors": result["errors"],
                                "installed_count": len(result["installed"]),
                                "error_count": len(result["errors"]),
                            },
                        })
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json({
                        "type": "skill_install_pending",
                        "payload": {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    })

            elif msg_type == "skill_install_confirm":
                # P4-S20 Stage C — finalize or cancel a staged install.
                payload = raw.get("payload", {}) or {}
                staging_id = payload.get("staging_id", "")
                approve = bool(payload.get("approve", False))
                if skill_installer is None or staging_id not in _skill_staged:
                    await ws.send_json({
                        "type": "skill_install_confirm_response",
                        "payload": {"ok": False, "error": "no such staging_id"},
                    })
                    continue
                staged = _skill_staged.pop(staging_id)
                if not approve:
                    skill_installer.cancel(staged)
                    await ws.send_json({
                        "type": "skill_install_confirm_response",
                        "payload": {"ok": False, "reason": "user denied"},
                    })
                    continue
                try:
                    final_path = skill_installer.finalize(staged)
                    # Trigger SkillLoader hot-reload best-effort
                    try:
                        loader = service_context.get("skill_loader")
                        if loader is not None and hasattr(loader, "reload"):
                            loader.reload()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("skill_loader_reload_failed", error=str(exc))
                    await ws.send_json({
                        "type": "skill_install_confirm_response",
                        "payload": {
                            "ok": True,
                            "name": staged.name,
                            "path": str(final_path),
                        },
                    })
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json({
                        "type": "skill_install_confirm_response",
                        "payload": {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    })

            elif msg_type == "skill_uninstall":
                payload = raw.get("payload", {}) or {}
                name = payload.get("name", "")
                if skill_installer is None:
                    await ws.send_json({
                        "type": "skill_uninstall_response",
                        "payload": {"ok": False, "error": "marketplace not initialized"},
                    })
                    continue
                try:
                    skill_installer.uninstall(name)
                    try:
                        loader = service_context.get("skill_loader")
                        if loader is not None and hasattr(loader, "reload"):
                            loader.reload()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("skill_loader_reload_failed", error=str(exc))
                    await ws.send_json({
                        "type": "skill_uninstall_response",
                        "payload": {"ok": True, "name": name},
                    })
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json({
                        "type": "skill_uninstall_response",
                        "payload": {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    })

            elif msg_type == "context_usage_request":
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                try:
                    authoritative = await _read_context_usage_authority(target_sid)
                    await ws.send_json(
                        {"type": "context_usage", "payload": authoritative}
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("context_usage_request_failed err=%s", exc)

            elif msg_type == "context_breakdown_request":
                # 2026-05-28 — drill-down composition for the ring's
                # click-through modal. Returns each context section's
                # token count + a short preview snippet (≤ 400 chars).
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                breakdown = await _compute_context_breakdown(target_sid)
                try:
                    await ws.send_json({
                        "type": "context_breakdown_response",
                        "payload": breakdown,
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.debug("context_breakdown_send_failed err=%s", exc)

            elif msg_type == "sessions_list":
                # 消息面板「历史会话」下拉：列主 Session 及其普通任务历史。
                _sl_sdb = service_context.get("session_db")
                _sl_out: list = []
                if _sl_sdb is not None:
                    try:
                        _sl_rows = await _sl_sdb.list_sessions_with_preview()
                        _sl_gate = service_context.get(
                            "session_terminal_projection_gate"
                        )
                        if _sl_gate is not None:
                            await asyncio.gather(
                                *(
                                    _sl_gate.ensure_current(
                                        str(_row.get("session_id") or "")
                                    )
                                    for _row in _sl_rows
                                    if str(_row.get("session_id") or "")
                                )
                            )
                            # Projection changes previews. Read again only
                            # after every session crossed the consistency gate.
                            _sl_rows = (
                                await _sl_sdb.list_sessions_with_preview()
                            )
                        for _sr in _sl_rows:
                            _ssid = _sr.get("session_id") or ""
                            if _is_companion_history_session_id(_ssid):
                                _sl_out.append(_sr)
                    except Exception as _sl_exc:  # noqa: BLE001
                        logger.warning(
                            "sessions_list_consistency_failed",
                            error=str(_sl_exc),
                        )
                        await ws.send_json({
                            "type": "sessions_list_error",
                            "payload": {
                                "code": "product_view_not_current",
                                "error": (
                                    "会话列表暂时无法确认是最新状态，请稍后重试。"
                                ),
                            },
                        })
                        continue
                await ws.send_json({
                    "type": "sessions_list_response",
                    "payload": {"sessions": _sl_out},
                })

            elif msg_type == "session_delete":
                # 删除一个会话的全部消息（消息面板下拉里每项的 X）。
                _sd_payload = raw.get("payload", {}) or {}
                _sd_sid = _sd_payload.get("session_id") or ""
                _sd_sdb = service_context.get("session_db")
                _sd_ok = False
                if _sd_sdb is not None and _sd_sid:
                    try:
                        _sd_workflows = service_context.get("workflow_service")
                        if _sd_workflows is not None:
                            async with _sd_workflows.session_lock(_sd_sid):
                                await _sd_sdb.clear(_sd_sid)
                                await _sd_workflows.cancel_runs_for_session(
                                    _sd_sid,
                                    reason="session_deleted",
                                )
                        else:
                            await _sd_sdb.clear(_sd_sid)
                        _sd_attempts = service_context.get("context_attempt_store")
                        if _sd_attempts is not None:
                            _sd_attempts.purge_session(_sd_sid)
                        _sd_ok = True
                        logger.info("session_deleted sid=%s", _sd_sid)
                    except Exception as _sd_exc:  # noqa: BLE001
                        logger.warning(
                            "session_delete_failed", error=str(_sd_exc),
                            session_id=_sd_sid,
                        )
                await ws.send_json({
                    "type": "session_deleted",
                    "payload": {"session_id": _sd_sid, "ok": _sd_ok},
                })

            elif msg_type == "session_rename":
                # 消息面板「重命名话题」：写自定义标题（空=还原自动预览）。
                _sr_payload = raw.get("payload", {}) or {}
                _sr_sid = _sr_payload.get("session_id") or ""
                _sr_title = _sr_payload.get("title") or ""
                _sr_sdb = service_context.get("session_db")
                _sr_ok = False
                _sr_stored = ""
                if _sr_sdb is not None and _sr_sid:
                    try:
                        _sr_stored = await _sr_sdb.set_session_title(_sr_sid, _sr_title)
                        _sr_ok = True
                        logger.info(
                            "session_renamed sid=%s title_len=%d", _sr_sid, len(_sr_stored)
                        )
                    except Exception as _sr_exc:  # noqa: BLE001
                        logger.warning(
                            "session_rename_failed", error=str(_sr_exc),
                            session_id=_sr_sid,
                        )
                await ws.send_json({
                    "type": "session_renamed",
                    "payload": {
                        "session_id": _sr_sid,
                        "title": _sr_stored,
                        "ok": _sr_ok,
                    },
                })

            elif msg_type == "session_messages_load":
                # P4-S23: panel reload (F5) needs to rehydrate chat
                # history from SessionDB. Returns messages for the
                # given session_id (default: ws session_id).
                # Capped at 200 msgs/session to keep payload sane —
                # users can scroll older messages via a future
                # paginated load if needed.
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                limit = int(payload.get("limit") or 200)
                sdb = service_context.get("session_db")
                msgs: list = []
                _companion_history_events: list[dict[str, Any]] = []
                if sdb is not None:
                    try:
                        _history_gate = service_context.get(
                            "session_terminal_projection_gate"
                        )
                        if _history_gate is not None:
                            await _history_gate.ensure_current(target_sid)
                        try:
                            _history_identity = _companion_identity_gate.freeze()
                            await _bind_companion_inbox_route(
                                target_sid,
                                _history_identity,
                                create_if_absent=False,
                            )
                        except Exception as _history_route_exc:  # noqa: BLE001
                            logger.debug(
                                "companion_history_route_unchanged sid=%s err=%s",
                                target_sid,
                                type(_history_route_exc).__name__,
                            )
                        rows = await sdb.get_messages(target_sid, limit=limit)
                        # P6 bugfix 2026-05-14: 历史还原 — 之前过滤了
                        # role not in (user,assistant) 和 empty content,
                        # 导致 tool_call (role='assistant' content=''
                        # tool_calls=[...]) 和 tool_result (role='tool')
                        # 全部被丢，UI 重启后只剩 user 气泡。现在全部
                        # 返回，前端 ws.ts 决定怎么渲染。
                        msgs = []
                        _history_tool_names: dict[str, str] = {}
                        for _history_row in rows:
                            _history_calls = _history_row.get("tool_calls")
                            if not _history_calls:
                                continue
                            try:
                                _history_calls = (
                                    json.loads(_history_calls)
                                    if isinstance(_history_calls, str)
                                    else _history_calls
                                )
                                if isinstance(_history_calls, list):
                                    for _history_call in _history_calls:
                                        if not isinstance(_history_call, dict):
                                            continue
                                        _history_function = _history_call.get("function")
                                        if isinstance(_history_function, dict):
                                            _history_tool_names[str(_history_call.get("id") or "")] = str(
                                                _history_function.get("name") or ""
                                            )
                            except Exception:
                                continue
                        for r in rows:
                            if str(r.get("projection_kind") or "") == "companion_event":
                                # Companion rows are hydrated through the
                                # owner-fenced visibility overlay below, never
                                # as ordinary assistant text.
                                continue
                            _row_role = r.get("role") or ""
                            if _row_role not in ("user", "assistant", "tool"):
                                continue  # 仍跳过 system 等
                            _entry: dict[str, Any] = {
                                "id": str(
                                    r.get("workflow_event_id") or r.get("id") or ""
                                ),
                                "role": _row_role,
                                "text": r.get("content") or "",
                                "ts": float(r.get("created_at") or 0) * 1000,
                                "projection_kind": str(
                                    r.get("projection_kind") or ""
                                ),
                                "workflow_event_id": str(
                                    r.get("workflow_event_id") or ""
                                ),
                            }
                            if r.get("root_run_id"):
                                _entry["run_id"] = str(r["root_run_id"])
                            if r.get("task_scope_id"):
                                _entry["task_scope_id"] = str(
                                    r["task_scope_id"]
                                )
                            # tool_calls JSON (assistant 行调工具时)
                            _tcs_raw = r.get("tool_calls")
                            if _tcs_raw:
                                try:
                                    import json as _load_json
                                    _entry["tool_calls"] = project_public_tool_calls(
                                        _load_json.loads(_tcs_raw)
                                        if isinstance(_tcs_raw, str) else _tcs_raw
                                    )
                                except Exception:
                                    pass
                            # tool_call_id (tool 行的回指)
                            _tcid = r.get("tool_call_id")
                            if _tcid:
                                _entry["tool_call_id"] = _tcid
                                if _row_role == "tool":
                                    _public_history_result = project_public_tool_result(
                                        _history_tool_names.get(str(_tcid), ""),
                                        r.get("content") or "",
                                    )
                                    _entry["text"] = (
                                        json.dumps(_public_history_result, ensure_ascii=False)
                                        if not isinstance(_public_history_result, str)
                                        else _public_history_result
                                    )
                            msgs.append(_entry)

                        _workflow_history = service_context.get("workflow_service")
                        await _attach_workflow_history_events(
                            rows=rows,
                            messages=msgs,
                            session_id=target_sid,
                            session_db=sdb,
                            workflow_service=_workflow_history,
                        )
                        _companion_history_events = (
                            await _attach_companion_history_events(rows=rows)
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "session_messages_consistency_failed",
                            error=str(exc), session_id=target_sid,
                        )
                        await ws.send_json({
                            "type": "session_messages_error",
                            "payload": {
                                "session_id": target_sid,
                                "code": "product_view_not_current",
                                "error": (
                                    "会话记录暂时无法确认是最新状态，请稍后重试。"
                                ),
                            },
                        })
                        continue
                # FEAT-A4: 带回 awaiting plan（不塞进被白名单 3659 过滤的 messages
                # 流，单独挂 payload.plan）。前端 rehydration 据此重建 plan card +
                # [执行]/[取消] 栏。只在 awaiting 时附；否则字段缺省 → 旧行为不变。
                _plan_payload: dict[str, Any] | None = None
                if sdb is not None:
                    try:
                        _sp = await sdb.get_session_plan(target_sid)
                        if _sp is not None and _sp.get("awaiting"):
                            _plan_payload = {
                                "rationale": _sp.get("rationale") or "",
                                "steps": _sp.get("steps") or [],
                                "target_directory": _sp.get(
                                    "target_directory"
                                ),
                                "action_categories": _sp.get(
                                    "action_categories"
                                )
                                or [],
                                "auto_confirmed": bool(
                                    _sp.get("auto_confirmed")
                                ),
                                "awaiting": True,
                            }
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(
                            "session_plan_load_failed sid=%s err=%s",
                            target_sid, exc,
                        )
                _msgs_payload: dict[str, Any] = {
                    "session_id": target_sid, "messages": msgs,
                }
                _msgs_payload["companion_events"] = _companion_history_events
                if _plan_payload is not None:
                    _msgs_payload["plan"] = _plan_payload
                await ws.send_json({
                    "type": "session_messages_response",
                    "payload": _msgs_payload,
                })

            elif msg_type == "slash_command":
                # WI-A1 v1 — /command 解析（plans/2026-05-25-companion-code-
                # skill-upgrade）。InputBar 解析 `/<cmd> [args]` → 发本 WS
                # 消息；后端直接路由到 SkillLoader / goal_store / builtins，
                # 不走 AgentLoop。feature flag `[features] slash_commands`
                # 默认 OFF；OFF 时返 disabled 错。
                payload = raw.get("payload", {}) or {}
                cmd_name = (payload.get("command") or "").lstrip("/")
                cmd_args = payload.get("args") or ""
                target_sid = payload.get("session_id") or session_id
                _slash_enabled = bool(getattr(
                    getattr(config, "features", None), "slash_commands", False,
                ))
                if not _slash_enabled:
                    await ws.send_json({
                        "type": "slash_command_result",
                        "payload": {
                            "command": cmd_name,
                            "session_id": target_sid,
                            "result": {
                                "type": "error",
                                "message": "slash_commands feature disabled "
                                           "(set [features] slash_commands = true)",
                            },
                        },
                    })
                else:
                    try:
                        from deskpet.commands import dispatch_slash_command
                        _slash_skill_loader = (
                            service_context.get(
                                "managed_skill_discovery_projection"
                            )
                            or service_context.get("skill_loader")
                        )
                        _slash_skill_catalog = _slash_skill_loader
                        _slash_goal_store = service_context.get("session_goal_store")
                        _slash_pref_mem = service_context.get("preference_memory")
                        _slash_result = await dispatch_slash_command(
                            cmd_name, cmd_args, target_sid,
                            skill_loader=_slash_skill_loader,
                            skill_catalog=_slash_skill_catalog,
                            session_goal_store=_slash_goal_store,
                            session_pref_memory=_slash_pref_mem,
                        )
                    except Exception as _slash_exc:  # noqa: BLE001
                        logger.warning(
                            "slash_command dispatch failed: %s", _slash_exc,
                        )
                        _slash_result = {
                            "type": "error",
                            "message": f"dispatch error: {_slash_exc}",
                        }
                    if _slash_result.get("type") == (
                        "instruction_activation_request"
                    ):
                        _slash_text = str(
                            _slash_result.get("user_text") or ""
                        ).strip()
                        if not _slash_text:
                            raise RuntimeError(
                                "slash instruction activation is empty"
                            )
                        _launch_product_harness_chat(
                            ws,
                            _slash_text,
                            target_sid,
                            client_request_id=(
                                str(
                                    payload.get("request_id")
                                    or raw.get("request_id")
                                    or ""
                                ).strip()
                                or None
                            ),
                            client_turn_id=(
                                str(payload.get("turn_id") or "").strip()
                                or None
                            ),
                        )
                        _slash_result = {
                            "type": "accepted",
                            "route": "product_harness",
                            "skill_name": cmd_name,
                            "prepared_skill_scope": _slash_result.get(
                                "prepared_skill_scope"
                            ),
                        }
                    await ws.send_json({
                        "type": "slash_command_result",
                        "payload": {
                            "command": cmd_name,
                            "session_id": target_sid,
                            "result": _slash_result,
                        },
                    })

            elif msg_type == "chat_v2_interrupt":
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                target_run_id = str(payload.get("run_id") or "").strip()
                cancelled = await _cancel_product_harness_run(
                    target_sid, target_run_id, reason="user_interrupt"
                )
                _interrupt_evt = {
                    "type": "chat_v2_interrupted",
                    "payload": {
                        "session_id": target_sid,
                        "run_id": target_run_id,
                        "cancelled": cancelled,
                    },
                }
                await ws.send_json(_interrupt_evt)
                await _broadcast_default_chat_peers(ws, _interrupt_evt)

            elif msg_type == "plan_confirm":
                # superpowers 决策2 — plan-confirm 硬门的用户裁决。
                # 前端 PlanCard 的 [执行]/[取消] 按钮发来 {session_id, decision}。
                # R6 只接受完整 durable decision fence。
                payload = raw.get("payload", {}) or {}
                _csid = payload.get("session_id") or session_id
                _decision = payload.get("decision") or "go"
                if _harness_accepting and _harness_runtime is not None:
                    _run_id = str(payload.get("run_id") or "").strip()
                    _decision_id = str(payload.get("decision_id") or "").strip()
                    _nonce = str(payload.get("nonce") or "").strip()
                    _version = payload.get("version")
                    if not _run_id or not _decision_id or not _nonce or _version is None:
                        raise ValueError("plan confirmation requires the durable decision fence")
                    _host, _provider, _chain, _snapshot = await _issue_product_harness_host(
                        _csid,
                        workspace=None,
                    )
                    _receipt = await _harness_runtime.run_client.signal(
                        {"run_id": _run_id, "expected_session_id": _csid},
                        _host,
                        {
                            "decision_id": _decision_id,
                            "nonce": _nonce,
                            "version": int(_version),
                            "response": {"decision": _decision},
                        },
                    )
                    logger.info(
                        "harness_plan_decision sid=%s run_id=%s accepted=%s duplicate=%s",
                        _csid,
                        _run_id,
                        _receipt.accepted,
                        _receipt.duplicate,
                    )
                    _sdb_pc = service_context.get("session_db")
                    if _sdb_pc is not None:
                        await _sdb_pc.clear_session_plan_awaiting(_csid)
                    continue
            elif msg_type == "models_list":
                # The session model picker is data-driven. Pull the
                # relay's live catalog (中转站 GET /models); fall
                # back to the registry endpoint's configured `models`
                # array if the live fetch fails. Each model carries a
                # per-family capability map so the picker only shows the
                # params that model actually supports (gpt-5.x exposes
                # reasoning_effort; claude opus/sonnet exposes thinking;
                # they differ).
                _reg = service_context.get("provider_registry")
                _model_ids: list[str] = []
                _source = "none"
                _base_url = ""
                # 生效的 provider 默认模型：preferred_model 留空(跟随 provider 默认)
                # 时,UI 据此显示「当前真正用的模型」而非「默认模型」占位词。
                _default_model = ""
                if _reg is not None:
                    try:
                        _chain = _reg.get_chain()
                    except Exception:
                        _chain = []
                    if _chain:
                        _first = _chain[0]
                        _pid0 = _first.get("id") if isinstance(_first, dict) else None
                        _entry0 = _reg.get_entry(_pid0) if _pid0 else None
                        if _entry0 is not None:
                            _base_url = str(getattr(_entry0, "base_url", "") or "")
                            # ProviderEntry.model = default_model or models[0]。
                            _default_model = str(getattr(_entry0, "model", "") or "")
                            _cfg_models = list(getattr(_entry0, "models", []) or [])
                            _api_key = _reg.resolve_api_key(_pid0)
                            try:
                                from llm.model_catalog import fetch_models as _fm
                                _live = await _fm(_base_url, _api_key, timeout=8.0)
                            except Exception:
                                _live = []
                            if _live:
                                _model_ids = _live
                                _source = "live"
                            elif _cfg_models:
                                _model_ids = _cfg_models
                                _source = "config"
                # Fallback: provider_registry chain 为空时（典型场景：用户登录
                # 中转站后，relay provider 只是前端"虚拟项"、不走 settings_providers_add
                # 注册进 backend registry，见 SettingsProviders.tsx），用当前
                # local_llm（登录后经 update_cloud_config 已切到中转站 base_url+key）
                # 拉中转站 /models。这样登录中转站即可在当前会话换模型，无需手动
                # 再"添加 Provider"。
                if not _model_ids and local_llm is not None:
                    _ll_base = str(getattr(local_llm, "base_url", "") or "")
                    _ll_key = getattr(local_llm, "api_key", None)
                    # 跳过本地 ollama（localhost）—— 它的 /models 是本地小模型，
                    # 不是中转站目录，且 key 通常是占位 "ollama"。
                    if _ll_base and "localhost" not in _ll_base and "127.0.0.1" not in _ll_base:
                        try:
                            from llm.model_catalog import fetch_models as _fm2
                            _live_ll = await _fm2(_ll_base, _ll_key, timeout=8.0)
                        except Exception:
                            _live_ll = []
                        if _live_ll:
                            _model_ids = _live_ll
                            _source = "live-local_llm"
                            _base_url = _ll_base
                # registry chain 为空时,生效默认模型取 local_llm 的运行时 model
                # (onboarding 登录中转站后已写 gpt-5.5)。
                if not _default_model and local_llm is not None:
                    _default_model = str(getattr(local_llm, "model", "") or "")
                from llm.model_catalog import build_catalog as _bc
                await ws.send_json({
                    "type": "models_list_response",
                    "payload": {
                        "models": _bc(_model_ids),
                        "source": _source,
                        "base_url": _base_url,
                        "default_model": _default_model,
                    },
                })

            elif msg_type == "session_provider_get":
                payload = raw.get("payload", {}) or {}
                target_sid = str(payload.get("session_id") or "").strip()
                session_db = service_context.get("session_db")
                readiness = service_context.get("provider_routing_readiness")
                if not target_sid or session_db is None:
                    await ws.send_json({
                        "type": "settings_providers_error",
                        "payload": {
                            "reason": "missing_field",
                            "detail": "session_id required + session_db must be initialized",
                        },
                    })
                    continue
                try:
                    readiness.require_ready()
                except Exception:
                    await ws.send_json({
                        "type": "settings_providers_error",
                        "payload": {
                            "reason": "provider_routing_initializing",
                            "detail": "provider routing is not ready",
                        },
                    })
                    continue
                binding = await session_db.get_session_provider_binding_authority(
                    target_sid
                )
                await ws.send_json({
                    "type": "session_provider_binding",
                    "payload": {
                        "session_id": target_sid,
                        "provider_id": binding.get("provider_id"),
                        "preferred_model": binding.get("preferred_model"),
                        "model_params": binding.get("model_params"),
                        "provider_incarnation_id": binding.get("provider_incarnation_id"),
                        "provider_config_revision": binding.get("provider_config_revision"),
                        "binding_epoch": binding.get("binding_epoch", 0),
                    },
                })

            elif msg_type in ("session_set_provider", "session_set_model"):
                payload = raw.get("payload", {}) or {}
                target_sid = str(payload.get("session_id") or "").strip()
                session_db = service_context.get("session_db")
                registry = service_context.get("provider_registry")
                readiness = service_context.get("provider_routing_readiness")
                if not target_sid or session_db is None or registry is None:
                    await ws.send_json({
                        "type": "settings_providers_error",
                        "payload": {
                            "reason": "missing_field",
                            "detail": "session_id required + session_db must be initialized",
                        },
                    })
                    continue
                if "expected_binding_epoch" not in payload:
                    await ws.send_json({
                        "type": "settings_providers_error",
                        "payload": {
                            "reason": "provider_binding_conflict",
                            "detail": "expected_binding_epoch is required",
                            "session_id": target_sid,
                        },
                    })
                    continue
                try:
                    readiness.require_ready()
                except Exception:
                    await ws.send_json({
                        "type": "settings_providers_error",
                        "payload": {
                            "reason": "provider_routing_initializing",
                            "detail": "provider routing is not ready",
                        },
                    })
                    continue
                current = await session_db.get_session_provider_binding_authority(
                    target_sid
                )
                if msg_type == "session_set_provider":
                    new_provider_id = payload.get("provider_id")
                    new_model = current.get("preferred_model")
                    new_params = current.get("model_params")
                    response_type = "session_provider_set"
                else:
                    new_provider_id = current.get("provider_id")
                    new_model = payload.get("model")
                    raw_params = payload.get("params")
                    new_params = (
                        raw_params if isinstance(raw_params, dict) else None
                    )
                    response_type = "session_model_set"
                try:
                    if new_provider_id and (
                        "expected_provider_incarnation_id" not in payload
                        or "expected_provider_config_revision" not in payload
                    ):
                        raise ValueError("provider identity expectation is required")
                    binding = await registry.set_session_binding(
                        session_db,
                        session_id=target_sid,
                        provider_id=new_provider_id,
                        preferred_model=new_model,
                        model_params=new_params,
                        expected_binding_epoch=int(payload["expected_binding_epoch"]),
                        expected_incarnation_id=payload.get(
                            "expected_provider_incarnation_id"
                        ),
                        expected_config_revision=(
                            int(payload["expected_provider_config_revision"])
                            if payload.get("expected_provider_config_revision") is not None
                            else None
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    authoritative = await session_db.get_session_provider_binding_authority(
                        target_sid
                    )
                    await ws.send_json({
                        "type": "settings_providers_error",
                        "payload": {
                            "reason": (
                                "provider_binding_conflict"
                                if str(exc) == "provider_binding_conflict"
                                or isinstance(exc, ValueError)
                                else "internal_error"
                            ),
                            "detail": str(exc),
                            "session_id": target_sid,
                        },
                    })
                    await ws.send_json({
                        "type": "session_provider_binding",
                        "payload": {"session_id": target_sid, **authoritative},
                    })
                else:
                    await ws.send_json({
                        "type": response_type,
                        "payload": {
                            "session_id": target_sid,
                            **binding,
                        },
                    })
                    # The provider/model binding and Context Usage authority
                    # advance in the same durable transaction.  Push the
                    # freshly committed binding-only snapshot immediately so
                    # a new Session does not keep rendering its pre-binding
                    # "no model yet" cache until the first LLM call.
                    try:
                        await _send_context_usage_authority(
                            ws, session_db, target_sid
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(
                            "context_usage_binding_push_failed sid=%s err=%s",
                            target_sid,
                            exc,
                        )

            # 2026-06-13: 此处原有一个简版 "model_context_set" 分支(只认
            # {model, context_window}) —— 它在 elif 链里抢在 p4_ipc(6456)
            # 之前,把 ModelContextCard 的完整协议请求({scope, model,
            # fields})劫持并报错,坏了设置卡的保存两天。已删,统一走
            # p4_ipc 完整版(fields 白名单含 context_window/compact_at_pct,
            # scope global/project 深合并 TOML)。ChangeModelModal 的档位
            # 选择也已改发完整协议。

            elif msg_type in (
                "settings_providers_list_request",
                "settings_providers_add",
                "settings_providers_update",
                "settings_providers_remove",
                "settings_providers_reorder",
                "settings_providers_ensure",
                "settings_providers_relay_logout",
            ):
                # P5-S2 multi-provider-management Phase 2:
                # CRUD + reorder against LLMProviderRegistry. Mutations
                # broadcast a `providers_changed` event to ALL control
                # connections so multi-window UIs (pet panel + code panel)
                # stay in sync without polling.
                #
                # Spec: openspec/changes/multi-provider-management/specs/
                #       frontend-ipc-surface/spec.md
                _payload = raw.get("payload", {}) or {}
                _reg = service_context.get("provider_registry")
                if _reg is None:
                    await ws.send_json({
                        "type": "settings_providers_error",
                        "payload": {
                            "reason": "registry_unavailable",
                            "detail": "provider_registry not initialized",
                        },
                    })
                    continue

                async def _broadcast_providers_changed() -> None:
                    """Fan out the new provider list to every open control
                    ws. Mirrors the pattern used by `_todo_broadcaster` /
                    supervisor alert broadcaster."""
                    snapshot = {
                        "type": "providers_changed",
                        "payload": {"providers": _reg.list_providers()},
                    }
                    if not _control_connections:
                        return
                    for _sid_key, _ws_obj in list(_control_connections.items()):
                        try:
                            await _ws_obj.send_json(snapshot)
                        except Exception as _bex:  # noqa: BLE001
                            logger.debug(
                                "providers_changed_broadcast_failed sid=%s err=%s",
                                _sid_key, _bex,
                            )

                if msg_type == "settings_providers_list_request":
                    await ws.send_json({
                        "type": "settings_providers_list_response",
                        "payload": {"providers": _reg.list_providers()},
                    })

                elif msg_type == "settings_providers_add":
                    try:
                        entry = await _reg.add_provider(_payload)
                    except ValueError as exc:
                        # Classify error: duplicate vs missing_field vs
                        # invalid_id. The registry raises a single
                        # ValueError for all of these; we sniff the text.
                        _msg = str(exc)
                        if "already exists" in _msg:
                            _reason = "duplicate_id"
                        elif "missing required fields" in _msg or "api_key is required" in _msg:
                            _reason = "missing_field"
                        elif "invalid provider id" in _msg:
                            _reason = "invalid_id"
                        else:
                            _reason = "invalid_payload"
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": _reason, "detail": _msg},
                        })
                    except Exception as exc:  # noqa: BLE001
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "internal_error", "detail": str(exc)},
                        })
                    else:
                        await ws.send_json({
                            "type": "settings_providers_added",
                            "payload": {"provider": entry.to_public_dict()},
                        })
                        await _broadcast_providers_changed()

                elif msg_type == "settings_providers_ensure":
                    # WI-2: decision logic lives in llm.relay_provider_ops
                    # (unit-testable without importing main.py). Returns an
                    # error payload or None on success.
                    # WI-6 kill-switch: reject when the backend feature flag is
                    # OFF (primary gate is the frontend RELAY_MANAGED_PROVIDER,
                    # which decides whether to send this at all).
                    if not config.features.relay_managed_provider:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {
                                "reason": "relay_managed_disabled",
                                "detail": "relay_managed_provider feature off",
                            },
                        })
                    else:
                        _payload = raw.get("payload", {}) or {}
                        _err = await ensure_relay_provider(_reg, _payload)
                        if _err is not None:
                            await ws.send_json({
                                "type": "settings_providers_error",
                                "payload": _err,
                            })
                        else:
                            _refresh_research_live_llm()
                            await _broadcast_providers_changed()

                elif msg_type == "settings_providers_relay_logout":
                    await relay_logout(_reg)
                    _refresh_research_live_llm()
                    await _broadcast_providers_changed()

                elif msg_type == "settings_providers_update":
                    _pid = _payload.get("id")
                    _patch = _payload.get("patch", {}) or {}
                    if (
                        not _pid
                        or "expected_incarnation_id" not in _payload
                        or "expected_config_revision" not in _payload
                    ):
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {
                                "reason": "missing_field",
                                "detail": "id and expected provider identity are required",
                            },
                        })
                        continue
                    try:
                        entry = await _reg.update_provider(
                            _pid,
                            expected_incarnation_id=_payload.get("expected_incarnation_id"),
                            expected_config_revision=int(
                                _payload["expected_config_revision"]
                            ),
                            **_patch,
                        )
                    except KeyError:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "not_found", "detail": f"provider {_pid!r} not found"},
                        })
                    except ProviderMutationConflict as exc:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {
                                "reason": "provider_binding_conflict",
                                "detail": str(exc),
                            },
                        })
                    except ValueError as exc:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "invalid_payload", "detail": str(exc)},
                        })
                    except Exception as exc:  # noqa: BLE001
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "internal_error", "detail": str(exc)},
                        })
                    else:
                        await ws.send_json({
                            "type": "settings_providers_updated",
                            "payload": {"provider": entry.to_public_dict()},
                        })
                        _refresh_research_live_llm()
                        await _broadcast_providers_changed()

                elif msg_type == "settings_providers_remove":
                    _pid = _payload.get("id")
                    if (
                        not _pid
                        or "expected_incarnation_id" not in _payload
                        or "expected_config_revision" not in _payload
                    ):
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {
                                "reason": "missing_field",
                                "detail": "id and expected provider identity are required",
                            },
                        })
                        continue
                    try:
                        await _reg.remove_provider(
                            _pid,
                            expected_incarnation_id=_payload.get("expected_incarnation_id"),
                            expected_config_revision=int(
                                _payload["expected_config_revision"]
                            ),
                        )
                    except KeyError:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "not_found", "detail": f"provider {_pid!r} not found"},
                        })
                    except ProviderMutationConflict as exc:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {
                                "reason": "provider_binding_conflict",
                                "detail": str(exc),
                            },
                        })
                    except Exception as exc:  # noqa: BLE001
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "internal_error", "detail": str(exc)},
                        })
                    else:
                        await ws.send_json({
                            "type": "settings_providers_removed",
                            "payload": {"id": _pid},
                        })
                        await _broadcast_providers_changed()

                elif msg_type == "settings_providers_reorder":
                    _ordered = _payload.get("ordered_ids") or []
                    _expected_raw = _payload.get("expected_versions")
                    try:
                        if not isinstance(_expected_raw, dict):
                            raise ValueError("expected_versions is required")
                        _expected_versions = {
                            str(pid): (
                                str(value["incarnation_id"]),
                                int(value["config_revision"]),
                            )
                            for pid, value in _expected_raw.items()
                            if isinstance(value, dict)
                        }
                        await _reg.reorder(
                            list(_ordered), expected_versions=_expected_versions
                        )
                    except ProviderMutationConflict as exc:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {
                                "reason": "provider_binding_conflict",
                                "detail": str(exc),
                            },
                        })
                    except ValueError as exc:
                        _msg = str(exc)
                        # Compute the missing-id detail for nicer UX.
                        _have = {p["id"] for p in _reg.list_providers()}
                        _missing = sorted(_have - set(_ordered))
                        _detail = f"missing: {', '.join(_missing)}" if _missing else _msg
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "incomplete_order", "detail": _detail},
                        })
                    except Exception as exc:  # noqa: BLE001
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "internal_error", "detail": str(exc)},
                        })
                    else:
                        await ws.send_json({
                            "type": "settings_providers_reordered",
                            "payload": {"providers": _reg.list_providers()},
                        })
                        await _broadcast_providers_changed()

            elif msg_type == "settings_providers_probe_models":
                # P5-S2 v2: GET <base_url>/models for an OpenAI-compat endpoint
                # to discover available models. Used by AddProviderModal "🔍
                # auto-fetch" so users don't have to type model names by hand.
                # Reply: { ok, models: [string], detail? }
                _payload = raw.get("payload", {}) or {}
                _base_url = str(_payload.get("base_url") or "").rstrip("/")
                _api_key = str(_payload.get("api_key") or "")
                if not _base_url:
                    await ws.send_json({
                        "type": "settings_providers_probe_models_response",
                        "payload": {
                            "ok": False,
                            "models": [],
                            "detail": "base_url required",
                        },
                    })
                    continue
                try:
                    import httpx as _httpx
                    _models_url = f"{_base_url}/models"
                    _headers = {"Accept": "application/json"}
                    if _api_key and _api_key.lower() != "ollama":
                        _headers["Authorization"] = f"Bearer {_api_key}"
                    async with _httpx.AsyncClient(timeout=15.0) as _hc:
                        _resp = await _hc.get(_models_url, headers=_headers)
                    if _resp.status_code != 200:
                        await ws.send_json({
                            "type": "settings_providers_probe_models_response",
                            "payload": {
                                "ok": False,
                                "models": [],
                                "detail": f"HTTP {_resp.status_code}: {_resp.text[:200]}",
                            },
                        })
                    else:
                        _body = _resp.json()
                        # OpenAI-format: {"data": [{"id": "..."}, ...]}
                        # Ollama-format: {"models": [{"name": "..."}, ...]}
                        # tolerate either.
                        _items = _body.get("data") if isinstance(_body, dict) else None
                        if not isinstance(_items, list):
                            _items = _body.get("models") if isinstance(_body, dict) else None
                        if not isinstance(_items, list):
                            _items = []
                        _names: list[str] = []
                        for _it in _items:
                            if isinstance(_it, dict):
                                _n = _it.get("id") or _it.get("name") or _it.get("model")
                                if _n:
                                    _names.append(str(_n))
                            elif isinstance(_it, str):
                                _names.append(_it)
                        await ws.send_json({
                            "type": "settings_providers_probe_models_response",
                            "payload": {
                                "ok": True,
                                "models": _names,
                            },
                        })
                except Exception as _probe_exc:  # noqa: BLE001
                    await ws.send_json({
                        "type": "settings_providers_probe_models_response",
                        "payload": {
                            "ok": False,
                            "models": [],
                            "detail": str(_probe_exc)[:300],
                        },
                    })

            elif msg_type in ("chat", "chat_v2"):
                # P4-S20-LLM-Unified: 单一对话路径。
                # 永远走 AgentLoop tool_use loop（即原 chat_v2 路径），
                # 同时保留 ContextAssembler 长期记忆召回 + BillingLedger
                # 计费（继承自原 chat 路径）。
                #
                # CRITICAL: run in a background task so the WS recv
                # loop keeps draining permission_response messages —
                # gate's responder Future never gets set otherwise.
                #
                # `chat_v2` 类型保留作为别名，前端无感升级。
                # Payload may carry an explicit task `session_id`; honor it
                # and otherwise fall back to the WS-level main session.
                _payload = raw.get("payload", {}) or {}
                text = _payload.get("text", "")
                from deskpet.agent.attachment_budget import (
                    normalize_user_attachment_blocks as _normalize_attachment_blocks,
                )
                _attachment_blocks = _normalize_attachment_blocks(
                    _payload.get("attachments")
                )
                _msg_sid = _payload.get("session_id") or session_id
                _base_msg_sid = _msg_sid
                _scope_decision = _resolve_chat_task_scope(
                    base_sid=_msg_sid,
                    text=text,
                    payload=_payload,
                )
                _msg_sid = _scope_decision.effective_sid
                text = _scope_decision.stripped_text
                _memory_policy_override = (
                    {"l2_page_in": "always"}
                    if getattr(
                        _scope_decision,
                        "force_l2_page_in",
                        None,
                    ) == "always"
                    else None
                )
                _identity_gate = service_context.get(
                    "companion_identity_gate"
                )
                try:
                    if _identity_gate is None:
                        raise RuntimeError("companion_identity_not_ready")
                    # Freeze now, before a background Run is created. Later
                    # owner-aware boundaries consume this immutable snapshot;
                    # account switches never rewrite an in-flight Turn.
                    _frozen_companion_owner = _identity_gate.freeze()
                    _companion_route_bound = await _bind_companion_inbox_route(
                        _msg_sid,
                        _frozen_companion_owner,
                        create_if_absent=True,
                    )
                except Exception as _identity_exc:  # noqa: BLE001
                    await ws.send_json(
                        {
                            "type": "chat_v2_error",
                            "payload": {
                                "error": "companion_identity_not_ready",
                                "code": "companion_identity_not_ready",
                                "retryable": True,
                                "session_id": _msg_sid,
                            },
                        }
                    )
                    logger.info(
                        "companion_chat_blocked_identity_not_ready",
                        session_id=_msg_sid,
                        error=type(_identity_exc).__name__,
                    )
                    continue
                if not _companion_route_bound:
                    _route_error = (
                        "新会话创建失败，请重试。"
                        if _scope_decision.created
                        else "当前选择的是只读历史会话，请重新发送。"
                    )
                    _route_code = (
                        "companion_session_create_failed"
                        if _scope_decision.created
                        else "companion_session_read_only"
                    )
                    await ws.send_json(
                        {
                            "type": "chat_v2_error",
                            "payload": {
                                "error": _route_error,
                                "code": _route_code,
                                "retryable": bool(_scope_decision.created),
                                "session_id": _msg_sid,
                            },
                        }
                    )
                    logger.info(
                        "companion_chat_blocked_read_only_session",
                        session_id=_msg_sid,
                    )
                    continue
                if _scope_decision.created:
                    try:
                        _inherited_model_binding = (
                            await service_context.get(
                                "provider_registry"
                            ).inherit_session_binding(
                                session_db,
                                source_session_id=_base_msg_sid,
                                target_session_id=_msg_sid,
                                expected_binding_epoch=0,
                            )
                        )
                    except Exception as _model_inherit_exc:  # noqa: BLE001
                        await ws.send_json(
                            {
                                "type": "chat_v2_error",
                                "payload": {
                                    "error": "新会话模型配置继承失败，请重试。",
                                    "code": "session_model_inherit_failed",
                                    "retryable": True,
                                    "session_id": _base_msg_sid,
                                },
                            }
                        )
                        logger.exception(
                            "session_model_inherit_failed",
                            source_session_id=_base_msg_sid,
                            target_session_id=_msg_sid,
                            error=type(_model_inherit_exc).__name__,
                            detail=str(_model_inherit_exc)[:240],
                        )
                        continue
                    # The control socket keeps a stable transport id while
                    # the visible conversation switches to this fresh,
                    # owner-fenced UUID session.
                    _remap_chat_peer_group(session_id, _msg_sid)
                    _switch_payload = {
                        "old_sid": _base_msg_sid,
                        "new_sid": _msg_sid,
                        "reason": _scope_decision.reason,
                        "provider_id": _inherited_model_binding.get("provider_id"),
                        "preferred_model": _inherited_model_binding.get(
                            "preferred_model"
                        ),
                        "model_params": _inherited_model_binding.get("model_params"),
                    }
                    for _evt_type in (
                        "session_switched",
                        "task_session_started",
                    ):
                        _switch_evt = {
                            "type": _evt_type,
                            "payload": dict(_switch_payload),
                        }
                        try:
                            await ws.send_json(_switch_evt)
                        except Exception:
                            pass
                        await _broadcast_default_chat_peers(
                            ws, _switch_evt
                        )
                    await _send_new_session_origin_user_echo(
                        ws, _msg_sid, text
                    )
                # Clicking an empty "new topic" still creates and switches
                # session above, but must not launch an empty AgentLoop turn.
                if not (text or "").strip():
                    continue
                if (
                    deskpet_tool_registry_v2 is None
                    or permission_gate_v2 is None
                ):
                    await ws.send_json({
                        "type": "chat_v2_error",
                        "payload": {"error": "v2 stack not initialized", "session_id": _msg_sid},
                    })
                    continue
                _target_root_run_id = str(
                    _payload.get("target_root_run_id") or ""
                ).strip()
                _target_task_scope_id = str(
                    _payload.get("task_scope_id") or ""
                ).strip()
                if bool(_target_root_run_id) != bool(
                    _target_task_scope_id
                ):
                    await ws.send_json(
                        {
                            "type": "chat_v2_error",
                            "payload": {
                                "error": (
                                    "continuation requires both "
                                    "target_root_run_id and task_scope_id"
                                ),
                                "session_id": _msg_sid,
                                "run_id": _target_root_run_id,
                                "task_scope_id": _target_task_scope_id,
                            },
                        }
                    )
                    continue
                if _target_root_run_id:
                    _boundary_version = _payload.get(
                        "conversation_boundary_version"
                    )
                    if _boundary_version is None:
                        await ws.send_json(
                            {
                                "type": "chat_v2_error",
                                "payload": {
                                    "error": (
                                        "continuation requires "
                                        "conversation_boundary_version"
                                    ),
                                    "session_id": _msg_sid,
                                    "run_id": _target_root_run_id,
                                    "task_scope_id": _target_task_scope_id,
                                },
                            }
                        )
                        continue
                    _continuation_request_id = (
                        str(
                            _payload.get("request_id")
                            or raw.get("request_id")
                            or ""
                        ).strip()
                        or uuid.uuid4().hex
                    )
                    _launch_product_harness_continuation(
                        ws,
                        text,
                        _msg_sid,
                        root_run_id=_target_root_run_id,
                        task_scope_id=_target_task_scope_id,
                        expected_boundary_version=int(
                            _boundary_version
                        ),
                        request_id=_continuation_request_id,
                    )
                    continue

                # P5-S2 Phase 4: a fresh user-initiated chat message means
                # the user has implicitly granted a new auto-resume budget.
                # Reset the attempts counter so subsequent failures may
                # auto-resume up to ``max_attempts`` times again. Skipped
                # for synthetic re-entry texts (`<<supervisor_followup>>`,
                # `<<auto_resume>>`) so they consume the existing budget.
                if not (text or "").startswith("<<"):
                    _sa_for_reset = service_context.get("session_activity")
                    if _sa_for_reset is not None:
                        try:
                            await _sa_for_reset.reset_auto_resume_attempts(_msg_sid)
                        except Exception as _ex:  # noqa: BLE001
                            logger.debug("auto_resume_reset_failed sid=%s err=%s", _msg_sid, _ex)

                _chat_task = _launch_product_harness_chat(
                    ws,
                    text,
                    _msg_sid,
                    _memory_policy_override,
                    bool(_scope_decision.created),
                    _attachment_blocks,
                    str(_payload.get("request_id") or raw.get("request_id") or "") or None,
                    str(_payload.get("turn_id") or raw.get("turn_id") or "") or None,
                )

                def _make_followup_cb(target_sid: str, target_ws):
                    def _cb(_t):
                        async def _maybe_followup():
                            try:
                                _nq_check = service_context.get("nudge_queue")
                                if _nq_check is None or not await _nq_check.peek(target_sid):
                                    return
                                new_task = _launch_product_harness_chat(
                                    target_ws, "<<supervisor_followup>>", target_sid
                                )
                                new_task.add_done_callback(_make_followup_cb(target_sid, target_ws))
                                logger.info(
                                    "supervisor_followup_scheduled sid=%s",
                                    target_sid,
                                )
                            except Exception as _exc:  # noqa: BLE001
                                logger.debug(
                                    "supervisor_followup_failed sid=%s error=%s",
                                    target_sid,
                                    _exc,
                                )

                        try:
                            asyncio.create_task(_maybe_followup())
                        except RuntimeError:
                            # No running loop (shutdown); silently skip.
                            pass

                    return _cb

                _chat_task.add_done_callback(_make_followup_cb(_msg_sid, ws))

            elif msg_type == "interrupt":
                payload = raw.get("payload", {}) or {}
                voice_transport = _voice_transports.get(session_id)
                if voice_transport is not None:
                    voice_transport.interrupt()
                await _cancel_product_harness_run(
                    session_id,
                    str(payload.get("run_id") or "").strip(),
                    reason="voice_interrupt",
                )
                await ws.send_json({"type": "interrupt_ack"})

            elif msg_type == "budget_status":
                # P2-1-S8: SettingsPanel "今日使用" pulls from here.
                try:
                    status = await billing_ledger.status()
                    await ws.send_json({"type": "budget_status", "payload": status})
                except Exception as exc:
                    logger.warning("budget_status_failed", error=str(exc))
                    await ws.send_json({
                        "type": "error",
                        "payload": {"message": f"budget_status failed: {exc}"},
                    })

            elif msg_type in ("memory_list", "memory_delete", "memory_clear", "memory_export"):
                # S14 (V5 §6 threat 5): user-facing controls over persisted
                # conversation history. All four go through the same memory
                # store the agent reads from, so redaction-on-write still holds.
                await _handle_memory_message(ws, session_id, msg_type, raw.get("payload", {}) or {})

            elif msg_type in p4_ipc.P4_IPC_MESSAGE_TYPES:
                # P4-S11 (§16.8): MemoryPanel + ContextTrace IPC surface.
                # Gracefully degrades when P4 services aren't registered
                # (pre-S12 wire-in) — UI shows empty state instead of error.
                await p4_ipc.handle(
                    ws,
                    session_id,
                    msg_type,
                    raw.get("payload", {}) or {},
                    service_context,
                )

            elif msg_type.startswith("workflow_"):
                # Durable Graph/Trace/Replay/Eval IPC. The service is built
                # during lifespan startup; keeping the dispatcher here avoids
                # coupling the workflow core to FastAPI/WebSocket types.
                from deskpet.workflows.ipc import start_workflow_ipc_dispatch

                workflow_service = service_context.get("workflow_service")
                if workflow_service is None:
                    await ws.send_json({
                        "type": "workflow_ipc_error",
                        "request_type": msg_type,
                        "request_id": raw.get("request_id"),
                        "ok": False,
                        "error": {
                            "code": "workflow_service_unavailable",
                            "message": "Durable workflow service is unavailable",
                            "retryable": True,
                        },
                    })
                else:
                    start_workflow_ipc_dispatch(
                        workflow_service,
                        raw,
                        ws.send_json,
                        _workflow_ipc_background_tasks,
                    )

            elif msg_type == "provider_test_connection":
                # P2-1-S3: SettingsPanel「测试连接」button. The candidate
                # credentials travel through the already-authenticated control
                # channel; nothing is persisted here — the UI saves via the
                # Tauri `set_cloud_api_key` command only on success.
                from provider_test_connection import handle_provider_test_connection
                await handle_provider_test_connection(ws, raw.get("payload", {}) or {})

            elif msg_type == "supervisor_user_choice":
                # P5-S2/S3: user clicked a button on the supervisor bubble.
                # P5-S1 D fix: also act on the choice — "Continue/重试/let it"
                # triggers a follow-up that consumes the queued hint;
                # "Cancel/中断/stop" cancels in-flight + clears queue.
                _payload = raw.get("payload", {}) or {}
                _target_sid = _payload.get("session_id") or session_id
                _alert_id = str(_payload.get("alert_id") or "")
                _btn_idx = int(_payload.get("button_index") or 0)
                _btn_text = str(_payload.get("button_text") or "")
                logger.info(
                    "supervisor_user_choice sid=%s alert=%s button_idx=%d text=%s",
                    _target_sid, _alert_id, _btn_idx, _btn_text,
                )
                _sdb_for_choice = service_context.get("session_db")
                if _sdb_for_choice is not None:
                    try:
                        await _sdb_for_choice.append_supervisor_hint(
                            session_id=_target_sid,
                            alert_id=_alert_id,
                            hint_text=_btn_text,
                            action="user_choice",
                            severity="green",
                            user_button=_btn_text,
                        )
                    except Exception as _exc:
                        logger.debug("supervisor_user_choice_audit_failed error=%s", _exc)

                # Heuristic intent matching by button text. We accept
                # both Chinese and English labels since the LLM picks
                # them dynamically. Stop/cancel takes priority over
                # continue if both keywords appear.
                _btn_lower = _btn_text.lower().strip()
                _is_stop = any(k in _btn_text for k in ("中断", "停止", "取消", "终止")) or \
                           any(k in _btn_lower for k in ("cancel", "stop", "abort", "interrupt"))
                _is_continue = any(k in _btn_text for k in ("继续", "重试", "再试", "让它", "再等")) or \
                               any(k in _btn_lower for k in ("continue", "retry", "let", "wait"))

                if _is_stop:
                    # Cancel any in-flight task + clear the nudge queue
                    await _cancel_product_harness_run(
                        _target_sid,
                        str(_payload.get("run_id") or "").strip(),
                        reason="supervisor_user_choice",
                    )
                    _nq_for_stop = service_context.get("nudge_queue")
                    if _nq_for_stop is not None:
                        try:
                            await _nq_for_stop.clear(_target_sid)
                        except Exception:
                            pass
                elif _is_continue:
                    try:
                        _launch_product_harness_chat(
                            ws, "<<supervisor_followup>>", _target_sid
                        )
                        logger.info(
                            "supervisor_user_choice_followup_scheduled sid=%s",
                            _target_sid,
                        )
                    except Exception as _ex_fu:
                        logger.warning(
                            "supervisor_user_choice_followup_failed sid=%s error=%s",
                            _target_sid, _ex_fu,
                        )

            elif msg_type == "supervisor_toggle":
                # P5-S2: enable / disable the supervisor at runtime.
                # When disabling: cancel watchdog, clear queues, drop activity.
                # When enabling: rebuild watchdog if not present (best-effort —
                # full restart is recommended for reliability).
                _payload = raw.get("payload", {}) or {}
                _enabled = bool(_payload.get("enabled", True))
                _persisted = _persist_supervisor_enabled(_enabled)
                _wd_inst = service_context.get("watchdog")
                if _enabled:
                    if _wd_inst is None or not _wd_inst.is_running():
                        logger.info("supervisor_toggle_enable_requested (restart_recommended)")
                    else:
                        _wd_inst.enable()
                        logger.info("supervisor_toggle_enabled")
                else:
                    if _wd_inst is not None:
                        _wd_inst.disable()
                    _nq_inst = service_context.get("nudge_queue")
                    if _nq_inst is not None:
                        try:
                            await _nq_inst.clear()
                        except Exception:
                            pass
                    logger.info("supervisor_toggle_disabled")
                await ws.send_json({
                    "type": "supervisor_toggle_ack",
                    "payload": {"enabled": _enabled, "persisted": _persisted},
                })

            elif msg_type == "__debug_force_error_pending":
                # P5-S1 D — force a session into error_pending so the
                # next watchdog tick triggers supervisor. Used to test
                # fallback alert when supervisor LLM also fails.
                _payload = raw.get("payload", {}) or {}
                target_sid = _payload.get("session_id") or ""
                _sa = service_context.get("session_activity")
                if _sa is not None and target_sid:
                    await _sa.bump(target_sid, event_type="error", snippet="forced for E2E test")
                    await _sa.mark_error_pending(target_sid)
                    # Also reset watchdog dedup so next scan fires
                    _wd = service_context.get("watchdog")
                    if _wd is not None:
                        _wd.reset_dedup(target_sid)
                    await ws.send_json({
                        "type": "__debug_force_error_pending_ack",
                        "payload": {"sid": target_sid, "ok": True},
                    })

            elif msg_type == "__debug_dump_session_activity":
                # P5-S1 D — dump current session_activity store so we
                # can verify error_pending got set + watchdog ran.
                _sa = service_context.get("session_activity")
                _wd = service_context.get("watchdog")
                if _sa is not None:
                    _all = await _sa.snapshot_all()
                else:
                    _all = {}
                _wd_state = {
                    "running": _wd.is_running() if _wd else False,
                    "last_scans": dict(_wd._last_scan_ts) if _wd else {},
                }
                await ws.send_json({
                    "type": "__debug_dump_response",
                    "payload": {"session_activity": _all, "watchdog": _wd_state},
                })

            elif msg_type == "__debug_inject_supervisor_alert":
                # P5-S1 D — Debug IPC for visual / E2E testing. Already
                # protected by SHARED_SECRET (the ws connection itself
                # requires it), so we don't double-gate on DEV_MODE.
                # Single-user desktop-pet threat model: anyone with the
                # secret already has full backend access.
                _payload = raw.get("payload", {}) or {}
                # Fan out to every control WS, identical path to the
                # real supervisor's broadcast.
                msg = {"type": "supervisor_alert", "payload": _payload}
                for _sid_key, _ws_obj in list(_control_connections.items()):
                    try:
                        await _ws_obj.send_json(msg)
                    except Exception as _bex:
                        logger.debug(
                            "debug_supervisor_alert_send_failed sid=%s error=%s",
                            _sid_key, _bex,
                        )
                logger.info(
                    "__debug_inject_supervisor_alert dispatched payload=%s",
                    _payload,
                )
                await ws.send_json({
                    "type": "__debug_inject_ack",
                    "payload": {"ok": True},
                })

            else:
                await ws.send_json({
                    "type": "error",
                    "payload": {"message": f"unknown type: {msg_type}"},
                })

    except WebSocketDisconnect:
        _cancel_harness_inspector_tasks(ws)
        # P4-S20: only clear the dict entry if it's still pointing at
        # OUR ws — a later connection may have replaced us already.
        if _control_connections.get(connection_registry_key) is ws:
            _control_connections.pop(connection_registry_key, None)
        _chat_peer_groups.pop(connection_registry_key, None)
        logger.info("control channel disconnected", session_id=session_id)


@app.websocket("/ws/audio")
async def audio_channel(ws: WebSocket):
    await ws.accept()
    if not _validate_secret(ws):
        try:
            await ws.close(code=4001, reason="invalid secret")
        except Exception:
            pass
        return

    if not _voice_runtime.enabled:
        from deskpet.voice_runtime import (
            VOICE_DISABLED_CODE,
            VOICE_DISABLED_MESSAGE,
        )

        await ws.send_json(
            {
                "type": "error",
                "payload": {
                    "code": VOICE_DISABLED_CODE,
                    "message": VOICE_DISABLED_MESSAGE,
                    "realtime": "pending",
                },
            }
        )
        await ws.close(code=4403, reason="voice temporarily disabled")
        return

    session_id = ws.query_params.get("session_id", "default")
    control_ws = _control_connections.get(session_id)

    from pipeline.voice_pipeline import VoicePipeline

    # Each audio connection gets its own VAD instance (stateful)
    session_vad = _voice_runtime.create_session_vad()
    await session_vad.load()

    # VOICE-MSGPANEL-SYNC fix: 语音广播用「实时」解析的发起窗口 control_ws 作
    # originator，而非 audio 连接建立时的快照 —— backend respawn 后 audio_ws 常
    # 先于 control_ws 重连，快照会是 None/失效，导致守卫挡掉广播、或不能正确 skip
    # 主窗口（重复显示）。这里对齐文字路径（main.py chat handler 用实时 _ws）。
    async def _voice_broadcast(_origin_snapshot, _msg: dict) -> None:
        current_control_ws = _control_connections.get(session_id)
        # If the pipeline snapshot is still the live originator, it already
        # received point-to-point tool activity and should be skipped here.
        # After a reconnect the snapshot is None/stale, so broadcast to every
        # current control peer; otherwise the new originator silently misses
        # tool_call/tool_result while the tool itself still executes.
        originator = (
            current_control_ws
            if _origin_snapshot is current_control_ws
            else None
        )
        await _broadcast_default_chat_peers(originator, _msg)

    # V5 §2.3 + S1: voice pipeline routes through agent_engine (not llm directly)
    # so that S2 memory / S3 tools flow uniformly through voice and text paths.
    # P4-S21 #13: also pass the v2 tool stack (registry + permission gate +
    # raw LLM provider) so voice utterances run through AgentLoop and can
    # actually invoke tools — same code path text chat already uses.
    pipeline = VoicePipeline(
        vad=session_vad,
        asr=service_context.asr_engine,
        agent=service_context.agent_engine,
        tts=service_context.tts_engine,
        control_ws=control_ws,
        session_id=session_id,
        vad_threshold_during_tts=config.voice.vad_threshold_during_tts,
        min_speech_ms_during_tts=config.voice.min_speech_ms_during_tts,
        tts_cooldown_ms=config.voice.tts_cooldown_ms,
        service_context=service_context,
        tool_registry_v2=deskpet_tool_registry_v2,
        permission_gate_v2=permission_gate_v2,
        local_llm=local_llm,
        # VOICE-MSGPANEL-SYNC: 注入实时解析 originator 的语音广播器（_voice_broadcast
        # 在上面定义，闭包捕获 session_id，广播时实时取发起窗口 control_ws 作 skip 目标）。
        broadcast=_voice_broadcast,
        run_client=(
            _harness_runtime.run_client
            if _harness_accepting and _harness_runtime is not None
            else None
        ),
    )
    pipeline.bind_run_host_factory(_voice_run_host)
    # Register so control-channel `interrupt` messages can reach us.
    _voice_transports[session_id] = pipeline

    logger.info("audio channel connected", session_id=session_id)
    try:
        while True:
            data = await ws.receive_bytes()
            await pipeline.process_audio_chunk(data, ws)
    except WebSocketDisconnect:
        logger.info("audio channel disconnected", session_id=session_id)
    finally:
        if _voice_transports.get(session_id) is pipeline:
            _voice_transports.pop(session_id, None)


def main():
    logger.info("starting backend", host=config.backend.host, port=config.backend.port)
    print(f"SHARED_SECRET={SHARED_SECRET}", flush=True)
    uvicorn.run(app, host=config.backend.host, port=config.backend.port, log_level=config.backend.log_level.lower())


if __name__ == "__main__":
    main()
