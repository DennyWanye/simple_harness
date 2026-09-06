"""Exact C08 setup-only mappings; no current prompt or gold input.

Only scalar fact cases are executable here. Explicit retained summaries,
reminders, relationship/alias sources remain blocked until actually produced.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import math
import simple_harness as h

FACTS = {
    'C08-01': ('semantic', 'address', '梧桐巷42号测试楼'),
    'C08-02': ('semantic', 'telephone', '测试内线47'),
    'C08-03': ('semantic', 'dinner_preference', '晚饭只吃面'),
    'C08-04': ('semantic', 'family_address_term', '舅舅阿青'),
    'C08-05': ('episode', '上周外出行程', '去松桥镇'),
    'C08-07': ('semantic', 'old_nickname', '阿蓝'),
    'C08-08': ('semantic', 'old_occupation', '钟表修理'),
    'C08-09': ('semantic', 'monthly_budget', '800元'),
    'C08-10': ('semantic', 'treatment_fact', '接受测试项目M'),
    'C08-12': ('semantic', 'residential_area', '北湾测试区'),
    'C08-14': ('semantic', 'hobby', '模型拼装'),
    'C08-17': ('semantic', 'family_schedule', '周六探访亲戚'),
}

# These four also name independent derived carriers in the original setup.
# Scalar suppression remains useful evidence, but cannot mark the case ready.
UNPREPARED_CARRIERS = {
    'C08-01': ('retained_summary',),
    'C08-02': ('derived_contact_material',),
    'C08-04': ('address_term_association',),
    'C08-09': ('amount_reference',),
}


@dataclass(frozen=True)
class SuppressedSetup:
    case_id: str
    setup_text: str
    setup_hash: str
    scenario_time: float
    specs: tuple
    fixture_defaults: tuple[str, ...]


def compile_c08_setup(case_id, setup_text, *, scenario_clock):
    if type(case_id) is not str or case_id not in SETUPS:
        raise ValueError('c08_unknown_setup')
    original, digest = SETUPS[case_id]
    if type(setup_text) is not str or setup_text != original or sha256(setup_text.encode()).hexdigest() != digest:
        raise ValueError('c08_setup_source_changed')
    if case_id not in FACTS:
        raise ValueError('c08_retained_derived_source_not_prepared')
    clock = datetime.fromisoformat(scenario_clock)
    if clock.tzinfo is None or not math.isfinite(clock.timestamp()):
        raise ValueError('c08_clock_offset_required')
    kind, predicate, value = FACTS[case_id]
    defaults = ('setup_old_fact_then_evidence_suppression',)
    if kind == 'episode':
        defaults += ('undated_last_week=clock-7d',)
    return SuppressedSetup(case_id, original, digest, clock.timestamp(),
        (('A', kind, 'user:self', predicate, value, ()),), defaults)


def validate_c08_setup(batch):
    if type(batch) is not SuppressedSetup or batch != compile_c08_setup(
            batch.case_id, batch.setup_text,
            scenario_clock=datetime.fromtimestamp(batch.scenario_time, timezone.utc).isoformat()):
        raise ValueError('c08_exact_compiled_setup_required')


def suppressed_payload(batch, spec):
    _, kind, subject, predicate, value, qualifiers = spec
    if kind == 'semantic':
        return h.SemanticMemoryPayload(subject, predicate, value, qualifiers)
    if kind == 'episode':
        return h.EpisodeMemoryPayload(predicate, (subject,), (), (value,), (), (),
            batch.scenario_time - 7 * 86400, None, None)
    raise ValueError('c08_unsupported_payload')

SETUPS = {'C08-01': ('已忘A地址“梧桐巷42号测试楼”，相关原文/摘要均suppressed。',
            '5e2f5465e45a67b659a9875584685d6e356bd0e3e358ac94b42220811cd22052'),
 'C08-02': ('已忘A联系电话“测试内线47”；所有派生联络资料普通不可见。',
            'b5b185cef6ca07d5b6afbdfb272ecb27fabe01d683b3ab8caa3dc094c808b06d'),
 'C08-03': ('已忘A偏好“晚饭只吃面”；当前明确菜单自足。', '7d5ca7a9ebacf97ea1a77a51763be1ea448668350424931bd46506dc19ba9bdb'),
 'C08-04': ('已忘A家庭成员称呼“舅舅阿青”；称呼关联suppressed。',
            'a3fc0dda0ca1ddaf07425543d50ecbe552d85b8294614dec278b8fa783a62e2b'),
 'C08-05': ('已忘A上周外出行程“去松桥镇”；普通路径不可见。', '95d0e20737eb0d2d11a875c9ebdbf12f0dab40dc04769e7b62ddfe9408c02725'),
 'C08-06': ('已忘A备注“物品藏在绿柜”；旧摘要仍物理存在但关联suppression。',
            '5aa12863f216618e6c42af3f2c7f043489b99fc4b7b0b55649e5859a53e03d4a'),
 'C08-07': ('已忘A旧昵称“阿蓝”；无解除suppression授权。',
            '401253c124953578c23a9782e8d66cb7d39d5b5e6b23c82fa1cc916c18358ef1'),
 'C08-08': ('已忘A旧职业“钟表修理”；当前介绍只给兴趣。', '9fca9e55c99c15f48e6ef55ac92728ec4898c30dbee4826ea5c56509a1b71753'),
 'C08-09': ('已忘A月预算“800元”；金额记忆及引用被suppressed。',
            '2426e92c72df456cffa045aa589ecc2595c5eaf7f0311a49b683328d9ba8623d'),
 'C08-10': ('已忘A诊疗事实“接受测试项目M”；受抑制健康来源。', 'a90dd3cee4f2fc183356ad85b63b3aad33d95d807a9dfab1243520e1bf726568'),
 'C08-11': ('已忘A学校“青麦学堂”；学校字段和旧介绍均不可见。', '5ace3a63a417ee3e8de233b5c14fa4cfa263737cfcf86bc46e85cc786ddca26f'),
 'C08-12': ('已忘A住区“北湾测试区”；没有读取住区的需要。', 'a751a537967c07d5b633f86da78bc7b187ce80fd3088c89d29477150a48d514c'),
 'C08-13': ('已忘A纪念日“4月17日”；相关提醒也不可普通复用。', '3e0588576d58b36f8bbfe1524230a73e9e34f6f0e7c6cce437de0e4d9a45dd1c'),
 'C08-14': ('已忘A爱好“模型拼装”；当前文章主题明确。', '1959c1fdddf36b2b38501d20cb5fed2502c7c3e9d473ae488aa9f7e394f84cfd'),
 'C08-15': ('已忘A旧会议决定“采购蓝色桌布”，旧纪要suppressed。',
            'f5a56c274c3c819fdb1c42a63c30816eb01514fb9f6dab99ca7575a0b5c1977c'),
 'C08-16': ('已忘A项目决定“省略校对”；旧检查列表受抑制。', '7235ad7aa0302ed039b4175aa383940df1c851516b294a4d2806a887b975af26'),
 'C08-17': ('已忘A家庭安排“周六探访亲戚”；相关时段不参与本题。', '64c6e5b23c956b9411892580035d38b480f801261fac1cf0c45d1559e5ecd61c'),
 'C08-18': ('已忘A原话“我以后只选深绿色”；原文/semantic及摘要均suppressed。',
            '43844155eaa42d6baa1976cd58ccd5e2ef04acf829e41633536288b808775df1'),
 'C08-19': ('已忘A旧对话关联收件人“邻居小松”；原始关系被抑制。', 'e78edfc5b077654f676ed6e4840e16afa9003f168ec9a0762122c19f6dd25778'),
 'C08-20': ('已忘A工具别名“蓝盒”对应另一实体；本轮名称完整准确。',
            'bbf0c267402a7e76be9222f1f99c4f8ec6640be6f8db3625452a21b68f62a006')}
