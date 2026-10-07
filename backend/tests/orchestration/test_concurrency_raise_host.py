# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""NEXT-TG-1.0 §9 多任务并发: raising the model-call slots keeps an existing library running.

2026-09-28 the Host tried the SDK's default of two slots and the isolated library would not
start ("provider admission identity differs from a persisted intent"): the slot count was
hashed into every frozen request.  SDK opt.85 leaves it out of the identity and still accepts
the earlier form.  Here a library freezes a request under one slot and restarts under two.
"""

from __future__ import annotations

import pytest

from deskpet.orchestration.service import OrchestrationSettings

from ._support import notes_request
from .test_native_plane_host import _service


@pytest.mark.asyncio
async def test_a_library_frozen_under_one_slot_starts_under_two(orchestration_root, principal):
    service = _service(orchestration_root, principal, max_concurrency=1, max_concurrent_model_calls=1)
    await service.start()
    try:
        mission_id = service.create_mission(notes_request("slots-1"))["mission_id"]
        loop = service._orchestrator
        # 2026-10-03（SDK 3f34b51e8）起规划闸门：现行要求未确认不开规划轮。按自动权限模式，
        # 由 Host 职责确认纯内容要求（同产品每轮职责），再冻结第一条规划请求。
        assert service._duties.auto_confirm_content_completion(auto=True) == 1
        assert await loop._try_planner_intent(mission_id, ordinal=1)
        frozen = [i for i in loop.store.list_intents("PENDING", "CLAIMED") if i.mission_id == mission_id]
        assert frozen and frozen[0].config.get("provider_admission_fingerprint")
        before = frozen[0].config["provider_admission_fingerprint"]
    finally:
        await service.close()

    service = _service(orchestration_root, principal)  # the new default: two slots
    assert OrchestrationSettings().max_concurrent_model_calls == 2
    await service.start()
    try:
        status = service.status()
        assert status["state"] == "available", status["reason"]
        assert service._config.max_concurrent_model_calls == 2
        loop = service._orchestrator
        intent = loop.store.get_intent(frozen[0].intent_id)
        assert intent.config["provider_admission_fingerprint"] == before  # nothing rewritten
        profile = intent.config.get("runtime_profile_id") or "default"
        guard = loop._admission_for(profile)
        # the slot count is no longer part of the identity: same identity under two slots
        assert guard is not None and guard.accepts(before) and guard.fingerprint == before
    finally:
        await service.close()
