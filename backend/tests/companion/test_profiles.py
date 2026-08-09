from __future__ import annotations

import json
import asyncio

import pytest

from config import CompanionGrowthConfig, ConfigError, _load_config_impl
from context import ServiceContext
from deskpet.companion.contracts import OwnerRef
from deskpet.companion.identity import (
    HumanIdentity,
    ProfileBindingCoordinator,
    load_or_create_local_identity,
)
from deskpet.companion.identity_gate import (
    CompanionIdentityNotReady,
    FrozenOwnerIdentity,
    IdentityReadyGate,
)
from deskpet.companion.clock import SystemClock
from deskpet.companion.preferences import PreferencePolicy, PreferenceResolver
from deskpet.companion.runtime import CompanionRuntime, ForegroundActivityGate
from deskpet.companion.store import CompanionStore


def test_relay_identity_source_is_gone() -> None:
    """2026-08-09：托管登录（relay）身份来源已移除，只剩本地身份。

    以"缺席"形式钉住，防止 relay 命名空间被悄悄接回——它一旦回来，
    profile_id 就会随登录态在 relay_*／legacy_local_profile 之间跳变，
    重新推进 binding_epoch，也就重新打开 WBUI-DEF-COMP-01 的成因。
    """
    import deskpet.companion.identity as identity_mod

    assert not hasattr(identity_mod, "relay_human_identity")
    assert "relay_human_identity" not in identity_mod.__all__
    with pytest.raises(ValueError, match="unsupported identity namespace"):
        identity_mod._namespaced_hash("relay", "relay-user-123")


def test_local_identity_survives_restart(tmp_path) -> None:
    first = load_or_create_local_identity(tmp_path)
    second = load_or_create_local_identity(tmp_path)
    assert first == second
    assert first.profile_id == "legacy_local_profile"
    assert first.identity_kind == "local"
    raw = json.loads(
        (tmp_path / "companion-local-identity.json").read_text(encoding="utf-8")
    )
    assert raw["schema_version"] == 1


def test_identity_ready_gate_freezes_exact_owner_epoch() -> None:
    gate = IdentityReadyGate()
    with pytest.raises(CompanionIdentityNotReady) as exc_info:
        gate.freeze()
    assert exc_info.value.retryable is True
    frozen = FrozenOwnerIdentity(
        owner=OwnerRef("profile-a", 1),
        owner_key="companion:profile-a:1",
        binding_epoch=3,
    )
    gate.bind(frozen)
    assert gate.freeze() == frozen
    with pytest.raises(ValueError, match="binding_epoch_moved_backwards"):
        gate.bind(
            FrozenOwnerIdentity(
                owner=frozen.owner,
                owner_key=frozen.owner_key,
                binding_epoch=2,
            )
        )
    with pytest.raises(ValueError, match="binding_epoch_mismatch"):
        gate.unbind(expected_binding_epoch=2)
    gate.unbind(expected_binding_epoch=3)
    assert gate.ready is False


def test_identity_process_services_are_shared_across_session_contexts() -> None:
    gate = IdentityReadyGate()
    context = ServiceContext()
    context.register("companion_identity_gate", gate)
    session = context.create_session()
    assert session.companion_identity_gate is gate


def test_dormant_companion_services_are_process_owned_across_sessions(
    tmp_path,
) -> None:
    context = ServiceContext()
    clock = SystemClock()
    store = CompanionStore(tmp_path / "companion.db")
    resolver = PreferenceResolver(store, policy=PreferencePolicy())
    gate = IdentityReadyGate()

    async def handler(_owner, _claim):
        raise AssertionError("dormant handler must not run")

    runtime = CompanionRuntime(
        store=store,
        identity_gate=gate,
        foreground_gate=ForegroundActivityGate(None, clock=clock),
        handler=handler,
        clock=clock,
    )
    context.register("companion_preference_resolver_dormant", resolver)
    context.register("companion_clock", clock)
    context.register("companion_runtime", runtime)
    session = context.create_session()
    assert session.companion_preference_resolver_dormant is resolver
    assert session.companion_clock is clock
    assert session.companion_runtime is runtime


def test_growth_config_defaults_on_and_typed_nested_loading(tmp_path) -> None:
    defaults = CompanionGrowthConfig()
    assert defaults.enabled is True
    assert defaults.reflection_enabled is True
    assert defaults.low_risk_auto_activation is True
    assert defaults.proactive_enabled is True
    assert defaults.paused is False
    assert defaults.preference_promotion_independent_context_threshold == 3

    path = tmp_path / "config.toml"
    path.write_text(
        """
[companion]
memory_cross_session_decay = 0.25
capability_gate_enabled = false
write_scope_enforced = false

[companion.growth]
enabled = true
reflection_enabled = true
low_risk_auto_activation = true
proactive_enabled = true
preference_promotion_independent_context_threshold = 4
""",
        encoding="utf-8",
    )
    config = _load_config_impl(path)
    assert config.companion.memory_cross_session_decay == 0.25
    assert config.companion.capability_gate_enabled is False
    assert config.companion.write_scope_enforced is False
    assert config.companion.growth.preference_promotion_independent_context_threshold == 4

    path.write_text(
        "[companion.growth]\n"
        "preference_promotion_independent_context_threshold = 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="COMPANION-GROWTH-CONFIG-1"):
        _load_config_impl(path)


@pytest.mark.asyncio
async def test_profile_switch_ack_waits_for_projection_and_orders_a_b_a() -> None:
    events: list[str] = []
    ready = asyncio.Event()

    class Store:
        binding = None

        def get_profile_binding(self, *, device_scope):
            return dict(self.binding) if self.binding else None

        def bind_profile_with_control_command(
            self,
            *,
            profile_id,
            profile_generation,
            expected_binding_epoch,
            **_kwargs,
        ):
            previous = self.binding
            changed = (
                previous is None
                or previous["profile_id"] != profile_id
                or previous["profile_generation"] != profile_generation
            )
            epoch = (
                1
                if previous is None
                else previous["binding_epoch"] + (1 if changed else 0)
            )
            assert expected_binding_epoch == (
                1 if previous is None else previous["binding_epoch"]
            )
            self.binding = {
                "profile_id": profile_id,
                "profile_generation": profile_generation,
                "binding_epoch": epoch,
                "status": "unready" if changed else previous["status"],
            }
            return {"binding_epoch": epoch, "changed_owner": changed}

        def mark_profile_binding_ready(
            self, *, owner, expected_binding_epoch, **_kwargs
        ):
            assert owner.profile_id == self.binding["profile_id"]
            assert expected_binding_epoch == self.binding["binding_epoch"]
            self.binding["status"] = "ready"

    class Projection:
        async def close_owner_ingress(self, owner, *, binding_epoch):
            events.append(f"close:{owner.profile_id}:{binding_epoch}")

        async def cleanup_owner_runtime(self, owner, *, binding_epoch):
            events.append(f"cleanup:{owner.profile_id}:{binding_epoch}")

        async def activate_owner(self, owner, *, owner_key, binding_epoch):
            assert gate.ready is False
            assert owner_key == f"companion:{owner.profile_id}:1"
            events.append(f"activate-start:{owner.profile_id}:{binding_epoch}")
            if owner.profile_id == "b":
                await ready.wait()
            events.append(f"activate-ready:{owner.profile_id}:{binding_epoch}")
            return {"ready": True}

    store = Store()
    gate = IdentityReadyGate()
    coordinator = ProfileBindingCoordinator(
        store=store, gate=gate, projection=Projection()
    )
    await coordinator.bind(
        identity=HumanIdentity("a", "ha", "relay"),
        profile_generation=1,
        expected_binding_epoch=1,
        control_facts={},
    )
    switching = asyncio.create_task(
        coordinator.bind(
            identity=HumanIdentity("b", "hb", "relay"),
            profile_generation=1,
            expected_binding_epoch=1,
            control_facts={},
        )
    )
    await asyncio.sleep(0)
    assert switching.done() is False
    assert events[-1] == "activate-start:b:2"
    ready.set()
    bound_b = await switching
    assert bound_b.owner == OwnerRef("b", 1)
    await coordinator.bind(
        identity=HumanIdentity("a", "ha", "relay"),
        profile_generation=1,
        expected_binding_epoch=2,
        control_facts={},
    )
    assert events == [
        "activate-start:a:1",
        "activate-ready:a:1",
        "close:a:1",
        "cleanup:a:1",
        "activate-start:b:2",
        "activate-ready:b:2",
        "close:b:2",
        "cleanup:b:2",
        "activate-start:a:3",
        "activate-ready:a:3",
    ]
    assert gate.freeze().binding_epoch == 3


@pytest.mark.asyncio
async def test_first_process_bind_recovers_dormant_runtime_before_ready() -> None:
    owner = OwnerRef("persisted", 1)
    events: list[str] = []

    class Store:
        binding = {
            "profile_id": owner.profile_id,
            "profile_generation": owner.profile_generation,
            "binding_epoch": 7,
            "status": "ready",
        }

        def get_profile_binding(self, *, device_scope):
            return dict(self.binding)

        def bind_profile_with_control_command(self, **_kwargs):
            return {"binding_epoch": 7, "changed_owner": False}

        def mark_profile_binding_ready(self, **_kwargs):
            events.append("store-ready")

    class Projection:
        async def close_owner_ingress(self, owner, *, binding_epoch):
            events.append("projection-close")

        async def cleanup_owner_runtime(self, owner, *, binding_epoch):
            events.append("projection-cleanup")

        async def activate_owner(self, owner, *, owner_key, binding_epoch):
            assert gate.ready is False
            assert owner_key == "companion:persisted:1"
            events.append("projection-ready")
            return {"ready": True}

    class Runtime:
        async def pause(self, *, timeout=None):
            events.append("runtime-pause")

        async def recover(self, recovered_owner):
            assert gate.ready is False
            assert recovered_owner == owner
            events.append("runtime-recover")
            return {}

        async def start(self, started_owner):
            raise AssertionError("production binding must use prebound start")

        async def start_prebound(self, started_owner, *, binding_epoch):
            assert gate.ready is False
            assert started_owner == owner
            assert binding_epoch == 7
            events.append("runtime-start-prebound")
            return True

    gate = IdentityReadyGate()
    coordinator = ProfileBindingCoordinator(
        store=Store(),
        gate=gate,
        projection=Projection(),
        runtime=Runtime(),
        runtime_start_enabled=True,
    )
    frozen = await coordinator.bind(
        identity=HumanIdentity("persisted", "hash", "relay"),
        profile_generation=1,
        expected_binding_epoch=7,
        control_facts={},
    )
    assert frozen.owner_key == "companion:persisted:1"
    assert events == [
        "projection-cleanup",
        "projection-ready",
        "runtime-recover",
        "runtime-start-prebound",
        "store-ready",
    ]
    assert gate.freeze() == frozen


@pytest.mark.asyncio
async def test_same_process_same_owner_rebind_does_not_pause_runtime() -> None:
    pauses = 0

    class Runtime:
        async def pause(self, *, timeout=None):
            nonlocal pauses
            pauses += 1

        async def recover(self, owner):
            return {}

        async def start(self, owner):
            return True

    class Store:
        binding = None

        def get_profile_binding(self, *, device_scope):
            return None if self.binding is None else dict(self.binding)

        def bind_profile_with_control_command(
            self, *, profile_id, profile_generation, **_kwargs
        ):
            changed = self.binding is None
            self.binding = {
                "profile_id": profile_id,
                "profile_generation": profile_generation,
                "binding_epoch": 1,
                "status": "ready",
            }
            return {"binding_epoch": 1, "changed_owner": changed}

        def mark_profile_binding_ready(self, **_kwargs):
            return None

    gate = IdentityReadyGate()
    coordinator = ProfileBindingCoordinator(
        store=Store(),
        gate=gate,
        runtime=Runtime(),
    )
    identity = HumanIdentity("same", "hash", "relay")
    await coordinator.bind(
        identity=identity,
        profile_generation=1,
        expected_binding_epoch=1,
        control_facts={},
    )
    await coordinator.bind(
        identity=identity,
        profile_generation=1,
        expected_binding_epoch=1,
        control_facts={},
    )
    assert pauses == 0
    assert gate.freeze().owner_key == "companion:same:1"
