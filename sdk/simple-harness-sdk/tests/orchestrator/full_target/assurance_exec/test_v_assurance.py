# SPDX-License-Identifier: Apache-2.0
"""V group (validity / sources): plan cases V01–V14 over the real evaluator.

V01–V05, V12 and the bounded half of V14 drive the production anchor selector,
grounded/clean closure and the fixed acceptance evaluator directly: they are
pure over rows the validity service reads, so no Store is stubbed and nothing
here can cache a prior VERIFIED. The Store-level cases (barrier, racing epoch,
authority/expiry, shared consumers, certificate context, root quarantine,
event replay) live in ``test_v_assurance_store.py``.
"""

from __future__ import annotations

from typing import NamedTuple

import pytest

from agent_orchestrator.assurance.checks import Grade
from agent_orchestrator.assurance.codec import AssuranceError, fingerprint
from agent_orchestrator.assurance.grounding import compute_grounded_support
from agent_orchestrator.assurance.refs import AssuranceRef, Pin
from agent_orchestrator.contracts.evidence_state import (
    ObservationRecord,
    QueryCompleteness,
    TruthValue,
    Validity,
    WitnessPurpose,
)
from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.contracts.semantic_base import (
    EvidenceRef,
    EvidenceRefKind,
    TypedRef,
    TypedRefKind,
    VersionedRef,
    content_hash_of,
)
from agent_orchestrator.knowledge.assurance_sources import (
    CheckAnchorInput,
    ReviewAnchorInput,
    content_acceptable_key,
    evaluate_acceptance_support,
)
from agent_orchestrator.knowledge.justifications import (
    Anchor,
    AnchorCandidate,
    AnchorOrigin,
    AnchorRejection,
    AnchorSelector,
    Atom,
    JustificationSet,
    PropositionPremise,
    SupportGraph,
    WitnessKind,
)
from agent_orchestrator.knowledge.predicates import (
    ArgumentType,
    PredicateParameter,
    PredicateSignature,
    WorldAssumption,
    proposition_key,
)

MISSION = "mission-v"
SCOPE = "scope-v"
NOW = 1_700_000_000_000
HASH = "a" * 64


def signature(predicate_id, *, closed=False, observers=()):
    declaration = {"id": predicate_id, "version": 1}
    return PredicateSignature(
        predicate_ref=VersionedRef(predicate_id, 1, content_hash_of(declaration)),
        parameters=(PredicateParameter("subject_id", ArgumentType.STRING),),
        world_assumption=WorldAssumption.CLOSED if closed else WorldAssumption.OPEN,
        observer_ids=tuple(observers),
    )


def key_of(sig, subject="s"):
    return proposition_key(sig, {"subject_id": subject})


class ObservationRow(NamedTuple):
    """One observation as the selector tests feed it: the record, its scope, its hash."""

    record: ObservationRecord
    scope_id: str
    content_hash: str


def observation(observation_id, key, polarity, *, source="src-1", authoritative=False,
                observer="observer-1", scope=SCOPE, observed_at=NOW - 10, valid_until=None):
    record = ObservationRecord(
        observation_id=observation_id, proposition_key=key, polarity=polarity,
        source_ref=TypedRef(kind=TypedRefKind.SOURCE, id=source, revision=1, content_hash=HASH),
        observed_at_ms=observed_at, recorded_at_ms=observed_at,
        coverage=QueryCompleteness.AUTHORITATIVE_WITH_SCOPE if authoritative else QueryCompleteness.BEST_EFFORT,
        coverage_scope=scope if authoritative else None,
        query_watermark_ms=observed_at if authoritative else None,
        valid_until_ms=valid_until, observer_id=observer,
    )
    return ObservationRow(record, scope, fingerprint(record.to_json()))


def candidate(row, **overrides):
    record = row.record
    fields = dict(
        observation=record, scope_id=row.scope_id,
        source_group=f"{record.source_ref.kind!s}:{record.source_ref.id}", origin=AnchorOrigin.OBSERVATION,
        evidence_ref=EvidenceRef(kind=EvidenceRefKind.OBSERVATION, id=record.observation_id, revision=1,
                                 content_hash=row.content_hash),
        observer_id=record.observer_id,
    )
    fields.update(overrides)
    return AnchorCandidate(**fields)


def rule(conclusion, *premises, version="rule-v1"):
    return JustificationSet(conclusion=conclusion, premises=tuple(PropositionPremise(Atom(key=p)) for p in premises),
                            rule_version=version)


def select(candidates, signatures, *, purpose=WitnessPurpose.ACCEPT, now=NOW, scope=SCOPE):
    return AnchorSelector(scope_id=scope, purpose=purpose, as_of_ms=now, signatures=signatures).select(candidates)


def review_input(acceptable=True, record_id="rec-1"):
    return ReviewAnchorInput(AssuranceRef("review", Pin(record_id, 1, HASH)), acceptable, NOW - 5)


def check_input(binding_id="cb-1", grade=Grade.PASS, not_after=None):
    return CheckAnchorInput(AssuranceRef("check_binding", Pin(binding_id, 1, HASH)), grade, NOW - 5, not_after)


def evaluate(**overrides):
    fields = dict(mission_id=MISSION, scope_id=SCOPE, purpose="ACCEPT", now_ms=NOW, subject_hash=HASH,
                  review=review_input(), checks=(check_input(),))
    fields.update(overrides)
    return evaluate_acceptance_support(**fields)


# --------------------------------------------------------------------------- V01
def test_anchors_and_explicit_negation():
    p, k = signature("pred.p"), signature("pred.k")
    P, K = key_of(p), key_of(k)
    graph = SupportGraph((rule(K, P),))
    # A source *statement* with an unregistered predicate anchors nothing.
    unregistered = observation("obs-unreg", key_of(signature("pred.other")), True)
    # An empty / best-effort query that found nothing is not a denial.
    empty_query = observation("obs-empty", P, False)
    # A qualified negative observation (authoritative, scoped, watermarked) is one.
    denial = observation("obs-denial", P, False, authoritative=True, source="src-2")
    signatures = {P: p, K: k}

    selection = select([candidate(unregistered), candidate(empty_query)], signatures)
    assert selection.reason_for("obs-unreg") is AnchorRejection.UNREGISTERED_PREDICATE
    assert selection.reason_for("obs-empty") is AnchorRejection.NOT_AUTHORITATIVE_NEGATIVE
    support = compute_grounded_support(graph, selection)
    assert support.supported.truth_for(P) is TruthValue.UNKNOWN  # not FALSE
    assert support.supported.truth_for(K) is TruthValue.UNKNOWN and not support.usable(K)

    selection = select([candidate(denial)], signatures)
    assert selection.admitted_ids() == {"obs-denial"}
    support = compute_grounded_support(graph, selection)
    assert support.supported.truth_for(P) is TruthValue.FALSE
    assert not support.usable(K)
    # A CLOSED-world denial additionally needs an authoritative observer.
    closed = signature("pred.closed", closed=True, observers=("auditor",))
    C = key_of(closed)
    stranger = observation("obs-stranger", C, False, authoritative=True, observer="someone")
    auditor = observation("obs-auditor", C, False, authoritative=True, observer="auditor")
    selection = select([candidate(stranger), candidate(auditor)], {C: closed})
    assert selection.reason_for("obs-stranger") is AnchorRejection.OBSERVER_NOT_AUTHORITATIVE
    assert selection.admitted_ids() == {"obs-auditor"}

    # At the acceptance evaluator the review and the checks are the only anchors
    # (stage D): an unacceptable review is not usable, whatever else is stored.
    result = evaluate(review=review_input(acceptable=False))
    assert result.truth is not TruthValue.TRUE and not result.usable
    assert result.conclusion_key == content_acceptable_key(MISSION, SCOPE, HASH)
    # An acceptable review and a PASS check: usable, and the clean support names
    # exactly the review and the check.
    result = evaluate()
    assert result.truth is TruthValue.TRUE and result.usable
    assert {ref.kind for ref in result.clean_support_refs} == {"review", "check_binding"}
    assert result.earliest_expiry_ms is None


# --------------------------------------------------------------------------- V02
def test_rule_admission_required():
    a, k = signature("pred.a"), signature("pred.k")
    A, K = key_of(a), key_of(k)
    anchor = observation("obs-a", A, True)
    signatures = {A: a, K: k}
    # An unadmitted rule never fires: K stays UNKNOWN without it, TRUE with it.
    selection = select([candidate(anchor)], signatures)
    assert not compute_grounded_support(SupportGraph(()), selection).supported.reached(Atom(K))
    support = compute_grounded_support(SupportGraph((rule(K, A),)), selection)
    assert support.usable(K)
    witnesses = support.clean.witnesses_for(Atom(K))
    assert [w.kind for w in witnesses] == [WitnessKind.DERIVED]  # traceable to the admitted rule
    # The acceptance evaluator has exactly one rule: the fixed acceptance rule.
    assert "rules:1" in evaluate().reasons


# --------------------------------------------------------------------------- V03
def test_cycle_without_anchor():
    a, b, e = signature("pred.a"), signature("pred.b"), signature("pred.e")
    A, B, E = key_of(a), key_of(b), key_of(e)
    graph = SupportGraph((rule(A, B), rule(B, A), rule(A, A, E)))
    signatures = {A: a, B: b, E: e}
    only_e = observation("obs-e", E, True)
    real_a = observation("obs-a", A, True, source="src-a")

    support = compute_grounded_support(graph, select([candidate(only_e)], signatures))
    assert not support.supported.reached(Atom(A)) and not support.supported.reached(Atom(B))
    support = compute_grounded_support(graph, select([candidate(only_e), candidate(real_a)], signatures))
    assert support.usable(A) and support.usable(B)
    # Withdrawal: a fresh selection from the current sources; nothing from the
    # previous evaluation can seed the next one.
    again = compute_grounded_support(graph, select([candidate(only_e)], signatures))
    assert not again.supported.reached(Atom(A)) and not again.supported.reached(Atom(B))
    with pytest.raises(AssuranceError) as raised:
        compute_grounded_support(graph, support)  # type: ignore[arg-type]
    assert raised.value.code == "FRESH_ANCHOR_SELECTION_REQUIRED"
    with pytest.raises(ContractError):
        Anchor(atom=Atom(A), anchor_id="hand-made", source_group="g", origin=AnchorOrigin.OBSERVATION)


# --------------------------------------------------------------------------- V04
def test_alternate_support():
    a, b, c, k = (signature("pred." + n) for n in "abck")
    A, B, C, K = (key_of(s) for s in (a, b, c, k))
    graph = SupportGraph((rule(K, A, B), rule(K, C, version="rule-c")))
    signatures = {A: a, B: b, C: c, K: k}
    rows = {n: observation("obs-" + n, key, True, source="src-" + n) for n, key in (("a", A), ("b", B), ("c", C))}

    full = compute_grounded_support(graph, select([candidate(r) for r in rows.values()], signatures))
    assert full.usable(K) and full.clean.satisfies_independence(K, required=2)
    without_a = compute_grounded_support(graph, select([candidate(rows["b"]), candidate(rows["c"])], signatures))
    assert without_a.usable(K)
    fired = [w for w in without_a.clean.witnesses_for(Atom(K)) if w.kind is WitnessKind.DERIVED]
    assert [w.rule_version for w in fired] == ["rule-c"]
    assert not without_a.clean.satisfies_independence(K, required=2)
    # A report that literally cites A cannot be repaired by swapping the support.
    from agent_orchestrator.contracts.evidence_state import RecheckOutcome
    from agent_orchestrator.knowledge.justifications import (
        ConsumerUse,
        LineageRecord,
        reevaluate_consumer,
    )

    cite_a = EvidenceRef(kind=EvidenceRefKind.OBSERVATION, id="obs-a", revision=1, content_hash=rows["a"].content_hash)
    report = TypedRef(kind=TypedRefKind.ARTIFACT, id="report-1", revision=1, content_hash=HASH)
    lineage = LineageRecord(report, was_used=(cite_a,), supports_for_use=("rule-a-b",))
    assert lineage.withdrawn_citations(without_a.clean.admitted_signatures) == (cite_a,)
    with pytest.raises(ContractError):
        lineage.rewrite_history_from_supports()
    before = ConsumerUse(report, WitnessPurpose.ACCEPT, K, truth=TruthValue.TRUE, support_signatures=("rule-a-b",),
                         literal_citations=(cite_a,))
    rebound = ConsumerUse(report, WitnessPurpose.ACCEPT, K, truth=TruthValue.TRUE, support_signatures=("rule-c",),
                          literal_citations=(cite_a,), withdrawn_citations=(cite_a,))
    assert reevaluate_consumer(before, rebound) is RecheckOutcome.NEEDS_REVIEW
    # An artifact that never cited A is simply rebound to C: no Worker rerun.
    clean_before = ConsumerUse(report, WitnessPurpose.ACCEPT, K, truth=TruthValue.TRUE, support_signatures=("rule-a-b",))
    clean_after = ConsumerUse(report, WitnessPurpose.ACCEPT, K, truth=TruthValue.TRUE, support_signatures=("rule-c",))
    assert reevaluate_consumer(clean_before, clean_after) is RecheckOutcome.REBOUND_SUPPORT


# --------------------------------------------------------------------------- V05
def test_conflicted_paths():
    a, e, k = signature("pred.a"), signature("pred.e"), signature("pred.k")
    A, E, K = key_of(a), key_of(e), key_of(k)
    graph = SupportGraph((rule(K, A), rule(K, E, version="rule-e")))
    signatures = {A: a, E: e, K: k}
    a_pos = observation("obs-a-pos", A, True, source="src-a")
    a_neg = observation("obs-a-neg", A, False, authoritative=True, source="src-a2")
    e_pos = observation("obs-e", E, True, source="src-e")
    k_neg = observation("obs-k-neg", K, False, authoritative=True, source="src-k")

    conflicted = compute_grounded_support(graph, select([candidate(a_pos), candidate(a_neg)], signatures))
    assert conflicted.supported.truth_for(A) is TruthValue.CONFLICT
    assert conflicted.supported.reached(Atom(K)) and not conflicted.usable(K)  # supported, never executable
    assert not conflicted.clean.reached(Atom(K))
    with_e = compute_grounded_support(graph, select([candidate(a_pos), candidate(a_neg), candidate(e_pos)], signatures))
    assert with_e.usable(K)
    assert [w.rule_version for w in with_e.clean.witnesses_for(Atom(K)) if w.kind is WitnessKind.DERIVED] == ["rule-e"]
    self_conflicted = compute_grounded_support(
        graph, select([candidate(a_pos), candidate(a_neg), candidate(e_pos), candidate(k_neg)], signatures))
    assert self_conflicted.supported.truth_for(K) is TruthValue.CONFLICT and not self_conflicted.usable(K)
    assert self_conflicted.supported.is_complete and self_conflicted.clean.is_complete


# --------------------------------------------------------------------------- V12
def test_independent_provenance_roots():
    a, k = signature("pred.a"), signature("pred.k")
    A, K = key_of(a), key_of(k)
    graph = SupportGraph((rule(K, A),))
    copies = [observation(f"obs-copy-{i}", A, True, source="src-experiment-1") for i in range(10)]
    independent = observation("obs-independent", A, True, source="src-experiment-2")
    support = compute_grounded_support(graph, select([candidate(row) for row in copies], {A: a, K: k}))
    assert support.usable(K)
    assert len(support.clean.witnesses_for(Atom(A))) == 10  # ten witnesses ...
    assert support.clean.independent_source_groups(A) == {"source:src-experiment-1"}  # ... one source
    assert not support.clean.satisfies_independence(A, required=2)
    # A copy with a different format/hash of the same source is still the same root.
    reformatted = observation("obs-reformat", A, True, source="src-experiment-1", observed_at=NOW - 3)
    assert reformatted.content_hash != copies[0].content_hash
    support = compute_grounded_support(graph, select([candidate(copies[0]), candidate(reformatted)], {A: a, K: k}))
    assert not support.clean.satisfies_independence(A, required=2)
    support = compute_grounded_support(graph, select([candidate(copies[0]), candidate(independent)], {A: a, K: k}))
    assert support.clean.satisfies_independence(A, required=2)
    assert not support.clean.satisfies_independence(A, required=3)
    # A derived conclusion inherits the union of its premises' sources: one rule
    # over A is one reason for K, however many roots A has (errs towards refusing).
    assert support.clean.independent_source_groups(K) == {"source:src-experiment-1", "source:src-experiment-2"}
    assert not support.clean.satisfies_independence(K, required=2)


# --------------------------------------------------------------------------- V14 (bounded half)
def test_bounded_restartable_evaluation():
    base = signature("pred.chain")
    keys = [key_of(base, f"n{i}") for i in range(60)]
    graph = SupportGraph(tuple(rule(keys[i], keys[i - 1]) for i in range(1, 60)))
    root = observation("obs-root", keys[0], True)
    support = compute_grounded_support(graph, select([candidate(root)], {k: base for k in keys}))
    assert support.usable(keys[-1]) and support.supported.is_complete
    # One proposition past the closure limit: the evaluator refuses instead of
    # reporting an incomplete pass as "no counter-evidence".
    many = [key_of(base, f"m{i}") for i in range(10_002)]
    wide = SupportGraph(tuple(rule(many[i], many[i - 1]) for i in range(1, len(many))))
    with pytest.raises(AssuranceError) as raised:
        compute_grounded_support(wide, select([candidate(observation("obs-m0", many[0], True))], {k: base for k in many}))
    assert raised.value.code == "EVIDENCE_EVALUATION_INCOMPLETE"
    with pytest.raises(AssuranceError) as raised:
        evaluate(checks=tuple(check_input(f"cb-{i}") for i in range(257)))
    assert raised.value.code == "CHECK_ANCHOR_INVALID"
    # Expired / not-yet-valid anchors are rejected for ACCEPT and only readable
    # as history for CONTEXT; a check with a deadline bounds the certificate.
    stale = observation("obs-stale", keys[0], True, valid_until=NOW - 1)
    assert select([candidate(stale)], {keys[0]: base}).reason_for("obs-stale") is AnchorRejection.EXPIRED
    from agent_orchestrator.contracts.evidence_state import TemporalUse
    historical = select([candidate(stale, temporal_use=TemporalUse.HISTORICAL_AS_OF)], {keys[0]: base},
                        purpose=WitnessPurpose.CONTEXT)
    assert [anchor.historical for anchor in historical.anchors] == [True]
    result = evaluate(checks=(check_input(not_after=NOW + 500),))
    assert result.usable and result.earliest_expiry_ms == NOW + 500
    result = evaluate(checks=(check_input(grade=Grade.UNKNOWN),))
    assert result.truth is TruthValue.UNKNOWN and not result.usable and "checks:1" in result.reasons
    result = evaluate(checks=(check_input(grade=Grade.FAIL),))
    assert result.truth is not TruthValue.TRUE and not result.usable
    not_current = select([candidate(root, validity=Validity.STALE)], {keys[0]: base})
    assert not_current.reason_for("obs-root") is AnchorRejection.NOT_CURRENT


# =========================================================================== Store-level cases
# 2026-10-03（HTN 补齐阶段 A′）：任务经产品那一份部署组装建出（保证通道、执行图建任务时绑定），
# 不跑主循环（确认页没点，任务停在 CREATED），直接读写这一个任务的库；"另一个写入方"是真实的产品
# 写入（另一个连接上，人在确认页确认完成映射）。原来的对照任务（没选保证通道的分层任务）随
# "选不选保证通道"删除；手插观察记录的几段（观察表在桌面产品上没有写入方，裁决①b2）并入"前提 /
# 观测"那条主循环用例，等带观察器的测试世界。
import asyncio  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from agent_orchestrator.assurance.certificates import (  # noqa: E402
    UseCertificate,
    UseIdentity,
    check_certificate_binding,
)
from agent_orchestrator.assurance.evidence import ReadItem  # noqa: E402
from agent_orchestrator.contracts.evidence_state import TemporalUse  # noqa: E402
from agent_orchestrator.governance.permissions import Principal  # noqa: E402
from agent_orchestrator.orchestrator.assurance_validity import AssuranceValidity  # noqa: E402
from agent_orchestrator.storage.assurance_reads import (  # noqa: E402
    AssuranceReader,
    read_complete_evidence_snapshot,
    read_epochs_locked,
    require_epochs_locked,
)
from agent_orchestrator.storage.assurance_store import AssuranceStore  # noqa: E402
from agent_orchestrator.storage.assurance_work import AssuranceWorkStore, WorkTarget  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.storage.store import Store, StoreConflict  # noqa: E402
from agent_orchestrator.testing.fixtures import RoleScriptedProvider  # noqa: E402
from agent_orchestrator.testing.product_world import TENANT, product_world  # noqa: E402

SDK_ROOT = Path(__file__).resolve().parents[4]
PRINCIPAL = Principal("exec-current-user")


def _run(root, checks):
    async def body():
        async with product_world(root / "root", RoleScriptedProvider({}), auto=False) as product:
            created = product.create({"goal": "assured v", "success_criteria": ["file:NOTES.md"],
                                      "idempotency_key": "assured-v"})
            assured = product.store.get_mission(created["mission_id"])
            assert AssuranceStore(product.store).lane(assured.id) == "ASSURANCE_1_1"
            world = SimpleNamespace(store=product.store, commit=product.loop.commit, installed=product.deployment.assurance,
                                    control=product.control, product=product)
            await checks(world, assured)
    asyncio.run(body())


def _epochs(world, mission_id):
    with world.store.read_view() as connection:
        return read_epochs_locked(connection, mission_id)


def _confirm_on_another_connection(world, mission_id):
    """另一个写入方：同一个库的另一条连接上，人在确认页确认了完成映射（真实的产品写入，碰到
    保证通道的有效性屏障）。"""

    from agent_orchestrator.api.operation_completion import OperationCompletionApi
    from agent_orchestrator.orchestrator.commit_service import CommitService

    page = world.control.snapshot(mission_id)["snapshot"]["operation_workspace"]
    ref = page["requirements_ref"]
    command = {"mission_id": mission_id, "command_id": "confirm-on-another-connection",
               "expected_requirements_ref": ref,
               "proposal": {"schema_version": 1, "mission_id": mission_id,
                            "requirements_ref": {key: ref[key] for key in ("id", "revision", "content_hash")},
                            "mode": "CONTENT_ONLY", "content_criterion_ids": [c["id"] for c in page["criteria"]],
                            "effects": []}}
    other = Store.open(world.store.path)
    try:
        OperationCompletionApi(CommitService(other), tenant_id=TENANT,
                               principal=world.product.deployment.principal).approve(command)
    finally:
        other.close()


def _certificate(epochs, **overrides):
    fields = dict(
        mission_id=MISSION, consumer_kind="result", consumer_id="result-1", scope_id=SCOPE,
        principal_id=PRINCIPAL.principal_id, purpose="ACCEPT", truth="TRUE", freshness="CURRENT",
        availability="READABLE", decision="USABLE", coverage="COMPLETE", policy_ref=Pin("policy-1", 1, HASH),
        read_set=(ReadItem("OBJECT", "object-1", HASH), ReadItem("QUERY_SET", "query-1", HASH),
                  ReadItem("ACCESS", "acl-1", HASH), ReadItem("POLICY", "policy-1", HASH)),
        clean_support_refs=(AssuranceRef("review", Pin("rec-1", 1, HASH)),), issued_at_ms=NOW,
        not_after_ms=NOW + 1000, reasons=("fixture",), mission_epoch=epochs[0], environment_epoch=epochs[1],
        clock_generation=epochs[2], root_incarnation_id="root-1",
    )
    fields.update(overrides)
    return UseCertificate(**fields)


def _identity(**overrides):
    fields = dict(mission_id=MISSION, consumer_kind="result", consumer_id="result-1", scope_id=SCOPE,
                  principal_id=PRINCIPAL.principal_id, purpose="ACCEPT", root_incarnation_id="root-1")
    fields.update(overrides)
    return UseIdentity(**fields)


def _bind(certificate, identity=None, *, epochs=(7, 3, 1), clock_state="STABLE", now=NOW + 10,
          access=ReadItem("ACCESS", "acl-1", HASH), policy=ReadItem("POLICY", "policy-1", HASH)):
    check_certificate_binding(certificate, identity=identity or _identity(), mission_epoch=epochs[0],
                              environment_epoch=epochs[1], clock_generation=epochs[2], clock_state=clock_state,
                              now_ms=now, current_access=access, current_policy=policy)


def _refused(call, code):
    with pytest.raises(AssuranceError) as raised:
        call()
    assert raised.value.code == code, (raised.value.code, code)


# --------------------------------------------------------------------------- V06
def test_complete_collection_not_topk(tmp_path):
    async def checks(world, assured):
        reader = AssuranceReader(world.store, tenant_id=TENANT, mission_id=assured.id)
        complete = read_complete_evidence_snapshot(reader, scope_id="scope-v")
        kinds = {json.loads(read.query_key)["query_kind"] for read in complete}
        assert {"observations", "events", "sources"} <= kinds
        assert not {"justification_sets", "support_members"} & kinds
        for read in complete:
            item = read.read_item
            assert item.channel == "QUERY_SET" and item.coverage == "COMPLETE"
            assert json.loads(read.query_key)["selection"] == "MISSION_SUPERSET"
            # The witness carries counts/digests of the set, never the private rows.
            assert not any(row in item.fingerprint for row in read.rows)
        # A bounded read refuses to call itself complete; it never returns a top-K subset as COMPLETE.
        _refused(lambda: read_complete_evidence_snapshot(reader, scope_id="scope-v", maximum_rows=1),
                 "EVIDENCE_EVALUATION_INCOMPLETE")
        _refused(lambda: read_complete_evidence_snapshot(reader, scope_id="scope-v", maximum_bytes=64),
                 "EVIDENCE_EVALUATION_INCOMPLETE")
        _refused(lambda: read_complete_evidence_snapshot(reader, scope_id="scope-v", maximum_rows=100_000),
                 "INTEGER_INVALID")  # the reader's own cap cannot be raised by a caller
        # Another writer's real change moves the mission epoch and the snapshot's witness.
        _confirm_on_another_connection(world, assured.id)
        again = read_complete_evidence_snapshot(reader, scope_id="scope-v")
        assert again[0].epochs.mission == complete[0].epochs.mission + 1

    _run(tmp_path, checks)


def test_snapshot_reads_a_whole_request_manifest_row_over_256kb(tmp_path):
    """2026-09-25 desktop run: an earlier reviewer turn's input manifest (211 KB body,
    275 KB as a row with the body escaped) made every later snapshot of the Mission fail
    JSON_BYTES_LIMIT, so no review could ever be prepared again.  Historical records get
    the larger record cap; the encoding (and every set hash) is unchanged."""

    from agent_orchestrator.assurance.codec import MAX_BYTES, canonical, fingerprint

    async def checks(world, assured):
        body = {"messages": [{"role": "tool", "name": f"t{i}", "content": f"line {i}"} for i in range(4200)]}
        row = {"manifest_hash": fingerprint(body), "origin_mission_id": assured.id,
               "manifest_json": canonical(body), "created_at": 1.0}
        assert len(canonical(row, limit=8 * 1024 * 1024).encode()) > MAX_BYTES  # over the old cap as a row
        # 产品写入口记下这份很大的输入清单（任务的根任务名下）。
        from agent_orchestrator.testing.product_world import USER_GOAL_NAMES

        row["manifest_hash"] = HtnStore(world.store).insert_input_manifest(
            assured.id, USER_GOAL_NAMES.task_prefix + assured.id, body)
        reader = AssuranceReader(world.store, tenant_id=TENANT, mission_id=assured.id)
        complete = read_complete_evidence_snapshot(reader, scope_id="scope-v")
        manifests = next(r for r in complete if json.loads(r.query_key)["query_kind"] == "input_manifests")
        assert any(row["manifest_hash"] in encoded for encoded in manifests.rows)
        # Other records keep the 256 KB cap.
        with pytest.raises(AssuranceError) as raised:
            canonical({"x": "y" * (MAX_BYTES + 1)})
        assert raised.value.code == "JSON_BYTES_LIMIT"

    _run(tmp_path, checks)


# --------------------------------------------------------------------------- V07
def test_a_concurrent_source_change_is_an_insert_barrier(tmp_path):
    """另一个连接上的真实产品写入（确认完成映射）挪动保证通道纪元、记一条证据变化事件；按旧纪元
    拿到的证明在最后一道锁里被拒，按新纪元的照常通过。原来"反证观察"的那一半等带观察器的测试世界。"""

    async def checks(world, assured):
        captured = _epochs(world, assured.id)
        events_before = [e.type for e in world.store.list_events(assured.id)]
        _confirm_on_another_connection(world, assured.id)
        current = _epochs(world, assured.id)
        assert current.mission == captured.mission + 1 and current.environment == captured.environment
        changed = [e for e in world.store.list_events(assured.id) if e.type == "AssuranceEvidenceChanged"]
        assert changed and changed[-1].payload["scope"] == "MISSION" and changed[-1].payload["epoch"] == current.mission
        assert changed[-1].payload["source_table"] == "operation_completion_specs"
        assert len(world.store.list_events(assured.id)) > len(events_before)
        with world.store.read_view() as connection:
            _refused(lambda: require_epochs_locked(connection, assured.id, captured, now_ms=NOW), "RECHECK_REQUIRED")
            require_epochs_locked(connection, assured.id, current, now_ms=int(world.store.now * 1000) + 1)
        old = _certificate((captured.mission, captured.environment, captured.clock_generation))
        _refused(lambda: _bind(old, epochs=(current.mission, current.environment, current.clock_generation)),
                 "RECHECK_REQUIRED")

    _run(tmp_path, checks)


# --------------------------------------------------------------------------- V08
def test_authority_and_expiry():
    good = _certificate((7, 3, 1))
    _bind(good)
    # ACL revoked / policy changed: the current witness is not in the read set.
    _refused(lambda: _bind(good, access=ReadItem("ACCESS", "acl-2", HASH)), "RECHECK_REQUIRED")
    _refused(lambda: _bind(good, policy=ReadItem("POLICY", "policy-2", HASH)), "RECHECK_REQUIRED")
    _refused(lambda: _bind(good, access=ReadItem("POLICY", "policy-1", HASH), policy=ReadItem("ACCESS", "acl-1", HASH)),
             "ACCESS_POLICY_WITNESS_REQUIRED")
    # TTL equal to now is expired; before issue is not yet valid; no timer refresh.
    _refused(lambda: _bind(good, now=NOW + 1000), "CERTIFICATE_EXPIRED")
    _refused(lambda: _bind(good, now=NOW - 1), "CERTIFICATE_EXPIRED")
    _bind(good, now=NOW + 999)
    _refused(lambda: _certificate((7, 3, 1), not_after_ms=NOW), "CERTIFICATE_EXPIRED_AT_ISSUE")
    # Clock rollback or a new clock generation: recheck, never a grant.
    _refused(lambda: _bind(good, clock_state="ROLLBACK"), "CLOCK_RECHECK_REQUIRED")
    _refused(lambda: _bind(good, epochs=(7, 3, 2)), "CLOCK_RECHECK_REQUIRED")
    with pytest.raises(AssuranceError):
        _bind(good, clock_state="WHATEVER")
    # Historical facts stay readable as history; new use is blocked (selector level).
    base = signature("pred.h")
    stale = observation("obs-h", key_of(base), True, valid_until=NOW - 1)
    assert select([candidate(stale)], {key_of(base): base}, purpose=WitnessPurpose.START).reason_for("obs-h") \
        is AnchorRejection.EXPIRED
    assert select([candidate(stale, temporal_use=TemporalUse.HISTORICAL_AS_OF)], {key_of(base): base},
                  purpose=WitnessPurpose.CONTEXT).admitted_ids() == {"obs-h"}
    # MAINTAIN sampling has no continuous guarantee without a monitor interval.
    sampled = observation("obs-s", key_of(base), True)
    assert select([candidate(sampled, temporal_use=TemporalUse.CONTINUOUS)], {key_of(base): base},
                  purpose=WitnessPurpose.MAINTAIN).reason_for("obs-s") is AnchorRejection.NO_CONTINUOUS_GUARANTEE
    assert select([candidate(sampled, temporal_use=TemporalUse.CONTINUOUS, monitor_interval_ms=1000)],
                  {key_of(base): base}, purpose=WitnessPurpose.MAINTAIN).admitted_ids() == {"obs-s"}
    # A non-USABLE certificate never binds, whatever the context.
    _refused(lambda: _bind(_certificate((7, 3, 1), decision="BLOCKED", truth="FALSE")), "CERTIFICATE_NOT_USABLE")
    _refused(lambda: _certificate((7, 3, 1), truth="UNKNOWN"), "CERTIFICATE_NOT_USABLE")
    _refused(lambda: _certificate((7, 3, 1), read_set=(ReadItem("OBJECT", "o", HASH),)), "READSET_INCOMPLETE")


# --------------------------------------------------------------------------- V09
def test_all_consumers_share_validity(tmp_path):
    async def checks(world, assured):
        installed = world.installed
        validity = installed.validity
        # One validity authority per deployment: consumers, factory-side acceptance,
        # root resolution and the read API all resolve the same instance.
        assert world.commit._assurance_validity is validity
        assert installed.api._validity is validity
        assert getattr(installed.review, "validity", validity) is validity
        _refused(lambda: AssuranceValidity(world.commit, tenant_id=TENANT, principal_id=PRINCIPAL.principal_id,
                                           cas=validity.cas, check_adapter=None, authority=validity.authority),
                 "ASSURANCE_VALIDITY_ALREADY_BOUND")
        # A stored summary cannot stand in for the current use: the read verb reports
        # NOT_APPLICABLE for criteria without a certificate.
        page = installed.api.snapshot({"schema_version": 1, "request_id": "v09", "mission_id": assured.id,
                                       "view": "CURRENT", "at_event_seq": None, "cursor": None, "limit": 100})
        assert {item["current_use"] for item in page["items"] if item["kind"] == "CRITERION"} == {"NOT_APPLICABLE"}
        assert world.store.connection.execute(
            "SELECT COUNT(*) FROM assurance_use_certificates WHERE mission_id=?", (assured.id,)).fetchone()[0] == 0
        _refused(lambda: validity.require_current_locked(None, now_ms=NOW), "READ_TRANSACTION_REQUIRED")
        assert validity.candidate_for(assured.id, "no-such-record") is None

    _run(tmp_path, checks)


# --------------------------------------------------------------------------- V10
def test_read_certificate_context():
    good = _certificate((7, 3, 1))
    _bind(good)
    for change in ({"mission_id": "mission-other"}, {"consumer_id": "result-2"}, {"consumer_kind": "artifact"},
                   {"scope_id": "scope-other"}, {"purpose": "START"}, {"principal_id": "someone-else"},
                   {"root_incarnation_id": "root-2"}):
        _refused(lambda change=change: _bind(good, _identity(**change)), "CERTIFICATE_USE_IDENTITY")
    # The certificate's own hash/bytes are not authority: a re-encoded copy with a
    # longer expiry or another identity is a different, still-bound object.
    tampered = UseCertificate.from_json({**good.to_json(), "not_after_ms": NOW + 10_000})
    assert tampered != good
    _refused(lambda: _bind(tampered, now=NOW + 5000, epochs=(8, 3, 1)), "RECHECK_REQUIRED")
    _refused(lambda: _bind(UseCertificate.from_json({**good.to_json(), "purpose": "PLAN"})), "CERTIFICATE_USE_IDENTITY")
    with pytest.raises(AssuranceError):
        UseCertificate.from_json({**good.to_json(), "token": "param"})
    # Same context, fresh read: usable again.
    _bind(good, now=NOW + 1)


# --------------------------------------------------------------------------- V11
def test_invalidation_racing_cache(tmp_path):
    async def checks(world, assured):
        seven = _epochs(world, assured.id)
        stale_projection = _certificate((seven.mission, seven.environment, seven.clock_generation))
        _confirm_on_another_connection(world, assured.id)  # epoch 8 lands while "epoch 7" is being computed
        eight = _epochs(world, assured.id)
        assert eight.mission == seven.mission + 1
        with world.store.read_view() as connection:
            _refused(lambda: require_epochs_locked(connection, assured.id, seven, now_ms=NOW), "RECHECK_REQUIRED")
        _refused(lambda: _bind(stale_projection, epochs=(eight.mission, eight.environment, eight.clock_generation)),
                 "RECHECK_REQUIRED")
        # Nothing of the stale projection reached the current index …
        assert world.store.connection.execute(
            "SELECT COUNT(*) FROM assurance_use_certificates WHERE mission_id=?", (assured.id,)).fetchone()[0] == 0
        # … and the epoch-8 change stays dirty for the consumers until they ingest it.
        change = [e for e in world.store.list_events(assured.id) if e.type == "AssuranceEvidenceChanged"][-1]
        cursors = world.store.connection.execute(
            "SELECT consumer,last_event_seq FROM assurance_event_cursors WHERE mission_id=?", (assured.id,)).fetchall()
        assert cursors and all(row["last_event_seq"] < change.seq for row in cursors)
        # The historical diagnostic (before the change) is still readable as history through the read verb.
        history = world.installed.api.snapshot({"schema_version": 1, "request_id": "v11", "mission_id": assured.id,
                                                "view": "HISTORY", "at_event_seq": change.seq - 1, "cursor": None,
                                                "limit": 100})
        assert history["snapshot_seq"] == change.seq - 1

    _run(tmp_path, checks)


# --------------------------------------------------------------------------- V13
def test_root_quarantine_and_current_read_authority():
    """根闸门接缝（产品同形）：根状态文件丢失时主循环不派发；冷启动只开管理面、没有当前读权限时拒绝一切
    读取；闸门层契约下本租户读得到，别的租户 / 权限过期按名拒绝。离线备份与受管恢复已删（A″）。"""
    seam = SDK_ROOT / "scripts/assurance_seams/root-gate-seam.py"
    completed = subprocess.run([sys.executable, str(seam)], capture_output=True, text=True, timeout=600,
                               cwd=str(SDK_ROOT))
    assert completed.returncode == 0, completed.stderr[-4000:]
    summary = json.loads(completed.stdout.strip().splitlines()[-1])
    report = json.loads(Path(summary["evidence"]).read_text())
    assert report["provider_calls"] == 0
    assert set(report["results"]) == {
        "missing_root_state_refuses_the_main_loop_before_any_model_call",
        "quarantine_without_current_authority_refuses_every_read",
        "native_root_read_checks_tenant_and_expiry",
    } and all(report["results"].values())


# --------------------------------------------------------------------------- V14 (replay half)
def test_bounded_restartable_replay(tmp_path):
    async def checks(world, assured):
        work = AssuranceWorkStore(world.store)
        world.control.comment(assured.id, "看一下进度")  # a real durable event on the Mission moves its head
        target = WorkTarget("work-v14", HASH)
        cursor = world.store.connection.execute(
            "SELECT row_version FROM assurance_event_cursors WHERE mission_id=? AND consumer='VALIDITY'",
            (assured.id,)).fetchone()
        now = int(world.store.now * 1000)
        head = work.ingest(assured.id, "VALIDITY", expected_version=cursor["row_version"],
                           classify=lambda event, consumer: (target,), now_ms=now)
        pending = lambda: world.store.connection.execute(  # noqa: E731
            "SELECT work_key,target_epoch,row_version,state FROM assurance_pending_work "
            "WHERE mission_id=? AND consumer='VALIDITY'", (assured.id,)).fetchall()
        first = [tuple(row) for row in pending()]
        [(key, epoch, version, state)] = first  # one job however many change events the page carried
        assert (key, epoch, state) == ("work-v14", head, "PENDING")
        # Replaying the same change events with the stale cursor version is a conflict, not a second job.
        with pytest.raises(StoreConflict):
            work.ingest(assured.id, "VALIDITY", expected_version=cursor["row_version"],
                        classify=lambda event, consumer: (target,), now_ms=now)
        assert work.ingest(assured.id, "VALIDITY", expected_version=cursor["row_version"] + 1,
                           classify=lambda event, consumer: (target,), now_ms=now) == head
        assert [tuple(row) for row in pending()] == first
        # A later event with the same target fingerprint moves the job, never duplicates it.
        world.control.comment(assured.id, "再看一下")
        later = work.ingest(assured.id, "VALIDITY", expected_version=cursor["row_version"] + 1,
                            classify=lambda event, consumer: (target,), now_ms=now)
        assert later > head
        assert [tuple(row) for row in pending()] == [("work-v14", later, version + 1, "PENDING")]

    _run(tmp_path, checks)
