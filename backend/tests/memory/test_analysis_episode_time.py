# SPDX-License-Identifier: BUSL-1.1
"""A14-Q1: delayed/recovered analysis retains the first durable Host observation time."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from deskpet.memory import analysis_proposal as ap
from deskpet.memory import human_memory_program as program
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from tests.faults._runner_contract import state_hash
from tests.sdk_adapters import s5b_closure_harness as ch
from tests.sdk_adapters import s5b_memory_harness as mh


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_at", [0.0, 1_700_000_000.0])
@pytest.mark.parametrize("recover", [False, True], ids=["delayed-first-analysis", "settled-response-reopen"])
async def test_episode_time_survives_delay_reopen_and_duplicate_ingress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stored_at: float, recover: bool,
) -> None:
    # Change only the Host store's clock, not the global time module. The real
    # append transaction supplies the oracle; no UPDATE of durable source rows.
    store_clock = SimpleNamespace(time=lambda: stored_at)
    monkeypatch.setattr(program, "time", store_clock)
    env = await mh.bound_turn_run(tmp_path, "sdk-run-episode-time")
    assert env.receipt.admitted_at == 0.0
    assert mh.rows(
        env.db_path, "SELECT committed_at FROM human_memory_evidence WHERE evidence_id=?", env.evidence_id,
    ) == [(stored_at,)]
    raw_before = state_hash(env.db_path, mh.RAW_HOST_TABLES)
    store_clock.time = lambda: stored_at + 3_600.0
    await program.HumanMemoryProgramStore(env.db_path).append_evidence(env.envelope, env.receipt)
    assert state_hash(env.db_path, mh.RAW_HOST_TABLES) == raw_before
    await mh.finish_clean_run(env)

    adapter = ch.FakeAdapter([mh.proposal_call([mh.episode_op(mh.item_id(env), mh.TURN_TEXT)])])

    def interrupt_derivation(point: str) -> None:
        if point == "analysis-before-derive":
            raise RuntimeError("interrupted after durable Provider response")

    menv = mh.memory_env(env, adapter, fault=interrupt_derivation if recover else None)
    try:
        assert await menv.worker.run_once() == "delivered"
        if recover:
            assert await mh.run_job(menv) == "retry_scheduled"
            [(status, raw)] = mh.rows(
                env.db_path, "SELECT status,result_envelope_json FROM post_turn_invocation_attempts",
            )
            assert status == "succeeded" and json.loads(raw)["response"] is not None
            assert json.loads(raw)["envelope"] is None
            assert len(adapter.calls) == 1
    finally:
        await mh.close(menv)

    # A new runtime/executor/authority opens the same DB after a day. Recovery
    # must compile the durable response with no second transport handoff.
    env.clock.now += 86_400.0
    menv = mh.memory_env(env, adapter)
    try:
        assert await mh.run_job(menv) == "applied"
        assert len(adapter.calls) == 1
        assert menv.executor.provider_calls == (0 if recover else 1)
        [(raw,)] = await mh.memory_rows(menv, "SELECT content_json FROM cognitive_memory_revisions")
        episode = json.loads(raw)
        assert episode["occurred_start"] == stored_at
        assert episode["occurred_end"] == stored_at
        [(raw_plan,)] = await mh.memory_rows(menv, "SELECT plan_json FROM accepted_analysis_plans")
        assert json.loads(raw_plan)["operations"][0]["payload"] == episode
        assert state_hash(env.db_path, mh.RAW_HOST_TABLES) == raw_before
        # The compatibility resolver still returns the original signed pair.
        envelope, receipt = await HostEvidenceAuthority(env.db_path).read_admitted(env.evidence_id)
        assert envelope.to_json() == env.envelope.to_json()
        assert receipt.to_json() == env.receipt.to_json()
    finally:
        await mh.close(menv)


def test_explicit_zero_episode_time_is_not_replaced_by_compile_clock() -> None:
    envelope, receipt = mh.build_foreground_turn_evidence(
        subject=mh.SUBJECT, authority_ref=mh.AUTHORITY_REF, delivery_key="epoch-zero", text=mh.TURN_TEXT,
    )
    item = ap.admitted_item(envelope, receipt)
    span = ap.derive_span(item, mh.TURN_TEXT, span_id="epoch-zero-span")
    operation = mh.episode_op(item.item_id, mh.TURN_TEXT)
    first = ap.compile_operation(operation, span, item=item, now=10_000.0)
    delayed = ap.compile_operation(operation, span, item=item, now=20_000.0)
    assert first.payload.occurred_start == delayed.payload.occurred_start == 0.0
    assert first.payload.occurred_end == delayed.payload.occurred_end == 0.0
