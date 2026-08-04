from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.memory.migrator import DEFAULT_MIGRATIONS_DIR, MigrationError, run_migrations
from deskpet.memory.session_db import ProviderBindingConflict, SessionDB
from deskpet.harness.context import HostContextFactory
from llm.provider_registry import (
    LLMProviderRegistry,
    ProviderMutationConflict,
)
from llm.resolution import (
    ProviderRoutingReadiness,
    SessionProviderUnavailable,
    resolve_session_provider_route,
)


class _FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    import llm.provider_registry as module

    fake = _FakeKeyring()
    monkeypatch.setattr(module, "keyring", fake)
    monkeypatch.setattr(module, "_KEYRING_AVAILABLE", True)
    return fake


def _fields(provider_id: str = "relay") -> dict[str, object]:
    return {
        "id": provider_id,
        "name": provider_id,
        "base_url": "https://example.invalid/v1",
        "models": ["kimi-k3", "glm-5.2"],
        "default_model": "kimi-k3",
        "api_key": "secret-v1",
    }


@pytest.mark.asyncio
async def test_binding_epoch_cas_prevents_set_clear_set_aba(tmp_path: Path) -> None:
    db = SessionDB(tmp_path / "state.db")
    await db.initialize()
    first = await db.set_session_provider_binding(
        "session",
        "relay",
        "kimi-k3",
        provider_incarnation_id="inc-1",
        provider_config_revision=1,
        expected_binding_epoch=0,
    )
    assert first["binding_epoch"] == 1
    cleared = await db.set_session_provider_binding(
        "session", None, None, expected_binding_epoch=1
    )
    assert cleared["binding_epoch"] == 2
    with pytest.raises(ProviderBindingConflict):
        await db.set_session_provider_binding(
            "session",
            "relay",
            "glm-5.2",
            provider_incarnation_id="inc-1",
            provider_config_revision=1,
            expected_binding_epoch=1,
        )
    final = await db.set_session_provider_binding(
        "session",
        "relay",
        "glm-5.2",
        provider_incarnation_id="inc-1",
        provider_config_revision=1,
        expected_binding_epoch=2,
    )
    assert final["binding_epoch"] == 3
    assert (await db.get_session_provider_binding_authority("session")) == final


@pytest.mark.asyncio
async def test_registry_failed_toml_commit_does_not_replace_old_secret(
    tmp_path: Path,
    fake_keyring: _FakeKeyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text("schema_version = 1\n", encoding="utf-8")
    registry = LLMProviderRegistry(config)
    old = await registry.add_provider(_fields())
    assert fake_keyring.get_password("deskpet", "provider.relay") == "secret-v1"

    def fail_commit(_candidate):
        raise OSError("replace failed")

    monkeypatch.setattr(registry, "_persist_entries_to_toml", fail_commit)
    with pytest.raises(OSError, match="replace failed"):
        await registry.update_provider(
            "relay",
            expected_incarnation_id=old.incarnation_id,
            expected_config_revision=old.config_revision,
            api_key="secret-v2",
        )
    current = registry.get_entry("relay")
    assert current is not None and current.config_revision == 1
    assert fake_keyring.get_password("deskpet", "provider.relay") == "secret-v1"
    assert registry.resolve_api_key("relay") == "secret-v1"


@pytest.mark.asyncio
async def test_registry_cas_and_remove_readd_incarnation_fail_closed(
    tmp_path: Path, fake_keyring: _FakeKeyring
) -> None:
    config = tmp_path / "config.toml"
    config.write_text("schema_version = 1\n", encoding="utf-8")
    registry = LLMProviderRegistry(config)
    old = await registry.add_provider(_fields())
    with pytest.raises(ProviderMutationConflict):
        await registry.update_provider(
            "relay",
            expected_incarnation_id=old.incarnation_id,
            expected_config_revision=old.config_revision + 1,
            enabled=False,
        )

    db = SessionDB(tmp_path / "state.db")
    await db.initialize()
    await registry.set_session_binding(
        db,
        session_id="session",
        provider_id="relay",
        preferred_model="kimi-k3",
        model_params=None,
        expected_binding_epoch=0,
        expected_incarnation_id=old.incarnation_id,
        expected_config_revision=old.config_revision,
    )
    await registry.remove_provider(
        "relay",
        expected_incarnation_id=old.incarnation_id,
        expected_config_revision=old.config_revision,
    )
    new = await registry.add_provider({**_fields(), "api_key": "secret-v2"})
    assert new.incarnation_id != old.incarnation_id
    with pytest.raises(SessionProviderUnavailable, match="bound_provider_recreated"):
        await resolve_session_provider_route(
            "session", registry=registry, session_db=db
        )


@pytest.mark.asyncio
async def test_root_snapshot_route_is_immutable_after_session_change(
    tmp_path: Path, fake_keyring: _FakeKeyring
) -> None:
    config = tmp_path / "config.toml"
    config.write_text("schema_version = 1\n", encoding="utf-8")
    registry = LLMProviderRegistry(config)
    entry = await registry.add_provider(_fields())
    snapshot = SimpleNamespace(
        run_context_json=json.dumps(
            {
                "session_id": "session",
                "provider_plan": {
                    "providers": ["relay"],
                    "bindings": [
                        {
                            "provider_id": "relay",
                            "model_id": "kimi-k3",
                            "incarnation_id": entry.incarnation_id,
                            "config_revision": entry.config_revision,
                            "binding_epoch": 7,
                        }
                    ],
                },
            }
        )
    )

    class _Reader:
        async def read_run_start_snapshot(self, run_id: str):
            assert run_id == "root-1"
            return snapshot

    class _SessionMustNotBeRead:
        async def get_session_provider_binding_authority(self, _session_id: str):
            raise AssertionError("root route consulted live Session")

    route = await resolve_session_provider_route(
        "session",
        registry=registry,
        session_db=_SessionMustNotBeRead(),
        root_run_id="root-1",
        start_snapshot_reader=_Reader(),
    )
    assert route.provenance == "root_snapshot"
    assert route.model == "kimi-k3"
    assert route.binding_epoch == 7


def test_readiness_fails_closed_until_reconciliation_completes() -> None:
    readiness = ProviderRoutingReadiness()
    with pytest.raises(SessionProviderUnavailable, match="provider_routing_initializing"):
        readiness.require_ready()
    readiness.mark_ready()
    readiness.require_ready()


def test_host_context_freezes_provider_identity_and_binding_epoch() -> None:
    context = HostContextFactory().create_run_context(
        session_id="session",
        root_run_id="root",
        request_id="request",
        turn_id="turn",
        venue="text",
        capability_hash="c" * 64,
        provider_plan=("relay",),
        provider_bindings=(("relay", "kimi-k3", "inc-1", 4, 7),),
        trace_id="trace",
        principal_id="principal",
    )
    assert context.provider_plan["bindings"][0] == {
        "provider_id": "relay",
        "model_id": "kimi-k3",
        "incarnation_id": "inc-1",
        "config_revision": 4,
        "binding_epoch": 7,
    }


@pytest.mark.asyncio
async def test_v23_migration_rolls_back_partial_ddl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import deskpet.memory.migrator as module

    legacy_dir = tmp_path / "legacy-migrations"
    legacy_dir.mkdir()
    for source in DEFAULT_MIGRATIONS_DIR.glob("*.sql"):
        if source.name < "015_provider_binding_lifecycle_v23.sql":
            shutil.copy2(source, legacy_dir / source.name)
    db_path = tmp_path / "state.db"
    await run_migrations(db_path, legacy_dir)

    original = module._execute_transactional_script

    async def fail_after_first_statement(db, sql: str) -> None:
        if "provider_binding_reconcile_marker" in sql:
            await db.execute(
                "ALTER TABLE code_session_provider "
                "ADD COLUMN provider_incarnation_id TEXT"
            )
            raise sqlite3.OperationalError("fault after first DDL")
        await original(db, sql)

    monkeypatch.setattr(module, "_execute_transactional_script", fail_after_first_statement)
    with pytest.raises(MigrationError, match="015_provider_binding_lifecycle_v23"):
        await run_migrations(db_path)
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(code_session_provider)")}
        assert "provider_incarnation_id" not in columns
        assert conn.execute("PRAGMA user_version").fetchone() == (22,)
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=?",
            ("015_provider_binding_lifecycle_v23.sql",),
        ).fetchone() == (0,)


def test_migration_sql_leaves_version_to_transaction_runner() -> None:
    sql = (
        DEFAULT_MIGRATIONS_DIR / "015_provider_binding_lifecycle_v23.sql"
    ).read_text(encoding="utf-8")
    assert "PRAGMA user_version" not in sql


def test_production_entrypoints_require_authority_and_defer_registry_startup() -> None:
    main_source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
    lifespan_offset = main_source.index("async def lifespan")
    constructor_offset = main_source.index("LLMProviderRegistry(_CONFIG_PATH)")
    assert constructor_offset > lifespan_offset
    assert '"expected_binding_epoch" not in payload' in main_source
    assert '"expected_incarnation_id" not in _payload' in main_source
    assert "clear_bindings_for_provider(_pid)" not in main_source
