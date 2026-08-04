"""Trusted Companion action-card decision recovery and signalling."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from deskpet.execution.contracts import (
    ActorContext,
    DecisionSignal,
    DecisionStatus,
    RunRef,
)

from .contracts import OwnerRef
from .identity_gate import (
    CompanionIdentityNotReady,
    FrozenOwnerIdentity,
    IdentityReadyGate,
)
from .store import CompanionStore


class CompanionActionDecisionRejected(RuntimeError):
    """Fail-closed rejection carrying a stable product-neutral reason code."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class TrustedCompanionControlIdentity:
    """Identity resolved by the authenticated Companion control channel."""

    owner: OwnerRef
    owner_key: str
    binding_epoch: int

    def __post_init__(self) -> None:
        if not str(self.owner_key or "").strip() or self.binding_epoch < 1:
            raise ValueError("trusted_companion_control_identity_invalid")

    @classmethod
    def from_frozen(
        cls, identity: FrozenOwnerIdentity
    ) -> "TrustedCompanionControlIdentity":
        return cls(
            owner=identity.owner,
            owner_key=identity.owner_key,
            binding_epoch=identity.binding_epoch,
        )


@dataclass(frozen=True, slots=True)
class RecoveredCompanionDecision:
    decision_id: str
    run_id: str
    session_id: str
    root_run_id: str
    request_id: str
    principal_id: str
    auth_epoch: int
    status: str
    nonce: str
    decision_version: int
    expires_at: float | None
    domain_kind: str | None
    domain_id: str | None
    call_id: str | None
    effect_id: str | None
    tool_name: str | None
    args_hash: str | None
    capability_hash: str | None
    scope_hash: str | None


class ExecutionDecisionRecoveryPort(Protocol):
    async def recover_companion_decision(
        self, decision_id: str
    ) -> RecoveredCompanionDecision: ...


class KernelDecisionSignalPort(Protocol):
    async def signal_decision(
        self,
        ref: RunRef,
        actor: ActorContext,
        signal: DecisionSignal,
    ) -> object: ...


class SqliteExecutionDecisionRecovery:
    """Read-only decision/run recovery from the authoritative execution DB."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)

    async def recover_companion_decision(
        self, decision_id: str
    ) -> RecoveredCompanionDecision:
        normalized = str(decision_id or "").strip()
        if not normalized:
            raise CompanionActionDecisionRejected("decision_id_required")
        if self._path == ":memory:":
            raise ValueError("execution_recovery_requires_durable_db")
        uri = Path(self._path).resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(
            uri, isolation_level=None, timeout=5.0, uri=True
        )
        db.row_factory = sqlite3.Row
        try:
            row = db.execute(
                """SELECT d.*,r.session_id,r.root_run_id,r.request_id,
                          r.principal_id,r.auth_epoch
                   FROM execution_decisions AS d
                   JOIN execution_runs AS r ON r.run_id=d.run_id
                   WHERE d.decision_id=?""",
                (normalized,),
            ).fetchone()
        finally:
            db.close()
        if row is None:
            raise CompanionActionDecisionRejected("decision_not_found")
        return RecoveredCompanionDecision(
            decision_id=str(row["decision_id"]),
            run_id=str(row["run_id"]),
            session_id=str(row["session_id"]),
            root_run_id=str(row["root_run_id"]),
            request_id=str(row["request_id"]),
            principal_id=str(row["principal_id"]),
            auth_epoch=int(row["auth_epoch"]),
            status=str(row["status"]),
            nonce=str(row["nonce"]),
            decision_version=int(row["decision_version"]),
            expires_at=(
                None if row["expires_at"] is None else float(row["expires_at"])
            ),
            domain_kind=row["domain_kind"],
            domain_id=row["domain_id"],
            call_id=row["call_id"],
            effect_id=row["effect_id"],
            tool_name=row["tool_name"],
            args_hash=row["args_hash"],
            capability_hash=row["capability_hash"],
            scope_hash=row["scope_hash"],
        )


class CompanionActionDecisionService:
    """Resolve ``decision_id + allow`` through authoritative durable bindings."""

    def __init__(
        self,
        *,
        identity_gate: IdentityReadyGate,
        companion_store: CompanionStore,
        execution: ExecutionDecisionRecoveryPort,
        kernel_client: KernelDecisionSignalPort,
        wall_time: Any = time.time,
    ) -> None:
        self._identity_gate = identity_gate
        self._companion_store = companion_store
        self._execution = execution
        self._kernel_client = kernel_client
        self._wall_time = wall_time

    async def decide(
        self,
        *,
        decision_id: str,
        allow: bool,
        control_identity: TrustedCompanionControlIdentity,
    ) -> object:
        if not isinstance(allow, bool):
            raise CompanionActionDecisionRejected("decision_allow_invalid")
        if not isinstance(control_identity, TrustedCompanionControlIdentity):
            raise CompanionActionDecisionRejected("control_identity_untrusted")
        try:
            current = self._identity_gate.freeze()
        except CompanionIdentityNotReady as exc:
            raise CompanionActionDecisionRejected("identity_logged_out") from exc
        if (
            current.owner != control_identity.owner
            or current.owner_key != control_identity.owner_key
        ):
            raise CompanionActionDecisionRejected("control_owner_mismatch")
        if current.binding_epoch != control_identity.binding_epoch:
            raise CompanionActionDecisionRejected("control_binding_stale")

        recovered = await self._execution.recover_companion_decision(decision_id)
        if recovered.status != DecisionStatus.OPEN.value:
            raise CompanionActionDecisionRejected("decision_not_open")
        if (
            recovered.expires_at is not None
            and recovered.expires_at <= float(self._wall_time())
        ):
            raise CompanionActionDecisionRejected("decision_expired")
        binding = self._read_active_binding(
            control_identity.owner, run_id=recovered.run_id
        )
        if binding is None:
            raise CompanionActionDecisionRejected("companion_run_binding_stale")
        if (
            str(binding["request_id"]) != recovered.request_id
            or str(binding["root_run_id"]) != recovered.root_run_id
        ):
            raise CompanionActionDecisionRejected("companion_run_binding_mismatch")

        signal = DecisionSignal(
            decision_id=recovered.decision_id,
            run_id=recovered.run_id,
            expected_session_id=recovered.session_id,
            nonce=recovered.nonce,
            expected_version=recovered.decision_version,
            allow=allow,
            response_schema_version=1,
            response={
                "allow": allow,
                "provenance": "user_explicit_companion_action",
            },
            domain_kind=recovered.domain_kind,
            domain_id=recovered.domain_id,
            call_id=recovered.call_id,
            effect_id=recovered.effect_id,
            tool_name=recovered.tool_name,
            args_hash=recovered.args_hash,
            capability_hash=recovered.capability_hash,
            scope_hash=recovered.scope_hash,
        )
        ref = RunRef(recovered.run_id, recovered.session_id)
        actor = ActorContext(
            principal_id=recovered.principal_id,
            session_id=recovered.session_id,
            auth_epoch=recovered.auth_epoch,
            root_run_id=recovered.root_run_id,
        )
        return await self._kernel_client.signal_decision(ref, actor, signal)

    def _read_active_binding(
        self, owner: OwnerRef, *, run_id: str
    ) -> dict[str, Any] | None:
        with self._companion_store.read() as db:
            row = db.execute(
                """SELECT * FROM companion_run_bindings
                   WHERE profile_id=? AND profile_generation=? AND run_id=?
                     AND status='active'""",
                (owner.profile_id, owner.profile_generation, run_id),
            ).fetchone()
        return None if row is None else dict(row)


__all__ = [
    "CompanionActionDecisionRejected",
    "CompanionActionDecisionService",
    "ExecutionDecisionRecoveryPort",
    "KernelDecisionSignalPort",
    "RecoveredCompanionDecision",
    "SqliteExecutionDecisionRecovery",
    "TrustedCompanionControlIdentity",
]
