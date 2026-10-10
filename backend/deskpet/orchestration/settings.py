# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""``config.toml [orchestration]`` and the test-only scenario gate (plan §3.3, §3.8).

There is still no *switch* for running model-written code on this machine, and there will
not be one: P3.2 replaced the question "may it run" with "has isolation proven itself
here".  At every start the SDK's capability probe runs (cached by its environment digest);
the deployment calls itself ``sandboxed`` only when all eight checks pass, and ``off``
otherwise.  This Host never uses ``process_only`` — an unisolated child process is for
trusted code, which model-written code is not.

``max_concurrency`` / ``max_concurrent_model_calls`` enter the ACTIVE policy only when
the library is first seeded; a later change of the config records ``PolicyConfigDrift``
and the ACTIVE version still governs (plan review P1-5); nothing replaces an ACTIVE
version once the library exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any



@dataclass(frozen=True)
class OrchestrationSettings:
    enabled: bool = True  # CLAUDE.md: capabilities that passed testing ship on
    # NEXT-TG-1.0 §9 多任务并发 (2026-09-29): the SDK's own defaults.  The slot count is
    # no longer part of a frozen request's admission identity (SDK opt.85), so an
    # existing library keeps starting; a smaller provider quota is set in config.toml.
    max_concurrency: int = 2
    max_concurrent_model_calls: int = 2
    tick_active_seconds: float = 2.0  # work or a turn in flight
    tick_waiting_seconds: float = 20.0  # only a person is awaited (plan review P2)
    tick_idle_seconds: float = 30.0
    lease_seconds: float = 60.0  # tests shorten it; the product does not expose it
    backoff_max_seconds: float = 60.0
    rebuild_after_failures: int = 3
    degraded_after_failures: int = 5
    # This deployment offers no Mission without bounds (native run 2026-09-12, adjudication
    # C): a blank budget item takes these.  400000 tokens is the value both HA-11 real runs
    # passed with; 12 attempts because the Mission count covers every Worker Attempt of
    # every Task (3 would fail a three-Task Mission on its first retry).
    # 2026-09-25 user decision: 20M for every Mission (a ceiling, not a charge — the
    # complex desktop ledger task spent 1.31M), and each leaf gets a fixed 1M of it
    # instead of an even share of the pool.
    # 2026-09-26 user decision: 3M per leaf.  A seven-step desktop Mission died twice on
    # a 1M leaf: one Assurance review costs 130k–270k tokens (every evidence read re-sends
    # the whole context, counted at full price) plus a 295k review reserve.
    default_mission_max_tokens: int = 20_000_000
    task_max_tokens: int = 3_000_000
    default_mission_max_attempts: int = 12
    # 第 2 批 H11（原计划 §18.2 Global Budget、§28 "多 Mission 配额"，2026-10-06）+ 车道 P（同日晚
    # 用户定）：这台机器上所有任务每个自然月合计的 token 上限（月配额）。SDK 每月一个全局总账
    # （``budget:global:YYYY-MM``，本机本地时间的月份），任务账户挂在建立当月的总账下（子不超父），
    # 任务的全部用量按任务建立月份计，月初自然是新的空账。当月用完后新任务第一轮规划就以
    # budget_exhausted（scope=global）停下，当月建立、在跑的任务同样停下；调大这个数并重启，
    # 当月下一个新任务建立时当月总账的上限随之改。任务预算超过它的新任务按 SDK 现有拒绝路径如实报
    # （invalid_request）。默认 20 亿 = 100 个默认上限（2000 万）的任务；单步 300 万不变（2026-09-26）。
    global_monthly_max_tokens: int = 2_000_000_000
    # New Missions' context window: 600K by default (user 2026-10-10; the line reads ~600K token
    # inputs completely, larger ones were accepted but not read to the end), 512K or 256K on
    # request; an existing Mission keeps the pool it was frozen on.
    context_input_tokens: int = 614_400
    # P3.2 (plan D9 / P32-14): the one directory the user authorised for published files.
    # Empty means no directory is authorised, and then nothing can be published at all —
    # the connector is not even enabled, so a Mission may not carry a publish criterion.
    publish_dir: str = ""
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
    """DeepSeek thinking mode for new Missions: ``enabled`` (default, user decision
    2026-09-24: DeepSeek is the main model and thinks by default) or ``disabled``.

    It only picks a new Mission's pool; an existing Mission keeps the pool (and mode) it
    was frozen on, so switching it back never breaks an Agent."""
    if not isinstance(value, str):
        return "enabled"
    normalised = value.strip().lower()
    return normalised if normalised in ("enabled", "disabled") else "enabled"


def _compatible_hosts(value: Any) -> str:
    items = value.split(",") if isinstance(value, str) else (value if isinstance(value, list) else [])
    hosts = []
    for item in items:
        if isinstance(item, str) and item.strip():
            hosts.append(item.strip().lower())
    return ",".join(dict.fromkeys(hosts))


def _aliases(value: Any) -> str:
    items = value.split(",") if isinstance(value, str) else (value if isinstance(value, list) else [])
    return ",".join(dict.fromkeys(item.strip() for item in items if isinstance(item, str) and item.strip()))


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
    enabled = raw.get("enabled", True)
    publish_dir = raw.get("publish_dir")
    return OrchestrationSettings(
        enabled=enabled if isinstance(enabled, bool) else True,
        max_concurrency=_bounded_int(raw.get("max_concurrency"), 2, 1, 4),
        max_concurrent_model_calls=_bounded_int(raw.get("max_concurrent_model_calls"), 2, 1, 4),
        context_input_tokens=(raw["context_input_tokens"] if type(raw.get("context_input_tokens")) is int
                              and raw["context_input_tokens"] in (262_144, 524_288) else 614_400),
        # a path only; whether it exists and can carry a hard link is decided at start-up,
        # and a directory that cannot is never authorised (P3.2 review round 2 P2-5)
        publish_dir=str(publish_dir).strip() if isinstance(publish_dir, str) else "",
        storage_warn_bytes=_bounded_int(raw.get("storage_warn_bytes"), 5 * 1024**3, 1024**2, 1024**5),
        # 下限是单任务默认上限：再小默认任务一个都建不了（§18.2 子不超父）
        global_monthly_max_tokens=_bounded_int(
            raw.get("global_monthly_max_tokens"), OrchestrationSettings.global_monthly_max_tokens,
            OrchestrationSettings.default_mission_max_tokens, 10**12,
        ),
        deepseek_compatible_hosts=_compatible_hosts(raw.get("deepseek_compatible_hosts")),
        thinking=_thinking(raw.get("thinking")),
        response_model_aliases=_aliases(raw.get("response_model_aliases")),
    )


__all__ = (
    "OrchestrationSettings",
    "load_settings",
)
