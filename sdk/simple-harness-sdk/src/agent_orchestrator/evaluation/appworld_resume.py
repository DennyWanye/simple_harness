"""Cold attach to the same surviving AppWorld service at a quiescent checkpoint.

This restores the host observation ledgers, not the remote world. Any service/world
identity change refuses recovery before execution. No initialize/load_state is sent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.request import Request, urlopen

from simple_harness.contracts import canonical_json

from ..contracts.models import ContractError
from ..contracts.semantic_base import content_hash_of
from .appworld import AppWorldConfig


def durable_document(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w") as stream:
        stream.write(canonical_json({"state": state, "sha256": content_hash_of(state)}))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_document(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if set(data) != {"state", "sha256"} or content_hash_of(data["state"]) != data["sha256"]:
        raise ContractError("recovery checkpoint integrity differs")
    return data["state"]


class _AttachedRemoteTransport:
    """Published remote methods only; construction intentionally makes no requests."""

    def __init__(self, config: AppWorldConfig, instruction: str) -> None:
        self.remote_environment_url = str(config.remote_environment_url)
        self.task_id = config.task_id
        self.task = SimpleNamespace(instruction=instruction)

    def _call(self, method: str, **values: Any) -> Any:
        request = Request(
            self.remote_environment_url.rstrip("/") + "/" + method,
            data=canonical_json({"task_id": self.task_id, **values}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=30) as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ContractError("AppWorld response exceeds bound")
        return json.loads(raw)["output"]

    def execute(self, code: str) -> str:
        value = self._call("execute", code=code)
        if not isinstance(value, str):
            raise ContractError("AppWorld execute response is not text")
        return value

    def save_state(self, *, state_id: str) -> Any:
        return self._call("save_state", state_id=state_id)

    def load_state(self, *, state_id: str) -> Any:
        return self._call("load_state", state_id=state_id)

    def close(self) -> None:
        self._call("close")
