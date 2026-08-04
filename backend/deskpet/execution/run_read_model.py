"""Immutable public read-model contracts for Harness Inspector schema V3."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any, Mapping


JsonObject = Mapping[str, Any]


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class PublicFactEnvelope:
    source: str
    stable_id: str
    root_run_id: str
    kind: str
    public_payload: JsonObject
    created_at: float
    workflow_event_id: str | None = None
    invocation_id: str | None = None
    source_seq: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "public_payload", _freeze_json(self.public_payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "stable_id": self.stable_id,
            "root_run_id": self.root_run_id,
            "kind": self.kind,
            "public_payload": _thaw_json(self.public_payload),
            "created_at": self.created_at,
            "workflow_event_id": self.workflow_event_id,
            "invocation_id": self.invocation_id,
            "source_seq": self.source_seq,
        }


@dataclass(frozen=True, slots=True)
class ReadSourceCutV1:
    captured_at: float
    data_version: int | None
    complete: bool


@dataclass(frozen=True, slots=True)
class ReadCutV1:
    workflow: ReadSourceCutV1
    state: ReadSourceCutV1
    unmatched_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProjectionTotalsV1:
    workflow_facts: int = 0
    content_facts: int = 0
    provider_details: int = 0
    tool_details: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProjectionManifestV1:
    projection_id: str
    session_id: str
    root_run_id: str
    facts: tuple[PublicFactEnvelope, ...]
    aggregate_outcome: JsonObject
    semantic_phases: tuple[JsonObject, ...]
    detail_rows: Mapping[str, tuple[JsonObject, ...]]
    totals: ProjectionTotalsV1
    projection_complete: bool
    diagnostics: tuple[str, ...]
    read_cut: ReadCutV1
    created_at: float
    expires_at: float
    schema_version: str = "3"

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", tuple(self.facts))
        object.__setattr__(self, "aggregate_outcome", _freeze_json(self.aggregate_outcome))
        object.__setattr__(
            self,
            "semantic_phases",
            tuple(_freeze_json(item) for item in self.semantic_phases),
        )
        object.__setattr__(
            self,
            "detail_rows",
            MappingProxyType(
                {
                    str(key): tuple(_freeze_json(item) for item in rows)
                    for key, rows in self.detail_rows.items()
                }
            ),
        )
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))


@dataclass(frozen=True, slots=True)
class PublicRunSnapshotV3:
    projection_id: str
    session_id: str
    root_run_id: str
    aggregate_outcome: JsonObject
    semantic_phases: tuple[JsonObject, ...]
    totals: ProjectionTotalsV1
    projection_complete: bool
    diagnostics: tuple[str, ...]
    read_cut: ReadCutV1
    schema_version: str = "3"

    def __post_init__(self) -> None:
        object.__setattr__(self, "aggregate_outcome", _freeze_json(self.aggregate_outcome))
        object.__setattr__(
            self,
            "semantic_phases",
            tuple(_freeze_json(item) for item in self.semantic_phases),
        )
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "projection_id": self.projection_id,
            "session_id": self.session_id,
            "root_run_id": self.root_run_id,
            "aggregate_outcome": _thaw_json(self.aggregate_outcome),
            "semantic_phases": _thaw_json(self.semantic_phases),
            "totals": self.totals.to_dict(),
            "projection_complete": self.projection_complete,
            "diagnostics": list(self.diagnostics),
            "read_cut": self.read_cut.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PublicDetailPageV1:
    projection_id: str
    query_kind: str
    items: tuple[JsonObject, ...]
    total: int
    next_cursor: str | None
    projection_complete: bool
    schema_version: str = "3"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "items", tuple(_freeze_json(item) for item in self.items)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "projection_id": self.projection_id,
            "query_kind": self.query_kind,
            "items": _thaw_json(self.items),
            "total": self.total,
            "next_cursor": self.next_cursor,
            "projection_complete": self.projection_complete,
        }


class PublicReadError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


DETAIL_QUERY_KINDS = frozenset(
    {"workflow_facts", "content_facts", "provider_details", "tool_details"}
)


__all__ = [
    "DETAIL_QUERY_KINDS",
    "ProjectionManifestV1",
    "ProjectionTotalsV1",
    "PublicDetailPageV1",
    "PublicFactEnvelope",
    "PublicReadError",
    "PublicRunSnapshotV3",
    "ReadCutV1",
    "ReadSourceCutV1",
]
