"""历史事件与伴侣收件箱：工作流与伴侣历史事件附加、伴侣收件箱路由绑定与投影结算"""

from __future__ import annotations

import asyncio
import uuid

from deskpet.companion.companion_message_projection import TrustedCompanionOwner
from deskpet.execution.contracts import EventNotFound
from deskpet.workflows.harness_delivery import workflow_event_envelope

from host.runtime import logger, service_context
from host.state import host_state


async def _attach_workflow_history_events(
    *,
    rows: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    session_id: str,
    session_db: Any,
    execution_uow: Any,
) -> None:
    """Attach fenced durable envelopes from the execution ledger to history messages in place.

    会话库里引用的事件号只在执行账本（``execution_events``）里查；查不到的
    （后台旧工作流引擎时代的旧引用）不附带。
    """

    event_ids = list(
        dict.fromkeys(
            str(row.get("workflow_event_id") or "").strip()
            for row in rows
            if str(row.get("workflow_event_id") or "").strip()
        )
    )
    if not event_ids or execution_uow is None:
        return
    delivery_state = await session_db.get_session_delivery_state(session_id)
    if delivery_state.get("deleted_at") is not None:
        return

    events_by_id: dict[str, dict[str, Any]] = {}
    for event_id in event_ids:
        try:
            canonical_event = await execution_uow.get_event(event_id)
        except EventNotFound:
            continue
        if canonical_event.session_id != session_id:
            continue
        envelope = workflow_event_envelope(canonical_event)
        if envelope.get("event_type") == "workflow.decision":
            payload = envelope.get("payload")
            decision_id = str((payload or {}).get("decision_id") or "")
            if decision_id:
                decision = await execution_uow.get_decision_projection(
                    decision_id,
                    expected_run_id=canonical_event.run_id,
                    expected_session_id=session_id,
                )
                if isinstance(payload, dict):
                    payload["status"] = decision.status.value
                    payload["version"] = decision.decision_version
        events_by_id[event_id] = envelope
    for message in messages:
        event = events_by_id.get(str(message.get("id") or ""))
        if event is not None:
            message["workflow_event"] = event


async def _bind_companion_inbox_route(
    session_id: str,
    frozen_identity: Any,
    *,
    create_if_absent: bool,
) -> bool:
    """Bind a new empty task session and make it the current profile inbox."""

    session_db = service_context.get("session_db")
    if session_db is None:
        return False

    trusted_owner = TrustedCompanionOwner(
        profile_id=frozen_identity.owner.profile_id,
        profile_generation=frozen_identity.owner.profile_generation,
        binding_epoch=frozen_identity.binding_epoch,
    )
    try:
        if create_if_absent:
            await session_db.ensure_session(session_id)
            await session_db.bind_session_owner_if_absent(session_id, trusted_owner)
        await session_db.set_companion_default_route(
            trusted_owner.profile_id,
            trusted_owner.profile_generation,
            trusted_owner.binding_epoch,
            session_id,
        )
    except RuntimeError as exc:
        if str(exc) in {
            "companion_session_missing",
            "companion_legacy_session_cannot_be_claimed",
            "companion_route_target_fence_mismatch",
        }:
            return False
        raise
    if host_state.companion_notification_service is not None:
        await host_state.companion_notification_service.bind_and_drain(frozen_identity)
    return True


# 2026-09-09 事件 AK：同 userdata 重启后 UI 停在「等待主对话就绪」。
# 前端唯一的就绪输入是 `companion_profile_bound`
# （`tauri-app/src/primary/controller.ts:148-158`），而这一帧是在
# `companion_profile_bind` 处理链**跑完之后**才发的。链上的
# `_ensure_companion_inbox_route` + `bind_and_drain` 是两个无界 await：
# 重启时 Host 短时索引重放正以高占空比霸占 SDK 写道（见
# `deskpet/memory/short_index_worker.py` 事件 AK 注释），它们可以卡任意久，
# 于是耐久绑定已经是 ready（`profile_bindings.status='ready'`，实测
# 05:53:37.612Z），UI 却永远收不到就绪帧，也没有任何日志或报错。
#
# 就绪信号不能依赖闭合历史投影：身份门已经 bind，绑定行已经 ready，
# 投影抽干只是补历史。给它一个明确的预算，超时就走既有的失败分支
# （稳定码 + 日志），**照常发就绪帧**，让 UI 立刻可用；未抽干的通知
# 由 `CompanionNotificationService.wake_repair` 继续追平。
COMPANION_BIND_PROJECTION_TIMEOUT_SECONDS = 10.0
COMPANION_BIND_PROJECTION_TIMEOUT_CODE = "companion_projection_bind_drain_timeout"


async def _settle_companion_bind_projection(
    frozen_identity: Any,
    *,
    ensure_route: Any,
    notification_service: Any,
    timeout_seconds: float = COMPANION_BIND_PROJECTION_TIMEOUT_SECONDS,
) -> tuple[str | None, str | None]:
    """Bounded post-bind projection settle; returns ``(session_id, failure_code)``.

    Never raises: readiness is already durable once the coordinator marked the
    binding ready, so a slow or missing projection dependency must degrade to a
    stable code, not to an unbounded wait on the identity socket.
    """

    try:
        async with asyncio.timeout(float(timeout_seconds)):
            session_id = await ensure_route(frozen_identity)
            if notification_service is not None:
                await notification_service.bind_and_drain(frozen_identity)
            return str(session_id), None
    except TimeoutError:
        logger.warning(
            "companion_projection_bind_drain_failed",
            error="TimeoutError",
            code=COMPANION_BIND_PROJECTION_TIMEOUT_CODE,
            timeout_seconds=float(timeout_seconds),
        )
        return None, COMPANION_BIND_PROJECTION_TIMEOUT_CODE
    except Exception as exc:  # noqa: BLE001 - stable public failure projection
        code = str(getattr(exc, "code", None) or type(exc).__name__)
        logger.warning(
            "companion_projection_bind_drain_failed",
            error=type(exc).__name__,
            code=code,
            exc_info=True,
        )
        return None, code


async def _ensure_companion_inbox_route(
    frozen_identity: Any,
) -> str:
    """Return one owner-fenced inbox without claiming legacy history."""

    session_db = service_context.get("session_db")
    if session_db is None:
        raise RuntimeError("companion_session_store_unavailable")
    owner = frozen_identity.owner
    route = await session_db.get_companion_projection_route(
        owner.profile_id,
        owner.profile_generation,
        frozen_identity.binding_epoch,
    )
    if route is not None:
        session_id = str(route.get("target_session_id") or "")
        if session_id and await _bind_companion_inbox_route(
            session_id,
            frozen_identity,
            create_if_absent=False,
        ):
            return session_id
    for _attempt in range(3):
        session_id = str(uuid.uuid4())
        if await _bind_companion_inbox_route(
            session_id,
            frozen_identity,
            create_if_absent=True,
        ):
            return session_id
    raise RuntimeError("companion_inbox_route_unavailable")
