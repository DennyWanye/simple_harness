# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Typed Host facade for the fresh human-memory composition.

Identity and authority are constructor-bound.  Public request DTOs deliberately
contain no subject, allowed-scope set, composition mode, database path, binding
revision, or worker authority fields.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.schema import (
    StartupCompositionMode,
    StartupEpochDecision,
)
from deskpet.task_scope.protocol import identifier, reject_private_payload
from deskpet.task_scope.store import CanonicalTaskScopeStore


class HumanMemoryHostServiceError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AuthenticatedHostSnapshot:
    subject: str
    principal_id: str
    authority_ref: str

    def __post_init__(self) -> None:
        identifier(self.subject, "subject", 512)
        identifier(self.principal_id, "principal_id", 512)
        identifier(self.authority_ref, "authority_ref", 512)


@dataclass(frozen=True, slots=True)
class CreateTaskScopeRequest:
    fixture_key: str
    title: str
    goal: str
    idempotency_key: str

    def __post_init__(self) -> None:
        identifier(self.fixture_key, "fixture_key", 512)
        identifier(self.title, "title", 4096)
        identifier(self.goal, "goal", 16_384)
        identifier(self.idempotency_key, "idempotency_key", 512)


@dataclass(frozen=True, slots=True)
class AppendDeterministicEventsRequest:
    scope_ref: str
    count: int
    canary: str
    idempotency_key: str

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        identifier(self.canary, "canary", 4096)
        identifier(self.idempotency_key, "idempotency_key", 512)
        if isinstance(self.count, bool) or not isinstance(self.count, int):
            raise TypeError("count must be an integer")
        if self.count < 1 or self.count > 100_000:
            raise ValueError("count must be between 1 and 100000")


@dataclass(frozen=True, slots=True)
class SaveCheckpointRequest:
    scope_ref: str
    checkpoint: Mapping[str, object]
    idempotency_key: str

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        identifier(self.idempotency_key, "idempotency_key", 512)
        reject_private_payload(dict(self.checkpoint), "checkpoint")


class DeterministicEventSeedPort(Protocol):
    """Verification-only bulk authority implemented by the canonical producer."""

    def append_deterministic_events(
        self,
        *,
        subject: str,
        task_scope_id: str,
        count: int,
        canary: str,
        idempotency_key: str,
    ) -> Awaitable[Mapping[str, object]]: ...


@dataclass(frozen=True, slots=True)
class _RawSetSpec:
    logical_name: str
    table: str


_RAW_SET_ALLOWLIST = (
    _RawSetSpec("program.bootstrap", "human_memory_program_bootstrap"),
    _RawSetSpec("program.marker", "human_memory_program_marker"),
    _RawSetSpec("program.migration_chain", "human_memory_migration_chain"),
    _RawSetSpec("program.primary", "human_memory_primary_conversations"),
    _RawSetSpec("program.init_receipts", "human_memory_init_receipts"),
    _RawSetSpec("program.evidence", "human_memory_evidence"),
    _RawSetSpec("program.evidence_receipts", "human_memory_sanitization_receipts"),
    _RawSetSpec("scope.identities", "task_scopes"),
    _RawSetSpec("scope.revisions", "task_scope_canonical_revisions"),
    _RawSetSpec("scope.heads", "task_scope_heads"),
    _RawSetSpec("scope.events", "task_scope_events"),
    _RawSetSpec("scope.steps", "task_scope_steps"),
    _RawSetSpec("scope.evidence_links", "task_scope_evidence_links"),
    _RawSetSpec("scope.mutation_attempts", "task_scope_mutation_attempts"),
    _RawSetSpec("scope.mutation_decisions", "task_scope_mutation_decisions"),
    _RawSetSpec("scope.checkpoints", "task_scope_checkpoints"),
    _RawSetSpec("scope.provisions", "task_scope_provisions"),
    _RawSetSpec("scope.provision_receipts", "task_scope_provision_receipts"),
    _RawSetSpec("binding.proposals", "task_workspace_binding_proposals"),
    _RawSetSpec("binding.run_modes", "task_workspace_run_mode_snapshots"),
    _RawSetSpec("binding.revisions", "task_workspace_binding_revisions"),
    _RawSetSpec("binding.roots", "task_workspace_binding_roots"),
    _RawSetSpec("binding.heads", "task_workspace_binding_heads"),
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _json_cell(value: object) -> object:
    if isinstance(value, bytes):
        return {"blob_sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError("unsupported SQLite value in raw integrity manifest")


class HumanMemoryHostService:
    def __init__(
        self,
        db_path: str | Path,
        *,
        auth: AuthenticatedHostSnapshot,
        startup: StartupEpochDecision,
        deterministic_event_seed: DeterministicEventSeedPort | None = None,
    ) -> None:
        if startup.composition_mode is not StartupCompositionMode.HUMAN:
            raise HumanMemoryHostServiceError(
                "human_memory_program_legacy_database_unsupported"
            )
        self._db_path = Path(db_path)
        self._auth = auth
        self._startup = startup
        self._program = HumanMemoryProgramStore(self._db_path)
        self._scopes = CanonicalTaskScopeStore(self._db_path)
        self._deterministic_event_seed = deterministic_event_seed

    @property
    def startup_decision(self) -> StartupEpochDecision:
        return self._startup

    async def open_primary(self) -> Mapping[str, object]:
        receipt = await self._program.initialize_subject(self._auth.subject)
        return {
            "primary_ref": receipt.primary_conversation_id,
            "receipt_ref": receipt.receipt_id,
            "receipt_hash": receipt.receipt_sha256,
        }

    async def create_task_scope(
        self, request: CreateTaskScopeRequest
    ) -> Mapping[str, object]:
        scope_ref = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "simple-harness:host-scope:"
                f"{self._auth.subject}:{request.idempotency_key}:{request.fixture_key}",
            )
        )
        receipt = await self._scopes.create_task_scope(
            task_scope_id=scope_ref,
            subject=self._auth.subject,
            title=request.title,
        )
        return {
            "scope_ref": receipt.task_scope_id,
            "revision": receipt.revision,
            "state_hash": receipt.state_hash,
            "goal": request.goal,
        }

    async def append_deterministic_events(
        self, request: AppendDeterministicEventsRequest
    ) -> Mapping[str, object]:
        await self._assert_owned_scope(request.scope_ref)
        if self._deterministic_event_seed is None:
            raise HumanMemoryHostServiceError(
                "human_memory_bulk_seed_authority_unavailable"
            )
        return await self._deterministic_event_seed.append_deterministic_events(
            subject=self._auth.subject,
            task_scope_id=request.scope_ref,
            count=request.count,
            canary=request.canary,
            idempotency_key=request.idempotency_key,
        )

    async def save_checkpoint(
        self, request: SaveCheckpointRequest
    ) -> Mapping[str, object]:
        await self._assert_owned_scope(request.scope_ref)
        checkpoint_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "simple-harness:host-checkpoint:"
                f"{self._auth.subject}:{request.scope_ref}:{request.idempotency_key}",
            )
        )
        receipt = await self._scopes.create_checkpoint(
            checkpoint_id=checkpoint_id,
            task_scope_id=request.scope_ref,
            metadata=dict(request.checkpoint),
        )
        return {
            "checkpoint_ref": receipt.checkpoint_id,
            "scope_ref": receipt.task_scope_id,
            "revision": receipt.revision,
            "checkpoint_hash": receipt.checkpoint_hash,
            "event_watermark": receipt.event_watermark,
        }

    async def raw_integrity_manifest(self) -> Mapping[str, object]:
        """Return a bounded read-only raw-set digest, not a v42 sealed receipt."""

        owned_scopes = await self._owned_scope_ids()
        raw_sets: dict[str, Mapping[str, object]] = {}
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            available = {
                str(row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for spec in _RAW_SET_ALLOWLIST:
                if spec.table not in available:
                    continue
                columns = [
                    str(row[1])
                    for row in db.execute(f'PRAGMA table_info("{spec.table}")')
                ]
                if "subject" in columns:
                    rows = db.execute(
                        f'SELECT * FROM "{spec.table}" WHERE subject=?',
                        (self._auth.subject,),
                    ).fetchall()
                elif "task_scope_id" in columns:
                    if not owned_scopes:
                        rows = []
                    else:
                        placeholders = ",".join("?" for _ in owned_scopes)
                        rows = db.execute(
                            f'SELECT * FROM "{spec.table}" '
                            f"WHERE task_scope_id IN ({placeholders})",
                            tuple(owned_scopes),
                        ).fetchall()
                else:
                    rows = db.execute(f'SELECT * FROM "{spec.table}"').fetchall()
                canonical_rows = sorted(
                    _canonical(
                        {
                            key: _json_cell(row[key])
                            for key in row.keys()
                        }
                    )
                    for row in rows
                )
                digest = hashlib.sha256()
                for row_bytes in canonical_rows:
                    digest.update(len(row_bytes).to_bytes(8, "big"))
                    digest.update(row_bytes)
                raw_sets[spec.logical_name] = {
                    "row_count": len(canonical_rows),
                    "content_sha256": digest.hexdigest(),
                }
        return {
            "schema_version": 1,
            "format_epoch": self._startup.composition_mode.value,
            "raw_sets": raw_sets,
        }

    async def _owned_scope_ids(self) -> tuple[str, ...]:
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            rows = db.execute(
                "SELECT task_scope_id FROM task_scopes WHERE subject=? "
                "ORDER BY task_scope_id",
                (self._auth.subject,),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    async def _assert_owned_scope(self, scope_ref: str) -> None:
        identifier(scope_ref, "scope_ref", 512)
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT subject FROM task_scopes WHERE task_scope_id=?",
                (scope_ref,),
            ).fetchone()
        if row is None or str(row[0]) != self._auth.subject:
            raise HumanMemoryHostServiceError("human_memory_permission_denied")


@dataclass(frozen=True, slots=True)
class HumanMemoryHostServiceFactory:
    db_path: Path
    startup: StartupEpochDecision

    def bind(
        self,
        auth: AuthenticatedHostSnapshot,
        *,
        deterministic_event_seed: DeterministicEventSeedPort | None = None,
    ) -> HumanMemoryHostService:
        return HumanMemoryHostService(
            self.db_path,
            auth=auth,
            startup=self.startup,
            deterministic_event_seed=deterministic_event_seed,
        )


__all__ = (
    "AppendDeterministicEventsRequest",
    "AuthenticatedHostSnapshot",
    "CreateTaskScopeRequest",
    "DeterministicEventSeedPort",
    "HumanMemoryHostService",
    "HumanMemoryHostServiceError",
    "HumanMemoryHostServiceFactory",
    "SaveCheckpointRequest",
)
