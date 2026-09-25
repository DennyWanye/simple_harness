# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""ARP-EXEC-1.1.1 RP-E3: the Host runs pools on the native runtime plane.

Seam tests only (RP-E test plan layer ②): the native pools are assembled beside the
legacy ones, one runtime-plane read, one write and its replay go through the control
channel, and the Host links a Skill evaluation to its original Assurance Mission.  The
counter is a certified fixture standing in for the DeepSeek one (trusted composition).
"""

from __future__ import annotations

import asyncio

import pytest

from deskpet.orchestration.handlers import handle
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings
from simple_harness.agents.arp.meter import MeterBinding, model_limits
from simple_harness.agents.arp.pins import Pin
from simple_harness.agents.arp.profile import default_policy
from simple_harness.agents.arp.strict import digest

from ._support import notes_provider, notes_request


class ExactWordCounter:
    """One token per whitespace word: exact for the scripted provider, certified below."""

    fingerprint = "host-test-exact-words:v1"
    count_mode = "EXACT"
    requires_prior_output_reserve = False
    tool_schema_mode = "legacy"
    bound_protocol = "host-test-exact-words-v1"

    def count_text(self, text: str) -> int:
        return len(text.split())

    def estimate_input_tokens(self, request) -> int:  # type: ignore[no-untyped-def]
        return sum(self.count_text(m.content if isinstance(m.content, str) else "") for m in request.messages)

    def meter_factory(self, counter, *, input_limit_tokens, max_output_tokens, prior_reserve=None):  # type: ignore[no-untyped-def]
        limits = model_limits(model="agent-model", tokenizer=counter, input_limit_tokens=input_limit_tokens, max_output_tokens=max_output_tokens, provider_id="host-test")
        return MeterBinding(tokenizer=counter, model_limits=limits, certification_ref=Pin("receipt", "meter-certification:host-test-exact-words", 0, digest({"rule": "one token per word"})), prior_reserve=prior_reserve)


def _service(root, principal, **settings):  # type: ignore[no-untyped-def]
    return OrchestrationService(root, OrchestrationSettings(**settings), provider=notes_provider(), principal=principal, drive=False, native_test_counter=ExactWordCounter())


def _request(verb: str, *, subject_id: str, payload, command_id=None, expected_revision=None, request_id="r"):  # type: ignore[no-untyped-def]
    return {"request_id": request_id, "schema_version": 1, "verb": verb, "command_id": command_id, "subject_id": subject_id,
            "expected_revision": expected_revision, "cursor": None, "limit": 10, "payload_ref": None, "payload": payload}


@pytest.mark.asyncio
async def test_native_pools_answer_runtime_plane_reads_writes_and_replays(orchestration_root, principal):
    service = _service(orchestration_root, principal)
    await service.start()
    try:
        status = service.status()
        assert status["state"] == "available", status["reason"]
        native = status["native_plane"]
        assert native["available"] is True and {p["profile_id"] for p in native["profiles"]} == {"deepseek-native-256k-v1", "deepseek-native-512k-v1"}
        assert all(p["count_mode"] == "EXACT" for p in native["profiles"])
        # 2026-09-25 条目 6：每个池带后台循环健康行（建好即为 5 个循环，全部 0 次失败）
        assert all({row["loop"] for row in p["background"]} == {"index", "draining", "recall", "tool_probe", "reap"} for p in native["profiles"])
        assert all(row["consecutive_failures"] == 0 for p in native["profiles"] for row in p["background"])
        assert status["default_context_profile_id"] == "deepseek-native-256k-v1"
        rows = {p["profile_id"]: p for p in status["context_profiles"]}
        assert rows["deepseek-native-256k-v1"]["native_plane"] is True and rows["deepseek-context-256k-v1"]["native_plane"] is False
        pool = service._orchestrator.assembled.pool("deepseek-native-256k-v1")
        assert pool.runtime.arp.protocol == "ARP_V1_1_1" and pool.bridge.native_plane is True
        assert service._orchestrator.assembled.pool("default").bridge.native_plane is False
        # Each pool has its own Assurance acceptance reader, bound to that pool's own Skill
        # lifecycle and to the orchestrator store (review finding: a shared reader answered
        # for the last pool built).
        readers = service._native.acceptances
        assert set(readers) == {"deepseek-native-256k-v1", "deepseek-native-512k-v1"}
        for profile_id, reader in readers.items():
            assert reader.store is service._orchestrator.store
            assert reader.dispatches.__self__ is service._orchestrator.assembled.pool(profile_id).runtime.arp.lifecycle
        # The deployment authorization port allows the deployment's tools and the Assurance
        # reviewers' read-only evidence tools, and denies everything else by policy name.
        from types import SimpleNamespace

        from simple_harness.runtime.ports import AuthorizationRequest
        port = service._native.authorization

        async def decision(name: str) -> str:
            return (await port.request_authorization(AuthorizationRequest(SimpleNamespace(name=name, arguments={}), run_id="r"))).decision
        assert await decision("workspace_read_file") == "allow"
        assert await decision("assurance_find_evidence") == "allow" and await decision("assurance_read_evidence") == "allow"
        assert await decision("shell_exec") == "deny"
        namespace = pool.runtime.arp.catalogue.namespace_id
        assert {p["profile_id"]: p["catalogue_namespace_id"] for p in native["profiles"]}["deepseek-native-256k-v1"] == namespace

        # READ: the catalogue page of this deployment's namespace.
        read = await handle(service, "agent_runtime_request", _request(
            "agent_skills_list", subject_id=namespace, request_id="r1",
            payload={"namespace_id": namespace, "kind": "SKILL", "cursor": None, "limit": 10}))
        assert read["payload"]["ok"] is True and read["payload"]["request_id"] == "r1"
        page = read["payload"]["data"]
        assert page["error"] is None and page["request_verb"] == "agent_skills_list" and page["items"][0]["items"] == []
        # A subject outside the authenticated namespace is refused by the SDK by name.
        outside = await handle(service, "agent_runtime_request", _request(
            "agent_skills_list", subject_id="someone-else", payload={"namespace_id": "someone-else", "kind": "SKILL", "cursor": None, "limit": 10}))
        assert outside["payload"]["ok"] is True and outside["payload"]["data"]["error"]["code"] == "REF_OUTSIDE_SCOPE"

        # WRITE + REPLAY: one policy submission, then the same command again, then a reused id.
        policy = default_policy("host-test-policy")
        submit = _request("agent_context_policy_submit", subject_id=namespace, command_id="cmd-policy-1", expected_revision=0,
                          payload={"schema_version": 1, "policy": policy, "expected_policy_ref": None})
        first = (await handle(service, "agent_runtime_request", dict(submit)))["payload"]["data"]
        assert first["error"] is None and first["items"][0]["policy_ref"]["revision"] == 1 and first["command_receipt"] is not None
        again = (await handle(service, "agent_runtime_request", dict(submit)))["payload"]["data"]
        assert again["items"][0]["policy_ref"] == first["items"][0]["policy_ref"]
        assert again["command_receipt"]["command_hash"] == first["command_receipt"]["command_hash"]
        reused = dict(submit); reused["payload"] = {**submit["payload"], "policy": {**policy, "max_recall_items": 16}}
        conflict = (await handle(service, "agent_runtime_request", reused))["payload"]["data"]
        assert conflict["error"]["code"] == "EXPECTED_REVISION_MISMATCH"
        # The submission is on the native pool's own library, not the legacy one.
        from simple_harness.agents.arp import store as arp_store
        assert arp_store.latest_policy_revision(pool.runtime.uow.database.connection, "host-test-policy") == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_native_plane_off_keeps_the_legacy_pools_and_says_so(orchestration_root, principal):
    service = _service(orchestration_root, principal, native_plane="off")
    await service.start()
    try:
        status = service.status()
        assert status["native_plane"] == {"enabled": False, "available": False, "reason": "原生运行平面已关闭"}
        assert status["default_context_profile_id"] == "deepseek-context-256k-v1"
        refused = await handle(service, "agent_runtime_request", _request("agent_skills_list", subject_id="x", payload={"namespace_id": "x", "kind": "SKILL", "cursor": None, "limit": 1}))
        assert refused["payload"]["ok"] is False and refused["payload"]["error_code"] == "native_plane_unavailable"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_an_evaluation_mission_carries_the_evaluation_key_and_the_dispatch_link_is_checked(orchestration_root, principal):
    from simple_harness.agents.arp.assurance_acceptance import evaluation_mission_key

    service = _service(orchestration_root, principal)
    await service.start()
    try:
        evaluation = Pin("evaluation", "skill-eval:demo@1:0123456789abcdef:cmd-trial-1", 1, digest({"demo": 1}))
        body = {"request_id": "m1", "evaluation_ref": evaluation.to_json(), **{k: v for k, v in notes_request("ignored").items() if k != "idempotency_key"}}
        created = (await handle(service, "agent_skill_evaluation_mission", dict(body)))["payload"]
        assert created["ok"] is True and created["data"]["created"] is True
        assert created["data"]["idempotency_key"] == evaluation_mission_key(evaluation)
        mission_id = created["data"]["mission_id"]
        replay = (await handle(service, "agent_skill_evaluation_mission", dict(body)))["payload"]["data"]
        assert replay["created"] is False and replay["mission_id"] == mission_id
        mission = service._orchestrator.store.get_mission(mission_id)
        assert mission.idempotency_key == evaluation_mission_key(evaluation)
        bad_kind = (await handle(service, "agent_skill_evaluation_mission", {**body, "evaluation_ref": Pin("skill", "x", 1, digest({})).to_json()}))["payload"]
        assert bad_kind["ok"] is False and bad_kind["error_code"] == "invalid_request"

        # The evaluation Mission runs on the native pool: once the person approves the
        # completion spec (the Host's planning gate), the loop plans it through an Agent the
        # native plane created from the claimed dispatch intent, and the first provider
        # request is metered by the certified counter.  (The scripted provider knows no
        # synthesizer role, so planning itself stops there: fixture migration is the
        # Assurance handoff's item ③, not this seam.)
        from agent_orchestrator.contracts.semantic_base import TypedRef, TypedRefKind
        from agent_orchestrator.storage.htn_store import HtnStore

        loop = service._orchestrator
        requirements = HtnStore(loop.store).latest_requirements_revision(mission_id)
        ref = TypedRef(kind=TypedRefKind.REQUIREMENTS, id=str(requirements.revision_id), revision=requirements.revision, content_hash=requirements.content_hash())
        approved = service.approve_operation_completion_spec({
            "mission_id": mission_id, "command_id": "confirm-eval-1", "expected_requirements_ref": ref.to_json(),
            "proposal": {"schema_version": 1, "mission_id": mission_id,
                         "requirements_ref": {"id": ref.id, "revision": ref.revision, "content_hash": ref.content_hash},
                         "mode": "CONTENT_ONLY", "content_criterion_ids": [c.criterion_id for c in requirements.criteria], "effects": []},
        })
        assert approved["authority"]["kind"] == "USER_CONFIRMED"
        await asyncio.wait_for(loop.run(max_cycles=40), timeout=240)
        assert loop.store.get_mission(mission_id).status.value == "PLANNING"
        pool = loop.assembled.pool("deepseek-native-256k-v1")
        connection = pool.runtime.uow.database.connection
        sessions = connection.execute("SELECT agent_id, state FROM arp_agent_sessions").fetchall()
        assert len(sessions) == 1 and sessions[0][1] == "ACTIVE"
        intents = connection.execute("SELECT state, original_receipt_ref_json FROM arp_creation_intents").fetchall()
        assert len(intents) == 1 and intents[0][0] == "BOUND" and "dispatch-intent:intent-plan-" in intents[0][1]
        metered = connection.execute("SELECT input_charge, input_budget FROM arp_context_requests").fetchall()
        assert len(metered) == 1 and 0 < metered[0][0] < metered[0][1]
        assert loop.assembled.pool("default").runtime.uow.database.connection.execute("SELECT COUNT(*) FROM provider_invocations").fetchone()[0] == 0
        tasks = loop.store.list_tasks(mission_id)
        assert tasks == []  # the fixture provider has no synthesizer script (see above)

        # Dispatch link: an unrelated Mission is refused and a missing task is refused (the
        # success path needs a planned task: it is exercised on the real-model runs).
        other = service.create_mission(notes_request("plain-notes"))["mission_id"]
        link = {"request_id": "d1", "evaluation_ref": evaluation.to_json(), "mission_id": other, "task_id": "t", "command_id": "cmd-dispatch-1"}
        wrong = (await handle(service, "agent_skill_evaluation_dispatch", dict(link)))["payload"]
        assert wrong["ok"] is False and wrong["error_code"] == "invalid_request"
        no_task = (await handle(service, "agent_skill_evaluation_dispatch", {**link, "mission_id": mission_id}))["payload"]
        assert no_task["ok"] is False and no_task["error_code"] == "not_found"
    finally:
        await service.close()
