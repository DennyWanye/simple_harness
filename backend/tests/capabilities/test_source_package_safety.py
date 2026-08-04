from __future__ import annotations

import io
import os
import stat
import zipfile
from pathlib import Path

import pytest

from deskpet.capabilities.package_limits import (
    CapabilityPackageLimitsV1,
    CapabilityPackageValidationError,
    CapabilityPackageValidator,
    WindowsPackagePathPolicyV1,
)
from deskpet.capabilities.source import (
    CapabilitySourceError,
    CapabilitySourceResolver,
    PackSourceRequest,
)


def make_tree(root: Path, *, body: bytes = b"# demo\n") -> None:
    (root / "skills" / "demo").mkdir(parents=True)
    (root / "deskpet-pack.json").write_bytes(b"{}")
    (root / "skills" / "demo" / "SKILL.md").write_bytes(body)


def make_archive(entries: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for path, content in entries:
            archive.writestr(path, content)
    return output.getvalue()


@pytest.mark.asyncio
async def test_local_source_is_validated_before_and_after_copy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    make_tree(source)
    destination = tmp_path / "staging" / "op"

    staged = await CapabilitySourceResolver().stage(
        PackSourceRequest("local", str(source), "rev-1"),
        destination,
    )

    assert staged.validated_ref is not None
    assert staged.validated_ref.source_kind == "local"
    assert staged.validated_ref.entry_count == 2
    assert staged.validated_ref.file_set_hash


@pytest.mark.asyncio
async def test_local_limit_failure_happens_before_destination_creation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    make_tree(source, body=b"x" * 9)
    destination = tmp_path / "staging" / "op"
    limits = CapabilityPackageLimitsV1(
        max_single_file_bytes=8,
        total_uncompressed_bytes=64,
        archive_bytes=1024,
        manifest_bytes=32,
        file_count=8,
        max_path_depth=8,
        max_component_utf8_bytes=32,
        max_relative_path_utf8_bytes=128,
    )

    with pytest.raises(CapabilitySourceError) as exc:
        await CapabilitySourceResolver(
            package_validator=CapabilityPackageValidator(limits)
        ).stage(
            PackSourceRequest("local", str(source), "rev-1"),
            destination,
        )

    assert exc.value.code == "capability_package_limit_exceeded"
    assert not destination.exists()


@pytest.mark.asyncio
async def test_local_hardlink_is_rejected_before_copy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    make_tree(source)
    try:
        os.link(
            source / "skills" / "demo" / "SKILL.md",
            source / "skills" / "demo" / "alias.md",
        )
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")
    destination = tmp_path / "staging" / "op"

    with pytest.raises(CapabilitySourceError) as caught:
        await CapabilitySourceResolver().stage(
            PackSourceRequest("local", str(source), "rev-1"),
            destination,
        )

    assert caught.value.code == "capability_package_limit_exceeded"
    assert "materialized_hardlink_rejected" in str(caught.value)
    assert not destination.exists()


@pytest.mark.asyncio
async def test_local_symlink_is_rejected_before_copy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    make_tree(source)
    try:
        (source / "skills" / "linked.md").symlink_to(
            source / "skills" / "demo" / "SKILL.md"
        )
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    destination = tmp_path / "staging" / "op"

    with pytest.raises(CapabilitySourceError) as caught:
        await CapabilitySourceResolver().stage(
            PackSourceRequest("local", str(source), "rev-1"),
            destination,
        )

    assert caught.value.code == "source_symlink_rejected"
    assert not destination.exists()


@pytest.mark.asyncio
async def test_companion_archive_uses_safe_materializer_and_exact_ref(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "candidate.zip"
    archive_path.write_bytes(
        make_archive(
            [
                ("deskpet-pack.json", b"{}"),
                ("skills/demo/SKILL.md", b"# demo\n"),
            ]
        )
    )
    destination = tmp_path / "staging" / "candidate"

    staged = await CapabilitySourceResolver().stage(
        PackSourceRequest(
            "companion_growth",
            str(archive_path),
            "candidate-receipt-1",
        ),
        destination,
    )

    assert staged.validated_ref is not None
    assert staged.validated_ref.source_kind == "companion_growth"
    assert (destination / "skills" / "demo" / "SKILL.md").read_bytes() == b"# demo\n"


@pytest.mark.asyncio
async def test_archive_path_escape_fails_without_external_write(tmp_path: Path) -> None:
    archive_path = tmp_path / "candidate.zip"
    archive_path.write_bytes(
        make_archive(
            [
                ("deskpet-pack.json", b"{}"),
                ("../outside.txt", b"owned"),
            ]
        )
    )
    destination = tmp_path / "staging" / "candidate"
    outside = tmp_path / "staging" / "outside.txt"

    with pytest.raises(CapabilitySourceError) as exc:
        await CapabilitySourceResolver().stage(
            PackSourceRequest(
                "companion_growth",
                str(archive_path),
                "candidate-receipt-1",
            ),
            destination,
        )

    assert exc.value.code == "capability_package_limit_exceeded"
    assert not destination.exists()
    assert not outside.exists()


@pytest.mark.asyncio
async def test_archive_windows_reserved_path_cleans_staging(tmp_path: Path) -> None:
    archive_path = tmp_path / "candidate.zip"
    archive_path.write_bytes(
        make_archive(
            [
                ("deskpet-pack.json", b"{}"),
                ("skills/CON.txt", b"blocked"),
            ]
        )
    )
    destination = tmp_path / "staging" / "candidate"

    with pytest.raises(CapabilitySourceError):
        await CapabilitySourceResolver().stage(
            PackSourceRequest(
                "companion_growth",
                str(archive_path),
                "candidate-receipt-1",
            ),
            destination,
        )

    assert not destination.exists()


def test_materialized_tree_tamper_no_longer_matches_archive_ref(
    tmp_path: Path,
) -> None:
    raw = make_archive(
        [
            ("deskpet-pack.json", b"{}"),
            ("skills/demo/SKILL.md", b"# demo\n"),
        ]
    )
    target = tmp_path / "staging"
    validator = CapabilityPackageValidator()
    expected = validator.materialize_zip(
        raw,
        target,
        source_kind="companion_growth",
    )
    (target / "skills" / "demo" / "SKILL.md").write_bytes(b"tampered")

    with pytest.raises(CapabilityPackageValidationError) as exc:
        validator.validate_materialized_tree(
            target,
            source_kind="companion_growth",
            expected_ref=expected,
        )

    assert exc.value.reason == "validated_ref_materialized_mismatch"
    assert exc.value.dimension == "file_set_hash"


def test_materializer_revalidates_each_path_before_write(tmp_path: Path) -> None:
    class RecordingPolicy(WindowsPackagePathPolicyV1):
        def __init__(self, policy_limits):
            super().__init__(policy_limits)
            self.calls: list[str] = []

        def validate_relative_path(self, raw: str):
            self.calls.append(raw)
            return super().validate_relative_path(raw)

    policy_limits = CapabilityPackageLimitsV1()
    policy = RecordingPolicy(policy_limits)
    validator = CapabilityPackageValidator(policy_limits, policy)
    raw = make_archive(
        [
            ("deskpet-pack.json", b"{}"),
            ("skills/demo/SKILL.md", b"# demo\n"),
        ]
    )

    validator.materialize_zip(
        raw,
        tmp_path / "staging",
        source_kind="companion_growth",
    )

    assert policy.calls.count("deskpet-pack.json") >= 2
    assert policy.calls.count("skills/demo/SKILL.md") >= 2


def test_windows_reparse_attribute_is_rejected() -> None:
    class ReparseStat:
        st_file_attributes = 0x400
        st_mode = stat.S_IFDIR
        st_nlink = 1

    with pytest.raises(CapabilityPackageValidationError) as exc:
        CapabilityPackageValidator._assert_stat_safe(
            ReparseStat(), require_regular=False
        )
    assert exc.value.reason == "materialized_reparse_rejected"
