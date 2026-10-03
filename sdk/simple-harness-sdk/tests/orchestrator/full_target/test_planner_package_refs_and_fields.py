# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The Planner package's protocol fields and its reference collector.

``planning_protocol`` (the protocol name and the decision kinds), ``planning_subjects``
(the per-request subject keys the model may name, §19), ``visible_refs`` (the §17
quadruples the model's references must byte-match, at most 128), ``previous_feedback``
(``PlanningFeedbackV1.to_json()`` or ``null``) and ``decision_limits`` (§16).

The reference collector is a pure function of the rows the package shows plus the
caller's authoritative digests for tasks and obligations (§5.1): a digest is stated by
whoever owns it, travels as an argument and never as a package key, and a source that
cannot state its reference correctly is skipped rather than invented.

The helpers below build the views from the row shapes each section of this file
exercises (a goal row, a method row, a fact row, an accepted result).

HTN 补齐阶段 A′：要真实任务行与存储列的用例，任务都经产品那一份部署组装建出
（:func:`_on_product_mission`，建任务即初始化根、绑定执行图、走保证通道），然后直接组包；
同一性质的几条合并成一条。"已提交计划后主题键仍唯一"一条删除，由
``product_world/test_sub_goal.py`` 覆盖（第二轮按 subject_key 点名子目标）；只用纯构造网络
就能说清的两条（坏权威行不崩、封包不渲染权威表）和"用组好的包测哈希辅助"改为纯函数用例。
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


from agent_orchestrator.contracts.models import ContractError  # noqa: E402
from agent_orchestrator.contracts.planning_decisions import (  # noqa: E402
    exposed_enablement,
    MAX_PD_ALTERNATIVES,
    MAX_PD_ARGUMENTS,
    MAX_PD_ASSUMPTIONS,
    MAX_PD_BINDINGS,
    MAX_PD_BLOCKERS,
    MAX_PD_HUMAN_OPTIONS,
    MAX_PD_RATIONALE_CHARS,
    MAX_PD_REASON_REFS,
    MAX_PD_REPLAN_TRIGGERS,
    MAX_PD_UNCERTAINTIES,
    MAX_PD_WAIT_REFS,
    PLANNING_DECISION_V1,
    PlanningFeedbackV1,
    PlanningRefKind,
    PlanningRefV1,
    PlanningRetryBudgetView,
)
from agent_orchestrator.contracts.semantic_base import content_hash_of  # noqa: E402
from agent_orchestrator.planning.htn.planner_package import (  # noqa: E402
    MAX_VISIBLE_REFS,
    VIEW_NAMES,
    PlannerPackageError,
    _merge_authorities,
    _network_authorities,
    assemble_planner_package,
    collect_refs,
    goal_rows,
    method_rows,
    method_signatures,
    package_hash,
    subject_bindings_hash,
    visible_refs_digest,
)

PACKAGE_VERSION = 10

#: The §32 structural system fields the model is forbidden to write.  The package
#: states ``mission.mission_id`` and the plan's ``plan_revision`` *nested*, as facts;
#: no top-level field may be one of these.
SYSTEM_BOUND_FIELDS = frozenset(
    {
        "mission_id",
        "tenant_id",
        "principal",
        "principal_id",
        "scope",
        "scope_id",
        "manager_epoch",
        "budget_account",
        "budget_grant_revision",
        "registry_status",
        "opened_by",
        "authorization_ref",
        "grant_ref",
        "provenance",
        "authored_by",
        "dispatch_generation",
        "plan_revision",
        "expected_plan_revision",
        "operation_id",
        "acceptance_id",
        "approval_id",
        "decision_id",
        "request_id",
    }
)

#: §38: the protocol fields.  The authoritative task/obligation digests §5.1 needs
#: travel as a *collector argument*, never as a package key.
DECISION_ONLY_FIELDS = frozenset(
    {
        "planning_protocol",
        "planning_subjects",
        "visible_refs",
        "previous_feedback",
        "decision_limits",
    }
)


def _on_product_mission(tmp_path: Any, build: Any) -> Any:
    """Create a user Mission through the product's deployment assembly (root initialised,
    TaskGraph bound, Assurance on, planning begun) and call ``build(seed)`` on it while
    its store is open; returns what ``build`` returned."""

    from h1i_seed import seeded

    async def case() -> Any:
        async with seeded(Path(tmp_path), key="planner-package-refs") as seed:
            return build(seed)

    return asyncio.run(case())


class _Budget:
    def to_json(self) -> dict[str, Any]:
        return {
            "max_tokens": 1000,
            "max_cost_micros": None,
            "max_attempts": 3,
            "max_runtime_seconds": None,
            "max_concurrency": None,
            "max_tool_calls": None,
        }


class _Mission:
    id = "mission-stub"
    goal = "stub goal"
    success_criteria = ("file:a.md",)
    allowed_tools = ("read",)
    risk_level = "low"
    budget = _Budget()


class _Network:
    """The smallest thing the packager reads: a plan with nothing on it."""

    mission_id = _Mission.id
    plan_revision = 0
    root_occurrence_ids: tuple[str, ...] = ()
    required_obligations: tuple[str, ...] = ()
    occurrences: tuple[Any, ...] = ()
    task_bindings: tuple[Any, ...] = ()

    def adopted_instance_for(self, occurrence_id: Any) -> Any:
        return None


def hierarchical_planner_package(
    mission: Any, network: Any, *, registry: Any = None, authoritative_refs: Sequence[Any] = (),
    previous_feedback: Any = None,
) -> dict[str, Any]:
    """The package as the pure assembler builds it from a network and a registry.

    The reader in ``orchestrator/planner_views`` adds the store-backed rows; the
    protocol fields and the references asserted here do not depend on them.
    """

    views: dict[str, Any] = {name: () for name in VIEW_NAMES}
    views["goals"] = goal_rows(network)
    views["plans"] = [{"plan_revision": int(network.plan_revision)}]
    if registry is not None:
        views["methods"] = method_rows(registry, method_signatures(network))[0]
    return assemble_planner_package(
        package_version=PACKAGE_VERSION, mission=mission, network=network, views=views,
        authorities=_merge_authorities(_network_authorities(network), authoritative_refs),
        previous_feedback=previous_feedback)


def stub_package(**kwargs: Any) -> dict[str, Any]:
    return hierarchical_planner_package(_Mission(), _Network(), registry=None, **kwargs)


def decision_package(**kwargs: Any) -> dict[str, Any]:
    return stub_package(**kwargs)


def empty_package(**sections: Any) -> dict[str, Any]:
    """The views the collector reads, given section by section.

    ``goals`` rows name a ``task_id`` and an ``obligation_id``; ``methods`` rows a
    ``method_ref``; ``facts`` rows an ``observation_ref``; ``accepted_results`` rows an
    ``acceptance_ref`` or a ``resolution_ref``.
    """

    views: dict[str, Any] = {name: [] for name in ("goals", "methods", "facts", "accepted_results")}
    for name, value in sections.items():
        assert name in views, name
        views[name] = value
    return views


def authority(kind: str, id: str, revision: int, digest: str) -> dict[str, Any]:
    """One sidecar quadruple: an authoritative (§5.1) digest for a task/obligation."""

    return {
        "kind": kind,
        "id": id,
        "semantic_revision": revision,
        "content_hash": digest,
    }


def refs_of(
    package: Mapping[str, Any], authorities: Sequence[Any] = ()
) -> tuple[dict[str, Any], ...]:
    """The collector, with the caller-supplied §5.1 digests as an argument."""

    views = package["views"] if "views" in package else package
    return tuple(collect_refs(views, authorities=authorities))


def by_key(
    package: Mapping[str, Any], authorities: Sequence[Any] = ()
) -> dict[tuple[str, str], dict[str, Any]]:
    return {(item["kind"], item["id"]): dict(item) for item in refs_of(package, authorities)}


def task_authorities(network: Any) -> list[dict[str, Any]]:
    """The task digests a caller reads off the network's bindings (§5.1)."""

    return [
        authority(
            "task",
            str(binding.task_id),
            int(binding.contract_revision),
            binding.content_hash(),
        )
        for binding in network.task_bindings
    ]


class _WideBinding:
    """Enough of ``TaskSemanticBindingV1`` for the packager: id, revision, digest."""

    def __init__(
        self, task_id: str, obligation_id: str, digest: str, revision: int = 1
    ) -> None:
        self.task_id = task_id
        self.obligation_id = obligation_id
        self.contract_revision = revision
        self.contract_hash = digest
        self.goal_signature = _Signature()
        self.typed_parameters: dict[str, Any] = {}
        self.requirement_refs: tuple[str, ...] = ()
        self.capability_requirements: tuple[str, ...] = ()

    def content_hash(self) -> str:
        return self.contract_hash


class _Signature:
    signature_id = "wide.signature"
    statement = "a wide goal"


class _Occurrence:
    def __init__(self, occurrence_id: str, task_id: str, obligation_id: str) -> None:
        from agent_orchestrator.contracts.htn import TaskForm

        self.occurrence_id = occurrence_id
        self.task_id = task_id
        self.obligation_id = obligation_id
        self.form = TaskForm.COMPOUND
        self.requiredness = "required"


class _WideNetwork:
    """A network with ``count`` compound goals, so the 128 cap is reachable."""

    mission_id = _Mission.id
    plan_revision = 0
    root_occurrence_ids: tuple[str, ...] = ()
    required_obligations: tuple[str, ...] = ()

    def __init__(self, count: int) -> None:
        self._by_occurrence: dict[str, _WideBinding] = {}
        occurrences: list[_Occurrence] = []
        bindings: list[_WideBinding] = []
        for index in range(count):
            task_id = f"task-{index:03d}"
            occurrence_id = f"occ-{index:03d}"
            digest = hex_digest(index + 1)
            binding = _WideBinding(task_id, f"obl-{index:03d}", digest)
            self._by_occurrence[occurrence_id] = binding
            bindings.append(binding)
            occurrences.append(_Occurrence(occurrence_id, task_id, f"obl-{index:03d}"))
        self.occurrences = tuple(occurrences)
        self.task_bindings = tuple(bindings)

    def adopted_instance_for(self, occurrence_id: str) -> Any:
        return None

    def binding_for_occurrence(self, occurrence_id: str) -> _WideBinding:
        return self._by_occurrence[occurrence_id]


def wide_world(count: int, *, obligations: bool = True) -> tuple[dict[str, Any], list[Any]]:
    """A wide network, the authority argument a caller would pass, and the package."""

    network = _WideNetwork(count)
    authorities: list[Any] = list(task_authorities(network))
    if obligations:
        authorities += obligation_authorities(count)
    package = hierarchical_planner_package(
        _Mission(),
        network,
        registry=None,
        authoritative_refs=authorities,
    )
    return package, authorities


def obligation_authorities(count: int) -> list[dict[str, Any]]:
    return [
        authority("obligation", f"obl-{index:03d}", 1, hex_digest(index + 1))
        for index in range(count)
    ]


def hex_digest(index: int) -> str:
    """A stable 64-character lowercase hex digest for one small integer."""

    return f"{index % 256:02x}" * 32


def method_entry(index: int, *, version: int = 1) -> dict[str, Any]:
    digest = hex_digest(index)
    return {
        "goal_signature_id": "sig",
        "method_ref": {"kind": "method", "id": f"m-{index:04d}", "semantic_revision": version,
                       "content_hash": digest},
        "registry_status": "UNKNOWN",
    }


# ======================================================================================
# 2. the new fields, present and correctly typed (§38, §39, §16)
# ======================================================================================


def test_the_decision_package_states_the_protocol_and_its_enabled_types() -> None:
    protocol = decision_package()["planning_protocol"]
    assert protocol["protocol"] == PLANNING_DECISION_V1
    decision_types, repair_kinds = exposed_enablement()
    assert protocol["enabled_decision_types"] == decision_types
    assert protocol["enabled_repair_kinds"] == repair_kinds
    assert all("/" not in value for value in decision_types + repair_kinds)


def test_the_package_states_its_version_as_an_integer() -> None:
    package = decision_package()
    assert package["package_version"] == PACKAGE_VERSION
    assert "output_contract" not in package  # the output format is the prompt's to state


def test_the_decision_limits_are_the_section_16_constants() -> None:
    limits = decision_package()["decision_limits"]
    assert limits == {
        "MAX_PD_RATIONALE_CHARS": MAX_PD_RATIONALE_CHARS,
        "MAX_PD_REASON_REFS": MAX_PD_REASON_REFS,
        "MAX_PD_ASSUMPTIONS": MAX_PD_ASSUMPTIONS,
        "MAX_PD_ALTERNATIVES": MAX_PD_ALTERNATIVES,
        "MAX_PD_UNCERTAINTIES": MAX_PD_UNCERTAINTIES,
        "MAX_PD_REPLAN_TRIGGERS": MAX_PD_REPLAN_TRIGGERS,
        "MAX_PD_BINDINGS": MAX_PD_BINDINGS,
        "MAX_PD_WAIT_REFS": MAX_PD_WAIT_REFS,
        "MAX_PD_BLOCKERS": MAX_PD_BLOCKERS,
        "MAX_PD_HUMAN_OPTIONS": MAX_PD_HUMAN_OPTIONS,
        "MAX_PD_ARGUMENTS": MAX_PD_ARGUMENTS,
    }


def test_previous_feedback_is_null_by_default() -> None:
    assert decision_package()["previous_feedback"] is None


def test_previous_feedback_is_written_as_plain_json_when_given() -> None:
    feedback = PlanningFeedbackV1(
        previous_decision_id="pd-0123456789abcdef01234567",
        status="REJECTED",
        rejection_codes=(),
        problems=(),
        changed_refs=(),
        budgets=PlanningRetryBudgetView(
            same_request_format_retries_remaining=1,
            planning_rounds_remaining=2,
            root_review_repairs_remaining=0,
            repeated_failure_before_escalation_remaining=None,
        ),
    )
    package = decision_package(previous_feedback=feedback)
    assert package["previous_feedback"] == feedback.to_json()
    assert json.loads(json.dumps(package))["previous_feedback"] == feedback.to_json()


def test_previous_feedback_is_validated_through_the_contract() -> None:
    with pytest.raises(ContractError):
        decision_package(previous_feedback={"previous_decision_id": ""})


# ======================================================================================
# 3. planning_subjects (§19): unique, stable, recomputable
# ======================================================================================


def test_planning_subjects_are_unique_stable_and_cover_every_occurrence(tmp_path: Any) -> None:
    """§19 on a Mission the product assembly created: two builds give the same subjects,
    every key is unique, each subject carries exactly the §19 keys, and every occurrence
    on the board is named.  (The "after a committed plan" variant is the sub-goal product
    case, whose second round names the sub-goal by its subject_key.)"""

    def build(seed: Any) -> tuple[Any, Any, set[str]]:
        network = seed.dispatch.network(seed.mission.id)
        first = hierarchical_planner_package(seed.mission, network, registry=seed.world.registry)
        second = hierarchical_planner_package(seed.mission, network, registry=seed.world.registry)
        return first["planning_subjects"], second["planning_subjects"], {
            str(item.occurrence_id) for item in network.occurrences}

    first, second, occurrences = _on_product_mission(tmp_path, build)
    assert first == second, "same request, same subjects"
    keys = [item["subject_key"] for item in first]
    assert keys and len(keys) == len(set(keys)), "every subject_key is unique"
    assert all(item["subject_key"].startswith("subject-") for item in first)
    for item in first:
        assert set(item) == {"subject_key", "occurrence_id", "task_id", "obligation_id", "contract_revision"}
        assert isinstance(item["contract_revision"], int) and not isinstance(item["contract_revision"], bool)
    assert {item["occurrence_id"] for item in first} == occurrences


# ======================================================================================
# 4. visible_refs: one case per source, fact rewritten to observation, deterministic
# ======================================================================================


def test_a_method_library_entry_becomes_a_method_ref() -> None:
    refs = refs_of(
        empty_package(methods=[method_entry(1, version=3)])
    )
    assert [dict(item) for item in refs] == [
        {
            "kind": "method",
            "id": "m-0001",
            "semantic_revision": 3,
            "content_hash": hex_digest(1),
        }
    ]


def test_an_applicability_row_also_exposes_its_method() -> None:
    package = empty_package(
        methods=[
            {
                "goal_occurrence_id": "occ-1",
                "goal_signature_id": "sig",
                "method_ref": {"method_id": "m-0002", "version": 2, "content_hash": "2" * 64},
                "verdict": "NEEDS_EVIDENCE",
            }
        ]
    )
    assert [dict(item) for item in refs_of(package)] == [
        {"kind": "method", "id": "m-0002", "semantic_revision": 2, "content_hash": "2" * 64}
    ]


def test_an_open_goal_yields_a_task_and_an_obligation_ref() -> None:
    """§5.1: the ref carries the object's *authoritative* digest, not a derived one."""

    task_hash = "a" * 64
    obligation_hash = "b" * 64
    package = empty_package(
        goals=[
            {
                "occurrence_id": "occ-1",
                "task_id": "task-1",
                "obligation_id": "obl-1",
                "contract_revision": 4,
            }
        ]
    )
    refs = by_key(
        package,
        [
            authority("task", "task-1", 4, task_hash),
            authority("obligation", "obl-1", 1, obligation_hash),
        ],
    )
    assert refs[("task", "task-1")] == {
        "kind": "task",
        "id": "task-1",
        "semantic_revision": 4,
        "content_hash": task_hash,
    }
    assert refs[("obligation", "obl-1")] == {
        "kind": "obligation",
        "id": "obl-1",
        "semantic_revision": 1,
        "content_hash": obligation_hash,
    }


def test_a_task_or_obligation_without_authority_is_skipped_rather_than_invented() -> None:
    """§18.5: a hash this package cannot state authoritatively is *omitted*, not faked."""

    package = empty_package(
        goals=[
            {
                "occurrence_id": "occ-1",
                "task_id": "task-1",
                "obligation_id": "obl-1",
                "contract_revision": 4,
            }
        ]
    )
    assert refs_of(package) == ()


def test_a_task_ref_takes_the_sidecars_revision_and_hash_verbatim() -> None:
    """A task entry that carries its own ``contract_revision`` still needs authority."""

    package = empty_package(
        goals=[
            {
                "occurrence_id": "occ-1",
                "task_id": "task-1",
                "obligation_id": "obl-1",
                "contract_revision": 9,
            }
        ]
    )
    refs = by_key(package, [authority("task", "task-1", 2, "c" * 64)])
    assert refs[("task", "task-1")]["semantic_revision"] == 2
    assert refs[("task", "task-1")]["content_hash"] == "c" * 64
    assert ("obligation", "obl-1") not in refs


def test_a_committed_primitive_yields_a_task_and_an_obligation_ref() -> None:
    package = empty_package(
        goals=[
            {"occurrence_id": "occ-2", "task_id": "task-2", "obligation_id": "obl-2"}
        ]
    )
    authorities = [
        authority("task", "task-2", 1, "d" * 64),
        authority("obligation", "obl-2", 1, "e" * 64),
    ]
    ids = {(item["kind"], item["id"]) for item in refs_of(package, authorities)}
    assert ids == {("task", "task-2"), ("obligation", "obl-2")}


def test_a_fact_entry_is_written_as_an_observation() -> None:
    """V2 §17: there is no ``fact`` kind; a recorded fact is an ``observation``."""

    package = empty_package(
        facts=[
            {
                "proposition_key": "p-1",
                "observation_ref": {
                    "kind": "fact",
                    "id": "obsrec-1",
                    "semantic_revision": 2,
                    "content_hash": "d" * 64,
                },
            }
        ]
    )
    refs = [dict(item) for item in refs_of(package)]
    assert refs == [
        {"kind": "observation", "id": "obsrec-1", "semantic_revision": 2, "content_hash": "d" * 64}
    ]
    assert PlanningRefKind.OBSERVATION.value == "observation"


def test_an_accepted_result_exposes_its_acceptance_ref() -> None:
    package = empty_package(
        accepted_results=[
            {"acceptance_ref": {"id": "acc-1", "semantic_revision": 5, "content_hash": "f" * 64}}
        ]
    )
    refs = [dict(item) for item in refs_of(package)]
    assert refs == [
        {"kind": "acceptance", "id": "acc-1", "semantic_revision": 5, "content_hash": "f" * 64}
    ]


def test_a_non_integer_revision_is_skipped_not_coerced() -> None:
    """P2-1: ``"3"`` and ``3.5`` are not revisions; the ref is dropped (§18.5)."""

    package = empty_package(
        methods=[
            {"method_ref": {"id": "m-str", "version": "3", "content_hash": "1" * 64}},
            {"method_ref": {"id": "m-float", "version": 3.5, "content_hash": "2" * 64}},
            method_entry(7, version=3),
        ]
    )
    assert [item["id"] for item in refs_of(package)] == ["m-0007"]


def test_a_non_positive_revision_is_skipped_not_raised_to_one() -> None:
    """P2-1: §5.1's "无则 1" is only for the ledger; a 0 revision has no valid四元组."""

    package = empty_package(
        goals=[{"task_id": "task-1", "obligation_id": "obl-1"}]
    )
    authorities = [
        authority("task", "task-1", 0, "a" * 64),
        authority("obligation", "obl-1", 0, "b" * 64),
    ]
    assert refs_of(package, authorities) == ()


def test_an_observation_without_a_hash_is_skipped_not_derived() -> None:
    """P2-3: §5.1 gives the observation hash as ``ReadItem.content_hash`` — no derivation."""

    package = empty_package(
        facts=[
            {"observation_ref": {"kind": "fact", "id": "obsrec-1", "semantic_revision": 1}},
            {
                "observation_ref": {
                    "kind": "fact",
                    "id": "obsrec-2",
                    "semantic_revision": 1,
                    "content_hash": "c" * 64,
                }
            },
        ]
    )
    assert [item["id"] for item in refs_of(package)] == ["obsrec-2"]


def test_a_resolution_ref_keeps_its_own_kind() -> None:
    """P2-2: §5.1 lists ``acceptance`` and ``resolution`` as two kinds, two sources."""

    package = empty_package(
        accepted_results=[
            {"resolution_ref": {"id": "res-1", "semantic_revision": 2, "content_hash": "d" * 64}}
        ]
    )
    assert by_key(package)[("resolution", "res-1")] == {
        "kind": "resolution",
        "id": "res-1",
        "semantic_revision": 2,
        "content_hash": "d" * 64,
    }


def test_the_authority_table_keys_on_kind_and_id_not_id_alone() -> None:
    """P2-5: a task and an obligation may share an id; the ref must not cross kinds."""

    package = empty_package(
        goals=[
            {"task_id": "shared", "obligation_id": "shared", "contract_revision": 1}
        ]
    )
    refs = by_key(
        package,
        [
            authority("task", "shared", 1, "a" * 64),
            authority("obligation", "shared", 2, "b" * 64),
        ],
    )
    assert refs[("task", "shared")] == {
        "kind": "task",
        "id": "shared",
        "semantic_revision": 1,
        "content_hash": "a" * 64,
    }
    assert refs[("obligation", "shared")] == {
        "kind": "obligation",
        "id": "shared",
        "semantic_revision": 2,
        "content_hash": "b" * 64,
    }


def test_a_caller_obligation_sharing_a_task_id_is_not_dropped() -> None:
    """P1-7: the merge's de-dup key must be ``(kind, id)``, not ``id`` alone.

    ``test_the_authority_table_keys_on_kind_and_id_not_id_alone`` covers the *lookup*
    side (``_authority_index``).  This covers the *merge* side (``_merge_authorities``)
    through the public builder: a task and an obligation **sharing an id** must both
    survive, the builder attesting the task and the caller supplying the obligation.
    A de-dup keyed on ``id`` alone would drop the caller's obligation.
    """

    shared = "shared"
    network = _WideNetwork(1)
    binding = network.task_bindings[0]
    # The two kinds carry their own identities by default; make them collide so the
    # only thing keeping the obligation is the *kind* component of the merge key.
    binding.task_id = shared
    binding.obligation_id = shared
    network.occurrences[0].task_id = shared
    network.occurrences[0].obligation_id = shared
    network.root_occurrence_ids = (network.occurrences[0].occurrence_id,)
    obligation_hash = "b" * 64
    package = hierarchical_planner_package(
        _Mission(),
        network,
        registry=None,
        authoritative_refs=[authority("obligation", shared, 1, obligation_hash)],
    )
    emitted = {(item["kind"], item["id"]): dict(item) for item in package["visible_refs"]}
    assert ("task", shared) in emitted
    assert ("obligation", shared) in emitted, "the obligation ref was dropped by the merge"
    assert emitted[("task", shared)]["content_hash"] == binding.content_hash()
    assert emitted[("obligation", shared)]["content_hash"] == obligation_hash


def test_an_authority_row_with_only_one_kind_does_not_answer_for_the_other() -> None:
    """P2-5: the obligation entry is not served by a task row that shares its id."""

    package = empty_package(
        goals=[
            {"task_id": "shared", "obligation_id": "shared", "contract_revision": 1}
        ]
    )
    authorities = [authority("task", "shared", 1, "a" * 64)]
    assert [item["kind"] for item in refs_of(package, authorities)] == ["task"]


def test_the_authority_sidecar_order_does_not_change_the_refs() -> None:
    """P2-6: the side table is an input set; its row order is not semantic."""

    entries = [
        {"task_id": "task-a", "obligation_id": "obl-a", "contract_revision": 1},
        {"task_id": "task-b", "obligation_id": "obl-b", "contract_revision": 1},
    ]
    rows = [
        authority("task", "task-a", 1, "a" * 64),
        authority("obligation", "obl-a", 1, "b" * 64),
        authority("task", "task-b", 1, "c" * 64),
        authority("obligation", "obl-b", 1, "d" * 64),
    ]
    forward = empty_package(goals=entries)
    assert refs_of(forward, rows) == refs_of(forward, list(reversed(rows)))


def test_an_acceptance_ref_wins_over_a_resolution_ref_on_one_row() -> None:
    """P2-7: an ``accepted_results`` row reports its acceptance when both are present."""

    package = empty_package(
        accepted_results=[
            {
                "acceptance_ref": {"id": "acc-1", "semantic_revision": 1, "content_hash": "a" * 64},
                "resolution_ref": {"id": "res-1", "semantic_revision": 1, "content_hash": "b" * 64},
            }
        ]
    )
    assert [dict(item) for item in refs_of(package)] == [
        {"kind": "acceptance", "id": "acc-1", "semantic_revision": 1, "content_hash": "a" * 64}
    ]


def test_a_resolution_ref_is_used_when_no_acceptance_ref_is_present() -> None:
    package = empty_package(
        accepted_results=[
            {"resolution_ref": {"id": "res-1", "semantic_revision": 1, "content_hash": "b" * 64}}
        ]
    )
    assert [item["kind"] for item in refs_of(package)] == [
        "resolution"
    ]


def test_the_refs_are_sorted_by_hash_as_the_last_component() -> None:
    """P2-9: two refs equal on (kind, id, revision) are ordered by their hash."""

    package = empty_package(
        methods=[
            {"method_ref": {"id": "m-same", "version": 1, "content_hash": "b" * 64}},
            {"method_ref": {"id": "m-same", "version": 1, "content_hash": "a" * 64}},
        ]
    )
    assert [item["content_hash"] for item in refs_of(package)] == ["a" * 64, "b" * 64]


def test_the_refs_are_sorted_by_id_ahead_of_revision_and_hash() -> None:
    """Self-audit: two same-kind refs whose id order opposes their (rev, hash) order.

    The kind test uses one ref per kind; without a second same-kind pair whose id
    order disagrees with the rest of the key, dropping the ``id`` component is
    invisible.  Here ``m-a`` sorts before ``m-z`` only because of the id.
    """

    package = empty_package(
        methods=[
            {"method_ref": {"id": "m-z", "version": 1, "content_hash": "a" * 64}},
            {"method_ref": {"id": "m-a", "version": 2, "content_hash": "e" * 64}},
        ]
    )
    assert [item["id"] for item in refs_of(package)] == ["m-a", "m-z"]


def test_the_refs_are_sorted_by_revision_numerically_not_as_strings() -> None:
    """P1-10: revision 10 must sort *after* 2, so the component is an int, not a str.

    ``_ref_sort_key`` compares the revision as an integer; if it were compared as a
    string, ``"10" < "2"`` would put revision 10 first.  The array order of
    ``visible_refs`` feeds the canonical hash (§15), so this is behaviour, not style.
    """

    package = empty_package(
        methods=[
            {"method_ref": {"id": "m-same", "version": 10, "content_hash": "a" * 64}},
            {"method_ref": {"id": "m-same", "version": 2, "content_hash": "a" * 64}},
        ]
    )
    assert [item["semantic_revision"] for item in refs_of(package)] == [2, 10]


def test_the_refs_are_sorted_by_revision_ahead_of_hash() -> None:
    """Self-audit: two refs sharing (kind, id) whose revision order opposes hash order."""

    package = empty_package(
        methods=[
            {"method_ref": {"id": "m-same", "version": 2, "content_hash": "a" * 64}},
            {"method_ref": {"id": "m-same", "version": 1, "content_hash": "e" * 64}},
        ]
    )
    assert [item["semantic_revision"] for item in refs_of(package)] == [1, 2]


def test_refs_differing_only_in_revision_are_not_collapsed() -> None:
    """Self-audit: the de-dup key includes ``semantic_revision``.

    Two refs with the same (kind, id, content_hash) but different revisions are two
    distinct §17 quadruples; keying de-dup without the revision would merge them.
    """

    package = empty_package(
        accepted_results=[
            {"acceptance_ref": {"id": "acc-1", "semantic_revision": 1, "content_hash": "a" * 64}},
            {"acceptance_ref": {"id": "acc-1", "semantic_revision": 2, "content_hash": "a" * 64}},
        ]
    )
    assert [item["semantic_revision"] for item in refs_of(package)] == [1, 2]


def test_refs_differing_only_in_id_are_not_collapsed() -> None:
    """Self-audit: the de-dup key includes ``id``.

    Two refs with the same (kind, revision, content_hash) but different ids are two
    distinct quadruples; keying de-dup without the id would merge them.
    """

    package = empty_package(
        methods=[
            {"method_ref": {"id": "m-one", "version": 1, "content_hash": "a" * 64}},
            {"method_ref": {"id": "m-two", "version": 1, "content_hash": "a" * 64}},
        ]
    )
    assert [item["id"] for item in refs_of(package)] == ["m-one", "m-two"]


def test_the_refs_are_sorted_by_kind_ahead_of_id() -> None:
    """P1-5: ``kind`` sorts ahead of ``id`` even when the ids disagree in the other direction.

    The previous pair (task ``"z"``, obligation ``"a"``) had id order coinciding with
    kind order, so dropping the ``kind`` component went unnoticed.  Here the task id
    sorts *before* the obligation id, so only a kind-first key yields obligation first.
    """

    package = empty_package(
        goals=[{"task_id": "a", "obligation_id": "z", "contract_revision": 1}]
    )
    authorities = [
        authority("task", "a", 1, "a" * 64),
        authority("obligation", "z", 1, "a" * 64),
    ]
    assert [(item["kind"], item["id"]) for item in refs_of(package, authorities)] == [
        ("obligation", "z"),
        ("task", "a"),
    ]


def test_a_malformed_authority_row_is_ignored_without_raising() -> None:
    """P2-10: a caller's junk row must not crash the collector nor produce a ref."""

    package = empty_package(
        goals=[{"task_id": "task-1", "obligation_id": "obl-1"}]
    )
    authorities = [
        "not-a-row",
        {"kind": "task"},  # no id
        {"id": "task-1"},  # no kind
        None,
        authority("task", "task-1", 1, "a" * 64),
    ]
    refs = refs_of(package, authorities)
    assert [item["id"] for item in refs] == ["task-1"]


def test_a_malformed_authority_row_does_not_crash_the_builder() -> None:
    """P2-10: a junk row at the *builder* boundary (where the sort runs) must not raise.

    The collector never requires a well-formed row — ``_authority_index`` skips one —
    so ordering must be equally forgiving: a caller that passes ``["not-a-row", None]``
    beside its real rows gets the real refs, not a ``TypeError`` mid-package.
    """

    network = _WideNetwork(2)
    authorities = [
        "not-a-row",
        None,
        {"kind": "task"},  # no id
        {"id": "task-000"},  # no kind
        *task_authorities(network),
    ]
    package = hierarchical_planner_package(_Mission(), network, registry=None, authoritative_refs=authorities)
    assert {("task", "task-000"), ("task", "task-001")} <= {
        (item["kind"], item["id"]) for item in package["visible_refs"]}


def test_a_malformed_source_ref_is_skipped_rather_than_invented() -> None:
    package = empty_package(
        methods=[
            {"method_ref": {"id": "m-1", "version": 1, "content_hash": "not-hex"}},
            {"method_ref": {"id": "", "version": 1, "content_hash": "a" * 64}},
            method_entry(2),
        ]
    )
    refs = [dict(item) for item in refs_of(package)]
    assert refs == [
        {
            "kind": "method",
            "id": "m-0002",
            "semantic_revision": 1,
            "content_hash": hex_digest(2),
        }
    ]


def test_every_built_ref_is_a_plain_valid_quadruple_and_nothing_is_guessed(tmp_path: Any) -> None:
    """On a Mission the product assembly created: every visible ref is a valid §17
    quadruple with exactly the four keys; with no obligation ledger digest handed in, no
    obligation ref is emitted (never a guess); the caller's own authority rows are the
    same plain quadruples as the refs they resolve to."""

    def build(seed: Any) -> tuple[Any, Any, Any]:
        network = seed.dispatch.network(seed.mission.id)
        authorities = task_authorities(network)
        bare = hierarchical_planner_package(seed.mission, network, registry=seed.world.registry)
        with_rows = hierarchical_planner_package(seed.mission, network, registry=seed.world.registry,
                                                 authoritative_refs=authorities)
        return bare, with_rows, authorities

    bare, with_rows, authorities = _on_product_mission(tmp_path, build)
    for package in (bare, with_rows):
        assert package["visible_refs"]
        for item in package["visible_refs"]:
            assert set(item) == {"kind", "id", "semantic_revision", "content_hash"}
            PlanningRefV1.from_json(item)
    assert "obligation" not in {item["kind"] for item in bare["visible_refs"]}
    assert "task" in {item["kind"] for item in bare["visible_refs"]}
    for row in authorities:
        assert set(row) == {"kind", "id", "semantic_revision", "content_hash"}
        PlanningRefV1.from_json(row)
    assert {row["kind"] for row in authorities} == {"task"}
    assert by_key(with_rows, authorities)[("task", authorities[0]["id"])]["content_hash"] == (
        authorities[0]["content_hash"])


def test_the_same_package_twice_yields_the_same_refs_in_the_same_order() -> None:
    package = empty_package(methods=[method_entry(index) for index in range(1, 6)])
    first = refs_of(package)
    second = refs_of(package)
    assert first == second
    assert list(first) == sorted(
        first,
        key=lambda item: (
            item["kind"],
            item["id"],
            item["semantic_revision"],
            item["content_hash"],
        ),
    )


def test_duplicate_sources_are_collapsed_to_one_ref() -> None:
    entry = method_entry(1)
    package = empty_package(methods=[entry, dict(entry)])
    assert len(refs_of(package)) == 1


def test_the_authority_table_is_a_lookup_and_never_a_source_of_refs() -> None:
    """A digest the caller attests is quoted only when a row names its object: an
    obligation no goal on the board carries is not something a decision may reference.
    A reference no row carries (an adopted method instance) is passed as ``extra``."""

    rows = [authority("obligation", "obl-elsewhere", 1, "a" * 64),
            authority("method_instance", "mi-1", 1, "b" * 64)]
    assert refs_of(empty_package(), rows) == ()
    assert [item["id"] for item in collect_refs(empty_package(), authorities=rows, extra=rows[1:])] == ["mi-1"]


def test_more_than_128_refs_is_refused_not_cut() -> None:
    """The references a decision may quote are never silently shortened: a cut ref
    would read to the model as an object that does not exist.  Over the bound the
    request is refused, and the planning subject has to be narrowed."""

    assert MAX_VISIBLE_REFS == 128
    at_the_bound, _ = wide_world(MAX_VISIBLE_REFS // 2)  # a task and an obligation per goal
    assert len(at_the_bound["visible_refs"]) == MAX_VISIBLE_REFS
    assert at_the_bound["truncated"] is False
    with pytest.raises(PlannerPackageError, match="exceed 128"):
        wide_world(MAX_VISIBLE_REFS // 2 + 1)


def test_a_ref_shown_by_two_rows_is_one_ref() -> None:
    entries = [method_entry(1), method_entry(2)]
    assert len(refs_of(empty_package(methods=[*entries, dict(entries[0])]))) == 2


def test_a_supplied_obligation_ledger_digest_reaches_the_built_package(tmp_path: Any) -> None:
    """The root duty's ledger digest, handed in by the caller, becomes the obligation
    ref; the package's ``visible_refs`` is exactly the collector's output over the same
    rows, nothing truncated.  (The product world registers no library method before
    the first planning round, so the kinds are task and obligation.)"""

    from agent_orchestrator.storage.obligation_store import ObligationStore
    from h1i_seed import root_duty

    def build(seed: Any) -> tuple[Any, Any, str, str]:
        network = seed.dispatch.network(seed.mission.id)
        duty_id = root_duty(seed.mission.id)
        duty = ObligationStore(seed.loop.store).obligation(seed.mission.id, duty_id)
        digest = content_hash_of(duty.to_json())
        authorities = [*task_authorities(network), authority("obligation", duty_id, 1, digest)]
        package = hierarchical_planner_package(seed.mission, network, registry=seed.world.registry,
                                               authoritative_refs=authorities)
        return package, authorities, duty_id, digest

    package, authorities, duty_id, digest = _on_product_mission(tmp_path, build)
    assert package["truncated"] is False and package["omitted_counts"] == {}
    assert package["visible_refs"] == list(refs_of(package, authorities))
    assert {item["kind"] for item in package["visible_refs"]} == {"task", "obligation"}
    assert by_key(package, authorities)[("obligation", duty_id)] == {
        "kind": "obligation", "id": duty_id, "semantic_revision": 1, "content_hash": digest}


def test_a_task_ref_is_the_bindings_own_stored_digest_whoever_calls(tmp_path: Any) -> None:
    """P1-4 / §5.1 on a Mission the product assembly created: a task ref's hash is the
    binding's own ``content_hash`` — the very ``task_semantics.content_hash`` column —
    and its revision the binding's ``contract_revision``; never a derived digest.  That
    holds with no caller rows (the production caller passes none), with the caller's
    rows, and when a caller row names the same task with a bogus digest (it cannot
    override the builder's own row)."""

    def build(seed: Any) -> tuple[Any, ...]:
        network = seed.dispatch.network(seed.mission.id)
        bindings = {str(spec.task_id): network.binding_for_occurrence(spec.occurrence_id)
                    for spec in network.occurrences}
        stored = {str(row[0]): str(row[1]) for row in seed.loop.store.connection.execute(
            "SELECT task_id, content_hash FROM task_semantics WHERE mission_id = ?", (seed.mission.id,))}
        authorities = task_authorities(network)
        some = next(iter(bindings))
        bogus = authority("task", some, 99, "f" * 64)
        packages = [hierarchical_planner_package(seed.mission, network, registry=seed.world.registry,
                                                 authoritative_refs=rows)
                    for rows in ((), authorities, [bogus])]
        return bindings, stored, authorities, packages, bogus

    bindings, stored, authorities, packages, bogus = _on_product_mission(tmp_path, build)
    assert bindings
    for package in packages:
        emitted = {(item["kind"], item["id"]): dict(item) for item in package["visible_refs"]}
        for task_id, binding in bindings.items():
            ref = emitted[("task", task_id)]
            assert ref["content_hash"] == binding.content_hash() == content_hash_of(binding.to_json())
            assert ref["content_hash"] == stored[task_id]
            assert ref["semantic_revision"] == int(binding.contract_revision)
            assert ref["content_hash"] != content_hash_of(
                {"kind": "task", "id": task_id, "semantic_revision": int(binding.contract_revision)})
            assert ref["content_hash"] != bogus["content_hash"]
    assert packages[1]["visible_refs"] == packages[0]["visible_refs"] == packages[2]["visible_refs"]


def test_a_task_ref_revision_is_the_binding_not_the_plan_revision() -> None:
    """P1-9: §5.1's ``semantic_revision`` is ``binding_revision`` — never ``plan_revision``.

    The fixture worlds all sit at ``plan_revision = 0``, so ``int(network.plan_revision)
    + int(binding.contract_revision)`` is indistinguishable from the binding's own
    revision there.  With ``plan_revision = 5`` and a binding at revision 1 the ref
    must still read 1: a plan-revision offset would break the §17 byte-match against
    ``task_semantics.binding_revision``.
    """

    network = _WideNetwork(1)
    binding = network.task_bindings[0]
    network.plan_revision = 5
    binding.contract_revision = 1
    package = hierarchical_planner_package(
        _Mission(),
        network,
        registry=None,
    )
    emitted = {(item["kind"], item["id"]): dict(item) for item in package["visible_refs"]}
    ref = emitted[("task", str(binding.task_id))]
    assert ref["semantic_revision"] == int(binding.contract_revision) == 1
    assert ref["semantic_revision"] != int(network.plan_revision) + int(binding.contract_revision)


def test_a_task_ref_revision_comes_from_the_binding_even_past_one(tmp_path: Any) -> None:
    """P1-8: §5.1's ``semantic_revision`` is ``binding_revision``, not the constant 1.

    Every binding in the fixture worlds happens to sit at revision 1, so an
    implementation that wrote ``1`` (or dropped the field's source) would pass them
    all.  A binding at ``contract_revision = 3`` — reachable in production, where a
    superseded binding is re-versioned — must surface ``3`` in the ref, because the
    §17 quadruple is byte-matched against ``task_semantics.binding_revision``.
    """

    network = _WideNetwork(1)
    binding = network.task_bindings[0]
    binding.contract_revision = 3
    package = hierarchical_planner_package(
        _Mission(),
        network,
        registry=None,
    )
    emitted = {(item["kind"], item["id"]): dict(item) for item in package["visible_refs"]}
    ref = emitted[("task", str(binding.task_id))]
    assert ref["semantic_revision"] == 3
    assert ref["semantic_revision"] != 1


def test_the_package_body_never_carries_a_sixth_field() -> None:
    """Ruling 2026-09-19 06:30: authorities travel as an argument, not as a key.

    The decision package carries V2 §38's five fields and neither ``authoritative_refs``
    nor ``visible_refs_omitted`` (the caller reads the latter from the helper).
    """

    authorities = [authority("obligation", "obl-root", 1, "b" * 64)]
    package = decision_package(authoritative_refs=authorities)
    assert DECISION_ONLY_FIELDS <= set(package)
    assert DECISION_ONLY_FIELDS == {
        "planning_protocol",
        "planning_subjects",
        "visible_refs",
        "previous_feedback",
        "decision_limits",
    }
    body = json.dumps(package)
    assert "authoritative_refs" not in body
    assert "visible_refs_omitted" not in body


def test_the_caller_authority_rows_are_an_input_set_not_a_sequence(tmp_path: Any) -> None:
    """P2-6 / P1-6 on a Mission the product assembly created: the order of the caller's
    rows never moves the package bytes (the request binding hashes the whole package),
    and two rows for one ``(kind, id)`` resolve to the smallest quadruple whatever the
    order — same revision with differing hashes, and differing revision *and* hash."""

    from h1i_seed import root_duty

    def build(seed: Any) -> dict[str, Any]:
        network = seed.dispatch.network(seed.mission.id)
        duty = root_duty(seed.mission.id)

        def both(rows: list[Any]) -> tuple[Any, Any]:
            make = lambda refs: hierarchical_planner_package(  # noqa: E731
                seed.mission, network, registry=seed.world.registry, authoritative_refs=refs)
            return make(rows), make(list(reversed(rows)))

        return {
            "duty": duty,
            "distinct": both([authority("obligation", duty, 1, "b" * 64),
                              authority("obligation", "obl-second", 1, "c" * 64)]),
            "same_rev": both([authority("obligation", duty, 1, "b" * 64),
                              authority("obligation", duty, 1, "a" * 64)]),
            "crossed": both([authority("obligation", duty, 2, "a" * 64),
                             authority("obligation", duty, 1, "f" * 64)]),
        }

    built = _on_product_mission(tmp_path, build)
    for name in ("distinct", "same_rev", "crossed"):
        forward, backward = built[name]
        assert forward["visible_refs"] == backward["visible_refs"], name
        assert forward == backward, name

    def winner(package: dict[str, Any]) -> dict[str, Any]:
        return {(item["kind"], item["id"]): item for item in package["visible_refs"]}[("obligation", built["duty"])]

    assert winner(built["same_rev"][0])["content_hash"] == "a" * 64
    crossed = winner(built["crossed"][0])
    assert (crossed["semantic_revision"], crossed["content_hash"]) == (1, "f" * 64)


def test_caller_authority_revisions_sort_numerically_not_as_strings() -> None:
    """Self-audit: the authority sort key's revision is an int too (P1-10 sibling).

    ``_authority_sort_key`` fixes the winner of a duplicate ``(kind, id)``; with
    revisions 2 and 10 the numeric minimum is 2.  A string comparison would pick 10
    (``"10" < "2"``), so the emitted obligation ref would carry the wrong revision.
    """

    shared = "shared"
    network = _WideNetwork(1)
    binding = network.task_bindings[0]
    binding.task_id = shared
    binding.obligation_id = shared
    network.occurrences[0].task_id = shared
    network.occurrences[0].obligation_id = shared
    network.root_occurrence_ids = (network.occurrences[0].occurrence_id,)
    package = hierarchical_planner_package(
        _Mission(),
        network,
        registry=None,
        authoritative_refs=[
            authority("obligation", shared, 10, "a" * 64),
            authority("obligation", shared, 2, "a" * 64),
        ],
    )
    emitted = {(item["kind"], item["id"]): dict(item) for item in package["visible_refs"]}
    assert emitted[("obligation", shared)]["semantic_revision"] == 2


def test_the_sealed_text_never_renders_the_authority_list() -> None:
    """Ruling: the authority digests are a call argument, never a model-visible key."""

    from agent_orchestrator.context.context_builder import _seal

    package, _authorities = wide_world(3)
    sealed = _seal(package)
    assert "## authoritative_refs" not in sealed.text
    assert "authoritative_refs" not in sealed.text
    assert "authoritative_refs" not in sealed.package


def test_the_built_decision_package_reports_a_stub_with_no_refs() -> None:
    package = decision_package()
    assert package["visible_refs"] == []
    assert package["truncated"] is False and package["omitted_counts"] == {}


# ======================================================================================
# 5. the three hash helpers (canonical JSON sha256; key order never matters)
# ======================================================================================


def test_package_hash_ignores_object_key_order() -> None:
    left = {"a": {"x": 1, "y": 2}, "b": [1, 2]}
    right = {"b": [1, 2], "a": {"y": 2, "x": 1}}
    assert package_hash(left) == package_hash(right)
    assert len(package_hash(left)) == 64


def test_package_hash_is_the_canonical_json_sha256() -> None:
    import hashlib

    from simple_harness.contracts import canonical_json

    value = {"b": [1, 2], "a": {"y": 2, "x": 1}}
    expected = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    assert package_hash(value) == expected


def test_package_hash_changes_when_an_array_order_changes() -> None:
    assert package_hash({"a": [1, 2]}) != package_hash({"a": [2, 1]})


def test_visible_refs_digest_ignores_object_key_order() -> None:
    left = [{"kind": "method", "id": "m-1", "semantic_revision": 1, "content_hash": "a" * 64}]
    right = [{"content_hash": "a" * 64, "semantic_revision": 1, "id": "m-1", "kind": "method"}]
    assert visible_refs_digest(left) == visible_refs_digest(right)
    assert len(visible_refs_digest(left)) == 64
    assert visible_refs_digest(left) != visible_refs_digest([])


def test_visible_refs_digest_changes_when_the_order_changes() -> None:
    first = [{"kind": "method", "id": "m-1", "semantic_revision": 1, "content_hash": "a" * 64}]
    second = [{"kind": "task", "id": "m-1", "semantic_revision": 1, "content_hash": "a" * 64}]
    assert visible_refs_digest(first) != visible_refs_digest(second)


def test_subject_bindings_hash_ignores_object_key_order() -> None:
    left = [
        {
            "subject_key": "subject-occ-1",
            "occurrence_id": "occ-1",
            "task_id": "task-1",
            "obligation_id": "obl-1",
            "contract_revision": 1,
        }
    ]
    right = [
        {
            "contract_revision": 1,
            "obligation_id": "obl-1",
            "task_id": "task-1",
            "occurrence_id": "occ-1",
            "subject_key": "subject-occ-1",
        }
    ]
    assert subject_bindings_hash(left) == subject_bindings_hash(right)
    assert subject_bindings_hash(left) != subject_bindings_hash([])


def test_the_helpers_are_independent_of_each_other() -> None:
    refs = [{"kind": "method", "id": "m-1", "semantic_revision": 1, "content_hash": "a" * 64}]
    subjects = [
        {
            "subject_key": "subject-occ-1",
            "occurrence_id": "occ-1",
            "task_id": "task-1",
            "obligation_id": "obl-1",
            "contract_revision": 1,
        }
    ]
    assert len({visible_refs_digest(refs), subject_bindings_hash(subjects), package_hash({})}) == 3


def test_the_helpers_hash_the_built_package_sections() -> None:
    package, authorities = wide_world(3)
    assert package_hash(package) == package_hash(json.loads(json.dumps(package)))
    assert visible_refs_digest(package["visible_refs"]) == visible_refs_digest(
        refs_of(package, authorities)
    )
    assert subject_bindings_hash(package["planning_subjects"]) == subject_bindings_hash(
        package["planning_subjects"]
    )


# ======================================================================================
# 6. the model still cannot be handed a system-authority field (§32)
# ======================================================================================


def test_no_new_top_level_field_is_a_system_bound_field() -> None:
    assert not (set(decision_package()) & SYSTEM_BOUND_FIELDS)


def test_planning_protocol_carries_only_the_name_and_the_enabled_types() -> None:
    assert set(decision_package()["planning_protocol"]) == {
        "protocol",
        "enabled_decision_types",
        "enabled_repair_kinds",
    }


def test_the_new_sections_leak_no_registry_or_authority_field() -> None:
    package = decision_package()
    for name in ("planning_protocol", "planning_subjects", "visible_refs", "decision_limits"):
        body = json.dumps(package[name])
        assert "registry_status" not in body
        assert "authorization_ref" not in body
        assert "grant_ref" not in body
