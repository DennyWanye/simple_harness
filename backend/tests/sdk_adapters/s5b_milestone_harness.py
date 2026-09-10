# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b 价值验证里程碑基座（Task 4）：真 ReActLoop（effect gate 基座）+ 真 foreground Run（真 state.db
admission/claim/bind/terminal）+ 真 ClosureFallback + 真 Memory 0.6.1（生产 builder）+ 生产 outbox
worker / analysis executor / typed recall。

链路：用户消息经 ``HumanMemoryHostService.enqueue_turn``（真实 sanitized evidence + foreground turn）→
claim/bind foreground Run（sdk_run_id = 基座 RUN）→ ReActLoop：route → ``write_file``（EffectGate）→
host.file 脏标记 → ``task_scope_update``（漏调则兜底一次）→ 终答 → SDK terminal 观察 → Host 终态
（同事务 outbox）→ worker ingest → job runner → analysis（executor 由 binding 重建 adapter）→ APPLIED
且物化 → 下一轮 ``typed_recall``。只有 Provider（脚本化 / 真实中继）与 SDK runtime stack 读口是替身。
本模块不含用例。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from deskpet.execution.foreground_queue import ContextLineage, ForegroundQueueStore
from deskpet.memory.human_memory_service import QueueTurnRequest
from tests.sdk_adapters import s5b_closure_harness as ch
from tests.sdk_adapters import s5b_effect_gate_harness as h

# 2026-09-10：``tests/sdk_adapters/s5b_memory_harness`` 随认知记忆 SDK 一并删除。
# 本 harness 只用到它四个纯 Host 的东西，就地内联，不再依赖那个模块。
BINDING = ch.BINDING_RECORD
ENDPOINT = "e" * 64  # FakeAdapter.target.endpoint_identity


async def record_terminal(env, observed, *, binding=None, endpoint=ENDPOINT):  # type: ignore[no-untyped-def]
    """Host terminal with the durable Run binding (originally s5b_memory_harness)."""

    record = {**(binding or BINDING), "run_id": env.run_id}
    return await env.store.record_sdk_terminal(
        host_run_id=env.admission.host_run_id, sdk_run_id=env.run_id,
        owner_id=env.admission.owner_id, generation=env.admission.generation,
        terminal_state=observed.terminal_state, sdk_event_id=observed.sdk_event_id,
        sdk_event_hash=observed.sdk_event_hash,
        idempotency_key=f"runtime-terminal:{env.admission.host_run_id}",
        run_binding=record, endpoint_identity=endpoint,
    )


def post_turn_attempts(db_path: Path) -> list[tuple]:
    return rows(
        db_path,
        "SELECT attempt_ordinal,status,unknown_class,reason_code,request_hash,evidence_set_key,"
        "result_envelope_json IS NOT NULL FROM post_turn_invocation_attempts WHERE purpose='analysis' "
        "ORDER BY reserved_at,attempt_ordinal",
    )

SUBJECT = h.AUTH.subject
MESSAGE = "把 README 里的版本号改成 1.2.0"
README_BEFORE = "# Demo Project\n\nversion: 1.1.3\n\n发布说明见 CHANGELOG。\n"
DELIVERY_KEY = "turn-readme-1"


class MilestoneEnv(SimpleNamespace):
    pass


def rows(db_path: Path, sql: str, *params):
    with sqlite3.connect(db_path) as db:
        return db.execute(sql, params).fetchall()


async def claim_and_bind(store: ForegroundQueueStore, *, subject: str, sdk_run_id: str, owner: str = "owner-1"):  # type: ignore[no-untyped-def]
    candidate = await store.read_next_preparation_candidate(subject)
    assert candidate is not None
    draft = await store.prepare_candidate(
        subject=subject, expected_candidate_hash=candidate.candidate_hash,
        context=ContextLineage("context-1", 1, "c" * 64), idempotency_key="prepare-1",
    )
    admission = await store.claim_next(
        subject=subject, owner_id=owner, claim_idempotency_key="claim-1",
        preparation_draft_id=draft.draft_id, preparation_draft_hash=draft.draft_hash, lease_seconds=300,
    )
    assert admission is not None
    await store.record_execution_preparation(
        host_run_id=admission.host_run_id, owner_id=owner, generation=admission.generation,
        context_ref="context:milestone", context_hash="c" * 64, provider_ref="provider:milestone", provider_hash="d" * 64,
        tool_ref="tools:milestone", tool_hash="e" * 64, execution_request_hash="f" * 64, idempotency_key="final-prepare-1",
    )
    await store.record_start_intent(
        host_run_id=admission.host_run_id, sdk_run_id=sdk_run_id, owner_id=owner, generation=admission.generation,
        start_request_hash="a" * 64, idempotency_key="start-intent-1",
    )
    await store.record_start_observation(
        host_run_id=admission.host_run_id, sdk_run_id=sdk_run_id, owner_id=owner, generation=admission.generation,
        outcome="RETURNED", result_ref=f"sdk-start:{sdk_run_id}", result_hash="b" * 64, idempotency_key="start-observation-1",
    )
    await store.bind_sdk_run(
        host_run_id=admission.host_run_id, sdk_run_id=sdk_run_id, owner_id=owner, generation=admission.generation,
        idempotency_key="bind-1",
    )
    await store.record_sdk_started(
        host_run_id=admission.host_run_id, sdk_run_id=sdk_run_id, owner_id=owner, generation=admission.generation,
        sdk_event_id=f"sdk-start:{sdk_run_id}", idempotency_key=f"sdk-start:{sdk_run_id}",
    )
    return admission


async def build(tmp_path: Path, *, message: str = MESSAGE) -> MilestoneEnv:
    gate = await h.build_env(tmp_path, first_message=message)
    scope_id, workspace = await h.make_bound_scope(gate, "readme", "root-readme")
    (workspace / "README.md").write_text(README_BEFORE, encoding="utf-8")
    h.freeze_run(gate, task_scope_id=scope_id, workspace_root=workspace)
    queued = await gate.service.enqueue_turn(QueueTurnRequest(scope_id, DELIVERY_KEY, message))
    store = ForegroundQueueStore(gate.db_path, clock=time.time)
    admission = await claim_and_bind(store, subject=SUBJECT, sdk_run_id=h.RUN.value)
    [(evidence_id,)] = rows(gate.db_path, "SELECT evidence_id FROM foreground_turns WHERE turn_id=?", str(queued["turn_ref"]))
    return MilestoneEnv(
        gate=gate, db_path=gate.db_path, store=store, clock=time.time, admission=admission, run_id=h.RUN.value,
        scope_id=scope_id, workspace=workspace, turn_id=str(queued["turn_ref"]), evidence_id=str(evidence_id),
        primary_id=None, ingress=gate.evidence_ingress,
    )


def readme(m: MilestoneEnv) -> str:
    return (m.workspace / "README.md").read_text(encoding="utf-8")


def evidence_ids(m: MilestoneEnv) -> list[str]:
    return [str(r[0]) for r in rows(m.db_path, "SELECT DISTINCT evidence_id FROM task_scope_evidence_links WHERE task_scope_id=? ORDER BY created_at,evidence_id", m.scope_id)]


def revision(m: MilestoneEnv) -> int:
    [(value,)] = rows(m.db_path, "SELECT current_revision FROM task_scope_heads WHERE task_scope_id=?", m.scope_id)
    return int(value)


def closure_arguments(m: MilestoneEnv, key: str = "closure-readme") -> dict[str, Any]:
    refs = evidence_ids(m)
    return {
        "outcome": "mutate", "base_revision": revision(m), "evidence_refs": refs, "idempotency_key": key,
        "operations": [
            {"operation_id": "op-readme", "kind": "plan.step.add", "value": "README 版本号已改为 1.2.0",
             "reason_code": "objective_file_change", "evidence_refs": refs},
        ],
    }


class LazyProvider(h.ScriptedProvider):
    """Scripted main-run provider whose closure call is computed lazily (live refs/revision)."""

    async def invoke(self, run_id, request, *, cancel, execution_lease):  # type: ignore[no-untyped-def]
        if self.responses and callable(self.responses[0]):
            self.responses[0] = self.responses[0]()
        return await super().invoke(run_id, request, cancel=cancel, execution_lease=execution_lease)


def scripted_main_run(m: MilestoneEnv, *, call_closure: bool = True, answer: str = "已把 README 的版本号改成 1.2.0。") -> LazyProvider:
    script: list[Any] = [
        h.tool_call("context_route", {"route": "resume_existing", "task_scope_id": m.scope_id}, raw_id="raw-route"),
        h.tool_call("write_file", {"path": "README.md", "content": README_BEFORE.replace("1.1.3", "1.2.0")}, raw_id="raw-write"),
    ]
    if call_closure:
        script.append(lambda: h.tool_call("task_scope_update", closure_arguments(m), raw_id="raw-closure"))
    script.append(h.answer(answer))
    return LazyProvider(script)


async def run_main(m: MilestoneEnv, provider) -> dict:  # type: ignore[no-untyped-def]
    return await h.run_capture(m.gate, provider)


def last_answer(m: MilestoneEnv) -> str:
    from simple_harness.contracts.messages import MessageRole

    for message in reversed(m.gate.context.messages):
        if message.role is MessageRole.ASSISTANT and str(message.content or "").strip():
            return str(message.content)
    return ""


async def settle_terminal(m: MilestoneEnv, *, closure_adapter, binding: dict[str, Any] | None = None, endpoint: str | None = ENDPOINT):  # type: ignore[no-untyped-def]
    """SDK terminal observed → drain → closure fallback (only if still dirty) → Host terminal (+ outbox)."""

    from deskpet.execution.foreground_runtime import SqliteSdkTerminalObserver

    record = {**(binding or BINDING), "run_id": m.run_id}
    facts = ch.FakeRunFacts(m.run_id, last_answer=last_answer(m) or None, binding=record)
    observed = await SqliteSdkTerminalObserver(str(m.db_path), ch._SdkIngress(facts.state), facts).observe(
        host_run_id=m.admission.host_run_id, sdk_run_id=m.run_id, subject=SUBJECT,
        owner_id=m.admission.owner_id, generation=m.admission.generation,
    )
    assert observed is not None, "SDK terminal must be observable"
    fallback, _ = ch.build_fallback(m, facts, closure_adapter, clock=time.time)
    settlement = await ch.settle(m, fallback)
    receipt = await record_terminal(m, observed, binding=record, endpoint=endpoint)
    return observed, settlement, receipt


def closure_receipts(m: MilestoneEnv) -> list[tuple]:
    return rows(m.db_path, "SELECT sdk_run_id,outcome,plan_id,reason_code FROM task_scope_closure_receipts WHERE task_scope_id=? ORDER BY rowid", m.scope_id)


def analysis_attempts(m: MilestoneEnv) -> list[tuple]:
    return post_turn_attempts(m.db_path)


def transcript_path(lane: str, started: float) -> Path:
    root = Path(__file__).resolve().parents[3] / ".local-test-evidence" / lane
    root.mkdir(parents=True, exist_ok=True)
    return root / f"run-{int(started)}.json"


def dump_transcript(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    assert "api_key" not in text.lower() or '"api_key"' not in text, "api_key must never reach the transcript"
    path.write_text(text, encoding="utf-8")
