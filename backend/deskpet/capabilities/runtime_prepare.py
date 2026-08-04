"""Product-neutral prepare/start-ACK/health/activate runtime-set protocol."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


def _required(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class PreparedRuntimeInstanceSpec:
    entry_id: str
    runtime_kind: str
    adapter_id: str
    adapter_fingerprint: str
    start_envelope: Mapping[str, Any]
    health_envelope: Mapping[str, Any]
    ordinal: int

    def __post_init__(self) -> None:
        for field_name in (
            "entry_id",
            "runtime_kind",
            "adapter_id",
            "adapter_fingerprint",
        ):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), field_name),
            )
        if self.ordinal < 0:
            raise ValueError("runtime instance ordinal must be non-negative")
        object.__setattr__(self, "start_envelope", dict(self.start_envelope))
        object.__setattr__(self, "health_envelope", dict(self.health_envelope))

    @property
    def fingerprint(self) -> str:
        return _hash(
            {
                "entry_id": self.entry_id,
                "runtime_kind": self.runtime_kind,
                "adapter_id": self.adapter_id,
                "adapter_fingerprint": self.adapter_fingerprint,
                "start_envelope": self.start_envelope,
                "health_envelope": self.health_envelope,
                "ordinal": self.ordinal,
            }
        )


@dataclass(frozen=True, slots=True)
class PreparedRuntimeSet:
    operation_id: str
    purpose: str
    owner_key: str
    scope: str
    scope_key: str
    owner_runtime_activation_generation: int
    owner_binding_set_stamp: str
    launch_revocation_epoch: int
    instances: tuple[PreparedRuntimeInstanceSpec, ...]
    runtime_set_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for field_name in (
            "operation_id",
            "purpose",
            "owner_key",
            "scope",
            "scope_key",
            "owner_binding_set_stamp",
        ):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), field_name),
            )
        if (
            self.owner_runtime_activation_generation < 1
            or self.launch_revocation_epoch < 0
        ):
            raise ValueError("runtime set generation/epoch is invalid")
        instances = tuple(sorted(self.instances, key=lambda item: item.ordinal))
        if tuple(item.ordinal for item in instances) != tuple(range(len(instances))):
            raise ValueError("runtime instance ordinals must be contiguous")
        if len({item.entry_id for item in instances}) != len(instances):
            raise ValueError("runtime set entries must be unique")
        object.__setattr__(self, "instances", instances)
        object.__setattr__(
            self,
            "runtime_set_hash",
            _hash(
                {
                    "operation_id": self.operation_id,
                    "purpose": self.purpose,
                    "owner_key": self.owner_key,
                    "scope": self.scope,
                    "scope_key": self.scope_key,
                    "owner_runtime_activation_generation": (
                        self.owner_runtime_activation_generation
                    ),
                    "owner_binding_set_stamp": self.owner_binding_set_stamp,
                    "launch_revocation_epoch": self.launch_revocation_epoch,
                    "instances": [item.fingerprint for item in instances],
                }
            ),
        )

    @property
    def expected_instance_count(self) -> int:
        return len(self.instances)


@dataclass(frozen=True, slots=True)
class OwnerRuntimeActivation:
    owner_activation_id: str
    owner_key: str
    scope: str
    scope_key: str
    generation: int
    owner_binding_set_stamp: str
    startup_or_profile_epoch: int
    runtime_set_hashes: tuple[str, ...]


_AUTHORITY_SENTINEL = object()


def runtime_instance_id_for(
    prepared_set: PreparedRuntimeSet,
    instance: PreparedRuntimeInstanceSpec,
) -> str:
    if instance not in prepared_set.instances:
        raise ValueError("runtime instance is not in prepared set")
    return _hash(
        {
            "domain": "managed-runtime-instance-v1",
            "operation_id": prepared_set.operation_id,
            "runtime_set_hash": prepared_set.runtime_set_hash,
            "entry_id": instance.entry_id,
        }
    )


@dataclass(frozen=True, slots=True)
class RuntimeLaunchAuthorization:
    operation_id: str
    runtime_set_hash: str
    entry_id: str
    runtime_instance_id: str
    launch_revocation_epoch: int
    nonce: str
    _authority: object = field(repr=False, compare=False)

    @classmethod
    def issue(
        cls,
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
    ) -> "RuntimeLaunchAuthorization":
        if instance not in prepared_set.instances:
            raise ValueError("runtime instance is not in prepared set")
        nonce = secrets.token_hex(16)
        return cls(
            operation_id=prepared_set.operation_id,
            runtime_set_hash=prepared_set.runtime_set_hash,
            entry_id=instance.entry_id,
            runtime_instance_id=runtime_instance_id_for(
                prepared_set, instance
            ),
            launch_revocation_epoch=prepared_set.launch_revocation_epoch,
            nonce=nonce,
            _authority=_AUTHORITY_SENTINEL,
        )

    def validate(
        self,
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
    ) -> None:
        if self._authority is not _AUTHORITY_SENTINEL:
            raise ValueError("runtime launch authorization is not host-issued")
        if (
            self.operation_id != prepared_set.operation_id
            or self.runtime_set_hash != prepared_set.runtime_set_hash
            or self.entry_id != instance.entry_id
            or self.runtime_instance_id
            != runtime_instance_id_for(prepared_set, instance)
            or self.launch_revocation_epoch
            != prepared_set.launch_revocation_epoch
        ):
            raise ValueError("runtime launch authorization is stale")

    @property
    def authorization_hash(self) -> str:
        return _hash(
            {
                "operation_id": self.operation_id,
                "runtime_set_hash": self.runtime_set_hash,
                "entry_id": self.entry_id,
                "runtime_instance_id": self.runtime_instance_id,
                "launch_revocation_epoch": self.launch_revocation_epoch,
                "nonce": self.nonce,
            }
        )


@dataclass(frozen=True, slots=True)
class RuntimeStartedAck:
    operation_id: str
    runtime_set_hash: str
    entry_id: str
    runtime_instance_id: str
    adapter_identity: str
    start_identity: Mapping[str, Any]
    acknowledged_at: float


@dataclass(frozen=True, slots=True)
class RuntimeNotStarted:
    operation_id: str
    entry_id: str
    reason_code: str
    runtime_instance_id: str = ""


@dataclass(frozen=True, slots=True)
class RuntimeStartUnknown:
    operation_id: str
    entry_id: str
    reason_code: str
    runtime_instance_id: str = ""


RuntimeStartResult = RuntimeStartedAck | RuntimeNotStarted | RuntimeStartUnknown


@dataclass(frozen=True, slots=True)
class RuntimeHealthOutcome:
    operation_id: str
    entry_id: str
    healthy: bool
    outcome_hash: str
    detail: Mapping[str, Any] = field(default_factory=dict)


class PreparedRuntimeAdapter(Protocol):
    adapter_id: str
    adapter_fingerprint: str

    async def start_prepared(
        self,
        spec: PreparedRuntimeInstanceSpec,
        authorization: RuntimeLaunchAuthorization,
    ) -> RuntimeStartResult: ...

    async def await_health(
        self,
        spec: PreparedRuntimeInstanceSpec,
        ack: RuntimeStartedAck,
    ) -> RuntimeHealthOutcome: ...

    async def abort(
        self,
        spec: PreparedRuntimeInstanceSpec,
        start: RuntimeStartResult | None,
    ) -> None: ...


class RuntimeSetLedgerPort(Protocol):
    """Durable operations required from the future CapabilityStore migration."""

    async def next_owner_runtime_generation(
        self, owner_key: str, scope: str, scope_key: str
    ) -> int: ...

    async def create_owner_runtime_activation(
        self, activation: OwnerRuntimeActivation
    ) -> OwnerRuntimeActivation: ...

    async def create_runtime_set(
        self, prepared_set: PreparedRuntimeSet
    ) -> PreparedRuntimeSet: ...

    async def claim_runtime_instance(
        self,
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
        authorization: RuntimeLaunchAuthorization,
    ) -> str: ...

    async def record_start_result(
        self,
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
        result: RuntimeStartResult,
    ) -> None: ...

    async def get_runtime_instance_start_result(
        self,
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
    ) -> RuntimeStartResult | None: ...

    async def record_health_outcome(
        self,
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
        outcome: RuntimeHealthOutcome,
    ) -> None: ...

    async def assert_set_health_passed(
        self, operation_id: str, runtime_set_hash: str
    ) -> None: ...

    async def mark_set_activated(
        self, operation_id: str, runtime_set_hash: str
    ) -> None: ...

    async def mark_set_aborted(
        self, operation_id: str, runtime_set_hash: str, reason_code: str
    ) -> None: ...


class RuntimeProjectionActivationPort(Protocol):
    async def activate_prepared_projection(
        self, prepared_set: PreparedRuntimeSet
    ) -> Mapping[str, Any]: ...


class CapabilityRuntimeSetCoordinator:
    def __init__(
        self,
        *,
        ledger: RuntimeSetLedgerPort,
        adapters: Mapping[str, PreparedRuntimeAdapter],
        projection: RuntimeProjectionActivationPort,
    ) -> None:
        self._ledger = ledger
        self._adapters = dict(adapters)
        self._projection = projection

    async def prepare_owner_runtime_activation(
        self,
        *,
        owner_key: str,
        scope: str,
        scope_key: str,
        owner_binding_set_stamp: str,
        startup_or_profile_epoch: int,
        operation_id: str,
        instances: Sequence[PreparedRuntimeInstanceSpec],
        launch_revocation_epoch: int,
    ) -> tuple[OwnerRuntimeActivation, PreparedRuntimeSet]:
        generation = await self._ledger.next_owner_runtime_generation(
            owner_key, scope, scope_key
        )
        prepared = PreparedRuntimeSet(
            operation_id=operation_id,
            purpose="owner_rehydrate",
            owner_key=owner_key,
            scope=scope,
            scope_key=scope_key,
            owner_runtime_activation_generation=generation,
            owner_binding_set_stamp=owner_binding_set_stamp,
            launch_revocation_epoch=launch_revocation_epoch,
            instances=tuple(instances),
        )
        activation = OwnerRuntimeActivation(
            owner_activation_id=_hash(
                {
                    "owner_key": owner_key,
                    "scope": scope,
                    "scope_key": scope_key,
                    "generation": generation,
                    "startup_or_profile_epoch": startup_or_profile_epoch,
                }
            ),
            owner_key=owner_key,
            scope=scope,
            scope_key=scope_key,
            generation=generation,
            owner_binding_set_stamp=owner_binding_set_stamp,
            startup_or_profile_epoch=startup_or_profile_epoch,
            runtime_set_hashes=(prepared.runtime_set_hash,),
        )
        activation = await self._ledger.create_owner_runtime_activation(activation)
        prepared = await self._ledger.create_runtime_set(prepared)
        return activation, prepared

    async def start_prepared_runtime_instance(
        self,
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
        authorization: RuntimeLaunchAuthorization,
        *,
        ack_timeout_seconds: float = 5.0,
    ) -> RuntimeStartResult:
        authorization.validate(prepared_set, instance)
        adapter = self._adapter(instance)
        await self._ledger.claim_runtime_instance(
            prepared_set, instance, authorization
        )
        try:
            async with asyncio.timeout(ack_timeout_seconds):
                result = await adapter.start_prepared(instance, authorization)
        except TimeoutError:
            result = RuntimeStartUnknown(
                prepared_set.operation_id,
                instance.entry_id,
                "start_ack_timeout",
                authorization.runtime_instance_id,
            )
        except asyncio.CancelledError:
            unknown = RuntimeStartUnknown(
                prepared_set.operation_id,
                instance.entry_id,
                "start_cancelled_outcome_unknown",
                authorization.runtime_instance_id,
            )
            await asyncio.shield(
                self._ledger.record_start_result(
                    prepared_set, instance, unknown
                )
            )
            raise
        self._validate_start_result(
            prepared_set, instance, authorization, result
        )
        await self._ledger.record_start_result(prepared_set, instance, result)
        return result

    async def await_prepared_runtime_instance_health(
        self,
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
    ) -> RuntimeHealthOutcome:
        start = await self._ledger.get_runtime_instance_start_result(
            prepared_set, instance
        )
        if not isinstance(start, RuntimeStartedAck):
            raise RuntimeError("runtime instance has no started ACK")
        outcome = await self._adapter(instance).await_health(instance, start)
        if (
            outcome.operation_id != prepared_set.operation_id
            or outcome.entry_id != instance.entry_id
        ):
            raise ValueError("runtime health outcome binding mismatch")
        await self._ledger.record_health_outcome(prepared_set, instance, outcome)
        return outcome

    async def activate_prepared_set(
        self, prepared_set: PreparedRuntimeSet
    ) -> Mapping[str, Any]:
        await self._ledger.assert_set_health_passed(
            prepared_set.operation_id, prepared_set.runtime_set_hash
        )
        # Projection owns the final durable activated transition while its
        # CatalogGate writer is still closed.  Opening the gate here and then
        # marking the ledger would expose a projection whose set is not
        # durably activated if the second write fails.
        return await self._projection.activate_prepared_projection(prepared_set)

    async def abort_prepared_set(
        self, prepared_set: PreparedRuntimeSet, *, reason_code: str
    ) -> None:
        for instance in reversed(prepared_set.instances):
            start = await self._ledger.get_runtime_instance_start_result(
                prepared_set, instance
            )
            await self._adapter(instance).abort(instance, start)
        await self._ledger.mark_set_aborted(
            prepared_set.operation_id,
            prepared_set.runtime_set_hash,
            _required(reason_code, "reason_code"),
        )

    @staticmethod
    def _validate_start_result(
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
        authorization: RuntimeLaunchAuthorization,
        result: RuntimeStartResult,
    ) -> None:
        if (
            result.operation_id != prepared_set.operation_id
            or result.entry_id != instance.entry_id
        ):
            raise ValueError("runtime start result binding mismatch")
        if isinstance(result, RuntimeStartedAck):
            if (
                result.runtime_set_hash != prepared_set.runtime_set_hash
                or result.runtime_instance_id
                != authorization.runtime_instance_id
            ):
                raise ValueError("runtime started ACK set identity mismatch")

    def _adapter(
        self, instance: PreparedRuntimeInstanceSpec
    ) -> PreparedRuntimeAdapter:
        try:
            adapter = self._adapters[instance.adapter_id]
        except KeyError as exc:
            raise RuntimeError(
                f"runtime adapter unavailable: {instance.adapter_id}"
            ) from exc
        if adapter.adapter_fingerprint != instance.adapter_fingerprint:
            raise RuntimeError("runtime adapter fingerprint drift")
        return adapter


__all__ = [
    "CapabilityRuntimeSetCoordinator",
    "OwnerRuntimeActivation",
    "PreparedRuntimeAdapter",
    "PreparedRuntimeInstanceSpec",
    "PreparedRuntimeSet",
    "RuntimeHealthOutcome",
    "RuntimeLaunchAuthorization",
    "RuntimeNotStarted",
    "RuntimeProjectionActivationPort",
    "RuntimeSetLedgerPort",
    "RuntimeStartedAck",
    "RuntimeStartResult",
    "RuntimeStartUnknown",
    "runtime_instance_id_for",
]
