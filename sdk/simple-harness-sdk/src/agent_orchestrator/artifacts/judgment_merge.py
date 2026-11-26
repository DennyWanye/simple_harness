# SPDX-License-Identifier: Apache-2.0
"""从已验收的版本组装判定文件，保留原产物与合并字节的区别。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from ..contracts import Artifact
from .store import read_verified
from .workspace_base import HARNESS_ROOT, BaseEntry, BaseRepo, _readable


@dataclass(frozen=True, slots=True)
class MergeFacts:
    merged_steps: tuple[str, ...] = ()
    merge_conflict: tuple[str, ...] = ()
    merge_skipped: str | None = None
    refused_proposals: tuple[tuple[str, str], ...] = ()

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
                facts["refused_proposals"] = [
                    {"task_id": task, "artifact_id": artifact}
                    for task, artifact in self.refused_proposals
                ]
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


# 权重依次为根要求、端口、验收序号；同一产物可同时出现在步骤清单和端口索引。
Placement = tuple[tuple[int, int, int], str, Artifact, str | None]


def _select_file(
    path: str,
    placements: Sequence[Placement],
    repo: BaseRepo | None,
    entry: BaseEntry | None,
    starts_from_base: Callable[[Artifact], bool],
) -> JudgmentFile:
    winner = placements[0][2]

    def original(reason: str | None = None, conflicts: tuple[str, ...] = ()) -> JudgmentFile:
        return JudgmentFile(
            path, (winner,), facts=MergeFacts(merge_skipped=reason, merge_conflict=conflicts)
        )

    if len({artifact.content_hash for _, _, artifact, _ in placements}) == 1:
        return original()
    if repo is None:
        return original("no_base")
    if entry is None:
        return original("added")
    if not entry.readable:
        return original("binary")

    by_time = sorted(placements, key=lambda item: item[0][2])
    versions = tuple({artifact.id: artifact for _, _, artifact, _ in by_time}.values())
    # 同字节的独立产物也各自核对起点；只有合并字节时才去重。
    if not all(starts_from_base(item) for item in versions):
        return original("lineage")
    base = repo.blobs([entry.blob])[entry.blob]
    contents = {item.id: read_verified(item) for item in versions}
    distinct = list(dict.fromkeys(contents.values()))
    if b"\0" in base or any(not _readable(data) or b"\0" in data for data in distinct):
        return original("binary")

    merged = distinct[0]
    for index, incoming in enumerate(distinct[1:], 1):
        result = repo.merge_file(merged, base, incoming)
        if result is None:
            # 整体回退。逐对复核只用于列名，包含首次冲突之后的版本。
            involved = set()
            prefix = distinct[:index]
            conflict_in_prefix = False
            for left, right in combinations(distinct, 2):
                if repo.merge_file(left, base, right) is None:
                    involved.update((left, right))
                    if right == incoming and left in prefix:
                        conflict_in_prefix = True
            if not conflict_in_prefix:
                # 单个版本都能和本次输入干净合并，冲突来自已累积的组合。
                involved.update((*prefix, incoming))
            steps = tuple(
                dict.fromkeys(item.task_id for item in versions if contents[item.id] in involved)
            )
            return original("conflict", steps)
        merged = result
    steps = tuple(dict.fromkeys(item.task_id for item in versions))
    return JudgmentFile(path, versions, merged, MergeFacts(merged_steps=steps))


def refused_spellings(placements: Sequence[Placement]) -> MergeFacts:
    proposals = tuple(dict.fromkeys((task, artifact.id) for _, task, artifact, _ in placements))
    return MergeFacts(merge_skipped="path_spelling", refused_proposals=proposals)


def place_judgment_file(
    path: str,
    offered: Sequence[Placement],
    repo: BaseRepo | None,
    entry: BaseEntry | None,
    ambiguous: bool,
    starts_from_base: Callable[[Artifact], bool],
) -> tuple[list[JudgmentFile], dict[str, Any] | None]:
    """放置主路径和根要求引用的原端口文件，单独记录被覆盖的版本。"""
    placements = sorted(offered, key=lambda item: item[0], reverse=True)
    kept_weight, kept_task, winner, _port = placements[0]
    if ambiguous:
        # 没有任何提议写入主路径。根要求引用的原件仍可按端口单独核对。
        refused = JudgmentFile(path, (winner,), facts=refused_spellings(placements))
        evidence = [
            JudgmentFile(f"{HARNESS_ROOT}accepted-outputs/{task_id}/{port}/{path}", (artifact,))
            for weight, task_id, artifact, port in placements
            if weight[0] and port is not None
        ]
        return [refused, *evidence], None
    file = _select_file(path, placements, repo, entry, starts_from_base)
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
