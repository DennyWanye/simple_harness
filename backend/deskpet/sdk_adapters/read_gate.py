# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Call-time workspace gate for the Run's read-class file Tools (defect F-Z1).

Why this exists
---------------
``ProductForegroundToolPort.freeze`` projects the SDK catalog once, at Run
start.  In the normal HM-TO-A6 shape every Run starts unrouted and the model
calls ``context_route`` as its *first* Tool, so at freeze time
``task_scope_id is None``: the Run is ``projectless`` with
``primary_route_capable=True``.  ``filter_sdk_catalog_for_workspace`` then kept
only ``projectless_admission == "safe"`` Tools plus
``PROJECT_EFFECT_TOOL_NAMES`` — so the Run could ``write_file`` / ``edit_file``
/ ``run_shell`` but could never ``read_file`` (incident Z,
``DECISION-Z-UNSCOPED-GUIDANCE-WRAP-UP.md`` §三, followup **F-Z1**).

The asymmetry was never about privacy being stricter for reads.  Write-class
Tools are exposed in an unrouted Run only because *each call* is still gated:
the SDK react barrier (route REQUIRED + UNROUTED → ``ROUTE_BARRIER_NOT_OBSERVED``),
the Host ``TaskExecutionEnvelope`` authority, and the
:class:`~deskpet.sdk_adapters.effect_gate.EffectGate` verify the durable route
receipt and the bound workspace root before any physical effect.  Read-class
Tools are ``requires_project`` because a read must be confined to the bound
workspace root (S4 binding authority: canonical path + POSIX inode identity,
never a Session-derived guess) — and they had **no** equivalent call-time gate,
so "expose them" would have meant "read any path with no bound root".

This module is that missing gate.  With it in place the projection may expose
the read family in a route-capable projectless Run (the frozen identity
semantics are untouched: the same describe → activate nonce/hash chain, the
same ``_FrozenCapabilitySpec``), because every *call* must still prove:

1. the Run has, by call time, a **durable** ``context_route`` decision
   (``context_route_decisions`` — the same v45 ledger row the EffectGate and
   ``task_scope_update`` read) that binds a TaskScope;
2. that TaskScope's committed binding set resolves to a verified workspace root
   (``WorkspaceBindingAuthorityStore.verify_effect_authority`` — canonical path,
   live inode identity, ``workspace_root_too_broad`` refusal);
3. every path the arguments name resolves (symlinks included) **inside** that
   root.

A standalone route (``direct_standalone`` / ``memory_standalone``) writes a
decision row with ``task_scope_id IS NULL``, so step 1 fails closed and reads
stay refused there — exactly as PROJECT_EFFECT Tools are.

The verdict is binary and never raises: ``None`` admits, otherwise a
``ToolResult.rejected`` carrying one stable reason code.  Reason codes:

``read_requires_bound_workspace``
    No durable task route yet in this Run.  The public message names the one
    executable next step — ``context_route`` with
    ``create_new`` / ``resume_existing`` / ``continue_active`` — and says the
    read may be retried *in this same Run* right after.
``read_workspace_root_unavailable``
    A task route exists but its binding set has no verifiable root (never
    bound, root moved/deleted, identity drift).  Not retryable by rerouting the
    same task; the model is told to stop and say so.
``path_outside_workspace_root``
    The requested path resolves outside the bound root (parent directory,
    absolute escape, symlink pointing out, or a ``..`` segment in a glob/grep
    pattern).

Admitted calls run with the verified root projected into
``ToolExecutionContext.workspace`` / ``.write_scope_root`` for the dispatch
lifetime only (:func:`read_root_projection`), so a relative path and a
``path``-less ``glob``/``grep`` resolve against the bound root instead of the
backend process cwd, and the handlers' own containment check
(``agent.write_scope.write_scope_check``) applies as defence in depth.

Every decision — admit and refuse alike — is recorded in
``host_pre_admission_audit``: the reason code and a canonical hash of the
arguments, never the path, the pattern or any file content.  The rows reuse the
existing ``payload_kind='context_route'`` (the gate's whole subject *is* the
Run's route state) and namespace themselves with the
``workspace_read.`` reason-code prefix, exactly as the analysis lane namespaces
``memory.analysis.blocked`` under ``payload_kind='analysis_result'``; a
dedicated ``payload_kind`` would need a CHECK-widening schema migration, which
this change deliberately does not take.  Read them with
:func:`workspace_read_audit_rows`.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from simple_harness import CallId, RunId
from simple_harness.tools import ToolContext, ToolResult

from deskpet.sdk_adapters.task_scope_mutation import write_pre_admission_audit_tx
from deskpet.sdk_adapters.tool_authority import PROJECT_READ_TOOL_NAMES
from deskpet.task_scope.workspace_bindings import WorkspaceBindingError
from deskpet.types.task_work_context import PrimaryRunWorkContext

logger = logging.getLogger(__name__)

# The read-class file Tools admitted into a route-capable projectless Run.
# Defined beside ``PROJECT_EFFECT_TOOL_NAMES`` in ``tool_authority`` because the
# Run-start projection guard and this gate must agree by construction.
__doc__ += f"\nGated read Tools: {', '.join(PROJECT_READ_TOOL_NAMES)}.\n"

# Argument keys that carry a filesystem target for the Tools above.  Absent =
# "the bound root itself" (glob/grep default to ``context.workspace``), which
# is inside the root by construction.
READ_PATH_ARGUMENT_KEYS: tuple[str, ...] = ("path",)
# Pattern arguments are not paths, but ``Path.rglob("../*")`` really does walk
# out of the root, so a ``..`` segment in one is an escape attempt.
READ_PATTERN_ARGUMENT_KEYS: tuple[str, ...] = ("pattern", "glob")

READ_ROUTE_REASON = "read_requires_bound_workspace"
READ_ROOT_REASON = "read_workspace_root_unavailable"
READ_OUTSIDE_REASON = "path_outside_workspace_root"

READ_ROUTE_MESSAGE = (
    "This Tool reads inside a task workspace, so it needs a bound TaskScope. "
    "This Run has not routed yet. Call context_route once with "
    "route=continue_active (the Run's current active task), resume_existing "
    "(an exact task_scope_id) or create_new (a new task with title and goal), "
    "then call this read Tool again in this same Run — the projection already "
    "carries it, only the workspace binding was missing. Under "
    "direct_standalone or memory_standalone reads stay refused."
)
READ_ROOT_MESSAGE = (
    "This Run is routed to a task whose workspace root cannot be verified, so "
    "no read is scoped to it. Routing to the same task again will not change "
    "this. Stop calling file Tools and tell the user this task has no usable "
    "bound project directory."
)
READ_OUTSIDE_MESSAGE = (
    "Read rejected: the requested path is outside this task's bound workspace "
    "root. Reads never leave that root — parent directories, absolute paths "
    "elsewhere, symlinks pointing out and '..' segments in a glob/grep pattern "
    "are all refused. Use a path inside the workspace, or list_directory on "
    "the workspace root to see what is readable."
)
_READ_GATE_MESSAGES: Mapping[str, str] = {
    READ_ROUTE_REASON: READ_ROUTE_MESSAGE,
    READ_ROOT_REASON: READ_ROOT_MESSAGE,
    READ_OUTSIDE_REASON: READ_OUTSIDE_MESSAGE,
}
# ``host_pre_admission_audit.payload_kind`` is CHECK-constrained to three
# values; the read gate reuses the route kind and namespaces its rows by the
# reason-code prefix (same pattern as ``memory.analysis.blocked``).
READ_GATE_AUDIT_KIND = "context_route"
READ_GATE_AUDIT_PREFIX = "workspace_read."
READ_GATE_ADMITTED_REASON = "admitted"


def read_gate_public_message(code: str) -> str:
    return _READ_GATE_MESSAGES.get(code, READ_ROUTE_MESSAGE)


def is_gated_read_tool(authority: Any, tool_name: str) -> bool:
    """Is ``tool_name`` a read Tool admitted only by the F-Z1 exemption?

    The exemption exists exactly for the Run shape whose projection was frozen
    ``primary_route_capable`` — i.e. an unscoped primary Run
    (:class:`PrimaryRunWorkContext`), which is what
    ``candidate.task_scope_id is None`` produces at freeze.  Every other Run
    (legacy Session, ``project_bound``, a projectless Run that *was* already
    scoped) keeps exactly today's behaviour: those either never expose the read
    Tools, or expose them under an already-frozen root.
    """

    if str(tool_name) not in PROJECT_READ_TOOL_NAMES:
        return False
    work = getattr(authority, "task_work_context", None)
    return isinstance(work, PrimaryRunWorkContext)


# --------------------------------------------------------------------------
# Verified-root projection for the dispatch lifetime (mirrors the PROJECT_EFFECT
# ``_primary_effect_root`` projection in effect_gate.py).
# --------------------------------------------------------------------------

_workspace_read_root: ContextVar[tuple[str, str, str] | None] = ContextVar(
    "workspace_read_root", default=None
)


def read_root_projection() -> tuple[str, str, str] | None:
    """``(run_id, call_id, root)`` of the read admitted for this dispatch."""

    return _workspace_read_root.get()


def project_read_execution_context(base: Any) -> Any:
    """Apply the verified read root to ``base`` for this dispatch lifetime."""

    projection = _workspace_read_root.get()
    if projection is None:
        return base
    run_id, call_id, root = projection
    if base.run_id != run_id:
        # Another Run's context built inside this dispatch task claims nothing
        # from the read admission; hand it back untouched (fail closed: it just
        # keeps no workspace).
        return base
    if base.call_id != call_id:
        raise RuntimeError("workspace_read_context_identity_mismatch")
    return replace(base, workspace=root, write_scope_root=root)


# --------------------------------------------------------------------------
# Path containment
# --------------------------------------------------------------------------


def _resolved(path: Path) -> Path | None:
    try:
        return path.expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def path_within_root(candidate: str, root: str) -> bool:
    """Symlink-resolving containment: is ``candidate`` inside ``root``?

    Fail-closed: an unresolvable candidate or root is *not* inside.  Both sides
    are fully resolved first, so ``root/link -> /etc`` and ``root/../x`` are
    both refused, and a relative path is taken against the bound root (the same
    rule ``normalize_model_path`` applies inside the handlers).
    """

    resolved_root = _resolved(Path(root))
    if resolved_root is None:
        return False
    raw = Path(candidate).expanduser()
    if not raw.is_absolute():
        raw = resolved_root / raw
    resolved = _resolved(raw)
    if resolved is None:
        return False
    if resolved == resolved_root:
        return True
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return False
    return True


def _pattern_escapes(value: object) -> bool:
    """``Path.rglob('../*')`` walks out of the root — treat ``..`` as an escape."""

    if not isinstance(value, str) or not value:
        return False
    return ".." in Path(value).parts


def read_arguments_violation(arguments: Mapping[str, Any], root: str) -> bool:
    """Does any path/pattern argument leave ``root``?  Fail closed."""

    for key in READ_PATH_ARGUMENT_KEYS:
        value = arguments.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str) or not path_within_root(value, root):
            return True
    for key in READ_PATTERN_ARGUMENT_KEYS:
        if _pattern_escapes(arguments.get(key)):
            return True
    return False


# --------------------------------------------------------------------------
# Ports
# --------------------------------------------------------------------------


class ReadRouteLedger(Protocol):
    async def latest_route_decision_for_run(
        self, sdk_run_id: str, *, task_only: bool = False
    ) -> Mapping[str, Any] | None: ...

    async def read_route_receipt(
        self, sdk_run_id: str, receipt_id: str, *, db: Any | None = None
    ) -> Any | None: ...


class WorkspaceReadGate:
    """Binary call-time admission for the F-Z1 read family."""

    def __init__(
        self,
        *,
        binding_store: Any,
        route_ledger: ReadRouteLedger,
        scope_store: Any,
        authority_resolver: Callable[[RunId], Any],
        clock: Callable[[], float] = time.time,
    ) -> None:
        for name, value in (
            ("binding_store", binding_store),
            ("route_ledger", route_ledger),
            ("scope_store", scope_store),
            ("authority_resolver", authority_resolver),
        ):
            if value is None:
                raise TypeError(f"sdk_read_gate_composition_missing:{name}")
        if not callable(authority_resolver):
            raise TypeError("sdk_read_gate_composition_missing:resolver_not_callable")
        if not callable(getattr(scope_store, "_connection", None)):
            raise TypeError("sdk_read_gate_composition_missing:scope_store")
        self._binding_store = binding_store
        self._route_ledger = route_ledger
        self._scope_store = scope_store
        self._authority_resolver = authority_resolver
        self._clock = clock
        # ``(run_id, call_id) -> verified root`` handed from ``verify`` to the
        # dispatch scope of that same call.  Bounded by construction: one entry
        # per admitted call, popped by ``execution_scope``.
        self._admitted: dict[tuple[str, str], str] = {}

    # ---------------------------------------------------------------- root

    async def bound_root(self, run_id: str) -> tuple[str | None, str | None]:
        """``(verified root, reason code)`` for one Run's durable task route.

        Exactly one of the two is set.  ``task_only=True``: a standalone route
        issued *after* a task route must not un-bind reads mid-Run, and a Run
        that only ever routed standalone has no task row at all — both cases
        are what the S5b same-Run single-scope rule already encodes for
        ``task_scope_update``.
        """

        decision = await self._route_ledger.latest_route_decision_for_run(
            run_id, task_only=True
        )
        if decision is None or not decision.get("task_scope_id"):
            return None, READ_ROUTE_REASON
        scope_id = str(decision["task_scope_id"])
        route_receipt = await self._route_ledger.read_route_receipt(
            run_id, str(decision.get("receipt_id") or "")
        )
        if route_receipt is None or str(
            getattr(route_receipt, "task_scope_id", "") or ""
        ) != scope_id:
            return None, READ_ROOT_REASON
        # Same lineage the EffectGate walks for a write: the *exact* binding
        # revision this route committed, never the live head.  A later append
        # cannot silently widen what an already-routed Run may read.
        try:
            receipt = await self._binding_store.verify_route_binding(route_receipt)
        except WorkspaceBindingError:
            return None, READ_ROOT_REASON
        roots: list[str] = []
        for root_hash in getattr(receipt, "root_identity_hashes", ()):
            try:
                authority = await self._binding_store.verify_effect_authority(
                    task_scope_id=scope_id,
                    binding_set_revision=receipt.binding_set_revision,
                    binding_set_receipt_id=receipt.receipt_id,
                    binding_set_receipt_hash=receipt.receipt_hash,
                    root_identity_hash=str(root_hash),
                )
            except WorkspaceBindingError:
                return None, READ_ROOT_REASON
            roots.append(authority.root.canonical_path)
        # Never select one root implicitly (same rule as the Run-start freeze:
        # zero/multi root carries no single write or read authority).
        if len(roots) != 1:
            return None, READ_ROOT_REASON
        return roots[0], None

    # -------------------------------------------------------------- verify

    async def verify(
        self,
        context: ToolContext,
        tool_name: str,
        *,
        call_id: CallId | None = None,
        arguments: Mapping[str, Any] | None = None,
    ) -> ToolResult | None:
        run_id = context.run_id
        authority = self._authority_resolver(run_id)
        if not is_gated_read_tool(authority, tool_name):
            return None
        reject_call_id = call_id if call_id is not None else context.call_id
        if reject_call_id is None:
            raise RuntimeError("read_gate_call_identity_missing")
        payload = dict(arguments or {})

        root, code = await self.bound_root(run_id.value)
        if code is None and read_arguments_violation(payload, str(root)):
            code = READ_OUTSIDE_REASON
        await self._audit(
            run_id.value, code or READ_GATE_ADMITTED_REASON, tool_name, payload
        )
        if code is None:
            self._admitted[(run_id.value, reject_call_id.value)] = str(root)
            return None
        logger.warning(
            "workspace_read_denied run=%s tool=%s reason=%s",
            run_id.value,
            tool_name,
            code,
        )
        return ToolResult.rejected(
            reject_call_id, code, read_gate_public_message(code)
        )

    # ----------------------------------------------------- dispatch scope

    @asynccontextmanager
    async def execution_scope(
        self,
        context: ToolContext,
        tool_name: str,
        *,
        call_id: CallId | None = None,
    ):
        """Project the verified root for the dispatch of an admitted read."""

        authority = self._authority_resolver(context.run_id)
        resolved_call_id = call_id if call_id is not None else context.call_id
        key = (
            context.run_id.value,
            "" if resolved_call_id is None else resolved_call_id.value,
        )
        if not is_gated_read_tool(authority, tool_name):
            yield
            return
        root = self._admitted.pop(key, None)
        if root is None:
            # ``verify`` did not admit this exact call (replay path, or an
            # executor that skipped the gate): never fabricate a root.
            raise RuntimeError("workspace_read_root_unverified")
        token = _workspace_read_root.set((key[0], key[1], root))
        try:
            yield
        finally:
            _workspace_read_root.reset(token)

    # --------------------------------------------------------------- audit

    async def _audit(
        self,
        run_id: str,
        reason_code: str,
        tool_name: str,
        payload: Mapping[str, Any],
    ) -> None:
        """One ``host_pre_admission_audit`` row per gate decision.

        Reason code + Tool name + a canonical hash of the arguments.  No path,
        no pattern, no file content ever reaches the audit trail.
        """

        from deskpet.tools.capabilities import canonical_hash

        try:
            async with self._scope_store._connection() as db:  # noqa: SLF001
                await db.execute("BEGIN IMMEDIATE")
                try:
                    await write_pre_admission_audit_tx(
                        db,
                        sdk_run_id=run_id,
                        payload_kind=READ_GATE_AUDIT_KIND,
                        reason_code=(
                            f"{READ_GATE_AUDIT_PREFIX}{tool_name}.{reason_code}"
                        ),
                        payload_hash=canonical_hash(_hashable(payload)),
                        now=float(self._clock()),
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise
        except Exception as exc:  # noqa: BLE001
            # The receipt is evidence, not authority: losing it must never turn
            # a refusal into an admission, nor an admission into a Run fault.
            logger.warning(
                "workspace_read_audit_unavailable run=%s tool=%s reason=%s error=%s",
                run_id,
                tool_name,
                reason_code,
                exc,
            )


async def workspace_read_audit_rows(
    db_path: Any,
) -> list[tuple[str | None, str, str]]:
    """Durable read-gate receipts ``(sdk_run_id, reason_code, payload_hash)``."""

    import aiosqlite

    async with aiosqlite.connect(f"file:{Path(db_path)}?mode=ro", uri=True) as db:
        cursor = await db.execute(
            "SELECT sdk_run_id,reason_code,payload_hash FROM host_pre_admission_audit "
            "WHERE payload_kind=? AND reason_code LIKE ? ORDER BY created_at,audit_id",
            (READ_GATE_AUDIT_KIND, f"{READ_GATE_AUDIT_PREFIX}%"),
        )
        rows = await cursor.fetchall()
        await cursor.close()
    return [
        (None if row[0] is None else str(row[0]), str(row[1]), str(row[2]))
        for row in rows
    ]


def _hashable(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _json_safe(value) for key, value in payload.items()}


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


__all__ = [
    "PROJECT_READ_TOOL_NAMES",
    "READ_GATE_ADMITTED_REASON",
    "READ_GATE_AUDIT_KIND",
    "READ_GATE_AUDIT_PREFIX",
    "READ_OUTSIDE_REASON",
    "READ_ROOT_REASON",
    "READ_ROUTE_REASON",
    "WorkspaceReadGate",
    "is_gated_read_tool",
    "path_within_root",
    "project_read_execution_context",
    "read_arguments_violation",
    "read_gate_public_message",
    "read_root_projection",
    "workspace_read_audit_rows",
]
