from __future__ import annotations

import hashlib
import time

import pytest

from deskpet.capabilities.contracts import (
    CapabilityBinding,
    CapabilityCatalogSnapshot,
    CapabilityDescriptor,
    CapabilityScope,
    CapabilityVersionDescriptor,
    CatalogStamp,
)
from deskpet.capabilities.search import CapabilityClaimGuard, CapabilitySearch
from deskpet.capabilities.store import (
    CapabilityStore,
    initialize_capability_database,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _descriptor(
    stamp: CatalogStamp,
    capability_id: str,
    *,
    display_name: str,
    description: str,
    aliases: tuple[str, ...] = (),
) -> CapabilityDescriptor:
    version = CapabilityVersionDescriptor(
        capability_id=capability_id,
        display_name=display_name,
        version="1.0.0",
        kind="instruction",
        source=f"fixture:{capability_id}",
        description=description,
        aliases=aliases,
        logical_tool_ids=(),
        provider_tool_names=(),
        permission_categories=(),
        effect_kinds=(),
        schema_hash=_hash(f"schema:{capability_id}"),
        manifest_hash=_hash(f"manifest:{capability_id}"),
        health="healthy",
    )
    binding = CapabilityBinding(
        binding_id=_hash(f"binding:{capability_id}"),
        capability_id=capability_id,
        version="1.0.0",
        manifest_hash=version.manifest_hash,
        scope="user",
        scope_key="default",
        active=True,
        generation=1,
    )
    return CapabilityDescriptor(
        version=version,
        visible_bindings=(binding,),
        executable=False,
        installed=True,
        tool_spec_fingerprints=(),
        stamp=stamp,
    )


def _snapshot(stamp: CatalogStamp | None = None) -> CapabilityCatalogSnapshot:
    actual_stamp = stamp or CatalogStamp(1, 2, 3, 4, 5)
    return CapabilityCatalogSnapshot(
        stamp=actual_stamp,
        scope=CapabilityScope(),
        descriptors=(
            _descriptor(
                actual_stamp,
                "godot_builder",
                display_name="Godot Builder",
                description="Build Godot games and tower defense scenes",
                aliases=("tower defense", "game engine"),
            ),
            _descriptor(
                actual_stamp,
                "photo_organizer",
                display_name="Photo Organizer",
                description="Sort and rename a folder of photos",
                aliases=("image cleanup",),
            ),
        ),
        created_at=100.0,
    )


class _BrokenEmbedder:
    async def embed(self, _texts):
        raise RuntimeError("semantic model unavailable")


class _ConstantEmbedder:
    async def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


@pytest.mark.asyncio
async def test_search_ranks_exact_alias_and_token_matches() -> None:
    search = CapabilitySearch()
    snapshot = _snapshot()

    exact = await search.search(
        snapshot=snapshot,
        root_run_id="root-1",
        query="godot_builder",
    )
    alias = await search.search(
        snapshot=snapshot,
        root_run_id="root-1",
        query="tower defense",
    )
    token = await search.search(
        snapshot=snapshot,
        root_run_id="root-1",
        query="godot scenes",
    )

    assert exact.hits[0].match_kind == "exact"
    assert alias.hits[0].match_kind == "alias"
    assert token.hits[0].match_kind == "token"
    assert {result.hits[0].capability_id for result in (exact, alias, token)} == {
        "godot_builder"
    }


@pytest.mark.asyncio
async def test_semantic_failure_degrades_to_lexical_search() -> None:
    search = CapabilitySearch(embedder=_BrokenEmbedder())
    result = await search.search(
        snapshot=_snapshot(),
        root_run_id="root-1",
        query="rename photos",
    )
    assert not result.semantic_used
    assert result.hits[0].capability_id == "photo_organizer"
    assert result.hits[0].match_kind == "token"


@pytest.mark.asyncio
async def test_semantic_search_is_additive_for_no_lexical_match() -> None:
    search = CapabilitySearch(embedder=_ConstantEmbedder())
    result = await search.search(
        snapshot=_snapshot(),
        root_run_id="root-1",
        query="unrelated semantic phrase",
    )
    assert result.semantic_used
    assert result.hits
    assert all(hit.match_kind == "semantic" for hit in result.hits)


@pytest.mark.asyncio
async def test_negative_action_claim_requires_current_stamp_receipt(
    tmp_path,
) -> None:
    path = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(path, clock=lambda: 100.0)
    guard = CapabilityClaimGuard(store)
    snapshot = _snapshot()

    missing = await guard.evaluate_negative_claim(
        root_run_id="root-1",
        stamp=snapshot.stamp,
        action_context=True,
    )
    assert not missing.allowed and missing.requires_search
    conversation = await guard.evaluate_negative_claim(
        root_run_id="root-1",
        stamp=snapshot.stamp,
        action_context=False,
    )
    assert conversation.allowed and not conversation.requires_search

    search = CapabilitySearch(store=store, clock=lambda: 100.0)
    first = await search.search(
        snapshot=snapshot,
        root_run_id="root-1",
        query="make a tower defense game",
    )
    repeated = await search.search(
        snapshot=snapshot,
        root_run_id="root-1",
        query="make a tower defense game",
    )
    assert repeated.receipt.receipt_id == first.receipt.receipt_id
    grounded = await guard.evaluate_negative_claim(
        root_run_id="root-1",
        stamp=snapshot.stamp,
        action_context=True,
    )
    assert grounded.allowed

    newer_stamp = CatalogStamp(2, 2, 3, 4, 5)
    stale = await guard.evaluate_negative_claim(
        root_run_id="root-1",
        stamp=newer_stamp,
        action_context=True,
    )
    assert not stale.allowed and stale.requires_search


@pytest.mark.asyncio
async def test_lexical_search_of_medium_catalog_stays_below_500ms() -> None:
    stamp = CatalogStamp(1, 1, 1, 1, 1)
    snapshot = CapabilityCatalogSnapshot(
        stamp=stamp,
        scope=CapabilityScope(),
        descriptors=tuple(
            _descriptor(
                stamp,
                f"fixture_{index}",
                display_name=f"Fixture {index}",
                description=f"Operate on fixture category {index}",
                aliases=(f"alias {index}",),
            )
            for index in range(300)
        ),
        created_at=100.0,
    )
    search = CapabilitySearch()

    started = time.perf_counter()
    result = await search.search(
        snapshot=snapshot,
        root_run_id="root-1",
        query="fixture category 299",
    )
    elapsed = time.perf_counter() - started

    assert result.hits
    assert elapsed < 0.5
