"""Faults occur around real Host journal/public SDK reads, not business replay."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
import simple_harness as sdk
from deskpet.operation_audit.composition import compose_terminal_audit
from deskpet.operation_audit.consumer import PublicAuditReader
from deskpet.operation_audit.store import AuditStoreError
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.execution.test_primary_foreground_runtime import build
from tests.operation_audit.test_terminal_audit import drain, setup

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not hasattr(sdk, "RunOperationAuditPageV1"),
        reason="SDK public audit pages unavailable",
    ),
]


class Crash(BaseException):
    pass


class TracedReader(PublicAuditReader):
    def __init__(self, getter):
        super().__init__(getter)
        self.calls = []

    async def read(self, claim, *, page_size):
        self.calls.append((claim.job["snapshot_hash"], claim.job["next_cursor"]))
        return await super().read(claim, page_size=page_size)


async def consumer_at(state, stack, clock):
    consumer = await compose_terminal_audit(
        state,
        stack_getter=lambda: stack,
        subject=local_owner_auth().subject,
        start=False,
        page_size=2,
    )
    consumer.store.clock = lambda: clock[0]
    consumer.lease_seconds = 2
    consumer.reader = TracedReader(lambda: stack)
    return consumer


@pytest.mark.parametrize(
    "point", ["audit.page.before_commit", "audit.page.after_commit"]
)
async def test_page_crash_reopen_preserves_cursor_and_deduplicates_findings(
    tmp_path, point
):
    state, _, provider, runtime, stack, _ = await setup(tmp_path)
    clock = [100.0]
    consumer = await consumer_at(state, stack, clock)
    try:
        sources = await consumer.sources.read()
        job_id = sources[0].job_id
        assert await consumer.tick(max_pages=1) == 1
        first = await consumer.store.inspect(job_id)
        assert first["job"]["next_cursor"] is not None
        expected_cursor = first["job"]["next_cursor"]

        def crash(where):
            if where == point:
                raise Crash()

        consumer.store.fault = crash
        with pytest.raises(Crash):
            await consumer.tick(max_pages=1)
        saved = await consumer.store.inspect(job_id)
        if point.endswith("before_commit"):
            assert saved["job"]["next_cursor"] == expected_cursor
        else:
            assert saved["job"]["next_page"] == first["job"]["next_page"] + 1
        await runtime.close()
        await stack.close()
        clock[0] += 3
        runtime, stack, _ = await build(tmp_path, state, provider)
        resumed = await consumer_at(state, stack, clock)
        await drain(resumed)
        final = await resumed.store.inspect(job_id)
        assert final["job"]["status"] == "enumerated"
        assert final["job"]["snapshot_hash"] == first["job"]["snapshot_hash"]
        assert all(snapshot is not None for snapshot, _ in resumed.reader.calls)
        assert resumed.reader.calls[0][1] == saved["job"]["next_cursor"]
        assert len(final["pages"]) == final["job"]["total_pages"]
        assert len({f["finding_id"] for f in final["findings"]}) == len(
            final["findings"]
        )
        await drain(resumed)
        assert await resumed.store.inspect(job_id) == final
        assert len(provider.requests) == 1
    finally:
        await runtime.close()
        await stack.close()


async def test_unsaved_open_keeps_unknown_and_explicit_new_generation(tmp_path):
    state, _, provider, runtime, stack, _ = await setup(tmp_path)
    clock = [100.0]
    consumer = await consumer_at(state, stack, clock)
    try:
        job_id = (await consumer.sources.read())[0].job_id

        def crash(where):
            if where == "audit.page.before_commit":
                raise Crash()

        consumer.store.fault = crash
        with pytest.raises(Crash):
            await consumer.tick(max_pages=1)
        before = await consumer.store.inspect(job_id)
        assert before["job"]["snapshot_hash"] is None and before["pages"] == []
        assert len(consumer.reader.calls) == 1  # Open really executed; not "not sent".
        old_attempt = before["attempts"][0]["attempt_id"]
        clock[0] += 3
        resumed = await consumer_at(state, stack, clock)
        await drain(resumed)
        final = await resumed.store.inspect(job_id)
        old = next(a for a in final["attempts"] if a["attempt_id"] == old_attempt)
        assert old["outcome"] == "unknown" and old["code"] == "abandoned_unsaved_read"
        new = next(
            a for a in final["attempts"] if a["attempt_id"] == old["superseded_by"]
        )
        assert new["snapshot_generation"] == 2
        assert old["snapshot_generation"] == 1
        assert final["job"]["snapshot_generation"] == 2
        assert final["job"]["status"] == "enumerated"
        assert sum(snapshot is None for snapshot, _ in resumed.reader.calls) == 1
        assert len(provider.requests) == 1
    finally:
        await runtime.close()
        await stack.close()


async def test_lost_first_dispatch_keeps_started_and_reclaim_never_overwrites_winner(
    tmp_path,
):
    state, _, provider, runtime, stack, _ = await setup(tmp_path)
    clock = [100.0]
    consumer = await consumer_at(state, stack, clock)
    try:
        source = (await consumer.sources.read())[0]
        await consumer.store.admit(source)
        first = await consumer.store.claim("old", lease_seconds=2)
        assert await consumer.store.claim("second", lease_seconds=2) is None
        record = await consumer.store.inspect(source.job_id)
        assert record["attempts"][0]["settled_at"] is None
        clock[0] += 3
        winner = await consumer.store.claim("winner", lease_seconds=2)
        assert winner is not None
        with pytest.raises(AuditStoreError, match="audit_lease_lost"):
            await consumer.store.unavailable(first, "reader_failed")
        current = await consumer.store.inspect(source.job_id)
        assert current["job"]["lease_owner"] == "winner"
        assert current["attempts"][0]["outcome"] == "unknown"
        assert current["attempts"][1]["settled_at"] is None
        await consumer.store.unavailable(winner, "reader_failed")
        assert len(provider.requests) == 1
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.parametrize("fault", ["missing", "snapshot", "run", "index", "metadata"])
async def test_pinned_page_fault_never_falls_back_to_open(tmp_path, fault):
    state, _, provider, runtime, stack, _ = await setup(tmp_path)
    clock = [100.0]
    consumer = await consumer_at(state, stack, clock)
    try:
        source = (await consumer.sources.read())[0]
        await consumer.tick(max_pages=1)
        before = await consumer.store.inspect(source.job_id)

        class BadReader(TracedReader):
            async def read(self, claim, *, page_size):
                assert claim.job["snapshot_hash"] is not None
                if fault == "missing":
                    self.calls.append(
                        (claim.job["snapshot_hash"], claim.job["next_cursor"])
                    )
                    raise sdk.RunAuditUnavailable("fixture_missing_snapshot")
                value = await super().read(claim, page_size=page_size)
                changes = {
                    "snapshot": {"snapshot_hash": "f" * 64},
                    "run": {"run_id": "wrong-owner"},
                    "index": {"page_index": value.page_index + 1},
                    "metadata": {
                        "metadata": {**value.to_json()["metadata"], "run_version": -1}
                    },
                }
                return replace(value, **changes[fault])

        consumer.reader = BadReader(lambda: stack)
        await consumer.tick(max_pages=1)
        after = await consumer.store.inspect(source.job_id)
        assert after["job"]["status"] == "unavailable"
        assert after["job"]["snapshot_hash"] == before["job"]["snapshot_hash"]
        assert after["pages"] == before["pages"]
        assert len(consumer.reader.calls) == 1
        assert len(provider.requests) == 1
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.parametrize("failed", [False, True])
async def test_findings_bind_actual_provider_operation_and_unknown_pricing(
    tmp_path, failed
):
    from simple_harness.providers import ProviderRequestRejectedError
    from tests.execution.test_primary_foreground_runtime import Provider

    class UnpricedProvider(Provider):
        async def invoke(self, request, *, cancel):
            if failed:
                self.requests.append(request)
                raise ProviderRequestRejectedError()
            return replace(await super().invoke(request, cancel=cancel), usage=None)

    state, _, provider, runtime, stack, _ = await setup(
        tmp_path, provider=UnpricedProvider()
    )
    consumer = await consumer_at(state, stack, [100.0])
    try:
        source = (await consumer.sources.read())[0]
        await drain(consumer)
        record = await consumer.store.inspect(source.job_id)
        assert record["job"]["status"] == "enumerated"
        operations = [
            op
            for page in record["pages"]
            for op in json.loads(page["payload_json"])["operations"]
        ]
        observed = {(op["operation_id"], op["source_hash"]): op for op in operations}
        assert record[
            "findings"
        ]  # Never allow an empty oracle to prove useful findings.
        assert any(
            f["rule_id"] == "provider_usage_unavailable" for f in record["findings"]
        )
        if failed:
            assert any(
                f["rule_id"] == "operation_error_observed" for f in record["findings"]
            )
        for finding in record["findings"]:
            payload = json.loads(finding["payload_json"])
            original = observed[
                (payload["source_operation_id"], finding["source_hash"])
            ]
            assert finding["operation_id"] == (
                original.get("provider_invocation_id")
                or original.get("effect_id")
                or original["operation_id"]
            )
            assert finding["rule_version"] == "terminal-run-v1"
            assert finding["owner_ref"] == source.owner_ref
            assert finding["owner_component"].startswith("harness.")
            payload = json.loads(finding["payload_json"])
            assert payload["priced_cost_microunits"] is None
            assert payload["price_provenance"] == "unavailable"
        keys = [
            (f["operation_id"], f["rule_id"], f["rule_version"], f["owner_ref"])
            for f in record["findings"]
        ]
        assert len(keys) == len(set(keys))
        for support in record["finding_sources"]:
            assert (support["source_operation_id"], support["source_hash"]) in observed
        if failed:
            errors = [
                f
                for f in record["findings"]
                if f["rule_id"] == "operation_error_observed"
            ]
            assert len(errors) == 1
            error_sources = {
                op["source_hash"]
                for op in operations
                if op.get("error_code") or op.get("error_code_hash")
            }
            assert {
                s["source_hash"]
                for s in record["finding_sources"]
                if s["finding_id"] == errors[0]["finding_id"]
            } == error_sources
        assert len(provider.requests) == 1
    finally:
        await runtime.close()
        await stack.close()


async def test_later_real_terminal_does_not_replace_selected_snapshot(tmp_path):
    from deskpet.memory.human_memory_service import QueueTurnRequest

    state, service, provider, runtime, stack, _ = await setup(tmp_path)
    consumer = await consumer_at(state, stack, [100.0])
    try:
        source = (await consumer.sources.read())[0]
        await consumer.tick(max_pages=1)
        saved = await consumer.store.inspect(source.job_id)
        # Terminal Runs cannot accept conversation-mode commands. Exercise actual
        # foreground admission of a later Run instead of forging a late event.
        await service.enqueue_turn(
            QueueTurnRequest(None, "later-real-turn", "Second actual turn")
        )
        assert await runtime._drive_once()
        assert len(provider.requests) == 2
        await drain(consumer)
        final = await consumer.store.inspect(source.job_id)
        assert final["job"]["snapshot_hash"] == saved["job"]["snapshot_hash"]
        assert final["job"]["total_operations"] == saved["job"]["total_operations"]
        assert final["job"]["status"] == "enumerated"
        with consumer.store.connect() as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM audit_jobs WHERE status='enumerated'"
                ).fetchone()[0]
                == 2
            )
        assert len(provider.requests) == 2  # Audit itself never adds a call.
    finally:
        await runtime.close()
        await stack.close()


async def test_corrupt_bounded_prefix_is_durable_and_does_not_starve_successor(
    tmp_path,
):
    import sqlite3

    from deskpet.memory.human_memory_service import QueueTurnRequest

    state, service, provider, runtime, stack, _ = await setup(tmp_path)
    consumer = await consumer_at(state, stack, [100.0])
    try:
        await service.enqueue_turn(
            QueueTurnRequest(None, "second", "Other actual turn")
        )
        assert await runtime._drive_once()
        with sqlite3.connect(state) as db:
            first, second = db.execute(
                "SELECT terminal_receipt_id,host_run_id,generation FROM "
                "foreground_terminal_receipts ORDER BY terminal_receipt_id"
            ).fetchall()
            # Corruption fixture only: production guard correctly prevents this.
            guard = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='foreground_run_heads_guard'"
            ).fetchone()[0]
            db.execute("DROP TRIGGER foreground_run_heads_guard")
            db.execute(
                "UPDATE foreground_run_heads SET generation=? WHERE host_run_id=?",
                (first[2] + 1, first[1]),
            )
        assert await consumer.sources.read(limit=1) == ()
        with consumer.store.connect() as db:
            rejection = dict(
                db.execute("SELECT * FROM audit_source_rejections").fetchone()
            )
        assert rejection["code"] == "source_binding_invalid"
        assert (await consumer.store.coverage())["source_rejections"] == 1
        successor = (await consumer.sources.read(limit=1))[0]
        assert successor.host_run_id == second[1]
        await drain(consumer)
        assert (await consumer.store.inspect(successor.job_id))["job"][
            "status"
        ] == "enumerated"
        # Repair the actual authority: fingerprint changes, not a permanent blacklist.
        with sqlite3.connect(state) as db:
            db.execute(
                "UPDATE foreground_run_heads SET generation=? WHERE host_run_id=?",
                (first[2], first[1]),
            )
            db.execute(guard)
        repaired = (await consumer.sources.read(limit=1))[0]
        assert repaired.host_run_id == first[1]
        await drain(consumer)
        assert (await consumer.store.inspect(repaired.job_id))["job"][
            "status"
        ] == "enumerated"
        with consumer.store.connect() as db:
            assert (
                dict(db.execute("SELECT * FROM audit_source_rejections").fetchone())
                == rejection
            )
        assert len(provider.requests) == 2
    finally:
        await runtime.close()
        await stack.close()


async def test_reported_usage_and_reconciliation_do_not_multiply_calls_or_findings(
    tmp_path,
):
    from deskpet.operation_audit.consumer import findings_for

    state, _, provider, runtime, stack, _ = await setup(tmp_path)
    consumer = await consumer_at(state, stack, [100.0])
    try:
        source = (await consumer.sources.read())[0]
        await drain(consumer)
        record = await consumer.store.inspect(source.job_id)
        operations = [
            op
            for p in record["pages"]
            for op in json.loads(p["payload_json"])["operations"]
        ]
        heads = [
            op
            for op in operations
            if op["kind"] == "provider" and op["record_type"] == "head"
        ]
        assert len(heads) == len(provider.requests) == 1
        head = heads[0]
        assert head["usage"]["total_tokens"] == 20
        assert any(
            op["record_type"] == "transition"
            and op["operation_id"] == head["operation_id"]
            for op in operations
        )
        assert not any(
            f["rule_id"]
            in {"terminal_operation_unresolved", "provider_usage_unavailable"}
            for f in record["findings"]
        )
        # Controlled public projection boundary: SDK revisions may repeat reported
        # usage in settlement/reconciliation. This is not a forged business event.
        error_head = {**head, "error_code": "provider_error"}
        settle = {**error_head, "record_type": "transition", "source_hash": "a" * 64}
        reconcile = {
            **error_head,
            "kind": "reconciliation",
            "record_type": "receipt",
            "operation_id": "reconciliation:fixture",
            "source_hash": "b" * 64,
        }
        page = json.loads(record["pages"][0]["payload_json"])
        projected = {**page, "operations": [error_head, settle, reconcile]}
        found = findings_for(projected)
        assert len(found) == 3
        assert {f["operation_id"] for f in found} == {head["provider_invocation_id"]}
        # Persist on an isolated Host journal, one source per page: dedupe across pages.
        from deskpet.operation_audit.store import AuditStore

        journal = AuditStore(tmp_path / "projected-audit.db", clock=lambda: 100)
        await journal.initialize()
        await journal.admit(source)
        for index, operation in enumerate((error_head, settle, reconcile)):
            claim = await journal.claim("projection-test")
            one = {
                **page,
                "operations": [operation],
                "page_index": index,
                "page_size": 1,
                "total_pages": 3,
                "total_operations": 3,
                "next_cursor": None if index == 2 else f"opaque-{index}",
            }
            await journal.commit_page(claim, one, findings_for(one))
        persisted = await journal.inspect(source.job_id)
        assert len(persisted["findings"]) == 1
        assert len(persisted["finding_sources"]) == 3
        assert (
            persisted["job"]["processed_operations"] == 3
        )  # DTOs, not physical calls.
        assert all(
            json.loads(p["payload_json"])["operations"][0]["usage"]["total_tokens"]
            == 20
            for p in persisted["pages"]
        )
        for value in (record["job"], persisted["job"]):
            assert (
                not {"physical_calls", "total_usage", "total_tokens", "total_cost"}
                & value.keys()
            )
        # An effect's provider correlation does not make it the same operation.
        effect = {**error_head, "kind": "effect", "effect_id": "effect:fixture"}
        assert (
            findings_for({"operations": [effect]})[0]["operation_id"]
            == "effect:fixture"
        )
        assert len(provider.requests) == 1
    finally:
        await runtime.close()
        await stack.close()


async def test_cancelled_reader_leaves_started_then_new_generation_can_recover(
    tmp_path,
):
    import asyncio

    state, _, provider, runtime, stack, _ = await setup(tmp_path)
    clock = [100.0]
    consumer = await consumer_at(state, stack, clock)
    entered = asyncio.Event()

    class HangingReader(TracedReader):
        async def read(self, claim, *, page_size):
            entered.set()
            await asyncio.Event().wait()

    try:
        source = (await consumer.sources.read())[0]
        consumer.reader = HangingReader(lambda: stack)
        consumer.start()
        await asyncio.wait_for(entered.wait(), 5)
        await consumer.close(timeout=0.01)
        before = await consumer.store.inspect(source.job_id)
        assert before["attempts"][0]["settled_at"] is None
        assert before["pages"] == []
        clock[0] += 3
        resumed = await consumer_at(state, stack, clock)
        await drain(resumed)
        after = await resumed.store.inspect(source.job_id)
        assert after["job"]["status"] == "enumerated"
        assert after["attempts"][0]["outcome"] == "unknown"
        assert len(provider.requests) == 1
    finally:
        await consumer.close()
        await runtime.close()
        await stack.close()


@pytest.mark.parametrize("damage", ["lost_snapshot", "lost_cursor", "bad_page"])
async def test_corrupted_host_resume_anchor_cannot_reopen(tmp_path, damage):
    state, _, provider, runtime, stack, _ = await setup(tmp_path)
    consumer = await consumer_at(state, stack, [100.0])
    try:
        source = (await consumer.sources.read())[0]
        await consumer.tick(max_pages=1)
        with consumer.store.connect() as db:
            if damage == "lost_snapshot":
                db.execute("UPDATE audit_jobs SET snapshot_hash=NULL")
            elif damage == "lost_cursor":
                db.execute("UPDATE audit_jobs SET next_cursor='wrong'")
            else:
                db.execute("UPDATE audit_pages SET payload_json='{}'")
        calls = list(consumer.reader.calls)
        await consumer.tick(max_pages=1)
        record = await consumer.store.inspect(source.job_id)
        assert record["job"]["status"] == "unavailable"
        assert record["job"]["last_code"] == "journal_cursor_invalid"
        assert consumer.reader.calls == calls
        assert len(provider.requests) == 1
    finally:
        await runtime.close()
        await stack.close()


async def test_expired_reader_cannot_commit_after_winner_selects_snapshot(tmp_path):
    import asyncio

    state, _, provider, runtime, stack, _ = await setup(tmp_path)
    clock = [100.0]
    old = await consumer_at(state, stack, clock)
    winner = await consumer_at(state, stack, clock)
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedReader(TracedReader):
        async def read(self, claim, *, page_size):
            result = await super().read(claim, page_size=page_size)
            entered.set()
            await release.wait()
            return result

    old.reader = DelayedReader(lambda: stack)
    task = None
    try:
        source = (await old.sources.read())[0]
        task = asyncio.create_task(old.tick(max_pages=1))
        await asyncio.wait_for(entered.wait(), 5)
        clock[0] += 3
        assert await winner.tick(max_pages=1) == 1
        selected = await winner.store.inspect(source.job_id)
        release.set()
        with pytest.raises(AuditStoreError, match="audit_lease_lost"):
            await task
        assert await winner.store.inspect(source.job_id) == selected
        await drain(winner)
        final = await winner.store.inspect(source.job_id)
        assert final["job"]["status"] == "enumerated"
        assert final["job"]["snapshot_generation"] == 2
        assert final["attempts"][0]["outcome"] == "unknown"
        assert len(provider.requests) == 1
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await runtime.close()
        await stack.close()
