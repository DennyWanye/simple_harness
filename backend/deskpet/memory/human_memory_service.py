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

from simple_harness import (
    DeliveryRecipient,
    DisclosureContext,
    DisclosureGeneration,
    DisclosurePurpose,
    DisclosureReasonCode,
    DisclosureSource,
    DisclosureTrust,
    EvidenceReasonCode,
    EvidenceSourceKind,
    IntendedAudience,
    SanitizedEvidenceEnvelope,
    SanitizedEvidenceReceipt,
)

from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.schema import (
    StartupCompositionMode,
    StartupEpochDecision,
)
from deskpet.task_scope.projections import TaskScopeProjectionStore
from deskpet.task_scope.protocol import canonical_hash, canonical_json, identifier, reject_private_payload
from deskpet.task_scope.search import TaskScopeSearchStore
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


@dataclass(frozen=True, slots=True)
class SearchTaskScopesRequest:
    query: str
    max_candidates: int = 8
    cursor: str | None = None

    def __post_init__(self) -> None:
        identifier(self.query, "query", 65_536)
        if isinstance(self.max_candidates, bool) or not isinstance(
            self.max_candidates, int
        ):
            raise TypeError("max_candidates must be an integer")
        if not 1 <= self.max_candidates <= 100:
            raise ValueError("max_candidates must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class OpenTaskScopeRequest:
    scope_ref: str
    live_probe: Mapping[str, object] | None = None
    expected_source_hash: str | None = None

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        if self.live_probe is not None:
            reject_private_payload(dict(self.live_probe), "live_probe")


@dataclass(frozen=True, slots=True)
class QueueTurnRequest:
    scope_ref: str
    delivery_key: str
    text: str

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        identifier(self.delivery_key, "delivery_key", 512)
        identifier(self.text, "text", 16_384)


@dataclass(frozen=True, slots=True)
class ReadTaskScopeViewRequest:
    scope_ref: str
    kind: str

    def __post_init__(self) -> None:
        identifier(self.scope_ref, "scope_ref", 512)
        identifier(self.kind, "kind", 64)


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
        self._projections = TaskScopeProjectionStore(self._db_path)
        self._search = TaskScopeSearchStore(self._db_path)
        self._foreground = ForegroundQueueStore(self._db_path)
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

    async def enqueue_turn(self, request: QueueTurnRequest) -> Mapping[str, object]:
        await self._assert_owned_scope(request.scope_ref)
        primary = await self._program.initialize_subject(self._auth.subject)
        payload = {
            "schema_version": 1,
            "delivery_key": request.delivery_key,
            "text": request.text,
        }
        payload_hash = canonical_hash(payload)
        run_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"simple-harness:foreground-evidence-run:{self._auth.subject}",
            )
        )
        evidence_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "simple-harness:foreground-evidence:"
                f"{self._auth.subject}:{request.delivery_key}",
            )
        )
        disclosure = DisclosureContext(
            run_id=run_id,
            subject=self._auth.subject,
            recipient=DeliveryRecipient.USER_SELF,
            recipient_id=self._auth.subject,
            intended_audience=IntendedAudience.USER_SELF,
            purpose=DisclosurePurpose.TASK_EXECUTION,
            source=DisclosureSource.AUTHENTICATED_HOST,
            trust=DisclosureTrust.TRUSTED_AUTHORITY,
            generation=DisclosureGeneration.CURRENT,
            authority_ref=self._auth.authority_ref,
            reason_codes=(DisclosureReasonCode.MINIMUM_NECESSARY,),
        )
        envelope = SanitizedEvidenceEnvelope(
            evidence_id=evidence_id,
            run_id=run_id,
            subject=self._auth.subject,
            source_kind=EvidenceSourceKind.USER_MESSAGE,
            source_ref=f"foreground-turn:{request.delivery_key}",
            source_hash=payload_hash,
            sanitized_payload=payload,
            sanitized_hash=payload_hash,
            filter_policy_version="host-public-turn/v1",
            removed_spans=(),
            disclosure_context=disclosure,
            evidence_refs=(),
        )
        receipt = SanitizedEvidenceReceipt(
            receipt_id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"simple-harness:foreground-evidence-receipt:{evidence_id}",
                )
            ),
            run_id=run_id,
            subject=self._auth.subject,
            evidence_id=evidence_id,
            envelope_hash=envelope.envelope_hash,
            source_hash=payload_hash,
            sanitized_hash=payload_hash,
            filter_policy_version="host-public-turn/v1",
            accepted=True,
            reason_codes=(EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
            disclosure_context=disclosure,
            evidence_refs=(),
            admitted_at=0.0,
        )
        committed = await self._program.append_evidence(envelope, receipt)
        queued = await self._foreground.enqueue_turn(
            subject=self._auth.subject,
            primary_conversation_id=primary.primary_conversation_id,
            evidence_id=committed.evidence_id,
            evidence_hash=committed.envelope_sha256,
            idempotency_key=request.delivery_key,
            turn_payload=payload,
            task_scope_id=request.scope_ref,
        )
        return {
            "turn_ref": queued.turn_id,
            "scope_ref": queued.task_scope_id,
            "enqueue_sequence": queued.enqueue_sequence,
            "content_sha256": queued.turn_hash,
            "delivery_key": request.delivery_key,
        }

    async def search_task_scopes(
        self, request: SearchTaskScopesRequest
    ) -> Mapping[str, object]:
        allowed = await self._owned_scope_ids()
        result = await self._search.search(
            subject=self._auth.subject,
            allowed_scope_ids=allowed,
            query=request.query,
            limit=request.max_candidates,
            cursor=request.cursor,
        )
        return {
            "candidates": [
                {
                    "scope_ref": item.task_scope_id,
                    "source_ref": item.source_id,
                    "source_hash": item.source_hash,
                    "title": item.title,
                    "goal": item.goal,
                    "project": item.project,
                    "status": item.status,
                    "snippet": item.snippet,
                    "rank": item.rank,
                }
                for item in result.candidates
            ],
            "next_cursor": result.next_cursor,
            "receipt_hash": result.receipt_hash,
        }

    async def open_task_scope(
        self, request: OpenTaskScopeRequest
    ) -> Mapping[str, object]:
        allowed = await self._owned_scope_ids()
        result = await self._search.open_exact(
            subject=self._auth.subject,
            allowed_scope_ids=allowed,
            task_scope_id=request.scope_ref,
            expected_source_hash=request.expected_source_hash,
            live_probe=request.live_probe,
        )
        drift = result.drift_report
        return {
            "scope_ref": result.task_scope_id,
            "source_ref": result.source_id,
            "source_hash": result.source_hash,
            "resume_package": result.resume_package,
            "resume_sha256": result.resume_package_hash,
            "receipt_hash": result.receipt_hash,
            "drift_report": None
            if drift is None
            else {
                "drifted": drift.drifted,
                "changed_fields": list(drift.changed_fields),
                "checkpoint_ref": drift.checkpoint_id,
                "checkpoint_hash": drift.checkpoint_hash,
                "report_hash": drift.report_hash,
            },
        }

    async def read_view(
        self, request: ReadTaskScopeViewRequest
    ) -> Mapping[str, object]:
        await self._assert_owned_scope(request.scope_ref)
        view = await self._projections.read_view(
            request.kind, task_scope_id=request.scope_ref
        )
        pages: list[dict[str, object]] = []
        if view.view_kind == "EVIDENCE":
            groups = await self._projections.list_evidence_groups(
                task_scope_id=request.scope_ref
            )
            for group in groups:
                events = await self._projections.read_evidence_group(
                    str(group["block_id"])
                )
                pending: list[dict[str, object]] = []
                for raw_event in events:
                    event = dict(raw_event)
                    payload = event.get("payload", {})
                    if not isinstance(payload, Mapping):
                        raise HumanMemoryHostServiceError(
                            "task_scope_projection_event_payload_invalid"
                        )
                    normalized = {
                        "event_index": payload.get("event_index"),
                        "event_sequence": event.get("event_sequence"),
                        "event_id": event.get("event_id"),
                        "payload_hash": event.get("payload_hash"),
                    }
                    candidate = canonical_json(
                        {"schema_version": 1, "events": [*pending, normalized]}
                    )
                    if pending and len(candidate.encode("utf-8")) > 32 * 1024:
                        content = canonical_json(
                            {"schema_version": 1, "events": pending}
                        )
                        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                        pages.append(
                            {
                                "page_id": f"sha256:{content_hash}",
                                "content": content,
                                "content_sha256": content_hash,
                                "events": pending,
                            }
                        )
                        pending = [normalized]
                    else:
                        pending.append(normalized)
                if pending:
                    content = canonical_json(
                        {"schema_version": 1, "events": pending}
                    )
                    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    pages.append(
                        {
                            "page_id": f"sha256:{content_hash}",
                            "content": content,
                            "content_sha256": content_hash,
                            "events": pending,
                        }
                    )
        return {
            "scope_ref": request.scope_ref,
            "source_ref": view.source_id,
            "kind": view.view_kind,
            "content": view.content,
            "content_sha256": view.content_sha256,
            "root_block_id": view.root_block_id,
            "block_count": view.block_count,
            "receipt_hash": view.receipt_hash,
            "pages": pages,
        }

    async def drop_rebuildable(self, scope_ref: str) -> Mapping[str, object]:
        await self._assert_owned_scope(scope_ref)
        with sqlite3.connect(self._db_path) as db:
            db.execute(
                "DELETE FROM task_scope_search_fts WHERE task_scope_id=?",
                (scope_ref,),
            )
            db.execute(
                "DELETE FROM task_scope_projection_cache WHERE task_scope_id=?",
                (scope_ref,),
            )
            db.commit()
        return {"scope_ref": scope_ref, "dropped": ["fts", "legacy_projection_cache"]}

    async def rebuild_derived(self, scope_ref: str) -> Mapping[str, object]:
        await self._assert_owned_scope(scope_ref)
        views = await self._projections.materialize(task_scope_id=scope_ref)
        document_hash = await self._search.rebuild_scope(scope_ref)
        return {
            "scope_ref": scope_ref,
            "view_hashes": {kind: view.content_sha256 for kind, view in views.items()},
            "search_document_hash": document_hash,
        }

    async def authority_snapshot(self) -> Mapping[str, object]:
        primary = await self._program.initialize_subject(self._auth.subject)
        owned = await self._owned_scope_ids()
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            if owned:
                placeholders = ",".join("?" for _ in owned)
                rows = db.execute(
                    "SELECT task_scope_id,current_revision,current_receipt_id,"
                    "current_receipt_hash FROM task_workspace_binding_heads "
                    f"WHERE task_scope_id IN ({placeholders}) ORDER BY task_scope_id",
                    owned,
                ).fetchall()
            else:
                rows = []
        current = await self._foreground.current_snapshot(self._auth.subject)
        return {
            "primary_ref": primary.primary_conversation_id,
            "active_cursor": None,
            "binding_heads": [dict(row) for row in rows],
            "tool_authority_refs": [],
            "foreground_run_ref": None if current is None else current.host_run_id,
        }

    async def queue_snapshot(self) -> Mapping[str, object]:
        with sqlite3.connect(f"file:{self._db_path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT t.turn_id,t.enqueue_sequence,t.turn_json,h.current_state "
                "FROM foreground_turns t JOIN foreground_turn_heads h "
                "ON h.turn_id=t.turn_id WHERE t.subject=? "
                "ORDER BY t.enqueue_sequence,t.turn_id",
                (self._auth.subject,),
            ).fetchall()
        turns = []
        for row in rows:
            stored = json.loads(str(row["turn_json"]))
            payload = stored.get("payload", {})
            turns.append(
                {
                    "turn_ref": str(row["turn_id"]),
                    "enqueue_sequence": int(row["enqueue_sequence"]),
                    "state": str(row["current_state"]),
                    "delivery_key": payload.get("delivery_key"),
                }
            )
        return {"turns": turns}

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
    "OpenTaskScopeRequest",
    "QueueTurnRequest",
    "ReadTaskScopeViewRequest",
    "SaveCheckpointRequest",
    "SearchTaskScopesRequest",
)
