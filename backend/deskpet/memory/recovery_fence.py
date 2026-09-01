# SPDX-License-Identifier: BUSL-1.1

"""Host-composition seam for the v42 recovery lifecycle.

The public builder binds the database and artifact directory once.  Request
payloads cannot select either authority boundary; ``subject`` is only a Host
identity assertion and is pinned on first use.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from pathlib import Path

from deskpet.execution.recovery_fence import (
    HumanMemoryRecoveryCoordinator,
    HumanMemoryRecoveryError,
    RecoveryManifestReceipt,
)
from deskpet.task_scope.protocol import identifier


class RecoveryLifecyclePort:
    def __init__(self, *, db_path: Path, artifact_dir: Path) -> None:
        self._db_path = db_path
        self._artifact_dir = artifact_dir
        self._coordinator: HumanMemoryRecoveryCoordinator | None = None
        self._subject: str | None = None

    async def manifest(self, *, subject: str) -> Mapping[str, object]:
        subject = self._assert_subject(subject)
        coordinator = await self._bound_coordinator()
        state = await coordinator.snapshot()
        if state.state == "FAILED_CLOSED":
            raise HumanMemoryRecoveryError(
                state.failure_code or "human_memory_recovery_failed_closed"
            )
        if state.state == "OPEN":
            state = await coordinator.begin_close()
        if state.state == "CLOSING":
            await coordinator.drain_or_park(action="park")
            state = await coordinator.quiesce()
        if state.state == "QUIESCED":
            await coordinator.checkpoint_wal()
            receipt = await coordinator.seal()
        elif state.state == "SEALED":
            receipt = await coordinator.current_manifest()
        else:
            raise HumanMemoryRecoveryError("human_memory_recovery_state_invalid")
        await coordinator.verify_manifest(receipt.manifest_id)
        return _manifest_mapping(subject, receipt)

    async def emergency_export(self, *, subject: str) -> Mapping[str, object]:
        subject = self._assert_subject(subject)
        coordinator = await self._bound_coordinator()
        manifest = await self.manifest(subject=subject)
        generation = int(manifest["generation"])
        export_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"simple-harness:human-memory-export:{subject}:{generation}",
            )
        )
        receipt = await coordinator.emergency_export(export_id=export_id)
        return {
            "subject": subject,
            "receipt_ref": receipt.receipt_id,
            "export_ref": receipt.export_id,
            "manifest_ref": receipt.manifest_id,
            "artifact_path": str(receipt.artifact_path),
            "artifact_sha256": receipt.artifact_sha256,
            "artifact_size": receipt.artifact_size,
            "overall_root": receipt.overall_root,
            "receipt_hash": receipt.receipt_hash,
        }

    async def _bound_coordinator(self) -> HumanMemoryRecoveryCoordinator:
        if self._coordinator is None:
            self._coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
                self._db_path, export_root=self._artifact_dir
            )
        return self._coordinator

    def _assert_subject(self, subject: str) -> str:
        subject = identifier(subject, "subject", 512)
        if self._subject is None:
            self._subject = subject
        elif self._subject != subject:
            raise HumanMemoryRecoveryError("human_memory_recovery_subject_mismatch")
        return subject


def build_recovery_lifecycle_port(
    *, db_path: str | Path, artifact_dir: str | Path
) -> RecoveryLifecyclePort:
    """Bind the trusted Host lifecycle authority without opening the DB yet."""

    return RecoveryLifecyclePort(
        db_path=Path(db_path), artifact_dir=Path(artifact_dir)
    )


def _manifest_mapping(
    subject: str, receipt: RecoveryManifestReceipt
) -> Mapping[str, object]:
    return {
        "subject": subject,
        "manifest_ref": receipt.manifest_id,
        "generation": receipt.generation,
        "cutoff": receipt.cutoff,
        "db_instance_ref": receipt.db_instance_id,
        "migration_chain_hash": receipt.migration_chain_hash,
        "table_set_hash": receipt.table_set_hash,
        "overall_root": receipt.overall_root,
        "manifest_hash": receipt.manifest_hash,
    }


__all__ = ["RecoveryLifecyclePort", "build_recovery_lifecycle_port"]
