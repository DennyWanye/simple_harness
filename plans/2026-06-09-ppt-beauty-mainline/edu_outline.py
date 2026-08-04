# -*- coding: utf-8 -*-
"""《中国现阶段教育现状》outline — 模板版(丰富多页)与 image-2 版(全幅生图)共用素材。"""

TEMPLATE_OUTLINE = [
    {"layout": "title", "title": "中国教育现状全景",
     "subtitle": "2026 · 转型中的挑战与机遇"},
    {"layout": "toc", "title": "目录", "bullets": [
        "教育基本盘：规模与普及", "城乡与区域差距", "双减之后的教育生态",
        "AI 时代的教育变革", "总结与展望"]},
    {"layout": "section", "title": "一、教育基本盘", "subtitle": "世界最大规模的教育体系"},
    {"layout": "bullet", "title": "规模与普及水平", "bullets": [
        "各级各类在校生约 2.9 亿人，专任教师超 1880 万",
        "九年义务教育巩固率约 95.7%，基本实现全面普及",
        "高等教育毛入学率突破 60%，进入普及化阶段",
        "学前教育毛入园率超 91%，普惠园覆盖率持续提升"]},
    {"layout": "two_column", "title": "城乡与区域差距",
     "left_title": "城市教育", "left": [
        "优质师资与名校资源集中", "数字化教学设施完善", "课程选择与课外资源丰富"],
     "right_title": "乡村教育", "right": [
        "青年教师流失与结构性缺编", "小规模学校与生源减少", "数字资源利用率有待提升"]},
    {"layout": "section", "title": "二、深层挑战", "subtitle": "内卷、负担与公平"},
    {"layout": "bullet", "title": "双减之后的教育生态", "bullets": [
        "学科类校外培训大幅压减，监管常态化",
        "课后服务基本实现义务教育学校全覆盖",
        "部分家长焦虑转向素质类与隐形补习",
        "学生心理健康问题受到前所未有的关注"]},
    {"layout": "bullet", "title": "升学竞争与评价改革", "bullets": [
        "中高考竞争依然激烈，分流焦虑普遍存在",
        "新高考改革持续推进，选科走班成为常态",
        "职业教育法修订实施，职普协调发展定位明确",
        "多元评价体系建设仍在探索之中"]},
    {"layout": "section", "title": "三、变革方向", "subtitle": "数字化与 AI 重塑教育"},
    {"layout": "bullet", "title": "AI 与教育数字化", "bullets": [
        "国家智慧教育平台访问量居全球同类平台前列",
        "生成式 AI 进入课堂，辅助备课与个性化学习",
        "教师角色从知识传授转向学习设计与引导",
        "AI 素养教育纳入中小学课程探索试点"]},
    {"layout": "bullet", "title": "总结与展望", "bullets": [
        "从规模扩张转向质量提升与公平兼顾",
        "技术赋能与人文关怀需要并重",
        "教育评价改革是破解内卷的关键",
        "建设学习型社会，让教育回归育人本质"]},
]

STYLE = (
    " Cinematic lighting, warm scholarly palette of deep blue and amber,"
    " ultra-detailed modern digital illustration, elegant and hopeful mood,"
    " main subject in upper two thirds, bottom quarter dark simple and clean"
    " for text overlay, no text, no letters, no typography, no watermark."
)

IMAGE_OUTLINE = [
    {"layout": "image_full", "title": "中国教育现状全景",
     "caption": "2026 · 转型中的挑战与机遇",
     "image_prompt": "A vast modern Chinese school campus at golden hour seen from above, students flowing across the courtyard like rivers of light, distant city skyline." + STYLE},
    {"layout": "image_full", "title": "世界最大规模的教育体系",
     "bullets": ["在校生约 2.9 亿人", "义务教育巩固率 95.7%", "高等教育毛入学率超 60%"],
     "image_prompt": "An immense library interior with endless glowing bookshelves curving like a galaxy, tiny figures of students walking among them." + STYLE},
    {"layout": "image_full", "title": "城乡之间的距离",
     "bullets": ["城市：资源与师资集中", "乡村：教师流失与小规模学校", "数字化正在弥合鸿沟"],
     "image_prompt": "Split scene: left a bright modern city classroom with holographic screens, right a small rural school under big sky and mountains, a bridge of light connecting them." + STYLE},
    {"layout": "image_full", "title": "双减之后的教育生态",
     "bullets": ["校外培训大幅压减", "课后服务全覆盖", "关注学生心理健康"],
     "image_prompt": "Children playing and reading freely in a sunlit schoolyard after class, kites in the sky, relaxed and joyful atmosphere." + STYLE},
    {"layout": "image_full", "title": "AI 重塑课堂",
     "bullets": ["国家智慧教育平台", "生成式 AI 辅助个性化学习", "教师转向学习设计者"],
     "image_prompt": "A futuristic classroom where a teacher and students interact with floating holographic knowledge graphs, warm light, sense of wonder." + STYLE},
    {"layout": "image_full", "title": "让教育回归育人本质",
     "caption": "质量 · 公平 · 人文 —— 通向学习型社会",
     "image_prompt": "Sunrise over a long road through fields leading to a glowing school on the horizon, a few students walking toward it, hopeful new beginning." + STYLE},
]
