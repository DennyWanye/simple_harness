"""HTTP 路由：健康检查、前端指标上报与指标查询"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request, Response
from observability.metrics import render as render_metrics
from observability.startup import registry as startup_errors

from host.auth import DEV_MODE
from host.llm_providers import llm
from host.runtime import SHARED_SECRET
from host.state import host_state

health_router = APIRouter()


@health_router.get("/health")
async def health():
    # surface startup failures (esp. CUDA unavailable / model dir
    # missing) so the Rust supervisor and future frontend banner can
    # react instead of treating a crippled backend as "ready".
    errors = startup_errors.snapshot()
    return {
        "status": "degraded" if errors else "ok",
        "secret_hint": SHARED_SECRET[:4] + "...",
        "strategy": llm._strategy.value,
        # 旧 `llm._cloud` 是 HybridRouter 的废弃字段,
        # unified schema 重构后恒为 None → /health 永远误报 cloud_configured=false
        # (即便 backend 实际有云端 key)。改为读真实生效的 local_llm: base_url
        # 非本地 + 有真 key(非 ollama 占位) = 已配云端。
        "cloud_configured": bool(
            host_state.local_llm is not None
            and "localhost" not in str(getattr(host_state.local_llm, "base_url", ""))
            and "127.0.0.1" not in str(getattr(host_state.local_llm, "base_url", ""))
            and getattr(host_state.local_llm, "api_key", None)
            and getattr(host_state.local_llm, "api_key", "") != "ollama"
        ),
        "startup_errors": errors,
    }


@health_router.get("/metrics")
async def metrics(request: Request):
    """Prometheus scrape endpoint (P2-1-S6).

    Gated by the same shared secret that protects WS connections. In
    DEV_MODE the gate is open so local `curl` / smoke scripts can hit it
    without juggling headers.
    """
    if not DEV_MODE:
        secret = request.headers.get("x-shared-secret", "")
        if not secret or not secrets.compare_digest(secret, SHARED_SECRET):
            # RFC 7235 §3.1: a 401 MUST carry WWW-Authenticate so clients
            # know which scheme/realm to retry with. Prometheus scrapers
            # and curl both surface the header to the operator.
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="metrics"'},
            )
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)
