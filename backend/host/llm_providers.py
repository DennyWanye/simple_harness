"""模型提供方的启动装配：设置页的运行时覆盖、密钥解析、本地与云端提供方、计费账本、混合路由。"""

from __future__ import annotations

import hashlib
import json as _json
import os
from zoneinfo import ZoneInfo

import paths as _paths

# --- Register providers ---
from billing.ledger import BillingLedger
from config import resolve_cloud_api_key as _resolve_cloud_api_key  # P2-1-S3
from providers.openai_compatible import OpenAICompatibleProvider
from router.hybrid_router import HybridRouter, RoutingStrategy

from host.runtime import config, logger, service_context
from host.state import host_state

# P4-S20-LLM-Unified: runtime overrides — settings panel 里改的会写到这个文件，
# 启动时读它覆盖 config.toml [llm] 段（只 base_url / model / temperature；
# api_key 走 keychain 不进文件）。允许用户改完不动 config.toml 也保留。
LLM_RUNTIME_PATH = _paths.user_data_dir() / "llm_runtime.json"


def _load_llm_runtime_overrides() -> dict:
    """Read llm_runtime.json — empty/missing/invalid → {}."""
    if not LLM_RUNTIME_PATH.exists():
        return {}
    try:
        return _json.loads(LLM_RUNTIME_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_runtime_overrides_load_failed: %s", exc, exc_info=True)
        return {}


def _save_llm_runtime_overrides(data: dict) -> None:
    """Write llm_runtime.json — best-effort, never raise."""

    try:
        LLM_RUNTIME_PATH.parent.mkdir(parents=True, exist_ok=True)
        LLM_RUNTIME_PATH.write_text(
            _json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("llm_runtime_overrides_saved path=%s", LLM_RUNTIME_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_runtime_overrides_save_failed: %s", exc, exc_info=True)


# Agent 有效执行预算（默认 15 分钟，可在设置里调整）。等待用户确认、选择目录
# 或完成外部操作时由 durable Run 预算暂停，不再按整轮墙钟时间误取消。
def _chat_turn_timeout_s() -> float:
    try:
        m = float(_load_llm_runtime_overrides().get("chat_turn_timeout_minutes", 15))
    except Exception:  # noqa: BLE001
        logger.debug("chat_turn_timeout_s_float_failed", exc_info=True)
        m = 15.0
    return max(60.0, min(3600.0, m * 60.0))


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
        except Exception:  # noqa: BLE001 - 模型覆盖写不进配置时沿用原配置
            logger.warning("llm_model_override_failed", exc_info=True)
    if "base_url" in _llm_overrides:
        # base_url 也同步 raw(让 breakdown 探针的 base_url 预览也对;真出站本就用 dataclass)
        try:
            config.raw.setdefault("llm", {})["base_url"] = _llm_overrides["base_url"]
        except Exception:  # noqa: BLE001 - 地址覆盖写不进配置时沿用原配置
            logger.warning("llm_base_url_override_failed", exc_info=True)
    if "temperature" in _llm_overrides:
        config.llm.local.temperature = float(_llm_overrides["temperature"])
    if "api_key" in _llm_overrides and _llm_overrides["api_key"]:
        # plaintext stored in runtime json — usable but not recommended;
        # the proper place is OS keychain via SettingsPanel.
        config.llm.local.api_key = _llm_overrides["api_key"]
    logger.info(
        "llm_runtime_overrides_applied base_url=%s model=%s",
        config.llm.local.base_url,
        config.llm.local.model,
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
from providers.context_os_e2e_hooks import ContextOSE2EHooks as _ContextOSE2EHooks

_context_os_e2e_boot_hooks = _ContextOSE2EHooks.from_env()
_e2e_provider_base = (
    _context_os_e2e_boot_hooks.provider_url
    if _context_os_e2e_boot_hooks is not None and _context_os_e2e_boot_hooks.provider_url
    else ""
)

# 2026-05-17 deepseek-inline-cot-dsml-sanitize Strangler-Fig flag (default
# on). Read once; passed to every OpenAICompatibleProvider so setting
# [llm] sanitize_inline_cot_dsml = false instantly restores legacy raw
# passthrough after a restart (demo rollback).
_sanitize_cot_dsml = bool((config.raw.get("llm") or {}).get("sanitize_inline_cot_dsml", True))
logger.info("sanitize_inline_cot_dsml_flag", enabled=_sanitize_cot_dsml)

host_state.local_llm = OpenAICompatibleProvider(
    base_url=_e2e_provider_base or config.llm.local.base_url,
    api_key="ctx-e2e-local-key" if _e2e_provider_base else _resolved_api_key,
    model="ctx-primary" if _e2e_provider_base else config.llm.local.model,
    temperature=config.llm.local.temperature,
    sanitize_inline_cot_dsml=_sanitize_cot_dsml,
)


# Keep cloud_llm symbol around for legacy code paths but it's None now —
# config.llm.cloud is no longer set under the unified [llm] schema.
cloud_llm = None
host_state.current_cloud_api_key = (
    _resolved_api_key if "localhost" not in config.llm.local.base_url else None
)
if config.llm.cloud is not None:
    # 兼容旧 [llm.cloud] schema — 新部署不会走这里。
    _cloud_key = _resolve_cloud_api_key()
    if _cloud_key:
        host_state.current_cloud_api_key = _cloud_key
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

# Provider authority is created only after state.db migrations complete in
# lifespan. Importing main must never make a pre-migration registry usable.
service_context.register("provider_registry", None)
service_context.register("provider_routing_readiness", None)

llm = HybridRouter(
    local=host_state.local_llm,
    cloud=cloud_llm,
    strategy=RoutingStrategy(config.llm.strategy),
    # P2-1-S8: BillingLedger's hook debits cloud spend and denies cloud
    # calls once daily_budget_cny is exhausted. Local calls bypass the
    # hook entirely (free). See spec §1.1 / §2.4.
    budget_hook=billing_ledger.create_hook(),
)
service_context.register("llm_engine", llm)
