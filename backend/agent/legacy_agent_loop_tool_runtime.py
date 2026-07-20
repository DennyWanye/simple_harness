"""Stateless legacy tool-round collaborator for AgentLoop compatibility."""
from __future__ import annotations
import asyncio, copy, inspect, json, logging, uuid
from dataclasses import dataclass
from typing import Any, Optional
from agent import errors as agent_errors
from agent.workflow_trace import traced_call
from deskpet.workflows.trace import SpanKind
from llm.types import ChatResponse, ToolCall
logger = logging.getLogger("deskpet.agent.loop")
@dataclass(slots=True)
class LegacyToolRoundState:
    response: ChatResponse
    tool_schemas: list[dict[str, Any]]; loop_user_request: str
    is_sentinel_run: bool
    task_id: str
    iteration: int
    totals: dict[str, int]
    session_id: str
    working_messages: list[dict[str, Any]]
    context_metadata_enabled: bool
    current_tool_set: Any
    prepared_context: Any
    llm_kwargs: dict[str, Any]
    context_request_id: str | None
    tool_execution_context: Any
    skill_compaction_happened: bool
    force_finish_queued: bool
    tools_used_count: int; run_tool_calls: int; terminal: bool = False
class LegacyAgentLoopToolRuntime:
    """Runs one legacy tool round; owns no run, task, queue, or session state."""
    __slots__ = ()
    async def run_round(self, loop: Any, state: LegacyToolRoundState):
        from agent import agent_loop as _loop_api
        response, tool_schemas, loop_user_request, is_sentinel_run, tid, iteration, totals, session_id, working_messages = state.response, state.tool_schemas, state.loop_user_request, state.is_sentinel_run, state.task_id, state.iteration, state.totals, state.session_id, state.working_messages
        context_metadata_enabled, current_tool_set, prepared_context, llm_kwargs, context_request_id, tool_execution_context = state.context_metadata_enabled, state.current_tool_set, state.prepared_context, state.llm_kwargs, state.context_request_id, state.tool_execution_context
        _skill_compaction_happened, _force_finish_queued, tools_used_count, _run_tool_calls = state.skill_compaction_happened, state.force_finish_queued, state.tools_used_count, state.run_tool_calls
        try:
            for tc in response.tool_calls:
                _loop_api._inject_loop_user_request(
                    tc,
                    tool_schemas,
                    loop_user_request=loop_user_request,
                )
            if is_sentinel_run and any(tc.name == "deepresearch" for tc in response.tool_calls):
                yield _loop_api.FinalEvent(
                    type="final",
                    task_id=tid,
                    iteration=iteration,
                    content=(
                        "Auto-resume sentinel cannot start deepresearch. "
                        "Please explicitly send a research request."
                    ),
                    total_input_tokens=totals["input"],
                    total_output_tokens=totals["output"],
                    total_cache_read_tokens=totals["cache_read"],
                    total_cache_write_tokens=totals["cache_write"],
                )
                state.terminal = True
                return
            accepted_async_calls = [
                tc
                for tc in response.tool_calls
                if self._tool_completion_semantics(loop, tc.name) == "accepted_async"
            ]
            if accepted_async_calls and len(response.tool_calls) != 1:
                rejection = json.dumps(
                    {
                        "ok": False,
                        "error": "accepted_async_must_be_single",
                        "hint": (
                            "An accepted_async workflow tool must be the only "
                            "tool call in the assistant message. Retry it alone."
                        ),
                    },
                    ensure_ascii=False,
                )
                await traced_call(
                    name="accepted_async.preflight",
                    kind=SpanKind.GATE,
                    lifecycle_stage="gate",
                    attributes={
                        "iteration": iteration,
                        "gate": "accepted_async_preflight",
                        "tool_call_count": len(response.tool_calls),
                    },
                    invoke=lambda: False,
                )
                import json as _json_at
                rejected_assistant: dict[str, Any] = {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": _json_at.dumps(
                                    tc.arguments, ensure_ascii=False
                                ),
                            },
                        }
                        for tc in response.tool_calls
                    ],
                }
                if response.reasoning_content:
                    rejected_assistant["reasoning_content"] = response.reasoning_content
                _rejected_group_id = (
                    f"agent-loop:{tid}:iteration:{iteration}:async-rejection"
                )
                _loop_api._append_loop_transcript(
                    working_messages,
                    rejected_assistant,
                    metadata_enabled=context_metadata_enabled,
                    source="agent_loop.assistant_tool_group",
                    role="assistant",
                    fragment_id=f"{_rejected_group_id}:assistant",
                    causal_group_id=_rejected_group_id,
                )
                for tc in response.tool_calls:
                    yield _loop_api.ToolCallEvent(
                        type="tool_call",
                        task_id=tid,
                        iteration=iteration,
                        tool_call=tc,
                    )
                    yield _loop_api.ToolResultEvent(
                        type="tool_result",
                        task_id=tid,
                        iteration=iteration,
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        result=rejection,
                    )
                    _loop_api._append_loop_transcript(
                        working_messages,
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.name,
                            "content": rejection,
                        },
                        metadata_enabled=context_metadata_enabled,
                        source="agent_loop.tool_result",
                        role="tool",
                        fragment_id=f"{_rejected_group_id}:tool:{tc.id}",
                        causal_group_id=_rejected_group_id,
                    )
                return
            if loop.activity_store is not None:
                from agent.session_activity import args_hash as _args_hash  # noqa: PLC0415
                repeat_hit: tuple[str, int] | None = None  # (tool_name, count)
                sa = await loop.activity_store.get(session_id)
                if sa is not None:
                    sig_window = dict(sa.tool_signature_window)
                    for tc in response.tool_calls:
                        sig = f"{tc.name}:{_args_hash(tc.arguments)}"
                        prior = int(sig_window.get(sig, 0))
                        if prior >= (loop._signature_repeat_threshold - 1):
                            repeat_hit = (tc.name, prior + 1)
                            break
                if repeat_hit is not None:
                    name, count = repeat_hit
                    nudge = _loop_api._REPEAT_NUDGE_MSG.format(name=name, count=count)
                    _repeat_anchor = _loop_api._latest_context_anchor(
                        working_messages,
                        fallback=f"agent-loop:{tid}:iteration:{iteration}:response",
                    )
                    if response.content:
                        _repeat_reply = _loop_api._append_loop_transcript(
                            working_messages,
                            {
                                "role": "assistant",
                                "content": response.content,
                            },
                            metadata_enabled=context_metadata_enabled,
                            source="agent_loop.signature_repeat.trigger",
                            role="assistant",
                            fragment_id=(
                                f"agent-loop:{tid}:iteration:{iteration}:"
                                "signature-repeat-trigger"
                            ),
                        )
                        _repeat_anchor = (
                            _loop_api._context_fragment_id(_repeat_reply) or _repeat_anchor
                        )
                    _loop_api._append_loop_control(
                        working_messages,
                        {"role": "system", "content": nudge},
                        metadata_enabled=context_metadata_enabled,
                        source="agent_loop.signature_repeat_nudge",
                        anchor_after=_repeat_anchor,
                        fragment_id=(
                            f"agent-loop:{tid}:iteration:{iteration}:"
                            f"signature-repeat-nudge:{count}"
                        ),
                    )
                    logger.info(
                        "p5s2_signature_repeat_nudge sid=%s tid=%s iter=%d "
                        "name=%s count=%d",
                        session_id, tid, iteration, name, count,
                    )
                    return
            import json as _json_at
            asst_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": _json_at.dumps(
                                tc.arguments, ensure_ascii=False
                            ),
                        },
                    }
                    for tc in response.tool_calls
                ],
            }
            if response.reasoning_content:
                asst_msg["reasoning_content"] = response.reasoning_content
            _tool_group_id = f"agent-loop:{tid}:iteration:{iteration}:tool-group"
            _loop_api._append_loop_transcript(
                working_messages,
                asst_msg,
                metadata_enabled=context_metadata_enabled,
                source="agent_loop.assistant_tool_group",
                role="assistant",
                fragment_id=f"{_tool_group_id}:assistant",
                causal_group_id=_tool_group_id,
            )
            activation_calls = [
                tc for tc in response.tool_calls if tc.name == "tool_activate"
            ]
            if activation_calls and len(response.tool_calls) != 1:
                rejection = _json_at.dumps(
                    {
                        "error": "tool_activation_requires_exclusive_turn",
                        "retriable": True,
                    },
                    ensure_ascii=False,
                )
                for tc in response.tool_calls:
                    yield _loop_api.ToolResultEvent(
                        type="tool_result",
                        task_id=tid,
                        iteration=iteration,
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                        result=rejection,
                    )
                    _loop_api._append_loop_transcript(
                        working_messages,
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tc.name,
                            "content": rejection,
                        },
                        metadata_enabled=context_metadata_enabled,
                        source="agent_loop.tool_result",
                        role="tool",
                        fragment_id=f"{_tool_group_id}:tool:{tc.id}",
                        causal_group_id=_tool_group_id,
                    )
                return
            if loop.external_tool_dispatch:
                yield _loop_api.ToolBatchEvent(
                    task_id=tid,
                    iteration=iteration,
                    tool_calls=tuple(response.tool_calls),
                    canonical_messages=tuple(
                        copy.deepcopy(dict(message)) for message in working_messages
                    ),
                )
                state.terminal = True
                return
            _run_tool_calls += len(response.tool_calls)
            tool_coros = []
            call_order: list[ToolCall] = []
            for tc in response.tool_calls:
                _loop_api._inject_loop_user_request(
                    tc,
                    tool_schemas,
                    loop_user_request=loop_user_request,
                )
                _tok, _treason = await traced_call(
                    name="termination_gate.allows_tool",
                    kind=SpanKind.GATE,
                    lifecycle_stage="gate",
                    attributes={
                        "iteration": iteration,
                        "gate": "termination",
                        "tool": tc.name,
                    },
                    invoke=lambda tc=tc: loop._gate.allows_tool(tc.name),
                )
                if not _tok:
                    if tool_coros:
                        _flushed = await asyncio.gather(
                            *tool_coros, return_exceptions=True
                        )
                        for _ftc, _fres in zip(call_order, _flushed):
                            if isinstance(_fres, BaseException):
                                import json as _json_flush
                                _fres_str = _json_flush.dumps(
                                    {
                                        "error": f"{type(_fres).__name__}: {_fres}",
                                        "retriable": False,
                                    },
                                    ensure_ascii=False,
                                )
                            else:
                                _fres_str = _fres
                            yield _loop_api.ToolResultEvent(
                                type="tool_result",
                                task_id=tid,
                                iteration=iteration,
                                tool_call_id=_ftc.id,
                                tool_name=_ftc.name,
                                result=_fres_str,
                            )
                    if loop._convergence_controller is not None:
                        _cap_summary = dict(loop._gate.summary())
                        if _treason is not None:
                            _cap_summary["reason"] = _treason.value
                        _verdict = loop._convergence_controller.evaluate(
                            principal_resolved=False,
                            unverified_claims=0,
                            gate_summary=_cap_summary,
                        )
                        if loop._pipeline_observability:
                            yield loop._pipeline_event("chat_v2_convergence", iteration, {
                                "converged": _verdict.converged,
                                "principal_resolved": _verdict.principal_resolved,
                                "stop_reason": _verdict.stop_reason,
                                "report": _verdict.report,
                            })
                        if _verdict.should_stop_loss and _verdict.report:
                            loop._gate.record_final_answer()
                            yield _loop_api.FinalEvent(
                                type="final",
                                task_id=tid,
                                iteration=iteration,
                                content=_verdict.report,
                                stop_reason="stop_loss",
                            )
                            state.terminal = True
                            return
                    yield _loop_api.ErrorEvent(
                        type="error",
                        task_id=tid,
                        iteration=iteration,
                        reason=(
                            _treason.value
                            if _treason is not None else "unknown"
                        ),
                        detail=f"Termination gate blocked tool {tc.name}",
                    )
                    state.terminal = True
                    return
                loop._gate.record_tool_call(tc.name, args=tc.arguments)
                if (
                    loop._evidence_gate is not None
                    and not loop._evidence_gathered
                    and loop._evidence_gate.is_investigative(tc.name)
                    and tc.name not in loop._history_tool_names
                ):
                    loop._evidence_gathered = True
                    logger.info("evidence_gathered_set sid=%s tool=%s", session_id, tc.name)
                if tc.name == "skill_invoke" and loop.skill_loader is not None:
                    _sname = None
                    try:
                        _sargs = tc.arguments
                        if isinstance(_sargs, dict):
                            _sname = _sargs.get("skill_name")
                        elif isinstance(_sargs, str):
                            import json as _json_sk
                            _sargs_d = _json_sk.loads(_sargs)
                            _sname = _sargs_d.get("skill_name")
                    except Exception:  # noqa: BLE001 — never block dispatch
                        pass
                    if _sname and _sname not in loop._skills_used_this_run:
                        loop._skills_used_this_run.add(_sname)
                        loop._skills_used_order.append(_sname)
                        if _skill_compaction_happened:
                            _remounted = loop._remount_skills(
                                working_messages,
                                session_id,
                                prepared_context=prepared_context,
                            )
                            working_messages[:] = _remounted
                tools_used_count += 1
                yield _loop_api.ToolCallEvent(
                    type="tool_call",
                    task_id=tid,
                    iteration=iteration,
                    tool_call=tc,
                )
                tool_coros.append(
                    self._dispatch_tool(
                        loop,
                        _loop_api,
                        tc,
                        tid,
                        session_id,
                        execution_context=tool_execution_context,
                    )
                )
                call_order.append(tc)
            results = await asyncio.gather(*tool_coros, return_exceptions=True)
            permanent_break: tuple[str, str] | None = None  # (reason, detail)
            for tc, result in zip(call_order, results):
                if isinstance(result, BaseException):
                    import json as _json  # local import: rarely used path
                    result_str = _json.dumps(
                        {"error": f"{type(result).__name__}: {result}", "retriable": False},
                        ensure_ascii=False,
                    )
                else:
                    result_str = result
                if tc.name == "tool_activate" and current_tool_set is not None:
                    try:
                        _outer = _json_at.loads(result_str)
                        _inner_raw = _outer.get("result") if isinstance(_outer, dict) else None
                        _inner = (
                            _json_at.loads(_inner_raw)
                            if isinstance(_inner_raw, str)
                            else _inner_raw
                        )
                        _control = (
                            _inner.get("__deskpet_control")
                            if isinstance(_inner, dict)
                            else None
                        )
                        if isinstance(_control, dict) and _control.get("kind") == "tool_activation":
                            from deskpet.agent.context_budget import (
                                estimate_request_budget,
                                prepare_openai_tool_payload,
                            )
                            from deskpet.tools.capabilities import PreparedToolCapability
                            _ref = next(
                                (
                                    ref
                                    for ref in current_tool_set.deferred
                                    if ref.capability_id == _control.get("capability_id")
                                ),
                                None,
                            )
                            if (
                                _ref is None
                                or _ref.schema_hash != _control.get("schema_hash")
                                or current_tool_set.revision
                                != int(_control.get("base_scope_revision", -1))
                            ):
                                raise RuntimeError("capability_stale")
                            _capability = PreparedToolCapability(_ref, _control["schema"])
                            _candidate = current_tool_set.activate(_capability)
                            _payload = prepare_openai_tool_payload(_candidate)
                            _model_info = loop._ctx.config._resolved_model_info()
                            _activation_budget = estimate_request_budget(
                                working_messages,
                                _payload,
                                context_window=_model_info.context_window,
                                effective_pct=_model_info.effective_pct,
                                generation_reserve=_loop_api._planned_generation_reserve(
                                    prepared_context,
                                    llm_kwargs,
                                ),
                            )
                            if not _activation_budget.fits:
                                raise RuntimeError("tool_activation_budget_exceeded")
                            _scope_store = getattr(
                                loop.tools, "capability_scope_store", None
                            )
                            if _scope_store is None:
                                raise RuntimeError("tool_capability_runtime_unavailable")
                            async with _scope_store.lock_for(current_tool_set.scope_id):
                                _record = _scope_store.get(
                                    current_tool_set.scope_id,
                                    session_id=session_id,
                                    request_id=context_request_id or tid,
                                )
                                if (
                                    _record is None
                                    or _record.prepared.revision != current_tool_set.revision
                                ):
                                    raise RuntimeError("capability_stale")
                                loop.tools.validate_prepared_tool_set(
                                    _candidate,
                                    eligibility=_record.eligibility,
                                )
                                _activation_receipt = (
                                    await loop._persist_activation_tool_context_locked(
                                        session_id=session_id,
                                        scope_store=_scope_store,
                                        scope_record=_record,
                                        candidate=_candidate,
                                        prepared_context=prepared_context,
                                        tool_payload=_payload,
                                    )
                                )
                                _activation_handle = (
                                    _activation_receipt.new_handle
                                    if _activation_receipt is not None
                                    else _record.snapshot_handle
                                )
                                _scope_store.commit_prevalidated(
                                    _candidate,
                                    snapshot_handle=_activation_handle,
                                )
                                if (
                                    _activation_receipt is not None
                                    and prepared_context is not None
                                ):
                                    prepared_context.active_snapshot_handle = (
                                        _activation_receipt.new_handle
                                    )
                                current_tool_set = _candidate
                                tool_schemas = list(_candidate.logical_schemas())
                            _outer["result"] = _json_at.dumps(
                                {
                                    "status": "activated",
                                    "capability_id": _ref.capability_id,
                                    "scope_revision": current_tool_set.revision,
                                },
                                ensure_ascii=False,
                            )
                            result_str = _json_at.dumps(_outer, ensure_ascii=False)
                    except Exception as _activation_exc:  # noqa: BLE001
                        result_str = _json_at.dumps(
                            {
                                "ok": False,
                                "result": None,
                                "error": str(_activation_exc),
                            },
                            ensure_ascii=False,
                        )
                if loop._tracer:
                    loop._tracer.record({
                        "kind": "tool_result",
                        "iter": iteration,
                        "name": tc.name,
                        "args": tc.arguments,
                        "ok": not isinstance(result, BaseException),
                        "result_preview": str(result_str)[:200],
                    })
                yield _loop_api.ToolResultEvent(
                    type="tool_result",
                    task_id=tid,
                    iteration=iteration,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    result=result_str,
                    pipeline_label=(
                        {"step": 5, "observation_summary": str(result_str)[:120]}
                        if loop._pipeline_observability else None
                    ),
                )
                _content_for_history, _trunc_ref = loop._ctx.record_tool_result(
                    tool_name=tc.name, result=result_str,
                )
                if _trunc_ref is not None:
                    logger.info(
                        "p5s2_tool_result_truncated sid=%s tool=%s "
                        "orig_len=%d kept_len=%d ref_id=%s",
                        session_id, tc.name, len(result_str),
                        len(_content_for_history), _trunc_ref,
                    )
                _tool_result_message = _loop_api._append_loop_transcript(
                    working_messages,
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.name,
                        "content": _content_for_history,
                    },
                    metadata_enabled=context_metadata_enabled,
                    source="agent_loop.tool_result",
                    role="tool",
                    fragment_id=f"{_tool_group_id}:tool:{tc.id}",
                    causal_group_id=_tool_group_id,
                )
                if tc.name == "deepresearch" and _loop_api._deepresearch_result_is_complete(result_str):
                    _loop_api._append_loop_control(
                        working_messages,
                        {
                            "role": "system",
                            "content": _loop_api._DEEPRESEARCH_FINALIZE_MSG,
                        },
                        metadata_enabled=context_metadata_enabled,
                        source="agent_loop.deepresearch_completion",
                        anchor_after=(
                            _loop_api._context_fragment_id(_tool_result_message)
                            or f"{_tool_group_id}:tool:{tc.id}"
                        ),
                        fragment_id=(
                            f"agent-loop:{tid}:iteration:{iteration}:"
                            "deepresearch-finalize"
                        ),
                        protected=True,
                    )
                    _force_finish_queued = True
                    logger.info(
                        "deepresearch_finalize_queued sid=%s tid=%s iter=%d",
                        session_id, tid, iteration,
                    )
                _tp_err_class = None
                if permanent_break is None:
                    err_class = _loop_api._classify_tool_result(result_str)
                    _tp_err_class = err_class
                    if err_class is agent_errors.PermanentToolError:
                        permanent_break = (
                            "permanent_tool_error",
                            _loop_api._extract_break_detail(result_str, tc.name),
                        )
                    elif err_class is agent_errors.HallucinationError:
                        permanent_break = (
                            "hallucination",
                            _loop_api._extract_break_detail(result_str, tc.name),
                        )
                if loop.tool_path_recorder is not None:
                    try:
                        _tp_ok = not isinstance(result, BaseException) and _tp_err_class not in (
                            agent_errors.PermanentToolError,
                            agent_errors.HallucinationError,
                        )
                        loop.tool_path_recorder.record_tool(
                            session_id, name=tc.name, ok=_tp_ok,
                        )
                    except Exception:  # noqa: BLE001 — never block dispatch
                        pass
                if (
                    len(accepted_async_calls) == 1
                    and tc.id == accepted_async_calls[0].id
                ):
                    handoff = _loop_api._async_handoff_details(result_str)
                    if handoff is not None:
                        event_id = handoff["event_id"]
                        if event_id and loop._workflow_service is not None:
                            try:
                                await traced_call(
                                    name="workflow.deliver_handoff",
                                    kind=SpanKind.DELIVERY,
                                    lifecycle_stage="delivery",
                                    attributes={"iteration": iteration},
                                    invoke=lambda: loop._workflow_service.deliver_event_once(
                                        event_id
                                    ),
                                )
                            except Exception as exc:  # noqa: BLE001 - outbox remains retryable
                                logger.warning(
                                    "async_handoff_delivery_failed sid=%s event_id=%s error=%s",
                                    session_id,
                                    event_id,
                                    str(exc)[:200],
                                )
                        if loop._tracer:
                            loop._tracer.record(
                                {
                                    "kind": "async_handoff",
                                    "iter": iteration,
                                    "name": tc.name,
                                    "event_id": event_id,
                                    "run_id": handoff["run_id"],
                                }
                            )
                        yield _loop_api.AsyncHandoffEvent(
                            type="async_handoff",
                            task_id=tid,
                            iteration=iteration,
                            tool_call_id=tc.id,
                            tool_name=tc.name,
                            result=result_str,
                            event_id=event_id,
                            run_id=handoff["run_id"],
                            request_id=handoff["request_id"],
                            turn_id=handoff["turn_id"],
                        )
                        state.terminal = True
                        return
            if permanent_break is not None:
                reason, detail = permanent_break
                logger.info(
                    "p5s2_tool_error_classified sid=%s tid=%s iter=%d "
                    "reason=%s detail=%s",
                    session_id, tid, iteration, reason, detail[:200],
                )
                yield _loop_api.ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=iteration,
                    reason=reason,
                    detail=detail,
                )
                state.terminal = True
                return
        finally:
            state.tool_schemas, state.current_tool_set = tool_schemas, current_tool_set
            state.force_finish_queued, state.tools_used_count, state.run_tool_calls = _force_finish_queued, tools_used_count, _run_tool_calls
    def _tool_completion_semantics(self, loop: Any, name: str) -> str:
        get_spec = getattr(loop.tools, "get", None)
        if callable(get_spec):
            spec = get_spec(name)
            semantics = getattr(spec, "completion_semantics", None)
            if semantics in {"sync", "accepted_async"}:
                return semantics
        all_specs = getattr(loop.tools, "all_specs", None)
        if callable(all_specs):
            for spec in all_specs():
                if getattr(spec, "name", None) == name:
                    semantics = getattr(spec, "completion_semantics", None)
                    if semantics in {"sync", "accepted_async"}:
                        return semantics
        return "sync"
    async def _dispatch_tool(
        self,
        loop: Any,
        _loop_api: Any,
        tc: ToolCall,
        task_id: str,
        session_id: str = "default",
        *,
        execution_context: Optional[Any] = None,
    ) -> str:
        return await traced_call(
            name="tool.execute",
            kind=SpanKind.TOOL,
            lifecycle_stage="tool",
            attributes={
                "tool": tc.name,
                "completion_semantics": self._tool_completion_semantics(loop, tc.name),
                "argument_keys": sorted(tc.arguments) if isinstance(tc.arguments, dict) else [],
            },
            invoke=lambda: self._dispatch_tool_untraced(
                loop,
                _loop_api,
                tc,
                task_id,
                session_id,
                execution_context=execution_context,
            ),
            result_is_error=_loop_api._tool_dispatch_failed,
        )
    async def _dispatch_tool_untraced(
        self,
        loop: Any,
        _loop_api: Any,
        tc: ToolCall,
        task_id: str,
        session_id: str = "default",
        *,
        execution_context: Optional[Any] = None,
    ) -> str:
        import json as _json
        if tc.args_parse_error:
            args_raw = tc.args_raw or ""
            preview = args_raw[:300]
            if len(args_raw) > 300:
                preview = preview + "…"
            logger.warning(
                "p5s2_dispatch_short_circuit_malformed_args "
                "tool=%s args_len=%d parse_error=%s",
                tc.name, len(args_raw), tc.args_parse_error[:200],
            )
            is_truncation = (
                len(args_raw) > 3000
                and "Unterminated string" in (tc.args_parse_error or "")
            )
            if is_truncation:
                hint_text = (
                    f"你刚发的 tool_call.arguments 太长 ({len(args_raw)} 字符) 被 LLM "
                    "输出 token 上限截断了，JSON 不完整无法解析。**不要重试同样的 "
                    "tool_call** —— 同样会再被截断。请改用以下任一策略：\n"
                    "1) write_file 一次只写不超过 3000 字符（约 80 行代码），"
                    "如果文件大就分多次：先 write_file 写主结构+ TODO 注释，再 "
                    "用 edit_file/write_file 多次追加补完。\n"
                    "2) 把大文件拆成多个小文件（按职责分组件 / hook / util），"
                    "每个文件 < 80 行。\n"
                    "3) 如果只是修改局部，用 edit_file 而非 write_file 整覆盖。"
                )
            else:
                hint_text = (
                    f"你刚发的 tool_call.arguments 不是合法 JSON: "
                    f"{tc.args_parse_error}. 你写了 {len(args_raw)} 字符的 args, "
                    "但解析失败。最常见原因：长字符串里 \\n / \\\" / \\\\ "
                    "没正确转义。请重新生成同一个 tool_call，"
                    "确保 JSON 严格合法（特别是 multi-line content 字段）。"
                )
            return _json.dumps(
                {
                    "ok": False,
                    "error": (
                        "tool_call_args_truncated_by_max_tokens"
                        if is_truncation
                        else "tool_call_args_malformed_json"
                    ),
                    "hint": hint_text,
                    "tool": tc.name,
                    "args_raw_preview": preview,
                    "parse_error": tc.args_parse_error,
                    "args_len": len(args_raw),
                    "likely_cause": (
                        "max_tokens_truncation"
                        if is_truncation
                        else "escape_error"
                    ),
                },
                ensure_ascii=False,
            )
        try:
            if loop._supports_execute_tool:
                if execution_context is None:
                    envelope = await loop.tools.execute_tool(  # type: ignore[attr-defined]
                        tc.name, tc.arguments, session_id, task_id
                    )
                else:
                    envelope = await loop.tools.execute_tool(  # type: ignore[attr-defined]
                        tc.name,
                        tc.arguments,
                        session_id,
                        task_id,
                        execution_context=execution_context,
                    )
                return _json.dumps(envelope, ensure_ascii=False)
            result = loop.tools.dispatch(tc.name, tc.arguments, task_id)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001
            return _json.dumps(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "retriable": False,
                },
                ensure_ascii=False,
            )
        if isinstance(result, (dict, list)):
            return _json.dumps(result, ensure_ascii=False)
        return str(result)
