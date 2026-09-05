"""Reuse the signed profile binding of this control connection for HUMAN APIs."""

from __future__ import annotations

from deskpet.companion.control_ingress import CompanionControlIngress
from deskpet.companion.identity_gate import FrozenOwnerIdentity
from deskpet.memory.human_memory_service import HumanMemoryHostServiceError
from deskpet.sdk_adapters.context_route import local_owner_auth


class HumanMemoryControlBinding:
    """Connection-local proof; never inferred from another socket's ready gate."""

    def __init__(self) -> None:
        self._identity: FrozenOwnerIdentity | None = None
        self._connection_id: str | None = None

    def bound(self, ingress: CompanionControlIngress, challenge) -> None:
        # Called only after execute(companion_profile_bind) has verified the
        # credential and committed the profile binding successfully.
        self._identity = ingress.identity_gate.freeze()
        self._connection_id = challenge.connection_id

    def clear(self) -> None:
        self._identity = None
        self._connection_id = None

    def authenticate(self, ingress: CompanionControlIngress | None, challenge):
        if (
            ingress is None
            or challenge is None
            or self._identity is None
            or self._connection_id != challenge.connection_id
        ):
            raise HumanMemoryHostServiceError("human_memory_connection_unbound")
        try:
            current = ingress.identity_gate.freeze()
        except Exception as exc:
            raise HumanMemoryHostServiceError("human_memory_connection_stale") from exc
        if current != self._identity or challenge.binding_epoch != current.binding_epoch:
            raise HumanMemoryHostServiceError("human_memory_connection_stale")
        # A same-owner reconnect may leave the global identity unchanged but
        # revoke this lease. Check the existing durable lease, not query labels.
        with ingress.store.read() as db:
            lease = db.execute(
                "SELECT * FROM profile_control_leases "
                "WHERE device_scope=? AND connection_id=?",
                (ingress.device_scope, challenge.connection_id),
            ).fetchone()
        if lease is None or any(
            lease[key] != value
            for key, value in {
                "status": "active",
                "window_label": "main",
                "scope": "identity_bind",
                "backend_process_instance_id": ingress.verifier.backend_process_instance_id,
                "control_epoch": challenge.control_epoch,
                "challenge_hash": challenge.challenge_hash,
            }.items()
        ):
            raise HumanMemoryHostServiceError("human_memory_connection_stale")
        # Keep the existing single-local-owner namespace and stable evidence
        # authority across reconnects. Connection epochs are admission checks,
        # not a new identity or a changing delivery-idempotency payload.
        return local_owner_auth()
