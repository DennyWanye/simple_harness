#!/usr/bin/env python3
"""Fail when Workbench-owned UI surfaces introduce literal hex colors."""

from __future__ import annotations

import re
import sys
from pathlib import Path


HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
REPO_ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = REPO_ROOT / "tauri-app" / "src"


def audited_files() -> list[Path]:
    files = [
        UI_ROOT / "components" / "WorkbenchShell.tsx",
        UI_ROOT / "components" / "Sidebar.tsx",
        UI_ROOT / "components" / "SessionList.tsx",
        UI_ROOT / "components" / "ui.tsx",
    ]
    files.extend(sorted((UI_ROOT / "views").rglob("*.tsx")))
    return files


def main() -> int:
    findings: list[str] = []
    missing: list[str] = []
    for path in audited_files():
        if not path.is_file():
            missing.append(str(path.relative_to(REPO_ROOT)))
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if HEX_COLOR.search(line):
                findings.append(
                    f"{path.relative_to(REPO_ROOT)}:{line_number}:{line.strip()}"
                )

    if missing:
        print("WORKBENCH_THEME_AUDIT: FAIL (missing audited files)")
        print("\n".join(missing))
        return 2
    if findings:
        print("WORKBENCH_THEME_AUDIT: FAIL (literal hex colors found)")
        print("\n".join(findings))
        return 1

    print(f"WORKBENCH_THEME_AUDIT: PASS ({len(audited_files())} files, 0 literal hex colors)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
