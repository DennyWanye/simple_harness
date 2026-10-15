#!/usr/bin/env python3
"""代码质量整治：提交前确认执行图部署清单与 SDK 源码一致（阶段 5G 第一批漏更新清单的教训）。

用法：python3 scripts/code_quality/manifest_fresh.py   一致退出码 0，否则列出不一致的文件。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "sdk/simple-harness-sdk/src"
MANIFEST = "agent_orchestrator/orchestrator/taskgraph_deployment_manifest.json"


def main() -> int:
    sources = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))["source_files"]
    actual = {
        p.relative_to(ROOT).as_posix()
        for ns in ("agent_orchestrator", "simple_harness")
        for p in (ROOT / ns).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}
    } - {MANIFEST}
    bad = sorted(actual ^ set(sources))
    for name in sorted(actual & set(sources)):
        digest = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        if sources[name] not in (digest, f"sha256:{digest}"):
            bad.append(name)
    for name in bad:
        print(f"部署清单不一致：{name}")
    print("部署清单：" + ("最新" if not bad else f"过期（{len(bad)} 个文件），先跑 slice_check.sh sdk 重生成"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
