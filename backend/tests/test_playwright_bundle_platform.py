"""Platform contract regressions; no network, browser process, or native build."""

from dataclasses import replace
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import zipfile

import pytest

from deskpet import playwright_bundle as bundle


def test_windows_pin_is_unchanged():
    contract = bundle.get_platform_contract("win32", "AMD64")
    assert contract == bundle.PlaywrightBundleContract()
    assert contract.archive_length == 119_099_822
    assert contract.archive_sha256 == "5CFDA0C763AA6A867CE2EFAD0C467E3220E9C5C01C4CBA02FD57AFE49EDE5457"
    assert contract.archive_md5 == "1BC51B5A9F308F4B7F47AC15A1BE049D"
    assert contract.archive_entries == 290
    assert contract.executable_length == 203_034_112
    assert contract.executable_sha256 == "28016DF6864D302434C9231E1F9F1A8A7ECC512CB2FE3FAABB2A36130B96BCF1"
    assert contract.executable_relative == "chrome-headless-shell-win64/chrome-headless-shell.exe"
    assert "executable_architecture" not in bundle._manifest_payload(contract)


def test_arm64_contract_binds_measured_official_archive():
    contract = bundle.get_platform_contract("darwin", "arm64")
    assert contract is bundle.MAC_ARM64_CONTRACT
    assert contract.revision == "1228"
    assert contract.archive_url == "https://cdn.playwright.dev/builds/cft/149.0.7827.55/mac-arm64/chrome-headless-shell-mac-arm64.zip"
    assert contract.archive_length == 98_043_456
    assert contract.archive_sha256 == "302F82603BE06683947594ECD60F849E362A8FE3DD82A89BD4408477C97E75A6"
    assert contract.executable_architecture == "macho-arm64"


@pytest.mark.parametrize("system,machine", [("darwin", "x86_64"), ("linux", "aarch64"), ("linux", "x86_64"), ("win32", "ARM64")])
def test_unsupported_platform_has_no_windows_fallback(system, machine):
    with pytest.raises(bundle.PlaywrightBundleError, match="unsupported browser bundle platform"):
        bundle.get_platform_contract(system, machine)


def _archive(tmp_path: Path, *, architecture=0x0100000C, license_present=True):
    # Small fixture with a real Mach-O header shape; hashes bind these bytes.
    executable = struct.pack("<II", 0xFEEDFACF, architecture) + bytes(24)
    license_bytes = b"fixture browser license"
    archive = tmp_path / "browser.zip"
    relative = bundle.MAC_ARM64_CONTRACT.executable_relative
    with zipfile.ZipFile(archive, "w") as output:
        info = zipfile.ZipInfo(relative)
        info.external_attr = 0o100755 << 16
        output.writestr(info, executable)
        if license_present:
            output.writestr(str(Path(relative).parent / "LICENSE.headless_shell"), license_bytes)
    raw = archive.read_bytes()
    contract = replace(bundle.MAC_ARM64_CONTRACT,
        archive_length=len(raw), archive_md5=hashlib.md5(raw).hexdigest().upper(),
        archive_sha256=hashlib.sha256(raw).hexdigest().upper(),
        archive_entries=2 if license_present else 1,
        executable_length=len(executable), executable_sha256=hashlib.sha256(executable).hexdigest().upper(),
        license_sha256=hashlib.sha256(license_bytes).hexdigest().upper())
    return archive, contract


def test_mac_archive_publish_preserves_executable_and_ownership(tmp_path):
    archive, contract = _archive(tmp_path)
    owner = tmp_path / "owned"
    root = bundle.publish_archive(archive, owner, contract)
    executable = root / contract.executable_relative
    assert executable.stat().st_mode & 0o111
    assert bundle.validate_browser_owner(owner, contract) == root
    assert bundle.publish_archive(archive, owner, contract) == root


def test_mac_archive_rejects_x64_even_with_matching_archive_hash(tmp_path):
    archive, contract = _archive(tmp_path, architecture=0x01000007)
    with pytest.raises(bundle.PlaywrightBundleError, match="architecture"):
        bundle.validate_archive(archive, contract)


def test_mac_archive_requires_pinned_license(tmp_path):
    archive, contract = _archive(tmp_path, license_present=False)
    with pytest.raises(bundle.PlaywrightBundleError, match="license"):
        bundle.validate_archive(archive, contract)


def test_mac_owner_rejects_changed_license(tmp_path):
    archive, contract = _archive(tmp_path)
    owner = tmp_path / "owned"
    root = bundle.publish_archive(archive, owner, contract)
    (root / Path(contract.executable_relative).parent / "LICENSE.headless_shell").write_text("changed")
    with pytest.raises(bundle.PlaywrightBundleError, match="license"):
        bundle.validate_browser_owner(owner, contract)


def test_mac_owner_rejects_missing_execute_bit(tmp_path):
    archive, contract = _archive(tmp_path)
    root = bundle.publish_archive(archive, tmp_path / "owned", contract)
    (root / contract.executable_relative).chmod(0o644)
    with pytest.raises(bundle.PlaywrightBundleError, match="executable permission"):
        bundle.validate_browser_owner(tmp_path / "owned", contract)


def test_frozen_owner_failure_does_not_use_dev_cache(tmp_path, monkeypatch):
    archive, contract = _archive(tmp_path)
    bundle.publish_archive(archive, tmp_path / "valid-dev", contract)
    monkeypatch.setattr(bundle.sys, "_MEIPASS", str(tmp_path / "broken-frozen"), raising=False)
    monkeypatch.setattr(bundle, "validate_playwright_package", lambda contract: None)
    with pytest.raises(bundle.PlaywrightBundleError, match="missing pinned browser root"):
        bundle.resolve_browser_bundle(dev_owner=tmp_path / "valid-dev", contract=contract)


def _script(name):
    path = Path(__file__).parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_offline_acquisition_selects_mac_contract_and_reuses_verified_owner(tmp_path, monkeypatch):
    archive, contract = _archive(tmp_path)
    script = _script("acquire_playwright_browser")
    monkeypatch.setattr(script, "get_platform_contract", lambda: contract)
    monkeypatch.setattr(script, "validate_playwright_package", lambda contract: None)
    first = script.acquire(tmp_path / "cache", archive, True)
    second = script.acquire(tmp_path / "cache", None, True)
    assert first["reused"] is False
    assert second["reused"] is True
    assert first["executable_sha256"] == contract.executable_sha256


def test_download_cannot_exceed_pinned_archive_length(tmp_path, monkeypatch):
    script = _script("acquire_playwright_browser")
    contract = replace(bundle.MAC_ARM64_CONTRACT, archive_length=4)
    response = io.BytesIO(b"12345")
    monkeypatch.setattr(script.urllib.request, "urlopen", lambda *args, **kwargs: response)
    with pytest.raises(bundle.PlaywrightBundleError, match="bounded size/time"):
        script._download(tmp_path / "archive.zip", contract)
    assert not (tmp_path / "archive.zip").exists()


def test_mac_spec_collects_only_verified_platform_tree(tmp_path, monkeypatch):
    script = _script("playwright_bundle_spec_support")
    archive, contract = _archive(tmp_path)
    owner = tmp_path / "cache"
    root = bundle.publish_archive(archive, owner, contract)
    monkeypatch.setattr(bundle, "get_platform_contract", lambda: contract)
    monkeypatch.setattr(bundle, "validate_playwright_package", lambda contract: None)
    monkeypatch.setattr(script, "collect_data_files", lambda name: [])
    monkeypatch.setattr(script, "copy_metadata", lambda name: [])
    monkeypatch.setattr(script, "collect_submodules", lambda name: [])
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(owner))
    repo = Path(__file__).parents[2]
    datas, _ = script.collect_playwright_bundle(repo)
    assert (str(root), f"{contract.owner_dir}/{contract.revision_dir}") in datas
    assert not any("win64" in source for source, _ in datas)


def test_mac_packaging_asserts_actual_candidate_resource_mapping(tmp_path, monkeypatch):
    script = _script("assert_playwright_packaging")
    archive, contract = _archive(tmp_path)
    browser_metadata = b'{"fixture": true}'
    contract = replace(contract, browsers_json_sha256=hashlib.sha256(browser_metadata).hexdigest().upper())
    monkeypatch.setattr(script, "get_platform_contract", lambda: contract)
    monkeypatch.setattr(script.sys, "platform", "darwin")
    dist = tmp_path / "frozen-dist" / "deskpet-backend"
    internal = dist / "_internal"
    bundle.publish_archive(archive, internal / contract.owner_dir, contract)
    metadata = internal / "playwright/driver/package/browsers.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_bytes(browser_metadata)
    for path in [internal / "licenses/THIRD_PARTY_NOTICES.playwright-chromium.txt", internal / f"playwright-{contract.playwright_version}.dist-info/licenses/LICENSE"]:
        path.parent.mkdir(parents=True)
        path.write_text("fixture license")
    config = tmp_path / "tauri-app/src-tauri/tauri.conf.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"bundle": {"resources": {}}}))
    overlay = tmp_path / "candidate.json"
    overlay.write_text(json.dumps({"bundle": {"resources": {str(dist): "backend"}}}))
    assert script.assert_packaging(dist, tmp_path, tauri_config=overlay)["status"] == "ok"
    overlay.write_text(json.dumps({"bundle": {"resources": {str(tmp_path / "wrong-dist"): "backend"}}}))
    with pytest.raises(RuntimeError, match="does not map the verified dist root"):
        script.assert_packaging(dist, tmp_path, tauri_config=overlay)
