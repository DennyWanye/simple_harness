"""Host index of actual primary tool identities, independent of TaskScope watermarks.

Rows grant no authority and contain no tool output. SDK public read_effect remains
the authority for the result. Recording before handler entry also survives a
crash between SDK settlement and the next Provider request.
"""
from __future__ import annotations

from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx
from deskpet.task_scope.protocol import canonical_hash, canonical_json, identifier


async def record_effect(binding, context, tool_name):
    if context.effect_id is None or context.run_id.value != binding.sdk_run_id:
        raise ValueError("primary_effect_index_identity_mismatch")
    store = binding.store
    from deskpet.execution.foreground_queue import (
        EffectBoundary, _EFFECT_BOUNDARY_ALLOWED_STATES, _clock_value,
    )
    body = dict(host_run_id=binding.host_run_id, sdk_run_id=binding.sdk_run_id,
                effect_id=identifier(context.effect_id.value, "effect_id", 512),
                tool_name=identifier(tool_name, "tool_name", 512))
    async with store._connection() as db:
        await db.execute("BEGIN IMMEDIATE")
        await assert_human_memory_ingress_open_tx(db)
        head = await store._validate_lease_tx(db, binding.host_run_id,
            binding.owner_id, binding.generation, _clock_value(store._clock))
        if str(head["current_state"]) not in _EFFECT_BOUNDARY_ALLOWED_STATES[EffectBoundary.TOOL]:
            raise ValueError("primary_effect_index_state_rejected")
        await store._validate_sdk_binding_tx(db, binding.host_run_id, binding.sdk_run_id)
        row = await store._fetchone(db,
            "SELECT identity_hash FROM primary_effect_identities WHERE effect_id=?",
            (body["effect_id"],))
        digest = canonical_hash(body)
        if row is not None:
            if row["identity_hash"] != digest:
                raise ValueError("primary_effect_index_replay_conflict")
        else:
            await db.execute("INSERT INTO primary_effect_identities "
                "(host_run_id,sdk_run_id,effect_id,tool_name,identity_hash,identity_json) VALUES (?,?,?,?,?,?)",
                (*body.values(), digest, canonical_json(body)))
        await db.commit()
