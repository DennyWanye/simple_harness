# C08 剩余来源：旧纪要与旧检查列表

2026-09-07。基线 `c5b55387`，自有树 `simple_harness-corpus-clock`，分支
`feat/corpus-c08-remaining`。本叶仅源码与两项新控制准备，**NOT_RUN**。
主线既有 scalar13、retained5、dispatcher1 的通过记录保持原边界，本叶不重跑，
不称新 case 已准备完成或模型质量通过；实际 Provider 仍有独立阻塞。

## 从原 setup/current 得出的范围

原文件为 Memory 仓库
`plans/2026-08-29-human-memory-digital-twin/quality/recall-corpus-candidate/review-zh/successor-12x20/08-suppressed.md`。
编译器只接收 exact setup 与共同可信时钟；current 只供后续独立请求。
gold 不参与事实、载体、权限或响应构造，原 MD/旧 JSONL/阈值均不改。

| ID | 已有来源与真实缺口 | 本叶处理 |
| --- | --- | --- |
| 02 | 标量联系电话已有；独立派生联络资料未构造 | 保留 partial，不把一个电话节点当全部联络资料 |
| 04 | 称呼标量已有；称呼关联尚缺实际关联来源 | 保留 partial，不从称呼字符串造关系 edge |
| 09 | 金额标量已有；独立金额引用尚缺 | 保留 partial，不把标量 read 当引用来源 |
| 13 | 纪念日及相关提醒；提醒需要真实 mutation/registration/source 权威 | 本叶不接，不造提醒 receipt 或用普通文本冒提醒 |
| 15 | 旧会议决定及旧纪要，当前只概括本轮两项纪要 | 新增实际旧对话中的纪要文本来源 |
| 16 | 旧项目决定及旧检查列表，当前整理本轮三项检查 | 新增实际旧对话中的检查列表文本来源 |
| 19 | 旧对话关联收件人；需真实关系来源 | 本叶不接，不从单一 semantic 推称关系已测 |
| 20 | 工具别名对应另一实体；原 setup 未明确目标实体身份 | 明确缺口，不借当前“青灯阅读器”或 gold 反填别名目标 |

其余原 scalar 与四个 retained 不增加新工作。15/16 原 setup 未规定必须存在磁盘文件或
TaskScope 文档；本叶明确采用 **S1 中实际 assistant 消息的纪要/检查列表文本**。
这是有限、可复现的 fixture 实现，不等同任意外部文档、TaskScope closure、Procedure、
滚动摘要或短期 generation。如果后续需要这些独立载体，仍须另接其真实权威。

## 两项固定实现输入

以下是设置端撰写的旧对话实现，不宣称为原语料附带的逐字旧对话。只增加让原载体实际生成的
整理指令，不增加日期、人物、项目身份、完成事实或成功证据。

| ID | 原 setup SHA-256 | 旧 USER | 实际 assistant 载体 |
| --- | --- | --- | --- |
| C08-15 | `f5a56c274c3c819fdb1c42a63c30816eb01514fb9f6dab99ca7575a0b5c1977c` | 会议决定：采购蓝色桌布。请整理为会议纪要。 | `会议纪要\n决定：采购蓝色桌布。` |
| C08-16 | `7235ad7aa0302ed039b4175aa383940df1c851516b294a4d2806a887b975af26` | 项目决定：省略校对。请把这个决定列入检查列表。 | `项目检查列表\n1. 确认按原决定省略校对。` |

表内换行在源码为真正 `\n`，不是模型自行猜分界。新映射位于
`backend/deskpet/quality/corpus_c08_documents.py`，固定 carrier kind 为
`retained-meeting-minutes` / `retained-project-checklist`，纳入新 case manifest。
旧四例编译输入、manifest 字段/domain、旧 kind、旧 fixture defaults 不变。

## 实际生产来源链与接口

沿现有 `compile_c08_retained_setup` → `RetainedSummaryProvider` →
`execute_retained_phase`，无新初始化、后台服务、数据库 schema 或私有 SQL：

1. 已初始化 actual main 运行旧 USER，loopback 只返回固定文档；实际 SDK 终态经 Host
   outbox/S1 形成两个独立 registration。任何错 assistant/mapping 均按已有 exact 校验拒绝。
2. 真实 USER 已入队 job 按其原 provider/model/config/evidence lineage 执行；固定 fixture
   analysis 创建 `meeting_decision` 或 `project_decision` semantic，证据只绑定 USER。
   assistant 作为独立历史载体，不被当成新的用户事实或 Procedure adoption/success。
3. 遗忘前公共 mutation receipt/graph 必须有实际 A，两个 history binding 都 visible。
   对真实 USER evidence 执行公共 suppression，经实际 terminal 祖先传播到 assistant。
4. 遗忘后 graph 空、两 history 均 `history_suppressed`；原 S1 仍可由权威按 exact ref
   回读，原 job 必须 IDLE，不能删源制造无内容。
5. fixture manager 关闭后才原 production manager 重开，原 analysis authority 保持；
   再公共核 graph/history。其后原 current 的独立实际请求不得带旧决定/旧文档/setup。

新 phase kind 为 `deterministic_retained_document`，不误标为摘要。主线共享
`session.py`、`corpus_scoring.py` 的四例 allowlist **未修改**；Hegel 后续只需将15/16
纳入现 retained 分支，跳过 scalar seed，沿同一 helper 确认后才准入评分 Provider。
本叶既有入口能构造两新源，不宣称正式 dispatcher 已开放。

## 仅两项待执行控制

文件 `backend/tests/quality/test_corpus_c08_documents_main.py`，nodeid：

- `tests/quality/test_corpus_c08_documents_main.py::test_actual_main_document_source_forget_reopen_current[C08-15]`
- `tests/quality/test_corpus_c08_documents_main.py::test_actual_main_document_source_forget_reopen_current[C08-16]`

直接复用原 actual-main child 与全部初始化/自然退出路径，只运行两新 case，不收集旧测试
parametrization。每项附带 setup 变更与跨 case assistant mapping 拒绝；核独立 S1 registration、
USER-only mutation evidence、遗忘前非空/可见、遗忘后不可见、APPLIED/IDLE、生产重开和下一
physical 请求无旧内容。零远程模型、阻止 WeMM 实际加载，不初始化另一份 main。

由主线在 current installed H0710/M619/S0313 与现有 Python 下统一执行，raw 写新 ignored
批次目录，默认共享资源锁；本树不执行、不安装、不打包。两项通过也只是新增源的实际 public
runtime 控制，不是模型评分，不把零查询解释为后台抑制 gate 自动受测。
