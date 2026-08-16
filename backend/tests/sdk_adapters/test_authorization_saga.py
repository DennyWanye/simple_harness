from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from dataclasses import replace

import aiosqlite
import pytest

from simple_harness import CallId, EffectId, RunId
from simple_harness.execution import EffectState, effect_request_hash
from simple_harness.execution.sqlite import Database, SqliteExecutionUnitOfWork
from simple_harness.execution.uow import DecisionState
from simple_harness.tools import (
    AuthorizationDecision,
    AuthorizationReceipt,
    AuthorizationRequest,
    AuthorizationResult,
    PreparedToolEffect,
    ReconciliationState,
    ToolCall,
    ToolResult,
    ToolSpec,
    bind_authorization_receipts,
    sdk_authorization_receipt,
)

from deskpet.sdk_adapters.authorization import ProductAuthorizationAdapter
from deskpet.sdk_adapters.reconciliation import ProductReconciliationAdapter

from deskpet.product_state.authorization_saga import (
    AuthorizationSagaIdentity,
    AuthorizationSagaRepository,
    AuthorizationSagaState,
    SagaConflict,
)
from deskpet.product_state.database import ProductStateDatabase
from deskpet.product_state.task_grants import (
    DurableTaskGrantAuthority,
    TaskGrantConflict,
)
from deskpet.types.task_grants import TaskGrant
from deskpet.capabilities.store import CapabilityStore, CapabilityStoreConflict


def identity(
    *, effect_id: str = "effect-1", decision_nonce: str = "nonce-1"
) -> AuthorizationSagaIdentity:
    return AuthorizationSagaIdentity(
        authorization_id="authorization-1",
        principal_id="owner-1",
        session_id="session-1",
        root_run_id="root-1",
        run_id="run-1",
        call_id="call-1",
        effect_id=effect_id,
        tool_name="write_file",
        arguments={"path": "note.txt"},
        capability_hash="a" * 64,
        schema_hash="b" * 64,
        scope_hash="c" * 64,
        grant_id="grant-1",
        grant_version=1,
        grant_fingerprint=grant().fingerprint,
        policy_generation=4,
        decision_nonce=decision_nonce,
        decision_version=0,
        run_lease_epoch=2,
        execution_lease_epoch=3,
    )


def receipt(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def grant(*, expires_at: float | None = 100.0) -> TaskGrant:
    return TaskGrant(
        task_grant_id="grant-1",
        root_run_id="root-1",
        principal_id="owner-1",
        resource_selectors=(),
        permission_categories=("filesystem.write",),
        effect_kinds=("write",),
        source="user",
        policy_generation=4,
        expires_at=expires_at,
        version=1,
    )


def set_policy_generation(database: ProductStateDatabase, generation: int = 4) -> None:
    database.connection.execute(
        "UPDATE authorization_policy_state SET generation=?,updated_at=? "
        "WHERE singleton_id=1",
        (generation, 1.0),
    )
    database.connection.commit()


def test_product_state_import_does_not_load_legacy_execution_authority() -> None:
    code = """
import json, sys
import deskpet.product_state
blocked = sorted(name for name in sys.modules if name.startswith(
    ('deskpet.workflows', 'deskpet.harness')
))
print(json.dumps(blocked))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(__file__.rsplit("/tests/", 1)[0]),
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == []


def test_database_first_open_reopen_and_failed_initialize_rolls_back(tmp_path) -> None:
    path = tmp_path / "data" / "product_state.db"
    database = ProductStateDatabase(path)
    database.initialize()
    set_policy_generation(database)
    assert database.schema_version == 1
    assert database.connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert database.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert database.connection.execute("PRAGMA user_version").fetchone()[0] == 1
    assert {
        "capability_versions",
        "authorization_policy_state",
        "task_grants",
        "authorization_sagas",
    }.issubset(database.table_names())
    database.close()

    reopened = ProductStateDatabase(path)
    reopened.initialize()
    assert reopened.schema_version == 1
    reopened.close()

    broken = ProductStateDatabase(tmp_path / "broken.db")
    with pytest.raises(RuntimeError, match="schema fault"):
        broken.initialize(fault=lambda point: (_ for _ in ()).throw(RuntimeError("schema fault")) if point == "schema.before_commit" else None)
    assert broken.table_names() == ()
    broken.close()


def test_partial_or_rogue_schema_never_reopens_ready(tmp_path) -> None:
    partial = ProductStateDatabase(tmp_path / "partial.db")
    partial.connection.execute("CREATE TABLE rogue(value TEXT)")
    partial.connection.commit()
    with pytest.raises(RuntimeError, match="partial|foreign"):
        partial.initialize()
    partial.close()

    path = tmp_path / "rogue-after-ready.db"
    ready = ProductStateDatabase(path)
    ready.initialize()
    ready.connection.execute("CREATE TABLE rogue(value TEXT)")
    ready.connection.commit()
    ready.close()
    reopened = ProductStateDatabase(path)
    with pytest.raises(RuntimeError, match="manifest"):
        reopened.initialize()
    reopened.close()


def test_saga_receipts_are_idempotent_and_reopen_to_settled(tmp_path) -> None:
    path = tmp_path / "product_state.db"
    database = ProductStateDatabase(path)
    database.initialize()
    repository = AuthorizationSagaRepository(database)
    prepared = repository.prepare(identity(), now=1.0)
    duplicate = repository.prepare(identity(), now=2.0)
    assert duplicate == prepared

    decision = repository.bind_decision(
        "authorization-1",
        expected_version=prepared.version,
        sdk_receipt_hash=receipt("sdk-decision"),
        host_receipt_hash=receipt("host-decision"),
        now=3.0,
    )
    effect = repository.bind_effect(
        "authorization-1",
        expected_version=decision.version,
        sdk_receipt_hash=receipt("sdk-effect"),
        host_receipt_hash=receipt("host-effect"),
        now=4.0,
    )
    handoff = repository.commit_handoff(
        "authorization-1",
        expected_version=effect.version,
        sdk_receipt_hash=receipt("sdk-handoff"),
        host_receipt_hash=receipt("host-handoff"),
        now=5.0,
    )
    duplicate_handoff = repository.commit_handoff(
        "authorization-1",
        expected_version=effect.version,
        sdk_receipt_hash=receipt("sdk-handoff"),
        host_receipt_hash=receipt("host-handoff"),
        now=6.0,
    )
    assert duplicate_handoff == handoff
    settled = repository.settle(
        "authorization-1",
        expected_version=handoff.version,
        outcome_hash=receipt("tool-result"),
        now=7.0,
    )
    assert settled.state is AuthorizationSagaState.SETTLED
    database.close()

    reopened = ProductStateDatabase(path)
    reopened.initialize()
    stored = AuthorizationSagaRepository(reopened).read("authorization-1")
    assert stored == settled
    reopened.close()


@pytest.mark.parametrize(
    ("method", "target_state"),
    [
        ("bind_decision", AuthorizationSagaState.DECISION_BOUND),
        ("bind_effect", AuthorizationSagaState.EFFECT_BOUND),
        ("commit_handoff", AuthorizationSagaState.HANDOFF_COMMITTED),
    ],
)
def test_each_receipt_write_rolls_back_on_crash(tmp_path, method, target_state) -> None:
    database = ProductStateDatabase(tmp_path / f"{method}.db")
    database.initialize()
    repository = AuthorizationSagaRepository(database)
    current = repository.prepare(identity(), now=1.0)
    if method in {"bind_effect", "commit_handoff"}:
        current = repository.bind_decision(
            "authorization-1",
            expected_version=current.version,
            sdk_receipt_hash=receipt("sdk-decision"),
            host_receipt_hash=receipt("host-decision"),
            now=2.0,
        )
    if method == "commit_handoff":
        current = repository.bind_effect(
            "authorization-1",
            expected_version=current.version,
            sdk_receipt_hash=receipt("sdk-effect"),
            host_receipt_hash=receipt("host-effect"),
            now=3.0,
        )
    prior = current

    kwargs = {
        "expected_version": prior.version,
        "sdk_receipt_hash": receipt(f"sdk-{method}"),
        "host_receipt_hash": receipt(f"host-{method}"),
        "now": 4.0,
        "fault": lambda point: (_ for _ in ()).throw(RuntimeError("crash")) if point.endswith("before_commit") else None,
    }
    with pytest.raises(RuntimeError, match="crash"):
        getattr(repository, method)("authorization-1", **kwargs)
    assert repository.read("authorization-1") == prior
    assert repository.read("authorization-1").state is not target_state
    database.close()


def test_wrong_owner_fence_and_receipt_conflict_quarantine(tmp_path) -> None:
    database = ProductStateDatabase(tmp_path / "fence.db")
    database.initialize()
    first = AuthorizationSagaRepository(database, owner_id="runtime-1")
    prepared = first.prepare(identity(), now=1.0)
    second = AuthorizationSagaRepository(database, owner_id="runtime-2")
    with pytest.raises(SagaConflict, match="owner"):
        second.prepare(identity(), now=1.5)
    with pytest.raises(SagaConflict, match="owner"):
        second.bind_decision(
            "authorization-1",
            expected_version=prepared.version,
            sdk_receipt_hash=receipt("sdk"),
            host_receipt_hash=receipt("host"),
            now=2.0,
        )

    first.bind_decision(
        "authorization-1",
        expected_version=prepared.version,
        sdk_receipt_hash=receipt("sdk"),
        host_receipt_hash=receipt("host"),
        now=3.0,
    )
    with pytest.raises(SagaConflict, match="receipt"):
        first.bind_decision(
            "authorization-1",
            expected_version=prepared.version,
            sdk_receipt_hash=receipt("different"),
            host_receipt_hash=receipt("host"),
            now=4.0,
        )
    assert first.read("authorization-1").state is AuthorizationSagaState.QUARANTINED
    database.close()


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("abort", AuthorizationSagaState.ABORTED),
        ("expire", AuthorizationSagaState.EXPIRED),
        ("revoke", AuthorizationSagaState.REVOKED),
    ],
)
def test_pre_handoff_terminal_transitions_are_durable_and_idempotent(
    tmp_path, operation, expected
) -> None:
    database = ProductStateDatabase(tmp_path / f"{operation}.db")
    database.initialize()
    repository = AuthorizationSagaRepository(database)
    prepared = repository.prepare(identity(), now=1.0)
    kwargs = {
        "expected_version": prepared.version,
        "reason_hash": receipt(operation),
        "now": 2.0,
    }
    terminal = getattr(repository, operation)("authorization-1", **kwargs)
    duplicate = getattr(repository, operation)("authorization-1", **kwargs)
    assert duplicate == terminal
    assert terminal.state is expected
    database.close()


def test_task_grant_policy_generation_expiry_and_reopen_are_fail_closed(tmp_path) -> None:
    path = tmp_path / "task-grant.db"
    database = ProductStateDatabase(path)
    database.initialize()
    set_policy_generation(database)
    authority = DurableTaskGrantAuthority(database)
    assert authority.prepare(grant(), now=1.0).status == "prepared"
    authority.activate("grant-1", version=1, policy_generation=4, now=2.0)
    assert authority.assert_active(
        "grant-1", version=1, policy_generation=4, now=3.0
    ).task_grant_id == "grant-1"
    with pytest.raises(TaskGrantConflict, match="generation"):
        authority.assert_active("grant-1", version=1, policy_generation=5, now=3.0)
    set_policy_generation(database, 5)
    with pytest.raises(TaskGrantConflict, match="not current"):
        authority.assert_active("grant-1", version=1, policy_generation=4, now=3.0)
    set_policy_generation(database, 4)
    database.close()

    reopened = ProductStateDatabase(path)
    reopened.initialize()
    recovered = DurableTaskGrantAuthority(reopened)
    with pytest.raises(TaskGrantConflict, match="expired"):
        recovered.assert_active("grant-1", version=1, policy_generation=4, now=101.0)
    assert recovered._read("grant-1").status == "expired"
    reopened.close()


def test_product_capability_store_rejects_foreign_transaction(tmp_path) -> None:
    async def case() -> None:
        database = ProductStateDatabase(tmp_path / "product-capability.db")
        database.initialize()
        store = CapabilityStore(database)
        await store.initialize()
        assert store.product_owned is True
        async with aiosqlite.connect(database.path) as foreign:
            with pytest.raises(CapabilityStoreConflict, match="product-state"):
                store.bind(foreign)
        async with store.write_transaction() as owned:
            assert store.bind(owned) is not None
        database.close()

    asyncio.run(case())


@pytest.mark.parametrize(
    ("operation", "saga_state", "grant_state"),
    [
        ("cancel", AuthorizationSagaState.ABORTED, "revoked"),
        ("expire", AuthorizationSagaState.EXPIRED, "expired"),
        ("revoke", AuthorizationSagaState.REVOKED, "revoked"),
    ],
)
def test_authorization_adapter_terminal_paths_revoke_physical_permission(
    tmp_path, operation, saga_state, grant_state
) -> None:
    async def case() -> None:
        database = ProductStateDatabase(tmp_path / f"adapter-{operation}.db")
        database.initialize()
        set_policy_generation(database)
        repository = AuthorizationSagaRepository(database)
        authority = DurableTaskGrantAuthority(database)
        prepared = PreparedToolEffect(
            EffectId("effect-1"),
            RunId("run-1"),
            ToolCall(CallId("call-1"), "write_file", {"path": "note.txt"}),
            ToolSpec(
                "write_file",
                "Write a file.",
                {"type": "object", "properties": {}, "additionalProperties": False},
            ),
            {},
        )
        adapter = ProductAuthorizationAdapter(
            repository,
            policy=lambda _effect: AuthorizationResult(
                AuthorizationDecision.REQUIRE_USER,
                reason_code="user_confirmation_required",
                request=AuthorizationRequest("Allow write?", "nonce-1"),
            ),
            identity_factory=lambda _prepared, _request: identity(),
            grant_authority=authority,
            grant_factory=lambda _prepared, _result: grant(),
            clock=lambda: 10.0,
        )
        await adapter.prepare(prepared)
        getattr(adapter, operation)(prepared, reason="test terminal")
        assert repository.read("authorization-1").state is saga_state
        assert authority._read("grant-1").status == grant_state
        database.close()

    asyncio.run(case())


def test_policy_deny_never_activates_task_grant(tmp_path) -> None:
    async def case() -> None:
        database = ProductStateDatabase(tmp_path / "policy-deny.db")
        database.initialize()
        set_policy_generation(database)
        repository = AuthorizationSagaRepository(database)
        authority = DurableTaskGrantAuthority(database)
        prepared = PreparedToolEffect(
            EffectId("effect-1"),
            RunId("run-1"),
            ToolCall(CallId("call-1"), "write_file", {}),
            ToolSpec(
                "write_file",
                "Write a file.",
                {"type": "object", "properties": {}, "additionalProperties": False},
            ),
            {},
        )
        adapter = ProductAuthorizationAdapter(
            repository,
            policy=lambda _effect: AuthorizationResult(
                AuthorizationDecision.DENY, reason_code="policy_denied"
            ),
            identity_factory=lambda _prepared, _request: identity(),
            grant_authority=authority,
            grant_factory=lambda _prepared, _result: grant(),
            clock=lambda: 10.0,
        )
        denied = await adapter.prepare(prepared)
        assert denied.decision is AuthorizationDecision.DENY
        assert repository.read("authorization-1").state is AuthorizationSagaState.ABORTED
        assert authority._read("grant-1").status == "revoked"
        database.close()

    asyncio.run(case())


def test_sdk_authorization_port_binds_both_product_receipts_before_handoff(
    tmp_path,
) -> None:
    async def case() -> None:
        database = ProductStateDatabase(tmp_path / "authorization-port.db")
        database.initialize()
        set_policy_generation(database)
        repository = AuthorizationSagaRepository(database)
        prepared = PreparedToolEffect(
            EffectId("effect-1"),
            RunId("run-1"),
            ToolCall(CallId("call-1"), "write_file", {"path": "note.txt"}),
            ToolSpec(
                "write_file",
                "Write a file.",
                {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            {},
        )

        def make_identity(_prepared, request):
            assert request is not None
            return identity(decision_nonce=request.nonce)

        adapter = ProductAuthorizationAdapter(
            repository,
            policy=lambda effect: AuthorizationResult(
                AuthorizationDecision.REQUIRE_USER,
                reason_code="user_confirmation_required",
                request=AuthorizationRequest("Allow write?", "host-nonce"),
            ),
            identity_factory=make_identity,
            grant_authority=DurableTaskGrantAuthority(database),
            grant_factory=lambda _prepared, _result: grant(),
            clock=lambda: 10.0,
        )
        pending = await adapter.prepare(prepared)
        assert pending.decision is AuthorizationDecision.REQUIRE_USER
        for forged in (
            replace(prepared, run_id=RunId("wrong-run")),
            replace(
                prepared,
                call=ToolCall(CallId("call-1"), "write_file", {"path": "other.txt"}),
            ),
        ):
            with pytest.raises(RuntimeError, match="differs"):
                await adapter.bind_decision(
                    forged,
                    AuthorizationRequest("Allow write?", "sdk-final-nonce"),
                    AuthorizationDecision.ALLOW,
                    AuthorizationReceipt(
                        "sdk:forged", receipt("forged"), receipt("forged")
                    ),
                )
        assert repository.read("authorization-1").state is AuthorizationSagaState.PREPARED
        assert DurableTaskGrantAuthority(database)._read("grant-1").status == "prepared"
        sdk_decision_hash = receipt("sdk-decision-port")
        final_request = AuthorizationRequest("Allow write?", "sdk-final-nonce")
        decision_receipt = await adapter.bind_decision(
            prepared,
            final_request,
            AuthorizationDecision.ALLOW,
            AuthorizationReceipt("sdk:decision", sdk_decision_hash, sdk_decision_hash),
        )
        assert decision_receipt.bound_sdk_receipt_hash == sdk_decision_hash
        with pytest.raises(RuntimeError, match="nonce"):
            await adapter.bind_decision(
                prepared,
                AuthorizationRequest("Allow write?", "wrong-nonce"),
                AuthorizationDecision.ALLOW,
                AuthorizationReceipt(
                    "sdk:wrong", receipt("wrong"), receipt("wrong")
                ),
            )
        sdk_handoff_hash = receipt("sdk-handoff-port")
        handoff_receipt = await adapter.bind_effect_handoff(
            prepared,
            decision_receipt.receipt_ref,
            AuthorizationReceipt("sdk:handoff", sdk_handoff_hash, sdk_handoff_hash),
        )
        assert handoff_receipt.bound_sdk_receipt_hash == sdk_handoff_hash
        stored = repository.read("authorization-1")
        assert stored is not None
        assert stored.state is AuthorizationSagaState.HANDOFF_COMMITTED
        assert stored.decision_sdk_receipt_hash == sdk_decision_hash
        assert stored.effect_sdk_receipt_hash == sdk_handoff_hash
        assert stored.handoff_sdk_receipt_hash == sdk_handoff_hash
        database.close()

    asyncio.run(case())


def test_authorization_product_write_crashes_reopen_and_complete_exact_receipts(
    tmp_path,
) -> None:
    async def case() -> None:
        path = tmp_path / "authorization-crash-matrix.db"
        prepared = PreparedToolEffect(
            EffectId("effect-1"),
            RunId("run-1"),
            ToolCall(CallId("call-1"), "write_file", {"path": "note.txt"}),
            ToolSpec(
                "write_file",
                "Write a file.",
                {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            {},
        )

        def policy(_effect):
            return AuthorizationResult(
                AuthorizationDecision.REQUIRE_USER,
                reason_code="user_confirmation_required",
                request=AuthorizationRequest("Allow write?", "host-nonce"),
            )

        def make_identity(_prepared, request):
            assert request is not None
            return identity(decision_nonce=request.nonce)

        def open_adapter(fail_at=None):
            database = ProductStateDatabase(path)
            database.initialize()
            set_policy_generation(database)

            def fault(point):
                if point == fail_at:
                    raise RuntimeError(f"crash:{point}")

            return database, ProductAuthorizationAdapter(
                AuthorizationSagaRepository(database),
                policy=policy,
                identity_factory=make_identity,
                grant_authority=DurableTaskGrantAuthority(database),
                grant_factory=lambda _prepared, _result: grant(),
                clock=lambda: 10.0,
                fault=fault,
            )

        database, adapter = open_adapter("after_product_prepare")
        with pytest.raises(RuntimeError, match="after_product_prepare"):
            await adapter.prepare(prepared)
        database.close()

        database, adapter = open_adapter()
        pending = await adapter.prepare(prepared)
        database.close()

        sdk_decision_hash = receipt("sdk-decision-crash")
        sdk_decision = AuthorizationReceipt(
            "sdk:decision", sdk_decision_hash, sdk_decision_hash
        )
        database, adapter = open_adapter("product_decision_bind")
        with pytest.raises(RuntimeError, match="product_decision_bind"):
            await adapter.bind_decision(
                prepared,
                pending.request,
                AuthorizationDecision.ALLOW,
                sdk_decision,
            )
        assert AuthorizationSagaRepository(database).read("authorization-1").state \
            is AuthorizationSagaState.DECISION_BOUND
        database.close()

        database, adapter = open_adapter()
        decision_receipt = await adapter.bind_decision(
            prepared,
            pending.request,
            AuthorizationDecision.ALLOW,
            sdk_decision,
        )
        database.close()

        sdk_handoff_hash = receipt("sdk-handoff-crash")
        sdk_handoff = AuthorizationReceipt(
            "sdk:handoff", sdk_handoff_hash, sdk_handoff_hash
        )
        database, adapter = open_adapter("product_effect_bind")
        with pytest.raises(RuntimeError, match="product_effect_bind"):
            await adapter.bind_effect_handoff(
                prepared, decision_receipt.receipt_ref, sdk_handoff
            )
        assert AuthorizationSagaRepository(database).read("authorization-1").state \
            is AuthorizationSagaState.EFFECT_BOUND
        database.close()

        database, adapter = open_adapter("product_handoff_commit")
        with pytest.raises(RuntimeError, match="product_handoff_commit"):
            await adapter.bind_effect_handoff(
                prepared, decision_receipt.receipt_ref, sdk_handoff
            )
        assert AuthorizationSagaRepository(database).read("authorization-1").state \
            is AuthorizationSagaState.HANDOFF_COMMITTED
        database.close()

        database, adapter = open_adapter()
        recovered = await adapter.bind_effect_handoff(
            prepared, decision_receipt.receipt_ref, sdk_handoff
        )
        stored = AuthorizationSagaRepository(database).read("authorization-1")
        assert recovered.bound_sdk_receipt_hash == sdk_handoff_hash
        assert stored is not None
        assert stored.state is AuthorizationSagaState.HANDOFF_COMMITTED
        assert stored.version == 3
        database.close()

        database, adapter = open_adapter("before_product_settle")
        with pytest.raises(RuntimeError, match="before_product_settle"):
            adapter.settle(prepared, outcome_hash=receipt("tool-outcome"))
        assert AuthorizationSagaRepository(database).read("authorization-1").state \
            is AuthorizationSagaState.HANDOFF_COMMITTED
        database.close()

        database, adapter = open_adapter()
        adapter.settle(prepared, outcome_hash=receipt("tool-outcome"))
        assert AuthorizationSagaRepository(database).read("authorization-1").state \
            is AuthorizationSagaState.SETTLED
        database.close()

    asyncio.run(case())


def test_handoff_then_revoke_remains_dispatch_unknown(tmp_path) -> None:
    database = ProductStateDatabase(tmp_path / "post-handoff-revoke.db")
    database.initialize()
    repository = AuthorizationSagaRepository(database)
    current = repository.prepare(identity(), now=1.0)
    effect = type("Effect", (), {"effect_id": EffectId("effect-1")})()
    before = asyncio.run(ProductReconciliationAdapter(repository).observe(effect))
    assert before.state is ReconciliationState.CONFIRMED_NOT_STARTED
    current = repository.bind_effect(
        "authorization-1",
        expected_version=current.version,
        sdk_receipt_hash=receipt("sdk-effect"),
        host_receipt_hash=receipt("host-effect"),
        now=2.0,
    )
    current = repository.commit_handoff(
        "authorization-1",
        expected_version=current.version,
        sdk_receipt_hash=receipt("sdk-handoff"),
        host_receipt_hash=receipt("host-handoff"),
        now=3.0,
    )
    revoked = repository.revoke(
        "authorization-1",
        expected_version=current.version,
        reason_hash=receipt("revoked"),
        now=4.0,
    )
    assert revoked.state is AuthorizationSagaState.DISPATCH_UNKNOWN
    after = asyncio.run(ProductReconciliationAdapter(repository).observe(effect))
    assert after.state is ReconciliationState.STILL_UNKNOWN
    database.close()


class _TwoDatabaseAuthorizationCase:
    """One deterministic effect driven through the real SDK and product UoWs."""

    def __init__(self, root) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.sdk_path = root / "sdk-execution.sqlite3"
        self.product_path = root / "product-state.db"
        self.physical_path = root / "physical-receipt.json"
        self.sdk_database = Database.open(self.sdk_path)
        self.sdk = SqliteExecutionUnitOfWork(self.sdk_database)
        self.product = ProductStateDatabase(self.product_path)
        self.product.initialize()
        set_policy_generation(self.product)
        self.prepared = PreparedToolEffect(
            EffectId("effect-1"),
            RunId("run-1"),
            ToolCall(CallId("call-1"), "write_file", {"path": "note.txt"}),
            ToolSpec(
                "write_file",
                "Write a file.",
                {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            {},
        )
        self.final_request = AuthorizationRequest("Allow write?", "sdk-final-nonce")
        if self.sdk.read_run("run-1") is None:
            self.sdk.create_with_start_snapshot(
                execution_session_id="session-1",
                run_id="run-1",
                request_id="request-1",
                profile_key="agent.general",
                driver_kind="react",
                snapshot={"schema_version": 1},
                event_id="run-created",
                now=1.0,
            )
            _, self.execution_lease = self.sdk.claim_runtime_activation(
                run_id="run-1",
                owner_id="runtime-1",
                namespace="runtime.kernel",
                now=2.0,
                lease_ttl_seconds=1000.0,
            )
            self.run_fence = None
        else:
            raise AssertionError("fresh matrix case unexpectedly existed")

    async def initialize(self) -> None:
        self.run_fence = await self.sdk.acquire(
            RunId("run-1"), self.execution_lease, now=2.0
        )

    def adapter(self, fail_at: str | None = None) -> ProductAuthorizationAdapter:
        def fault(point: str) -> None:
            if point == fail_at:
                raise RuntimeError(f"crash:{point}")

        return ProductAuthorizationAdapter(
            AuthorizationSagaRepository(self.product),
            policy=lambda _effect: AuthorizationResult(
                AuthorizationDecision.REQUIRE_USER,
                reason_code="user_confirmation_required",
                request=AuthorizationRequest("Allow write?", "host-suggested-nonce"),
            ),
            identity_factory=lambda _prepared, _request: identity(),
            grant_authority=DurableTaskGrantAuthority(self.product),
            grant_factory=lambda _prepared, _result: grant(),
            clock=lambda: 10.0,
            fault=fault,
        )

    @property
    def repository(self) -> AuthorizationSagaRepository:
        return AuthorizationSagaRepository(self.product)

    def close(self) -> None:
        self.sdk_database.close()
        self.product.close()

    def reopen(self) -> None:
        self.sdk_database = Database.open(self.sdk_path)
        self.sdk = SqliteExecutionUnitOfWork(self.sdk_database)
        self.product = ProductStateDatabase(self.product_path)
        self.product.initialize()

    def _sdk_decision_receipt(self):
        decision = self.sdk.read_decision("authorization:effect-1")
        assert decision is not None
        saga = self.repository.read("authorization-1")
        decision_version = (
            decision.version
            if saga is None or saga.bound_decision_version is None
            else saga.bound_decision_version
        )
        return sdk_authorization_receipt(
            "decision",
            {
                "decision": AuthorizationDecision.ALLOW.value,
                "decision_id": decision.decision_id,
                "decision_version": decision_version,
                "effect_id": "effect-1",
                "nonce": self.final_request.nonce,
                "run_id": "run-1",
            },
        )

    async def advance(self, stop_after: str | None = None) -> None:
        assert self.run_fence is not None
        adapter = self.adapter(
            stop_after
            if stop_after
            in {
                "after_product_prepare",
                "product_decision_bind",
                "product_effect_bind",
                "product_handoff_commit",
                "before_product_settle",
            }
            else None
        )
        saga = self.repository.read("authorization-1")
        if saga is None or saga.state is AuthorizationSagaState.PREPARED:
            try:
                await adapter.prepare(self.prepared)
            except RuntimeError as error:
                if stop_after == "after_product_prepare" and str(error).startswith("crash:"):
                    return
                raise
        if stop_after == "after_product_prepare":
            return

        decision_request = {
            "prompt": self.final_request.prompt,
            "nonce": self.final_request.nonce,
            "expires_at": None,
            "metadata": {},
            "effect_id": "effect-1",
            "call_id": "call-1",
            "tool_name": "write_file",
            "arguments": {"path": "note.txt"},
        }
        decision = self.sdk.read_decision("authorization:effect-1")
        if decision is None:
            decision = self.sdk.commit_decision(
                decision_id="authorization:effect-1",
                run_id="run-1",
                kind="tool_authorization",
                state=DecisionState.OPEN,
                request=decision_request,
                response=None,
                event_id="authorization-open",
                now=11.0,
            )
        if stop_after == "sdk_decision_bind":
            return

        sdk_decision_receipt = self._sdk_decision_receipt()
        try:
            host_decision_receipt = await adapter.bind_decision(
                self.prepared,
                self.final_request,
                AuthorizationDecision.ALLOW,
                sdk_decision_receipt,
            )
        except RuntimeError as error:
            if stop_after == "product_decision_bind" and str(error).startswith("crash:"):
                return
            raise
        decision_binding = bind_authorization_receipts(
            sdk_decision_receipt, host_decision_receipt
        )
        if stop_after == "product_decision_bind":
            return

        decision = self.sdk.read_decision("authorization:effect-1")
        assert decision is not None
        if decision.state is DecisionState.OPEN:
            decision = self.sdk.commit_decision(
                decision_id=decision.decision_id,
                run_id="run-1",
                kind=decision.kind,
                state=DecisionState.ALLOWED,
                request=decision_request,
                response={"authorization_receipt_ref": decision_binding},
                event_id="authorization-allowed",
                now=12.0,
            )
        if stop_after == "sdk_decision_consume":
            return

        effect = self.sdk.read_effect(EffectId("effect-1"))
        if effect is None:
            effect = self.sdk.prepare_effect(
                effect_id=EffectId("effect-1"),
                run_id=RunId("run-1"),
                call_id=CallId("call-1"),
                tool_name="write_file",
                arguments={"path": "note.txt"},
                request_hash=effect_request_hash(
                    tool_name="write_file", arguments={"path": "note.txt"}
                ),
                authorization_receipt_ref=decision_binding,
                run_fence=self.run_fence,
                execution_lease=self.execution_lease,
                now=13.0,
            )
        if stop_after == "sdk_effect_prepare":
            return

        if effect.state is EffectState.PREPARED:
            sdk_handoff_receipt = sdk_authorization_receipt(
                "effect-handoff",
                {
                    "authorization_receipt_ref": decision_binding,
                    "effect_id": "effect-1",
                    "effect_version": effect.version,
                    "fence_epoch": self.run_fence.epoch,
                    "run_id": "run-1",
                },
            )
            if stop_after == "sdk_handoff_intent":
                return
            try:
                host_handoff_receipt = await adapter.bind_effect_handoff(
                    self.prepared, decision_binding, sdk_handoff_receipt
                )
            except RuntimeError as error:
                if stop_after in {
                    "product_effect_bind",
                    "product_handoff_commit",
                } and str(error).startswith("crash:"):
                    return
                raise
            handoff_binding = bind_authorization_receipts(
                sdk_handoff_receipt, host_handoff_receipt
            )
            if stop_after in {"product_effect_bind", "product_handoff_commit"}:
                return
        else:
            assert effect.handoff_receipt_ref is not None
            handoff_binding = effect.handoff_receipt_ref

        effect = self.sdk.read_effect(EffectId("effect-1"))
        assert effect is not None
        if effect.state is EffectState.PREPARED:
            effect = self.sdk.mark_effect_handed_off(
                EffectId("effect-1"),
                expected_version=effect.version,
                run_fence=self.run_fence,
                handoff_receipt_ref=handoff_binding,
                execution_lease=self.execution_lease,
                now=14.0,
            )
        if stop_after == "sdk_handoff_commit":
            return

        if not self.physical_path.exists():
            self.physical_path.write_text(
                json.dumps({"calls": 1, "result": {"written": True}}),
                encoding="utf-8",
            )
        physical = json.loads(self.physical_path.read_text(encoding="utf-8"))
        assert physical["calls"] == 1
        result = ToolResult.succeeded(CallId("call-1"), physical["result"])
        if stop_after == "handler_return":
            return

        effect = self.sdk.read_effect(EffectId("effect-1"))
        assert effect is not None
        if effect.state in {EffectState.HANDED_OFF, EffectState.UNKNOWN}:
            effect = self.sdk.settle_effect(
                EffectId("effect-1"),
                expected_version=effect.version,
                expected_fence_epoch=effect.fence_epoch,
                result=result,
                evidence_ref="host-physical-receipt:effect-1",
                now=15.0,
            )
        if stop_after == "sdk_effect_settle":
            return

        outcome_hash = receipt("tool-outcome")
        try:
            adapter.settle(self.prepared, outcome_hash=outcome_hash)
        except RuntimeError as error:
            if stop_after == "before_product_settle" and str(error).startswith("crash:"):
                return
            raise


@pytest.mark.parametrize(
    "stop_after",
    [
        "after_product_prepare",
        "sdk_decision_bind",
        "product_decision_bind",
        "sdk_decision_consume",
        "sdk_effect_prepare",
        "sdk_handoff_intent",
        "product_effect_bind",
        "product_handoff_commit",
        "sdk_handoff_commit",
        "handler_return",
        "sdk_effect_settle",
        "before_product_settle",
    ],
)
@pytest.mark.asyncio
async def test_same_case_two_database_authorization_fault_matrix(
    tmp_path, stop_after
) -> None:
    case = _TwoDatabaseAuthorizationCase(tmp_path / stop_after)
    await case.initialize()
    assert case.sdk_path != case.product_path
    await case.advance(stop_after)
    calls_before = (
        0
        if not case.physical_path.exists()
        else json.loads(case.physical_path.read_text(encoding="utf-8"))["calls"]
    )
    if stop_after in {
        "after_product_prepare",
        "sdk_decision_bind",
        "product_decision_bind",
        "sdk_decision_consume",
        "sdk_effect_prepare",
        "sdk_handoff_intent",
        "product_effect_bind",
        "product_handoff_commit",
        "sdk_handoff_commit",
    }:
        assert calls_before == 0
    else:
        assert calls_before == 1
    case.close()

    case.reopen()
    await case.advance()
    sdk_effect = case.sdk.read_effect(EffectId("effect-1"))
    product_saga = case.repository.read("authorization-1")
    physical = json.loads(case.physical_path.read_text(encoding="utf-8"))
    assert sdk_effect is not None and sdk_effect.state is EffectState.SUCCEEDED
    assert product_saga is not None
    assert product_saga.state is AuthorizationSagaState.SETTLED
    assert product_saga.decision_sdk_receipt_hash is not None
    assert product_saga.decision_host_receipt_hash is not None
    assert product_saga.effect_sdk_receipt_hash is not None
    assert product_saga.effect_host_receipt_hash is not None
    assert product_saga.handoff_sdk_receipt_hash is not None
    assert product_saga.handoff_host_receipt_hash is not None
    assert sdk_effect.authorization_receipt_ref.startswith("authorization-binding-v1:")
    assert sdk_effect.handoff_receipt_ref is not None
    assert sdk_effect.handoff_receipt_ref.startswith("authorization-binding-v1:")
    assert physical["calls"] == 1
    assert calls_before <= physical["calls"]
    case.close()
