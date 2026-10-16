"""Shared helpers for the split class."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any

from ..contracts import JsonValue, canonical_json

START_SNAPSHOT_KEY = "_workflow_start"


class WorkflowServiceError(RuntimeError):
    """Stable error contract consumed by IPC without framework coupling."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        current_version: int | None = None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.current_version = current_version
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class WorkflowStartIdentity:
    venue: str
    base_session_id: str
    code_session_id: str
    delivery_session_id: str
    base_epoch: int
    code_epoch: int
    request_id: str
    turn_id: str
    workflow_name: str
    logical_slot: str

    def to_dict(self) -> dict[str, JsonValue]:
        return asdict(self)

    @property
    def identity_key(self) -> str:
        return _hash_json(self.to_dict())


def _hash_json(value: JsonValue) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _json_value(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_plain(item) for item in value]
    return value
