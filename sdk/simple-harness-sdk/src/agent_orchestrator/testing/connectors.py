# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""测试用的连接器替身（不在公开合同里）。

``TestConfigService`` 是第 7 步演示用的测试服务（ORCH §9.1"修改测试配置项"）：带幂等台账的
文件键值存储。对着它通过不代表生产可用。``PaymentConnectorStub`` 是没有幂等与查询能力的高风险
连接器，部署必须拒绝启用它。
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..runtime.connectors import (
    ConnectorRejected,
    ConnectorTransportError,
    OperationSpec,
    Receipt,
    params_hash,
)


@dataclass
class TestConfigService:
    """The step-7 test service (ORCH §9.1): ``read`` L0, ``set`` L2, ``delete`` L3.

    State and the idempotency ledger live in one JSON file; an operation repeated with
    the same idempotency key returns the original receipt and is not applied again.
    Fault injection for the tests: ``lose_receipt_after_apply`` applies the change and
    then raises a transport error (the caller cannot tell it happened);
    ``reject_next`` refuses the next operation without applying it."""

    path: Path
    name: str = "test_config"
    supports_idempotency: bool = True
    supports_reconciliation: bool = True
    # P3.2 D7: state and ledger live in one file written under an exclusive lock, so a key
    # that is not in the ledger really was never applied
    lookup_authority: str = "authoritative"
    lose_receipt_after_apply: int = 0
    reject_next: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    __test__ = False  # not a pytest test class

    operations: Mapping[str, OperationSpec] = field(
        default_factory=lambda: {
            "read": OperationSpec("read", "L0", (), mutates=False),
            "set": OperationSpec("set", "L2", ("value",)),
            "delete": OperationSpec("delete", "L3", ()),
        }
    )

    def normalize_target(self, target: str) -> str:
        return ".".join(part.strip() for part in str(target).strip().split("."))

    @contextlib.contextmanager
    def _exclusive(self) -> Iterator[None]:
        """One writer at a time, across threads and processes (plan D7-5': the ledger
        write and the change are one atomic replace, so ``lookup`` is authoritative)."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self.path.with_suffix(".lock"), "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"config": {}, "ledger": {}, "applied_count": 0}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        tmp.replace(self.path)

    def state(self) -> dict[str, Any]:
        with self._exclusive():
            return self._load()

    def execute(
        self, operation: str, target: str, params: Mapping[str, Any], *, idempotency_key: str
    ) -> Receipt:

        with self._exclusive():
            self.calls.append(
                {"operation": operation, "target": target, "idempotency_key": idempotency_key}
            )
            if self.reject_next > 0:
                self.reject_next -= 1
                raise ConnectorRejected(f"test_config refused {operation} {target}")
            spec = self.operations.get(operation)
            if spec is None:
                raise ConnectorRejected(f"unknown operation {operation}")
            missing = [p for p in spec.required_params if p not in params]
            if missing:
                raise ConnectorRejected(f"{operation}: missing params {missing}")
            state = self._load()
            existing = state["ledger"].get(idempotency_key)
            if existing is not None:  # the same key never applies twice
                receipt = Receipt.from_json(existing)
            else:
                config = state["config"]
                before = config.get(target)
                if operation == "set":
                    config[target] = params["value"]
                elif operation == "delete":
                    config.pop(target, None)
                after = config.get(target)
                receipt = Receipt(
                    idempotency_key=idempotency_key,
                    connector=self.name,
                    operation=operation,
                    target=target,
                    params_hash=params_hash(params),
                    applied=spec.mutates,
                    before=before,
                    after=after,
                    service_ref=f"test-config#{len(state['ledger']) + 1}",
                    applied_at=time.time(),
                )
                if spec.mutates:
                    state["ledger"][idempotency_key] = receipt.to_json(with_hash=False)
                    state["applied_count"] = int(state.get("applied_count", 0)) + 1
                    self._save(state)
            if self.lose_receipt_after_apply > 0 and spec.mutates:
                self.lose_receipt_after_apply -= 1
                raise ConnectorTransportError("test_config: connection reset after apply")
            return receipt

    def lookup(self, idempotency_key: str) -> Receipt | None:
        with self._exclusive():
            entry = self._load()["ledger"].get(idempotency_key)
            return None if entry is None else Receipt.from_json(entry)


@dataclass
class PaymentConnectorStub:
    """A high-risk connector without idempotency or reconciliation: the deployment must
    refuse to enable it (ORCH §12.3; original §21.2 "财务 Agent → 不能直接支付")."""

    name: str = "payment"
    supports_idempotency: bool = False
    supports_reconciliation: bool = False
    operations: Mapping[str, OperationSpec] = field(
        default_factory=lambda: {"pay": OperationSpec("pay", "L3", ("amount",), kind="event")}
    )

    def normalize_target(self, target: str) -> str:
        return str(target).strip()

    def execute(self, operation, target, params, *, idempotency_key):  # type: ignore[no-untyped-def]
        raise ConnectorRejected("payment connector is not enabled in this build")

    def lookup(self, idempotency_key: str) -> Receipt | None:
        return None


def person_approves(connector: Any) -> Any:
    """把连接器的每个操作标成"总要人批"：自动模式下主机不代批，测试里由人点批准。

    测"人批了才执行"的用例用它；不标的话自动模式会代批（用户 2026-10-20）。"""

    connector.operations = {
        name: replace(spec, always_needs_approval=True)
        for name, spec in dict(connector.operations).items()
    }
    return connector


__all__ = ("PaymentConnectorStub", "TestConfigService", "person_approves")
