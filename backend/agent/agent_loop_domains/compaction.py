# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Context compaction: compressor model resolution, the compression run, snapshots and coverage jobs."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

from deskpet.agent.compression_model_resolver import CompressionModelCandidate
from deskpet.agent.context_segment_store import CoverageCommitProof
from deskpet.agent.context_snapshot_store import SnapshotConflictError, await_snapshot_commit_ack
from deskpet.agent.context_usage import context_usage_sample_id
from deskpet.agent.tokens import count_messages_tokens as _cmt
from llm.context_messages import CONTEXT_MESSAGE_META_KEY
from providers import context_os_e2e_hooks

from .core import AgentLoopCore
from .support import AgentEvent, _RunState, logger


def _context_message_signature(message: Mapping[str, Any]) -> str:
    wire = {key: value for key, value in message.items() if key != CONTEXT_MESSAGE_META_KEY}
    metadata = message.get(CONTEXT_MESSAGE_META_KEY)
    identity = {
        "wire": wire,
        "fragment_id": (metadata.get("fragment_id") if isinstance(metadata, Mapping) else None),
        "causal_group_id": (
            metadata.get("causal_group_id") if isinstance(metadata, Mapping) else None
        ),
    }
    return json.dumps(identity, ensure_ascii=False, sort_keys=True, default=str)


def _capture_context_remount(
    messages: list[dict[str, Any]], *, recent_groups: int = 6
) -> dict[str, tuple[Any, ...]]:
    protected: list[dict[str, Any]] = []
    transcript_groups: list[list[dict[str, Any]]] = []
    open_group_id: str | None = None
    open_group: list[dict[str, Any]] = []

    def flush_group() -> None:
        nonlocal open_group_id, open_group
        if open_group:
            transcript_groups.append(open_group)
        open_group_id = None
        open_group = []

    for message in messages:
        metadata = message.get(CONTEXT_MESSAGE_META_KEY)
        if not isinstance(metadata, Mapping):
            continue
        placement = str(metadata.get("placement") or "")
        lifetime = str(metadata.get("lifetime") or "")
        source = str(metadata.get("source") or "")
        if placement == "prefix" and (
            bool(metadata.get("protected"))
            or lifetime in {"platform", "stable", "task"}
            or source.startswith("project_rules")
        ):
            protected.append(dict(message))
        if placement != "transcript" or metadata.get("trim_policy") == "page_in":
            continue
        causal_group_id = str(metadata.get("causal_group_id") or "")
        if causal_group_id:
            if open_group_id and open_group_id != causal_group_id:
                flush_group()
            if not open_group:
                open_group_id = causal_group_id
            open_group.append(dict(message))
        else:
            flush_group()
            transcript_groups.append([dict(message)])
    flush_group()
    tail = transcript_groups[-max(1, int(recent_groups)) :]
    return {
        "protected": tuple(protected),
        "tail_groups": tuple(tuple(group) for group in tail),
    }


def _remount_context_after_compaction(
    messages: list[dict[str, Any]],
    remount: Mapping[str, Any],
) -> list[dict[str, Any]]:
    restored = [dict(message) for message in messages]
    existing = {_context_message_signature(message) for message in restored}
    missing_prefix = [
        dict(message)
        for message in tuple(remount.get("protected", ()) or ())
        if _context_message_signature(message) not in existing
    ]
    if missing_prefix:
        insert_at = next(
            (
                index
                for index, message in enumerate(restored)
                if str(message.get("role") or "") != "system"
            ),
            len(restored),
        )
        restored[insert_at:insert_at] = missing_prefix
        existing.update(_context_message_signature(item) for item in missing_prefix)

    for raw_group in tuple(remount.get("tail_groups", ()) or ()):
        group = [dict(message) for message in raw_group]
        signatures = {_context_message_signature(message) for message in group}
        if signatures and signatures.issubset(existing):
            continue
        # A partial tool group is more dangerous than a duplicate: remove the
        # surviving fragment, then restore the original group atomically.
        restored = [
            message for message in restored if _context_message_signature(message) not in signatures
        ]
        insert_at = len(restored)
        while insert_at > 0:
            metadata = restored[insert_at - 1].get(CONTEXT_MESSAGE_META_KEY)
            if not (isinstance(metadata, Mapping) and metadata.get("placement") == "control"):
                break
            insert_at -= 1
        restored[insert_at:insert_at] = group
        existing = {_context_message_signature(message) for message in restored}
    return restored


@dataclass
class ContextCompactedEvent(AgentEvent):
    """WI-1B-2 压缩可观测 — flag ``features.ctx_observability`` ON 时,上下文
    压缩命中后发出一条轻量事件。main.py 转成 ws
    ``{type: "context_compacted", reduction, tokens_in, tokens_out, model}``,
    前端在圈圈 gauge 附近浮一条 toast「已压缩,省 N token」(N = tokens_in -
    tokens_out)。flag OFF 时本事件**永不构造**(零开销,字节级 BC)。"""

    reduction: float = 0.0  # 节省比 0~1（middle 段 reduction_ratio）
    tokens_in: int = 0  # 压缩前 middle 段 token
    tokens_out: int = 0  # 压缩后摘要 token
    model: str = ""  # 摘要所用模型（可选）
    source_event_id: str = ""  # durable producer identity
    based_on_sample_id: str = ""  # exact measured sample compacted
    occurred_at: float = 0.0  # producer time for observability; not lineage authority

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "context_compacted"


class CompactionMixin(AgentLoopCore):
    """Context compaction: compressor model resolution, the compression run, snapshots and coverage jobs."""

    __slots__ = ()

    async def _compact_context(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """上下文超限时压缩。"""

        # WI-4.0 compaction: after budget guard, before LLM call.
        # Reuses _budget.estimated_tokens (just computed above).
        # compressor=None (flag off) → short-circuit, zero overhead.
        # Operates in-place on working_messages (not assemble — BC).
        # System messages (skill_prelude/persona/frozen) are kept verbatim
        # by _partition inside compress() — "不绕 assemble" constraint met.
        if self.compressor is not None:
            # The compressor service is process-wide, while ContextManager
            # is frozen per Run from the selected model.  Use an isolated
            # routed view so concurrent Sessions cannot bleed limits.
            state._active_compressor = self.compressor
            try:
                state._ctx_cfg_for_compression = getattr(self._ctx, "config", None)
                state._model_info_for_compression = getattr(
                    state._ctx_cfg_for_compression, "model_info", None
                )
                state._for_context = getattr(self.compressor, "for_context", None)
                if callable(state._for_context) and state._model_info_for_compression is not None:
                    state._active_compressor = state._for_context(
                        context_window=int(state._model_info_for_compression.context_window),
                        threshold_percent=min(
                            float(state._model_info_for_compression.compact_at_pct),
                            0.70,
                        ),
                        effective_pct=float(state._model_info_for_compression.effective_pct),
                    )
            except Exception:  # noqa: BLE001 -- legacy fake/BC compressor
                state._active_compressor = self.compressor
            try:
                state._ctoken_est = getattr(state._budget, "estimated_tokens", 0)
            except Exception:  # noqa: BLE001
                state._ctoken_est = 0
            # 第 3 刀: real usage 兜底(见 _last_real_prompt_tokens 注释)。
            if state._last_real_prompt_tokens > state._ctoken_est:
                state._ctoken_est = state._last_real_prompt_tokens
            # ★ 第 4 刀(真机测压缩发现的核心 bug 修复): 直接数【即将发送的
            # working_messages】的 token。原来只用 _budget.estimated_tokens(被
            # BudgetAllocator 压到 window×0.6,低于压缩阈值 window×0.8)+
            # _last_real_prompt_tokens(每条消息开头重置 0、且单轮聊天不迭代第二次
            # 拿不到真值) → 两路都够不到阈值 → 压缩**永不触发**(实测 12 消息/
            # 1746 真 token 仍不压)。直接数 working_messages 调用前可得、不受
            # allocator 截断、不延迟,是最可靠的触发信号(复用统一计数,优化 #1+#3)。
            try:
                state._wm_tokens = _cmt(state.working_messages)
                if state._wm_tokens > state._ctoken_est:
                    state._ctoken_est = state._wm_tokens
            except Exception:  # noqa: BLE001
                pass
            state._ctx_should_compress = False
            try:
                state._ctx_cfg = getattr(self._ctx, "config", None)
                # WI-1B-3: adaptive_compact_pct ON → 按本 run 是否 agentic
                # (工具调用计数 ≥ 阈值) 取微调后的触发线;OFF → for() 直接
                # 返回原 compact_at_tokens 属性(字节级 BC,等价旧分支)。
                state._ctx_compact_at = None
                state._for_fn = getattr(state._ctx_cfg, "compact_at_tokens_for", None)
                if callable(state._for_fn):
                    state._agentic = state._run_tool_calls >= state._AGENTIC_TOOL_THRESHOLD
                    state._ctx_compact_at = state._for_fn(state._agentic)
                else:
                    state._ctx_compact_at = getattr(state._ctx_cfg, "compact_at_tokens", None)
                if state._ctx_compact_at is not None:
                    state._ctx_should_compress = state._ctoken_est >= int(state._ctx_compact_at) > 0
            except Exception:  # noqa: BLE001
                state._ctx_should_compress = False
            if (
                state._budget_block_pending
                or state._active_compressor.should_compress(state._ctoken_est)
                or state._ctx_should_compress
            ):
                try:
                    async with aclosing(self._run_compression(state)) as _phase:
                        async for _event in _phase:
                            yield _event
                except Exception as _cmp_exc:  # noqa: BLE001
                    # Compaction is advisory — never abort the loop on it.
                    state._latest_compression_report = {
                        "requested_model": self._requested_compression_model(),
                        "failure": str(_cmp_exc)[:200],
                    }
                    logger.debug(
                        "p1_4_compaction_failed sid=%s iter=%d err=%s",
                        state.session_id,
                        state.iteration,
                        str(_cmp_exc)[:200],
                    )

    async def _run_compression(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """执行一次压缩：取压缩模型、调用压缩器、处理结果。"""

        state._gt = None
        if self.session_goal_store is not None:
            state._gt_fn = getattr(self.session_goal_store, "get_goal_text", None)
            if callable(state._gt_fn):
                state._gt = state._gt_fn(state.session_id)
        state._compression_resolution = None
        state._compaction_cycle_id = None
        state._requested_compression_model = self._requested_compression_model()
        if state.context_metadata_enabled:
            state._compaction_cycle_index += 1
            state._compaction_cycle_id = f"{state.context_request_id or state.tid}:compaction:{state._compaction_cycle_index}"
            await self._flush_context_snapshot(
                cycle_id=state._compaction_cycle_id,
                session_id=state.session_id,
                request_id=state.context_request_id or state.tid,
                working_messages=state.working_messages,
                prepared_context=state.prepared_context,
                current_tool_set=state.current_tool_set,
            )
            state._compression_resolution = self._resolve_compression_model(
                state.provider_chain,
                requested_model=state._requested_compression_model,
            )

            if context_os_e2e_hooks.consume_context_os_e2e_fault("compactor_model_error"):
                raise RuntimeError("compactor_model_error")
            await self._execute_coverage_compaction_jobs(
                cycle_id=state._compaction_cycle_id,
                prepared_context=state.prepared_context,
                resolution=state._compression_resolution,
            )
        await self._preflush_task_state(state)
        state._compress_kwargs: dict[str, Any] = {"goal_text": state._gt}
        if state._compression_resolution is not None:
            state._candidate = state._compression_resolution.candidates[0]
            state._compress_kwargs.update(
                resolved_provider=state._candidate.provider,
                resolved_model=state._candidate.model_id,
                compaction_cycle_id=state._compaction_cycle_id,
            )
        if self._provider_workload_context_factory is not None:
            state._compress_kwargs["workload_context"] = self._provider_workload_context_factory(
                "agent.context_compaction"
            )
        state._context_remount = (
            _capture_context_remount(state.working_messages)
            if state.context_metadata_enabled
            else None
        )
        if not state.context_metadata_enabled:
            if context_os_e2e_hooks.consume_context_os_e2e_fault("compactor_model_error"):
                raise RuntimeError("compactor_model_error")
        state._cresult = await state._active_compressor.compress(
            state.working_messages, **state._compress_kwargs
        )
        state._resolved_candidate = (
            state._compression_resolution.candidates[0]
            if state._compression_resolution is not None
            else None
        )
        state._latest_compression_report = {
            "requested_model": state._requested_compression_model,
            "resolved_model": (
                state._resolved_candidate.model_id
                if state._resolved_candidate is not None
                else str(getattr(self.compressor, "model", "") or "")
            ),
            "actual_model": (
                state._resolved_candidate.model_id
                if state._resolved_candidate is not None
                else str(getattr(self.compressor, "model", "") or "")
            ),
            "provider": (
                state._resolved_candidate.provider_id
                if state._resolved_candidate is not None
                else ""
            ),
            "source": (
                state._compression_resolution.source
                if state._compression_resolution is not None
                else "legacy"
            ),
            "failure": str(getattr(state._cresult, "error", "") or "")[:200],
        }
        async with aclosing(self._apply_compression_result(state)) as _phase:
            async for _event in _phase:
                yield _event

    async def _preflush_task_state(self, state: _RunState) -> None:
        """压缩前把任务态写进文件记忆（每轮运行最多一次，失败不阻断压缩）。"""

        # WI-4b pre-flush: 摘掉中段前把任务态写进 L1(跨 session 记任务)。
        # best-effort + 每 run 限一次(latch),失败绝不阻断压缩。
        if (
            not state.context_metadata_enabled
            and self.file_memory is not None
            and not state._preflush_done
        ):
            state._preflush_done = True
            try:
                state._last_user = ""
                for state._m in reversed(state.working_messages):
                    if state._m.get("role") == "user":
                        state._last_user = str(state._m.get("content") or "")[:500]
                        break
                state._flush_parts = []
                if state._gt:
                    state._flush_parts.append(f"目标: {state._gt}")
                if state._last_user:
                    state._flush_parts.append(f"最近请求: {state._last_user}")
                if state._flush_parts:
                    state._flush_body = "[任务态快照/task-state] " + "; ".join(state._flush_parts)
                    await self.file_memory.append("memory", state._flush_body, salience=0.6)
                    logger.info(
                        "wi4b_preflush_l1 sid=%s tid=%s chars=%d",
                        state.session_id,
                        state.tid,
                        len(state._flush_body),
                    )
            except Exception as _pf_exc:  # noqa: BLE001
                logger.debug(
                    "wi4b_preflush_failed sid=%s err=%s",
                    state.session_id,
                    str(_pf_exc)[:120],
                )

    async def _apply_compression_result(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """压缩成功后替换工作消息并记录报告。"""

        if getattr(state._cresult, "compressed", False):
            state._skill_compaction_happened = True
            state.working_messages = state._cresult.messages
            if state._context_remount is not None:
                state.working_messages = _remount_context_after_compaction(
                    state.working_messages,
                    state._context_remount,
                )
            # WI-4.2: re-inline skill bodies after compaction so
            # the LLM doesn't lose skill step details that were
            # in the compressed "middle" messages.
            state.working_messages = self._remount_skills(
                state.working_messages,
                state.session_id,
                prepared_context=state.prepared_context,
            )
            if not state._compaction_warn_logged:
                logger.info(
                    "p1_4_compaction_fired sid=%s tid=%s iter=%d reduction=%s",
                    state.session_id,
                    state.tid,
                    state.iteration,
                    getattr(state._cresult, "reduction_ratio", "?"),
                )
                state._compaction_warn_logged = True
            # WI-1B-2 压缩可观测: flag ON 时额外 emit metrics +
            # yield 轻量事件。flag OFF → 整块 short-circuit(BC,
            # 既不 record 也不构造/yield ContextCompactedEvent)。
            if self.ctx_observability:
                state._ctx_in = int(getattr(state._cresult, "input_tokens", 0) or 0)
                state._ctx_out = int(getattr(state._cresult, "output_tokens", 0) or 0)
                state._ctx_ratio = float(getattr(state._cresult, "reduction_ratio", 0.0) or 0.0)
                try:
                    # 按需导入：测试替身（observability.metrics_sink）
                    from observability.metrics_sink import (
                        record as _ctx_metric,
                    )

                    _ctx_metric(
                        "context_compacted",
                        {
                            "ratio": round(state._ctx_ratio, 3),
                            "model": getattr(self.compressor, "model", "") or "",
                            "count": state._ctx_in - state._ctx_out,
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass
                state._actual_compaction_model = (
                    state._resolved_candidate.model_id
                    if state._resolved_candidate is not None
                    else str(getattr(self.compressor, "model", "") or "")
                )
                state._compaction_source_event_id = (
                    f"context-compaction:{state.session_id}:{state.tid}:{state.iteration}"
                )
                yield ContextCompactedEvent(
                    task_id=state.tid,
                    iteration=state.iteration,
                    reduction=round(state._ctx_ratio, 3),
                    tokens_in=state._ctx_in,
                    tokens_out=state._ctx_out,
                    model=state._actual_compaction_model,
                    source_event_id=(state._compaction_source_event_id),
                    based_on_sample_id=str(
                        getattr(
                            self,
                            "_context_usage_basis_sample_id",
                            "",
                        )
                        or ""
                    ),
                    occurred_at=time.time(),
                )

                self._context_usage_basis_sample_id = context_usage_sample_id(
                    state.session_id,
                    state._compaction_source_event_id,
                )

    def _requested_compression_model(self) -> str:
        provider = self.compression_model_provider
        if callable(provider):
            value = str(provider() or "").strip()
            if value:
                return value
        return self.compression_model

    def _resolve_compression_model(
        self,
        provider_chain: Any,
        *,
        requested_model: str | None = None,
    ) -> Any:
        """Resolve one exact compaction provider; never walk a fallback chain."""

        if self.compression_model_resolver is None:
            return None
        providers = provider_chain
        if not providers:
            providers = getattr(self.llm, "providers", ()) or ()
            if isinstance(providers, dict):
                providers = tuple(providers.values())

        candidates = tuple(
            CompressionModelCandidate(
                provider_id=str(
                    getattr(provider, "id", None)
                    or getattr(provider, "provider_id", None)
                    or type(provider).__name__
                ),
                model_id=str(getattr(provider, "model", None) or "unknown"),
                provider=provider,
            )
            for provider in providers
        )
        return self.compression_model_resolver.resolve(
            str(requested_model or self._requested_compression_model()),
            session_chain=candidates,
        )

    async def _flush_context_snapshot(
        self,
        *,
        cycle_id: str,
        session_id: str,
        request_id: str,
        working_messages: list[dict[str, Any]],
        prepared_context: Any,
        current_tool_set: Any,
    ) -> Any:
        """Project authorities and durably flush one derived cycle snapshot."""

        if self.context_projector is None or self.context_snapshot_store is None:
            return None

        if context_os_e2e_hooks.consume_context_os_e2e_fault("snapshot_cas_timeout"):
            raise TimeoutError("snapshot_cas_timeout")
        last_user = next(
            (
                str(message.get("content") or "")
                for message in reversed(working_messages)
                if message.get("role") == "user"
            ),
            "",
        )

        async def _project() -> Any:
            return await self.context_projector.project(
                effective_sid=session_id,
                request_id=request_id,
                user_text=last_user,
            )

        snapshot = await _project()
        if snapshot is None:
            return None
        handle = getattr(prepared_context, "active_snapshot_handle", None)
        if (
            handle is not None
            and getattr(handle, "session_id", None) == snapshot.session_id
            and getattr(handle, "task_scope_id", None) == snapshot.task_scope_id
        ):
            expected_revision = int(handle.row_revision)
        else:
            stored = await self.context_snapshot_store.get(
                snapshot.session_id, snapshot.task_scope_id
            )
            expected_revision = int(stored.handle.row_revision) if stored is not None else 0
        tool_summary = self._prepared_toolset_summary(current_tool_set)

        async def _write(projection: Any, expected: int) -> Any:

            task = asyncio.create_task(
                self.context_snapshot_store.flush_once(
                    cycle_id,
                    projection,
                    expected_row_revision=expected,
                    prepared_toolset_summary=tool_summary,
                )
            )
            return await await_snapshot_commit_ack(task)

        try:
            receipt = await _write(snapshot, expected_revision)
        except Exception as exc:
            if not isinstance(exc, SnapshotConflictError):
                raise
            # One bounded re-read/re-project retry.  No authority is mutated;
            # the store remains a derived CAS projection only.
            latest = await self.context_snapshot_store.get(
                snapshot.session_id, snapshot.task_scope_id
            )
            refreshed = await _project()
            if refreshed is None or latest is None:
                raise
            if latest.last_compaction_cycle_id != cycle_id:
                # Another compaction cycle won the CAS. Replaying this older
                # cycle on top of it can make stale authority look current;
                # keep the raw context and let the next request replan.
                raise
            receipt = await _write(refreshed, latest.handle.row_revision)
        prepared_context.active_snapshot_handle = receipt.new_handle
        return receipt

    async def _execute_coverage_compaction_jobs(
        self,
        *,
        cycle_id: str,
        prepared_context: Any,
        resolution: Any,
    ) -> None:
        jobs = tuple(getattr(prepared_context, "coverage_compaction_jobs", ()) or ())
        if not jobs:
            return
        if self.context_segment_store is None or resolution is None:
            raise RuntimeError("coverage_compaction_owner_unavailable")
        candidate = resolution.candidates[0]
        committed_ranges: set[tuple[int, int, str]] = set()

        for job in jobs:
            source_range = (
                int(job.first_message_id),
                int(job.last_message_id),
                str(job.source_hash),
            )
            if source_range in committed_ranges:
                continue
            summary = await self.compressor.compress_coverage_job(
                job,
                resolved_provider=candidate.provider,
                resolved_model=candidate.model_id,
                compaction_cycle_id=cycle_id,
                **(
                    {
                        "workload_context": self._provider_workload_context_factory(
                            "agent.context_compaction"
                        )
                    }
                    if self._provider_workload_context_factory is not None
                    else {}
                ),
            )
            proof = CoverageCommitProof(
                message_ids=tuple(int(item) for item in job.message_ids),
                source_hash=str(job.source_hash),
                valid=True,
                child_source_hashes=tuple(job.child_source_hashes),
            )
            logger.info(
                "coverage_commit_started cycle_id=%s first_message_id=%s last_message_id=%s",
                cycle_id,
                job.first_message_id,
                job.last_message_id,
            )
            await asyncio.wait_for(
                self.context_segment_store.commit_summary(
                    str(job.session_id),
                    tuple(job.child_segment_ids),
                    summary_text=summary,
                    proof=proof,
                    provider_id=str(candidate.provider_id),
                    model_id=str(candidate.model_id),
                    token_estimates={"deskpet-conservative-v1": max(1, len(summary) // 3)},
                ),
                timeout=10.0,
            )
            logger.info(
                "coverage_commit_completed cycle_id=%s first_message_id=%s last_message_id=%s",
                cycle_id,
                job.first_message_id,
                job.last_message_id,
            )
            committed_ranges.add(source_range)
