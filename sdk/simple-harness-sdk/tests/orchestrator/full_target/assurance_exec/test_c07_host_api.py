# SPDX-License-Identifier: Apache-2.0
"""C07 (SDK half): the fixed-caller Assurance read verbs over a real Orchestrator.

The product's deployment (``product_world``: native root, fixed principal, factory,
four consumers, TaskGraph), real assured Missions created the product way, real
facade. No model is asked (the loop is never run), no Host process: the Host
route/UI half of C07 lives in the Host tests.

2026-10-03（HTN 补齐阶段 A′）：不再自拼"按任务键选不选保证通道"的部署。偏离：
* 产品里每个任务都走保证通道，"没选保证通道的任务 → PROFILE_UNBOUND"这个探针建不出来，删掉；
  PROFILE_UNBOUND 仍由"别的认证身份""别的租户"两条覆盖。"拿别的任务的游标"改用第二个保证通道
  任务，按产品实际的拒绝码断言。
* 移动事件头不再手插通知事件（产品自己才写的事件），改用产品真会发生的两步：部署代签内容
  完成映射、开始规划（``begin_planning``）。
"""

from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.api.assurance import ITEM_KINDS, AssuranceReadError
from agent_orchestrator.api.facade import FacadeError, MissionControlV1
from agent_orchestrator.assurance.contracts import ContractViolation, validate
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.testing.product_world import TENANT, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

HOST_FINGERPRINT = "ab" * 32  # product_world's deployment fingerprint


def _create(world, key: str):  # type: ignore[no-untyped-def]
    created = world.create({"goal": "assured " + key, "idempotency_key": key,
                            "success_criteria": ["the answer file is written", "it names the fixture"]})
    return world.store.get_mission(created["mission_id"])


#: 拿别的任务的游标去读：产品实际给的拒绝码。
OTHER_MISSION_CURSOR = "SNAPSHOT_CHANGED"


def _snapshot_request(mission_id, **overrides):
    body = {
        "schema_version": 1, "request_id": "r-snap", "mission_id": mission_id,
        "view": "CURRENT", "at_event_seq": None, "cursor": None, "limit": 100,
    }
    body.update(overrides)
    return body


def _refused(call, code):
    with pytest.raises(FacadeError) as raised:
        call()
    assert raised.value.code == code, (raised.value.code, str(raised.value))
    wire = raised.value.wire  # type: ignore[attr-defined]
    validate("host-error-v1", wire)
    assert wire["code"] == code
    return wire


async def _deployment(root, checks):
    async with product_world(root / "root", LayeredScriptedProvider()) as world:
        await checks(world)


def test_snapshot_review_use_check_contracts_and_errors(tmp_path):
    async def checks(world):
        orch, store = world.loop, world.store
        control = world.control
        principal = world.deployment.principal
        assured = _create(world, "assured-1")
        other_mission = _create(world, "assured-2")

        # --- ownership, lane and contract gates -------------------------------
        _refused(lambda: control.assurance_snapshot(_snapshot_request("no-such-mission")), "NOT_FOUND")
        _refused(lambda: control.assurance_snapshot({**_snapshot_request(assured.id), "extra": 1}), "CONTRACT_INVALID")
        _refused(lambda: control.assurance_snapshot(_snapshot_request(assured.id, limit=0)), "CONTRACT_INVALID")
        _refused(lambda: control.assurance_snapshot(_snapshot_request(assured.id, at_event_seq=3)), "CONTRACT_INVALID")
        _refused(lambda: control.assurance_snapshot(_snapshot_request(assured.id, cursor="not-a-cursor")), "CONTRACT_INVALID")
        # Another authenticated identity has no installed read assembly: never a body-level override.
        other = MissionControlV1(orch, tenant_id=TENANT, principal=Principal("someone-else"))
        _refused(lambda: other.assurance_snapshot(_snapshot_request(assured.id)), "PROFILE_UNBOUND")
        stranger = MissionControlV1(orch, tenant_id="other-tenant", principal=principal)
        _refused(lambda: stranger.assurance_snapshot(_snapshot_request(assured.id)), "PROFILE_UNBOUND")

        # --- CURRENT snapshot over the real activated Mission ----------------------
        request = _snapshot_request(assured.id)
        validate("host-snapshot-request-v1", request)
        page = control.assurance_snapshot(request)
        validate("host-response-v1", page)
        assert page["mission_id"] == assured.id and page["view"] == "CURRENT"
        assert page["host_fingerprint"] == HOST_FINGERPRINT and len(page["sdk_fingerprint"]) == 64
        assert page["snapshot_seq"] == store.last_event_seq(assured.id) > 0
        assert page["next_cursor"] is None and page["truncated"] is False
        kinds = [(item["kind"], item["id"]) for item in page["items"]]
        assert kinds == sorted(kinds) and all(kind in ITEM_KINDS for kind, _ in kinds)
        by_kind = {}
        for item in page["items"]:
            by_kind.setdefault(item["kind"], []).append(item)
        assert [c["id"] for c in by_kind["CRITERION"]] == ["c-user-1", "c-user-2"]
        assert {c["history_state"] for c in by_kind["CRITERION"]} == {"UNREVIEWED"}
        assert {c["current_use"] for c in by_kind["CRITERION"]} == {"NOT_APPLICABLE"}
        assert by_kind["CLOSEOUT"] == [{
            "kind": "CLOSEOUT", "id": assured.id, "history_state": "NOT_EVALUATED",
            "current_use": "UNAVAILABLE", "reason_codes": ["NO_CLOSEOUT_ROW"],
            "evidence_count": 0, "artifact_ref": None,
        }]
        assert "REVIEW" not in by_kind and "CONTRIBUTION" not in by_kind

        # --- stable paging and SNAPSHOT_CHANGED --------------------------------------
        first = control.assurance_snapshot(_snapshot_request(assured.id, limit=1))
        validate("host-response-v1", first)
        assert first["truncated"] is True and first["next_cursor"]
        assert first["items"] == page["items"][:1]
        second = control.assurance_snapshot(_snapshot_request(assured.id, limit=1, cursor=first["next_cursor"]))
        assert second["items"] == page["items"][1:2]
        # Cursor of another Mission is refused, not silently applied.
        _refused(lambda: control.assurance_snapshot(_snapshot_request(other_mission.id, cursor=first["next_cursor"])),
                 OTHER_MISSION_CURSOR)
        # A new event moves the head (the deployment confirms the content completion mapping,
        # as the product's auto mode does): the old cursor must not be spliced onto the new state.
        head_before = store.last_event_seq(assured.id)
        assert world.deployment.duties.auto_confirm_content_completion(auto=True) >= 1
        assert store.last_event_seq(assured.id) > head_before
        _refused(lambda: control.assurance_snapshot(_snapshot_request(assured.id, limit=1, cursor=first["next_cursor"])),
                 "SNAPSHOT_CHANGED")

        # --- HISTORY view -----------------------------------------------------------
        events = store.list_events(assured.id)
        activation = next(e for e in events if e.type == "AssuranceProfileActivated")
        head = store.last_event_seq(assured.id)
        _refused(lambda: control.assurance_snapshot(_snapshot_request(
            assured.id, view="HISTORY", at_event_seq=activation.seq - 1)), "SOURCE_UNAVAILABLE")
        _refused(lambda: control.assurance_snapshot(_snapshot_request(
            assured.id, view="HISTORY", at_event_seq=head + 1)), "NOT_FOUND")
        _refused(lambda: control.assurance_snapshot(_snapshot_request(
            assured.id, view="HISTORY", at_event_seq=None)), "CONTRACT_INVALID")
        history = control.assurance_snapshot(_snapshot_request(assured.id, view="HISTORY", at_event_seq=activation.seq))
        validate("host-response-v1", history)
        assert history["view"] == "HISTORY" and history["snapshot_seq"] == activation.seq
        h_kinds = {(item["kind"], item["id"], item["history_state"]) for item in history["items"]}
        assert ("CRITERION", "c-user-1", "UNREVIEWED") in h_kinds
        assert ("CLOSEOUT", assured.id, "NOT_EVALUATED") in h_kinds
        # History pages are pinned to their seq: a later head does not change them (planning
        # begins).
        head_before = store.last_event_seq(assured.id)
        orch.commit.begin_planning(assured.id)
        assert store.last_event_seq(assured.id) > head_before
        again = control.assurance_snapshot(_snapshot_request(assured.id, view="HISTORY", at_event_seq=activation.seq))
        assert again["items"] == history["items"] and again["snapshot_seq"] == activation.seq

        # --- review -------------------------------------------------------------------
        review_request = {"schema_version": 1, "request_id": "r-rev", "mission_id": assured.id,
                          "review_key": "no-such-review", "cursor": None, "limit": 10}
        validate("host-review-request-v1", review_request)
        _refused(lambda: control.assurance_review(review_request), "NOT_FOUND")
        _refused(lambda: control.assurance_review({**review_request, "limit": 101}), "CONTRACT_INVALID")

        # --- use_check: diagnostic only, never a certificate ----------------------------
        use_request = {"schema_version": 1, "request_id": "r-use", "mission_id": assured.id,
                       "subject_ref": {"kind": "result", "pin": {"id": "result-none", "revision": 0,
                                                                  "content_hash": "0" * 64}},
                       "view": "CURRENT", "at_event_seq": None}
        validate("host-use-request-v1", use_request)
        use = control.assurance_use_check(use_request)
        validate("host-use-response-v1", use)
        assert use["decision"] == "UNAVAILABLE" and use["diagnostic_only"] is True
        assert use["certificate_ref"] is None and use["coverage"] == "INCOMPLETE"
        assert "REVIEW_RESULT_SOURCE_MISSING" in use["reason_codes"]
        assert store.connection.execute(
            "SELECT COUNT(*) FROM assurance_use_certificates WHERE mission_id=?", (assured.id,)
        ).fetchone()[0] == 0
        unsupported = control.assurance_use_check({**use_request, "subject_ref": {
            "kind": "artifact", "pin": {"id": "a", "revision": 1, "content_hash": "1" * 64}}})
        assert unsupported["decision"] == "UNAVAILABLE"
        assert unsupported["reason_codes"] == ["SUBJECT_KIND_UNSUPPORTED:artifact"]
        _refused(lambda: control.assurance_use_check({**use_request, "view": "HISTORY", "at_event_seq": 1}),
                 "SOURCE_UNAVAILABLE")
        _refused(lambda: control.assurance_use_check({**use_request, "subject_ref": {"kind": "bogus"}}),
                 "CONTRACT_INVALID")
        # The read verbs never touch the four consumers' queue.
        assert not world.notices

    asyncio.run(_deployment(tmp_path, checks))


def test_contract_checker_rejects_drift():
    good = {"schema_version": 1, "request_id": "x", "code": "NOT_FOUND", "message": "m", "retryable": False}
    validate("host-error-v1", good)
    for bad in (
        {**good, "code": "SOMETHING_ELSE"},
        {**good, "schema_version": 2},
        {**good, "extra": True},
        {key: value for key, value in good.items() if key != "retryable"},
    ):
        with pytest.raises(ContractViolation):
            validate("host-error-v1", bad)
    with pytest.raises(ValueError):
        AssuranceReadError("NOT_A_CODE", "x")
