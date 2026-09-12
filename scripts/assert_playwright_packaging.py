#!/usr/bin/env python3
"""Static and artifact assertions for the product-owned Playwright bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from deskpet.playwright_bundle import get_platform_contract, validate_browser_owner  # noqa: E402


def assert_packaging(dist_root: Path, repo_root: Path = REPO_ROOT, *, tauri_config: Path | None = None) -> dict[str, object]:
    contract = get_platform_contract()
    internal = dist_root.resolve() / "_internal"
    owner = internal / contract.owner_dir
    revision_root = validate_browser_owner(owner, contract)
    browsers_json = internal / "playwright" / "driver" / "package" / "browsers.json"
    notice = internal / "licenses" / "THIRD_PARTY_NOTICES.playwright-chromium.txt"
    playwright_license = (
        internal / f"playwright-{contract.playwright_version}.dist-info" / "licenses" / "LICENSE"
    )
    if not browsers_json.is_file() or not notice.is_file() or not playwright_license.is_file():
        raise RuntimeError("frozen package lacks Playwright driver metadata or required licenses")
    if hashlib.sha256(browsers_json.read_bytes()).hexdigest().upper() != contract.browsers_json_sha256:
        raise RuntimeError("frozen Playwright browsers.json does not match the product pin")
    forbidden = [path for path in owner.rglob("*") if path.name in {"downloads", ".links"}]
    if forbidden:
        raise RuntimeError(f"cache-only paths leaked into frozen package: {forbidden}")

    config_root = repo_root / "tauri-app" / "src-tauri"
    tauri = json.loads((config_root / "tauri.conf.json").read_text(encoding="utf-8"))
    resources = tauri["bundle"]["resources"]
    overlays = [config_root / "tauri.windows.conf.json"] if sys.platform == "win32" else []
    if tauri_config is not None:
        overlays.append(tauri_config)
    for overlay in overlays:
        resources.update(json.loads(overlay.read_text(encoding="utf-8")).get("bundle", {}).get("resources", {}))
    backend_sources = [source for source, target in resources.items() if target.replace("\\", "/").rstrip("/") == "backend"]
    browser_sources = [source for source in resources if "playwright" in source.casefold() or "chromium" in source.casefold()]
    if len(backend_sources) != 1 or browser_sources:
        raise RuntimeError("Tauri must own one backend onedir and no second browser resource")
    if (config_root / backend_sources[0]).resolve() != dist_root.resolve():
        raise RuntimeError("Tauri backend resource does not map the verified dist root")
    total = sum(path.stat().st_size for path in dist_root.rglob("*") if path.is_file())
    return {
        "status": "ok",
        "dist_root": str(dist_root.resolve()),
        "total_bytes": total,
        "file_count": sum(1 for path in dist_root.rglob("*") if path.is_file()),
        "revision_root": str(revision_root),
        "revision": contract.revision,
        "executable_sha256": contract.executable_sha256,
        "tauri_backend_resource_count": 1,
        "tauri_browser_resource_count": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist_root", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--tauri-config", type=Path, help="candidate overlay used by the Tauri build")
    args = parser.parse_args()
    result = assert_packaging(args.dist_root, tauri_config=args.tauri_config)
    print(json.dumps(result, indent=2) if args.json else result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
