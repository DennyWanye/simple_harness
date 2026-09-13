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


def source_runtime_options(
    config: Any,
    provider: Any,
    snapshot: ProviderSnapshot | None,
) -> dict[str, Any]:
    from deskpet.sdk_adapters.sdk_candidate import SDK_RUNTIME_MODE_ENV

    if os.environ.get(SDK_RUNTIME_MODE_ENV, "wheel") != "editable-source":
        return {}

    from agent_orchestrator.runtime.assembly import resolve_profile_context_policy
    from agent_orchestrator.runtime.model_router import RuntimeProfile

    counter = None
    if (
        snapshot is not None
        and snapshot.requested_model == "deepseek-flash"
        and urlparse(snapshot.base_url).hostname in DEEPSEEK_OFFICIAL_HOSTS
    ):
        from agent_orchestrator.runtime.deepseek_tokens import DeepSeekV41TokenEstimator

        configured = os.environ.get(TOKENIZER_PATH_ENV)
        if not configured or not Path(configured).is_absolute():
            raise RuntimeError(
                "源码 DeepSeek profile 需要绝对路径 DESKPET_ORCH_TOKENIZER_PATH"
            )
        counter = DeepSeekV41TokenEstimator(
            Path(configured), model=snapshot.requested_model
        )

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
        options["provider_token_estimators"] = {key: counter for key in options["profiles"]}
        frozen_admission = profile_has_frozen_admission(config, "default")
        if frozen_admission is False or (frozen_admission is None and policy is None):
            # None preserves the old external admission identity. SDK composition
            # separately supplies shared, durable physical slots for this pool.
            options["provider_token_estimators"]["default"] = None
    return options
