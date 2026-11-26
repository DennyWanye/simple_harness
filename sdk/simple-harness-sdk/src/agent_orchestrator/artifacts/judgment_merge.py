# SPDX-License-Identifier: Apache-2.0
"""从已验收的版本组装判定文件，保留原产物与合并字节的区别。"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from ..contracts import Artifact
from .store import read_verified
from .versioning import UpstreamInput
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


def ambiguous_paths(paths: Iterable[str]) -> set[str]:
    """比较文件和每一级目录的原拼写；折叠后的名字只用来发现歧义。"""
    prefixes: dict[str, list[str]] = {}
    spelling: dict[str, str] = {}
    ambiguous: set[str] = set()
    for path in paths:
        parts = path.split("/")
        keys = []
        for depth in range(1, len(parts) + 1):
            prefix = "/".join(parts[:depth])
            # 有些组合附标在大小写折叠后会变成字母，须先规范分解再折叠。
            decomposed = unicodedata.normalize("NFD", prefix)
            key = unicodedata.normalize("NFD", decomposed.casefold())
            if spelling.setdefault(key, prefix) != prefix:
                ambiguous.add(key)
            keys.append(key)
        prefixes[path] = keys
    return {path for path, keys in prefixes.items() if not ambiguous.isdisjoint(keys)}


def _merge_versions(
    repo: BaseRepo,
    entry: BaseEntry,
    versions: Sequence[Artifact],
    winner: Artifact,
    upstream: Callable[[Artifact], Sequence[UpstreamInput]],
) -> JudgmentFile:
    """按验收次序折叠多个版本。冻结输入证明的前身由后继承接，独立分支共享任务底。"""
    original = JudgmentFile(entry.path, (winner,))
    data = {item.id: read_verified(item) for item in versions}
    if any(not _readable(content) or b"\0" in content for content in data.values()):
        return original
    base = repo.blobs([entry.blob])[entry.blob]
    if b"\0" in base:
        return original

    # 同一端口与任务产物可能指向同一版本；仅凭步骤先后不能证明后者读过前者。
    inherited = {
        (item.artifact_id, item.content_hash)
        for artifact in versions
        for item in upstream(artifact)
        if item.path == entry.path
    }
    heads = [a for a in versions if (a.id, a.content_hash) not in inherited]
    merged = base
    seen: set[str] = set()
    for index, artifact in enumerate(heads):
        if artifact.content_hash in seen:
            continue
        seen.add(artifact.content_hash)
        incoming = data[artifact.id]
        if merged == base:
            merged = incoming
            continue
        result = repo.merge_file(merged, base, incoming)
        if result is None:
            # 累计版本可能还有无关步骤。逐份复核，只给确实与本版冲突的步骤记名；
            # 单独都能合时，冲突来自组合，保留参与这次组合的步骤。
            previous = heads[:index]
            conflicting = (
                previous
                if len(previous) == 1
                else [
                    other
                    for other in previous
                    if repo.merge_file(data[other.id], base, incoming) is None
                ]
            )
            steps = tuple(dict.fromkeys(a.task_id for a in [*(conflicting or previous), artifact]))
            return JudgmentFile(entry.path, (winner,), facts=MergeFacts(merge_conflict=steps))

        merged = result
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
    ambiguous: bool,
    upstream: Callable[[Artifact], Sequence[UpstreamInput]],
) -> JudgmentFile:
    winner = placements[0][2]
    if ambiguous:
        return JudgmentFile(path, (winner,), facts=MergeFacts(merge_skipped="path_spelling"))
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
    upstream: Callable[[Artifact], Sequence[UpstreamInput]],
) -> tuple[list[JudgmentFile], dict[str, Any] | None]:
    """放置主路径和根要求引用的原端口文件，单独记录被覆盖的版本。"""
    placements = sorted(offered, key=lambda item: item[0], reverse=True)
    kept_weight, kept_task, winner, _port = placements[0]
    file = _select_file(path, placements, repo, entry, ambiguous, upstream)
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
