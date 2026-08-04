from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.capabilities.source import (
    CapabilitySourceError,
    CapabilitySourceResolver,
    PackSourceRequest,
)


@pytest.mark.asyncio
async def test_local_source_copies_only_into_empty_staging(tmp_path: Path) -> None:
    source = tmp_path / "源 package"
    source.mkdir()
    (source / "deskpet-pack.json").write_text("{}", encoding="utf-8")
    destination = tmp_path / "staging" / "op-1"
    resolver = CapabilitySourceResolver()
    staged = await resolver.stage(
        PackSourceRequest("local", str(source), "rev-1"), destination
    )
    assert staged.root == destination.resolve()
    assert (staged.root / "deskpet-pack.json").read_text(encoding="utf-8") == "{}"

    with pytest.raises(CapabilitySourceError) as caught:
        await resolver.stage(
            PackSourceRequest("local", str(source), "rev-1"), destination
        )
    assert caught.value.code == "staging_exists"


@pytest.mark.asyncio
async def test_configured_source_resolves_alias_without_recursion(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "deskpet-pack.json").write_text("{}", encoding="utf-8")
    (source / "file.txt").write_text("ok", encoding="utf-8")
    resolver = CapabilitySourceResolver(
        configured_sources={
            "trusted": PackSourceRequest("local", str(source), "catalog-rev")
        }
    )
    destination = tmp_path / "staging" / "op"
    staged = await resolver.stage(
        PackSourceRequest("configured", "trusted", "configured"), destination
    )
    assert (staged.root / "file.txt").read_text(encoding="utf-8") == "ok"
    assert staged.source.source_type == "configured"


def test_source_subdirectory_rejects_traversal() -> None:
    with pytest.raises(CapabilitySourceError) as caught:
        PackSourceRequest("git", "https://example.invalid/repo.git", "main", "../pack")
    assert caught.value.code == "source_path_traversal"


def test_source_revision_cannot_be_parsed_as_git_option() -> None:
    with pytest.raises(CapabilitySourceError) as caught:
        PackSourceRequest("git", "https://example.invalid/repo.git", "--help")
    assert caught.value.code == "invalid_source_revision"
