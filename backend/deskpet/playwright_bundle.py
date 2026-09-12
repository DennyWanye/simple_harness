"""Fail-closed Playwright browser bundle acquisition and runtime resolution.

The product owns exactly one Chromium Headless Shell revision.  This module is
stdlib-only so build scripts, PyInstaller specs, and the frozen runtime all use
the same contract without importing the backend application graph.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import struct
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath


class PlaywrightBundleError(RuntimeError):
    """The pinned product browser is missing, ambiguous, or corrupt."""


@dataclass(frozen=True)
class PlaywrightBundleContract:
    playwright_version: str = "1.61.0"
    revision: str = "1228"
    browser_version: str = "149.0.7827.55"
    archive_name: str = "chrome-headless-shell-win64-149.0.7827.55.zip"
    archive_url: str = (
        "https://cdn.playwright.dev/dbazure/download/playwright/builds/"
        "chromium/1228/chrome-headless-shell-win64.zip"
    )
    archive_length: int = 119_099_822
    archive_md5: str = "1BC51B5A9F308F4B7F47AC15A1BE049D"
    archive_sha256: str = "5CFDA0C763AA6A867CE2EFAD0C467E3220E9C5C01C4CBA02FD57AFE49EDE5457"
    archive_entries: int = 290
    browsers_json_sha256: str = "EE39BC924BC3D1BD895626C2910F1292D109BBFEEB5ABD113ACB45E1951CC942"
    revision_dir: str = "chromium_headless_shell-1228"
    executable_relative: str = "chrome-headless-shell-win64/chrome-headless-shell.exe"
    executable_length: int = 203_034_112
    executable_sha256: str = "28016DF6864D302434C9231E1F9F1A8A7ECC512CB2FE3FAABB2A36130B96BCF1"
    owner_dir: str = "playwright-browsers"
    product_manifest: str = "deskpet-playwright-bundle.json"
    executable_architecture: str | None = None
    license_sha256: str | None = None


# Historical Windows identity remains available to explicit legacy callers.
# Runtime/build defaults select a supported native platform below.
CONTRACT = PlaywrightBundleContract()
MAC_ARM64_CONTRACT = replace(
    CONTRACT,
    archive_name="chrome-headless-shell-mac-arm64-149.0.7827.55.zip",
    # Installed Playwright 1.61.0 coreBundle.js cftUrl + browsers.json r1228.
    archive_url="https://cdn.playwright.dev/builds/cft/149.0.7827.55/mac-arm64/chrome-headless-shell-mac-arm64.zip",
    archive_length=98_043_456,
    archive_md5="9EA0A6D16E46DCC685D462D210D78116",
    archive_sha256="302F82603BE06683947594ECD60F849E362A8FE3DD82A89BD4408477C97E75A6",
    archive_entries=17,
    executable_relative="chrome-headless-shell-mac-arm64/chrome-headless-shell",
    executable_length=159_293_248,
    executable_sha256="11E393326C7D20A7C56641A7C65DEF33EA9C280DA3B0B74CF8563B07989A0EE3",
    executable_architecture="macho-arm64",
    license_sha256="EA614F3494514366B3EE83DB6E3E6DED39E0060C9FF3FB283FFB9A2F60CE59C5",
)


def get_platform_contract(system: str | None = None, machine: str | None = None) -> PlaywrightBundleContract:
    system = sys.platform if system is None else system
    machine = (platform.machine() if machine is None else machine).lower()
    if system == "win32" and machine in {"amd64", "x86_64"}:
        return CONTRACT
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return MAC_ARM64_CONTRACT
    raise PlaywrightBundleError(f"unsupported browser bundle platform: {system}/{machine}")


def _validate_executable_header(header: bytes, contract: PlaywrightBundleContract) -> None:
    if contract.executable_architecture == "macho-arm64":
        if len(header) < 32 or header[:4] != b"\xcf\xfa\xed\xfe" or struct.unpack("<I", header[4:8])[0] != 0x0100000C:
            raise PlaywrightBundleError("browser executable architecture is not Mach-O arm64")


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def validate_playwright_package(contract: PlaywrightBundleContract | None = None) -> Path:
    contract = contract or get_platform_contract()
    try:
        version = importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError as exc:
        raise PlaywrightBundleError("playwright is not installed") from exc
    if version != contract.playwright_version:
        raise PlaywrightBundleError(
            f"playwright version {version!r} != pinned {contract.playwright_version!r}"
        )
    package = Path(__import__("playwright").__file__).resolve().parent
    browsers_json = package / "driver" / "package" / "browsers.json"
    if not browsers_json.is_file():
        raise PlaywrightBundleError(f"missing Playwright browsers.json: {browsers_json}")
    if _digest(browsers_json, "sha256") != contract.browsers_json_sha256:
        raise PlaywrightBundleError("Playwright browsers.json hash does not match product pin")
    payload = json.loads(browsers_json.read_text(encoding="utf-8"))
    matches = [
        item for item in payload.get("browsers", [])
        if item.get("name") == "chromium-headless-shell"
    ]
    if len(matches) != 1 or str(matches[0].get("revision")) != contract.revision:
        raise PlaywrightBundleError("Playwright package does not declare the pinned headless-shell revision")
    return package


def _safe_zip_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    for member in members:
        pure = PurePosixPath(member.filename.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts or (pure.parts and ":" in pure.parts[0]):
            raise PlaywrightBundleError(f"unsafe archive member: {member.filename!r}")
    return members


def validate_archive(path: Path, contract: PlaywrightBundleContract | None = None) -> None:
    contract = contract or get_platform_contract()
    path = Path(path)
    if not path.is_file() or path.stat().st_size != contract.archive_length:
        raise PlaywrightBundleError("browser archive length does not match product pin")
    if _digest(path, "md5") != contract.archive_md5:
        raise PlaywrightBundleError("browser archive MD5 does not match product pin")
    if _digest(path, "sha256") != contract.archive_sha256:
        raise PlaywrightBundleError("browser archive SHA256 does not match product pin")
    try:
        with zipfile.ZipFile(path) as archive:
            members = _safe_zip_members(archive)
            if len(members) != contract.archive_entries:
                raise PlaywrightBundleError("browser archive entry count does not match product pin")
            bad = archive.testzip()
            if bad:
                raise PlaywrightBundleError(f"browser archive CRC failed at {bad!r}")
            expected = contract.executable_relative.replace("\\", "/")
            executable = next((item for item in members if item.filename.rstrip("/") == expected), None)
            if executable is None or executable.file_size != contract.executable_length:
                raise PlaywrightBundleError("browser executable entry length does not match product pin")
            digest = hashlib.sha256()
            with archive.open(executable) as stream:
                header = stream.read(32)
                _validate_executable_header(header, contract)
                digest.update(header)
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest().upper() != contract.executable_sha256:
                raise PlaywrightBundleError("browser executable entry hash does not match product pin")
            if contract.license_sha256:
                license_name = str(PurePosixPath(expected).parent / "LICENSE.headless_shell")
                try:
                    license_bytes = archive.read(license_name)
                except KeyError as exc:
                    raise PlaywrightBundleError("browser archive license is missing") from exc
                if hashlib.sha256(license_bytes).hexdigest().upper() != contract.license_sha256:
                    raise PlaywrightBundleError("browser archive license hash does not match product pin")
    except (OSError, zipfile.BadZipFile) as exc:
        raise PlaywrightBundleError(f"invalid browser archive: {exc}") from exc


def _manifest_payload(contract: PlaywrightBundleContract) -> dict[str, object]:
    payload = asdict(contract)
    # Keep existing Windows ownership markers byte-for-byte compatible.
    for optional in ("executable_architecture", "license_sha256"):
        if payload[optional] is None:
            del payload[optional]
    payload["schema_version"] = 1
    return payload


def validate_browser_owner(owner: Path, contract: PlaywrightBundleContract | None = None) -> Path:
    contract = contract or get_platform_contract()
    owner = Path(owner).resolve()
    revision_root = owner / contract.revision_dir
    if not revision_root.is_dir():
        raise PlaywrightBundleError(f"missing pinned browser root: {revision_root}")
    foreign = sorted(
        path.name for path in owner.glob("chromium_headless_shell-*")
        if path.is_dir() and path.name != contract.revision_dir
    )
    if foreign:
        raise PlaywrightBundleError(f"unexpected browser revision(s): {', '.join(foreign)}")
    manifest = revision_root / contract.product_manifest
    marker = revision_root / "INSTALLATION_COMPLETE"
    if not marker.is_file() or not manifest.is_file():
        raise PlaywrightBundleError("browser root lacks product ownership markers")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlaywrightBundleError("invalid product browser manifest") from exc
    expected = _manifest_payload(contract)
    if any(payload.get(key) != value for key, value in expected.items()):
        raise PlaywrightBundleError("product browser manifest does not match the locked contract")
    executable = revision_root / Path(contract.executable_relative)
    if not executable.is_file() or executable.stat().st_size != contract.executable_length:
        raise PlaywrightBundleError("installed browser executable length does not match product pin")
    if _digest(executable, "sha256") != contract.executable_sha256:
        raise PlaywrightBundleError("installed browser executable hash does not match product pin")
    if contract.executable_architecture:
        with executable.open("rb") as stream:
            _validate_executable_header(stream.read(32), contract)
        if not executable.stat().st_mode & 0o111:
            raise PlaywrightBundleError("installed browser lacks executable permission")
    license_path = revision_root / Path(contract.executable_relative).parent / "LICENSE.headless_shell"
    if not license_path.is_file():
        raise PlaywrightBundleError("installed browser license is missing")
    if contract.license_sha256 and _digest(license_path, "sha256") != contract.license_sha256:
        raise PlaywrightBundleError("installed browser license hash does not match product pin")
    return revision_root


def publish_archive(
    archive_path: Path,
    owner: Path,
    contract: PlaywrightBundleContract | None = None,
) -> Path:
    """Validate, stage on the target volume, then atomically publish."""
    contract = contract or get_platform_contract()
    archive_path, owner = Path(archive_path).resolve(), Path(owner).resolve()
    validate_archive(archive_path, contract)
    owner.mkdir(parents=True, exist_ok=True)
    target = owner / contract.revision_dir
    if target.exists():
        return validate_browser_owner(owner, contract)
    stage_owner = Path(tempfile.mkdtemp(prefix=f".{contract.revision_dir}.stage-", dir=owner))
    stage = stage_owner / contract.revision_dir
    try:
        stage.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            _safe_zip_members(archive)
            archive.extractall(stage)
        if contract.executable_architecture:
            # zipfile does not restore POSIX executable bits. The exact binary
            # bytes and architecture have already been verified above.
            (stage / contract.executable_relative).chmod(0o755)
        (stage / "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")
        (stage / contract.product_manifest).write_text(
            json.dumps(_manifest_payload(contract), ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        validate_browser_owner(stage_owner, contract)
        os.replace(stage, target)
        return validate_browser_owner(owner, contract)
    finally:
        shutil.rmtree(stage_owner, ignore_errors=True)


@dataclass(frozen=True)
class BrowserBundleDiagnostic:
    source: str
    owner: Path
    revision_root: Path
    playwright_version: str
    expected_revision: str
    revision: str
    executable_sha256: str

    def public_dict(self, *, launch_status: str = "not_checked") -> dict[str, str]:
        return {
            "source": self.source,
            "playwright_version": self.playwright_version,
            "expected_revision": self.expected_revision,
            "actual_revision": self.revision,
            "browser": "chromium-headless-shell",
            "executable_sha256": self.executable_sha256,
            "launch_status": launch_status,
        }

    def environment(self) -> dict[str, str]:
        return {
            "PLAYWRIGHT_BROWSERS_PATH": str(self.owner),
            "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
        }


def default_dev_owner(contract: PlaywrightBundleContract | None = None) -> Path:
    contract = contract or get_platform_contract()
    local = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    return local / "DPW" / f"pw-{contract.playwright_version.replace('.', '')}-{contract.revision}" / contract.owner_dir


def resolve_browser_bundle(
    *,
    test_override: Path | None = None,
    tauri_resource_root: Path | None = None,
    dev_owner: Path | None = None,
    contract: PlaywrightBundleContract | None = None,
) -> BrowserBundleDiagnostic:
    contract = contract or get_platform_contract()
    validate_playwright_package(contract)
    source: str
    owner: Path
    if test_override is not None:
        source, owner = "test-override", Path(test_override)
    elif (frozen_root := getattr(sys, "_MEIPASS", None)):
        source, owner = "frozen", Path(frozen_root) / contract.owner_dir
    elif (resource_root := tauri_resource_root or os.environ.get("DESKPET_TAURI_RESOURCE_ROOT")):
        source, owner = "tauri", Path(resource_root) / contract.owner_dir
    else:
        source, owner = "dev-cache", dev_owner or default_dev_owner(contract)

    # The selected runtime context is authoritative. Never fall through to a
    # different owner: a broken frozen/Tauri package must fail on the user's
    # machine even if a developer cache happens to exist beside it.
    owner = owner.resolve()
    root = validate_browser_owner(owner, contract)
    return BrowserBundleDiagnostic(
        source,
        owner,
        root,
        contract.playwright_version,
        contract.revision,
        contract.revision,
        contract.executable_sha256,
    )


def assert_short_build_paths(*paths: Path, max_deepest_length: int = 240) -> None:
    contract = get_platform_contract()
    suffix = Path(contract.owner_dir) / contract.revision_dir / Path(contract.executable_relative)
    for path in paths:
        predicted = Path(path).resolve() / suffix
        if len(str(predicted)) > max_deepest_length:
            raise PlaywrightBundleError(
                f"build path is too long ({len(str(predicted))}>{max_deepest_length}): {predicted}"
            )
