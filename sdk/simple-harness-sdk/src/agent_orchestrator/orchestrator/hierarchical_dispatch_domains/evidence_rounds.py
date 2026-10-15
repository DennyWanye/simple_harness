# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Evidence rounds: re-reading recorded propositions and unknown preconditions, publish steps."""

from __future__ import annotations

import json
from collections.abc import Collection
from typing import TYPE_CHECKING, Any

from ...contracts.evidence_state import truth_change
from ...contracts.htn import MissionRef, TaskForm
from ...contracts.semantic_base import VersionedRef, content_hash_of
from ...graph.task_network import TaskNetworkSnapshot
from ...planning.htn.evidence_round import (
    EvidenceAsk,
    EvidenceRoundResult,
    asks_for_requests,
    pending_asks,
    run_round,
)
from ...planning.htn.observation_pipeline import observe_predicate, record_observation
from ...planning.htn.refinement import evidence_requests, unknown_predicates
from ..action_commits import parse_action_criterion
from ..plan_commits import PLAN_REVISION_COMMITTED
from .core import HierarchicalDispatchCore
from .support import append_hierarchical_event


class EvidenceRoundMixin(HierarchicalDispatchCore):
    """Evidence rounds: re-reading recorded propositions and unknown preconditions, publish steps."""

    __slots__ = ()

    if TYPE_CHECKING:

        def network(self, mission_id: str) -> TaskNetworkSnapshot: ...

    def plan_revision_committed_at(self, mission_id: str, plan_revision: int) -> int | None:
        """When this plan revision was committed, in observation milliseconds.

        ``None`` when no such revision has been committed — third-round review P2-7.
        It used to answer ``0``, which the evidence cap reads as "look at everything
        since the beginning of time": the degradation back to a Mission-lifetime cap
        was silent, and silent is the one thing a cap must not be.  The caller decides
        what to do with "there is no such revision"; this method only says so.

        Read with a typed query rather than by scanning every event of the Mission.
        This runs once per evidence round per Mission, so the old ``list_events``
        sweep was an O(events) read on a hot path that lengthens with the run.
        """

        rows = self.store.connection.execute(
            "SELECT created_at, payload_json FROM events WHERE mission_id = ? AND type = ?"
            " ORDER BY seq",
            (str(mission_id), PLAN_REVISION_COMMITTED),
        ).fetchall()
        stamps = [
            int(float(row[0]) * 1000)
            for row in rows
            if int((json.loads(row[1]) or {}).get("plan_revision", -1)) == int(plan_revision)
        ]
        return max(stamps) if stamps else None

    def propositions_looked_at(self, mission_id: str, *, plan_revision: int) -> frozenset[str]:
        """The propositions this Mission has already read **under this revision**.

        An observation does not always settle the atom that motivated it — a
        non-authoritative negative on an OPEN predicate is still UNKNOWN (§6.6) — so
        ``pending_asks`` keeps offering it, and the first real-model evidence round
        recorded the same proposition several hundred times in a few seconds until
        ``EvidenceEntry`` refused the snapshot.  A second identical read of an
        unchanged world produces the same answer at the cost of one more row, so
        within one revision each proposition is read once.

        Review P1-4: part 2c capped it over the Mission's whole **lifetime**, which
        made evidence unrefreshable — a precondition repaired while the plan ran (the
        smoke's own ``code.test-is-failing``) was never looked at again, and
        ``issue_start_witnesses``' promise of I19's "recompute, do not reuse the old
        TRUE" was recomputing an expression over a frozen snapshot.  The rule, written
        down: **committing a plan revision re-opens the look.**  A revision is the
        moment the world has demonstrably changed — work retired, a method adopted, a
        duty opened — and it is bounded, because a revision costs a Planner round of
        its own.  So an observation recorded *before* the current revision was
        committed no longer counts as having looked.
        """

        since = self.plan_revision_committed_at(mission_id, plan_revision)
        if since is None:
            # Review P2-7: a revision this Mission never committed is no barrier, and
            # saying so out loud is the difference between "no barrier" and a silent
            # fall back to the Mission-lifetime cap this rule replaced.  Every read
            # counts, which is the conservative answer: nothing is looked at twice on
            # the strength of a revision that does not exist.
            return frozenset(
                str(record.proposition_key)
                for record in self.semantics().list_observations(mission_id)
            )
        return frozenset(
            str(record.proposition_key)
            for record in self.semantics().list_observations(mission_id)
            if int(record.recorded_at_ms) >= since
        )

    def run_evidence_round(self, mission_id: str, *, now_ms: int | None = None) -> Any:
        """One evidence round: re-read what the world may have changed, then look at the
        UNKNOWN preconditions (阶段 D)."""

        reread, read = self._reread_recorded(mission_id, now_ms=now_ms)
        # a proposition read again just now is not asked a second time in the same round
        looked = self._unknown_precondition_round(mission_id, now_ms=now_ms, skip=read)
        return EvidenceRoundResult(
            outcomes=(*reread, *looked.outcomes), unobservable=looked.unobservable
        )

    def _reread_recorded(
        self, mission_id: str, *, now_ms: int | None
    ) -> tuple[tuple[Any, ...], frozenset[str]]:
        """Read the recorded propositions again when the world has moved.

        A desktop Mission's world is its accepted artifacts and its reference material: a
        step is accepted, a source is replaced, and a proposition recorded earlier may no
        longer hold — while the start gate only reads what is recorded.  So once per
        *world mark* (the set of acceptances plus the state of the sources table) every
        recorded proposition an installed observer can read is asked again, from the
        question stored on its row; only a changed truth is written (which raises the
        scope epoch, see ``insert_observation``).  One ``EvidenceReread`` event per mark
        says what was read and what changed.  An unchanged world is not read again: a
        seed domain's observer may be expensive, and writing an unchanged reading would
        stale every planning round in flight."""

        index = getattr(self._world(), "observer_index", None)
        if index is None:
            return (), frozenset()
        semantics = self.semantics()
        mark = content_hash_of(
            {
                "acceptances": sorted(
                    str(item.acceptance_id) for item in semantics.list_acceptances(mission_id)
                ),
                "sources": [
                    list(row)
                    for row in self.store.connection.execute(
                        "SELECT path, revision, version_hash, superseded_by, revoked FROM sources"
                        " WHERE mission_id=? ORDER BY path, revision, version_hash",
                        (mission_id,),
                    )
                ],
            }
        )
        done = f"EvidenceReread:{mission_id}:{mark}"
        if (
            self.store.connection.execute(
                "SELECT 1 FROM events WHERE idempotency_key=?", (done,)
            ).fetchone()
            is not None
        ):
            return (), frozenset()
        questions = semantics.observation_questions(mission_id)
        moment = int(self.store.now * 1000) if now_ms is None else int(now_ms)
        written: list[Any] = []
        read: list[str] = []
        for question in questions:
            reference = VersionedRef.from_json(question["predicate_ref"])
            if index.observer_for(str(reference.id)) is None:
                continue
            outcome = observe_predicate(index, reference, question["arguments"], now_ms=moment)
            read.append(question["proposition_key"])
            if outcome.record is None:
                continue  # could not look: nothing is written, the recorded reading stands
            before, after = truth_change(
                semantics.list_observations(
                    mission_id, proposition_key=question["proposition_key"]
                ),
                outcome.record,
            )
            if after is before:
                continue
            written.append(
                record_observation(semantics, mission_id, outcome, scope_id=question["scope_id"])
            )
        if not questions:
            return (
                (),
                frozenset(),
            )  # nothing was ever recorded: nothing to read again, and nothing to say
        append_hierarchical_event(
            self.store,
            "EvidenceReread",
            mission_id,
            key=f"{mission_id}:{mark}",
            payload={
                "mark": mark,
                "read": read,
                "changed": [str(item.record.proposition_key) for item in written],
            },
        )
        return tuple(written), frozenset(read)

    def _unknown_precondition_round(
        self, mission_id: str, *, now_ms: int | None = None, skip: Collection[str] = ()
    ) -> Any:
        """Look at the UNKNOWN preconditions of every still-open goal, once.

        P2.3c part 2c.  Part 2b built the round (``planning.htn.evidence_round``) and
        left it for a deployment to call by hand, so in the real loop an UNKNOWN
        precondition stayed UNKNOWN forever: the Planner was handed a package whose
        methods all read ``NEEDS_EVIDENCE`` and no evidence was ever gathered.  ADR-07
        is "look, do not guess", and nothing was looking.

        What it does **not** do is dispatch an agent.  Every read goes through the
        deployment's :class:`~..planning.htn.observation_pipeline.ObserverIndex`,
        which is the one path that enforces §6.6 C28 (only a listed observer, only a
        read-only type) on the way in; an observer that cannot answer produces
        ``OBSERVER_UNAVAILABLE`` and **no record**, so a proposition never becomes
        FALSE because nobody looked.

        Which propositions are asked is the catalogue's decision, not this method's:
        an atom is asked only when ``evidence_requests`` found a registered read-only
        task type that declares it observes that predicate.  A deployment that
        installed no observer index gets an empty round rather than an error — it has
        simply not wired the evidence lane, which the readiness reports already say.
        """

        world = self._world()
        index = getattr(world, "observer_index", None)
        if index is None:
            return EvidenceRoundResult()
        network = self.network(mission_id)
        snapshot = world.snapshot()
        looked_at = self.propositions_looked_at(
            mission_id, plan_revision=int(network.plan_revision)
        )
        moment = int(self.store.now * 1000) if now_ms is None else int(now_ms)
        asks: dict[str, EvidenceAsk] = {}
        for spec in sorted(network.occurrences, key=lambda item: str(item.occurrence_id)):
            if spec.form is not TaskForm.COMPOUND:
                continue
            if network.adopted_instance_for(spec.occurrence_id) is not None:
                continue
            goal = network.binding_for_occurrence(spec.occurrence_id)
            parameters = dict(goal.typed_parameters)
            for reference in world.registry.method_refs():
                if not world.registry.retrievable(reference, mission_id=MissionRef(mission_id)):
                    continue
                contract = world.registry.definition(reference)
                if contract is None:
                    continue
                if str(contract.goal_type_ref.id) != str(goal.goal_signature.signature_id):
                    continue
                conditions = contract.applicable_when
                unknowns = unknown_predicates(
                    conditions,
                    parameters=parameters,
                    predicates=world.predicates,
                    snapshot=snapshot,
                    now_ms=moment,
                )
                if not unknowns:
                    continue
                requests = evidence_requests(
                    unknowns,
                    for_occurrence=spec.occurrence_id,
                    obligation_id=spec.obligation_id,
                    catalog=world.catalog,
                    semantic_scope=goal.semantic_scope,
                    contract_revision=int(goal.contract_revision),
                )
                candidates = pending_asks(
                    conditions,
                    parameters=parameters,
                    registry=world.predicates,
                    snapshot=snapshot,
                    now_ms=moment,
                )
                for ask in asks_for_requests(requests, candidates):
                    if ask.proposition_key in looked_at or ask.proposition_key in skip:
                        continue
                    asks.setdefault(ask.proposition_key, ask)
        if not asks:
            return EvidenceRoundResult()
        return run_round(
            index,
            self.semantics(),
            mission_id,
            tuple(asks[key] for key in sorted(asks)),
            now_ms=moment,
        )

    def _publish_source_steps(
        self, mission_id: str, method: Any, share: Collection[str] | None = None
    ) -> list[str]:
        """Publish targets whose ``file:`` criterion is not linked to exactly one step.

        The Host puts ``file:X`` in front of every ``action:file_publish.publish:X``
        (``c-user-<n>`` names the Mission's n-th criterion).

        ``share``（片 B）：这份做法的目标负责的要求编号。给了就只查其中的 ``file:`` 要求——
        要发布的文件归别的目标或步骤时，这份做法既不该、也不能（覆盖检查会判越界）链接它。
        """

        mission = self.store.get_mission(mission_id)
        if mission is None:
            return []
        # 按需导入：测试替身（agent_orchestrator.deployment.root）
        from ...deployment.root import current_statements

        criteria = [str(item).strip() for item in current_statements(self.store, mission)]
        steps: dict[str, set[str]] = {}
        for link in getattr(method.composition, "criterion_links", ()) or ():
            if link.child_step is not None:
                steps.setdefault(str(link.parent_criterion_id), set()).add(str(link.child_step))
        unclear = []
        for criterion in criteria:
            parsed = parse_action_criterion(criterion)
            if parsed is None:
                continue
            source = f"file:{parsed[2]}"
            if source not in criteria:
                continue
            source_id = f"c-user-{criteria.index(source) + 1}"
            if share is not None and source_id not in share:
                continue
            linked = sorted(steps.get(source_id, ()))
            if len(linked) != 1:
                unclear.append(
                    f"{parsed[2]} 链接到 {len(linked)} 个步骤"
                    + (f"（{'、'.join(linked)}）" if linked else "")
                )
        return unclear
