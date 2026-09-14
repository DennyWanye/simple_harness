# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Explicit offline-tokenized local model; no credential or network access here."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .provider import ProviderSnapshot

LOCAL_PROFILE_ID = "local-context-256k-v1"
LOCAL_TOTAL_TOKENS = 262_144


def local_runtime_options(config: Any, provider: Any, snapshot: ProviderSnapshot | None,
                          profile_path: str) -> dict[str, Any]:
    from agent_orchestrator.runtime.assembly import resolve_profile_context_policy
    from agent_orchestrator.runtime.hf_chat_tokens import HFChatTokenEstimator
    from agent_orchestrator.runtime.legacy_provider_slots import (
        profile_has_frozen_admission,
    )
    from agent_orchestrator.runtime.model_router import RuntimeProfile
    from simple_harness.agents.context.budget import ContextPolicy

    try:
        path = Path(profile_path)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("profile path")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if (snapshot is None or raw["base_url"].rstrip("/") != snapshot.base_url.rstrip("/")
                or raw["model"] != snapshot.requested_model
                or type(raw["max_total_tokens"]) is not int
                or raw["max_total_tokens"] != LOCAL_TOTAL_TOKENS):
            raise ValueError("provider or window mismatch")
        tokenizer_path = Path(raw["tokenizer_path"])
        if not tokenizer_path.is_absolute():
            raise ValueError("tokenizer path")
        counter = HFChatTokenEstimator(
            tokenizer_path, model=snapshot.requested_model,
            expected_files=raw["tokenizer_files"],
            chat_template_kwargs=raw.get("chat_template_kwargs", {}),
        )
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise RuntimeError("本地模型配置或 tokenizer 身份无效；不会回退到付费模型") from error

    wanted = ContextPolicy(
        max_input_tokens=LOCAL_TOTAL_TOKENS, max_total_tokens=LOCAL_TOTAL_TOKENS,
        output_reserve=32768, safety_margin=1024, max_tool_result_tokens=16384,
        render_slack_tokens=0,
    )
    frozen = resolve_profile_context_policy(
        config, profile_id=LOCAL_PROFILE_ID, tokenizer=counter, fresh_policy=wanted,
    )
    if frozen != wanted:
        raise RuntimeError("本地 256K 执行库配置不一致")
    legacy = resolve_profile_context_policy(config, tokenizer=counter)
    profiles = {
        "default": RuntimeProfile(
            "default", provider, config.model, price_table=config.price_table,
            provider_kind="env", context_policy=legacy,
            tokenizer=counter if legacy is not None else None,
        ),
        LOCAL_PROFILE_ID: RuntimeProfile(
            LOCAL_PROFILE_ID, provider, config.model, price_table=config.price_table,
            provider_kind="env", context_policy=frozen, tokenizer=counter,
            default_max_output_tokens=8192, max_output_tokens_ceiling=32768,
            max_concurrent_model_calls=1,
        ),
    }
    counters = {key: counter for key in profiles}
    old_admission = profile_has_frozen_admission(config, "default")
    if old_admission is False or (old_admission is None and legacy is None):
        counters["default"] = None
    return {"profiles": profiles, "provider_token_estimators": counters}
