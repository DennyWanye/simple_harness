from __future__ import annotations

from types import SimpleNamespace

import pytest

from deskpet.execution.contracts import RunContext
from deskpet.harness.adapters.product_profiles import build_product_profile_registry
from deskpet.harness.adapters.routing import DeskPetRouteClassifier
from deskpet.harness.ports import DriverStart
from deskpet.harness.router import RouteRequest


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

    research = profiles["workflow.deep_research"].request_factory
    ppt = profiles["workflow.ppt_pro"].request_factory
    assert research is not None and ppt is not None
    assert research(request("research.deep.v7", {"text": "topic"})) == {
        "topic": "topic",
        "mode": "standard",
        "research_config": {},
        "blob_root": str(tmp_path),
    }
    assert ppt(request("ppt.create.v1", {"text": "deck", "pages": 5}))["pages"] == 5


def test_code_profile_rejects_lossy_payload(tmp_path) -> None:
    profiles = build_product_profile_registry(Registry(), blob_root=tmp_path).specs
    build = profiles["workflow.code_complex"].request_factory
    assert build is not None
    with pytest.raises(ValueError, match="code profile payload is incomplete"):
        build(request("code.execute.v1", {"text": "fix it"}))


def test_required_product_adapter_failure_is_explicit(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="ppt_pro@v1"):
        build_product_profile_registry(
            Registry(omit={("ppt_pro", "v1")}),
            blob_root=tmp_path,
        )


def test_router_and_workflow_driver_share_exact_immutable_catalog(tmp_path, monkeypatch) -> None:
    profiles = build_product_profile_registry(Registry(), blob_root=tmp_path)
    assert {
        key: spec.workflow_key
        for key, spec in profiles.workflow_specs.items()
    } == {
        "workflow.deep_research": "research.deep.v7",
        "workflow.ppt_pro": "ppt.create.v1",
        "workflow.code_complex": "code.execute.v1",
    }
    from deskpet.harness.adapters import routing
    from deskpet.workflows.routing import RouteDecision, WorkflowRoute

    monkeypatch.setattr(
        routing, "route_task",
        lambda *args, **kwargs: RouteDecision(WorkflowRoute.DEEP_RESEARCH, "fixture", 1.0),
    )
    classified = DeskPetRouteClassifier(profiles).classify(
        RouteRequest("research", "request-1", "turn-1")
    )
    assert classified.profile_key == "workflow.deep_research"
