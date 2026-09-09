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
    The requested path resolves outside **every** bound root *and* outside the
    configured workspace (parent directory, absolute escape, symlink pointing
    out, or a ``..`` segment in a glob/grep pattern).  Since F-Z1b this is the
    verdict only when the path leaves the configured workspace root entirely;
    a path that is merely outside the *task's* roots but still inside the
    configured workspace enters the binding-proposal path below.
``workspace_root_too_broad``
    The S4 code, raised by the F-Z1b candidate-root rule before any proposal:
    the requested path names the configured workspace root itself, its nearest
    existing directory ancestor *is* that root, or the candidate would be a
    common parent of a root this task already holds.
``context_route_binding_authorization_required``
    F-Z1b under ``manual`` policy: a durable binding proposal + challenge was
    issued for the candidate root and the 『项目目录授权』 card is now pending.
``read_workspace_binding_revised``
    F-Z1b under ``auto`` policy: the candidate root was bound with a
    ``policy:auto`` grant and the task's binding set moved one revision
    forward.  The read is *not* admitted in the same call — see below.

F-Z1b: the read gate is a binding-proposal origin
-------------------------------------------------
Incident F-Z1b (HM-TO-A6 attempt 11): the journey's fixture files live in
``<workspace>/a6-fixture/`` while the Run's task holds only its managed home
``<workspace>/task-<id>/``.  F-Z1 refused those reads with
``path_outside_workspace_root`` and *no way forward*, because the read gate —
unlike the write/effect path — never entered the S4 multi-root binding flow.

Since F-Z1b, a read whose path is outside the task's roots but still a strict
descendant of the configured workspace root computes one **candidate root**
deterministically — the nearest *existing* directory ancestor of the
symlink-resolved path, never the configured workspace root itself, never a
common parent of a root the task already holds — and hands it to exactly the
same authority the ``context_route`` create_new path uses:
``HumanMemoryHostService.append_binding``.  Under ``manual`` that becomes a
durable proposal + challenge (the card; product decision 2026-09-07 keeps
``auto`` prompt-free); under ``auto`` it becomes a ``policy:auto`` grant and a
binding revision.  The proposal is journalled in
``context_route_tool_invocations`` in the same rejection shape the route tool
uses, so ``PrimaryWorkspaceBindings`` projects the card without a new query.

**Same-call vs retry (the pinned rule): never the same call.**  Read authority
is the *exact* binding revision this Run's durable route receipt names, never
the live head (:meth:`WorkspaceReadGate.bound_context`), and the EffectGate is
stricter still — after any append the head no longer equals the receipt
revision, so every later project effect of this Run would reject with
``workspace_binding_receipt_superseded`` until a new ``context_route`` receipt
is issued.  A binding revision therefore *always* ends with the model calling
``context_route`` once more (``continue_active``) and then re-calling the read;
that one extra call repairs the write path in the same motion.  Both F-Z1b
rejections say exactly that.

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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from simple_harness import CallId, RunId
from simple_harness.tools import ToolContext, ToolResult

from deskpet.sdk_adapters.task_scope_mutation import write_pre_admission_audit_tx
from deskpet.sdk_adapters.tool_authority import PROJECT_READ_TOOL_NAMES
from deskpet.task_scope.store import TaskScopeConflict
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
# --- F-Z1b -----------------------------------------------------------------
# The read gate as a binding-proposal origin.  ``READ_BINDING_AUTH_REASON``
# deliberately reuses the route tool's code: ``PrimaryWorkspaceBindings`` and
# the Manual-journey driver both select the pending card by exactly this string
# in ``context_route_tool_invocations.detail_json.$.code``, and a read-triggered
# proposal must reach the same card without a second query.
READ_BINDING_AUTH_REASON = "context_route_binding_authorization_required"
READ_BINDING_REVISED_REASON = "read_workspace_binding_revised"
READ_ROOT_TOO_BROAD_REASON = "workspace_root_too_broad"
# S4 append conflicts that mean "the binding set already moved past this Run's
# route receipt": the remedy is the same one revision always needs — re-route,
# then re-call the read.
_BINDING_AHEAD_CONFLICTS = frozenset(
    {"workspace_binding_root_already_present", "workspace_binding_base_revision_conflict"}
)
# The one durable next step after any binding revision (see the module note on
# same-call vs retry): re-route, then re-call the read.
_REROUTE_STEP = (
    "Call context_route once with route=continue_active (no other argument) to "
    "refresh this Run's binding receipt, then call this read Tool again with "
    "the same path. Until that receipt is refreshed this Run reads and writes "
    "only the roots its current receipt already names."
)
READ_BINDING_AUTH_MESSAGE = (
    "Read rejected for now: the requested path is outside this task's bound "
    "roots but inside the configured workspace, so the Host issued a workspace "
    "binding proposal for the directory that contains it. The owner must "
    "approve it in the 『项目目录授权』 card (allow this binding). Do not retry "
    "in a loop: say what you asked for and wait for the answer. Once it is "
    "allowed: " + _REROUTE_STEP
)
READ_BINDING_REVISED_MESSAGE = (
    "The directory containing the requested path has been bound to this task "
    "(a new workspace binding revision). The read was not executed, because "
    "this Run's route receipt still names the previous binding revision. "
    + _REROUTE_STEP
)
READ_ROOT_TOO_BROAD_MESSAGE = (
    "Read rejected: binding the directory that would be needed for this path "
    "is refused. The configured workspace root itself, any parent of it, and "
    "any directory that would swallow a root this task already holds are never "
    "bindable — a task works in exact project directories, never in the whole "
    "workspace. Name a specific project directory inside the workspace instead."
)

_READ_GATE_MESSAGES: Mapping[str, str] = {
    READ_ROUTE_REASON: READ_ROUTE_MESSAGE,
    READ_ROOT_REASON: READ_ROOT_MESSAGE,
    READ_OUTSIDE_REASON: READ_OUTSIDE_MESSAGE,
    READ_BINDING_AUTH_REASON: READ_BINDING_AUTH_MESSAGE,
    READ_BINDING_REVISED_REASON: READ_BINDING_REVISED_MESSAGE,
    READ_ROOT_TOO_BROAD_REASON: READ_ROOT_TOO_BROAD_MESSAGE,
}
# ``host_pre_admission_audit.payload_kind`` is CHECK-constrained to three
# values; the read gate reuses the route kind and namespaces its rows by the
# reason-code prefix (same pattern as ``memory.analysis.blocked``).
READ_GATE_AUDIT_KIND = "context_route"
READ_GATE_AUDIT_PREFIX = "workspace_read."
READ_GATE_ADMITTED_REASON = "admitted"
# F-Z1b: ``host_pre_admission_audit.reason_code`` is the only column left for
# the proposal reference (the table's CHECK-constrained ``payload_kind`` and the
# 64-hex ``payload_hash`` are both taken, and widening the schema is a migration
# this change deliberately does not take).  The receipt therefore reads
# ``workspace_read.<tool>.<reason>@<binding_proposal_ref>``; the ref is a
# challenge / binding-receipt id, never a path.
READ_GATE_AUDIT_PROPOSAL_SEPARATOR = "@"


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


def containing_root(candidate: str, roots: Sequence[str]) -> str | None:
    """The first bound root that contains ``candidate``, or ``None``.

    F-Z1b: a task may hold several verified roots (S4 multi-root binding), so
    containment is "inside *any* of them" and the answer names *which* one, so
    the dispatch projection can run the read in exactly that root.
    """

    for root in roots:
        if path_within_root(candidate, root):
            return str(root)
    return None


def read_arguments_violation(
    arguments: Mapping[str, Any], root: str | Sequence[str]
) -> bool:
    """Does any path/pattern argument leave the bound root set?  Fail closed."""

    return read_target_violation(arguments, root)[1] is not None


def read_target_violation(
    arguments: Mapping[str, Any], root: str | Sequence[str]
) -> tuple[str | None, tuple[str, str] | None]:
    """``(root to project, violation)`` for one read call's arguments.

    Exactly one side is set.  The violation is ``("path", value)`` — which
    F-Z1b may still route through the binding-proposal authority — or
    ``("pattern", value)``: a ``..`` segment in a glob/grep pattern names no
    directory, so it is never a binding candidate and stays refused.
    """

    roots: tuple[str, ...] = (
        (str(root),) if isinstance(root, str) else tuple(str(item) for item in root)
    )
    # ``path``-less glob/grep default to ``context.workspace``; the projection
    # therefore runs them in the task's primary (first-appended) root.
    projected: str | None = roots[0] if roots else None
    for key in READ_PATH_ARGUMENT_KEYS:
        value = arguments.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            return None, ("path", str(value))
        inside = containing_root(value, roots)
        if inside is None:
            return None, ("path", value)
        projected = inside
    for key in READ_PATTERN_ARGUMENT_KEYS:
        value = arguments.get(key)
        if _pattern_escapes(value):
            return None, ("pattern", str(value))
    return projected, None


def read_binding_candidate_root(
    requested: str,
    *,
    primary_root: str,
    configured_root: str,
    bound_roots: Sequence[str] = (),
) -> tuple[str | None, str | None]:
    """``(candidate root, refusal code)`` for one out-of-root read path (F-Z1b).

    Exactly one side is set.  The rule is deterministic and independent of the
    model's wording — the model never names a root, it names a file:

    1. resolve ``requested`` (relative paths against the task's primary root),
       following symlinks, exactly as :func:`path_within_root` does;
    2. it must be a **strict descendant** of the configured workspace root —
       otherwise ``path_outside_workspace_root`` (a symlink whose target leaves
       the workspace lands here, after resolution, like any other outside path);
       the configured root *itself* is ``workspace_root_too_broad``;
    3. the candidate is the nearest **existing directory** ancestor of the
       resolved path (the path itself when it is already a directory).  If that
       walk reaches the configured root, the only bindable answer would be the
       workspace itself → ``workspace_root_too_broad``;
    4. the candidate may never be a common parent of — or equal to — a root the
       task already holds → ``workspace_root_too_broad``.

    Everything else is left to the S4 store, which re-derives the canonical
    root and re-applies its own refusals over the real filesystem.
    """

    resolved_configured = _resolved(Path(configured_root))
    resolved_primary = _resolved(Path(primary_root))
    if resolved_configured is None or resolved_primary is None:
        return None, READ_OUTSIDE_REASON
    raw = Path(requested).expanduser()
    if not raw.is_absolute():
        raw = resolved_primary / raw
    resolved = _resolved(raw)
    if resolved is None:
        return None, READ_OUTSIDE_REASON
    if resolved == resolved_configured:
        return None, READ_ROOT_TOO_BROAD_REASON
    if resolved_configured not in resolved.parents:
        return None, READ_OUTSIDE_REASON
    candidate: Path | None = None
    node = resolved
    while node != resolved_configured:
        try:
            if node.is_dir():
                candidate = node
                break
        except OSError:
            return None, READ_OUTSIDE_REASON
        node = node.parent
    if candidate is None:
        # Nothing between the requested path and the workspace root exists as a
        # directory: the only bindable ancestor would be the workspace itself.
        return None, READ_ROOT_TOO_BROAD_REASON
    for held in bound_roots:
        resolved_held = _resolved(Path(held))
        if resolved_held is None:
            continue
        if candidate == resolved_held or candidate in resolved_held.parents:
            return None, READ_ROOT_TOO_BROAD_REASON
    return str(candidate), None


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

    async def record_tool_invocation(self, **kwargs: Any) -> None: ...


@dataclass(frozen=True)
class BoundReadContext:
    """One Run's durable read authority: the exact revision its route names."""

    task_scope_id: str
    binding_set_revision: int
    roots: tuple[str, ...]

    @property
    def primary_root(self) -> str:
        return self.roots[0]


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
        service_factory_getter: Callable[[], Any] | None = None,
        binding_append_getter: Callable[[], Any] | None = None,
        auth_factory: Callable[[], Any] | None = None,
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
        # F-Z1b: the binding-proposal authority.  Optional by construction —
        # without it the gate keeps exactly F-Z1's behaviour (an out-of-root
        # path is ``path_outside_workspace_root``, full stop), which is what a
        # composition that has no Host service (tests of the containment rule
        # alone) must keep getting.
        self._service_factory_getter = service_factory_getter
        self._binding_append_getter = binding_append_getter
        if auth_factory is None:
            from deskpet.sdk_adapters.context_route import local_owner_auth

            auth_factory = local_owner_auth
        self._auth_factory = auth_factory
        # ``(run_id, call_id) -> verified root`` handed from ``verify`` to the
        # dispatch scope of that same call.  Bounded by construction: one entry
        # per admitted call, popped by ``execution_scope``.
        self._admitted: dict[tuple[str, str], str] = {}

    # ---------------------------------------------------------------- root

    async def bound_root(self, run_id: str) -> tuple[str | None, str | None]:
        """``(the task's primary verified root, reason code)``.

        Kept as the narrow F-Z1 accessor.  With F-Z1b's multi-root binding the
        primary root is the one this task bound *first* — revision 1's root, the
        task's managed home (see :meth:`_ordered_root_hashes`).
        """

        bound, code = await self.bound_context(run_id)
        return (None if bound is None else bound.primary_root), code

    async def _ordered_root_hashes(self, task_scope_id: str, receipt: Any) -> tuple[str, ...]:
        """The receipt's roots in **append order** (revision 1 first).

        ``WorkspaceBindingSetReceipt.root_identity_hashes`` is
        ``tuple(sorted(...))`` — a set digest, ordered by hash, not by history.
        Reading ``roots[0]`` off it made :attr:`BoundReadContext.primary_root`
        depend on which SHA happened to sort first, so after F-Z1b's second
        append the "primary" root could flip to the *proposed* directory: a
        relative read path and the F-Z1b candidate-root computation would then
        resolve against it instead of the task's managed home (it also made
        ``test_multi_root_reads_run_in_the_root_that_contains_the_path`` pass or
        fail with the tmp directory's name).  ``task_workspace_binding_roots``
        keeps the durable append order in ``first_binding_set_revision``; that is
        the order used here.  Ordering is presentation, never authority — every
        root is still verified one by one against the exact revision the route
        receipt names, so an unreadable order falls back to the receipt's own.
        """

        hashes = tuple(str(value) for value in getattr(receipt, "root_identity_hashes", ()))
        if len(hashes) < 2:
            return hashes
        order: dict[str, int] = {}
        try:
            async with self._scope_store._connection() as db:  # noqa: SLF001
                cursor = await db.execute(
                    "SELECT root_identity_hash,first_binding_set_revision FROM "
                    "task_workspace_binding_roots WHERE task_scope_id=?",
                    (task_scope_id,),
                )
                for row in await cursor.fetchall():
                    order[str(row[0])] = int(row[1])
        except Exception as exc:  # noqa: BLE001 - order is not an authority
            logger.warning(
                "workspace_read_root_order_unavailable scope=%s error=%s",
                task_scope_id,
                exc,
            )
            return hashes
        return tuple(sorted(hashes, key=lambda value: (order.get(value, 1 << 31), value)))

    async def bound_context(
        self, run_id: str
    ) -> tuple[BoundReadContext | None, str | None]:
        """``(read authority, reason code)`` for one Run's durable task route.

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
        for root_hash in await self._ordered_root_hashes(scope_id, receipt):
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
        # F-Z1b: a task may legitimately hold several verified roots (S4
        # multi-root binding, HM-S9), and F-Z1's "exactly one root or nothing"
        # rule would have turned this gate's own successful proposal into
        # ``read_workspace_root_unavailable``.  Reads are per-path, so several
        # roots carry read authority perfectly well — containment simply has to
        # name *which* root admitted the path.  Zero roots still carries none.
        if not roots:
            return None, READ_ROOT_REASON
        return (
            BoundReadContext(
                task_scope_id=scope_id,
                binding_set_revision=int(receipt.binding_set_revision),
                roots=tuple(roots),
            ),
            None,
        )

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

        bound, code = await self.bound_context(run_id.value)
        projected: str | None = None
        proposal_ref: str | None = None
        if code is None and bound is not None:
            projected, violation = read_target_violation(payload, bound.roots)
            if violation is not None:
                kind, value = violation
                if kind == "pattern":
                    # A ``..`` glob/grep pattern names no directory, so it can
                    # never become a binding candidate: refused as in F-Z1.
                    code = READ_OUTSIDE_REASON
                else:
                    code, proposal_ref = await self._propose_binding(
                        context, reject_call_id, tool_name, bound, value
                    )
        await self._audit(
            run_id.value,
            code or READ_GATE_ADMITTED_REASON,
            tool_name,
            payload,
            binding_proposal_ref=proposal_ref,
        )
        if code is None:
            self._admitted[(run_id.value, reject_call_id.value)] = str(projected)
            return None
        logger.warning(
            "workspace_read_denied run=%s tool=%s reason=%s binding_proposal=%s",
            run_id.value,
            tool_name,
            code,
            proposal_ref or "-",
        )
        return ToolResult.rejected(
            reject_call_id, code, read_gate_public_message(code)
        )

    # ----------------------------------------------------- F-Z1b proposal

    async def _propose_binding(
        self,
        context: ToolContext,
        call_id: CallId,
        tool_name: str,
        bound: BoundReadContext,
        requested: str,
    ) -> tuple[str, str | None]:
        """Route one out-of-root read path through the S4 binding authority.

        Returns ``(reason code, binding_proposal_ref)``.  Never raises and never
        admits: a binding revision always ends with the model re-routing and
        re-calling the read (module note "same-call vs retry").
        """

        if self._service_factory_getter is None or self._binding_append_getter is None:
            return READ_OUTSIDE_REASON, None
        try:
            configured = self._binding_store.configured_root().canonical_path
        except Exception as exc:  # noqa: BLE001 - a refusal never fails on detail
            logger.warning("workspace_read_configured_root_unavailable error=%s", exc)
            return READ_OUTSIDE_REASON, None
        candidate, refusal = read_binding_candidate_root(
            requested,
            primary_root=bound.primary_root,
            configured_root=str(configured),
            bound_roots=bound.roots,
        )
        if candidate is None:
            return str(refusal), None

        binding_append = self._binding_append_getter()
        factory = self._service_factory_getter()
        if binding_append is None or factory is None:
            return READ_OUTSIDE_REASON, None
        effect_id = (
            context.effect_id.value
            if context.effect_id is not None
            else f"read-gate:{call_id.value}"
        )
        # The UI card projection (``PrimaryWorkspaceBindings._item``) pins the
        # proposal's idempotency key to exactly this shape and joins the pending
        # challenge through a ``context_route_tool_invocations`` row keyed by the
        # same effect id.  A read-triggered proposal reuses both, so the same
        # 『项目目录授权』 card appears with no new query and no new schema.
        idempotency_key = f"context-route:{context.run_id.value}:{effect_id}"
        from deskpet.memory.human_memory_service import AppendBindingRequest

        service = factory.bind(self._auth_factory(), binding_append=binding_append)
        try:
            outcome = dict(
                await service.append_binding(
                    AppendBindingRequest(
                        scope_ref=bound.task_scope_id,
                        root=str(candidate),
                        idempotency_key=idempotency_key,
                    )
                )
            )
        except WorkspaceBindingError as exc:
            # The S4 code set passes through unchanged (too broad, not a
            # configured descendant, symlink/not-a-directory, identity drift).
            return str(exc.code), None
        except TaskScopeConflict as exc:
            # The candidate is already a root of this task, or the binding head
            # moved since this call read it.  Both mean "the binding set is
            # ahead of this Run's route receipt", which is exactly the
            # re-route-then-retry verdict — never a fresh proposal loop.  (A
            # model that keeps re-calling the read without re-routing lands
            # here on every attempt and keeps getting the same next step.)
            if str(exc) in _BINDING_AHEAD_CONFLICTS:
                return READ_BINDING_REVISED_REASON, None
            return str(getattr(exc, "code", "") or "") or READ_OUTSIDE_REASON, None
        except Exception as exc:  # noqa: BLE001 - stable fail-closed surface
            code = str(getattr(exc, "code", "") or "")
            logger.warning(
                "workspace_read_binding_proposal_failed run=%s tool=%s error=%s",
                context.run_id.value,
                tool_name,
                exc,
            )
            return code or READ_OUTSIDE_REASON, None

        if str(outcome.get("status", "")) == "authorization_required":
            challenge_ref = str(outcome.get("challenge_ref") or "")
            await self._record_binding_invocation(
                run_id=context.run_id.value,
                raw_call_id=call_id.value,
                effect_id=effect_id,
                tool_name=tool_name,
                task_scope_id=bound.task_scope_id,
                challenge=outcome,
            )
            return READ_BINDING_AUTH_REASON, challenge_ref or None
        return READ_BINDING_REVISED_REASON, str(
            outcome.get("binding_set_receipt_ref") or outcome.get("receipt_ref") or ""
        ) or None

    async def _record_binding_invocation(
        self,
        *,
        run_id: str,
        raw_call_id: str,
        effect_id: str,
        tool_name: str,
        task_scope_id: str,
        challenge: Mapping[str, Any],
    ) -> None:
        """Journal the pending challenge so the owner's card can find it."""

        recorder = getattr(self._route_ledger, "record_tool_invocation", None)
        if not callable(recorder):
            return
        try:
            await recorder(
                sdk_run_id=run_id,
                raw_call_id=raw_call_id,
                effect_id=effect_id,
                proposal={"tool": tool_name, "origin": "workspace_read_gate"},
                verdict="rejected",
                decision_id=None,
                detail={
                    "code": READ_BINDING_AUTH_REASON,
                    "task_scope_id": task_scope_id,
                    "binding_challenge": dict(challenge),
                    "required_action": "binding.manual.decide",
                    "origin": "workspace_read_gate",
                },
            )
        except Exception as exc:  # noqa: BLE001 - the card is evidence, not authority
            logger.warning(
                "workspace_read_binding_invocation_unavailable run=%s error=%s",
                run_id,
                exc,
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
        binding_proposal_ref: str | None = None,
    ) -> None:
        """One ``host_pre_admission_audit`` row per gate decision.

        Reason code + Tool name + a canonical hash of the arguments, plus (F-Z1b)
        the binding proposal this decision produced.  No path, no pattern, no
        file content ever reaches the audit trail.
        """

        from deskpet.tools.capabilities import canonical_hash

        suffix = (
            ""
            if not binding_proposal_ref
            else f"{READ_GATE_AUDIT_PROPOSAL_SEPARATOR}{binding_proposal_ref}"
        )

        try:
            async with self._scope_store._connection() as db:  # noqa: SLF001
                await db.execute("BEGIN IMMEDIATE")
                try:
                    await write_pre_admission_audit_tx(
                        db,
                        sdk_run_id=run_id,
                        payload_kind=READ_GATE_AUDIT_KIND,
                        reason_code=(
                            f"{READ_GATE_AUDIT_PREFIX}{tool_name}.{reason_code}{suffix}"
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


def parse_read_audit_reason(reason_code: str) -> tuple[str, str, str | None]:
    """``(tool, reason, binding_proposal_ref)`` of one read-gate receipt."""

    body = str(reason_code)
    if body.startswith(READ_GATE_AUDIT_PREFIX):
        body = body[len(READ_GATE_AUDIT_PREFIX) :]
    proposal_ref: str | None = None
    if READ_GATE_AUDIT_PROPOSAL_SEPARATOR in body:
        body, _, ref = body.partition(READ_GATE_AUDIT_PROPOSAL_SEPARATOR)
        proposal_ref = ref or None
    tool, _, reason = body.partition(".")
    return tool, reason, proposal_ref


__all__ = [
    "PROJECT_READ_TOOL_NAMES",
    "READ_BINDING_AUTH_REASON",
    "READ_BINDING_REVISED_REASON",
    "READ_GATE_AUDIT_PROPOSAL_SEPARATOR",
    "READ_ROOT_TOO_BROAD_REASON",
    "BoundReadContext",
    "containing_root",
    "parse_read_audit_reason",
    "read_binding_candidate_root",
    "read_target_violation",
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
