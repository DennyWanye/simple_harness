from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import zipfile
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.capabilities.contracts import CapabilityBinding
from deskpet.capabilities.manager import (
    CapabilityManagerError,
    capability_operation_id,
)
from deskpet.capabilities.platform import (
    CapabilityLifecycleStaticRequest,
    CapabilityPlatform,
    PreparedCapabilityLifecycleMutation,
)
from deskpet.capabilities.runtime_prepare import (
    PreparedRuntimeInstanceSpec,
    PreparedRuntimeSet,
    RuntimeHealthOutcome,
    RuntimeStartedAck,
)
from deskpet.companion.activation import (
    ActivationDispatchError,
    CapabilityMutationAuthorization,
)
from deskpet.companion.activation_platform import (
    CapabilityManagerStaticLifecyclePort,
    CompanionCapabilityMutationPlatform,
    CompanionStoreActivationDecisionProofResolver,
    CompanionStoreLifecycleCandidateSourceResolver,
    TrustedLifecycleCandidateSourceV1,
    TrustedActivationDecisionProofV1,
)
from deskpet.companion.contracts import CandidateMode, MutationAction
from deskpet.companion.store import canonical_hash
from deskpet.capabilities.source import PackSourceRequest


def _authorization(
    *,
    action: MutationAction = MutationAction.INSTALL,
    executable: bool = True,
) -> CapabilityMutationAuthorization:
    candidate = action in {MutationAction.INSTALL, MutationAction.UPDATE}
    return CapabilityMutationAuthorization(
        request_id="request-1",
        request_fingerprint="request-fingerprint-1",
        action=action,
        target_owner_key="companion:alice:1",
        target_scope="user",
        target_scope_key="profile",
        pack_id="personal.summarize-day",
        manager_idempotency_key=f"manager:{action.value}:request-1",
        target_expected_binding_generation=2,
        claim_owner="activation-worker",
        claim_epoch=1,
        revocation_epoch=7,
        candidate_id="candidate-1" if candidate else None,
        candidate_mode=CandidateMode.UPDATE if candidate else None,
        target_version=(
            "1.0.2"
            if candidate or action is MutationAction.ROLLBACK
            else None
        ),
        target_manifest_hash=(
            "manifest-1"
            if candidate or action is MutationAction.ROLLBACK
            else None
        ),
        target_package_hash="package-1" if candidate else None,
        target_archive_hash="archive-1" if candidate else None,
        report_id="report-1" if candidate else None,
        risk_id="risk-1" if candidate else None,
        decision_id="decision-1" if candidate else None,
        risk_ack=(
            "persistent_local_code_no_os_sandbox" if executable else "none"
        ),
        rollback_kind=(
            "same_owner_version"
            if action is MutationAction.ROLLBACK
            else None
        ),
    )


def _proof(
    authorization: CapabilityMutationAuthorization,
    *,
    executable: bool = True,
    code_digest: str | None = "code-digest-1",
    decision_mode: str = "user_confirmed",
) -> TrustedActivationDecisionProofV1:
    return TrustedActivationDecisionProofV1.issue(
        authorization_hash=authorization.authorization_hash,
        decision_id=str(authorization.decision_id),
        candidate_id=str(authorization.candidate_id),
        report_id=str(authorization.report_id),
        risk_id=str(authorization.risk_id),
        package_hash=str(authorization.target_package_hash),
        executable=executable,
        decision_mode=decision_mode,  # type: ignore[arg-type]
        code_digest=code_digest,
        activation_risk_ack=(
            "persistent_local_code_no_os_sandbox" if executable else "none"
        ),
    )


class _Proofs:
    def __init__(self, proof=None, *, auto_mode: bool = False) -> None:
        self.proof = proof
        self.auto_mode = auto_mode
        self.calls = 0

    async def resolve_activation_decision(self, authorization):
        del authorization
        self.calls += 1
        return self.proof


class _StaticPlatform:
    def __init__(self, *, with_runtime: bool = True) -> None:
        self.with_runtime = with_runtime
        self.trace = []

    async def prepare_lifecycle_mutation_static(self, request):
        self.trace.append(("prepare_static", request.action))
        runtime_set = None
        runtime_ref = None
        if self.with_runtime:
            spec = PreparedRuntimeInstanceSpec(
                entry_id="tool:daily-summary",
                runtime_kind="local_process",
                adapter_id="local-process-v1",
                adapter_fingerprint="adapter-build-1",
                start_envelope={
                    "argv": ["python", "worker.py"],
                    "env_scope": {"candidate": request.target_package_hash},
                    "workdir": "candidate",
                },
                health_envelope={"probe": "ready-v1"},
                ordinal=0,
            )
            runtime_set = PreparedRuntimeSet(
                operation_id="manager-operation-1",
                purpose=f"lifecycle_{request.action}",
                owner_key=request.owner_key,
                scope=request.scope,
                scope_key=request.scope_key,
                owner_runtime_activation_generation=3,
                owner_binding_set_stamp="owner-stamp-before",
                launch_revocation_epoch=request.launch_revocation_epoch,
                instances=(spec,),
            )
            runtime_ref = f"runtime-set:{runtime_set.runtime_set_hash}"
        return PreparedCapabilityLifecycleMutation(
            manager_operation_id="manager-operation-1",
            request=request,
            runtime_set_ref=runtime_ref,
            runtime_set=runtime_set,
        )

    async def start_prepared_runtime_instance(
        self,
        prepared_set,
        instance,
        authorization,
    ):
        authorization.validate(prepared_set, instance)
        self.trace.append(("start", instance.entry_id))
        return RuntimeStartedAck(
            operation_id=prepared_set.operation_id,
            runtime_set_hash=prepared_set.runtime_set_hash,
            entry_id=instance.entry_id,
            runtime_instance_id=authorization.runtime_instance_id,
            adapter_identity="local-process-v1@adapter-build-1",
            start_identity={"job": "job-1", "pid": 123},
            acknowledged_at=time.time(),
        )

    async def await_prepared_runtime_instance_health(
        self, prepared_set, instance
    ):
        self.trace.append(("health", instance.entry_id))
        return RuntimeHealthOutcome(
            operation_id=prepared_set.operation_id,
            entry_id=instance.entry_id,
            healthy=True,
            outcome_hash="health-outcome-1",
        )

    async def activate_prepared_set(self, prepared_set):
        self.trace.append(("activate_set", prepared_set.runtime_set_hash))
        return {
            "manager_operation_id": prepared_set.operation_id,
            "action": "install",
            "pack_id": "personal.summarize-day",
            "manager_receipt_set_hash": "manager-receipt-1",
            "version": "1.0.2",
            "manifest_hash": "manifest-1",
            "binding_generation": 3,
            "committed_owner_binding_set_stamp": "owner-stamp-after",
            "process_projection_fingerprint": "process-projection-1",
        }

    async def activate_empty_lifecycle_mutation(self, prepared):
        self.trace.append(("activate_empty", prepared.request.action))
        return {
            "manager_operation_id": prepared.manager_operation_id,
            "action": prepared.request.action,
            "pack_id": prepared.request.pack_id,
            "result_hash": "manager-removal-receipt-1",
            "binding_generation": 3,
            "committed_owner_binding_set_stamp": "owner-stamp-after",
        }

    async def abort_prepared_set(self, prepared_set, *, reason_code):
        self.trace.append(("abort_set", prepared_set.runtime_set_hash, reason_code))

    async def abort_lifecycle_mutation_static(
        self, prepared, *, reason_code
    ):
        self.trace.append(("abort_static", prepared.manager_operation_id, reason_code))
        return True


@pytest.mark.asyncio
async def test_missing_activation_confirmation_rejects_before_static_prepare() -> None:
    platform = _StaticPlatform()
    facade = CompanionCapabilityMutationPlatform(
        platform=platform,  # type: ignore[arg-type]
        decision_proofs=_Proofs(),
    )

    with pytest.raises(ActivationDispatchError) as exc:
        await facade.prepare_mutation(
            _authorization(),
            persisted_operation_id=None,
            persisted_runtime_set_ref=None,
            persisted_runtime_set_hash=None,
        )

    assert exc.value.reason_code == "activation_confirmation_required"
    assert platform.trace == []


@pytest.mark.asyncio
async def test_auto_mode_never_substitutes_for_exact_activation_decision() -> None:
    platform = _StaticPlatform()
    proofs = _Proofs(auto_mode=True)
    facade = CompanionCapabilityMutationPlatform(
        platform=platform,  # type: ignore[arg-type]
        decision_proofs=proofs,
    )

    with pytest.raises(ActivationDispatchError) as exc:
        await facade.prepare_mutation(
            _authorization(),
            persisted_operation_id=None,
            persisted_runtime_set_ref=None,
            persisted_runtime_set_hash=None,
        )

    assert exc.value.reason_code == "activation_confirmation_required"
    assert proofs.auto_mode is True
    assert platform.trace == []


@pytest.mark.asyncio
async def test_executable_confirmation_requires_exact_code_digest_before_prepare() -> None:
    authorization = _authorization()
    platform = _StaticPlatform()
    facade = CompanionCapabilityMutationPlatform(
        platform=platform,  # type: ignore[arg-type]
        decision_proofs=_Proofs(
            _proof(authorization, executable=True, code_digest=None)
        ),
    )

    with pytest.raises(ActivationDispatchError) as exc:
        await facade.prepare_mutation(
            authorization,
            persisted_operation_id=None,
            persisted_runtime_set_ref=None,
            persisted_runtime_set_hash=None,
        )

    assert (
        exc.value.reason_code
        == "executable_activation_confirmation_incomplete"
    )
    assert platform.trace == []


@pytest.mark.asyncio
async def test_runtime_lifecycle_reuses_platform_start_health_and_activate() -> None:
    authorization = _authorization()
    platform = _StaticPlatform()
    facade = CompanionCapabilityMutationPlatform(
        platform=platform,  # type: ignore[arg-type]
        decision_proofs=_Proofs(_proof(authorization)),
    )

    prepared = await facade.prepare_mutation(
        authorization,
        persisted_operation_id=None,
        persisted_runtime_set_ref=None,
        persisted_runtime_set_hash=None,
    )
    assert len(prepared.instances) == 1

    started = await facade.start_runtime_instance(
        authorization, prepared, prepared.instances[0]
    )
    assert started.outcome == "started"
    assert await facade.await_runtime_instance_health(
        authorization, prepared, prepared.instances[0], started
    )
    receipt = await facade.activate_mutation(authorization, prepared)

    assert receipt.activation_request_id == authorization.request_id
    assert receipt.manager_operation_id == "manager-operation-1"
    assert receipt.action is MutationAction.INSTALL
    assert receipt.runtime_set_hash == prepared.runtime_set_hash
    assert receipt.result_hash == "manager-receipt-1"
    assert [item[0] for item in platform.trace] == [
        "prepare_static",
        "start",
        "health",
        "activate_set",
    ]


@pytest.mark.asyncio
async def test_disable_uses_only_explicit_empty_static_path() -> None:
    authorization = _authorization(action=MutationAction.DISABLE)
    platform = _StaticPlatform(with_runtime=False)
    facade = CompanionCapabilityMutationPlatform(
        platform=platform,  # type: ignore[arg-type]
        decision_proofs=_Proofs(),
    )

    prepared = await facade.prepare_mutation(
        authorization,
        persisted_operation_id=None,
        persisted_runtime_set_ref=None,
        persisted_runtime_set_hash=None,
    )
    assert prepared.instances == ()
    assert prepared.runtime_set_hash is None

    receipt = await facade.activate_mutation(authorization, prepared)
    assert receipt.action is MutationAction.DISABLE
    assert receipt.result_hash == "manager-removal-receipt-1"
    assert platform.trace == [
        ("prepare_static", "disable"),
        ("activate_empty", "disable"),
    ]


@pytest.mark.asyncio
async def test_current_platform_fails_closed_without_static_manager_api() -> None:
    platform = object.__new__(CapabilityPlatform)
    platform.lifecycle_static_port = None
    request = CapabilityLifecycleStaticRequest(
        authorization_hash="auth-1",
        action="disable",
        owner_key="companion:alice:1",
        scope="user",
        scope_key="profile",
        pack_id="personal.summarize-day",
        manager_idempotency_key="manager:disable:request-1",
        expected_binding_generation=2,
        launch_revocation_epoch=7,
    )

    with pytest.raises(CapabilityManagerError) as exc:
        await platform.prepare_lifecycle_mutation_static(request)

    assert exc.value.code == "static_lifecycle_prepare_unavailable"


class _CandidateSources:
    def __init__(self, source: TrustedLifecycleCandidateSourceV1) -> None:
        self.source = source
        self.calls = 0

    async def resolve_candidate_source(self, request):
        self.calls += 1
        self.source.validate(request)
        return self.source


class _Manager:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = []

    async def update(self, source, **kwargs):
        self.calls.append(("update", source, kwargs))
        return self.result

    async def install(self, source, **kwargs):
        self.calls.append(("install", source, kwargs))
        return self.result


def _lifecycle_request() -> CapabilityLifecycleStaticRequest:
    return CapabilityLifecycleStaticRequest(
        authorization_hash="auth-1",
        action="update",
        owner_key="companion:alice:1",
        scope="user",
        scope_key="profile",
        pack_id="summarize-day",
        manager_idempotency_key="manager:update:request-1",
        expected_binding_generation=2,
        launch_revocation_epoch=7,
        candidate_id="candidate-1",
        candidate_mode="update",
        target_version="1.0.2",
        target_manifest_hash="a" * 64,
        target_package_hash="b" * 64,
        target_archive_hash="c" * 64,
    )


def _lifecycle_source(
    request: CapabilityLifecycleStaticRequest,
    *,
    executable: bool = False,
) -> TrustedLifecycleCandidateSourceV1:
    source = PackSourceRequest(
        source_type="companion_growth",
        uri=str(Path("candidate.zip").resolve()),
        revision=str(request.target_version),
    )
    payload = {
        "schema_version": 1,
        "candidate_id": request.candidate_id,
        "pack_id": request.pack_id,
        "version": request.target_version,
        "manifest_hash": request.target_manifest_hash,
        "package_hash": request.target_package_hash,
        "archive_hash": request.target_archive_hash,
        "executable": executable,
        "source_type": source.source_type,
        "source_uri": source.uri,
        "source_revision": source.revision,
    }
    return TrustedLifecycleCandidateSourceV1(
        candidate_id=str(request.candidate_id),
        pack_id=request.pack_id,
        version=str(request.target_version),
        manifest_hash=str(request.target_manifest_hash),
        package_hash=str(request.target_package_hash),
        archive_hash=str(request.target_archive_hash),
        executable=executable,
        source=source,
        source_hash=canonical_hash(payload),
    )


@pytest.mark.asyncio
async def test_manager_static_port_preserves_owner_generation_and_receipt() -> None:
    request = _lifecycle_request()
    operation_id = capability_operation_id(request.manager_idempotency_key)
    result = SimpleNamespace(
        operation=SimpleNamespace(
            operation_id=operation_id,
            status="succeeded",
            phase="published",
        ),
        binding=CapabilityBinding(
            binding_id="binding-1",
            capability_id=request.pack_id,
            version=str(request.target_version),
            manifest_hash=str(request.target_manifest_hash),
            scope="user",
            scope_key=request.scope_key,
            active=True,
            generation=3,
            owner_key=request.owner_key,
            management_policy="user_managed",
            management_generation=1,
        ),
        manager_receipt_hash="d" * 64,
        committed_owner_binding_set_stamp="e" * 64,
        process_projection_fingerprint="f" * 64,
    )
    manager = _Manager(result)
    port = CapabilityManagerStaticLifecyclePort(
        manager=manager,  # type: ignore[arg-type]
        candidate_sources=_CandidateSources(_lifecycle_source(request)),
    )

    prepared = await port.prepare_static(request)
    receipt = await port.activate_empty(prepared)

    assert receipt["manager_operation_id"] == operation_id
    assert receipt["result_hash"] == "d" * 64
    assert receipt["binding_generation"] == 3
    assert len(manager.calls) == 1
    action, _source, kwargs = manager.calls[0]
    assert action == "update"
    assert kwargs["owner_key"] == "companion:alice:1"
    assert kwargs["expected_binding_generation"] == 2
    assert kwargs["management_policy"] == "user_managed"


@pytest.mark.asyncio
async def test_manager_static_port_rejects_executable_before_manager_call() -> None:
    request = _lifecycle_request()
    manager = _Manager(None)
    port = CapabilityManagerStaticLifecyclePort(
        manager=manager,  # type: ignore[arg-type]
        candidate_sources=_CandidateSources(
            _lifecycle_source(request, executable=True)
        ),
    )

    with pytest.raises(CapabilityManagerError) as exc:
        await port.prepare_static(request)

    assert exc.value.code == "static_lifecycle_runtime_prepare_required"
    assert manager.calls == []


@pytest.mark.asyncio
async def test_manager_static_port_remove_override_proves_exact_fallback(
    monkeypatch,
) -> None:
    user_binding = CapabilityBinding(
        binding_id="user-binding",
        capability_id="summarize-day",
        version="1.0.1",
        manifest_hash="a" * 64,
        scope="user",
        scope_key="profile",
        active=True,
        generation=3,
        owner_key="companion:alice:1",
        management_policy="user_managed",
        management_generation=1,
    )
    fallback_binding = CapabilityBinding(
        binding_id="builtin-binding",
        capability_id="summarize-day",
        version="1.0.0",
        manifest_hash="b" * 64,
        scope="builtin",
        scope_key="builtin",
        active=True,
        generation=1,
        owner_key="builtin",
        management_policy="host_managed",
        management_generation=1,
    )

    class _RemoveOverrideStore:
        def __init__(self):
            self.user = user_binding

        async def get_binding(
            self, scope, scope_key, pack_id, *, owner_key=None
        ):
            assert pack_id == "summarize-day"
            if owner_key == "builtin":
                return fallback_binding
            if owner_key == "companion:alice:1":
                return self.user
            return None

        async def get_version(self, pack_id, version, manifest_hash):
            assert (pack_id, version, manifest_hash) == (
                "summarize-day",
                "1.0.0",
                "b" * 64,
            )
            return SimpleNamespace(install_path=Path("builtin-pack"))

        async def read_detail_token_vector(self, keys):
            assert keys[0].owner_key == "builtin"
            return SimpleNamespace(
                items=(
                    SimpleNamespace(
                        committed_owner_binding_set_stamp="c" * 64
                    ),
                )
            )

    class _RemoveOverrideManager:
        def __init__(self):
            self.store = _RemoveOverrideStore()
            self.environment = SimpleNamespace()
            self.calls = []

        async def uninstall(self, **kwargs):
            self.calls.append(kwargs)
            self.store.user = CapabilityBinding(
                binding_id=user_binding.binding_id,
                capability_id=user_binding.capability_id,
                version=user_binding.version,
                manifest_hash=user_binding.manifest_hash,
                scope=user_binding.scope,
                scope_key=user_binding.scope_key,
                active=False,
                generation=4,
                owner_key=user_binding.owner_key,
                management_policy=user_binding.management_policy,
                management_generation=2,
            )
            return SimpleNamespace(
                operation=SimpleNamespace(
                    operation_id=capability_operation_id(
                        kwargs["idempotency_key"]
                    ),
                    status="succeeded",
                    phase="published",
                ),
                binding=self.store.user,
                manager_receipt_hash="d" * 64,
                committed_owner_binding_set_stamp="e" * 64,
                process_projection_fingerprint="f" * 64,
            )

    monkeypatch.setattr(
        "deskpet.companion.activation_platform.load_and_validate_pack",
        lambda *_args, **_kwargs: SimpleNamespace(
            manifest=SimpleNamespace(
                tools=(),
                mcp_servers=(),
                workflows=(),
            )
        ),
    )
    manager = _RemoveOverrideManager()
    port = CapabilityManagerStaticLifecyclePort(
        manager=manager,  # type: ignore[arg-type]
        candidate_sources=SimpleNamespace(),  # type: ignore[arg-type]
    )
    request = CapabilityLifecycleStaticRequest(
        authorization_hash="remove-override-auth",
        action="rollback",
        owner_key="companion:alice:1",
        scope="user",
        scope_key="profile",
        pack_id="summarize-day",
        manager_idempotency_key="manager:rollback:request-1",
        expected_binding_generation=3,
        launch_revocation_epoch=7,
        rollback_kind="remove_override",
        cause_ref="notification-1",
    )

    prepared = await port.prepare_static(request)
    receipt = await port.activate_empty(prepared)

    assert receipt["action"] == "rollback"
    assert receipt["target_user_owner_binding_set_stamp"] == "e" * 64
    assert receipt["fallback_owner_key"] == "builtin"
    assert receipt["fallback_binding_id"] == "builtin-binding"
    assert receipt["fallback_version"] == "1.0.0"
    assert receipt["fallback_manifest_hash"] == "b" * 64
    assert receipt["fallback_owner_binding_set_stamp"] == "c" * 64
    assert len(receipt["fallback_process_projection_fingerprint"]) == 64
    assert receipt["fallback_runtime_set_ref"].startswith("runtime-set:")
    assert len(receipt["fallback_runtime_set_hash"]) == 64
    assert manager.calls == [
        {
            "pack_id": "summarize-day",
            "scope": "user",
            "scope_key": "profile",
            "idempotency_key": "manager:rollback:request-1",
            "owner_key": "companion:alice:1",
            "expected_binding_generation": 3,
        }
    ]


class _DecisionStore:
    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE growth_decisions(
              profile_id TEXT,profile_generation INTEGER,decision_id TEXT,
              candidate_id TEXT,report_id TEXT,risk_id TEXT,
              target_owner_key TEXT,target_scope TEXT,target_scope_key TEXT,
              target_expected_binding_generation INTEGER,candidate_mode TEXT,
              activation_package_hash TEXT,activation_code_digest TEXT,
              activation_risk_ack TEXT,decision TEXT,actor TEXT
            );
            CREATE TABLE candidate_artifacts(
              profile_id TEXT,profile_generation INTEGER,candidate_id TEXT,
              package_id TEXT,status TEXT
            );
            CREATE TABLE candidate_packages(
              profile_id TEXT,profile_generation INTEGER,package_id TEXT,
              candidate_package_hash TEXT
            );
            CREATE TABLE risk_assessments(
              profile_id TEXT,profile_generation INTEGER,candidate_id TEXT,
              risk_id TEXT,risk TEXT,static_preflight_json TEXT
            );
            CREATE TABLE evaluation_reports(
              profile_id TEXT,profile_generation INTEGER,report_id TEXT,
              verdict TEXT
            );
            """
        )

    @contextmanager
    def read(self):
        yield self.db


@pytest.mark.asyncio
async def test_store_decision_resolver_requires_passed_low_risk_auto_decision() -> None:
    authorization = _authorization(executable=False)
    store = _DecisionStore()
    store.db.execute(
        "INSERT INTO candidate_artifacts VALUES (?,?,?,?,?)",
        ("alice", 1, "candidate-1", "package-id-1", "activation_pending"),
    )
    store.db.execute(
        "INSERT INTO candidate_packages VALUES (?,?,?,?)",
        ("alice", 1, "package-id-1", "package-1"),
    )
    store.db.execute(
        "INSERT INTO risk_assessments VALUES (?,?,?,?,?,?)",
        (
            "alice",
            1,
            "candidate-1",
            "risk-1",
            "low",
            '{"candidate_kind":"instruction"}',
        ),
    )
    store.db.execute(
        "INSERT INTO evaluation_reports VALUES (?,?,?,?)",
        ("alice", 1, "report-1", "passed"),
    )
    store.db.execute(
        "INSERT INTO growth_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "alice",
            1,
            "decision-1",
            "candidate-1",
            "report-1",
            "risk-1",
            "companion:alice:1",
            "user",
            "profile",
            2,
            "update",
            "package-1",
            None,
            "none",
            "activate",
            "system",
        ),
    )
    store.db.commit()

    proof = await CompanionStoreActivationDecisionProofResolver(
        store
    ).resolve_activation_decision(authorization)

    assert proof is not None
    assert proof.decision_mode == "safe_auto"
    assert proof.executable is False
    proof.validate()


@pytest.mark.asyncio
async def test_store_candidate_source_materializes_exact_inactive_archive(
    tmp_path,
) -> None:
    store = _DecisionStore()
    store.db.executescript(
        """
        ALTER TABLE candidate_artifacts ADD COLUMN ignored TEXT;
        ALTER TABLE candidate_packages ADD COLUMN pack_id TEXT;
        ALTER TABLE candidate_packages ADD COLUMN version TEXT;
        ALTER TABLE candidate_packages ADD COLUMN candidate_manifest_hash TEXT;
        ALTER TABLE candidate_packages ADD COLUMN archive_hash TEXT;
        ALTER TABLE candidate_packages ADD COLUMN content_state TEXT;
        ALTER TABLE candidate_packages ADD COLUMN blob_cleanup_state TEXT;
        CREATE TABLE candidate_package_blobs(
          profile_id TEXT,profile_generation INTEGER,package_id TEXT,
          blob_kind TEXT,blob_id TEXT,payload BLOB,cleanup_state TEXT
        );
        CREATE TABLE capability_activation_requests(
          profile_id TEXT,profile_generation INTEGER,candidate_id TEXT,
          risk_id TEXT,manager_idempotency_key TEXT
        );
        """
    )
    manifest = {
        "id": "summarize-day",
        "version": "1.0.2",
        "entries": {"skills": ["SKILL.md"], "tools": [], "mcp_servers": []},
    }
    manifest_payload = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    archive_buffer = BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("deskpet-pack.json", manifest_payload)
    archive_payload = archive_buffer.getvalue()
    manifest_hash = hashlib.sha256(manifest_payload).hexdigest()
    archive_hash = hashlib.sha256(archive_payload).hexdigest()
    store.db.execute(
        """INSERT INTO candidate_artifacts(
             profile_id,profile_generation,candidate_id,package_id,status,ignored
           ) VALUES (?,?,?,?,?,?)""",
        ("alice", 1, "candidate-1", "package-id-1", "activation_pending", ""),
    )
    store.db.execute(
        """INSERT INTO candidate_packages(
             profile_id,profile_generation,package_id,candidate_package_hash,
             pack_id,version,candidate_manifest_hash,archive_hash,
             content_state,blob_cleanup_state
           ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            "alice",
            1,
            "package-id-1",
            "b" * 64,
            "summarize-day",
            "1.0.2",
            manifest_hash,
            archive_hash,
            "live",
            "live",
        ),
    )
    store.db.executemany(
        "INSERT INTO candidate_package_blobs VALUES (?,?,?,?,?,?,?)",
        (
            (
                "alice",
                1,
                "package-id-1",
                "manifest",
                manifest_hash,
                manifest_payload,
                "live",
            ),
            (
                "alice",
                1,
                "package-id-1",
                "archive",
                archive_hash,
                archive_payload,
                "live",
            ),
        ),
    )
    store.db.execute(
        "INSERT INTO risk_assessments VALUES (?,?,?,?,?,?)",
        (
            "alice",
            1,
            "candidate-1",
            "risk-1",
            "low",
            '{"candidate_kind":"instruction"}',
        ),
    )
    store.db.execute(
        "INSERT INTO capability_activation_requests VALUES (?,?,?,?,?)",
        (
            "alice",
            1,
            "candidate-1",
            "risk-1",
            "manager:update:request-1",
        ),
    )
    store.db.commit()
    request = CapabilityLifecycleStaticRequest(
        authorization_hash="auth-1",
        action="update",
        owner_key="companion:alice:1",
        scope="user",
        scope_key="profile",
        pack_id="summarize-day",
        manager_idempotency_key="manager:update:request-1",
        expected_binding_generation=2,
        launch_revocation_epoch=7,
        candidate_id="candidate-1",
        candidate_mode="update",
        target_version="1.0.2",
        target_manifest_hash=manifest_hash,
        target_package_hash="b" * 64,
        target_archive_hash=archive_hash,
    )

    resolved = await CompanionStoreLifecycleCandidateSourceResolver(
        store,
        cache_root=tmp_path / "inactive-candidates",
    ).resolve_candidate_source(request)

    resolved.validate(request)
    assert resolved.executable is False
    cached = Path(resolved.source.uri)
    assert cached.is_file()
    assert cached.read_bytes() == archive_payload
