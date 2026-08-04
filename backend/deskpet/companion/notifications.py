"""Durable Companion notification projection into the ordinary message history."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, Mapping, Sequence

from deskpet.memory.companion_message_projection import (
    COMPANION_REDACTION_TOMBSTONE,
    COMPANION_REDACTION_TOMBSTONE_HASH,
    CurrentCompanionProjection,
    TrustedCompanionOwner,
    TrustedCompanionProjectionRoute,
    canonical_hash,
    canonical_json,
)

from .contracts import OwnerRef

logger = logging.getLogger(__name__)

LiveSink = Callable[[Mapping[str, Any]], Awaitable[None] | None]

_SOURCE_NOTIFICATION_EVENTS = (
    "capability_mutation_settled",
    "evaluation_reported",
    "growth_event_forgotten",
    "preference_changed",
)


@dataclass(frozen=True, slots=True)
class _NotificationDraft:
    kind: str
    summary: str
    source_refs: tuple[str, ...]
    detail: Mapping[str, Any]
    available_actions: tuple[str, ...]
    important: bool


def _owner_key(owner: OwnerRef) -> str:
    return f"{owner.profile_id}:{owner.profile_generation}"


def _trusted_owner(frozen_identity: Any) -> TrustedCompanionOwner:
    return TrustedCompanionOwner(
        profile_id=str(frozen_identity.owner.profile_id),
        profile_generation=int(frozen_identity.owner.profile_generation),
        binding_epoch=int(frozen_identity.binding_epoch),
    )


class CompanionProjectionVisibilityPort:
    """Fail-closed owner overlay used immediately before history serialization."""

    def __init__(self, service: "CompanionNotificationService") -> None:
        self._service = service

    async def project_history(
        self,
        frozen_identity: Any,
        rows: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        if not self._service.history_ready(frozen_identity):
            return []
        owner = frozen_identity.owner
        try:
            notifications = {
                str(item["notification_id"]): item
                for item in self._service.store.list_projectable_notifications(owner)
            }
        except Exception:
            return []
        events: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("projection_kind") or "") != "companion_event":
                continue
            if (
                str(row.get("projection_owner_id") or "") != owner.profile_id
                or int(row.get("projection_owner_generation") or 0)
                != owner.profile_generation
            ):
                continue
            event_id = str(row.get("projection_event_id") or "")
            current = notifications.get(event_id)
            if current is None or str(current.get("status")) == "superseded":
                continue
            try:
                current = await self._service.materialize_notification(
                    frozen_identity, current
                )
            except Exception as exc:
                logger.warning(
                    "companion_notification_materialize_failed "
                    "notification_id=%s error=%s code=%s",
                    event_id,
                    type(exc).__name__,
                    getattr(exc, "code", ""),
                )
                continue
            notification = dict(current["notification"])
            redacted = str(current.get("status")) == "redacted"
            current_projection_hash = (
                COMPANION_REDACTION_TOMBSTONE_HASH
                if redacted
                else str(current["payload_hash"])
            )
            stale_projection = (
                str(row.get("projection_payload_hash") or "")
                != current_projection_hash
            )
            if redacted or stale_projection:
                notification = {
                    "notification_id": event_id,
                    "kind": "companion_projection_tombstone",
                    "summary": "此成长记录已被遗忘。",
                    "detail_ref": None,
                    "detail_version": str(
                        notification.get("detail_version") or ""
                    ),
                    "available_actions": [],
                }
            if stale_projection:
                self._service.wake_repair(
                    frozen_identity,
                    current,
                    route_version=int(
                        row.get("projection_route_version") or 0
                    ),
                )
            events.append(
                {
                    "event_id": event_id,
                    "profile_id": owner.profile_id,
                    "profile_generation": owner.profile_generation,
                    "session_id": str(row.get("session_id") or ""),
                    "seq": int(current.get("sequence") or row.get("id") or 0),
                    "route_version": int(
                        row.get("projection_route_version") or 0
                    ),
                    "redaction_version": int(
                        current.get("redaction_version") or 0
                    ),
                    "notification": notification,
                    **(
                        {"decision": dict(current["decision"])}
                        if not (redacted or stale_projection)
                        and isinstance(current.get("decision"), Mapping)
                        else {}
                    ),
                    "tombstone": redacted or stale_projection,
                }
            )
        return events


class CompanionNotificationService:
    """At-least-once cross-database dispatcher with owner/route/hash fences."""

    def __init__(
        self,
        *,
        store: Any,
        session_db: Any,
        live_sink: LiveSink | None = None,
        detail_query: Any | None = None,
        claim_owner: str | None = None,
        lease_seconds: float = 30.0,
        digest_period: str = "daily",
        clock: Callable[[], datetime | str] | None = None,
    ) -> None:
        if digest_period not in {"daily", "weekly"}:
            raise ValueError("digest_period must be daily or weekly")
        self.store = store
        self.session_db = session_db
        self.live_sink = live_sink
        self.detail_query = detail_query
        self.claim_owner = claim_owner or f"companion-projection:{uuid.uuid4().hex}"
        self.lease_seconds = float(lease_seconds)
        self.digest_period = digest_period
        self._clock = clock
        self.visibility = CompanionProjectionVisibilityPort(self)
        self._history_open: set[str] = set()
        self._drain_locks: dict[str, asyncio.Lock] = {}

    async def materialize_notification(
        self,
        frozen_identity: Any,
        notification: Mapping[str, Any],
    ) -> dict[str, Any]:
        current = dict(notification)
        envelope = dict(current["notification"])
        if self.detail_query is not None and str(current.get("status")) != "redacted":
            envelope["detail_version"] = (
                await self.detail_query.current_detail_version(
                    frozen_identity=frozen_identity,
                    notification=current,
                )
            )
        else:
            envelope["detail_version"] = str(
                envelope.get("detail_version") or ""
            )
        current["notification"] = envelope
        detail = current.get("detail")
        if isinstance(detail, Mapping) and isinstance(
            detail.get("decision"), Mapping
        ):
            decision = dict(detail["decision"])
            nonce = str(decision.get("nonce") or "")
            with self.store.read() as db:
                consumed = db.execute(
                    """SELECT decision FROM growth_decisions
                       WHERE profile_id=? AND profile_generation=? AND nonce=?""",
                    (
                        frozen_identity.owner.profile_id,
                        frozen_identity.owner.profile_generation,
                        nonce,
                    ),
                ).fetchone()
            if consumed is not None:
                decision["status"] = (
                    "confirmed"
                    if str(consumed["decision"]) == "activate"
                    else "rejected"
                )
            elif self._parse_time(str(decision["expires_at"])) <= self._now():
                decision["status"] = "expired"
            current["decision"] = decision
        return current

    def wake_repair(
        self,
        frozen_identity: Any,
        notification: Mapping[str, Any],
        *,
        route_version: int,
    ) -> None:
        fence = canonical_hash(
            [
                notification.get("payload_hash"),
                notification.get("redaction_version"),
                int(route_version),
            ]
        )
        try:
            self.store.enqueue_projection_repair(
                frozen_identity.owner,
                notification_id=str(notification["notification_id"]),
                repair_fence=fence,
            )
        except Exception:
            return
        try:
            asyncio.get_running_loop().create_task(
                self.bind_and_drain(frozen_identity)
            )
        except RuntimeError:
            pass

    def history_ready(self, frozen_identity: Any) -> bool:
        return _owner_key(frozen_identity.owner) in self._history_open

    async def bind_and_drain(self, frozen_identity: Any) -> bool:
        """Open Companion hydration only after route and redaction repair drains."""

        owner = frozen_identity.owner
        key = _owner_key(owner)
        lock = self._drain_locks.setdefault(key, asyncio.Lock())
        self._history_open.discard(key)
        async with lock:
            try:
                ok = await self.drain(frozen_identity)
            except Exception:
                self._history_open.discard(key)
                raise
            if ok:
                self._history_open.add(key)
            return ok

    def close_history(self, owner: OwnerRef | None = None) -> None:
        if owner is None:
            self._history_open.clear()
        else:
            self._history_open.discard(_owner_key(owner))

    async def drain(self, frozen_identity: Any, *, max_items: int = 1000) -> bool:
        owner = frozen_identity.owner
        if not await self._drain_notification_sources(
            owner, max_items=max_items
        ):
            return False
        self.store.finalize_due_digest_notifications(
            owner,
            before=self._utc_text(self._now()),
        )
        for _ in range(max_items):
            route_claim = (
                await self.session_db.claim_companion_projection_route_outbox(
                    self.claim_owner,
                    self.lease_seconds,
                    profile_id=owner.profile_id,
                    profile_generation=owner.profile_generation,
                )
            )
            if route_claim is None:
                break
            try:
                self.store.enqueue_projection_reconcile(
                    owner,
                    route_version=int(route_claim["route_version"]),
                )
                await self.session_db.settle_companion_projection_route_outbox(
                    str(route_claim["outbox_id"]),
                    int(route_claim["attempt"]),
                    delivered=True,
                )
            except Exception:
                await self.session_db.settle_companion_projection_route_outbox(
                    str(route_claim["outbox_id"]),
                    int(route_claim["attempt"]),
                    retry_at=time.time() + 1.0,
                )
                raise
        else:
            return False

        for _ in range(max_items):
            claim = self.store.claim_outbox(
                owner,
                claim_owner=self.claim_owner,
                lease_seconds=self.lease_seconds,
                sink_kind="session_projection",
            )
            if claim is None:
                return True
            try:
                projected = await self._project_claim(frozen_identity, claim)
            except Exception:
                retry_at = (
                    datetime.now(UTC) + timedelta(seconds=1)
                ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                self.store.retry_outbox(
                    owner,
                    outbox_id=claim.item_id,
                    claim_owner=claim.claim_owner,
                    claim_epoch=claim.claim_epoch,
                    retry_at=retry_at,
                    reason_code="projection_execution_retry",
                )
                raise
            if not projected:
                retry_at = (
                    datetime.now(UTC) + timedelta(seconds=1)
                ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                self.store.retry_outbox(
                    owner,
                    outbox_id=claim.item_id,
                    claim_owner=claim.claim_owner,
                    claim_epoch=claim.claim_epoch,
                    retry_at=retry_at,
                    reason_code="projection_route_unavailable",
                )
                return False
        return False

    def _now(self) -> datetime:
        value = self._clock() if self._clock is not None else self.store._now()
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _utc_text(value: datetime) -> str:
        return value.astimezone(UTC).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")

    @staticmethod
    def _parse_time(value: str) -> datetime:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _digest_bucket(self, now: datetime) -> tuple[datetime, datetime]:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self.digest_period == "weekly":
            start -= timedelta(days=start.weekday())
            return start, start + timedelta(days=7)
        return start, start + timedelta(days=1)

    async def _drain_notification_sources(
        self,
        owner: OwnerRef,
        *,
        max_items: int,
    ) -> bool:
        """Materialize durable producer outbox facts before projecting cards."""

        for _ in range(max_items):
            claim = self.store.claim_outbox(
                owner,
                claim_owner=f"{self.claim_owner}:source",
                lease_seconds=self.lease_seconds,
                sink_kind="companion_notification",
                event_kinds=_SOURCE_NOTIFICATION_EVENTS,
            )
            if claim is None:
                return True
            try:
                notification = self._materialize_source_claim(owner, claim)
                result_hash = canonical_hash(
                    {
                        "source_outbox_id": claim.item_id,
                        "notification_id": notification["notification_id"],
                        "payload_hash": notification["payload_hash"],
                    }
                )
                self.store.settle_outbox(
                    owner,
                    outbox_id=claim.item_id,
                    claim_owner=claim.claim_owner,
                    claim_epoch=claim.claim_epoch,
                    delivered=True,
                    result_hash=result_hash,
                    reason_code="companion_notification_materialized",
                )
            except Exception:
                retry_at = self._utc_text(self._now() + timedelta(seconds=1))
                self.store.retry_outbox(
                    owner,
                    outbox_id=claim.item_id,
                    claim_owner=claim.claim_owner,
                    claim_epoch=claim.claim_epoch,
                    retry_at=retry_at,
                    reason_code="companion_notification_materialize_retry",
                )
                raise
        return False

    def _materialize_source_claim(
        self,
        owner: OwnerRef,
        claim: Any,
    ) -> Mapping[str, Any]:
        event_kind = str(claim.event_kind or "")
        event_id = str(claim.event_id or claim.item_id)
        draft = self._draft_for_source(
            owner=owner,
            event_kind=event_kind,
            event_id=event_id,
            payload=dict(claim.payload),
            reason_code=str(claim.reason_code or ""),
        )
        if draft.important:
            notification_id = canonical_hash(
                [
                    "important_notification",
                    owner.profile_id,
                    owner.profile_generation,
                    event_kind,
                    event_id,
                ]
            )
            return self.store.create_notification(
                owner,
                notification_id=notification_id,
                kind=draft.kind,
                source_refs=draft.source_refs,
                summary=draft.summary,
                detail=draft.detail,
                available_actions=draft.available_actions,
                reason_code="important_growth_fact_materialized",
            )

        start, end = self._digest_bucket(self._now())
        bucket_start = self._utc_text(start)
        bucket_end = self._utc_text(end)
        notification_id = canonical_hash(
            [
                "growth_digest",
                owner.profile_id,
                owner.profile_generation,
                self.digest_period,
                bucket_start,
            ]
        )
        return self.store.append_digest_notification(
            owner,
            notification_id=notification_id,
            bucket_start=bucket_start,
            bucket_end=bucket_end,
            period=self.digest_period,
            source_ref=event_id,
            item={
                "kind": draft.kind,
                "summary": draft.summary,
                "detail": dict(draft.detail),
            },
        )

    def _draft_for_source(
        self,
        *,
        owner: OwnerRef,
        event_kind: str,
        event_id: str,
        payload: Mapping[str, Any],
        reason_code: str,
    ) -> _NotificationDraft:
        reason = str(payload.get("reason_code") or reason_code or "completed")
        if event_kind == "capability_mutation_settled":
            action = str(payload.get("action") or "update")
            pack_id = str(payload.get("pack_id") or "capability")
            version = str(payload.get("version") or "")
            changed = (
                f"{pack_id}@{version}" if version else pack_id
            )
            activated = action in {"install", "update"}
            summary = (
                f"已启用能力 {changed}"
                if activated
                else f"已完成能力{action}：{changed}"
            )
            refs = [
                str(payload.get("activation_request_id") or event_id)
            ]
            if payload.get("report_id"):
                refs.append(str(payload["report_id"]))
            return _NotificationDraft(
                kind="growth_activation" if activated else "growth_rollback",
                summary=summary,
                source_refs=tuple(refs),
                detail={
                    "overview": [
                        {
                            "id": event_id,
                            "changed": changed,
                            "why": reason,
                            "evaluation_report_id": payload.get("report_id"),
                            "result_hash": payload.get("result_hash"),
                            "action": action,
                        }
                    ]
                },
                available_actions=(
                    ("view_detail", "rollback")
                    if activated
                    else ("view_detail",)
                ),
                important=True,
            )
        if event_kind == "evaluation_reported":
            verdict = str(payload.get("verdict") or "inconclusive")
            decision = (
                self._activation_decision_for_report(
                    owner,
                    report_id=str(payload.get("report_id") or event_id),
                )
                if verdict == "passed"
                else None
            )
            return _NotificationDraft(
                kind="growth_evaluation",
                summary=f"成长评测已完成：{verdict}",
                source_refs=(str(payload.get("report_id") or event_id),),
                detail={
                    "overview": [
                        {
                            "id": event_id,
                            "verdict": verdict,
                            "why": reason,
                            "results_root_hash": payload.get(
                                "results_root_hash"
                            ),
                            "candidate_package_hash": payload.get(
                                "candidate_package_hash"
                            ),
                        }
                    ],
                    **({"decision": decision} if decision is not None else {}),
                },
                available_actions=("view_detail",),
                important=True,
            )
        if event_kind == "growth_event_forgotten":
            audit_id = str(payload.get("forget_audit_id") or event_id)
            return _NotificationDraft(
                kind="growth_forget",
                summary="已遗忘所选成长记录",
                source_refs=(audit_id,),
                detail={
                    "overview": [
                        {
                            "id": audit_id,
                            "status": "forgotten",
                            "why": reason,
                        }
                    ]
                },
                available_actions=("view_detail",),
                important=True,
            )
        if event_kind == "preference_changed":
            preference_key = str(payload.get("preference_key") or "preference")
            return _NotificationDraft(
                kind="preference_update",
                summary=f"已更新偏好：{preference_key}",
                source_refs=(event_id,),
                detail={
                    "preference_key": preference_key,
                    "change": payload.get("change"),
                    "state_version": payload.get("state_version"),
                    "why": reason,
                },
                available_actions=("view_detail",),
                important=False,
            )
        raise RuntimeError(f"unsupported_companion_notification_source:{event_kind}")

    def _activation_decision_for_report(
        self,
        owner: OwnerRef,
        *,
        report_id: str,
    ) -> Mapping[str, Any] | None:
        with self.store.read() as db:
            row = db.execute(
                """SELECT a.candidate_id,a.target_owner_key,a.target_scope,
                          a.target_scope_key,
                          a.target_expected_binding_generation,
                          p.pack_id,p.version,p.candidate_content_hash,
                          p.candidate_package_hash,er.results_root_hash,
                          er.created_at,r.risk_id,r.risk_hash,r.risk,
                          r.static_preflight_json
                   FROM evaluation_reports er
                   JOIN evaluation_runs e
                     ON e.profile_id=er.profile_id
                    AND e.profile_generation=er.profile_generation
                    AND e.evaluation_id=er.evaluation_id
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id
                    AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   JOIN risk_assessments r
                     ON r.profile_id=a.profile_id
                    AND r.profile_generation=a.profile_generation
                    AND r.candidate_id=a.candidate_id
                   WHERE er.profile_id=? AND er.profile_generation=?
                     AND er.report_id=? AND er.verdict='passed'
                   ORDER BY r.created_at DESC LIMIT 1""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    report_id,
                ),
            ).fetchone()
        if row is None or str(row["risk"]) == "low":
            return None
        preflight = json.loads(str(row["static_preflight_json"]))
        candidate_kind = str(preflight.get("candidate_kind") or "unknown")
        created = self._parse_time(str(row["created_at"]))
        expires_at = self._utc_text(created + timedelta(hours=24))
        challenge_facts = {
            "candidate_id": str(row["candidate_id"]),
            "pack_id": str(row["pack_id"]),
            "candidate_version": str(row["version"]),
            "candidate_package_hash": str(row["candidate_package_hash"]),
            "evaluation_report_hash": str(row["results_root_hash"]),
            "risk_assessment_hash": str(row["risk_hash"]),
            "scope": str(row["target_scope"]),
            "scope_key": str(row["target_scope_key"]),
            "owner_key": str(row["target_owner_key"]),
            "expected_binding_generation": int(
                row["target_expected_binding_generation"]
            ),
        }
        return {
            "kind": "activation",
            **challenge_facts,
            **(
                {
                    "candidate_code_digest": str(
                        row["candidate_content_hash"]
                    )
                }
                if candidate_kind in {"code", "hook", "local_runtime"}
                else {}
            ),
            "nonce": (
                "activation-challenge:"
                + canonical_hash(
                    {
                        "schema_version": 1,
                        "report_id": report_id,
                        **challenge_facts,
                    }
                )
            ),
            "decision_version": 1,
            "expires_at": expires_at,
            "status": "open",
        }

    async def _project_claim(self, frozen_identity: Any, claim: Any) -> bool:
        owner: OwnerRef = frozen_identity.owner
        notification_id = str(claim.payload.get("notification_id") or "")
        if not notification_id:
            raise RuntimeError("projection_notification_id_missing")
        current = self.store.get_notification(owner, notification_id)
        if current is None:
            raise RuntimeError("projection_notification_missing")
        current = await self.materialize_notification(frozen_identity, current)
        trusted_owner = _trusted_owner(frozen_identity)
        route_row = await self.session_db.get_companion_projection_route(
            owner.profile_id,
            owner.profile_generation,
            int(frozen_identity.binding_epoch),
        )
        if route_row is None:
            return False
        route = TrustedCompanionProjectionRoute(
            owner=trusted_owner,
            session_id=str(route_row["target_session_id"]),
            projection_epoch=int(route_row["target_epoch"]),
            route_version=int(route_row["route_version"]),
        )
        existing = await self.session_db.get_companion_projection(
            trusted_owner, notification_id
        )
        redacted = str(current["status"]) == "redacted"
        if redacted:
            old_hash = (
                self.store.get_redaction_source_hash(owner, notification_id)
                or (
                    str(existing["projection_payload_hash"])
                    if existing is not None
                    else None
                )
            )
            if not old_hash:
                raise RuntimeError("projection_redaction_source_missing")
            projection = CurrentCompanionProjection(
                owner=trusted_owner,
                event_id=notification_id,
                payload_hash=COMPANION_REDACTION_TOMBSTONE_HASH,
                content=COMPANION_REDACTION_TOMBSTONE,
                status="redacted",
                redaction_id=canonical_hash(
                    [
                        "projection_redaction",
                        owner.profile_id,
                        owner.profile_generation,
                        notification_id,
                        int(current["redaction_version"]),
                    ]
                ),
                redaction_version=int(current["redaction_version"]),
                redacted_from_payload_hash=old_hash,
            )
        else:
            content = canonical_json(current["notification"])
            projection = CurrentCompanionProjection(
                owner=trusted_owner,
                event_id=notification_id,
                # The identity hash is the durable CompanionStore notification
                # payload hash. Composite detail versions are a read fence and
                # may change without authorizing a content transition.
                payload_hash=str(current["payload_hash"]),
                content=content,
            )

        if existing is None:
            result = await self.session_db.append_current_projection_if_absent(
                projection, route
            )
        else:
            existing_hash = str(existing["projection_payload_hash"])
            if redacted and existing_hash != COMPANION_REDACTION_TOMBSTONE_HASH:
                if existing_hash != str(projection.redacted_from_payload_hash):
                    raise RuntimeError("companion_projection_payload_hash_conflict")
                result = await self.session_db.redact_projection_if_hash(
                    owner=trusted_owner,
                    event_id=notification_id,
                    expected_payload_hash=existing_hash,
                    tombstone_payload_hash=COMPANION_REDACTION_TOMBSTONE_HASH,
                    redaction_id=str(projection.redaction_id),
                    redaction_version=int(projection.redaction_version or 0),
                )
                existing = await self.session_db.get_companion_projection(
                    trusted_owner, notification_id
                )
            elif existing_hash != projection.payload_hash:
                raise RuntimeError("companion_projection_payload_hash_conflict")
            else:
                result = {"status": "already_exists", **existing}
            if existing is not None and (
                str(existing["session_id"]) != route.session_id
                or int(existing["projection_epoch"]) != route.projection_epoch
                or int(existing["projection_route_version"]) != route.route_version
            ):
                result = await self.session_db.relocate_projection_if_epoch(
                    trusted_owner=trusted_owner,
                    projection_event_id=notification_id,
                    expected_payload_hash=str(existing["projection_payload_hash"]),
                    expected_session_id=str(existing["session_id"]),
                    expected_epoch=int(existing["projection_epoch"]),
                    expected_route_version=int(existing["projection_route_version"]),
                    new_session_id=route.session_id,
                    new_epoch=route.projection_epoch,
                    new_route_version=route.route_version,
                )

        if not redacted:
            self.store.mark_notification_projected(
                owner,
                notification_id=notification_id,
                expected_payload_hash=str(current["payload_hash"]),
            )
        await self._emit(
            {
                "type": (
                    "companion_projection_retracted"
                    if redacted
                    else "companion_event"
                ),
                "payload": self._live_payload(current, route, redacted=redacted),
            }
        )
        result_hash = canonical_hash(
            {
                "notification_id": notification_id,
                "route_version": route.route_version,
                "projection_status": str(result.get("status") or "committed"),
                "redacted": redacted,
            }
        )
        self.store.settle_outbox(
            owner,
            outbox_id=claim.item_id,
            claim_owner=claim.claim_owner,
            claim_epoch=claim.claim_epoch,
            delivered=True,
            result_hash=result_hash,
            reason_code="session_projection_committed",
        )
        return True

    @staticmethod
    def _live_payload(
        current: Mapping[str, Any],
        route: TrustedCompanionProjectionRoute,
        *,
        redacted: bool,
    ) -> dict[str, Any]:
        owner = route.owner
        base = {
            "event_id": str(current["notification_id"]),
            "profile_id": owner.profile_id,
            "profile_generation": owner.profile_generation,
            "session_id": route.session_id,
            "seq": int(current["sequence"]),
            "route_version": route.route_version,
            "redaction_version": int(current.get("redaction_version") or 0),
        }
        if redacted:
            base.update(
                {
                    "notification_id": str(current["notification_id"]),
                    "notification": {
                        "notification_id": str(current["notification_id"]),
                        "kind": "companion_projection_tombstone",
                        "summary": "此成长记录已被遗忘。",
                        "detail_ref": None,
                        "detail_version": str(
                            current["notification"].get("detail_version") or ""
                        ),
                        "available_actions": [],
                    },
                    "tombstone": True,
                }
            )
        else:
            base["notification"] = dict(current["notification"])
            if isinstance(current.get("decision"), Mapping):
                base["decision"] = dict(current["decision"])
        return base

    async def _emit(self, envelope: Mapping[str, Any]) -> None:
        if self.live_sink is None:
            return
        result = self.live_sink(envelope)
        if inspect.isawaitable(result):
            await result


__all__ = [
    "CompanionNotificationService",
    "CompanionProjectionVisibilityPort",
]
