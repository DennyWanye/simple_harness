# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""fault-matrix lane `taskscope-init-binding` runner（S5b Task 6b 实装）。

terminal oracle 见 fixtures/fault-matrix.json：one writable primary conversation；no half TaskScope；
exact binding revision。runner_contract 输出由 `_runner_contract.emit` 统一产生。

S4 六条 seam 用真实 state.db + 生产 store 的提交边界钩子（`fault_hook` / `fault_inject`，与 S4
kill/retry 单测同一命名）：kill → 零半状态（before/after hash 守恒）→ replay 收敛 → 再 replay 幂等。
S5b 五条 seam（effect gate，Task 1/6 场景以 lane runner 形式复用）：稳定拒绝 / 整 Run 故障 → 零写入、
binding revision 精确不变、durable FAILED 携带稳定码；重放幂等。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.execution import RunState
from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.execution.foreground_runtime import SqliteSdkTerminalObserver
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.sdk_adapters.run_faults import RunFaultMemo
from deskpet.task_scope.provisioning import (
    TaskScopeProvisioner,
    TaskScopeProvisionRequest,
)
from deskpet.task_scope.store import (
    CanonicalTaskScopeStore,
    TaskScopeConflict,
    TaskScopeNotFound,
)
from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore
from tests.execution import test_foreground_queue as fq
from tests.faults._runner_contract import LANE_SEAMS, emit, new_root_run_id, state_hash
from tests.sdk_adapters import s5b_effect_gate_harness as h
from tests.sdk_adapters.s5b_closure_harness import OneShot
from tests.task_scope import test_workspace_bindings as wb

LANE = "taskscope-init-binding"
SUBJECT = "actor-1"
SCOPE = "scope-1"
TITLE = "Memory SDK / Upgrade"
FIXTURE = Path(__file__).parents[3] / "testcase" / "human-memory-program" / "fixtures" / "fault-matrix.json"
SPEC = FIXTURE.parents[1] / "s5b-effect-closure-memory-verification-spec.json"

INIT_TABLES = (
    "human_memory_primary_conversations",
    "human_memory_init_receipts",
    "task_scopes",
    "task_scope_canonical_revisions",
    "task_scope_heads",
    "task_scope_provisions",
    "task_scope_provision_receipts",
    "task_workspace_binding_revisions",
    "task_workspace_binding_heads",
    "task_workspace_binding_roots",
    "task_workspace_binding_grants",
    "task_scope_checkpoints",
)
GATE_TABLES = (*INIT_TABLES, "effect_gate_rejections")
TERMINAL_TABLES = (
    "foreground_run_heads",
    "foreground_terminal_receipts",
    "task_scope_execution_ingest_receipts",
    "task_scope_events",
)


def _rows(db_path: Path, sql: str, *params):
    with sqlite3.connect(db_path) as db:
        return db.execute(sql, params).fetchall()


def _count(db_path: Path, table: str, where: str = "1=1") -> int:
    return int(_rows(db_path, f"SELECT COUNT(*) FROM {table} WHERE {where}")[0][0])


def _root_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _combined(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


async def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(db_path)
    return db_path


def _assert_one_primary(db_path: Path) -> None:
    assert _count(db_path, "human_memory_primary_conversations", f"subject='{SUBJECT}' AND writable=1") == 1
    assert _count(db_path, "human_memory_init_receipts", f"subject='{SUBJECT}'") == 1


def _binding_store(db_path: Path, tmp_path: Path, manual) -> WorkspaceBindingAuthorityStore:  # type: ignore[no-untyped-def]
    configured = tmp_path / "configured"
    configured.mkdir(exist_ok=True)
    return WorkspaceBindingAuthorityStore(
        db_path, configured_workspace_root=configured, clock_millis=lambda: 1500,
        manual_authorization_authority=manual,
    )


async def _granted(store, manual, root: Path, *, index: int, revision: int):  # type: ignore[no-untyped-def]
    root.mkdir(exist_ok=True)
    proposal = wb._proposal(root, proposal_id=f"proposal-{index}", revision=revision, key=f"append-{index}")
    _, _, grant = await wb._manual_grant(store, proposal, nonce=f"nonce-{index}", authority=manual)
    return proposal, grant


def _provision_request() -> TaskScopeProvisionRequest:
    return TaskScopeProvisionRequest(
        task_scope_id=SCOPE, title=TITLE, idempotency_key="provision-1", mode="managed",
        provenance="host_managed_policy",
    )


def _checkpoint_meta(head: str = "abc") -> dict:
    return {"repo": "/repo", "branch": "main", "head": head, "dirty": False, "files": ["a.py"], "tests": ["pytest"],
            "artifacts": [], "next_action": "continue"}


# --- S4 seams -----------------------------------------------------------------------


async def _primary_conversation_create(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """主对话创建事务（primary row + init receipt 同事务）提交前 kill → 零行；重放 → 恰一条可写主对话 +
    一条 receipt；24 路并发重放 / ack 丢失重放 → 同 receipt、hash 不变。"""
    db_path = await _fresh_db(tmp_path)
    before = state_hash(db_path, INIT_TABLES)
    with pytest.raises(RuntimeError, match="injected:initialize_subject.before_commit"):
        await HumanMemoryProgramStore(db_path, fault_hook=OneShot("initialize_subject.before_commit")).initialize_subject(SUBJECT)
    assert state_hash(db_path, INIT_TABLES) == before
    assert _count(db_path, "human_memory_primary_conversations") == 0
    assert _count(db_path, "human_memory_init_receipts") == 0
    receipt = await HumanMemoryProgramStore(db_path).initialize_subject(SUBJECT)
    converged = state_hash(db_path, INIT_TABLES)
    assert converged != before
    _assert_one_primary(db_path)
    replays = await asyncio.gather(*(HumanMemoryProgramStore(db_path).initialize_subject(SUBJECT) for _ in range(24)))
    assert set(replays) == {receipt}
    with pytest.raises(RuntimeError, match="injected:initialize_subject.after_commit"):
        await HumanMemoryProgramStore(db_path, fault_hook=OneShot("initialize_subject.after_commit")).initialize_subject(SUBJECT)
    assert await HumanMemoryProgramStore(db_path).initialize_subject(SUBJECT) == receipt
    assert state_hash(db_path, INIT_TABLES) == converged
    _assert_one_primary(db_path)
    return before, converged, {"primary_conversation_id": receipt.primary_conversation_id}


async def _task_home_create(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """task home 物化（目录已建、committed receipt 未落库）时 kill → 无 receipt、无 binding；重放 → 同一目录、
    恰一条 committed provision + 一条 receipt、managed root 下恰一个目录；再重放 → 同 receipt、hash 不变。"""
    db_path = await _fresh_db(tmp_path)
    await CanonicalTaskScopeStore(db_path).create_task_scope(task_scope_id=SCOPE, subject=SUBJECT, title=TITLE)
    managed_root = tmp_path / "managed"
    provisioner = TaskScopeProvisioner(db_path, managed_workspace_root=managed_root)
    before = state_hash(db_path, INIT_TABLES)
    with pytest.raises(RuntimeError, match="injected:after_filesystem_create"):
        await provisioner.provision(_provision_request(), fault_inject=OneShot("after_filesystem_create"))
    assert _count(db_path, "task_scope_provision_receipts") == 0
    assert _count(db_path, "task_scope_provisions", "state='committed'") == 0
    assert _count(db_path, "task_workspace_binding_revisions") == 0
    receipt = await provisioner.provision(_provision_request())
    converged = state_hash(db_path, INIT_TABLES)
    assert converged != before
    assert await provisioner.provision(_provision_request()) == receipt
    assert state_hash(db_path, INIT_TABLES) == converged
    assert Path(receipt.task_home).parent == managed_root.resolve()
    assert (Path(receipt.task_home) / ".simple-harness-provision.json").is_file()
    assert len([p for p in managed_root.iterdir() if not p.name.startswith(".")]) == 1
    assert _count(db_path, "task_scope_provisions") == 1 and _count(db_path, "task_scope_provisions", "state='committed'") == 1
    assert _count(db_path, "task_scope_provision_receipts") == 1
    assert _count(db_path, "task_workspace_binding_revisions") == 0
    _assert_one_primary(db_path)
    return before, converged, {"task_home": receipt.task_home}


async def _taskscope_row(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """TaskScope 行事务（task_scopes + revision 1 + head + projection source 同事务）提交前 kill → 零半 TaskScope
    （主对话仍恰一条）；重放 → revision 1；同 id 不同 title → 稳定拒绝；ack 丢失重放 → 同 receipt、hash 不变。"""
    db_path = await _fresh_db(tmp_path)
    await HumanMemoryProgramStore(db_path).initialize_subject(SUBJECT)
    before = state_hash(db_path, INIT_TABLES)
    with pytest.raises(RuntimeError, match="injected:create_task_scope.before_commit"):
        await CanonicalTaskScopeStore(db_path, fault_hook=OneShot("create_task_scope.before_commit")).create_task_scope(
            task_scope_id=SCOPE, subject=SUBJECT, title=TITLE
        )
    assert state_hash(db_path, INIT_TABLES) == before
    for table in ("task_scopes", "task_scope_heads", "task_scope_canonical_revisions"):
        assert _count(db_path, table) == 0
    _assert_one_primary(db_path)
    receipt = await CanonicalTaskScopeStore(db_path).create_task_scope(task_scope_id=SCOPE, subject=SUBJECT, title=TITLE)
    assert receipt.revision == 1
    converged = state_hash(db_path, INIT_TABLES)
    assert converged != before
    assert await CanonicalTaskScopeStore(db_path).create_task_scope(task_scope_id=SCOPE, subject=SUBJECT, title=TITLE) == receipt
    with pytest.raises(TaskScopeConflict, match="task_scope_identity_conflict"):
        await CanonicalTaskScopeStore(db_path).create_task_scope(task_scope_id=SCOPE, subject=SUBJECT, title="other")
    assert state_hash(db_path, INIT_TABLES) == converged
    # ack 丢失形态（第二个 scope 首次提交后 kill）：提交已持久，重放同 receipt、hash 不变。
    with pytest.raises(RuntimeError, match="injected:create_task_scope.after_commit"):
        await CanonicalTaskScopeStore(db_path, fault_hook=OneShot("create_task_scope.after_commit")).create_task_scope(
            task_scope_id="scope-2", subject=SUBJECT, title=TITLE
        )
    durable = state_hash(db_path, INIT_TABLES)
    second = await CanonicalTaskScopeStore(db_path).create_task_scope(task_scope_id="scope-2", subject=SUBJECT, title=TITLE)
    assert second.revision == 1 and state_hash(db_path, INIT_TABLES) == durable
    assert _count(db_path, "task_scopes") == 2 and _count(db_path, "task_scope_canonical_revisions") == 2
    _assert_one_primary(db_path)
    return before, durable, {"revision": 1}


async def _binding_revision(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """binding revision 追加在 revision 行插入前 / 提交前 kill → 无 head（exact revision 不变）；重放 → 恰 revision 1；
    同 proposal 再重放 → 同 receipt；base_revision=1 追加 → revision 2 且 parent 精确；过期 base_revision → 稳定拒绝。"""
    db_path = await _fresh_db(tmp_path)
    await CanonicalTaskScopeStore(db_path).create_task_scope(task_scope_id=SCOPE, subject=SUBJECT, title=TITLE)
    manual = wb._DurableManualAuthority()
    store = _binding_store(db_path, tmp_path, manual)
    proposal, grant = await _granted(store, manual, tmp_path / "root-a", index=1, revision=0)
    before = state_hash(db_path, INIT_TABLES)
    for point in ("before_binding_revision_insert", "before_binding_commit"):
        with pytest.raises(RuntimeError, match=f"injected:{point}"):
            await store.append_binding(proposal, grant, fault_inject=OneShot(point))
        assert state_hash(db_path, INIT_TABLES) == before
        with pytest.raises(TaskScopeNotFound):
            await store.current_receipt(SCOPE)
    receipt1 = await store.append_binding(proposal, grant)
    assert receipt1.binding_set_revision == 1
    converged = state_hash(db_path, INIT_TABLES)
    assert converged != before
    assert await store.append_binding(proposal, grant) == receipt1
    assert state_hash(db_path, INIT_TABLES) == converged
    proposal2, grant2 = await _granted(store, manual, tmp_path / "root-b", index=2, revision=1)
    receipt2 = await store.append_binding(proposal2, grant2)
    assert receipt2.binding_set_revision == 2 and receipt2.parent_receipt_id == receipt1.receipt_id
    stale, stale_grant = await _granted(store, manual, tmp_path / "root-c", index=3, revision=1)
    with pytest.raises(TaskScopeConflict, match="base_revision_conflict"):
        await store.append_binding(stale, stale_grant)
    after = state_hash(db_path, INIT_TABLES)
    assert (await store.current_receipt(SCOPE)) == receipt2
    assert await store.exact_receipt(
        task_scope_id=SCOPE, binding_set_revision=1, binding_set_receipt_id=receipt1.receipt_id,
        binding_set_receipt_hash=receipt1.receipt_hash,
    ) == receipt1
    assert _count(db_path, "task_workspace_binding_revisions") == 2
    assert state_hash(db_path, INIT_TABLES) == after
    return before, after, {"revision": 2}


async def _checkpoint(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """checkpoint 事务提交前 kill → 零 checkpoint；重放 → 恰一条；同 id 不同内容 → 稳定拒绝；ack 丢失重放 → 同 receipt。"""
    db_path = await _fresh_db(tmp_path)
    plain = CanonicalTaskScopeStore(db_path)
    await plain.create_task_scope(task_scope_id=SCOPE, subject=SUBJECT, title=TITLE)
    before = state_hash(db_path, INIT_TABLES)
    with pytest.raises(RuntimeError, match="injected:checkpoint.before_commit"):
        await CanonicalTaskScopeStore(db_path, fault_hook=OneShot("checkpoint.before_commit")).create_checkpoint(
            checkpoint_id="checkpoint-1", task_scope_id=SCOPE, metadata=_checkpoint_meta()
        )
    assert state_hash(db_path, INIT_TABLES) == before
    assert _count(db_path, "task_scope_checkpoints") == 0
    receipt = await plain.create_checkpoint(checkpoint_id="checkpoint-1", task_scope_id=SCOPE, metadata=_checkpoint_meta())
    converged = state_hash(db_path, INIT_TABLES)
    assert converged != before
    assert await plain.create_checkpoint(checkpoint_id="checkpoint-1", task_scope_id=SCOPE, metadata=_checkpoint_meta()) == receipt
    with pytest.raises(TaskScopeConflict, match="checkpoint_id_hash_conflict"):
        await plain.create_checkpoint(checkpoint_id="checkpoint-1", task_scope_id=SCOPE, metadata=_checkpoint_meta("def"))
    assert state_hash(db_path, INIT_TABLES) == converged
    # ack 丢失形态（checkpoint-2 首次提交后 kill）：提交已持久，重放同 receipt、hash 不变。
    with pytest.raises(RuntimeError, match="injected:checkpoint.after_commit"):
        await CanonicalTaskScopeStore(db_path, fault_hook=OneShot("checkpoint.after_commit")).create_checkpoint(
            checkpoint_id="checkpoint-2", task_scope_id=SCOPE, metadata=_checkpoint_meta()
        )
    durable = state_hash(db_path, INIT_TABLES)
    second = await plain.create_checkpoint(checkpoint_id="checkpoint-2", task_scope_id=SCOPE, metadata=_checkpoint_meta())
    assert second.revision == receipt.revision and state_hash(db_path, INIT_TABLES) == durable
    assert _count(db_path, "task_scope_checkpoints") == 2
    return before, durable, {"checkpoint_revision": receipt.revision}


async def _commit_before_ack(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """init 链每一步提交后、ack 前 kill（主对话 → TaskScope 行 → task home → binding → checkpoint）：
    每步 kill 后的 hash 与重放后的 hash 相等（提交已持久、ack 丢失），重放返回同一 receipt，无重复行。"""
    db_path = await _fresh_db(tmp_path)
    before = state_hash(db_path, INIT_TABLES)
    steps: list[str] = []

    async def lost_ack(point: str, faulty, replay):  # type: ignore[no-untyped-def]
        with pytest.raises(RuntimeError, match=f"injected:{point}"):
            await faulty()
        durable = state_hash(db_path, INIT_TABLES)
        receipt = await replay()
        assert state_hash(db_path, INIT_TABLES) == durable
        assert await replay() == receipt
        steps.append(point)
        return receipt

    await lost_ack(
        "initialize_subject.after_commit",
        lambda: HumanMemoryProgramStore(db_path, fault_hook=OneShot("initialize_subject.after_commit")).initialize_subject(SUBJECT),
        lambda: HumanMemoryProgramStore(db_path).initialize_subject(SUBJECT),
    )
    scope_kwargs = {"task_scope_id": SCOPE, "subject": SUBJECT, "title": TITLE}
    await lost_ack(
        "create_task_scope.after_commit",
        lambda: CanonicalTaskScopeStore(db_path, fault_hook=OneShot("create_task_scope.after_commit")).create_task_scope(**scope_kwargs),
        lambda: CanonicalTaskScopeStore(db_path).create_task_scope(**scope_kwargs),
    )
    provisioner = TaskScopeProvisioner(db_path, managed_workspace_root=tmp_path / "managed")
    await lost_ack(
        "after_committed_receipt",
        lambda: provisioner.provision(_provision_request(), fault_inject=OneShot("after_committed_receipt")),
        lambda: provisioner.provision(_provision_request()),
    )
    manual = wb._DurableManualAuthority()
    store = _binding_store(db_path, tmp_path, manual)
    proposal, grant = await _granted(store, manual, tmp_path / "root-a", index=1, revision=0)
    binding = await lost_ack(
        "after_binding_commit",
        lambda: store.append_binding(proposal, grant, fault_inject=OneShot("after_binding_commit")),
        lambda: store.append_binding(proposal, grant),
    )
    assert binding.binding_set_revision == 1
    ckpt = {"checkpoint_id": "checkpoint-1", "task_scope_id": SCOPE, "metadata": _checkpoint_meta()}
    await lost_ack(
        "checkpoint.after_commit",
        lambda: CanonicalTaskScopeStore(db_path, fault_hook=OneShot("checkpoint.after_commit")).create_checkpoint(**ckpt),
        lambda: CanonicalTaskScopeStore(db_path).create_checkpoint(**ckpt),
    )
    after = state_hash(db_path, INIT_TABLES)
    assert after != before
    _assert_one_primary(db_path)
    for table in ("task_scopes", "task_scope_provisions", "task_scope_provision_receipts", "task_workspace_binding_revisions", "task_scope_checkpoints"):
        assert _count(db_path, table) == 1, table
    assert (await store.current_receipt(SCOPE)).binding_set_revision == 1
    return before, after, {"steps": steps}


# --- S5b seams（effect gate / Run 故障 / Auto）-------------------------------------------


class _FailedIngress:
    async def wait_idle(self, run_id: str) -> None:
        del run_id

    def query(self, run_id: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(state=SimpleNamespace(value="failed"))


class _FailedStack:
    def read_run_terminal_evidence(self, run_id: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            run_id=run_id, state="failed", event_id=f"sdk-terminal:{run_id}", event_hash="9" * 64,
            occurred_at=101.0, error_code="driver_failed",
        )


async def _durable_failed(queue_dir: Path, sdk_run_id: str, memo: RunFaultMemo, expected_code: str) -> tuple[Path, str]:
    """整 Run 故障的 durable FAILED：SDK 只暴露 driver_failed，Host memo 的稳定码进 run_terminal
    public_payload.error_code；record_sdk_terminal 重放（同 idempotency_key）→ 同 receipt；memo 在终态持久后释放。"""
    queue_db, primary_id, clock = await fq._ready(queue_dir)
    store = ForegroundQueueStore(queue_db, clock=clock)
    await fq._enqueue(store, primary_id, 1)
    admission = await fq._claim_and_bind(store, sdk_run_id=sdk_run_id)
    await store.record_sdk_started(
        host_run_id=admission.host_run_id, sdk_run_id=sdk_run_id, owner_id=admission.owner_id,
        generation=admission.generation, sdk_event_id=f"sdk-start:{sdk_run_id}", idempotency_key=f"sdk-start:{sdk_run_id}",
    )
    assert memo.read(sdk_run_id) == expected_code
    observed = await SqliteSdkTerminalObserver(str(queue_db), _FailedIngress(), _FailedStack(), run_fault_memo=memo).observe(  # type: ignore[arg-type]
        host_run_id=admission.host_run_id, sdk_run_id=sdk_run_id, subject=fq.SUBJECT,
        owner_id=admission.owner_id, generation=admission.generation,
    )
    assert observed is not None and observed.terminal_state is RunState.FAILED
    kwargs = {
        "host_run_id": admission.host_run_id, "sdk_run_id": sdk_run_id, "owner_id": admission.owner_id,
        "generation": admission.generation, "terminal_state": observed.terminal_state,
        "sdk_event_id": observed.sdk_event_id, "sdk_event_hash": observed.sdk_event_hash, "idempotency_key": "terminal-fault",
    }
    terminal = await store.record_sdk_terminal(**kwargs)
    assert terminal.terminal_state is RunState.FAILED
    assert _rows(queue_db, "SELECT current_state FROM foreground_run_heads WHERE host_run_id=?", admission.host_run_id) == [("FAILED",)]
    [(payload_json,)] = _rows(
        queue_db,
        "SELECT e.payload_json FROM task_scope_execution_ingest_receipts r JOIN task_scope_events e ON e.event_id=r.event_id "
        "WHERE r.run_id=? AND r.evidence_kind='run_terminal'",
        sdk_run_id,
    )
    assert json.loads(payload_json)["public_payload"]["error_code"] == expected_code
    assert memo.read(sdk_run_id) is None
    durable = state_hash(queue_db, TERMINAL_TABLES)
    assert await store.record_sdk_terminal(**kwargs) == terminal
    assert state_hash(queue_db, TERMINAL_TABLES) == durable
    return queue_db, durable


async def _run_fault(tmp_path: Path, *, scope_roots: int, route: dict, expected_code: str) -> tuple[str, str, dict]:
    """Task 1/6 场景复用：standalone / 多 root 下强制调用 write_file → 整 Run 故障（稳定码），零写入、
    snapshot tools 两轮均不含写工具、binding revision 精确不变、init 表 hash 守恒；durable FAILED + 重放幂等。"""
    env = await h.build_env(tmp_path / "gate")
    scope, root = await h.make_bound_scope(env, "a", "root-a")
    roots = [root]
    for index in range(2, scope_roots + 1):
        extra = env.workspace_base / f"root-a{index}"
        extra.mkdir(parents=True)
        await h.bind_scope_root(env.db_path, scope, extra, base_revision=index - 1, tag=f"a{index}")
        roots.append(extra)
    h.freeze_run(env, task_scope_id=scope, workspace_root=root if scope_roots == 1 else None)
    head = await env.binding_store.current_receipt(scope)
    assert head.binding_set_revision == scope_roots == len(head.root_identity_hashes)
    canary = [_root_hash(r) for r in roots]
    init_before = state_hash(env.db_path, INIT_TABLES)
    queue_dir = tmp_path / "queue"
    provider = h.ScriptedProvider([
        h.tool_call("context_route", {**route, **({"task_scope_id": scope} if route["route"] != "direct_standalone" else {})}, raw_id="raw-route"),
        h.tool_call("write_file", {"path": "forced.txt", "content": "must not land"}, raw_id="raw-write"),
        h.answer("never reached"),
    ])
    out = await h.run_capture(env, provider)
    exc = out["exception"]
    assert exc is not None and getattr(exc, "code", None) == expected_code
    exposed = h.provider_tool_names(provider)
    assert len(exposed) == 2 and all("write_file" not in names for names in exposed)
    assert env.effects.calls == ["context_route"] and env.effects.write_file_calls == []
    assert [_root_hash(r) for r in roots] == canary and not any((r / "forced.txt").exists() for r in roots)
    assert h.tool_messages(env, "write_file") == []
    # exact binding revision / no half TaskScope：init 表 hash 守恒。
    assert (await env.binding_store.current_receipt(scope)) == head
    assert state_hash(env.db_path, INIT_TABLES) == init_before
    queue_db, durable = await _durable_failed(queue_dir, h.RUN.value, env.memo, expected_code)
    assert state_hash(env.db_path, INIT_TABLES) == init_before
    before = _combined(init_before, "no-terminal")
    after = _combined(init_before, durable)
    return before, after, {"code": expected_code, "binding_revision": head.binding_set_revision, "queue_db": str(queue_db)}


async def _run_fault_route_authority_missing(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    return await _run_fault(
        tmp_path, scope_roots=1, route={"route": "direct_standalone"},
        expected_code="sdk_task_execution_route_authority_missing",
    )


async def _run_fault_root_authority_ambiguous(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    return await _run_fault(
        tmp_path, scope_roots=2, route={"route": "resume_existing"},
        expected_code="sdk_task_execution_root_authority_ambiguous",
    )


async def _run_fault_catalog_policy_unavailable(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """hidden 工具（deferred，未 activate）的 execution_policy → RuntimeToolCatalogError
    ``catalog_execution_policy_unavailable`` 进 Run 故障备忘（首码优先，重复读取稳定）；durable FAILED 携带稳定码。"""
    from simple_harness import RunId
    from simple_harness.tools import RuntimeToolCatalogError

    from deskpet.sdk_adapters.tool_authority import SdkRunToolAuthorityRegistry
    from tests.sdk_adapters import test_tool_authority as ta

    sdk_run_id = h.RUN.value
    memo = RunFaultMemo()
    registry = SdkRunToolAuthorityRegistry(run_fault_sink=memo)
    ta._prepare(registry, sdk_run_id, deferred_names=("read_file",))
    exposure = registry.resolve_exposure(RunId(sdk_run_id))
    exposure.restore(RunId(sdk_run_id), None)
    assert [spec.name for spec in exposure.provider_specs(RunId(sdk_run_id))] == ["tool_search"]
    code = "catalog_execution_policy_unavailable"
    for _ in range(2):
        with pytest.raises(RuntimeToolCatalogError) as hidden:
            exposure.execution_policy(RunId(sdk_run_id), "read_file")
        assert hidden.value.code == code and memo.read(sdk_run_id) == code
    exposure.execution_policy(RunId(sdk_run_id), "tool_search")
    queue_db, durable = await _durable_failed(tmp_path / "queue", sdk_run_id, memo, code)
    # no half TaskScope：队列库里 admission scope 恰一条、binding 零行。
    assert _count(queue_db, "task_scopes") == 1 and _count(queue_db, "task_workspace_binding_revisions") == 0
    return _combined("no-terminal"), _combined(durable), {"code": code}


async def _frozen_root_split(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """Run 冻结单根（revision 1）后，Run 内 Manual 追加第二根 → head revision 2 与冻结口径分裂：后续 PROJECT_EFFECT
    稳定拒绝 ``workspace_binding_receipt_superseded``，对同 route receipt sticky（换冻结根也不解封），
    rejection memo 首写胜出恰一行；两根零写入、canary 不变；revision 1 exact receipt 仍精确可解析；重放 hash 不变。"""
    from deskpet.sdk_adapters.effect_gate import EFFECT_GATE_STICKY_REASON
    from tests.sdk_adapters.test_effect_gate import _code, _context, _routed_write

    env, scope, root, envelope = await _routed_write(tmp_path / "gate")
    assert (root / "a.txt").read_text(encoding="utf-8") == "alpha"
    canary = _root_hash(root)
    before = state_hash(env.db_path, GATE_TABLES)
    root2 = env.workspace_base / "root-a2"
    root2.mkdir(parents=True)
    await h.bind_scope_root(env.db_path, scope, root2, base_revision=1, tag="a2")
    head = await env.binding_store.current_receipt(scope)
    assert head.binding_set_revision == 2 and len(head.root_identity_hashes) == 2
    assert envelope.binding_set_revision == 1
    assert _code(await env.gate.verify(_context(envelope), "write_file")) == "workspace_binding_receipt_superseded"
    assert _code(await env.gate.verify(_context(envelope), "write_file")) == EFFECT_GATE_STICKY_REASON
    h.freeze_run(env, task_scope_id=scope, workspace_root=root2)
    assert _code(await env.gate.verify(_context(envelope), "write_file")) == EFFECT_GATE_STICKY_REASON
    rejections = _rows(env.db_path, "SELECT sdk_run_id,route_receipt_id,reason_code FROM effect_gate_rejections")
    assert rejections == [(h.RUN.value, envelope.route_receipt_id, "workspace_binding_receipt_superseded")]
    assert _root_hash(root) == canary and not (root2 / "a.txt").exists()
    exact = await env.binding_store.exact_receipt(
        task_scope_id=scope, binding_set_revision=1, binding_set_receipt_id=head.parent_receipt_id,
        binding_set_receipt_hash=head.parent_receipt_hash,
    )
    assert exact.binding_set_revision == 1 and exact.receipt_id == envelope.binding_set_receipt_id
    converged = state_hash(env.db_path, GATE_TABLES)
    assert converged != before
    assert _code(await env.gate.verify(_context(envelope), "write_file")) == EFFECT_GATE_STICKY_REASON
    assert state_hash(env.db_path, GATE_TABLES) == converged
    assert _count(env.db_path, "task_workspace_binding_revisions", f"task_scope_id='{scope}'") == 2
    return before, converged, {"binding_revision": 2, "rejections": 1}


async def _auto_destructive(tmp_path: Path, run_id: str) -> tuple[str, str, dict]:
    """Auto 模式 + DESTRUCTIVE（inventory 冻结 EffectClass）→ REQUIRE_USER，候选 grant 来源 ``user``（绝不合成
    ``policy:auto``）；durable saga 恰一条 prepared；重放 prepare → 同判定、hash 不变；对照 reversible_local 走 auto。"""
    from simple_harness.tools import AuthorizationDecision

    from deskpet.product_state.authorization_saga import AuthorizationSagaRepository
    from deskpet.product_state.database import ProductStateDatabase
    from deskpet.product_state.task_grants import DurableTaskGrantAuthority
    from deskpet.sdk_adapters.authorization import ProductAuthorizationAdapter
    from deskpet.sdk_adapters.tool_authority import (
        SdkRunToolAuthorityRegistry,
        confirm_only_tool_names,
    )
    from tests.sdk_adapters import test_tool_authority as ta

    inventory = (
        ta._Inventory("write_file", "async", "write_file"),
        ta._Inventory("purge_dir", "async", "write_file", effect_class="destructive"),
        ta._Inventory("run_shell", "async", "shell"),
    )
    authorities = SdkRunToolAuthorityRegistry()
    authority = authorities.prepare_run(
        run_id=run_id, session_id="session-a", request_id=f"request-{run_id}", root_run_id=run_id,
        task_scope_id=SCOPE, workspace_root=None, catalog=ta._catalog_for(ta._write_specs(), inventory), inventory=inventory,
    )
    assert authority.specs["purge_dir"].effect_class == "destructive"
    assert confirm_only_tool_names(authority) == ("purge_dir", "run_shell")
    policy = await ta._auto_policy(authorities)
    database = ProductStateDatabase(tmp_path / "product.db")
    database.initialize()
    adapter = ProductAuthorizationAdapter(
        AuthorizationSagaRepository(database, owner_id="fault-lane"), policy=policy,
        identity_factory=policy.identity_factory,
        grant_authority=DurableTaskGrantAuthority(database, policy_generation_provider=policy.current_policy_generation),
        grant_factory=policy.grant_factory, clock=lambda: 100.0,
    )
    saga_db = tmp_path / "product.db"
    before = state_hash(saga_db, ("authorization_sagas",))
    prepared = ta._effect_for(run_id, "purge_dir", effect_id="effect-purge")
    pending = await adapter.prepare(prepared)
    assert pending.decision is AuthorizationDecision.REQUIRE_USER
    assert pending.reason_code == "product_policy_user_confirmation"
    assert pending.request is not None and pending.request.metadata["grant_source"] == "user"
    facts = policy.facts_for(prepared)
    assert facts.grant.source == "user" and facts.plan.authorization_origin == "explicit_decision"
    assert _rows(saga_db, "SELECT state FROM authorization_sagas") == [("prepared",)]
    converged = state_hash(saga_db, ("authorization_sagas",))
    assert converged != before
    # 重放（同 effect）：同判定、无 auto grant、saga 不变。
    replay = await adapter.prepare(prepared)
    assert replay.decision is AuthorizationDecision.REQUIRE_USER and replay.request is not None
    assert replay.request.metadata["grant_source"] == "user"
    assert state_hash(saga_db, ("authorization_sagas",)) == converged
    # 对照：reversible_local 的 write_file 走既有 auto 策略（policy:auto）；DESTRUCTIVE 永不。
    allowed = await policy.decide(ta._effect_for(run_id, "write_file", effect_id="effect-write"), request=None)
    assert allowed.decision is AuthorizationDecision.ALLOW
    assert policy.facts_for(ta._effect_for(run_id, "write_file", effect_id="effect-write")).grant.source == "policy:auto"
    assert state_hash(saga_db, ("authorization_sagas",)) == converged
    return before, converged, {"decision": "REQUIRE_USER", "grant_source": "user"}


SEAM_RUNNERS = {
    "primary-conversation-create": _primary_conversation_create,
    "task-home-create": _task_home_create,
    "taskscope-row": _taskscope_row,
    "binding-revision": _binding_revision,
    "checkpoint": _checkpoint,
    "commit-before-ack": _commit_before_ack,
    "run-fault-route-authority-missing": _run_fault_route_authority_missing,
    "run-fault-root-authority-ambiguous": _run_fault_root_authority_ambiguous,
    "run-fault-catalog-policy-unavailable": _run_fault_catalog_policy_unavailable,
    "frozen-root-split": _frozen_root_split,
    "auto-destructive": _auto_destructive,
}


def test_fixture_pin_and_lane_seams_match_spec() -> None:
    """冻结 oracle：fixture sha256 与 spec pin 一致；lane runner 覆盖 fixture 声明的全部 seam（S5b 增量为超集）。"""
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    pins = {value for value in _walk(spec, "fault_matrix_sha256")}
    assert pins == {hashlib.sha256(FIXTURE.read_bytes()).hexdigest()}
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    [lane] = [item for item in fixture["lanes"] if item["id"] == LANE]
    assert set(lane["seams"]) <= set(LANE_SEAMS[LANE]) == set(SEAM_RUNNERS)
    assert lane["terminal_oracle"] == "one writable primary conversation; no half TaskScope; exact binding revision"


def _walk(value, key: str):  # type: ignore[no-untyped-def]
    if isinstance(value, dict):
        for name, item in value.items():
            if name == key:
                yield item
            else:
                yield from _walk(item, key)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item, key)


@pytest.mark.asyncio
@pytest.mark.parametrize("seam", list(LANE_SEAMS[LANE]))
async def test_seam_kill_replay_converges(seam: str, tmp_path: Path) -> None:
    root_run_id = new_root_run_id(LANE)
    before, after, extra = await SEAM_RUNNERS[seam](tmp_path, root_run_id)
    assert before != after
    emit(LANE, root_run_id, before, after, {"seam": seam, **extra})
