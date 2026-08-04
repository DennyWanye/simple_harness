from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from deskpet.agent.task_work_context import TaskWorkContextResolver
from deskpet.execution.contracts import (
    PersistenceLevel,
    RunContext,
    RunCreate,
    fingerprint_json,
)
from deskpet.memory.session_db import SessionDB
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


def _spec(run_id: str, index: int) -> RunCreate:
    capability_hash = fingerprint_json({"tools": ["shell", "read_file"]})
    return RunCreate(
        run_id=run_id,
        idempotency_key=f"root:main-session:request-{index}:turn-{index}",
        context=RunContext(
            session_id="main-session",
            root_run_id=run_id,
            parent_run_id=None,
            request_id=f"request-{index}",
            turn_id=f"turn-{index}",
            venue="text",
            workspace={"root": f"F:/tasks/{index}"},
            capability_hash=capability_hash,
            provider_plan={"model": "fixture"},
            trace_id=f"trace-{index}",
            principal_id="principal",
            auth_epoch=1,
        ),
        payload_fingerprint=fingerprint_json({"index": index}),
        capability_fingerprint=capability_hash,
        driver_kind="react",
        profile_key="agent.general",
        persistence_level=PersistenceLevel.DURABLE,
    )


@pytest.mark.asyncio
async def test_three_roots_share_session_but_keep_durable_task_identity(
    tmp_path,
):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    resolver = TaskWorkContextResolver(tmp_path / "tasks")

    async def create(index: int):
        run_id = f"root-{index}"
        spec = _spec(run_id, index)
        await uow.create(spec)
        task_scope_id = resolver.task_scope_id(
            "main-session", f"request-{index}", f"turn-{index}"
        )
        work = resolver.resolve(
            session_id="main-session",
            root_run_id=run_id,
            task_scope_id=task_scope_id,
            explicit_workspace=tmp_path / "tasks" / str(index),
        )
        conversation = resolver.conversation_boundary(
            work, (f"message:{index}",)
        )
        projection = resolver.projection(work)
        return await uow.create_task_context(
            work, conversation, projection
        )

    created = await asyncio.gather(*(create(index) for index in range(3)))

    assert {item[0].session_id for item in created} == {"main-session"}
    assert len({item[0].root_run_id for item in created}) == 3
    assert len({item[0].task_scope_id for item in created}) == 3
    assert len({item[0].workspace_root for item in created}) == 3
    assert len({item[1].boundary_ref for item in created}) == 3
    assert len({item[2].projection_id for item in created}) == 3
    assert all(item[2].ui_state == "open" for item in created)

    projections = await uow.list_task_run_projections("main-session")
    assert {item.root_run_id for item in projections} == {
        "root-0",
        "root-1",
        "root-2",
    }
    lifecycle = await uow.get_task_run_lifecycle(
        "root-0",
        expected_session_id="main-session",
    )
    assert lifecycle is not None
    assert lifecycle["status"] == "created"
    assert (
        await uow.get_task_run_lifecycle(
            "root-0",
            expected_session_id="another-session",
        )
        is None
    )


@pytest.mark.asyncio
async def test_selected_project_root_survives_a_later_workspace_free_run(
    tmp_path,
):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    resolver = TaskWorkContextResolver(tmp_path / "tasks")

    first_spec = _spec("root-project", 1)
    await uow.create(first_spec)
    first_scope = resolver.task_scope_id(
        "main-session", "request-1", "turn-1"
    )
    first_work = resolver.resolve(
        session_id="main-session",
        root_run_id="root-project",
        task_scope_id=first_scope,
        create_default=True,
    )
    await uow.create_task_context(
        first_work,
        resolver.conversation_boundary(first_work, ("message:1",)),
        resolver.projection(first_work),
    )
    selected_root = tmp_path / "games" / "apocalypse-demo"
    await uow.rebind_task_workspace_for_project(
        "root-project",
        str(selected_root),
        expected_binding_version=1,
    )

    second_spec = _spec("root-later", 2)
    second_spec = replace(
        second_spec,
        context=replace(second_spec.context, workspace={}),
    )
    await uow.create(second_spec)
    second_scope = resolver.task_scope_id(
        "main-session", "request-2", "turn-2"
    )
    second_work = resolver.resolve(
        session_id="main-session",
        root_run_id="root-later",
        task_scope_id=second_scope,
    )
    await uow.create_task_context(
        second_work,
        resolver.conversation_boundary(second_work, ("message:2",)),
        resolver.projection(second_work),
    )

    assert await uow.get_latest_session_project_context("main-session") == {
        "project_name": "apocalypse-demo",
        "project_root": str(selected_root.resolve(strict=False)),
    }
    assert await uow.get_latest_session_project_context("another-session") == {
        "project_name": None,
        "project_root": None,
    }


@pytest.mark.asyncio
async def test_continuation_and_projection_changes_are_root_local(tmp_path):
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    resolver = TaskWorkContextResolver(tmp_path / "tasks")
    contexts = []
    for index in range(3):
        run_id = f"root-{index}"
        await uow.create(_spec(run_id, index))
        task_scope = resolver.task_scope_id(
            "main-session", f"request-{index}", f"turn-{index}"
        )
        work = resolver.resolve(
            session_id="main-session",
            root_run_id=run_id,
            task_scope_id=task_scope,
            explicit_workspace=tmp_path / "tasks" / str(index),
        )
        conversation = resolver.conversation_boundary(
            work, (f"message:{index}",)
        )
        projection = resolver.projection(work)
        contexts.append(
            await uow.create_task_context(
                work, conversation, projection
            )
        )

    for index, (work, conversation, _projection) in enumerate(contexts):
        await uow.enqueue_user_continuation(
            work.root_run_id,
            f"message:continue:{index}",
            f"continue {index}",
            task_scope_id=work.task_scope_id,
            expected_boundary_version=conversation.version,
        )

    closed = await uow.set_task_run_projection_state(
        "root-1", "closed", expected_version=0
    )
    assert closed.ui_state == "closed"
    assert {
        item.root_run_id
        for item in await uow.list_task_run_projections("main-session")
    } == {"root-0", "root-2"}

    for index, (work, _conversation, _projection) in enumerate(contexts):
        stored = await uow.get_conversation_boundary(work.root_run_id)
        assert stored is not None
        assert stored.seed_message_refs == (f"message:{index}",)
        assert stored.continuation_message_refs == (
            f"message:continue:{index}",
        )


@pytest.mark.asyncio
async def test_interleaved_session_messages_filter_to_exact_root(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    await db.initialize()
    for turn in range(2):
        for index in range(3):
            await db.append_message(
                session_id="main-session",
                role="user",
                content=f"root-{index}:user-{turn}",
                root_run_id=f"root-{index}",
                task_scope_id=f"scope-{index}",
            )
            await db.append_message(
                session_id="main-session",
                role="tool",
                content=f"root-{index}:tool-{turn}",
                tool_call_id=f"call-{index}-{turn}",
                root_run_id=f"root-{index}",
                task_scope_id=f"scope-{index}",
            )
            await db.append_message(
                session_id="main-session",
                role="assistant",
                content=f"root-{index}:assistant-{turn}",
                root_run_id=f"root-{index}",
                task_scope_id=f"scope-{index}",
            )

    for index in range(3):
        rows = await db.get_recent_messages(
            "main-session",
            limit=50,
            root_run_id=f"root-{index}",
            task_scope_id=f"scope-{index}",
        )
        text = "\n".join(str(row["content"]) for row in rows)
        assert f"root-{index}:" in text
        for other in range(3):
            if other != index:
                assert f"root-{other}:" not in text
