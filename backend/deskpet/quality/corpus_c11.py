"""C11 expired setup-only facts and explicit temporal fixture defaults.

No current input/oracle is accepted. An unspecified past expiry is represented
by scenario time minus one second, not asserted as an authored historical date.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import math

from deskpet.task_scope.protocol import canonical_hash

SETUPS = {'C11-01': ('旧打印优惠0.5元/页于9月1日00:00上海时间到期；当前价目由用户给出。',
            'a1e992716520f74a7b5adee31c4404f7aa2dd3614581e663de3006cc0b3ee689'),
 'C11-02': ('临时开放到22:00规则已于8月31日结束；当前时段完整。',
            '087c43c3697bf654b988e030c7cfdd809556de03cda757e1293e0bbe5e0d47c5'),
 'C11-03': ('旧夏季活动只穿白色的临时要求已到期。', '8cd6e47ff00a1ddf7455d362c005455f39ab6e5d882936f8fae3e6fb665cfd95'),
 'C11-04': ('临时支出额度1000元到期；本轮额度500元明确。', '0875ec47192dc81254e60310401e334554b4e7c1102c01b77a3296e5c2b13ead'),
 'C11-05': ('过期出行方案含旧集合时间06:00；当前只是排序。', '8be927ef9dc38a9caab1f3e30c85c55fbd0284b57a84fd06373d29fc20f00cda'),
 'C11-06': ('旧活动临时模板必须加“周年庆”已到期。', '053c665ea38a3f11ca883847c6dc25d039d803f7d70e4cb56b70e218a9ce18f7'),
 'C11-07': ('过去临时采用UTC记录的约定已到期；当前本轮上海时间明确。',
            '8dcd8b89a5df753b42f1894246c45f1e140de569263cdbc5c66809d73af85fea'),
 'C11-08': ('过期租用备注“钥匙在柜顶”不再有效，当前无钥匙位置需求。',
            '2979f2997b139462bc7e1fde6b59f3ff6a6f13db7d7e767df83499204d856967'),
 'C11-09': ('旧报名窗口截止8月20日已过期；当前用户只改文句。', '7e093cfbfb151abac6659184961c8cab420e268547c7b3b2420fce340fde069b'),
 'C11-10': ('旧活动地点“南桥展厅”有效至8月31日；当前地点北院。',
            '24a5e79e00594517086b740ddc4422bc38508a0d40ed8905abf11832f41c85ff'),
 'C11-11': ('短期昵称“值班小蓝”仅八月有效，已到期。', '0599c5e17408d82f2e429a2de2d2553bcb6316d820fca5f3e88bac15ecd46317'),
 'C11-12': ('过期运费优惠免邮仍出现在旧摘要，但有效期已结束；摘要有相同时间血缘。',
            'd01e1f5112168e07ef101e53b68c6da9794b5664ef11491ec818353283ec32c7'),
 'C11-13': ('A旧额度200元已到期；B新额度800元9月10日才生效；当前用户明确500元。',
            'fd4a0ca1b660ee331552de849fe72211adb2c5afa041b5b388d90299e97f85de'),
 'C11-14': ('旧便签到期恰为2026-09-06 10:00上海时区，当前now等于到期值；本题无其依赖。',
            '5de00caf1f718a08bb89037dbc2aefcd530649a3054ac1afc0aa27c7cf6663d9'),
 'C11-15': ('旧临时规定单价8元已到期，当前价目恰也8元；来源状态不同。',
            '7e4056e9f99888e84eb6d0262728b9a7908e38d1d705d644f9c7eeff36b296f9'),
 'C11-16': ('A提醒领取旧门票已expired；A正文普通不可作为当前待办。',
            '716e27fa4b6e68ab7a9931d3fe64f39c72f67d17b1f08bcd622081eb6936f467'),
 'C11-17': ('临时口味偏好只吃凉食到9月1日结束；本轮明确热粥。', '8352eb6f75d11002a133cef026b308c59d363f6249f896acfdec1fcd7aa85d43'),
 'C11-18': ('旧项目临时允许无来源引用已到期；本轮公开格式明确要求出处。',
            'c8eb02a2544aeea943f03c69f35142c71ffa96e35dd96d28cbb4626f91a51bc6'),
 'C11-19': ('旧费率、旧数量上限、旧运费优惠均在8月底到期。', '24ce57a7bae2f7e53822c71b9cbcc064db788e7023b1e1bb2ee05f2c7f49ae30'),
 'C11-20': ('旧地址“西苑测试楼”已于8月1日失效；当前新地址缺失。',
            'a57c04e8f4886ac54831ec002093daefaae3838c903c40fd3f370111e711a128')}

# label, predicate, old value, qualifiers, expiry rule. Every concrete value is
# present in the original setup; no value is copied from current input.
SPECS = {
 'C11-01': (('A', 'printing_offer', '0.5元/页', (), 'sep01'),),
 'C11-02': (('A', 'temporary_opening', '22:00', (), 'sep01'),),
 'C11-03': (('A', 'temporary_clothing', '只穿白色', ('夏季活动',), 'past'),),
 'C11-04': (('A', 'temporary_spending_limit', '1000元', (), 'past'),),
 'C11-05': (('A', 'old_gathering_time', '06:00', ('出行方案',), 'past'),),
 'C11-06': (('A', 'temporary_template_suffix', '周年庆', (), 'past'),),
 'C11-07': (('A', 'temporary_log_timezone', 'UTC', (), 'past'),),
 'C11-08': (('A', 'rental_key_note', '钥匙在柜顶', (), 'past'),),
 'C11-09': (('A', 'registration_deadline', '8月20日', (), 'aug21'),),
 'C11-10': (('A', 'temporary_event_location', '南桥展厅', (), 'sep01'),),
 'C11-11': (('A', 'temporary_nickname', '值班小蓝', (), 'sep01'),),
 'C11-13': (('A', 'temporary_quota', '200元', (), 'past'),
             ('B', 'temporary_quota', '800元', (), 'future_sep10')),
 'C11-14': (('A', 'temporary_note_marker', '旧便签', (), 'boundary'),),
 'C11-15': (('A', 'temporary_unit_price', '8元', (), 'past'),),
 'C11-17': (('A', 'temporary_food_preference', '只吃凉食', (), 'sep02'),),
 'C11-18': (('A', 'temporary_reference_exception', '允许无来源引用', ('旧项目',), 'past'),),
 'C11-20': (('A', 'temporary_address', '西苑测试楼', (), 'aug01'),),
}


@dataclass(frozen=True)
class ExpiredSetup:
    case_id: str
    setup_text: str
    setup_hash: str
    scenario_time: float
    ingestion_time: float
    specs: tuple
    intervals: tuple
    defaults: tuple
    manifest_hash: str


def compile_c11_setup(case_id, setup_text, *, scenario_clock):
    if case_id not in SETUPS or type(setup_text) is not str or setup_text != SETUPS[case_id][0]:
        raise ValueError('c11_exact_original_setup_required')
    if case_id not in SPECS:
        raise ValueError('c11_derived_or_prospective_source_pending')
    instant = datetime.fromisoformat(scenario_clock)
    if instant.tzinfo is None or not math.isfinite(instant.timestamp()):
        raise ValueError('c11_aware_scenario_clock_required')
    # Authored month/day references are in Shanghai; year comes only from the
    # trusted scenario clock. End-of-day references use the following midnight.
    from zoneinfo import ZoneInfo
    zone = ZoneInfo('Asia/Shanghai')
    year = instant.astimezone(zone).year
    def at(month, day): return datetime(year, month, day, tzinfo=zone).timestamp()
    now = instant.timestamp()
    times = dict(sep01=at(9,1), sep02=at(9,2), aug21=at(8,21), aug01=at(8,1),
        past=now-1, boundary=datetime(2026,9,6,10,tzinfo=zone).timestamp())
    intervals, specs, defaults = [], [], []
    for label, predicate, value, qualifiers, rule in SPECS[case_id]:
        start, end = (at(9,10), None) if rule == 'future_sep10' else (None, times[rule])
        if (end is not None and end > now) or (start is not None and start <= now):
            raise ValueError('c11_authored_temporal_state_differs')
        if rule == 'boundary' and end != now:
            raise ValueError('c11_exact_boundary_clock_required')
        specs.append((label, 'semantic', 'user:self', predicate, value, qualifiers))
        intervals.append((label, start, end))
        if rule == 'past': defaults.append((label, 'unspecified_expiry=scenario_minus_one_second'))
        elif rule != 'boundary': defaults.append((label, 'Shanghai_month_day_year_from_scenario'))
        if case_id == 'C11-14': defaults.append((label, 'known_note_marker_only_no_invented_note_body'))
    ingestion = min(end for _, _, end in intervals if end is not None) - 1
    if ingestion < 0: raise ValueError('c11_ingestion_clock_invalid')
    values = dict(case_id=case_id, setup_text=setup_text, setup_hash=SETUPS[case_id][1],
        scenario_time=now, ingestion_time=ingestion, specs=tuple(specs),
        intervals=tuple(intervals), defaults=tuple(defaults))
    return ExpiredSetup(**values, manifest_hash=canonical_hash(dict(domain='corpus-c11/v1', **values)))


def validate_c11_setup(batch):
    if type(batch) is not ExpiredSetup or batch != compile_c11_setup(batch.case_id,
            batch.setup_text, scenario_clock=datetime.fromtimestamp(batch.scenario_time, timezone.utc).isoformat()):
        raise ValueError('c11_exact_manifest_required')
