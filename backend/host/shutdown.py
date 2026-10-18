"""关闭步骤：运行时与后台任务、各项服务"""

from __future__ import annotations

import asyncio

from deskpet.orchestration.wiring import deactivate_orchestration
from deskpet.retrieval.runtime import shutdown_default_gateway
from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

from host.runtime import logger, service_context
from host.sdk_registry import (
    _sdk_recovery_watch_tasks,
    _sdk_retained_presentations,
    _sdk_run_ids_by_root,
    _sdk_unavailable_tool_authority_runs,
)
from host.state import host_state
from host.stores_bootstrap import (
    _episodic_background_tasks,
    _legacy_reflection_tasks,
    _provider_workload_maintenance_tasks,
)


async def _shutdown_runtimes_and_tasks() -> None:
    """关闭：检索网关、前台运行时、审计、SDK 入口与恢复任务、伴侣运行时与各类后台任务。"""
    await shutdown_default_gateway()
    _foreground_runtime = service_context.get("human_memory_foreground_runtime_execution_authority")
    if _foreground_runtime is not None:
        try:
            await _foreground_runtime.close(timeout=5.0)
            logger.info("human_memory_foreground_runtime_stopped")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "human_memory_foreground_runtime_shutdown_failed",
                error=str(exc),
                exc_info=True,
            )
    _terminal_audit = service_context.get("terminal_operation_audit")
    if _terminal_audit is not None:
        try:
            await _terminal_audit.close(timeout=5.0)
        except Exception:
            logger.warning("terminal_operation_audit_shutdown_incomplete", exc_info=True)
        finally:
            service_context.register("terminal_operation_audit", None)
    # 2026-09-10：记忆分析 lane 已移除；原先嵌在它 finally 里的两次前台调度槽位
    # 复位现在无条件执行（旧写法在没有 lane 时根本不会复位）。
    service_context.register("human_memory_foreground_scheduler_wake", None)
    service_context.register("human_memory_foreground_runtime_execution_authority", None)
    # Close SDK Runtime ingress
    if host_state.sdk_ingress is not None:
        host_state.sdk_ingress.close()
    _pending_sdk_recovery = tuple(_sdk_recovery_watch_tasks.values())
    for _task in _pending_sdk_recovery:
        _task.cancel()
    if _pending_sdk_recovery:
        await asyncio.gather(*_pending_sdk_recovery, return_exceptions=True)
    _sdk_recovery_watch_tasks.clear()
    _companion_runtime_service = service_context.get("companion_runtime")
    if _companion_runtime_service is not None:
        try:
            await _companion_runtime_service.close(timeout=5.0)
            _runtime_diagnostics = _companion_runtime_service.diagnostics()
            if (
                _runtime_diagnostics["scheduler_count"] != 0
                or _runtime_diagnostics["child_count"] != 0
            ):
                raise RuntimeError("companion_runtime_shutdown_incomplete")
            logger.info(
                "companion_runtime_stopped",
                scheduler_count=0,
                child_count=0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("companion_runtime_shutdown_failed", error=str(exc), exc_info=True)
    _legacy_reflection_pending = tuple(_legacy_reflection_tasks)
    for _task in _legacy_reflection_pending:
        _task.cancel()
    if _legacy_reflection_pending:
        await asyncio.gather(
            *_legacy_reflection_pending,
            return_exceptions=True,
        )
    _provider_maintenance_pending = tuple(_provider_workload_maintenance_tasks)
    for _task in _provider_maintenance_pending:
        _task.cancel()
    if _provider_maintenance_pending:
        await asyncio.gather(
            *_provider_maintenance_pending,
            return_exceptions=True,
        )
    _provider_workload_maintenance_tasks.clear()


async def _shutdown_services() -> None:
    """关闭：桌面测试桥、SDK 运行栈、会话库、能力平台、工作流、看门狗、MCP、向量与图片工作器、技能目录、情节后台任务。"""
    if host_state.sdk_desktop_bridge is not None:
        try:
            await host_state.sdk_desktop_bridge.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("sdk_desktop_test_shutdown_failed", error=str(exc), exc_info=True)
        finally:
            host_state.sdk_desktop_bridge = None
            service_context.register("sdk_runtime_ready", None)
    # Agent 编排服务（plans/2026-09-11-orchestrator-host-integration）先于 SDK stack 关闭。
    # 这只是有序关停路径；App 真实退出是 SIGKILL，编排的正确性不依赖这里。
    try:
        await deactivate_orchestration(service_context)
    except Exception as exc:  # noqa: BLE001
        logger.warning("orchestration_shutdown_failed", error=str(exc), exc_info=True)
    # Close SDK Runtime Stack (Slice C)
    if host_state.sdk_runtime_stack is not None:
        try:
            await host_state.sdk_runtime_stack.close()
            logger.info("product_sdk_runtime_stopped")
        except Exception as exc:  # noqa: BLE001
            logger.warning("product_sdk_runtime_shutdown_failed", error=str(exc), exc_info=True)
        finally:
            host_state.sdk_runtime_stack = None
            host_state.sdk_ingress = None

            for _sdk_run_id, _retained in tuple(_sdk_retained_presentations.items()):
                _delivery_adapters.pop(_sdk_run_id, None)
                _context = _retained[3]
                _root_run_id = str(getattr(_context, "run_id", "") or "")
                if _sdk_run_ids_by_root.get(_root_run_id) == _sdk_run_id:
                    _sdk_run_ids_by_root.pop(_root_run_id, None)
            _sdk_retained_presentations.clear()
            _sdk_unavailable_tool_authority_runs.clear()
            service_context.register("sdk_runtime_ready", None)
    # SessionDB 是纯 Host 会话账本；在每个 Runtime 借用方停止后关闭一次，
    # 带硬超时，避免 shutdown 卡在存储故障上。
    _owned_session_db = service_context.get("session_db")
    if _owned_session_db is not None:
        try:
            await asyncio.wait_for(
                _owned_session_db.close(timeout_seconds=5.0),
                timeout=6.0,
            )
            logger.info("product_memory_owner_stopped")
        except Exception as exc:  # noqa: BLE001
            logger.warning("product_memory_owner_shutdown_failed", error=str(exc), exc_info=True)
        finally:
            service_context.register("session_db", None)
    _capability_platform = service_context.get("capability_platform")
    _capability_center = service_context.get("capability_center")
    if _capability_center is not None:
        try:
            await _capability_center.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning("capability_center_shutdown_failed", error=str(exc), exc_info=True)
        finally:
            service_context.register("capability_center", None)
    if _capability_platform is not None:
        try:
            cleanup_reports = await _capability_platform.shutdown()
            logger.info(
                "capability_platform_stopped",
                cleanup_reports=len(cleanup_reports),
                remaining_pids=sum(len(report.remaining_pids) for report in cleanup_reports),
                released_private_memory_bytes=sum(
                    report.released_private_memory_bytes for report in cleanup_reports
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("capability_platform_shutdown_failed", error=str(exc), exc_info=True)
        finally:
            service_context.register("capability_platform", None)
    _execution_uow = service_context.get("execution_uow")
    if _execution_uow is not None:
        try:
            await asyncio.wait_for(_execution_uow.close(), timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("execution_ledger_shutdown_failed", error=str(exc), exc_info=True)
    # stop the watchdog cleanly so its task doesn't dangle past
    # shutdown and produce "Task was destroyed but it is pending!" noise.
    _wd = service_context.get("watchdog")
    if _wd is not None:
        try:
            await _wd.stop()
        except Exception as exc:
            logger.warning("p5_supervisor_watchdog_stop_failed", error=str(exc), exc_info=True)
    # stop in reverse-dependency order — MCP servers first (so they
    # don't keep firing tool_invoke writes), then VectorWorker (drain
    # outstanding embeds), then SkillLoader's watchdog thread.
    _mcp = service_context.get("mcp_manager")
    if _mcp is not None:
        try:
            await _mcp.stop()
        except Exception as exc:
            logger.warning("p4_mcp_manager_stop_failed", error=str(exc), exc_info=True)
    _vw = service_context.get("vector_worker")
    if _vw is not None:
        try:
            await _vw.stop()
        except Exception as exc:
            logger.warning("p4_vector_worker_stop_failed", error=str(exc), exc_info=True)
    for _skill_surface in (
        service_context.get("managed_skill_discovery_projection"),
        service_context.get("skill_loader"),
    ):
        if _skill_surface is not None:
            try:
                await _skill_surface.stop()
            except Exception as exc:
                logger.warning("p4_skill_surface_stop_failed", error=str(exc), exc_info=True)

    # Stage 2 / WI-S2.4 (D12 v2)：等所有 episodic fact 抽取 task 完成。
    # 防 "Task was destroyed but it is pending" warning + 数据丢失。
    pending = list(_episodic_background_tasks)
    if pending:
        logger.info(
            "episodic_background_tasks_draining count=%d",
            len(pending),
        )
        try:
            await asyncio.gather(*pending, return_exceptions=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "episodic_background_tasks_drain_failed: %s",
                exc,
                exc_info=True,
            )

    logger.info("shutting down")
