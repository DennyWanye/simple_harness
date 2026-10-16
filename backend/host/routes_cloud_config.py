"""HTTP 路由：云端模型配置（POST /config/cloud）"""

from __future__ import annotations

import os
import re
import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response
from providers.openai_compatible import OpenAICompatibleProvider
from pydantic import BaseModel, field_validator
from router.hybrid_router import RoutingStrategy

from host.auth import DEV_MODE
from host.llm_calls import _refresh_research_live_llm
from host.llm_providers import (
    _e2e_provider_base,
    _load_llm_runtime_overrides,
    _sanitize_cot_dsml,
    _save_llm_runtime_overrides,
    llm,
)
from host.runtime import SHARED_SECRET, logger, service_context
from host.state import host_state

cloud_config_router = APIRouter()


class CloudConfigRequest(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None  # absent or empty = keep current key
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
        if not re.match(r"^https?://[^\s]+$", v):
            raise ValueError(
                "base_url must start with http:// or https:// and contain no whitespace"
            )
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
    await registry.add_provider(fields)
    logger.info("provider_registry_seeded_from_runtime base_url=%s model=%s", base_url, model)


def _provider_display_name(base_url: str, fallback: str) -> str:
    """从 base_url 取主机名做 provider 显示名，取不到就回退到 id。"""

    try:
        return urlsplit(base_url).hostname or fallback
    except Exception:  # noqa: BLE001 - 显示名不值得让配置写入失败
        logger.debug("provider_display_name_urlsplit_failed", exc_info=True)
        return fallback


@cloud_config_router.post("/config/cloud")
async def update_cloud_config(body: CloudConfigRequest, request: Request):
    """P4-S20-LLM-Unified: hot-swap the unified LLM provider.

    URL keeps the legacy /config/cloud name for frontend compat, but
    semantics now: replace the single `local_llm` (which serves all
    chat traffic) and persist to llm_runtime.json so the new config
    survives restart.

    Auth: same shared-secret gate as /metrics. DEV_MODE bypasses.
    """

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
        if host_state.current_cloud_api_key:
            resolved_key = host_state.current_cloud_api_key
        elif host_state.local_llm is not None:
            resolved_key = getattr(host_state.local_llm, "api_key", None) or "ollama"
        else:
            resolved_key = "ollama"

    current_temperature = 0.7
    if host_state.local_llm is not None:
        current_temperature = getattr(host_state.local_llm, "temperature", 0.7)

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
    host_state.local_llm = new_provider
    host_state.current_cloud_api_key = resolved_key
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
        body.base_url,
        body.model,
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
            _ordered_ids = [
                _pid,
                *[str(item["id"]) for item in _upsert_reg.get_chain() if str(item["id"]) != _pid],
            ]
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
                exc_info=True,
            )

    return {
        "ok": True,
        "cloud_configured": True,  # always true under unified schema
        "base_url": body.base_url,
        "model": body.model,
        "has_api_key": bool(resolved_key) and resolved_key != "ollama",
        "strategy": llm._strategy.value,
    }
