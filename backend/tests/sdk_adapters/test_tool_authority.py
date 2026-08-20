from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from simple_harness import CallId, EffectId, RunId
from simple_harness.tools import PreparedToolEffect, ToolCall, ToolSpec

from deskpet.permissions.policy import AuthorizationPolicyState
from deskpet.permissions.runtime import PreparedAuthorizationRuntime
from deskpet.product_state.authorization_saga import AuthorizationSagaRepository
from deskpet.product_state.database import ProductStateDatabase
from deskpet.product_state.task_grants import DurableTaskGrantAuthority
from deskpet.sdk_adapters.authorization import ProductAuthorizationAdapter
from deskpet.sdk_adapters.tool_authority import (
    SdkCapabilityBridgeAdapter,
    SdkPreparedAuthorizationPolicy,
    SdkRunToolAuthorityRegistry,
)


@dataclass(frozen=True)
class _Inventory:
    name: str
    dispatch_kind: str
    permission_category: str
    source: str = "real-tool-manifest"
    version: str = "v1"


class _AuthorizationStore:
    def __init__(self, mode: str = "auto", generation: int = 0) -> None:
        self.state = AuthorizationPolicyState(mode, generation, 10.0)

    async def get_policy_state(self):
        return self.state

    async def get_task_grant(self, _task_grant_id: str):
        return None


def _catalog() -> tuple[dict, tuple[_Inventory, ...]]:
    specs = [
        {
            "name": "tool_search",
            "description": "Search capabilities",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {
            "name": "read_file",
            "description": "Read one file",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    ]
    from deskpet.sdk_adapters.context_authority import canonical_sha256

    schema_fingerprints = {
        item["name"]: canonical_sha256(item["input_schema"])
        for item in specs
    }
    return (
        {
            "generation": 7,
            "content_fingerprint": canonical_sha256(specs),
            "specs": specs,
            "schema_fingerprints": schema_fingerprints,
        },
        (
            _Inventory("tool_search", "control", "read_file"),
            _Inventory("read_file", "async", "read_file"),
        ),
    )


def _prepare(
    registry: SdkRunToolAuthorityRegistry,
    run_id: str,
    *,
    session_id: str = "session-a",
    deferred_names=(),
):
    catalog, inventory = _catalog()
    return registry.prepare_run(
        run_id=run_id,
        session_id=session_id,
        request_id=f"request-{run_id}",
        root_run_id=f"root-{run_id}",
        task_scope_id=f"task-{run_id}",
        workspace_root=None,
        catalog=catalog,
        inventory=inventory,
        deferred_names=deferred_names,
    )


def _effect(run_id: str, *, effect_id: str = "effect-a") -> PreparedToolEffect:
    return PreparedToolEffect(
        EffectId(effect_id),
        RunId(run_id),
        ToolCall(CallId("call-a"), "read_file", {"path": "/tmp/input.txt"}),
        ToolSpec(
            "read_file",
            "Read one file",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
        {
            "session_id": "session-a",
            "root_run_id": f"root-{run_id}",
        },
    )


def test_run_authority_isolated_and_waiting_scope_survives_until_terminal():
    registry = SdkRunToolAuthorityRegistry()
    first = _prepare(registry, "run-a")
    second = _prepare(registry, "run-b", session_id="session-b")

    assert first.capability_hash == second.capability_hash
    assert first.scope_hash != second.scope_hash
    assert len(first.prepared_tool_set.logical_schemas()) == 2
    assert first.task_work_context.root_run_id == "root-run-a"
    assert first.capability_hash != "0" * 64
    assert first.scope_hash != "0" * 64

    waiting = registry.mark_waiting("run-a")
    assert waiting.lease_state == "waiting"
    assert registry.scope_store.get(
        waiting.prepared_tool_set.scope_id,
        session_id=waiting.session_id,
        request_id=waiting.request_id,
    ) is not None
    registry.mark_terminal("run-b", "completed")
    with pytest.raises(KeyError):
        registry.resolve("run-b")
    assert registry.resolve("run-a").lease_state == "waiting"

    # Restart reconstruction is deterministic and does not borrow another
    # Session's scope or catalog authority.
    reconstructed = SdkRunToolAuthorityRegistry()
    restored = _prepare(reconstructed, "run-a")
    assert restored.capability_hash == first.capability_hash
    assert restored.scope_hash == first.scope_hash


def test_restart_policy_generation_is_available_before_next_tool_decision():
    policy = SdkPreparedAuthorizationPolicy(
        PreparedAuthorizationRuntime(_AuthorizationStore("manual", 9)),
        SdkRunToolAuthorityRegistry(),
        initial_policy_generation=9,
    )

    assert policy.current_policy_generation() == 9
    policy.update_policy_generation(10)
    assert policy.current_policy_generation() == 10
    with pytest.raises(RuntimeError, match="regressed"):
        policy.update_policy_generation(9)


def test_real_capability_bridge_search_describe_activate_updates_exact_scope():
    registry = SdkRunToolAuthorityRegistry()
    _prepare(registry, "run-a", deferred_names=("read_file",))

    def context():
        return registry.resolve("run-a").execution_context()

    bridge = SdkCapabilityBridgeAdapter(registry, context)
    result = bridge.search("read file")
    assert [item["name"] for item in result["matches"]] == ["read_file"]
    capability_id = result["matches"][0]["capability_id"]
    described = bridge.describe(capability_id)
    assert described["schema_hash"] != "0" * 64
    proposal = bridge.activate(
        capability_id,
        described["schema_hash"],
        described["describe_nonce"],
    )
    assert proposal.prepared_capability.ref.name == "read_file"
    activated = registry.resolve("run-a")
    assert activated.prepared_tool_set.has_direct("read_file")
    assert activated.prepared_tool_set.revision == 2
    assert activated.capability_hash != _prepare(
        SdkRunToolAuthorityRegistry(), "run-a", deferred_names=("read_file",)
    ).capability_hash


@pytest.mark.asyncio
async def test_product_authorization_uses_exact_hashes_and_real_auto_grant(
    tmp_path: Path,
):
    authorities = SdkRunToolAuthorityRegistry()
    authority = _prepare(authorities, "run-a")
    runtime = PreparedAuthorizationRuntime(
        _AuthorizationStore("auto", 0), clock=lambda: 100.0
    )
    policy = SdkPreparedAuthorizationPolicy(
        runtime, authorities, clock=lambda: 100.0
    )
    database = ProductStateDatabase(tmp_path / "product.db")
    database.initialize()
    repository = AuthorizationSagaRepository(database, owner_id="test-sdk")
    adapter = ProductAuthorizationAdapter(
        repository,
        policy=policy,
        identity_factory=policy.identity_factory,
        grant_authority=DurableTaskGrantAuthority(
            database,
            policy_generation_provider=policy.current_policy_generation,
        ),
        grant_factory=policy.grant_factory,
        clock=lambda: 100.0,
    )
    prepared = _effect("run-a")

    allowed = await adapter.prepare(prepared)

    assert allowed.decision.value == "allow"
    facts = policy.facts_for(prepared)
    identity = policy.identity_factory(prepared, None)
    stored = repository.read(identity.authorization_id)
    assert stored is not None
    assert stored.identity.capability_hash == authority.capability_hash
    assert stored.identity.schema_hash == authority.specs["read_file"].schema_hash
    assert stored.identity.schema_hash == (
        _catalog()[0]["schema_fingerprints"]["read_file"]
    )
    assert stored.identity.scope_hash == authority.scope_hash
    assert "0" * 64 not in {
        stored.identity.capability_hash,
        stored.identity.schema_hash,
        stored.identity.scope_hash,
    }
    assert stored.identity.grant_fingerprint == facts.grant.fingerprint
    assert facts.grant.source == "policy:auto"
    assert facts.grant.policy_generation == 0
    assert facts.grant.resource_selectors[0].kind == "system_change"
    assert facts.call.args_hash in facts.grant.resource_selectors[0].canonical_value

    replay = await adapter.prepare(prepared)
    assert replay.decision.value == "allow"
    rows = database.connection.execute(
        "SELECT COUNT(*) FROM authorization_sagas"
    ).fetchone()
    assert int(rows[0]) == 1
    authorities.mark_terminal("run-a", "completed")
    with pytest.raises(RuntimeError, match="facts are unavailable"):
        policy.facts_for(prepared)


@pytest.mark.asyncio
async def test_manual_policy_prepares_user_grant_and_waits_for_sdk_decision():
    authorities = SdkRunToolAuthorityRegistry()
    _prepare(authorities, "run-a")
    policy = SdkPreparedAuthorizationPolicy(
        PreparedAuthorizationRuntime(
            _AuthorizationStore("manual", 3), clock=lambda: 100.0
        ),
        authorities,
        clock=lambda: 100.0,
    )
    prepared = _effect("run-a", effect_id="effect-manual")

    result = await policy.decide(prepared, request=None)

    assert result.decision.value == "require_user"
    assert result.request is not None
    facts = policy.facts_for(prepared)
    assert facts.grant.source == "user"
    assert facts.grant.policy_generation == 3
    identity = policy.identity_factory(prepared, result.request)
    assert identity.grant_fingerprint == facts.grant.fingerprint
    assert identity.decision_nonce == result.request.nonce


@pytest.mark.asyncio
async def test_authorization_fails_closed_for_deferred_or_cross_run_tool():
    authorities = SdkRunToolAuthorityRegistry()
    _prepare(authorities, "run-a", deferred_names=("read_file",))
    _prepare(authorities, "run-b", session_id="session-b")
    policy = SdkPreparedAuthorizationPolicy(
        PreparedAuthorizationRuntime(
            _AuthorizationStore("auto", 0), clock=lambda: 100.0
        ),
        authorities,
        clock=lambda: 100.0,
    )

    with pytest.raises(RuntimeError, match="capability_denied"):
        await policy.decide(_effect("run-a"), request=None)
    with pytest.raises(KeyError):
        await policy.decide(_effect("unknown-run"), request=None)


@pytest.mark.asyncio
async def test_same_effect_id_is_isolated_by_run_identity():
    authorities = SdkRunToolAuthorityRegistry()
    _prepare(authorities, "run-a")
    _prepare(authorities, "run-b", session_id="session-b")
    policy = SdkPreparedAuthorizationPolicy(
        PreparedAuthorizationRuntime(
            _AuthorizationStore("auto", 0), clock=lambda: 100.0
        ),
        authorities,
        clock=lambda: 100.0,
    )
    first = _effect("run-a", effect_id="same-effect")
    second = _effect("run-b", effect_id="same-effect")

    await policy.decide(first, request=None)
    await policy.decide(second, request=None)

    assert policy.facts_for(first).authority.run_id == "run-a"
    assert policy.facts_for(second).authority.run_id == "run-b"
