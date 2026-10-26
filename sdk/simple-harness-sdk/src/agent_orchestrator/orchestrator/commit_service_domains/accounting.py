# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (long event / receipt literals)

"""Global quota, backpressure, resource grants, tool calls and provider profile health."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from simple_harness.contracts import canonical_json

from ...contracts import Event
from ...contracts.models import jsonable
from ...governance.budgets import AccountSnapshot, BudgetError
from ...scheduling.backpressure import (
    BACKLOG_KEY,
    STATE_KEY,
    BacklogResponse,
    BackpressureLimits,
    BackpressureState,
    Observation,
    Transition,
    evaluate,
)
from ..budget_accounts import GLOBAL_ACCOUNT_PREFIX, global_account_id, is_global_account
from ..resource_requests import LAPSED
from .core import CommitServiceCore

#: 应用注册的工具每个阶段对应的任务事件名
EXTERNAL_TOOL_EVENTS = {
    "started": "ExternalToolCallStarted",
    "succeeded": "ExternalToolCallSucceeded",
    "failed": "ExternalToolCallFailed",
    "unknown": "ExternalToolCallUnknown",
}
#: 成功结果写进事件的上限（字节）；更大的结果只在执行账本里
EXTERNAL_RESULT_LIMIT = 32 * 1024


def cooldown_started_at(
    health: Mapping[str, Any],
    events: Iterable[tuple[float, Mapping[str, Any]]],
    *,
    now: float,
) -> float:
    """Read the durable segment origin, with recovery-aware legacy event fallback."""
    if health.get("unavailable_since") is not None:
        return float(health["unavailable_since"])
    since = float(health.get("last_failure_at", now))
    recovered_at = float(health.get("recovered_at", float("-inf")))
    previous_until = float("-inf")
    for created_at, payload in events:
        until = payload.get("until")
        if until is None or created_at < recovered_at:
            continue
        if created_at > previous_until:
            since = float(payload.get("unavailable_since", created_at))
        previous_until = float(until)
    return since


class AccountingCommitsMixin(CommitServiceCore):
    """Global quota, backpressure, resource grants, tool calls and provider profile health."""

    __slots__ = ()

    # ------------------------------------------------------- global quota
    def _follow_global_quota(self, account_id: str) -> None:
        """The one rule for a Global account's limits (N3-24): they equal the deployment's
        configured monthly quota.  Must run inside a transaction."""

        if (
            self._global_budget is not None
            and self._ledger.account(account_id).limits != self._global_budget
        ):
            self._ledger.set_limits(account_id, self._global_budget)

    def _follow_global_quota_on_start(self) -> None:
        """夜间 N3-24：服务启动时，账本里已有的每个全局账户（不分月份）上限都跟上当前配置的月配额。

        不只同步当月：按任务建立月份计（车道 P），上月建立、仍在跑的任务还在上月的总账里花；
        "全局账户上限 = 当前配置的月配额"只留这一条规则，不按月份分两种对待。已用、已预留的数字
        照旧留在账上（配额调小时余额可能为负，之后的预留会被拒——多算方向）。"""

        if self._global_budget is None:
            return
        with self._store.transaction():
            rows = self._store.connection.execute(
                "SELECT account_id FROM budget_accounts WHERE account_id LIKE ? ORDER BY account_id",
                (GLOBAL_ACCOUNT_PREFIX + "%",),
            ).fetchall()
            for row in rows:
                if is_global_account(str(row[0])):
                    self._follow_global_quota(str(row[0]))

    # ----------------------------------------------------------- backpressure
    def backpressure_state(self) -> BackpressureState:
        return BackpressureState.from_json(self._store.get_scheduler_state(STATE_KEY))

    def record_backpressure(
        self,
        observation: Observation,
        *,
        limits: BackpressureLimits,
        mission_ids: Sequence[str],
    ) -> tuple[BackpressureState, list[Transition]]:
        """One evaluation step (D6-2'): the new state and, when a dimension crossed a
        watermark, the ``BackpressureRaised`` / ``BackpressureCleared`` events on every
        active Mission's timeline — state and events in one transaction (review P1-4).
        The state document keeps a bounded log of transitions: that log is the single
        truth for ``scheduler.json`` / ``metrics.json`` (review P1-11)."""

        with self._store.transaction():
            previous = BackpressureState.from_json(self._store.get_scheduler_state(STATE_KEY))
            state, transitions = evaluate(previous, observation, limits)
            raw = self._store.get_scheduler_state(STATE_KEY) or {}
            peaks = {str(k): int(v) for k, v in dict(raw.get("peaks") or {}).items()}
            new_peak = False
            for dimension in ("running_attempts", "pending_dispatch", "pending_verifications"):
                observed = observation.value(dimension)
                if observed > peaks.get(dimension, 0):  # review P2-4: the real peak
                    peaks[dimension] = observed
                    new_peak = True
            if not transitions and not new_peak and previous.observation is not None:
                return state, []
            document = state.to_json()
            document["peaks"] = peaks
            log = list(raw.get("log") or [])
            for transition in transitions:
                log.append({**transition.to_json(), "at": observation.observed_at})
            document["log"] = log[-200:]
            document["limits"] = limits.to_json()
            self._store.put_scheduler_state(STATE_KEY, document)
            for transition in transitions:
                for mission_id in mission_ids:
                    self._emit(
                        transition.event_type,
                        mission_id,
                        key=f"{transition.dimension}:{state.changes}:{mission_id}",
                        payload={**transition.to_json(), "since": state.since},
                    )
            return state, transitions

    def record_resource_grant_lapsed(
        self, mission_id: str, *, task_id: str, failed_attempt_id: str, detail: Mapping[str, Any]
    ) -> Event:
        """推后第 3 批 H10：批准了但发放时额度已不够——如实记一条，这次尝试按基础额度开工。"""

        with self._store.transaction():
            return self._emit(
                LAPSED,
                mission_id,
                key=failed_attempt_id,
                task_id=task_id,
                payload={"failed_attempt_id": failed_attempt_id, **dict(detail)},
            )

    def record_backlog_response(
        self, response: BacklogResponse, *, mission_ids: Sequence[str]
    ) -> bool:
        """推后第 3 批 H12：积压应对一变（升起、回落、暂停到期），状态与每个在跑任务时间线上的
        ``BacklogResponseChanged`` 同一个事务落库；没变什么也不写。第一次记录且没有积压时只存状态。"""

        document = response.to_json()
        with self._store.transaction():
            previous = self._store.get_scheduler_state(BACKLOG_KEY)
            if previous is not None and {key: previous.get(key) for key in document} == document:
                return False
            changes = 0 if previous is None else int(previous.get("changes", 0))
            if previous is None and response.reason == "normal":
                self._store.put_scheduler_state(BACKLOG_KEY, {**document, "changes": 0})
                return False
            changes += 1
            self._store.put_scheduler_state(BACKLOG_KEY, {**document, "changes": changes})
            before = (
                None
                if previous is None
                else {
                    key: previous.get(key)
                    for key in ("verifier_workers", "decomposition_paused", "reason")
                }
            )
            for mission_id in mission_ids:
                self._emit(
                    "BacklogResponseChanged",
                    mission_id,
                    key=f"backlog:{changes}:{mission_id}",
                    payload={**document, "previous": before},
                )
            return True

    # ------------------------------------------------------------ tool audit
    def record_tool_rejected(
        self,
        mission_id: str,
        *,
        task_id: str | None,
        attempt_id: str | None,
        run_id: str,
        call_key: str,
        record: Mapping[str, Any],
    ) -> Event:
        """§21.1 last step / §21.3 "对可疑指令进行隔离和审计": a refused tool call is
        a durable fact on the Mission's timeline (arguments reduced to their keys and
        the path, never file contents)."""

        arguments = dict(record.get("arguments") or {})
        with self._store.transaction():
            return self._emit(
                "ToolCallRejected",
                mission_id,
                key=call_key,  # review P1-3: the SDK call id — stable across a restart
                task_id=task_id,
                attempt_id=attempt_id,
                payload={
                    "tool": record.get("tool"),
                    "reason": record.get("error_code"),
                    "stage": record.get("stage"),
                    "outcome": record.get("outcome"),
                    "argument_keys": sorted(arguments),
                    "path": str(arguments["path"])[:200]  # review P2-5: bounded, never content
                    if isinstance(arguments.get("path"), str)
                    else None,
                    "run_id": run_id,
                },
            )

    def record_external_tool(
        self,
        mission_id: str,
        *,
        task_id: str | None,
        attempt_id: str | None,
        phase: str,
        record: Mapping[str, Any],
    ) -> Event:
        """应用注册的工具的一次调用在任务时间线上的事实（开始 / 成功 / 失败 / 不确定），
        消费方据此画工具卡。结果带上限：超过 ``EXTERNAL_RESULT_LIMIT`` 字节只留前半并标 truncated。"""

        call_key = str(record.get("call_key") or "")
        payload: dict[str, Any] = {
            "tool": record.get("tool"),
            "call_key": call_key,
            "run_id": record.get("run_id"),
            "argument_keys": list(record.get("argument_keys") or []),
        }
        if phase != "started":
            payload["duration_ms"] = record.get("duration_ms")
        if phase == "succeeded":
            encoded = canonical_json(dict(record.get("result") or {}))
            truncated = len(encoded.encode("utf-8")) > EXTERNAL_RESULT_LIMIT
            payload["result"] = (
                dict(record.get("result") or {})
                if not truncated
                else {
                    "_truncated_preview": encoded.encode("utf-8")[:EXTERNAL_RESULT_LIMIT].decode(
                        "utf-8", "ignore"
                    )
                }
            )
            payload["truncated"] = truncated
        elif phase in ("failed", "unknown"):
            payload["error_code"] = record.get("error_code")
            payload["error_type"] = record.get("error_type")  # 只记类型名，不记异常文字
            if "still_running" in record:
                payload["still_running"] = bool(record.get("still_running"))
        with self._store.transaction():
            return self._emit(
                EXTERNAL_TOOL_EVENTS[phase],
                mission_id,
                key=f"{call_key}:{phase}",
                task_id=task_id,
                attempt_id=attempt_id,
                payload=payload,
            )

    def record_tool_call(
        self, *, call_key: str, subject_id: str, mission_id: str, tool: str
    ) -> bool:
        """An executed tool call of an Attempt — the tool-call dimension's fact (review P1-3)."""

        return self._store.record_tool_call(
            call_key=call_key,
            subject_id=subject_id,
            mission_id=mission_id,
            tool=tool,
            outcome="succeeded",
        )

    def record_reservation_held(
        self, subject_id: str, mission_id: str, *, task_id: str | None, reason: str
    ) -> Event:
        """L3-3 (plan §6.2): a reservation an UNKNOWN charge keeps occupied is visible on the
        timeline — once per subject — and listed in ``costs.json``; never auto-released."""

        with self._store.transaction():
            reservation = self._ledger.reservation(subject_id) or {}
            return self._emit(
                "ReservationHeld",
                mission_id,
                key=subject_id,
                task_id=task_id,
                attempt_id=subject_id if task_id else None,
                payload={
                    "subject_id": subject_id,
                    "reason": reason,
                    "reserved_tokens": reservation.get("reserved_tokens"),
                },
            )

    def _profile_cooldown_events(self, profile_id: str) -> Iterable[tuple[float, Mapping[str, Any]]]:
        rows = self._store.connection.execute(
            "SELECT created_at,payload_json FROM events "
            "WHERE type='RuntimeProfileUnavailable' "
            "AND json_extract(payload_json,'$.profile_id')=? ORDER BY seq",
            (profile_id,),
        ).fetchall()
        return ((float(row[0]), json.loads(row[1])) for row in rows)

    def profile_health(self) -> dict[str, dict[str, Any]]:
        raw = self._store.get_scheduler_state(self.PROFILE_HEALTH_KEY) or {}
        profiles = {str(k): dict(v) for k, v in dict(raw.get("profiles") or {}).items()}
        for profile_id, entry in profiles.items():
            if entry.get("unavailable_until") is not None and entry.get("unavailable_since") is None:
                # Derive legacy segments from all Missions, without mutating health on a read.
                entry["unavailable_since"] = cooldown_started_at(
                    entry, self._profile_cooldown_events(profile_id), now=self._store.now
                )
        return profiles

    def unavailable_until(self) -> dict[str, float]:
        """profile → cooldown end, for the router (D6-5')."""

        return {
            pid: float(entry["unavailable_until"])
            for pid, entry in self.profile_health().items()
            if entry.get("unavailable_until") is not None
        }

    def record_profile_failure(
        self,
        profile_id: str,
        *,
        error: Mapping[str, Any] | None,
        threshold: int,
        cooldown_seconds: float,
        mission_ids: Sequence[str],
    ) -> float | None:
        """A provider-unavailable turn failure on ``profile_id``; ``threshold`` of them in
        a row put the profile into a cooldown (``RuntimeProfileUnavailable`` on every
        active Mission's timeline).  Returns the cooldown end when tripped."""

        with self._store.transaction():
            raw = self._store.get_scheduler_state(self.PROFILE_HEALTH_KEY) or {"profiles": {}}
            profiles = dict(raw.get("profiles") or {})
            entry = dict(profiles.get(profile_id) or {"failures": 0, "unavailable_until": None})
            now = self._store.now
            previous_until = entry.get("unavailable_until")
            since = now
            if previous_until is not None and float(previous_until) > now:
                legacy_events: Iterable[tuple[float, Mapping[str, Any]]] = ()
                if entry.get("unavailable_since") is None:
                    legacy_events = self._profile_cooldown_events(profile_id)
                since = cooldown_started_at(entry, legacy_events, now=now)
                entry["unavailable_since"] = since
            entry["failures"] = int(entry.get("failures", 0)) + 1
            entry["last_error"] = jsonable(error or {})
            entry["last_failure_at"] = now
            until: float | None = None
            if entry["failures"] >= max(1, threshold):
                until = now + float(cooldown_seconds)
                entry["unavailable_until"] = until
                entry["unavailable_since"] = since
                entry["trips"] = int(entry.get("trips", 0)) + 1
                entry["failures"] = 0
            profiles[profile_id] = entry
            self._store.put_scheduler_state(self.PROFILE_HEALTH_KEY, {"profiles": profiles})
            if until is not None:
                for mission_id in mission_ids:
                    self._emit(
                        "RuntimeProfileUnavailable",
                        mission_id,
                        key=f"{profile_id}:{entry['trips']}:{mission_id}",
                        payload={
                            "profile_id": profile_id,
                            "reason": "provider_unavailable",
                            "until": until,
                            "unavailable_since": since,
                            "trips": entry["trips"],
                            "last_error": entry["last_error"],
                        },
                    )
            return until

    def record_profile_success(self, profile_id: str) -> None:
        """A committed turn on ``profile_id`` closes its failure streak and any cooldown."""

        with self._store.transaction():
            raw = self._store.get_scheduler_state(self.PROFILE_HEALTH_KEY) or {"profiles": {}}
            profiles = dict(raw.get("profiles") or {})
            entry = dict(profiles.get(profile_id) or {})
            if not entry or (
                entry.get("failures", 0) == 0 and entry.get("unavailable_until") is None
            ):
                return
            entry["failures"] = 0
            entry["unavailable_until"] = None
            entry["unavailable_since"] = None
            entry["recovered_at"] = self._store.now
            profiles[profile_id] = entry
            self._store.put_scheduler_state(self.PROFILE_HEALTH_KEY, {"profiles": profiles})

    def record_profile_unavailable(
        self,
        profile_id: str,
        *,
        reason: str,
        until: float | None,
        mission_ids: Sequence[str],
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        with self._store.transaction():
            for mission_id in mission_ids:
                self._emit(
                    "RuntimeProfileUnavailable",
                    mission_id,
                    key=f"{profile_id}:{reason}:{(detail or {}).get('intent_id', '')}:{mission_id}",
                    payload={
                        "profile_id": profile_id,
                        "reason": reason,
                        "until": until,
                        **dict(detail or {}),
                    },
                )

    def global_account(self) -> AccountSnapshot | None:
        """This month's deployment-wide account (§18.2 Global Budget, a monthly quota), if this
        deployment set one and a Mission was created this month (the account opens then)."""

        if self._global_budget is None:
            return None
        with self._store.transaction():
            try:
                return self._ledger.account(global_account_id(self._store.now))
            except BudgetError:
                return None
