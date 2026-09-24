# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Explicit source-runtime context and token accounting configuration.

The installed candidate retains its original interface. Source development uses
the new SDK ports, while existing execution pools retain their frozen profile.
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


def long_context_profile_id(tokens: int) -> str:
    if type(tokens) is not int or tokens not in CONTEXT_INPUT_LIMITS:
        raise ValueError("上下文仅支持 256K 或 512K")
    return f"deepseek-context-{tokens // 1024}k-v1"


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


def _CURRENT_COUNTERS() -> tuple[type, ...]:
    from agent_orchestrator.runtime.deepseek_meter import CertifiedDeepSeekCounter

    return (CertifiedDeepSeekCounter,)


def legacy_counter_for(snapshot: ProviderSnapshot | None, state_dir: Path | None = None) -> Any:
    """The counter identity released before 2026-09-24, for pools frozen with it; on a relay
    it also charges that host's learned tool-preamble margin (identity unchanged)."""

    if snapshot is None or snapshot.requested_model not in DEEPSEEK_COUNTER_MODELS:
        return None
    try:
        from agent_orchestrator.runtime.deepseek_meter import LegacyRelayDeepSeekCounter
        from agent_orchestrator.runtime.deepseek_tokens import LegacyPriorOutputDeepSeekCounter
    except ImportError:
        return None
    path = tokenizer_path()
    if path is None:
        return None
    host = urlparse(snapshot.base_url).hostname or ""
    if host in DEEPSEEK_OFFICIAL_HOSTS:
        return LegacyPriorOutputDeepSeekCounter(path, model=snapshot.requested_model)
    return LegacyRelayDeepSeekCounter(path, model=snapshot.requested_model, margin=relay_tool_margin(host, state_dir))


def frozen_tokenizer_fingerprint(config: Any, profile_id: str) -> str | None:
    """The tokenizer fingerprint a pool's execution library was frozen with, if any."""

    import json

    from agent_orchestrator.runtime.assembly import execution_db_for

    database = execution_db_for(config, profile_id)
    sidecar = database.with_name(database.name + ".context.json")
    if not sidecar.is_file():
        return None
    try:
        value = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # the SDK's own strict reader reports an unreadable identity
    fingerprint = value.get("tokenizer_fingerprint") if isinstance(value, dict) else None
    return fingerprint if isinstance(fingerprint, str) else None


def counter_for_pool(config: Any, identifier: str, current: Any, legacy: Any, *, native_pool: bool) -> Any:
    """The counter a pool's frozen context identity requires (None: do not register it).

    Nothing migrates old requests (SDK principle), so a pool frozen with the counter identity
    released before 2026-09-24 keeps that exact counter and its semantics; an ARP pool (only
    ever on the development branch) or an unknown identity is not registered, so its Missions
    fail closed instead of stopping the whole service."""

    frozen = frozen_tokenizer_fingerprint(config, identifier)
    if frozen is None or current is None or frozen == current.fingerprint:
        return current
    if legacy is not None and frozen == legacy.fingerprint and not native_pool:
        return legacy
    logger.warning("execution pool %s retired: frozen counter identity %s is not served", identifier, str(frozen)[:24])
    return None


def calibrated(provider: Any, counter: Any) -> Any:
    """A relay pool's provider learns the relay margin from every reported prompt count."""

    try:
        from agent_orchestrator.runtime.deepseek_meter import (
            CalibratingProvider,
            LegacyRelayDeepSeekCounter,
            RelayDeepSeekCounter,
        )
    except ImportError:
        return provider
    if provider is None or not isinstance(counter, (RelayDeepSeekCounter, LegacyRelayDeepSeekCounter)):
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

    state_dir = getattr(config, "evidence_root", None)
    counter = deepseek_counter_for(snapshot, settings, state_dir=state_dir)
    # Trusted deployment composition only (tests): a certified fixture counter that
    # stands in for the DeepSeek one so the native pools can be assembled offline.
    if counter is None and native_test_counter is not None:
        counter = native_test_counter

    legacy_counter = legacy_counter_for(snapshot, state_dir) if isinstance(counter, _CURRENT_COUNTERS()) else None
    pool_counters: dict[str, Any] = {}

    def pick(identifier: str, current: Any, *, native_pool: bool) -> Any:
        return counter_for_pool(config, identifier, current, legacy_counter, native_pool=native_pool)

    def provider_for(pool_counter: Any, base: Any) -> Any:
        return calibrated(base, pool_counter) if pool_counter is not None else base

    default_counter = pick("default", counter, native_pool=False)
    if default_counter is None:
        default_counter = counter  # the default pool is required: keep the old strict refusal
    policy = resolve_profile_context_policy(config, tokenizer=default_counter)
    pool_counters["default"] = default_counter
    options: dict[str, Any] = {
        "profiles": {
            "default": RuntimeProfile(
                "default",
                provider_for(default_counter, provider),
                config.model,
                price_table=config.price_table,
                provider_kind="env" if snapshot is not None else "fixtures",
                context_policy=policy,
                tokenizer=default_counter if policy is not None else None,
            )
        }
    }
    if counter is not None:
        from agent_orchestrator.runtime.legacy_provider_slots import (
            profile_has_frozen_admission,
        )
        from simple_harness.agents.context.budget import ContextPolicy

        def register(identifier: str, tokens: int, current: Any, base: Any, *, native_pool: bool, kind: str) -> None:
            pool_counter = pick(identifier, current, native_pool=native_pool)
            if pool_counter is None:
                return
            wanted = ContextPolicy(
                max_input_tokens=tokens, output_reserve=32768,
                max_tool_result_tokens=16384, render_slack_tokens=0,
            )
            frozen = resolve_profile_context_policy(
                config, profile_id=identifier, tokenizer=pool_counter, fresh_policy=wanted,
            )
            if frozen != wanted:
                raise RuntimeError(f"上下文执行库配置不一致：{identifier}")
            pool_counters[identifier] = pool_counter
            options["profiles"][identifier] = RuntimeProfile(
                identifier, provider_for(pool_counter, base), config.model, price_table=config.price_table,
                provider_kind=kind,
                context_policy=frozen, tokenizer=pool_counter,
                default_max_output_tokens=8192, max_output_tokens_ceiling=32768,
                **({"native_plane": native.assembly(identifier, tokens=tokens, counter=pool_counter)} if native_pool else {}),
            )

        # New named pools coexist with the old default pool. Never reinterpret
        # a legacy Mission or an already frozen dispatch as a new capacity.
        for tokens in CONTEXT_INPUT_LIMITS:
            register(long_context_profile_id(tokens), tokens, counter, provider, native_pool=False, kind="env")
        if native is not None and getattr(settings, "native_plane", "on") == "on":
            from .native_plane import native_profile_id

            # ARP-EXEC-1.1.1: the native-plane pools, beside (never instead of) the legacy
            # ones.  An existing Mission keeps the pool it was frozen on.
            for tokens in CONTEXT_INPUT_LIMITS:
                register(native_profile_id(tokens), tokens, counter, provider, native_pool=True,
                         kind="env" if snapshot is not None else "fixtures")
            # Thinking-mode pools (user decision 2026-09-24: both modes supported): a separate
            # provider (thinking enabled, reasoning replayed) and a counter bound to the same
            # mode.  Only for a DeepSeek deployment with a certified counter.
            thinking_counter = (
                deepseek_counter_for(snapshot, settings, thinking="enabled", state_dir=state_dir)
                if thinking_provider is not None and snapshot is not None else None
            )
            if thinking_counter is not None:
                for tokens in CONTEXT_INPUT_LIMITS:
                    register(native_profile_id(tokens, thinking=True), tokens, thinking_counter, thinking_provider,
                             native_pool=True, kind="env")
        options["provider_token_estimators"] = {
            key: pool_counters.get(key, counter) for key in options["profiles"]
        }
        if (
            legacy_counter is not None
            and frozen_tokenizer_fingerprint(config, "default") is None
            and options["provider_token_estimators"]["default"] is counter
        ):
            # A default library without a context identity may still hold intents admitted
            # with the released counter: offer both; the persisted admission identity picks
            # (review 2026-09-24).  A fresh pool takes the current counter.
            options["provider_token_estimators"]["default"] = (counter, legacy_counter)
        frozen_admission = profile_has_frozen_admission(config, "default")
        if frozen_admission is False or (frozen_admission is None and policy is None):
            # None preserves the old external admission identity. SDK composition
            # separately supplies shared, durable physical slots for this pool.
            options["provider_token_estimators"]["default"] = None
    return options
