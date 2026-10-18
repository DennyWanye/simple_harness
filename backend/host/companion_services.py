"""伴侣服务：投影服务、动作决策服务初始化与动作命令分发"""

from __future__ import annotations

import inspect
import secrets

from deskpet.companion.action_decisions import (
    CompanionActionDecisionService,
    SqliteExecutionDecisionRecovery,
    TrustedCompanionControlIdentity,
)
from deskpet.companion.activation import ActivationDecisionCoordinator
from deskpet.companion.control_ingress import (
    CompanionActivationDecisionService,
    CompanionEvaluationDecisionService,
    CompanionForgetDecisionService,
    CompanionRollbackDecisionService,
    UnavailableCompanionGrowthControlService,
)
from deskpet.companion.detail_query import CompanionDetailQueryPort
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
    detail_query = CompanionDetailQueryPort(
        store=host_state.companion_store,
        platform_store=service_context.get("capability_platform"),
        cursor_secret=secrets.token_bytes(32),
    )
    notifications = CompanionNotificationService(
        store=host_state.companion_store,
        session_db=session_db,
        live_sink=_broadcast_control,
        detail_query=detail_query,
    )
    host_state.companion_detail_query = detail_query
    host_state.companion_notification_service = notifications
    service_context.register("companion_detail_query", detail_query)
    service_context.register("companion_notification_service", notifications)
    service_context.register("companion_projection_visibility", notifications.visibility)
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


def _initialize_companion_action_decision_service() -> None:
    """Bind all five trusted card actions to explicit production services."""

    uow = service_context.get("execution_uow")
    unavailable = {
        "companion_growth_action_decision_service": "companion_action_decision_dependencies_unavailable",
        "companion_growth_evaluation_decision_service": "companion_evaluation_decision_dependencies_unavailable",
        "companion_growth_activation_decision_service": "companion_activation_decision_dependencies_unavailable",
        "companion_rollback_service": "companion_rollback_dependencies_unavailable",
        "companion_forget_service": "companion_forget_dependencies_unavailable",
    }
    for service_key, error_code in unavailable.items():
        service_context.register(
            service_key,
            UnavailableCompanionGrowthControlService(error_code),
        )
    if host_state.companion_store is None or host_state.companion_identity_gate is None:
        return
    service_context.register(
        "companion_growth_evaluation_decision_service",
        CompanionEvaluationDecisionService(host_state.companion_store),
    )
    service_context.register(
        "companion_growth_activation_decision_service",
        CompanionActivationDecisionService(
            host_state.companion_store,
            ActivationDecisionCoordinator(host_state.companion_store),
            dispatcher=host_state.companion_activation_dispatcher,
        ),
    )
    if host_state.companion_detail_query is not None:
        service_context.register(
            "companion_rollback_service",
            CompanionRollbackDecisionService(
                host_state.companion_store,
                host_state.companion_detail_query,
                dispatcher=host_state.companion_activation_dispatcher,
            ),
        )
        service_context.register(
            "companion_forget_service",
            CompanionForgetDecisionService(
                host_state.companion_store, host_state.companion_detail_query
            ),
        )
    if uow is not None and host_state.sdk_ingress is not None:
        service_context.register(
            "companion_growth_action_decision_service",
            CompanionActionDecisionService(
                identity_gate=host_state.companion_identity_gate,
                companion_store=host_state.companion_store,
                execution=SqliteExecutionDecisionRecovery(uow.path),
                kernel_client=host_state.sdk_ingress,
            ),
        )


async def _dispatch_companion_action_command(
    command_kind: str,
    message: dict[str, Any],
) -> None:
    """Dispatch the five exact UI action names after trusted ingress validation."""

    payload = message.get("payload")
    body = payload.get("body") if isinstance(payload, dict) else None
    if not isinstance(body, dict):
        raise RuntimeError("companion_action_body_invalid")
    frozen = host_state.companion_identity_gate.freeze()
    if command_kind == "companion_growth_action_decision":
        if set(body) != {"decision_id", "allow"}:
            raise RuntimeError("companion_action_decision_fields_invalid")
        service = service_context.get("companion_growth_action_decision_service")
        if service is None:
            raise RuntimeError("companion_action_decision_unavailable")
        await service.decide(
            decision_id=str(body["decision_id"]),
            allow=body["allow"],
            control_identity=TrustedCompanionControlIdentity.from_frozen(frozen),
        )
        return
    service_key = {
        "companion_growth_evaluation_decision": ("companion_growth_evaluation_decision_service"),
        "companion_growth_activation_decision": ("companion_growth_activation_decision_service"),
        "companion_rollback": "companion_rollback_service",
        "companion_forget": "companion_forget_service",
    }.get(command_kind)
    if service_key is None:
        raise RuntimeError("companion_action_kind_unsupported")
    service = service_context.get(service_key)
    if service is None:
        raise RuntimeError(f"{service_key}_unavailable")
    handler = getattr(service, "decide", None)
    if handler is None:
        raise RuntimeError(f"{service_key}_invalid")
    result = handler(frozen_identity=frozen, **body)
    if inspect.isawaitable(result):
        await result
    if (
        command_kind in {"companion_rollback", "companion_forget"}
        and host_state.companion_notification_service is not None
    ):
        await host_state.companion_notification_service.bind_and_drain(frozen)
