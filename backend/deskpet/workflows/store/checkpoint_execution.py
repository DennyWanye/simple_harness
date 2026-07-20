"""Generic execution-ledger projection for native checkpoint transactions.

Every method receives the checkpointer's current SQLite connection.  This
module deliberately has no connection factory and never commits or rolls back.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import aiosqlite

from deskpet.execution.contracts import (
    DeliveryPolicy,
    DeliverySpec,
    OutcomeStatus,
    canonical_json,
    stable_delivery_id,
    stable_event_id,
)


class CheckpointExecutionError(RuntimeError):
    """Stable failure raised before a generic checkpoint projection can commit."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SqliteCheckpointExecutionAdapter:
    """Write generic decisions/effects/events/final state on the caller's tx."""

    _TERMINAL_EFFECTS = frozenset(
        {"succeeded", "failed", "unknown", "cancelled", "late_reconciled"}
    )
    _DECISION_KINDS = frozenset(
        {"permission", "plan", "clarification", "ppt_outline", "skill_candidate"}
    )

    def __init__(
        self,
        *,
        fault_injector: Callable[[str], None | Awaitable[None]] | None = None,
    ) -> None:
        self._fault_injector = fault_injector

    async def _fault(self, stage: str) -> None:
        if self._fault_injector is None:
            return
        result = self._fault_injector(stage)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _stable_id(*parts: object) -> str:
        return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()

    @staticmethod
    async def _execution_run(
        db: aiosqlite.Connection, run_id: str
    ) -> aiosqlite.Row:
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

    async def consume_decisions(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        decisions: Sequence[str | Mapping[str, Any]],
        checkpoint_id: str,
        now: float,
    ) -> list[str]:
        await self._execution_run(db, run_id)
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
                (
                    now,
                    checkpoint_id,
                    decision_id,
                    run_id,
                    expected_version,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise CheckpointExecutionError(
                    "stale_decision", "generic decision changed while consumed"
                )
            response = (
                json.loads(str(row["response_json"]))
                if row["response_json"] is not None else {}
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
                if signal is None or any(
                    str(signal[key]) != str(child.get(field) or "")
                    for key, field in (
                        ("command_id", "command_id"),
                        ("child_run_id", "child_run_id"),
                    )
                ) or f"child_{signal['kind']}" != str(child.get("kind") or ""):
                    raise CheckpointExecutionError(
                        "workflow_child_signal_conflict",
                        "Native checkpoint child response differs from its durable inbox",
                    )
                if str(signal["kind"]) == "terminal":
                    terminal = json.loads(str(signal["payload_json"]))
                    if terminal.get("status") != child.get("status") or terminal.get("value") != child.get("value"):
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
        await self._fault("generic_decisions.after_write")
        return consumed

    async def open_decision(
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
        await self._execution_run(db, run_id)
        decision_id = self._stable_id("execution-decision", run_id, interrupt_id)
        normalized_kind = str(kind).strip().lower()
        if normalized_kind not in self._DECISION_KINDS:
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
        await self._fault("generic_decision_open.after_write")
        return {
            "decision_id": str(row["decision_id"]),
            "kind": str(row["kind"]),
            "nonce": str(row["nonce"]),
            "version": int(row["decision_version"]),
        }

    @staticmethod
    def _outcome(event_type: str, payload: Mapping[str, Any]) -> OutcomeStatus:
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
    async def _delivery_specs(
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
                raw.extend(
                    (
                        {"channel": "session_message", "target_id": str(ref["session_id"])},
                        {"channel": "websocket", "target_id": str(ref["session_id"])},
                    )
                )
                business_channel = str(intent.get("channel") or "").strip().lower()
                if business_channel in {"artifact", "receipt"}:
                    raw.append(
                        {"channel": business_channel, "target_id": str(ref["session_id"])}
                    )
                if business_channel == "final":
                    raw.append({"channel": "receipt", "target_id": str(ref["session_id"])})
        result: list[DeliverySpec] = []
        for delivery in raw:
            channel = str(delivery["channel"]).strip().lower()
            target_id = str(delivery["target_id"]).strip()
            policy = (
                DeliveryPolicy.DURABLE_REQUIRED
                if delivery.get("required_durable") is True
                else DeliveryPolicy.RETRY_WHILE_BOUND
            )
            result.append(
                DeliverySpec(
                    sink_kind=channel,
                    sink_instance="workflow",
                    target_id=target_id,
                    policy=policy,
                )
            )
        identities = {
            (item.sink_kind, item.sink_instance, item.target_id) for item in result
        }
        if len(identities) != len(result):
            raise CheckpointExecutionError(
                "duplicate_delivery", "generic intent contains duplicate deliveries"
            )
        return tuple(result)

    async def materialize_intent(
        self,
        db: aiosqlite.Connection,
        *,
        run: Mapping[str, Any],
        intent: Mapping[str, Any],
        now: float,
    ) -> str:
        run_id = str(run["run_id"])
        await self._execution_run(db, run_id)
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
        outcome = self._outcome(event_type, payload)
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
        deliveries = await self._delivery_specs(db, run_id=run_id, intent=intent)
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
        await self._fault("generic_event.after_write")
        return event_id

    async def link_effects(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        links: Sequence[Mapping[str, Any]],
        now: float,
    ) -> None:
        await self._execution_run(db, run_id)
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
            if effect is None or str(effect["status"]) not in self._TERMINAL_EFFECTS:
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
                (
                    run_id,
                    node_execution_id,
                    effect_id,
                    checkpoint_ns,
                    checkpoint_id,
                    now,
                ),
            )
        await self._fault("generic_effect_links.after_write")

    async def finalize_run(
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
        execution = await self._execution_run(db, run_id)
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
            terminal_event_id = await self.materialize_intent(
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
            execution = await self._execution_run(db, run_id)
        if str(execution["status"]) in {"completed", "failed", "cancelled"}:
            if (
                str(execution["status"]) != terminal_status
                or str(execution["terminal_event_id"] or "") != terminal_event_id
            ):
                raise CheckpointExecutionError(
                    "terminal_conflict", "another generic terminal intent already won"
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
        await self._fault("generic_final.after_write")
        return terminal_event_id


__all__ = ["CheckpointExecutionError", "SqliteCheckpointExecutionAdapter"]
