# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5b Task 3 ``task_scope_update`` — the model-facing TaskScope semantic closure Tool.

Registration (host-composed like ``context_route``): direct kernel, projectless
safe, SDK execution policy ``(non_project_effect, required, required)``.  The
Tool is exposed on **every** provider turn; "only when dirty" is a *handler*
gate, never a visibility change (a hidden-Tool call is a whole-Run fault in
the frozen SDK).

Strict schema (design-freeze §7).  The model fills ``outcome``,
``base_revision``, ``operations[]``, ``closure_reason``, ``evidence_refs[]``
(evidence ids) and ``idempotency_key``; the Host fills ``plan_id =
sha256(idempotency_key + task_scope_id)``, ``run_id``, ``subject``,
``task_scope_id`` (the Run's *admission* scope), ``source_turn_id`` and the
``disclosure_context``.

Handler order and stable reason codes:

1. standalone route (latest durable route decision of the Run carries no
   TaskScope) → ``task_scope_update_scope_unbound``
2. payload shape / DTO validation → ``task_scope_update_payload_invalid``
3. no material dirt and no pending receipt → ``task_scope_update_nothing_to_close``
   (revision untouched, so the model's ``base_revision`` cannot drift)
4. evidence refs not linked to the scope → ``task_scope_update_refs_outside_scope``
5. status transition table → ``task_scope_update_after_complete`` /
   ``task_scope_update_illegal_transition``
6. ``apply_mutation_plan`` **and** the closure receipt in one transaction;
   ``mutation_base_revision_conflict`` passes through as a retryable failure.

Every rejection writes ``host_pre_admission_audit(payload_kind='task_scope_update')``
at the rejection point.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from deskpet.task_scope.protocol import (
    TaskScopeProtocolError,
    canonical_hash,
    canonical_json,
    identifier,
)
from deskpet.task_scope.store import (
    CanonicalTaskScopeStore,
    TaskScopeConflict,
    TaskScopeNotFound,
)

TASK_SCOPE_UPDATE_TOOL_NAME = "task_scope_update"
HOST_TASK_SCOPE_UPDATE_AUTHORITY_REF = "host:task-scope-update:v1"
MODEL_CLOSURE_REASON_CODE = "model_closure"

_MUTATION_KINDS: tuple[str, ...] = (
    "goal.set", "goal.revise", "scope.include", "scope.exclude",
    "decision.record", "decision.supersede", "plan.step.add",
    "plan.step.revise", "plan.step.cancel", "plan.reorder", "task.pause",
    "task.block", "task.resume", "task.complete", "resume.update",
    "checkpoint.request", "relation.add",
)
_STATUS_KINDS: frozenset[str] = frozenset({"task.pause", "task.block", "task.resume", "task.complete"})
_COMPLETE_STATUSES: frozenset[str] = frozenset({"complete", "completed"})
# design-freeze §7: {draft,active,in_progress} --status.update--> {active,in_progress,paused,blocked}
_PAUSABLE_FROM: frozenset[str] = frozenset({"draft", "active", "in_progress", "open"})
_STATUS_AFTER: dict[str, str] = {
    "task.pause": "paused",
    "task.block": "blocked",
    "task.resume": "active",
    "task.complete": "complete",
}
_MAX_REFS = 64
_MAX_OPERATIONS = 32

TASK_SCOPE_UPDATE_DESCRIPTION = (
    "Submit the TaskScope semantic closure for this turn (call at most once, "
    "before your final answer, only after real project effects happened): "
    "either outcome=mutate with a plan of operations describing what "
    "materially changed (goal / plan steps / decisions / status / next "
    "action), or outcome=no_mutation with a closure_reason. Every operation "
    "and the plan itself must cite evidence_refs from the closure instruction's "
    "allowed_evidence_refs; base_revision must equal the current TaskScope "
    "revision. Rejected with a stable code when nothing needs closing or the "
    "Run is not routed to a task."
)

TASK_SCOPE_UPDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["outcome", "base_revision", "evidence_refs", "idempotency_key"],
    "properties": {
        "outcome": {"type": "string", "enum": ["mutate", "no_mutation"]},
        "base_revision": {"type": "integer", "minimum": 1},
        "closure_reason": {"type": "string", "minLength": 1, "maxLength": 4096},
        "evidence_refs": {
            "type": "array",
            "minItems": 1,
            "maxItems": _MAX_REFS,
            "items": {"type": "string", "minLength": 1, "maxLength": 512},
        },
        "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 256},
        "operations": {
            "type": "array",
            "maxItems": _MAX_OPERATIONS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["operation_id", "kind", "value", "reason_code", "evidence_refs"],
                "properties": {
                    "operation_id": {"type": "string", "minLength": 1, "maxLength": 256},
                    "kind": {"type": "string", "enum": list(_MUTATION_KINDS)},
                    "value": {"type": "string", "minLength": 1, "maxLength": 32768},
                    "reason_code": {"type": "string", "minLength": 1, "maxLength": 256},
                    "evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": _MAX_REFS,
                        "items": {"type": "string", "minLength": 1, "maxLength": 512},
                    },
                },
            },
        },
    },
}


def derive_plan_id(idempotency_key: str, task_scope_id: str) -> str:
    """Host-derived ``plan_id`` = sha256(idempotency_key + task_scope_id) (design-freeze §7)."""

    return hashlib.sha256(f"{idempotency_key}{task_scope_id}".encode()).hexdigest()


async def write_pre_admission_audit_tx(
    db: aiosqlite.Connection,
    *,
    sdk_run_id: str | None,
    payload_kind: str,
    reason_code: str,
    payload_hash: str,
    now: float,
) -> str:
    """Shared tiny writer for ``host_pre_admission_audit`` (Task 5 reuses it)."""

    audit_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO host_pre_admission_audit(audit_id,sdk_run_id,payload_kind,reason_code,payload_hash,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (audit_id, sdk_run_id, payload_kind, reason_code, payload_hash, float(now)),
    )
    return audit_id


class ClosureRejected(Exception):
    """Internal control flow: one stable reason code + optional detail."""

    def __init__(self, code: str, *, retryable: bool = False, **detail: Any) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.detail = dict(detail)


@dataclass(frozen=True, slots=True)
class ClosureApplyResult:
    accepted: bool
    error_code: str | None = None
    retryable: bool = False
    detail: Mapping[str, Any] | None = None
    receipt: Any = None  # deskpet.execution.semantic_closure.ClosureReceipt
    committed_revision: int | None = None
    replayed: bool = False

    def as_tool_value(self) -> dict[str, Any]:
        assert self.accepted and self.receipt is not None
        return {
            "ok": True,
            "closure_receipt": self.receipt.to_json(),
            "committed_revision": self.committed_revision,
            "replayed": self.replayed,
        }


class TaskScopeUpdateService:
    """Host adjudication of one ``task_scope_update`` payload (Tool path and fallback path)."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        tool_context_getter: Callable[[], Any],
        route_ledger: Any,
        clock: Callable[[], float] = time.time,
        fault_inject: Callable[[str], None] | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._store = CanonicalTaskScopeStore(db_path)
        self._tool_context_getter = tool_context_getter
        self._route_ledger = route_ledger
        self._clock = clock
        self._fault_inject = fault_inject

    # -- Tool path ---------------------------------------------------------

    async def handle_task_scope_update(self, arguments: Mapping[str, Any]) -> Any:
        from simple_harness.tools import ToolResult

        context = self._tool_context_getter()
        if context is None:
            raise RuntimeError("task_scope_update invoked outside SDK ToolRegistry")
        run_id = str(context.run_id.value)
        call_id = context.call_id
        if call_id is None:
            raise RuntimeError("task_scope_update_call_identity_missing")
        envelope = getattr(context, "task_execution_envelope", None)
        turn_ordinal = max(1, int(getattr(envelope, "turn_ordinal", 1) or 1))
        payload = {k: v for k, v in dict(arguments).items() if k != "deskpet_public_progress"}
        try:
            scope = await self._admission_scope(run_id)
            result = await self.apply_closure(
                payload,
                run_id=run_id,
                host_run_id=scope.host_run_id,
                task_scope_id=scope.task_scope_id,
                subject=scope.subject,
                source_turn_id=f"sdk-run:{run_id}:turn:{turn_ordinal}",
                reason_code=MODEL_CLOSURE_REASON_CODE,
            )
        except (TaskScopeProtocolError, TaskScopeNotFound) as exc:
            # Task 3 review F-5: Host protocol/lookup errors inside the handler are
            # stable rejections with an audit row, never ``tool_handler_failed``.
            code = (
                "task_scope_update_scope_unbound"
                if isinstance(exc, TaskScopeNotFound)
                else "task_scope_update_payload_invalid"
            )
            await self._audit(run_id, code, payload)
            return ToolResult.rejected(call_id, code, f"task_scope_update rejected: {code} {str(exc)[:128]}")
        except ClosureRejected as rejected:
            await self._audit(run_id, rejected.code, payload)
            message = f"task_scope_update rejected: {rejected.code}"
            if rejected.detail:
                message += " " + canonical_json(rejected.detail)
            if rejected.retryable:
                return ToolResult.failed(call_id, rejected.code, message, retryable=True)
            return ToolResult.rejected(call_id, rejected.code, message)
        if not result.accepted:
            await self._audit(run_id, str(result.error_code), payload)
            message = f"task_scope_update rejected: {result.error_code}"
            if result.detail:
                message += " " + canonical_json(dict(result.detail))
            if result.retryable:
                return ToolResult.failed(call_id, str(result.error_code), message, retryable=True)
            return ToolResult.rejected(call_id, str(result.error_code), message)
        return result.as_tool_value()

    @dataclass(frozen=True, slots=True)
    class _Scope:
        task_scope_id: str
        subject: str
        host_run_id: str

    async def _admission_scope(self, run_id: str) -> TaskScopeUpdateService._Scope:
        """Admission scope of the Run; standalone route → ``task_scope_update_scope_unbound``."""

        from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress

        route = None
        if self._route_ledger is not None:
            route = await self._route_ledger.latest_route_decision_for_run(run_id)
        if route is None or not route.get("task_scope_id"):
            raise ClosureRejected("task_scope_update_scope_unbound")
        ingress = ExecutionEvidenceIngress(self._db_path)
        binding = await ingress.resolve_run_scope(run_id)
        if binding is not None:
            return self._Scope(binding.task_scope_id, binding.subject, binding.host_run_id)
        scope_id = str(route["task_scope_id"])
        subject = await ingress.scope_subject(scope_id)
        if subject is None:
            raise ClosureRejected("task_scope_update_scope_unbound")
        return self._Scope(scope_id, subject, f"unbound:{run_id}")

    async def _audit(self, run_id: str, reason_code: str, payload: Mapping[str, Any]) -> None:
        async with self._store._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await write_pre_admission_audit_tx(
                    db,
                    sdk_run_id=run_id,
                    payload_kind="task_scope_update",
                    reason_code=reason_code,
                    payload_hash=canonical_hash(_json_safe(payload)),
                    now=self._clock(),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    # -- shared core (Tool path + ClosureFallback) --------------------------

    async def apply_closure(
        self,
        arguments: Mapping[str, Any],
        *,
        run_id: str,
        host_run_id: str,
        task_scope_id: str,
        subject: str,
        source_turn_id: str,
        reason_code: str,
        plan_id: str | None = None,
        attempt_id: str | None = None,
        require_dirty: bool = True,
        commit_extension: Callable[[aiosqlite.Connection], Any] | None = None,
    ) -> ClosureApplyResult:
        """Validate one closure payload and apply it with its receipt in one transaction.

        Raises :class:`ClosureRejected` for every stable rejection (the Tool
        path turns it into ``ToolResult``; the fallback records it as pending).
        """

        from simple_harness import DisclosureContext, EvidenceRef
        from simple_harness.runtime.task_scope_protocol import (
            TaskScopeMutationOperation,
            TaskScopeMutationOutcome,
            TaskScopeMutationPlan,
        )

        from deskpet.execution.semantic_closure import (
            dirty_state_tx,
            pending_receipts_tx,
            write_closure_receipt_tx,
        )

        payload = _validate_shape(arguments)
        async with self._store._connection() as db:
            head = await self._store._fetchone(
                db,
                "SELECT h.current_revision,h.event_watermark,r.state_json,s.subject "
                "FROM task_scope_heads h JOIN task_scopes s ON s.task_scope_id=h.task_scope_id "
                "JOIN task_scope_canonical_revisions r ON r.task_scope_id=h.task_scope_id "
                "AND r.revision=h.current_revision WHERE h.task_scope_id=?",
                (task_scope_id,),
            )
            if head is None or str(head["subject"]) != subject:
                raise ClosureRejected("task_scope_update_scope_unbound")
            derived_plan_id = plan_id or derive_plan_id(str(payload["idempotency_key"]), task_scope_id)
            applied_before = await self._store._fetchone(
                db,
                "SELECT 1 FROM task_scope_mutation_attempts WHERE plan_id=? AND result='applied'",
                (derived_plan_id,),
            )
            if require_dirty and applied_before is None:
                dirty = await dirty_state_tx(db, task_scope_id)
                pending = await pending_receipts_tx(db, task_scope_id)
                if not dirty.is_dirty and not pending:
                    raise ClosureRejected("task_scope_update_nothing_to_close")
            linked = await db.execute(
                "SELECT DISTINCT evidence_id,content_hash FROM task_scope_evidence_links WHERE task_scope_id=?",
                (task_scope_id,),
            )
            link_rows = await linked.fetchall()
            await linked.close()
        content_hash_by_id = {str(row["evidence_id"]): str(row["content_hash"]) for row in link_rows}
        state = json.loads(str(head["state_json"]))
        status = str(state.get("status") or "active")
        if applied_before is None:
            # An idempotent replay of an already-applied plan is judged by the
            # store (plan_hash), not by the status it has since produced.
            _check_transitions(status, payload)
        outside = sorted(
            {ref for ref in payload["evidence_refs"] if ref not in content_hash_by_id}
            | {ref for op in payload["operations"] for ref in op["evidence_refs"] if ref not in content_hash_by_id}
        )
        if outside:
            raise ClosureRejected("task_scope_update_refs_outside_scope", refs=outside[:8])

        def refs(ids: Sequence[str]) -> tuple[EvidenceRef, ...]:
            unique = list(dict.fromkeys(ids))
            return tuple(EvidenceRef(ref, content_hash_by_id[ref], ordinal) for ordinal, ref in enumerate(unique, 1))

        try:
            plan = TaskScopeMutationPlan(
                plan_id=derived_plan_id,
                run_id=run_id,
                subject=subject,
                task_scope_id=task_scope_id,
                base_revision=int(payload["base_revision"]),
                outcome=TaskScopeMutationOutcome(payload["outcome"]),
                operations=tuple(
                    TaskScopeMutationOperation(
                        operation_id=str(op["operation_id"]),
                        kind=str(op["kind"]),  # type: ignore[arg-type]
                        value=str(op["value"]),
                        evidence_refs=refs(op["evidence_refs"]),
                        reason_code=str(op["reason_code"]),
                    )
                    for op in payload["operations"]
                ),
                closure_reason=payload.get("closure_reason"),
                source_turn_id=source_turn_id,
                disclosure_context=_disclosure(DisclosureContext, run_id, subject),
                evidence_refs=refs(payload["evidence_refs"]),
                idempotency_key=str(payload["idempotency_key"]),
            )
        except (TypeError, ValueError) as exc:
            raise ClosureRejected("task_scope_update_payload_invalid", message=str(exc)[:256]) from exc

        captured: dict[str, Any] = {}

        async def commit_hook(hook_db: aiosqlite.Connection, receipt: Any, replayed: bool) -> None:
            from deskpet.execution.semantic_closure import closure_receipt_for_plan_tx

            # Task 3 review F-1: an idempotent replay returns the receipt written with
            # the decision — never a second receipt at the *current* head watermark
            # (which would silently cover material events that landed in between).
            existing = await closure_receipt_for_plan_tx(hook_db, task_scope_id=task_scope_id, plan_id=derived_plan_id)
            if existing is not None:
                captured["receipt"] = existing
                captured["replayed"] = replayed
                if commit_extension is not None:
                    await commit_extension(hook_db)
                return
            # First write: the watermark is the decision's own revision watermark
            # (the mutation.plan event sequence, same transaction as apply), so the
            # receipt covers exactly the events ≤ that watermark.
            watermark_row = await self._store._fetchone(
                hook_db,
                "SELECT r.event_watermark,d.created_at FROM task_scope_mutation_decisions d "
                "JOIN task_scope_canonical_revisions r ON r.decision_id=d.decision_id "
                "WHERE d.task_scope_id=? AND d.plan_id=?",
                (task_scope_id, derived_plan_id),
            )
            assert watermark_row is not None
            closure_receipt = await write_closure_receipt_tx(
                hook_db,
                task_scope_id=task_scope_id,
                sdk_run_id=run_id,
                host_run_id=host_run_id,
                closure_watermark=int(watermark_row["event_watermark"]),
                outcome=str(payload["outcome"]),
                plan_id=derived_plan_id,
                reason_code=reason_code,
                attempt_id=attempt_id,
                # same `now` as the decision row: one transaction, one instant.
                now=float(watermark_row["created_at"]),
            )
            if commit_extension is not None:
                await commit_extension(hook_db)
            captured["receipt"] = closure_receipt
            captured["replayed"] = replayed
            if self._fault_inject is not None:
                self._fault_inject("semantic-closure-commit")

        try:
            applied = await self._store.apply_mutation_plan(plan, commit_hook=commit_hook)
        except TaskScopeConflict as exc:
            code = str(exc)
            if code == "mutation_base_revision_conflict":
                raise ClosureRejected(
                    code, retryable=True, current_revision=int(head["current_revision"])
                ) from exc
            raise ClosureRejected(code) from exc
        return ClosureApplyResult(
            accepted=True,
            receipt=captured["receipt"],
            committed_revision=applied.committed_revision,
            replayed=bool(captured.get("replayed", False)),
        )


def _disclosure(cls: Any, run_id: str, subject: str) -> Any:
    from simple_harness import (
        DeliveryRecipient,
        DisclosureGeneration,
        DisclosurePurpose,
        DisclosureReasonCode,
        DisclosureSource,
        DisclosureTrust,
        IntendedAudience,
    )

    return cls(
        run_id=run_id,
        subject=subject,
        recipient=DeliveryRecipient.USER_SELF,
        recipient_id=subject,
        intended_audience=IntendedAudience.USER_SELF,
        purpose=DisclosurePurpose.TASK_EXECUTION,
        source=DisclosureSource.AUTHENTICATED_HOST,
        trust=DisclosureTrust.TRUSTED_AUTHORITY,
        generation=DisclosureGeneration.CURRENT,
        authority_ref=HOST_TASK_SCOPE_UPDATE_AUTHORITY_REF,
        reason_codes=(DisclosureReasonCode.MINIMUM_NECESSARY,),
    )


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return {"unserializable": True}


def _string_list(value: Any, name: str, *, required: bool) -> list[str]:
    if not isinstance(value, (list, tuple)) or (required and not value) or len(value) > _MAX_REFS:
        raise ClosureRejected("task_scope_update_payload_invalid", field=name)
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item.encode("utf-8")) > 512:
            raise ClosureRejected("task_scope_update_payload_invalid", field=name)
        items.append(item)
    return items


def _validate_shape(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Strict structural validation mirroring the schema (defense in depth)."""

    if not isinstance(arguments, Mapping):
        raise ClosureRejected("task_scope_update_payload_invalid", field="payload")
    allowed = set(TASK_SCOPE_UPDATE_SCHEMA["properties"])
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise ClosureRejected("task_scope_update_payload_invalid", field=unknown[0])
    outcome = arguments.get("outcome")
    if outcome not in {"mutate", "no_mutation"}:
        raise ClosureRejected("task_scope_update_payload_invalid", field="outcome")
    base_revision = arguments.get("base_revision")
    if isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 1:
        raise ClosureRejected("task_scope_update_payload_invalid", field="base_revision")
    key = arguments.get("idempotency_key")
    # Task 3 review F-5: the 256 limit is bytes (the identifier contract), not
    # characters — a 200-CJK-character key must be a stable payload rejection,
    # never a ``tool_handler_failed`` escaping from ``identifier()``.
    if not isinstance(key, str) or not key.strip() or len(key.encode("utf-8")) > 256:
        raise ClosureRejected("task_scope_update_payload_invalid", field="idempotency_key")
    closure_reason = arguments.get("closure_reason")
    if closure_reason is not None and (
        not isinstance(closure_reason, str) or not closure_reason.strip() or len(closure_reason.encode("utf-8")) > 4096
    ):
        raise ClosureRejected("task_scope_update_payload_invalid", field="closure_reason")
    if outcome == "no_mutation" and closure_reason is None:
        raise ClosureRejected("task_scope_update_payload_invalid", field="closure_reason", message="no_mutation requires closure_reason")
    operations_raw = arguments.get("operations", [])
    if operations_raw is None:
        operations_raw = []
    if not isinstance(operations_raw, (list, tuple)) or len(operations_raw) > _MAX_OPERATIONS:
        raise ClosureRejected("task_scope_update_payload_invalid", field="operations")
    if (outcome == "mutate") != bool(operations_raw):
        raise ClosureRejected("task_scope_update_payload_invalid", field="operations", message="mutate requires operations; no_mutation forbids them")
    operations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in operations_raw:
        if not isinstance(raw, Mapping) or set(raw) != {"operation_id", "kind", "value", "reason_code", "evidence_refs"}:
            raise ClosureRejected("task_scope_update_payload_invalid", field="operations")
        operation_id = raw.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id.strip() or operation_id in seen:
            raise ClosureRejected("task_scope_update_payload_invalid", field="operation_id")
        seen.add(operation_id)
        kind = raw.get("kind")
        if kind not in _MUTATION_KINDS:
            raise ClosureRejected("task_scope_update_payload_invalid", field="kind")
        value = raw.get("value")
        if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 32_768:
            raise ClosureRejected("task_scope_update_payload_invalid", field="value")
        reason = raw.get("reason_code")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 256:
            raise ClosureRejected("task_scope_update_payload_invalid", field="reason_code")
        operations.append(
            {
                "operation_id": operation_id,
                "kind": kind,
                "value": value,
                "reason_code": reason,
                "evidence_refs": _string_list(raw.get("evidence_refs"), "operations.evidence_refs", required=True),
            }
        )
    for name in ("outcome", "base_revision", "idempotency_key"):
        try:
            identifier(str(arguments[name]), name, 512)
        except TaskScopeProtocolError as exc:
            raise ClosureRejected("task_scope_update_payload_invalid", field=name, message=str(exc)[:128]) from exc
    return {
        "outcome": outcome,
        "base_revision": int(base_revision),
        "closure_reason": closure_reason,
        "evidence_refs": _string_list(arguments.get("evidence_refs"), "evidence_refs", required=True),
        "idempotency_key": key,
        "operations": operations,
    }


def _check_transitions(status: str, payload: Mapping[str, Any]) -> None:
    """design-freeze §7 status transition table over the operations, in order."""

    current = status
    for operation in payload["operations"]:
        kind = str(operation["kind"])
        if current in _COMPLETE_STATUSES and (kind in _STATUS_KINDS or kind.startswith("plan.")):
            raise ClosureRejected("task_scope_update_after_complete", status=current, kind=kind)
        if kind in {"task.pause", "task.block"} and current not in _PAUSABLE_FROM:
            raise ClosureRejected("task_scope_update_illegal_transition", status=current, kind=kind)
        if kind in _STATUS_AFTER:
            current = _STATUS_AFTER[kind]


__all__ = [
    "HOST_TASK_SCOPE_UPDATE_AUTHORITY_REF",
    "MODEL_CLOSURE_REASON_CODE",
    "TASK_SCOPE_UPDATE_DESCRIPTION",
    "TASK_SCOPE_UPDATE_SCHEMA",
    "TASK_SCOPE_UPDATE_TOOL_NAME",
    "ClosureApplyResult",
    "ClosureRejected",
    "TaskScopeUpdateService",
    "derive_plan_id",
    "write_pre_admission_audit_tx",
]
