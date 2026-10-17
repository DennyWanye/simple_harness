# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""代码质量整治阶段 10：工作流定义的字节身份快照（阶段10-切片设计.md 裁决执行条件 4）。

工作流清单哈希把节点函数与选择函数的源码原文（含注释）算进去，改到这些函数体内的注释就会
改变 expected_manifest_hash / expected_implementation_fingerprint。每个只改注释的提交前后各取
一次快照，必须相同。

用法（在仓库根目录运行）：
    backend/.venv/bin/python scripts/code_quality/workflow_identity.py product > a.json   # 后端产品 2 套
    sdk/simple-harness-sdk/.venv/bin/python scripts/code_quality/workflow_identity.py official > b.json  # SDK 官方 3 套
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def snapshot(registrations) -> dict[str, dict[str, str]]:
    return {
        f"{r.profile.workflow_name}@{r.profile.workflow_version}": {
            "expected_manifest_hash": r.expected_manifest_hash,
            "expected_implementation_fingerprint": r.expected_implementation_fingerprint,
        }
        for r in registrations
    }


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    owner = object()
    if mode == "product":
        sys.path.insert(0, str(REPO / "backend"))
        from deskpet.sdk_adapters.workflows import build_product_workflow_registrations

        regs = build_product_workflow_registrations(generation=1, transaction_owner=owner)
    elif mode == "official":
        sys.path.insert(0, str(REPO / "sdk/simple-harness-sdk/src"))
        from simple_harness.workflows.capability_build.factory import build_capability_build_registration
        from simple_harness.workflows.durable_task.factory import build_durable_task_registration
        from simple_harness.workflows.personal_v1.factory import build_personal_v1_registration

        regs = tuple(
            f(generation=1, transaction_owner=owner)
            for f in (build_durable_task_registration, build_personal_v1_registration, build_capability_build_registration)
        )
    else:
        print("用法：workflow_identity.py product|official", file=sys.stderr)
        return 2
    print(json.dumps(snapshot(regs), ensure_ascii=False, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
