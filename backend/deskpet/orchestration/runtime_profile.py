# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Execution pools and token accounting for orchestration.

2026-09-30 user decision (development phase, no compatibility, no hiding): the Host
assembles **only native-plane pools**, each with a certified counter.  The legacy
``default`` / ``deepseek-context-*`` pools, the local-model profile and the old counter
identities are gone; a deployment without a certified (DeepSeek) counter has no pool and
the service refuses with an explicit "only DeepSeek" reason.
No credentials are consulted here and no resources are downloaded at startup.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .provider import DEEPSEEK_OFFICIAL_HOSTS, ProviderSnapshot

TOKENIZER_PATH_ENV = "DESKPET_ORCH_TOKENIZER_PATH"
CONTEXT_INPUT_LIMITS = (262_144, 524_288)


logger = logging.getLogger(__name__)

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


_RELAY_MARGINS: dict[str, Any] = {}


def relay_tool_margin(host: str, state_dir: Path | None) -> Any:
    """The learned tool-preamble margin of one relay host, shared by every pool and counter
    of this process and persisted under ``state_dir`` (it only ever grows)."""

    from agent_orchestrator.runtime.deepseek_meter import RelayToolMargin

    key = host.lower()
    margin = _RELAY_MARGINS.get(key)
    if margin is None:
        path = None if state_dir is None else Path(state_dir) / "relay-tool-margin" / f"{key}.json"
        margin = _RELAY_MARGINS[key] = RelayToolMargin(state_path=path)
    return margin


# Thinking pools send an explicit effort with the switch.  Measured 2026-09-24 on a relay:
# the switch alone never produced reasoning (0/6), switch + effort always did (11/11); "high"
# is DeepSeek's documented default effort, so stating it changes nothing on the official API.
THINKING_EFFORT = "high"


def deepseek_counter_for(snapshot: ProviderSnapshot | None, settings: Any = None, *, thinking: str = "disabled",
                         state_dir: Path | None = None) -> Any:
    """The certified V4.1 counter for a DeepSeek endpoint, or None for any other provider.

    The official endpoint gets the exact counter.  A declared-compatible host is a relay:
    measured 2026-09-24, a relay adds its own tool preamble (~110 tokens), so it gets the
    upper-bound relay counter with that host's learned margin (user decision 2026-09-24:
    may overcount, never undercount)."""

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
    effort = THINKING_EFFORT if thinking == "enabled" else None  # the counter renders what is sent
    if host in DEEPSEEK_OFFICIAL_HOSTS:
        return CertifiedDeepSeekCounter(path, model=snapshot.requested_model, thinking=thinking, reasoning_effort=effort)
    from agent_orchestrator.runtime.deepseek_meter import RelayDeepSeekCounter

    return RelayDeepSeekCounter(
        path, model=snapshot.requested_model, thinking=thinking, reasoning_effort=effort,
        margin=relay_tool_margin(str(host), state_dir),
    )


def calibrated(provider: Any, counter: Any) -> Any:
    """A relay pool's provider learns the relay margin from every reported prompt count."""

    from agent_orchestrator.runtime.deepseek_meter import CalibratingProvider, RelayDeepSeekCounter

    if provider is None or not isinstance(counter, RelayDeepSeekCounter):
        return provider
    return CalibratingProvider(provider, counter)


def deepseek_thinking(snapshot: ProviderSnapshot | None, settings: Any = None) -> str | None:
    """The explicit thinking mode of the Host's shared provider: ``disabled`` for a DeepSeek
    model (it thinks by default, and a tool loop without replayed reasoning is rejected);
    None for every other model, whose requests stay unchanged.  Thinking-mode work runs on
    the separate thinking pools (``native_profile_id(tokens, thinking=True)``)."""

    del settings
    if snapshot is None or snapshot.requested_model not in DEEPSEEK_COUNTER_MODELS:
        return None
    return "disabled"


ONLY_DEEPSEEK_REASON = "编排只支持 DeepSeek：当前模型没有经过认证的用量计数器，请在设置里改用 DeepSeek 模型"


def source_runtime_options(
    config: Any,
    provider: Any,
    snapshot: ProviderSnapshot | None,
    *,
    settings: Any = None,
    native: Any = None,
    native_test_counter: Any = None,
    thinking_provider: Any = None,
) -> dict[str, Any]:
    """The native-plane pools of this deployment (``profiles`` empty when it has none).

    A pool needs a certified counter: the DeepSeek one for a DeepSeek endpoint, or the
    trusted test composition's ``native_test_counter`` (tests and fixture scenarios).
    Any other model gets no pool at all; the service reports ``ONLY_DEEPSEEK_REASON``.
    """

    from agent_orchestrator.runtime.assembly import resolve_profile_context_policy
    from agent_orchestrator.runtime.model_router import RuntimeProfile
    from simple_harness.agents.context.budget import ContextPolicy

    from .native_plane import native_profile_id

    state_dir = getattr(config, "evidence_root", None)
    counter = deepseek_counter_for(snapshot, settings, state_dir=state_dir)
    if counter is None:
        counter = native_test_counter
    options: dict[str, Any] = {"profiles": {}}
    if counter is None or native is None or provider is None:
        return options
    counters: dict[str, Any] = {}

    def register(identifier: str, tokens: int, pool_counter: Any, base: Any, *, kind: str,
                 default_output: int = 8192) -> None:
        wanted = ContextPolicy(
            max_input_tokens=tokens, output_reserve=32768,
            max_tool_result_tokens=16384, render_slack_tokens=0,
        )
        policy = resolve_profile_context_policy(
            config, profile_id=identifier, tokenizer=pool_counter, fresh_policy=wanted,
        )
        if policy != wanted:
            raise RuntimeError(f"上下文执行库配置不一致：{identifier}")
        counters[identifier] = pool_counter
        options["profiles"][identifier] = RuntimeProfile(
            identifier, calibrated(base, pool_counter), config.model, price_table=config.price_table,
            provider_kind=kind, context_policy=policy, tokenizer=pool_counter,
            default_max_output_tokens=default_output, max_output_tokens_ceiling=32768,
            native_plane=native.assembly(identifier, tokens=tokens, counter=pool_counter),
        )

    kind = "env" if snapshot is not None else "fixtures"
    for tokens in CONTEXT_INPUT_LIMITS:
        register(native_profile_id(tokens), tokens, counter, provider, kind=kind)
    # Thinking-mode pools (user decision 2026-09-24: both modes supported): a separate
    # provider (thinking enabled, reasoning replayed) and a counter bound to the same mode.
    thinking_counter = (
        deepseek_counter_for(snapshot, settings, thinking="enabled", state_dir=state_dir)
        if thinking_provider is not None and snapshot is not None else None
    )
    if thinking_counter is not None:
        for tokens in CONTEXT_INPUT_LIMITS:
            # Reasoning shares the output limit: at 8192 a thinking reviewer spent the budget
            # thinking and its verdict JSON was cut mid-string (2026-09-25 desktop run).
            register(native_profile_id(tokens, thinking=True), tokens, thinking_counter,
                     thinking_provider, kind="env", default_output=32768)
    options["provider_token_estimators"] = dict(counters)
    return options
