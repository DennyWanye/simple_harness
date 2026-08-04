from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.permissions.effect_policy import IrreversibleEffectPolicy
from deskpet.permissions.policy import AuthorizationPolicyState
from deskpet.permissions.runtime import PreparedAuthorizationRuntime
from deskpet.permissions.task_grants import ResourceSelector
from deskpet.harness.drivers.react import ReActDriver, ReactToolBatch
from deskpet.harness.ports import DriverStart, ExecuteTools
from deskpet.harness.tool_executor import EffectBatchExecutor
from deskpet.tools.build_identity import EffectClass, IdempotencyClass
from deskpet.tools.capabilities import (
    PreparedToolCapability,
    PreparedToolSet,
    ToolCapabilityRef,
    ToolCapabilityScopeStore,
    ToolEligibilityContext,
    ToolExecutionContext,
    canonical_hash,
)
from deskpet.tools.prepared_snapshot import (
    dump_prepared_tool_set,
    load_prepared_tool_set,
)
from deskpet.tools.registry import ToolSpec
from deskpet.workflows.effects import PreparedToolCall


def _spec(name: str, effect: EffectClass) -> ToolSpec:
    schema = {
        "name": name,
        "description": name,
        "parameters": {"type": "object", "properties": {}},
    }
    return ToolSpec(
        name=name,
        toolset="test",
        schema=schema,
        handler=lambda _args, _task_id: "{}",
        schema_hash=canonical_hash(schema),
        stable_handler_id=f"core.{name}.v1",
        effect_class=effect,
        idempotency=(
            IdempotencyClass.IDEMPOTENT
            if effect is EffectClass.READ_ONLY
            else IdempotencyClass.NON_IDEMPOTENT
        ),
        target_normalizer_version="v1",
    )


def _prepared(*specs: ToolSpec, confirm_only_names: tuple[str, ...] = ()) -> PreparedToolSet:
    capabilities = tuple(
        PreparedToolCapability(
            ToolCapabilityRef(
                capability_id=f"builtin:{spec.name}",
                name=spec.name,
                toolset=spec.toolset,
                source="builtin",
                description=spec.description_for_llm,
                schema_hash=spec.schema_hash,
            ),
            spec.schema,
        )
        for spec in specs
    )
    return PreparedToolSet.create(
        scope_id="scope",
        revision=1,
        registry_revision=1,
        direct=capabilities,
        deferred=(),
        activated=(),
        denied_names=(),
        policy_fingerprint="catalog-policy",
        decisions=(),
        confirm_only_names=confirm_only_names,
    )


@pytest.mark.parametrize("run_kind", ["foreground", "delegated_task"])
@pytest.mark.parametrize(
    "effect",
    [
        EffectClass.EXTERNAL_SEND,
        EffectClass.DESTRUCTIVE,
        EffectClass.PAYMENT,
        EffectClass.CREDENTIAL,
        EffectClass.PRIVACY,
        EffectClass.UNKNOWN,
    ],
)
def test_all_companion_owner_runs_force_risky_or_unknown_to_confirm_only(
    run_kind: str, effect: EffectClass
) -> None:
    safe = _spec("safe_read", EffectClass.READ_ONLY)
    risky = _spec("risky", effect)
    result = IrreversibleEffectPolicy().apply(
        _prepared(safe, risky),
        owner_key="companion:profile-a:7",
        run_kind=run_kind,
        specs=(safe, risky),
    )
    assert result.confirm_only_names == ("risky",)
    assert result.has_direct("safe_read")
    assert result.has_direct("risky")
    assert result.effect_policy_hash
    assert {item.name for item in result.effect_classifications} == {
        "safe_read",
        "risky",
    }


def test_policy_ors_existing_confirm_only_instead_of_downgrading() -> None:
    local = _spec("local", EffectClass.REVERSIBLE_LOCAL)
    result = IrreversibleEffectPolicy().apply(
        _prepared(local, confirm_only_names=("local",)),
        owner_key="companion:profile-a:7",
        run_kind="foreground",
        specs=(local,),
    )
    assert result.confirm_only_names == ("local",)


@pytest.mark.parametrize("run_kind", ["reflection", "evaluation"])
def test_reflection_and_evaluation_exclude_risky_tools(
    run_kind: str,
) -> None:
    read = _spec("read", EffectClass.READ_ONLY)
    send = _spec("send", EffectClass.EXTERNAL_SEND)
    result = IrreversibleEffectPolicy().apply(
        _prepared(read, send),
        owner_key="companion:profile-a:7",
        run_kind=run_kind,
        specs=(read, send),
    )
    assert result.has_direct("read")
    assert not result.has_direct("send")
    assert result.confirm_only_names == ()
    assert next(
        item for item in result.effect_classifications if item.name == "send"
    ).disposition == "excluded"


def test_confirm_only_policy_snapshot_round_trips_and_is_hash_covered() -> None:
    send = _spec("send", EffectClass.EXTERNAL_SEND)
    prepared = IrreversibleEffectPolicy().apply(
        _prepared(send),
        owner_key="companion:profile-a:7",
        run_kind="foreground",
        specs=(send,),
    )
    payload = dump_prepared_tool_set(prepared)
    restored = load_prepared_tool_set(payload)
    assert restored == prepared

    payload["confirm_only_names"] = []
    with pytest.raises(ValueError, match="schema fingerprint mismatch"):
        load_prepared_tool_set(payload)


def test_unknown_confirm_only_name_fails_closed() -> None:
    read = _spec("read", EffectClass.READ_ONLY)
    with pytest.raises(ValueError, match="unknown confirm-only"):
        _prepared(read, confirm_only_names=("not-visible",))


class _AuthorizationStore:
    async def get_policy_state(self) -> AuthorizationPolicyState:
        return AuthorizationPolicyState("auto", 3, 100.0)

    async def get_task_grant(self, _task_grant_id: str):
        return None


@pytest.mark.asyncio
async def test_driver_and_executor_both_enforce_confirm_only_in_auto(
    tmp_path: Path,
) -> None:
    risky = _spec("risky", EffectClass.EXTERNAL_SEND)
    prepared = IrreversibleEffectPolicy().apply(
        _prepared(risky),
        owner_key="companion:profile-a:7",
        run_kind="foreground",
        specs=(risky,),
    )
    scopes = ToolCapabilityScopeStore()
    scopes.open(
        prepared,
        ToolEligibilityContext("session-1", "request-1", "chat"),
    )

    class _Registry:
        capability_scope_store = scopes

        @staticmethod
        def prepared_execution_policy(_call):
            return False, True

        @staticmethod
        def resolve_prepared_spec(_call):
            return SimpleNamespace(permission_category="external_action")

    runtime = PreparedAuthorizationRuntime(
        _AuthorizationStore(), clock=lambda: 100.0
    )
    driver = ReActDriver(
        object(),
        None,  # type: ignore[arg-type]
        _Registry(),
        auto_mode_check=lambda: True,
        authorization_runtime=runtime,
        capability_scope_store=scopes,
    )
    target = tmp_path / "external-target"
    call = PreparedToolCall.prepare(
        tool_name="risky",
        stable_call_id="call-1",
        final_params={"target": str(target)},
        tool_spec_version="v1",
        schema_hash=risky.schema_hash,
        permission_policy_version="v1",
        effect_type="external_send",
        resource_selectors=(ResourceSelector.filesystem(target, "write"),),
    )
    context = ToolExecutionContext(
        scope_id=prepared.scope_id,
        session_id="session-1",
        request_id="request-1",
        root_run_id="run-1",
        run_id="run-1",
        call_id="call-1",
        effect_id="effect-1",
        capability_hash="c" * 64,
        scope_hash="d" * 64,
    )
    request = DriverStart(
        run_id="run-1",
        session_id="session-1",
        canonical_messages=(),
        tool_set_snapshot_ref="tool-set:run-1",
    )
    boundary = driver._boundary_for_batch(
        request,
        ReactToolBatch("command-1", (call,), (context,)),
    )

    assert boundary.authorization_indexes == (0,)
    assert boundary.confirm_only_names == ("risky",)
    decision = driver._permission_decision(boundary, 0)
    assert decision.prompt["authorization_origin"] == "explicit_decision"
    waiting = await driver._plan_permission(
        boundary, decision, confirmed=False
    )
    assert waiting.action == "wait"
    assert waiting.authorization_origin == "explicit_decision"

    command = ExecuteTools(
        "run-1",
        "command-1",
        (call,),
        (context,),
        (0,),
        effectful=(True,),
        confirm_only=(True,),
        confirm_only_snapshot_ref=boundary.confirm_only_snapshot_ref,
        confirm_only_snapshot_hash=boundary.confirm_only_snapshot_hash,
    )
    executor = EffectBatchExecutor(None, _Registry())  # type: ignore[arg-type]
    assert executor._validate_confirm_only_snapshot(command) == (True,)
