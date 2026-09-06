# SPDX-License-Identifier: BUSL-1.1

"""TaskScope semantic closure (S5b Task 2 dirty state + Task 3 receipts / gate / fallback).

Dirty state (design-freeze §2)
    ``dirty_state(scope)`` = the *material* events of one TaskScope whose
    ``event_sequence`` is greater than the ``closure_watermark`` of the scope's
    last closure receipt with ``outcome ∈ {mutate, no_mutation}`` (no receipt →
    since 0).  A ``pending`` receipt never clears dirt.  Material / trivial is
    decided by the frozen mapping table, never by keywords:

    * ``host.file`` / ``host.test`` → material
    * ``host.turn`` → trivial
    * ``harness.tool_invocation`` whose ``public_payload.tool_name`` is a
      PROJECT_EFFECT tool (§1 list) → material, any other tool → trivial
    * ``harness.tool_invocation`` tombstone ``status=abandoned`` with
      ``effect_class=project_effect`` (outcome unknown at Run terminal) →
      material (Task 2 review F-2: the file may already be written)
    * ``harness.provider_invocation`` / ``harness.context_snapshot`` /
      ``harness.route_decision`` / ``harness.run_terminal`` → trivial
    * ``mutation.plan`` (the closure itself) → trivial

Closure receipts (v46 ``task_scope_closure_receipts``)
    One row per (Run, watermark, outcome).  ``mutate`` / ``no_mutation`` rows
    are written in the same transaction as ``apply_mutation_plan``; ``pending``
    rows carry the stable reason the closure could not be obtained and are
    settled by a later closing receipt of the same scope.

Terminal gate (third watermark, ``foreground_queue.record_sdk_terminal``)
    A Run may reach its Host terminal only when every material event of its
    admission scope is ≤ the last closing receipt's watermark, or the Run
    itself produced a ``pending`` receipt.  Applies to COMPLETED / FAILED /
    CANCELLED / STOPPED alike.

Fallback (``ClosureFallback``, between SDK terminal and Host terminal)
    Only for COMPLETED Runs with a last assistant message: one Run-bound
    provider call (same provider / model / config as the Run) that exposes
    only ``task_scope_update``; the returned plan goes through the same
    handler as the in-Run Tool.  Illegal / declined / timeout / unknown →
    ``pending`` and the Host terminal is committed as usual.  The debt belongs
    to the *admission scope*: the next Run of the same scope sees it in its
    snapshot instruction and may close it; ``force_close_pending`` settles it
    with ``no_mutation(closure_abandoned, host_forced)`` and zero provider
    calls when the scope is completed / checkpointed / resumed by the Host.
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

from deskpet.task_scope.protocol import canonical_hash, canonical_json
from deskpet.task_scope.store import CanonicalTaskScopeStore, TaskScopeConflict

MATERIAL_HOST_EVENT_KINDS: frozenset[str] = frozenset({"host.file", "host.test"})
TRIVIAL_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "host.turn",
        "harness.provider_invocation",
        "harness.context_snapshot",
        "harness.route_decision",
        "harness.run_terminal",
        "mutation.plan",
    }
)
CLOSING_RECEIPT_OUTCOMES: frozenset[str] = frozenset({"mutate", "no_mutation"})
CLOSURE_TOOL_NAME = "task_scope_update"
FALLBACK_CLOSURE_REASON_CODE = "fallback_closure"
HOST_FORCED_REASON_CODE = "host_forced"
CLOSURE_ABANDONED_REASON = "closure_abandoned"
HOST_FORCED_CLOSURE_AUTHORITY_REF = "host:forced-closure:v1"
_MAX_INSTRUCTION_EVENTS = 32
_MAX_INSTRUCTION_REFS = 64
_MAX_ANSWER_BYTES = 8192

CLOSURE_SYSTEM_INSTRUCTION = (
    "你是桌面工作台的主模型。这一轮你已经替用户完成了工作并写好了最终回答，但任务档案还没有收口。"
    "现在只允许调用 task_scope_update 一次：如果客观事件表明任务状态/进度/下一步发生了实质变化，"
    "提交 outcome=mutate 并逐项引用 allowed_evidence_refs 里的 evidence_refs；"
    "如果没有实质变化，提交 outcome=no_mutation 并给 closure_reason。"
    "base_revision 必须等于 task_scope.current_revision。不要输出其他内容，不要重复最终回答。"
)
CLOSURE_SNAPSHOT_INSTRUCTION = (
    "本任务档案有尚未收口的客观事件（见 material_events / pending_receipts）。"
    "在给出最终回答之前，调用一次 task_scope_update：实质变化 → outcome=mutate 并引用 "
    "allowed_evidence_refs 中的 evidence_refs；无实质变化 → outcome=no_mutation 并给 closure_reason。"
    "base_revision 必须等于 current_revision。"
)


def _uuid(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"simple-harness:{label}"))


# --------------------------------------------------------------------- dirty state


@dataclass(frozen=True, slots=True)
class MaterialEvent:
    event_id: str
    event_sequence: int
    event_kind: str
    source_event_id: str


@dataclass(frozen=True, slots=True)
class DirtyState:
    task_scope_id: str
    closure_watermark: int
    material_events: tuple[MaterialEvent, ...]

    @property
    def is_dirty(self) -> bool:
        return bool(self.material_events)

    @property
    def event_watermark(self) -> int:
        """Highest material sequence (== closure_watermark when clean)."""

        if not self.material_events:
            return self.closure_watermark
        return max(item.event_sequence for item in self.material_events)


def is_material_event(event_kind: str, payload: Mapping[str, object] | None) -> bool:
    """Deterministic §2 mapping over one archived event."""

    if event_kind in MATERIAL_HOST_EVENT_KINDS:
        return True
    if event_kind == "harness.tool_invocation":
        from deskpet.sdk_adapters.tool_authority import PROJECT_EFFECT_TOOL_NAMES

        public = payload.get("public_payload") if isinstance(payload, Mapping) else None
        if not isinstance(public, Mapping):
            return False
        tool_name = public.get("tool_name")
        status = public.get("status")
        if status is not None:
            from deskpet.execution.evidence_ingress import MATERIAL_TOMBSTONE_STATUSES

            # Task 2 review F-2 / Task 6: an unknown-outcome or Host-rejected
            # PROJECT_EFFECT tombstone is dirt (the file may be written); an
            # SDK-denied (``rejected``) effect never dispatched → trivial.
            return (
                status in MATERIAL_TOMBSTONE_STATUSES
                and public.get("effect_class") == "project_effect"
            )
        return isinstance(tool_name, str) and tool_name in PROJECT_EFFECT_TOOL_NAMES
    return False


async def last_closing_watermark_tx(db: aiosqlite.Connection, task_scope_id: str) -> int:
    receipt = await CanonicalTaskScopeStore._fetchone(
        db,
        "SELECT closure_watermark FROM task_scope_closure_receipts "
        "WHERE task_scope_id=? AND outcome IN ('mutate','no_mutation') "
        "ORDER BY closure_watermark DESC, created_at DESC LIMIT 1",
        (task_scope_id,),
    )
    return 0 if receipt is None else int(receipt["closure_watermark"])


async def dirty_state_tx(db: aiosqlite.Connection, task_scope_id: str) -> DirtyState:
    watermark = await last_closing_watermark_tx(db, task_scope_id)
    cursor = await db.execute(
        "SELECT event_id,event_sequence,event_kind,source_event_id,payload_json "
        "FROM task_scope_events WHERE task_scope_id=? AND event_sequence>? "
        "ORDER BY event_sequence",
        (task_scope_id, watermark),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    material: list[MaterialEvent] = []
    for row in rows:
        kind = str(row["event_kind"])
        payload: Mapping[str, object] | None = None
        if kind == "harness.tool_invocation":
            try:
                loaded = json.loads(str(row["payload_json"]))
            except (TypeError, ValueError):
                loaded = None
            payload = loaded if isinstance(loaded, Mapping) else None
        if is_material_event(kind, payload):
            material.append(
                MaterialEvent(
                    str(row["event_id"]),
                    int(row["event_sequence"]),
                    kind,
                    str(row["source_event_id"]),
                )
            )
    return DirtyState(task_scope_id, watermark, tuple(material))


async def dirty_state(store: CanonicalTaskScopeStore, task_scope_id: str) -> DirtyState:
    async with store._connection() as db:
        return await dirty_state_tx(db, task_scope_id)


# ------------------------------------------------------------------------ receipts


@dataclass(frozen=True, slots=True)
class ClosureReceipt:
    receipt_id: str
    task_scope_id: str
    sdk_run_id: str
    host_run_id: str
    closure_watermark: int
    outcome: str
    plan_id: str | None
    reason_code: str
    attempt_id: str | None
    created_at: float

    def to_json(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "task_scope_id": self.task_scope_id,
            "sdk_run_id": self.sdk_run_id,
            "host_run_id": self.host_run_id,
            "closure_watermark": self.closure_watermark,
            "outcome": self.outcome,
            "plan_id": self.plan_id,
            "reason_code": self.reason_code,
            "attempt_id": self.attempt_id,
        }


def _receipt_from_row(row: Any) -> ClosureReceipt:
    return ClosureReceipt(
        str(row["receipt_id"]),
        str(row["task_scope_id"]),
        str(row["sdk_run_id"]),
        str(row["host_run_id"]),
        int(row["closure_watermark"]),
        str(row["outcome"]),
        None if row["plan_id"] is None else str(row["plan_id"]),
        str(row["reason_code"]),
        None if row["attempt_id"] is None else str(row["attempt_id"]),
        float(row["created_at"]),
    )


async def write_closure_receipt_tx(
    db: aiosqlite.Connection,
    *,
    task_scope_id: str,
    sdk_run_id: str,
    host_run_id: str,
    closure_watermark: int,
    outcome: str,
    plan_id: str | None,
    reason_code: str,
    attempt_id: str | None,
    now: float,
) -> ClosureReceipt:
    """Append one closure receipt (idempotent on (sdk_run_id, watermark, outcome))."""

    if outcome not in {"mutate", "no_mutation", "pending"}:
        raise ValueError("closure_outcome_invalid")
    existing = await CanonicalTaskScopeStore._fetchone(
        db,
        "SELECT * FROM task_scope_closure_receipts WHERE sdk_run_id=? AND closure_watermark=? AND outcome=?",
        (sdk_run_id, int(closure_watermark), outcome),
    )
    if existing is not None:
        current = _receipt_from_row(existing)
        if current.task_scope_id != task_scope_id or (
            plan_id is not None and current.plan_id not in (None, plan_id)
        ):
            raise TaskScopeConflict("closure_receipt_conflict")
        return current
    receipt_id = _uuid(f"task-scope-closure-receipt:{sdk_run_id}:{closure_watermark}:{outcome}")
    await db.execute(
        "INSERT INTO task_scope_closure_receipts(receipt_id,task_scope_id,sdk_run_id,host_run_id,"
        "closure_watermark,outcome,plan_id,reason_code,attempt_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            receipt_id, task_scope_id, sdk_run_id, host_run_id, int(closure_watermark), outcome,
            plan_id, reason_code, attempt_id, float(now),
        ),
    )
    from deskpet.task_scope.projection_sources import append_projection_source_tx

    # STATUS projection must show `semantic_closure_pending`; bump the source.
    await append_projection_source_tx(db, task_scope_id, now=float(now))
    return ClosureReceipt(
        receipt_id, task_scope_id, sdk_run_id, host_run_id, int(closure_watermark), outcome,
        plan_id, reason_code, attempt_id, float(now),
    )


async def write_closure_receipt(
    db_path: str | Path,
    *,
    task_scope_id: str,
    sdk_run_id: str,
    host_run_id: str,
    closure_watermark: int,
    outcome: str,
    plan_id: str | None,
    reason_code: str,
    attempt_id: str | None,
    clock: Callable[[], float] = time.time,
    extension: Callable[[aiosqlite.Connection], Any] | None = None,
) -> ClosureReceipt:
    from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx

    store = CanonicalTaskScopeStore(db_path)
    async with store._connection() as db:
        await db.execute("BEGIN IMMEDIATE")
        await assert_human_memory_ingress_open_tx(db)
        try:
            receipt = await write_closure_receipt_tx(
                db,
                task_scope_id=task_scope_id,
                sdk_run_id=sdk_run_id,
                host_run_id=host_run_id,
                closure_watermark=closure_watermark,
                outcome=outcome,
                plan_id=plan_id,
                reason_code=reason_code,
                attempt_id=attempt_id,
                now=clock(),
            )
            if extension is not None:
                await extension(db)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return receipt


async def closure_receipt_for_plan_tx(
    db: aiosqlite.Connection, *, task_scope_id: str, plan_id: str
) -> ClosureReceipt | None:
    """The receipt written together with ``plan_id``'s decision (first write wins; F-1)."""

    row = await CanonicalTaskScopeStore._fetchone(
        db,
        "SELECT * FROM task_scope_closure_receipts WHERE task_scope_id=? AND plan_id=? ORDER BY rowid LIMIT 1",
        (task_scope_id, plan_id),
    )
    return None if row is None else _receipt_from_row(row)


async def pending_receipts_tx(db: aiosqlite.Connection, task_scope_id: str) -> tuple[ClosureReceipt, ...]:
    """Open ``pending`` receipts: watermark above the last closing receipt."""

    watermark = await last_closing_watermark_tx(db, task_scope_id)
    cursor = await db.execute(
        "SELECT * FROM task_scope_closure_receipts WHERE task_scope_id=? AND outcome='pending' "
        "AND closure_watermark>? ORDER BY created_at, receipt_id",
        (task_scope_id, watermark),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return tuple(_receipt_from_row(row) for row in rows)


async def pending_receipts(store: CanonicalTaskScopeStore, task_scope_id: str) -> tuple[ClosureReceipt, ...]:
    async with store._connection() as db:
        return await pending_receipts_tx(db, task_scope_id)


async def receipts_for_run_tx(db: aiosqlite.Connection, sdk_run_id: str) -> tuple[ClosureReceipt, ...]:
    cursor = await db.execute(
        "SELECT * FROM task_scope_closure_receipts WHERE sdk_run_id=? ORDER BY created_at, receipt_id",
        (sdk_run_id,),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return tuple(_receipt_from_row(row) for row in rows)


@dataclass(frozen=True, slots=True)
class ClosureCoverage:
    dirty: DirtyState
    pending_by_run: bool

    @property
    def satisfied(self) -> bool:
        return not self.dirty.is_dirty or self.pending_by_run


async def closure_coverage_tx(
    db: aiosqlite.Connection, *, task_scope_id: str, sdk_run_id: str
) -> ClosureCoverage:
    """Third terminal watermark: material events covered, or this Run wrote ``pending``."""

    dirty = await dirty_state_tx(db, task_scope_id)
    pending_by_run = False
    if dirty.is_dirty:
        row = await CanonicalTaskScopeStore._fetchone(
            db,
            "SELECT 1 FROM task_scope_closure_receipts WHERE sdk_run_id=? AND task_scope_id=? "
            "AND outcome='pending' AND closure_watermark>=? LIMIT 1",
            (sdk_run_id, task_scope_id, dirty.closure_watermark),
        )
        pending_by_run = row is not None
    return ClosureCoverage(dirty, pending_by_run)


# ---------------------------------------------------------------- hash derivation


def closure_request_hash(
    sdk_run_id: str, task_scope_id: str, closure_watermark: int, last_assistant_message_hash: str
) -> str:
    return canonical_hash(
        {
            "sdk_run_id": sdk_run_id,
            "task_scope_id": task_scope_id,
            "closure_watermark": int(closure_watermark),
            "last_assistant_message_hash": last_assistant_message_hash,
        }
    )


def closure_plan_id(request_hash: str) -> str:
    return canonical_hash({"closure-plan": request_hash})


def evidence_set_key(event_ids: Sequence[str]) -> str:
    return canonical_hash(sorted(set(event_ids)))


def message_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------ observation model


async def _scope_observation_tx(
    db: aiosqlite.Connection, task_scope_id: str, dirty: DirtyState
) -> dict[str, Any]:
    """Bounded, deterministic Host-authored view of the scope for model consumption."""

    head = await CanonicalTaskScopeStore._fetchone(
        db,
        "SELECT h.current_revision,h.event_watermark,r.state_json,s.title FROM task_scope_heads h "
        "JOIN task_scopes s ON s.task_scope_id=h.task_scope_id "
        "JOIN task_scope_canonical_revisions r ON r.task_scope_id=h.task_scope_id AND r.revision=h.current_revision "
        "WHERE h.task_scope_id=?",
        (task_scope_id,),
    )
    if head is None:
        raise TaskScopeConflict("task_scope_not_found")
    state = json.loads(str(head["state_json"]))
    ref_cursor = await db.execute(
        "SELECT DISTINCT evidence_id, MIN(created_at) AS first_seen FROM task_scope_evidence_links "
        "WHERE task_scope_id=? GROUP BY evidence_id ORDER BY first_seen, evidence_id",
        (task_scope_id,),
    )
    ref_rows = await ref_cursor.fetchall()
    await ref_cursor.close()
    allowed_refs = [str(row["evidence_id"]) for row in ref_rows][:_MAX_INSTRUCTION_REFS]
    events: list[dict[str, Any]] = []
    for item in dirty.material_events[-_MAX_INSTRUCTION_EVENTS:]:
        row = await CanonicalTaskScopeStore._fetchone(
            db, "SELECT payload_json FROM task_scope_events WHERE event_id=?", (item.event_id,)
        )
        payload = json.loads(str(row["payload_json"])) if row is not None else {}
        link_cursor = await db.execute(
            "SELECT evidence_id FROM task_scope_evidence_links WHERE event_id=? ORDER BY ordinal",
            (item.event_id,),
        )
        links = [str(r["evidence_id"]) for r in await link_cursor.fetchall()]
        await link_cursor.close()
        events.append(_event_summary(item, payload, links))
    pending = await pending_receipts_tx(db, task_scope_id)
    return {
        "task_scope": {
            "task_scope_id": task_scope_id,
            "title": str(head["title"]),
            "current_revision": int(head["current_revision"]),
            "status": state.get("status"),
            "goal": state.get("goal"),
            "resume": state.get("resume"),
        },
        "closure_watermark": dirty.closure_watermark,
        "event_watermark": dirty.event_watermark,
        "material_events": events,
        "pending_receipts": [
            {
                "sdk_run_id": receipt.sdk_run_id,
                "closure_watermark": receipt.closure_watermark,
                "reason_code": receipt.reason_code,
            }
            for receipt in pending[-8:]
        ],
        "allowed_evidence_refs": allowed_refs,
    }


def _event_summary(item: MaterialEvent, payload: Mapping[str, Any], links: list[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "event_kind": item.event_kind,
        "event_sequence": item.event_sequence,
        "source_event_id": item.source_event_id,
        "evidence_refs": links,
    }
    public = payload.get("public_payload") if item.event_kind.startswith("harness.") else payload
    if isinstance(public, Mapping):
        for key in ("tool_name", "outcome", "effect_state", "error_code", "targets", "command_head", "exit_code"):
            if key in public:
                summary[key] = public[key]
        if public.get("status") == "abandoned":
            summary["outcome"] = "unknown"
            summary["unknown_outcome"] = True
            summary["note"] = "write operation with unknown result (effect not settled at Run terminal)"
    return summary


async def closure_instruction_for_run(db_path: str | Path, sdk_run_id: str) -> Any | None:
    """Protected snapshot message when the Run's admission scope is dirty or has pending receipts.

    Controls only the instruction text — never Tool visibility (``task_scope_update``
    is always exposed).  ``None`` for unbound Runs and clean scopes.
    """

    from simple_harness.contracts.messages import Message, MessageRole

    from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress

    store = CanonicalTaskScopeStore(db_path)
    ingress = ExecutionEvidenceIngress(db_path)
    async with store._connection() as db:
        binding = await ingress.resolve_run_scope_tx(db, sdk_run_id)
        if binding is None:
            return None
        dirty = await dirty_state_tx(db, binding.task_scope_id)
        pending = await pending_receipts_tx(db, binding.task_scope_id)
        if not dirty.is_dirty and not pending:
            return None
        observation = await _scope_observation_tx(db, binding.task_scope_id, dirty)
    body = {
        "kind": "task_scope_closure_required",
        "task_scope_id": binding.task_scope_id,
        "current_revision": observation["task_scope"]["current_revision"],
        "closure_watermark": observation["closure_watermark"],
        "material_events": observation["material_events"],
        "pending_receipts": observation["pending_receipts"],
        "allowed_evidence_refs": observation["allowed_evidence_refs"],
        "instruction": CLOSURE_SNAPSHOT_INSTRUCTION,
    }
    return Message(
        role=MessageRole.SYSTEM,
        content=canonical_json(body),
        metadata={"source": "semantic_closure", "trust": "host_authority"},
    )


# --------------------------------------------------------------------- fallback


@dataclass(frozen=True, slots=True)
class ClosureRunFacts:
    """Durable SDK-side inputs of the fallback: the Run binding + last assistant message."""

    binding_record: Mapping[str, Any] | None
    last_assistant_message: str | None


@dataclass(frozen=True, slots=True)
class ClosureSettlement:
    status: str  # clean | already_closed | mutate | no_mutation | pending | lease_lost
    reason_code: str | None = None
    receipt: ClosureReceipt | None = None
    provider_calls: int = 0
    attempt_id: str | None = None


class ClosureFallback:
    """Host-side closure after the SDK terminal and before the Host terminal."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        invoker: Any,
        service: Any,
        run_facts_reader: Any,
        request_authority: Any = None,
        clock: Callable[[], float] = time.time,
        fault_inject: Callable[[str], None] | None = None,
        deadline_seconds: float = 60.0,
    ) -> None:
        self._db_path = Path(db_path)
        self._store = CanonicalTaskScopeStore(db_path)
        self._invoker = invoker
        self._service = service
        self._facts = run_facts_reader
        self._request_authority = request_authority
        self._clock = clock
        self._fault_inject = fault_inject
        self._deadline_seconds = float(deadline_seconds)

    async def settle(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        terminal_state: Any,
    ) -> ClosureSettlement:
        from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress

        ingress = ExecutionEvidenceIngress(self._db_path)
        state_value = str(getattr(terminal_state, "value", terminal_state)).upper()
        async with self._store._connection() as db:
            binding = await ingress.resolve_run_scope_tx(db, sdk_run_id)
            if binding is None:
                return ClosureSettlement("clean")
            scope = binding.task_scope_id
            subject = binding.subject
            coverage = await closure_coverage_tx(db, task_scope_id=scope, sdk_run_id=sdk_run_id)
            if not coverage.dirty.is_dirty:
                own = await receipts_for_run_tx(db, sdk_run_id)
                if own:
                    return ClosureSettlement("already_closed", own[-1].reason_code, own[-1])
                return ClosureSettlement("clean")
            if coverage.pending_by_run:
                own = await receipts_for_run_tx(db, sdk_run_id)
                return ClosureSettlement("pending", own[-1].reason_code if own else None, own[-1] if own else None)
            dirty = coverage.dirty
            # Non-success settlement needs only an honest pending debt, never
            # a source-bearing model observation (sources may be withdrawn).
            observation = (await _scope_observation_tx(db, scope, dirty)
                           if state_value == "COMPLETED" else None)
        watermark = dirty.event_watermark

        async def pending(reason: str, *, attempt_id: str | None = None, extension: Any = None) -> ClosureSettlement:
            receipt = await write_closure_receipt(
                self._db_path,
                task_scope_id=scope,
                sdk_run_id=sdk_run_id,
                host_run_id=host_run_id,
                closure_watermark=watermark,
                outcome="pending",
                plan_id=None,
                reason_code=reason,
                attempt_id=attempt_id,
                clock=self._clock,
                extension=extension,
            )
            return ClosureSettlement("pending", reason, receipt, provider_calls, attempt_id)

        provider_calls = 0
        if state_value != "COMPLETED":
            return await pending("closure_run_not_completed")
        facts: ClosureRunFacts = self._facts.read_closure_run_facts(sdk_run_id)
        answer = (facts.last_assistant_message or "").strip()
        if not answer:
            return await pending("closure_no_final_answer")
        if not facts.binding_record:
            return await pending("closure_binding_unavailable")
        answer = answer.encode("utf-8")[:_MAX_ANSWER_BYTES].decode("utf-8", "ignore")
        request_hash = closure_request_hash(sdk_run_id, scope, watermark, message_hash(answer))
        plan_id = closure_plan_id(request_hash)
        members = tuple(
            (subject, sdk_run_id, ref)
            for ref in dict.fromkeys(ref for event in observation["material_events"] for ref in event["evidence_refs"])
        )
        observation_body = {**observation, "staged_final_answer": answer}

        def build_request(row: Any) -> Any:
            from simple_harness import RequestId
            from simple_harness.contracts.messages import Message, MessageRole
            from simple_harness.providers import ProviderRequest, ProviderToolSpec

            from deskpet.sdk_adapters.task_scope_mutation import (
                TASK_SCOPE_UPDATE_DESCRIPTION,
                TASK_SCOPE_UPDATE_SCHEMA,
            )

            return ProviderRequest(
                RequestId(f"post-turn-closure-{request_hash[:24]}-{row.attempt_ordinal}"),
                (
                    Message(role=MessageRole.SYSTEM, content=CLOSURE_SYSTEM_INSTRUCTION),
                    Message(
                        role=MessageRole.USER,
                        content="[closure observation]\n" + json.dumps(observation_body, ensure_ascii=False, indent=1),
                    ),
                ),
                tools=(ProviderToolSpec(CLOSURE_TOOL_NAME, TASK_SCOPE_UPDATE_DESCRIPTION, TASK_SCOPE_UPDATE_SCHEMA),),
                max_output_tokens=1200,
            )

        async def prepare_attempt(row):
            from types import SimpleNamespace
            prepared = await self._request_authority.prepare(
                host_run_id=host_run_id, sdk_run_id=sdk_run_id, subject=subject,
                owner_id=owner_id, generation=generation, scope=scope,
                observation=observation, answer=answer, binding_record=facts.binding_record)
            actual_members = tuple((subject, s["run_id"], s["evidence_id"])
                                   for s in prepared["host"]["sources"])
            request = build_request(SimpleNamespace(**row))
            async def observer(attempt, db):
                await self._request_authority.bind_attempt(prepared, attempt, request, db=db)
            return actual_members, observer

        from deskpet.execution.closure_request_guard import ClosureSourceIncomplete
        try:
            outcome = await self._invoker.invoke(
                purpose="closure",
                host_run_id=host_run_id,
                sdk_run_id=sdk_run_id,
                generation=generation,
                task_scope_id=scope,
                closure_watermark=watermark,
                request_hash=request_hash,
                evidence_set_key=evidence_set_key([event.event_id for event in dirty.material_events]),
                members=members,
                binding_record=facts.binding_record,
                build_request=build_request,
                plan_id=plan_id,
                deadline_seconds=self._deadline_seconds,
                **({"prepare_attempt": prepare_attempt} if self._request_authority is not None else {}),
            )
        except ClosureSourceIncomplete:
            return await pending("closure_source_incomplete")
        provider_calls = int(outcome.provider_calls)
        attempt_id = outcome.attempt_id
        if outcome.status == "lease_lost":
            return ClosureSettlement("lease_lost", outcome.reason_code, None, provider_calls, attempt_id)
        if outcome.status == "reused":
            return await self._derive_from_decision(
                scope=scope, sdk_run_id=sdk_run_id, host_run_id=host_run_id, plan_id=plan_id,
                attempt_id=attempt_id, watermark=watermark, provider_calls=provider_calls,
            )
        if outcome.status != "succeeded":
            return await pending(outcome.reason_code or f"closure_attempt_{outcome.status}", attempt_id=attempt_id)

        response = outcome.response
        assert response is not None and attempt_id is not None

        async def settle_success(db: aiosqlite.Connection) -> None:
            await self._invoker.settle_succeeded_tx(db, attempt_id, response=response, plan_id=plan_id)
            if self._request_authority is not None:
                from deskpet.task_scope.mutation_disclosure import record_closure_result_tx
                await record_closure_result_tx(db, db_path=self._db_path,
                    attempt_id=attempt_id, plan_id=plan_id, response=response)

        arguments = _closure_arguments(response)
        if arguments is None:
            return await pending("closure_model_declined", attempt_id=attempt_id, extension=settle_success)
        from deskpet.sdk_adapters.task_scope_mutation import ClosureRejected

        try:
            applied = await self._service.apply_closure(
                arguments,
                run_id=sdk_run_id,
                host_run_id=host_run_id,
                task_scope_id=scope,
                subject=subject,
                source_turn_id=f"closure:{sdk_run_id}",
                reason_code=FALLBACK_CLOSURE_REASON_CODE,
                plan_id=plan_id,
                attempt_id=attempt_id,
                require_dirty=False,
                commit_extension=settle_success,
            )
        except ClosureRejected as rejected:
            return await pending(rejected.code, attempt_id=attempt_id, extension=settle_success)
        receipt = applied.receipt
        return ClosureSettlement(receipt.outcome, FALLBACK_CLOSURE_REASON_CODE, receipt, provider_calls, attempt_id)

    async def _derive_from_decision(
        self,
        *,
        scope: str,
        sdk_run_id: str,
        host_run_id: str,
        plan_id: str,
        attempt_id: str | None,
        watermark: int,
        provider_calls: int,
    ) -> ClosureSettlement:
        """``succeeded`` attempt without a receipt: derive from the durable decision, else pending."""

        from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx

        async with self._store._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                decision = await self._store._fetchone(
                    db,
                    "SELECT d.outcome, r.event_watermark FROM task_scope_mutation_decisions d "
                    "JOIN task_scope_canonical_revisions r ON r.decision_id=d.decision_id "
                    "WHERE d.plan_id=? AND d.task_scope_id=?",
                    (plan_id, scope),
                )
                existing = await closure_receipt_for_plan_tx(db, task_scope_id=scope, plan_id=plan_id)
                if existing is not None:
                    await db.commit()
                    return ClosureSettlement(existing.outcome, existing.reason_code, existing, provider_calls, attempt_id)
                if decision is None:
                    receipt = await write_closure_receipt_tx(
                        db, task_scope_id=scope, sdk_run_id=sdk_run_id, host_run_id=host_run_id,
                        closure_watermark=watermark, outcome="pending", plan_id=None,
                        reason_code="closure_attempt_unapplied", attempt_id=attempt_id, now=self._clock(),
                    )
                    status = "pending"
                    reason = "closure_attempt_unapplied"
                else:
                    status = str(decision["outcome"])
                    reason = FALLBACK_CLOSURE_REASON_CODE
                    # F-1: the decision's own revision watermark, never the live head.
                    receipt = await write_closure_receipt_tx(
                        db, task_scope_id=scope, sdk_run_id=sdk_run_id, host_run_id=host_run_id,
                        closure_watermark=int(decision["event_watermark"]), outcome=status, plan_id=plan_id,
                        reason_code=reason, attempt_id=attempt_id, now=self._clock(),
                    )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return ClosureSettlement(status, reason, receipt, provider_calls, attempt_id)


def _closure_arguments(response: Any) -> dict[str, Any] | None:
    """Exactly one ``task_scope_update`` tool call → its arguments, else ``None``."""

    from simple_harness import thaw_json

    calls = [call for call in tuple(getattr(response, "tool_calls", ()) or ()) if call.name == CLOSURE_TOOL_NAME]
    if len(calls) != 1 or len(tuple(response.tool_calls)) != 1:
        return None
    arguments = thaw_json(calls[0].arguments)
    return dict(arguments) if isinstance(arguments, Mapping) else None


# ------------------------------------------------------------------ force close


async def force_close_pending(
    db_path: str | Path,
    *,
    task_scope_id: str,
    subject: str,
    reason: str = CLOSURE_ABANDONED_REASON,
    clock: Callable[[], float] = time.time,
) -> ClosureReceipt | None:
    """Settle an open pending closure with Host ``no_mutation`` (zero provider calls).

    Called when the scope is completed / checkpointed / resumed by the Host
    while a closure is still pending.  Returns ``None`` when nothing is pending.
    """

    from simple_harness import DisclosureContext, EvidenceRef
    from simple_harness.runtime.task_scope_protocol import (
        TaskScopeMutationOutcome,
        TaskScopeMutationPlan,
    )

    from deskpet.memory.human_memory_program import HumanMemoryProgramStore
    from deskpet.memory.human_memory_service import build_host_typed_evidence
    from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx
    from deskpet.sdk_adapters.task_scope_mutation import _disclosure

    store = CanonicalTaskScopeStore(db_path)
    pending = await pending_receipts(store, task_scope_id)
    if not pending:
        return None
    latest = pending[-1]
    program = HumanMemoryProgramStore(db_path)
    primary = await program.initialize_subject(subject)
    envelope, receipt = build_host_typed_evidence(
        subject=subject,
        authority_ref=HOST_FORCED_CLOSURE_AUTHORITY_REF,
        payload={
            "schema_version": 1,
            "task_scope_id": task_scope_id,
            "closure_reason": reason,
            "pending_receipt_id": latest.receipt_id,
        },
        idempotency_key=f"forced-closure:{latest.receipt_id}",
        source_ref=f"host-forced-closure:{latest.receipt_id}",
        run_id=latest.sdk_run_id,
    )
    async with store._connection() as db:
        await db.execute("BEGIN IMMEDIATE")
        await assert_human_memory_ingress_open_tx(db)
        try:
            committed = await program.append_evidence_tx(
                db, envelope, receipt, primary_conversation_id=primary.primary_conversation_id, committed_at=clock()
            )
            head = await store._fetchone(
                db, "SELECT current_revision FROM task_scope_heads WHERE task_scope_id=?", (task_scope_id,)
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    assert head is not None
    evidence_ref = EvidenceRef(committed.evidence_id, committed.envelope_sha256, 1)
    plan = TaskScopeMutationPlan(
        plan_id=canonical_hash({"forced-closure-plan": latest.receipt_id}),
        run_id=latest.sdk_run_id,
        subject=subject,
        task_scope_id=task_scope_id,
        base_revision=int(head["current_revision"]),
        outcome=TaskScopeMutationOutcome.NO_MUTATION,
        operations=(),
        closure_reason=reason,
        source_turn_id=f"host-forced-closure:{latest.receipt_id}",
        disclosure_context=_disclosure(DisclosureContext, latest.sdk_run_id, subject),
        evidence_refs=(evidence_ref,),
        idempotency_key=f"forced-closure:{latest.receipt_id}",
    )
    captured: dict[str, ClosureReceipt] = {}

    async def commit_hook(hook_db: aiosqlite.Connection, _receipt: Any, _replayed: bool) -> None:
        existing = await closure_receipt_for_plan_tx(hook_db, task_scope_id=task_scope_id, plan_id=plan.plan_id)
        if existing is not None:
            captured["receipt"] = existing
            return
        # Task 3 review F-7: the forced closure only settles the debt the pending
        # receipt carried — its own ``closure_watermark`` — never the live head
        # (which may already contain material events of a Run still executing;
        # those keep the scope dirty for that Run's own closure).
        captured["receipt"] = await write_closure_receipt_tx(
            hook_db,
            task_scope_id=task_scope_id,
            sdk_run_id=latest.sdk_run_id,
            host_run_id=latest.host_run_id,
            closure_watermark=int(latest.closure_watermark),
            outcome="no_mutation",
            plan_id=plan.plan_id,
            reason_code=HOST_FORCED_REASON_CODE,
            attempt_id=None,
            now=clock(),
        )

    await store.apply_mutation_plan(plan, commit_hook=commit_hook)
    return captured["receipt"]


__all__ = [
    "CLOSING_RECEIPT_OUTCOMES",
    "CLOSURE_ABANDONED_REASON",
    "CLOSURE_SNAPSHOT_INSTRUCTION",
    "CLOSURE_SYSTEM_INSTRUCTION",
    "CLOSURE_TOOL_NAME",
    "FALLBACK_CLOSURE_REASON_CODE",
    "HOST_FORCED_REASON_CODE",
    "MATERIAL_HOST_EVENT_KINDS",
    "TRIVIAL_EVENT_KINDS",
    "ClosureCoverage",
    "ClosureFallback",
    "ClosureReceipt",
    "ClosureRunFacts",
    "ClosureSettlement",
    "DirtyState",
    "MaterialEvent",
    "closure_coverage_tx",
    "closure_instruction_for_run",
    "closure_plan_id",
    "closure_request_hash",
    "dirty_state",
    "dirty_state_tx",
    "evidence_set_key",
    "force_close_pending",
    "is_material_event",
    "last_closing_watermark_tx",
    "message_hash",
    "pending_receipts",
    "pending_receipts_tx",
    "receipts_for_run_tx",
    "write_closure_receipt",
    "write_closure_receipt_tx",
]
