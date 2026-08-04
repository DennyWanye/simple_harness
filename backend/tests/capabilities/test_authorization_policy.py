from __future__ import annotations

import hashlib

from deskpet.permissions.policy import (
    AuthorizationPolicy,
    AuthorizationPolicyState,
    DECISION_AUTOMATION_RULES,
    DecisionContext,
)
from deskpet.permissions.task_grants import (
    ExactGrantRequest,
    ResourceSelector,
    TaskGrant,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _grant_and_request(tmp_path, *, source: str, generation: int):
    selector = ResourceSelector.filesystem(tmp_path / "workspace", "write")
    grant = TaskGrant(
        task_grant_id="grant-1",
        root_run_id="root-1",
        principal_id="user-1",
        resource_selectors=(selector,),
        permission_categories=("filesystem_write",),
        effect_kinds=("staged_file",),
        source=source,  # type: ignore[arg-type]
        policy_generation=generation,
        expires_at=500.0,
        version=1,
    )
    request = ExactGrantRequest(
        root_run_id="root-1",
        run_id="run-1",
        call_id="call-1",
        effect_id="effect-1",
        tool_name="write_file",
        args_hash=_hash("args"),
        capability_hash=_hash("capability"),
        schema_hash=_hash("schema"),
        scope_hash=_hash("scope"),
        resource_selectors=(
            ResourceSelector.filesystem(tmp_path / "workspace" / "out.txt", "write"),
        ),
        permission_categories=("filesystem_write",),
        effect_kinds=("staged_file",),
        expires_at=200.0,
    )
    return grant, request


def test_rule_matrix_has_unique_complete_keys() -> None:
    keys = [rule.key for rule in DECISION_AUTOMATION_RULES]
    assert len(keys) == len(set(keys))
    assert len(keys) == 12


def test_auto_prepared_tool_issues_exact_grant_without_wait(tmp_path) -> None:
    grant, request = _grant_and_request(
        tmp_path, source="policy:auto", generation=4
    )
    decision = AuthorizationPolicy().evaluate(
        state=AuthorizationPolicyState("auto", 4, 100.0),
        context=DecisionContext(
            1, "permission", "react", "prepared_tool", "root-1"
        ),
        task_grant=grant,
        exact_request=request,
        actual_root_run_id="root-1",
        now=100.0,
    )
    assert decision.action == "allow"
    assert not decision.projects_waiting_ui
    assert decision.exact_grant is not None
    assert decision.exact_grant.source == "policy:auto"


def test_manual_confirmed_task_grant_suppresses_repeat_popup(tmp_path) -> None:
    grant, request = _grant_and_request(tmp_path, source="user", generation=0)
    decision = AuthorizationPolicy().evaluate(
        state=AuthorizationPolicyState("manual", 8, 100.0),
        context=DecisionContext(
            1, "permission", "react", "prepared_tool", "root-1"
        ),
        task_grant=grant,
        exact_request=request,
        actual_root_run_id="root-1",
        now=100.0,
    )
    assert decision.action == "allow"
    assert decision.exact_grant is not None


def test_user_content_uac_and_unknown_rules_never_auto_allow() -> None:
    policy = AuthorizationPolicy()
    state = AuthorizationPolicyState("auto", 1, 100.0)
    content = policy.evaluate(
        state=state,
        context=DecisionContext(
            1, "clarification", "react", "user_content", "root-1"
        ),
        task_grant=None,
        exact_request=None,
        actual_root_run_id="root-1",
        now=100.0,
    )
    uac = policy.evaluate(
        state=state,
        context=DecisionContext(
            1, "workflow_hitl", "external", "windows_uac", "root-1"
        ),
        task_grant=None,
        exact_request=None,
        actual_root_run_id="root-1",
        now=100.0,
    )
    unknown = policy.evaluate(
        state=state,
        context=DecisionContext(
            1, "workflow_hitl", "workflow", "mystery", "root-1"
        ),
        task_grant=None,
        exact_request=None,
        actual_root_run_id="root-1",
        now=100.0,
    )
    assert content.action == "require_user_content"
    assert uac.action == "external_wait"
    assert unknown.action == "wait"
    assert all(item.projects_waiting_ui for item in (content, uac, unknown))


def test_policy_transition_increments_generation_in_both_directions() -> None:
    initial = AuthorizationPolicyState("auto", 4, 100.0)
    manual = initial.transition("manual", expected_generation=4, now=101.0)
    auto = manual.transition("auto", expected_generation=5, now=102.0)
    assert (manual.generation, auto.generation) == (5, 6)
