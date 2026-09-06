# C07 no-match setup 准备

2026-09-07；base e698d449；分支 feat/corpus-c07-prepare。**源码待审，6 个必要控制 NOT_RUN；20 条模型质量均 NOT_RUN。** 未占资源槽，未安装/构建/启动模型或 native。

原义务来自 Memory 仓库 `plans/2026-08-29-human-memory-digital-twin/quality/recall-corpus-candidate/review-zh/successor-12x20/07-no-match.md` 的 setup 与 recent_messages。原文/fixture/阈值/候选 pins 不改。编译入口只有 case_id、原 setup 字符串、scenario_clock；拒绝整个 Case/input/gold 对象。SETUPS 保存逐条原文与固定 SHA256。scoring 子进程仍不读取 oracle；模型输入与 setup 分离。

## 具体干扰项

原文多数仅给记录类别，未给具体值。以下是本叶显式新增的**合成 fixture 默认值**，不冒称原文已有事实、模型抽取或真人事件；供本次独立源审查。每例独立库，仅一条实际非空长期记录；无要求记录的例也保留无关旅行偏好。具体值由 setup 编译端固定，不读取当前问题或 gold；完整 synthetic S1 与 payload 一起由 manifest_hash 绑定。未指定旧 episode 的发生时间为 scenario_clock−7天，明确为 fixture 默认值。

| Case | 实际类型 | 具体干扰内容 |
|---|---|---|
| 01 | semantic | 旅行偏好火车 |
| 02 | semantic | 阅读小说偏好英语版本 |
| 03 | episode | 旧采购清单：信封、胶带 |
| 04 | episode | 旧活动账本场地费260元 |
| 05 | semantic | 长篇报告偏好分节叙述 |
| 06 | semantic | 旧文具收纳习惯：木抽屉 |
| 07 | semantic | 旧项目标题：山径观察 |
| 08 | semantic | 无关旅行偏好 |
| 09 | episode | 上次会议安排上午十一点 |
| 10 | semantic | 内部备忘录使用简短陈述句 |
| 11 | procedure ACTIVE | 明确采用档案归档五步：编号、页码、借阅状态、装袋、归架 |
| 12 | semantic | 无关旅行偏好 |
| 13 | episode | 旧办公支出340元 |
| 14 | episode | 旧书架第三层放工具书 |
| 15 | semantic | 日常订餐四人份 |
| 16 | semantic | 两个虚构联系人，电话为占位号码 |
| 17、18 | semantic | 无关旅行偏好，各自独立库 |
| 19 | semantic | 旧样品甲登记绿色 |
| 20 | semantic | 长期目标：学会园艺 |

Procedure 11 保留真正 procedure，不改成 semantic。源中明确采用与五步内容，原清单转换请求不作为该程序的来源。

## 真实准备路径与独立检查

`compile_c07_setup` → Host 原 S1 producer/append_evidence/read_admitted → public Memory owner 注册/ingest → 原 DurableMemoryJobRunner + 原 FixtureAnalysisDelivery proof → 实际 ACCEPTED application → public display-only graph 验证唯一节点的类型/revision/payload hash。

Fixture executor 名称沿用 `corpus-fixture-plan/no-language-model`，不是生产模型。零 token 仅说明本地 compiler；不是 Provider 价格结论。既有 delivery canonical/hash 不变；proof 的 setup_hash 绑定本叶完整 manifest。APPLIED 不能代替 ACCEPTED，也不能代替非空 materialization；ID/receipt/application 来自实际 SDK。公开图谱只用于设置端证据，不进入 Agent。没有 Memory 私有 SQL、新业务 ledger 或 SDK 测试 helper。

编译期：原20条 setup bytes/hash 逐条对照；错误/整Case输入与 tampered batch 必须拒绝。运行期：真实 semantic/episode/procedure 三分支各一控，查原 S1、accepted application、实际节点，worker 再次 IDLE只作已 APPLIED 后的观察，关闭重开再读同 ID。合成 seed 不建 foreground Turn/terminal/history。

## 06 / 14 最近历史与明确剩余

`prepare_c07_recent_group` 接收原 ordinal/role/content 三字段，错误顺序/角色先拒绝。它通过真实 service.enqueue + runtime.after_enqueue/drain、ingestion worker 和 PrimaryConversationAuthority 取得完整两消息 group；逐项比较 SDK 真实终态/精确逐消息 source绑定。助手内容只能来自实际运行的 producer，不能直接插入历史、拼角色标签字符串或从 gold 生成。评分另提交当前 USER，同一库由原 Context assembler 读取最近组。

两条控制使用原 authored assistant 的确定性 transport 驱动真实 Host/SDK，再发独立当前输入，核下一**实际 ProviderRequest**的分角色历史与 seed 不泄漏。此证据只证明 carrier/Context 准备，不是语言模型质量。

**当前实际缺口是评分 session 的相位适配，不是 SDK 能力：** 现 `corpus_scoring_session.run` 只有单一真实 Provider 初始化路径，尚无受控的 setup-only 确定性 producer → 原 SDK/Host完整终态 → 同库生产评分 Provider 恢复的相位接线。06/14 在该生产评分入口明确 `c07_scoring_same_store_recent_producer_unwired`，不得因为长期 seed 可运行就声称两例 READY。独立 helper 与其两项实际运行栈控制已实现但 NOT_RUN；后续应复用真实 runtime factory，不以 fake terminal/评分时拼接消息补洞。

其余18条接入 C07 compiler/preparer 分发，**仍是源码可达、未执行，不是18 PASS**。评分主链当前 input/setup 传递、审批、实际 trace 与统一政策沿用；C07是否真正零查询/零披露/未建新记忆或提醒须后续逐例实际 trace 判，不凭 setup 完成推断。

## 文件边界与待跑

独占 `corpus_c07.py`、`corpus_c07_prepare.py`、`test_corpus_c07_prepare.py`。与 Carver 已协调共享小 hook：`corpus_scoring.py::prepare_batch` C07 ID 白名单/对应审阅提示；`corpus_scoring_session.py` C07 compiler/preparer 分发及结构化未接线拒绝。不改 C05、公共写入 helper、SDK、main 工作树。

6个控制：1个全部setup/输入边界；3个真实非空类型分支/原proof/重开；2个实际最近组→下一请求。没有全401/240/旧绿复跑。默认资源锁获得后才运行；当前全部 NOT_RUN，失败时保留原 raw，只重试实质修正后的相关红。

待跑命令（在本树 backend；借用既有解释器与主 H079/M619 target，**非本树独立安装身份**）：

```sh
/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python -I -B /Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py --evidence-dir /Users/denny/projects/simple_harness-typed-recall-context-use-full/.local-test-evidence/2026-09-07/corpus-c07-prepare/r1 -- /Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python -I -B -c 'import sys; sys.path[:0]=["/Users/denny/projects/simple_harness-typed-recall-context-use-full/backend", "/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-07/memory619-artifact/installed"]; import pytest; raise SystemExit(pytest.main(["-q", "tests/quality/test_corpus_c07_prepare.py", "--basetemp=/Users/denny/projects/simple_harness-typed-recall-context-use-full/.local-test-evidence/2026-09-07/corpus-c07-prepare/r1/basetemp"]))'
```

不启动此命令争槽；主/Singer当前持有资源。ARCH 未写“完成”：只有这批新控制通过后才回写当前验证事实与剩余边界。
