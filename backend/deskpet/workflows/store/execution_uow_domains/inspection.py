"""The harness run inspection read."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from .core import ExecutionUowCore


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed


def _prepared_call_id(value: Any) -> str:
    prepared = _json(value, {})
    if not isinstance(prepared, Mapping):
        return ""
    return str(prepared.get("stable_call_id") or "")


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _iteration(group_id: str) -> int | None:
    marker = ":iter:"
    if marker not in group_id:
        return None
    suffix = group_id.split(marker, 1)[1].split(":", 1)[0]
    try:
        return int(suffix)
    except ValueError:
        return None


def _checkpoint_payload(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    try:
        raw = bytes(value) if not isinstance(value, bytes) else value
        parsed = json.loads(raw.decode("utf-8"))
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _public_message(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(
        r"<think>[\s\S]*?(?:</think>|$)",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()


class RunInspectionMixin(ExecutionUowCore):
    """The harness run inspection read."""

    __slots__ = ()

    async def inspect_harness_run(
        self,
        root_run_id: str,
        *,
        expected_session_id: str,
    ) -> Mapping[str, Any] | None:
        """Return a payload-safe, durable projection of one Harness Run.

        This is an observability read model only. It intentionally projects
        the existing execution ledger instead of maintaining a second state
        machine. Detailed payloads are consumed by the local inspector UI and
        shown verbatim when the user expands a record.
        """

        run_key = str(root_run_id or "").strip()
        session_key = str(expected_session_id or "").strip()
        if not run_key or not session_key:
            raise ValueError("run_id and expected_session_id are required")

        async with self._read_connection() as db:
            (
                _returned,
                _result,
                continuation,
                projection,
                run,
                snapshot,
            ) = await self._inspect_run_rows(db, run_key, session_key)
            if _returned:
                return _result
            goal, latest_plan, lineage_rows = await self._inspect_goal_rows(db, run_key)
            batch_rows, call_rows, provider_rows = await self._inspect_provider_rows(db, run_key)
            (
                effect_rows,
                workflow_checkpoint_rows,
                workflow_effect_rows,
            ) = await self._inspect_effect_rows(db, run_key)
            attempt_rows, event_rows, failure_rows = await self._inspect_attempt_rows(db, run_key)

            pending = (
                _json(continuation["pending_prepared_call_json"], {})
                if continuation is not None
                else {}
            )
            pending_calls = pending.get("calls", []) if isinstance(pending, Mapping) else []
            (
                completion_state,
                pending_outcome_status_by_call,
                pending_tool_names,
                tool_observations_by_call,
            ) = self._inspect_pending_calls(pending, pending_calls)
            observed_batch_status, pending_current_attempt, providers = self._inspect_batch_status(
                call_rows=call_rows,
                completion_state=completion_state,
                pending=pending,
                pending_calls=pending_calls,
                pending_outcome_status_by_call=pending_outcome_status_by_call,
                provider_rows=provider_rows,
            )

            events, product_turn_steps, snapshot_request, workflow_plans, workflow_step_by_call = (
                self._inspect_workflow_steps(
                    event_rows=event_rows,
                    snapshot=snapshot,
                    workflow_checkpoint_rows=workflow_checkpoint_rows,
                )
            )

            now = float(self._clock())
            run_status = str(run["status"]).split(".")[-1].lower()
            latest_provider = providers[-1] if providers else None
            latest_effect = effect_rows[-1] if effect_rows else None
            active_children, last_progress_at = self._inspect_progress(
                attempt_rows,
                continuation=continuation,
                effect_rows=effect_rows,
                event_rows=event_rows,
                lineage_rows=lineage_rows,
                provider_rows=provider_rows,
                run=run,
                run_key=run_key,
            )
            stalled_for_seconds = max(0.0, now - last_progress_at)

            last_error, phase, summary, terminal_statuses, waiting_reason = (
                self._inspect_status_summary(
                    active_children,
                    continuation=continuation,
                    events=events,
                    failure_rows=failure_rows,
                    latest_effect=latest_effect,
                    latest_provider=latest_provider,
                    providers=providers,
                    run_status=run_status,
                    tool_observations_by_call=tool_observations_by_call,
                )
            )
            artifact_count = sum(
                len(refs)
                for refs in (_json(item["artifact_refs_json"], []) for item in effect_rows)
                if isinstance(refs, list)
            )

            return {
                "schema_version": 2,
                "refreshed_at": now,
                "session_id": session_key,
                "activity": _inspect_activity(
                    artifact_count,
                    last_error=last_error,
                    last_progress_at=last_progress_at,
                    phase=phase,
                    run_status=run_status,
                    stalled_for_seconds=stalled_for_seconds,
                    summary=summary,
                    terminal_statuses=terminal_statuses,
                    waiting_reason=waiting_reason,
                ),
                "run": _inspect_run(run),
                "projection": (_inspect_projection(projection)),
                "prepared_context": _inspect_prepared_context(
                    product_turn_steps, snapshot, snapshot_request
                ),
                "continuation": (
                    _inspect_continuation(continuation, pending_calls, pending_tool_names)
                ),
                "goal": (_inspect_goal(goal, latest_plan)),
                "lineage": _inspect_lineage(lineage_rows),
                "provider_invocations": providers,
                "action_batches": _inspect_action_batches(batch_rows, observed_batch_status),
                "tool_calls": _inspect_tool_calls(
                    call_rows, pending_outcome_status_by_call, tool_observations_by_call
                ),
                "effects": _inspect_effects(effect_rows),
                "workflow_effects": _inspect_workflow_effects(
                    workflow_effect_rows=workflow_effect_rows,
                    workflow_step_by_call=workflow_step_by_call,
                ),
                "workflow_plans": workflow_plans,
                "attempts": _inspect_attempts(
                    attempt_rows, observed_batch_status, pending_current_attempt
                ),
                "failures": _inspect_failures(failure_rows, tool_observations_by_call),
                "events": events,
            }

    async def _inspect_run_rows(self, db, run_key, session_key):
        run = await (
            await db.execute(
                """SELECT * FROM execution_runs
                    WHERE run_id=? AND root_run_id=run_id AND session_id=?""",
                (run_key, session_key),
            )
        ).fetchone()
        (
            _returned,
            _result,
            continuation,
            projection,
            snapshot,
        ) = await self._inspect_start_snapshot(db, run, run_key)
        if _returned:
            return (True, (_result), None, None, None, None)
        return False, None, continuation, projection, run, snapshot

    def _inspect_pending_calls(self, pending, pending_calls):
        pending_tool_names = [
            str(item.get("tool_name") or "")
            for item in pending_calls
            if isinstance(item, Mapping) and item.get("tool_name")
        ]
        tool_observations_by_call: dict[str, dict[str, str | None]] = {}
        completion_state = pending.get("completion_state") if isinstance(pending, Mapping) else None
        self._collect_tool_observations(completion_state, pending, tool_observations_by_call)

        pending_outcome_status_by_call: dict[str, str] = {
            call_id: str(observation["status"])
            for call_id, observation in tool_observations_by_call.items()
            if observation.get("status")
        }
        return (
            completion_state,
            pending_outcome_status_by_call,
            pending_tool_names,
            tool_observations_by_call,
        )

    def _inspect_progress(
        self,
        attempt_rows,
        *,
        continuation,
        effect_rows,
        event_rows,
        lineage_rows,
        provider_rows,
        run,
        run_key,
    ):
        active_children = [
            item
            for item in lineage_rows
            if str(item["run_id"]) != run_key
            and str(item["status"]).split(".")[-1].lower()
            not in {"completed", "failed", "cancelled"}
        ]
        progress_timestamps = [
            float(run["updated_at"]),
            *[float(item["updated_at"]) for item in provider_rows],
            *[float(item["updated_at"]) for item in effect_rows],
            *[float(item["updated_at"]) for item in attempt_rows],
            *[float(item["created_at"]) for item in event_rows],
        ]
        if continuation is not None:
            progress_timestamps.append(float(continuation["updated_at"]))
        last_progress_at = max(progress_timestamps)
        return active_children, last_progress_at

    def _collect_tool_observations(
        self,
        completion_state,
        pending,
        tool_observations_by_call: dict[str, dict[str, str | None]],
    ) -> None:
        canonical_messages = (
            pending.get("canonical_messages", []) if isinstance(pending, Mapping) else []
        )
        if isinstance(canonical_messages, list):
            for message in canonical_messages:
                if not isinstance(message, Mapping) or message.get("role") != "tool":
                    continue
                call_id = str(message.get("tool_call_id") or "").strip()
                content = message.get("content")
                payload = _json(content, {}) if isinstance(content, str) else content
                if not call_id or not isinstance(payload, Mapping):
                    continue
                outcome = payload.get("outcome")
                outcome = outcome if isinstance(outcome, Mapping) else {}
                raw_status = str(
                    payload.get("outcome_status")
                    or payload.get("status")
                    or outcome.get("state")
                    or ""
                ).strip()
                status = {
                    "success": "succeeded",
                    "failure": "failed",
                }.get(raw_status, raw_status)
                error = outcome.get("error")
                if isinstance(error, Mapping):
                    error_code = str(error.get("code") or "").strip()
                    error_message = str(error.get("message") or "").strip()
                else:
                    error_code = ""
                    error_message = str(error).strip() if error is not None else ""
                tool_observations_by_call[call_id] = {
                    "status": status or None,
                    "error_code": error_code or None,
                    "error_message": (error_message[:1000] or None),
                }
        feedback = (
            completion_state.get("agent_loop_feedback")
            if isinstance(completion_state, Mapping)
            else None
        )
        tool_history = feedback.get("tool_history") if isinstance(feedback, Mapping) else None
        if isinstance(tool_history, list):
            for item in tool_history:
                if not isinstance(item, Mapping):
                    continue
                call_id = str(item.get("call_id") or "").strip()
                ok = item.get("ok")
                if not call_id or not isinstance(ok, bool):
                    continue
                observation = tool_observations_by_call.setdefault(
                    call_id,
                    {
                        "status": None,
                        "error_code": None,
                        "error_message": None,
                    },
                )
                if not observation.get("status"):
                    observation["status"] = "succeeded" if ok else "failed"

    def _inspect_workflow_steps(
        self,
        *,
        event_rows,
        snapshot,
        workflow_checkpoint_rows,
    ):
        workflow_step_by_call: dict[tuple[str, str], str] = {}
        workflow_plans = []
        events = self._inspect_workflow_checkpoint_events(
            event_rows=event_rows,
            workflow_checkpoint_rows=workflow_checkpoint_rows,
            workflow_plans=workflow_plans,
            workflow_step_by_call=workflow_step_by_call,
        )

        snapshot_request = {} if snapshot is None else _json(snapshot["sanitized_request_json"], {})
        snapshot_payload = (
            snapshot_request.get("payload", {}) if isinstance(snapshot_request, Mapping) else {}
        )
        product_turn_steps = (
            snapshot_payload.get("product_turn_trace", [])
            if isinstance(snapshot_payload, Mapping)
            else []
        )
        if not isinstance(product_turn_steps, list):
            product_turn_steps = []
        return events, product_turn_steps, snapshot_request, workflow_plans, workflow_step_by_call

    def _inspect_status_summary(
        self,
        active_children,
        *,
        continuation,
        events,
        failure_rows,
        latest_effect,
        latest_provider,
        providers,
        run_status,
        tool_observations_by_call: dict[str, dict[str, str | None]],
    ):
        phase = "agent"
        waiting_reason = None
        summary = "Agent 正在规划下一步"
        terminal_statuses = {"completed", "failed", "cancelled"}
        phase, summary, waiting_reason = self._inspect_phase_summary(
            active_children,
            continuation=continuation,
            latest_effect=latest_effect,
            latest_provider=latest_provider,
            phase=phase,
            run_status=run_status,
            summary=summary,
            terminal_statuses=terminal_statuses,
            waiting_reason=waiting_reason,
        )

        error_candidates: list[tuple[float, dict[str, Any]]] = []
        self._inspect_provider_failures(
            error_candidates,
            events=events,
            failure_rows=failure_rows,
            providers=providers,
            tool_observations_by_call=tool_observations_by_call,
        )
        last_error = (
            None if not error_candidates else max(error_candidates, key=lambda item: item[0])[1]
        )
        return last_error, phase, summary, terminal_statuses, waiting_reason

    async def _inspect_start_snapshot(self, db, run, run_key):
        if run is None:
            return (True, (None), None, None, None)

        snapshot = await (
            await db.execute(
                """SELECT * FROM execution_run_start_snapshots
                    WHERE run_id=?""",
                (run_key,),
            )
        ).fetchone()
        continuation = await (
            await db.execute(
                """SELECT iteration,continuation_version,
                    pending_decision_id,pending_prepared_call_json,updated_at
                    FROM execution_continuations WHERE run_id=?""",
                (run_key,),
            )
        ).fetchone()
        projection = await (
            await db.execute(
                """SELECT task_scope_id,ui_state,projection_version,
                    created_at,updated_at
                    FROM execution_task_run_projections
                    WHERE root_run_id=?""",
                (run_key,),
            )
        ).fetchone()
        return False, None, continuation, projection, snapshot

    def _inspect_batch_status(
        self,
        *,
        call_rows,
        completion_state,
        pending,
        pending_calls,
        pending_outcome_status_by_call: dict[str, str],
        provider_rows,
    ):
        pending_outcome_statuses = (
            pending.get("outcome_statuses", []) if isinstance(pending, Mapping) else []
        )
        if isinstance(pending_outcome_statuses, list):
            for index, call in enumerate(pending_calls):
                if not isinstance(call, Mapping) or index >= len(pending_outcome_statuses):
                    continue
                call_id = str(call.get("stable_call_id") or "").strip()
                outcome_status = str(pending_outcome_statuses[index] or "").strip()
                if call_id and outcome_status:
                    pending_outcome_status_by_call[call_id] = outcome_status
        observed_statuses_by_batch: dict[str, list[str | None]] = {}
        for item in call_rows:
            batch_id = str(item["provider_batch_id"])
            call_id = str(item["provider_call_id"])
            observed_statuses_by_batch.setdefault(batch_id, []).append(
                pending_outcome_status_by_call.get(call_id)
            )
        observed_batch_status: dict[str, str] = {}
        pending_current_attempt = self._pending_current_attempt(
            completion_state, observed_batch_status, observed_statuses_by_batch
        )
        providers = []
        for item in provider_rows:
            self._inspect_tool_dispatch_item(
                item=item,
                providers=providers,
            )
        return observed_batch_status, pending_current_attempt, providers

    async def _inspect_goal_rows(self, db, run_key):
        goal = await (
            await db.execute(
                """SELECT goal_id,task_scope_id,status,goal_version,
                    created_at,updated_at,ended_at
                    FROM execution_task_goals WHERE root_run_id=?""",
                (run_key,),
            )
        ).fetchone()
        latest_plan = await (
            await db.execute(
                """SELECT plan_version,trigger_failure_set_id,created_at
                    FROM execution_plan_versions WHERE root_run_id=?
                    ORDER BY plan_version DESC LIMIT 1""",
                (run_key,),
            )
        ).fetchone()

        lineage_rows = await (
            await db.execute(
                """SELECT run_id,parent_run_id,driver_kind,profile_key,
                    status,version,created_at,started_at,updated_at,ended_at
                    FROM execution_runs WHERE root_run_id=?
                    ORDER BY created_at,run_id LIMIT 200""",
                (run_key,),
            )
        ).fetchall()
        return goal, latest_plan, lineage_rows

    async def _inspect_provider_rows(self, db, run_key):
        provider_rows = await (
            await db.execute(
                """SELECT i.run_id,i.invocation_id,i.provider_id,i.model_id,
                    i.adapter_id,i.idempotency_group_id,i.attempt_ordinal,
                    i.status,i.claimed_at,i.dispatch_started_at,i.updated_at,
                    i.outcome_ref,i.request_hash,i.policy_snapshot_json,
                    p.payload_json AS input_payload_json,
                    o.payload_json AS outcome_payload_json,
                    a.reason AS audit_reason,
                    a.error_type AS audit_error_type,
                    a.error_message AS audit_error_message
                    FROM execution_provider_invocations AS i
                    JOIN execution_runs AS r ON r.run_id=i.run_id
                    LEFT JOIN execution_provider_invocation_inputs AS p
                      ON p.run_id=i.run_id
                     AND p.invocation_id=i.invocation_id
                    LEFT JOIN execution_provider_invocation_outcomes AS o
                      ON o.run_id=i.run_id
                     AND o.invocation_id=i.invocation_id
                    LEFT JOIN execution_provider_invocation_audits AS a
                      ON a.run_id=i.run_id
                     AND a.invocation_id=i.invocation_id
                    WHERE r.root_run_id=?
                    ORDER BY i.claimed_at,i.invocation_id LIMIT 200""",
                (run_key,),
            )
        ).fetchall()
        batch_rows = await (
            await db.execute(
                """SELECT provider_batch_id,provider_turn_id,
                    pending_call_count,failure_set_id,status,batch_version,
                    created_at,updated_at,settled_at
                    FROM execution_provider_action_batches
                    WHERE root_run_id=?
                    ORDER BY created_at,provider_batch_id LIMIT 200""",
                (run_key,),
            )
        ).fetchall()
        call_rows = await (
            await db.execute(
                """SELECT call_record_id,provider_batch_id,call_order,
                    provider_call_id,raw_tool_name,admission_state,
                    raw_arguments_ref,raw_arguments_hash,
                    parsed_arguments_hash,prepared_call_ref,
                    terminal_outcome_ref,call_version,created_at,updated_at
                    FROM execution_provider_action_calls
                    WHERE root_run_id=?
                    ORDER BY created_at,provider_batch_id,call_order LIMIT 400""",
                (run_key,),
            )
        ).fetchall()
        return batch_rows, call_rows, provider_rows

    async def _inspect_effect_rows(self, db, run_key):
        effect_rows = await (
            await db.execute(
                """SELECT e.effect_id,e.run_id,e.call_id,e.tool_name,
                    e.effect_type,e.status,e.handoff_state,
                    e.completion_disposition,e.receipt_ref,e.policy_json,
                    e.prepared_json,e.outcome_json,e.artifact_refs_json,
                    e.created_at,e.updated_at,e.ended_at
                    FROM execution_effects AS e
                    JOIN execution_runs AS r ON r.run_id=e.run_id
                    WHERE r.root_run_id=?
                    ORDER BY e.created_at,e.effect_id LIMIT 400""",
                (run_key,),
            )
        ).fetchall()
        workflow_effect_rows = await (
            await db.execute(
                """SELECT e.effect_id,e.run_id,e.status,e.prepared_json,
                    e.outcome_json,e.receipt_ref,e.artifact_refs_json,
                    e.started_at,e.updated_at,e.ended_at
                    FROM workflow_effects AS e
                    JOIN execution_runs AS r ON r.run_id=e.run_id
                    WHERE r.root_run_id=?
                    ORDER BY e.started_at,e.effect_id LIMIT 400""",
                (run_key,),
            )
        ).fetchall()
        workflow_checkpoint_rows = await (
            await db.execute(
                """SELECT wr.run_id,wc.checkpoint_blob
                    FROM workflow_runs AS wr
                    JOIN execution_runs AS er ON er.run_id=wr.run_id
                    LEFT JOIN workflow_checkpoints AS wc
                      ON wc.thread_id=wr.thread_id
                     AND wc.checkpoint_ns=wr.head_checkpoint_ns
                     AND wc.checkpoint_id=wr.head_checkpoint_id
                    WHERE er.root_run_id=?
                      AND wr.workflow_name='durable_task'
                    ORDER BY er.created_at,wr.run_id LIMIT 100""",
                (run_key,),
            )
        ).fetchall()
        return effect_rows, workflow_checkpoint_rows, workflow_effect_rows

    async def _inspect_attempt_rows(self, db, run_key):
        attempt_rows = await (
            await db.execute(
                """SELECT attempt_id,run_id,provider_turn_id,
                    provider_batch_id,plan_version,trigger_failure_set_id,
                    supersedes_attempt_id,status,attempt_version,created_at,
                    updated_at,ended_at
                    FROM execution_attempt_records WHERE root_run_id=?
                    ORDER BY created_at,attempt_id LIMIT 200""",
                (run_key,),
            )
        ).fetchall()
        failure_rows = await (
            await db.execute(
                """SELECT report_ref,run_id,attempt_id,plan_version,
                    provider_call_id,child_run_id,failed_call_id,
                    failed_effect_id,failed_step,error_class,error_code,
                    source_kind,source_identity,created_at
                    FROM execution_task_failure_reports WHERE root_run_id=?
                    ORDER BY created_at,report_ref LIMIT 400""",
                (run_key,),
            )
        ).fetchall()
        event_rows = await (
            await db.execute(
                """SELECT e.event_id,e.run_id,e.durable_seq,e.kind,
                    e.status,e.driver_kind,e.correlation_json,e.payload_json,
                    e.error_json,e.created_at
                    FROM execution_events AS e
                    JOIN execution_runs AS r ON r.run_id=e.run_id
                    WHERE r.root_run_id=?
                    ORDER BY e.created_at,e.run_id,e.durable_seq LIMIT 400""",
                (run_key,),
            )
        ).fetchall()
        return attempt_rows, event_rows, failure_rows

    def _pending_current_attempt(
        self,
        completion_state,
        observed_batch_status: dict[str, str],
        observed_statuses_by_batch: dict[str, list[str | None]],
    ):
        terminal_tool_statuses = {
            "succeeded",
            "failed",
            "cancelled",
            "unknown",
            "rejected",
            "skipped",
        }
        for batch_id, statuses in observed_statuses_by_batch.items():
            concrete = [status for status in statuses if status]
            if len(concrete) != len(statuses) or any(
                status not in terminal_tool_statuses for status in concrete
            ):
                continue
            if any(status in {"failed", "unknown", "rejected"} for status in concrete):
                observed_batch_status[batch_id] = "failed"
            elif any(status == "cancelled" for status in concrete):
                observed_batch_status[batch_id] = "cancelled"
            else:
                observed_batch_status[batch_id] = "succeeded"
        pending_current_attempt = None
        if isinstance(completion_state, Mapping):
            current_attempt = completion_state.get("current_attempt")
            if isinstance(current_attempt, Mapping):
                pending_current_attempt = current_attempt
        return pending_current_attempt

    def _inspect_tool_dispatch_item(self, *, item, providers) -> None:
        started_at = _optional_float(item["dispatch_started_at"])
        updated_at = float(item["updated_at"])
        status = str(item["status"])
        audit_reason = None if item["audit_reason"] is None else str(item["audit_reason"])
        if status == "unknown" and audit_reason is None:
            audit_reason = "legacy_unknown_reason_not_recorded"
        providers.append(
            {
                "run_id": str(item["run_id"]),
                "invocation_id": str(item["invocation_id"]),
                "provider_id": str(item["provider_id"]),
                "model_id": str(item["model_id"]),
                "adapter_id": str(item["adapter_id"]),
                "iteration": _iteration(str(item["idempotency_group_id"])),
                "attempt_ordinal": int(item["attempt_ordinal"]),
                "status": status,
                "claimed_at": float(item["claimed_at"]),
                "dispatch_started_at": started_at,
                "updated_at": updated_at,
                "duration_ms": max(
                    0.0,
                    (
                        updated_at
                        - (started_at if started_at is not None else float(item["claimed_at"]))
                    )
                    * 1000.0,
                ),
                "audit_reason": audit_reason,
                "request_hash": str(item["request_hash"]),
                "policy": _json(item["policy_snapshot_json"], {}),
                "input": _json(item["input_payload_json"], None),
                "output": _json(item["outcome_payload_json"], None),
                "error_type": (
                    None if item["audit_error_type"] is None else str(item["audit_error_type"])
                ),
                "error_message": (
                    None
                    if item["audit_error_message"] is None
                    else str(item["audit_error_message"])
                ),
            }
        )

    def _inspect_workflow_checkpoint_events(
        self,
        *,
        event_rows,
        workflow_checkpoint_rows,
        workflow_plans,
        workflow_step_by_call: dict[tuple[str, str], str],
    ):
        for item in workflow_checkpoint_rows:
            workflow_run_id = str(item["run_id"])
            checkpoint = _checkpoint_payload(item["checkpoint_blob"])
            state = checkpoint.get("state")
            values = state.get("values") if isinstance(state, Mapping) else None
            if not isinstance(values, Mapping):
                continue
            proposal = values.get("proposal_state")
            proposal = proposal if isinstance(proposal, Mapping) else {}
            committed = proposal.get("committed_tool_results")
            current_results = values.get("tool_results")
            for raw_results in (committed, current_results):
                if not isinstance(raw_results, Mapping):
                    continue
                for raw_call_id, raw_result in raw_results.items():
                    if not isinstance(raw_result, Mapping):
                        continue
                    call_id = str(raw_result.get("stable_call_id") or raw_call_id or "").strip()
                    step_id = str(raw_result.get("workflow_step_id") or "").strip()
                    if call_id and step_id:
                        workflow_step_by_call[(workflow_run_id, call_id)] = step_id

            raw_todos = values.get("todos")
            steps = []
            if isinstance(raw_todos, list):
                for index, raw_todo in enumerate(raw_todos):
                    if not isinstance(raw_todo, Mapping):
                        continue
                    step_id = str(raw_todo.get("workflow_step_id") or "").strip()
                    title = str(raw_todo.get("title") or "").strip()
                    if not step_id or not title:
                        continue
                    steps.append(
                        {
                            "workflow_step_id": step_id,
                            "index": int(raw_todo.get("index", index)),
                            "title": title[:4000],
                            "status": str(raw_todo.get("status") or "pending"),
                        }
                    )

            messages = []
            raw_messages = proposal.get("messages")
            if isinstance(raw_messages, list):
                for index, raw_message in enumerate(raw_messages):
                    if (
                        not isinstance(raw_message, Mapping)
                        or str(raw_message.get("role") or "") != "assistant"
                    ):
                        continue
                    text = _public_message(raw_message.get("content"))
                    raw_calls = raw_message.get("tool_calls")
                    call_ids = []
                    if isinstance(raw_calls, list):
                        call_ids = [
                            str(call.get("id") or "").strip()
                            for call in raw_calls
                            if isinstance(call, Mapping) and str(call.get("id") or "").strip()
                        ]
                    if not text and not call_ids:
                        continue
                    step_id = next(
                        (
                            workflow_step_by_call.get((workflow_run_id, call_id))
                            for call_id in call_ids
                            if workflow_step_by_call.get((workflow_run_id, call_id))
                        ),
                        None,
                    )
                    messages.append(
                        {
                            "message_id": str(
                                raw_message.get("message_id") or f"assistant-{index}"
                            ),
                            "text": text,
                            "tool_call_ids": call_ids,
                            "workflow_step_id": step_id,
                        }
                    )
            workflow_plans.append(
                {
                    "run_id": workflow_run_id,
                    "plan_id": (
                        str(proposal.get("active_plan_id"))
                        if proposal.get("active_plan_id")
                        else None
                    ),
                    "active_step_id": (
                        str(proposal.get("active_step_id"))
                        if proposal.get("active_step_id")
                        else None
                    ),
                    "steps": steps,
                    "public_messages": messages,
                }
            )

        events = []
        for item in event_rows:
            error = _json(item["error_json"], {})
            correlation = _json(item["correlation_json"], {})
            events.append(
                {
                    "event_id": str(item["event_id"]),
                    "run_id": str(item["run_id"]),
                    "seq": int(item["durable_seq"]),
                    "kind": str(item["kind"]),
                    "status": str(item["status"]),
                    "driver_kind": str(item["driver_kind"]),
                    "failure_layer": (
                        str(correlation.get("failure_layer"))
                        if isinstance(correlation, Mapping)
                        and correlation.get("failure_layer") is not None
                        else None
                    ),
                    "failure_code": (
                        str(correlation.get("failure_code"))
                        if isinstance(correlation, Mapping)
                        and correlation.get("failure_code") is not None
                        else None
                    ),
                    "error_code": (
                        str(error.get("code"))
                        if isinstance(error, Mapping) and error.get("code") is not None
                        else None
                    ),
                    "error_message": (
                        str(error.get("message"))[:1000]
                        if isinstance(error, Mapping) and error.get("message") is not None
                        else None
                    ),
                    "payload": _json(item["payload_json"], {}),
                    "correlation": correlation,
                    "created_at": float(item["created_at"]),
                }
            )
        return events

    def _inspect_phase_summary(
        self,
        active_children,
        *,
        continuation,
        latest_effect,
        latest_provider,
        phase,
        run_status,
        summary,
        terminal_statuses,
        waiting_reason,
    ):
        if run_status in terminal_statuses:
            phase = "terminal"
            summary = {
                "completed": "任务已完成",
                "failed": "任务已失败",
                "cancelled": "任务已取消",
            }[run_status]
        elif continuation is not None and continuation["pending_decision_id"] is not None:
            phase = "authorization"
            waiting_reason = "user_decision"
            summary = "正在等待你的授权或选择"
        elif (
            latest_provider is not None
            and str(latest_provider["status"]).split(".")[-1].lower() == "claimed"
        ):
            phase = "provider"
            waiting_reason = "provider_response"
            summary = f"正在等待模型 {latest_provider['model_id']} 返回结果"
        elif latest_effect is not None and str(latest_effect["status"]).split(".")[
            -1
        ].lower() not in terminal_statuses | {
            "succeeded",
            "rejected",
            "unknown",
        }:
            phase = "tool"
            waiting_reason = "tool_execution"
            summary = f"正在执行工具 {latest_effect['tool_name']}"
        elif active_children:
            phase = "delegated_workflow"
            waiting_reason = "child_run"
            child_profile = str(active_children[-1]["profile_key"])
            child_label = {
                "workflow.durable_task": "多步骤任务",
                "workflow.capability_build": "新能力构建任务",
                "workflow.deep_research": "深度调研任务",
                "workflow.presentation": "演示文稿任务",
                "workflow.personal_v1": "个人工作流",
            }.get(child_profile, "委派任务")
            summary = f"正在处理{child_label}，完成后会自动回到当前对话"
        elif run_status == "waiting":
            phase = "waiting"
            waiting_reason = "no_active_operation"
            summary = "任务处于等待状态，但当前没有可见的执行动作"
        return phase, summary, waiting_reason

    def _inspect_provider_failures(
        self,
        error_candidates: list[tuple[float, dict[str, Any]]],
        *,
        events,
        failure_rows,
        providers,
        tool_observations_by_call: dict[str, dict[str, str | None]],
    ) -> None:
        for provider_item in providers:
            if (
                provider_item.get("error_type")
                or provider_item.get("error_message")
                or provider_item.get("audit_reason")
            ):
                error_candidates.append(
                    (
                        float(provider_item["updated_at"]),
                        {
                            "source": "provider",
                            "code": provider_item.get("audit_reason"),
                            "type": provider_item.get("error_type"),
                            "message": provider_item.get("error_message"),
                        },
                    )
                )
        for failure_item in failure_rows:
            error_candidates.append(
                (
                    float(failure_item["created_at"]),
                    {
                        "source": "task_failure",
                        "code": str(failure_item["error_code"]),
                        "type": str(failure_item["error_class"]),
                        "message": tool_observations_by_call.get(
                            str(failure_item["provider_call_id"] or ""),
                            {},
                        ).get("error_message"),
                    },
                )
            )
        for event_item in events:
            if event_item.get("error_code") or event_item.get("error_message"):
                error_candidates.append(
                    (
                        float(event_item["created_at"]),
                        {
                            "source": "event",
                            "code": event_item.get("error_code"),
                            "type": event_item.get("failure_layer"),
                            "message": event_item.get("error_message"),
                        },
                    )
                )


def _inspect_projection(projection):
    return (
        None
        if projection is None
        else {
            "task_scope_id": str(projection["task_scope_id"]),
            "ui_state": str(projection["ui_state"]),
            "version": int(projection["projection_version"]),
            "created_at": float(projection["created_at"]),
            "updated_at": float(projection["updated_at"]),
        }
    )


def _inspect_activity(
    artifact_count,
    *,
    last_error,
    last_progress_at,
    phase,
    run_status,
    stalled_for_seconds,
    summary,
    terminal_statuses,
    waiting_reason,
):
    return {
        "phase": phase,
        "summary": summary,
        "waiting_reason": waiting_reason,
        "last_progress_at": last_progress_at,
        "stalled_for_seconds": stalled_for_seconds,
        "is_stalled": (run_status not in terminal_statuses and stalled_for_seconds >= 60.0),
        "last_error": last_error,
        "artifact_count": artifact_count,
    }


def _inspect_run(run):
    return {
        "run_id": str(run["run_id"]),
        "root_run_id": str(run["root_run_id"]),
        "request_id": str(run["request_id"]),
        "turn_id": str(run["turn_id"]),
        "trace_id": str(run["trace_id"]),
        "driver_kind": str(run["driver_kind"]),
        "profile_key": str(run["profile_key"]),
        "persistence_level": str(run["persistence_level"]),
        "status": str(run["status"]),
        "version": int(run["version"]),
        "durable_seq": int(run["durable_seq"]),
        "owner_kind": str(run["owner_kind"]),
        "owner_generation": int(run["owner_generation"]),
        "created_at": float(run["created_at"]),
        "started_at": _optional_float(run["started_at"]),
        "updated_at": float(run["updated_at"]),
        "ended_at": _optional_float(run["ended_at"]),
    }


def _inspect_prepared_context(product_turn_steps, snapshot, snapshot_request):
    return {
        "available": snapshot is not None,
        "start_fingerprint": (None if snapshot is None else str(snapshot["start_fingerprint"])),
        "prepared_ref_count": (
            0
            if snapshot is None
            else len(
                _json(snapshot["prepared_refs_json"], {})
                if isinstance(
                    _json(snapshot["prepared_refs_json"], {}),
                    Mapping,
                )
                else {}
            )
        ),
        "terminal_delivery_count": (
            0
            if snapshot is None
            else len(
                _json(snapshot["terminal_deliveries_json"], [])
                if isinstance(
                    _json(snapshot["terminal_deliveries_json"], []),
                    list,
                )
                else []
            )
        ),
        "created_at": (None if snapshot is None else float(snapshot["created_at"])),
        "steps": product_turn_steps,
        "details": (
            None
            if snapshot is None
            else {
                "canonical_messages": _json(snapshot["canonical_messages_json"], []),
                "sanitized_request": snapshot_request,
                "run_context": _json(snapshot["run_context_json"], {}),
                "run_spec": _json(snapshot["run_spec_json"], {}),
                "prepared_refs": _json(snapshot["prepared_refs_json"], {}),
                "capability_snapshot": _json(snapshot["capability_snapshot_json"], {}),
                "provider_launch_policy": _json(snapshot["provider_launch_policy_json"], {}),
            }
        ),
    }


def _inspect_continuation(continuation, pending_calls, pending_tool_names):
    return (
        None
        if continuation is None
        else {
            "iteration": int(continuation["iteration"]),
            "version": int(continuation["continuation_version"]),
            "pending_decision_id": (
                None
                if continuation["pending_decision_id"] is None
                else str(continuation["pending_decision_id"])
            ),
            "pending_call_count": len(pending_calls),
            "pending_tool_names": pending_tool_names[:50],
            "updated_at": float(continuation["updated_at"]),
        }
    )


def _inspect_goal(goal, latest_plan):
    return (
        None
        if goal is None
        else {
            "goal_id": str(goal["goal_id"]),
            "task_scope_id": str(goal["task_scope_id"]),
            "status": str(goal["status"]),
            "version": int(goal["goal_version"]),
            "plan_version": (None if latest_plan is None else int(latest_plan["plan_version"])),
            "trigger_failure_set_id": (
                None
                if latest_plan is None or latest_plan["trigger_failure_set_id"] is None
                else str(latest_plan["trigger_failure_set_id"])
            ),
            "created_at": float(goal["created_at"]),
            "updated_at": float(goal["updated_at"]),
            "ended_at": _optional_float(goal["ended_at"]),
        }
    )


def _inspect_lineage(lineage_rows):
    return [
        {
            "run_id": str(item["run_id"]),
            "parent_run_id": (
                None if item["parent_run_id"] is None else str(item["parent_run_id"])
            ),
            "driver_kind": str(item["driver_kind"]),
            "profile_key": str(item["profile_key"]),
            "status": str(item["status"]),
            "version": int(item["version"]),
            "created_at": float(item["created_at"]),
            "started_at": _optional_float(item["started_at"]),
            "updated_at": float(item["updated_at"]),
            "ended_at": _optional_float(item["ended_at"]),
        }
        for item in lineage_rows
    ]


def _inspect_action_batches(batch_rows, observed_batch_status):
    return [
        {
            "batch_id": str(item["provider_batch_id"]),
            "provider_turn_id": str(item["provider_turn_id"]),
            "pending_call_count": int(item["pending_call_count"]),
            "failure_set_id": (
                None if item["failure_set_id"] is None else str(item["failure_set_id"])
            ),
            "status": str(item["status"]),
            "observed_status": observed_batch_status.get(str(item["provider_batch_id"])),
            "version": int(item["batch_version"]),
            "created_at": float(item["created_at"]),
            "updated_at": float(item["updated_at"]),
            "settled_at": _optional_float(item["settled_at"]),
        }
        for item in batch_rows
    ]


def _inspect_tool_calls(call_rows, pending_outcome_status_by_call, tool_observations_by_call):
    return [
        {
            "call_record_id": str(item["call_record_id"]),
            "batch_id": str(item["provider_batch_id"]),
            "order": int(item["call_order"]),
            "call_id": str(item["provider_call_id"]),
            "tool_name": str(item["raw_tool_name"]),
            "admission_state": str(item["admission_state"]),
            "outcome_status": (pending_outcome_status_by_call.get(str(item["provider_call_id"]))),
            "error_code": (
                tool_observations_by_call.get(str(item["provider_call_id"]), {}).get("error_code")
            ),
            "error_message": (
                tool_observations_by_call.get(str(item["provider_call_id"]), {}).get(
                    "error_message"
                )
            ),
            "terminal_outcome_ref": (
                None if item["terminal_outcome_ref"] is None else str(item["terminal_outcome_ref"])
            ),
            "details": {
                "raw_arguments_ref": (
                    None if item["raw_arguments_ref"] is None else str(item["raw_arguments_ref"])
                ),
                "raw_arguments_hash": (
                    None if item["raw_arguments_hash"] is None else str(item["raw_arguments_hash"])
                ),
                "parsed_arguments_hash": (
                    None
                    if item["parsed_arguments_hash"] is None
                    else str(item["parsed_arguments_hash"])
                ),
                "prepared_call_ref": (
                    None if item["prepared_call_ref"] is None else str(item["prepared_call_ref"])
                ),
            },
            "version": int(item["call_version"]),
            "created_at": float(item["created_at"]),
            "updated_at": float(item["updated_at"]),
        }
        for item in call_rows
    ]


def _inspect_effects(effect_rows):
    return [
        {
            "effect_id": str(item["effect_id"]),
            "run_id": str(item["run_id"]),
            "call_id": str(item["call_id"]),
            "tool_name": str(item["tool_name"]),
            "effect_type": str(item["effect_type"]),
            "status": str(item["status"]),
            "handoff_state": str(item["handoff_state"]),
            "completion_disposition": str(item["completion_disposition"]),
            "receipt_ref": (None if item["receipt_ref"] is None else str(item["receipt_ref"])),
            "details": {
                "policy": _json(item["policy_json"], {}),
                "prepared": _json(item["prepared_json"], {}),
                "outcome": _json(item["outcome_json"], None),
                "artifact_refs": _json(item["artifact_refs_json"], []),
            },
            "created_at": float(item["created_at"]),
            "updated_at": float(item["updated_at"]),
            "ended_at": _optional_float(item["ended_at"]),
        }
        for item in effect_rows
    ]


def _inspect_workflow_effects(*, workflow_effect_rows, workflow_step_by_call):
    return [
        {
            "effect_id": str(item["effect_id"]),
            "run_id": str(item["run_id"]),
            "status": str(item["status"]),
            "workflow_step_id": workflow_step_by_call.get(
                (
                    str(item["run_id"]),
                    _prepared_call_id(item["prepared_json"]),
                )
            ),
            "receipt_ref": (None if item["receipt_ref"] is None else str(item["receipt_ref"])),
            "details": {
                "prepared": _json(item["prepared_json"], {}),
                "outcome": _json(item["outcome_json"], None),
                "artifact_refs": _json(item["artifact_refs_json"], []),
            },
            "created_at": float(item["started_at"]),
            "updated_at": float(item["updated_at"]),
            "ended_at": _optional_float(item["ended_at"]),
        }
        for item in workflow_effect_rows
    ]


def _inspect_attempts(attempt_rows, observed_batch_status, pending_current_attempt):
    return [
        {
            "attempt_id": str(item["attempt_id"]),
            "run_id": str(item["run_id"]),
            "provider_turn_id": str(item["provider_turn_id"]),
            "batch_id": str(item["provider_batch_id"]),
            "plan_version": int(item["plan_version"]),
            "trigger_failure_set_id": (
                None
                if item["trigger_failure_set_id"] is None
                else str(item["trigger_failure_set_id"])
            ),
            "supersedes_attempt_id": (
                None
                if item["supersedes_attempt_id"] is None
                else str(item["supersedes_attempt_id"])
            ),
            "status": str(item["status"]),
            "continuation_status": (
                str(pending_current_attempt["status"])
                if pending_current_attempt is not None
                and str(pending_current_attempt.get("attempt_id") or "") == str(item["attempt_id"])
                and pending_current_attempt.get("status") is not None
                else observed_batch_status.get(str(item["provider_batch_id"]))
            ),
            "version": int(item["attempt_version"]),
            "created_at": float(item["created_at"]),
            "updated_at": float(item["updated_at"]),
            "ended_at": _optional_float(item["ended_at"]),
        }
        for item in attempt_rows
    ]


def _inspect_failures(failure_rows, tool_observations_by_call):
    return [
        {
            "report_ref": str(item["report_ref"]),
            "run_id": str(item["run_id"]),
            "attempt_id": str(item["attempt_id"]),
            "plan_version": int(item["plan_version"]),
            "provider_call_id": (
                None if item["provider_call_id"] is None else str(item["provider_call_id"])
            ),
            "child_run_id": (None if item["child_run_id"] is None else str(item["child_run_id"])),
            "failed_call_id": (
                None if item["failed_call_id"] is None else str(item["failed_call_id"])
            ),
            "failed_effect_id": (
                None if item["failed_effect_id"] is None else str(item["failed_effect_id"])
            ),
            "failed_step": str(item["failed_step"]),
            "error_class": str(item["error_class"]),
            "error_code": str(item["error_code"]),
            "error_message": (
                tool_observations_by_call.get(str(item["provider_call_id"] or ""), {}).get(
                    "error_message"
                )
            ),
            "source_kind": str(item["source_kind"]),
            "source_identity": str(item["source_identity"]),
            "created_at": float(item["created_at"]),
        }
        for item in failure_rows
    ]
