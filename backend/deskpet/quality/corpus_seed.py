"""Public corpus setup mutation, never a Provider input/authorization adapter.

Accepts only isolated, reviewed setup records and real Host admitted source pairs.
No Case/gold, recent-history construction, SQL writes or synthetic authority.
"""
from dataclasses import dataclass
from hashlib import sha256

import simple_harness as h
from simple_harness_memory import MemoryScope
from deskpet.memory.analysis_proposal import admitted_item, derive_span


@dataclass(frozen=True)
class SemanticSeed:
    label: str
    source_text: str
    predicate: str
    object_value: str
    qualifiers: tuple[str, ...] = ()


@dataclass(frozen=True)
class SeedReceipt:
    label: str
    source_hash: str
    plan: object
    apply_result: object
    committed_view: object


# Explicit mapping from setup only. These are fixture records, not a query hint
# or a selected recall type. Other cases remain unimplemented, never discarded.
C01_10_SETUP = 'A：本人的待办清单按截止时间升序，没日期的放最后。'


def c01_10_seed(setup_source_text: str) -> SemanticSeed:
    if type(setup_source_text) is not str or setup_source_text != C01_10_SETUP:
        raise ValueError('corpus_setup_source_changed')
    return SemanticSeed('A', '本人的待办清单按截止时间升序，没日期的放最后。',
        'todo_sort_order', '按截止时间升序，没日期的放最后', ('待办清单',))


async def apply_semantic_seed(*, manager, principal, seed: SemanticSeed,
                              envelope, receipt, plan_id: str, base_revision: int) -> SeedReceipt:
    if type(seed) is not SemanticSeed:
        raise TypeError('SemanticSeed required; do not pass Case or gold')
    if type(base_revision) is not int or base_revision < 1:
        raise ValueError('corpus_actual_base_revision_required')
    item = admitted_item(envelope, receipt)
    if item.text != seed.source_text or envelope.subject != principal.actor_id:
        raise ValueError('corpus_admitted_source_differs')
    # This public call uses the configured real evidence authority. A fabricated
    # receipt or uncommitted Host evidence is rejected by the SDK, not trusted here.
    await manager.register_principal_owner(principal, MemoryScope.personal(principal.actor_id))
    await manager.ingest_committed_evidence(envelope, receipt)
    span = derive_span(item, seed.source_text, span_id='corpus-source')
    operation = h.MemoryMutationOperation(
        operation_id='seed-'+seed.label, kind=h.MemoryMutationKind.CREATE,
        memory_type=h.LongTermMemoryType.SEMANTIC,
        payload=h.SemanticMemoryPayload('user:self', seed.predicate, seed.object_value, seed.qualifiers),
        target=None, depends_on_operation_ids=(), lifecycle_state=h.SemanticLifecycleState.ACTIVE,
        epistemic_status=h.EpistemicStatus.EXPLICIT_USER, conflict_status=h.ConflictStatus.UNCONTESTED,
        verification_state=h.VerificationState.SOURCE_BOUND,
        valid_time_interval=h.ValidTimeInterval(None,None),
        proposed_privacy_class=h.PrivacyClass.PERSONAL,
        proposed_information_attributes=(h.InformationAttribute.PREFERENCE,),
        evidence_spans=(span,), reason_code='explicit_user_assertion')
    plan = h.MemoryMutationPlan(plan_id, envelope.run_id, 'corpus-setup-'+plan_id,
        principal.actor_id, base_revision, h.MemoryMutationPlanOutcome.MUTATE,
        (operation,), envelope.disclosure_context,
        (h.EvidenceRef(envelope.evidence_id,envelope.envelope_hash,1),),plan_id)
    applied = await manager.apply_memory_mutation_plan(principal=principal,
        scope=MemoryScope.personal(principal.actor_id),plan=plan)
    if applied.outcome is not h.MemoryMutationApplyOutcome.COMMITTED or applied.receipt_ref is None:
        raise ValueError('corpus_seed_not_committed')
    view = await manager.get_memory_mutation_receipt_view(principal=principal,receipt_ref=applied.receipt_ref)
    if (view.plan_id != plan.plan_id or view.plan_hash != plan.plan_hash
            or view.receipt_id != applied.receipt_ref.receipt_id
            or view.receipt_hash != applied.receipt_ref.receipt_hash or len(view.operations)!=1):
        raise ValueError('corpus_seed_receipt_differs')
    committed = view.operations[0]
    if (committed.operation_id != operation.operation_id or committed.memory_type != 'semantic'
            or committed.revision != 1 or committed.epistemic_status != 'explicit_user'
            or committed.evidence_ids != (envelope.evidence_id,)):
        raise ValueError('corpus_seed_operation_differs')
    # This is an immutable commit readback, not current visibility/lifecycle proof.
    return SeedReceipt(seed.label,sha256(seed.source_text.encode()).hexdigest(),plan,applied,view)


@dataclass(frozen=True)
class GraphSeed:
    """Explicit fixture instructions, separate from corpus gold and model output."""
    source_text: str
    preference_quote: str
    procedure_quote: str
    relation_quote: str
    predicate: str
    preference: str
    procedure_title: str
    conditions: tuple[str, ...]
    steps: tuple[str, ...]


async def apply_graph_seed(*, manager, principal, seed: GraphSeed, envelope, receipt,
                           plan_id: str, base_revision: int):
    from dataclasses import replace
    if type(seed) is not GraphSeed:
        raise TypeError('GraphSeed required')
    item = admitted_item(envelope, receipt)
    if item.text != seed.source_text or envelope.subject != principal.actor_id:
        raise ValueError('corpus_admitted_source_differs')
    # Quotes must each be actual spans of the already committed Host source.
    spans = tuple(derive_span(item, quote, span_id='graph-'+name) for name,quote in
        (('preference',seed.preference_quote),('procedure',seed.procedure_quote),('relation',seed.relation_quote)))
    claim=h.MemoryMutationOperation(operation_id='claim',kind=h.MemoryMutationKind.CREATE,
        memory_type=h.LongTermMemoryType.SEMANTIC,
        payload=h.SemanticMemoryPayload('user:self',seed.predicate,seed.preference,()),
        target=None,depends_on_operation_ids=(),lifecycle_state=h.SemanticLifecycleState.ACTIVE,
        epistemic_status=h.EpistemicStatus.EXPLICIT_USER,conflict_status=h.ConflictStatus.UNCONTESTED,
        verification_state=h.VerificationState.SOURCE_BOUND,valid_time_interval=h.ValidTimeInterval(None,None),
        proposed_privacy_class=h.PrivacyClass.PERSONAL,
        proposed_information_attributes=(h.InformationAttribute.PREFERENCE,),
        evidence_spans=(spans[0],),reason_code='explicit_user_assertion')
    procedure=replace(claim,operation_id='procedure',memory_type=h.LongTermMemoryType.PROCEDURE,
        payload=h.ProcedureMemoryPayload(seed.procedure_title,seed.conditions,seed.steps,h.ProcedureRiskLevel.LOW),
        lifecycle_state=h.ProcedureLifecycleState.ACTIVE,evidence_spans=(spans[1],))
    relation=replace(claim,operation_id='relation',depends_on_operation_ids=('claim','procedure'),
        payload=h.SemanticRelationMemoryPayload(h.SemanticRelationKind.APPLIES_TO,
            h.CreatedByOperationTarget('claim'),h.CreatedByOperationTarget('procedure')),
        evidence_spans=(spans[2],))
    plan=h.MemoryMutationPlan(plan_id,envelope.run_id,'fixture-graph-'+plan_id,principal.actor_id,
        base_revision,h.MemoryMutationPlanOutcome.MUTATE,(claim,procedure,relation),envelope.disclosure_context,
        (h.EvidenceRef(envelope.evidence_id,envelope.envelope_hash,1),),plan_id)
    await manager.register_principal_owner(principal,MemoryScope.personal(principal.actor_id))
    await manager.ingest_committed_evidence(envelope, receipt)
    applied=await manager.apply_memory_mutation_plan(principal=principal,
        scope=MemoryScope.personal(principal.actor_id),plan=plan)
    if applied.outcome is not h.MemoryMutationApplyOutcome.COMMITTED or applied.receipt_ref is None:
        raise ValueError('corpus_graph_seed_not_committed')
    view=await manager.get_memory_mutation_receipt_view(principal=principal,receipt_ref=applied.receipt_ref)
    if view.plan_hash!=plan.plan_hash or view.plan_id!=plan_id or view.apply_mode!='strict_atomic':
        raise ValueError('corpus_graph_receipt_differs')
    operations={v.operation_id:v for v in view.operations}
    if (set(operations)!={'claim','procedure','relation'}
            or [(operations[k].memory_type,operations[k].semantic_kind) for k in ('claim','procedure','relation')]
                != [('semantic','claim'),('procedure',None),('semantic','relation')]
            or any(v.revision!=1 or v.evidence_ids!=(envelope.evidence_id,) for v in operations.values())):
        raise ValueError('corpus_graph_operation_differs')
    # Display-only public graph readback, never Agent recall/Provider input.
    graph=await manager.get_twin_graph_view(principal=principal)
    expected={operations[k].memory_id for k in ('claim','procedure')}
    if {n.memory_id for n in graph.nodes}!=expected or len(graph.edges)!=1:
        raise ValueError('corpus_graph_not_two_nodes_one_edge')
    edge=graph.edges[0]
    if (edge.relation_kind!='applies_to'
            or edge.source_node_id!=operations['claim'].memory_id+'@1'
            or edge.target_node_id!=operations['procedure'].memory_id+'@1'):
        raise ValueError('corpus_graph_edge_differs')
    return plan, applied, view, graph
