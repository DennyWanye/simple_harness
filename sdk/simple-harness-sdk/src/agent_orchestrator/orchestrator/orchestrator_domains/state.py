# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Mutable scheduling state the orchestrator keeps between cycles, grouped by concern."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..deferred_planning import DeferredPlanning

if TYPE_CHECKING:
    from ...runtime.model_router import ModelRouter
    from ...runtime.output_blocks import PortClaim
    from ...verification.critics import CriticVerdict
    from ..event_handler import Orchestrator
    from ..hierarchical_dispatch import HierarchicalDispatch


@dataclass(slots=True)
class PlanningRoundState:
    """Planner rounds waiting, blocked or stalled, per Mission."""

    # review P0-1: a Planner whose pool is cooling down waits too: mission_id → (since, ordinal)
    deferred_planning: dict[str, tuple[float, int]] = field(default_factory=DeferredPlanning)

    #: 开规划轮时证据签不出 PLAN 证书、正在等下一轮
    #: 重签的任务。这是合法等待，不判停滞；建出规划意图即清。
    #: mission → (这段连续等待开始的存储时钟时刻, 最近一次签不出的时刻)；等待上限从开始时刻算
    planning_evidence_waits: dict[str, tuple[float, float]] = field(default_factory=dict)

    #: P2.3f: when this process first saw a service turn blocked on an unknown
    #: Provider outcome, per ``intent_id:replays``.  In memory on purpose: the
    #: bound is a *wait*, and a restarted process starting the wait again costs at
    #: most one more window; the re-hand-off itself is durable (the event).
    service_blocked_since: dict[str, float] = field(default_factory=dict)

    #: blocked keys whose same-call-key re-read found a result at the bound;
    #: they get exactly one more window for the collector before the round's own door.
    handoff_known_waits: set[str] = field(default_factory=set)

    #: P2.3p: consecutive after-handoff 0-token UNKNOWNs per Mission, and the
    #: invocation ids already counted so a poll does not increment twice.
    after_handoff_zero_streak: dict[str, int] = field(default_factory=dict)

    counted_after_handoff_unknowns: set[str] = field(default_factory=set)

    #: mission id → the fingerprint of the stall just recorded for it, handed to
    #: ``_confirm_and_stop_stalled`` so the confirmation compares *this* stall
    #: against what one more cycle produces (P2.3c part 2d, decision 2).
    stalled_at: dict[str, str] = field(default_factory=dict)

    #: mission id → how many times this ``run()`` has already carried on because
    #: the stall confirmation moved the world (third-round review P1-A).
    stall_carry_ons: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class AttemptState:
    """Attempts deferred, collected, released or being verified."""

    deferred: dict[str, float] = field(default_factory=dict)

    #: result id → the output-port claims that arrived with that envelope
    #: (P2.3c part 2d, decision 4).  ``ResultEnvelope`` is a frozen contract with
    #: ``additionalProperties`` refused, so the claims are parsed out of the block
    #: and carried beside it for the rest of this cycle — the same shape
    #: ``_critic_verdicts`` above uses, and for the same reason.  The durable
    #: guard against a lost claim is ``accept_review``'s ``OUTPUT_PORT_UNCLAIMED``,
    #: which refuses rather than indexing a port nobody named.
    port_claims: dict[str, tuple[PortClaim, ...]] = field(default_factory=dict)

    # 结果块上取下的资源申请，按结果编号暂存到记录这次结果为止
    resource_requests: dict[str, Any] = field(default_factory=dict)

    # 结果块上取下的中途动作提议，同样按结果编号暂存
    action_requests: dict[str, Any] = field(default_factory=dict)

    client_ids: dict[str, str | None] = field(default_factory=dict)

    released: set[str] = field(default_factory=set)

    verifying: dict[str, asyncio.Task[bool]] = field(default_factory=dict)

    #: planning intents already noted as waiting for their TaskGraph binding.
    creation_refusals_noted: set[str] = field(default_factory=set)

    cancel_receipts: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ReviewState:
    """Critic verdicts, refusals and overdue review calls."""

    critic_verdicts: dict[str, CriticVerdict] = field(default_factory=dict)

    # result id -> (refusal signature, consecutive count); see _verdict_refused
    verdict_refusals: dict[str, tuple[str, int]] = field(default_factory=dict)

    #: 重审连续没能得出结论的次数（按（结果, 要求版本）），见 ``_carried_review``
    carried_errors: dict[str, int] = field(default_factory=dict)

    #: intent id → the refusal last noted for its collection, so a refusal that
    #: repeats every round is noted once per distinct reason (NEXT-TG-1.0 §5.1).
    # 审阅调用的等待（阶段 B 裁决第 6 类）：意图 → (等待的形态, 这一形态开始的库时钟)
    review_call_marks: dict[str, tuple[Any, float]] = field(default_factory=dict)


@dataclass(slots=True)
class MissionRoundState:
    """Per-Mission round faults, recovery isolation and scheduling marks."""

    # 一轮故障（阶段 B 裁决第 9 类）：(任务, 出事地点) → (连续次数, 第一次的库时钟)
    #: (任务, 位置) → (连续故障轮数, 第一次的时刻, 下次可以再试的时刻)
    round_faults: dict[tuple[str, str], tuple[int, float, float]] = field(default_factory=dict)

    #: Missions whose restart recovery faulted: each round retries the recovery first,
    #: inside the boundary, and skips the rest of that Mission's round until it holds.
    unrecovered: set[str] = field(default_factory=set)

    # 重启恢复第 3 步（reducer 重建）对不上的任务：本进程主循环不再处理（隔离该流），
    # 值是对不上的事实（哪些表）。下次启动重新核对。
    recovery_isolated: dict[str, Any] = field(default_factory=dict)

    #: faults caught by ``_round_boundary`` inside a global scan, settled right after it
    parked_faults: list[tuple[str, str, Exception]] = field(default_factory=list)

    #: Faults caught while binding frozen tool authority at startup, handed to the
    #: boundary by the first ``run()`` (the loop is not running yet in ``__aenter__``).
    startup_faults: list[tuple[str, str, Exception]] = field(default_factory=list)

    # 任务号 → (上次无进展一轮时的全局事件游标, 时刻)；见 _missions_due
    mission_marks: dict[str, tuple[int, float]] = field(default_factory=dict)

    rotation: int = 0

    #: Hierarchical Missions whose planning-protocol binding was read and is the
    #: one this build serves (see ``_refuse_unsupported_contract``).
    contract_checked: set[str] = field(default_factory=set)

    # 2026-09-30: finished Missions' Agents are closed in bounded, throttled sweeps
    agent_sweep_at: float | None = None

    agents_closed: set[str] = field(default_factory=set)

    progress_log: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RoutingCache:
    """Per-Mission policy and model-router caches."""

    policies: dict[str, dict[str, Any]] = field(default_factory=dict)

    routers: dict[tuple[str, str | None, str | None], ModelRouter] = field(default_factory=dict)

    route_drops: dict[str, dict[str, str]] = field(default_factory=dict)

    route_noted: set[str] = field(default_factory=set)


@dataclass(slots=True)
class CycleHooks:
    """The deployment's between-cycle duty and its pacing."""

    #: The embedding host's own duties (NEXT-TG-1.0 2B, 2A.1g): see
    #: :meth:`set_between_cycles`.
    between_cycles: Callable[[], Any] | None = None

    between_every: float = 0.0

    between_last: float = 0.0


@dataclass(slots=True)
class DeploymentWiring:
    """What a deployment installs: hierarchical, TaskGraph and Assurance hooks."""

    # P2.3b: the hierarchical assembly (§14 / §18.2 "only assemble and call").
    # ``None`` until a deployment installs one; without it nothing is planned,
    # dispatched or judged (``_assembly_missing``).
    hierarchical: HierarchicalDispatch | None = None

    planning_world_factory: Any = None

    planning_start_gate: Any = None

    mission_dispatches: dict[str, HierarchicalDispatch] = field(default_factory=dict)

    taskgraph_dispatch_setup: Any = None

    taskgraph_notifications: Any = None

    assurance_tick: Any = None

    assurance_local_checks: Any = None

    assurance_reviews: Any = None

    assurance_root_gate: Any = None

    assurance_root_setup: Callable[[Orchestrator], None] | None = None

    assurance_management_only: bool = False

    taskgraph_read_apis: list[Any] = field(default_factory=list)

    assurance_read_apis: list[Any] = field(default_factory=list)

    taskgraph_operator: Any = None

    taskgraph_policy: Any = None
