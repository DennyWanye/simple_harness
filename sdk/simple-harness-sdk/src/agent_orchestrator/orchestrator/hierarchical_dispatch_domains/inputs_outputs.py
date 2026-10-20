# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Data contracts: resolved inputs, accepted outputs, delivery sources and write conflicts."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from dataclasses import replace as _replace
from typing import TYPE_CHECKING, Any, cast

from ...artifacts.input_bindings import (
    AcceptedOutput,
    AcceptedOutputsIndex,
    DeliveredFile,
    DeliverySources,
    ExplicitPortOrder,
    InputManifest,
    ResolutionProblemKind,
    ResolutionResult,
    TargetRules,
    resolve_deliveries,
)
from ...artifacts.taskgraph_inputs import decode_frozen_manifest
from ...artifacts.versioning import (
    ArtifactConflict,
    UpstreamInput,
    manifest_upstream_inputs,
    resolve_input_manifest,
)
from ...contracts import ContractError, TaskStatus
from ...contracts.evidence_state import ValidityWitness
from ...contracts.htn import (
    ORDERED_CARDINALITIES,
    MethodContract,
    OccurrenceId,
    OccurrenceSpec,
    OutputValue,
    PortCardinality,
    PortOrdering,
    TaskForm,
    TaskRef,
)
from ...graph.eligibility import OccurrenceOutcome
from ...graph.projection_validation import ordering_of
from ...graph.task_network import TaskNetworkSnapshot
from ...planning.htn.compiler import _derive_requirement_id
from ...planning.htn.registry import iter_values
from ...storage.operation_completion_store import OperationCompletionStore
from ...storage.store import StoreError
from ..accepted_outputs import (
    accepted_output_from_json,
    carried_criteria_for,
    output_ports_in_revision,
)
from ..completion_status import read_occurrence_completion
from ..occurrence_tasks import read_only_leaf
from .core import HierarchicalDispatchCore
from .support import (
    WriteConflictPending,
    _is_continuation,
    append_hierarchical_event,
    missing_bindings,
)


class InputOutputMixin(HierarchicalDispatchCore):
    """Data contracts: resolved inputs, accepted outputs, delivery sources and write conflicts."""

    __slots__ = ()

    if TYPE_CHECKING:

        def input_witnesses(self, mission_id: str, task_id: str) -> dict[str, ValidityWitness]: ...
        def network(self, mission_id: str) -> TaskNetworkSnapshot: ...
        def scope_epochs(self, mission_id: str) -> dict[str, int]: ...
        def target_rules_for(self, task_id: str) -> TargetRules: ...

    def input_result(
        self,
        mission_id: str,
        network: TaskNetworkSnapshot,
        spec: OccurrenceSpec,
        *,
        accepted: AcceptedOutputsIndex | None = None,
        witnesses: Mapping[str, ValidityWitness] | None = None,
        now_ms: int | None = None,
    ) -> ResolutionResult | None:
        """The declared DATA contract of one occurrence, resolved — or None.

        None means "this occurrence declares no data requirement", which is a
        different answer from "its inputs did not resolve"; the DATA gate reads the
        difference, so the two are not collapsed here.
        """

        requirements = [
            item
            for item in network.data_requirements
            if item.consumer_occurrence == spec.occurrence_id
        ]
        binding = network.binding_for_occurrence(spec.occurrence_id)
        # NEXT-TG-1.0 §5.2: "no requirement" is a legitimate no-input only when the
        # contract declares no required input port either.  A required port the plan
        # drew no edge for goes through the resolver, which names it
        # UNBOUND_REQUIRED_PORT, instead of becoming an empty success below.
        if not requirements and not any(port.required for port in binding.input_ports):
            return None
        # P2.3c part 2b: the witnesses this lane needs are keyed by **acceptance
        # id** and issued to *this* consumer (I19), which is a different question
        # from ``witnesses()``'s "which condition was this witness taken for".
        # Passing the precondition map here meant no key ever matched and every
        # DATA consumer refused with ``WITNESS_MISSING`` whatever the deployment
        # had recorded.
        licences = (
            self.input_witnesses(mission_id, str(spec.task_id)) if witnesses is None else witnesses
        )
        policy = self.resolution_policy_for(
            mission_id, now_ms=now_ms, consumer=spec.occurrence_id, network=network
        )
        result = resolve_input_manifest(
            binding,
            network,
            accepted if accepted is not None else self.accepted_outputs(mission_id, network),
            consumer_occurrence=spec.occurrence_id,
            witnesses=licences,
            policy=policy,
        )
        if result.problems or result.manifest is None or not result.manifest.is_frozen:
            return result
        # 端口解析完、冻结了，再把这些上游交出的其余文件（交付绑定）按同一份
        # 规则算进清单——每份都带真实产出者那次验收的见证；版本证明不了的报冲突，不替规划器选。
        return resolve_deliveries(
            result.manifest,
            self.delivery_sources(mission_id, result.manifest),
            target_rules=self.target_rules_for(str(spec.task_id)),
            witnesses=licences,
            policy=policy,
        )

    def resolution_policy_for(
        self,
        mission_id: str,
        *,
        now_ms: int | None = None,
        consumer: Any = None,
        network: Any = None,
    ) -> Any:
        """This deployment's resolution policy with the Mission's live epochs in it.

        I19 is a *comparison*, and the policy this class is constructed with does not
        know the scope epochs — so a policy left at its defaults would refuse every
        binding with ``WITNESS_EPOCH_UNKNOWN``.  The epochs are read here rather than
        cached on the dataclass, for the same reason nothing else on this class is
        cached: a bumped epoch has to be visible to the very next read.

        The installed policy is always bound to the actual current epochs and
        evaluation clock.
        """

        policy = self.resolution_policy
        explicit = tuple(policy.explicit_orders)
        if consumer is not None:
            # 2026-10-09 第 4 条：这一步集合端口上各上游的先后，就是规划器在做法里数组写的先后
            explicit = (*explicit, *self._explicit_port_orders(mission_id, consumer, network))
        return _replace(
            policy,
            explicit_orders=explicit,
            scope_epochs=self.scope_epochs(mission_id),
            now_ms=int(self.store.now * 1000) if now_ms is None else int(now_ms),
        )

    def _explicit_port_orders(
        self, mission_id: str, consumer: Any, network: Any = None
    ) -> tuple[Any, ...]:
        """The declared order of each EXPLICIT set input port of ``consumer``: the order the
        adopted method lists the upstream outputs in that port's argument array (TaskGraph §5.5
        "按显式 order 列表").  Read from the plan, decided by nobody here."""

        network = self.network(mission_id) if network is None else network
        binding = network.binding_for_occurrence(consumer)
        ports = [
            port
            for port in binding.input_ports
            if port.cardinality in ORDERED_CARDINALITIES and port.ordering is PortOrdering.EXPLICIT
        ]
        if not ports:
            return ()
        semantics = self.semantics()
        for instance in network.method_instances:
            if not network.is_adopted(instance.instance_id):
                continue
            mine = next(
                (child for child in instance.child_bindings if child.occurrence_id == consumer),
                None,
            )
            if mine is None:
                continue
            entry = semantics.get_method(
                str(instance.method_ref.method_id), int(instance.method_ref.version)
            )
            # get_method 返回 StoredMethod，总带 contract；getattr 的回落分支走不到（阶段 8 清理）
            contract = cast(MethodContract, getattr(entry, "contract", entry))
            step = next((item for item in contract.steps if item.local_id == mine.slot_key), None)
            if step is None:
                continue
            slot_to_occurrence = {
                child.slot_key: child.occurrence_id for child in instance.child_bindings
            }
            live = {item.requirement_id for item in network.data_requirements}
            orders = []
            for port in ports:
                argument = step.arguments.get(port.port_key)
                ids: list[str] = []
                for node in iter_values(argument) if argument is not None else ():
                    if (
                        isinstance(node, OutputValue)
                        and node.step != step.local_id
                        and node.step in slot_to_occurrence
                    ):
                        derived = _derive_requirement_id(
                            slot_to_occurrence[node.step], node.port, consumer, port.port_key
                        )
                        if derived in live:  # an edge a repair replaced is no longer named
                            ids.append(derived)
                # 规划器后来用修复操作改接/加接的边不在做法正文里：排在做法列出的之后，按网络
                # 里的（固定）顺序；这是写明的约定，不是替规划器挑谁先谁后。
                later = [
                    item.requirement_id
                    for item in network.data_requirements
                    if item.consumer_occurrence == consumer
                    and item.input_port == port.port_key
                    and item.requirement_id not in ids
                ]
                orders.append(
                    ExplicitPortOrder(port.port_key, tuple(dict.fromkeys([*ids, *later])))
                )
            return tuple(orders)
        return ()

    def resolved_inputs(
        self,
        mission_id: str,
        network: TaskNetworkSnapshot,
        spec: OccurrenceSpec,
        *,
        accepted: AcceptedOutputsIndex | None = None,
        witnesses: Mapping[str, ValidityWitness] | None = None,
        now_ms: int | None = None,
    ) -> ResolutionResult:
        """The DATA gate's input, never ``None`` (P2.3c part 2).

        ``input_result`` answers ``None`` for an occurrence that declares no data
        requirement, and :func:`~..graph.eligibility.evaluate_readiness` reads ``None``
        as *"no input resolution was supplied"* — which is ``WAITING_DATA``.  That is
        the right answer for a caller that forgot to resolve, and the wrong one for an
        occurrence that has nothing to resolve: before this, every input-less leaf in
        the plan sat in ``WAITING_DATA`` forever and nothing could ever be dispatched.

        So an occurrence with no declared requirement gets an **empty frozen**
        manifest rather than no manifest.  "This dispatch consumed nothing" is a
        statement an ``Acceptance`` can quote and an ``input_manifest_hash`` can
        cover; "nobody resolved the inputs" is not, and the two must not share a
        representation.
        """

        result = self.input_result(
            mission_id, network, spec, accepted=accepted, witnesses=witnesses, now_ms=now_ms
        )
        if result is not None:
            return result
        return ResolutionResult(manifest=InputManifest(consumer_task_ref=spec.task_id))

    def accepted_outputs(
        self,
        mission_id: str,
        network: TaskNetworkSnapshot,
        *,
        outcomes: Mapping[OccurrenceId, OccurrenceOutcome] | None = None,
    ) -> AcceptedOutputsIndex:
        """What the resolver may choose from.

        P2.3b reads the *completed producers* from the completion scopes; a producer
        without an accepted output therefore gives ``WAITING_DATA`` rather than a
        silent all-ancestors sweep.
        """

        del outcomes  # the completion scopes below are the reading, not the projection
        if self.semantics().active_plan_revision(mission_id) is None:
            return AcceptedOutputsIndex(outputs=(), completed_producers=frozenset())

        statuses = {
            spec.occurrence_id: read_occurrence_completion(
                self.store, mission_id, str(spec.occurrence_id)
            )
            for spec in network.occurrences
        }
        prepared = frozenset(key for key, value in statuses.items() if value.preparation_ready)
        allowed_acceptances = {
            acceptance
            for value in statuses.values()
            for acceptance in value.preparation_acceptance_ids
        }

        completion_store = OperationCompletionStore(self.store)
        scoped_outputs = []
        for output in self._recorded_outputs(mission_id, network):
            state = statuses.get(output.producer_occurrence)
            if (
                state is None
                or output.acceptance_id not in allowed_acceptances
                or output.acceptance_id not in state.preparation_acceptance_ids
            ):
                continue
            row = completion_store.get_acceptance_scope_exact(mission_id, output.acceptance_id)
            if row is None:
                continue
            contribution = row["document"]
            if any(
                ref.id == output.artifact_id
                and str(ref.revision) == output.source_revision
                and ref.content_hash == output.content_hash
                for ref in contribution.output_artifact_refs
            ):
                scoped_outputs.append(output)
        # 片 B：完成的中间目标的端口对到它收尾步骤的产出；它也就成了"已完成的生产者"。
        done = frozenset(key for key, value in statuses.items() if value.complete)
        delivered = self.goal_port_outputs(mission_id, network, scoped_outputs, complete=done)
        # 授权版本（阶段 D）：一个生产者端口上"当前计数的那次验收"的产出版本。同一端口出现
        # 两个计数中的版本就不填——跟随的输入会如实报"授权版本读不到"，不任选。
        seen: dict[tuple[Any, str], set[str]] = {}
        for output in (*scoped_outputs, *delivered):
            seen.setdefault((output.producer_occurrence, output.output_port), set()).add(
                output.source_revision
            )
        return AcceptedOutputsIndex(
            outputs=(*scoped_outputs, *delivered),
            completed_producers=prepared | {item.producer_occurrence for item in delivered},
            authorized_revisions={
                key: next(iter(found)) for key, found in seen.items() if len(found) == 1
            },
        )

    def goal_port_outputs(
        self,
        mission_id: str,
        network: TaskNetworkSnapshot,
        outputs: Sequence[Any],
        *,
        complete: Collection[Any],
    ) -> tuple[Any, ...]:
        """片 B：完成的中间目标对外交付什么——它收尾步骤在同名端口上已验收的产出。

        一步执行时只铺通过输入端口接进来的上游产出；中间目标自己不执行、没有产出，接它端口的
        后续步骤要的其实是它下面做出来的东西。规则只有一条：目标的端口 = 采用做法的收尾步骤的
        同名端口（收尾步骤本身是子目标时再往下找）。只有 ``complete`` 里的目标（组合审阅通过、
        结论已形成）才对外交付，所以后续步骤拿到的一定是审过的那一版。

        返回的是别名：原产出记录原样，只把"生产者"换成这个目标，接它的数据依赖于是按原规则
        解析、发见证、冻结输入，文件归属仍是真正写出它的那一步。

        阶段 D（补全方案 2.2）：目标端口与收尾步骤同名端口声明的格式不同就不出别名——接它的步骤
        拿不到数据（数据未绑定），并记一条 ``GoalPortSchemaMismatch`` 说明是哪个目标哪个端口。
        """

        if not complete:
            return ()
        by_place: dict[tuple[str, str], list[Any]] = {}
        for item in outputs:
            by_place.setdefault((str(item.producer_occurrence), str(item.output_port)), []).append(
                item
            )
        roots = {str(item) for item in network.root_occurrence_ids}
        goals: list[tuple[Any, str, tuple[str, ...]]] = []
        for spec in network.occurrences:
            if (
                spec.form is not TaskForm.COMPOUND
                or str(spec.occurrence_id) in roots
                or spec.occurrence_id not in complete
            ):
                continue
            adopted = network.adopted_instance_for(spec.occurrence_id)
            ports: Any = {
                str(port.port_key): port.schema_ref
                for port in network.binding_for_occurrence(spec.occurrence_id).output_ports
            }
            if adopted is None or not ports:
                continue
            try:
                contract = (
                    self.semantics()
                    .get_method(str(adopted.method_ref.method_id), int(adopted.method_ref.version))
                    .contract
                )
            except StoreError:
                continue
            finalizer = next(
                (
                    str(child.occurrence_id)
                    for child in adopted.child_bindings
                    if str(child.slot_key) == str(contract.composition.finalizer_step)
                ),
                None,
            )
            if finalizer is not None:
                goals.append((spec.occurrence_id, finalizer, ports))
        aliases: list[Any] = []
        moved = True
        while moved:  # a finalizer that is itself a sub-goal resolves one level per pass
            moved = False
            for goal, finalizer, ports in goals:
                for port, schema in ports.items():
                    if (str(goal), port) in by_place or (finalizer, port) not in by_place:
                        continue
                    delivered = {
                        str(item.port_key): item.schema_ref
                        for item in network.binding_for_occurrence(
                            OccurrenceId(finalizer)
                        ).output_ports
                    }
                    if delivered.get(port) != schema:
                        try:
                            append_hierarchical_event(
                                self.store,
                                "GoalPortSchemaMismatch",
                                mission_id,
                                key=f"{mission_id}:{goal}:{port}:{int(network.plan_revision)}",
                                payload={
                                    "goal_occurrence": str(goal),
                                    "port": port,
                                    "finalizer_occurrence": finalizer,
                                    "detail": "the goal's port and its finalizer step's port declare "
                                    "different formats; the goal delivers nothing on it",
                                },
                            )
                        except StoreError:
                            pass  # read from a read-only view: the note is written by the next writable read
                        continue
                    named = [
                        replace(item, producer_occurrence=goal)
                        for item in by_place[(finalizer, port)]
                    ]
                    by_place[(str(goal), port)] = named
                    aliases.extend(named)
                    moved = True
        return tuple(aliases)

    def _complete_goals(self, mission_id: str, network: TaskNetworkSnapshot) -> frozenset[Any]:
        """The non-root compound occurrences whose completion is recorded (片 B)."""

        roots = {str(item) for item in network.root_occurrence_ids}
        return frozenset(
            spec.occurrence_id
            for spec in network.occurrences
            if spec.form is TaskForm.COMPOUND
            and str(spec.occurrence_id) not in roots
            and network.binding_for_occurrence(spec.occurrence_id).output_ports
            and read_occurrence_completion(self.store, mission_id, str(spec.occurrence_id)).complete
        )

    def declared_output_ports_for(
        self, mission_id: str, task_id: str, network: TaskNetworkSnapshot | None = None
    ) -> tuple[dict[str, Any], ...]:
        """The output ports this leaf is expected to deliver on, for its own context.

        P2.3c part 2d, decision 4.  Part 2c's smoke ended here: the plan declared one
        output port, the model wrote two files under names of its own, and nothing had
        ever told it that ``repository_facts`` was the name the plan used.  The accept
        side then paired by substring (``artifacts/…`` matches a port called ``facts``)
        or by "one port, one file" — the guess TG design §10.2 forbids — or, honestly
        but uselessly, not at all.

        The ports come from :func:`~.accepted_outputs.output_ports_in_revision`, the
        same function the accept side checks against, so "which ports exist" has one
        answer rather than two.  A port is in this list because a ``DataRequirement``
        consumes it **or** because a ``criterion_link`` of the adopted method points at
        this occurrence (P2.3d / defect D3: the finalizer step's port has no downstream
        edge and the root's success criterion still reads it, so a leaf that was never
        told the port existed delivered nothing and the root review rejected the
        Mission).  An occurrence that is neither consumed nor criterion-linked feeds
        nobody and is not asked for at all.

        Deliberately **not** intersected with the binding's own ``output_ports``.  The
        memo describes the two sources as an intersection, and a port a live edge
        consumes while the producer's contract does not declare it *is* a real defect —
        but it is a defect in the **plan**, and answering it by quietly dropping the
        port here would hide it: the leaf would then be told to produce nothing, the
        consumer would wait for a port nobody was asked for, and the Mission would stall
        with no reason anybody could read.  Plan integrity is where that belongs, and
        until it refuses there, the honest thing is to ask for the port the edge needs
        and let ``OUTPUT_PORT_UNCLAIMED`` name it if it never arrives.  The binding is
        consulted for ``cardinality`` only, because that is where TG §4.3 declares it;
        a port the binding does not list is reported ``single``, the contract's own
        default.
        """

        semantics = self.semantics()
        active = semantics.active_plan_revision(mission_id)
        if active is None:
            return ()
        revision = int(active.revision)
        occurrence = next(
            (
                spec.occurrence_id
                for spec in semantics.list_plan_memberships(mission_id, revision)
                if str(spec.task_id) == str(task_id)
            ),
            None,
        )
        if occurrence is None:
            return ()
        ports = output_ports_in_revision(semantics, mission_id, revision, occurrence, str(task_id))
        binding = semantics.task_semantics_of(mission_id, str(task_id))
        specs = {} if binding is None else {item.port_key: item for item in binding.output_ports}
        del network  # the rows are the authority here; the network is not rebuilt
        return tuple(
            {
                "port": port,
                "required": True,
                "cardinality": (
                    str(specs[port].cardinality) if port in specs else str(PortCardinality.SINGLE)
                ),
                "schema": {"id": schema.id, "version": int(schema.version)},
            }
            for port, schema in sorted(ports.items())
        )

    def carried_root_criteria_for(
        self, mission_id: str, task_id: str
    ) -> tuple[dict[str, Any], ...]:
        """The root criteria this leaf is answerable for, for its own context (P2.3h).

        Read from the adopted method's ``criterion_links`` through
        :func:`~.accepted_outputs.carried_criteria_for` — the same rows the frozen
        completion scope projects the leaf's acceptance criteria from — so what the
        Worker is *told* it carries and what its acceptance is *held to* cannot drift
        apart.  Each entry
        names the root criterion, the leaf criterion it is judged under, the
        ``evidence_requirement`` the method wrote for the link (the sentence the
        leaf's report has to satisfy) and the ports that output is read from.

        The Grok C3 run is why: ``c-change-explained`` hung on a step whose only
        declared port carried code, the Worker was never told it owed an explanation
        anywhere, and the root reviewer correctly found none in the package.
        """

        semantics = self.semantics()
        active = semantics.active_plan_revision(mission_id)
        if active is None:
            return ()
        revision = int(active.revision)
        occurrence = next(
            (
                spec.occurrence_id
                for spec in semantics.list_plan_memberships(mission_id, revision)
                if str(spec.task_id) == str(task_id)
            ),
            None,
        )
        if occurrence is None:
            return ()
        links = carried_criteria_for(semantics, mission_id, revision, occurrence)
        if not links:
            return ()
        ports = sorted(
            output_ports_in_revision(semantics, mission_id, revision, occurrence, str(task_id))
        )
        goals: dict[str, str] = {}
        for item in links:
            parent = str(item.parent_task_id)
            if parent not in goals:
                root = semantics.task_semantics_of(mission_id, parent)
                goals[parent] = "" if root is None else str(root.goal_signature.statement)
        return tuple(
            {
                "root_criterion_id": str(item.parent_criterion_id),
                "root_task_id": str(item.parent_task_id),
                "root_goal_statement": goals[str(item.parent_task_id)],
                "leaf_criterion_id": str(item.leaf_criterion_id),
                "evidence_requirement": str(item.evidence_requirement),
                "ports": list(ports),
            }
            for item in links
        )

    def _recorded_outputs(self, mission_id: str, network: TaskNetworkSnapshot) -> Sequence[Any]:
        """The accepted outputs the resolver may choose from (P2.3c part 2).

        Read from migration 17's ``acceptance_outputs``, which is the *recorded*
        answer to "which artifact did this Acceptance accept, at which output port".
        P2.3b returned nothing here and said why: nothing in the schema held that
        fact, and taking the Attempt's artifacts and guessing the port from the path
        would be the all-ancestors sweep §24.1 decision 4 removed, wearing a typed
        name.

        The one writer is ``ResolutionCommitsMixin.accept_review`` (P2.3c part 2b),
        and it writes inside its own transaction only what
        :func:`.accepted_outputs.check_against_ports` passed — the same rule
        :func:`.accepted_outputs.check_declared` applies to a whole network, read off
        the committed ``DataRequirement`` rows instead of a rebuilt snapshot — so an
        entry exists only where the plan drew an edge.  A static guard in the suite
        keeps that the *only* writer.

        Two rows are dropped rather than offered, for the same reason in two lanes:

        * an occurrence this revision no longer contains — an accepted output of a
          retired branch is history, and the resolver choosing from it would bind a
          consumer to work the current plan does not do;
        * an entry whose ``Acceptance`` is no longer CURRENT — ``list_acceptance_outputs``
          joins ``acceptances`` and filters on validity (review F3), so a supersede or a
          revoke stops feeding consumers at the read rather than after them.
        """

        live = {str(spec.occurrence_id) for spec in network.occurrences}
        outputs: list[AcceptedOutput] = []
        for row in self.semantics().list_acceptance_outputs(mission_id):
            if str(row.get("producer_occurrence")) not in live:
                continue
            outputs.append(accepted_output_from_json(row))
        return tuple(outputs)

    def attempt_inputs(self, mission_id: str, task_id: str) -> list[UpstreamInput]:
        """What one dispatch of ``task_id`` starts from, in the new mode.

        Only the resolved :class:`InputManifest` — §24.1 decision 4's "no longer
        collect every ancestor's files".  An ORDER-only predecessor contributes
        nothing however many artifacts it accepted, which is the property T015 /
        T066 exist to hold.  2026-10-11 第三批: the manifest carries the delivery
        bindings too, so this is a straight materialisation of it; a version conflict
        the resolution reported is handed to the write-conflict repair request rather
        than stopping the task as an artifact conflict (2026-10-09 第 4 条).
        """

        network = self.network(mission_id)
        spec = next((item for item in network.occurrences if str(item.task_id) == task_id), None)
        if spec is None:
            raise missing_bindings(mission_id, [task_id])
        result = self.input_result(mission_id, network, spec)
        if result is None:
            return []
        clashes = []
        for problem in result.problems:
            if problem.kind not in (
                ResolutionProblemKind.TARGET_PATH_CONFLICT,
                ResolutionProblemKind.DELIVERY_VERSION_CONFLICT,
            ):
                continue
            artifacts = [self.store.get_artifact(artifact_id) for artifact_id in problem.candidates]
            paths = sorted({artifact.path for artifact in artifacts if artifact is not None})
            steps = list(problem.producers) or [
                artifact.task_id for artifact in artifacts if artifact is not None
            ]
            clashes.append(
                {
                    "path": paths[0] if paths else "",
                    "artifacts": list(problem.candidates),
                    "steps": list(dict.fromkeys(steps)),
                    "consumer": task_id,
                }
            )
        if clashes:
            raise WriteConflictPending(clashes)
        if result.manifest is None or not result.manifest.is_frozen:
            # A producer that has not finished leaves a *symbolic* binding.  That is
            # "not resolved yet", which is the DATA gate's business (WAITING_DATA) and
            # not a materialisation failure — so nothing is placed and nothing raises.
            return []
        # 阶段 D（10-03）：开工前先查这一步直接上游牵涉的写入冲突（规则一：没有先后的两步把同一文件写成
        # 两样）。
        producers = tuple(
            dict.fromkeys(str(item.producer_task_ref) for item in result.manifest.bindings)
        )
        clashes = self.producer_write_conflicts(mission_id, network, touching=producers)
        if clashes:
            raise WriteConflictPending(clashes)
        return manifest_upstream_inputs(
            result.manifest, self.target_rules_for(task_id), network=network
        )

    def write_conflicts(
        self, mission_id: str, *, touching: Sequence[str] | None = None
    ) -> list[dict[str, Any]]:
        """Files the plan lets two versions of reach one place with nothing saying which (阶段 D；
        2026-10-09 四项修复第 4 条扩成两条规则).

        Rule one: two steps with no order between them each delivered a different content at one
        path.  Which one a later step starts from is not something the plan says, and picking one
        ("first come") would be the Harness deciding it.  Reported as ``{path, steps, artifacts}``.
        The files are the ones each step's *acceptance* covers (2026-10-11 第三批: the same set the
        manifest delivers), read off the acceptances that feed a DATA edge.

        Rule two: several versions of one path reach one consumer and the frozen manifests prove
        no relay that makes one of them the latest; or two of its *port* files land on one path.
        That is the delivery resolution's own refusal (:func:`resolve_deliveries`), read back from
        :meth:`attempt_inputs` so the repair request and the refusal to start never disagree.  A
        relay (one step delivers the next version of a file it demonstrably received) is not a
        conflict; identical content is not one either.  ``touching`` keeps only the conflicts one
        of those producer tasks is a party to (rule one) or feeds (rule two).
        """
        network = self.network(mission_id)
        found = self.producer_write_conflicts(mission_id, network, touching=touching)
        consumers: dict[str, set[str]] = {}
        for item in network.data_requirements:
            consumers.setdefault(
                str(network.occurrence(item.consumer_occurrence).task_id), set()
            ).add(str(network.occurrence(item.producer_occurrence).task_id))

        def note(conflicts: Sequence[Mapping[str, Any]]) -> None:
            for item in conflicts:
                if not any(
                    f["path"] == item["path"] and set(f["artifacts"]) == set(item["artifacts"])
                    for f in found
                ):
                    found.append(dict(item))

        for consumer, producers in consumers.items():
            if touching is not None and not producers & set(touching):
                continue  # 只读这些上游喂的下游，不解析别的
            consumer_task = self.store.get_task(consumer)
            if consumer_task is not None and consumer_task.status is TaskStatus.COMPLETED:
                # 做完的下游拿到的输入早已冻结在它的清单里，再解析
                # 一遍只是开销（每轮修复触发都对全部下游完整解析，步骤多时核心用例集从 601 秒涨到 670 秒）。
                # 没做完的（含停下的）照旧报：那是规划器与事后取证都要看的事实。
                continue
            try:
                self.attempt_inputs(mission_id, consumer)
            except WriteConflictPending as error:
                note(error.conflicts)
            except ArtifactConflict:
                continue  # 那一步自己的别的物化问题，由它自己的派发报，不在这里替它停
        return found

    def producer_write_conflicts(
        self,
        mission_id: str,
        network: TaskNetworkSnapshot,
        *,
        touching: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Rule one of :meth:`write_conflicts`, on its own so dispatch can ask it before a step
        starts without re-entering the per-consumer resolution (rule two)."""

        semantics = self.semantics()
        occurrences: dict[str, list[Any]] = {}
        for spec in network.occurrences:
            if spec.form is TaskForm.PRIMITIVE:
                occurrences.setdefault(str(spec.task_id), []).append(spec.occurrence_id)
        producers_by_acceptance: dict[str, str] = {}
        for row in semantics.list_acceptance_outputs(mission_id):
            producers_by_acceptance.setdefault(
                str(row["acceptance_id"]), str(row["producer_task_ref"])
            )
        holders: dict[str, list[tuple[str, Any]]] = {}
        for acceptance_id, task_id in sorted(producers_by_acceptance.items()):
            if task_id not in occurrences:
                continue
            for ref in semantics.get_acceptance(acceptance_id).artifact_refs:
                artifact = self.store.get_artifact(ref.id)
                if (
                    artifact is None
                    or artifact.content_hash != ref.content_hash
                    or artifact.path.startswith(".")
                ):
                    continue
                holders.setdefault(artifact.path, []).append((task_id, artifact))
        ordered = None
        found: list[dict[str, Any]] = []
        for path in sorted(holders):
            entries = holders[path]
            for seat, (left, left_file) in enumerate(entries):
                for right, right_file in entries[seat + 1 :]:
                    if left == right or left_file.content_hash == right_file.content_hash:
                        continue
                    if touching is not None and not {left, right} & set(touching):
                        continue
                    ordered = ordered or ordering_of(network)
                    if any(ordered(a, b) for a in occurrences[left] for b in occurrences[right]):
                        continue
                    found.append(
                        {
                            "path": path,
                            "steps": [left, right],
                            "artifacts": [left_file.id, right_file.id],
                        }
                    )
        return found

    def delivery_sources(self, mission_id: str, manifest: InputManifest) -> DeliverySources:
        """What the DATA-bound producers of ``manifest``'s consumer hand on, read once.

        The one place the delivery rule is spelled out (2026-10-11 第三批):

        * a writing producer hands on every file its *acceptance* covers
          (``Acceptance.artifact_refs`` — the per-acceptance set, not the task-level
          one, so a re-acceptance changes it in step with the licence);
        * a read-only producer hands on only its copies of original-workspace files
          (it changes nothing; its new ``tests/`` stay off the consumer's baseline);
        * operation candidates and dot-files are not part of the delivered work
          (the same two exclusions :meth:`write_conflicts` applies);
        * a continuation producer (every input port also an output port) also hands
          on what it received — read from its own frozen manifest, one level, since
          that manifest already holds what *it* was handed;
        * ``received`` maps each acceptance to the exact ``(producer, path, hash)`` set
          its Attempt was frozen with — the only proof of a relay the resolution accepts.

        A source that cannot be read is named in ``unavailable`` rather than skipped.
        """

        mission = self.store.get_mission(mission_id)
        if mission is None:
            raise ContractError("delivery sources: the Mission is unavailable")
        seed = set((mission.final_report or {}).get("workspace_seed", {}))
        semantics = self.semantics()
        files: list[DeliveredFile] = []
        received: dict[str, frozenset[tuple[str, str, str]]] = {}
        unavailable: list[tuple[str, str]] = []
        direct: dict[str, tuple[str, int]] = {}
        for binding in manifest.bindings:
            direct.setdefault(
                binding.acceptance_id,
                (str(binding.producer_task_ref), int(binding.support_revision)),
            )

        def frozen_inputs_of(acceptance: Any) -> tuple[DeliveredFile, ...] | None:
            """The files the producer's accepted Attempt started from, as its manifest froze them."""
            try:
                frozen = decode_frozen_manifest(
                    semantics.get_input_manifest(acceptance.input_manifest_hash)
                )
            except (StoreError, ContractError) as error:
                unavailable.append(
                    (str(acceptance.acceptance_id), f"frozen inputs unreadable: {error}")
                )
                return None
            rules = self.target_rules_for(str(acceptance.task_id))
            entries: list[DeliveredFile] = []
            for port in frozen.bindings:
                path = rules.target_for(port).path
                entries.append(
                    DeliveredFile(
                        port.producer_task_ref,
                        port.acceptance_id,
                        int(port.support_revision),
                        port.artifact_id,
                        path,
                        port.content_hash,
                    )
                )
            for item in frozen.deliveries:
                entries.append(
                    DeliveredFile(
                        item.producer_task_ref,
                        item.acceptance_id,
                        int(item.support_revision),
                        item.artifact_id,
                        item.path,
                        item.content_hash,
                    )
                )
            return tuple(entries)

        for acceptance_id, (producer, support) in sorted(direct.items()):
            try:
                acceptance = semantics.get_acceptance(acceptance_id)
            except StoreError as error:
                unavailable.append((acceptance_id, str(error)))
                continue
            if acceptance.mission_id != mission_id or str(acceptance.task_id) != producer:
                unavailable.append(
                    (acceptance_id, "the acceptance belongs to another Mission or task")
                )
                continue
            semantic = semantics.task_semantics_of(mission_id, producer)
            read_only = semantic is not None and read_only_leaf(semantic)
            for ref in acceptance.artifact_refs:
                artifact = self.store.get_artifact(ref.id)
                if (
                    artifact is None
                    or artifact.mission_id != mission_id
                    or artifact.task_id != producer
                    or artifact.content_hash != ref.content_hash
                ):
                    unavailable.append(
                        (acceptance_id, f"accepted artifact {ref.id} is missing or changed")
                    )
                    continue
                if artifact.path.startswith("."):
                    continue
                if read_only and artifact.path not in seed:
                    continue
                files.append(
                    DeliveredFile(
                        TaskRef(producer),
                        acceptance_id,
                        support,
                        artifact.id,
                        artifact.path,
                        artifact.content_hash,
                    )
                )
            frozen = frozen_inputs_of(acceptance)
            if frozen is None:
                continue
            received[acceptance_id] = frozenset(item.version_key for item in frozen)
            if not _is_continuation(semantic):
                continue
            for item in frozen:
                files.append(replace(item, carried_from=TaskRef(producer)))
                if item.acceptance_id in received:
                    continue
                try:
                    origin = semantics.get_acceptance(item.acceptance_id)
                except StoreError as error:
                    unavailable.append((item.acceptance_id, str(error)))
                    continue
                inner = frozen_inputs_of(origin)
                if inner is not None:
                    received[item.acceptance_id] = frozenset(entry.version_key for entry in inner)
        return DeliverySources(
            files=tuple(files), received=received, unavailable=tuple(unavailable)
        )
