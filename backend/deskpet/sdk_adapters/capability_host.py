"""Idempotent Host implementation of SDK capability_build public Ports."""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from simple_harness import JsonValue

from deskpet.product_state.database import ProductStateDatabase


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class MemoryOperationReceipts:
    """Explicit test double; production composition must not use this class."""

    def __init__(self) -> None:
        self.values: dict[
            str, tuple[str, str, Mapping[str, JsonValue] | None]
        ] = {}

    def begin(self, operation_key: str, request_fingerprint: str):
        prior = self.values.get(operation_key)
        if prior is None:
            self.values[operation_key] = (request_fingerprint, "pending", None)
            return "new", None
        if prior[0] != request_fingerprint:
            raise ValueError("operation_key was reused with different intent")
        return prior[1], prior[2]

    def settle(
        self,
        operation_key: str,
        request_fingerprint: str,
        result: Mapping[str, JsonValue],
    ) -> None:
        prior = self.values.get(operation_key)
        if prior is None or prior[0] != request_fingerprint:
            raise ValueError("operation receipt was not prepared")
        self.values[operation_key] = (request_fingerprint, "settled", dict(result))


class ProductCapabilityOperationReceipts:
    def __init__(self, database: ProductStateDatabase, *, clock=time.time) -> None:
        self._database = database
        self._clock = clock

    def begin(self, operation_key: str, request_fingerprint: str):
        row = self._database.connection.execute(
            "SELECT request_fingerprint,state,result_json FROM "
            "capability_host_operation_receipts WHERE operation_key=?",
            (operation_key,),
        ).fetchone()
        if row is None:
            now = float(self._clock())
            try:
                with self._database.connection:
                    self._database.connection.execute(
                        "INSERT INTO capability_host_operation_receipts("
                        "operation_key,request_fingerprint,state,result_json,result_hash,"
                        "created_at,updated_at) VALUES(?,?,'pending',NULL,NULL,?,?)",
                        (operation_key, request_fingerprint, now, now),
                    )
                return "new", None
            except Exception:
                row = self._database.connection.execute(
                    "SELECT request_fingerprint,state,result_json FROM "
                    "capability_host_operation_receipts WHERE operation_key=?",
                    (operation_key,),
                ).fetchone()
                if row is None:
                    raise
        if str(row["request_fingerprint"]) != request_fingerprint:
            raise ValueError("operation_key was reused with different intent")
        result = (
            None
            if row["result_json"] is None
            else json.loads(str(row["result_json"]))
        )
        return str(row["state"]), result

    def settle(
        self,
        operation_key: str,
        request_fingerprint: str,
        result: Mapping[str, JsonValue],
    ) -> None:
        result_json = json.dumps(
            dict(result), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        result_hash = hashlib.sha256(result_json.encode()).hexdigest()
        with self._database.connection:
            changed = self._database.connection.execute(
                "UPDATE capability_host_operation_receipts SET state='settled',"
                "result_json=?,result_hash=?,updated_at=? WHERE operation_key=? "
                "AND request_fingerprint=? AND state='pending'",
                (
                    result_json,
                    result_hash,
                    float(self._clock()),
                    operation_key,
                    request_fingerprint,
                ),
            ).rowcount
        if changed == 1:
            return
        state, prior = self.begin(operation_key, request_fingerprint)
        if state != "settled" or prior != json.loads(result_json):
            raise ValueError("operation receipt settlement CAS conflict")


class ProductCapabilityHostAdapter:
    def __init__(
        self,
        *,
        search=None,
        authorize_source=None,
        build=None,
        store=None,
        authorize_build=None,
        activate=None,
        receipts=None,
        reconcile=None,
        fault=None,
    ) -> None:
        self._ports = {
            "search": search,
            "authorize_source": authorize_source,
            "build": build,
            "store": store,
            "authorize_build": authorize_build,
            "activate": activate,
        }
        if receipts is None:
            raise TypeError("durable capability operation receipts are required")
        self._receipts = receipts
        self._reconcile = reconcile
        self._fault = fault

    async def _call(self, kind: str, *, operation_key: str, **kwargs):
        if not isinstance(operation_key, str) or not operation_key.strip():
            raise ValueError("operation_key is required")
        request_fingerprint = _fingerprint({"kind": kind, **kwargs})
        state, prior = self._receipts.begin(operation_key, request_fingerprint)
        if state == "settled":
            return dict(prior)
        if state == "pending":
            if self._reconcile is None:
                raise RuntimeError("capability operation outcome is pending reconciliation")
            result = self._reconcile(
                kind=kind, operation_key=operation_key, request_fingerprint=request_fingerprint
            )
            if inspect.isawaitable(result):
                result = await result
            if result is None:
                raise RuntimeError("capability operation outcome is pending reconciliation")
            if not isinstance(result, Mapping):
                raise TypeError("capability reconciliation result must be a mapping")
            frozen = dict(result)
            self._receipts.settle(operation_key, request_fingerprint, frozen)
            return frozen
        port = self._ports[kind]
        if port is None:
            raise RuntimeError(f"capability Host port is unavailable: {kind}")
        result = port(operation_key=operation_key, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, Mapping):
            raise TypeError("capability Host result must be a mapping")
        frozen = dict(result)
        if self._fault is not None:
            self._fault("handler_return")
        self._receipts.settle(operation_key, request_fingerprint, frozen)
        return frozen

    async def search(self, *, query, operation_key, admission):
        return await self._call(
            "search", query=query, operation_key=operation_key, admission=admission
        )

    async def authorize_source(self, *, source, operation_key, admission):
        return await self._call(
            "authorize_source",
            source=source,
            operation_key=operation_key,
            admission=admission,
        )

    async def build(self, *, candidate, source_policy, operation_key, admission):
        return await self._call(
            "build",
            candidate=candidate,
            source_policy=source_policy,
            operation_key=operation_key,
            admission=admission,
        )

    async def store(self, *, package, operation_key, admission):
        return await self._call(
            "store", package=package, operation_key=operation_key, admission=admission
        )

    async def authorize_build(self, *, operation_key, admission):
        return await self._call(
            "authorize_build", operation_key=operation_key, admission=admission
        )

    async def activate(
        self, *, package_ref, activation_key, operation_key, admission
    ):
        return await self._call(
            "activate",
            package_ref=package_ref,
            activation_key=activation_key,
            operation_key=operation_key,
            admission=admission,
        )


__all__ = (
    "MemoryOperationReceipts",
    "ProductCapabilityHostAdapter",
    "ProductCapabilityOperationReceipts",
)
