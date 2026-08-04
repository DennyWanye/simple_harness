from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from deskpet.capabilities.catalog_gate import (
    CapabilityCatalogGate,
    CatalogGateKey,
    CatalogReconcilingError,
)
from deskpet.capabilities.platform import (
    CapabilityPlatform,
    CurrentExecutionScopeFacts,
)
from deskpet.capabilities.execution_scope_authority import (
    SqliteCurrentExecutionScopeAuthority,
)
from deskpet.capabilities.runtime_prepare import PreparedRuntimeSet
from deskpet.capabilities.contracts import CapabilityScope
from deskpet.companion.authority import RevocationBarrier
from deskpet.companion.contracts import OwnerRef
from deskpet.companion.identity_gate import FrozenOwnerIdentity, IdentityReadyGate


@pytest.mark.asyncio
async def test_close_rejects_new_reads_and_waits_existing_short_reader() -> None:
    gate = CapabilityCatalogGate()
    exact = CatalogGateKey("owner-a", "user", "profile-a", "pack-a")
    wildcard = CatalogGateKey("owner-a", "user", "profile-a", "*")
    reader = gate.read((wildcard,))
    await reader.__aenter__()
    writer_entered = asyncio.Event()

    async def write():
        async with gate.writer((exact,)) as token:
            writer_entered.set()
        await gate.open_after_reconcile(
            token, committed_stamp="stamp-2", manager_receipt_hash="receipt-2"
        )

    task = asyncio.create_task(write())
    await asyncio.sleep(0)
    assert not writer_entered.is_set()
    with pytest.raises(CatalogReconcilingError):
        async with gate.read((exact,), timeout=0.1):
            pass

    await reader.__aexit__(None, None, None)
    await task
    assert writer_entered.is_set()
    assert gate.is_open(exact)


class FakeScopeAuthority:
    def __init__(self, facts) -> None:
        self.facts = facts
        self.calls = 0

    async def resolve_current_execution_scope(self, **kwargs):
        self.calls += 1
        return self.facts


def _lease_platform():
    owner = OwnerRef("profile-a", 3)
    identity = FrozenOwnerIdentity(owner, "owner-a", 7)
    gate = IdentityReadyGate()
    gate.bind(identity)
    facts = CurrentExecutionScopeFacts(
        owner_key="owner-a",
        profile_generation=3,
        binding_epoch=7,
        capability_hash="a" * 64,
        scope_hash="b" * 64,
        owner_binding_set_stamp="c" * 64,
        scope="user",
        scope_key="profile-a",
        pack_ids=("pack-a",),
    )
    platform = object.__new__(CapabilityPlatform)
    platform.identity_gate = gate
    platform.execution_scope_authority = FakeScopeAuthority(facts)
    platform.revocation_barrier = RevocationBarrier()
    platform.publish_lock = asyncio.Lock()
    platform.catalog_gate = CapabilityCatalogGate()
    return platform, identity, facts


def test_platform_execution_identity_gate_binds_once_without_replacement() -> None:
    platform = object.__new__(CapabilityPlatform)
    platform.identity_gate = None
    first = IdentityReadyGate()
    second = IdentityReadyGate()

    platform.bind_execution_identity_gate(first)
    platform.bind_execution_identity_gate(first)

    assert platform.identity_gate is first
    with pytest.raises(
        RuntimeError,
        match="execution_scope_identity_gate_already_bound",
    ):
        platform.bind_execution_identity_gate(second)
    assert platform.identity_gate is first


@pytest.mark.asyncio
async def test_sqlite_scope_authority_uses_durable_run_start_and_bound_catalog() -> None:
    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        async def fetchone(self):
            return self.rows[0] if self.rows else None

        async def fetchall(self):
            return self.rows

    class Db:
        async def execute(self, sql, _params):
            if "execution_run_start_snapshots" in sql:
                return Cursor(
                    [
                        {
                            "run_context_json": json.dumps(
                                {
                                    "owner_key": "owner-a",
                                    "profile_generation": 3,
                                    "binding_epoch": 7,
                                    "capability_hash": "a" * 64,
                                    "workspace": {"scope_hash": "b" * 64},
                                }
                            ),
                            "current_workspace_json": json.dumps(
                                {"scope_hash": "d" * 64}
                            ),
                            "run_catalog_content_stamp": "c" * 64,
                            "request_scope_canonical_json": json.dumps(
                                {"user_key": "owner-a"}
                            ),
                        }
                    ]
                )
            return Cursor([{"pack_id": "pack-a"}, {"pack_id": "pack-b"}])

    class Store:
        @asynccontextmanager
        async def read_connection(self):
            yield Db()

    facts = await SqliteCurrentExecutionScopeAuthority(
        Store()
    ).resolve_current_execution_scope(
        run_id="run-1",
        owner_key="owner-a",
        profile_generation=3,
        binding_epoch=7,
    )
    assert facts.capability_hash == "a" * 64
    assert facts.scope_hash == "d" * 64
    assert facts.owner_binding_set_stamp == "c" * 64
    assert facts.pack_ids == ("pack-a", "pack-b")


@pytest.mark.asyncio
async def test_platform_execution_scope_lease_holds_gate_until_release() -> None:
    platform, _, facts = _lease_platform()
    lease = await platform.acquire_current_execution_scope(
        owner_key=facts.owner_key,
        profile_generation=facts.profile_generation,
        binding_epoch=facts.binding_epoch,
        capability_hash=facts.capability_hash,
        scope_hash=facts.scope_hash,
    )
    writer_entered = asyncio.Event()

    async def writer():
        async with platform.catalog_gate.writer(
            (CatalogGateKey("owner-a", "user", "profile-a", "pack-a"),)
        ):
            writer_entered.set()

    task = asyncio.create_task(writer())
    await asyncio.sleep(0)
    assert not writer_entered.is_set()
    assert platform.publish_lock.locked()
    assert lease.owner_binding_set_stamp == "c" * 64

    await lease.release()
    assert not platform.publish_lock.locked()
    await task
    assert writer_entered.is_set()
    await lease.release()


@pytest.mark.asyncio
async def test_platform_execution_scope_rejects_stale_hash_and_identity() -> None:
    platform, identity, facts = _lease_platform()
    with pytest.raises(RuntimeError, match="execution_scope_fingerprint_stale"):
        await platform.acquire_current_execution_scope(
            owner_key=facts.owner_key,
            profile_generation=facts.profile_generation,
            binding_epoch=facts.binding_epoch,
            capability_hash="d" * 64,
            scope_hash=facts.scope_hash,
        )

    platform.identity_gate.unbind(expected_binding_epoch=identity.binding_epoch)
    with pytest.raises(RuntimeError, match="execution_scope_identity_not_ready"):
        await platform.acquire_current_execution_scope(
            owner_key=facts.owner_key,
            profile_generation=facts.profile_generation,
            binding_epoch=facts.binding_epoch,
            capability_hash=facts.capability_hash,
            scope_hash=facts.scope_hash,
        )


@pytest.mark.asyncio
async def test_runtime_projection_opens_gate_only_after_matching_receipt() -> None:
    gate = CapabilityCatalogGate()
    key = CatalogGateKey("owner-a", "user", "profile-a", "*")

    class Commit:
        async def activate_owner_runtime_projection(self, prepared_set):
            assert not gate.is_open(key)
            return {
                "committed_owner_binding_set_stamp": "stamp-1",
                "manager_receipt_set_hash": "receipt-1",
            }

    platform = object.__new__(CapabilityPlatform)
    platform.runtime_projection_commit = Commit()
    platform.publish_lock = asyncio.Lock()
    platform.catalog_gate = gate
    class Ledger:
        def __init__(self):
            self.calls = []

        async def mark_set_activated(self, operation_id, runtime_set_hash):
            assert not gate.is_open(key)
            self.calls.append((operation_id, runtime_set_hash))

    ledger = Ledger()
    platform.runtime_set_ledger = ledger
    prepared = PreparedRuntimeSet(
        operation_id="operation-1",
        purpose="owner_rehydrate",
        owner_key="owner-a",
        scope="user",
        scope_key="profile-a",
        owner_runtime_activation_generation=1,
        owner_binding_set_stamp="stamp-1",
        launch_revocation_epoch=2,
        instances=(),
    )

    receipt = await platform.activate_prepared_projection(prepared)

    assert receipt["manager_receipt_set_hash"] == "receipt-1"
    assert ledger.calls == [
        (prepared.operation_id, prepared.runtime_set_hash)
    ]
    assert gate.is_open(key)


@pytest.mark.asyncio
async def test_runtime_projection_mismatch_leaves_catalog_closed() -> None:
    gate = CapabilityCatalogGate()
    key = CatalogGateKey("owner-a", "user", "profile-a", "*")

    class Commit:
        async def activate_owner_runtime_projection(self, prepared_set):
            return {
                "committed_owner_binding_set_stamp": "wrong",
                "manager_receipt_set_hash": "receipt-1",
            }

    platform = object.__new__(CapabilityPlatform)
    platform.runtime_projection_commit = Commit()
    platform.publish_lock = asyncio.Lock()
    platform.catalog_gate = gate
    prepared = PreparedRuntimeSet(
        operation_id="operation-1",
        purpose="owner_rehydrate",
        owner_key="owner-a",
        scope="user",
        scope_key="profile-a",
        owner_runtime_activation_generation=1,
        owner_binding_set_stamp="stamp-1",
        launch_revocation_epoch=2,
        instances=(),
    )

    with pytest.raises(RuntimeError, match="owner_runtime_binding_stamp_mismatch"):
        await platform.activate_prepared_projection(prepared)
    assert not gate.is_open(key)


@pytest.mark.asyncio
async def test_prepare_run_catalog_lease_captures_hub_exactly_once() -> None:
    gate = CapabilityCatalogGate()
    key = CatalogGateKey("owner-a", "user", "profile-a", "*")
    calls = []

    class Hub:
        def _gate_keys(self, scope, owner_key):
            return (key,)

        async def _snapshot_locked(self, scope):
            calls.append("snapshot")
            return "snapshot-1", ("entry-1",)

    class Preparer:
        async def prepare_captured_run_catalog(self, **kwargs):
            calls.append("persist")
            assert kwargs["snapshot"] == "snapshot-1"
            assert kwargs["selected_entries"] == ("entry-1",)
            return "prepared-lease"

    platform = object.__new__(CapabilityPlatform)
    platform.publish_lock = asyncio.Lock()
    platform.catalog_gate = gate
    platform.hub = Hub()
    platform.run_catalog_lease_preparer = Preparer()
    platform.snapshot_lease_ready_gate = object()

    result = await platform.prepare_run_catalog_lease(
        scope=CapabilityScope(user_key="profile-a"),
        owner_key="owner-a",
        prepared_tool_set=object(),
        prepared_tool_set_fingerprint="a" * 64,
        run_id="run-1",
        root_run_id="root-1",
        request_id="request-1",
        turn_id="turn-1",
        owner_operation_id="run-start:run-1:1",
    )

    assert result == "prepared-lease"
    assert calls == ["snapshot", "persist"]
