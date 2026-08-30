# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from simple_harness import CallId, RequestId, RunId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.runtime import (
    AgentIdentity,
    ConversationContextBounds,
    ConversationContextRequest,
    MemoryRecallBounds,
    MemoryRecallRequest,
    MemoryScopeKind,
    MemoryScopeRef,
)
from simple_harness.tools import CancellationToken, ToolContext
from simple_harness_memory import (
    MemoryIdempotencyConflict,
    MemoryManager,
    MemoryOwnershipConflict,
    MemoryPrincipal,
)

from deskpet.memory.identity import (
    MemorySessionRebind,
    ProductMemoryIdentityResolver,
    ValidatedLocalMemoryIdentityAuthority,
)
from deskpet.memory.session_db import SessionDB
from deskpet.migrations.sdk_v4_cutover import recover_product_sdk_pair_v4
from deskpet.sdk_adapters.context_provider import ProductConversationContextProvider
from deskpet.sdk_adapters.context_source import ProductContextSourceRepository
from deskpet.sdk_adapters.memory_faults import (
    DevMemoryFaultPort,
    wrap_dev_memory_faults,
)
from deskpet.sdk_adapters.sdk_candidate import (
    SDK_MEMORY_WHEEL_SHA256,
    SDK_WHEEL_SHA256,
)
from deskpet.tool_catalog.providers import ToolCatalogDependencies, _dynamic_handlers


@pytest.mark.asyncio
async def test_identity_binding_is_stable_and_session_cannot_rebind(tmp_path) -> None:
    state = tmp_path / "state.db"
    session = SessionDB(state)
    await session.initialize()
    await session.ensure_session("session-a")
    resolver = ProductMemoryIdentityResolver(state)
    first = await resolver.bind(session_id="session-a", trusted_actor_id="actor-a")
    refreshed = await resolver.bind(session_id="session-a", trusted_actor_id="actor-a")
    assert refreshed == first
    with pytest.raises(MemorySessionRebind):
        await resolver.bind(session_id="session-a", trusted_actor_id="actor-b")
    await session.close()


@pytest.mark.asyncio
async def test_validated_identity_is_stable_across_same_userdata_restart(tmp_path) -> None:
    state = tmp_path / "state.db"
    user_data = tmp_path / "user-data"
    session = SessionDB(state)
    await session.initialize()
    await session.ensure_session("session-a")

    first = await ValidatedLocalMemoryIdentityAuthority(
        state,
        user_data_dir=user_data,
    ).bind(session_id="session-a")
    restarted = await ValidatedLocalMemoryIdentityAuthority(
        state,
        user_data_dir=user_data,
    ).bind(session_id="session-a")

    assert restarted == first
    assert first.actor_id != "legacy_local_profile"
    assert len(first.actor_id) == 64
    await session.close()


@pytest.mark.asyncio
async def test_validated_identity_ignores_provider_api_key_model_and_payload_spoof(
    monkeypatch,
    tmp_path,
) -> None:
    state = tmp_path / "state.db"
    user_data = tmp_path / "user-data"
    session = SessionDB(state)
    await session.initialize()
    await session.ensure_session("session-a")
    await session.ensure_session("session-b")

    class ProductAuthOnly:
        async def current_snapshot(self):
            return {
                "mode": "local",
                "user_id": None,
                "actor_id": "payload-spoof",
                "model": "model-spoof",
                "api_key": "api-key-spoof",
            }

    authority = ValidatedLocalMemoryIdentityAuthority(
        state,
        user_data_dir=user_data,
        auth_provider=ProductAuthOnly(),
    )
    first = await authority.bind(session_id="session-a")
    monkeypatch.setenv("DESKPET_CLOUD_API_KEY", "changed-provider-key")
    monkeypatch.setenv("DESKPET_LLM_MODEL", "changed-provider-model")
    second = await authority.bind(session_id="session-b")

    assert second.actor_id == first.actor_id
    assert second.household_id == first.household_id
    assert second.actor_id not in {
        "payload-spoof",
        "model-spoof",
        "api-key-spoof",
        "changed-provider-key",
        "legacy_local_profile",
    }
    await session.close()


@pytest.mark.asyncio
async def test_validated_identity_isolated_across_userdata_roots(tmp_path) -> None:
    state = tmp_path / "state.db"
    session = SessionDB(state)
    await session.initialize()
    await session.ensure_session("session-a")
    await session.ensure_session("session-b")

    first = await ValidatedLocalMemoryIdentityAuthority(
        state,
        user_data_dir=tmp_path / "user-data-a",
    ).bind(session_id="session-a")
    second = await ValidatedLocalMemoryIdentityAuthority(
        state,
        user_data_dir=tmp_path / "user-data-b",
    ).bind(session_id="session-b")

    assert second.deployment_id == first.deployment_id
    assert second.actor_id != first.actor_id
    assert second.household_id != first.household_id
    await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity_contents", "snapshot"),
    [
        ("not-json", {"mode": "local", "user_id": None}),
        (
            json.dumps({"schema_version": 1, "local_identity_id": "not-a-uuid"}),
            {"mode": "local", "user_id": None},
        ),
        (None, {"mode": "local", "user_id": "spoofed-user"}),
        (None, {"mode": "relay", "user_id": "spoofed-user"}),
    ],
)
async def test_invalid_identity_or_auth_snapshot_fails_before_llm(
    tmp_path,
    identity_contents,
    snapshot,
) -> None:
    state = tmp_path / "state.db"
    user_data = tmp_path / "user-data"
    user_data.mkdir()
    session = SessionDB(state)
    await session.initialize()
    await session.ensure_session("session-a")
    if identity_contents is not None:
        identity_path = user_data / "companion-local-identity.json"
        identity_path.write_text(
            identity_contents,
            encoding="utf-8",
        )
        identity_path.chmod(0o600)

    class InvalidAuth:
        async def current_snapshot(self):
            return snapshot

    llm = AsyncMock()
    authority = ValidatedLocalMemoryIdentityAuthority(
        state,
        user_data_dir=user_data,
        auth_provider=InvalidAuth(),
    )
    with pytest.raises((ValueError, RuntimeError, json.JSONDecodeError)):
        await authority.bind(session_id="session-a")
    llm.assert_not_awaited()
    await session.close()


@pytest.mark.asyncio
async def test_root_and_continuation_sources_are_immutable_and_independent(tmp_path) -> None:
    state = tmp_path / "state.db"
    session = SessionDB(state)
    await session.initialize()
    repository = ProductContextSourceRepository(state, retention_seconds=10)
    root_payload = {"provider_messages": [{"role": "user", "content": "root"}]}
    continuation_payload = {
        "provider_messages": [{"role": "user", "content": "continuation"}]
    }
    root_binding, root_ref = await repository.put_pending(
        root_run_id="run-a", continuation_id=None, payload=root_payload, now=1
    )
    continuation_binding, continuation_ref = await repository.put_pending(
        root_run_id="run-a",
        continuation_id="continuation-a",
        payload=continuation_payload,
        now=2,
    )
    assert root_binding != continuation_binding
    assert root_ref != continuation_ref
    assert (await repository.read(root_ref))[0] == root_payload
    with pytest.raises(RuntimeError, match="context_source_binding_conflict"):
        await repository.put_pending(
            root_run_id="run-a",
            continuation_id=None,
            payload=continuation_payload,
            now=3,
        )
    await session.close()


@pytest.mark.asyncio
async def test_context_provider_is_read_only_and_same_ref_same_hash(tmp_path) -> None:
    state = tmp_path / "state.db"
    session = SessionDB(state)
    await session.initialize()
    repository = ProductContextSourceRepository(state)
    payload = {"provider_messages": [{"role": "user", "content": "hello"}]}
    _binding, ref = await repository.put_pending(
        root_run_id="run-a", continuation_id=None, payload=payload
    )
    provider = ProductConversationContextProvider(repository)
    request = ConversationContextRequest(
        "prepare-a",
        AgentIdentity("deployment", "household", "actor", "session"),
        "run-a",
        None,
        ref,
        Message(MessageRole.USER, "hello"),
        ConversationContextBounds(),
    )
    first = await provider.prepare_once(request)
    second = await provider.prepare_once(request)
    assert first.result_hash == second.result_hash
    with sqlite3.connect(state) as db:
        assert db.execute(
            "SELECT status FROM sdk_context_source_bindings"
        ).fetchone()[0] == "pending"
    await session.close()


@pytest.mark.asyncio
async def test_context_gc_retains_active_or_inspector_failure_and_reclaims_orphan(tmp_path) -> None:
    state = tmp_path / "state.db"
    session = SessionDB(state)
    await session.initialize()
    repository = ProductContextSourceRepository(state, retention_seconds=1)
    payload = {"provider_messages": [{"role": "user", "content": "shared"}]}
    _first, ref = await repository.put_pending(
        root_run_id="run-active",
        continuation_id=None,
        payload=payload,
        now=1,
        lease_seconds=1,
    )
    _second, shared_ref = await repository.put_pending(
        root_run_id="run-orphan",
        continuation_id=None,
        payload=payload,
        now=1,
        lease_seconds=1,
    )
    assert shared_ref == ref
    assert await repository.cleanup(
        now=10,
        orphan_horizon_seconds=1,
        claim_exists=lambda root, _continuation: root == "run-active",
    ) == 1
    assert (await repository.read(ref))[0] == payload
    with sqlite3.connect(state) as db:
        assert db.execute(
            "SELECT ref_count FROM sdk_context_sources WHERE source_snapshot_ref=?",
            (ref,),
        ).fetchone()[0] == 1

    def broken_inspector(_root, _continuation):
        raise RuntimeError("inspector unavailable")

    assert await repository.cleanup(
        now=20, orphan_horizon_seconds=1, claim_exists=broken_inspector
    ) == 0
    assert await repository.cleanup(
        now=20,
        orphan_horizon_seconds=1,
        claim_exists=lambda _root, _continuation: False,
    ) >= 2
    with pytest.raises(KeyError):
        await repository.read(ref)
    await session.close()


@pytest.mark.asyncio
async def test_context_claim_and_terminal_run_release_all_bindings(tmp_path) -> None:
    state = tmp_path / "state.db"
    session = SessionDB(state)
    await session.initialize()
    repository = ProductContextSourceRepository(state)
    root_binding, _ = await repository.put_pending(
        root_run_id="run-claim",
        continuation_id=None,
        payload={"message": "root"},
    )
    continuation_binding, _ = await repository.put_pending(
        root_run_id="run-claim",
        continuation_id="continue-1",
        payload={"message": "next"},
    )

    await repository.mark_claimed(
        continuation_binding,
        claim_token="continue-1",
    )
    with sqlite3.connect(state) as db:
        row = db.execute(
            """SELECT status,claim_token,lease_expires_at
               FROM sdk_context_source_bindings WHERE binding_id=?""",
            (continuation_binding,),
        ).fetchone()
    assert row is not None
    assert row[0] == "claimed"
    assert row[1] == "continue-1"
    assert float(row[2]) > 0

    assert await repository.consume_run("run-claim") == 2
    with sqlite3.connect(state) as db:
        statuses = db.execute(
            """SELECT status FROM sdk_context_source_bindings
               WHERE binding_id IN (?,?) ORDER BY binding_id""",
            (root_binding, continuation_binding),
        ).fetchall()
    assert statuses == [("consumed",), ("consumed",)]
    await session.close()


@pytest.mark.asyncio
async def test_dev_fault_wrapper_is_one_shot_and_fail_closed(monkeypatch, tmp_path) -> None:
    target = SimpleNamespace(
        recall_for_turn=AsyncMock(return_value="ok"),
        record_committed_turn=AsyncMock(return_value="ok"),
        release_recall=AsyncMock(),
    )
    monkeypatch.setenv("DESKPET_MEMORY_RECALL_FAULT", "timeout")
    monkeypatch.setenv("DESKPET_DEV_MODE", "1")
    isolated = tmp_path / ".local-test-evidence" / "run"
    wrapped = DevMemoryFaultPort(target, recall_fault="timeout", record_fault=None)
    with pytest.raises(TimeoutError):
        await wrapped.recall_for_turn(object())
    assert await wrapped.recall_for_turn(object()) == "ok"
    monkeypatch.setenv("DESKPET_DEV_MODE", "0")
    with pytest.raises(RuntimeError, match="memory_faults_require_dev_mode"):
        wrap_dev_memory_faults(target, user_data_dir=isolated)


def test_final_candidate_rejects_every_superseded_wheel_hash() -> None:
    assert SDK_WHEEL_SHA256 == (
        "b9421ddf2b1d5a4a4a0920a2e878c1d3cf098ff6ef0af8975b9eb5c516037d7b"
    )
    assert SDK_MEMORY_WHEEL_SHA256 == (
        "deff2fa85a269a3978f2c6efcd99fda77abcb74444170361365fd00ec0164e9e"
    )
    assert SDK_WHEEL_SHA256 not in {
        "1e4d21d58bee0e58ea3bc49768ff63ba9095eefd2e2d3436375576005bbac99a",
        "d27b2273ba6a0b75ddbc21781a10e15ed72fd163b9eecf5fd5bda9315695af2c",
        "cf629ceed1e419fccacabc220f66ba201120f21ed58d30af5c4f70da97dae147",
        "aaf8d79a71b75bde0d71157a635b841eb557ea8889e2824571cacd7d8a58ecb6",
        "ecb6e85c65e9140c6838666f59f38239557e15cf410c1afe023ffd06bfb35be7",
    }
    assert SDK_MEMORY_WHEEL_SHA256 not in {
        "2fad089b111b8f6a1e6406e5b6f12167daf911371cfdd2e5c41e0e7a9818700f",
        "f61dbbb747bb5e593088f9e7e7aeeb5ca4757dcf7e24d88403fed44c97f3e376",
        "bf4335d3d06fa1dd3aa538f581af5233abdf15b4441d3b05e6757db6889c8f09",
        "e4055587faf0bff50bcc919625096c595b6f2bc78dcb5bdb21245c561a4249a1",
        "bfcd25061477dcf31dab23afbe4578ffc4418ffa1dbd3e4416679a2beba8f144",
        "c274fa6b2db538c29897f684b3f2f85775cb4b3a6870018e83792ff90b51ea46",
    }


def test_interrupted_pair_journal_restores_both_old_databases(tmp_path) -> None:
    execution = tmp_path / "execution.db"
    memory = tmp_path / "memory.db"
    execution_backup = tmp_path / "execution.bak"
    memory_backup = tmp_path / "memory.bak"
    execution.write_bytes(b"mixed-new-execution")
    memory.write_bytes(b"mixed-new-memory")
    execution_backup.write_bytes(b"old-execution")
    memory_backup.write_bytes(b"old-memory")
    digest = lambda value: hashlib.sha256(value).hexdigest()
    journal = tmp_path / "cutover.json"
    journal.write_text(
        json.dumps(
            {
                "protocol": "simple-harness-product/sdk-v4-cutover-journal/v1",
                "phase": "execution_v4",
                "execution_path": str(execution),
                "memory_path": str(memory),
                "execution_backup": str(execution_backup),
                "memory_backup": str(memory_backup),
                "old_execution_hash": digest(b"old-execution"),
                "old_memory_hash": digest(b"old-memory"),
            }
        ),
        encoding="utf-8",
    )
    assert recover_product_sdk_pair_v4(journal) == "old_pair"
    assert execution.read_bytes() == b"old-execution"
    assert memory.read_bytes() == b"old-memory"


@pytest.mark.asyncio
async def test_explicit_memory_write_uses_trusted_identity_and_event_key() -> None:
    manager = SimpleNamespace(
        remember_fact=AsyncMock(return_value=41),
    )
    identity = AgentIdentity("deployment", "household", "actor", "session")
    resolver = SimpleNamespace(resolve=AsyncMock(return_value=identity))
    no_op = SimpleNamespace(
        replace_session_todos=lambda *_args: None,
        get=lambda *_args: None,
        mark_active=lambda *_args: None,
        recall_readonly=lambda *_args: None,
        resolve_for_run=lambda *_args: None,
        search=lambda *_args: None,
        describe=lambda *_args: None,
        suggestions=lambda *_args: None,
        activate=lambda *_args: None,
    )
    dependencies = ToolCatalogDependencies(
        no_op,
        lambda: None,
        no_op,
        lambda: None,
        no_op,
        no_op,
        no_op,
        SimpleNamespace(search=lambda *_args: None),
        memory_manager=manager,
        memory_identity_resolver=resolver,
    )
    handler, mode = _dynamic_handlers(dependencies)["memory_write"]
    assert mode == "context"
    result = await handler(
        {
            "text": "remember this",
            "salience": 0.9,
            "pinned": True,
            "tier": "l3",
        },
        SimpleNamespace(
            session_id="session",
            root_run_id="root",
            call_id="call",
        ),
    )
    assert result["memory_id"] == 41
    principal, content = manager.remember_fact.await_args.args
    assert (
        principal.deployment_id,
        principal.household_id,
        principal.actor_id,
        principal.session_id,
    ) == ("deployment", "household", "actor", "session")
    assert content == "remember this"
    assert manager.remember_fact.await_args.kwargs == {
        "source_event_id": "explicit-memory-action/v1/root/call",
        "salience": 0.9,
        "pinned": True,
        "tier": "identity",
    }


@pytest.mark.asyncio
async def test_explicit_memory_write_accepts_real_sdk_tool_context_shape() -> None:
    manager = SimpleNamespace(remember_fact=AsyncMock(return_value=52))
    identity = AgentIdentity("deployment", "household", "actor", "session-real")
    resolver = SimpleNamespace(resolve=AsyncMock(return_value=identity))
    no_op = SimpleNamespace(
        replace_session_todos=lambda *_args: None,
        get=lambda *_args: None,
        mark_active=lambda *_args: None,
        recall_readonly=lambda *_args: None,
        resolve_for_run=lambda *_args: None,
        search=lambda *_args: None,
        describe=lambda *_args: None,
        suggestions=lambda *_args: None,
        activate=lambda *_args: None,
    )
    dependencies = ToolCatalogDependencies(
        no_op,
        lambda: None,
        no_op,
        lambda: SimpleNamespace(
            session_id="session-real",
            root_run_id="root-real",
            call_id="call-real",
        ),
        no_op,
        no_op,
        no_op,
        SimpleNamespace(search=lambda *_args: None),
        memory_manager=manager,
        memory_identity_resolver=resolver,
    )
    handler = _dynamic_handlers(dependencies)["memory_write"][0]
    context = ToolContext(
        run_id=RunId("sdk-run"),
        request_id=RequestId("request"),
        cancellation=CancellationToken(),
        metadata={},
        call_id=CallId("call-real"),
    )

    result = await handler(
        {"text": "cobalt", "salience": 0.8, "pinned": True, "tier": "l3"},
        context,
    )

    assert result["memory_id"] == 52
    resolver.resolve.assert_awaited_once_with("session-real")
    assert manager.remember_fact.await_args.kwargs["source_event_id"] == (
        "explicit-memory-action/v1/root-real/call-real"
    )


@pytest.mark.asyncio
async def test_explicit_read_and_forget_use_full_trusted_principal() -> None:
    identities = {
        "session-a": AgentIdentity("deployment", "household-a", "actor-a", "session-a"),
        "session-b": AgentIdentity("deployment", "household-b", "actor-b", "session-b"),
    }

    async def read_fact(principal, fact_id):
        if principal.actor_id != "actor-a":
            return None
        return SimpleNamespace(id=fact_id, key="preference", value="concise")

    manager = SimpleNamespace(
        read_fact=AsyncMock(side_effect=read_fact),
        forget_fact=AsyncMock(return_value="forgotten"),
    )
    resolver = SimpleNamespace(
        resolve=AsyncMock(side_effect=lambda session_id: identities[session_id])
    )
    no_op = SimpleNamespace(
        replace_session_todos=lambda *_args: None,
        get=lambda *_args: None,
        mark_active=lambda *_args: None,
        recall_readonly=lambda *_args: None,
        resolve_for_run=lambda *_args: None,
        search=lambda *_args: None,
        describe=lambda *_args: None,
        suggestions=lambda *_args: None,
        activate=lambda *_args: None,
    )
    dependencies = ToolCatalogDependencies(
        no_op,
        lambda: None,
        no_op,
        lambda: None,
        no_op,
        no_op,
        no_op,
        SimpleNamespace(search=lambda *_args: None),
        memory_manager=manager,
        memory_identity_resolver=resolver,
    )
    handlers = _dynamic_handlers(dependencies)
    read = handlers["memory_read"][0]
    forget = handlers["memory_forget"][0]

    own = await read(
        {"memory_id": 41},
        SimpleNamespace(session_id="session-a", root_run_id="root", call_id="read"),
    )
    cross = await read(
        {"memory_id": 41},
        SimpleNamespace(session_id="session-b", root_run_id="root", call_id="cross"),
    )
    forgotten = await forget(
        {"fact_id": 41},
        SimpleNamespace(session_id="session-a", root_run_id="root", call_id="forget"),
    )

    assert own == {
        "ok": True,
        "memory": {"id": 41, "key": "preference", "value": "concise"},
    }
    assert cross == {"ok": False, "error": "memory_not_found"}
    assert forgotten == {
        "ok": True,
        "forgotten": True,
        "receipt": "forgotten",
        "source_event_id": "explicit-memory-action/v1/root/forget",
    }
    principal = manager.forget_fact.await_args.kwargs["principal"]
    assert (
        principal.deployment_id,
        principal.household_id,
        principal.actor_id,
        principal.session_id,
    ) == ("deployment", "household-a", "actor-a", "session-a")
    assert manager.forget_fact.await_args.kwargs == {
        "reason": "",
        "principal": principal,
        "source_event_id": "explicit-memory-action/v1/root/forget",
        "payload_hash": None,
    }


@pytest.mark.asyncio
async def test_official_explicit_fact_api_is_durable_scoped_and_idempotent(
    tmp_path,
) -> None:
    manager = await MemoryManager.build_development(tmp_path / "memory.db")
    owner = MemoryPrincipal("deployment", "household-a", "actor-a", "session-a")
    outsider = MemoryPrincipal("deployment", "household-b", "actor-b", "session-b")
    event_id = "explicit-memory-action/v1/root/call"
    try:
        fact_id = await manager.remember_fact(
            owner,
            "remember this",
            source_event_id=event_id,
            salience=0.9,
            pinned=True,
            tier="identity",
        )
        replay_id = await manager.remember_fact(
            owner,
            "remember this",
            source_event_id=event_id,
            salience=0.9,
            pinned=True,
            tier="identity",
        )
        assert replay_id == fact_id
        fact = await manager.read_fact(owner, fact_id)
        assert fact is not None
        assert fact.id == fact_id
        assert fact.value == "remember this"
        assert fact.pinned is True
        assert fact.category == "profile"
        assert await manager.read_fact(outsider, fact_id) is None
        recall = await manager.recall_for_turn(
            MemoryRecallRequest(
                query_id="explicit-fact-recall",
                turn_id="turn-after-explicit-write",
                identity=AgentIdentity(
                    "deployment", "household-a", "actor-a", "session-a"
                ),
                scopes=(MemoryScopeRef(MemoryScopeKind.PERSONAL, "actor-a"),),
                query_text="remember",
                bounds=MemoryRecallBounds(),
                turn_started_at=time.time(),
            )
        )
        assert any(
            item["record_id"] == f"fact:{fact_id}"
            and item["text"] == "remember this"
            for item in recall.payload["items"]
        )

        with pytest.raises(MemoryIdempotencyConflict):
            await manager.remember_fact(
                owner,
                "remember this",
                source_event_id=event_id,
                salience=0.8,
                pinned=True,
                tier="identity",
            )
        with pytest.raises(MemoryOwnershipConflict):
            await manager.remember_fact(
                outsider,
                "remember this",
                source_event_id=event_id,
                salience=0.9,
                pinned=True,
                tier="identity",
            )

        assert await manager.forget_fact(fact_id, reason="test", principal=owner)
        assert (
            await manager.remember_fact(
                owner,
                "remember this",
                source_event_id=event_id,
                salience=0.9,
                pinned=True,
                tier="identity",
            )
            == fact_id
        )
        assert await manager.read_fact(owner, fact_id) is None
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_official_forget_receipt_replays_conflicts_and_survives_restart(
    tmp_path,
) -> None:
    path = tmp_path / "memory.db"
    owner = MemoryPrincipal("deployment", "household-a", "actor-a", "session-a")
    outsider = MemoryPrincipal("deployment", "household-b", "actor-b", "session-b")
    action = "explicit-memory-action/v1/root/forget-call"
    later_action = "explicit-memory-action/v1/root/later-forget-call"

    manager = await MemoryManager.build_development(path)
    first_id = await manager.remember_fact(
        owner, "first", source_event_id="explicit-write-first"
    )
    second_id = await manager.remember_fact(
        owner, "second", source_event_id="explicit-write-second"
    )
    assert (
        await manager.forget_fact(
            first_id,
            reason="",
            principal=owner,
            source_event_id=action,
            payload_hash=None,
        )
        is True
    )
    assert (
        await manager.forget_fact(
            first_id,
            reason="",
            principal=owner,
            source_event_id=action,
            payload_hash=None,
        )
        is True
    )
    with pytest.raises(MemoryIdempotencyConflict):
        await manager.forget_fact(
            second_id,
            reason="",
            principal=owner,
            source_event_id=action,
            payload_hash=None,
        )
    with pytest.raises(MemoryOwnershipConflict):
        await manager.forget_fact(
            first_id,
            reason="",
            principal=outsider,
            source_event_id=action,
            payload_hash=None,
        )
    assert (
        await manager.forget_fact(
            first_id,
            reason="",
            principal=owner,
            source_event_id=later_action,
            payload_hash=None,
        )
        is False
    )
    await manager.close()

    reopened = await MemoryManager.build_development(path)
    try:
        assert (
            await reopened.forget_fact(
                first_id,
                reason="",
                principal=owner,
                source_event_id=action,
                payload_hash=None,
            )
            is True
        )
        assert (
            await reopened.forget_fact(
                first_id,
                reason="",
                principal=owner,
                source_event_id=later_action,
                payload_hash=None,
            )
            is False
        )
        assert await reopened.read_fact(owner, first_id) is None
        assert await reopened.read_fact(outsider, second_id) is None
    finally:
        await reopened.close()
