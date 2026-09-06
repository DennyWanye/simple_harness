"""C05 authored setup only. Compilation is not disclosure or readiness proof.

No input/followup/gold fields are accepted. Labels remain setup-side mappings;
actual TaskScope IDs must come from the real context_route result.
"""
from dataclasses import dataclass
import hashlib

SETUPS = {'C05-01': ('本人A：资料归档，2025-11纸质扫描，下一步核备份；B：同名2025-06照片，下一步地点标签；均有合格披露来源。',
            'd98cbed48ec00d379a74aa99f88033456ce7b443c72e2b192cdfe023803f49e4'),
 'C05-02': ('A：2024春季旧录音整理，下一步校对访谈名；B：2026音频转码。最近目录不含A。',
            'd628b4d9747301ef248805d5be09c484b241e0c2ecbecced6b3953736fa16013'),
 'C05-03': ('A旧名“展览准备”现名“秋季小展”，别名可检索，下一步核标签；B：冬季展览。',
            '2773908db3854f7a801c95ac61f96daa454a61e5ad9c1e1d3aa606e2b1d6b1e8'),
 'C05-04': ('A：社区物品登记，目标为减少借还漏记，下一步核对编号；B：图书归档。',
            'ae738d8caf4275d8825767e8eb8ad0b88ba7e7b71ed7a201bac69f311fe1332d'),
 'C05-05': ('A：读书节2024，下一步归还展板；B：读书节2025，下一步整理票据。',
            'd0045b31f479a7b6feb435236cb5585370acb42728a07cb30539e0ee5cb9c3d3'),
 'C05-06': ('A：网站整理的内容校对任务；B：同项目图片压缩任务；binding可相同但scope不同。',
            '5d64942f22df8151a5f84a111467adc6dddeeb83f4ca77297177151318d603d9'),
 'C05-07': ('当前active为课程备课C；A旧任务为家庭藏书编目，下一步查重复号。',
            '39f53f3668cb49ee3f0ea1ebc7770b6b57b00dd998cd0cdd0d82674f45288ac8'),
 'C05-08': ('A：已完成活动复盘，允许受控只读恢复档案，末步为结账已完成；B：进行中的新活动。',
            '279ad3cd7fe3a36c991550db0ec491fd52774f017cbcd499a6dea1adf98d56a0'),
 'C05-09': ('A暂停的家谱排版，等待字体授权；B暂停的相册裁剪，等照片。',
            '0250928bd57283d6cb4071a767cd9bbd818384791c1198846c42262741591d7a'),
 'C05-10': ('搜索结果分页；首批有旧书清点B，第二页有目标旧书修补A；A下一步订书脊。',
            'cf4a3299fa0251a7fabfb491fd1a02cf94ad1fb0fab6fbca7692cfb24115fad9'),
 'C05-11': ('A：海报校稿；B：海报打印；搜索故意按B、A顺序，ID不含正确标签。',
            'fb132f5221689485bca9bd649d5d086e5606c03c4aeea07cef71a6febf534c56'),
 'C05-12': ('A本人照片编目；B另一主体同名，permission-first禁止返回；C本人其他摄影任务。',
            'bdc07bc70b8ea23e26c82b087e9eb0f7564a3810c2a6dd3a1987d0610221d8cd'),
 'C05-13': ('A项目捐书：标题/下一步清点合格；一段家庭地址已suppressed；B项目旧书换购。',
            'ee31691b884088234d12ec1b4bf74019d6209474f04541d9780fad77cdb69939'),
 'C05-14': ('A工作坊筹备，合格preview含“下一步核座位”；B其他工作坊；预览带真实disclosure来源。',
            '7963407254798231efdcee251a00bfd4c859b1efb568567fd361ae7fb992082e'),
 'C05-15': ('A任务有标题“相册校对”，公开日期明确为2026年8月；该日期由设置端预先建立并可经reader披露，不得从followup或gold反推seed。一段备注因缺provenance被reader隐藏，非消歧所需。',
            '0114f78a21a57a3ff58b18dce517a39325aad186badb99c08ec560a24c1105fd'),
 'C05-16': ('A/B同名同月；首次披露仅标题月份，不足区分；经允许的补充搜索可得主题纸质/数码。 f1后的预置补充搜索应允许披露主题差异；runner不临时修改权限来补答案。',
            'bc558d3b8f1cd637b6b890a2dc5be8934a733dd1b816cec069bc5bafc5e17147'),
 'C05-17': ('A绑定exact root“展板-甲”，B绑定相似“展板-乙”；公共父目录未授权；A下一步验页码。',
            '11b6f52e522ca82f56ea54b98370d83c5ff8871a297c3fdf9b83dbfa1d19793b'),
 'C05-18': ('A初次预览next=校对；用户选择前受控setup追加当前revision为待确认图片；需重取当前披露。 '
            'fixture_action固定追加这一已写定的新revision，与模型选择/gold无关。',
            'ee6c656133872b990240c371dfd334202a3e8b96acb2ae977bc00f94c32cfdaf'),
 'C05-19': ('A在workspace一处理笔记，B在workspace二处理扫描，主体相同；当前无目标绑定。',
            '5943d0b6b203255769a7963a9340d215d8e0f8891ce6db7c2592268c142af357'),
 'C05-20': ('A为彩页排版，B为封面校对，均合格；无预先已选任务。', '0f8b535f46c4822e717938befd20ae46c633e026654830baf91ab579bba30578')}

@dataclass(frozen=True)
class ScopeSeed:
    label: str
    owner: str
    title: str
    goal: str | None = None

@dataclass(frozen=True)
class TaskSetupBatch:
    case_id: str
    setup_text: str
    setup_hash: str
    scopes: tuple[ScopeSeed, ...]

# Initial narrow cases require no invented next-step/status/date. create_new's
# ordinary absent-goal default is a structural product default, not new fact.
SPECS = {
    'C05-06': (ScopeSeed('A', 'self', '网站整理的内容校对任务'),
               ScopeSeed('B', 'self', '图片压缩任务')),
    'C05-12': (ScopeSeed('A', 'self', '照片编目'),
               ScopeSeed('B', 'other', '照片编目'),
               ScopeSeed('C', 'self', '其他摄影任务')),
    'C05-20': (ScopeSeed('A', 'self', '彩页排版'),
               ScopeSeed('B', 'self', '封面校对')),
}

def compile_c05_setup(case_id: str, setup_text: str) -> TaskSetupBatch:
    expected, digest = SETUPS[case_id]
    if type(setup_text) is not str or setup_text != expected:
        raise ValueError('c05_exact_setup_required')
    if hashlib.sha256(setup_text.encode()).hexdigest() != digest:
        raise ValueError('c05_setup_hash_differs')
    if case_id not in SPECS:
        raise ValueError('c05_lifecycle_or_disclosure_setup_not_implemented')
    scopes = SPECS[case_id]
    if any(scope.title not in setup_text for scope in scopes):
        raise ValueError('c05_title_not_in_source')
    return TaskSetupBatch(case_id, setup_text, digest, scopes)
