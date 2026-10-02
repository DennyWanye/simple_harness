# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The one planning protocol: defaults, the durable binding, and the removed name.

2026-10-01: the proposal-text protocol and every historical package pairing are gone.
A charter that names nothing is a hierarchical Mission on ``planning-decision-v1``;
the old name is refused with a message that says it was removed; a hierarchical
Mission already in the library without a current binding is stopped by name.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest

from agent_orchestrator.contracts import Budget, ContractError, MissionStatus
from agent_orchestrator.contracts import planning_decisions as decision_contracts
from agent_orchestrator.contracts.planning_decisions import (
    PLANNING_DECISION_V1,
    UnsupportedPlanningPackage,
)
from agent_orchestrator.orchestrator.commit_service import (
    CommitRejected,
    CommitService,
    MissionConflict,
    MissionSpec,
    sha256_hex,
)
from agent_orchestrator.orchestrator.plan_commits import (
    HIERARCHICAL_SEMANTICS,
    semantics_of,
)
from agent_orchestrator.orchestrator.planning_protocol_binding import (
    PLANNING_PROTOCOLS,
    current_planning_protocol,
    planning_protocol_binding_hash,
    planning_protocol_for_mission,
    planning_protocol_replay_conflict,
)
from agent_orchestrator.runtime.role_templates import (
    PLANNING_DECISION_PACKAGE_VERSION,
    PLANNING_DECISION_PROMPT_VERSION,
)
from agent_orchestrator.storage.store import Store

REMOVED_NAME = "legacy-plan-proposal-v1"


def _spec(key: str, **kwargs: object) -> MissionSpec:
    return MissionSpec(
        goal="g",
        success_criteria=("ok",),
        tenant_id="tenant",
        idempotency_key=key,
        budget=Budget(max_tokens=1000, max_attempts=1),
        **kwargs,
    )


def _spec_with_field_set_behind_the_constructor(key: str, protocol_version: str) -> MissionSpec:
    """A spec whose ``planning_protocol_version`` slot was assigned directly (§8.1).

    ``object.__setattr__`` writes the frozen field without ``__post_init__`` — the same
    escape hatch a deserialiser or a hand-built record has.  The door must refuse the
    value regardless, before the document is digested or stored.
    """

    spec = _spec(key)
    object.__setattr__(spec, "planning_protocol_version", protocol_version)
    return spec


def binding_rows(store: Store, mission_id: str) -> int:
    return store.connection.execute(
        "SELECT count(*) FROM mission_planning_protocols WHERE mission_id = ?", (mission_id,)
    ).fetchone()[0]


# ---------------------------------------------------------------------------------------
# Defaults: a charter that names nothing is hierarchical, on the one protocol.
# ---------------------------------------------------------------------------------------


def test_the_default_spec_is_hierarchical_on_the_current_protocol(tmp_path) -> None:
    spec = _spec("default")
    assert spec.orchestration_semantics_version == HIERARCHICAL_SEMANTICS
    assert spec.planning_protocol_version == PLANNING_DECISION_V1
    document = spec.to_json()
    assert document["orchestration_semantics_version"] == HIERARCHICAL_SEMANTICS
    assert document["planning_protocol_version"] == PLANNING_DECISION_V1

    store = Store.open(tmp_path / "orchestrator.db")
    mission, created = CommitService(store).create_mission(spec)
    assert created
    assert semantics_of(mission) == HIERARCHICAL_SEMANTICS
    binding = planning_protocol_for_mission(store, mission.id)
    assert binding is not None
    assert binding["protocol_version"] == PLANNING_DECISION_V1
    assert binding["package_version"] == PLANNING_DECISION_PACKAGE_VERSION
    assert binding["prompt_version"] == PLANNING_DECISION_PROMPT_VERSION
    assert current_planning_protocol(store, mission.id) == binding


def test_the_request_parser_default_is_hierarchical_on_the_current_protocol() -> None:
    from agent_orchestrator.api.missions import spec_from_request

    plain = spec_from_request("tenant", {"idempotency_key": "y"})
    assert plain.orchestration_semantics_version == HIERARCHICAL_SEMANTICS
    assert plain.planning_protocol_version == PLANNING_DECISION_V1
    named = spec_from_request(
        "tenant", {"idempotency_key": "x", "planning_protocol_version": PLANNING_DECISION_V1}
    )
    assert named.to_json() == replace(plain, idempotency_key="x").to_json()


# ---------------------------------------------------------------------------------------
# The removed protocol name is refused, by name, at every door.
# ---------------------------------------------------------------------------------------


def test_the_removed_protocol_name_no_longer_exists_in_the_contract() -> None:
    assert PLANNING_PROTOCOLS == frozenset({"planning-decision-v1"})
    assert PLANNING_DECISION_V1 == "planning-decision-v1"
    assert not hasattr(decision_contracts, "LEGACY_PLANNING_PROTOCOL")
    from agent_orchestrator.orchestrator import commit_service

    assert not hasattr(commit_service, "LEGACY_PLANNING_PROTOCOL")


def test_the_removed_protocol_name_is_refused_with_a_message_that_says_so(tmp_path) -> None:
    with pytest.raises(ContractError, match="was removed"):
        _spec("old", planning_protocol_version=REMOVED_NAME)

    from agent_orchestrator.api.missions import MissionRequestError, spec_from_request

    with pytest.raises(MissionRequestError, match="was removed"):
        spec_from_request(
            "tenant", {"idempotency_key": "old", "planning_protocol_version": REMOVED_NAME}
        )

    store = Store.open(tmp_path / "orchestrator.db")
    forged = _spec_with_field_set_behind_the_constructor("forged-old", REMOVED_NAME)
    with pytest.raises(CommitRejected, match="was removed"):
        CommitService(store).create_mission(forged)
    assert store.find_mission("tenant", "forged-old") is None


def test_unknown_protocol_values_are_rejected(tmp_path) -> None:
    with pytest.raises(ContractError, match="planning protocol"):
        _spec("unknown", planning_protocol_version="planning-decision-v99")
    for invalid in ([], {}):
        with pytest.raises(ContractError, match="planning protocol"):
            _spec("invalid-type", planning_protocol_version=invalid)

    from agent_orchestrator.api.missions import MissionRequestError, spec_from_request

    for value in (1, {}, [PLANNING_DECISION_V1], "planning-decision-v99"):
        with pytest.raises(MissionRequestError, match="planning protocol"):
            spec_from_request(
                "tenant", {"idempotency_key": "bad", "planning_protocol_version": value}
            )

    store = Store.open(tmp_path / "orchestrator.db")
    forged = _spec_with_field_set_behind_the_constructor("forged", "planning-decision-v99")
    with pytest.raises(CommitRejected, match="planning protocol"):
        CommitService(store).create_mission(forged)
    assert store.find_mission("tenant", "forged") is None


# ---------------------------------------------------------------------------------------
# The durable binding of a hierarchical Mission.
# ---------------------------------------------------------------------------------------


def test_creation_writes_one_binding_with_the_frozen_hash(tmp_path) -> None:
    store = Store.open(tmp_path / "orchestrator.db")
    mission, _ = CommitService(store).create_mission(_spec("new"))
    binding = planning_protocol_for_mission(store, mission.id)
    document = {
        "protocol_version": PLANNING_DECISION_V1,
        "package_version": PLANNING_DECISION_PACKAGE_VERSION,
        "prompt_version": PLANNING_DECISION_PROMPT_VERSION,
    }
    expected = hashlib.sha256(
        json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    assert binding is not None and binding["binding_hash"] == expected
    assert planning_protocol_binding_hash(PLANNING_DECISION_V1) == expected
    assert binding_rows(store, mission.id) == 1


def test_binding_is_transactional_on_creation_failure(tmp_path) -> None:
    store = Store.open(tmp_path / "orchestrator.db")
    service = CommitService(store)
    observed: dict[str, object] = {}

    emit = service._emit

    def fail_after_binding(kind: str, mission_id: str, **kwargs: object):  # type: ignore[no-untyped-def]
        if kind == "MissionCreated":  # written right after the binding, in the same transaction
            observed["binding"] = planning_protocol_for_mission(store, mission_id)
            raise CommitRejected("boom")
        return emit(kind, mission_id, **kwargs)

    service._emit = fail_after_binding  # type: ignore[method-assign]
    with pytest.raises(CommitRejected, match="boom"):
        service.create_mission(_spec("rollback"))
    assert observed["binding"] is not None
    assert (
        store.connection.execute("SELECT count(*) FROM mission_planning_protocols").fetchone()[0]
        == 0
    )
    assert store.find_mission("tenant", "rollback") is None


def test_replay_is_idempotent_and_keeps_exactly_one_binding_row(tmp_path) -> None:
    store = Store.open(tmp_path / "orchestrator.db")
    service = CommitService(store)
    spec = _spec("idem")
    mission, _ = service.create_mission(spec)
    first = planning_protocol_for_mission(store, mission.id)
    again, created = service.create_mission(spec)
    assert again == mission and created is False
    service.create_mission(replace(spec, goal="g", success_criteria=("ok",)))
    assert binding_rows(store, mission.id) == 1
    assert planning_protocol_for_mission(store, mission.id) == first
    assert planning_protocol_replay_conflict(store, mission.id, PLANNING_DECISION_V1) is None
    assert store.find_mission("tenant", "idem")[1] == sha256_hex(spec.to_json())


def test_a_replay_against_a_tampered_binding_is_a_conflict(tmp_path) -> None:
    store = Store.open(tmp_path / "orchestrator.db")
    service = CommitService(store)
    spec = _spec("tampered")
    mission, _ = service.create_mission(spec)
    store.connection.execute(
        "UPDATE mission_planning_protocols SET package_version = 7 WHERE mission_id = ?",
        (mission.id,),
    )
    with pytest.raises(MissionConflict, match="durably bound"):
        service.create_mission(spec)


def test_binding_survives_a_new_connection(tmp_path) -> None:
    path = tmp_path / "orchestrator.db"
    first = Store.open(path)
    mission, _ = CommitService(first).create_mission(_spec("restart"))
    first.close()
    second = Store.open(path)
    assert current_planning_protocol(second, mission.id)["protocol_version"] == (
        PLANNING_DECISION_V1
    )


def test_the_durable_binding_ignores_the_ambient_environment(tmp_path, monkeypatch) -> None:
    """§8.2: the mode is never guessed from the environment, on write or on read."""

    monkeypatch.setenv("SIMPLE_HARNESS_PLANNING_PROTOCOL", REMOVED_NAME)
    monkeypatch.setenv("PLANNING_PROTOCOL_VERSION", REMOVED_NAME)
    store = Store.open(tmp_path / "orchestrator.db")
    service = CommitService(store)
    enabled, _ = service.create_mission(_spec("env-enabled"))
    assert planning_protocol_for_mission(store, enabled.id)["protocol_version"] == (
        PLANNING_DECISION_V1
    )


def test_policy_snapshot_digest_does_not_include_planning_protocol(tmp_path) -> None:
    from agent_orchestrator.governance import policies
    from agent_orchestrator.runtime.assembly import OrchestratorConfig

    snapshot = policies.policy_snapshot(OrchestratorConfig(evidence_root=tmp_path / "a"))
    assert "planning_protocol_version" not in snapshot["config"]
    assert "planning_protocol_version" not in json.dumps(snapshot)
    assert "planning_protocol_version" not in policies.SNAPSHOT_FIELDS
    assert "planning_protocol_version" not in {
        field.name for field in __import__("dataclasses").fields(OrchestratorConfig)
    }


# ---------------------------------------------------------------------------------------
# A hierarchical Mission built under a contract this build dropped is stopped by name.
# ---------------------------------------------------------------------------------------


def test_a_missing_or_stale_binding_is_named_unsupported(tmp_path) -> None:
    store = Store.open(tmp_path / "orchestrator.db")
    service = CommitService(store)
    unbound, _ = service.create_mission(_spec("unbound"))
    store.connection.execute(
        "DELETE FROM mission_planning_protocols WHERE mission_id = ?", (unbound.id,)
    )
    with pytest.raises(UnsupportedPlanningPackage, match="removed"):
        current_planning_protocol(store, unbound.id)

    stale, _ = service.create_mission(_spec("stale"))
    store.connection.execute(
        "UPDATE mission_planning_protocols SET package_version = 7 WHERE mission_id = ?",
        (stale.id,),
    )
    with pytest.raises(UnsupportedPlanningPackage, match="package 7"):
        current_planning_protocol(store, stale.id)


@pytest.mark.parametrize("damage", ["unbound", "stale_package", "stale_deployment"])
def test_the_loop_stops_an_old_contract_mission_and_leaves_the_others_alone(
    tmp_path, damage: str
) -> None:
    """Old development data is not migrated and not served on a fallback path: the
    Mission ends with ``unsupported_planning_package`` and the loop carries on."""

    from agent_orchestrator.orchestrator.event_handler import Orchestrator
    from agent_orchestrator.runtime.assembly import OrchestratorConfig
    from agent_orchestrator.testing.fixtures import RoleScriptedProvider

    async def case() -> None:
        config = OrchestratorConfig(evidence_root=tmp_path / "evidence", max_concurrency=1)
        async with Orchestrator(config, RoleScriptedProvider({"planner": []})) as loop:
            old, _ = loop.commit.create_mission(_spec("old-contract"))
            if damage == "unbound":
                loop.store.connection.execute(
                    "DELETE FROM mission_planning_protocols WHERE mission_id = ?", (old.id,)
                )
            elif damage == "stale_package":
                loop.store.connection.execute(
                    "UPDATE mission_planning_protocols SET package_version = 7"
                    " WHERE mission_id = ?",
                    (old.id,),
                )
            else:
                # A Mission whose first planning round froze a deployment identity this
                # build no longer produces (here: the removed repair switch).  Its next
                # round used to raise out of ``run()`` and take every Mission down.
                from agent_orchestrator.orchestrator.hierarchical_dispatch import (
                    append_hierarchical_event,
                )

                append_hierarchical_event(
                    loop.store, "PlanningDeploymentBound", old.id, key=old.id,
                    payload={"backend_id": "native", "limits": None,
                             "method_selection_policy": "MODEL_ON_MULTIPLE",
                             "repair_enabled": True})
            healthy, _ = loop.commit.create_mission(_spec("healthy"))
            await loop.run(max_cycles=3)
            stopped = loop.store.get_mission(old.id)
            assert stopped.status is MissionStatus.FAILED
            failure = (stopped.final_report or {}).get("planning_failure") or {}
            assert failure.get("reason") == "unsupported_planning_package"
            assert {"unbound": "removed", "stale_package": "package 7",
                    "stale_deployment": "deployment"}[damage] in failure["error"]
            # the other Mission is not touched by the stop (it has no assembly here,
            # so it simply stays where it is)
            assert loop.store.get_mission(healthy.id).status is MissionStatus.CREATED

    asyncio.run(case())
