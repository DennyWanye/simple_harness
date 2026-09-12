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
    if counter is not None and policy is not None:
        options["provider_token_estimator"] = counter
    return options
