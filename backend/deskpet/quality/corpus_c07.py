"""C07 setup-only compiler. Neither gold nor current query is an input.

Concrete distractor values below are explicit synthetic fixture defaults where
07-no-match leaves values unspecified. They are not extracted model memories.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import math

import simple_harness as h
from deskpet.task_scope.protocol import canonical_hash


@dataclass(frozen=True)
class Distractor:
    kind: str
    source_text: str
    predicate: str
    value: str
    qualifiers: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()


# Underspecified values are authored only here, not from provider_input/gold.
# Even cases without a required old record receive one unrelated distractor.
_TRAVEL = Distractor('semantic', '我旅行时喜欢乘坐火车。', 'travel_transport', '火车', ('旅行',))
DISTRACTORS = {
    'C07-01': _TRAVEL,
    'C07-02': Distractor('semantic', '我阅读小说时偏好英语版本。', 'novel_language', '英语', ('阅读小说',)),
    'C07-03': Distractor('episode', '上次采购清单是信封和胶带。', '旧采购清单', '信封和胶带'),
    'C07-04': Distractor('episode', '上次活动的历史账本记录场地费260元。', '旧活动账本', '场地费260元'),
    'C07-05': Distractor('semantic', '我的长篇报告偏好分节叙述。', 'report_style', '分节叙述', ('长篇报告',)),
    'C07-06': Distractor('semantic', '我过去习惯把文具收进木抽屉。', 'stationery_storage', '木抽屉', ('旧收纳习惯',)),
    'C07-07': Distractor('semantic', '我以前的项目标题是山径观察。', 'old_project_title', '山径观察'),
    'C07-08': _TRAVEL,
    'C07-09': Distractor('episode', '上次会议安排在上午十一点。', '旧会议安排', '上午十一点'),
    'C07-10': Distractor('semantic', '我的内部备忘录通常用简短的陈述句。', 'memo_style', '简短的陈述句', ('内部备忘录',)),
    'C07-11': Distractor('procedure',
        '整理纸质档案时，我明确采用档案归档流程：核对档案编号；检查页码；登记借阅状态；装入档案袋；归还档案架。',
        '档案归档', '', ('整理纸质档案',),
        ('核对档案编号', '检查页码', '登记借阅状态', '装入档案袋', '归还档案架')),
    'C07-12': _TRAVEL,
    'C07-13': Distractor('episode', '旧财务记录中的办公支出是340元。', '旧财务记录', '办公支出340元'),
    'C07-14': Distractor('episode', '更早的书架记录是第三层放工具书。', '旧书架安排', '第三层放工具书'),
    'C07-15': Distractor('semantic', '我日常订餐通常是四人份。', 'daily_meal_count', '四人份', ('日常订餐',)),
    'C07-16': Distractor('semantic', '我的测试联系人名单是虚构联系人甲、虚构联系人乙，电话均为占位号码。',
        'fixture_contacts', '虚构联系人甲、虚构联系人乙，电话均为占位号码'),
    'C07-17': _TRAVEL,
    'C07-18': _TRAVEL,
    'C07-19': Distractor('semantic', '旧样品甲登记的颜色是绿色。', 'old_sample_colour', '绿色', ('旧样品甲',)),
    'C07-20': Distractor('semantic', '我的长期目标是学会园艺。', 'long_term_goal', '学会园艺'),
}

# These are the original authored recent_messages, never answers to the query.
RECENT_MESSAGES = {
    'C07-06': (('user', '红盒放钥匙，蓝盒放卡片。'), ('assistant', '收到，钥匙在红盒，卡片在蓝盒。')),
    'C07-14': (('user', '书架第二层留空。'), ('assistant', '收到，第二层留空。')),
}


@dataclass(frozen=True)
class NoMatchSetup:
    case_id: str
    setup_text: str
    setup_hash: str
    scenario_time: float
    distractor: Distractor
    recent_messages: tuple[tuple[str, str], ...]
    fixture_defaults: tuple[str, ...]
    manifest_hash: str


def compile_c07_setup(case_id, setup_text, *, scenario_clock):
    if type(case_id) is not str or case_id not in SETUPS:
        raise ValueError('c07_unknown_setup')
    text, digest = SETUPS[case_id]
    if (type(setup_text) is not str or setup_text != text
            or sha256(setup_text.encode()).hexdigest() != digest):
        raise ValueError('c07_setup_source_changed')
    clock = datetime.fromisoformat(scenario_clock)
    if clock.tzinfo is None or not math.isfinite(clock.timestamp()):
        raise ValueError('c07_clock_offset_required')
    distractor = DISTRACTORS[case_id]
    recent = RECENT_MESSAGES.get(case_id, ())
    defaults = ('synthetic_distractor_values=v1', 'unrelated_record_even_when_not_required')
    if distractor.kind == 'episode':
        defaults += ('undated_old_episode=clock-7d',)
    manifest = canonical_hash(dict(version=1, case_id=case_id, setup_hash=digest,
        source_text=distractor.source_text, payload=c07_payload(distractor, clock.timestamp()).to_json(),
        recent_messages=recent, fixture_defaults=defaults, scenario_time=clock.timestamp()))
    return NoMatchSetup(case_id, text, digest, clock.timestamp(), distractor, recent, defaults, manifest)


def validate_c07_setup(batch):
    if type(batch) is not NoMatchSetup:
        raise TypeError('NoMatchSetup required, never Case/gold/input')
    expected = compile_c07_setup(batch.case_id, batch.setup_text,
        scenario_clock=datetime.fromtimestamp(batch.scenario_time, timezone.utc).isoformat())
    if batch != expected:
        raise ValueError('c07_setup_manifest_differs')


def c07_payload(distractor, clock):
    if distractor.kind == 'semantic':
        return h.SemanticMemoryPayload('user:self', distractor.predicate,
            distractor.value, distractor.qualifiers)
    if distractor.kind == 'episode':
        return h.EpisodeMemoryPayload(distractor.predicate, ('user:self',), (),
            (distractor.value,), (), (), clock - 7 * 86400, None, None)
    if distractor.kind == 'procedure':
        return h.ProcedureMemoryPayload(distractor.predicate, distractor.qualifiers,
            distractor.steps, h.ProcedureRiskLevel.LOW)
    raise ValueError('c07_unimplemented_distractor_type')

# Exact original setup fields; no provider-input/answer/scoring metadata.
SETUPS = {'C07-01': ('库内只有无关旅行偏好；本句不在旧记忆中。', 'cd75ab7a5900df3e86dbc40999dc045b7e77d1ff0c37734fa37e95d697cbe239'),
 'C07-02': ('库内英语偏好与本题无关，当前明确指定目标语。', 'a83489afe9fc72c931a77243ed93cdbbcb965adbe8fe130fbafdcc14104ae668'),
 'C07-03': ('库内存在旧采购列表，不与当前列表相连。', 'ab04bbe03cfe5125727071b7c79c513f255c0d00b808efc2c7345414d6690073'),
 'C07-04': ('历史账本不需要，所有金额本轮给出。', 'dfca979602e47319ad8684d3c692c9b0b446492cd0d46a9e92ab79cb0c45c4c5'),
 'C07-05': ('库中有风格偏好，但本轮仅改错字。', '4c366ae244883d3ceb34c8a272500b72a911e8545aadfb319b03c274e7bf4a1c'),
 'C07-06': ('旧记录不含本次盒子安排。设置端按下列recent_messages经真实历史入口建立同一会话已提交的user→assistant完整因果组；current_user_message另作为本次请求。需验证它们由实际最近Context读取，不从gold派生，不直接拼装成一个用户字符串。',
            'c02a801dfa00adc37b186e6d565e900817a860aa186762c8aa1ce29485fb8ee8'),
 'C07-07': ('库内项目标题不同；当前段落自足。', 'a3720e7366b54fb07b235d853bec7a9cbefd05895c2c7923531f99d90e34659e'),
 'C07-08': ('没有要求个人偏好；当前两个选项已经完整。', 'a2ca5315acf3601beba420e5d6af7469242772b6ae249eeaeb403af6c2e00635'),
 'C07-09': ('旧会议时间不适用；本题为纯抽取。', '9fc08b65f56e998c865ff94e0ff88ab3073d957656b667a545c8dce9d078451d'),
 'C07-10': ('本轮显式风格优先，无须查长期风格。', 'd798d36b2447fb62a455e2c7762ce28104c51c2adcf796d7d8885ebb179394c5'),
 'C07-11': ('库有复杂procedure，但本轮只是格式转换。', '97ff2de0d9ff1b1337c9ce8535343d37aa2e2323b36ded1b8c445789f59b2e5b'),
 'C07-12': ('当前续写限制齐全，无个人故事需求。', '2be6598d8de8fc6931c887192081089c58b2e45b044ac6afc7f291e810584a7b'),
 'C07-13': ('旧财务数据无关；本轮整数运算。', '4aebcb786f22c31a9a9d8b6b187fc722b27e2767d4c1e9d5edf10e61f04258dc'),
 'C07-14': ('库内更早的书架记录不适用。设置端按下列recent_messages经真实历史入口建立同一会话已提交的user→assistant完整因果组；current_user_message单独提交。由实际Context '
            'assembler读取这组最近历史，不能把带角色标签的文字作为一个user消息代替。',
            '5a7e6e1123fbd9eefe4bfbe41cfccc45d9469295a21a1d9c63c15b26d1f85965'),
 'C07-15': ('库内有用户日常人数，但本轮是未指明活动，不允许猜。', '467dc6bd291f61f4636807867c7427de650dc25715bb75411f2470b17559792e'),
 'C07-16': ('库有联系人名单但不需要。', '298cc7fb658caec46f730cc835c3696fd786c7f86c3c0cfbfe3828d4e83a7f40'),
 'C07-17': ('无代码项目上下文需求，代码本轮完整。', '5188582b7f66d0d8023c9bd6420949ee13eeb1f489757dd7ace6181eaac964ac'),
 'C07-18': ('无历史表达式依赖。', '8665949c077e1260521aaf285da5fea85b23ba837d5f77762cc2126a3aa6b3b1'),
 'C07-19': ('库内实体状态可能不同但本轮封闭逻辑。', '5db84abc3ae7e0605067dc6ea1ac01e36f2d123e6d3e41d27629e610d572f81f'),
 'C07-20': ('记忆中有长期目标，但本轮没有保存意图。', '0521517a99499b9e1691b1462844f370e08a9bd03a2756ef9cb1fa074b0de6f4')}
