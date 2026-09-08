"""C10 contested-not-required setup-only compiler. Neither gold nor query is input.

Every case seeds one or more slots whose incumbent value is later CONTESTED by a
challenger value from a distinct evidence source, exactly the SDK conflict-group
mechanism (`MemoryMutationKind.CONTEST`). Concrete values below are explicit
synthetic fixture defaults where the original setup leaves them unspecified;
they are never extracted from provider_input or gold.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import math

import simple_harness as h
from deskpet.task_scope.protocol import canonical_hash

# Exact original setup fields; no provider-input/answer/scoring metadata.
SETUPS = {'C10-01': ('默认区域上海/东京存在未解决冲突，通知内容不依赖区域。',
            'e26f84f8ceb1f222ea892b8583b6ed1e04a772f80bdd53083738d25fee6ff1db'),
 'C10-02': ('同一收件地址南楼/北楼有争议；本次只给金额。', '8159260c2ed3cdf05440ef71ad2fed3131d3d1ebe3f91fd44f04f7effc1ee29a'),
 'C10-03': ('个人技术栈Python/JavaScript争议未决，本题不是选型。',
            '3d4a7dfb240f2b781208081ff12582799c7ed3c37a1094bf676a3c7e6b5e06ca'),
 'C10-04': ('同一人物当前角色经理/顾问冲突；本题文本无该人物。', 'c19c14d4ccae8e668ea63c1b7fc4a07486d0e2346ac2b430410100c99b82e8a8'),
 'C10-05': ('工具版本3.1/3.2有冲突；当前日期清单独立。', '55687a6433abd989c331765b8dce05e63783081d7cfbf7c90c8ac81a58d7325e'),
 'C10-06': ('预算上限100/200有未确认冲突；用户本轮只要求已给金额总和。',
            'e5aea0f1eb300d9b760a9c904fc1b1688cd9adf5af5a5adb30ec0958ad2017cc'),
 'C10-07': ('会议时间周二/周四争议；当前段落只要求标题。', '65635febe44bb00514e8d16dbaf230e934f51fce558138071b977faa11a33aae'),
 'C10-08': ('语气偏好正式/随意有争议；当前明确指定正式口吻。', '3fb0cd8efb3f5531807ba64caba8071685392b8ce095bcda48e5c7130423205f'),
 'C10-09': ('旅行城市南京/苏州有争议，与当前文章排版无关。', '2a0c187eff8e1fda7de1182da7670e6495896e34ba3691cd6edcae6c3abebdf7'),
 'C10-10': ('联系人对应邮箱有两个冲突候选，本题空白模板不需要联系人。',
            '1bfa7067ae1e835a36e95306a82ecc945cc1d07009e420347be4eb36fa5df317'),
 'C10-11': ('旧安装procedure争议未决；本题只读给定表达式，无安装执行。',
            '8f85afe19bdffb116b9b35519a4936e4eab0e154ebe84b86eb37d68db9554944'),
 'C10-12': ('地址、预算、语言三组隐藏冲突均未解决。', '4f8a34b1223813bcb07b6403bcdd846dac2ccce1c7b8b833db4a879dd519678e'),
 'C10-13': ('同一图书馆开放时间有争议；本轮只按已给名称排标题。', '52eadee703487d7af4dc2db91fd14e010d9871618ae6e9cae4d262fcca451391'),
 'C10-14': ('隐藏冲突两侧均因recipient/purpose不合格；本任务只转换公开名单顺序。',
            '53bcc93625d7c273c06b5ef6e0a723beb7f917a9ce50c006eab2740497741dd8'),
 'C10-15': ('隐藏区域冲突A/B，A后来被suppression；本题不依赖组是否完整。',
            '6099b05cff49902fd0e969b8bf6f29dd76e39877c3ed0c1b118d50c6e6da2609'),
 'C10-16': ('一分钟前新产生工具默认设置冲突，但未进入当前可见任务材料。',
            'd8b0bd44b98dd92bf03e35797aa5b2704b84508f8bca29c70d9e2e806afe4886'),
 'C10-17': ('无关集合地点存在冲突，当前用户给了明确活动名称。', 'aae183e5da24b47c20b751c3915833dadd2078a6bb62d67253cd665d92e68949'),
 'C10-18': ('库内争议是另一个项目工期；本轮给定文章中的争议措辞与该工期无关。',
            '37fb248964a2e531b7f9f9edf0ac2943f54d65699a2e2980f23c9b831a641d38'),
 'C10-19': ('个人纸张尺寸A4/A5冲突；本轮数学题无打印需求。', '5fb639f11b2c71e60a7af691b603e171ab8b527159cde335818d2d822f735d4d'),
 'C10-20': ('无关软件版本冲突；本题缺少计算数量，历史数量未获指定适用。',
            'f6ca705da1dfc7ae2cfae4a08ad583cf1780cd8fe172c091f384c24bc3d4ffde')}


@dataclass(frozen=True)
class ContestedSlot:
    """One slot: incumbent value (revision 1) contested by a challenger (revision 2)."""
    label: str
    kind: str
    subject: str
    predicate: str
    incumbent: str
    challenger: str
    qualifiers: tuple[str, ...] = ()
    incumbent_steps: tuple[str, ...] = ()
    challenger_steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class UncontestedExtra:
    """An unrelated uncontested record seeded with the incumbent evidence."""
    label: str
    kind: str
    predicate: str
    value: str
    qualifiers: tuple[str, ...] = ()


def _slot(label, predicate, incumbent, challenger, qualifiers=(), subject='user:self'):
    return ContestedSlot(label, 'semantic', subject, predicate, incumbent, challenger, tuple(qualifiers))


_REGION = _slot('A', 'default_region', '上海', '东京', ('默认区域',))
_ADDRESS = _slot('A', 'delivery_address', '南楼', '北楼', ('同一收件地址',))
_BUDGET = _slot('B', 'budget_limit', '100元', '200元', ('预算上限',))

SLOTS = {
    'C10-01': (_REGION,),
    'C10-02': (_ADDRESS,),
    'C10-03': (_slot('A', 'personal_tech_stack', 'Python', 'JavaScript', ('个人技术栈',)),),
    'C10-04': (_slot('A', 'current_role', '经理', '顾问', ('同一人物',), subject='person:fixture-colleague'),),
    'C10-05': (_slot('A', 'tool_version', '3.1', '3.2', ('工具版本',)),),
    'C10-06': (_slot('A', 'budget_limit', '100元', '200元', ('预算上限',)),),
    'C10-07': (_slot('A', 'meeting_weekday', '周二', '周四', ('会议时间',)),),
    'C10-08': (_slot('A', 'tone_preference', '正式', '随意', ('语气偏好',)),),
    'C10-09': (_slot('A', 'travel_city', '南京', '苏州', ('旅行城市',)),),
    'C10-10': (_slot('A', 'contact_email', 'lianxiren-a@example.test', 'lianxiren-b@example.test',
                     ('联系人邮箱',), subject='person:fixture-contact'),),
    'C10-11': (ContestedSlot('A', 'procedure', 'user:self', '旧安装流程', '', '', ('旧安装',),
                             ('下载安装包', '校验签名', '运行安装程序', '重启机器'),
                             ('下载安装包', '运行安装程序', '重启机器', '事后校验签名')),),
    'C10-12': (_ADDRESS, _BUDGET, _slot('C', 'work_language', '中文', '英文', ('语言',))),
    'C10-13': (_slot('A', 'opening_hours', '09:00', '10:00', ('青石图书馆',), subject='entity:qingshi-library'),),
    'C10-14': (_slot('A', 'private_group_member', '丁', '戊', ('受限小组成员',)),),
    'C10-15': (_REGION,),
    'C10-16': (_slot('A', 'tool_default_setting', '自动保存', '手动保存', ('工具默认设置',)),),
    'C10-17': (_slot('A', 'gathering_place', '东门', '北门', ('集合地点',)),),
    'C10-18': (_slot('A', 'schedule_duration', '两周', '三周', ('另一个项目工期',), subject='project:fixture-other'),),
    'C10-19': (_slot('A', 'paper_size', 'A4', 'A5', ('个人纸张尺寸',)),),
    'C10-20': (_slot('A', 'software_version', '2.0', '2.1', ('软件版本',)),),
}

EXTRAS = {
    # Unrelated historical quantity: the gold forbids using it to fill the gap.
    'C10-20': (UncontestedExtra('H', 'episode', '上次购买数量', '12件'),),
}

# Fixture prose delivered as two DISTINCT evidence sources: the SDK refuses a
# CONTEST whose evidence is a subset of the incumbent's. Values are quoted
# verbatim from these texts; never from the original setup line or gold.
SOURCES = {
    'C10-01': ('我的默认区域是上海。', '我的默认区域是东京。'),
    'C10-02': ('同一收件地址是南楼。', '同一收件地址是北楼。'),
    'C10-03': ('我的个人技术栈是Python。', '我的个人技术栈是JavaScript。'),
    'C10-04': ('那位同事的当前角色是经理。', '那位同事的当前角色是顾问。'),
    'C10-05': ('我用的工具版本是3.1。', '我用的工具版本是3.2。'),
    'C10-06': ('我的预算上限是100元。', '我的预算上限是200元。'),
    'C10-07': ('我的会议时间是周二。', '我的会议时间是周四。'),
    'C10-08': ('我的语气偏好是正式。', '我的语气偏好是随意。'),
    'C10-09': ('我的旅行城市是南京。', '我的旅行城市是苏州。'),
    'C10-10': ('那位联系人的邮箱是lianxiren-a@example.test。', '那位联系人的邮箱是lianxiren-b@example.test。'),
    'C10-11': ('我的旧安装流程是：下载安装包；校验签名；运行安装程序；重启机器。',
               '我的旧安装流程是：下载安装包；运行安装程序；重启机器；事后校验签名。'),
    'C10-12': ('同一收件地址是南楼；我的预算上限是100元；我的工作语言是中文。',
               '同一收件地址是北楼；我的预算上限是200元；我的工作语言是英文。'),
    'C10-13': ('青石图书馆的开放时间是09:00。', '青石图书馆的开放时间是10:00。'),
    'C10-14': ('受限小组成员是丁。', '受限小组成员是戊。'),
    'C10-15': ('我的默认区域是上海。', '我的默认区域是东京。'),
    'C10-16': ('我的工具默认设置是自动保存。', '我的工具默认设置是手动保存。'),
    'C10-17': ('我们的集合地点是东门。', '我们的集合地点是北门。'),
    'C10-18': ('另一个项目的工期是两周。', '另一个项目的工期是三周。'),
    'C10-19': ('我的个人纸张尺寸是A4。', '我的个人纸张尺寸是A5。'),
    'C10-20': ('我的软件版本是2.0。上次购买数量是12件。', '我的软件版本是2.1。'),
}

# Both conflict members are ineligible for the ordinary user_self/task purpose:
# proposed RESTRICTED joins to effective RESTRICTED, which the SDK excludes.
RESTRICTED_CASES = frozenset({'C10-14'})
# The incumbent's source evidence is suppressed after the contest was recorded.
SUPPRESSED_INCUMBENT_CASES = frozenset({'C10-15'})
# Seconds before scenario time at which the contest evidence was admitted.
CONTEST_OFFSETS = {'C10-16': 60.0}
DEFAULT_CONTEST_OFFSET = 86400.0
INCUMBENT_OFFSET = 7 * 86400.0


@dataclass(frozen=True)
class ContestedSetup:
    case_id: str
    setup_text: str
    setup_hash: str
    scenario_time: float
    incumbent_time: float
    contest_time: float
    slots: tuple
    extras: tuple
    incumbent_text: str
    challenger_text: str
    privacy_class: str
    suppress_incumbent: bool
    fixture_defaults: tuple[str, ...]
    manifest_hash: str


def compile_c10_setup(case_id, setup_text, *, scenario_clock):
    if type(case_id) is not str or case_id not in SETUPS:
        raise ValueError('c10_unknown_setup')
    original, digest = SETUPS[case_id]
    if (type(setup_text) is not str or setup_text != original
            or sha256(setup_text.encode()).hexdigest() != digest):
        raise ValueError('c10_setup_source_changed')
    instant = datetime.fromisoformat(scenario_clock)
    if instant.tzinfo is None or not math.isfinite(instant.timestamp()) or instant.timestamp() < 0:
        raise ValueError('c10_trusted_aware_clock_required')
    now = instant.timestamp()
    offset = CONTEST_OFFSETS.get(case_id, DEFAULT_CONTEST_OFFSET)
    incumbent_time, contest_time = now - INCUMBENT_OFFSET, now - offset
    if not incumbent_time < contest_time < now:
        raise ValueError('c10_fixture_clock_order_invalid')
    slots, extras = SLOTS[case_id], EXTRAS.get(case_id, ())
    incumbent_text, challenger_text = SOURCES[case_id]
    for slot in slots:
        quotes = ((slot.incumbent, incumbent_text), (slot.challenger, challenger_text)) \
            if slot.kind == 'semantic' else ()
        for value, text in quotes:
            if value not in text:
                raise ValueError('c10_fixture_value_not_in_source:' + slot.label)
    for extra in extras:
        if extra.value not in incumbent_text:
            raise ValueError('c10_fixture_extra_not_in_source:' + extra.label)
    if incumbent_text == challenger_text or setup_text in (incumbent_text, challenger_text):
        raise ValueError('c10_fixture_sources_must_be_distinct_from_setup')
    privacy = 'restricted' if case_id in RESTRICTED_CASES else 'personal'
    suppress = case_id in SUPPRESSED_INCUMBENT_CASES
    defaults = ('synthetic_contested_values=v1', 'incumbent_admitted=scenario-7d',
        'contest_admitted=scenario-%ds' % int(offset), 'contest_via_sdk_conflict_group')
    if privacy == 'restricted':
        defaults += ('both_members_proposed_restricted=ineligible_for_user_self_task_purpose',)
    if suppress:
        defaults += ('incumbent_evidence_suppressed_after_contest=user_forget',
                     'partial_group_visibility_recorded_not_scored')
    if extras:
        defaults += ('undated_extra_episode=incumbent_time-7d',)
    values = dict(case_id=case_id, setup_text=setup_text, setup_hash=digest, scenario_time=now,
        incumbent_time=incumbent_time, contest_time=contest_time, slots=slots, extras=extras,
        incumbent_text=incumbent_text, challenger_text=challenger_text, privacy_class=privacy,
        suppress_incumbent=suppress, fixture_defaults=defaults)
    manifest = canonical_hash(dict(domain='host:corpus-c10/v1', **{k: _wire(v) for k, v in values.items()}))
    return ContestedSetup(**values, manifest_hash=manifest)


def _wire(value):
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    if isinstance(value, (ContestedSlot, UncontestedExtra)):
        return {name: _wire(getattr(value, name)) for name in value.__dataclass_fields__}
    return value


def validate_c10_setup(batch):
    if type(batch) is not ContestedSetup or batch != compile_c10_setup(batch.case_id,
            batch.setup_text, scenario_clock=datetime.fromtimestamp(batch.scenario_time, timezone.utc).isoformat()):
        raise ValueError('c10_exact_compiled_setup_required')


def incumbent_payload(batch, slot):
    if slot.kind == 'semantic':
        return h.SemanticMemoryPayload(slot.subject, slot.predicate, slot.incumbent, slot.qualifiers)
    if slot.kind == 'procedure':
        return h.ProcedureMemoryPayload(slot.predicate, slot.qualifiers, slot.incumbent_steps,
                                        h.ProcedureRiskLevel.LOW)
    raise ValueError('c10_unimplemented_slot_type')


def challenger_payload(batch, slot):
    if slot.kind == 'semantic':
        return h.SemanticMemoryPayload(slot.subject, slot.predicate, slot.challenger, slot.qualifiers)
    if slot.kind == 'procedure':
        return h.ProcedureMemoryPayload(slot.predicate, slot.qualifiers, slot.challenger_steps,
                                        h.ProcedureRiskLevel.LOW)
    raise ValueError('c10_unimplemented_slot_type')


def extra_payload(batch, extra):
    if extra.kind == 'episode':
        return h.EpisodeMemoryPayload(extra.predicate, ('user:self',), (), (extra.value,), (), (),
                                      batch.incumbent_time - 7 * 86400, None, None)
    if extra.kind == 'semantic':
        return h.SemanticMemoryPayload('user:self', extra.predicate, extra.value, extra.qualifiers)
    raise ValueError('c10_unimplemented_extra_type')


def lifecycle_for(kind):
    return {'semantic': h.SemanticLifecycleState.ACTIVE, 'episode': h.EpisodeLifecycleState.ACTIVE,
            'procedure': h.ProcedureLifecycleState.ACTIVE}[kind]
