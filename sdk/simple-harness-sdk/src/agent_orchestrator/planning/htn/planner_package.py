# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The package a hierarchical Planner is given, assembled in one layer.

What the model reads is facts, in twelve views, plus the things it is asked to act on:

``views``
    ``goals`` (every goal and step on the board, with its state), ``obligations``,
    ``plans`` (the current revision: adopted method instances, order and data edges),
    ``methods`` (the library rows for the goals that can take a method, each with its
    applicability reports and — for a method proposed in this Mission — its review),
    ``facts``, ``accepted_results``, ``failures`` (an index of what failed; no
    details), ``capabilities``, ``planning_budgets``, ``method_library``, ``library_reads``
    and ``knowledge`` (第 2 批 K03: the blackboard as the Planner sees it — verified knowledge
    that is current right now and the reviewer-checked step summaries, newest first, bounded;
    原计划 §11 / §24 第 12 步 "重新判断方向要看到全局视图").
``repair_requests`` / ``human_answers``
    what happened that the Planner is asked about, and what the user already said.
    **The details of a failure live here and nowhere else**: the pending request
    carries the reviewer's findings and the verifier's record; ``views.failures`` only
    names the attempt, so the same record is not sent twice.
``method_selection`` / ``method_proposal_contexts``
    per goal that still needs a method: which library methods can run, and the
    material a new method is written from.
the candidate lists
    ``sharing_candidates``, ``successor_types``, ``evidence_predicates`` — what the corresponding decisions may name.
the protocol fields
    ``planning_protocol``, ``planning_subjects``, ``visible_refs``,
    ``previous_feedback``, ``decision_limits`` (V2 §38).

There is no intermediate mapping: every row is built once, from the store, the
registry and the network, and is the row the model sees.  The module is pure — the
reads happen in ``orchestrator/planner_views``, which hands the rows to
:func:`assemble_planner_package`.

Nothing here decides anything.  Every value is read, and the Planner's answer still
goes through decode → admission → compile → commit before a single row moves.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from simple_harness.contracts import canonical_json

from ...contracts.htn import ReadItemKind, TaskForm
from ...contracts.models import ContractError
from ...contracts.planning_decisions import (
    MAX_PD_ALTERNATIVES,
    MAX_PD_ARGUMENTS,
    MAX_PD_ASSUMPTIONS,
    MAX_PD_BINDINGS,
    MAX_PD_BLOCKERS,
    MAX_PD_HUMAN_OPTIONS,
    MAX_PD_RATIONALE_CHARS,
    MAX_PD_REASON_REFS,
    MAX_PD_REPLAN_TRIGGERS,
    MAX_PD_UNCERTAINTIES,
    MAX_PD_WAIT_REFS,
    PLANNING_DECISION_V1,
    PlanningFeedbackV1,
    PlanningRefKind,
    PlanningRefV1,
    exposed_enablement,
)
from ...contracts.semantic_base import content_hash_of
from ...graph.task_network import TaskNetworkSnapshot

#: The views, in the order the package lists them.  ``method_library`` is the directory of
#: library precedents (id and one line of purpose, never the method); ``library_reads`` the
#: entries this Mission's Planner read, with the method as written for its source Mission.
VIEW_NAMES = (
    "goals", "obligations", "plans", "methods", "facts", "accepted_results",
    "failures", "capabilities", "planning_budgets", "method_library", "library_reads",
    "knowledge",
)

#: The whole provider envelope is bounded, not just the views: the package is a prompt.
MAX_PACKAGE_BYTES = 96 * 1024

#: How many accepted results / failures one package lists.  The newest are kept.
MAX_ACCEPTED_RESULTS = 24
MAX_FAILURES = 16

#: The views size pressure may shorten, in the order it does so.  Goals, the plan and
#: the budgets are mandatory and are never dropped.
_SHRINKABLE = ("accepted_results", "failures", "facts", "knowledge", "methods", "library_reads")


class PlannerPackageError(ContractError):
    """The package cannot be assembled within its bounds."""

#: V2 §48 / §18: how many reference quadruples one package exposes.  A bound, for the
#: same reason every other cap here exists — the package is a prompt — and a
#: *deterministic* one: the surviving refs are the sorted prefix, so two builds of the
#: same request carry the same ones and the request binding can be recomputed.
MAX_VISIBLE_REFS = 128
#: §16 decision limits, spelled with the contract constants so the package and the
#: codec can never disagree about a number.
DECISION_LIMITS: Mapping[str, int] = {
    "MAX_PD_RATIONALE_CHARS": MAX_PD_RATIONALE_CHARS,
    "MAX_PD_REASON_REFS": MAX_PD_REASON_REFS,
    "MAX_PD_ASSUMPTIONS": MAX_PD_ASSUMPTIONS,
    "MAX_PD_ALTERNATIVES": MAX_PD_ALTERNATIVES,
    "MAX_PD_UNCERTAINTIES": MAX_PD_UNCERTAINTIES,
    "MAX_PD_REPLAN_TRIGGERS": MAX_PD_REPLAN_TRIGGERS,
    "MAX_PD_BINDINGS": MAX_PD_BINDINGS,
    "MAX_PD_WAIT_REFS": MAX_PD_WAIT_REFS,
    "MAX_PD_BLOCKERS": MAX_PD_BLOCKERS,
    "MAX_PD_HUMAN_OPTIONS": MAX_PD_HUMAN_OPTIONS,
    "MAX_PD_ARGUMENTS": MAX_PD_ARGUMENTS,
}
#: How many method definitions one package lists per goal signature.  A bound, because
#: the package is a prompt: a registry with two hundred methods for one signature would
#: push the open goals out of the model's attention long before it ran out of context.
MAX_METHODS_PER_SIGNATURE = 12
#: How many refused-applicability reports one package carries.  Same reason.
MAX_APPLICABILITY_REPORTS = 12
#: How many recorded observations one package quotes.  Same reason again, and one
#: more: the section exists so the Planner can *cite* a fact, not so it can browse
#: the Mission's whole evidence history.
MAX_FACTS = 24
#: How many blackboard rows (verified knowledge, then checked step summaries) one package
#: carries.  Same reason: the Planner reads them to judge direction, not to browse the
#: Mission's whole memory; the catalogue stays readable by the Workers' tools.
MAX_KNOWLEDGE = 16
#: One candidate row's text in ``views.knowledge``: a lead, not a fact, so a bounded
#: preview (the catalogue shows 200); a cut says so at its end (opt.167 评估建议第 5 条).
CANDIDATE_CONTENT_LIMIT = 600


def candidate_content(content: str) -> str:
    """A candidate's text as the Planner sees it: whole, or the first
    :data:`CANDIDATE_CONTENT_LIMIT` characters and a note that it was cut."""

    if len(content) <= CANDIDATE_CONTENT_LIMIT:
        return content
    return (content[:CANDIDATE_CONTENT_LIMIT]
            + f"（候选结论共 {len(content)} 字，这里只列前 {CANDIDATE_CONTENT_LIMIT} 字）")

def _ref_key(reference: Any) -> tuple[str, int, str]:
    to_json = getattr(reference, "to_json", None)
    data = to_json() if callable(to_json) else dict(reference or {})
    return (
        str(data.get("method_id", data.get("id", ""))),
        int(data.get("version", 0) or 0),
        str(data.get("content_hash", "")),
    )


@dataclass(frozen=True, slots=True)
class MethodApplicability:
    """One ``assess_method`` verdict, with the goal and method it is about.

    :class:`~.applicability.ApplicabilityReport` carries the verdict and nothing that
    says *whose* verdict it is — ``assess_method`` is called with the goal and the
    method as arguments — so the two identities travel beside it rather than being
    guessed from the report.
    """

    goal_occurrence_id: str
    goal_signature_id: str
    method_ref: Any
    report: Any


def applicability_reports(
    reports: Sequence[MethodApplicability], *, limit: int = MAX_APPLICABILITY_REPORTS
) -> tuple[dict[str, Any], ...]:
    """Why the methods that *looked* applicable were not.

    Four axes kept apart, because they are four different repairs: a missing
    precondition is waited for or observed, a parameter mismatch is re-bound, a
    missing capability needs a different method, and a missing authority needs a
    person.  Merging them into "not applicable" is what made the round unactionable.

    """

    out: list[dict[str, Any]] = []
    for entry in list(reports)[: max(0, limit)]:
        report = entry.report
        gate = getattr(report, "authorization", None)
        out.append(
            {
                "goal_occurrence_id": str(entry.goal_occurrence_id),
                "goal_signature_id": str(entry.goal_signature_id),
                "method_ref": _ref_json(entry.method_ref),
                "verdict": str(getattr(report, "status", "")),
                "precondition_truth": str(getattr(report, "truth", "")),
                # The preconditions nobody has looked at yet.  These are the
                # propositions an evidence round would observe, and the reason the
                # ``facts`` section below exists.
                "unknown_preconditions": [
                    str(item) for item in getattr(report, "needs_evidence", ())
                ],
                "conflicting_preconditions": [
                    str(item) for item in getattr(report, "conflicts", ())
                ],
                "parameter_problems": [str(item) for item in getattr(report, "type_errors", ())],
                "missing_capabilities": [
                    str(item) for item in getattr(report, "unmet_capabilities", ())
                ],
                # Authority is a *gate decision*, not a list: it is refused with a
                # reason or it is not refused at all.
                "missing_authority": (
                    []
                    if gate is None or bool(getattr(gate, "allowed", False))
                    else [str(getattr(gate, "reason", "authorisation refused"))]
                ),
            }
        )
    return tuple(out)


# ------------------------------------------------------------------------- view rows


def goal_rows(
    network: TaskNetworkSnapshot,
    *,
    task_states: Mapping[str, Mapping[str, Any]] | None = None,
    under_repair: Sequence[str] = (),
) -> tuple[dict[str, Any], ...]:
    """``views.goals``: every goal and step on the board, each said once.

    ``open`` marks a compound that has no adopted method yet — exactly the goals a
    REFINE or a PROPOSE_METHOD may name.  A refined compound carries ``adopted_method``
    instead, and ``under_repair`` says a pending repair request is about it (so a
    decision that replaces its method can quote the instance it retires).  A primitive
    is listed so the Planner does not re-plan work it already committed.  The task
    state, when the caller supplies it, is the store's own; it is an execution fact and
    never implies the work was accepted.
    """

    subjects = {row["occurrence_id"]: row["subject_key"] for row in planning_subjects(network)}
    repairing = {str(item) for item in under_repair}
    rows: list[dict[str, Any]] = []
    for spec in sorted(network.occurrences, key=lambda item: str(item.occurrence_id)):
        occurrence = str(spec.occurrence_id)
        binding = network.binding_for_occurrence(spec.occurrence_id)
        compound = spec.form is TaskForm.COMPOUND
        adopted = network.adopted_instance_for(spec.occurrence_id) if compound else None
        row: dict[str, Any] = {
            "subject_key": subjects[occurrence],
            "occurrence_id": occurrence,
            "task_id": str(spec.task_id),
            "obligation_id": str(spec.obligation_id),
            "form": str(spec.form),
            "signature_id": str(binding.goal_signature.signature_id),
            "statement": binding.goal_signature.statement,
            "params": dict(binding.typed_parameters),
            "requirement_refs": list(binding.requirement_refs),
            "contract_revision": int(binding.contract_revision),
            "requiredness": str(spec.requiredness),
            "capability_requirements": sorted(str(item) for item in binding.capability_requirements),
            "open": compound and adopted is None,
            "adopted_method": None if adopted is None else {
                "method_instance_id": str(adopted.instance_id),
                "method_ref": _method_ref(adopted.method_ref),
            },
            "under_repair": occurrence in repairing,
        }
        state = (task_states or {}).get(occurrence)
        if state is not None:
            row.update(task_status=state["task_status"], task_version=state["task_version"],
                       occurrence_outcome=state["occurrence_outcome"])
        rows.append(row)
    return tuple(rows)


def plan_row(
    network: TaskNetworkSnapshot, *, method_instance_refs: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """``views.plans``: the current revision — what was adopted and how steps connect.

    Each adopted method instance carries its reference quadruple (the form a repair
    decision quotes) beside its own record; each data edge carries the hash a
    REBIND_INPUT must repeat.
    """

    refs = {str(ref["id"]): dict(ref) for ref in method_instance_refs
            if ref.get("kind") == PlanningRefKind.METHOD_INSTANCE.value}
    adopted = set(network.adopted_instance_ids)
    return {
        "plan_revision": int(network.plan_revision),
        "root_occurrences": [str(item) for item in network.root_occurrence_ids],
        "required_obligations": sorted(str(item) for item in network.required_obligations),
        "adopted_methods": [
            {"method_instance_ref": refs.get(str(instance.instance_id)), **instance.to_json()}
            for instance in network.method_instances if instance.instance_id in adopted
        ],
        "order_constraints": [item.to_json() for item in network.order_constraints],
        "data_requirements": [
            {"requirement": edge.to_json(), "expected_requirement_hash": content_hash_of(edge.to_json())}
            for edge in network.data_requirements
        ],
    }


def method_signatures(network: TaskNetworkSnapshot, under_repair: Sequence[str] = ()) -> tuple[str, ...]:
    """The goal types the library is read for: goals with no method, and goals a
    pending repair is about (their method may be replaced)."""

    repairing = {str(item) for item in under_repair}
    found: set[str] = set()
    for spec in network.occurrences:
        if spec.form is not TaskForm.COMPOUND:
            continue
        if network.adopted_instance_for(spec.occurrence_id) is None or str(spec.occurrence_id) in repairing:
            found.add(str(network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id))
    return tuple(sorted(found))


def method_rows(
    registry: Any,
    signatures: Sequence[str],
    *,
    mission_id: str | None = None,
    retired: Sequence[Mapping[str, Any]] = (),
    reviews: Mapping[tuple[str, int, str], Mapping[str, Any]] | None = None,
    reports: Sequence[MethodApplicability] = (),
    schemas: Any = None,
    first: Sequence[str] = (),
    limit: int = MAX_METHODS_PER_SIGNATURE,
) -> tuple[tuple[dict[str, Any], ...], int]:
    """``views.methods``: the library rows for ``signatures``, and how many were left out.

    One row per method, carrying everything the package says about it: the reference
    quadruple a decision quotes (``method_ref``, the same spelling ``visible_refs``
    uses), its steps, its parameters, its preconditions, the applicability reports of
    this request, the plan's retirement history (``rejected_reasons`` — a fact to
    weigh, not a ban) and, for a method proposed in this Mission, where its independent
    review stands (``review``; a library method has none).  ``registry_status`` is
    shown and is read-only.

    At most ``limit`` rows per goal type.  ``first`` names method ids that must not be
    the ones left out (the methods that can run now): a registry's alphabetical first
    twelve must not hide the only applicable one.
    """

    reasons: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for item in retired:
        reasons.setdefault(_ref_key(item.get("method_ref")), []).append(
            {key: value for key, value in dict(item).items() if key != "method_ref"})
    by_method: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for report in applicability_reports(reports, limit=len(reports)):
        row = dict(report)
        by_method.setdefault(_ref_key(row.pop("method_ref")), []).append(row)
    priority = {str(item) for item in first}
    rows: list[dict[str, Any]] = []
    omitted = 0
    for signature in sorted({str(item) for item in signatures}):
        found = _methods_for(registry, signature)
        if mission_id is not None:
            from ...contracts.htn import MissionRef
            found = tuple(contract for contract in found
                          if registry.retrievable(contract.method_ref(), mission_id=MissionRef(mission_id)))
        ordered = sorted(found, key=lambda contract: (
            str(contract.method_id) not in priority, str(contract.method_id), int(contract.method_version)))
        omitted += max(0, len(ordered) - max(0, limit))
        for contract in ordered[: max(0, limit)]:
            reference = contract.method_ref()
            key = _ref_key(reference)
            review = (reviews or {}).get(key)
            schema = None if schemas is None else schemas.resolve(contract.parameter_schema_ref)
            if schemas is not None and schema is None:
                raise PlannerPackageError("method parameter schema is unavailable")
            rows.append({
                "method_ref": _method_ref(reference),
                "goal_signature_id": signature,
                "registry_status": str(_status(registry, contract)),
                **({} if review is None else {"review": dict(review)}),
                "rejected_reasons": [dict(item) for item in reasons.get(key, ())],
                "steps": [
                    {
                        "step": str(getattr(item, "local_id", "")),
                        "form": str(getattr(item, "form", "")),
                        "task_type_ref": _ref_id(getattr(item, "task_type_ref", None)),
                        "required_capabilities": sorted(
                            str(one) for one in getattr(item, "required_capabilities", ())),
                    }
                    for item in getattr(contract, "steps", ())
                ],
                "required_capabilities": sorted(
                    str(item) for item in getattr(contract, "required_capabilities", ())),
                "parameters": [] if schema is None else [field.to_json() for field in schema.fields],
                "applicable_when": [item.to_json() for item in getattr(contract, "applicable_when", ())],
                "applicability": by_method.get(key, []),
            })
    return tuple(rows), omitted


def fact_rows(
    observations: Sequence[Any], *, read_item: Any = None, state_of: Any = None,
    limit: int = MAX_FACTS,
) -> tuple[tuple[dict[str, Any], ...], int]:
    """``views.facts``: the newest observation per proposition, and how many were left out.

    Each row carries the reference a decision quotes (``observation_ref``): the
    observation's id, the semantic revision and the content hash the read-set checker
    recomputes.  ``read_item`` is that checker's own function, so the proposing side
    and the checking side cannot disagree about what a revision is; the literal below
    is the offline form, for a package rendered without a store behind it.

    Only the newest record per proposition is shown: an earlier one is superseded, and
    quoting it would refuse the commit.  ``state_of`` (proposition key → availability,
    truth) is the evidence snapshot's reading; the package states it and draws nothing
    from it.
    """

    newest: dict[str, Any] = {}
    for record in observations:
        key = str(getattr(record, "proposition_key", ""))
        if key:
            newest[key] = record
    rows: list[dict[str, Any]] = []
    for key in sorted(newest):
        record = newest[key]
        identity = str(getattr(record, "observation_id", ""))
        if read_item is not None:
            quoted = read_item(ReadItemKind.FACT, identity).to_json()
        else:
            to_json = getattr(record, "to_json", None)
            quoted = {"kind": "fact", "id": identity, "semantic_revision": 1,
                      "content_hash": content_hash_of(to_json()) if callable(to_json) else ""}
        availability, truth = ("recorded", "UNKNOWN") if state_of is None else state_of(key)
        rows.append({
            "observation_ref": quoted,
            "proposition_key": key,
            "polarity": bool(getattr(record, "polarity", False)),
            "availability": str(availability),
            "truth": str(truth),
            "coverage": str(getattr(record, "coverage", "")),
            "observer": getattr(record, "observer_id", None),
            "times": {name: getattr(record, name, None) for name in (
                "observed_at_ms", "recorded_at_ms", "valid_from_ms", "valid_until_ms",
                "query_watermark_ms")},
        })
    kept = tuple(rows[: max(0, limit)])
    return kept, len(rows) - len(kept)


def knowledge_rows(
    records: Sequence[Any], summaries: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]] = (), *, limit: int = MAX_KNOWLEDGE,
) -> tuple[tuple[dict[str, Any], ...], int]:
    """``views.knowledge`` (第 2 批 K03): the blackboard's three layers a Planner judges
    direction from, and how many rows were left out.

    ``records`` are the Mission's knowledge records that are current *right now* — the
    caller filters them with the one judgement every reader shares
    (``context.knowledge_tools.current_knowledge``); nothing here re-decides validity.
    ``summaries`` are the reviewer-checked step summaries (``step_summaries``), already
    newest first.  Verified knowledge leads, newest first, because it is what a decision
    may treat as fact; a summary tells what a step did.  ``candidates`` (夜间 N3-03) are
    the claims not verified yet (``context.knowledge_tools.candidate_claims``): leads, not
    facts, each with its ``marker``; they come last so the cap drops them first and never
    crowds out verified knowledge.  Each row says which layer it is
    and carries the ``ref`` a Worker would cite — it is *not* a planning reference
    quadruple, so none of these rows enters ``visible_refs``.
    """

    rows: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: (-float(item.created_at), str(item.id))):
        rows.append({
            "layer": "verified",
            "ref": f"{record.id}@{int(record.version)}",
            "id": str(record.id), "version": int(record.version), "key": record.key,
            "stance": record.stance,
            "basis": str(record.verifier.get("basis") or "test_observation"),
            "content": record.content, "source_task": record.source_task,
            "assurance_level": record.assurance_level,
            "validity_interval": None if record.validity_interval is None else dict(record.validity_interval),
            "permitted_uses": None if record.permitted_uses is None else list(record.permitted_uses),
        })
    for row in summaries:
        rows.append({
            "layer": "summary", "id": str(row["id"]), "source_task": row["source_task"],
            "summary": row["summary"],
            "artifacts": [{"path": a["path"], "version": a["version"]} for a in row.get("artifacts", ())],
            "checked_by": row["checked_by"],
        })
    for row in candidates:
        rows.append({
            "layer": "candidate", "id": str(row["id"]), "status": str(row["status"]),
            "marker": str(row["marker"]), "key": row["key"], "content": candidate_content(str(row["content"])),
            "source_task": row["source_task"],
        })
    kept = tuple(rows[: max(0, limit)])
    return kept, len(rows) - len(kept)


def failure_outline(failure: Mapping[str, Any] | None) -> dict[str, Any]:
    """What ``views.failures`` says about one failed attempt: which layers failed and
    their one-line summaries.  The nested record (the verifier's ``detail``, the
    envelope it judged) is not repeated here — it is in the pending repair request
    about this failure, which is where the Planner is asked to act on it."""

    def scalars(value: Mapping[str, Any]) -> dict[str, Any]:
        return {str(key): item for key, item in value.items()
                if item is None or isinstance(item, (str, int, float, bool))}

    if not isinstance(failure, Mapping):
        return {}
    outline = scalars(failure)
    layers = [scalars(item) for item in failure.get("failures", ()) if isinstance(item, Mapping)]
    if layers:
        outline["failures"] = layers
    return outline


def failure_index(
    *, attempts: Sequence[tuple[float, Mapping[str, Any]]],
    planning: Sequence[tuple[float, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """``views.failures`` in the order it is shown: the steps' failed attempts first,
    newest first, then the Planner's own refused replies, newest first.

    Each input is ``(time, row)``.  Attempts lead because a decision names one of them
    (a retry quotes the attempt it redoes): however many replies were refused, the
    count cap must not push a step's failure out of the index.
    """

    def newest_first(rows: Sequence[tuple[float, Mapping[str, Any]]]) -> list[dict[str, Any]]:
        return [dict(row) for _, row in sorted(rows, key=lambda item: item[0], reverse=True)]

    return [*newest_first(attempts), *newest_first(planning)]


def attempt_is_indexed(package: Mapping[str, Any], attempt_id: str) -> bool:
    """Whether this request's failure index names ``attempt_id`` — the question a
    retry decision is checked against.  A row with no attempt (a refused reply) is
    simply not that attempt."""

    rows = (package.get("views") or {}).get("failures", ())
    return any(isinstance(row, Mapping)
               and (row.get("attempt_review_ref") or {}).get("id") == attempt_id
               for row in rows)


def task_type_row(spec: Any, schemas: Any) -> dict[str, Any]:
    """One task type as a decision may quote it: its reference, what it does, the
    parameters to bind and its ports."""

    schema = None if spec.parameter_schema_ref is None else schemas.resolve(spec.parameter_schema_ref)
    return {
        "task_type_ref": spec.task_type_ref.to_json(),
        "form": str(spec.form),
        "statement": spec.goal_signature.statement,
        "parameter_schema_ref": (None if spec.parameter_schema_ref is None
                                 else spec.parameter_schema_ref.to_json()),
        "parameters": [field.to_json() for field in getattr(schema, "fields", ())],
        "input_ports": [port.port_key for port in spec.input_ports],
        "output_ports": [port.port_key for port in spec.output_ports],
    }


# ------------------------------------------------------------------ visible references
# Every reference the model may quote, as the §17 quadruple.  A pure function of the
# rows the package shows plus the caller's authoritative digests for the kinds whose
# rows quote only an id (tasks, obligations): §18 makes the quadruple a byte-match
# contract, so a digest is stated by whoever owns it and never derived here.


def _one_ref(
    kind: str, id: object, semantic_revision: object, content_hash: object = None
) -> dict[str, Any] | None:
    """One reference quadruple, or ``None`` when the source is not usable.

    A reference the package cannot state correctly is *skipped*, never invented.  The
    hard part is the hash: §18 makes the quadruple a byte-match contract, §5.1 says
    where each kind's hash comes from, and the only value this function may emit is
    that *authoritative* one.  So a source that does not carry a hash — a task whose
    binding was never attached, an obligation with no ledger beside it — is dropped
    rather than given a plausible-looking digest.  A digest derived from
    ``{kind, id, revision}`` describes the reference, not the object it points at, and
    every downstream re-check (admission, the read-set checker) compares hashes:
    a fabricated one fails that comparison *after* the model has been told to quote
    it, which is worse than the ref never having been offered.

    Other reasons to drop rather than repair, all deliberately strict:

    * a non-integer revision (``"3"``, ``3.5``) or a revision below 1 — ``semantic_revision``
      is an ``index(minimum=1)`` on the wire, so there is no correct value to
      substitute;
    * a malformed hash — a method ref whose digest disagrees with ``method_contracts``
      would be refused at commit time anyway, so it is not shown.

    ``PlanningRefV1`` is the validator, so the shape returned here is exactly the
    shape the admission layer re-checks the model's answer against.
    """

    if id is None or str(id) == "":
        return None
    if isinstance(semantic_revision, bool) or not isinstance(semantic_revision, int):
        # ``index()`` refuses ``bool`` and ``"3"`` alike; mirror that here so the
        # collector never coerces a string or float into a revision.
        return None
    if semantic_revision < 1:
        return None
    if content_hash is None or content_hash == "":
        return None
    try:
        PlanningRefV1(
            kind=PlanningRefKind(str(kind)),
            id=str(id),
            semantic_revision=semantic_revision,
            content_hash=content_hash,
        )
    except (ContractError, ValueError):
        return None
    return {
        "kind": str(kind),
        "id": str(id),
        "semantic_revision": semantic_revision,
        "content_hash": str(content_hash),
    }


def _method_ref(value: Any) -> dict[str, Any] | None:
    """A ``{id, version, content_hash}`` method triple, however the package spells it."""

    to_json = getattr(value, "to_json", None)
    data = to_json() if callable(to_json) else value
    if not isinstance(data, Mapping):
        return None
    method_id = data.get("id", data.get("method_id"))
    version = data.get("version", data.get("semantic_revision"))
    if method_id is None or version is None:
        return None
    return _one_ref(PlanningRefKind.METHOD.value, method_id, version, data.get("content_hash"))


def _authority_index(
    authorities: object,
) -> Mapping[tuple[str, str], Mapping[str, Any]]:
    """The authoritative digest per ``(kind, id)`` the *caller* supplied (§5.1).

    ``visible_refs`` has to carry the *object's* digest, and a goal row quotes a
    ``contract_revision`` but never a hash, while nothing in the rows reaches the
    obligation ledger.  Rather than derive a digest from the ref (which describes the
    ref, not the object), the caller passes one quadruple per referenced object —
    read from ``task_semantics.content_hash`` / the obligation's canonical JSON — as a
    plain argument.  The table itself is never a package field.

    A ``(kind, id)`` that is not in the table has no authoritative digest, so its ref
    is simply not emitted; a malformed row is skipped here rather than raising.
    """

    index: dict[tuple[str, str], Mapping[str, Any]] = {}
    rows = authorities if isinstance(authorities, Sequence) else ()
    for entry in rows:
        if not isinstance(entry, Mapping):
            continue
        kind = entry.get("kind")
        id = entry.get("id")
        if kind is None or id is None:
            continue
        index[(str(kind), str(id))] = entry
    return index


def _ref_sort_key(ref: Mapping[str, Any]) -> tuple[str, str, int, str]:
    return (
        str(ref["kind"]),
        str(ref["id"]),
        int(ref["semantic_revision"]),
        str(ref["content_hash"]),
    )


def _authority_sort_key(row: Any) -> tuple[str, str, int, str]:
    """A total order over side-table rows that never raises on a malformed one.

    ``_authority_index`` already drops a row without ``kind``/``id``; the sort merely
    has to be *deterministic* for the rows that survive, so a missing or non-integer
    revision is ordered as ``0``/``-1`` rather than being validated here.
    """

    if not isinstance(row, Mapping):
        return ("", "", 0, str(row))
    try:
        revision = int(row.get("semantic_revision", 0))
    except (TypeError, ValueError):
        revision = 0
    return (
        str(row.get("kind", "")),
        str(row.get("id", "")),
        revision,
        str(row.get("content_hash", "")),
    )


def _task_refs(
    row: Mapping[str, Any], authorities: Mapping[tuple[str, str], Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The task and the obligation a goal row names, with their §5.1 digests.

    Both take their revision and hash from the authority table and nothing from the
    row: the row's ``contract_revision`` is what the *goal* quotes, while the ref's
    revision is the binding's, and the two need not agree.  A kind the table does not
    cover yields no ref.
    """

    out: list[dict[str, Any]] = []
    for kind, key in ((PlanningRefKind.TASK.value, "task_id"),
                      (PlanningRefKind.OBLIGATION.value, "obligation_id")):
        identity = row.get(key)
        if not identity:
            continue
        authority = authorities.get((kind, str(identity)))
        if authority is None:
            continue
        ref = _one_ref(kind, identity, authority.get("semantic_revision"), authority.get("content_hash"))
        if ref is not None:
            out.append(ref)
    return out


def _observation_ref(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """A fact row's reference as an ``observation`` (V2 §17: there is no ``fact`` kind)."""

    quoted = row.get("observation_ref")
    if not isinstance(quoted, Mapping):
        return None
    return _one_ref(PlanningRefKind.OBSERVATION.value, quoted.get("id"),
                    quoted.get("semantic_revision"), quoted.get("content_hash"))


def _accepted_ref(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """An accepted result's acceptance ref, else its resolution ref — two kinds with
    two sources (§5.1); neither is renamed into the other."""

    for key, kind in (("acceptance_ref", PlanningRefKind.ACCEPTANCE.value),
                      ("resolution_ref", PlanningRefKind.RESOLUTION.value)):
        goal = row.get(key)
        if not isinstance(goal, Mapping):
            continue
        ref = _one_ref(kind, goal.get("id"), goal.get("semantic_revision", goal.get("revision")),
                       goal.get("content_hash"))
        if ref is not None:
            return ref
    return None


def collect_refs(
    views: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    authorities: object = (),
    extra: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Every reference the views show, deduped on the quadruple and sorted.

    ``authorities`` is a lookup table and nothing more: the digests of the tasks and
    obligations the goal rows name.  ``extra`` is for references no view row carries —
    an adopted method instance a repair may name, a goal that can be shared and its
    resolution.  A source that cannot state its reference correctly is skipped, never
    invented.
    """

    authority_rows = sorted(_as_json_refs(authorities), key=_authority_sort_key)
    index = _authority_index(authority_rows)
    collected: list[dict[str, Any]] = []
    for row in views.get("goals", ()):
        if isinstance(row, Mapping):
            collected.extend(_task_refs(row, index))
    for row in views.get("methods", ()):
        if isinstance(row, Mapping):
            ref = _method_ref(row.get("method_ref"))
            if ref is not None:
                collected.append(ref)
    for row in views.get("facts", ()):
        if isinstance(row, Mapping):
            ref = _observation_ref(row)
            if ref is not None:
                collected.append(ref)
    for row in views.get("accepted_results", ()):
        if isinstance(row, Mapping):
            ref = _accepted_ref(row)
            if ref is not None:
                collected.append(ref)
    for row in extra:
        if not isinstance(row, Mapping):
            continue
        ref = _one_ref(row.get("kind"), row.get("id"), row.get("semantic_revision"), row.get("content_hash"))
        if ref is not None:
            collected.append(ref)
    unique = {_ref_sort_key(ref): ref for ref in collected}
    return [unique[key] for key in sorted(unique)]


def planning_subjects(network: TaskNetworkSnapshot) -> tuple[dict[str, Any], ...]:
    """The §19 subject bindings: one per occurrence, unique and recomputable.

    ``subject_key`` is derived from the occurrence and its semantic binding, not
    handed out as a counter, so the same request computed twice yields the same
    keys and the request binding (§34 ``subject_bindings_hash``) can be recomputed
    by whoever checks the reply.  The key is scoped to this one PlannerRequest; the
    model only ever copies it back.
    """

    subjects: list[dict[str, Any]] = []
    for spec in network.occurrences:
        binding = network.binding_for_occurrence(spec.occurrence_id)
        material = {
            "occurrence_id": str(spec.occurrence_id),
            "task_id": str(spec.task_id),
            "obligation_id": str(spec.obligation_id),
            "contract_revision": int(binding.contract_revision),
        }
        subjects.append(
            {
                "subject_key": "subject-" + content_hash_of(material)[:32],
                **material,
            }
        )
    return tuple(sorted(subjects, key=lambda item: item["subject_key"]))


def _as_json_refs(values: object) -> list[Any]:
    rows: list[Any] = []
    for item in values if isinstance(values, Sequence) else ():  # type: ignore[arg-type]
        to_json = getattr(item, "to_json", None)
        rows.append(to_json() if callable(to_json) else item)
    return rows


def visible_refs_digest(refs: object) -> str:
    """The canonical-JSON SHA-256 of a visible-ref list (§34 request binding)."""

    return content_hash_of(_as_json_refs(refs))


def subject_bindings_hash(subjects: object) -> str:
    """The canonical-JSON SHA-256 of a subject-binding list (§34 request binding)."""

    return content_hash_of(_as_json_refs(subjects))


def package_hash(package: Mapping[str, Any]) -> str:
    """The canonical-JSON SHA-256 of a whole package (§34 ``package_hash``).

    Canonical JSON sorts object keys, so two packages that differ only in the order
    their keys were inserted hash the same; array order *does* count, exactly as §15
    says.  A model never computes or submits this — the caller does, before sending.
    """

    return content_hash_of(package)


def _feedback_json(value: Any) -> dict[str, Any] | None:
    """The ``PlanningFeedbackV1`` a re-ask carries, or ``None`` for a first round.

    A mapping is accepted and *validated* through the contract rather than trusted:
    a malformed feedback object is a programming error on the calling side and must
    not reach the model as a half-formed field.
    """

    if value is None:
        return None
    if isinstance(value, PlanningFeedbackV1):
        feedback = value
    else:
        feedback = PlanningFeedbackV1.from_json(value, "previous_feedback")
    return feedback.to_json()


def _network_authorities(network: TaskNetworkSnapshot) -> list[dict[str, Any]]:
    """The authoritative §5.1 digest of every task the network carries.

    ``task_semantics.content_hash`` is the binding's own canonical-JSON digest, and
    the network is built from those bindings — so the packager can state a task's
    authority without a store lookup, and does so for every binding on the board
    (open goals and committed primitives alike) rather than only the ones a section
    happens to quote.
    """

    authorities: list[dict[str, Any]] = []
    for binding in getattr(network, "task_bindings", ()):
        digest = getattr(binding, "content_hash", None)
        authorities.append(
            {
                "kind": PlanningRefKind.TASK.value,
                "id": str(binding.task_id),
                "semantic_revision": int(binding.contract_revision),
                "content_hash": digest() if callable(digest) else binding.contract_hash,
            }
        )
    return authorities


def _merge_authorities(
    builder_rows: Sequence[Any], caller_rows: object
) -> list[dict[str, Any]]:
    """Builder-attested rows first, caller rows only filling kinds the builder cannot.

    Both sides are ``{kind, id, semantic_revision, content_hash}`` quadruples and both
    may name a task or an obligation.  The builder is authoritative for what it can
    read (tasks, from the network's bindings), so a caller row whose ``(kind, id)`` is
    already present is dropped instead of overwriting; caller rows for the kinds the
    builder cannot reach (obligations) pass through.  The result keeps the builder's
    ordering for its own rows and appends the caller's, and is stable under either
    input's order.
    """

    # Sort the caller's rows first: the merge keeps the *first* occurrence of a key,
    # so without a deterministic order two callers handing the same rows in a
    # different order would pick different winners for a duplicate (kind, id).
    caller = sorted(_as_json_refs(caller_rows), key=_authority_sort_key)
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in (*builder_rows, *caller):
        if not isinstance(row, Mapping):
            # A malformed row is skipped here exactly as ``_authority_index`` skips it.
            merged.append(row)  # type: ignore[arg-type]
            continue
        kind = row.get("kind")
        id = row.get("id")
        if kind is None or id is None:
            merged.append(dict(row))
            continue
        key = (str(kind), str(id))
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(row))
    return merged


# ------------------------------------------------------------------------- assembling


def _size(package: Mapping[str, Any]) -> int:
    return len(canonical_json(package).encode("utf-8"))


def assemble_planner_package(
    *,
    package_version: int,
    mission: Any,
    network: TaskNetworkSnapshot,
    views: Mapping[str, Sequence[Mapping[str, Any]]],
    sections: Mapping[str, Any] | None = None,
    authorities: object = (),
    extra_refs: Sequence[Mapping[str, Any]] = (),
    previous_feedback: Any = None,
    omitted: Mapping[str, int] | None = None,
    requirements: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The whole package, as a plain mapping the context builder can seal.

    ``views`` is the twelve views, row for row as the model will read them; ``sections``
    is everything else the reader gathered (repair requests, candidate lists, …).
    This function adds the protocol fields, computes ``visible_refs`` from the rows
    actually shown, applies the count caps and the size bound, and records what it
    left out in ``truncated`` / ``omitted_counts`` — a cut row is hidden, not absent,
    and the package says so.

    Returned as data rather than as a rendered string, so the caller keeps ownership
    of rendering and of the context hash.
    """

    if set(views) != set(VIEW_NAMES):
        raise PlannerPackageError(
            f"the package needs exactly the twelve views; got {sorted(views)}")
    omitted_counts = {str(key): int(value) for key, value in dict(omitted or {}).items() if int(value) > 0}
    shown: dict[str, list[Any]] = {name: [dict(row) for row in views[name]] for name in VIEW_NAMES}
    for name, limit in (("accepted_results", MAX_ACCEPTED_RESULTS), ("failures", MAX_FAILURES)):
        if len(shown[name]) > limit:
            omitted_counts[name] = omitted_counts.get(name, 0) + len(shown[name]) - limit
            shown[name] = shown[name][:limit]
    decision_types, repair_kinds = exposed_enablement()
    package: dict[str, Any] = {
        "package_version": int(package_version),
        "role": "planner",
        "mode": "hierarchical",
        "mission": {
            "mission_id": mission.id,
            "goal": mission.goal,
            # 现行要求：第几版、每条的编号、条目版本与原文（调用方从最新要求修订读出）
            "requirements": dict(requirements or {"revision": 0, "criteria": []}),
            "allowed_tools": list(mission.allowed_tools),
            "budget": mission.budget.to_json(),
            "risk_level": mission.risk_level,
        },
        "planning_protocol": {
            "protocol": PLANNING_DECISION_V1,
            "enabled_decision_types": decision_types,
            "enabled_repair_kinds": repair_kinds,
        },
        "planning_subjects": [dict(item) for item in planning_subjects(network)],
        "previous_feedback": _feedback_json(previous_feedback),
        "decision_limits": dict(DECISION_LIMITS),
        "views": shown,
        **{str(key): value for key, value in dict(sections or {}).items()},
    }

    def refs() -> list[dict[str, Any]]:
        return collect_refs(shown, authorities=authorities, extra=extra_refs)

    def drop_last(name: str) -> None:
        shown[name].pop()
        omitted_counts[name] = omitted_counts.get(name, 0) + 1

    # Everything shown with a reference is quotable, so the reference bound is kept by
    # showing fewer optional rows — never by listing a row whose reference is missing.
    for name in _SHRINKABLE:
        while len(refs()) > MAX_VISIBLE_REFS and shown[name]:
            drop_last(name)
    if len(refs()) > MAX_VISIBLE_REFS:
        raise PlannerPackageError(
            "required visible references exceed 128; narrow the planning subject")

    def seal() -> None:
        package["visible_refs"] = refs()
        package["omitted_counts"] = dict(omitted_counts)
        package["truncated"] = bool(omitted_counts)

    seal()
    for name in _SHRINKABLE:
        while _size(package) > MAX_PACKAGE_BYTES and shown[name]:
            drop_last(name)
            seal()
    if _size(package) > MAX_PACKAGE_BYTES:
        raise PlannerPackageError("mandatory request exceeds 96 KiB; narrow the planning subject")
    return package


# ------------------------------------------------------------------ registry probing
# The registry is a Protocol in ``hierarchical_dispatch`` and a concrete class in
# ``planning/htn/registry``; deployments bring their own.  These readers ask for the
# richer interface and fall back to the narrower one.


def _methods_for(registry: Any, signature: str) -> Sequence[Any]:
    """Every definition the registry holds whose goal type is ``signature``.

    ``MethodRegistry`` exposes ``method_refs()`` + ``definition(ref)``; a deployment
    that brings a richer index (``methods_for_signature``) is used directly.  Neither
    is required, because a deployment with no registry at all is a deployment whose
    Planner is simply shown no methods — which is a package that says "there is
    nothing to choose from", not a crash mid-prompt.
    """

    reader = getattr(registry, "methods_for_signature", None)
    if callable(reader):
        try:
            return tuple(reader(signature))
        except (KeyError, TypeError, ValueError):
            return ()
    refs = getattr(registry, "method_refs", None)
    definition = getattr(registry, "definition", None)
    if not callable(refs) or not callable(definition):
        return ()
    found: list[Any] = []
    try:
        for reference in refs():
            contract = definition(reference)
            if contract is None:
                continue
            if str(getattr(contract.goal_type_ref, "id", "")) == signature:
                found.append(contract)
    except (KeyError, TypeError, ValueError):
        return ()
    return tuple(found)


def _status(registry: Any, contract: Any) -> str:
    reader = getattr(registry, "registration", None)
    if not callable(reader):
        return "UNKNOWN"
    try:
        registration = reader(contract.method_ref())
    except (KeyError, TypeError, ValueError):
        return "UNKNOWN"
    return (
        "UNKNOWN"
        if registration is None
        else str(getattr(registration, "registry_status", "UNKNOWN"))
    )


def _ref_id(value: Any) -> str | None:
    return None if value is None else str(getattr(value, "id", value))


def _ref_json(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    to_json = getattr(value, "to_json", None)
    return to_json() if callable(to_json) else {"id": str(value)}


__all__ = (
    "DECISION_LIMITS",
    "MAX_ACCEPTED_RESULTS",
    "MAX_APPLICABILITY_REPORTS",
    "MAX_FACTS",
    "MAX_FAILURES",
    "MAX_METHODS_PER_SIGNATURE",
    "MAX_PACKAGE_BYTES",
    "MAX_VISIBLE_REFS",
    "VIEW_NAMES",
    "MethodApplicability",
    "PlannerPackageError",
    "applicability_reports",
    "assemble_planner_package",
    "attempt_is_indexed",
    "collect_refs",
    "fact_rows",
    "failure_index",
    "failure_outline",
    "goal_rows",
    "method_rows",
    "method_signatures",
    "package_hash",
    "plan_row",
    "planning_subjects",
    "subject_bindings_hash",
    "task_type_row",
    "visible_refs_digest",
)
