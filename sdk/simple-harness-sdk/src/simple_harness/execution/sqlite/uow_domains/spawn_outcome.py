# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Workflow spawn continuation outcomes and admission continuation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from simple_harness.contracts import CallId, JsonValue, RunId, canonical_json, thaw_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.execution.contracts.children import AttachmentPolicy
from simple_harness.execution.effects import EffectState
from simple_harness.execution.uow import (
    RUNTIME_LEASE_NAMESPACE,
    ExecutionLease,
    FaultHook,
    RunState,
    UnitOfWorkConflict,
)
from simple_harness.runtime.orchestration import (
    RuntimeStartAdmission,
    RuntimeStartDisposition,
    WorkflowLaunchTicket,
    WorkflowSpawnContinuationClaim,
)
from simple_harness.runtime.start_snapshot import StartSnapshot
from simple_harness.runtime.workflow_spawn import (
    ChildStartDispatchRef,
    WorkflowChildWaitBinding,
    WorkflowSpawnChildControlKind,
    _create_workflow_spawn_admission_outcome,
    _create_workflow_spawn_child_control,
    _create_workflow_spawn_result,
    _create_workflow_spawn_tool_outcome,
)
from simple_harness.tools.contracts import ToolOutcome, ToolResult
from simple_harness.workflow.execution_ports import (
    StartAdmissionRequest,
    WorkflowTerminalOutcome,
    WorkflowTransaction,
)

if TYPE_CHECKING:
    from simple_harness.runtime.context import ContextSnapshot
    from simple_harness.runtime.orchestration import (
        RuntimeActivationClaim,
        RuntimeStartAdmission,
        WorkflowLaunchRequest,
        WorkflowLaunchTicket,
        WorkflowSpawnContinuationClaim,
    )
    from simple_harness.runtime.start_snapshot import RunStart, StartSnapshot
    from simple_harness.runtime.workflow_spawn import (
        WorkflowSpawnAdmissionOutcome,
        WorkflowSpawnToolOutcome,
    )
    from simple_harness.workflow.execution_ports import WorkflowTerminalOutcome
from .core import ExecutionUnitOfWorkCore, _SqliteWorkflowTransaction
from .support import (
    _WORKFLOW_SPAWN_COMPLETION_FIELDS,
    _fault,
    _required,
    _time,
    _tool_result,
    _tool_result_json,
    _workflow_launch_request_from_json,
    _workflow_spawn_activation_hash,
    _workflow_spawn_completion_hash,
    _workflow_spawn_continuation_claim,
    _workflow_spawn_failure_evidence,
    _workflow_spawn_ready_activation,
)


def _workflow_spawn_completion_hash_from_row(row: sqlite3.Row) -> str:
    return _workflow_spawn_completion_hash(
        {name: row[name] for name in _WORKFLOW_SPAWN_COMPLETION_FIELDS}
    )


class WorkflowSpawnOutcomeMixin(ExecutionUnitOfWorkCore):
    """Workflow spawn continuation outcomes and admission continuation."""

    __slots__ = ()

    if TYPE_CHECKING:

        @staticmethod
        def _read_workflow_terminal_outcome(connection: sqlite3.Connection, run_id: str): ...
        @classmethod
        def _verify_workflow_terminal(
            cls, connection: sqlite3.Connection, outcome: object
        ) -> bool: ...
        async def admit_runtime_start(
            self,
            transaction: WorkflowTransaction,
            ticket: WorkflowLaunchTicket,
            start: RunStart,
            request: StartAdmissionRequest,
            snapshot: StartSnapshot,
            claim: RuntimeActivationClaim,
            *,
            now: float,
            fault: FaultHook | None = None,
        ) -> RuntimeStartAdmission: ...

    async def read_spawn_continuation_outcome(
        self,
        transaction: WorkflowTransaction,
        spawn_operation_id: str,
    ) -> ToolResult | None:
        tx = self._assert_open_workflow_transaction(transaction)
        spawn_operation_id = _required(spawn_operation_id, "spawn_operation_id")
        continuation = tx.connection.execute(
            "SELECT * FROM workflow_spawn_continuations WHERE operation_id=?",
            (spawn_operation_id,),
        ).fetchone()
        completion = tx.connection.execute(
            "SELECT * FROM workflow_spawn_completion_receipts WHERE spawn_operation_id=?",
            (spawn_operation_id,),
        ).fetchone()
        if continuation is None:
            if completion is not None:
                raise UnitOfWorkConflict("workflow spawn completion lacks its continuation")
            return None
        pointer = continuation["completion_receipt_id"]
        if pointer is None and completion is None:
            return None
        if (
            pointer is None
            or completion is None
            or str(pointer) != str(completion["completion_receipt_id"])
            or str(continuation["state"]) != "completed"
            or str(continuation["completion_path_kind"]) != str(completion["path_kind"])
        ):
            raise UnitOfWorkConflict("workflow spawn completion pointer differs")

        ticket, request = self._verify_spawn_completion_ticket(
            tx, spawn_operation_id, continuation, completion
        )

        result = self._verify_spawn_completion_effect(tx, completion)

        path_kind = self._verify_spawn_completion_path(tx, spawn_operation_id, completion)

        child_start_receipt_id = completion["child_runtime_start_receipt_id"]
        child_wait_receipt_id = completion["child_wait_receipt_id"]
        if child_start_receipt_id is not None or child_wait_receipt_id is not None:
            return self._verify_spawn_child_success(
                tx,
                spawn_operation_id,
                completion,
                request,
                result,
                child_start_receipt_id,
                child_wait_receipt_id,
            )

        self._verify_spawn_failure_evidence(tx, completion, result, path_kind, ticket)
        return result

    def _verify_spawn_completion_ticket(
        self,
        tx: _SqliteWorkflowTransaction,
        spawn_operation_id: str,
        continuation: sqlite3.Row,
        completion: sqlite3.Row,
    ) -> tuple[sqlite3.Row, WorkflowLaunchRequest]:
        ticket = tx.connection.execute(
            "SELECT * FROM workflow_launch_ticket_receipts WHERE ticket_receipt_id=?",
            (str(continuation["ticket_receipt_id"]),),
        ).fetchone()
        if ticket is None:
            raise UnitOfWorkConflict("workflow spawn completion ticket is missing")
        payload = json.loads(str(ticket["canonical_payload"]))
        if not isinstance(payload, dict):
            raise UnitOfWorkConflict("workflow spawn completion ticket is malformed")
        canonical_payload = canonical_json(payload)
        payload_hash = hashlib.sha256(canonical_payload.encode()).hexdigest()
        request = _workflow_launch_request_from_json(payload.get("request"))
        if (
            request.request_key != spawn_operation_id
            or str(ticket["request_key"]) != spawn_operation_id
            or str(ticket["canonical_payload"]) != canonical_payload
            or str(ticket["payload_hash"]) != payload_hash
            or str(ticket["ticket_id"])
            != self._derived_id("workflow-launch/ticket/v1", payload_hash)
        ):
            raise UnitOfWorkConflict("workflow spawn completion ticket hash differs")

        completion_hash = _workflow_spawn_completion_hash_from_row(completion)
        if completion_hash != str(completion["canonical_hash"]):
            raise UnitOfWorkConflict("workflow spawn completion hash differs")
        if (
            str(completion["ticket_receipt_id"]) != str(ticket["ticket_receipt_id"])
            or str(completion["parent_run_id"]) != str(continuation["parent_run_id"])
            or str(completion["effect_id"]) != str(continuation["effect_id"])
            or int(completion["handoff_attempt"]) != int(continuation["handoff_attempt"])
            or str(completion["effect_request_hash"]) != str(continuation["effect_request_hash"])
            or str(completion["issue_authority_hash"]) != str(continuation["issue_authority_hash"])
        ):
            raise UnitOfWorkConflict("workflow spawn completion identity differs")
        return ticket, request

    def _verify_spawn_completion_effect(
        self, tx: _SqliteWorkflowTransaction, completion: sqlite3.Row
    ) -> ToolResult:
        result = _tool_result(completion["tool_result_json"])
        effect = tx.connection.execute(
            "SELECT * FROM execution_effects WHERE effect_id=?",
            (str(completion["effect_id"]),),
        ).fetchone()
        if result is None or effect is None:
            raise UnitOfWorkConflict("workflow spawn completion Effect is missing")
        result_json = _tool_result_json(result)
        if (
            str(completion["tool_result_json"]) != result_json
            or str(completion["tool_result_hash"])
            != hashlib.sha256(result_json.encode()).hexdigest()
            or str(effect["state"]) != result.outcome.value
            or str(effect["result_json"]) != result_json
            or str(effect["call_id"]) != result.call_id.value
            or str(effect["request_hash"]) != str(completion["effect_request_hash"])
            or int(effect["handoff_attempt"]) != int(completion["handoff_attempt"])
            or (
                completion["failure_evidence_id"] is not None
                and effect["evidence_ref"] != completion["failure_evidence_id"]
            )
        ):
            raise UnitOfWorkConflict("workflow spawn completion Effect differs")
        return result

    def _verify_spawn_completion_path(
        self, tx: _SqliteWorkflowTransaction, spawn_operation_id: str, completion: sqlite3.Row
    ) -> str:
        path_kind = str(completion["path_kind"])
        ready_count = int(
            tx.connection.execute(
                "SELECT COUNT(*) FROM workflow_spawn_continuation_ready WHERE operation_id=?",
                (spawn_operation_id,),
            ).fetchone()[0]
        )
        activation_count = int(
            tx.connection.execute(
                "SELECT COUNT(*) FROM workflow_spawn_ready_activations WHERE spawn_operation_id=?",
                (spawn_operation_id,),
            ).fetchone()[0]
        )
        if path_kind == "direct":
            if ready_count != 0 or activation_count != 0:
                raise UnitOfWorkConflict("workflow spawn direct completion has recovery rows")
        elif path_kind in {"ready_recovery", "parent_terminal_activated"}:
            ready_row = tx.connection.execute(
                "SELECT * FROM workflow_spawn_continuation_ready WHERE operation_id=?",
                (spawn_operation_id,),
            ).fetchone()
            activation_rows = tx.connection.execute(
                """
                SELECT * FROM workflow_spawn_ready_activations
                WHERE spawn_operation_id=? ORDER BY created_at,activation_receipt_id
                """,
                (spawn_operation_id,),
            ).fetchall()
            if (
                ready_count != 1
                or ready_row is None
                or ready_row["consumed_at"] is None
                or not activation_rows
                or str(ready_row["ticket_receipt_id"]) != str(completion["ticket_receipt_id"])
                or str(ready_row["effect_id"]) != str(completion["effect_id"])
                or int(ready_row["handoff_attempt"]) != int(completion["handoff_attempt"])
                or str(completion["activation_chain_head_id"])
                != str(activation_rows[-1]["activation_receipt_id"])
            ):
                raise UnitOfWorkConflict("workflow spawn recovery completion chain differs")
            predecessor: str | None = None
            for index, activation in enumerate(activation_rows):
                if (
                    str(activation["ready_receipt_id"]) != str(ready_row["ready_receipt_id"])
                    or str(activation["spawn_operation_id"]) != spawn_operation_id
                    or str(activation["parent_run_id"]) != str(completion["parent_run_id"])
                    or str(activation["effect_id"]) != str(completion["effect_id"])
                    or activation["predecessor_activation_receipt_id"] != predecessor
                    or str(activation["canonical_hash"])
                    != _workflow_spawn_activation_hash(
                        activation_receipt_id=str(activation["activation_receipt_id"]),
                        ready_receipt_id=str(activation["ready_receipt_id"]),
                        spawn_operation_id=str(activation["spawn_operation_id"]),
                        parent_run_id=str(activation["parent_run_id"]),
                        effect_id=str(activation["effect_id"]),
                        owner_id=str(activation["owner_id"]),
                        runtime_lease_epoch=int(activation["runtime_lease_epoch"]),
                        run_fence_epoch=int(activation["run_fence_epoch"]),
                        workflow_lease_epoch=(
                            None
                            if activation["workflow_lease_epoch"] is None
                            else int(activation["workflow_lease_epoch"])
                        ),
                        continuation_claim_epoch=int(activation["continuation_claim_epoch"]),
                        predecessor_activation_receipt_id=predecessor,
                        version=int(activation["version"]),
                    )
                    or (
                        index < len(activation_rows) - 1
                        and str(activation["state"]) != "superseded"
                    )
                    or (
                        index == len(activation_rows) - 1 and str(activation["state"]) != "consumed"
                    )
                ):
                    raise UnitOfWorkConflict("workflow spawn recovery activation chain differs")
                predecessor = str(activation["activation_receipt_id"])
        elif path_kind == "parent_terminal_ticket_only":
            if (
                ready_count != 0
                or activation_count != 0
                or completion["activation_chain_head_id"] is not None
            ):
                raise UnitOfWorkConflict("workflow spawn terminal ticket-only chain differs")
        elif path_kind == "parent_terminal_ready_unactivated":
            ready_row = tx.connection.execute(
                "SELECT * FROM workflow_spawn_continuation_ready WHERE operation_id=?",
                (spawn_operation_id,),
            ).fetchone()
            blocker = (
                None
                if ready_row is None
                else tx.connection.execute(
                    "SELECT * FROM run_wait_blockers WHERE run_id=? "
                    "AND kind='tool' AND ledger_identity=? AND handoff_attempt=?",
                    (
                        str(completion["parent_run_id"]),
                        str(ready_row["effect_id"]),
                        int(ready_row["handoff_attempt"]),
                    ),
                ).fetchone()
            )
            if (
                ready_count != 1
                or activation_count != 0
                or ready_row is None
                or ready_row["consumed_at"] is None
                or str(ready_row["ticket_receipt_id"]) != str(completion["ticket_receipt_id"])
                or str(ready_row["effect_id"]) != str(completion["effect_id"])
                or int(ready_row["handoff_attempt"]) != int(completion["handoff_attempt"])
                or completion["activation_chain_head_id"] is not None
                or blocker is None
                or int(blocker["wake_consumed"]) != 1
                or blocker["consumed_at"] is None
                or str(blocker["superseded_by"]) != str(ready_row["ready_receipt_id"])
            ):
                raise UnitOfWorkConflict("workflow spawn unactivated terminal chain differs")
        else:
            raise UnitOfWorkConflict("workflow spawn completion path is unsupported")
        return path_kind

    def _verify_spawn_child_success(
        self,
        tx: _SqliteWorkflowTransaction,
        spawn_operation_id: str,
        completion: sqlite3.Row,
        request: WorkflowLaunchRequest,
        result: ToolResult,
        child_start_receipt_id: object,
        child_wait_receipt_id: object,
    ) -> ToolResult:
        if (
            child_start_receipt_id is None
            or child_wait_receipt_id is None
            or result.outcome is not ToolOutcome.SUCCEEDED
            or completion["failure_evidence_kind"] is not None
            or completion["failure_evidence_id"] is not None
            or completion["failure_evidence_json"] is not None
            or completion["failure_evidence_hash"] is not None
            or not isinstance(result.value, Mapping)
        ):
            raise UnitOfWorkConflict("workflow spawn success completion shape differs")
        wait = tx.connection.execute(
            "SELECT * FROM workflow_spawn_child_wait_receipts WHERE parent_wait_receipt_id=?",
            (str(child_wait_receipt_id),),
        ).fetchone()
        start_receipt = tx.connection.execute(
            "SELECT * FROM runtime_start_receipts WHERE ticket_receipt_id=?",
            (str(child_start_receipt_id),),
        ).fetchone()
        child = (
            None
            if start_receipt is None
            else tx.connection.execute(
                "SELECT * FROM runs WHERE run_id=?",
                (str(start_receipt["run_id"]),),
            ).fetchone()
        )
        command = tx.connection.execute(
            "SELECT * FROM child_commands WHERE workflow_ticket_receipt_id=?",
            (str(child_start_receipt_id),),
        ).fetchone()
        link = (
            None
            if child is None
            else tx.connection.execute(
                "SELECT * FROM run_links WHERE parent_run_id=? AND child_run_id=?",
                (str(completion["parent_run_id"]), str(child["run_id"])),
            ).fetchone()
        )
        parent = tx.connection.execute(
            "SELECT state,version FROM runs WHERE run_id=?",
            (str(completion["parent_run_id"]),),
        ).fetchone()
        if (
            wait is None
            or start_receipt is None
            or child is None
            or command is None
            or link is None
            or parent is None
            or str(wait["spawn_operation_id"]) != spawn_operation_id
            or str(wait["parent_run_id"]) != str(completion["parent_run_id"])
            or str(wait["child_run_id"]) != str(child["run_id"])
            or str(wait["child_command_id"]) != str(command["command_id"])
            or str(command["parent_run_id"]) != str(completion["parent_run_id"])
            or str(command["child_run_id"]) != str(child["run_id"])
            or str(command["state"]) not in {"pending", "scheduled", "acked"}
            or str(link["attachment_policy"]) != AttachmentPolicy.ATTACHED.value
            or str(child["parent_run_id"]) != str(completion["parent_run_id"])
            or str(child["root_run_id"]) != request.root_run_id
        ):
            raise UnitOfWorkConflict("workflow spawn success durable chain differs")
        wait_state = str(wait["state"])
        claimed_continuation = self._verify_spawn_child_wait(
            tx, spawn_operation_id, completion, parent, child, wait, wait_state
        )
        if str(command["state"]) == "acked":
            terminal = tx.connection.execute(
                "SELECT * FROM child_terminal_receipts WHERE command_id=?",
                (str(command["command_id"]),),
            ).fetchone()
            signal = (
                None
                if terminal is None or terminal["signal_id"] is None
                else tx.connection.execute(
                    "SELECT * FROM child_signals WHERE signal_id=?",
                    (str(terminal["signal_id"]),),
                ).fetchone()
            )
            if (
                terminal is None
                or signal is None
                or str(terminal["child_run_id"]) != str(child["run_id"])
                or str(terminal["terminal_state"]) != str(child["state"])
                or str(signal["parent_run_id"]) != str(completion["parent_run_id"])
                or str(signal["child_run_id"]) != str(child["run_id"])
            ):
                raise UnitOfWorkConflict("workflow spawn child terminal successor differs")
        checkpoint = tx.connection.execute(
            "SELECT * FROM workflow_checkpoints WHERE run_id=? AND"
            " namespace='react.termination.v1' AND version=?",
            (
                str(completion["parent_run_id"]),
                int(wait["react_checkpoint_revision"]),
            ),
        ).fetchone()
        context = tx.connection.execute(
            "SELECT checkpoint_json FROM workflow_checkpoints WHERE run_id=? AND"
            " namespace='react.context.v1' AND version=?",
            (
                str(completion["parent_run_id"]),
                int(wait["context_post_revision"]),
            ),
        ).fetchone()
        fence = tx.connection.execute(
            "SELECT * FROM run_fences WHERE run_id=?",
            (str(completion["parent_run_id"]),),
        ).fetchone()
        historical_checkpoint_differs = (
            checkpoint is None
            or context is None
            or fence is None
            or str(checkpoint["checkpoint_hash"]) != str(wait["react_checkpoint_hash"])
            or hashlib.sha256(str(checkpoint["checkpoint_json"]).encode()).hexdigest()
            != str(checkpoint["checkpoint_hash"])
        )
        historical_fence_differs = wait_state in {"unconsumed", "woken"} and (
            fence is None
            or str(fence["state"]) != "released"
            or int(fence["runtime_lease_epoch"]) != int(wait["released_runtime_lease_epoch"])
            or int(fence["epoch"]) != int(wait["released_run_fence_epoch"])
        )
        current_fence_differs = wait_state in {"claimed", "acked_completion_pending"} and (
            fence is None
            or claimed_continuation is None
            or str(fence["state"]) != "active"
            or str(fence["owner_id"]) != str(claimed_continuation["claimed_by"])
            or int(fence["runtime_lease_epoch"]) != int(claimed_continuation["runtime_lease_epoch"])
            or int(fence["runtime_lease_epoch"]) <= int(wait["released_runtime_lease_epoch"])
            or int(fence["epoch"]) <= int(wait["released_run_fence_epoch"])
        )
        if historical_checkpoint_differs or historical_fence_differs or current_fence_differs:
            raise UnitOfWorkConflict("workflow spawn success checkpoint differs")
        return result

    def _verify_spawn_child_wait(
        self,
        tx: _SqliteWorkflowTransaction,
        spawn_operation_id: str,
        completion: sqlite3.Row,
        parent: sqlite3.Row,
        child: sqlite3.Row,
        wait: sqlite3.Row,
        wait_state: str,
    ) -> sqlite3.Row | None:
        claimed_continuation: sqlite3.Row | None = None
        if wait_state == "unconsumed":
            if (
                wait["child_signal_id"] is not None
                or wait["continuation_id"] is not None
                or str(parent["state"]) != RunState.WAITING.value
                or int(parent["version"]) != int(wait["parent_waiting_version"])
                or tx.connection.execute(
                    "SELECT 1 FROM workflow_leases WHERE run_id=? LIMIT 1",
                    (str(completion["parent_run_id"]),),
                ).fetchone()
                is not None
            ):
                raise UnitOfWorkConflict("workflow spawn unconsumed child-wait differs")
        elif wait_state == "woken":
            self._verify_spawn_child_wait_woken(tx, completion, parent, child, wait)
        elif wait_state == "claimed":
            claimed_continuation = self._verify_spawn_child_wait_claimed(
                tx, completion, parent, wait
            )
        elif wait_state in {"acked_completion_pending", "acked"}:
            claimed_continuation = self._verify_spawn_child_wait_acked(
                tx, completion, parent, wait, wait_state
            )
        elif wait_state == "acked_parent_terminal":
            self._verify_spawn_child_wait_parent_terminal(
                tx, spawn_operation_id, completion, parent, child, wait
            )
        else:
            raise UnitOfWorkConflict("workflow spawn child-wait lifecycle is unsupported")
        return claimed_continuation

    def _verify_spawn_child_wait_woken(
        self,
        tx: _SqliteWorkflowTransaction,
        completion: sqlite3.Row,
        parent: sqlite3.Row,
        child: sqlite3.Row,
        wait: sqlite3.Row,
    ) -> None:
        signal = tx.connection.execute(
            "SELECT * FROM child_signals WHERE signal_id=?",
            (wait["child_signal_id"],),
        ).fetchone()
        continuation = tx.connection.execute(
            "SELECT * FROM continuations WHERE continuation_id=?",
            (wait["continuation_id"],),
        ).fetchone()
        expected_lifecycle_hash = hashlib.sha256(
            canonical_json(
                {
                    "identity_hash": str(wait["identity_hash"]),
                    "state": "woken",
                    "version": int(wait["version"]),
                    "child_signal_id": wait["child_signal_id"],
                    "continuation_id": wait["continuation_id"],
                }
            ).encode()
        ).hexdigest()
        if (
            signal is None
            or continuation is None
            or str(signal["state"]) != "acked"
            or str(signal["parent_run_id"]) != str(completion["parent_run_id"])
            or str(signal["child_run_id"]) != str(child["run_id"])
            or str(continuation["run_id"]) != str(completion["parent_run_id"])
            or str(continuation["state"]) != "pending"
            or str(parent["state"]) != RunState.QUEUED.value
            or str(wait["lifecycle_hash"]) != expected_lifecycle_hash
        ):
            raise UnitOfWorkConflict("workflow spawn woken child-wait differs")

    def _verify_spawn_child_wait_claimed(
        self,
        tx: _SqliteWorkflowTransaction,
        completion: sqlite3.Row,
        parent: sqlite3.Row,
        wait: sqlite3.Row,
    ) -> sqlite3.Row:
        signal = tx.connection.execute(
            "SELECT * FROM child_signals WHERE signal_id=?",
            (wait["child_signal_id"],),
        ).fetchone()
        continuation = tx.connection.execute(
            "SELECT * FROM continuations WHERE continuation_id=?",
            (wait["continuation_id"],),
        ).fetchone()
        claimed_continuation = continuation
        runtime_lease = tx.connection.execute(
            "SELECT * FROM workflow_leases WHERE run_id=? AND namespace=?",
            (str(completion["parent_run_id"]), RUNTIME_LEASE_NAMESPACE),
        ).fetchone()
        expected_lifecycle_hash = (
            None
            if continuation is None
            else hashlib.sha256(
                canonical_json(
                    {
                        "identity_hash": str(wait["identity_hash"]),
                        "state": "claimed",
                        "version": int(wait["version"]),
                        "child_signal_id": wait["child_signal_id"],
                        "continuation_id": wait["continuation_id"],
                        "claim_owner": continuation["claimed_by"],
                        "runtime_lease_epoch": continuation["runtime_lease_epoch"],
                    }
                ).encode()
            ).hexdigest()
        )
        if (
            signal is None
            or continuation is None
            or runtime_lease is None
            or str(signal["state"]) != "acked"
            or str(continuation["state"]) != "claimed"
            or str(continuation["run_id"]) != str(completion["parent_run_id"])
            or str(runtime_lease["owner_id"]) != str(continuation["claimed_by"])
            or int(runtime_lease["epoch"]) != int(continuation["runtime_lease_epoch"])
            or str(parent["state"]) != RunState.RUNNING.value
            or str(wait["lifecycle_hash"]) != expected_lifecycle_hash
        ):
            raise UnitOfWorkConflict("workflow spawn claimed child-wait differs")
        return claimed_continuation

    def _verify_spawn_child_wait_acked(
        self,
        tx: _SqliteWorkflowTransaction,
        completion: sqlite3.Row,
        parent: sqlite3.Row,
        wait: sqlite3.Row,
        wait_state: str,
    ) -> sqlite3.Row:
        signal = tx.connection.execute(
            "SELECT * FROM child_signals WHERE signal_id=?",
            (wait["child_signal_id"],),
        ).fetchone()
        continuation = tx.connection.execute(
            "SELECT * FROM continuations WHERE continuation_id=?",
            (wait["continuation_id"],),
        ).fetchone()
        progress = tx.connection.execute(
            "SELECT * FROM continuation_progress_receipts WHERE receipt_id=?",
            (wait["progress_receipt_id"],),
        ).fetchone()
        claimed_continuation = continuation
        pending_json = wait["pending_child_completion_json"]
        pending_hash = wait["pending_child_completion_hash"]
        if (
            signal is None
            or continuation is None
            or progress is None
            or str(signal["state"]) != "acked"
            or str(continuation["state"]) != "acked"
            or continuation["ack_receipt_id"] != wait["progress_receipt_id"]
            or str(progress["continuation_id"]) != str(continuation["continuation_id"])
            or str(progress["owner_id"]) != str(continuation["claimed_by"])
            or int(progress["runtime_lease_epoch"]) != int(continuation["runtime_lease_epoch"])
            or int(progress["claim_epoch"]) != int(continuation["claim_epoch"])
            or pending_json is None
            or pending_hash is None
            or hashlib.sha256(str(pending_json).encode()).hexdigest() != str(pending_hash)
        ):
            raise UnitOfWorkConflict("workflow spawn acknowledged child-wait differs")
        if wait_state == "acked_completion_pending":
            expected_lifecycle_hash = hashlib.sha256(
                canonical_json(
                    {
                        "identity_hash": str(wait["identity_hash"]),
                        "state": "acked_completion_pending",
                        "version": int(wait["version"]),
                        "child_signal_id": wait["child_signal_id"],
                        "continuation_id": wait["continuation_id"],
                        "progress_receipt_id": wait["progress_receipt_id"],
                        "pending_child_completion_hash": pending_hash,
                    }
                ).encode()
            ).hexdigest()
            if (
                wait["child_completion_append_receipt_id"] is not None
                or wait["child_completion_context_revision"] is not None
                or str(parent["state"]) != RunState.RUNNING.value
                or str(wait["lifecycle_hash"]) != expected_lifecycle_hash
            ):
                raise UnitOfWorkConflict("workflow spawn pending completion differs")
        else:
            expected_lifecycle_hash = hashlib.sha256(
                canonical_json(
                    {
                        "identity_hash": str(wait["identity_hash"]),
                        "state": "acked",
                        "version": int(wait["version"]),
                        "progress_receipt_id": wait["progress_receipt_id"],
                        "child_completion_append_id": wait["child_completion_append_id"],
                        "child_completion_context_revision": wait[
                            "child_completion_context_revision"
                        ],
                    }
                ).encode()
            ).hexdigest()
            appended_context = tx.connection.execute(
                "SELECT checkpoint_json FROM workflow_checkpoints "
                "WHERE run_id=? AND namespace='react.context.v1' "
                "AND version=?",
                (
                    str(completion["parent_run_id"]),
                    wait["child_completion_context_revision"],
                ),
            ).fetchone()
            appended_payload = (
                None
                if appended_context is None
                else json.loads(str(appended_context["checkpoint_json"]))
            )
            append_receipts = (
                None
                if not isinstance(appended_payload, dict)
                else appended_payload.get("append_receipts")
            )
            if (
                wait["child_completion_append_receipt_id"] != wait["child_completion_append_id"]
                or not isinstance(append_receipts, dict)
                or wait["child_completion_append_id"] not in append_receipts
                or str(wait["lifecycle_hash"]) != expected_lifecycle_hash
            ):
                raise UnitOfWorkConflict("workflow spawn completed Context append differs")
        return claimed_continuation

    def _verify_spawn_child_wait_parent_terminal(
        self,
        tx: _SqliteWorkflowTransaction,
        spawn_operation_id: str,
        completion: sqlite3.Row,
        parent: sqlite3.Row,
        child: sqlite3.Row,
        wait: sqlite3.Row,
    ) -> None:
        signal = tx.connection.execute(
            "SELECT * FROM child_signals WHERE signal_id=?",
            (wait["child_signal_id"],),
        ).fetchone()
        continuation = tx.connection.execute(
            "SELECT * FROM continuations WHERE continuation_id=?",
            (wait["continuation_id"],),
        ).fetchone()
        progress = tx.connection.execute(
            "SELECT * FROM continuation_progress_receipts WHERE receipt_id=?",
            (wait["progress_receipt_id"],),
        ).fetchone()
        terminal_event = tx.connection.execute(
            "SELECT kind FROM run_events WHERE event_id=? AND run_id=?",
            (
                wait["pending_completion_terminal_receipt_id"],
                str(completion["parent_run_id"]),
            ),
        ).fetchone()
        pending_json = wait["pending_child_completion_json"]
        pending_hash = wait["pending_child_completion_hash"]
        terminal_state = wait["pending_completion_terminal_state"]
        terminal_receipt_id = wait["pending_completion_terminal_receipt_id"]
        phase_kind = wait["parent_terminal_phase_kind"]
        claimed_ack = wait["claimed_continuation_terminal_ack_receipt_id"]
        if phase_kind not in {
            "child_active",
            "signal_pending",
            "continuation_claimed",
            "completion_pending",
        }:
            raise UnitOfWorkConflict("workflow spawn parent-terminal phase differs")
        expected_terminal_hash = hashlib.sha256(
            canonical_json(
                {
                    "parent_wait_receipt_id": str(wait["parent_wait_receipt_id"]),
                    "spawn_operation_id": spawn_operation_id,
                    "phase_kind": phase_kind,
                    "terminal_receipt_id": terminal_receipt_id,
                    "terminal_state": terminal_state,
                    "child_signal_id": (
                        None if phase_kind == "child_active" else wait["child_signal_id"]
                    ),
                    "continuation_id": (
                        None if phase_kind == "child_active" else wait["continuation_id"]
                    ),
                    "pending_child_completion_hash": pending_hash,
                    "claimed_continuation_ack_receipt_id": claimed_ack,
                    "child_cancel_request_id": wait["child_cancel_request_id"],
                    "child_cancel_receipt_id": wait["child_cancel_receipt_id"],
                    "reused_child_cancel_receipt_id": wait["reused_child_cancel_receipt_id"],
                }
            ).encode()
        ).hexdigest()
        lifecycle_payload: dict[str, JsonValue] = {
            "identity_hash": str(wait["identity_hash"]),
            "state": "acked_parent_terminal",
            "version": int(wait["version"]),
            "phase_kind": phase_kind,
            "terminal_receipt_id": terminal_receipt_id,
            "terminal_state": terminal_state,
            "terminal_hash": expected_terminal_hash,
        }
        if phase_kind == "child_active" and wait["late_signal_quarantine_receipt_id"] is not None:
            lifecycle_payload["late_signal_quarantine_receipt_id"] = wait[
                "late_signal_quarantine_receipt_id"
            ]
        expected_lifecycle_hash = hashlib.sha256(
            canonical_json(lifecycle_payload).encode()
        ).hexdigest()
        common_differs = (
            terminal_state
            not in {
                RunState.COMPLETED.value,
                RunState.FAILED.value,
                RunState.CANCELLED.value,
            }
            or terminal_event is None
            or str(parent["state"]) != str(terminal_state)
            or str(terminal_event["kind"]) != f"run.{terminal_state}"
            or str(wait["pending_completion_terminal_hash"]) != expected_terminal_hash
            or str(wait["lifecycle_hash"]) != expected_lifecycle_hash
        )
        progress_differs = (
            continuation is None
            or progress is None
            or str(continuation["state"]) != "acked"
            or continuation["ack_receipt_id"] != wait["progress_receipt_id"]
            or str(progress["continuation_id"]) != str(continuation["continuation_id"])
        )
        phase_differs = False
        if phase_kind == "child_active":
            cancel_id = (
                wait["child_cancel_receipt_id"]
                if wait["child_cancel_receipt_id"] is not None
                else wait["reused_child_cancel_receipt_id"]
            )
            cancel = tx.connection.execute(
                "SELECT * FROM workflow_cancel_receipts WHERE cancel_id=?",
                (cancel_id,),
            ).fetchone()
            late_receipt_id = wait["late_signal_quarantine_receipt_id"]
            late_ack = (
                None
                if late_receipt_id is None
                else tx.connection.execute(
                    "SELECT * FROM child_signal_ack_receipts WHERE receipt_id=?",
                    (late_receipt_id,),
                ).fetchone()
            )
            late_event = (
                None
                if late_ack is None
                else tx.connection.execute(
                    "SELECT kind FROM run_events WHERE event_id=?",
                    (late_ack["event_id"],),
                ).fetchone()
            )
            cancel_is_terminal = cancel is not None and str(cancel["phase"]) == "terminal"
            late_differs = (
                signal is not None or continuation is not None
                if late_receipt_id is None
                else (
                    signal is None
                    or continuation is None
                    or late_ack is None
                    or late_event is None
                    or str(signal["state"]) != "acked"
                    or signal["ack_receipt_id"] != late_receipt_id
                    or str(continuation["state"]) != "quarantined"
                    or str(late_ack["signal_id"]) != str(signal["signal_id"])
                    or str(late_ack["continuation_id"]) != str(continuation["continuation_id"])
                    or str(late_event["kind"]) != "child.signal_quarantined"
                )
            )
            phase_differs = (
                late_differs
                or progress is not None
                or pending_json is not None
                or pending_hash is not None
                or cancel is None
                or str(cancel["run_id"]) != str(child["run_id"])
                or str(cancel["phase"])
                not in {
                    "requested",
                    "cancelling",
                    "blocked",
                    "terminal",
                }
                or (cancel_is_terminal and str(child["state"]) != RunState.CANCELLED.value)
                or (
                    not cancel_is_terminal
                    and str(child["state"]) != RunState.CANCEL_REQUESTED.value
                )
                or (
                    wait["child_cancel_receipt_id"] is not None
                    and wait["child_cancel_request_id"] != wait["child_cancel_receipt_id"]
                )
                or (
                    wait["child_cancel_receipt_id"] is None
                    and wait["reused_child_cancel_receipt_id"] is None
                )
                or (
                    wait["child_cancel_receipt_id"] is not None
                    and wait["reused_child_cancel_receipt_id"] is not None
                )
                or claimed_ack is not None
            )
        elif phase_kind == "signal_pending":
            phase_differs = (
                signal is None
                or continuation is None
                or str(signal["state"]) != "acked"
                or str(continuation["state"]) != "quarantined"
                or progress is not None
                or pending_json is not None
                or pending_hash is not None
                or wait["late_signal_quarantine_receipt_id"] != terminal_receipt_id
                or claimed_ack is not None
                or wait["child_cancel_request_id"] is not None
                or wait["child_cancel_receipt_id"] is not None
                or wait["reused_child_cancel_receipt_id"] is not None
            )
        elif phase_kind == "continuation_claimed":
            phase_differs = (
                signal is None
                or str(signal["state"]) != "acked"
                or progress_differs
                or pending_json is not None
                or pending_hash is not None
                or claimed_ack != wait["progress_receipt_id"]
                or wait["late_signal_quarantine_receipt_id"] is not None
                or wait["child_cancel_request_id"] is not None
                or wait["child_cancel_receipt_id"] is not None
                or wait["reused_child_cancel_receipt_id"] is not None
            )
        else:
            phase_differs = (
                signal is None
                or str(signal["state"]) != "acked"
                or progress_differs
                or pending_json is None
                or pending_hash is None
                or hashlib.sha256(str(pending_json).encode()).hexdigest() != str(pending_hash)
                or wait["child_completion_append_receipt_id"] is not None
                or wait["child_completion_context_revision"] is not None
                or wait["late_signal_quarantine_receipt_id"] is not None
                or claimed_ack is not None
                or wait["child_cancel_request_id"] is not None
                or wait["child_cancel_receipt_id"] is not None
                or wait["reused_child_cancel_receipt_id"] is not None
            )
        if common_differs or phase_differs:
            raise UnitOfWorkConflict("workflow spawn parent-terminal child-wait differs")

    def _verify_spawn_failure_evidence(
        self,
        tx: _SqliteWorkflowTransaction,
        completion: sqlite3.Row,
        result: ToolResult,
        path_kind: str,
        ticket: sqlite3.Row,
    ) -> None:
        evidence = _workflow_spawn_failure_evidence(completion)
        evidence_hash = str(completion["failure_evidence_hash"])
        if str(completion["failure_evidence_id"]) != self._derived_id(
            "workflow-spawn/failure-evidence/v1", evidence_hash
        ):
            raise UnitOfWorkConflict("workflow spawn failure evidence identity differs")
        evidence_kind = evidence.get("kind")
        if evidence_kind == "catalog_stale":
            if (
                result.outcome is not ToolOutcome.FAILED
                or result.error_code != "workflow_catalog_stale"
                or evidence.get("ticket_catalog_generation") != int(ticket["catalog_generation"])
                or evidence.get("ticket_catalog_version")
                != int(ticket["catalog_authority_version"])
                or evidence.get("ticket_catalog_hash") != str(ticket["catalog_hash"])
                or (
                    evidence.get("observed_catalog_generation"),
                    evidence.get("observed_catalog_version"),
                    evidence.get("observed_catalog_hash"),
                )
                == (
                    int(ticket["catalog_generation"]),
                    int(ticket["catalog_authority_version"]),
                    str(ticket["catalog_hash"]),
                )
            ):
                raise UnitOfWorkConflict("workflow spawn catalog-stale evidence differs")
        elif evidence_kind == "graph_version_unavailable":
            activation = tx.connection.execute(
                "SELECT * FROM workflow_spawn_ready_activations WHERE activation_receipt_id=?",
                (str(completion["activation_chain_head_id"]),),
            ).fetchone()
            observed_kind = evidence.get("observed_kind")
            observed_hash = evidence.get("observed_implementation_hash")
            if (
                result.outcome is not ToolOutcome.FAILED
                or result.error_code != "graph_version_unavailable"
                or activation is None
                or evidence.get("ticket_receipt_id") != str(ticket["ticket_receipt_id"])
                or evidence.get("profile_key") != str(ticket["profile_key"])
                or evidence.get("workflow_name") != str(ticket["workflow_name"])
                or evidence.get("workflow_version") != str(ticket["workflow_version"])
                or evidence.get("expected_implementation_hash")
                != str(ticket["implementation_fingerprint"])
                or evidence.get("activation_receipt_id") != str(activation["activation_receipt_id"])
                or evidence.get("parent_run_id") != str(activation["parent_run_id"])
                or evidence.get("owner_id") != str(activation["owner_id"])
                or evidence.get("runtime_lease_epoch") != int(activation["runtime_lease_epoch"])
                or evidence.get("run_fence_epoch") != int(activation["run_fence_epoch"])
                or evidence.get("workflow_lease_epoch")
                != (
                    None
                    if activation["workflow_lease_epoch"] is None
                    else int(activation["workflow_lease_epoch"])
                )
                or evidence.get("continuation_claim_epoch")
                != int(activation["continuation_claim_epoch"])
                or not isinstance(evidence.get("registry_content_digest"), str)
                or observed_kind not in {"missing", "drift"}
                or (observed_kind == "missing" and observed_hash is not None)
                or (
                    observed_kind == "drift"
                    and (
                        not isinstance(observed_hash, str)
                        or observed_hash == evidence.get("expected_implementation_hash")
                    )
                )
            ):
                raise UnitOfWorkConflict("workflow spawn graph-unavailable evidence differs")
        elif evidence_kind == "workflow_parent_terminal_before_spawn":
            terminal = self._read_workflow_terminal_outcome(
                tx.connection, str(completion["parent_run_id"])
            )
            if (
                path_kind
                not in {
                    "parent_terminal_ticket_only",
                    "parent_terminal_ready_unactivated",
                    "parent_terminal_activated",
                }
                or result.outcome is not ToolOutcome.FAILED
                or result.error_code != "workflow_parent_terminal_before_spawn"
                or not isinstance(terminal, WorkflowTerminalOutcome)
                or not self._verify_workflow_terminal(tx.connection, terminal)
                or evidence.get("terminal_receipt_id") != terminal.receipt_id
                or evidence.get("terminal_state") != terminal.state
                or evidence.get("terminal_outcome_hash") != terminal.outcome_hash
            ):
                raise UnitOfWorkConflict("workflow spawn parent-terminal evidence differs")
        else:
            raise UnitOfWorkConflict("workflow spawn failure evidence is unsupported")

    async def read_spawn_admission_outcome(
        self,
        transaction: WorkflowTransaction,
        spawn_operation_id: str,
    ) -> WorkflowSpawnAdmissionOutcome | None:

        tx = self._assert_open_workflow_transaction(transaction)
        result = await self.read_spawn_continuation_outcome(transaction, spawn_operation_id)
        if result is None:
            return None
        completion = tx.connection.execute(
            "SELECT * FROM workflow_spawn_completion_receipts WHERE spawn_operation_id=?",
            (spawn_operation_id,),
        ).fetchone()
        if (
            completion is None
            or completion["child_runtime_start_receipt_id"] is None
            or completion["child_wait_receipt_id"] is None
            or result.outcome is not ToolOutcome.SUCCEEDED
            or not isinstance(result.value, Mapping)
        ):
            return None
        wait = tx.connection.execute(
            "SELECT * FROM workflow_spawn_child_wait_receipts WHERE parent_wait_receipt_id=?",
            (str(completion["child_wait_receipt_id"]),),
        ).fetchone()
        if wait is None:
            raise UnitOfWorkConflict("workflow spawn child-wait receipt is missing")
        value = thaw_json(result.value)
        if not isinstance(value, dict):
            raise UnitOfWorkConflict("workflow spawn result payload is malformed")
        spawn_result = _create_workflow_spawn_result(
            schema_version=value.get("schema_version"),
            child_run_id=value.get("child_run_id"),
            child_request_id=value.get("child_request_id"),
            parent_run_id=value.get("parent_run_id"),
            root_run_id=value.get("root_run_id"),
            ticket_receipt_id=value.get("ticket_receipt_id"),
            runtime_start_receipt_id=value.get("runtime_start_receipt_id"),
            child_command_id=value.get("child_command_id"),
            attachment_policy=AttachmentPolicy(cast(str, value.get("attachment_policy"))),
        )
        if (
            spawn_result.child_run_id != str(wait["child_run_id"])
            or spawn_result.parent_run_id != str(wait["parent_run_id"])
            or spawn_result.child_command_id != str(wait["child_command_id"])
            or spawn_result.runtime_start_receipt_id
            != str(completion["child_runtime_start_receipt_id"])
        ):
            raise UnitOfWorkConflict("workflow spawn admission result differs")
        child_start_ref = ChildStartDispatchRef(
            child_start_receipt_id=str(wait["child_start_receipt_id"]),
            child_dispatch_claim_id=str(wait["child_dispatch_claim_id"]),
            child_run_id=str(wait["child_run_id"]),
        )
        suspension = WorkflowChildWaitBinding(
            parent_run_id=str(wait["parent_run_id"]),
            child_run_id=str(wait["child_run_id"]),
            child_command_id=str(wait["child_command_id"]),
            parent_wait_receipt_id=str(wait["parent_wait_receipt_id"]),
            expected_parent_version=int(wait["parent_waiting_version"]),
            react_checkpoint_revision=int(wait["react_checkpoint_revision"]),
            expected_signal_domain=str(wait["expected_signal_domain"]),
            source_phase=str(wait["source_phase"]),
            batch_digest=str(wait["batch_digest"]),
            spawn_ordinal=int(wait["spawn_ordinal"]),
            next_tool_ordinal=int(wait["next_tool_ordinal"]),
            spawn_result_append_receipt_id=str(wait["spawn_result_append_receipt_id"]),
            context_revision=int(wait["context_post_revision"]),
            termination_started_at=float(wait["termination_started_at"]),
            termination_last_observed_at=float(wait["termination_last_observed_at"]),
            wall_deadline=(None if wait["wall_deadline"] is None else float(wait["wall_deadline"])),
            termination_policy_snapshot_hash=str(wait["termination_policy_snapshot_hash"]),
        )
        return _create_workflow_spawn_admission_outcome(
            child_start_ref=child_start_ref,
            result=spawn_result,
            suspension=suspension,
        )

    async def continue_spawn_admission(
        self,
        transaction: WorkflowTransaction,
        ticket: WorkflowLaunchTicket,
        continuation: WorkflowSpawnContinuationClaim,
        start: RunStart,
        request: StartAdmissionRequest,
        snapshot: StartSnapshot,
        claim: RuntimeActivationClaim,
        *,
        now: float,
        fault: FaultHook | None = None,
    ) -> WorkflowSpawnToolOutcome:
        # 按需导入：循环依赖（uow ↔ context）
        from simple_harness.runtime.context import _append_context_in_transaction

        tx = self._assert_open_workflow_transaction(transaction)
        if (
            await self.read_spawn_admission_outcome(transaction, continuation.spawn_operation_id)
            is not None
        ):
            raise UnitOfWorkConflict(
                "workflow spawn admission already committed; use receipt reader"
            )
        now = _time(now)
        (
            continuation_row,
            parent,
            runtime,
            active_activation,
            path_kind,
            activation_chain_head_id,
        ) = self._require_spawn_admission_parent(tx, ticket, continuation, now)

        admission = await self.admit_runtime_start(
            transaction,
            ticket,
            start,
            request,
            snapshot,
            claim,
            now=now,
            fault=fault,
        )
        if (
            admission.disposition
            not in {
                RuntimeStartDisposition.START_NEW,
                RuntimeStartDisposition.START_ORPHAN,
            }
            or admission.dispatch_claim is None
        ):
            raise UnitOfWorkConflict("direct workflow spawn requires a fresh child start authority")
        ticket_row = tx.connection.execute(
            "SELECT * FROM workflow_launch_ticket_receipts WHERE ticket_receipt_id=?",
            (ticket.ticket_receipt_id,),
        ).fetchone()
        effect = tx.connection.execute(
            "SELECT * FROM execution_effects WHERE effect_id=?",
            (str(continuation_row["effect_id"]),),
        ).fetchone()
        checkpoint = tx.connection.execute(
            "SELECT * FROM workflow_checkpoints WHERE run_id=? AND namespace='react.termination.v1'"
            " ORDER BY version DESC LIMIT 1",
            (continuation.parent_run_id,),
        ).fetchone()
        if ticket_row is None or effect is None or checkpoint is None:
            raise UnitOfWorkConflict("workflow spawn success authority is missing")
        checkpoint_json = str(checkpoint["checkpoint_json"])
        checkpoint_value = json.loads(checkpoint_json)
        if (
            not isinstance(checkpoint_value, dict)
            or checkpoint_value.get("phase") != "tool_batch_reserved"
            or int(checkpoint["version"]) != 0
            or str(effect["state"]) not in {EffectState.HANDED_OFF.value, EffectState.UNKNOWN.value}
            or str(effect["run_id"]) != continuation.parent_run_id
        ):
            raise UnitOfWorkConflict("workflow spawn success phase differs")
        spawn_result = _create_workflow_spawn_result(
            schema_version="workflow_spawn.result.v1",
            child_run_id=admission.receipt.run_id,
            child_request_id=start.request_id.value,
            parent_run_id=continuation.parent_run_id,
            root_run_id=str(parent["root_run_id"]),
            ticket_receipt_id=ticket.ticket_receipt_id,
            runtime_start_receipt_id=admission.receipt.ticket_receipt_id,
            child_command_id=str(ticket_row["child_command_id"]),
            attachment_policy=AttachmentPolicy.ATTACHED,
        )
        tool_result = ToolResult.succeeded(CallId(str(effect["call_id"])), spawn_result.to_json())
        tool_result_json = _tool_result_json(tool_result)
        tool_result_hash = hashlib.sha256(tool_result_json.encode()).hexdigest()
        append_id = self._derived_id(
            "workflow-spawn/context-append/v1", continuation.spawn_operation_id
        )
        context_pre_revision = checkpoint_value.get("context_revision")
        if isinstance(context_pre_revision, bool) or not isinstance(context_pre_revision, int):
            raise UnitOfWorkConflict("workflow spawn Context revision is missing")
        parent_lease = ExecutionLease(
            continuation.parent_run_id,
            RUNTIME_LEASE_NAMESPACE,
            continuation.owner_id,
            continuation.runtime_lease_epoch,
            float(runtime["expires_at"]),
        )
        context = _append_context_in_transaction(
            tx.connection,
            RunId(continuation.parent_run_id),
            parent_lease,
            context_pre_revision,
            append_id,
            (
                Message(
                    MessageRole.TOOL,
                    canonical_json(spawn_result.to_json()),
                    name="workflow_spawn",
                    call_id=CallId(str(effect["raw_call_id"])),
                ),
            ),
            now=now,
        )
        _fault(fault, "workflow:spawn_admission:after_context_write")
        batch_digest = hashlib.sha256(checkpoint_json.encode()).hexdigest()
        next_checkpoint_hash, next_checkpoint_version = self._write_spawn_child_wait_checkpoint(
            tx,
            continuation=continuation,
            admission=admission,
            checkpoint=checkpoint,
            checkpoint_value=checkpoint_value,
            context=context,
            effect=effect,
            now=now,
            fault=fault,
        )
        parent_waiting_version = self._suspend_parent_for_spawn_child(
            tx,
            continuation=continuation,
            parent=parent,
            active_activation=active_activation,
            now=now,
            fault=fault,
        )
        parent_wait_receipt_id = self._derived_id(
            "workflow-spawn/child-wait/v1", continuation.spawn_operation_id
        )
        termination_started_at = float(checkpoint_value["started_at"])
        termination_last_observed_at = float(checkpoint_value["last_observed_at"])
        termination_policy_snapshot_hash = hashlib.sha256(
            canonical_json(
                {
                    "started_at": termination_started_at,
                    "last_observed_at": termination_last_observed_at,
                }
            ).encode()
        ).hexdigest()
        wait_identity: dict[str, JsonValue] = {
            "spawn_operation_id": continuation.spawn_operation_id,
            "parent_run_id": continuation.parent_run_id,
            "child_run_id": admission.receipt.run_id,
            "child_command_id": str(ticket_row["child_command_id"]),
            "parent_wait_receipt_id": parent_wait_receipt_id,
        }
        identity_hash = hashlib.sha256(canonical_json(wait_identity).encode()).hexdigest()
        lifecycle_hash = hashlib.sha256(
            canonical_json(
                {
                    **wait_identity,
                    "state": "unconsumed",
                    "parent_waiting_version": parent_waiting_version,
                    "react_checkpoint_hash": next_checkpoint_hash,
                    "context_post_revision": context.revision,
                }
            ).encode()
        ).hexdigest()
        tx.connection.execute(
            """
            INSERT INTO workflow_spawn_child_wait_receipts(
                parent_wait_receipt_id,spawn_operation_id,parent_run_id,child_run_id,
                child_command_id,parent_pre_version,parent_waiting_version,
                react_checkpoint_revision,react_checkpoint_hash,expected_signal_domain,
                source_phase,batch_digest,spawn_ordinal,next_tool_ordinal,
                prior_result_append_receipts_json,raw_tool_call_id,
                spawn_result_append_id,spawn_result_append_receipt_id,
                spawn_tool_message_hash,context_pre_revision,context_post_revision,
                released_runtime_lease_epoch,released_workflow_lease_epoch,
                termination_started_at,termination_last_observed_at,wall_deadline,
                termination_policy_snapshot_hash,released_run_fence_epoch,
                child_start_receipt_id,child_dispatch_claim_id,
                child_runtime_lease_epoch,wake_activation_receipt_id,
                state,version,identity_hash,lifecycle_hash,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,'tool_batch_reserved',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'unconsumed',0,?,?,?)
            """,  # noqa: E501
            (
                parent_wait_receipt_id,
                continuation.spawn_operation_id,
                continuation.parent_run_id,
                admission.receipt.run_id,
                str(ticket_row["child_command_id"]),
                int(parent["version"]),
                parent_waiting_version,
                next_checkpoint_version,
                next_checkpoint_hash,
                "child.terminal.v1",
                batch_digest,
                int(effect["call_ordinal"]),
                int(effect["call_ordinal"]) + 1,
                "[]",
                str(effect["raw_call_id"]),
                append_id,
                append_id,
                tool_result_hash,
                context_pre_revision,
                context.revision,
                continuation.runtime_lease_epoch,
                continuation.workflow_lease_epoch,
                termination_started_at,
                termination_last_observed_at,
                None,
                termination_policy_snapshot_hash,
                continuation.run_fence_epoch,
                admission.receipt.ticket_receipt_id,
                admission.dispatch_claim.claim_id,
                admission.activation.execution_lease.epoch,  # type: ignore[union-attr]
                activation_chain_head_id,
                identity_hash,
                lifecycle_hash,
                now,
            ),
        )
        _fault(fault, "workflow:spawn_admission:after_child_wait_write")
        self._record_spawn_admission_completion(
            tx,
            continuation=continuation,
            continuation_row=continuation_row,
            admission=admission,
            effect=effect,
            tool_result_json=tool_result_json,
            tool_result_hash=tool_result_hash,
            parent_wait_receipt_id=parent_wait_receipt_id,
            path_kind=path_kind,
            activation_chain_head_id=activation_chain_head_id,
            now=now,
            fault=fault,
        )
        tx.register_after_commit_fault("workflow:spawn_admission:after_commit")
        child_start_ref = ChildStartDispatchRef(
            admission.receipt.ticket_receipt_id,
            admission.dispatch_claim.claim_id,
            admission.receipt.run_id,
        )
        suspension = WorkflowChildWaitBinding(
            parent_run_id=continuation.parent_run_id,
            child_run_id=admission.receipt.run_id,
            child_command_id=str(ticket_row["child_command_id"]),
            parent_wait_receipt_id=parent_wait_receipt_id,
            expected_parent_version=parent_waiting_version,
            react_checkpoint_revision=next_checkpoint_version,
            expected_signal_domain="child.terminal.v1",
            source_phase="tool_batch_reserved",
            batch_digest=batch_digest,
            spawn_ordinal=int(effect["call_ordinal"]),
            next_tool_ordinal=int(effect["call_ordinal"]) + 1,
            spawn_result_append_receipt_id=append_id,
            context_revision=context.revision,
            termination_started_at=termination_started_at,
            termination_last_observed_at=termination_last_observed_at,
            wall_deadline=None,
            termination_policy_snapshot_hash=termination_policy_snapshot_hash,
        )
        control_kind = {
            RuntimeStartDisposition.START_NEW: WorkflowSpawnChildControlKind.START,
            RuntimeStartDisposition.START_ORPHAN: WorkflowSpawnChildControlKind.START,
            RuntimeStartDisposition.RECOVER_START: WorkflowSpawnChildControlKind.RECOVER,
            RuntimeStartDisposition.RECOVER_RESUME: WorkflowSpawnChildControlKind.RECOVER,
            RuntimeStartDisposition.ATTACH_CURRENT: WorkflowSpawnChildControlKind.ATTACH,
            RuntimeStartDisposition.FOREIGN_ACTIVE: WorkflowSpawnChildControlKind.WAITING,
            RuntimeStartDisposition.WAITING: WorkflowSpawnChildControlKind.WAITING,
            RuntimeStartDisposition.CANCEL_PENDING: WorkflowSpawnChildControlKind.CANCEL,
            RuntimeStartDisposition.TERMINAL: WorkflowSpawnChildControlKind.TERMINAL,
        }[admission.disposition]
        child_control = _create_workflow_spawn_child_control(kind=control_kind, admission=admission)
        return _create_workflow_spawn_tool_outcome(
            tool_result=tool_result,
            child_control=child_control,
            child_start_ref=child_start_ref,
            suspension=suspension,
        )

    def _write_spawn_child_wait_checkpoint(
        self,
        tx: _SqliteWorkflowTransaction,
        *,
        continuation: WorkflowSpawnContinuationClaim,
        admission: RuntimeStartAdmission,
        checkpoint: sqlite3.Row,
        checkpoint_value: dict[str, JsonValue],
        context: ContextSnapshot,
        effect: sqlite3.Row,
        now: float,
        fault: FaultHook | None,
    ) -> tuple[str, int]:
        next_checkpoint = dict(checkpoint_value)
        next_checkpoint.update(
            {
                "phase": "child_wait",
                "context_revision": context.revision,
                "tool_result_progress": int(effect["call_ordinal"]) + 1,
                "workflow_spawn_operation_id": continuation.spawn_operation_id,
                "workflow_spawn_child_run_id": admission.receipt.run_id,
            }
        )
        next_checkpoint_json = canonical_json(cast(JsonValue, next_checkpoint))
        next_checkpoint_hash = hashlib.sha256(next_checkpoint_json.encode()).hexdigest()
        next_checkpoint_version = int(checkpoint["version"]) + 1
        tx.connection.execute(
            "INSERT INTO"
            " workflow_checkpoints(checkpoint_id,run_id,namespace,checkpoint_json,checkpoint_hash,lease_epoch,version,created_at)"  # noqa: E501
            " VALUES(?,?,?,?,?,?,?,?)",
            (
                f"{continuation.parent_run_id}:react.termination.v1:{next_checkpoint_version}",
                continuation.parent_run_id,
                "react.termination.v1",
                next_checkpoint_json,
                next_checkpoint_hash,
                continuation.runtime_lease_epoch,
                next_checkpoint_version,
                now,
            ),
        )
        _fault(fault, "workflow:spawn_admission:after_checkpoint_write")
        return next_checkpoint_hash, next_checkpoint_version

    def _require_spawn_admission_parent(
        self,
        tx: _SqliteWorkflowTransaction,
        ticket: WorkflowLaunchTicket,
        continuation: WorkflowSpawnContinuationClaim,
        now: float,
    ) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row, sqlite3.Row | None, str, str | None]:
        continuation_row = tx.connection.execute(
            "SELECT * FROM workflow_spawn_continuations WHERE operation_id=?",
            (continuation.spawn_operation_id,),
        ).fetchone()
        if (
            continuation_row is None
            or str(continuation_row["state"]) != "claimed"
            or _workflow_spawn_continuation_claim(continuation_row) != continuation
            or continuation.expires_at <= now
            or continuation.ticket_receipt_id != ticket.ticket_receipt_id
        ):
            raise UnitOfWorkConflict("workflow spawn continuation claim differs")
        parent = tx.connection.execute(
            "SELECT * FROM runs WHERE run_id=?",
            (continuation.parent_run_id,),
        ).fetchone()
        runtime = tx.connection.execute(
            "SELECT * FROM workflow_leases WHERE run_id=? AND namespace=?",
            (continuation.parent_run_id, RUNTIME_LEASE_NAMESPACE),
        ).fetchone()
        fence = tx.connection.execute(
            "SELECT * FROM run_fences WHERE run_id=?",
            (continuation.parent_run_id,),
        ).fetchone()
        if (
            parent is None
            or str(parent["state"]) != RunState.RUNNING.value
            or runtime is None
            or fence is None
            or str(runtime["owner_id"]) != continuation.owner_id
            or int(runtime["epoch"]) != continuation.runtime_lease_epoch
            or float(runtime["expires_at"]) <= now
            or str(fence["state"]) != "active"
            or str(fence["owner_id"]) != continuation.owner_id
            or int(fence["runtime_lease_epoch"]) != continuation.runtime_lease_epoch
            or int(fence["epoch"]) != continuation.run_fence_epoch
        ):
            raise UnitOfWorkConflict("workflow spawn parent authority differs")
        active_activation = tx.connection.execute(
            "SELECT * FROM workflow_spawn_ready_activations "
            "WHERE spawn_operation_id=? AND state='active'",
            (continuation.spawn_operation_id,),
        ).fetchone()
        path_kind = "direct"
        activation_chain_head_id: str | None = None
        if active_activation is not None:
            durable_activation = _workflow_spawn_ready_activation(tx.connection, active_activation)
            if durable_activation.continuation_claim != continuation:
                raise UnitOfWorkConflict("workflow spawn recovery activation claim differs")
            ready = tx.connection.execute(
                "SELECT consumed_at FROM workflow_spawn_continuation_ready "
                "WHERE ready_receipt_id=?",
                (durable_activation.ready_receipt.ready_receipt_id,),
            ).fetchone()
            if ready is None or ready["consumed_at"] is None:
                raise UnitOfWorkConflict("workflow spawn recovery ready receipt is unconsumed")
            path_kind = "ready_recovery"
            activation_chain_head_id = durable_activation.activation_receipt_id
        return (
            continuation_row,
            parent,
            runtime,
            active_activation,
            path_kind,
            activation_chain_head_id,
        )

    def _suspend_parent_for_spawn_child(
        self,
        tx: _SqliteWorkflowTransaction,
        *,
        continuation: WorkflowSpawnContinuationClaim,
        parent: sqlite3.Row,
        active_activation: sqlite3.Row | None,
        now: float,
        fault: FaultHook | None,
    ) -> int:
        parent_waiting_version = int(parent["version"]) + 1
        changed = tx.connection.execute(
            "UPDATE runs SET state='waiting',version=version+1,updated_at=? WHERE run_id=? AND"
            " state='running' AND version=?",
            (now, continuation.parent_run_id, int(parent["version"])),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn parent WAITING CAS failed")
        _fault(fault, "workflow:spawn_admission:after_parent_write")
        runtime_deleted = tx.connection.execute(
            "DELETE FROM workflow_leases WHERE run_id=? AND namespace=? AND owner_id=? AND epoch=?",
            (
                continuation.parent_run_id,
                RUNTIME_LEASE_NAMESPACE,
                continuation.owner_id,
                continuation.runtime_lease_epoch,
            ),
        ).rowcount
        if runtime_deleted != 1:
            raise UnitOfWorkConflict("workflow spawn Runtime release CAS failed")
        if continuation.workflow_lease_epoch is not None:
            workflow_deleted = tx.connection.execute(
                "DELETE FROM workflow_leases WHERE run_id=? AND namespace<>? AND owner_id=? AND"
                " epoch=?",
                (
                    continuation.parent_run_id,
                    RUNTIME_LEASE_NAMESPACE,
                    continuation.owner_id,
                    continuation.workflow_lease_epoch,
                ),
            ).rowcount
            if workflow_deleted != 1:
                raise UnitOfWorkConflict("workflow spawn Workflow release CAS failed")
        changed = tx.connection.execute(
            "UPDATE run_fences SET state='released',released_at=? WHERE run_id=? AND owner_id=? AND"
            " runtime_lease_epoch=? AND epoch=? AND state='active'",
            (
                now,
                continuation.parent_run_id,
                continuation.owner_id,
                continuation.runtime_lease_epoch,
                continuation.run_fence_epoch,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn RunFence release CAS failed")
        _fault(fault, "workflow:spawn_admission:after_authority_release")
        if active_activation is not None:
            consumed_version = int(active_activation["version"]) + 1
            consumed_hash = _workflow_spawn_activation_hash(
                activation_receipt_id=str(active_activation["activation_receipt_id"]),
                ready_receipt_id=str(active_activation["ready_receipt_id"]),
                spawn_operation_id=str(active_activation["spawn_operation_id"]),
                parent_run_id=str(active_activation["parent_run_id"]),
                effect_id=str(active_activation["effect_id"]),
                owner_id=str(active_activation["owner_id"]),
                runtime_lease_epoch=int(active_activation["runtime_lease_epoch"]),
                run_fence_epoch=int(active_activation["run_fence_epoch"]),
                workflow_lease_epoch=(
                    None
                    if active_activation["workflow_lease_epoch"] is None
                    else int(active_activation["workflow_lease_epoch"])
                ),
                continuation_claim_epoch=int(active_activation["continuation_claim_epoch"]),
                predecessor_activation_receipt_id=(
                    None
                    if active_activation["predecessor_activation_receipt_id"] is None
                    else str(active_activation["predecessor_activation_receipt_id"])
                ),
                version=consumed_version,
            )
            consumed = tx.connection.execute(
                "UPDATE workflow_spawn_ready_activations "
                "SET state='consumed',version=?,canonical_hash=?,consumed_at=? "
                "WHERE activation_receipt_id=? AND state='active' AND version=?",
                (
                    consumed_version,
                    consumed_hash,
                    now,
                    str(active_activation["activation_receipt_id"]),
                    int(active_activation["version"]),
                ),
            ).rowcount
            if consumed != 1:
                raise UnitOfWorkConflict("workflow spawn recovery activation consume CAS failed")
            _fault(fault, "workflow:spawn_admission:after_activation_write")
        return parent_waiting_version

    def _record_spawn_admission_completion(
        self,
        tx: _SqliteWorkflowTransaction,
        *,
        continuation: WorkflowSpawnContinuationClaim,
        continuation_row: sqlite3.Row,
        admission: RuntimeStartAdmission,
        effect: sqlite3.Row,
        tool_result_json: str,
        tool_result_hash: str,
        parent_wait_receipt_id: str,
        path_kind: str,
        activation_chain_head_id: str | None,
        now: float,
        fault: FaultHook | None,
    ) -> None:
        changed = tx.connection.execute(
            "UPDATE execution_effects SET"
            " state='succeeded',result_json=?,settled_at=?,version=version+1 WHERE effect_id=? AND"
            " state IN ('handed_off','unknown') AND version=?",
            (tool_result_json, now, str(effect["effect_id"]), int(effect["version"])),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn Effect settlement CAS failed")
        completion_receipt_id = self._derived_id(
            "workflow-spawn/completion/v1", continuation.spawn_operation_id
        )
        completion_values: dict[str, object] = {
            "completion_receipt_id": completion_receipt_id,
            "spawn_operation_id": continuation.spawn_operation_id,
            "ticket_receipt_id": continuation.ticket_receipt_id,
            "parent_run_id": continuation.parent_run_id,
            "path_kind": path_kind,
            "effect_id": str(effect["effect_id"]),
            "handoff_attempt": int(effect["handoff_attempt"]),
            "effect_request_hash": str(effect["request_hash"]),
            "issue_authority_hash": str(continuation_row["issue_authority_hash"]),
            "tool_result_json": tool_result_json,
            "tool_result_hash": tool_result_hash,
            "child_runtime_start_receipt_id": admission.receipt.ticket_receipt_id,
            "failure_evidence_kind": None,
            "failure_evidence_id": None,
            "failure_evidence_json": None,
            "failure_evidence_hash": None,
            "activation_chain_head_id": activation_chain_head_id,
            "child_wait_receipt_id": parent_wait_receipt_id,
            "created_at": now,
        }
        completion_hash = _workflow_spawn_completion_hash(completion_values)
        tx.connection.execute(
            """
            INSERT INTO workflow_spawn_completion_receipts(
                completion_receipt_id,spawn_operation_id,ticket_receipt_id,parent_run_id,
                path_kind,effect_id,handoff_attempt,effect_request_hash,
                issue_authority_hash,tool_result_json,tool_result_hash,
                child_runtime_start_receipt_id,failure_evidence_kind,
                failure_evidence_id,failure_evidence_json,failure_evidence_hash,
                activation_chain_head_id,child_wait_receipt_id,canonical_hash,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,?,?,?,?)
            """,
            (
                completion_receipt_id,
                continuation.spawn_operation_id,
                continuation.ticket_receipt_id,
                continuation.parent_run_id,
                path_kind,
                str(effect["effect_id"]),
                int(effect["handoff_attempt"]),
                str(effect["request_hash"]),
                str(continuation_row["issue_authority_hash"]),
                tool_result_json,
                tool_result_hash,
                admission.receipt.ticket_receipt_id,
                activation_chain_head_id,
                parent_wait_receipt_id,
                completion_hash,
                now,
            ),
        )
        changed = tx.connection.execute(
            "UPDATE workflow_spawn_continuations SET"
            " state='completed',completion_receipt_id=?,completion_path_kind=?,version=version+1,updated_at=?"  # noqa: E501
            " WHERE operation_id=? AND state='claimed' AND version=?",
            (
                completion_receipt_id,
                path_kind,
                now,
                continuation.spawn_operation_id,
                continuation.version,
            ),
        ).rowcount
        if changed != 1:
            raise UnitOfWorkConflict("workflow spawn completion CAS failed")
        _fault(fault, "workflow:spawn_admission:after_completion_write")
