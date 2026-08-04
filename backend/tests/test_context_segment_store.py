from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from deskpet.memory.context_segment_store import (
    ContextSegmentError,
    ContextSegmentStore,
    CoverageCommitProof,
    SegmentConflictError,
    canonical_message_hash,
    group_causal_messages,
)
from deskpet.memory.migrator import run_migrations


def _message(message_id: int, content: str, *, role: str = "user", **extra):
    return {
        "id": message_id,
        "session_id": "s1",
        "role": role,
        "content": content,
        **extra,
    }


@pytest_asyncio.fixture
async def store(tmp_path):
    db_path = tmp_path / "state.db"
    await run_migrations(db_path)
    return ContextSegmentStore(db_path)


@pytest.mark.asyncio
async def test_raw_index_keeps_tool_group_and_interleaved_ids(store):
    messages = [
        _message(
            1,
            "calling",
            role="assistant",
            tool_calls=[
                {"id": "call-a", "type": "function", "function": {"name": "a"}},
                {"id": "call-b", "type": "function", "function": {"name": "b"}},
            ],
        ),
        _message(4, "a-result", role="tool", tool_call_id="call-a"),
        _message(7, "b-result", role="tool", tool_call_id="call-b"),
        _message(10, "after"),
    ]

    segments = await store.replace_raw_index("s1", messages, max_messages=2)

    assert [segment.message_count for segment in segments] == [3, 1]
    assert segments[0].first_message_id == 1
    assert segments[0].last_message_id == 7
    groups, errors = group_causal_messages(messages)
    assert not errors and groups[0].message_ids == (1, 4, 7)


@pytest.mark.asyncio
async def test_summary_commit_requires_current_child_hashes(store):
    messages = [_message(index, f"m{index}") for index in (1, 4, 7, 10)]
    leaves = await store.replace_raw_index("s1", messages, max_messages=2)
    proof = CoverageCommitProof(
        message_ids=(1, 4, 7, 10),
        source_hash=canonical_message_hash(messages),
        child_source_hashes=tuple(leaf.source_hash for leaf in leaves),
        valid=True,
    )

    summary = await store.commit_summary(
        "s1",
        [leaf.segment_id for leaf in leaves],
        summary_text="The four messages established the task.",
        proof=proof,
        provider_id="test",
        model_id="summary-model",
        token_estimates={"deskpet-conservative-v1": 12},
    )

    assert summary.kind == "summary"
    assert summary.level == 1
    assert summary.message_count == 4
    with pytest.raises(ContextSegmentError):
        await store.commit_summary(
            "s1",
            [leaf.segment_id for leaf in leaves],
            summary_text="stale",
            proof=CoverageCommitProof(
                message_ids=proof.message_ids,
                source_hash=proof.source_hash,
                child_source_hashes=("wrong", "wrong"),
                valid=True,
            ),
            provider_id="test",
            model_id="summary-model",
            token_estimates={"deskpet-conservative-v1": 3},
        )


@pytest.mark.asyncio
async def test_changed_raw_content_invalidates_leaf_and_ancestor(store):
    original = [_message(index, f"m{index}") for index in (1, 2, 3, 4)]
    leaves = await store.replace_raw_index("s1", original, max_messages=2)
    summary = await store.commit_summary(
        "s1",
        [leaf.segment_id for leaf in leaves],
        summary_text="summary",
        proof=CoverageCommitProof(
            message_ids=(1, 2, 3, 4),
            source_hash=canonical_message_hash(original),
            child_source_hashes=tuple(leaf.source_hash for leaf in leaves),
            valid=True,
        ),
        provider_id="test",
        model_id="summary-model",
        token_estimates={"deskpet-conservative-v1": 3},
    )
    changed = list(original)
    changed[1] = _message(2, "edited")

    await store.replace_raw_index("s1", changed, max_messages=2)

    assert (await store.get(leaves[0].segment_id)).status == "stale"
    assert (await store.get(summary.segment_id)).status == "stale"

    restored = await store.replace_raw_index("s1", original, max_messages=2)
    assert (await store.get(restored[0].segment_id)).status == "valid"
    # A lossy ancestor is never revived implicitly; it must be recomputed.
    assert (await store.get(summary.segment_id)).status == "stale"


@pytest.mark.asyncio
async def test_segment_status_is_cas_and_session_delete_purges(store):
    segment = (await store.replace_raw_index("s1", [_message(1, "one")]))[0]
    stale = await store.mark_stale(segment.segment_id, expected_revision=1)
    assert stale.status == "stale" and stale.revision == 2
    with pytest.raises(SegmentConflictError):
        await store.mark_stale(segment.segment_id, expected_revision=1)
    assert await store.delete_session("s1") == 1
    assert await store.get(segment.segment_id) is None


@pytest.mark.asyncio
async def test_orphan_tool_result_is_rejected(store):
    with pytest.raises(ContextSegmentError, match="orphan tool"):
        await store.replace_raw_index(
            "s1",
            [_message(1, "orphan", role="tool", tool_call_id="missing")],
        )


@pytest.mark.asyncio
async def test_concurrent_raw_reconcile_is_idempotent(store):
    messages = [_message(index, f"m{index}") for index in range(1, 7)]
    left, right = await asyncio.gather(
        store.replace_raw_index("s1", messages, max_messages=2),
        store.replace_raw_index("s1", messages, max_messages=2),
    )
    assert [item.segment_id for item in left] == [item.segment_id for item in right]
    valid = await store.list_segments("s1", kind="raw_index")
    assert len(valid) == 3


@pytest.mark.asyncio
async def test_concurrent_raw_reconcile_and_summary_commit_are_bounded(store):
    messages = [_message(index, f"m{index}") for index in range(1, 7)]
    leaves = await store.replace_raw_index("s1", messages, max_messages=2)
    proof = CoverageCommitProof(
        message_ids=tuple(range(1, 7)),
        source_hash=canonical_message_hash(messages),
        child_source_hashes=tuple(leaf.source_hash for leaf in leaves),
        valid=True,
    )

    async def commit():
        return await store.commit_summary(
            "s1",
            [leaf.segment_id for leaf in leaves],
            summary_text="bounded concurrent summary",
            proof=proof,
            provider_id="test",
            model_id="summary-model",
            token_estimates={"deskpet-conservative-v1": 8},
        )

    for _ in range(20):
        _, summary = await asyncio.wait_for(
            asyncio.gather(
                store.replace_raw_index("s1", messages, max_messages=2),
                commit(),
            ),
            timeout=2.0,
        )
        assert summary.status == "valid"

    valid = await store.list_segments("s1", kind="summary")
    assert len(valid) == 1


@pytest.mark.asyncio
async def test_appending_after_partial_summary_preserves_summary_boundary(store):
    original = [_message(index, f"m{index}") for index in range(1, 5)]
    leaves = await store.replace_raw_index("s1", original, max_messages=32)
    proof = CoverageCommitProof(
        message_ids=tuple(range(1, 5)),
        source_hash=canonical_message_hash(original),
        child_source_hashes=(leaves[0].source_hash,),
        valid=True,
    )
    summary = await store.commit_summary(
        "s1",
        [leaves[0].segment_id],
        summary_text="stable partial tail",
        proof=proof,
        provider_id="test",
        model_id="summary-model",
        token_estimates={"deskpet-conservative-v1": 8},
    )

    appended = [*original, _message(5, "m5"), _message(6, "m6")]
    refreshed = await store.replace_raw_index("s1", appended, max_messages=32)

    assert [(item.first_message_id, item.last_message_id) for item in refreshed] == [
        (1, 4),
        (5, 6),
    ]
    assert (await store.get(summary.segment_id)).status == "valid"
