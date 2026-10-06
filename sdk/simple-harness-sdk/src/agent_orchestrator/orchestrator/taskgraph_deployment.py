# SPDX-License-Identifier: Apache-2.0
"""Read the installed derivation of a verified HTN wiring delivery.

The build manifest records actual upstream evidence and the complete derived
package inventory. It is not a TaskGraph test verdict or a planning grant.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from simple_harness.contracts import canonical_json

from ..graph.revision_records import SourceRef
from ..runtime.planning_operations import SourceUnavailable
from ..storage.store import Store
from .taskgraph_policy import InstalledGraphPolicy


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


#: 清单自己在安装目录里的位置；盘点安装文件时按这个相对路径排除它（与构建脚本同一条规则），
#: 不按读取方当前指向的文件排除——测试把读取方指到别的文件时，盘点结果不能因此变化。
MANIFEST_REL = "agent_orchestrator/orchestrator/taskgraph_deployment_manifest.json"


class InstalledHtnWiringAcceptance:
    """Fixed package-owned source; no IPC argument or environment bypass."""
    def __init__(self) -> None:
        self.root = Path(__file__).resolve().parents[2]
        self.manifest = self.root / MANIFEST_REL

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.manifest.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or set(value) != {
                    "schema", "deployment_id", "upstream", "source_files", "taskgraph_acceptance",
                    "acceptance_evidence"}
                    or value["schema"] != "taskgraph-htn-wiring-derivation-v1"
                    or not _digest(value["deployment_id"])):
                raise ValueError("invalid deployment manifest")
            upstream = value["upstream"]
            # 全业务重放 v3：上游局必须重放一致，并带 v3 报告文件的哈希
            if (not isinstance(upstream, dict) or set(upstream) != {
                    "status", "scope", "wheel_sha256", "manifest_sha256", "source_inputs_sha256",
                    "mission_id", "mission_receipt_sha256", "cold_replay_receipt_sha256",
                    "business_replay", "business_replay_receipt_sha256"}
                    or upstream["status"] != "READY_FOR_TASKGRAPH_WIRING"
                    or upstream["scope"] != "CORE_INTEGRATION_E2E"
                    or upstream["business_replay"] != "CONSISTENT"
                    or not isinstance(upstream["mission_id"], str) or not upstream["mission_id"]
                    or any(not _digest(v) for k, v in upstream.items() if k.endswith("_sha256"))):
                raise ValueError("actual HTN wiring evidence is missing")
            sources = value["source_files"]
            if not isinstance(sources, dict) or not sources:
                raise ValueError("installed source inventory is missing")
            actual_names = {p.relative_to(self.root).as_posix()
                for namespace in ("agent_orchestrator", "simple_harness")
                for p in (self.root / namespace).rglob("*")
                if p.is_file() and "__pycache__" not in p.parts
                and p.relative_to(self.root).as_posix() != MANIFEST_REL
                and p.suffix not in {".pyc", ".pyo"}}
            if actual_names != set(sources):
                raise ValueError("installed source inventory changed")
            for name, digest in sources.items():
                path = PurePosixPath(name)
                if (path.is_absolute() or ".." in path.parts
                        or path.parts[0] not in {"agent_orchestrator", "simple_harness"}
                        or not _digest(digest) or _hash((self.root / name).read_bytes()) != digest):
                    raise ValueError("installed source bytes changed")
            identity = {k: v for k, v in value.items() if k != "deployment_id"}
            if _hash(canonical_json(identity).encode()) != value["deployment_id"]:
                raise ValueError("deployment identity changed")
            # 执行图验收门（原计划 §0.3 / §16，补齐第 1 批 V12）：清单里的验收结论只有两种取值。
            # NOT_RUN 时不得带任何证据；VALIDATED 时必须带门报告的哈希，且门报告是对着**这一份**源码
            # 清单跑的（source_files_sha256 相等）。读取方从不把 NOT_RUN 当成通过；生产开关由 Host 按
            # :meth:`acceptance_status` 决定开不开（SDK 测试世界允许 NOT_RUN，门本身就是这些测试）。
            if value["taskgraph_acceptance"] not in {"NOT_RUN", "VALIDATED"}:
                raise ValueError("invalid TaskGraph validation scope")
            evidence = value["acceptance_evidence"]
            if value["taskgraph_acceptance"] == "NOT_RUN":
                if evidence is not None:
                    raise ValueError("TaskGraph acceptance evidence without a validation")
            else:
                if (not isinstance(evidence, dict) or set(evidence) != {
                        "gate_sha256", "gate_status", "gate_run_at", "source_files_sha256"}
                        or evidence["gate_status"] != "PASS"
                        or not _digest(evidence["gate_sha256"])
                        or not isinstance(evidence["gate_run_at"], str) or not evidence["gate_run_at"]
                        or evidence["source_files_sha256"] != _hash(canonical_json(sources).encode())):
                    raise ValueError("TaskGraph acceptance evidence does not match this package")
            return value
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
            raise SourceUnavailable("taskgraph_deployed_source_unverified") from error

    def acceptance_status(self) -> str:
        """``NOT_RUN`` or ``VALIDATED`` from a manifest that verifies; refusals propagate."""

        return str(self._read()["taskgraph_acceptance"])

    def __call__(self, store: Store, mission_id: str, policy: InstalledGraphPolicy) -> SourceRef:
        if not store.connection.in_transaction or store.get_mission(mission_id) is None:
            raise SourceUnavailable("taskgraph_deployment_requires_original_mission_transaction")
        if not isinstance(policy, InstalledGraphPolicy):
            raise SourceUnavailable("taskgraph_deployment_policy_missing")
        value = self._read()
        return SourceRef(channel="h1h_deployment_acceptance", identity=value["deployment_id"],
                         revision=1, digest=_hash(canonical_json(value).encode()))
