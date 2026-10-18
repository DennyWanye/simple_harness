"""模型调用的构造与刷新：给事实抽取、深度研究、图像接口用的调用函数，以及设置变化后的重新接线。"""

from __future__ import annotations

from deskpet.execution.provider_workloads import SessionAwareLLMCall
from deskpet.tools import image_tools as _image_tools
from deskpet.tools import research_tools as _research_tools
from deskpet.tools.research_fetch import _is_loopback_url
from llm.context_messages import provider_purpose_scope
from providers.openai_compatible import OpenAICompatibleProvider

from host.llm_providers import _e2e_provider_base, _resolved_api_key, _sanitize_cot_dsml
from host.runtime import config, logger, service_context
from host.state import host_state


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
                        messages=_msgs,
                        max_tokens=max_tokens,
                        temperature=0.2,
                        response_format=response_format,
                    )
                content = (result or {}).get("content") or ""
                if content.strip():
                    return content
            except Exception:  # noqa: BLE001 - 非流式失败时改用流式
                logger.debug("llm_nonstream_call_failed", exc_info=True)
            with provider_purpose_scope(purpose):
                result = await provider.chat_with_tools(
                    messages=_msgs,
                    max_tokens=max_tokens,
                    temperature=0.2,
                    response_format=response_format,
                )
            return (result or {}).get("content") or ""
        # 无 schema（facts/reflection 等）：保持流式，字节级 BC。
        with provider_purpose_scope(purpose):
            result = await provider.chat_with_tools(
                messages=_msgs,
                max_tokens=max_tokens,
                temperature=0.2,
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

    return SessionAwareLLMCall(
        lambda: service_context.get("provider_workload_router"),
        max_tokens=max_tokens,
        response_format=response_format,
    )


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
            name,
            exc,
            exc_info=True,
        )
        return base_provider


# 把运行中的 local_llm(relay base_url + keychain key) 注入 deep-research，
# 让 deepresearch 的 plan/synthesize/reflection 走和聊天 agent 同一个 live
# relay（修旧 _resolve_default_llm_call 读 providers[0] 丢 key 的隐患）。
try:
    from deskpet.tools import research_tools as _research_tools

    _research_tools.set_live_llm_call(
        _make_str_llm_call(host_state.local_llm, max_tokens=4096, purpose="research")
    )

    # LLM 重排桥: 用【廉价模型】(默认 gpt-4.1-mini,中转站有)做 research 召回后的
    # cross-encoder 式精排 —— 免下载本地 bge-reranker、免占本地内存,复用 relay。
    # 同 base_url + keychain key,只换 model。[research].reranker_model 可覆盖。
    # 仅在云端/relay base_url 下注入: 本地 ollama(localhost) 通常没有 gpt-4.1-mini,
    # 会每次研究都失败白调,故 localhost 端点不注入(research 自动跳过精排)。
    try:
        _r_base = str(config.llm.local.base_url or "")
        if _r_base and not _is_loopback_url(_r_base):
            _rerank_model = str(
                (config.raw.get("research") or {}).get("reranker_model", "gpt-4.1-mini")
            )
            _rerank_provider = OpenAICompatibleProvider(
                base_url=_r_base,
                api_key=_resolved_api_key,
                model=_rerank_model,
            )
            _research_tools.set_rerank_llm_call(
                _make_str_llm_call(_rerank_provider, max_tokens=2048, purpose="research")
            )
    except Exception as _exc2:  # noqa: BLE001 — 未注入则 research 跳过精排
        logger.debug("research_rerank_wiring_skipped", error=str(_exc2), exc_info=True)
except Exception as _exc:  # noqa: BLE001 — research 仍可回退 config 重建
    logger.debug("research_live_llm_wiring_skipped", error=str(_exc), exc_info=True)


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
        except (
            Exception
        ):  # NoProviderConfiguredError 等 —— 没配就是没配  # noqa: BLE001 - 没配提供方就是没配
            logger.debug("primary_provider_chain_missing", exc_info=True)
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
        logger.debug("primary_provider_resolve_skipped", error=str(_exc), exc_info=True)
    return None


def _refresh_image_endpoint_resolver() -> None:
    """Keep image/PPT generation aligned with the active relay provider."""
    try:

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
        logger.debug("image_endpoint_resolver_wiring_skipped", error=str(_exc), exc_info=True)


def _refresh_research_live_llm(provider=None) -> None:
    """Keep deep-research/PPT background LLM aligned with chat routing."""
    try:
        _provider = provider or _resolve_primary_registry_provider() or host_state.local_llm
        _research_tools.set_live_llm_call(
            _make_str_llm_call(_provider, max_tokens=4096, purpose="research")
        )

        try:
            _r_base = str(getattr(_provider, "base_url", "") or "")
            if _r_base and not _is_loopback_url(_r_base):
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
                    _make_str_llm_call(_rerank_provider, max_tokens=2048, purpose="research")
                )
        except Exception as _exc2:  # noqa: BLE001
            logger.debug("research_rerank_wiring_skipped", error=str(_exc2), exc_info=True)

        logger.info(
            "research_live_llm_refreshed base_url=%s model=%s",
            getattr(_provider, "base_url", ""),
            getattr(_provider, "model", ""),
        )
    except Exception as _exc:  # noqa: BLE001
        logger.debug("research_live_llm_wiring_skipped", error=str(_exc), exc_info=True)


_refresh_image_endpoint_resolver()
_refresh_research_live_llm()
