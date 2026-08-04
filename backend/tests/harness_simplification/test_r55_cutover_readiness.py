from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.harness.adapters import product_composition
from scripts.acceptance import legacy_cutover_audit


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.asyncio
async def test_product_composition_rejects_semantic_classifier() -> None:
    with pytest.raises(TypeError, match="fixed agent.general root"):
        await product_composition.build_product_harness_composition(
            uow=object(),
            profiles=object(),
            workflow_launcher=object(),
            tool_registry=object(),
            loop_factory=object(),
            classifier=object(),
        )


@pytest.mark.asyncio
async def test_product_composition_builds_only_through_shared_runtime(monkeypatch) -> None:
    from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
    from deskpet.tools.registry import ToolRegistry

    calls = []
    runtime = SimpleNamespace(run_client=object())

    async def build_runtime(**kwargs):
        calls.append(kwargs)
        return runtime

    monkeypatch.setattr(product_composition, "build_harness_runtime", build_runtime)
    composition = await product_composition.build_product_harness_composition(
        uow=object(),
        profiles=ProfileRegistry((
            ProfileSpec("agent.general", "general", "react"),
            ProfileSpec(
                "workflow.fixture", "fixture", "workflow",
                workflow_key="fixture.v1", workflow_name="fixture",
                workflow_version="v1", state_factory=dict, context_factory=dict,
            ),
        )),
        workflow_launcher=object(),
        tool_registry=ToolRegistry(),
        loop_factory=object(),
    )
    composed_runtime, venue = composition
    assert composed_runtime is runtime
    assert venue._run_client is runtime.run_client
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_product_driver_graph_has_single_react_workflow_effect_and_child_owners(monkeypatch) -> None:
    from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
    from deskpet.tools.registry import ToolRegistry

    profiles = ProfileRegistry((
        ProfileSpec("agent.general", "general", "react"),
        ProfileSpec(
            "workflow.fixture", "fixture", "workflow",
            workflow_key="fixture.v1", workflow_name="fixture",
            workflow_version="v1", state_factory=lambda **_: {},
            context_factory=lambda: {}, request_factory=lambda request: request.request_payload,
        ),
    ))
    uow = SimpleNamespace()
    launcher = SimpleNamespace()
    loop_factory = object()

    captured = {}

    async def build_runtime(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(run_client=object())

    monkeypatch.setattr(product_composition, "build_harness_runtime", build_runtime)
    await product_composition.build_product_harness_composition(
        uow=uow, profiles=profiles, workflow_launcher=launcher,
        tool_registry=ToolRegistry(), loop_factory=loop_factory,
    )
    drivers = captured["drivers"]
    children = captured["child_runs"]
    effects = captured["tool_executor"]

    assert [(item.kind, item.durable_from_start, item.atomic_start) for item in drivers] == [
        ("react", True, False),
        ("workflow", True, True),
    ]
    assert children._store is uow
    assert effects._uow is uow
    assert drivers[0].driver._collaborator._loop_factory is loop_factory
    shared_fence = captured["run_execution_fence"]
    assert effects._provider_fence_acquirer.__self__ is shared_fence
    assert (
        drivers[1].driver._run_execution_fence_acquirer.__self__
        is shared_fence
    )


def test_product_factory_is_reachable_and_production_is_kernel_owned() -> None:
    result = legacy_cutover_audit.audit_dormant_factories(repo=ROOT)
    assert result["passed"] is True
    assert result["production_owner"] == "kernel/1"
    assert result["schema_starts_fail_closed"] is True


def test_r6_cutover_audit_requires_complete_clean_live_stacks(tmp_path) -> None:
    dynamic = tmp_path / "dynamic.json"
    dynamic.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "captured_commit": legacy_cutover_audit._full_commit("HEAD", repo=ROOT),
                "complete": True,
                "stacks": [{"stack_id": "text-kernel", "legacy_frames": []}],
            }
        ),
        encoding="utf-8",
    )
    manifest = legacy_cutover_audit._load_manifest(legacy_cutover_audit.DEFAULT_MANIFEST)
    assert manifest["owner_count"] == 15
    assert len(manifest["roots"]) == len(legacy_cutover_audit.ROOT_SPECS)
    cutover = legacy_cutover_audit.audit_cutover(manifest, live_stacks=dynamic, repo=ROOT)
    assert cutover["exact_matches"] == []
    assert cutover["similar_matches"] == [], json.dumps(
        cutover["similar_matches"], indent=2
    )
    assert cutover["references"] == [], json.dumps(cutover["references"], indent=2)
    assert cutover["passed"] is True

    payload = json.loads(dynamic.read_text(encoding="utf-8"))
    payload["complete"] = False
    dynamic.write_text(json.dumps(payload), encoding="utf-8")
    assert legacy_cutover_audit.audit_cutover(manifest, live_stacks=dynamic, repo=ROOT)["passed"] is False


def test_r55_manifest_generation_still_fails_closed_without_complete_dynamic_evidence(tmp_path) -> None:
    evidence = tmp_path / "dynamic.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "captured_commit": legacy_cutover_audit._full_commit("HEAD", repo=ROOT),
                "complete": False,
                "stacks": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(legacy_cutover_audit.CutoverAuditError, match="complete=true"):
        legacy_cutover_audit._load_dynamic(evidence, legacy_cutover_audit._full_commit("HEAD", repo=ROOT))
