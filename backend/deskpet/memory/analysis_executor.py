# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5b Task 4 — Host ``MemoryAnalysisExecutorPort`` + ``MemoryAnalysisDeliveryAuthorityPort``.

``HostMemoryAnalysisExecutor`` is **one object** that the v7 builder binds as
``analysis_delivery_authority`` and the ``DurableMemoryJobRunner`` uses as both
executor and delivery authority (spike A2 fact 3: identity-bound).

``analyze_memory(request)`` (runs outside every Memory transaction):

1. ``request.ordered_evidence_refs`` → Host ``memory_ingestion_evidence_links`` →
   the terminal outbox row → durable ``run_binding`` (``SdkRunBindingV1`` record
   frozen at the Host terminal commit) + ``host_run_id`` / ``sdk_run_id``;
   unresolvable → executor error (Memory retry → dead_letter, zero calls);
2. ``request.provider_id / model_id / model_config_hash`` must equal the values
   derived from that binding (``analysis_lineage.binding_model_config_hash``) →
   otherwise ``analysis_lineage_mismatch`` (audited, zero calls);
3. ``RunBoundInvoker.invoke(purpose="analysis", request_hash=request.request_hash,
   evidence_set_key=sha256(canonical(request − {job_id, attempt, idempotency_key})),
   members=(subject, run_id, evidence_id…))`` — three-key lookup first: a
   ``succeeded`` row replays its durable envelope (zero calls); any member with an
   open ``handed_off`` / ``sent_unknown`` attempt → blocked (zero calls) and a
   durable Host ``memory.analysis.blocked`` audit row, so Memory's bounded retry
   ends in ``dead_letter``; the adapter is rebuilt from the binding through the
   same resolver as the SDK Run (``not_sent(binding_unrebuildable:*)`` when it
   cannot be);
4. the model's ``memory_analysis_proposal`` → deterministic span derivation →
   ``MemoryMutationPlan`` (``base_revision`` from Memory 0.6.1
   ``current_analysis_apply_head()``) → ``MemoryAnalysisResult`` +
   ``MemoryAnalysisDeliveryReceipt`` → envelope, persisted on the attempt row in
   the same transaction as ``handed_off → succeeded``.

``verify_analysis_delivery`` re-reads that attempt row: issuer, request hash,
Memory attempt and the canonical envelope must match the durable record.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import aiosqlite

from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.memory.analysis_lineage import binding_model_config_hash
from deskpet.memory.analysis_proposal import (
    ANALYSIS_SYSTEM_INSTRUCTION,
    AdmittedItem,
    compile_proposal,
    prompt_items,
    proposal_from_response,
    proposal_tool_spec,
)
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx
from deskpet.sdk_adapters.post_turn_invoker import (
    RunBoundInvoker,
    durable_envelope_json,
    durable_response,
    durable_result_json,
)
from deskpet.task_scope.protocol import canonical_hash, canonical_json

log = logging.getLogger(__name__)

HOST_ANALYSIS_ISSUER_ID = "deskpet-host-analysis-authority/v1"
ANALYSIS_BLOCKED_EVENT = "memory.analysis.blocked"
AUDIT_PAYLOAD_KIND = "analysis_result"


class HostAnalysisExecutorError(RuntimeError):
    """Stable-coded executor failure: Memory records ``analysis_executor_failed`` and retries."""

    def __init__(self, code: str, **detail: Any) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class AnalysisLeaseExpired(RuntimeError):
    code = "analysis_lease_expired"


@dataclass(frozen=True, slots=True)
class ResolvedOutbox:
    outbox_id: str
    host_run_id: str
    sdk_run_id: str
    generation: int
    run_binding: dict[str, Any]
    endpoint_identity: str | None
    lineage: dict[str, Any]


def _host_plan_id(request_hash: str, attempt_id: str) -> str:
    """Deterministic Host analysis plan id (known before derivation — Task 6 F-1)."""

    return f"host-analysis-plan-{hashlib.sha256(f'{request_hash}:{attempt_id}'.encode()).hexdigest()[:32]}"


def evidence_set_key(request: Any) -> str:
    """Attempt-independent identity of the analysis request (design-freeze §6)."""

    body = {k: v for k, v in request.to_json().items() if k not in {"job_id", "attempt", "idempotency_key"}}
    return canonical_hash(json.loads(canonical_json(body)))


class AnalysisLeaseFence:
    """Memory job lease as the fence: reserve for a terminal Run; revalidate against the deadline."""

    def __init__(self, store: ForegroundQueueStore, *, clock: Callable[[], float], lease_seconds: float) -> None:
        self._store = store
        self._clock = clock
        self._lease_seconds = float(lease_seconds)
        self.host_run_id = ""
        self.sdk_run_id = ""
        self._reserved_at: float | None = None

    def bind(self, *, host_run_id: str, sdk_run_id: str) -> None:
        self.host_run_id = host_run_id
        self.sdk_run_id = sdk_run_id
        self._reserved_at = None

    async def reserve_attempt(self, row: Mapping[str, Any], members: Sequence[tuple[str, str, str]]) -> None:
        await self._store.reserve_analysis_attempt(
            host_run_id=self.host_run_id, sdk_run_id=self.sdk_run_id, attempt=row, members=members
        )
        self._reserved_at = float(self._clock())

    async def revalidate(self) -> None:
        started = self._reserved_at
        if started is not None and float(self._clock()) - started > self._lease_seconds:
            raise AnalysisLeaseExpired(AnalysisLeaseExpired.code)


class HostMemoryAnalysisExecutor:
    """Executor + delivery authority over the Host attempt ledger (one object, identity-bound)."""

    issuer_id = HOST_ANALYSIS_ISSUER_ID

    def __init__(
        self,
        db_path: str | Path,
        *,
        adapter_factory: Callable[[Mapping[str, Any]], Any],
        clock: Callable[[], float] = time.time,
        fault_inject: Callable[[str], None] | None = None,
        reconciliation_observer: Callable[[Any], Any] | None = None,
        lease_margin_seconds: float = 30.0,
    ) -> None:
        self._db_path = Path(db_path)
        self._adapter_factory = adapter_factory
        self._clock = clock
        self._fault_inject = fault_inject
        self._reconciliation_observer = reconciliation_observer
        self._lease_margin = float(lease_margin_seconds)
        self._store = ForegroundQueueStore(self._db_path, clock=clock)
        self._evidence = HostEvidenceAuthority(self._db_path)
        self.provider_calls = 0
        self.calls = 0
        self.last_outcome: Any | None = None

    # -- durable reads ---------------------------------------------------

    async def _resolve_outbox(self, request: Any) -> ResolvedOutbox:
        ids = [str(ref.evidence_id) for ref in request.ordered_evidence_refs]
        if not ids:
            raise HostAnalysisExecutorError("analysis_request_without_evidence")
        async with aiosqlite.connect(f"file:{self._db_path}?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            placeholders = ",".join("?" for _ in ids)
            cursor = await db.execute(
                "SELECT DISTINCT o.outbox_id,o.host_run_id,o.sdk_run_id,o.analysis_lineage_json "
                "FROM memory_ingestion_evidence_links l JOIN memory_ingestion_outbox o ON o.outbox_id=l.outbox_id "
                f"WHERE l.evidence_id IN ({placeholders}) ORDER BY o.created_at",
                tuple(ids),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            if not rows:
                raise HostAnalysisExecutorError("analysis_binding_unresolved", reason="no_outbox_link")
            by_run = {str(row["sdk_run_id"]) for row in rows}
            if len(by_run) != 1:
                # Members from several Runs (a Memory batch over a grown evidence set):
                # the lineage triple must agree across them; the binding of the newest
                # terminal is the Run-bound identity the adapter is rebuilt from.
                triples = set()
                for row in rows:
                    lineage = json.loads(str(row["analysis_lineage_json"]))
                    triples.add((lineage.get("provider_id"), lineage.get("model_id"), lineage.get("model_config_hash")))
                if len(triples) != 1:
                    raise HostAnalysisExecutorError("analysis_binding_unresolved", reason="mixed_run_lineage")
            row = rows[-1]
            cursor = await db.execute(
                "SELECT generation FROM foreground_run_heads WHERE host_run_id=?", (str(row["host_run_id"]),)
            )
            head = await cursor.fetchone()
            await cursor.close()
        lineage = json.loads(str(row["analysis_lineage_json"]))
        binding = lineage.get("run_binding")
        if not isinstance(binding, Mapping):
            raise HostAnalysisExecutorError("analysis_binding_unresolved", reason="run_binding_missing")
        return ResolvedOutbox(
            outbox_id=str(row["outbox_id"]),
            host_run_id=str(row["host_run_id"]),
            sdk_run_id=str(row["sdk_run_id"]),
            generation=int(head["generation"]) if head is not None else 1,
            run_binding=dict(binding),
            endpoint_identity=lineage.get("endpoint_identity"),
            lineage=lineage,
        )

    async def _audit(self, sdk_run_id: str | None, reason_code: str, payload_hash: str) -> None:
        from deskpet.sdk_adapters.task_scope_mutation import (
            write_pre_admission_audit_tx,
        )

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                await write_pre_admission_audit_tx(
                    db, sdk_run_id=sdk_run_id, payload_kind=AUDIT_PAYLOAD_KIND, reason_code=reason_code,
                    payload_hash=payload_hash, now=float(self._clock()),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def _durable_envelope(self, request_hash: str) -> tuple[Any | None, str | None]:
        """Newest settled attempt of ``request_hash`` carrying a durable envelope."""

        from simple_harness.runtime import MemoryAnalysisResultEnvelope

        if not self._db_path.exists():
            return None, None
        try:
            async with aiosqlite.connect(f"file:{self._db_path}?mode=ro", uri=True) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT attempt_id,result_envelope_json FROM post_turn_invocation_attempts "
                    "WHERE request_hash=? AND purpose='analysis' AND result_envelope_json IS NOT NULL "
                    "AND (status='succeeded' OR (status='unknown' AND unknown_class='sent_confirmed')) "
                    "ORDER BY attempt_ordinal DESC LIMIT 1",
                    (request_hash,),
                )
                row = await cursor.fetchone()
                await cursor.close()
        except aiosqlite.Error:
            # No attempt ledger here (foreign / missing state.db): nothing is durable.
            return None, None
        if row is None:
            return None, None
        envelope_json = durable_envelope_json(row["result_envelope_json"])
        if envelope_json is None:
            return None, None
        return MemoryAnalysisResultEnvelope.from_json(envelope_json), str(row["attempt_id"])

    # -- executor port ----------------------------------------------------

    async def analyze_memory(self, request: Any) -> Any:
        from simple_harness.runtime import MemoryAnalysisRequest

        if not isinstance(request, MemoryAnalysisRequest):
            raise TypeError("request must use MemoryAnalysisRequest")
        self.calls += 1
        durable, _ = await self._durable_envelope(request.request_hash)
        if durable is not None:
            durable.verify_request(request)
            return durable
        outbox = await self._resolve_outbox(request)
        binding = outbox.run_binding
        expected = (
            str(binding.get("provider_id") or ""),
            str(binding.get("model_id") or ""),
            binding_model_config_hash(binding, endpoint_identity=outbox.endpoint_identity),
        )
        if (request.provider_id, request.model_id, request.model_config_hash) != expected:
            await self._audit(outbox.sdk_run_id, "analysis_lineage_mismatch", request.request_hash)
            raise HostAnalysisExecutorError("analysis_lineage_mismatch")
        items: list[AdmittedItem] = []
        for ref in request.ordered_evidence_refs:
            item = await self._evidence.read_analysis_item(ref.evidence_id)
            if item.envelope.envelope_hash != ref.content_hash:
                raise HostAnalysisExecutorError("analysis_evidence_hash_mismatch", evidence_id=ref.evidence_id)
            items.append(item)
        deadline_seconds = float(request.budget.deadline_ms) / 1000.0
        fence = AnalysisLeaseFence(self._store, clock=self._clock, lease_seconds=deadline_seconds + self._lease_margin)
        fence.bind(host_run_id=outbox.host_run_id, sdk_run_id=outbox.sdk_run_id)
        invoker = RunBoundInvoker(
            self._db_path,
            fence=fence,
            adapter_factory=self._adapter_factory,
            clock=self._clock,
            fault_inject=self._fault_inject,
            reconciliation_observer=self._reconciliation_observer,
        )
        rendered = prompt_items(items)

        def build_request(row: Any) -> Any:
            from simple_harness import RequestId
            from simple_harness.contracts.messages import Message, MessageRole
            from simple_harness.providers import ProviderRequest

            body = {
                "now_iso": datetime.fromtimestamp(float(self._clock())).astimezone().isoformat(timespec="seconds"),
                "subject": request.subject,
                "evidence_items": rendered,
            }
            return ProviderRequest(
                RequestId(f"post-turn-analysis-{request.request_hash[:24]}-{row.attempt_ordinal}"),
                (
                    Message(role=MessageRole.SYSTEM, content=ANALYSIS_SYSTEM_INSTRUCTION),
                    Message(role=MessageRole.USER, content="[analysis evidence]\n" + json.dumps(body, ensure_ascii=False, indent=1)),
                ),
                tools=(proposal_tool_spec(),),
                max_output_tokens=int(request.budget.max_output_tokens),
            )

        started = time.monotonic()
        outcome = await invoker.invoke(
            purpose="analysis",
            host_run_id=outbox.host_run_id,
            sdk_run_id=outbox.sdk_run_id,
            generation=outbox.generation,
            task_scope_id=None,
            closure_watermark=None,
            request_hash=request.request_hash,
            evidence_set_key=evidence_set_key(request),
            members=tuple((request.subject, request.run_id, str(ref.evidence_id)) for ref in request.ordered_evidence_refs),
            binding_record=binding,
            build_request=build_request,
            plan_id=None,
            deadline_seconds=deadline_seconds,
        )
        self.last_outcome = outcome
        self.provider_calls += int(outcome.provider_calls)
        if outcome.status == "reused":
            durable, _ = await self._durable_envelope(request.request_hash)
            if durable is not None:
                durable.verify_request(request)
                return durable
            # Task 4 review F-1: the settled row carries the Provider response but
            # no envelope yet (derivation failed / crashed after settle) →
            # re-derive from the durable response, zero Provider calls.
            response, settled_attempt = await self._durable_response(request.request_hash)
            if response is None or settled_attempt is None:
                raise HostAnalysisExecutorError("analysis_attempt_result_unavailable", attempt_id=outcome.attempt_id)
            return await self._derive_and_attach(
                request, outbox, invoker, response, settled_attempt,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        if outcome.status == "blocked":
            await self._audit(
                outbox.sdk_run_id,
                f"{ANALYSIS_BLOCKED_EVENT}:{outcome.reason_code or 'analysis_attempt_unknown'}",
                request.request_hash,
            )
            raise HostAnalysisExecutorError(
                f"analysis_blocked:{outcome.reason_code}", unknown_class=outcome.unknown_class, attempt_id=outcome.attempt_id
            )
        if outcome.status == "lease_lost" and outcome.ledger_response is not None and outcome.attempt_id is not None:
            # The Provider answered but the Memory lease is gone: the row is already
            # settled `succeeded` (ledger fact); attach the durable result so the next
            # lease owner replays it with zero calls, then let this claim fail stale.
            await self._deliver(
                request, outbox, invoker, outcome, latency_ms=int((time.monotonic() - started) * 1000), attach_only=True,
            )
            raise HostAnalysisExecutorError("analysis_lease_lost", attempt_id=outcome.attempt_id)
        if outcome.status != "succeeded":
            raise HostAnalysisExecutorError(
                str(outcome.reason_code or f"analysis_attempt_{outcome.status}"),
                status=outcome.status, unknown_class=outcome.unknown_class, attempt_id=outcome.attempt_id,
            )
        assert outcome.attempt_id is not None and outcome.response is not None
        attempt_id = str(outcome.attempt_id)
        # Task 4 review F-1 (P1): the Provider answered — settle the row
        # ``succeeded`` with the public response durable in ONE transaction
        # BEFORE any derivation / compilation / audit.  A Host exception after
        # this point can no longer strand the attempt in ``handed_off`` (which
        # would block every re-delivery until dead_letter): the next delivery
        # re-derives from the durable response with zero Provider calls.
        async with invoker.transaction() as db:
            await invoker.settle_succeeded_tx(
                db, attempt_id, response=outcome.response, plan_id=_host_plan_id(request.request_hash, attempt_id),
                result_envelope_json=durable_result_json(response=outcome.response, envelope_json=None),
            )
            self._fault_point("analysis-response-settled")
        return await self._derive_and_attach(
            request, outbox, invoker, outcome.response, attempt_id,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def _durable_response(self, request_hash: str) -> tuple[Any | None, str | None]:
        """Newest settled attempt of ``request_hash`` whose durable copy holds a response."""

        if not self._db_path.exists():
            return None, None
        try:
            async with aiosqlite.connect(f"file:{self._db_path}?mode=ro", uri=True) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT attempt_id,result_envelope_json FROM post_turn_invocation_attempts "
                    "WHERE request_hash=? AND purpose='analysis' AND result_envelope_json IS NOT NULL "
                    "AND status='succeeded' ORDER BY attempt_ordinal DESC LIMIT 1",
                    (request_hash,),
                )
                row = await cursor.fetchone()
                await cursor.close()
        except aiosqlite.Error:
            return None, None
        if row is None:
            return None, None
        response = durable_response(row["result_envelope_json"])
        if response is None:
            return None, None
        return response, str(row["attempt_id"])

    async def _derive_and_attach(
        self, request: Any, outbox: ResolvedOutbox, invoker: RunBoundInvoker, response: Any, attempt_id: str, *,
        latency_ms: int,
    ) -> Any:
        """Derive the result envelope from a settled response and attach it once.

        Any derivation failure is reported as a retryable
        ``analysis_derivation_failed:*`` — the attempt row stays ``succeeded``
        with its durable response, so the retry never calls the Provider.
        """

        try:
            envelope, durable = await self._derive_envelope(request, outbox, response, attempt_id, latency_ms=latency_ms)
        except asyncio.CancelledError:
            raise
        except HostAnalysisExecutorError:
            raise
        except Exception as exc:  # derivation taxonomy (retry from the durable response)
            self._fault_point("analysis-derivation-failed")
            raise HostAnalysisExecutorError(
                f"analysis_derivation_failed:{type(exc).__name__}", attempt_id=attempt_id, detail=str(exc)[:200]
            ) from exc
        async with invoker.transaction() as db:
            await db.execute(
                "UPDATE post_turn_invocation_attempts SET result_envelope_json=? "
                "WHERE attempt_id=? AND (result_envelope_json IS NULL "
                "OR json_extract(result_envelope_json,'$.envelope') IS NULL)",
                (durable, attempt_id),
            )
            self._fault_point("analysis-result-settled")
        return envelope

    async def _deliver(
        self, request: Any, outbox: ResolvedOutbox, invoker: RunBoundInvoker, outcome: Any, *, latency_ms: int,
        attach_only: bool = False,
    ) -> Any:
        """Lease-lost / observer-confirmed rows: the row is already settled, only the
        envelope may be attached (once)."""

        del attach_only
        response = outcome.response if outcome.response is not None else outcome.ledger_response
        return await self._derive_and_attach(request, outbox, invoker, response, str(outcome.attempt_id), latency_ms=latency_ms)

    async def _derive_envelope(
        self, request: Any, outbox: ResolvedOutbox, response: Any, attempt_id: str, *, latency_ms: int,
    ) -> tuple[Any, str]:
        from simple_harness.runtime import (
            MemoryAnalysisDeliveryReceipt,
            MemoryAnalysisResult,
            MemoryAnalysisResultEnvelope,
        )
        from simple_harness_memory.core.jobs import current_analysis_apply_head

        self._fault_point("analysis-before-derive")
        base_revision = current_analysis_apply_head() or 1
        items = [await self._evidence.read_analysis_item(ref.evidence_id) for ref in request.ordered_evidence_refs]
        compiled = compile_proposal(
            proposal_from_response(response),
            request=request,
            items=items,
            base_revision=int(base_revision),
            plan_id=_host_plan_id(request.request_hash, attempt_id),
            now=float(self._clock()),
        )
        for rejected in compiled.rejected:
            await self._audit(outbox.sdk_run_id, rejected.code, canonical_hash(rejected.to_json()))
        usage = getattr(response, "usage", None)
        # 「响应不可用」必须留下可观测信号，不能与「模型主动判定无可记」一样静悄悄
        # 收敛成成功。实测：output_tokens 顶满 max_output_tokens 时工具调用发不完整，
        # 记忆静默丢失而系统认为自己成功了。
        if (
            isinstance(compiled.structured_result, Mapping)
            and compiled.structured_result.get("closure_reason")
            == "analysis_response_unusable"
        ):
            _truncated = int(getattr(usage, "output_tokens", 0) or 0)
            logger.warning(
                "memory.analysis_response_unusable job=%s output_tokens=%s "
                "(输出可能被 max_output_tokens 截断；本轮记忆未物化)",
                request.job_id,
                _truncated,
            )
            await self._audit(
                outbox.sdk_run_id,
                "analysis_response_unusable",
                canonical_hash({"output_tokens": _truncated}),
            )
        provider_response_id = str(getattr(response, "provider_request_id", None) or f"host-attempt:{attempt_id}")
        result = MemoryAnalysisResult(
            job_id=request.job_id,
            run_id=request.run_id,
            request_hash=request.request_hash,
            provider_response_id=provider_response_id,
            structured_result=compiled.structured_result,
            input_tokens=max(0, int(getattr(usage, "input_tokens", 0) or 0)),
            output_tokens=max(0, int(getattr(usage, "output_tokens", 0) or 0)),
            cost_microunits=0,
            latency_ms=max(0, int(latency_ms)),
        )
        issued_at = float(self._clock())
        delivery = MemoryAnalysisDeliveryReceipt(
            receipt_id=f"host-analysis-delivery:{attempt_id}",
            issuer_id=self.issuer_id,
            run_id=result.run_id,
            job_id=result.job_id,
            request_hash=result.request_hash,
            result_hash=result.result_hash,
            attempt=request.attempt,
            provider_response_id=provider_response_id,
            provider_response_hash=hashlib.sha256(provider_response_id.encode("utf-8")).hexdigest(),
            issued_at=issued_at,
            host_receipt_id=attempt_id,
            host_receipt_hash=hashlib.sha256(f"{request.request_hash}:{result.result_hash}:{request.attempt}".encode()).hexdigest(),
        )
        envelope = MemoryAnalysisResultEnvelope(result, delivery)
        return envelope, durable_result_json(response=response, envelope_json=envelope.to_json())

    def _fault_point(self, point: str) -> None:
        if self._fault_inject is not None:
            self._fault_inject(point)

    # -- delivery authority port -------------------------------------------

    async def verify_analysis_delivery(self, request: Any, envelope: Any) -> None:
        envelope.verify_request(request)
        if envelope.delivery_receipt.issuer_id != self.issuer_id:
            raise ValueError("analysis_delivery_issuer_differs")
        durable, _ = await self._durable_envelope(request.request_hash)
        if durable is None:
            raise ValueError("analysis_delivery_not_durable")
        if canonical_json(json.loads(canonical_json(durable.to_json()))) != canonical_json(json.loads(canonical_json(envelope.to_json()))):
            raise ValueError("analysis_delivery_differs")
        if durable.delivery_receipt.attempt != request.attempt or durable.result.result_hash != envelope.result.result_hash:
            raise ValueError("analysis_delivery_lineage_differs")


async def blocked_audit_rows(db_path: str | Path) -> list[tuple[str | None, str, str]]:
    """Durable ``memory.analysis.blocked`` rows (tests / STATUS)."""

    async with aiosqlite.connect(f"file:{Path(db_path)}?mode=ro", uri=True) as db:
        cursor = await db.execute(
            "SELECT sdk_run_id,reason_code,payload_hash FROM host_pre_admission_audit "
            "WHERE payload_kind=? AND reason_code LIKE ? ORDER BY created_at",
            (AUDIT_PAYLOAD_KIND, f"{ANALYSIS_BLOCKED_EVENT}%"),
        )
        rows = await cursor.fetchall()
        await cursor.close()
    return [(None if r[0] is None else str(r[0]), str(r[1]), str(r[2])) for r in rows]


__all__ = [
    "ANALYSIS_BLOCKED_EVENT",
    "AUDIT_PAYLOAD_KIND",
    "HOST_ANALYSIS_ISSUER_ID",
    "AnalysisLeaseExpired",
    "AnalysisLeaseFence",
    "HostAnalysisExecutorError",
    "HostMemoryAnalysisExecutor",
    "ResolvedOutbox",
    "blocked_audit_rows",
    "evidence_set_key",
]
