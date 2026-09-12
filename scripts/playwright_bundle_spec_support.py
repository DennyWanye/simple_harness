"""Single PyInstaller collection owner for the pinned Playwright browser."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


def collect_playwright_bundle(repo_root: Path) -> tuple[list[tuple[str, str]], list[str]]:
    backend = repo_root / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from deskpet.playwright_bundle import get_platform_contract, validate_browser_owner, validate_playwright_package

    contract = get_platform_contract()
    validate_playwright_package(contract)
    raw_owner = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not raw_owner:
        raise RuntimeError("PLAYWRIGHT_BROWSERS_PATH must point to the isolated product cache")
    owner = Path(raw_owner).resolve()
    revision_root = validate_browser_owner(owner, contract)
    notice = repo_root / "resources" / "THIRD_PARTY_NOTICES.playwright-chromium.txt"
    if not notice.is_file():
        raise RuntimeError(f"missing third-party notice: {notice}")

    datas: list[tuple[str, str]] = []
    datas += collect_data_files("playwright")
    datas += copy_metadata("playwright")
    datas.append((str(revision_root), f"{contract.owner_dir}/{contract.revision_dir}"))
    datas.append((str(notice), "licenses"))
    hidden = collect_submodules("playwright")
    print(f"[spec] Playwright {contract.playwright_version}; headless shell r{contract.revision}; {contract.executable_relative}")
    return datas, hidden
