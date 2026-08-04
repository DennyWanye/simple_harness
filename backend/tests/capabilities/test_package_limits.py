from __future__ import annotations

import io
import stat
import zipfile

import pytest

from deskpet.capabilities.package_limits import (
    BASELINE_HASH,
    PACKAGE_POLICY_VERSION,
    CapabilityPackageLimitsV1,
    CapabilityPackageValidationError,
    CapabilityPackageValidator,
    ValidatedCapabilityPackageRefV1,
    WindowsPackagePathPolicyV1,
)


def limits(**changes) -> CapabilityPackageLimitsV1:
    values = {
        "file_count": 8,
        "max_single_file_bytes": 64,
        "total_uncompressed_bytes": 128,
        "archive_bytes": 4096,
        "manifest_bytes": 64,
        "max_archive_entry_compression_ratio": 20,
        "max_path_depth": 4,
        "max_component_utf8_bytes": 32,
        "max_relative_path_utf8_bytes": 64,
    }
    values.update(changes)
    return CapabilityPackageLimitsV1(**values)


def archive(
    entries: list[tuple[str, bytes]],
    *,
    compression: int = zipfile.ZIP_STORED,
    special: zipfile.ZipInfo | None = None,
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as package:
        for path, payload in entries:
            package.writestr(path, payload)
        if special is not None:
            package.writestr(special, b"target")
    return output.getvalue()


def minimal_archive(*, manifest: bytes = b"{}", body: bytes = b"x") -> bytes:
    return archive(
        [
            ("deskpet-pack.json", manifest),
            ("skills/demo/SKILL.md", body),
        ]
    )


def assert_limit(exc, dimension: str) -> None:
    assert exc.value.code == "capability_package_limit_exceeded"
    assert exc.value.dimension == dimension


def test_production_limits_match_task0_machine_baseline() -> None:
    value = CapabilityPackageLimitsV1()
    assert value.baseline_hash == BASELINE_HASH
    assert value.to_dict() == {
        "schema": PACKAGE_POLICY_VERSION,
        "baseline_hash": BASELINE_HASH,
        "file_count": 128,
        "max_single_file_bytes": 4 * 1024 * 1024,
        "total_uncompressed_bytes": 16 * 1024 * 1024,
        "archive_bytes": 8 * 1024 * 1024,
        "manifest_bytes": 512 * 1024,
        "max_archive_entry_compression_ratio": 20.0,
        "max_path_depth": 12,
        "max_component_utf8_bytes": 128,
        "max_relative_path_utf8_bytes": 512,
    }


@pytest.mark.parametrize("count,allowed", [(2, True), (3, True), (4, False)])
def test_file_count_boundary(count: int, allowed: bool) -> None:
    raw = archive(
        [("deskpet-pack.json", b"{}")]
        + [(f"f{index}.txt", b"x") for index in range(count - 1)]
    )
    validator = CapabilityPackageValidator(limits(file_count=3))
    if allowed:
        assert len(validator.preflight_zip_index(raw).entries) == count
    else:
        with pytest.raises(CapabilityPackageValidationError) as exc:
            validator.preflight_zip_index(raw)
        assert_limit(exc, "file_count")


@pytest.mark.parametrize("size,allowed", [(7, True), (8, True), (9, False)])
def test_single_file_boundary(size: int, allowed: bool) -> None:
    raw = minimal_archive(body=b"x" * size)
    validator = CapabilityPackageValidator(
        limits(max_single_file_bytes=8, total_uncompressed_bytes=64)
    )
    if allowed:
        validator.preflight_zip_index(raw)
    else:
        with pytest.raises(CapabilityPackageValidationError) as exc:
            validator.preflight_zip_index(raw)
        assert_limit(exc, "max_single_file_bytes")


@pytest.mark.parametrize("size,allowed", [(9, True), (10, True), (11, False)])
def test_total_uncompressed_boundary(size: int, allowed: bool) -> None:
    raw = minimal_archive(manifest=b"{}", body=b"x" * (size - 2))
    validator = CapabilityPackageValidator(
        limits(max_single_file_bytes=32, total_uncompressed_bytes=10)
    )
    if allowed:
        validator.preflight_zip_index(raw)
    else:
        with pytest.raises(CapabilityPackageValidationError) as exc:
            validator.preflight_zip_index(raw)
        assert_limit(exc, "total_uncompressed_bytes")


@pytest.mark.parametrize("size,allowed", [(7, True), (8, True), (9, False)])
def test_manifest_boundary(size: int, allowed: bool) -> None:
    raw = minimal_archive(manifest=b"x" * size)
    validator = CapabilityPackageValidator(limits(manifest_bytes=8))
    if allowed:
        validator.preflight_zip_index(raw)
    else:
        with pytest.raises(CapabilityPackageValidationError) as exc:
            validator.preflight_zip_index(raw)
        assert_limit(exc, "manifest_bytes")


def test_archive_bytes_limit_is_checked_before_zip_parse() -> None:
    raw = minimal_archive()
    CapabilityPackageValidator(limits(archive_bytes=len(raw))).preflight_zip_index(raw)
    with pytest.raises(CapabilityPackageValidationError) as exc:
        CapabilityPackageValidator(
            limits(archive_bytes=len(raw) - 1)
        ).preflight_zip_index(raw)
    assert_limit(exc, "archive_bytes")


def test_archive_limit_rejects_without_reading_payload() -> None:
    class CountingStream(io.BytesIO):
        reads = 0

        def read(self, *args):
            self.reads += 1
            return super().read(*args)

    stream = CountingStream(minimal_archive())
    with pytest.raises(CapabilityPackageValidationError) as exc:
        CapabilityPackageValidator(
            limits(archive_bytes=len(stream.getvalue()) - 1)
        ).preflight_zip_index(stream)
    assert_limit(exc, "archive_bytes")
    assert stream.reads == 0


@pytest.mark.parametrize("depth,allowed", [(2, True), (3, True), (4, False)])
def test_path_depth_boundary(depth: int, allowed: bool) -> None:
    policy = WindowsPackagePathPolicyV1(limits(max_path_depth=3))
    path = "/".join(["a"] * depth)
    if allowed:
        assert policy.validate_relative_path(path).depth == depth
    else:
        with pytest.raises(CapabilityPackageValidationError) as exc:
            policy.validate_relative_path(path)
        assert_limit(exc, "max_path_depth")


@pytest.mark.parametrize("size,allowed", [(7, True), (8, True), (9, False)])
def test_component_utf8_boundary(size: int, allowed: bool) -> None:
    policy = WindowsPackagePathPolicyV1(
        limits(max_component_utf8_bytes=8, max_relative_path_utf8_bytes=32)
    )
    if allowed:
        policy.validate_relative_path("a" * size)
    else:
        with pytest.raises(CapabilityPackageValidationError) as exc:
            policy.validate_relative_path("a" * size)
        assert_limit(exc, "max_component_utf8_bytes")


@pytest.mark.parametrize("size,allowed", [(7, True), (8, True), (9, False)])
def test_relative_path_utf8_boundary(size: int, allowed: bool) -> None:
    policy = WindowsPackagePathPolicyV1(
        limits(max_component_utf8_bytes=32, max_relative_path_utf8_bytes=8)
    )
    path = "a/" + "b" * (size - 2)
    if allowed:
        policy.validate_relative_path(path)
    else:
        with pytest.raises(CapabilityPackageValidationError) as exc:
            policy.validate_relative_path(path)
        assert_limit(exc, "max_relative_path_utf8_bytes")


@pytest.mark.parametrize(
    "path",
    [
        "/absolute",
        r"C:/drive",
        "//server/share",
        r"\\server\share",
        r"mixed/path\name",
        "../escape",
        "a//b",
        "a/./b",
        "name:stream",
        "trailing.",
        "trailing ",
    ],
)
def test_windows_path_policy_rejects_unsafe_shapes(path: str) -> None:
    with pytest.raises(CapabilityPackageValidationError):
        WindowsPackagePathPolicyV1(limits()).validate_relative_path(path)


@pytest.mark.parametrize(
    "component",
    [
        "CON",
        "con.txt",
        "PrN",
        "AUX.log",
        "nul",
        *[f"COM{index}.x" for index in range(1, 10)],
        *[f"lpt{index}" for index in range(1, 10)],
    ],
)
def test_windows_reserved_devices_are_rejected(component: str) -> None:
    with pytest.raises(CapabilityPackageValidationError) as exc:
        WindowsPackagePathPolicyV1(limits()).validate_relative_path(
            f"safe/{component}"
        )
    assert exc.value.reason == "path_reserved_device"


@pytest.mark.parametrize(
    "paths",
    [
        ("Skills/Demo.md", "skills/demo.MD"),
        ("Ｋ/readme.md", "k/README.md"),
        ("straße/a", "STRASSE/A"),
    ],
)
def test_nfkc_casefold_duplicate_paths_are_rejected(paths) -> None:
    with pytest.raises(CapabilityPackageValidationError) as exc:
        WindowsPackagePathPolicyV1(limits()).validate_unique_paths(paths)
    assert "path_collision" in exc.value.reason


def test_zip_index_rejects_duplicate_casefold_and_declared_set_mismatch() -> None:
    raw = archive(
        [
            ("deskpet-pack.json", b"{}"),
            ("Skill.md", b"a"),
            ("skill.MD", b"b"),
        ]
    )
    with pytest.raises(CapabilityPackageValidationError):
        CapabilityPackageValidator(limits()).preflight_zip_index(raw)

    with pytest.raises(CapabilityPackageValidationError) as exc:
        CapabilityPackageValidator(limits()).preflight_zip_index(
            minimal_archive(),
            declared_paths={"deskpet-pack.json", "different.txt"},
        )
    assert exc.value.reason == "archive_declared_file_set_mismatch"


def test_zip_index_rejects_symlink_and_reparse_entries() -> None:
    symlink = zipfile.ZipInfo("link")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(CapabilityPackageValidationError) as exc:
        CapabilityPackageValidator(limits()).preflight_zip_index(
            archive([("deskpet-pack.json", b"{}")], special=symlink)
        )
    assert exc.value.reason == "archive_link_or_special_entry"

    reparse = zipfile.ZipInfo("junction")
    reparse.create_system = 0
    reparse.external_attr = 0x400
    with pytest.raises(CapabilityPackageValidationError) as exc:
        CapabilityPackageValidator(limits()).preflight_zip_index(
            archive([("deskpet-pack.json", b"{}")], special=reparse)
        )
    assert exc.value.reason == "archive_reparse_entry"


def test_compression_ratio_rejects_zip_bomb_before_streaming() -> None:
    raw = archive(
        [
            ("deskpet-pack.json", b"{}"),
            ("bomb.txt", b"A" * 4096),
        ],
        compression=zipfile.ZIP_DEFLATED,
    )
    with pytest.raises(CapabilityPackageValidationError) as exc:
        CapabilityPackageValidator(
            limits(
                max_single_file_bytes=8192,
                total_uncompressed_bytes=8192,
                archive_bytes=8192,
                max_archive_entry_compression_ratio=20,
            )
        ).preflight_zip_index(raw)
    assert_limit(exc, "max_archive_entry_compression_ratio")


def test_compression_ratio_limit_minus_exact_plus_boundary() -> None:
    raw = archive(
        [
            ("deskpet-pack.json", b"{}"),
            ("payload.bin", bytes(range(64)) * 4),
        ],
        compression=zipfile.ZIP_DEFLATED,
    )
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        info = package.getinfo("payload.bin")
        ratio = info.file_size / info.compress_size

    common = {
        "max_single_file_bytes": 512,
        "total_uncompressed_bytes": 1024,
        "archive_bytes": 4096,
    }
    CapabilityPackageValidator(
        limits(
            **common,
            max_archive_entry_compression_ratio=ratio + 0.01,
        )
    ).preflight_zip_index(raw)
    CapabilityPackageValidator(
        limits(**common, max_archive_entry_compression_ratio=ratio)
    ).preflight_zip_index(raw)
    with pytest.raises(CapabilityPackageValidationError) as exc:
        CapabilityPackageValidator(
            limits(
                **common,
                max_archive_entry_compression_ratio=max(1.0, ratio - 0.01),
            )
        ).preflight_zip_index(raw)
    assert_limit(exc, "max_archive_entry_compression_ratio")


def test_streaming_detects_forged_central_directory_size() -> None:
    raw = bytearray(
        archive(
            [
                ("deskpet-pack.json", b"{}"),
                ("payload.bin", b"abcdefgh"),
            ]
        )
    )
    signatures = []
    offset = 0
    while True:
        offset = raw.find(b"PK\x01\x02", offset)
        if offset < 0:
            break
        signatures.append(offset)
        offset += 4
    assert len(signatures) == 2
    raw[signatures[1] + 24 : signatures[1] + 28] = (1).to_bytes(4, "little")

    with pytest.raises(CapabilityPackageValidationError) as exc:
        CapabilityPackageValidator(limits()).validate_zip_archive(
            bytes(raw), source_kind="local"
        )
    assert exc.value.reason == "archive_stream_validation_failed"


def test_validated_ref_binds_archive_manifest_file_set_and_policy() -> None:
    raw = minimal_archive()
    validator = CapabilityPackageValidator(limits(archive_bytes=4096))
    result = validator.validate_zip_archive(
        raw,
        source_kind="companion_growth",
        declared_paths={"deskpet-pack.json", "skills/demo/SKILL.md"},
    )

    assert result.archive_hash
    assert result.manifest_hash
    assert result.file_set_hash
    assert result.policy_hash == validator.limits.policy_hash
    assert result.baseline_hash == BASELINE_HASH

    with pytest.raises(
        CapabilityPackageValidationError, match="validated_ref_host_only"
    ):
        ValidatedCapabilityPackageRefV1(
            source_kind="local",
            policy_version=PACKAGE_POLICY_VERSION,
            baseline_hash=BASELINE_HASH,
            policy_hash="a" * 64,
            archive_hash="a" * 64,
            manifest_hash="a" * 64,
            file_set_hash="a" * 64,
            entry_count=1,
            total_uncompressed_bytes=1,
            validation_receipt_hash="a" * 64,
        )
