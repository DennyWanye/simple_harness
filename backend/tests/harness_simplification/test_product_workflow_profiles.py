from __future__ import annotations

from types import SimpleNamespace

import pytest

from deskpet.execution.contracts import RunContext
from deskpet.harness.adapters.product_profiles import build_product_workflow_profiles
from deskpet.harness.ports import DriverStart


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
    profiles = {
        profile.profile_key: profile
        for profile in build_product_workflow_profiles(Registry(), blob_root=tmp_path)
    }

    research = profiles["research.deep.v7"].start_payload_factory
    ppt = profiles["ppt.create.v1"].start_payload_factory
    assert research is not None and ppt is not None
    assert research(request("research.deep.v7", {"text": "topic"})) == {
        "topic": "topic",
        "mode": "standard",
        "research_config": {},
        "blob_root": str(tmp_path),
    }
    assert ppt(request("ppt.create.v1", {"text": "deck", "pages": 5}))["pages"] == 5


def test_code_profile_rejects_lossy_payload(tmp_path) -> None:
    profiles = {
        profile.profile_key: profile
        for profile in build_product_workflow_profiles(Registry(), blob_root=tmp_path)
    }
    build = profiles["code.execute.v1"].start_payload_factory
    assert build is not None
    with pytest.raises(ValueError, match="code profile payload is incomplete"):
        build(request("code.execute.v1", {"text": "fix it"}))


def test_required_product_adapter_failure_is_explicit(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="ppt_pro@v1"):
        build_product_workflow_profiles(
            Registry(omit={("ppt_pro", "v1")}),
            blob_root=tmp_path,
        )
