# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""START licences: issuing and indexing input and precondition witnesses."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from ...artifacts.taskgraph_inputs import decode_frozen_manifest
from ...contracts import ContractError
from ...contracts.evidence_state import (
    Availability,
    TruthValue,
    Validity,
    ValidityWitness,
    WitnessDecision,
    WitnessPurpose,
)
from ...contracts.htn import TaskForm, condition_digest
from ...contracts.semantic_base import TypedRef, TypedRefKind, content_hash_of
from ...graph.task_network import TaskNetworkSnapshot
from ...knowledge.predicates import proposition_key
from ...knowledge.validity import acceptance_subject, condition_subject
from ...planning.htn.applicability import (
    all_truth,
    authorization_gate,
    evaluate_condition,
    ground_value,
)
from ...planning.htn.registry import iter_predicates
from ...storage.store import StoreError
from .core import HierarchicalDispatchCore
from .support import (
    WITNESS_KEY_TAKEN,
    PlanningWorld,
    _is_continuation,
    _same_conclusion,
    _typed,
    append_hierarchical_event,
)

if TYPE_CHECKING:
    from ...artifacts.input_bindings import DeliveryBinding, ResolvedInputBinding


class WitnessIssueMixin(HierarchicalDispatchCore):
    """START licences: issuing and indexing input and precondition witnesses."""

    __slots__ = ()

    if TYPE_CHECKING:

        def _complete_goals(
            self, mission_id: str, network: TaskNetworkSnapshot
        ) -> frozenset[Any]: ...
        def _recorded_outputs(
            self, mission_id: str, network: TaskNetworkSnapshot
        ) -> Sequence[Any]: ...
        def goal_port_outputs(
            self,
            mission_id: str,
            network: TaskNetworkSnapshot,
            outputs: Sequence[Any],
            *,
            complete: Collection[Any],
        ) -> tuple[Any, ...]: ...
        def network(self, mission_id: str) -> TaskNetworkSnapshot: ...

    def witnesses(
        self, mission_id: str, network: TaskNetworkSnapshot
    ) -> dict[str, ValidityWitness]:
        """The START-precondition witnesses, keyed by condition digest.

        The link is the method instance's own
        :class:`~..contracts.htn.PreconditionWitnessRecord`, which is the only place
        that says *which condition* a stored :class:`ValidityWitness` was taken for.
        Guessing that link from the witness alone would let one witness license a
        precondition it was never computed for.
        """

        by_id = {
            str(witness.witness_id): witness
            for witness in self.semantics().list_validity_witnesses(mission_id)
        }
        found: dict[str, ValidityWitness] = {}
        for draft in network.method_instances:
            for record in draft.precondition_witnesses:
                if record.witness_ref is None:
                    continue
                witness = by_id.get(str(record.witness_ref.id))
                if witness is not None:
                    found[record.condition_digest] = witness
        return found

    def input_witness_index(self, mission_id: str) -> dict[str, dict[str, ValidityWitness]]:
        """consumer task id → acceptance id → the START witness issued for it.

        The link from a witness to the acceptance it covers is the witness's own
        ``support_refs``: a witness that does not name what it was taken over could
        be made to license anything, which is exactly what §11.5 says a witness is
        not.  Witnesses for other consumers land under *their* task id rather than
        being filtered later, because the resolver's consumer check would only be
        able to report the last one it happened to see.

        The whole index is built in one pass for the same reason ``read`` fetches
        the acceptance projection once: the readiness gates ask this question for
        every occurrence in the plan, and asking per occurrence turned one read of
        the witness library into a quadratic number of store round-trips.
        """

        index: dict[str, dict[str, ValidityWitness]] = {}
        for witness in self.semantics().list_validity_witnesses(mission_id):
            if witness.purpose is not self.INPUT_WITNESS_PURPOSE:
                continue
            if witness.consumer_ref.kind is not TypedRefKind.TASK:
                continue
            for reference in witness.support_refs:
                if reference.kind is TypedRefKind.ACCEPTANCE:
                    index.setdefault(str(witness.consumer_ref.id), {})[str(reference.id)] = witness
        return index

    def input_witnesses(self, mission_id: str, task_id: str) -> dict[str, ValidityWitness]:
        """acceptance id → the START witness issued to ``task_id`` for it."""

        return self.input_witness_index(mission_id).get(str(task_id), {})

    def issue_input_witnesses(
        self,
        mission_id: str,
        network: TaskNetworkSnapshot,
        *,
        now_ms: int,
        scope_id: str = "mission",
    ) -> tuple[ValidityWitness, ...]:
        """Re-read each accepted output a consumer depends on and record the licence.

        This is the "recompute rather than reuse the old TRUE" half of I19 on the
        DATA lane: for every declared edge whose producer has a recorded accepted
        output, the acceptance is read *now* and a START witness is written for the
        consumer, carrying the acceptance's own support revision.  An acceptance that
        is no longer ``CURRENT`` produces an ``UNUSABLE`` witness rather than none, so
        the refusal downstream says "this was revoked" and not "nobody looked".
        """

        semantics = self.semantics()
        epoch = semantics.epoch(mission_id, scope_id)
        issued: list[ValidityWitness] = []
        recorded = tuple(self._recorded_outputs(mission_id, network))
        # 片 B：接在中间目标端口上的步骤，见证发给它实际读到的那份验收（收尾步骤的）。
        delivered = self.goal_port_outputs(
            mission_id, network, recorded, complete=self._complete_goals(mission_id, network)
        )
        covered: set[tuple[str, str]] = set()
        for output in (*recorded, *delivered):
            consumers = {
                str(network.binding_for_occurrence(item.consumer_occurrence).task_id)
                for item in network.data_requirements
                if item.producer_occurrence == output.producer_occurrence
                and item.output_port == output.output_port
            }
            # 接力文件的真实产出者那次验收也要给这个下游一份见证——与清单构造
            # 同一处枚举（``delivery_sources`` 读的就是这些验收），一个验收一份，不按文件数发。
            sources = [
                (str(output.acceptance_id), int(output.support_revision)),
                *self._carried_acceptances(mission_id, str(output.acceptance_id)),
            ]
            for consumer in sorted(consumers):
                for acceptance_id, support_revision in sources:
                    if (consumer, acceptance_id) in covered:
                        continue
                    covered.add((consumer, acceptance_id))
                    stored = self._issue_acceptance_witness(
                        mission_id,
                        consumer,
                        acceptance_id,
                        support_revision,
                        epoch=epoch,
                        scope_id=scope_id,
                        now_ms=now_ms,
                    )
                    if stored is not None:
                        issued.append(stored)
        return tuple(issued)

    def _issue_acceptance_witness(
        self,
        mission_id: str,
        consumer: str,
        acceptance_id: str,
        support_revision: int,
        *,
        epoch: int,
        scope_id: str,
        now_ms: int,
    ) -> ValidityWitness | None:
        """One START licence for ``consumer`` over ``acceptance_id``, read now (I19)."""

        acceptance = self.semantics().get_acceptance(acceptance_id)
        usable = acceptance.validity is Validity.CURRENT
        witness = ValidityWitness(
            witness_id="wit-in-"
            + content_hash_of({"c": consumer, "a": acceptance_id, "e": epoch})[:28],
            consumer_ref=_typed(TypedRefKind.TASK, consumer),
            purpose=self.INPUT_WITNESS_PURPOSE,
            truth=TruthValue.TRUE if usable else TruthValue.FALSE,
            freshness=Validity.CURRENT if usable else acceptance.validity,
            availability=Availability.READABLE,
            # ``BLOCKED``, not ``UNAVAILABLE``: the acceptance was read and found
            # revoked or superseded, which is a different fact from "the
            # library could not be read".
            decision=WitnessDecision.USABLE if usable else WitnessDecision.BLOCKED,
            scope_id=scope_id,
            scope_epoch=epoch,
            support_revision=int(support_revision),
            as_of_ms=int(now_ms),
            support_refs=(_typed(TypedRefKind.ACCEPTANCE, acceptance_id),),
        )
        subject = acceptance_subject(acceptance_id)
        stored = self._record_witness(mission_id, witness, subject=subject)
        if stored is None:
            # Since migration 18 the subject is part of the key, so this is no
            # longer the DATA lane colliding with the precondition lane: it is
            # this consumer already holding a *different* verdict about this
            # very acceptance at this very reading of the world.  Skipping is
            # the honest answer — the consumer then reports WAITING_DATA,
            # which is true — and the anomaly is written down.
            self._witness_key_taken(mission_id, witness, subject=subject)
            return None
        return stored

    def _carried_acceptances(self, mission_id: str, acceptance_id: str) -> list[tuple[str, int]]:
        """The acceptances a continuation producer's accepted Attempt was frozen with
        (acceptance id, support revision) — the real producers of what it hands on."""

        semantics = self.semantics()
        try:
            acceptance = semantics.get_acceptance(acceptance_id)
        except StoreError:
            return []
        if acceptance.mission_id != mission_id or not _is_continuation(
            semantics.task_semantics_of(mission_id, str(acceptance.task_id))
        ):
            return []
        try:
            frozen = decode_frozen_manifest(
                semantics.get_input_manifest(acceptance.input_manifest_hash)
            )
        except (StoreError, ContractError):
            return []  # the resolution names this source as unavailable; nothing to license
        found: dict[str, int] = {}
        for item in cast(
            "tuple[ResolvedInputBinding | DeliveryBinding, ...]",
            (*frozen.bindings, *frozen.deliveries),
        ):
            found.setdefault(item.acceptance_id, int(item.support_revision))
        return sorted(found.items())

    def _record_witness(
        self, mission_id: str, witness: ValidityWitness, *, subject: str
    ) -> ValidityWitness | None:
        """Store one witness, or answer what is already stored under its identity.

        Three outcomes, none of them an exception for the caller to interpret: the
        row is written; the very same row is already there (the same reading of the
        same world, re-issued) and comes back; or the *key* is held by a different
        licence and the answer is None.

        Since migration 18 the key carries ``subject`` — which object this licence was
        issued for — so the DATA lane and the precondition lane no longer contend for
        one row.  ``None`` now means what it always claimed to mean: two different
        conclusions about the *same* subject at the same reading of the world, which
        is a real contradiction and is reported rather than swallowed.

        Third-round review P2-1 closed the way that claim was still escapable.  Both
        production lanes derive ``witness_id`` from the key's own components, so a
        contention shows up as a **primary-key** conflict rather than an index one —
        and the old recovery re-read the row *by id* and returned it, whatever it
        said.  A second, opposite reading of the same world therefore inherited the
        first reading's ``USABLE`` licence in silence (visible the moment a revoke or
        supersede command exists: the same acceptance goes CURRENT → REVOKED and
        ``issue_input_witnesses`` hands back the old permission).  So the row that
        comes back is now *compared*: identical conclusion → the same licence,
        re-issued; different conclusion → ``None``, and the caller records
        :data:`WITNESS_KEY_TAKEN`.
        """

        try:
            semantics = self.semantics()
            semantics.insert_validity_witness(mission_id, witness, subject=subject)
            return witness
        except StoreError:
            for held in self.semantics().list_validity_witnesses(mission_id):
                if held.witness_id != witness.witness_id:
                    continue
                if _same_conclusion(held, witness):
                    return held
                return None
            return None

    def _witness_key_taken(
        self, mission_id: str, witness: ValidityWitness, *, subject: str
    ) -> None:
        """Record, once, that two conclusions were reached about the same subject.

        Before migration 18 this fired whenever the two licence lanes met, which was
        routine rather than exceptional.  With ``subject_digest`` in the key it fires
        only when the *same* consumer reaches a *different* verdict about the *same*
        subject at the same epoch and support revision — one reading of one world
        giving two answers.  That is a genuine anomaly and is written down.
        """

        append_hierarchical_event(
            self.store,
            WITNESS_KEY_TAKEN,
            mission_id,
            key=f"{mission_id}:{witness.consumer_ref.id}:{witness.scope_epoch}:"
            f"{witness.support_revision}:{subject}",
            task_id=str(witness.consumer_ref.id),
            payload={
                "consumer": str(witness.consumer_ref.id),
                "purpose": str(witness.purpose),
                "subject_digest": str(subject),
                "scope_id": witness.scope_id,
                "scope_epoch": int(witness.scope_epoch),
                "support_revision": int(witness.support_revision),
                "reason_codes": list(witness.reason_codes),
                "detail": (
                    "the validity_witnesses unique index holds one licence per "
                    "consumer per purpose per subject per scope epoch and support "
                    "revision, and this consumer already holds a different verdict "
                    "about that same subject at that same reading of the world; the "
                    "licence was not stored and the occurrence stays withheld rather "
                    "than running on a contested one"
                ),
            },
        )

    def issue_start_witnesses(
        self,
        mission_id: str,
        network: TaskNetworkSnapshot | None = None,
        *,
        now_ms: int | None = None,
        scope_id: str = "mission",
    ) -> tuple[ValidityWitness, ...]:
        """Re-read every occurrence's START preconditions and record the licence.

        P2.3c part 2c, found by the real-model smoke.  ``grounding.task_binding_for``
        gives every primitive child the *parent method's* ``applicable_when`` as a
        SELECT :class:`~..contracts.htn.PreconditionRef`, and TG §9 refuses to
        dispatch an occurrence whose START precondition has no ``purpose=START``
        :class:`ValidityWitness`.  Nothing issued one: ``issue_input_witnesses``
        covers the DATA lane only and ``grounding`` deliberately leaves
        ``witness_ref`` empty, so under a gated method every leaf sat in
        ``WAITING_EVIDENCE`` / ``witness_missing`` for ever — the plan committed, the
        duty was admitted, and no work was ever dispatched.

        It is the same shape as the DATA lane and for the same reason (I19): the
        conditions are **evaluated again now**, against the deployment's current
        evidence snapshot, and the verdict is written down with the support revision
        it was taken at.  A condition that no longer holds produces a ``BLOCKED``
        witness rather than none, so the refusal downstream says "the world moved"
        and not "nobody looked"; only an evaluation that
        :func:`~..planning.htn.applicability.authorization_gate` admits — TRUE with
        every leaf backed by a real observation or an authoritative denial (§6.6
        rule 2) — is ``USABLE``.

        One witness covers **all** of a consumer's START preconditions, and names
        each of them in its reason codes.  Since migration 18 the row is filed under
        ``conditions:<digest of that set>``, so "this consumer may start, now, on this
        reading of the world, over *these* conditions" is one row by construction —
        and it sits beside, rather than instead of, the DATA lane's licence for the
        same consumer.  ALL semantics make the combination honest — one UNKNOWN
        member is enough to withhold the whole licence (§6.6 rule 1).
        """

        try:
            world = self._world()
        except ContractError:
            # No PlanningWorld: the predicates and the evidence snapshot a condition is
            # judged against do not exist here, so no licence may be issued.  Fail
            # closed and say nothing new — the occurrences stay withheld with
            # ``witness_missing``, and the deployment defect is already reported by
            # ``HierarchicalAssemblyMissing`` / ``require_planning_world``.
            return ()
        semantics = self.semantics()
        snapshot = world.snapshot()
        network = self.network(mission_id) if network is None else network
        epoch = semantics.epoch(mission_id, scope_id)
        moment = int(self.store.now * 1000) if now_ms is None else int(now_ms)
        forms = {spec.occurrence_id: spec.form for spec in network.occurrences}
        issued: list[ValidityWitness] = []
        for draft in sorted(network.method_instances, key=lambda item: str(item.instance_id)):
            contract = world.registry.definition(draft.method_ref)
            if contract is None:
                # The method left the registry.  That is a recheck problem (§6.6 rule
                # 3), not a licence this method may invent from the frozen verdict.
                continue
            by_digest = {condition_digest(item): item for item in contract.applicable_when}
            if not by_digest:
                continue
            parameters = {binding.name: binding.value for binding in draft.grounded_parameters}
            evaluations = {
                digest: evaluate_condition(
                    by_digest[digest],
                    registry=world.predicates,
                    snapshot=snapshot,
                    parameters=parameters,
                    now_ms=moment,
                )
                for digest in sorted(by_digest)
            }
            truth = all_truth(tuple(item.truth for item in evaluations.values()))
            allowed = all(authorization_gate(item).allowed for item in evaluations.values())
            support: list[TypedRef] = []
            seen: set[tuple[str, str]] = set()
            for digest in sorted(by_digest):
                for reference in self._condition_support(
                    by_digest[digest], parameters=parameters, world=world, snapshot=snapshot
                ):
                    key = (str(reference.kind), str(reference.id))
                    if key in seen:
                        continue
                    seen.add(key)
                    support.append(reference)
            reasons = tuple(
                f"{self.CONDITION_REASON_PREFIX}{digest}" for digest in sorted(by_digest)
            )
            # The whole (sorted) condition set is what this licence covers, so it is
            # what the row is filed under.  ``condition_subject`` is the same function
            # the store re-runs over ``reason_codes`` before it writes.
            subject = condition_subject(by_digest)
            for child in sorted(draft.child_bindings, key=lambda item: str(item.occurrence_id)):
                if forms.get(child.occurrence_id) is not TaskForm.PRIMITIVE:
                    continue
                consumer = str(network.binding_for_occurrence(child.occurrence_id).task_id)
                witness = ValidityWitness(
                    witness_id="wit-pre-"
                    + content_hash_of(
                        {
                            "c": consumer,
                            "e": epoch,
                            "s": int(snapshot.support_revision),
                            "p": str(scope_id),
                            # The subject is part of the row's identity since
                            # migration 18, so it is part of the row's name too: a
                            # different condition set is a different licence, not a
                            # second verdict about the same one.
                            "j": subject,
                        }
                    )[:28],
                    consumer_ref=_typed(TypedRefKind.TASK, consumer),
                    purpose=WitnessPurpose.START,
                    truth=truth,
                    freshness=Validity.CURRENT,
                    availability=Availability.READABLE,
                    decision=(WitnessDecision.USABLE if allowed else WitnessDecision.BLOCKED),
                    scope_id=scope_id,
                    scope_epoch=epoch,
                    support_revision=int(snapshot.support_revision),
                    as_of_ms=moment,
                    support_refs=tuple(support),
                    reason_codes=reasons,
                )
                stored = self._record_witness(mission_id, witness, subject=subject)
                if stored is None:
                    self._witness_key_taken(mission_id, witness, subject=subject)
                    continue
                issued.append(stored)
        return tuple(issued)

    def _condition_support(
        self,
        condition: Any,
        *,
        parameters: Mapping[str, Any],
        world: PlanningWorld,
        snapshot: Any,
    ) -> tuple[TypedRef, ...]:
        """The observations the snapshot holds for this condition's own atoms.

        A licence that named nothing could not be audited back to what was read; a
        licence that named every observation of the Mission would say nothing about
        *this* condition.  Only the atoms of this condition are followed, and only
        as far as the snapshot already went — this method never observes.
        """

        refs: list[TypedRef] = []
        seen: set[tuple[str, str]] = set()
        for atom in iter_predicates((condition,)):
            signature = world.predicates.resolve(atom.predicate_ref)
            if signature is None:
                continue
            errors: list[str] = []
            arguments = {
                name: ground_value(item, parameters, path="precondition", errors=errors)
                for name, item in atom.arguments.items()
            }
            if errors or not world.predicates.check_arguments(signature, arguments).ok:
                continue
            entry = snapshot.lookup(proposition_key(signature, arguments))
            if entry is None:
                continue
            for reference in entry.observation_refs:
                key = (str(reference.kind), str(reference.id))
                if key in seen:
                    continue
                seen.add(key)
                refs.append(reference)
        return tuple(refs)

    def start_witness_index(self, mission_id: str) -> dict[str, dict[str, ValidityWitness]]:
        """consumer task id → condition digest → the freshest START witness for it.

        "Freshest" is the support revision the witness was taken at, then its clock:
        re-issuing after new evidence must not leave the earlier verdict in play, and
        a witness is keyed by what it concluded, so both rows exist in the library.
        """

        index: dict[str, dict[str, ValidityWitness]] = {}
        for witness in self.semantics().list_validity_witnesses(mission_id):
            if witness.purpose is not WitnessPurpose.START:
                continue
            if witness.consumer_ref.kind is not TypedRefKind.TASK:
                continue
            digests = [
                code[len(self.CONDITION_REASON_PREFIX) :]
                for code in witness.reason_codes
                if code.startswith(self.CONDITION_REASON_PREFIX)
            ]
            for digest in digests:
                held = index.setdefault(str(witness.consumer_ref.id), {}).get(digest)
                if held is not None and (held.support_revision, held.as_of_ms) >= (
                    witness.support_revision,
                    witness.as_of_ms,
                ):
                    continue
                index.setdefault(str(witness.consumer_ref.id), {})[digest] = witness
        return index

    def start_witnesses(self, mission_id: str, task_id: str) -> dict[str, ValidityWitness]:
        """condition digest → the START witness issued to ``task_id`` for it."""

        return self.start_witness_index(mission_id).get(str(task_id), {})
