# SPDX-License-Identifier: Apache-2.0
"""从已验收的版本组装判定文件，保留原产物与合并字节的区别。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from graphlib import CycleError, TopologicalSorter
from itertools import combinations
from typing import Any

from ..contracts import Artifact
from .store import read_verified
from .versioning import ArtifactConflict
from .workspace_base import HARNESS_ROOT, BaseEntry, BaseRepo, _readable


@dataclass(frozen=True, slots=True)
class MergeFacts:
    merged_steps: tuple[str, ...] = ()
    merge_conflict: tuple[str, ...] = ()
    merge_skipped: str | None = None

    def to_json(self) -> dict[str, Any]:
        facts: dict[str, Any] = {}
        if self.merged_steps:
            facts["merged_steps"] = list(self.merged_steps)
        if self.merge_conflict:
            facts["merge_conflict"] = list(self.merge_conflict)
        if self.merge_skipped:
            facts["merge_skipped"] = self.merge_skipped
            if self.merge_skipped == "path_spelling":
                facts["refused"] = "path_spelling"
        return facts


@dataclass(frozen=True, slots=True)
class JudgmentFile:
    path: str
    sources: tuple[Artifact, ...]
    merged: bytes | None = None
    facts: MergeFacts = MergeFacts()

    @property
    def artifact(self) -> Artifact | None:
        return self.sources[0] if self.merged is None else None

    def read(self) -> bytes:
        return self.merged if self.merged is not None else read_verified(self.sources[0])


def _lineage(
    versions: Sequence[Artifact], upstream: Callable[[Artifact], Sequence[Artifact]]
) -> tuple[list[Artifact], dict[str, set[str]], list[Artifact]]:
    """沿同一目标的冻结绑定回溯。未被根结论选作产出的前身也能证明共同祖先。"""
    artifacts = {item.id: item for item in versions}
    parents: dict[str, tuple[str, ...]] = {}
    pending = list(versions)
    while pending:
        item = pending.pop()
        predecessors = upstream(item)
        parents[item.id] = tuple(parent.id for parent in predecessors)
        for parent in predecessors:
            if parent.id not in artifacts:
                artifacts[parent.id] = parent
                pending.append(parent)
    try:
        order = list(TopologicalSorter(parents).static_order())
    except CycleError as error:
        raise ArtifactConflict("冻结产物的继承关系成环") from error
    ancestors: dict[str, set[str]] = {}
    for key in order:
        ancestors[key] = {key}.union(*(ancestors[parent] for parent in parents[key]))
    inherited = {key for predecessors in parents.values() for key in predecessors}
    return (
        [artifacts[key] for key in reversed(order)],
        ancestors,
        [item for item in versions if item.id not in inherited],
    )


@dataclass(frozen=True, slots=True)
class _Branch:
    data: bytes
    heads: tuple[Artifact, ...]
    ancestors: set[str]


def _merge_versions(
    repo: BaseRepo,
    entry: BaseEntry,
    versions: Sequence[Artifact],
    winner: Artifact,
    upstream: Callable[[Artifact], Sequence[Artifact]],
) -> JudgmentFile:
    """先在最近的共同版本上合并兄弟，再把独立分支合到任务底。"""
    original = JudgmentFile(entry.path, (winner,))
    order, ancestors, heads = _lineage(versions, upstream)
    data = {item.id: read_verified(item) for item in order}
    if any(not _readable(content) or b"\0" in content for content in data.values()):
        return original
    base = repo.blobs([entry.blob])[entry.blob]
    if b"\0" in base:
        return original

    def common_base(left: Artifact, right: Artifact) -> bytes:
        common = ancestors[left.id] & ancestors[right.id]
        return next((data[item.id] for item in order if item.id in common), base)

    @lru_cache(maxsize=128)
    def combine(left: bytes, ancestor: bytes, right: bytes) -> bytes | None:
        if left == right or right == ancestor:
            return left
        if left == ancestor:
            return right
        return repo.merge_file(left, ancestor, right)

    branches = [_Branch(data[item.id], (item,), ancestors[item.id]) for item in heads]
    conflicts: set[str] = set()
    for ancestor in [*order, None]:
        group = [b for b in branches if ancestor is None or ancestor.id in b.ancestors]
        if len(group) < 2:
            continue
        pivot = base if ancestor is None else data[ancestor.id]
        current = group[0]
        for incoming in group[1:]:
            merged = combine(current.data, pivot, incoming.data)
            if merged is None:
                # 逐对区分同处冲突与组合冲突。继续检查后面的分支以保留完整名单。
                involved = {
                    item.task_id
                    for left in current.heads
                    for right in incoming.heads
                    if combine(data[left.id], common_base(left, right), data[right.id]) is None
                    for item in (left, right)
                }
                conflicts.update(
                    involved or {item.task_id for item in (*current.heads, *incoming.heads)}
                )
                continue
            current = _Branch(
                merged, current.heads + incoming.heads, current.ancestors | incoming.ancestors
            )
        # 折叠结果占最早成员的位置，独立分支仍按验收顺序处理。
        branches = [
            current if branch is group[0] else branch
            for branch in branches
            if branch is group[0] or (ancestor is not None and ancestor.id not in branch.ancestors)
        ]
    if conflicts:
        # 回退后不再有可用于后续合并的冲突字节；从所有原始末版复核其余冲突对。
        for left, right in combinations(heads, 2):
            if combine(data[left.id], common_base(left, right), data[right.id]) is None:
                conflicts.update((left.task_id, right.task_id))
        steps = tuple(dict.fromkeys(item.task_id for item in heads if item.task_id in conflicts))
        return JudgmentFile(entry.path, (winner,), facts=MergeFacts(merge_conflict=steps))
    [result] = branches
    merged = result.data
    return JudgmentFile(
        entry.path,
        tuple(versions),
        merged,
        MergeFacts(merged_steps=tuple(dict.fromkeys(item.task_id for item in versions))),
    )


# 权重依次为根要求、端口、验收序号；同一产物可同时出现在步骤清单和端口索引。
Placement = tuple[tuple[int, int, int], str, Artifact, str | None]


def _select_file(
    path: str,
    placements: Sequence[Placement],
    repo: BaseRepo | None,
    entry: BaseEntry | None,
    upstream: Callable[[Artifact], Sequence[Artifact]],
) -> JudgmentFile:
    winner = placements[0][2]
    if repo is None or entry is None or not entry.readable:
        return JudgmentFile(path, (winner,))
    if len({artifact.content_hash for _, _, artifact, _ in placements}) == 1:
        return JudgmentFile(path, (winner,))
    by_time = sorted(placements, key=lambda item: item[0][2])
    versions = {artifact.id: artifact for _, _, artifact, _ in by_time}
    return _merge_versions(repo, entry, list(versions.values()), winner, upstream)


def place_judgment_file(
    path: str,
    offered: Sequence[Placement],
    repo: BaseRepo | None,
    entry: BaseEntry | None,
    ambiguous: bool,
    upstream: Callable[[Artifact], Sequence[Artifact]],
) -> tuple[list[JudgmentFile], dict[str, Any] | None]:
    """放置主路径和根要求引用的原端口文件，单独记录被覆盖的版本。"""
    placements = sorted(offered, key=lambda item: item[0], reverse=True)
    kept_weight, kept_task, winner, _port = placements[0]
    if ambiguous:
        # 没有任何提议写入主路径。根要求引用的原件仍可按端口单独核对。
        refused = JudgmentFile(path, (winner,), facts=MergeFacts(merge_skipped="path_spelling"))
        evidence = [
            JudgmentFile(f"{HARNESS_ROOT}accepted-outputs/{task_id}/{port}/{path}", (artifact,))
            for weight, task_id, artifact, port in placements
            if weight[0] and port is not None
        ]
        return [refused, *evidence], None
    file = _select_file(path, placements, repo, entry, upstream)
    files = [file]
    losers = []
    seen = {winner.id}
    for weight, task_id, artifact, port in placements:
        if file.merged is None and (
            artifact.id in seen or artifact.content_hash == winner.content_hash
        ):
            continue
        seen.add(artifact.id)
        kept_at = None
        if weight[0] and port is not None:
            kept_at = f"{HARNESS_ROOT}accepted-outputs/{task_id}/{port}/{path}"
            files.append(JudgmentFile(kept_at, (artifact,)))
        if file.merged is None:
            losers.append(
                {
                    "task_id": task_id,
                    "artifact_id": artifact.id,
                    "content_hash": artifact.content_hash,
                    "linked": bool(weight[0]),
                    "port": port,
                    "kept_at": kept_at,
                }
            )
    if not losers:
        return files, None
    kept_by = "acceptance_order"
    if kept_weight[0]:
        kept_by = (
            "acceptance_order_between_linked"
            if any(item["linked"] for item in losers)
            else "criterion_link"
        )
    return files, {
        "path": path,
        "kept_task_id": kept_task,
        "kept_artifact_id": winner.id,
        "kept_content_hash": winner.content_hash,
        "kept_by": kept_by,
        "superseded_task_ids": list(dict.fromkeys(item["task_id"] for item in losers)),
        "superseded": losers,
        **file.facts.to_json(),
    }
