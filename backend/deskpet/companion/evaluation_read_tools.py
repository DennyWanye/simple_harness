"""Frozen, read-only tool fixtures for companion evaluation.

This module intentionally has no import of SessionDB, the memory retriever, or
an embedder.  An evaluation adapter can only read the immutable fixture passed
to its constructor and fails closed when any production or fixture identity
drifts.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Mapping

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_MEMORY_RECALL_TOOL_NAME = "memory_recall"
_ADAPTER_ID = "evaluation.memory_recall"
_ADAPTER_VERSION = "v1"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_hash(value: object, field_name: str) -> str:
    digest = _require_text(value, field_name)
    if _SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"{field_name} must be a lowercase sha256")
    return digest


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _clone_json_object(value: Mapping[str, Any]) -> dict[str, Any]:
    cloned = json.loads(_canonical_json(_thaw_json(value)))
    if not isinstance(cloned, dict):  # pragma: no cover - defensive
        raise ValueError("expected a JSON object")
    return cloned


class EvaluationReadToolError(RuntimeError):
    """Fail-closed evaluation read failure.

    ``inconclusive`` is always true: identity or fixture failures must not be
    interpreted as a candidate regression and must never trigger live fallback.
    """

    inconclusive = True

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        message = code if not detail else f"{code}: {detail}"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class EvaluationMemoryRecordV1:
    """A deliberately small, sanitized memory record contract."""

    record_id: str
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", _require_text(self.record_id, "record_id"))
        object.__setattr__(self, "text", _require_text(self.text, "text"))
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be a mapping")
        cloned = _clone_json_object(self.metadata)
        frozen = _freeze_json(cloned)
        if not isinstance(frozen, Mapping):  # pragma: no cover - defensive
            raise ValueError("metadata must be a JSON object")
        object.__setattr__(self, "metadata", frozen)

    def to_payload(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "text": self.text,
            "metadata": _clone_json_object(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class EvaluationMemoryFixtureStoreV1:
    """Immutable case-scoped records with a content-addressed identity."""

    fixture_id: str
    fixture_ref: str
    fixture_hash: str
    records: tuple[EvaluationMemoryRecordV1, ...]
    content_state: str = "active"
    sanitized: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "fixture_id", _require_text(self.fixture_id, "fixture_id"))
        object.__setattr__(
            self,
            "fixture_ref",
            _require_text(self.fixture_ref, "fixture_ref"),
        )
        object.__setattr__(
            self,
            "fixture_hash",
            _require_hash(self.fixture_hash, "fixture_hash"),
        )
        if self.content_state not in {"active", "redacted"}:
            raise ValueError("content_state must be active or redacted")
        if not self.sanitized:
            raise ValueError("evaluation fixture must be sanitized")
        if self.content_state == "redacted" and self.records:
            raise ValueError("redacted fixture must not retain records")
        record_ids = [record.record_id for record in self.records]
        if len(set(record_ids)) != len(record_ids):
            raise ValueError("fixture record_id values must be unique")
        if record_ids != sorted(record_ids):
            raise ValueError("fixture records must use canonical record_id order")

    @staticmethod
    def _active_payload(
        fixture_id: str,
        records: Iterable[EvaluationMemoryRecordV1],
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "fixture_id": fixture_id,
            "content_state": "active",
            "sanitized": True,
            "records": [record.to_payload() for record in records],
        }

    @classmethod
    def freeze(
        cls,
        *,
        fixture_id: str,
        records: Iterable[EvaluationMemoryRecordV1],
        sanitized: bool,
    ) -> EvaluationMemoryFixtureStoreV1:
        fixture_id = _require_text(fixture_id, "fixture_id")
        if not sanitized:
            raise ValueError("evaluation fixture requires sanitized=True")
        supplied_records = tuple(records)
        if not all(
            isinstance(record, EvaluationMemoryRecordV1) for record in supplied_records
        ):
            raise ValueError("records must be EvaluationMemoryRecordV1 values")
        frozen_records = tuple(
            sorted(supplied_records, key=lambda record: record.record_id)
        )
        payload = cls._active_payload(fixture_id, frozen_records)
        fixture_hash = _canonical_hash(payload)
        return cls(
            fixture_id=fixture_id,
            fixture_ref=f"evaluation-memory-fixture:v1:{fixture_hash}",
            fixture_hash=fixture_hash,
            records=frozen_records,
            content_state="active",
            sanitized=True,
        )

    @classmethod
    def restore(
        cls,
        payload: Mapping[str, Any],
        *,
        expected_fixture_ref: str,
        expected_fixture_hash: str,
    ) -> EvaluationMemoryFixtureStoreV1:
        if not isinstance(payload, Mapping):
            raise EvaluationReadToolError("evaluation_fixture_invalid")
        cloned = _clone_json_object(payload)
        if (
            cloned.get("schema_version") != 1
            or cloned.get("content_state") != "active"
            or cloned.get("sanitized") is not True
            or not isinstance(cloned.get("records"), list)
        ):
            raise EvaluationReadToolError("evaluation_fixture_invalid")
        try:
            fixture_id = _require_text(cloned.get("fixture_id"), "fixture_id")
            records = tuple(
                EvaluationMemoryRecordV1(
                    record_id=item["record_id"],
                    text=item["text"],
                    metadata=item.get("metadata", {}),
                )
                for item in cloned["records"]
                if isinstance(item, dict)
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EvaluationReadToolError("evaluation_fixture_invalid") from exc
        if len(records) != len(cloned["records"]):
            raise EvaluationReadToolError("evaluation_fixture_invalid")
        restored = cls.freeze(
            fixture_id=fixture_id,
            records=records,
            sanitized=True,
        )
        if (
            restored.fixture_ref != expected_fixture_ref
            or restored.fixture_hash != expected_fixture_hash
        ):
            raise EvaluationReadToolError("evaluation_fixture_hash_mismatch")
        return restored

    def frozen_payload(self) -> Mapping[str, Any]:
        if self.content_state != "active":
            raise EvaluationReadToolError("evaluation_fixture_redacted")
        return self._active_payload(self.fixture_id, self.records)

    def redact(self) -> EvaluationMemoryFixtureStoreV1:
        """Return a tombstone retaining identity but no readable body."""

        return EvaluationMemoryFixtureStoreV1(
            fixture_id=self.fixture_id,
            fixture_ref=self.fixture_ref,
            fixture_hash=self.fixture_hash,
            records=(),
            content_state="redacted",
            sanitized=True,
        )

    def recall(self, *, query: str, limit: int) -> tuple[Mapping[str, Any], ...]:
        if self.content_state != "active":
            raise EvaluationReadToolError("evaluation_fixture_redacted")
        terms = frozenset(_TOKEN_RE.findall(query.casefold()))
        ranked: list[tuple[int, str, EvaluationMemoryRecordV1]] = []
        for record in self.records:
            haystack = frozenset(_TOKEN_RE.findall(record.text.casefold()))
            score = len(terms & haystack)
            if score:
                ranked.append((-score, record.record_id, record))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return tuple(
            {
                "record_id": record.record_id,
                "text": record.text,
                "metadata": _clone_json_object(record.metadata),
            }
            for _, _, record in ranked[:limit]
        )


@dataclass(frozen=True, slots=True)
class ProductionReadToolIdentityV1:
    """Exact production contract that an evaluation adapter stands in for."""

    tool_name: str
    tool_spec_ref: str
    tool_spec_hash: str
    input_schema_hash: str
    execution_build_fingerprint: str
    effect_policy_ref: str
    effect_policy_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_name", _require_text(self.tool_name, "tool_name"))
        object.__setattr__(
            self,
            "tool_spec_ref",
            _require_text(self.tool_spec_ref, "tool_spec_ref"),
        )
        object.__setattr__(
            self,
            "tool_spec_hash",
            _require_hash(self.tool_spec_hash, "tool_spec_hash"),
        )
        object.__setattr__(
            self,
            "input_schema_hash",
            _require_hash(self.input_schema_hash, "input_schema_hash"),
        )
        object.__setattr__(
            self,
            "execution_build_fingerprint",
            _require_hash(
                self.execution_build_fingerprint,
                "execution_build_fingerprint",
            ),
        )
        object.__setattr__(
            self,
            "effect_policy_ref",
            _require_text(self.effect_policy_ref, "effect_policy_ref"),
        )
        object.__setattr__(
            self,
            "effect_policy_hash",
            _require_hash(self.effect_policy_hash, "effect_policy_hash"),
        )

    @property
    def identity_hash(self) -> str:
        return _canonical_hash(
            {
                "tool_name": self.tool_name,
                "tool_spec_ref": self.tool_spec_ref,
                "tool_spec_hash": self.tool_spec_hash,
                "input_schema_hash": self.input_schema_hash,
                "execution_build_fingerprint": self.execution_build_fingerprint,
                "effect_policy_ref": self.effect_policy_ref,
                "effect_policy_hash": self.effect_policy_hash,
            }
        )


DEFAULT_EVALUATION_READ_ADAPTER_BUILD_FINGERPRINT = _canonical_hash(
    {
        "adapter_id": _ADAPTER_ID,
        "adapter_version": _ADAPTER_VERSION,
        "algorithm": "case-scoped-lexical-intersection-v1",
        "input_schema": {
            "query": "non-empty-string",
            "limit": "integer[1,50]",
        },
        "output_schema": "canonical-memory-records-v1",
    }
)


@dataclass(frozen=True, slots=True)
class EvaluationReadToolBindingV1:
    production_identity: ProductionReadToolIdentityV1
    adapter_id: str
    adapter_version: str
    adapter_build_fingerprint: str
    fixture_ref: str
    fixture_hash: str

    def __post_init__(self) -> None:
        if self.production_identity.tool_name != _MEMORY_RECALL_TOOL_NAME:
            raise ValueError("only memory_recall has an evaluation adapter")
        if self.adapter_id != _ADAPTER_ID or self.adapter_version != _ADAPTER_VERSION:
            raise ValueError("unsupported evaluation adapter identity")
        object.__setattr__(
            self,
            "adapter_build_fingerprint",
            _require_hash(
                self.adapter_build_fingerprint,
                "adapter_build_fingerprint",
            ),
        )
        object.__setattr__(
            self,
            "fixture_ref",
            _require_text(self.fixture_ref, "fixture_ref"),
        )
        object.__setattr__(
            self,
            "fixture_hash",
            _require_hash(self.fixture_hash, "fixture_hash"),
        )

    @classmethod
    def bind(
        cls,
        *,
        production_identity: ProductionReadToolIdentityV1,
        fixture: EvaluationMemoryFixtureStoreV1,
        adapter_build_fingerprint: str = DEFAULT_EVALUATION_READ_ADAPTER_BUILD_FINGERPRINT,
    ) -> EvaluationReadToolBindingV1:
        if fixture.content_state != "active":
            raise EvaluationReadToolError("evaluation_fixture_redacted")
        return cls(
            production_identity=production_identity,
            adapter_id=_ADAPTER_ID,
            adapter_version=_ADAPTER_VERSION,
            adapter_build_fingerprint=adapter_build_fingerprint,
            fixture_ref=fixture.fixture_ref,
            fixture_hash=fixture.fixture_hash,
        )

    @property
    def binding_hash(self) -> str:
        return _canonical_hash(
            {
                "production_identity_hash": self.production_identity.identity_hash,
                "adapter_id": self.adapter_id,
                "adapter_version": self.adapter_version,
                "adapter_build_fingerprint": self.adapter_build_fingerprint,
                "fixture_ref": self.fixture_ref,
                "fixture_hash": self.fixture_hash,
            }
        )


@dataclass(frozen=True, slots=True)
class EvaluationReadAuthorizationV1:
    origin: str
    binding_hash: str
    production_identity: ProductionReadToolIdentityV1
    adapter_build_fingerprint: str
    fixture_ref: str
    fixture_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "binding_hash",
            _require_hash(self.binding_hash, "binding_hash"),
        )
        object.__setattr__(
            self,
            "adapter_build_fingerprint",
            _require_hash(
                self.adapter_build_fingerprint,
                "adapter_build_fingerprint",
            ),
        )
        object.__setattr__(
            self,
            "fixture_ref",
            _require_text(self.fixture_ref, "fixture_ref"),
        )
        object.__setattr__(
            self,
            "fixture_hash",
            _require_hash(self.fixture_hash, "fixture_hash"),
        )

    @classmethod
    def for_binding(
        cls,
        binding: EvaluationReadToolBindingV1,
        *,
        origin: str = "evaluation",
    ) -> EvaluationReadAuthorizationV1:
        return cls(
            origin=origin,
            binding_hash=binding.binding_hash,
            production_identity=binding.production_identity,
            adapter_build_fingerprint=binding.adapter_build_fingerprint,
            fixture_ref=binding.fixture_ref,
            fixture_hash=binding.fixture_hash,
        )


class EvaluationReadToolAdapterV1:
    """Evaluation-only implementation of production ``memory_recall`` input."""

    def __init__(
        self,
        *,
        binding: EvaluationReadToolBindingV1,
        fixture: EvaluationMemoryFixtureStoreV1,
    ) -> None:
        self._binding = binding
        self._fixture = fixture

    @property
    def binding(self) -> EvaluationReadToolBindingV1:
        return self._binding

    def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        authorization: EvaluationReadAuthorizationV1,
    ) -> Mapping[str, Any]:
        self._authorize(authorization)
        query, limit = self._validate_arguments(arguments)
        matches = self._fixture.recall(query=query, limit=limit)
        # Canonical JSON round-trip produces a plain JSON-compatible result,
        # suitable for the same serialization path as the production tool.
        return json.loads(
            _canonical_json(
                {
                    "query": query,
                    "limit": limit,
                    "matches": matches,
                    "fixture_ref": self._fixture.fixture_ref,
                    "fixture_hash": self._fixture.fixture_hash,
                }
            )
        )

    def _authorize(self, authorization: EvaluationReadAuthorizationV1) -> None:
        if authorization.origin != "evaluation":
            raise EvaluationReadToolError("evaluation_read_origin_forbidden")
        if authorization.binding_hash != self._binding.binding_hash:
            raise EvaluationReadToolError("evaluation_read_binding_drift")
        if authorization.production_identity != self._binding.production_identity:
            raise EvaluationReadToolError("evaluation_read_production_identity_drift")
        if (
            authorization.adapter_build_fingerprint
            != self._binding.adapter_build_fingerprint
        ):
            raise EvaluationReadToolError("evaluation_read_adapter_build_drift")
        if (
            authorization.fixture_ref != self._binding.fixture_ref
            or authorization.fixture_hash != self._binding.fixture_hash
        ):
            raise EvaluationReadToolError("evaluation_read_fixture_identity_drift")
        if (
            self._fixture.fixture_ref != self._binding.fixture_ref
            or self._fixture.fixture_hash != self._binding.fixture_hash
        ):
            raise EvaluationReadToolError("evaluation_read_fixture_identity_drift")
        if self._fixture.content_state != "active":
            raise EvaluationReadToolError("evaluation_fixture_redacted")

    @staticmethod
    def _validate_arguments(arguments: Mapping[str, Any]) -> tuple[str, int]:
        if not isinstance(arguments, Mapping) or set(arguments) != {"query", "limit"}:
            raise EvaluationReadToolError("evaluation_read_schema_mismatch")
        query = arguments["query"]
        limit = arguments["limit"]
        if not isinstance(query, str) or not query.strip():
            raise EvaluationReadToolError("evaluation_read_schema_mismatch")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise EvaluationReadToolError("evaluation_read_schema_mismatch")
        return query, limit


__all__ = [
    "DEFAULT_EVALUATION_READ_ADAPTER_BUILD_FINGERPRINT",
    "EvaluationMemoryFixtureStoreV1",
    "EvaluationMemoryRecordV1",
    "EvaluationReadAuthorizationV1",
    "EvaluationReadToolAdapterV1",
    "EvaluationReadToolBindingV1",
    "EvaluationReadToolError",
    "ProductionReadToolIdentityV1",
]
