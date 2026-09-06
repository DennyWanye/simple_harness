"""C09 setup-only values; no current input, gold, or provider reply is accepted.

Explicit old/new scalar facts are fixtures of the original authored setup.
Procedure replacement is separate and remains unsupported here.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import math

from deskpet.task_scope.protocol import canonical_hash

SETUPS = {'C09-01': ('同一服务旧端口7342已superseded，当前head8451。',
            '6f20123db86d487d464300758670e1e3c19620fa2873cae20c964b83994311f5'),
 'C09-02': ('旧地址为松木路8号，明确更正后已superseded；当前地址由用户提供。',
            '55b1aa5229b592a9840b81aba6d493151612cc2fc9ecd4db230c452d224d95d0'),
 'C09-03': ('文稿旧题“河岸散记”已被“河岸观察”替代。', '139b34e3e4d9c8b21f0b8438b9d038d517f1e220cc7a73beddef24984afc6fe1'),
 'C09-04': ('同项目旧负责人小梁已更为小顾，当前head已更新。', 'f30f4cdc7986bf3091eebbf83d3d7e7cccd5fbc4ad3a0dd80fecfcee7e2ab39e'),
 'C09-05': ('旧日志默认UTC已被Asia/Shanghai取代。', '0df395845064f7b21ae5034d58b8d5d2bd364c39dfa3bef0afa930ba202ad2df'),
 'C09-06': ('旧接口/v1/export已superseded，新值/v2/exports。',
            '8731ff6e2e8be4f58805d67fff9f36ae1e0f65311cfafdec962abb7e55649885'),
 'C09-07': ('旧采购上限500已被300取代；同一预算事实有纠正链。',
            'f124d826f8b62183dd2caa36e8aad5dff36faf80ebe48365a547a7b01e5fe1b4'),
 'C09-08': ('旧会议周三14:00已被周四15:00替代。', 'a501295c6ef15c756ac24f3ca9de56346429288b7e8418103535da524fddd275'),
 'C09-09': ('旧称呼“主任”已superseded，当前本人要求叫“小陈”。',
            'a0b0cab49cb663cef59cbd97190a5c68b526d8be3b86b1516f77c6192ecc8190'),
 'C09-10': ('本人旧长期表格要求已被短列表替代。', 'c54286a587337daf59e6a5f8addb10c02a013dfa7b9f9a57100d564d1861fa2c'),
 'C09-11': ('部署文档旧版本2.4已由2.6替代，旧head不可当前使用。',
            'b5a3f343852169b6e8e6f2b77e73b2ddc8d15ae6ed4ea71d48f20d8119d1b111'),
 'C09-12': ('旧记录单位毫米已更正为厘米；当前读数带单位。', '396e0236f73ccab5ce38767e1544972b228b503c5f605c6a53530c1b1357a7b5'),
 'C09-13': ('旧程序中的“附纸质副本”步骤已明确取消并superseded；其余不要求历史检索。',
            '255c05461dca5d582aacaaa5a2fb5390a85dc4ad6ea4dc1f667476557c793505'),
 'C09-14': ('审批角色旧为项目负责人，新为值班协调员，旧授权规则已替代。',
            'e4b683fba8767b31e5d3e6a2801940cecaea82c93c9533f80e0fbdc6721df607'),
 'C09-15': ('旧截止9月12日已被9月9日替代。', '7a523f2b6392165db6e06811b3999a17ee125729d2d95756108a5d5f4b193c83'),
 'C09-16': ('送件地点旧A座已改B座；送件时间10:00未变化，有独立当前事实。',
            '6bc8eb71abf48123c672daab7b5182f28343b1b8753c4cb3941b0267ec98d3c1'),
 'C09-17': ('旧地址有楼层3层但整条旧地址已superseded；新地址仅登记青海楼，楼层未知。',
            'cce77918effa8e0ec8f33c6aff602d3603d45604ea8d0c76f5d19a87af91c28f'),
 'C09-18': ('旧通用命名规则曾要求拼音，已废；当前允许本次中文标题。',
            '616ef02d40c6753c9b42f5bf596b03e5e5601c6913f50b41c1eb16d12d52a3ce'),
 'C09-19': ('旧编号AB-018已纠正为AB-081；旧事实superseded。',
            'a5456501322c01bce4202f195e0c86f0a4143f91f6ca8e02ca91925a07d93d7c'),
 'C09-20': ('旧单价12元/数量4均已分别纠正为单价15元/数量3。',
            '48a667718b8d4cd52f6320a677094b841c767094f0e8be9a99c8f2474f81a637')}

# (predicate, old value, new value or None, qualifiers). None means the old
# claim is explicitly retired without inventing a successor from current input.
CHANGES = {
    'C09-01': (('service_port', '7342', '8451', ('同一服务',)),),
    'C09-02': (('address', '松木路8号', None, ()),),
    'C09-03': (('document_title', '河岸散记', '河岸观察', ('文稿',)),),
    'C09-04': (('project_owner', '小梁', '小顾', ('同项目',)),),
    'C09-05': (('log_timezone', 'UTC', 'Asia/Shanghai', ()),),
    'C09-06': (('export_endpoint', '/v1/export', '/v2/exports', ()),),
    'C09-07': (('purchase_limit', '500', '300', ()),),
    'C09-08': (('meeting_time', '周三14:00', '周四15:00', ()),),
    'C09-09': (('preferred_name', '主任', '小陈', ()),),
    'C09-10': (('output_format', '表格', '短列表', ()),),
    'C09-11': (('deployment_version', '2.4', '2.6', ()),),
    'C09-12': (('measurement_unit', '毫米', '厘米', ()),),
    'C09-14': (('approval_role_description', '项目负责人', '值班协调员', ()),),
    'C09-15': (('deadline', '9月12日', '9月9日', ()),),
    'C09-16': (('delivery_place', 'A座', 'B座', ()),),
    # Only the authored known part of the old address is materialized.
    'C09-17': (('address', '楼层3层', '青海楼；楼层未知', ()),),
    'C09-18': (('naming_rule', '拼音', None, ()),),
    'C09-19': (('reference_code', 'AB-018', 'AB-081', ()),),
    'C09-20': (('unit_price', '12', '15', ('元',)), ('quantity', '4', '3', ())),
}
UNCHANGED = {'C09-16': (('delivery_time', '10:00', ()),)}


@dataclass(frozen=True)
class SupersededSetup:
    case_id: str
    setup_text: str
    setup_hash: str
    scenario_time: float
    changes: tuple
    unchanged: tuple
    specs: tuple
    manifest_hash: str


def compile_c09_setup(case_id, setup_text, *, scenario_clock):
    if type(case_id) is not str or case_id not in SETUPS:
        raise ValueError('c09_unknown_setup')
    original, digest = SETUPS[case_id]
    if type(setup_text) is not str or setup_text != original or sha256(setup_text.encode()).hexdigest() != digest:
        raise ValueError('c09_setup_source_changed')
    if case_id not in CHANGES:
        raise ValueError('c09_procedure_successor_not_prepared')
    instant = datetime.fromisoformat(scenario_clock)
    if instant.tzinfo is None or not math.isfinite(instant.timestamp()) or instant.timestamp() < 0:
        raise ValueError('c09_trusted_aware_clock_required')
    changes, unchanged = CHANGES[case_id], UNCHANGED.get(case_id, ())
    specs = tuple((f'old-{i}', 'semantic', 'user:self', predicate, value, qualifiers)
        for i, (predicate, value, _, qualifiers) in enumerate(changes)) + tuple(
        (f'unchanged-{i}', 'semantic', 'user:self', predicate, value, qualifiers)
        for i, (predicate, value, qualifiers) in enumerate(unchanged))
    manifest = canonical_hash(dict(domain='host:corpus-c09/v1', case_id=case_id,
        setup_hash=digest, scenario_time=instant.timestamp(), changes=changes,
        unchanged=unchanged, specs=specs))
    return SupersededSetup(case_id, original, digest, instant.timestamp(), changes, unchanged, specs, manifest)


def validate_c09_setup(batch):
    if type(batch) is not SupersededSetup or batch != compile_c09_setup(batch.case_id,
            batch.setup_text, scenario_clock=datetime.fromtimestamp(batch.scenario_time, timezone.utc).isoformat()):
        raise ValueError('c09_exact_compiled_setup_required')
