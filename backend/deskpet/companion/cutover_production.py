# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Production adapters for the one-time Companion growth cutover.

Legacy sources are read only.  Their normalized snapshot is frozen before the
irreversible marker, and every imported row is owned by
``legacy_local_profile``.  Nothing in this module can publish a Capability
binding or reopen a retired writer.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deskpet.capabilities.contracts import OwnerScopeKey

from .authority import GrowthIngressUnavailable
from .contracts import GrowthEvent, OwnerRef
from .cutover import GrowthCutoverStepAuthorization
from .store import canonical_hash


_LEGACY_OWNER = OwnerRef("legacy_local_profile", 1)


class RetiredLegacyGrowthAuthority:
    """Fail closed when a pre-marker upgrade aborts.

    The final binary intentionally contains no callback capable of reviving
    the old JSON/codifier/reminder writers.  The durable pointer may return to
    ``legacy`` before the irreversible marker, but ordinary chat remains
    available while growth ingress reports a visible migration error.
    """

    async def read(self, _request: Any) -> object:
        raise GrowthIngressUnavailable("legacy_growth_authority_retired")

    async def write(self, _request: Any) -> object:
        raise GrowthIngressUnavailable("legacy_growth_authority_retired")


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class LegacyGrowthSnapshot:
    preference_path: Path
    preference_hash: str
    pending_candidates: tuple[Mapping[str, Any], ...]
    skill_files: tuple[Mapping[str, Any], ...]
    migration_hash: str


class LegacySkillMigrationBlocked(RuntimeError):
    """A legacy Skill cannot be retired without an immutable Manager receipt."""

    def __init__(self, *, file_count: int, inventory_hash: str) -> None:
        self.file_count = int(file_count)
        self.inventory_hash = str(inventory_hash)
        super().__init__(
            "legacy_skill_immutable_migration_unavailable:"
            f"file_count={self.file_count}:inventory_hash={self.inventory_hash}"
        )


@dataclass(frozen=True, slots=True)
class CapabilityOwnerCutoverSnapshot:
    """Read-only Capability authority facts frozen into the cutover plan."""

    binding_generation: int
    owner_binding_set_stamp: str
    owner_key: str
    scope_key: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.binding_generation, int)
            or isinstance(self.binding_generation, bool)
            or self.binding_generation < 0
        ):
            raise ValueError("capability binding generation must be non-negative")
        if len(self.owner_binding_set_stamp) != 64:
            raise ValueError("owner binding set stamp must be a SHA-256 digest")
        if not self.owner_key or not self.scope_key:
            raise ValueError("capability owner scope is required")


def require_legacy_skill_immutable_migration(
    snapshot: LegacyGrowthSnapshot,
) -> Mapping[str, Any]:
    """Prove that cutover does not need to invent a legacy Skill publish.

    The production composition currently has no operation-scoped conversion
    from an arbitrary legacy user directory into a validated immutable pack
    plus a trustworthy CapabilityPackManager receipt.  Treating the file
    inventory as if it were such a receipt would retire the only working
    source without proving a replacement.  Therefore any non-empty inventory
    blocks before the irreversible marker.
    """

    inventory_hash = canonical_hash(tuple(dict(row) for row in snapshot.skill_files))
    if snapshot.skill_files:
        raise LegacySkillMigrationBlocked(
            file_count=len(snapshot.skill_files),
            inventory_hash=inventory_hash,
        )
    return {
        "legacy_skill_strategy": "no_legacy_skill_mutation",
        "legacy_skill_file_count": 0,
        "legacy_skill_inventory_hash": inventory_hash,
        "immutable_manager_receipt_required": True,
    }


async def read_capability_owner_cutover_snapshot(
    *,
    capability_store: Any,
    owner_key: str,
    scope_key: str,
) -> CapabilityOwnerCutoverSnapshot:
    """Read real Store generation/stamp facts without publishing anything."""

    key = OwnerScopeKey(str(owner_key), "user", str(scope_key))
    state = await capability_store.state()
    vector = await capability_store.read_detail_token_vector((key,))
    items = tuple(vector.items)
    if len(items) != 1 or items[0].key != key:
        raise RuntimeError("capability_owner_cutover_snapshot_incomplete")
    token = items[0]
    return CapabilityOwnerCutoverSnapshot(
        binding_generation=int(state.binding_generation),
        owner_binding_set_stamp=str(token.committed_owner_binding_set_stamp),
        owner_key=key.owner_key,
        scope_key=key.scope_key,
    )


def read_legacy_growth_snapshot(
    *,
    state_db_path: str | Path,
    preference_path: str | Path,
    skill_roots: Sequence[str | Path] = (),
) -> LegacyGrowthSnapshot:
    """Read normalized legacy residue without creating or mutating its tables."""

    state_path = Path(state_db_path).expanduser().resolve(strict=False)
    pref_path = Path(preference_path).expanduser().resolve(strict=False)
    candidates: list[Mapping[str, Any]] = []
    if state_path.is_file():
        uri = f"{state_path.as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as db:
            db.row_factory = sqlite3.Row
            exists = db.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type='table' AND name='pending_skill_candidates'"""
            ).fetchone()
            if exists is not None:
                candidates = [
                    dict(row)
                    for row in db.execute(
                        """SELECT id,name,description,trigger_pattern,steps_json,
                                  status,created_at
                           FROM pending_skill_candidates
                           WHERE status='pending'
                           ORDER BY id"""
                    ).fetchall()
                ]
    skill_files: list[Mapping[str, Any]] = []
    for raw_root in sorted(str(Path(item)) for item in skill_roots):
        root = Path(raw_root).expanduser().resolve(strict=False)
        if not root.is_dir():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            skill_files.append(
                {
                    "root": str(root),
                    "relative_path": relative,
                    "size_bytes": path.stat().st_size,
                    "content_hash": _file_hash(path),
                }
            )
    preference_hash = _file_hash(pref_path) if pref_path.is_file() else ""
    normalized_candidates = tuple(
        json.loads(
            json.dumps(
                dict(row),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
        for row in candidates
    )
    normalized_skills = tuple(dict(row) for row in skill_files)
    migration_hash = canonical_hash(
        {
            "schema_version": 1,
            "source_owner_policy": "legacy_local_only",
            "preference_hash": preference_hash,
            "pending_candidates": normalized_candidates,
            "skill_files": normalized_skills,
        }
    )
    return LegacyGrowthSnapshot(
        preference_path=pref_path,
        preference_hash=preference_hash,
        pending_candidates=normalized_candidates,
        skill_files=normalized_skills,
        migration_hash=migration_hash,
    )


def import_legacy_growth_snapshot(
    *,
    store: Any,
    preference_resolver: Any,
    owner: OwnerRef,
    snapshot: LegacyGrowthSnapshot,
) -> Mapping[str, Any]:
    """Idempotently import legacy facts without granting candidate authority."""

    if owner != _LEGACY_OWNER:
        raise ValueError("legacy growth import owner must be legacy_local_profile:1")
    skill_migration_proof = require_legacy_skill_immutable_migration(snapshot)
    imported_preferences = preference_resolver.import_legacy_json(
        owner, snapshot.preference_path
    )
    imported_candidates = 0
    for row in snapshot.pending_candidates:
        source_hash = canonical_hash(dict(row))
        legacy_id = str(row["id"])
        store.record_growth_event(
            GrowthEvent(
                owner=owner,
                event_id=f"legacy-pending-candidate:{legacy_id}:{source_hash[:16]}",
                source_kind="legacy_pending_candidate",
                source_ref=f"state.db:pending_skill_candidates:{legacy_id}",
                context_key=f"legacy_pending_candidate:{legacy_id}",
                root_run_id="legacy-growth-cutover",
                reason_code="legacy_needs_evidence",
                payload={
                    "source_owner_policy": "legacy_local_only",
                    "legacy_candidate_id": legacy_id,
                    "name": str(row.get("name") or ""),
                    "description": str(row.get("description") or ""),
                    "trigger_pattern": str(row.get("trigger_pattern") or ""),
                    "steps_json": str(row.get("steps_json") or "[]"),
                    "legacy_source_hash": source_hash,
                    "activation_allowed": False,
                },
            )
        )
        imported_candidates += 1
    imported_skill_files = 0
    by_root: dict[str, list[Mapping[str, Any]]] = {}
    for row in snapshot.skill_files:
        by_root.setdefault(str(row["root"]), []).append(row)
    for root, rows in sorted(by_root.items()):
        inventory_hash = canonical_hash(rows)
        store.record_growth_event(
            GrowthEvent(
                owner=owner,
                event_id=f"legacy-skill-inventory:{inventory_hash[:24]}",
                source_kind="legacy_skill_inventory",
                source_ref=f"legacy-user-skill-root:{inventory_hash}",
                context_key=f"legacy_skill_inventory:{inventory_hash}",
                root_run_id="legacy-growth-cutover",
                reason_code="legacy_needs_evidence",
                payload={
                    "source_owner_policy": "legacy_local_only",
                    "root_hash": hashlib.sha256(root.encode("utf-8")).hexdigest(),
                    "inventory_hash": inventory_hash,
                    "file_count": len(rows),
                    "activation_allowed": False,
                },
            )
        )
        imported_skill_files += len(rows)
    return {
        "schema_version": 1,
        "migration_hash": snapshot.migration_hash,
        "source_owner_policy": "legacy_local_only",
        "imported_preferences": imported_preferences,
        "imported_candidates": imported_candidates,
        "imported_skill_files": imported_skill_files,
        "activation_allowed": False,
        **skill_migration_proof,
    }


CutoverCallback = Callable[
    [GrowthCutoverStepAuthorization],
    object | Awaitable[object],
]


class ProductionGrowthCutoverStepExecutor:
    """Execute one exact production callback before issuing its receipt hash."""

    def __init__(self, callbacks: Mapping[str, CutoverCallback]) -> None:
        self._callbacks = dict(callbacks)

    async def execute(
        self, authorization: GrowthCutoverStepAuthorization
    ) -> Mapping[str, Any]:
        callback = self._callbacks.get(authorization.step)
        if callback is None:
            raise RuntimeError(
                f"growth_cutover_callback_missing:{authorization.step}"
            )
        result = callback(authorization)
        if inspect.isawaitable(result):
            result = await result
        if (
            not isinstance(result, Mapping)
            or not result
            or any(not isinstance(key, str) or not key for key in result)
        ):
            raise RuntimeError(
                f"growth_cutover_step_not_proved:{authorization.step}"
            )
        proof = {
            "schema_version": 1,
            "cutover_operation_id": authorization.cutover_operation_id,
            "plan_hash": authorization.plan_hash,
            "step": authorization.step,
            "marker_committed": authorization.marker_committed,
            "result": dict(result),
        }
        return {
            "receipt_hash": authorization.expected_step_hash,
            "result_hash": canonical_hash(proof),
        }


__all__ = [
    "CapabilityOwnerCutoverSnapshot",
    "LegacySkillMigrationBlocked",
    "LegacyGrowthSnapshot",
    "ProductionGrowthCutoverStepExecutor",
    "RetiredLegacyGrowthAuthority",
    "import_legacy_growth_snapshot",
    "read_legacy_growth_snapshot",
    "read_capability_owner_cutover_snapshot",
    "require_legacy_skill_immutable_migration",
]
