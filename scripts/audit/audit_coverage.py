#!/usr/bin/env python3
"""HM-AC-7 全操作审计覆盖核对 CLI（薄包装，逻辑在 deskpet.quality.audit_coverage）。

用法：
  python scripts/audit/audit_coverage.py --evidence <primary-ui-dir|userdata/data> \
      [--json out.json] [--markdown out.md] [--fail-on-gap]

证据只读：DB 先复制到临时目录再打开；不输出任何 payload 正文或密钥。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from deskpet.quality.audit_coverage import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
