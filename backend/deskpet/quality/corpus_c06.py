"""C06 setup-only manifest. No initial, followup, gold, or scoring imports.

All twenty authored setups remain present; mapped preparation is separate from
actual runtime readiness. A personal global memory is not cross-owner authority.
"""
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import math

SETUPS = {'C06-01': ('S：本人个人整理统一中文Markdown；P：先列材料再标主题后整理，个人文本通用；均来自旧任务。',
            '42af61e8c48bf469a7ca175a83133d6240c786e2e318fee95e329f26d2428d55'),
 'C06-02': ('S：本人日期统一ISO格式；P：排日程先核时区再核日期重叠，跨项目通用。',
            '6e9f0930f7297f76f70976e5deaa1e412ba6f691f8b32694ea95e71626299d18'),
 'C06-03': ('S：本人金额保留两位小数；P：预算草表先分固定/浮动再核总额，通用低风险。',
            'dd9414ec06398503d92502405d5ed2d7fe5580c692908362b093b11a363ccdce'),
 'C06-04': ('S：本人手机会议摘要不用表格；P：摘要先列决定后列负责人和待确认点。',
            'c2e2d120eaa09bd2e55f5852537fab30008086870b089b5426313e8f64f90d4f'),
 'C06-05': ('S：本人外出素材先用小体积预览；P：压缩前核原件副本再核可读性；只描述不做。',
            '981ada31d3dffbd46eedd936602c429d761c39a03886f29fc9b16c6bd386ec50'),
 'C06-06': ('S：本人图表要配文字说明；P：文档检查按标题层级→阅读顺序→替代文本。',
            'c9190475b26362c934eb6a1c61c8149c2cf301ebee9e28570f5983eb2ee95a15'),
 'C06-07': ('S：本人批次文件名日期_主题；P：整理前清单预览、确认对应再执行，通用程序。',
            'c418510efed1e8321d356b479b3eb5bfec80419e6b9e653c57a864fdb32097ee'),
 'C06-08': ('S：本人方案说明先结论；P：比较时列约束、给候选、指出取舍，通用。',
            'e16bc23a6c8f41e4c99cc0b022b3f540be8525c593b0b74c74bb6ec0ecf60d0f'),
 'C06-09': ('S：本人晚间不安排噪声活动；P：排日程先列固定项再放可移动项。',
            '31b352eb3f695c5e2204470956d107a5eaa6cfe708e55f302ff4d459051833c0'),
 'C06-10': ('S：本人学习资料要离线可读；P：准备时先列来源再核授权最后核离线副本。',
            '8cf97a9eca34c83fd542e836fe377ef4cb85acf80fa8db75de07adf8458e1c23'),
 'C06-11': ('S：本人教学示例优先Python；P：讲解先最小运行例再解释边界，跨课程。',
            'cb68769315e5cb928812381c9b7093f7d8de69dd6a2ca7f992d9c940104f65f5'),
 'C06-12': ('S：本人摘录需保留来源链接；P：整理时分原话与概括再标主题，跨项目。',
            '644945b50b04d93b760528fc8ba8cdde7206a0e1ce7e45b331c54647a0370f89'),
 'C06-13': ('S：本人书签标签不超过两个层级；P：先按URL对照再看标题，重复项只列候选不删除。',
            'aa8ff27ad6d4867cf4c24031ef3e71a141b395585c7bfb9df2adeeb60471e2e9'),
 'C06-14': ('S：本人公开草稿不放个人联系方式；P：发布前草稿检查出处、私密字段、版本号，需用户后续确认发送。',
            '99cab40e7cd69fcfd9dde6e163ca23d8407fe0021d7f6ca111f5cc949de7b9f0'),
 'C06-15': ('S：本人测量记录用厘米；P：录入前核单位、核量程、标异常，通用手工程序。',
            'ef882d37f977ed3fc90055255924f8b3cab8d59f48e743ef38c1f06f6aa75e34'),
 'C06-16': ('S：本人每段学习材料不超过十分钟；P：按目标拆单元，先基础后练习，跨课程通用。',
            '4d4f8bd5476aabe42feadc7a2d9d0f6abc8cd142e3d567a7cbb0a7e96f91962f'),
 'C06-17': ('S：本人报告先风险；P：通用复核先清单后抽样；E：旧项目跳过抽样导致出错。',
            '9a73e9aeb6c09384eea247ff4da83d67b5e197e529d820423456e2fbf9e65c2e'),
 'C06-18': ('S：本人普通笔记用Markdown；P1：全局先列来源后概括；P2：仅财务项目先套专有账模板。',
            'ace4f656d7ad491c692e1c71f423de0832c569c53c9418c5335d3751aab59e29'),
 'C06-19': ('S：本人离线环境只用纯文本；P1：通用手工清单核对；P2：需联网插件的active程序；当前设备离线。',
            'b1f749a0fb7f20ed80f9c2919848993193840332a15eec01543f37bd4b5757ea'),
 'C06-20': ('S：本人索引保留原文件名；P：若材料有日期先按日期分组，无日期先按主题，通用。',
            'f85d9353ab3216e2e5ba57142d925b1934e1ab9beef5923f2dc10d976e1e12e3')}

@dataclass(frozen=True)
class C06Batch:
    case_id: str
    setup_text: str
    setup_hash: str
    scenario_time: float

# Exact authored spans; labels never become fabricated SDK memory IDs.
# Title/conditions/steps operationalize only the setup's explicit procedure.
SPECS = {
    'C06-02': ('本人日期统一ISO格式', '日期格式', 'ISO格式',
        '排日程先核时区再核日期重叠，跨项目通用', '排日程',
        ('排日程',), ('先核时区', '再核日期重叠')),
    'C06-03': ('本人金额保留两位小数', '金额精度', '保留两位小数',
        '预算草表先分固定/浮动再核总额，通用低风险', '预算草表',
        ('预算草表',), ('先分固定/浮动', '再核总额')),
    'C06-04': ('本人手机会议摘要不用表格', '手机会议摘要格式', '不用表格',
        '摘要先列决定后列负责人和待确认点', '会议摘要',
        ('会议摘要',), ('先列决定', '后列负责人和待确认点')),
    'C06-06': ('本人图表要配文字说明', '图表说明', '要配文字说明',
        '文档检查按标题层级→阅读顺序→替代文本', '文档检查',
        ('文档检查',), ('标题层级', '阅读顺序', '替代文本')),
    'C06-08': ('本人方案说明先结论', '方案说明顺序', '先结论',
        '比较时列约束、给候选、指出取舍，通用', '方案比较',
        ('比较方案',), ('列约束', '给候选', '指出取舍')),
    'C06-09': ('本人晚间不安排噪声活动', '晚间活动约定', '不安排噪声活动',
        '排日程先列固定项再放可移动项', '排日程',
        ('排日程',), ('先列固定项', '再放可移动项')),
    'C06-11': ('本人教学示例优先Python', '教学示例语言', '优先Python',
        '讲解先最小运行例再解释边界，跨课程', '教学讲解',
        ('教学讲解',), ('先最小运行例', '再解释边界')),
    'C06-12': ('本人摘录需保留来源链接', '摘录来源', '需保留来源链接',
        '整理时分原话与概括再标主题，跨项目', '摘录整理',
        ('摘录整理',), ('分原话与概括', '再标主题')),
    'C06-15': ('本人测量记录用厘米', '测量记录单位', '厘米',
        '录入前核单位、核量程、标异常，通用手工程序', '测量记录录入',
        ('测量记录录入前',), ('核单位', '核量程', '标异常')),
    'C06-16': ('本人每段学习材料不超过十分钟', '学习材料分段时长', '不超过十分钟',
        '按目标拆单元，先基础后练习，跨课程通用', '学习材料单元安排',
        ('安排学习材料',), ('按目标拆单元', '先基础', '后练习')),
    'C06-05': ('本人外出素材先用小体积预览', '外出素材预览', '先用小体积预览',
        '压缩前核原件副本再核可读性；只描述不做', '压缩前检查描述',
        ('压缩前检查', '只描述不做'), ('核原件副本', '再核可读性')),
    'C06-07': ('本人批次文件名日期_主题', '批次文件命名', '日期_主题',
        '整理前清单预览、确认对应再执行，通用程序', '批次整理',
        ('整理前清单预览', '确认对应再执行'), ('清单预览', '确认对应', '再执行')),
    'C06-10': ('本人学习资料要离线可读', '学习资料可读性', '离线可读',
        '准备时先列来源再核授权最后核离线副本', '学习资料准备',
        ('准备学习资料',), ('先列来源', '再核授权', '最后核离线副本')),
    'C06-13': ('本人书签标签不超过两个层级', '书签标签层级', '不超过两个层级',
        '先按URL对照再看标题，重复项只列候选不删除', '书签重复候选检查',
        ('重复项只列候选不删除',), ('先按URL对照', '再看标题', '重复项只列候选不删除')),
    'C06-14': ('本人公开草稿不放个人联系方式', '公开草稿私密字段', '不放个人联系方式',
        '发布前草稿检查出处、私密字段、版本号，需用户后续确认发送', '发布前草稿检查',
        ('发布前草稿检查', '需用户后续确认发送'), ('检查出处', '检查私密字段', '检查版本号')),
    'C06-20': ('本人索引保留原文件名', '索引文件名', '保留原文件名',
        '若材料有日期先按日期分组，无日期先按主题，通用', '材料条件分组',
        ('材料分组',), ('若材料有日期先按日期分组，无日期先按主题',)),
}


def compile_c06_setup(case_id, setup_text, *, scenario_clock):
    if case_id not in SETUPS:
        raise ValueError('corpus_c06_unknown_case')
    expected, digest = SETUPS[case_id]
    if type(setup_text) is not str or setup_text != expected or sha256(setup_text.encode()).hexdigest() != digest:
        raise ValueError('corpus_c06_setup_source_changed')
    if case_id not in SPECS:
        raise ValueError('corpus_c06_setup_mapping_pending:' + case_id)
    clock = datetime.fromisoformat(scenario_clock)
    if clock.tzinfo is None or not math.isfinite(clock.timestamp()):
        raise ValueError('corpus_c06_clock_requires_offset')
    return C06Batch(case_id, setup_text, digest, float(clock.timestamp()))


def validate_batch(batch):
    from datetime import timezone
    if type(batch) is not C06Batch:
        raise TypeError('C06Batch required, never complete Case')
    expected = compile_c06_setup(batch.case_id, batch.setup_text,
        scenario_clock=datetime.fromtimestamp(batch.scenario_time, timezone.utc).isoformat())
    if batch != expected:
        raise ValueError('corpus_c06_manifest_differs')
