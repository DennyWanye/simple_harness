最后更新：2026-09-30 晚 CST（审阅判不下来：换新会话复审一次，仍判不下来交给人，SDK `opt.104`）。此前保证通道下审阅员回"判不下来"（INCONCLUSIVE）时取结论那一步直接报错，校验按"没通过"让执行者重做，可能对的活被反复重做。现在：①第 1 次调用的回复解释出来是判不下来时不导入为正式记录，记 `AssuranceReviewSecondOpinionRequested`（classification=`SECOND_OPINION`）并按现有第二次调用的路建第 2 次调用（理由 `SECOND_OPINION`，同一冻结请求、不带第一次结论、新 Agent 会话），第 2 次的结论照常导入（`assurance_review_consumer._prepare_second_opinion`）；②正式记录仍是判不下来时，`assurance_review_runtime._verdict` 返回"通过但需要人定"（`needs_human=True`），走旧通道 D7-8' 现成的挂起 → `review` 审批 → 界面"复核通过 / 不通过 + 理由" → `review_result`；每步只升级一次的上限沿用；③用户复核时在保证通道下写裁决回执 `AssuranceReviewAdjudicated`（`human_commits._record_review_adjudication`，主体 = 正式记录），准备通过证书时把它读进读集，"记录判不下来 + 用户通过"判为可用，未评级的判据按通过计，理由 `human_adjudication:<回执>`（`assurance_validity._prepare_use`）；验收公式对带人裁决的证书免掉"记录结论不是通过"这一条（`AssuredAcceptance.human_adjudicated`），内容审阅读校验层时"审阅员判不了 + 人工通过"算通过（`scoped_content_review`）。**不改**正式记录、不改审阅提示词、不加策略字段。未做：两个审阅员都下了结论但相反时的仲裁（按现在的路第一次已下结论就不复审，不会出现）。真实模型第 1 局把复审 → 挂起 → 登记复核审批全走到，暴露空闲判定只认操作类审批、把"等人复核"判成无事可做（opt.105：待处理的任何审批请求都算等人，`event_handler._idle_facts`）；第 3 局走到用户裁决通过、验收提交，再暴露完成度读取与支撑记录只认"记录结论 = 通过"（opt.106：`review_adjudication.accepted_or_adjudicated` 统一口径，`completion_status._official_accept_review`、`completion_support.read_completion_support` 改用它）。测试 `assurance_exec/test_review_second_opinion.py`（夹具级真实链路 4 条；5 处变异全被抓住）、`test_progress_idle_verdict.py`；相关组 assurance_exec + operation_completion 306、旧通道人工复核 p33 973 全过。方案 `plans/2026-09-27-desktop-next/审阅升级-方案.md`。

最后更新：2026-09-28 CST（NEXT-TG-1.0 插入项，SDK opt.42～opt.52）：**保障层开启（默认）时带发布的任务可以完整走完**。操作提案、操作结果两类审查此前在保障层下从未真跑过，本轮逐层接通：
- 检查策略：SDK 对外批准接口与无损映射支持"操作提案""操作结果"（结果类带效果键，只在拥有该效果的范围上成立）；Host 每轮为拥有效果的范围自动批准（系统身份记账）。提案四项改为审阅员语义判断（代码的三道关卡保留）。
- 提案审查：提交事务只存审查包与四张系统核对回执，审查轮在事务外开；审阅材料含将要发布的已验收文件与四张核对回执；物化复核从已认证清单读有效等级（须全部通过、总结论接受）并严格复核回执。审查按准备该操作的叶子任务记账。
- 结果验收：事务外准备使用许可证书（证书准备器支持操作结果用途），与验收同事务提交。
- 根结论：根效果要求改为核对证书认定的有效等级为通过（正式记录不带证据引用）。
- 最终审查：材料补上根任务自己的已验收操作结果（当前有效）及其发布读回回执（此前只给子步骤验收，审阅员看不到已执行的事实）。
- 结算：保障层下动作的额度预留改用与执行图同一个"原始操作证明"检查结清（动作没有模型执行意图，此前被永久持有，收尾停在排空中）。
真机验收：`mission-e5f82ae8c9f24ff8`（写 MIN.md 并发布）界面全程点击，真实发布→结果验收→最终审查通过→`MissionCompleted` + `AssuranceMissionFinalized`。明细：`plans/2026-09-27-desktop-next/PLAN-STATUS.md` 插入项一节。

最后更新：2026-09-26 CST（SDK opt.18～opt.22，复杂编排真机跑通时修的保证审阅四处；详情见 [AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md) 同日条目 6～9）

**收尾按上限计入**（用户决定 2026-09-26，SDK opt.28/29）：DRAINING 只剩“用量未知”的预留且内容已通过时，按预留额与已知用量较大者结清并记 `ReservationCountedAtUpperBound`，随后 READY→FINALIZED；详情见 [AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md) 顶部条目。

1. **审阅导入读审阅员输入清单改用记录上限**
   - `assurance_review_import.py` 读取审阅员自己的输入清单时，上限从 256KB 改为 `MAX_RECORD_BYTES`（8MB）。
   - 原因：清单包含审阅员读证据的全部对话，长审阅会超过 256KB，每次导入都失败，32 次复核后转“需人工处理”。读取侧早已按 8MB 放行，导入侧漏改了。
2. **空 code_test 的说明文字**
   - `executor_checks.py`、`deterministic_checks.py` 里，没人点名、也收集不到测试的全目录运行（退出码 5），说明改为“不适用、视为满足、不作为无法下结论的理由”。
   - 判定逻辑不变：点名的目标或裸 `pytest:` 收集不到测试，仍判失败。
   - 这是把 2026-09-24 用户的决定落实到审阅员能看到的文字上。
3. **卡死检查不抢在审阅工作前面**
   - 审阅队列里还有未完成的工作，或审阅结果类事件还没被 REVIEW 读进度读到时，编排不判“无可派发工作”。
   - 已转“需人工处理”的工作仍按卡死处理。
4. **可解析但导入失败的审阅回复重问一次**
   - 适用错误码：`UNEXPOSED_EVIDENCE`、`DUPLICATE_CRITERION`、`FINDING_SCOPE`、`MANDATORY_CRITERIA_INVALID`。
   - 处理：第一次调用出现上述错误时，写 `AssuranceReviewInterpretationRejected` 回执，走格式修复同一通道（`reason=FORMAT_REPAIR`，第二次调用），请求里的 `format_feedback` 写明具体改法。
   - 第二次仍错就最终拒绝（`AssuranceReviewImportRejected`）；`POLICY_CATALOGUE_MISMATCH` 直接终拒。
   - 校验：`_require_format_repair` 接受新分类 `INTERPRETATION_INVALID`，要求对应的轮次已提交；第一次调用必须先结算。

独立子代理复核：无阻断级问题。审阅员回复本身的解析**仍然严格**：多余字段、JSON 前多写文字都判格式错误（用户 2026-09-24、2026-09-26 决定）。

最后更新：2026-09-25 CST（主流程优化条目 3/4，SDK `0.13.0.dev20260925+opt.1`）。条目 3：`verification/deterministic_checks.code_test` 区分"判据要求的运行"（`pytest:<path>` 与裸 `pytest:`）和"无人要求的顺带全量运行"，只有后者在退出码 5 时记 `no_tests_collected` 走"无可证明内容"路径（文档类任务规则不变）；判据要求的运行一个测试都没收集到即 FAIL，`assurance/executor_checks.py` 无需改动。条目 4：`orchestrator/assurance_check_policy.approve_check_policy(..., approval_source="HUMAN"|"HOST_LOSSLESS_AUTO")`，Host `project_check_policies()` 传 `HOST_LOSSLESS_AUTO`，事件 `AssuranceCheckPolicyApproved` 记 `actor_type="system"`、`actor_id="host:assurance-check-policy-projector"`，payload 增 `approval_source`、`on_behalf_of_principal_id`；`approval_source` 不进回执正文，同命令重放身份不变；对外入口 `approve_assurance_check_policy` 放行可选字段 `approval_source`。测试 `assurance_exec/test_check_policy_lossless_mapping.py::test_host_auto_approval_is_recorded_as_system_not_human`、`tests/orchestrator/test_code_test_no_tests_collected.py`。核对结论与护栏：证书签发即被判 SOURCE_CHANGED 只写观察记录，不会让正常任务失败或释放资金（23 个真实跑局库无一例）；**以后绝不能把证书失效接到 `goal_resolutions.validity` 列**，否则经 `event_handler._assured_root_grades` → `commit_service` 直接判失败并释放预算；要做"完成前复查依据"须先把读集粒度缩到证书真正依赖的 73 项。

# Assurance 当前生产边界

最后更新：2026-09-24 CST（Assurance 第十段：默认开启 + 主流程跑通）。**保证机制默认开启**：SDK 单一默认选择点 `default_assurance_profile_for_new_mission()` 返回 `AssurancePolicy()`，Host `OrchestrationSettings.assurance_profile` 默认 `"on"`，`"off"` 为显式退出。新增生产环节：Host 以自身已认证 caller，在每轮编排循环后、以及 SDK 审阅准备前（部署端口 `AssuranceDeploymentPorts.check_policy_projector`），为每个冻结完成范围批准由原需求无损推导的检查策略（SDK `lossless_scope_mapping`：语义判据→SEMANTIC、具名检查→精确注册 CheckSpec，推不出就 `CHECK_POLICY_UNRESOLVED`，不猜），并为根范围批准最终审阅用途的策略（`mission_final_scope_id` + 对外接口可选 `purpose`）。保证通道下任务整体判定复述已采纳根决议上的认证等级，不另请未认证评判；根节点完成判断读根决议判据而非旧格式审阅记录。**验证**：Host 生产装配 + 真实模型（Grok Build 通道 `grok-4.6`；DeepSeek 日卡上游当晚只回空占位）run-21 从创建走到 MissionCompleted（verification_passed），收尾 FINALIZED/USABLE；途中 14 个接线缺陷逐局修复（授权键粒度、复核比较有效期、审阅预算编号、披露排序、审阅调用上限等），明细见 SDK `plans/assurance-1.1/BODY-WORK-IN-PROGRESS.md` 第十段、[HANDOFF](../HANDOFF-2026-09-23.md) §5。钉版 `0.13.0.dev20260923+assurance.14`（源 1903fbaf）。**边界**：只跑通 1 局 1 题 1 提供方；12 局真实模型、独立审阅、原生点击、Host 26 个既有失败迁移、证书签发即判 SOURCE_CHANGED 的读集粒度未做。

最后更新：2026-09-23 CST（Assurance 第九段，Host/UI 接线）。**生产链（代码存在，定向测试验证，非集中验收）**：Mission 创建（原 UoW）→ factory 选择器（Host 单一选择点 `settings.assurance_profile=="on"` → `AssurancePolicy()`，否则回原 COMPLETION_V1/LEGACY）→ 四 consumer 生产装配（`install_assurance`，Host 在 `startup_assembly` 里 hierarchical→taskgraph→assurance 顺序装配，`assurance_root_setup` 用认证 principal/租户在原生根上幂等 `install_assurance_root`）→ 读侧：`MissionControlV1.assurance_snapshot/review/use_check` → 固定 caller `AssuranceApi`（SDK `api/assurance.py`；每个已安装 (tenant, principal) 一个实例，请求体不得命名租户/principal，多余字段 CONTRACT_INVALID）→ Host `handlers.py` 三个 `mission_assurance_*` 消息（先原 facade 归属检查，拒绝时 payload 带 host-error-v1 的 `assurance_error`）→ 前端 `assuranceStore.ts` 严格解析（字段集合精确、枚举、64 位指纹/摘要、`diagnostic_only`/`certificate_ref` 硬校验）→ `MissionAssurance.tsx`（历史状态与当前可用性分列；HISTORY 钉 seq；分页 ≤100、`SNAPSHOT_CHANGED` 从第一页重来；审阅详情钉 review_key；use check 只诊断；`mission_changed` 置 stale；重连/换 Mission 清屏；无效 DTO 保留上次画面）。通知 transport：SDK NOTIFY consumer → `service._assurance_notices`（≤256）+ 变更泵唤醒，status 暴露 `assurance_available/assurance_profile/assurance_notices`。指纹：`sdk_fingerprint`=已装 SDK assurance 源码摘要；`host_fingerprint`=Host commit+脏标+钉版 wheel 摘要。合同：SDK `assurance/contracts/host-*-v1.schema.json` + `validate()`。**边界（第九段时点；2026-09-24 起默认 on，见上条）**：`assurance_profile` 当时默认 off；原生点击、真实模型、48 组/继承 66/OCC 12/变异/stateful/legacy H1 未跑；Host `tests/orchestration` 全目录 全量 26 failed / 355 passed / 20 skipped（788s）；用 monkeypatch 禁用 Assurance 装配、再禁用 TaskGraph 装配两路各 28 failed / 353 passed（多出的 2 个正是本段新加的 Host Assurance 用例，其余 26 个集合逐条相同）——即 26 个既有失败与本段装配无关：6 个因 Mission 停在 CREATED（Host 启动门要求已批准的完成 Spec，用例写于 2026-09-13）、5 个 `not enough values to unpack`、1 个用例自绑 package 6 与 planning-decision-v1 冲突、1 个要求 SDK 源根为 Git 根、1 个超时等；归第 10 项 legacy/全量回归阶段处理，不作为本段通过依据；`verify_development_handoff.py` 对已改源码报 differs（交接冻结快照，第 10 项重生成）。
最后更新：2026-09-23。跨电脑源码交付：完整 HTN + TaskGraph23 + Assurance WIP SDK 位于 `sdk/simple-harness-sdk/`；最终 TaskGraph Host UI2 源码位于 `development/taskgraph-host-overlay/`，尚未覆盖根 Host。根 Host 仍固定 HTN wheel。Assurance 主体、UseCertificate/acceptance/终态/生产装配未完成；仅局部 seam，不是整体产品 PASS。实际交接见 [HANDOFF](../HANDOFF-2026-09-23.md)，下文保留历史检查点。


SDK 源码已保存到私有主仓库，但新 lane 的 factory、四 consumer、完整 current Validity/UseCertificate、acceptance 和唯一 terminal writer 尚未部署。TASK_CONTENT 原 runner/collector/official 的局部 scripted 接缝已实现；不是新 Mission 默认启用的已完成能力。

全部剩余工作、代码入口、源码身份和下一电脑命令统一见根 HANDOFF；测试原始证据留在本机 ignored 目录。
