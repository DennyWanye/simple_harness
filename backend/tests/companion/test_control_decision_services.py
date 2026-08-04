from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from deskpet.companion.control_ingress import (
    CompanionActivationDecisionService,
    CompanionEvaluationDecisionService,
    CompanionForgetDecisionService,
    CompanionGrowthControlError,
    CompanionRollbackDecisionService,
    UnavailableCompanionGrowthControlService,
)
from deskpet.companion.contracts import OwnerRef


OWNER = OwnerRef("profile-1", 1)
FROZEN = SimpleNamespace(owner=OWNER, binding_epoch=3)


class _Rows:
    def __init__(self, store, sql: str) -> None:
        self.store = store
        self.sql = sql

    def fetchall(self):
        if (
            "FROM evaluation_runs e" in self.sql
            or "FROM candidate_artifacts a" in self.sql
        ):
            return list(self.store.rows)
        if "FROM capability_activation_requests q" in self.sql:
            return list(self.store.rollback_rows)
        return []

    def fetchone(self):
        if "FROM growth_decisions" in self.sql:
            return self.store.activation_nonce
        if "FROM evaluation_authorizations" in self.sql:
            return self.store.evaluation_nonce
        return None


class _DB:
    def __init__(self, store) -> None:
        self.store = store

    def execute(self, sql, _params=()):
        return _Rows(self.store, sql)


class _Store:
    def __init__(self) -> None:
        self.rows = []
        self.rollback_rows = []
        self.activation_nonce = None
        self.evaluation_nonce = None
        self.calls = []
        self.notification = None
        self.activation_challenge = {
            "kind": "activation",
            "candidate_id": "candidate-1",
            "pack_id": "personal.pack",
            "candidate_version": "1.0.0",
            "candidate_package_hash": "package-hash",
            "candidate_code_digest": "content-hash",
            "evaluation_report_hash": "report-hash",
            "risk_assessment_hash": "risk-hash",
            "scope": "user",
            "scope_key": "profile",
            "owner_key": "companion:profile-1:1",
            "expected_binding_generation": 0,
            "nonce": "activation-nonce",
            "decision_version": 1,
            "expires_at": "2999-01-01T00:00:00Z",
            "status": "open",
        }

    @contextmanager
    def read(self):
        yield _DB(self)

    def create_evaluation_authorization(self, owner, **kwargs):
        self.calls.append(("evaluation", owner, kwargs))
        return kwargs

    def record_activation_decision(self, owner, **kwargs):
        self.calls.append(("activation", owner, kwargs))
        return kwargs

    def create_capability_mutation_request(self, owner, request):
        self.calls.append(("mutation", owner, request))
        return {
            "status": "pending",
            "activation_request_id": request.request_id,
        }

    def get_notification(self, owner, notification_id):
        assert owner == OWNER
        assert notification_id == "notification-1"
        return self.notification

    def get_activation_decision_challenge(self, owner, *, nonce):
        assert owner == OWNER
        if self.activation_challenge.get("nonce") != nonce:
            return None
        return dict(self.activation_challenge)

    def forget_growth_event(self, owner, *, event_id, reason_code):
        self.calls.append(("forget", owner, event_id, reason_code))
        return True


class _Coordinator:
    def __init__(self) -> None:
        self.calls = []

    def decide_and_enqueue(self, owner, **kwargs):
        self.calls.append((owner, kwargs))
        return {
            "status": "pending",
            "activation_request_id": kwargs["mutation"].request_id,
        }


class _Detail:
    async def current_detail_version(self, **_kwargs):
        return "detail-1"


class _VersionedDetail:
    def __init__(self, version: str) -> None:
        self.version = version

    async def current_detail_version(self, **_kwargs):
        return self.version


class _Dispatcher:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, owner, **kwargs):
        self.calls.append((owner, kwargs))
        return {
            "status": "succeeded",
            "activation_request_id": kwargs["request_id"],
        }


def _evaluation_body(**overrides):
    value = {
        "candidate_id": "candidate-1",
        "candidate_revision": 2,
        "candidate_package_hash": "package-hash",
        "candidate_code_digest": "content-hash",
        "suite_hash": "suite-hash",
        "runner_policy_hash": "runner-hash",
        "nonce": "eval-nonce",
        "decision_version": 1,
        "expires_at": "2999-01-01T00:00:00Z",
        "allow": True,
    }
    value.update(overrides)
    return value


def _activation_body(**overrides):
    value = {
        "candidate_id": "candidate-1",
        "pack_id": "personal.pack",
        "candidate_version": "1.0.0",
        "candidate_package_hash": "package-hash",
        "candidate_code_digest": "content-hash",
        "evaluation_report_hash": "report-hash",
        "risk_assessment_hash": "risk-hash",
        "scope": "user",
        "scope_key": "profile",
        "owner_key": "companion:profile-1:1",
        "expected_binding_generation": 0,
        "nonce": "activation-nonce",
        "decision_version": 1,
        "expires_at": "2999-01-01T00:00:00Z",
        "allow": True,
        "activation_risk_ack": "persistent_local_code_no_os_sandbox",
    }
    value.update(overrides)
    return value


@pytest.mark.asyncio
async def test_evaluation_service_resolves_exact_store_fence() -> None:
    store = _Store()
    store.rows = [
        {
            "evaluation_id": "evaluation-1",
            "attempt_generation": 2,
            "candidate_content_hash": "content-hash",
            "candidate_kind": "code",
        }
    ]
    result = await CompanionEvaluationDecisionService(store).decide(
        frozen_identity=FROZEN,
        **_evaluation_body(),
    )
    assert result["status"] == "authorized"
    assert store.calls[0][0] == "evaluation"
    assert store.calls[0][2]["risk_ack"] == "no_os_sandbox"


@pytest.mark.asyncio
async def test_evaluation_nonce_cannot_be_reused_as_activation_nonce() -> None:
    store = _Store()
    store.rows = [
        {
            "evaluation_id": "evaluation-1",
            "attempt_generation": 2,
            "candidate_content_hash": "content-hash",
            "candidate_kind": "code",
        }
    ]
    store.activation_nonce = (1,)
    with pytest.raises(
        CompanionGrowthControlError,
        match="companion_evaluation_nonce_wrong_domain",
    ):
        await CompanionEvaluationDecisionService(store).decide(
            frozen_identity=FROZEN,
            **_evaluation_body(),
        )
    assert store.calls == []


@pytest.mark.asyncio
async def test_activation_service_enqueues_task9_saga_without_auto_bypass() -> None:
    store = _Store()
    store.rows = [
        {
            "candidate_mode": "genesis",
            "candidate_content_hash": "content-hash",
            "candidate_manifest_hash": "manifest-hash",
            "archive_hash": "archive-hash",
            "candidate_kind": "code",
            "report_id": "report-1",
            "risk_id": "risk-1",
            "source_owner_key": None,
            "source_scope": None,
            "source_scope_key": None,
            "source_version": None,
            "source_manifest_hash": None,
            "source_binding_generation": None,
            "target_expected_absent": 1,
        }
    ]
    coordinator = _Coordinator()
    result = await CompanionActivationDecisionService(
        store, coordinator
    ).decide(
        frozen_identity=FROZEN,
        **_activation_body(),
    )
    assert result["status"] == "pending"
    call = coordinator.calls[0][1]
    assert call["actor"] == "user"
    assert (
        call["activation_risk_ack"]
        == "persistent_local_code_no_os_sandbox"
    )
    assert call["mutation"].target_package_hash == "package-hash"


@pytest.mark.asyncio
async def test_activation_requires_exact_server_issued_challenge() -> None:
    store = _Store()
    store.rows = []
    with pytest.raises(
        CompanionGrowthControlError,
        match="companion_activation_challenge_mismatch",
    ):
        await CompanionActivationDecisionService(
            store, _Coordinator()
        ).decide(
            frozen_identity=FROZEN,
            **_activation_body(candidate_package_hash="forged-package"),
        )


@pytest.mark.asyncio
async def test_activation_confirmation_dispatches_same_durable_request() -> None:
    store = _Store()
    store.rows = [
        {
            "candidate_mode": "genesis",
            "candidate_content_hash": "content-hash",
            "candidate_manifest_hash": "manifest-hash",
            "archive_hash": "archive-hash",
            "candidate_kind": "code",
            "report_id": "report-1",
            "risk_id": "risk-1",
            "source_owner_key": None,
            "source_scope": None,
            "source_scope_key": None,
            "source_version": None,
            "source_manifest_hash": None,
            "source_binding_generation": None,
            "target_expected_absent": 1,
        }
    ]
    dispatcher = _Dispatcher()
    result = await CompanionActivationDecisionService(
        store,
        _Coordinator(),
        dispatcher=dispatcher,
    ).decide(
        frozen_identity=FROZEN,
        **_activation_body(),
    )
    assert result["status"] == "succeeded"
    assert dispatcher.calls[0][1]["request_id"] == (
        result["activation_request_id"]
    )


@pytest.mark.asyncio
async def test_activation_rejects_missing_exact_code_ack_even_if_auto_is_true() -> None:
    store = _Store()
    store.auto_mode = True
    store.rows = [
        {
            "candidate_mode": "genesis",
            "candidate_content_hash": "content-hash",
            "candidate_manifest_hash": "manifest-hash",
            "archive_hash": "archive-hash",
            "candidate_kind": "code",
            "report_id": "report-1",
            "risk_id": "risk-1",
            "source_owner_key": None,
            "source_scope": None,
            "source_scope_key": None,
            "source_version": None,
            "source_manifest_hash": None,
            "source_binding_generation": None,
            "target_expected_absent": 1,
        }
    ]
    coordinator = _Coordinator()
    with pytest.raises(
        CompanionGrowthControlError,
        match="companion_activation_code_confirmation_incomplete",
    ):
        await CompanionActivationDecisionService(
            store, coordinator
        ).decide(
            frozen_identity=FROZEN,
            **_activation_body(activation_risk_ack="none"),
        )
    assert coordinator.calls == []


@pytest.mark.asyncio
async def test_rollback_and_forget_use_current_notification_fence() -> None:
    store = _Store()
    store.notification = {
        "notification_id": "notification-1",
        "available_actions": ["rollback", "forget"],
        "source_refs": ["activation-1", "event-1"],
    }
    store.rollback_rows = [
        {
            "activation_request_id": "activation-1",
            "candidate_id": "candidate-1",
            "candidate_mode": "genesis",
            "source_fence_json": None,
            "target_owner_key": "companion:profile-1:1",
            "target_scope": "user",
            "target_scope_key": "profile",
            "pack_id": "personal.pack",
            "binding_generation": 1,
        }
    ]
    body = {
        "notification_id": "notification-1",
        "expected_detail_version": "detail-1",
    }
    rolled = await CompanionRollbackDecisionService(
        store, _Detail()
    ).decide(frozen_identity=FROZEN, **body)
    forgotten = await CompanionForgetDecisionService(
        store, _Detail()
    ).decide(frozen_identity=FROZEN, **body)
    assert rolled["status"] == "pending"
    assert forgotten["forgotten_count"] == 2
    mutation = next(call[2] for call in store.calls if call[0] == "mutation")
    assert mutation.candidate_id == "candidate-1"
    assert mutation.candidate_mode.value == "genesis"


@pytest.mark.asyncio
async def test_rollback_dispatches_the_pending_mutation_immediately() -> None:
    store = _Store()
    store.notification = {
        "notification_id": "notification-1",
        "available_actions": ["rollback"],
        "source_refs": ["activation-1"],
    }
    store.rollback_rows = [
        {
            "activation_request_id": "activation-1",
            "candidate_mode": "genesis",
            "source_fence_json": None,
            "target_owner_key": "companion:profile-1:1",
            "target_scope": "user",
            "target_scope_key": "profile",
            "pack_id": "personal.pack",
            "binding_generation": 1,
        }
    ]
    dispatcher = _Dispatcher()

    result = await CompanionRollbackDecisionService(
        store,
        _Detail(),
        dispatcher=dispatcher,
    ).decide(
        frozen_identity=FROZEN,
        notification_id="notification-1",
        expected_detail_version="detail-1",
    )

    assert result["status"] == "succeeded"
    assert dispatcher.calls == [
        (
            OWNER,
            {
                "request_id": result["activation_request_id"],
                "claim_owner": (
                    "trusted-rollback:" + result["activation_request_id"]
                ),
            },
        )
    ]


@pytest.mark.asyncio
async def test_rollback_reads_durable_request_after_receipt_only_dispatch() -> None:
    class _ReceiptStore(_Store):
        def __init__(self) -> None:
            super().__init__()
            self.request_status = "pending"

        def create_capability_mutation_request(self, owner, request):
            result = super().create_capability_mutation_request(owner, request)
            self.request_id = str(result["activation_request_id"])
            return result

        def get_capability_mutation_request(self, owner, *, request_id):
            assert owner == OWNER
            assert request_id == self.request_id
            return {
                "status": self.request_status,
                "activation_request_id": request_id,
            }

    class _ReceiptDispatcher:
        async def execute(self, owner, **kwargs):
            assert owner == OWNER
            store.request_status = "succeeded"
            return {
                "activation_request_id": kwargs["request_id"],
                "manager_operation_id": "manager-operation-1",
                "result_hash": "receipt-hash",
            }

    store = _ReceiptStore()
    store.notification = {
        "notification_id": "notification-1",
        "available_actions": ["rollback"],
        "source_refs": ["activation-1"],
    }
    store.rollback_rows = [
        {
            "activation_request_id": "activation-1",
            "candidate_mode": "builtin_override",
            "source_fence_json": None,
            "target_owner_key": "companion:profile-1:1",
            "target_scope": "user",
            "target_scope_key": "profile",
            "pack_id": "personal.pack",
            "binding_generation": 1,
        }
    ]

    result = await CompanionRollbackDecisionService(
        store,
        _Detail(),
        dispatcher=_ReceiptDispatcher(),
    ).decide(
        frozen_identity=FROZEN,
        notification_id="notification-1",
        expected_detail_version="detail-1",
    )

    assert result == {
        "status": "succeeded",
        "activation_request_id": store.request_id,
    }


@pytest.mark.asyncio
async def test_rollback_identity_is_stable_across_detail_refreshes() -> None:
    store = _Store()
    store.notification = {
        "notification_id": "notification-1",
        "available_actions": ["rollback"],
        "source_refs": ["activation-1"],
    }
    store.rollback_rows = [
        {
            "activation_request_id": "activation-1",
            "candidate_mode": "genesis",
            "source_fence_json": None,
            "target_owner_key": "companion:profile-1:1",
            "target_scope": "user",
            "target_scope_key": "profile",
            "pack_id": "personal.pack",
            "binding_generation": 1,
        }
    ]

    for version in ("detail-1", "detail-2"):
        await CompanionRollbackDecisionService(
            store,
            _VersionedDetail(version),
        ).decide(
            frozen_identity=FROZEN,
            notification_id="notification-1",
            expected_detail_version=version,
        )

    requests = [call[2] for call in store.calls if call[0] == "mutation"]
    assert len(requests) == 2
    assert requests[0].request_id == requests[1].request_id
    assert requests[0].request_fingerprint == requests[1].request_fingerprint


@pytest.mark.asyncio
async def test_rollback_dispatches_the_store_adopted_request_id() -> None:
    class _AdoptingStore(_Store):
        def create_capability_mutation_request(self, owner, request):
            self.calls.append(("mutation", owner, request))
            return {
                "status": "pending",
                "activation_request_id": "legacy-pending-request",
            }

    store = _AdoptingStore()
    store.notification = {
        "notification_id": "notification-1",
        "available_actions": ["rollback"],
        "source_refs": ["activation-1"],
    }
    store.rollback_rows = [
        {
            "activation_request_id": "activation-1",
            "candidate_mode": "genesis",
            "source_fence_json": None,
            "target_owner_key": "companion:profile-1:1",
            "target_scope": "user",
            "target_scope_key": "profile",
            "pack_id": "personal.pack",
            "binding_generation": 1,
        }
    ]
    dispatcher = _Dispatcher()

    result = await CompanionRollbackDecisionService(
        store,
        _Detail(),
        dispatcher=dispatcher,
    ).decide(
        frozen_identity=FROZEN,
        notification_id="notification-1",
        expected_detail_version="detail-1",
    )

    assert result == {
        "status": "succeeded",
        "activation_request_id": "legacy-pending-request",
    }
    assert dispatcher.calls[0][1] == {
        "request_id": "legacy-pending-request",
        "claim_owner": "trusted-rollback:legacy-pending-request",
    }


@pytest.mark.asyncio
async def test_registered_unavailable_adapter_fails_with_typed_code() -> None:
    service = UnavailableCompanionGrowthControlService("dependency_missing")
    with pytest.raises(CompanionGrowthControlError, match="dependency_missing"):
        await service.decide(decision_id="decision-1", allow=True)
