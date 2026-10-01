最后更新：2026-10-01 下午 CST（HTN 精简改造 片 0：先删先并，SDK opt.113）。依据 `plans/2026-09-27-desktop-next/HTN-后续-方案.md` 第 4 版；逐步记录见同目录 `HTN-片0-实施记录.md`。
- **只剩一种规划协议**：旧的"计划提案"协议整条路径删除（文本解析器、回复收集、修复文本重放、按规划包版本号的分支）。SDK 默认值 = 分层模式 + 当前决定协议；平面模式仍可用但必须在任务规格里明写，平面任务没有规划协议、不写协议绑定。旧协议名在规格构造、请求解析、创建三处都被明确拒绝。库里按旧契约建的分层任务（没有协议绑定行，或绑的不是当前规划包版本）在主循环入口被停掉，停止原因 `unsupported_planning_package`（`Orchestrator._refuse_unsupported_contract` / `planning_protocol_binding.current_planning_protocol`）。
- **修复只有一条路**：根终审打回、只读步骤越权改文件、同一步反复同样失败三种专用修复全部并入通用修复请求（`PlanningRepairRequested`）。Harness 只报告事实，不再替规划器退掉根目标的做法、取消步骤或写结论：
  - 根终审打回（含人裁决打回）：`Orchestrator._request_root_review_repair` 按最终审查的正式记录记一条请求，带审阅员没判通过的**全部**判据与原话、这是第几次、上限；请求范围是整个计划（任何一步上的计划改动都算处理了它）。上限仍是 `max_root_review_repairs`（默认 1），用完且没有还在等规划器的请求时按 `root_review_repairs_exhausted` 停。保证通道上的打回不带"阻断级"标记，旧专用路径在这种打回上什么都不做——这个真机缺陷随之消失。
  - 只读越权：拒收保留（权限），拒收之后的升级删除；`ResultRejected` 走普通失败请求。
  - 步骤失败请求（`planning_repair_requests.collect_triggers`）新增 `occurrence`：这一步失败了几次、连续几次是同一个失败、失败指纹（`step_failure_facts` / `failure_fingerprint`）。
- **规划包**：顶层"被拒做法段"与做法库条目上的两个分原因标记删除；做法库条目改带一个 `rejected_reasons`（该做法在本计划某目标上被采用后又被哪次修复决定退役，原因 = 规划器当时写的理由 + 那次决定处理的请求，`HierarchicalDispatch.retired_methods`），是事实不是禁令。新增 `plan.refined_goals_under_repair`：修复请求所指的已细化目标，按未细化目标同样的形状列出并带当前采用的做法实例（规划器换做法要引用目标参数）。发给后续步骤的最终审查意见只含审阅员原话（`review_feedback`，版本 `final-review-feedback-v2`）。
- **删掉的开关与规则**：配置项"分层修复开关"及其关闭分支（修复恒开；评测模块的"关掉修复"对照臂遇到即报错）；注册检查里的测试关键词规则（判据出现"新增测试"等词就强制要求测试输出端口）。
- **顺手修的两处"异常逃出主循环"**：计划损坏时收集修复触发源不再拖垮循环（按计划完整性停掉该任务）；执行者结果没写必需输出端口时按"结果被拒收"（`completion_inputs_refused`）处理。
- **新增欠账**（片0-欠1）：没有协议绑定的分层任务在代码里是一整套平行旧世界——旧编译/提交入口（`apply_plan_proposal` / `compile_proposal`）与约 100 处按有无协议绑定分支的旧验收路径，生产上已不可达，但约 90 个测试文件的夹具建在其上（通过两个只存在于测试目录的接缝保持运行）。删除它需要先迁移这些夹具，单独立项。
- 验证：变异检查 12 项全部被抓住（`.local-test-evidence/2026-10-01/slice0/mutations/`）；全量回归与真机记录见实施记录。

最后更新：2026-10-01 凌晨 CST（任务级"文档覆盖"裁判：多步任务里没有评估的声称不否决、由有评估的贡献决定，SDK opt.112，`verification/mission_coverage.py`；规划器重复提问沿用已有回答、已答问题一律给规划器看（标 binding_current），SDK opt.111，`planner_views.answered_questions_for_planner` / `PlanningHumanStore.register_reusing_answer` / `event_handler._register_human_question`；同版：最终 / 组合审查包的验收候选引用改按真实验收正文（修订 0 + 正文哈希，`root_review.acceptance_ref(store, id)`），与披露给审阅员的证据一致；收尾前复查"通过的依据还有效吗"：证书按读集逐项重读、真变了收尾不放行并给规划器记证据失效请求，SDK opt.110；中间层的独立组合审阅结果真正用起来：通过 → 形成中间目标结论、判不下来 → 问人、打回 → 修复请求，SDK opt.109——真机在现有方法合成器提示词下到不了中间层，夹具级证明；均见 [ASSURANCE.md](ASSURANCE.md) 顶部；此前审阅判不下来：换新会话复审一次、仍判不下来交给人，SDK opt.104–108；规划器"等待"可以等方法实例 / 目标，SDK opt.103；此前真实模型结构修复走通：资料换版 → 后继步骤 → 第 2 版计划 → 用新版完成交付，SDK opt.94–102；此前架构方案 C：回答规划问题可登记成任务资料，SDK opt.91；此前 09-29 收口：任务执行者用技能、技能准入评估、多任务并发，SDK opt.83–86；结构修复真机补的 5 处，opt.87–90；此前：发布交给系统 + 按谁的错扣次数，SDK opt.66–82；方案 `plans/2026-09-28-system-operations/00-PLAN.md`）。
- **规划器"等待"可以等方法实例 / 目标**（SDK opt.103）：WAIT 能被唤醒的只有步骤（跑完）和已完成的记录；规划器在修复轮常写"等这一步所在的方法实例 / 目标"，此前一律判"没有可等的生产者"拒掉、白花规划次数。现在 `_expand_planning_wait` 先按规划包给出的身份核对（方法实例：已采用、版本 = max(1, 计划版本)、参数摘要；目标：内容哈希），再用 `planning_wait_targets.steps_under` 沿已采用的方法实例往下找叶子步骤，只挑正在跑的去等；`PlanningWaitRegistered.wait_for` 记换算后的步骤，`requested_wait_for` 记规划器原话。下面没有正在跑的步骤仍拒，理由写明。规划器模板不改。
- **真实模型结构修复走通**（收口第 6 项，SDK opt.94–102，2026-09-30）：第 8 局（`mission-d4615e8a9404e5d8`）资料换版 → 规划器给已通过的第一步提后继步骤 → 第 2 版计划 → 后继与第二步重做通过 → 终审 → 交付，产出引用新版资料。途中补的缺陷：①规划器模板 `planner-hierarchical-v13`（v12 + 子结构字段说明 + 两个填好的例子），格式被拒时下一轮带 `previous_feedback`（按字段路径指出错处），`goal_type_ref` 缺唯一可推断的字段时无损补全并记 `autofilled`；②执行图共享检查只拒"独立需要的工作被移除"和"仍存活的消费者还在用"，保留下来重新组合的目标放行；③输入绑定版本允许"不晚于"（迁移 V30）；④换代后的步骤，旧一代的失败不再等"原样重试"批准；⑤执行池实例上限只数没关闭的，编排器定期关闭已结束任务的执行者（只改状态、记录保留）；⑥反复失败后修方法的机会按步骤算（用户 09-30 选 A）；⑦结构修复给被换代目标记的"旧派发作废"标记，在该目标新一代结果提交时清掉（`HtnStore.clear_revoked_generation`；此前没有任何地方清，动到根目标的结构修复全都收不了尾）。证据 `.local-test-evidence/2026-09-30/struct-repair/`，过程记录 `plans/2026-09-27-desktop-next/PLAN-STATUS.md`。
- **资料换版本 / 撤销 → 重新规划**（架构方案 B，SDK opt.92）：`planning_repair_requests.source_change_triggers` 听 `SourceSuperseded` / `SourceRevoked`，只对有证据的受影响对象发一条"证据失效"修复请求——claims 引用了旧版的已通过结果；拿着旧版还在跑的尝试等它跑完再评估（opt.93：验收本来就对照当前资料，当轮发请求只会让规划器回 WAIT 又被修复轮拒绝），评估记录记下拿旧版跑过的尝试；未派发的步骤下次自动拿新版；新登记的资料不发请求；没影响到谁只记 `SourceChangeAssessed`。终审切包事件记 `source_versions_hash`，资料集变了报 `SOURCES_MOVED` 重切（此前终审不看资料版本，带资料的通用任务用旧资料会静默完成）。修复请求新增系统消费出口 `settle_addressed_requests`：影响范围没有新增工作、每个受影响叶子步骤都在请求之后重新验收通过（`AcceptanceCommitted` / `TaskCompleted`）→ `PlanningRepairAddressed(decision_type=SYSTEM_REVALIDATED)`，不再逼规划器开新轮。
- **回答规划问题可登记成任务资料**（架构方案 C，SDK opt.91）：门面 `answer_planning_question` 多一个可选 `attach_as_source`；为真时 `api/planning_answers.py` 在同一事务里回答 + `register_source("sources/answers/<问题id>.md", kind=markdown, trust=untrusted_external)`，幂等键由问题 id 推出，回执带 `source{path, version_hash}`；选项式问题、没有资料根目录的域、资料存储不可用都具名拒绝。资料按每次尝试冻结、只读挂载，所以规划器让那一步原方法重试，新尝试自动挂上这份文件；任务级备注末尾写明路径。不加决定种类、不改规划包、不改模板（"换输入"决定只能绑到步骤产出，绑不到资料）。界面文字回答默认勾选"作为资料附上"。方案与审阅记录：`plans/2026-09-27-desktop-next/架构修改-方案.md`。
- **结构修复真机补的 5 处**（收口第 6 项，SDK opt.87–90）：①系统代办发布提交前先 `ensure_operation_runtime`（只在真要提交时装配；此前进程里没人手动提交过时每轮报"操作运行时不可用"、任务空转）；②规划决定里不确定性的 `affects` 写成一段文字时当一条；③规划器 `DECLARE_BLOCKED` 且没有方法合成可接手时（`event_handler._ask_person_about_blockage`）登记阻塞式人工问题、任务停下等回答，回答后按原路重新规划，待回答期间空闲判定算"等规划"而非卡死；④规划器模板 `planner-hierarchical-v12` = v11 + 修复轮缺外部资料时用 REPAIR/ESCALATE 向人提问（中文、无选项、阻塞），第 8 版包同配 v11/v12，按任务绑定的模板版本选择，已绑 v11 的任务不变；新任务默认 v12；⑤回答规划问题时同一事务追加任务级 `HumanCommentAdded`（`planning_human_store._note_answer_for_workers`，目标为任务），每个执行尝试的反馈都带任务级用户备注，执行者能看到用户给的资料。真实模型五轮：修复请求→提案→问人→回答→再规划都走到过，未走到第 2 版计划收敛；卡点是规划器修复轮写对复杂决定格式的稳定性（证据 `.local-test-evidence/2026-09-29/e3-real/`）。
- **按谁的错扣次数**（第 1 批）：`orchestrator/failure_classes.py` 把尝试失败分成 模型做错 / 格式没写对 / 服务出错 / 被打断 四类；只有模型做错扣任务次数，其余在失败时退还（账本链与 `attempt_count` 同步退，按 `AttemptChargeReleased:<尝试>` 幂等）；同一步非模型失败合计 6 次停下（`non_model_failures_exhausted`）。
- **发布交给系统**（第 2、3 批）：确认页批准的发布效果由系统按 `AUTHORIZED_SLOT` 自动准备申请单（`orchestrator/system_operations.py`），模型不写候选；申请单引用审过的真实文件，理由标 `reason_source=system`，人仍在批准卡片上逐个批准；找不到源文件时先请规划器补步骤（最多 2 次）再明确停下。
- **后台只处理有变化的任务**（第 4 批）：主循环按"本轮开始时的全局非心跳事件游标"判断，只处理有新事件 / 满 10 秒 / 刚创建 / 总时限已到的任务；空闲返回前全量看一遍。真机采样：剩余 CPU 尖峰主要是会话向量索引（onnxruntime），编排主循环约占一核 14%。
- **步骤声明产出文件**（第 5 批）：任务入口（界面与主 Agent 共用）在每条 `action:file_publish.publish:X` 前补 `file:X`；定计划时检查每个要发布文件的 `file:` 要求恰好链接到一个步骤（否则退回合成器重写一次）；系统发布以声明产出者为锚、在它及其下游取路径完全相同的最下游一版；未被链接的步骤不再兜底承担别人的写文件要求。
- **审阅不再交白卷**：审阅循环上限 = 查看上限 32 + 余量 8，剩余 ≤8 次时查看结果附"尽快作答"，查满后工具拒绝并要求立即作答。
- **重启打断后不再挂住**（opt.74～76）：执行者一轮挂在"工具结果未知"（执行层上报工具阻塞时带上工具操作的持久状态，只认 `unknown`，正在执行的工具不误伤）与"模型调用结果未知"同样处理（等满时限按被打断放弃、不扣次数、换新尝试）；单轮墙钟超时与执行层内部异常（`base_agent_driver_exception`，重启后调用已交出、结果未知）归为被打断（原地重做、不问规划器）；发布结果审阅重试用完后不再算合法等待，交给卡死检测停下并在停止说明写 `operation_outcome_review`。
- **主 Agent 入口**：`mission_start` 结果在主对话（`PrimaryChatView`）里显示为任务卡片（进度、完成要求确认、发布批准，走任务页同一连接与消息，人亲手点）；新增只读工具 `mission_status`（进度 / 等谁 / 已发布 / 停止原因，不能代批）。确认完成要求默认预填（普通要求算内容、每条发布各配一个"哈希一致"效果），一般一次点击。
- **合成器看得懂每条要求**（opt.77、opt.80）：方法合成请求的 `criterion_evidence` 原先给每个 `c-user-N` 配同一句总目标，模型只能按描述猜编号（真机把"写出 README.md"挂到写模块那步）；现在每个编号带任务要求里自己的原文（`HierarchicalDispatch.synthesis_request`）。`file:`/`action:` 原文后附谁负责（`synthesis_statement`：操作由系统在内容通过后执行、不设步骤、证据要求不写发布目录/落点；写文件在工作区写出即满足），只改请求数据、不改提示模板。
- **接力步骤交出全部文件**（opt.78）：输入与输出端口相同的续写步骤（如 `desktop.continue-delivery`）把通过核验的全部文件交给下一步（`overlay_attempt_inputs`，操作申请单除外）；此前只交端口文件 + 原工作区文件 + 测试文件，一步写三个文件时新写的模块被丢。
- **端口规则只有一条**（opt.79）：完成协议下"这一步有哪些输出端口"，接受侧（`read_review_origin` → `declared_output_ports(own_ports=True)`）与核对侧（`output_ports_in_revision`）都算上这一步自己声明的端口；此前没下游、没被链接的最后一步两边算法不同，验收永远被拒。
- **真机验收**：带重启的完整走通 = 第九局（任务页）；从主对话发起、对话里一次点击确认、卡片两次点击批准到完成 = 第十四局（两份发布逐字节一致，两步各一次通过）。第六～十三局各暴露并修掉一处缺陷，记录见方案 G 节。
- **卡住交给规划器**（opt.81）：步骤如实报告卡住 / 失败 / 没进展（`OutcomeRecorded`）与结果被拒、验证失败一样生成规划修补请求（`planning_repair_requests.collect_triggers`，带步骤原话），由规划器决定重排、补步骤或重试；此前没人问规划器，几秒后判"没有可派发的工作"整局失败。
- **被打断的审阅补一次机会**（opt.81）：审阅协议每个审阅只准调用 2 次。采集时若这次调用没提交、且原因不是审阅员的错（被打断：重启/墙钟超时；服务出错：`provider_*`、工具调用解析失败——opt.82，与执行尝试扣次规则一致），另记 `AssuranceReviewTurnInterrupted`（`failure_classes.record_review_interruption`，不改已有回执）。用完且第 2 次是被打断的：整局最终审查把 `REVIEW_INTERRUPTED` 作为包过期原因重切新包（新审阅，仍受每版切包上限）；发布结果审阅准备一次重审（清单加 `review_retake`，新审阅包与审阅编号，同一份回执；`outcome_retake_due` 只允许一次，重审再用完才停，`outcome_exhaustion_is_final`）。独立审阅无阻断项。
- 已知后续：规划器向人提问时的措辞可能把"系统发布"误说成步骤在发布；重审的准备工作若每轮都失败会一直算合法等待（与首次准备失败同一既有行为）。

最后更新：2026-09-28 CST（NEXT-TG-1.0 第三～五批，SDK opt.56–59）。
- **执行过程只读接口**（第三批）：SDK `api/taskgraph.py` 新增 `execution_snapshot`（严格执行图 + 执行过程同一读取时点；键集分页，游标绑定任务/调用者/计划版本/清单哈希/执行内容哈希，变了报 `SNAPSHOT_CHANGED`）与 `execution_detail`（按执行意图所在执行池精确读回合记录；白名单只出模型可见原话、整形后的工具事实、提交摘要、审阅结论理由，全部脱敏）。投影 `orchestrator/taskgraph_execution_view.py`：尝试/检查/审阅/规划/修补请求/计划修订/操作节点与 attempt_of、rework_of、review_of 等因果边，全用记录下来的身份连接，不进调度图。Host 控制通道 `taskgraph.execution_snapshot/detail`；直读 SDK 表的 `live_graph.py` 与 `mission_live_graph`/`mission_planning_decisions` 已删除。
- **产品入口**（第四批）：工具熔断与自动续跑与监工开关解耦（`[self_healing]`，监工关时用按失败原因的固定提示，会话活动记录总是注册）；删 7 个无人读取的配置；设置页「任务发布目录」（`orchestration_publish_dir_get/set`，校验后写回 config.toml 并重启编排服务）；主 Agent 工具 `mission_start`（同任务页的创建路径，幂等键由对话回合+调用派生）；前台运行没有执行入口的委派工具不再放进模型可见目录。并发上限、模型路由、多候选/冲突仲裁保持默认，原因与欠项见进度文件。
- **任务模式 Agent**（第五批 A）：编排执行池的 ARP 配置改为 MISSION（配置修订 2；修订 1 行原样保留，旧会话照常）。每个 Agent 由派发意图的按角色来源集创建（SDK `runtime/mission_sources.py` 只读编排器记录：执行者的冻结输入清单身份、审阅包、终判视图、规划/方法合成写明没有执行尝试），缺失或不符具名拒绝；来源哈希进创建命令哈希；每次新请求冻结前复核并钉进上下文清单；创建被拒只停该意图，不打断编排循环。
- **统一技能目录**（第五批 B）：SDK `arp/shared_catalogue.py`，256K 非思考池为唯一权威，其余原生池镜像（同一技能/版本/哈希/正式验收），成员池拒绝直接写，每次使用先问所有者（一次暂停所有池下一次使用即生效），缺工具的池该技能不可用；Host `skill_catalogue.py` 提供总览/本地安装/暂停恢复退役，控制通道只放行 `agent_skill_request` 与两条评估动词。**任务执行者用技能**（2026-09-29，SDK opt.83）：部署策略 `skill_tools` 声明原生池提供的三件技能工具（`skill_discover`/`skill_load`/`skill_execute`），执行者层级模板 v5（v4 原字节保留）列出它们，经原有"任务∩步骤∩角色∩部署"交集冻结进请求；旧式池不冻结。**准入评估**（SDK opt.84）：试用中的技能只能被评估派发链接指向的那个评估任务使用（每次使用复核，过期/别的任务/暂停都拒）；Host `skill_catalogue.evaluate` 一次完成"开始试用 + 以评估键建评估任务 + 挂到根步骤 `desktop-root-<任务>`"，`admit` 取根步骤的根解析证书（内容类任务，opt.86）或步骤验收证书交 SDK 核对；设置页有开始评估/准入/重新评估/刷新。**多任务并发**（opt.85）：模型调用准入身份 v3 不含名额数，旧 v2 身份只差名额数时照认；Host 默认并发 2。**方法合成宽限**（opt.85）：没回复的回合不占询问次数，宽限 6 次。
- **收尾不被被打断的审阅卡住**（第六批，SDK opt.60–61）：审阅模型调用被强制退出打断后，该审阅记为等待原调用核对、不重发；若它的费用未知，它不再阻塞已判定任务的收尾（`assurance_consumers._drain_decision`），费用按上限计入，意图仍交原核对流程。全量回归无新增失败，见 `.local-test-evidence/2026-09-28/batch6/REGRESSION.md`。
- 进度、证据与欠项：`plans/2026-09-27-desktop-next/PLAN-STATUS.md`。

最后更新：2026-09-28 CST（NEXT-TG-1.0 第二批 B：推进规则，SDK opt.53–55）。
- `orchestrator/progress.py`（纯函数）：`idle_verdict(IdleFacts)` 把空闲任务判成带具名唤醒来源的等待（收尾中、根目标已解决待判定、在跑、动作结果核对中、待人批准、操作结果待定、保障层待办、修复续接等待、规划等待、执行图来源等待）或"卡住候选"；归不了类的一律"等待 + 需系统诊断"。卡住记录与卡住确认共用它（确认仍只读一次计划）。
- 主循环 `run()`：`set_between_cycles(duty, every_seconds)` 让宿主职责在轮与轮之间按间隔执行（Host 注册自动授权/策略批准/执行图启用/自动确认，间隔 2 秒）；`durable_watermark()`（非观察类事件序号 + 意图/尝试/结果行）识别"声称有进展但无持久变化"的轮次——仍计入轮次上限，但必须睡；等待分支无写入时从轮询间隔翻倍退避到 1 秒。Host 两轮水位相同则用 20 秒长节拍。
- 其他：规划在途不计操作提案/结果审查回合；池冷却延期规划存 `scheduler_state`（重启不丢）；重规划复用同一方法实例具名拒绝 `REPAIR_NOT_ALLOWED`；审阅格式修复反馈逐码写明规则；终审回复不可读的停止写明 `final_review.reason`；执行图终态事件与记录同事务。
- 验证：定向测试（含 §7.3 验收用例 7 个）无新增失败；真机内容任务完成、无卡住误报，后台 CPU 由约 100% 降到约 10%。进度与遗留：`plans/2026-09-27-desktop-next/PLAN-STATUS.md`「第二批 2B」。

最后更新：2026-09-27 CST（保温杯任务真机跑通；收尾按上限计入、拆步打回；复杂编排跑通）。

**收尾与拆步两项用户决定（2026-09-26～27，SDK opt.26～opt.32）**：
- **审阅也只看这一步负责的要求**（opt.32）：只改执行侧后，第一步只写了自己的文件，但审阅仍按 5 份文件审——桌面步骤类型声明“覆盖全部根要求”，编译每步完成范围时把这份声明并进了审阅清单，其余 4 份判“未知”，结论永远是“无法判定”，同一步验证失败 6 次任务失败。现在被方法链接到要求的步骤只按链接审（`planning/htn/completion_scopes.py`），5 条根要求留在顶层总任务上最后统一审。测试 `operation_completion/test_completion_scope_compiler.py` 新增 1 个。
- **重启后遗留名额释放**（opt.31）：退出时仍在线路上的请求，重启后所属运行已不在当前进程，却一直占着唯一的模型名额，新任务第一个请求排队到超时、任务直接失败。恢复检查里把这种请求标为“线路已断”：释放名额，费用仍按未知保留（`runtime/provider_budget_guard.py` 的 `_owner_gone`）。测试 `p35/test_provider_budget_guard.py` 新增 1 个。
- **方法合成回合没拿到回复时重问一次**（opt.31）：以前请求失败/超时当场判任务失败；现在与“回复读不懂”一样重问一次。测试 `test_synthesis_rejection_reask.py` 新增 1 个。线路在 01:14～01:40 间歇故障时两次都失败，排查中曾误判为思考模式所致，用户追问后对照实验证实同一请求开思考也能成功。
- **真机结果**：保温杯任务（1 份参考资料、5 份交付物）26 分钟完成，5 步各一次通过、无验证失败，每步只写自己的文件；一笔未知用量按上限计入后收尾完成（`MissionCompleted`、`AssuranceMissionFinalized`），结算约 162 万 token；预算按渠道与按周合计均为 8 万元，与资料一致。编排回归 5483 通过，74 个失败在改动前版本同样失败。
- **每一步只带自己的要求**（opt.30）：真机保温杯任务拆成 5 步，但每一步的任务都带着全部 5 个 file: 检查和整个任务目标，第 1 步只好把 5 份文件全写了（拆步形同虚设，后面每步还得重写一遍）。现在生成步骤时读取方法里“哪条要求由哪一步负责”（`accepted_outputs.owned_criteria`，与审阅用的对应关系同源），被链接到要求的步骤只带自己的要求和文件检查，目标开头写明“本步骤只负责：…；其他文件由其他步骤负责，不要创建或改写”，整个任务目标放在后面供理解上下文（`occurrence_tasks.scoped_goal`）；没有链接任何要求的步骤保持原样。测试 `test_leaf_owns_linked_criteria.py`；编排回归 5479 通过，75 个失败在改动前版本上同样失败。
- **未知用量按上限计入，任务能收尾**（opt.28/29）：内容全部通过、只剩已终止尝试的“用量未知”预留时，收尾判定（`AssuranceCloseoutConsumer._upper_bound_plan`）不再永远停在“清算中”，而是由 `BudgetLedger.settle_at_upper_bound` 按“预留额与已知用量取较大者”结清（只多算不少算，用量事实本身仍记为未知），逐笔记 `ReservationCountedAtUpperBound` 后再写最终状态。任务在新代码下恢复时（`PolicyInterpreterDrift`）会重算一次收尾，旧版本留下的卡住任务也能结束。真机：保温杯任务卡在清算中的 57,024 token 按上限计入后 `MissionCompleted`。测试 `tests/orchestrator/full_target/test_unknown_usage_upper_bound.py`。
- **拆分过粗打回一次**（opt.27/28）：方法合成提示词 v9（`METHOD_SYNTHESIZER_VERSION`，v8 保留）写明“依赖只表示先后，不是合并理由；每个 file: 条件只由一个步骤产出”。仍有一步承担 3 个及以上 file: 条件时，第一次回复被打回并写明原因（`hierarchical_dispatch._coarse_file_steps`）；每轮最多问两次，所以只在第一次回复检查，第二次原样接受，绝不因此让任务失败。测试 `test_synthesis_granularity.py`。真机：保温杯任务（5 份文件）拆成 5 步。
- **已下发尝试的结果未知时有上限**（opt.26）：保证通道里一次执行尝试卡在“服务商结果未知”超过 180 秒，就把尝试记为丢失、预留保持占用（交给上面的按上限计入），释放后重新派发，不再无限等待。测试 `assurance_exec/test_assured_planner_unknown_bounded.py`。

**一个通用任务 + 可选参考资料**（用户决定 2026-09-26，SDK opt.25，后续由主 Agent 调用）：通用任务配置升到 code-v1 第 5 版（`governance/domains.py` 的 `CODE_PROFILE`），开放 `sources/` 资料目录并允许 `source` 证据；资料走与文档研究相同的机制（`_active_source_binding` 按尝试冻结版本 → `_source_files` 挂进执行者工作区 → `_protected_files` 禁止改写），但不带文档研究的严格引用检查。第 1～4 版原样保留（已有任务冻结的配置不变），无人机仿真配置改派生自第 4 版。调用入口：`mission_create_with_sources`，`mission` 里不写 `domain` 即通用任务，写 `doc-research-v1` 即严格引用模式。资料目录开放后，发布目录与证据存储重叠的安全检查对通用任务同样生效。测试 `tests/orchestrator/p33/test_general_mission_sources.py`；真机：附一份周会纪要的通用任务约 3 分钟完成，待办表与纪要逐条一致。

**纯内容完成要求自动确认**（用户决定 2026-09-26，SDK opt.24）：auto 权限模式下，`OrchestrationService._auto_confirm_content_completion` 每轮循环检查状态为 CREATED 的任务；成功条件里没有 `action:` 且完成要求仍待确认时，按页面同样的“全部必需判据归为内容”映射提交，`approval_source=HOST_AUTO_PERMISSION`，事件记为系统（`host:auto-permission-completion`，写明代谁确认），回执不变；SDK 拒绝 Host 自动确认任何带操作效果的映射。有操作要求或 manual 模式仍保留“确认上述完成要求”按钮。测试：SDK `operation_completion/test_completion_spec_approval.py` 新增 3 个，Host `tests/orchestration/test_auto_confirm_content_completion.py`。真机：重启后停在“已创建”的任务数秒内自动确认并进入规划。

当前 SDK `0.13.0.dev20260925+opt.32`，Host 钉版提交 `5faa739f`。

**结果**：真机用 7 步的“读书会首期筹备方案”任务（6 个交付文件，带依赖）做验证。前七趟各暴露一个新缺陷，逐个修复后，第八趟从规划走到 `MissionCompleted`（`verification_passed`）：
- 用时约 31 分钟，花费约 230 万 token；
- 6 步都一次做成，审阅员格式错误 5 次，全部在重试后恢复。

记录在 `.local-test-evidence/2026-09-26/live-view/bookclub*-watch.log`。

**今天的修复（SDK opt.16～opt.23，每处都带“修前失败、修后通过”的测试）**

1. **编排循环空转**（opt.16/opt.17）
   - 现象：任务在等人确认完成要求时，`_start_planning` 什么也没做却报“有进展”，循环不休眠，后台 CPU 100%，每秒写 18 条时钟回执，启动要 2 分钟。
   - 修法：该函数如实返回是否真的开始了规划。另加 `commit_receipts(kind)` 索引（迁移 28）。
   - 测试：`test_idle_cycle_does_not_spin.py`。
2. **方法合成提示词 v8**（opt.17）
   - 多个独立交付物各成一步，步骤名可读，写明依赖顺序，汇总放最后；简单目标仍拆 1～2 步。
   - v7 保留注册。
3. **规划次数按每个问题单独计数**（opt.18）
   - 修法：`event_handler._planning_attempts` 只数上一次提交决定之后被拒的次数，原先是整个任务累计，默认只有 2 次。
   - 兜底：任务额度、整体终审修复上限、卡死检查。
   - 测试：`test_planning_bound_per_round.py`。
4. **规划器回复的枚举值不区分大小写**（opt.18）
   - 修法：`planning_decisions.py` 里的本地 `enum_of`，`"high"` 按 `HIGH` 接收，规范化哈希不变。
5. **工具参数 JSON 坏了自动重发**（opt.18）
   - 触发条件：`finish_reason=tool_calls`，但工具参数不是合法 JSON（DeepSeek 约每 100 次工具调用出现 1 次）。
   - 修法：`simple_harness/agents/execution.py` 用同样的输出上限原样重发，与截断重试共用 `empty_response_retries`，默认 2 次。
   - 保证：每次调用都结算记账，坏掉的调用绝不执行。
   - 测试：`test_tool_output_length_recovery.py`、`test_protocol_failure_usage.py`。
6. **审阅员输入清单的解码上限**（opt.18）
   - 修法：`assurance_review_import.py` 两处改用 `MAX_RECORD_BYTES`（8MB）。
   - 原因：280KB 的审阅员对话撞上 256KB 的 JSON 上限，32 次复核后被转成“需人工处理”。
   - 测试：`test_review_import_large_manifest.py`。
7. **空 code_test 的说明文字**（opt.19）
   - 修法：没人点名、也收集不到测试的全目录运行，检查结果写“不适用、视为满足、不作为无法下结论的理由”；判定逻辑不变。
   - 原因：审阅员把原来的“无可证明内容”读成“必过检查什么也没证明”，两次判无法下结论，把一步额度烧光。
8. **卡死检查豁免排队中的审阅工作**（opt.20/opt.22）
   - `_has_pending_assurance_work` 在以下两种情况下，不判“无可派发工作”：
     - 保证审阅队列里还有未完成的工作（已转“需人工处理”的除外）；
     - 审阅结果类事件（`AssuranceReviewClassified`/`FormatRejected`）已经出现，但 REVIEW 读进度还没读到。
   - 周期性的保证事件不算进去，所以真卡死的任务最多约 300 秒后照常判定。
   - 测试：`test_stall_waits_for_assurance_work.py`。
9. **审阅回复可解析但无法导入时重问一次**（opt.21）
   - 适用错误：`UNEXPOSED_EVIDENCE`、`DUPLICATE_CRITERION`、`FINDING_SCOPE`、`MANDATORY_CRITERIA_INVALID`，只限第一次调用。
   - 修法：写 `AssuranceReviewInterpretationRejected` 回执（`classification=INTERPRETATION_INVALID`），走格式修复同一通道发起第二次调用，并在请求里附上具体改法（例如“只引用 complete=true 读过的标签”）。
   - 第二次仍错才终拒；`POLICY_CATALOGUE_MISMATCH` 仍直接终拒。
   - 场景脚本：`scripts/assurance_seams/evidence-tools-seam.py` 的“再错即终拒 / 改正即通过”两种情况。
10. **规划类回复丢弃多余字段**（opt.23，用户决定）
    - 修法：新文件 `planning/unknown_fields.py` 的 `decode_dropping_unknown`，按拒绝信息点名的位置删掉多余字段后重新解码，最多 16 次；位置不明时只删唯一的持有者，否则照旧拒绝。
    - 接入位置：`parse_planning_decision`（包括顶层未知键）、`parse_method_proposal`、`planning_method_proposal.prepare_method`。
    - 不变的部分：系统字段和越权声明仍先在原始回复上拒绝；审阅员回复与执行者声明不走这里，仍严格。冻结的编解码源码（`semantic_base.py`、`contracts/htn.py` 在编解码清单 v2～v5 里登记了哈希）未改动。
    - 测试：`test_planning_replies_drop_unknown_fields.py`；`test_planning_decision_codec.py` 里两条旧断言按新决定改写。

**Host 侧**
- 单步固定额度 `OrchestrationSettings.task_max_tokens` 从 100 万提到 **300 万**（用户决定），任务总上限 2000 万不变。原因：一次保证审阅要 13～27 万 token，再加 29.5 万的审阅预留。

**核验**
- 独立子代理复核第 3～9 项：无阻断级问题。
- 各项定向回归与“不带改动的版本”逐条对比，没有新增失败。已知原有失败：SDK 规划/存储类约 11 个，Host `tests/orchestration` 26 个，退回 opt.17 同样失败。

**遗留（治本项，待用户决定）**
- 规划器不该给文档步骤安排 code_test，或者空检查不该进必过清单。
- 审阅成本高：每读一次证据都重发全部上下文。
- 需要一个“7 步任务 + 故障注入”的快速端到端测试，代替逐趟真机试错。

最后更新：2026-09-25 CST（主流程优化条目 2，SDK `0.13.0.dev20260925+opt.1`）。**规划请求包与解码器对齐**：`orchestrator/planner_views.py` 现在通过 `contracts/planning_decisions.exposed_enablement()` 把内部启用矩阵翻成两个字段——`planning_protocol.enabled_decision_types`（只含 9 个 `PlanningDecisionType` 值）与 `planning_protocol.enabled_repair_kinds`（`RepairKind` 值）；准入侧 `event_handler.py` 用逆函数 `internal_enablement_keys()` 还原成 `REPAIR/<kind>` 内部键，授权行 `planning_lane_grants`、`taskgraph_policy_sources` 等仍用内部键不变。当前包版本单一：`PLANNING_DECISION_PACKAGE_VERSION = 8`、标签 `PLANNING_DECISION_PACKAGE_LABEL = "planner-package-hierarchical-v10"`、提示词由配对表推导 `PLANNING_DECISION_PROMPT_VERSION = planner-hierarchical-v11`（v10 + 一条"decision_type 不含斜杠，子类写 payload.repair_kind"硬规则）；`_planning_decision_package_version` 只认当前标签，`_hierarchical_planner_template` 只剩"当前包→v11 / 无绑定或旧协议→旧提示词"，绑定到 4–7 版包的任务派发时抛 `ContractError("unsupported planning package version")`（用户决定：开发期不兼容旧数据）。修复开关关闭时同时去掉 `REPAIR` 并清空 `enabled_repair_kinds`；格式重问提醒补一句斜杠纠正。测试 `tests/orchestrator/full_target/test_planning_decision_enablement_contract.py`。同批：条目 5 `simple_harness/agents/arp/context/recall.py` 结果页达 `MAX_RESULT_PAGES` 且游标未空时 `_skipped(RECALL_AGGREGATE_LIMIT)`；条目 6 `simple_harness/agents/background_health.py`（`BackgroundHealthBook`，循环 index/draining/recall/tool_probe/reap）接入 `AgentRuntime._index_pump`、`ArpRuntime._tick_after`（三段逐项隔离）、`SessionLifecycleService.drive_draining(on_error=)`，对外 `AgentRuntime.background_health()`。

最后更新：2026-09-24 CST（Assurance 第十段：默认开启 + 主流程跑通）。**保证机制默认开启**：SDK 单一默认选择点 `default_assurance_profile_for_new_mission()` 返回 `AssurancePolicy()`，Host `OrchestrationSettings.assurance_profile` 默认 `"on"`，`"off"` 为显式退出。新增生产环节：Host 以自身已认证 caller，在每轮编排循环后、以及 SDK 审阅准备前（部署端口 `AssuranceDeploymentPorts.check_policy_projector`），为每个冻结完成范围批准由原需求无损推导的检查策略（SDK `lossless_scope_mapping`：语义判据→SEMANTIC、具名检查→精确注册 CheckSpec，推不出就 `CHECK_POLICY_UNRESOLVED`，不猜），并为根范围批准最终审阅用途的策略（`mission_final_scope_id` + 对外接口可选 `purpose`）。保证通道下任务整体判定复述已采纳根决议上的认证等级，不另请未认证评判；根节点完成判断读根决议判据而非旧格式审阅记录。**验证**：Host 生产装配 + 真实模型（Grok Build 通道 `grok-4.6`；DeepSeek 日卡上游当晚只回空占位）run-21 从创建走到 MissionCompleted（verification_passed），收尾 FINALIZED/USABLE；途中 14 个接线缺陷逐局修复（授权键粒度、复核比较有效期、审阅预算编号、披露排序、审阅调用上限等），明细见 SDK `plans/assurance-1.1/BODY-WORK-IN-PROGRESS.md` 第十段、[HANDOFF](../HANDOFF-2026-09-23.md) §5。钉版 `0.13.0.dev20260923+assurance.14`（源 1903fbaf）。**边界**：只跑通 1 局 1 题 1 提供方；12 局真实模型、独立审阅、原生点击、Host 26 个既有失败迁移、证书签发即判 SOURCE_CHANGED 的读集粒度未做。

最后更新：2026-09-23。跨电脑源码交付：完整 HTN + TaskGraph23 + Assurance WIP SDK 位于 `sdk/simple-harness-sdk/`；最终 TaskGraph Host UI2 源码位于 `development/taskgraph-host-overlay/`，尚未覆盖根 Host。根 Host 仍固定 HTN wheel。Assurance 主体、UseCertificate/acceptance/终态/生产装配未完成；仅局部 seam，不是整体产品 PASS。实际交接见 [HANDOFF](../HANDOFF-2026-09-23.md)，下文保留历史检查点。

<!-- v14-final-integration-current -->
最后更新：2026-09-22 CST。V1.4（去除NanoJev）本阶段核心最终集成与原生完整效果闭环 PASS，TaskGraph接线资料 READY。Host已安装 `0.13.0.dev20260922+htn.1`（wheel SHA-256 `af9e273061ceeeb3204ccbb4e4d32965fb3bba568e6c7e63f1af1b18244b9800`），528包内文件逐字节一致。新Mission默认hierarchical/独立world；Mission与根合同同事务，CompletionSpec确认后才规划。真实Tauri案例 `mission-5bb7c1fef5597956` 完成内容→操作审查→界面审批→ActionExecutor发布→效果验收→根Resolution→Mission COMPLETED：12次DeepSeek调用、98229tokens、0未知、1次发布。冷恢复/只读回放前后1Attempt/8intents/102events/12calls/1action不变。旧回放语义投影仍PARTIAL（23未知事件类型/1未覆盖字段/UI账本未对齐；覆盖字段差异0），不得将此记为全部回放通过。真实模型新请求默认输出16384，上限32768；历史失败保留。SDK候选dirty源码未整体合并main、未release，Host工作树改动保留。H6大批量晋级、H8 576局对比依用户要求移出阶段并停止，原完整门禁历史保持OPEN。后文旧状态仅为历史。 [Delivery and evidence](/Users/denny/projects/simple_harness/plans/taskSys2/升级planV1/v1.4/最终集成与端到端交付-2026-09-22.md).
<!-- /v14-final-integration-current -->

最后更新：2026-09-22 18:33 CST。本阶段按用户新范围仅做最终集成与必要端到端验收；H6大批量候选晋级评测、H8四方案576局对比评测移出本阶段，功能与历史证据保留，不记为通过。H6 cohort-5在模型调用均已结算的边界停止，PID82450已退出：7个baseline已有成功回执，当前未完成案例不计PASS，停止前0未知用量；新版H8未启动，自动继续已取消。原H8为40 PASS/1 FAIL/1 INTERRUPTED。V1.4仍IN_PROGRESS，最终Host集成与完整实际操作闭环待验收；未合并、替换Host wheel或通知TaskGraph ready。

最后更新：2026-09-22 18:07 CST。V1.4（去除NanoJev）IN_PROGRESS。主体H1–H8接线已实现，正在处理真实验收故障；未合并、替换Host wheel或通知TaskGraph ready。非流式manifest-18 H8停于40 PASS/1 FAIL/1 INTERRUPTED（HTTP524，125.96秒，1未知），534局未启动。H6 source-10真实COMPLETED/oracle=true，48calls/218041tokens/0unknown；cohort-4首baseline第25调用HTTP502（1.32秒）后停止，110908已知tokens/1未知，未晋级。已实现单次SSE传输、完整工具参数组装、断流拒绝与已知用量保留；真实文本/工具两探针通过，30项适配定点、3项计量/身份、22项旧Provider兼容通过。当前stream=true已冻结manifest-19；source-11真实COMPLETED/oracle=true，44calls/199597tokens/0unknown，唯一候选已产生，cohort-5的50局配对验证已自动开始；未宣称解决上游502或验证全部长请求稳定。UI07在manifest-18源码快照真实确认CONTENT_HASH_VERIFIED效果要求，唯一审批事件、0模型调用/0发布文件；不是完整效果执行。完整H6/H8、原14局、独立核验、最终Host集成仍开放。

2026-09-22 Host 完成要求/操作请求补齐丢响应恢复：30 秒超时解除等待，保留原 command_id/idempotency_key；新请求忽略旧响应，父组件更新回调不会丢失在途请求。OperationWorkspace 单文件 4 PASS（65ms），覆盖精确重试/迟到响应；当前原生 UI 后继验证尚待，不代表 Host 或 V1.4 整体完成。证据 `.local-test-evidence/2026-09-22/v14-host/operation-lost-reply/junit.xml`，SHA-256 `34c2d5879ae6bdf3c2154cdd5c84147b513f3d801a938bd4c4ea43c09446b495`。

最后更新：2026-09-22 CST。V1.4（去除 NanoJev）仍 IN_PROGRESS。真实 H6 source-6 COMPLETED、独立 code oracle=true，48次 DeepSeek v4.1 Flash 调用/228605 tokens/0 unknown，正式生成1个候选。Selection合成已分离 DATA 与候选材料来源，精确联合校验实际挂载；两个真实 T0 intent/review/materialization 同target精确隔离，2项定点PASS/0.43s；overlay跨Mission/Task错链拒绝3 PASS/1.69s；O04/I08按权威合同3 PASS/1.05s（专用caller-tenant reader与wheel安装并非这两项必要条件）。I07冷恢复+两项旧package字节golden 3 PASS/0.55s。H1-H静态35 MATCH/1 PARTIAL（I07完整legacy收尾），不是整门35 PASS。H6 cohort-1首个baseline FAILED（26898tokens），公共测试缩进错误已修且新增冻结前AST/隔离校验；原评测集合与失败证据保留，新source-7在独立runtime继续。H6晋级/H8矩阵/最终质量门及Host整体验收仍未完成，未通知TaskGraph ready。

最后更新：2026-09-22 CST。V1.4（去除 NanoJev）仍 IN_PROGRESS。修复真实 H6 source-5 在 4 个 Task 完成后的派发中断：派发器与 completion freeze 共用 DATA-bound producer 的 accepted workspace overlay，保持精确 artifact/hash 校验及 ORDER-only/只读新增测试隔离；定点 3 PASS/1.46s。P06 参数 schema 错误以 typed ParameterBindingsError 归类 STRUCTURE_INVALID，真实在途兄弟/外来 lease 保留及合法替换冷恢复 1 PASS/1.04s。H1-H 静态映射现 32 MATCH/4 PARTIAL，非执行 32 PASS。source-5 27 次调用/119574 tokens/0 unknown，未完成，无 ready receipt；source-6 使用新冻结 manifest-12 继续真实闭环。H6 cohort、H8完整矩阵、剩余 H1 门禁和当前 Host 全链仍开放。未合并/重装 Host wheel，未通知 TaskGraph ready。

Last updated: 2026-09-22 CST. V1.4 excluding NanoJev remains IN_PROGRESS. Real DeepSeek v4.1 Flash code Mission COMPLETED with independent domain success: 27 physical calls, 105645 tokens, 102.551s, zero unknown usage (manifest-6; h8-code-scoped-content-1/probe-receipt.json). This validates scoped TASK_CONTENT Worker/Critic wiring for this scenario, not the full H8 matrix. Prior-source full_target: 4106 PASS / 1 FAIL / 5 SKIP; the outdated drone template fixture subsequently passed its targeted recheck. H1-H extraction from that JUnit: 30 PASS / 6 PARTIAL. Current follow-up fixes cover effect preparation scope and registry eligibility; H6 real cohort, complete H8 matrix, remaining H1 gates and current Host effect UI remain open. Candidate not merged, Host wheel unchanged, TaskGraph readiness notification not sent. See the Host V1.4 acceptance repair checkpoint.

最后更新：2026-09-22 CST。V1.4（去除 NanoJev）整体 IN_PROGRESS。D2原始回执/完整handoff负证明producer、D3延期冷恢复、H8真实进程强杀与AppWorld同episode重接已实现并完成具名局部验证；Host原生内容确认与冷恢复已实点通过（非完整效果Mission）。当前DeepSeek第5次最小聊天恢复200/可见输出/usage，正式Worker复验中；H6 cohort/H8完整矩阵及完整H1门禁仍未关闭。H1-H静态映射30 MATCH/6 PARTIAL不是执行PASS。候选未合并、Host wheel未重装、未通知TaskGraph ready。 [当前证据与边界](../plans/taskSys2/升级planV1/v1.4/验收修复检查点-2026-09-22.md)。下文保留历史时点。

最后更新：2026-09-22 CST。V1.4（去除 NanoJev）主体接线已写入，进入验收，整体 IN_PROGRESS。Operation T0/T1/T3、D3、H2–H8 runtime 和 Host 完成确认/操作提交/规划授权/人工回答入口已接；H6 同库真实 cohort 入口及 H8 四臂冻结配置已补。当前专项 `test_v14_runtime_closure.py` **15 PASS / 0.72s**（首次 14 PASS/1 FAIL 为旧 v8 fixture 断言，与新 v9 默认不符，已修正）；仅覆盖具名15案例，不是 H1–H8 完整门禁。证据位于 SDK 候选 `.local-test-evidence/2026-09-22/v14-closure/{pytest-fixed.log,junit-fixed.xml}`。AppWorld 16条服务规则注册已核对，未计作业务场景PASS。真实单Mission/矩阵与Host原生UI验收继续中；候选未合并、Host wheel未重装，未通知TaskGraph ready。下文为历史检查点，当前状态以本段及Host V1.4主体编码检查点为准。

最后更新：2026-09-22 CST。Operation 补遗继续实施，V1.4（去除 NanoJev）整体未完成。OC-1 Spec 批准与 OC-2 Scope/原子准备事务已接入；MIXED 保持 VERIFYING、禁止自动动作/重开 Worker。上一固定源码 full_target 为 4016 PASS / 8 FAIL / 5 SKIP（146.72s，589 文件 hash 不变）；8 项失败已修并经 231 项定向复验，后继组合相关 235 PASS（3.58s），不能合称全门通过。新增 Selection 准备/回放/回滚 2 PASS，真实非空 DATA 冻结及伪造 mount 拒绝 1 PASS，等待态不误停与内容完整性 10 PASS。完整 nested compound 与中间 local criterion 链正在实测；OC-3 payload/source reader 开始实现，T0/T1/T3 producer、D3、H1-I/完整 H1、H2–H8 收尾及当前 Host 原生 UI 仍待。无新 PlanAgent 待决；保留所有 dirty worktree，未合并/重装 Host/调用真实 Provider。

最后更新：2026-09-21 CST。V1.4（去除 NanoJev）仍未完成。后继修复 operation/action link 同身份重放假冲突（原反例 1 FAIL / 4 PASS，修复相关 28 PASS；旧 action 相邻回归 74 PASS / 1 原有条件 SKIP），并保持重复重放零写与 alias 原子拒绝。新增取消 Task 的真实在途/lease 检查，与原 repair 套件共 8 PASS；两个真实 SQLite 写事务交错与真实方法退役后的 UNKNOWN 读取均已通过（后续组合首轮另有 cycle 夹具失败，已修正）。compiler 拒绝保留 typed report，collector 不再将非四类缺陷归为 COVERAGE_GAP；未知 producer code 强制 INTERNAL_CONTRACT_ERROR。最新相关 181 PASS / 3 既有 codec SKIP（2.00 秒），3 个 preview/collector 源文件 mypy 与定向 Ruff 通过。此前 full_target 3891/5 与 H1-H 20/8/8 是前一源码检查点，尚未重新全量/矩阵汇总。Operation 上游 producer、延期恢复合同、H1-I/完整 H1、H2–H8 与 Host UI 仍待，候选未合并。架构裁定问题见 Host plan 的 PLAN-AGENT-架构裁定请求-2026-09-21.md。

最后更新：2026-09-21 CST。**V1.4（去除 NanoJev）整体未完成。** 候选 `codex/h1h-impl` / HEAD `102ad3dfa2db38d575ea929d39ec5ed1561a71da` 加保留的未提交改动，最新固定源码 full_target **3891 PASS / 5 SKIP / 137.29 秒**，1045 个 Python 源码/测试 hash 前后不变；5 个变更源码文件 mypy 通过。当前实际 H1-H matrix 为 **20 PASS / 8 PARTIAL / 8 NOT_COVERED / 0 FAIL**，exit 2，整门仍 OPEN。已完成本地修复：提交/最终decision原子恢复、UNKNOWN action保留预算、授权issuer/tenant/Mission隔离、原始reply CAS留存、两种固定decode-only先解码后拒绝；补齐A01/A03/I01/I04/P05/P08/P10等真实断言。历史8个旧fixture失败保留，补真实ArtifactStore后25定向及本次全量通过。真实 DeepSeek v4.1 Flash WAIT场景已完成（早于后继raw/授权修复）：190.609秒、40次物理串行调用全succeeded、4次WAIT注册/唤醒、4件accepted artifacts、所有reserved字段0；带测试调度/签发器，不代表Host UI或完整H1-I。Operation上游冻结身份/参数引用/物化链、其余门禁及H2–H8仍待；候选未合并、Host wheel未重装、原生UI未验。当前事实与原始证据索引见Host `plans/taskSys2/升级planV1/v1.4/WAIT复验-2026-09-21.md`，后文旧数字仅为历史。

最后更新：2026-09-21 CST。**当前 V1.4（去除 NanoJev）状态纠正：整体未完成。** SDK 候选 WAIT 固定源码回归 3812 passed / 5 skipped（138.38 秒，545 个源码/测试 hash 不变）；后续 authority/operation 定向 41 passed（0.72 秒）属于更新后的局部源码。H1-H 原“36/36”仅为测试执行数，修正规格映射后为 **5 PASS / 10 PARTIAL / 21 NOT_COVERED**，不能关闭门禁。真实 DeepSeek WAIT 注册→Worker 完成→唤醒 PASS（49.503 秒、9 次物理调用）；同 Mission 恢复完成 4 件 accepted artifacts，但在 240.089 秒/20 次新增调用边界下仍 ACTIVE，最终评审标签拼错被严格拒绝，不能报 H1-I 完成。旧模式回归 559 PASS / 1 timeout FAIL / 13 SKIP；失败文件原样复跑 6 PASS，原因未定，原失败保留。候选未合并、Host wheel 未重装、原生 UI 未验。后文旧检查点保留历史时点，不覆盖本条。详见 Host `plans/taskSys2/升级planV1/v1.4/WAIT复验-2026-09-21.md`。

最后更新：2026-09-15 11:15 CST。A96 已冻结12个dev题，小对照 D-arm smoke 超时失败（未知用量1），96次未启动。[冻结](../plans/taskSys2/testPhase1-a96-freeze-2026-09-15.md)。

最后更新：2026-09-15 10:45 CST。N5 A 轮 Qwen 干净/攻击各一例已在 69d679c 上评分；系统观察晋级，KnowledgeUsed 0。A 轮未完成。[记录](../plans/taskSys2/testPhase1-n5-blackboard-2026-09-15.md)。

最后更新：2026-09-15 08:51 CST。N5 AgentDojo黑板传播：Host 在 Task 接受后把成功官方工具回执投影为系统 VERIFIED `tool_observation`；Blackboard 只读；模型 Claim 仍最多 SUPPORTED。错误回执与伪造 knowledge id 不晋级。Runner Mission 允许 `knowledge_list`/`knowledge_read`。词面不相关时知识在库中但 `verified_knowledge` 包为空。定向 40 PASS / 7.11 秒，0 应用模型调用。[记录](../plans/taskSys2/testPhase1-n5-blackboard-2026-09-15.md)。

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

**最后更新：2026-09-14 20:10 CST — testPhase1后续修复与独立Flash16次回收完成。** 当前SDK功能5406fb5，完整编排2105PASS/20SKIP；256K四题S/R各3/4、D/F各4/4，有效14/16，648调用8760086tokens，runner2234.319秒。0未知用量/网关终态缺失/工具重下发，8个D/F终态预留0；知识复用和动态图收益未得到证明。旧本地9终态/7未执行/1未知保留。用户发现白屏已通过完整源码重启与实际点击恢复，原0残留仅指受管组；当前UI有意保持运行，独立评测服务已结束。 [完整结果、耗时和后续问题](../plans/taskSys2/testPhase1-flash-final-2026-09-14.md)。以下检查点保留原时点范围。

**最后更新：2026-09-14 19:59 CST — 用户报告的源码测试窗口白屏已恢复。** 受管服务退出后另有独立carrier窗口存活、15173/18140无监听；原0残留仅覆盖旧进程组。完整源码launcher恢复后，实际点击任务与REPORT通过；12表/5产物/15旧调用保持、0新增调用。当前UI有意保持运行，未改业务源码。[原因、恢复和收尾修正](../plans/taskSys2/ui-white-recovery-2026-09-14.md)。

**最后更新：2026-09-14 19:32 CST — 当前AppWorld契约v2源码验收通过，完整Flash对照进行中。** SDK5406fb5；最新完整编排2105PASS/20SKIP/0FAIL670.45秒（runner673.922秒），原生v48冷读12表/5产物/15调用核对、0新调用278.158秒，进程均清理。独立Flash400万/120调用校准四Task＋官方评分通过，实际44调用455861tokens/226.874秒；不对参数变更作单变量归因。新四题16次Flash块采用256K/400万/120调用，仍在运行；原本地9终态/7未执行/1未知完整保留，未混入后继得分。[最新证据与限制](../plans/taskSys2/testPhase1-followup-2026-09-14.md)。

**最后更新：2026-09-14 19:11 CST — AppWorld结果契约v2修复进入验收。** 本地后继矩阵9终态/7未执行，1未知调用触发停止；进程清理完成，未知Attempt预留保留。独立Flash探针暴露Worker模板非法schema_version；新AppWorld profile默认v2修复八角色示例，旧v1原样保留。反例8FAIL→新整组143PASS/8.92秒；完整回归与新源码真实复测仍待。不扩大调用预算，不打包，上下文仍本地默认256K，512K只限Flash。[当前结果和边界](../plans/taskSys2/testPhase1-followup-2026-09-14.md)。

**最后更新：2026-09-14 18:33 CST — 上下文测试范围调整。** 用户指定后续仅测试128K/256K/512K，本地默认256K，512K仅使用DeepSeek Flash。正在运行的四题16次复测为本地256K，冻结参数未变；新增档位不提前宣称通过。单次输出和累计Mission预算独立记录。其余功能与验收状态见下一检查点。[当前范围与证据](../plans/taskSys2/testPhase1-followup-2026-09-14.md)。

**最后更新：2026-09-14 17:55 CST — testPhase1三项修复进入真实复测。** AppWorld Mission显式256K池绑定接通现有522240任务预算下限；R改为有界版本化选择且独立valid_success；网关异常/取消补终态审计。定向155PASS/11.88秒；完整编排2096PASS/20SKIP/0FAIL，686.51秒，runner687.033秒，退出无残留；当前源码原生v47冷读通过，12表/15调用精确一致、0新调用，228.444秒；原四题16次本地模型复测进行中，首题S/R有效成功，D后续多轮仍受Task硬上限停止，预算规划效果不宣称全面完成。旧16次失败保持原样；默认256K/物理1，DeepSeek0调用，不打包。 [修复边界、耗时与证据](../plans/taskSys2/testPhase1-followup-2026-09-14.md)。

**最后更新：2026-09-14 16:47 CST — testPhase1测试执行完成，3项后续修复明确保留。** 功能源码全编排2079PASS/20SKIP/0FAIL，879.28秒；原生v45可信知识两Task/真实pytest/独立Critic/人工复核与冷恢复通过，v46复制数据重开通过，0重调。正式四臂16/16完成，282调用3076186tokens，5758.78秒；S有效3/4，R有效2/4（官方终态4/4，另2次自选JSON解析失败），D/F各0/4且均任务级预算停止。D/F无已观察知识复用/动态图收益；全部终态预留0、SDK工具重下发0，保留1工具失败/1拒绝及1网关outcome缺失。剩余Planner预算可行性、R选择协议、异常网关终态3项尚未修复。默认本地256K/物理1，DeepSeek0调用；不打包、不扩大96次。 [完整结果与证据](../plans/taskSys2/testPhase1-results-2026-09-14.md)。

## testPhase1 当前Host链路（2026-09-14）

- `backend/deskpet/orchestration/service.py`部署默认工具门包含knowledge_list/knowledge_read，code_test仍按已有本地执行许可接通；新Mission会冻结该权限，旧Mission不回写扩大权限。
- 跨分支原生夹具以真实system test_observation为可复用依据，下游实际查目录、读原文并引用精确观察ID/工作区hash；用户在源码UI查看代码、测试、报告后批准，两个Task及Mission进入COMPLETED，最终产物VERIFIED。
- 当前源码验证依赖配套SDK editable checkout；旧固定wheel不包含新增实现。AppWorld本轮通过SDK独立评测入口，不是新增Host跨应用业务UI。
- 最新功能源对应SDK84c3235/Host23cb7d37；全编排2079PASS/20SKIP及原生证据各保留自己的快照身份。原效果实验失败保留；预算与R协议的SDK后继修复已通过定向测试，真实复测仍待，不能从UI交付成功外推为编排普遍有效。

**最后更新：2026-09-14 13:15 CST — testPhase1仍在执行。** 新code profile v2默认范围化pytest观察，17项新正负控通过；知识原文分页/精确引用/撤回投影通过离线检查，真实本地中英消费5调用24405tokens/113.63秒通过。AppWorld第三领域及真实保存恢复/独立评分接通；首技术探针预算失败保留，5题校准进行中。S/R实际BaseAgent身份/自选控制及计量离线通过；D/F整体、16episodes、T6、最新原生UI仍待。不覆盖历史Phase3验收，不打包。 [执行证据](../plans/taskSys2/agent-orchestrator-gap-review-testPhase1-2026-09-14.execution.md)。

**Last updated:2026-09-14 CST — local DGX source acceptance PASS.** Default local qwen38-flash-next,262144 shared total/228352 Mission input. Actual260001-input request PASS111.089s. Frozen Host922b2d7b/SDK6d4ddc7 native chat+code Mission PASS:7calls18654tokens, real pytest2PASS/CriticPASS/VERIFIED artifacts; cold rows identical0new calls. Native370.415s/cold72.388s, both clean exit. Source checks SDK105+Host81+foreground44+UI81/typecheckPASS. Prior v42 foreground failure retained; no packaging/full-model-quality claim. [Current evidence](../plans/2026-09-14-local-dgx/README.md). Earlier checkpoints below are historical.

**Last updated:2026-09-14 CST — native-discovered foreground fix.** First native-v42 chat failed before HTTP because the foreground adapter had not opted into configured LAN HTTP. Product adapter now opts in for registered endpoints; public/DNS/link-local plaintext remains rejected.44 focused checks PASS4.29s. Actual260001-token request passed111.089s with three correct markers. Native successor pending; original failure retained.

**Last updated: 2026-09-14 CST — local DGX connection checkpoint.** Source local256K total /223K input profile, exact offline HF template accounting and explicit private-LAN HTTP are implemented. SDK105PASS5.92s, Host81PASS18.33s, UI81PASS1.40s/typecheckPASS; actual short/tool/continuation counts match server. Near-window and native acceptance pending. [Current local scope](../plans/2026-09-14-local-dgx/README.md). Historical Phase3 evidence below keeps its original scope.

**Last updated: 2026-09-14 CST. Current Phase3 source acceptance:46 SOURCE PASS /0 OPEN /2 user-deferred packaging criteria.** P3.4-A04 now passes one fixed real deepseek-flash strict-profile FIRST/COMPARE pair on SDK ae8d37b, snapshot-v41:188calls1918557tokens,1029.73seconds; both strictPASS/COMPLETED, zero physical errors/unknown usage/reserve/rehandoff and zero residual test processes. Committed affected158PASS6.76seconds. [Current evidence and boundaries](../plans/2026-09-12-phase3-host-g/v15-real-pair-review.md). Host production18ff5d24 and prior native-v39/earlier evidence retain their own source scope; strict mode is explicit SDK configuration, not an automatic Host redirect or new native UI acceptance. One pair does not prove model-quality superiority. No packaging/installer/release/push; historical failed pairs retained.

**Historical checkpoints below retain their original dates and evidence boundaries; their open lists do not override the current status above.**

**Last updated: 2026-09-14 CST.** Host native-v39 functionality retains its tested18ff5d24 source and307PASS cumulative scope. SDK real pair v13: COMPARE strictPASS; FIRST delivered with one retained physical tool-parse error, so original48AC remains45SOURCE PASS/1OPEN/2packaging DEFERRED. Total157calls1543084tokens880.20s/zero residual test processes. SDK diagnostics-only successor67PASS1.90s is not a new Host/native acceptance claim. [Current evidence](../plans/2026-09-12-phase3-host-g/v13-real-pair-review.md). Earlier entries below are historical.

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

**Last updated: 2026-09-13 09:15 CST.**


## 2026-09-13 09:15 CST — Source synthesis checkpoint

Host doc8 compatibility plus controlled search and launcher regression: 34 PASS/12.33s (g-native-search-doc8-host-v4; wrapper12.83s). Synthesis form UI101 PASS/1.47s and typecheck remain unchanged. Native P34 and real N1 doc8 quality recheck are pending. This commits the already reviewed source feature/wiring; new native load files remain a separate unverified slice. SDK1f0c536 preserves legacy runtime controls; full regression has not yet passed after the three old-test assumption repairs (focused16PASS9.33s). No packaging, P36, release or push.

**Last updated: 2026-09-13 08:44 CST.**

Source-native checkpoint, 2026-09-13 08:44 CST. Optional final independent synthesis is now exposed in Mission creation with explicit goal, criteria and bounded token/attempt budgets. Code/document requests use the same public schema; failed retries keep identity until content changes; success clears synthesis fields. UI101 PASS/1.47s and typecheck PASS (g-ui-synthesis-v2). Controlled P34 Host/default-policy fixture preserves two failed A Attempts, independently verifies F, keeps B, retargets C, executes C and final S probes, and cold-reopens without Provider replay: 33 PASS/8.04s (g-native-search-host-v2). Independent Terra review found no P1/P2. These are software results; source-native P34/synthesis UI is still pending.

**Last updated: 2026-09-13 08:24 CST - source-native slot binding.**
**最后更新：2026-09-13 08:12 CST — doc7 原生仲裁与冷重开。** SDK aaa3593 / Host 648ad185 的源码快照v13，经真实表单导入两来源，240000/6总预算与30000冲突预留实际生效；两Worker/Arbiter/独立Critic后UI进入待仲裁，展开两份完整原句、通过UI提交contextual并打开实际245B仲裁报告。审批GRANTED、Conflict RESOLVED_BY_HUMAN；两原主张保持DISPUTED、0知识条目，Mission按claim_not_usable成为mission_criteria_unmet（不是交付成功），2850已结算/0预留。新建表单预留为空，原生复验了跨任务残留修复。相同源码/数据冷重开后原身份与状态保持，Provider19/19、0rehandoff、12工具效果不变。原始证据Host `.local-test-evidence/2026-09-13/p33-g/source-ui-arbitration-v13/case-summary.json` SHA256 `5c7946166a2593bdafd0edfb5f92a53bdf1400fec9d652c2297d232c41a9a967`。初始/冷进程组均退出0无残留，539.712s/43.737s为包含人工操作等待的载体生命周期，不是模型运行时间。此为受控原生仲裁边界通过，不是真实模型能力或Phase3整体完成。

**最后更新：2026-09-13 07:35 CST — 冲突核对预算表单。** 新建 Mission 可显式从总 Token 预算中预留冲突核对额度；留空不发送预留字段，非法值、负数、小数及超过总额会阻止提交。文档原子创建和重试保持该值，创建成功后新表单清空。Luna 独立审查发现并复验通过跨任务残留问题；主线程前端 85 PASS/1.33s、typecheck PASS。原始证据 Host `.local-test-evidence/2026-09-13/p33-g/g-ui-conflict-reserve-{green,typecheck}-v4.log`。源码启动器已登记该仲裁用例，20 PASS/0.11s（g-launcher-arbitration-v1）；当前源码原生仲裁表单仍待验，P33/P34/P35 整体 OPEN。

**最后更新：2026-09-13 07:09 CST — 已有源码原生运行全量回放。** 对16个已关闭原生运行库的全部17个Mission（含失败运行）及16个部署事件流执行只读回放，33个观察全部PASS、0差异/未覆盖/发现/读取错误，耗时0.532s；Provider调用、工具效果、Action、Context selection和事件计数前后相同。原始索引Host `.local-test-evidence/2026-09-13/p33-g/replay-all-native-v1.json`，SHA256 `376f19c68746cee73c8f186fb8b66e4aa099e3f2cb944718d2db34013b15cce5`。仅证明已存在原生运行的编排回放；执行库为清单枚举而非完整SDK执行回放，新后继UI与最终全测试观察仍待完成。P33/P34/P35整体OPEN。

**最后更新：2026-09-13 07:06 CST — 当前 doc7 正式仲裁受控链路。** 隔离 document-ui 入口新增 contextual arbitration 场景：两个实际 Worker 读取带环境限定的来源、提交有引用支持的非逐字世界候选，经正式冲突检测进入DISPUTED；实际Arbiter读取两来源及两产物、独立Critic读取仲裁报告，公开人工contextual裁决后冷重开保留原裁决且零新模型调用。14 PASS/25.55s（runner26.05s），包含真实测试路由、来源场景和隔离门；证据SDK `.local-test-evidence/2026-09-12/p33-g/g-host-doc7-route-v5.{json,log}`。首轮引用归属/世界主张混淆和后继状态断言失败保留。doc7新增Manager模板，历史doc6仍可读。此项是受控Provider软件证据，当前源码原生仲裁UI仍待验；P33/P34/P35整体OPEN，不打包/P36/推送。

**最后更新：2026-09-13 06:37 CST — 启动失败生命周期。** SDK enter失败会确定性关闭已装配runtime与Store，并保留原WorkspaceCleanupIncomplete和UNKNOWN占用；Host rebuild候选仅在enter及facade成功后发布，失败关闭候选且后续完整重试，不能直接run半初始化对象。SDK3 PASS/.32s；Host新控制4 PASS/.19s，连同生命周期/并发回归16 PASS/12.11s。首次失败恢复仍须deactivate/activate创建新service/client，不复用已关闭HTTP client；没有新增首次启动自动重试。命令与原始证据在SDK `.local-test-evidence/2026-09-12/p33-g/g-startup-failure-lifecycle-v1` / `g-host-lifecycle-affected-v2`。

**最后更新：2026-09-13 06:32 CST — 拒绝原因原生复验通过。** 源码SDK b0f8dd7 / Host d9d56461、快照v12，原生创建document-v6任务后先批准来源撤销，再批准原报告：界面明确显示结果最终未接受（FAIL/DONE）、stale_source、revoked及原版本，Claim仍UNDER_REVIEW；原layer和人审PASS历史保留。Mission mission-aa32b2525a480041，唯一Attempt，0 VERIFIED，Provider前后6/6/0，900已结算/0预留。证据 Host `.local-test-evidence/2026-09-13/p33-g/source-ui-n6-active-revoke-v12/case-summary.json` SHA-256 `964c37865575bd3d2762247b6ff58c2cfda40082873a5e51bc4740f01f771efa`。PG78917已退出/无残留。该受控原生结果不代表真实模型质量或其他Phase3门槛关闭。

**最后更新：2026-09-13 06:26 CST — 最终拒绝原因投影。** Task结果现在直接显示持久的Result verdict/state；对于DONE/FAIL，仅从完全相同的Attempt/Task/Mission投影白名单拒绝原因与来源版本，保留先前layer通过记录和UNDER_REVIEW Claim，不从人工GRANTED推导结果接受。后端46 PASS/8.36s（SDK c8e2541隔离源码环境；旧backend venv产生1项预算usage兼容失败并保留），前端79 PASS/1.38s，typecheck PASS。新源码快照原生验证待完成；此前v11撤销runtime负例保持有效，新的显示修复尚非原生PASS。

**最后更新：2026-09-13 06:15 CST — FIRST 与 N6 新证据。** SDK b0f8dd7 / Host c6beb926 源码快照 v11 原生 N6 已验证：1/2 不确定条件允许交付且明确保留不确定性；2/3 不确定条件任务PASS但Mission为 insufficient_evidence；active-revoke先批准撤销来源、再批准原报告，原结果DONE/FAIL（stale_source）、产物REJECTED、0 VERIFIED、唯一Attempt、Provider仍6次，900已结算/0预留。三例为受控原生UI，不代表真实模型质量；分别证据 `source-ui-n6-half-v11`、`source-ui-n6-two-thirds-v11`、`source-ui-n6-active-revoke-v11` 位于 Host `.local-test-evidence/2026-09-13/p33-g/`。原生最终拒绝原因未在Task验证列表直接展示的问题仍在修复。FIRST定向56 PASS仅SDK工作树，尚未纳入该UI快照；文档冲突仲裁UI、O4全量及P34/P35整体仍OPEN。

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

# Agent 编排（任务编排视图）· 生产事实

- 最后更新：2026-09-12 23:05 CST

当前决定：用户批准直接运行源码Tauri UI完成P3.1–P3.5功能验收，暂停安装包构建与发布，不含P3.6。冻结环境探针新增明确不可执行原因，避免把后端应用当Python解释器；四项控制通过及独立限定ACCEPT，源码模式仍执行真实探针。源码开发启动已到orchestration_ready/available；共用日志过滤补URL query凭据脱敏，26项控制通过；真实UI任务尚待。此前安装包失败保留，下面为历史定位过程。

P3.3 G进行中：Host已接入原子文档创建、来源版本审批、绑定引用全文读取与系统结论展示。候选SDK0.11.1（源码a5c8fca，wheel49137655…）已钉版并安装，Service SDK保持原定0.3.13；本机旧venv的Harness0.7.2/Service0.3.12已对齐。Host实际安装组合定向49项、前端86项组件测试/typecheck通过。干净Host c3d4e227的macOS PyInstaller与Tauri构建成功，浏览器原件/许可证及构建身份核验通过；但原生首次启动因SDK公开延迟导入的workspace_binding_protocol未入包而失败，尚未发出本次真实flash请求。公开延迟导入已修复并在第二版真实启动越过；第二次lifespan因工作流handler源码未随包导致稳定manifest编译失败，源码收集修复的3项控制通过，仍待原生复验。第三次真实启动已越过workflow源码检查，但缺Tool目录JSON；资源全集修复14项控制通过，待第四版原生复验。不宣称P3.3交付。当前失败与后续修复见[Host G journal](../plans/2026-09-12-phase3-host-g/journal.md)。
- 计划与记录：`plans/2026-09-11-orchestrator-host-integration/`（plan 第 3 版、acceptance、journal）
- 方向依据：用户的 Phase3 计划 `plans/taskSys2/agent-orchestrator-phase3-plan.zh-CN.md`。本模块是其中 **P3.1 真实 App Mission 控制闭环** 的 Host 直连实现。
- SDK：`simple-harness-sdk` 的 `agent_orchestrator`（与 `simple_harness` 同在一个 wheel 里）。Host 钉版以 `backend/deskpet/sdk_adapters/sdk_candidate.py` 为唯一来源。

> 状态（2026-09-12）：P3.1 Host 直连路径已交付。
> - 自动化：`tests/orchestration` 107 passed，vitest 772 passed。
> - 真实 deepseek-flash 运行：HA-11 通过。
> - 原生 App 验收：HA-12 ①–⑥ 全部通过，用的是 verify bundle `f51ddc37`（debug .app 加源码后端），报告见计划目录的 `reports/native-ui-run1.md`。
> - 冻结打包的安装包没有验证（PyInstaller spec 仍停在 0.6.4），HA-22 ① 的 WebView 刷新也没有做原生验收，两者都列为遗留。

## 1. 装配位置

| 层 | 位置 | 职责 |
|---|---|---|
| 启动 | `backend/main.py` lifespan，紧跟 `_activate_product_sdk_runtime()` | 调 `deskpet.orchestration.wiring.activate_orchestration`。失败只让编排服务不可用，并记入 `startup_errors`；"未配置模型"不算故障。任何情况下都不会让后端启动失败 |
| 关停 | `backend/main.py` lifespan 关停段，在 SDK stack 之前 | 调 `deactivate_orchestration`。这只是有序关停路径：App 真实退出时后端收到的是 SIGKILL，编排的正确性不依赖这一步 |
| 服务 | `deskpet/orchestration/service.py` 的 `OrchestrationService` | 持有目录锁、provider 快照、`Orchestrator` 实例与驱动循环、SDK facade、部署清单，并做 Host 门口检查。<br>驱动循环失败时按指数退避，时长不超过 `backoff_max_seconds`；指数本身也有上限，失败次数再多也不会溢出。连续 3 次失败就重建运行时，连续 5 次标为 degraded；重建本身失败时循环不退出，只标 degraded 并写明原因。<br>degraded 时，读取、取消、审批决定、接管、评论照常可用，只拒绝新建 Mission（`orchestration_degraded`） |
| 协议 | `deskpet/orchestration/handlers.py`，`/ws/control` 中 `mission_*` / `orchestration_*` 消息 | 纯分发函数，响应格式为 `<type>_response {request_id, ok, data ｜ error_code, error}`，异常不会抛进 socket 循环。payload 不是对象时回 `invalid_request`，不会断开主对话共用的控制通道 |
| 推送 | `deskpet/orchestration/pump.py` 的 `MissionChangePump` | 用只读连接每秒查看各 Mission 的状态和最大 seq，每次写操作后也立即查一次；有变化就广播 `mission_changed`，并带上新事件（2026-09-26：`from_seq`/`events`/`truncated`，每次最多 50 条，字段同 `project_event`；进程首次看到的任务只标 `truncated`；查事件失败下一轮重试） |
| 运行视图 | `deskpet/orchestration/live_graph.py` | `mission_live_graph`（结构来自 `plan_memberships`→`method_instances.goal_occurrence_id`、顺序/数据依赖、`tasks.status`、最新 `CompoundPhaseChanged` 阶段）与 `mission_planning_decisions`；先校验归属再只读打开库 |
| 投影 | `deskpet/orchestration/projection.py` | 按白名单输出，长度有上限；动作参数超过 600 字符时，只给截断后的预览。<br>模型写的文本都标注 `source: "model"`：结果摘要与 claims、Task 目标、critic_review 摘要、审批摘要、动作理由。<br>事件只给 seq、type、时间、Task / Attempt id 与 actor_type，payload 一律不外传；唯一例外是评论事件，带评论文字。<br>产物只给工作区相对路径和 hash，不给 `storage_uri`；不暴露 intents。<br>`ui_state` 按 P3.1 的状态词汇统一推导（请求已接收／排队／运行／待验证／待人／UNKNOWN／正式交付，另有失败、已取消），列表与详情共用同一套 |
| 前端 | `tauri-app/src/views/MissionsView.tsx`、`stores/missionsStore.ts`、`stores/useMissionsFeed.ts`、`components/Sidebar.tsx`（入口"任务编排"，角标显示待审批数）、`components/WorkbenchShell.tsx`（视图 `missions`） | 界面只做投影，所有改动都走控制通道命令。<br>• 常驻订阅：`useMissionsFeed` 挂在 App 上，与视图是否打开无关。启动时拉取 status 和列表；收到 `mission_changed` 就更新 store，并节流重拉列表（最多 1 次/秒），所以侧栏角标和列表的状态词会实时变化。<br>• 事件游标：推送看到的 seq（`lastSeq`，只用来防倒退）与已加载事件的游标（`eventCursor`）分开保存，拉事件一律从游标往后。推送的 seq 超过游标时，连续分页补拉，每页 200 条，一次最多 20 页，超出后显示"加载更多事件"。时间线只显示最近 50 条。<br>• 状态词统一用后端的 `ui_state`。<br>• 详情里还有：产物（"查看产物"，按 id 读取并核对 hash；二进制和截断都会注明）、评论（"评论"、"发表评论"，评论对象是 Mission）、策略漂移提示、等待原因（人工复核、动作审批、仲裁，附开始时间）。<br>• 模型写的文字标注"模型生成，未核实" |
| 服务登记 | `backend/context.py` 的 `_VALID_SERVICES`：`orchestration`、`orchestration_pump` | — |

## 2. 数据

- 正式目录：`<user_data>/data/agent-orchestrator/`，内含：
  - `orchestrator.db`：编排库，只有 SDK 的 Commit Service 写；
  - `execution.db`：编排自己的 SDK 执行库，与主对话的 `execution-v6.sqlite3` 分开；
  - `workspaces/`；
  - `deployment-manifest.json`；
  - `.instance.lock`。
- 测试场景目录：`<user_data>/data/agent-orchestrator-test/`（见 §6），正式库一行不写。
- 两个目录都不能是软链，也不能解析到 user_data 之外。
- **单实例**：编排目录上有 `flock`。第二个进程拿不到锁，就标为 unavailable，不派发任何任务。持锁进程被 SIGKILL 后，锁由内核释放。
- **owner**：每个进程各自一个，形如 `deskpet-orchestrator-<pid>-<随机8位>`。不能共用：SDK 会把同一 owner 名下的有效租约都当成自己的。

## 3. 部署政策（固定）

| 项 | 值 | 理由 |
|---|---|---|
| `code_execution` | 由启动时的探针决定：`sandboxed` 或 `off` | P3.2 §4.3：问题不再是"允不允许执行"，而是"隔离在这台机器上证明过没有"。每次启动跑 SDK 的能力探针（8 项：读家目录、读他人临时目录、写越界、联网、向宿主发信号、完整 daemonize 的回收、输出截断、CPU 上限）；**全过才是 `sandboxed`**，否则 `off`。Host **永不使用 `process_only`**——不隔离的子进程只适合可信代码，而模型写的代码不是。<br>`off` 时（与 P3.1 相同）：`code_test` 不是已部署层；`pytest:` 条件在入口被拒；旧 Task 的 `pytest:` 由规则层判 FAIL；冲突转 DEFERRED；`run_tests` 被拒。<br>`sandboxed` 时：这些重新开放，`run_tests` 进入 `allowed_tools`，模型写的代码只在一次性副本里、经 seatbelt 运行。探针报告写进部署清单与 `status()` |
| `allowed_tools` | 三个工作区工具 | 编排 Agent 拿不到 Host 的任何工具（shell、MCP、文件系统、浏览器都没有） |
| `enabled_connectors` | 默认空；授权发布目录后含 `file_publish`；`test_config` 仅测试场景 | P3.2 P32-14：只有用户在配置里指明了发布目录、该目录存在、**且能承载硬链接**（连接器唯一的原子提交点）时，`file_publish` 才启用。三者缺一就不启用，于是 Mission 连带 `action:file_publish…` 的成功条件都不能提交——拒绝发生在门口，而不是等模型产出候选之后。不支持硬链接的卷（exFAT、部分网络盘）在授权时就被挡下，不会变成运行期的 UNKNOWN |
| `max_action_level` | L2 | 单机只有一个人，满足不了 L3 要求的两个不同的人 |
| 本机执行测试的开关 | 不提供，也不会有 | P3.2 用"隔离是否证明过"取代了"允不允许"。配置里没有任何开关能打开它；决定权在探针，结果在部署清单里可查 |

## 4. 身份与授权

- **Principal**：`local-user:<companion 本机身份 profile_id>`，显示为"本机用户"。单机没有认证，身份就等于能操作这台 App 的人。这一点登记为限制。
- **与 Host 的 auto / manual 模式分开**：auto / manual 只管主对话里的工具效果。编排的人工审批（原文 §22）必须由人来决定：auto 不会自动批准，manual 也不会弹出主对话的授权窗。
- **tenant**：固定为 `local-desktop`。SDK facade 按 tenant 检查每个对象的归属：不属于调用方的对象和根本不存在的对象，一律返回同一句 `not_found`。
- **密钥门口**：下列内容会同时按通用密钥模式和当前 provider 的密钥检查，命中就回 `secret_rejected`，一个字都不写进编排库。provider 密钥即使不是 `sk-` 形态也能拦下。
  - 新建请求里的每一个字符串：目标、成功条件、`stop_conditions`、`synthesis`、`workspace_seed`，连键名也查；
  - 审批的理由和备注、接管依据、评论。
- **seq 是全库自增**：一个编排库只有一个 tenant 时没有影响；多 tenant 共用一个库时，跳号会暴露其他 tenant 的事件量。

## 5. 恢复语义（App 退出 = SIGKILL）

| 被杀的时点 | 新进程里发生什么 | 界面 |
|---|---|---|
| 等人工复核（所有回合都已提交） | 旧 owner 的租约过期（`lease_seconds`）后，新 owner 继续验证**同一个** Attempt，不重做已提交的工作（P3.1-A07） | 复核通过后几秒内进入正式交付 |
| 模型调用中途 | SDK 回合停在 running；心跳里的 `liveness.blocked` 变为 true（`kind: provider`） | 详情里显示"回合结果未知"，可以接管：停止，或带说明重试（`mission_takeover`，basis 必填）。2026-09-12 实测（租约 2 s）：新 owner 启动后约 6 s 出现 blocked。不接管的话，Mission 一直停在 ACTIVE，观察 240 s 也没有自动收敛，所以接管入口是必需的 |
| 其他时点 | SDK 按原文 §16.4 恢复：已完成的不重跑，SUBMITTED 未验证的重新进入验证 | — |

## 6. 仅测试用的场景

- `DESKPET_ORCHESTRATION_TEST_SCENARIO=approval-action`，并且 user_data 位于某个 `.local-test-evidence/` 目录下。两个条件缺一个都不生效，也不依赖 DEV_MODE。
- 生效后：使用独立目录；provider 换成 SDK 的 approval-action 夹具，只用工作区工具；启用测试配置服务连接器 `TestConfigService`；只允许一个 Mission；界面标出"测试场景"。
- 这个场景只用于原生验收里的审批流程，它的通过不代表生产授权。
- 原生启动器 `scripts/native/launch_native_candidate.py` 会丢弃继承来的 `DESKPET_*` 变量，所以要用 `--orchestration-test-scenario approval-action` 把场景传给后端。`--userdata` 必须位于 `.local-test-evidence/` 下，后端才会承认这个场景。

## 7. 设置（`config.toml [orchestration]`）

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | true | 按 CLAUDE.md：测试阶段已完成的能力默认开启 |
| `max_concurrency` / `max_concurrent_model_calls` | 1 / 1 | 与主对话共用 provider 限额。**只在编排库第一次 seed 时进入 ACTIVE 策略**，之后修改只会记一条 `PolicyConfigDrift`，要经策略晋级才会生效 |
| `default_mission_max_tokens` / `default_mission_max_attempts` | 400000 / 12 | 代码常量（`OrchestrationSettings`），不从 config 读。**本部署不提供无上限的 Mission**：<br>• 请求里预算留空的项，由 Host 门口补上这个默认值；<br>• 用户填了的值原样保留；<br>• 0、负数、非整数一律拒绝，不会被默认值替换；<br>• 默认值在进 facade 之前补上，所以回执的 spec hash 已经包含它；<br>• `orchestration_status.mission_budget_defaults` 把默认值下发给表单占位符，详情显示实际生效的预算。<br>依据：原生验收时，留空预算的 Mission 被真实 Planner 编出 800 tokens 的 Task 预算，结果以 `budget_exhausted` 失败。裁决见计划 journal §4.4。<br>12 次的理由：Mission 级尝试次数统计的是所有 Task 的全部 Worker Attempt |
| `decision_mode` | `"existing"` | NanoJev 决策滚动模式（§11）。白名单只有 `existing`｜`shadow`；**未知值/非字符串/缺键一律 `existing`**，`nanojev` 刻意不在白名单（Primary 是后续门）。只从 config 读，环境变量无效 |
| `decision_shadow_timeout_seconds` | 空（无 Host 侧上限） | `shadow` 下观测的超时上限。只接受正数，硬上限 60s；0/负数/非数字/布尔 → 不设 Host 侧上限。超时按"观测失败"处理，plan 不受影响 |

## 8. 模型与费用

- provider 在启动时对 `get_chain()` 的第一个启用项做一次快照，之后换 provider 需要重启才生效。
- DeepSeek 官方端点把 `deepseek-v4-flash` 映射为 `deepseek-flash`，status 里同时显示两个 id。
- 没有注入价目表：金额记为 null，界面显示"未计价"，不写成 0。
- **Task 预算下限**（SDK 0.9.11 起，F-ORCH-1）：
  - Graph Manager 会拒绝预算低于 `k × (base + critic)` 的 Task。k 是每个 Task 的候选数；base 是单轮最多产出的 token 数，这个部署是 8192；critic 部分只在验证政策含 critic_review 时计入，是 Critic 的预留 6000。
  - 被拒后，Planner / Manager 会收到原因（`task_budget_below_floor`）并重新规划，系统不会替它们编一个数。它们的输入里也写明了下限。
  - 下限是**预留层面**的必要条件：它只保证在预留那一刻，第一个 Attempt 和它的 Critic 都能预留得到，前提是一轮结算的用量不超过它的预留。真实模型一轮会连输入一起结算，远超 base（原生验收时一轮结算了 22003），所以预算正好等于下限的 Task，第一轮之后照样可能付不起 Critic，之后的修复和重试也不在保证之内。
  - 这与 Host 门口的默认预算（见 §7）是两道互补的保护。
- **产物的验证状态**（SDK 0.9.11 起，F-ORCH-3），在对应的提交事务里一并写入：
  - 结果被接受：VERIFIED；
  - 结果被判 FAIL：REJECTED；
  - 被取代的候选：保持 UNVERIFIED。

  注意：产物的 REJECTED 和结果的 REJECTED 意思不同。
  - 产物 REJECTED 表示它所在的结果被判了 FAIL。
  - 被取代的结果，其 `verification_state` 也是 REJECTED（verdict=superseded），但它从来没有被评判过，所以它的产物是 UNVERIFIED。

  读模型把两者并排显示时，要按上面的含义解读。
- **Attempt 的 RETRY_WAIT**：这是失败 Attempt 的终态。原文 §25.2 没有 Attempt 的 FAILED 状态，重试的时候另起一个新 Attempt。所以 Mission 结束后，个别 Attempt 停在 RETRY_WAIT 是设计如此，不是还在排队重试。

## 9. 部署清单（P3.1-A08）

`deployment-manifest.json` 与 `orchestration_status.deployment_manifest` 记录以下内容，不含密钥：
- host_commit，以及 `host_dirty`：backend、tauri-app、scripts 下有没有未提交或未跟踪的改动；打包版没有 git 时为 null；
- `simple_harness` / `agent_orchestrator` 实际导入的文件路径与版本；
- distribution 版本与钉版的 wheel sha；
- schema；
- 生效的部署政策、测试场景、设置；
- 模型。

实际导入的版本与钉版不一致时，服务标为 unavailable。这个检查在打开编排库之前做，所以版本不对的 SDK 不会去迁移编排库；这种情况下，清单只记录 `refused: pin_mismatch` 和实际导入的信息。

## 10. 限制与遗留

- 身份是自报的本机用户，没有多用户认证。
- 沙箱（P3.2）的边界，如实记下：
  - **内存与进程数只是软限制**：本机没有 cgroup，`RLIMIT_AS` / `RLIMIT_DATA` 设不进去，`RLIMIT_NPROC` 又按整个 uid 计数，所以只能采样后回收，属于事后处理；`cpu_seconds` 是**每进程**的硬限制，整次执行靠墙钟兜底。
  - **没有 uid 隔离**：没有管理员权限，同一 uid 下的残余风险靠 seatbelt 规则收敛。
  - **元数据可读**：放行 stat 才能让 venv 的软链解析正常，代价是沙箱里的代码能探测任意路径是否存在、大小与修改时间，但读不到内容。
  - **用到的是已废弃且无公开文档的接口**：seatbelt 与 `sandbox_check`（Chromium、WebKit 也在用）。每次启动由探针重新验证；探针不过就退回 `off`。
  - **回收耗时受外部工具影响**：认进程要靠 `ps`，不隔离模式还要靠 `lsof`，两者都有单次超时，但极端情况下一次回收仍可能偏慢；正确性不受影响。

## 11. NanoJev 决策接缝（PR-7，Shadow-only）

- 最后更新：2026-09-20
- 计划来源：`plans/taskSys2/升级planV1/v1.4/NanoJevAdd.md` §57 PR-7（Shadow 接入）+ 本仓任务书 `plans/taskSys2/升级planV1/v1.4/任务书-PR7-2026-09-20/pr7-impl.md`

**生产链路**

| 层 | 位置 | 职责 |
|---|---|---|
| 配置载体 | `config.toml [orchestration] decision_mode` / `decision_shadow_timeout_seconds` | Host 唯一真源。缺省在 `deskpet/orchestration/settings.py`：`decision_mode = "existing"`、超时 `None`。**只从本文件读，环境变量无效** |
| 解析 | `settings.py::_decision_mode` / `_shadow_timeout` | 白名单只有 `("existing", "shadow")`。未知值、非字符串、缺键、空串 → `existing`；`nanojev` 刻意不在白名单，故 Primary 配置写不出来。超时只接受正数，上限 60s，其余 → `None` |
| Host 接缝 | `deskpet/orchestration/decision.py::DecisionSeam` | 把设置变成显式 typed `DecisionPolicy`（`build_decision_seam`）；`ready_priority_plan()` 调既有 `allocate()`，仅在 `shadow` 且拿到 journal 时才追加观测；`DecisionSeamStatus` 供 `status()["decision"]` 投影 |
| 装配 | `deskpet/orchestration/service.py::_install_decision_seam` | 在 `_open()` 末尾一次性构建，journal 用编排器自己的 Store（`DecisionEventJournal`，写 `orchestrator.db` 的既有 `events` 表，无新表）。构建失败只记日志，不影响启动 |
| SDK 接缝 | SDK `agent_orchestrator/decision/host_integration.py` | Host 面向的调用点：`frontier_priority_candidates`（`frontier()` 顺序）、`compute_ready_task_decision_id`（`njr-` 确定性 id）、`observe_frontier_priority`、`ready_task_priority_decision`。SDK 侧不读任何配置 |
| 事件 | SDK `decision/events.py` 既有 `DecisionEventJournal` | additive 事件类型，按 `idempotency_key` 幂等；`pd-` 前缀被拒 |

**硬约束（不因本片放宽）**

- **分配器授权集合是唯一权威**：`READY_TASK_PRIORITY` 只观测 `frontier()` 顺序，观测前后 `AllocationPlan` 逐字节相同（`grants`/`scores`/`open_attempts`/`eligible`/`slots` 全部相等）。Shadow 答案永不生效——即使它"很有信心"，或指向一个被 paused/依赖挡住的 Task（该 Task 根本不在候选集里，越界答案被 SDK 结果校验拒绝）。
- **默认 `EXISTING`**：缺键、拼错、类型不对都落在既有的确定性路径上，不观测、不打事件、不调模型。
- **无 ad-hoc 环境变量**：`decision.py` 不出现 `os.environ`/`getenv`；设置只从 `config.toml` 读。
- **Primary 不可达**：Host 白名单无 `nanojev`，SDK 侧 `DecisionMode.NANOJEV` 也不被本接缝引用。
- **`RETRY_OR_ESCALATE` 未接线（blocker）**：`RetryAction` 有六个值，映射到两值契约需要单独裁定。本片不猜；`tests/orchestration/test_decision_shadow.py` 用 AST 扫描钉住"除状态位 `retry_wired`（恒 False）外，模块不引用任何 retry/escalate 标识"。
- **观测失败不是生产失败**：shadow 异常/超时/越界、journal 写失败、缺 `mission_id` 一律转成 "plan 照常返回 + `status()["decision"].failures += 1`"，不抛进驱动循环。
- **0/1 候选不调模型**：`frontier()` 少于 2 个候选（含 0 个）时直接返回，连请求都不构造——Host 接缝与 SDK 接缝各有一道同样的闸。
- **无 journal 不观测**：没有可归因的事件汇时，不调用模型（不存在"悄悄观测"这条路）。

**已知边界（如实记录，未绕过）**

- 本片观测是**结构性的、不是模型驱动的**：没有真实 NanoJev checkpoint、没有加载任何权重。它是"接缝通了、数据能收"，**不得**读作模型质量、shadow 收益或 Primary 就绪。
- `frontier()` 顺序 ≠ `allocate()` 授权顺序：`frontier()` 按 `(-priority, ordinal)`；`allocate()` 在 Task 有 §29.3 分数时按分数排，仅对无分数的 Task 回落到 `(-priority, ordinal)`。本片按 §57 的口径观测 frontier 顺序，**不声称两者一致**。
- 观测的事件落库路径只在接缝层测过（真实 `DecisionEventJournal` + 真实 `Store`），未在真实 Mission 的驱动循环里跑过端到端。

**验证状态（2026-09-20）**

- SDK `tests/orchestrator/full_target`：**3866 PASS / 5 SKIP / 0 FAIL**（5 skip 全为环境/开关性）。
- SDK 决策+分配器相邻套件：**205 PASS**（含新增接缝 30 条）。
- Host `tests/orchestration/test_decision_shadow.py`：**56 PASS**（SDK 源码环境）／**41 PASS / 15 SKIP**（vendored wheel 0.12.2——该 wheel 早于 decision 包，skip 是显式声明的"合同缺席"，不是静默通过）。
- Host `tests/orchestration` 全量：**293 PASS / 97 FAIL**；与 `git stash` 干净基线**逐条一致**（基线 237 PASS / 97 FAIL，+56 恰为本片新增；97 项为既有 SDK pin/环境失败）。
- mutation：SDK 接缝 6 个行为可区分 mutation 全部 killed（含"去掉字段分隔符"与"用 frontier 顺序替换 allocate 授权"两条语义型）；Host 接缝的 mode 短路、journal 闸、候选数闸、id 前缀由行为测试钉住。
- ruff：本片新增/修改文件全绿（`manifest.py` 的 2 条为既有）。
- **未验证**：真实模型/checkpoint 观测、真实 Mission 驱动循环内的端到端、Primary、`RETRY_OR_ESCALATE`。未打包、未发布。
  - **宿主崩溃后的逃逸进程认不出来**：金丝雀随执行目录一起删除，宿主重启时没有线索可扫（SDK journal 已登记，留待后续处理）。
- DeepSeek 价目没有注入，金额显示"未计价"。
- 策略只读：提议、评测、晋级要用 SDK CLI。
- 编排的证据目录（workspaces）不会自动清理。
- PyInstaller 打包 spec 仍停在 0.6.4，尚未跟进。
- 模型调用中途被杀之后，需要人来接管，SDK 不会自己收敛。自动恢复"结果未知"的回合，归入 P3.5 处理。租约为默认 60 s 时，"结果未知"要多久才出现，还没有在原生 App 里测过。


Source-native slot binding (2026-09-13 08:24 CST): launcher supports explicit 1..4 logical/model slots, default 1/1; values are part of the source identity and are written only before first backend startup. Resume checks exact integer config without rewriting seeded policy. Older source runs require their recorded pre-slot launcher. Main verification: g-source-slots-search-v2, 32 PASS/2.09s (runner2.70s; includes controlled search software test). Terra rework fixed incomplete CLI oracle and TOML bool equality; native multi-Mission load remains OPEN.

Real N1 v14 (SDK4e79dac/Hoste6a1dac7) reached verification_passed in223.94s: seven literal VERIFIED,three SUPPORTED,224843tokens settled/0reserved,12Providerhandoffs (10succeeded/2failed withusage),0rehandoffs. Actual UI opened REPORT and full table citation; cold same-state12->12. Raw case-summary SHA25684d7385071c30cab20e668f5b25eef41e4f53c1f1abcbf6212649b74bd81fc04 under Host .local-test-evidence/2026-09-13/p33-g/source-ui-n1-v14/. Manual quality FAIL: REPORT3.1 says both sources lack frozen build/install/verification records, contradicted sourceAline7 historical builds/startup failures. Both Worker/Critic had read whole sources. Runtime/citation/cold PASS does not close N1 report quality. Doc8 successor guidance and original400000/12 recheck in progress. Native owned groups94307/97182 exited0,noresidual; lifecycles477.554/80.27s include UI/analysis waiting. No packaging/P36/push.

## PR-7 local runtime closure (2026-09-20)

The initial production probe found the backend environment importing SDK `0.11.1` without
`agent_orchestrator.decision`, while the Host pin is `0.12.2`. The authorized local closure
uses an explicitly attested editable SDK source (`51dbed2`, 443 source inputs) in the
development environment; it does not replace or publish the release wheel.

The live caller is `Orchestrator._decide()`'s existing `allocate()` branch. It computes the
`AllocationPlan` first and then calls an optional Host observer with that plan. The observer
cannot replace or reorder grants and callback/provider failures are isolated. A real
two-ready-Task Mission reached the observer and persisted `DecisionRequested` plus
`ShadowDecisionProduced` (`njr-` identity) in the existing `orchestrator.db` `events` table.
The run used a FakeShadowProvider because no NanoJev checkpoint is authorized in PR-7.
Default `EXISTING` remains short-circuited; Primary and `RETRY_OR_ESCALATE` remain out of
scope. Evidence is under `.local-test-evidence/2026-09-20/pr7-production-closure/`.

## PR-7 wheel runtime closure update (2026-09-20)

The local runtime now uses the non-public development candidate
`0.13.0.dev20260920` from `backend/vendor`, with candidate and lock identity bound to
wheel SHA `9687d023c3fc9bc00c990c35c2827e035c3e56fbda49659e91acefc7fcfd2ef1`. The
manifest records dirty-source provenance (`51dbed2`, 443 inputs, snapshot digest
`74b4067b…`, `release_published=false`). Backend/.venv imports this wheel without an
editable install or SDK checkout path. A real two-ready-task Mission completed in both
EXISTING and SHADOW; SHADOW persisted DecisionRequested and ShadowDecisionProduced
with FakeShadowProvider, while EXISTING emitted no decision events. The SDK caller
schedules async observation via a retained task, so provider latency cannot delay the
allocator; sync Host seam refuses `asyncio.run` from a running loop. `allocate_v2`,
Primary, real checkpoint and RETRY_OR_ESCALATE remain outside PR-7.
最后更新：2026-09-20 CST。**NanoJev/PR7 状态边界：** local Host runtime candidate 已对齐，但仍 Shadow-only；T09 真实双Task事件通过，grants six-invariance 只到 probe-level；T01 计数为 non-vacuous。R02 真实运行质量为 2/8，不能升 Primary。H1-H 仍处于候选 worktree，相关门禁未闭合；不宣称完整 H1 或 Primary。[依据：`candidate-closure-terra.md`、`t09-grants-closure-terra.md`、`t01-call-counter-sol.md`、`r02-real-deepseek.md`、`h1-gate-closure-sol.md`]
最后更新：2026-09-21 CST。SDK H1-H 候选的 WAIT 由现有持久事件关系驱动，按权威 Task semantic identity 和实际状态判断；登记+结算、唤醒+Planner package+预算+binding 分别同事务。Task 终态与 Acceptance 分开，新协议 package v6/int5 冻结状态和 occurrence outcome；legacy 包不变，历史 v5 request 继续识别为 int4。160 项定向及 48 项重叠回归通过，完整复验与真实 Worker WAIT 尚待；不代表 Host 运行版本更新或 H1-H8 完成。[验证范围](../plans/taskSys2/升级planV1/v1.4/WAIT复验-2026-09-21.md)。
最后更新：2026-09-21 CST。Operation 补遗实施中，V1.4（去除 NanoJev）整体未完成。新增完成规格批准命令 Host handler → SDK facade → 原 CommitService/Store，Spec/receipt/event 同事务；来源、租户、重放、过期拒绝及迁移定向 27 PASS（0.54 秒，后继 Task contract hash 修正仍在复验）。Host 在独立临时源码副本对齐两包版本后，真实 service/handler 接线 11 PASS（18.28 秒）；仅为派生源码接线证据，不是当前候选字节、wheel 或 UI 验收。移除启动/重建路径上已延期的 PR-7 observer 依赖，保留历史文件。Scope/Plan Commit、准备与效果区分、T0/T3、D3 及完整 H1–H8 仍待；未合并/重装 Host。当前无新增 PlanAgent 架构待决。详见 Host V1.4 的 Operation补遗实施记录-2026-09-21.md。
