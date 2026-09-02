# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b Task 3 closure 测试基座（真实 state.db + 真实 ForegroundQueueStore/ExecutionEvidenceIngress/
CanonicalTaskScopeStore + 确定性 provider adapter 替身）。

只有传输层是替身：Provider adapter（脚本化 ProviderResponse / 异常）、SDK runtime stack 的三个读口
（terminal 证据、预留事实、closure run facts）。lease/attempt 账本、receipt、apply、终态门全部走生产代码。
本模块不含用例（文件名不以 ``test_`` 开头）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from simple_harness import CallId, RequestId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall

from deskpet.execution.evidence_ingress import (
    ExecutionEvidenceIngress,
    ObjectiveEventSpec,
    ToolInvocationFact,
)
from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.execution.foreground_runtime import SqliteSdkTerminalObserver
from deskpet.execution.semantic_closure import ClosureFallback, ClosureRunFacts
from deskpet.sdk_adapters.post_turn_invoker import (
    ForegroundLeaseFence,
    RunBoundInvoker,
)
from deskpet.sdk_adapters.task_scope_mutation import TaskScopeUpdateService
from tests.execution import test_foreground_queue as fq

SUBJECT = fq.SUBJECT
SCOPE = fq.SCOPE
LAST_ANSWER = "已把 a.txt 写好并跑完测试。"

BINDING_RECORD: dict[str, Any] = {
    "schema_version": 1,
    "run_id": "sdk-run-closure",
    "session_id": "session-1",
    "request_id": "request-1",
    "snapshot_id": "snapshot-1",
    "provider_id": "provider-1",
    "provider_incarnation_id": "inc-1",
    "provider_config_revision": 1,
    "binding_epoch": 1,
    "model_id": "model-1",
    "model_params": {},
    "context_window": 32768,
    "catalog_generation": 1,
    "catalog_fingerprint": "c" * 64,
    "budget_fingerprint": "b" * 64,
}


def rows(db_path: Path, sql: str, *params):
    with sqlite3.connect(db_path) as db:
        return db.execute(sql, params).fetchall()


class OneShot:
    def __init__(self, point: str) -> None:
        self.point = point
        self.fired = False

    def __call__(self, point: str) -> None:
        if point == self.point and not self.fired:
            self.fired = True
            raise RuntimeError(f"injected:{point}")


class Env(SimpleNamespace):
    pass


async def bound_run(tmp_path: Path, run_id: str, *, owner: str = "owner-1", claim_key: str = "claim-1") -> Env:
    """One foreground Run bound to SCOPE (real queue + real state.db)."""

    queue_db, primary_id, clock = await fq._ready(tmp_path / "queue")
    store = ForegroundQueueStore(queue_db, clock=clock)
    await fq._enqueue(store, primary_id, 1)
    admission = await fq._claim_and_bind(store, owner=owner, claim_key=claim_key, sdk_run_id=run_id)
    await store.record_sdk_started(
        host_run_id=admission.host_run_id, sdk_run_id=run_id, owner_id=admission.owner_id,
        generation=admission.generation, sdk_event_id=f"sdk-start:{run_id}",
        idempotency_key=f"sdk-start:{run_id}",
    )
    return Env(
        db_path=queue_db, store=store, clock=clock, admission=admission, run_id=run_id,
        primary_id=primary_id, ingress=ExecutionEvidenceIngress(queue_db),
    )


async def next_run(env: Env, run_id: str, *, owner: str = "owner-1", claim_key: str = "claim-2", index: int = 2) -> Env:
    """Enqueue + claim + bind a second Run on the same SCOPE after the first settled."""

    await fq._enqueue(env.store, env.primary_id, index)
    admission = await fq._claim_and_bind(env.store, owner=owner, claim_key=claim_key, sdk_run_id=run_id)
    await env.store.record_sdk_started(
        host_run_id=admission.host_run_id, sdk_run_id=run_id, owner_id=admission.owner_id,
        generation=admission.generation, sdk_event_id=f"sdk-start:{run_id}",
        idempotency_key=f"sdk-start:{run_id}",
    )
    return Env(
        db_path=env.db_path, store=env.store, clock=env.clock, admission=admission, run_id=run_id,
        primary_id=env.primary_id, ingress=env.ingress,
    )


def write_fact(run_id: str, effect_id: str, *, path: str = "a.txt", state: str = "succeeded") -> ToolInvocationFact:
    return ToolInvocationFact(
        run_id=run_id, effect_id=effect_id, call_id=f"call:{effect_id}", tool_name="write_file",
        effect_state=state, outcome=state, error_code=None,
        objective=ObjectiveEventSpec(
            event_kind="host.file",
            payload={"tool_name": "write_file", "effect_id": effect_id, "call_id": f"call:{effect_id}",
                     "outcome": state, "error_code": None, "targets": [path]},
        ),
    )


async def material_write(env: Env, effect_id: str, *, path: str = "a.txt") -> None:
    """One settled write_file → host.file + evidence 行 + harness.tool_invocation（material）。"""

    await env.ingress.commit_fact(task_scope_id=SCOPE, subject=SUBJECT, fact=write_fact(env.run_id, effect_id, path=path))


def scope_evidence_ids(db_path: Path, scope: str = SCOPE) -> list[str]:
    return [
        str(r[0])
        for r in rows(
            db_path,
            "SELECT DISTINCT evidence_id FROM task_scope_evidence_links WHERE task_scope_id=? ORDER BY created_at,evidence_id",
            scope,
        )
    ]


def head(db_path: Path, scope: str = SCOPE) -> tuple[int, int]:
    [(revision, watermark)] = rows(
        db_path, "SELECT current_revision,event_watermark FROM task_scope_heads WHERE task_scope_id=?", scope
    )
    return int(revision), int(watermark)


def receipts(db_path: Path, scope: str = SCOPE) -> list[tuple]:
    return rows(
        db_path,
        "SELECT sdk_run_id,closure_watermark,outcome,plan_id,reason_code,attempt_id "
        "FROM task_scope_closure_receipts WHERE task_scope_id=? ORDER BY created_at,receipt_id",
        scope,
    )


def attempts(db_path: Path) -> list[tuple]:
    return rows(
        db_path,
        "SELECT attempt_ordinal,status,unknown_class,reason_code,plan_id,generation "
        "FROM post_turn_invocation_attempts ORDER BY request_hash,attempt_ordinal",
    )


# --- provider adapter stand-in --------------------------------------------------


def closure_call(arguments: dict[str, Any], *, raw_id: str = "raw-closure") -> ProviderResponse:
    return ProviderResponse(
        request_id=RequestId("fixture"),
        message=Message(role=MessageRole.ASSISTANT, content=""),
        tool_calls=(ProviderToolCall(CallId(raw_id), "task_scope_update", arguments),),
        model="model-1",
        finish_reason="tool_calls",
        provider_request_id="prov-req-1",
    )


def plain_answer(text: str = "no tool call") -> ProviderResponse:
    return ProviderResponse(
        request_id=RequestId("fixture"),
        message=Message(role=MessageRole.ASSISTANT, content=text),
        model="model-1",
        finish_reason="stop",
    )


def mutate_arguments(evidence_ids: list[str], *, base_revision: int, idempotency_key: str = "closure-1") -> dict[str, Any]:
    return {
        "outcome": "mutate",
        "base_revision": base_revision,
        "evidence_refs": list(evidence_ids),
        "idempotency_key": idempotency_key,
        "operations": [
            {
                "operation_id": "op-1",
                "kind": "plan.step.add",
                "value": "a.txt 已写入并通过测试",
                "reason_code": "objective_file_change",
                "evidence_refs": list(evidence_ids),
            }
        ],
    }


def no_mutation_arguments(evidence_ids: list[str], *, base_revision: int, idempotency_key: str = "closure-nm", closure_reason: str | None = "model_no_change") -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "outcome": "no_mutation",
        "base_revision": base_revision,
        "evidence_refs": list(evidence_ids),
        "idempotency_key": idempotency_key,
    }
    if closure_reason is not None:
        arguments["closure_reason"] = closure_reason
    return arguments


class FakeAdapter:
    """Deterministic ``ProductProviderAdapter`` stand-in (same ``invoke`` surface)."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[Any] = []
        self.target = SimpleNamespace(provider_id="provider-1", model="model-1", endpoint_identity="e" * 64)

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        self.calls.append(request)
        if not self.script:
            raise AssertionError("unexpected provider call")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return await item(request)
        return item


class FakeRunFacts:
    """SDK runtime stack stand-in: terminal evidence + reserved facts + closure run facts."""

    def __init__(self, run_id: str, *, state: str = "completed", last_answer: str | None = LAST_ANSWER,
                 binding: dict[str, Any] | None = BINDING_RECORD, facts: dict[str, object] | None = None) -> None:
        self.run_id = run_id
        self.state = state
        self.last_answer = last_answer
        self.binding = None if binding is None else {**binding, "run_id": run_id}
        self.facts = facts or {}

    def read_run_terminal_evidence(self, r: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            run_id=r, state=self.state, event_id=f"sdk-terminal:{r}", event_hash="7" * 64,
            occurred_at=9.0, error_code=None,
        )

    async def read_reserved_fact(self, reservation):  # type: ignore[no-untyped-def]
        return self.facts.get(reservation.source_event_id)

    def read_closure_run_facts(self, r: str) -> ClosureRunFacts:
        assert r == self.run_id
        return ClosureRunFacts(binding_record=self.binding, last_assistant_message=self.last_answer)


class _SdkIngress:
    def __init__(self, state: str) -> None:
        self.state = state

    async def wait_idle(self, r: str) -> None:
        del r

    def query(self, r: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(state=SimpleNamespace(value=self.state))


async def observe_terminal(env: Env, facts: FakeRunFacts):  # type: ignore[no-untyped-def]
    return await SqliteSdkTerminalObserver(str(env.db_path), _SdkIngress(facts.state), facts).observe(
        host_run_id=env.admission.host_run_id, sdk_run_id=env.run_id, subject=SUBJECT,
        owner_id=env.admission.owner_id, generation=env.admission.generation,
    )


def build_fallback(env: Env, facts: FakeRunFacts, adapter: FakeAdapter, *, fault=None, owner_id: str | None = None,
                   generation: int | None = None, clock=None) -> tuple[ClosureFallback, RunBoundInvoker]:
    owner = owner_id or env.admission.owner_id
    gen = generation or env.admission.generation
    fence = ForegroundLeaseFence(
        env.store, host_run_id=env.admission.host_run_id, sdk_run_id=env.run_id, owner_id=owner, generation=gen,
    )
    invoker = RunBoundInvoker(
        env.db_path, fence=fence, adapter_factory=lambda record: adapter, clock=clock or env.clock, fault_inject=fault,
    )
    service = TaskScopeUpdateService(
        env.db_path, tool_context_getter=lambda: None, route_ledger=None, clock=clock or env.clock, fault_inject=fault,
    )
    fallback = ClosureFallback(
        env.db_path, invoker=invoker, service=service, run_facts_reader=facts, clock=clock or env.clock, fault_inject=fault,
    )
    return fallback, invoker


async def settle(env: Env, fallback: ClosureFallback, *, terminal_state: str = "COMPLETED", owner_id: str | None = None,
                 generation: int | None = None):  # type: ignore[no-untyped-def]
    from deskpet.execution import RunState

    return await fallback.settle(
        host_run_id=env.admission.host_run_id, sdk_run_id=env.run_id,
        owner_id=owner_id or env.admission.owner_id, generation=generation or env.admission.generation,
        terminal_state=RunState(terminal_state),
    )


async def record_terminal(env: Env, observed, *, owner_id: str | None = None, generation: int | None = None):  # type: ignore[no-untyped-def]
    return await env.store.record_sdk_terminal(
        host_run_id=env.admission.host_run_id, sdk_run_id=env.run_id,
        owner_id=owner_id or env.admission.owner_id, generation=generation or env.admission.generation,
        terminal_state=observed.terminal_state, sdk_event_id=observed.sdk_event_id,
        sdk_event_hash=observed.sdk_event_hash, idempotency_key=f"runtime-terminal:{env.admission.host_run_id}",
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def loads(value: str) -> Any:
    return json.loads(value)


async def run_blocking(coro):  # type: ignore[no-untyped-def]
    return await asyncio.wait_for(coro, timeout=30)
