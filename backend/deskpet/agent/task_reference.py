"""Fail-closed resolver for typed references to another Run in one Session."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

from deskpet.types.task_reference import (
    TaskReferenceCandidate,
    TaskReferenceIntent,
    TaskReferenceKind,
    TaskReferenceResolution,
    TaskReferenceStatus,
)


_MARKERS: dict[TaskReferenceKind, tuple[str, ...]] = {
    TaskReferenceKind.RECENT: (
        "刚才", "刚刚", "上次", "之前", "previous", "last time",
    ),
    TaskReferenceKind.CONTINUATION: (
        "继续", "接着", "再打开", "再运行", "continue", "resume",
    ),
    TaskReferenceKind.DEMONSTRATIVE: (
        "这个", "那个", "这些", "那些", "已有", "现有", "原来",
        "该项目", "该游戏", "打开它", "运行它", "this", "that",
        "existing", "the game", "the project",
    ),
}


def _tokens(text: str) -> set[str]:
    normalized = str(text or "").strip().lower()
    result = set(re.findall(r"[a-z0-9_-]{2,}", normalized))
    for run in re.findall(r"[一-鿿]+", normalized):
        if len(run) == 1:
            result.add(run)
        else:
            result.update(run[index:index + 2] for index in range(len(run) - 1))
    return result


_REFERENCE_NOISE_TOKENS = _tokens(
    "请 帮我 帮忙 把 的 再 打开 运行 启动 继续 接着 项目 任务 游戏 它 "
    "please open run start continue this that it the"
)
_REFERENCE_NOISE_TERMS = (
    "帮我", "帮忙", "打开", "运行", "启动", "继续", "接着",
    "项目", "任务", "游戏", "please", "open", "run", "start",
    "continue", "this", "that", "the", "it", "and", "please",
    "然后", "并且", "一下", "请", "把", "的", "再", "它", "我", "你",
)


def _has_marker(text: str, marker: str) -> bool:
    if marker.isascii() and " " not in marker:
        return re.search(
            rf"(?<![a-z0-9_]){re.escape(marker)}(?![a-z0-9_])",
            text,
        ) is not None
    return marker in text


def _reference_query_tokens(intent: TaskReferenceIntent) -> set[str]:
    query_text = intent.source_text.lower()
    for marker in intent.markers:
        query_text = query_text.replace(marker, " ")
    for term in _REFERENCE_NOISE_TERMS:
        if term.isascii():
            query_text = re.sub(
                rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])",
                " ",
                query_text,
            )
        else:
            query_text = query_text.replace(term, " ")
    return _tokens(query_text) - _REFERENCE_NOISE_TOKENS


class TaskReferenceResolver:
    """Turns loose language into an explicit resolved/ambiguous contract.

    Marker matching only decides whether a catalog is needed. It never picks
    arbitrary transcript rows. Selection is by stable Run/task identity and
    closes as ambiguous when multiple candidates cannot be distinguished.
    """

    def intent(self, text: str) -> TaskReferenceIntent | None:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return None
        hits = {
            kind: tuple(
                marker
                for marker in markers
                if _has_marker(normalized[:160], marker)
            )
            for kind, markers in _MARKERS.items()
        }
        hits = {kind: markers for kind, markers in hits.items() if markers}
        if not hits:
            return None
        # An explicit temporal cue ("刚才/上次") is more precise than the
        # action verb ("继续/再打开"), so it owns the resolution policy.
        if TaskReferenceKind.RECENT in hits:
            kind = TaskReferenceKind.RECENT
        elif TaskReferenceKind.CONTINUATION in hits:
            kind = TaskReferenceKind.CONTINUATION
        else:
            kind = TaskReferenceKind.DEMONSTRATIVE
        return TaskReferenceIntent(
            kind=kind,
            source_text=str(text),
            markers=tuple(
                dict.fromkeys(
                    marker
                    for markers in hits.values()
                    for marker in markers
                )
            ),
        )

    def candidates(
        self, rows: Iterable[Mapping[str, Any]]
    ) -> tuple[TaskReferenceCandidate, ...]:
        grouped: dict[
            tuple[str, str],
            list[tuple[int, Mapping[str, Any], str]],
        ] = defaultdict(list)
        for index, row in enumerate(rows):
            role = str(row.get("role") or "")
            content = str(row.get("content") or "").strip()
            root = str(row.get("root_run_id") or "").strip()
            scope = str(row.get("task_scope_id") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            if not root and not scope:
                continue
            key = ("run", root) if root else ("scope", scope)
            grouped[key].append((index, row, content))

        result: list[TaskReferenceCandidate] = []
        for (identity_kind, identity), items in grouped.items():
            root = next(
                (
                    str(row.get("root_run_id"))
                    for _index, row, _content in reversed(items)
                    if row.get("root_run_id")
                ),
                None,
            )
            scope = next(
                (
                    str(row.get("task_scope_id"))
                    for _index, row, _content in reversed(items)
                    if row.get("task_scope_id")
                ),
                None,
            )
            first_user = next(
                (
                    content
                    for _index, row, content in items
                    if str(row.get("role") or "") == "user"
                ),
                "",
            )
            last_content = items[-1][2]
            preview_parts = list(
                dict.fromkeys(
                    part for part in (first_user, last_content) if part
                )
            )
            preview = " / ".join(preview_parts)
            if len(preview) > 360:
                preview = preview[:357].rstrip() + "..."
            token_counts = Counter(
                token
                for _index, _row, content in items
                for token in _tokens(content)
            )
            result.append(
                TaskReferenceCandidate(
                    reference_id=f"{identity_kind}:{identity}",
                    root_run_id=root,
                    task_scope_id=scope,
                    row_ids=tuple(
                        int(row["id"])
                        for _index, row, _content in items
                        if isinstance(row.get("id"), int)
                    ),
                    preview=preview,
                    match_tokens=tuple(
                        token
                        for token, _count in token_counts.most_common(256)
                    ),
                    recency=max(index for index, _row, _content in items),
                )
            )
        result.sort(key=lambda item: item.recency, reverse=True)
        return tuple(result)

    def resolve(
        self,
        intent: TaskReferenceIntent | None,
        candidates: tuple[TaskReferenceCandidate, ...],
    ) -> TaskReferenceResolution:
        if intent is None:
            return TaskReferenceResolution(TaskReferenceStatus.NOT_REQUESTED, None)
        if not candidates:
            return TaskReferenceResolution(
                TaskReferenceStatus.MISSING,
                intent,
            )
        query_tokens = _reference_query_tokens(intent)
        if len(candidates) == 1:
            candidate = candidates[0]
            if query_tokens and not (
                query_tokens & set(candidate.match_tokens)
            ):
                return TaskReferenceResolution(
                    TaskReferenceStatus.MISSING,
                    intent,
                    candidates,
                )
            return TaskReferenceResolution(
                TaskReferenceStatus.RESOLVED,
                intent,
                candidates,
                candidate.reference_id,
            )

        ranked = sorted(
            (
                (
                    len(query_tokens & set(item.match_tokens)),
                    item.recency,
                    item,
                )
                for item in candidates
            ),
            reverse=True,
            key=lambda value: (value[0], value[1]),
        )
        top_score, _recency, top = ranked[0]
        runner_up = ranked[1][0]
        if top_score >= 1 and top_score > runner_up:
            return TaskReferenceResolution(
                TaskReferenceStatus.RESOLVED,
                intent,
                candidates,
                top.reference_id,
            )
        if query_tokens and top_score == 0:
            return TaskReferenceResolution(
                TaskReferenceStatus.MISSING,
                intent,
                candidates,
            )
        if intent.kind is TaskReferenceKind.RECENT and not query_tokens:
            return TaskReferenceResolution(
                TaskReferenceStatus.RESOLVED,
                intent,
                candidates,
                candidates[0].reference_id,
            )
        return TaskReferenceResolution(
            TaskReferenceStatus.AMBIGUOUS,
            intent,
            candidates,
        )


__all__ = ["TaskReferenceResolver"]
