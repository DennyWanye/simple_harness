# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""The frozen execution snapshot of one Attempt.

Written once, when the Attempt is created: what the Task was allowed to do, which
complete input files it was given (the Mission's seed files and the upstream artifacts
it mounts, each stored by content hash), and under which domain, policy and sources.
Readers take the snapshot from the Attempt's intent; nothing recomputes it from the
live workspace.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from ..artifacts.store import ArtifactStore, read_verified
from ..contracts import ContractError, Task
from ..contracts.models import sha256_hex
from ..storage.store import Store
from ..verification.evidence_resolver import _safe_path

# Known graph bookkeeping cannot change permission or the meaning of a criterion.
_BOOKKEEPING = frozenset(
    {
        "graph_version",
        "change_id",
        "proposed_by_attempt",
        "supersedes_task",
        "supersede_depth",
        "replaced_by",
    }
)


def _path(path: Any) -> str:
    if not isinstance(path, str) or not _safe_path(path):
        raise ContractError("attempt execution: noncanonical material path")
    return path


def freeze_attempt_execution(
    store: Store,
    artifact_store: ArtifactStore,
    *,
    task: Task,
    intent_config: Mapping[str, Any],
    inputs: Sequence[Mapping[str, Any]],
    retry_of: str | None,
    validated_input_paths: Mapping[str, str] | None = None,
    validated_input_identities: frozenset[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    """System-only creation hook. Never apply it when replaying an old intent.

    ``validated_input_paths`` comes only from Commit's own checked completion inputs,
    never from intent_config or model metadata. It maps a real artifact ID to its one
    approved mount path, without changing its bytes. TaskGraph instead supplies the
    exact (artifact, mount path, hash) set checked by its DATA resolver, which also
    permits one artifact at two distinct approved paths without collapsing either
    mount. The stored manifests include whole input files, not selected excerpts.
    """
    mission = store.get_mission(task.mission_id)
    if mission is None:
        raise ContractError("attempt execution: mission unavailable")
    files: dict[str, dict[str, Any]] = {}
    for path, text in (mission.final_report or {}).get("workspace_seed", {}).items():
        _path(path)
        if not isinstance(text, str):
            raise ContractError("attempt execution: seed must be text")
        data = text.encode("utf-8")
        files[path] = {"content_hash": artifact_store.put_bytes(data), "kind": "seed"}
    validated_paths = dict(validated_input_paths or {})
    if set(validated_paths) - {item.get("artifact_id") for item in inputs}:
        raise ContractError("attempt execution: validated input path has no actual input")
    if validated_input_identities is not None:
        actual = frozenset((str(item.get("artifact_id")), str(item.get("path")), str(item.get("content_hash")))
                           for item in inputs)
        if actual != validated_input_identities or len(actual) != len(inputs):
            raise ContractError("attempt execution: validated input identities differ from actual inputs")
    mounted_paths: set[str] = set()
    for item in inputs:
        artifact = store.get_artifact(item.get("artifact_id", ""))
        if (
            artifact is None
            or artifact.mission_id != mission.id
            or artifact.content_hash != item.get("content_hash")
            or (validated_input_identities is None
                and validated_paths.get(artifact.id, artifact.path) != item.get("path"))
            or (validated_input_identities is not None
                and (artifact.id, str(item.get("path")), artifact.content_hash) not in validated_input_identities)
        ):
            raise ContractError("attempt execution: frozen input identity mismatch")
        mounted = _path(item["path"])
        folded = unicodedata.normalize("NFC", mounted).casefold()
        if folded in mounted_paths:
            raise ContractError("attempt execution: duplicate input mount path")
        mounted_paths.add(folded)
        data = read_verified(artifact)
        files[mounted] = {
            "content_hash": artifact_store.put_bytes(data),
            "kind": "artifact",
            "artifact_id": artifact.id,
            "original_path": artifact.path,
        }
    domain = store.get_mission_domain(mission.id)
    body = {
        "schema_version": 1,
        "task_id": task.id,
        "allowed_tools": list(task.allowed_tools),
        "budget": task.budget.to_json(),
        "dependency_ids": list(task.dependency_ids),
        "context": {key: value for key, value in task.context.items() if key not in _BOOKKEEPING},
        "domain": None if domain is None else domain["json"],
        "policy_binding": store.get_mission_policy(mission.id),
        "source_versions": dict(intent_config.get("source_versions", {})),
        "source_roots": list(intent_config.get("source_roots", ())),
        "inputs": [dict(item) for item in inputs],
        "files": files,
        "retry_of": retry_of,
    }
    return {**body, "constraints_revision": sha256_hex(body)}


__all__ = ("freeze_attempt_execution",)
