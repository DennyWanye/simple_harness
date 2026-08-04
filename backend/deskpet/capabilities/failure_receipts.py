# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Host-signed capability failure and bounded repair contracts.

Capability workers may return arbitrary error payloads, but only the host may
turn a failed prepared call into a repairable receipt.  The receipt freezes the
exact immutable pack version, prepared arguments, binding, and error
fingerprint that the repair workflow is allowed to use.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

from .contracts import JsonValue, canonical_json, fingerprint_json

CapabilityRepairAttemptStatus = Literal[
    "admitted",
    "running",
    "succeeded",
    "failed",
    "cancelled",
]


def parse_capability_registry_source(
    source: str,
) -> tuple[str, str, str] | None:
    """Parse the immutable source emitted by ``CapabilityToolPublisher``."""

    prefix, separator, remainder = str(source or "").partition(":")
    if prefix != "capability" or not separator:
        return None
    try:
        capability_id, version, manifest_hash = remainder.rsplit(":", 2)
    except ValueError:
        return None
    if (
        not capability_id
        or not version
        or len(manifest_hash) != 64
        or any(character not in "0123456789abcdef" for character in manifest_hash)
    ):
        return None
    return capability_id, version, manifest_hash


def _required_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _digest(value: object, name: str) -> str:
    text = _required_text(value, name)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return text


def _json_mapping(
    value: Mapping[str, Any], name: str
) -> Mapping[str, JsonValue]:
    try:
        encoded = canonical_json(dict(value))
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a JSON object") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{name} must be a JSON object")
    return MappingProxyType(decoded)


@dataclass(frozen=True, slots=True)
class CapabilityFailureReceipt:
    receipt_ref: str
    root_run_id: str
    run_id: str
    attempt_id: str
    failure_report_ref: str
    provider_call_id: str
    effect_id: str
    capability_id: str
    pack_version: str
    manifest_hash: str
    tool_name: str
    tool_spec_fingerprint: str
    binding_scope: str
    scope_key: str
    canonical_args: Mapping[str, JsonValue]
    args_hash: str
    error_code: str
    error_fingerprint: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "root_run_id",
            "run_id",
            "attempt_id",
            "failure_report_ref",
            "provider_call_id",
            "effect_id",
            "capability_id",
            "pack_version",
            "tool_name",
            "scope_key",
            "error_code",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        for name in (
            "receipt_ref",
            "manifest_hash",
            "tool_spec_fingerprint",
            "args_hash",
            "error_fingerprint",
        ):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        if self.binding_scope not in {"builtin", "run", "project", "user"}:
            raise ValueError(f"unsupported binding scope: {self.binding_scope}")
        canonical_args = _json_mapping(self.canonical_args, "canonical_args")
        if fingerprint_json(dict(canonical_args)) != self.args_hash:
            raise ValueError("canonical args do not match args_hash")
        evidence_refs = tuple(
            dict.fromkeys(
                _required_text(item, "evidence_ref") for item in self.evidence_refs
            )
        )
        object.__setattr__(self, "canonical_args", canonical_args)
        object.__setattr__(self, "evidence_refs", evidence_refs)
        if self.receipt_ref != self.expected_receipt_ref:
            raise ValueError("capability failure receipt identity mismatch")

    @property
    def signed_payload(self) -> dict[str, JsonValue]:
        return {
            "root_run_id": self.root_run_id,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "failure_report_ref": self.failure_report_ref,
            "provider_call_id": self.provider_call_id,
            "effect_id": self.effect_id,
            "capability_id": self.capability_id,
            "pack_version": self.pack_version,
            "manifest_hash": self.manifest_hash,
            "tool_name": self.tool_name,
            "tool_spec_fingerprint": self.tool_spec_fingerprint,
            "binding_scope": self.binding_scope,
            "scope_key": self.scope_key,
            "canonical_args": dict(self.canonical_args),
            "args_hash": self.args_hash,
            "error_code": self.error_code,
            "error_fingerprint": self.error_fingerprint,
            "evidence_refs": list(self.evidence_refs),
        }

    @property
    def expected_receipt_ref(self) -> str:
        return hashlib.sha256(
            canonical_json(self.signed_payload).encode("utf-8")
        ).hexdigest()

    def to_dict(self) -> dict[str, JsonValue]:
        return {"receipt_ref": self.receipt_ref, **self.signed_payload}

    @classmethod
    def from_dict(
        cls, value: Mapping[str, object]
    ) -> "CapabilityFailureReceipt":
        canonical_args = value.get("canonical_args")
        if not isinstance(canonical_args, Mapping):
            raise ValueError("canonical_args must be an object")
        return cls(
            receipt_ref=str(value.get("receipt_ref") or ""),
            root_run_id=str(value.get("root_run_id") or ""),
            run_id=str(value.get("run_id") or ""),
            attempt_id=str(value.get("attempt_id") or ""),
            failure_report_ref=str(value.get("failure_report_ref") or ""),
            provider_call_id=str(value.get("provider_call_id") or ""),
            effect_id=str(value.get("effect_id") or ""),
            capability_id=str(value.get("capability_id") or ""),
            pack_version=str(value.get("pack_version") or ""),
            manifest_hash=str(value.get("manifest_hash") or ""),
            tool_name=str(value.get("tool_name") or ""),
            tool_spec_fingerprint=str(
                value.get("tool_spec_fingerprint") or ""
            ),
            binding_scope=str(value.get("binding_scope") or ""),
            scope_key=str(value.get("scope_key") or ""),
            canonical_args=dict(canonical_args),
            args_hash=str(value.get("args_hash") or ""),
            error_code=str(value.get("error_code") or ""),
            error_fingerprint=str(value.get("error_fingerprint") or ""),
            evidence_refs=tuple(
                str(item) for item in value.get("evidence_refs", ()) or ()
            ),
        )


class CapabilityFailureReceiptIssuer:
    """Construct deterministic receipts exclusively from trusted host facts."""

    @staticmethod
    def issue(
        *,
        root_run_id: str,
        run_id: str,
        attempt_id: str,
        failure_report_ref: str,
        provider_call_id: str,
        effect_id: str,
        capability_id: str,
        pack_version: str,
        manifest_hash: str,
        tool_name: str,
        tool_spec_fingerprint: str,
        binding_scope: str,
        scope_key: str,
        canonical_args: Mapping[str, Any],
        error_code: str,
        error_fingerprint: str,
        evidence_refs: Sequence[str] = (),
    ) -> CapabilityFailureReceipt:
        args = json.loads(canonical_json(dict(canonical_args)))
        args_hash = fingerprint_json(args)
        unsigned: dict[str, JsonValue] = {
            "root_run_id": str(root_run_id),
            "run_id": str(run_id),
            "attempt_id": str(attempt_id),
            "failure_report_ref": str(failure_report_ref),
            "provider_call_id": str(provider_call_id),
            "effect_id": str(effect_id),
            "capability_id": str(capability_id),
            "pack_version": str(pack_version),
            "manifest_hash": str(manifest_hash),
            "tool_name": str(tool_name),
            "tool_spec_fingerprint": str(tool_spec_fingerprint),
            "binding_scope": str(binding_scope),
            "scope_key": str(scope_key),
            "canonical_args": args,
            "args_hash": args_hash,
            "error_code": str(error_code),
            "error_fingerprint": str(error_fingerprint),
            "evidence_refs": list(dict.fromkeys(str(item) for item in evidence_refs)),
        }
        receipt_ref = hashlib.sha256(
            canonical_json(unsigned).encode("utf-8")
        ).hexdigest()
        return CapabilityFailureReceipt(
            receipt_ref=receipt_ref,
            root_run_id=str(root_run_id),
            run_id=str(run_id),
            attempt_id=str(attempt_id),
            failure_report_ref=str(failure_report_ref),
            provider_call_id=str(provider_call_id),
            effect_id=str(effect_id),
            capability_id=str(capability_id),
            pack_version=str(pack_version),
            manifest_hash=str(manifest_hash),
            tool_name=str(tool_name),
            tool_spec_fingerprint=str(tool_spec_fingerprint),
            binding_scope=str(binding_scope),
            scope_key=str(scope_key),
            canonical_args=args,
            args_hash=args_hash,
            error_code=str(error_code),
            error_fingerprint=str(error_fingerprint),
            evidence_refs=tuple(
                dict.fromkeys(str(item) for item in evidence_refs)
            ),
        )


@dataclass(frozen=True, slots=True)
class CapabilityRepairAttempt:
    root_run_id: str
    error_fingerprint: str
    attempt_no: int
    failure_receipt_ref: str
    control_call_id: str
    status: CapabilityRepairAttemptStatus
    child_run_id: str | None = None
    operation_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "root_run_id",
            "failure_receipt_ref",
            "control_call_id",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        object.__setattr__(
            self,
            "error_fingerprint",
            _digest(self.error_fingerprint, "error_fingerprint"),
        )
        object.__setattr__(
            self,
            "failure_receipt_ref",
            _digest(self.failure_receipt_ref, "failure_receipt_ref"),
        )
        if not 1 <= self.attempt_no <= 3:
            raise ValueError("capability repair attempt must be between 1 and 3")
        if self.status not in {
            "admitted",
            "running",
            "succeeded",
            "failed",
            "cancelled",
        }:
            raise ValueError(f"unsupported repair attempt status: {self.status}")


__all__ = [
    "CapabilityFailureReceipt",
    "CapabilityFailureReceiptIssuer",
    "CapabilityRepairAttempt",
    "CapabilityRepairAttemptStatus",
    "parse_capability_registry_source",
]
