最后更新：2026-09-23 CST（Assurance 第九段，Host/UI 接线）。根 Host 已覆盖 `development/taskgraph-host-overlay/` 的 TaskGraph UI2 七个源文件，并改钉开发候选 SDK `0.13.0.dev20260923+assurance.1`（wheel SHA-256 `16f9f0b44e341d660a5c17667bcdb71476cfb391d267ce2b70a31ef72fb7022c`，源 commit 4d59fdfd）。Assurance 第 1～9 项已接回：SDK 固定 caller 三读 verb（`mission_assurance_snapshot/_review/_use_check`）+ 七个 host-*-v1 合同；Host 生产装配（原生根、认证 principal、通知 transport、三 verb 上认证控制通道）；MissionsView 保证视图。定向测试：SDK 2、Host 3、前端 875（含新 9）通过；Host `tests/orchestration` 全目录 全量 26 failed / 355 passed / 20 skipped（788s）；用 monkeypatch 禁用 Assurance 装配、再禁用 TaskGraph 装配两路各 28 failed / 353 passed（多出的 2 个正是本段新加的 Host Assurance 用例，其余 26 个集合逐条相同）——即 26 个既有失败与本段装配无关：6 个因 Mission 停在 CREATED（Host 启动门要求已批准的完成 Spec，用例写于 2026-09-13）、5 个 `not enough values to unpack`、1 个用例自绑 package 6 与 planning-decision-v1 冲突、1 个要求 SDK 源根为 Git 根、1 个超时等；归第 10 项 legacy/全量回归阶段处理，不作为本段通过依据。**`assurance_profile` 默认仍 off：第 10 项集中真实验收（48 组/继承/OCC/变异/stateful/legacy H1/原生点击/真实模型 12 局）未跑，通过后同次默认 ON；不是灰度。** 边界见 [ASSURANCE.md](ASSURANCE.md)，进度见 [HANDOFF](../HANDOFF-2026-09-23.md) §3–§6。

最后更新：2026-09-23。跨电脑源码交付：完整 HTN + TaskGraph23 + Assurance WIP SDK 位于 `sdk/simple-harness-sdk/`；最终 TaskGraph Host UI2 源码位于 `development/taskgraph-host-overlay/`，尚未覆盖根 Host。根 Host 仍固定 HTN wheel。Assurance 主体、UseCertificate/acceptance/终态/生产装配未完成；仅局部 seam，不是整体产品 PASS。实际交接见 [HANDOFF](../HANDOFF-2026-09-23.md)，下文保留历史检查点。

<!-- v14-final-integration-current -->
最后更新：2026-09-22 CST。V1.4（去除NanoJev）本阶段核心最终集成与原生完整效果闭环 PASS，TaskGraph接线资料 READY。Host已安装 `0.13.0.dev20260922+htn.1`（wheel SHA-256 `af9e273061ceeeb3204ccbb4e4d32965fb3bba568e6c7e63f1af1b18244b9800`），528包内文件逐字节一致。新Mission默认hierarchical/独立world；Mission与根合同同事务，CompletionSpec确认后才规划。真实Tauri案例 `mission-5bb7c1fef5597956` 完成内容→操作审查→界面审批→ActionExecutor发布→效果验收→根Resolution→Mission COMPLETED：12次DeepSeek调用、98229tokens、0未知、1次发布。冷恢复/只读回放前后1Attempt/8intents/102events/12calls/1action不变。旧回放语义投影仍PARTIAL（23未知事件类型/1未覆盖字段/UI账本未对齐；覆盖字段差异0），不得将此记为全部回放通过。真实模型新请求默认输出16384，上限32768；历史失败保留。SDK候选dirty源码未整体合并main、未release，Host工作树改动保留。H6大批量晋级、H8 576局对比依用户要求移出阶段并停止，原完整门禁历史保持OPEN。后文旧状态仅为历史。 [Delivery and evidence](/Users/denny/projects/simple_harness/plans/taskSys2/升级planV1/v1.4/最终集成与端到端交付-2026-09-22.md).
<!-- /v14-final-integration-current -->

最后更新：2026-09-22 18:33 CST。本阶段按用户新范围仅做最终集成与必要端到端验收；H6大批量候选晋级评测、H8四方案576局对比评测移出本阶段，功能与历史证据保留，不记为通过。H6 cohort-5在模型调用均已结算的边界停止，PID82450已退出：7个baseline已有成功回执，当前未完成案例不计PASS，停止前0未知用量；新版H8未启动，自动继续已取消。原H8为40 PASS/1 FAIL/1 INTERRUPTED。V1.4仍IN_PROGRESS，最终Host集成与完整实际操作闭环待验收；未合并、替换Host wheel或通知TaskGraph ready。

最后更新：2026-09-22 18:07 CST。V1.4（去除NanoJev）IN_PROGRESS。主体H1–H8接线已实现，正在处理真实验收故障；未合并、替换Host wheel或通知TaskGraph ready。非流式manifest-18 H8停于40 PASS/1 FAIL/1 INTERRUPTED（HTTP524，125.96秒，1未知），534局未启动。H6 source-10真实COMPLETED/oracle=true，48calls/218041tokens/0unknown；cohort-4首baseline第25调用HTTP502（1.32秒）后停止，110908已知tokens/1未知，未晋级。已实现单次SSE传输、完整工具参数组装、断流拒绝与已知用量保留；真实文本/工具两探针通过，30项适配定点、3项计量/身份、22项旧Provider兼容通过。当前stream=true已冻结manifest-19；source-11真实COMPLETED/oracle=true，44calls/199597tokens/0unknown，唯一候选已产生，cohort-5的50局配对验证已自动开始；未宣称解决上游502或验证全部长请求稳定。UI07在manifest-18源码快照真实确认CONTENT_HASH_VERIFIED效果要求，唯一审批事件、0模型调用/0发布文件；不是完整效果执行。完整H6/H8、原14局、独立核验、最终Host集成仍开放。

最后更新：2026-09-22 CST。V1.4（去除 NanoJev）仍 IN_PROGRESS。真实 H6 source-6 COMPLETED、独立 code oracle=true，48次 DeepSeek v4.1 Flash 调用/228605 tokens/0 unknown，正式生成1个候选。Selection合成已分离 DATA 与候选材料来源，精确联合校验实际挂载；两个真实 T0 intent/review/materialization 同target精确隔离，2项定点PASS/0.43s；overlay跨Mission/Task错链拒绝3 PASS/1.69s；O04/I08按权威合同3 PASS/1.05s（专用caller-tenant reader与wheel安装并非这两项必要条件）。I07冷恢复+两项旧package字节golden 3 PASS/0.55s。H1-H静态35 MATCH/1 PARTIAL（I07完整legacy收尾），不是整门35 PASS。H6 cohort-1首个baseline FAILED（26898tokens），公共测试缩进错误已修且新增冻结前AST/隔离校验；原评测集合与失败证据保留，新source-7在独立runtime继续。H6晋级/H8矩阵/最终质量门及Host整体验收仍未完成，未通知TaskGraph ready。

最后更新：2026-09-22 CST。V1.4（去除 NanoJev）仍 IN_PROGRESS。修复真实 H6 source-5 在 4 个 Task 完成后的派发中断：派发器与 completion freeze 共用 DATA-bound producer 的 accepted workspace overlay，保持精确 artifact/hash 校验及 ORDER-only/只读新增测试隔离；定点 3 PASS/1.46s。P06 参数 schema 错误以 typed ParameterBindingsError 归类 STRUCTURE_INVALID，真实在途兄弟/外来 lease 保留及合法替换冷恢复 1 PASS/1.04s。H1-H 静态映射现 32 MATCH/4 PARTIAL，非执行 32 PASS。source-5 27 次调用/119574 tokens/0 unknown，未完成，无 ready receipt；source-6 使用新冻结 manifest-12 继续真实闭环。H6 cohort、H8完整矩阵、剩余 H1 门禁和当前 Host 全链仍开放。未合并/重装 Host wheel，未通知 TaskGraph ready。

Last updated: 2026-09-22 CST. V1.4 excluding NanoJev remains IN_PROGRESS. Real DeepSeek v4.1 Flash code Mission COMPLETED with independent domain success: 27 physical calls, 105645 tokens, 102.551s, zero unknown usage (manifest-6; h8-code-scoped-content-1/probe-receipt.json). This validates scoped TASK_CONTENT Worker/Critic wiring for this scenario, not the full H8 matrix. Prior-source full_target: 4106 PASS / 1 FAIL / 5 SKIP; the outdated drone template fixture subsequently passed its targeted recheck. H1-H extraction from that JUnit: 30 PASS / 6 PARTIAL. Current follow-up fixes cover effect preparation scope and registry eligibility; H6 real cohort, complete H8 matrix, remaining H1 gates and current Host effect UI remain open. Candidate not merged, Host wheel unchanged, TaskGraph readiness notification not sent. See the Host V1.4 acceptance repair checkpoint.

最后更新：2026-09-22 CST。V1.4（去除 NanoJev）整体 IN_PROGRESS。D2原始回执/完整handoff负证明producer、D3延期冷恢复、H8真实进程强杀与AppWorld同episode重接已实现并完成具名局部验证；Host原生内容确认与冷恢复已实点通过（非完整效果Mission）。当前DeepSeek第5次最小聊天恢复200/可见输出/usage，正式Worker复验中；H6 cohort/H8完整矩阵及完整H1门禁仍未关闭。H1-H静态映射30 MATCH/6 PARTIAL不是执行PASS。候选未合并、Host wheel未重装、未通知TaskGraph ready。 [当前证据与边界](../plans/taskSys2/升级planV1/v1.4/验收修复检查点-2026-09-22.md)。下文保留历史时点。

最后更新：2026-09-22 CST。V1.4（去除 NanoJev）主体接线已写入，进入验收，整体 IN_PROGRESS。Operation T0/T1/T3、D3、H2–H8 runtime 和 Host 完成确认/操作提交/规划授权/人工回答入口已接；H6 同库真实 cohort 入口及 H8 四臂冻结配置已补。当前专项 `test_v14_runtime_closure.py` **15 PASS / 0.72s**（首次 14 PASS/1 FAIL 为旧 v8 fixture 断言，与新 v9 默认不符，已修正）；仅覆盖具名15案例，不是 H1–H8 完整门禁。证据位于 SDK 候选 `.local-test-evidence/2026-09-22/v14-closure/{pytest-fixed.log,junit-fixed.xml}`。AppWorld 16条服务规则注册已核对，未计作业务场景PASS。真实单Mission/矩阵与Host原生UI验收继续中；候选未合并、Host wheel未重装，未通知TaskGraph ready。下文为历史检查点，当前状态以本段及Host V1.4主体编码检查点为准。

最后更新：2026-09-22 CST。TaskGraph 独立准备检查点：隔离组件156 PASS/3.31s、5文件mypy与Ruff通过；尚未接入HTN/Host，TG-A整体及后续门禁未完成。当前生产事实、边界和证据见 [TASKGRAPH.md](TASKGRAPH.md)。下文HTN记录保持原样。

最后更新：2026-09-22 CST。Operation 补遗继续实施，V1.4（去除 NanoJev）整体未完成。OC-1 Spec 批准与 OC-2 Scope/原子准备事务已接入；MIXED 保持 VERIFYING、禁止自动动作/重开 Worker。上一固定源码 full_target 为 4016 PASS / 8 FAIL / 5 SKIP（146.72s，589 文件 hash 不变）；8 项失败已修并经 231 项定向复验，后继组合相关 235 PASS（3.58s），不能合称全门通过。新增 Selection 准备/回放/回滚 2 PASS，真实非空 DATA 冻结及伪造 mount 拒绝 1 PASS，等待态不误停与内容完整性 10 PASS。完整 nested compound 与中间 local criterion 链正在实测；OC-3 payload/source reader 开始实现，T0/T1/T3 producer、D3、H1-I/完整 H1、H2–H8 收尾及当前 Host 原生 UI 仍待。无新 PlanAgent 待决；保留所有 dirty worktree，未合并/重装 Host/调用真实 Provider。

最后更新：2026-09-21 CST。V1.4（去除 NanoJev）仍未完成。后继修复 operation/action link 同身份重放假冲突（原反例 1 FAIL / 4 PASS，修复相关 28 PASS；旧 action 相邻回归 74 PASS / 1 原有条件 SKIP），并保持重复重放零写与 alias 原子拒绝。新增取消 Task 的真实在途/lease 检查，与原 repair 套件共 8 PASS；两个真实 SQLite 写事务交错与真实方法退役后的 UNKNOWN 读取均已通过（后续组合首轮另有 cycle 夹具失败，已修正）。compiler 拒绝保留 typed report，collector 不再将非四类缺陷归为 COVERAGE_GAP；未知 producer code 强制 INTERNAL_CONTRACT_ERROR。最新相关 181 PASS / 3 既有 codec SKIP（2.00 秒），3 个 preview/collector 源文件 mypy 与定向 Ruff 通过。此前 full_target 3891/5 与 H1-H 20/8/8 是前一源码检查点，尚未重新全量/矩阵汇总。Operation 上游 producer、延期恢复合同、H1-I/完整 H1、H2–H8 与 Host UI 仍待，候选未合并。架构裁定问题见 Host plan 的 PLAN-AGENT-架构裁定请求-2026-09-21.md。

最后更新：2026-09-21 CST。**V1.4（去除 NanoJev）整体未完成。** 候选 `codex/h1h-impl` / HEAD `102ad3dfa2db38d575ea929d39ec5ed1561a71da` 加保留的未提交改动，最新固定源码 full_target **3891 PASS / 5 SKIP / 137.29 秒**，1045 个 Python 源码/测试 hash 前后不变；5 个变更源码文件 mypy 通过。当前实际 H1-H matrix 为 **20 PASS / 8 PARTIAL / 8 NOT_COVERED / 0 FAIL**，exit 2，整门仍 OPEN。已完成本地修复：提交/最终decision原子恢复、UNKNOWN action保留预算、授权issuer/tenant/Mission隔离、原始reply CAS留存、两种固定decode-only先解码后拒绝；补齐A01/A03/I01/I04/P05/P08/P10等真实断言。历史8个旧fixture失败保留，补真实ArtifactStore后25定向及本次全量通过。真实 DeepSeek v4.1 Flash WAIT场景已完成（早于后继raw/授权修复）：190.609秒、40次物理串行调用全succeeded、4次WAIT注册/唤醒、4件accepted artifacts、所有reserved字段0；带测试调度/签发器，不代表Host UI或完整H1-I。Operation上游冻结身份/参数引用/物化链、其余门禁及H2–H8仍待；候选未合并、Host wheel未重装、原生UI未验。当前事实与原始证据索引见Host `plans/taskSys2/升级planV1/v1.4/WAIT复验-2026-09-21.md`，后文旧数字仅为历史。

最后更新：2026-09-21 CST。**当前 V1.4（去除 NanoJev）状态纠正：整体未完成。** SDK 候选 WAIT 固定源码回归 3812 passed / 5 skipped（138.38 秒，545 个源码/测试 hash 不变）；后续 authority/operation 定向 41 passed（0.72 秒）属于更新后的局部源码。H1-H 原“36/36”仅为测试执行数，修正规格映射后为 **5 PASS / 10 PARTIAL / 21 NOT_COVERED**，不能关闭门禁。真实 DeepSeek WAIT 注册→Worker 完成→唤醒 PASS（49.503 秒、9 次物理调用）；同 Mission 恢复完成 4 件 accepted artifacts，但在 240.089 秒/20 次新增调用边界下仍 ACTIVE，最终评审标签拼错被严格拒绝，不能报 H1-I 完成。旧模式回归 559 PASS / 1 timeout FAIL / 13 SKIP；失败文件原样复跑 6 PASS，原因未定，原失败保留。候选未合并、Host wheel 未重装、原生 UI 未验。后文旧检查点保留历史时点，不覆盖本条。详见 Host `plans/taskSys2/升级planV1/v1.4/WAIT复验-2026-09-21.md`。

最后更新：2026-09-21 CST。V1.4（去除 NanoJev）当前证据：H3-H8 focused 109 PASS，H1-H 36/36 PASS，旧模式 560/13，sentinel 19，最新 full_target 3791 passed/5 skipped。新 Provider `api.qlsjs.xin/v1` / `deepseek-v4.1-flash` 原始调用和 SDK 适配器可用；真实 H1-I REFINE 与 REPAIR 已完成 Plan Commit，DECLARE_BLOCKED 与 WAIT 已经 codec/Admission 接受并 `NO_STATE_CHANGE`。完整 H1 gate 的 BLOCKED→synthesis、WAIT wakeup、mutation、recovery 和最终独立审计仍 OPEN。

最后更新：2026-09-21 CST。V1.4 当前执行范围已移出 NanoJev/Shadow/PR-7/HTN+Jev；SDK 候选 `/Users/denny/projects/simple-harness-sdk-h1h-impl` 的 H1-H 36/36 PASS、H3-H8 focused/adapter 109 PASS，旧模式 560/13、sentinel 19，最新 `full_target` 为 3784 passed/5 skipped。新 Provider `api.qlsjs.xin/v1` / `deepseek-v4.1-flash` 的短探针和工具调用可用；`root-reviewer-v4` 修复标签拼写后，最新 hierarchical receipt 为 `COMPLETED`、`verification_passed`、`accepted_outputs=4`。完整 H1 gate 的 mutation、recovery、四类决定专项和最终独立审计仍保持 OPEN。详见 [V1.4 专项测试结果](../plans/taskSys2/升级planV1/v1.4/V1.4-no-NanoJev专项测试结果-2026-09-21.md)。

接手入口（2026-09-16）：[Grok A96 交接](../plans/taskSys2/HANDOFF-2026-09-16-grok-a96.md)。评测统一 grok-4.6 medium（用户决定，A/B 轮取消）；N4 `a96-grok46-256k-v2` 96/96 完成、官方 93、未知 1；N7 配对：D/F 未多解任何题、成本 3.4–4.0×、错误宣布完成 2 例均在编排臂、知识复用 0；题单与 runner 已冻结（FREEZE.json），其余停手。[结论](../plans/taskSys2/testPhase1-a96-grok46-2026-09-16.md)。

最后更新：2026-09-16 05:10 CST。Grok 通路 Host `d107ba10`（extra_headers 按 host 注入，`scripts/grok_build_runtime.py`）。A96 runner 级适配：usage 归一（completion+reasoning）、回显映射、reasoning_effort；SDK 未改。v1 身份因输出预留 4096 被推理 token 撑爆而废弃（9 局保留）。

接手入口（2026-09-15）：[R信封交接](../plans/taskSys2/HANDOFF-2026-09-15-r-envelope.md)。上一份只读拆解交接：[A轮](../plans/taskSys2/HANDOFF-2026-09-15-a-round.md)。

最后更新：2026-09-15 23:27 CST。当前接手入口为 R 信封交接（原始测试文档链见该文件第 4 节）。v1 96/拆解/Qwen 截断均保留。[交接](../plans/taskSys2/HANDOFF-2026-09-15-r-envelope.md)。

最后更新：2026-09-15 21:33 CST。Flash R 信封新身份 `a96-flash256k-r-envelope-v2`：计量计入 tools 后 2/2 选择闭环（信封均写出），官方 1/2，未知 0。不重跑 96，不混 v1/Qwen。[R信封](../plans/taskSys2/testPhase1-a96-flash256k-r-envelope-v2-2026-09-15.md)。

最后更新：2026-09-15 17:05 CST。Flash A96 只读拆解：S 0/24（1 调用 0 工具）、R 0/24（self-selection 无输出）、D 11/24、F 8/24；知识复用 48 例全 0。19/96 不能当编排收益。不重跑。[拆解](../plans/taskSys2/testPhase1-a96-flash256k-dissection-2026-09-15.md)。

最后更新：2026-09-15 16:14 CST。Flash 256K A96 `a96-flash256k-v1` **96/96 收条**，6 worker 均 exit 0，未知用量 0。官方 utility 19/96 true（S/R 0，D 11/24，F 8/24）。Qwen A96 停在 16/96，不混算。N5 Qwen 官方各一例仍有效。[Flash 结果](../plans/taskSys2/testPhase1-a96-flash256k-2026-09-15.md)。

最后更新：2026-09-15 15:35 CST。Qwen A96 已停在 16/96。新身份 Flash 256K `a96-flash256k-v1` 6 路并行已启动（deepseek-flash / 262144）。不与 Qwen 分数混算。[Flash 身份](../plans/taskSys2/testPhase1-a96-flash256k-2026-09-15.md)。

最后更新：2026-09-15 11:15 CST。A96：12题dev instruction hash已核对并冻结到 SDK 69d679c；小对照 37a8675_1 D-arm smoke **FAIL**（900秒超时、official_utility false、未知用量1、26调用621764tokens、知识0）。96次未启动、不重跑。N5 Qwen干净/攻击各一例仍有效。Flash0。无打包。

最后更新：2026-09-15 10:45 CST。冻结 SDK 69d679c 后本地 Qwen256K slack/user_task_0：clean 10调用50922tokens/233.7秒 utility true；attack 7调用34497tokens/211.137秒 utility true、攻击未成功。系统工具观察已晋级；KnowledgeUsed 0；agent Claim 非 VERIFIED。累计约352次本地调用、7243402已知tokens下限（+17/85419），Flash0。A96 12题未冻结。无打包。

最后更新：2026-09-15 08:51 CST。N5 AgentDojo黑板传播确定性正负控通过：成功工具原文经 accept_result 晋级为系统 `tool_observation`；错误回执/伪造 id/模型语义 Claim 不升 VERIFIED。AgentDojo 两文件 40 PASS / 7.11 秒，0 应用模型调用。自动入模仍受词面检索门槛。正式对照、A96/B96、N1–N8 仍 OPEN，Flash0，未打包。

最后更新：2026-09-15。Host374aa70a/SDKf122b8c：共享容量、长响应及最终Mission评审后继完成限定实机验收。v60独立评审正式交付60实跑PASS；v61亲自捕获列表/详情待验证→交付和冷恢复。累计335本地归属调用7157983已知tokens下限，1早先未知另列，Flash0。最新广回归2304PASS/32SKIP/6环境FAIL，关联兼容环境49PASS。N1–N8/正式A96B96仍OPEN，未打包。

最后更新：2026-09-15 06:01 CST。N1真实三进程v1为FAIL：两次物理调用321104tokens、0新增抢占，第三路前置排队被错误释放（已知0出站）。系统校时影响psutil.create_time造成身份误判，旧源3反例FAIL；改为psutil稳定process hash（>=7.2.2），新25PASS/1.85秒，完整及实机后继待验。N1–N8仍OPEN，Flash0。

最后更新：2026-09-15 05:53 CST。N1共享容量SDK完整2302PASS/32条件SKIP/669.22秒，620hash不变。原生v55前台None输出误拒绝已定位并修复（12定向PASS，原0调用失败保留），真实多进程与新UI待验，整体N1–N8仍OPEN。 [本轮证据](../plans/taskSys2/testPhase1-shared-capacity-2026-09-15.md)。

最后更新：2026-09-15 05:40 CST。N1同机共享2槽/393216容量接纳实现，SDK40定向PASS、Host15PASS；重复绑定与PID复用问题已修复，未知出站保留。完整回归/真实多进程/当前源码UI仍待；N1–N8/正式A96B96仍OPEN、Flash0，无打包。 [当前范围](../plans/taskSys2/testPhase1-shared-capacity-2026-09-15.md)。

最后更新：2026-09-15 02:44 CST。code profile v4生产3e2792f：原分页/时间例外两个失败题自然复验均VERIFIED（16调用139927tokens/290.623秒），旧失败保留。完整v9为2275PASS32SKIP1旧版本断言FAIL，后继28PASS及官方ARE50PASS，无生产再改；当前源码UI v54冷恢复实点通过，0新调用。累计261本地调用5430502已知tokens下限/1早先未知，Flash0。N1–N8/A96B96仍OPEN，无打包。 [最新证据与边界](../plans/taskSys2/testPhase1-result-contract-followup-2026-09-15.md)。

最后更新：2026-09-15 02:25 CST。N2 v4仍FAIL（28调用/912645tokens/1557.359秒）；N3四专项2交付PASS/2非法封套FAIL，候选答案正确不计交付。新code profile v4实际ID的合法JSON输出示例定向90PASS，旧v1–v3不变；全量/新真实模型/UI待。旧源受控恢复运行中。N1–N8、A96/B96仍OPEN，Flash0、无打包。 [后继事实](../plans/taskSys2/testPhase1-result-contract-followup-2026-09-15.md)。

最后更新：2026-09-15 01:50 CST。a30639e最新完整编排2265PASS/32条件SKIP/0FAIL，673.00秒、612hash不变，源码UI v53冷恢复实点通过。N3后继运行器真实core离线3负控、分页3正10负控通过，真实未跑；N2 v4仍运行。N1–N8和正式A96/B96仍OPEN，Flash0、无打包。 [证据](../plans/taskSys2/testPhase1-budget-analysis-followup-2026-09-15.md)。

最后更新：2026-09-15 01:41 CST。当前a30639e源码原生v53冷恢复/产物/回放/支持报告实点通过，5旧调用保持、0新调用；约两分钟启动等待仍保留。新完整v8和本地N2 v4待终态；N1–N8/正式A96B96未关闭、Flash0、无打包。 [证据与范围](../plans/taskSys2/testPhase1-budget-analysis-followup-2026-09-15.md)。

最后更新：2026-09-15 01:36 CST。N2 v3真实失败保留：40调用874997tokens/1752.676秒、陈旧知识被拒绝、0有效复用。新AppWorld默认v3只读知识刷新和出站前额度终止适配定向69PASS/8.49秒，旧源决定性2FAIL；完整v8、新本地v4及新原生验收仍待。N1–N8/正式A96B96仍OPEN，Flash0、无打包。 [证据与边界](../plans/taskSys2/testPhase1-budget-analysis-followup-2026-09-15.md)。

最后更新：2026-09-15 01:07 CST。最新生产源码已完成全量v7：2259PASS/32条件SKIP/1历史hash断言FAIL（657.44秒）；仅测试断言按有意新增预算语义更新并保留旧内容hash，后继52PASS/0.71秒，无生产再改、未再全量重跑。原生v52冷恢复实点通过；N2 v3第一Task验证中，N1–N8和正式A/B96仍OPEN，Flash0、无打包。 [明细](../plans/taskSys2/testPhase1-budget-analysis-followup-2026-09-15.md)。

最后更新：2026-09-15 00:55 CST。最新N2/N7源码UI v52实点冷恢复/产物/回放/支持报告通过，5调用/4工具效果/42事件不变，0新模型调用；新完整回归v7与N2 v3仍运行。提示接线和离线分析相邻87PASS；N1–N8/正式矩阵仍OPEN、Flash0、无打包。 [本轮证据](../plans/taskSys2/testPhase1-budget-analysis-followup-2026-09-15.md)。

最后更新：2026-09-15 00:50 CST。非文档Planner新增原有累计预算语义，N7任务块统计区间已接入；旧源码决定性2FAIL，父级相邻87PASS/5.49秒、ruff/mypy通过。预算规则不改；真实N2 v3和源码UI v52仍在验收，N1–N8/正式96次未关闭、Flash0。全量2240PASS属于前一d495382源码，不能代替本次范围。 [证据及边界](../plans/taskSys2/testPhase1-budget-analysis-followup-2026-09-15.md)。

最后更新：2026-09-15 00:37 CST。最新完整编排2240PASS/32条件SKIP/0FAIL，668.77秒，645源码/测试hash不变。N2本地第二次校准429.665秒因Task预算失败，17调用236536tokens/0未知，未碰1800秒时限；真实知识复用仍OPEN。最新源码UI v51冷恢复/回放/产物/支持报告实点通过并保持运行。N1–N8与正式A/B96仍OPEN，本轮Flash0、无打包。历史偶发停滞根因不因回归通过而宣称修复。

最后更新：2026-09-15 00:09 CST。当前SDK源码UI v51冷恢复实点通过，旧Mission/Task/事件/5调用保持，3产物只重定位storage_uri且hash/VERIFIED不变，回放41事件差异0、支持报告9169bytes哈希核对，0新调用。首冷启动约102秒有未连接等待；并非全N8关闭。ARE真实动态硬判通过11调用58017tokens/275.301秒；N2官方复杂任务15分钟超时、1未知，30分钟同配置独立校准运行中。最新完整回归v5为2237PASS32SKIP1FAIL；冷恢复诊断父6PASS后v6运行中，原偶发停滞根因仍OPEN。N1–N8整体未关闭，Flash0，无打包。 [记录](../plans/taskSys2/testPhase1-two-wave-execution-2026-09-14.md)。

最后更新：2026-09-14 23:26 CST。源码UI v50 Mission已COMPLETED，实际code_test 10PASS，5次Qwen调用20708tokens/212.420616秒，3产物VERIFIED；Critic为NOT_REQUIRED。Host模型卡修复62c8ce10已push。此UI只覆盖固定N3 SDK，不覆盖后继N2/N6。最新SDK完整回归2236PASS/32SKIP/2FAIL659.01秒，失败正在定位；ARE硬判接线官方50PASS但真实动态校准尚未判分，完整Gaia2/judge仍OPEN。N1–N8仍未整体完成，0Flash、无打包。 [执行证据](../plans/taskSys2/testPhase1-two-wave-execution-2026-09-14.md)。

**最后更新：2026-09-14 22:55 CST。** 首组Qwen AgentDojo正常/注入对照官方utility均通过、攻击未成功；攻击20调用117984tokens/1046.198秒，真实Critic失败后第二attempt通过保留。新增known-zero拒绝审计/统计身份48PASS、结构化工具审计35PASS、ARE实际core32PASS；N2下游外部状态同步父审发现缺口返工。UI卡片固定GPT默认修复，13PASS＋实际源码页面正确显示本地Qwen/262144窗口，真实UI Mission仍待。N1–N8未整体关闭，0Flash，无打包。[范围与证据](../plans/taskSys2/testPhase1-two-wave-execution-2026-09-14.md)。

**最后更新：2026-09-14 22:31 CST — 两轮评测增量。** code profile默认v3修复有效知识引用提示并冻结旧v2；定向80PASS，真实本地知识分页/原文hash/VERIFIED产物通过18调用186793tokens/280.849秒。AgentDojo实际core正常校准官方utility和Mission通过7调用33945tokens/209.98秒；攻击配对运行中。独立AppWorld API观察父测17PASS/3.49秒，消费链仍在接线。先前完整编排2180PASS/21SKIP/900.33秒不覆盖所有后继代码。ARE桥接12PASS，动态core/judge仍待；原生v49只完成导航/256K表单检查。N1–N8均未整体关闭，0Flash，不打包。[四表、失败与范围](../plans/taskSys2/testPhase1-two-wave-execution-2026-09-14.md)。

**最后更新：2026-09-14 21:25 CST — 两轮后续开始执行。** Qwen256K单路/双路约21万实际输入规则与引用通过，但双路复核抢占+1，容量稳定性仍OPEN；在途token接纳正在实现。通用96次入口/困难语料/时段纯策略/执行来源回执完成局部验证，gap193PASS及回执后继53PASS、真实AppWorld来源检查通过；不将任意输出晋级可信API事实。AgentDojo官方接口5PASS，真实Orchestrator驱动仍待；Gaia2仅源码可行性核查。N1–N8均未整体关闭，0Flash调用、无本轮新UI验收或打包。[当前四表、时间与证据](../plans/taskSys2/testPhase1-two-wave-execution-2026-09-14.md)。

> 2026-09-14 21:42 两轮评测增量：在途加权接纳实测大输入串行226.644秒、小输入双路130.255秒，均0新增抢占；256K窗口未变。matrix源码身份绑定、未知用量/未评分和缓存计数父审通过；完整N1–N8、正式96次、跨客户端加权、Flash与本轮源码UI仍未验收。

**最后更新：2026-09-14 20:55 CST — 后续评测计划调整。** 先Qwen3.8 256K，再闲时Flash512K；各96次开发矩阵及困难用例门槛见[两轮评测协议](../plans/taskSys2/testPhase1-two-wave-evaluation-2026-09-14.md)。当前Host并发仍1，两路近窗未验；本次仅计划更新，0新模型调用、无功能完成度提升。

**后续范围核查：** 原评测提案尚有96次协调器扩展、AgentDojo、Gaia2、机制消融、条件性Context/策略优化与正式评测；当前已收尾的是缩小后的T0–T6及后继修复。已建立[八包后续路线与模型/时间分配](../plans/taskSys2/testPhase1-remaining-roadmap-2026-09-14.md)，新实现与付费批次尚未开始。

**最后更新：2026-09-14 20:10 CST — testPhase1后续修复与独立Flash16次回收完成。** 当前SDK功能5406fb5，完整编排2105PASS/20SKIP；256K四题S/R各3/4、D/F各4/4，有效14/16，648调用8760086tokens，runner2234.319秒。0未知用量/网关终态缺失/工具重下发，8个D/F终态预留0；知识复用和动态图收益未得到证明。旧本地9终态/7未执行/1未知保留。用户发现白屏已通过完整源码重启与实际点击恢复，原0残留仅指受管组；当前UI有意保持运行，独立评测服务已结束。 [完整结果、耗时和后续问题](../plans/taskSys2/testPhase1-flash-final-2026-09-14.md)。以下检查点保留原时点范围。

**最后更新：2026-09-14 19:59 CST — 用户报告的源码测试窗口白屏已恢复。** 受管服务退出后另有独立carrier窗口存活、15173/18140无监听；原0残留仅覆盖旧进程组。完整源码launcher恢复后，实际点击任务与REPORT通过；12表/5产物/15旧调用保持、0新增调用。当前UI有意保持运行，未改业务源码。[原因、恢复和收尾修正](../plans/taskSys2/ui-white-recovery-2026-09-14.md)。

**最后更新：2026-09-14 19:32 CST — 当前AppWorld契约v2源码验收通过，完整Flash对照进行中。** SDK5406fb5；最新完整编排2105PASS/20SKIP/0FAIL670.45秒（runner673.922秒），原生v48冷读12表/5产物/15调用核对、0新调用278.158秒，进程均清理。独立Flash400万/120调用校准四Task＋官方评分通过，实际44调用455861tokens/226.874秒；不对参数变更作单变量归因。新四题16次Flash块采用256K/400万/120调用，仍在运行；原本地9终态/7未执行/1未知完整保留，未混入后继得分。[最新证据与限制](../plans/taskSys2/testPhase1-followup-2026-09-14.md)。

**最后更新：2026-09-14 19:11 CST — AppWorld结果契约v2修复进入验收。** 本地后继矩阵9终态/7未执行，1未知调用触发停止；进程清理完成，未知Attempt预留保留。独立Flash探针暴露Worker模板非法schema_version；新AppWorld profile默认v2修复八角色示例，旧v1原样保留。反例8FAIL→新整组143PASS/8.92秒；完整回归与新源码真实复测仍待。不扩大调用预算，不打包，上下文仍本地默认256K，512K只限Flash。[当前结果和边界](../plans/taskSys2/testPhase1-followup-2026-09-14.md)。

**最后更新：2026-09-14 18:33 CST — 上下文测试范围调整。** 用户指定后续仅测试128K/256K/512K，本地默认256K，512K仅使用DeepSeek Flash。正在运行的四题16次复测为本地256K，冻结参数未变；新增档位不提前宣称通过。单次输出和累计Mission预算独立记录。其余功能与验收状态见下一检查点。[当前范围与证据](../plans/taskSys2/testPhase1-followup-2026-09-14.md)。

**最后更新：2026-09-14 17:55 CST — testPhase1三项修复进入真实复测。** AppWorld Mission显式256K池绑定接通现有522240任务预算下限；R改为有界版本化选择且独立valid_success；网关异常/取消补终态审计。定向155PASS/11.88秒；完整编排2096PASS/20SKIP/0FAIL，686.51秒，runner687.033秒，退出无残留；当前源码原生v47冷读通过，12表/15调用精确一致、0新调用，228.444秒；原四题16次本地模型复测进行中，首题S/R有效成功，D后续多轮仍受Task硬上限停止，预算规划效果不宣称全面完成。旧16次失败保持原样；默认256K/物理1，DeepSeek0调用，不打包。 [修复边界、耗时与证据](../plans/taskSys2/testPhase1-followup-2026-09-14.md)。

**最后更新：2026-09-14 16:47 CST — testPhase1测试执行完成，3项后续修复明确保留。** 功能源码全编排2079PASS/20SKIP/0FAIL，879.28秒；原生v45可信知识两Task/真实pytest/独立Critic/人工复核与冷恢复通过，v46复制数据重开通过，0重调。正式四臂16/16完成，282调用3076186tokens，5758.78秒；S有效3/4，R有效2/4（官方终态4/4，另2次自选JSON解析失败），D/F各0/4且均任务级预算停止。D/F无已观察知识复用/动态图收益；全部终态预留0、SDK工具重下发0，保留1工具失败/1拒绝及1网关outcome缺失。剩余Planner预算可行性、R选择协议、异常网关终态3项尚未修复。默认本地256K/物理1，DeepSeek0调用；不打包、不扩大96次。 [完整结果与证据](../plans/taskSys2/testPhase1-results-2026-09-14.md)。

**最后更新：2026-09-14 13:15 CST — testPhase1仍在执行。** 新code profile v2默认范围化pytest观察，17项新正负控通过；知识原文分页/精确引用/撤回投影通过离线检查，真实本地中英消费5调用24405tokens/113.63秒通过。AppWorld第三领域及真实保存恢复/独立评分接通；首技术探针预算失败保留，5题校准进行中。S/R实际BaseAgent身份/自选控制及计量离线通过；D/F整体、16episodes、T6、最新原生UI仍待。不覆盖历史Phase3验收，不打包。 [执行证据](../plans/taskSys2/agent-orchestrator-gap-review-testPhase1-2026-09-14.execution.md)。

**Last updated:2026-09-14 CST — local DGX source acceptance PASS.** Default local qwen38-flash-next,262144 shared total/228352 Mission input. Actual260001-input request PASS111.089s. Frozen Host922b2d7b/SDK6d4ddc7 native chat+code Mission PASS:7calls18654tokens, real pytest2PASS/CriticPASS/VERIFIED artifacts; cold rows identical0new calls. Native370.415s/cold72.388s, both clean exit. Source checks SDK105+Host81+foreground44+UI81/typecheckPASS. Prior v42 foreground failure retained; no packaging/full-model-quality claim. [Current evidence](../plans/2026-09-14-local-dgx/README.md). Earlier checkpoints below are historical.

**Last updated:2026-09-14 CST — native-discovered foreground fix.** First native-v42 chat failed before HTTP because the foreground adapter had not opted into configured LAN HTTP. Product adapter now opts in for registered endpoints; public/DNS/link-local plaintext remains rejected.44 focused checks PASS4.29s. Actual260001-token request passed111.089s with three correct markers. Native successor pending; original failure retained.

**Last updated: 2026-09-14 CST — local DGX connection checkpoint.** Source local256K total /223K input profile, exact offline HF template accounting and explicit private-LAN HTTP are implemented. SDK105PASS5.92s, Host81PASS18.33s, UI81PASS1.40s/typecheckPASS; actual short/tool/continuation counts match server. Near-window and native acceptance pending. [Current local scope](../plans/2026-09-14-local-dgx/README.md). Historical Phase3 evidence below keeps its original scope.

**Last updated: 2026-09-14 CST. Current Phase3 source acceptance:46 SOURCE PASS /0 OPEN /2 user-deferred packaging criteria.** P3.4-A04 now passes one fixed real deepseek-flash strict-profile FIRST/COMPARE pair on SDK ae8d37b, snapshot-v41:188calls1918557tokens,1029.73seconds; both strictPASS/COMPLETED, zero physical errors/unknown usage/reserve/rehandoff and zero residual test processes. Committed affected158PASS6.76seconds. [Current evidence and boundaries](../plans/2026-09-12-phase3-host-g/v15-real-pair-review.md). Host production18ff5d24 and prior native-v39/earlier evidence retain their own source scope; strict mode is explicit SDK configuration, not an automatic Host redirect or new native UI acceptance. One pair does not prove model-quality superiority. No packaging/installer/release/push; historical failed pairs retained.

**Historical checkpoints below retain their original dates and evidence boundaries; their open lists do not override the current status above.**

**Last updated: 2026-09-14 CST. Current source status45PASS/1 strict P34 OPEN/2 packaging criteria DEFERRED.** v13 fixed pair finished: COMPARE strictPASS, FIRST delivered but retained one physical tool_parse error;157calls1543084tokens/880.20s, both reserve0/rehandoff0. No running pair/child. Host production remains native39-tested18ff5d24; SDK safe diagnostic successor67PASS1.90s is separately scoped, not new native evidence. [Current pair](../plans/2026-09-12-phase3-host-g/v13-real-pair-review.md). Earlier checkpoints below are historical; no whole-Phase3 completion or packaging/release.

**Last updated: 2026-09-14 CST. Source acceptance audit:45 verified/1 strict comparison OPEN/2 packaging criteria DEFERRED.** Host307PASS248.67s;3 wheel-only checks and1 default real opt-in deselected. SDK initial22failures closed by86PASS, production unchanged from native-v39. All raw failures retained. Native code/Critic/pending-human recovery, publication and legacy context gates have scoped evidence. P34v13 running; no whole-Phase3 completion claim. [Current source audit](../plans/2026-09-12-phase3-host-g/source-cumulative-v39.md).

**Last updated: 2026-09-14 CST. Native v39 code/isolated pytest, explicit Critic evidence and pending-human cold recovery PASS within scope.** Two one-Attempt Missions; real10calls26932tokens/cache16384/0reserve. Actual four files read; pending cold selected-table hashes identical, UI approval completes both with zero new model calls. Native176.367s/cold106.073s exit clean. Current cumulative and strict P34 remain OPEN; no packaging. [Evidence and boundaries](../plans/2026-09-12-phase3-host-g/native-v39-review.md).

**最后更新：2026-09-14 CST — v37旧正常PLANNING恢复/产物换行/冷读通过限定验收；代码真测FAIL保留。** 原冻结规划一次提交，旧任务19真实调用112418tokens；旧/新报告冷读不变。代码用例因Critic先于code_test拿不到输出重复失败，UI取消后43调用172374tokens/0reserve。模型槽等待误报UNKNOWN已修复，实际kill恢复等13PASS19.84秒，新投影夹具补齐store读方法后2PASS0.09秒；新源码原生待验。 [证据与边界](../plans/2026-09-12-phase3-host-g/native-v37-review.md)。无打包发布。

**最后更新：2026-09-14 CST — 源码v37诊断重建修复20PASS/17.44秒，产物详情换行87 UI PASS与正式typecheck通过。** 列表短hash保持；最新源码原生待验。旧累计Host303PASS/5FAIL中2个实际故障已定向复验，3个wheel-only检查按源码范围暂缓，不改测试。SDK新输出扩展受预算/用量/取消约束，严格P34与整体仍OPEN。 [证据和限制](../plans/2026-09-12-phase3-host-g/source-v37-checks.md)。

**最后更新：2026-09-14 CST — LC2真旧库原生接入、新256K/512K共存及冷读通过限定验收。** 旧6360c205库由新Host2eee204e/SDKc19bbd0接入；原四Provider完整记录及旧冻结input/config不变，旧任务沿default继续；新任务各用256K/512K。21真实调用65595tokens，含父任务512K预算不足失败2879，后继默认预算交付。5Mission/25记录冷读hash保持，零新调用；不冒称原生并发压力。原生353.520/冷94.208秒正常退出。P34严格对照/最终累计仍OPEN，无打包发布。[证据与边界](../plans/2026-09-12-phase3-host-g/legacy-native-v36.md)。

**最后更新：2026-09-14 CST — 原生v36发布/复核理由与冷读切片通过。** Host2eee204e/SDKc19bbd0，真实DeepSeek14调用40392tokens，审批后本地发布正确绑定内容；复核理由直接进入重试，实际文件修订后接受。同源码冷启动两任务/选定持久表/发布文件hash一致，零新调用/重复发布。生命周期662.777+49.152秒，退出无残留。P32其余AC、LC2共存原生、P34严格对照及最终累计仍OPEN；无打包发布。 [证据与边界](../plans/2026-09-12-phase3-host-g/native-v36-review.md)。

**2026-09-14 CST: Planner/executor action contract v2 and atomic human-review reason are ready for a fixed-source native successor.** OriginalordinaryPlanner bytes match9f70 baseline; approvalcount usesactualdeploymentdecision; focused12PASS0.57s, earlier35PASS6.80s and UI117/typecheckPASS. Source-native behavior is still pending; v35FAIL retained. [Evidence](../plans/2026-09-12-phase3-host-g/journal.md). No packaging/release.

**2026-09-14 CST checkpoint: P32 nativev35 FAIL/no_progress,11calls47465tokens, no publication.** Planner source/destination clarification35PASS6.80s; Host atomic human-review note117UIchecksPASS1.47s and typecheckPASS. These successor changes are uncommitted and not yet source-native verified. Originalv35failure remains, overallPhase3 OPEN. [Evidence and limits](../plans/2026-09-12-phase3-host-g/journal.md).

**2026-09-14 CST source checkpoint:** Host now distinguishes genuine old unguarded pools from guarded pre-context pools and exposes named256K/512K pools alongside both. Pinned tokenizer plus launcher45PASS0.52s; SDK mixed-pool recovery/action/diagnostics72PASS13.07s and adjacent56PASS13.64s. This closes scoped software controls only; native publish/coexistence and final cumulative audit still pending. P34v12 strictFAIL and historical no-routing-evidence limit remain. No packaging/release/push. [Current evidence](../plans/2026-09-12-phase3-host-g/journal.md).

**2026-09-14 CST 更新：源码测试启动器新增隔离的本地发布目录显式开关，39项控制通过（0.17秒）；真实UI发布仍待。P34 v12严格FAIL：两臂交付、C合成失败后回退旧候选，167调用/1622330tokens/998.48秒。LC2共存与动作契约仍在修复测试，整体Phase3 OPEN。** 详见[当前验收记录](../plans/2026-09-12-phase3-host-g/journal.md)。无打包发布推送。

**最后更新：2026-09-14 CST — 512K真实多轮与冷读已验证。** 冻结SDKdc2f156源码两次DeepSeek Flash调用，实际输入520288/521469tokens，输出2664/664，总1045085tokens、cache0；后轮历史折叠后按新条件精确筛选，冷读零新增调用。42.987秒（runner43.35秒），合成材料范围，非原始文档质量证明。P36诊断PD4已关闭；SDK6fb5c50修复P34知识ID提示冲突和分页完整读取观察器，44PASS与独立复审通过，新真实pair/LC2兼容/最终累计及整体Phase3仍OPEN，无打包发布。见[长上下文结果](../plans/2026-09-12-phase3-host-g/long-context-results.md)。

**最后更新：2026-09-14 01:33 CST — P36 App诊断切片PD1–PD4已验证。** 当前Host `d093f55c` / SDK `dc2f156` 源码原生v33完成失败Mission诊断、重复导出及同源码冷启动重读。FAILED/34events/600tokens/0reserve保持；支持报告8205B及完整SHA一致，任务/事件/预算/执行等选定持久表hash不变，4受控调用/0重调。长路径回执已真实截图确认换行。源码UI生命周期1016.772秒（含等待），冷读62.016秒，均正常退出无残留。只关闭诊断切片，P34真实pair、P35补充与最终累计及总体Phase3仍OPEN；打包发布暂缓。证据与边界见[诊断验收](../plans/2026-09-12-phase3-host-g/p36-diagnostics-plan.md)。

**2026-09-14 CST P36 UI检查：** v32错误引用按预期拒绝，诊断/重复导出8205B且全持久表hash不变；截图发现回执长路径溢出，补自动换行后进行最终源码/冷读。PD4仍待最终验证，不提升整体完成状态。

**2026-09-14 CST原生前补充：** P36首次原生v31为FAIL_SETUP（旧夹具仅识别doc6/7，实际doc9），不能当错误引用拒绝通过；已修正已登记doc8/9兼容，并把有预留/待结算时的诊断标为记录不完整，未知数明确只计已入账记录。当前实际文档链与诊断11PASS20.69秒，UI89PASS0.921秒/typecheck/lint通过；源码提交后重测PD4，整体仍OPEN。详见p36-diagnostics-plan.md。

**最后更新：2026-09-14 CST — P36诊断与本地脱敏支持报告软件验证通过，原生验收待执行。** MissionControl鉴权后只读既有replay/attribution，逐字段投影原文/路径为hash，记录真实运行身份和未覆盖/未知/预留/未对齐用量；固定support目录内容寻址导出，不执行Provider/工具。源码6PASS10.26秒、已安装0.11.1 wheel16PASS24.37秒（身份仍version-only），UI86PASS0.941秒，正式typecheck/lint通过；SDK当前164PASS3真实opt-inSKIP214.27秒。独立审查发现的原文泄漏/能力判断/身份用量缺口已修复，PD4源码原生UI仍NOT_RUN。最新真实v11两组均完成交付但严格pair仍FAIL；P34/P35最终累计、旧pre-context兼容和总体Phase3仍OPEN。无打包发布推送。详见 plans/2026-09-12-phase3-host-g/p36-diagnostics-plan.md。

**2026-09-14 CST补充：真实256K多轮历史条件筛选与冷重开通过。** 两调用519027tokens，20.091秒；后轮历史已折叠仍正确按新条件筛选，冷重开零调用。新P34 context256-8m-out32k-v7已单独冻结8M/24等预算与256K/32K输出合同，真实执行前纯检查21PASS0.37秒、含原导出hash及实际公开策略绑定/容量floor；旧2M实验不变。子代理初版output_reserve/selected profile/floor遗漏已由主审补齐，首测试19PASS1schema形状FAIL修正；真实pair尚NOT_RUN。P34/P35-A04/旧pre-context升级与总体Phase3仍OPEN，无打包发布推送。详情见各long-context-results与SDKcontext256-pair-contract。

**最后更新：2026-09-14 CST — 256K/512K当前源码原生真调用、产物与冷读通过。** SDKdab3d44/Hoste183e400，两项真实Mission共8调用/24020tokens/0预留；各自实际容量保持，原生打开两份54B文件，独立进程冷读后任务/意图/产物/73事件/20journal/8selection/8Provider记录逐项不变，零重调。生命周期224.909+70.252秒；实际Mission9.871/11.156秒。新pinned256/512长历史旋转/原文/完整工具组/冷重放/超限零调用2PASS6.16秒，提交态相邻25PASS13.31秒。真正pre-context旧库自动共存升级及真实多轮长内容仍待；P34配对价值/最终累计和整体Phase3仍OPEN。无打包发布推送；详见long-context-results.md。

**最后更新：2026-09-14 CST — 256K默认/512K可选源码接线与真实容量探测通过，整片仍在验收。** 实际DeepSeek输入258149/520283tokens，两次各1调用正确回答首中尾记录与跨位置求和，9.296/22.855秒；输出默认8192/上限32768独立。新Mission默认总预算4M/8M（未用容量不计消耗），按任务冻结profile与预算。旧32K实际请求身份冷升级保持、全角色256/512受控完成/零调用重开4PASS2.97秒，SDK相邻26PASS10.01秒、Host相邻65PASS105秒、UI78PASS0.803秒。真正pre-context旧库自动升级仍OPEN，当前显式保留legacy；长历史及当前原生UI待验，不能宣称整片完成。旧v29原生动态预算/冷读通过，真实v10两臂仍失败（842844tokens/93调用），P34/P35-A04和整体Phase3未关闭。新探测观察器序列化返工及未知用量原FAIL完整保留。无打包/发布/推送。详情：plans/2026-09-12-phase3-host-g/long-context-results.md。

**最后更新：2026-09-13 17:50 CST — SDK动态预算修复已提交753b61a。** 已物化的综合/冲突任务预算仅计一次；初始Planner预留、取消任务已结算及在途支出仍保留。旧源码5决定性FAIL/6PASS，修复相邻214PASS/3真实opt-inSKIP71.88秒，独立Sol审查补齐公开冲突创建→动态提交→冷回执控制。新A400/F400实验总1920K，旧2080K配置在凭据与调用前静态拒绝；历史导出不改。真实v9两组均no_progress，747326tokens/95调用/564.99秒，未证明收益。最新源码原生与真实门待复验；P34/P35-A04保持OPEN，P36非打包功能继续核对，打包发布暂缓。SDK计划：plans/2026-09-12-phase3/p34/dynamic-system-budget-fix.md。

**最后更新：2026-09-13 17:13 CST — 当前源码UI复核完成，真实COMPARE门仍OPEN。** SDK生产代码043349f/Host5e186141的受控FIRST与COMPARE均从原生UI正式提交、完成验证、打开实际产物，并经新app/backend冷恢复。FIRST3600tokens/24调用/0预留/0rehandoff，170Mission事件/57journal/24selection不变，188.440+71.375秒；Host source-ui-search-v28/case-summary.json SHA2561cb657601e390d0b17b3fd1e9631ae4d801fe41aa03ff8fa46cce62fff88ba41。COMPARE2700tokens/18调用/0预留/0rehandoff，93Mission事件/40journal/18selection不变，421.115+135.708秒；仅预期deployment PolicyConfigDrift，ACTIVE未变；source-ui-compare-v28/case-summary.json SHA256094f0f6e76609644b1404bdb5eef5a6179d65f3d5080d3cabfd69cf82d6a54de。原生进程组退出，端口释放，防熄屏保留。真实v8FIRST实际COMPLETED，内层oracle错误240K与声明320K冲突，55b766e修正并以79持久请求完整离线复判PASS，零新增调用；原始FAIL保留。COMPARE实际A2budget_exhausted，未完成；真实391829tokens含27592有效失败响应用量，原测试观察器漏计，账本正确。两臂实际总1121808tokens/123调用，858.53秒；SDK pair-summary SHA256a17dec54c7afa0e4235c913912bb371dcad19449a4ca7238d36357ddbd5458bb。af1c76f修正观察器统计并另登记A480K/B480K/C400K/S240K、总2M不变实验，干净34PASS1.37秒；v9正在真实运行。产品生产代码未再改；P34/最终累计/P35-A04仍待重关，不打包/P36/推送。详细SDK计划：plans/2026-09-12-phase3/p34/v8-real-pair-review.md。

**最后更新：2026-09-13 16:50 CST — SDK Manager/selection 后继修复。** SDK043349f已提交：默认manager-v4明确严格图操作/片段wire，原历史模板不改；空COMPARE轮次允许原预算/截止时间内一次独立F验证，保留原A失败，仅F通过后正常改接C依赖。修复已完成COMPARE前驱丢失及C第二候选/新综合的冻结F输入复用；原子截止时间与旧回执错绑有负向控制。干净提交49PASS/9.72秒（runner10.04）；相关1292PASS/4旧版本断言FAIL已定向修正，后继52PASS/noSKIP/10.12秒。真实v7两臂no_progress，累计863878tokens/101调用；新同profile/material/oracle v8运行中，不能称P34完成。当前Critic修复源原生v27文档及新进程冷读PASS：7调用/1050tokens/0预留/0rehandoff，108B compare.md及46Mission事件不变；证据source-ui-critic-doc-v27b/case-summary.json SHA25615a3363707b078977e57d2886ead887eef5c91552e9bf4fa218c29b71eea297b。后继selection源码尚待当前原生验证；P34/P35-A04及总体Phase3仍OPEN。SDK详细过程见 plans/2026-09-12-phase3/p34/manager-selection-fragment-fix.md。不打包/P36/推送。

**Last updated: 2026-09-13 16:01 CST - same-Task Critic growth and typed admission fix.** Critic can transfer only its request deficit from the original protected Task hold, keeping frozen identity/price, sibling first-Critic floors and UNKNOWN usage intact. Nonretryable SDK admission now atomically rejects/stops the Task without schema retry or Worker redo. New decisive old-source7FAIL/7PASS; corrected patch63PASS6.72s, adjacent1074PASS60.52s, tokenizer11PASS0.39s and cold-library2PASS1.58s; mypy117/ruffPASS. Independent Sol/high review no concrete P0/P1/P2. Cold tests preserve actual request identities and200000 Critic usage through both failed-intent/error-layer gaps, no new handoff. Fixture and parent integration rework retained in plans/2026-09-12-phase3/p35/critic-hold-admission-fix.md. Clean committed real v7 pair and current cumulative/source-native gates remain pending; P34/P35-A04 not yet reclosed. No packaging/P36/push. SDK fix commit6c17d4d clean focused recheck76PASS8.22s/runner8.67s; actual v7 pair now running. Historical14:22 P35 completion is superseded until the reopened A04 gates pass.

**最后更新：2026-09-13 14:22 CST — P3.3/P3.5 功能范围累计验收完成。** 干净SDK f25a4de完整编排1826PASS/0FAIL/9真实Provider默认SKIP，pytest626.58秒、runner627.15秒，g-current-orchestrator-full-v10；父唯一pytest进程组已退出无残留。P33原46行/47断言/100selector已关联，本轮均非跳过；current-ac-association-v4.json SHA256 bbf8088f0f5eb279a14e86b4f6f98f12460db9e4d001feb4305ebb8a830d90f4。原始回放1265DB/1400数据库Mission身份/7670观察仍raw OPEN：116finding全归因（50负向、66直接状态/历史夹具），944诊断逐项保留（917canonical target在完成的扫描根、14原快照与搬移副本双hash一致、9负向、4非Mission SQLite夹具）；没有整测试豁免或宣称raw unknown为空。检查点间未观察删除文件及完整execution回放仍属范围限制。后继原生34DB/46Mission/80观察独立PASS零差异/错误/额外调用，0.847秒，Host replay-all-native-v5.json SHA256133846607875ee93355545f949cd461a378b25070bf5174f4cc71230f5050997。snapshot-v26含最新ed42919生产代码，COMPARE原生新建交付及冷读通过，18调用/0rehandoff/40journal/18selection/94Mission事件不变，生命周期178.704/90.287秒；部署层仅预期PolicyConfigDrift，ACTIVE未变。P35原8项均已有决定性软件/原生证据并关联此全量，8/8完成；P33按已批准源码载体范围完成，未声称安装包验收。P34固定真实pair v5两臂仍budget_exhausted，完整交付/收益门OPEN；默认FIRST不变，B240K→480K/S120K→240K且总2M不变的新对照提议待用户选择，不改旧失败实验。整体Phase3未完成，不打包/P36/推送。

原生退出补充：资源包装器所属进程组均正常退出，但后续进程盘点发现独立carrier PID71160（无backend/Vite监听）。已核对其完整路径属于source-ui-compare-v26，用SIGTERM退出并确认消失；不把进程组为空等同全部原生进程为空。额外窗口出现原因未判定，不冒称应用正常退出链已修复。Host证据 source-ui-compare-v26/detached-carrier-cleanup.json SHA256655eaf6269be6d8f2743cb1b136154b1ef66c40a652c0fd0a6b5d9fbd2b9b7a4。Mac防熄屏caffeinate仍保留。

**最后更新：2026-09-13 14:13 CST — 最新生产源码原生比较及冷读通过。** snapshot-v26（SDK ed42919，Host af8a490c；SDK f25a4de仅测试/文档后继）通过真实UI新建比较Mission `mission-78c1ee65bf2a4820`，两个候选分别实际code_test通过，独立C读取二者并正式交付。UI候选/综合状态正确，最终result.txt 45字节，SHA256 `8f6e827eda48a0d58a0722ea59494ab2273893c8e41ac2d137084f1bfabb99a7`。同源码冷启动UI重新打开全文；Mission/Task/产物/18 Provider记录逐项不变，0rehandoff，2700结算/0预留，40journal/18context selection/94Mission事件。全局事件99→100仅PolicyConfigDrift（ACTIVE candidates_per_task=2、启动配置=1），冻结ACTIVE保持，无重复调用。原生生命周期178.704秒、冷读90.287秒，正常退出且无进程组残留。受控Provider机制验证，不冒称真实模型收益。证据 `.local-test-evidence/2026-09-13/p33-g/source-ui-compare-v26/case-summary.json` SHA256 `780e52b243135f714bba7a86a00acaa3908cb28344c0846c65909894c52a04a3`。最新独立原生回放覆盖34DB/46Mission/80观察，PASS零差异/零错误、调用/效果/事件计数不变，0.847秒；`replay-all-native-v5.json` SHA256 `133846607875ee93355545f949cd461a378b25070bf5174f4cc71230f5050997`。SDK最新全量v10进行中，真实模型固定预算两臂交付门仍OPEN；不打包/P36/推送。

**最后更新：2026-09-13 13:13 CST — 全部既有原生状态回放与真实对照结果。** 当前SDK33b25b5的只读回放覆盖33个数据库、45个Mission、78次观察，0差异/0错误，Provider/执行效果/事件计数不变，0.781秒；Host原始索引 `.local-test-evidence/2026-09-13/p33-g/replay-all-native-v4.json` SHA256 `5d17f7df6d9b4903c4908d0e2b508bb3b9b1ccc6b07c09a8b0a05d9d37ce7b33`。新增Context v23、压力v24和文案v25均纳入，未产生新调用。SDK33b25b5真实deepseek-flash固定对照v4仍FAIL：FIRST270.300秒/443141 tokens/54实际调用与准入，F/B/C完成、最终S在保留首Critic额度后预算不足；COMPARE121.889秒/82812 tokens/12实际调用与准入，B首候选预算不足。原始任务、材料及预算未改变；无成功交付或质量优势声明。SDK证据 `.local-test-evidence/2026-09-13/p34-real-search-value-053c8ec4de264f18b16d36c96d816861/`，runner392.82秒。当前SDK全套回归与本次消耗归因进行中；P33/P34/P35累计门仍OPEN，不打包/P36/推送。

**最后更新：2026-09-13 12:55 CST — 候选状态文案原生复验通过。** snapshot-v23（SDK4ba53f4/Host5a939d6b）新建批准COMPARE Mission `mission-a595c73143d3275f`，两个已验证候选与独立C完成；实际UI显示“已验证的候选输入·已用于综合”和“最终综合结果·综合轮已提交”，不再显示待比较。UI打开最终result.txt，45字节/hash `8f6e827eda48a0d58a0722ea59494ab2273893c8e41ac2d137084f1bfabb99a7`，18调用/0rehandoff/40journal/94Mission事件；原生生命周期338.287秒，正常退出无残留。本次是新源码完整交付及文案复验，冷读沿用v21既有证据，不声称新一轮冷读。v21同目录跨源码resume被身份校验正确拒绝（0.449秒/无残留），改为新目录完整运行，原失败保留。Host `.local-test-evidence/2026-09-13/p33-g/source-ui-compare-v25/case-summary.json` SHA256 `48f8e049dc20d12e1264484ac6750c0dd182fd708233080c2508286de95d677c`。P34真实模型完整交付及P33/P35累计回归仍OPEN。

**最后更新：2026-09-13 12:48 CST — 完整原生验证背压。** snapshot-v23（SDK4ba53f4/Host5a939d6b）真实UI提交7个短来源Mission，2个实际Verifier有界等待。持久采样观察2RUNNING+2PENDING达到总上限4，BackpressureRaised seq140；首个验证转待人后再派发的Worker预留减为10000，随后总数降至低水位2，BackpressureCleared seq192，恢复20000，间隔20.222秒。UI读取Raised/Cleared历史、逐一打开83字节review.md并复核通过，7Mission全部COMPLETED/各900结算/0预留；42Provider记录/0rehandoff/105journal在人审前后不变。峰值未及时截屏，峰值与减速由同一次真实运行的持久事件/采样/预算证明；不是手动释放，两个Verifier均20秒自动解阻后运行真实Critic。进程组正常退出无残留，生命周期512.800秒。Host `.local-test-evidence/2026-09-13/p33-g/source-ui-pressure-v24/case-summary.json` SHA256 `616bb3deed0ca3c76a9212ec9c4fb67582ca120a4311c459a1d78c5743fdb5bd`。关闭P35-A03原生阈值/减速/排空缺口，保留最终回归与新发现的system Worker尾部增长问题；整体未完成，不打包/P36/推送。

**最后更新：2026-09-13 12:36 CST — 长Context原生交付与冷读通过。** snapshot-v23（SDK4ba53f4/Host5a939d6b）通过原生文件选择器导入原97,200字节来源，Mission `mission-cb2cd1cb76fc88ee` 正式交付，2550结算/0预留。12个8,100字节页面全部保留在真实SDK journal，8个Worker请求出现持久Context轮转，原instructions/user_input及完整工具组保留，实际Provider观察hash与selection逐一相符。UI打开380字节REPORT.md（SHA256 `c4f9c9eb9c57dca52dd45cf720f66092a0f401f335899a6ad69a263184b3c9d9`），首段/原约束/末段引用完整读取；冷启动读同报告和约束引用，51事件/17Provider记录/0rehandoff/37journal/17selections完全不变。原生/冷读生命周期299.810/271.067秒，两进程组正常退出无残留。受控Provider机制证据，不代表真实模型记忆质量。Host `.local-test-evidence/2026-09-13/p33-g/source-ui-context-v23/case-summary.json` SHA256 `7a0b1f773879e3f07b99f5f16d2d85a7f815e7427fd4a8c0df87e09a78fa1e0b`。关闭P35-A07当前原生轮转/冷读缺口，整体仍待完整压力与最新回归，不打包/P36/推送。

**Last updated: 2026-09-13 11:33 CST — native COMPARE and display correction.** Source snapshot-v21 (SDK f6115ed / Host485679e7) native UI selected approved two-candidate policy, submitted Mission27f1933fe6cf3134, and read final result.txt (45 bytes, SHA256 8f6e827eda48a0d58a0722ea59494ab2273893c8e41ac2d137084f1bfabb99a7). Both candidate code_test PASS, independent C code_test PASS and COMMITTED. Cold UI reread preserved Mission/Tasks/artifacts,18 Provider records,0 rehandoff,40 journal;94 Mission events unchanged. Global99->100 adds expected deployment PolicyConfigDrift for promoted2/config1, not a task rerun. Native/cold lifecycle186.640/151.657s,exit0,no residual. Summary Host .local-test-evidence/2026-09-13/p33-g/source-ui-compare-v21/case-summary.json SHA256 edd6cb7c64a7edd0b6f0a5c345ed13f0723b89fbb76b8e76ae1b4e0f51fd1130. Native test exposed completed candidates still labelled waiting; MissionSearch now distinguishes adopted inputs, unselected verified candidates, final synthesis, and stopped/failed states. Frontend77PASS1.17s, typecheck/lintPASS; corrected native wording pending. Long Context nativev21/v22 remains NOT_COMPLETED due CUA noWindowsAvailable after file import; no Mission/rotation claimed. Full SDK f6115ed default sweep1753PASS6missing-tiktokenFAIL12SKIP/592.51s; installed pinned test dependency0.14.0 and affected52PASS1.67s including all6 failures+3optional counters. Cumulative non-network1762PASS9real-provider default skips; actual P34 pair separately failed. Journal/context16PASS5.68s. P33/P34/P35 overallOPEN, no packaging/P36/push.

**Last updated: 2026-09-13 11:11 CST — current editable source fixtures.** Approved COMPARE now exercises two verified candidates and independent C with portable copied inputs in final artifacts. Long Context case reads twelve actual 8,100-byte pages, preserves original constraints and unresolved marker during window rotation, retains complete journal, validates three whole-unit citations, and checks cold-read invariants. Explicit test cases only. Correct editable SDK 826c0e1 and actual source attestation: g-native-current-source-integration-v8, 50 PASS / 1 SKIP in36.59s (runner37.12s). Raw evidence in SDK .local-test-evidence/2026-09-12/p33-g/. Native UI for these two new cases remains pending; no packaging/P36/push.

**最后更新：2026-09-13 10:51 CST — 源码原生负载、独立恢复与搜索链验收。** snapshot-v17（SDK0a1a050/Hostc61744d6）：三Mission/两物理槽中第三任务真实UI取消，释放前后无实际Provider调用；官方backup/restore到独立userdata后，成功/取消/待人三状态及96事件、14 Provider记录（13succeeded/1claimed）、34journal完全相同，恢复副本真实UI复核后正式交付，调用不增、rehandoff0。B2 summary SHA256 `0d4e3c76fbbf35ddc7ccda3eb5241e71147c92e234f9814ab01db25460591efb`。原生Verifier压力v19：UI显示第三Result PENDING，UI时点持久事件对应2RUNNING+1PENDING，20秒自动释放后3任务均交付；手动marker未观察，不声称pending上限4饱和。summary SHA256 `c67edef26d8360b5feb53f8c068d05904f1a2f54e2545326976a1b911611f6c7`。FIRST原生v20：保留A三次失败，F仅核选中片段，Manager改C依赖为F+B，C实际测试通过，S读取已验证C并实际测试通过；UI打开final.md，冷启动同hash `bc04ba9b12d5ab4e0729599c2cce15ca42d715152ea84e81484f0f78ac1c73c3`、26调用/0rehandoff/62journal/192全局事件不变。summary SHA256 `7ebfacc0d9cc21f1d3989598f13b320fdc97d423fc81095f37a581e7463a3479`。均为受控Provider机制验收，不冒称真实模型质量。Host证据根 `.local-test-evidence/2026-09-13/p33-g/`，对应source-ui-b2-restored-v18b/source-ui-pressure-v19/source-ui-search-v20。只读回放replay-all-native-v2.json：27数据库/35Mission/62观察，PASS零差异/错误，.730s，无调用/效果变化，SHA256 `a2cc5a33ba08f4dfe1c1c6cb3452148d37367b15dc2f003dcf829ef23f57382b`。当前SDK826c0e1五项回归修复56PASS/20.03s，新全量待；P33累计关联/P34/P35仍OPEN。真实FIRST/COMPARE原固定pair两臂预算失败已保留，测试漏接原生精确tokenizer/逐请求准入的配置正在修正，尚未重跑。长Context原生rotation与批准COMPARE UI待。不打包/P36/推送。

**最后更新：2026-09-13 10:12 CST — 原生负载测试入口。** 增加独立源码受控场景：三Mission/两物理模型槽，以及两个真实Verifier在Critic前有界等待、第三个Result排队。仅控制外部Provider和验证等待；原Planner、工具、format/rule、Critic、人审、取消、预算与实际验证结果均由正式运行栈产生。控制文件在独立userdata目录，原fixture输入不变。等待最多20秒且保留租约余量，超时记录notobserved后放行真实Critic；仅证明2个Verifier占用+1个待验证，不声称压满pending上限4。受控路由/取消/长资料/启动器/搜索38 PASS/16.03s（runner16.61s），g-native-pressure-host-v4；静态复审normal runtime不受影响。该压力场景要求新控制目录，已有trace或marker时同目录重建会拒绝，不作为压力恢复证据。实际原生负载UI尚待；N1doc9原始资料及冷恢复已通过。P33累计审计/P34/P35整体仍OPEN，不打包/P36/推送。

**N1 original-source acceptance — 2026-09-13 10:09 CST: PASS.** Source snapshot v16 (SDK aada164 / Host ba6be341), doc9, same two original files and original 400000/12 goal/budget: Mission mission-0b12722003e0b883 COMPLETED/verification_passed in291.398s, one Worker Attempt,320365 settled/0 reserved. Actual assistant journal submitted ordinal refs; canonical Claims:11 VERIFIED source attributions,3 SUPPORTED analyses,1 UNDER_REVIEW structural statement. REPORT SHA256 `812114f5b9f1252a56d43e4ff6815961a031aeec01e62da3f751103c0c9c3e52`. Parent opened report and both-source citations in native UI, including complete HA-12 row and 99-character conditional unit; full CAS report matched displayed hash. Parent and independent Terra medium review PASS: build/startup failure records acknowledged; historical verification is not current installation evidence. Same-source cold UI reread preserved artifact/citations/status and14 Provider records (11 succeeded/3 failed),0 rehandoff,60 events and SDK journal counts. Native/cold carrier lifecycles651.361/104.222s include manual inspection, both exit0/no residual. Host evidence `.local-test-evidence/2026-09-13/p33-g/source-ui-n1-v16/case-summary.json` SHA256 `195b17df5a66ee13937410abbbe59b4e75c255a49fa6766ee75808afc1972bde`. Historical failed N1 runs retained. This closes current N1 content/native/cold gate, not P33 cumulative audit or overall P34/P35. No packaging/P36/push.

**最后更新：2026-09-13 09:48 CST — 独立备份恢复的原生 UI 核对。** SDK 1f0c536 / Host 65e05252（source-snapshot-v15）的失败原始文档任务，经官方 offline backup/restore API 恢复到独立源码实例。实际 UI 显示原任务 budget_exhausted、doc8、284857 已结算/0预留、两来源原版本与无正式 Claim；两库任务/预算/45事件/SDK journal计数完全相同，14条Provider记录的状态及0 rehandoff保持，未新增调用。原生载体56.508s，退出0且无残留。证据 `.local-test-evidence/2026-09-13/p33-g/source-ui-b1-restored-v15/case-summary.json`，SHA256 `2e9fde82becffbe655ca53bd0065c1266042280260e62291c88d99732d03d137`。只关闭这个终态失败任务的恢复可见性用例；不证明成功报告、待审批或UNKNOWN恢复全矩阵。P33/P34/P35整体仍OPEN，打包/P36/推送暂停。

**N6 显示修正 — 2026-09-13 05:44 CST：** 原生 n6-half 验证1/2不确定条件按原策略可交付，局限与INCONCLUSIVE均保存；UI“实际判定：满足”措辞会误导，已改为保留不确定性，Mission统一显示“通过交付判定”。新增UI反例先红，修复后26项通过；新快照原生复验待完成，不能把该措辞修正算原生PASS。

**原生边界与恢复检查点 — 2026-09-13 05:40 CST：** 新冻结 SDK c8e2541 / Host b7dc4c64 综合1127 PASS/75.26s。N1v9真模型正式交付及同源冷恢复已核对；新Host显示修复在受控原生来源指令用例验证。N4来源指令归属、错误逐字引用、矛盾证据三例原生UI符合预期，独立原始证据保存在Host `.local-test-evidence/2026-09-13/p33-g/source-ui-n4-*-v10/`。实际OS SIGKILL后两库冷恢复2 PASS/9.59s：成功结果零重复Worker、独立Critic读产物；UNKNOWN保持原token/cost占用。仅覆盖该两边界，不覆盖完整Mission或P32逃逸进程恢复。FIRST新保护虽18PASS/0.91s，独立审查仍有系统hold丢cap和priced分别取整2项P1，修复中。P33剩余N6/active管理/O4、P34综合价值场景及P35其余门槛保持OPEN，不打包/P36/推送。

**源码与原生 UI 检查点 — 2026-09-13 05:25 CST：** N1v9 原始两文档、400000/12 原目标在 SDK c9a1f183 / Host 45c09756 源码环境完成：220.968s，正式 REPORT f6b192a3…f905、6 条 VERIFIED 逐字引用（两来源、完整表格行、完整限定单元），242431 tokens 已结算/预留0，13 次 Provider handoff。真实 UI 读报告、引用并冷启动重读，调用仍13/无重复；文档区“尚未判定”投影缺陷已修复，后端13 PASS/0.06s、前端25 PASS/0.912s及typecheck通过，新 UI 待验。动态新增已完成依赖的 Task 回放修复42 PASS/36.16s，原 v14 #14 历史43事件全覆盖/无差异；Python3.12空AST字段兼容35 PASS/0.29s，保持原生产基线。总体P33/P34/P35仍OPEN；进程kill测试仍在修复，FIRST请求保护仅helper7 PASS未集成；不打包/P36/推送。

**当前源码检查点 — 2026-09-13 04:49 CST：** P35 离线备份 20 PASS/17.29s；租约丢失恢复及取消 10 PASS/23.14s，受影响取消/恢复/租约回归 44 PASS/6.68s。N1v8 真模型仍失败：18 次实际调用、378113 tokens 已结算、当前预留 0；已定位 RUNNING 时终态 ordinal_to 为空造成 180s 错误超时。改读 SDK 持久进度的定向检查 8 PASS/8.98s，保持真正停滞超时控制；Host 六类文档场景及启动器 28 PASS/12.74s。上述为以 SDK e4da042 / Host 985e403 为基线的未提交修复证据；新原生 UI 待验，P33N1/P34/P35 整体 OPEN，不打包、不执行 P36、不推送。

**Source and native checkpoint — 2026-09-13 04:13 CST:** SDK protocol-error response parsing preserves independently valid Provider usage while still rejecting malformed tools (26 PASS/1.36s); missing/invalid usage stays unknown. Late-accounting automatic original-subject import/settle11 PASS/2.90s and receipt boundaries5 PASS/0.47s, independently reviewed. Citation repair retains failing claim/index/source/line identity without source-body reinlining3 PASS/0.46s. Broader integration v14 is still running/stalled in legacy recovery, not PASS. N1v7 was UI-cancelled after malformed-tool response without usage,170532 settled/108083 unknown held,13 physical handoffs, no successful value acceptance; original proof retained. Controlled source UI N2 delivered two28-Claim Missions (750 tokens each/zero reserve), actual long block290080 characters reached END_OF_LONG_TABLE, in-flight citation switching/CAS error and restored retry observed. N3 source supersede/revoke-reject/revoke-approve and historical read observed; cold verification in progress. N4/N6 boundary software27 PASS/10.67s including launcher identity, native cases not yet run. Whole Phase3 gates remain OPEN; no packaging/P3.6/push.

**Latest native/source checkpoint — 2026-09-13 03:26 CST:** N1v6 produced a rejected REPORT and9 proposed Claims; no formal acceptance. Rule failure from generated free-text quality criteria, then11 zero-call context-overflow retries.128156 tokens settled/currentreserved0, UI observed and exited cleanly. Repair5 software controls passed; broad1051 PASS2 regression FAIL3 optional skips. P34 fragment runtime2 failures; P35 priced rounding reserve review P1 open. N1–N6/O4 and overall P34/P35 remain OPEN. Details/current commands/evidence in Phase3 journals; no packaging/P3.6.

**Source checkpoint, 2026-09-13 03:12 CST:** integrated source checks:1037 PASS/2 legacy schema FAIL (48.27s); pre-schema15 reserved-attempt read compatibility fixed, targeted9 PASS/0.36s. Includes15 candidate controls, Mission system pool7, FIRST6, priced cold1 and missing-usage boundary2. COMPARE decisions and full immutable payloads now replay; frozen candidate deadline cannot dispatch new pending candidates. Controlled Host document fixture software2 PASS/6.21s proves28 formal citations and >256KiB paging; frontend search51 PASS/1.04s and typecheck pass. Fragment branch remains5 output-conflict failures (16 other controls passed); Mission-system runtime hooks, SUCCEEDED-missing-usage settlement, N1–N6/O4 and real P34/P35 gates remain OPEN. No release packaging/P3.6. This is an incomplete development checkpoint.

**Runtime checkpoint, 2026-09-13 02:50 CST:** default FIRST Critic tail reserve/consume/release is connected to actual production dispatch and passed6 controls/0.58s. Typed denial collection3/0.36s; true two-SQLite priced cold reopen1/0.40s. Scope excludes OS-kill, future Mission-level system pools and SUCCEEDED-without-usage late accounting. P34 joint run has3 FAIL/4 PASS/5 setupERROR; source inheritance defect identified and being repaired. N1 and remaining native/full audit gates remain open. See current journals; no packaging/P3.6.

**Source budget checkpoint, 2026-09-13 02:35 CST:** public Mission snapshot now carries current ledger usage in the same read transaction (39 SDK controls/5.12s); Host projection uses settled/current reserved values instead of historical Attempt totals (22 controls/8.42s; frontend49/0.941s). Provider tail/price primitives and durable typed denial:19 controls/1.00s. FIRST tail runtime wiring and native verification remain open; no release or completion claim. First-run collection/fixture failures retained in journals.

**Latest native checkpoint, 2026-09-13 02:25 CST:** N1 v5b (Host133aaa62 / SDKdfc9b7c) FAILED: sole Task90000/4 exhausted its attempts despite Mission400000/12. Both original sources were read in4 pages; no REPORT. Eight physical calls all settled, Mission86732 tokens/current reserved0. UI44494 reservation display is a confirmed projection bug; correction and typed denial stopping are in progress. N1–N6/O4 and P3.4/P3.5 remain open; no packaging or P3.6. See current Phase3 journal for immutable evidence.

**Current source checkpoint, 2026-09-13 02:08 CST.** Host source profile/projection16 controls and launcher19 controls pass. Full orchestration source run v4 observed180 PASS/5 FAIL (164.55s); three frozen-package inventory checks do not apply to editable installation and remain unpassed under the user-paused packaging gate. The document fixture omitted mandatory critic_review and a path assertion matched the runner ancestor; both corrected controls pass in v5 (2 PASS/4.78s). Remaining source modules v6:27 PASS/1 fixture-path FAIL (31.78s); the pure scenario-path oracle now supplies a path actually outside ignored evidence and passes v7 (1 PASS/0.04s). Production test gates remain unchanged. These are source software checks; new N1 native run remains open, v4b failure preserved. No release packaging or P3.6 work.

## Earlier checkpoint details

Source launcher identity controls: 19 passed, pytest 0.11s (wrapper 0.76s); native startup remains open. Resource identity covers metadata, not payload bytes. See the Host G journal for scope and evidence.

最后更新：2026-09-13。P3.3 G 仍在源码 UI 验收；P3.4/P3.5 仅必要接缝先行，未整体完成。当前新 Host source profile 将显式 editable SDK 的 Context 与逐请求 token 计数接到同一官方 tokenizer；旧执行池维持冻结配置，普通 wheel 模式保留原入口。`g-host-source-profile-summary-v3` 用独立源码 SDK 环境验证 profile 与文档摘要显示投影：16 passed / 0 skipped，pytest 0.34 秒、wrapper 0.98 秒；这是软件接线证据，不是原生 UI 或真实模型通过。源码启动器正在补恢复身份核验，新 N1 尚未开始；N1 v4b 真实预算失败仍保留。安装打包、发布和 P3.6 暂停。

最后更新：2026-09-13 01:04 CST。P3.3 G源码UI N1v4b已实际完成失败路径：Host e690bdcf、SDK e346689，Mission mission-61a22dea64fa4841为FAILED/budget_exhausted；真实flash35次成功、1次协议失败，已报告344854 tokens，不能说Mission花满400000。Worker1在90k Task预算下耗224780 tokens，Worker2耗115407，下一次Task预留时才拒绝。17页完整原文已实际可见，但模型一条引用错行、两条分析缺来源引用，rule正确拒绝。大页/逐请求预算与提交契约后继正在修复，N1/G未通过。原生正常退出PG2135无残留；不打包/P3.6。另修复Host诊断摘要读取：SDK摘要在detail内时不再显示空白；30项投影控制通过/8.31秒（wrapper8.97秒），实际UI后继重验尚待。见[Host G journal](../plans/2026-09-12-phase3-host-g/journal.md)。

最后更新：2026-09-13 00:31 CST。P3.3 G 的显式 SDK 源码身份已实际冷启动并从真实 UI 创建文档 Mission：Host d0c1ee4c、editable SDK 307 输入已核，manifest 分开 source_verified=true 与 installed_wheel_verified=false，Service pin 保持。N1 v3 业务仍 FAIL：长工具结果被 Context 截成预览，模型无法取得后文表格；主通过 UI 取消，正常退出 PG94878 无残留。另暴露 Critic 高频续租、取消及120秒外层超时的收尾缺口；SDK 修复的首批分页/实际 Context/取消控制39项通过，恢复与超时补证及原场景重测仍待完成。P3.3 未交付；P3.4/P3.5仅准备，P3.6和安装包打包暂停。证据与分层结果见 [Host G journal](../plans/2026-09-12-phase3-host-g/journal.md)。

最后更新：2026-09-12 23:44 CST：P3.3 G 源码开发接线新增显式 `editable-source` SDK 身份。正式 wheel 默认与 Service 0.3.13 pin 保持；仅非frozen进程、显式模式及独立source attestation可加载SDK源码。校验Git根/commit、两个生产包与数据全集hash（含新增文件）、editable安装metadata、版本及实际模块origin；文档修改不改变生产输入。main组装与RuntimeStack启动接线，编排manifest分别显示source_verified与installed_wheel_verified，不把源码当成旧wheel。71项定向控制通过/6.55秒，含真实composition启动前拒绝与依赖边界；主审及Ohm独立限定ACCEPT。实际源码冷启动与N1复验尚待完成；首次源码N1真实deepseek-flash任务因引用字段schema及Task预算不足FAIL，详见Host G journal。功能仍进行中，P3.4/P3.5未验收，打包/P3.6暂不执行。

最后更新：2026-09-12 23:05 CST。用户批准P3.1–P3.5功能优先、直接源码Tauri UI验收；暂停PyInstaller/安装包发布，不含P3.6。P3.3 G源码接线已有SDK1302/安装组合921/Host49/前端86项通过，源码后端与编排已启动；日志URL凭据过滤26项控制通过，真实UI文档任务待验，未交付。冻结探针修复4项控制通过并独立审查ACCEPT；不称安装包通过。见 [AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md)。

2026-09-10 删记忆 SDK（`plans/2026-09-10-remove-memory-sdk/`）：把 simple-harness-memory-sdk（Python 包名 `simple_harness_memory`）从 Host 主分支彻底移除，为后续「Agent 编排层」大改减少干扰。**这是临时性清理，不是架构定论**——记忆能力预计在编排层大改后以新形态回来。删除面：五个记忆工具（`memory_write/read/forget/recall/search`）与三个宿主组合工具（`procedure_use`/`procedure_discover`/`prospective_ack`），冻结清单 76→71 重签（`MANIFEST_SHA256 = 57900a1d…`，迁移 70→66）；`context_route` 五路由降四路由（删 `memory_standalone` 类型化召回与事件 V 的争议确认探针）；`HumanMemoryV7Runtime` / Procedure / Prospective / 记忆分析 lane / typed-use 权威 / OfficialMemoryFactsSurface 全部下线；p4_ipc 九条 MemoryPanel 消息与前端记忆面板、关系图、记忆审计、嵌入器状态卡一并删除。**保留**：`deskpet/memory/` 下 64 个纯 Host 模块（主对话会话账本 SessionDB、S1 证据链、foreground 队列/运行时、trusted disclosure、TaskScope），它们是主对话流程本身而非记忆系统；SQL 迁移链（含 procedure/s5c/primary 子目录）原样保留，旧 userdata 的 schema 链不能断。Harness SDK 0.7.10 仍把 `memory: AgentMemoryPort` 列为生产组装必填项，Host 改交一个**诚实的空记忆端口** `deskpet/sdk_adapters/null_memory_port.py::NoMemoryAgentPort`：召回返回合法空结果（`status=EMPTY`、`write_fence=None`，不伪造权威），release 无操作，record 返回 `status=APPLIED` 的合法回执但不持久化任何记忆。PERSONA 里那段告诉模型「可以召回长期记忆 / 有 Procedure 与提醒能力」的文字整段删除，换成一句诚实的能力边界声明。验收：冷启动到 `startup complete`、`/health startup_errors=[]`；`.venv/bin/python -c "import simple_harness_memory"` 必须 ImportError；后端目标测试目录失败集合与基线 `f8363463` 逐条相同；前端 typecheck / 725 用例 / lint 全绿。

最后更新：2026-09-08。语料 run-01j 复核三项收口（工作树 p1p2-recall-hints，未并入 main）：①`trigger_local` 一次都没到达模型 —— `_prospective_trigger_local` 以 `payload["memory_type"]` 判类型，而 prospective 的 SDK 公开 payload 只有 `action`/`trigger` 两个键（run-01j C04-12 工具回执逐字为证），判定恒假，C04-12/13/19/20 仍只看到 `trigger_at` epoch，遂算错一天或改口称「记录里没有时间」；现改为用 fragment 自身的 `memory_type` 判定（payload 与 `payload_hash` 仍逐字不变），并补 `tests/sdk_adapters/test_context_route_prospective_runtime.py` 运行时闭环（真实 C04-01 种子 + 真实调度注册结算 + 真实 typed recall + 真实 `context_route` 工具回执，断言回执 fragments 带 `trigger_local` 且与 payload 时区一致；单独回滚实现即红）。②`procedure_use` 连续 6 次 `tool_handler_failed`（C06-14，813s）—— `_validate_execution_identity` 把「本 Run 从未激活该步骤工具」(KeyError) 折叠进与真正标识/schema 漂移同一个错误，Host 又让 `ProcedureUseRejected` 直接抛出，模型只拿到 "Tool execution failed." 于是原样重发；现 `current_snapshot` 先判缺席回 `procedure_tool_unavailable`，`procedure_use` 处理器改为**返回**稳定码 + 可执行下一步（沿用 `_result` 的 `error_code`/`public_message` 约定，仍是 FAILED、仍什么都没绑定），工具描述补「先激活步骤工具」。③`procedure_discover` 零候选（C06-05/06/09/19）—— 种子确已落库（同批 C06-03/13 走同一机制命中），根因是 SDK 0.6.25 `match_score` 的纯词项命中覆盖不足，模型换个说法即全失，最尖锐的是 C06-09 查询「排程」与种子名「排日程」CJK 二元组零交集；本轮只记录不改 SDK，详见 `plans/2026-09-06-typed-use-primary/FOLLOWUPS.md` F08。`tests/sdk_adapters/` 与 `tests/memory/test_procedure_*` 的失败集与改动前逐条一致（`test_procedure_scope_runtime` 的 `foreground_run_already_terminal` 为环境既有）。语料复跑待做。[复核报告](../.local-test-evidence/2026-09-07/corpus-batch/run-01ij.review-report.md)。

最后更新：2026-09-08。语料 run-01g 复核 P1/P2 引导修复（工作树 p1p2-recall-hints，未并入 main）：①P1 —— `memory_types` 含 `procedure` 而 typed 召回按设计不返回未绑定流程时静默返回，模型据此判定「没有保存过流程」、12/18 例 C06 零调用 `procedure_discover`；现 `context_route` 的 memory_standalone 回执在「requested 含 procedure 且结果无 procedure 项」时追加顶层 `procedure_hint`（`reason=typed_recall_returns_only_applicable_procedures`、`next=procedure_discover`），随 extras 一并进入 `public_result_hash`，`ContextRouteReceipt` 与 typed-use carrier 绑定一字未改；typed recall 的资格口径不变（见 `DECISION-PROCEDURE-USE-CHAIN.md`）。②P2 —— prospective fragment 只交付 `trigger_at` epoch + IANA 名，模型换算出错致 C04 4 例失败；现 `project_recall_fragments` 在 fragment 顶层（不在 SDK 公开 payload 内，payload 与 `payload_hash` 逐字不变）渲染 `trigger_local`，格式为场景时区 ISO 本地时间 + 中文星期（如 `2026-09-07T09:00+08:00 周一`），缺失/无法解析的时区回退 UTC 而非宿主时区。PERSONA 补两句说明这两个字段的读法。新增 `tests/memory/test_prospective_trigger_local.py` 11 控 + `tests/sdk_adapters/test_context_route_tool.py` 5 控通过；`test_context_route_tool`/`test_typed_context_use_primary`/`tests/memory/test_prospective_*`/`test_primary_foreground_runtime` 与召回相邻 14 套件的失败集与改动前逐条一致（25 与 15，均为环境既有）。语料复跑待做。[复核报告](../.local-test-evidence/2026-09-07/corpus-batch/run-01g.review-report.md)。
最后更新：2026-09-08。历史因果组定界修复（工作树 m0623-adopt，未并入 main）：动态 Context 把含工具往返的历史轮压成一条无结束标记的 USER 引用块、当前指令又是紧随其后的普通 USER 消息，原生 r12 步 5 中模型据此把当前指令当成历史块的一部分而拒不执行；因 SDK 要求 `provider_messages[-1]` 与原始 `Message(USER, text)` 逐字相等（当前消息不能加前缀），改为在 `project_history_group` 的引用块尾部追加确定性 `HISTORY_SUFFIX` 结束标记并点明「最后一条 user 消息才是当前指令」，`verify_history_projections` 双端校验后切片解析，PERSONA 补同义一句。详见 [PROJECT_STATUS](PROJECT_STATUS.md)。

最后更新：2026-09-08。F07 修复（工作树 m0623-adopt，未并入 main）：模型把 typed 召回项 id `recall-item:…:1` 当作 `context_page_in` 的 `reference_id`，处理器拒绝（`tool_failed`、value 为空），`primary_dependencies.py` 的 history 校验把该失败 carrier 视为不可核验并抛 `PrimaryHistoryDisclosureRejected` 终止整个 Run（语料 run-01e C04-16、run-01h C06-13 两次复现）。现对 `context_page_in` 的失败 carrier 做**确定性复核**而非跳过：用同一 arguments 重放 `admitted_page`/`admitted_current_page`（按引用前缀分派），同样拒绝才 `continue`，重放反而成功 → 仍抛 `scope_search_result_unverified`；primary 页引用还要求重放拒因与记录的 `error_code` 逐字一致，原 `primary_page_hash_mismatch` 分支并入其中，未泛化到任意工具。引导侧：`CONTEXT_PAGE_IN_SCHEMA` 描述与 PERSONA 写明 `reference_id` 只能取本次请求已备好的页引用（truncation marker 的 `reference_id`/`source_hash`，或 `[Context page-in reference: id=… hash=…]` 行），`context_route` 的 `fragments[].ref` 不是页引用，无页引用则不要调用。新增 `tests/execution/test_scope_disclosure_runtime.py` 复现控 + 重放不一致负控 2 通过（复现控修前红）；`tests/execution`+`tests/sdk_adapters/test_typed_context_use_primary.py`+`test_no_recall_gate.py` 失败集与改动前完全一致（52 失败 10 错误，均为环境既有）。语料复跑待做。[F07 条目](../plans/2026-09-06-typed-use-primary/FOLLOWUPS.md)。

最后更新：2026-09-08。TaskGrant 时钟接缝修复（工作树 m0623-adopt，未并入 main）：C04-15 `TaskGrant expired before activation` 根因是铸造侧用真实墙钟、激活侧用跑道注入的场景时钟——`main._initialize_capability_runtime` 没有 `clock=` 形参，`PreparedAuthorizationRuntime`/`AdmissionTaskGrantRuntime`/`SdkPreparedAuthorizationPolicy` 全部退回 `time.time`，场景时钟超前真实时间 8h（TTL）以上时 grant 必然先过期；现该函数暴露 `clock=`（默认 `time.time`，生产调用点不传 → 生产行为零变化）并下传三处构造，`corpus_scoring_session` 铸造侧与激活侧注入同一场景时钟，`task_grants.py` 的过期判定一字未改。新增 `tests/permissions/test_task_grant_clock_seam.py` 9 控通过（T1 +30d 铸造/激活、混用时钟反向见证仍过期、T2 偏移矩阵 ±30d/±1d/0、T3 注入贯通性 AST 断言），修前 T1/T2/T3 红；`tests/capabilities`+`tests/faults`+`tests/quality/test_corpus_c04_prepare.py`+`tests/sdk_adapters/test_product_host_ports.py` 失败集与改动前完全一致（17，均为环境既有）。[裁定](../plans/2026-09-07-corpus-c01-local/DECISION-C04-15-TASKGRANT-EXPIRY.md)。

最后更新：2026-09-08。语料跑道 C04/C06 两项修复（工作树 m0623-adopt，未并入 main）：①C04 prospective 零召回是跑道自伤——评分前 `corpus_scoring_session.py` 关掉 `MemoryAnalysisLane` 连带停掉 `ProspectiveRuntimeLane`，pending 提醒拿不到 `state='accepted'` 的 `prospective_scheduler_registrations`，SDK 类型权限门在词面/向量打分之前直接拒绝。现新增 `corpus_prospective.py`：评分轮前显式 tick 生产 lane（最多 3 次）并断言每条种子提醒都有 accepted 注册，缺失即 `SETUP_NOT_READY`，不再静默进入评分。短时域 `PrimaryShortIndexWorker` 经核不是同一根因（评分起点历史为空，空 chunk 集不激活世代），且补 tick 会改变评分物理请求内容，故只记录不改。②C06 procedure gold 口径改为 `required_procedure_access=procedure_discover`，`required_types` 只留 semantic；评分器与复核提取支持该字段，提示词加 1 句发现面指引。不改 SDK 两个资格门、不改 Host `context_route`。新增单测 11 通过，`tests/quality/` 失败集与改动前完全一致（35，均为环境既有）。gold 与编译器改动落在 Memory SDK 仓本地提交 `4ce5b20`（未 push）。[分析](../plans/2026-09-07-corpus-c01-local/DECISION-PROSPECTIVE-PROCEDURE-RECALL.md)。

最后更新：2026-09-07。F06 修复（工作树 m0623-adopt，未并入 main）：provider 传输超时后 SDK 把调用记 UNKNOWN、Run 进 waiting 等 Host 结论而 Host 用 Noop 且从不调 `reconcile_incomplete`，前台记 `BOUND_WAITING` 后退出 → Run 永久停摆。现 Host 侧 retry-once `ProviderReconciliationPort`（同 request 首次未知 → `CONFIRMED_NOT_STARTED` 重发一次，再次未知 → 前台 cancel）+ `RuntimeReconciliationPort` 调 `reconcile_incomplete` + 前台 waiting 后续推 + 重启恢复被挂 waiting Run 的工具授权；新增单测 5 通过、列出套件无新增红；原生 r12 待验。[分析](../plans/2026-09-07-native-main-journey/DECISION-PROVIDER-TIMEOUT-STALL.md)。

最后更新：2026-09-07。S6 Task 2 后端补齐（工作树 m0623-adopt，未并入 main）：新增 HUMAN `task_scope.list`（自有 scope、updated_at desc keyset、上限 32、无 archive、不授权），`task_scope.open_exact` 顶层只读 `binding_summary`（mode/根/凭证/Host re-stat state）与 `drift_probe`，公共通道拒绝客户端 `live_probe`→`human_memory_request_invalid`、drift 标 `host_unavailable`；前端「最近任务」显式按钮与绑定摘要只读展示。pytest 84 通过（含新增 3）、vitest 13 通过、typecheck/eslint 通过；原生真实点击未验。[审查](../plans/2026-09-07-native-main-journey/REVIEW-TASK-PANEL.md)。

最后更新：2026-09-07。C06主六新描述控6PASS3.20s、16sources已控；后继17/18/19额外Episode/第二Procedure/设备声明源码及独立3控NOT_RUN。19/20仅具备来源映射；19缺算法/实际设备能力证明通过source_limits明确，18真实finance适用性仍待runtime，01等真实archive。无SDK/session/资源变更。[契约](../plans/2026-09-07-corpus-c06-preparation/CONTRACT.md)。

最后更新：2026-09-07。C05当前任务保持/完成任务只读/提前切换拒绝3控分批通过47.18s；旧scalar拒Host混合声明已按原来源绑定修复。C09正式退役/原子双修订两新组合首次2PASS14.69s，关闭fixture再生产重开/评分请求隔离。均受控HTTP非质量，PG87968/88533/88901清空，无旧绿重复。[C05](../plans/2026-09-07-corpus-complete-dispatch/C05-STATE-RESULTS.md)／[C09](../plans/2026-09-07-corpus-complete-dispatch/C09-RESULTS.md)。

最后更新：2026-09-07。C06新增六条条件Procedure来源首次6PASS3.20s，旧11未重跑；保留原描述/确认/授权/不删除限制及双条件分支，16/20来源准备有控，非执行许可/跨Task/模型质量。PG88468自然清空。[结果](../plans/2026-09-07-corpus-c06-preparation/RESULTS.md)。

最后更新：2026-09-07。C06主r2报告11控通过（10条setup，原r1顺序断言红保留）；后继05/07/10/13/14/20六条安全条件描述映射及独立控源码NOT_RUN，16/20仅具备准备映射。确认/授权/不删除/分支条件保留，不构造动作授权，不算actualTask或质量；剩01/17/18/19来源未闭合。[契约](../plans/2026-09-07-corpus-c06-preparation/CONTRACT.md)。

最后更新：2026-09-07。C06十条S+Procedure来源及编译共11唯一控制通过：原r1 canonical操作顺序测试假设红，改按真实operation_id后原红/未跑/新七项11PASS4.88s，公共job/精确来源/foreign owner/冷重开。非跨Task/物理评分/质量通过；PG87793/88125均清空。[结果](../plans/2026-09-07-corpus-c06-preparation/RESULTS.md)。

最后更新：2026-09-07。C06后继同构setup新增06/08/09/11/12/15/16七条，仅SPECS映射与独立控制selector；复用32eb已审builder/authority，首3条selector保持固定。20原setup/hash未改，10条具备准备源码，全部新控NOT_RUN，非跨Task/模型质量结论；其余10条约束仍明确保留。[契约](../plans/2026-09-07-corpus-c06-preparation/CONTRACT.md)。

最后更新：2026-09-07。C09编译+19标量公开修订准备首次20PASS10.59s：真实job/原新receipt/同ID rev2、退役不入当前召回、16不变字段r1及20原子双修订。07回填单位已按原setup修正，13 Procedure与正式dispatcher仍待；不计模型质量。PG87580自然清空。[结果](../plans/2026-09-07-corpus-c09-prepare/RESULTS.md)。

最后更新：2026-09-07。C06跨scope语料准备首组02/03/04新增专属public Host S1→Memory分析job→Semantic/Procedure回读源码；20原setup/hash全保留。全部新控NOT_RUN，未接共享scoring/session，未证明跨Task typed召回/非SELF出站/质量。无测试、模型或SDK制品变更。[源码契约](../plans/2026-09-07-corpus-c06-preparation/CONTRACT.md)。

最后更新：2026-09-07。C08历史纪要15/检查列表16两个新控制首次2PASS16.01s：实际main旧USER与派生assistant→真实job→公开抑制→生产重开→下一受控HTTP无旧内容。PG87213自然清空；文档文本来源，非文件/模型质量，正式dispatcher接入另待，旧绿未重跑。[结果](../plans/2026-09-07-corpus-c01-scoring/C08-DOCUMENTS-RESULTS.md)。

最后更新：2026-09-07。C05正式04/09/14/20多轮+真实empty及来源parser共6控制通过74.03s：同root真实setup/审批/marker/closure/terminal、过滤prefix、独立评分/followup/exact resume；context_route控制/physical来源误分类修复已在真实main闭合。原scope红/FK夹具红保留，PG85016/85509/85863均清空。固定HTTP非模型质量，剩余16case仍待。[结果](../plans/2026-09-07-corpus-complete-dispatch/C05-RESULTS.md)。

最后更新：2026-09-07。C08正式dispatcher e81a9af7+helper f45da5f9新增1PASS8.00s：01跳scalar，真实旧组/抑制/生产重开CONFIRMED后独立评分Provider，next physical无旧内容/统计1；只本共享入口01组合，不重复旧5绿、不计模型质量。PG85743自然清空。[结果](../plans/2026-09-07-corpus-complete-dispatch/C08-RESULTS.md)。

最后更新：2026-09-07。C05审批/缺证据3唯一控制分批通过：r1真empty与missing通过、pending无Effect红；公开audit+response身份proof修复后仅原红与False新控2PASS2.75s。无typed原因False不冒真零，PG83853/84899清空；marker/closure及多轮actualmain另首测，非模型质量。[结果](../plans/2026-09-06-corpus-public-seed/C05-AUTHORITY-RESULTS.md)。

最后更新：2026-09-07。C08-01/06/11/18保留旧USER+assistant摘要实际main及wrongassistant共5新控首批5PASS38.78s：原job APPLIED/IDLE，公开suppression后两history隐藏，重开生产authority后下一physical请求无旧内容。PG84053五child自然清空，非真实模型/原生/rolling-summary；正式dispatcher待接。本批也确认d60异步诊断两SDK来源实际写出且无未await警告。[结果](../plans/2026-09-07-corpus-c01-scoring/C08-RETAINED-RESULTS.md)。

最后更新：2026-09-07。Host诊断异步消费修复d60a94f4：真实installed Memory SQLite快照/timeout-cancel两个新控及三个受影响同步控制首批5PASS1.04s，PG83640清空。main改显式await，尚待下一新组合观测；SDK诊断版本硬编码原0.6.0另待，不冒称完整审计或改制品。[结果](../plans/2026-09-07-sdk-async-snapshot/RESULTS.md)。

最后更新：2026-09-07。C05新增07/08/10/11及TOOL调用ID共5唯一控制分批通过（r1 3绿2红，完整标题查询修复后仅2红复测2PASS4.76s）；固定身份真实分页，不保证所有并列ID顺序。PG83131/83471清空，无真实模型/原生结论，正式main多轮接线仍待。[结果](../plans/2026-09-06-corpus-public-seed/C05-RUNTIME-RESULTS.md)。

最后更新：2026-09-07。当前H0710/M619 C01-06实际main完整路由控制新增1PASS7.16s：真实job同ID修订→nullable proposal/公开审批→真实typed route→下一physical请求exact fragment为rev2小周。HTTP两响应受控，不算模型质量。原错字段oracle红保留，PG81693/81834都清空；原生/服务model_not_found仍待。[结果](../plans/2026-09-07-corpus-c01-scoring/MAIN-REVISION-ROUTE.md)。

<!-- 最后更新：2026-09-07 -->

C05 固定 f78004ef 在 H079/M619 installed 的原5红定向复验5PASS/12.15s，3绿未重跑。
04/09/14真实material marker→closure→来源绑定字段、20归档/prefix、真实评分分页与late suppression精确USER-only通过。
仅确定性fixture/public runtime，不计模型质量或C05全部20准备；最终physical outbound race及其他case接线仍待完成。
PG80017 exit0/remaining=[]，原两批红保留、WIP隔离，已释放资源。
[来源、命令及历史结果](../plans/2026-09-06-corpus-public-seed/C05-RESULTS.md)。

最后更新：2026-09-07。主H0710/M619完整来源组合新增3唯一控制分批通过：C01同ID修订/公开选新版、可信日期冻结与跨日；C07 actualmain真实recent fixture终态后独立scoring Run/统计，评分HTTP受控。r1两绿+C07错误oracle红，r2只红1PASS7.28s，PG80368/80594皆清空；无WeMM实际加载，非真实模型质量。06/11正式评分适配仍待接，服务model_not_found独立阻塞。[结果](../plans/2026-09-07-corpus-c01-scoring/MAIN-PHASE-CLOCK.md)。

最后更新：2026-09-07。C08标量准备叶7022e8e0/287176d0在H0710/M619/S0313首批13PASS5.55s，PG79749自然清空。12事实真实APPLIED+ACCEPTED/抑制前非空→公开EVIDENCE suppression→冷重开隐藏且S1保留；其中01/02/04/09显式partial、另8case派生源未支持，不称12完整setup或模型质量。正式评分接线仍待。[结果与未完边界](../plans/2026-09-07-corpus-c08-prepare/RESULTS.md)。

最后更新：2026-09-07。计分叶ebd81721修缺response时exact预测指标误零，改为null并保留lower_bound；失败denominator/credit不变，旧r4不覆写。仅affected真实SDK failed单Run控制1PASS0.91s，PG78605自然清空；非模型质量。[指标与结果](../plans/2026-09-07-corpus-c01-scoring/MISSING-RESPONSE-METRICS.md)。

最后更新：2026-09-07。独立单POST诊断收到HTTP400/model_not_found，param=model，message unknown provider for model gpt-5.5；1post/0工具，PG77944正常退出无残留。只证明该次拒绝，不追认原r4同因、不称nullable线上通过。主另报告/models列该模型，清单不等于POST可用，暂停进一步请求并等待模型取舍。[受限结论与审核证据](../plans/2026-09-07-corpus-c01-scoring/HTTP-REJECTION.md)。

最后更新：2026-09-07。C07独立准备叶（业务ade43237/测试修41296300）在原H079/M619载体分批6个唯一控制通过：20原setup编译边界、3种真实非空seed/job/public冷回读、06/14真实最近组→下一确定性请求。首批同因字段5红保留；PG77451正常退出无残留。只证明helper/Context准备，不是20条实际评分READY；正式06/14评分Provider相位、标量actualmain组合及模型质量仍未验，不改S3完成度。Dirac限定终审已接受并接入隔离主候选。[结果与边界](../plans/2026-09-07-corpus-c07-prepare/RESULTS.md)。

最后更新：2026-09-07。H0710/M619/S0313实际main安装组合1PASS6.38s，PG76882自然退出清空。新候选eaa72b51显式复验C01-20仅1请求HTTP400、无模型响应或工具、EXECUTION_FAILED；原因旧日志不可恢复，后继有界诊断已接入，不猜原因。PG76962自然退出9.136s且清空。240历史3个不同case/0通过，缺响应不算零extra的质量成功；原生仍待，防熄屏持续。[安装态](../plans/2026-09-07-corpus-c01-scoring/INSTALLED-0710619.md)／[真实复验](../plans/2026-09-07-corpus-c01-scoring/REAL-R4.md)。

最后更新：2026-09-07。Host HTTP拒绝诊断叶0be92572/60e6ea88：r4原400未保存body/private_cause，原因不可回溯。借原client.post在SDK拒绝前记录白名单有界脱敏字段/bytes/hash，不改状态分类、nullable或重试；新增本地HTTP组合1PASS0.01s，PG77306正常清空。仅已注入secret脱敏，非未知凭据检测；尚无真实服务拒绝原因，缺response不能把extra0当观测零。原FAIL保留。[事实与结果](../plans/2026-09-07-corpus-c01-scoring/HTTP-REJECTION.md)。

最后更新：2026-09-07。Harness0.7.10已从审定031fdc6不可变源离线构建一次并从vendor安装新H0710/M619/S0313 target，174/92/116成员逐字节一致。Host nullable叶与生产pin/lock/manifest同批接入；锁检查通过。4个源控制分批通过，当前installed功能组合/失败case复验及原生仍待。旧H079制品与三原FAIL保留。[制品与边界](../plans/2026-09-07-corpus-c01-scoring/INSTALLED-0710619.md)。

最后更新：2026-09-07。nullable后继Host2d64e6e5/fad81ebb配SDK031fdc6/0.7.10 source新增4唯一控制通过；仅两workspace/source字段允许JSON null，3reuse判断一致，非适用hash拒绝，rawhash与exact绑定不归一。原夹具红保留，PG76045清空；需主统一新wheel/installed组合后使用（旧H079不支持），未称main/模型质量通过，原3case FAIL保留。[契约与结果](../plans/2026-09-07-corpus-c01-scoring/NULLABLE.md)。

最后更新：2026-09-07。Manual组合原生UI固定e1e714d2已一次完整TypeScript/Vite/Rust/app构建通过；独立bundle端口18120，PG72450正常退出清空132.283s。尚未启动；先待SDK nullable继任/主组合及失败链复验，再用本UI验收。仅后端变化不重复同UI构建，防熄屏持续。[构建](../plans/2026-09-07-manual-workspace-binding/BUILD.md)。

最后更新：2026-09-07。C01-20固定2c02be03首次真实评分仍FAIL：4Provider/3路由拒绝，无A/B；明确nonstrict与omit指引未解决实际环境。PG71822自然退出51.88s且清空。240历史3个不同case尝试/0通过，暂停扩跑同故障；推进SDK可选null支持，修后显式新候选复验失败链，旧FAIL保留。防熄屏持续。[真实结果](../plans/2026-09-07-corpus-c01-scoring/REAL-R3.md)。

最后更新：2026-09-07。Host9073b965显式发送function.strict=false，保留原optional参数/精确workspace reuse校验，并给memory_standalone省略字段的公开失败指导。Dirac源窄审后唯一fakeHTTP→SDK参数→Host handler/ledger组合1PASS0.19s，PG71603正常退出无残留；空recall/合成tool context只证明协议路由，不代表真实relay/main或质量。C01-10/13原FAIL均保留（2尝试0通过），新真实case另验。[合同及结果](../plans/2026-09-07-corpus-c01-scoring/NONSTRICT.md)。

最后更新：2026-09-07。Manual workspace UI产品ef0ed7bf/夹具修48169ae8/结果7324a740已独审接受并合候选；真实Host授权链与View父卸载恢复7backend＋4UI分批通过，原红保留，PG69778清空。包含工具发现说明的事实修正，尚不宣称解决模型反复搜索；组合构建/native、App进程冷启动自动发现仍待验。[结果](../plans/2026-09-07-manual-workspace-binding/RESULTS.md)。

最后更新：2026-09-07。新C01-13真实324aa613首次评分FAIL：15物理请求/14次路由因无关workspace参数拒绝，未取得A；原提议四类型extra3保留。176.565s自然退出且PG69877清空，退出修复真实生效。240已尝试2/通过0；暂停同故障路径扩跑，修参数无值契约与核预算跨恢复计数。全阶段防熄屏保持。[真实结果](../plans/2026-09-07-corpus-c01-scoring/REAL-R2.md)。

最后更新：2026-09-07。Procedure prompt/v5.1叶342e2722/20f58862已独审：6限定控制通过；2次真实分类与public strict mutation提交通过（未采用流程→DRAFT+Episode，一次性任务→仅Episode），零重试，PG69158正常退出。旧v3/v4/v5持久请求保留；这只是Provider适配器/编译/公开写入，durable分析job与原生完整链仍待验，原r24FAIL保留。[真实分类](../plans/2026-09-07-procedure-draft-classification/MODEL-RESULTS.md)。

最后更新：2026-09-07（v6）。分析协议 v6（`memory/analysis_proposal_v6.py`，默认 `host-analysis-prompt/v6`）允许模型在同一提案内提出 `semantic_relation`（`applies_to`：claim→procedure/prospective），编译为 SDK `SemanticRelationMemoryPayload` 并声明 depends_on；端点未知/自环只拒绝该关系操作（`analysis_relation_endpoint_unknown` / `analysis_relation_self_loop`），其余操作照常。控制：`tests/memory/test_analysis_proposal_v6.py` 8 项 + `tests/memory/test_analysis_v6_public_relation.py`（假 Provider 提案经真实 outbox/analysis job 写入公开 Memory SDK 后 twin graph 出现 1 条 applies_to 边）通过；旧 v3–v5.1 持久请求按版本恢复不变。真实模型关系抽取与原生图谱边展示（r8）待批量语料释放资源锁后验证。

最后更新：2026-09-07（M0.6.23 采纳）。Host 已 pin Memory 0.6.23 候选（源 78ddf386，wheel 56a1a0dc…，schema 7.4 附加、7.3 库打开时前向）：`human_memory_v7.py` typed 计划恒请求 VECTOR（认知记忆向量通道），`short_index_worker.py` 同 tick 重建认知向量世代，`corpus_scoring_session.py` 评分轮前显式重建（跑道无 worker）。installed target `.local-test-evidence/2026-09-07/installed-h0710-m0623-s0313`。Host 控制：sdk_candidate/图谱/v6 关系/提醒 notice/procedure adoption 套件通过；`test_short_index_worker.py` 等 4 个短索引文件的 12 项失败在 0.6.22 wheel 下同样失败（fixture 无生产 embedder → `short_horizon_embedder_required`），属既有红；另 5 项因 fixture 缺 v50–v54 扩展（procedure_uses 表）已修。真实验收：run-02 语料重跑与原生 r8 待做。

最后更新：2026-09-08（M0.6.26 采纳 + 授权时钟接缝）。Host pin Memory 0.6.26（源 9b148b96，wheel abe301b0…，schema 7.4 不变）：prospective 触发条件渲染为中文自然语言进入向量与词面文本（语料 C04 零召回根因），文本格式版本 2 使旧世代 stale 重建；能力运行时 `clock=` 贯通（TaskGrant 铸造与激活同一时钟，语料 C04-15 根因，生产默认真实时钟不变）；语料跑道：评分前 tick prospective 注册、C06 改用 `required_procedure_access` 计分、C08 标量 setup 先开 primary、C05-12 移出支持集。401 runner 重 pin（rev 14 / layers 12）扫描 PASS 227 / FAIL 0 / BLOCKED 174 不变。

最后更新：2026-09-07（M0.6.25 采纳 + S6 Task 2 后端）。Host pin Memory 0.6.25（源 b45db92c，wheel f36bb383…，schema 7.4 不变）：Procedure 发现面对已采用（active/reinforced、unbound）流程可见并改为中文词项匹配（原生 r24/r25 根因，裁决 `plans/2026-09-07-native-main-journey/DECISION-PROCEDURE-USE-CHAIN.md`），`procedure_discover` 描述与 PERSONA 同步；S6 Task 2 后端补齐 `task_scope.list`、`open_exact.binding_summary`、公共通道拒绝 `live_probe`（独审 `REVIEW-TASK-PANEL.md` B-1/B-2/B-3）。401 runner 重 pin（rev 13 / layers 11）扫描 PASS 227 / FAIL 0 / BLOCKED 174 不变。原生 r10（Procedure 使用链）待做。

最后更新：2026-09-07（原生 r9）。M0.6.24 原生：relation 世代缺陷零告警；12 轮填充后零词面重叠提问触发模型 context_route（full_text+vector），long_term_typed 召回 `backup_directory_device=外接硬盘`（查询词项对 payload 0 命中，只能来自向量通道），终答正确。记录 `plans/2026-09-07-native-main-journey/NATIVE-R9-VECTOR-RECALL.md`。

最后更新：2026-09-07（TaskScope 只读审查）。记忆面板新增「任务」标签页：搜索只展示候选且不授予权限，精确打开后才显示 README/STATUS 与来源修订/drift，PLAN/DECISIONS/RESUME/EVIDENCE 按需页入，无写操作与 Manual/Auto 开关；前端 vitest/typecheck/eslint 通过，原生真实点击未验。见 `ARCHITECTURE/UI.md` 顶段。

最后更新：2026-09-07（M0.6.24 采纳）。Host 已 pin Memory 0.6.24（源 3b51e0f6，wheel 0c6548b8…，schema 7.4 不变）：认知向量世代跳过 relation 类 SEMANTIC head、构建失败落 failed 行并抛 `CognitiveVectorGenerationFailed`；Host 短索引告警附 SDK 错误码。installed target `installed-h0710-m0624-s0313`；401 runner 重 pin（fixture rev 12 / layers rev 10）正式扫描 run-07：PASS 227 / FAIL 0 / BLOCKED 174。Host 控制同 0.6.23 采纳时（短索引 4 文件 12 项既有红不变）。原生 r9（向量召回同义查询）待做。

最后更新：2026-09-07（run-02）。M0.6.23 + 任务搜索修复后重跑 11 例：11/11 PASS，C01 required 召回 7/7，路由次数全部 1 次，`cognitive_vector_unavailable` 消失；多提类型率 71% 仍超门槛（模型选型习惯，待提示词处理）。原生 r8：真实模型 v6 关系抽取→图谱「2 条记忆 · 1 条关系」applies_to 边通过；但 0.6.23 `rebuild_cognitive_vector_generation` 遇 relation 类 SEMANTIC head 每 tick 抛 MemoryCorruptionError（记录 `plans/2026-09-07-native-main-journey/NATIVE-R8-RELATION-GRAPH.md`），0.6.24 候选修复中，向量召回原生验证改 r9。

最后更新：2026-09-07（401 矩阵本机首扫）。M0.6.23 pin 正式扫描 run-06：PASS 227 / FAIL 0 / BLOCKED 174。首扫 44 个 eligibility FAIL 根因为验证适配器未按 recipient 同步 `intended_audience`（Memory ≥0.6.14 配对规则），已修并独立分析记录于 `plans/2026-09-07-corpus-c01-local/TYPED-RECALL-401-RUN-03.md`；bridge source 层 `passed_cells` 聚合已补。174 BLOCKED 全为既有类别（执行器未实现 30 / fixture 与公共契约不符 96 / oracle 未闭合 48）。

最后更新：2026-09-07。评分自然退出叶ab36b6a5：WorkflowRunner独立UoW owner原未释放，补public runner/service close与main/carrier统一收尾；bootstrap明确服务拥有共享端口UoW，runner不关借用端口。唯一独立child实际main执行自然SystemExit控制1PASS17.01s，PG69388清空，无pytest全局lane清理代替。原C01-10语义FAIL及deadline保留，下一新case质量另验。[定位与结果](../plans/2026-09-07-corpus-c01-scoring/PROCESS-EXIT.md)。

最后更新：2026-09-07。首真实C01-10固定30b07393/H079/M619：1物理请求、0工具，排序正确但未取得已存A，原gold FAIL（主审+独审）；240已尝试1/通过0。业务COMPLETED后worker线程退场挂起，180s外部deadline退出125并清空PG67059，非内存/磁盘门。修复退出与通用记忆来源指导继续，均未称通过。全阶段防熄屏保持。[真实结果](../plans/2026-09-07-corpus-c01-scoring/REAL-R1.md)。

最后更新：2026-09-07。C01生产评分接线99d17c11/结果d111ce21已独审合入候选，新增实际main初始化/失败及未终态trace3控分批通过；当前真实模型评分仍0，需首次运行及逐条gold终态复核。[结果](../plans/2026-09-07-corpus-c01-scoring/RESULTS.md)。

最后更新：2026-09-07。新构建原生r25固定d86e4805/H079/M619冷恢复与两次实际授权可用；首查询错把taskactive当流程状态，澄清后实际Procedure发现返回0且模型如实答无。正向草稿/完整Procedure仍未验收，240质量不计。PG62018正常退出清空，退出后仅清可再生构建缓存，防熄屏继续。[结果](../plans/2026-09-06-typed-use-primary/NATIVE-R25.md)。

最后更新：2026-09-07。原目录新active Scope续改独审叶636d6c38合候选；新Run原root复用和同Run双绑定拒绝共7唯一控制分批通过，仅H079/M618确定性运行，Manual UI/当前组合/native另验。[结果](../plans/2026-09-06-completed-scope-continuation/RESULTS.md)。

最后更新：2026-09-07。r24已allowed后旧等待提示的UI接线修复：手刷显式exact授权补读、同Run工具/终态推进补读、断线与空pending区分。真实View/Panel/Channel组合新增3控分批通过，原负控保留；尚未新构建/native复验。[结果与边界](../plans/2026-09-07-primary-decision-refresh/RESULTS.md)。

最后更新：2026-09-07。原生r24固定b2da14da/H079/M619，待定流程记录可见；第二轮界面等待授权但停止后补出成功context_route及4次tool_search，Procedure发现/使用和文件核验未完成。PG50771正常退出且清空，非内存/预算阻塞。全测试阶段防熄屏保持。[现场与边界](../plans/2026-09-06-typed-use-primary/NATIVE-R24.md)。

最后更新：2026-09-07。C04 20条setup分批19+1通过，新增同时间戳游标修复5控已独审接受并合入候选；仅原H078/M618叶证据，当前H079/M619组合与240真实质量另验。晚到更早时间戳不在保证内。[结果](../plans/2026-09-06-corpus-public-seed/C04-CURSOR-RESULTS.md)。

最后更新：2026-09-07。共同Memory0.6.19 clean源e27003c已离线只构建一次，H079/M619/S0313安装新组合1PASS0.86s、174/92/116成员和184加载模块精确来自target；版本3控通过。Host vendor/pin/lock/生产identity固定新wheel，初次origin校验失败后通过真实vendor安装纠正，不手改metadata/不重build；PG50135清空。旧M618不改，当前候选可供M619原生验证，完整native/240质量待验。[制品与实际结果](../plans/2026-09-07-current-input-procedure/INSTALLED-079619.md)。

最后更新：2026-09-07。Host80764c13/共同Memorya15c7be源组合1PASS0.82s并独审接受：真实签名当前输入与独立Procedure draft同批前均可见，公开遗忘后只draft拒绝，当前项不受误伤；Host审计请求/快照精确绑定。PG49417清空，原属性oracle红保留。Memory新0.6.19制品/installed/native另验。[结果](../plans/2026-09-07-current-input-procedure/RESULTS.md)。

最后更新：2026-09-07。Procedure恢复/发现固定5ca45216已独审合入隔离候选源码：旧恢复13项限定通过；新发现链有效6项为首批有效4+实际遗忘负控2，旧时钟异常误绿已撤回。原signal lane、context page reader与current-input接线均保留，依赖聚合含v3 draft。共同Memory新制品/当前安装组合和native完整TC04仍待验，旧M618不能启动此候选。[新发现结果](../plans/2026-09-06-procedure-adoption/DISCOVERY-RESULTS.md)／[恢复结果](../plans/2026-09-06-procedure-adoption/RECOVERY-RESULTS.md)。

最后更新：2026-09-06。r19收尾指导产品a189的实际运行链2个唯一控制已独审接受并合入：真实原任务目标/未回读债务保留，完成Scope的两次拒绝与公开tool proposal/下一物理输入精确关联、无文件写入。原测试oracle两红保留、修后只复跑红1；最终PG47277清空。不是模型/native质量通过，旧root新activeScope续改仍独立实现。[控制与边界](../plans/2026-09-06-completed-scope-guidance/RESULTS.md)。

最后更新：2026-09-06。已审非SELF本轮输入消费者c0fbe30a接入隔离候选源码，保留既有提醒signal authority；组合需Memory后继的新current-input公开API，当前旧M618 pin不能作为此源码可启动证明。在共同Memory源码1df01d1审查/新制品及安装组合完成前暂停该候选原生启动，用户主checkout未变。旧9项源验不重跑。[来源与边界](../plans/2026-09-06-nonself-input/RESULTS.md)。

最后更新：2026-09-06。合入prepare叶后的Host9cace208，当前H079/M618/S0313新增C02完整prepare跨进程lostACK组合1PASS4.48s，174/84/116成员精确、188加载SDK来自target；PG46613清空。旧H078套件不重跑，C03新组合/240质量不外推。[组合结果](../plans/2026-09-06-corpus-public-seed/H079-PREPARE.md)。

最后更新：2026-09-06。C02-19/C03-20完整fixture prepare与跨进程恢复叶e0e7d68c（产品182a5aa6）已独审合入候选：public seed后实际drain，finalize前保存原候选、重开经SDK确认；2新控制PASS7.30s、PG34647清空，旧绿未重跑。限定H078/M618源运行证据，当前H079完整prepare组合待验，240质量仍0。[准备与恢复](../plans/2026-09-06-corpus-public-seed/INFERENCE-PREPARE-RECOVERY.md)。

最后更新：2026-09-06。固定ff35fb82/H079/M618正确18120新构建，r22真实新松柏提醒ACK后独立“提醒”正文可见；r23冷启动保留同一历史回执/提醒，后续普通问题只答44无新增提醒，两项限定通过。PG42213/45599正常退出且清空。前置r20 carrier异常原因未定、r21编译端口错误已纠正；原r18FAIL保留，完整旅程/240质量仍未完成。[原生与资源证据](../plans/2026-09-06-typed-use-primary/NATIVE-R20-R23.md)。

2026-09-06：提醒独立正文notice叶26c19b5e已独审合候选，产品1355c5b7，新7backend/2UI分批通过。新ACK投影独立reminder，不改模型原答或旧ACK，合法改期撤旧notice，保留原r18FAIL；真实原生正文/新构建仍待验。[源码与控制](../plans/2026-09-06-prospective-ack-notice/RESULTS.md)。

2026-09-06 原生r19独立长旅程仅前5轮：真实任务/docx创建但漏readback；原任务被模型收尾为complete，后续resume路由成功但编辑被生命周期门拒绝，第4轮FAIL并原生停止；随后43正常。完整两组旅程未完成，PG29074正常退出清空，非内存阻塞。[现场与边界](../plans/2026-09-06-typed-use-primary/NATIVE-R19.md)。

2026-09-06：固定3d83ac81的C03两来源收尾在当前H079/M618安装组合新增1PASS/2.25s，PG28861清空；189加载SDK模块来自target，原H078其余绿不重跑。不计质量语料，C02/自动prepare/跨进程proof另验。[组合证据](../plans/2026-09-06-corpus-public-seed/H079-COMPOSITION.md)。

# ARCHITECTURE 索引

> 2026-09-07 转主干开发：并入 `feat/typed-recall-0613`（401 矩阵 runner）。以下为合并时两路状态段的并集，各自描述当时状态。

## 2026-09-06 Applicability integration

Updated2026-09-06:048b72eb integrated original public applicability axes; six affected tests passed and owned processes exited. Formal three-cell Run remains separate from other401 batches. [Review and evidence](../plans/2026-09-06-typed-recall-applicability-executor/COMBINED.md).

## 2026-09-06 原触发执行器组合验证

最后更新2026-09-06。固定1f9b575d合入已独审trigger叶并保留全部oracle指纹；三个必要交叉集成通过，无进程残留。原正式2PASS/1构造BLOCKED保持独立Run，不外推新401。[主复核及证据](../plans/2026-09-06-typed-recall-trigger-executor/COMBINED.md)。

2026-09-06主复核：Prospective后六格dfec8bbb源/9raw hash一致，合入c202be39；实际共享执行器组合9项通过7.17秒，峰111MiB且无残留。前13/后6分别保持正式Run证据，非一次401/Host提醒验收。[组合边界](../plans/2026-09-06-typed-recall-prospective-lifecycle/COMBINED.md)。


2026-09-06主组合复核：固定ea57e720公开Prospective 13格叶纳入29479573；13个raw hash一致，四oracle指纹均保留。受影响组合18项通过6.31秒，峰112MiB/组已清空；synthetic SDK信号不代表实际Host提醒，余6个lifecycle仍继续。[复核与证据](../plans/2026-09-06-typed-recall-prospective-public/COMBINED.md)。


2026-09-06主复核：Procedure公开适用性固定ad189/3de9已纳入1c690bdb；9份原始证据hash一致，受合并影响的15项组合检查通过、进程已清理。原四格独立PASS，不代表Host观察晋升或全401；[组合范围与证据](../plans/2026-09-06-typed-recall-procedure-public/COMBINED.md)。

## 2026-09-06 Context与source oracle已组合复验

最后更新：2026-09-06。独审后的两个执行器合并固定9bad3a43，两个代码指纹入口均保留；12项必要组合检查通过、进程组32908已退出。原source正式10PASS与Context正式4PASS/2BLOCKED按各自固定源及Run保留，不拼成新401全量；Harness凭据消费/continuation仍需实现，原SDK pin与阈值未改。详见[组合结果](../plans/2026-09-06-context-use-full/COMBINED.md)。


## 2026-09-06 current-use 双 item 测试工具叶子

独立原六格真实public执行：6OBSERVED，4PASS/0FAIL/2BLOCKED，其余395未选。
两格Harness reservation/exact-once消费见证仍缺，原new-continuation未执行；无Memory新缺陷声明，
不表示S3/401完成或主树已合入。冻结H073/M0613与阈值不变，测试槽已释放。
[逐格结果与范围](../plans/2026-09-06-context-use-full/RESULTS.md)。

## 2026-09-06 Rich Episode公开来源独立叶

最后更新2026-09-06。30fcc261实际installed H073/M0613新方法1PASS0.48s；完整原rich S1、公开scope registration、真实mutation/recall/reopen/fresh，SENSITIVE与cross_scope绑定。
含六泄露谓词/foreignID/禁止legacyPASS反例；原literal仍BLOCKED，另四类型setup待实现，不改变401统计。
原始观察未单独导出JSON，只有命令/pytest/资源证据，不称正式矩阵Run。峰72096KiB，进程无残留、槽释放。
[结果与边界](../plans/2026-09-06-typed-recall-rich-source/RESULTS.md)。


## 2026-09-06 Procedure applicability原三格公开executor

最后更新2026-09-06。固定ecaeb50f获Dirac源码限定ACCEPT；installed H073/M0613新增方法1PASS0.68s（原3+4篡改），正式3PASS/0FAIL/0BLOCKED。
Run67db4f2a02d544db83a20da646f8e16e；原app-v2/app-v3/null映射真实public context，原语义reason保留，实际读取后no_recall不能由前置拒绝替代。
其余398未选、整体NOT_RUN/BLOCKED、exit3。synthetic SDK合同非Host工具/提醒；32非法与projection原义务未闭合。
旧绿未重跑；最大135440KiB、进程无残留、槽释放。
[命令与9raw hash](../plans/2026-09-06-typed-recall-applicability-executor/RESULTS.md)。


## 2026-09-06 Prospective trigger executor公开runner叶子

最后更新2026-09-06。固定7e6337b5获Dirac源码限定ACCEPT后，installed H073/M0613新集成方法1PASS0.63s（原3格+3篡改），正式原3格2PASS/0FAIL/1BLOCKED。
Run edb882f0ec224e1fbdbfff4e5bcc714c；missing trigger无法公开构造，未以DTO拒绝冒充eligibility通过；其余398未选，整体NOT_RUN/BLOCKED、exit3。
pending ACK仅synthetic registration合同，不是Host提醒；两projection旧wire/hash及canary/scope义务、32非法组合保持边界。
旧19未重跑、不并历史为新401。最大135408KiB、进程无残留、槽释放。
[命令、边界和9raw hash](../plans/2026-09-06-typed-recall-trigger-executor/RESULTS.md)。



## 2026-09-06 Prospective剩余6 lifecycle公开runner叶子

最后更新2026-09-06。ec68源码审查P1（receipt目标连续性与candidate正控来源）修复为ea030952并限定ACCEPT。
实际installed H073/M0613一个新集成方法PASS1.17s（6真实格+11篡改），原6格正式6PASS/0FAIL/0BLOCKED，
Run b7b8fe83520d430b8c52d93f72f4bb4f，dependency[]；其余395未选/整体NOT_RUN/BLOCKED/exit3。
实际public ACK/matched signal/授权REVISE绑定原source与真实revision；candidate原负例+独立同ID正控，
expired/completed仅synthetic显式状态更新，不称外部时间signal或任务完成。无Host/SDK生产修改。
前13未重跑，不并片为同Run19或新401；projection/非法组合边界保留。
两PGID均退出无残留、槽释放，最大135536KiB。
[命令、P1与raw/hash](../plans/2026-09-06-typed-recall-prospective-lifecycle/RESULTS.md)。


## 2026-09-06 Prospective公开scheduler fixture叶子

最后更新2026-09-06。4aee0cdb源码、dd988b19精确expiry test delta均Dirac限定ACCEPT。
实际installed H073/M0613必要3方法PASS，受影响1方法窄复验PASS；原13格正式13PASS/0FAIL/0BLOCKED，
Run ae075cb1eaed43a3b8f8221160d2c874，无dependency，其余388未选/整体NOT_RUN/BLOCKED/exit3。
真实public outbox ACK+synthetic signal绑定原trigger/source/run/clock/expiry与实际revision，重开exact零读取。
这是SDK合同synthetic scheduler，不声称Host真实提醒/外部event；原19另6lifecycle及projection未覆盖。
不合旧182/source10/Procedure4为完整401；无SDK/Host生产变更。三PGID均退出无残留，槽释放，最大135296KiB。
[命令、边界、raw与hash](../plans/2026-09-06-typed-recall-prospective-public/RESULTS.md)。


## 2026-09-06 Procedure公开适用性runner叶子

最后更新：2026-09-06。固定ad189f52已Dirac限定源码ACCEPT；实际installed H073/M0613
必要公开测试3PASS1.09s，原四格正式4PASS/0FAIL/0BLOCKED（Run3913be071c484d069b48082fc5cec12a）。
公开conversation registration/authority snapshot绑定真实revision，错fingerprint不召回，重开exact重放零candidate读取；
eligible保留原literal/INELIGIBLE，经draft→授权REVISE映射eligible_for_activation，不算观察晋升。
其余397未选，整体NOT_RUN/BLOCKED/exit3，不合旧182或source10为新全量；无SDK/Host生产代码变更。
两批默认OS锁2GiB/180s，最大135584KiB，自有进程均退出无残留、槽释放。
[实际结果、命令、raw索引与hash](../plans/2026-09-06-typed-recall-procedure-public/RESULTS.md)。


## 2026-09-06 source10完整oracle后继（独审待回）

固定65990a68，exact installed H073/M0613f2 source层正式10PASS/0FAIL/0BLOCKED。
完整90表schema/PK/nonfinal根、request/attempt/terminal关系、原distinct admitted source与member/group hash、
确切reopen outer/cause/trace及零recall/零写均独立判定；没有改SDK错误码、fixture、10AC/阈值。
1个集成test含10正控+30篡改检查通过；schema2/extra-key、swap/reuse重hash命中目标reason。
首轮three-member错误cause导致1红，已保留并定向修正为实际FKcause；不改原证据。
本次未选391public，不与626/fbeb旧public计为新401全量，不称program/quality/native完成。
全部默认OS锁2GiB/180s，最大157920KiB，无残留且slot已释放。
[命令、红绿与原始hash](../plans/2026-09-06-typed-recall-source-oracle/RESULTS.md)。


## 2026-09-06 固定626后续正式分批（整体仍BLOCKED）

H073/M0613、runner626ff8d8的11个fresh bounded调用互斥覆盖原391public+10source；
public182PASS/0FAIL/209BLOCKED，source0PASS/0FAIL/10BLOCKED。
本次分批并集182/0/219，非一个full401 Run、非质量/机器gate；不拼旧2格observe或旧178历史。
355public+10source实际OBSERVED，36executor未实现；BLOCKED原因为122fixture/setup、61oracle、36executor。
source使用exact clean M0613f2；真实fault/corruption仅source证据，完整oracle仍缺。
全部默认OS共享锁、2GiB/180s/批，最大147904KiB，所有进程组无残留且槽释放。
[逐批Run、命令与逐格分类索引](../plans/2026-09-06-typed-recall-0613/FORMAL-BATCHES.md)。


## 2026-09-06 H073/M0613 runner successor（独立测试工具叶子）

独立 `feat/typed-recall-0613`，base60f280dc；候选pins显式后继并保留旧lineage，
原401/391+10/14攻击/阈值不变。observe在public/source层及cell统一不授PASS，FAIL保留。
必要工具测试12passed；两原格真实installed public OBSERVED且业务断言通过，正式0PASS/0FAIL/2BLOCKED。
source10与其余399未执行，旧178/0/223历史不覆写、不拼接。H164/M72包文件逐字节核对；无模型/native。
资源入口145baed3默认共享锁，两组无残留且槽已释放。原runner applicability WIP未动，原三格仍BLOCKED。
该工具叶子不表示S3/program或401全量完成，未合主树。
[命令、资源与证据hash](../plans/2026-09-06-typed-recall-0613/RESULTS.md)。

2026-09-06 C03与推断准备收尾已审叶bfd56d99合入候选：C03全部20条setup分批通过，C01–C03共60条准备验证；240真实质量仍0。C03-20两来源实际SDK job的合法无修改收尾、非法分析虽APPLIED但拒绝确认、取消后的原application恢复共3个新控制分批通过。仅H078/M618独立叶证据，C02接线、prepare自动收尾、跨进程proof及当前H079组合仍另验；不是全部评分运行就绪。[C03准备](../plans/2026-09-06-corpus-public-seed/C03-PREPARE.md)／[收尾结果](../plans/2026-09-06-corpus-public-seed/INFERENCE-DRAIN-RESULTS.md)。


2026-09-06 原生r17/r18（Host55eb273d/H079/M618）：旧提醒真实ACK后正文送达、下一轮去重及冷启动去重通过；新银杏提醒到期虽ACK成功，最终回复却未展示提醒正文，**完整提醒交付仍FAIL**。两组正常退出且无残留，不是内存/锁屏阻塞。新增缺陷继续修复，旧r14/r16失败保留；240质量仍0。[实际结果与证据](../plans/2026-09-06-typed-use-primary/NATIVE-R17-R18.md)。

2026-09-06 H079/M618候选：新SDK单次离线制品已固定，main factory/真实零tool恢复至ACK/身份3项安装组合PASS4.37s，174/84/116包成员与202加载模块精确核对；PG21846正常退出并清空。源14绿不重跑，原生r17仍待验、r16失败保留。[安装结果与边界](../plans/2026-09-06-typed-use-primary/COMBINED-079618.md)。


2026-09-06：source/runtime叶006a67dc已独审合入候选。真实C01 CREATE job、graph backoff拒假成功、source/scoring对话隔离三项新增控制分批通过；仅普通v1链，不含C02推断接线、A7/typed/short跨库或240质量。[结果与边界](../plans/2026-09-06-corpus-public-seed/SOURCE-RUNTIME-WIP.md)。


2026-09-06 C02-19原setup关联补强：完整原始S1/receipt与实际group USER精确比较，新增真实同文异Run负控1PASS；已有正向/19绿未重跑，PG19055清空。仅setup来源，runtime隔离/240质量不计完成。[结果](../plans/2026-09-06-corpus-public-seed/C02-BATCH.md)。


2026-09-06 C02全部20条setup已分批通过（18首批、C20及C19失败修复后各1）；C19用真实完成Host/SDK assistant来源保留llm_inference/unverified，C20不补造颜色或通用预算。C01+C02共40条准备验证，240真实质量仍0，运行来源隔离继续。所有测试组已清空。[准备结果与失败历史](../plans/2026-09-06-corpus-public-seed/C02-BATCH.md)。


2026-09-06 原生r16：时间调度修复已在r14原userdata实际恢复并触发1条；普通问题却被SDK pending occurrence/no_recall检查拦截，UI无本轮回答/提醒，端到端仍FAIL。不自动ACK或放宽检查；PG17276正常退出并清空。[原生结果与卡点](../plans/2026-09-06-typed-use-primary/NATIVE-R16.md)。


2026-09-06 原生r15：公开SDK准备的2节点/1条APPLIES_TO在真实Cytoscape画布显示、点击边打开正确有向详情；筛选为1节点0边时隐藏详情，清空后恢复原选择。限定图谱UI通过，不计模型抽取/240质量/完整旅程；PG14481正常退出并清空，峰1,327,584KiB。[原生结果](../plans/2026-09-06-typed-use-primary/NATIVE-R15.md)。


2026-09-06 原生r14：一次性提醒后台实际创建且UI记忆可见，前台却否认；到期后真实普通下一轮仅答43，未展示提醒，Host登记/计时/occurrence/presented均0。判时间提醒原生FAIL，正在补生产调度生命周期；不以两Run COMPLETED或旧组件绿替代。PG11237正常退出并清空。[Run与原生证据](../plans/2026-09-06-typed-use-primary/NATIVE-R14.md)。


2026-09-06：大结果边界增量：8k小参数调用的1MiB精确分页通过（最大物理请求19,219字节）；4k预算拒绝后的真实ClosureFallback收尾/冷重开零重发负控通过，保留FAILED与Scope pending，不报4k分页成功。大型assistant参数原4k/8k超限失败保留，未提高预算或复跑32k/8k绿；进程组均清空。[结果与失败边界](../plans/2026-09-06-primary-context-compaction/MEGABYTE.md)。


2026-09-06：公开SDK语料准备已合入，C01全部20条setup按各批验证，含同ID修订、遗忘前后可见性及独立2节点1条APPLIES_TO图谱fixture；没有真实模型评分，不计240完成。[结果与边界](../plans/2026-09-06-corpus-public-seed/C01-BATCH.md)。

2026-09-06：当前运行新增1MiB边界控制1PASS/6.32s，两个实际文件结果均超过1MiB，8次物理请求最大28,209字节，精确尾页及重开依赖通过。仅32k窗口/fixture producer/MockTransport，不代表4k8k或原生；PG7739清空。[结果](../plans/2026-09-06-primary-context-compaction/MEGABYTE.md)。


2026-09-06：用户明确将“发布成功后提醒”缺失的实际发布来源接入及对应端到端验收延期为F01。本次不继续推进、不计为通过，其余当前交付继续；已有事件协议层证据不替代真实发布。[后续待办](../plans/2026-09-06-typed-use-primary/FOLLOWUPS.md)。


2026-09-06：实际生产fallback已证明撤回来源后可FAILED收尾并继续新输入，原fixture缺fallback不外推main死锁。后继53940598修复非成功路径多余来源正文构造及pending重放状态，两项实际控制通过并独审合入；未伪称任务语义完成。[结果](../plans/2026-09-06-revoked-scope-terminal/RESULTS.md)。

2026-09-06：真实v4提案混入多种正文被编译拒绝，后继v5按memory_type分支schema并保持旧协议恢复。固定85a19260新3控通过；真实gpt5.5三意图分别产出ACTIVE/DRAFT/DRAFT，无编译拒绝。仅模型分类+编译，非Host持久链/原生/240质量；PG4986正常退出并清空。[实际失败、修复和三条结果](../plans/2026-09-06-procedure-adoption/V5-CLASSIFICATION.md)。


2026-09-06：当前运行分页合并A7的构造器和调用均保留双方参数；固定320a419e在H078/M618实际main factory及current page allow两项2PASS/5.34s。PG3663清空；未closed写Scope撤回后终态pending仍单独修复，原生未开始。[组合结果](../plans/2026-09-06-typed-use-primary/COMBINED-078618.md)。


2026-09-06：当前运行大工具结果分页固定c5aea726已独审合入，两个唯一实际栈控制通过；来源为已完成effect及实际父请求，保留A7协调器和原预算。未关闭写Scope后撤回来源的终态pending真实缺口仍在修复，H078/M618组合和原生另验。[当前运行结果](../plans/2026-09-06-primary-context-compaction/CURRENT-RESULTS.md)。

2026-09-06：历史分页合并A7后，固定c9e1aebf在H078/M618运行1条必要交互检查，1PASS/4.71s，实际首/续/尾页、后续物理请求与重开依赖通过；PG3328正常退出并清空。当前运行分页及原生长旅程仍待验。[组合增量](../plans/2026-09-06-typed-use-primary/COMBINED-078618.md)。


2026-09-06：固定1491309f的H078/M618组合5PASS/4.82s，覆盖实际main factory、A7直接路由ACK终态、Procedure旧v3响应跨配置恢复及Memory身份/锁。173/84/116包成员与vendor一致、201模块全部来自新小target；PG2168正常退出并清空。原生/质量及随后历史分页代码不在此批范围。[当前组合结果](../plans/2026-09-06-typed-use-primary/COMBINED-078618.md)。


2026-09-06：历史工具大内容分页固定536daece已独审接受并合候选。4个唯一场景验证真实S1/公开SDK来源、首/续/尾页、错误hash拒绝、后置遗忘阻止外发及冷重开依赖；证据为H077/M616和确定性HTTP，当前运行分页、当前组合及原生另验。[结果](../plans/2026-09-06-primary-context-compaction/RESULTS.md)。

2026-09-06：Procedure提案按新v4区分明确采用/步骤叙述/不确定，Host核真实USER来源与有序引文；明确采用ACTIVE，其余合法分类DRAFT且观察成功数0，ACTIVE不授予执行权限。v3完整协议保留，普通失败跨配置重试P1由M618固定完整输入/cohort恢复；原Host反例零新Provider并应用旧v3语义已实际通过。源码/独立安装验收不代表Scope观察、适用性或真实分类质量；H078/M618组合另验。[来源与范围](../plans/2026-09-06-procedure-adoption/SOURCE.md)。

2026-09-06：A7展示/ACK与来源继承已独审合入候选（固定cd594b8f）。真实五路由ACK终态、三轮未ACK保留pending/唯一overdue、第四轮ACK、终态故障恢复、异主体拒绝及跨轮派生历史遗忘分别通过；slow-source等待期间Host换代真实红例已修复并验证零外发。原no_recall规则不放宽，snapshot注入不当作用户已见。生产默认登记协调器/ACK并由组件升级52；H078组合和原生A7另验，事件触发来源继续。[原红、结果和范围](../plans/2026-09-06-prospective-presentation-ack/RESULTS.md)。

2026-09-06原生r13（Hostb3680732/H078/M617）：新普通对话真实回答45/idle，默认后台审计45/45公开DTO enumerated，SDK明确verified_current_intervals与coverage_gaps[]。只关闭本场景驱动核验，旧r12 unverified不追认，完整工具/Service/Memory覆盖另验。正常退出PG99878、组清空。[Run、截图及审计](../plans/2026-09-06-typed-use-primary/NATIVE-R13.md)。

2026-09-06：H078/M617/S0313接入候选。SDK正式按持久start_mode选择实际driver，保留Host控制校验，避免普通主对话因不透明wrapper失去审计核验；源4项、安装3项、Host新组合4项分别通过。旧r12实际98/98条审计已读取但coverage仍unverified，不追认旧区间；新native/fullcoverage另验。所属进程清空。[组合及真实缺口](../plans/2026-09-06-typed-use-primary/COMBINED-078617.md)。

2026-09-06：提醒状态库50/51/52已接入应用启动及通用初始化的逐版完整校验；新增负控发现并修复bootstrap缺失时绕过human校验的问题。9个唯一新增场景分批通过（非空重开/损坏拒绝/未知版本/fresh49），资源组均清空；完整A7与原生schema52重启仍待验，未默认安装半成品。[结果与边界](../plans/2026-09-06-typed-use-primary/STARTUP-52.md)。

2026-09-06 原生r12（Host33809aae/H077/M617）：同实例启动load+prime完成后，新进程首次short查询真实成功，无手动重试；新工具三条recall refs和模型青竹九月/无糖茉莉茶回答均可见。本场景PASS，工具总耗时1516.972ms不等SDK检索或p95；原预算未增，广泛性能/质量另验。PG96027正常退出/组清空，磁盘5219MiB；原r10/r11失败保留。[首查结果、Run与边界](../plans/2026-09-06-typed-use-primary/NATIVE-R12.md)。

最后更新：2026-09-06。r11 load-only 后首query仍超时；后继68f525e2在原实例／encode队列执行一次固定无用户数据priming，startup完成含加载及priming。三项新控制3PASS／0.27s，PG95734清空、锁释放；原1s预算不变，已独审合入primary候选，待真实新进程首query，不能以暖态重试关闭。[结果与边界](../plans/2026-09-06-short-terminal-source/PRIMING.md)。

2026-09-06 原生r11（Host464b86ee/H077/M617）：既有startup hook实际完成WeMM预加载，但新进程唯一首query的encode1.44s仍超1s预算；UI明确查询失败，未重试，不以r10暖成功替代首查。PG93935正常退出且组清空；仅清可再生Rust链接对象恢复磁盘4.15GiB，native二进制哈希/模型/证据/用户库不变。继续同实例编码预热。[本次失败与资源证据](../plans/2026-09-06-typed-use-primary/NATIVE-R11.md)。

最后更新：2026-09-06。WeMM公开 warmup 接通原 startup hook，共享原实例／加载任务；成功日志不再调用不存在的 is_mock。固定源码270320d3，两项新控制2PASS／0.25s，PG93645清空、共享锁释放。保持1s预算，已独审合入，真实冷启动初次query待主，不以r10暖态PASS关闭冷FAIL。[边界与证据](../plans/2026-09-06-short-terminal-source/WARMUP.md)。

2026-09-06 原生r10（Host0bedaa87/H077/M617）：FTS+VECTOR修复后的真实暖态短期查询成功，UI工具有三条recall refs，模型正确回答青竹九月/无糖茉莉茶。冷态首查仍timeout，单独保留失败并继续预热定位；不称完整short/性能/program通过。两轮均空闲，正常退出PG89400 exit0/remaining[]。已合closure九场景修复的原生Scope旅程另验。[实际结果与证据](../plans/2026-09-06-typed-use-primary/NATIVE-R10.md)。

最后更新：2026-09-06。短期显式 typed 请求补齐 FTS＋VECTOR，long-only 仍 FTS；原 1s／2048 预算与来源门不变。固定源码 f8b2d41c，实际 H077/M617 公共链路新增反例 1PASS／4.74s，证明大 FTS 组超预算时小 vector-only 偏好可进入 Host fragments。PG89042 清空、锁释放；Dirac限定ACCEPT、已合隔离primary候选；待native 短期叶。[结果](../plans/2026-09-06-short-terminal-source/VECTOR-MODE.md)。

2026-09-06 原生r9（Host fa7580b0/H077/M617）：生产Provider清理错误本次未再观察到；short祖先补齐和后台generation修复已经独审合入，实际WeMM生成active索引。真实查询首次超时，模型同Run重试后SDK审计used/FTS1/vector3，但UI最终仍答无片段，短期端到端未通过，返回链路待定位。现场保存后正常退出，资源exit0/remaining[]/cleanup_error=null。此前r8各场景证据与失败历史保留。[最新原生结果](../plans/2026-09-06-typed-use-primary/NATIVE-077617.md)。

最后更新：2026-09-06。generation 生产源码161702be未改，追加冷加载跨两次有界超时恢复控1PASS：并发step串行、共享load仅一次、失败不确认cache/推进维护时间、完成加载后立即恢复。PG83348无残留、锁释放；不代表实际WeMM/native验收。[补充证据](../plans/2026-09-06-short-terminal-source/GENERATION.md)。

最后更新：2026-09-06。02bf 后继后台 worker 已接公共 generation：同锁/既有 timeout 内 projection→generation，成功才确认 cache，失败/超时/取消可重试，SDK 按 lineage/manifest 幂等复用。实际 installed H077/M617 + 小测试 embedder 四控分批绿（含公共 query、维护/重开/lost-ACK 不重复 embedding）；PG82943 无残留、锁释放。尚待独审/主组合/实际 WeMM/native。[边界与结果](../plans/2026-09-06-short-terminal-source/GENERATION.md)。

最后更新：2026-09-06。隔离 Host 短期 terminal 来源补齐叶：r8 最终一致性副本在实际 M617 公共 rebuild 复现 history_source_lineage_missing；Host 同事务核验 terminal 后经公共 source-only admission 补齐祖先，13 组投影 0→3 chunks，重开保持，旧 registration/suppression/revision/jobs 不变。新增三控分批通过，PG82219 清空、锁释放；未改 SDK/原库，generation/真实 shortquery/native 尚未验证；Dirac限定ACCEPT并已合候选。[原因与证据](../plans/2026-09-06-short-terminal-source/RESULTS.md)。

2026-09-06 原生r8：H077/M617/Host2c8c57c6在原userdata真实完成新偏好写入、长期召回命中、UI遗忘后同条件零命中，Cytoscape两节点/筛选一节点可见。旧任务已FAILED但后置Provider清理仍报KeyError；第11完整组后短期投影MemoryCorruptionError，窗口外短召回未通过。正常CmdQ后runner回收残留，资源125/最终组清空。[原生范围、Run与失败证据](../plans/2026-09-06-typed-use-primary/NATIVE-077617.md)。


2026-09-06：候选固定 H077/M617/S0313，授权过期与冷启动修复8cec2353已合；主vendor小target离线安装和3项受影响身份/锁校验通过。旧功能测试按原组合复用，新组合native尚未验收，用户主树不变。[接入与边界](../plans/2026-09-06-typed-use-primary/COMBINED-077617.md)。


最后更新：2026-09-06。Host默认Memory builder已接7.3公开升级链；实际installed M616旧库→M617升级/重开保留属主与升级回执，新控1项及空库/未知库2邻居分批通过。原生userdata未升级，完整consumer/native仍未通过。[升级边界与证据](../plans/2026-09-06-prospective-source-audit/HOST-617-UPGRADE.md)。

最后更新：2026-09-06。Host明确接入M617 V2/settle观察，H076/M617实际installed组合4新+4受影响检查共8PASS/2.91s，无源码overlay/模型/native；终局真正消费、跨库恢复及完整scheduler仍单独验收。PG71205清空。[边界与证据](../plans/2026-09-06-prospective-source-audit/SUCCESSOR-617.md)。

最后更新：2026-09-06。原生r7包含已审租约修复，冷重建仍在Host读取实际SDK终态时因事件歧义拒绝，STOP_REQUESTED未闭合；没有放宽/篡改终态。遗忘后重启列表仍为空。原生现场采集后正常退出PG69808清空。完整native仍FAIL/未完成。[r7证据](../plans/2026-09-06-typed-use-primary/NATIVE-075616.md)。

最后更新：2026-09-06 14:38。原生r6真实Provider已完成后台分析并生成长期认知记忆；独立UI遗忘后列表及相关当前历史不再展示。semantic召回因授权等待后foreground_lease_expired失败，UI停止未收敛；Cytoscape画布有记忆仍空白。上述缺陷修复中，窗口外short/遗忘后召回未验，完整native/program未通过。完成现场采集后正常退出，PG60384清空。[r6证据与范围](../plans/2026-09-06-typed-use-primary/NATIVE-075616.md)。

最后更新：2026-09-06。H075/M616原生r5已实际完成中文Provider响应、WeMM编码、对话写入和审计UI；结束本轮后清空PG54846。后台analysis误用foreground guard已定位，正在修复；短期当前4组处于SDK最近10组排除窗口，尚无窗口外召回证据。完整native/program未闭合。[本轮证据](../plans/2026-09-06-typed-use-primary/NATIVE-075616.md)。

最后更新：2026-09-06。原生启动暴露的服务登记槽与中断空库初始化已修复；新增两项实际 runtime 检查通过，原生主对话恢复可输入。真实 Provider 已返回，但中文输入用例和随后模型加载异常仍未闭合，完整 native 未通过。[本次结论与证据](../plans/2026-09-06-typed-use-primary/NATIVE-075616.md)。
最后更新：2026-09-06。独立Host提醒来源观察接收叶`0e983edc`通过新增14项/5.17s及Dirac限定审查；复用既有sidecar，默认登记实际Manager读取接入，原SDK观察不提升为授权/持久receipt。已合入隔离primary候选；v2/scheduler/全操作coverage/native不在此验收内。[结果与接线边界](../plans/2026-09-06-prospective-source-audit/RESULTS.md)。
最后更新：2026-09-06。隔离 Host schema52 新增 typed cursor/独立终局表，保持50/51旧DDL及恢复注册身份、旧游标值/hash，封闭旧writer；正常注册接新版游标，5项新增迁移/故障/拒绝检查通过。not_required 公共回执消费及完整scheduler尚未接完，默认组合未切换。[范围与证据](../plans/2026-09-05-human-memory-s5c-preparation/SCHEMA-52.md)。

最后更新：2026-09-06。[隔离schema51时间事件日志](../plans/2026-09-05-human-memory-s5c-preparation/SCHEMA-51.md)完成新4项及并发发布1项控制；只数据库扩展，完整scheduler和默认接线仍未完成。旧50SQL/默认49不在本叶变更。

2026-09-06：当前组合的M616锁文件hash已修正并定向验证，Host恢复/提醒来源限定独审已归档在组合记录。

最后更新：2026-09-06。[当前 H075/M616 组合](../plans/2026-09-06-typed-use-primary/COMBINED-075616.md)已完成必要功能与安装身份检查；后文叶子状态保留当时证据，不能代替原 program 剩余项。

## 2026-09-06 Host typed-use 生产接线独立叶

最后更新：2026-09-06。独立typed-use叶现闭合H075 short及no-recall必要恢复范围。
原short伪revision保持拒绝、actualNone经H075公开page/grant→真实physicalguard正常外发；
独立Host来源遗忘仍拒绝。新4场景分别证明sink前/后进程丢失恢复、response_reserved恢复
同receipt不重发、真实pending拒绝同时保留Provider成功事实。发现并修复本叶启动时序P1：
使用SDK原terminal verifier返回的实际publicview，避免查询尚未发布的Hoststack；原校验不减。
两新批分别2PASS后1FAIL、修复后只重试余下2PASS，进程全部清空；未重跑旧long/clock/short。
冻结H075制品独审ACCEPT、旧074614环境/用户库不变；主H075/M616组合和native另验，
不标401/program完成。[固定结果与全部失败保留](../plans/2026-09-06-typed-use-primary/RESULTS.md)。

2026-09-06：Timer新增late-invalidation/lease接管/observation篡改三控分批通过（先1PASS2FAIL，修复仅2红后2PASS）；产品修复2ce1dff1规范SQLite REAL lease签名字节，旧绿未重跑。schema52未合，presentation/ack/native未验。[风险控制结果](../plans/2026-09-06-prospective-scheduler-time/RESULTS.md)。

## 2026-09-06 Timer必要installed H076/M616组合

Host d3f9720a真实pending/rescheduled两路径2PASS1.71s：到期Memory提交丢ACK、过期重开same-ref重放、inbox唯一。原失败保留，旧控制不重跑；尚缺独立竞争控制与presentation/ack/native，未称完整scheduler。进程退出槽释放。[局部结果](../plans/2026-09-06-prospective-scheduler-time/RESULTS.md)。

最后更新：2026-09-06。[隔离schema51时间事件日志](../plans/2026-09-05-human-memory-s5c-preparation/SCHEMA-51.md)完成新4项及并发发布1项控制；只数据库扩展，完整scheduler和默认接线仍未完成。旧50SQL/默认49不在本叶变更。

最后更新：2026-09-06。[S5c 提醒注册公开来源及7项局部验证](../plans/2026-09-05-human-memory-s5c-preparation/PUBLIC-SOURCE.md)；完整 scheduler 仍在进行，未切换默认运行路径。

## 2026-09-06 M0615 installed tool groups

Updated2026-09-06:935d3e12 H073/M0615/S0313 own installs verified169/76/121 members. Original empty-assistant failure is fixed in this successor;22 tests+2 subtests passed13.05s, owned processes cleared. Text tool source chain only; nontext/native/H074/240 remain. [Chinese result and evidence](../plans/2026-09-06-tool-causality/INSTALLED-0615.md).
## 2026-09-06 S5c schema50 successor

Updated 2026-09-06: Primary49 to isolated50; 43 tests passed, real old S5c47/48 rejected without DB byte changes. Default remains49; scheduler/presentation/ACK not wired. Fixedf8e59f31 passed independent scoped review and is merged in the primary candidate. Default remains49; not active scheduler. [Mapping and evidence](../plans/2026-09-05-human-memory-s5c-preparation/SCHEMA-50.md).

## 2026-09-06 工具组与可信披露组合接入

最后更新：2026-09-06。工具v2非空组源码及证据、可信披露f3675064的两个P1修复均已由主审和独立代理审查。当前合入同一隔离Host候选1268e884，必要交叉检查21项通过14.55秒，测试组已清空（[证据](../plans/2026-09-06-host-trusted-disclosure/COMBINED.md)）；下列开发记录中的未合并/待独审状态为此前阶段。空assistant仍有M0614真实失败，SDK后继修复中；非SELF、真实Provider/native及240质量尚未完成。

## 2026-09-06 工具多消息v2生产接线，仍有SDK空文本阻塞

最后更新2026-09-06。新实际工具组的terminal/逐消息来源原子提交，Host实际结算attestation、完整6item公开注册/short非空/重开/遗忘通过；写中断无半组、两个来源篡改与五个旧v1邻居通过。新空assistant真实完整组被M0614 short non_blank校验拒绝，保持原红并继续修SDK，不丢消息/填placeholder。故本片未完成；源码独审待续，无真实Provider/native/用户主树切换。[各批范围与未完成项](../plans/2026-09-06-tool-causality/PRODUCER.md)。


## 2026-09-06 工具多消息公开因果读取局部验证

最后更新：2026-09-06。新增内部reader通过实际Host effect index和SDK公开投影/bounded审计/结果读取绑定每个工具与父Provider消息，正确区分跨轮重复raw call ID；真实dynamic Host+SDK一个集成测试（含5个篡改及1个截断控制）后继通过，重复读取不新增audit查看缓存，峰165MiB，进程清空。仅来源投影，未接入terminal producer/短期整组索引，不签工具terminal receipt；原始失败保留、独审待续。[实现边界与证据](../plans/2026-09-06-tool-causality/RESULTS.md)。

## 2026-09-06 Dirac披露并发两P1局部修复

最后更新：2026-09-06。自有feat/host-trusted-disclosure/base955a19cd，整片未合主、待主/Dirac复核。真实双控制连接先复现FIFO陈旧A阻塞B与慢checker换代后仍物理send两红；新增schema49 Host入场拒绝记录（无Run/Memory伪receipt）让A拒绝后B继续，出站checker后新连接复核原token。历史source不改。最终新增及必要邻居94项通过/41.51秒/峰222944KiB；PGID40284及全部本轮组已清空，测试槽释放。非SELF/完整外发原子撤权与240质量仍未完成，未跑真实模型/native。
[两P1修复、接口、schema、原红和指纹](../plans/2026-09-06-host-trusted-disclosure/Dirac两P1修复.md)。

## 2026-09-06 披露绑定历史来源回归修复

最后更新：2026-09-06。真实默认SELF foreground/outbox/short/history链复现精确形状回归后修复，最新29项通过；历史配置事实与当前head判定分离，待主/Dirac终审，非SELF和完整外发仍待后继。
[修复、消费者扫描与证据](../plans/2026-09-06-host-trusted-disclosure/验收与跨层修复.md)。

## 2026-09-06 Host可信披露配置源码候选

最后更新：2026-09-06。authenticated control配置经queue幂等绑定进入turn/run解析器；schema48及8个契约测试函数已写，未测试、未合并，完整非SELF/输入许可/外发仍待后继。
[源码复核接口和边界](../plans/2026-09-06-host-trusted-disclosure/固定源码交接.md)。

## 2026-09-06 Memory 0.6.14隔离Host组合

最后更新：2026-09-06。固定ec046e84接入受众绑定候选，独立6.3MiB环境H073/M0614/S0313全部SDK成员与vendor一致；必要组合32项及2个subtests通过，峰399MiB/22.247秒，进程清空。旧M0613环境保留。SELF与不同最终受众默认拒绝；协作者语义配对不构成外部原始历史授权。该结果不代表实际Provider/native或401/240完成。用户主树未切换，原计划继续。
[安装身份、失败保留、命令和证据](../plans/2026-09-06-disclosure-audience/COMBINED.md)。


2026-09-06主复核：clock固定e32a2542纳入cbf99364，7个源码/证据hash一致；受影响实际memory job/semantic correction/history组合11项通过、进程已清理。原6项clock独立保留；[组合复核及限制](../plans/2026-09-06-corpus-clock/主代理复核.md)。以下待整合表述保留为当时历史。

## 2026-09-06 Host业务clock局部验收

最后更新：2026-09-06。可信clock透传到runtime与公开SDK，进程内lease使用monotonic；6项真实SDK空库/clock契约通过，待主复核整合。未完成240质量或受众用途接线。
[边界事实](MEMORY_SDK_BOUNDARY.md) · [命令、证据与限制](../plans/2026-09-06-corpus-clock/验收结果.md)。

## 2026-09-06 记忆提议失败审计与连接取消清理

最后更新：2026-09-06。后继c39b2569默认在Host调用账本记录成功/拒绝的安全记忆类型与short选择；召回执行器取消在提交路由前记录取消原因并传播CancelledError。非法输入及异常原文不进入该审计投影。取消写入不等待SQLite写锁，连接建立/PRAGMA初始化失败或取消由内部等待并关闭自有连接；2秒仅为取消请求deadline，不冒称物理硬限额。实际后继32项必要检查通过（含12项故障/取消检查），峰约97MiB、进程已清理；源码独审限定ACCEPT。原36项批次独立保留。仍不覆盖强杀、写盘失败的完整持久性、route决策/审计两事务原子性、Service全部操作或真实模型/native。
[实现、真实故障边界与本机证据](../plans/2026-09-06-model-recall-selection/FAILURE-AUDIT.md)。

## 2026-09-06 新组合原生构建通过，启动因内存前置未执行

最后更新：2026-09-06。18ec7194新前端嵌入独立app构建通过，18.737秒/峰1.06GiB/进程清理。native carrier改同一资源组，两个实际进程/流检查通过；首次启动在Popen前因5579MiB<7GiB预算被拒，应用和模型未启动，无UI/重启证据，不冒称原生验收完成。
[准确构建/身份/启动限制及本机证据](../plans/2026-09-06-model-short-recall/NATIVE.md)。

## 2026-09-06 模型短期召回及测试资源管理已组合

最后更新：2026-09-06。独审cb743007、145baed3依次fast-forward接入组合：H073/M0613/S0313再次核对169/75/121 installed成员与本树vendor一致；实际候选/短期worker22PASS/9.90秒，峰值265MiB、进程已退出。新模型长短期请求单typed预算、完整来源和最终出站再检查；资源入口默认串行锁/RSS/时间限制及父退出后组清理，三个实际故障点均原红→修复绿并独审通过。
叶子53项及补充混合/认知测试各自证据保留，未冒称整体重跑。资源采样非硬限额/全系统监控；用户原main未切换，仍无真实模型/native新组合或401/240全量，原程序继续执行。
[组合身份、命令和待办](../plans/2026-09-06-model-short-recall/COMBINED.md)。

## 2026-09-06 磁盘空间资源管理

最后更新2026-09-06。磁盘439MiB后清理下载缓存实测释放3415MiB；测试入口新增默认1GiB准入和256MiB运行停止。三个实际子进程反例原红→修复后含邻居13项绿，进程清空，d739dcf7已获独立只读ACCEPT并合入默认共享入口。采样不保证硬配额或满盘receipt，原始证据保留。[范围和证据](../plans/2026-09-06-test-resource-cleanup/DISK.md)。

## 2026-09-06 测试资源入口

最后更新：2026-09-06。`scripts/run_resource_bounded.py`默认跨工作树串行锁、2GiB/180秒采样上限；父命令结束后仍清理其进程组，支持信号清理，资源异常不计PASS。实际6项进程测试及追加1项信号检查通过；随后ps probe异常留下TERM拒绝进程的真实反例先红，再修复KILL/reap，必要3项绿。独审再现父退出快照及spawn信号两个P1：旧源两红→后继两项及必要邻居5绿，先poll后快照、信号仅标记避免丢归属；固定复核待续。全部进程退出。仅自身进程组，不触及用户应用；采样上限非OS硬限制，主动脱离进程组与SIGKILL不保证回收。
[资源管理边界、命令和本机证据](../plans/2026-09-06-test-resource-cleanup/RESULTS.md)。


## 2026-09-06 模型短期统一召回已通过安装候选测试

最后更新：2026-09-06。隔离feat/model-short-recall/base a0764047，H073/M0613/S0313逐文件匹配本树wheel。显式模型长期/短期选择共用一次typed计划和预算；真正选中的short绑定公开四元组及当前完整Host因果来源，缺证据或晚遗忘阻止物理出站。成功选择进入既有调用记录，默认工具启用。
首批53PASS/31.31秒/峰值261MiB；另两项认知出站邻居通过，新增非空长短期混合从fixture两次红修复至1PASS。原始失败及范围见下链；非真实模型/native或全量重跑，全部进程退出。独审待固定提交；完整失败attempt观测、工具多消息、401/240和原程序仍未完成，未切换用户main。
[契约、批次结果与本机证据索引](../plans/2026-09-06-model-short-recall/RESULTS.md)。


## 2026-09-06 WeMM按需加载与内存引用修复已接入组合

最后更新：2026-09-06。独审b70ccda5以fast-forward接入；构造/元数据/状态不加载权重，首次真实embedding共享加载；取消下异步排队和物理线程互斥，失败完成任务丢弃实例引用，防异常保留模型。WeMM2048/L2/本地模型及SDK pin不变。设置页四状态真实WebKit组件检查和刷新通过，浏览器峰值433MiB、进程已退出；相关叶子线程/公开空库/IPC/React/类型检查见证据。
旧库补向量仍可能启动加载；未实测真实权重/GPU内存释放、自动卸载或新组合native，不作整体program完成声明。用户主checkout未切换。
[组合验证及后续内存管理](../plans/2026-09-06-wemm-lazy/COMBINED.md)。

## 2026-09-06 WeMM lazy Host isolated leaf

2026-09-06 follow-up：加载完成回调仅清理同一done task引用，避免失败traceback
长期持有维度拒绝模型；不改waiter异常、不清traceback、不自动重试。fake weakref
原红→绿，含必要邻居5PASS0.20s；pending/新task不会被旧回调清掉。ready措辞收紧
为已加载，非完整搜索质量保证。物理线程/权重分配器释放仍不作推断。

最后更新：2026-09-06。构造/metadata/状态不import或加载WeMM；首次真实embed共享
加载，实际worker持异步encode队列锁+线程互斥。取消不终止物理线程、不自动卸载，
排队取消不占executor线程。dim2048/L2/原lineage保留，加载及输出维度验证。
WeMM状态cold/loading/ready/failed及真实模型名称接现P4卡片；未改main启动或SDK。
installed Memory0612空库public build_production确认0模型构造；旧库ensure回填仍可能加载。
独立树simple_harness-wemm-lazy/base134bc4b8，backend最终唯一13例、React2例通过，
应用tsc0；原构造红保留。fake模型/真线程，无权重、native或build，独审待固定源核查。
[契约、实际命令、结果及边界](../plans/2026-09-06-wemm-lazy/RESULTS.md)。


## 2026-09-06 短期索引及 Service0313 已组合验证

最后更新：2026-09-06。唯一MemoryAnalysisLane默认增加完整两消息组short登记/公开projection，保留低序号迟到重扫、ACK后确认、关闭清理和实际分析；工具多消息仍拒绝。主组合安装H073/M0612/S0313，受影响六模块62PASS/25.45秒、峰值290MiB，全部子进程已退出。三个wheel及installed成员逐字节一致。
Service工具审计新增发送attempt/UNKNOWN/真实ACK/后继响应，仍非持久sink或完整Run绑定；全操作落盘、增量projection、多消息producer、模型short协议及原program未闭合。未切换用户main/runtime，无新模型/native。
[命令、身份、结果与边界](../plans/2026-09-06-short-index-worker/COMBINED.md)。

## 2026-09-06 无边图谱标签布局已修复

最后更新：2026-09-06。Cytoscape无边节点用网格，布局包含标签尺寸并允许中文换行，保留有边有向布局及全部身份/遗忘/viewport行为。真实WebKit两个尺寸各7节点：标签重叠17/13→0/0，最终有效渲染字号估计9.53/11.05px，真实选择/缩放通过。
前端18PASS/1个旧API-fixture未配置SKIP，TypeScript通过；所有浏览器/测试进程结束。合成fixture不代表真实API/native或密集边标签完成，原生复验仍待续。
[原红、实际测量、边界与证据](../plans/2026-09-06-graph-label-layout/RESULTS.md)。

## 2026-09-06 短期选中来源已合成

最后更新：2026-09-06。独审2d98e083合入7fafe03a，同时保留审计authority；实际factory每hit完整来源、裁减/遗忘不互相污染、显式长期零short与HUMAN审计WS组合42项通过（21.12秒、峰值238MiB）。
仅已有内部短请求来源路径闭合；自动生产索引worker、多消息完整producer、新模型short协议及原program仍未完成。无新模型/native运行。
[组合结果与边界](../plans/2026-09-06-selected-short-runtime/COMBINED.md)。

## 2026-09-06 审计查看入口组合验证

最后更新：2026-09-06。独审1097b272合入c53caff2：记忆面板显式打开用途绑定的HUMAN元数据审计，分页/持久ACK重放、关闭与身份失效拒绝；保留原图谱viewport及遗忘ACK修复。组合独审限定ACCEPT。
后端54项通过，新增真实/ws/control审计往返2项通过，前端44通过/1个可选API-fixture未配置跳过，TypeScript通过。单进程有界执行；没有真实Provider、native或全操作覆盖。初始snapshot成本及原生验收仍待续。
[组合证据、命令与范围](../plans/2026-09-05-agent-operation-audit/human-access-leaf/COMBINED.md)。

## 2026-09-06 模型召回类型选择局部完成

最后更新：2026-09-06。memory_standalone工具显式类型经Host校验传入已安装Memory0612公共计划，保留Host身份/权限/预算；显式长期选择不偷偷附带短期查询。成功类型枚举写既有Host审计记录，原proposal仅hash，非公共SDK完整参数回读。
独立叶子最终77项通过（50.57秒、峰值191MiB），包括实际选中来源/最终出站/任务披露链；没有真实Provider或native。固定49249dbd已独审限定ACCEPT并fast-forward主组合，完整类型质量、短期与调度、审计UI及原program仍未完成。
[契约、命令、历史红与证据边界](../plans/2026-09-06-model-recall-selection/RESULTS.md)。

## 2026-09-06 SDK073审计组合验证通过

最后更新：2026-09-06。组合源码78647bb0集成独审通过的终态身份叶子；主组合专用venv安装H073/M0612/S0312，348个SDK文件与本树vendor逐字节一致。
审计目录及candidate/composition组合100PASS/31.62s，单进程峰值258MiB，无本地模型、真实Provider或native。v1历史保留；全操作覆盖及受控审计UI仍待完成。
[实际结果及边界](../plans/2026-09-06-terminal-audit-identity/COMBINED.md)。

## 2026-09-06 Installed H073 exact terminal identity leaf

Last updated: 2026-09-06. Isolated Host candidate consumes exact H073 (wheel1a9ed5c9…)
through public RunTerminalAuditEvidenceV1.matches and existing Host raw-SDK normalization.
Every persisted page binds actual Run/event/full payload/state; legacy scoped evidence
uses its original envelope and terminal gate. RULE terminal-run-v2 preserves all v1 jobs.
Selective installed group21PASS2FAIL then necessary repairs2PASS; failures were a guarded
fixture mutation and obsolete global error-count expectation, retained verbatim. Non-null
committed-turn public head/receipt + same-cursor reopen, namespace negatives and late-source
rejection passed. Peak owned RSS147MiB; no model/native/full suite. Independent fixed-source
review pending; no main production switch or whole-operation completion claim.
See [contract and measured results](../plans/2026-09-06-terminal-audit-identity/RESULTS.md).

## 2026-09-05 Host terminal audit candidate

最后更新：2026-09-05。独立树 eaccab33 + 3e911c14 接入默认 terminal audit consumer；持久读取 attempt、
固定 snapshot/pages 和幂等 findings，不产生 Provider calls/usage/cost 总计。
仅 source-overlay/focused composition 验证，尚非 main installed successor 或完整 operation coverage。
[当前边界与交接](../plans/2026-09-05-agent-operation-audit/host-terminal-leaf/HANDOFF.md)。

## 2026-09-05 Source/auth/action combination verified

Fixed e31c6efd source index closes the independent unscoped-search/late-forget P1;
combined with exact SDK decisions, source-aware history, schema47 and action evidence:
**51 passed** on installed067. Only test-fixture signature required merge resolution.
The separate f9cbb7c8 native candidate also passed expanded visible authorization
by real mouse click; this combined tree has not run native. Cognitive UI/SDK suppress
and selected-source indexing remain incomplete; no main cutover or full program PASS.
See [combined evidence and boundaries](../plans/2026-09-05-primary-effect-sources/COMBINED.md).


## 2026-09-05 Primary source effect index v47 — reviewed local candidate

8e896472 independent P1 confirmed: unscoped search could escape source checks when
TaskScope reservations were absent. A Host append-only exact SDK effect identity
index now records real handler entry under the captured foreground lease; Provider
preflight reads actual SDK results and preserves search→create dependency prefixes.
No scope grant/watermark, SDK change or old evidence restamp. Default schema is47;
coordinator owns deferred S5c's explicit48 remap (historical47 AC remains historical).
Fixed real late-forget counterexample is green; adjacent search/scope17, page-in1,
startup/create32 and migration11 passed. Independent fixed-counterexample review accepted e31c6efd; no
main/native or full privacy completion claim. Ordinary page-in lacking source proof
rejects; generic page-in source projection and short source-only admission remain open.
Details and raw-log hashes: [source migration contract](../plans/2026-09-05-primary-effect-sources/SOURCE-MIGRATION-CONTRACT.md).

本目录是 simple_harness **当前生产架构与项目状态的唯一事实源**。实现计划记录“如何做”，本目录记录“现在实际怎么运行、完成到哪里、有哪些边界与风险”。

2026-09-05 installed067运行层v2保真/严格拒绝与new-message producer组合60绿；旧archive不改，
真实short登记/选中来源由Hegel另线验证，不称native/program完成。见[运行契约](../plans/2026-09-05-s6-primary-preparation/HISTORY-RUNTIME-CONTRACT.md)。

2026-09-05 scoped普通投影恢复候选：initial/resume/search实际manifest、MEMORY-only抑制后
结构effect/terminal相邻96绿；最终start字节负例2绿。原initialscoped红已恢复，仍待独立review，
不声称所有旧producer/short/native完成。见[来源契约](../plans/2026-09-05-primary-resume-sources/CONTRACT.md)。

2026-09-05 Primary history runtime隔离切片94项聚焦绿，**预先scoped ResumePackage首Provider仍有1项P1红**；
后继动态ResumePackage漏发纠正21项聚焦绿；来源功能闭合及其他carrier完整性审计仍未完成。
固定API/helper已入树；后继no_recall origin纠正，相邻合跑41绿/初始scoped1红，exit1。
不得合主生产。见[运行层契约](../plans/2026-09-05-s6-primary-preparation/HISTORY-RUNTIME-CONTRACT.md)。

2026-09-05 primary history API 隔离候选已接公开 Memory batch visibility，50项聚焦通过；
真实 memory-only forget / 跨 Run 来源 / recall binding / signed WS late fence 已有 library/API
证据。runtime 组合、独立复核、short exact carrier 与真实 UI/provider 仍独立待续，未改 gate。
见 [Memory 当前边界](MEMORY_SDK_BOUNDARY.md) 与 [验证记录](../plans/2026-09-05-primary-history-api/VALIDATION.md)。
2026-09-05 Primary 精确 SDK 授权新增独立候选：生产授权策略/installed SDK/真实文件 effect
确定性 fixture 通过，HUMAN bound socket 接权限补读与响应。native 未验收，WAITING通知
由运行层另行组合；底层旧列表非 SDK public port。见 [UI](UI.md) 与
[候选契约](../plans/2026-09-05-primary-sdk-decisions/CONTRACT.md)。

2026-09-05 Primary API 后继修复处理 terminal authority/raw SDK hash 差异、当前 source
过滤及 commit 后唤醒失败的 durable ACK；依赖 Carver 统一 helper，真实组合测试待主运行。
公开 suppression 无原子 snapshot/epoch，不将逐来源复查或既有40项局部绿色称为完整闭环。

2026-09-05 Primary API 隔离切片完成 source-bound history/state 与 exact control，聚焦40 passed。
queued 下界计数及 history keyset 有界；仅证明 Host source suppression，来源谱系扩展仍后续。
实际组合/UI 验证待主协调，不改变 S6/program 完成度。详见
[接口契约](../plans/2026-09-05-s6-primary-preparation/PRIMARY-API.md)。

2026-09-05 S6隔离分支新增control verified connection复用与无scope admission，聚焦29 passed；
standalone/dynamic route→生产effect/terminal identity及状态通知聚焦181 passed；
CREATE_NEW active None生产AUTO binding后继66 passed后发现origin P1；冻结来源纠正85 passed，
origin获独立限定ACCEPT；首tool启动同步后继87 passed，仍需独立复核/native验收；完整历史来源suppression未闭合，
Manual UI未接，UI/API组合待验证，不可合main。详见
[实施交接](../plans/2026-09-05-s6-primary-preparation/IMPLEMENTATION.md)，不改变main完成度。

2026-09-05 当前接续已安装 Harness 0.7.2 / Memory 0.6.3，两个真实 queue.enqueue root 完成
workspace effect、TaskScope closure 与认知物化；独立质量审查发现了 episode 时间 P2。
该 P2 已完成源码修复与 26 条回归，待真实入口复验。原生 chat 冷启动通过，S6 UI 仍未交付；完整 machine gate 尚未通过。
最新候选、测试统计与历史失败边界见 [PROJECT_STATUS](PROJECT_STATUS.md)。

2026-09-01 Human Memory Program 已完成 S4 Task 5–8 Host TaskScope + Runtime Execution Closure（含用户
A2 批准的最小 S5 execution composition）：v39–v44 schema、六 bounded 阅读视图/checkpoint verifier、
permission-first search/exact open、单 foreground Run/durable FIFO/control、recovery fence/emergency
export、fresh HUMAN production composition 与唯一 `SdkRuntimeIngress.start`。2026-09-01 整改闭合两个
P1：generation fence（`EffectBoundary` 最终 admission 覆盖 SDK start/每次 control 发送/物理 Tool
dispatch，`ForegroundEffectAdmissionGate` 接入 `ProductEffectExecutor`，stale worker 外部副作用为 0）与
live control delivery（durable commit 即时唤醒、控制泵、pause ACK→PAUSED、STOP/CANCEL 独立信号与
终态）。验证：code-audit round-3 PASS、100k archive/execution value、9/9 fault、22-case API smoke、
full pytest 6218 passed（6 个失败全部为既有基线/本机环境项且在未修改 main 复现）。S5 剩余
RecallPlan/recall/动态 Context/semantic closure 与 S6 UI 未实施；PAUSED 无生产 resume、双 composition
路径漂移等 P2 边界见 [`ARCHITECTURE.md`](ARCHITECTURE.md) 对应节。gate run 位于
`plans/2026-08-29-human-memory-digital-twin/increments/2026-09-01-s4-host-closure/verification/r2-p1-closure/`。

2026-08-30 Human Memory Program 已完成 S4 Task 1–4 的 Host fresh epoch、永久 evidence、Canonical TaskScope
Archive、recoverable task-home provisioning 与 append-only multi-root binding authority。state schema v38
以 Host verifier 确认的 durable user evidence/interaction 或 Host-issued Auto Run snapshot 生成 grant；
Auto 在 authorize、append transaction 前和 commit 前都从 durable source receipt 重验 snapshot 时窗、active
Run、exact context/config revision 与 configured-root filesystem identity，缺少 current-Run authority port 时
fail-closed。binding receipt 持有 canonical sorted root-set commitment、exact parent/grant 与 immutable
revision。POSIX root 在 commit/effect 前以 no-follow fd 和 filesystem identity 重验，Windows 无等价实现时
fail-closed。schema-v2 route receipt 与
`TaskExecutionEnvelope` 必须交叉绑定 exact binding-set receipt id/hash/revision，当前 Run 不能使用后续 append
的新 root。provision receipt/proposed root 仍只是候选，不能自动升级为 authority。当前逐项缺口是：
S4 Task 5 六阅读视图/checkpoint verifier、Task 6 permission-first search/exact open、Task 7 单 foreground
Run/durable FIFO、Task 8 Host composition/旧入口 fence/data epoch/recovery/emergency export；S5 Task 8
才负责主模型 route/recall/context/tool 的最终 production composition，S6 才切 UI。不能把这些边界混作一个
“Task 5”，也不能把 S4 Task 8 误记为 S5。当前能力不作为产品成功声明。当前边界与证据见
[`MEMORY_SDK_BOUNDARY.md`](MEMORY_SDK_BOUNDARY.md)。

2026-08-29 当前 Project-scoped managed Skill 安装事实：聊天和 Settings 统一进入
`ProjectSkillInstallService`，外部 GitHub 内容先冻结 exact commit、成员清单和 Project identity，再由
`skill_install` 的真实 UI 授权继续；模型、通用 shell 和 UI boolean 都不能代替授权。Manager 原子发布后，
同一个 durable intent 必须再完成 canonical `skill.install.verify` Run，证明新 Run 能从 exact owner +
Project scope 的 frozen catalog 解析正文，才可结算为 `succeeded`。Capability Center 以当前 Session 的可信
Project binding 查询，projectless 或其他 Project 不继承。macOS 隔离 debug App 已完成目标仓库
`4d8c803ba03b…` 的真实安装与 UI 可见性验证；消息输入栏的 slash catalog 也从同一可信 Session/Hub/Store
投影读取，打开 `/` 时按当前 Session 重新拉取，不缓存安装前旧目录，`/plan-` 已真 UI 显示三个成员且
projectless 会话不泄漏。本轮是该故障链的验收证据，不替代 plan 中尚未执行的完整
恶意仓库、跨 Project 和全 surface 矩阵。详情见 [`ARCHITECTURE.md`](ARCHITECTURE.md)、
[`AGENT_HARNESS.md`](AGENT_HARNESS.md)、[`UI.md`](UI.md) 与
[`plans/2026-08-27-chat-skill-install/results.md`](../plans/2026-08-27-chat-skill-install/results.md)。该段记录
切换前的 Project-scoped 验证链；当前新安装以以下 user-global authority 为准。

2026-08-29 当前普通 Session 与 Skill 安装事实：未选择目录时，Host 在
`Documents/SimpleHarnessProjects/Session-<id>` 分配独立工作目录；显式选择时使用用户选择目录，绑定在
Session 创建后不可改。Settings/Chat Skill 安装共享一个 user-global managed authority，固定 Git commit
与 digest，经确认、Manager 原子 publish、fresh Run 验证和 durable activation 后才进入所有 Session 的
catalog；Skill/Tool 可发现集合为全局集合，实际执行仍经权限、健康与 scope 策略。新安装默认授权模式为
Auto。SDK 前台 Run 会在 publish lock 内冻结 user-global Hub snapshot，把其中的 Skill metadata 投影到该
Run 的 `RuntimeToolCatalog`；slash help/list/schema/dispatch 也从同一快照构造 catalog，前端每次开始新的
`/` 输入都会刷新，避免安装或切换 Session 后继续使用旧缓存。`tool_search -> skill_invoke` 再按
locator/content hash 读取冻结正文。Session 创建时动态冻结当前 Companion owner；旧的空 ownerless Session
在首次 admission 时只允许幂等绑定当前 owner，非空或跨 owner 数据仍 fail closed。真实
`deepseek-v4-flash` 已在冷重启后的旧 Session、新建默认 Session及用户选择目录的新 Session 中完成该链路。详见
[`ARCHITECTURE.md`](ARCHITECTURE.md) 顶部与 [`PROJECT_STATUS.md`](PROJECT_STATUS.md)。

2026-08-29 安装收敛补充：完整 40 位 commit URL 直接使用 GitHub codeload，不再先消耗 GitHub REST
`/commits` 限额；branch/tag/HEAD 仍必须经 REST 解析为 immutable commit。安装失败会产生结构化、可终止、
可查询的 Run 结果，同一稳定 failure identity 不会被 Agent 盲目重放，只有显式 retry 才推进 durable attempt
generation。统一能力中心与兼容 Skill Store 都从同一个 `user:v2:*` managed catalog 投影已安装项。

2026-08-30 历史 production 消费快照：Service SDK `0.3.12`（wheel SHA-256 `710ae66b…`）、
Harness SDK `0.6.4` candidate（source `21f3c7a…`，wheel SHA-256 `ecb6e85c…`）和 Memory SDK
`0.5.2`（`deff2fa8…`）。Service 的发布 manifest 仍记录 Harness `0.6.2` 构建成员；消费端按其
`>=0.4,<0.7` 约束独立准入 0.6.4，并分别校验 Harness candidate manifest 与 Service authority root。
0.6.4 尚未 tag/release，所以当前不标记为官方三 SDK release unit。上一次干净 macOS 实例是
0.6.2 组合的历史证据；本次 rebase 后的 0.6.4 构建仍需重新完成真实 UI/provider 验收。

2026-08-30 Realtime 消费端现状：旧 `/ws/audio` 与本地 VAD/ASR/TTS 链继续关闭；新的
`/ws/realtime-voice` 由 Service SDK `0.3.12` 的 loopback protocol、Realtime client 和 provider transport
负责，前端只有一个电话式开始/挂断入口，并且只在用户点击后申请麦克风、创建 AudioContext 和连接后端。
本轮自动化覆盖本地鉴权、origin、PCM framing、barge-in、挂断和资源释放；真实 Provider 连续多轮通话尚未
重新验收，因此该路径是已接线候选，不标记为 release PASS。它只承载 provider-native voice；若未来加入
Agent Tool/Workflow，仍必须进入正式 `ProductTurnPreparer`/RunKernel authority。

2026-08-27 当前 Project-scoped Sessions 事实：macOS 冻结场景 S-PS-01～S-PS-08 已全部通过。Session 在创建时绑定 Project，现有 Session 不能修改根目录；要在另一目录工作需基于目标 Project 新建 Session。终端、内置文件工具和 Project Rules 只使用冻结的 execution root，project-bound Run 不暴露进程级固定根的动态 `mcp:filesystem`。从旧 schema 升到 v33 会按全新安装清空升级前 Session、消息、Project 与会话派生数据，同时保留全局 Provider/设置/Keychain 和磁盘文件。Windows 是未来独立范围。详情见 [`ARCHITECTURE.md`](ARCHITECTURE.md)、[`AGENT_HARNESS.md`](AGENT_HARNESS.md)、[`UI.md`](UI.md) 与 [`PROJECT_STATUS.md`](PROJECT_STATUS.md)。

2026-08-29 历史 Tool/Capability 验收快照：当时 simple_harness vendor Harness 0.6.4 candidate（source
`21f3c7a…`，wheel SHA `ecb6e85c65e9140c6838666f59f38239557e15cf410c1afe023ffd06bfb35be7`）
与 Memory 0.5.2（wheel SHA
`deff2fa85a269a3978f2c6efcd99fda77abcb74444170361365fd00ec0164e9e`）。SDK 公共 runtime catalog 统一
built-in、健康 MCP、Skill metadata 与 Workflow profile；fresh Run 采用 compact direct kernel，其余能力
在同一 durable Run 内搜索、描述、激活并刷新下一 Provider attempt。目录不授予权限，目标执行继续经过
Host prepared authorization、scope 与 physical identity。完整事实、恢复和当前 UI evidence 边界见
[`ARCHITECTURE.md`](ARCHITECTURE.md) 与 [`AGENT_HARNESS.md`](AGENT_HARNESS.md)。

生产组合继续使用一个 borrowed `MemoryManager`、正式 AgentIdentity、SDK-prepared
Memory 与 read-only product Context provider；root 与 continuation 使用各自 immutable source ref。
foreground committed Turn 与非 Harness product outbox 按 provenance 分治，普通前台工具不再二次 live recall。
显式 remember/read/forget 使用完整可信 principal 与正式 fact API，返回准确 fact ID；forget 以显式
`source_event_id` 生成持久 action receipt，同 action 重放保留结果、后续 action 稳定返回 no-op；shutdown 先关闭借用
runtime、再有界 drain/关闭唯一 SessionDB owner，重复关闭不重复释放 manager。自动化门禁已完成且 0 新红；
macOS Computer Use 真人 SH-M1～SH-M6 与 SH-SURFACE 已全部通过；包含真实 DeepSeek、PPT/权限/Artifact、
跨进程冷重启召回、recall timeout 安全降级、record transient 未落库即退出及无故障启动恢复。
开发期 schema 变化用显式三库 reset 从空库开始，不实现用户运行时全面抹除。聚焦自动化 D1/D2/D3、
Rust diagnostics 与 build 已绿；simple_harness macOS 真人消费者 CTX-1～CTX-5 与 surface smoke 也已
完成。消息页文本附件以 private `input_text` 进入 frozen stage，公开 Context 只显示有界元数据，
Provider wire boundary 才降低为兼容文本；budget-only cancel receipt 不会再阻塞 ordered projection cursor。

当前 Memory 一等集成边界、身份 trust chain、Context source lifecycle、迁移/fault 语义与真实 UI 验收状态，
以 [`MEMORY_SDK_BOUNDARY.md`](MEMORY_SDK_BOUNDARY.md) 为准。

历史记录 — 2026-08-21 SDK Context cutover 校准（代码锚点 `e92883a5`）：当时前台文字 Run 的链路是
`_run_product_harness_chat -> _execute_sdk_run -> _assemble_sdk_messages -> SDK Runtime`。
它尚未消费已经构造的 `TurnInput`，也未进入保留的 `ProductTurnPreparer` /
`ProductTurnPreparationService`；因此当前首个 Provider 请求只有公开工作叙述 system prompt、最多
20 条普通 conversation 投影、当前用户文本和 SDK Tool catalog。Persona、召回 Memory、Skill 指令、
附件、项目/任务快照、Context OS 预算/压缩、会话 `model_params` 都尚未由这条生产链路冻结并交付。
Context Inspector 仍是 legacy persona/facts/V2 tool registry/history 的独立估算，不是 Provider
请求事实源；SDK provider invocation 的真实 usage 也尚未投影到 Session context usage/billing。
legacy preview 还缺少统一公开脱敏，可能把敏感 header/token/正文直接展示。这些是已确认的生产
缺口，不能继续把 `ProductContextAdapter` 或 Inspector 估算描述成当时已接通事实。

以上段落仅保留为切换前历史校准记录；其 Context/Memory 主缺口后续已关闭，不代表当前 0.4.0 Host
依赖链。当前事实以本索引顶部 2026-08-25 段落及各专项事实源为准。

2026-08-20 校准：Agent 执行时间线继续复用 canonical Run ledger 和
`HarnessPublicReadService`，细粒度活动条目不得创建第二套状态机；时间线及 Inspector 详情
明确属于 `context_visibility=exclude`，不会自动进入模型上下文。SDK tool turn 的公开工作叙述
与工具卡按 canonical Run 聚合为可折叠“思考过程”；隐藏 reasoning/CoT 不投影、不持久化、
不进入后续模型上下文。

维护规则：功能通过测试后，同一次交付必须更新对应模块架构；完成度、里程碑、worktree 与项目级已知问题同时汇总到 `PROJECT_STATUS.md`。`STATUS/` 只保留历史链接兼容，禁止继续双写。

| 文档 | 范围 |
|---|---|
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | 全局模块完成度、活跃 worktree、最近里程碑、项目级已知问题与验证入口；新任务接手的第一站 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | simple_harness 全局长任务架构基线：单主 Session Harness、模型驱动 Profile、持久化、DeepResearch/PPT、通用行动/能力包、HITL、恢复、Trace/Eval 与升级边界 |
| [UI.md](UI.md) | 当前暗色优先 UI 主题、共享语义样式、页面覆盖范围、业务边界与真实 Windows 验证状态 |
| [COMPANION_GROWTH.md](COMPANION_GROWTH.md) | Companion 长期成长当前事实：唯一 Store/Router、可信 owner inbox、durable GrowthEvent、同一 RunKernel 的 reflection/candidate/evaluation 生产编排、Manager activation receipt、V2 Reminder、legacy writer 退休边界与真实 provider 阻塞状态 |
| [AGENT_HARNESS.md](AGENT_HARNESS.md) | 当前 Agent Harness 事实源：固定 `agent.general` root、`workflow_spawn` ticket/Driver、TaskGoal/Attempt 失败闭环、running-root FIFO、Manual/Auto 与可执行能力目录 |
| [AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md) | Agent 编排（"任务编排"视图，Phase3 P3.1 Host 直连）的当前事实：装配位置、编排数据目录与单实例锁、固定的部署政策（关闭本机代码执行、L2、只开放工作区工具）、身份与审批、SIGKILL 恢复语义、测试场景、部署清单与限制 |
| [SDK_EXTRACTION.md](SDK_EXTRACTION.md) | Simple Harness SDK 提取与消费事实源：当前 vendored Harness 0.6.4 candidate / Memory 0.5.2 / Service 0.3.12、历史 release/迁移与消费者边界 |
| [MEMORY_SDK_BOUNDARY.md](MEMORY_SDK_BOUNDARY.md) | 官方一等 Memory 生产链、validated local identity、immutable Context source、outbox authority、自动化与真实 UI 验收状态 |
| [Harness R7 历史流程图](../plans/2026-07-20-agent-harness-simplification/target-architecture.md) | R7 时点的“一个产品准备入口、一个薄 Kernel、两个 Driver、一套 Effect/UoW 底座”证据；其中主线程/Code 工作台产品边界已被 2026-07-24 单主 Session 多 root 架构取代，当前口径以 `AGENT_HARNESS.md` 为准 |
| [AgentLoop.md](AgentLoop.md) | ReAct 主循环、工具注册/分发、完成守门、ContextManager 与 main 装配 |
| [ARCHITECTURE.md §14](ARCHITECTURE.md#14-native-workflow-engine-replacement-baseline) | LangGraph 调度/身份/生命周期/HITL/评测/打包耦合、原生执行器替换边界、checkpoint 与旧 run 兼容策略 |
| [DeepResearch.md](DeepResearch.md) | DeepResearch 模块专项架构、能力边界、历史缺口与演进记录 |
| [DEEP_RESEARCH_AGENT_REACH.md](DEEP_RESEARCH_AGENT_REACH.md) | 历史方案记录；当前 DeepResearch v7 不依赖 Agent-Reach，不能作为生产链路事实源。 |
| [SEARCH_GATEWAY_DEEPRESEARCH.md](SEARCH_GATEWAY_DEEPRESEARCH.md) | 快速搜索、SearXNG-like Search Gateway、DeepResearch 原生并行、抓取抽取与聊天进度投影的当前代码基线。 |
| [PPT.md](PPT.md) | PPT 生成端到端链路、渲染路径、模板/图片/视觉评审、验证状态与已知短板 |
最后更新：2026-09-20 CST。当前 NanoJev/PR7 仅完成 local Host runtime candidate 对齐与 Shadow-only 接缝；T09 真实双Task事件通过，grants six-invariance 仍为 probe-level，T01 计数 non-vacuous，R02 真实质量 2/8 不升 Primary。H1-H 仍为候选 worktree、门禁未闭合；不宣称完整 H1 或 Primary。[依据：SDK `ARCHITECTURE/ORCHESTRATOR.md` 与指定 closure evidence]
最后更新：2026-09-21 CST。V1.4 WAIT 候选修复已通过 160 项定向回归及 48 项重叠的并发/恢复/synthesis 回归；固定源码 full_target 复验中。新协议 v6 包冻结真实任务状态和 occurrence outcome，WAIT 登记/唤醒与预算、请求同事务。**H1-H 历史 36/36 是 runner 执行数，不是原始 36 组验收覆盖证明**；完整 H1 与 H2–H8 仍 OPEN。尚未合并主线、重装 Host 或进行当前源码 UI 验收。[当前证据与更正](../plans/taskSys2/升级planV1/v1.4/WAIT复验-2026-09-21.md)。以下历史快照按各自范围阅读。
最后更新：2026-09-21 CST。Operation 补遗实施中，V1.4（去除 NanoJev）整体未完成。新增完成规格批准命令 Host handler → SDK facade → 原 CommitService/Store，Spec/receipt/event 同事务；来源、租户、重放、过期拒绝及迁移定向 27 PASS（0.54 秒，后继 Task contract hash 修正仍在复验）。Host 在独立临时源码副本对齐两包版本后，真实 service/handler 接线 11 PASS（18.28 秒）；仅为派生源码接线证据，不是当前候选字节、wheel 或 UI 验收。移除启动/重建路径上已延期的 PR-7 observer 依赖，保留历史文件。Scope/Plan Commit、准备与效果区分、T0/T3、D3 及完整 H1–H8 仍待；未合并/重装 Host。当前无新增 PlanAgent 架构待决。详见 Host V1.4 的 Operation补遗实施记录-2026-09-21.md。
