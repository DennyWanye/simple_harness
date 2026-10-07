# SPDX-License-Identifier: Apache-2.0
"""Managed-root identity and current read grants; never execution resurrection.

The protected state file is deliberately outside the backup's file inventory.
It points to an original Commit receipt. Neither a backup nor a database receipt
alone opens this gate. Current external ACL checks remain mandatory on each read.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..artifacts.store import open_nofollow
from .codec import AssuranceError, decode, fields, fingerprint, integer, text
from .evidence import ReadItem
from .refs import AssuranceRef

if TYPE_CHECKING:
    from ..storage.store import Store

STATE_FILE = "assurance-root-state.json"
ROOT_KIND = "AssuranceRootInstalled"


@dataclass(frozen=True, slots=True)
class RootIdentity:
    root_incarnation_id: str


@dataclass(frozen=True, slots=True)
class CurrentReadPermission:
    """Result of a deployed CURRENT authority, not something decoded from a request."""

    access: ReadItem
    policy: ReadItem
    not_after_ms: int

    def __post_init__(self) -> None:
        if self.access.channel != "ACCESS" or self.policy.channel != "POLICY":
            raise AssuranceError("ACCESS_POLICY_WITNESS_REQUIRED")
        integer(self.not_after_ms)


def _read(path: Path, *, protected: bool = False) -> bytes:
    try:
        with open_nofollow(path) as stream:
            if protected and stat.S_IMODE(os.fstat(stream.fileno()).st_mode) & 0o077:
                raise AssuranceError("ROOT_STATE_PERMISSIONS")
            data = stream.read(32 * 1024 * 1024 + 1)
        if len(data) > 32 * 1024 * 1024:
            raise AssuranceError("ROOT_MANIFEST_LIMIT")
        return data
    except (OSError, ValueError) as error:
        if isinstance(error, AssuranceError):
            raise
        raise AssuranceError("ROOT_QUARANTINED") from error


class AssuranceRootGate:
    def __init__(self, store: Store, root: Path) -> None:
        self.store = store
        self.root = Path(root).absolute()
        if (
            self.root.is_symlink()
            or store.path.resolve() != (self.root / "orchestrator.db").resolve()
        ):
            raise AssuranceError("ROOT_STORE_MISMATCH")

    @staticmethod
    def required(store: Store, root: Path) -> bool:
        # An existing Assurance root may never opt out merely by omitting its
        # deployment collaborator.
        return (
            (root / STATE_FILE).exists()
            or (root / STATE_FILE).is_symlink()
            or store.connection.execute(
                "SELECT 1 FROM assurance_mission_bindings UNION ALL SELECT 1 FROM commit_receipts "
                "WHERE kind='AssuranceRootInstalled' LIMIT 1"
            ).fetchone()
            is not None
        )

    def _head_locked(self, root_id: str):
        # Current root authorization head, NOT an exact-object/latest resolver.
        return self.store.connection.execute(
            "SELECT rowid,* FROM commit_receipts WHERE kind=? "
            "AND json_extract(receipt_json,'$.root_incarnation_id')=? "
            "AND json_extract(receipt_json,'$.root_path')=? ORDER BY rowid DESC LIMIT 1",
            (ROOT_KIND, root_id, str(self.root)),
        ).fetchone()

    def _state_locked(self) -> tuple[dict, dict]:
        state = fields(
            decode(_read(self.root / STATE_FILE, protected=True)),
            {
                "schema_version",
                "root_incarnation_id",
                "receipt_ref",
            },
        )
        if integer(state["schema_version"]) != 1:
            raise AssuranceError("ROOT_STATE_INVALID")
        ref = AssuranceRef.from_json(state["receipt_ref"], kinds={"commit_receipt"})
        row = self._head_locked(text(state["root_incarnation_id"]))
        if row is None or row["commit_id"] != ref.pin.id or ref.pin.revision != 0:
            raise AssuranceError("ROOT_QUARANTINED")
        common = {
            "schema_version",
            "mode",
            "root_path",
            "root_incarnation_id",
            "restore_manifest_hash",
            "tenant_id",
            "principal_id",
            "command_id",
            "issued_at_ms",
        }
        body = fields(decode(row["receipt_json"]), common | {"installation_origin"})
        if (
            fingerprint(body) != ref.pin.content_hash
            or row["proposal_hash"] != fingerprint(body)
            or row["subject_id"] != state["root_incarnation_id"]
            or row["base_version"] != 0
            or integer(body["schema_version"]) != 1
            or body.get("root_incarnation_id") != state["root_incarnation_id"]
            or body.get("root_path") != str(self.root)
        ):
            raise AssuranceError("ROOT_STATE_MISMATCH")
        for key in ("tenant_id", "principal_id", "command_id"):
            text(body[key])
        integer(body["issued_at_ms"])
        # ``restore_manifest_hash`` stays in the receipt body and is always null: the
        # managed restore it described was removed (2026-10-03, decision ⑤).
        if (
            body["mode"] != "NATIVE_INSTALLATION"
            or body["restore_manifest_hash"] is not None
            or body["installation_origin"] not in {"EMPTY", "HISTORICAL"}
        ):
            raise AssuranceError("ROOT_QUARANTINED")
        return dict(row), body

    def require_execution(self) -> RootIdentity:
        with self.store.read_view():
            _row, body = self._state_locked()
            return RootIdentity(body["root_incarnation_id"])

    def diagnostic(self) -> dict:
        # No titles, summaries, source paths, object ids or evidence contents.
        try:
            with self.store.read_view():
                self._state_locked()
            state = "NATIVE"
        except AssuranceError:
            state = "QUARANTINED"
        return {
            "state": state,
            "execution_allowed": state == "NATIVE",
            "current_authentication_required": state == "QUARANTINED",
        }
