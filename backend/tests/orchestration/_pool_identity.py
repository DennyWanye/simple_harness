# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""执行池身份的字节快照（HTN 补齐阶段 A′ 第 1 步）。

部署组装搬进 SDK 之前，用当时的 Host 代码对固定输入拼出全部执行池，把会落盘、入库、参与准入
身份的东西逐项取出来存成基准；搬完以后同样的输入必须逐字节相等——执行池身份一变，已有执行池
就起不来（2026-09-26 教训）。取值全部来自真实拼出来的对象与真实写下的根标记文件，不在这里
重算任何摘要。
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

TENANT = "tenant-pool-identity"
PRINCIPAL = "principal-pool-identity"
TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list", "shell_run")
MODEL = "deepseek-flash"


class StubMeter:
    count_mode = "STUB"


def stub_meter_factory(counter: Any, *, input_limit_tokens: int, max_output_tokens: int) -> StubMeter:
    del counter, input_limit_tokens, max_output_tokens
    return StubMeter()


def fake_models_dir(root: Path) -> Path:
    """A BGE-M3 directory with the three files the port checks (contents are fixed bytes)."""

    model = root / "models" / "bge-m3-int8"
    model.mkdir(parents=True, exist_ok=True)
    for name, body in (("model.int8.onnx", b"onnx"), ("model.int8.onnx.data", b"data"),
                       ("tokenizer.json", b'{"fixed": true}'), ("config.json", b'{"dim": 1024}')):
        (model / name).write_bytes(body)
    return root / "models"


#: Random per directory creation, not part of a pool's identity.
VOLATILE = frozenset({"created_at_ms", "incarnation", "root_incarnation"})


def _plain(value: Any) -> Any:
    if hasattr(value, "to_json"):
        return _plain(value.to_json())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def identity(native: Any, options: dict[str, Any], scratch: Path) -> dict[str, Any]:
    """Everything identity-bearing about ``native`` and the pools in ``options``."""

    pools: dict[str, Any] = {}
    for profile_id, profile in sorted(options["profiles"].items()):
        plane = profile.native_plane
        execution_db = scratch / profile_id / "execution.sqlite3"
        execution_db.parent.mkdir(parents=True, exist_ok=True)
        ports = plane.arp_ports(execution_db)
        marker = sorted(p.name for p in execution_db.parent.iterdir())
        root_markers = {}
        for directory in execution_db.parent.iterdir():
            for path in sorted(directory.glob("*.json")) if directory.is_dir() else ():
                root_markers[f"{directory.name}/{path.name}"] = json.loads(path.read_text())
        pools[profile_id] = {
            "runtime_profile": {
                "model": profile.model, "provider_kind": profile.provider_kind,
                "context_policy": _plain(profile.context_policy),
                "default_max_output_tokens": profile.default_max_output_tokens,
                "max_output_tokens_ceiling": profile.max_output_tokens_ceiling,
                "tokenizer": getattr(profile.tokenizer, "fingerprint", None),
            },
            "authorization_policy": plane.authorization.policy_id,
            "arp_profile": _plain(ports.profile),
            "activation_receipt": _plain(ports.activation_receipt),
            "embedding_resource_ref": _plain(ports.embedding_resource_ref),
            "catalogue_authority": None if ports.catalogue_authority is None else "shared",
            "script_runner": None if ports.script_runner is None else type(ports.script_runner).__name__,
            "files": marker,
            "root_markers": {k: _plain(v) for k, v in root_markers.items()},
        }
    caller = native.control_caller({"command_id": "pin-command", "x": 1})
    read_caller = native.control_caller({"x": 1})
    status = native.status()
    status["profiles"] = [{k: v for k, v in row.items() if k != "background"} for row in status["profiles"]]
    return {
        "catalogue_owner": native.catalogue_owner_id,
        "owner_contract": _plain(native.owner_contract),
        "embedding_ref": _plain(native.embedding_ref),
        "control_caller": _plain(caller),
        "read_caller": _plain(read_caller),
        "status": status,
        "pools": pools,
    }
