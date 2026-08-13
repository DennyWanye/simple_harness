from __future__ import annotations

from types import SimpleNamespace

import pytest

from deskpet.execution.contracts import RunContext
from deskpet.harness.adapters.product_profiles import build_product_profile_registry
from deskpet.harness.ports import DriverStart
from deskpet.workflows.adapters.durable_task_runtime import workflow_session_ref
from deskpet.workflows.definitions.code_nodes import (
    MAX_DURABLE_PROPOSAL_TURNS,
    MAX_FIX_ROUNDS,
)
from deskpet.workflows.definitions.v1.durable_task import (
    DURABLE_TASK_V1_DEFINITION,
)
from deskpet.workflows.launcher import _resolve_precreated_session_binding


class Registry:
    def __init__(self, *, omit=()) -> None:
        self.omit = set(omit)

    def get(self, workflow_name, workflow_version):
        if (workflow_name, workflow_version) in self.omit:
            return None
        return SimpleNamespace(
            state_factory=lambda **values: values,
            context_factory=lambda *args, **kwargs: (args, kwargs),
        )


def request(profile_key: str, payload: dict) -> DriverStart:
    context = RunContext(
        session_id="session-1",
        root_run_id="run-1",
        parent_run_id=None,
        request_id="request-1",
        turn_id="turn-1",
        venue="text",
        workspace={},
        capability_hash="c" * 64,
        provider_plan={},
        trace_id="trace-1",
        principal_id="principal-1",
    )
    return DriverStart(
        run_id="run-1",
        session_id="session-1",
        canonical_messages=({"role": "user", "content": "fallback topic"},),
        run_context=context,
        profile_key=profile_key,
        request_payload=payload,
    )


def test_product_profiles_derive_research_and_ppt_payloads(tmp_path) -> None:
    profiles = build_product_profile_registry(Registry(), blob_root=tmp_path).specs

    personal = profiles["workflow.personal_v1"]
    assert personal.use_when == (
        "The host snapshot contains an exact matching Personal Workflow selection.",
    )
    assert personal.avoid_when == (
        "No trusted selection exists or a different workflow is requested.",
    )
    research = profiles["workflow.deep_research"].request_factory
    ppt = profiles["workflow.presentation"].request_factory
    assert research is not None and ppt is not None
    assert research(request("research.deep.v7", {"text": "topic"})) == {
        "topic": "topic",
        "mode": "standard",
        "research_config": {},
        "blob_root": str(tmp_path),
    }
    ppt_payload = ppt(request("ppt.create.v1", {"text": "deck", "pages": 5}))
    assert ppt_payload["pages"] == 5
    assert ppt_payload["editable_required"] is True
    assert ppt_payload["full_page_images"] is False
    assert profiles["workflow.deep_research"].capabilities == frozenset(
        {"workflow", "deep_research"}
    )
    assert profiles["workflow.presentation"].capabilities == frozenset(
        {"workflow", "ppt_workflow"}
    )


def test_durable_task_profile_rejects_lossy_payload(tmp_path) -> None:
    profiles = build_product_profile_registry(Registry(), blob_root=tmp_path).specs
    build = profiles["workflow.durable_task"].request_factory
    assert build is not None
    with pytest.raises(ValueError, match="durable task profile payload is incomplete"):
        build(request("durable.task.v1", {"text": "fix it"}))


def test_durable_task_profile_preserves_host_output_contract(tmp_path) -> None:
    profiles = build_product_profile_registry(Registry(), blob_root=tmp_path).specs
    build = profiles["workflow.durable_task"].request_factory
    assert build is not None
    contract = {
        "schema_version": 1,
        "workspace_root": str(tmp_path),
        "output_refs": ["summary.json"],
        "scratch_refs": [".scratch/"],
        "baseline_digest": "a" * 64,
    }
    payload = build(
        request(
            "workflow.durable_task",
            {
                "request": "summarize",
                "session_ref": {},
                "capability_snapshot": [],
                "messages": [{"role": "user", "content": "summarize"}],
                "provider_snapshot": {},
                "model_snapshot": {},
                "started_at": 1.0,
                "request_id": "request-1",
                "turn_id": "turn-1",
                "output_contract": contract,
            },
        )
    )
    assert payload["output_contract"] == contract


def test_capability_builder_is_a_bounded_durable_task_profile(tmp_path) -> None:
    profiles = build_product_profile_registry(Registry(), blob_root=tmp_path).specs
    builder = profiles["workflow.capability_build"]
    assert builder.workflow_key == "capability.build.v1"
    assert builder.workflow_name == "durable_task"
    assert builder.capabilities == frozenset({"workflow", "capability_build"})
    build = builder.request_factory
    assert build is not None
    payload = build(
        request(
            "workflow.capability_build",
            {
                "request": "build a reusable photo renamer",
                "session_ref": {},
                "capability_snapshot": [],
                "messages": [{"role": "user", "content": "build it"}],
                "provider_snapshot": {},
                "model_snapshot": {},
                "started_at": 1.0,
                "request_id": "request-1",
                "turn_id": "turn-1",
                "proposal_budget": 40,
                "fix_budget": 3,
            },
        )
    )
    assert payload["fix_budget"] == 3
    assert payload["proposal_budget"] == 40


def test_durable_task_engine_budget_covers_its_full_proposal_budget() -> None:
    definition = DURABLE_TASK_V1_DEFINITION
    worst_case_loop_supersteps = 3 * (
        MAX_DURABLE_PROPOSAL_TURNS + MAX_FIX_ROUNDS
    )

    assert definition.loop_budgets["proposal_turns"] == MAX_DURABLE_PROPOSAL_TURNS
    assert definition.max_supersteps >= (
        len(definition.nodes) + worst_case_loop_supersteps
    )
    assert definition.recursion_limit > definition.max_supersteps


def test_required_product_adapter_failure_is_explicit(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="ppt_pro@v1"):
        build_product_profile_registry(
            Registry(omit={("ppt_pro", "v1")}),
            blob_root=tmp_path,
        )


def test_model_catalog_and_workflow_driver_share_exact_immutable_catalog(tmp_path) -> None:
    profiles = build_product_profile_registry(Registry(), blob_root=tmp_path)
    assert {
        key: spec.workflow_key
        for key, spec in profiles.workflow_specs.items()
    } == {
        "workflow.durable_task": "durable.task.v1",
        "workflow.capability_build": "capability.build.v1",
        "workflow.deep_research": "research.deep.v7",
        "workflow.personal_v1": "personal.workflow.v1",
        "workflow.presentation": "ppt.create.v1",
    }
    assert tuple(sorted(profiles.model_spawnable)) == (
        "workflow.deep_research",
        "workflow.durable_task",
        "workflow.personal_v1",
        "workflow.presentation",
    )
    assert profiles.specs["workflow.capability_build"].launch_policy == (
        "reserved_control"
    )
    assert profiles.specs["agent.general"].driver_kind == "react"
    assert profiles.specs["agent.general"].launch_policy == "reserved_control"


def test_profile_catalog_has_no_semantic_route_tags() -> None:
    profiles = build_product_profile_registry(Registry(), blob_root="blobs")
    assert all(spec.route_tag is None for spec in profiles.specs.values())


def test_new_durable_task_session_identity_has_no_mode_fields(tmp_path) -> None:
    ref = workflow_session_ref(
        session_id="session-1",
        task_scope_id="task-1",
        delivery_session_id="session-1",
        workspace_root=tmp_path,
        session_epoch=2,
        task_epoch=3,
    ).to_dict()
    assert ref["session_id"] == "session-1"
    assert ref["task_scope_id"] == "task-1"
    assert ref["session_epoch"] == 2
    assert ref["task_epoch"] == 3
    assert "code_session_id" not in ref
    assert "base_session_id" not in ref


def test_precreated_launcher_accepts_mode_free_durable_task_session_ref(
    tmp_path,
) -> None:
    ref = workflow_session_ref(
        session_id="session-1",
        task_scope_id="task-1",
        delivery_session_id="session-1",
        workspace_root=tmp_path,
        session_epoch=2,
        task_epoch=3,
    ).to_dict()

    assert _resolve_precreated_session_binding(
        ref,
        session_id="session-1",
        base_epoch=2,
        code_session_id=None,
        delivery_session_id=None,
        code_epoch=0,
    ) == (None, "session-1", 0)


def test_precreated_launcher_rejects_mode_free_session_ref_from_another_run(
    tmp_path,
) -> None:
    ref = workflow_session_ref(
        session_id="other-session",
        task_scope_id="task-1",
        delivery_session_id="other-session",
        workspace_root=tmp_path,
        session_epoch=2,
        task_epoch=3,
    ).to_dict()

    with pytest.raises(
        ValueError,
        match="session binding disagrees with trusted Run context",
    ):
        _resolve_precreated_session_binding(
            ref,
            session_id="session-1",
            base_epoch=2,
            code_session_id=None,
            delivery_session_id=None,
            code_epoch=0,
        )


def test_precreated_launcher_keeps_legacy_code_session_binding() -> None:
    assert _resolve_precreated_session_binding(
        {
            "base_session_id": "session-1",
            "base_epoch": 2,
            "code_session_id": "code-1",
            "delivery_session_id": "session-1",
            "code_epoch": 4,
        },
        session_id="session-1",
        base_epoch=2,
        code_session_id=None,
        delivery_session_id=None,
        code_epoch=0,
    ) == ("code-1", "session-1", 4)
