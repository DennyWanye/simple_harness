from __future__ import annotations

from dataclasses import dataclass

import pytest

from deskpet.capabilities.skill_install_ui import (
    ProjectSkillInstallUIAdapter,
    ProjectSkillInstallUIError,
    TrustedProjectInstallContext,
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

    async def confirm_authorized(self, **kwargs):
        self.calls.append(("confirm_authorized", kwargs))
        return {"intent_id": "intent-1", "status": "succeeded"}

    async def cancel_authorized(self, **kwargs):
        self.calls.append(("cancel_authorized", kwargs))
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
    assert service.calls == [
        (
            "stage",
            {
                "repository_url": "https://github.com/o/r",
                "requested_ref": "HEAD",
                "project": {
                    "project_id": "project-a",
                    "project_name": "Project A",
                    "project_revision": 3,
                    "project_identity": "identity-a",
                    "project_root": "/tmp/project-a",
                },
                "principal_id": "session:session-a",
                "channel": "settings",
            },
        )
    ]


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
    assert service.calls == [
        (
            "confirm_authorized",
            {
                "intent_id": "intent-1",
                "digest": "digest-1",
                "decision_receipt": {
                    "kind": "settings",
                    "receipt_id": "host-receipt-1",
                    "decision": "approve",
                },
            },
        )
    ]
    assert "decision" not in service.calls[0][1]


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
    assert "decision_receipt" in service.calls[0][1]


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
