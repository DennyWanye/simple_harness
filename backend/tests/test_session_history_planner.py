from __future__ import annotations

import pytest
import pytest_asyncio

from deskpet.agent.session_history_planner import (
    CoverageEntry,
    SessionHistoryPlanner,
    build_coverage_report,
)
from deskpet.memory.context_segment_store import (
    ContextSegmentStore,
    CoverageCommitProof,
    canonical_message_hash,
)
from deskpet.memory.migrator import run_migrations


class _SessionDB:
    def __init__(self, rows):
        self.rows = list(rows)

    async def get_messages(self, session_id: str, limit: int = 50, offset: int = 0):
        selected = [row for row in self.rows if row["session_id"] == session_id]
        return selected[offset : offset + limit]


def _message(message_id: int, content: str, *, role: str = "user", **extra):
    return {
        "id": message_id,
        "session_id": "s1",
        "role": role,
        "content": content,
        **extra,
    }


def _estimate(messages):
    total = 0
    for message in messages:
        content = str(message.get("content", ""))
        total += 10 if content.startswith("[Session history summary]") else len(content)
    return max(1, total)


@pytest_asyncio.fixture
async def segment_store(tmp_path):
    db_path = tmp_path / "state.db"
    await run_migrations(db_path)
    return ContextSegmentStore(db_path)


@pytest.mark.asyncio
async def test_full_raw_fit_loads_every_message_not_fixed_top_k(segment_store):
    rows = [_message(index, f"same-text-{index % 2}") for index in range(1, 81)]
    planner = SessionHistoryPlanner(
        _SessionDB(rows),
        segment_store,
        token_estimator=_estimate,
        page_size=13,
    )

    plan = await planner.plan("s1", available_tokens=10_000)

    assert plan.blocked is False
    assert len(plan.messages) == 80
    assert [row["id"] for row in plan.messages] == list(range(1, 81))
    assert plan.coverage_report.valid is True
    assert not plan.used_summaries
    assert not plan.required_direct_tools


@pytest.mark.asyncio
async def test_empty_session_is_valid(segment_store):
    planner = SessionHistoryPlanner(_SessionDB([]), segment_store)
    plan = await planner.plan("s1", available_tokens=1)
    assert plan.messages == ()
    assert plan.coverage_report.valid is True
    assert plan.coverage_report.eligible_message_ids == ()


@pytest.mark.asyncio
async def test_default_estimator_routes_large_cjk_history_to_compaction(segment_store):
    rows = [
        _message(1, "历" * 2_000),
        _message(2, "史" * 2_000, role="assistant"),
    ]
    planner = SessionHistoryPlanner(_SessionDB(rows), segment_store)

    plan = await planner.plan("s1", available_tokens=2_000)

    assert plan.blocked is True
    assert plan.compaction_jobs
    assert plan.messages == ()


@pytest.mark.asyncio
async def test_current_message_is_covered_once_but_not_reinjected(segment_store):
    rows = [_message(index, f"m{index}") for index in range(1, 5)]
    planner = SessionHistoryPlanner(
        _SessionDB(rows), segment_store, token_estimator=_estimate
    )

    plan = await planner.plan(
        "s1",
        available_tokens=100,
        current_message_id=4,
    )

    assert [row["id"] for row in plan.messages] == [1, 2, 3]
    current = [entry for entry in plan.coverage_report.entries if entry.kind == "current"]
    assert current[0].message_ids == (4,)
    assert plan.coverage_report.valid is True


@pytest.mark.asyncio
async def test_incomplete_legacy_tool_groups_recover_without_blocking_session(
    segment_store,
):
    rows = [
        _message(1, "search for Python"),
        _message(
            2,
            "",
            role="assistant",
            tool_calls=[
                {
                    "id": "missing-call",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": "{}"},
                }
            ],
        ),
        _message(3, "stream interrupted", role="assistant"),
        _message(4, "detached result", role="tool", tool_call_id="orphan-call"),
        _message(5, "try again"),
    ]
    planner = SessionHistoryPlanner(
        _SessionDB(rows), segment_store, token_estimator=_estimate
    )

    plan = await planner.plan(
        "s1",
        available_tokens=1_000,
        current_message_id=5,
    )

    assert plan.blocked is False
    assert plan.coverage_report.valid is True
    assert plan.coverage_report.eligible_message_ids == (1, 2, 3, 4, 5)
    assert [message["id"] for message in plan.messages] == [1, 2, 3, 4]
    recovered = [
        message
        for message in plan.messages
        if message.get("__deskpet_recovered_causal_group")
    ]
    assert [message["id"] for message in recovered] == [2, 4]
    assert all(message["role"] == "assistant" for message in recovered)
    assert all("tool_calls" not in message for message in recovered)
    assert all("tool_call_id" not in message for message in recovered)
    assert [entry.kind for entry in plan.coverage_report.entries] == [
        "raw",
        "recovered_raw",
        "raw",
        "recovered_raw",
        "current",
    ]


@pytest.mark.asyncio
async def test_summary_cover_plus_raw_tail_has_no_gap_or_overlap(segment_store):
    rows = [_message(index, "x" * 100) for index in range(1, 7)]
    leaves = await segment_store.replace_raw_index(
        "s1", rows, max_messages=2, token_estimator=_estimate
    )
    first_four = rows[:4]
    summary = await segment_store.commit_summary(
        "s1",
        [leaves[0].segment_id, leaves[1].segment_id],
        summary_text="Earlier work established the inputs.",
        proof=CoverageCommitProof(
            message_ids=(1, 2, 3, 4),
            source_hash=canonical_message_hash(first_four),
            child_source_hashes=(leaves[0].source_hash, leaves[1].source_hash),
            valid=True,
        ),
        provider_id="test",
        model_id="summary",
        token_estimates={"deskpet-conservative-v1": 10},
    )
    planner = SessionHistoryPlanner(
        _SessionDB(rows),
        segment_store,
        token_estimator=_estimate,
        raw_leaf_max_messages=2,
    )

    plan = await planner.plan("s1", available_tokens=220)

    assert plan.blocked is False
    assert plan.used_summaries == (summary.segment_id,)
    assert plan.required_direct_tools == ("session_history_page_in",)
    assert [entry.kind for entry in plan.coverage_report.entries] == [
        "summary",
        "raw",
        "raw",
    ]
    assert plan.coverage_report.gaps == ()
    assert plan.coverage_report.overlaps == ()
    assert plan.coverage_report.valid is True


@pytest.mark.asyncio
async def test_missing_summary_returns_bounded_nonoverlap_jobs(segment_store):
    rows = [_message(index, "x" * 100) for index in range(1, 9)]
    planner = SessionHistoryPlanner(
        _SessionDB(rows),
        segment_store,
        token_estimator=_estimate,
        raw_leaf_max_messages=2,
    )

    plan = await planner.plan("s1", available_tokens=210)

    assert plan.blocked is True
    assert plan.blocked_reason == "coverage_compaction_required"
    assert plan.compaction_jobs
    assert plan.messages == ()
    covered = [message_id for job in plan.compaction_jobs for message_id in job.message_ids]
    assert len(covered) == len(set(covered))
    assert all(len(job.message_ids) <= 2 for job in plan.compaction_jobs)
    tail_ids = [row["id"] for row in plan.messages]
    assert covered + tail_ids == list(range(1, 9))
    assert plan.coverage_report.gaps


@pytest.mark.asyncio
async def test_changed_source_hash_prevents_stale_summary_use(segment_store):
    rows = [_message(index, "x" * 100) for index in range(1, 5)]
    db = _SessionDB(rows)
    leaves = await segment_store.replace_raw_index(
        "s1", rows, max_messages=2, token_estimator=_estimate
    )
    summary = await segment_store.commit_summary(
        "s1",
        [leaf.segment_id for leaf in leaves],
        summary_text="Old summary",
        proof=CoverageCommitProof(
            message_ids=(1, 2, 3, 4),
            source_hash=canonical_message_hash(rows),
            child_source_hashes=tuple(leaf.source_hash for leaf in leaves),
            valid=True,
        ),
        provider_id="test",
        model_id="summary",
        token_estimates={"deskpet-conservative-v1": 1},
    )
    db.rows[1] = _message(2, "edited" * 20)
    planner = SessionHistoryPlanner(
        db,
        segment_store,
        token_estimator=_estimate,
        raw_leaf_max_messages=2,
    )

    plan = await planner.plan("s1", available_tokens=50)

    assert plan.blocked is True
    assert summary.segment_id not in plan.used_summaries
    assert (await segment_store.get(summary.segment_id)).status == "stale"


@pytest.mark.asyncio
async def test_minimum_summary_cover_over_budget_blocks_without_new_jobs(segment_store):
    rows = [_message(index, "x" * 100) for index in range(1, 5)]
    leaves = await segment_store.replace_raw_index(
        "s1", rows, max_messages=2, token_estimator=_estimate
    )
    await segment_store.commit_summary(
        "s1",
        [leaf.segment_id for leaf in leaves],
        summary_text="still too expensive",
        proof=CoverageCommitProof(
            message_ids=(1, 2, 3, 4),
            source_hash=canonical_message_hash(rows),
            child_source_hashes=tuple(leaf.source_hash for leaf in leaves),
            valid=True,
        ),
        provider_id="test",
        model_id="summary",
        token_estimates={"deskpet-conservative-v1": 50},
    )
    planner = SessionHistoryPlanner(
        _SessionDB(rows),
        segment_store,
        token_estimator=_estimate,
        raw_leaf_max_messages=2,
    )
    plan = await planner.plan("s1", available_tokens=5)
    assert plan.blocked is True
    assert plan.blocked_reason == "minimum_summary_cover_exceeds_budget"
    assert plan.compaction_jobs == ()


@pytest.mark.asyncio
async def test_existing_summary_can_compact_only_new_raw_tail(segment_store):
    rows = [_message(index, "x" * 100) for index in range(1, 9)]
    leaves = await segment_store.replace_raw_index(
        "s1", rows, max_messages=2, token_estimator=_estimate
    )
    first_four = rows[:4]
    await segment_store.commit_summary(
        "s1",
        [leaves[0].segment_id, leaves[1].segment_id],
        summary_text="Earlier work established the inputs.",
        proof=CoverageCommitProof(
            message_ids=(1, 2, 3, 4),
            source_hash=canonical_message_hash(first_four),
            child_source_hashes=(leaves[0].source_hash, leaves[1].source_hash),
            valid=True,
        ),
        provider_id="test",
        model_id="summary",
        token_estimates={"deskpet-conservative-v1": 10},
    )
    planner = SessionHistoryPlanner(
        _SessionDB(rows),
        segment_store,
        token_estimator=_estimate,
        raw_leaf_max_messages=2,
    )

    plan = await planner.plan("s1", available_tokens=110)

    assert plan.blocked is True
    assert plan.blocked_reason == "coverage_compaction_required"
    assert [message_id for job in plan.compaction_jobs for message_id in job.message_ids] == [
        5,
        6,
        7,
        8,
    ]


@pytest.mark.asyncio
async def test_old_system_and_derived_summary_rows_are_not_eligible(segment_store):
    rows = [
        _message(1, "rules", role="system"),
        _message(2, "[压缩摘要 / compressed summary]\nold", role="assistant"),
        _message(5, "unmarked legacy summary", role="assistant", is_summary=True),
        _message(3, "real user", role="user"),
        _message(4, "real answer", role="assistant"),
    ]
    planner = SessionHistoryPlanner(
        _SessionDB(rows), segment_store, token_estimator=_estimate
    )
    plan = await planner.plan("s1", available_tokens=100)
    assert [row["id"] for row in plan.messages] == [3, 4]
    assert plan.coverage_report.eligible_message_ids == (3, 4)


def test_report_rejects_gap_and_overlap():
    report = build_coverage_report(
        "s1",
        (1, 2, 3),
        (
            CoverageEntry("summary", (1, 2), segment_id="a"),
            CoverageEntry("raw", (2,)),
        ),
    )
    assert report.valid is False
    assert report.gaps == (3,)
    assert report.overlaps == (2,)
