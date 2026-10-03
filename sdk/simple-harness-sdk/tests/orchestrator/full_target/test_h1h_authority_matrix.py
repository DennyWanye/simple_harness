"""H1-H §8 A02-A04: planning authority at the real collector and Plan Commit.

Every case runs on the product's deployment: the main loop has proposed a method, had
it independently reviewed and opened the adoption round, whose authority the
deployment's duty issued (``h1i_seed.reviewed``).  The planner's reply is delivered
through the production collector.  Faults are external events only: damaged or
unreadable authority rows (adjudication ①b1), the person revoking or renewing the
grant, or the grant running out, while the reply is in the collector (between preview
and commit); a delivery presenting another principal or scope is sent ahead of the
real one at the commit entry and must be refused (①a).
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest
from h1i_seed import events as _events
from h1i_seed import plan_reply, refuse_tampered_first, reviewed

from agent_orchestrator.contracts.planning_decisions import PlanningDecisionStatus
from agent_orchestrator.governance.planning_authorization import (
    PlanningLanePolicy,
    SourceUnavailable,
    StorePlanningAuthorityReader,
    build_planning_authorization,
)
from agent_orchestrator.orchestrator.plan_commits import PlanPrincipal
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.planning_admission_store import PlanningAdmissionStore
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.testing.fixtures import lift_immutable_guards


def _caller(binding: dict[str, Any]) -> PlanPrincipal:
    """The bound planner principal, as the collector presents it."""

    return PlanPrincipal(str(binding["planner_principal_id"]), str(binding["scope_id"]))


def _binding(loop: Any, intent: Any) -> dict[str, Any]:
    binding = PlanningAdmissionStore(loop.store).get_request_binding(intent.intent_id)
    assert binding is not None
    return binding


@pytest.mark.parametrize(
    ("table", "column", "where_column"),
    (
        ("planning_request_authority_bindings", "binding_json", "request_id"),
        ("planning_lane_grants", "allowed_decisions_json", "grant_id"),
        ("planning_lane_grants", "grant_json", "grant_id"),
    ),
)
def test_a02_malformed_authority_json_is_typed_source_unavailable_without_writes(
    tmp_path, table: str, column: str, where_column: str
) -> None:
    async def case() -> None:
        async with reviewed(tmp_path, key=f"h1h-a02-malformed-{column}") as ((loop, mission, *_rest), intent, _provider):
            binding = _binding(loop, intent)
            identity = intent.intent_id if where_column == "request_id" else binding["grant_id"]
            # The table/column names come only from the closed parametrization above.
            lift_immutable_guards(loop.store.connection, table)
            loop.store.connection.execute(
                f"UPDATE {table} SET {column} = ? WHERE {where_column} = ?",  # noqa: S608
                ("{not-json", identity),
            )
            before = loop.store.connection.total_changes
            authority = build_planning_authorization(
                intent.intent_id,
                read=StorePlanningAuthorityReader(PlanningAdmissionStore(loop.store), loop.store),
                caller=_caller(binding),
                policy=PlanningLanePolicy(),
                now_ms=int(loop.store.now * 1000),
            )
            assert isinstance(authority, SourceUnavailable)
            assert authority.reason_code == "SOURCE_UNAVAILABLE"
            assert authority.source == "planning_authority"
            assert loop.store.connection.total_changes == before

    asyncio.run(case())


@pytest.mark.parametrize(
    ("fault", "expected_code"),
    (
        ("missing_binding", "AUTHORIZATION_REQUIRED"),
        ("dangling_grant", "INTERNAL_CONTRACT_ERROR"),
        ("malformed_binding_json", "INTERNAL_CONTRACT_ERROR"),
        ("malformed_allowed_decisions_json", "INTERNAL_CONTRACT_ERROR"),
        ("malformed_grant_json", "INTERNAL_CONTRACT_ERROR"),
        ("unreadable_sqlite", "INTERNAL_CONTRACT_ERROR"),
    ),
)
def test_a02_real_collector_records_authority_failures_as_closed_rejections(
    tmp_path, fault: str, expected_code: str
) -> None:
    """Decoded replies use the closed rejection vocabulary without format retry.

    Producer diagnostics remain in detail: missing authority is the existing
    AUTHORIZATION_REQUIRED code; corrupt or unreadable authority uses the existing
    INTERNAL_CONTRACT_ERROR code with SOURCE_UNAVAILABLE detail.  None is a codec
    failure, so the durable status must be REJECTED rather than UNREADABLE.  Nothing
    reaches the plan or the action ledger (was also the commit-level A02 matrix).
    """

    async def case() -> None:
        async with reviewed(tmp_path, key=f"h1h-a02-collector-{fault}") as ((loop, mission, _world, _root, dispatch, _product), opener, _provider):
            binding = _binding(loop, opener)
            renamed = False
            if fault == "missing_binding":
                lift_immutable_guards(loop.store.connection, "planning_request_authority_bindings")
                loop.store.connection.execute(
                    "DELETE FROM planning_request_authority_bindings WHERE request_id = ?",
                    (opener.intent_id,),
                )
            elif fault == "dangling_grant":
                loop.store.connection.execute("PRAGMA foreign_keys = OFF")
                try:
                    lift_immutable_guards(loop.store.connection, "planning_lane_grants")
                    loop.store.connection.execute(
                        "DELETE FROM planning_lane_grants WHERE grant_id = ?",
                        (binding["grant_id"],),
                    )
                finally:
                    loop.store.connection.execute("PRAGMA foreign_keys = ON")
            elif fault == "malformed_binding_json":
                lift_immutable_guards(loop.store.connection, "planning_request_authority_bindings")
                loop.store.connection.execute(
                    "UPDATE planning_request_authority_bindings SET binding_json = ? "
                    "WHERE request_id = ?",
                    ("{not-json", opener.intent_id),
                )
            elif fault.startswith("malformed_"):
                column = fault.removeprefix("malformed_")
                lift_immutable_guards(loop.store.connection, "planning_lane_grants")
                loop.store.connection.execute(
                    f"UPDATE planning_lane_grants SET {column} = ? WHERE grant_id = ?",  # noqa: S608
                    ("{not-json", binding["grant_id"]),
                )
            elif fault == "unreadable_sqlite":
                loop.store.connection.execute(
                    "ALTER TABLE planning_request_authority_bindings "
                    "RENAME TO unavailable_planning_request_authority_bindings"
                )
                renamed = True

            before_revisions = len(HtnStore(loop.store).list_plan_revisions(mission.id))
            before_actions = len(loop.store.list_actions(mission.id))
            try:
                await loop._collect_plan_decision(
                    opener,
                    object(),
                    mission,
                    plan_reply(opener.config["planning_package"]),
                    dispatch,
                )
            finally:
                if renamed:
                    loop.store.connection.execute(
                        "ALTER TABLE unavailable_planning_request_authority_bindings "
                        "RENAME TO planning_request_authority_bindings"
                    )

            evaluated = _events(loop, mission.id, "PlanningDecisionEvaluated")[-1]
            stored = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(
                opener.intent_id, 0
            )
            assert stored is not None
            assert evaluated.payload["status"] == str(PlanningDecisionStatus.REJECTED)
            assert evaluated.payload["rejection_codes"] == [expected_code]
            assert evaluated.payload["canonical_hash"] == stored["canonical_hash"]
            assert evaluated.payload["canonical_hash"] is not None
            if fault != "missing_binding":
                assert "SOURCE_UNAVAILABLE" in str(evaluated.payload["detail"])
            assert len(HtnStore(loop.store).list_plan_revisions(mission.id)) == before_revisions == 0
            assert len(loop.store.list_actions(mission.id)) == before_actions
            assert not _events(loop, mission.id, "PlanRevisionCommitted")
            assert loop.store.connection.execute(
                "SELECT count(*) FROM planning_requests WHERE request_id = ?",
                (opener.intent_id,),
            ).fetchone()[0] == 1

    asyncio.run(case())


def test_real_collector_success_event_uses_the_stored_canonical_hash(tmp_path) -> None:
    async def case() -> None:
        async with reviewed(tmp_path, key="h1h-canonical-success") as ((loop, mission, _world, _root, dispatch, _product), opener, _provider):
            await loop._collect_plan_decision(
                opener,
                object(),
                mission,
                plan_reply(opener.config["planning_package"]),
                dispatch,
            )

            evaluated = _events(loop, mission.id, "PlanningDecisionEvaluated")[-1]
            stored = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(
                opener.intent_id, 0
            )
            assert stored is not None
            assert evaluated.payload["status"] == str(PlanningDecisionStatus.COMMITTED)
            assert evaluated.payload["canonical_hash"] == stored["canonical_hash"]
            assert evaluated.payload["canonical_hash"] is not None

    asyncio.run(case())


def test_real_collector_codec_failure_has_no_canonical_hash(tmp_path) -> None:
    async def case() -> None:
        async with reviewed(tmp_path, key="h1h-canonical-unreadable") as ((loop, mission, _world, _root, dispatch, _product), opener, _provider):
            await loop._collect_plan_decision(
                opener,
                object(),
                mission,
                "<planning_decision>{not-json}</planning_decision>",
                dispatch,
            )

            evaluated = _events(loop, mission.id, "PlanningDecisionEvaluated")[-1]
            stored = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(
                opener.intent_id, 0
            )
            assert stored is not None
            assert evaluated.payload["status"] == str(PlanningDecisionStatus.UNREADABLE)
            assert evaluated.payload["canonical_hash"] is None
            assert stored["canonical_hash"] is None

    asyncio.run(case())


@pytest.mark.parametrize(
    ("principal_changes", "reason"),
    (
        ({"principal_id": "other-tenant-planner"}, "PRINCIPAL_MISMATCH"),
        ({"scope_id": "other-mission"}, "SCOPE_NOT_AUTHORIZED"),
    ),
)
def test_a03_wrong_bound_principal_or_scope_is_refused_without_leak_or_writes(
    tmp_path, monkeypatch: pytest.MonkeyPatch, principal_changes: dict[str, str], reason: str
) -> None:
    """The commit entry, delivered the real command by another principal or into another
    scope, refuses by name, writes nothing and names no grant; the real delivery then
    commits."""

    refusals = refuse_tampered_first(
        monkeypatch,
        lambda command, principal, kwargs: (
            command, dataclasses.replace(principal, **principal_changes), kwargs
        ),
        reason,
    )

    async def case() -> None:
        async with reviewed(tmp_path, key=f"h1h-a03-{reason.lower()}") as ((loop, mission, _world, _root, dispatch, _product), opener, _provider):
            grant_id = _binding(loop, opener)["grant_id"]
            await loop._collect_plan_decision(
                opener,
                object(),
                mission,
                plan_reply(opener.config["planning_package"]),
                dispatch,
            )
            assert len(refusals) == 1
            assert grant_id not in refusals[0]
            assert _events(loop, mission.id, "PlanningDecisionEvaluated")[-1].payload[
                "status"
            ] == str(PlanningDecisionStatus.COMMITTED)
            assert len(HtnStore(loop.store).list_plan_revisions(mission.id)) == 1

    asyncio.run(case())


@pytest.mark.parametrize("change", ("revoke", "renew", "expire"))
def test_a04_reply_bound_before_grant_change_is_stale_and_never_rebound(
    tmp_path, change: str
) -> None:
    """The person revokes or renews the grant, or it runs out, while the reply is between
    preview and commit: the commit rechecks authority inside its transaction, refuses,
    and the reply is never rebound to the new grant (was also commit_guard A05 / I06)."""

    async def case() -> None:
        async with reviewed(tmp_path, key=f"h1h-a04-{change}") as ((loop, mission, _world, _root, dispatch, product), opener, _provider):
            binding = _binding(loop, opener)
            grant = PlanningAdmissionStore(loop.store).get_grant(
                binding["grant_id"], binding["grant_revision"]
            )
            assert grant is not None
            original = dispatch.preview_plan_proposal

            def preview_then_change(proposal: Any, *, inputs: Any) -> Any:
                result = original(proposal, inputs=inputs)
                if change == "expire":
                    loop.store._clock = lambda: (int(grant["expires_at_ms"]) + 1) / 1000
                else:
                    command = {"operation": change, "grant_id": binding["grant_id"],
                               "expected_revision": int(binding["grant_revision"]),
                               "command_id": f"person-{change}-{opener.intent_id}"}
                    if change == "revoke":
                        command["reason"] = "the person withdrew planning authority"
                    product.control.planning_authorization(command)
                return result

            dispatch.preview_plan_proposal = preview_then_change  # type: ignore[method-assign]
            try:
                await loop._collect_plan_decision(
                    opener,
                    object(),
                    mission,
                    plan_reply(opener.config["planning_package"]),
                    dispatch,
                )
            finally:
                dispatch.preview_plan_proposal = original  # type: ignore[method-assign]

            stored = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(
                opener.intent_id, 0
            )
            assert stored is not None
            assert stored["status"] == str(PlanningDecisionStatus.COMMIT_REJECTED), stored
            expected = "AUTHORIZATION_REQUIRED" if change == "expire" else "REQUEST_BINDING_STALE"
            assert stored["rejection_codes"] == [expected], stored
            assert [item["reason"] for item in stored["detail"]["refusals"]] == [expected]
            assert HtnStore(loop.store).list_plan_revisions(mission.id) == ()
            assert not _events(loop, mission.id, "PlanRevisionCommitted")
            # Never rebound: the request still names the grant it was bound to.
            after = _binding(loop, opener)
            assert (after["grant_id"], after["grant_revision"]) == (
                binding["grant_id"], binding["grant_revision"]
            )

    asyncio.run(case())
