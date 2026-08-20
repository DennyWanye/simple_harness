# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from types import SimpleNamespace

import pytest

import main


def test_sdk_tool_grant_uses_supported_auto_policy_source():
    prepared = SimpleNamespace(
        effect_id=SimpleNamespace(value="effect-1"),
        run_id=SimpleNamespace(value="run-1"),
        context_metadata={
            "session_id": "session-1",
            "root_run_id": "canonical-root-1",
        },
    )

    grant = main._build_sdk_task_grant(prepared, generation=7)

    assert grant.source == "policy:auto"
    assert grant.policy_generation == 7
    assert grant.root_run_id == "canonical-root-1"
    assert grant.expires_at is None

    second = main._build_sdk_task_grant(prepared, generation=7)
    assert second.fingerprint == grant.fingerprint


def test_sdk_prepared_identity_uses_context_metadata_without_request_id():
    prepared = SimpleNamespace(
        run_id=SimpleNamespace(value="sdk-run-1"),
        context_metadata={
            "session_id": "session-1",
            "root_run_id": "canonical-root-1",
        },
    )

    assert main._sdk_prepared_identity(prepared) == (
        "session-1",
        "canonical-root-1",
    )


class _FakeIngress:
    def __init__(self) -> None:
        self.accepting = True
        self.closed = False

    def close(self) -> None:
        self.accepting = False
        self.closed = True

    def open(self) -> None:
        self.accepting = True


class _FakeStack:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_provider_change_activates_runtime_after_first_provider(monkeypatch):
    new_stack = _FakeStack()
    new_ingress = _FakeIngress()
    main._sdk_runtime_stack = None
    main._sdk_ingress = None

    async def activate() -> None:
        main._sdk_runtime_stack = new_stack
        main._sdk_ingress = new_ingress

    async def bind_and_open() -> None:
        new_ingress.open()

    monkeypatch.setattr(main, "_activate_product_sdk_runtime", activate)
    monkeypatch.setattr(
        main,
        "_activate_companion_runtime_adapter_and_open_ingress",
        bind_and_open,
    )

    assert await main._refresh_product_sdk_runtime_after_provider_change() is True
    assert main._sdk_runtime_stack is new_stack
    assert main._sdk_ingress is new_ingress
    assert new_ingress.accepting is True


@pytest.mark.asyncio
async def test_provider_change_closes_old_stack_before_rebuild(monkeypatch):
    old_stack = _FakeStack()
    old_ingress = _FakeIngress()
    new_stack = _FakeStack()
    new_ingress = _FakeIngress()
    main._sdk_runtime_stack = old_stack
    main._sdk_ingress = old_ingress

    async def activate() -> None:
        assert old_stack.closed is True
        assert old_ingress.closed is True
        main._sdk_runtime_stack = new_stack
        main._sdk_ingress = new_ingress

    async def bind_and_open() -> None:
        new_ingress.open()

    monkeypatch.setattr(main, "_activate_product_sdk_runtime", activate)
    monkeypatch.setattr(
        main,
        "_activate_companion_runtime_adapter_and_open_ingress",
        bind_and_open,
    )

    assert await main._refresh_product_sdk_runtime_after_provider_change() is True
    assert old_stack.closed is True
    assert old_ingress.closed is True
    assert main._sdk_runtime_stack is new_stack
    assert main._sdk_ingress is new_ingress


@pytest.mark.asyncio
async def test_session_model_binding_refreshes_frozen_sdk_provider(monkeypatch):
    ingress = _FakeIngress()
    main._sdk_ingress = ingress
    main._sdk_runtime_provider_binding = ("provider-1", "model-a")
    calls: list[tuple[str | None, str | None]] = []

    async def refresh(*, provider_id_override=None, model_override=None):
        calls.append((provider_id_override, model_override))
        main._sdk_runtime_provider_binding = (
            str(provider_id_override),
            str(model_override),
        )
        return True

    monkeypatch.setattr(
        main, "_refresh_product_sdk_runtime_after_provider_change", refresh
    )

    await main._ensure_product_sdk_runtime_provider_binding(
        "provider-1", "model-b"
    )

    assert calls == [("provider-1", "model-b")]
    assert main._sdk_runtime_provider_binding == ("provider-1", "model-b")
