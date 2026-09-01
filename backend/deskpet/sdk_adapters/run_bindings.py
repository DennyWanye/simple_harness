# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Immutable product-owned bindings between a root Run and SDK execution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from simple_harness import freeze_json, thaw_json

from deskpet.sdk_adapters.context_authority import canonical_sha256


class RunBindingConflict(RuntimeError):
    code = "sdk_run_binding_conflict"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


def _text(value: object, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


@dataclass(frozen=True, slots=True)
class SdkRunBindingV1:
    run_id: str
    session_id: str
    request_id: str
    snapshot_id: str
    provider_id: str
    provider_incarnation_id: str
    provider_config_revision: int
    binding_epoch: int
    model_id: str
    model_params: object
    context_window: int
    catalog_generation: int
    catalog_fingerprint: str
    budget_fingerprint: str
    binding_fingerprint: str
    lease_state: str = "active"

    @classmethod
    def build(cls, **raw: Any) -> SdkRunBindingV1:
        lease_state = str(raw.get("lease_state") or "active").strip().lower()
        if lease_state not in {"active", "waiting"}:
            raise ValueError("lease_state must be active or waiting")
        record = {
            "run_id": _text(raw.get("run_id"), "run_id"),
            "session_id": _text(raw.get("session_id"), "session_id"),
            "request_id": _text(raw.get("request_id"), "request_id"),
            "snapshot_id": _text(raw.get("snapshot_id"), "snapshot_id"),
            "provider_id": _text(raw.get("provider_id"), "provider_id"),
            "provider_incarnation_id": _text(
                raw.get("provider_incarnation_id"), "provider_incarnation_id"
            ),
            "provider_config_revision": int(raw.get("provider_config_revision", 0)),
            "binding_epoch": int(raw.get("binding_epoch", 0)),
            "model_id": _text(raw.get("model_id"), "model_id"),
            "model_params": raw.get("model_params") or {},
            "context_window": int(raw.get("context_window", 0)),
            "catalog_generation": int(raw.get("catalog_generation", 0)),
            "catalog_fingerprint": _text(
                raw.get("catalog_fingerprint"), "catalog_fingerprint"
            ),
            "budget_fingerprint": _text(
                raw.get("budget_fingerprint"), "budget_fingerprint"
            ),
            "lease_state": lease_state,
        }
        for field in (
            "provider_config_revision", "binding_epoch", "context_window",
            "catalog_generation",
        ):
            if record[field] < 0:
                raise ValueError(f"{field} must be non-negative")
        fingerprint_payload = dict(record)
        # Waiting is a lifecycle marker, not a mutation of the immutable binding.
        fingerprint_payload.pop("lease_state")
        return cls(
            **{**record, "model_params": freeze_json(record["model_params"])},
            binding_fingerprint=canonical_sha256(fingerprint_payload),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "snapshot_id": self.snapshot_id,
            "provider_id": self.provider_id,
            "provider_incarnation_id": self.provider_incarnation_id,
            "provider_config_revision": self.provider_config_revision,
            "binding_epoch": self.binding_epoch,
            "model_id": self.model_id,
            "model_params": thaw_json(self.model_params),
            "context_window": self.context_window,
            "catalog_generation": self.catalog_generation,
            "catalog_fingerprint": self.catalog_fingerprint,
            "budget_fingerprint": self.budget_fingerprint,
            "binding_fingerprint": self.binding_fingerprint,
            "lease_state": self.lease_state,
        }

    @classmethod
    def from_record(cls, value: Mapping[str, Any]) -> SdkRunBindingV1:
        if int(value.get("schema_version", 1)) != 1:
            raise ValueError("unsupported SDK run binding schema")
        rebuilt = cls.build(**dict(value))
        supplied = str(value.get("binding_fingerprint") or "")
        if supplied and supplied != rebuilt.binding_fingerprint:
            raise RunBindingConflict("SDK run binding fingerprint mismatch")
        return rebuilt

    def replace(self, **changes: Any) -> SdkRunBindingV1:
        raw = self.to_record()
        raw.update(changes)
        raw.pop("binding_fingerprint", None)
        raw.pop("schema_version", None)
        return self.build(**raw)


class SdkRunBindingRegistry:
    """In-memory lease registry; durable WAITING rows can reconstruct it."""

    def __init__(self) -> None:
        self._bindings: dict[str, SdkRunBindingV1] = {}

    def register(self, binding: SdkRunBindingV1) -> SdkRunBindingV1:
        current = self._bindings.get(binding.run_id)
        if current is not None:
            if current.binding_fingerprint != binding.binding_fingerprint:
                raise RunBindingConflict()
            return current
        self._bindings[binding.run_id] = binding
        return binding

    def resolve(self, run_id: str) -> SdkRunBindingV1 | None:
        return self._bindings.get(str(run_id))

    def mark_waiting(self, run_id: str) -> SdkRunBindingV1:
        binding = self._bindings.get(str(run_id))
        if binding is None:
            raise KeyError(run_id)
        waiting = binding.replace(lease_state="waiting")
        self._bindings[binding.run_id] = waiting
        return waiting

    def mark_terminal(self, run_id: str, terminal_state: str) -> SdkRunBindingV1:
        if str(terminal_state).lower() not in {
            "completed",
            "failed",
            "cancelled",
            "stopped",
        }:
            raise ValueError(
                "terminal_state must be completed, failed, cancelled, or stopped"
            )
        binding = self._bindings.pop(str(run_id), None)
        if binding is None:
            raise KeyError(run_id)
        return binding

    @classmethod
    def reconstruct(
        cls, records: Iterable[Mapping[str, Any]]
    ) -> SdkRunBindingRegistry:
        registry = cls()
        for record in records:
            binding = SdkRunBindingV1.from_record(record)
            registry.register(binding)
        return registry


__all__ = ["RunBindingConflict", "SdkRunBindingRegistry", "SdkRunBindingV1"]
