# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""第 2 批车道 N（第 1 批评估处置）：收尾定稿重读权限（A01）、收尾正文过 closeout-v1 严格校验（A17）、
已交出未收敛的动作让收尾 DRAINING（A07）。

原计划 §7.2："最终事务重读当前 epoch/权限/effect/运行集合才 READY→FINALIZED"；"实际写者未收敛 → DRAINING"。
closeout-v1：``assurance/schemas/closeout-v1.schema.json``（与 ``plans/Assurance/specs/1.1/contracts`` 同一份），
``additionalProperties:false``、``read_set`` 必填且至少一项、``root_resolution_ref`` 是 pin。

世界：产品同形部署 + 脚本化模型回复。
"""
from __future__ import annotations

import asyncio
import json
import re
from importlib import resources
from typing import Any

import pytest

from agent_orchestrator.assurance.codec import AssuranceError, decode, fingerprint
from agent_orchestrator.orchestrator.assurance_consumers import AssuranceCloseoutConsumer
from agent_orchestrator.orchestrator.assurance_final_writer import closeout_document
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

GOAL = {"goal": "写一份 NOTES.md，列出三条要点。", "success_criteria": ["file:NOTES.md"]}
EPOCHS = {"mission": 7, "environment": 1, "clock_generation": 0, "wall_high_ms": 100, "clock_state": "STABLE"}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _closeout(store: Any, mission_id: str) -> tuple[Any, dict[str, Any]]:
    row = store.connection.execute(
        "SELECT * FROM assurance_closeouts WHERE mission_id=?", (mission_id,)).fetchone()
    assert row is not None, "no closeout row"
    return row, decode(row["check_body_json"])


def _consumer(world: Any) -> AssuranceCloseoutConsumer:
    return world.deployment.assurance.consumers["CLOSEOUT"]


async def _completed(world: Any, key: str) -> str:
    mission_id = world.create({**GOAL, "idempotency_key": key})["mission_id"]
    done = await world.run_until_settled(mission_id, timeout=60)
    assert str(done.status.value) == "COMPLETED", done.final_report
    return mission_id


# ------------------------------------------------------------------ 最小 JSON Schema 校验器（只认 closeout-v1 用到的关键字）
def _schema(name: str) -> dict[str, Any]:
    return json.loads((resources.files("agent_orchestrator.assurance") / "schemas" / name).read_text("utf-8"))


def _resolve(ref: str, root: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """``$ref`` 指到的节点，连同它所在的文档（跨文件引用后，局部 ``#/$defs`` 以那份文档为根）。"""
    file_part, _, pointer = ref.partition("#")
    document = root if not file_part else _schema(file_part)
    node: Any = document
    for step in pointer.strip("/").split("/"):
        node = node[step]
    return node, document


def validate(schema: dict[str, Any], value: Any, *, root: dict[str, Any], path: str = "$") -> list[str]:
    """按 schema 校验 value，返回全部不符点（空列表 = 通过）。支持 closeout-v1 与 common 用到的关键字。"""
    if "$ref" in schema:
        target, document = _resolve(schema["$ref"], root)
        return validate(target, value, root=document, path=path)
    errors: list[str] = []
    if "oneOf" in schema:
        passing = [branch for branch in schema["oneOf"] if not validate(branch, value, root=root, path=path)]
        if len(passing) != 1:
            errors.append(f"{path}: oneOf matched {len(passing)} branches")
        return errors
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: const {schema['const']!r} != {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum")
    expected = schema.get("type")
    if expected is not None:
        ok = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
              "string": lambda v: isinstance(v, str), "null": lambda v: v is None,
              "integer": lambda v: isinstance(v, int) and not isinstance(v, bool)}[expected](value)
        if not ok:
            return [*errors, f"{path}: type {expected} != {type(value).__name__}"]
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: {value!r} !~ {schema['pattern']}")
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(set(encoded)) != len(encoded):
                errors.append(f"{path}: duplicate items")
        if "items" in schema:
            for index, item in enumerate(value):
                errors.extend(validate(schema["items"], item, root=root, path=f"{path}[{index}]"))
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", ()):
            if name not in value:
                errors.append(f"{path}: missing {name}")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    errors.append(f"{path}: unexpected key {name}")
        for name, sub in properties.items():
            if name in value:
                errors.extend(validate(sub, value[name], root=root, path=f"{path}.{name}"))
    return errors


def test_the_minimal_validator_rejects_what_closeout_v1_forbids():
    """校验器自测：缺必填、多余键、ref 当 pin、空 read_set 都要报；否则下面的严格校验没有意义。"""
    root = _schema("closeout-v1.schema.json")
    pin = {"id": "r", "revision": 0, "content_hash": "a" * 64}
    item = {"channel": "OBJECT", "key": "k", "fingerprint": "b" * 64, "coverage": "COMPLETE"}
    good = {"schema_version": 1, "mission_id": "m", "root_resolution_ref": pin, "requirements_ref": pin,
            "completion_spec_hash": "c" * 64, "state": "READY", "pending_effect_keys": [],
            "unsettled_operation_refs": [], "accounting_pending_refs": [], "dangerous_work_refs": [],
            "read_set": [item], "report_ref": None, "as_of_ms": 1, "reasons": []}
    assert validate(root, good, root=root) == []
    assert validate(root, {**good, "epochs": {}}, root=root) == ["$: unexpected key epochs"]
    assert validate(root, {k: v for k, v in good.items() if k != "read_set"}, root=root) == ["$: missing read_set"]
    assert validate(root, {**good, "read_set": []}, root=root) == ["$.read_set: fewer than 1 items"]
    assert validate(root, {**good, "root_resolution_ref": {"kind": "resolution", "pin": pin}}, root=root)
    assert validate(root, {**good, "report_ref": {"kind": "commit_receipt", "pin": pin}}, root=root) == []
    assert validate(root, {**good, "state": "DONE"}, root=root)


# ------------------------------------------------------------------ A01：定稿事务重读权限
def test_a01_the_final_transaction_rereads_the_authorization_the_closeout_rests_on(tmp_path):
    """收尾依据里有"权限"一项：依据读取时记下当前权威对每张所依赖证书身份就根结论签的 ACCESS / POLICY
    见证；定稿那次事务重读，不一致 → RECHECK_REQUIRED；读集（read_set）同样重比。没到 READY 不比。"""
    preview = {"state": "READY", "epochs": EPOCHS, "as_of_ms": 1000,
               "authorization": [{"channel": "ACCESS", "key": "a", "fingerprint": "1" * 64, "coverage": "COMPLETE"}],
               "read_set": [{"channel": "OBJECT", "key": "o", "fingerprint": "2" * 64, "coverage": "COMPLETE"}]}
    same = {**preview, "as_of_ms": 1005, "epochs": dict(EPOCHS)}
    AssuranceCloseoutConsumer.require_final_consistency(preview, same)
    regranted = {**same, "authorization": [{**preview["authorization"][0], "fingerprint": "3" * 64}]}
    with pytest.raises(AssuranceError) as raised:
        AssuranceCloseoutConsumer.require_final_consistency(preview, regranted)
    assert raised.value.code == "RECHECK_REQUIRED"
    reread = {**same, "read_set": [*preview["read_set"], {**preview["read_set"][0], "key": "p"}]}
    with pytest.raises(AssuranceError) as raised:
        AssuranceCloseoutConsumer.require_final_consistency(preview, reread)
    assert raised.value.code == "RECHECK_REQUIRED"
    AssuranceCloseoutConsumer.require_final_consistency(
        {**preview, "state": "DRAINING"}, {**regranted, "state": "DRAINING"})

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            store = world.store
            mission_id = await _completed(world, "a01-authorization")
            mission = store.get_mission(mission_id)
            consumer = _consumer(world)
            authority = world.deployment.assurance.authority
            now_ms = int(store.now * 1000)
            with store.read_view():
                before = consumer._evaluate_locked(mission, now_ms=now_ms)
            channels = {item["channel"] for item in before["authorization"]}
            assert channels == {"ACCESS", "POLICY"}, before["authorization"]
            # 权限见证就是读集的一部分；每张所依赖的证书身份都被问过一遍
            assert all(item in before["read_set"] for item in before["authorization"])
            certificates = store.connection.execute(
                "SELECT count(*) FROM assurance_use_certificates WHERE mission_id=? "
                "AND consumer_kind IN ('ACCEPTANCE','ROOT_RESOLUTION','COMPOUND_RESOLUTION')", (mission_id,)).fetchone()[0]
            assert certificates >= 2
            # 部署策略换了（POLICY 见证指纹变）：重读到的权限与依据读取时不一致 → 不定稿，退回重算
            original = authority.policy_fingerprint
            authority.policy_fingerprint = "f" * 64
            try:
                with store.read_view():
                    after = consumer._evaluate_locked(mission, now_ms=now_ms + 1)
            finally:
                authority.policy_fingerprint = original
            assert after["authorization"] != before["authorization"]
            ready_before = {**before, "state": "READY"}
            ready_after = {**after, "state": "READY"}
            with pytest.raises(AssuranceError) as raised:
                AssuranceCloseoutConsumer.require_final_consistency(ready_before, ready_after)
            assert raised.value.code == "RECHECK_REQUIRED"
            with store.read_view():
                again = consumer._evaluate_locked(mission, now_ms=now_ms + 2)
            AssuranceCloseoutConsumer.require_final_consistency(ready_before, {**again, "state": "READY"})
    asyncio.run(case())


# ------------------------------------------------------------------ A17：收尾正文过 closeout-v1 严格校验
def test_a17_every_closeout_document_validates_strictly_against_closeout_v1(tmp_path):
    """收尾行正文、任务最终报告里的收尾记录、每张收尾回执的文档部分：字段集等于 schema 必填集、无多余键、
    ``read_set`` 如实写（读了根结论 / 要求版本 / 完成范围 / 权限）、``root_resolution_ref`` 是 pin。
    内部核对字段（任务版本、纪元、根化身、依据过期清单……）在回执里，不在正文里。"""
    root = _schema("closeout-v1.schema.json")
    required = set(root["required"])

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            store = world.store
            mission_id = await _completed(world, "a17-strict")
            row, body = _closeout(store, mission_id)
            assert row["state"] == "FINALIZED"
            assert validate(root, body, root=root) == [], body
            assert set(body) == required
            assert set(body["root_resolution_ref"]) == {"id", "revision", "content_hash"}
            assert body["root_resolution_ref"]["id"] == row["resolution_id"]
            keys = {(item["channel"], decode(item["key"])["kind"]) for item in body["read_set"] if item["channel"] == "OBJECT"}
            assert {("OBJECT", "resolution"), ("OBJECT", "requirements"), ("OBJECT", "completion_scope")} <= keys, keys
            assert {item["channel"] for item in body["read_set"]} == {"OBJECT", "ACCESS", "POLICY"}
            report = store.get_mission(mission_id).final_report["assurance_closeout"]
            assert validate(root, report, root=root) == [], report
            assert report["report_ref"] == body["report_ref"]
            # 定稿回执 = 文档 + 判定 / 版本字段；文档部分严格通过
            receipt = store.get_receipt(body["report_ref"]["pin"]["id"])
            assert {"root_incarnation_id", "epochs", "authorization"} <= set(receipt)
            assert validate(root, closeout_document(receipt), root=root) == []
            # 历史上每一次评估（NOT_READY / READY …）写下的回执：文档部分都过；内部核对字段都在回执里
            receipts = [decode(r[0]) for r in store.connection.execute(
                "SELECT receipt_json FROM commit_receipts WHERE subject_id=? AND kind='AssuranceCloseoutEvaluated'",
                (mission_id,)).fetchall()]
            assert receipts
            documents = 0
            for evaluated in receipts:
                assert {"mission_version", "epochs", "root_incarnation_id", "authorization"} <= set(evaluated)
                if evaluated["root_resolution_ref"] is None:
                    # 根结论还没有时的评估只是回执里的投影（收尾行不写），不是 closeout-v1 记录
                    assert evaluated["state"] == "NOT_READY"
                    assert {"ROOT_RESOLUTION_MISSING", "ROOT_NETWORK_UNAVAILABLE"} & set(evaluated["reasons"])
                    continue
                documents += 1
                assert validate(root, closeout_document(evaluated), root=root) == [], evaluated
                # 那次评估写进收尾行的正文就是文档本身（行指纹 = 文档指纹），不是整份评估
                assert evaluated["check_body_hash"] == fingerprint(closeout_document(evaluated))
            assert documents >= 1
            assert {"mission_version", "epochs", "root_incarnation_id"} & set(body) == set()
    asyncio.run(case())


# ------------------------------------------------------------------ A07：已交出未收敛的动作 → DRAINING
def test_a07_a_handed_off_action_drains_the_closeout_until_it_converges(tmp_path):
    """任务下有一条已交出、结果还没回来（HANDED_OFF，租约未到）的动作：收尾 DRAINING / OPEN_OPERATIONS，
    ``unsettled_operation_refs`` 点名它、``dangerous_work_refs`` 空；任务不完成。动作收敛（SUCCEEDED）后再评，
    这个原因消失。"""
    async def case():
        provider = LayeredScriptedProvider()
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            store = world.store
            mission_id = world.create({**GOAL, "idempotency_key": "a07-handed-off"})["mission_id"]
            for _ in range(20):
                await world.drain(timeout=5)
                if provider.entered.is_set():
                    break
            assert provider.entered.is_set()
            action_key = "action-batch2-handed-off:v1"
            now = store.now
            record = {"action_key": action_key, "action_id": "action-batch2-handed-off", "version": 1,
                      "mission_id": mission_id, "state": "HANDED_OFF", "connector": "batch2-none",
                      "operation": "publish", "target": "out-of-scope", "params": {}, "params_hash": "0" * 64,
                      "reservation_subject": None, "handoffs": 1, "lease_expires_at": now + 3600}
            with store.transaction():
                store.connection.execute(
                    "INSERT INTO actions(action_key,action_id,version,mission_id,state,json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (action_key, record["action_id"], 1, mission_id, "HANDED_OFF", json.dumps(record), now, now))
            provider.release.set()
            mission = await world.run_until_settled(mission_id, rounds=8, timeout=20)
            assert str(mission.status.value) == "ACTIVE", mission.status
            row, body = _closeout(store, mission_id)
            assert row["state"] == "DRAINING" and body["reasons"] == ["OPEN_OPERATIONS"], body
            assert [ref["pin"]["id"] for ref in body["unsettled_operation_refs"]] == [action_key]
            assert body["dangerous_work_refs"] == [] and body["pending_effect_keys"] == []
            # 收敛后再评：这个原因不再出现
            with store.transaction():
                store.connection.execute(
                    "UPDATE actions SET state='SUCCEEDED', json=?, updated_at=? WHERE action_key=?",
                    (json.dumps({**record, "state": "SUCCEEDED"}), store.now, action_key))
            with store.read_view():
                after = _consumer(world)._evaluate_locked(store.get_mission(mission_id), now_ms=int(store.now * 1000))
            assert "OPEN_OPERATIONS" not in after["reasons"] and after["state"] != "DRAINING", after
            assert after["unsettled_operation_refs"] == []
    asyncio.run(case())
