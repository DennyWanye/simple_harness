# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Atomic same-run capability refresh preparation and commit orchestration.

The service deliberately does not know the production continuation schema.
Instead it lends the caller the same SQLite transaction that records the new
snapshot lease and consumes the refresh nonce. The Execution UoW adapter must
CAS its continuation in that transaction and return verifiable evidence.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol

import aiosqlite

from deskpet.tools.capabilities import (
    PreparedToolSet,
    ToolCapabilityResolver,
    ToolEligibilityContext,
    ToolExposureIntent,
)
from deskpet.tools.prepared_snapshot import (
    dump_context_os_snapshot,
    dump_prepared_tool_set,
)

from .contracts import (
    CapabilityCatalogSnapshot,
    CapabilityScope,
    JsonValue,
    fingerprint_json,
)
from .hub import CapabilityHub
from .refresh_contracts import (
    CapabilityOperationReceipt,
    CapabilityRefreshCommit,
    CapabilityRefreshIntent,
)
from .store import CapabilityStore, CapabilityStoreConflict


class CapabilityRefreshError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def exposure_intent_payload(intent: ToolExposureIntent) -> dict[str, JsonValue]:
    return {
        "direct_selectors": list(intent.direct_selectors),
        "discoverable_selectors": list(intent.discoverable_selectors),
        "deny_selectors": list(intent.deny_selectors),
        "required_direct_names": list(intent.required_direct_names),
    }


def exposure_intent_ref(intent: ToolExposureIntent) -> str:
    return fingerprint_json(exposure_intent_payload(intent))


def context_os_snapshot_ref(
    prepared: PreparedToolSet, eligibility: ToolEligibilityContext
) -> str:
    return fingerprint_json(dump_context_os_snapshot(prepared, eligibility))


class CapabilityRefreshSnapshotRepository(Protocol):
    async def load_context_os(
        self, snapshot_ref: str
    ) -> tuple[PreparedToolSet, ToolEligibilityContext]:
        ...

    async def put_context_os(
        self,
        snapshot_ref: str,
        payload: Mapping[str, JsonValue],
    ) -> str:
        ...

    async def load_exposure_intent(
        self, intent_ref: str
    ) -> ToolExposureIntent:
        ...

    async def put_exposure_intent(
        self,
        intent_ref: str,
        intent: ToolExposureIntent,
    ) -> str:
        ...


class SqliteCapabilityRefreshSnapshotRepository:
    """Content-addressed refresh inputs stored in the execution database."""

    def __init__(self, store: CapabilityStore) -> None:
        self._store = store

    async def _put(
        self,
        *,
        snapshot_ref: str,
        snapshot_kind: str,
        payload: Mapping[str, JsonValue],
    ) -> str:
        if fingerprint_json(dict(payload)) != snapshot_ref:
            raise CapabilityRefreshError(
                "refresh_snapshot_ref_mismatch",
                f"{snapshot_kind} payload does not match its reference",
            )
        encoded = json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        async with self._store.write_transaction() as db:
            existing = await (
                await db.execute(
                    """SELECT payload_json FROM capability_refresh_snapshots
                    WHERE snapshot_ref=? AND snapshot_kind=?""",
                    (snapshot_ref, snapshot_kind),
                )
            ).fetchone()
            if existing is not None:
                if str(existing["payload_json"]) != encoded:
                    raise CapabilityRefreshError(
                        "refresh_snapshot_collision",
                        "content-addressed refresh snapshot has different bytes",
                    )
                return snapshot_ref
            await db.execute(
                """INSERT INTO capability_refresh_snapshots(
                    snapshot_ref,snapshot_kind,payload_json,created_at
                ) VALUES(?,?,?,CAST(strftime('%s','now') AS REAL))""",
                (snapshot_ref, snapshot_kind, encoded),
            )
        return snapshot_ref

    async def _load(
        self, *, snapshot_ref: str, snapshot_kind: str
    ) -> Mapping[str, JsonValue]:
        async with self._store.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT payload_json FROM capability_refresh_snapshots
                    WHERE snapshot_ref=? AND snapshot_kind=?""",
                    (snapshot_ref, snapshot_kind),
                )
            ).fetchone()
        if row is None:
            raise CapabilityRefreshError(
                "refresh_snapshot_not_found",
                f"{snapshot_kind} snapshot does not exist: {snapshot_ref}",
            )
        payload = json.loads(str(row["payload_json"]))
        if not isinstance(payload, dict):
            raise CapabilityRefreshError(
                "refresh_snapshot_corrupt",
                f"{snapshot_kind} snapshot is not an object",
            )
        if fingerprint_json(payload) != snapshot_ref:
            raise CapabilityRefreshError(
                "refresh_snapshot_corrupt",
                f"{snapshot_kind} snapshot failed content verification",
            )
        return payload

    async def load_context_os(
        self, snapshot_ref: str
    ) -> tuple[PreparedToolSet, ToolEligibilityContext]:
        from deskpet.tools.prepared_snapshot import load_context_os_snapshot

        payload = await self._load(
            snapshot_ref=snapshot_ref,
            snapshot_kind="context_os",
        )
        return load_context_os_snapshot(payload)

    async def put_context_os(
        self,
        snapshot_ref: str,
        payload: Mapping[str, JsonValue],
    ) -> str:
        return await self._put(
            snapshot_ref=snapshot_ref,
            snapshot_kind="context_os",
            payload=payload,
        )

    async def load_exposure_intent(
        self, intent_ref: str
    ) -> ToolExposureIntent:
        payload = await self._load(
            snapshot_ref=intent_ref,
            snapshot_kind="exposure_intent",
        )
        return ToolExposureIntent(
            direct_selectors=tuple(
                str(item) for item in payload.get("direct_selectors", ())
            ),
            discoverable_selectors=tuple(
                str(item) for item in payload.get("discoverable_selectors", ())
            ),
            deny_selectors=tuple(
                str(item) for item in payload.get("deny_selectors", ())
            ),
            required_direct_names=tuple(
                str(item) for item in payload.get("required_direct_names", ())
            ),
        )

    async def put_exposure_intent(
        self,
        intent_ref: str,
        intent: ToolExposureIntent,
    ) -> str:
        return await self._put(
            snapshot_ref=intent_ref,
            snapshot_kind="exposure_intent",
            payload=exposure_intent_payload(intent),
        )

class CapabilityToolSetRebuilder(Protocol):
    def rebuild(
        self,
        *,
        old_tool_set: PreparedToolSet,
        exposure_intent: ToolExposureIntent,
        eligibility: ToolEligibilityContext,
        receipt: CapabilityOperationReceipt,
        catalog_snapshot: CapabilityCatalogSnapshot,
    ) -> PreparedToolSet:
        ...


class RegistryExposureToolSetRebuilder:
    """Re-evaluate the original exposure intent against one live registry."""

    def __init__(self, resolver: ToolCapabilityResolver) -> None:
        self._resolver = resolver

    def rebuild(
        self,
        *,
        old_tool_set: PreparedToolSet,
        exposure_intent: ToolExposureIntent,
        eligibility: ToolEligibilityContext,
        receipt: CapabilityOperationReceipt,
        catalog_snapshot: CapabilityCatalogSnapshot,
    ) -> PreparedToolSet:
        del receipt
        activated_names = tuple(
            capability.ref.name for capability in old_tool_set.activated
        )
        draft = self._resolver.resolve_draft(
            exposure_intent,
            eligibility=eligibility,
            conditional_direct_names=activated_names,
        )
        if draft.registry_revision != catalog_snapshot.stamp.registry_revision:
            raise CapabilityRefreshError(
                "refresh_registry_drift",
                "tool exposure and capability snapshot used different registry revisions",
            )
        conditional_names = {
            capability.ref.name for capability in draft.conditional_direct
        }
        fresh = draft.finalize(
            required_conditional_names=tuple(
                name for name in activated_names if name in conditional_names
            ),
            scope_id=old_tool_set.scope_id,
        )
        return PreparedToolSet.create(
            scope_id=old_tool_set.scope_id,
            revision=old_tool_set.revision + 1,
            registry_revision=fresh.registry_revision,
            direct=fresh.direct,
            deferred=fresh.deferred,
            activated=fresh.activated,
            denied_names=fresh.denied_names,
            policy_fingerprint=fresh.policy_fingerprint,
            decisions=fresh.decisions,
        )


@dataclass(frozen=True, slots=True)
class CapabilityContinuationCommitEvidence:
    root_run_id: str
    run_id: str
    previous_version: int
    new_version: int
    refresh_pending_cleared: bool
    new_catalog_snapshot_ref: str
    new_tool_set_snapshot_ref: str
    new_stamp_fingerprint: str
    context_os_fingerprint: str
    driver_runtime_cleared: bool


@dataclass(frozen=True, slots=True)
class CapabilityRefreshStageEvidence:
    root_run_id: str
    run_id: str
    source_kind: str
    source_command_id: str
    source_effect_id: str
    previous_version: int
    new_version: int
    refresh_pending_intent_id: str
    outer_effect_state: str
    provider_outcome_staged: bool
    child_terminal_acked: bool
    provider_backfill_blocked: bool


@dataclass(frozen=True, slots=True)
class PreparedCapabilityRefresh:
    intent: CapabilityRefreshIntent
    receipt: CapabilityOperationReceipt
    commit: CapabilityRefreshCommit
    catalog_snapshot: CapabilityCatalogSnapshot
    prepared_tool_set: PreparedToolSet
    eligibility: ToolEligibilityContext
    restart_required: bool = True


ContinuationCommitter = Callable[
    [aiosqlite.Connection, PreparedCapabilityRefresh],
    CapabilityContinuationCommitEvidence
    | Awaitable[CapabilityContinuationCommitEvidence],
]
RefreshSourceStager = Callable[
    [aiosqlite.Connection, CapabilityRefreshIntent],
    CapabilityRefreshStageEvidence | Awaitable[CapabilityRefreshStageEvidence],
]


class CapabilityRefreshStagingService:
    """Stage source settlement/ack and the pending marker in one UoW."""

    def __init__(self, store: CapabilityStore) -> None:
        self.store = store

    @staticmethod
    def _validate(
        intent: CapabilityRefreshIntent,
        evidence: CapabilityRefreshStageEvidence,
    ) -> None:
        if intent.expected_continuation_version <= 0:
            raise CapabilityStoreConflict(
                "refresh_continuation_version_invalid",
                "staged refresh continuation version must be positive",
            )
        tool_source = intent.source_kind == "tool_effect"
        expected = (
            intent.root_run_id,
            intent.run_id,
            intent.source_kind,
            intent.source_command_id,
            intent.source_effect_id,
            intent.expected_continuation_version - 1,
            intent.expected_continuation_version,
            intent.intent_id,
            "settled" if tool_source else "deferred_pending",
            tool_source,
            not tool_source,
            True,
        )
        actual = (
            evidence.root_run_id,
            evidence.run_id,
            evidence.source_kind,
            evidence.source_command_id,
            evidence.source_effect_id,
            evidence.previous_version,
            evidence.new_version,
            evidence.refresh_pending_intent_id,
            evidence.outer_effect_state,
            evidence.provider_outcome_staged,
            evidence.child_terminal_acked,
            evidence.provider_backfill_blocked,
        )
        if actual != expected:
            raise CapabilityStoreConflict(
                "refresh_stage_evidence_mismatch",
                "execution UoW did not stage the exact refresh source transition",
            )

    async def _stage(
        self,
        intent: CapabilityRefreshIntent,
        stage_source: RefreshSourceStager,
    ) -> CapabilityRefreshIntent:
        async with self.store.write_transaction() as db:
            evidence = stage_source(db, intent)
            if inspect.isawaitable(evidence):
                evidence = await evidence
            if not isinstance(evidence, CapabilityRefreshStageEvidence):
                raise CapabilityStoreConflict(
                    "refresh_stage_evidence_missing",
                    "execution UoW must return refresh staging evidence",
                )
            self._validate(intent, evidence)
            return await self.store.bind(db).stage_refresh_intent(intent)

    async def stage_in_transaction(
        self,
        db: aiosqlite.Connection,
        intent: CapabilityRefreshIntent,
        evidence: CapabilityRefreshStageEvidence,
    ) -> CapabilityRefreshIntent:
        """Stage an intent in the caller's effect/child settlement transaction."""

        self._validate(intent, evidence)
        return await self.store.bind(db).stage_refresh_intent(intent)

    async def settle_control_effect_and_stage_refresh(
        self,
        intent: CapabilityRefreshIntent,
        settle_control_effect: RefreshSourceStager,
    ) -> CapabilityRefreshIntent:
        if intent.source_kind != "tool_effect":
            raise CapabilityRefreshError(
                "refresh_source_mismatch",
                "tool-effect staging requires source_kind=tool_effect",
            )
        return await self._stage(intent, settle_control_effect)

    async def ack_child_terminal_and_stage_refresh(
        self,
        intent: CapabilityRefreshIntent,
        ack_child_terminal: RefreshSourceStager,
    ) -> CapabilityRefreshIntent:
        if intent.source_kind != "child_terminal":
            raise CapabilityRefreshError(
                "refresh_source_mismatch",
                "child staging requires source_kind=child_terminal",
            )
        return await self._stage(intent, ack_child_terminal)


class CapabilityRefreshService:
    """Commit a refreshed catalog and continuation through one SQLite CAS."""

    def __init__(
        self,
        *,
        store: CapabilityStore,
        hub: CapabilityHub,
        snapshots: CapabilityRefreshSnapshotRepository,
        rebuilder: CapabilityToolSetRebuilder,
    ) -> None:
        self.store = store
        self.hub = hub
        self.snapshots = snapshots
        self.rebuilder = rebuilder

    @staticmethod
    def _validate_continuation_evidence(
        prepared: PreparedCapabilityRefresh,
        evidence: CapabilityContinuationCommitEvidence,
    ) -> None:
        commit = prepared.commit
        expected = (
            commit.root_run_id,
            commit.run_id,
            commit.expected_continuation_version,
            commit.expected_continuation_version + 1,
            True,
            commit.new_catalog_snapshot_ref,
            commit.new_tool_set_snapshot_ref,
            commit.new_stamp.fingerprint,
            fingerprint_json(commit.to_dict()["new_context_os"]),
            True,
        )
        actual = (
            evidence.root_run_id,
            evidence.run_id,
            evidence.previous_version,
            evidence.new_version,
            evidence.refresh_pending_cleared,
            evidence.new_catalog_snapshot_ref,
            evidence.new_tool_set_snapshot_ref,
            evidence.new_stamp_fingerprint,
            evidence.context_os_fingerprint,
            evidence.driver_runtime_cleared,
        )
        if actual != expected:
            raise CapabilityStoreConflict(
                "refresh_continuation_evidence_mismatch",
                "execution UoW did not commit the exact refreshed continuation",
            )

    async def refresh(
        self,
        intent_id: str,
        *,
        scope: CapabilityScope,
        commit_continuation: ContinuationCommitter,
        release_old_snapshot: bool = True,
    ) -> PreparedCapabilityRefresh:
        intent = await self.store.get_refresh_intent(intent_id)
        if intent is None:
            raise CapabilityRefreshError(
                "refresh_intent_not_found", intent_id
            )
        if intent.status != "pending":
            raise CapabilityRefreshError(
                "refresh_intent_not_pending",
                f"refresh intent is already {intent.status}",
            )
        receipt = await self.store.get_operation_receipt(intent.operation_id)
        if receipt is None:
            raise CapabilityRefreshError(
                "operation_receipt_not_found", intent.operation_id
            )
        old_tool_set, eligibility = await self.snapshots.load_context_os(
            intent.old_tool_set_snapshot_ref
        )
        if (
            context_os_snapshot_ref(old_tool_set, eligibility)
            != intent.old_tool_set_snapshot_ref
        ):
            raise CapabilityRefreshError(
                "old_tool_set_snapshot_mismatch",
                "old Context OS snapshot does not match its durable reference",
            )
        exposure = await self.snapshots.load_exposure_intent(
            intent.exposure_intent_ref
        )
        if exposure_intent_ref(exposure) != intent.exposure_intent_ref:
            raise CapabilityRefreshError(
                "exposure_intent_mismatch",
                "tool exposure intent does not match its durable reference",
            )

        async with self.hub.publish_lock:
            catalog, entries = await self.hub.snapshot_for_atomic_lease(scope)
            if catalog.stamp.fingerprint == intent.old_stamp.fingerprint:
                raise CapabilityRefreshError(
                    "refresh_catalog_unchanged",
                    "published operation did not advance the visible catalog",
                )
            if catalog.snapshot_ref == intent.old_catalog_snapshot_ref:
                raise CapabilityRefreshError(
                    "refresh_catalog_unchanged",
                    "published operation did not create a new catalog snapshot",
                )
            candidate = self.rebuilder.rebuild(
                old_tool_set=old_tool_set,
                exposure_intent=exposure,
                eligibility=eligibility,
                receipt=receipt,
                catalog_snapshot=catalog,
            )
            if (
                candidate.scope_id != old_tool_set.scope_id
                or candidate.revision != old_tool_set.revision + 1
            ):
                raise CapabilityRefreshError(
                    "refresh_tool_set_revision_invalid",
                    "refreshed tool set must preserve scope and advance once",
                )
            context_os = dump_context_os_snapshot(candidate, eligibility)
            new_tool_set_ref = fingerprint_json(context_os)
            persisted_ref = await self.snapshots.put_context_os(
                new_tool_set_ref, context_os
            )
            if persisted_ref != new_tool_set_ref:
                raise CapabilityRefreshError(
                    "refresh_snapshot_repository_mismatch",
                    "snapshot repository returned a non-content-addressed reference",
                )
            commit = CapabilityRefreshCommit(
                intent_id=intent.intent_id,
                root_run_id=intent.root_run_id,
                run_id=intent.run_id,
                operation_id=intent.operation_id,
                refresh_nonce=intent.refresh_nonce,
                expected_continuation_version=(
                    intent.expected_continuation_version
                ),
                old_stamp=intent.old_stamp,
                new_stamp=catalog.stamp,
                old_catalog_snapshot_ref=intent.old_catalog_snapshot_ref,
                new_catalog_snapshot_ref=catalog.snapshot_ref,
                old_tool_set_snapshot_ref=intent.old_tool_set_snapshot_ref,
                new_tool_set_snapshot_ref=new_tool_set_ref,
                new_context_os=context_os,
                affected_tool_spec_fingerprints=(
                    receipt.affected_tool_spec_fingerprints
                ),
            )
            prepared = PreparedCapabilityRefresh(
                intent=intent,
                receipt=receipt,
                commit=commit,
                catalog_snapshot=catalog,
                prepared_tool_set=candidate,
                eligibility=eligibility,
            )
            await self.hub.mirror_snapshot_lease(
                snapshot_ref=catalog.snapshot_ref,
                run_id=intent.run_id,
                entries=entries,
            )
            try:
                async with self.store.write_transaction() as db:
                    tx = self.store.bind(db)
                    await tx.acquire_snapshot_lease(
                        snapshot_ref=catalog.snapshot_ref,
                        run_id=intent.run_id,
                        root_run_id=intent.root_run_id,
                        entries=entries,
                    )
                    evidence = commit_continuation(db, prepared)
                    if inspect.isawaitable(evidence):
                        evidence = await evidence
                    if not isinstance(
                        evidence, CapabilityContinuationCommitEvidence
                    ):
                        raise CapabilityStoreConflict(
                            "refresh_continuation_evidence_missing",
                            "execution UoW must return refresh commit evidence",
                        )
                    self._validate_continuation_evidence(prepared, evidence)
                    await tx.commit_refresh_intent(commit)
                    if release_old_snapshot:
                        await tx.release_snapshot_lease(
                            intent.old_catalog_snapshot_ref, intent.run_id
                        )
            except BaseException:
                await self.hub.mirror_snapshot_release(
                    snapshot_ref=catalog.snapshot_ref,
                    run_id=intent.run_id,
                )
                raise
            if release_old_snapshot:
                await self.hub.mirror_snapshot_release(
                    snapshot_ref=intent.old_catalog_snapshot_ref,
                    run_id=intent.run_id,
                )
            return prepared


__all__ = [
    "CapabilityContinuationCommitEvidence",
    "CapabilityRefreshError",
    "CapabilityRefreshService",
    "CapabilityRefreshSnapshotRepository",
    "CapabilityRefreshStageEvidence",
    "CapabilityRefreshStagingService",
    "CapabilityToolSetRebuilder",
    "ContinuationCommitter",
    "PreparedCapabilityRefresh",
    "RefreshSourceStager",
    "RegistryExposureToolSetRebuilder",
    "SqliteCapabilityRefreshSnapshotRepository",
    "context_os_snapshot_ref",
    "exposure_intent_payload",
    "exposure_intent_ref",
]
