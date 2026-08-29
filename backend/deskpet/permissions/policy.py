# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Manual/auto authorization policy and the versioned decision matrix."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

from deskpet.types.task_grants import (
    DerivedAuthorizationGrant,
    ExactGrantRequest,
    TaskGrant,
    TaskGrantScopeError,
    derive_exact_grant,
)

AuthorizationMode = Literal["manual", "auto"]
AuthorizationPolicyProvenance = Literal[
    "factory_default",
    "factory_default_migrated",
    "legacy_import",
    "user_explicit",
    "needs_user_choice",
]
StorageKind = Literal[
    "permission",
    "plan",
    "clarification",
    "ppt_outline",
    "skill_candidate",
    "workflow_hitl",
]
DomainKind = Literal["admission", "react", "workflow", "capability", "external"]
AutoAction = Literal["allow", "deny", "require_user_content", "external_wait"]
PolicyAction = Literal[
    "allow",
    "deny",
    "wait",
    "require_user_content",
    "external_wait",
    "task_grant_required",
    "scope_expansion_required",
]


class AuthorizationPolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AuthorizationPolicyState:
    mode: AuthorizationMode
    generation: int
    updated_at: float
    provenance: AuthorizationPolicyProvenance = "needs_user_choice"
    schema_generation: int = 2
    user_set_receipt_ref: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"manual", "auto"}:
            raise AuthorizationPolicyError(
                "invalid_policy_mode", f"unknown authorization mode: {self.mode}"
            )
        if not isinstance(self.generation, int) or self.generation < 0:
            raise AuthorizationPolicyError(
                "invalid_policy_generation", "policy generation must be non-negative"
            )
        if not math.isfinite(float(self.updated_at)):
            raise AuthorizationPolicyError(
                "invalid_policy_timestamp", "updated_at must be finite"
            )
        if self.provenance not in {
            "factory_default", "factory_default_migrated", "legacy_import",
            "user_explicit", "needs_user_choice",
        }:
            raise AuthorizationPolicyError(
                "invalid_policy_provenance", "unknown authorization provenance"
            )
        if self.schema_generation != 2:
            raise AuthorizationPolicyError(
                "invalid_policy_schema_generation", "policy schema generation must be 2"
            )
        if self.provenance == "user_explicit" and not self.user_set_receipt_ref:
            raise AuthorizationPolicyError(
                "missing_user_policy_receipt", "explicit policy requires a receipt"
            )

    def transition(
        self,
        mode: AuthorizationMode,
        *,
        expected_generation: int,
        now: float,
        provenance: AuthorizationPolicyProvenance = "user_explicit",
        user_set_receipt_ref: str | None = None,
    ) -> "AuthorizationPolicyState":
        """Return the CAS successor.

        Every explicit user transition advances the generation.  Factory and
        legacy classification do not mint a user generation; provenance keeps
        those migrations distinguishable without reviving an old Auto grant.
        """

        if expected_generation != self.generation:
            raise AuthorizationPolicyError(
                "policy_generation_conflict",
                "authorization policy generation changed",
            )
        if mode not in {"manual", "auto"}:
            raise AuthorizationPolicyError(
                "invalid_policy_mode", f"unknown authorization mode: {mode}"
            )
        receipt = user_set_receipt_ref
        if provenance == "user_explicit" and not receipt:
            receipt = f"policy-user-set:{expected_generation + 1}"
        return AuthorizationPolicyState(
            mode=mode,
            generation=self.generation + (
                provenance == "user_explicit"
                and (mode != self.mode or self.provenance != "user_explicit")
            ),
            updated_at=now,
            provenance=provenance,
            schema_generation=2,
            user_set_receipt_ref=receipt if provenance == "user_explicit" else None,
        )


@dataclass(frozen=True, slots=True)
class DecisionAutomationRule:
    schema_version: int
    storage_kind: StorageKind
    domain_kind: DomainKind
    domain_subkind: str
    auto_action: AutoAction
    required_task_grant: bool
    response_template: Mapping[str, object] | None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise AuthorizationPolicyError(
                "unsupported_rule_schema", "decision rule schema must be 1"
            )
        if not self.domain_subkind.strip():
            raise AuthorizationPolicyError(
                "invalid_domain_subkind", "domain_subkind is required"
            )
        if self.auto_action not in {
            "allow",
            "deny",
            "require_user_content",
            "external_wait",
        }:
            raise AuthorizationPolicyError(
                "invalid_auto_action", f"unknown auto action: {self.auto_action}"
            )
        if self.response_template is not None:
            object.__setattr__(
                self,
                "response_template",
                MappingProxyType(dict(self.response_template)),
            )

    @property
    def key(self) -> tuple[int, str, str, str]:
        return (
            self.schema_version,
            self.storage_kind,
            self.domain_kind,
            self.domain_subkind,
        )


def _rule(
    storage_kind: StorageKind,
    domain_kind: DomainKind,
    domain_subkind: str,
    auto_action: AutoAction,
    *,
    required_task_grant: bool,
) -> DecisionAutomationRule:
    response = (
        MappingProxyType({"action": "allow", "actor": "policy:auto"})
        if auto_action == "allow"
        else None
    )
    return DecisionAutomationRule(
        schema_version=1,
        storage_kind=storage_kind,
        domain_kind=domain_kind,
        domain_subkind=domain_subkind,
        auto_action=auto_action,
        required_task_grant=required_task_grant,
        response_template=response,
    )


DECISION_AUTOMATION_RULES: tuple[DecisionAutomationRule, ...] = (
    _rule("plan", "admission", "root_task", "allow", required_task_grant=True),
    _rule(
        "permission",
        "react",
        "prepared_tool",
        "allow",
        required_task_grant=True,
    ),
    _rule(
        "permission",
        "capability",
        "install_or_update",
        "allow",
        required_task_grant=True,
    ),
    _rule(
        "permission",
        "capability",
        "scope_expansion",
        "allow",
        required_task_grant=True,
    ),
    _rule(
        "workflow_hitl",
        "workflow",
        "plan_approval",
        "allow",
        required_task_grant=True,
    ),
    _rule(
        "skill_candidate",
        "capability",
        "install_authorization",
        "allow",
        required_task_grant=True,
    ),
    _rule(
        "clarification",
        "react",
        "user_content",
        "require_user_content",
        required_task_grant=False,
    ),
    _rule(
        "ppt_outline",
        "workflow",
        "content_review",
        "require_user_content",
        required_task_grant=False,
    ),
    _rule(
        "workflow_hitl",
        "workflow",
        "user_choice",
        "require_user_content",
        required_task_grant=False,
    ),
    _rule(
        "workflow_hitl",
        "external",
        "login_or_secret",
        "external_wait",
        required_task_grant=False,
    ),
    _rule(
        "workflow_hitl",
        "external",
        "payment_or_otp",
        "external_wait",
        required_task_grant=False,
    ),
    _rule(
        "workflow_hitl",
        "external",
        "windows_uac",
        "external_wait",
        required_task_grant=False,
    ),
)
_RULES_BY_KEY = MappingProxyType({rule.key: rule for rule in DECISION_AUTOMATION_RULES})


@dataclass(frozen=True, slots=True)
class DecisionContext:
    schema_version: int
    storage_kind: StorageKind
    domain_kind: DomainKind
    domain_subkind: str
    root_run_id: str

    @property
    def key(self) -> tuple[int, str, str, str]:
        return (
            self.schema_version,
            self.storage_kind,
            self.domain_kind,
            self.domain_subkind,
        )


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    action: PolicyAction
    reason: str
    rule: DecisionAutomationRule | None
    response: Mapping[str, object] | None = None
    exact_grant: DerivedAuthorizationGrant | None = None

    @property
    def projects_waiting_ui(self) -> bool:
        return self.action in {
            "wait",
            "require_user_content",
            "external_wait",
            "task_grant_required",
            "scope_expansion_required",
        }


class AuthorizationPolicy:
    """Pure policy engine; persistence and atomic signaling stay in the UoW."""

    def __init__(
        self,
        rules: tuple[DecisionAutomationRule, ...] = DECISION_AUTOMATION_RULES,
    ) -> None:
        by_key = {rule.key: rule for rule in rules}
        if len(by_key) != len(rules):
            raise AuthorizationPolicyError(
                "duplicate_rule", "decision automation rule keys must be unique"
            )
        self._rules = MappingProxyType(by_key)

    @property
    def rules(self) -> tuple[DecisionAutomationRule, ...]:
        return tuple(self._rules.values())

    def rule_for(self, context: DecisionContext) -> DecisionAutomationRule | None:
        return self._rules.get(context.key)

    def evaluate(
        self,
        *,
        state: AuthorizationPolicyState,
        context: DecisionContext,
        task_grant: TaskGrant | None,
        exact_request: ExactGrantRequest | None,
        actual_root_run_id: str,
        now: float,
    ) -> PolicyDecision:
        """Evaluate one already-persisted decision candidate.

        Unknown rule keys fail closed to a real wait.  This method never
        resolves a decision or signals a driver itself; the runtime must do
        those writes in the fenced UoW sequence.
        """

        rule = self.rule_for(context)
        if rule is None:
            return PolicyDecision(
                action="wait",
                reason="unlisted decision combination fails closed",
                rule=None,
            )

        if rule.auto_action == "require_user_content":
            return PolicyDecision(
                action="require_user_content",
                reason="decision requires real user content",
                rule=rule,
            )
        if rule.auto_action == "external_wait":
            return PolicyDecision(
                action="external_wait",
                reason="decision requires an external user or system action",
                rule=rule,
            )
        if rule.auto_action == "deny":
            return PolicyDecision(action="deny", reason="policy rule denies", rule=rule)

        if state.mode == "manual":
            # A confirmed root TaskGrant suppresses repeated prepared-tool
            # popups only for calls fully inside that grant.  Admission and
            # capability expansion still wait for the user's confirmation.
            if (
                context.storage_kind != "permission"
                or context.domain_kind != "react"
                or context.domain_subkind != "prepared_tool"
            ):
                return PolicyDecision(
                    action="wait", reason="manual mode requires confirmation", rule=rule
                )

        if rule.required_task_grant and task_grant is None:
            return PolicyDecision(
                action="task_grant_required",
                reason="decision requires a task-level resource grant",
                rule=rule,
            )

        exact_grant: DerivedAuthorizationGrant | None = None
        if exact_request is not None:
            if task_grant is None:
                return PolicyDecision(
                    action="task_grant_required",
                    reason="exact authorization requires a TaskGrant",
                    rule=rule,
                )
            try:
                exact_grant = derive_exact_grant(
                    task_grant,
                    exact_request,
                    actual_root_run_id=actual_root_run_id,
                    policy_mode=state.mode,
                    policy_generation=state.generation,
                    now=now,
                )
            except TaskGrantScopeError as exc:
                return PolicyDecision(
                    action="scope_expansion_required",
                    reason=str(exc),
                    rule=rule,
                )
        elif (
            context.storage_kind == "permission"
            and context.domain_kind == "react"
            and context.domain_subkind == "prepared_tool"
        ):
            return PolicyDecision(
                action="scope_expansion_required",
                reason="prepared tool decision is missing exact resource bindings",
                rule=rule,
            )

        return PolicyDecision(
            action="allow",
            reason=(
                "auto policy allows without waiting"
                if state.mode == "auto"
                else "confirmed TaskGrant covers the prepared call"
            ),
            rule=rule,
            response=rule.response_template,
            exact_grant=exact_grant,
        )


__all__ = [
    "AuthorizationMode",
    "AuthorizationPolicy",
    "AuthorizationPolicyError",
    "AuthorizationPolicyProvenance",
    "AuthorizationPolicyState",
    "AutoAction",
    "DECISION_AUTOMATION_RULES",
    "DecisionAutomationRule",
    "DecisionContext",
    "PolicyAction",
    "PolicyDecision",
]
