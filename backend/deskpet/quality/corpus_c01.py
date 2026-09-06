"""Reviewed setup-only mapping. No gold/runtime-input parsing or Provider output."""
from dataclasses import dataclass
from hashlib import sha256
from datetime import datetime
import simple_harness as h

# Pinned setup fields only, read independently of class labels and gold.
SETUPS = {'C01-01': ('A：本人明确要求日常更新最多三条、每条一句；B：曾阅读长文的单次episode。',
            'a46bcccbbf7ff64d1ef423fd5a44731b1de0892505cc965114bbd0d9f582c3e6'),
 'C01-02': ('A：本人要求个人技术答复用简体中文；B：给海外客户的英语特例不适用。',
            'eadb70840b09b70154d6b18dfe02a1b96752520a9c2fe1eedc8812f3306770f6'),
 'C01-03': ('A：本人要求非整数测量结果保留两位小数；B：财务报表保留整数的项目特例。',
            '51ef9fa76cff28485ee8f0a25dc030da1d9bd48a3f35bc2d79416f74dcc9fd34'),
 'C01-04': ('A：本人约定笔记日期用YYYY-MM-DD；B：旧活动海报曾用中文日期。',
            '41b970b72c5dfc1db31cedc0f08e8d0b668c0bad67398bd5b31d5e7134d93896'),
 'C01-05': ('A：内部进度用三条清单；B：客户报价用表格；均为本人明确语义约定。',
            '79332f7dca2ab95fa6350974b31b29fac035792ee408f3811be8e20491c019e7'),
 'C01-06': ('A：本人明确把称呼从老师改为小周，当前head为小周；B为superseded旧称呼。',
            '1ccbc6d8c455e6c4a60ac53ab1bdd98c14baed2c57fd4dbce59baa617640236c'),
 'C01-07': ('A：本人要求回复不要以“还有什么可以帮你”收尾；B：一般礼貌表达不受禁止。',
            'b78f3a086e0190466aeb92c91f5a2979df9cb7cabbedc7f695c809c802a39ef6'),
 'C01-08': ('A：通常报告要表格，但手机阅读时改短列表；本人明确条件规则。',
            '728dddc99a2871a0e83fcf1b9df3d3860e72f229cd568a9b51ba4240c7c87cae'),
 'C01-09': ('A：本人日常距离用公里；B：一次活动记录写过英里，无偏好变更。',
            'ce5f692f67f99d8dfc9476cd15c6fc962617b5b7ec1c646cd8a3b233ebdaa2f6'),
 'C01-10': ('A：本人的待办清单按截止时间升序，没日期的放最后。',
            '602efef3c751378f9449ba85cb37f644c7e604affb1df17641fc2596648522ed'),
 'C01-11': ('A：本人草稿文件命名约定为日期_主题；无步骤程序。',
            '4472b35fd4b2b00e1ed96342c0eb4e8a233e143253ffb4b68f7a405ca18cd2d3'),
 'C01-12': ('A：本人工作联系时段09:00–17:00；B：同事夜班20:00–04:00。',
            '3fcaa27982c2b03a36dc68ff149291e394b6bdcc4fcf7bd82f7d1735048c9eae'),
 'C01-13': ('A：本人词表把checkpoint固定译成检查点；B：另一项目译法为快照。',
            'a301e6251683dba0f07fb678867a7af37260518dbf3ae369e0acea25ca8c9a6d'),
 'C01-14': ('A：本人日志默认时区Asia/Shanghai；B：服务器位于UTC不代表本人偏好。',
            '1d0c87a9eec7cf1bd164fe38912f81fe8a2a4daeddc7058743e5c5081413c041'),
 'C01-15': ('A：本人比较采购金额统一人民币；B：原始报价可用美元。',
            'a84bc5f9b3f7704dc2f55ce2d3c2b1535da324b33fc981fd01c5689fd4c90b82'),
 'C01-16': ('A：本人摘要应先结论；B：本人会议通常下午；同一用户不同属性。',
            'eca2f1378bcb8a4af3272350df301b856c65dbf3af28a9aa1676508ad892a492'),
 'C01-17': ('A：本人邮件草稿最多两段；B：有一条未到期提醒，与拟稿无关。',
            '947f1884f8cd81daa4e08d32b749b8c87bf8483463045ae95d669d6c3986d3ec'),
 'C01-18': ('A：聊天答复允许项目符号，短信只用纯文本；均为稳定渠道规则。',
            '650571684a3da63bfd2195e0d329000e72e7411966762d746be65e79fd1e85ef'),
 'C01-19': ('A：本人明确规定解释先说结论再说理由；B：一次讨论先聊背景。',
            'eaa989c0576ce488959005d148df5d1e302b51f617086cbe3bbcaf6c9d5426f4'),
 'C01-20': ('A：个人日报最多三点；B：个人周报最多五点，两者当前有效。',
            'a62543d9aada7b3bf2fa4942e12f69f212cc8885df34d181fba6d1aec52928e6')}

# label, memory type, subject, predicate/title, value, qualifiers/conditions.
# Every distractor is retained. No required_type/expected-answer metadata here.
SPECS = {
'C01-01': (('A','semantic','user:self','daily_update_format','最多三条、每条一句',('日常更新',)), ('B','episode','user:self','阅读长文','曾阅读长文',())),
'C01-02': (('A','semantic','user:self','technical_answer_language','简体中文',('个人技术答复',)), ('B','semantic','user:self','client_answer_language','英语',('海外客户',))),
'C01-03': (('A','semantic','user:self','measurement_precision','两位小数',('非整数测量结果',)), ('B','semantic','user:self','financial_precision','整数',('财务报表','项目特例'))),
'C01-04': (('A','semantic','user:self','note_date_format','YYYY-MM-DD',('笔记',)), ('B','episode','user:self','旧活动海报日期','曾用中文日期',())),
'C01-05': (('A','semantic','user:self','internal_progress_format','三条清单',('内部进度',)), ('B','semantic','user:self','client_quote_format','表格',('客户报价',))),
'C01-07': (('A','semantic','user:self','reply_closing','不要以“还有什么可以帮你”收尾',()), ('B','semantic','user:self','ordinary_politeness','一般礼貌表达不受禁止',())),
'C01-08': (('A','semantic','user:self','report_format','通常报告要表格，但手机阅读时改短列表',('报告','手机阅读例外')) ,),
'C01-09': (('A','semantic','user:self','distance_unit','公里',('日常距离',)), ('B','episode','user:self','一次活动距离记录','写过英里，无偏好变更',())),
'C01-10': (('A','semantic','user:self','todo_sort_order','按截止时间升序，没日期的放最后',('待办清单',)),),
'C01-11': (('A','semantic','user:self','draft_filename','日期_主题',('草稿文件','无步骤程序')),),
'C01-12': (('A','semantic','user:self','work_contact_hours','09:00–17:00',()), ('B','semantic','person:colleague','night_shift_hours','20:00–04:00',('同事',))),
'C01-13': (('A','semantic','user:self','checkpoint_translation','检查点',('本人词表',)), ('B','semantic','user:self','checkpoint_translation','快照',('另一项目',))),
'C01-14': (('A','semantic','user:self','log_timezone','Asia/Shanghai',('本人日志',)), ('B','semantic','server:default','server_timezone','UTC',('不代表本人偏好',))),
'C01-15': (('A','semantic','user:self','purchase_currency','人民币',('比较采购金额',)), ('B','semantic','user:self','quote_currency','可用美元',('原始报价',))),
'C01-16': (('A','semantic','user:self','summary_order','先结论',('摘要',)), ('B','semantic','user:self','meeting_time','通常下午',('会议',))),
'C01-17': (('A','semantic','user:self','email_draft_length','最多两段',('邮件草稿',)), ('B','prospective','user:self','未到期提醒','未到期提醒',())),
'C01-18': (('A','semantic','user:self','channel_format','聊天答复允许项目符号，短信只用纯文本',('聊天','短信')),),
'C01-19': (('A','semantic','user:self','explanation_order','先说结论再说理由',()), ('B','episode','user:self','一次讨论','先聊背景',())),
'C01-20': (('A','semantic','user:self','daily_report_length','最多三点',('个人日报',)), ('B','semantic','user:self','weekly_report_length','最多五点',('个人周报',))),
}


@dataclass(frozen=True)
class SetupBatch:
    case_id: str
    setup_text: str
    setup_hash: str
    scenario_time: float
    specs: tuple
    fixture_defaults: tuple[str,...]


def compile_setup(case_id, setup_text, *, scenario_clock):
    from deskpet.quality.corpus_c02 import SETUPS as C02_SETUPS, SPECS as C02_SPECS
    setups, all_specs = (C02_SETUPS, C02_SPECS) if type(case_id) is str and case_id.startswith('C02-') else (SETUPS, SPECS)
    if type(case_id) is not str or case_id not in setups:
        raise ValueError('corpus_unknown_setup')
    text,digest=setups[case_id]
    if type(setup_text) is not str or setup_text!=text or sha256(setup_text.encode()).hexdigest()!=digest:
        raise ValueError('corpus_setup_source_changed')
    if case_id not in all_specs:
        raise ValueError('corpus_revision_setup_requires_public_action_authority')
    clock=datetime.fromisoformat(scenario_clock)
    if clock.tzinfo is None:raise ValueError('corpus_clock_offset_required')
    specs=all_specs[case_id]
    defaults=tuple(sorted({ 'undated_past_episode=clock-24h' if s[1]=='episode' else
        'undated_unexpired_prospective=clock+24h' for s in specs if s[1] in ('episode','prospective')}))
    return SetupBatch(case_id,text,digest,clock.timestamp(),specs,defaults)


def payload(spec, clock):
    _,kind,subject,predicate,value,qualifiers=spec
    if kind=='semantic':return h.SemanticMemoryPayload(subject,predicate,value,qualifiers)
    if kind=='episode':return h.EpisodeMemoryPayload(predicate,(subject,),(),(value,),(),(),clock-86400,None,None)
    if kind=='prospective':return h.ProspectiveMemoryPayload(value,h.ProspectiveTimeTrigger(clock+86400,'Asia/Shanghai'))
    raise ValueError('corpus_seed_type_unimplemented')


async def apply_setup(*, manager, principal, batch, envelope, receipt, base_revision=1, inference_path=None, inference_host_run_id=None):
    from simple_harness_memory import MemoryScope
    from deskpet.memory.analysis_proposal import admitted_item, derive_span
    from deskpet.task_scope.protocol import canonical_hash
    if type(batch) is not SetupBatch:
        raise TypeError('SetupBatch required')
    expected=compile_setup(batch.case_id,batch.setup_text,
        scenario_clock=datetime.fromtimestamp(batch.scenario_time,__import__('datetime').timezone.utc).isoformat())
    if batch!=expected:raise ValueError('corpus_setup_manifest_differs')
    item=admitted_item(envelope,receipt)
    if item.text!=batch.setup_text or envelope.subject!=principal.actor_id:
        raise ValueError('corpus_setup_admitted_source_differs')
    await manager.register_principal_owner(principal,MemoryScope.personal(principal.actor_id))
    await manager.ingest_committed_evidence(envelope,receipt)
    sources=[h.EvidenceRef(envelope.evidence_id,envelope.envelope_hash,1)]
    spans={}
    if batch.case_id=='C02-19':
        if inference_path is None or inference_host_run_id is None:
            raise ValueError('corpus_inference_actual_source_required')
        from deskpet.quality.corpus_inference import inference_source
        extra,extra_receipt,span,terminal=await inference_source(path=inference_path,
            subject=principal.actor_id,batch=batch,host_run_id=inference_host_run_id,quote='偏好云端',
            original_envelope=envelope,original_receipt=receipt)
        # Public full admission is required by cognitive mutation; source-only
        # admission does not satisfy this prerequisite and is not mixed in.
        await manager.ingest_committed_evidence(extra,extra_receipt)
        await manager.admit_evidence_source(principal=principal,envelope=terminal[0],receipt=terminal[1])
        spans['B']=span
        sources.append(h.EvidenceRef(extra.evidence_id,extra.envelope_hash,len(sources)+1))
    elif inference_path is not None or inference_host_run_id is not None:
        raise ValueError('corpus_unexpected_inference_source')
    operations=[]
    for spec in batch.specs:
        label,kind,*_=spec
        span=spans.get(label) or derive_span(item,batch.setup_text,span_id='setup-'+label)
        lifecycle={'semantic':h.SemanticLifecycleState.ACTIVE,'episode':h.EpisodeLifecycleState.ACTIVE,
            'prospective':h.ProspectiveLifecycleState.PENDING}[kind]
        from deskpet.quality.corpus_c02 import CLASSIFICATIONS
        classification=CLASSIFICATIONS.get((batch.case_id,label))
        epistemic,verification,reason=h.EpistemicStatus.EXPLICIT_USER,h.VerificationState.SOURCE_BOUND,'explicit_user_assertion'
        if classification is not None:
            state,knowledge,verified,reason=classification
            lifecycle=h.SemanticLifecycleState(state)
            epistemic,verification=h.EpistemicStatus(knowledge),h.VerificationState(verified)
        operations.append(h.MemoryMutationOperation(operation_id=label,kind=h.MemoryMutationKind.CREATE,
            memory_type=h.LongTermMemoryType(kind),payload=payload(spec,batch.scenario_time),
            target=None,depends_on_operation_ids=(),lifecycle_state=lifecycle,
            epistemic_status=epistemic,conflict_status=h.ConflictStatus.UNCONTESTED,
            verification_state=verification,valid_time_interval=h.ValidTimeInterval(None,None),
            proposed_privacy_class=h.PrivacyClass.PERSONAL,proposed_information_attributes=(),
            evidence_spans=(span,),reason_code=reason))
    identity='corpus-'+canonical_hash([batch.case_id,batch.setup_hash,envelope.evidence_id,batch.scenario_time])
    plan=h.MemoryMutationPlan(identity,envelope.run_id,identity,principal.actor_id,base_revision,
        h.MemoryMutationPlanOutcome.MUTATE,tuple(operations),envelope.disclosure_context,
        tuple(sources),identity)
    applied=await manager.apply_memory_mutation_plan(principal=principal,
        scope=MemoryScope.personal(principal.actor_id),plan=plan)
    if applied.outcome is not h.MemoryMutationApplyOutcome.COMMITTED or applied.receipt_ref is None:
        raise ValueError('corpus_setup_not_committed')
    view=await manager.get_memory_mutation_receipt_view(principal=principal,receipt_ref=applied.receipt_ref)
    if view.plan_hash!=plan.plan_hash or view.plan_id!=identity or view.apply_mode!='strict_atomic':
        raise ValueError('corpus_setup_receipt_differs')
    mapped={op.operation_id:op for op in view.operations}
    if set(mapped)!={op.operation_id for op in operations}:
        raise ValueError('corpus_setup_labels_incomplete')
    for op in operations:
        actual=mapped[op.operation_id]
        if (actual.memory_type!=op.memory_type.value or actual.revision!=1
                or actual.content_hash!=canonical_hash(op.payload.to_json())
                or actual.evidence_ids!=tuple(sorted({s.evidence_id for s in op.evidence_spans}))
                or actual.epistemic_status!=op.epistemic_status.value):
            raise ValueError('corpus_setup_public_readback_differs')
    return dict(case_id=batch.case_id,setup_hash=batch.setup_hash,fixture_defaults=batch.fixture_defaults,
        source_id=envelope.evidence_id,source_hash=envelope.envelope_hash,
        plan=plan,apply_result=applied,receipt=view,labels=mapped)
