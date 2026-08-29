from __future__ import annotations

import json

import pytest


class _RegistryProbe:
    def __init__(self) -> None:
        self.ephemeral: list[dict] = []
        self.durable: list[dict] = []

    def list_providers(self) -> list[dict]:
        return []

    async def add_ephemeral_provider(self, fields: dict):
        self.ephemeral.append(fields)

    async def add_provider(self, fields: dict):
        self.durable.append(fields)


@pytest.mark.asyncio
async def test_runtime_seed_uses_process_only_provider_for_injected_key(
    tmp_path, monkeypatch
):
    import main

    runtime_path = tmp_path / "llm_runtime.json"
    runtime_path.write_text(
        json.dumps(
            {"base_url": "https://relay.example/v1", "model": "runtime-model"}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "LLM_RUNTIME_PATH", runtime_path)
    monkeypatch.setenv("DESKPET_CLOUD_API_KEY", "process-secret")
    registry = _RegistryProbe()

    await main._seed_registry_from_runtime_overrides(registry)

    assert registry.durable == []
    assert len(registry.ephemeral) == 1
    assert registry.ephemeral[0]["api_key"] == "process-secret"
    assert registry.ephemeral[0]["default_model"] == "runtime-model"
