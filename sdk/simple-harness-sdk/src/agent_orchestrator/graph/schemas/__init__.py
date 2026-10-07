# SPDX-License-Identifier: Apache-2.0
"""执行图对外合同的 JSON Schema（原计划 §4 / 附录 E；第 2 批 T06、T07；推后第 3 批 U09）。

这些文件是对外发布的边界合同文本，随 SDK 包交付。生产代码**不用它们校验**——校验只走各自的
Python 边界 codec（``network_codec`` / ``execution_contracts`` / ``notification_contracts`` /
``structural_diff`` / ``view_contracts``），同一件事只留一条路径；Schema 与 codec 对同一正负样本
一致由用例守住（``tests/orchestrator/full_target/test_taskgraph_schema_contracts.py``）。
修订记录见同目录 ``README.md``。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_NAMES: tuple[str, ...] = (
    "network-document-v1",
    "preview-binding-v1",
    "followup-v1",
    "taskgraph-error-v1",
    "taskgraph-view-v1",
    "taskgraph-explanation-v1",
    "taskgraph-diff-v1",
    "taskgraph-convergence-view-v2",
    # 推后第 3 批 U09：执行过程主画面与回合详情（HTN §17.2"前端用同一公开 Schema 验证"）
    "taskgraph-execution-view-v1",
    "taskgraph-execution-detail-v1",
)


def schema_path(name: str) -> Path:
    if name not in SCHEMA_NAMES:
        raise KeyError(name)
    return Path(__file__).with_name(name + ".schema.json")


def load_schema(name: str) -> dict[str, Any]:
    """按名读一份合同 Schema；名字不在 ``SCHEMA_NAMES`` 之内即 ``KeyError``。"""
    return json.loads(schema_path(name).read_text(encoding="utf-8"))


__all__ = ("SCHEMA_NAMES", "load_schema", "schema_path")
