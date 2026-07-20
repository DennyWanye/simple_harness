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
import json
import os
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
from paths import resolve_model_dir  # P3-S1
from context import ServiceContext
from deskpet.agent.product_domain_sink import LegacyProductDomainSink
from deskpet.agent.run_presenter import (
    PresentationState,
    RunPresentationContext,
    build_legacy_run_presenter,
)
from deskpet.agent.turn_preparer import ProductTurnPreparer, TurnInput
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

# P3-S5 / P4-S20: register the bundled CUDA DLL dir BEFORE any provider
# import that drags in torch (silero_vad / faster_whisper_asr both do).
# torch's `_load_dll_libraries` runs at module import and globs
# `torch/lib/*.dll` with LoadLibrary; if cudart64_12.dll / cublas64_12.dll
# can't be resolved on the DLL search path, `shm.dll` (which links those
# transitively) raises `OSError [WinError 126]` and the frozen exe dies
# before logging a single line. Doing AddDllDirectory here — before the
# `from providers...` block on the next line — fixes that race.
# Was previously below, after vad/asr provider construction. That worked
# in dev (CUDA on system PATH) but blew up in the frozen bundle where
# the only copy of cudart64_12.dll lives in `_internal/ctranslate2/`.
if getattr(sys, "frozen", False):
    try:
        _ct2_dir_early = Path(sys._MEIPASS) / "ctranslate2"  # type: ignore[attr-defined]
        if _ct2_dir_early.is_dir():
            os.add_dll_directory(str(_ct2_dir_early))
    except Exception:  # pragma: no cover — silent; logged after structlog ready
        pass

# --- Register providers ---
from providers.openai_compatible import OpenAICompatibleProvider
from providers.silero_vad import SileroVAD
from providers.faster_whisper_asr import FasterWhisperASR
from providers.edge_tts_provider import EdgeTTSProvider
from providers.cosyvoice_tts import CosyVoice2Provider
from agent.providers.simple_llm import SimpleLLMAgent
from agent.providers.tool_using import ToolUsingAgent
from memory.sensitive_filter import RedactingMemoryStore
from tools.registry import ToolRegistry
from tools.get_time import get_time_tool
from tools.clipboard import read_clipboard_tool
from tools.reminder import list_reminders_tool
from deskpet.tools.public_projection import (
    project_public_tool_arguments,
    project_public_tool_calls,
    project_public_tool_result,
)
from observability.vram import classify_tier, recommend_asr_device
from router.hybrid_router import HybridRouter, LLMUnavailableError, RoutingStrategy
from billing.ledger import BillingLedger

from config import resolve_cloud_api_key as _resolve_cloud_api_key  # P2-1-S3
from deskpet.tools import ppt_tools
from deskpet.tools.ppt_outline_store import (
    PPTOutlineWaiters,
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


# 对话回合硬超时(默认 15 分钟,可在设置里调整,持久化到 llm_runtime.json
# 的 chat_turn_timeout_minutes)。relay/网络持续不可用时,避免 agent 无限重试
# 让桌宠"努力工作中"死转 —— 超时即优雅停止 + 友好告知用户。夹在 1~60 分钟。
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
try:
    from llm.provider_registry import (
        LLMProviderRegistry,
        _migrate_legacy_provider_config,
    )
    from llm.relay_provider_ops import ensure_relay_provider, relay_logout
    try:
        _migrate_legacy_provider_config(_CONFIG_PATH)
    except Exception as _exc:  # noqa: BLE001
        logger.warning("provider_registry_migration_failed: %s", _exc)
    _provider_registry = LLMProviderRegistry(_CONFIG_PATH)
    service_context.register("provider_registry", _provider_registry)
    logger.info(
        "provider_registry_initialized providers=%d",
        len(_provider_registry.list_providers()),
    )
except Exception as _exc:  # noqa: BLE001
    logger.warning("provider_registry_init_failed: %s", _exc)

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
tool_registry.register(list_reminders_tool)
service_context.register("tool_router", tool_registry)

# P4-S20: v2 deskpet.tools.registry singleton — full schema-aware
# registry used by the new tool_use agent loop. Hosts the 7 OS tools
# (read/write/edit/list/shell/web/desktop_create_file) plus the
# auto-discovered file/web/memory tools from earlier slices.
# PermissionGate is wired with the control-WS responder so user
# popups appear before any sensitive op runs.
_tool_capability_scope_store = None
_tool_capability_resolver = None
try:
    from deskpet.tools.registry import registry as deskpet_tool_registry_v2
    from deskpet.tools.os_tools import register_os_tools as _register_os_tools_v2
    from deskpet.permissions.gate import (
        PermissionGate as _PermissionGate,
        PermissionGateConfig as _PermissionGateConfig,
    )
    from deskpet.types.skill_platform import (
        PermissionResponse as _PermissionResponse,
    )
    from deskpet.tools.code_tools.clarify_tool import resolve_clarification_response
    _register_os_tools_v2(deskpet_tool_registry_v2)
    # P4-S22: Code mode tools (glob, grep, web_search) registered now;
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
    # P4-S25: persist auto_mode across restart. Path lives under the
    # user data dir so it follows the user's profile (dev mode uses
    # `<repo>/userdata/`, prod uses `%APPDATA%/deskpet/`). Loading
    # happens immediately so by the time the first chat runs, the
    # gate already knows the user's prior choice.
    try:
        from pathlib import Path as _Path
        _automode_path = _Path(_paths.user_data_dir()) / "permissions_auto_mode.json"
        permission_gate_v2.bind_persistence_path(_automode_path)
        _restored_automode = permission_gate_v2.load_auto_mode()
        if _restored_automode:
            logger.info("permission_auto_mode_restored", enabled=True)
    except Exception as _exc:  # noqa: BLE001
        logger.warning("auto_mode_persist_init_failed: %s", _exc)
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
    # Per-session pending request map: request_id → asyncio.Future.
    # Filled by the gate responder, drained by the WS handler when
    # a permission_response arrives.
    _permission_pending: dict[str, "asyncio.Future"] = {}
    _clarify_pending: dict[str, "asyncio.Future"] = {}

    async def _permission_responder(req):  # PermissionRequest → PermissionResponse
        """Broadcast permission_request via the control WS for the request's session,
        await matching permission_response. Falls back to deny on timeout/disconnect."""
        ws = _control_connections.get(req.session_id) or _control_connections.get("default")
        if ws is None:
            return _PermissionResponse(request_id=req.request_id, decision="deny")
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[_PermissionResponse]" = loop.create_future()
        _permission_pending[req.request_id] = fut
        try:
            await ws.send_json({
                "type": "permission_request",
                "payload": {
                    "request_id": req.request_id,
                    "category": req.category,
                    "summary": req.summary,
                    "params": req.params,
                    "default_action": req.default_action,
                    "dangerous": req.dangerous,
                    "session_id": req.session_id,
                },
            })
            return await fut
        finally:
            _permission_pending.pop(req.request_id, None)

    permission_gate_v2.set_responder(_permission_responder)

    # WI-TG-2: register the gate as a read-only service so the p4_ipc
    # `permissions_pending_list` handler can surface in-flight requests to
    # the ApprovalCenterPanel. Read-only — the panel never mutates the gate.
    try:
        service_context.register("permission_gate", permission_gate_v2)
    except Exception as _pg_reg_exc:  # noqa: BLE001 — non-fatal
        logger.warning("permission_gate_register_failed", error=str(_pg_reg_exc))

    async def _clarify_ask(question: str, options: list[str], session_id: str) -> str:
        """Broadcast clarification_request via the independent control WS."""
        ws = _control_connections.get(session_id) or _control_connections.get("default")
        if ws is None:
            return ""
        request_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        _clarify_pending[request_id] = fut
        try:
            await ws.send_json({
                "type": "clarification_request",
                "payload": {
                    "request_id": request_id,
                    "question": question,
                    "options": list(options or []),
                },
            })
            return await asyncio.wait_for(fut, 120)
        except asyncio.TimeoutError:
            return ""
        finally:
            _clarify_pending.pop(request_id, None)

    logger.info(
        "p4_s20_tool_registry_v2_ready",
        os_tools=len(deskpet_tool_registry_v2.list_tools(source="builtin")),
    )
except Exception as _v2_exc:  # noqa: BLE001 — non-fatal, log + degrade
    logger.warning("p4_s20_v2_init_failed", error=str(_v2_exc))
    deskpet_tool_registry_v2 = None
    permission_gate_v2 = None
    _permission_pending = {}
    _clarify_pending = {}
    _clarify_ask = None
    resolve_clarification_response = None
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

vad = SileroVAD(
    threshold=config.vad.threshold,
    min_speech_ms=config.vad.min_speech_ms,
    min_silence_ms=config.vad.min_silence_ms,
)
service_context.register("vad_engine", vad)

# P3-S5: frozen bundle ships ctranslate2 + minimal CUDA DLLs
# (cublas/cublasLt/cudart/nvrtc, ~450 MB) under _internal/ctranslate2/.
# Register that dir so cublas64_12.dll resolves at first transcribe.
# ctranslate2 itself already calls AddDllDirectory on its own dir at import,
# but that happens only when ctranslate2 is imported — we do it eagerly
# so nothing races with torch's own DLL probe.
if getattr(sys, "frozen", False):
    try:
        _ct2_dir = Path(sys._MEIPASS) / "ctranslate2"  # type: ignore[attr-defined]
        if _ct2_dir.is_dir():
            os.add_dll_directory(str(_ct2_dir))
            logger.info("cuda_dll_dir_registered", path=str(_ct2_dir))
    except Exception as e:  # pragma: no cover — best-effort
        logger.warning("cuda_dll_dir_register_failed", error=str(e))

# S4: device="auto" in config.toml → pick cuda/cpu based on detected VRAM.
# Explicit "cuda" or "cpu" is respected verbatim (user override).
if config.asr.device == "auto":
    _asr_device, _asr_compute = recommend_asr_device()
    logger.info("asr_device_selected", device=_asr_device, compute=_asr_compute, source="auto")
else:
    _asr_device, _asr_compute = config.asr.device, config.asr.compute_type

asr = FasterWhisperASR(
    model=config.asr.model,
    device=_asr_device,
    compute_type=_asr_compute,
    local_dir=str(resolve_model_dir(config.asr.model_dir)),  # P3-S1
    hotwords=config.asr.hotwords,  # P2-2-F1: short-phrase logit bias
)
service_context.register("asr_engine", asr)

# S9 (R11): TTS provider selection. "cosyvoice2" tries local first, with
# built-in edge-tts fallback on any failure (see CosyVoice2Provider.load).
# "edge-tts" (or anything else) goes straight to the cloud voice.
if config.tts.provider == "cosyvoice2":
    # P3-S1: model_dir is a bare subfolder name under paths.model_root();
    # resolve_model_dir handles dev-mode + PyInstaller + env override.
    tts = CosyVoice2Provider(
        model_dir=str(resolve_model_dir(config.tts.model_dir)),
        fallback_voice=config.tts.voice,
    )
else:
    tts = EdgeTTSProvider(voice=config.tts.voice)
service_context.register("tts_engine", tts)
# P4-S21 #13: hand the TTS to the permission gate so voice-context
# requests get an audible "please click Allow" cue alongside the popup.
# permission_gate_v2 was constructed earlier (line ~301) and may be
# None when v2 stack init failed.
try:
    if permission_gate_v2 is not None:
        permission_gate_v2.set_tts_engine(tts)
except NameError:
    # permission_gate_v2 didn't get initialized (e.g. import error in
    # the v2 block). Voice prompts skip TTS narration; popup still works.
    pass

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
# code 模式非平凡任务出 plan 后，_run_chat（后台任务）在此 Future 上 await，
# 等前端点 [执行]/[取消] → plan_confirm WS handler set_result → 继续/取消。
# 后台任务 await 不阻塞 WS recv loop，所以无需抽取 ReAct 块（最小改动）。
# 单用户桌宠：按 session_id key 足够（code session id 唯一；门只在 code 模式触发）。
# value = {"fut": Future[str], "text": 原始任务文本}（text 供 Layer 1B 计划记忆记录）。
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


_PLAN_CONFIRM_WAITERS: dict[str, dict] = {}

# ─── WI-4.3 技能自创闭环：skill_candidate_confirm Future-await ────────
# 复制 _PLAN_CONFIRM_WAITERS 模式，独立 channel（不复用 plan-confirm）。
# key = candidate_id (int)；value = Future[str] ("accept" | "reject")。
# propose() 后把 Future 注册到此字典，WS handler skill_candidate_confirm
# 调 _SKILL_CANDIDATE_WAITERS.resolve() → set_result → 唤醒挂起协程。
try:
    from deskpet.skills.skill_codifier import SkillCandidateWaiters as _SCWaiters
    _SKILL_CANDIDATE_WAITERS = _SCWaiters()
except Exception:
    _SKILL_CANDIDATE_WAITERS = None  # type: ignore[assignment]


# ─── WI-4.3 技能自创闭环 hook（方案 B 抽 helper）─────────────────────
# 2026-06-06：原 codify hook 只 inline 在 chat handler 的 FinalEvent 分支 →
# turn 经 ErrorEvent（relay ReadError）/ 迭代上限 / 被 stop 中止结束时整段不跑
# → 多工具 turn 跑了 ≥5 工具但卡不弹（真机诊断）。抽成模块级 helper，在
# FinalEvent + ErrorEvent 两处都调，让 turn 任意路径结束都能触发技能自创。
# safe-fail：任何异常只 debug log，不中断正常 turn。flag OFF → 整段跳过（BC）。
async def _maybe_codify_skill(service_context, config, sid, ws, waiters):
    try:
        _codify_cfg = getattr(getattr(config, "skills", None), "codify", None)
        if _codify_cfg is None or not getattr(_codify_cfg, "enabled", False):
            return
        from deskpet.skills.skill_codifier import SkillCodifier as _SCodifier
        _tp_rec = service_context.get("tool_path_recorder")
        _sc_loader = service_context.get("skill_loader")
        _sc_candidate_store = service_context.get("skill_candidate_store")
        if _tp_rec is None or _sc_candidate_store is None:
            return
        _sg_store = service_context.get("session_goal_store")
        _sc_goal_id = None
        if _sg_store is not None:
            _ag = getattr(_sg_store, "get_active_goal", None)
            if callable(_ag):
                _ag_obj = _ag(sid)
                if _ag_obj is not None:
                    _sc_goal_id = getattr(_ag_obj, "goal_id", None)
        if _sc_goal_id is None:
            _sc_goal_id = sid
        _sc_goal_text = ""
        if _sg_store is not None:
            _gt_fn = getattr(_sg_store, "get_goal_text", None)
            if callable(_gt_fn):
                _sc_goal_text = _gt_fn(sid) or ""
        # complete() 把 agent_loop 录得的 _active 工具步快照成 ToolPath（pop）。
        # 无录步 → steps=[] → 不提候选（trivial turn）。idempotent：第二次调
        # （FinalEvent 后 ErrorEvent 不会重复触发，因 _active 已被 pop 空）。
        _tp = _tp_rec.complete(sid, goal_id=_sc_goal_id, goal_text=_sc_goal_text)
        if _tp is None or not _tp.steps:
            return
        _sc_user_dir = None
        if _sc_loader is not None:
            _dirs = getattr(_sc_loader, "_dirs", [])
            if len(_dirs) >= 2:
                _sc_user_dir = _dirs[1]
        if _sc_user_dir is None:
            return
        _llm_for_codify = service_context.get("llm_registry")
        if _llm_for_codify is None:
            return

        async def _make_codify_llm_call(prompt: str) -> str:
            from agent.context_messages import provider_purpose_scope
            from llm.types import ChatResponse as _CR
            with provider_purpose_scope("codifier", session_id=sid):
                _cr: _CR = await _llm_for_codify.chat_with_fallback(
                    [{"role": "user", "content": prompt}], model=None,
                )
            return _cr.content or ""

        _codifier = _SCodifier(
            candidate_store=_sc_candidate_store,
            user_skill_dir=_sc_user_dir,
            llm_call=_make_codify_llm_call,
            skill_loader=_sc_loader,
        )
        _sc_cid = await _codifier.propose(_tp)
        if _sc_cid is None:
            return
        _sc_pending = await _sc_candidate_store.fetch_pending(_sc_cid)
        if _sc_pending is None:
            return
        await ws.send_json({
            "type": "skill_candidate_proposed",
            "payload": {
                "candidate_id": _sc_cid,
                "name": _sc_pending.get("name"),
                "description": _sc_pending.get("description"),
                "steps": _sc_pending.get("steps", []),
                "session_id": sid,
            },
        })
        logger.info(
            "skill_candidate_proposed cid=%d name=%s sid=%s",
            _sc_cid, _sc_pending.get("name"), sid,
        )
        if waiters is not None:
            import asyncio as _aio_sc

            # Bug#2 修复 (2026-06-11)：confirm 等待拆独立 task。原 300s
            # Future-await 内联在 chat task 里 → chat_v2_final 已发但 task
            # 还活着;用户下一条消息触发同 sid 抢占 cancel → 候选 Future
            # 一起死(卡点击无响应、candidate 永久 pending),且确认期间
            # chat 被实际占住。独立 task 后:chat task 立即收尾,候选确认
            # 与后续对话真并行,新消息的 preempt 也伤不到确认链路。
            _sc_fut = _aio_sc.get_event_loop().create_future()
            waiters.add(_sc_cid, _sc_fut)

            async def _await_candidate_decision(
                _cid=_sc_cid, _fut=_sc_fut, _cod=_codifier, _w=waiters
            ):
                try:
                    try:
                        _decision = await _aio_sc.wait_for(_fut, timeout=300)
                    except _aio_sc.TimeoutError:
                        _decision = "reject"
                        logger.info("skill_candidate_timeout cid=%d", _cid)
                    finally:
                        _w.pop(_cid)
                    await _cod.confirm(_cid, accept=(_decision == "accept"))
                    logger.info(
                        "skill_candidate_resolved cid=%d decision=%s", _cid, _decision,
                    )
                except Exception as _aw_ex:  # noqa: BLE001 — safe-fail
                    logger.debug(
                        "skill_candidate_wait_failed cid=%d err=%s", _cid, _aw_ex
                    )

            _aio_sc.create_task(_await_candidate_decision())
    except Exception as _sc_ex:  # noqa: BLE001 — safe-fail
        logger.debug("skill_codify_hook_failed sid=%s err=%s", sid, _sc_ex)


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
    code_todo_getter=None,
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
    # FP-5 缺口 2 (2026-06-06) — WI-1.6 工具路径录制喂 FP-5 技能自创。
    # None (默认) → agent_loop 不喂 recorder（字节级 BC：codify hook complete() 得空 steps → 不提候选）。
    tool_path_recorder=None,
    # WI-OH-4 — 记忆 self-curation nudge。None (默认) → agent_loop 不调 nudge
    # （字节级 BC）。非 None 时每 N 回合 fire-and-forget 触发一次。
    memory_curator=None,
    # ─── 七步问题处理流水线 IN-LOOP 闸透传（plans/2026-06-24-...）。全默认 None/False = BC。
    # self_check_gate / convergence_controller 不由 caller 传——前者 build_agent 内构造（B3，
    # 依赖本函数的 verify_gate/external_evaluator），后者 AgentLoop 内自建（依赖 self._gate）。
    evidence_gate=None,
    pipeline_problem_type=None,
    pipeline_needs_investigation=False,
    pipeline_observability=False,
    convergence_report_on_stop=False,
    trace_store=None,
    context_attempt_store=None,
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
        max_iterations: AgentLoop 迭代上限（companion 16 / code 50）
        completion_probe: 完成探针 (P5-S2 Hook A)
        code_todo_getter: code todo 快照读取器 (WI-4 Focus Chain)
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
        if verifier_cfg is not None and verifier_cfg.verify_gate_mode != "off":
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
            # WI-2.2: construct ephemeral verifier from cloud/local LLM.
            # 救援子代理走 [tools.verifier].ephemeral_subagent_model 配的专用
            # 模型（D6 第 3 次失败救援）——之前 bug 是直接复用主 LLM，配置永不
            # 生效。现按配置解析出专用 provider；缺省/解析失败回退主 LLM。
            # provider=None → None → VerifyGate falls back to conservative fail (BC)
            _ephemeral_base = local_llm or cloud_llm
            _ephemeral_provider = _resolve_ephemeral_provider(
                _ephemeral_base,
                getattr(verifier_cfg, "ephemeral_subagent_model", ""),
            )
            if _ephemeral_provider is not None:
                # Preserve compatibility with injected/legacy two-argument
                # factories while the production adapter accepts purpose.
                import inspect as _inspect_ephemeral_factory

                _factory_params = _inspect_ephemeral_factory.signature(
                    _make_str_llm_call
                ).parameters
                _ephemeral_llm = _make_str_llm_call(
                    _ephemeral_provider,
                    max_tokens=256,
                    **(
                        {"purpose": "supervisor"}
                        if "purpose" in _factory_params
                        else {}
                    ),
                )
            else:
                _ephemeral_llm = None
            if _ephemeral_provider is not None:
                logger.info(
                    "ephemeral_verifier_model",
                    model=getattr(_ephemeral_provider, "model", "?"),
                    base=getattr(_ephemeral_base, "model", "?"),
                )
            _ephemeral_subagent = (
                make_ephemeral_verifier(_ephemeral_llm)
                if _ephemeral_llm is not None
                else None
            )
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
            _evaluator_provider_key = (
                getattr(verifier_cfg, "evaluator_provider", "default")
                if verifier_cfg else "default"
            )
            # Resolve provider: "default" → reuse local_llm (first positional provider
            # available in the registry). Same pattern as WI-2.3 judgment LLM.
            # provider=None → ExternalEvaluator safe-fails (logs evaluator_skipped).
            _ev_provider = None
            try:
                # local_llm is available via the registry's first provider
                _ev_provider = getattr(llm_registry, "providers", [None])[0] if llm_registry else None
            except Exception:  # noqa: BLE001
                pass
            _ev_llm_call = _make_str_llm_call(
                _ev_provider, max_tokens=512, purpose="supervisor"
            )
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
            _eb = local_llm or cloud_llm
            _ep = _resolve_ephemeral_provider(_eb, _scm) if _scm else _eb
            _ell = (
                _make_str_llm_call(_ep, max_tokens=256, purpose="supervisor")
                if _ep is not None
                else None
            )
            _sub = make_ephemeral_verifier(_ell) if _ell is not None else None
            return VerifyGate(extractor=RegexExtractor(_patterns), mode="strict", ephemeral_subagent=_sub)
        except Exception as _e:  # noqa: BLE001
            logger.warning("pipeline_build_verify_gate_failed err=%s", str(_e)[:200])
            return None

    def _build_pipeline_external_evaluator(_scm: str):
        """异体评分子代理：走 _resolve_ephemeral_provider 出独立 model（非执行者打分）。失败 → None。"""
        try:
            from deskpet.agent.external_evaluator import ExternalEvaluator as _EE  # noqa: PLC0415
            _eb = local_llm or cloud_llm
            _evp = _resolve_ephemeral_provider(_eb, _scm) if _scm else _eb
            if _evp is None:
                return None
            logger.info("pipeline_external_evaluator_model model=%s base=%s",
                        getattr(_evp, "model", "?"), getattr(_eb, "model", "?"))
            _evc = _make_str_llm_call(
                _evp, max_tokens=512, purpose="supervisor"
            )
            return _EE(llm_call=_evc, conservative_on_error=True)
        except Exception as _e:  # noqa: BLE001
            logger.warning("pipeline_build_external_evaluator_failed err=%s", str(_e)[:200])
            return None

    _self_check_gate = None
    _pp = getattr(getattr(cfg, "features", None), "problem_pipeline", None)
    if _pp is not None and getattr(_pp, "enabled", False) and getattr(_pp, "self_check", False):
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
        code_todo_getter=code_todo_getter,
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
        # ─── FP-5 缺口 2：WI-1.6 工具路径录制喂技能自创（flag off = None = BC）───
        tool_path_recorder=tool_path_recorder,
        # ─── WI-OH-4：记忆 self-curation nudge（curator None = BC，不调 nudge）───
        memory_curator=memory_curator,
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
        subagent_registry=globals().get("service_context").get("subagent_registry")
        if globals().get("service_context") is not None
        else None,
        # ─── 七步问题处理流水线 IN-LOOP 闸（plans/2026-06-24-...）。flag off → 全 None/False = BC。
        evidence_gate=evidence_gate,
        self_check_gate=_self_check_gate,
        convergence_report_on_stop=convergence_report_on_stop,
        pipeline_problem_type=pipeline_problem_type,
        pipeline_needs_investigation=pipeline_needs_investigation,
        pipeline_observability=pipeline_observability,
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
    _context_page_in_store = None
    if _context_os_enabled:
        from deskpet.tools.capabilities import current_tool_execution_context
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
            loader = service_context.get("skill_loader")
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
        _facts_llm = _make_str_llm_call(local_llm, purpose="memory_summarizer")
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
                _qr_llm = _make_str_llm_call(
                    local_llm, max_tokens=128, purpose="classifier"
                )
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
                    _ent_llm = _make_str_llm_call(
                        local_llm, max_tokens=128, purpose="classifier"
                    )
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

    # P4-S17: RedactingMemoryStore remains the only write path exposed to
    # the agent/admin API, but the inner store is now the canonical state.db.
    memory_store = RedactingMemoryStore(_session_db)
    service_context.register("memory_store", memory_store)

    # SkillLoader: explicitly point dir[0] at the package-data builtin dir so
    # the three shipped skills (recall-yesterday / summarize-day / weather-
    # report) are found without needing a user-dir copy step. dir[1] is the
    # user's override dir under %AppData%/deskpet/skills/user.
    # enable_watch=False in rc1 to avoid the watchdog thread on cold boot;
    # UI's refresh button triggers a manual reload via list_skills() anyway.
    import deskpet.skills.builtin as _builtin_pkg
    _builtin_dir = Path(_builtin_pkg.__file__).parent
    _user_skills_dir = _paths.user_data_dir() / "skills" / "user"
    _user_skills_dir.mkdir(parents=True, exist_ok=True)
    _skill_loader = _SkillLoader(
        skill_dirs=[_builtin_dir, _user_skills_dir],
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
    # WI-T3.2 v3：skill_invoke 工具接电 SkillLoader（取代 stubs.py 同名 stub）.
    try:
        from deskpet.tools import skill_tools as _skill_tools
        _skill_tools.bind(skill_loader=_skill_loader)
        logger.info("p4_skill_invoke_tool_bound")
    except Exception as _sk_exc:  # noqa: BLE001
        logger.warning("p4_skill_invoke_bind_failed", error=str(_sk_exc))

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
                llm_call=_make_str_llm_call(
                    local_llm or cloud_llm, purpose="supervisor"
                )
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
            _pp_base = local_llm or cloud_llm
            # 决策3：analysis_model 留空 → 复用主 LLM（gpt-5.5）；非空 → 克隆独立 model（失败回退主 LLM）。
            _analysis_base = _pp_base
            if getattr(_pp_cfg, "analysis_model", ""):
                _analysis_base = _resolve_ephemeral_provider(_pp_base, _pp_cfg.analysis_model) or _pp_base
            # 决策4：Step1+3 合并 → 单一绑定合并 schema 的 callable（_make_str_llm_call 透传 response_format）。
            _pre_llm = _make_str_llm_call(
                _analysis_base,
                max_tokens=1536,
                response_format=_PRE_ANALYSIS_SCHEMA,
                purpose="classifier",
            ) if _analysis_base is not None else None
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
            # context_window default 32000 for relay models; threshold 0.75.
            # Use the already-constructed local_llm (haiku-scale) for the
            # summary call so we don't spin up a new connection.
            # FP-2 真机修复: compressor 调 chat_with_fallback,而裸 provider
            # (OpenAICompatibleProvider) 没有该方法 → AttributeError 被
            # safe-fail 吞掉,压缩永远失败(should_compress 过了也白过)。
            # 复用 codify 同款 shim 包装(见下方 _CodifyShim 注释,同一个坑)。
            from agent.tool_use_shim import (
                OpenAICompatibleAgentLLM as _CmpShim,
            )
            _compactor_llm = local_llm or cloud_llm
            # 2026-06-12: 压缩窗口不再 hardcode 32000 —— 按当前主模型经
            # model_info 三层解析(BUILTIN ← 用户档位 override)。用户在
            # 「模型与参数」面板选 1M 档后,重启即对压缩阈值生效。
            _cmp_window, _cmp_threshold, _cmp_eff_pct = 32000, 0.75, 0.95
            try:
                from llm import model_info as _mi_cmp
                # P-B 修复: 用有效出站模型(运行时覆盖后,如 gpt-5.5)解析压缩窗口,而非
                # config.raw 旧种子(gemma 32K)→ 否则按错模型阈值过早压缩浪费大窗口。
                _cmp_model = effective_llm_model(config).strip()
                if _cmp_model:
                    _cmp_info = _mi_cmp.resolve(_cmp_model)
                    _cmp_window = int(_cmp_info.context_window)
                    _cmp_threshold = float(_cmp_info.compact_at_pct)
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
                llm_registry=(
                    _CmpShim(provider=_compactor_llm)
                    if _compactor_llm is not None else None
                ),
                context_window=_cmp_window,
                threshold_percent=_cmp_threshold,
                effective_pct=_cmp_eff_pct,
                microcompact_size_aware=_micro_size_aware,
            )
            logger.info(
                "wi4_0_compaction_enabled context_window=%d threshold=%.2f eff_pct=%.2f"
                % (_cmp_window, _cmp_threshold, _cmp_eff_pct)
            )
        except Exception as _cmp_init_exc:  # noqa: BLE001
            logger.warning(
                "wi4_0_compaction_init_failed err=%s — disabled",
                _cmp_init_exc,
            )
            _context_compressor = None
    service_context.register("context_compressor", _context_compressor)

    # ─── goal-completion FP-5 接线修复 (2026-06-06) ──────────────────────────
    # 独立验收发现 codify hook (main.py:5836/5838/5859) + remount/auto-disclosure
    # 接线引用的 3 个 service (tool_path_recorder / skill_candidate_store /
    # llm_registry) + skill_matcher 在 lifespan 从未构造/注册 → get() 抛
    # ValueError("Unknown service") 被 try/except 吞 → WI-4.1/4.2/4.3 真机 no-op。
    # 修法：flag ON 时构造真实例 register；flag OFF 时一律 register(None) 占位
    # （保字节级 BC + 让 get() 不抛）。所有新构造都 flag-gated。
    #
    # 缺口 2 — ToolPathRecorder (WI-1.6)：录工具路径喂 4.3 自创。codify flag ON
    # 才构造。喂数据接线已补全（2026-06-06）：build_agent → _AgentLoop(tool_path_recorder=)
    # → agent_loop 每个 tool_result 调 record_tool(name, ok)；codify hook 在 run 结束
    # 调 complete() 快照 → get/complete 返回非空 ToolPath，真机自创端到端可触发。
    _tool_path_recorder = None
    _skill_candidate_store = None
    _llm_registry_for_codify = None
    _codify_flag = bool(
        getattr(getattr(getattr(config, "skills", None), "codify", None), "enabled", False)
    )
    if _codify_flag:
        try:
            from deskpet.agent.tool_path import ToolPathRecorder as _TPRecorder
            from deskpet.skills.skill_codifier import (
                SkillCandidateStore as _SCStore,
            )
            from agent.tool_use_shim import OpenAICompatibleAgentLLM as _CodifyShim
            _tool_path_recorder = _TPRecorder()
            # 缺口 3 — SkillCandidateStore：pending 候选独立表 (pending_skill_
            # candidates)，与 SkillMemoryStore 同 state.db，lazy _ensure_table
            # （首写才建表 → flag-OFF 字节基线不受影响）。
            _skill_candidate_store = _SCStore(_state_db_path)
            # 缺口 4 — llm_registry：codify hook 需 chat_with_fallback(...) ->
            # ChatResponse。复用 chat handler 同款 OpenAICompatibleAgentLLM shim
            # （provider_registry 是 [[llm.providers]] 配置管理器，无 chat_with_
            # fallback → 不可复用）。绑定已构造的 local_llm/cloud_llm provider。
            _codify_provider = local_llm or cloud_llm
            if _codify_provider is not None:
                _llm_registry_for_codify = _CodifyShim(provider=_codify_provider)
            logger.info(
                "fp5_codify_wiring_ready tool_path=%s candidate_store=%s llm=%s",
                _tool_path_recorder is not None,
                _skill_candidate_store is not None,
                _llm_registry_for_codify is not None,
            )
        except Exception as _codify_wire_exc:  # noqa: BLE001
            logger.warning(
                "fp5_codify_wiring_failed err=%s — disabled", _codify_wire_exc
            )
            _tool_path_recorder = None
            _skill_candidate_store = None
            _llm_registry_for_codify = None
    service_context.register("tool_path_recorder", _tool_path_recorder)
    service_context.register("skill_candidate_store", _skill_candidate_store)
    service_context.register("llm_registry", _llm_registry_for_codify)

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
        skill_loader=_skill_loader,
        # FP-5 缺口 5j (2026-06-06 子代理复评)：把 auto_disclosure 配置交给
        # assembler 当 assemble() 的 default_config → 任何 venue（文字 _run_chat /
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
                llm_call=_make_str_llm_call(
                    local_llm, purpose="memory_summarizer"
                ),
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

    # P4-S22: Code Mode session manager (per-base-session enable map).
    from deskpet.code_mode import CodeModeManager as _CodeModeManager
    _code_mode_manager = _CodeModeManager()
    # P4-S25 B4: bind SessionDB so projects persist across restart.
    # Actual `load_persisted` runs inside the async lifespan (see below)
    # because we can't await at module level.
    _code_mode_manager.bind_persistence(_session_db)
    service_context.register("code_mode", _code_mode_manager)

    # P5-S1: supervisor watchdog infrastructure. SessionActivity tracks
    # AgentEvent timestamps + tool-call signature windows so the watchdog
    # can detect "stuck" Code-mode sessions. Watchdog itself is started
    # later in lifespan() with a 30s grace period; here we just register
    # the data structures so the WS forwarder can bump them.
    from agent.session_activity import SessionActivityStore as _SessionActivityStore
    _session_activity_store = _SessionActivityStore()
    service_context.register("session_activity", _session_activity_store)

    # P5-S4: NudgeQueue is created here (not in lifespan) so the IPC
    # handlers — code_mode_exit, message build path — can reach it
    # without nullable plumbing. Capacity from [supervisor].max_hints_per_session.
    from agent.nudge_queue import NudgeQueue as _NudgeQueue
    _nudge_queue = _NudgeQueue(
        cap=int((config.raw.get("supervisor") or {}).get("max_hints_per_session", 3))
    )
    service_context.register("nudge_queue", _nudge_queue)

    # P4-S22: register the two Code-mode tools that need closures over
    # runtime objects (SessionDB for todo_write, LLM shim + tool
    # registry for agent). The other three (glob/grep/web_search) were
    # registered earlier with static handlers.
    if deskpet_tool_registry_v2 is not None:
        try:
            from deskpet.tools.code_tools import (
                build_todo_write_tool as _build_todo_write_tool,
                build_agent_tool as _build_agent_tool,
                build_ask_clarification_tool as _build_ask_clarification_tool,
            )
            from agent.tool_use_shim import (  # type: ignore[import-not-found]
                OpenAICompatibleAgentLLM as _ShimForAgent,
            )

            # todo_write needs (session_db, code_session_id_resolver, broadcaster)
            # P4-S22 fix: wire a real broadcaster that pushes
            # `code_todo_update` to whichever control WebSocket owns
            # the active code-mode session. Without this, todos write to
            # DB but the frontend's TodoListPanel doesn't update until
            # the user manually closes / reopens the panel (or sends a
            # new chat turn that re-pulls). Now they update live.
            def _resolve_code_sid() -> str | None:
                # The chat handler stores the active session id on the
                # tool registry's session_context dict — pull it from
                # there. If multiple sessions are concurrent this picks
                # the most-recent one written; for now there's only ever
                # one chat session active per backend process.
                cm = service_context.get("code_mode")
                if cm is None:
                    return None
                # Last writer wins — code_mode's all_sessions() returns
                # all base sessions; pick any enabled one.
                for base_sid, st in cm.all_sessions().items():
                    if st.enabled and st.code_session_id:
                        return st.code_session_id
                return None

            def _resolve_base_sid_for_code_session(code_sid: str) -> str | None:
                """Reverse map: code session id → base session id, so we
                know which control_ws to broadcast to. Useful when the
                tool runs deep inside AgentLoop and only knows the
                code_session_id."""
                cm = service_context.get("code_mode")
                if cm is None:
                    return None
                for base_sid, st in cm.all_sessions().items():
                    if st.code_session_id == code_sid:
                        return base_sid
                return None

            async def _todo_broadcaster(msg: dict) -> None:
                """Broadcast a `code_todo_update` to ALL connected
                control WebSockets so both the pet and the code panel
                update in lockstep. Multi-window setups (P4-S23) need
                this — a single-target send to the chat-trigger ws
                misses the sibling window even though both render
                the same code session.
                Best-effort: WS errors are swallowed so a stale tab
                doesn't break tool execution.
                """
                # Snapshot to avoid mutation during iteration when a
                # ws closes mid-broadcast.
                targets = list(_control_connections.values())
                for ws in targets:
                    try:
                        await ws.send_json(msg)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("code_todo_broadcast_failed", error=str(exc))

            _todo_handler, _todo_schema = _build_todo_write_tool(
                session_db=_session_db,
                code_session_id_resolver=_resolve_code_sid,
                broadcaster=_todo_broadcaster,
            )

            _shim_for_agent = _ShimForAgent(provider=local_llm)

            # 子代理并发驱动 WI-4.2：per-kind 模型路由。KindProfile.model 非空时
            # 给该子代理换一个指向不同 model 的 shim（新建 provider——shim/provider
            # 的 model 不可变，R2-3/R2-4）。缓存 per-model shim 防重复建连。默认所有
            # KindProfile.model=None → 永不调用 → 用父 shim（BC）.
            _shim_by_model: dict[str, object] = {}

            def _make_shim_for_model(model: str):
                if not model:
                    return _shim_for_agent
                _cached = _shim_by_model.get(model)
                if _cached is not None:
                    return _cached
                try:
                    from providers.openai_compatible import (
                        OpenAICompatibleProvider as _OAP,
                    )
                    _prov = _OAP(
                        base_url=local_llm.base_url,
                        api_key=local_llm.api_key,
                        model=model,
                        temperature=local_llm.temperature,
                        sanitize_inline_cot_dsml=local_llm.sanitize_inline_cot_dsml,
                    )
                    _shim = _ShimForAgent(provider=_prov)
                    _shim_by_model[model] = _shim
                    return _shim
                except Exception as _sm_exc:  # noqa: BLE001 — 失败回退父 shim
                    logger.warning("subagent shim_for_model(%s) failed: %s", model, _sm_exc)
                    return _shim_for_agent

            def _resolve_parent_sid() -> str:
                cm = service_context.get("code_mode")
                if cm is not None:
                    for base_sid, st in cm.all_sessions().items():
                        if st.enabled:
                            return base_sid
                return "default"

            async def _prepare_subagent_context_os(
                *, messages, session_id, user_message, tool_names, venue
            ):
                import uuid as _uuid_subctx
                from agent.context_manager import ContextManager as _SubCtxMgr
                from deskpet.agent.assembler.bundle import ContextFragment
                from deskpet.tools.capabilities import (
                    ToolEligibilityContext as _SubEligibility,
                    ToolExposureIntent as _SubIntent,
                )

                _planner = service_context.get("context_request_planner")
                _scope_store = service_context.get("tool_capability_scope_store")
                _assembler_for_sub = service_context.get("context_assembler")
                if _planner is None or _scope_store is None or _assembler_for_sub is None:
                    raise RuntimeError("context_os_subagent_runtime_unavailable")
                _task_type = "web_search" if str(venue) in {"web", "research"} else "code"
                _bundle_for_sub = await _assembler_for_sub.assemble(
                    user_message=str(user_message),
                    memory_manager=service_context.get("memory_manager"),
                    tool_registry=deskpet_tool_registry_v2,
                    skill_registry=service_context.get("skill_loader"),
                    mcp_manager=service_context.get("mcp_manager"),
                    session_id=str(session_id),
                    task_type_override=_task_type,
                )
                _bundle_for_sub.tool_exposure_intent = _SubIntent(
                    direct_selectors=tuple(str(name) for name in tool_names)
                )
                _bundle_for_sub.fragments.append(ContextFragment(
                    fragment_id=f"venue.{venue}.subagent",
                    source="code_subagent_entrypoint",
                    role="system",
                    content=f"Isolated {venue} subagent capability scope.",
                    lifetime="task",
                    placement="prefix",
                    priority=100,
                    trim_policy="never",
                    protected=True,
                    reason="subagent venue isolation",
                ))
                _request_id = _uuid_subctx.uuid4().hex
                _eligibility = _SubEligibility(
                    session_id=str(session_id), request_id=_request_id,
                    task_type=_task_type, mode=str(venue),
                )
                _sub_ctx_mgr = _SubCtxMgr.for_session(
                    model=getattr(local_llm, "model", "unknown"),
                    project_root=None, v2_enabled=True,
                )
                _sub_model_info = _sub_ctx_mgr.config._resolved_model_info()
                from deskpet.agent.attachment_budget import (
                    collect_attachment_budget as _collect_subagent_attachments,
                )
                _sub_attachment_refs, _sub_attachment_tokens = (
                    _collect_subagent_attachments(list(messages))
                )
                _planned = await _planner.prepare_initial(
                    _bundle_for_sub, base_system="",
                    history=_bundle_for_sub.history,
                    user_message=str(user_message), eligibility=_eligibility,
                    context_window=_sub_model_info.context_window,
                    effective_pct=_sub_model_info.effective_pct,
                    generation_reserve=min(
                        8192, max(512, int(_model_info.context_window) // 8)
                    ),
                    prebuilt_messages=list(messages),
                    attachment_refs=_sub_attachment_refs,
                    attachment_tokens=_sub_attachment_tokens,
                )
                _scope_store.open(
                    _planned.prepared_context.tool_set, _eligibility,
                    snapshot_handle=_planned.prepared_context.active_snapshot_handle,
                )
                return _planned.prepared_context, _request_id

            _subagent_context_prepare = (
                _prepare_subagent_context_os
                if bool(getattr(config.features, "context_os_v1", False))
                else None
            )

            _agent_handler, _agent_schema = _build_agent_tool(
                llm_shim=_shim_for_agent,
                parent_tool_registry=deskpet_tool_registry_v2,
                parent_session_id_resolver=_resolve_parent_sid,
                context_prepare=_subagent_context_prepare,
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

            # Companion+Code v1 WI-C: agent_parallel 接电。subagent_driver 或旧
            # agent_parallel 任一开即注册；带 scheduler 时走有界调度+事务分型，
            # 否则现状扁平 gather（scheduler=None → 字节级 BC）.
            _parallel_handler = None
            _parallel_schema = None
            _feat = getattr(config, "features", None)
            if bool(getattr(_feat, "agent_parallel", False)) or bool(
                getattr(_feat, "subagent_driver", False)
            ):
                try:
                    from deskpet.tools.code_tools.agent_parallel_tool import (
                        build_agent_parallel_tool as _build_parallel,
                    )
                    _parallel_handler, _parallel_schema = _build_parallel(
                        llm_shim=_shim_for_agent,
                        parent_tool_registry=deskpet_tool_registry_v2,
                        parent_session_id_resolver=_resolve_parent_sid,
                        scheduler=_subagent_scheduler,
                        kind_overrides=_kind_overrides,
                        shim_resolver=_make_shim_for_model,  # WI-4.2 per-kind 模型路由
                        context_prepare=_subagent_context_prepare,
                    )
                    logger.info(
                        "companion_code_v1_agent_parallel_ready scheduler=%s",
                        _subagent_scheduler is not None,
                    )
                except Exception as _ap_exc:  # noqa: BLE001
                    logger.warning(
                        "companion_code_v1_agent_parallel_init_failed: %s",
                        _ap_exc,
                    )

            # ===== 子代理并发驱动 — spawn_team 暴露 (WI-2.4) =====
            # flag agent_team ON：构造 TeamStore/TaskGraphStore（注入 service_context）
            # + spawn_team LLM 工具。OFF 时 handler=None → 不注册（BC）.
            _spawn_team_handler = None
            _spawn_team_schema = None
            if bool(getattr(
                getattr(config, "features", None), "agent_team", False,
            )):
                try:
                    from pathlib import Path as _PathAT
                    from deskpet.agent.team.team_store import TeamStore as _TeamStore
                    from deskpet.agent.task_graph import (
                        TaskGraphStore as _TaskGraphStore,
                    )
                    from deskpet.tools.code_tools.spawn_team_tool import (
                        build_spawn_team_tool as _build_spawn_team,
                    )

                    _team_store = _TeamStore(
                        base_dir=_PathAT(_paths.user_data_dir()) / "teams"
                    )
                    try:
                        _team_store.cleanup_old()  # WI-2.6 防 .db 堆积
                    except Exception:  # noqa: BLE001
                        pass
                    _task_graph_store = _TaskGraphStore(db=_session_db)
                    service_context.register("team_store", _team_store)
                    service_context.register("task_graph_store", _task_graph_store)

                    _sg = service_context.get("session_goal_store")
                    _goal_text_resolver = (
                        (lambda: _sg.get_goal_text(_resolve_parent_sid()))
                        if _sg is not None else None
                    )
                    _spawn_team_handler, _spawn_team_schema = _build_spawn_team(
                        llm_shim=_shim_for_agent,
                        parent_tool_registry=deskpet_tool_registry_v2,
                        parent_session_id_resolver=_resolve_parent_sid,
                        team_store=_team_store,
                        task_graph_store=_task_graph_store,
                        kind_overrides=_kind_overrides,
                        goal_text_resolver=_goal_text_resolver,
                        goal_id_resolver=None,
                    )
                    logger.info("agent_team_ready")
                except Exception as _at_exc:  # noqa: BLE001
                    logger.warning("agent_team_init_failed: %s", _at_exc)

            # ===== 子代理并发驱动 — 非阻塞 spawn_subagents/await_subagents (WI-3.2) =====
            # flag subagent_nonblocking ON：构造 SubagentRegistry + 两个工具。
            # 非阻塞依赖调度器：若 subagent_driver OFF（_subagent_scheduler 为 None）
            # 则现场建一个默认调度器供非阻塞用（多做不少做）。OFF 时 handler=None（BC）.
            _spawn_subs_handler = None
            _spawn_subs_schema = None
            _await_subs_handler = None
            _await_subs_schema = None
            if bool(getattr(
                getattr(config, "features", None), "subagent_nonblocking", False,
            )):
                try:
                    from deskpet.agent.subagent_registry import (
                        SubagentRegistry as _SubReg,
                    )
                    from deskpet.tools.code_tools.spawn_subagents_tool import (
                        build_spawn_subagents_tools as _build_spawn_subs,
                    )

                    _nb_scheduler = _subagent_scheduler
                    if _nb_scheduler is None:
                        from deskpet.agent.subagent_scheduler import (
                            SubagentScheduler as _SubSched2,
                        )
                        from config import get_subagent_concurrency as _gc2
                        _g2, _l2 = _gc2(config)
                        _nb_scheduler = _SubSched2(
                            global_concurrency=_g2, lane_caps=_l2,
                            progress_sink=_subagent_progress_sink,
                        )
                        if _kind_overrides is None:
                            from deskpet.agent.task_kinds import (
                                load_kind_overrides as _lk2,
                            )
                            _kind_overrides = _lk2(
                                (getattr(config, "raw", None) or {}).get("agent")
                            )
                    _subagent_registry = _SubReg()
                    service_context.register("subagent_registry", _subagent_registry)
                    (
                        (_spawn_subs_handler, _spawn_subs_schema),
                        (_await_subs_handler, _await_subs_schema),
                    ) = _build_spawn_subs(
                        llm_shim=_shim_for_agent,
                        parent_tool_registry=deskpet_tool_registry_v2,
                        parent_session_id_resolver=_resolve_parent_sid,
                        scheduler=_nb_scheduler,
                        registry=_subagent_registry,
                        kind_overrides=_kind_overrides,
                        shim_resolver=_make_shim_for_model,  # WI-4.2 per-kind 模型路由
                    )
                    logger.info("subagent_nonblocking_ready")
                except Exception as _nb_exc:  # noqa: BLE001
                    logger.warning("subagent_nonblocking_init_failed: %s", _nb_exc)

            # Re-register the full code tool set including the closures.
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
            # P5-S2 G1: count bumped 5→6 — added fetch_tool_result
            if _clarify_ask is not None:
                _clarify_handler, _clarify_schema = _build_ask_clarification_tool(
                    _clarify_ask
                )
                deskpet_tool_registry_v2.register(
                    name="ask_clarification",
                    toolset="code",
                    schema=_clarify_schema,
                    handler=_clarify_handler,
                    context_handler=lambda args, context: _clarify_handler(
                        args,
                        context.call_id,
                        execution_context=context,
                    ),
                    permission_category="read_file",
                    source="builtin",
                    timeout_seconds=130.0,
                    replace_allowed=True,
                )
            logger.info("p4_s22_code_tools_registered", count=6)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Preload models on startup (best-effort — failures logged but don't block)."""
    logger.info("preloading models...")
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
            ProposalPort as _RecoveryProposalPort,
            ToolDispatchPort as _RecoveryToolDispatchPort,
        )
        from deskpet.workflows.definitions.v1 import (
            code_complex_initial_state as _code_recovery_initial_state,
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

        def _code_recovery_context_factory(row, start_payload):
            provider = local_llm or cloud_llm
            shim = _RecoveryShim(provider=provider)
            session_id = str(row.get("session_id") or "default")
            provider_name = str(
                dict(start_payload.get("provider_snapshot") or {}).get("provider") or ""
            )
            model_name = str(
                dict(start_payload.get("model_snapshot") or {}).get("model") or ""
            )
            return WorkflowContext(
                ports={
                    "llm": _RecoveryProposalPort(
                        shim,
                        deskpet_tool_registry_v2,
                        session_id=session_id,
                        provider_name=provider_name,
                        model_name=model_name,
                    ),
                    "tool": _RecoveryToolDispatchPort(
                        deskpet_tool_registry_v2,
                        session_id=session_id,
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

        async def _start_deepresearch_graph(args: dict, task_id: str) -> dict:
            topic = str(args.get("topic") or "").strip()
            depth = str(args.get("depth") or "standard").lower()
            research_config = {
                key: int(args[key])
                for key in (
                    "max_sub_questions",
                    "max_urls_per_query",
                    "max_total_passages",
                    "max_rounds",
                )
                if args.get(key) is not None
            }
            # Durable graph inputs must snapshot runtime research switches.
            # Otherwise v4 silently falls back to True and ignores an explicit
            # [research] direct_sources/source_packs=false configuration.
            raw_research = config.raw.get("research", {})
            if isinstance(raw_research, dict):
                for key in ("direct_sources", "source_packs"):
                    if key in raw_research:
                        research_config.setdefault(key, bool(raw_research[key]))

            sid = str(args.get("_session_id") or "default")
            async with _workflow_service.session_lock(sid):
                delivery_state = (
                    await _sdb.get_session_delivery_state(sid)
                    if _sdb is not None else {"epoch": 0}
                )
                if delivery_state.get("deleted_at") is not None:
                    raise RuntimeError("workflow delivery session was deleted")
                workflow_version, version_reason = resolve_deep_research_workflow_version(
                    str(config.workflows.deep_research_version or "v7"),
                    environment=os.environ,
                )
                logger.info(
                    "deepresearch_version_selected",
                    workflow_version=workflow_version,
                    reason=version_reason,
                )
                runtime_adapter = _workflow_service.runtime_adapters.require(
                    "deep_research", workflow_version
                )
                return await _workflow_launcher.launch(
                    workflow_name="deep_research",
                    workflow_version=workflow_version,
                    session_id=sid,
                    request_id=str(args.get("_request_id") or task_id),
                    turn_id=str(args.get("_turn_id") or task_id),
                    start_payload={
                        "topic": topic,
                        "mode": depth,
                        "research_config": research_config,
                        "blob_root": str(_paths.user_data_dir() / "workflows" / "blobs"),
                    },
                    capability_snapshot={
                        "tools": ["deepresearch"],
                        "schema_version": 2 if workflow_version in {"v5", "v6"} else 1,
                        **({
                            "research_llm_budget": {
                                "max_input_tokens": config.research_v5.llm_input_token_budget,
                                "max_output_tokens": config.research_v5.llm_output_token_budget,
                                "max_cost_micros": config.research_v5.llm_cost_budget_micros,
                            },
                            "research_io_budget": {
                                "max_input_tokens": config.research_v5.io_operation_budget,
                                "max_output_tokens": 0,
                                "max_cost_micros": 0,
                            },
                        } if workflow_version in {"v5", "v6"} else {}),
                    },
                    state_factory=runtime_adapter.state_factory,
                    context_factory=runtime_adapter.context_factory,
                    base_epoch=int(delivery_state.get("epoch", 0)),
                    authorize_disabled_deep_research_v6_new_root=(
                        workflow_version == "v6"
                        and version_reason == "dev_isolated_override"
                    ),
                )

        _workflow_research_tools.set_deepresearch_workflow_starter(
            _start_deepresearch_graph
            if config.workflows.enabled and config.workflows.deep_research
            else None
        )
        try:
            _wire_ppt_pro_services_for_startup(recover=False)
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
        try:
            from deskpet.tools import research_tools as _workflow_research_tools

            _workflow_research_tools.set_deepresearch_workflow_starter(None)
        except Exception:
            pass
        logger.warning("workflow_service_init_failed", error=str(exc))
    _expire_ppt_outline_dangling_for_startup()
    # P4-S25 B4: restore persisted code-mode projects from SessionDB.
    # Done after _sdb.initialize() so migration v13 (code_sessions
    # table) is in place. Failure is non-fatal — user just sees an
    # empty project list and can re-add manually.
    _cmm_for_restore = service_context.get("code_mode")
    if _cmm_for_restore is not None and _sdb is not None:
        try:
            _restored_n = await _cmm_for_restore.load_persisted(_sdb)
            logger.info(
                "code_sessions_restored",
                count=_restored_n,
            )
        except Exception as exc:
            logger.warning("code_sessions_restore_failed", error=str(exc))
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
    _sl = service_context.get("skill_loader")
    if _sl is not None:
        try:
            await _sl.start()
            logger.info("p4_skill_loader_ready", count=len(_sl.list_skills()))
        except Exception as exc:
            logger.warning("p4_skill_loader_start_failed", error=str(exc))
    # TC-5.1 修复 (2026-06-11)：SkillMatcher 缓存预热。module-level 的 sync
    # build() 对 async 生产 embedder 是静默 no-op → 这里 loader 启动后台跑
    # build_async（encode 内部自动 warmup embedder，不阻塞启动）。失败无害——
    # match_async 仍会逐 turn 惰性建缓存。
    _sm_for_prewarm = service_context.get("skill_matcher")
    if _sm_for_prewarm is not None and _sl is not None:
        async def _skill_matcher_prewarm_bg() -> None:
            try:
                _n = await _sm_for_prewarm.build_async(_sl.all())
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
    # superpowers Layer 1B — PreferenceMemory（BGE-M3 语义偏好记忆）。
    # flag OFF（默认）→ 不构造 → plan-confirm 门每次都等确认（现状）。
    # 需要真 embedder（embed 接口）；mock 也能跑（向量稳定可匹配）。
    if bool(getattr(config.features, "preference_memory", False)) and _emb is not None:
        try:
            from deskpet.agent.preference_memory import PreferenceMemory
            _pref_mem = PreferenceMemory(
                _paths.user_data_dir() / "preference_memory.json",
                _emb.embed,
                pref_decay=bool(config.memory.v2.pref_decay),
            )
            service_context.register("preference_memory", _pref_mem)
            logger.info(
                "preference_memory_ready entries=%d",
                len(_pref_mem.list_entries()),
            )
        except Exception as _pm_exc:  # noqa: BLE001
            logger.warning("preference_memory_init_failed error=%s", _pm_exc)
    # P4-S15: VectorWorker — starts after SessionDB is initialised so the
    # vec0 schema is in place. After start, wire its enqueue() onto the
    # SessionDB write-hook so new chat turns auto-embed.
    _vw = service_context.get("vector_worker")
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
                await _vw.enqueue(mid, text)
                if (
                    config.memory.v2.facts_extract
                    and _fact_extractor is not None
                ):
                    async def _extract_facts_bg(_mid: int, _text: str) -> None:
                        try:
                            _role = await _sdb.get_message_role(_mid)
                            if not _role:
                                return
                            await _fact_extractor.process_message(
                                message_id=_mid, content=_text, role=_role,
                            )
                        except Exception as _ex:  # noqa: BLE001
                            logger.warning(
                                "facts_extract_bg_failed mid=%s err=%s",
                                _mid, _ex,
                            )
                    asyncio.create_task(_extract_facts_bg(mid, text))
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
        _reflection_llm = _make_str_llm_call(
            local_llm, max_tokens=256, purpose="memory_summarizer"
        )
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
                        _fid = await _worker.run_once()
                        logger.info("reflection_run_once", fact_id=_fid)
                    except Exception as _rex:  # noqa: BLE001
                        logger.warning("reflection_run_failed err=%s", _rex)
                    await asyncio.sleep(_interval_s)

            asyncio.create_task(_reflection_loop())
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
        _curation_llm = _make_str_llm_call(
            local_llm, max_tokens=512, purpose="memory_summarizer"
        )
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
                    code_mode_manager=service_context.get("code_mode"),
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
                # handler each user turn under `_auto_resume_redispatchers`).
                # If no dispatcher is registered yet, we silently no-op
                # — the queued hint will be consumed when the user (or
                # auto_resume) next pokes the agent.
                _redisp = _auto_resume_redispatchers.get(sid)
                if _redisp is None:
                    logger.info(
                        "supervisor_auto_followup_noop sid=%s reason=no_dispatcher",
                        sid,
                    )
                    return
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
                    await _redisp(_target_ws, sid)
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
            # base_sid → code_sid → SessionDB.get_code_todos → filter
            # incomplete. Returned to the watchdog so the (c) trigger
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
                    cm_local = service_context.get("code_mode")
                    if cm_local is None:
                        return []
                    code_sid = cm_local.code_session_id(_base_sid)
                    if not code_sid:
                        return []
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
                    rows = await sdb_local.get_code_todos(code_sid)
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
                code_mode_manager=service_context.get("code_mode"),
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
                async def _auto_resume_dispatch(_sid: str, _msgs: list[dict]) -> None:
                    # 1. Pull hint text out of the injected system msg.
                    _hint_text = ""
                    for _m in reversed(_msgs):
                        if _m.get("_is_supervisor_hint"):
                            _content = _m.get("content") or ""
                            if _content.startswith("[Supervisor Hint] "):
                                _hint_text = _content[len("[Supervisor Hint] "):]
                            else:
                                _hint_text = _content
                            break
                    # 2. Push to nudge_queue so the next chat task picks it up.
                    if _hint_text:
                        try:
                            from agent.nudge_queue import Hint as _Hint2
                            _nq2 = service_context.get("nudge_queue")
                            if _nq2 is not None:
                                await _nq2.push(
                                    _sid,
                                    _Hint2(
                                        text=_hint_text,
                                        alert_id="auto_resume",
                                        severity="yellow",
                                    ),
                                )
                        except Exception as _ex:  # noqa: BLE001
                            logger.debug("auto_resume_hint_push_failed sid=%s err=%s", _sid, _ex)
                    # 3. Look up the per-sid re-dispatcher and fire.
                    _redisp = _auto_resume_redispatchers.get(_sid)
                    _ws_for_sid = _control_connections.get(_sid) or _control_connections.get("default")
                    if _redisp is None or _ws_for_sid is None:
                        logger.warning(
                            "auto_resume_no_redispatcher sid=%s ws=%s",
                            _sid, "yes" if _ws_for_sid else "no",
                        )
                        return
                    try:
                        await _redisp(_ws_for_sid, _sid)
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
                    chat_dispatcher=_auto_resume_dispatch,
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

    logger.info("startup complete")
    yield
    from deskpet.retrieval.runtime import shutdown_default_gateway
    await shutdown_default_gateway()
    _workflow_service = service_context.get("workflow_service")
    _workflow_launcher = getattr(_workflow_service, "launcher", None)
    if _workflow_launcher is not None:
        try:
            await _workflow_launcher.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning("workflow_launcher_shutdown_failed", error=str(exc))
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
    _sl = service_context.get("skill_loader")
    if _sl is not None:
        try:
            await _sl.stop()
        except Exception as exc:
            logger.warning("p4_skill_loader_stop_failed", error=str(exc))

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
_PPT_OUTLINE_WAITERS = PPTOutlineWaiters()
# Transport websocket sid -> effective chat sid shown by that peer.
_chat_peer_groups: dict[str, str] = {
    "default": "default",
    "message-panel-main": "default",
}


async def _broadcast_control(msg: dict) -> None:
    """Best-effort fan-out to every control websocket."""
    for _sid, _ws in list(_control_connections.items()):
        try:
            await _ws.send_json(msg)
        except Exception as exc:  # noqa: BLE001
            logger.debug("control_broadcast_failed sid=%s err=%s", _sid, exc)


async def _ppt_outline_propose(
    sid: str,
    *,
    topic,
    slides,
    sources_count,
    outline_md,
    no_research,
) -> dict:
    oid = uuid.uuid4().hex
    cfg = _ppt_pro_cfg()
    history_enabled = bool(getattr(cfg, "outline_history", False))
    if history_enabled:
        save_outline(oid, sid, topic, slides, int(sources_count or 0))

    payload = {
        "outline_id": oid,
        "topic": topic,
        "outline_md": outline_md,
        "session_id": sid,
        "sources_count": int(sources_count or 0),
        "no_research": bool(no_research),
        "history": list_history(sid, 20) if history_enabled else [],
    }
    await _broadcast_control({"type": "ppt_outline_proposed", "payload": payload})
    try:
        text = f"PPT 大纲确认 · {topic}\n\n{outline_md}".strip()
        sdb = service_context.get("session_db")
        if sdb is not None:
            msg_id = await sdb.append_message(
                session_id=sid or "default",
                role="assistant",
                content=text,
            )
            vw = service_context.get("vector_worker")
            if vw is not None and msg_id is not None:
                await vw.enqueue(msg_id, text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ppt_outline_persist_failed sid=%s err=%s", sid, exc)

    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    _PPT_OUTLINE_WAITERS.add(oid, fut)
    try:
        decision = await asyncio.wait_for(
            fut,
            float(getattr(cfg, "confirm_timeout_s", 1800.0) or 1800.0),
        )
    except asyncio.TimeoutError:
        decision = {"action": "cancel"}
    except asyncio.CancelledError:
        mark_status(oid, "cancelled")
        raise
    finally:
        _PPT_OUTLINE_WAITERS.pop(oid)

    action = str((decision or {}).get("action") or "").lower()
    mark_status(
        oid,
        {
            "accept": "accepted",
            "modify": "proposed",
            "cancel": "cancelled",
            "reuse": "accepted",
        }.get(action, "rejected"),
    )
    return dict(decision or {})


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


async def _start_workflow_ppt_outline_resume(oid: str, response: dict) -> str | None:
    if not oid.startswith("workflow:"):
        return None
    parts = oid.split(":", 2)
    if len(parts) != 3 or not parts[1]:
        return None
    run_id = parts[1]
    service = service_context.get("workflow_service")
    if service is None:
        return None
    decisions = await service.human_store.list_open_decisions(run_id=run_id)
    decision = next(
        (
            item
            for item in decisions
            if item.kind == "ppt_outline"
            and isinstance(item.prompt, dict)
            and str(item.prompt.get("outline_id") or "") == oid
        ),
        None,
    )
    if decision is None:
        return None
    row = await service.run_store.get_run(run_id)
    sid = str((row or {}).get("session_id") or "default")
    task = asyncio.create_task(
        _resume_workflow_ppt_outline(service, decision, response, sid)
    )
    _workflow_ipc_background_tasks.add(task)
    task.add_done_callback(_workflow_ipc_background_tasks.discard)
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


async def _handle_control_ws_message(raw: dict, *, session_id: str, ws: WebSocket) -> bool:  # noqa: ARG001
    msg_type = raw.get("type", "")
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
    workflow_sid = None
    resolved = bool(oid and _PPT_OUTLINE_WAITERS.resolve(oid, decision))
    if oid and not resolved:
        workflow_sid = await _start_workflow_ppt_outline_resume(oid, decision)
        resolved = workflow_sid is not None
    if resolved:
        await _broadcast_control({
            "type": "ppt_outline_resolved",
            "payload": {"outline_id": oid},
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


async def _ppt_artifact_push(sid: str, artifacts: list, text: str = "") -> None:
    payload = {
        "tool": "ppt_pro",
        "ok": True,
        "result": text or "",
        "text": text or "",
        "artifacts": list(artifacts or []),
        "session_id": sid or "default",
    }
    sdb = service_context.get("session_db")
    if sdb is not None:
        try:
            await sdb.append_message(
                session_id=sid or "default",
                role="tool",
                content=json.dumps(payload, ensure_ascii=False),
                tool_call_id="",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ppt_artifact_push_persist_failed", error=str(exc))
    await _broadcast_control({"type": "tool_result", "payload": payload})


def _ppt_receipt_report(
    sid: str,
    *,
    tool: str = "ppt_pro",
    outcome,
    path=None,
    shas=None,
) -> None:
    try:
        store_getter = globals().get("_get_receipt_store")
        store = store_getter() if callable(store_getter) else None
        if store is None:
            return
        from deskpet.tools.receipt_store import emit_receipt

        now = datetime.now(timezone.utc)
        ok = str(outcome or "").lower() in {"ok", "success", "succeeded"}
        emit_receipt(
            store,
            tool_name=str(tool or "ppt_pro"),
            args={"outcome": outcome, "path": path},
            started_at=now,
            ended_at=now,
            ok=ok,
            session_id=sid or "default",
            artifact_shas=list(shas or []) or None,
            error_class=None if ok else str(outcome or "failed"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("ppt_receipt_report_failed", error=str(exc))


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


def _wire_ppt_pro_services_for_startup(*, recover: bool = True) -> None:
    workflow_starter = None
    workflow_service = service_context.get("workflow_service")
    launcher = getattr(workflow_service, "launcher", None)
    if (
        launcher is not None
        and config.workflows.enabled
        and config.workflows.ppt_pro
    ):
        from deskpet.workflows.adapters.ppt_runtime import PptRuntime
        from deskpet.workflows.contracts import WorkflowContext
        from deskpet.workflows.definitions.research_core import legacy_ports
        from deskpet.workflows.definitions.v1 import ppt_pro_initial_state
        from deskpet.tools import research_tools as _workflow_research_tools

        ppt_runtime = PptRuntime(executor=_PPT_PRO_RENDER_EXECUTOR)

        async def _ppt_context_factory(row, start_payload) -> WorkflowContext:
            llm_call = await _workflow_research_tools._resolve_default_llm_call()
            ports = legacy_ports(llm_call=llm_call, search=None, extract=None)
            return WorkflowContext(
                ports={
                    "llm": ports.llm,
                    "search": ports.search,
                    "fetch": ports.fetch,
                    "effect": ppt_runtime,
                    "evaluator": ppt_runtime,
                    "notifier": _ppt_workflow_outline_notify,
                },
                request_id=str(row.get("request_id") or ""),
                turn_id=str(row.get("turn_id") or ""),
            )

        def _ppt_state_factory(**values):
            return ppt_pro_initial_state(
                topic=str(values["topic"]),
                run_id=str(values["run_id"]),
                thread_id=str(values["thread_id"]),
                session_id=str(values["session_id"]),
                pages=int(values.get("pages") or 8),
                depth=str(values.get("depth") or "deep"),
                theme=str(values.get("theme") or "minimal"),
                image_mode=bool(values.get("image_mode", True)),
                title=str(values.get("title") or values["topic"]),
                author=str(values.get("author") or "DeskPet"),
                output_path=values.get("output_path"),
                blob_root=str(values.get("blob_root") or ""),
            )

        launcher.register_adapter(
            "ppt_pro",
            "v1",
            state_factory=_ppt_state_factory,
            context_factory=_ppt_context_factory,
        )

        async def workflow_starter(payload: dict) -> dict:
            sid = str(payload.get("session_id") or "default")
            start_payload = {
                key: payload.get(key)
                for key in (
                    "topic", "pages", "depth", "theme", "image_mode",
                    "title", "author", "output_path",
                )
            }
            start_payload["blob_root"] = str(
                _paths.user_data_dir() / "workflows" / "blobs"
            )
            async with workflow_service.session_lock(sid):
                session_db = service_context.get("session_db")
                delivery_state = (
                    await session_db.get_session_delivery_state(sid)
                    if session_db is not None else {"epoch": 0}
                )
                if delivery_state.get("deleted_at") is not None:
                    raise RuntimeError("workflow delivery session was deleted")
                return await launcher.launch(
                    workflow_name="ppt_pro",
                    workflow_version="v1",
                    session_id=sid,
                    request_id=str(payload["request_id"]),
                    turn_id=str(payload["turn_id"]),
                    start_payload=start_payload,
                    capability_snapshot={"tools": ["ppt_pro"], "schema_version": 1},
                    state_factory=_ppt_state_factory,
                    context_factory=_ppt_context_factory,
                    base_epoch=int(delivery_state.get("epoch", 0)),
                )

        if recover:
            asyncio.create_task(launcher.recover_pending())

    ppt_tools.set_ppt_pro_services(
        outline_propose=_ppt_outline_propose,
        notifier=_ppt_notify_chat_bubble,
        run_blocking=lambda fn: asyncio.get_running_loop().run_in_executor(_PPT_PRO_RENDER_EXECUTOR, fn),
        artifact_pusher=_ppt_artifact_push,
        receipt_reporter=_ppt_receipt_report,
        workflow_starter=workflow_starter,
    )


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
    if decision.created:
        _remap_chat_peer_group(base_sid, decision.effective_sid)
    return decision


def _select_code_workflow_provider(*, local_llm, cloud_llm):
    """Select the legacy fallback when no session provider chain resolves."""

    return local_llm or cloud_llm


def _build_code_workflow_provider_chain(*, entries, registry):
    """Build every provider resolved for a durable Code workflow, in order."""

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
        providers.append(provider)
    return providers


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
    # The service deliberately caps one hydration request at 100 ids.  Chat
    # sessions can legitimately contain more than that, especially now that a
    # DeepResearch run publishes one durable event per visible stage.  Hydrate
    # in bounded batches instead of rejecting the entire session history.
    hydrated_events: list[dict[str, Any]] = []
    for offset in range(0, len(event_ids), 100):
        hydrated_page = await workflow_service.hydrate_session_history_event_ids(
            session_id,
            event_ids[offset : offset + 100],
            current_session_epoch=int(delivery_state.get("epoch", 0)),
            session_deleted=delivery_state.get("deleted_at") is not None,
        )
        hydrated_events.extend(hydrated_page.get("events", []))
    events_by_id = {
        str(event.get("event_id") or ""): event
        for event in hydrated_events
    }
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


def _is_companion_history_session_id(
    session_id: str,
    *,
    code_base_session_ids: set[str] | None = None,
) -> bool:
    """Return whether a session id should be shown in the message panel list."""
    sid = (session_id or "").strip()
    if sid == "default":
        return True
    # Legacy task ids remain visible so old history can still be renamed,
    # copied, or deleted. Newly generated task sessions are opaque UUIDs.
    if sid.startswith("task-"):
        return True
    if code_base_session_ids and sid in code_base_session_ids:
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
    compact_at_pct = 0.80
    recall_sweet = 16_000
    if _resolve_model is not None and model:
        try:
            _info = _resolve_model(model)
            context_window = int(_info.context_window)
            effective_pct = float(_info.effective_pct)
            compact_at_pct = float(_info.compact_at_pct)
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
    _session_context_state[session_id] = payload
    return {"type": "context_usage", "payload": payload}


async def _emit_context_usage(
    originator_ws: WebSocket,
    session_id: str,
    *,
    provider_chain: Any | None,
    fallback_provider: Any | None = None,
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

    # 5) Total + LLM authoritative number for cross-check.
    last = _session_context_state.get(session_id) or {}
    return {
        "session_id": session_id,
        "model": last.get("model", ""),
        "sections": sections,
        "total_estimated_tokens": sum(s["tokens"] for s in sections),
        "last_usage_prompt_tokens": last.get("prompt_tokens", 0),
        "context_window": last.get("context_window", 0),
        "effective_ceiling": last.get("effective_ceiling", 0),
        "compact_at": last.get("compact_at", 0),
        "updated_at": last.get("updated_at", 0),
        "ts": time.time(),
    }


_PEER_BROADCAST_TIMEOUT_S = 1.0


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
    for _peer_sid, _peer_ws in list(_control_connections.items()):
        if _peer_ws is originator_ws:
            continue
        _main_group = _chat_peer_groups.get(_peer_sid, _initial_chat_peer_group(_peer_sid))
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


# P4-S23: track in-flight chat tasks per session_id so multi-session
# panels can cancel-on-retry without leaking stale AgentLoop runs.
_chat_inflight: dict[str, asyncio.Task] = {}
# P5-S2 Phase 4: per-sid re-dispatchers populated by the chat handler so
# the AutoResumeOrchestrator can spawn a follow-up turn without needing
# direct access to the chat-handler-scoped ``_run_chat`` closure. The
# value is an awaitable ``(ws, sid) -> None`` that re-enters _run_chat
# with a synthetic trigger text. The chat handler refreshes this entry
# on every user-initiated turn so the closure always captures the
# latest local state.
_auto_resume_redispatchers: dict[str, "Callable[[WebSocket, str], Awaitable[None]]"] = {}  # noqa: F821
# Track active voice pipelines by session so that a control-channel `interrupt`
# message can reach the audio-channel pipeline (they are separate WebSockets).
_pipelines: dict[str, "VoicePipeline"] = {}  # noqa: F821 — forward ref, set at runtime


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
    _sl = service_context.get("skill_loader")
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
    _sl = service_context.get("skill_loader")
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
    _sl = service_context.get("skill_loader")
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
    _register_chat_peer(session_id)
    # P4-S20: gracefully kick the previous holder of this session_id
    # (e.g. an old E2E client) so its disconnect callback doesn't
    # later pop OUR entry. Don't kill ourselves if we're the holder.
    _prev_ws = _control_connections.get(session_id)
    if _prev_ws is not None and _prev_ws is not ws:
        try:
            await _prev_ws.close(code=4002, reason="session replaced")
        except Exception:
            pass
    _control_connections[session_id] = ws
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
    try:
        while True:
            raw = await ws.receive_json()
            msg_type = raw.get("type", "")

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
                # P4-S20: drain a pending PermissionGate request.
                payload = raw.get("payload", {}) or {}
                rid = payload.get("request_id", "")
                decision = payload.get("decision", "deny")
                fut = _permission_pending.get(rid) if "_permission_pending" in dir() or True else None
                # Note: above expression always reads the module-level
                # name; ``_permission_pending`` is initialized at import.
                fut = _permission_pending.get(rid)
                if fut is not None and not fut.done():
                    try:
                        from deskpet.types.skill_platform import PermissionResponse as _Resp
                        fut.set_result(_Resp(request_id=rid, decision=decision))
                    except Exception as _e:
                        logger.warning("permission_response_set_result_failed", error=str(_e))
                else:
                    logger.info(
                        "permission_response_no_pending",
                        request_id=rid,
                    )

            elif msg_type == "clarification_response":
                payload = raw.get("payload", {}) or {}
                if callable(resolve_clarification_response) and resolve_clarification_response(
                    _clarify_pending,
                    payload,
                ):
                    logger.info(
                        "clarification_response_resolved",
                        request_id=payload.get("request_id", ""),
                    )
                else:
                    logger.info(
                        "clarification_response_no_pending",
                        request_id=payload.get("request_id", ""),
                    )

            elif msg_type == "permission_auto_mode_set":
                # P4-S21 #13: toggle "yes-to-all" mode. Settings panel
                # sends {enabled: bool}; we flip the flag on the live
                # PermissionGate instance. Default is OFF — has to be
                # explicitly opted in. Reverts on backend restart.
                payload = raw.get("payload", {}) or {}
                enabled = bool(payload.get("enabled", False))
                if permission_gate_v2 is not None:
                    # P4-S25: route through set_auto_mode so the choice
                    # persists across backend restart (was process-only).
                    permission_gate_v2.set_auto_mode(enabled)
                    logger.info(
                        "permission_auto_mode_set", enabled=enabled,
                    )
                await ws.send_json({
                    "type": "permission_auto_mode_response",
                    "payload": {"enabled": enabled},
                })

            elif msg_type == "permission_auto_mode_get":
                enabled = bool(
                    permission_gate_v2 is not None
                    and getattr(permission_gate_v2, "auto_mode", False)
                )
                await ws.send_json({
                    "type": "permission_auto_mode_response",
                    "payload": {"enabled": enabled},
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

            elif msg_type == "code_mode_enter":
                # P4-S22: enter Code mode for this session.
                # Payload: {project_path?, suggested_name?, session_id?}
                # P4-S23: payload.session_id lets the new code panel
                # bind a fresh base sid per project, so multiple Code
                # mode sessions can run in parallel without colliding
                # on the WS-level "default" session.
                payload = raw.get("payload", {}) or {}
                project_path = payload.get("project_path") or None
                suggested_name = (
                    payload.get("suggested_name") or "untitled"
                )
                target_sid = payload.get("session_id") or session_id
                cmm = service_context.get("code_mode")
                if cmm is None:
                    await ws.send_json({
                        "type": "code_mode_state",
                        "payload": {
                            "enabled": False,
                            "error": "code mode manager not initialized",
                            "session_id": target_sid,
                        },
                    })
                    continue
                try:
                    from deskpet.code_mode import resolve_project_root
                    root = resolve_project_root(project_path, suggested_name)
                    # P4-S25 B4 fix: if this project_root already has a
                    # persisted base_session_id, REUSE it. Otherwise the
                    # frontend's freshly-generated random sid creates a
                    # new conversation slot and orphans all prior chat
                    # history (which is keyed by base_session_id in the
                    # messages table). Lookup is by absolute path —
                    # resolve_project_root already canonicalised it.
                    sdb = service_context.get("session_db")
                    if sdb is not None:
                        try:
                            existing = await sdb.list_code_sessions()
                            target_root = str(root.resolve())
                            for row in existing:
                                if str(row["project_root"]) == target_root:
                                    target_sid = row["base_session_id"]
                                    logger.info(
                                        "code_mode_enter_reusing_existing",
                                        base_session_id=target_sid,
                                        project_root=target_root,
                                    )
                                    break
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                "code_mode_enter_lookup_failed",
                                error=str(exc),
                            )
                    state = cmm.enter(target_sid, root)
                    await ws.send_json({
                        "type": "code_mode_state",
                        "payload": {
                            "enabled": True,
                            "project_root": str(state.project_root),
                            "project_name": state.project_name,
                            "code_session_id": state.code_session_id,
                            "session_id": target_sid,
                        },
                    })
                except Exception as exc:  # noqa: BLE001
                    logger.warning("code_mode_enter_failed", error=str(exc))
                    await ws.send_json({
                        "type": "code_mode_state",
                        "payload": {
                            "enabled": False,
                            "error": str(exc),
                            "session_id": target_sid,
                        },
                    })

            elif msg_type == "code_mode_exit":
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                cmm = service_context.get("code_mode")
                if cmm is not None:
                    cmm.exit(target_sid)
                # Clear injected project_root from tool registry context
                if deskpet_tool_registry_v2 is not None:
                    deskpet_tool_registry_v2.set_session_context(target_sid, None)
                # P5-S1: clear supervisor state (activity tracking, queued nudges)
                # before broadcasting the exit so any in-flight watchdog scan
                # sees a consistent "no longer in Code mode" view.
                _sa = service_context.get("session_activity")
                if _sa is not None:
                    try:
                        await _sa.drop(target_sid)
                    except Exception as _exc:
                        logger.debug("session_activity_drop_failed", error=str(_exc))
                _nq = service_context.get("nudge_queue")
                if _nq is not None:
                    try:
                        await _nq.clear(target_sid)
                    except Exception as _exc:
                        logger.debug("nudge_queue_clear_failed", error=str(_exc))
                await ws.send_json({
                    "type": "code_mode_state",
                    "payload": {"enabled": False, "session_id": target_sid},
                })

            elif msg_type == "code_mode_status":
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                cmm = service_context.get("code_mode")
                if cmm is None or not cmm.is_enabled(target_sid):
                    await ws.send_json({
                        "type": "code_mode_state",
                        "payload": {"enabled": False, "session_id": target_sid},
                    })
                else:
                    s = cmm.get(target_sid)
                    await ws.send_json({
                        "type": "code_mode_state",
                        "payload": {
                            "enabled": True,
                            "project_root": str(s.project_root) if s else None,
                            "project_name": s.project_name if s else "",
                            "code_session_id": s.code_session_id if s else None,
                            "session_id": target_sid,
                        },
                    })

            elif msg_type == "code_todo_list":
                # P4-S22: frontend asks for current todos (e.g. on panel open).
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                cmm = service_context.get("code_mode")
                sdb = service_context.get("session_db")
                items: list = []
                if cmm and sdb:
                    csid = cmm.code_session_id(target_sid)
                    if csid:
                        try:
                            items = await sdb.get_code_todos(csid)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "code_todo_list_failed", error=str(exc),
                            )
                await ws.send_json({
                    "type": "code_todo_update",
                    "payload": {"items": items, "session_id": target_sid},
                })

            elif msg_type == "context_usage_request":
                # 2026-05-28 — frontend on (re)connect pulls the cached
                # context-usage snapshot per session_id so the ring gauge
                # can hydrate before any new LLM turn fires.
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                cached = _session_context_state.get(target_sid)
                if cached:
                    try:
                        await ws.send_json({"type": "context_usage", "payload": cached})
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("context_usage_request_send_failed err=%s", exc)
                else:
                    # No snapshot yet — emit a "model-only" stub so the
                    # frontend can render the ring with 0 / window before
                    # the first real turn lands.
                    try:
                        from llm.model_info import resolve as _resolve_model_for_stub
                        # P-B 修复: 用有效出站模型(运行时覆盖后)发给前端 context_usage 环,
                        # 而非 config.raw 旧种子 → 否则前端模型环显示旧 gemma 名 + 错窗口。
                        _stub_model = effective_llm_model(config)
                        _info = _resolve_model_for_stub(_stub_model) if _stub_model else None
                        _cw = int(_info.context_window) if _info else 32_000
                        _eff = float(_info.effective_pct) if _info else 0.95
                        _ca = float(_info.compact_at_pct) if _info else 0.80
                        _rs = int(_info.recall_sweet_tokens) if _info else 16_000
                        await ws.send_json({
                            "type": "context_usage",
                            "payload": {
                                "session_id": target_sid,
                                "model": _stub_model,
                                "prompt_tokens": 0,
                                "completion_tokens": 0,
                                "cached_tokens": 0,
                                "context_window": _cw,
                                "effective_ceiling": int(_cw * _eff),
                                "compact_at": int(_cw * _ca),
                                "recall_sweet": _rs,
                                "updated_at": time.time(),
                                "stub": True,
                            },
                        })
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("context_usage_request_stub_failed err=%s", exc)

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
                # 消息面板「历史会话」下拉：列 companion 会话(default + UUID)，
                # 并保留旧 task-* 历史兼容。排除 code-* / 内部会话(mr_/epi_ 等)。
                _sl_sdb = service_context.get("session_db")
                _sl_out: list = []
                if _sl_sdb is not None:
                    try:
                        _sl_rows = await _sl_sdb.list_sessions_with_preview()
                        _sl_code_base_ids = {
                            str(row.get("base_session_id") or "")
                            for row in await _sl_sdb.list_code_sessions()
                        }
                        for _sr in _sl_rows:
                            _ssid = _sr.get("session_id") or ""
                            if _is_companion_history_session_id(
                                _ssid,
                                code_base_session_ids=_sl_code_base_ids,
                            ):
                                _sl_out.append(_sr)
                    except Exception as _sl_exc:  # noqa: BLE001
                        logger.warning("sessions_list_failed", error=str(_sl_exc))
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
                if sdb is not None:
                    try:
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
                            }
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
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "session_messages_load_failed",
                            error=str(exc), session_id=target_sid,
                        )
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
                        _slash_skill_loader = service_context.get("skill_loader")
                        _slash_goal_store = service_context.get("session_goal_store")
                        _slash_pref_mem = service_context.get("preference_memory")
                        _slash_result = await dispatch_slash_command(
                            cmd_name, cmd_args, target_sid,
                            skill_loader=_slash_skill_loader,
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
                    await ws.send_json({
                        "type": "slash_command_result",
                        "payload": {
                            "command": cmd_name,
                            "session_id": target_sid,
                            "result": _slash_result,
                        },
                    })

            elif msg_type == "chat_v2_interrupt":
                # P4-S25 B3: stop the in-flight chat task for a given
                # session WITHOUT enqueueing a new one. Pairs with the
                # frontend "停止" button — when the user clicks it, the
                # current LLM call gets cancelled and the inflight
                # state on that session clears, freeing the user to
                # type a fresh prompt.
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                _t = _chat_inflight.get(target_sid)
                cancelled = False
                if _t is not None and not _t.done():
                    _t.cancel()
                    cancelled = True
                # 子代理并发驱动 WI-3.3/F6/D10：取消级联——/stop 同时收割所有活的
                # 非阻塞子代理 task（它们由 registry 持有、不属 chat task）。OFF 时
                # 槽为 None → no-op（BC）.
                try:
                    _sub_reg = service_context.get("subagent_registry")
                    if _sub_reg is not None:
                        _sub_reg.cancel_all()
                except Exception as _cc_exc:  # noqa: BLE001
                    logger.debug("subagent cancel cascade failed: %s", _cc_exc)
                # PPT Pro（WI-7③/R-16）：/停止 同时取消该 session 正在跑的 ppt_pro
                # 后台编排 task（调研/等确认/渲染阶段）——它由 _PPT_PRO_RUNNING 持有、
                # 不属 chat task，故需单独收割；无在跑任务时 no-op。
                try:
                    ppt_tools._ppt_pro_cancel(target_sid)
                except Exception as _pc_exc:  # noqa: BLE001
                    logger.debug("ppt_pro cancel cascade failed: %s", _pc_exc)
                # Tell the frontend regardless — it expects a response so
                # the button can revert. If nothing was running, send the
                # confirmation anyway (idempotent UX).
                _interrupt_evt = {
                    "type": "chat_v2_interrupted",
                    "payload": {
                        "session_id": target_sid,
                        "cancelled": cancelled,
                    },
                }
                await ws.send_json(_interrupt_evt)
                await _broadcast_default_chat_peers(ws, _interrupt_evt)

            elif msg_type == "plan_confirm":
                # superpowers 决策2 — plan-confirm 硬门的用户裁决。
                # 前端 PlanCard 的 [执行]/[取消] 按钮发来 {session_id, decision}。
                # set_result 唤醒挂在 _PLAN_CONFIRM_WAITERS 上的 _run_chat 协程。
                payload = raw.get("payload", {}) or {}
                _csid = payload.get("session_id") or session_id
                _decision = payload.get("decision") or "go"
                _waiter = _PLAN_CONFIRM_WAITERS.get(_csid)
                _fut = _waiter.get("fut") if _waiter else None
                if _fut is not None and not _fut.done():
                    _fut.set_result("go" if _decision == "go" else "cancel")
                    logger.info(
                        "plan_confirm_received sid=%s decision=%s", _csid, _decision
                    )
                    # Layer 1B: 用户批准 → 记计划记忆,下次相似任务自动确认。
                    if _decision == "go":
                        _pref = service_context.get("preference_memory")
                        _task_text = (_waiter or {}).get("text")
                        if _pref is not None and _task_text:
                            asyncio.create_task(
                                _pref.record(_task_text, "approved", "plan")
                            )
                else:
                    logger.info(
                        "plan_confirm_no_waiter sid=%s (timed out / already resolved)",
                        _csid,
                    )
                # FEAT-A4: 不论 go/cancel（含无 waiter 的 already-resolved），用户
                # 已对 plan 做出裁决 → 清 sidecar awaiting 标记。与 _run_chat
                # finally 重复但幂等，覆盖"finally 尚未跑到"的竞态窗口。
                _sdb_pc = service_context.get("session_db")
                if _sdb_pc is not None:
                    try:
                        await _sdb_pc.clear_session_plan_awaiting(_csid)
                    except Exception as _pc_e:  # noqa: BLE001
                        logger.debug(
                            "plan_confirm_clear_failed sid=%s err=%s", _csid, _pc_e
                        )

            elif msg_type == "skill_candidate_confirm":
                # WI-4.3 技能自创闭环 — 前端确认/拒绝候选技能。
                # 与 plan_confirm 镜像：set_result 唤醒 _run_chat 末段挂起的协程。
                # payload: {candidate_id: int, accept: bool}
                _sc_payload = raw.get("payload", {}) or {}
                _sc_cid = int(_sc_payload.get("candidate_id", 0) or 0)
                _sc_accept = bool(_sc_payload.get("accept", False))
                _sc_decision = "accept" if _sc_accept else "reject"
                if _SKILL_CANDIDATE_WAITERS is not None:
                    _SKILL_CANDIDATE_WAITERS.resolve(_sc_cid, _sc_decision)
                    logger.info(
                        "skill_candidate_confirm_received cid=%d decision=%s",
                        _sc_cid, _sc_decision,
                    )
                else:
                    logger.debug("skill_candidate_confirm: waiters not initialized")

            elif msg_type == "code_session_delete":
                # P4-S24 followup: user clicked 🗑️ on a project tile / sidebar
                # entry and confirmed the dialog. Drop the in-memory state
                # AND wipe code_todos for that code session. Chat history
                # in `messages` is preserved on purpose (same philosophy as
                # `code_mode_exit`) so re-adding the same project root
                # later resumes from where the user left off.
                payload = raw.get("payload", {}) or {}
                target_sid = payload.get("session_id") or session_id
                cmm = service_context.get("code_mode")
                sdb = service_context.get("session_db")
                deleted_csid: str | None = None
                workflows = service_context.get("workflow_service")
                candidate_csid = (
                    str(cmm.code_session_id(target_sid) or "") if cmm is not None else ""
                )
                held_locks = []
                deleted_todos = 0
                try:
                    if workflows is not None:
                        for lock_sid in sorted({target_sid, candidate_csid} - {""}):
                            lock = workflows.session_lock(lock_sid)
                            await lock.acquire()
                            held_locks.append(lock)
                    if cmm is not None:
                        deleted_csid = await cmm.delete(target_sid)
                    if deskpet_tool_registry_v2 is not None:
                        deskpet_tool_registry_v2.set_session_context(target_sid, None)
                    if sdb is not None and deleted_csid:
                        await sdb.tombstone_session(
                            deleted_csid,
                            reason="code_session_deleted",
                        )
                        if workflows is not None:
                            await workflows.cancel_runs_for_session(
                                deleted_csid,
                                session_kind="code",
                                reason="code_session_deleted",
                            )
                        deleted_todos = await sdb.delete_code_todos(deleted_csid)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "code_session_delete_todos_failed",
                        error=str(exc),
                        session_id=deleted_csid,
                    )
                finally:
                    for lock in reversed(held_locks):
                        if lock.locked():
                            lock.release()
                await ws.send_json({
                    "type": "code_session_deleted",
                    "payload": {
                        "base_session_id": target_sid,
                        "code_session_id": deleted_csid,
                        "deleted_todos": deleted_todos,
                    },
                })
                # Auto-broadcast a refreshed list so multi-window
                # dashboards (pet + code panel) both update without
                # the frontend having to ask.
                items_list_after: list = []
                if cmm is not None:
                    for base_sid_after, st_after in cmm.all_sessions().items():
                        todo_count_after = 0
                        if sdb is not None and st_after.code_session_id:
                            try:
                                tds = await sdb.get_code_todos(st_after.code_session_id)
                                todo_count_after = len(tds)
                            except Exception:
                                pass
                        items_list_after.append({
                            "base_session_id": base_sid_after,
                            "code_session_id": st_after.code_session_id,
                            "project_root": str(st_after.project_root) if st_after.project_root else None,
                            "project_name": st_after.project_name,
                            "todo_count": todo_count_after,
                            "enabled": st_after.enabled,
                        })
                await ws.send_json({
                    "type": "code_sessions_list_response",
                    "payload": {"items": items_list_after},
                })

            elif msg_type == "code_sessions_list":
                # P4-S23: dashboard pulls all enabled code sessions in
                # one shot. Returns [{base_session_id, code_session_id,
                # project_root, project_name, todo_count, enabled}].
                cmm = service_context.get("code_mode")
                sdb = service_context.get("session_db")
                items_list: list = []
                if cmm is not None:
                    for base_sid, st in cmm.all_sessions().items():
                        todo_count = 0
                        if sdb is not None and st.code_session_id:
                            try:
                                todos = await sdb.get_code_todos(st.code_session_id)
                                todo_count = len(todos)
                            except Exception:
                                pass
                        # code-session-model-params: carry the per-session
                        # binding so the picker pre-fills + the tile's
                        # model badge survives a restart (the list was the
                        # only sync path that dropped it → "badge gone, did
                        # the switch even work?" confusion).
                        _pid = _pref = None
                        _mparams = None
                        if sdb is not None:
                            try:
                                _b = await sdb.get_code_session_provider_binding(base_sid)
                                _pid = _b.get("provider_id")
                                _pref = _b.get("preferred_model")
                                _mparams = _b.get("model_params")
                            except Exception:
                                pass
                        items_list.append({
                            "base_session_id": base_sid,
                            "code_session_id": st.code_session_id,
                            "project_root": str(st.project_root) if st.project_root else None,
                            "project_name": st.project_name,
                            "todo_count": todo_count,
                            "enabled": st.enabled,
                            "provider_id": _pid,
                            "preferred_model": _pref,
                            "model_params": _mparams,
                        })
                await ws.send_json({
                    "type": "code_sessions_list_response",
                    "payload": {"items": items_list},
                })

            elif msg_type == "code_models_list":
                # code-session-model-params: the picker's model dropdown
                # is data-driven, NOT a hardcoded preset list. Pull the
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
                # 拉中转站 /models。这样登录中转站即可在 code 模式换模型，无需手动
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
                    "type": "code_models_list_response",
                    "payload": {
                        "models": _bc(_model_ids),
                        "source": _source,
                        "base_url": _base_url,
                        "default_model": _default_model,
                    },
                })

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
                    if not _pid:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "missing_field", "detail": "id required"},
                        })
                        continue
                    try:
                        entry = await _reg.update_provider(_pid, **_patch)
                    except KeyError:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "not_found", "detail": f"provider {_pid!r} not found"},
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
                    if not _pid:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "missing_field", "detail": "id required"},
                        })
                        continue
                    try:
                        await _reg.remove_provider(_pid)
                    except KeyError:
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "not_found", "detail": f"provider {_pid!r} not found"},
                        })
                    except Exception as exc:  # noqa: BLE001
                        await ws.send_json({
                            "type": "settings_providers_error",
                            "payload": {"reason": "internal_error", "detail": str(exc)},
                        })
                    else:
                        # Clean up orphan code_session_provider bindings
                        # so resolution doesn't surface a stale pin.
                        _sdb_clean = service_context.get("session_db")
                        if _sdb_clean is not None:
                            try:
                                await _sdb_clean.clear_bindings_for_provider(_pid)
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(
                                    "clear_bindings_for_provider_failed pid=%s err=%s",
                                    _pid, exc,
                                )
                        await ws.send_json({
                            "type": "settings_providers_removed",
                            "payload": {"id": _pid},
                        })
                        await _broadcast_providers_changed()

                elif msg_type == "settings_providers_reorder":
                    _ordered = _payload.get("ordered_ids") or []
                    try:
                        await _reg.reorder(list(_ordered))
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

            elif msg_type in ("code_session_set_provider", "code_session_set_model"):
                # P5-S2 multi-provider-management Phase 2:
                # Per-session override binding. set_provider rewrites the
                # provider_id (preserving preferred_model); set_model
                # rewrites preferred_model alone (preserving provider_id,
                # which may stay null = "still global chain"). Both
                # respond with the full resolved binding so the frontend
                # can update its session card without a separate fetch.
                _payload = raw.get("payload", {}) or {}
                _sid_target = _payload.get("session_id")
                _sdb_bind = service_context.get("session_db")
                if not _sid_target or _sdb_bind is None:
                    await ws.send_json({
                        "type": "settings_providers_error",
                        "payload": {
                            "reason": "missing_field",
                            "detail": "session_id required + session_db must be initialized",
                        },
                    })
                    continue

                current = await _sdb_bind.get_code_session_provider_binding(_sid_target)
                if msg_type == "code_session_set_provider":
                    new_pid = _payload.get("provider_id")  # may be None
                    new_model = current.get("preferred_model")
                    # provider-only change preserves existing model_params.
                    new_params = current.get("model_params")
                    out_type = "code_session_provider_set"
                else:
                    new_pid = current.get("provider_id")  # preserve
                    new_model = _payload.get("model")  # may be None
                    # code-session-model-params: Cursor picker sends
                    # `params`; legacy `{session_id,model}` (no `params`
                    # key) ⇒ provider defaults (None), per spec
                    # "Back-compat IPC". Must be a dict or None.
                    _raw_params = _payload.get("params")
                    new_params = _raw_params if isinstance(_raw_params, dict) else None
                    out_type = "code_session_model_set"

                try:
                    await _sdb_bind.set_code_session_provider_binding(
                        _sid_target, new_pid, new_model, new_params,
                    )
                except Exception as exc:  # noqa: BLE001
                    await ws.send_json({
                        "type": "settings_providers_error",
                        "payload": {"reason": "internal_error", "detail": str(exc)},
                    })
                else:
                    await ws.send_json({
                        "type": out_type,
                        "payload": {
                            "session_id": _sid_target,
                            "provider_id": new_pid,
                            "preferred_model": new_model,
                            "model_params": new_params,
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
                # P4-S23: payload may carry an explicit `session_id` —
                # the new code-panel can talk to multiple code sessions
                # over one shared WS, so we honor whatever the client
                # asks for and fall back to the WS-level session_id
                # (which is "default" for the pet's chat).
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
                if _scope_decision.created:
                    _sdb_for_session = service_context.get("session_db")
                    if _sdb_for_session is not None:
                        _ensure_session = getattr(_sdb_for_session, "ensure_session", None)
                        if callable(_ensure_session):
                            try:
                                await _ensure_session(
                                    _msg_sid,
                                    {
                                        "origin": "chat_task",
                                        "base_session_id": _base_msg_sid,
                                        "reason": _scope_decision.reason,
                                    },
                                )
                            except Exception as _ens_exc:  # noqa: BLE001
                                logger.warning(
                                    "session_ensure_failed sid=%s err=%s",
                                    _msg_sid,
                                    _ens_exc,
                                )
                    _switch_payload = {
                        "old_sid": _base_msg_sid,
                        "new_sid": _msg_sid,
                        "reason": _scope_decision.reason,
                    }
                    for _evt_type in ("session_switched", "task_session_started"):
                        _switch_evt = {"type": _evt_type, "payload": dict(_switch_payload)}
                        try:
                            await ws.send_json(_switch_evt)
                        except Exception:
                            pass
                        await _broadcast_default_chat_peers(ws, _switch_evt)
                    await _send_new_session_origin_user_echo(ws, _msg_sid, text)
                # 空消息守护：输入框为空时点「新话题」会发一条 text="" 的 chat_v2
                # （new_session=true）。会话切换/新建事件已在上方广播，这里**不再**跑
                # AgentLoop —— 否则空消息进预分析被判 ambiguous → 桌宠反问"你想说什么"
                # （UX 毛刺）。非 new_session 的空消息同样直接忽略（前端本就不该发）。
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

                # P4-S22: auto-suggest Code mode when user looks like
                # they're starting a project, and code mode isn't on yet.
                # We send a one-shot banner — the actual chat still runs
                # below as a normal companion turn (we don't hijack).
                try:
                    from deskpet.code_mode import maybe_suggest_code_mode
                    cmm = service_context.get("code_mode")
                    in_code = bool(cmm and cmm.is_enabled(_msg_sid))
                    if not in_code and maybe_suggest_code_mode(text):
                        await ws.send_json({
                            "type": "code_mode_suggest",
                            "payload": {
                                "trigger_text": text[:120],
                                "reason": "detected project intent",
                                "session_id": _msg_sid,
                            },
                        })
                except Exception as _exc:  # noqa: BLE001
                    logger.debug("code_mode_suggest_failed", error=str(_exc))

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

                async def _run_chat(
                    _ws,
                    _text,
                    _sid,
                    _memory_policy_override=None,
                    _task_scope_explicit_new=False,
                    _user_attachment_blocks=(),
                ):
                    # P4-S20-LLM-Unified-fix: 持久化用户消息到 SessionDB
                    # 并入向量库。老 chat 路径靠 SimpleLLMAgent.chat_stream
                    # 内部写入；新路径直接调 AgentLoop 绕过了 SimpleLLMAgent，
                    # 这里必须显式补回，否则下次召回什么都没。
                    _mm = service_context.get("memory_manager")
                    _vw = service_context.get("vector_worker")
                    _sdb = service_context.get("session_db")
                    _user_msg_id: int | None = None
                    # P6 bugfix 2026-05-13 (UI visible bug): 不要把 supervisor /
                    # auto-resume 用的 sentinel 文本 (`<<auto_resume>>`,
                    # `<<supervisor_followup>>`) 当用户消息写进 SessionDB —— 否则
                    # code panel 会把它们渲染成 "You: <<auto_resume>>" 让用户困惑。
                    # 这些 sentinel 只是触发 token，真正的 hint 走另外的 system msg
                    # 注入路径，pop_hint() 会把上下文喂给 LLM。
                    _is_sentinel = (_text or "").startswith("<<") and (_text or "").endswith(">>")
                    if _sdb is not None and not _is_sentinel:
                        try:
                            _user_msg_id = await _sdb.append_message(
                                session_id=_sid, role="user", content=_text,
                            )
                            if _vw is not None and _user_msg_id is not None:
                                await _vw.enqueue(_user_msg_id, _text)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "chat_persist_user_failed", error=str(exc),
                            )

                    # 2026-05-28: 多窗口同步 user echo —— 把用户键入的消息
                    # 广播给同 "default" 会话的其他控制通道 (主桌宠 + 消息
                    # 面板互相同步)。originating WS 已经本地 push_message
                    # 过了，所以跳过 originator 避免重复。
                    if not _is_sentinel:
                        _user_echo_evt = {
                            "type": "chat_v2_user_echo",
                            "payload": {"session_id": _sid, "text": _text},
                        }
                        await _broadcast_default_chat_peers(_ws, _user_echo_evt)

                    # OpenSpec 2026-05-16 §D2 — capability gate（防漂移）。
                    # 在进 ContextAssembler / agent loop 前拦下"明显需要
                    # deskpet 没有的能力"的请求（图像/视频/语音/作曲/3D
                    # 生成），直接 graceful refuse + 替代建议，杜绝"无法
                    # 完成 → 漂移到记忆里的旧项目"（2026-05-16 实测 bug：
                    # default session 说"生成海报图片" → agent 建了 17 个
                    # VPN CLI 文件）。这不是沙箱/弹窗，是诚实直接答复。
                    # sentinel（<<auto_resume>> 等）不过门；available_tools
                    # 实时从 ToolRegistry 取，新增图像工具自动放行。
                    if not _is_sentinel:
                        try:
                            _cap_cfg = (config.raw.get("companion") or {})
                            _cap_enabled = bool(
                                _cap_cfg.get("capability_gate_enabled", True)
                            )
                            from agent.capability_gate import (
                                classify_request as _cap_classify,
                            )
                            _avail_tools = (
                                deskpet_tool_registry_v2.list_tools()
                                if deskpet_tool_registry_v2 is not None
                                else []
                            )
                            # 歧义兜底用的 haiku-class LLM：复用统一
                            # provider 包一层 tool_use_shim（与 agent
                            # loop 同 endpoint）。构造失败 → None →
                            # 跳过兜底默认放行（不冤枉正常请求）。
                            _gate_llm = None
                            try:
                                from agent.tool_use_shim import (
                                    OpenAICompatibleAgentLLM as _GateShim,
                                )
                                _gate_provider = local_llm or cloud_llm
                                if _gate_provider is not None:
                                    _gate_llm = _GateShim(provider=_gate_provider)
                            except Exception:  # noqa: BLE001
                                _gate_llm = None
                            _verdict = await _cap_classify(
                                _text or "",
                                available_tools=_avail_tools,
                                enabled=_cap_enabled,
                                llm_registry=_gate_llm,
                            )
                            from agent.capability_gate import Verdict as _Verdict
                            if _verdict.verdict is _Verdict.REFUSE:
                                _refuse_text = _verdict.render_text()
                                logger.info(
                                    "capability_gate_refused sid=%s text=%s",
                                    _sid, (_text or "")[:120],
                                )
                                # 直接当一轮 assistant 回复落库 + 推前端，
                                # 不进 agent loop。
                                if _sdb is not None:
                                    try:
                                        _ref_id = await _sdb.append_message(
                                            session_id=_sid,
                                            role="assistant",
                                            content=_refuse_text,
                                        )
                                        if _vw is not None and _ref_id is not None:
                                            await _vw.enqueue(_ref_id, _refuse_text)
                                    except Exception as _ex:  # noqa: BLE001
                                        logger.warning(
                                            "capability_gate_persist_failed error=%s",
                                            _ex,
                                        )
                                _refuse_final = {
                                    "type": "chat_v2_final",
                                    "payload": {
                                        "text": _refuse_text,
                                        "iterations": 0,
                                        "session_id": _sid,
                                    },
                                }
                                await _ws.send_json(_refuse_final)
                                await _broadcast_default_chat_peers(_ws, _refuse_final)
                                # capability-gate path uses local_llm or cloud_llm
                                # (no resolved chain); usage is best-effort.
                                await _emit_context_usage(
                                    _ws,
                                    _sid,
                                    provider_chain=None,
                                    fallback_provider=_gate_provider or local_llm,
                                )
                                return
                        except Exception as _cap_exc:  # noqa: BLE001
                            # 能力门永远不能阻断正常对话——异常即放行。
                            logger.debug(
                                "capability_gate_skipped error=%s", _cap_exc
                            )

                    _turn_preparer = ProductTurnPreparer()
                    _code_mode_for_turn = service_context.get("code_mode")
                    _turn_in_code_mode = bool(
                        _code_mode_for_turn
                        and _code_mode_for_turn.is_enabled(_sid)
                    )
                    _turn_workspace_ref = None
                    if _turn_in_code_mode:
                        _turn_state = _code_mode_for_turn.get(_sid)
                        if _turn_state and _turn_state.project_root:
                            _turn_workspace_ref = str(_turn_state.project_root)
                    import uuid as _uuid_turn

                    _turn_request_id = _uuid_turn.uuid4().hex
                    _turn_input = TurnInput(
                        text=_text,
                        session_id=_sid,
                        request_id=_turn_request_id,
                        turn_id=(str(_user_msg_id) if _user_msg_id is not None else None),
                        venue=("code" if _turn_in_code_mode else "text"),
                        mode=("code" if _turn_in_code_mode else "companion"),
                        memory_policy=_memory_policy_override,
                        explicit_new=bool(_task_scope_explicit_new),
                        attachment_blocks=tuple(_user_attachment_blocks),
                        provider_ref=str(getattr(local_llm, "model", "") or "") or None,
                        capability_ref=(
                            f"catalog:{deskpet_tool_registry_v2.catalog_snapshot().revision}"
                        ),
                        workspace_ref=_turn_workspace_ref,
                    )
                    _prepared_turn = await _turn_preparer.prepare_context(
                        _turn_input,
                        services=service_context,
                        config=config,
                        local_llm=local_llm,
                        tool_registry=deskpet_tool_registry_v2,
                        current_message_id=_user_msg_id,
                        summary_user_is_confused=_sql_user_is_confused,
                        summary_latest_task_snapshot=_sql_latest_task_snapshot,
                        summary_build_reinject_msg=_sql_build_reinject_msg,
                    )
                    _bundle = _prepared_turn.bundle
                    _assembler = _prepared_turn.assembler
                    _msgs = _prepared_turn.messages
                    final_text = ""
                    # P4-S24: capture the LAST assistant turn's
                    # reasoning_content so we persist it alongside
                    # final_text. Multi-iteration runs (tool calls)
                    # may produce reasoning_content per iteration —
                    # only the terminal one matters for round-trip,
                    # since intermediate turns are already complete
                    # in working_messages by the time agent_loop ends.
                    final_reasoning = ""
                    try:
                        from agent.agent_loop import AgentLoop as _AgentLoop
                        from agent.tool_use_shim import OpenAICompatibleAgentLLM as _Shim
                        # P4-S20-LLM-Unified: 单一 endpoint。local_llm 来自
                        # 统一 [llm] 段（base_url + api_key + model）；不管你
                        # 把它指向 Ollama 还是任何 OpenAI 兼容云端都一样。
                        _provider = _select_code_workflow_provider(
                            local_llm=local_llm,
                            cloud_llm=cloud_llm,
                        )
                        _shim = _Shim(provider=_provider)
                        # P4-S22: Code mode bumps max_iterations to 50 so
                        # long tool-use chains (read → grep → edit → bash
                        # → repeat) can finish a real task.
                        # 2026-05-16: companion 8 → 16。实测"找桌面的
                        # 花名册并生成 Excel"这类正当多步任务（搜目录→
                        # 定位文件→读→生成→写）8 轮就爆，过早触发
                        # auto_resume。16 给真实任务留余量，仍远小于
                        # code 的 50，纯闲聊跑满 16 仍会被 supervisor 接住。
                        _cmm = service_context.get("code_mode")
                        _in_code_mode = bool(_cmm and _cmm.is_enabled(_sid))
                        _max_iter = 50 if _in_code_mode else 16
                        from deskpet.context_os_e2e_hooks import (
                            trusted_context_os_e2e_case as _trusted_context_os_e2e_case,
                        )
                        _e2e_max_iter_raw = os.environ.get(
                            "DESKPET_CONTEXT_OS_E2E_MAX_ITERATIONS", ""
                        ).strip()
                        if not _trusted_context_os_e2e_case(
                            allowed_cases=("E2E-CTX-10",), session_id=_sid
                        ):
                            _e2e_max_iter_raw = ""
                        if _e2e_max_iter_raw:
                            _e2e_max_iter = int(_e2e_max_iter_raw)
                            if not 1 <= _e2e_max_iter <= 100:
                                raise ValueError(
                                    "DESKPET_CONTEXT_OS_E2E_MAX_ITERATIONS must be in [1, 100]"
                                )
                            _max_iter = _e2e_max_iter
                            logger.info(
                                "context_os_e2e_max_iterations_override",
                                max_iterations=_max_iter,
                                session_id=_sid,
                            )

                        # Durable Complex Code v1 ingress. Classification happens
                        # before plan extraction and AgentLoop construction so a
                        # write/action turn has one owner. WorkflowLauncher retains
                        # the graph task after this chat turn returns.
                        if (
                            _in_code_mode
                            and not _is_sentinel
                            and config.workflows.enabled
                            and config.workflows.code_complex
                        ):
                            from deskpet.workflows.adapters.code_runtime import (
                                ProposalPort as _CodeProposalPort,
                                ToolDispatchPort as _CodeToolDispatchPort,
                                capability_snapshot as _code_capability_snapshot,
                                workflow_session_ref as _code_workflow_session_ref,
                            )
                            from deskpet.workflows.contracts import WorkflowContext as _WorkflowContext
                            from deskpet.workflows.definitions.v1 import (
                                code_complex_initial_state as _code_initial_state,
                            )
                            from deskpet.workflows.routing import (
                                WorkflowRoute as _WorkflowRoute,
                                route_task as _route_workflow_task,
                            )

                            _route_decision = _route_workflow_task(
                                _text or "",
                                workspace_context=True,
                            )
                            if _route_decision.route is _WorkflowRoute.CODE_COMPLEX:
                                _workflow_service = service_context.get("workflow_service")
                                _workflow_launcher = getattr(
                                    _workflow_service, "launcher", None
                                )
                                if _workflow_launcher is not None:
                                    _accepted = None
                                    _held_session_locks = []
                                    try:
                                        import hashlib as _hashlib

                                        _code_sid = str(_cmm.code_session_id(_sid) or "")
                                        _project_root = _cmm.project_root(_sid)
                                        if not _code_sid or _project_root is None:
                                            raise RuntimeError(
                                                "code workflow session binding is incomplete"
                                            )
                                        for _lock_sid in sorted({_sid, _code_sid}):
                                            _lock = _workflow_service.session_lock(_lock_sid)
                                            await _lock.acquire()
                                            _held_session_locks.append(_lock)
                                        _base_epoch = 0
                                        _code_epoch = 0
                                        if _sdb is not None:
                                            _base_delivery = await _sdb.get_session_delivery_state(
                                                _sid
                                            )
                                            _code_delivery = await _sdb.get_session_delivery_state(
                                                _code_sid
                                            )
                                            _base_epoch = int(_base_delivery.get("epoch", 0))
                                            _code_epoch = int(_code_delivery.get("epoch", 0))
                                            if (
                                                _base_delivery.get("deleted_at") is not None
                                                or _code_delivery.get("deleted_at") is not None
                                            ):
                                                raise RuntimeError(
                                                    "code workflow session was deleted"
                                                )
                                        _session_ref = _code_workflow_session_ref(
                                            base_session_id=_sid,
                                            code_session_id=_code_sid,
                                            delivery_session_id=_sid,
                                            project_root=_project_root,
                                            base_epoch=_base_epoch,
                                            code_epoch=_code_epoch,
                                        )
                                        deskpet_tool_registry_v2.set_session_context(
                                            _sid,
                                            {
                                                "_project_root": str(_project_root),
                                                "_write_scope_root": str(_project_root),
                                                "_session_id": _sid,
                                            },
                                        )
                                        _capabilities = _code_capability_snapshot(
                                            deskpet_tool_registry_v2
                                        )
                                        _capability_payload = [
                                            item.to_dict() for item in _capabilities
                                        ]
                                        # Resolve the same per-session provider/keychain
                                        # binding used by AgentLoop.  The durable graph
                                        # starts before the legacy chain-resolution block
                                        # below, so reusing module-level local/cloud objects
                                        # here can retain an expired API key after login.
                                        _code_provider = _provider
                                        _code_registry = service_context.get(
                                            "provider_registry"
                                        )
                                        if _code_registry is not None and _sdb is not None:
                                            from llm.resolution import (
                                                resolve_provider_for_session as _resolve_code_chain,
                                            )

                                            _agent_cfg = (
                                                config.raw.get("agent")
                                                if hasattr(config, "raw")
                                                else None
                                            ) or {}
                                            _code_default_model = (
                                                str(_agent_cfg.get("code_model") or "").strip()
                                                or None
                                            )
                                            _code_entries = await _resolve_code_chain(
                                                _sid,
                                                is_code_session=True,
                                                registry=_code_registry,
                                                session_db=_sdb,
                                                code_default_model=_code_default_model,
                                            )
                                            if _code_entries:
                                                _code_providers = (
                                                    _build_code_workflow_provider_chain(
                                                        entries=_code_entries,
                                                        registry=_code_registry,
                                                    )
                                                )
                                                from agent.tool_use_shim import (
                                                    OpenAICompatibleAgentLLMChain as _CodeChainShim,
                                                )

                                                _code_shim = _CodeChainShim(_code_providers)
                                                _code_provider = _code_providers[0]
                                            else:
                                                _code_shim = _Shim(provider=_code_provider)
                                        else:
                                            _code_shim = _Shim(provider=_code_provider)
                                        _fallback_turn_key = _hashlib.sha256(
                                            f"{_sid}\0{_text}".encode("utf-8")
                                        ).hexdigest()[:24]
                                        _turn_key = str(
                                            _user_msg_id or _fallback_turn_key
                                        )
                                        _request_id = f"chat:{_sid}:{_turn_key}"
                                        _turn_id = f"turn:{_sid}:{_turn_key}"
                                        _provider_name = str(
                                            getattr(_code_provider, "name", "")
                                            or "openai_compatible"
                                        )
                                        _model_name = str(
                                            getattr(_code_provider, "model", "") or ""
                                        )
                                        _code_context_os = bool(
                                            getattr(config.features, "context_os_v1", False)
                                        )
                                        _code_prepared_context = None
                                        _code_eligibility = None
                                        if _code_context_os:
                                            from llm.model_info import resolve as _resolve_code_model_info
                                            from deskpet.tools.capabilities import (
                                                ToolEligibilityContext as _CodeEligibility,
                                                ToolExecutionContext as _CodeExecutionContext,
                                                ToolExposureIntent as _CodeToolIntent,
                                            )

                                            _code_planner = service_context.get(
                                                "context_request_planner"
                                            )
                                            _code_scope_store = service_context.get(
                                                "tool_capability_scope_store"
                                            )
                                            if _code_planner is None or _code_scope_store is None:
                                                raise RuntimeError(
                                                    "context_os_code_workflow_runtime_unavailable"
                                                )
                                            _code_eligibility = _CodeEligibility(
                                                session_id=_sid,
                                                request_id=_request_id,
                                                task_type="code",
                                                mode="code_workflow",
                                            )
                                            _bundle.tool_exposure_intent = _CodeToolIntent(
                                                direct_selectors=tuple(
                                                    str(item.get("tool_name") or "")
                                                    for item in _capability_payload
                                                    if item.get("tool_name")
                                                )
                                            )
                                            _code_model_info = _resolve_code_model_info(
                                                _model_name or "_default"
                                            )
                                            _code_active_snapshot = (
                                                await _project_initial_context_snapshot(
                                                    session_id=_sid,
                                                    request_id=_request_id,
                                                    user_text=str(_text or ""),
                                                    explicit_new=True,
                                                )
                                            )
                                            _attach_task_snapshot_to_request(
                                                _bundle,
                                                _msgs,
                                                _code_active_snapshot,
                                            )
                                            from deskpet.agent.attachment_budget import (
                                                collect_attachment_budget as _collect_code_attachments,
                                            )
                                            (
                                                _code_attachment_refs,
                                                _code_attachment_tokens,
                                            ) = _collect_code_attachments(_msgs)
                                            _code_planned = await _code_planner.prepare_initial(
                                                _bundle,
                                                base_system="",
                                                history=_bundle.history,
                                                user_message=str(_text or ""),
                                                eligibility=_code_eligibility,
                                                context_window=_code_model_info.context_window,
                                                effective_pct=_code_model_info.effective_pct,
                                                generation_reserve=min(
                                                    8192,
                                                    max(
                                                        512,
                                                        int(_code_model_info.context_window) // 8,
                                                    ),
                                                ),
                                                 active_snapshot=_code_active_snapshot,
                                                 prebuilt_messages=list(_msgs),
                                                 current_message_id=_user_msg_id,
                                                 attachment_refs=_code_attachment_refs,
                                                 attachment_tokens=_code_attachment_tokens,
                                             )
                                            _code_prepared_context = (
                                                _code_planned.prepared_context
                                            )
                                            _code_scope_store.open(
                                                _code_prepared_context.tool_set,
                                                _code_eligibility,
                                                snapshot_handle=(
                                                    _code_prepared_context.active_snapshot_handle
                                                ),
                                            )
                                            _msgs = _code_prepared_context.messages
                                        _start_payload = {
                                            "request": str(_text or ""),
                                            "session_ref": _session_ref.to_dict(),
                                            "capability_snapshot": _capability_payload,
                                            "messages": [dict(message) for message in _msgs],
                                            "plan_steps": [str(_text or "")],
                                            "approval_required": bool(
                                                getattr(
                                                    config.features,
                                                    "plan_confirm_gate",
                                                    True,
                                                )
                                            ),
                                            "started_at": time.time(),
                                            "request_id": _request_id,
                                            "turn_id": _turn_id,
                                            "provider_snapshot": {
                                                "provider": _provider_name
                                            },
                                            "model_snapshot": {"model": _model_name},
                                        }

                                        def _code_state_factory(**values):
                                            return _code_initial_state(
                                                request=str(values["request"]),
                                                run_id=str(values["run_id"]),
                                                thread_id=str(values["thread_id"]),
                                                session_id=str(values["session_id"]),
                                                session_ref=dict(values["session_ref"]),
                                                capability_snapshot=list(
                                                    values["capability_snapshot"]
                                                ),
                                                messages=list(values["messages"]),
                                                plan_steps=list(values["plan_steps"]),
                                                approval_required=bool(
                                                    values["approval_required"]
                                                ),
                                                started_at=float(values["started_at"]),
                                                request_id=str(values["request_id"]),
                                                turn_id=str(values["turn_id"]),
                                                provider_snapshot=dict(
                                                    values["provider_snapshot"]
                                                ),
                                                model_snapshot=dict(
                                                    values["model_snapshot"]
                                                ),
                                            )

                                        def _code_context_factory():
                                            return _WorkflowContext(
                                                ports={
                                                    "llm": _CodeProposalPort(
                                                        _code_shim,
                                                        deskpet_tool_registry_v2,
                                                        session_id=_sid,
                                                        provider_name=_provider_name,
                                                        model_name=_model_name,
                                                        context_os_v1=_code_context_os,
                                                        prepared_tool_set=(
                                                            _code_prepared_context.tool_set
                                                            if _code_prepared_context is not None
                                                            else None
                                                        ),
                                                        eligibility=_code_eligibility,
                                                    ),
                                                    "tool": _CodeToolDispatchPort(
                                                        deskpet_tool_registry_v2,
                                                        session_id=_sid,
                                                        execution_context=(
                                                            _CodeExecutionContext(
                                                                _code_prepared_context.tool_set.scope_id,
                                                                _sid,
                                                                _request_id,
                                                            )
                                                            if _code_prepared_context is not None
                                                            else None
                                                        ),
                                                    ),
                                                },
                                                request_id=_request_id,
                                                turn_id=_turn_id,
                                            )

                                        _accepted = await _workflow_launcher.launch(
                                            workflow_name="code_complex",
                                            workflow_version="v1",
                                            session_id=_sid,
                                            code_session_id=_code_sid,
                                            request_id=_request_id,
                                            turn_id=_turn_id,
                                            start_payload=_start_payload,
                                            capability_snapshot={
                                                "schema_version": 1,
                                                "tools": _capability_payload,
                                            },
                                            state_factory=_code_state_factory,
                                            context_factory=_code_context_factory,
                                            base_epoch=_base_epoch,
                                            code_epoch=_code_epoch,
                                        )
                                        _accepted_final = {
                                            "type": "chat_v2_final",
                                            "payload": {
                                                "text": "Code workflow started.",
                                                "iterations": 0,
                                                "session_id": _sid,
                                                "accepted": True,
                                                "completion_semantics": "accepted_async",
                                                "run_id": _accepted["run_id"],
                                            },
                                        }
                                        await _ws.send_json(_accepted_final)
                                        await _broadcast_default_chat_peers(
                                            _ws, _accepted_final
                                        )
                                        logger.info(
                                            "code_workflow_accepted sid=%s run_id=%s reason=%s",
                                            _sid,
                                            _accepted["run_id"],
                                            _route_decision.reason,
                                        )
                                        return
                                    except Exception as _workflow_exc:  # noqa: BLE001
                                        if _accepted is not None:
                                            logger.warning(
                                                "code_workflow_accept_frame_failed sid=%s run_id=%s err=%s",
                                                _sid,
                                                _accepted.get("run_id", ""),
                                                _workflow_exc,
                                            )
                                            return
                                        logger.warning(
                                            "code_workflow_start_failed sid=%s err=%s",
                                            _sid,
                                            _workflow_exc,
                                        )
                                    finally:
                                        for _lock in reversed(_held_session_locks):
                                            if _lock.locked():
                                                _lock.release()

                        # Strong DeepResearch intent has deterministic ingress.
                        # Do not ask the outer ReAct model whether to call the
                        # durable tool: that can bypass progress/checkpoint/Trace
                        # or answer from pretraining without evidence.
                        if (
                            not _in_code_mode
                            and not _is_sentinel
                            and config.workflows.enabled
                            and config.workflows.deep_research
                        ):
                            from deskpet.agent.assembler.components.tool import (
                                is_deep_research_request as _is_deep_research_request,
                            )

                            if _is_deep_research_request(_text or ""):
                                import hashlib as _hashlib
                                import json as _json
                                from deskpet.tools.research_tools import (
                                    _handle_deepresearch as _start_deepresearch,
                                )

                                _fallback_turn_key = _hashlib.sha256(
                                    f"{_sid}\0{_text}".encode("utf-8")
                                ).hexdigest()[:24]
                                _turn_key = str(_user_msg_id or _fallback_turn_key)
                                _request_id = f"chat:{_sid}:{_turn_key}"
                                _turn_id = f"turn:{_sid}:{_turn_key}"
                                _started_raw = await _start_deepresearch(
                                    {
                                        "topic": str(_text or ""),
                                        "user_request": str(_text or ""),
                                        "depth": (
                                            "deep"
                                            if any(
                                                token in str(_text or "")
                                                for token in ("完整报告", "全面", "深入")
                                            )
                                            else "standard"
                                        ),
                                        "_session_id": _sid,
                                        "_request_id": _request_id,
                                        "_turn_id": _turn_id,
                                    },
                                    _request_id,
                                )
                                _started = _json.loads(_started_raw)
                                if not _started.get("accepted"):
                                    raise RuntimeError(
                                        str(_started.get("error") or "DeepResearch start failed")
                                    )
                                _handoff_final = {
                                    "type": "chat_v2_final",
                                    "payload": {
                                        "text": "",
                                        "iterations": 0,
                                        "session_id": _sid,
                                        "handoff_run_id": _started["run_id"],
                                    },
                                }
                                await _ws.send_json(_handoff_final)
                                await _broadcast_default_chat_peers(
                                    _ws, _handoff_final
                                )
                                logger.info(
                                    "deepresearch_workflow_accepted sid=%s run_id=%s",
                                    _sid,
                                    _started["run_id"],
                                )
                                return

                        # P5-S2 D2: long tool_call args reliability hint.
                        # Models (deepseek-v4-pro etc.) corrupt JSON escapes
                        # in args > ~3000 chars about 30-40% of the time when
                        # streaming — observed parse_ok=False on 7552/7594/7661
                        # char write_file args. Guidance prepended to message
                        # stack so the model prefers small multi-call writes
                        # over a single huge one.
                        if _in_code_mode:
                            _long_args_hint = {
                                "role": "system",
                                "content": (
                                    "[Tool reliability guidance]\n"
                                    "When calling `write_file` (or any tool with a long string arg):\n"
                                    "1. Keep each call's `content` ≤ 2000 chars. Long strings (>3KB) "
                                    "are unreliable due to JSON escape errors in streaming output — "
                                    "the call will be rejected as malformed.\n"
                                    "2. To write a longer file: call write_file with `mode=\"write\"` "
                                    "for the first chunk, then call again with `mode=\"append\"` for "
                                    "each subsequent chunk. Two-three medium calls beat one giant call.\n"
                                    "3. If unsure of length, break early — over-splitting is free, "
                                    "but a corrupted single call wastes the whole turn."
                                ),
                            }
                            _insert_at = 0
                            while _insert_at < len(_msgs) and _msgs[_insert_at].get("role") == "system":
                                _insert_at += 1
                            _msgs.insert(_insert_at, _long_args_hint)

                        # P6 Phase 6 — ContextManager is always constructed
                        # here. The pre-call history compaction below runs
                        # AFTER ``_provider_chain`` resolves (it needs the
                        # chain's first provider as the summarize source).
                        # The legacy ``if _ctx_mgr is None:`` inline B2
                        # block (~60 lines re-implementing what
                        # chat_prep.prepare_chat_messages_for_chain does
                        # cleanly) was removed in Phase 6.
                        # Phase 1.1.4 — per-model context map wiring. Resolve
                        # this session's ModelContextInfo via the 3-layer
                        # chain (builtin ← %APPDATA% global ← project
                        # .deskpet/context.toml). Code mode passes its
                        # project_root so the project layer applies; non-code
                        # mode passes None (only builtin + global). The
                        # [context.manager].v2_enabled knob is the
                        # Strangler-Fig rollback闸 — false 退回 2026-05-15
                        # stop-gap 绝对值 ContextConfig，忽略 per-model map。
                        from agent.context_manager import ContextManager as _CtxMgr
                        _ctx_v2_enabled = bool(
                            ((config.raw.get("context") or {}).get("manager") or {})
                            .get("v2_enabled", True)
                        )
                        _ctx_model = (
                            "ctx-primary"
                            if _e2e_provider_base
                            else (getattr(_provider, "model", "") or "_default")
                        )
                        _ctx_proot = (
                            _cmm.project_root(_sid)
                            if (_in_code_mode and _cmm is not None)
                            else None
                        )
                        # WI-1B-3: adaptive_compact_pct flag(默认 OFF=BC)穿到
                        # ContextConfig；OFF 时 compact_at_tokens_for() 等价旧触发线。
                        _adaptive_cpct = bool(
                            getattr(
                                getattr(config, "features", None),
                                "adaptive_compact_pct",
                                False,
                            )
                        )
                        _ctx_mgr = _CtxMgr.for_session(
                            model=_ctx_model,
                            project_root=_ctx_proot,
                            v2_enabled=_ctx_v2_enabled,
                            adaptive_compact_pct=_adaptive_cpct,
                        )

                        # ─── P5-S2 Phase 3.15: provider_chain resolution ───
                        # If the LLMProviderRegistry is wired up (Phase 1+2)
                        # and we have a SessionDB, resolve a chain for THIS
                        # session (code → per-session binding may pin to one
                        # provider; companion → always global chain). Each
                        # ProviderEntry becomes a fresh OpenAICompatibleProvider
                        # instance with its own api_key from keychain. Empty
                        # chain leaves _provider_chain=None so AgentLoop falls
                        # through to its legacy single-provider path.
                        _provider_chain: list[Any] | None = None
                        try:
                            _registry = service_context.get("provider_registry")
                            if _registry is not None and _sdb is not None:
                                from llm.resolution import resolve_provider_for_session as _resolve_chain
                                # code-session-model-params: code-mode
                                # default model (Strangler-Fig — empty/
                                # absent ⇒ None ⇒ legacy shared model;
                                # pet/companion never pass this, untouched).
                                _agent_cfg = (config.raw.get("agent") if hasattr(config, "raw") else None) or {}
                                _code_default_model = (
                                    (str(_agent_cfg.get("code_model") or "").strip() or None)
                                    if _in_code_mode
                                    else None
                                )
                                _entries = await _resolve_chain(
                                    _sid,
                                    is_code_session=_in_code_mode,
                                    registry=_registry,
                                    session_db=_sdb,
                                    code_default_model=_code_default_model,
                                )
                                if _entries:
                                    _chain: list[Any] = []
                                    for _entry in _entries:
                                        _api_key = _registry.resolve_api_key(_entry.id) or "ollama"
                                        _chain.append(OpenAICompatibleProvider(
                                            base_url=_entry.base_url,
                                            api_key=_api_key,
                                            model=_entry.model,
                                            temperature=getattr(_entry, "temperature", 0.7),
                                            sanitize_inline_cot_dsml=_sanitize_cot_dsml,
                                            code_params=getattr(_entry, "code_params", None),
                                            is_relay=(_entry.source == "relay"),
                                        ))
                                    _provider_chain = _chain
                                logger.info(
                                    "p5s2_chain_resolved sid=%s in_code_mode=%s "
                                    "code_default_model=%s n_entries=%d "
                                    "models=%s code_params=%s",
                                    _sid, _in_code_mode, _code_default_model,
                                    len(_entries or []),
                                    [getattr(e, "model", "?") for e in (_entries or [])],
                                    [getattr(e, "code_params", {}) for e in (_entries or [])],
                                )
                        except Exception as _resolve_exc:  # noqa: BLE001
                            logger.warning(
                                "p5s2_provider_chain_resolve_failed sid=%s err=%s — falling back to legacy single-provider path",
                                _sid, str(_resolve_exc)[:200],
                            )
                            _provider_chain = None
                        if _e2e_provider_base:
                            # The isolated fixture must own every selected
                            # attempt, independent of persisted relay/session
                            # provider bindings in the signed-in UI.
                            # Keep all deterministic E2E candidates in the
                            # real AgentLoop chain so fallback and explicit
                            # compaction-model selection exercise production
                            # retry/resolution code rather than a fixture-only
                            # HTTP replay.
                            _provider_chain = [
                                OpenAICompatibleProvider(
                                    base_url=_e2e_provider_base,
                                    api_key="ctx-e2e-local-key",
                                    model=_model,
                                    temperature=config.llm.local.temperature,
                                    sanitize_inline_cot_dsml=_sanitize_cot_dsml,
                                )
                                for _model in (
                                    "ctx-primary",
                                    "ctx-fallback",
                                    "ctx-compact-fixture",
                                )
                            ]

                        # P6 Phase 6 — ContextManager-based preflight
                        # compaction. Uses ``_provider_chain[0]`` (resolved
                        # above) for the summarize step. Best-effort: any
                        # exception falls back to the original messages
                        # (better long context than a hard error mid-task).
                        _context_os_active = bool(
                            getattr(
                                getattr(config, "features", None),
                                "context_os_v1",
                                False,
                            )
                        )
                        if not _context_os_active:
                            try:
                                from agent.chat_prep import (
                                    prepare_chat_messages_for_chain as _prep_chat_msgs,
                                )
                                _orig_len = len(_msgs)
                                _msgs = await _prep_chat_msgs(
                                    _msgs,
                                    provider_chain=_provider_chain,
                                    ctx_mgr=_ctx_mgr,
                                    fallback_summarizer=local_llm,
                                )
                                if len(_msgs) != _orig_len:
                                    logger.info(
                                        "p6_history_compacted sid=%s orig=%d new=%d",
                                        _sid, _orig_len, len(_msgs),
                                    )
                            except Exception as _p6_exc:  # noqa: BLE001
                                logger.warning(
                                    "p6_chat_prep_failed sid=%s err=%s",
                                    _sid, str(_p6_exc)[:200],
                                )

                        # superpowers Layer 1B WI-A1 — 意图记忆 hint 注入。
                        # 匹配以往同类消息的意图(ask/task)，注入 system hint 引导
                        # persona：纯提问别动手 / 派活进工作流。决策1"记下来后续直接做"。
                        # 仅 code 模式 + pref_mem 存在 + 非 sentinel；命中才注入。
                        if _in_code_mode and not _is_sentinel:
                            _pref_im = service_context.get("preference_memory")
                            if _pref_im is not None:
                                try:
                                    _im = await _pref_im.match(_text, "intent")
                                    _im_hint = _build_intent_hint(
                                        _im.get("label") if _im is not None else ""
                                    )
                                    if _im_hint:
                                        _ins = 0
                                        while (_ins < len(_msgs)
                                               and _msgs[_ins].get("role") == "system"):
                                            _ins += 1
                                        _msgs.insert(_ins, {"role": "system",
                                                            "content": _im_hint})
                                        logger.info(
                                            "intent_memory_hint sid=%s label=%s score=%.3f",
                                            _sid, _im.get("label"),
                                            float(_im.get("score", 0.0)),
                                        )
                                except Exception as _im_e:  # noqa: BLE001
                                    logger.debug("intent_memory_match_failed: %s", _im_e)

                        # Inject project root into per-session tool-arg
                        # context so glob/grep can run without the LLM
                        # restating the path every call. Cleared when
                        # code mode exits via ``code_mode_exit`` IPC.
                        if _in_code_mode:
                            _proot = _cmm.project_root(_sid)
                            if _proot is not None:
                                deskpet_tool_registry_v2.set_session_context(
                                    _sid,
                                    {
                                        "_project_root": str(_proot),
                                        "_session_id": _sid,
                                    },
                                )
                        else:
                            # OpenSpec 2026-05-16 §D3 — companion session
                            # write-scope。陪伴 session 的写盘类工具 path
                            # 限定在 resolve(workspace_root) 内（默认
                            # <user_data_dir>/workspace）；越界 → 工具
                            # 返回引导文案让用户进 code 模式。这不是
                            # 沙箱（不拦读/命令/弹窗），是 session 类型
                            # 语义。``set_session_context`` 注入的
                            # ``_write_scope_root`` 会被 execute_tool 合并
                            # 进每次 tool 调用 params，写盘工具据此校验。
                            # write_scope_enforced=false → 不注入 + 清掉
                            # 残留 → 工具读不到该键 → 退回旧自由写盘。
                            try:
                                _comp_cfg = (config.raw.get("companion") or {})
                                _ws_enforced = bool(
                                    _comp_cfg.get("write_scope_enforced", True)
                                )
                                # OpenSpec 2026-05-16-async-image-gen:
                                # generate_image needs _session_id (route
                                # the completion back to this pet session)
                                # + _image_worker (submit target). Inject
                                # them ALWAYS via the same merge mechanism;
                                # _write_scope_root is added conditionally.
                                _sctx: dict[str, Any] = {
                                    "_session_id": _sid,
                                    "_image_worker": service_context.get(
                                        "image_worker"
                                    ),
                                }
                                if _ws_enforced:
                                    from agent.write_scope import (
                                        resolve_workspace_root as _resolve_ws,
                                    )
                                    _ws_root = _resolve_ws(
                                        configured=str(
                                            _comp_cfg.get("workspace_root", "")
                                        )
                                    )
                                    _sctx["_write_scope_root"] = str(_ws_root)
                                deskpet_tool_registry_v2.set_session_context(
                                    _sid, _sctx
                                )
                            except Exception as _ws_exc:  # noqa: BLE001
                                logger.debug(
                                    "companion_write_scope_skipped sid=%s err=%s",
                                    _sid, _ws_exc,
                                )

                        # Provider/context preflight may replace the message list after
                        # prepare-context; route/plan always continue from that exact list.
                        _prepared_turn.messages = _msgs
                        _run_presenter = build_legacy_run_presenter()
                        _product_domain_sink = LegacyProductDomainSink(
                            websocket=_ws,
                            services=service_context,
                            session_db=_sdb,
                            broadcast=_broadcast_default_chat_peers,
                            plan_waiters=_PLAN_CONFIRM_WAITERS,
                            tool_registry=deskpet_tool_registry_v2,
                        )
                        _routed_turn = await _turn_preparer.route_intent(
                            _prepared_turn,
                            services=service_context,
                        )
                        for _command in _routed_turn.commands:
                            if not await _run_presenter.present_domain(
                                _command, _product_domain_sink
                            ):
                                return
                        _pre = _routed_turn.pre_loop
                        _pipe_attack_order = _routed_turn.attack_order
                        _pipe_contra_descs = _routed_turn.contradiction_descs
                        _pipe_problem_type = _routed_turn.problem_type
                        if not _routed_turn.continue_turn:
                            return

                        _planned_turn = await _turn_preparer.plan_decision(
                            _routed_turn,
                            services=service_context,
                            config=config,
                            provider=_provider,
                            code_mode=_cmm,
                            in_code_mode=_in_code_mode,
                        )
                        for _command in _planned_turn.commands:
                            if not await _run_presenter.present_domain(
                                _command, _product_domain_sink
                            ):
                                return
                        # P5-S2 Hook A: completion guard probe.
                        #
                        # Maps base_session_id → code_session_id (via
                        # CodeModeManager) → reads incomplete code_todos
                        # from SessionDB. Returned to AgentLoop so it
                        # can rebound the LLM with a "you said done but
                        # X todos still pending" system message instead
                        # of finalizing prematurely.
                        #
                        # Companion-mode sessions (no code_session_id)
                        # short-circuit to []: no todos in scope, no
                        # rebound. Only Code-mode sessions get the
                        # completion check.
                        async def _completion_probe(_base_sid: str) -> list[dict]:
                            try:
                                cm_local = service_context.get("code_mode")
                                if cm_local is None:
                                    return []
                                code_sid = cm_local.code_session_id(_base_sid)
                                if not code_sid:
                                    return []
                                if _sdb is None:
                                    return []
                                rows = await _sdb.get_code_todos(code_sid)
                                return [
                                    r for r in rows
                                    if (r.get("status") or "").lower() not in ("completed", "cancelled")
                                ]
                            except Exception as _e:  # noqa: BLE001
                                logger.warning(
                                    "p5s2_completion_probe_lookup_failed sid=%s err=%s",
                                    _base_sid, str(_e)[:200],
                                )
                                return []

                        async def _code_todo_getter(_base_sid: str) -> list[dict]:
                            try:
                                cm_local = service_context.get("code_mode")
                                if cm_local is None:
                                    return []
                                code_sid = cm_local.code_session_id(_base_sid)
                                if not code_sid:
                                    return []
                                if _sdb is None:
                                    return []
                                return await _sdb.get_code_todos(code_sid)
                            except Exception as _e:  # noqa: BLE001
                                logger.warning(
                                    "wi4_todo_getter_lookup_failed sid=%s err=%s",
                                    _base_sid, str(_e)[:200],
                                )
                                return []

                        # P5-S2 Phase 6: pull supervisor-section knobs
                        # for the AgentLoop's in-loop death-loop
                        # suppression. Read fresh from config so a
                        # config reload (future) takes effect on the
                        # next chat task without restart.
                        _sig_repeat_thr = int(
                            ((config.raw.get("supervisor") or {}).get(
                                "tool_signature_repeat_threshold", 3
                            ))
                        )
                        # WI-T2.1 v3：build_agent 工厂接电 VerifyGate +
                        # ReceiptStore（修 last-mile P0-1，详 module 顶
                        # build_agent docstring）。
                        # Companion+Code v1 WI-B3: 拉 goal_mode 接电资产 (None when flag OFF)
                        _goal_store_for_agent = service_context.get("session_goal_store")
                        _goal_checker_for_agent = service_context.get("goal_checker")
                        # WI-4.0 compaction: None when flag off (BC)
                        _compressor_for_agent = service_context.get("context_compressor")
                        # FP-5 缺口 5b (2026-06-06)：取 skill_loader/skill_matcher 传
                        # build_agent → _AgentLoop 真做 WI-4.2 remount + 自动披露。
                        # flag OFF → matcher=None → 降级 desc-only（字节级 BC）。
                        _skill_loader_for_agent = service_context.get("skill_loader")
                        _skill_matcher_for_agent = service_context.get("skill_matcher")
                        # FP-5 缺口 2：WI-1.6 工具路径录制器（flag off → None → BC）
                        _tp_recorder_for_agent = service_context.get("tool_path_recorder")
                        # WI-OH-4：记忆 self-curation curator（flag off → None → BC）
                        _curator_for_agent = service_context.get("memory_curator")
                        _agent = build_agent(
                            config,
                            llm_registry=_shim,
                            tool_registry=deskpet_tool_registry_v2,
                            context_manager=_ctx_mgr,
                            receipt_store_getter=_get_receipt_store,
                            max_iterations=_max_iter,
                            completion_probe=_completion_probe,
                            code_todo_getter=_code_todo_getter,
                            max_completion_nudges=2,
                            signature_repeat_threshold=_sig_repeat_thr,
                            session_goal_store=_goal_store_for_agent,
                            goal_checker=_goal_checker_for_agent,
                            compressor=_compressor_for_agent,
                            skill_loader=_skill_loader_for_agent,
                            skill_matcher=_skill_matcher_for_agent,
                            tool_path_recorder=_tp_recorder_for_agent,
                            memory_curator=_curator_for_agent,
                            context_attempt_store=service_context.get(
                                "context_attempt_store"
                            ),
                            # ─── 七步流水线 IN-LOOP 闸（plans/2026-06-24-...）。flag off → 全 None/False = BC。
                            # self_check_gate 由 build_agent 内构造（B3）；convergence 由 AgentLoop 内自建。
                            evidence_gate=(service_context.get("pipeline_evidence_gate")
                                           if (_pre and not _pre.short_circuit) else None),
                            pipeline_problem_type=_pipe_problem_type,
                            pipeline_needs_investigation=bool(
                                _pre.intent.needs_investigation if (_pre and _pre.intent) else False
                            ),
                            pipeline_observability=bool(
                                getattr(getattr(config, "features", None), "problem_pipeline", None)
                                and config.features.problem_pipeline.observability_events
                            ),
                            convergence_report_on_stop=bool(
                                _pre and not _pre.short_circuit
                                and getattr(getattr(config, "features", None), "problem_pipeline", None)
                                and config.features.problem_pipeline.convergence_report_on_stop
                            ),
                        )
                        # P4-S25 A1: stream by default — gives the user
                        # instant visible feedback on thinking-mode
                        # models that otherwise stare back blank for 30+
                        # seconds. Caller-side `chat_v2_delta` event is
                        # accumulated by the frontend MessageStream.

                        # P5-S1: SessionActivity bumping. Every AgentEvent
                        # for a Code-mode session feeds the supervisor
                        # watchdog's view of the world (last_event_ts,
                        # recent_events ring buffer, tool signature window).
                        # Companion-mode sessions are not tracked.
                        _sa_store = service_context.get("session_activity") if _in_code_mode else None
                        if _sa_store is not None:
                            try:
                                await _sa_store.set_status(_sid, "running")
                            except Exception:
                                pass

                        # WI-A1: 本轮是否真调过工具 — 决定意图记忆记 task/ask。
                        _had_tool_call = False
                        _prepared_context = None
                        _context_request_id = None
                        if _context_os_active:
                            if _bundle is None:
                                raise RuntimeError(
                                    "context_os_v1 requires a successful ContextBundle"
                                )
                            _request_planner = service_context.get(
                                "context_request_planner"
                            )
                            _scope_store = service_context.get(
                                "tool_capability_scope_store"
                            )
                            if _request_planner is None or _scope_store is None:
                                raise RuntimeError(
                                    "tool_capability_runtime_unavailable"
                                )
                            from deskpet.tools.capabilities import (
                                ToolEligibilityContext as _ToolEligibilityContext,
                            )
                            _context_request_id = _turn_input.request_id
                            _eligibility = _ToolEligibilityContext(
                                session_id=_sid,
                                request_id=_context_request_id,
                                task_type=_bundle.task_type,
                                mode=("code" if _in_code_mode else "companion"),
                            )
                            _model_info = _ctx_mgr.config._resolved_model_info()
                            _active_snapshot = await _project_initial_context_snapshot(
                                session_id=_sid,
                                request_id=_context_request_id,
                                user_text=_text,
                                explicit_new=bool(_task_scope_explicit_new),
                            )
                            _attach_task_snapshot_to_request(
                                _bundle,
                                _msgs,
                                _active_snapshot,
                            )
                            from deskpet.agent.attachment_budget import (
                                collect_attachment_budget as _collect_chat_attachments,
                            )
                            (
                                _chat_attachment_refs,
                                _chat_attachment_tokens,
                            ) = _collect_chat_attachments(_msgs)
                            _planned_request = await _request_planner.prepare_initial(
                                _bundle,
                                base_system="",
                                history=_bundle.history,
                                user_message=_text,
                                eligibility=_eligibility,
                                context_window=_model_info.context_window,
                                effective_pct=_model_info.effective_pct,
                                generation_reserve=min(
                                    8192,
                                    max(512, int(_model_info.context_window) // 8),
                                ),
                                active_snapshot=_active_snapshot,
                                prebuilt_messages=_msgs,
                                current_message_id=_user_msg_id,
                                attachment_refs=_chat_attachment_refs,
                                attachment_tokens=_chat_attachment_tokens,
                            )
                            _prepared_context = _planned_request.prepared_context
                            _prepared_context.request_budget = _planned_request.budget
                            _msgs = _prepared_context.messages
                            _scope_store.open(
                                _prepared_context.tool_set,
                                _eligibility,
                                snapshot_handle=_prepared_context.active_snapshot_handle,
                            )
                        _bundle_tool_names = (
                            (
                                [
                                    cap.ref.name
                                    for cap in _prepared_context.tool_set.direct
                                ]
                                if _prepared_context is not None
                                else [
                                str(
                                    (
                                        schema.get("function", {})
                                        if isinstance(schema.get("function"), dict)
                                        else schema
                                    ).get("name", "")
                                )
                                for schema in _bundle.tool_schemas
                                if isinstance(schema, dict)
                                ]
                            )
                            if _bundle is not None
                            else None
                        )
                        from deskpet.agent.assembler.components.tool import (
                            is_short_contextual_followup as _is_short_contextual_followup,
                        )
                        _buffer_short_followup_stream = (
                            _is_short_contextual_followup(_text)
                            and "generate_image" not in (_bundle_tool_names or [])
                        )
                        _presentation_state = PresentationState(
                            final_text=final_text,
                            final_reasoning=final_reasoning,
                            had_tool_call=_had_tool_call,
                        )
                        _presentation_context = RunPresentationContext(
                            session_id=_sid,
                            text=_text,
                            websocket=_ws,
                            services=service_context,
                            config=config,
                            messages=_msgs,
                            session_db=_sdb,
                            vector_worker=_vw,
                            activity_store=_sa_store,
                            provider_chain=_provider_chain,
                            fallback_provider=local_llm,
                            request_id=_context_request_id,
                            max_iterations=_max_iter,
                            in_code_mode=_in_code_mode,
                            is_sentinel=_is_sentinel,
                            buffer_short_followup_stream=_buffer_short_followup_stream,
                            skill_candidate_waiters=_SKILL_CANDIDATE_WAITERS,
                            broadcast=_broadcast_default_chat_peers,
                            send_final=_send_chat_final,
                            emit_context_usage=_emit_context_usage,
                            codify_skill=_maybe_codify_skill,
                            intent_label_from_turn=_intent_label_from_turn,
                            assembler=_assembler,
                            bundle=_bundle,
                            billing_ledger=billing_ledger,
                            provider=_provider,
                        )
                        async for ev in _agent.run(
                            _msgs,
                            session_id=_sid,
                            stream=True,
                            provider_chain=_provider_chain,
                            loop_user_request=(None if _is_sentinel else _text),
                            is_sentinel_run=_is_sentinel,
                            tool_names_filter=(
                                None if _prepared_context is not None else _bundle_tool_names
                            ),
                            prepared_context=_prepared_context,
                            context_request_id=_context_request_id,
                        ):
                            await _run_presenter.present(
                                ev, _presentation_context, _presentation_state
                            )
                        final_text = _presentation_state.final_text
                        final_reasoning = _presentation_state.final_reasoning
                        _had_tool_call = _presentation_state.had_tool_call
                        await _run_presenter.finish_turn(
                            _presentation_context, _presentation_state
                        )
                        # P4-S24: assistant persistence moved INTO the
                        # FinalEvent handler above so a same-sid task
                        # cancellation (race when user fires the next
                        # message faster than the post-loop tail can
                        # finish) doesn't strand the assistant row.
                        # The `final_text and _sdb` guard there already
                        # mirrors what this block used to do.

                    except Exception as exc:  # noqa: BLE001
                        # P6 bugfix 2026-05-14 (live-test): ASGI ws-close
                        # races during long streams produce the noisy
                        # "Unexpected ASGI message 'websocket.send', after
                        # sending 'websocket.close'" RuntimeError. This is
                        # benign — the client just disconnected mid-stream
                        # (panel close, network blip, ws client timeout).
                        # Demote to debug + skip the chat_v2_error send (it
                        # would just trigger the same error again).
                        _msg = str(exc)
                        _is_ws_closed = (
                            isinstance(exc, RuntimeError)
                            and "websocket" in _msg.lower()
                            and ("close" in _msg.lower() or "completed" in _msg.lower())
                        )
                        if _is_ws_closed:
                            logger.debug(
                                "chat_v2_ws_closed_midstream sid=%s — client disconnected",
                                _sid,
                            )
                            return  # don't try to send chat_v2_error on closed ws
                        logger.warning(
                            "chat_v2_failed",
                            error=str(exc),
                            error_type=type(exc).__name__,
                        )
                        # P4-S23: include session_id so the panel can
                        # show the error on the right tile/slot.
                        # P4-S22 carryover: also include error_type so
                        # the UI can display "ConnectError" instead of
                        # falling back to "unknown" when str(exc) is "".
                        try:
                            await _ws.send_json({
                                "type": "chat_v2_error",
                                "payload": {
                                    "error": str(exc) or type(exc).__name__,
                                    "detail": type(exc).__name__,
                                    "session_id": _sid,
                                },
                            })
                        except Exception:
                            pass

                # Fire-and-forget — recv loop must keep draining
                # permission_response while we're awaiting the gate.
                # P4-S23: stamp the per-message session_id so multi-
                # session panels work correctly. Track in-flight task
                # per-sid so a same-sid retry cancels its predecessor
                # (prevents stale tool calls if user rage-types).
                # 对话回合硬超时包装(默认 15 分钟,设置里可调)。relay/网络持续
                # 不可用时 agent 会无限重试让桌宠"努力工作中"死转 —— 超时即优雅
                # 停止 + 发 chat_v2_final 友好告知,清掉前端转圈。
                async def _run_chat_with_timeout(
                    _g_ws,
                    _g_text,
                    _g_sid,
                    _g_mp=None,
                    _g_explicit_new=False,
                    _g_attachment_blocks=(),
                ):
                    _g_to = _chat_turn_timeout_s()
                    try:
                        await asyncio.wait_for(
                            _run_chat(
                                _g_ws,
                                _g_text,
                                _g_sid,
                                _g_mp,
                                _g_explicit_new,
                                _g_attachment_blocks,
                            ),
                            timeout=_g_to,
                        )
                    except asyncio.TimeoutError:
                        _g_min = int(round(_g_to / 60))
                        logger.warning(
                            "chat_turn_timeout sid=%s timeout_s=%.0f", _g_sid, _g_to,
                        )
                        _g_evt = {
                            "type": "chat_v2_final",
                            "payload": {
                                "text": (
                                    f"⏱️ 这次请求超过 {_g_min} 分钟还没完成,可能是网络或"
                                    f"中转站不稳。我先停下来了,稍后再试试看~"
                                    f"(超时时长可在设置里调整)"
                                ),
                                "iterations": 0,
                                "session_id": _g_sid,
                            },
                        }
                        try:
                            await _g_ws.send_json(_g_evt)
                            await _broadcast_default_chat_peers(_g_ws, _g_evt)
                        except Exception:  # noqa: BLE001
                            pass

                _prev_task = _chat_inflight.get(_msg_sid)
                if _prev_task is not None and not _prev_task.done():
                    _prev_task.cancel()
                _chat_task = asyncio.create_task(
                    _run_chat_with_timeout(
                        ws,
                        text,
                        _msg_sid,
                        _memory_policy_override,
                        bool(_scope_decision.created),
                        _attachment_blocks,
                    )
                )
                _chat_inflight[_msg_sid] = _chat_task

                # P5-S2 Phase 4: register a per-sid re-dispatcher closure
                # so the AutoResumeOrchestrator can spawn a follow-up
                # task without needing access to the chat-handler-scoped
                # ``_run_chat``. The closure captures the latest _run_chat
                # and is overwritten on every user turn, so it always
                # reflects the freshest local state.
                async def _redisp(_target_ws, _target_sid):
                    _new_task = asyncio.create_task(
                        _run_chat_with_timeout(_target_ws, "<<auto_resume>>", _target_sid)
                    )
                    _chat_inflight[_target_sid] = _new_task
                    _new_task.add_done_callback(_make_followup_cb(_target_sid, _target_ws))

                _auto_resume_redispatchers[_msg_sid] = _redisp

                # P5-S4: register a done_callback so when this task
                # finishes (success or cancel), we check whether the
                # supervisor pushed a hint while it was running and
                # automatically schedule a follow-up turn to consume it.
                # Critical safety property: if the user retried in the
                # meantime, _chat_inflight[sid] points at a NEWER task —
                # we detect that and skip, letting the new task consume
                # the hint via its own message-build pop_all path.
                def _make_followup_cb(target_sid: str, target_ws):
                    own_task = _chat_task

                    def _cb(_t):
                        async def _maybe_followup():
                            try:
                                cur = _chat_inflight.get(target_sid)
                                if cur is not own_task:
                                    # User retried (or another follow-up
                                    # took over) — let that task handle hints.
                                    return
                                _nq_check = service_context.get("nudge_queue")
                                if _nq_check is None or not await _nq_check.peek(target_sid):
                                    return
                                # Schedule a follow-up turn with synthesized
                                # trigger text. ``_run_chat`` will pop the
                                # hint via the standard injection path.
                                new_task = asyncio.create_task(
                                    _run_chat_with_timeout(target_ws, "<<supervisor_followup>>", target_sid)
                                )
                                _chat_inflight[target_sid] = new_task
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
                # Forward barge-in to the audio pipeline (separate WS). Cancels
                # in-flight ASR/LLM/TTS so user's new utterance gets priority.
                pipeline = _pipelines.get(session_id)
                if pipeline is not None:
                    pipeline.interrupt()
                    logger.info("interrupt dispatched", session_id=session_id)
                else:
                    logger.info("interrupt received but no active pipeline", session_id=session_id)
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
                    _prev = _chat_inflight.get(_target_sid)
                    if _prev is not None and not _prev.done():
                        _prev.cancel()
                        logger.info("supervisor_user_choice_cancelled sid=%s", _target_sid)
                    _nq_for_stop = service_context.get("nudge_queue")
                    if _nq_for_stop is not None:
                        try:
                            await _nq_for_stop.clear(_target_sid)
                        except Exception:
                            pass
                elif _is_continue:
                    # Trigger a follow-up that consumes the queued hint.
                    # If a chat task is already running, leave it alone —
                    # the existing done_callback will handle the queue.
                    _cur = _chat_inflight.get(_target_sid)
                    if _cur is None or _cur.done():
                        # P6 bugfix 2026-05-14 (live-test): _run_chat 是
                        # chat_v2 分支的 nested async def (line ~2707)，与
                        # 本分支并列。Python 函数作用域规则：outer 函数里
                        # 任何分支定义过的局部变量都视为整个函数的 local，
                        # 但**未走该分支时未赋值** → UnboundLocalError。
                        # 之前用户点"允许并继续"按钮时如果该 ws 连接还没
                        # 跑过 chat_v2，就会崩这里。fallback：locals() 检
                        # 测 → 缺失就给用户友好提示 + 用 _chat_inflight 已
                        # 注册的 redispatcher（如果存在）兜底。
                        _run_chat_fn = locals().get("_run_chat")
                        if _run_chat_fn is None:
                            logger.warning(
                                "supervisor_user_choice_followup_skipped sid=%s "
                                "reason=dispatcher_not_initialized",
                                _target_sid,
                            )
                            try:
                                await ws.send_json({
                                    "type": "chat_v2_error",
                                    "payload": {
                                        "session_id": _target_sid,
                                        "reason": "dispatcher_not_ready",
                                        "detail": (
                                            "supervisor 后续路径还没准备好。"
                                            "请在输入框直接重发一条消息即可继续任务。"
                                        ),
                                    },
                                })
                            except Exception:
                                pass
                        else:
                            try:
                                _new_task = asyncio.create_task(
                                    _run_chat_fn(ws, "<<supervisor_followup>>", _target_sid)
                                )
                                _chat_inflight[_target_sid] = _new_task
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
        # P4-S20: only clear the dict entry if it's still pointing at
        # OUR ws — a later connection may have replaced us already.
        if _control_connections.get(session_id) is ws:
            _control_connections.pop(session_id, None)
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

    session_id = ws.query_params.get("session_id", "default")
    control_ws = _control_connections.get(session_id)

    from pipeline.voice_pipeline import VoicePipeline

    # Each audio connection gets its own VAD instance (stateful)
    session_vad = SileroVAD(
        threshold=config.vad.threshold,
        min_speech_ms=config.vad.min_speech_ms,
        min_silence_ms=config.vad.min_silence_ms,
    )
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
        # FP-5 缺口 5k：传 AppConfig 让语音 venue 走 build_agent 工厂（接 verify_gate
        # + codify + skill 披露/重挂），与文字 venue 对齐。
        app_config=config,
    )
    # Register so control-channel `interrupt` messages can reach us.
    _pipelines[session_id] = pipeline

    logger.info("audio channel connected", session_id=session_id)
    try:
        while True:
            data = await ws.receive_bytes()
            await pipeline.process_audio_chunk(data, ws)
    except WebSocketDisconnect:
        logger.info("audio channel disconnected", session_id=session_id)
    finally:
        _pipelines.pop(session_id, None)


def main():
    logger.info("starting backend", host=config.backend.host, port=config.backend.port)
    print(f"SHARED_SECRET={SHARED_SECRET}", flush=True)
    uvicorn.run(app, host=config.backend.host, port=config.backend.port, log_level=config.backend.log_level.lower())


if __name__ == "__main__":
    main()
