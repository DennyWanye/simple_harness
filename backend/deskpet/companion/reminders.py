"""Durable, owner-scoped Companion reminder core.

The service owns schedule normalization and deterministic identities.  All
mutations are delegated to :class:`CompanionStore`, so a reminder, its first
occurrence, mutation receipt, and scheduler outbox row commit atomically.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Awaitable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .clock import ClockPort, SystemClock
from .contracts import LeaseClaim, OwnerRef
from .store import canonical_hash, canonical_json


_MAX_TEXT_LENGTH = 2000
_RESULT_PREFIX = "json:"


class ReminderStorePort(Protocol):
    def mutate_reminder(self, owner: OwnerRef, **kwargs: Any) -> Mapping[str, Any]: ...

    def get_reminder_mutation_receipt(
        self,
        owner: OwnerRef,
        *,
        effect_id: str,
        request_hash: str | None = None,
    ) -> Mapping[str, Any] | None: ...

    def list_reminders(
        self,
        owner: OwnerRef,
        *,
        status: str = "active",
        cursor: str | None = None,
        limit: int = 20,
    ) -> Sequence[Mapping[str, Any]]: ...

    def list_reminder_occurrences(
        self,
        owner: OwnerRef,
        *,
        reminder_id: str | None = None,
    ) -> Sequence[Mapping[str, Any]]: ...

    def claim_due_reminder_occurrence(
        self,
        owner: OwnerRef,
        **kwargs: Any,
    ) -> LeaseClaim | None: ...

    def count_reserved_reminder_occurrences(
        self,
        owner: OwnerRef,
        *,
        window_start: str,
        window_end: str,
    ) -> int: ...

    def defer_reminder_occurrence(
        self,
        owner: OwnerRef,
        **kwargs: Any,
    ) -> Mapping[str, Any]: ...

    def authorize_reminder_occurrence_delivery(
        self,
        owner: OwnerRef,
        **kwargs: Any,
    ) -> Mapping[str, Any]: ...

    def settle_reminder_occurrence_delivery(
        self,
        owner: OwnerRef,
        **kwargs: Any,
    ) -> Mapping[str, Any]: ...

    def claim_outbox(
        self,
        owner: OwnerRef,
        **kwargs: Any,
    ) -> LeaseClaim | None: ...

    def get_job(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
    ) -> Mapping[str, Any]: ...


class ReminderServicePort(Protocol):
    def create(
        self,
        authority: "ReminderRunAuthority",
        *,
        effect_id: str,
        args: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def list(
        self,
        authority: "ReminderRunAuthority",
        *,
        args: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def cancel(
        self,
        authority: "ReminderRunAuthority",
        *,
        effect_id: str,
        args: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ReminderSchedulerPolicy:
    proactive_enabled: bool = True
    paused: bool = False
    timezone: str = "Asia/Shanghai"
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "08:00"
    daily_frequency_limit: int = 20
    max_lateness_seconds: int = 900
    policy_retry_seconds: int = 60

    def __post_init__(self) -> None:
        _zone(self.timezone)
        _parse_hhmm(self.quiet_hours_start, "quiet_start")
        _parse_hhmm(self.quiet_hours_end, "quiet_end")
        if (
            self.daily_frequency_limit < 0
            or self.max_lateness_seconds < 0
            or self.policy_retry_seconds <= 0
        ):
            raise ValueError("reminder_scheduler_policy_invalid")


class ReminderPolicyProviderPort(Protocol):
    def snapshot(
        self,
        owner: OwnerRef,
    ) -> ReminderSchedulerPolicy | Awaitable[ReminderSchedulerPolicy]: ...


@dataclass(frozen=True, slots=True)
class ReminderDispatch:
    occurrence_claim: LeaseClaim
    outbox_id: str
    draft_job_id: str | None


@dataclass(frozen=True, slots=True)
class ReminderRunAuthority:
    """Host-issued facts which model arguments are forbidden to supply."""

    owner: OwnerRef
    timezone: str
    quiet_policy: Mapping[str, Any]
    source_message_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _zone(self.timezone)
        refs = tuple(
            _required_text(item, "source_message_ref", 512)
            for item in self.source_message_refs
        )
        if len(set(refs)) != len(refs):
            raise ValueError("reminder_source_message_ref_duplicate")
        object.__setattr__(self, "source_message_refs", refs)
        object.__setattr__(
            self,
            "quiet_policy",
            _normalize_quiet_policy(self.quiet_policy, default_timezone=self.timezone),
        )


def reminder_id_for(owner: OwnerRef, effect_id: str) -> str:
    return canonical_hash(
        [
            "reminder_v2",
            owner.profile_id,
            owner.profile_generation,
            _required_text(effect_id, "effect_id", 512),
        ]
    )


def occurrence_id_for(reminder_id: str, due_at: str) -> str:
    return canonical_hash(
        [
            _required_text(reminder_id, "reminder_id", 128),
            _required_text(due_at, "due_at", 64),
        ]
    )


def _required_text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name}_invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name}_invalid")
    return normalized


def _zone(value: object) -> ZoneInfo:
    name = _required_text(value, "timezone", 128)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("reminder_timezone_invalid") from exc


def _parse_hhmm(value: object, name: str) -> time:
    raw = _required_text(value, name, 5)
    try:
        parsed = datetime.strptime(raw, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"{name}_invalid") from exc
    return parsed


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("reminder_due_at_naive")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_datetime(value: object, name: str) -> datetime:
    raw = _required_text(value, name, 64)
    normalized = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{name}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name}_timezone_required")
    return parsed.astimezone(UTC)


def _normalize_local(local_date: date, local_time: time, zone: ZoneInfo) -> datetime:
    """Return a real local instant, deferring DST gaps via UTC round-trip."""

    candidate = datetime.combine(local_date, local_time, tzinfo=zone).replace(fold=0)
    return candidate.astimezone(UTC).astimezone(zone)


def _normalize_quiet_policy(
    raw: Mapping[str, Any] | None,
    *,
    default_timezone: str,
) -> dict[str, Any]:
    policy = dict(raw or {})
    enabled = bool(policy.get("enabled", False))
    if not enabled:
        return {
            "enabled": False,
            "mode": "defer",
            "timezone": default_timezone,
        }
    extras = set(policy) - {"enabled", "mode", "start", "end", "timezone"}
    if extras:
        raise ValueError("reminder_quiet_policy_extra_fields")
    mode = str(policy.get("mode") or "defer")
    if mode != "defer":
        raise ValueError("reminder_quiet_policy_mode_invalid")
    start = _parse_hhmm(policy.get("start"), "quiet_start")
    end = _parse_hhmm(policy.get("end"), "quiet_end")
    if start == end:
        raise ValueError("reminder_quiet_policy_window_invalid")
    timezone = str(policy.get("timezone") or default_timezone)
    _zone(timezone)
    return {
        "enabled": True,
        "mode": "defer",
        "start": start.strftime("%H:%M"),
        "end": end.strftime("%H:%M"),
        "timezone": timezone,
    }


def _defer_for_quiet_hours(
    due_utc: datetime,
    policy: Mapping[str, Any],
) -> datetime:
    if not policy.get("enabled"):
        return due_utc.astimezone(UTC)
    zone = _zone(policy["timezone"])
    start = _parse_hhmm(policy["start"], "quiet_start")
    end = _parse_hhmm(policy["end"], "quiet_end")
    local = due_utc.astimezone(zone)
    current = local.timetz().replace(tzinfo=None)
    if start < end:
        inside = start <= current < end
        target_date = local.date()
    else:
        inside = current >= start or current < end
        target_date = local.date() + (
            timedelta(days=1) if current >= start else timedelta()
        )
    if not inside:
        return due_utc.astimezone(UTC)
    return _normalize_local(target_date, end, zone).astimezone(UTC)


def _policy_quiet_end(
    now_utc: datetime,
    *,
    timezone: str,
    start_text: str,
    end_text: str,
) -> datetime | None:
    zone = _zone(timezone)
    start = _parse_hhmm(start_text, "quiet_start")
    end = _parse_hhmm(end_text, "quiet_end")
    if start == end:
        return None
    local = now_utc.astimezone(zone)
    current = local.timetz().replace(tzinfo=None)
    if start < end:
        inside = start <= current < end
        target_date = local.date()
    else:
        inside = current >= start or current < end
        target_date = local.date() + (
            timedelta(days=1) if current >= start else timedelta()
        )
    if not inside:
        return None
    return _normalize_local(target_date, end, zone).astimezone(UTC)


def _first_due(
    schedule: Mapping[str, Any],
    *,
    now_utc: datetime,
) -> tuple[dict[str, Any], str, datetime]:
    if not isinstance(schedule, Mapping):
        raise ValueError("reminder_schedule_invalid")
    kind = schedule.get("kind")
    if kind == "once":
        if set(schedule) != {"kind", "at_utc"}:
            raise ValueError("reminder_once_schedule_invalid")
        due = _parse_datetime(schedule["at_utc"], "at_utc")
        if due <= now_utc:
            raise ValueError("reminder_due_at_not_future")
        return {"kind": "once", "at_utc": _utc_text(due)}, "UTC", due
    if kind != "weekly":
        raise ValueError("reminder_schedule_kind_invalid")
    if set(schedule) != {"kind", "weekday", "local_time", "timezone"}:
        raise ValueError("reminder_weekly_schedule_invalid")
    weekday = schedule.get("weekday")
    if (
        isinstance(weekday, bool)
        or not isinstance(weekday, int)
        or not 1 <= weekday <= 7
    ):
        raise ValueError("reminder_weekday_invalid")
    local_time = _parse_hhmm(schedule.get("local_time"), "local_time")
    timezone = _required_text(schedule.get("timezone"), "timezone", 128)
    zone = _zone(timezone)
    local_now = now_utc.astimezone(zone)
    days = (weekday - local_now.isoweekday()) % 7
    due_local = _normalize_local(
        local_now.date() + timedelta(days=days),
        local_time,
        zone,
    )
    due = due_local.astimezone(UTC)
    if due <= now_utc:
        due_local = _normalize_local(
            local_now.date() + timedelta(days=days + 7),
            local_time,
            zone,
        )
        due = due_local.astimezone(UTC)
    return {
        "kind": "weekly",
        "weekday": weekday,
        "local_time": local_time.strftime("%H:%M"),
        "timezone": timezone,
    }, timezone, due


def _inline_result(result: Mapping[str, Any]) -> str:
    return _RESULT_PREFIX + canonical_json(dict(result))


def _read_inline_result(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = str(receipt.get("result_payload_ref") or "")
    if not raw.startswith(_RESULT_PREFIX):
        raise RuntimeError("reminder_result_payload_unavailable")
    payload = json.loads(raw[len(_RESULT_PREFIX) :])
    if not isinstance(payload, dict) or canonical_hash(payload) != receipt["result_hash"]:
        raise RuntimeError("reminder_result_payload_corrupt")
    return payload


class ReminderService:
    def __init__(
        self,
        store: ReminderStorePort,
        *,
        clock: ClockPort | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or SystemClock()

    def recover_mutation(
        self,
        authority: ReminderRunAuthority,
        *,
        effect_id: str,
        request_hash: str,
    ) -> Mapping[str, Any] | None:
        receipt = self._store.get_reminder_mutation_receipt(
            authority.owner,
            effect_id=effect_id,
            request_hash=request_hash,
        )
        return None if receipt is None else _read_inline_result(receipt)

    def create(
        self,
        authority: ReminderRunAuthority,
        *,
        effect_id: str,
        args: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        normalized_args = _validate_create_args(args)
        request_hash = canonical_hash(normalized_args)
        replay = self.recover_mutation(
            authority,
            effect_id=effect_id,
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        now = self._clock.now_utc().astimezone(UTC)
        schedule_spec, schedule_timezone, due = _first_due(
            normalized_args["schedule"],
            now_utc=now,
        )
        if schedule_spec["kind"] == "once":
            schedule_timezone = authority.timezone
        due = _defer_for_quiet_hours(due, authority.quiet_policy)
        due_at = _utc_text(due)
        reminder_id = reminder_id_for(authority.owner, effect_id)
        occurrence_id = occurrence_id_for(reminder_id, due_at)
        stored_schedule = {
            "schema_version": 1,
            "spec": schedule_spec,
            "text": normalized_args["text"],
            "prepare_draft": normalized_args["prepare_draft"],
            "source_message_refs": list(authority.source_message_refs),
            "next_due_at": due_at,
        }
        result = {
            "ok": True,
            "operation": "create",
            "reminder_id": reminder_id,
            "schedule_version": 1,
            "next_due_at": due_at,
            "timezone": schedule_timezone,
        }
        result_hash = canonical_hash(result)
        self._store.mutate_reminder(
            authority.owner,
            effect_id=effect_id,
            operation_kind="create",
            args=normalized_args,
            request_hash=request_hash,
            reminder_id=reminder_id,
            before_schedule_version=None,
            schedule=stored_schedule,
            timezone=schedule_timezone,
            quiet_policy=authority.quiet_policy,
            result_payload_ref=_inline_result(result),
            result_hash=result_hash,
            reason_code="reminder_create",
            occurrence_id=occurrence_id,
            due_at=due_at,
        )
        return result

    def list(
        self,
        authority: ReminderRunAuthority,
        *,
        args: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        status, cursor, limit = _validate_list_args(args)
        rows = self._store.list_reminders(
            authority.owner,
            status=status,
            cursor=cursor,
            limit=limit + 1,
        )
        page = list(rows[:limit])
        has_more = len(rows) > limit
        items = []
        for row in page:
            schedule = json.loads(str(row["schedule_json"]))
            items.append(
                {
                    "reminder_id": row["reminder_id"],
                    "status": row["status"],
                    "schedule_version": int(row["schedule_version"]),
                    "timezone": row["timezone"],
                    "text": schedule["text"],
                    "schedule": schedule["spec"],
                    "next_due_at": schedule.get("next_due_at"),
                    "prepare_draft": bool(schedule.get("prepare_draft", False)),
                }
            )
        return {
            "ok": True,
            "items": items,
            "next_cursor": str(page[-1]["reminder_id"]) if has_more and page else None,
        }

    def cancel(
        self,
        authority: ReminderRunAuthority,
        *,
        effect_id: str,
        args: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        normalized_args = _validate_cancel_args(args)
        request_hash = canonical_hash(normalized_args)
        replay = self.recover_mutation(
            authority,
            effect_id=effect_id,
            request_hash=request_hash,
        )
        if replay is not None:
            return replay
        before = normalized_args["expected_schedule_version"]
        result = {
            "ok": True,
            "operation": "cancel",
            "reminder_id": normalized_args["reminder_id"],
            "before_schedule_version": before,
            "schedule_version": before + 1,
            "status": "cancelled",
        }
        self._store.mutate_reminder(
            authority.owner,
            effect_id=effect_id,
            operation_kind="cancel",
            args=normalized_args,
            request_hash=request_hash,
            reminder_id=normalized_args["reminder_id"],
            before_schedule_version=before,
            schedule={},
            timezone=authority.timezone,
            quiet_policy=authority.quiet_policy,
            result_payload_ref=_inline_result(result),
            result_hash=canonical_hash(result),
            reason_code="reminder_cancel",
        )
        return result


class ReminderScheduler:
    """Dormant two-check scheduler for durable local reminder delivery."""

    def __init__(
        self,
        store: ReminderStorePort,
        *,
        policy_provider: ReminderPolicyProviderPort,
        clock: ClockPort | None = None,
        lease_seconds: float = 30.0,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("reminder_scheduler_lease_invalid")
        self._store = store
        self._policy_provider = policy_provider
        self._clock = clock or SystemClock()
        self._lease_seconds = lease_seconds

    async def _policy(self, owner: OwnerRef) -> ReminderSchedulerPolicy:
        policy = self._policy_provider.snapshot(owner)
        if inspect.isawaitable(policy):
            policy = await policy
        if not isinstance(policy, ReminderSchedulerPolicy):
            raise RuntimeError("reminder_scheduler_policy_unavailable")
        return policy

    @staticmethod
    def _window(now_utc: datetime, policy: ReminderSchedulerPolicy) -> tuple[str, str]:
        zone = _zone(policy.timezone)
        local = now_utc.astimezone(zone)
        start_local = datetime.combine(local.date(), time.min, tzinfo=zone)
        end_local = start_local + timedelta(days=1)
        return _utc_text(start_local), _utc_text(end_local)

    async def tick(
        self,
        owner: OwnerRef,
        *,
        claim_owner: str,
    ) -> ReminderDispatch | None:
        now = self._clock.now_utc().astimezone(UTC)
        schedule_policy = await self._policy(owner)
        if not schedule_policy.proactive_enabled or schedule_policy.paused:
            return None
        if (
            _policy_quiet_end(
                now,
                timezone=schedule_policy.timezone,
                start_text=schedule_policy.quiet_hours_start,
                end_text=schedule_policy.quiet_hours_end,
            )
            is not None
        ):
            return None
        window_start, window_end = self._window(now, schedule_policy)
        claim = self._store.claim_due_reminder_occurrence(
            owner,
            claim_owner=claim_owner,
            lease_seconds=self._lease_seconds,
            due_at_or_before=_utc_text(now),
            expire_before=_utc_text(
                now - timedelta(seconds=schedule_policy.max_lateness_seconds)
            ),
            frequency_window_start=window_start,
            frequency_window_end=window_end,
            frequency_limit=schedule_policy.daily_frequency_limit,
        )
        if claim is None:
            return None

        # Policy is intentionally reacquired after the durable claim.  A pause,
        # frequency change, or quiet-window change cannot ride a stale check
        # into the physical delivery outbox.
        delivery_policy = await self._policy(owner)
        quiet_end = _policy_quiet_end(
            now,
            timezone=delivery_policy.timezone,
            start_text=delivery_policy.quiet_hours_start,
            end_text=delivery_policy.quiet_hours_end,
        )
        reminder_quiet_end = _quiet_policy_end(
            now,
            claim.payload.get("quiet_policy", {}),
        )
        delivery_window_start, delivery_window_end = self._window(
            now,
            delivery_policy,
        )
        reserved_deliveries = self._store.count_reserved_reminder_occurrences(
            owner,
            window_start=delivery_window_start,
            window_end=delivery_window_end,
        )
        if (
            not delivery_policy.proactive_enabled
            or delivery_policy.paused
            or delivery_policy.daily_frequency_limit <= 0
            or reserved_deliveries > delivery_policy.daily_frequency_limit
            or quiet_end is not None
            or reminder_quiet_end is not None
        ):
            retry = max(
                (
                    value
                    for value in (
                        quiet_end,
                        reminder_quiet_end,
                        now
                        + timedelta(seconds=delivery_policy.policy_retry_seconds),
                    )
                    if value is not None
                ),
                default=now
                + timedelta(seconds=delivery_policy.policy_retry_seconds),
            )
            self._store.defer_reminder_occurrence(
                owner,
                occurrence_id=claim.item_id,
                claim_owner=claim.claim_owner,
                claim_epoch=claim.claim_epoch,
                retry_at=_utc_text(retry),
                reason_code="reminder_delivery_policy_deferred",
            )
            return None

        schedule = dict(claim.payload["schedule"])
        text = _required_text(schedule.get("text"), "reminder_text", _MAX_TEXT_LENGTH)
        prepare_draft = bool(schedule.get("prepare_draft", False))
        draft_payload = None
        # The grant is part of the immutable draft job payload.  Derive its
        # expiry from the durable occurrence, not the scheduler attempt time,
        # so a lease-expiry replay produces the identical payload hash.
        grant_expires_at = _utc_text(
            _parse_datetime(claim.payload["due_at"], "due_at")
            + timedelta(days=1)
        )
        if prepare_draft:
            source_message_refs = tuple(
                _required_text(item, "source_message_ref", 512)
                for item in schedule.get("source_message_refs", ())
            )
            grant_target = f"reminder:{claim.item_id}"
            delegated_grants = [
                {
                    "grant_id": canonical_hash(
                        ["reminder_draft_grant", claim.item_id, scope]
                    ),
                    "scope": scope,
                    "target": grant_target,
                    "expires_at": grant_expires_at,
                    "version": 1,
                }
                for scope in ("read", "draft", "reversible_local")
            ]
            draft_payload = {
                "schema_version": 1,
                "purpose": "delegated_task",
                "owner_key": (
                    f"companion:{owner.profile_id}:{owner.profile_generation}"
                ),
                "evidence_ids": list(source_message_refs),
                "delegated_grants": delegated_grants,
                "text": (
                    "请只生成一份非空的本地提醒草稿，不要调用工具，不要发送、"
                    "发布或提交任何内容。仅返回草稿正文。\n"
                    f"提醒：{text}\n"
                    "已冻结来源引用："
                    + json.dumps(
                        list(source_message_refs),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                ),
                "request_payload": {
                    "companion_stage": "reminder_draft",
                    "operation": "prepare_reminder_draft",
                    "reminder_id": claim.payload["reminder_id"],
                    "occurrence_id": claim.item_id,
                    "reminder_text": text,
                    "source_message_refs": list(source_message_refs),
                    "allowed_grant_scopes": [
                        "read",
                        "draft",
                        "reversible_local",
                    ],
                    "external_send_allowed": False,
                },
                "requires_idle": True,
            }
        prepared = self._store.authorize_reminder_occurrence_delivery(
            owner,
            occurrence_id=claim.item_id,
            claim_owner=claim.claim_owner,
            claim_epoch=claim.claim_epoch,
            delivery_payload={
                "schema_version": 1,
                "occurrence_id": claim.item_id,
                "reminder_id": claim.payload["reminder_id"],
                "due_at": claim.payload["due_at"],
                "text": text,
                "external_send_allowed": False,
            },
            draft_payload=draft_payload,
            grant_expires_at=grant_expires_at,
        )
        return ReminderDispatch(
            occurrence_claim=claim,
            outbox_id=str(prepared["outbox_id"]),
            draft_job_id=(
                None
                if prepared["draft_job_id"] is None
                else str(prepared["draft_job_id"])
            ),
        )

    def claim_delivery_outbox(
        self,
        owner: OwnerRef,
        *,
        claim_owner: str,
    ) -> LeaseClaim | None:
        return self._store.claim_outbox(
            owner,
            claim_owner=claim_owner,
            lease_seconds=self._lease_seconds,
            sink_kind="companion_notification",
        )

    def settle(
        self,
        owner: OwnerRef,
        *,
        dispatch: ReminderDispatch,
        outbox_claim: LeaseClaim,
        result_hash: str,
    ) -> Mapping[str, Any]:
        claim = dispatch.occurrence_claim
        schedule = dict(claim.payload["schedule"])
        settlement_hash = result_hash
        draft: Mapping[str, Any] | None = None
        if dispatch.draft_job_id is not None:
            draft = _durable_reminder_draft(
                self._store,
                owner,
                draft_job_id=dispatch.draft_job_id,
                occurrence_id=claim.item_id,
                reminder_id=str(claim.payload["reminder_id"]),
                source_message_refs=tuple(
                    str(item)
                    for item in schedule.get("source_message_refs", ())
                ),
            )
            settlement_hash = canonical_hash(
                {
                    "schema_version": 1,
                    "projection_result_hash": result_hash,
                    "draft_result_hash": draft["result_hash"],
                }
            )
        spec = dict(schedule["spec"])
        next_occurrence_id = None
        next_due_at = None
        next_schedule = None
        if spec.get("kind") == "weekly":
            _normalized, _timezone, due = _first_due(
                spec,
                now_utc=_parse_datetime(claim.payload["due_at"], "due_at"),
            )
            due = _defer_for_quiet_hours(
                due,
                claim.payload.get("quiet_policy", {}),
            )
            next_due_at = _utc_text(due)
            next_occurrence_id = occurrence_id_for(
                str(claim.payload["reminder_id"]),
                next_due_at,
            )
            next_schedule = {**schedule, "next_due_at": next_due_at}
        return self._store.settle_reminder_occurrence_delivery(
            owner,
            occurrence_id=claim.item_id,
            claim_owner=claim.claim_owner,
            claim_epoch=claim.claim_epoch,
            outbox_id=dispatch.outbox_id,
            outbox_claim_owner=outbox_claim.claim_owner,
            outbox_claim_epoch=outbox_claim.claim_epoch,
            result_hash=settlement_hash,
            draft_payload=draft,
            source_message_refs=tuple(
                str(item)
                for item in schedule.get("source_message_refs", ())
            ),
            next_occurrence_id=next_occurrence_id,
            next_due_at=next_due_at,
            next_schedule=next_schedule,
        )


def _durable_reminder_draft(
    store: ReminderStorePort,
    owner: OwnerRef,
    *,
    draft_job_id: str,
    occurrence_id: str,
    reminder_id: str,
    source_message_refs: tuple[str, ...],
) -> Mapping[str, Any]:
    """Load and validate the draft job's durable settlement before delivery."""

    job = store.get_job(owner, job_id=draft_job_id)
    if str(job.get("status") or "") != "succeeded":
        raise RuntimeError("reminder_draft_not_durable")
    result_ref = str(job.get("result_ref") or "")
    if not result_ref.startswith(_RESULT_PREFIX):
        raise RuntimeError("reminder_draft_result_ref_invalid")
    try:
        body = json.loads(result_ref[len(_RESULT_PREFIX) :])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("reminder_draft_result_ref_invalid") from exc
    if not isinstance(body, Mapping):
        raise RuntimeError("reminder_draft_result_invalid")
    draft_text = str(body.get("text") or "").strip()
    raw_refs = body.get("source_message_refs")
    if isinstance(raw_refs, (str, bytes)) or not isinstance(raw_refs, Sequence):
        raise RuntimeError("reminder_draft_source_refs_invalid")
    if (
        body.get("schema_version") != 1
        or body.get("kind") != "reminder_draft"
        or str(body.get("occurrence_id") or "") != occurrence_id
        or str(body.get("reminder_id") or "") != reminder_id
        or tuple(str(item) for item in raw_refs) != source_message_refs
        or not draft_text
        or body.get("external_send_allowed") is not False
        or body.get("external_send_count") != 0
    ):
        raise RuntimeError("reminder_draft_result_invalid")
    result_hash = canonical_hash(dict(body))
    if str(job.get("result_hash") or "") != result_hash:
        raise RuntimeError("reminder_draft_result_hash_invalid")
    return {**dict(body), "result_hash": result_hash}


def _quiet_policy_end(
    now_utc: datetime,
    policy: Mapping[str, Any],
) -> datetime | None:
    if not policy.get("enabled"):
        return None
    return _policy_quiet_end(
        now_utc,
        timezone=str(policy["timezone"]),
        start_text=str(policy["start"]),
        end_text=str(policy["end"]),
    )


def _validate_create_args(args: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(args, Mapping) or set(args) - {"text", "schedule", "prepare_draft"}:
        raise ValueError("reminder_create_arguments_invalid")
    text = _required_text(args.get("text"), "reminder_text", _MAX_TEXT_LENGTH)
    prepare_draft = args.get("prepare_draft", False)
    if not isinstance(prepare_draft, bool):
        raise ValueError("reminder_prepare_draft_invalid")
    schedule = args.get("schedule")
    if not isinstance(schedule, Mapping):
        raise ValueError("reminder_schedule_invalid")
    return {
        "text": text,
        "schedule": dict(schedule),
        "prepare_draft": prepare_draft,
    }


def _validate_list_args(args: Mapping[str, Any]) -> tuple[str, str | None, int]:
    if not isinstance(args, Mapping) or set(args) - {"status", "cursor", "limit"}:
        raise ValueError("reminder_list_arguments_invalid")
    status = args.get("status", "active")
    if status not in {"active", "all"}:
        raise ValueError("reminder_status_filter_invalid")
    cursor = args.get("cursor")
    if cursor is not None:
        cursor = _required_text(cursor, "reminder_cursor", 256)
    limit = args.get("limit", 20)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("reminder_limit_invalid")
    return str(status), cursor, limit


def _validate_cancel_args(args: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(args, Mapping) or set(args) != {
        "reminder_id",
        "expected_schedule_version",
    }:
        raise ValueError("reminder_cancel_arguments_invalid")
    reminder_id = _required_text(args.get("reminder_id"), "reminder_id", 128)
    version = args.get("expected_schedule_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("reminder_schedule_version_invalid")
    return {
        "reminder_id": reminder_id,
        "expected_schedule_version": version,
    }


__all__ = [
    "ReminderDispatch",
    "ReminderPolicyProviderPort",
    "ReminderRunAuthority",
    "ReminderScheduler",
    "ReminderSchedulerPolicy",
    "ReminderService",
    "ReminderServicePort",
    "ReminderStorePort",
    "occurrence_id_for",
    "reminder_id_for",
]
