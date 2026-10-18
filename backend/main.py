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

from contextlib import asynccontextmanager

# isort: split


import uvicorn

# isort: split

from fastapi import (
    FastAPI,
)

# isort: split
from host.runtime import (
    SHARED_SECRET,
    config,
    logger,
)

# isort: split


# isort: split
from host.shutdown import (
    _shutdown_runtimes_and_tasks,
    _shutdown_services,
)

# isort: split
from host.startup_core import (
    _startup_core_services,
    _startup_provider_routing,
    _startup_runtime_and_desktop_bridge,
    _startup_search_and_billing,
)

# isort: split
from host.startup_workflows import (
    _startup_workflow_services,
)

# isort: split
from host.startup_background import (
    _startup_curation_image_and_mcp,
    _startup_embedder_and_workload_audit,
    _startup_restore_and_skill_prewarm,
    _startup_vector_worker,
)

# isort: split
from host.startup_supervision import (
    _startup_model_context,
    _startup_self_healing,
    _startup_supervisor,
)

# isort: split


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Preload models on startup (best-effort — failures logged but don't block)."""
    _provider_readiness, startup_epoch = await _startup_core_services()
    await _startup_provider_routing(_provider_readiness)
    await _startup_search_and_billing()
    _sdb = await _startup_workflow_services()
    await _startup_restore_and_skill_prewarm()
    _vw = await _startup_embedder_and_workload_audit()
    await _startup_vector_worker(_sdb, _vw)
    await _startup_curation_image_and_mcp()
    await _startup_self_healing()
    # supervisor watchdog + LLM agent. Starts after the rest of
    # startup is done so the 30s grace can run while normal startup races
    # finish. Disabled when [supervisor].enabled = false; in that case we
    # skip construction entirely so the asyncio.Task isn't even created.
    await _startup_supervisor()
    await _startup_model_context()
    await _startup_runtime_and_desktop_bridge(startup_epoch)
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


# ─── WI-1B-4 摘要质量回路 — 纯检测/构造 helper（main.py 闭包 + 单测共用）──────


# isort: split


# ppt_pro 渲染专用线程池：真机 E2E 发现，若用默认共享 executor(None)，render 任务会
# 排在 BGE-M3 embedder / vector-worker / summarizer 等大量 run_in_executor 工作后面、
# 长时间不执行(表现为 deck 永不落盘的"假 hang")。给 render 独立池根治排队饿死。


# isort: split


# SDK Runtime globals (production ingress)
# F06：单例 retry-once provider reconciliation；SDK 端口与前台运行时共用同一份
# ``exhausted_runs``（二次未知的 run 由前台 cancel 收尾）。
# F06：SDK 栈构建时绑定的 Host RuntimeReconciliationPort（coordinator.reconcile_incomplete）。


# isort: split


# Shared final Tool-effect admission gate.  The SDK effect executor consults
# it before every physical dispatch; the foreground runtime registers exact
# lease identities into it.  Created lazily because the SDK stack starts
# before the human-memory foreground ports.


# process-local memo of whole-Run fault codes.  The authorities
# that raise a Run fault (TaskExecutionEnvelope authority, run Tool exposure)
# record the stable code; the SDK terminal observer copies it into the
# ``run_terminal`` evidence ``public_payload.error_code`` on durable FAILED.


# HM-TO-A6 incident A: process-local memo of the ContextRouteState each provider
# turn was prepared under.  ``ProductRunContextAuthority`` writes it, the
# capability-discovery bridge reads it so tool_search / tool_describe /
# tool_activate stop offering PROJECT_EFFECT Tools the Run's route can never
# execute.  Advisory only; every authority check downstream is unchanged.


# single owner of Harness evidence reservations / same-transaction
# objective events over the human-memory state.db.  Shared by the effect
# executor (tool_invocation + host.file/host.test), the provider coordinator
# (provider_invocation) and the v45 route ledger (snapshot / route facts); the
# terminal observer drains it through the SDK runtime stack's fact reader.


# the `task_scope_update` handler shared by the Tool registration and
# the terminal closure fallback (process-wide, like the binding resolver above).


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
from host.routes_cloud_config import (
    cloud_config_router,
)

# isort: split
from host.routes_commands import (
    commands_router,
)

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
from host.control_channel import (
    control_router,
)

# isort: split


app.include_router(control_router)


def main():
    logger.info("starting backend", host=config.backend.host, port=config.backend.port)
    print(f"SHARED_SECRET={SHARED_SECRET}", flush=True)  # noqa: T201 - 外壳从标准输出读共享密钥，是握手协议
    uvicorn.run(
        app,
        host=config.backend.host,
        port=config.backend.port,
        log_level=config.backend.log_level.lower(),
    )


if __name__ == "__main__":
    main()
