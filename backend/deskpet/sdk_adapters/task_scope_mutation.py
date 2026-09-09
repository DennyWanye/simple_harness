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
3. ``outcome=no_mutation`` with no material dirt and no pending receipt →
   ``task_scope_update_nothing_to_close`` (revision untouched, so the model's
   ``base_revision`` cannot drift).  ``outcome=mutate`` is **never** gated on
   dirt: the mutation plan is itself the material change (2026-09-08 A6 事件 C,
   see ``plans/2026-09-08-hm-to-a6/DECISION-CLOSURE-DIRT-AND-PROTOCOL-RETRY.md``)
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

# What the model must do differently after ``task_scope_update_nothing_to_close``.
# Rendered into the rejection message, so it names both the missing precondition
# and the payload that *is* accepted on a clean scope.
_NOTHING_TO_CLOSE_GUIDANCE = (
    "outcome=no_mutation declares that objective effects needed closing and "
    "nothing changed, but this TaskScope has no material event since the last "
    "closure (file write / shell / test / project-effect tool) and no pending "
    "closure receipt, so there is nothing to declare closed. To record what the "
    "user stated, send outcome=mutate with the matching operations instead "
    "(goal.set / goal.revise / decision.record / plan.step.* / task.* / "
    "resume.update); a mutate plan is accepted on a clean scope."
)

# How many admissible evidence ids the ``refs_outside_scope`` rejection discloses.
# Ids only (opaque uuids), never payloads; the same list the closure instruction
# already publishes as ``allowed_evidence_refs``.
_MAX_DISCLOSED_REFS = 16
# Identical ``refs_outside_scope`` rejections inside one Run before the Host
# escalates the guidance (HM-TO-A6 incident U: seven identical rejections).
_REFS_ESCALATION_AFTER = 2

_REFS_OUTSIDE_SCOPE_NEXT_STEP = (
    "evidence_refs (and every operation's evidence_refs) must be evidence ids the "
    "Host has already admitted for this task. They are opaque ids you cannot "
    "construct or guess: never send a run id, call id, effect id, "
    "envelope/receipt/binding id, memory id, content hash, or any prefixed form "
    "of them. You never send a content_hash — the Host resolves it. Re-send this "
    "same task_scope_update with evidence_refs and each operation's evidence_refs "
    "drawn from allowed_evidence_refs below (or from a closure instruction's "
    "allowed_evidence_refs, if you were given one)."
)
_REFS_OUTSIDE_SCOPE_CURRENT_TURN = (
    " current_turn_evidence_ref is this turn's own user message: cite exactly "
    "that id when you record what the user just stated (goal.set / goal.revise "
    "/ decision.record)."
)
_REFS_OUTSIDE_SCOPE_EMPTY = (
    "The Host has admitted no evidence for this task yet, so no task_scope_update "
    "payload can be accepted in this Run — evidence_refs requires at least one id "
    "and the admissible set is empty. Do not retry with other ids and do not try "
    "to derive one. State the user's content in your final answer instead; the "
    "Host closes the task archive itself at the end of the Run."
)
_REFS_OUTSIDE_SCOPE_ESCALATION = (
    "You have now been rejected with task_scope_update_refs_outside_scope "
    "{count} times in this Run, each time with ids that are not in the "
    "admissible set. Stop composing new ids. Either send the payload using "
    "allowed_evidence_refs verbatim, or stop calling task_scope_update and "
    "finish your answer to the user."
)

TASK_SCOPE_UPDATE_DESCRIPTION = (
    "Submit the TaskScope semantic closure for this turn (call at most once, "
    "before your final answer, only after real project effects happened): "
    "either outcome=mutate with a plan of operations describing what "
    "materially changed (goal / plan steps / decisions / status / next "
    "action), or outcome=no_mutation with a closure_reason. Every operation "
    "and the plan itself must cite evidence_refs the Host already admitted: "
    "the closure instruction's allowed_evidence_refs, or the "
    "current_turn_evidence_ref the accepted context_route published for this "
    "turn — never invent an id from a run id, call id, effect id or content "
    "hash, and never send a content_hash (the Host resolves it). "
    "base_revision must equal the current TaskScope "
    "revision. Rejected with a stable code when nothing needs closing or the "
    "Run is not routed to a task."
    " Turn closure is not whole-task completion. A successful tool or file creation "
    "does not satisfy an unperformed readback/check requested by the user. "
    "Use task.complete only when all original goal obligations are fulfilled; "
    "otherwise preserve unfinished obligations and record progress/next steps. "
    "Do not narrow the goal to a completed substep. A completed TaskScope cannot "
    "be reopened by context_route or task.resume."
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


@dataclass(frozen=True, slots=True)
class _AdmissibleRefs:
    """The evidence ids one closure may cite, newest first, plus this turn's own."""

    ordered: tuple[tuple[str, str], ...]
    turn_ref: str | None


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
            repeats = await self._audit(run_id, rejected.code, payload)
            detail = dict(rejected.detail)
            if (
                rejected.code == "task_scope_update_refs_outside_scope"
                and repeats > _REFS_ESCALATION_AFTER
                # Review SHOULD-FIX 1: with an empty admissible set ``next_step``
                # already says "no payload can be accepted, do not retry".  Adding
                # "or send it using allowed_evidence_refs" there would tell the
                # model to retry with an empty list — the opposite of the terminus.
                and detail.get("allowed_evidence_refs")
            ):
                # HM-TO-A6 incident U: the model re-guessed ids seven times and
                # burned the Run.  The gate never softens — the escalation only
                # tells it, once the audit trail proves the loop, to stop.
                detail["escalation"] = _REFS_OUTSIDE_SCOPE_ESCALATION.format(count=repeats)
            message = f"task_scope_update rejected: {rejected.code}"
            if detail:
                message += " " + canonical_json(detail)
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

    async def _audit(self, run_id: str, reason_code: str, payload: Mapping[str, Any]) -> int:
        """Write the rejection's audit row; return how many this Run now has for that code.

        The count is read inside the same ``BEGIN IMMEDIATE`` that wrote the row,
        so the bounded hint escalation below is a deterministic function of the
        audit trail itself — no new state, and every escalation is evidenced.
        """

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
                row = await self._store._fetchone(
                    db,
                    "SELECT COUNT(*) AS repeats FROM host_pre_admission_audit "
                    "WHERE sdk_run_id=? AND payload_kind='task_scope_update' AND reason_code=?",
                    (run_id, reason_code),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return int(row["repeats"]) if row is not None else 1

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
            # The dirt gate is an *effect-closure* gate.  It descends from the
            # frozen plan's "expose the closure Tool only when the scope is
            # dirty/pending" rule, which had to become a handler gate because a
            # hidden-Tool call is a whole-Run fault (challenge synthesis
            # ``task-scope-update-projectless-safe-vs-dirty-exposure``).  It was
            # never a write authority over the canonical archive: design-freeze
            # §2 makes ``host.turn`` *trivial* only so ordinary conversation
            # cannot force a closure — not so that a user-stated goal/decision
            # is unrecordable, and the Host's own typed route
            # (``human_memory_service``) applies the same operations with no
            # dirt check at all.  A ``mutate`` plan therefore carries its own
            # material change (it appends a decision + revision under CAS) and
            # is admitted on a clean scope; only ``no_mutation`` — a claim that
            # there is something to close and nothing changed — still needs
            # dirt or a pending receipt behind it.
            if require_dirty and applied_before is None and str(payload["outcome"]) != "mutate":
                dirty = await dirty_state_tx(db, task_scope_id)
                pending = await pending_receipts_tx(db, task_scope_id)
                if not dirty.is_dirty and not pending:
                    raise ClosureRejected(
                        "task_scope_update_nothing_to_close",
                        accepts=_NOTHING_TO_CLOSE_GUIDANCE,
                    )
            admissible = await _admissible_refs_tx(
                db,
                task_scope_id=task_scope_id,
                subject=subject,
                sdk_run_id=run_id,
                host_run_id=host_run_id,
            )
        content_hash_by_id = dict(admissible.ordered)
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
            # HM-TO-A6 incident U: echoing only the offending refs told the model
            # nothing about which ids *are* citable, so it guessed run ids, call
            # ids, effect ids, hashes and prefixed forms until the Run burned out.
            # The admissible ids are opaque evidence ids (never payloads) and the
            # Host already discloses exactly this list to the model in the
            # end-of-Run closure instruction (``allowed_evidence_refs``); the
            # rejection now discloses the same bounded list at the moment the
            # model needs it.  The gate itself stays fail-closed.
            raise ClosureRejected(
                "task_scope_update_refs_outside_scope",
                refs=outside[:8],
                **_refs_outside_scope_disclosure(admissible),
            )

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


#: Model-facing name of the current turn's own admitted user evidence.  The
#: ``refs_outside_scope`` rejection publishes it under this key and so does the
#: accepted ``context_route`` result (事件 AL): one name, one meaning, whichever
#: surface the model reads it from first.
CURRENT_TURN_EVIDENCE_REF_KEY = "current_turn_evidence_ref"


async def _turn_evidence_rows_tx(
    db: aiosqlite.Connection,
    *,
    task_scope_id: str,
    subject: str,
    sdk_run_id: str,
    host_run_id: str,
) -> list[Any]:
    """The Run's admitted user evidence rows, newest turn first.

    The single query behind both surfaces that name this turn's evidence: the
    ``refs_outside_scope`` rejection (:func:`_admissible_refs_tx`) and the
    accepted ``context_route`` result (:func:`read_current_turn_evidence_ref`).
    One query, so the id the model is handed *before* it composes a payload is
    byte-identical to the id the rejection would have published afterwards.
    """

    try:
        cursor = await db.execute(
            "SELECT t.evidence_id AS evidence_id, t.evidence_hash AS content_hash "
            "FROM foreground_run_sdk_bindings b "
            "JOIN foreground_runs r ON r.host_run_id=b.host_run_id "
            "JOIN foreground_turns t ON t.turn_id=r.turn_id AND t.subject=r.subject "
            "JOIN human_memory_evidence e ON e.evidence_id=t.evidence_id "
            "WHERE b.sdk_run_id=? AND r.host_run_id=? AND r.subject=? "
            "AND e.subject=? AND e.envelope_sha256=t.evidence_hash "
            # Review SHOULD-FIX 3: the caller always passes the Run's own admission
            # scope, but ``resolve_run_scope_tx`` can fall back to a model-chosen
            # route decision when ``foreground_runs.task_scope_id`` is NULL — keep
            # the cross-scope invariant local to the query that asserts it.
            "AND (t.task_scope_id IS NULL OR t.task_scope_id=?) "
            "ORDER BY t.enqueue_sequence DESC, t.evidence_id",
            (sdk_run_id, host_run_id, subject, subject, task_scope_id),
        )
        rows = await cursor.fetchall()
        await cursor.close()
    except aiosqlite.OperationalError as exc:
        # Only a DB without the foreground tables (unit fixtures) degrades to
        # "no turn evidence".  Anything else — a lock, a corrupt page — must not
        # be swallowed into a spurious ``refs_outside_scope``.
        if "no such table" not in str(exc):
            raise
        return []
    return list(rows)


async def read_current_turn_evidence_ref(
    db_path: Any, sdk_run_id: str, task_scope_id: str
) -> str:
    """This turn's own admitted user evidence id, or ``""`` when unresolvable.

    HM-TO-A6 事件 AL (2026-09-09, 第 13 次整跑 T17).  The admissible ids used to
    become visible **only** inside the ``task_scope_update_refs_outside_scope``
    rejection, so the tool description told the model to "send your best payload
    once" and read the ids off the refusal.  On a 32000-token window that
    two-step is not a protocol, it is a budget bomb: T17's first ``goal.set``
    carried a 26 KB verbatim goal (6256 output tokens), was rejected for citing
    the route receipt id, and the resend then died
    ``sdk_provider_wire_input_budget_exceeded`` (floor 27176 > effective 26752)
    because the rejected 26 KB call was still in the history.

    So the id is published on the accepted ``context_route`` result instead —
    one turn earlier, before any payload is composed.  Read-only, Host-computed,
    fail-closed: no model input reaches the query, an unresolvable Run returns
    ``""`` (the rejection lane still publishes the list), and the id is the very
    same ``turn_ref`` :func:`_admissible_refs_tx` would have disclosed.
    """

    run_id = str(sdk_run_id or "").strip()
    scope_id = str(task_scope_id or "").strip()
    if not run_id or not scope_id:
        return ""
    async with aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        db.row_factory = aiosqlite.Row
        try:
            cursor = await db.execute(
                "SELECT r.subject AS subject, r.host_run_id AS host_run_id "
                "FROM foreground_run_sdk_bindings b "
                "JOIN foreground_runs r ON r.host_run_id=b.host_run_id "
                "WHERE b.sdk_run_id=? LIMIT 2",
                (run_id,),
            )
            bindings = await cursor.fetchall()
            await cursor.close()
        except aiosqlite.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            return ""
        # Exactly one binding or nothing: an ambiguous Run must not be answered
        # with some other Run's turn (same fail-closed shape as
        # ``read_current_turn_text``).
        if len(bindings) != 1:
            return ""
        rows = await _turn_evidence_rows_tx(
            db,
            task_scope_id=scope_id,
            subject=str(bindings[0]["subject"]),
            sdk_run_id=run_id,
            host_run_id=str(bindings[0]["host_run_id"]),
        )
    return str(rows[0]["evidence_id"]) if rows else ""


async def _admissible_refs_tx(
    db: aiosqlite.Connection,
    *,
    task_scope_id: str,
    subject: str,
    sdk_run_id: str,
    host_run_id: str,
) -> _AdmissibleRefs:
    """Ordered ``(evidence_id, content_hash)`` pairs this closure may cite, newest first.

    Two Host-computed, subject-bound sources — no model input reaches this query:

    * the Run's **admitted user evidence** (``foreground_turns.evidence_id``), i.e.
      the message currently being answered.  Evidence links are only written when
      an event is appended (material effect, or the Run's own terminal), so a
      TaskScope created inside this Run has *zero* linked evidence for the whole
      Run and ``evidence_refs`` (minItems 1) would be unsatisfiable — a
      user-stated goal would be unrecordable in the turn that stated it, which
      design-freeze §2 explicitly does not intend (``host.turn`` is trivial only
      so ordinary conversation cannot *force* a closure).  This is the same
      "turn group's sanitized evidence" the Host itself uses for the turn's
      Memory batch (``foreground_queue._append_memory_ingestion_outbox_tx``).
    * every evidence id already linked to the scope, newest link first.

    Fail-closed: the row must be bound to *this* sdk Run **and** this host Run,
    carry this scope's subject, and its ``evidence_hash`` must equal the evidence
    authority row's ``envelope_sha256``.  No model input reaches the query, so the
    admissible set is a pure function of Host state.  A DB without the foreground
    tables (unit fixtures) contributes nothing rather than widening the set.

    Precondition: called outside an explicit transaction (``apply_closure`` issues
    no ``BEGIN``).  The ``no such table`` recovery below must not run inside one.
    """

    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    turn_ref: str | None = None
    turn_rows = await _turn_evidence_rows_tx(
        db,
        task_scope_id=task_scope_id,
        subject=subject,
        sdk_run_id=sdk_run_id,
        host_run_id=host_run_id,
    )
    for row in turn_rows:
        evidence_id = str(row["evidence_id"])
        if evidence_id not in seen:
            seen.add(evidence_id)
            ordered.append((evidence_id, str(row["content_hash"])))
            if turn_ref is None:
                turn_ref = evidence_id
    link_cursor = await db.execute(
        "SELECT evidence_id,content_hash,MAX(created_at) AS linked_at "
        "FROM task_scope_evidence_links WHERE task_scope_id=? "
        "GROUP BY evidence_id,content_hash ORDER BY linked_at DESC, evidence_id",
        (task_scope_id,),
    )
    link_rows = await link_cursor.fetchall()
    await link_cursor.close()
    for row in link_rows:
        evidence_id = str(row["evidence_id"])
        if evidence_id not in seen:
            seen.add(evidence_id)
            ordered.append((evidence_id, str(row["content_hash"])))
    return _AdmissibleRefs(tuple(ordered), turn_ref)


def _refs_outside_scope_disclosure(admissible: _AdmissibleRefs) -> dict[str, Any]:
    """Model-facing detail of ``task_scope_update_refs_outside_scope``: ids + next step.

    Ids and nothing else — no envelope, payload, title or goal text crosses this
    boundary, and ``content_hash`` stays Host-side (the model never sends one).
    """

    disclosed = [evidence_id for evidence_id, _ in admissible.ordered[:_MAX_DISCLOSED_REFS]]
    if not disclosed:
        # Stable detail shape across rejections of the same code.
        return {
            "allowed_evidence_refs": [],
            "allowed_evidence_refs_total": 0,
            "next_step": _REFS_OUTSIDE_SCOPE_EMPTY,
        }
    detail: dict[str, Any] = {
        "allowed_evidence_refs": disclosed,
        "allowed_evidence_refs_total": len(admissible.ordered),
        "next_step": _REFS_OUTSIDE_SCOPE_NEXT_STEP,
    }
    if admissible.turn_ref is not None:
        detail[CURRENT_TURN_EVIDENCE_REF_KEY] = admissible.turn_ref
        detail["next_step"] += _REFS_OUTSIDE_SCOPE_CURRENT_TURN
    return detail


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
    "CURRENT_TURN_EVIDENCE_REF_KEY",
    "HOST_TASK_SCOPE_UPDATE_AUTHORITY_REF",
    "MODEL_CLOSURE_REASON_CODE",
    "TASK_SCOPE_UPDATE_DESCRIPTION",
    "TASK_SCOPE_UPDATE_SCHEMA",
    "TASK_SCOPE_UPDATE_TOOL_NAME",
    "ClosureApplyResult",
    "ClosureRejected",
    "TaskScopeUpdateService",
    "derive_plan_id",
    "read_current_turn_evidence_ref",
    "write_pre_admission_audit_tx",
]
