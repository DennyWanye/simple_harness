"""Fail-closed readiness gate and immutable owner snapshots."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .contracts import OwnerRef


class CompanionIdentityNotReady(RuntimeError):
    code = "companion_identity_not_ready"
    retryable = True


@dataclass(frozen=True, slots=True)
class FrozenOwnerIdentity:
    owner: OwnerRef
    owner_key: str
    binding_epoch: int


class IdentityReadyGate:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._current: FrozenOwnerIdentity | None = None

    def __deepcopy__(self, _memo):
        # Process-owned synchronization service: per-session contexts share it.
        return self

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._current is not None

    def bind(self, identity: FrozenOwnerIdentity) -> None:
        if identity.binding_epoch < 1 or not identity.owner_key:
            raise ValueError("invalid owner binding")
        with self._lock:
            if (
                self._current is not None
                and identity.binding_epoch < self._current.binding_epoch
            ):
                raise ValueError("binding_epoch_moved_backwards")
            if (
                self._current is not None
                and identity.binding_epoch == self._current.binding_epoch
                and identity != self._current
            ):
                raise ValueError("binding_epoch_identity_conflict")
            self._current = identity

    def unbind(self, *, expected_binding_epoch: int) -> None:
        with self._lock:
            if (
                self._current is not None
                and self._current.binding_epoch != expected_binding_epoch
            ):
                raise ValueError("binding_epoch_mismatch")
            self._current = None

    def freeze(self) -> FrozenOwnerIdentity:
        with self._lock:
            if self._current is None:
                raise CompanionIdentityNotReady(
                    CompanionIdentityNotReady.code
                )
            return self._current


__all__ = [
    "CompanionIdentityNotReady",
    "FrozenOwnerIdentity",
    "IdentityReadyGate",
]
