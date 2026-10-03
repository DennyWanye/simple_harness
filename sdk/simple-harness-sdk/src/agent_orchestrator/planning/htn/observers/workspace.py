# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""The desktop workspace observer: three read-only looks at a Mission's files (阶段 D).

What a desktop Mission's "world" is: the files it was seeded with, the artifacts its
accepted steps produced (each path's version in force), and the reference material
registered for it.  Three CLOSED predicates read exactly that, from the store — no disk
access, no model call, nothing written:

* ``desktop.file-present(path)`` — the Mission's file view has this path;
* ``desktop.file-sha256(path, sha256)`` — the content in force at this path has this hash;
* ``desktop.source-current(path, version_hash)`` — the reference material registered at
  this path is, right now, this version (not superseded, not revoked).

The implementation lives here once; the product deployment and the product-shaped test
world both register it (``workspace_predicates`` / ``workspace_observers``).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from ....contracts.semantic_base import VersionedRef, content_hash_of
from ....knowledge.predicates import (
    ArgumentType,
    PredicateParameter,
    PredicateSignature,
    WorldAssumption,
)
from . import Observation, PredicateObserver, denial, observed, unavailable

OBSERVER_ID = "desktop.workspace-reader"
OBSERVER_VERSION = "1"
FILE_PRESENT = "desktop.file-present"
FILE_SHA256 = "desktop.file-sha256"
SOURCE_CURRENT = "desktop.source-current"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _signature(predicate_id: str, statement: str, *parameters: str) -> PredicateSignature:
    body = {"id": predicate_id, "version": 1, "parameters": list(parameters), "statement": statement}
    return PredicateSignature(
        predicate_ref=VersionedRef(predicate_id, 1, content_hash_of(body)),
        parameters=tuple(PredicateParameter(name, ArgumentType.STRING) for name in parameters),
        world_assumption=WorldAssumption.CLOSED,
        observer_ids=(OBSERVER_ID,),
        statement=statement,
    )


def workspace_predicates() -> tuple[PredicateSignature, ...]:
    """The three predicate declarations a desktop deployment registers."""

    return (
        _signature(FILE_PRESENT, "the Mission's file view has a file at this path", "path"),
        _signature(FILE_SHA256, "the content in force at this path has this SHA-256", "path", "sha256"),
        _signature(SOURCE_CURRENT, "the reference material registered at this path is this version",
                   "path", "version_hash"),
    )


def mission_file_view(store: Any, mission_id: str) -> dict[str, str]:
    """``path → content hash`` of the Mission's files as they stand: its seed, overlaid by
    the accepted artifacts, each path at its version in force (the same notion of "in
    force" the knowledge standing uses: the highest accepted version)."""

    view: dict[str, str] = {}
    mission = store.get_mission(mission_id)
    seed = (mission.final_report or {}).get("workspace_seed") if mission is not None else None
    for path, body in dict(seed or {}).items():
        text = str(body)
        view[str(path)] = text if _HEX64.match(text) else hashlib.sha256(text.encode("utf-8")).hexdigest()
    versions: dict[str, int] = {}
    for artifact in store.list_mission_artifacts(mission_id):
        if artifact.verification_status != "VERIFIED" or artifact.path.startswith("."):
            continue
        if artifact.version >= versions.get(artifact.path, -1):
            versions[artifact.path] = artifact.version
            view[artifact.path] = artifact.content_hash
    return view


class WorkspaceObserver:
    """Reads the Mission's file view and its registered reference material."""

    def __init__(self, store: Any, mission_id: str) -> None:
        self._store, self._mission_id = store, mission_id

    @property
    def observer_id(self) -> str:
        return OBSERVER_ID

    def predicate_ids(self) -> tuple[str, ...]:
        return (FILE_PRESENT, FILE_SHA256, SOURCE_CURRENT)

    def observe(self, signature: PredicateSignature, arguments: Mapping[str, Any], *, now_ms: int) -> Observation:
        predicate = signature.predicate_ref.id
        path = str(arguments.get("path", ""))
        try:
            if predicate == SOURCE_CURRENT:
                row = self._store.connection.execute(
                    "SELECT version_hash FROM sources WHERE mission_id=? AND path=? AND superseded_by IS NULL"
                    " AND revoked=0", (self._mission_id, path)).fetchall()
                if len(row) > 1:
                    return unavailable(OBSERVER_ID, predicate, f"{path!r} has more than one version in force")
                holds = bool(row) and str(row[0][0]) == str(arguments.get("version_hash"))
                scope = f"sources:{self._mission_id}"
            else:
                view = mission_file_view(self._store, self._mission_id)
                holds = path in view and (predicate == FILE_PRESENT or view[path] == str(arguments.get("sha256")))
                scope = f"files:{self._mission_id}"
        except Exception as error:  # noqa: BLE001 - not being able to look is the third answer
            return unavailable(OBSERVER_ID, predicate, f"the store could not be read: {type(error).__name__}")
        if holds:
            return observed(signature, arguments, polarity=True, observer_id=OBSERVER_ID, now_ms=now_ms,
                            observer_version=OBSERVER_VERSION)
        return denial(signature, arguments, observer_id=OBSERVER_ID, now_ms=now_ms, coverage_scope=scope,
                      observer_version=OBSERVER_VERSION)


def workspace_observers(store: Any, mission_id: str) -> tuple[PredicateObserver, ...]:
    return (WorkspaceObserver(store, mission_id),)


__all__ = ("FILE_PRESENT", "FILE_SHA256", "OBSERVER_ID", "SOURCE_CURRENT", "WorkspaceObserver",
           "mission_file_view", "workspace_observers", "workspace_predicates")
