# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Service turns and provider-blocked services."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from ...contracts import (
    TERMINAL_MISSION,
    ContractError,
    Mission,
    MissionStatus,
    MissionStopReason,
    Task,
)
from ...runtime.agent_worker import Liveness
from ...runtime.model_router import RoutingUnavailable
from ...runtime.provider_budget_guard import check_unknown_handoff, resend_record
from ...storage.store import DispatchIntent
from .. import assurance_review_wait
from ..commit_service import CommitRejected
from ..hierarchical_dispatch import HierarchicalDispatch
from .core import OrchestratorCore

#: P2.3f: how long a hierarchical Mission's service turn (Planner,
#: root reviewer, Critic) may sit on a Provider hand-off whose outcome is *unknown*
#: before the loop acts — the smaller of ``stall_seconds`` and this ceiling.  The
#: runtime is right not to settle such an invocation (the request may have reached
#: the model), and it is equally right that a Mission does not spend its whole
#: deadline on one question nobody can answer: the loop hands the same request off
#: once more, and if that is unknown too it ends the round through the role's own
#: failure door.
#: 2026-10-02 用户决定：300 秒改 30 秒。原来实际生效的是 ``stall_seconds``（180 秒）——
#: 服务重启打断一次模型调用后，任务要原地不动等满三分钟才重做。结果不明的调用再等也等不
#: 来答案，30 秒足够把"慢"和"丢了"分开；真正还在进行的调用不走这条路（那是
#: ``provider_response_wait``，由 ``stall_seconds`` 管）。
MAX_SERVICE_BLOCKER_SECONDS = 30.0


class ServiceTurnMixin(OrchestratorCore):
    """Service turns and provider-blocked services."""

    __slots__ = ()

    async def _await_service_turn(  # type: ignore[no-untyped-def]
        self, intent: DispatchIntent, deadline: float, *, attempt_id: str | None
    ):
        """Dispatch a Critic intent and wait for its turn (the Critic runner's loop).

        Returns ``(intent, result)``; ``result`` is ``None`` when the wait window
        closed — or, P2.3f, when the turn was waiting on an unknown Provider outcome,
        was re-handed off once and was unknown again — and the caller's own
        "did not answer" door takes it from there.  Raises ``CommitRejected`` when the
        Critic's subject stopped meanwhile (the after-stop collector owns it then).
        """

        intent = await self._dispatch_until_submitted(intent, deadline)
        result = None
        while self.store.now < deadline:
            if self._critic_subject_stopped(intent):
                # The cycle's after-stop collector owns cancellation and cost
                # settlement from here, including after a process restart.
                await self._collect_after_stop(intent)
                raise CommitRejected("Critic subject stopped during verification")
            assert intent.agent_id and intent.expected_turn_id
            result = await self.bridge_for(intent).result(
                agent_id=intent.agent_id, turn_id=intent.expected_turn_id
            )
            if result is not None:
                break
            # P2.3f: a Critic turn waiting on an unknown Provider outcome is not "the
            # Critic thinking"; it is a question nobody is answering.  Same bound and
            # same two steps as a Planner turn — re-hand-off once, then the caller's
            # own "did not answer" door (``result is None``).
            liveness = await self.bridge_for(intent).liveness(
                agent_id=intent.agent_id, turn_id=intent.expected_turn_id
            )
            outcome = await self._resolve_provider_blocked_service(intent, liveness)
            if outcome == "rehandoff":
                refreshed = self.store.get_intent(intent.intent_id)
                assert refreshed is not None
                intent = await self._dispatch_until_submitted(refreshed, deadline)
                continue
            if outcome == "give_up":
                break
            if attempt_id is not None:
                self._hold_lease(attempt_id)  # P1-3: keep the lease while the Critic thinks
            await asyncio.sleep(self._poll)
        return intent, result

    async def _dispatch_until_submitted(
        self, intent: DispatchIntent, deadline: float
    ) -> DispatchIntent:
        """Drive one service intent through create + submit (the Critic runner's loop)."""

        while intent.state in {"PENDING", "CLAIMED", "AGENT_CREATED"}:
            if not await self._dispatch(intent):  # another owner holds the claim (P1-8)
                if self.store.now >= deadline:
                    raise ContractError("critic intent is claimed elsewhere; wait window over")
                await asyncio.sleep(self._poll)
            refreshed = self.store.get_intent(intent.intent_id)
            assert refreshed is not None
            intent = refreshed
        assert intent.agent_id and intent.expected_turn_id
        return intent

    # ------------------------------------------- P2.3f: unknown Provider outcomes
    @property
    def _service_blocker_limit(self) -> float:
        return min(float(self._config.stall_seconds), MAX_SERVICE_BLOCKER_SECONDS)

    @staticmethod
    def _provider_blocked(liveness: Liveness) -> bool:
        """The turn is waiting on a Provider hand-off whose outcome is unknown.

        Only the run's own *wait blocker* of kind ``provider`` counts — or ``tool`` whose
        effect is durably ``unknown`` (2026-09-29 真机第八局：重启打断了执行者正在做的工具操作，
        那一轮挂在"工具结果未知"上永远不结束；执行者的工具只作用在它自己的尝试工作区，放弃重做
        不会造成重复副作用）。正在执行的工具同样是 kind=tool，但状态不是 unknown，不算。  The bridge also
        reports ``provider_slot_wait`` (queued behind the concurrency limit) and
        ``provider_response_wait`` (a call that is genuinely in progress) as
        ``blocked``; both are the executor making progress and neither is timed here.
        """

        blocker = liveness.blocker
        return (
            liveness.exists
            and not liveness.settled
            and isinstance(blocker, Mapping)
            and (
                str(blocker.get("kind", "")) == "provider"
                or (
                    str(blocker.get("kind", "")) == "tool"
                    and str(blocker.get("effect_state", "")) == "unknown"
                )
            )
        )

    async def _resolve_provider_blocked_service(
        self, intent: DispatchIntent, liveness: Liveness
    ) -> str | None:
        """End the one wait the executor cannot end: a hand-off with an unknown outcome.

        P2.3f, found by the P2.3e probe episode.  The runtime hands a request off, the
        transport fails 0.2 s later, and — correctly — the invocation is settled
        UNKNOWN rather than FAILED: the request may have reached the model, and
        replaying it blindly would be a second charge for the same question.  The run
        then waits for a reconciliation observation that this deployment has nobody to
        make, and the intent stayed SUBMITTED until the caller's deadline (1800 s in
        the acceptance runner) with the Mission at PLANNING and nothing written down.

        The loop's answer is bounded and durable: after :attr:`_service_blocker_limit`
        seconds on the same blocker, the request is handed off **once more** to a new
        executor (:data:`MAX_SERVICE_REHANDOFFS`, recorded as
        ``ServiceIntentRehandedOff``); if that one is unknown too, the round ends
        through the role's own failure door — a Planner round is rejected with
        ``provider_outcome_unknown`` and the ladder decides, an assured review call
        (method review, root review) is closed as interrupted (``REVIEW_CALL_ABANDONED``)
        and reopened once in a fresh session before its package is recut, and a Critic
        turn is handed back to its runner's own "did not answer" path.  The abandoned turn's charge stays unknown in the
        runtime ledger and keeps the reservation held, which is the honest count.

        Returns ``None`` (keep waiting), ``"rehandoff"`` (a new executor is about to
        be dispatched) or ``"give_up"`` (the round was ended, or — for a Critic — is
        the runner's to end).
        """

        key = f"{intent.intent_id}:{intent.replays}"
        auth_blocked = self._definite_auth_failure(liveness.blocker)
        if not self._provider_blocked(liveness) and not auth_blocked:
            self._planning.service_blocked_since.pop(key, None)
            return None
        mission = self.store.get_mission(intent.mission_id)
        if mission is None or mission.status in TERMINAL_MISSION:
            return None

        assurance_review_wait.record_provider_wait(self.commit, intent, liveness)
        # Preserve the exact original executor, reservation and UNKNOWN
        # grants. The ordinary collector can still import a later answer.
        # Neither an auth failure nor a business timeout is accounting proof.
        # Never re-handed off here (that re-sends the original request).
        planning = intent.kind == "plan" and not self._assured_review_intent(intent)
        if intent.kind == "attempt":
            # 2026-09-26 Host run: a Worker whose hand-off came back half-read
            # (200 OK, body never finished) was settled UNKNOWN and then waited on
            # the Provider blocker for 35 minutes — nothing here ended an assured
            # Attempt.  After the same bound as a Planner round it ends as LOST, so
            # the ordinary repair/retry path runs; the UNKNOWN charge keeps its
            # reservation held (overcount, never undercount, never freeze).
            since = self._planning.service_blocked_since.setdefault(key, self.store.now)
            waited = self.store.now - since
            if waited < self._service_blocker_limit:
                return None
            self._planning.service_blocked_since.pop(key, None)
            await self._give_up_blocked_assured_attempt(
                intent,
                detail={
                    "waited_seconds": round(waited, 3),
                    "limit_seconds": self._service_blocker_limit,
                    "blocker": dict(liveness.blocker or {}),
                    "assurance_lane": True,
                },
            )
            return "give_up"
        new_mode = self._new_mode(mission) if planning else None
        if new_mode is None:
            # Reviews keep their original executor (§6.2): a review awaited inline is
            # handed back to its runner's own "did not answer" door; a plan-kind review
            # collected by the loop never reaches here (``_end_overdue_review_call``).
            return "give_up"
        # A Planner round is not a review: after the same bound as
        # P2.3f it ends through its own failure door instead of waiting for the wall
        # clock (host-final-arp10, 2026-09-24: ~17 minutes frozen).  On this lane that
        # door keeps the UNKNOWN grants and the reservation (never under-counted).
        since = self._planning.service_blocked_since.setdefault(key, self.store.now)
        waited = self.store.now - since
        if waited < self._service_blocker_limit:
            return None  # still waiting: not progress (阶段 B 裁决第 6 类)
        # 第 2 批 A27 (§6.2 "原 intent 由 runtime 核对"): the bound passed — re-read this
        # hand-off's own call key before the round is ended and the ladder asks again.

        check = self._unknown_handoff_check(intent)
        if check.verdict == "result_known" and key not in self._planning.handoff_known_waits:
            # the same call has a result now: nothing is asked again; the ordinary collector
            # imports it.  One more window only, so a turn that never wakes still ends.
            self._planning.handoff_known_waits.add(key)
            self._planning.service_blocked_since[key] = self.store.now
            self._note(
                f"{intent.subject_id}: the lost call has a result; collecting instead of re-asking"
            )
            return None
        self._planning.service_blocked_since.pop(key, None)
        self._planning.handoff_known_waits.discard(key)
        await self._give_up_blocked_plan_intent(
            intent,
            mission,
            new_mode,
            detail={
                "waited_seconds": round(waited, 3),
                "limit_seconds": self._service_blocker_limit,
                "blocker": dict(liveness.blocker or {}),
                "rehandoffs": 0,
                "assurance_lane": True,
                "handoff_check": check.to_json(),
                "resend": resend_record(check, ordinal=int(intent.config.get("ordinal", 1))),
            },
        )
        return "give_up"

    def _unknown_handoff_check(self, intent: DispatchIntent):  # type: ignore[no-untyped-def]
        """第 2 批 A27: re-read the intent's latest hand-off (same invocation, same ordinal)
        in the pool's own runtime ledger.  No runtime to read from leaves it unresolved."""

        try:
            uow = self.bridge_for(intent).runtime.uow
        except Exception:  # noqa: BLE001 - the pool is gone: nothing can be re-read
            uow = None
        return check_unknown_handoff(self.store, uow, intent_id=intent.intent_id)

    async def _give_up_blocked_plan_intent(
        self,
        intent: DispatchIntent,
        mission: Mission,
        new_mode: HierarchicalDispatch,
        *,
        detail: Mapping[str, Any],
    ) -> None:
        """The second unknown outcome ends the round through the role's own door."""

        self._import_usage(intent)  # facts of the executor that did answer, if any
        self._settle_intent(intent, "FAILED")
        self._settle_service_if_known(intent.subject_id, mission.id)
        self._note(
            f"{intent.subject_id}: unknown Provider outcome again after "
            f"{detail.get('rehandoffs')} re-hand-off(s); the round ends"
        )
        await self._planning_rejected(
            intent, reason="provider_outcome_unknown", detail=dict(detail)
        )

    async def _give_up_blocked_assured_attempt(
        self, intent: DispatchIntent, *, detail: Mapping[str, Any]
    ) -> None:
        """End an assured Worker turn stuck on an unknown Provider outcome.

        Unlike :meth:`_give_up_blocked_attempt` nothing is released: the Assurance
        lane keeps the original UNKNOWN grants and the reservation stays held,
        visible and counted, until an observation settles it (count rule 2026-09-24).
        """

        self._import_usage(intent)
        self._settle_intent(intent, "FAILED")
        attempt = self.store.get_attempt(intent.subject_id)
        if attempt is None:
            return
        self.commit.mark_attempt_lost(attempt.id, reason="provider_outcome_unknown")
        self._settle_if_known(attempt)
        await self._release_attempt(attempt.id, cancel=True)
        self._note(
            f"attempt {attempt.id} LOST: unknown Provider outcome for "
            f"{detail.get('waited_seconds')}s on the Assurance lane; reservation held"
        )

    async def _fail_runtime_unavailable(
        self,
        mission: Mission,
        *,
        reason: str,
        detail: Mapping[str, Any],
    ) -> None:
        """Named stop: MissionFailed + runtime_unavailable, never PLANNING with no reason."""

        current = self.store.get_mission(mission.id)
        if current is None or current.status in TERMINAL_MISSION:
            return
        self._dispatch_h4_repair_trigger(
            current,
            event_type="RuntimeUnavailable",
            trigger_ref=current.id,
            detail={"reason": reason, **dict(detail)},
        )
        payload = dict(detail)
        if current.status is MissionStatus.PLANNING:
            self._commit_fail_planning(
                current.id,
                reason=reason,
                detail=payload,
                stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
            )
        else:
            self._commit_fail_mission(
                current.id,
                stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
                detail={"reason": reason, **payload},
            )
        await self._release_mission(current.id)
        self._note(f"mission {current.id}: consecutive after-handoff UNKNOWN → runtime_unavailable")

    # --------------------------------------------------------------- decide
    async def _defer_for_profile(
        self, mission: Mission, task: Task, unavailable: RoutingUnavailable
    ) -> bool:
        """S6-06 (D6-5'): the profile the Task needs is cooling down and has no fallback —
        the Task waits visibly (no Attempt, the loop stays alive) for at most
        ``profile_wait_seconds`` measured on the store clock, then stops explicitly."""

        now = self.store.now
        since = self._attempts.deferred.get(task.id)
        if since is None:
            self._attempts.deferred[task.id] = now
            self._note(
                f"task {task.id}: waiting for runtime profile {unavailable.profile_id!r} "
                f"(unavailable until {unavailable.until})"
            )
            return False
        if now - since < self._config.profile_wait_seconds:
            return False
        self._attempts.deferred.pop(task.id, None)
        self._commit_stop_task(
            task.id,
            stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
            detail={
                "profile_id": unavailable.profile_id,
                "waited_seconds": round(now - since, 3),
                "profile_wait_seconds": self._config.profile_wait_seconds,
                "unavailable_until": unavailable.until,
            },
        )
        await self._release_mission(mission.id)
        self._note(
            f"task {task.id} stopped: runtime profile {unavailable.profile_id!r} unavailable "
            f"for {now - since:.1f}s"
        )
        return True
