# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Official read-only non-Memory Context provider backed by frozen sources."""

from __future__ import annotations

from simple_harness.contracts import canonical_json
from simple_harness.runtime import ConversationContextRequest, ConversationContextResult

from .context_source import ProductContextSourceRepository


class ProductConversationContextProvider:
    def __init__(self, repository: ProductContextSourceRepository) -> None:
        self._repository = repository

    async def prepare_once(self, request: ConversationContextRequest) -> ConversationContextResult:
        payload, item_count, byte_count = await self._repository.read(
            request.source_snapshot_ref
        )
        actual_bytes = len(canonical_json(payload).encode("utf-8"))
        if actual_bytes != byte_count:
            raise RuntimeError("context_source_byte_count_conflict")
        if item_count > request.bounds.max_items or byte_count > request.bounds.max_bytes:
            raise ValueError("context_source_exceeds_sdk_bounds")
        return ConversationContextResult(
            request.preparation_id,
            request.source_snapshot_ref,
            payload,
            item_count,
            byte_count,
        )


__all__ = ("ProductConversationContextProvider",)
