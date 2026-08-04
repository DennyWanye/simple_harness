from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from deskpet.capabilities.configured_catalog import (
    ConfiguredCapabilityCatalogSource,
)
from deskpet.capabilities.manager import (
    CapabilityManagerError,
    CapabilityPackManager,
)
from deskpet.capabilities.manifest import PackEnvironment, PackManifestError
from deskpet.capabilities.source import (
    CapabilitySourceResolver,
    PackSourceRequest,
)
from deskpet.capabilities.store import (
    CapabilityStore,
    initialize_capability_database,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = (
    REPO_ROOT / "capability-packs" / "fixtures" / "ultraforge-badhash"
)


class _MustNotPublish:
    async def prepare_candidate(self, *_args, **_kwargs):
        raise AssertionError("bad-hash fixture reached candidate publication")


@pytest.mark.asyncio
async def test_configured_ultraforge_is_discoverable_but_never_activated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = PackSourceRequest(
        source_type="local",
        uri=str(FIXTURE_ROOT),
        revision="fixture-badhash-v1",
    )
    catalog = await ConfiguredCapabilityCatalogSource(
        {"ultraforge": configured}
    ).snapshot()
    assert len(catalog.entries) == 1
    assert catalog.entries[0].version.capability_id == "ultraforge"
    assert catalog.entries[0].version.source.startswith(
        "configured:ultraforge@"
    )

    canary = tmp_path / "ultraforge-executed.canary"
    monkeypatch.setenv("DESKPET_ULTRAFORGE_CANARY", str(canary))
    db = await initialize_capability_database(tmp_path / "execution.db")
    store = CapabilityStore(db, clock=lambda: 100.0)
    resolver = CapabilitySourceResolver(
        configured_sources={"ultraforge": configured}
    )
    manager = CapabilityPackManager(
        store=store,
        user_data_root=tmp_path / "user-data",
        publisher=_MustNotPublish(),
        environment=PackEnvironment(
            deskpet_version="0.6.0-beta.9",
            os="windows",
            architecture="x86_64",
            python_version="3.11.9",
        ),
        source_resolver=resolver,
    )

    request = PackSourceRequest(
        source_type="configured",
        uri="ultraforge",
        revision="configured",
    )
    for _ in range(2):
        with pytest.raises(
            (PackManifestError, CapabilityManagerError)
        ) as caught:
            await manager.install(
                request,
                scope="user",
                scope_key="default",
                idempotency_key="ua-ultraforge-badhash",
                expected_pack_id="ultraforge",
            )
        assert caught.value.code == "hash_mismatch"

    assert not canary.exists()
    assert await store.list_versions("ultraforge") == ()
    assert await store.get_binding("user", "default", "ultraforge") is None
    operation_id = hashlib.sha256(
        b"capability:ua-ultraforge-badhash"
    ).hexdigest()
    assert not manager.layout.staging_path(operation_id).exists()
