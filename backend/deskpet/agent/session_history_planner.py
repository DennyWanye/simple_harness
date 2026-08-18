# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Budget-aware, gap-free Session history planner for Context OS."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from deskpet.agent.tokens import count_messages_tokens
from deskpet.agent.context_segment_store import (
    CausalMessageGroup,
    ContextSegment,
    ContextSegmentStore,
    canonical_message_hash,
    eligible_session_messages,
    group_causal_messages,
    recover_broken_causal_messages,
)


@dataclass(frozen=True, slots=True)
class CoverageEntry:
    kind: str
    message_ids: tuple[int, ...]
    segment_id: str | None = None
    source_hash: str | None = None


@dataclass(frozen=True, slots=True)
class SessionCoverageReport:
    session_id: str
    eligible_message_ids: tuple[int, ...]
    entries: tuple[CoverageEntry, ...]
    valid: bool
    gaps: tuple[int, ...] = ()
    overlaps: tuple[int, ...] = ()
    stale_segment_ids: tuple[str, ...] = ()
    broken_causal_groups: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CoverageCompactionJob:
    session_id: str
    first_message_id: int
    last_message_id: int
    message_ids: tuple[int, ...]
    source_hash: str
    child_segment_ids: tuple[str, ...]
    child_source_hashes: tuple[str, ...]
    estimated_tokens: int
    messages: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class SessionHistoryPlan:
    messages: tuple[Mapping[str, Any], ...]
    coverage_report: SessionCoverageReport
    estimated_tokens: int
    used_summaries: tuple[str, ...] = ()
    required_direct_tools: tuple[str, ...] = ()
    compaction_jobs: tuple[CoverageCompactionJob, ...] = ()
    blocked: bool = False
    blocked_reason: str = ""


def _message_id(message: Mapping[str, Any]) -> int:
    return int(message.get("id", message.get("message_id", 0)) or 0)


def _flatten(groups: Sequence[CausalMessageGroup]) -> list[Mapping[str, Any]]:
    return [message for group in groups for message in group.messages]


def _coverage_entry_for_group(group: CausalMessageGroup) -> CoverageEntry:
    recovered = any(
        bool(message.get("__deskpet_recovered_causal_group"))
        for message in group.messages
    )
    return CoverageEntry("recovered_raw" if recovered else "raw", group.message_ids)


def _estimate_messages(
    messages: Sequence[Mapping[str, Any]],
    estimator: Callable[[Sequence[Mapping[str, Any]]], int],
) -> int:
    return 0 if not messages else int(estimator(messages))


def _summary_message(segment: ContextSegment) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": (
            "[Session history summary]\n"
            + segment.summary_text
            + "\n[detail_ref: session_history_page_in(segment_id='"
            + segment.segment_id
            + "')]"
        ),
    }


def build_coverage_report(
    session_id: str,
    eligible_ids: Sequence[int],
    entries: Sequence[CoverageEntry],
    *,
    stale_segment_ids: Sequence[str] = (),
    broken_causal_groups: Sequence[str] = (),
) -> SessionCoverageReport:
    counts = {int(message_id): 0 for message_id in eligible_ids}
    for entry in entries:
        for message_id in entry.message_ids:
            if message_id in counts:
                counts[message_id] += 1
    gaps = tuple(message_id for message_id in eligible_ids if counts[message_id] == 0)
    overlaps = tuple(message_id for message_id in eligible_ids if counts[message_id] > 1)
    valid = not gaps and not overlaps and not stale_segment_ids and not broken_causal_groups
    return SessionCoverageReport(
        session_id=session_id,
        eligible_message_ids=tuple(int(item) for item in eligible_ids),
        entries=tuple(entries),
        valid=valid,
        gaps=gaps,
        overlaps=overlaps,
        stale_segment_ids=tuple(stale_segment_ids),
        broken_causal_groups=tuple(broken_causal_groups),
    )


class SessionHistoryPlanner:
    def __init__(
        self,
        session_db: object,
        segment_store: ContextSegmentStore,
        *,
        tokenizer_id: str = "deskpet-conservative-v1",
        token_estimator: Callable[[Sequence[Mapping[str, Any]]], int] | None = None,
        raw_leaf_max_messages: int = 32,
        page_size: int = 500,
        compaction_summary_reserve_tokens: int = 256,
    ) -> None:
        self._session_db = session_db
        self._segments = segment_store
        self._tokenizer_id = tokenizer_id
        # Keep the history fit decision on the exact same CJK-aware accounting
        # as the final whole-request budget.  The legacy JSON length estimator
        # under-counted Chinese text, producing a plan that was rejected before
        # AgentLoop could execute the required compaction.
        self._estimate = token_estimator or (
            lambda messages: count_messages_tokens(
                [dict(message) for message in messages]
            )
        )
        self._raw_leaf_max_messages = raw_leaf_max_messages
        self._page_size = page_size
        self._compaction_summary_reserve_tokens = max(
            0, int(compaction_summary_reserve_tokens)
        )

    async def _load_all(self, session_id: str) -> list[Mapping[str, Any]]:
        getter = getattr(self._session_db, "get_messages", None)
        if not callable(getter):
            raise RuntimeError("SessionDB does not provide get_messages")
        rows: list[Mapping[str, Any]] = []
        offset = 0
        while True:
            page = await getter(session_id, limit=self._page_size, offset=offset)
            rows.extend(page)
            if len(page) < self._page_size:
                break
            offset += len(page)
        return rows

    async def plan(
        self,
        session_id: str,
        *,
        available_tokens: int,
        current_message_id: int | None = None,
    ) -> SessionHistoryPlan:
        rows = await self._load_all(session_id)
        eligible, _recovery_errors = recover_broken_causal_messages(
            eligible_session_messages(rows)
        )
        eligible_ids = tuple(_message_id(message) for message in eligible)
        groups, group_errors = group_causal_messages(eligible)
        await self._segments.replace_raw_index(
            session_id,
            eligible,
            max_messages=self._raw_leaf_max_messages,
            tokenizer_id=self._tokenizer_id,
            token_estimator=self._estimate,
            isolate_message_ids=(current_message_id,) if current_message_id is not None else (),
        )
        if group_errors:
            report = build_coverage_report(
                session_id,
                eligible_ids,
                (),
                broken_causal_groups=group_errors,
            )
            return SessionHistoryPlan(
                messages=(),
                coverage_report=report,
                estimated_tokens=0,
                blocked=True,
                blocked_reason="broken_causal_group",
            )

        current_group: CausalMessageGroup | None = None
        history_groups: list[CausalMessageGroup] = []
        for group in groups:
            if current_message_id is not None and current_message_id in group.message_ids:
                if len(group.message_ids) != 1:
                    report = build_coverage_report(
                        session_id,
                        eligible_ids,
                        (),
                        broken_causal_groups=("current_message_splits_causal_group",),
                    )
                    return SessionHistoryPlan(
                        messages=(),
                        coverage_report=report,
                        estimated_tokens=0,
                        blocked=True,
                        blocked_reason="current_message_splits_causal_group",
                    )
                current_group = group
            else:
                history_groups.append(group)

        if current_group is not None and current_group.message_ids[-1] != eligible_ids[-1]:
            report = build_coverage_report(
                session_id,
                eligible_ids,
                (),
                broken_causal_groups=("current_message_is_not_session_tail",),
            )
            return SessionHistoryPlan(
                messages=(),
                coverage_report=report,
                estimated_tokens=0,
                blocked=True,
                blocked_reason="current_message_is_not_session_tail",
            )

        history_messages = _flatten(history_groups)
        raw_cost = _estimate_messages(history_messages, self._estimate)
        current_entries = (
            [CoverageEntry("current", current_group.message_ids)] if current_group else []
        )
        if raw_cost <= max(0, int(available_tokens)):
            raw_entries = [_coverage_entry_for_group(group) for group in history_groups]
            report = build_coverage_report(
                session_id,
                eligible_ids,
                [*raw_entries, *current_entries],
            )
            return SessionHistoryPlan(
                messages=tuple(history_messages),
                coverage_report=report,
                estimated_tokens=raw_cost,
                blocked=not report.valid,
                blocked_reason="" if report.valid else "coverage_invalid",
            )

        summaries, stale_ids = await self._validated_summaries(
            session_id,
            history_messages,
        )
        message_positions = {
            _message_id(message): index for index, message in enumerate(history_messages)
        }
        had_complete_cover = False
        for tail_group_index in range(1, len(history_groups) + 1):
            prefix_groups = history_groups[:tail_group_index]
            tail_groups = history_groups[tail_group_index:]
            prefix_count = sum(len(group.messages) for group in prefix_groups)
            cover = self._minimum_summary_cover(
                summaries,
                message_positions,
                prefix_count,
            )
            if cover is None:
                continue
            had_complete_cover = True
            summary_messages = [_summary_message(segment) for segment in cover]
            tail_messages = _flatten(tail_groups)
            cost = _estimate_messages(summary_messages, self._estimate) + _estimate_messages(
                tail_messages, self._estimate
            )
            if cost > max(0, int(available_tokens)):
                continue
            entries = [
                CoverageEntry(
                    "summary",
                    tuple(
                        _message_id(message)
                        for message in history_messages[
                            message_positions[segment.first_message_id] :
                            message_positions[segment.last_message_id] + 1
                        ]
                    ),
                    segment_id=segment.segment_id,
                    source_hash=segment.source_hash,
                )
                for segment in cover
            ]
            entries.extend(_coverage_entry_for_group(group) for group in tail_groups)
            entries.extend(current_entries)
            report = build_coverage_report(
                session_id,
                eligible_ids,
                entries,
                stale_segment_ids=(),
            )
            if not report.valid:
                continue
            return SessionHistoryPlan(
                messages=tuple([*summary_messages, *tail_messages]),
                coverage_report=report,
                estimated_tokens=cost,
                used_summaries=tuple(segment.segment_id for segment in cover),
                required_direct_tools=("session_history_page_in",),
            )

        covered_prefix_count = 0
        reserved_summary_tokens = 0
        if had_complete_cover:
            for prefix_count in range(len(history_groups), 0, -1):
                cover = self._minimum_summary_cover(
                    summaries,
                    message_positions,
                    sum(len(group.messages) for group in history_groups[:prefix_count]),
                )
                if cover is None:
                    continue
                covered_prefix_count = sum(
                    len(group.messages) for group in history_groups[:prefix_count]
                )
                reserved_summary_tokens = _estimate_messages(
                    [_summary_message(segment) for segment in cover],
                    self._estimate,
                )
                break

        jobs, tail_groups = await self._compaction_jobs(
            session_id,
            history_groups,
            available_tokens=max(
                0,
                int(available_tokens)
                - reserved_summary_tokens
                - self._compaction_summary_reserve_tokens,
            ),
            compact_start_index=covered_prefix_count,
        )
        tail_messages = _flatten(tail_groups)
        tail_entries = [_coverage_entry_for_group(group) for group in tail_groups]
        report = build_coverage_report(
            session_id,
            eligible_ids,
            [*tail_entries, *current_entries],
            stale_segment_ids=stale_ids,
        )
        return SessionHistoryPlan(
            messages=tuple(tail_messages),
            coverage_report=report,
            estimated_tokens=_estimate_messages(tail_messages, self._estimate),
            compaction_jobs=tuple(jobs),
            blocked=True,
            blocked_reason=(
                "minimum_summary_cover_exceeds_budget"
                if had_complete_cover and not jobs
                else "coverage_compaction_required"
            ),
        )

    async def _validated_summaries(
        self,
        session_id: str,
        messages: Sequence[Mapping[str, Any]],
    ) -> tuple[list[ContextSegment], tuple[str, ...]]:
        candidates = await self._segments.list_segments(session_id, kind="summary")
        positions = {_message_id(message): index for index, message in enumerate(messages)}
        valid: list[ContextSegment] = []
        stale: list[str] = []
        for segment in candidates:
            start = positions.get(segment.first_message_id)
            end = positions.get(segment.last_message_id)
            if start is None or end is None or end < start:
                stale.append(segment.segment_id)
            else:
                covered = messages[start : end + 1]
                if (
                    len(covered) != segment.message_count
                    or canonical_message_hash(covered) != segment.source_hash
                ):
                    stale.append(segment.segment_id)
                else:
                    valid.append(segment)
                    continue
            try:
                await self._segments.mark_stale(
                    segment.segment_id,
                    expected_revision=segment.revision,
                )
            except Exception:
                pass
        return valid, tuple(stale)

    def _minimum_summary_cover(
        self,
        summaries: Sequence[ContextSegment],
        positions: Mapping[int, int],
        prefix_count: int,
    ) -> list[ContextSegment] | None:
        by_start: dict[int, list[tuple[int, ContextSegment, int]]] = {}
        for segment in summaries:
            start = positions.get(segment.first_message_id)
            end = positions.get(segment.last_message_id)
            if start is None or end is None or start >= prefix_count or end >= prefix_count:
                continue
            cost = max(
                int(segment.token_estimates.get(self._tokenizer_id, 0)),
                _estimate_messages([_summary_message(segment)], self._estimate),
            )
            by_start.setdefault(start, []).append((end + 1, segment, cost))
        best: dict[int, tuple[int, list[ContextSegment]]] = {0: (0, [])}
        for position in range(prefix_count):
            state = best.get(position)
            if state is None:
                continue
            for end, segment, cost in by_start.get(position, ()):
                candidate = (state[0] + cost, [*state[1], segment])
                prior = best.get(end)
                if prior is None or candidate[0] < prior[0]:
                    best[end] = candidate
        return best.get(prefix_count, (0, None))[1]

    async def _compaction_jobs(
        self,
        session_id: str,
        groups: Sequence[CausalMessageGroup],
        *,
        available_tokens: int,
        compact_start_index: int = 0,
    ) -> tuple[list[CoverageCompactionJob], list[CausalMessageGroup]]:
        all_messages = _flatten(groups)
        raw_segments = await self._segments.list_segments(session_id, kind="raw_index")
        positions = {_message_id(message): index for index, message in enumerate(all_messages)}
        valid_segments: list[tuple[ContextSegment, int, int, list[Mapping[str, Any]]]] = []
        for segment in raw_segments:
            start = positions.get(segment.first_message_id)
            end = positions.get(segment.last_message_id)
            if start is None or end is None or end < start:
                continue
            covered = all_messages[start : end + 1]
            if (
                len(covered) == segment.message_count
                and canonical_message_hash(covered) == segment.source_hash
            ):
                valid_segments.append((segment, start, end, covered))

        tail_start = len(all_messages)
        tail_cost = 0
        for segment, start, end, covered in reversed(valid_segments):
            if end != tail_start - 1:
                break
            cost = _estimate_messages(covered, self._estimate)
            if tail_cost + cost > available_tokens:
                break
            tail_cost += cost
            tail_start = start
        tail = [
            group
            for group in groups
            if positions.get(group.message_ids[0], -1) >= tail_start
        ]
        jobs: list[CoverageCompactionJob] = []
        for segment, start, end, covered in valid_segments:
            if start < max(0, int(compact_start_index)) or end >= tail_start:
                continue
            jobs.append(
                CoverageCompactionJob(
                    session_id=session_id,
                    first_message_id=segment.first_message_id,
                    last_message_id=segment.last_message_id,
                    message_ids=tuple(_message_id(message) for message in covered),
                    source_hash=canonical_message_hash(covered),
                    child_segment_ids=(segment.segment_id,),
                    child_source_hashes=(segment.source_hash,),
                    estimated_tokens=int(
                        segment.token_estimates.get(
                            self._tokenizer_id,
                            _estimate_messages(covered, self._estimate),
                        )
                    ),
                    messages=tuple(covered),
                )
            )
        return jobs, tail


__all__ = [
    "CoverageCompactionJob",
    "CoverageEntry",
    "SessionCoverageReport",
    "SessionHistoryPlan",
    "SessionHistoryPlanner",
    "build_coverage_report",
]
