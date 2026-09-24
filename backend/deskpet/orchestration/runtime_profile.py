# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Explicit source-runtime context and token accounting configuration.

The installed candidate retains its original interface. Source development uses
the new SDK ports, while existing execution pools retain their frozen profile.
No credentials are consulted here and no resources are downloaded at startup.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .provider import DEEPSEEK_OFFICIAL_HOSTS, ProviderSnapshot

TOKENIZER_PATH_ENV = "DESKPET_ORCH_TOKENIZER_PATH"
CONTEXT_INPUT_LIMITS = (262_144, 524_288)


def long_context_profile_id(tokens: int) -> str:
    if type(tokens) is not int or tokens not in CONTEXT_INPUT_LIMITS:
        raise ValueError("上下文仅支持 256K 或 512K")
    return f"deepseek-context-{tokens // 1024}k-v1"


DEEPSEEK_COUNTER_MODELS = frozenset({"deepseek-flash", "deepseek-v4.1-flash"})


def tokenizer_path() -> Path | None:
    """The pinned DeepSeek tokenizer: ``DESKPET_ORCH_TOKENIZER_PATH`` (absolute), else the
    deployment's model directory (``<models>/deepseek-v41/tokenizer.json``)."""

    configured = os.environ.get(TOKENIZER_PATH_ENV)
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else None
    try:
        from paths import user_models_dir
    except ImportError:
        return None
    candidate = Path(user_models_dir()) / "deepseek-v41" / "tokenizer.json"
    return candidate if candidate.is_file() else None


def deepseek_counter_for(snapshot: ProviderSnapshot | None, settings: Any = None, *, thinking: str = "disabled") -> Any:
    """The certified official V4.1 counter for an official (or declared-compatible) DeepSeek
    endpoint, or None for any other provider."""

    if snapshot is None or snapshot.requested_model not in DEEPSEEK_COUNTER_MODELS:
        return None
    from .settings import compatible_hosts

    host = urlparse(snapshot.base_url).hostname
    if host not in DEEPSEEK_OFFICIAL_HOSTS and (host or "").lower() not in compatible_hosts(settings):
        return None
    from agent_orchestrator.runtime.deepseek_meter import CertifiedDeepSeekCounter

    path = tokenizer_path()
    if path is None:
        raise RuntimeError("源码 DeepSeek profile 需要绝对路径 DESKPET_ORCH_TOKENIZER_PATH")
    return CertifiedDeepSeekCounter(path, model=snapshot.requested_model, thinking=thinking)


def deepseek_thinking(snapshot: ProviderSnapshot | None, settings: Any = None) -> str | None:
    """The explicit thinking mode of the Host's shared provider: ``disabled`` for a DeepSeek
    model (it thinks by default, and a tool loop without replayed reasoning is rejected);
    None for every other model, whose requests stay unchanged.  Thinking-mode work runs on
    the separate thinking pools (``native_profile_id(tokens, thinking=True)``)."""

    del settings
    if snapshot is None or snapshot.requested_model not in DEEPSEEK_COUNTER_MODELS:
        return None
    return "disabled"


def source_runtime_options(
    config: Any,
    provider: Any,
    snapshot: ProviderSnapshot | None,
    *, local_profile_path: str = "",
    settings: Any = None,
    native: Any = None,
    native_test_counter: Any = None,
    thinking_provider: Any = None,
) -> dict[str, Any]:
    # The candidate wheel now ships the same context ports as source runs.
    # Resolve the persisted pool identity in both installations; dropping these
    # options on wheel startup reinterprets every frozen source dispatch.
    try:
        from agent_orchestrator.runtime.assembly import resolve_profile_context_policy
        from agent_orchestrator.runtime.model_router import RuntimeProfile
    except ImportError:
        if local_profile_path:
            raise RuntimeError("当前 SDK 不支持本地上下文 profile") from None
        return {}
    if local_profile_path:
        from .local_profile import local_runtime_options
        return local_runtime_options(config, provider, snapshot, local_profile_path)

    counter = deepseek_counter_for(snapshot, settings)
    # Trusted deployment composition only (tests): a certified fixture counter that
    # stands in for the DeepSeek one so the native pools can be assembled offline.
    if counter is None and native_test_counter is not None:
        counter = native_test_counter

    policy = resolve_profile_context_policy(config, tokenizer=counter)
    options: dict[str, Any] = {
        "profiles": {
            "default": RuntimeProfile(
                "default",
                provider,
                config.model,
                price_table=config.price_table,
                provider_kind="env" if snapshot is not None else "fixtures",
                context_policy=policy,
                tokenizer=counter if policy is not None else None,
            )
        }
    }
    if counter is not None:
        from agent_orchestrator.runtime.legacy_provider_slots import (
            profile_has_frozen_admission,
        )
        from simple_harness.agents.context.budget import ContextPolicy

        # New named pools coexist with the old default pool. Never reinterpret
        # a legacy Mission or an already frozen dispatch as a new capacity.
        for tokens in CONTEXT_INPUT_LIMITS:
            identifier = long_context_profile_id(tokens)
            wanted = ContextPolicy(
                max_input_tokens=tokens, output_reserve=32768,
                max_tool_result_tokens=16384, render_slack_tokens=0,
            )
            frozen = resolve_profile_context_policy(
                config, profile_id=identifier, tokenizer=counter, fresh_policy=wanted,
            )
            if frozen != wanted:
                raise RuntimeError(f"上下文执行库配置不一致：{identifier}")
            options["profiles"][identifier] = RuntimeProfile(
                identifier, provider, config.model, price_table=config.price_table,
                provider_kind="env", context_policy=frozen, tokenizer=counter,
                default_max_output_tokens=8192, max_output_tokens_ceiling=32768,
            )
        if native is not None and getattr(settings, "native_plane", "on") == "on":
            from .native_plane import native_profile_id

            # ARP-EXEC-1.1.1: the native-plane pools, beside (never instead of) the legacy
            # ones.  An existing Mission keeps the pool it was frozen on.
            for tokens in CONTEXT_INPUT_LIMITS:
                identifier = native_profile_id(tokens)
                wanted = ContextPolicy(
                    max_input_tokens=tokens, output_reserve=32768,
                    max_tool_result_tokens=16384, render_slack_tokens=0,
                )
                frozen = resolve_profile_context_policy(
                    config, profile_id=identifier, tokenizer=counter, fresh_policy=wanted,
                )
                if frozen != wanted:
                    raise RuntimeError(f"上下文执行库配置不一致：{identifier}")
                options["profiles"][identifier] = RuntimeProfile(
                    identifier, provider, config.model, price_table=config.price_table,
                    provider_kind="env" if snapshot is not None else "fixtures",
                    context_policy=frozen, tokenizer=counter,
                    default_max_output_tokens=8192, max_output_tokens_ceiling=32768,
                    native_plane=native.assembly(identifier, tokens=tokens, counter=counter),
                )
            # Thinking-mode pools (user decision 2026-09-24: both modes supported): a separate
            # provider (thinking enabled, reasoning replayed) and a counter bound to the same
            # mode.  Only for a DeepSeek deployment with a certified counter.
            thinking_counter = (
                deepseek_counter_for(snapshot, settings, thinking="enabled")
                if thinking_provider is not None and snapshot is not None else None
            )
            if thinking_counter is not None:
                for tokens in CONTEXT_INPUT_LIMITS:
                    identifier = native_profile_id(tokens, thinking=True)
                    wanted = ContextPolicy(
                        max_input_tokens=tokens, output_reserve=32768,
                        max_tool_result_tokens=16384, render_slack_tokens=0,
                    )
                    frozen = resolve_profile_context_policy(
                        config, profile_id=identifier, tokenizer=thinking_counter, fresh_policy=wanted,
                    )
                    if frozen != wanted:
                        raise RuntimeError(f"上下文执行库配置不一致：{identifier}")
                    options["profiles"][identifier] = RuntimeProfile(
                        identifier, thinking_provider, config.model, price_table=config.price_table,
                        provider_kind="env", context_policy=frozen, tokenizer=thinking_counter,
                        default_max_output_tokens=8192, max_output_tokens_ceiling=32768,
                        native_plane=native.assembly(identifier, tokens=tokens, counter=thinking_counter),
                    )
        options["provider_token_estimators"] = {
            key: (profile.tokenizer if "-thinking-" in key else counter)
            for key, profile in options["profiles"].items()
        }
        frozen_admission = profile_has_frozen_admission(config, "default")
        if frozen_admission is False or (frozen_admission is None and policy is None):
            # None preserves the old external admission identity. SDK composition
            # separately supplies shared, durable physical slots for this pool.
            options["provider_token_estimators"]["default"] = None
    return options
