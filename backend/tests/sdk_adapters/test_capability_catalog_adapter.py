# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from types import SimpleNamespace

import pytest
from simple_harness import RunId

from deskpet.sdk_adapters.capability_catalog import (
    ProductCapabilityCatalogSourceAdapter,
    ProductExecutableSourceFact,
    ProductSkillSourceFact,
    ProductWorkflowSourceFact,
)


_A = "a" * 64
_B = "b" * 64
_C = "c" * 64
_D = "d" * 64
_E = "e" * 64


def _builtin(name: str = "tool_search") -> tuple[object, object]:
    return (
        SimpleNamespace(
            name=name,
            description="Search the frozen capability catalog.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        SimpleNamespace(
            name=name,
            source="real-tool-manifest",
            version="catalog-v1",
            execution_identity=_A,
            permission_category="read_file",
        ),
    )


def _mcp(*, stateful_name: str = "mcp_playwright_browser_navigate") -> object:
    return SimpleNamespace(
        specs=(
            SimpleNamespace(
                name=stateful_name,
                source="mcp:playwright",
                schema={
                    "name": stateful_name,
                    "description": "Navigate a browser page.",
                    "parameters": {
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                        "additionalProperties": False,
                    },
                },
                fixture_remote_name="browser_navigate",
                stable_handler_id="mcp:playwright:browser_navigate",
                execution_build_identity=SimpleNamespace(fingerprint=_B),
                dispatch_adapter_id="mcp.stdio",
                dispatch_adapter_version="v1",
                dispatch_adapter_fingerprint=_C,
                permission_category="browser",
            ),
        )
    )


def _skill(*, enabled: bool = True) -> object:
    return SimpleNamespace(
        name="translate-doc",
        description="Translate a document while preserving structure.",
        owner_key="builtin",
        pack_id="skill-translate-doc",
        version="0.1.0",
        manifest_hash=_A,
        content_hash=_B,
        scope_hash=_C,
        allowed_tools=("doc_read", "doc_edit"),
        triggers=("翻译",),
        task_types=("task",),
        when_to_use="The user asks to translate a document.",
        user_invocable=True,
        disable_model_invocation=not enabled,
    )


def _workflow() -> object:
    schema = {
        "type": "object",
        "properties": {"topic": {"type": "string"}},
        "required": ["topic"],
        "additionalProperties": False,
    }
    from deskpet.sdk_adapters.context_authority import canonical_sha256

    return SimpleNamespace(
        profile=SimpleNamespace(
            descriptor=SimpleNamespace(
                key="workflow.deep_research",
                description="Produce a durable sourced report.",
                use_when="The user requests multi-source research.",
                avoid_when="The user needs only a short answer.",
                input_schema_ref="deskpet://workflow.deep_research/start/v1",
                fingerprint=_D,
            ),
            workflow_name="deep_research",
            workflow_version="v1",
            start_input_schema=SimpleNamespace(
                canonical_schema=schema,
                schema_hash=canonical_sha256(schema),
            ),
        ),
        expected_implementation_fingerprint=_E,
    )


def test_four_host_sources_form_one_stable_body_free_projection() -> None:
    builtin_spec, builtin_inventory = _builtin()
    adapter = ProductCapabilityCatalogSourceAdapter()

    projection = adapter.compose(
        builtin_specs=(builtin_spec,),
        builtin_inventory=(builtin_inventory,),
        direct_tool_names=("tool_search",),
        mcp_catalog=_mcp(),
        mcp_server_states={"playwright": "running"},
        mcp_incarnations={"playwright": "playwright-incarnation-7"},
        skills=(_skill(),),
        workflows=(_workflow(),),
    )
    reordered = adapter.compose(
        builtin_specs=(builtin_spec,),
        builtin_inventory=(builtin_inventory,),
        direct_tool_names=("tool_search",),
        workflows=(_workflow(),),
        skills=(_skill(),),
        mcp_catalog=_mcp(),
        mcp_incarnations={"playwright": "playwright-incarnation-7"},
        mcp_server_states={"playwright": "running"},
    )

    assert projection.fingerprint == reordered.fingerprint
    assert [item.canonical_id for item in projection.records] == [
        "builtin:tool_search",
        "mcp:playwright:browser_navigate",
        "skill:translate-doc",
        "workflow:deep_research",
    ]
    builtin, mcp, skill, workflow = projection.records
    assert isinstance(builtin, ProductExecutableSourceFact)
    assert builtin.exposure_mode == "direct"
    assert isinstance(mcp, ProductExecutableSourceFact)
    assert mcp.exposure_mode == "deferred"
    assert mcp.source_revision == "playwright-incarnation-7"
    assert isinstance(skill, ProductSkillSourceFact)
    assert "instruction" not in skill.to_json()
    assert isinstance(workflow, ProductWorkflowSourceFact)
    assert workflow.profile_key == "workflow.deep_research"
    assert projection.exclusions == ()


def test_unhealthy_mcp_and_model_disabled_skill_are_explicitly_excluded() -> None:
    builtin_spec, builtin_inventory = _builtin()
    projection = ProductCapabilityCatalogSourceAdapter().compose(
        builtin_specs=(builtin_spec,),
        builtin_inventory=(builtin_inventory,),
        direct_tool_names=("tool_search",),
        mcp_catalog=_mcp(),
        mcp_server_states={"playwright": "reconnecting"},
        mcp_incarnations={"playwright": "playwright-incarnation-8"},
        skills=(_skill(enabled=False),),
    )

    assert [item.canonical_id for item in projection.records] == [
        "builtin:tool_search"
    ]
    assert {(item.source_kind, item.reason_code) for item in projection.exclusions} == {
        ("mcp", "mcp_not_running"),
        ("skill", "skill_model_invocation_disabled"),
    }


def test_running_mcp_without_incarnation_is_not_silently_admitted() -> None:
    builtin_spec, builtin_inventory = _builtin()
    projection = ProductCapabilityCatalogSourceAdapter().compose(
        builtin_specs=(builtin_spec,),
        builtin_inventory=(builtin_inventory,),
        direct_tool_names=("tool_search",),
        mcp_catalog=_mcp(),
        mcp_server_states={"playwright": "running"},
    )

    assert [item.reason_code for item in projection.exclusions] == [
        "mcp_incarnation_unavailable"
    ]


def test_catalog_rejects_inventory_drift_unknown_direct_and_oversized_kernel() -> None:
    builtin_spec, builtin_inventory = _builtin()
    adapter = ProductCapabilityCatalogSourceAdapter()

    with pytest.raises(ValueError, match="inventory is missing"):
        adapter.compose(
            builtin_specs=(builtin_spec,),
            builtin_inventory=(),
            direct_tool_names=("tool_search",),
        )
    with pytest.raises(ValueError, match="direct Tool names are absent"):
        adapter.compose(
            builtin_specs=(builtin_spec,),
            builtin_inventory=(builtin_inventory,),
            direct_tool_names=("tool_search", "missing"),
        )
    with pytest.raises(ValueError, match="exceeds 24"):
        adapter.compose(
            builtin_specs=(builtin_spec,),
            builtin_inventory=(builtin_inventory,),
            direct_tool_names=tuple(f"tool-{index}" for index in range(25)),
        )


def test_schema_bytes_are_detached_from_live_source_objects() -> None:
    builtin_spec, builtin_inventory = _builtin()
    projection = ProductCapabilityCatalogSourceAdapter().compose(
        builtin_specs=(builtin_spec,),
        builtin_inventory=(builtin_inventory,),
        direct_tool_names=("tool_search",),
    )
    before = projection.fingerprint
    builtin_spec.input_schema["properties"]["query"]["type"] = "integer"

    assert projection.fingerprint == before
    assert projection.to_json()["records"][0]["input_schema"]["properties"]["query"][
        "type"
    ] == "string"


def test_current_first_party_skill_and_workflow_sources_keep_exact_identities() -> None:
    from deskpet.capabilities.manifest import PackEnvironment
    from deskpet.companion.skills import (
        ManagedSkillDiscoveryProjection,
        inventory_first_party_skill_packs,
    )
    from deskpet.sdk_adapters.workflows import build_product_workflow_registrations
    from paths import first_party_capability_pack_roots

    inventory = inventory_first_party_skill_packs(
        first_party_capability_pack_roots(),
        environment=PackEnvironment(
            deskpet_version="0.6.0",
            os="macos",
            architecture="aarch64",
            python_version="3.11",
        ),
    )
    skills = ManagedSkillDiscoveryProjection(inventory)
    translate = skills.get("translate-doc")
    assert translate is not None
    builtin_spec, builtin_inventory = _builtin()

    projection = ProductCapabilityCatalogSourceAdapter().compose(
        builtin_specs=(builtin_spec,),
        builtin_inventory=(builtin_inventory,),
        direct_tool_names=("tool_search",),
        skills=(translate,),
        workflows=build_product_workflow_registrations(
            generation=9, transaction_owner=object()
        ),
    )

    by_id = {item.canonical_id: item for item in projection.records}
    frozen_skill = by_id["skill:translate-doc"]
    assert isinstance(frozen_skill, ProductSkillSourceFact)
    assert frozen_skill.manifest_hash == (
        "fe364ff884bb213aebb73bde9a29033f42ac3a85ce09b85d664b5d065f6b5d64"
    )
    assert frozen_skill.content_hash == (
        "bd2bb330f2992c91a79ad24a24bbcc105b82075124e00a3922b79838df489e35"
    )
    assert {key for key in by_id if key.startswith("workflow:")} == {
        "workflow:deep_research",
        "workflow:presentation",
    }


def test_sdk_resource_records_are_body_free_and_deferred() -> None:
    from simple_harness.tools import RuntimeToolCatalog, SkillResourceRecord

    records = ProductCapabilityCatalogSourceAdapter().sdk_resource_records(
        skills=(_skill(),),
        workflows=(_workflow(),),
    )
    catalog = RuntimeToolCatalog(records, generation=1)

    assert [item.capability_id for item in records] == [
        "skill:translate-doc",
        "workflow:deep_research",
    ]
    skill = records[0]
    assert isinstance(skill, SkillResourceRecord)
    assert skill.capability_id == "skill:translate-doc"
    assert skill.skill_locator == "translate-doc"
    assert "instruction" not in skill.to_json()
    state = catalog.start_run(RunId("run-skill-search"))
    matches = catalog.search(
        state,
        "Markdown translation preserve heading hierarchy",
    )
    assert matches.items[0].capability_id == "skill:translate-doc"
    assert matches.items[0].selection_key == "translate-doc"
    assert all(item.exposure_mode.value == "deferred" for item in records)
    assert catalog.snapshot.fingerprint != "0" * 64


def test_sdk_skill_namespace_accepts_records_from_distinct_pack_owners() -> None:
    from simple_harness.tools import RuntimeToolCatalog

    builtin = _skill()
    user_global = SimpleNamespace(
        **{
            **vars(builtin),
            "name": "global-plan-test",
            "owner_key": "user:v2:" + "9" * 64,
            "pack_id": "global-plan-test",
        }
    )
    records = ProductCapabilityCatalogSourceAdapter().sdk_resource_records(
        skills=(builtin, user_global),
    )

    catalog = RuntimeToolCatalog(records, generation=1)

    assert [item.skill_locator for item in catalog.snapshot.records] == [
        "global-plan-test",
        "translate-doc",
    ]
    assert {item.source for item in records} == {"product-skill-catalog"}
    assert {item.source_revision for item in records} == {
        "product-skill-catalog-v1"
    }
