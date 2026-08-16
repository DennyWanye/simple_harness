# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Exact product ports for the detached DeepResearch definition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from simple_harness.contracts import JsonValue
from simple_harness.workflow import WorkflowContext


@runtime_checkable
class ResearchLLMPort(Protocol):
    async def complete(self, prompt: str, *, operation_key: str) -> str: ...


@runtime_checkable
class ResearchSearchPort(Protocol):
    async def search(
        self, query: str, *, operation_key: str
    ) -> list[dict[str, JsonValue]]: ...


@runtime_checkable
class ResearchFetchPort(Protocol):
    async def fetch(self, url: str, *, operation_key: str) -> str: ...


@runtime_checkable
class ResearchArtifactPort(Protocol):
    async def save_report(
        self,
        *,
        title: str,
        report_markdown: str,
        report_sha256: str,
        operation_key: str,
    ) -> dict[str, JsonValue]: ...


@runtime_checkable
class ResearchBlobPort(Protocol):
    async def put(
        self, payload: bytes, *, media_type: str, operation_key: str
    ) -> dict[str, JsonValue]: ...

    async def get(self, blob_ref: str) -> bytes: ...


def _typed(context: WorkflowContext, name: str, expected: type[object]) -> object:
    value = context.port(name)
    if not isinstance(value, expected):
        raise TypeError(f"workflow port {name} does not satisfy {expected.__name__}")
    return value


@dataclass(frozen=True, slots=True)
class ResearchPorts:
    llm: ResearchLLMPort
    search: ResearchSearchPort
    fetch: ResearchFetchPort
    artifact: ResearchArtifactPort
    blob: ResearchBlobPort

    @classmethod
    def from_context(cls, context: WorkflowContext) -> "ResearchPorts":
        return cls(
            llm=_typed(context, "llm", ResearchLLMPort),  # type: ignore[arg-type]
            search=_typed(context, "search", ResearchSearchPort),  # type: ignore[arg-type]
            fetch=_typed(context, "fetch", ResearchFetchPort),  # type: ignore[arg-type]
            artifact=_typed(context, "artifact", ResearchArtifactPort),  # type: ignore[arg-type]
            blob=_typed(context, "blob", ResearchBlobPort),  # type: ignore[arg-type]
        )


__all__ = (
    "ResearchArtifactPort",
    "ResearchBlobPort",
    "ResearchFetchPort",
    "ResearchLLMPort",
    "ResearchPorts",
    "ResearchSearchPort",
)
