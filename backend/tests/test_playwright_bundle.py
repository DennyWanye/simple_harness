from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from deskpet.playwright_bundle import (
    CONTRACT,
    PlaywrightBundleError,
    assert_short_build_paths,
    publish_archive,
    resolve_browser_bundle,
    validate_archive,
    validate_browser_owner,
    validate_playwright_package,
)


def _hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    digest.update(path.read_bytes())
    return digest.hexdigest().upper()


def _fixture_archive(tmp_path: Path):
    executable = b"tiny-browser-fixture"
    archive = tmp_path / "browser.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("bin/browser.exe", executable)
        bundle.writestr("bin/LICENSE.headless_shell", "BSD")
    contract = replace(
        CONTRACT,
        revision="7",
        revision_dir="chromium_headless_shell-7",
        executable_relative="bin/browser.exe",
        executable_length=len(executable),
        executable_sha256=hashlib.sha256(executable).hexdigest().upper(),
        archive_name=archive.name,
        archive_length=archive.stat().st_size,
        archive_md5=_hash(archive, "md5"),
        archive_sha256=_hash(archive, "sha256"),
        archive_entries=2,
    )
    return archive, contract


def test_product_contract_is_exactly_pinned():
    assert CONTRACT.playwright_version == "1.61.0"
    assert CONTRACT.revision == "1228"
    assert CONTRACT.browser_version == "149.0.7827.55"
    assert CONTRACT.archive_length == 119_099_822
    assert CONTRACT.archive_md5 == "1BC51B5A9F308F4B7F47AC15A1BE049D"
    assert CONTRACT.archive_sha256 == "5CFDA0C763AA6A867CE2EFAD0C467E3220E9C5C01C4CBA02FD57AFE49EDE5457"
    assert CONTRACT.executable_sha256 == "28016DF6864D302434C9231E1F9F1A8A7ECC512CB2FE3FAABB2A36130B96BCF1"


def test_installed_playwright_package_matches_contract():
    assert validate_playwright_package().name == "playwright"


def test_archive_validation_and_atomic_publish(tmp_path: Path):
    archive, contract = _fixture_archive(tmp_path)
    owner = tmp_path / "owner"
    validate_archive(archive, contract)
    root = publish_archive(archive, owner, contract)
    assert root == owner.resolve() / contract.revision_dir
    assert validate_browser_owner(owner, contract) == root
    assert json.loads((root / contract.product_manifest).read_text())["revision"] == "7"
    assert not list(owner.glob("*.stage-*"))


def test_atomic_publish_cleans_staging_when_replace_fails(tmp_path: Path, monkeypatch):
    archive, contract = _fixture_archive(tmp_path)
    owner = tmp_path / "owner"

    def fail_replace(_source, _target):
        raise OSError("injected replace failure")

    monkeypatch.setattr("deskpet.playwright_bundle.os.replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        publish_archive(archive, owner, contract)
    assert not (owner / contract.revision_dir).exists()
    assert not list(owner.glob("*.stage-*"))


@pytest.mark.parametrize("mutation", ["length", "md5", "sha", "entries", "exe"])
def test_archive_tampering_fails_closed(tmp_path: Path, mutation: str):
    archive, contract = _fixture_archive(tmp_path)
    if mutation == "length":
        contract = replace(contract, archive_length=contract.archive_length + 1)
    elif mutation == "md5":
        contract = replace(contract, archive_md5="0" * 32)
    elif mutation == "sha":
        contract = replace(contract, archive_sha256="0" * 64)
    elif mutation == "entries":
        contract = replace(contract, archive_entries=3)
    else:
        contract = replace(contract, executable_sha256="0" * 64)
    with pytest.raises(PlaywrightBundleError):
        validate_archive(archive, contract)


def test_invalid_zip_fails_closed_after_digest_checks(tmp_path: Path):
    archive = tmp_path / "not-a-zip.bin"
    archive.write_bytes(b"not a zip archive")
    contract = replace(
        CONTRACT,
        archive_length=archive.stat().st_size,
        archive_md5=_hash(archive, "md5"),
        archive_sha256=_hash(archive, "sha256"),
    )
    with pytest.raises(PlaywrightBundleError, match="invalid browser archive"):
        validate_archive(archive, contract)


def test_installed_tampering_and_foreign_revision_fail_closed(tmp_path: Path):
    archive, contract = _fixture_archive(tmp_path)
    owner = tmp_path / "owner"
    root = publish_archive(archive, owner, contract)
    (owner / "chromium_headless_shell-8").mkdir()
    with pytest.raises(PlaywrightBundleError, match="unexpected browser revision"):
        validate_browser_owner(owner, contract)
    (owner / "chromium_headless_shell-8").rmdir()
    (root / "bin/browser.exe").write_bytes(b"tampered")
    with pytest.raises(PlaywrightBundleError, match="length"):
        validate_browser_owner(owner, contract)


def test_resolver_priorities_and_public_diagnostic(tmp_path: Path, monkeypatch):
    archive, contract = _fixture_archive(tmp_path)
    owner = tmp_path / "same-owner"
    publish_archive(archive, owner, contract)
    monkeypatch.setattr("deskpet.playwright_bundle.validate_playwright_package", lambda _contract: None)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    diagnostic = resolve_browser_bundle(test_override=owner, dev_owner=tmp_path / "broken", contract=contract)
    assert diagnostic.source == "test-override"
    assert diagnostic.environment()["PLAYWRIGHT_BROWSERS_PATH"] == str(owner.resolve())
    public = diagnostic.public_dict()
    assert public["playwright_version"] == "1.61.0"
    assert public["expected_revision"] == public["actual_revision"] == "7"
    assert public["launch_status"] == "not_checked"
    assert str(owner) not in json.dumps(public)


def test_resolver_never_falls_back_from_frozen_to_dev(tmp_path: Path, monkeypatch):
    archive, contract = _fixture_archive(tmp_path)
    dev = tmp_path / "dev"
    publish_archive(archive, dev, contract)
    monkeypatch.setattr("deskpet.playwright_bundle.validate_playwright_package", lambda _contract: None)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "broken-frozen"), raising=False)
    with pytest.raises(PlaywrightBundleError, match="missing pinned browser root"):
        resolve_browser_bundle(dev_owner=dev, contract=contract)


def test_resolver_supports_frozen_and_tauri(tmp_path: Path, monkeypatch):
    archive, contract = _fixture_archive(tmp_path)
    frozen = tmp_path / "frozen"
    publish_archive(archive, frozen / contract.owner_dir, contract)
    monkeypatch.setattr("deskpet.playwright_bundle.validate_playwright_package", lambda _contract: None)
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen), raising=False)
    diagnostic = resolve_browser_bundle(dev_owner=tmp_path / "missing", contract=contract)
    assert diagnostic.source == "frozen"
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    tauri = tmp_path / "tauri"
    publish_archive(archive, tauri / contract.owner_dir, contract)
    diagnostic = resolve_browser_bundle(
        tauri_resource_root=tauri, dev_owner=tmp_path / "missing", contract=contract
    )
    assert diagnostic.source == "tauri"


def test_short_path_assertion():
    assert_short_build_paths(Path("C:/DPW6/d"), Path("C:/DPW6/w"))
    with pytest.raises(PlaywrightBundleError, match="too long"):
        assert_short_build_paths(Path("C:/") / ("x" * 220))


def test_specs_and_tauri_have_single_packaging_owner():
    repo = Path(__file__).resolve().parents[2]
    spec = (repo / "backend" / "deskpet-backend.spec").read_text(encoding="utf-8")
    assert spec.count("collect_playwright_bundle(_repo_root)") == 1
    tauri = json.loads((repo / "tauri-app" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    resources = tauri["bundle"]["resources"]
    backend = [source for source in resources if "dist-portable/deskpet-backend" in source.replace("\\", "/")]
    browser = [source for source in resources if "playwright" in source.lower() or "chromium" in source.lower()]
    assert len(backend) == 1
    assert browser == []
