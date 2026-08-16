"""Product half of the cross-database Tool authorization saga."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from .database import ProductStateDatabase


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _receipt_hash(value: str, name: str) -> str:
    value = _required(value, name)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a SHA-256 hash")
    return value


class AuthorizationSagaState(StrEnum):
    PREPARED = "prepared"
    DECISION_BOUND = "decision_bound"
    EFFECT_BOUND = "effect_bound"
    HANDOFF_COMMITTED = "handoff_committed"
    DISPATCH_UNKNOWN = "dispatch_unknown"
    SETTLED = "settled"
    ABORTED = "aborted"
    EXPIRED = "expired"
    REVOKED = "revoked"
    QUARANTINED = "quarantined"


class SagaConflict(RuntimeError):
    code = "authorization_saga_conflict"


@dataclass(frozen=True, slots=True)
class AuthorizationSagaIdentity:
    authorization_id: str
    principal_id: str
    session_id: str
    root_run_id: str
    run_id: str
    call_id: str
    effect_id: str
    tool_name: str
    arguments: Mapping[str, Any]
    capability_hash: str
    schema_hash: str
    scope_hash: str
    grant_id: str
    grant_version: int
    grant_fingerprint: str
    policy_generation: int
    decision_nonce: str | None
    decision_version: int
    run_lease_epoch: int
    execution_lease_epoch: int

    def __post_init__(self) -> None:
        for name in (
            "authorization_id",
            "principal_id",
            "session_id",
            "root_run_id",
            "run_id",
            "call_id",
            "effect_id",
            "tool_name",
            "capability_hash",
            "schema_hash",
            "scope_hash",
            "grant_id",
            "grant_fingerprint",
        ):
            _required(getattr(self, name), name)
        if self.decision_nonce is not None:
            _required(self.decision_nonce, "decision_nonce")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        for name in (
            "grant_version",
            "policy_generation",
            "decision_version",
            "run_lease_epoch",
            "execution_lease_epoch",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def facts(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def request_fingerprint(self) -> str:
        return _hash(self.facts())


@dataclass(frozen=True, slots=True)
class AuthorizationSagaRecord:
    identity: AuthorizationSagaIdentity
    request_fingerprint: str
    owner_id: str
    state: AuthorizationSagaState
    version: int
    decision_sdk_receipt_hash: str | None = None
    decision_host_receipt_hash: str | None = None
    bound_decision_nonce: str | None = None
    bound_decision_version: int | None = None
    effect_sdk_receipt_hash: str | None = None
    effect_host_receipt_hash: str | None = None
    handoff_sdk_receipt_hash: str | None = None
    handoff_host_receipt_hash: str | None = None
    outcome_hash: str | None = None
    terminal_reason_hash: str | None = None


class AuthorizationSagaRepository:
    def __init__(
        self, database: ProductStateDatabase, *, owner_id: str = "runtime-1"
    ) -> None:
        self.database = database
        self.owner_id = _required(owner_id, "owner_id")

    def prepare(
        self,
        identity: AuthorizationSagaIdentity,
        *,
        now: float,
        fault: Callable[[str], None] | None = None,
    ) -> AuthorizationSagaRecord:
        now = self._time(now)
        conflict: str | None = None
        result_id = identity.authorization_id
        try:
            self.database.connection.execute("BEGIN IMMEDIATE")
            existing = self.database.connection.execute(
                "SELECT authorization_id,request_fingerprint,owner_id FROM authorization_sagas "
                "WHERE authorization_id=? OR (effect_id=? AND call_id=?)",
                (identity.authorization_id, identity.effect_id, identity.call_id),
            ).fetchone()
            if existing is not None:
                result_id = str(existing["authorization_id"])
                if str(existing["owner_id"]) != self.owner_id:
                    conflict = "authorization saga belongs to another owner"
                elif (
                    result_id == identity.authorization_id
                    and str(existing["request_fingerprint"])
                    == identity.request_fingerprint
                ):
                    pass
                else:
                    self.database.connection.execute(
                        "UPDATE authorization_sagas SET state='quarantined',"
                        "version=version+1,updated_at=? WHERE authorization_id=? "
                        "AND state!='settled'",
                        (now, result_id),
                    )
                    conflict = "authorization identity conflicts with durable request"
            else:
                self.database.connection.execute(
                    "INSERT INTO authorization_sagas(authorization_id,request_fingerprint,"
                    "request_json,effect_id,call_id,owner_id,state,version,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,'prepared',0,?,?)",
                    (
                        identity.authorization_id,
                        identity.request_fingerprint,
                        _canonical(identity.facts()),
                        identity.effect_id,
                        identity.call_id,
                        self.owner_id,
                        now,
                        now,
                    ),
                )
                if fault is not None:
                    fault("prepare.before_commit")
            self.database.connection.commit()
        except BaseException:
            self.database.connection.rollback()
            raise
        if conflict is not None:
            raise SagaConflict(conflict)
        result = self.read(result_id)
        assert result is not None
        return result

    def bind_decision(self, authorization_id: str, **kwargs: Any) -> AuthorizationSagaRecord:
        current = self._owned(authorization_id)
        kwargs["decision_nonce"] = _required(
            kwargs.get("decision_nonce") or current.identity.decision_nonce,
            "decision_nonce",
        )
        decision_version = kwargs.get("decision_version", current.identity.decision_version)
        if (
            isinstance(decision_version, bool)
            or not isinstance(decision_version, int)
            or decision_version < 0
        ):
            raise ValueError("decision_version must be a non-negative integer")
        kwargs["decision_version"] = decision_version
        return self._bind(
            authorization_id,
            source=(AuthorizationSagaState.PREPARED,),
            target=AuthorizationSagaState.DECISION_BOUND,
            prefix="decision",
            **kwargs,
        )

    def bind_effect(self, authorization_id: str, **kwargs: Any) -> AuthorizationSagaRecord:
        return self._bind(
            authorization_id,
            source=(AuthorizationSagaState.PREPARED, AuthorizationSagaState.DECISION_BOUND),
            target=AuthorizationSagaState.EFFECT_BOUND,
            prefix="effect",
            **kwargs,
        )

    def commit_handoff(self, authorization_id: str, **kwargs: Any) -> AuthorizationSagaRecord:
        return self._bind(
            authorization_id,
            source=(AuthorizationSagaState.EFFECT_BOUND,),
            target=AuthorizationSagaState.HANDOFF_COMMITTED,
            prefix="handoff",
            **kwargs,
        )

    def _bind(
        self,
        authorization_id: str,
        *,
        source: tuple[AuthorizationSagaState, ...],
        target: AuthorizationSagaState,
        prefix: str,
        expected_version: int,
        sdk_receipt_hash: str,
        host_receipt_hash: str,
        now: float,
        fault: Callable[[str], None] | None = None,
        decision_nonce: str | None = None,
        decision_version: int | None = None,
    ) -> AuthorizationSagaRecord:
        sdk_receipt_hash = _receipt_hash(sdk_receipt_hash, "sdk_receipt_hash")
        host_receipt_hash = _receipt_hash(host_receipt_hash, "host_receipt_hash")
        now = self._time(now)
        current = self._owned(authorization_id)
        stored_sdk = getattr(current, f"{prefix}_sdk_receipt_hash")
        stored_host = getattr(current, f"{prefix}_host_receipt_hash")
        if current.state is target:
            decision_matches = prefix != "decision" or (
                current.bound_decision_nonce == decision_nonce
                and current.bound_decision_version == decision_version
            )
            if (
                stored_sdk == sdk_receipt_hash
                and stored_host == host_receipt_hash
                and decision_matches
            ):
                return current
            self._quarantine(authorization_id, now=now)
            raise SagaConflict("receipt conflicts with durable binding")
        if current.version != expected_version or current.state not in source:
            self._quarantine(authorization_id, now=now)
            raise SagaConflict("authorization saga CAS conflict")
        try:
            self.database.connection.execute("BEGIN IMMEDIATE")
            decision_set = (
                ",bound_decision_nonce=?,bound_decision_version=?"
                if prefix == "decision"
                else ""
            )
            decision_values = (
                (decision_nonce, decision_version) if prefix == "decision" else ()
            )
            changed = self.database.connection.execute(
                f"UPDATE authorization_sagas SET state=?,version=version+1,"
                f"{prefix}_sdk_receipt_hash=?,{prefix}_host_receipt_hash=?"
                f"{decision_set},updated_at=? "
                "WHERE authorization_id=? AND owner_id=? AND version=? AND state IN ("
                + ",".join("?" for _ in source)
                + ")",
                (
                    target.value,
                    sdk_receipt_hash,
                    host_receipt_hash,
                    *decision_values,
                    now,
                    authorization_id,
                    self.owner_id,
                    expected_version,
                    *(state.value for state in source),
                ),
            ).rowcount
            if changed != 1:
                raise SagaConflict("authorization saga CAS conflict")
            if fault is not None:
                fault(f"{prefix}.before_commit")
            self.database.connection.commit()
        except BaseException:
            self.database.connection.rollback()
            raise
        result = self.read(authorization_id)
        assert result is not None
        return result

    def settle(
        self,
        authorization_id: str,
        *,
        expected_version: int,
        outcome_hash: str,
        now: float,
    ) -> AuthorizationSagaRecord:
        outcome_hash = _receipt_hash(outcome_hash, "outcome_hash")
        current = self._owned(authorization_id)
        if current.state is AuthorizationSagaState.SETTLED:
            if current.outcome_hash == outcome_hash:
                return current
            self._quarantine(authorization_id, now=now)
            raise SagaConflict("outcome receipt conflicts with durable settlement")
        if (
            current.version != expected_version
            or current.state
            not in {AuthorizationSagaState.HANDOFF_COMMITTED, AuthorizationSagaState.DISPATCH_UNKNOWN}
        ):
            self._quarantine(authorization_id, now=now)
            raise SagaConflict("settlement CAS conflict")
        with self.database.connection:
            changed = self.database.connection.execute(
                "UPDATE authorization_sagas SET state='settled',version=version+1,"
                "outcome_hash=?,updated_at=? WHERE authorization_id=? AND owner_id=? "
                "AND version=? AND state IN ('handoff_committed','dispatch_unknown')",
                (
                    outcome_hash,
                    self._time(now),
                    authorization_id,
                    self.owner_id,
                    expected_version,
                ),
            ).rowcount
            if changed != 1:
                raise SagaConflict("settlement CAS conflict")
        result = self.read(authorization_id)
        assert result is not None
        return result

    def mark_dispatch_unknown(
        self,
        authorization_id: str,
        *,
        expected_version: int,
        evidence_hash: str,
        now: float,
    ) -> AuthorizationSagaRecord:
        evidence_hash = _receipt_hash(evidence_hash, "evidence_hash")
        now = self._time(now)
        current = self._owned(authorization_id)
        if current.state is AuthorizationSagaState.DISPATCH_UNKNOWN:
            if current.terminal_reason_hash == evidence_hash:
                return current
            self._quarantine(authorization_id, now=now)
            raise SagaConflict("dispatch evidence conflicts with durable state")
        if (
            current.state is not AuthorizationSagaState.HANDOFF_COMMITTED
            or current.version != expected_version
        ):
            self._quarantine(authorization_id, now=now)
            raise SagaConflict("dispatch-unknown CAS conflict")
        with self.database.connection:
            changed = self.database.connection.execute(
                "UPDATE authorization_sagas SET state='dispatch_unknown',"
                "version=version+1,terminal_reason_hash=?,updated_at=? "
                "WHERE authorization_id=? AND owner_id=? AND version=? "
                "AND state='handoff_committed'",
                (
                    evidence_hash,
                    now,
                    authorization_id,
                    self.owner_id,
                    expected_version,
                ),
            ).rowcount
            if changed != 1:
                raise SagaConflict("dispatch-unknown CAS conflict")
        result = self.read(authorization_id)
        assert result is not None
        return result

    def abort(
        self,
        authorization_id: str,
        *,
        expected_version: int,
        reason_hash: str,
        now: float,
    ) -> AuthorizationSagaRecord:
        return self._terminate(
            authorization_id,
            expected_version=expected_version,
            reason_hash=reason_hash,
            target=AuthorizationSagaState.ABORTED,
            now=now,
        )

    def expire(
        self,
        authorization_id: str,
        *,
        expected_version: int,
        reason_hash: str,
        now: float,
    ) -> AuthorizationSagaRecord:
        return self._terminate(
            authorization_id,
            expected_version=expected_version,
            reason_hash=reason_hash,
            target=AuthorizationSagaState.EXPIRED,
            now=now,
        )

    def revoke(
        self,
        authorization_id: str,
        *,
        expected_version: int,
        reason_hash: str,
        now: float,
    ) -> AuthorizationSagaRecord:
        return self._terminate(
            authorization_id,
            expected_version=expected_version,
            reason_hash=reason_hash,
            target=AuthorizationSagaState.REVOKED,
            now=now,
        )

    def _terminate(
        self,
        authorization_id: str,
        *,
        expected_version: int,
        reason_hash: str,
        target: AuthorizationSagaState,
        now: float,
    ) -> AuthorizationSagaRecord:
        if target not in {
            AuthorizationSagaState.ABORTED,
            AuthorizationSagaState.EXPIRED,
            AuthorizationSagaState.REVOKED,
        }:
            raise ValueError("target is not a terminal authorization state")
        reason_hash = _receipt_hash(reason_hash, "reason_hash")
        now = self._time(now)
        current = self._owned(authorization_id)
        if current.state is target:
            if current.terminal_reason_hash == reason_hash:
                return current
            self._quarantine(authorization_id, now=now)
            raise SagaConflict("terminal reason conflicts with durable state")
        if current.state is AuthorizationSagaState.DISPATCH_UNKNOWN:
            if current.terminal_reason_hash == reason_hash:
                return current
            self._quarantine(authorization_id, now=now)
            raise SagaConflict("terminal reason conflicts with dispatch evidence")
        if current.version != expected_version:
            self._quarantine(authorization_id, now=now)
            raise SagaConflict("terminal transition CAS conflict")
        source = {
            AuthorizationSagaState.PREPARED,
            AuthorizationSagaState.DECISION_BOUND,
            AuthorizationSagaState.EFFECT_BOUND,
        }
        durable_target = target
        if current.state in {
            AuthorizationSagaState.HANDOFF_COMMITTED,
            AuthorizationSagaState.DISPATCH_UNKNOWN,
        }:
            # Once both receipts are durable the physical effect may have begun.
            # Cancellation/expiry/revocation can only remove permission for
            # future work; this effect must be reconciled, never called absent.
            source = {
                AuthorizationSagaState.HANDOFF_COMMITTED,
                AuthorizationSagaState.DISPATCH_UNKNOWN,
            }
            durable_target = AuthorizationSagaState.DISPATCH_UNKNOWN
        if current.state not in source:
            self._quarantine(authorization_id, now=now)
            raise SagaConflict("terminal transition is not legal from current state")
        try:
            self.database.connection.execute("BEGIN IMMEDIATE")
            changed = self.database.connection.execute(
                "UPDATE authorization_sagas SET state=?,version=version+1,"
                "terminal_reason_hash=?,updated_at=? WHERE authorization_id=? "
                "AND owner_id=? AND version=? AND state=?",
                (
                    durable_target.value,
                    reason_hash,
                    now,
                    authorization_id,
                    self.owner_id,
                    expected_version,
                    current.state.value,
                ),
            ).rowcount
            if changed != 1:
                raise SagaConflict("terminal transition CAS conflict")
            self.database.connection.commit()
        except BaseException:
            self.database.connection.rollback()
            raise
        result = self.read(authorization_id)
        assert result is not None
        return result

    def read(self, authorization_id: str) -> AuthorizationSagaRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM authorization_sagas WHERE authorization_id=?",
            (_required(authorization_id, "authorization_id"),),
        ).fetchone()
        if row is None:
            return None
        raw = json.loads(str(row["request_json"]))
        identity = AuthorizationSagaIdentity(**raw)
        return AuthorizationSagaRecord(
            identity=identity,
            request_fingerprint=str(row["request_fingerprint"]),
            owner_id=str(row["owner_id"]),
            state=AuthorizationSagaState(str(row["state"])),
            version=int(row["version"]),
            decision_sdk_receipt_hash=row["decision_sdk_receipt_hash"],
            decision_host_receipt_hash=row["decision_host_receipt_hash"],
            bound_decision_nonce=row["bound_decision_nonce"],
            bound_decision_version=row["bound_decision_version"],
            effect_sdk_receipt_hash=row["effect_sdk_receipt_hash"],
            effect_host_receipt_hash=row["effect_host_receipt_hash"],
            handoff_sdk_receipt_hash=row["handoff_sdk_receipt_hash"],
            handoff_host_receipt_hash=row["handoff_host_receipt_hash"],
            outcome_hash=row["outcome_hash"],
            terminal_reason_hash=row["terminal_reason_hash"],
        )

    def read_for_effect(
        self, effect_id: str, call_id: str | None = None
    ) -> AuthorizationSagaRecord | None:
        if call_id is None:
            row = self.database.connection.execute(
                "SELECT authorization_id FROM authorization_sagas WHERE effect_id=?",
                (_required(effect_id, "effect_id"),),
            ).fetchone()
        else:
            row = self.database.connection.execute(
                "SELECT authorization_id FROM authorization_sagas "
                "WHERE effect_id=? AND call_id=?",
                (_required(effect_id, "effect_id"), _required(call_id, "call_id")),
            ).fetchone()
        return None if row is None else self.read(str(row[0]))

    def _owned(self, authorization_id: str) -> AuthorizationSagaRecord:
        current = self.read(authorization_id)
        if current is None:
            raise SagaConflict("authorization saga is missing")
        if current.owner_id != self.owner_id:
            raise SagaConflict("authorization saga belongs to another owner")
        return current

    def _quarantine(self, authorization_id: str, *, now: float) -> None:
        with self.database.connection:
            self.database.connection.execute(
                "UPDATE authorization_sagas SET state='quarantined',version=version+1,"
                "updated_at=? WHERE authorization_id=? AND state!='settled'",
                (self._time(now), authorization_id),
            )

    @staticmethod
    def _time(value: float) -> float:
        value = float(value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("now must be finite and non-negative")
        return value


__all__ = (
    "AuthorizationSagaIdentity",
    "AuthorizationSagaRecord",
    "AuthorizationSagaRepository",
    "AuthorizationSagaState",
    "SagaConflict",
)
