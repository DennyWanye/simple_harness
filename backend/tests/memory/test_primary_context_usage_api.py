# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""主对话读用量、读明细操作的字段校验与转发。"""

from __future__ import annotations

import pytest
from deskpet.memory.human_memory_api import _dispatch
from deskpet.memory.primary_read_model import PrimaryReadError


class _Service:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def read_primary_context_usage(self, **kwargs):
        self.calls.append(kwargs)
        return {"primary_ref": kwargs["primary_ref"], "usage": None}


@pytest.mark.asyncio
async def test_context_usage_forwards_primary_ref() -> None:
    service = _Service()
    result = await _dispatch(service, "primary.context.usage", {"primary_ref": "p-1"}, "req-1")
    assert result == {"primary_ref": "p-1", "usage": None}
    assert service.calls == [{"request_id": "req-1", "primary_ref": "p-1"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("request_body", [{}, {"primary_ref": "p-1", "session_id": "other"}])
async def test_context_usage_rejects_missing_or_extra_fields(request_body) -> None:
    service = _Service()
    with pytest.raises(PrimaryReadError, match="primary_read_request_invalid"):
        await _dispatch(service, "primary.context.usage", dict(request_body), "req-1")
    assert service.calls == []


class _BreakdownService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def read_primary_context_breakdown(self, **kwargs):
        self.calls.append(kwargs)
        return {"primary_ref": kwargs["primary_ref"], "breakdown": None}


@pytest.mark.asyncio
async def test_context_breakdown_forwards_primary_ref() -> None:
    service = _BreakdownService()
    result = await _dispatch(service, "primary.context.breakdown", {"primary_ref": "p-1"}, "req-1")
    assert result == {"primary_ref": "p-1", "breakdown": None}
    assert service.calls == [{"request_id": "req-1", "primary_ref": "p-1"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("request_body", [{}, {"primary_ref": "p-1", "session_id": "other"}])
async def test_context_breakdown_rejects_missing_or_extra_fields(request_body) -> None:
    service = _BreakdownService()
    with pytest.raises(PrimaryReadError, match="primary_read_request_invalid"):
        await _dispatch(service, "primary.context.breakdown", dict(request_body), "req-1")
    assert service.calls == []
