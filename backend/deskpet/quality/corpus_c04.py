"""C04 exact setup-only temporal mapping; no gold, input or scoring fields.

Synthetic clock concretization preserves original precision in public payloads.
No event is asserted to have occurred by constructing a pending event trigger.
"""
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

import simple_harness as h
from deskpet.task_scope.protocol import canonical_hash

# label, kind, authored action/result, local time anchor, timezone, precision,
# authored date text. Anchors are setup metadata, not precise historical claims.
SPECS = {
 'C04-01': (('E','episode','验样封面偏暗','2026-09-04T12:00','Asia/Shanghai','day','9月4日'), ('P','prospective','索取修正版','2026-09-07T09:00','Asia/Shanghai','minute','9月7日09:00')),
 'C04-02': (('E','episode','盘点缺两本','2026-08-31T12:00','Asia/Shanghai','day','8月31日'), ('P','prospective','补登记','2026-09-08T10:00','Asia/Shanghai','minute','9月8日10:00')),
 'C04-03': (('E','episode','整理年册缺照片','2025-12-31T12:00','Asia/Shanghai','day','2025年12月31日'), ('P','prospective','补拍年册素材','2027-01-02T10:00','Asia/Shanghai','minute','2027年1月2日10:00')),
 'C04-04': (('E','episode','试用导出成功','2026-09-05T12:00','Asia/Shanghai','day','9月5日'), ('P','prospective','清点导出副本','2026-09-10T15:00','Asia/Shanghai','minute','9月10日15:00')),
 'C04-05': (('E1','episode','打印偏色','2026-09-05T09:00','Asia/Shanghai','minute','9月5日09:00'), ('E2','episode','校色通过','2026-09-05T15:00','Asia/Shanghai','minute','当天15:00'), ('P','prospective','领取校色样','2026-09-07T11:00','Asia/Shanghai','minute','9月7日11:00')),
 'C04-06': (('E','episode','远程访谈音频缺段','2026-09-04T12:00','Asia/Shanghai','day','9月4日'), ('P','prospective','补录','2026-09-08T09:00','Europe/London','minute','9月8日09:00 Europe/London')),
 'C04-07': (('E','episode','因页码错序返工','2026-08-20T12:00','Asia/Shanghai','day','8月20日'), ('P','prospective','复核','2026-09-09T14:00','Asia/Shanghai','minute','9月9日14:00')),
 'C04-08': (('E','episode','交接少一份表','2026-09-05T12:00','Asia/Shanghai','day','9月5日'), ('P','prospective','补送表格','2026-09-07T08:30','Asia/Shanghai','minute','9月7日08:30')),
 'C04-09': (('E1','episode','修复缺页','2026-08-24T12:00','Asia/Shanghai','week','8月24–30周'), ('E2','episode','发现重复封面','2026-08-31T12:00','Asia/Shanghai','week','8月31–9月6周'), ('P','prospective','复核封面','2026-09-08T12:00','Asia/Shanghai','day','9月8日')),
 'C04-10': (('E','episode','上次借书归还时漏带借阅卡','2026-09-05T10:00','Asia/Shanghai','undated','上次'), ('P','prospective','归档借阅回执',None,None,'event','下一次归还成功')),
 'C04-11': (('E','episode','试印失败，颜色不符','2026-09-05T12:00','Asia/Shanghai','day','9月5日'), ('P','prospective','寄正式样',None,None,'event','试印验收成功')),
 'C04-12': (('E','episode','因场地检修推迟讨论','2026-09-04T12:00','Asia/Shanghai','day','9月4日'), ('P_OLD','prospective','旧讨论提醒（原文未指定正文）','2026-09-07T12:00','Asia/Shanghai','day','旧9月7日提醒')),
 'C04-13': (('E','episode','申请材料核对缺签名','2026-09-03T12:00','Asia/Shanghai','day','9月3日'), ('P','prospective','补签（截止日9月10日）','2026-09-09T10:00','Asia/Shanghai','minute','9月9日10:00')),
 'C04-14': (('E','episode','第一阶段数据去重完成；第二阶段格式检查未开始','2026-09-05T10:00','Asia/Shanghai','undated','未指定日期'), ('P','prospective','开始格式检查','2026-09-08T14:00','Asia/Shanghai','minute','9月8日14:00')),
 'C04-15': (('E','episode','三季度清点缺备份索引','2026-09-30T16:00','Asia/Shanghai','minute','2026年9月30日16:00'), ('P','prospective','补索引','2026-10-02T10:00','Asia/Shanghai','minute','2026年10月2日10:00')),
 'C04-16': (('E1','episode','首次试课麦克风失效','2026-07-15T12:00','Asia/Shanghai','month','7月首次'), ('E2','episode','最近试课计时超长','2026-09-04T12:00','Asia/Shanghai','day','9月4日最近'), ('P','prospective','缩短练习','2026-09-07T12:00','Asia/Shanghai','minute','9月7日12:00')),
 'C04-17': (('E','episode','团体出游因天气取消','2026-08-15T12:00','Asia/Shanghai','month','8月'), ('P','prospective','核对退回押金','2026-09-10T16:00','Asia/Shanghai','minute','9月10日16:00'), ('P_OLD','prospective','旧出发提醒','2026-08-16T12:00','Asia/Shanghai','synthetic_old_departure','旧出发提醒（无日期）')),
 'C04-18': (('E','episode','修订封面缺作者名','2026-08-12T12:00','Asia/Shanghai','day','2026年8月12日'), ('P','prospective','交修正版','2026-09-12T09:00','Asia/Shanghai','minute','2026年9月12日09:00')),
 'C04-19': (('E','episode','测试已开始；执行记录中结果尚未确认','2026-09-04T12:00','Asia/Shanghai','day','9月4日'), ('P','prospective','追问测试结果','2026-09-08T10:00','Asia/Shanghai','minute','9月8日10:00')),
 'C04-20': (('E','episode','夜间传稿漏附件','2026-09-04T21:00','Asia/Shanghai','night','9月4日夜间'), ('P','prospective','补附件','2026-09-08T00:30','Asia/Shanghai','minute','9月8日00:30 Asia/Shanghai')),
}


def timestamp(local, zone='Asia/Shanghai'):
    parsed = datetime.fromisoformat(local)
    if parsed.tzinfo is not None:
        raise ValueError('c04_local_anchor_requires_named_zone')
    aware = parsed.replace(tzinfo=ZoneInfo(zone))
    if datetime.fromtimestamp(aware.timestamp(), ZoneInfo(zone)).replace(tzinfo=None) != parsed:
        raise ValueError('c04_nonexistent_local_time')
    return float(aware.timestamp())


@dataclass(frozen=True)
class TemporalSetupBatch:
    case_id: str
    setup_text: str
    setup_hash: str
    scenario_time: float
    ingestion_time: float
    specs: tuple


def compile_c04_setup(case_id, setup_text, *, scenario_clock):
    if case_id not in SETUPS:
        raise ValueError('c04_unknown_case')
    text, digest = SETUPS[case_id]
    if setup_text != text or sha256(setup_text.encode()).hexdigest() != digest:
        raise ValueError('c04_setup_source_differs')
    actual = datetime.fromisoformat(scenario_clock)
    expected = datetime.fromisoformat(SCENARIO_CLOCKS[case_id])
    if actual.tzinfo is None or actual != expected:
        raise ValueError('c04_scenario_clock_differs')
    ingestion = (timestamp('2026-09-05T12:00') if case_id == 'C04-07' else
        timestamp('2026-09-30T17:00') if case_id == 'C04-15' else expected.timestamp())
    return TemporalSetupBatch(case_id, text, digest, expected.timestamp(), ingestion, SPECS[case_id])


def temporal_payload(batch, spec):
    label, kind, text, local, zone, precision, authored = spec
    if precision != 'minute':
        text += f'【原时间={authored}；精度={precision}；具体日时仅synthetic fixture锚点，非原文事实或评分答案】'
    if kind == 'episode':
        return h.EpisodeMemoryPayload(text, ('user:self',), (), (text,), (), (), timestamp(local, zone), None, None)
    if precision == 'event':
        # An unresolved fixture trigger namespace is not an observed event or a
        # product publisher grant. No resolver/signal is installed for it.
        condition = authored
        trigger = h.ProspectiveEventTrigger('corpus:unobserved-event:' + canonical_hash([batch.setup_hash, label]),
            condition, canonical_hash(condition))
    else:
        trigger = h.ProspectiveTimeTrigger(timestamp(local, zone), zone)
    return h.ProspectiveMemoryPayload(text, trigger)

SETUPS = {'C04-01': ('E：9月4日验样封面偏暗；P：9月7日09:00上海时区提醒索取修正版，pending。',
            'e8e1ccf8078c4628cf75a2b2e1e2d24ce9a5660e5d7e60e5a577e2fb7c27ead7'),
 'C04-02': ('E：8月31日盘点缺两本；P：9月8日10:00提醒补登记，pending。',
            'e4eb3c3dabb08e5388e1383e553e7c89c23265e328ccec6fea06671c033fd1fd'),
 'C04-03': ('E：2025年12月31日整理年册缺照片；P：2027年1月2日10:00提醒补拍年册素材，pending。',
            'c9789268ea626811c4067a75d89dcaf1e814966929e3ea21b91a3a968d466fb0'),
 'C04-04': ('E：9月5日试用导出成功；P：9月10日15:00提醒清点导出副本，pending。',
            '25c1411a6285a8b5b177160eab0da3936bad90c6d724c46498f3bb77b5c06a14'),
 'C04-05': ('E1：9月5日09:00打印偏色；E2：当天15:00校色通过；P：9月7日11:00提醒领取校色样。',
            '1709c62acbeb96ea9b7a93f25c4de43433416845a72ffe4f30dd5aad93b945f9'),
 'C04-06': ('E：9月4日远程访谈音频缺段；P：9月8日09:00 Europe/London提醒补录，来源有明确时区。',
            '5bc9e7beef8dd27843098d116f25099fe1546a9ba343bf62e68f326cd011fbea'),
 'C04-07': ('E：8月20日因页码错序返工，9月5日才录入；P：9月9日14:00提醒复核，pending。',
            'd7210c874e5502b1d5bb9ec76570349e8d502683a1f546ce7c22a0fc5f0679ab'),
 'C04-08': ('E：9月5日交接少一份表；P：9月7日08:30提醒补送表格，pending。',
            'ed5bd9433a82846f4c843c28d24520b4070871d2d4087da3e9044aeef4b52308'),
 'C04-09': ('E1：8月24–30周修复缺页；E2：8月31–9月6周发现重复封面；P：9月8日提醒复核封面。',
            'be63faf9d69184a54ededf8414b4895e99e82db0dc1e406828ac77890dfb4b1b'),
 'C04-10': ('E：上次借书归还时漏带借阅卡；P：下一次归还成功后提醒归档借阅回执，pending事件触发。',
            'bce5a07a2f97ee38793d604182c7b01efd6606151290dcd380209dbbbeaf3f40'),
 'C04-11': ('E：9月5日试印失败，颜色不符；P：试印验收成功后提醒寄正式样，pending。',
            'ba02534c75a323ea642360d872e418df774d47674c01031d44b3b4a9e03b6280'),
 'C04-12': ('E：9月4日因场地检修推迟讨论；P：已rescheduled为9月9日09:30提醒确认新场地；旧9月7日提醒superseded。',
            '9dcfe3136166ce5a1560557e6e21016b5c4d8dab012d5def93eacb75df268e4c'),
 'C04-13': ('E：9月3日申请材料核对缺签名；P：9月9日10:00提醒补签，截止日9月10日。',
            '40962ffc9318ed52f1e8c1422d36a62b67836ae4decd55f7f47d48904fe05977'),
 'C04-14': ('E：第一阶段数据去重完成，第二阶段格式检查未开始；P：9月8日14:00提醒开始格式检查。',
            'bcfb3645d4764b95ab271e2da98a852e40a09a8495f30f5567462bb51660f798'),
 'C04-15': ('E：2026年9月30日16:00三季度清点缺备份索引，17:00已入账；P：2026年10月2日10:00提醒补索引，pending。',
            'c30dfa3bbeb192d95c4d3753ccbebfe4be7ede39e4d007c09a0e7d60b8bcaeb2'),
 'C04-16': ('E1：7月首次试课麦克风失效；E2：9月4日最近试课计时超长；P：9月7日12:00提醒缩短练习。',
            '4ea489611a39e8fa139b0cbc4a16e07ad6420d14526abefaac221ad5561b544c'),
 'C04-17': ('E：8月团体出游因天气取消；P：9月10日16:00提醒核对退回押金，pending；旧出发提醒已取消。',
            'fffa46e573727f429157307555f1c8e7966ba6ab487d9e4e4250b3d3dc4ffa09'),
 'C04-18': ('E：2026年8月12日修订封面缺作者名；P：2026年9月12日09:00提醒交修正版。',
            '918953030d6280da5e71f7f512cf1a9c8761aed11e342bdf52bc6cebbcd325af'),
 'C04-19': ('E：9月4日测试已开始但执行记录中结果尚未确认；P：9月8日10:00提醒追问测试结果。',
            '4aab60a0e2b77076ebfddf87eef5989964aeb0a3b076b978f91d8a1eff58e696'),
 'C04-20': ('E：9月4日夜间传稿漏附件；P：9月8日00:30 Asia/Shanghai提醒补附件，原始trigger仅记录上海时间；UTC结果由计分端独立换算核对，不在初始提示补答案。',
            '8938a3a5b162031b9d2a60242a1b048d5c91a2af05f10702fa50ad84aa6f7564')}

SCENARIO_CLOCKS = {'C04-01': '2026-09-06T10:00:00+08:00',
 'C04-02': '2026-09-06T10:00:00+08:00',
 'C04-03': '2026-09-06T10:00:00+08:00',
 'C04-04': '2026-09-06T10:00:00+08:00',
 'C04-05': '2026-09-06T10:00:00+08:00',
 'C04-06': '2026-09-06T10:00:00+08:00',
 'C04-07': '2026-09-06T10:00:00+08:00',
 'C04-08': '2026-09-06T10:00:00+08:00',
 'C04-09': '2026-09-06T10:00:00+08:00',
 'C04-10': '2026-09-06T10:00:00+08:00',
 'C04-11': '2026-09-06T10:00:00+08:00',
 'C04-12': '2026-09-06T10:00:00+08:00',
 'C04-13': '2026-09-06T10:00:00+08:00',
 'C04-14': '2026-09-06T10:00:00+08:00',
 'C04-15': '2026-09-30T18:00:00+08:00',
 'C04-16': '2026-09-06T10:00:00+08:00',
 'C04-17': '2026-09-06T10:00:00+08:00',
 'C04-18': '2026-09-06T10:00:00+08:00',
 'C04-19': '2026-09-06T10:00:00+08:00',
 'C04-20': '2026-09-06T10:00:00+08:00'}
