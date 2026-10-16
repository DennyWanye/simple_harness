"""Workflow run execution, decisions consumed by workflows and workflow finalisation."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from deskpet.execution.contracts import (
    AdmissionLaunchClaim,
    AdmissionPhase,
    DecisionConflict,
    DeliveryPolicy,
    DeliverySpec,
    IdempotencyConflict,
    OutcomeStatus,
    PersistenceLevel,
    PersistenceRequired,
    RunCreate,
    RunEventCandidate,
    RunIdentityConflict,
    RunNotFound,
    RunRecord,
    RunStartSnapshotRecord,
    VersionConflict,
    WorkflowRunSeed,
    WorkflowStartResult,
    canonical_json,
    fingerprint_json,
    stable_delivery_id,
    stable_event_id,
    thaw_json,
    validate_json_value,
)

from .core import ExecutionUowCore
from .support import CheckpointExecutionError


class WorkflowRunMixin(ExecutionUowCore):
    """Workflow run execution, decisions consumed by workflows and workflow finalisation."""

    __slots__ = ()

    async def read_workflow_resume_payload(
        self,
        run_id: str,
    ) -> Mapping[str, JsonValue]:
        """Read resolved, unconsumed Native workflow decisions for crash recovery."""

        async with self._read_connection() as db:
            run = await (
                await db.execute(
                    "SELECT driver_kind,status FROM execution_runs WHERE run_id=?",
                    (run_id,),
                )
            ).fetchone()
            if run is None:
                raise RunNotFound(
                    "run_not_found",
                    f"execution run does not exist: {run_id}",
                )
            if str(run["driver_kind"]) != "workflow":
                raise CheckpointExecutionError(
                    "execution_driver_conflict",
                    "workflow resume payload cannot read another driver",
                )
            rows = await (
                await db.execute(
                    """SELECT nonce,response_json FROM execution_decisions
                    WHERE run_id=? AND status IN ('allowed','denied')
                    AND consumed_at IS NULL
                    ORDER BY resolved_at,decision_id""",
                    (run_id,),
                )
            ).fetchall()
        payload: dict[str, JsonValue] = {}
        for row in rows:
            response = (
                json.loads(str(row["response_json"])) if row["response_json"] is not None else {}
            )
            if not isinstance(response, dict):
                raise CheckpointExecutionError(
                    "decision_response_invalid",
                    "workflow decision response must be an object",
                )
            validate_json_value(response, path="$.workflow_resume")
            payload[str(row["nonce"])] = response
        return payload

    @staticmethod
    async def _workflow_execution_run_tx(db: aiosqlite.Connection, run_id: str) -> aiosqlite.Row:
        row = await (
            await db.execute("SELECT * FROM execution_runs WHERE run_id=?", (run_id,))
        ).fetchone()
        if row is None:
            raise CheckpointExecutionError(
                "execution_run_not_found", f"generic execution run does not exist: {run_id}"
            )
        if str(row["driver_kind"]) != "workflow":
            raise CheckpointExecutionError(
                "execution_driver_conflict", "workflow checkpoint cannot mutate another driver"
            )
        return row

    @staticmethod
    async def _mark_workflow_running_on_claim_tx(
        db: aiosqlite.Connection,
        *,
        run_id: str,
        now: float,
    ) -> bool:
        """Synchronize the generic Run while the native lease claim is open.

        Returning ``False`` lets the Workflow store preserve its existing
        ``StaleRunFence`` boundary without teaching that store execution SQL.
        """

        row = await (
            await db.execute(
                "SELECT driver_kind FROM execution_runs WHERE run_id=?",
                (run_id,),
            )
        ).fetchone()
        if row is None or str(row["driver_kind"]) != "workflow":
            return False
        cursor = await db.execute(
            """UPDATE execution_runs
            SET status='running',
                started_at=COALESCE(started_at,?),
                version=version+CASE
                    WHEN status!='running' OR started_at IS NULL THEN 1
                    ELSE 0
                END,
                updated_at=CASE
                    WHEN status!='running' OR started_at IS NULL THEN ?
                    ELSE updated_at
                END
            WHERE run_id=? AND terminal_event_id IS NULL
              AND status IN ('created','queued','running','waiting')""",
            (now, now, run_id),
        )
        return cursor.rowcount == 1

    async def _consume_workflow_decisions_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        decisions: Sequence[str | Mapping[str, Any]],
        checkpoint_id: str,
        now: float,
    ) -> list[str]:
        await self._workflow_execution_run_tx(db, run_id)
        consumed: list[str] = []
        for item in decisions:
            decision_id = str(item if isinstance(item, str) else item["decision_id"])
            expected_version = None if isinstance(item, str) else item.get("expected_version")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=? AND run_id=?",
                    (decision_id, run_id),
                )
            ).fetchone()
            if row is None and isinstance(item, str):
                row = await (
                    await db.execute(
                        "SELECT * FROM execution_decisions WHERE nonce=? AND run_id=?",
                        (decision_id, run_id),
                    )
                ).fetchone()
                if row is not None:
                    decision_id = str(row["decision_id"])
            if row is None:
                raise CheckpointExecutionError(
                    "decision_not_found", "generic resume decision was not found"
                )
            if row["consumed_at"] is not None:
                if str(row["consumed_checkpoint_id"] or "") != checkpoint_id:
                    raise CheckpointExecutionError(
                        "decision_already_consumed", "decision belongs to another checkpoint"
                    )
                consumed.append(decision_id)
                continue
            if str(row["status"]) not in {"allowed", "denied"}:
                raise CheckpointExecutionError(
                    "decision_not_resolved", "generic decision is not resumable"
                )
            if expected_version is not None and int(row["decision_version"]) != int(
                expected_version
            ):
                raise CheckpointExecutionError("stale_decision", "decision version changed")
            cursor = await db.execute(
                """UPDATE execution_decisions SET consumed_at=?,consumed_checkpoint_id=?,
                decision_version=decision_version+1 WHERE decision_id=? AND run_id=?
                AND status IN ('allowed','denied') AND consumed_at IS NULL
                AND (? IS NULL OR decision_version=?)""",
                (now, checkpoint_id, decision_id, run_id, expected_version, expected_version),
            )
            if cursor.rowcount != 1:
                raise CheckpointExecutionError(
                    "stale_decision", "generic decision changed while consumed"
                )
            response = (
                json.loads(str(row["response_json"])) if row["response_json"] is not None else {}
            )
            child = response.get("child_signal") if isinstance(response, Mapping) else None
            if isinstance(child, Mapping):
                signal_id = str(child.get("signal_id") or "")
                signal = await (
                    await db.execute(
                        """SELECT * FROM execution_child_signal_inbox
                        WHERE signal_id=? AND parent_run_id=?""",
                        (signal_id, run_id),
                    )
                ).fetchone()
                if (
                    signal is None
                    or any(
                        str(signal[key]) != str(child.get(field) or "")
                        for key, field in (
                            ("command_id", "command_id"),
                            ("child_run_id", "child_run_id"),
                        )
                    )
                    or f"child_{signal['kind']}" != str(child.get("kind") or "")
                ):
                    raise CheckpointExecutionError(
                        "workflow_child_signal_conflict",
                        "Native checkpoint child response differs from its durable inbox",
                    )
                if str(signal["kind"]) == "terminal":
                    terminal = json.loads(str(signal["payload_json"]))
                    if terminal.get("status") != child.get("status") or terminal.get(
                        "value"
                    ) != child.get("value"):
                        raise CheckpointExecutionError(
                            "workflow_child_signal_conflict",
                            "Native checkpoint child terminal payload differs from its inbox",
                        )
                await db.execute(
                    """UPDATE execution_child_signal_inbox SET delivered_at=?,updated_at=?
                    WHERE signal_id=? AND delivered_at IS NULL""",
                    (now, now, signal_id),
                )
            consumed.append(decision_id)
        self._fault("checkpoint_consume_decisions_after_write")
        return consumed

    async def _open_workflow_decision_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        interrupt_id: str,
        checkpoint_id: str,
        task_id: str | None,
        kind: str,
        prompt: object,
        expires_at: float | None,
        now: float,
    ) -> Mapping[str, Any]:
        run_id = str(run["run_id"])
        await self._workflow_execution_run_tx(db, run_id)
        decision_id = self._checkpoint_stable_id("execution-decision", run_id, interrupt_id)
        normalized_kind = str(kind).strip().lower()
        if normalized_kind not in self._WORKFLOW_DECISION_KINDS:
            normalized_kind = "workflow_hitl"
        prompt_json = canonical_json(
            {
                "interrupt_id": interrupt_id,
                "checkpoint_id": checkpoint_id,
                "task_id": task_id,
                "prompt": copy.deepcopy(prompt),
            }
        )
        existing = await (
            await db.execute(
                "SELECT * FROM execution_decisions WHERE run_id=? AND nonce=?",
                (run_id, interrupt_id),
            )
        ).fetchone()
        if existing is None:
            await db.execute(
                """INSERT INTO execution_decisions(
                decision_id,schema_version,run_id,nonce,kind,status,prompt_schema_version,
                prompt_json,decision_version,expires_at,created_at
                ) VALUES(?,1,?,?,?,'open',1,?,0,?,?)""",
                (
                    decision_id,
                    run_id,
                    interrupt_id,
                    normalized_kind,
                    prompt_json,
                    expires_at,
                    now,
                ),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM execution_decisions WHERE decision_id=?", (decision_id,)
                )
            ).fetchone()
            assert row is not None
        else:
            row = existing
            if (
                str(row["decision_id"]) != decision_id
                or str(row["kind"]) != normalized_kind
                or str(row["prompt_json"]) != prompt_json
            ):
                raise CheckpointExecutionError(
                    "decision_intent_conflict", "interrupt nonce names another decision"
                )
        cursor = await db.execute(
            """UPDATE execution_runs SET status='waiting',version=version+1,updated_at=?
            WHERE run_id=? AND terminal_event_id IS NULL
            AND status IN ('created','queued','running','waiting')""",
            (now, run_id),
        )
        if cursor.rowcount != 1:
            raise CheckpointExecutionError(
                "execution_waiting_conflict", "generic execution cannot enter waiting"
            )
        self._fault("checkpoint_open_decision_after_write")
        return {
            "decision_id": str(row["decision_id"]),
            "kind": str(row["kind"]),
            "nonce": str(row["nonce"]),
            "version": int(row["decision_version"]),
        }

    @staticmethod
    def _workflow_outcome(event_type: str, payload: Mapping[str, Any]) -> OutcomeStatus:
        kind = str(payload.get("kind") or "").lower()
        status = str(payload.get("status") or "").lower()
        if event_type == "workflow.accepted" or kind == "accepted":
            return OutcomeStatus.ACCEPTED
        if event_type == "workflow.final" or kind == "final":
            return {
                "completed": OutcomeStatus.SUCCEEDED,
                "succeeded": OutcomeStatus.SUCCEEDED,
                "failed": OutcomeStatus.FAILED,
                "cancelled": OutcomeStatus.CANCELLED,
            }.get(status, OutcomeStatus.UNKNOWN)
        if event_type in {"workflow.decision", "workflow.progress"} or kind in {
            "decision",
            "progress",
        }:
            return OutcomeStatus.WAITING
        if status == "cancel_requested":
            return OutcomeStatus.CANCEL_REQUESTED
        return OutcomeStatus.UNKNOWN

    @staticmethod
    async def _workflow_delivery_specs_tx(
        db: aiosqlite.Connection,
        *,
        run_id: str,
        intent: Mapping[str, Any],
    ) -> tuple[DeliverySpec, ...]:
        raw = list(intent.get("deliveries") or [])
        structured = list(intent.get("delivery_specs") or [])
        if structured:
            ref = await (
                await db.execute(
                    """SELECT session_id FROM workflow_session_refs WHERE run_id=?
                    AND session_kind='delivery' AND deleted_at IS NULL""",
                    (run_id,),
                )
            ).fetchone()
            if ref is None:
                raise CheckpointExecutionError(
                    "delivery_session_not_bound", "delivery specs require a delivery binding"
                )
            for item in structured:
                if not isinstance(item, Mapping) or not str(item.get("channel") or "").strip():
                    raise CheckpointExecutionError(
                        "invalid_delivery_intent", "delivery specs require a channel"
                    )
                raw.append(
                    {
                        "channel": str(item["channel"]),
                        "target_id": str(ref["session_id"]),
                        "required_durable": item.get("required_durable") is True,
                    }
                )
        elif not raw:
            ref = await (
                await db.execute(
                    """SELECT session_id FROM workflow_session_refs WHERE run_id=?
                    AND session_kind='delivery' AND deleted_at IS NULL""",
                    (run_id,),
                )
            ).fetchone()
            if ref is not None:
                business_channel = str(intent.get("channel") or "").strip().lower()
                target_id = str(ref["session_id"])
                if business_channel in {"artifact", "artifact_message"}:
                    raw.extend(
                        (
                            {
                                "channel": "artifact",
                                "target_id": target_id,
                                "required_durable": True,
                            },
                            {"channel": "websocket", "target_id": target_id},
                        )
                    )
                elif business_channel == "workflow_report":
                    raw.append({"channel": "websocket", "target_id": target_id})
                elif business_channel in {"receipt", "receipt_jsonl"}:
                    raw.append(
                        {"channel": "receipt", "target_id": target_id, "required_durable": True}
                    )
                else:
                    raw.extend(
                        (
                            {
                                "channel": "session_message",
                                "target_id": target_id,
                                "required_durable": True,
                            },
                            {"channel": "websocket", "target_id": target_id},
                        )
                    )
                    if business_channel == "final":
                        raw.append(
                            {"channel": "receipt", "target_id": target_id, "required_durable": True}
                        )
        result: list[DeliverySpec] = []
        for delivery in raw:
            policy = (
                DeliveryPolicy.DURABLE_REQUIRED
                if delivery.get("required_durable") is True
                else DeliveryPolicy.RETRY_WHILE_BOUND
            )
            result.append(
                DeliverySpec(
                    sink_kind=str(delivery["channel"]).strip().lower(),
                    sink_instance="workflow",
                    target_id=str(delivery["target_id"]).strip(),
                    policy=policy,
                )
            )
        identities = {(item.sink_kind, item.sink_instance, item.target_id) for item in result}
        if len(identities) != len(result):
            raise CheckpointExecutionError(
                "duplicate_delivery", "generic intent contains duplicate deliveries"
            )
        return tuple(result)

    async def _append_workflow_event_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        intent: Mapping[str, Any],
        now: float,
    ) -> str:
        run_id = str(run["run_id"])
        await self._workflow_execution_run_tx(db, run_id)
        intent_id = str(intent.get("intent_id") or "").strip()
        event_type = str(intent.get("event_type") or "").strip()
        if not intent_id or not event_type:
            raise CheckpointExecutionError(
                "invalid_delivery_intent", "intent_id and event_type are required"
            )
        event_key = str(intent.get("event_key") or f"intent:{intent_id}").strip()
        payload = copy.deepcopy(dict(intent.get("payload") or {}))
        payload.update(
            {
                "run_id": run_id,
                "request_id": run["request_id"],
                "turn_id": run["turn_id"],
                "workflow_name": run["workflow_name"],
                "workflow_version": run["workflow_version"],
            }
        )
        outcome = self._workflow_outcome(event_type, payload)
        event_id = stable_event_id(run_id, event_key)
        correlation = canonical_json(
            {
                "request_id": run["request_id"],
                "turn_id": run["turn_id"],
                "workflow_name": run["workflow_name"],
                "workflow_version": run["workflow_version"],
            }
        )
        payload_json = canonical_json(payload)
        error = payload.get("error")
        error_json = canonical_json(dict(error)) if isinstance(error, Mapping) else None
        artifact_refs = list(payload.get("artifact_refs") or [])
        if payload.get("manifest_ref") and str(payload["manifest_ref"]) not in artifact_refs:
            artifact_refs.append(str(payload["manifest_ref"]))
        artifact_refs_json = canonical_json(artifact_refs)
        deliveries = await self._workflow_delivery_specs_tx(db, run_id=run_id, intent=intent)
        existing = await (
            await db.execute(
                "SELECT * FROM execution_events WHERE run_id=? AND event_key=?",
                (run_id, event_key),
            )
        ).fetchone()
        if existing is None:
            seq_row = await (
                await db.execute(
                    """UPDATE execution_runs SET durable_seq=durable_seq+1,
                    version=version+1,updated_at=? WHERE run_id=? AND terminal_event_id IS NULL
                    RETURNING durable_seq""",
                    (now, run_id),
                )
            ).fetchone()
            if seq_row is None:
                raise CheckpointExecutionError(
                    "execution_run_terminal", "terminal execution cannot accept another event"
                )
            await db.execute(
                """INSERT INTO execution_events(
                event_id,schema_version,event_key,run_id,durable_seq,kind,status,driver_kind,
                correlation_json,payload_json,error_json,artifact_refs_json,created_at
                ) VALUES(?,1,?,?,?,?,?,'workflow',?,?,?,?,?)""",
                (
                    event_id,
                    event_key,
                    run_id,
                    int(seq_row["durable_seq"]),
                    event_type,
                    outcome.value,
                    correlation,
                    payload_json,
                    error_json,
                    artifact_refs_json,
                    now,
                ),
            )
        else:
            expected = {
                "event_id": event_id,
                "kind": event_type,
                "status": outcome.value,
                "driver_kind": "workflow",
                "correlation_json": correlation,
                "payload_json": payload_json,
                "error_json": error_json,
                "artifact_refs_json": artifact_refs_json,
            }
            if any(existing[key] != value for key, value in expected.items()):
                raise CheckpointExecutionError(
                    "event_intent_conflict", "event key names different generic content"
                )
        for delivery in deliveries:
            delivery_id = stable_delivery_id(event_id, delivery)
            await db.execute(
                """INSERT INTO execution_deliveries(
                delivery_id,schema_version,event_id,run_id,sink_kind,sink_instance,target_id,
                policy,status,created_at,updated_at
                ) VALUES(?,1,?,?,?,?,?,?,'pending',?,?)
                ON CONFLICT(event_id,sink_kind,sink_instance,target_id) DO NOTHING""",
                (
                    delivery_id,
                    event_id,
                    run_id,
                    delivery.sink_kind,
                    delivery.sink_instance,
                    delivery.target_id,
                    delivery.policy.value,
                    now,
                    now,
                ),
            )
        self._fault("checkpoint_append_event_after_write")
        return event_id

    async def _link_workflow_effects_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        links: Sequence[Mapping[str, Any]],
        now: float,
    ) -> None:
        await self._workflow_execution_run_tx(db, run_id)
        for link in links:
            effect_id = str(link.get("effect_id") or "").strip()
            node_execution_id = str(link.get("node_execution_id") or "").strip()
            if not effect_id:
                raise CheckpointExecutionError(
                    "invalid_effect_link", "generic effect link requires effect_id"
                )
            effect = await (
                await db.execute(
                    "SELECT status FROM execution_effects WHERE effect_id=? AND run_id=?",
                    (effect_id, run_id),
                )
            ).fetchone()
            if effect is None or str(effect["status"]) not in self._WORKFLOW_TERMINAL_EFFECTS:
                raise CheckpointExecutionError(
                    "effect_not_committed", "only a committed generic effect may be checkpointed"
                )
            grant = await (
                await db.execute(
                    "SELECT status FROM execution_grants WHERE run_id=? AND effect_id=?",
                    (run_id, effect_id),
                )
            ).fetchone()
            if grant is not None and str(grant["status"]) != "consumed":
                raise CheckpointExecutionError(
                    "effect_grant_not_consumed", "effect authorization is not consumed"
                )
            await db.execute(
                """INSERT OR IGNORE INTO execution_effect_links(
                run_id,node_execution_id,effect_id,checkpoint_ns,checkpoint_id,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (run_id, node_execution_id, effect_id, checkpoint_ns, checkpoint_id, now),
            )
        self._fault("checkpoint_link_effects_after_write")

    async def _finalize_workflow_run_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        terminal_status: str,
        terminal_error: Mapping[str, Any] | None,
        recovery_action: str | None,
        event_ids: Sequence[str],
        now: float,
    ) -> str:
        run_id = str(run["run_id"])
        execution = await self._workflow_execution_run_tx(db, run_id)
        expected_outcome = {
            "completed": "succeeded",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(terminal_status)
        if expected_outcome is None:
            raise CheckpointExecutionError(
                "invalid_terminal_status", f"unsupported generic terminal status: {terminal_status}"
            )
        terminal_event_id: str | None = None
        for event_id in reversed(tuple(event_ids)):
            event = await (
                await db.execute(
                    "SELECT status FROM execution_events WHERE event_id=? AND run_id=?",
                    (event_id, run_id),
                )
            ).fetchone()
            if event is not None and str(event["status"]) == expected_outcome:
                terminal_event_id = str(event_id)
                break
        if terminal_event_id is None:
            terminal_event_id = await self._append_workflow_event_tx(
                db,
                run=run,
                intent={
                    "intent_id": f"{run_id}:run-final",
                    "event_key": "run:terminal",
                    "event_type": "workflow.final",
                    "channel": "final",
                    "payload": {
                        "kind": "final",
                        "status": terminal_status,
                        "error": copy.deepcopy(terminal_error),
                        "recovery_action": recovery_action,
                    },
                },
                now=now,
            )
            execution = await self._workflow_execution_run_tx(db, run_id)
        artifact_items: list[dict[str, Any]] = []
        artifact_refs: list[str] = []
        if terminal_status == "completed" and str(run["workflow_name"] or "") == "durable_task":
            effect_rows = await (
                await db.execute(
                    """SELECT effect_id,prepared_json,outcome_json,artifact_refs_json
                    FROM workflow_effects
                    WHERE run_id=? AND status='committed'
                    AND artifact_refs_json!='[]'
                    ORDER BY ended_at,effect_id""",
                    (run_id,),
                )
            ).fetchall()
            seen_artifacts: set[tuple[str, str]] = set()
            for effect_row in effect_rows:
                try:
                    refs = [str(item) for item in json.loads(str(effect_row["artifact_refs_json"]))]
                    outcome_payload = json.loads(str(effect_row["outcome_json"]))
                    prepared_payload = json.loads(str(effect_row["prepared_json"]))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise CheckpointExecutionError(
                        "artifact_effect_invalid",
                        "committed workflow artifact metadata is not valid JSON",
                    ) from exc
                tool_name = (
                    str(prepared_payload.get("tool_name") or "")
                    if isinstance(prepared_payload, Mapping)
                    else ""
                )
                value = (
                    outcome_payload.get("value") if isinstance(outcome_payload, Mapping) else None
                )
                raw_artifacts = value.get("artifacts") if isinstance(value, Mapping) else None
                if refs and not isinstance(raw_artifacts, list):
                    if tool_name == "register_artifacts":
                        raise CheckpointExecutionError(
                            "artifact_effect_payload_missing",
                            "register_artifacts refs require an artifacts outcome envelope",
                        )
                    # Some workflow effects own input/dependency blobs. They
                    # are durable evidence, not user-facing output artifacts.
                    continue
                registered_digests: set[str] = set()
                for raw_artifact in raw_artifacts or ():
                    if not isinstance(raw_artifact, Mapping):
                        raise CheckpointExecutionError(
                            "artifact_effect_payload_invalid",
                            "workflow artifact entries must be objects",
                        )
                    item = copy.deepcopy(dict(raw_artifact))
                    digest = str(item.get("sha256") or "").strip()
                    path = str(item.get("path") or "").strip()
                    if not digest:
                        if tool_name == "register_artifacts":
                            raise CheckpointExecutionError(
                                "artifact_effect_payload_invalid",
                                "register_artifacts entries require a sha256 digest",
                            )
                        # write_file and other producer tools may publish a
                        # provisional UI envelope while the receipt digest is
                        # owned only by prepared metadata.  It is not a final
                        # registered artifact and must not poison terminal
                        # aggregation.
                        continue
                    if digest not in refs:
                        raise CheckpointExecutionError(
                            "artifact_effect_ref_mismatch",
                            "workflow artifact digest is absent from its committed refs",
                        )
                    registered_digests.add(digest)
                    if digest not in artifact_refs:
                        artifact_refs.append(digest)
                    identity = (digest, path)
                    if identity not in seen_artifacts:
                        seen_artifacts.add(identity)
                        artifact_items.append(item)
                if tool_name == "register_artifacts" and set(refs) != registered_digests:
                    raise CheckpointExecutionError(
                        "artifact_effect_ref_mismatch",
                        "register_artifacts refs and artifact digests must match exactly",
                    )
            if artifact_items:
                await self._append_workflow_event_tx(
                    db,
                    run=run,
                    intent={
                        "intent_id": f"{run_id}:registered-artifacts",
                        "event_key": "workflow:registered-artifacts",
                        "event_type": "workflow.artifact_card",
                        "channel": "artifact",
                        "payload": {
                            "kind": "artifact_card",
                            "status": "completed",
                            "tool": "register_artifacts",
                            "text": (f"Registered {len(artifact_items)} workflow artifact(s)."),
                            "artifacts": artifact_items,
                            "artifact_refs": artifact_refs,
                        },
                    },
                    now=now,
                )
        child_command = await (
            await db.execute(
                """SELECT operation_id FROM execution_child_commands
                WHERE child_run_id=?""",
                (run_id,),
            )
        ).fetchone()
        parent_signal_value: dict[str, Any] = {
            "run_id": run_id,
            "status": terminal_status,
            "terminal_event_id": terminal_event_id,
            "error": copy.deepcopy(terminal_error),
            "recovery_action": recovery_action,
            "artifacts": artifact_items,
            "artifact_refs": artifact_refs,
        }
        for event_kind, value_key in (
            ("workflow.final_assistant", "final_assistant"),
            ("workflow.workflow_report", "workflow_report"),
        ):
            result_event = await (
                await db.execute(
                    """SELECT payload_json FROM execution_events
                    WHERE run_id=? AND kind=? ORDER BY durable_seq DESC LIMIT 1""",
                    (run_id, event_kind),
                )
            ).fetchone()
            if result_event is None:
                continue
            result_payload = json.loads(str(result_event["payload_json"]))
            parent_signal_value[value_key] = copy.deepcopy(
                result_payload.get("payload", result_payload)
            )
        if str(execution["status"]) in {"completed", "failed", "cancelled"}:
            if (
                str(execution["status"]) != terminal_status
                or str(execution["terminal_event_id"] or "") != terminal_event_id
            ):
                raise CheckpointExecutionError(
                    "terminal_conflict", "another generic terminal intent already won"
                )
            if child_command is not None:
                await self._enqueue_child_terminal_signal_tx(
                    db,
                    str(child_command["operation_id"]),
                    terminal_status=terminal_status,
                    value=parent_signal_value,
                )
            return terminal_event_id
        cursor = await db.execute(
            """UPDATE execution_runs SET status=?,terminal_event_id=?,version=version+1,
            updated_at=?,ended_at=? WHERE run_id=? AND terminal_event_id IS NULL
            AND status NOT IN ('completed','failed','cancelled')""",
            (terminal_status, terminal_event_id, now, now, run_id),
        )
        if cursor.rowcount != 1:
            raise CheckpointExecutionError(
                "terminal_conflict", "generic terminal compare-and-set lost"
            )
        if child_command is not None:
            await self._enqueue_child_terminal_signal_tx(
                db,
                str(child_command["operation_id"]),
                terminal_status=terminal_status,
                value=parent_signal_value,
            )
        self._fault("checkpoint_finalize_run_after_write")
        return terminal_event_id

    async def _insert_workflow_tx(
        self,
        db: aiosqlite.Connection,
        *,
        run: RunRecord,
        workflow: WorkflowRunSeed,
    ) -> bool:
        snapshot = thaw_json(workflow.capability_snapshot)
        if fingerprint_json(snapshot) != workflow.capability_hash:
            raise RunIdentityConflict(
                "workflow_capability_conflict",
                "workflow capability hash does not match its immutable snapshot",
            )
        if workflow.trace_id != run.context.trace_id:
            raise RunIdentityConflict(
                "workflow_trace_conflict", "workflow and execution trace ids differ"
            )
        capability_json = canonical_json(snapshot)
        existing_capability = await (
            await db.execute(
                "SELECT snapshot_json FROM workflow_capabilities WHERE capability_hash=?",
                (workflow.capability_hash,),
            )
        ).fetchone()
        if existing_capability is not None:
            if (
                canonical_json(json.loads(str(existing_capability["snapshot_json"])))
                != capability_json
            ):
                raise IdempotencyConflict(
                    "workflow_capability_conflict",
                    "capability hash already names different workflow capability content",
                )
        else:
            await db.execute(
                """INSERT INTO workflow_capabilities(capability_hash,snapshot_json,created_at)
                VALUES(?,?,?)""",
                (workflow.capability_hash, capability_json, self._clock()),
            )
        existing_start = await (
            await db.execute(
                "SELECT * FROM workflow_start_requests WHERE request_key=?",
                (workflow.request_key,),
            )
        ).fetchone()
        if existing_start is not None and str(existing_start["run_id"]) != run.run_id:
            raise IdempotencyConflict(
                "workflow_start_conflict", "workflow request key already names another run"
            )
        existing_run = await (
            await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run.run_id,))
        ).fetchone()
        if existing_run is not None:
            expected = {
                "trace_id": workflow.trace_id,
                "thread_id": workflow.thread_id,
                "checkpoint_ns": workflow.checkpoint_ns,
                "session_id": run.context.session_id,
                "request_id": run.context.request_id,
                "turn_id": run.context.turn_id,
                "workflow_name": workflow.workflow_name,
                "workflow_version": workflow.workflow_version,
                "manifest_hash": workflow.manifest_hash,
                "implementation_hash": workflow.implementation_hash,
                "capability_hash": workflow.capability_hash,
                "state_schema_version": workflow.state_schema_version,
            }
            for field_name, value in expected.items():
                stored = existing_run[field_name]
                stored = int(stored) if field_name == "state_schema_version" else str(stored or "")
                if stored != value:
                    raise IdempotencyConflict(
                        "workflow_start_conflict",
                        f"workflow run differs at {field_name}",
                    )
        else:
            now = float(self._clock())
            await db.execute(
                """INSERT INTO workflow_runs(
                run_id,trace_id,thread_id,checkpoint_ns,parent_run_id,source_checkpoint_id,
                session_id,request_id,turn_id,workflow_name,workflow_version,manifest_hash,
                implementation_hash,capability_hash,state_schema_version,status,
                active_nodes_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'created','[]',?,?)""",
                (
                    run.run_id,
                    workflow.trace_id,
                    workflow.thread_id,
                    workflow.checkpoint_ns,
                    run.context.parent_run_id,
                    workflow.source_checkpoint_id,
                    run.context.session_id,
                    run.context.request_id,
                    run.context.turn_id,
                    workflow.workflow_name,
                    workflow.workflow_version,
                    workflow.manifest_hash,
                    workflow.implementation_hash,
                    workflow.capability_hash,
                    workflow.state_schema_version,
                    now,
                    now,
                ),
            )
        if existing_start is None:
            await db.execute(
                """INSERT INTO workflow_start_requests(
                request_key,session_id,request_id,turn_id,workflow_name,
                capability_hash,run_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    workflow.request_key,
                    run.context.session_id,
                    run.context.request_id,
                    run.context.turn_id,
                    workflow.workflow_name,
                    workflow.capability_hash,
                    run.run_id,
                    self._clock(),
                ),
            )
        for ref in workflow.session_refs:
            existing_ref = await (
                await db.execute(
                    """SELECT session_id,session_epoch FROM workflow_session_refs
                    WHERE run_id=? AND session_kind=?""",
                    (run.run_id, ref.session_kind),
                )
            ).fetchone()
            if existing_ref is not None:
                if (
                    str(existing_ref["session_id"]) != ref.session_id
                    or int(existing_ref["session_epoch"]) != ref.session_epoch
                ):
                    raise IdempotencyConflict(
                        "workflow_session_ref_conflict",
                        "workflow session kind already names another session fence",
                    )
                continue
            await db.execute(
                """INSERT INTO workflow_session_refs(
                run_id,session_kind,session_id,session_epoch,deleted_at
                ) VALUES(?,?,?,?,NULL)""",
                (run.run_id, ref.session_kind, ref.session_id, ref.session_epoch),
            )
        return existing_run is None

    async def start_workflow(
        self,
        spec: RunCreate,
        workflow: WorkflowRunSeed,
        *,
        start_snapshot: RunStartSnapshotRecord | None = None,
        start_commit_extensions: Sequence[Any] = (),
        association_event: RunEventCandidate | None = None,
        accepted_event: RunEventCandidate | None = None,
        deliveries: Sequence[DeliverySpec] = (),
        admission_launch: AdmissionLaunchClaim | None = None,
    ) -> WorkflowStartResult:
        if spec.persistence_level is not PersistenceLevel.DURABLE:
            raise PersistenceRequired(
                "workflow_must_be_durable", "workflow runs are durable from creation"
            )
        if accepted_event is None and deliveries:
            raise IdempotencyConflict(
                "delivery_without_event", "workflow deliveries require an accepted event"
            )
        if accepted_event is not None and accepted_event.status != OutcomeStatus.ACCEPTED:
            raise IdempotencyConflict(
                "invalid_accepted_event", "workflow start event must be accepted"
            )
        async with self._write_transaction() as db:
            row, created = await self._insert_run_tx(db, spec, version=0)
            if start_snapshot is not None:
                if start_snapshot.run_id != spec.run_id:
                    raise IdempotencyConflict(
                        "run_start_scope_conflict",
                        "workflow start snapshot belongs to another Run",
                    )
                await self._ensure_start_snapshot_tx(
                    db,
                    spec=spec,
                    start_snapshot=start_snapshot,
                    run_created=created,
                    start_commit_extensions=start_commit_extensions,
                )
            elif start_commit_extensions:
                raise IdempotencyConflict(
                    "start_extension_without_snapshot",
                    "workflow start extensions require a start snapshot",
                )
            record = self._row_to_record(row)
            self._fault("start_workflow_after_execution")
            if admission_launch is not None:
                self._fault("batch_boundary_after_promotion")
            workflow_created = await self._insert_workflow_tx(db, run=record, workflow=workflow)
            self._fault("start_workflow_after_workflow")
            admission_consumed = created
            if admission_launch is not None:
                await self._assert_recovery_fence_tx(
                    db, admission_launch.recovery_lease, run_id=spec.run_id
                )
                continuation, boundary = await self._admission_tx(db, spec.run_id)
                if workflow_created:
                    if boundary != admission_launch.boundary:
                        raise VersionConflict(
                            "stale_admission_version", "workflow launch claim differs"
                        )
                    launched = self._advance_admission(
                        boundary, AdmissionPhase.LAUNCHED, continuation.version + 1
                    )
                    await self._save_continuation_tx(
                        db,
                        run=row,
                        expected_version=continuation.version,
                        payload_json=self._continuation_payload_json(
                            self._with_admission(continuation.payload, launched)
                        ),
                        decision=None,
                        now=float(self._clock()),
                    )
                    admission_consumed = True
                    self._fault("batch_boundary_after_continuation")
                elif boundary.phase is not AdmissionPhase.LAUNCHED:
                    raise IdempotencyConflict(
                        "workflow_start_conflict", "workflow replay has unconsumed admission"
                    )
            elif not created and workflow_created:
                admitted = await (
                    await db.execute(
                        "SELECT 1 FROM execution_continuations WHERE run_id=? AND json_type(pending_prepared_call_json,'$._admission') IS NOT NULL",
                        (spec.run_id,),
                    )
                ).fetchone()
                if admitted is not None:
                    raise DecisionConflict(
                        "admission_required", "an admitted workflow requires its launch claim"
                    )
            if association_event is not None:
                _, row, _ = await self._append_event_tx(
                    db,
                    row,
                    expected_version=record.version,
                    event=association_event,
                    deliveries=(),
                )
                record = self._row_to_record(row)
            if accepted_event is not None:
                _, updated, _ = await self._append_event_tx(
                    db,
                    row,
                    expected_version=record.version,
                    event=accepted_event,
                    deliveries=deliveries,
                )
                record = self._row_to_record(updated)
            if admission_launch is not None:
                self._fault("batch_boundary_after_waiting_event")
                self._fault("batch_boundary_before_commit")
            self._fault("start_workflow_before_commit")
            await db.commit()
            return WorkflowStartResult(
                record,
                created,
                workflow_created,
                admission_consumed,
                workflow_created and (created or admission_consumed),
            )
