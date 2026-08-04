from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.agent_loop import (
    AgentLoop,
    FinalEvent,
    _capture_context_remount,
    _remount_context_after_compaction,
)
from agent.context_messages import ContextMessageMeta, tag_message
from agent.token_budget import BudgetCheck, BudgetCheckResult
from deskpet.agent.context_compressor import CompressionResult
from deskpet.agent.context_compressor import ContextCompressor
from deskpet.memory.context_snapshot_store import SnapshotConflictError
from llm.types import ChatResponse


class _LLM:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def chat_with_fallback(self, messages, **kwargs):
        self.calls.append(list(messages))
        return ChatResponse(
            content="done",
            stop_reason="end_turn",
            tool_calls=[],
            usage={"input_tokens": 10, "output_tokens": 2},
        )


class _Tools:
    def schemas(self, enabled_toolsets=None):
        return []


class _ContextManager:
    def __init__(self, *, blocked_first: bool = False) -> None:
        self.blocked_first = blocked_first
        self.calls = 0
        self.config = SimpleNamespace(compact_at_tokens=1)

    def check_budget(
        self, messages, *, model: str, real_prompt_tokens_floor: int = 0
    ):
        self.calls += 1
        blocked = self.blocked_first and self.calls == 1
        return BudgetCheckResult(
            verdict=BudgetCheck.BLOCK if blocked else BudgetCheck.OK,
            estimated_tokens=100 if blocked else 1,
            context_window=64,
            ratio=2.0 if blocked else 0.01,
        )

    def record_tool_result(self, *, tool_name: str, result: str):
        return result, None


class _Projector:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.calls = 0

    async def project(self, **kwargs):
        self.calls += 1
        self.order.append("project")
        return SimpleNamespace(
            session_id=kwargs["effective_sid"],
            task_scope_id="goal:g1",
        )


class _SnapshotStore:
    def __init__(self, order: list[str], *, fail: Exception | None = None) -> None:
        self.order = order
        self.fail = fail
        self.calls: list[str] = []

    async def get(self, session_id, task_scope_id):
        return None

    async def flush_once(self, cycle_id, projection, **kwargs):
        self.calls.append(cycle_id)
        self.order.append("flush")
        if self.fail is not None:
            raise self.fail
        return SimpleNamespace(
            new_handle=SimpleNamespace(
                session_id=projection.session_id,
                task_scope_id=projection.task_scope_id,
                row_revision=1,
            )
        )


class _Compressor:
    model = "summary-model"

    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.calls = 0

    def should_compress(self, prompt_tokens: int) -> bool:
        return True

    async def compress(self, messages, **kwargs):
        self.calls += 1
        self.order.append("compress")
        return CompressionResult(
            messages=[{"role": "user", "content": "short"}],
            compressed=True,
        )


def _prepared():
    return SimpleNamespace(
        tool_set=None,
        active_snapshot_handle=None,
        coverage_compaction_jobs=(),
    )


@pytest.mark.asyncio
async def test_context_os_flushes_once_before_compression() -> None:
    order: list[str] = []
    store = _SnapshotStore(order)
    compressor = _Compressor(order)
    loop = AgentLoop(
        _LLM(),
        _Tools(),
        compressor=compressor,
        context_manager=_ContextManager(),
        context_projector=_Projector(order),
        context_snapshot_store=store,
    )

    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "long task"}],
            task_id="task-1",
            context_request_id="request-1",
            prepared_context=_prepared(),
        )
    ]

    assert isinstance(events[-1], FinalEvent)
    assert order[:3] == ["project", "flush", "compress"]
    assert store.calls == ["request-1:compaction:1"]
    assert compressor.calls == 1


@pytest.mark.asyncio
async def test_flush_failure_keeps_raw_messages_and_skips_compressor() -> None:
    order: list[str] = []
    llm = _LLM()
    compressor = _Compressor(order)
    loop = AgentLoop(
        llm,
        _Tools(),
        compressor=compressor,
        context_manager=_ContextManager(),
        context_projector=_Projector(order),
        context_snapshot_store=_SnapshotStore(order, fail=RuntimeError("db locked")),
    )

    async for _ in loop.run(
        [{"role": "user", "content": "original"}],
        prepared_context=_prepared(),
    ):
        pass

    assert compressor.calls == 0
    assert any(message.get("content") == "original" for message in llm.calls[0])


@pytest.mark.asyncio
async def test_e2e_snapshot_fault_keeps_raw_messages_and_skips_compressor(monkeypatch) -> None:
    order: list[str] = []
    llm = _LLM()
    compressor = _Compressor(order)
    monkeypatch.setattr(
        "deskpet.context_os_e2e_hooks.consume_context_os_e2e_fault",
        lambda name: True if name == "snapshot_cas_timeout" else None,
    )
    loop = AgentLoop(
        llm,
        _Tools(),
        compressor=compressor,
        context_manager=_ContextManager(),
        context_projector=_Projector(order),
        context_snapshot_store=_SnapshotStore(order),
    )

    async for _ in loop.run(
        [{"role": "user", "content": "snapshot-fault-original"}],
        prepared_context=_prepared(),
    ):
        pass

    assert compressor.calls == 0
    assert any(
        message.get("content") == "snapshot-fault-original"
        for message in llm.calls[0]
    )


@pytest.mark.asyncio
async def test_e2e_compactor_fault_commits_no_summary_and_keeps_raw(monkeypatch) -> None:
    order: list[str] = []
    llm = _LLM()
    compressor = _Compressor(order)
    monkeypatch.setattr(
        "deskpet.context_os_e2e_hooks.consume_context_os_e2e_fault",
        lambda name: True if name == "compactor_model_error" else None,
    )
    loop = AgentLoop(
        llm,
        _Tools(),
        compressor=compressor,
        context_manager=_ContextManager(),
        context_projector=_Projector(order),
        context_snapshot_store=_SnapshotStore(order),
    )

    async for _ in loop.run(
        [{"role": "user", "content": "compactor-fault-original"}],
        prepared_context=_prepared(),
    ):
        pass

    assert compressor.calls == 0
    assert any(
        message.get("content") == "compactor-fault-original"
        for message in llm.calls[0]
    )


@pytest.mark.asyncio
async def test_snapshot_cas_conflict_reprojects_once() -> None:
    order: list[str] = []
    projector = _Projector(order)

    class _ConflictStore(_SnapshotStore):
        async def get(self, session_id, task_scope_id):
            return SimpleNamespace(
                handle=SimpleNamespace(row_revision=4),
                last_compaction_cycle_id="request-cas:compaction:1",
            )

        async def flush_once(self, cycle_id, projection, **kwargs):
            self.calls.append(cycle_id)
            if len(self.calls) == 1:
                raise SnapshotConflictError(0, 4)
            return SimpleNamespace(
                new_handle=SimpleNamespace(
                    session_id=projection.session_id,
                    task_scope_id=projection.task_scope_id,
                    row_revision=5,
                )
            )

    store = _ConflictStore(order)
    loop = AgentLoop(
        _LLM(),
        _Tools(),
        compressor=_Compressor(order),
        context_manager=_ContextManager(),
        context_projector=projector,
        context_snapshot_store=store,
    )
    prepared = _prepared()
    async for _ in loop.run(
        [{"role": "user", "content": "task"}],
        context_request_id="request-cas",
        prepared_context=prepared,
    ):
        pass

    assert projector.calls == 2
    assert store.calls == [
        "request-cas:compaction:1",
        "request-cas:compaction:1",
    ]
    assert prepared.active_snapshot_handle.row_revision == 5


@pytest.mark.asyncio
async def test_snapshot_cas_conflict_from_newer_cycle_fails_closed() -> None:
    order: list[str] = []
    llm = _LLM()
    compressor = _Compressor(order)

    class _NewerCycleStore(_SnapshotStore):
        async def get(self, session_id, task_scope_id):
            return SimpleNamespace(
                handle=SimpleNamespace(row_revision=4),
                last_compaction_cycle_id="other-request:compaction:1",
            )

        async def flush_once(self, cycle_id, projection, **kwargs):
            self.calls.append(cycle_id)
            raise SnapshotConflictError(0, 4)

    store = _NewerCycleStore(order)
    loop = AgentLoop(
        llm,
        _Tools(),
        compressor=compressor,
        context_manager=_ContextManager(),
        context_projector=_Projector(order),
        context_snapshot_store=store,
    )
    async for _ in loop.run(
        [{"role": "user", "content": "keep raw on conflict"}],
        context_request_id="request-old",
        prepared_context=_prepared(),
    ):
        pass

    assert store.calls == ["request-old:compaction:1"]
    assert compressor.calls == 0
    assert any(
        message.get("content") == "keep raw on conflict"
        for message in llm.calls[0]
    )


@pytest.mark.asyncio
async def test_restart_reuses_deterministic_cycle_id_without_second_write() -> None:
    order: list[str] = []

    class _RestartStore(_SnapshotStore):
        def __init__(self):
            super().__init__(order)
            self.committed: set[str] = set()
            self.revision = 0

        async def get(self, session_id, task_scope_id):
            if not self.revision:
                return None
            return SimpleNamespace(
                handle=SimpleNamespace(row_revision=self.revision)
            )

        async def flush_once(self, cycle_id, projection, **kwargs):
            self.calls.append(cycle_id)
            if cycle_id not in self.committed:
                self.committed.add(cycle_id)
                self.revision += 1
            return SimpleNamespace(
                new_handle=SimpleNamespace(
                    session_id=projection.session_id,
                    task_scope_id=projection.task_scope_id,
                    row_revision=self.revision,
                )
            )

    store = _RestartStore()
    for _ in range(2):
        loop = AgentLoop(
            _LLM(),
            _Tools(),
            compressor=_Compressor(order),
            context_manager=_ContextManager(),
            context_projector=_Projector(order),
            context_snapshot_store=store,
        )
        async for _event in loop.run(
            [{"role": "user", "content": "resume"}],
            task_id="task-restart",
            context_request_id="request-restart",
            prepared_context=_prepared(),
        ):
            pass

    assert store.calls == [
        "request-restart:compaction:1",
        "request-restart:compaction:1",
    ]
    assert store.revision == 1


@pytest.mark.asyncio
async def test_budget_block_gets_single_owner_recovery_before_final_block() -> None:
    order: list[str] = []
    llm = _LLM()
    loop = AgentLoop(
        llm,
        _Tools(),
        compressor=_Compressor(order),
        context_manager=_ContextManager(blocked_first=True),
        context_projector=_Projector(order),
        context_snapshot_store=_SnapshotStore(order),
    )

    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "too large"}],
            prepared_context=_prepared(),
        )
    ]
    assert isinstance(events[-1], FinalEvent)
    assert len(llm.calls) == 1
    assert order.count("compress") == 1


@pytest.mark.asyncio
async def test_off_path_still_uses_legacy_memory_preflush() -> None:
    order: list[str] = []

    class _Memory:
        async def append(self, *args, **kwargs):
            order.append("memory")

    loop = AgentLoop(
        _LLM(),
        _Tools(),
        compressor=_Compressor(order),
        context_manager=_ContextManager(),
        file_memory=_Memory(),
    )
    async for _ in loop.run([{"role": "user", "content": "legacy"}]):
        pass

    assert order[:2] == ["memory", "compress"]


@pytest.mark.asyncio
async def test_coverage_source_range_commits_at_most_once_per_cycle() -> None:
    order: list[str] = []

    class _CoverageCompressor(_Compressor):
        async def compress_coverage_job(self, job, **kwargs):
            order.append("coverage")
            return "summary"

    class _Segments:
        def __init__(self):
            self.calls = []

        async def commit_summary(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    job = SimpleNamespace(
        session_id="s1",
        first_message_id=1,
        last_message_id=2,
        message_ids=(1, 2),
        source_hash="hash",
        child_segment_ids=("raw-1",),
        child_source_hashes=("child-hash",),
    )
    segments = _Segments()
    compressor = _CoverageCompressor(order)
    loop = AgentLoop(
        _LLM(),
        _Tools(),
        compressor=compressor,
        context_segment_store=segments,
    )
    prepared = SimpleNamespace(
        coverage_compaction_jobs=(job, job),
    )
    resolution = SimpleNamespace(
        candidates=(
            SimpleNamespace(
                provider=object(), provider_id="p1", model_id="m1"
            ),
        )
    )

    await loop._execute_coverage_compaction_jobs(
        cycle_id="cycle-1",
        prepared_context=prepared,
        resolution=resolution,
    )

    assert order == ["coverage"]
    assert len(segments.calls) == 1


@pytest.mark.asyncio
async def test_initial_coverage_jobs_replan_before_first_provider_call() -> None:
    order: list[str] = []

    class _CoverageCompressor(_Compressor):
        def should_compress(self, prompt_tokens: int) -> bool:
            return False

        async def compress_coverage_job(self, job, **kwargs):
            order.append("coverage")
            return "summary"

    class _Segments:
        async def commit_summary(self, *args, **kwargs):
            order.append("commit")

    class _Provider:
        id = "provider-1"
        model = "model-1"

        async def chat_with_tools(self, messages, tools=None, **kwargs):
            order.append("provider")
            assert any(message.get("content") == "replanned" for message in messages)
            return {
                "content": "done",
                "stop_reason": "end_turn",
                "tool_calls": [],
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }

    job = SimpleNamespace(
        session_id="s1",
        first_message_id=1,
        last_message_id=2,
        message_ids=(1, 2),
        source_hash="hash",
        child_segment_ids=("raw-1",),
        child_source_hashes=("child-hash",),
    )
    prepared = SimpleNamespace(
        messages=[{"role": "user", "content": "tail"}],
        tool_set=None,
        active_snapshot_handle=None,
        coverage_report=SimpleNamespace(valid=False, gaps=(1, 2)),
        coverage_compaction_jobs=(job,),
        request_budget=SimpleNamespace(fits=True),
    )

    async def replan():
        assert order == ["project", "flush", "coverage", "commit"]
        order.append("replan")
        prepared.messages = [{"role": "user", "content": "replanned"}]
        prepared.coverage_report = SimpleNamespace(
            valid=True,
            gaps=(),
            overlaps=(),
            stale_segment_ids=(),
            broken_causal_groups=(),
        )
        prepared.coverage_compaction_jobs = ()
        return SimpleNamespace(prepared_context=prepared)

    prepared.replan_after_compaction = replan
    context_manager = _ContextManager()
    context_manager.config.compact_at_tokens = 999_999
    from deskpet.agent.compression_model_resolver import CompressionModelResolver

    provider = _Provider()
    loop = AgentLoop(
        _LLM(),
        _Tools(),
        compressor=_CoverageCompressor(order),
        context_manager=context_manager,
        context_segment_store=_Segments(),
        compression_model_resolver=CompressionModelResolver(),
        context_projector=_Projector(order),
        context_snapshot_store=_SnapshotStore(order),
    )
    events = [
        event
        async for event in loop.run(
            prepared.messages,
            task_id="task-coverage",
            session_id="s1",
            context_request_id="request-coverage",
            prepared_context=prepared,
            provider_chain=[provider],
        )
    ]

    assert isinstance(events[-1], FinalEvent)
    assert order == [
        "project",
        "flush",
        "coverage",
        "commit",
        "replan",
        "provider",
    ]


@pytest.mark.asyncio
async def test_chain_safe_coverage_flushes_snapshot_before_summary_commit() -> None:
    order: list[str] = []

    class _CapturingProjector(_Projector):
        user_text = ""

        async def project(self, **kwargs):
            self.user_text = kwargs["user_text"]
            return await super().project(**kwargs)

    class _CapturingStore(_SnapshotStore):
        tool_summary = None

        async def flush_once(self, cycle_id, projection, **kwargs):
            self.tool_summary = kwargs.get("prepared_toolset_summary")
            return await super().flush_once(cycle_id, projection, **kwargs)

    class _CoverageCompressor(_Compressor):
        def should_compress(self, prompt_tokens: int) -> bool:
            return False

        async def compress_coverage_job(self, job, **kwargs):
            order.append("coverage")
            return "summary"

    class _Segments:
        async def commit_summary(self, *args, **kwargs):
            order.append("commit")

    class _Provider:
        effective_pct = 1.0

        def __init__(self, provider_id: str, context_window: int) -> None:
            self.id = provider_id
            self.model = provider_id
            self.context_window = context_window

        async def chat_with_tools(self, messages, tools=None, **kwargs):
            order.append(self.id)
            return {
                "content": "done",
                "stop_reason": "end_turn",
                "tool_calls": [],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            }

    job = SimpleNamespace(
        session_id="s-chain",
        first_message_id=1,
        last_message_id=2,
        message_ids=(1, 2),
        source_hash="hash",
        child_segment_ids=("raw-1",),
        child_source_hashes=("child-hash",),
    )
    prepared = SimpleNamespace(
        messages=[{"role": "user", "content": "x" * 2_000}],
        tool_set=None,
        active_snapshot_handle=None,
        coverage_report=SimpleNamespace(valid=False, gaps=(1, 2)),
        coverage_compaction_jobs=(),
        request_budget=SimpleNamespace(generation_reserve=20, fits=True),
        attachment_tokens=0,
    )
    replans = 0

    async def replan_for_budget(*, context_window, effective_pct, generation_reserve):
        nonlocal replans
        replans += 1
        order.append(f"replan:{replans}")
        if replans == 1:
            prepared.coverage_compaction_jobs = (job,)
            prepared.messages = [
                {"role": "user", "content": "replanned-authority"}
            ]
            prepared.tool_set = SimpleNamespace(
                direct=(
                    SimpleNamespace(
                        ref=SimpleNamespace(
                            name="replanned_tool", schema_hash="hash"
                        )
                    ),
                ),
                activated=(),
                revision=2,
                registry_revision=3,
                policy_fingerprint="policy-new",
                schema_fingerprint="schema-new",
            )
        else:
            prepared.coverage_compaction_jobs = ()
            prepared.messages = [{"role": "user", "content": "safe"}]
            prepared.tool_set = None
            prepared.coverage_report = SimpleNamespace(
                valid=True,
                gaps=(),
                overlaps=(),
                stale_segment_ids=(),
                broken_causal_groups=(),
            )
        prepared.request_budget = SimpleNamespace(
            generation_reserve=generation_reserve,
            fits=True,
        )
        return SimpleNamespace(prepared_context=prepared)

    prepared.replan_for_budget = replan_for_budget
    context_manager = _ContextManager()
    context_manager.config.compact_at_tokens = 999_999
    from deskpet.agent.compression_model_resolver import CompressionModelResolver

    projector = _CapturingProjector(order)
    store = _CapturingStore(order)
    loop = AgentLoop(
        _LLM(),
        _Tools(),
        compressor=_CoverageCompressor(order),
        context_manager=context_manager,
        context_segment_store=_Segments(),
        compression_model_resolver=CompressionModelResolver(),
        context_projector=projector,
        context_snapshot_store=store,
    )
    events = [
        event
        async for event in loop.run(
            prepared.messages,
            task_id="task-chain",
            session_id="s-chain",
            context_request_id="request-chain",
            prepared_context=prepared,
            provider_chain=[_Provider("primary", 1_000), _Provider("fallback", 200)],
        )
    ]

    assert isinstance(events[-1], FinalEvent)
    assert order == [
        "replan:1",
        "project",
        "flush",
        "coverage",
        "commit",
        "replan:2",
        "primary",
    ]
    assert projector.user_text == "replanned-authority"
    assert store.tool_summary["direct_names"] == ["replanned_tool"]


@pytest.mark.asyncio
async def test_provider_chain_uses_smallest_window_before_primary_attempt() -> None:
    order: list[str] = []

    class _Provider:
        effective_pct = 1.0

        def __init__(self, provider_id: str, context_window: int) -> None:
            self.id = provider_id
            self.model = provider_id
            self.context_window = context_window

        async def chat_with_tools(self, messages, tools=None, **kwargs):
            order.append(self.id)
            assert messages[-1]["content"] == "safe"
            return {
                "content": "done",
                "stop_reason": "end_turn",
                "tool_calls": [],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            }

    prepared = SimpleNamespace(
        messages=[{"role": "user", "content": "x" * 2_000}],
        tool_set=None,
        active_snapshot_handle=None,
        coverage_report=SimpleNamespace(
            valid=True,
            gaps=(),
            overlaps=(),
            stale_segment_ids=(),
            broken_causal_groups=(),
        ),
        coverage_compaction_jobs=(),
        request_budget=SimpleNamespace(generation_reserve=20, fits=True),
        attachment_tokens=0,
    )

    async def replan_for_budget(*, context_window, effective_pct, generation_reserve):
        order.append(f"replan:{context_window}")
        assert context_window == 200
        assert effective_pct == 1.0
        assert generation_reserve == 20
        prepared.messages = [{"role": "user", "content": "safe"}]
        prepared.request_budget = SimpleNamespace(
            generation_reserve=generation_reserve,
            fits=True,
        )
        return SimpleNamespace(prepared_context=prepared)

    prepared.replan_for_budget = replan_for_budget
    context_manager = _ContextManager()
    context_manager.config.compact_at_tokens = 999_999
    loop = AgentLoop(_LLM(), _Tools(), context_manager=context_manager)
    events = [
        event
        async for event in loop.run(
            prepared.messages,
            task_id="task-chain-min",
            session_id="s-chain-min",
            context_request_id="request-chain-min",
            prepared_context=prepared,
            provider_chain=[_Provider("primary", 1_000), _Provider("fallback", 200)],
        )
    ]

    assert isinstance(events[-1], FinalEvent)
    assert order == ["replan:200", "primary"]


@pytest.mark.asyncio
async def test_provider_chain_compacts_once_before_terminal_budget_failure() -> None:
    order: list[str] = []

    class _Provider:
        effective_pct = 1.0

        def __init__(self, provider_id: str, context_window: int) -> None:
            self.id = provider_id
            self.model = provider_id
            self.context_window = context_window

        async def chat_with_tools(self, messages, tools=None, **kwargs):
            order.append(self.id)
            assert messages[0]["content"] == "short"
            assert messages[-1]["content"] == "tail-5"
            return {
                "content": "done",
                "stop_reason": "end_turn",
                "tool_calls": [],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            }

    long_history = [
        {"role": "user", "content": "x" * 2_000},
        {"role": "assistant", "content": "y" * 2_000},
        *[
            {"role": "user", "content": f"tail-{index}"}
            for index in range(6)
        ],
    ]
    prepared = SimpleNamespace(
        messages=long_history,
        tool_set=None,
        active_snapshot_handle=None,
        coverage_report=SimpleNamespace(
            valid=True,
            gaps=(),
            overlaps=(),
            stale_segment_ids=(),
            broken_causal_groups=(),
        ),
        coverage_compaction_jobs=(),
        request_budget=SimpleNamespace(generation_reserve=20, fits=False),
        attachment_tokens=0,
    )

    async def replan_for_budget(*, context_window, effective_pct, generation_reserve):
        order.append(f"replan:{context_window}")
        assert context_window == 200
        prepared.request_budget = SimpleNamespace(
            generation_reserve=generation_reserve,
            fits=False,
        )
        return SimpleNamespace(prepared_context=prepared)

    prepared.replan_for_budget = replan_for_budget
    context_manager = _ContextManager()
    context_manager.config.compact_at_tokens = 999_999
    from deskpet.agent.compression_model_resolver import CompressionModelResolver

    compressor = _Compressor(order)
    loop = AgentLoop(
        _LLM(),
        _Tools(),
        compressor=compressor,
        context_manager=context_manager,
        compression_model_resolver=CompressionModelResolver(),
        context_projector=_Projector(order),
        context_snapshot_store=_SnapshotStore(order),
    )
    events = [
        event
        async for event in loop.run(
            prepared.messages,
            task_id="task-chain-rescue",
            session_id="s-chain-rescue",
            context_request_id="request-chain-rescue",
            prepared_context=prepared,
            provider_chain=[
                _Provider("primary", 1_000),
                _Provider("fallback", 200),
            ],
        )
    ]

    assert isinstance(events[-1], FinalEvent)
    assert compressor.calls == 1
    assert order == [
        "replan:200",
        "project",
        "flush",
        "compress",
        "primary",
    ]


@pytest.mark.asyncio
async def test_attempt_budget_block_falls_through_to_next_provider() -> None:
    calls: list[str] = []

    class _Provider:
        def __init__(self, provider_id: str) -> None:
            self.id = provider_id
            self.model = provider_id

        async def chat_with_tools(self, messages, tools=None, **kwargs):
            calls.append(self.id)
            return {
                "content": "done",
                "stop_reason": "end_turn",
                "tool_calls": [],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            }

    primary = _Provider("primary")
    fallback = _Provider("fallback")
    loop = AgentLoop(_LLM(), _Tools())
    original = loop._run_context_attempt

    async def budget_once(**kwargs):
        if kwargs["provider"] is primary:
            raise RuntimeError("provider_context_budget_exceeded")
        return await original(**kwargs)

    loop._run_context_attempt = budget_once  # type: ignore[method-assign]
    events = [
        event
        async for event in loop.run(
            [{"role": "user", "content": "safe"}],
            task_id="task-budget-fallback",
            session_id="s-budget-fallback",
            provider_chain=[primary, fallback],
        )
    ]

    assert any(event.type == "provider_chain_fallback" for event in events)
    assert isinstance(events[-1], FinalEvent)
    assert calls == ["fallback"]


@pytest.mark.asyncio
async def test_dedicated_compaction_provider_failure_never_falls_back() -> None:
    class _FallbackRegistry:
        def __init__(self):
            self.calls = 0

        async def chat_with_fallback(self, *args, **kwargs):
            self.calls += 1
            return SimpleNamespace(content="must not be used")

    class _SelectedProvider:
        def __init__(self):
            self.calls = 0

        async def chat_with_tools(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("selected provider unavailable")

    fallback = _FallbackRegistry()
    selected = _SelectedProvider()
    compressor = ContextCompressor(
        llm_registry=fallback,
        first_n=1,
        last_n=1,
    )
    messages = [
        {"role": "user", "content": f"message-{index}"}
        for index in range(5)
    ]

    result = await compressor.compress(
        messages,
        resolved_provider=selected,
        resolved_model="dedicated-model",
        compaction_cycle_id="cycle-1",
    )

    assert result.compressed is False
    assert result.messages == messages
    assert selected.calls == 1
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_dedicated_compaction_provider_uses_summary_output_budget() -> None:
    class _SelectedProvider:
        def __init__(self):
            self.kwargs = None

        async def chat_with_tools(self, messages, **kwargs):
            self.kwargs = kwargs
            return {"content": "bounded summary"}

    selected = _SelectedProvider()
    compressor = ContextCompressor(summary_max_tokens=768)
    job = SimpleNamespace(
        messages=({"role": "user", "content": "source"},),
        first_message_id=1,
        last_message_id=1,
        source_hash="source-hash",
    )

    summary = await compressor.compress_coverage_job(
        job,
        resolved_provider=selected,
        resolved_model="8k-model",
        compaction_cycle_id="cycle-8k",
    )

    assert summary == "bounded summary"
    assert selected.kwargs == {
        "tools": None,
        "max_tokens": 768,
        "temperature": 0.0,
    }


def test_compaction_remount_restores_protected_prefix_and_complete_causal_tail() -> None:
    def tagged(role, content, *, lifetime, placement, source, group=None, **extra):
        return tag_message(
            {"role": role, "content": content, **extra},
            ContextMessageMeta(
                placement=placement,
                lifetime=lifetime,
                source=source,
                protected=placement == "prefix",
                trim_policy="never" if placement == "prefix" else "summarize",
                causal_group_id=group,
                fragment_id=f"fragment:{source}:{content}",
            ),
        )

    original = [
        tagged("system", "stable", lifetime="stable", placement="prefix", source="persona"),
        tagged("system", "task", lifetime="task", placement="prefix", source="task_context_projector"),
        tagged("system", "path rules", lifetime="task", placement="prefix", source="project_rules:AGENTS.md"),
        tagged("user", "old", lifetime="history", placement="transcript", source="session"),
        tagged(
            "assistant",
            "",
            lifetime="history",
            placement="transcript",
            source="session",
            group="tool-group-1",
            tool_calls=[{"id": "call-1", "type": "function", "function": {"name": "read", "arguments": "{}"}}],
        ),
        tagged(
            "tool",
            "result",
            lifetime="history",
            placement="transcript",
            source="session",
            group="tool-group-1",
            tool_call_id="call-1",
        ),
        tagged("user", "recent", lifetime="current", placement="transcript", source="current"),
        tag_message(
            {"role": "system", "content": "control"},
            ContextMessageMeta(
                placement="control",
                lifetime="current",
                source="control",
                trim_policy="drop",
                fragment_id="control:1",
                anchor_after="fragment:current:recent",
            ),
        ),
    ]
    remount = _capture_context_remount(original, recent_groups=2)
    compressed = [original[5], original[-1]]  # partial tool group + control only

    restored = _remount_context_after_compaction(compressed, remount)

    assert [message.get("content") for message in restored[:3]] == [
        "stable",
        "task",
        "path rules",
    ]
    assistant_index = next(
        index for index, message in enumerate(restored) if message.get("tool_calls")
    )
    assert restored[assistant_index + 1].get("tool_call_id") == "call-1"
    assert (
        restored[assistant_index]["__deskpet_context"]["causal_group_id"]
        == restored[assistant_index + 1]["__deskpet_context"]["causal_group_id"]
        == "tool-group-1"
    )
    assert restored[-2].get("content") == "recent"
    assert restored[-1].get("content") == "control"
