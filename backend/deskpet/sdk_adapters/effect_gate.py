# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Per-effect workspace EffectGate (S5b Task 1, design-freeze §4 steps 1/3/4/5/6).

``ProductEffectExecutor.execute`` consults :class:`EffectGate` before every
physical Tool dispatch.  Only PROJECT_EFFECT Tools (policy read from the frozen
run catalog record) are gated; every other Tool passes through untouched.

The gate is a *binary* verdict: ``None`` (admit) or ``ToolResult.rejected``
with one stable reason code — never an exception, never a partial effect.  A
rejection leaves no ``execution_effects`` row and no Host event; the model sees
the reason code as an ordinary TOOL message and may re-route.

Check order (frozen; the first failing step names the reason):

0. exact replay is never re-verified (Task 1 review F-1): if the SDK effect
   ledger already holds a record for this ``effect_id`` (any state — settled,
   HANDED_OFF, UNKNOWN, PREPARED), ``ProductEffectExecutor.execute`` skips the
   gate and hands the call to the SDK replay / reconcile path.  The admission
   decision was made durably on first occurrence; a later change of the gate
   conditions (binding append, scope close, root drift) must not turn an
   already-physical effect into a fabricated terminal ``rejected``.
1. envelope present and echoing this exact effect
   (``effect_gate_envelope_missing`` / ``effect_gate_envelope_identity_mismatch``)
2. *(Task 6)* sticky memo ``effect_gate_route_receipt_rejected``
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
   equal the receipt revision (Manual/Auto alike, 裁决 A6)
6. ``effect_gate_task_scope_not_active`` — TaskScope status ∉ {active, open}
7. *(Task 6)* confirm-only / grant

Frozen-authority note: ``ProductForegroundToolPort.freeze`` registers an exact
single root as ``workspace_root`` = canonical path with resolution kind
``legacy`` (the Session-only ``project_bound`` validator is bypassed on
purpose), and zero/multi root as ``projectless`` with no root.  "Project-bound"
for this gate therefore means *an exact frozen root exists*; ``projectless`` /
``missing`` / no root is rejected.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, Protocol

from simple_harness import CallId, RunId
from simple_harness.tools import ToolContext, ToolResult
from simple_harness.tools.runtime_catalog import ToolEffectClass

from deskpet.execution.evidence_ingress import ObjectiveEventSpec
from deskpet.task_scope.store import TaskScopeNotFound
from deskpet.task_scope.workspace_bindings import WorkspaceBindingError

EFFECT_GATE_PUBLIC_MESSAGE = (
    "Project effect was rejected by the workspace effect gate; "
    "observe a fresh Context route before retrying."
)
PROJECT_EFFECT_ACTIVE_SCOPE_STATUSES: frozenset[str] = frozenset({"active", "open"})


class RouteReceiptReader(Protocol):
    async def read_route_receipt(self, sdk_run_id: str, receipt_id: str) -> Any: ...


class ScopeStatusReader(Protocol):
    async def read_head_status(self, task_scope_id: str) -> str | None: ...


class EffectGate:
    """Binary per-effect admission for PROJECT_EFFECT Tools."""

    def __init__(
        self,
        *,
        binding_store: Any,
        route_ledger: RouteReceiptReader,
        scope_store: ScopeStatusReader,
        authority_resolver: Callable[[RunId], Any],
        exposure_resolver: Callable[[RunId], Any],
    ) -> None:
        self._binding_store = binding_store
        self._route_ledger = route_ledger
        self._scope_store = scope_store
        self._authority_resolver = authority_resolver
        self._exposure_resolver = exposure_resolver

    async def verify(
        self,
        context: ToolContext,
        tool_name: str,
        *,
        call_id: CallId | None = None,
    ) -> ToolResult | None:
        run_id = context.run_id
        exposure = self._exposure_resolver(run_id)
        # RuntimeToolCatalogError (hidden Tool) propagates: that is a whole-Run
        # fault (catalog_execution_policy_unavailable), not a rejection.
        policy = exposure.execution_policy(run_id, tool_name)
        if policy.effect_class is not ToolEffectClass.PROJECT_EFFECT:
            return None
        reject_call_id = call_id if call_id is not None else context.call_id
        if reject_call_id is None:
            raise RuntimeError("effect_gate_call_identity_missing")

        def reject(code: str) -> ToolResult:
            return ToolResult.rejected(reject_call_id, code, EFFECT_GATE_PUBLIC_MESSAGE)

        # 1. envelope present + exact identity echo
        envelope = context.task_execution_envelope
        if envelope is None:
            return reject("effect_gate_envelope_missing")
        if (
            envelope.run_id != run_id
            or envelope.call_id != reject_call_id
            or (context.effect_id is not None and envelope.effect_id != context.effect_id)
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

        # 3. frozen Run-admission authority (scope, project-bound)
        authority = self._authority_resolver(run_id)
        work = authority.task_work_context
        resolution = dict(getattr(authority, "workspace_resolution", None) or {})
        if envelope.task_scope_id != work.task_scope_id:
            return reject("effect_gate_frozen_scope_mismatch")
        frozen_root = work.workspace_root
        kind = str(resolution.get("kind") or "")
        if kind in {"projectless", "missing"} or not frozen_root:
            return reject("effect_gate_projectless_project_effect")

        # 4. S4 lineage + membership + filesystem identity (codes pass through)
        route_receipt = await self._route_ledger.read_route_receipt(
            run_id.value, str(envelope.route_receipt_id)
        )
        if route_receipt is None:
            return reject("workspace_binding_route_authority_missing")
        try:
            verified = await self._binding_store.verify_task_execution_envelope(
                envelope, route_receipt
            )
        except WorkspaceBindingError as exc:
            return reject(exc.code)

        # 3 (cont.). verified root must be the frozen write root
        if verified.root.canonical_path != frozen_root:
            return reject("effect_gate_frozen_root_mismatch")

        # 5. strict head == receipt revision
        try:
            head = await self._binding_store.current_receipt(str(envelope.task_scope_id))
        except TaskScopeNotFound:
            return reject("workspace_binding_receipt_superseded")
        if (
            head.binding_set_revision != envelope.binding_set_revision
            or head.receipt_id != envelope.binding_set_receipt_id
            or head.receipt_hash != envelope.binding_set_receipt_hash
        ):
            return reject("workspace_binding_receipt_superseded")

        # 6. TaskScope lifecycle
        status = await self._scope_store.read_head_status(str(envelope.task_scope_id))
        if status not in PROJECT_EFFECT_ACTIVE_SCOPE_STATUSES:
            return reject("effect_gate_task_scope_not_active")
        return None


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


def _test_runner_head(tokens: list[str]) -> str | None:
    for head in TEST_RUNNER_COMMANDS:
        if tuple(tokens[: len(head)]) == head:
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
