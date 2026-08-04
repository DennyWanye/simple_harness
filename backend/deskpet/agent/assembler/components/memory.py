# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Memory component (P4-S7 task 12.5).

Wraps the three-layer :class:`~deskpet.memory.manager.MemoryManager`. On
every turn this component asks the manager for:

1. L1 frozen snapshot (`{memory, user}` raw file text) — optional.
2. L2 recent session messages.
3. L3 RRF hybrid recall hits.

It then packages the results into a single markdown-ish block that the
LLM can consume naturally. L1 is treated as ``"frozen"`` bucket (it only
changes when the user edits their MEMORY.md / USER.md — cache-friendly),
while L2 + L3 go into ``"dynamic"`` because they shift every turn.

Core memory (L1) MAY NEVER be removed by policy (spec D9). The component
honours ``policy.memory.l1 == "off"`` by skipping the *block* but still
adds L1 content to the frozen bucket — just with empty strings.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

import structlog

from deskpet.agent.assembler.bundle import ContextFragment, Slice
from deskpet.agent.assembler.components.base import Component, ComponentContext
from deskpet.agent.task_reference import TaskReferenceResolver
from deskpet.memory.entity_extractor import _STOPWORDS
from deskpet.types.task_reference import (
    TaskReferenceResolution,
    TaskReferenceStatus,
)

logger = structlog.get_logger(__name__)


_L2_CONTEXT_LABEL = "以下为较早的对话记录，可能涉及其他话题，仅供背景参考。"
_CURRENT_REQUEST_NUDGE = (
    "当前请求是本轮唯一任务；先前对话仅为背景，若与当前请求冲突，以当前请求为准。"
)
_ANAPHORA_PREFIXES = (
    "它",
    "他",
    "她",
    "其",
    "这个",
    "那个",
    "这些",
    "那些",
    "这",
    "那",
    "继续",
    "然后",
    "what",
    "why",
    "how",
    "which",
    "this",
    "that",
    "these",
    "those",
)
_ANAPHORA_WORDS = {"it", "they", "them", "he", "she", "his", "her", "their"}
_TASK_REFERENCE_RESOLVER = TaskReferenceResolver()


class MemoryComponent:
    """Provides L1 snapshot + L2/L3 recall results."""

    name: str = "memory"

    async def provide(self, ctx: ComponentContext) -> Slice:
        start = time.monotonic()
        mm = ctx.memory_manager
        if mm is None:
            # No memory manager wired — graceful empty. The assembler
            # never "fails" a turn just because L3 is offline.
            return Slice(
                component_name=self.name,
                text_content="",
                tokens=0,
                priority=100,
                bucket="frozen",
                meta={"status": "no_memory_manager"},
            )

        policy_memory = ctx.policy.memory
        call_policy = {
            "l1": policy_memory.l1,
            "l2_top_k": policy_memory.l2_top_k,
            "l3_top_k": policy_memory.l3_top_k,
            "session_id": ctx.session_id,
        }
        if isinstance(ctx.config, dict) and ctx.config.get(
            "provider_workload_context"
        ) is not None:
            call_policy["provider_workload_context"] = ctx.config[
                "provider_workload_context"
            ]
        task_conversation = (
            ctx.config.get("task_conversation", {})
            if isinstance(ctx.config, dict)
            else {}
        )
        task_reference_intent = None
        if isinstance(task_conversation, dict):
            root_run_id = task_conversation.get("root_run_id")
            task_scope_id = task_conversation.get("task_scope_id")
            if root_run_id:
                call_policy["root_run_id"] = str(root_run_id)
            if task_scope_id:
                call_policy["task_scope_id"] = str(task_scope_id)
            if root_run_id or task_scope_id:
                # Semantic message recall is session-wide today. Root-local
                # execution must never absorb another task transcript.
                call_policy["l3_top_k"] = 0
                task_reference_intent = _TASK_REFERENCE_RESOLVER.intent(
                    ctx.user_message
                )
                if task_reference_intent is not None:
                    # Fetch enough typed Run groups to avoid the old
                    # "latest four rows" guess. Tool rows and empty shells
                    # remain excluded in MemoryManager.
                    call_policy["session_reference_top_k"] = 96
        # L2 is the continuity baseline; L3 is optional enrichment.  Bound the
        # semantic lookup inside MemoryManager so a cold/busy embedder cannot
        # consume this component's whole 1.5s deadline and erase L2 with it.
        remaining_ms = ctx.time_remaining_ms()
        if remaining_ms is not None:
            call_policy["l3_timeout_s"] = max(
                0.05,
                min(0.75, (remaining_ms / 1000.0) * 0.5),
            )
        l2_page_in = getattr(policy_memory, "l2_page_in", "always")
        if l2_page_in == "off":
            call_policy["l2_top_k"] = 0
        elif (
            l2_page_in == "followup"
            and not _starts_with_anaphora(ctx.user_message)
        ):
            call_policy["l2_top_k"] = 0

        # The E2E control plane is dev+loopback gated.  A forced L3 timeout
        # must exercise the real component degradation path without erasing
        # L2 continuity, so skip only semantic enrichment for this request.
        e2e_l3_fault = None
        try:
            from deskpet.context_os_e2e_hooks import consume_context_os_e2e_fault

            e2e_l3_fault = consume_context_os_e2e_fault("l3_timeout")
        except RuntimeError as exc:
            e2e_l3_fault = str(exc)
        if e2e_l3_fault:
            call_policy["l3_top_k"] = 0

        try:
            result = await mm.recall(ctx.user_message, policy=call_policy)
        except Exception as exc:  # defensive — manager is supposed to be safe
            return Slice(
                component_name=self.name,
                text_content="",
                priority=100,
                bucket="frozen",
                meta={"error": str(exc), "error_type": type(exc).__name__},
            )

        l2_rows = result.get("l2") or []
        reference_candidates = result.get("l2_reference") or []
        typed_candidates = _TASK_REFERENCE_RESOLVER.candidates(
            reference_candidates,
        )
        reference_resolution = _TASK_REFERENCE_RESOLVER.resolve(
            task_reference_intent,
            typed_candidates,
        )
        reference_rows = _rows_for_resolved_reference(
            reference_candidates,
            reference_resolution,
        )
        if reference_rows:
            combined: list[dict[str, Any]] = []
            seen_ids: set[object] = set()
            for row in (*reference_rows, *l2_rows):
                if not isinstance(row, dict):
                    continue
                row_id = row.get("id")
                dedupe_key: object = (
                    ("id", row_id)
                    if row_id is not None
                    else (
                        "content",
                        row.get("role"),
                        row.get("content"),
                        row.get("root_run_id"),
                    )
                )
                if dedupe_key in seen_ids:
                    continue
                seen_ids.add(dedupe_key)
                combined.append(row)
            l2_rows = combined
        l3_hits = result.get("l3") or []

        # The text chat path persists the current user row before assembly.
        # Remove only that exact row by id; text-based dedupe would corrupt a
        # legitimate repeated user message ("再试一次", "再试一次").
        current_row_deduped = False
        if ctx.current_message_id is not None and l2_rows:
            before = len(l2_rows)
            l2_rows = [
                row
                for row in l2_rows
                if not (
                    isinstance(row, dict)
                    and row.get("id") == ctx.current_message_id
                )
            ]
            current_row_deduped = len(l2_rows) != before

        topic_shift_gate = bool(getattr(policy_memory, "topic_shift_gate", False))
        relabel_l2 = bool(getattr(policy_memory, "relabel_l2", False))
        anchor_current = bool(getattr(policy_memory, "anchor_current", False))
        topic_shift_threshold = float(
            getattr(policy_memory, "topic_shift_threshold", 0.35)
        )
        l2_keep_on_shift = int(getattr(policy_memory, "l2_keep_on_shift", 1))
        topic_shift_min_len = int(getattr(policy_memory, "topic_shift_min_len", 16))

        l2_count_in = len(l2_rows)
        gate_sim: float | None = None
        shift_path = "off"
        l2_truncated = False
        if l2_rows and topic_shift_gate:
            emb = _get_embedder(mm)
            l2_concat = "\n".join(
                (row.get("content") or "").strip()
                for row in l2_rows
                if isinstance(row, dict) and (row.get("content") or "").strip()
            )
            gate_timeout_s = 0.25
            remaining_ms = ctx.time_remaining_ms()
            if remaining_ms is not None:
                gate_timeout_s = max(
                    0.05,
                    min(gate_timeout_s, (remaining_ms / 1000.0) * 0.5),
                )
            try:
                gate_sim = await asyncio.wait_for(
                    _topic_similarity(emb, ctx.user_message, l2_concat),
                    timeout=gate_timeout_s,
                )
            except asyncio.TimeoutError:
                logger.info(
                    "task_drift_sim_skip",
                    reason="component_deadline",
                    timeout_s=round(gate_timeout_s, 3),
                )
                gate_sim = None
            # Primary signal = embedding cosine. But the BGE-M3 subprocess
            # can be lock-contended (vector-worker backfill / research load)
            # and time out under the ~1500ms component budget — real E2E
            # showed gate_sim=None reliably right after boot. So when the
            # embedding is unavailable we fall back to a zero-latency lexical
            # overlap signal instead of failing open (which would let the
            # drift survive). Cross-domain shifts (Rust vs CATL) have ~0
            # content-token overlap → reliably caught even with no embedder.
            if gate_sim is not None:
                topic_diff = gate_sim < topic_shift_threshold
                shift_path = "embed"
            else:
                topic_diff = _lexical_topic_shift(ctx.user_message, l2_concat)
                shift_path = "lexical"
            is_shift = (
                topic_diff
                and len(ctx.user_message.strip()) >= topic_shift_min_len
                and not _starts_with_anaphora(ctx.user_message)
            )
            if is_shift:
                keep = max(0, l2_keep_on_shift)
                l2_rows = l2_rows[-keep:] if keep else []
                l2_truncated = True

        frozen_text = _render_l1(result.get("l1"))
        # P4-S21 #16 fix: L2 raw rows are now promoted to bundle.history
        # by the assembler stitcher (see meta["l2_history"] below). The
        # text rendering only carries L3 semantic recall — keeping L2
        # *also* in the system memory_block would just double-charge tokens
        # AND risk the LLM ignoring it as instruction noise. Old behaviour
        # was `_render_l2_l3(...)`; we now emit L3 only.
        dynamic_text = _render_l3_only(l3_hits)

        # Frozen slice for L1
        combined = frozen_text
        if dynamic_text:
            # Two sections merged; the frozen half comes first so the
            # whole block stays cache-friendly as long as L1 doesn't
            # change. When L2/L3 shift we only invalidate the tail.
            combined = f"{frozen_text}\n\n{dynamic_text}".strip()

        tokens = _approx_tokens(combined)
        elapsed_ms = (time.monotonic() - start) * 1000.0

        features = ctx.config.get("features", {}) if isinstance(ctx.config, dict) else {}
        context_os_on = bool(
            features.get("context_os_v1", False)
            if isinstance(features, dict)
            else getattr(features, "context_os_v1", False)
        )

        # Promote L2 raw rows to OpenAI message format. The assembler's
        # `_stitch` reads meta["l2_history"] and assigns to bundle.history.
        l2_history: list[dict[str, Any]] = []
        if l2_rows and relabel_l2:
            l2_history.append({"role": "system", "content": _L2_CONTEXT_LABEL})
        reference_catalog = _render_task_reference_catalog(
            reference_resolution
        )
        if reference_catalog:
            l2_history.append(
                {"role": "system", "content": reference_catalog}
            )
        reference_workspaces = _render_reference_workspaces(
            reference_rows, ctx.config
        )
        if reference_workspaces:
            l2_history.append(
                {"role": "system", "content": reference_workspaces}
            )
        for row in l2_rows:
            role = row.get("role")
            content = (row.get("content") or "").strip()
            # System summaries (is_summary=1) live in messages too — keep
            # them as system-role hints so the LLM treats them as context
            # without confusing "assistant" turn boundaries.
            allowed_roles = (
                ("user", "assistant", "system", "tool")
                if context_os_on
                else ("user", "assistant", "system")
            )
            has_tool_calls = role == "assistant" and bool(row.get("tool_calls"))
            if (not content and not has_tool_calls) or role not in allowed_roles:
                continue
            entry: dict[str, Any] = {"role": role, "content": content}
            if context_os_on:
                for key in ("tool_calls", "tool_call_id", "name"):
                    if row.get(key) is not None:
                        entry[key] = row[key]
            # P4-S24: thinking-mode round-trip. If a prior assistant
            # message stored a reasoning_content (DeepSeek V4 Pro /
            # Qwen3 thinking / GLM-4.5), echo it back into history so
            # the LLM doesn't 400 with "reasoning_content must be
            # passed back". NULL/empty for non-thinking models — skip
            # the field entirely so plain Ollama / GPT-4o payloads
            # stay clean.
            if role == "assistant":
                rc = row.get("reasoning_content")
                if rc:
                    entry["reasoning_content"] = rc
            l2_history.append(entry)

        # Task-drift observability anchor (grep `task_drift_context_gate` in
        # the tauri-dev stderr log). Makes Tier 1 (relabel/anchor) and Tier 2
        # (topic-shift truncation) verifiable in real E2E without a prompt
        # dump — and doubles as production telemetry for drift incidents.
        logger.info(
            "task_drift_context_gate",
            session_id=ctx.session_id,
            relabel_applied=bool(l2_rows and relabel_l2),
            anchor_applied=anchor_current,
            topic_shift_gate=topic_shift_gate,
            l2_truncated=l2_truncated,
            shift_path=shift_path,
            gate_sim=(round(gate_sim, 4) if gate_sim is not None else None),
            l2_count_in=l2_count_in,
            l2_count_out=len(l2_rows),
        )

        meta = {
            "l1_bytes": len(frozen_text),
            "l2_count": len(l2_rows),
            "l2_reference_count": len(reference_rows),
            "l2_reference_candidate_count": len(reference_candidates),
            "task_reference_status": reference_resolution.status.value,
            "task_reference_selected": (
                reference_resolution.selected_reference_id
            ),
            "task_reference_candidates": [
                {
                    "reference_id": candidate.reference_id,
                    "root_run_id": candidate.root_run_id,
                    "task_scope_id": candidate.task_scope_id,
                }
                for candidate in reference_resolution.candidates
            ],
            "l3_count": len(l3_hits),
            "current_row_deduped": current_row_deduped,
            "latency_ms": round(elapsed_ms, 2),
            "l2_history": l2_history,
            **(
                {"l3_degraded": True, "l3_failure": str(e2e_l3_fault)}
                if e2e_l3_fault
                else {}
            ),
            **(
                {"late_system_nudge": _CURRENT_REQUEST_NUDGE}
                if anchor_current
                else {}
            ),
        }
        if context_os_on:
            fragments: list[ContextFragment] = []
            if frozen_text:
                fragments.append(
                    ContextFragment(
                        fragment_id="memory:l1",
                        source="memory:l1",
                        role="system",
                        content=frozen_text,
                        lifetime="stable",
                        placement="prefix",
                        priority=100,
                        trim_policy="never",
                        protected=True,
                        reason="l1_core_memory",
                        cache_scope="session",
                    )
                )
            if dynamic_text:
                fragments.append(
                    ContextFragment(
                        fragment_id="memory:l3",
                        source="memory:l3",
                        role="system",
                        content=dynamic_text,
                        lifetime="retrieved",
                        placement="prefix",
                        priority=55,
                        trim_policy="page_in",
                        reason="semantic_recall",
                        meta={
                            "page_in_kind": "memory_l3",
                            "page_in_source": "memory:l3",
                            "page_in_content": dynamic_text,
                        },
                    )
                )
            return Slice(
                component_name=self.name,
                fragments=fragments,
                tokens=tokens,
                priority=100,
                meta=meta,
            )

        return Slice(
            component_name=self.name,
            text_content=combined,
            tokens=tokens,
            priority=100,
            bucket="dynamic" if dynamic_text else "frozen",
            meta=meta,
        )


def _get_embedder(mm: Any) -> Any:
    r = getattr(mm, "_retriever", None)
    return getattr(r, "_embedder", None) if r is not None else None


async def _topic_similarity(emb: Any, current: str, l2_concat: str) -> float | None:
    if emb is None:
        logger.info("task_drift_sim_skip", reason="no_embedder")
        return None
    try:
        if not emb.is_ready():
            logger.info("task_drift_sim_skip", reason="not_ready")
            return None
        if emb.is_mock():
            logger.info("task_drift_sim_skip", reason="mock")
            return None
        _t = time.monotonic()
        # 1.0s inner cap: the BGE-M3 subprocess is already warm by this
        # point (L3 recall encoded the query earlier this turn), so a
        # 2-text encode is normally well under this. The cap still guards
        # the ~1500ms component fan-out budget — on the rare slow encode we
        # fail-open (keep all L2) rather than time out the whole slice.
        vecs = await asyncio.wait_for(
            emb.encode([current, l2_concat]), timeout=1.0
        )
        _ms = round((time.monotonic() - _t) * 1000.0, 1)
    except asyncio.TimeoutError:
        logger.info("task_drift_sim_skip", reason="encode_timeout")
        return None
    except Exception as exc:  # noqa: BLE001 — fail-open, never break assembly
        logger.info("task_drift_sim_skip", reason="encode_error", err=str(exc))
        return None
    if getattr(vecs, "shape", [0])[0] < 2:
        logger.info("task_drift_sim_skip", reason="bad_shape")
        return None
    sim = float(vecs[0] @ vecs[1])
    logger.info("task_drift_sim_ok", sim=round(sim, 4), encode_ms=_ms)
    return sim


def _content_tokens(text: str) -> set[str]:
    """Cheap content-token set for lexical topic-shift detection.

    No segmenter dependency: ascii words (len≥2) + CJK character bigrams.
    Crude but free — used only as a fallback when the embedder is
    unavailable. Common function words appear in both sides so they bias
    toward "keep" (fewer false truncations), which is the safe direction.
    """
    text = text.strip().lower()
    tokens: set[str] = set()
    for w in re.findall(r"[a-z0-9]{2,}", text):
        if w not in _ANAPHORA_WORDS:
            tokens.add(w)
    for run in re.findall(r"[一-鿿]+", text):
        if len(run) == 1:
            tokens.add(run)
            continue
        for i in range(len(run) - 1):
            tokens.add(run[i:i + 2])
    return tokens


def _lexical_topic_shift(current: str, l2_concat: str) -> bool:
    """Fallback topic-shift signal when the embedder is unavailable.

    True when <15% of the current request's content tokens also appear in
    the recent L2 history — i.e. the request talks about something the
    recent conversation didn't. Cross-domain shifts (e.g. Rust vs CATL)
    score ~0 overlap and are reliably caught; same-topic follow-ups share
    many tokens and are kept.
    """
    cur = _content_tokens(current)
    if len(cur) < 2:
        return False  # too little signal → don't truncate (safe)
    hist = _content_tokens(l2_concat)
    if not hist:
        return False
    overlap = len(cur & hist) / len(cur)
    return overlap < 0.15


def _starts_with_anaphora(current: str) -> bool:
    text = current.strip()
    if not text:
        return False
    if len(text) < 10:
        return True

    lower = text.lower()
    if any(lower.startswith(prefix) for prefix in _ANAPHORA_PREFIXES):
        return True

    first = _first_token(text)
    if not first:
        return False
    first_lower = first.lower()
    return (
        first_lower in _ANAPHORA_WORDS
        or first in _STOPWORDS
        or first_lower.capitalize() in _STOPWORDS
    )


def _rows_for_resolved_reference(
    rows: list[dict[str, Any]],
    resolution: TaskReferenceResolution,
    *,
    max_rows: int = 12,
    max_chars: int = 16_000,
    per_row_chars: int = 6_000,
) -> list[dict[str, Any]]:
    """Page one exact typed Run/task group under a hard context budget."""

    if resolution.status is not TaskReferenceStatus.RESOLVED:
        return []
    selected = next(
        (
            item
            for item in resolution.candidates
            if item.reference_id == resolution.selected_reference_id
        ),
        None,
    )
    if selected is None:
        return []
    valid: list[tuple[int, dict[str, Any], str]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "")
        content = str(raw.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        if selected.root_run_id:
            same_identity = (
                str(raw.get("root_run_id") or "")
                == selected.root_run_id
            )
        else:
            same_identity = bool(
                selected.task_scope_id
                and str(raw.get("task_scope_id") or "")
                == selected.task_scope_id
            )
        if not same_identity:
            continue
        valid.append((index, raw, content))

    # Keep the latest bounded tail of the exact selected task, then restore
    # chronological order. No unrelated fallback rows are permitted.
    ranked = list(reversed(valid))
    chosen: list[tuple[int, dict[str, Any]]] = []
    used_chars = 0
    for index, row, content in ranked:
        if len(chosen) >= max_rows or used_chars >= max_chars:
            break
        row_budget = min(per_row_chars, max_chars - used_chars)
        if row_budget <= 0:
            break
        selected = dict(row)
        if len(content) > row_budget:
            suffix = "\n[较长历史消息已截断]"
            content_budget = row_budget - len(suffix)
            if content_budget <= 0:
                break
            selected["content"] = (
                content[:content_budget].rstrip() + suffix
            )
        chosen.append((index, selected))
        used_chars += len(str(selected.get("content") or ""))

    chosen.sort(key=lambda item: item[0])
    return [row for _index, row in chosen]


def _render_task_reference_catalog(
    resolution: TaskReferenceResolution,
) -> str:
    if resolution.status is TaskReferenceStatus.AMBIGUOUS:
        lines = [
            "[跨任务引用需要确认]",
            "用户的话可能指向多个历史任务。不要猜，也不要直接执行；"
            "请用下面的候选摘要向用户确认具体是哪一个。",
        ]
        for candidate in resolution.candidates[:8]:
            lines.append(
                f"- {candidate.reference_id} | "
                f"task_scope={candidate.task_scope_id or '-'} | "
                f"{candidate.preview}"
            )
        return "\n".join(lines)
    if resolution.status is TaskReferenceStatus.MISSING:
        return (
            "[跨任务引用未解析]\n"
            "用户提到了历史任务，但当前 Session 没有带稳定 Run/task 身份的候选。"
            "请询问具体任务，不要猜测。"
        )
    return ""


def _render_reference_workspaces(
    rows: list[dict[str, Any]],
    config: Any,
) -> str:
    """Render host-verified prior task workspaces, never model-made paths."""

    if not rows or not isinstance(config, dict):
        return ""
    workspace_context = config.get("workspace_context")
    if not isinstance(workspace_context, dict):
        return ""
    raw_current = str(
        workspace_context.get("root")
        or workspace_context.get("active_path")
        or ""
    ).strip()
    if not raw_current:
        return ""

    current = Path(raw_current).expanduser().resolve(strict=False)
    base = current.parent
    by_scope: dict[str, tuple[int, Path, tuple[str, ...], bool]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        scope = str(row.get("task_scope_id") or "").strip()
        if not re.fullmatch(r"task-[A-Za-z0-9_-]+", scope):
            continue
        candidate = (base / scope).resolve(strict=False)
        try:
            candidate.relative_to(base)
        except ValueError:
            continue
        if candidate == current or not candidate.is_dir():
            continue
        try:
            children = tuple(
                sorted(
                    (
                        f"{child.name}/" if child.is_dir() else child.name
                        for child in candidate.iterdir()
                    ),
                    key=str.lower,
                )[:8]
            )
            if not children:
                continue
            project_markers = tuple(
                sorted(
                    str(marker.relative_to(candidate)).replace("\\", "/")
                    for pattern in (
                        "project.godot",
                        "*/project.godot",
                        "package.json",
                        "*/package.json",
                        "pyproject.toml",
                        "*/pyproject.toml",
                    )
                    for marker in candidate.glob(pattern)
                    if marker.is_file()
                )
            )
        except OSError:
            continue
        by_scope[scope] = (
            index,
            candidate,
            project_markers,
            bool(project_markers),
        )

    if not by_scope:
        return ""
    ordered = sorted(
        by_scope.values(),
        key=lambda item: (item[3], item[0]),
        reverse=True,
    )[:8]
    lines = [
        "[相关会话工作区（Host 已验证，仅用于定位已有内容）]",
        "优先复用已有内容；不要把打开、继续或运行任务改写为重新创建。",
        "Windows Shell 使用下面的 drive-colon 路径（如 F:/...），不要改写成 /f/...",
        "启动已有 GUI/游戏时优先使用 process_start 或 app_launch，"
        "不要用前台 run_shell 阻塞后续截图和按键。",
    ]
    for _index, candidate, markers, _has_marker in ordered:
        top_level = ", ".join(
            f"project={marker}" for marker in markers
        )
        if not top_level:
            try:
                top_level = ", ".join(
                    f"{child.name}/" if child.is_dir() else child.name
                    for child in sorted(candidate.iterdir(), key=lambda p: p.name.lower())[:8]
                )
            except OSError:
                top_level = ""
        lines.append(
            f"- {candidate.as_posix()}"
            + (f" ({top_level})" if top_level else "")
        )
    return "\n".join(lines)


def _first_token(text: str) -> str:
    cjk = re.match(r"[\u4e00-\u9fff]+", text)
    if cjk:
        span = cjk.group(0)
        candidates = sorted(_STOPWORDS, key=len, reverse=True)
        for word in candidates:
            if span.startswith(word):
                return word
        return span[:2]

    word = re.match(r"[A-Za-z]+", text)
    return word.group(0) if word else text[:1]


# ---------------------------------------------------------------------------
# Renderers — keep layout stable for prompt caching
# ---------------------------------------------------------------------------
def _render_l1(l1: Any) -> str:
    """Render L1 snapshot into a stable block.

    Returns empty string if l1 is missing / empty — avoids inserting a
    blank section that would still occupy tokens in the prompt.
    """
    if not isinstance(l1, dict):
        return ""
    memory = (l1.get("memory") or "").strip()
    user = (l1.get("user") or "").strip()
    if not memory and not user:
        return ""

    parts: list[str] = ["## 记忆档案 (L1, frozen)"]
    if memory:
        parts.append("### MEMORY.md\n" + memory)
    if user:
        parts.append("### USER.md\n" + user)
    return "\n\n".join(parts)


def _render_l3_only(l3_hits: list[dict[str, Any]]) -> str:
    """Render only L3 (RRF semantic recall) into a system-prompt-friendly block.

    L2 (recent conversation) used to be rendered here too, but as of P4-S21
    #16 it's promoted to ``bundle.history`` (real OpenAI message turns).
    Keeping L2 in this block as well would double-charge tokens AND risk
    the LLM ignoring it as instruction noise — see the "VPN bug" in
    `plans/2026-05-07-msi-known-issues.md` #16.

    L3 stays as text because it pulls semantically-similar bits from
    *anywhere* (other sessions, archived summaries, etc.) — those don't
    belong in the conversation history per se, they're "retrieved
    memories" the LLM should consult.
    """
    if not l3_hits:
        return ""

    parts: list[str] = ["## 相关记忆片段 (L3, RRF recall)"]
    lines = []
    for hit in l3_hits:
        text = (hit.get("text") or "").strip()
        if not text:
            continue
        if len(text) > 240:
            text = text[:240] + "…"
        score = hit.get("score")
        src = hit.get("source", "?")
        score_str = (
            f"{score:.3f}" if isinstance(score, (int, float)) else "?"
        )
        lines.append(f"- [{src} {score_str}] {text}")
    if not lines:
        return ""
    parts.append("\n".join(lines))
    return "\n\n".join(parts)


# Kept under the old name for any out-of-tree caller that imports it.
# Internally everything routes through `_render_l3_only` now.
def _render_l2_l3(
    l2_rows: list[dict[str, Any]], l3_hits: list[dict[str, Any]]
) -> str:
    """Deprecated — kept for backwards compatibility with tests. Use
    ``_render_l3_only`` for new code. L2 rendering is intentionally a
    no-op now; L2 lives in ``bundle.history`` as real messages."""
    del l2_rows  # ignored; see docstring
    return _render_l3_only(l3_hits)


def _approx_tokens(text: str) -> int:
    """委托统一入口 ``deskpet.agent.tokens.count_text_tokens``（CJK-aware）。

    全后端 token 计数走同一口径，足够做 budget allocation。
    """
    if not text:
        return 0
    from deskpet.agent.tokens import count_text_tokens
    return count_text_tokens(text)


# For structural typing / Protocol conformance checking in tests.
_ASSERT_PROTOCOL: Component = MemoryComponent()  # noqa: E501
