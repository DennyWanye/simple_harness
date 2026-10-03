# SPDX-License-Identifier: Apache-2.0
"""The production current-read authority keys its ACCESS witness per source.

Host real-model runs 8 and 9 (2026-09-23, Grok lane): every TASK_CONTENT review
preparation failed with RECHECK_REQUIRED out of ``_merge_reads`` because
``FixedPrincipalAuthority`` issued one ACCESS key per (caller, mission, use) with a
per-source fingerprint, so any read set with two or more sources conflicted with
itself. The seam fixture's authority always keyed per source, which is why no
seam caught it.

2026-10-03（HTN 补齐阶段 A′）：世界换成产品同形部署（``product_world``），读的是产品部署装上的
那份当前读授权，身份是部署的认证身份；不再自拼 ``install_assurance``。
"""


from __future__ import annotations

import asyncio

from agent_orchestrator.assurance.certificates import UseIdentity
from agent_orchestrator.assurance.refs import AssuranceRef, Pin
from agent_orchestrator.orchestrator.assurance_check_use import _merge_reads
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


def test_two_sources_get_two_access_keys_and_merge_without_conflict(tmp_path):
    async def scenario():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            created = world.create({"goal": "access key per source", "success_criteria": ["one"],
                                    "idempotency_key": "assured-access-key"})
            mission = world.store.get_mission(created["mission_id"])
            principal = world.deployment.principal
            authority = world.loop._assurance_reviews.consumer.authority
            identity = UseIdentity(mission.id, "REVIEW", "review-key", "scope-x",
                                   principal.principal_id, "DISCLOSE", "root-x")
            first = authority(identity, AssuranceRef("result", Pin("result-1", 0, "1" * 64)))
            second = authority(identity, AssuranceRef("task", Pin("task-1", 1, "2" * 64)))
            assert first.access.channel == second.access.channel == "ACCESS"
            assert first.access.key != second.access.key
            assert first.access.fingerprint != second.access.fingerprint
            # The same source read twice is one observation; two sources are two.
            merged = _merge_reads([first.access, first.policy, second.access, second.policy, first.access])
            assert len([item for item in merged if item.channel == "ACCESS"]) == 2
            assert len([item for item in merged if item.channel == "POLICY"]) == 1

    asyncio.run(scenario())
