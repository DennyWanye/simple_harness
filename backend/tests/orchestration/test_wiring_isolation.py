# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-17 and plan §3.3 startup isolation (review P2-10): nothing the orchestration wiring
does can fail the Host lifespan.  A fresh install without a model is an ordinary state, not
a startup error; any other failure is recorded in ``startup_errors`` and the chat goes on.
"""

from __future__ import annotations

from typing import Any

import pytest

import deskpet.orchestration.wiring as wiring
from deskpet.orchestration.provider import NO_MODEL
from deskpet.orchestration.wiring import activate_orchestration, deactivate_orchestration


class _Context:
    def __init__(self, **services: Any) -> None:
        self._services = dict(services)

    def get(self, name: str) -> Any:
        return self._services.get(name)

    def register(self, name: str, value: Any) -> None:
        self._services[name] = value


def _activate(context: _Context, tmp_path, errors: list):  # type: ignore[no-untyped-def]
    user_data = tmp_path / "userdata"
    user_data.mkdir(exist_ok=True)
    return activate_orchestration(
        context,
        config_path=tmp_path / "config.toml",
        user_data=user_data,
        broadcast_targets=lambda: [],
        record_startup_error=lambda name, error: errors.append((name, error)),
    )


@pytest.fixture(autouse=True)
def _no_test_scenario(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.delenv("DESKPET_ORCHESTRATION_TEST_SCENARIO", raising=False)


@pytest.mark.asyncio
async def test_no_model_is_unavailable_but_not_a_startup_error(tmp_path):
    errors: list = []
    context = _Context(provider_registry=None)
    service = await _activate(context, tmp_path, errors)
    try:
        assert service is not None and context.get("orchestration") is service
        status = service.status()
        assert status["state"] == "unavailable"
        assert str(status["reason"]).startswith(NO_MODEL)
        assert errors == []
        assert context.get("orchestration_pump") is not None
    finally:
        await deactivate_orchestration(context)
    assert context.get("orchestration") is None and context.get("orchestration_pump") is None


@pytest.mark.asyncio
async def test_a_failing_activation_is_recorded_and_never_raises(tmp_path, monkeypatch):
    def broken(_user_data):  # type: ignore[no-untyped-def]
        raise RuntimeError("identity store is broken")

    monkeypatch.setattr(wiring, "_principal", broken)
    errors: list = []
    context = _Context(provider_registry=None)
    assert await _activate(context, tmp_path, errors) is None
    assert [name for name, _ in errors] == ["orchestration"]
    assert "identity store is broken" in str(errors[0][1])
    assert context.get("orchestration") is None


@pytest.mark.asyncio
async def test_a_disabled_section_is_honoured_without_an_error(tmp_path):
    (tmp_path / "config.toml").write_text("[orchestration]\nenabled = false\n", encoding="utf-8")
    errors: list = []
    context = _Context(provider_registry=None)
    service = await _activate(context, tmp_path, errors)
    try:
        assert service is not None and service.status()["state"] == "disabled"
        assert errors == []
    finally:
        await deactivate_orchestration(context)


class _Registry:
    def get_chain(self) -> list[dict]:
        return [{"id": "primary", "base_url": "http://127.0.0.1:28181/v1", "model": "deepseek-v4.1-flash"}]

    def resolve_api_key(self, _provider_id: str) -> str:
        return "test-key-not-real"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("section", "expected"),
    [
        ('response_model_aliases = "deepseek-ai/DeepSeek-V4.1-Flash"\n', ("deepseek-ai/DeepSeek-V4.1-Flash",)),
        ("", ()),
    ],
)
async def test_the_declared_relay_echo_alias_reaches_the_app_provider(tmp_path, monkeypatch, section, expected):
    """2026-09-25 native UI run: the app path must carry the relay's declared echo name,
    or every call's usage stays untrusted; an undeclared alias is never guessed."""

    seen: list = []

    def capture(snapshot, **_kwargs):  # type: ignore[no-untyped-def]
        seen.append(snapshot)
        raise wiring.ProviderUnavailable("captured")

    monkeypatch.setattr(wiring, "build_provider", capture)
    (tmp_path / "config.toml").write_text("[orchestration]\n" + section, encoding="utf-8")
    errors: list = []
    context = _Context(provider_registry=_Registry())
    service = await _activate(context, tmp_path, errors)
    try:
        assert errors == [] and len(seen) == 1
        assert seen[0].response_model_aliases == expected
    finally:
        await deactivate_orchestration(context)
