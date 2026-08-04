# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""One-time import of the pre-SQLite permission auto-mode setting."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from deskpet.capabilities.store import (
    CapabilityStore,
    LegacyAuthorizationImportOutcome,
    LegacyAuthorizationImportRecord,
)
from deskpet.permissions.policy import AuthorizationMode, AuthorizationPolicyState

LEGACY_AUTO_MODE_SOURCE_KEY = "permissions_auto_mode.json:v1"


@dataclass(frozen=True, slots=True)
class LegacyAutoModeImportResult:
    """Result of consuming the legacy source for this execution database."""

    record: LegacyAuthorizationImportRecord
    policy_state: AuthorizationPolicyState
    consumed_now: bool


@dataclass(frozen=True, slots=True)
class _ObservedLegacyAutoMode:
    outcome: LegacyAuthorizationImportOutcome
    source_fingerprint: str | None
    mode: AuthorizationMode | None
    error_code: str | None


def _observe_legacy_auto_mode(path: Path) -> _ObservedLegacyAutoMode:
    try:
        payload = path.read_bytes()
    except FileNotFoundError:
        return _ObservedLegacyAutoMode(
            outcome="missing",
            source_fingerprint=None,
            mode=None,
            error_code=None,
        )
    except OSError:
        return _ObservedLegacyAutoMode(
            outcome="invalid",
            source_fingerprint=None,
            mode=None,
            error_code="legacy_auto_mode_read_failed",
        )

    source_fingerprint = hashlib.sha256(payload).hexdigest()
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _ObservedLegacyAutoMode(
            outcome="invalid",
            source_fingerprint=source_fingerprint,
            mode=None,
            error_code="legacy_auto_mode_invalid_json",
        )
    if not isinstance(decoded, dict) or type(decoded.get("enabled")) is not bool:
        return _ObservedLegacyAutoMode(
            outcome="invalid",
            source_fingerprint=source_fingerprint,
            mode=None,
            error_code="legacy_auto_mode_invalid_payload",
        )
    return _ObservedLegacyAutoMode(
        outcome="imported",
        source_fingerprint=source_fingerprint,
        mode="auto" if decoded["enabled"] else "manual",
        error_code=None,
    )


async def import_legacy_auto_mode_once(
    store: CapabilityStore,
    path: str | Path,
) -> LegacyAutoModeImportResult:
    """Consume ``permissions_auto_mode.json`` once per execution database.

    The policy transition and durable import marker commit in one SQLite
    transaction.  Once a marker exists, this function never reads the legacy
    source into policy again; callers must treat the SQLite policy row as the
    only authority and may leave the old file untouched for rollback evidence.
    """

    async with store.write_transaction() as db:
        tx = store.bind(db)
        existing = await tx.get_legacy_authorization_import(
            LEGACY_AUTO_MODE_SOURCE_KEY
        )
        if existing is not None:
            return LegacyAutoModeImportResult(
                record=existing,
                policy_state=await tx.get_policy_state(),
                consumed_now=False,
            )

        observed = _observe_legacy_auto_mode(Path(path))
        policy_state = await tx.get_policy_state()
        if observed.mode is not None:
            policy_state = await tx.compare_and_set_policy_mode(
                observed.mode,
                expected_generation=policy_state.generation,
            )
        record = LegacyAuthorizationImportRecord(
            source_key=LEGACY_AUTO_MODE_SOURCE_KEY,
            outcome=observed.outcome,
            source_fingerprint=observed.source_fingerprint,
            imported_mode=observed.mode,
            error_code=observed.error_code,
            imported_at=store.now(),
        )
        await tx.put_legacy_authorization_import(record)
        return LegacyAutoModeImportResult(
            record=record,
            policy_state=policy_state,
            consumed_now=True,
        )


__all__ = [
    "LEGACY_AUTO_MODE_SOURCE_KEY",
    "LegacyAutoModeImportResult",
    "import_legacy_auto_mode_once",
]
