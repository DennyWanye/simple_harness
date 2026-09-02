# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Per-effect workspace EffectGate (S5b Task 1 + Task 6, design-freeze §4).

``ProductEffectExecutor.execute`` consults :class:`EffectGate` before every
physical Tool dispatch.  Only PROJECT_EFFECT Tools (policy read from the frozen
run catalog record) are gated; every other Tool passes through untouched.

The gate is a *binary* verdict: ``None`` (admit) or ``ToolResult.rejected``
with one stable reason code — never an exception, never a partial effect.  A
rejection leaves no ``execution_effects`` row and no Host event; the model sees
the reason code as an ordinary TOOL message and may re-route.

Check order (frozen; the first failing step names the reason):

0. exact replay is never re-verified (Task 1 review F-1 / Task 2 review F-3):
   if the SDK effect ledger already holds a record for this ``effect_id`` in
   any state *other than* PREPARED (settled, HANDED_OFF, UNKNOWN),
   ``ProductEffectExecutor.execute`` skips the gate and hands the call to the
   SDK replay / reconcile path.  A PREPARED row means no physical action has
   happened yet, so the replay is re-verified like a first occurrence.
1. envelope present and echoing this exact effect
   (``effect_gate_envelope_missing`` / ``effect_gate_envelope_identity_mismatch``;
   Task 6 review F-8: a missing ``context.effect_id`` is an identity mismatch,
   never a silent pass)
2. sticky memo ``effect_gate_route_receipt_rejected`` (Task 6, review F-3): a
   durable ``effect_gate_rejections(sdk_run_id, route_receipt_id)`` row keeps
   rejecting every later project effect of the same Run under the same route
   receipt — until a new ``context_route`` receipt is issued.
3. Run-admission frozen authority consistency —
   ``effect_gate_frozen_scope_mismatch`` (envelope scope ≠ frozen scope),
   ``effect_gate_projectless_project_effect`` (Run frozen without an exact
   single root: zero/multi-root freeze), ``effect_gate_frozen_root_mismatch``
   (verified root canonical path ≠ frozen write root — evaluated right after
   step 4 because the canonical path comes from the S4 re-verification)
4. ``WorkspaceBindingAuthorityStore.verify_task_execution_envelope`` against the
   durable v45 route receipt: the S4 code set passes through unchanged
   (``workspace_binding_route_authority_missing|stale``,
   ``workspace_binding_envelope_lineage_mismatch``,
   ``workspace_binding_effect_authority_missing|stale``,
   ``workspace_binding_envelope_root_mismatch``, ``workspace_root_unavailable``,
   ``workspace_root_identity_drift``, ``workspace_root_not_canonical``,
   ``workspace_root_too_broad``)
5. ``workspace_binding_receipt_superseded`` — strict: binding head revision must
   equal the receipt revision (Manual/Auto alike, 裁决 A6).  A missing binding
   head row is ``workspace_binding_effect_authority_missing`` (Task 6 review
   F-7), never "superseded".
6. ``effect_gate_task_scope_not_active`` — TaskScope status ∉ {active, open}
7. confirm-only / grant — ``product_policy_user_confirmation`` is produced by
   the SDK authorization path (``SdkPreparedAuthorizationPolicy.decide`` with
   ``explicit_only`` from the frozen EffectClass), not by this gate.

Snapshot discipline (Task 6, review F-2): steps 2, 4, 5 and 6 read the route
receipt, the binding revision/root rows, the binding head and the TaskScope head
inside **one** SQLite read transaction, so the verdict is computed over a single
consistent state.  The remaining window between that snapshot and the physical
dispatch is closed by :meth:`EffectGate.reservation_check`: the Harness
evidence reservation transaction (``BEGIN IMMEDIATE``, immediately before the
physical action) re-reads the binding head under the write lock; a head that
moved since the snapshot is rejected (``workspace_binding_receipt_superseded``)
before any reservation or dispatch.  Only the reservation-commit → physical
write window remains, and an append landing there cannot replace the old root
(append-only binding sets).

Frozen-authority note: ``ProductForegroundToolPort.freeze`` registers an exact
single root as ``workspace_root`` = canonical path with resolution kind
``legacy`` (the Session-only ``project_bound`` validator is bypassed on
purpose), and zero/multi root as ``projectless`` with no root.  "Project-bound"
for this gate therefore means *an exact frozen root exists*; ``projectless`` /
``missing`` / no root is rejected.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, Protocol

import aiosqlite
from simple_harness import CallId, RunId
from simple_harness.tools import ToolContext, ToolResult
from simple_harness.tools.runtime_catalog import ToolEffectClass

from deskpet.execution.evidence_ingress import ObjectiveEventSpec
from deskpet.task_scope.store import CanonicalTaskScopeStore, TaskScopeNotFound, _uuid
from deskpet.task_scope.workspace_bindings import WorkspaceBindingError

EFFECT_GATE_PUBLIC_MESSAGE = (
    "Project effect was rejected by the workspace effect gate; "
    "observe a fresh Context route before retrying."
)
PROJECT_EFFECT_ACTIVE_SCOPE_STATUSES: frozenset[str] = frozenset({"active", "open"})
EFFECT_GATE_STICKY_REASON = "effect_gate_route_receipt_rejected"
# Steps that name a route receipt and therefore stick to it (step 1 has no
# trustworthy receipt id yet; step 0 never rejects).
_NON_STICKY_CODES: frozenset[str] = frozenset(
    {"effect_gate_envelope_missing", "effect_gate_envelope_identity_mismatch"}
)


class RouteReceiptReader(Protocol):
    async def read_route_receipt(
        self, sdk_run_id: str, receipt_id: str, *, db: Any | None = None
    ) -> Any: ...


class ScopeStatusReader(Protocol):
    async def read_head_status(
        self, task_scope_id: str, *, db: Any | None = None
    ) -> str | None: ...


class EffectGateRejected(Exception):
    """Raised inside the reservation transaction when the head moved (step 5 re-check)."""

    def __init__(self, result: ToolResult) -> None:
        super().__init__(result.error_code)
        self.result = result


class EffectGate:
    """Binary per-effect admission for PROJECT_EFFECT Tools."""

    def __init__(
        self,
        *,
        binding_store: Any,
        route_ledger: RouteReceiptReader,
        scope_store: CanonicalTaskScopeStore,
        authority_resolver: Callable[[RunId], Any],
        exposure_resolver: Callable[[RunId], Any],
        clock: Callable[[], float] = time.time,
        fault_inject: Callable[[str], Any] | None = None,
    ) -> None:
        # Composition contract (AC-6①): every piece is mandatory; a missing one
        # fails the stack build instead of degrading into "no gate".
        for name, value in (
            ("binding_store", binding_store),
            ("route_ledger", route_ledger),
            ("scope_store", scope_store),
            ("authority_resolver", authority_resolver),
            ("exposure_resolver", exposure_resolver),
        ):
            if value is None:
                raise TypeError(f"sdk_effect_gate_composition_missing:{name}")
        if not callable(authority_resolver) or not callable(exposure_resolver):
            raise TypeError("sdk_effect_gate_composition_missing:resolver_not_callable")
        if not callable(getattr(scope_store, "_connection", None)) or not callable(
            getattr(scope_store, "read_head_status", None)
        ):
            raise TypeError("sdk_effect_gate_composition_missing:scope_store")
        self._binding_store = binding_store
        self._route_ledger = route_ledger
        self._scope_store = scope_store
        self._authority_resolver = authority_resolver
        self._exposure_resolver = exposure_resolver
        self._clock = clock
        self._fault_inject = fault_inject

    async def _fault(self, point: str) -> None:
        if self._fault_inject is None:
            return
        outcome = self._fault_inject(point)
        if inspect.isawaitable(outcome):
            await outcome

    # ------------------------------------------------------------- helpers

    def _is_project_effect(self, run_id: RunId, tool_name: str) -> bool:
        exposure = self._exposure_resolver(run_id)
        # RuntimeToolCatalogError (hidden Tool) propagates: that is a whole-Run
        # fault (catalog_execution_policy_unavailable), not a rejection.
        policy = exposure.execution_policy(run_id, tool_name)
        return policy.effect_class is ToolEffectClass.PROJECT_EFFECT

    @staticmethod
    def _reject_call_id(context: ToolContext, call_id: CallId | None) -> CallId:
        reject_call_id = call_id if call_id is not None else context.call_id
        if reject_call_id is None:
            raise RuntimeError("effect_gate_call_identity_missing")
        return reject_call_id

    async def _sticky_tx(
        self, db: aiosqlite.Connection, run_id: str, route_receipt_id: str
    ) -> str | None:
        row = await CanonicalTaskScopeStore._fetchone(
            db,
            "SELECT reason_code FROM effect_gate_rejections "
            "WHERE sdk_run_id=? AND route_receipt_id=?",
            (run_id, route_receipt_id),
        )
        return None if row is None else str(row["reason_code"])

    async def memoize_rejection(self, context: ToolContext, result: ToolResult) -> None:
        """Step 2 memo: record ``(sdk_run_id, route_receipt_id) → reason`` (first writer wins).

        Called by the executor for every gate rejection that names a route
        receipt; a later rejection under the same receipt is reported as
        ``effect_gate_route_receipt_rejected`` until a new receipt is issued.
        """

        envelope = context.task_execution_envelope
        code = result.error_code or ""
        if envelope is None or not envelope.route_receipt_id or code in _NON_STICKY_CODES:
            return
        run_id = context.run_id.value
        receipt_id = str(envelope.route_receipt_id)
        async with self._scope_store._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO effect_gate_rejections("
                    "rejection_id,sdk_run_id,route_receipt_id,reason_code,created_at) "
                    "VALUES (?,?,?,?,?)",
                    (
                        _uuid(f"effect-gate-rejection:{run_id}:{receipt_id}"),
                        run_id,
                        receipt_id,
                        code if code != EFFECT_GATE_STICKY_REASON else "effect_gate_rejected",
                        float(self._clock()),
                    ),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def _reject_and_memo(
        self, context: ToolContext, result: ToolResult
    ) -> ToolResult:
        await self.memoize_rejection(context, result)
        return result

    # -------------------------------------------------------------- verify

    async def verify(
        self,
        context: ToolContext,
        tool_name: str,
        *,
        call_id: CallId | None = None,
    ) -> ToolResult | None:
        run_id = context.run_id
        if not self._is_project_effect(run_id, tool_name):
            return None
        reject_call_id = self._reject_call_id(context, call_id)

        def reject(code: str) -> ToolResult:
            return ToolResult.rejected(reject_call_id, code, EFFECT_GATE_PUBLIC_MESSAGE)

        # 1. envelope present + exact identity echo (effect_id echo is mandatory)
        envelope = context.task_execution_envelope
        if envelope is None:
            return reject("effect_gate_envelope_missing")
        if (
            context.effect_id is None
            or envelope.run_id != run_id
            or envelope.call_id != reject_call_id
            or envelope.effect_id != context.effect_id
            or envelope.tool_name != tool_name
        ):
            return reject("effect_gate_envelope_identity_mismatch")
        if any(
            value is None
            for value in (
                envelope.route_receipt_id,
                envelope.route_receipt_hash,
                envelope.task_scope_id,
                envelope.root_id,
                envelope.root_identity_hash,
                envelope.binding_set_revision,
                envelope.binding_set_receipt_id,
                envelope.binding_set_receipt_hash,
            )
        ):
            return reject("effect_gate_envelope_missing")

        # 3. frozen Run-admission authority (scope, project-bound) — no DB read
        authority = self._authority_resolver(run_id)
        work = authority.task_work_context
        resolution = dict(getattr(authority, "workspace_resolution", None) or {})
        if envelope.task_scope_id != work.task_scope_id:
            return await self._reject_and_memo(
                context, reject("effect_gate_frozen_scope_mismatch")
            )
        frozen_root = work.workspace_root
        kind = str(resolution.get("kind") or "")
        if kind in {"projectless", "missing"} or not frozen_root:
            return await self._reject_and_memo(
                context, reject("effect_gate_projectless_project_effect")
            )

        scope_id = str(envelope.task_scope_id)
        receipt_id = str(envelope.route_receipt_id)
        # 2 / 4 / 5 / 6 in ONE read snapshot (explicit deferred transaction:
        # SHARED lock from the first SELECT until ROLLBACK).  The verdict is
        # only *computed* here; the sticky memo is written after the snapshot
        # closes (a write under our own SHARED lock would self-deadlock in
        # rollback-journal mode).
        code = await self._snapshot_verdict(
            run_id=run_id,
            receipt_id=receipt_id,
            scope_id=scope_id,
            envelope=envelope,
            frozen_root=frozen_root,
        )
        await self._fault("effect-gate-snapshot-closed")
        if code is None:
            return None
        if code == EFFECT_GATE_STICKY_REASON:
            return reject(code)
        return await self._reject_and_memo(context, reject(code))

    async def _snapshot_verdict(
        self,
        *,
        run_id: RunId,
        receipt_id: str,
        scope_id: str,
        envelope: Any,
        frozen_root: str,
    ) -> str | None:
        """Steps 2/4/3(cont.)/5/6 over one read snapshot; returns the reason code or None."""

        async with self._scope_store._connection() as db:
            await db.execute("BEGIN")
            try:
                sticky = await self._sticky_tx(db, run_id.value, receipt_id)
                if sticky is not None:
                    return EFFECT_GATE_STICKY_REASON
                route_receipt = await self._route_ledger.read_route_receipt(
                    run_id.value, receipt_id, db=db
                )
                if route_receipt is None:
                    return "workspace_binding_route_authority_missing"
                try:
                    verified = await self._binding_store.verify_task_execution_envelope(
                        envelope, route_receipt, db=db
                    )
                except WorkspaceBindingError as exc:
                    return exc.code
                await self._fault("effect-gate-after-verify")
                # 3 (cont.). verified root must be the frozen write root
                if verified.root.canonical_path != frozen_root:
                    return "effect_gate_frozen_root_mismatch"
                # 5. strict head == receipt revision (same snapshot)
                superseded = await self._head_mismatch_tx(db, envelope, scope_id)
                if superseded is not None:
                    return superseded
                # 6. TaskScope lifecycle (same snapshot)
                status = await self._scope_store.read_head_status(scope_id, db=db)
                if status not in PROJECT_EFFECT_ACTIVE_SCOPE_STATUSES:
                    return "effect_gate_task_scope_not_active"
                return None
            finally:
                await db.rollback()

    async def _head_mismatch_tx(
        self, db: aiosqlite.Connection, envelope: Any, scope_id: str
    ) -> str | None:
        try:
            head = await self._binding_store.current_receipt(scope_id, db=db)
        except TaskScopeNotFound:
            # No binding head row at all: authority is missing, not superseded
            # (review F-7 — an operator must not read this as a user append).
            return "workspace_binding_effect_authority_missing"
        if (
            head.binding_set_revision != envelope.binding_set_revision
            or head.receipt_id != envelope.binding_set_receipt_id
            or head.receipt_hash != envelope.binding_set_receipt_hash
        ):
            return "workspace_binding_receipt_superseded"
        return None

    # ------------------------------------------------- reservation re-check

    def reservation_check(
        self, context: ToolContext, tool_name: str, *, call_id: CallId | None = None
    ) -> Callable[[aiosqlite.Connection], Any] | None:
        """Write-lock re-check hook for the Harness evidence reservation transaction.

        Returns ``None`` for non-project Tools.  For a PROJECT_EFFECT the hook
        re-reads the binding head under the reservation's ``BEGIN IMMEDIATE``
        and raises :class:`EffectGateRejected` when it no longer equals the
        envelope receipt — the reservation is rolled back and nothing is
        dispatched.
        """

        if not self._is_project_effect(context.run_id, tool_name):
            return None
        envelope = context.task_execution_envelope
        reject_call_id = self._reject_call_id(context, call_id)
        if envelope is None or envelope.task_scope_id is None:
            raise RuntimeError("effect_gate_envelope_missing_at_reservation")
        scope_id = str(envelope.task_scope_id)

        async def check(db: aiosqlite.Connection) -> None:
            code = await self._head_mismatch_tx(db, envelope, scope_id)
            if code is None:
                status = await self._scope_store.read_head_status(scope_id, db=db)
                if status not in PROJECT_EFFECT_ACTIVE_SCOPE_STATUSES:
                    code = "effect_gate_task_scope_not_active"
            if code is not None:
                raise EffectGateRejected(
                    ToolResult.rejected(reject_call_id, code, EFFECT_GATE_PUBLIC_MESSAGE)
                )

        return check



# ---------------------------------------------------------------------------
# Objective event mapping (S5b Task 2, design-freeze §2) — deterministic:
# tool name + exit code + registered test-runner allowlist; no keywords, no
# regex.  Every PROJECT_EFFECT tool (§1) has exactly one rule (exhaustiveness
# is asserted by tests/sdk_adapters/test_objective_events.py).
# ---------------------------------------------------------------------------

OBJECTIVE_FILE_EVENT = "host.file"
OBJECTIVE_TEST_RUNNER_RULE = "test-runner-rule"  # host.test iff allowlisted head + exit code
OBJECTIVE_EVENT_MAP: Mapping[str, str] = MappingProxyType(
    {
        "write_file": OBJECTIVE_FILE_EVENT,
        "file_write": OBJECTIVE_FILE_EVENT,
        "edit_file": OBJECTIVE_FILE_EVENT,
        "move_file": OBJECTIVE_FILE_EVENT,
        "file_organize": OBJECTIVE_FILE_EVENT,
        "run_shell": OBJECTIVE_TEST_RUNNER_RULE,
        "process_start": OBJECTIVE_TEST_RUNNER_RULE,
        "doc_create": OBJECTIVE_FILE_EVENT,
        "doc_edit": OBJECTIVE_FILE_EVENT,
        "excel_create": OBJECTIVE_FILE_EVENT,
        "ppt_create": OBJECTIVE_FILE_EVENT,
        "pdf_export": OBJECTIVE_FILE_EVENT,
        "download_file": OBJECTIVE_FILE_EVENT,
        "workspace_prepare": OBJECTIVE_FILE_EVENT,
    }
)
# Registered test runners, matched on the *leading* command tokens only.
TEST_RUNNER_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("pytest",),
    ("python", "-m", "pytest"),
    ("npm", "test"),
    ("pnpm", "test"),
    ("cargo", "test"),
    ("go", "test"),
    ("vitest",),
    ("jest",),
)
_TARGET_ARGUMENT_KEYS: tuple[str, ...] = (
    "path", "file_path", "source", "source_path", "destination", "destination_path",
    "target", "target_path", "output_path", "output", "directory", "root", "url",
)
_EXIT_CODE_KEYS: tuple[str, ...] = ("exit_code", "returncode", "return_code", "status_code")


def _command_tokens(arguments: Mapping[str, Any]) -> list[str]:
    command = arguments.get("command")
    tokens: list[str] = []
    if isinstance(command, str):
        tokens.extend(command.split())
    elif isinstance(command, (list, tuple)):
        tokens.extend(str(item) for item in command)
    args = arguments.get("args")
    if isinstance(args, (list, tuple)):
        tokens.extend(str(item) for item in args)
    return tokens


# Shell control / redirection tokens: a command line that continues past the
# allowlisted runner head with any of these is a compound command (``pytest &&
# rm -rf build``), which is a file effect, not "just a test run".  Token-level
# equality / prefix checks only — no regex, no keyword search inside tokens.
_SHELL_OPERATOR_TOKENS: tuple[str, ...] = (";", "&&", "||", "|", "&", ">", ">>", "<", "$(", "`")
_SHELL_OPERATOR_PREFIXES: tuple[str, ...] = ("$(", "`", ">", "<", "|", "&", ";")


def _has_shell_operator(tokens: list[str]) -> bool:
    for token in tokens:
        if token in _SHELL_OPERATOR_TOKENS:
            return True
        if token.startswith(_SHELL_OPERATOR_PREFIXES) or token.endswith((";", "|", "&", "`")):
            return True
        if any(marker in token for marker in ("&&", "||", "$(", ";")):
            return True
    return False


def _test_runner_head(tokens: list[str]) -> str | None:
    """Allowlisted runner head, or ``None`` for compound / operator-bearing lines
    (Task 2 review F-4)."""

    for head in TEST_RUNNER_COMMANDS:
        if tuple(tokens[: len(head)]) == head:
            if _has_shell_operator(tokens[len(head):]):
                return None
            return " ".join(head)
    return None


def _exit_code(result: ToolResult) -> int | None:
    value = result.value
    if not isinstance(value, Mapping):
        return None
    for key in _EXIT_CODE_KEYS:
        code = value.get(key)
        if isinstance(code, bool):
            continue
        if isinstance(code, int):
            return code
    return None


def _targets(arguments: Mapping[str, Any]) -> tuple[list[str], bool]:
    """Public target paths of one call, credential-shaped fragments redacted.

    Task 2 review F-1: the values come straight from model arguments and are
    recorded after the physical effect settled, so the Host must never raise on
    them — a credential-shaped fragment (``bearer …``, ``sk-…`` …) is replaced
    by the stable placeholder and ``redacted`` is reported to the caller.
    """

    from deskpet.task_scope.protocol import redact_credential_shapes

    targets: list[str] = []
    redacted = False
    raw: list[str] = []
    for key in _TARGET_ARGUMENT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value:
            raw.append(value)
    paths = arguments.get("paths")
    if isinstance(paths, (list, tuple)):
        raw.extend(str(item) for item in paths if isinstance(item, str) and item)
    for item in raw:
        clean, hit = redact_credential_shapes(item)
        redacted = redacted or hit
        targets.append(clean)
    return targets, redacted


def _public_error_code(result: ToolResult) -> tuple[str | None, bool]:
    from deskpet.task_scope.protocol import redact_credential_shapes

    code = result.error_code
    if code is None:
        return None, False
    return redact_credential_shapes(str(code))


def classify_objective_event(
    tool_name: str,
    arguments: Mapping[str, Any],
    result: ToolResult,
    *,
    effect_id: str | None = None,
    call_id: str | None = None,
) -> ObjectiveEventSpec | None:
    """Map one settled PROJECT_EFFECT execution to its ``host.*`` objective event.

    Returns ``None`` for tools outside the frozen list (read tools, control
    tools).  The payload carries only public identity facts: tool name, ids,
    outcome / error code, the target paths (never file content) or the
    allowlisted command head + exit code (never the full command line or
    output).
    """

    rule = OBJECTIVE_EVENT_MAP.get(tool_name)
    if rule is None:
        return None
    error_code, error_redacted = _public_error_code(result)
    base: dict[str, object] = {
        "tool_name": tool_name,
        "effect_id": effect_id,
        "call_id": call_id,
        "outcome": result.outcome.value,
        "error_code": error_code,
    }
    if rule == OBJECTIVE_TEST_RUNNER_RULE:
        head = _test_runner_head(_command_tokens(arguments))
        # ``shell=True`` hands the whole line to a shell: operators may hide in
        # a single token, so the call is a file effect regardless of its head.
        if arguments.get("shell") is True:
            head = None
        exit_code = _exit_code(result)
        if head is not None and exit_code is not None:
            payload: dict[str, object] = {**base, "command_head": head, "exit_code": exit_code}
            if error_redacted:
                payload["redacted"] = True
            return ObjectiveEventSpec("host.test", payload)
        targets, targets_redacted = _targets(arguments)
        payload = {**base, "command_head": head, "exit_code": exit_code, "targets": targets}
        if error_redacted or targets_redacted:
            payload["redacted"] = True
        return ObjectiveEventSpec("host.file", payload)
    targets, targets_redacted = _targets(arguments)
    payload = {**base, "targets": targets}
    if error_redacted or targets_redacted:
        payload["redacted"] = True
    return ObjectiveEventSpec("host.file", payload)


__all__ = [
    "EFFECT_GATE_PUBLIC_MESSAGE",
    "OBJECTIVE_EVENT_MAP",
    "OBJECTIVE_FILE_EVENT",
    "OBJECTIVE_TEST_RUNNER_RULE",
    "PROJECT_EFFECT_ACTIVE_SCOPE_STATUSES",
    "TEST_RUNNER_COMMANDS",
    "EffectGate",
    "RouteReceiptReader",
    "ScopeStatusReader",
    "classify_objective_event",
]
