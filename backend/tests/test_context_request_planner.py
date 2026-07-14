from __future__ import annotations

from types import SimpleNamespace

import pytest

from deskpet.agent.assembler.bundle import AttachmentRef, ContextBundle, ContextFragment
from deskpet.agent.context_request_planner import ContextBudgetExceeded, ContextRequestPlanner
from deskpet.agent.session_history_planner import SessionHistoryPlanner
from deskpet.agent.context_task import TaskContextProjector, TaskFact
from deskpet.memory.context_segment_store import ContextSegmentStore, CoverageCommitProof
from deskpet.memory.context_snapshot_store import ContextSnapshotStore, SnapshotConflictError
from deskpet.memory.migrator import run_migrations
from deskpet.tools.capabilities import ToolCapabilityResolver, ToolEligibilityContext, ToolExposureIntent
from deskpet.tools.registry import ToolRegistry
from deskpet.tools.context_page_in_tools import ContextPageInStore


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        "read",
        "core",
        {"name": "read", "description": "read", "parameters": {"type": "object"}},
        lambda _args, _task: "ok",
    )
    registry.register(
        "session_history_page_in",
        "memory",
        {
            "name": "session_history_page_in",
            "description": "page in exact session history",
            "parameters": {"type": "object"},
        },
        lambda _args, _task: "ok",
    )
    registry.register(
        "context_page_in", "memory",
        {"name": "context_page_in", "description": "exact context page in",
         "parameters": {"type": "object"}},
        lambda _args, _task: "ok",
    )
    return registry


@pytest.mark.asyncio
async def test_over_window_fragment_becomes_scoped_ref_and_conditional_tool() -> None:
    registry = _registry()
    store = ContextPageInStore()
    bundle = ContextBundle(task_type="chat")
    bundle.fragments.append(ContextFragment(
        fragment_id="memory:l3", source="memory:l3", role="system",
        content="x" * 5000, lifetime="retrieved", placement="prefix", priority=50,
        trim_policy="page_in", reason="semantic recall",
        meta={"page_in_kind": "memory_l3", "page_in_content": "x" * 5000},
    ))
    result = await ContextRequestPlanner(
        ToolCapabilityResolver(registry), page_in_store=store,
    ).prepare_initial(
        bundle, base_system="", history=[], user_message="now",
        eligibility=ToolEligibilityContext("s", "r", "chat"),
        context_window=1800, effective_pct=.95, generation_reserve=500,
    )
    assert result.prepared_context.tool_set.has_direct("context_page_in")
    assert len(result.prepared_context.page_in_refs) == 1
    assert "Context page-in reference" in result.prepared_context.messages[0]["content"]
    assert "x" * 100 not in result.prepared_context.messages[0]["content"]
    assert result.prepared_context.assembly_decisions[0].action == "trimmed"


class _SessionDB:
    def __init__(self, rows):
        self.rows = list(rows)

    async def get_messages(self, session_id: str, limit: int = 50, offset: int = 0):
        selected = [row for row in self.rows if row["session_id"] == session_id]
        return selected[offset : offset + limit]


def _session_message(message_id: int, content: str, *, role: str = "user"):
    return {
        "id": message_id,
        "session_id": "s",
        "role": role,
        "content": content,
    }


def _history_estimate(messages):
    total = 0
    for message in messages:
        content = str(message.get("content", ""))
        total += 10 if content.startswith("[Session history summary]") else len(content)
    return max(1, total)


@pytest.mark.asyncio
async def test_prepare_initial_resolves_tools_once_and_counts_schema() -> None:
    registry = _registry()
    calls = 0
    original = registry.catalog_snapshot

    def counted():
        nonlocal calls
        calls += 1
        return original()

    registry.catalog_snapshot = counted  # type: ignore[method-assign]
    bundle = ContextBundle(task_type="chat")
    bundle.tool_exposure_intent = ToolExposureIntent(direct_selectors=("read",))
    planner = ContextRequestPlanner(ToolCapabilityResolver(registry))
    result = await planner.prepare_initial(
        bundle,
        base_system="system",
        history=[{"role": "assistant", "content": "prior"}],
        user_message="now",
        eligibility=ToolEligibilityContext("s", "r", "chat"),
        context_window=8_000,
        effective_pct=0.95,
        generation_reserve=1_024,
        conditional_direct_names=(),
    )
    assert calls == 1
    assert result.prepared_context.tool_set.has_direct("read")
    assert result.budget.tool_tokens > 0
    assert result.budget.fits


@pytest.mark.asyncio
async def test_explicit_remote_mcp_name_stays_deferred_in_initial_attempt() -> None:
    registry = _registry()
    for bridge in ("tool_search", "tool_describe", "tool_activate"):
        registry.register(
            bridge,
            "control",
            {"name": bridge, "description": bridge, "parameters": {"type": "object"}},
            lambda _args, _task: "ok",
        )
    canonical_name = "mcp_context-os-e2e_mcp_ctx_fixture_tool_001"
    registry.register(
        canonical_name,
        "mcp",
        {
            "name": canonical_name,
            "description": "fixture tool",
            "parameters": {"type": "object"},
        },
        lambda _args, _task: "ok",
        source="mcp:context-os-e2e",
        fixture_remote_name="mcp_ctx_fixture_tool_001",
    )
    bundle = ContextBundle(task_type="task")
    bundle.tool_exposure_intent = ToolExposureIntent(
        direct_selectors=(),
        discoverable_selectors=("source:mcp:*",),
    )
    result = await ContextRequestPlanner(ToolCapabilityResolver(registry)).prepare_initial(
        bundle,
        base_system="system",
        history=[],
        user_message=(
            "Must call mcp_ctx_fixture_tool_001 with marker=CTX-CALL-1"
        ),
        eligibility=ToolEligibilityContext("s", "r", "task"),
        context_window=8_192,
        effective_pct=0.95,
        generation_reserve=1_024,
    )

    assert not result.prepared_context.tool_set.has_direct(canonical_name)
    assert [ref.name for ref in result.prepared_context.tool_set.deferred] == [
        canonical_name
    ]


@pytest.mark.asyncio
async def test_minimum_request_that_cannot_fit_blocks() -> None:
    registry = _registry()
    bundle = ContextBundle(task_type="chat")
    bundle.tool_exposure_intent = ToolExposureIntent(direct_selectors=("read",))
    planner = ContextRequestPlanner(ToolCapabilityResolver(registry))
    with pytest.raises(ContextBudgetExceeded):
        await planner.prepare_initial(
            bundle,
            base_system="x" * 20_000,
            history=[],
            user_message="now",
            eligibility=ToolEligibilityContext("s", "r", "chat"),
            context_window=8_000,
            effective_pct=0.9,
            generation_reserve=8_192,
            conditional_direct_names=(),
        )


def test_replan_reuses_existing_tool_set() -> None:
    registry = _registry()
    draft = ToolCapabilityResolver(registry).resolve_draft(
        ToolExposureIntent(direct_selectors=("read",)),
        eligibility=ToolEligibilityContext("s", "r", "chat"),
    )
    bundle = ContextBundle(task_type="chat")
    prepared = bundle.prepare_request(user_message="now", tool_set=draft.finalize())
    result = ContextRequestPlanner(ToolCapabilityResolver(registry)).replan(
        prepared,
        context_window=32_000,
        effective_pct=0.95,
        generation_reserve=1_024,
    )
    assert result.prepared_context.tool_set is prepared.tool_set


@pytest.mark.asyncio
async def test_attachment_refs_are_counted_once_in_whole_request_budget() -> None:
    registry = _registry()
    planner = ContextRequestPlanner(ToolCapabilityResolver(registry))
    bundle = ContextBundle(task_type="chat")
    ref = AttachmentRef(
        fragment_id="attachment:1",
        message_index=0,
        content_index=1,
        media_type="image/png",
        byte_size=4096,
        estimated_tokens=240,
        estimate_method="test",
    )
    result = await planner.prepare_initial(
        bundle,
        base_system="system",
        history=[],
        user_message="inspect",
        eligibility=ToolEligibilityContext("s", "r", "chat"),
        context_window=8_000,
        effective_pct=0.95,
        generation_reserve=1_024,
        conditional_direct_names=(),
        attachment_refs=(ref,),
        attachment_tokens=200,
    )
    assert result.budget.attachment_tokens == 240
    assert result.prepared_context.attachment_refs == (ref,)
    assert result.prepared_context.attachment_tokens == 240
    assert result.prepared_context.request_budget is result.budget


@pytest.mark.asyncio
async def test_attachment_budget_can_block_before_provider_attempt() -> None:
    registry = _registry()
    planner = ContextRequestPlanner(ToolCapabilityResolver(registry))
    with pytest.raises(ContextBudgetExceeded):
        await planner.prepare_initial(
            ContextBundle(task_type="chat"),
            base_system="system",
            history=[],
            user_message="inspect",
            eligibility=ToolEligibilityContext("s", "r", "chat"),
            context_window=2_000,
            effective_pct=0.95,
            generation_reserve=1_024,
            conditional_direct_names=(),
            attachment_tokens=2_000,
        )


@pytest.mark.asyncio
async def test_prepare_initial_create_or_cas_persists_projection_and_tool_summary(
    tmp_path,
) -> None:
    db_path = tmp_path / "state.db"
    await run_migrations(db_path)
    store = ContextSnapshotStore(db_path)
    snapshot = await TaskContextProjector().project(
        effective_sid="s",
        request_id="r",
        user_text="Build the long-running report",
        explicit_new=True,
        structured_evidence=(
            TaskFact("request:r", "Build the long-running report", "session_request", "pending"),
        ),
    )
    assert snapshot is not None
    bundle = ContextBundle(task_type="chat")
    bundle.tool_exposure_intent = ToolExposureIntent(direct_selectors=("read",))
    planner = ContextRequestPlanner(
        ToolCapabilityResolver(_registry()),
        snapshot_store=store,
    )

    first = await planner.prepare_initial(
        bundle,
        base_system="system",
        history=[],
        user_message="Build the long-running report",
        eligibility=ToolEligibilityContext("s", "r", "chat"),
        context_window=8_000,
        effective_pct=0.95,
        generation_reserve=1_024,
        conditional_direct_names=(),
        active_snapshot=snapshot,
    )
    second = await planner.prepare_initial(
        bundle,
        base_system="system",
        history=[],
        user_message="Build the long-running report",
        eligibility=ToolEligibilityContext("s", "r2", "chat"),
        context_window=8_000,
        effective_pct=0.95,
        generation_reserve=1_024,
        conditional_direct_names=(),
        active_snapshot=snapshot,
    )
    loaded = await store.get("s", "session:s")

    assert first.prepared_context.active_snapshot_handle is not None
    assert second.prepared_context.active_snapshot_handle == loaded.handle
    assert loaded is not None
    assert loaded.objective == "Build the long-running report"
    assert loaded.prepared_toolset_summary["direct_names"] == ["read"]
    assert loaded.prepared_toolset_summary["schema_tokens"] > 0


@pytest.mark.asyncio
async def test_prepare_initial_cas_uses_revision_frozen_before_catalog_resolution() -> None:
    class RacingStore:
        def __init__(self) -> None:
            self.revision = 1
            self.expected = None

        async def get(self, session_id, task_scope_id):
            return SimpleNamespace(handle=SimpleNamespace(row_revision=self.revision))

        async def persist_projection_with_tool_context_cas(
            self, projection, *, expected_row_revision, prepared_toolset_summary
        ):
            self.expected = expected_row_revision
            if expected_row_revision != self.revision:
                raise SnapshotConflictError(expected_row_revision, self.revision)
            return SimpleNamespace(new_handle=SimpleNamespace(row_revision=self.revision + 1))

    store = RacingStore()
    real_resolver = ToolCapabilityResolver(_registry())

    class AdvancingResolver:
        def resolve_draft(self, *args, **kwargs):
            # A concurrent activation commits after prepare starts but before
            # this request freezes its catalog snapshot.
            store.revision = 2
            return real_resolver.resolve_draft(*args, **kwargs)

    planner = ContextRequestPlanner(AdvancingResolver(), snapshot_store=store)
    snapshot = SimpleNamespace(session_id="s", task_scope_id="session:s")

    with pytest.raises(SnapshotConflictError):
        await planner.prepare_initial(
            ContextBundle(task_type="chat"),
            base_system="system",
            history=[],
            user_message="race",
            eligibility=ToolEligibilityContext("s", "r", "chat"),
            context_window=8_000,
            effective_pct=0.95,
            generation_reserve=1_024,
            conditional_direct_names=(),
            active_snapshot=snapshot,
        )
    assert store.expected == 1


@pytest.mark.asyncio
async def test_prepare_initial_large_window_uses_all_session_raw_not_prebuilt_top_k(
    tmp_path,
) -> None:
    db_path = tmp_path / "state.db"
    await run_migrations(db_path)
    rows = [_session_message(index, f"history-{index}") for index in range(1, 13)]
    rows.append(_session_message(13, "now"))
    history_planner = SessionHistoryPlanner(
        _SessionDB(rows),
        ContextSegmentStore(db_path),
        token_estimator=_history_estimate,
        page_size=5,
    )
    bundle = ContextBundle(task_type="chat")
    bundle.tool_exposure_intent = ToolExposureIntent(direct_selectors=("read",))
    planner = ContextRequestPlanner(
        ToolCapabilityResolver(_registry()),
        history_planner=history_planner,
    )

    result = await planner.prepare_initial(
        bundle,
        base_system="system",
        history=[{"role": "user", "content": "history-12"}],
        user_message="now",
        eligibility=ToolEligibilityContext("s", "r", "chat"),
        context_window=32_000,
        effective_pct=0.95,
        generation_reserve=1_024,
        prebuilt_messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "history-12"},
            {"role": "user", "content": "now"},
        ],
        current_message_id=13,
    )

    contents = [
        message.get("content")
        for message in result.prepared_context.messages
        if message.get("role") == "user"
    ]
    assert contents == [*(f"history-{index}" for index in range(1, 13)), "now"]
    assert result.prepared_context.coverage_report.valid is True
    assert result.prepared_context.coverage_report.eligible_message_ids == tuple(
        range(1, 14)
    )
    assert result.prepared_context.coverage_compaction_jobs == ()


@pytest.mark.asyncio
async def test_overwindow_jobs_must_commit_then_replan_gap_free_before_send(
    tmp_path,
) -> None:
    db_path = tmp_path / "state.db"
    await run_migrations(db_path)
    rows = [_session_message(index, "x" * 400) for index in range(1, 9)]
    rows.append(_session_message(9, "now"))
    segments = ContextSegmentStore(db_path)
    history_planner = SessionHistoryPlanner(
        _SessionDB(rows),
        segments,
        token_estimator=_history_estimate,
        raw_leaf_max_messages=2,
    )
    bundle = ContextBundle(task_type="chat")
    bundle.tool_exposure_intent = ToolExposureIntent(direct_selectors=("read",))
    planner = ContextRequestPlanner(
        ToolCapabilityResolver(_registry()),
        history_planner=history_planner,
    )
    initial = await planner.prepare_initial(
        bundle,
        base_system="system",
        history=[],
        user_message="now",
        eligibility=ToolEligibilityContext("s", "r", "chat"),
        context_window=2_000,
        effective_pct=0.95,
        generation_reserve=200,
        current_message_id=9,
    )
    prepared = initial.prepared_context
    assert prepared.coverage_compaction_jobs
    assert prepared.coverage_report.valid is False
    assert prepared.tool_set.has_direct("session_history_page_in")

    for job in prepared.coverage_compaction_jobs:
        await segments.commit_summary(
            job.session_id,
            job.child_segment_ids,
            summary_text="bounded summary",
            proof=CoverageCommitProof(
                message_ids=job.message_ids,
                source_hash=job.source_hash,
                child_source_hashes=job.child_source_hashes,
                valid=True,
            ),
            provider_id="test",
            model_id="summary",
            token_estimates={"deskpet-conservative-v1": 10},
        )

    replanned = await prepared.replan_after_compaction()
    assert replanned.prepared_context.coverage_report.valid is True
    assert replanned.prepared_context.coverage_report.gaps == ()
    assert replanned.prepared_context.coverage_report.overlaps == ()
    assert replanned.prepared_context.coverage_compaction_jobs == ()
    assert replanned.budget.fits is True
    assert sum(
        message.get("role") == "user" and message.get("content") == "now"
        for message in replanned.prepared_context.messages
    ) == 1
