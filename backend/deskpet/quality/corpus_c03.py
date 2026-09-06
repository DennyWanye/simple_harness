"""C03 authored setup bytes only. No gold/initial/followup input.

Mapping source under development; presence here is not execution readiness.
Entity aliases are source facts, not fabricated public entity authority.
"""

SETUPS = {'C03-01': ('E：与林岚联调因时区误会错过窗口；S：与她约定周二集中反馈；D：林澜偏好实时反馈。',
            'adca85e6829998794a7e22b72deac33f36eae8f680486759de04d94f37e74e0a'),
 'C03-02': ('E：晨风社旧名青禾社，春季试刊曾漏目录；S：更名后约定交稿附目录；别名有明确证据。',
            '3e31f49249b636370872509ceeae79175c715e552feeed7c7b58d9e51555126c'),
 'C03-03': ('E：采购王老师上次验收发现数量不足；S：与采购王老师约定先核数量；D：授课王老师另有排课约定。',
            '24d6c5a39609b0876d274f54727207e8cda7168a3664b96dbaeeb1965a075d2e'),
 'C03-04': ('E：与陈工电话沟通漏记一项参数；S：与他约定参数在邮件确认，紧急事项才电话。',
            'ebdcc22bb5a0a5cd5bce3a200130dc1d65c313e47de6bb9ae20e1135aab3b7b5'),
 'C03-05': ('E：本人称便携打印机为小白，上次卡纸因受潮；S：小白只用干燥存放的纸。',
            'aee4d0fdc29ae02b0d9b9b7367bde184af81ae8c97a62e6a0f7e8b60902d27cf'),
 'C03-06': ('E：东城图书馆南馆上次闭馆时间误传；S：南馆合作信息经值班台确认；北馆规则不同。',
            '22ae17df915c3b27aec1583df2e9fc70d4de8b381c23ebf8dfa4c00ac4eb9545'),
 'C03-07': ('E：家中猫豆豆上次躲进纸箱误锁房间；S：本人约定关门前清点宠物；D：朋友的狗也叫豆豆。',
            '6a6f8d583d7df1122bac9beff9ee06ad864b5c167cd515790a9f509dc4f4f6c4'),
 'C03-08': ('E：本人参加海棠工坊试课，投影接头不兼容；S：与该工坊约定课前确认接口；D：他人转述别家断电。',
            'a0ddc536a8652f11a0bf4494973ab7e9aaa3e27a207b5d45f1eb2807eb716486'),
 'C03-09': ('E：客户的新展板项目上次色差；S：该项目当前约定先看电子样；旧包装项目直接印刷规则不适用。',
            '78f0ec2d12766cb010115434dbfadb73b016792a35a2fcfb18fa90d2a5cf869b'),
 'C03-10': ('E：标签P2的同型号扫描仪上次双面漏页；S：P2约定双面后核页码；P1只有单面记录。',
            '718f6da616d881ff68ddf3f9bf3ad95ace3296dd7fbfd907d06cf2072c24a1f8'),
 'C03-11': ('E：合作者曾用艺名山岚，上次录音背景噪声大；S：现称林舟并约定录音前关风扇。',
            'c48c4cb70e1fecb047542c91dc491a676f76b56ed4b0165582e371e185896a1d'),
 'C03-12': ('E：杭州的春日书店上次取件晚到；S：约定到店前确认营业；同名上海店无该事件。',
            '3071788c1890ebfd0d5cadfae13fb0d24a5a9a20a0a9d83862eacbf9852836f9'),
 'C03-13': ('E：与原联络人小赵交接时文件漏传；S：本人和小赵约定邮件列附件；团队后来换联络人。',
            '1c39a86ec2d14d86be8b887898b28f4bb169ea7bec6ccb8bec4376fcbdc85d0c'),
 'C03-14': ('E：与花木社活动迟到因集合点写错；S：当前约定集合通知含定位链接。',
            'a684f33450b696e7d1d3957d1945a0892e6285022e8f342b04461a2ecb9206bd'),
 'C03-15': ('E：与同学高远吃饭因餐馆客满等位40分钟；S：高远明确说并不介意排队，但约定至少提前告知。',
            'c97f39e7b2c3dcf1d3ed5aae4a6fe2d01fa91d929b20cf4ff41ca7017bbf5c5a'),
 'C03-16': ('E：合作者陶老师某次出差中临时接受语音稿；S：与陶老师正常交稿仍约定文字版。',
            '2bc2860b775fdfe13c7c6b6018355d0e4e37d42e1c035e3c90d1c2b5d8096483'),
 'C03-17': ('E1：蓝桥社六月排练灯坏；E2：八月排练音箱故障；S：八月后约定先试音。',
            'fdc73a57aed1fdc27c647cf763b4d0fc3ea015a05226acba02896db9bf2aecc4'),
 'C03-18': ('E：合作方North Pier与中文北岸同一机构，首单包装破损；S：约定后续加护角。',
            '4c71dc90d3e9e579271bd383c58976e2e6b605ab4845940acabbb2751f5340dd'),
 'C03-19': ('E：本人导师介绍的装裱店上次漏装背板；S：与该店约定取件验背板；导师其他联系人无关。',
            '26eb800431a2ffb82a5d9894aaeb64101406577e21615f3e91354ef4c6c455b0'),
 'C03-20': ('E：共享菜园上次领工具数量不齐；S：本人确认约定按清单点数；D：模型推测以后统一购买，无用户证据。',
            'ce774fca2f5b24bc95a6d0c5e5dc34a57f75b889f22198424638ef25e79b6bf5')}

# Tuple format shared with corpus_c01: label/type/subject/title-or-predicate/value/qualifiers.
# Subject strings preserve authored distinctions; they are not SDK entity IDs.
SPECS = {
 'C03-01': (
  ('E','episode','person:林岚','联调错过窗口','与林岚联调因时区误会错过窗口',()),
  ('S','semantic','person:林岚','反馈约定','周二集中反馈',()),
  ('D','semantic','person:林澜','反馈偏好','实时反馈',())),
 'C03-03': (
  ('E','episode','person:采购王老师','验收数量不足','采购王老师上次验收发现数量不足',()),
  ('S','semantic','person:采购王老师','验收约定','先核数量',()),
  ('D','semantic','person:授课王老师','排课约定','另有排课约定，具体内容未提供',())),
 'C03-04': (
  ('E','episode','person:陈工','电话沟通漏记参数','与陈工电话沟通漏记一项参数',()),
  ('S','semantic','person:陈工','参数确认渠道','参数在邮件确认，紧急事项才电话',())),
 'C03-05': (
  ('E','episode','device:本人便携打印机小白','打印机卡纸','小白上次卡纸因受潮',()),
  ('S','semantic','device:本人便携打印机小白','纸张要求','只用干燥存放的纸',('本人称便携打印机为小白',))),
 'C03-06': (
  ('E','episode','organization:东城图书馆南馆','闭馆时间误传','南馆上次闭馆时间误传',()),
  ('S','semantic','organization:东城图书馆南馆','合作信息确认','经值班台确认',('北馆规则不同，具体内容未提供',))),
 'C03-07': (
  ('E','episode','pet:家中猫豆豆','猫被误锁房间','家中猫豆豆上次躲进纸箱误锁房间',()),
  ('S','semantic','user:self','关门约定','关门前清点宠物',()),
  ('D','semantic','pet:朋友的狗豆豆','宠物身份','朋友的狗也叫豆豆',())),
 'C03-08': (
  ('E','episode','organization:海棠工坊','本人试课接口不兼容','本人参加海棠工坊试课，投影接头不兼容',()),
  ('S','semantic','organization:海棠工坊','课前约定','课前确认接口',()),
  ('D','episode','organization:他人转述的别家','转述的断电事件','他人转述别家断电',('本人未参与；仅记录转述，不是可信外部观察',))),
 'C03-09': (
  ('E','episode','project:客户新展板','展板色差','客户的新展板项目上次色差',()),
  ('S','semantic','project:客户新展板','印前约定','先看电子样',('旧包装项目直接印刷规则不适用',))),
 'C03-10': (
  ('E','episode','device:扫描仪P2','双面扫描漏页','标签P2的同型号扫描仪上次双面漏页',()),
  ('S','semantic','device:扫描仪P2','双面扫描约定','双面后核页码',('P1只有单面记录；不是同一设备',))),
 'C03-11': (
  ('E','episode','person:林舟','录音噪声','合作者曾用艺名山岚，上次录音背景噪声大',()),
  ('S','semantic','person:林舟','录音前约定','录音前关风扇',('曾用艺名山岚；现称林舟',))),
 'C03-12': (
  ('E','episode','place:杭州春日书店','取件晚到','杭州的春日书店上次取件晚到',()),
  ('S','semantic','place:杭州春日书店','到店前约定','确认营业',('同名上海店无该事件',))),
 'C03-13': (
  ('E','episode','person:原联络人小赵','交接文件漏传','与原联络人小赵交接时文件漏传',()),
  ('S','semantic','person:原联络人小赵','邮件约定','邮件列附件',('团队后来换联络人；约定主体仍小赵',))),
 'C03-14': (
  ('E','episode','organization:花木社','活动迟到','与花木社活动迟到因集合点写错',()),
  ('S','semantic','organization:花木社','集合通知约定','含定位链接',())),
 'C03-15': (
  ('E','episode','person:同学高远','餐馆等位','与高远吃饭因餐馆客满等位40分钟',()),
  ('S','semantic','person:同学高远','排队约定','并不介意排队，但至少提前告知',('高远明确说；不是从负面经历推断',))),
 'C03-16': (
  ('E','episode','person:合作者陶老师','出差临时接受语音稿','陶老师某次出差中临时接受语音稿',()),
  ('S','semantic','person:合作者陶老师','正常交稿约定','文字版',('语音稿仅某次出差的临时例外',))),
 'C03-18': (
  ('E','episode','organization:北岸North Pier','首单包装破损','合作方North Pier与中文北岸同一机构，首单包装破损',()),
  ('S','semantic','organization:北岸North Pier','后续包装约定','加护角',('North Pier与北岸同一机构',))),
 'C03-19': (
  ('E','episode','organization:本人导师介绍的装裱店','漏装背板','本人导师介绍的装裱店上次漏装背板',()),
  ('S','semantic','organization:本人导师介绍的装裱店','取件约定','验背板',('导师其他联系人无关',))),
}


SPECS.update({
 'C03-02': (
  ('E','episode','organization:晨风社青禾社','春季试刊漏目录','晨风社旧名青禾社，春季试刊曾漏目录',()),
  ('S','semantic','organization:晨风社青禾社','交稿约定','更名后交稿附目录',('晨风社旧名青禾社；别名有明确证据',))),
 'C03-17': (
  ('E1','episode','organization:蓝桥社','六月排练灯坏','蓝桥社六月排练灯坏',()),
  ('E2','episode','organization:蓝桥社','八月排练音箱故障','蓝桥社八月排练音箱故障',()),
  ('S','semantic','organization:蓝桥社','排练约定','八月后先试音',())),
})

# Original cases retained, not transformed into an easier state or deleted.
REQUIRES_SPECIAL_MAPPING = {
 'C03-20': 'D为无用户证据的模型推测；需真实ASSISTANT source与candidate/llm_inference/unverified',
}


def compile_c03_setup(case_id, setup_text, *, scenario_clock):
    """Strict setup-only compilation, pending common registry integration."""
    from hashlib import sha256
    from datetime import datetime
    from deskpet.quality.corpus_c01 import SetupBatch
    if type(case_id) is not str or case_id not in SETUPS:
        raise ValueError('corpus_unknown_setup')
    text,digest=SETUPS[case_id]
    if type(setup_text) is not str or setup_text != text or sha256(setup_text.encode()).hexdigest()!=digest:
        raise ValueError('corpus_setup_source_changed')
    if case_id in REQUIRES_SPECIAL_MAPPING:
        raise ValueError('corpus_c03_specific_mapping_required:'+REQUIRES_SPECIAL_MAPPING[case_id])
    clock=datetime.fromisoformat(scenario_clock)
    if clock.tzinfo is None:
        raise ValueError('corpus_clock_offset_required')
    default=('coarse_date=group_year/day15/noon/Asia_Shanghai;synthetic_day=true',) if case_id in ('C03-02','C03-17') else ('undated_past_episode=clock-24h',)
    return SetupBatch(case_id,text,digest,clock.timestamp(),SPECS[case_id],default)
