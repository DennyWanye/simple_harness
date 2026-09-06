"""Five exact C08 derived carriers over the existing retained phase.

Association records are source-bound semantic claims, not invented entity
edges. The reminder alone uses the real public Prospective registration path.
No current question, oracle, private SDK SQL or constructed receipt.
"""
from dataclasses import dataclass, replace
from datetime import datetime
from types import MappingProxyType
from zoneinfo import ZoneInfo

import simple_harness as h

from deskpet.task_scope.protocol import canonical_hash


@dataclass(frozen=True)
class DerivedCarrier:
    kind: str
    predicate: str
    value: str
    user: str
    assistant: str
    derived_predicate: str
    fields: tuple[tuple[str, str], ...] = ()


# Setup realizations, not purported verbatim historical corpus conversations.
# All relationship components below are explicitly restated in the real USER.
# No external contact ID, address-book authority or entity alias is synthesized.
CARRIERS = MappingProxyType({
    'C08-02': DerivedCarrier('retained-contact-record', 'telephone', '测试内线47',
        '我的联系电话是测试内线47。请用这个号码整理一条联络资料。',
        '联络资料\n电话：测试内线47。', 'contact_record', (('telephone', '测试内线47'),)),
    'C08-04': DerivedCarrier('retained-address-term-association', 'family_address_term', '舅舅阿青',
        '我把这位家庭成员称为舅舅阿青，亲属称谓是舅舅，名字是阿青。请整理这条称呼关联。',
        '称呼关联\n称呼：舅舅阿青；亲属称谓：舅舅；名字：阿青。',
        'address_term_association', (('address_term', '舅舅阿青'), ('kinship', '舅舅'), ('name', '阿青'))),
    'C08-09': DerivedCarrier('retained-amount-reference', 'monthly_budget', '800元',
        '我的月预算是800元。请在预算备注中引用这个月预算金额。',
        '预算备注\n引用月预算：800元。',
        'budget_amount_reference', (('reference_kind', 'monthly_budget'), ('amount', '800元'))),
    'C08-13': DerivedCarrier('retained-registered-reminder', 'anniversary', '4月17日',
        '纪念日是4月17日。请在下一个4月17日中午12点提醒我纪念日。',
        '纪念日提醒记录\n日期：4月17日；下次当日12:00提醒纪念日。', 'anniversary_reminder'),
    'C08-19': DerivedCarrier('retained-recipient-association', 'past_conversation_recipient', '邻居小松',
        '旧对话关联的收件人是邻居小松，关联关系是邻居。请整理这条收件人关联资料。',
        '收件人关联资料\n收件人：邻居小松；关系：邻居。',
        'recipient_association', (('recipient', '邻居小松'), ('relationship', '邻居'))),
})


def extra_payload(batch):
    spec = CARRIERS[batch.case_id]
    if batch.case_id != 'C08-13':
        return h.SemanticMemoryPayload('user:self', spec.derived_predicate, dict(spec.fields), ())
    # Original setup supplies month/day but no year/time. This explicit fixture
    # default is the NEXT Apr 17 at noon Asia/Shanghai, a single occurrence.
    # It is not an annual recurrence rule or a date copied from current/gold.
    now = datetime.fromtimestamp(batch.scenario_time, ZoneInfo('Asia/Shanghai'))
    due = datetime(now.year, 4, 17, 12, tzinfo=now.tzinfo)
    if due <= now:
        due = due.replace(year=now.year + 1)
    return h.ProspectiveMemoryPayload('提醒纪念日（4月17日）',
        h.ProspectiveTimeTrigger(due.timestamp(), 'Asia/Shanghai'))


def extra_manifest(batch):
    return dict(derived_payload=extra_payload(batch).to_json(),
        realization=('next-Apr17/noon/Asia_Shanghai/single-occurrence'
            if batch.case_id == 'C08-13' else 'independent-source-bound-structured-claim'))


def expand_operations(batch, original):
    if batch.case_id not in CARRIERS:
        return (original,)
    prospective = batch.case_id == 'C08-13'
    derived = replace(original, operation_id='P' if prospective else 'D',
        memory_type=h.LongTermMemoryType.PROSPECTIVE if prospective else h.LongTermMemoryType.SEMANTIC,
        payload=extra_payload(batch),
        lifecycle_state=h.ProspectiveLifecycleState.PENDING if prospective else h.SemanticLifecycleState.ACTIVE)
    return (original, derived)


def verify_extra_views(batch, plan, view, graph, user_evidence_id):
    """Exact public receipt + nonempty ordinary view, before forgetting."""
    label = 'P' if batch.case_id == 'C08-13' else 'D'
    expected = {op.operation_id: op for op in plan.operations}
    actual = {op.operation_id: op for op in view.operations}
    if (set(actual) != {'A', label} or set(expected) != set(actual)
            or len(view.operations) != 2 or view.plan_hash != plan.plan_hash
            or view.apply_mode != 'strict_atomic' or len(graph.nodes) != 2 or graph.edges):
        raise ValueError('c08_derived_exact_nonempty_carriers_required')
    for name, op in actual.items():
        payload_hash = canonical_hash(expected[name].payload.to_json())
        found = [node for node in graph.nodes if node.memory_id == op.memory_id]
        if (op.evidence_ids != (user_evidence_id,) or op.revision != 1
                or op.content_hash != payload_hash or len(found) != 1
                or found[0].revision != 1 or found[0].content_hash != payload_hash
                or found[0].lifecycle_state != expected[name].lifecycle_state.value
                or found[0].memory_type != expected[name].memory_type.value):
            raise ValueError('c08_derived_public_source_or_payload_differs')
    return actual


async def fixture_options(*, path, principal, batch):
    if batch.case_id != 'C08-13':
        return {}
    from deskpet.memory.s5c_terminal_schema import initialize_s5c_terminal_state_db
    from deskpet.memory.s5c_store import HostProspectiveSignalAuthority
    await initialize_s5c_terminal_state_db(path)
    return {'prospective_signal_authority': HostProspectiveSignalAuthority(path, principal)}


async def register_reminder(*, path, manager, principal, batch, labels, mutation_receipt_ref):
    if batch.case_id != 'C08-13':
        return None
    from deskpet.memory.s5c_store import S5cStore
    from deskpet.memory.s5c_consumer import ProspectiveRegistrationConsumer
    from deskpet.memory.prospective_registration_source import PublicRegistrationAuthoritySource
    from simple_harness_memory import MutationTargetSource
    store = S5cStore(path, principal)
    consumer = ProspectiveRegistrationConsumer(store, manager,
        PublicRegistrationAuthoritySource(store=store, memory=manager, clock=lambda: batch.scenario_time))
    await consumer.run_once()
    target = labels['P']
    accepted = await store.accepted_registration(memory_id=target.memory_id, revision=target.revision)
    if (accepted is None or accepted.result is None or accepted.result.outcome.value != 'acknowledged'
            or accepted.result.reason_code != 'prospective_registration_acknowledged'
            or accepted.authority.intent.trigger != extra_payload(batch).trigger):
        raise ValueError('c08_reminder_actual_registration_ack_required')
    source = await manager.read_prospective_outbox_source_v2(principal=principal,
        outbox_id=accepted.entry.outbox_id, payload_hash=accepted.entry.payload_hash)
    if (source.target_memory_id != target.memory_id or source.target_revision != target.revision
            or type(source.target_source) is not MutationTargetSource
            or source.target_source.mutation_receipt_ref != mutation_receipt_ref
            or source.target_source.operation_id != 'P' or source.command != 'registration'
            or source.target_lifecycle_state is not h.ProspectiveLifecycleState.PENDING):
        raise ValueError('c08_reminder_actual_mutation_source_differs')
    ordinary = await manager.get_twin_graph_view(principal=principal)
    if {node.memory_id for node in ordinary.nodes} != {labels['A'].memory_id, target.memory_id}:
        raise ValueError('c08_reminder_must_remain_ordinary_visible_before_forget')
    return dict(registration=accepted, source=source, timer_fired=False,
        future_delivery_exercised=False, registration_cancelled=False)


async def verify_reminder_reopen(*, path, manager, principal, seed):
    reminder = seed.get('reminder_carrier')
    if reminder is None:
        return None
    from deskpet.memory.s5c_store import S5cStore
    original = reminder['registration']
    source = reminder['source']
    accepted = await S5cStore(path, principal).accepted_registration(
        memory_id=source.target_memory_id, revision=source.target_revision)
    reread = await manager.read_prospective_outbox_source_v2(principal=principal,
        outbox_id=original.entry.outbox_id, payload_hash=original.entry.payload_hash)
    if accepted != original or reread.source_hash != source.source_hash:
        raise ValueError('c08_reminder_persistent_registration_source_changed')
    # These historical authority readers do NOT prove ordinary visibility.
    # The caller separately requires graph empty + source history suppressed.
    return dict(registration=accepted, source=reread, historical_authority_only=True)
