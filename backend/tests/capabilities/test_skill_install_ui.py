from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from deskpet.capabilities.skill_install_ui import (
    ProjectSkillInstallUIAdapter,
    ProjectSkillInstallUIError,
    SettingsSkillInstallAuthorizerFactory,
    TrustedGlobalInstallContext,
    TrustedProjectInstallContext,
)
from deskpet.capabilities.store import (
    CapabilitySkillInstallIntent,
    CapabilitySkillInstallMember,
    CapabilityStore,
    initialize_capability_database,
)


@dataclass
class _Binding:
    kind: str = "project"
    project_id: str = "project-a"
    project_name: str = "Project A"
    project_revision: int = 3
    project_identity: str = "identity-a"
    project_root: str = "/tmp/project-a"


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def stage(self, **kwargs):
        self.calls.append(("stage", kwargs))
        return {"intent_id": "intent-1", "status": "awaiting_confirmation"}

    async def confirm_authorized(self, receipt):
        self.calls.append(("confirm_authorized", receipt))
        return {"intent_id": "intent-1", "status": "succeeded"}

    async def cancel_authorized(self, receipt):
        self.calls.append(("cancel_authorized", receipt))
        return {"intent_id": "intent-1", "status": "denied"}


class _Authorizer:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def issue_skill_install_receipt(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "kind": "settings",
            "receipt_id": "host-receipt-1",
            "decision": kwargs["decision"],
        }


@pytest.mark.asyncio
async def test_stage_uses_only_frozen_project_context() -> None:
    service = _Service()
    adapter = ProjectSkillInstallUIAdapter(service=service, authorizer=_Authorizer())
    project = TrustedProjectInstallContext.from_binding(_Binding())

    result = await adapter.stage(
        url="https://github.com/o/r",
        project=project,
        principal_id="session:session-a",
    )

    assert result["intent_id"] == "intent-1"
    assert len(service.calls) == 1
    name, call = service.calls[0]
    assert name == "stage"
    assert call["url"] == "https://github.com/o/r"
    assert call["requested_ref"] == "HEAD"
    assert call["channel"] == "settings"
    assert call["project"].project_id == "project-a"
    assert call["project"].principal_id == "session:session-a"
    assert call["run_id"] == call["root_run_id"]
    assert call["call_id"].startswith("settings-call:")
    assert call["effect_id"].startswith("settings-effect:")


@pytest.mark.asyncio
async def test_stage_accepts_only_frozen_global_owner_context() -> None:
    service = _Service()
    adapter = ProjectSkillInstallUIAdapter(service=service, authorizer=_Authorizer())
    owner = TrustedGlobalInstallContext(
        principal_id="session:session-a", global_owner_key="user:v2:" + "9" * 64
    )
    await adapter.stage(
        url="https://github.com/o/r", owner=owner,
        principal_id="session:session-a",
    )
    call = service.calls[0][1]
    assert "project" not in call
    assert call["owner"].global_owner_key == owner.global_owner_key
    assert call["owner"].principal_id == "session:session-a"


@pytest.mark.asyncio
async def test_stage_projects_durable_global_confirmation_contract() -> None:
    class _Store:
        async def get_skill_install_intent(self, _intent_id):
            return SimpleNamespace(
                member_set_stamp="digest-1", confirmation_nonce="nonce-1",
                confirmation_version=3, exact_commit="a" * 40, expires_at=200.0,
                source={"normalized_url": "https://github.com/o/r"},
            )

        async def skill_install_members(self, _intent_id):
            return (SimpleNamespace(
                normalized_name="example", version="1.0.0",
                manifest_hash="manifest", content_hash="content",
                member={"allowed_tools": ["read_file"]},
            ),)

    service = _Service()
    service.store = _Store()
    adapter = ProjectSkillInstallUIAdapter(service=service, authorizer=_Authorizer())
    owner = TrustedGlobalInstallContext(
        principal_id="session:session-a", global_owner_key="user:v2:" + "9" * 64
    )

    result = await adapter.stage(
        url="https://github.com/o/r", owner=owner, principal_id="session:session-a"
    )

    assert result["digest"] == "digest-1"
    assert result["decision_nonce"] == "nonce-1"
    assert result["decision_version"] == 3
    assert result["resolved_commit"] == "a" * 40
    assert result["members"] == [{
        "name": "example", "version": "1.0.0", "manifest_hash": "manifest",
        "content_hash": "content", "permission_categories": ["read_file"],
    }]


@pytest.mark.asyncio
async def test_stage_translates_advertised_github_tree_url_to_exact_ref() -> None:
    service = _Service()
    adapter = ProjectSkillInstallUIAdapter(service=service, authorizer=_Authorizer())
    owner = TrustedGlobalInstallContext(
        principal_id="session:session-a", global_owner_key="user:v2:" + "9" * 64
    )
    commit = "a" * 40

    await adapter.stage(
        url=f"https://github.com/o/r/tree/{commit}/skills/example",
        owner=owner,
        principal_id="session:session-a",
    )

    call = service.calls[0][1]
    assert call["url"] == "https://github.com/o/r"
    assert call["requested_ref"] == commit


@pytest.mark.asyncio
async def test_ui_decision_is_exchanged_for_host_receipt() -> None:
    service = _Service()
    authorizer = _Authorizer()
    adapter = ProjectSkillInstallUIAdapter(service=service, authorizer=authorizer)
    await adapter.settle(
        intent_id="intent-1",
        digest="digest-1",
        decision_nonce="nonce-1",
        decision_version=2,
        decision="approve",
    )

    assert authorizer.calls == [{
        "intent_id": "intent-1",
        "digest": "digest-1",
        "decision_nonce": "nonce-1",
        "decision_version": 2,
        "decision": "approve",
    }]
    assert service.calls == [("confirm_authorized", {
        "kind": "settings", "receipt_id": "host-receipt-1", "decision": "approve"
    })]


@pytest.mark.asyncio
async def test_success_projection_requires_and_exposes_runtime_verification() -> None:
    class _VerifiedService(_Service):
        def __init__(self):
            super().__init__()
            self.store = SimpleNamespace(
                skill_install_members=lambda _intent_id: ("a", "b", "c")
            )

        async def confirm_authorized(self, receipt):
            self.calls.append(("confirm_authorized", receipt))
            return {
                "intent_id": "intent-1", "status": "succeeded",
                "verification_ref": "verified-ref",
            }

    adapter = ProjectSkillInstallUIAdapter(
        service=_VerifiedService(), authorizer=_Authorizer()
    )
    result = await adapter.settle(
        intent_id="intent-1", digest="digest-1", decision_nonce="nonce-1",
        decision_version=1, decision="approve",
    )

    assert result["runtime_verified"] is True
    assert result["installed_count"] == 3


@pytest.mark.asyncio
async def test_missing_host_authorizer_fails_closed() -> None:
    adapter = ProjectSkillInstallUIAdapter(service=_Service(), authorizer=None)
    with pytest.raises(ProjectSkillInstallUIError) as exc:
        await adapter.settle(
            intent_id="intent-1",
            digest="digest-1",
            decision_nonce="nonce-1",
            decision_version=1,
            decision="approve",
        )
    assert exc.value.code == "settings_install_authorization_unavailable"


@pytest.mark.asyncio
async def test_deny_is_settled_with_a_host_receipt() -> None:
    service = _Service()
    authorizer = _Authorizer()
    adapter = ProjectSkillInstallUIAdapter(service=service, authorizer=authorizer)
    result = await adapter.settle(
        intent_id="intent-1",
        digest="digest-1",
        decision_nonce="nonce-1",
        decision_version=1,
        decision="deny",
    )

    assert result["status"] == "denied"
    assert authorizer.calls[0]["decision"] == "deny"
    assert service.calls[0][0] == "cancel_authorized"
    assert service.calls[0][1]["kind"] == "settings"


def test_projectless_binding_is_rejected() -> None:
    with pytest.raises(ProjectSkillInstallUIError) as exc:
        TrustedProjectInstallContext.from_binding(_Binding(kind="projectless"))
    assert exc.value.code == "project_session_required"


@pytest.mark.asyncio
async def test_spoofed_ui_identity_fields_cannot_enter_authorizer_contract() -> None:
    adapter = ProjectSkillInstallUIAdapter(service=_Service(), authorizer=_Authorizer())
    with pytest.raises(TypeError):
        await adapter.settle(
            intent_id="intent-1",
            digest="digest-1",
            decision_nonce="nonce-1",
            decision_version=1,
            decision="approve",
            session_id="spoofed",  # type: ignore[call-arg]
            principal_id="spoofed",  # type: ignore[call-arg]
            project={"project_id": "spoofed"},  # type: ignore[call-arg]
        )


@pytest.mark.asyncio
async def test_chat_or_sdk_receipt_is_rejected() -> None:
    class _BadAuthorizer:
        async def issue_skill_install_receipt(self, **_kwargs):
            return {"kind": "chat", "sdk_receipt_hash": "sdk-hash"}

    service = _Service()
    adapter = ProjectSkillInstallUIAdapter(service=service, authorizer=_BadAuthorizer())
    with pytest.raises(ProjectSkillInstallUIError) as exc:
        await adapter.settle(
            intent_id="intent-1",
            digest="digest-1",
            decision_nonce="nonce-1",
            decision_version=1,
            decision="approve",
        )
    assert exc.value.code == "settings_install_receipt_invalid"
    assert service.calls == []


@pytest.mark.asyncio
async def test_settings_authorizer_binds_global_intent_to_control_connection(tmp_path) -> None:
    owner_key = "user:v2:" + "9" * 64
    principal_id = f"settings:{owner_key}"
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    intent = CapabilitySkillInstallIntent(
        intent_id="intent-global", effect_id="effect-global", call_id="call-global",
        root_run_id="root-global", run_id="run-global", channel="settings",
        project_scope_key=owner_key, principal_id=principal_id,
        source={"schema": "global-skill-install-source-v2", "url": "https://github.com/o/r"},
        exact_commit="a" * 40, archive_hash="archive", raw_tree_hash="tree",
        member_set_stamp="digest-global", permission_set_hash="permissions",
        confirmation_nonce="nonce-global", confirmation_version=2,
        expires_at=200.0, status="staging", state_version=1,
        settlement_ref=None, cleanup_ref=None, verification_ref=None, error=None,
        created_at=100.0, updated_at=100.0,
    )
    member = CapabilitySkillInstallMember(
        intent_id=intent.intent_id, ordinal=0, normalized_name="example",
        pack_id="example", version="1.0.0", manifest_hash="manifest",
        content_hash="content", source_digest="source", member={"name": "example"},
    )
    await store.create_skill_install_intent(intent, (member,))
    await store.cas_skill_install_intent(
        intent.intent_id, expected_state_version=1, status="awaiting_confirmation"
    )
    factory = SettingsSkillInstallAuthorizerFactory(
        store=store, global_owner_key=owner_key, clock=lambda: 101.0
    )

    bound = await factory.bind_control_request(
        SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    )
    receipt = await bound.issue_skill_install_receipt(
        intent_id=intent.intent_id, digest=intent.member_set_stamp,
        decision_nonce=intent.confirmation_nonce,
        decision_version=intent.confirmation_version, decision="approve",
    )

    assert bound.principal_id == principal_id
    assert receipt.channel == "settings"
    assert receipt.project_scope_key == owner_key
    assert receipt.principal_id == principal_id
    assert receipt.approved is True
