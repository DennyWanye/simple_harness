from __future__ import annotations

import json

from deskpet.tools import registry
from deskpet.tools.build_identity import EffectClass, IdempotencyClass
from deskpet.tools.project_group_send import configure_project_group_transport


class _Transport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send_all_project_groups(
        self, *, content: str, idempotency_key: str
    ) -> dict[str, object]:
        self.calls.append((content, idempotency_key))
        return {
            "ok": True,
            "delivery_ref": "delivery:test",
            "physical_send_count": 1,
        }


def test_project_group_send_has_checked_external_effect_identity() -> None:
    spec = registry.get("project_group_send")
    assert spec is not None
    assert spec.effect_class is EffectClass.EXTERNAL_SEND
    assert spec.idempotency is IdempotencyClass.NON_IDEMPOTENT
    assert spec.target_normalizer_version == "project_group_v1"
    assert spec.permission_category == "external_action"
    assert spec.dangerous is True
    assert spec.concurrency_safe is False
    assert spec.execution_build_identity is not None


def test_project_group_send_fails_typed_without_physical_transport() -> None:
    spec = registry.get("project_group_send")
    assert spec is not None
    configure_project_group_transport(None)
    payload = json.loads(
        spec.handler(
            {
                "target_scope": "all_project_groups",
                "content": "daily summary",
            },
            "effect:test",
        )
    )
    assert payload == {
        "ok": False,
        "error": {
            "code": "project_group_transport_unavailable",
            "message": "No project-group transport is connected.",
            "retriable": True,
        },
        "physical_send_count": 0,
    }


def test_physical_transport_begins_only_when_handler_is_dispatched() -> None:
    spec = registry.get("project_group_send")
    assert spec is not None
    transport = _Transport()
    configure_project_group_transport(transport)
    try:
        assert transport.calls == []
        payload = json.loads(
            spec.handler(
                {
                    "target_scope": "all_project_groups",
                    "content": "  daily summary  ",
                },
                "effect:confirmed",
            )
        )
        assert payload == {
            "ok": True,
            "delivery_ref": "delivery:test",
            "physical_send_count": 1,
        }
        assert transport.calls == [("daily summary", "effect:confirmed")]
    finally:
        configure_project_group_transport(None)
