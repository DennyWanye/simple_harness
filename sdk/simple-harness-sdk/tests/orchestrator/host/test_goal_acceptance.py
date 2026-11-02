# SPDX-License-Identifier: Apache-2.0
"""目标成功收口要绑定可信验收记录：条件有身份、结论有验证者、证据可核；文字依据替代不了。"""

from __future__ import annotations

import asyncio

import pytest
from test_goals import make, spec
from test_session import SPEC

from agent_orchestrator.host import Session
from agent_orchestrator.host.session_ledger import SessionError

HASH = "a" * 64


def criteria():
    return ["报告包含可核验来源", {"criterion_id": "delivered", "text": "报告文件已交付"}]


def record(criterion_id, verdict="pass", **changes):
    body = {
        "criterion_id": criterion_id,
        "verdict": verdict,
        "validator_id": "agentos-report-check",
        "validator_version": "1.0",
        "method": "mechanical",
        "evidence": [{"ref": "business:report-row-17", "content_hash": HASH}],
    }
    body.update(changes)
    return body


def finish(session, command="finish", version=1, basis="已核验"):
    return session.finish_goal(
        "g", "succeeded", command_id=command, expected_version=version, basis=basis
    )


def test_basis_alone_cannot_finish_and_each_criterion_needs_a_current_pass(tmp_path):
    _, session = make(tmp_path)
    session.put_goal(spec(completion_criteria=criteria()), command_id="put", expected_version=0)
    view = session.goal("g")
    assert [c["criterion_id"] for c in view["completion_criteria"]] == ["c1", "delivered"]
    assert all(c["required"] for c in view["completion_criteria"])

    with pytest.raises(SessionError, match="acceptance_incomplete"):
        finish(session)
    assert session.goal("g")["acceptance_blockers"] == [
        "criterion_unmet:c1:missing",
        "criterion_unmet:delivered:missing",
    ]
    assert session.goal("g")["state"] == "active"

    failed = session.record_acceptance(
        "g", record("c1", "fail"), command_id="r1", expected_version=1
    )
    assert failed["status"] == "completed" and failed["record_id"] and failed["current"]
    with pytest.raises(SessionError, match="acceptance_incomplete"):
        finish(session, command="finish-2")
    assert "criterion_unmet:c1:fail" in session.goal("g")["acceptance_blockers"]

    session.record_acceptance("g", record("c1", "unknown"), command_id="r2", expected_version=1)
    with pytest.raises(SessionError, match="acceptance_incomplete"):
        finish(session, command="finish-3")
    assert "criterion_unmet:c1:unknown" in session.goal("g")["acceptance_blockers"]

    passed = session.record_acceptance("g", record("c1"), command_id="r3", expected_version=1)
    assert (
        session.record_acceptance("g", record("c1"), command_id="r3", expected_version=1) == passed
    )
    with pytest.raises(SessionError, match="idempotency_conflict"):
        session.record_acceptance(
            "g", record("c1", note="改参数"), command_id="r3", expected_version=1
        )
    with pytest.raises(SessionError, match="acceptance_incomplete"):
        finish(session, command="finish-4")
    assert session.goal("g")["acceptance_blockers"] == ["criterion_unmet:delivered:missing"]

    rows = session.acceptance_records("g")
    assert [r["record_id"] for r in rows if r["current"]] == [passed["record_id"]]
    assert [r["superseded_by"] for r in rows if not r["current"]] == [
        rows[1]["record_id"],
        passed["record_id"],
    ]
    assert all(r["source_principal_id"] == "u" and r["goal_version"] == 1 for r in rows)

    delivered = session.record_acceptance(
        "g",
        record("delivered", evidence=[{"ref": "business:file-9", "receipt_ref": "rcpt-9"}]),
        command_id="r4",
        expected_version=1,
    )
    done = finish(session, command="finish-5")
    assert done["state"] == "succeeded"
    assert sorted(done["accepted_records"]["record_ids"]) == sorted(
        [passed["record_id"], delivered["record_id"]]
    )
    assert done["accepted_records"]["digest"]
    assert finish(session, command="finish-5") == done
    assert session.goal("g")["acceptance_blockers"] == []
    # 失败收口不要求验收记录
    _, other = make(tmp_path / "other")
    other.put_goal(spec(completion_criteria=criteria()), command_id="put", expected_version=0)
    assert other.finish_goal("g", "failed", command_id="f", expected_version=1)["state"] == "failed"


def test_records_are_host_only_and_evidence_must_bind_to_this_tree(tmp_path):
    host, session = make(tmp_path)
    session.put_goal(spec(completion_criteria=criteria()), command_id="put", expected_version=0)
    asyncio.run(session.create_child("a", command_id="child"))
    with pytest.raises(SessionError, match="not_found_or_forbidden"):
        session.child("a").record_acceptance("g", record("c1"), command_id="x", expected_version=1)
    with pytest.raises(SessionError, match="stale_version"):
        session.record_acceptance("g", record("c1"), command_id="stale", expected_version=2)
    for bad in (
        record("c1", evidence=[]),
        record("c1", evidence=[{"ref": "business:x", "content_hash": "zz"}]),
        record("c1", evidence=[{"ref": "business:x"}]),
        record("c1", verdict="maybe"),
        record("nope"),
        record("c1", validator_id=""),
        {**record("c1"), "source_principal_id": "forged"},
    ):
        with pytest.raises(SessionError, match="invalid_argument|not_found_or_forbidden"):
            session.record_acceptance("g", bad, command_id="bad", expected_version=1)
    assert session.acceptance_records("g") == []
    assert session._store.rows("commands") == [] or all(
        row["kind"] != "record_acceptance" for row in session._store.rows("commands")
    )
    # 任务产物：不属于本树的任务引用拒绝；哈希不一致拒绝；一致才登记
    task = asyncio.run(session._delegations.delegate("turn", "call", {**SPEC, "child_key": "a"}))
    mid = task["mission_id"]
    original = host.snapshot

    def with_artifact(mission_id):
        detail = original(mission_id)
        detail["snapshot"]["artifacts"] = [
            {"id": "art-1", "content_hash": HASH, "logical_path": "r.md"}
        ]
        return detail

    host.snapshot = with_artifact
    with pytest.raises(SessionError, match="not_found_or_forbidden"):
        session.record_acceptance(
            "g",
            record("c1", evidence=[{"ref": "artifact:other-mission:art-1", "content_hash": HASH}]),
            command_id="foreign",
            expected_version=1,
        )
    with pytest.raises(SessionError, match="invalid_argument"):
        session.record_acceptance(
            "g",
            record("c1", evidence=[{"ref": f"artifact:{mid}:art-1", "content_hash": "b" * 64}]),
            command_id="tampered",
            expected_version=1,
        )
    ok = session.record_acceptance(
        "g",
        record("c1", evidence=[{"ref": f"artifact:{mid}:art-1", "content_hash": HASH}]),
        command_id="bound",
        expected_version=1,
    )
    assert ok["current"]
    assert "must not escape" not in str(session._store.events(0))


def test_new_goal_version_and_changed_evidence_invalidate_records(tmp_path):
    host, session = make(tmp_path)
    session.put_goal(spec(completion_criteria=["唯一条件"]), command_id="put", expected_version=0)
    task = asyncio.run(session._delegations.delegate("turn", "call", SPEC))
    mid = task["mission_id"]
    artifacts = [{"id": "art-1", "content_hash": HASH, "logical_path": "r.md"}]
    original = host.snapshot

    def with_artifact(mission_id):
        detail = original(mission_id)
        detail["snapshot"]["artifacts"] = artifacts
        return detail

    host.snapshot = with_artifact
    host.finish(mid)
    session._inbox.catch_up()
    session.exclude_result(session.inbox()[0]["event_id"], "宿主另行验收", command_id="ex")
    session.record_acceptance(
        "g",
        record("c1", evidence=[{"ref": f"artifact:{mid}:art-1", "content_hash": HASH}]),
        command_id="r1",
        expected_version=1,
    )
    # 证据在收口前被改：拒绝
    artifacts[0]["content_hash"] = "c" * 64
    with pytest.raises(SessionError, match="acceptance_incomplete"):
        finish(session)
    assert session.goal("g")["acceptance_blockers"] == ["criterion_unmet:c1:evidence_invalid"]
    artifacts[0]["content_hash"] = HASH
    # Goal 改版：旧版本记录对新版本无效
    session.put_goal(
        spec(completion_criteria=["唯一条件"], objective="扩写"),
        command_id="v2",
        expected_version=1,
    )
    with pytest.raises(SessionError, match="acceptance_incomplete"):
        finish(session, command="finish-v2", version=2)
    assert session.goal("g")["acceptance_blockers"] == ["criterion_unmet:c1:stale_version"]
    # 重开后记录与哈希都在
    reopened = Session(host, session_id="main")
    assert reopened.acceptance_records("g")[0]["goal_version"] == 1
    reopened.record_acceptance(
        "g",
        record("c1", evidence=[{"ref": f"artifact:{mid}:art-1", "content_hash": HASH}]),
        command_id="r2",
        expected_version=2,
    )
    assert finish(reopened, command="finish-v2b", version=2)["state"] == "succeeded"
