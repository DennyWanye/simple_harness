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
if os.environ.get("DESKPET_WINDOW_CONTROL_BOOTSTRAP") in {"stdin-v1", "stdin-v2"}:
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
from logging.handlers import RotatingFileHandler
import paths as _paths
from observability.log_redaction import add_safe_exception_summary, redact_log_event
from observability.sdk import HostSdkObservability
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
_log_dir = _paths.user_log_dir()
_log_dir.mkdir(parents=True, exist_ok=True)
_log_file = _log_dir / "backend.log"
_file_handler = RotatingFileHandler(
    _log_file,
    maxBytes=20 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_stream_handler = logging.StreamHandler()
_foreign_pre_chain = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    structlog.stdlib.PositionalArgumentsFormatter(),
    add_safe_exception_summary,
    redact_log_event,
    structlog.processors.TimeStamper(fmt="iso"),
]
_json_log_formatter = structlog.stdlib.ProcessorFormatter(
    processor=structlog.processors.JSONRenderer(sort_keys=True),
    foreign_pre_chain=_foreign_pre_chain,
)
_file_handler.setFormatter(_json_log_formatter)
_stream_handler.setFormatter(_json_log_formatter)
logging.basicConfig(
    level=logging.INFO,
    handlers=[_stream_handler, _file_handler],
    force=True,  # override anything uvicorn may have installed earlier
)
# aiosqlite DEBUG records interpolate bound parameters, which can include
# conversation and recalled-memory plaintext.  Keep the dependency below the
# application log boundary even when a developer raises the root level.
logging.getLogger("aiosqlite").setLevel(logging.WARNING)

_sdk_observability = HostSdkObservability(_log_dir)

# structlog defaults to its own PrintLogger (stdout only). Point it at
# stdlib logging so the FileHandler above actually receives events.
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        add_safe_exception_summary,
        redact_log_event,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
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
service_context.register("sdk_runtime_ready", None)


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
from deskpet.sdk_adapters.sdk_candidate import (
    SDK_VERSION,
    build_candidate_identity,
    verify_memory_candidate,
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
    from deskpet.tools import registry as deskpet_tool_registry_v2
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
def _resolve_primary_registry_provider():
    """Return the highest-priority enabled provider from the registry.

    2026-08-09：原 `_resolve_relay_registry_provider` 只认写死的 relay-cloud
    条目。relay 托管登录移除后，provider 一律由用户手动配置（baseUrl +
    apiKey），"当前该用哪个"就等于 registry 链的头部——与主聊天链路
    (`get_chain()`) 同一口径，避免图片/研究链路各自为政选出不同的 provider。
    """
    try:
        _reg = service_context.get("provider_registry")
        if _reg is None:
            return None
        try:
            _chain = _reg.get_chain()
        except Exception:  # NoProviderConfiguredError 等 —— 没配就是没配
            return None
        if not _chain:
            return None
        _primary_id = str(_chain[0].get("id") or "")
        _entry = _reg.get_entry(_primary_id) if _primary_id else None
        if _entry is None:
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
        )
    except Exception as _exc:  # noqa: BLE001
        logger.debug("primary_provider_resolve_skipped", error=str(_exc))
    return None


def _refresh_image_endpoint_resolver() -> None:
    """Keep image/PPT generation aligned with the active relay provider."""
    try:
        from deskpet.tools import image_tools as _image_tools

        def _resolver() -> tuple[str | None, str | None] | None:
            _provider = _resolve_primary_registry_provider()
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

        _provider = provider or _resolve_primary_registry_provider() or local_llm
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

    # Early return if P4 services (memory system) are unavailable
    session_db = service_context.get("session_db")
    managed_projection = service_context.get("managed_skill_discovery_projection")
    if session_db is None or managed_projection is None:
        logger.warning(
            "growth_authority_skipped",
            reason="p4_services_unavailable",
            session_db_available=session_db is not None,
            managed_projection_available=managed_projection is not None,
        )
        return

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
        LocalAuthSnapshotProvider,
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
    # P4-S13: 记忆 SDK 接线 —— 把认知记忆能力暴露为 product SDK 的 memory tool
    # provider。旧 deskpet.tools.memory_recall 模块已在记忆系统清理时删除，这里改用
    # host 侧 recall_adapter（只做「product SDK tool 契约 <-> MemoryBackend」翻译）。
    from deskpet.memory.recall_adapter import (
        CompanionRunMemoryScopeResolver,
        OwnerMemoryRecallQueryAdapter,
        register_memory_recall,
    )
    from deskpet.memory.session_db import DEFAULT_MEMORY_USER_ID
    memory_recall_query = OwnerMemoryRecallQueryAdapter(
        _memory_backend,
        session_db,
        default_user_id=DEFAULT_MEMORY_USER_ID,
    )
    service_context.register("memory_recall_query", memory_recall_query)
    scope_resolver = CompanionRunMemoryScopeResolver(store)
    service_context.register("memory_recall_scope_resolver", scope_resolver)
    if deskpet_tool_registry_v2 is not None:
        if deskpet_tool_registry_v2.get("memory_recall") is None:
            # P4-S13 TODO: core handler authority 清单仍指向已删除的
            # deskpet/tools/memory_recall.py，需重建 execution_build_* 三个 manifest 后
            # 才能通过 authority_accepts_handler。在此之前仅注册 provider，不阻断启动；
            # SDK runtime 的 memory_recall 走 product tool catalog，不受 host registry 影响。
            try:
                register_memory_recall(
                    deskpet_tool_registry_v2,
                    memory_recall_query,
                    scope_resolver,
                )
            except Exception as _mem_recall_reg_exc:  # noqa: BLE001
                logger.warning(
                    "memory_recall_host_registration_deferred",
                    reason=str(_mem_recall_reg_exc)[:200],
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
            trusted_auth_provider=LocalAuthSnapshotProvider(),
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
        # Growth authority was skipped (e.g., P4 services unavailable)
        logger.warning(
            "growth_authority_cutover_skipped",
            reason="growth_authority_unavailable",
        )
        return
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
        # Companion store/session_db unavailable (P4 services failed)
        logger.warning(
            "companion_projection_skipped",
            reason="dependencies_unavailable",
            companion_store_available=_companion_store is not None,
            session_db_available=session_db is not None,
        )
        return
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
    if uow is not None and _sdk_ingress is not None:
        service_context.register(
            "companion_growth_action_decision_service",
            CompanionActionDecisionService(
                identity_gate=_companion_identity_gate,
                companion_store=_companion_store,
                execution=SqliteExecutionDecisionRecovery(uow.path),
                kernel_client=_sdk_ingress,
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

# --- P4-S13 记忆系统接入（simple-harness-memory-sdk） ---
# 旧 FileMemory / Manager / Embedder / VectorWorker / Facts / Retriever 等已删除。
# 这里构造 host 会话账本 SessionDB + SDK 认知记忆 MemoryBackend（双写）；失败降级为 None，
# app 以无认知记忆模式启动但不阻断。
_summarizer_state_db_path = None
_state_db_path = _paths.user_data_dir() / "data" / "state.db"
try:
    from deskpet.memory.session_db import SessionDB
    _memory_db_path = _paths.user_data_dir() / "data" / "memory.db"
    _memory_backend = None
    _session_db = SessionDB(db_path=_state_db_path)
except Exception as _memory_sdk_exc:  # noqa: BLE001
    logger.warning("memory_sdk_wiring_failed error=%s", str(_memory_sdk_exc)[:200])
    _memory_backend = None
    _session_db = None
# 旧记忆装配块曾赋值、且 main.py 别处仍可能引用的全局变量统一置 None，避免 NameError。
_file_memory = None
_image_worker = None
_fact_extractor = None
_embedder = None
_vector_worker = None
_retriever = None
try:
    from deskpet.skills.loader import SkillLoader as _SkillLoader
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
        knowledge_enabled=bool(
            getattr(getattr(config, "skills", None), "knowledge_enabled", False)
        ),
    )
except Exception as _skills_exc:  # noqa: BLE001
    logger.warning("skills_wiring_failed error=%s", str(_skills_exc)[:200])
    _managed_skill_projection = None
    _skill_loader = None
_context_snapshot_store = None
_context_segment_store = None
_session_history_planner = None
_memory_manager = None
_workspace_mem_store = None
_nudge_queue = None
service_context.register("context_assembler", None)
service_context.register("session_db", _session_db)


def _freeze_current_session_owner():
    """Resolve the trusted owner when creation runs, not during startup wiring.

    Session services are composed before Companion identity opens. Capturing
    ``None`` at that point left every later Project Session ownerless.
    """

    identity_gate = service_context.get("companion_identity_gate")
    if identity_gate is None:
        return None
    return identity_gate.freeze()


if _session_db is not None:
    from deskpet.session.project_binding import (
        ProjectBindingService as _ProjectBindingService,
        SessionCreationService as _SessionCreationService,
    )

    _project_binding_service = _ProjectBindingService(
        _state_db_path, write_lock=_session_db._write_lock
    )
    _session_creation_service = _SessionCreationService(
        _project_binding_service,
        session_db=_session_db,
        owner_identity_resolver=_freeze_current_session_owner,
        provider_binding_validator=None,
        documents_root=(
            _WINDOW_CONTROL_BOOTSTRAP.documents_root
            if _WINDOW_CONTROL_BOOTSTRAP is not None
            else None
        ),
        documents_identity=(
            _WINDOW_CONTROL_BOOTSTRAP.documents_identity
            if _WINDOW_CONTROL_BOOTSTRAP is not None
            else None
        ),
        allow_test_projectless=False,
    )
else:
    _project_binding_service = None
    _session_creation_service = None
service_context.register("project_binding_service", _project_binding_service)
service_context.register("session_creation_service", _session_creation_service)
service_context.register("vector_worker", None)
service_context.register("embedder", None)
service_context.register("skill_loader", _skill_loader)
service_context.register(
    "managed_skill_discovery_projection", _managed_skill_projection
)
# P4-S13: ContextPageInStore 是请求作用域的 page-in 引用权威（Context OS），不依赖
# 记忆 SDK；product SDK tool catalog 的 context_page_in handler 需要它作为 provider。
from deskpet.tools.context_page_in_tools import ContextPageInStore as _ContextPageInStore
service_context.register("context_page_in_store", _ContextPageInStore())

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
                project_id=context.project_id or None,
                project_revision=context.project_revision or None,
                project_identity=context.project_identity or None,
                user_key=global_owner_key,
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
    from deskpet.capabilities.skill_install import (
        BindableSkillInstallRuntimeVerifier,
        GlobalSkillInstallService,
    )
    from deskpet.capabilities.contracts import canonical_global_owner_key
    from deskpet.companion.identity import load_or_create_local_identity
    from deskpet.capabilities.skill_source import BoundedGitHubSkillSource

    skill_install_runtime_verifier = BindableSkillInstallRuntimeVerifier()
    from deskpet.sdk_adapters.skill_install_verification import (
        BindableSkillInstallVerificationDriverFactory,
    )

    skill_install_driver_factory = BindableSkillInstallVerificationDriverFactory()
    global_owner_key = canonical_global_owner_key(
        load_or_create_local_identity(_paths.user_data_dir()).identity_namespace_hash
    )
    await store.converge_legacy_project_skill_bindings(
        global_owner_key=global_owner_key
    )
    platform.global_capability_scope_key = global_owner_key
    project_skill_install_service = GlobalSkillInstallService(
        store=store,
        source=BoundedGitHubSkillSource(),
        staging_root=platform.manager.layout.root / "skill-install-intents",
        batch_publisher=platform.manager,
        runtime_verifier=skill_install_runtime_verifier,
        global_owner_key=global_owner_key,
    )
    from deskpet.capabilities.skill_install_ui import SettingsSkillInstallAuthorizerFactory
    service_context.register(
        "project_skill_install_settings_authorizer",
        SettingsSkillInstallAuthorizerFactory(
            store=store, global_owner_key=global_owner_key
        ),
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
    service_context.register(
        "legacy_frozen_skill_instruction_resolver",
        frozen_skill_resolver,
    )
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
    from deskpet.capabilities.project_skill_discovery import (
        ProjectSkillDiscoveryService,
    )
    service_context.register(
        "project_skill_discovery_service",
        ProjectSkillDiscoveryService(
            store=store,
            hub=platform.hub,
            environment=platform.environment,
        ),
    )
    service_context.register(
        "project_skill_install_service", project_skill_install_service
    )
    service_context.register(
        "skill_install_runtime_verifier", skill_install_runtime_verifier
    )
    service_context.register(
        "skill_install_verification_driver_factory", skill_install_driver_factory
    )
    service_context.register(
        "capability_center",
        CapabilityCenterService(
            store=store,
            manager=platform.manager,
            hub=platform.hub,
            notifier=_broadcast_control,
            default_user_key=global_owner_key,
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
    sdk_policy = service_context.get("sdk_prepared_authorization_policy")
    if sdk_policy is not None:
        sdk_policy.update_policy_generation(state.generation)
    return state.mode == "auto"


async def _authorization_auto_mode() -> bool:
    store = service_context.get("capability_store")
    if store is None:
        return bool(
            permission_gate_v2 is not None
            and getattr(permission_gate_v2, "auto_mode", False)
        )
    return (await store.get_policy_state()).mode == "auto"


async def _activate_human_memory_host_ports(startup_epoch) -> None:  # type: ignore[no-untyped-def]
    """Publish fresh-HUMAN authorities only after their dependencies are ready."""

    from deskpet.memory.schema import StartupCompositionMode

    names = (
        "human_memory_binding_append_authority",
        "human_memory_recovery_lifecycle",
        "human_memory_foreground_scheduler_wake",
        "human_memory_foreground_runtime_execution_authority",
    )
    if startup_epoch.composition_mode is not StartupCompositionMode.HUMAN:
        for name in names:
            service_context.register(name, None)
        return
    policy = service_context.get("capability_store")
    if policy is None:
        raise RuntimeError("Human Memory binding requires authorization policy")
    from deskpet.execution.foreground_queue import ForegroundQueueStore
    from deskpet.memory.recovery_fence import build_recovery_lifecycle_port
    from deskpet.task_scope.runtime_binding_authority import (
        WorkspaceBindingRuntimeAuthority,
    )

    foreground = ForegroundQueueStore(_state_db_path)
    binding = WorkspaceBindingRuntimeAuthority(
        _state_db_path,
        subject="deskpet-local-owner-v1",
        foreground=foreground,
        policy=policy,
    )
    recovery = build_recovery_lifecycle_port(
        db_path=_state_db_path,
        artifact_dir=Path(_paths.user_data_dir())
        / "human-memory-emergency-exports",
    )
    service_context.register("human_memory_binding_append_authority", binding)
    service_context.register("human_memory_recovery_lifecycle", recovery)
    if any(
        item is None
        for item in (
            _sdk_ingress,
            _sdk_runtime_stack,
            _provider_registry,
            _sdk_provider_binding_resolver,
            _sdk_runtime_catalog,
            _sdk_runtime_tool_inventory,
            _sdk_tool_authority_registry,
        )
    ):
        # Fresh installs without a configured Provider keep the Host mutation
        # authorities available, but cannot manufacture an Agent authority.
        service_context.register("human_memory_foreground_scheduler_wake", None)
        service_context.register(
            "human_memory_foreground_runtime_execution_authority", None
        )
        logger.warning(
            "human_memory_foreground_runtime_unavailable",
            reason="sdk_runtime_authority_unavailable",
        )
        return

    from deskpet.execution.foreground_runtime import (
        ForegroundRuntimeExecutionAuthority,
        SqliteSdkTerminalObserver,
    )
    from deskpet.execution.foreground_runtime_ports import (
        ProductForegroundProviderPort,
        ProductForegroundToolPort,
        TaskScopeForegroundContextPort,
    )

    class _AuditSink:
        def record(self, event: str, payload: Mapping[str, object]) -> None:
            logger.info(event, **dict(payload))

    # S5b Task 3: lease-fenced semantic-closure fallback.  The Provider adapter
    # is rebuilt from the durable Run binding through the same resolver the SDK
    # Run used (no new client); the handler is the same one behind the
    # `task_scope_update` Tool; run facts come from the SDK runtime stack.
    from deskpet.execution.semantic_closure import ClosureFallback
    from deskpet.sdk_adapters.post_turn_invoker import RunBoundInvoker
    from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1

    _closure_service = _ensure_task_scope_update_service()
    if _sdk_provider_binding_resolver is None:
        raise RuntimeError("sdk_context_authority_composition_missing:sdk_provider_binding_resolver")

    class _RuntimeLeaseFence:
        """Per-Run lease fence resolved lazily from the driver's (owner, generation)."""

        def __init__(self, owner_id: str) -> None:
            self.owner_id = owner_id
            self.host_run_id = ""
            self.sdk_run_id = ""
            self.generation = 0

        def bind(self, *, host_run_id: str, sdk_run_id: str, generation: int) -> None:
            self.host_run_id = host_run_id
            self.sdk_run_id = sdk_run_id
            self.generation = int(generation)

        async def reserve_attempt(self, row, members):  # type: ignore[no-untyped-def]
            await foreground.reserve_post_turn_attempt(
                host_run_id=self.host_run_id, sdk_run_id=self.sdk_run_id, owner_id=self.owner_id,
                generation=self.generation, attempt=row, members=members,
            )

        async def revalidate(self) -> None:
            from deskpet.execution.foreground_queue import EffectBoundary

            await foreground.authorize_effect(
                host_run_id=self.host_run_id, sdk_run_id=self.sdk_run_id, owner_id=self.owner_id,
                generation=self.generation, boundary=EffectBoundary.CLOSURE,
            )

    _runtime_owner_id = f"deskpet-foreground:{os.getpid()}:{uuid.uuid4().hex}"
    _closure_fence = _RuntimeLeaseFence(_runtime_owner_id)

    def _closure_adapter(record):  # type: ignore[no-untyped-def]
        binding = SdkRunBindingV1.from_record(record)
        return _sdk_provider_binding_resolver.build_authority(binding).provider

    class _BoundClosureFallback:
        """Binds the fence to the (host_run, sdk_run, generation) of each settle call."""

        def __init__(self) -> None:
            self._inner = ClosureFallback(
                _state_db_path,
                invoker=RunBoundInvoker(
                    _state_db_path, fence=_closure_fence, adapter_factory=_closure_adapter
                ),
                service=_closure_service,
                run_facts_reader=_sdk_runtime_stack,
            )

        async def settle(self, *, host_run_id, sdk_run_id, owner_id, generation, terminal_state):  # type: ignore[no-untyped-def]
            if owner_id != _closure_fence.owner_id:
                raise RuntimeError("foreground_runtime_closure_owner_mismatch")
            _closure_fence.bind(host_run_id=host_run_id, sdk_run_id=sdk_run_id, generation=generation)
            return await self._inner.settle(
                host_run_id=host_run_id, sdk_run_id=sdk_run_id, owner_id=owner_id,
                generation=generation, terminal_state=terminal_state,
            )

    runtime = ForegroundRuntimeExecutionAuthority(
        store=foreground,
        subject="deskpet-local-owner-v1",
        owner_id=_runtime_owner_id,
        closure_fallback=_BoundClosureFallback(),
        ingress=_sdk_ingress,
        context=TaskScopeForegroundContextPort(
            _state_db_path,
            subject="deskpet-local-owner-v1",
        ),
        provider=ProductForegroundProviderPort(
            _provider_registry,
            _sdk_provider_binding_resolver,
        ),
        tools=ProductForegroundToolPort(
            _state_db_path,
            catalog=_sdk_runtime_catalog,
            inventory=_sdk_runtime_tool_inventory,
            registry=_sdk_tool_authority_registry,
        ),
        terminal_observer=SqliteSdkTerminalObserver(
            str(_state_db_path),
            _sdk_ingress,
            _sdk_runtime_stack,
            run_fault_memo=_ensure_run_fault_memo(),
        ),
        audit_sink=_AuditSink(),
        effect_gate=_ensure_foreground_effect_gate(),
    )
    service_context.register("human_memory_foreground_scheduler_wake", runtime)
    service_context.register(
        "human_memory_foreground_runtime_execution_authority", runtime
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Preload models on startup (best-effort — failures logged but don't block)."""
    logger.info("preloading models...")
    from llm.resolution import ProviderRoutingReadiness

    global _provider_registry, _memory_backend, _session_creation_service
    global _realtime_voice_service
    _provider_readiness = ProviderRoutingReadiness()
    service_context.register("provider_routing_readiness", _provider_readiness)
    # v33 reset preflight must finish before MemoryManager, workflow,
    # companion, or SDK execution databases are opened.
    from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory
    from deskpet.memory.schema import (
        StartupCompositionMode,
        dispatch_startup_epoch,
    )

    approved_fresh_lane = (
        _state_db_path.resolve().is_relative_to(
            Path(_paths.user_data_dir()).resolve()
        )
    )
    startup_epoch = await dispatch_startup_epoch(
        _state_db_path,
        approved_fresh_lane=approved_fresh_lane,
    )
    service_context.register(
        "human_memory_host_service_factory",
        (
            HumanMemoryHostServiceFactory(_state_db_path, startup_epoch)
            if startup_epoch.composition_mode is StartupCompositionMode.HUMAN
            else None
        ),
    )
    if _memory_backend is None:
        from paths import resolve_model_dir
        from simple_harness_memory import MemoryManager

        from deskpet.memory.wemm_embedder import WeMMEmbedder

        # 2026-09-01: user-selected vector model — tencent/WeMM-Embedding-2B
        # replaces BGE-M3 across the memory composition.
        memory_resource = resolve_model_dir("wemm-embedding-2b").resolve()
        if not memory_resource.is_dir():
            raise RuntimeError("memory_embedding_resource_unavailable")
        memory_embedder = WeMMEmbedder(
            memory_resource,
            revision="product-bundled",
        )
        memory_build_kwargs = {
            "embedder": memory_embedder,
            "resource_path": memory_resource,
        }
        if "observability_sink" in inspect.signature(
            MemoryManager.build_production
        ).parameters:
            memory_build_kwargs.update(
                observability_sink=_sdk_observability.sink,
                correlation=_sdk_observability.correlation,
            )
        _memory_backend = await MemoryManager.build_production(
            _memory_db_path, **memory_build_kwargs
        )
        memory_snapshot = getattr(_memory_backend, "diagnostics_snapshot", None)
        if callable(memory_snapshot):
            _sdk_observability.register_snapshot_source("memory", memory_snapshot)
        _sdk_observability.export()
        # Settings must report the embedder that the production Memory SDK
        # actually owns.  Leaving this slot at the legacy ``None`` placeholder
        # makes the UI claim BGE is stopped while recall is already using it.
        service_context.register("embedder", memory_embedder)
        _session_db.bind_memory_manager(_memory_backend)
    try:
        # SessionDB owns the migration/backup recovery path. Registry identity
        # is loaded only after v23 exists, then legacy bindings are reconciled
        # in one SQLite transaction before ingress opens.
        await _session_db.initialize()
        _migrate_legacy_provider_config(_CONFIG_PATH)
        _provider_registry = LLMProviderRegistry(_CONFIG_PATH)
        # 2026-08-09：把 llm_runtime.json 的手填配置同步成一条 registry provider。
        #
        # onboarding 与设置页写的是 llm_runtime.json（用户可直接编辑，明文是有意
        # 为之），而跑 agent 的 harness 只认 registry chain。relay 时代由登录桥
        # 负责填 registry，relay 移除后这条链断了——表现为"填了 provider，聊天却
        # 报 no LLM provider configured"（真机实测）。
        # 放在启动而不只放在 /config/cloud 的原因：存量用户已经填过了，不该被迫
        # 重走一遍 onboarding 才能把配置补进 registry。
        # 只在 registry 为空时补，绝不覆盖用户在设置页管理的多 provider 配置。
        try:
            await _seed_registry_from_runtime_overrides(_provider_registry)
        except Exception as _seed_exc:  # noqa: BLE001 - 补种失败不阻断启动
            logger.warning("provider_registry_seed_failed error=%s", str(_seed_exc)[:200])
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
        if _project_binding_service is not None:
            def _validate_created_session_provider(
                provider_id: str, incarnation: str, revision: int
            ) -> None:
                entry = _provider_registry.get_entry(provider_id)
                if (
                    entry is None
                    or str(entry.incarnation_id) != incarnation
                    or int(entry.config_revision) != revision
                ):
                    raise RuntimeError("provider_binding_stale")

            _session_creation_service = _SessionCreationService(
                _project_binding_service,
                provider_mutation_lock=_provider_registry.mutation_lock,
                session_db=_session_db,
                owner_identity_resolver=_freeze_current_session_owner,
                provider_binding_validator=_validate_created_session_provider,
                documents_root=(
                    _WINDOW_CONTROL_BOOTSTRAP.documents_root
                    if _WINDOW_CONTROL_BOOTSTRAP is not None
                    else None
                ),
                documents_identity=(
                    _WINDOW_CONTROL_BOOTSTRAP.documents_identity
                    if _WINDOW_CONTROL_BOOTSTRAP is not None
                    else None
                ),
                allow_test_projectless=False,
            )
            service_context.register(
                "session_creation_service", _session_creation_service
            )
            await _session_creation_service.reconcile_automatic_workspaces()
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
        from deskpet.tools import registry as _runtime_tool_registry

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
        from deskpet.workflows.output_contract import (
            TaskOutputContractPort as _TaskOutputContractPort,
            TaskOutputContractV1 as _TaskOutputContractV1,
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
                output_contract=dict(values.get("output_contract") or {}),
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
                output_contract=dict(values.get("output_contract") or {}),
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
            workflow_name = str(row.get("workflow_name") or "code_complex")
            from deskpet.harness.adapters.legacy_execution_migration import (
                resolve_frozen_legacy_workspace,
            )
            workspace = await resolve_frozen_legacy_workspace(
                row=row,
                start_payload=start_payload,
                session_db=service_context.get("session_db"),
            )
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
            output_contract = None
            output_contract_port = None
            if workflow_name == "durable_task":
                raw_output_contract = start_payload.get("output_contract")
                if isinstance(raw_output_contract, dict):
                    output_contract = _TaskOutputContractV1.from_dict(
                        raw_output_contract
                    )
                    if Path(output_contract.workspace_root) != Path(workspace):
                        raise RuntimeError(
                            "durable task output contract workspace mismatch"
                        )
                    output_contract_port = _TaskOutputContractPort(
                        output_contract
                    )
                elif start_payload.get("capability_builder") is None:
                    # Compatibility read for Runs created before
                    # task-output-contract-v1 shipped. New durable-task launches
                    # are rejected by the host unless they carry a frozen
                    # contract, but making an old checkpoint unrecoverable would
                    # turn a safety upgrade into data loss.
                    logger.warning(
                        "durable_task_legacy_output_contract_missing",
                        run_id=str(row.get("run_id") or ""),
                        root_run_id=str(row.get("root_run_id") or ""),
                    )
            ports = {
                "llm": _RecoveryProposalPort(
                    shim,
                    deskpet_tool_registry_v2,
                    session_id=session_id,
                    provider_name=provider_name,
                    model_name=model_name,
                    profile_key=f"workflow.{workflow_name}",
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
                    output_contract=output_contract,
                ),
                "tool": _RecoveryToolDispatchPort(
                    deskpet_tool_registry_v2,
                    session_id=session_id,
                    execution_context=execution_context,
                    runtime_tool_state=runtime_tool_state,
                    workflow_name=workflow_name,
                    dispatch_fence_acquirer=service_context.get(
                        "run_execution_fence_acquirer"
                    ),
                ),
            }
            if output_contract_port is not None:
                ports["output_contract"] = output_contract_port
            return WorkflowContext(
                ports=ports,
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

        # Import the actual ToolRegistry instance for personal_runtime_adapter
        from deskpet.tools.registry import registry as _actual_tool_registry
        _workflow_service.runtime_adapters.register(
            build_personal_runtime_adapter(
                journal=_PersonalEffectJournal(
                    _workflow_service.run_store.path
                ),
                tool_registry=_actual_tool_registry,
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
        from deskpet.workflows.startup_recovery import activate_and_recover_workflows

        _workflow_recovery = await activate_and_recover_workflows(
            project_bindings=_project_binding_service,
            workflow_service=_workflow_service,
            workflow_launcher=_workflow_launcher,
            required_runtime_identities=_workflow_service.runtime_adapters.identities(),
        )
        _delete_reconcile = _workflow_recovery["delete_reconcile"]
        startup_recoveries = _workflow_recovery["startup_recoveries"]
        recovered_decisions = _workflow_recovery["recovered_decisions"]
        recovered_deliveries = _workflow_recovery["recovered_deliveries"]
        recovered_runs = _workflow_recovery["recovered_runs"]
        if _delete_reconcile["failed"]:
            logger.warning(
                "session_delete_cancel_reconcile_incomplete",
                **_delete_reconcile,
            )
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
    if _emb is not None and callable(getattr(_emb, "warmup", None)):
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
    # 记忆系统 reflection 低频任务已随旧记忆系统移除（等待 simple-harness-memory-sdk）。

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

    # P4-S20-D 启动时后台总结老对话任务已随旧记忆系统移除（等待 SDK）。

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

    if _sdk_desktop_test_enabled():
        # This local acceptance mode exists to validate the new SDK itself.
        # Do not make it depend on the legacy Workflow/Harness cutover gates.
        _initialize_sdk_desktop_test_bridge()
    else:
        # Task 13 startup order is fail-closed: Platform foundation exists before
        # authority composition, while user catalog and Companion ingress remain
        # closed until Harness registers the full core catalog and the durable
        # cutover reconciler has proved every receipt.
        await _initialize_capability_runtime()
        await _initialize_growth_authority()
        # Slice C: Use SDK Runtime instead of legacy harness
        await _activate_product_sdk_runtime()
        await _activate_human_memory_host_ports(startup_epoch)
        await _complete_growth_authority_cutover()
        await _initialize_companion_projection_services()
        await _activate_companion_runtime_adapter_and_open_ingress()
        _initialize_companion_action_decision_service()
    from deskpet.realtime_voice import (
        REALTIME_VOICE_ENABLED,
        RealtimeVoiceService,
        allowed_realtime_origins,
    )

    try:
        _realtime_vite_port = int(os.getenv("DESKPET_VITE_PORT", "5173"))
    except ValueError:
        _realtime_vite_port = 5173
    if REALTIME_VOICE_ENABLED:
        _realtime_voice_service = RealtimeVoiceService(
            shared_secret=SHARED_SECRET,
            relay_endpoint_supplier=lambda: str(
                getattr(local_llm, "base_url", config.llm.local.base_url)
            ),
            api_key_supplier=lambda: _current_cloud_api_key
            or _resolve_cloud_api_key(),
            allowed_origins=allowed_realtime_origins(_realtime_vite_port),
        )
        logger.info(
            "realtime_voice_ready",
            path="/ws/realtime-voice",
            protocol_version="2026-08-27.1",
        )
    else:
        _realtime_voice_service = None
        logger.info(
            "realtime_voice_disabled",
            path="/ws/realtime-voice",
            reason="temporarily_disabled_by_product",
        )
    logger.info("startup complete")
    yield
    if _realtime_voice_service is not None:
        try:
            await _realtime_voice_service.close()
            logger.info("realtime_voice_stopped")
        except Exception:  # noqa: BLE001
            logger.warning("realtime_voice_shutdown_failed")
        finally:
            _realtime_voice_service = None
    from deskpet.retrieval.runtime import shutdown_default_gateway
    await shutdown_default_gateway()
    global _sdk_runtime_stack, _sdk_ingress
    global _sdk_desktop_bridge
    _foreground_runtime = service_context.get(
        "human_memory_foreground_runtime_execution_authority"
    )
    if _foreground_runtime is not None:
        try:
            await _foreground_runtime.close(timeout=5.0)
            logger.info("human_memory_foreground_runtime_stopped")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "human_memory_foreground_runtime_shutdown_failed",
                error=str(exc),
            )
        finally:
            service_context.register(
                "human_memory_foreground_scheduler_wake", None
            )
            service_context.register(
                "human_memory_foreground_runtime_execution_authority", None
            )
    # Close SDK Runtime ingress
    if _sdk_ingress is not None:
        _sdk_ingress.close()
    _pending_sdk_recovery = tuple(_sdk_recovery_watch_tasks.values())
    for _task in _pending_sdk_recovery:
        _task.cancel()
    if _pending_sdk_recovery:
        await asyncio.gather(
            *_pending_sdk_recovery, return_exceptions=True
        )
    _sdk_recovery_watch_tasks.clear()
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
    if _sdk_desktop_bridge is not None:
        try:
            await _sdk_desktop_bridge.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("sdk_desktop_test_shutdown_failed", error=str(exc))
        finally:
            _sdk_desktop_bridge = None
            service_context.register("sdk_runtime_ready", None)
    # Close SDK Runtime Stack (Slice C)
    if _sdk_runtime_stack is not None:
        try:
            await _sdk_runtime_stack.close()
            logger.info("product_sdk_runtime_stopped")
        except Exception as exc:  # noqa: BLE001
            logger.warning("product_sdk_runtime_shutdown_failed", error=str(exc))
        finally:
            _sdk_runtime_stack = None
            _sdk_ingress = None
            from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

            for _sdk_run_id, _retained in tuple(
                _sdk_retained_presentations.items()
            ):
                _delivery_adapters.pop(_sdk_run_id, None)
                _context = _retained[3]
                _root_run_id = str(
                    getattr(_context, "run_id", "") or ""
                )
                if _sdk_run_ids_by_root.get(_root_run_id) == _sdk_run_id:
                    _sdk_run_ids_by_root.pop(_root_run_id, None)
            _sdk_retained_presentations.clear()
            _sdk_unavailable_tool_authority_runs.clear()
            service_context.register("sdk_runtime_ready", None)
    # SessionDB is the sole owner of the borrowed MemoryManager and its
    # product outbox dispatcher.  Close it after every Runtime borrower, once,
    # with a hard bound so shutdown cannot hang on a provider/storage fault.
    _owned_session_db = service_context.get("session_db")
    if _owned_session_db is not None:
        try:
            await asyncio.wait_for(
                _owned_session_db.close(timeout_seconds=5.0),
                timeout=6.0,
            )
            logger.info("product_memory_owner_stopped")
        except Exception as exc:  # noqa: BLE001
            logger.warning("product_memory_owner_shutdown_failed", error=str(exc))
        finally:
            service_context.register("session_db", None)
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


async def _project_open_sdk_authorizations(
    ws: WebSocket,
    *,
    session_id: str | None = None,
    sdk_run_id: str | None = None,
    rehydrated: bool = False,
) -> int:
    """Project durable SDK Tool decisions through the existing desktop UI protocol."""

    ingress = _sdk_ingress
    if ingress is None or not hasattr(ingress, "list_open_authorizations"):
        return 0
    try:
        decisions = ingress.list_open_authorizations(
            run_id=sdk_run_id,
            session_id=session_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "sdk_authorization_projection_failed",
            sdk_run_id=sdk_run_id,
            session_id=session_id,
            error_type=type(exc).__name__,
        )
        return 0
    count = 0
    for decision in decisions:
        root_run_id = str(decision.run_id or "").strip()
        durable_sdk_run_id = str(decision.sdk_run_id or "").strip()
        if not root_run_id or not durable_sdk_run_id:
            logger.warning(
                "sdk_authorization_projection_route_missing",
                decision_id=decision.decision_id,
            )
            continue
        existing_sdk_run_id = _sdk_run_ids_by_root.setdefault(
            root_run_id, durable_sdk_run_id
        )
        if existing_sdk_run_id != durable_sdk_run_id:
            logger.error(
                "sdk_authorization_projection_route_conflict",
                decision_id=decision.decision_id,
                root_run_id=root_run_id,
            )
            continue
        frame = {
            "type": "permission_request",
            "payload": {
                "session_id": decision.session_id,
                "run_id": root_run_id,
                "sdk_run_id": durable_sdk_run_id,
                "task_scope_id": decision.task_scope_id,
                "turn_id": decision.turn_id,
                "request_id": decision.request_id,
                "decision_id": decision.decision_id,
                "nonce": decision.nonce,
                "version": decision.version,
                "category": decision.category,
                "summary": (
                    decision.prompt
                    or f"允许 Simple Harness 执行 {decision.tool_name}"
                ),
                "params": {
                    **decision.params,
                    "tool_name": decision.tool_name,
                },
                "default_action": "prompt",
                "dangerous": decision.dangerous,
                "expires_at": decision.expires_at,
                "rehydrated": rehydrated,
            },
        }
        await ws.send_json(frame)
        await _broadcast_default_chat_peers(ws, frame)
        count += 1
    return count


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
    await _project_open_sdk_authorizations(
        ws,
        session_id=session_id,
        rehydrated=True,
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
            author=str(values.get("author") or "Simple Harness"),
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


def _resolve_chat_source_session(
    transport_sid: str,
    requested_session_id: object,
) -> str:
    """Resolve the user-visible source Session for a new conversation.

    An empty ChatView sends no session id. The WebSocket transport id is a
    stable window identity (often ``message-panel-main``), not a model-binding
    authority. In that case inherit from the transport's currently mapped
    peer group so a new topic follows the model the user was actually using.
    """

    requested = str(requested_session_id or "").strip()
    if requested:
        return requested
    transport = str(transport_sid or "default")
    mapped = str(_chat_peer_groups.get(transport) or "").strip()
    return mapped or _initial_chat_peer_group(transport)


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
    if assembler is None:
        # The legacy assembler is optional after the SDK Context cutover.  It
        # must not gate the Manager-backed resolver used by fresh SDK Turns.
        logger.info("assembler_unavailable_binding_sdk_skill_reader_only")
    else:
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


# SDK Runtime globals (production ingress)
_sdk_runtime_stack = None
_sdk_ingress = None
# Shared final Tool-effect admission gate.  The SDK effect executor consults
# it before every physical dispatch; the foreground runtime registers exact
# lease identities into it.  Created lazily because the SDK stack starts
# before the human-memory foreground ports.
_foreground_effect_gate = None


def _ensure_foreground_effect_gate():  # type: ignore[no-untyped-def]
    global _foreground_effect_gate
    if _foreground_effect_gate is None:
        from deskpet.execution.foreground_runtime import (
            ForegroundEffectAdmissionGate,
        )

        _foreground_effect_gate = ForegroundEffectAdmissionGate()
    return _foreground_effect_gate


# S5b Task 1: process-local memo of whole-Run fault codes.  The authorities
# that raise a Run fault (TaskExecutionEnvelope authority, run Tool exposure)
# record the stable code; the SDK terminal observer copies it into the
# ``run_terminal`` evidence ``public_payload.error_code`` on durable FAILED.
_run_fault_memo = None


def _ensure_run_fault_memo():  # type: ignore[no-untyped-def]
    global _run_fault_memo
    if _run_fault_memo is None:
        from deskpet.sdk_adapters.run_faults import RunFaultMemo

        _run_fault_memo = RunFaultMemo()
    return _run_fault_memo


# S5b Task 2: single owner of Harness evidence reservations / same-transaction
# objective events over the human-memory state.db.  Shared by the effect
# executor (tool_invocation + host.file/host.test), the provider coordinator
# (provider_invocation) and the v45 route ledger (snapshot / route facts); the
# terminal observer drains it through the SDK runtime stack's fact reader.
_evidence_ingress = None


def _ensure_evidence_ingress():  # type: ignore[no-untyped-def]
    global _evidence_ingress
    if _evidence_ingress is None:
        from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress

        _evidence_ingress = ExecutionEvidenceIngress(_state_db_path)
    return _evidence_ingress
_sdk_context_port = None
_sdk_runtime_catalog: dict[str, Any] | None = None
_sdk_run_binding_registry = None
_sdk_provider_binding_resolver = None
# S5b Task 3: the `task_scope_update` handler shared by the Tool registration and
# the terminal closure fallback (process-wide, like the binding resolver above).
_task_scope_update_service = None


def _ensure_task_scope_update_service():  # type: ignore[no-untyped-def]
    global _task_scope_update_service
    if _task_scope_update_service is None:
        from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
        from deskpet.sdk_adapters.task_scope_mutation import TaskScopeUpdateService
        from deskpet.sdk_adapters.tools import active_product_tool_context

        _task_scope_update_service = TaskScopeUpdateService(
            _state_db_path,
            tool_context_getter=active_product_tool_context,
            route_ledger=ContextRouteLedgerStore(_state_db_path),
        )
    return _task_scope_update_service
_sdk_tool_authority_registry = None
_sdk_runtime_tool_inventory = None
# Product/UI projections expose the Host canonical root id, while RunClient
# cancellation is keyed by the SDK's deterministic internal id. Keep the
# active bridge explicit and bounded to the lifetime of _execute_sdk_run.
_sdk_run_ids_by_root: dict[str, str] = {}
_sdk_cancel_requested_run_ids: set[str] = set()
_sdk_retained_presentations: dict[str, tuple[Any, Any, Any, Any]] = {}
_sdk_recovery_watch_tasks: dict[str, asyncio.Task] = {}
_sdk_unavailable_tool_authority_runs: set[str] = set()

_sdk_desktop_bridge = None


class _RecoveredSdkWebSocket:
    """Detached transport that fans recovered events to current Session peers."""

    async def send_json(self, frame: dict[str, Any]) -> None:
        await _broadcast_default_chat_peers(None, frame)


async def _recovered_sdk_noop_broadcast(
    _origin: Any, _frame: dict[str, Any]
) -> None:
    return None


async def _recovered_sdk_send_final(
    _origin: Any,
    frame: dict[str, Any],
    **_identity: Any,
) -> None:
    await _broadcast_default_chat_peers(None, frame)


def _restore_sdk_delivery_route(
    record: Any, start: Mapping[str, Any], session_db: Any
) -> bool:
    """Rebuild one detached presentation route before SDK startup recovery."""

    from deskpet.agent.run_presenter import (
        CanonicalRunEventPresentationAdapter,
        PresentationState,
        RunPresentationContext,
        build_product_run_presenter,
    )
    from deskpet.sdk_adapters.delivery import ProductDeliveryAdapter
    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

    start_input = start.get("input")
    metadata = (
        start_input.get("context_metadata")
        if isinstance(start_input, Mapping)
        else None
    )
    if not isinstance(metadata, Mapping):
        return False
    run_binding = metadata.get("run_binding")
    if not isinstance(run_binding, Mapping):
        return False
    sdk_run_id = str(record.run_id)
    session_id = str(metadata.get("session_id") or "").strip()
    root_run_id = str(metadata.get("root_run_id") or "").strip()
    request_id = str(metadata.get("request_id") or "").strip()
    task_scope_id = str(metadata.get("task_scope_id") or "").strip()
    if not all((sdk_run_id, session_id, root_run_id, request_id, task_scope_id)):
        return False
    product_input = start_input.get("input")
    text = (
        str(product_input.get("text") or "")
        if isinstance(product_input, Mapping)
        else ""
    )
    websocket = _RecoveredSdkWebSocket()
    context = RunPresentationContext(
        session_id=session_id,
        text=text,
        websocket=websocket,
        services=service_context,
        config=config,
        messages=[],
        session_db=session_db,
        vector_worker=service_context.get("vector_worker"),
        activity_store=None,
        provider_chain=None,
        fallback_provider=None,
        request_id=request_id,
        max_iterations=50,
        is_sentinel=False,
        broadcast=_recovered_sdk_noop_broadcast,
        send_final=_recovered_sdk_send_final,
        emit_context_usage=_sdk_context_usage_from_projection_only,
        intent_label_from_turn=_intent_label_from_turn,
        billing_ledger=None,
        provider=None,
        run_id=root_run_id,
        task_scope_id=task_scope_id,
        conversation_boundary_ref=(
            str(metadata.get("conversation_boundary_ref") or "") or None
        ),
        provider_binding_epoch=int(run_binding.get("binding_epoch") or 0),
        provider_binding_provider_id=str(
            run_binding.get("provider_id") or ""
        ) or None,
        provider_binding_model_id=str(run_binding.get("model_id") or "") or None,
    )
    presenter = build_product_run_presenter()
    adapter = CanonicalRunEventPresentationAdapter()
    state = PresentationState()
    delivery = ProductDeliveryAdapter(
        session_id=session_id,
        request_id=request_id,
        run_id=sdk_run_id,
        presenter=presenter,
        adapter=adapter,
        context=context,
        state=state,
    )
    _delivery_adapters[sdk_run_id] = delivery
    _sdk_run_ids_by_root[root_run_id] = sdk_run_id
    _sdk_retained_presentations[sdk_run_id] = (
        delivery, presenter, state, context
    )
    return True


def _isolate_unrestorable_sdk_tool_authority(
    *,
    sdk_run_id: str,
    metadata: Mapping[str, Any],
    provider_binding_resolver: Any,
    tool_authorities: Any,
    error: BaseException,
) -> None:
    """Remove partial recovery leases for one fail-closed legacy Run."""

    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

    try:
        provider_binding_resolver.mark_terminal(sdk_run_id, "failed")
    except (KeyError, RuntimeError, ValueError):
        pass
    try:
        tool_authorities.mark_terminal(sdk_run_id, "failed")
    except (KeyError, RuntimeError, ValueError):
        pass
    _delivery_adapters.pop(sdk_run_id, None)
    _sdk_retained_presentations.pop(sdk_run_id, None)
    _sdk_unavailable_tool_authority_runs.add(sdk_run_id)
    root_run_id = str(metadata.get("root_run_id") or "")
    if _sdk_run_ids_by_root.get(root_run_id) == sdk_run_id:
        _sdk_run_ids_by_root.pop(root_run_id, None)
    logger.error(
        "sdk_recovery_tool_authority_isolated",
        sdk_run_id=sdk_run_id,
        error_code=getattr(error, "code", type(error).__name__),
    )


def _sdk_price_snapshot(provider_id: str, model_id: str) -> tuple[int, int, str]:
    """Freeze the product pricing table for one physical Provider binding.

    The SDK budget authority is expressed as micro-dollars per million
    tokens.  The old stack passed ``(0, 0)`` for every model, which converted
    unknown charge into a trusted zero.  Product pricing deliberately has a
    pessimistic entry for unknown models, so a missing table row remains
    bounded without becoming free.
    """

    from deskpet.sdk_adapters.context_authority import canonical_sha256
    from llm.pricing import get_price

    price = get_price(provider_id, model_id)
    payload = {
        "provider_id": str(provider_id),
        "model_id": str(model_id),
        "input_per_m": price.input_per_m,
        "output_per_m": price.output_per_m,
        "cache_read_per_m": price.cache_read_per_m,
        "cache_write_per_m": price.cache_write_per_m,
    }
    return (
        max(0, round(price.input_per_m * 1_000_000)),
        max(0, round(price.output_per_m * 1_000_000)),
        f"deskpet-pricing:{canonical_sha256(payload)}",
    )


def _freeze_sdk_catalog(
    tools_adapter: Any,
    generation: int,
    *,
    visibility_registry: Any | None = None,
) -> dict[str, Any]:
    """Return the exact immutable Tool catalog used by Provider and executor."""

    from deskpet.sdk_adapters.context_authority import canonical_sha256
    from simple_harness import thaw_json

    specs: list[dict[str, Any]] = []
    schema_fingerprints: dict[str, str] = {}
    schema_token_count = 0
    for spec in tools_adapter.specs:
        if str(spec.name) in {"memory_recall", "memory_search"}:
            continue
        live_spec = (
            visibility_registry.get(str(spec.name))
            if visibility_registry is not None
            else None
        )
        if (
            live_spec is not None
            and getattr(live_spec, "visible_when", None) is not None
            and str(getattr(live_spec, "visibility_scope", "global")) == "global"
            and not live_spec.is_visible(None)
        ):
            logger.info(
                "sdk_tool_excluded_from_catalog name=%s "
                "reason=global_visibility_not_ready",
                spec.name,
            )
            continue
        # SDK ToolSpec recursively freezes schemas with MappingProxyType.  A
        # shallow dict() only unwraps the root and leaves nested properties
        # non-JSON, which makes canonical hashing fail during cold startup.
        schema = thaw_json(spec.input_schema)
        if not isinstance(schema, dict):
            raise TypeError(f"{spec.name} Tool schema must be a JSON object")
        record = {
            "name": str(spec.name),
            "description": str(spec.description),
            "input_schema": schema,
        }
        specs.append(record)
        schema_fingerprints[str(spec.name)] = canonical_sha256(schema)
        schema_token_count += max(1, len(repr(schema)) // 4)
    fingerprint = canonical_sha256(specs)
    return {
        "generation": int(generation),
        "content_fingerprint": fingerprint,
        "tool_names": [item["name"] for item in specs],
        "tool_count": len(specs),
        "schema_token_count": schema_token_count,
        "schema_fingerprints": schema_fingerprints,
        "specs": specs,
    }


class _ProductSdkProviderBindingResolver:
    """SDK resolver backed only by immutable per-Run product bindings."""

    def __init__(self, provider_registry: Any, client: Any) -> None:
        from deskpet.sdk_adapters.run_bindings import SdkRunBindingRegistry

        self.registry = SdkRunBindingRegistry()
        self._provider_registry = provider_registry
        self._client = client
        self._authorities: dict[str, Any] = {}

    def build_authority(self, binding: Any) -> Any:
        from simple_harness.execution import ProviderBinding
        from simple_harness.execution.budget import BudgetPolicy, FrozenPriceEstimator
        from simple_harness import thaw_json
        from deskpet.sdk_adapters.provider import ProductProviderAdapter

        entry = self._provider_registry.get_entry(binding.provider_id)
        if entry is None:
            raise RuntimeError("SDK bound Provider incarnation is unavailable")
        if (
            str(getattr(entry, "incarnation_id", ""))
            != binding.provider_incarnation_id
            or int(getattr(entry, "config_revision", 0) or 0)
            != binding.provider_config_revision
        ):
            raise RuntimeError("SDK bound Provider incarnation changed")
        provider = ProductProviderAdapter(
            self._provider_registry,
            provider_id=binding.provider_id,
            client=self._client,
            price_resolver=_sdk_price_snapshot,
            model=binding.model_id,
            model_params=thaw_json(binding.model_params),
        )
        price = provider.price_snapshot
        estimator = FrozenPriceEstimator(
            price.version,
            provider.target.pricing_key,
            price.input_micros_per_million,
            price.output_micros_per_million,
        )
        return ProviderBinding(provider, estimator, BudgetPolicy())

    def create_binding(self, **raw: Any) -> Any:
        """Build both sides without allowing their budget identity to drift."""

        from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1

        provisional = SdkRunBindingV1.build(
            **raw,
            budget_fingerprint="pending-provider-budget",
        )
        authority = self.build_authority(provisional)
        binding = SdkRunBindingV1.build(
            **raw,
            budget_fingerprint=authority.budget_fingerprint,
        )
        self.registry.register(binding)
        self._authorities[binding.run_id] = authority
        return binding

    def register(self, binding: Any) -> Any:
        current = self.registry.register(binding)
        authority = self._authorities.get(current.run_id)
        if authority is None:
            authority = self.build_authority(current)
            if authority.budget_fingerprint != current.budget_fingerprint:
                raise RuntimeError("SDK Run budget binding fingerprint mismatch")
            self._authorities[current.run_id] = authority
        return authority

    def resolve(self, run_id: Any) -> Any:
        value = str(getattr(run_id, "value", run_id))
        binding = self.registry.resolve(value)
        authority = self._authorities.get(value)
        if binding is None or authority is None:
            raise KeyError(value)
        return authority

    def mark_waiting(self, run_id: str) -> None:
        self.registry.mark_waiting(run_id)

    def mark_terminal(self, run_id: str, state: str) -> None:
        self.registry.mark_terminal(run_id, state)
        self._authorities.pop(str(run_id), None)

    def active_provider_ids(self) -> frozenset[str]:
        return frozenset(
            binding.provider_id
            for run_id in tuple(self._authorities)
            if (binding := self.registry.resolve(run_id)) is not None
        )


def _assert_sdk_provider_mutation_allowed(provider_ids: Any) -> None:
    """Fence mutations that would invalidate active or WAITING Run leases."""

    if _sdk_provider_binding_resolver is None:
        return
    active = _sdk_provider_binding_resolver.active_provider_ids()
    conflicts = active.intersection(str(item) for item in provider_ids)
    if conflicts:
        raise ProviderMutationConflict(sorted(conflicts)[0])


def _sdk_desktop_test_enabled() -> bool:
    # SDK测试模式已完全禁用（2026-08-17）。
    # 该模式造成用户困惑且限制核心功能（只暴露2个工具）。
    # SDK集成测试应使用专门的测试脚本，不在生产代码中保留受限分支。
    return False


def _initialize_sdk_desktop_test_bridge() -> None:
    """Install the narrow SDK desktop-test ingress without changing normal boots."""

    global _sdk_desktop_bridge
    if not _sdk_desktop_test_enabled():
        return
    provider_registry = service_context.get("provider_registry")
    if provider_registry is None:
        raise RuntimeError("SDK desktop test requires the product provider registry")
    from deskpet.sdk_adapters.desktop_runtime import DesktopSdkRuntimeBridge

    _sdk_desktop_bridge = DesktopSdkRuntimeBridge(
        user_data_root=_paths.user_data_dir(),
        provider_registry=provider_registry,
        ready_publisher=lambda ready: service_context.register(
            "sdk_runtime_ready", ready
        ),
    )
    logger.info(
        "sdk_desktop_test_bridge_ready",
        sdk_version=SDK_VERSION,
        ingress="lazy",
        tools=("process_list", "ppt_create"),
    )


def _trigger_harness_recovery_after_identity_bind() -> bool:
    """Retry durable Runs only after the process identity gate is open."""

    if _sdk_ingress is None:
        return False
    reconciler = getattr(_sdk_ingress, "reconciler", None)
    trigger = getattr(reconciler, "trigger", None)
    if not callable(trigger):
        return False
    trigger()
    return True


class _SdkRunContextAuthorityProxy:
    async def prepare_snapshot(self, request):  # type: ignore[no-untyped-def]
        target = service_context.get("sdk_run_context_authority")
        operation = getattr(target, "prepare_snapshot", None)
        if not callable(operation):
            raise RuntimeError("sdk_run_context_authority_unavailable")
        return await operation(request)


class _SdkRuntimeDecisionSinkProxy:
    async def record_no_recall(self, **values):  # type: ignore[no-untyped-def]
        target = service_context.get("sdk_runtime_decision_sink")
        operation = getattr(target, "record_no_recall", None)
        if not callable(operation):
            raise RuntimeError("sdk_runtime_decision_sink_unavailable")
        return await operation(**values)


class _SdkTaskExecutionAuthorityProxy:
    async def issue_envelope(self, request):  # type: ignore[no-untyped-def]
        target = service_context.get("sdk_task_execution_authority")
        operation = getattr(target, "issue_envelope", None)
        if not callable(operation):
            raise RuntimeError("sdk_task_execution_authority_unavailable")
        return await operation(request)


def _sdk_runtime_authority_bindings() -> dict[str, object]:
    """Bind SDK 0.7 required ports without granting fallback authority."""

    return {
        "run_context_authority": _SdkRunContextAuthorityProxy(),
        "runtime_decision_sink": _SdkRuntimeDecisionSinkProxy(),
        "task_execution_authority": _SdkTaskExecutionAuthorityProxy(),
    }


def _local_owner_auth():
    """Single Host authority template for the authenticated local owner."""

    from deskpet.sdk_adapters.context_route import local_owner_auth

    return local_owner_auth()


async def _build_product_sdk_runtime_stack(
    generation: int,
):
    """Build SDK Runtime Stack with product adapters (Slice C ingress)."""
    verify_memory_candidate()
    from simple_harness import RuntimeProfile
    from simple_harness.execution.budget import BudgetPolicy
    from simple_harness.execution.delivery import DeliveryDispatcher
    from simple_harness.runtime import RuntimePorts, SqliteContextPort
    from simple_harness.runtime.drivers import build_react_driver
    from simple_harness.runtime.termination import TerminationLimits

    from deskpet.sdk_adapters.composition import (
        OwnedResourceCloser,
        ProductionRuntimeBuild,
        ProductSdkRuntimeStack,
        SdkRuntimeBuildInputs,
    )
    from deskpet.sdk_adapters.runtime_paths import (
        ProductRuntimePathsAdapter,
    )
    from deskpet.sdk_adapters.provider import ProductProviderInvocationCoordinator
    from deskpet.sdk_adapters.authorization import ProductAuthorizationAdapter
    from deskpet.sdk_adapters.tool_authority import (
        SdkRuntimeCapabilityBridgeAdapter,
        SdkPreparedAuthorizationPolicy,
        SdkRunToolAuthorityRegistry,
        SdkToolAuthorityMigrationUnavailable,
    )
    from deskpet.sdk_adapters.tools import ProductToolsAdapter
    from deskpet.sdk_adapters.reconciliation import ProductReconciliationAdapter
    from deskpet.sdk_adapters.delivery import ProductDeliveryAdapter
    from deskpet.sdk_adapters.context import ProductContextAdapter

    # Verify required services
    session_db = service_context.get("session_db")
    capability_platform = service_context.get("capability_platform")
    if session_db is None or capability_platform is None:
        raise RuntimeError("SDK Runtime requires session_db and capability_platform")
    session_state_db_path = Path(getattr(session_db, "db_path", _state_db_path))
    memory_identity_authority = service_context.get("memory_identity_authority")
    if memory_identity_authority is None:
        from deskpet.memory.identity import ValidatedLocalMemoryIdentityAuthority

        memory_identity_authority = ValidatedLocalMemoryIdentityAuthority(
            session_state_db_path,
            user_data_dir=_paths.user_data_dir(),
        )
        service_context.register("memory_identity_authority", memory_identity_authority)
    memory_identity_resolver = memory_identity_authority.resolver
    service_context.register("memory_identity_resolver", memory_identity_resolver)
    from deskpet.sdk_adapters.memory_facts_surface import OfficialMemoryFactsSurface

    service_context.register(
        "memory_facts_surface",
        OfficialMemoryFactsSurface(_memory_backend, memory_identity_resolver),
    )

    # The runtime is stable across Provider mutations.  A physical Provider is
    # constructed only after a Run freezes its own Session binding.
    provider_registry = service_context.get("provider_registry")
    if provider_registry is None:
        raise RuntimeError("SDK Runtime requires provider_registry")

    import httpx
    client = httpx.AsyncClient()
    budget_policy = BudgetPolicy()
    provider_binding_resolver = _ProductSdkProviderBindingResolver(
        provider_registry, client
    )

    # Build tool adapter
    from deskpet.tool_catalog import ToolCatalogDependencies, build_explicit_product_tool_catalog
    from deskpet.sdk_adapters.tools import (
        ProductEffectExecutor,
        build_product_tool_registry,
        extend_product_registry_with_mcp,
    )

    # Build tool catalog dependencies
    todo_session_db = service_context.get("session_db")
    workflow_service = service_context.get("workflow_service")
    context_page_store = service_context.get("context_page_in_store")
    memory_query = service_context.get("memory_recall_query")
    memory_scope_resolver = service_context.get("memory_recall_scope_resolver")
    search_gateway = service_context.get("search_gateway")
    authorization_runtime = service_context.get("authorization_runtime")
    capability_store = service_context.get("capability_store")
    capability_scope_store = service_context.get("tool_capability_scope_store")
    if (
        authorization_runtime is None
        or capability_store is None
        or capability_scope_store is None
    ):
        raise RuntimeError("SDK Runtime requires prepared authorization authority")
    initial_authorization_policy = await capability_store.get_policy_state()
    from deskpet.sdk_adapters.capability_catalog import (
        ProductCapabilityCatalogSourceAdapter,
    )
    from deskpet.sdk_adapters.workflows import build_product_workflow_registrations

    skill_projection = service_context.get("managed_skill_discovery_projection")
    skill_metas = (
        tuple(skill_projection.list_metas())
        if skill_projection is not None
        else ()
    )
    workflow_metadata = build_product_workflow_registrations(
        generation=generation,
        transaction_owner=object(),
    )
    resource_records = ProductCapabilityCatalogSourceAdapter().sdk_resource_records(
        skills=skill_metas,
        workflows=workflow_metadata,
    )
    tool_authorities = SdkRunToolAuthorityRegistry(
        scope_store=capability_scope_store,
        resource_records=resource_records,
        workspace_identity_validator=getattr(
            service_context.get("project_binding_service"),
            "validate_workspace_identity",
            None,
        ),
        run_fault_sink=_ensure_run_fault_memo(),
    )
    project_bindings = service_context.get("project_binding_service")
    if project_bindings is not None:
        from deskpet.sdk_adapters.runtime_paths import (
            ProductRuntimePathsAdapter,
            durable_sdk_run_start_exists,
        )

        sdk_execution_db = ProductRuntimePathsAdapter(
            _paths.user_data_dir()
        ).execution_database

        async def _has_durable_sdk_run_start(run_id: str) -> bool:
            return await asyncio.to_thread(
                durable_sdk_run_start_exists, sdk_execution_db, run_id
            )

        reconciled_project_claims = await project_bindings.reconcile_orphan_run_claims(
            _has_durable_sdk_run_start
        )
        if any(reconciled_project_claims.values()):
            logger.warning(
                "project_run_claims_reconciled %s", reconciled_project_claims
            )

        def _release_project_run(authority: Any) -> None:
            asyncio.create_task(project_bindings.release_run(authority.run_id))

        tool_authorities.add_terminal_listener(_release_project_run)
    from deskpet.sdk_adapters.skill_resolver import (
        SdkThenLegacyFrozenSkillResolver,
    )
    from deskpet.tools import skill_tools as _skill_tools

    _skill_tools.bind(
        skill_resolver=SdkThenLegacyFrozenSkillResolver(
            authorities=tool_authorities,
            snapshot_resolver=service_context.get(
                "frozen_skill_instruction_resolver"
            ),
            legacy_resolver=service_context.get(
                "legacy_frozen_skill_instruction_resolver"
            ),
        )
    )
    deskpet_tool_registry_v2.set_dynamic_capability_admission_provider(
        tool_authorities.validate_runtime_tool_admission
    )
    driver = build_react_driver(
        limits=TerminationLimits(max_turns=25, max_tool_calls=50),
        budget_policy=budget_policy,
        estimator=None,
        tool_exposure_resolver=tool_authorities.resolve_exposure,
    )
    from deskpet.sdk_adapters.skill_install_verification import (
        ProductRootDriverRouter,
        SkillInstallVerificationAttemptResolver,
    )

    verification_driver_factory = service_context.get(
        "skill_install_verification_driver_factory"
    )
    if verification_driver_factory is None:
        raise RuntimeError("Skill verification driver factory is unavailable")
    driver = ProductRootDriverRouter(
        react_driver=driver,
        attempt_resolver=SkillInstallVerificationAttemptResolver(capability_store),
        verification_driver_factory=verification_driver_factory,
    )

    def execution_context_getter():
        from deskpet.sdk_adapters.tools import (
            active_product_tool_call_id,
            active_product_tool_context,
        )

        tool_context = active_product_tool_context()
        run_id = tool_context.run_id.value
        effect = getattr(tool_context, "effect_id", None)
        effect_id = str(getattr(effect, "value", effect) or "")
        if not effect_id:
            raise RuntimeError("SDK product Tool effect identity is unavailable")
        return tool_authorities.resolve(run_id).execution_context(
            call_id=active_product_tool_call_id().value,
            effect_id=effect_id,
        )

    capability_bridge = SdkRuntimeCapabilityBridgeAdapter(
        tool_authorities, execution_context_getter
    )

    dependencies = ToolCatalogDependencies(
        todo_session_db=todo_session_db,
        workflow_service_provider=lambda: workflow_service,
        context_page_store=context_page_store,
        execution_context_getter=execution_context_getter,
        memory_query=memory_query,
        memory_scope_resolver=memory_scope_resolver,
        capability_bridge_service=capability_bridge,
        search_gateway=search_gateway,
        memory_manager=_memory_backend,
        memory_identity_resolver=memory_identity_resolver,
    )

    # Authorization state must exist before the Skill install registration is
    # frozen, because its physical handler resolves only durable Product saga
    # receipts and never accepts a model-provided approval token.
    from deskpet.product_state.database import ProductStateDatabase
    from deskpet.product_state.authorization_saga import AuthorizationSagaRepository
    from deskpet.capabilities.skill_install import AuthorizedPreflightReceiptResolver

    sdk_product_state_db_path = _paths.user_data_dir() / "data" / "sdk-product-state.db"
    sdk_product_state_db_path.parent.mkdir(parents=True, exist_ok=True)
    product_state_db = ProductStateDatabase(sdk_product_state_db_path)
    product_state_db.initialize()
    repository = AuthorizationSagaRepository(
        product_state_db, owner_id=f"sdk-runtime-g{generation}"
    )
    project_skill_install_service = service_context.get(
        "project_skill_install_service"
    )
    if project_skill_install_service is None:
        raise RuntimeError("SDK Runtime requires ProjectSkillInstallService")
    skill_install_receipts = AuthorizedPreflightReceiptResolver(
        capability_store, repository
    )

    async def skill_install_handler(_arguments, _context):
        from deskpet.capabilities.skill_install import ProjectSkillInstallError
        from simple_harness.tools import ToolResult

        trusted = execution_context_getter()
        authority = tool_authorities.resolve(trusted.run_id)
        try:
            receipt = await skill_install_receipts.resolve_chat(
                context=trusted, principal_id=authority.principal_id
            )
            return await project_skill_install_service.confirm_authorized(receipt)
        except ProjectSkillInstallError as exc:
            code = str(getattr(exc, "code", "skill_install_failed"))
            retryable = bool(getattr(exc, "retryable", False))
            public_messages = {
                "skill_install_intent_not_found": "The Skill install request is no longer available.",
                "skill_install_runtime_verifier_unavailable": "Skill verification is temporarily unavailable.",
                "skill_pack_manifest_invalid": "The repository does not contain a valid Skill package.",
            }
            public_message = public_messages.get(
                code, "Skill installation could not be completed safely."
            )
            failure_receipt_ref = hashlib.sha256(json.dumps(
                {
                    "schema": "skill-install-handler-failure-v1",
                    "code": code,
                    "principal_id": authority.principal_id,
                    "tool_name": "skill_install",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            allowed_actions = (
                ["retry", "change_source", "cancel"]
                if retryable
                else ["change_source", "cancel"]
            )
            return ToolResult.failed(
                _context.call_id,
                code,
                json.dumps({
                    "schema": "skill-install-failure-v1",
                    "code": code,
                    "public_message": public_message,
                    "retryable": retryable,
                    "failure_receipt_ref": failure_receipt_ref,
                    "attempt_generation": 1,
                    "allowed_actions": allowed_actions,
                }, sort_keys=True, separators=(",", ":")),
                retryable=retryable,
            )

    catalog = build_explicit_product_tool_catalog(dependencies)
    from dataclasses import replace as dataclass_replace
    from deskpet.sdk_adapters.tools import ProductToolRegistration

    projected_registrations = tuple(
        dataclass_replace(
            item,
            description=(
                "Administrative memory override only. Ordinary facts, preferences, "
                "and requests such as 'remember X' are recorded automatically after "
                "the Turn completes; do not call this tool for them. Call only when "
                "the user explicitly requests pinned/non-decaying retention or a "
                "specific memory tier."
            ),
        )
        if item.name == "memory_write"
        else item
        for item in catalog.registrations
    )
    projected_registrations = (*projected_registrations, ProductToolRegistration(
        name="skill_install",
        description="Install Skills from one public GitHub repository for all Sessions of the current user.",
        input_schema={
            "type": "object",
            # The SDK intentionally supports a bounded JSON-Schema subset and
            # rejects the optional `format` keyword. Source resolution performs
            # the authoritative public-GitHub URL validation.
            "properties": {
                "url": {"type": "string"},
                "retry_failure_receipt_ref": {"type": "string"},
                "retry_attempt_generation": {"type": "integer", "minimum": 1},
                "retry_command_id": {"type": "string"},
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        handler=skill_install_handler,
        dispatch_kind="async",
        permission_category="skill_install",
        metadata={
            "source": "product-skill-install",
            "version": "2",
            "stable_handler_id": "core.skill_install.v2",
        },
    ))
    # S5a — five-route Context authority tools (host-composed like skill_install).
    from deskpet.sdk_adapters.context_authority import (
        ContextRouteLedgerStore as _ContextRouteLedgerStore,
    )
    from deskpet.sdk_adapters.context_route import (
        CONTEXT_ROUTE_SCHEMA,
        TASK_SCOPE_SEARCH_SCHEMA,
        ContextRouteToolService,
    )
    from deskpet.sdk_adapters.tools import active_product_tool_context

    def _context_route_binding_store():
        from deskpet.task_scope.workspace_bindings import (
            WorkspaceBindingAuthorityStore,
        )

        return WorkspaceBindingAuthorityStore(_state_db_path)

    from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime

    _human_memory_v7 = HumanMemoryV7Runtime(
        Path(_paths.user_data_dir()) / "data" / "human_memory_v7.db",
        embedder_getter=lambda: service_context.get("embedder"),
    )
    service_context.register("human_memory_v7_runtime", _human_memory_v7)

    _context_route_service = ContextRouteToolService(
        service_factory_getter=lambda: service_context.get(
            "human_memory_host_service_factory"
        ),
        binding_store_factory=_context_route_binding_store,
        binding_append_getter=lambda: service_context.get(
            "human_memory_binding_append_authority"
        ),
        ledger=_ContextRouteLedgerStore(
            _state_db_path, evidence_ingress=_ensure_evidence_ingress()
        ),
        tool_context_getter=active_product_tool_context,
        recall_executor=_human_memory_v7.typed_recall,
    )

    async def context_route_handler(arguments, _context):
        return await _context_route_service.handle_context_route(arguments)

    async def task_scope_search_handler(arguments, _context):
        return await _context_route_service.handle_task_scope_search(arguments)

    projected_registrations = (
        *projected_registrations,
        ProductToolRegistration(
            name="context_route",
            description=(
                "Commit the Context route for this Run: direct_standalone (no "
                "memory needed), memory_standalone (typed recall), "
                "continue_active (exact current task), resume_existing (exact "
                "task_scope_id from a confirmed task_scope_search candidate; "
                "returns the bounded ResumePackage), or create_new (new "
                "multi-step task; requires title). Search hits never "
                "authorize; only this tool commits a route."
            ),
            input_schema=CONTEXT_ROUTE_SCHEMA,
            handler=context_route_handler,
            dispatch_kind="async",
            permission_category="context_route",
            metadata={
                "source": "product-context-route",
                "version": "1",
                "stable_handler_id": "core.context_route.v1",
            },
        ),
        ProductToolRegistration(
            name="task_scope_search",
            description=(
                "Permission-first search over the caller's own archived task "
                "scopes. Returns read-only candidates (title, goal, snippet, "
                "rank); candidates grant no authority and never change the "
                "active task. Confirm one and pass its exact task_scope_id to "
                "context_route(route=resume_existing)."
            ),
            input_schema=TASK_SCOPE_SEARCH_SCHEMA,
            handler=task_scope_search_handler,
            dispatch_kind="async",
            permission_category="task_scope_search",
            metadata={
                "source": "product-context-route",
                "version": "1",
                "stable_handler_id": "core.task_scope_search.v1",
            },
        ),
    )
    # S5b Task 3 — semantic closure Tool (host-composed, direct kernel, always
    # exposed; the Host handler gates scope_unbound / nothing_to_close).
    from deskpet.sdk_adapters.task_scope_mutation import (
        TASK_SCOPE_UPDATE_DESCRIPTION,
        TASK_SCOPE_UPDATE_SCHEMA,
    )

    _closure_tool_service = _ensure_task_scope_update_service()

    async def task_scope_update_handler(arguments, _context):
        return await _closure_tool_service.handle_task_scope_update(arguments)

    projected_registrations = (
        *projected_registrations,
        ProductToolRegistration(
            name="task_scope_update",
            description=TASK_SCOPE_UPDATE_DESCRIPTION,
            input_schema=TASK_SCOPE_UPDATE_SCHEMA,
            handler=task_scope_update_handler,
            dispatch_kind="async",
            permission_category="task_scope_update",
            metadata={
                "source": "product-task-scope-closure",
                "version": "1",
                "stable_handler_id": "core.task_scope_update.v1",
            },
        ),
    )
    tools_adapter, tool_inventory = build_product_tool_registry(projected_registrations)
    from deskpet.tools import registry as live_tool_registry

    tool_inventory = extend_product_registry_with_mcp(
        tools_adapter,
        tool_inventory,
        legacy_registry=live_tool_registry,
        execution_context_getter=execution_context_getter,
    )
    tools_adapter.bind_run_authorities(tool_authorities)
    frozen_catalog = _freeze_sdk_catalog(
        tools_adapter,
        generation,
        visibility_registry=live_tool_registry,
    )
    # Projected MCP handlers still execute through the physical registry.
    # Freeze its exact policy fingerprint into the SDK Run authority so the
    # strict execution-time stale check compares the same policy snapshot.
    frozen_catalog["policy_fingerprint"] = (
        deskpet_tool_registry_v2.read_policy_snapshot(strict=True).fingerprint
    )
    # memory_recall/memory_search remain registered for explicit product
    # surfaces, but automatic Memory is injected through AgentMemoryPort and
    # those tools are deliberately absent from the model-visible SDK catalog.
    # The per-Run authority must therefore receive the same visible projection,
    # not the larger executor inventory.
    visible_tool_names = frozenset(frozen_catalog["tool_names"])
    sdk_tool_inventory = tuple(
        item for item in tool_inventory if str(item.name) in visible_tool_names
    )
    if {str(item.name) for item in sdk_tool_inventory} != visible_tool_names:
        raise RuntimeError("SDK catalog is missing product inventory entries")

    # Build authorization system
    from deskpet.product_state.task_grants import DurableTaskGrantAuthority
    import time

    authorization_policy = SdkPreparedAuthorizationPolicy(
        authorization_runtime,
        tool_authorities,
        initial_policy_generation=initial_authorization_policy.generation,
        skill_install_preflight=project_skill_install_service,
    )

    # Build authorization adapter
    authorization_adapter = ProductAuthorizationAdapter(
        repository,
        policy=authorization_policy,
        identity_factory=authorization_policy.identity_factory,
        grant_authority=DurableTaskGrantAuthority(
            product_state_db,
            policy_generation_provider=(
                authorization_policy.current_policy_generation
            ),
        ),
        grant_factory=authorization_policy.grant_factory,
        clock=time.time,
        terminal_lifecycle=project_skill_install_service,
    )

    # Build reconciliation adapter
    reconciliation_adapter = ProductReconciliationAdapter(repository)

    # Build delivery sink: ProductDeliveryAdapter is per-run (needs session/request/run
    # presentation context), so the stack-level delivery port routes through the global
    # _DeliverySink, which dispatches to the adapter registered for each run by
    # _execute_sdk_run via _delivery_adapters.
    from deskpet.sdk_adapters.desktop_runtime import _DeliverySink
    delivery_adapter = _DeliverySink()

    # Create a simple Noop reconciliation for general reconciliation port
    class _NoopReconciliation:
        async def reconcile(self):
            return None

    projection_pump = None
    if _memory_backend is None:
        raise RuntimeError("SDK Runtime requires the Memory SDK manager")
    from deskpet.sdk_adapters.context_provider import ProductConversationContextProvider
    from deskpet.sdk_adapters.context_source import ProductContextSourceRepository

    # Context source/binding tables are owned by the product SessionDB migration
    # chain (023_official_memory_integration_v31.sql).  Keep them in state.db;
    # sdk-product-state.db is a separate authority for authorization sagas and
    # task grants and intentionally does not carry the SessionDB schema.
    context_source_repository = ProductContextSourceRepository(session_state_db_path)
    context_provider = ProductConversationContextProvider(context_source_repository)
    from deskpet.sdk_adapters.memory_faults import wrap_dev_memory_faults

    agent_memory_port = wrap_dev_memory_faults(
        _memory_backend,
        user_data_dir=_paths.user_data_dir(),
    )
    service_context.register("sdk_context_source_repository", context_source_repository)

    async def close_projection_pump() -> None:
        if projection_pump is not None:
            await projection_pump.close()

    # S5b Task 1: per-effect workspace EffectGate in front of every physical
    # PROJECT_EFFECT dispatch (envelope echo → frozen Run authority → S4
    # verify_task_execution_envelope over the durable v45 route receipt →
    # strict head revision → TaskScope active).  Constructed at stack build so
    # a missing piece fails startup instead of degrading to "no gate".
    from deskpet.sdk_adapters.effect_gate import EffectGate
    from deskpet.task_scope.store import CanonicalTaskScopeStore as _GateScopeStore

    if tool_authorities is None:
        raise RuntimeError("sdk_effect_gate_composition_missing:tool_authority_registry")
    effect_gate = EffectGate(
        binding_store=_context_route_binding_store(),
        route_ledger=_ContextRouteLedgerStore(_state_db_path),
        scope_store=_GateScopeStore(_state_db_path),
        authority_resolver=tool_authorities.resolve,
        exposure_resolver=tool_authorities.resolve_exposure,
    )
    service_context.register("sdk_effect_gate", effect_gate)

    def ports_factory(database, uow):
        nonlocal projection_pump
        global _sdk_context_port
        context = SqliteContextPort(database)
        _sdk_context_port = context
        from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1

        recoverable = (
            *uow.list_recoverable_root_runs(),
            *uow.list_recoverable_child_runs(),
        )
        for record in recoverable:
            start = uow.read_start_snapshot(str(record.run_id))
            start_input = start.get("input") if isinstance(start, Mapping) else None
            metadata = (
                start_input.get("context_metadata")
                if isinstance(start_input, Mapping)
                else None
            )
            raw_binding = (
                metadata.get("run_binding")
                if isinstance(metadata, Mapping)
                else None
            )
            if isinstance(raw_binding, Mapping):
                restored = SdkRunBindingV1.from_record(raw_binding)
                provider_binding_resolver.register(restored)
                record_state = str(
                    getattr(getattr(record, "state", None), "value", None)
                    or getattr(record, "state", "")
                ).lower()
                if record_state == "waiting":
                    provider_binding_resolver.mark_waiting(restored.run_id)
                if not _restore_sdk_delivery_route(record, start, session_db):
                    logger.warning(
                        "sdk_recovery_delivery_route_missing",
                        sdk_run_id=str(record.run_id),
                        state=record_state,
                    )
        effects = ProductEffectExecutor(
            uow=uow,
            registry=tools_adapter,  # tools_adapter is already a ToolRegistry
            authorization=authorization_adapter,
            reconciliation=reconciliation_adapter,
            foreground_admission=_ensure_foreground_effect_gate(),
            effect_gate=effect_gate,
            evidence_ingress=_ensure_evidence_ingress(),
        )
        from simple_harness.execution.context_authority import (
            DurableToolCatalogResolver,
        )

        durable_catalog_resolver = DurableToolCatalogResolver(uow)
        catalog_snapshot = uow.put_tool_catalog_snapshot(
            effects.provider_tool_specs(tuple(frozen_catalog["tool_names"]))
        )
        frozen_catalog["generation"] = catalog_snapshot.generation
        frozen_catalog["content_fingerprint"] = (
            catalog_snapshot.content_fingerprint
        )
        for record in recoverable:
            start = uow.read_start_snapshot(str(record.run_id))
            start_input = start.get("input") if isinstance(start, Mapping) else None
            metadata = (
                start_input.get("context_metadata")
                if isinstance(start_input, Mapping)
                else None
            )
            if not isinstance(metadata, Mapping):
                continue
            raw_binding = metadata.get("run_binding")
            raw_tool_authority = metadata.get("tool_authority")
            if not isinstance(raw_binding, Mapping) or not isinstance(
                raw_tool_authority, Mapping
            ):
                logger.warning(
                    "sdk_recovery_tool_authority_missing",
                    sdk_run_id=str(record.run_id),
                )
                continue
            restored_binding = SdkRunBindingV1.from_record(raw_binding)
            record_state = str(
                getattr(getattr(record, "state", None), "value", None)
                or getattr(record, "state", "")
            ).lower()
            try:
                if record_state == "waiting":
                    tool_authorities.restore_waiting_run(
                        run_start_record=raw_tool_authority,
                        run_binding=restored_binding,
                        catalog_resolver=durable_catalog_resolver,
                    )
                else:
                    tool_authorities.restore_run(
                        run_start_record=raw_tool_authority,
                        run_binding=restored_binding,
                        catalog_resolver=durable_catalog_resolver,
                        lease_state="active",
                    )
            except SdkToolAuthorityMigrationUnavailable as exc:
                _isolate_unrestorable_sdk_tool_authority(
                    sdk_run_id=str(record.run_id),
                    metadata=metadata,
                    provider_binding_resolver=provider_binding_resolver,
                    tool_authorities=tool_authorities,
                    error=exc,
                )
        provider_port = ProductProviderInvocationCoordinator(
            uow=uow,
            resolver=provider_binding_resolver,
            evidence_ingress=_ensure_evidence_ingress(),
        )
        if projection_pump is None:
            from deskpet.sdk_adapters.provider_projection_pump import (
                ProviderProjectionContextV1,
                SdkProviderProjectionPump,
            )

            def projection_context(receipt: Any) -> ProviderProjectionContextV1:
                start = uow.read_start_snapshot(str(receipt.run_id))
                if not isinstance(start, Mapping):
                    raise RuntimeError("sdk_provider_projection_start_snapshot_missing")
                start_input = start.get("input")
                metadata = (
                    start_input.get("context_metadata")
                    if isinstance(start_input, Mapping)
                    else None
                )
                if not isinstance(metadata, Mapping):
                    raise RuntimeError("sdk_provider_projection_context_missing")
                return ProviderProjectionContextV1.from_value(metadata)

            projection_pump = SdkProviderProjectionPump(
                uow,
                session_db,
                context_resolver=projection_context,
                on_committed=lambda context: _broadcast_context_usage_snapshot(
                    session_db, context.session_id
                ),
            )
            projection_pump.trigger()
        from simple_harness.execution.context_staging import (
            ContextStagingRepository,
        )
        context_staging = ContextStagingRepository(database)
        service_context.register("sdk_context_staging", context_staging)
        return RuntimePorts(
            provider=provider_port,
            tools=effects,
            authorization=authorization_adapter,
            context=context,
            delivery=DeliveryDispatcher(uow, {"product": delivery_adapter}),
            tool_reconciliation=reconciliation_adapter,
            reconciliation=_NoopReconciliation(),
            provider_reconciliation=_NoopReconciliation(),
            react_checkpoint=uow,
            tool_catalog=durable_catalog_resolver,
            owner_id=f"deskpet-product-sdk-g{generation}",
            agent_memory=agent_memory_port,
            context_provider=context_provider,
            context_staging=context_staging,
        )

    class _ProductionToolCatalogProxy:
        def _target(self):
            ports = production_ports.get("ports")
            if ports is None:
                raise RuntimeError("production tool catalog is not bound")
            return ports.tool_catalog

        def current_generation(self):
            return self._target().current_generation()

        def resolve(self, generation, content_fingerprint):
            return self._target().resolve(generation, content_fingerprint)

    class _ProductionProjectionPumpProxy:
        async def start(self):
            if projection_pump is None:
                raise RuntimeError("production provider projection pump is not bound")
            started = projection_pump.start()
            if inspect.isawaitable(started):
                await started

        async def close(self):
            if projection_pump is not None:
                await projection_pump.close()

    production_ports: dict[str, Any] = {}
    production_tool_catalog = _ProductionToolCatalogProxy()
    production_projection_pump = _ProductionProjectionPumpProxy()

    def _production_ports_for(uow):
        cached = production_ports.get("ports")
        if cached is None:
            cached = ports_factory(uow.database, uow)
            production_ports["ports"] = cached
        elif cached.react_checkpoint is not uow:
            raise RuntimeError("production runtime transaction owner changed")
        return cached

    def production_runtime_factory(execution_path):
        from simple_harness.runtime import (
            ResourceOwnership,
            ProductionRuntimeConfig,
            build_production_runtime,
        )

        production_ports.clear()
        profiles = {"agent.general": RuntimeProfile("agent.general", "react")}
        runtime_config_kwargs = dict(
            execution_path=execution_path,
            provider_builder=lambda uow: _production_ports_for(uow).provider,
            tools_builder=lambda uow: _production_ports_for(uow).tools,
            delivery_builder=lambda uow: _production_ports_for(uow).delivery,
            context_builder=lambda _database: production_ports["ports"].context,
            context_staging_builder=lambda _database: production_ports["ports"].context_staging,
            authorization=authorization_adapter,
            tool_reconciliation=reconciliation_adapter,
            reconciliation=_NoopReconciliation(),
            provider_reconciliation=_NoopReconciliation(),
            tool_catalog=production_tool_catalog,
            driver=driver,
            profiles=profiles,
            memory=agent_memory_port,
            memory_ownership=ResourceOwnership.BORROWED,
            context_provider=context_provider,
            provider_budget_resolver=provider_binding_resolver,
            provider_projection_pump=production_projection_pump,
            run_binding=tool_authorities,
            structured_message_services=ProductContextAdapter(),
            **_sdk_runtime_authority_bindings(),
            owner_id=f"deskpet-product-sdk-g{generation}",
        )
        if "observability_sink" in inspect.signature(
            ProductionRuntimeConfig
        ).parameters:
            runtime_config_kwargs["observability_sink"] = _sdk_observability.sink
        config = ProductionRuntimeConfig(**runtime_config_kwargs)
        runtime = build_production_runtime(config)
        runtime_snapshot = getattr(runtime, "diagnostics_snapshot", None)
        if callable(runtime_snapshot):
            _sdk_observability.register_snapshot_source("harness", runtime_snapshot)
        _sdk_observability.export()
        return ProductionRuntimeBuild(
            runtime=runtime,
            transaction_owner=production_ports["ports"].react_checkpoint,
        )

    # Build SDK Runtime Stack (wheel identity from the single source of truth)
    stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(_paths.user_data_dir()),
        candidate_identity=build_candidate_identity(),
        dependency_loader=lambda: SdkRuntimeBuildInputs(
            profiles={"agent.general": RuntimeProfile("agent.general", "react")},
            drivers={"react": driver},
            ports_factory=ports_factory,
            workflow_catalog_digest=frozen_catalog["content_fingerprint"],
            owned_resources=(
                OwnedResourceCloser("provider-http", client.aclose),
            ),
            runtime_factory=production_runtime_factory,
        ),
        ready_publisher=lambda ready: service_context.register(
            "sdk_runtime_ready", ready
        ),
    )

    service_context.register("sdk_runtime_catalog", frozen_catalog)
    service_context.register("sdk_provider_binding_resolver", provider_binding_resolver)
    service_context.register("sdk_tool_authority_registry", tool_authorities)
    service_context.register("sdk_runtime_tool_inventory", sdk_tool_inventory)
    service_context.register(
        "sdk_prepared_authorization_policy", authorization_policy
    )

    # S5a — register the three concrete SDK 0.7 authorities.  Missing any
    # prerequisite must fail the stack build (no Noop/fake fallback); the
    # proxies in _sdk_runtime_authority_bindings stay fail-closed otherwise.
    from deskpet.sdk_adapters.context_authority import (
        ContextRouteLedgerStore,
        ProductRunContextAuthority,
        ProductRuntimeDecisionSink,
    )
    from deskpet.sdk_adapters.task_execution import (
        BindingRootResolver,
        ProductTaskExecutionAuthority,
    )

    _state_version = ContextRouteLedgerStore(_state_db_path).user_version()
    if _state_version < 35:
        # Legacy (pre human-memory epoch) composition keeps the SDK bare
        # context path; the v45 route/snapshot ledger does not exist there and
        # the fail-closed proxies stay unregistered exactly as before S5a.
        logger.info(
            "sdk_context_authorities_skipped_legacy_epoch version=%s",
            _state_version,
        )
        return stack
    if tool_authorities is None:
        raise RuntimeError("sdk_context_authority_composition_missing:tool_authority_registry")
    context_route_ledger = ContextRouteLedgerStore(
        _state_db_path, evidence_ingress=_ensure_evidence_ingress()
    )
    context_route_ledger.verify_schema()

    def _authority_ports():
        ports = production_ports.get("ports")
        if ports is None:
            raise RuntimeError("sdk_run_context_authority_ports_unbound")
        return ports

    _v7_runtime = service_context.get("human_memory_v7_runtime")
    if _v7_runtime is None:
        raise RuntimeError(
            "sdk_context_authority_composition_missing:human_memory_v7_runtime"
        )

    _occurrence_reconcile = _v7_runtime.pending_occurrences

    service_context.register(
        "sdk_run_context_authority",
        ProductRunContextAuthority(
            ports_resolver=_authority_ports,
            exposure_resolver=tool_authorities.resolve_exposure,
            ledger=context_route_ledger,
            reconcile=_occurrence_reconcile,
        ),
    )
    service_context.register(
        "sdk_runtime_decision_sink",
        ProductRuntimeDecisionSink(
            ledger=context_route_ledger, reconcile=_occurrence_reconcile
        ),
    )
    # S5b Task 1: PROJECT_EFFECT envelopes resolve their exact single root
    # from the route receipt's immutable binding-set receipt (never the live
    # head, never a default root); zero/multi root is a whole-Run fault whose
    # stable code is memoised for the terminal evidence.
    service_context.register(
        "sdk_task_execution_authority",
        ProductTaskExecutionAuthority(
            root_resolver=BindingRootResolver(_context_route_binding_store()),
            fault_sink=_ensure_run_fault_memo(),
        ),
    )
    for slot in (
        "sdk_run_context_authority",
        "sdk_runtime_decision_sink",
        "sdk_task_execution_authority",
        "sdk_effect_gate",
    ):
        if service_context.get(slot) is None:
            raise RuntimeError(f"sdk_context_authority_composition_missing:{slot}")
    return stack


async def _issue_product_harness_host(
    session_id: str,
    *,
    workspace: str | None,
    allow_provider_unavailable: bool = False,
    allow_workspace_unavailable: bool = False,
    projectless: bool = False,
    project_id: str = "",
    project_revision: int = 0,
    project_identity: str = "",
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
    if (
        write_scope_root is None
        and not projectless
        and bool(companion.get("write_scope_enforced", True))
    ):
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
        project_id=project_id,
        project_revision=project_revision,
        project_identity=project_identity,
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
    provider_id, model_id, incarnation_id, revision, binding_epoch = (
        binding[:5]
    )
    provider_id = str(provider_id or "").strip()
    model_id = str(model_id or "").strip()
    if not provider_id or not model_id:
        return {}
    return {
        "provider_id": provider_id,
        "preferred_model": model_id,
        "provider_incarnation_id": str(incarnation_id or ""),
        "provider_config_revision": int(revision or 0),
        "binding_epoch": int(binding_epoch or 0),
    }


async def _freeze_sdk_provider_authority(
    session_db: Any, session_id: str, host: Any
) -> dict[str, Any]:
    """Project the already-resolved Host route into one immutable SDK binding."""

    from llm.code_params import code_params_to_request
    from llm.model_catalog import model_context_window

    frozen = _snapshot_context_usage_binding_for_run(host)
    model_id = str(frozen.get("preferred_model") or "")
    session_binding = await session_db.get_session_provider_binding_authority(
        session_id
    )
    if int(session_binding.get("binding_epoch") or 0) != int(
        frozen.get("binding_epoch") or 0
    ):
        raise RuntimeError("sdk_provider_binding_changed_during_start")
    for session_key, frozen_key in (
        ("provider_id", "provider_id"),
        ("preferred_model", "preferred_model"),
    ):
        selected = str(session_binding.get(session_key) or "")
        if selected and selected != str(frozen.get(frozen_key) or ""):
            raise RuntimeError("sdk_provider_binding_changed_during_start")
    model_params = dict(session_binding.get("model_params") or {})
    request_params = code_params_to_request(model_params)
    extra_body = request_params.get("extra_body")
    context_override = (
        int(extra_body.get("context_window") or 0)
        if isinstance(extra_body, Mapping)
        else 0
    )
    context_window = context_override or model_context_window(model_id)
    if not context_window:
        raise RuntimeError("sdk_context_window_unavailable")
    return {
        "provider_id": str(frozen["provider_id"]),
        "model_id": model_id,
        "provider_incarnation_id": str(frozen["provider_incarnation_id"]),
        "provider_config_revision": int(frozen["provider_config_revision"]),
        "binding_epoch": int(frozen["binding_epoch"]),
        "model_params": model_params,
        "context_window": int(context_window),
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
    if _sdk_ingress is None:
        raise RuntimeError("SDK Runtime is unavailable")
    host, _provider, _providers, _provider_snapshot = await _issue_product_harness_host(
        session_id=session_id, workspace=None
    )
    return host


async def _cancel_product_harness_run(
    session_id: str,
    run_id: str,
    *,
    reason: str,
) -> bool:
    """Cancel one authenticated Run without consulting a process-local task map."""

    if _sdk_ingress is None or not _sdk_ingress.accepting or not run_id:
        return False
    # SDK RunClient.cancel(run_id) resolves the persisted Run and its frozen
    # start snapshot (which carries the session identity), so cancellation is
    # already authenticated by run_id alone — no host / expected_session_id is
    # needed (the old harness signature took those, the SDK one does not).
    from simple_harness import RunId as SdkRunId

    sdk_run_id = _sdk_run_ids_by_root.get(run_id, run_id)
    logger.info(
        "product_harness_cancel",
        run_id=run_id,
        sdk_run_id=sdk_run_id,
        reason=reason,
    )
    _sdk_cancel_requested_run_ids.add(sdk_run_id)
    try:
        cancelled_record = await _sdk_ingress.require_ready().client.cancel(
            SdkRunId(sdk_run_id)
        )
        cancelled_state = str(
            getattr(
                getattr(cancelled_record, "state", None),
                "value",
                getattr(cancelled_record, "state", ""),
            )
        ).lower()
    except KeyError:
        # A stale/terminal projection or a legacy caller may no longer have a
        # live SDK identity. Treat that as an idempotent miss instead of
        # tearing down the control WebSocket.
        logger.info(
            "product_harness_cancel_not_found",
            run_id=run_id,
            sdk_run_id=sdk_run_id,
            reason=reason,
        )
        _sdk_cancel_requested_run_ids.discard(sdk_run_id)
        return False
    if cancelled_state != "cancelled":
        logger.info(
            "product_harness_cancel_not_applied",
            run_id=run_id,
            sdk_run_id=sdk_run_id,
            state=cancelled_state,
            reason=reason,
        )
        _sdk_cancel_requested_run_ids.discard(sdk_run_id)
        return False
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


def _authoritative_sdk_run_projection(root_run_id: str, sdk_run_id: str = "") -> dict:
    """Read one Run projection from the SDK authority, never UI/process state."""

    resolved_sdk_run_id = str(
        sdk_run_id or _sdk_run_ids_by_root.get(root_run_id, root_run_id)
    ).strip()
    if _sdk_ingress is None or not _sdk_ingress.accepting or not resolved_sdk_run_id:
        return {
            "sdk_run_id": resolved_sdk_run_id,
            "run_version": 0,
            "state": "unavailable",
            "waiting_reason": "sdk_runtime_unavailable",
            "allowed_actions": ["cancel"],
            "retryable": True,
            "terminal_event_ref": None,
        }
    record = _sdk_ingress.query(resolved_sdk_run_id)
    if record is None:
        return {
            "sdk_run_id": resolved_sdk_run_id,
            "run_version": 0,
            "state": "unknown",
            "waiting_reason": "run_not_found",
            "allowed_actions": ["cancel"],
            "retryable": False,
            "terminal_event_ref": None,
        }
    raw_state = getattr(record, "state", record)
    state = str(getattr(raw_state, "value", raw_state)).lower()
    version = int(getattr(record, "version", 0) or 0)
    terminal = state in {"completed", "failed", "cancelled"}
    retained = resolved_sdk_run_id in _sdk_retained_presentations
    waiting_reason = None
    allowed_actions: list[str] = []
    if state == "waiting":
        waiting_reason = (
            "sdk_recovery_pending" if retained else "unowned_waiting"
        )
        allowed_actions = ["resume", "cancel"] if retained else ["cancel"]
    elif not terminal:
        allowed_actions = ["cancel"]
    return {
        "sdk_run_id": resolved_sdk_run_id,
        "run_version": version,
        "state": state,
        "waiting_reason": waiting_reason,
        "allowed_actions": allowed_actions,
        "retryable": state in {"waiting", "unknown", "unavailable"},
        "terminal_event_ref": (
            f"sdk-run:{resolved_sdk_run_id}:terminal:v{version}" if terminal else None
        ),
    }


async def _product_harness_has_live_attached_child(
    session_id: str,
    run_id: str,
) -> bool:
    """Legacy diagnostic: report a non-terminal attached child Run.

    Active-budget enforcement no longer consults this presentation-layer
    helper. Durable waiting transitions pause the root budget directly, and
    RunKernel owns cancellation when the active budget is actually exhausted.

    TEMPORARILY DISABLED: kernel access needs SDK Runtime adapter update.
    """
    # TODO: Re-enable when SDK Runtime provides kernel access through ingress
    return False

    # if _sdk_ingress is None or not _sdk_ingress.accepting or not run_id:
    #     return False
    # from deskpet.execution.contracts import (
    #     AttachmentPolicy,
    #     RunRef,
    #     TERMINAL_RUN_STATUSES,
    # )
    #
    # host, _provider, _providers, _provider_snapshot = await _issue_product_harness_host(
    #     session_id=session_id,
    #     workspace=None,
    # )
    # actor = host.actor(root_run_id=run_id)
    # parent_ref = RunRef(run_id, session_id)
    # kernel = _sdk_ingress.kernel
    # links = await kernel._uow.list_child_links(parent_ref, actor)
    # for link in links:
    #     if link.attachment_policy is AttachmentPolicy.DETACHED:
    #         continue
    #     child = await kernel._query(
    #         RunRef(link.child_run_id, session_id),
    #         actor,
    #     )
    #     if child.status not in TERMINAL_RUN_STATUSES:
    #         return True
    # return False


async def _signal_product_harness_decision(
    session_id: str,
    payload: dict[str, Any],
    response: dict[str, Any],
    *,
    authorization: bool = False,
    projection_ws: WebSocket | None = None,
):
    """Apply one fenced UI decision to the durable execution authority."""

    if _sdk_ingress is None or not _sdk_ingress.accepting:
        raise RuntimeError("SDK Runtime decision ingress is closed")
    run_id = str(payload.get("run_id") or "").strip()
    decision_id = str(
        payload.get("decision_id") or payload.get("request_id") or ""
    ).strip()
    nonce = str(payload.get("nonce") or "").strip()
    version = payload.get("version")
    if not run_id or not decision_id or not nonce or version is None:
        raise ValueError("decision response requires run_id/decision_id/nonce/version")
    sdk_run_id = _sdk_run_ids_by_root.get(run_id, run_id)
    if authorization:
        receipt = await _sdk_ingress.decide_authorization(
            run_id=sdk_run_id,
            decision_id=decision_id,
            nonce=nonce,
            expected_version=int(version),
            decision=str(response.get("decision") or "deny"),
        )
    else:
        receipt = await _sdk_ingress.signal(
            run_id=sdk_run_id,
            signal_id=decision_id,
            payload={
                "decision_id": decision_id,
                "nonce": nonce,
                "version": int(version),
                "response": response,
            },
        )
    if projection_ws is None:
        _ensure_sdk_recovery_watcher(sdk_run_id)
    else:
        _ensure_sdk_recovery_watcher(
            sdk_run_id,
            authorization_ws=projection_ws,
        )
    return receipt


async def _try_resolve_execution_workflow_decision(
    ws: WebSocket,
    *,
    session_id: str,
    raw: Mapping[str, Any],
    workflow_service: Any,
) -> bool:
    """Route ContextTrace decisions owned by the execution ledger.

    ``workflow_runs`` and ``execution_runs`` intentionally share the same
    public inspector, but their decision authorities are different.  The
    legacy workflow IPC only knows ``workflow_decisions``; execution-owned
    children must be fenced and resumed through RunKernel.  Return ``False``
    when the id is not an open execution decision so legacy IPC remains the
    compatibility fallback.
    """

    if str(raw.get("type") or "") != "workflow_decision_resolve":
        return False
    payload = raw.get("payload")
    if not isinstance(payload, Mapping):
        return False
    decision_id = str(payload.get("decision_id") or "").strip()
    if not decision_id:
        return False
    execution_uow = getattr(workflow_service, "execution_uow", None)
    if execution_uow is None or not hasattr(
        execution_uow, "list_open_decision_projections"
    ):
        return False
    projection = next(
        (
            item
            for item in await execution_uow.list_open_decision_projections(
                session_id
            )
            if item["decision"].request.decision_id == decision_id
        ),
        None,
    )
    if projection is None and hasattr(
        execution_uow, "get_open_decision_projection_by_id"
    ):
        projection = await execution_uow.get_open_decision_projection_by_id(
            decision_id
        )
    if projection is None:
        return False
    decision = projection["decision"]
    authoritative_session_id = str(projection["session_id"])
    response = payload.get("response")
    normalized_response = (
        dict(response)
        if isinstance(response, Mapping)
        else {"action": str(response or "cancel")}
    )
    try:
        receipt = await _signal_product_harness_decision(
            authoritative_session_id,
            {
                "run_id": decision.request.run_id,
                "decision_id": decision.request.decision_id,
                "nonce": str(payload.get("nonce") or ""),
                "version": payload.get("expected_version"),
            },
            normalized_response,
        )
    except Exception as exc:  # noqa: BLE001
        await ws.send_json(
            {
                "type": "workflow_ipc_error",
                "request_type": "workflow_decision_resolve",
                "request_id": raw.get("request_id"),
                "ok": False,
                "error": {
                    "code": getattr(exc, "code", type(exc).__name__),
                    "message": str(exc),
                    "retryable": False,
                },
                "payload": {
                    "error": {
                        "code": getattr(exc, "code", type(exc).__name__),
                        "message": str(exc),
                        "retryable": False,
                    }
                },
            }
        )
        return True
    await ws.send_json(
        {
            "type": "workflow_decision_resolve_response",
            "request_id": raw.get("request_id"),
            "ok": True,
            "payload": {
                "decision_id": decision.request.decision_id,
                "run_id": decision.request.run_id,
                "status": "accepted",
                "audit": "Execution decision recorded by RunKernel",
                "duplicate": bool(getattr(receipt, "duplicate", False)),
            },
        }
    )
    logger.info(
        "execution_workflow_decision_resolved",
        session_id=authoritative_session_id,
        run_id=decision.request.run_id,
        decision_id=decision.request.decision_id,
        action=normalized_response.get("action"),
    )
    return True


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
        _sdk_ingress is None
        or not _sdk_ingress.accepting
        or execution_uow is None
    ):
        raise RuntimeError("SDK Runtime continuation ingress is closed")
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
    sdk_run_id = _sdk_run_ids_by_root.get(root_run_id, root_run_id)
    session_db = service_context.get("session_db")
    context_sources = service_context.get("sdk_context_source_repository")
    if context_sources is None or session_db is None:
        raise RuntimeError("SDK continuation context authority is unavailable")
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.runtime import ConversationContinuationInput

    current_message = Message(MessageRole.USER, text)
    continuation_value = None
    from deskpet.sdk_adapters.causal_groups import plan_recent_causal_groups
    from deskpet.sdk_adapters.context_partitions import effective_input_budget

    # S5a: the continuation lane shares the frozen 32k-tier budget and causal
    # grouping with the per-turn Context authority — no second source of truth.
    history, _truncated = await _bounded_sdk_history(
        session_db,
        session_id=session_id,
        root_run_id=root_run_id,
        token_budget=effective_input_budget(32_768),
    )
    _group_plan = plan_recent_causal_groups(history)
    history = [
        item.to_history_row()
        for group in _group_plan.groups
        for item in group.items
    ]
    if _truncated or _group_plan.dropped_group_count:
        logger.info(
            "sdk_continuation_history_bounded truncated=%s dropped_groups=%s",
            _truncated,
            _group_plan.dropped_group_count,
        )
    source_payload = {
        "schema_version": 1,
        "provider_messages": [
            *(
                {"role": row["role"], "content": row["content"]}
                for row in history
            ),
            current_message.to_dict(),
        ],
        "current_message": current_message.to_dict(),
    }
    context_binding_id, source_ref = await context_sources.put_pending(
        root_run_id=sdk_run_id,
        continuation_id=message_ref,
        payload=source_payload,
    )
    continuation_value = ConversationContinuationInput(
        current_message,
        text,
        context_source_snapshot_ref=source_ref,
    )
    receipt = await _sdk_ingress.signal_conversation(
        run_id=sdk_run_id,
        continuation_id=message_ref,
        value=continuation_value,
    )
    accepted = bool(
        receipt.get("accepted")
        if isinstance(receipt, dict)
        else getattr(receipt, "accepted", False)
    )
    duplicate = bool(
        receipt.get("duplicate")
        if isinstance(receipt, dict)
        else getattr(receipt, "duplicate", False)
    )
    if not accepted and not duplicate:
        reason = (
            receipt.get("reason")
            if isinstance(receipt, dict)
            else getattr(receipt, "reason", None)
        )
        raise RuntimeError(
            str(reason or "continuation was not accepted")
        )
    await context_sources.mark_claimed(
        context_binding_id,
        claim_token=receipt.delivery_id,
    )
    _ensure_sdk_recovery_watcher(sdk_run_id)
    receipt_reason = (
        receipt.get("reason")
        if isinstance(receipt, dict)
        else getattr(receipt, "reason", "")
    )
    updated = await execution_uow.get_conversation_boundary(root_run_id)
    if updated is None:
        raise RuntimeError("accepted continuation lost its boundary")
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
                memory_authority="harness",
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
    """Commit and project a typed failure without provider or Tool execution."""

    from deskpet.execution.contracts import root_idempotency_key, RunRef

    if _sdk_ingress is None or _sdk_runtime_stack is None:
        raise RuntimeError("SDK Runtime is unavailable")
    del host
    root_run_id = RunRef(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"deskpet:{root_idempotency_key(session_id, request_id, turn_id)}",
        ).hex,
        session_id,
    ).run_id
    await _sdk_runtime_stack.commit_preflight_blocked_root(
        execution_session_id=session_id,
        run_id=root_run_id,
        request_id=request_id,
        turn_id=turn_id,
        task_scope_id=task_scope_id,
        text=text,
        reason_code=block.reason_code.value,
        evidence_refs=tuple(block.evidence_refs),
    )
    started = {
        "type": "chat_v2_run_started",
        "payload": {
            "session_id": session_id,
            "run_id": root_run_id,
            "request_id": request_id,
            "turn_id": turn_id,
            "task_scope_id": task_scope_id,
            "projection_version": 0,
        },
    }
    await websocket.send_json(started)
    await _broadcast_default_chat_peers(websocket, started)
    public_messages = {
        "workspace_unavailable": "项目目录不可用，请重新定位同一项目后再试。",
        "provider_binding_unavailable": "模型服务尚未就绪，请检查当前会话的模型设置后再试。",
        "capability_unavailable": "当前请求所需能力暂不可用，请检查工具设置后再试。",
    }
    await _send_chat_error(
        websocket,
        {
            "type": "chat_v2_error",
            "payload": {
                "session_id": session_id,
                "run_id": root_run_id,
                "request_id": request_id,
                "turn_id": turn_id,
                "task_scope_id": task_scope_id,
                "error": public_messages.get(
                    block.reason_code.value,
                    "请求暂时无法执行，请检查当前会话设置后再试。",
                ),
                "code": block.reason_code.value,
            },
        },
        session_id=session_id,
        request_id=request_id,
    )


async def _run_sdk_desktop_chat(
    websocket,
    text: str,
    session_id: str,
    *,
    client_request_id: str | None,
    client_turn_id: str | None,
) -> None:
    """Present one real SDK ReAct Run through the existing desktop protocol."""

    bridge = _sdk_desktop_bridge
    if bridge is None:
        raise RuntimeError("SDK desktop test bridge is unavailable")
    request_id = str(client_request_id or "").strip() or uuid.uuid4().hex
    turn_id = str(client_turn_id or client_request_id or "").strip() or uuid.uuid4().hex
    run_id = bridge.run_id_for(session_id, request_id, turn_id)
    task_scope_id = f"sdk-desktop:{run_id}"
    reserved = {
        "type": "chat_v2_run_reserved",
        "payload": {
            "session_id": session_id,
            "run_id": run_id,
            "request_id": request_id,
            "turn_id": turn_id,
            "task_scope_id": task_scope_id,
            "projection_version": 0,
        },
    }
    await websocket.send_json(reserved)
    await _broadcast_default_chat_peers(websocket, reserved)
    echo = {
        "type": "chat_v2_user_echo",
        "payload": {
            "session_id": session_id,
            "text": text,
            "request_id": request_id,
            "turn_id": turn_id,
            "run_id": run_id,
            "task_scope_id": task_scope_id,
        },
    }
    await _broadcast_default_chat_peers(websocket, echo)
    started = {
        "type": "chat_v2_run_started",
        "payload": {
            "session_id": session_id,
            "run_id": run_id,
            "request_id": request_id,
            "turn_id": turn_id,
            "task_scope_id": task_scope_id,
            "projection_version": 0,
        },
    }
    await websocket.send_json(started)
    await _broadcast_default_chat_peers(websocket, started)
    result = await bridge.run_chat(
        text=text,
        session_id=session_id,
        request_id=request_id,
        turn_id=turn_id,
    )
    logger.info(
        "sdk_desktop_test_run_completed",
        run_id=result.run_id,
        state=result.state,
        tools=result.tool_names,
    )
    await _send_chat_final(
        websocket,
        {
            "type": "chat_v2_final",
            "payload": {
                "text": result.text,
                "session_id": session_id,
                "run_id": result.run_id,
                "request_id": request_id,
                "turn_id": turn_id,
                "task_scope_id": task_scope_id,
                "sdk_version": SDK_VERSION,
                "tool_names": list(result.tool_names),
            },
        },
        session_id=session_id,
        request_id=request_id,
    )


async def _run_product_harness_chat(
    websocket,
    text: str,
    session_id: str,
    memory_policy_override=None,
    task_scope_explicit_new: bool = False,
    user_attachment_blocks=(),
    client_request_id: str | None = None,
    client_turn_id: str | None = None,
    prepared_skill_scope: Any | None = None,
    skill_arguments: tuple[str, ...] = (),
) -> None:
    """The single production chat ingress into ProductVenue/RunKernel."""

    if _sdk_desktop_test_enabled():
        await _run_sdk_desktop_chat(
            websocket,
            text,
            session_id,
            client_request_id=client_request_id,
            client_turn_id=client_turn_id,
        )
        return

    from deskpet.execution.run_block_signals import (
        PreflightBlocked,
        RootBlockReasonV1,
    )

    if _sdk_ingress is None or not _sdk_ingress.accepting:
        raise RuntimeError("SDK Runtime ingress is closed")
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
    from deskpet.execution.contracts import root_idempotency_key, RunRef

    root_ref = RunRef(
        uuid.uuid5(uuid.NAMESPACE_URL, f"deskpet:{root_idempotency_key(session_id, request_id, turn_id)}").hex,
        session_id,
    )
    task_scope_id = TaskWorkContextResolver.task_scope_id(
        session_id, request_id, turn_id
    )
    binding_service = service_context.get("project_binding_service")
    if binding_service is None:
        raise RuntimeError("project_binding_service_unavailable")
    creation_service = service_context.get("session_creation_service")
    if creation_service is None:
        raise RuntimeError("session_creation_service_unavailable")
    # Admission is the migration fence for legacy projectless Sessions. Any
    # allocation/persistence error propagates and prevents a projectless Tool
    # catalog from being constructed for this Run.
    await creation_service.migrate_projectless_session(session_id)
    workspace_binding = await binding_service.resolve_session(session_id)
    if workspace_binding.kind == "project":
        workspace = workspace_binding.effective_root
        workspace_resolution = {
            "kind": "project_bound",
            "session_id": workspace_binding.session_id,
            "project_id": workspace_binding.project_id,
            "project_name": workspace_binding.project_name,
            "project_root": workspace_binding.project_root,
            "execution_kind": workspace_binding.execution_kind,
            "effective_root": workspace_binding.effective_root,
            "project_identity": workspace_binding.project_identity,
            "execution_identity": workspace_binding.execution_identity,
            "project_revision": workspace_binding.project_revision,
            "binding_version": workspace_binding.binding_version,
            "handoff": None,
        }
    elif workspace_binding.kind == "projectless":
        workspace = None
        workspace_resolution = {
            "kind": "projectless",
            "session_id": workspace_binding.session_id,
            "effective_root": None,
            "binding_version": 1,
        }
    else:
        workspace = None
        workspace_resolution = {
            "kind": "missing",
            "session_id": workspace_binding.session_id,
            "project_id": workspace_binding.project_id,
            "project_name": workspace_binding.project_name,
            "project_root": workspace_binding.project_root,
            "execution_kind": workspace_binding.execution_kind,
            "effective_root": workspace_binding.effective_root,
            "project_revision": workspace_binding.project_revision,
            "binding_version": workspace_binding.binding_version,
            "error_code": workspace_binding.error_code,
        }

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
    if workspace_resolution["kind"] == "missing":
        host, _provider, _providers, _provider_snapshot = (
            await _issue_product_harness_host(
                session_id=session_id,
                workspace=None,
                allow_provider_unavailable=True,
                allow_workspace_unavailable=True,
                projectless=True,
            )
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
                RootBlockReasonV1.WORKSPACE_UNAVAILABLE,
                ("workspace-binding:" + str(workspace_resolution["error_code"]),),
            ),
        )
        return
    try:
        host, _provider, _providers, _provider_snapshot = await _issue_product_harness_host(
            session_id=session_id,
            workspace=workspace,
            projectless=workspace_resolution["kind"] == "projectless",
            project_id=str(workspace_resolution.get("project_id") or ""),
            project_revision=int(workspace_resolution.get("project_revision") or 0),
            project_identity=str(workspace_resolution.get("project_identity") or ""),
        )
        provider = _provider
        provider_chain = _providers
        provider_snapshot = _provider_snapshot
    except Exception as exc:
        from llm.resolution import SessionProviderUnavailable

        if isinstance(exc, PreflightBlocked):
            host, _provider, _providers, _provider_snapshot = await _issue_product_harness_host(
                session_id=session_id,
                workspace=workspace,
                allow_workspace_unavailable=True,
                projectless=workspace_resolution["kind"] == "projectless",
                project_id=str(workspace_resolution.get("project_id") or ""),
                project_revision=int(workspace_resolution.get("project_revision") or 0),
                project_identity=str(workspace_resolution.get("project_identity") or ""),
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
        host, _provider, _providers, _provider_snapshot = await _issue_product_harness_host(
            session_id=session_id,
            workspace=workspace,
            allow_provider_unavailable=True,
            projectless=workspace_resolution["kind"] == "projectless",
            project_id=str(workspace_resolution.get("project_id") or ""),
            project_revision=int(workspace_resolution.get("project_revision") or 0),
            project_identity=str(workspace_resolution.get("project_identity") or ""),
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
    # TODO: SDK Runtime client.resume() method not yet implemented
    # This is only needed for retry/resume scenarios (when turn_id is provided)
    retry_handle = None  # await _sdk_ingress.require_ready().client.resume(request_id, turn_id, host) if turn_id else None
    frozen_context_usage_basis_sample_id = (
        None
        if retry_handle is not None
        else await _snapshot_context_usage_basis_for_run(
            session_db, session_id
        )
    )
    if retry_handle is None and session_db is not None and not is_sentinel:
        frozen_owner = _companion_identity_gate.freeze()
        # New Sessions are bound atomically during creation. This idempotent
        # admission also claims older empty ownerless Sessions created before
        # the dynamic identity resolver fix; messageful or mismatched Sessions
        # remain fail-closed inside SessionDB.
        await session_db.bind_session_owner_if_absent(
            session_id,
            {
                "profile_id": frozen_owner.owner.profile_id,
                "profile_generation": frozen_owner.owner.profile_generation,
                "binding_epoch": frozen_owner.binding_epoch,
                "owner_kind": "companion_profile",
            },
        )
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
        # This value comes only from the immutable Session binding resolution;
        # the frontend cannot override it.  Passing the verified root lets
        # ProjectRules and TaskWorkContext consume the same Run authority.
        workspace_ref=workspace,
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
        emit_context_usage=_sdk_context_usage_from_projection_only,
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
    workspace_admitted = False
    workspace_admission_retained = False
    try:
        from deskpet.sdk_adapters.context_authority import (
            DefaultDenySnapshotRedactor,
        )
        from deskpet.sdk_adapters.ingress import SdkRuntimeIngress

        if (
            _sdk_provider_binding_resolver is None
            or _sdk_runtime_catalog is None
            or _sdk_tool_authority_registry is None
            or _sdk_runtime_tool_inventory is None
        ):
            raise RuntimeError("SDK per-Run authority is unavailable")
        sdk_run_id = SdkRuntimeIngress._compute_run_id(  # noqa: SLF001
            session_id, request_id, str(turn_id)
        ).value
        if workspace_resolution["kind"] == "project_bound":
            await binding_service.admit_run(workspace_resolution, sdk_run_id)
            workspace_admitted = True
            workspace_resolution["handoff"] = await binding_service.claim_handoff(
                session_id, sdk_run_id
            )
        if sdk_run_id in _sdk_unavailable_tool_authority_runs:
            raise RuntimeError("sdk_tool_authority_unavailable")
        provider_authority = await _freeze_sdk_provider_authority(
            session_db, session_id, host
        )
        from deskpet.sdk_adapters.tools import filter_sdk_catalog_for_workspace

        run_tool_catalog, run_tool_inventory = filter_sdk_catalog_for_workspace(
            _sdk_runtime_catalog,
            _sdk_runtime_tool_inventory,
            workspace_resolution_kind=str(workspace_resolution["kind"]),
        )
        from simple_harness.contracts.messages import (
            ContentBlock,
            Message,
            MessageRole,
        )
        from simple_harness.runtime import ConversationTurnInput
        from deskpet.agent.assembler.components.persona import _resolve_persona

        current_content: Any = text
        if user_attachment_blocks:
            current_content = (
                ContentBlock.from_dict({"type": "text", "text": text}),
                *tuple(
                    ContentBlock.from_dict(dict(block))
                    for block in user_attachment_blocks
                ),
            )
        identity_authority = service_context.get("memory_identity_authority")
        if identity_authority is None:
            raise RuntimeError("validated Memory identity authority is unavailable")
        identity = await identity_authority.bind(session_id=session_id)
        persona_text = _resolve_persona(dict(config.raw))
        prepared_snapshot = await _prepare_sdk_context_snapshot(
            session_db=session_db,
            session_id=session_id,
            request_id=request_id,
            root_run_id=root_ref.run_id,
            sdk_run_id=sdk_run_id,
            turn_id=str(turn_id),
            text=text,
            provider_binding=provider_authority,
            catalog=run_tool_catalog,
            attachment_blocks=tuple(user_attachment_blocks),
            project=workspace_resolution,
            task_scope_id=task_scope_id,
            persona_text=persona_text,
            memory_items=None,
            prepared_skill_scope=prepared_skill_scope,
            skill_arguments=skill_arguments,
            skill_instruction_resolver=service_context.get(
                "frozen_skill_instruction_resolver"
            ),
        )
        context_sources = service_context.get("sdk_context_source_repository")
        if context_sources is None:
            raise RuntimeError("SDK context source repository is unavailable")
        context_binding_id, source_ref = await context_sources.put_pending(
            root_run_id=sdk_run_id,
            continuation_id=None,
            payload=prepared_snapshot.private_record(),
        )
        conversation = ConversationTurnInput(
            identity=identity,
            message=Message(MessageRole.USER, current_content),
            memory_text=text,
            context_source_snapshot_ref=source_ref,
        )
        from deskpet.sdk_adapters.context_authority import (
            DefaultDenySnapshotRedactor,
        )
        public_snapshot = DefaultDenySnapshotRedactor().redact(prepared_snapshot)
        if session_db is not None:
            await session_db.put_sdk_context_public_snapshot(public_snapshot)
        run_binding = _sdk_provider_binding_resolver.create_binding(
            run_id=sdk_run_id,
            session_id=session_id,
            request_id=request_id,
            snapshot_id=prepared_snapshot.snapshot_id,
            provider_id=provider_authority["provider_id"],
            provider_incarnation_id=provider_authority["provider_incarnation_id"],
            provider_config_revision=provider_authority[
                "provider_config_revision"
            ],
            binding_epoch=provider_authority["binding_epoch"],
            model_id=provider_authority["model_id"],
            model_params=provider_authority["model_params"],
            context_window=provider_authority["context_window"],
            catalog_generation=int(run_tool_catalog["generation"]),
            catalog_fingerprint=str(
                run_tool_catalog["content_fingerprint"]
            ),
        )
        from deskpet.sdk_adapters.tool_authority import (
            SDK_DIRECT_TOOL_KERNEL,
            SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY,
        )

        visible_tool_names = frozenset(
            str(name) for name in run_tool_catalog["tool_names"]
        )
        direct_tool_names = visible_tool_names & SDK_DIRECT_TOOL_KERNEL
        deferred_tool_names = visible_tool_names - direct_tool_names
        if not {"tool_search", "tool_describe", "tool_activate"} <= direct_tool_names:
            raise RuntimeError("SDK direct Tool kernel is incomplete")

        global_skill_records = ()
        capability_platform = service_context.get("capability_platform")
        global_skill_service = service_context.get(
            "project_skill_install_service"
        )
        if capability_platform is not None and global_skill_service is not None:
            from deskpet.capabilities.contracts import CapabilityScope
            from deskpet.sdk_adapters.capability_catalog import (
                ProductCapabilityCatalogSourceAdapter,
            )

            global_owner_key = str(global_skill_service.global_owner_key)
            global_scope = CapabilityScope(user_key=global_owner_key)
            async with capability_platform.publish_lock:
                global_snapshot, _global_entries = (
                    await capability_platform.hub.snapshot_for_atomic_lease(
                        global_scope
                    )
                )
                global_skill_records = await (
                    ProductCapabilityCatalogSourceAdapter()
                    .sdk_global_resource_records_from_snapshot(
                        store=capability_platform.store,
                        snapshot=global_snapshot,
                        owner_key=global_owner_key,
                        user_scope_key=global_owner_key,
                    )
                )
            logger.info(
                "sdk_global_skill_catalog_captured count=%s skills=%s",
                len(global_skill_records),
                [record.skill_locator for record in global_skill_records],
            )

        try:
            _sdk_tool_authority_registry.prepare_run(
                run_id=sdk_run_id,
                session_id=session_id,
                request_id=request_id,
                root_run_id=root_ref.run_id,
                task_scope_id=task_scope_id,
                workspace_root=workspace,
                catalog=run_tool_catalog,
                inventory=run_tool_inventory,
                deferred_names=deferred_tool_names,
                disclosure_policy=SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY,
                binding_version=int(workspace_resolution.get("binding_version", 1)),
                workspace_resolution=workspace_resolution,
                resource_records=global_skill_records,
            )
        except BaseException:
            _sdk_provider_binding_resolver.mark_terminal(sdk_run_id, "failed")
            raise
        # Execute Agent via SDK Runtime. Concurrent Sessions resolve their own
        # immutable binding; no global stack mutation or full-Run lock occurs.
        await _execute_sdk_run(
            session_id=session_id,
            request_id=request_id,
            turn_id=turn_id,
            task_scope_id=task_scope_id,
            prepared_snapshot=prepared_snapshot,
            run_binding=run_binding,
            context=context,
            websocket=websocket,
            root_run_id=root_ref.run_id,
            conversation=conversation,
            context_stage=None,
            context_binding_id=context_binding_id,
        )
        context_run = _sdk_ingress.query(sdk_run_id)
        context_run_state = str(
            getattr(
                getattr(context_run, "state", None),
                "value",
                getattr(context_run, "state", ""),
            )
        ).lower()
        if context_run_state in {"completed", "failed", "cancelled"}:
            await context_sources.consume(context_binding_id)
        else:
            workspace_admission_retained = context_run_state == "waiting"
            await context_sources.mark_staged(context_binding_id)
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
        if workspace_admitted and not workspace_admission_retained:
            try:
                await binding_service.release_run(sdk_run_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "project_run_admission_release_failed",
                    run_id=sdk_run_id,
                    error_type=type(exc).__name__,
                )
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
                    from deskpet.execution.contracts import root_idempotency_key, RunRef
                    from deskpet.types.task_work_context import TaskWorkContextResolver

                    root_ref = RunRef(
                        uuid.uuid5(uuid.NAMESPACE_URL, f"deskpet:{root_idempotency_key(session_id, request_id, turn_id)}").hex,
                        session_id,
                    )
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


def _provider_chain_or_none(provider_registry):
    """Return the configured provider chain, or None when there isn't one.

    Fresh installs have no provider configured and ``get_chain()`` raises
    ``NoProviderConfiguredError`` there; that must never take down startup —
    the SDK runtime simply stays skipped until a provider is configured.
    """

    try:
        return provider_registry.get_chain()
    except Exception:
        return None


async def _activate_product_sdk_runtime(
) -> None:
    """Activate SDK Runtime Stack and ingress (Slice C production)."""
    global _sdk_runtime_stack, _sdk_ingress, _sdk_runtime_catalog
    global _sdk_provider_binding_resolver, _sdk_run_binding_registry
    global _sdk_tool_authority_registry, _sdk_runtime_tool_inventory

    from deskpet.sdk_adapters.ingress import SdkRuntimeIngress
    from deskpet.workflows.store import RuntimeActivationCommand

    workflow_service = service_context.get("workflow_service")
    if workflow_service is None:
        raise RuntimeError("workflow service is required for SDK Runtime activation")
    uow = workflow_service.execution_uow

    await uow.initialize()
    state = await uow.get_runtime_state()
    if state.phase == "legacy":
        state = await uow.activate_runtime(command=RuntimeActivationCommand.advance())
    if state.phase == "draining":
        while lease := await uow.activate_runtime(command=RuntimeActivationCommand.claim(
            f"deskpet-sdk:{os.getpid()}", lease_seconds=30.0
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

    provider_registry = service_context.get("provider_registry")
    if provider_registry is None:
        logger.warning("product_sdk_runtime_skipped", reason="provider_registry unavailable")
        return

    # Runtime activation is independent of the current Provider chain. Fresh
    # installs can add their first Provider later without replacing the stack.
    try:
        stack = await _build_product_sdk_runtime_stack(state.generation)
    except Exception as exc:
        logger.warning(
            "product_sdk_runtime_skipped",
            reason=f"build_failed: {exc}",
            exc_info=True,
        )
        return

    if state.phase == "activated":
        state = await uow.activate_runtime(command=RuntimeActivationCommand.advance())

    # Start SDK Runtime
    await stack.start()

    # Create ingress facade
    ingress = SdkRuntimeIngress(stack)

    # The SDK runtime persists the authoritative model-visible Tool Catalog
    # while the stack starts.  Skill-install verification Runs must bind to
    # that exact generation/fingerprint pair; the product runtime generation
    # is a different lifecycle counter and cannot be substituted here.
    runtime_catalog = service_context.get("sdk_runtime_catalog")
    if not isinstance(runtime_catalog, Mapping):
        await stack.close()
        raise RuntimeError("SDK runtime Tool Catalog identity unavailable")
    runtime_catalog_generation = runtime_catalog.get("generation")
    runtime_catalog_fingerprint = runtime_catalog.get("content_fingerprint")
    if (
        not isinstance(runtime_catalog_generation, int)
        or runtime_catalog_generation < 1
        or not isinstance(runtime_catalog_fingerprint, str)
        or not runtime_catalog_fingerprint.strip()
    ):
        await stack.close()
        raise RuntimeError("SDK runtime Tool Catalog identity is incomplete")

    # Complete the Project-scoped Skill-install composition only after the SDK
    # stack is ready.  The bindable holders were published with the Capability
    # runtime so the root driver could be frozen without a cyclic constructor;
    # the live verification service now joins that driver to the same execution
    # database/Manager owner used by install intent, publish, and attestation.
    from deskpet.sdk_adapters.capability_catalog import (
        ProductCapabilityCatalogSourceAdapter,
    )
    from deskpet.sdk_adapters.skill_install_verification import (
        SdkTerminalCapabilityReleaseReconciler,
        SkillInstallVerificationRunService,
    )

    capability_store = service_context.get("capability_store")
    capability_platform = service_context.get("capability_platform")
    skill_snapshot_resolver = service_context.get(
        "legacy_frozen_skill_instruction_resolver"
    )
    skill_install_runtime_verifier = service_context.get(
        "skill_install_runtime_verifier"
    )
    skill_install_driver_factory = service_context.get(
        "skill_install_verification_driver_factory"
    )
    project_skill_install_service = service_context.get(
        "project_skill_install_service"
    )
    if any(
        item is None
        for item in (
            capability_store,
            capability_platform,
            skill_snapshot_resolver,
            skill_install_runtime_verifier,
            skill_install_driver_factory,
            project_skill_install_service,
        )
    ):
        await stack.close()
        raise RuntimeError("Project Skill-install verification composition unavailable")
    if not callable(
        getattr(skill_snapshot_resolver, "resolve_frozen_instruction", None)
    ):
        await stack.close()
        raise RuntimeError(
            "Project Skill-install Run-frozen resolver unavailable"
        )
    skill_install_verification = SkillInstallVerificationRunService(
        store=capability_store,
        platform=capability_platform,
        catalog_source=ProductCapabilityCatalogSourceAdapter(),
        resolver=skill_snapshot_resolver,
        ingress=ingress,
        runtime_stack=stack,
        tool_catalog_generation=lambda: runtime_catalog_generation,
        tool_catalog_fingerprint=lambda: runtime_catalog_fingerprint,
    )
    skill_install_runtime_verifier.bind(skill_install_verification)
    skill_install_driver_factory.bind(
        skill_install_verification.driver_for_attempt
    )
    await SdkTerminalCapabilityReleaseReconciler(
        store=capability_store,
        platform=capability_platform,
        runtime_stack=stack,
    ).reconcile_pending()

    _sdk_runtime_stack = stack
    _sdk_ingress = ingress
    _sdk_runtime_catalog = service_context.get("sdk_runtime_catalog")
    _sdk_provider_binding_resolver = service_context.get(
        "sdk_provider_binding_resolver"
    )
    _sdk_run_binding_registry = getattr(
        _sdk_provider_binding_resolver, "registry", None
    )
    _sdk_tool_authority_registry = service_context.get(
        "sdk_tool_authority_registry"
    )
    _sdk_runtime_tool_inventory = service_context.get(
        "sdk_runtime_tool_inventory"
    )
    for recovered_run_id in tuple(_sdk_retained_presentations):
        _ensure_sdk_recovery_watcher(recovered_run_id)

    logger.info(
        "product_sdk_runtime_ready",
        generation=state.generation,
        phase=state.phase,
        sdk_version=SDK_VERSION,
        ingress="closed",
    )


async def _activate_companion_runtime_adapter_and_open_ingress() -> None:
    """Bind background Runs before opening the sole product ingress (SDK Runtime).

    Skips if SDK Runtime is unavailable (e.g. no provider configured).
    """

    global _sdk_ingress
    if _sdk_runtime_stack is None or _sdk_ingress is None:
        logger.warning("companion_runtime_adapter_skipped", reason="SDK Runtime unavailable - configure LLM provider in Settings")
        return

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
        # Use SDK ingress to create host
        host, _provider, _providers, _provider_snapshot = await _issue_product_harness_host(
            session_id=session_id,
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
        client=_sdk_ingress.require_ready().client,
        store=store,
        host_factory=_background_host,
        prepared_context_factory=growth_pipeline.prepared_context,
        text_factory=growth_pipeline.prompt,
        result_postprocessor=growth_pipeline.postprocess,
    )

    # Open SDK ingress
    _sdk_ingress.open()
    foreground_runtime = service_context.get(
        "human_memory_foreground_runtime_execution_authority"
    )
    if foreground_runtime is not None:
        # Startup recovery enters through the same public wake as enqueue;
        # SQLite, not this process task, decides whether work exists.
        await foreground_runtime.after_enqueue(subject="deskpet-local-owner-v1")

    # Manager publication may have committed before a crash or a bounded
    # verification failure.  Resume those internal SDK Runs only after the
    # product's single ingress is accepting starts; no repeated user
    # authorization is needed because the durable handoff already settled.
    project_skill_install_service = service_context.get(
        "project_skill_install_service"
    )
    if project_skill_install_service is None:
        raise RuntimeError("Project Skill-install recovery service unavailable")
    await project_skill_install_service.reconcile_pending_runtime_verifications()

    logger.info(
        "companion_runtime_adapter_ready product_ingress=open phase=%s sdk_version=%s",
        _growth_authority_router.current.phase.value,
        SDK_VERSION,
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
    from deskpet.companion.companion_message_projection import (
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
    # 2026-08-09：原先这里为保留会话 `default` 开了一条白名单。保留会话已移除，
    # 会话 id 一律是 uuid（或存量 `task-` 前缀），不再需要该特例。
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
# style ring gauge. Keyed by chat session_id (uuid，或 "code-XXX")。
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


async def _attach_context_snapshot_metadata(
    session_db: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Join public snapshot identity without conflating its version axis."""

    result = dict(payload)
    sid = str(result.get("session_id") or "").strip()
    if not sid or not hasattr(
        session_db, "get_latest_sdk_context_public_snapshot"
    ):
        return result
    public = await session_db.get_latest_sdk_context_public_snapshot(sid)
    if public is None:
        result.update({"snapshot_id": None, "snapshot_version": None})
        return result
    if not isinstance(public, dict) or str(
        public.get("session_id") or ""
    ).strip() != sid:
        raise RuntimeError("sdk_context_public_snapshot_session_mismatch")
    result["snapshot_id"] = str(public.get("snapshot_id") or "").strip() or None
    version = public.get("snapshot_version")
    result["snapshot_version"] = (
        int(version)
        if isinstance(version, int) and not isinstance(version, bool)
        and version >= 0 else None
    )
    result["snapshot_fingerprint"] = str(
        public.get("snapshot_fingerprint") or ""
    )[:160]
    return result


async def _read_context_usage_authority(
    session_id: str,
    *,
    attach_snapshot: bool = True,
) -> dict[str, Any]:
    """Read the durable Session authority; never fabricate a global stub."""

    context_sdb = service_context.get("session_db")
    if context_sdb is None or not hasattr(
        context_sdb, "get_context_usage_state"
    ):
        raise RuntimeError("context_usage_authority_unavailable")
    authoritative = await context_sdb.get_context_usage_state(session_id)
    if attach_snapshot:
        authoritative = await _attach_context_snapshot_metadata(
            context_sdb, authoritative
        )
    return _cache_context_usage_state(authoritative)


async def _send_context_usage_authority(
    websocket: Any,
    session_db: Any,
    session_id: str,
) -> dict[str, Any]:
    """Send the latest durable Context Usage authority to one UI peer."""

    authoritative = await session_db.get_context_usage_state(session_id)
    authoritative = await _attach_context_snapshot_metadata(
        session_db, authoritative
    )
    authoritative = _cache_context_usage_state(authoritative)
    await websocket.send_json({
        "type": "context_usage",
        "payload": authoritative,
    })
    return authoritative


async def _broadcast_context_usage_snapshot(
    session_db: Any, session_id: str
) -> bool:
    """Publish one strictly newer durable usage authority to matching peers."""

    from deskpet.sdk_adapters.context_authority import canonical_sha256

    incoming = await session_db.get_context_usage_state(session_id)
    incoming = await _attach_context_snapshot_metadata(session_db, incoming)
    if str(incoming.get("session_id") or "").strip() != str(session_id):
        raise RuntimeError("context_usage_projection_session_mismatch")
    version = int(incoming.get("version") or 0)
    if version <= 0:
        return False
    previous = _session_context_state.get(str(session_id))
    previous_version = int((previous or {}).get("version") or 0)
    if version < previous_version:
        return False
    if version == previous_version and previous is not None:
        if canonical_sha256(previous) != canonical_sha256(incoming):
            raise RuntimeError("context_usage_equal_version_conflict")
        return False
    _session_context_state[str(session_id)] = dict(incoming)
    await _broadcast_default_chat_peers(
        None,
        {"type": "context_usage", "payload": incoming},
    )
    return True


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
        payload = await _attach_context_snapshot_metadata(session_db, payload)
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


_PUBLIC_CONTEXT_SENSITIVE = re.compile(
    r"(?i)(authorization\s*:|bearer\s+[a-z0-9._-]+|api[_ -]?key|"
    r"cookie\s*:|(?:^|\s)(?:/Users/|/home/|[A-Za-z]:\\)|"
    r"reasoning_content|private[_ -]?reasoning)"
)


def _public_context_section(raw: Any) -> dict[str, Any] | None:
    """Normalize one already-redacted snapshot section for Inspector."""

    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip()[:80]
    label = str(raw.get("label") or "").strip()[:160]
    if not kind or not label:
        return None
    estimated = raw.get("estimated_tokens")
    tokens = (
        int(estimated)
        if isinstance(estimated, int) and not isinstance(estimated, bool)
        and estimated >= 0
        else None
    )
    availability = str(raw.get("availability") or "").strip()
    if availability not in {"available", "unavailable"}:
        availability = "available" if tokens is not None else "unavailable"
    public_ref = raw.get("ref")
    public_text = public_ref.strip() if isinstance(public_ref, str) else ""
    public_preview = (
        public_text[:600]
        if public_text and not _PUBLIC_CONTEXT_SENSITIVE.search(public_text)
        else None
    )
    item: dict[str, Any] = {
        "kind": kind,
        "label": label,
        "tokens": tokens,
        "token_source": "estimated" if tokens is not None else "unavailable",
        "availability": availability,
        "public_preview": public_preview,
        "preview_truncated": bool(
            isinstance(public_ref, str) and len(public_ref.strip()) > 600
        ),
    }
    count = raw.get("count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        item["count"] = count
    return item


async def _read_context_breakdown_authority(
    session_id: str,
) -> dict[str, Any]:
    """Read only the durable public SDK snapshot and Context usage state."""

    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session_id is required")
    context_sdb = service_context.get("session_db")
    if context_sdb is None or not hasattr(
        context_sdb, "get_latest_sdk_context_public_snapshot"
    ):
        raise RuntimeError("sdk_context_public_snapshot_authority_unavailable")

    public_snapshot = await context_sdb.get_latest_sdk_context_public_snapshot(
        sid
    )
    usage = await _read_context_usage_authority(sid, attach_snapshot=False)
    if public_snapshot is not None:
        if not isinstance(public_snapshot, dict):
            raise RuntimeError("sdk_context_public_snapshot_invalid")
        if str(public_snapshot.get("session_id") or "").strip() != sid:
            raise RuntimeError("sdk_context_public_snapshot_session_mismatch")

    raw_sections = (
        public_snapshot.get("sections", [])
        if isinstance(public_snapshot, dict)
        else []
    )
    sections = [
        normalized
        for raw in raw_sections if isinstance(raw_sections, list)
        if (normalized := _public_context_section(raw)) is not None
    ]
    snapshot_available = isinstance(public_snapshot, dict)
    binding = (
        public_snapshot.get("provider_binding", {})
        if snapshot_available else {}
    )
    budget = public_snapshot.get("budget", {}) if snapshot_available else {}
    binding = binding if isinstance(binding, dict) else {}
    budget = budget if isinstance(budget, dict) else {}
    has_measurement = bool(usage.get("has_measurement"))
    prompt_tokens = usage.get("prompt_tokens")
    measured_prompt_tokens = (
        int(prompt_tokens)
        if has_measurement and isinstance(prompt_tokens, int)
        and not isinstance(prompt_tokens, bool) and prompt_tokens >= 0
        else None
    )
    source = str(usage.get("source") or "")
    context_state = (
        "legacy_incomplete" if usage.get("legacy_incomplete")
        else "measured" if has_measurement
        else "binding_only" if source == "binding_only"
        else "unavailable"
    )
    snapshot_version = (
        public_snapshot.get("snapshot_version") if snapshot_available else None
    )
    usage_version = usage.get("version")
    return {
        "session_id": sid,
        "snapshot_id": (
            str(public_snapshot.get("snapshot_id") or "").strip() or None
            if snapshot_available else None
        ),
        "snapshot_version": (
            int(snapshot_version)
            if isinstance(snapshot_version, int)
            and not isinstance(snapshot_version, bool) and snapshot_version >= 0
            else None
        ),
        "snapshot_fingerprint": (
            str(public_snapshot.get("snapshot_fingerprint") or "")[:160]
            if snapshot_available else ""
        ),
        "sample_id": str(usage.get("sample_id") or "").strip() or None,
        "usage_version": (
            int(usage_version)
            if isinstance(usage_version, int) and not isinstance(usage_version, bool)
            and usage_version >= 0 else None
        ),
        "availability": "available" if snapshot_available else "unavailable",
        "context_state": context_state,
        "model": str(
            binding.get("model_id") or usage.get("model_id")
            or usage.get("model") or ""
        )[:160],
        "sections": sections,
        "total_estimated_tokens": (
            sum(item["tokens"] or 0 for item in sections)
            if snapshot_available else None
        ),
        "last_usage_prompt_tokens": measured_prompt_tokens,
        "context_window": budget.get("context_window") if snapshot_available else None,
        "effective_ceiling": budget.get("effective_ceiling") if snapshot_available else None,
        "compact_at": budget.get("compact_at") if snapshot_available else None,
        "updated_at": usage.get("updated_at"),
        "ts": time.time(),
    }


async def _handle_context_breakdown_request(
    websocket: Any,
    payload: Any,
    *,
    owner_session_id: str,
) -> bool:
    """Serve one owner/session/correlation-fenced Inspector query."""

    body = payload if isinstance(payload, dict) else {}
    requested_sid = str(body.get("session_id") or "").strip()
    correlation_id = str(
        body.get("request_id") or body.get("correlation_id") or ""
    ).strip()
    owner_sid = str(owner_session_id or "").strip()
    if (
        not requested_sid or not correlation_id or len(correlation_id) > 200
        or not owner_sid or requested_sid != owner_sid
    ):
        await websocket.send_json({
            "type": "error",
            "payload": {
                "code": "context_breakdown_request_invalid",
                "message": "Context Inspector request identity is invalid",
            },
        })
        return False
    breakdown = await _read_context_breakdown_authority(requested_sid)
    if breakdown.get("session_id") != requested_sid:
        raise RuntimeError("context_breakdown_session_mismatch")
    await websocket.send_json({
        "type": "context_breakdown_response",
        "payload": {**breakdown, "correlation_id": correlation_id},
    })
    return True


_PEER_BROADCAST_TIMEOUT_S = 1.0
_GLOBAL_BLOCKING_UI_EVENTS = frozenset({
    "permission_request",
    "external_wait_request",
    "project_directory_request",
    "clarification_request",
})




_SDK_PUBLIC_WORK_NARRATION_PROMPT = """\
你在执行需要调用工具的任务时，必须在每一轮 tool_calls 的 assistant.content 中先写一段面向用户的公开工作叙述。
要求：
1. 使用 1-2 句简洁、自然且与当前步骤相关的文字，说明你现在要做什么，以及必要时说明它与上一结果的关系。
2. 文字必须由你根据当前任务和已看到的公开工具结果自行撰写，避免重复固定模板。
3. 不要输出私有思维链、逐步内心分析、隐藏 reasoning、密钥、完整工具参数或原始工具结果。
4. 工具参数 schema 可能提供可选的 `deskpet_public_progress`；支持时可在这个参数中写上述公开叙述，但缺失、空值或类型不正确都不能阻断工具调用，也不得为补齐它而伪造内容。
5. 如果 Provider 支持在 assistant.content 中同时返回公开文字，优先写入自然的公开叙述；无法提供时保持兼容，不要输出私有推理替代它。
这段公开叙述会展示在“思考过程”区域，但不会作为后续模型上下文保存。\
"""

_SDK_AUTOMATIC_MEMORY_PROMPT = """\
Simple Harness 会在每个成功完成的 Turn 结束后自动记录可复用的事实和偏好。
普通事实、偏好以及“记住 X”这类请求不得调用 memory_write；直接自然确认即可。
只有用户明确要求置顶、永不衰减，或明确指定 L1/L2/L3 Memory 层级时，才调用 memory_write。
读取既有记忆由本 Turn 冻结的 Agent Memory 上下文提供；不要为了普通召回调用显式 Memory Tool。\
"""

_SDK_SKILL_DISCOVERY_PROMPT = """\
专业 Skill 使用规则：
1. 当用户明确要求把一个公开 GitHub 仓库中的 Skill 安装到当前 Project 时，直接调用 skill_install 并传入原始 URL。不要调用 agent、web、file 或 shell 去研究、下载、复制或手工安装，也不要先用 tool_search 查找安装工具；skill_install 会完成受信来源解析、预览、确认、发布和运行时验证。
2. 当用户请求或附件明显可能匹配某个已安装的专业 Skill 时，必须先调用 tool_search 搜索 Skill capability，再开始完成任务；用户不需要输入斜杠命令或 Skill 名称。
3. 如果搜索结果包含 kind=skill_resource 的匹配项，必须复制该结果返回的精确 selection_key，并调用 skill_invoke，将 selection_key 作为 skill_name 加载冻结的 Skill 正文。不得只根据 Skill 名称、触发词、描述或常识猜测正文并直接回答。
4. 加载成功后遵循返回的 instruction 完成任务；Skill 只增加任务说明和更窄的工具范围，不会扩大权限，后续工具仍按正常授权流程执行。
5. 如果搜索没有返回匹配 Skill，或者请求明显不需要专业 Skill，则继续正常处理；不要编造 locator，也不要反复搜索同一个查询。\
"""

_SDK_DEFERRED_TOOL_PROMPT = """\
延迟工具使用规则：
1. tool_search 只返回候选；必须复制结果中的完整 capability_id 调用 tool_describe，不得缩写或猜测 ID。
2. tool_describe 成功后，下一步必须调用 tool_activate，并逐字复制其顶层 capability_id、schema_hash、describe_nonce。
3. 在 tool_activate 成功、且下一轮模型输入正式出现目标工具之前，绝不能直接调用该目标工具；tool_describe 本身不等于激活。\
"""


def _trusted_local_page_url_from_env() -> str | None:
    """Resolve an optional host-provided local page without exposing raw input."""

    from deskpet.sdk_adapters.context_preparation import (
        normalize_trusted_local_page_url,
    )

    raw = os.environ.get("DESKPET_LOCAL_PAGE_URL", "").strip()
    if not raw:
        return None
    try:
        return normalize_trusted_local_page_url(raw)
    except ValueError:
        logger.warning("sdk_local_page_context_rejected reason=invalid_loopback_url")
        return None


async def _sdk_context_usage_from_projection_only(*_args: Any, **_kwargs: Any) -> None:
    """SDK usage is projected from durable physical-attempt receipts only."""

    return None


def _sdk_text_tokens(value: object) -> int:
    from deskpet.sdk_adapters.context_partitions import text_tokens

    return text_tokens(value)


async def _bounded_sdk_history(
    session_db: Any,
    *,
    session_id: str,
    root_run_id: str,
    token_budget: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Keep the newest complete conversational rows within a hard budget."""

    rows = await session_db.get_recent_messages(session_id, limit=10_000)
    allowed = {
        "user": {"legacy_message", "user_message"},
        "assistant": {"legacy_message", "assistant_message", "final_assistant"},
    }
    candidates = [
        {
            "root_run_id": str(row.get("root_run_id") or ""),
            "role": str(row.get("role") or ""),
            "content": str(row["content"]),
            "context_visibility": "conversation",
            "projection_kind": str(row.get("projection_kind") or "legacy_message"),
        }
        for row in rows
        if str(row.get("root_run_id") or "") != str(root_run_id)
        and str(row.get("context_visibility") or "conversation") == "conversation"
        and str(row.get("projection_kind") or "legacy_message")
        in allowed.get(str(row.get("role") or ""), set())
        and isinstance(row.get("content"), str)
        and bool(row.get("content"))
    ]
    kept: list[dict[str, Any]] = []
    remaining = max(0, int(token_budget))
    for row in reversed(candidates):
        cost = _sdk_text_tokens(row["content"]) + 4
        if cost > remaining:
            break
        kept.append(row)
        remaining -= cost
    kept.reverse()
    return kept, len(kept) != len(candidates)


async def _prepare_sdk_context_snapshot(
    *,
    session_db: Any,
    session_id: str,
    request_id: str,
    root_run_id: str,
    sdk_run_id: str,
    turn_id: str,
    text: str,
    provider_binding: dict[str, Any],
    catalog: dict[str, Any],
    attachment_blocks: tuple[dict[str, Any], ...],
    project: Mapping[str, Any],
    task_scope_id: str,
    persona_text: str,
    memory_items: tuple[dict[str, Any], ...] | None = None,
    prepared_skill_scope: Any | None = None,
    skill_arguments: tuple[str, ...] = (),
    skill_instruction_resolver: Any | None = None,
    context_query_id: str | None = None,
    memory_result_id: str | None = None,
    memory_result_hash: str | None = None,
    memory_result_payload: Mapping[str, Any] | None = None,
) -> Any:
    """Prepare once; the returned messages are the sole Provider authority."""

    from deskpet.sdk_adapters.context_authority import PreparedSdkContextSnapshotV1
    from deskpet.sdk_adapters.context_preparation import (
        SdkContextPreparationService,
        SdkContextSources,
        trusted_project_task_snapshot,
    )
    from deskpet.companion.skills import (
        PreparedSkillInvocationScopeV1,
        ResolvedSkillInstructionV1,
    )

    skill_items: tuple[dict[str, Any], ...] = ()
    if prepared_skill_scope is not None:
        if not isinstance(
            prepared_skill_scope, PreparedSkillInvocationScopeV1
        ):
            raise TypeError("SDK skill selection must be a frozen typed scope")
        resolve_instruction = getattr(
            skill_instruction_resolver, "resolve_instruction", None
        )
        if not callable(resolve_instruction):
            raise RuntimeError("frozen_skill_resolver_not_injected")
        resolved_skill = resolve_instruction(
            prepared_skill_scope,
            tuple(str(item) for item in skill_arguments),
        )
        if inspect.isawaitable(resolved_skill):
            resolved_skill = await resolved_skill
        if not isinstance(resolved_skill, ResolvedSkillInstructionV1):
            raise TypeError("frozen skill resolver returned an invalid instruction")
        if resolved_skill.scope != prepared_skill_scope:
            raise RuntimeError("frozen skill resolver changed the selected scope")
        skill_items = ({"instruction": resolved_skill.instruction},)

    # Compatibility-only direct helper callers predate typed Session binding
    # and may pass None/a path string. They cannot establish project authority;
    # treat them as explicitly projectless. Production always passes the
    # mapping returned by ProjectBindingService.
    project_resolution = (
        project if isinstance(project, Mapping) else {"kind": "projectless"}
    )
    project_snapshot = trusted_project_task_snapshot(
        task_scope_id=task_scope_id,
        root_run_id=root_run_id,
        request_id=request_id,
        workspace_resolution=project_resolution,
        local_page_url=_trusted_local_page_url_from_env(),
    )
    project_rule_instructions: tuple[str, ...] = ()
    if str(project_resolution.get("kind") or "") == "project_bound":
        from deskpet.agent.assembler.bundle import AssemblyPolicy
        from deskpet.agent.assembler.components.base import ComponentContext
        from deskpet.agent.assembler.components.project_rules import (
            ProjectRulesComponent,
        )

        execution_root = str(project_resolution["effective_root"])
        rule_slice = await ProjectRulesComponent().provide(
            ComponentContext(
                task_type="chat",
                policy=AssemblyPolicy(task_type="chat", prefer=["project_rules"]),
                user_message=text,
                config={
                    "workspace_context": {
                        "verified": True,
                        "root": execution_root,
                        "active_path": execution_root,
                    }
                },
            )
        )
        project_rule_instructions = tuple(
            str(fragment.content)
            for fragment in rule_slice.fragments
            if str(fragment.content).strip()
        )
    context_window = int(provider_binding["context_window"])
    compact_at = max(1, int(context_window * 0.8))
    reserved = (
        int(catalog.get("schema_token_count") or 0)
        + _sdk_text_tokens(_SDK_PUBLIC_WORK_NARRATION_PROMPT)
        + _sdk_text_tokens(_SDK_AUTOMATIC_MEMORY_PROMPT)
        + _sdk_text_tokens(_SDK_SKILL_DISCOVERY_PROMPT)
        + _sdk_text_tokens(_SDK_DEFERRED_TOOL_PROMPT)
        + _sdk_text_tokens(persona_text)
        + _sdk_text_tokens(text)
        + sum(_sdk_text_tokens(item.get("text")) for item in (memory_items or ()))
        + sum(
            _sdk_text_tokens(item.get("instruction")) for item in skill_items
        )
        + sum(_sdk_text_tokens(item) for item in project_rule_instructions)
        + sum(_sdk_text_tokens(item) for item in attachment_blocks)
        + _sdk_text_tokens(project_snapshot)
        + 256
    )
    if reserved > compact_at:
        raise RuntimeError("sdk_context_required_content_exceeds_budget")
    history, truncated = await _bounded_sdk_history(
        session_db,
        session_id=session_id,
        root_run_id=root_run_id,
        token_budget=max(0, compact_at - reserved),
    )
    service = SdkContextPreparationService(
        SdkContextSources(
            history=lambda _session_id: history,
            persona=lambda: (
                persona_text
                + "\n\n"
                + _SDK_PUBLIC_WORK_NARRATION_PROMPT
                + "\n\n"
                + _SDK_AUTOMATIC_MEMORY_PROMPT
                + "\n\n"
                + _SDK_SKILL_DISCOVERY_PROMPT
                + "\n\n"
                + _SDK_DEFERRED_TOOL_PROMPT
            ),
            memory=(
                (lambda _session_id, _text: memory_items)
                if memory_items is not None
                else None
            ),
            # A fresh non-Skill turn has an explicit empty source.  Only an
            # immutable selection captured by slash ingress can add text.
            skills=lambda _text: skill_items,
        )
    )
    prepared = await service.prepare(
        session_id=session_id,
        request_id=request_id,
        root_run_id=root_run_id,
        sdk_run_id=sdk_run_id,
        turn_id=turn_id,
        text=text,
        provider_binding=provider_binding,
        catalog=catalog,
        project_task_snapshot=project_snapshot,
        project_rule_instructions=project_rule_instructions,
        attachment_blocks=attachment_blocks,
        context_query_id=context_query_id,
        memory_result_id=memory_result_id,
        memory_result_hash=memory_result_hash,
        memory_result_payload=memory_result_payload,
    )
    private = prepared.private_record()
    budget = dict(private["budget"])
    budget.update(
        context_window=context_window,
        effective_ceiling=compact_at,
        compact_at=compact_at,
        truncated=truncated,
    )
    return PreparedSdkContextSnapshotV1.build(
        session_id=session_id,
        request_id=request_id,
        root_run_id=root_run_id,
        sdk_run_id=sdk_run_id,
        turn_id=turn_id,
        provider_binding=private["provider_binding"],
        provider_messages=private["provider_messages"],
        catalog=private["catalog"],
        attachments=private["attachments"],
        sections=private["sections"],
        budget=budget,
        lineage=private.get("lineage") or {},
        memory=private.get("memory"),
        current_message=private["current_message"],
        memory_projection_version=2,
    )


async def _execute_sdk_run(
    *,
    session_id: str,
    request_id: str,
    turn_id: int,
    task_scope_id: str | None,
    prepared_snapshot: Any,
    run_binding: Any,
    context: Any,  # RunPresentationContext
    websocket: Any,  # WebSocket
    root_run_id: str,
    conversation: Any | None = None,
    context_stage: Any | None = None,
    context_binding_id: str | None = None,
) -> None:
    """Execute Agent via SDK Runtime with event delivery to WebSocket.

    Replaces old ProductVenueRunAdapter.open() flow. Creates a
    ProductDeliveryAdapter, registers it in the global registry,
    starts SDK Runtime execution, and waits for completion.

    Args:
        session_id: Session identifier
        request_id: Request identifier
        turn_id: Turn number
        task_scope_id: Optional task scope identifier
        text: User input text
        context: RunPresentationContext with all presentation services
        websocket: WebSocket connection for real-time delivery
        root_run_id: Root run identifier

    Raises:
        SdkRuntimeNotReady: If SDK Runtime ingress is not ready
        Exception: Any SDK Runtime execution errors
    """
    from deskpet.agent.run_presenter import (
        build_product_run_presenter,
        CanonicalRunEventPresentationAdapter,
        PresentationState,
    )
    from deskpet.sdk_adapters.delivery import ProductDeliveryAdapter
    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters
    from deskpet.sdk_adapters.ingress import SdkRuntimeIngress

    # The frozen tool authority is keyed by the SDK's deterministic physical
    # Run identity, so compute it before reading any authority into RunStart.
    sdk_run_id = SdkRuntimeIngress._compute_run_id(  # noqa: SLF001
        session_id,
        request_id,
        str(turn_id),
    ).value
    private = prepared_snapshot.private_record()
    catalog = dict(private["catalog"])
    budget = dict(private["budget"])
    tool_authority = (
        _sdk_tool_authority_registry.resolve(sdk_run_id)
        if _sdk_tool_authority_registry is not None
        else None
    )
    if _sdk_tool_authority_registry is not None and tool_authority is None:
        raise RuntimeError("SDK Run Tool authority is unavailable")
    payload = {
        "input": {"text": str(getattr(context, "text", ""))},
        "messages": private["provider_messages"],
        "capability_snapshot": {"tools": list(catalog["tool_names"])},
        "context_metadata": {
            "session_id": session_id,
            "root_run_id": root_run_id,
            "request_id": request_id,
            "task_scope_id": task_scope_id,
            "workspace_root": (
                tool_authority.task_work_context.workspace_root
                if tool_authority is not None
                else None
            ),
            "workspace_binding_version": (
                tool_authority.task_work_context.binding_version
                if tool_authority is not None
                else 1
            ),
            "conversation_boundary_ref": context.conversation_boundary_ref,
            "conversation_boundary_version": 1,
            "snapshot_id": prepared_snapshot.snapshot_id,
            "binding_epoch": run_binding.binding_epoch,
            "context_window": run_binding.context_window,
            "effective_ceiling": int(budget.get("effective_ceiling") or 0),
            "run_binding": run_binding.to_record(),
            "tool_authority": (
                tool_authority.run_start_record()
                if tool_authority is not None
                else None
            ),
        },
    }

    # Create presentation infrastructure
    presenter = build_product_run_presenter()
    adapter = CanonicalRunEventPresentationAdapter()
    state = PresentationState()

    # SDK owns a deterministic internal execution id, while the product UI and
    # SessionDB use the canonical root id reserved by Host. Predict the SDK id
    # from the ingress's single identity function so the delivery bridge can be
    # registered *before* start() makes the Run visible to a worker. Registering
    # after awaiting start() races a fast first Provider/tool turn and silently
    # drops its public narration and tool lifecycle.
    # Create ProductDeliveryAdapter for this run
    delivery_adapter = ProductDeliveryAdapter(
        session_id=session_id,
        request_id=request_id,
        run_id=sdk_run_id,
        presenter=presenter,
        adapter=adapter,
        context=context,
        state=state,
    )

    # Register adapter in global registry before SDK start (for Provider,
    # ProductToolsAdapter, and _DeliverySink routing).
    _delivery_adapters[sdk_run_id] = delivery_adapter
    _sdk_run_ids_by_root[root_run_id] = sdk_run_id
    terminal_binding_state: str | None = "failed"
    observability_token = _sdk_observability.bind_ingress(
        run_id=sdk_run_id,
        session_id=session_id,
        request_id=request_id,
    )

    try:
        receipt = await _sdk_ingress.start(
            session_id=session_id,
            request_id=request_id,
            turn_id=str(turn_id),
            payload=payload,
            session_generation=run_binding.catalog_generation,
            tool_catalog_fingerprint=run_binding.catalog_fingerprint,
            provider_budget_fingerprint=run_binding.budget_fingerprint,
            conversation=conversation,
            context_preparation_mode=(
                "consumer_prepared" if context_stage is not None else None
            ),
            context_stage_id=(
                str(context_stage.stage_id) if context_stage is not None else None
            ),
            context_stage_hash=(
                str(context_stage.private_snapshot_hash)
                if context_stage is not None
                else None
            ),
            prepared_context=(
                dict(context_stage.private_snapshot)
                if context_stage is not None
                else None
            ),
        )
        if receipt.run_id != sdk_run_id:
            raise RuntimeError("SDK ingress returned an unexpected Run identity")
        if context_binding_id is not None:
            context_sources = service_context.get("sdk_context_source_repository")
            if context_sources is None:
                raise RuntimeError("SDK context source repository is unavailable")
            await context_sources.mark_claimed(
                context_binding_id,
                claim_token=receipt.run_id,
            )

        # Send run_started event to WebSocket
        started = {
            "type": "chat_v2_run_started",
            "payload": {
                "session_id": session_id,
                "run_id": root_run_id,
                "request_id": request_id,
                "turn_id": turn_id,
                "task_scope_id": task_scope_id,
                "conversation_boundary_ref": context.conversation_boundary_ref,
                "conversation_boundary_version": 1,
                "projection_version": 0,
            },
        }
        await websocket.send_json(started)
        await _broadcast_default_chat_peers(websocket, started)

        logger.info(
            "sdk_run_started",
            session_id=session_id,
            request_id=request_id,
            sdk_run_id=sdk_run_id,
            root_run_id=root_run_id,
        )

        # Wait for SDK Runtime to complete
        # Events are pushed via DeliveryDispatcher → _DeliverySink → ProductDeliveryAdapter
        await _sdk_ingress.wait_idle(sdk_run_id)

        # Query final state
        final_state = _sdk_ingress.query(sdk_run_id)
        sdk_state = getattr(final_state, "state", final_state)
        sdk_state_value = str(getattr(sdk_state, "value", sdk_state))
        if sdk_run_id in _sdk_cancel_requested_run_ids:
            # chat_v2_interrupt owns the canonical cancelled projection and
            # acknowledgement. Do not race it with a transient SDK
            # waiting/running read and manufacture run_failed in the UI.
            logger.info(
                "sdk_run_user_cancelled",
                session_id=session_id,
                request_id=request_id,
                root_run_id=root_run_id,
                sdk_run_id=sdk_run_id,
                observed_state=sdk_state_value,
            )
            terminal_binding_state = "cancelled"
            return
        if sdk_state_value == "waiting":
            terminal_binding_state = None
            _sdk_retained_presentations[sdk_run_id] = (
                delivery_adapter, presenter, state, context
            )
            if _sdk_provider_binding_resolver is not None:
                _sdk_provider_binding_resolver.mark_waiting(sdk_run_id)
            if _sdk_tool_authority_registry is not None:
                _sdk_tool_authority_registry.mark_waiting(sdk_run_id)
            await _project_open_sdk_authorizations(
                websocket,
                sdk_run_id=sdk_run_id,
            )
            logger.info(
                "sdk_run_waiting_binding_retained",
                sdk_run_id=sdk_run_id,
                root_run_id=root_run_id,
            )
            return
        if sdk_state_value != "completed":
            terminal_binding_state = (
                sdk_state_value
                if sdk_state_value in {"failed", "cancelled"}
                else "failed"
            )
            # A failed/cancelled SDK run has no assistant text to present.
            # Emit the product terminal error instead of an empty final frame,
            # so the UI can settle the canonical root projection visibly.
            await _send_chat_error(
                websocket,
                {
                    "type": "chat_v2_error",
                    "payload": {
                        "session_id": session_id,
                        "run_id": root_run_id,
                        "request_id": request_id,
                        "task_scope_id": task_scope_id,
                        "error": "run_failed",
                        "detail": f"sdk_run_{sdk_state_value}",
                    },
                },
                session_id=session_id,
                request_id=request_id,
            )
            logger.warning(
                "sdk_run_terminal_error_projected",
                session_id=session_id,
                request_id=request_id,
                root_run_id=root_run_id,
                sdk_run_id=sdk_run_id,
                state=sdk_state_value,
            )
            return

        # SDK 的 ReAct driver 不产生 delivery 事件，assistant 文本只落在 SDK 的
        # context 里。这里手动把它桥接回 presenter，走 _present_final 的
        # 「写 SessionDB + 推 WebSocket」既有展示链路。
        if _sdk_context_port is not None:
            try:
                from simple_harness import RunId as SdkRunId
                from agent.agent_loop import FinalEvent
                sdk_context = _sdk_context_port.load(SdkRunId(sdk_run_id))
                assistant_texts = [
                    message.content
                    for message in sdk_context.messages
                    if str(message.role.value) == "assistant"
                ]
                if assistant_texts:
                    await presenter.present(
                        FinalEvent(content=assistant_texts[-1]),
                        context,
                        state,
                    )
            except Exception as _assistant_exc:  # noqa: BLE001
                logger.warning(
                    "sdk_assistant_presentation_failed",
                    error=str(_assistant_exc)[:200],
                )

        # Finalize presentation
        await delivery_adapter.finish()
        terminal_binding_state = "completed"

        logger.info(
            "sdk_run_completed",
            session_id=session_id,
            request_id=request_id,
            sdk_run_id=sdk_run_id,
            root_run_id=root_run_id,
            state=final_state.value if hasattr(final_state, "value") else str(final_state),
        )

    finally:
        _sdk_observability.export()
        _sdk_observability.reset_ingress(observability_token)
        if terminal_binding_state is not None and _sdk_provider_binding_resolver is not None:
            try:
                _sdk_provider_binding_resolver.mark_terminal(
                    sdk_run_id, terminal_binding_state
                )
            except KeyError:
                pass
        if terminal_binding_state is not None and _sdk_tool_authority_registry is not None:
            try:
                _sdk_tool_authority_registry.mark_terminal(
                    sdk_run_id, terminal_binding_state
                )
            except KeyError:
                pass
        # WAITING is durable and resumable: retain both delivery and identity
        # routes until a signal causes the Run to reach a terminal state.
        if terminal_binding_state is not None:
            _sdk_retained_presentations.pop(sdk_run_id, None)
            _delivery_adapters.pop(sdk_run_id, None)
            if _sdk_run_ids_by_root.get(root_run_id) == sdk_run_id:
                _sdk_run_ids_by_root.pop(root_run_id, None)
            _sdk_cancel_requested_run_ids.discard(sdk_run_id)
        logger.debug(
            "sdk_run_adapter_cleanup",
            sdk_run_id=sdk_run_id,
            root_run_id=root_run_id,
            remaining_adapters=len(_delivery_adapters),
        )


async def _watch_retained_sdk_run(
    sdk_run_id: str,
    *,
    authorization_ws: WebSocket | None = None,
) -> None:
    """Project a resumed/recovered Run terminal through its retained route."""

    from agent.agent_loop import ErrorEvent, FinalEvent
    from simple_harness import RunId as SdkRunId
    from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

    retained = _sdk_retained_presentations.get(sdk_run_id)
    if retained is None or _sdk_ingress is None:
        return
    delivery, presenter, state, context = retained
    terminal_state: str | None = None
    projection_committed = False
    try:
        await _sdk_ingress.wait_idle(sdk_run_id)
        final = _sdk_ingress.query(sdk_run_id)
        raw_state = getattr(final, "state", final)
        state_value = str(getattr(raw_state, "value", raw_state)).lower()
        if state_value == "waiting":
            # One resumed Run may require several sequential Tool decisions
            # (search -> describe -> activate -> execute).  The first waiting
            # projection is emitted by _execute_sdk_run; every later waiting
            # boundary is owned by this recovery watcher.
            if authorization_ws is not None:
                await _project_open_sdk_authorizations(
                    authorization_ws,
                    sdk_run_id=sdk_run_id,
                )
            return
        terminal_state = (
            state_value
            if state_value in {"completed", "failed", "cancelled"}
            else "failed"
        )
        if terminal_state == "completed" and _sdk_context_port is not None:
            sdk_context = _sdk_context_port.load(SdkRunId(sdk_run_id))
            assistant_texts = [
                message.content
                for message in sdk_context.messages
                if str(message.role.value) == "assistant"
                and isinstance(message.content, str)
            ]
            if assistant_texts:
                await presenter.present(
                    FinalEvent(content=assistant_texts[-1]), context, state
                )
            else:
                await presenter.present(
                    ErrorEvent(
                        reason="run_failed",
                        detail="sdk_recovered_final_missing",
                        error_class="sdk_recovered_final_missing",
                    ),
                    context,
                    state,
                )
                terminal_state = "failed"
        elif terminal_state != "completed":
            await presenter.present(
                ErrorEvent(
                    reason="run_failed",
                    detail=f"sdk_run_{terminal_state}",
                    error_class="run_failed",
                ),
                context,
                state,
            )
        await delivery.finish()
        projection_committed = True
    finally:
        if terminal_state is not None and projection_committed:
            context_sources = service_context.get("sdk_context_source_repository")
            if context_sources is not None:
                try:
                    await context_sources.consume_run(sdk_run_id)
                except Exception as exc:  # retained cleanup retries on later GC
                    logger.warning(
                        "sdk_context_source_terminal_cleanup_deferred",
                        sdk_run_id=sdk_run_id,
                        error=str(exc)[:200],
                    )
            if _sdk_provider_binding_resolver is not None:
                try:
                    _sdk_provider_binding_resolver.mark_terminal(
                        sdk_run_id, terminal_state
                    )
                except KeyError:
                    pass
            if _sdk_tool_authority_registry is not None:
                try:
                    _sdk_tool_authority_registry.mark_terminal(
                        sdk_run_id, terminal_state
                    )
                except KeyError:
                    pass
            _sdk_retained_presentations.pop(sdk_run_id, None)
            _delivery_adapters.pop(sdk_run_id, None)
            root_run_id = str(getattr(context, "run_id", "") or "")
            if _sdk_run_ids_by_root.get(root_run_id) == sdk_run_id:
                _sdk_run_ids_by_root.pop(root_run_id, None)


def _ensure_sdk_recovery_watcher(
    sdk_run_id: str,
    *,
    authorization_ws: WebSocket | None = None,
) -> asyncio.Task | None:
    """Start at most one terminal watcher for a retained presentation route."""

    run_id = str(sdk_run_id or "").strip()
    if not run_id or run_id not in _sdk_retained_presentations:
        return None
    current = _sdk_recovery_watch_tasks.get(run_id)
    if current is not None and not current.done():
        return current
    task = asyncio.create_task(
        _watch_retained_sdk_run(
            run_id,
            authorization_ws=authorization_ws,
        ),
        name=f"sdk-recovered-presentation:{run_id}",
    )
    _sdk_recovery_watch_tasks[run_id] = task

    def settled(_done: asyncio.Task) -> None:
        if _sdk_recovery_watch_tasks.get(run_id) is _done:
            _sdk_recovery_watch_tasks.pop(run_id, None)

    task.add_done_callback(settled)
    return task


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
    run_id = payload.get("run_id", "")
    task_scope_id = payload.get("task_scope_id", "")

    logger.info(
        "chat_v2_final_send_started sid=%s request_id=%s run_id=%s task_scope_id=%s chars=%d",
        session_id, request_id, run_id, task_scope_id, text_len,
    )

    async def _send_originator() -> None:
        try:
            await asyncio.wait_for(
                originator_ws.send_json(msg),
                timeout=_PEER_BROADCAST_TIMEOUT_S,
            )
            logger.info(
                "chat_v2_final_send_completed sid=%s request_id=%s run_id=%s chars=%d",
                session_id, request_id, run_id, text_len,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "chat_v2_final_send_timeout sid=%s request_id=%s run_id=%s timeout_s=%.1f chars=%d",
                session_id, request_id, run_id, _PEER_BROADCAST_TIMEOUT_S, text_len,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "chat_v2_final_send_failed sid=%s request_id=%s run_id=%s err=%s err_type=%s",
                session_id, request_id, run_id, exc, type(exc).__name__,
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
_realtime_voice_service = None


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


async def _seed_registry_from_runtime_overrides(registry) -> None:
    """registry 为空时，用 llm_runtime.json 的手填配置补一条 provider。

    只在**完全为空**时补：用户一旦在设置页管理了多个 provider，registry 就是
    权威，不能被这份单 endpoint 的遗留配置覆盖。
    """
    if registry is None or registry.list_providers():
        return
    overrides = _load_llm_runtime_overrides()
    base_url = str(overrides.get("base_url") or "").strip()
    model = str(overrides.get("model") or "").strip()
    api_key = str(overrides.get("api_key") or "").strip()
    process_api_key = str(os.environ.get("DESKPET_CLOUD_API_KEY") or "").strip()
    if not base_url or not model or not (api_key or process_api_key):
        # 三者缺一就不补：add_provider 强制要求 api_key，半成品条目只会让
        # 链路以更难懂的方式失败。
        return
    fields = {
        "id": "primary",
        "name": _provider_display_name(base_url, "primary"),
        "base_url": base_url,
        "models": [model],
        "default_model": model,
        "api_key": api_key or process_api_key,
        "enabled": True,
        "priority": 1,
        "source": "user",
    }
    if not api_key:
        await registry.add_ephemeral_provider(fields)
        logger.info(
            "provider_registry_seeded_from_process_env base_url=%s model=%s",
            base_url,
            model,
        )
        return
    await registry.add_provider(
        fields
    )
    logger.info(
        "provider_registry_seeded_from_runtime base_url=%s model=%s", base_url, model
    )


def _provider_display_name(base_url: str, fallback: str) -> str:
    """从 base_url 取主机名做 provider 显示名，取不到就回退到 id。"""
    from urllib.parse import urlsplit

    try:
        return urlsplit(base_url).hostname or fallback
    except Exception:  # noqa: BLE001 - 显示名不值得让配置写入失败
        return fallback


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

    # 2026-08-09：把同一份配置 upsert 进 provider registry。
    #
    # 为什么必须做：onboarding 的「测试连接」走的就是本接口，而它以前只写
    # llm_runtime.json + 热替换 local_llm。真正跑 agent 的 harness 读的是
    # registry（`get_chain()`），两条路互不相通——relay 时代由登录桥把 registry
    # 填好所以没暴露；relay 移除后没人填，于是"用户在 onboarding 填了 provider，
    # 聊天却报 no LLM provider configured"（2026-08-09 真机实测）。
    # 这里补上唯一的写入方，让手填 provider 真正成为产品的单一路径。
    # llm_runtime.json 照旧保留（用户可直接改，明文是有意为之）。
    _upsert_reg = service_context.get("provider_registry")
    if _upsert_reg is not None and body.base_url:
        _pid = "primary"
        _fields = {
            "id": _pid,
            "name": _provider_display_name(body.base_url, _pid),
            "base_url": body.base_url,
            "models": [body.model] if body.model else [],
            "default_model": body.model or "",
            "api_key": resolved_key or "",
            "enabled": True,
            "priority": 1,
            "source": "user",
        }
        try:
            # Onboarding must make the provider it just validated the first
            # runtime choice. Seeded installs can contain an older enabled
            # endpoint without a key; leaving equal priorities in insertion
            # order would make cold startup select that unusable endpoint and
            # keep the SDK ingress closed despite a successful connection test.
            _was_present = _upsert_reg.get_entry(_pid) is not None
            await _upsert_reg.ensure_provider(_fields)
            _ordered_ids = [_pid, *[
                str(item["id"])
                for item in _upsert_reg.get_chain()
                if str(item["id"]) != _pid
            ]]
            await _upsert_reg.reorder(_ordered_ids)
            logger.info(
                "provider_registry_upsert_%s id=%s",
                "updated" if _was_present else "added",
                _pid,
            )
        except Exception as _upsert_exc:  # noqa: BLE001
            # 注册失败不该让"测试连接"整体失败——热替换的 local_llm 已生效，
            # 用户仍能看到连接结果；但要显式记录，否则聊天会以
            # "no LLM provider configured" 的形式二次暴露且难以溯源。
            logger.warning(
                "provider_registry_upsert_failed id=%s error=%s",
                _pid,
                str(_upsert_exc)[:200],
            )

    return {
        "ok": True,
        "cloud_configured": True,  # always true under unified schema
        "base_url": body.base_url,
        "model": body.model,
        "has_api_key": bool(resolved_key) and resolved_key != "ollama",
        "strategy": llm._strategy.value,
    }


async def _current_slash_skill_catalog():
    """Freeze the current user-global Skill command view atomically.

    First-party Skills remain available, while managed user-global Skills are
    derived from the same Hub snapshot and Manager version rows used by fresh
    SDK Runs.  No Session or selected workspace participates in visibility.
    """

    from deskpet.commands import (
        CompositeSkillCommandCatalog,
        FrozenSkillCommandCatalog,
    )

    first_party = (
        service_context.get("managed_skill_discovery_projection")
        or service_context.get("skill_loader")
    )
    capability_platform = service_context.get("capability_platform")
    global_skill_service = service_context.get("project_skill_install_service")
    if capability_platform is None or global_skill_service is None:
        return first_party

    from deskpet.capabilities.contracts import CapabilityScope
    from deskpet.sdk_adapters.capability_catalog import (
        ProductCapabilityCatalogSourceAdapter,
    )

    owner_key = str(global_skill_service.global_owner_key)
    scope = CapabilityScope(user_key=owner_key)
    async with capability_platform.publish_lock:
        snapshot, _entries = await capability_platform.hub.snapshot_for_atomic_lease(
            scope
        )
        records = await (
            ProductCapabilityCatalogSourceAdapter()
            .sdk_global_resource_records_from_snapshot(
                store=capability_platform.store,
                snapshot=snapshot,
                owner_key=owner_key,
                user_scope_key=owner_key,
            )
        )
    global_catalog = FrozenSkillCommandCatalog(records)
    return CompositeSkillCommandCatalog(first_party, global_catalog)


@app.get("/api/skills/list")
async def api_skills_list(session_id: str = ""):
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
    _sl = await _current_slash_skill_catalog()
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
async def api_commands_help(session_id: str = ""):
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
    _sl = await _current_slash_skill_catalog()
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
async def api_command_schema(name: str, session_id: str = ""):
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
    _sl = await _current_slash_skill_catalog()
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
        "realtime_voice": (
            _realtime_voice_service.health_payload()
            if _realtime_voice_service is not None
            else {"enabled": False, "path": "/ws/realtime-voice"}
        ),
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
    # SessionDB is the current production MemoryStore implementation.  The
    # legacy ``memory_store`` service key disappeared during the P4 migration,
    # but this user-facing IPC handler still looked up only that retired key,
    # leaving MemoryPanel stuck in its loading state forever.  Prefer the
    # compatibility key when present and otherwise use the authoritative DB.
    store = service_context.get("memory_store") or service_context.get(
        "session_db"
    )
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


def _skill_install_error_payload(exc: Exception) -> dict[str, Any]:
    code = str(getattr(exc, "code", "") or "skill_install_failed")
    if isinstance(exc, ValueError) and str(exc).startswith("skill_install_"):
        code = str(exc)
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": str(exc),
            "retryable": bool(getattr(exc, "retryable", False)),
        },
        # Compatibility text for older clients. It mirrors the structured
        # message and carries no authority or install state.
        "error_code": code,
        "message": str(exc),
    }


async def _settings_skill_install_adapter(ws: WebSocket):
    from deskpet.capabilities.skill_install_ui import (
        ProjectSkillInstallUIAdapter,
        ProjectSkillInstallUIError,
        TrustedGlobalInstallContext,
    )

    service = service_context.get("project_skill_install_service")
    if service is None:
        raise ProjectSkillInstallUIError(
            "skill_install_service_unavailable",
            "The managed global Skill installer is unavailable.",
            retryable=True,
        )
    authorizer_factory = service_context.get(
        "project_skill_install_settings_authorizer"
    )
    bind_request = getattr(authorizer_factory, "bind_control_request", None)
    if not callable(bind_request):
        raise ProjectSkillInstallUIError(
            "settings_install_authorization_unavailable",
            "Settings cannot establish trusted Host window authority in this build.",
            retryable=True,
        )
    # The authorizer owns request/window authentication. Query parameters and
    # UI payload fields are deliberately not supplied as identity inputs.
    bound_authorizer = await bind_request(ws)
    global_owner_key = str(getattr(service, "global_owner_key", "") or "").strip()
    if not global_owner_key:
        raise ProjectSkillInstallUIError(
            "settings_install_authorization_unavailable",
            "Settings cannot resolve the trusted global Skill owner.",
            retryable=True,
        )
    principal_id = str(getattr(bound_authorizer, "principal_id", "") or "").strip()
    if not principal_id:
        raise ProjectSkillInstallUIError(
            "settings_install_authorization_unavailable",
            "Settings cannot resolve an authenticated Host principal.",
            retryable=True,
        )
    # This is deliberately a separate Host adapter. The legacy PermissionGate
    # returns only an in-memory decision and cannot mint the durable receipt
    # required by GlobalSkillInstallService.confirm/cancel.
    return (
        ProjectSkillInstallUIAdapter(service=service, authorizer=bound_authorizer),
        TrustedGlobalInstallContext(
            principal_id=principal_id,
            global_owner_key=global_owner_key,
        ),
        principal_id,
    )


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
    _cc_sdb = service_context.get("session_db")
    if _cc_sdb is not None:
        try:
            from deskpet.memory.primary_authority import (
                assert_not_primary_authority,
            )

            await assert_not_primary_authority(_cc_sdb._db_path, session_id)
        except Exception as _cc_exc:  # noqa: BLE001
            _cc_code = getattr(_cc_exc, "code", None)
            if _cc_code:
                await ws.send_json(
                    {
                        "type": "session_authority_error",
                        "payload": {"ok": False, "code": _cc_code},
                    }
                )
                await ws.close(code=4003, reason=_cc_code)
                return
            raise
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
    if _sdk_desktop_test_enabled():
        await ws.send_json(
            {
                "type": "companion_identity_status",
                "payload": {
                    "ready": True,
                    "status": "ready",
                    "profile_id": "sdk-desktop-test",
                    "profile_generation": 1,
                    "binding_epoch": "1",
                    "session_id": "default",
                },
            }
        )
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
                # 旧记忆 summarizer 已移除；state_db 恒为 None，上面的 guard 已 continue。

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
                # 旧记忆 feedback store 已移除；state_db 恒为 None，上面的 guard 已 continue。

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

            elif msg_type in {"session_run_status_query", "session_health_check"}:
                payload = raw.get("payload", {}) or {}
                target_sid = str(payload.get("session_id") or session_id)
                root_run_id = str(
                    payload.get("root_run_id") or payload.get("run_id") or ""
                ).strip()
                projection = _authoritative_sdk_run_projection(
                    root_run_id, str(payload.get("sdk_run_id") or "")
                )
                await ws.send_json({
                    "type": "session_run_status_ack",
                    "payload": {
                        "query_id": str(payload.get("query_id") or ""),
                        "session_id": target_sid,
                        "root_run_id": root_run_id,
                        **projection,
                    },
                })

            elif msg_type == "permission_response":
                payload = raw.get("payload", {}) or {}
                target_sid = str(payload.get("session_id") or session_id)
                decision_id = payload.get("decision_id") or payload.get("request_id")
                try:
                    receipt = await _signal_product_harness_decision(
                        target_sid,
                        payload,
                        {"decision": str(payload.get("decision") or "deny")},
                        authorization=True,
                        projection_ws=ws,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "permission_response_rejected",
                        decision_id=decision_id,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    await ws.send_json(
                        {
                            "type": "permission_response_applied",
                            "payload": {
                                "ok": False,
                                "request_id": payload.get("request_id"),
                                "decision_id": decision_id,
                                "error": {
                                    "code": getattr(exc, "code", type(exc).__name__),
                                    "message": str(exc),
                                },
                            },
                        }
                    )
                    continue
                logger.info(
                    "permission_response_applied",
                    decision_id=decision_id,
                    duplicate=receipt.duplicate,
                )
                await ws.send_json(
                    {
                        "type": "permission_response_applied",
                        "payload": {
                            "ok": True,
                            "request_id": payload.get("request_id"),
                            "decision_id": decision_id,
                            "duplicate": receipt.duplicate,
                        },
                    }
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
                    payload = raw.get("payload", {}) or {}
                    target_session_id = str(
                        payload.get("session_id") or ""
                    ).strip()
                    capability_scope = None
                    capability_owner_key = None
                    if target_session_id:
                        project_bindings = service_context.get(
                            "project_binding_service"
                        )
                        if project_bindings is None:
                            raise RuntimeError(
                                "Project binding service is unavailable"
                            )
                        workspace = await project_bindings.resolve_session(
                            target_session_id
                        )
                        if workspace.kind == "project":
                            from deskpet.capabilities.contracts import (
                                CapabilityScope,
                            )

                            capability_owner_key = "sdk-runtime"
                            capability_scope = CapabilityScope.for_run(
                                f"capability-center:{target_session_id}",
                                project_id=workspace.project_id,
                                project_revision=workspace.project_revision,
                                project_identity=workspace.project_identity,
                                user_key=capability_owner_key,
                            )
                        elif workspace.kind == "missing":
                            raise RuntimeError(workspace.error_code)
                    capabilities = await center.list_capabilities(
                        capability_scope,
                        owner_key=capability_owner_key,
                    )
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
                    if msg_type == "capability_install":
                        _si_adapter, _si_project, _si_principal = (
                            await _settings_skill_install_adapter(ws)
                        )
                        _si_result = await _si_adapter.stage(
                            url=str(
                                payload.get("source_url")
                                or payload.get("url")
                                or ""
                            ),
                            project=_si_project,
                            principal_id=_si_principal,
                        )
                        await ws.send_json({
                            "type": "capability_action_response",
                            "payload": {
                                "ok": True,
                                "action": action,
                                "status": "awaiting_confirmation",
                                "install": _si_result,
                            },
                        })
                        continue
                    if msg_type in {
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
                    "request_id": raw.get("request_id"),
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
                    "request_id": raw.get("request_id"),
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
                # Managed user-global catalog is the only installed-Skill
                # authority.  The legacy userdata/skills directory is not a
                # success or visibility source.
                _capability_platform = service_context.get("capability_platform")
                _global_service = service_context.get("project_skill_install_service")
                if _capability_platform is None or _global_service is None:
                    await ws.send_json({
                        "type": "skill_list_installed_response",
                        "payload": {"skills": [], "error": "global Skill catalog unavailable"},
                    })
                    continue
                _installed = []
                from deskpet.capabilities.contracts import CapabilityScope
                from deskpet.capabilities.manifest import load_and_validate_pack

                _global_scope_key = str(_global_service.global_owner_key)
                _catalog_snapshot = await _capability_platform.hub.snapshot(
                    CapabilityScope(user_key=_global_scope_key)
                )
                for _descriptor in _catalog_snapshot.descriptors:
                    _binding = next(
                        (
                            item
                            for item in _descriptor.visible_bindings
                            if item.scope == "user"
                            and item.scope_key == _global_scope_key
                            and item.active
                        ),
                        None,
                    )
                    if _binding is None:
                        continue
                    _record = await _capability_platform.store.get_version(
                        _descriptor.version.capability_id,
                        _descriptor.version.version,
                        _descriptor.version.manifest_hash,
                    )
                    if _record is None:
                        continue
                    _manifest = load_and_validate_pack(_record.install_path).manifest
                    for _skill in _manifest.skills:
                        _installed.append({
                            "name": _skill.id,
                            "description": _manifest.name,
                            "version": _manifest.version,
                            "scope": "global",
                            "catalog_generation": _catalog_snapshot.stamp.catalog_generation,
                            "allowed_tools": list(_skill.allowed_tools),
                        })
                await ws.send_json({
                    "type": "skill_list_installed_response",
                    "payload": {"skills": _installed},
                })

            elif msg_type == "skill_install_from_url":
                # Managed user-global install only. The legacy marketplace
                # installer remains a diagnostic inventory, never the runtime
                # authority for URL installs.
                payload = raw.get("payload", {}) or {}
                try:
                    _si_adapter, _si_owner, _si_principal = (
                        await _settings_skill_install_adapter(ws)
                    )
                    _si_result = await _si_adapter.stage(
                        url=str(payload.get("url") or ""),
                        owner=_si_owner,
                        principal_id=_si_principal,
                    )
                    await ws.send_json({
                        "type": "skill_install_pending",
                        "request_id": raw.get("request_id"),
                        "payload": {"ok": True, **_si_result},
                    })
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json({
                        "type": "skill_install_pending",
                        "request_id": raw.get("request_id"),
                        "payload": _skill_install_error_payload(exc),
                    })

            elif msg_type == "skill_install_confirm":
                payload = raw.get("payload", {}) or {}
                try:
                    _si_adapter, _si_project, _si_principal = (
                        await _settings_skill_install_adapter(ws)
                    )
                    _si_decision = str(payload.get("decision") or "").strip()
                    if _si_decision not in {"approve", "deny"}:
                        raise ValueError("skill_install_decision_invalid")
                    _si_result = await _si_adapter.settle(
                        intent_id=str(
                            payload.get("intent_id")
                            or payload.get("staging_id")
                            or ""
                        ),
                        digest=str(payload.get("digest") or ""),
                        decision_nonce=str(payload.get("decision_nonce") or ""),
                        decision_version=int(payload.get("decision_version") or 0),
                        decision=_si_decision,
                    )
                    await ws.send_json({
                        "type": "skill_install_confirm_response",
                        "request_id": raw.get("request_id"),
                        "payload": {"ok": True, **_si_result},
                    })
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json({
                        "type": "skill_install_confirm_response",
                        "request_id": raw.get("request_id"),
                        "payload": _skill_install_error_payload(exc),
                    })

            elif msg_type == "skill_install_status":
                payload = raw.get("payload", {}) or {}
                try:
                    _si_adapter, _si_project, _si_principal = (
                        await _settings_skill_install_adapter(ws)
                    )
                    _si_result = await _si_adapter.status(
                        intent_id=str(payload.get("intent_id") or ""),
                        project=_si_project,
                    )
                    await ws.send_json({
                        "type": "skill_install_status_response",
                        "request_id": raw.get("request_id"),
                        "payload": {"ok": True, **_si_result},
                    })
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json({
                        "type": "skill_install_status_response",
                        "request_id": raw.get("request_id"),
                        "payload": _skill_install_error_payload(exc),
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
                payload = raw.get("payload", {}) or {}
                try:
                    await _handle_context_breakdown_request(
                        ws,
                        payload,
                        owner_session_id=_resolve_chat_source_session(
                            session_id, None
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("context_breakdown_send_failed err=%s", exc)

            elif msg_type == "human_memory_request":
                from deskpet.memory.human_memory_api import (
                    handle_human_memory_command,
                )
                from deskpet.memory.human_memory_service import (
                    AuthenticatedHostSnapshot,
                )

                _hm_response = await handle_human_memory_command(
                    raw,
                    factory=service_context.get(
                        "human_memory_host_service_factory"
                    ),
                    auth=_local_owner_auth(),
                    binding_append=service_context.get(
                        "human_memory_binding_append_authority"
                    ),
                    recovery=service_context.get(
                        "human_memory_recovery_lifecycle"
                    ),
                    scheduler_wake=service_context.get(
                        "human_memory_foreground_scheduler_wake"
                    ),
                )
                if _hm_response is not None:
                    await ws.send_json(_hm_response)

            elif msg_type in {
                "project_preview_register", "project_register", "session_create",
                "project_catalog_page", "project_sessions_page", "project_inspect",
                "project_relocate",
            }:
                from deskpet.session.project_protocol import (
                    handle_project_session_command as _handle_project_session_command,
                )

                _project_response = await _handle_project_session_command(
                    raw,
                    bindings=service_context.get("project_binding_service"),
                    creation=service_context.get("session_creation_service"),
                )
                if _project_response is not None:
                    await ws.send_json(_project_response)

            elif msg_type == "sessions_list":
                # The bounded Project catalog and per-scope Session pages are
                # the sole read model.  Returning a second flat projection
                # would let clients regroup paths and recreate split authority.
                await ws.send_json({
                    "type": "sessions_list_error",
                    "request_id": raw.get("request_id"),
                    "payload": {
                        "code": "protocol_replaced",
                        "error": "use project_catalog_page and project_sessions_page",
                    },
                })

            elif msg_type == "session_delete":
                # 删除一个会话的全部消息（消息面板下拉里每项的 X）。
                _sd_payload = raw.get("payload", {}) or {}
                _sd_sid = _sd_payload.get("session_id") or ""
                _sd_sdb = service_context.get("session_db")
                _sd_ok = False
                _sd_code = None
                if _sd_sdb is not None and _sd_sid:
                    try:
                        from deskpet.memory.primary_authority import (
                            assert_not_primary_authority,
                        )

                        await assert_not_primary_authority(
                            _sd_sdb._db_path, _sd_sid
                        )
                        _sd_workflows = service_context.get("workflow_service")
                        _sd_creation = service_context.get(
                            "session_creation_service"
                        )
                        if _sd_workflows is not None:
                            async with _sd_workflows.session_lock(_sd_sid):
                                if _sd_creation is not None:
                                    await _sd_creation.mark_deleted(_sd_sid)
                                else:
                                    await _sd_sdb.clear(_sd_sid)
                                await _sd_workflows.cancel_runs_for_session(
                                    _sd_sid,
                                    reason="session_deleted",
                                )
                        else:
                            if _sd_creation is not None:
                                await _sd_creation.mark_deleted(_sd_sid)
                            else:
                                await _sd_sdb.clear(_sd_sid)
                        _sd_attempts = service_context.get("context_attempt_store")
                        if _sd_attempts is not None:
                            _sd_attempts.purge_session(_sd_sid)
                        _sd_ok = True
                        logger.info("session_deleted sid=%s", _sd_sid)
                    except Exception as _sd_exc:  # noqa: BLE001
                        _sd_code = getattr(_sd_exc, "code", None)
                        logger.warning(
                            "session_delete_failed", error=str(_sd_exc),
                            session_id=_sd_sid,
                        )
                await ws.send_json({
                    "type": "session_deleted",
                    "payload": {
                        "session_id": _sd_sid,
                        "ok": _sd_ok,
                        **({"code": _sd_code} if _sd_code else {}),
                    },
                })

            elif msg_type == "session_rename":
                # 消息面板「重命名话题」：写自定义标题（空=还原自动预览）。
                _sr_payload = raw.get("payload", {}) or {}
                _sr_sid = _sr_payload.get("session_id") or ""
                _sr_title = _sr_payload.get("title") or ""
                _sr_sdb = service_context.get("session_db")
                _sr_ok = False
                _sr_stored = ""
                _sr_code = None
                if _sr_sdb is not None and _sr_sid:
                    try:
                        from deskpet.memory.primary_authority import (
                            assert_not_primary_authority,
                        )

                        await assert_not_primary_authority(
                            _sr_sdb._db_path, _sr_sid
                        )
                        _sr_stored = await _sr_sdb.set_session_title(_sr_sid, _sr_title)
                        _sr_ok = True
                        logger.info(
                            "session_renamed sid=%s title_len=%d", _sr_sid, len(_sr_stored)
                        )
                    except Exception as _sr_exc:  # noqa: BLE001
                        _sr_code = getattr(_sr_exc, "code", None)
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
                        **({"code": _sr_code} if _sr_code else {}),
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
                _sml_sdb = service_context.get("session_db")
                if _sml_sdb is not None:
                    try:
                        from deskpet.memory.primary_authority import (
                            assert_not_primary_authority,
                        )

                        await assert_not_primary_authority(
                            _sml_sdb._db_path, str(target_sid)
                        )
                    except Exception as _sml_exc:  # noqa: BLE001
                        _sml_code = getattr(_sml_exc, "code", None)
                        if _sml_code:
                            await ws.send_json(
                                {
                                    "type": "session_messages_error",
                                    "payload": {
                                        "ok": False,
                                        "session_id": target_sid,
                                        "code": _sml_code,
                                    },
                                }
                            )
                            continue
                        raise
                # Loading a transcript is also the product shell's explicit
                # declaration of which conversation this transport currently
                # presents.  Rebind the peer group before the consistency
                # gate: a recovered terminal may be committed while the
                # websocket is reconnecting, and its live notification must
                # route back to this selected history session rather than the
                # transport's startup/default topic.
                _remap_chat_peer_group(session_id, str(target_sid))
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
                            await _current_slash_skill_catalog()
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
                        from deskpet.companion.skills import (
                            PreparedSkillInvocationScopeV1,
                        )

                        _slash_scope = PreparedSkillInvocationScopeV1.from_dict(
                            _slash_result.get("prepared_skill_scope") or {}
                        )
                        _slash_arguments = tuple(
                            str(item)
                            for item in (_slash_result.get("arguments") or ())
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
                            prepared_skill_scope=_slash_scope,
                            skill_arguments=_slash_arguments,
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
                target_run_id = str(
                    payload.get("root_run_id") or payload.get("run_id") or ""
                ).strip()
                before = _authoritative_sdk_run_projection(
                    target_run_id, str(payload.get("sdk_run_id") or "")
                )
                expected_version = payload.get("expected_run_version")
                stale = (
                    expected_version is not None
                    and int(expected_version) != int(before["run_version"])
                )
                terminal_before = before["state"] in {
                    "completed", "failed", "cancelled"
                }
                cancelled = False
                if not stale and not terminal_before:
                    cancelled = await _cancel_product_harness_run(
                        target_sid, target_run_id,
                        reason=str(payload.get("reason") or "user_interrupt"),
                    )
                after = _authoritative_sdk_run_projection(
                    target_run_id, str(before.get("sdk_run_id") or "")
                )
                _interrupt_evt = {
                    "type": "chat_v2_interrupted",
                    "payload": {
                        "cancel_command_id": str(payload.get("cancel_command_id") or ""),
                        "session_id": target_sid,
                        "run_id": target_run_id,
                        "cancelled": cancelled,
                        "stale_expected_version": stale,
                        **after,
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
                if _sdk_ingress is not None and _sdk_ingress.accepting:
                    _run_id = str(payload.get("run_id") or "").strip()
                    _decision_id = str(payload.get("decision_id") or "").strip()
                    _nonce = str(payload.get("nonce") or "").strip()
                    _version = payload.get("version")
                    if not _run_id or not _decision_id or not _nonce or _version is None:
                        raise ValueError("plan confirmation requires the durable decision fence")
                    _receipt = await _signal_product_harness_decision(
                        _csid,
                        payload,
                        {"decision": _decision},
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
                                # A successful live catalog is also the
                                # durable fallback for the next restart or a
                                # later /models outage. Persist only if the
                                # Provider still has the exact identity and
                                # endpoint used for this fetch; the registry
                                # method is idempotent and does not invalidate
                                # Session bindings for a cache-only refresh.
                                try:
                                    await _reg.cache_discovered_models(
                                        _pid0,
                                        _live,
                                        expected_incarnation_id=str(
                                            getattr(_entry0, "incarnation_id", "") or ""
                                        ),
                                        expected_config_revision=int(
                                            getattr(_entry0, "config_revision", 0) or 0
                                        ),
                                        expected_base_url=_base_url,
                                    )
                                except Exception as _cache_exc:  # noqa: BLE001
                                    logger.warning(
                                        "provider_catalog_cache_refresh_failed "
                                        "provider=%s error=%s",
                                        _pid0,
                                        str(_cache_exc)[:200],
                                    )
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
                        _assert_sdk_provider_mutation_allowed((_pid,))
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
                        _assert_sdk_provider_mutation_allowed((_pid,))
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
                        _assert_sdk_provider_mutation_allowed(_ordered)
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
                _reg = service_context.get("provider_registry")
                if _reg is None:
                    await ws.send_json({
                        "type": "settings_providers_probe_models_response",
                        "payload": {
                            "ok": False,
                            "models": [],
                            "detail": "provider_registry not initialized",
                        },
                    })
                    continue
                _base_url = str(_payload.get("base_url") or "").rstrip("/")
                _api_key = str(_payload.get("api_key") or "")
                _provider_id = str(_payload.get("provider_id") or "").strip()
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
                if not _api_key and _provider_id:
                    _entry = _reg.get_entry(_provider_id)
                    if _entry is None:
                        await ws.send_json({
                            "type": "settings_providers_probe_models_response",
                            "payload": {
                                "ok": False,
                                "models": [],
                                "detail": "provider not found",
                            },
                        })
                        continue
                    if str(_entry.base_url).rstrip("/") != _base_url:
                        await ws.send_json({
                            "type": "settings_providers_probe_models_response",
                            "payload": {
                                "ok": False,
                                "models": [],
                                "detail": "base_url differs from saved provider; enter its API key to probe",
                            },
                        })
                        continue
                    _api_key = str(_reg.resolve_api_key(_provider_id) or "")
                    if not _api_key:
                        await ws.send_json({
                            "type": "settings_providers_probe_models_response",
                            "payload": {
                                "ok": False,
                                "models": [],
                                "detail": "saved provider API key is unavailable",
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
                _msg_sid = _resolve_chat_source_session(
                    session_id,
                    _payload.get("session_id"),
                )
                _base_msg_sid = _msg_sid
                from deskpet.session.task_scope import (
                    source_session_for_created_conversation as _creation_source_sid,
                )
                _source_session_id = _creation_source_sid(
                    _payload.get("session_id"),
                    _base_msg_sid,
                )
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
                if _sdk_desktop_test_enabled():
                    if (text or "").strip():
                        _launch_product_harness_chat(
                            ws,
                            text,
                            _msg_sid,
                            None,
                            False,
                            _attachment_blocks,
                            str(
                                _payload.get("request_id")
                                or raw.get("request_id")
                                or ""
                            )
                            or None,
                            str(
                                _payload.get("turn_id")
                                or raw.get("turn_id")
                                or ""
                            )
                            or None,
                        )
                    continue
                if _scope_decision.created:
                    _legacy_creation = service_context.get(
                        "session_creation_service"
                    )
                    _legacy_request_id = str(
                        _payload.get("request_id")
                        or raw.get("request_id")
                        or uuid.uuid4()
                    )
                    try:
                        _legacy_created = (
                            await _legacy_creation.create_conversation_session(
                                request_id=f"legacy-new-session:{_legacy_request_id}",
                                project_id=None,
                                source_session_id=_source_session_id,
                            )
                        )
                        _msg_sid = str(
                            _legacy_created["session"]["session_id"]
                        )
                    except Exception as _legacy_create_exc:  # noqa: BLE001
                        await ws.send_json({
                            "type": "chat_v2_error",
                            "payload": {
                                "error": "新会话创建失败，请重试。",
                                "code": str(getattr(_legacy_create_exc, "code", "session_create_failed")),
                                "retryable": True,
                                # Empty-state failures have no Session owner.
                                # Keep this empty so App's global error banner
                                # does not filter it as an inactive pseudo sid.
                                "session_id": _source_session_id or "",
                                "request_id": _legacy_request_id,
                            },
                        })
                        continue
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
                    # request_id 必须回传：这一轮在 Run 被预约之前就夭折了，
                    # 前端的 pending_root_request_id 只能靠它认领并清除。少了
                    # 它，该会话此后每条消息都会被判成 "等待 root run" 而永远
                    # 不发出去 —— 会话被静默卡死（r4 S18 实测 default 会话）。
                    # 这个 try 覆盖的不只是身份门：_bind_companion_inbox_route
                    # 的属主围栏错误（owner_tombstoned / rebind_forbidden 等）
                    # 也会落到这里。原实现一律报 companion_identity_not_ready，
                    # 把"会话属主坏了"说成"身份没就绪"，排查时会被带偏很远
                    # （r5 真机实测：default 被墓碑化后一直显示身份未就绪）。
                    # 真实原因放进 detail，code 仍保持稳定供前端分支。
                    _identity_detail = str(
                        getattr(_identity_exc, "code", "")
                        or _identity_exc
                        or type(_identity_exc).__name__
                    )
                    await ws.send_json(
                        {
                            "type": "chat_v2_error",
                            "payload": {
                                "error": "companion_identity_not_ready",
                                "code": "companion_identity_not_ready",
                                "detail": _identity_detail,
                                "retryable": True,
                                "session_id": _msg_sid,
                                "request_id": str(
                                    _payload.get("request_id")
                                    or raw.get("request_id")
                                    or ""
                                )
                                or None,
                                "turn_id": str(
                                    _payload.get("turn_id")
                                    or raw.get("turn_id")
                                    or ""
                                )
                                or None,
                            },
                        }
                    )
                    logger.info(
                        "companion_chat_blocked_identity_not_ready",
                        session_id=_msg_sid,
                        error=type(_identity_exc).__name__,
                        # 只记异常类名会把所有属主围栏错误压成 'RuntimeError'，
                        # 日志里看不出到底是身份没就绪还是会话属主坏了。
                        detail=_identity_detail,
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
                    # 同上：Run 预约前夭折的一轮必须带 request_id 回去，
                    # 否则前端 pending_root_request_id 永远挂着。
                    await ws.send_json(
                        {
                            "type": "chat_v2_error",
                            "payload": {
                                "error": _route_error,
                                "code": _route_code,
                                "retryable": bool(_scope_decision.created),
                                "session_id": _msg_sid,
                                "request_id": str(
                                    _payload.get("request_id")
                                    or raw.get("request_id")
                                    or ""
                                )
                                or None,
                                "turn_id": str(
                                    _payload.get("turn_id")
                                    or raw.get("turn_id")
                                    or ""
                                )
                                or None,
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
                        # 2026-08-09：这里原先直接引用局部名 `session_db`——它只在
                        # `session_provider_get/set` 两个分支里被赋值，而 Python 的
                        # 函数作用域让它在整个 control_channel 里都是局部名。以前不炸
                        # 是因为 ChatView 挂载必发 session_provider_get 把它绑上；
                        # 取消保留会话后空态 activeSid="" ⇒ hydration 直接 return ⇒
                        # 该消息永不发出 ⇒ 空态直发新建会话时 UnboundLocalError
                        # （真机实测）。改为每次从 service_context 取，不依赖执行顺序。
                        _inherit_sdb = service_context.get("session_db")
                        if _inherit_sdb is None:
                            raise RuntimeError("session_db_unavailable")
                        _inherited_model_binding = (
                            await _inherit_sdb.get_session_provider_binding(
                                _msg_sid
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
                # 方案 B（2026-08-20）：continuation 退化为 fresh run。SDK 0.1.4
                # 的 ReAct driver 是单轮语义，多轮对话 = 每条用户消息一个 fresh run、
                # 历史由 SDK context 组装。旧的 projection/boundary + signal 契约是
                # 8/17 迁移遗留的半成品（写投影的 create_task_context 已失联、signal
                # 签名未随 SDK 0.1.3 变更），继续走它只会报 identity mismatch。因此
                # 忽略 target_root_run_id/task_scope_id，直接落入下方 fresh run 分支。

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
                    if await _try_resolve_execution_workflow_decision(
                        ws,
                        session_id=session_id,
                        raw=raw,
                        workflow_service=workflow_service,
                    ):
                        continue
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


class _StarletteRealtimeSocket:
    """Minimal adapter from Starlette frames to the SDK socket protocol."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._closed = False

    async def recv(self) -> str | bytes:
        message = await self._websocket.receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            raise EOFError
        if message_type != "websocket.receive":
            raise RuntimeError("local websocket frame invalid")
        text = message.get("text")
        data = message.get("bytes")
        if isinstance(text, str) and data is None:
            return text
        if isinstance(data, bytes) and text is None:
            return data
        raise RuntimeError("local websocket frame invalid")

    async def send(self, message: str | bytes) -> None:
        if isinstance(message, str):
            await self._websocket.send_text(message)
        else:
            await self._websocket.send_bytes(message)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self._closed:
            return
        self._closed = True
        await self._websocket.close(code=code, reason=reason)


@app.websocket("/ws/realtime-voice")
async def realtime_voice_channel(ws: WebSocket):
    await ws.accept()
    service = _realtime_voice_service
    if service is None:
        await ws.close(code=1013, reason="realtime unavailable")
        return
    from simple_harness_service.realtime.transports import LocalAdmissionError

    peer_host = ws.client.host if ws.client is not None else ""
    try:
        await service.serve(
            _StarletteRealtimeSocket(ws),
            path=ws.url.path,
            origin=ws.headers.get("origin"),
            peer_host=peer_host,
        )
    except LocalAdmissionError as exc:
        logger.info("realtime_voice_local_rejected", stable_code=exc.code)
    except (EOFError, WebSocketDisconnect):
        logger.info("realtime_voice_local_closed")
    except Exception:  # noqa: BLE001
        logger.warning("realtime_voice_local_failed", stable_code="internal")


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
            _sdk_ingress.run_client
            if _sdk_ingress is not None and _sdk_ingress.accepting
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
