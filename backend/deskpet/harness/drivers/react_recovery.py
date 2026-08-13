"""Failure persistence and host-owned capability retry for the ReAct driver."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Mapping

from deskpet.capabilities.failure_receipts import parse_capability_registry_source
from deskpet.capabilities.store import CapabilityStoreError
from deskpet.execution.contracts import (
    AttemptFailureSet as DurableAttemptFailureSet,
    OutcomeStatus,
    TaskFailureReport as DurableTaskFailureReport,
    fingerprint_json,
)
from deskpet.execution.failure_reports import FailureReportIssuer, TaskFailureReport
from deskpet.harness.attempts import AttemptFailureSet, AttemptRecord
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.effects import (
    NormalizedToolOutcome,
    PreparedToolCall,
    ToolOutcomeState,
)

from .react_boundary import ReactCommandBoundary, _skill_tool_intersection
from .react_loop import AgentLoopCollaborator, ReactToolBatch


class ReactFailureRecoveryMixin:
    """Private recovery mechanics inherited by the single ReAct owner."""

    @staticmethod
    def _prepared_action_fingerprint(call: PreparedToolCall) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "tool_name": call.tool_name,
                    "args_hash": call.args_hash,
                    "effect_type": call.effect_type,
                    "tool_spec_fingerprint": call.tool_spec_fingerprint,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _stage_failure_reports(
        boundary: ReactCommandBoundary,
        tool_registry: Any | None = None,
    ) -> ReactCommandBoundary:
        failed_prepared = tuple(
            index
            for index, outcome in enumerate(boundary.outcomes)
            if outcome is not None
            and outcome.state is not ToolOutcomeState.SUCCESS
        )
        raw_failed = tuple(dict(item) for item in boundary.raw_failures)
        if not failed_prepared and not raw_failed:
            state = copy.deepcopy(dict(boundary.completion_state))
            state.pop("capability_retry_inflight", None)
            if state.get("current_attempt"):
                state["current_attempt"] = {
                    **dict(state["current_attempt"]),
                    "status": "succeeded",
                }
                state["attempt_status"] = "succeeded"
                return replace(boundary, completion_state=state)
            return boundary
        state = copy.deepcopy(dict(boundary.completion_state))
        current = dict(state.get("current_attempt") or {})
        if (
            state.get("failure_report_command_id") == boundary.command_id
            or not current.get("attempt_id")
        ):
            return boundary
        plan_version = max(1, int(current.get("plan_version") or 1))
        strategy = str(current.get("strategy_fingerprint") or "")
        prior = tuple(
            str(item)
            for item in state.get("prior_strategy_fingerprints", ())
            if str(item)
        )
        strategy_history = tuple(dict.fromkeys((*prior, strategy)))
        failed_attempt_status = str(current.get("status") or "failed")
        if failed_attempt_status not in {"failed", "rejected", "blocked"}:
            failed_attempt_status = "failed"
        attempt = AttemptRecord(
            attempt_id=str(current["attempt_id"]),
            root_run_id=(
                boundary.run_context.root_run_id
                if boundary.run_context is not None
                else boundary.run_id
            ),
            run_id=boundary.run_id,
            provider_turn_id=str(
                current.get("provider_turn_id") or boundary.command_id
            ),
            provider_batch_id=str(
                current.get("provider_batch_id") or boundary.command_id
            ),
            plan_version=plan_version,
            trigger_failure_set_id=(
                str(state["latest_failure_set_id"])
                if state.get("latest_failure_set_id")
                else None
            ),
            supersedes_attempt_id=(
                str(state["latest_attempt_id"])
                if state.get("latest_attempt_id")
                else None
            ),
            strategy_fingerprint=strategy,
            planned_call_refs=tuple(
                str(item) for item in current.get("planned_call_refs", ())
            ),
            checkpoint_ref=boundary.prepared_context_ref,
            status=failed_attempt_status,  # type: ignore[arg-type]
            budget_eligible=bool(current.get("budget_eligible", True)),
        )
        reports = []
        metadata = [dict(item) for item in boundary.outcome_metadata]
        task_scope_id = str(
            boundary.request_payload.get("task_scope_id")
            or boundary.request_payload.get("root_run_id")
            or attempt.root_run_id
        )
        for index in failed_prepared:
            call = boundary.pending_calls[index]
            context = boundary.tool_contexts[index]
            outcome = boundary.outcomes[index]
            assert outcome is not None and outcome.error is not None
            error = dict(outcome.error)
            error_code = str(error.get("code") or "tool_failed")
            try:
                if tool_registry is None:
                    raise KeyError("tool registry unavailable")
                prepared_spec = tool_registry.resolve_prepared_spec(call)
                capability_source = parse_capability_registry_source(
                    str(prepared_spec.source)
                )
            except Exception:  # noqa: BLE001 - stale provenance fails closed below
                capability_source = None
            source_kind = (
                "child_terminal"
                if metadata[index].get("child_run_id")
                else "tool_authorization"
                if error_code == "authorization_denied"
                else "strategy_rejected"
                if error_code == "replan_required"
                else "tool_executor"
            )
            action_fingerprint = ReactFailureRecoveryMixin._prepared_action_fingerprint(call)
            source_identity = (
                f"capability:{capability_source[0]}:{call.tool_name}"
                if capability_source is not None
                else f"{call.tool_name}@{call.tool_spec_version}"
            )
            report = FailureReportIssuer.issue(
                root_run_id=attempt.root_run_id,
                run_id=boundary.run_id,
                task_scope_id=task_scope_id,
                attempt_id=attempt.attempt_id,
                plan_version=plan_version,
                call_record_id=(
                    "provider-call:"
                    + hashlib.sha256(
                        f"{boundary.run_id}|{call.stable_call_id}".encode()
                    ).hexdigest()
                ),
                source_kind=source_kind,
                source_identity=source_identity,
                provider_call_id=call.stable_call_id,
                child_run_id=(
                    str(metadata[index]["child_run_id"])
                    if metadata[index].get("child_run_id")
                    else None
                ),
                failed_call_id=call.stable_call_id,
                failed_effect_id=context.effect_id,
                failed_step=call.tool_name,
                error_code=error_code,
                error_class=(
                    "capability" if capability_source is not None else None
                ),
                action_fingerprint=action_fingerprint,
                artifact_refs=tuple(
                    str(item)
                    for item in metadata[index].get("artifact_refs", ())
                ),
                checkpoint_ref=boundary.prepared_context_ref,
                prior_strategy_fingerprints=strategy_history,
            )
            reports.append(report)
            if capability_source is not None:
                metadata[index]["capability_failure_provenance"] = {
                    "capability_id": capability_source[0],
                    "pack_version": capability_source[1],
                    "manifest_hash": capability_source[2],
                    "tool_spec_fingerprint": call.tool_spec_fingerprint,
                }
        for failure in raw_failed:
            report = FailureReportIssuer.issue(
                root_run_id=attempt.root_run_id,
                run_id=boundary.run_id,
                task_scope_id=task_scope_id,
                attempt_id=attempt.attempt_id,
                plan_version=plan_version,
                call_record_id=str(failure["call_record_id"]),
                source_kind=str(failure["source_kind"]),  # type: ignore[arg-type]
                source_identity=str(failure["source_identity"]),
                provider_call_id=str(failure["provider_call_id"]),
                failed_call_id=str(failure["provider_call_id"]),
                failed_step=str(failure["failed_step"]),
                error_code=str(failure["error_code"]),
                action_fingerprint=str(failure["action_fingerprint"]),
                checkpoint_ref=boundary.prepared_context_ref,
                prior_strategy_fingerprints=strategy_history,
            )
            reports.append(report)
        failure_set = AttemptFailureSet.from_reports(attempt, reports)
        by_ref = {report.report_ref: report for report in reports}
        for index in failed_prepared:
            call_id = boundary.pending_calls[index].stable_call_id
            report = next(
                report
                for report in reports
                if report.provider_call_id == call_id
            )
            metadata[index].update(
                {
                    "failure_report_ref": report.report_ref,
                    "failure_report": report.to_dict(),
                    "failure_set_id": failure_set.failure_set_id,
                }
            )
        reports_by_call = {
            str(report.provider_call_id): report
            for report in reports
            if report.provider_call_id is not None
        }
        enriched_raw = []
        for failure in raw_failed:
            report = reports_by_call[str(failure["provider_call_id"])]
            failure.update(
                {
                    "failure_report_ref": report.report_ref,
                    "failure_report": report.to_dict(),
                    "failure_set_id": failure_set.failure_set_id,
                }
            )
            enriched_raw.append(failure)
        state.update(
            {
                "failure_report_command_id": boundary.command_id,
                "failure_reports": [
                    by_ref[ref].to_dict() for ref in failure_set.report_refs
                ],
                "latest_failure_set_id": failure_set.failure_set_id,
                "latest_attempt_id": attempt.attempt_id,
                "attempt_status": failed_attempt_status,
                "current_attempt": {
                    **current,
                    "status": failed_attempt_status,
                    "failure_set_id": failure_set.failure_set_id,
                },
                "prior_strategy_fingerprints": list(
                    strategy_history
                )[-3:],
            }
        )
        return replace(
            boundary,
            completion_state=state,
            outcome_metadata=tuple(metadata),
            raw_failures=tuple(enriched_raw),
        )

    async def _persist_failure_set(
        self, boundary: ReactCommandBoundary
    ) -> ReactCommandBoundary:
        """Commit trusted failure facts before any provider-visible backfill."""

        failure_report_command_id = str(
            boundary.completion_state.get("failure_report_command_id") or ""
        )
        if failure_report_command_id != boundary.command_id:
            return boundary
        failure_set_id = str(
            boundary.completion_state.get("latest_failure_set_id") or ""
        )
        if not failure_set_id:
            return boundary
        reports = tuple(
            TaskFailureReport.from_dict(item)
            for item in boundary.completion_state.get("failure_reports", ())
            if isinstance(item, Mapping)
        )
        if not reports:
            raise RuntimeError("failure set identity exists without failure reports")
        reports_by_call = {
            str(report.provider_call_id): report
            for report in reports
            if report.provider_call_id is not None
        }
        ordered = tuple(
            reports_by_call[provider_call_id]
            for provider_call_id in boundary.provider_call_order
            if provider_call_id in reports_by_call
        )
        if len(ordered) != len(reports):
            raise RuntimeError("failure reports do not match the provider call order")
        durable_reports = tuple(
            DurableTaskFailureReport(**report.to_dict()) for report in ordered
        )
        await self._uow.stage_attempt_failure_set(
            DurableAttemptFailureSet(
                failure_set_id=failure_set_id,
                root_run_id=durable_reports[0].root_run_id,
                failed_attempt_id=durable_reports[0].attempt_id,
                report_refs=tuple(
                    report.report_ref for report in durable_reports
                ),
                primary_report_ref=durable_reports[0].report_ref,
                backfill_state="ready",
                provider_resume_state="pending",
            ),
            durable_reports,
        )
        builder_host = self._capability_builder_host
        capability_store = (
            None if builder_host is None else getattr(builder_host, "store", None)
        )
        if capability_store is None:
            return boundary
        metadata = [dict(item) for item in boundary.outcome_metadata]
        state = copy.deepcopy(dict(boundary.completion_state))
        receipt_refs: list[str] = []
        for index, outcome in enumerate(boundary.outcomes):
            if (
                outcome is None
                or outcome.state is ToolOutcomeState.SUCCESS
                or not isinstance(
                    metadata[index].get("capability_failure_provenance"),
                    Mapping,
                )
            ):
                continue
            provenance = dict(
                metadata[index]["capability_failure_provenance"]
            )
            call = boundary.pending_calls[index]
            context = boundary.tool_contexts[index]
            report = reports_by_call.get(call.stable_call_id)
            if report is None:
                raise RuntimeError(
                    "capability failure has no trusted task failure report"
                )
            error = dict(outcome.error or {})
            try:
                receipt = await capability_store.issue_failure_receipt(
                    root_run_id=report.root_run_id,
                    run_id=boundary.run_id,
                    attempt_id=report.attempt_id,
                    failure_report_ref=report.report_ref,
                    provider_call_id=call.stable_call_id,
                    effect_id=context.effect_id,
                    capability_id=str(provenance["capability_id"]),
                    pack_version=str(provenance["pack_version"]),
                    manifest_hash=str(provenance["manifest_hash"]),
                    tool_name=call.tool_name,
                    tool_spec_fingerprint=call.tool_spec_fingerprint,
                    canonical_args=call.arguments_json(),
                    error_code=str(error.get("code") or "tool_failed"),
                    error_fingerprint=report.error_fingerprint,
                    evidence_refs=(
                        report.report_ref,
                        *report.artifact_refs,
                    ),
                )
            except CapabilityStoreError as exc:
                metadata[index]["capability_failure_receipt_error"] = {
                    "code": exc.code,
                    "message": str(exc),
                }
                continue
            metadata[index].update(
                {
                    "capability_failure_receipt_ref": receipt.receipt_ref,
                    "capability_failure_receipt": receipt.to_dict(),
                }
            )
            receipt_refs.append(receipt.receipt_ref)
        if not receipt_refs and metadata == [
            dict(item) for item in boundary.outcome_metadata
        ]:
            return boundary
        if receipt_refs:
            state["capability_failure_receipt_refs"] = list(
                dict.fromkeys(
                    (
                        *state.get("capability_failure_receipt_refs", ()),
                        *receipt_refs,
                    )
                )
            )
        return replace(
            boundary,
            completion_state=state,
            outcome_metadata=tuple(metadata),
        )

    def _pending_capability_retry_batch(
        self, boundary: ReactCommandBoundary
    ) -> ReactToolBatch | None:
        raw_pending = boundary.completion_state.get("pending_capability_retry")
        if not isinstance(raw_pending, Mapping):
            return None
        pending = copy.deepcopy(dict(raw_pending))
        receipt_ref = str(pending.get("failure_receipt_ref") or "")
        tool_name = str(pending.get("tool_name") or "")
        retry_of_effect_id = str(pending.get("retry_of_effect_id") or "")
        canonical_args = pending.get("canonical_args")
        if (
            not receipt_ref
            or not tool_name
            or not retry_of_effect_id
            or not isinstance(canonical_args, Mapping)
        ):
            raise RuntimeError("pending capability retry contract is malformed")

        identity_seed = (
            f"{boundary.run_id}|{receipt_ref}|{retry_of_effect_id}|{tool_name}"
        )
        provider_call_id = hashlib.sha256(
            f"capability-retry-call|{identity_seed}".encode("utf-8")
        ).hexdigest()
        command_id = hashlib.sha256(
            f"capability-retry-batch|{identity_seed}".encode("utf-8")
        ).hexdigest()
        effect_id = hashlib.sha256(
            f"effect|{boundary.run_id}|{provider_call_id}".encode("utf-8")
        ).hexdigest()
        arguments = copy.deepcopy(dict(canonical_args))
        assistant_message = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": provider_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(
                            arguments,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    },
                }
            ],
        }
        canonical_messages = (
            *boundary.canonical_messages,
            assistant_message,
        )
        feedback = {
            "_host_capability_retry": {
                "failure_receipt_ref": receipt_ref,
                "retry_of_effect_id": retry_of_effect_id,
            }
        }
        synthetic_call = SimpleNamespace(
            id=provider_call_id,
            name=tool_name,
            arguments=arguments,
            args_raw=None,
            args_parse_error=None,
        )

        error_code = ""
        error_message = ""
        prepared: PreparedToolCall | None = None
        context: ToolExecutionContext | None = None
        try:
            if boundary.run_context is None:
                raise RuntimeError(
                    "host retry requires the immutable root RunContext"
                )
            allowed = AgentLoopCollaborator._allowed_tool_names(
                boundary.to_start()
            )
            if allowed is not None and tool_name not in allowed:
                error_code = "capability_retry_tool_not_exposed"
                raise RuntimeError(
                    "repaired tool is absent from the refreshed capability snapshot"
                )
            from deskpet.harness.context import HostContextFactory

            context = HostContextFactory().create_tool_context(
                boundary.run_context,
                run_id=boundary.run_id,
                command_id=command_id,
                call_id=provider_call_id,
                effect_id=effect_id,
                scope_id=AgentLoopCollaborator._capability_scope_id(
                    boundary.to_start()
                ),
                capability_snapshot_ref=(
                    AgentLoopCollaborator._capability_snapshot_ref(
                        boundary.to_start()
                    )
                ),
                active_skill_scope_ids=boundary.active_skill_scope_ids,
                effective_skill_tool_ref_hashes=(
                    ()
                    if not boundary.active_skill_scope_ids
                    else _skill_tool_intersection(
                        boundary.capability_snapshot,
                        boundary.request_payload,
                        active_scope_ids=boundary.active_skill_scope_ids,
                        activated_scopes=(
                            boundary.activated_skill_scopes
                        ),
                    ).effective_tool_ref_hashes
                ),
                effective_skill_tool_refs_hash=(
                    boundary.effective_skill_tool_refs_hash or ""
                ),
            )
            prepare_call = getattr(self._tool_registry, "prepare_call", None)
            if not callable(prepare_call):
                error_code = "capability_retry_registry_unavailable"
                raise RuntimeError(
                    "tool registry cannot prepare the repaired invocation"
                )
            prepared = prepare_call(
                tool_name,
                arguments,
                boundary.session_id,
                provider_call_id,
                execution_context=context,
            )
            if prepared.catalog_snapshot_ref not in {
                "",
                context.capability_snapshot_ref,
            }:
                error_code = "capability_retry_snapshot_drift"
                raise RuntimeError(
                    "repaired tool capability snapshot ref drifted"
                )
            prepared = replace(
                prepared,
                catalog_snapshot_ref=context.capability_snapshot_ref,
            )
            if (
                fingerprint_json(prepared.arguments_json())
                != str(pending.get("args_hash") or "")
            ):
                error_code = "capability_retry_args_changed"
                raise RuntimeError(
                    "re-preparing the repaired tool changed its canonical arguments"
                )
            if prepared.tool_spec_fingerprint == str(
                pending.get("failed_tool_spec_fingerprint") or ""
            ):
                error_code = "capability_retry_spec_unchanged"
                raise RuntimeError(
                    "capability repair did not activate a new immutable ToolSpec"
                )
            prepared = replace(
                prepared, retry_of_effect_id=retry_of_effect_id
            )
        except KeyError as exc:
            error_code = error_code or "capability_retry_tool_not_found"
            error_message = str(exc)
        except Exception as exc:  # noqa: BLE001 - converted to trusted failure
            error_code = error_code or "capability_retry_prepare_failed"
            error_message = f"{type(exc).__name__}: {exc}"

        if prepared is not None and context is not None:
            return ReactToolBatch(
                command_id=command_id,
                calls=(prepared,),
                contexts=(context,),
                canonical_messages=canonical_messages,
                iteration=boundary.iteration + 1,
                feedback_state=feedback,
                provider_call_order=(provider_call_id,),
            )

        raw_failure = dict(
            AgentLoopCollaborator._raw_failure(
                run_id=boundary.run_id,
                tool_call=synthetic_call,
                call_order=0,
                source_kind="tool_prepare",
                error_code=error_code,
                message=error_message,
            )
        )
        raw_failure["source_identity"] = (
            f"capability_repair_retry:{receipt_ref}"
        )
        return ReactToolBatch(
            command_id=command_id,
            calls=(),
            contexts=(),
            canonical_messages=canonical_messages,
            iteration=boundary.iteration + 1,
            feedback_state=feedback,
            raw_failures=(raw_failure,),
            provider_call_order=(provider_call_id,),
        )
