# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b Task 4 测试基座（真实 state.db + 真实 ForegroundQueueStore/ClosureFallback + 真实 Memory 0.6.1
backend + 生产 HostEvidenceAuthority / HostMemoryAnalysisExecutor / MemoryIngestionOutboxWorker）。

只有传输层是替身：Provider adapter（脚本化 ``memory_analysis_proposal`` 调用 / 异常）、SDK runtime
stack 的读口（closure harness 的 ``FakeRunFacts``）。终态 outbox、worker、job runner、analysis
executor/delivery authority、物化与 typed recall 全部走生产代码。本模块不含用例。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from simple_harness import CallId, RequestId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall

from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress
from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.memory.analysis_executor import HostMemoryAnalysisExecutor
from deskpet.memory.analysis_lineage import binding_model_config_hash
from deskpet.memory.analysis_proposal import PROPOSAL_TOOL_NAME
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import build_foreground_turn_evidence
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
from deskpet.memory.memory_ingestion_outbox import (
    MemoryIngestionOutboxWorker,
    build_worker_config,
)
from tests.execution import test_foreground_queue as fq
from tests.sdk_adapters import s5b_closure_harness as ch

SUBJECT = fq.SUBJECT
SCOPE = fq.SCOPE
AUTHORITY_REF = "host:validated-control-channel:v1"
TURN_TEXT = "把 README 里的版本号改成 1.2.0"
BINDING = ch.BINDING_RECORD
ENDPOINT = "e" * 64  # FakeAdapter.target.endpoint_identity

HOST_TABLES = (
    "human_memory_evidence",
    "human_memory_sanitization_receipts",
    "memory_ingestion_outbox",
    "memory_ingestion_evidence_links",
    "post_turn_invocation_attempts",
    "post_turn_invocation_members",
    "foreground_terminal_receipts",
    "host_pre_admission_audit",
)
MEMORY_TABLES = (
    "evidence_envelopes",
    "jobs",
    "analysis_batches",
    "accepted_analysis_plans",
    "decision_records",
    "cognitive_memory_heads",
    "cognitive_memory_revisions",
    "memory_mutation_receipts",
    "outbox",
)
RAW_HOST_TABLES = ("human_memory_evidence", "human_memory_sanitization_receipts")
RAW_MEMORY_TABLES = ("evidence_envelopes",)


def rows(db_path: Path, sql: str, *params):
    with sqlite3.connect(db_path) as db:
        return db.execute(sql, params).fetchall()


def expected_model_config_hash(binding: dict[str, Any] | None = None, *, endpoint: str | None = ENDPOINT) -> str:
    return binding_model_config_hash(binding or BINDING, endpoint_identity=endpoint)


class Env(ch.Env):
    pass


async def bound_turn_run(
    tmp_path: Path,
    run_id: str,
    *,
    text: str = TURN_TEXT,
    delivery_key: str = "turn-1",
    owner: str = "owner-1",
    claim_key: str = "claim-1",
) -> Env:
    """One foreground Run whose turn evidence is a REAL sanitized envelope (state.db durable)."""

    queue_db, primary_id, clock = await fq._ready(tmp_path / "queue")
    store = ForegroundQueueStore(queue_db, clock=clock)
    env = Env(
        db_path=queue_db, store=store, clock=clock, primary_id=primary_id,
        ingress=ExecutionEvidenceIngress(queue_db), program=HumanMemoryProgramStore(queue_db),
    )
    return await next_turn_run(env, run_id, text=text, delivery_key=delivery_key, owner=owner, claim_key=claim_key)


async def next_turn_run(
    env: Env,
    run_id: str,
    *,
    text: str,
    delivery_key: str,
    owner: str = "owner-1",
    claim_key: str = "claim-2",
) -> Env:
    envelope, receipt = build_foreground_turn_evidence(
        subject=SUBJECT, authority_ref=AUTHORITY_REF, delivery_key=delivery_key, text=text
    )
    committed = await env.program.append_evidence(envelope, receipt)
    queued = await env.store.enqueue_turn(
        subject=SUBJECT,
        primary_conversation_id=env.primary_id,
        task_scope_id=SCOPE,
        evidence_id=committed.evidence_id,
        evidence_hash=committed.envelope_sha256,
        idempotency_key=delivery_key,
        turn_payload=dict(envelope.sanitized_payload),
    )
    admission = await fq._claim_and_bind(env.store, owner=owner, claim_key=claim_key, sdk_run_id=run_id)
    await env.store.record_sdk_started(
        host_run_id=admission.host_run_id, sdk_run_id=run_id, owner_id=admission.owner_id,
        generation=admission.generation, sdk_event_id=f"sdk-start:{run_id}",
        idempotency_key=f"sdk-start:{run_id}",
    )
    return Env(
        db_path=env.db_path, store=env.store, clock=env.clock, primary_id=env.primary_id,
        ingress=env.ingress, program=env.program, admission=admission, run_id=run_id,
        turn_id=queued.turn_id, evidence_id=committed.evidence_id, envelope=envelope, receipt=receipt,
    )


async def record_terminal(env: Env, observed, *, binding: dict[str, Any] | None = None, endpoint: str | None = ENDPOINT):  # type: ignore[no-untyped-def]
    """Host terminal with the durable Run binding → outbox row in the same transaction."""

    record = {**(binding or BINDING), "run_id": env.run_id}
    return await env.store.record_sdk_terminal(
        host_run_id=env.admission.host_run_id, sdk_run_id=env.run_id,
        owner_id=env.admission.owner_id, generation=env.admission.generation,
        terminal_state=observed.terminal_state, sdk_event_id=observed.sdk_event_id,
        sdk_event_hash=observed.sdk_event_hash, idempotency_key=f"runtime-terminal:{env.admission.host_run_id}",
        run_binding=record, endpoint_identity=endpoint,
    )


async def finish_clean_run(env: Env, *, binding: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
    """No material effect → no closure needed → SDK terminal → Host terminal (+ outbox)."""

    facts = ch.FakeRunFacts(env.run_id, binding=binding or BINDING)
    observed = await ch.observe_terminal(env, facts)
    assert observed is not None
    return await record_terminal(env, observed, binding=binding)


async def finish_effect_run(env: Env, *, effect_id: str = "e-1", path: str = "README.md"):  # type: ignore[no-untyped-def]
    """write_file → host.file dirty → fallback closure (mutate, one call) → Host terminal (+ outbox)."""

    await ch.material_write(env, effect_id, path=path)
    facts = ch.FakeRunFacts(env.run_id)
    observed = await ch.observe_terminal(env, facts)
    assert observed is not None
    refs = ch.scope_evidence_ids(env.db_path)
    revision, _ = ch.head(env.db_path)
    adapter = ch.FakeAdapter([ch.closure_call(ch.mutate_arguments(refs, base_revision=revision, idempotency_key=f"closure:{env.run_id}"))])
    fallback, _ = ch.build_fallback(env, facts, adapter)
    settlement = await ch.settle(env, fallback)
    assert settlement.status == "mutate", settlement
    receipt = await record_terminal(env, observed)
    return receipt, settlement, adapter


def outbox_rows(db_path: Path) -> list[tuple]:
    return rows(
        db_path,
        "SELECT sdk_run_id,turn_id,state,attempts,evidence_ids_json,model_config_hash,receipt_json,last_error "
        "FROM memory_ingestion_outbox ORDER BY created_at",
    )


def attempts(db_path: Path) -> list[tuple]:
    return rows(
        db_path,
        "SELECT attempt_ordinal,status,unknown_class,reason_code,request_hash,evidence_set_key,"
        "result_envelope_json IS NOT NULL FROM post_turn_invocation_attempts WHERE purpose='analysis' "
        "ORDER BY reserved_at,attempt_ordinal",
    )


# --- deterministic analysis provider -------------------------------------------


def semantic_op(item_id: str, quote: str, *, operation_id: str = "op-1", predicate: str = "readme_version",
                object_value: str = "README 版本号改为 1.2.0", subject_entity: str = "user:self") -> dict[str, Any]:
    return {
        "operation_id": operation_id, "memory_type": "semantic", "evidence_item_id": item_id, "exact_quote": quote,
        "reason_code": "explicit_user_statement",
        "semantic": {"subject_entity": subject_entity, "predicate": predicate, "object_value": object_value},
    }


def episode_op(item_id: str, quote: str, *, operation_id: str = "op-episode") -> dict[str, Any]:
    return {
        "operation_id": operation_id, "memory_type": "episode", "evidence_item_id": item_id, "exact_quote": quote,
        "reason_code": "task_episode",
        "episode": {"title": "README 版本升级", "actions": ["修改 README 版本号"], "results": ["版本号为 1.2.0"]},
    }


def procedure_op(item_id: str, quote: str, *, operation_id: str = "op-procedure") -> dict[str, Any]:
    return {
        "operation_id": operation_id, "memory_type": "procedure", "evidence_item_id": item_id, "exact_quote": quote,
        "reason_code": "user_procedure",
        "procedure": {"name": "发版改版本号", "steps": ["改 README 版本号", "提交"], "risk_level": "low"},
    }


def prospective_op(item_id: str, quote: str, *, operation_id: str = "op-prospective") -> dict[str, Any]:
    return {
        "operation_id": operation_id, "memory_type": "prospective", "evidence_item_id": item_id, "exact_quote": quote,
        "reason_code": "explicit_future_action",
        "prospective": {"action": "提交周报", "trigger_at_iso": "2026-09-04T18:00:00+08:00", "timezone": "Asia/Shanghai"},
    }


def proposal_call(operations: list[dict[str, Any]], *, outcome: str = "mutate", raw_id: str = "raw-proposal",
                  provider_request_id: str = "prov-analysis-1") -> ProviderResponse:
    return ProviderResponse(
        request_id=RequestId("fixture"),
        message=Message(role=MessageRole.ASSISTANT, content=""),
        tool_calls=(ProviderToolCall(CallId(raw_id), PROPOSAL_TOOL_NAME, {"outcome": outcome, "operations": operations}),),
        model="model-1",
        finish_reason="tool_calls",
        provider_request_id=provider_request_id,
    )


def item_id(env: Env) -> str:
    return str(env.envelope.sanitized_payload["delivery_key"])


# --- Memory side ------------------------------------------------------------------


class MemoryEnv(SimpleNamespace):
    pass


def _backend_factory(*, clock, fault_injector=None):  # type: ignore[no-untyped-def]
    async def factory(db_path: Path, **kwargs: Any):  # type: ignore[no-untyped-def]
        from simple_harness_memory import MemoryManager
        from simple_harness_memory.backends.sqlite_v5 import SQLiteHumanMemoryBackend
        from simple_harness_memory.core.manager import _NullWorldModel
        from simple_harness_memory.embedders.mock import HashEmbedder

        kwargs = dict(kwargs)
        kwargs["short_horizon_embedder"] = HashEmbedder(32)
        backend = SQLiteHumanMemoryBackend(db_path, fault_injector=fault_injector, now=clock, **kwargs)
        await backend.initialize()
        return MemoryManager(backend, _NullWorldModel())

    return factory


def memory_env(
    env: Env,
    adapter: Any,
    *,
    memory_db: Path | None = None,
    fault=None,
    memory_fault=None,
    observer=None,
    max_attempts: int = 3,
    deadline_ms: int = 5_000,
    worker_max_attempts: int = 3,
    worker_id: str = "worker-1",
    owner_id: str = "outbox-owner-1",
    subject: str = SUBJECT,
    production_builder: bool = False,
    binding: dict[str, Any] | None = None,
    endpoint: str | None = ENDPOINT,
) -> MemoryEnv:
    """Production composition over the test clock: executor ↔ v7 runtime ↔ worker ↔ runner.

    ``production_builder=True`` uses ``build_human_memory_v7`` (wall clock, no test
    seam) exactly as ``main.py`` does — the milestone lanes run that way.
    """

    clock = env.clock
    executor = HostMemoryAnalysisExecutor(
        env.db_path, adapter_factory=lambda record: adapter, clock=clock, fault_inject=fault,
        reconciliation_observer=observer,
    )
    from simple_harness_memory.core.identity import MemoryPrincipal

    runtime = HumanMemoryV7Runtime(
        memory_db or (env.db_path.parent / "human_memory_v7.db"),
        evidence_authority=HostEvidenceAuthority(env.db_path),
        analysis_authority=executor,
        backend_factory=None if production_builder else _backend_factory(clock=clock, fault_injector=memory_fault),
        # The harness subject is ``actor-1``; production binds the local owner (same shape).
        principal=MemoryPrincipal("deskpet-local", "deskpet-local-household", subject, "primary-conversation"),
    )
    worker = MemoryIngestionOutboxWorker(
        env.db_path, runtime.manager, owner_id=owner_id, clock=clock, lease_seconds=30.0,
        max_attempts=worker_max_attempts, retry_delays=(1.0, 2.0), fault_inject=fault,
    )
    record = binding or BINDING
    config = build_worker_config(
        provider_id=record["provider_id"], model_id=record["model_id"],
        model_config_hash=expected_model_config_hash(record, endpoint=endpoint), deadline_ms=deadline_ms,
        max_attempts=max_attempts,
    )
    return MemoryEnv(executor=executor, runtime=runtime, worker=worker, config=config, adapter=adapter,
                     clock=clock, worker_id=worker_id, runner=None)


async def runner(menv: MemoryEnv):  # type: ignore[no-untyped-def]
    if menv.runner is None:
        menv.runner = await menv.runtime.job_runner(menv.executor, menv.config, worker_id=menv.worker_id, now=menv.clock)
    return menv.runner


async def run_job(menv: MemoryEnv) -> str:
    return str(await (await runner(menv)).run_once())


async def memory_rows(menv: MemoryEnv, sql: str, params: tuple = ()) -> list[tuple]:
    backend = (await menv.runtime.manager()).backend
    async with backend.connection.execute(sql, params) as cursor:
        return [tuple(r) for r in await cursor.fetchall()]


async def memory_snapshot(menv: MemoryEnv) -> dict[str, Any]:
    return {
        "jobs": await memory_rows(menv, "SELECT state,attempt_count FROM jobs ORDER BY created_at"),
        "batches": await memory_rows(menv, "SELECT state FROM analysis_batches ORDER BY created_at"),
        "heads": (await memory_rows(menv, "SELECT COUNT(*) FROM cognitive_memory_heads"))[0][0],
        "decisions": (await memory_rows(menv, "SELECT COUNT(*) FROM decision_records"))[0][0],
        "outbox": await memory_rows(menv, "SELECT topic,state FROM outbox ORDER BY topic,created_at"),
        "analysis_head": await memory_rows(menv, "SELECT revision FROM analysis_apply_heads"),
        "cognitive_head": await memory_rows(menv, "SELECT revision FROM cognitive_apply_heads"),
    }


async def close(menv: MemoryEnv) -> None:
    await menv.runtime.close()


async def recall_payloads(menv: MemoryEnv, query: str, *, run_id: str = "recall-run", ordinal: int = 1) -> list[dict[str, Any]]:
    """Host ``typed_recall`` (next-turn memory_standalone lane) → public payloads."""

    from deskpet.memory.human_memory_v7 import project_recall_fragments

    lanes = await menv.runtime.typed_recall(query=query, run_id=run_id, turn_ordinal=ordinal, now=float(menv.clock()) + time.time())
    return [dict(fragment["payload"]) for fragment in project_recall_fragments(lanes) if fragment["lane"] == "long_term_typed"]
