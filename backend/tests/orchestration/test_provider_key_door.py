# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-3 ③ / HA-6 ⑤, review P1-3: the provider's own key is refused wherever a person's
text enters the orchestration library — every string of a create request (stop conditions
and workspace seed included), decisions, takeovers and comments.

The key used here is deliberately *not* ``sk-`` shaped: the generic patterns cannot see it,
so only the provider-key branch of the door can refuse it.
"""

from __future__ import annotations

import json

import pytest

from agent_orchestrator.testing.word_counter import FixtureWordCounter
from deskpet.orchestration.provider import ProviderSnapshot
from deskpet.orchestration.service import (
    OrchestrationRequestError,
    OrchestrationService,
    OrchestrationSettings,
)

from ._support import notes_provider, notes_request

KEY = "dsk-host-test-0123456789abcdefXYZ"


def _service(root, principal):  # type: ignore[no-untyped-def]
    snapshot = ProviderSnapshot(
        provider_id="provider-under-test",
        base_url="https://api.example.invalid/v1",
        configured_model="model-under-test",
        requested_model="model-under-test",
        api_key=KEY,
    )
    return OrchestrationService(
        root,
        OrchestrationSettings(),
        principal=principal,
        provider=notes_provider(),
        provider_snapshot=snapshot,
        drive=False,
        # A non-DeepSeek model has no certified counter (2026-09-30: no pool, "only
        # DeepSeek"); the trusted test counter keeps this key-door test on a native pool.
        native_test_counter=FixtureWordCounter(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"goal": f"调用接口时用 {KEY}"},
        {"success_criteria": ["file:NOTES.md", f"不要写出 {KEY}"]},
        {"stop_conditions": [f"遇到 {KEY} 就停"]},
        {"workspace_seed": {"CONFIG.md": f"key = {KEY}"}},
    ],
    ids=["goal", "criteria", "stop_conditions", "workspace_seed"],
)
async def test_the_provider_key_is_refused_anywhere_in_a_create(orchestration_root, principal, overrides):
    service = _service(orchestration_root, principal)
    await service.start()
    try:
        assert service.status()["available"] is True
        with pytest.raises(OrchestrationRequestError) as refused:
            service.create_mission(notes_request("k-key", **overrides))
        assert refused.value.code == "secret_rejected"
        assert KEY not in str(refused.value)
        assert service.list_missions() == []
        assert KEY not in json.dumps(service.status(), ensure_ascii=False, default=str)
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("call", ["decide", "takeover", "comment"])
async def test_the_provider_key_is_refused_in_decisions_takeovers_and_comments(
    orchestration_root, principal, call
):
    service = _service(orchestration_root, principal)
    await service.start()
    try:
        with pytest.raises(OrchestrationRequestError) as refused:
            if call == "decide":
                service.decide("review-any", "review_fail", note=f"对照 {KEY}")
            elif call == "takeover":
                service.takeover("task-any", "stop", basis=f"对照 {KEY}")
            else:
                service.comment("mission-any", f"对照 {KEY}")
        # refused at the door, before the facade looks the object up
        assert refused.value.code == "secret_rejected"
    finally:
        await service.close()
