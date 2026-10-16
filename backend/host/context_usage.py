"""上下文用量与上下文明细：会话上下文状态缓存、快照元数据、用量的读取与广播、用量事件、上下文明细的读取与请求处理。"""

from __future__ import annotations

import re
import time

from deskpet.agent.tokens import count_text_tokens
from deskpet.sdk_adapters.context_authority import canonical_sha256
from fastapi import WebSocket

from host.chat_delivery import _broadcast_default_chat_peers
from host.runtime import logger, service_context

# 2026-05-28 — per-session context-usage snapshot for the frontend Claude-Code-
# style ring gauge. Keyed by chat session_id (uuid，或 "code-XXX")。
# Updated on every successful LLM turn; pushed via ``context_usage`` ws event.
_session_context_state: dict[str, dict[str, Any]] = {}


def _cache_context_usage_state(payload: dict[str, Any]) -> dict[str, Any]:
    """Cache only a newer durable ContextUsageStateV2 version."""

    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return payload
    if int(payload.get("version") or 0) <= 0:
        return _session_context_state.get(session_id) or payload
    previous = _session_context_state.get(session_id)
    incoming_version = int(payload.get("version") or 0)
    previous_version = int((previous or {}).get("version") or 0)
    if previous is None or incoming_version >= previous_version:
        _session_context_state[session_id] = dict(payload)
        return payload
    return previous


async def _attach_context_snapshot_metadata(
    session_db: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Join public snapshot identity without conflating its version axis."""

    result = dict(payload)
    sid = str(result.get("session_id") or "").strip()
    if not sid or not hasattr(session_db, "get_latest_sdk_context_public_snapshot"):
        return result
    public = await session_db.get_latest_sdk_context_public_snapshot(sid)
    if public is None:
        result.update({"snapshot_id": None, "snapshot_version": None})
        return result
    if not isinstance(public, dict) or str(public.get("session_id") or "").strip() != sid:
        raise RuntimeError("sdk_context_public_snapshot_session_mismatch")
    result["snapshot_id"] = str(public.get("snapshot_id") or "").strip() or None
    version = public.get("snapshot_version")
    result["snapshot_version"] = (
        int(version)
        if isinstance(version, int) and not isinstance(version, bool) and version >= 0
        else None
    )
    result["snapshot_fingerprint"] = str(public.get("snapshot_fingerprint") or "")[:160]
    return result


async def _read_context_usage_authority(
    session_id: str,
    *,
    attach_snapshot: bool = True,
) -> dict[str, Any]:
    """Read the durable Session authority; never fabricate a global stub."""

    context_sdb = service_context.get("session_db")
    if context_sdb is None or not hasattr(context_sdb, "get_context_usage_state"):
        raise RuntimeError("context_usage_authority_unavailable")
    authoritative = await context_sdb.get_context_usage_state(session_id)
    if attach_snapshot:
        authoritative = await _attach_context_snapshot_metadata(context_sdb, authoritative)
    return _cache_context_usage_state(authoritative)


async def _send_context_usage_authority(
    websocket: Any,
    session_db: Any,
    session_id: str,
) -> dict[str, Any]:
    """Send the latest durable Context Usage authority to one UI peer."""

    authoritative = await session_db.get_context_usage_state(session_id)
    authoritative = await _attach_context_snapshot_metadata(session_db, authoritative)
    authoritative = _cache_context_usage_state(authoritative)
    await websocket.send_json(
        {
            "type": "context_usage",
            "payload": authoritative,
        }
    )
    return authoritative


async def _broadcast_context_usage_snapshot(session_db: Any, session_id: str) -> bool:
    """Publish one strictly newer durable usage authority to matching peers."""

    incoming = await session_db.get_context_usage_state(session_id)
    incoming = await _attach_context_snapshot_metadata(session_db, incoming)
    if str(incoming.get("session_id") or "").strip() != str(session_id):
        raise RuntimeError("context_usage_projection_session_mismatch")
    version = int(incoming.get("version") or 0)
    if version <= 0:
        return False
    previous = _session_context_state.get(str(session_id))
    previous_version = int((previous or {}).get("version") or 0)
    if version < previous_version:
        return False
    if version == previous_version and previous is not None:
        if canonical_sha256(previous) != canonical_sha256(incoming):
            raise RuntimeError("context_usage_equal_version_conflict")
        return False
    _session_context_state[str(session_id)] = dict(incoming)
    await _broadcast_default_chat_peers(
        None,
        {"type": "context_usage", "payload": incoming},
    )
    return True


def _snapshot_context_usage_event(
    session_id: str,
    *,
    provider_chain: Any | None,
    fallback_provider: Any | None = None,
) -> dict[str, Any] | None:
    """Pick the latest ``last_usage`` off the provider chain that actually
    served the last turn and combine it with the resolved ModelContextInfo
    (window / effective ceiling / compact threshold) into a single payload
    the frontend renders as a ring gauge.

    Returns ``None`` when no usage is available yet (don't emit).
    """
    try:
        # 按需导入：测试替身（llm.model_info.resolve）
        from llm.model_info import resolve as _resolve_model
    except Exception:  # noqa: BLE001
        logger.debug("snapshot_context_usage_event_import_failed", exc_info=True)
        _resolve_model = None  # type: ignore[assignment]

    # Read Context OS attempts before inspecting legacy provider state.  Some
    # adapters (including the strict E2E seam) intentionally do not expose a
    # mutable ``last_usage`` attribute, while still reporting authoritative
    # usage through ContextAttemptStore.
    try:
        _attempt_store = service_context.get("context_attempt_store")
    except Exception:
        logger.debug("snapshot_context_usage_event_get_failed", exc_info=True)
        _attempt_store = None
    _attempts: list[dict[str, Any]] = []
    _latest_attempt: dict[str, Any] | None = None
    if _attempt_store is not None:
        try:
            _attempts = _attempt_store.public_for_session(session_id)
            _latest_attempt = next(
                (
                    attempt
                    for attempt in reversed(_attempts)
                    if attempt.get("purpose") == "agent_response"
                    and attempt.get("state") == "succeeded"
                ),
                None,
            )
        except Exception:  # noqa: BLE001
            logger.debug("snapshot_context_usage_event_public_for_session_failed", exc_info=True)
            _attempts = []
            _latest_attempt = None

    # Pick first provider with non-None last_usage.
    _picked = None
    _candidates: list[Any] = []
    if provider_chain:
        try:
            _candidates.extend(list(provider_chain))
        except Exception:  # noqa: BLE001 - 读不到提供方链时只看当前模型
            logger.debug("context_usage_chain_read_failed", exc_info=True)
    if fallback_provider is not None:
        _candidates.append(fallback_provider)
    for _p in _candidates:
        _u = getattr(_p, "last_usage", None)
        if _u:
            _picked = _p
            break
    if _picked is None and _latest_attempt is None:
        return None
    usage = getattr(_picked, "last_usage", None) or {} if _picked is not None else {}
    model = getattr(_picked, "model", "") or "" if _picked is not None else ""
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    cached_tokens = 0
    # cached_tokens may be nested under prompt_tokens_details (OpenAI) or
    # at the top level (deepseek/some relays).
    _details = usage.get("prompt_tokens_details") or {}
    if isinstance(_details, dict):
        cached_tokens = int(_details.get("cached_tokens") or 0)
    if not cached_tokens:
        cached_tokens = int(usage.get("cached_tokens") or 0)

    # Context OS is the authoritative source for the actual attempt.  A
    # provider chain may retain ``last_usage`` from an older/fallback provider,
    # which previously made the header ring disagree with ContextTrace (for
    # example, showing the configured 380k model while the request actually ran
    # against an 8k fixture model).  Prefer the latest successful agent attempt
    # for model/window/usage while retaining the provider snapshot as a legacy
    # fallback when Context OS is disabled.
    if _latest_attempt is not None:
        model = str(_latest_attempt.get("model_id") or model)
        if _latest_attempt.get("actual_input_tokens") is not None:
            prompt_tokens = int(_latest_attempt["actual_input_tokens"])
        if _latest_attempt.get("actual_output_tokens") is not None:
            completion_tokens = int(_latest_attempt["actual_output_tokens"])
        if _latest_attempt.get("actual_cache_read_tokens") is not None:
            cached_tokens = int(_latest_attempt["actual_cache_read_tokens"])

    # Resolve model context window + thresholds.
    context_window = 32_000
    effective_pct = 0.95
    compact_at_pct = 0.70
    recall_sweet = 16_000
    if _resolve_model is not None and model:
        try:
            _info = _resolve_model(model)
            context_window = int(_info.context_window)
            effective_pct = float(_info.effective_pct)
            compact_at_pct = min(float(_info.compact_at_pct), 0.70)
            recall_sweet = int(_info.recall_sweet_tokens)
        except Exception:  # noqa: BLE001 - 模型信息读不到时用默认值
            logger.debug("context_usage_model_info_failed", exc_info=True)
    if _latest_attempt is not None and int(_latest_attempt.get("context_window") or 0) > 0:
        context_window = int(_latest_attempt["context_window"])
    payload = {
        "session_id": session_id,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
        "context_window": context_window,
        "effective_ceiling": int(context_window * effective_pct),
        "compact_at": int(context_window * compact_at_pct),
        "recall_sweet": recall_sweet,
        "updated_at": time.time(),
    }
    if _attempts:
        payload["attempts"] = _attempts
    return {"type": "context_usage", "payload": payload}


async def _emit_context_usage(
    originator_ws: WebSocket,
    session_id: str,
    *,
    provider_chain: Any | None,
    fallback_provider: Any | None = None,
    root_run_id: str | None = None,
    request_id: str | None = None,
    binding_epoch: int | None = None,
    frozen_provider_id: str | None = None,
    frozen_model_id: str | None = None,
) -> None:
    """Snapshot usage and push to originator + (for "default" session) peers.
    Best-effort: any error is logged at debug and swallowed."""
    try:
        msg = _snapshot_context_usage_event(
            session_id,
            provider_chain=provider_chain,
            fallback_provider=fallback_provider,
        )
        if msg is None:
            return
        payload = dict(msg.get("payload") or {})
        session_db = service_context.get("session_db")
        if session_db is None or not hasattr(session_db, "record_context_usage_sample"):
            return
        if session_db is not None:
            attempts = payload.get("attempts") or []
            latest_attempt = (
                attempts[-1]
                if isinstance(attempts, list) and attempts and isinstance(attempts[-1], dict)
                else {}
            )
            try:
                binding = (
                    await session_db.get_session_provider_binding(session_id)
                    if binding_epoch is None
                    else {}
                )
                stable_request_id = str(
                    latest_attempt.get("request_id") or request_id or ""
                ).strip()
                stable_attempt_id = str(latest_attempt.get("attempt_id") or "legacy").strip()
                if not stable_request_id:
                    raise ValueError("context usage provider sample lacks request identity")
                await session_db.record_context_usage_sample(
                    {
                        "source_event_id": (
                            "context-attempt:"
                            f"{root_run_id or stable_request_id}:"
                            f"{stable_request_id}:{stable_attempt_id}"
                        ),
                        "session_id": session_id,
                        "run_id": root_run_id,
                        "request_id": stable_request_id,
                        "attempt_id": stable_attempt_id,
                        "event_type": "provider_attempt",
                        "binding_epoch": (
                            binding.get("binding_epoch", 0)
                            if binding_epoch is None
                            else binding_epoch
                        ),
                        "provider_id": (
                            latest_attempt.get("provider_id")
                            or frozen_provider_id
                            or binding.get("provider_id")
                        ),
                        "model_id": (payload.get("model", "") or frozen_model_id),
                        "tokens_after": payload.get("prompt_tokens", 0),
                        "prompt_tokens": payload.get("prompt_tokens", 0),
                        "completion_tokens": payload.get("completion_tokens", 0),
                        "cached_tokens": payload.get("cached_tokens", 0),
                        "context_window": payload.get("context_window", 0),
                        "effective_ceiling": payload.get("effective_ceiling", 0),
                        "estimate_method": (
                            "provider_usage"
                            if latest_attempt.get("actual_input_tokens") is not None
                            else "legacy_provider_usage"
                        ),
                        "completed_at": payload.get("updated_at", time.time()),
                        "created_at": payload.get("updated_at", time.time()),
                        "metadata": {
                            "model": payload.get("model", ""),
                            "cached_tokens": payload.get("cached_tokens", 0),
                            "completion_tokens": payload.get("completion_tokens", 0),
                            "compression_source": latest_attempt.get("compression_source", ""),
                            "compression_model": latest_attempt.get("actual_compression_model", ""),
                        },
                    }
                )
                payload = await session_db.get_context_usage_state(session_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "context_usage_history_persist_failed sid=%s err=%s",
                    session_id,
                    exc,
                    exc_info=True,
                )
                try:
                    payload = await session_db.get_context_usage_state(session_id)
                except Exception as read_exc:  # noqa: BLE001
                    logger.debug(
                        "context_usage_authority_read_failed sid=%s err=%s",
                        session_id,
                        read_exc,
                        exc_info=True,
                    )
                    return
        payload = await _attach_context_snapshot_metadata(session_db, payload)
        payload = _cache_context_usage_state(payload)
        msg = {"type": "context_usage", "payload": payload}
        try:
            await originator_ws.send_json(msg)
        except Exception as exc:  # noqa: BLE001
            logger.debug("context_usage_send_originator_failed err=%s", exc, exc_info=True)
        await _broadcast_default_chat_peers(originator_ws, msg)
    except Exception as exc:  # noqa: BLE001
        logger.debug("context_usage_emit_failed sid=%s err=%s", session_id, exc, exc_info=True)


def _approx_tokens(text: str | None) -> int:
    """委托 tokens.count_text_tokens（CJK-aware）做统一口径估算。

    全后端 token 计数走唯一入口 ``deskpet.agent.tokens.count_text_tokens``
    （CJK×4 加权，可选 tiktoken，启发式回落），消除散落口径不一致。
    用于驱动 "where did my context go" 饼图；权威值仍是 LLM 自己的
    ``prompt_tokens``（已在 ring 里）。Returns 0 on falsy input."""
    if not text:
        return 0

    return count_text_tokens(text)


_PUBLIC_CONTEXT_SENSITIVE = re.compile(
    r"(?i)(authorization\s*:|bearer\s+[a-z0-9._-]+|api[_ -]?key|"
    r"cookie\s*:|(?:^|\s)(?:/Users/|/home/|[A-Za-z]:\\)|"
    r"reasoning_content|private[_ -]?reasoning)"
)


def _public_context_section(raw: Any) -> dict[str, Any] | None:
    """Normalize one already-redacted snapshot section for Inspector."""

    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip()[:80]
    label = str(raw.get("label") or "").strip()[:160]
    if not kind or not label:
        return None
    estimated = raw.get("estimated_tokens")
    tokens = (
        int(estimated)
        if isinstance(estimated, int) and not isinstance(estimated, bool) and estimated >= 0
        else None
    )
    availability = str(raw.get("availability") or "").strip()
    if availability not in {"available", "unavailable"}:
        availability = "available" if tokens is not None else "unavailable"
    public_ref = raw.get("ref")
    public_text = public_ref.strip() if isinstance(public_ref, str) else ""
    public_preview = (
        public_text[:600]
        if public_text and not _PUBLIC_CONTEXT_SENSITIVE.search(public_text)
        else None
    )
    item: dict[str, Any] = {
        "kind": kind,
        "label": label,
        "tokens": tokens,
        "token_source": "estimated" if tokens is not None else "unavailable",
        "availability": availability,
        "public_preview": public_preview,
        "preview_truncated": bool(isinstance(public_ref, str) and len(public_ref.strip()) > 600),
    }
    count = raw.get("count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        item["count"] = count
    return item


async def _read_context_breakdown_authority(
    session_id: str,
) -> dict[str, Any]:
    """Read only the durable public SDK snapshot and Context usage state."""

    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session_id is required")
    context_sdb = service_context.get("session_db")
    if context_sdb is None or not hasattr(context_sdb, "get_latest_sdk_context_public_snapshot"):
        raise RuntimeError("sdk_context_public_snapshot_authority_unavailable")

    public_snapshot = await context_sdb.get_latest_sdk_context_public_snapshot(sid)
    usage = await _read_context_usage_authority(sid, attach_snapshot=False)
    if public_snapshot is not None:
        if not isinstance(public_snapshot, dict):
            raise RuntimeError("sdk_context_public_snapshot_invalid")
        if str(public_snapshot.get("session_id") or "").strip() != sid:
            raise RuntimeError("sdk_context_public_snapshot_session_mismatch")

    raw_sections = public_snapshot.get("sections", []) if isinstance(public_snapshot, dict) else []
    sections = [
        normalized
        for raw in raw_sections
        if isinstance(raw_sections, list)
        if (normalized := _public_context_section(raw)) is not None
    ]
    snapshot_available = isinstance(public_snapshot, dict)
    binding = public_snapshot.get("provider_binding", {}) if snapshot_available else {}
    budget = public_snapshot.get("budget", {}) if snapshot_available else {}
    binding = binding if isinstance(binding, dict) else {}
    budget = budget if isinstance(budget, dict) else {}
    has_measurement = bool(usage.get("has_measurement"))
    prompt_tokens = usage.get("prompt_tokens")
    measured_prompt_tokens = (
        int(prompt_tokens)
        if has_measurement
        and isinstance(prompt_tokens, int)
        and not isinstance(prompt_tokens, bool)
        and prompt_tokens >= 0
        else None
    )
    source = str(usage.get("source") or "")
    context_state = (
        "legacy_incomplete"
        if usage.get("legacy_incomplete")
        else "measured"
        if has_measurement
        else "binding_only"
        if source == "binding_only"
        else "unavailable"
    )
    snapshot_version = public_snapshot.get("snapshot_version") if snapshot_available else None
    usage_version = usage.get("version")
    return {
        "session_id": sid,
        "snapshot_id": (
            str(public_snapshot.get("snapshot_id") or "").strip() or None
            if snapshot_available
            else None
        ),
        "snapshot_version": (
            int(snapshot_version)
            if isinstance(snapshot_version, int)
            and not isinstance(snapshot_version, bool)
            and snapshot_version >= 0
            else None
        ),
        "snapshot_fingerprint": (
            str(public_snapshot.get("snapshot_fingerprint") or "")[:160]
            if snapshot_available
            else ""
        ),
        "sample_id": str(usage.get("sample_id") or "").strip() or None,
        "usage_version": (
            int(usage_version)
            if isinstance(usage_version, int)
            and not isinstance(usage_version, bool)
            and usage_version >= 0
            else None
        ),
        "availability": "available" if snapshot_available else "unavailable",
        "context_state": context_state,
        "model": str(binding.get("model_id") or usage.get("model_id") or usage.get("model") or "")[
            :160
        ],
        "sections": sections,
        "total_estimated_tokens": (
            sum(item["tokens"] or 0 for item in sections) if snapshot_available else None
        ),
        "last_usage_prompt_tokens": measured_prompt_tokens,
        "context_window": budget.get("context_window") if snapshot_available else None,
        "effective_ceiling": budget.get("effective_ceiling") if snapshot_available else None,
        "compact_at": budget.get("compact_at") if snapshot_available else None,
        "updated_at": usage.get("updated_at"),
        "ts": time.time(),
    }


async def _handle_context_breakdown_request(
    websocket: Any,
    payload: Any,
    *,
    owner_session_id: str,
) -> bool:
    """Serve one owner/session/correlation-fenced Inspector query."""

    body = payload if isinstance(payload, dict) else {}
    requested_sid = str(body.get("session_id") or "").strip()
    correlation_id = str(body.get("request_id") or body.get("correlation_id") or "").strip()
    owner_sid = str(owner_session_id or "").strip()
    if (
        not requested_sid
        or not correlation_id
        or len(correlation_id) > 200
        or not owner_sid
        or requested_sid != owner_sid
    ):
        await websocket.send_json(
            {
                "type": "error",
                "payload": {
                    "code": "context_breakdown_request_invalid",
                    "message": "Context Inspector request identity is invalid",
                },
            }
        )
        return False
    breakdown = await _read_context_breakdown_authority(requested_sid)
    if breakdown.get("session_id") != requested_sid:
        raise RuntimeError("context_breakdown_session_mismatch")
    await websocket.send_json(
        {
            "type": "context_breakdown_response",
            "payload": {**breakdown, "correlation_id": correlation_id},
        }
    )
    return True
