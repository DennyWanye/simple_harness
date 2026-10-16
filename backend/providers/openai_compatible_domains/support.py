# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Shared helpers for the split class."""

from __future__ import annotations

import httpx
import structlog

# 原来不带名字，名字取第一次打日志的调用模块（就是原模块）；
# 搬家后钉住这个名字
logger = structlog.get_logger("providers.openai_compatible")


def _record_tool_choice_none_unsupported(provider: str, model: str) -> None:
    try:
        # 按需导入：测试替身（observability.metrics_sink）
        from observability.metrics_sink import record

        record(
            "tool_choice_none_unsupported",
            {"provider": provider, "model": model},
        )
    except Exception:  # noqa: BLE001
        pass


def _is_tool_choice_400(exc: httpx.HTTPStatusError, body: str) -> bool:
    return exc.response.status_code == 400 and "tool_choice" in (body or "")


def _merge_code_params(payload: dict, code_params: dict | None) -> dict:
    """code-session-model-params: splice the per-session request fragment.

    Top-level keys (e.g. ``reasoning_effort``) are set directly; an
    ``extra_body`` dict is shallow-merged. Total/pure: empty/None →
    payload unchanged. Never overrides ``model``/``messages``.
    """
    if not code_params:
        return payload
    for k, v in code_params.items():
        if k in ("model", "messages"):
            continue
        if k == "extra_body" and isinstance(v, dict):
            eb = dict(payload.get("extra_body") or {})
            eb.update(v)
            payload["extra_body"] = eb
        else:
            payload[k] = v
    return payload


def _temperature_for_model(model: str, requested: float) -> float:
    """Clamp model-specific fixed temperature contracts at the wire edge.

    Kimi K3's OpenAI-compatible endpoint rejects every value except 1.
    Keeping this normalization in the provider makes streaming, non-streaming
    fallback and plain chat follow the same contract, regardless of the
    user's global relay temperature.
    """
    normalized = (model or "").strip().lower()
    if normalized == "kimi-k3" or normalized.endswith("/kimi-k3"):
        return 1.0
    return requested


def _stabilize_prefix(messages: list[dict]) -> list[dict]:
    """返回 messages 的副本，抹平会破坏 prefix-cache 字节哈希的抖动。

    纪律（抄 DeepSeek-TUI，design.md D4）：**绝不改写历史前缀**。
    上游同一逻辑轮在不同请求里可能：
      - tool-call-only assistant：某次带 ``content: None``、某次完全
        没有 ``content`` 字段、某次 ``content: ""`` —— 三种序列化字节
        不同，prefix cache 全 miss。统一成稳定 ``""``。
      - ``reasoning_content``：某轮带空串、某轮没这个 key —— "有 key
        但空" 和 "无 key" 序列化字节也不同。空/None 一律删除该 key。

    只动 assistant 行的这两个字段；system/user/tool 原样透传。不删
    消息、不改下标（tool_call/tool_result 配对靠下标，动不得）。
    输入不被原地修改（返回深拷副本里需要改的那几行）。
    """
    out: list[dict] = []
    for m in messages or []:
        if m.get("role") != "assistant":
            out.append(m)
            continue
        fixed = dict(m)
        # tool-call-only / content 缺失或 None → 稳定 "" placeholder
        if fixed.get("content") is None:
            fixed["content"] = ""
        # reasoning_content 空/None → 删 key（不要 "空 key" vs "无 key" 抖）
        rc = fixed.get("reasoning_content")
        if rc is None or rc == "":
            fixed.pop("reasoning_content", None)
        out.append(fixed)
    return out


def _extract_cached_tokens(usage: dict | None) -> int | None:
    """防御式取回包里的 cached_tokens（命中的 prompt token 数）。

    位置优先级：
      1. usage.prompt_tokens_details.cached_tokens  ← OpenAI / deepseek 标准
      2. usage.cached_tokens                        ← 部分中转站平铺

    任何缺失 / 类型不对 → 返回 ``None``（调用方记 ``unknown``，
    绝不让脏数据把 LLM 调用搞崩 —— 这是硬约束）。
    """
    if not isinstance(usage, dict):
        return None
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        v = details.get("cached_tokens")
        if isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v
    v = usage.get("cached_tokens")
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    return None


def _log_cache_hit_rate(usage: dict | None, *, where: str) -> None:
    """落一条 prompt-cache 命中率日志（1.3.4）。

    回包带 cached_tokens → 记 cached/prompt + 命中率百分比；
    字段缺失（the relay 现状常见）→ 记 ``cached_tokens=unknown``，绝不崩。
    """
    try:
        cached = _extract_cached_tokens(usage)
        prompt = None
        if isinstance(usage, dict):
            pt = usage.get("prompt_tokens")
            if isinstance(pt, int) and not isinstance(pt, bool):
                prompt = pt
        if cached is None:
            logger.info(
                "p4s25_prompt_cache_hit",
                where=where,
                cached_tokens="unknown",
                prompt_tokens=prompt if prompt is not None else "unknown",
                hit_rate="unknown",
            )
            return
        rate = round(cached / prompt, 4) if prompt and prompt > 0 else 0.0
        logger.info(
            "p4s25_prompt_cache_hit",
            where=where,
            cached_tokens=cached,
            prompt_tokens=prompt if prompt is not None else "unknown",
            hit_rate=rate,
        )
    except Exception as exc:  # noqa: BLE001 — 日志埋点绝不能影响主流程
        logger.debug("p4s25_prompt_cache_hit_log_failed err=%s", str(exc)[:120])


def _is_anthropic_endpoint(base_url: str) -> bool:
    """Heuristic: should we emit Anthropic-style cache_control marks?

    True only for endpoints that we have evidence handle the field.
    Most OpenAI-compat proxies (the relay, sealos, vllm, ollama) reject
    unknown message keys silently — observed empirically when the relay
    started returning empty content after we started emitting
    cache_control. Better to no-op than break working flows.
    """
    u = (base_url or "").lower()
    return "anthropic.com" in u or "claude" in u


def _stamp_cache_control(messages: list[dict], *, boundary_index: int | None = None) -> list[dict]:
    """Copy messages and mark the explicit or legacy system breakpoint.

    ``boundary_index`` is a zero-based inclusive stable-prefix boundary.  The
    latest system message at or before it is marked.  ``None`` preserves the
    legacy behaviour of marking the last system message in the whole request.
    """
    if boundary_index is not None:
        if isinstance(boundary_index, bool) or not isinstance(boundary_index, int):
            raise TypeError("cache boundary must be an integer or None")
        if boundary_index < 0 or boundary_index >= len(messages):
            raise ValueError("cache boundary is outside the message list")
        search_end = boundary_index
    else:
        search_end = len(messages) - 1
    last_sys_idx = -1
    for i, m in enumerate(messages):
        if i > search_end:
            break
        if m.get("role") == "system":
            last_sys_idx = i
    if last_sys_idx < 0:
        return messages
    out: list[dict] = []
    for i, m in enumerate(messages):
        if i == last_sys_idx and isinstance(m.get("content"), str):
            stamped = dict(m)
            stamped["cache_control"] = {"type": "ephemeral"}
            out.append(stamped)
        else:
            out.append(m)
    return out
