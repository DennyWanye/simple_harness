# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import sys

# Force UTF-8 stdout on Windows (default GBK chokes on emoji in LLM output)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

from deskpet.frozen_worker_dispatch import dispatch_frozen_worker_if_requested

dispatch_frozen_worker_if_requested()

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

# isort: split

# isort: split

from pathlib import Path as _Path

import paths as _paths
import uvicorn

# isort: split

# isort: split

from config import (
    effective_llm_model,
    resolve_deep_research_workflow_version,
)
from deskpet.agent.product_domain_sink import LegacyProductDomainSink
from deskpet.agent.run_presenter import (
    PresentationState,
    RunPresentationContext,
)
from deskpet.agent.turn_preparer import (
    ProductTurnPreparer,
)
from fastapi import (
    FastAPI,
)
from host.state import host_state
from pydantic import (
    model_validator,
)

# isort: split
from host.runtime import (
    SHARED_SECRET,
    config,
    logger,
    service_context,
)

# isort: split


# isort: split

# isort: split


# isort: split
from deskpet.sdk_adapters.sdk_candidate import (
    SDK_VERSION,
)
from llm.provider_registry import (
    ProviderMutationConflict,
)
from tools.registry import ToolRegistry

# isort: split

# isort: split


# P4-S25 (2026-05-09): cross-endpoint Ollama fallback removed at
# user request. Single-endpoint mode — errors surface directly
# instead of auto-swapping to a different model with a different
# voice mid-turn.


# P4-S25 (2026-05-09): cross-endpoint Ollama fallback removed at user
# request — they don't want auto-swap to gemma4:e4b mid-turn because
# the model-style mismatch produces ugly mixed responses. Errors from
# the configured LLM endpoint now surface directly to the user.


# isort: split

# isort: split
# 语音装配模块在导入期登记语音引擎占位；保持原来的导入位置，登记顺序不变。
import host.voice_bootstrap  # noqa: F401

# isort: split


# isort: split

# isort: split


# ─── WI-4.3 技能自创闭环 hook（方案 B 抽 helper）─────────────────────
# 2026-06-06：原 codify hook 只 inline 在 chat handler 的 FinalEvent 分支 →
# turn 经 ErrorEvent（relay ReadError）/ 迭代上限 / 被 stop 中止结束时整段不跑
# → 多工具 turn 跑了 ≥5 工具但卡不弹（真机诊断）。抽成模块级 helper，在
# FinalEvent + ErrorEvent 两处都调，让 turn 任意路径结束都能触发技能自创。
# safe-fail：任何异常只 debug log，不中断正常 turn。flag OFF → 整段跳过（BC）。
_companion_ingress_tasks: set[asyncio.Task] = set()


# isort: split

# isort: split


# isort: split

# isort: split


# isort: split

# isort: split


# isort: split

# isort: split


# isort: split
from host.stores_bootstrap import (
    _episodic_background_tasks,
    _legacy_reflection_tasks,
    _provider_workload_maintenance_tasks,
)

# isort: split


# isort: split

# isort: split


# isort: split

# isort: split


# isort: split
from host.startup_core import (
    _startup_core_services,
    _startup_desktop_and_realtime_voice,
    _startup_provider_routing,
    _startup_search_and_voice,
)

# isort: split


# isort: split
from host.startup_workflows import (
    _startup_workflow_services,
)

# isort: split


# isort: split
from host.startup_background import (
    _startup_curation_image_and_mcp,
    _startup_embedder_and_workload_audit,
    _startup_restore_and_skill_prewarm,
    _startup_vector_worker,
)

# isort: split


# isort: split
from host.startup_supervision import (
    _startup_auto_resume_and_model_context,
    _startup_self_healing,
    _startup_supervisor,
)

# isort: split


async def _shutdown_runtimes_and_tasks() -> None:
    """关闭：实时语音、检索网关、前台运行时、审计、SDK 入口与恢复任务、伴侣运行时与各类后台任务。"""
    if host_state.realtime_voice_service is not None:
        try:
            await host_state.realtime_voice_service.close()
            logger.info("realtime_voice_stopped")
        except Exception:  # noqa: BLE001
            logger.warning("realtime_voice_shutdown_failed")
        finally:
            host_state.realtime_voice_service = None
    from deskpet.retrieval.runtime import shutdown_default_gateway

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
            )
    _terminal_audit = service_context.get("terminal_operation_audit")
    if _terminal_audit is not None:
        try:
            await _terminal_audit.close(timeout=5.0)
        except Exception:
            logger.warning("terminal_operation_audit_shutdown_incomplete")
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
    _pending_ingress = tuple(_companion_ingress_tasks)
    for _task in _pending_ingress:
        _task.cancel()
    if _pending_ingress:
        await asyncio.gather(*_pending_ingress, return_exceptions=True)
    _companion_ingress_tasks.clear()
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
            logger.warning("companion_runtime_shutdown_failed", error=str(exc))
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
            logger.warning("sdk_desktop_test_shutdown_failed", error=str(exc))
        finally:
            host_state.sdk_desktop_bridge = None
            service_context.register("sdk_runtime_ready", None)
    # Agent 编排服务（plans/2026-09-11-orchestrator-host-integration）先于 SDK stack 关闭。
    # 这只是有序关停路径；App 真实退出是 SIGKILL，编排的正确性不依赖这里。
    try:
        from deskpet.orchestration.wiring import deactivate_orchestration

        await deactivate_orchestration(service_context)
    except Exception as exc:  # noqa: BLE001
        logger.warning("orchestration_shutdown_failed", error=str(exc))
    # Close SDK Runtime Stack (Slice C)
    if host_state.sdk_runtime_stack is not None:
        try:
            await host_state.sdk_runtime_stack.close()
            logger.info("product_sdk_runtime_stopped")
        except Exception as exc:  # noqa: BLE001
            logger.warning("product_sdk_runtime_shutdown_failed", error=str(exc))
        finally:
            host_state.sdk_runtime_stack = None
            host_state.sdk_ingress = None
            from deskpet.sdk_adapters.desktop_runtime import _delivery_adapters

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
            logger.warning("product_memory_owner_shutdown_failed", error=str(exc))
        finally:
            service_context.register("session_db", None)
    _capability_platform = service_context.get("capability_platform")
    _capability_center = service_context.get("capability_center")
    if _capability_center is not None:
        try:
            await _capability_center.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning("capability_center_shutdown_failed", error=str(exc))
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
            logger.warning("capability_platform_shutdown_failed", error=str(exc))
        finally:
            service_context.register("capability_platform", None)
    _workflow_service = service_context.get("workflow_service")
    if _workflow_service is not None:
        try:
            await asyncio.wait_for(_workflow_service.close(), timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("workflow_owner_shutdown_failed", error=str(exc))
    # P5-S1: stop the watchdog cleanly so its task doesn't dangle past
    # shutdown and produce "Task was destroyed but it is pending!" noise.
    _wd = service_context.get("watchdog")
    if _wd is not None:
        try:
            await _wd.stop()
        except Exception as exc:
            logger.warning("p5_supervisor_watchdog_stop_failed", error=str(exc))
    # P4-S15: stop in reverse-dependency order — MCP servers first (so they
    # don't keep firing tool_invoke writes), then VectorWorker (drain
    # outstanding embeds), then SkillLoader's watchdog thread.
    _mcp = service_context.get("mcp_manager")
    if _mcp is not None:
        try:
            await _mcp.stop()
        except Exception as exc:
            logger.warning("p4_mcp_manager_stop_failed", error=str(exc))
    _vw = service_context.get("vector_worker")
    if _vw is not None:
        try:
            await _vw.stop()
        except Exception as exc:
            logger.warning("p4_vector_worker_stop_failed", error=str(exc))
    _iw = service_context.get("image_worker")
    if _iw is not None:
        try:
            await _iw.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("image_worker_stop_failed", error=str(exc))
    for _skill_surface in (
        service_context.get("managed_skill_discovery_projection"),
        service_context.get("skill_loader"),
    ):
        if _skill_surface is not None:
            try:
                await _skill_surface.stop()
            except Exception as exc:
                logger.warning("p4_skill_surface_stop_failed", error=str(exc))

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
            )

    logger.info("shutting down")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Preload models on startup (best-effort — failures logged but don't block)."""
    _provider_readiness, startup_epoch = await _startup_core_services()
    await _startup_provider_routing(_provider_readiness)
    await _startup_search_and_voice()
    _sdb = await _startup_workflow_services()
    await _startup_restore_and_skill_prewarm()
    _vw = await _startup_embedder_and_workload_audit()
    await _startup_vector_worker(_sdb, _vw)
    await _startup_curation_image_and_mcp()
    _heal_cfg = await _startup_self_healing()
    # P5-S1/S2: supervisor watchdog + LLM agent. Starts after the rest of
    # startup is done so the 30s grace can run while normal startup races
    # finish. Disabled when [supervisor].enabled = false; in that case we
    # skip construction entirely so the asyncio.Task isn't even created.
    await _startup_supervisor()
    await _startup_auto_resume_and_model_context(_heal_cfg)
    await _startup_desktop_and_realtime_voice(startup_epoch)
    yield
    await _shutdown_runtimes_and_tasks()
    await _shutdown_services()


app = FastAPI(title="Desktop Pet Backend", version="0.2.0", lifespan=lifespan)

# CORS: Tauri WebView2 runs on tauri://localhost (or https://tauri.localhost).
# fetch() to http://127.0.0.1:8100 is cross-origin and blocked without this.
# WebSocket connections are NOT subject to CORS, only HTTP (POST /config/cloud).
from fastapi.middleware.cors import CORSMiddleware

# v2 fix: regex 模式支持任意 vite dev port (worktree-aware 隔离场景).
# 默认 5173；v2 worktree 用 5473；其他 worktree 用 5273/5373/5573 等.
# allow_origin_regex 覆盖所有 localhost / 127.0.0.1 + 任意端口 + Tauri.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(tauri://localhost|https://tauri\.localhost|http://(localhost|127\.0\.0\.1):\d+)$",
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "X-Shared-Secret"],
)

# isort: split
from host.connections import (
    _control_connections,
)

# isort: split


# isort: split

# isort: split


# isort: split
from host.chat_delivery import (
    _broadcast_control,
)

# isort: split


# isort: split

# isort: split


# ─── WI-1B-4 摘要质量回路 — 纯检测/构造 helper（main.py 闭包 + 单测共用）──────
import re as _re_sql_module

_SUMMARY_CONFUSED_RE = _re_sql_module.compile(r"刚才|之前说的|你忘了|我们在弄|上一个")
_TASK_STATE_SNAPSHOT_MARKER = "[任务态快照"


def _sql_user_is_confused(text: str) -> bool:
    """用户消息是否用"困惑措辞"问起被压掉的上下文（词法匹配）。"""
    return bool(_SUMMARY_CONFUSED_RE.search(text or ""))


def _sql_latest_task_snapshot(entries: list[dict]) -> str | None:
    """从 L1 list_entries 结果里取最近一条 pre-flush 任务态快照（无则 None）。

    任务态快照由 agent_loop wi4b_preflush 在压缩触发时写入 MEMORY.md，
    形如 ``[任务态快照/task-state] 目标: ...; 最近请求: ...``。它的存在
    即"本 session 发生过压缩"的标志（不依赖 ctx_observability flag）。
    """
    snaps = [
        str(e.get("text", ""))
        for e in (entries or [])
        if _TASK_STATE_SNAPSHOT_MARKER in str(e.get("text", ""))
    ]
    return snaps[-1] if snaps else None


def _sql_build_reinject_msg(snapshot: str) -> dict:
    """据任务态快照构造一条回灌 system 消息。"""
    return {
        "role": "system",
        "content": (
            "[任务态回灌/summary-quality-loop] 用户提起了之前的上下文，但相关"
            "历史已被压缩。以下是压缩前保存的任务态快照，请据此续接，不要重新"
            "自我介绍或反问用户想做什么：\n" + snapshot
        ),
        "_is_summary_reinject": True,
    }


# isort: split

# isort: split


# ppt_pro 渲染专用线程池：真机 E2E 发现，若用默认共享 executor(None)，render 任务会
# 排在 BGE-M3 embedder / vector-worker / summarizer 等大量 run_in_executor 工作后面、
# 长时间不执行(表现为 deck 永不落盘的"假 hang")。给 render 独立池根治排队饿死。


# isort: split

# isort: split


# SDK Runtime globals (production ingress)
# F06：单例 retry-once provider reconciliation；SDK 端口与前台运行时共用同一份
# ``exhausted_runs``（二次未知的 run 由前台 cancel 收尾）。
# F06：SDK 栈构建时绑定的 Host RuntimeReconciliationPort（coordinator.reconcile_incomplete）。


# isort: split

# isort: split


# Shared final Tool-effect admission gate.  The SDK effect executor consults
# it before every physical dispatch; the foreground runtime registers exact
# lease identities into it.  Created lazily because the SDK stack starts
# before the human-memory foreground ports.


# S5b Task 1: process-local memo of whole-Run fault codes.  The authorities
# that raise a Run fault (TaskExecutionEnvelope authority, run Tool exposure)
# record the stable code; the SDK terminal observer copies it into the
# ``run_terminal`` evidence ``public_payload.error_code`` on durable FAILED.


# HM-TO-A6 incident A: process-local memo of the ContextRouteState each provider
# turn was prepared under.  ``ProductRunContextAuthority`` writes it, the
# capability-discovery bridge reads it so tool_search / tool_describe /
# tool_activate stop offering PROJECT_EFFECT Tools the Run's route can never
# execute.  Advisory only; every authority check downstream is unchanged.


# S5b Task 2: single owner of Harness evidence reservations / same-transaction
# objective events over the human-memory state.db.  Shared by the effect
# executor (tool_invocation + host.file/host.test), the provider coordinator
# (provider_invocation) and the v45 route ledger (snapshot / route facts); the
# terminal observer drains it through the SDK runtime stack's fact reader.


# S5b Task 3: the `task_scope_update` handler shared by the Tool registration and
# the terminal closure fallback (process-wide, like the binding resolver above).


# isort: split
from host.sdk_registry import (
    _sdk_recovery_watch_tasks,
    _sdk_retained_presentations,
    _sdk_run_ids_by_root,
    _sdk_unavailable_tool_authority_runs,
)

# isort: split


# isort: split

# isort: split


# isort: split

# isort: split


# 2026-09-10：S5b Task 4 的 Host↔Memory 异步面（HostEvidenceAuthority /
# HostMemoryAnalysisExecutor / MemoryIngestionOutboxWorker / MemoryAnalysisLane）
# 整条随认知记忆 SDK 移除，不再有可组装的槽位。


# isort: split

# isort: split


# isort: split

# isort: split

# if _sdk_ingress is None or not _sdk_ingress.accepting or not run_id:
#     return False
# from deskpet.execution.contracts import (
#     AttachmentPolicy,
#     RunRef,
#     TERMINAL_RUN_STATUSES,
# )
#
# host, _provider, _providers, _provider_snapshot = await _issue_product_harness_host(
#     session_id=session_id,
#     workspace=None,
# )
# actor = host.actor(root_run_id=run_id)
# parent_ref = RunRef(run_id, session_id)
# kernel = _sdk_ingress.kernel
# links = await kernel._uow.list_child_links(parent_ref, actor)
# for link in links:
#     if link.attachment_policy is AttachmentPolicy.DETACHED:
#         continue
#     child = await kernel._query(
#         RunRef(link.child_run_id, session_id),
#         actor,
#     )
#     if child.status not in TERMINAL_RUN_STATUSES:
#         return True
# return False


# isort: split

# isort: split


# isort: split

# isort: split


# isort: split

# isort: split


# isort: split

# isort: split


# isort: split
from host.routes_voice import (
    voice_router,
)

# isort: split


# isort: split

# isort: split


# isort: split
from host.routes_cloud_config import (
    cloud_config_router,
)

# isort: split


# isort: split
from host.routes_commands import (
    commands_router,
)

# isort: split


# isort: split
from host.routes_health import (
    health_router,
)

# isort: split


app.include_router(cloud_config_router)
app.include_router(commands_router)
app.include_router(health_router)


# --- S14 memory management dispatch -----------------------------------------
# Handled on the control WS (same auth gate as chat/interrupt) so we don't
# expose a second unauthenticated HTTP surface. The four verbs are the minimum
# needed for the "delete-my-data" affordance V5 §6 requires.


# isort: split

# isort: split


# isort: split

# isort: split


# isort: split
from host.control_channel import (
    control_router,
)

# isort: split


# isort: split

# isort: split


# isort: split

# isort: split


# isort: split

# isort: split


# isort: split

# isort: split


# isort: split

# isort: split


# isort: split

# isort: split


# isort: split

# isort: split


app.include_router(control_router)
app.include_router(voice_router)


def main():
    logger.info("starting backend", host=config.backend.host, port=config.backend.port)
    print(f"SHARED_SECRET={SHARED_SECRET}", flush=True)
    uvicorn.run(
        app,
        host=config.backend.host,
        port=config.backend.port,
        log_level=config.backend.log_level.lower(),
    )


if __name__ == "__main__":
    main()
