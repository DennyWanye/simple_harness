# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P5-S2 Phase 1: LLMProviderRegistry tests (TDD).

Covers:
  - Registry CRUD: add / remove / reorder / set_enabled / list / get_chain
  - Validation: kebab-case id, uniqueness, redacted api_key
  - Migration: legacy [llm.local] → [[llm.endpoints]], idempotent, broken keychain
  - Persistence: toml round-trip via _persist_to_toml + atomic write
  - Keychain: api_key written to keyring, never returned in plaintext

Mocks `keyring` at module-import time via fixture so no host credential
store is touched. Uses tmp_path for the toml file.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest


# ───────────────────────── fixtures ─────────────────────────


class _FakeKeyring:
    """In-memory drop-in for the `keyring` module used by provider_registry."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.delete_log: list[tuple[str, str]] = []

    def set_password(self, service: str, account: str, password: str) -> None:
        self.store[(service, account)] = password

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def delete_password(self, service: str, account: str) -> None:
        self.delete_log.append((service, account))
        self.store.pop((service, account), None)


@pytest.fixture
def fake_keyring(monkeypatch):
    """Replace the keyring module inside provider_registry with a fake."""
    from llm import provider_registry

    fake = _FakeKeyring()
    monkeypatch.setattr(provider_registry, "keyring", fake, raising=False)
    monkeypatch.setattr(provider_registry, "_KEYRING_AVAILABLE", True, raising=False)
    return fake


@pytest.fixture
def empty_toml(tmp_path: Path) -> Path:
    """A config.toml with no llm section at all."""
    p = tmp_path / "config.toml"
    p.write_text(
        textwrap.dedent(
            """
            schema_version = 1

            [backend]
            host = "127.0.0.1"
            port = 8100
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def legacy_toml(tmp_path: Path) -> Path:
    """A config.toml with legacy [llm.local] schema (single provider)."""
    p = tmp_path / "config.toml"
    p.write_text(
        textwrap.dedent(
            """
            schema_version = 1

            [backend]
            host = "127.0.0.1"

            [llm.local]
            base_url = "https://api.your-llm-relay.example.com/v1"
            model = "relay-deepseek-v3"
            api_key = "from-keychain"
            temperature = 0.7
            max_tokens = 2048
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def providers_toml(tmp_path: Path) -> Path:
    """A config.toml already on the new [[llm.endpoints]] schema."""
    p = tmp_path / "config.toml"
    p.write_text(
        textwrap.dedent(
            """
            schema_version = 1

            [[llm.endpoints]]
            id = "relay-deepseek"
            name = "Relay DeepSeek"
            base_url = "https://api.your-llm-relay.example.com/v1"
            model = "deepseek-v3"
            api_key_ref = "deskpet.provider.relay-deepseek"
            priority = 1
            enabled = true
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return p


# ───────────────────────── 1.1: Registry CRUD ─────────────────────────


def _make_provider_kwargs(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "relay-deepseek",
        "name": "Relay DeepSeek",
        "base_url": "https://api.your-llm-relay.example.com/v1",
        "model": "deepseek-v3",
        "api_key": "sk-real-secret",
        "priority": 1,
        "enabled": True,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_add_provider_persists(empty_toml: Path, fake_keyring):
    """1.1 — add() persists to toml + list_providers() returns the entry."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs())

    items = reg.list_providers()
    assert len(items) == 1
    assert items[0]["id"] == "relay-deepseek"

    # toml on disk has the entry
    import tomli

    with empty_toml.open("rb") as fh:
        data = tomli.load(fh)
    providers = data["llm"]["endpoints"]
    assert len(providers) == 1
    assert providers[0]["id"] == "relay-deepseek"
    # api_key plaintext NOT written to toml
    assert "api_key" not in providers[0]
    assert providers[0]["api_key_ref"] == (
        "com.dennywanye.simpleharness.provider.relay-deepseek"
    )

    # keychain has it
    assert fake_keyring.get_password(
        "com.dennywanye.simpleharness", "provider.relay-deepseek"
    ) == "sk-real-secret"


@pytest.mark.asyncio
async def test_remove_provider(empty_toml: Path, fake_keyring):
    """1.2 — remove() drops from list + deletes keychain entry."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="a"))
    await reg.add_provider(_make_provider_kwargs(id="b", priority=2))

    await reg.remove_provider("a")

    items = reg.list_providers()
    assert len(items) == 1
    assert items[0]["id"] == "b"

    # keychain entry for 'a' was deleted
    assert (
        "com.dennywanye.simpleharness", "provider.a"
    ) in fake_keyring.delete_log
    assert fake_keyring.get_password(
        "com.dennywanye.simpleharness", "provider.a"
    ) is None


@pytest.mark.asyncio
async def test_reorder_changes_chain(empty_toml: Path, fake_keyring):
    """1.3 — reorder() updates priorities + get_chain() returns new order."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="a", priority=1))
    await reg.add_provider(_make_provider_kwargs(id="b", priority=2))
    await reg.add_provider(_make_provider_kwargs(id="c", priority=3))

    await reg.reorder(["c", "a", "b"])

    chain_ids = [p["id"] for p in reg.get_chain()]
    assert chain_ids == ["c", "a", "b"]


@pytest.mark.asyncio
async def test_set_enabled_false_removes_from_chain(empty_toml: Path, fake_keyring):
    """1.4 — disabling provider keeps it in list but removes from chain."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="a", priority=1))
    await reg.add_provider(_make_provider_kwargs(id="b", priority=2))

    await reg.set_enabled("b", False)

    assert len(reg.list_providers()) == 2
    chain_ids = [p["id"] for p in reg.get_chain()]
    assert chain_ids == ["a"]


@pytest.mark.asyncio
async def test_get_chain_empty_raises_no_provider_configured(empty_toml: Path, fake_keyring):
    """1.5 — empty registry: get_chain() raises NoProviderConfiguredError."""
    from llm.provider_registry import LLMProviderRegistry, NoProviderConfiguredError

    reg = LLMProviderRegistry(empty_toml)
    with pytest.raises(NoProviderConfiguredError):
        reg.get_chain()


@pytest.mark.asyncio
async def test_process_only_provider_routes_without_persisting_or_keychain(
    empty_toml: Path, fake_keyring
):
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    entry = await reg.add_ephemeral_provider(
        {
            "id": "primary",
            "name": "runtime relay",
            "base_url": "https://relay.example/v1",
            "models": ["runtime-model"],
            "default_model": "runtime-model",
            "api_key": "process-secret",
        }
    )

    assert entry.source == "runtime-env"
    assert reg.get_chain()[0]["id"] == "primary"
    assert reg.resolve_api_key("primary") == "process-secret"
    assert fake_keyring.store == {}
    assert "[[llm.endpoints]]" not in empty_toml.read_text(encoding="utf-8")
    assert LLMProviderRegistry(empty_toml).list_providers() == []


@pytest.mark.asyncio
async def test_get_chain_filters_disabled(empty_toml: Path, fake_keyring):
    """1.6 — get_chain() excludes disabled providers."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="a", priority=1, enabled=True))
    await reg.add_provider(_make_provider_kwargs(id="b", priority=2, enabled=False))
    await reg.add_provider(_make_provider_kwargs(id="c", priority=3, enabled=True))

    chain_ids = [p["id"] for p in reg.get_chain()]
    assert chain_ids == ["a", "c"]


@pytest.mark.asyncio
async def test_get_chain_stable_on_equal_priority(empty_toml: Path, fake_keyring):
    """1.7 — equal priorities: get_chain() falls back to insertion order."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="x", priority=2))
    await reg.add_provider(_make_provider_kwargs(id="y", priority=2))
    await reg.add_provider(_make_provider_kwargs(id="z", priority=2))

    chain_ids = [p["id"] for p in reg.get_chain()]
    assert chain_ids == ["x", "y", "z"]


@pytest.mark.asyncio
async def test_list_providers_redacts_api_key(empty_toml: Path, fake_keyring):
    """1.8 — list_providers() returns api_key='********', not plaintext."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="a"))

    items = reg.list_providers()
    assert items[0]["api_key"] == "********"
    # Internal ref preserved
    assert items[0]["api_key_ref"] == (
        "com.dennywanye.simpleharness.provider.a"
    )
    # Real key is in fake keyring, never returned
    assert "sk-real-secret" not in str(items)


def test_resolve_api_key_migrates_only_matching_legacy_provider(
    empty_toml: Path, fake_keyring
):
    """A config-owned provider may copy its old secret into the app namespace."""
    from llm.provider_registry import LLMProviderRegistry

    empty_toml.write_text(
        """
[[llm.endpoints]]
id = "deepseeker-myself"
name = "DeepSeek"
base_url = "https://api.deepseek.com"
models = ["deepseek-v4-pro"]
default_model = "deepseek-v4-pro"
api_key_ref = "deskpet.provider.deepseeker-myself"
enabled = true
incarnation_id = "incarnation-a"
config_revision = 1
""".lstrip(),
        encoding="utf-8",
    )
    fake_keyring.set_password(
        "deskpet", "provider.deepseeker-myself", "sk-legacy"
    )
    fake_keyring.set_password(
        "deskpet", "provider.some-other-app", "sk-foreign"
    )

    reg = LLMProviderRegistry(empty_toml)

    assert reg.resolve_api_key("deepseeker-myself") == "sk-legacy"
    assert (
        "api_key_ref = \"com.dennywanye.simpleharness.provider.deepseeker-myself\""
        in empty_toml.read_text(encoding="utf-8")
    )
    assert fake_keyring.get_password(
        "com.dennywanye.simpleharness",
        "provider.deepseeker-myself.incarnation-a.1",
    ) == "sk-legacy"
    assert fake_keyring.get_password(
        "com.dennywanye.simpleharness", "provider.deepseeker-myself"
    ) == "sk-legacy"
    assert fake_keyring.get_password(
        "com.dennywanye.simpleharness", "provider.some-other-app"
    ) is None
    assert fake_keyring.get_password(
        "deskpet", "provider.some-other-app"
    ) == "sk-foreign"


@pytest.mark.asyncio
async def test_add_provider_unique_id_validation(empty_toml: Path, fake_keyring):
    """1.9 — duplicate id raises ValueError."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="dup"))

    with pytest.raises(ValueError, match="already exists|duplicate|unique"):
        await reg.add_provider(_make_provider_kwargs(id="dup"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_id",
    [
        "UpperCase",
        "snake_case",
        "with spaces",
        "trailing-",
        "-leading",
        "double--dash",
        "",
        "x" * 33,  # > 32 chars
        "with.dot",
        "with/slash",
    ],
)
async def test_add_provider_kebab_case_validation(empty_toml: Path, fake_keyring, bad_id: str):
    """1.10 — non-kebab-case id raises ValueError."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    with pytest.raises(ValueError, match="kebab-case|invalid id|invalid provider id"):
        await reg.add_provider(_make_provider_kwargs(id=bad_id))


# ───────────────────────── 1.3: Migration ─────────────────────────


def test_migrate_legacy_llm_local_to_providers(legacy_toml: Path, fake_keyring):
    """1.14 — legacy [llm.local] is converted to [[llm.endpoints]] with one entry."""
    from llm.provider_registry import _migrate_legacy_provider_config

    # Pre-populate keychain with the existing cloud key so the migration can
    # reference it (matches design.md: api_key_ref = "deskpet.cloud_api_key").
    fake_keyring.set_password("deskpet", "cloud_api_key", "existing-cloud-key")

    _migrate_legacy_provider_config(legacy_toml)

    import tomli

    with legacy_toml.open("rb") as fh:
        data = tomli.load(fh)
    providers = data["llm"]["endpoints"]
    assert len(providers) == 1
    entry = providers[0]
    assert entry["id"] == "legacy-default"
    assert entry["base_url"] == "https://api.your-llm-relay.example.com/v1"
    # v2 schema: models is the canonical array
    assert entry["models"] == ["relay-deepseek-v3"]
    assert entry.get("default_model") == "relay-deepseek-v3"
    assert entry["api_key_ref"] == "deskpet.cloud_api_key"
    assert entry["priority"] == 1
    assert entry["enabled"] is True
    assert "api_key" not in entry  # never plaintext


def test_migration_idempotent(providers_toml: Path, fake_keyring):
    """1.15 — already migrated toml stays unchanged on second migration call."""
    from llm.provider_registry import _migrate_legacy_provider_config

    before = providers_toml.read_text(encoding="utf-8")
    _migrate_legacy_provider_config(providers_toml)
    after = providers_toml.read_text(encoding="utf-8")

    # Content semantically unchanged: parse both and compare.
    import tomli

    before_data = tomli.loads(before)
    after_data = tomli.loads(after)
    assert before_data["llm"]["endpoints"] == after_data["llm"]["endpoints"]
    # And specifically: still exactly 1 entry, not 2 (no duplicate legacy-default).
    assert len(after_data["llm"]["endpoints"]) == 1
    assert after_data["llm"]["endpoints"][0]["id"] == "relay-deepseek"


def test_migration_handles_missing_keychain_key(legacy_toml: Path, fake_keyring, caplog):
    """1.16 — migration creates entry + warning even when keychain key missing."""
    import logging

    from llm.provider_registry import _migrate_legacy_provider_config

    # keychain intentionally NOT populated → resolve will return None
    with caplog.at_level(logging.WARNING, logger="deskpet.llm.provider_registry"):
        _migrate_legacy_provider_config(legacy_toml)

    import tomli

    with legacy_toml.open("rb") as fh:
        data = tomli.load(fh)
    providers = data["llm"]["endpoints"]
    assert len(providers) == 1
    assert providers[0]["api_key_ref"] == "deskpet.cloud_api_key"

    # warning was emitted
    msgs = [r.getMessage() for r in caplog.records]
    assert any("not found" in m or "missing" in m or "re-enter" in m for m in msgs), msgs


def test_migration_no_op_on_fresh_install(empty_toml: Path, fake_keyring):
    """Fresh install (no llm section at all): migration is no-op, no crash."""
    from llm.provider_registry import _migrate_legacy_provider_config

    before = empty_toml.read_text(encoding="utf-8")
    _migrate_legacy_provider_config(empty_toml)
    after = empty_toml.read_text(encoding="utf-8")

    import tomli

    after_data = tomli.loads(after)
    assert "providers" not in after_data.get("llm", {})


# ───────────────────────── relay-managed provider upsert ─────────────────────────


@pytest.mark.asyncio
async def test_source_and_account_ref_roundtrip_toml(empty_toml: Path, fake_keyring):
    """Relay-managed metadata persists to toml and reloads into public dicts."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(
        _make_provider_kwargs(
            id="relay-cloud",
            source="relay",
            account_ref="acct-123",
        )
    )

    reloaded = LLMProviderRegistry(empty_toml)
    items = reloaded.list_providers()
    assert items[0]["source"] == "relay"
    assert items[0]["account_ref"] == "acct-123"

    import tomli

    with empty_toml.open("rb") as fh:
        data = tomli.load(fh)
    provider = data["llm"]["endpoints"][0]
    assert provider["source"] == "relay"
    assert provider["account_ref"] == "acct-123"


@pytest.mark.asyncio
async def test_user_provider_omits_source_account_lines(empty_toml: Path, fake_keyring):
    """Manual providers keep byte-level TOML shape: no default metadata lines."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="manual-one"))

    text = empty_toml.read_text(encoding="utf-8")
    assert "\nsource = " not in text
    assert "\naccount_ref = " not in text


@pytest.mark.asyncio
async def test_ensure_provider_idempotent(empty_toml: Path, fake_keyring):
    """ensure_provider inserts once, then updates the same relay row."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    first = await reg.ensure_provider(
        _make_provider_kwargs(
            id="relay-cloud",
            source="relay",
            account_ref="acct-a",
        )
    )
    second = await reg.ensure_provider(
        {
            "id": "relay-cloud",
            "name": "Relay Cloud Updated",
            "base_url": "https://relay.example.com/v2",
            "models": ["gpt-4o-mini"],
            "source": "relay",
            "account_ref": "acct-b",
        }
    )

    assert first.id == second.id == "relay-cloud"
    items = reg.list_providers()
    assert [p["id"] for p in items] == ["relay-cloud"]
    assert items[0]["name"] == "Relay Cloud Updated"
    assert items[0]["account_ref"] == "acct-b"


@pytest.mark.asyncio
async def test_ensure_preserves_user_reorder(empty_toml: Path, fake_keyring):
    """Relay priority steal keeps existing manual relative order intact."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="manual-a", priority=1))
    await reg.add_provider(_make_provider_kwargs(id="manual-b", priority=2))
    await reg.reorder(["manual-b", "manual-a"])

    relay_payload = _make_provider_kwargs(
        id="relay-cloud",
        source="relay",
        account_ref="acct",
    )
    relay_payload.pop("priority")
    await reg.ensure_provider(relay_payload)

    chain_ids = [p["id"] for p in reg.get_chain()]
    assert chain_ids == ["relay-cloud", "manual-b", "manual-a"]


@pytest.mark.asyncio
async def test_ensure_updates_key(empty_toml: Path, fake_keyring):
    """ensure_provider rewrites keychain when api_key is supplied."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.ensure_provider(
        _make_provider_kwargs(
            id="relay-cloud",
            api_key="sk-old",
            source="relay",
            account_ref="acct",
        )
    )
    await reg.ensure_provider(
        {
            "id": "relay-cloud",
            "api_key": "sk-new",
            "source": "relay",
            "account_ref": "acct",
        }
    )

    assert reg.resolve_api_key("relay-cloud") == "sk-new"


@pytest.mark.asyncio
async def test_ensure_reenables_existing_relay_provider(empty_toml: Path, fake_keyring):
    """A fresh relay login must turn a previously disabled managed row back on."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.ensure_provider(
        _make_provider_kwargs(
            id="relay-cloud",
            api_key="sk-old",
            source="relay",
            account_ref="acct",
            enabled=False,
        )
    )

    await reg.ensure_provider(
        {
            "id": "relay-cloud",
            "api_key": "sk-new",
            "source": "relay",
            "account_ref": "acct",
            "enabled": True,
        }
    )

    providers = {p["id"]: p for p in reg.list_providers()}
    assert providers["relay-cloud"]["enabled"] is True
    assert reg.get_chain()[0]["id"] == "relay-cloud"
    assert reg.resolve_api_key("relay-cloud") == "sk-new"


@pytest.mark.asyncio
async def test_ensure_first_login_steals_default_priority(empty_toml: Path, fake_keyring):
    """First relay login becomes the default provider ahead of manual rows."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="manual-one", priority=1))

    relay_payload = _make_provider_kwargs(
        id="relay-cloud",
        source="relay",
        account_ref="acct",
    )
    relay_payload.pop("priority")
    await reg.ensure_provider(relay_payload)

    public = reg.list_providers()
    assert {p["id"]: p["priority"] for p in public} == {
        "manual-one": 2,
        "relay-cloud": 1,
    }
    assert [p["id"] for p in reg.get_chain()] == ["relay-cloud", "manual-one"]


@pytest.mark.asyncio
async def test_ensure_raises_key_missing_when_keychain_empty(empty_toml: Path, fake_keyring):
    """Existing managed provider without a stored key asks caller to recover."""
    from llm.provider_registry import KeyMissingError, LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.ensure_provider(
        _make_provider_kwargs(
            id="relay-cloud",
            source="relay",
            account_ref="acct",
        )
    )
    entry = reg.get_entry("relay-cloud")
    assert entry is not None
    fake_keyring.delete_password(
        "com.dennywanye.simpleharness",
        f"provider.relay-cloud.{entry.incarnation_id}.{entry.config_revision}",
    )
    fake_keyring.delete_password(
        "com.dennywanye.simpleharness", "provider.relay-cloud"
    )

    with pytest.raises(KeyMissingError) as excinfo:
        await reg.ensure_provider(
            {
                "id": "relay-cloud",
                "source": "relay",
                "account_ref": "acct",
            }
        )

    assert excinfo.value.provider_id == "relay-cloud"


@pytest.mark.asyncio
async def test_ensure_updates_base_url_models(empty_toml: Path, fake_keyring):
    """ensure_provider updates mutable endpoint metadata on existing rows."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.ensure_provider(
        _make_provider_kwargs(
            id="relay-cloud",
            base_url="https://relay.example.com/v1",
            model="old-model",
            source="relay",
            account_ref="acct",
        )
    )
    await reg.ensure_provider(
        {
            "id": "relay-cloud",
            "base_url": "https://relay.example.com/v2",
            "models": ["new-a", "new-b"],
            "default_model": "new-b",
            "source": "relay",
            "account_ref": "acct",
        }
    )

    item = reg.list_providers()[0]
    assert item["base_url"] == "https://relay.example.com/v2"
    assert item["models"] == ["new-a", "new-b"]
    assert item["default_model"] == "new-b"
    assert item["model"] == "new-b"


@pytest.mark.asyncio
async def test_cache_discovered_models_persists_without_invalidating_bindings(
    empty_toml: Path,
    fake_keyring,
):
    """A successful /models result becomes the restart fallback cache."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(
        _make_provider_kwargs(
            id="relay-cloud",
            base_url="https://relay.example.com/v1",
            models=["sf-glm-5.2"],
            default_model="sf-glm-5.2",
        )
    )
    before = reg.get_entry("relay-cloud")
    assert before is not None

    changed = await reg.cache_discovered_models(
        "relay-cloud",
        ["sf-glm-5.2", "kimi-k3", "kimi-k3", "  gpt-5.5  "],
        expected_incarnation_id=before.incarnation_id,
        expected_config_revision=before.config_revision,
        expected_base_url=before.base_url,
    )

    assert changed is True
    current = reg.get_entry("relay-cloud")
    assert current is not None
    assert current.models == ["sf-glm-5.2", "kimi-k3", "gpt-5.5"]
    assert current.default_model == "sf-glm-5.2"
    assert current.config_revision == before.config_revision
    reloaded = LLMProviderRegistry(empty_toml).get_entry("relay-cloud")
    assert reloaded is not None
    assert reloaded.models == current.models
    assert reloaded.default_model == "sf-glm-5.2"

    # Idempotent refreshes do not rewrite the file.
    mtime = empty_toml.stat().st_mtime_ns
    changed_again = await reg.cache_discovered_models(
        "relay-cloud",
        list(current.models),
        expected_incarnation_id=current.incarnation_id,
        expected_config_revision=current.config_revision,
        expected_base_url=current.base_url,
    )
    assert changed_again is False
    assert empty_toml.stat().st_mtime_ns == mtime


@pytest.mark.asyncio
async def test_cache_discovered_models_never_changes_missing_default(
    empty_toml: Path,
    fake_keyring,
):
    """Catalog refresh must not silently replace an explicit default model."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(
        _make_provider_kwargs(
            id="relay-cloud",
            models=["kimi-k3"],
            default_model="kimi-k3",
        )
    )
    before = reg.get_entry("relay-cloud")
    assert before is not None

    changed = await reg.cache_discovered_models(
        "relay-cloud",
        ["gpt-5.5"],
        expected_incarnation_id=before.incarnation_id,
        expected_config_revision=before.config_revision,
        expected_base_url=before.base_url,
    )

    assert changed is False
    assert reg.get_entry("relay-cloud").models == ["kimi-k3"]
    assert reg.get_entry("relay-cloud").default_model == "kimi-k3"


@pytest.mark.asyncio
async def test_normalize_priorities_unique(empty_toml: Path, fake_keyring):
    """_normalize_priorities rewrites stable priority order to 1..N."""
    from llm.provider_registry import LLMProviderRegistry

    reg = LLMProviderRegistry(empty_toml)
    await reg.add_provider(_make_provider_kwargs(id="a", priority=5))
    await reg.add_provider(_make_provider_kwargs(id="b", priority=5))
    await reg.add_provider(_make_provider_kwargs(id="c", priority=3))

    reg._normalize_priorities()

    items = sorted(reg.list_providers(), key=lambda p: p["priority"])
    assert [(p["id"], p["priority"]) for p in items] == [
        ("c", 1),
        ("a", 2),
        ("b", 3),
    ]


def test_ws_settings_provider_messages_are_wired():
    """守住 provider CRUD 的 WS 接线面，并断言 relay 消息已彻底退役。

    2026-08-09：relay 托管登录移除后，`settings_providers_ensure` /
    `settings_providers_relay_logout` 两个消息与 llm.relay_provider_ops
    一并删除。这条测试从"确认 relay 已接线"翻转为"确认 relay 不再存在"，
    防止后续有人把托管登录的旁路悄悄接回来。
    """
    main_py = Path(__file__).resolve().parents[1] / "main.py"
    text = main_py.read_text(encoding="utf-8")

    # 手动 provider 的 CRUD 面必须仍在
    for msg in (
        '"settings_providers_list_request"',
        '"settings_providers_add"',
        '"settings_providers_update"',
        '"settings_providers_remove"',
        '"settings_providers_reorder"',
    ):
        assert msg in text, f"manual provider CRUD 消息缺失: {msg}"

    # relay 面必须零残留
    for gone in (
        "settings_providers_relay_logout",
        "settings_providers_ensure",
        "relay_provider_ops",
        "ensure_relay_provider",
        "RegistryRelayAuthSnapshotProvider",
    ):
        assert gone not in text, f"relay 残留未清除: {gone}"

    # 身份走本地 provider
    assert "LocalAuthSnapshotProvider" in text
