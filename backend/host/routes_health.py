"""HTTP 路由：健康检查、前端指标上报与指标查询"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request, Response
from observability.metrics import render as render_metrics
from observability.startup import registry as startup_errors

from host.auth import DEV_MODE
from host.llm_providers import llm
from host.runtime import SHARED_SECRET, logger
from host.state import host_state
from host.voice_bootstrap import _voice_runtime

health_router = APIRouter()


@health_router.get("/health")
async def health():
    # P3-S2: surface startup failures (esp. CUDA unavailable / model dir
    # missing) so the Rust supervisor and future frontend banner can
    # react instead of treating a crippled backend as "ready".
    errors = startup_errors.snapshot()
    return {
        "status": "degraded" if errors else "ok",
        "secret_hint": SHARED_SECRET[:4] + "...",
        "strategy": llm._strategy.value,
        # P4-S20-LLM-Unified 修: 旧 `llm._cloud` 是 HybridRouter 的废弃字段,
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
        "voice": _voice_runtime.health_payload(),
        "realtime_voice": (
            host_state.realtime_voice_service.health_payload()
            if host_state.realtime_voice_service is not None
            else {"enabled": False, "path": "/ws/realtime-voice"}
        ),
    }


@health_router.post("/metrics/event")
async def post_metrics_event(request: Request):
    """WI-T1.7 last-mile: 前端 ArtifactCard 按钮点击 → 此端点 → metrics.jsonl。

    Body: {event: str, detail: dict}
    Auth: 同 /metrics — SHARED_SECRET 在 DEV_MODE 下放行。
    PRD §5 健康区间 metric (artifact_action click rate) 的入口。
    """
    if not DEV_MODE:
        secret = request.headers.get("x-shared-secret", "")
        if not secret or not secrets.compare_digest(secret, SHARED_SECRET):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="metrics"'},
            )
    try:
        body = await request.json()
    except Exception:
        logger.debug("post_metrics_event_json_failed", exc_info=True)
        return Response(status_code=400, content="invalid json")
    event = body.get("event")
    detail = body.get("detail") or {}
    if not isinstance(event, str) or not event:
        return Response(status_code=400, content="missing 'event' field")
    if not isinstance(detail, dict):
        return Response(status_code=400, content="'detail' must be dict")
    try:
        # 按需导入：测试替身（observability.metrics_sink）
        from observability.metrics_sink import record as _metric_record

        _metric_record(event, detail)
    except Exception as exc:  # noqa: BLE001
        logger.warning("metrics event drop: %s", exc, exc_info=True)
        return Response(status_code=500, content="sink error")
    return Response(status_code=204)


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
