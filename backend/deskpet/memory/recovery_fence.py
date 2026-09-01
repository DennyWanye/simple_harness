# SPDX-License-Identifier: BUSL-1.1

"""Host-composition seam for the v42 recovery lifecycle.

The public builder binds the database and artifact directory once.  Request
payloads cannot select either authority boundary; ``subject`` is only a Host
identity assertion and is pinned on first use.
"""

from __future__ import annotations

import asyncio
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
        self._lifecycle_loop: asyncio.AbstractEventLoop | None = None
        self._lifecycle_lock: asyncio.Lock | None = None

    async def manifest(self, *, subject: str) -> Mapping[str, object]:
        subject = self._assert_subject(subject)
        async with self._lifecycle_guard():
            coordinator = await self._bound_coordinator()
            try:
                receipt = await self._ensure_sealed(coordinator)
                await coordinator.verify_manifest(receipt.manifest_id)
                result = _manifest_mapping(subject, receipt)
                await coordinator.reopen()
                return result
            except Exception as exc:
                await self._fail_closed_on_error(coordinator, exc)
                raise

    async def emergency_export(self, *, subject: str) -> Mapping[str, object]:
        subject = self._assert_subject(subject)
        async with self._lifecycle_guard():
            coordinator = await self._bound_coordinator()
            try:
                manifest = await self._ensure_sealed(coordinator)
                export_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        "simple-harness:human-memory-export:"
                        f"{subject}:{manifest.generation}",
                    )
                )
                receipt = await coordinator.emergency_export(export_id=export_id)
                result = {
                    "subject": subject,
                    "receipt_ref": receipt.receipt_id,
                    "export_ref": receipt.export_id,
                    "manifest_ref": receipt.manifest_id,
                    "generation": manifest.generation,
                    "artifact_path": str(receipt.artifact_path),
                    "artifact_sha256": receipt.artifact_sha256,
                    "artifact_size": receipt.artifact_size,
                    "overall_root": receipt.overall_root,
                    "receipt_hash": receipt.receipt_hash,
                }
                await coordinator.reopen()
                return result
            except Exception as exc:
                await self._fail_closed_on_error(coordinator, exc)
                raise

    async def _ensure_sealed(
        self, coordinator: HumanMemoryRecoveryCoordinator
    ) -> RecoveryManifestReceipt:
        """Resume the durable lifecycle without ever guessing ingress is open."""

        for _ in range(8):
            state = await coordinator.snapshot()
            if state.state == "FAILED_CLOSED":
                raise HumanMemoryRecoveryError(
                    state.failure_code or "human_memory_recovery_failed_closed"
                )
            if state.state == "OPEN":
                await coordinator.begin_close()
                continue
            if state.state == "CLOSING":
                await coordinator.drain_or_park(action="park")
                await coordinator.quiesce()
                continue
            if state.state == "QUIESCED":
                await coordinator.checkpoint_wal()
                return await coordinator.seal()
            if state.state == "SEALED":
                return await coordinator.current_manifest()
            await coordinator.fail_closed("human_memory_recovery_state_invalid")
            raise HumanMemoryRecoveryError("human_memory_recovery_state_invalid")
        await coordinator.fail_closed("human_memory_recovery_resume_exhausted")
        raise HumanMemoryRecoveryError("human_memory_recovery_resume_exhausted")

    @staticmethod
    async def _fail_closed_on_error(
        coordinator: HumanMemoryRecoveryCoordinator, exc: Exception
    ) -> None:
        state = await coordinator.snapshot()
        if state.state == "FAILED_CLOSED":
            return
        code = (
            exc.code
            if isinstance(exc, HumanMemoryRecoveryError)
            else "human_memory_recovery_public_step_failed"
        )
        await coordinator.fail_closed(code)

    async def _bound_coordinator(self) -> HumanMemoryRecoveryCoordinator:
        if self._coordinator is None:
            self._coordinator = await HumanMemoryRecoveryCoordinator.bind_for_host(
                self._db_path, export_root=self._artifact_dir
            )
        return self._coordinator

    def _lifecycle_guard(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lifecycle_lock is None or self._lifecycle_loop is not loop:
            if self._lifecycle_lock is not None and self._lifecycle_lock.locked():
                raise HumanMemoryRecoveryError(
                    "human_memory_recovery_cross_loop_concurrency_rejected"
                )
            self._lifecycle_loop = loop
            self._lifecycle_lock = asyncio.Lock()
        return self._lifecycle_lock

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
        "receipt_ref": receipt.manifest_id,
        "generation": receipt.generation,
        "cutoff": receipt.cutoff,
        "db_instance_ref": receipt.db_instance_id,
        "migration_chain_hash": receipt.migration_chain_hash,
        "table_set_hash": receipt.table_set_hash,
        "overall_root": receipt.overall_root,
        "manifest_hash": receipt.manifest_hash,
    }


__all__ = ["RecoveryLifecyclePort", "build_recovery_lifecycle_port"]
