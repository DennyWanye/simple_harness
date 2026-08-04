from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import CompanionGrowthConfig
from deskpet.agent.turn_preparer import ProductTurnPreparer, TurnInput
from deskpet.companion.contracts import GrowthEvent
from deskpet.companion.identity_gate import (
    FrozenOwnerIdentity,
    IdentityReadyGate,
)
from deskpet.companion.preferences import PreferencePolicy, PreferenceResolver
from deskpet.companion.store import CompanionStore
from deskpet.companion.turn_authority import (
    CompanionCatalogLeaseCaptureV1,
    CompanionTurnAuthority,
)
from deskpet.memory.companion_message_projection import TrustedCompanionOwner
from deskpet.memory.enhanced_retriever import EnhancedRetriever
from deskpet.memory.retriever import Retriever
from deskpet.memory.session_db import SessionDB
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.memory_recall import (
    CompanionRunMemoryScopeResolver,
    MEMORY_RECALL_SCHEMA,
    build_memory_recall_handlers,
)


async def _owned_session(
    db: SessionDB,
    session_id: str,
    owner: TrustedCompanionOwner,
    content: str,
) -> int:
    await db.ensure_session(session_id)
    await db.bind_session_owner_if_absent(session_id, owner)
    return await db.append_message(session_id, "user", content)


@pytest.mark.asyncio
async def test_readonly_recall_isolates_owner_generation_unbound_and_as_of(
    tmp_path: Path,
) -> None:
    db = SessionDB(tmp_path / "state.db")
    owner_a = TrustedCompanionOwner("profile-a", 1, 7)
    owner_b = TrustedCompanionOwner("profile-b", 1, 4)
    owner_a_next = TrustedCompanionOwner("profile-a", 2, 1)
    a_id = await _owned_session(db, "a", owner_a, "alpha owner a")
    await _owned_session(db, "b", owner_b, "alpha owner b")
    await _owned_session(db, "a-next", owner_a_next, "alpha next generation")
    await db.ensure_session("legacy")
    await db.append_message("legacy", "user", "alpha legacy unbound")

    scope = await db.capture_owner_memory_read_scope("profile-a", 1, 7)
    late_id = await db.append_message("a", "user", "alpha late message")
    retriever = Retriever(db, object())
    hits = await retriever.recall_readonly("alpha", 20, scope)

    assert [hit.message_id for hit in hits] == [a_id]
    assert late_id > scope.as_of_message_id
    assert {hit.text for hit in hits} == {"alpha owner a"}


@pytest.mark.asyncio
async def test_enhanced_retriever_exposes_base_readonly_port_without_plugins(
    tmp_path: Path,
) -> None:
    class ExplodingPlugin:
        def __getattr__(self, name):
            raise AssertionError(f"readonly recall reached plug-in: {name}")

    db = SessionDB(tmp_path / "state.db")
    owner = TrustedCompanionOwner("profile-a", 1, 2)
    message_id = await _owned_session(db, "a", owner, "needle from owner")
    scope = await db.capture_owner_memory_read_scope("profile-a", 1, 2)
    retriever = EnhancedRetriever(
        Retriever(db, object()),
        facts_store=ExplodingPlugin(),
        facts_weight=1.0,
        reranker=ExplodingPlugin(),
        query_rewriter=ExplodingPlugin(),
        embedder=ExplodingPlugin(),
        chunk_store=ExplodingPlugin(),
        entity_extractor=ExplodingPlugin(),
    )

    hits = await retriever.recall_readonly("needle", 5, scope)

    assert [hit.message_id for hit in hits] == [message_id]
    assert [hit.text for hit in hits] == ["needle from owner"]


@pytest.mark.asyncio
async def test_readonly_recall_tombstone_wins_and_sqlite_audit_sees_zero_writes(
    tmp_path: Path,
) -> None:
    db = SessionDB(tmp_path / "state.db")
    owner = TrustedCompanionOwner("profile-a", 1, 1)
    await _owned_session(db, "a", owner, "needle before delete")
    scope = await db.capture_owner_memory_read_scope("profile-a", 1, 1)
    retriever = Retriever(db, object())

    traces: list[str] = []
    writes: list[int] = []
    write_actions = {
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_ALTER_TABLE,
    }

    def observe(connection: sqlite3.Connection) -> None:
        connection.set_trace_callback(traces.append)

        def authorize(action, _arg1, _arg2, _db_name, _trigger):
            if action in write_actions:
                writes.append(action)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)

    hits = await retriever.recall_readonly(
        "needle", 5, scope, connection_observer=observe
    )
    assert [hit.text for hit in hits] == ["needle before delete"]
    assert writes == []
    assert traces
    assert all(
        not statement.lstrip().upper().startswith(
            ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER")
        )
        for statement in traces
    )

    await db.tombstone_session("a")
    assert await retriever.recall_readonly("needle", 5, scope) == []


@pytest.mark.asyncio
async def test_tool_schema_and_handler_never_accept_owner_scope_from_model(
    tmp_path: Path,
) -> None:
    parameters = MEMORY_RECALL_SCHEMA["parameters"]
    assert set(parameters["properties"]) == {"query", "limit"}
    assert parameters["additionalProperties"] is False

    owner = TrustedCompanionOwner("profile-a", 1, 1)
    db = SessionDB(tmp_path / "state.db")
    # Construct a valid scope through the real authority instead of hand-writing hashes.
    await db.ensure_session("a")
    await db.bind_session_owner_if_absent("a", owner)
    await db.append_message("a", "user", "hello")
    scope = await db.capture_owner_memory_read_scope("profile-a", 1, 1)

    class Resolver:
        def resolve_for_run(self, run_id: str):
            assert run_id == "run-a"
            return scope

    class Query:
        async def recall_readonly(self, query, limit, owner_scope):
            assert (query, limit, owner_scope) == ("hello", 3, scope)
            return []

    direct, trusted = build_memory_recall_handlers(Query(), Resolver())
    with pytest.raises(RuntimeError, match="trusted_run_context"):
        await direct({"query": "hello"})
    context = ToolExecutionContext(
        scope_id="scope",
        session_id="a",
        request_id="request-a",
        run_id="run-a",
    )
    with pytest.raises(ValueError, match="only query and limit"):
        await trusted(
            {
                "query": "hello",
                "limit": 3,
                "profile_id": "profile-b",
                "session_ids": ["b"],
                "as_of_message_id": 999999,
            },
            context,
        )
    assert json.loads(await trusted({"query": "hello", "limit": 3}, context)) == {
        "items": [],
        "ok": True,
    }


@pytest.mark.asyncio
async def test_preparer_freezes_preferences_and_memory_scope_once(
    tmp_path: Path,
) -> None:
    companion_store = CompanionStore(tmp_path / "companion.db")
    owner = companion_store.create_profile(
        profile_id="profile-a",
        generation=1,
        identity_namespace_hash="identity-a",
    )
    resolver = PreferenceResolver(
        companion_store,
        policy=PreferencePolicy.from_growth_config(CompanionGrowthConfig()),
        clock=lambda: datetime(2026, 7, 25, tzinfo=UTC),
    )
    resolver.record(
        GrowthEvent(
            owner=owner,
            event_id="preference-event",
            source_kind="explicit_user_command",
            source_ref="message-1",
            context_key="context-1",
            root_run_id="old-run",
            reason_code="preference_observed",
            payload={"verified": True},
        ),
        preference_key="tone",
        value="formal",
        signal_kind="explicit_long_term",
    )
    session_db = SessionDB(tmp_path / "state.db")
    trusted_owner = TrustedCompanionOwner("profile-a", 1, 9)
    await _owned_session(
        session_db, "session-a", trusted_owner, "remember this fact"
    )

    async def capture(_turn, _services, _identity):
        return await session_db.capture_owner_memory_read_scope(
            "profile-a", 1, 9
        )

    gate = IdentityReadyGate()
    gate.bind(
        FrozenOwnerIdentity(
            owner=owner,
            owner_key="companion:profile-a:1",
            binding_epoch=9,
        )
    )
    authority = CompanionTurnAuthority(
        identity_gate=gate,
        preference_resolver=resolver,
        owner_memory_read_scope=capture,
    )
    preparer = ProductTurnPreparer(companion_turn_authority=authority)
    prepared = await preparer.prepare_context(
        TurnInput(
            text="continue",
            session_id="session-a",
            request_id="request-a",
            turn_id="turn-a",
            root_run_id="run-a",
        ),
        services={},
        config=SimpleNamespace(
            features=SimpleNamespace(summary_quality_loop=False),
            raw={},
        ),
        local_llm=SimpleNamespace(model="test", base_url=""),
        tool_registry=object(),
        current_message_id=1,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
    )

    assert {item.dependency_kind for item in prepared.growth_dependencies} == {
        "preference",
        "memory_scope",
    }
    finalized = await authority.finalize_after_catalog(
        prepared.companion_authority_state,
        CompanionCatalogLeaseCaptureV1(
            run_catalog_content_stamp="run-catalog",
            process_catalog_stamp="process-catalog",
            catalog_snapshot_ref="catalog-snapshot",
            capability_lease_intent_ref="lease-intent",
            exact_tools=(),
            lease_entries=(),
        ),
        run_id="run-a",
    )
    with companion_store.read() as db:
        assert db.execute(
            "SELECT count(*) FROM run_growth_snapshots WHERE run_id='run-a'"
        ).fetchone()[0] == 1
        dependency_kinds = db.execute(
            """SELECT dependency_kind FROM run_growth_dependency_items
               WHERE snapshot_id=? ORDER BY dependency_kind""",
            (finalized.product_snapshot_ref,),
        ).fetchall()
        assert [tuple(row) for row in dependency_kinds] == [
            ("memory_scope",),
            ("preference",),
        ]
        assert db.execute(
            "SELECT count(*) FROM owner_memory_read_scopes"
        ).fetchone()[0] == 1

    reopened = CompanionRunMemoryScopeResolver(
        companion_store
    ).resolve_for_run("run-a")
    assert reopened is not None
    assert {
        **reopened.to_dict(),
        "scope_ref": reopened.scope_ref,
    } == dict(prepared.owner_memory_read_scope)
