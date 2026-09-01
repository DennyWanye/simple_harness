"""Checkpoint-safe handlers for the DeepResearch v5 adaptive graph.

The graph owns orchestration state only. Network, browser, model, artifact, clock,
and control behavior is supplied through ``WorkflowContext`` ports so this module
can be compiled and replayed without product bootstrap wiring.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Mapping, Protocol

from ..contracts import JsonValue, StatePatch, WorkflowContext, WorkflowState
from .research_core import ResearchStageCancelled, ResearchStageOutputError
from .deep_research_v5_contracts import (
    ContractValidationError,
    DeliveryDecision,
    DimensionCoverage,
    GapWorkItem,
    ResearchBrief,
    ResearchControlCommand,
    validate_terminal_projection,
)
from .deep_research_v5_loop_policy import (
    LoopPolicyState,
    ProgressSnapshot,
    claim_query_strategy,
    evaluate_time_gate,
    initial_loop_state,
    progress_from_coverages,
    record_progress_round,
    request_cancel,
    request_generate_now,
)
from .deep_research_v5_policy import (
    build_research_brief,
    initial_dimension_coverages,
    validate_modeling_output,
)
from .deep_research_v5_progress import build_v5_stage_projection
from .deep_research_v5_queries import DimensionQuery, build_gap_query_plan, build_initial_query_plan
from .deep_research_v5_delivery import (
    ReportDraftError,
    apply_atomic_repair,
    build_extractively_grounded_draft,
    evaluate_structured_report,
    failed_report_evaluation,
    select_repair_dimension,
)


GAP_WORK_LIMIT = 64
REPAIR_LIMIT = 16
CONTROL_OBSERVE_INTERVAL_SECONDS = 0.5


class V5StagePort(Protocol):
    async def execute(
        self,
        *,
        stage: str,
        payload: Mapping[str, JsonValue],
        identity: object | None,
    ) -> Mapping[str, JsonValue]: ...


class V5ClockPort(Protocol):
    def active_seconds(
        self, *, operation_id: str, accumulated_active_seconds: float
    ) -> float | Awaitable[float]: ...

    def wall_time(self) -> str | Awaitable[str]: ...


class V5ControlPort(Protocol):
    def poll(
        self,
        *,
        run_id: str,
        checkpoint_id: str | None,
        checkpoint_ns: str | None = None,
    ) -> ResearchControlCommand | None | Awaitable[ResearchControlCommand | None]: ...

    async def settle(
        self,
        command_id: str,
        *,
        checkpoint_ns: str,
        checkpoint_id: str,
        result: Mapping[str, object],
    ) -> ResearchControlCommand: ...

    async def consume(
        self,
        command_id: str,
        *,
        checkpoint_ns: str,
        checkpoint_id: str,
        result: Mapping[str, object],
    ) -> ResearchControlCommand: ...


async def _await(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


def _values(state: WorkflowState) -> dict[str, JsonValue]:
    return copy.deepcopy(dict(state.get("values", {})))


def _mapping(value: object, name: str) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a JSON object")
    return copy.deepcopy(dict(value))  # StatePatch performs strict JSON validation.


def _identity_payload(context: WorkflowContext) -> dict[str, JsonValue]:
    identity = context.identity
    if identity is None:
        return {}
    return {
        "checkpoint_id": identity.checkpoint_id,
        "task_id": identity.task_id,
        "node_id": identity.node_id,
        "attempt": identity.attempt,
    }


async def _execute(
    context: WorkflowContext,
    port_name: str,
    stage: str,
    payload: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    port = context.ports.get(port_name)
    if port is None:
        return {}
    execute = getattr(port, "execute", None)
    if not callable(execute):
        raise TypeError(f"workflow {port_name} port must provide execute()")
    result = await _await(
        execute(stage=stage, payload=copy.deepcopy(dict(payload)), identity=context.identity)
    )
    return _mapping(result, f"{port_name}.{stage} result")


async def _clock_snapshot(
    context: WorkflowContext,
    loop: LoopPolicyState,
) -> tuple[float, str]:
    port = context.ports.get("clock")
    if port is None:
        return loop.accumulated_active_seconds, loop.wall_clock_anchor
    active_call = getattr(port, "active_seconds", None)
    wall_call = getattr(port, "wall_time", None)
    if not callable(active_call) or not callable(wall_call):
        raise TypeError("workflow clock port must provide active_seconds() and wall_time()")
    active = await _await(
        active_call(
            operation_id=loop.operation_id,
            accumulated_active_seconds=loop.accumulated_active_seconds,
        )
    )
    wall = await _await(wall_call())
    if isinstance(active, bool) or not isinstance(active, (int, float)):
        raise TypeError("clock active_seconds must be numeric")
    if float(active) < loop.accumulated_active_seconds:
        raise ValueError("clock active_seconds cannot roll back durable active time")
    if not isinstance(wall, str):
        raise TypeError("clock wall_time must be an ISO-8601 string")
    # LoopPolicyState validates the wall timestamp and numeric duration.
    checkpointed = replace(
        loop,
        accumulated_active_seconds=float(active),
        wall_clock_anchor=wall,
    )
    return checkpointed.accumulated_active_seconds, checkpointed.wall_clock_anchor


async def _poll_control(
    state: WorkflowState,
    context: WorkflowContext,
) -> ResearchControlCommand | None:
    port = context.ports.get("control")
    if port is None:
        return None
    poll = getattr(port, "poll", None)
    if not callable(poll):
        raise TypeError("workflow control port must provide poll()")
    kwargs: dict[str, object] = {
        "run_id": str(state["run_id"]),
        "checkpoint_id": (context.identity.checkpoint_id if context.identity else None),
    }
    try:
        parameters = inspect.signature(poll).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "checkpoint_ns" in parameters:
        kwargs["checkpoint_ns"] = (
            context.identity.checkpoint_ns if context.identity else None
        )
    value = await _await(poll(**kwargs))
    if value is not None and not isinstance(value, ResearchControlCommand):
        raise TypeError("control poll must return ResearchControlCommand or None")
    if value is not None and value.run_id != state["run_id"]:
        raise ValueError("control command belongs to a different run")
    return value


async def _settle_control(
    values: dict[str, JsonValue],
    context: WorkflowContext,
    *,
    route: str,
) -> None:
    raw = values.get("control_command")
    if not isinstance(raw, Mapping) or context.identity is None:
        return
    command = ResearchControlCommand.from_json(raw)
    if command.status == "settled":
        return
    port = context.ports.get("control")
    settle = getattr(port, "settle", None)
    if not callable(settle):
        return
    result = await _await(
        settle(
            command.command_id,
            checkpoint_ns=context.identity.checkpoint_ns,
            checkpoint_id=context.identity.checkpoint_id,
            result={
                "route": route,
                "last_committed_evidence": [
                    item.to_json() for item in _coverages(values)
                ],
            },
        )
    )
    if not isinstance(result, ResearchControlCommand):
        raise TypeError("control settle must return ResearchControlCommand")
    values["control_command"] = result.to_json()
    values["control_settle"] = {
        "status": result.status,
        "route": route,
        "checkpoint_id": context.identity.checkpoint_id,
    }


async def _consume_control(
    values: dict[str, JsonValue],
    context: WorkflowContext,
    *,
    delivery_status: str,
) -> None:
    raw = values.get("control_command")
    if not isinstance(raw, Mapping) or context.identity is None:
        return
    command = ResearchControlCommand.from_json(raw)
    if command.status != "settled":
        return
    port = context.ports.get("control")
    consume = getattr(port, "consume", None)
    if not callable(consume):
        return
    result = await _await(
        consume(
            command.command_id,
            checkpoint_ns=context.identity.checkpoint_ns,
            checkpoint_id=context.identity.checkpoint_id,
            result={"delivery_status": delivery_status},
        )
    )
    if not isinstance(result, ResearchControlCommand):
        raise TypeError("control consume must return ResearchControlCommand")
    values["control_command"] = result.to_json()


async def _materialize_snapshot(
    state: WorkflowState,
    context: WorkflowContext,
    values: dict[str, JsonValue],
) -> None:
    if context.identity is None:
        return
    port = context.ports.get("snapshot")
    persist = getattr(port, "persist_research_snapshot", None)
    if not callable(persist):
        return
    now = _now(values)
    continue_until = now + timedelta(days=30)
    result = await _await(
        persist(
            run_id=str(state["run_id"]),
            operation_id=str(values["operation_id"]),
            values=copy.deepcopy(values),
            execution_identity=context.identity,
            created_at=now.isoformat(),
            continue_until=continue_until.isoformat(),
        )
    )
    snapshot = _mapping(result, "snapshot.persist_research_snapshot result")
    snapshot_hash = snapshot.get("snapshot_hash")
    manifest_ref = snapshot.get("manifest_ref")
    if (
        not isinstance(snapshot_hash, str)
        or len(snapshot_hash) != 64
        or manifest_ref != snapshot_hash
    ):
        raise ValueError("persisted snapshot must expose its canonical blob hash")
    values["evidence_snapshot"] = snapshot
    values["evidence_snapshot_hash"] = snapshot_hash
    values["continue_until"] = continue_until.isoformat()


def _loop(values: Mapping[str, JsonValue]) -> LoopPolicyState:
    raw = values.get("loop_policy")
    if not isinstance(raw, Mapping):
        raise ValueError("v5 loop policy is not initialized")
    return LoopPolicyState.from_json(raw)


def _coverages(values: Mapping[str, JsonValue]) -> tuple[DimensionCoverage, ...]:
    raw = values.get("dimension_coverages", [])
    if not isinstance(raw, list):
        raise ValueError("dimension_coverages must be an array")
    return tuple(DimensionCoverage.from_json(item) for item in raw)


def _brief(values: Mapping[str, JsonValue]) -> ResearchBrief:
    raw = values.get("research_brief")
    if not isinstance(raw, Mapping):
        raise ValueError("research brief is not initialized")
    return ResearchBrief.from_json(raw)


def _progress(
    values: Mapping[str, JsonValue],
    coverages: tuple[DimensionCoverage, ...],
    *,
    quality_score: float | None = None,
) -> ProgressSnapshot:
    brief = _brief(values)
    core_ids = tuple(
        dimension.dimension_id
        for dimension in brief.dimensions
        if dimension.importance == "core"
    )
    return progress_from_coverages(
        coverages,
        core_dimension_ids=core_ids,
        quality_score=(
            quality_score
            if quality_score is not None
            else _loop(values).current_progress.quality_score
        ),
    )


async def _refresh_loop(
    state: WorkflowState,
    context: WorkflowContext,
    values: dict[str, JsonValue],
) -> tuple[LoopPolicyState, object]:
    loop = _loop(values)
    active, wall = await _clock_snapshot(context, loop)
    loop = replace(
        loop,
        accumulated_active_seconds=active,
        wall_clock_anchor=wall,
    )
    command = await _poll_control(state, context)
    if command is not None and command.status in {"accepted", "observed"}:
        if command.action == "generate_now" and loop.control_mode == "running":
            loop = request_generate_now(
                loop,
                action_id=command.command_id,
                idempotency_key=command.idempotency_key,
                active_seconds=active,
            )
        elif command.action == "generate_now" and loop.control_mode == "generate_now_settling":
            loop = request_generate_now(
                loop,
                action_id=command.command_id,
                idempotency_key=command.idempotency_key,
                active_seconds=active,
            )
        elif command.action == "cancel_settle":
            loop = request_cancel(
                loop,
                action_id=command.command_id,
                idempotency_key=command.idempotency_key,
                active_seconds=active,
            )
        values["control_command"] = command.to_json()
    loop, decision = evaluate_time_gate(loop, active_seconds=active)
    values["loop_policy"] = loop.to_json()
    values["loop_decision"] = {
        "reason": decision.reason,
        "start_new_upstream": decision.start_new_upstream,
        "allow_inflight_completion": decision.allow_inflight_completion,
        "checkpoint_required": decision.checkpoint_required,
        "renewed": decision.renewed,
        "next_review_active_seconds": decision.next_review_active_seconds,
        "settle_deadline_active_seconds": decision.settle_deadline_active_seconds,
    }
    return loop, decision


def _stage_patch(
    values: dict[str, JsonValue],
    stage: str,
    *,
    previous_state: WorkflowState | None = None,
) -> StatePatch:
    values["stage"] = stage
    public = values.get("public_progress")
    public_progress = copy.deepcopy(dict(public)) if isinstance(public, Mapping) else {}
    public_progress["stage_projection"] = build_v5_stage_projection(
        {"values": values},
        stage_id=stage,
        previous_state=previous_state,
    )
    values["public_progress"] = public_progress
    return StatePatch({"values": values})


async def normalize_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    if values.get("loop_policy") is None:
        wall = "1970-01-01T00:00:00+00:00"
        clock = context.ports.get("clock")
        if clock is not None:
            wall_call = getattr(clock, "wall_time", None)
            if not callable(wall_call):
                raise TypeError("workflow clock port must provide wall_time()")
            wall_value = await _await(wall_call())
            if not isinstance(wall_value, str):
                raise TypeError("clock wall_time must be an ISO-8601 string")
            wall = wall_value
        operation_id = str(values.get("operation_id") or state["run_id"])
        values["operation_id"] = operation_id
        runtime = values.get("research_config")
        runtime = runtime if isinstance(runtime, Mapping) else {}
        values["loop_policy"] = initial_loop_state(
            operation_id,
            wall_clock_anchor=wall,
            soft_checkpoint_seconds=float(runtime.get("soft_checkpoint_seconds", 300)),
            lease_seconds=float(runtime.get("lease_seconds", 120)),
            automatic_cap_seconds=float(runtime.get("auto_cap_seconds", 900)),
            plateau_round_limit=int(runtime.get("plateau_rounds", 2)),
        ).to_json()
    values["node_identity"] = _identity_payload(context)
    return _stage_patch(values, "normalize", previous_state=state)


async def model_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    wall_date = _loop(values).wall_clock_anchor[:10]
    attempts: list[Mapping[str, JsonValue]] = []
    validation_errors: list[str] = []
    rejected_dimensions: list[dict[str, JsonValue]] = []
    brief: ResearchBrief | None = None
    previous_output: Mapping[str, JsonValue] | None = None
    for attempt in range(2):
        payload: dict[str, JsonValue] = {
            "topic": str(values["topic"]),
            "operation_id": str(values["operation_id"]),
        }
        if attempt == 1:
            payload.update(
                {
                    "repair_attempt": 1,
                    "validation_error": validation_errors[-1],
                    "previous_modeling_output": copy.deepcopy(dict(previous_output or {})),
                }
            )
        try:
            result = await _execute(context, "llm", "modeling", payload)
        except ResearchStageOutputError as exc:
            validation_errors.append(str(exc))
            if attempt == 0:
                continue
            break
        attempts.append(result)
        modeling_output = result.get("modeling_output")
        if modeling_output is None:
            validation_errors.append("modeling_output_missing")
            if attempt == 0:
                continue
            break
        previous_output = (
            dict(modeling_output) if isinstance(modeling_output, Mapping) else None
        )
        try:
            if not isinstance(modeling_output, Mapping):
                raise ContractValidationError("modeling_output_not_object")
            validated = validate_modeling_output(
                modeling_output,
                require_complete=True,
            )
            if validated.dimensions is None:
                raise ContractValidationError("modeling_dimensions_missing")
            rejected_dimensions = [
                {"index": index, "reason": reason}
                for index, reason in validated.rejected_dimensions
            ]
            brief = build_research_brief(
                str(values["topic"]),
                as_of_date=date.fromisoformat(wall_date),
                modeling_output=dict(modeling_output),
            )
            break
        except ContractValidationError as exc:
            validation_errors.append(str(exc))

    modeling_fallback_reason: str | None = None
    if brief is None:
        # Modeling is advisory. After one separately journaled and budgeted
        # repair effect, deterministic policy remains the fail-safe.
        modeling_fallback_reason = "invalid_modeling_output_after_repair"
        brief = build_research_brief(
            str(values["topic"]),
            as_of_date=date.fromisoformat(wall_date),
        )
    values["model_result"] = dict(attempts[-1]) if attempts else {}
    values["modeling_attempts"] = [dict(item) for item in attempts]
    if rejected_dimensions:
        values["modeling_rejections"] = rejected_dimensions
    if modeling_fallback_reason is not None:
        values["modeling_fallback"] = {
            "reason": modeling_fallback_reason,
            "validation_errors": validation_errors,
        }
    values["research_brief"] = brief.to_json()
    if values.get("only_gaps") is not True:
        values["dimension_coverages"] = [
            item.to_json() for item in initial_dimension_coverages(brief)
        ]
    return _stage_patch(values, "model", previous_state=state)


async def plan_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    queries = build_initial_query_plan(_brief(values))
    values["initial_queries"] = [
        {**query.to_json(), "fingerprint": query.fingerprint} for query in queries
    ]
    values["executed_query_fingerprints"] = []
    values["executed_source_family_ids"] = []
    return _stage_patch(values, "plan", previous_state=state)


async def expand_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    # Deterministic templates always run first. The query-strategy LLM becomes
    # eligible only after an evidence rescue round records no measurable gain.
    values["query_strategy_result"] = None
    values["query_strategy_queries"] = []
    values["query_strategy_called"] = False
    return _stage_patch(values, "expand", previous_state=state)


async def _external_stage(
    state: WorkflowState,
    context: WorkflowContext,
    *,
    port_name: str,
    stage: str,
) -> StatePatch:
    values = _values(state)
    # Search/fetch effects may legitimately run longer than the durable
    # generate-now observation deadline.  Observe a command while the atomic
    # effect is in flight so the watchdog does not mistake a healthy long
    # fetch for an unresponsive worker.  The effect is still allowed to finish;
    # loop policy applies the fence at the next checkpoint-safe node boundary.
    effect = asyncio.create_task(_execute(context, port_name, stage, values))
    try:
        while not effect.done():
            done, _ = await asyncio.wait(
                (effect,), timeout=CONTROL_OBSERVE_INTERVAL_SECONDS
            )
            if done:
                break
            command = await _poll_control(state, context)
            if command is not None:
                values["control_command"] = command.to_json()
                break
        try:
            result = await effect
        except ResearchStageCancelled:
            # A generate-now settle deadline may cancel a long atomic read.
            # This is a control-plane fence, not a workflow failure. Preserve
            # the last committed evidence and let score/gap/rerank settle the
            # command into a deterministic partial/insufficient terminal.
            result = {
                "status": "cancelled",
                "cancel_reason": "control_settle_fence",
                "cancelled_stage": stage,
            }
    except BaseException:
        if not effect.done():
            effect.cancel()
        try:
            await effect
        except (asyncio.CancelledError, Exception):
            pass
        raise
    values[f"{stage}_result"] = result
    return _stage_patch(values, stage, previous_state=state)


async def search_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    return await _external_stage(state, context, port_name="search", stage="search")


async def direct_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    return await _external_stage(state, context, port_name="search", stage="direct")


async def fetch_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    # The injected fetch adapter owns static extraction and retrieval-owned
    # Playwright fallback; the graph never imports or starts a browser.
    return await _external_stage(state, context, port_name="fetch", stage="fetch")


async def score_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    fetch_result = values.get("fetch_result", {})
    if isinstance(fetch_result, Mapping) and isinstance(fetch_result.get("coverages"), list):
        coverages = tuple(
            DimensionCoverage.from_json(item) for item in fetch_result["coverages"]
        )
        values["dimension_coverages"] = [item.to_json() for item in coverages]
    else:
        coverages = _coverages(values)
    quality = (
        float(fetch_result.get("quality_score", 0.0))
        if isinstance(fetch_result, Mapping)
        else 0.0
    )
    loop = replace(_loop(values), current_progress=_progress(values, coverages, quality_score=quality))
    values["loop_policy"] = loop.to_json()
    if isinstance(fetch_result, Mapping) and isinstance(fetch_result.get("synthesis_route"), str):
        values["synthesis_route"] = str(fetch_result["synthesis_route"])
    if isinstance(fetch_result, Mapping):
        for key in (
            "passage_blob_refs",
            "source_families",
            "evidence_candidates",
            "executed_query_fingerprints",
            "budget_summary",
            "admission_decisions",
            "rejection_reason_counts",
            "rejection_summary_by_dimension",
        ):
            if key in fetch_result:
                values[key] = copy.deepcopy(fetch_result[key])
    return _stage_patch(values, "score", previous_state=state)


def _gap_item(
    operation_id: str,
    query: DimensionQuery,
    attempt: int,
    *,
    reason: str = "dimension_gap_rescue",
) -> GapWorkItem:
    digest = hashlib.sha256(
        f"{operation_id}\0{query.fingerprint}\0{attempt}\0{reason}".encode("utf-8")
    ).hexdigest()[:24]
    return GapWorkItem(
        item_id=f"gap-{digest}",
        operation_id=operation_id,
        dimension_id=query.dimension_id,
        work_kind="query",
        query=query.query,
        source_target=query.source_target,
        attempt=attempt,
        reason=reason,
    )


def _pending_strategy_queries(
    values: Mapping[str, JsonValue],
    *,
    executed_fingerprints: tuple[str, ...],
) -> tuple[DimensionQuery, ...]:
    pending: list[DimensionQuery] = []
    for raw_query in values.get("query_strategy_queries", []):
        if not isinstance(raw_query, Mapping):
            raise TypeError("query strategy query must be an object")
        query = DimensionQuery.from_json(
            {key: value for key, value in raw_query.items() if key != "fingerprint"}
        )
        if query.fingerprint not in executed_fingerprints:
            pending.append(query)
    return tuple(pending)


def _report_synthesis_payload(values: Mapping[str, Any]) -> dict[str, JsonValue]:
    """Project orchestration state into a bounded, evidence-only LLM input.

    Search/fetch results and rejected candidates can contain repeated full-page
    bodies. Passing the entire workflow values map makes the reserved prompt
    exceed the durable run budget before an effect can even be created. The
    synthesizer only needs the brief, coverage, prior analysis, and admitted
    evidence excerpts.
    """

    admitted: list[dict[str, JsonValue]] = []
    raw_candidates = values.get("evidence_candidates", [])
    if isinstance(raw_candidates, list):
        for raw in raw_candidates:
            if not isinstance(raw, Mapping) or raw.get("admitted") is not True:
                continue
            excerpt = raw.get("span_text") or raw.get("body_text") or ""
            admitted.append({
                "candidate_id": str(raw.get("candidate_id") or ""),
                "dimension_id": str(raw.get("dimension_id") or ""),
                "title": str(raw.get("title") or ""),
                "family_id": (
                    str(raw.get("family_id")) if raw.get("family_id") else None
                ),
                "source_tier": str(raw.get("source_tier") or ""),
                "source_type": str(raw.get("source_type") or ""),
                "published_date": str(raw.get("published_date") or ""),
                "relevance": float(raw.get("relevance") or 0.0),
                "evidence_text": str(excerpt)[:4_000],
            })
    return {
        "research_brief": copy.deepcopy(values["research_brief"]),
        "dimension_coverages": copy.deepcopy(values["dimension_coverages"]),
        "synthesis_route": str(values.get("synthesis_route") or ""),
        "dimension_analysis_result": copy.deepcopy(
            values.get("dimension_analysis_result")
        ),
        "evidence_candidates": admitted,
    }


async def gap_evaluate_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    loop, decision = await _refresh_loop(state, context, values)
    counters = state.get("loop_counters", {})
    iteration = int(counters.get("gap_work_iterations", 0))
    if not decision.start_new_upstream or iteration >= GAP_WORK_LIMIT:
        values["gap_evaluate_route"] = "synth"
        values["active_gap_work"] = None
        return _stage_patch(values, "gap_evaluate", previous_state=state)
    executed_fingerprints = tuple(
        str(item) for item in values.get("executed_query_fingerprints", [])
    )
    deterministic_queries = build_gap_query_plan(
        _brief(values),
        _coverages(values),
        executed_query_fingerprints=executed_fingerprints,
    )
    strategy_queries = _pending_strategy_queries(
        values, executed_fingerprints=executed_fingerprints
    )
    queries = tuple(strategy_queries) + tuple(
        query
        for query in deterministic_queries
        if query.fingerprint not in {item.fingerprint for item in strategy_queries}
    )
    if not queries:
        values["gap_evaluate_route"] = "synth"
        values["active_gap_work"] = None
        return _stage_patch(values, "gap_evaluate", previous_state=state)
    selected = queries[0]
    query_strategy_due = (
        loop.no_gain_rounds >= 1
        and not loop.query_strategy_operations
        and not bool(values.get("query_strategy_called"))
    )
    reason = (
        "query_strategy_after_deterministic_no_gain"
        if query_strategy_due
        else "dimension_gap_rescue"
    )
    item = _gap_item(loop.operation_id, selected, iteration + 1, reason=reason)
    values["active_gap_work"] = item.to_json()
    values["active_gap_fingerprint"] = selected.fingerprint
    values["active_gap_mode"] = "query_strategy" if query_strategy_due else "evidence"
    values["gap_evaluate_route"] = "gap_work"
    return _stage_patch(values, "gap_evaluate", previous_state=state)


async def gap_work_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    loop, decision = await _refresh_loop(state, context, values)
    raw_item = values.get("active_gap_work")
    if not isinstance(raw_item, Mapping):
        values["gap_work_result"] = {"status": "missing_work_item"}
        return _stage_patch(values, "gap_work", previous_state=state)
    item = GapWorkItem.from_json(raw_item)
    if not decision.start_new_upstream:
        values["gap_work_result"] = {
            "status": "cancelled" if loop.control_mode == "cancelled" else "skipped_fence",
            "work_item_id": item.item_id,
        }
        return _stage_patch(values, "gap_work", previous_state=state)
    if values.get("active_gap_mode") == "query_strategy":
        loop, accepted = claim_query_strategy(
            loop, operation_id=str(values["operation_id"])
        )
        values["loop_policy"] = loop.to_json()
        result = (
            await _execute(
                context,
                "llm",
                "query_strategy",
                {
                    "work_item": item.to_json(),
                    "loop_policy": loop.to_json(),
                    "research_brief": copy.deepcopy(values["research_brief"]),
                    "dimension_coverages": copy.deepcopy(values["dimension_coverages"]),
                    "executed_query_fingerprints": copy.deepcopy(
                        values.get("executed_query_fingerprints", [])
                    ),
                },
            )
            if accepted
            else {}
        )
        values["query_strategy_called"] = accepted or bool(
            values.get("query_strategy_called")
        )
    else:
        port_name = "fetch" if item.work_kind == "fetch" else "search"
        result = await _execute(
            context,
            port_name,
            "gap_work",
            {
                "work_item": item.to_json(),
                "query_fingerprint": copy.deepcopy(
                    values.get("active_gap_fingerprint")
                ),
                "loop_policy": loop.to_json(),
                "research_brief": copy.deepcopy(values["research_brief"]),
                "dimension_coverages": copy.deepcopy(values["dimension_coverages"]),
                "source_families": copy.deepcopy(values.get("source_families", [])),
                "passage_blob_refs": copy.deepcopy(values.get("passage_blob_refs", [])),
                "evidence_candidates": copy.deepcopy(values.get("evidence_candidates", [])),
                "executed_query_fingerprints": copy.deepcopy(
                    values.get("executed_query_fingerprints", [])
                ),
            },
        )
    result = {**result, "status": "completed", "work_item_id": item.item_id}
    values["gap_work_result"] = result
    # A cap/control fence that arrives during the atomic effect is observed only
    # after its result is available, so the result can be checkpointed but no
    # subsequent upstream work is started.
    await _refresh_loop(state, context, values)
    return _stage_patch(values, "gap_work", previous_state=state)


async def gap_join_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    loop = _loop(values)
    raw_item = values.get("active_gap_work")
    result = values.get("gap_work_result", {})
    committed = [str(item) for item in values.get("committed_gap_work_ids", [])]
    count = int(state.get("loop_counters", {}).get("gap_work_iterations", 0))
    completed_result = isinstance(result, Mapping) and result.get("status") == "completed"
    mode = str(values.get("active_gap_mode") or "evidence")
    if isinstance(raw_item, Mapping) and completed_result and mode == "query_strategy":
        item = GapWorkItem.from_json(raw_item)
        values["query_strategy_work_item_id"] = item.item_id
        values["query_strategy_result"] = copy.deepcopy(dict(result))
        raw_queries = result.get("queries") if isinstance(result, Mapping) else None
        if raw_queries is not None:
            invalid_query_count = 0
            queries: tuple[DimensionQuery, ...] = ()
            parsed_queries: list[DimensionQuery] = []
            if isinstance(raw_queries, list):
                for value in raw_queries:
                    if not isinstance(value, Mapping):
                        invalid_query_count += 1
                        continue
                    try:
                        parsed_queries.append(DimensionQuery.from_json(value))
                    except (ContractValidationError, TypeError, ValueError):
                        invalid_query_count += 1
                queries = tuple(parsed_queries)
            else:
                invalid_query_count = 1
            values["query_strategy_queries"] = [
                {**query.to_json(), "fingerprint": query.fingerprint}
                for query in queries
            ]
            if invalid_query_count:
                # Query strategy is advisory. A malformed model result must
                # exhaust this strategy lane and fall through to the current
                # evidence delivery, never fail an otherwise usable partial run.
                values["query_strategy_diagnostics"] = {
                    "invalid_query_count": invalid_query_count,
                    "valid_query_count": len(queries),
                }
    elif isinstance(raw_item, Mapping) and completed_result:
        item = GapWorkItem.from_json(raw_item)
        if item.item_id not in committed:
            committed.append(item.item_id)
            count += 1
            if isinstance(result, Mapping) and isinstance(result.get("coverages"), list):
                coverages = tuple(
                    DimensionCoverage.from_json(value) for value in result["coverages"]
                )
                values["dimension_coverages"] = [value.to_json() for value in coverages]
            else:
                coverages = _coverages(values)
            quality = (
                float(result.get("quality_score", loop.current_progress.quality_score))
                if isinstance(result, Mapping)
                else loop.current_progress.quality_score
            )
            progress = _progress(values, coverages, quality_score=quality)
            if loop.prohibit_new_upstream:
                loop = replace(loop, current_progress=progress)
            else:
                loop = record_progress_round(
                    loop,
                    progress,
                    active_seconds=loop.accumulated_active_seconds,
                )
            fingerprint = values.get("active_gap_fingerprint")
            executed = [
                str(value) for value in values.get("executed_query_fingerprints", [])
            ]
            if isinstance(fingerprint, str) and fingerprint not in executed:
                executed.append(fingerprint)
            values["executed_query_fingerprints"] = executed
            if isinstance(result, Mapping):
                for key in (
                    "passage_blob_refs",
                    "source_families",
                    "evidence_candidates",
                    "admission_decisions",
                    "rejection_reason_counts",
                    "rejection_summary_by_dimension",
                    "budget_summary",
                ):
                    if key in result:
                        values[key] = copy.deepcopy(result[key])
    values["committed_gap_work_ids"] = committed
    values["loop_policy"] = loop.to_json()
    values["active_gap_work"] = None
    values["active_gap_fingerprint"] = None
    values["active_gap_mode"] = None
    values["gap_work_result"] = None
    executed_fingerprints = tuple(
        str(item) for item in values.get("executed_query_fingerprints", [])
    )
    no_more_queries = not build_gap_query_plan(
        _brief(values),
        _coverages(values),
        executed_query_fingerprints=executed_fingerprints,
    ) and not _pending_strategy_queries(
        values, executed_fingerprints=executed_fingerprints
    )
    values["gap_join_route"] = (
        "synth"
        if loop.prohibit_new_upstream or count >= GAP_WORK_LIMIT or no_more_queries
        else "gap_evaluate"
    )
    values["stage"] = "gap_join"
    return StatePatch(
        {
            "values": values,
            "loop_counters": {
                **copy.deepcopy(dict(state.get("loop_counters", {}))),
                "gap_work_iterations": count,
            },
        }
    )


async def rerank_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    loop, decision = await _refresh_loop(state, context, values)
    brief = _brief(values)
    by_id = {item.dimension_id: item for item in _coverages(values)}
    core = tuple(item for item in brief.dimensions if item.importance == "core")
    supported = tuple(
        item
        for item in core
        if by_id.get(item.dimension_id) is not None
        and by_id[item.dimension_id].status in {"covered", "partially_covered"}
    )
    requested = values.get("synthesis_route")
    if not supported:
        route = "insufficient_summary"
    elif requested in {"full_synthesis", "partial_synthesis"}:
        route = str(requested)
    elif all(
        by_id.get(item.dimension_id) is not None
        and by_id[item.dimension_id].status == "covered"
        for item in core
    ):
        route = "full_synthesis"
    else:
        route = "partial_synthesis"
    values["synthesis_route"] = route
    if route == "insufficient_summary":
        values["rerank_route"] = "insufficient_finalize"
        values["dimension_analysis_result"] = None
    elif decision.start_new_upstream or loop.control_mode == "generate_now_settling":
        values["dimension_analysis_result"] = await _execute(
            context,
            "llm",
            "dimension_analysis",
            {
                "research_brief": copy.deepcopy(values["research_brief"]),
                "dimension_coverages": copy.deepcopy(values["dimension_coverages"]),
                "synthesis_route": route,
            },
        )
        values["rerank_route"] = "synth"
    else:
        # cancel-settle never starts a fresh analysis effect; committed
        # evidence still decides partial versus insufficient deterministically.
        values["dimension_analysis_result"] = None
        values["rerank_route"] = "synth"
    await _settle_control(values, context, route=route)
    return _stage_patch(values, "rerank", previous_state=state)


async def synth_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    if _loop(values).control_mode == "cancelled":
        # cancel-settle is a hard fence for every new LLM call.  Preserve a
        # deterministic safe report projection from already committed evidence.
        result: dict[str, JsonValue] = {
            "delivery_status": "partial",
            "safe_summary": True,
            "summary": "Research was settled using only evidence committed before cancellation.",
        }
    else:
        result = await _execute(
            context,
            "llm",
            "report_synthesis",
            _report_synthesis_payload(values),
        )
    values["synthesis_result"] = result
    if values.get("synthesis_route") == "partial_synthesis":
        values["delivery_status"] = "partial"
    # The model is not authoritative for the business terminal.  The locked
    # deterministic rubric in quality_audit_handler owns that decision.
    return _stage_patch(values, "synth", previous_state=state)


async def quality_audit_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    loop, decision = await _refresh_loop(state, context, values)
    draft = values.get("synthesis_result")
    raw_candidates = values.get("evidence_candidates", [])
    try:
        if not isinstance(draft, Mapping):
            raise ReportDraftError("report synthesis returned no structured draft")
        if not isinstance(raw_candidates, list) or any(
            not isinstance(item, Mapping) for item in raw_candidates
        ):
            raise ReportDraftError("evidence_candidates must be an array of objects")
        evaluated = evaluate_structured_report(
            brief=_brief(values),
            coverages=_coverages(values),
            draft=draft,
            evidence_candidates=raw_candidates,
        )
    except (ReportDraftError, TypeError, ValueError) as exc:
        try:
            if not isinstance(draft, Mapping):
                raise ReportDraftError("report synthesis returned no structured draft")
            if not isinstance(raw_candidates, list) or any(
                not isinstance(item, Mapping) for item in raw_candidates
            ):
                raise ReportDraftError("evidence_candidates must be an array of objects")
            extractive_draft = build_extractively_grounded_draft(
                brief=_brief(values),
                coverages=_coverages(values),
                draft=draft,
                evidence_candidates=raw_candidates,
            )
            evaluated = evaluate_structured_report(
                brief=_brief(values),
                coverages=_coverages(values),
                draft=extractive_draft,
                evidence_candidates=raw_candidates,
            )
            evaluated["reason_codes"] = [
                *evaluated.get("reason_codes", []),
                "extractive_support_recovery",
            ]
        except (ReportDraftError, TypeError, ValueError) as recovery_exc:
            evaluated = failed_report_evaluation(
                f"{exc}; extractive recovery failed: {recovery_exc}"
            )
    previous_audit = values.get("quality_audit_result")
    audit = evaluated.get("quality_audit")
    if not isinstance(audit, Mapping):
        raise ValueError("deterministic report quality audit is missing")
    no_gain = int(values.get("repair_no_gain_rounds", 0))
    if isinstance(previous_audit, Mapping) and values.get("committed_repair_ids"):
        previous_score = float(previous_audit.get("total_score", 0.0))
        current_score = float(audit.get("total_score", 0.0))
        previous_failures = {
            str(item) for item in previous_audit.get("hard_failures", [])
        }
        current_failures = {str(item) for item in audit.get("hard_failures", [])}
        no_gain = (
            0
            if current_score > previous_score or current_failures < previous_failures
            else no_gain + 1
        )
    values["repair_no_gain_rounds"] = no_gain
    values["synthesis_result"] = evaluated
    values["quality_audit_result"] = copy.deepcopy(dict(audit))
    delivery_status = str(
        evaluated.get("delivery_status") or "insufficient_evidence"
    )
    values["delivery_status"] = delivery_status
    allowed_repairs = [str(item) for item in audit.get("allowed_repairs", [])]
    count = int(state.get("loop_counters", {}).get("repair_iterations", 0))
    can_repair = (
        decision.start_new_upstream
        and not loop.prohibit_new_upstream
        and count < REPAIR_LIMIT
        and no_gain < 2
        and bool(allowed_repairs)
        and delivery_status == "insufficient_evidence"
        and values.get("synthesis_route") != "insufficient_summary"
    )
    repair_dimension = (
        select_repair_dimension(
            action=allowed_repairs[0],
            brief=_brief(values),
            coverages=_coverages(values),
            evaluated=evaluated,
            evidence_candidates=raw_candidates,
        )
        if can_repair
        else None
    )
    if can_repair and repair_dimension is not None:
        advisory = await _execute(
            context,
            "llm",
            "quality_audit",
            {
                "report_draft": copy.deepcopy(evaluated),
                "quality_audit": copy.deepcopy(dict(audit)),
                "allowed_repair": allowed_repairs[0],
                "target_dimension_id": repair_dimension,
            },
        )
        values["quality_audit_llm_result"] = advisory
        values["active_repair_action"] = allowed_repairs[0]
        values["active_repair_dimension"] = repair_dimension
        values["active_repair_id"] = f"repair-{count + 1}"
        values["quality_route"] = "repair_work"
    elif delivery_status == "insufficient_evidence":
        values["quality_route"] = "insufficient_finalize"
    else:
        values["quality_route"] = "persist"
    return _stage_patch(values, "quality_audit", previous_state=state)


async def repair_work_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    loop, decision = await _refresh_loop(state, context, values)
    repair_id = str(values.get("active_repair_id") or "")
    if not decision.start_new_upstream:
        values["repair_result"] = {
            "status": "cancelled" if loop.control_mode == "cancelled" else "skipped_fence",
            "repair_id": repair_id,
        }
        return _stage_patch(values, "repair_work", previous_state=state)
    try:
        result = await _execute(
            context,
            "llm",
            "targeted_repair",
            {
                "repair_id": repair_id,
                "repair_action": str(values.get("active_repair_action") or ""),
                "target_dimension_id": str(
                    values.get("active_repair_dimension") or ""
                ),
                "audit": copy.deepcopy(values.get("quality_audit_result", {})),
                "report_draft": copy.deepcopy(values.get("synthesis_result", {})),
                "evidence_candidates": copy.deepcopy(values.get("evidence_candidates", [])),
            },
        )
        repaired = apply_atomic_repair(
            values.get("synthesis_result", {}),
            result,
            allowed_action=str(values.get("active_repair_action") or ""),
            expected_dimension_id=str(
                values.get("active_repair_dimension") or ""
            ),
        )
    except Exception as exc:
        values["repair_result"] = {
            "status": "completed",
            "repair_id": repair_id,
            "applied": False,
            "error": f"{type(exc).__name__}: {exc}"[:240],
        }
    else:
        values["synthesis_result"] = repaired
        values["repair_result"] = {
            "status": "completed",
            "repair_id": repair_id,
            "applied": True,
        }
    await _refresh_loop(state, context, values)
    return _stage_patch(values, "repair_work", previous_state=state)


async def repair_join_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    repair_id = str(values.get("active_repair_id") or "")
    committed = [str(item) for item in values.get("committed_repair_ids", [])]
    count = int(state.get("loop_counters", {}).get("repair_iterations", 0))
    result = values.get("repair_result", {})
    completed_result = isinstance(result, Mapping) and result.get("status") == "completed"
    if repair_id and repair_id not in committed and completed_result:
        committed.append(repair_id)
        count += 1
    values["committed_repair_ids"] = committed
    values["active_repair_id"] = None
    values["active_repair_action"] = None
    values["active_repair_dimension"] = None
    requested = "quality_audit"
    if requested not in {"quality_audit", "persist", "insufficient_finalize"}:
        requested = "quality_audit"
    values["repair_route"] = requested
    values["stage"] = "repair_join"
    return StatePatch(
        {
            "values": values,
            "loop_counters": {
                **copy.deepcopy(dict(state.get("loop_counters", {}))),
                "repair_iterations": count,
            },
        }
    )


async def persist_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    result = await _execute(context, "artifact", "persist", values)
    values["artifact_result"] = result
    if not isinstance(result.get("report_ref"), str) or not str(result["report_ref"]):
        raise ValueError("completed/partial delivery requires a durably persisted report_ref")
    values["report_ref"] = str(result["report_ref"])
    return _stage_patch(values, "persist", previous_state=state)


def _now(values: Mapping[str, JsonValue]) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            _loop(values).wall_clock_anchor.replace("Z", "+00:00")
        )
    except ValueError:
        parsed = datetime.now(timezone.utc)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _delivery(
    values: Mapping[str, JsonValue],
    *,
    status: str,
) -> DeliveryDecision:
    now = _now(values)
    report_ref = str(values.get("report_ref") or "") or None
    if status in {"completed", "partial"} and report_ref is None:
        raise ValueError("completed/partial delivery requires a real persisted report_ref")
    continuable = status in {"partial", "insufficient_evidence"}
    snapshot_hash = str(values.get("evidence_snapshot_hash") or "")
    continue_until = str(values.get("continue_until") or "")
    if continuable and (
        len(snapshot_hash) != 64
        or any(ch not in "0123456789abcdef" for ch in snapshot_hash)
        or not continue_until
    ):
        raise ValueError("continuable delivery requires a persisted canonical snapshot pin")
    return DeliveryDecision(
        status=status,  # type: ignore[arg-type]
        reason_codes=(str(values.get("terminal_reason") or status),),
        report_ref=(None if status == "insufficient_evidence" else report_ref),
        summary_ref=f"summary:{values['operation_id']}",
        evidence_snapshot_hash=(snapshot_hash if continuable else None),
        quality_audit_ref=(
            f"quality:{values['operation_id']}"
            if values.get("quality_audit_result") is not None
            else None
        ),
        continue_until=(
            continue_until if continuable else None
        ),
        decided_at=now.isoformat(),
    )


async def finalize_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    status = str(values.get("delivery_status") or "completed")
    if status not in {"completed", "partial"}:
        status = "partial"
    if status == "partial":
        await _materialize_snapshot(state, context, values)
    decision = _delivery(values, status=status)
    public: dict[str, JsonValue] = {"delivery_status": decision.status}
    if status == "partial":
        public["action_matrix"] = [
            {"action_id": "continue_research", "enabled": True}
        ]
    validate_terminal_projection(
        engine_status="completed",
        delivery_decision=decision,
        terminal_public=public,
    )
    values["delivery_decision"] = decision.to_json()
    values["terminal_public"] = public
    values["terminal_status"] = "completed"
    await _consume_control(values, context, delivery_status=decision.status)
    return _stage_patch(values, "finalize", previous_state=state)


async def insufficient_finalize_handler(
    state: WorkflowState,
    context: WorkflowContext,
) -> StatePatch:
    values = _values(state)
    # Insufficient is a safe summary plus resumable evidence state only.  It
    # intentionally bypasses artifact persistence in the v5 graph.
    await _materialize_snapshot(state, context, values)
    decision = _delivery(values, status="insufficient_evidence")
    public: dict[str, JsonValue] = {
        "delivery_status": decision.status,
        "action_matrix": [
            {"action_id": "continue_research", "enabled": True}
        ],
    }
    validate_terminal_projection(
        engine_status="completed",
        delivery_decision=decision,
        terminal_public=public,
    )
    values["delivery_decision"] = decision.to_json()
    values["terminal_public"] = public
    values["terminal_status"] = "completed"
    await _consume_control(values, context, delivery_status=decision.status)
    return _stage_patch(values, "insufficient_finalize", previous_state=state)


def gap_evaluate_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    route = state.get("values", {}).get("gap_evaluate_route")
    return str(route) if route in {"gap_work", "synth"} else "synth"


def gap_join_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    route = state.get("values", {}).get("gap_join_route")
    return str(route) if route in {"gap_evaluate", "synth"} else "synth"


def rerank_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    route = state.get("values", {}).get("rerank_route")
    return str(route) if route in {"synth", "insufficient_finalize"} else "insufficient_finalize"


def quality_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    route = state.get("values", {}).get("quality_route")
    allowed = {"repair_work", "persist", "insufficient_finalize"}
    return str(route) if route in allowed else "persist"


def repair_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    route = state.get("values", {}).get("repair_route")
    allowed = {"quality_audit", "persist", "insufficient_finalize"}
    return str(route) if route in allowed else "persist"


__all__ = [
    "GAP_WORK_LIMIT",
    "REPAIR_LIMIT",
    "V5ClockPort",
    "V5ControlPort",
    "V5StagePort",
    "direct_handler",
    "expand_handler",
    "fetch_handler",
    "finalize_handler",
    "gap_evaluate_handler",
    "gap_evaluate_route",
    "gap_join_handler",
    "gap_join_route",
    "gap_work_handler",
    "insufficient_finalize_handler",
    "model_handler",
    "normalize_handler",
    "persist_handler",
    "plan_handler",
    "quality_audit_handler",
    "quality_route",
    "repair_join_handler",
    "repair_route",
    "repair_work_handler",
    "rerank_handler",
    "rerank_route",
    "score_handler",
    "search_handler",
    "synth_handler",
]
