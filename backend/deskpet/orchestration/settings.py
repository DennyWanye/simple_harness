# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""``config.toml [orchestration]``：桌面端在 SDK 宿主运行参数之上多出的几项。

运行参数本身（并发、节奏、预算默认、窗口、发布目录）是 SDK 的 ``HostSettings``；这里只加桌面自己的：
总开关、存储提醒阈值、DeepSeek 转发主机、思考开关、转发回显别名。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent_orchestrator.host import HostSettings, host_settings_from_mapping


@dataclass(frozen=True)
class OrchestrationSettings(HostSettings):
    enabled: bool = True  # CLAUDE.md: capabilities that passed testing ship on
    # 2026-09-25 user decision: task/session data is kept forever for audit; the Settings
    # page only reminds the user once ``<user_data>/data/agent-orchestrator`` passes this.
    storage_warn_bytes: int = 5 * 1024**3
    # Hosts this deployment declares to relay verbatim to official DeepSeek (a day-card
    # gateway, a local forwarder), comma separated and lower-cased.  Empty means only
    # api.deepseek.com counts as official.
    deepseek_compatible_hosts: str = ""
    # DeepSeek thinking mode for new Missions (user decision 2026-09-24: both modes supported,
    # thinking on by default).  Existing Missions keep their pool; "disabled" is the fallback.
    thinking: str = "enabled"
    # Model names the configured relay echoes for the requested model (comma separated,
    # e.g. "deepseek-ai/DeepSeek-V4.1-Flash").  Declared by the deployment, never guessed:
    # an undeclared echo keeps usage untrusted, which only ever over-counts.
    response_model_aliases: str = ""


def _thinking(value: Any) -> str:
    if not isinstance(value, str):
        return "enabled"
    normalised = value.strip().lower()
    return normalised if normalised in ("enabled", "disabled") else "enabled"


def _csv(value: Any, *, lower: bool) -> str:
    items = (
        value.split(",") if isinstance(value, str) else (value if isinstance(value, list) else [])
    )
    out = []
    for item in items:
        if isinstance(item, str) and item.strip():
            out.append(item.strip().lower() if lower else item.strip())
    return ",".join(dict.fromkeys(out))


def response_model_aliases(settings: Any) -> tuple[str, ...]:
    raw = getattr(settings, "response_model_aliases", "") or ""
    return tuple(a.strip() for a in str(raw).split(",") if a.strip())


def compatible_hosts(settings: Any) -> frozenset[str]:
    raw = getattr(settings, "deepseek_compatible_hosts", "") or ""
    return frozenset(h.strip().lower() for h in str(raw).split(",") if h.strip())


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(low, min(high, value))


def load_settings(section: Mapping[str, Any] | None) -> OrchestrationSettings:
    """Read ``[orchestration]``; unknown keys are ignored, bad values fall back."""

    raw = dict(section or {})
    base = host_settings_from_mapping(raw)
    enabled = raw.get("enabled", True)
    return OrchestrationSettings(
        max_concurrency=base.max_concurrency,
        max_concurrent_model_calls=base.max_concurrent_model_calls,
        context_input_tokens=base.context_input_tokens,
        publish_dir=base.publish_dir,
        global_monthly_max_tokens=base.global_monthly_max_tokens,
        enabled=enabled if isinstance(enabled, bool) else True,
        storage_warn_bytes=_bounded_int(
            raw.get("storage_warn_bytes"), 5 * 1024**3, 1024**2, 1024**5
        ),
        deepseek_compatible_hosts=_csv(raw.get("deepseek_compatible_hosts"), lower=True),
        thinking=_thinking(raw.get("thinking")),
        response_model_aliases=_csv(raw.get("response_model_aliases"), lower=False),
    )


__all__ = (
    "OrchestrationSettings",
    "compatible_hosts",
    "load_settings",
    "response_model_aliases",
)
