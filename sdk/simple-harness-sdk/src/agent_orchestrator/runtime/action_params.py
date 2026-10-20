# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""``action:`` 要求的参数：建任务时写下、随要求版本哈希，系统按它准备申请单（第 2 片裁决 2）。

参数只有一个来源——要求本身。值可以是普通 JSON，也可以引用一份已接受的内容产物：
``{"$artifact": "工作区路径"}``。系统准备申请单时把引用绑定成
``{artifact_path, content_hash, size, storage_uri}``（只绑引用，不内联内容），连接器按
``storage_uri`` 读字节、自己核哈希。

文件发布不写参数：要发布的就是目标路径那份产物，系统按发布档案的参数文档绑定（路径、产物号、
内容哈希、大小、存储位置，平铺在参数顶层）。找产物用的是同一个"按路径找已接受产物"的办法。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from simple_harness.contracts import canonical_json

from ..contracts.models import ContractError
from ..contracts.semantic_base import json_object
from ..observability.secrets import find_secrets

ARTIFACT_REF = "$artifact"
PUBLISH = ("file_publish", "publish")


def _artifact_path(value: object) -> str | None:
    """``{"$artifact": "路径"}`` 的路径；不是引用就是 ``None``；写坏的引用是合同错。"""

    if not isinstance(value, Mapping) or ARTIFACT_REF not in value:
        return None
    path = value[ARTIFACT_REF]
    if set(value) != {ARTIFACT_REF} or not isinstance(path, str) or not path.strip():
        raise ContractError('an artifact reference is {"$artifact": "<workspace path>"}')
    if path.startswith("/") or ".." in path.split("/"):
        raise ContractError(f"artifact reference escapes the workspace: {path!r}")
    return path


def artifact_paths(params: Any) -> tuple[str, ...]:
    """参数里引用的全部产物路径（按出现顺序、不重复）。"""

    found: list[str] = []

    def walk(value: Any) -> None:
        path = _artifact_path(value)
        if path is not None:
            if path not in found:
                found.append(path)
            return
        if isinstance(value, Mapping):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(params)
    return tuple(found)


def check_action_params(
    connector: str, operation: str, spec: Any, params: Mapping[str, Any] | None
) -> dict[str, Any]:
    """建任务与改要求时的检查（只查秩序）：规范 JSON、必填参数齐全、产物引用写对、不带密钥。
    返回这条要求用的参数（没写就是空）。文件发布不收参数。"""

    if (connector, operation) == PUBLISH:
        if params:
            raise ContractError("file_publish.publish takes no params: the target is the file")
        return {}
    values = json_object(params or {}, "action params")
    try:
        encoded = canonical_json(values)
    except (TypeError, ValueError) as error:
        raise ContractError("action params are not canonical JSON") from error
    missing = [name for name in getattr(spec, "required_params", ()) if name not in values]
    if missing:
        raise ContractError(f"action params miss required {missing}")
    if len(artifact_paths(values)) > 1:
        raise ContractError("an operation references at most one artifact")
    if find_secrets(encoded):
        raise ContractError("action params look like they carry a secret")
    return values


def bind_artifact_refs(params: Any, artifacts: Mapping[str, Any]) -> Any:
    """把参数里的每个产物引用换成 ``{artifact_path, content_hash, size, storage_uri}``；
    ``artifacts`` 是路径 → 已接受产物（调用方已核过它属于这次的来源）。"""

    path = _artifact_path(params)
    if path is not None:
        artifact = artifacts.get(path)
        if artifact is None:
            raise ContractError(f"no accepted artifact at {path!r}")
        return {
            "artifact_path": path,
            "content_hash": str(artifact.content_hash),
            "size": int(artifact.size_bytes),
            "storage_uri": str(artifact.storage_uri),
        }
    if isinstance(params, Mapping):
        return {key: bind_artifact_refs(value, artifacts) for key, value in params.items()}
    if isinstance(params, (list, tuple)):
        return [bind_artifact_refs(value, artifacts) for value in params]
    return params


__all__ = (
    "ARTIFACT_REF",
    "artifact_paths",
    "bind_artifact_refs",
    "check_action_params",
)
