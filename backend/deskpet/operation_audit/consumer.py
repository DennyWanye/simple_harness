"""Finite public SDK page reads; audit cannot dispatch business work."""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Callable
from typing import Any, cast

from deskpet.operation_audit.sources import TerminalSources
from deskpet.operation_audit.store import (
    AuditStore,
    AuditStoreError,
    ReadClaim,
    canonical,
)


class AuditCapabilityUnavailable(RuntimeError):
    pass


class AuditPageInvalid(RuntimeError):
    pass


class PublicAuditReader:
    def __init__(self, stack_getter: Callable[[], Any]) -> None:
        self._stack_getter = stack_getter

    def _client(self) -> Any:
        stack = self._stack_getter()
        if stack is None:
            raise AuditCapabilityUnavailable()
        client = stack.require_ready().client
        if not all(
            callable(getattr(client, name, None))
            for name in ("open_run_operation_audit", "read_run_operation_audit_page")
        ):
            raise AuditCapabilityUnavailable()
        return client

    async def read(self, claim: ReadClaim, *, page_size: int) -> object:
        from simple_harness import RunId

        client = self._client()
        run_id = RunId(claim.job["sdk_run_id"])
        if claim.job["snapshot_hash"] is None:
            return await client.open_run_operation_audit(run_id, page_size=page_size)
        return await client.read_run_operation_audit_page(
            run_id, cursor=claim.job["next_cursor"]
        )


def validated_page(result: object, claim: ReadClaim, page_size: int) -> dict[str, Any]:
    import simple_harness as sdk

    expected = getattr(sdk, "RunOperationAuditPageV1", None)
    if expected is None or type(result) is not expected:
        raise AuditPageInvalid()
    value = cast(Any, result).to_json()
    if len(canonical(value).encode()) > 2 * 1024 * 1024:
        raise AuditPageInvalid()
    if (
        value["run_id"] != claim.job["sdk_run_id"]
        or value["page_index"] != claim.job["next_page"]
    ):
        raise AuditPageInvalid()
    for key in ("page_index", "page_size", "total_operations", "total_pages"):
        if type(value[key]) is not int or value[key] < 0:
            raise AuditPageInvalid()
    if value["page_size"] != page_size or value["total_pages"] != max(
        1, (value["total_operations"] + page_size - 1) // page_size
    ):
        raise AuditPageInvalid()
    if value["page_index"] >= value["total_pages"]:
        raise AuditPageInvalid()
    if len(value["operations"]) != min(
        page_size, value["total_operations"] - value["page_index"] * page_size
    ):
        raise AuditPageInvalid()
    for key in ("snapshot_hash", "page_hash"):
        if (
            not isinstance(value[key], str)
            or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
        ):
            raise AuditPageInvalid()
    cursor = value["next_cursor"]
    last = value["page_index"] + 1 == value["total_pages"]
    if (last and cursor is not None) or (
        not last and (type(cursor) is not str or not 1 <= len(cursor) <= 4096)
    ):
        raise AuditPageInvalid()
    pinned = claim.job["snapshot_hash"]
    if pinned is not None and value["snapshot_hash"] != pinned:
        raise AuditPageInvalid()
    header = canonical(
        {
            k: value[k]
            for k in (
                "run_id",
                "snapshot_hash",
                "page_size",
                "total_operations",
                "total_pages",
                "metadata",
            )
        }
    )
    if claim.job["header_json"] is not None and claim.job["header_json"] != header:
        raise AuditPageInvalid()
    metadata = value["metadata"]
    expected_state = {
        "COMPLETED": "completed",
        "FAILED": "failed",
        "CANCELLED": "cancelled",
        "STOPPED": "cancelled",
    }
    if (
        metadata.get("run_id") != claim.job["sdk_run_id"]
        or metadata.get("run_state") != expected_state.get(claim.job["terminal_state"])
        or value.get("snapshot_source_complete") is not True
    ):
        raise AuditPageInvalid()
    return value


def findings_for(page: dict[str, Any]) -> list[dict[str, Any]]:
    owners = {
        "provider": "harness.provider",
        "effect": "harness.effect",
        "tool": "harness.tool",
        "decision": "harness.authorization",
        "command": "harness.command",
        "context": "harness.context",
    }
    findings = []
    for operation in page["operations"]:
        # Public explicit joins identify the owning operation, not a guessed
        # relationship from timestamps, DTO counts or matching usage.
        owner_id = operation["operation_id"]
        owner_kind = operation["kind"]
        if operation.get("effect_id") is not None:
            owner_id = operation["effect_id"]
            owner_kind = "effect"
        elif operation.get("provider_invocation_id") is not None:
            owner_id = operation["provider_invocation_id"]
            owner_kind = "provider"
        is_head = operation.get("record_type") == "head"
        rules = []
        if operation.get("error_code") or operation.get("error_code_hash"):
            rules.append("operation_error_observed")
        if is_head and operation["state"] in {
            "unknown",
            "pending",
            "handed_off",
            "running",
            "requested",
        }:
            rules.append("terminal_operation_unresolved")
        usage = operation.get("usage")
        if (
            is_head
            and operation["kind"] == "provider"
            and (
                usage is None
                or any(
                    usage.get(k) is None
                    for k in ("input_tokens", "output_tokens", "total_tokens")
                )
            )
        ):
            rules.append("provider_usage_unavailable")
        for rule in rules:
            findings.append(
                {
                    "rule_id": rule,
                    "operation_id": owner_id,
                    "source_operation_id": operation["operation_id"],
                    "source_hash": operation["source_hash"],
                    "owner_component": owners.get(owner_kind, "harness.runtime"),
                    "classification": "investigation",
                    "reported_usage": usage,
                    "priced_cost_microunits": None,
                    "price_provenance": "unavailable",
                }
            )
    return findings


class TerminalAuditConsumer:
    def __init__(
        self,
        store: AuditStore,
        sources: TerminalSources,
        reader: PublicAuditReader,
        *,
        page_size: int = 128,
        lease_seconds: float = 60,
        poll_seconds: float = 5,
    ) -> None:
        if type(page_size) is not int or not 1 <= page_size <= 256:
            raise ValueError("audit page size invalid")
        if lease_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("audit timing invalid")
        self.store, self.sources, self.reader = store, sources, reader
        self.page_size, self.lease_seconds, self.poll_seconds = (
            page_size,
            lease_seconds,
            poll_seconds,
        )
        self.worker = uuid.uuid4().hex
        self.last_code: str | None = None
        self._wake, self._stop = asyncio.Event(), asyncio.Event()
        self._task: asyncio.Task | None = None

    def wake(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="host-terminal-audit")

    async def tick(self, *, max_pages: int = 4) -> int:
        if type(max_pages) is not int or not 1 <= max_pages <= 16:
            raise ValueError("audit tick bound invalid")
        for source in await self.sources.read():
            await self.store.admit(source)
        processed = 0
        for _ in range(max_pages):
            claim = await self.store.claim(
                self.worker, lease_seconds=self.lease_seconds
            )
            if claim is None:
                break
            try:
                if not await self.sources.verify(claim.job):
                    await self.store.unavailable(claim, "source_binding_invalid")
                    continue
                result = await self.reader.read(claim, page_size=self.page_size)
                # Revalidate Host identity after slow SDK reading, before persistence.
                if not await self.sources.verify(claim.job):
                    await self.store.unavailable(claim, "source_binding_invalid")
                    continue
                page = validated_page(result, claim, self.page_size)
            except AuditCapabilityUnavailable:
                await self.store.unavailable(claim, "capability_unavailable")
                continue
            except AuditPageInvalid:
                await self.store.unavailable(claim, "page_invalid")
                continue
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - preserve safe failure taxonomy without raw errors
                # No live fallback even for missing/corrupted SDK snapshots.
                await self.store.unavailable(
                    claim,
                    "snapshot_unavailable"
                    if claim.job["snapshot_hash"]
                    else "reader_failed",
                )
                continue
            await self.store.commit_page(claim, page, findings_for(page))
            processed += 1
        return processed

    async def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                worked = await self.tick()
                self.last_code = None
            except asyncio.CancelledError:
                raise
            except AuditStoreError:
                self.last_code, worked = "audit_store_unavailable", 0
            except Exception:  # noqa: BLE001 - background audit failure cannot dispatch/retry business
                self.last_code, worked = "audit_consumer_unavailable", 0
            if worked:
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), self.poll_seconds)
            except TimeoutError:
                pass

    async def close(self, *, timeout: float = 5) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout)
            except TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
