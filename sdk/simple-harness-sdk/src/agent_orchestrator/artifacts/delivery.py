# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""任务交付：相对底的改动集，与按改动集写回底。

设计见 ``plans/2026-11-26-workspace-base/00-设计-任务工作区以底为基础.md`` §4.7。

* :func:`build_change_set`：任务判定时，用判定树同一份产物（验收通过的）对照底提交，得到改动集；
  在工作仓库里把它们叠成交付提交（父提交 = 底提交）。验的就是交的：判定树 = 底 + 这些产物。
* :func:`delivery_diff`：两个提交之间一个文件的 unified diff。
* :func:`apply_change_set`：把改动集（全部或宿主点名的一部分）写回可写的底。逐文件三方比对由
  适配器做（现状 == 底 → 写，现状 == 新 → 已是目标，其他 → 冲突不写），写完按现状复核。
* :class:`DeliveryLedger`：写回账本。同一操作键再调只给原回执，不重放；同键不同参数拒绝；
  写到一半进程没了，重调时重新执行——三方比对认得出已经写过的文件，不会写两遍。

谁能写回由宿主决定（人点、或档位规则）；模型没有写回工具。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .paths import under_prefix
from .workspace_base import (
    BaseRepo,
    WritableBase,
    _path_problem,
    _readable,
    _secret_name,
    git_blob_id,
)

#: 测试文件：按文件名，或放在测试目录里
_TEST_NAME = re.compile(
    r"^(test_.+\.py|.+_test\.(py|go)|.+\.(test|spec)\.[cm]?[jt]sx?|.+Tests?\.(swift|kt|java))$"
)
_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec"})
_OPERATION_KEY = re.compile(r"^[\x21-\x7e]{1,200}$")


class DeliveryRefused(ValueError):
    """写回请求本身不成立（不是某个文件写不了）：``code`` 给宿主映射成错误码。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Produced:
    """判定树里的一个产物：路径、字节、哪个产物、哪个步骤。"""

    path: str
    data: bytes
    artifact_id: str
    task_id: str


def is_test_path(path: str) -> bool:
    posix = PurePosixPath(path)
    return bool(_TEST_NAME.fullmatch(posix.name)) or any(
        part in _TEST_DIRS for part in posix.parts[:-1]
    )


def _case_spellings(paths: Iterable[str]) -> dict[str, str]:
    """底里每一级路径（文件与目录）：忽略大小写的写法 → 底里的写法。"""

    spellings: dict[str, str] = {}
    for path in paths:
        parts = PurePosixPath(path).parts
        for depth in range(1, len(parts) + 1):
            prefix = "/".join(parts[:depth])
            spellings.setdefault(prefix.casefold(), prefix)
    return spellings


def _refusal(
    path: str, *, exists: bool, withheld: Mapping[str, str], spellings: Mapping[str, str]
) -> str | None:
    """这个改动为什么不能写回（None = 可以写）。"""

    if path in withheld:
        return f"withheld:{withheld[path]}"
    problem = _path_problem(path)
    if problem is not None:
        return problem
    if _secret_name(path):
        return "secret_name"
    if not exists:
        parts = PurePosixPath(path).parts
        for depth in range(1, len(parts) + 1):
            prefix = "/".join(parts[:depth])
            known = spellings.get(prefix.casefold())
            if known is not None and known != prefix:
                return "case_conflict"
    return None


def build_change_set(
    bases: BaseRepo,
    base: Mapping[str, Any],
    produced: Sequence[Produced],
    *,
    reserved: Sequence[str],
    superseded: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """改动集：``produced`` 里和底提交不同的文件（SDK 自己的保留路径不算交付）。

    ``base`` 是任务行里的底记录（``final_report["workspace_base"]``）；``superseded`` 是判定树
    里被覆盖掉的版本（路径 → 那些步骤），照实写进改动集。全部改动（含不能写回的）叠成交付提交，
    它就是判定树验过的那棵树。"""

    base_commit = str(base["commit"])
    manifest = bases.manifest(base_commit)
    withheld = {str(k): str(v) for k, v in dict(base.get("withheld") or {}).items()}
    spellings = _case_spellings(manifest)
    folded_reserved = tuple(prefix.casefold() for prefix in reserved)
    overridden = {path: list(steps) for path, steps in dict(superseded or {}).items()}
    files: list[dict[str, Any]] = []
    changed: dict[str, bytes] = {}
    for item in sorted(produced, key=lambda produced_item: produced_item.path):
        if under_prefix(item.path.casefold(), folded_reserved):
            continue
        entry = manifest.get(item.path)
        blob = git_blob_id(item.data)
        if entry is not None and entry.blob == blob:
            continue
        changed[item.path] = item.data
        files.append(
            {
                "path": item.path,
                "op": "add" if entry is None else "modify",
                # 改了底里已有的测试：交付时单列，交给人判断
                "test": entry is not None and is_test_path(item.path),
                "base_blob": None if entry is None else entry.blob,
                "blob": blob,
                "sha256": hashlib.sha256(item.data).hexdigest(),
                "size": len(item.data),
                "readable": _readable(item.data),
                "executable": entry is not None and entry.executable,
                "artifact_id": item.artifact_id,
                "task_id": item.task_id,
                "overridden_steps": overridden.get(item.path, []),
                "refused": _refusal(
                    item.path, exists=entry is not None, withheld=withheld, spellings=spellings
                ),
            }
        )
    commit = bases.commit_on(
        base_commit,
        changed,
        executable=[item["path"] for item in files if item["executable"]],
    )
    return {
        "base_commit": base_commit,
        "commit": commit,
        "files": files,
        "counts": {
            "files": len(files),
            "added": sum(item["op"] == "add" for item in files),
            "modified": sum(item["op"] == "modify" for item in files),
            "tests": sum(bool(item["test"]) for item in files),
            "refused": sum(item["refused"] is not None for item in files),
        },
    }


def delivery_diff(bases: BaseRepo, change_set: Mapping[str, Any], path: str) -> str:
    """改动集里一个文件相对底的 unified diff；不在改动集里的路径抛 ``KeyError``。"""

    if not any(item["path"] == path for item in change_set.get("files", ())):
        raise KeyError(path)
    return bases.diff(str(change_set["base_commit"]), str(change_set["commit"]), path)


def apply_change_set(
    target: WritableBase,
    bases: BaseRepo,
    change_set: Mapping[str, Any],
    *,
    paths: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """把改动集写回 ``target``：``paths`` 为 None 写全部，否则只写点名的（必须都在改动集里）。

    每个文件一条回执：``status`` 见 :class:`~.workspace_base.FileWrite`；写了的再按现状复核，
    核不上记 ``failed``（``verify_mismatch``）——不把没落地的说成写了。"""

    entries = {str(item["path"]): item for item in change_set.get("files", ())}
    if paths is None:
        chosen = sorted(entries)
    else:
        unknown = sorted(set(paths) - set(entries))
        if unknown:
            raise DeliveryRefused("invalid_request", f"这些路径不在改动集里：{unknown[:5]}")
        chosen = sorted(set(paths))
    writable = [path for path in chosen if entries[path]["refused"] is None]
    blobs = bases.blobs(sorted({str(entries[path]["blob"]) for path in writable}))
    receipt: list[dict[str, Any]] = []
    for path in chosen:
        entry = entries[path]
        row: dict[str, Any] = {
            "path": path,
            "base_blob": entry["base_blob"],
            "blob": entry["blob"],
        }
        if entry["refused"] is not None:
            receipt.append(
                {**row, "status": "refused", "reason": entry["refused"], "current_blob": None}
            )
            continue
        try:
            outcome = target.write_file(
                path,
                blobs[str(entry["blob"])],
                expected=entry["base_blob"],
                executable=bool(entry["executable"]),
            )
        except Exception as error:  # noqa: BLE001 - 宿主的适配器出什么错都记成这个文件写失败
            receipt.append(
                {
                    **row,
                    "status": "failed",
                    "reason": f"adapter_error: {type(error).__name__}",
                    "current_blob": None,
                }
            )
            continue
        receipt.append(
            {
                **row,
                "status": outcome.status,
                "reason": outcome.reason,
                "current_blob": outcome.current,
            }
        )
    applied = [row["path"] for row in receipt if row["status"] == "applied"]
    if applied:
        now = target.current_hashes(applied)
        for row in receipt:
            if row["status"] == "applied" and now.get(row["path"]) != row["blob"]:
                row["status"] = "failed"
                row["reason"] = "verify_mismatch"
    return receipt


class DeliveryLedger:
    """写回账本：``<目录>/<操作键的 sha256>.json``，先记「已占」再写，写完记「完成」与回执。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    def _file(self, operation_key: str) -> Path:
        return self.root / f"{hashlib.sha256(operation_key.encode()).hexdigest()}.json"

    def _save(self, path: Path, body: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, delete=False, encoding="utf-8", suffix=".tmp"
        ) as out:
            json.dump(body, out, ensure_ascii=False, sort_keys=True)
        os.replace(out.name, path)

    def receipts(self, mission_id: str) -> list[dict[str, Any]]:
        """这个任务做完的写回回执，按时间先后。"""

        found = []
        for path in sorted(self.root.glob("*.json")) if self.root.is_dir() else ():
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("state") == "done" and record["params"]["mission_id"] == mission_id:
                found.append(dict(record["receipt"]))
        return sorted(found, key=lambda receipt: (receipt["at_ms"], receipt["operation_key"]))

    def get(self, operation_key: str) -> dict[str, Any] | None:
        path = self._file(operation_key)
        if not path.is_file():
            return None
        return dict(json.loads(path.read_text(encoding="utf-8")))

    def apply(
        self,
        operation_key: str,
        *,
        mission_id: str,
        paths: Sequence[str] | None,
        run: Callable[[], list[dict[str, Any]]],
    ) -> dict[str, Any]:
        if not isinstance(operation_key, str) or not _OPERATION_KEY.fullmatch(operation_key):
            raise DeliveryRefused(
                "invalid_request", "operation_key 是 1 到 200 个可见 ASCII 字符（不含空格）"
            )
        params = {"mission_id": mission_id, "paths": None if paths is None else sorted(set(paths))}
        with self._lock:
            path = self._file(operation_key)
            record = self.get(operation_key)
            resumed = False
            if record is not None:
                if record.get("params") != params:
                    raise DeliveryRefused(
                        "idempotency_conflict", "这个操作键已经用在另一次写回上（任务或文件不同）"
                    )
                if record.get("state") == "done":
                    return {**record["receipt"], "repeat": True}
                resumed = True  # 上次占了键却没写完：重新执行，三方比对认得出写过的文件
            self._save(path, {"operation_key": operation_key, "params": params, "state": "claimed"})
            files = run()
            receipt = {
                "operation_key": operation_key,
                **params,
                "files": files,
                "counts": {
                    status: sum(row["status"] == status for row in files)
                    for status in ("applied", "already", "conflict", "refused", "failed")
                },
                "repeat": False,
                "resumed": resumed,
                "at_ms": time.time_ns() // 1_000_000,
            }
            self._save(
                path,
                {
                    "operation_key": operation_key,
                    "params": params,
                    "state": "done",
                    "receipt": receipt,
                },
            )
            return receipt


__all__ = (
    "DeliveryLedger",
    "DeliveryRefused",
    "Produced",
    "apply_change_set",
    "build_change_set",
    "delivery_diff",
    "git_blob_id",
    "is_test_path",
)
