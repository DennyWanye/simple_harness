from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from deskpet.permissions.policy import AuthorizationPolicyState
from deskpet.permissions.runtime import PreparedAuthorizationRuntime
from deskpet.permissions.task_grants import ResourceSelector, TaskGrant
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.effects import PreparedToolCall


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _Store:
    def __init__(self, state: AuthorizationPolicyState) -> None:
        self.state = state
        self.grants: dict[str, TaskGrant] = {}

    async def get_policy_state(self) -> AuthorizationPolicyState:
        return self.state

    async def get_task_grant(self, task_grant_id: str) -> TaskGrant | None:
        return self.grants.get(task_grant_id)


def _context(workspace: Path, *, call_id: str = "call-1") -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id=_hash("scope"),
        session_id="session-1",
        request_id="request-1",
        root_run_id="root-1",
        turn_id="turn-1",
        workspace=str(workspace),
        write_scope_root=str(workspace),
        capability_hash=_hash("capability"),
        scope_hash=_hash("scope"),
        provider_plan=("provider-1",),
        run_id="run-1",
        call_id=call_id,
        effect_id=_hash(f"effect:{call_id}"),
        trace_id="trace-1",
    )


def _call(
    path: Path,
    *,
    call_id: str = "call-1",
    selectors: tuple[ResourceSelector, ...] | None = None,
) -> PreparedToolCall:
    return PreparedToolCall.prepare(
        tool_name="write_file",
        stable_call_id=call_id,
        final_params={"path": str(path), "content": "ok"},
        tool_spec_version="v1",
        schema_hash=_hash("schema"),
        permission_policy_version="v1",
        effect_type="staged_file",
        resource_selectors=(
            selectors
            if selectors is not None
            else (ResourceSelector.filesystem(path, "write"),)
        ),
    )


@pytest.mark.asyncio
async def test_auto_proposes_policy_grant_and_exact_call_without_ui(
    tmp_path: Path,
) -> None:
    store = _Store(AuthorizationPolicyState("auto", 4, 100.0))
    runtime = PreparedAuthorizationRuntime(store, clock=lambda: 100.0)
    context = _context(tmp_path)

    plan = await runtime.plan_prepared_call(
        call=_call(tmp_path / "one.txt"),
        context=context,
        permission_category="write_file",
        task_grant_id=None,
        principal_id="user-1",
        decision_expires_at=250.0,
    )

    assert plan.action == "allow"
    assert plan.resolves_without_ui
    assert plan.proposed_task_grant is not None
    assert plan.proposed_task_grant.source == "policy:auto"
    assert plan.proposed_task_grant.policy_generation == 4
    # Task-level scope is the trusted workspace, while the exact request stays
    # bound to the concrete file.
    assert plan.proposed_task_grant.resource_selectors[0].canonical_value == (
        ResourceSelector.filesystem(tmp_path, "write").canonical_value
    )
    assert plan.exact_request is not None
    assert plan.exact_request.resource_selectors[0].canonical_value == (
        ResourceSelector.filesystem(tmp_path / "one.txt", "write").canonical_value
    )
    assert plan.policy_decision is not None
    assert plan.policy_decision.exact_grant is not None
    commit = plan.to_commit()
    assert commit.proposed_task_grant is True
    assert commit.expected_policy_mode == "auto"
    assert commit.exact_request == plan.exact_request


@pytest.mark.asyncio
async def test_manual_confirm_once_covers_later_call_inside_workspace(
    tmp_path: Path,
) -> None:
    store = _Store(AuthorizationPolicyState("manual", 2, 100.0))
    runtime = PreparedAuthorizationRuntime(store, clock=lambda: 100.0)
    first_context = _context(tmp_path)

    waiting = await runtime.plan_prepared_call(
        call=_call(tmp_path / "one.txt"),
        context=first_context,
        permission_category="write_file",
        task_grant_id=None,
        principal_id="user-1",
    )
    assert waiting.action == "task_grant_required"
    assert waiting.projects_waiting_ui

    confirmed = await runtime.plan_prepared_call(
        call=_call(tmp_path / "one.txt"),
        context=first_context,
        permission_category="write_file",
        task_grant_id=None,
        principal_id="user-1",
        confirmed=True,
    )
    assert confirmed.action == "allow"
    assert confirmed.proposed_task_grant is not None
    store.grants[
        confirmed.proposed_task_grant.task_grant_id
    ] = confirmed.proposed_task_grant

    second_context = _context(tmp_path, call_id="call-2")
    second = await runtime.plan_prepared_call(
        call=_call(tmp_path / "two.txt", call_id="call-2"),
        context=second_context,
        permission_category="write_file",
        task_grant_id=confirmed.proposed_task_grant.task_grant_id,
        principal_id="user-1",
    )
    assert second.action == "allow"
    assert second.proposed_task_grant is None
    assert second.current_task_grant == confirmed.proposed_task_grant


@pytest.mark.asyncio
async def test_manual_out_of_scope_waits_and_auto_expands_with_new_version(
    tmp_path: Path,
) -> None:
    store = _Store(AuthorizationPolicyState("manual", 3, 100.0))
    runtime = PreparedAuthorizationRuntime(store, clock=lambda: 100.0)
    original = TaskGrant(
        task_grant_id="grant-original",
        root_run_id="root-1",
        principal_id="user-1",
        resource_selectors=(
            ResourceSelector.filesystem(tmp_path / "inside", "write"),
        ),
        permission_categories=("write_file",),
        effect_kinds=("staged_file",),
        source="user",
        policy_generation=0,
        expires_at=500.0,
        version=1,
    )
    store.grants[original.task_grant_id] = original
    outside = tmp_path.parent / "outside" / "result.txt"
    context = _context(tmp_path)

    manual = await runtime.plan_prepared_call(
        call=_call(outside),
        context=context,
        permission_category="write_file",
        task_grant_id=original.task_grant_id,
        principal_id="user-1",
    )
    assert manual.action == "scope_expansion_required"
    assert manual.proposed_task_grant is None

    store.state = AuthorizationPolicyState("auto", 4, 101.0)
    automatic = await runtime.plan_prepared_call(
        call=_call(outside),
        context=context,
        permission_category="write_file",
        task_grant_id=original.task_grant_id,
        principal_id="user-1",
    )
    assert automatic.action == "allow"
    assert automatic.proposed_task_grant is not None
    assert automatic.proposed_task_grant.task_grant_id != original.task_grant_id
    assert automatic.proposed_task_grant.source == "policy:auto"


@pytest.mark.asyncio
async def test_auto_grant_is_stale_immediately_after_switch_to_manual(
    tmp_path: Path,
) -> None:
    store = _Store(AuthorizationPolicyState("auto", 8, 100.0))
    runtime = PreparedAuthorizationRuntime(store, clock=lambda: 100.0)
    context = _context(tmp_path)
    initial = await runtime.plan_prepared_call(
        call=_call(tmp_path / "one.txt"),
        context=context,
        permission_category="write_file",
        task_grant_id=None,
        principal_id="user-1",
    )
    assert initial.proposed_task_grant is not None
    store.grants[
        initial.proposed_task_grant.task_grant_id
    ] = initial.proposed_task_grant

    store.state = AuthorizationPolicyState("manual", 9, 101.0)
    stale = await runtime.plan_prepared_call(
        call=_call(tmp_path / "two.txt", call_id="call-2"),
        context=_context(tmp_path, call_id="call-2"),
        permission_category="write_file",
        task_grant_id=initial.proposed_task_grant.task_grant_id,
        principal_id="user-1",
    )
    assert stale.action == "scope_expansion_required"
    assert stale.proposed_task_grant is None


@pytest.mark.asyncio
async def test_missing_resource_resolver_fails_closed_even_in_auto(
    tmp_path: Path,
) -> None:
    store = _Store(AuthorizationPolicyState("auto", 1, 100.0))
    runtime = PreparedAuthorizationRuntime(store, clock=lambda: 100.0)

    plan = await runtime.plan_prepared_call(
        call=_call(tmp_path / "one.txt", selectors=()),
        context=_context(tmp_path),
        permission_category="write_file",
        task_grant_id=None,
        principal_id="user-1",
    )

    assert plan.action == "scope_expansion_required"
    assert plan.projects_waiting_ui
    assert plan.exact_request is None


@pytest.mark.asyncio
async def test_confirm_only_never_uses_auto_or_historical_grant(
    tmp_path: Path,
) -> None:
    store = _Store(AuthorizationPolicyState("auto", 5, 100.0))
    runtime = PreparedAuthorizationRuntime(store, clock=lambda: 100.0)
    context = _context(tmp_path)
    call = _call(tmp_path / "external.txt")
    fences = {
        "explicit_only": True,
        "decision_id": "decision-external",
        "decision_nonce": "nonce-external",
        "confirm_only_snapshot_ref": "tool-set:run-1",
        "confirm_only_snapshot_hash": _hash("confirm-only-set"),
    }

    waiting = await runtime.plan_prepared_call(
        call=call,
        context=context,
        permission_category="external_action",
        task_grant_id=None,
        principal_id="user-1",
        **fences,
    )

    assert waiting.action == "wait"
    assert waiting.authorization_origin == "explicit_decision"
    assert waiting.proposed_task_grant is None

    confirmed = await runtime.plan_prepared_call(
        call=call,
        context=context,
        permission_category="external_action",
        task_grant_id=None,
        principal_id="user-1",
        confirmed=True,
        **fences,
    )

    assert confirmed.action == "allow"
    assert confirmed.proposed_task_grant is not None
    assert confirmed.proposed_task_grant.source == "user"
    commit = confirmed.to_commit()
    assert commit.authorization_origin == "explicit_decision"
    assert commit.decision_id == "decision-external"
    assert commit.confirm_only_snapshot_hash == _hash("confirm-only-set")

    store.grants[confirmed.proposed_task_grant.task_grant_id] = (
        confirmed.proposed_task_grant
    )
    replay_without_confirmation = await runtime.plan_prepared_call(
        call=call,
        context=context,
        permission_category="external_action",
        task_grant_id=confirmed.proposed_task_grant.task_grant_id,
        principal_id="user-1",
        **fences,
    )
    assert replay_without_confirmation.action == "wait"
    assert replay_without_confirmation.proposed_task_grant is None


@pytest.mark.asyncio
async def test_confirm_only_open_pass_does_not_require_future_decision_id(
    tmp_path: Path,
) -> None:
    store = _Store(AuthorizationPolicyState("manual", 2, 100.0))
    runtime = PreparedAuthorizationRuntime(store, clock=lambda: 100.0)
    waiting = await runtime.plan_prepared_call(
        call=_call(tmp_path / "confirm.txt"),
        context=_context(tmp_path),
        permission_category="write_file",
        task_grant_id=None,
        principal_id="user-1",
        explicit_only=True,
        decision_nonce="nonce-open",
        confirm_only_snapshot_ref="tool-set:run-1",
        confirm_only_snapshot_hash=_hash("confirm-only-open"),
    )

    assert waiting.action == "wait"
    assert waiting.projects_waiting_ui
    assert waiting.decision_id is None

    missing_confirmed_fence = await runtime.plan_prepared_call(
        call=_call(tmp_path / "confirm.txt"),
        context=_context(tmp_path),
        permission_category="write_file",
        task_grant_id=None,
        principal_id="user-1",
        confirmed=True,
        explicit_only=True,
        decision_nonce="nonce-open",
        confirm_only_snapshot_ref="tool-set:run-1",
        confirm_only_snapshot_hash=_hash("confirm-only-open"),
    )

    assert missing_confirmed_fence.action == "deny"
    assert "missing its frozen decision fences" in missing_confirmed_fence.reason
