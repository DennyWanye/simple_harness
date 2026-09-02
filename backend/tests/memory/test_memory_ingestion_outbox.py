# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b Task 4：Memory 摄入 outbox worker、analysis attempt 五态/UNIQUE、Memory 不可用 → dead-letter + raw 守恒、
duplicate 投递同 receipt、evidence authority 只读 Host state.db、v7 接线（filter policies / 属主注册 / fail-open 删除）。
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from simple_harness.providers.errors import ProviderRequestRejectedError

from tests.faults._runner_contract import state_hash
from tests.sdk_adapters import s5b_closure_harness as ch
from tests.sdk_adapters import s5b_memory_harness as mh


async def _delivered(tmp_path: Path, run_id: str, adapter, **kwargs):  # type: ignore[no-untyped-def]
    env = await mh.bound_turn_run(tmp_path, run_id)
    await mh.finish_clean_run(env)
    menv = mh.memory_env(env, adapter, **kwargs)
    assert await menv.worker.run_once() == "delivered"
    return env, menv


@pytest.mark.asyncio
async def test_duplicate_delivery_returns_same_receipt_and_raw_is_conserved(tmp_path: Path) -> None:
    """同一 outbox 行二次投递（Host receipt 丢失后重放）→ Memory 按 source_ref 幂等返回同 receipt；raw 行数/hash 守恒。"""
    env = await mh.bound_turn_run(tmp_path, "sdk-run-dup")
    await mh.finish_clean_run(env)
    menv = mh.memory_env(env, ch.FakeAdapter([]))
    raw_host = state_hash(env.db_path, mh.RAW_HOST_TABLES)
    assert await menv.worker.run_once() == "delivered"
    first = json.loads(mh.outbox_rows(env.db_path)[0][6])["receipts"]
    raw_memory = state_hash(menv.runtime.db_path, mh.RAW_MEMORY_TABLES)
    # 第二次投递（直接对同一 claim 语义 deliver）→ 同 receipt id，evidence_envelopes 不增。
    claim = await menv.worker.claim()
    assert claim is None  # delivered 行不可再 claim
    from deskpet.memory.memory_ingestion_outbox import OutboxClaim

    replay = await menv.worker.deliver(OutboxClaim(
        outbox_id="replay", host_run_id=env.admission.host_run_id, sdk_run_id=env.run_id, turn_id=env.turn_id,
        subject=mh.SUBJECT, evidence_ids=(env.evidence_id,), analysis_lineage=json.loads(
            mh.rows(env.db_path, "SELECT analysis_lineage_json FROM memory_ingestion_outbox")[0][0]
        ), attempts=1, lease_owner="replay",
    ))
    assert replay == first
    assert state_hash(menv.runtime.db_path, mh.RAW_MEMORY_TABLES) == raw_memory
    assert state_hash(env.db_path, mh.RAW_HOST_TABLES) == raw_host
    assert (await mh.memory_rows(menv, "SELECT COUNT(*) FROM jobs"))[0][0] == 1
    await mh.close(menv)


@pytest.mark.asyncio
async def test_memory_unavailable_bounded_retry_then_dead_letter_raw_conserved(tmp_path: Path) -> None:
    """Memory DB 只读（文件权限）→ ingest 失败 → attempts 递增、指数重试 → dead_letter + last_error；Host raw 不丢。"""
    env = await mh.bound_turn_run(tmp_path, "sdk-run-ro")
    await mh.finish_clean_run(env)
    memory_db = tmp_path / "ro" / "human_memory_v7.db"
    memory_db.parent.mkdir()
    menv = mh.memory_env(env, ch.FakeAdapter([]), memory_db=memory_db, worker_max_attempts=3)
    await menv.runtime.manager()  # 建库 + 属主注册
    await mh.close(menv)
    os.chmod(memory_db, 0o444)
    os.chmod(memory_db.parent, 0o555)
    raw_before = state_hash(env.db_path, mh.RAW_HOST_TABLES)
    try:
        menv = mh.memory_env(env, ch.FakeAdapter([]), memory_db=memory_db, worker_max_attempts=3)
        outcomes = []
        for _ in range(6):
            outcomes.append(await menv.worker.run_once())
            env.clock.now += 3.0
            if outcomes[-1] == "dead_letter":
                break
        assert outcomes[-1] == "dead_letter", outcomes
        assert outcomes[:-1] and all(o in {"retry_scheduled", "idle"} for o in outcomes[:-1])
        [(_, _, state, attempts, _, _, receipt_json, last_error)] = mh.outbox_rows(env.db_path)
        assert (state, attempts, receipt_json) == ("dead_letter", 3, None) and last_error
        assert await menv.worker.run_once() == "idle"
    finally:
        os.chmod(memory_db.parent, 0o755)
        os.chmod(memory_db, 0o644)
    assert state_hash(env.db_path, mh.RAW_HOST_TABLES) == raw_before
    assert mh.rows(env.db_path, "SELECT COUNT(*) FROM human_memory_evidence WHERE evidence_id=?", env.evidence_id) == [(1,)]
    # dead_letter 终态单调：不可回到 pending / delivered。
    with sqlite3.connect(env.db_path) as db, pytest.raises(sqlite3.IntegrityError, match="memory_ingestion_outbox_monotonic"):
        db.execute("UPDATE memory_ingestion_outbox SET state='pending'")


@pytest.mark.asyncio
async def test_analysis_attempt_five_states_and_unique(tmp_path: Path) -> None:
    """analysis purpose 的 attempt 账本：reserved→handed_off→succeeded/failed/unknown、UNIQUE(request_hash, ordinal)、
    not_sent 可 attempt+1、provider 4xx → failed(None) 永久拒绝、终态行不可改。"""
    import httpx

    adapter = ch.FakeAdapter([httpx.ConnectError("refused"), ProviderRequestRejectedError(public_message="bad request")])
    env, menv = await _delivered(tmp_path, "sdk-run-states", adapter)
    # ① not_sent → failed(not_sent)，Memory 重试。
    assert await mh.run_job(menv) == "retry_scheduled"
    assert [(o, s, u) for o, s, u, *_ in mh.attempts(env.db_path)] == [(1, "failed", "not_sent")]
    # ② 重试的 request（Memory attempt+1 → 新 request_hash）→ 新行 ordinal 1；provider 拒绝 → failed(None)。
    env.clock.now += 5.0
    assert await mh.run_job(menv) == "retry_scheduled"
    rows = mh.attempts(env.db_path)
    assert [(o, s, u) for o, s, u, *_ in rows] == [(1, "failed", "not_sent"), (1, "failed", None)]
    assert rows[0][4] != rows[1][4] and rows[0][5] == rows[1][5]  # request_hash 随 attempt 变，evidence_set_key 不变
    # ③ 第三次：成员无 open 行、latest failed(None) 只对同 request_hash 阻断；新 request_hash → 新 attempt。
    adapter.script.append(mh.proposal_call([mh.semantic_op(mh.item_id(env), "版本号改成 1.2.0")]))
    env.clock.now += 5.0
    assert await mh.run_job(menv) == "applied"
    rows = mh.attempts(env.db_path)
    assert [(o, s, u, e) for o, s, u, _, _, _, e in rows] == [(1, "failed", "not_sent", 0), (1, "failed", None, 0), (1, "succeeded", None, 1)]
    assert len(adapter.calls) == 3 and menv.executor.provider_calls == 3
    # ④ UNIQUE(request_hash, attempt_ordinal) 与终态不可变。
    [(request_hash,)] = mh.rows(env.db_path, "SELECT request_hash FROM post_turn_invocation_attempts WHERE status='succeeded'")
    with sqlite3.connect(env.db_path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO post_turn_invocation_attempts(attempt_id,purpose,host_run_id,sdk_run_id,generation,request_hash,"
                "attempt_ordinal,evidence_set_key,status,provider_id,model_id,model_config_hash,reserved_at) "
                "VALUES ('dup','analysis','h','s',1,?,1,?,'reserved','p','m',?,1)",
                (request_hash, "a" * 64, "b" * 64),
            )
        with pytest.raises(sqlite3.IntegrityError, match="post_turn_invocation_attempt_monotonic"):
            db.execute("UPDATE post_turn_invocation_attempts SET status='handed_off' WHERE status='succeeded'")
        with pytest.raises(sqlite3.IntegrityError, match="post_turn_invocation_attempt_monotonic"):
            db.execute("UPDATE post_turn_invocation_attempts SET result_envelope_json='{}' WHERE status='succeeded'")
    await mh.close(menv)


@pytest.mark.asyncio
async def test_lineage_mismatch_is_rejected_without_provider_call(tmp_path: Path) -> None:
    """Memory 派生的 request lineage ≠ binding 派生值 → analysis_lineage_mismatch（0 调用、audit 行）。"""
    from deskpet.memory.analysis_executor import HostAnalysisExecutorError

    adapter = ch.FakeAdapter([])
    env, menv = await _delivered(tmp_path, "sdk-run-lineage", adapter)
    from simple_harness.runtime import MemoryAnalysisRequest

    backend = (await menv.runtime.manager()).backend
    claim = await backend.claim_analysis_batch(menv.config, "probe")
    assert claim is not None
    forged = MemoryAnalysisRequest.from_json({**claim.request.to_json(), "model_id": "another-model"})
    with pytest.raises(HostAnalysisExecutorError, match="analysis_lineage_mismatch"):
        await menv.executor.analyze_memory(forged)
    assert len(adapter.calls) == 0 and mh.attempts(env.db_path) == []
    assert mh.rows(env.db_path, "SELECT payload_kind,reason_code FROM host_pre_admission_audit") == [("analysis_result", "analysis_lineage_mismatch")]
    await mh.close(menv)


@pytest.mark.asyncio
async def test_evidence_authority_reads_host_state_db_only(tmp_path: Path) -> None:
    """Host EvidenceAuthorityPort：从 state.db 读 durable envelope/receipt；未 admitted / 篡改 span → 拒绝。"""
    from deskpet.memory import analysis_proposal as ap
    from deskpet.memory.evidence_authority import (
        HostEvidenceAuthority,
        HostEvidenceUnavailable,
    )

    env = await mh.bound_turn_run(tmp_path, "sdk-run-authority")
    authority = HostEvidenceAuthority(env.db_path)
    envelope, receipt = await authority.read_admitted(env.evidence_id)
    assert envelope.envelope_hash == env.envelope.envelope_hash and receipt.receipt_hash == env.receipt.receipt_hash
    span = ap.derive_span(ap.admitted_item(envelope, receipt), "1.2.0", span_id="s")
    admitted = await authority.resolve_admitted_evidence(span)
    assert admitted.envelope == envelope and admitted.receipt == receipt
    assert admitted.item_authority.item_json_pointer == "/text" and admitted.item_authority.item_id == "turn-1"
    with pytest.raises(HostEvidenceUnavailable):
        await authority.read_admitted("never-admitted")
    import dataclasses

    with pytest.raises(HostEvidenceUnavailable, match="span_lineage_mismatch"):
        await authority.resolve_admitted_evidence(dataclasses.replace(span, sanitized_hash="0" * 64))
    with pytest.raises(ValueError):
        await authority.resolve_typed_observation(object())


@pytest.mark.asyncio
async def test_v7_runtime_registers_owner_and_passes_host_policies(tmp_path: Path) -> None:
    """接线：supported_filter_policies 含 host-public-turn/v1 + host-typed-ingress/v1；首次构建幂等
    register_principal_owner；fresh install 的 pending_occurrences 直接成功（fail-open 分支已删）。"""
    from simple_harness_memory import PrincipalRegistrationReceipt

    from deskpet.memory.human_memory_v7 import (
        HOST_SUPPORTED_FILTER_POLICIES,
        HumanMemoryV7Runtime,
    )

    assert HOST_SUPPORTED_FILTER_POLICIES == {"credential-filter/v1", "host-public-turn/v1", "host-typed-ingress/v1"}
    runtime = HumanMemoryV7Runtime(tmp_path / "fresh.db")
    try:
        assert await runtime.pending_occurrences(()) == ()
        first = runtime.registration_receipt
        assert isinstance(first, PrincipalRegistrationReceipt) and first.deployment_id == "deskpet-local"
        manager = await runtime.manager()
        kwargs = runtime.build_kwargs()
        assert kwargs["supported_filter_policies"] == HOST_SUPPORTED_FILTER_POLICIES
        assert kwargs["classification_policy"] is not None
        envelope, receipt = mh.build_foreground_turn_evidence(subject="deskpet-local-owner-v1", authority_ref=mh.AUTHORITY_REF, delivery_key="k", text="探针")
        accepted = await manager.ingest_committed_evidence(envelope, receipt)
        assert accepted.evidence_id == envelope.evidence_id
        again = await manager.register_principal_owner(mh.local_memory_principal(), mh.local_memory_scope())
        assert again == first
    finally:
        await runtime.close()
