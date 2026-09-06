"""A new memory has no existing target, including fully populated model payloads."""

import sqlite3

import pytest
from deskpet.memory.analysis_proposal import proposal_tool_spec
from jsonschema import Draft202012Validator
from simple_harness import thaw_json
from tests.memory.test_semantic_correction import memory_env, recalled
from tests.sdk_adapters import s5b_closure_harness as ch
from tests.sdk_adapters import s5b_memory_harness as mh


@pytest.mark.asyncio
@pytest.mark.parametrize("slot", [None, "", "model-invented-target"])
async def test_public_create_with_explicit_no_target(tmp_path, slot):
    text = "My preferred drink is coffee."
    env = await mh.bound_turn_run(tmp_path, "create-slot", text=text)
    await mh.finish_clean_run(env)
    operation = mh.semantic_op(
        mh.item_id(env), text, predicate="drink_preference", object_value="coffee"
    )
    operation["action"] = "create"
    if slot is not None:
        operation["candidate_key"] = slot
    proposal = {"outcome": "mutate", "operations": [operation]}
    Draft202012Validator(thaw_json(proposal_tool_spec().parameters)).validate(proposal)
    adapter = ch.FakeAdapter([mh.proposal_call([operation])])
    menv = memory_env(env, adapter)
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == "applied"
        items = await recalled(menv, "coffee", 1)
        if slot == "model-invented-target":
            assert not items
            with sqlite3.connect(env.db_path) as db:
                reasons = [row[0] for row in db.execute(
                    "SELECT reason_code FROM host_pre_admission_audit WHERE payload_kind='analysis_result'"
                )]
            assert "analysis_create_cannot_select_target" in reasons
        else:
            assert len(items) == 1
            assert items[0].public_payload["object_value"] == "coffee"
            assert items[0].selected_item.source_revision == 1
    finally:
        await mh.close(menv)
