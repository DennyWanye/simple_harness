"""C05 authored setup only. Compilation is not disclosure or readiness proof.

No input/followup/gold fields are accepted. Labels remain setup-side mappings;
actual TaskScope IDs must come from the real context_route result.
"""
from dataclasses import dataclass, replace
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
    title: str | None
    goal: str | None = None
    next_step: str | None = None
    status: str = 'active'
    date_text: str | None = None
    project: str | None = None
    root_label: str | None = None
    alias: str | None = None

@dataclass(frozen=True)
class TaskSetupBatch:
    case_id: str
    setup_text: str
    setup_hash: str
    scopes: tuple[ScopeSeed, ...]
    requirements: tuple[str, ...]

# Only authored values. Missing titles/dates are not guessed from evaluation
# input. Labels never become actual IDs or a hint to the scoring model.
SPECS = {
 'C05-01': (ScopeSeed('A','self','资料归档',next_step='核备份',date_text='2025-11',project='纸质扫描'), ScopeSeed('B','self','资料归档',next_step='地点标签',date_text='2025-06',project='照片')),
 'C05-02': (ScopeSeed('A','self','旧录音整理',next_step='校对访谈名',date_text='2024春季'), ScopeSeed('B','self','音频转码',date_text='2026')),
 'C05-03': (ScopeSeed('A','self','秋季小展',next_step='核标签',alias='展览准备'), ScopeSeed('B','self','冬季展览')),
 'C05-04': (ScopeSeed('A','self','社区物品登记',goal='减少借还漏记',next_step='核对编号'), ScopeSeed('B','self','图书归档')),
 'C05-05': (ScopeSeed('A','self','读书节2024',next_step='归还展板',date_text='2024'), ScopeSeed('B','self','读书节2025',next_step='整理票据',date_text='2025')),
 'C05-06': (ScopeSeed('A','self','内容校对任务',project='网站整理'), ScopeSeed('B','self','图片压缩任务',project='网站整理')),
 'C05-07': (ScopeSeed('C','self','课程备课'), ScopeSeed('A','self','家庭藏书编目',next_step='查重复号')),
 'C05-08': (ScopeSeed('A','self','活动复盘',next_step='结账已完成',status='complete'), ScopeSeed('B','self','新活动')),
 'C05-09': (ScopeSeed('A','self','家谱排版',next_step='等待字体授权',status='paused'), ScopeSeed('B','self','相册裁剪',next_step='等照片',status='paused')),
 'C05-10': (ScopeSeed('B','self','旧书清点'), ScopeSeed('A','self','旧书修补',next_step='订书脊')),
 'C05-11': (ScopeSeed('B','self','海报打印'), ScopeSeed('A','self','海报校稿')),
 'C05-12': (ScopeSeed('A','self','照片编目'), ScopeSeed('B','other','照片编目'), ScopeSeed('C','self','其他摄影任务')),
 'C05-13': (ScopeSeed('A','self','捐书',next_step='清点'), ScopeSeed('B','self','旧书换购')),
 'C05-14': (ScopeSeed('A','self','工作坊筹备',next_step='核座位'), ScopeSeed('B','self','其他工作坊')),
 'C05-15': (ScopeSeed('A','self','相册校对',date_text='2026年8月'),),
 'C05-16': (ScopeSeed('A','self',None,project='纸质'), ScopeSeed('B','self',None,project='数码')),
 'C05-17': (ScopeSeed('A','self',None,next_step='验页码',root_label='展板-甲'), ScopeSeed('B','self',None,root_label='展板-乙')),
 'C05-18': (ScopeSeed('A','self',None,next_step='校对'),),
 'C05-19': (ScopeSeed('A','self',None,goal='处理笔记',root_label='workspace一'), ScopeSeed('B','self',None,goal='处理扫描',root_label='workspace二')),
 'C05-20': (ScopeSeed('A','self','彩页排版'), ScopeSeed('B','self','封面校对')),
}
REQUIREMENTS = {
 'C05-01': ('date_and_medium_disclosure',), 'C05-02': ('date_disclosure','recent_directory_excludes_A'),
 'C05-03': ('historical_title_alias',), 'C05-04': (), 'C05-05': ('date_disclosure',),
 'C05-06': ('shared_project_disclosure',), 'C05-07': ('scoring_active_C',),
 'C05-08': ('completed_read_only_open',), 'C05-09': (),
 'C05-10': ('public_pagination_B_then_A',), 'C05-11': ('public_order_B_then_A',),
 'C05-12': ('separate_owned_principal_B','permission_first_excludes_B'),
 'C05-13': ('separate_address_source_suppressed',), 'C05-14': (),
 'C05-15': ('date_disclosure','unproven_note_hidden'),
 'C05-16': ('synthetic_shared_title_and_month','predeclared_second_search_fields'),
 'C05-17': ('synthetic_title','exact_named_roots_no_parent'),
 'C05-18': ('synthetic_title','before_selection_revision待确认图片'),
 'C05-19': ('synthetic_title','independent_workspaces','scoring_unbound'), 'C05-20': ('scoring_unbound',),
}

def compile_c05_setup(case_id: str, setup_text: str) -> TaskSetupBatch:
    expected, digest = SETUPS[case_id]
    if type(setup_text) is not str or setup_text != expected:
        raise ValueError('c05_exact_setup_required')
    if hashlib.sha256(setup_text.encode()).hexdigest() != digest:
        raise ValueError('c05_setup_hash_differs')
    return TaskSetupBatch(case_id, setup_text, digest, SPECS[case_id], REQUIREMENTS[case_id])


def composed_goal(spec: ScopeSeed) -> str | None:
    """Authored date/medium/project/alias text carried by the only product field
    that TaskScope creation, FTS search and scope disclosure all expose (goal).

    Only when the authored goal is absent. Parts already inside the title are
    not repeated. No value is derived from the corpus input, followup or gold.
    """
    if spec.goal is not None:
        return None
    parts = [spec.date_text, spec.project, None if spec.alias is None else '曾用名：' + spec.alias]
    parts = [p for p in parts if p is not None and (spec.title is None or p not in spec.title)]
    return '；'.join(parts) if parts else None


def operational_spec(batch: TaskSetupBatch, label: str, *, phase='create') -> ScopeSeed:
    """One neutral fixture title for every missing title; never an answer label.

    Authored SPECS remain unchanged. Actual producer inputs explicitly identify
    this synthetic value, so it cannot be mistaken for original historical text.
    A missing goal is composed only from authored date/project/alias metadata.
    """
    if batch != compile_c05_setup(batch.case_id, batch.setup_text):
        raise ValueError('c05_exact_batch_required')
    matches = [s for s in batch.scopes if s.label == label]
    if len(matches) != 1:
        raise ValueError('c05_setup_label_invalid')
    spec = matches[0]
    goal = composed_goal(spec)
    if goal is not None:
        spec = replace(spec, goal=goal)
    if spec.title is None:
        spec = replace(spec, title='合成任务')
    if phase == 'before_selection' and batch.case_id == 'C05-18' and label == 'A':
        spec = replace(spec, next_step='待确认图片')
    elif phase != 'create':
        raise ValueError('c05_setup_phase_invalid')
    return spec


def operational_text(batch: TaskSetupBatch, label: str, *, phase='create') -> str:
    spec = operational_spec(batch, label, phase=phase)
    original = next(s for s in batch.scopes if s.label == label)
    text = batch.setup_text
    if original.title is None:
        text += '\n[统一合成fixture元数据，非原历史事实] 未提供的任务标题统一取“合成任务”。'
    if composed_goal(original) is not None:
        text += '\n[统一合成fixture元数据，非原历史事实] 未提供目标的任务，目标字段取setup已写明的日期/介质/项目/曾用名文本。'
    if phase == 'before_selection':
        text += '\n[执行原setup预先声明的阶段] 现在将原任务下一步从“校对”修订为“待确认图片”。'
    return text
