"""Real Host tool occurrences -> Memory grants -> Harness consumed-use read.

Host SQL here reads only Host immutable facts. No SDK tables, invented receipts,
provider counters, or payload-derived authority. Whole final messages are bound
AFTER projection; a history occurrence of the same item is never exempted.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
from simple_harness import (
    ContextFragmentV2, ContextFragmentType, EvidenceRef,
    RecallContextUseIntentV1, RecallFragmentAuthorityBindingV1,
    RecallResultPageRequestV1, RunId, CallId, thaw_json,
)
from simple_harness.execution.provider_invocations import (
    provider_request_fingerprint, provider_request_json,
)
from deskpet.sdk_adapters.context_authority import canonical_sha256
from deskpet.memory.history_source_authority import history_namespace_tx
from deskpet.memory.primary_visibility import read_evidence_pair
from deskpet.memory.trusted_disclosure import resolve_current_disclosure

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdmittedRecallContext:
    run_id: str
    subject: str
    turn_id: str
    evidence_ref: EvidenceRef
    disclosure: object
    parent_request_id: str

    def to_json(self):
        return dict(run_id=self.run_id, subject=self.subject, turn_id=self.turn_id,
                    evidence_ref=self.evidence_ref.to_json(),
                    disclosure=self.disclosure.to_json(), parent_request_id=self.parent_request_id)


class ProductTypedContextUseAuthority:
    def __init__(self, *, state_path, memory_runtime, stack_getter, namespace, ledger, terminal_sink,
                 fault_sink=None):
        self._path = Path(state_path)
        self._memory = memory_runtime
        self.clock = memory_runtime.semantic_clock
        self._stack = stack_getter
        self._namespace = dict(namespace)
        self._ledger = ledger
        self._terminal_sink = terminal_sink
        # Optional ``RunFaultMemo``: a context-use fault is a whole-Run failure
        # whose SDK public code is only ``driver_failed``, so the stable Host
        # code is memoised here and copied into the durable terminal evidence.
        self._fault_sink = fault_sink
        self.authority_scope_ref = "host:typed-use:" + canonical_sha256(namespace)
        self.subject = namespace["subject"]

    @classmethod
    async def create(cls, *, state_path, memory_runtime, stack_getter, ledger, terminal_sink,
                     fault_sink=None):
        # Use the existing Host initialization transaction on first composition;
        # never mint an independent authority epoch or replace an existing one.
        from deskpet.memory.human_memory_program import HumanMemoryProgramStore
        await HumanMemoryProgramStore(state_path).initialize_subject(memory_runtime.principal().actor_id)
        async with aiosqlite.connect(f"file:{Path(state_path)}?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            namespace, _ = await history_namespace_tx(db, memory_runtime.principal().actor_id)
        return cls(state_path=state_path, memory_runtime=memory_runtime,
                   stack_getter=stack_getter, namespace=namespace, ledger=ledger,
                   terminal_sink=terminal_sink, fault_sink=fault_sink)

    async def _admission(self, db, *, run_id, turn_id, parent_request_id):
        namespace, primary = await history_namespace_tx(db, self.subject)
        if namespace != self._namespace:
            raise ValueError("typed_use_host_epoch_changed")
        row = await (await db.execute(
            "SELECT r.*,t.evidence_id,t.evidence_hash FROM foreground_runs r "
            "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id "
            "JOIN foreground_turns t ON t.turn_id=r.turn_id AND t.subject=r.subject "
            "WHERE b.sdk_run_id=?", (run_id,),
        )).fetchone()
        if (row is None or row["subject"] != self.subject or row["turn_id"] != turn_id
                or row["primary_conversation_id"] != primary):
            raise ValueError("typed_use_actual_turn_missing")
        envelope, receipt = await read_evidence_pair(
            db=db, subject=self.subject, primary_ref=primary, evidence_id=row["evidence_id"],
        )
        if envelope.envelope_hash != row["evidence_hash"]:
            raise ValueError("typed_use_actual_source_mismatch")
        # Reuse the existing exact Host turn-json/hash/order authority validation.
        from deskpet.memory.history_source_authority import HostHistorySourceAuthority
        origin = await HostHistorySourceAuthority(self._path).resolve_history_source(
            principal=self._memory.principal(), envelope=envelope, receipt=receipt,
        )
        if origin is None or origin.namespace.to_json() != namespace:
            raise ValueError("typed_use_source_origin_missing")
        disclosure = await resolve_current_disclosure(
            db_path=self._path, run_id=run_id, subject=self.subject, request_id=parent_request_id,
        )
        return AdmittedRecallContext(run_id, self.subject, turn_id,
            EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1), disclosure, parent_request_id)

    async def admitted_tool_context(self, context):
        run_id = context.run_id.value
        view = self._stack().read_primary_tool_parent_use(context)
        parent = view.provider_request_id
        if (view is None or view.invocation_state != "succeeded"
                or view.authority_scope_ref != self.authority_scope_ref
                or view.subject != self.subject or view.run_id != run_id
                or view.provider_request_id != parent):
            raise ValueError("typed_use_parent_handoff_unverified")
        async with aiosqlite.connect(f"file:{self._path}?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            return await self._admission(db, run_id=run_id, turn_id=view.turn_id,
                                         parent_request_id=parent)

    async def build_carrier(self, *, execution, projected, admitted, effect_id, recall_plan=None):
        """Acquire real public pages; persist only actual, retained result bindings.

        ``recall_plan`` is the Host-authored recall this carrier came from
        (query/selection/turn ordinal).  Event AA: the bound result carries a
        finite authority lease, and a turn that outlives it can only stay honest
        by *re-collecting* the same recall — so the plan that produced the
        binding, plus the lease it was issued under, is recorded beside the
        fragments.  Omitting it keeps the pre-AA carrier byte for byte.
        """
        manager = await self._memory.manager()
        result, decision = execution.result, getattr(execution, "execution", execution).decision
        fragments = []
        by_id = {item.selected_item.item_id: (offset, item) for offset, item in enumerate(result.items)}
        for displayed in projected:
            if displayed["lane"] == "short_horizon":
                continue
            offset, item = by_id[displayed["ref"]]
            selected = item.selected_item
            exact = dict(result_id=result.result_id, result_hash=result.result_hash,
                         item_id=selected.item_id, item_hash=item.result_item_hash)
            if (displayed["history_binding"] != exact
                    or displayed["payload"] != thaw_json(item.public_payload)
                    or displayed["payload_hash"] != selected.public_payload_hash):
                raise ValueError("typed_use_projection_differs")
            page = await manager.page_typed_recall_result(principal=self._memory.principal(),
                request=RecallResultPageRequestV1(result.result_id, result.result_hash,
                    offset + 1, offset, 1, 16384, self.clock()))
            if (page.result_id != result.result_id or page.result_hash != result.result_hash
                    or len(page.bindings) != 1 or page.bindings[0].item_id != selected.item_id
                    or page.bindings[0].item_hash != item.result_item_hash):
                raise ValueError("typed_use_actual_page_differs")
            binding = RecallFragmentAuthorityBindingV1(
                decision.decision_id, decision.decision_hash, result.result_id, result.result_hash,
                selected.item_id, item.result_item_hash, None, None, None,
                page.page_id, page.page_hash, None, None, selected.public_payload_hash,
            )
            fragments.append(ContextFragmentV2(
                "host-recall:" + canonical_sha256([admitted.run_id, effect_id, selected.item_id]),
                admitted.run_id, admitted.subject,
                ContextFragmentType.SHORT_HORIZON if displayed["lane"] == "short_horizon_typed"
                else ContextFragmentType.RECALLED_MEMORY,
                selected.source_ref, selected.source_revision, item.public_payload,
                selected.public_payload_hash, displayed["tokens"], displayed["bytes"],
                admitted.disclosure, (admitted.evidence_ref,), binding,
            ).to_json())
        return dict(schema_version=1, admitted=admitted.to_json(), fragments=fragments,
                    **({"recollect": dict(
                        query=str(recall_plan["query"]),
                        memory_types=[str(name) for name in recall_plan["memory_types"]],
                        include_short_horizon=bool(recall_plan["include_short_horizon"]),
                        turn_ordinal=int(recall_plan["turn_ordinal"]),
                        authority_epoch=int(result.authority_epoch),
                        authority_expires_at=float(result.authority_expires_at),
                    )} if recall_plan is not None else {}))

    async def _occurrences(self, db, *, run_id, turn_id, messages):
        """Verify each retained SDK tool effect against its immutable Host carrier."""
        from simple_harness import RequestId
        from simple_harness.providers import ProviderRequest
        rows = []
        message_json = provider_request_json(ProviderRequest(RequestId("hash-only"), tuple(messages)))["messages"]
        for ordinal, (message, raw_message) in enumerate(zip(messages, message_json, strict=True), 1):
            if message.role.value != "tool" or message.name != "context_route":
                continue
            try:
                wrapped = json.loads(message.content)
            except (TypeError, ValueError) as exc:
                raise ValueError("typed_use_tool_content_invalid") from exc
            value = wrapped.get("value") if isinstance(wrapped, Mapping) else None
            if not isinstance(value, Mapping) or "context_route_receipt" not in value:
                continue  # Actual rejected tool contains no successful recall.
            route = value["context_route_receipt"]
            if route.get("route") != "memory_standalone":
                continue
            if not isinstance(message.call_id, CallId):
                raise ValueError("typed_use_tool_call_id_missing")
            raw_call_id = message.call_id.value
            effect_id = route.get("effect_id")
            _, (effect,) = self._stack().read_primary_dependency_facts(run_id, (effect_id,))
            if (effect is None or not effect.terminal or effect.result is None
                    or effect.run_id.value != run_id or effect.tool_name != "context_route"
                    or effect.raw_call_id != raw_call_id or route.get("run_id") != run_id):
                raise ValueError("typed_use_tool_effect_unverified")
            expected = dict(outcome=effect.result.outcome.value, value=thaw_json(effect.result.value),
                            error_code=effect.result.error_code, public_message=effect.result.public_message)
            if wrapped != expected:
                raise ValueError("typed_use_tool_result_differs")
            row = await (await db.execute("SELECT * FROM context_route_tool_invocations "
                "WHERE sdk_run_id=? AND effect_id=?", (run_id, effect_id))).fetchone()
            if row is None or row["verdict"] != "accepted" or row["raw_call_id"] != raw_call_id:
                raise ValueError("typed_use_host_carrier_missing")
            detail = json.loads(row["detail_json"])
            bound = {key: row[key] for key in ("decision_id", "effect_id", "proposal_hash", "raw_call_id", "sdk_run_id", "verdict")}
            if canonical_sha256(dict(bound, detail=detail)) != row["invocation_hash"]:
                raise ValueError("typed_use_host_carrier_hash_differs")
            carrier = detail.get("typed_carrier")
            if (not isinstance(carrier, dict) or carrier.get("schema_version") != 1
                    or detail.get("public_result_hash") != canonical_sha256(value)):
                raise ValueError("typed_use_legacy_or_partial_carrier")
            original = carrier["admitted"]
            admitted = await self._admission(db, run_id=run_id, turn_id=turn_id,
                                            parent_request_id=original["parent_request_id"])
            if original != admitted.to_json():
                raise ValueError("typed_use_admission_binding_differs")
            parent = self._stack().read_provider_context_use(run_id, admitted.parent_request_id)
            if (parent is None or parent.invocation_state != "succeeded" or parent.turn_id != turn_id
                    or parent.authority_scope_ref != self.authority_scope_ref
                    or parent.subject != self.subject or parent.provider_turn_ordinal != effect.turn_ordinal):
                raise ValueError("typed_use_parent_receipt_missing")
            displayed = [f for f in value["fragments"] if f["lane"] != "short_horizon"]
            fragments = tuple(ContextFragmentV2.from_json(f) for f in carrier["fragments"])
            if len(displayed) != len(fragments):
                raise ValueError("typed_use_incomplete_carrier")
            for display, fragment in zip(displayed, fragments, strict=True):
                binding = fragment.recall_binding
                if (fragment.run_id != run_id or fragment.subject != self.subject
                        or fragment.public_payload_hash != display["payload_hash"]
                        or thaw_json(fragment.public_payload) != display["payload"]
                        or fragment.disclosure_context != admitted.disclosure
                        or fragment.evidence_refs != (admitted.evidence_ref,)
                        or binding is None or display["history_binding"] != dict(
                            result_id=binding.result_id, result_hash=binding.result_hash,
                            item_id=binding.item_id, item_hash=binding.item_hash)):
                    raise ValueError("typed_use_fragment_binding_differs")
            if fragments:
                plan = carrier.get("recollect")
                rows.append((effect_id, fragments, (ordinal, canonical_sha256(raw_message)),
                             None if plan is None else dict(plan, admitted=admitted)))
        return tuple(rows)

    async def snapshot_intents(self, *, request, messages):
        async with aiosqlite.connect(f"file:{self._path}?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            namespace, _ = await history_namespace_tx(db, self.subject)
            if namespace != self._namespace:
                raise ValueError("typed_use_host_epoch_changed")
            occurrences = await self._occurrences(db, run_id=request.run_id.value,
                                                  turn_id=request.turn_id, messages=messages)
        # The Host read transaction is closed first on purpose: refreshing a
        # binding executes a real recall and appends a Host receipt, and neither
        # may run inside a reader that a writer would then have to wait on.
        try:
            occurrences = await self._current_occurrences(occurrences, run_id=request.run_id.value)
        except Exception as error:  # noqa: BLE001 - re-raised; only labelled here
            self._record_fault(request.run_id, getattr(error, "code", None))
            raise
        return self._intents(occurrences)

    def _record_fault(self, run_id, code):
        """Memoise the stable Host code for a fault the SDK only calls driver_failed."""
        sink = getattr(self, "_fault_sink", None)
        if sink is None or not code:
            return
        record = getattr(sink, "record", None)
        if record is not None:
            record(run_id, code)

    async def _current_occurrences(self, occurrences, *, run_id):
        """Bind every occurrence to a recall whose use authority is current.

        Event AA: a bound typed recall carries a finite authority lease and the
        recall authority epoch can advance mid-Run.  When the binding this Run
        already handed the model can no longer be authorized, the honest answer
        is not to kill the Run and not to hand the value over unfenced — it is
        to *re-collect* the same recall under a new idempotency purpose and
        re-bind to a result Memory will authorize now.  The re-collection may
        only replace the binding, never the value: a bound source that comes
        back changed (or does not come back) fails closed.
        """
        from deskpet.memory.recall_authority import CONTEXT_USE_LEASE_MARGIN_SECONDS

        moment = float(self.clock())
        rows = []
        for effect_id, fragments, message, plan in occurrences:
            if plan is not None and moment + CONTEXT_USE_LEASE_MARGIN_SECONDS >= float(
                plan["authority_expires_at"]
            ):
                fragments = await self._rebind(run_id=run_id, effect_id=effect_id,
                                               fragments=fragments, plan=plan, now=moment)
            rows.append((effect_id, fragments, message, plan))
        return tuple(rows)

    async def _rebind(self, *, run_id, effect_id, fragments, plan, now):
        """Reuse a live re-collection receipt, or mint the next bounded one."""
        from deskpet.memory.recall_authority import (
            CONTEXT_USE_LEASE_MARGIN_SECONDS, MAX_CONTEXT_USE_RECOLLECTS,
            RecallContextUseAuthorityStale,
        )

        history = await self._ledger.read_context_use_recollections(
            sdk_run_id=run_id, effect_id=effect_id)
        for receipt in reversed(history):
            if now + CONTEXT_USE_LEASE_MARGIN_SECONDS < float(receipt["expires_at"]):
                return self._apply_recollection(fragments, receipt)
        if len(history) >= MAX_CONTEXT_USE_RECOLLECTS:
            _LOG.warning("recall_context_use_recollect_exhausted run_id=%s attempts=%s",
                         run_id, len(history))
            raise RecallContextUseAuthorityStale()
        receipt = await self._recollect(run_id=run_id, effect_id=effect_id, fragments=fragments,
                                        plan=plan, generation=len(history) + 1)
        return self._apply_recollection(fragments, receipt)

    async def _recollect(self, *, run_id, effect_id, fragments, plan, generation):
        """Re-run the *same* recall under its own idempotency purpose, then re-bind.

        Bounded and audited: one durable SDK request per generation, one
        immutable Host receipt per generation, both replayable.  Every bound
        fragment must come back as the same ``(source_ref, source_revision,
        public_payload_hash)`` — that triple is exactly "the bytes the model
        already holds are still the current ones".
        """
        import asyncio

        from deskpet.memory.recall_authority import (
            CONTEXT_USE_RECOLLECT_PURPOSE, CONTEXT_USE_RECOLLECTED,
            RecallContextUseAuthorityStale, RecallContextUseSourceSuperseded,
        )

        try:
            lanes = await self._memory.typed_recall(
                query=plan["query"], run_id=run_id, turn_ordinal=int(plan["turn_ordinal"]),
                memory_types=tuple(plan["memory_types"]),
                include_short_horizon=bool(plan["include_short_horizon"]),
                admitted_context=plan["admitted"],
                idempotency_purpose=CONTEXT_USE_RECOLLECT_PURPOSE,
                idempotency_scope=f"{effect_id}:{generation}",
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - stable, payload-free Host code
            _LOG.warning("recall_context_use_recollect_unavailable run_id=%s generation=%s",
                         run_id, generation)
            raise RecallContextUseAuthorityStale() from error
        execution = getattr(lanes, "execution", lanes)
        result, decision = lanes.result, execution.decision
        manager = await self._memory.manager()
        current = {}
        for offset, item in enumerate(result.items):
            selected = item.selected_item
            current[(selected.source_ref, selected.source_revision,
                     selected.public_payload_hash)] = (offset, item)
        bindings = []
        for fragment in fragments:
            key = (fragment.source_ref, fragment.source_revision, fragment.public_payload_hash)
            if key not in current:
                # The value the model already holds is no longer what Memory
                # answers with. Fail closed: never re-authorize a stale value.
                _LOG.warning("recall_context_use_source_superseded run_id=%s generation=%s",
                             run_id, generation)
                raise RecallContextUseSourceSuperseded()
            offset, item = current[key]
            selected = item.selected_item
            page = await manager.page_typed_recall_result(principal=self._memory.principal(),
                request=RecallResultPageRequestV1(result.result_id, result.result_hash,
                    offset + 1, offset, 1, 16384, self.clock()))
            if (page.result_id != result.result_id or page.result_hash != result.result_hash
                    or len(page.bindings) != 1 or page.bindings[0].item_id != selected.item_id
                    or page.bindings[0].item_hash != item.result_item_hash):
                raise ValueError("typed_use_actual_page_differs")
            bindings.append(dict(
                fragment_id=fragment.fragment_id, source_ref=selected.source_ref,
                source_revision=selected.source_revision,
                public_payload_hash=selected.public_payload_hash,
                item_id=selected.item_id, item_hash=item.result_item_hash,
                page_id=page.page_id, page_hash=page.page_hash,
            ))
        bound = fragments[0].recall_binding
        return await self._ledger.record_context_use_recollection(
            sdk_run_id=run_id, effect_id=effect_id, generation=generation,
            reason_code=CONTEXT_USE_RECOLLECTED,
            bound_result_id=bound.result_id, bound_result_hash=bound.result_hash,
            decision_id=decision.decision_id, decision_hash=decision.decision_hash,
            result_id=result.result_id, result_hash=result.result_hash,
            authority_epoch=int(result.authority_epoch),
            expires_at=float(result.authority_expires_at), bindings=bindings,
        )

    @staticmethod
    def _apply_recollection(fragments, receipt):
        """Swap only the authority binding; the disclosed bytes never move."""
        from dataclasses import replace

        from deskpet.memory.recall_authority import RecallContextUseSourceSuperseded

        covered = {str(item["fragment_id"]): item for item in receipt["bindings"]}
        rebound = []
        for fragment in fragments:
            item = covered.get(fragment.fragment_id)
            if (item is None
                    or str(item["public_payload_hash"]) != fragment.public_payload_hash
                    or str(item["source_ref"]) != fragment.source_ref
                    or item["source_revision"] != fragment.source_revision):
                raise RecallContextUseSourceSuperseded()
            rebound.append(replace(fragment, recall_binding=RecallFragmentAuthorityBindingV1(
                str(receipt["decision_id"]), str(receipt["decision_hash"]),
                str(receipt["result_id"]), str(receipt["result_hash"]),
                str(item["item_id"]), str(item["item_hash"]), None, None, None,
                str(item["page_id"]), str(item["page_hash"]), None, None,
                fragment.public_payload_hash,
            )))
        return tuple(rebound)

    async def _replayed_occurrences(self, occurrences, *, run_id, granted):
        """Re-derive the exact bindings a durable grant was issued against."""
        rows = []
        for effect_id, fragments, message, plan in occurrences:
            binding = fragments[0].recall_binding
            if (binding.result_id, binding.result_hash) not in granted:
                history = await self._ledger.read_context_use_recollections(
                    sdk_run_id=run_id, effect_id=effect_id)
                receipt = next((item for item in history
                                if (str(item["result_id"]), str(item["result_hash"])) in granted),
                               None)
                if receipt is None:
                    raise ValueError("typed_use_recollection_receipt_missing")
                fragments = self._apply_recollection(fragments, receipt)
            rows.append((effect_id, fragments, message, plan))
        return tuple(rows)

    @staticmethod
    def _intents(occurrences):
        groups = {}
        for _, fragments, message, _plan in occurrences:
            for fragment in fragments:
                b = fragment.recall_binding
                key = (b.decision_id, b.decision_hash, b.result_id, b.result_hash)
                fs, ms = groups.setdefault(key, ({}, set()))
                fs[fragment.fragment_id] = fragment
                ms.add(message)
        return tuple(RecallContextUseIntentV1(tuple(fs.values()), tuple(sorted(ms))) for fs, ms in groups.values())

    async def authorize_recall_context_use(self, request):
        if request.subject != self.subject:
            raise ValueError("typed_use_subject_differs")
        # Only Memory signs the durable use receipt. Its current policy/epoch,
        # source ownership and replay rules remain the authority.
        manager = await self._memory.manager()
        from deskpet.memory.recall_authority import (
            RecallContextUseAuthorityStale, is_recall_authority_stale,
        )
        try:
            return await manager.authorize_recall_context_use(
                principal=self._memory.principal(), request=request, now=self.clock(),
            )
        except Exception as error:  # noqa: BLE001 - only the exact stale fence is renamed
            if not is_recall_authority_stale(error):
                raise
            # Reached only as a backstop. The bounded re-collect runs one step
            # earlier, at ``snapshot_intents``, because that is the only place a
            # binding may still be replaced; by the time the request reaches
            # this fence its result identity is frozen in the provider attempt
            # and no retry here could ever succeed. Anything that still trips
            # the fence changed between composing the request and authorizing it
            # — a genuine fail-closed outcome, kept payload-free and stable so
            # it stays attributable.
            _LOG.warning("recall_context_use_authority_stale run_id=%s turn_id=%s",
                         request.run_id, request.turn_id)
            self._record_fault(request.run_id, RecallContextUseAuthorityStale.code)
            raise RecallContextUseAuthorityStale() from error

    async def consumed_occurrences(self, *, db, run_id, request):
        """Return exact (effect,item) grants, never a global binding exemption."""
        view = self._stack().read_provider_context_use(run_id, request.request_id.value)
        if (view is None or view.invocation_state != "handed_off" or view.run_id != run_id
                or view.subject != self.subject or view.authority_scope_ref != self.authority_scope_ref
                or view.provider_request_id != request.request_id.value
                or view.request_fingerprint != provider_request_fingerprint(request)
                or view.handoff_attempt != view.handoff_ordinal):
            raise ValueError("typed_use_consumed_handoff_missing")
        occurrences = await self._occurrences(db, run_id=run_id, turn_id=view.turn_id, messages=request.messages)
        # Event AA: the durable grant names the result each intent was actually
        # authorized against.  A re-collected binding is therefore replayed from
        # the Host receipt that produced it, never re-derived by re-collecting
        # again — the identity that reached Memory is the one that must verify.
        granted = frozenset((item.result_id, item.result_hash) for item in view.requests)
        # An empty grant is a manifest question, not a re-collection one: leave
        # it to the manifest check below so its existing code is what fails.
        replayed = (occurrences if not granted else
                    await self._replayed_occurrences(occurrences, run_id=run_id, granted=granted))
        intents = self._intents(replayed)
        if not (len(intents) == len(view.requests) == len(view.receipts) == len(view.message_bindings)):
            raise ValueError("typed_use_consumed_manifest_differs")
        # Recreate only the request commitments from actual source facts and
        # the SDK's durable identity/time; never construct a use receipt.
        from simple_harness import ProviderContextUseAttemptV1, ProviderContextUseGrantV1
        attempt = ProviderContextUseAttemptV1(**{name: getattr(view, name) for name in (
            "authority_scope_ref", "subject", "run_id", "turn_id", "continuation_id",
            "provider_request_id", "provider_turn_ordinal", "handoff_ordinal",
            "context_snapshot_id", "context_snapshot_revision", "request_fingerprint", "requested_at",
        )}, intents=intents)
        attempt.validate_provider_request(RunId(run_id), request)
        grant = ProviderContextUseGrantV1(attempt, view.receipts)
        if (attempt.provider_attempt_id != view.provider_attempt_id
                or attempt.intent_hash != view.intent_hash or grant.grant_hash != view.grant_hash):
            raise ValueError("typed_use_consumed_grant_hash_differs")
        for intent, actual, receipt, bindings in zip(intents, view.requests, view.receipts, view.message_bindings, strict=True):
            expected = intent.request(attempt)
            if (expected != actual or bindings != intent.message_bindings
                    or receipt.request_hash != actual.request_hash
                    or receipt.item_bindings != actual.item_bindings
                    or receipt.snapshot_manifest_hash != actual.snapshot_manifest_hash):
                raise ValueError("typed_use_consumed_binding_differs")
        # The consumed set is keyed by what the *model* was shown, so it is
        # taken from the original occurrences: a re-collected binding renames
        # the item inside Memory, it does not re-disclose anything.
        return frozenset((effect_id, fragment.recall_binding.item_id)
                         for effect_id, fs, _, _ in occurrences for fragment in fs)

    async def record_terminal(self, run_id, request, attempt):
        # The SDK has already checkpointed the real successful response. A typed
        # mandatory-action rejection may now safely enter bounded same-Run repair.
        route = await self._ledger.latest_route_decision_for_run(run_id.value)
        if route is None or route.get("origin") == "no_recall":
            if attempt.intents:
                raise ValueError("typed_use_nonempty_without_route")
            await self._terminal_sink.record_no_recall(run_id=run_id,
                provider_turn_ordinal=attempt.provider_turn_ordinal,
                request_fingerprint=provider_request_fingerprint(request))

    def verify_terminal(self, run_id, request_id, checkpoint, *, verified_use):
        if checkpoint["route_state"] != "unrouted":
            return  # The SDK's actual context_route/barrier remains authoritative.
        # The coordinator has just run the SDK's original durable terminal
        # verifier. Reuse its public result; Runtime.start recovery precedes the
        # Host global ready-stack publication, so querying that stack races boot.
        from simple_harness import ProviderContextUseViewV1
        view = verified_use
        if (type(view) is not ProviderContextUseViewV1
                or view.run_id != run_id.value or view.provider_request_id != request_id.value
                or view.authority_scope_ref != self.authority_scope_ref or view.subject != self.subject
                or view.invocation_state != "succeeded" or view.requests or view.receipts):
            raise ValueError("typed_use_terminal_route_missing")
        # Read the existing Host sink fact; no await/new decision in this SDK
        # synchronous terminal check and no private SDK storage access.
        with sqlite3.connect(f"file:{self._path}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM context_route_decisions "
                "WHERE sdk_run_id=? AND provider_turn_ordinal=? AND origin='no_recall'",
                (run_id.value, view.provider_turn_ordinal)).fetchone()
        if row is None:
            raise ValueError("typed_use_terminal_host_sink_missing")
        import uuid
        from simple_harness.execution.context_authority import ContextRouteReceipt
        from simple_harness import TaskScopeRoute
        marker = f"no-recall:{run_id.value}:{view.provider_turn_ordinal}"
        expected = ContextRouteReceipt(
            receipt_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"simple-harness:{marker}")),
            run_id=run_id.value, raw_call_id=marker, effect_id=marker,
            route=TaskScopeRoute.DIRECT_STANDALONE, task_scope_id=None, binding_set_revision=None,
        )
        payload = dict(idempotency_key=marker, origin="no_recall",
                       provider_turn_ordinal=view.provider_turn_ordinal,
                       receipt=expected.to_json(), request_fingerprint=view.request_fingerprint)
        if (row["request_fingerprint"] != view.request_fingerprint or row["idempotency_key"] != marker
                or row["receipt_hash"] != expected.receipt_hash
                or json.loads(row["receipt_json"]) != expected.to_json()
                or row["decision_hash"] != canonical_sha256(payload)):
            raise ValueError("typed_use_terminal_host_sink_differs")
