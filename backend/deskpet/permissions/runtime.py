# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""TaskGrant planning for prepared tool decisions.

This module decides *what* must be committed.  The execution UoW remains the
transaction owner that persists a proposed TaskGrant, resolves the durable
decision, and issues the exact one-shot grant together.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.effects import PreparedToolCall

from .policy import (
    AuthorizationPolicy,
    AuthorizationPolicyState,
    DecisionContext,
    PolicyDecision,
)
from deskpet.types.task_grants import (
    ExactGrantRequest,
    PreparedAuthorizationCommit,
    ResourceSelector,
    TaskGrant,
)


class AuthorizationStore(Protocol):
    async def get_policy_state(self) -> AuthorizationPolicyState: ...

    async def get_task_grant(self, task_grant_id: str) -> TaskGrant | None: ...


PlanAction = Literal[
    "allow",
    "wait",
    "deny",
    "require_user_content",
    "external_wait",
    "task_grant_required",
    "scope_expansion_required",
]


@dataclass(frozen=True, slots=True)
class PreparedAuthorizationPlan:
    action: PlanAction
    reason: str
    policy_state: AuthorizationPolicyState
    exact_request: ExactGrantRequest | None
    current_task_grant: TaskGrant | None
    proposed_task_grant: TaskGrant | None
    policy_decision: PolicyDecision | None
    authorization_origin: Literal["policy", "explicit_decision"] = "policy"
    decision_id: str | None = None
    decision_nonce: str | None = None
    confirm_only_snapshot_ref: str | None = None
    confirm_only_snapshot_hash: str | None = None

    @property
    def resolves_without_ui(self) -> bool:
        return self.action == "allow"

    @property
    def projects_waiting_ui(self) -> bool:
        return not self.resolves_without_ui

    @property
    def committed_task_grant(self) -> TaskGrant | None:
        return self.proposed_task_grant or self.current_task_grant

    def to_commit(self) -> PreparedAuthorizationCommit:
        grant = self.committed_task_grant
        if self.action != "allow" or self.exact_request is None or grant is None:
            raise ValueError("only an allowed prepared authorization can be committed")
        return PreparedAuthorizationCommit(
            task_grant=grant,
            exact_request=self.exact_request,
            expected_policy_mode=self.policy_state.mode,
            expected_policy_generation=self.policy_state.generation,
            proposed_task_grant=self.proposed_task_grant is not None,
            authorization_origin=self.authorization_origin,
            decision_id=self.decision_id,
            decision_nonce=self.decision_nonce,
            confirm_only_snapshot_ref=self.confirm_only_snapshot_ref,
            confirm_only_snapshot_hash=self.confirm_only_snapshot_hash,
        )


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _principal(context: ToolExecutionContext) -> str:
    # ToolExecutionContext intentionally carries no principal.  The run's
    # authenticated principal is injected separately by ReAct when available;
    # this local identity is only the backward-compatible fallback.
    return f"local:{context.session_id}"


def _within(candidate: str, root: str) -> bool:
    try:
        Path(candidate).relative_to(Path(root))
        return True
    except ValueError:
        return Path(candidate) == Path(root)


def _merge_selectors(
    selectors: tuple[ResourceSelector, ...],
) -> tuple[ResourceSelector, ...]:
    merged: dict[tuple[str, str], set[str]] = {}
    for selector in selectors:
        key = (selector.kind, selector.canonical_value)
        merged.setdefault(key, set()).update(selector.access)
    return tuple(
        ResourceSelector(kind, value, tuple(sorted(access)))
        for (kind, value), access in sorted(merged.items())
    )


def _task_scope_selectors(
    requested: tuple[ResourceSelector, ...],
    context: ToolExecutionContext,
) -> tuple[ResourceSelector, ...]:
    """Broaden only to a trusted host workspace, never to an arbitrary root."""

    trusted_root = context.write_scope_root or context.workspace
    root_selector: ResourceSelector | None = None
    if trusted_root:
        root_selector = ResourceSelector.filesystem(trusted_root, "read")
    result: list[ResourceSelector] = []
    for selector in requested:
        if (
            selector.kind == "filesystem"
            and root_selector is not None
            and _within(selector.canonical_value, root_selector.canonical_value)
        ):
            result.append(
                ResourceSelector.filesystem(
                    root_selector.canonical_value,
                    *selector.access,
                )
            )
        else:
            result.append(selector)
    return _merge_selectors(tuple(result))


class PreparedAuthorizationRuntime:
    """Evaluate prepared calls against persisted policy and TaskGrant state."""

    def __init__(
        self,
        store: AuthorizationStore,
        *,
        policy: AuthorizationPolicy | None = None,
        clock: Callable[[], float] = time.time,
        task_grant_ttl_seconds: float = 8 * 60 * 60,
        exact_grant_ttl_seconds: float = 5 * 60,
    ) -> None:
        self._store = store
        self._policy = policy or AuthorizationPolicy()
        self._clock = clock
        self._task_grant_ttl = max(60.0, float(task_grant_ttl_seconds))
        self._exact_grant_ttl = max(1.0, float(exact_grant_ttl_seconds))

    @staticmethod
    def _task_grant_id(
        *,
        root_run_id: str,
        principal_id: str,
        source: str,
        policy_generation: int,
        parent_task_grant_id: str | None,
        version: int,
        resources: tuple[ResourceSelector, ...],
        permission_categories: tuple[str, ...],
        effect_kinds: tuple[str, ...],
        grant_instance_id: str | None = None,
    ) -> str:
        fingerprint = _canonical_hash(
            {
                "root_run_id": root_run_id,
                "principal_id": principal_id,
                "source": source,
                "policy_generation": policy_generation,
                "parent_task_grant_id": parent_task_grant_id,
                "version": version,
                "resources": [item.to_dict() for item in resources],
                "permission_categories": list(permission_categories),
                "effect_kinds": list(effect_kinds),
                "grant_instance_id": grant_instance_id,
            }
        )
        return f"task-grant:{fingerprint}"

    def _new_task_grant(
        self,
        *,
        state: AuthorizationPolicyState,
        source: Literal["user", "policy:auto"],
        principal_id: str,
        root_run_id: str,
        resources: tuple[ResourceSelector, ...],
        permission_category: str,
        effect_kind: str,
        now: float,
        grant_instance_id: str | None = None,
    ) -> TaskGrant:
        scoped = _merge_selectors(resources)
        task_grant_id = self._task_grant_id(
            root_run_id=root_run_id,
            principal_id=principal_id,
            source=source,
            policy_generation=state.generation,
            parent_task_grant_id=None,
            version=1,
            resources=scoped,
            permission_categories=(permission_category,),
            effect_kinds=(effect_kind,),
            grant_instance_id=grant_instance_id,
        )
        return TaskGrant(
            task_grant_id=task_grant_id,
            root_run_id=root_run_id,
            principal_id=principal_id,
            resource_selectors=scoped,
            permission_categories=(permission_category,),
            effect_kinds=(effect_kind,),
            source=source,
            policy_generation=state.generation,
            expires_at=now + self._task_grant_ttl,
            version=1,
        )

    def _expanded_task_grant(
        self,
        current: TaskGrant,
        *,
        state: AuthorizationPolicyState,
        source: Literal["user", "policy:auto"],
        principal_id: str,
        root_run_id: str,
        resources: tuple[ResourceSelector, ...],
        permission_category: str,
        effect_kind: str,
        now: float,
        grant_instance_id: str | None = None,
    ) -> TaskGrant:
        if current.source != source:
            seed_resources = _merge_selectors(
                (*current.resource_selectors, *resources)
            )
            return self._new_task_grant(
                state=state,
                source=source,
                principal_id=principal_id,
                root_run_id=root_run_id,
                resources=seed_resources,
                permission_category=permission_category,
                effect_kind=effect_kind,
                now=now,
                grant_instance_id=grant_instance_id,
            )
        resources = _merge_selectors(
            (*current.resource_selectors, *resources)
        )
        permissions = tuple(
            sorted({*current.permission_categories, permission_category})
        )
        effects = tuple(sorted({*current.effect_kinds, effect_kind}))
        next_version = current.version + 1
        next_id = self._task_grant_id(
            root_run_id=root_run_id,
            principal_id=principal_id,
            source=source,
            policy_generation=state.generation,
            parent_task_grant_id=current.task_grant_id,
            version=next_version,
            resources=resources,
            permission_categories=permissions,
            effect_kinds=effects,
            grant_instance_id=grant_instance_id,
        )
        return TaskGrant(
            task_grant_id=next_id,
            root_run_id=root_run_id,
            principal_id=principal_id,
            resource_selectors=resources,
            permission_categories=permissions,
            effect_kinds=effects,
            source=source,
            policy_generation=state.generation,
            expires_at=now + self._task_grant_ttl,
            version=next_version,
        )

    def build_exact_request(
        self,
        *,
        call: PreparedToolCall,
        context: ToolExecutionContext,
        permission_category: str,
        decision_expires_at: float | None = None,
        now: float | None = None,
    ) -> ExactGrantRequest:
        current_time = float(self._clock()) if now is None else float(now)
        if not call.resource_selectors:
            raise ValueError(
                "prepared tool has no deterministic resource selectors"
            )
        if not context.root_run_id or not context.run_id:
            raise ValueError("prepared tool is missing its trusted run identity")
        exact_expiry = min(
            decision_expires_at
            if decision_expires_at is not None
            else current_time + self._exact_grant_ttl,
            current_time + self._exact_grant_ttl,
        )
        return ExactGrantRequest(
            root_run_id=context.root_run_id,
            run_id=context.run_id,
            call_id=call.stable_call_id,
            effect_id=context.effect_id,
            tool_name=call.tool_name,
            args_hash=call.args_hash,
            capability_hash=context.capability_hash,
            schema_hash=call.schema_hash,
            scope_hash=context.scope_hash,
            resource_selectors=call.resource_selectors,
            permission_categories=(permission_category,),
            effect_kinds=(call.effect_type,),
            expires_at=exact_expiry,
        )

    async def plan_prepared_call(
        self,
        *,
        call: PreparedToolCall,
        context: ToolExecutionContext,
        permission_category: str,
        task_grant_id: str | None,
        principal_id: str | None = None,
        confirmed: bool = False,
        decision_expires_at: float | None = None,
        explicit_only: bool = False,
        decision_id: str | None = None,
        decision_nonce: str | None = None,
        confirm_only_snapshot_ref: str | None = None,
        confirm_only_snapshot_hash: str | None = None,
    ) -> PreparedAuthorizationPlan:
        state = await self._store.get_policy_state()
        now = float(self._clock())
        principal = principal_id or _principal(context)
        explicit_fences = (
            decision_id,
            decision_nonce,
            confirm_only_snapshot_ref,
            confirm_only_snapshot_hash,
        )
        if explicit_only and any(
            not isinstance(value, str) or not value.strip()
            for value in explicit_fences
        ):
            return PreparedAuthorizationPlan(
                action="deny",
                reason="confirm-only authorization is missing its frozen decision fences",
                policy_state=state,
                exact_request=None,
                current_task_grant=None,
                proposed_task_grant=None,
                policy_decision=None,
                authorization_origin="explicit_decision",
                decision_id=decision_id,
                decision_nonce=decision_nonce,
                confirm_only_snapshot_ref=confirm_only_snapshot_ref,
                confirm_only_snapshot_hash=confirm_only_snapshot_hash,
            )
        if not call.resource_selectors:
            return PreparedAuthorizationPlan(
                action="scope_expansion_required",
                reason="prepared tool has no deterministic resource selectors",
                policy_state=state,
                exact_request=None,
                current_task_grant=None,
                proposed_task_grant=None,
                policy_decision=None,
            )
        if not context.root_run_id or not context.run_id:
            return PreparedAuthorizationPlan(
                action="wait",
                reason="prepared tool is missing its trusted run identity",
                policy_state=state,
                exact_request=None,
                current_task_grant=None,
                proposed_task_grant=None,
                policy_decision=None,
            )
        exact = self.build_exact_request(
            call=call,
            context=context,
            permission_category=permission_category,
            decision_expires_at=decision_expires_at,
            now=now,
        )
        current = (
            await self._store.get_task_grant(task_grant_id)
            if task_grant_id
            else None
        )
        if current is not None and (
            current.root_run_id != context.root_run_id
            or current.principal_id != principal
        ):
            current = None

        task_scope = _task_scope_selectors(call.resource_selectors, context)
        proposal: TaskGrant | None = None
        candidate = current
        covers = bool(
            not explicit_only
            and
            current
            and current.is_current(
                now=now,
                policy_mode=state.mode,
                policy_generation=state.generation,
            )
            and current.covers(
                root_run_id=context.root_run_id,
                resources=call.resource_selectors,
                permission_categories=(permission_category,),
                effect_kinds=(call.effect_type,),
            )
        )
        if explicit_only and not confirmed:
            return PreparedAuthorizationPlan(
                action="wait",
                reason="confirm-only tool requires this exact user decision",
                policy_state=state,
                exact_request=exact,
                current_task_grant=current,
                proposed_task_grant=None,
                policy_decision=None,
                authorization_origin="explicit_decision",
                decision_id=decision_id,
                decision_nonce=decision_nonce,
                confirm_only_snapshot_ref=confirm_only_snapshot_ref,
                confirm_only_snapshot_hash=confirm_only_snapshot_hash,
            )
        if not covers and (state.mode == "auto" or confirmed):
            source: Literal["user", "policy:auto"] = (
                "user"
                if explicit_only
                else "policy:auto"
                if state.mode == "auto"
                else "user"
            )
            if current is None:
                proposal = self._new_task_grant(
                    state=state,
                    source=source,
                    principal_id=principal,
                    root_run_id=context.root_run_id,
                    resources=task_scope,
                    permission_category=permission_category,
                    effect_kind=call.effect_type,
                    now=now,
                    grant_instance_id=(
                        decision_id
                        if confirmed and state.mode != "auto"
                        else None
                    ),
                )
            else:
                proposal = self._expanded_task_grant(
                    current,
                    state=state,
                    source=source,
                    principal_id=principal,
                    root_run_id=context.root_run_id,
                    resources=task_scope,
                    permission_category=permission_category,
                    effect_kind=call.effect_type,
                    now=now,
                    grant_instance_id=(
                        decision_id
                        if confirmed and state.mode != "auto"
                        else None
                    ),
                )
            candidate = proposal

        decision = self._policy.evaluate(
            state=state,
            context=DecisionContext(
                1,
                "permission",
                "react",
                "prepared_tool",
                context.root_run_id,
            ),
            task_grant=candidate,
            exact_request=exact,
            actual_root_run_id=context.root_run_id,
            now=now,
        )
        return PreparedAuthorizationPlan(
            action=decision.action,
            reason=decision.reason,
            policy_state=state,
            exact_request=exact,
            current_task_grant=current,
            proposed_task_grant=proposal,
            policy_decision=decision,
            authorization_origin=(
                "explicit_decision" if explicit_only else "policy"
            ),
            decision_id=decision_id if explicit_only else None,
            decision_nonce=decision_nonce if explicit_only else None,
            confirm_only_snapshot_ref=(
                confirm_only_snapshot_ref if explicit_only else None
            ),
            confirm_only_snapshot_hash=(
                confirm_only_snapshot_hash if explicit_only else None
            ),
        )


__all__ = [
    "AuthorizationStore",
    "PreparedAuthorizationPlan",
    "PreparedAuthorizationRuntime",
]
