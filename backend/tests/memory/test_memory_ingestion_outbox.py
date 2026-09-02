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
        local_memory_principal,
        local_memory_scope,
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
        again = await manager.register_principal_owner(local_memory_principal(), local_memory_scope())
        assert again == first
    finally:
        await runtime.close()



# ---- S5b Task 6：Task 4 审查 F-1 / F-4 / F-5 ---------------------------------------


@pytest.mark.asyncio
async def test_derivation_failure_after_provider_response_does_not_strand_attempt(tmp_path: Path) -> None:
    """F-1（P1）：Provider 响应到手后、派生/编译前的 Host 异常 → attempt 已 ``succeeded`` 且公开响应 durable
    （不停留 handed_off）；Memory 重试第二次投递 **0 Provider 调用** 从 durable 响应重派生并 applied；
    无 ``memory.analysis.blocked`` 审计行。>16KB 逐字引用是确定性拒绝（quote_too_long），不是 ValueError。"""

    from deskpet.memory.analysis_executor import blocked_audit_rows
    from deskpet.memory.analysis_proposal import AnalysisProposalRejected, derive_span

    env = await mh.bound_turn_run(tmp_path, "sdk-run-derive")
    await mh.finish_effect_run(env)
    adapter = ch.FakeAdapter([mh.proposal_call([mh.semantic_op(mh.item_id(env), "版本号改成 1.2.0")])])
    menv = mh.memory_env(env, adapter, fault=ch.OneShot("analysis-before-derive"))
    assert await menv.worker.run_once() == "delivered"
    # 第一次投递：响应先 settle（succeeded + 公开响应 durable），随后派生失败 → 可重试错误；
    # Memory 的重试（同 run_job 内或下一次）从 durable 响应重派生：**恰一次 Provider 调用**。
    outcomes: list[str] = []
    for _ in range(4):
        try:
            outcomes.append(await mh.run_job(menv))
        except Exception as exc:  # noqa: BLE001 - Memory runner may surface the executor error
            outcomes.append(f"raised:{type(exc).__name__}")
        if "applied" in outcomes:
            break
        env.clock.now += 40.0
    assert "applied" in outcomes, outcomes
    assert len(adapter.calls) == 1 and menv.executor.provider_calls == 1
    rows = mh.attempts(env.db_path)
    assert rows and all(status == "succeeded" for _, status, *_ in rows), rows  # 绝不停留 handed_off
    durable_rows = [
        json.loads(raw) for (raw,) in mh.rows(
            env.db_path,
            "SELECT result_envelope_json FROM post_turn_invocation_attempts WHERE purpose='analysis' ORDER BY attempt_ordinal",
        )
    ]
    assert all(item["response"] is not None for item in durable_rows)
    assert any(item["envelope"] is not None for item in durable_rows)
    assert await blocked_audit_rows(env.db_path) == []
    snapshot = await mh.memory_snapshot(menv)
    assert snapshot["heads"] == 1
    await mh.close(menv)

    # 确定性：>16KB 引用 → AnalysisProposalRejected(quote_too_long)，永不 ValueError。
    from deskpet.memory.analysis_proposal import admitted_item

    long_text = "甲" * 9000
    long_envelope, long_receipt = mh.build_foreground_turn_evidence(
        subject=mh.SUBJECT, authority_ref=mh.AUTHORITY_REF, delivery_key="turn-long", text=long_text
    )
    item = admitted_item(long_envelope, long_receipt)
    with pytest.raises(AnalysisProposalRejected) as too_long:
        derive_span(item, long_text, span_id="span-long")
    assert too_long.value.detail["reason"] == "quote_too_long"


@pytest.mark.asyncio
async def test_inconsistent_outbox_row_dead_letters_after_bounded_reclaims(tmp_path: Path) -> None:
    """F-4：evidence_ids_json ≠ links 的行在 claim 事务内判定：每次消耗一次 attempt、回 pending 带 last_error，
    到上限 → dead_letter（不再无限 reclaim）；队列里的好行照常投递。"""

    from deskpet.memory.memory_ingestion_outbox import LINKS_MISMATCH_ERROR

    env = await mh.bound_turn_run(tmp_path, "sdk-run-bad")
    await mh.finish_clean_run(env)
    env2 = await mh.next_turn_run(env, "sdk-run-good", text="第二轮", delivery_key="turn-2")
    await mh.finish_clean_run(env2)
    bad_id, good_id = [r[0] for r in mh.rows(env.db_path, "SELECT outbox_id FROM memory_ingestion_outbox ORDER BY created_at")]
    with sqlite3.connect(env.db_path) as db:
        db.execute("INSERT INTO memory_ingestion_evidence_links(outbox_id,evidence_id) VALUES (?,?)", (bad_id, "phantom-evidence"))
        db.commit()
    menv = mh.memory_env(env, ch.FakeAdapter([]), worker_max_attempts=3)
    outcomes = []
    for _ in range(8):
        outcomes.append(await menv.worker.run_once())
        env.clock.now += 3.0
    states = {r[0]: r[1:] for r in mh.rows(env.db_path, "SELECT outbox_id,state,attempts,last_error FROM memory_ingestion_outbox")}
    assert states[bad_id] == ("dead_letter", 3, LINKS_MISMATCH_ERROR)
    assert states[good_id][0] == "delivered"
    assert "delivered" in outcomes
    # 终态后不再被挑中。
    assert await menv.worker.claim() is None
    await mh.close(menv)


@pytest.mark.asyncio
async def test_terminal_commit_without_binding_does_not_stall_fifo(tmp_path: Path) -> None:
    """F-5：durable Run binding 不可得 → Host 终态照常提交，同事务写 outbox ``dead_letter(run_binding_unavailable)``
    （raw 不丢），FIFO 下一 turn 可继续；``_terminal_binding`` 不再抛错而是审计。"""

    from deskpet.execution.foreground_runtime import (
        RUN_BINDING_UNAVAILABLE_REASON,
        ForegroundRuntimeExecutionAuthority,
    )

    env = await mh.bound_turn_run(tmp_path, "sdk-run-nobinding")
    facts = ch.FakeRunFacts(env.run_id, binding=None)
    observed = await ch.observe_terminal(env, facts)
    assert observed is not None
    receipt = await env.store.record_sdk_terminal(
        host_run_id=env.admission.host_run_id, sdk_run_id=env.run_id, owner_id=env.admission.owner_id,
        generation=env.admission.generation, terminal_state=observed.terminal_state, sdk_event_id=observed.sdk_event_id,
        sdk_event_hash=observed.sdk_event_hash, idempotency_key=f"runtime-terminal:{env.admission.host_run_id}",
        run_binding=None, endpoint_identity=None, outbox_dead_letter_reason=RUN_BINDING_UNAVAILABLE_REASON,
    )
    assert receipt.terminal_state.value == "COMPLETED"
    [(sdk_run_id, _turn, state, attempts, evidence_ids_json, _hash, receipt_json, last_error)] = mh.outbox_rows(env.db_path)
    assert (sdk_run_id, state, attempts, receipt_json, last_error) == (env.run_id, "dead_letter", 0, None, RUN_BINDING_UNAVAILABLE_REASON)
    assert json.loads(evidence_ids_json) == [env.evidence_id]
    [(lineage_json,)] = mh.rows(env.db_path, "SELECT analysis_lineage_json FROM memory_ingestion_outbox")
    assert json.loads(lineage_json)["binding_missing"] is True
    assert mh.rows(env.db_path, "SELECT COUNT(*) FROM human_memory_evidence WHERE evidence_id=?", env.evidence_id) == [(1,)]
    # FIFO 不卡：下一 turn 照常入队/claim/终态（带 binding → pending 行）→ worker 投递它，跳过 dead_letter 行。
    env2 = await mh.next_turn_run(env, "sdk-run-after", text="下一轮", delivery_key="turn-2")
    await mh.finish_clean_run(env2)
    menv = mh.memory_env(env, ch.FakeAdapter([]))
    assert await menv.worker.run_once() == "delivered"
    states = dict(mh.rows(env.db_path, "SELECT sdk_run_id,state FROM memory_ingestion_outbox"))
    assert states == {env.run_id: "dead_letter", env2.run_id: "delivered"}
    await mh.close(menv)

    # 生产 runtime 的 _terminal_binding：reader 返回无 binding / 抛错 → (None, None) + 审计，不抛。
    audits: list[tuple[str, dict]] = []
    runtime = ForegroundRuntimeExecutionAuthority.__new__(ForegroundRuntimeExecutionAuthority)
    runtime._audit = type("Audit", (), {"record": staticmethod(lambda event, payload: audits.append((event, dict(payload))))})()
    runtime._endpoint_identity_resolver = None
    runtime._run_binding_reader = lambda run_id: ch.ClosureRunFacts(binding_record=None, last_assistant_message=None)
    assert runtime._terminal_binding("sdk-run-x") == (None, None)

    def broken(run_id: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("stack not ready")

    runtime._run_binding_reader = broken
    assert runtime._terminal_binding("sdk-run-y") == (None, None)
    assert [event for event, _ in audits] == ["foreground.runtime.run_binding_unavailable"] * 2
    assert audits[0][1]["error_code"] == RUN_BINDING_UNAVAILABLE_REASON
