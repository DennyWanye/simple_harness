"""LC1/2/3: actual Host create/read/restart, no paid provider calls."""

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from deskpet.orchestration.provider import ProviderSnapshot
from deskpet.orchestration.service import (
    OrchestrationRequestError,
    OrchestrationService,
)
from deskpet.orchestration.settings import OrchestrationSettings

from ._support import notes_provider, notes_request


@pytest.fixture
def long_runtime(monkeypatch, tmp_path):
    path = os.environ.get("DEEPSEEK_TOKENIZER_PATH")
    if not path:
        pytest.skip("requires pinned DeepSeek tokenizer")
    monkeypatch.setenv("DESKPET_SDK_RUNTIME_MODE", "editable-source")
    monkeypatch.setenv("DESKPET_ORCH_TOKENIZER_PATH", str(Path(path).resolve()))
    import simple_harness
    from deskpet.sdk_adapters.runtime_paths import capture_sdk_source_attestation

    attestation = tmp_path / "sdk-attestation.json"
    attestation.write_text(
        json.dumps(
            capture_sdk_source_attestation(
                Path(simple_harness.__file__).resolve().parents[2],
            )
        )
    )
    monkeypatch.setenv("DESKPET_SDK_SOURCE_ATTESTATION", str(attestation))

    async def no_execution(self):
        return {"ok": False, "reason": "read-only creation test"}

    monkeypatch.setattr(OrchestrationService, "_probe_sandbox", no_execution)


def service(root, principal, settings=None):
    provider = notes_provider()
    provider.model = "deepseek-flash"
    return OrchestrationService(
        root,
        settings or OrchestrationSettings(),
        provider=provider,
        principal=principal,
        drive=False,
        provider_snapshot=ProviderSnapshot(
            "deepseek",
            "https://api.deepseek.com",
            "deepseek-flash",
            "deepseek-flash",
            "unused-local",
        ),
    )


def request(key, **extra):
    result = notes_request(key)
    result.pop("budget", None)
    return {**result, **extra}


@pytest.mark.asyncio
async def test_default_and_selected_capacity_persist_and_retry_across_default_change(
    orchestration_root,
    principal,
    long_runtime,
):
    first = service(orchestration_root, principal)
    await first.start()
    try:
        assert first.status()["available"], first.status()["reason"]
        profiles = first.status()["context_profiles"]
        assert [p["max_input_tokens"] for p in profiles] == [262144, 524288]
        a = first.create_mission(request("long-default"))
        b = first.create_mission(
            request("long-selected", runtime_profile_id="deepseek-context-512k-v1")
        )
        for receipt, tokens, budget in [(a, 262144, 20_000_000), (b, 524288, 20_000_000)]:
            detail = first.mission_detail(receipt["mission_id"])
            assert detail["runtime_context"]["max_input_tokens"] == tokens
            assert detail["mission"]["budget"]["max_tokens"] == budget
        before = first.mission_detail(a["mission_id"])["runtime_context"]
    finally:
        await first.close()
    cold = service(
        orchestration_root,
        principal,
        replace(OrchestrationSettings(), context_input_tokens=524288),
    )
    await cold.start()
    try:
        assert cold.status()["available"], cold.status()["reason"]
        retry = cold.create_mission(request("long-default"))
        assert retry == {**a, "created": False}
        assert cold.mission_detail(a["mission_id"])["runtime_context"] == before
        new = cold.create_mission(request("new-default"))
        assert (
            cold.mission_detail(new["mission_id"])["runtime_context"][
                "max_input_tokens"
            ]
            == 524288
        )
        with pytest.raises(OrchestrationRequestError) as err:
            cold.create_mission(
                request("long-default", runtime_profile_id="deepseek-context-512k-v1")
            )
        assert err.value.code == "conflict"
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_old_blank_request_survives_upgrade_and_unknown_capacity_is_atomic(
    orchestration_root,
    principal,
    long_runtime,
    monkeypatch,
):
    # Seed the same real-model deployment's original default pool, without long options.
    import deskpet.orchestration.service as module
    from deskpet.orchestration.runtime_profile import source_runtime_options

    def legacy(config, provider, snapshot):
        options = source_runtime_options(config, provider, snapshot)
        options["profiles"] = {"default": options["profiles"]["default"]}
        options["provider_token_estimator"] = options.pop("provider_token_estimators")[
            "default"
        ]
        return options

    with monkeypatch.context() as scoped:
        scoped.setattr(module, "source_runtime_options", legacy)
        old = service(orchestration_root, principal)
        await old.start()
        try:
            assert old.status()["available"], old.status()["reason"]
            receipt = old.create_mission(request("old"))
            assert (
                old.mission_detail(receipt["mission_id"])["runtime_context"][
                    "max_input_tokens"
                ]
                == 32768
            )
            assert await old.drain(timeout=15)
            assert old._effective_provider.calls > 0
            frozen_intents = [
                tuple(row)
                for row in old._orchestrator.store.connection.execute(
                    "SELECT intent_id, config_json FROM dispatch_intents ORDER BY intent_id"
                ).fetchall()
            ]
            assert frozen_intents
        finally:
            await old.close()
    upgraded = service(orchestration_root, principal)
    await upgraded.start()
    try:
        assert upgraded.status()["available"], upgraded.status()["reason"]
        assert upgraded.create_mission(request("old")) == {**receipt, "created": False}
        assert [
            tuple(row)
            for row in upgraded._orchestrator.store.connection.execute(
                "SELECT intent_id, config_json FROM dispatch_intents ORDER BY intent_id"
            ).fetchall()
        ] == frozen_intents
        assert upgraded._effective_provider.calls == 0
        assert (
            upgraded.mission_detail(receipt["mission_id"])["mission"]["budget"][
                "max_tokens"
            ]
            == 400000
        )
        for bad in ("default", "missing", 524288, None):
            with pytest.raises(OrchestrationRequestError):
                upgraded.create_mission(request("bad", runtime_profile_id=bad))
        assert len(upgraded.list_missions()) == 1
    finally:
        await upgraded.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("tokens", [262144, 524288])
async def test_selected_capacity_executes_all_roles_and_cold_reopen_does_not_call_again(
    orchestration_root,
    principal,
    long_runtime,
    tokens,
):
    from agent_orchestrator.testing.fixtures import graph_proposal_step

    from ._support import NOTES_TASK

    identifier = f"deepseek-context-{tokens // 1024}k-v1"
    app = service(orchestration_root, principal)
    app._provider.scripts["planner"] = [
        graph_proposal_step(
            [{**NOTES_TASK, "budget": {"max_tokens": 1500000, "max_attempts": 2}}]
        )
    ]
    await app.start()
    try:
        assert app.status()["available"], app.status()["reason"]
        receipt = app.create_mission(
            request("long-complete", runtime_profile_id=identifier)
        )
        assert await app.drain(timeout=15)
        detail = app.mission_detail(receipt["mission_id"])
        assert detail["mission"]["status"] == "COMPLETED", detail["mission"]
        assert detail["runtime_context"]["max_input_tokens"] == tokens
        intents = [
            json.loads(r[0])
            for r in app._orchestrator.store.connection.execute(
                "SELECT config_json FROM dispatch_intents"
            ).fetchall()
        ]
        assert intents and all(i["runtime_profile_id"] == identifier for i in intents)
        assert all(
            i["runtime_context"]["policy"]["max_input_tokens"] == tokens
            for i in intents
        )
        fingerprint = {i["provider_admission_fingerprint"] for i in intents}
        assert len(fingerprint) == 1
        assert app._effective_provider.calls >= 4
    finally:
        await app.close()
    cold = service(orchestration_root, principal)
    await cold.start()
    try:
        assert cold.status()["available"], cold.status()["reason"]
        assert await cold.drain(timeout=5)
        assert cold._effective_provider.calls == 0
        assert (
            cold.mission_detail(receipt["mission_id"])["runtime_context"]
            == detail["runtime_context"]
        )
    finally:
        await cold.close()
