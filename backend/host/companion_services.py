"""伴侣服务：通知投影服务的装配与启动排空"""

from __future__ import annotations


from deskpet.companion.notifications import CompanionNotificationService

from host.chat_delivery import _broadcast_control
from host.runtime import logger, service_context
from host.state import host_state


async def _initialize_companion_projection_services() -> None:
    """Compose Task-12 services onto the existing SessionDB/control roots."""

    session_db = service_context.get("session_db")
    if host_state.companion_store is None or session_db is None:
        # Companion store/session_db unavailable (P4 services failed)
        logger.warning(
            "companion_projection_skipped",
            reason="dependencies_unavailable",
            companion_store_available=host_state.companion_store is not None,
            session_db_available=session_db is not None,
        )
        return
    notifications = CompanionNotificationService(
        store=host_state.companion_store,
        session_db=session_db,
        live_sink=_broadcast_control,
    )
    host_state.companion_notification_service = notifications
    service_context.register("companion_notification_service", notifications)
    try:
        frozen = host_state.companion_identity_gate.freeze()
    except Exception as unready_exc:  # noqa: BLE001
        # 2026-09-09 事件 AK：这条日志在每一次启动（全新与重启）都会出现——
        # 身份门要等主窗口那次签名 bind 才能 freeze，它**不是**故障。此前它
        # 只有一个事件名，重启事故里被误读成根因。补上收据：耐久绑定是否已经
        # 存在（即"这是一次重启"）、以及门未就绪的确切原因，让下一次现场
        # 一眼能分清「等签名 bind」和「依赖真的缺失」。
        durable_binding = None
        try:
            durable_binding = host_state.companion_store.get_profile_binding(device_scope="desktop")
        except Exception:  # noqa: BLE001 - observability must never fail startup
            logger.debug("initialize_companion_projection_services_get_profi_failed", exc_info=True)
            durable_binding = None
        logger.info(
            "companion_projection_history_closed_identity_unready",
            reason=type(unready_exc).__name__,
            code=str(getattr(unready_exc, "code", None) or ""),
            durable_binding_present=durable_binding is not None,
            durable_binding_status=(
                str(durable_binding["status"]) if durable_binding is not None else None
            ),
            awaits="companion_profile_bind",
            exc_info=True,
        )
    else:
        try:
            await notifications.bind_and_drain(frozen)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "companion_projection_startup_drain_failed",
                error=type(exc).__name__,
                exc_info=True,
            )
