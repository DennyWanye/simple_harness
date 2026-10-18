"""按会话选模型提供方链：代码工作流的提供方选择与恢复、持久任务的提供方快照、Agent 的提供方链构造与解析。"""

from __future__ import annotations

from llm.resolution import SessionProviderUnavailable, resolve_session_provider_chain
from providers.openai_compatible import OpenAICompatibleProvider

from host.llm_providers import _e2e_provider_base, _sanitize_cot_dsml
from host.runtime import config, logger, service_context


def _select_code_workflow_provider(*, local_llm, cloud_llm):
    """Select the legacy fallback when no session provider chain resolves."""

    return local_llm or cloud_llm


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
    return _build_agent_provider_chain(entries=entries, registry=registry) if entries else None
