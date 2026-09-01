# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Resolve the Context OS compaction model without silent model fallback."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CompressionModelCandidate:
    provider_id: str
    model_id: str
    provider: Any


@dataclass(frozen=True)
class CompressionModelResolution:
    requested_model: str
    candidates: tuple[CompressionModelCandidate, ...]
    source: str

    @property
    def resolved_provider(self) -> str:
        return self.candidates[0].provider_id

    @property
    def resolved_model(self) -> str:
        return self.candidates[0].model_id


class CompressionModelUnavailable(RuntimeError):
    pass


class CompressionModelResolver:
    def resolve(
        self,
        requested_model: str,
        *,
        session_chain: Iterable[CompressionModelCandidate],
        provider_catalog: Iterable[CompressionModelCandidate] = (),
    ) -> CompressionModelResolution:
        requested = (requested_model or "follow_session").strip()
        chain = tuple(session_chain)
        if requested == "follow_session":
            if not chain:
                raise CompressionModelUnavailable("compression_model_unavailable")
            return CompressionModelResolution(requested, chain, "follow_session")

        ordered = (*chain, *tuple(provider_catalog))
        seen: set[tuple[str, str]] = set()
        for candidate in ordered:
            key = (candidate.provider_id, candidate.model_id)
            if key in seen:
                continue
            seen.add(key)
            if candidate.model_id == requested:
                # Explicit selection is a single candidate: transport retry is
                # allowed inside the provider, cross-model fallback is not.
                return CompressionModelResolution(requested, (candidate,), "explicit")
        raise CompressionModelUnavailable(
            f"compression_model_unavailable:{requested}"
        )
