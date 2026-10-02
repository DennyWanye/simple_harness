最后更新：2026-10-02 CST（删旧平面模式第一刀完成 opt.130；第二刀完成 opt.131）。

**删旧平面模式（2026-10-02 起，用户决定整条线删）**：分三刀。第一刀删四样只属于旧平面模式的机制——管理员改图、片段验证、候选比较与选择轮（连同策略评测与晋级）、并行候选，分小步做，每步定向测试 + 故意改坏检验 + 只读核验 + 发布。
- **第 1 小步（SDK opt.128）策略评测与晋级**：一个编排库只剩建库时写入的那一个策略版本；策略接口与命令行只剩三个只读动作；评测计划、晋级门、从历史学习候选、评测库、消融开关全部删除（SDK 源码约 3400 行、测试 19 个文件）。候选比较从此开不起来，源码留到后面的小步删。记录：`plans/2026-09-27-desktop-next/删旧平面模式-第一刀实施记录.md`。
- **第 2～4 小步（SDK opt.129）管理员改图、片段验证、候选比较与选择轮**：一次删完（三样在主循环里交织）。执行者“做不了”只记历史后按次数重试，建议的新任务不进图；尾预算修订号、尝试执行快照两样迁出保留；迁移 31 删 4 张表；Host 详情页去掉“搜索”块、新建任务去掉“执行方式”。SDK 源码约 6400 行、测试约 30 个文件。AppWorld 的 F 对照臂改为明确报错。
- **第 5、6 小步（SDK opt.130）并行候选、执行者变体与角色可见性**：同一步同时只能有一次尝试（产品本来就是 1，行为不变）；五个执行者变体与按角色裁剪资料的模块删除（管理员删后不可达）。
- **第二刀（SDK opt.131）冲突、仲裁、预留、综合、系统尾池**：整条线删除；争议改为从结论本身读出，最终报告与详情带 `disputes`；迁移 32 删 4 张表；新建任务表单去掉两项。实施记录 `plans/2026-09-27-desktop-next/删旧平面模式-第二刀实施记录.md`。

最后更新：2026-10-02 CST（HTN 精简改造五片完成；旧分层旁路清理完成，SDK opt.124；审阅员提示词补全，SDK opt.125）。

**HTN 精简改造（2026-10-01 起，按"判断交给模型，Harness 只管约束与秩序"收口编排内核）**：
- **片 0（SDK opt.113）先删先并**：旧规划协议整条路径删除；三种专用修复并入一条通用修复请求，Harness 只报告事实、不再替规划器退做法或取消步骤；删测试关键词规则与修复开关。真机一局完成交付。
- **片 A（SDK opt.114）规划器自己判断**：程序不再替规划器选做法，只过滤；没有合适做法时规划器自己提，方法合成器的运行路径整条删除；规划器提的新做法必须先过独立审阅才能被计划采用（闸门在计划提交的同一事务里），审阅有结论才叫醒规划器，两次判不下来问用户；规划次数只剩一处判断；规划器提示词重写成完整一份。真机两局都完成交付，其中一局走通了"审阅打回 → 规划器改版重提 → 审阅通过"，全程没有合成轮、没有程序代选。
- **片 B（SDK opt.115～120）中间目标**：规划器可以把一组步骤交给一个中间目标；它的做法单独规划、单独过独立审阅（按分给它的要求审），做完先过独立组合审阅形成中间结论，后面的步骤其后才开工，并通过端口拿到它的产出。程序只加秩序：能放几层由类型的层级决定；交给中间目标的要求保持原编号；它的做法恰好覆盖分到的要求。"目标还没有做法"并入通用请求，主循环专用入口删除。真机六局，第 6 局完成交付；前五局暴露并修掉五处问题（含一个与片 B 无关、把主循环拖死的旧缺陷）。
- **片 C（SDK opt.121）规划包与提示词收口**：方法合成器角色删干净；规划器、分层执行者、根审阅员各只留一份提示词，规划请求末尾追加的"协议提醒"删除；"允许哪些决定"只有一张表，"声明受阻""升级问人"两种类型连类型一起删；规划包只组装一层、只发九个视图，同一件事不再发两遍，一步失败的完整记录只放在待处理的修复请求里。
- **旧分层旁路清理（SDK opt.124）**：分层任务只剩一个世界——带协议绑定、完成范围在计划提交时冻结。五个建世界的测试夹具全部迁到带绑定的世界；只服务分层任务的模块里"没有绑定"的分支全部删除（判断从 104 处减到 35 处，只留在与旧平面模式共用的入口）；旧的计划编译/提交入口整段删除，计划提交只有"预览候选 + 准入凭据"一个入口。SDK 源码净减约 1100 行。没动：旧平面模式整条线（待用户定）、不走保证通道的旧审阅路径、不装执行图的夹具世界。详见 [AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md) 顶部与 `plans/2026-09-27-desktop-next/旧分层旁路清理-实施记录.md`。
- **强杀重启后原地重做（SDK opt.127）**：真机发现强杀后端再重启会让任务直接失败（被打断的那一轮在准入处被拒、被当成不可重试）；已改成按"被打断"原地重做。
- **重启后等待缩短（SDK opt.126）**：重启打断一次模型调用后，判它丢失并重做前的等待从 180 秒缩到 30 秒（用户决定）。
- **删旧平面模式进行中**：整圈跑主循环的那一类老测试按用户决定已删（约 1.06 万行）；共用机制的测试分批迁到分层任务上（每批用"故意改坏"与迁移前对照）；Host 脚本化通道上有六条产品同形场景。
- **产品同形的脚本化测试世界（2026-10-02，Host 层）**：`backend/tests/orchestration/_layered_lane.py` 在 Host 默认部署（分层 + 执行图 + 保证通道）上只把模型回复换成脚本，一个任务能整圈跑完（提做法 → 审做法 → 采用 → 执行 → 步骤审查 → 最终审查），审查判返工后经规划器决定重做也能跑通。此前这条产品路径只有真机局在验证；SDK 层的同形世界还没有（做执行图那一轮补）。删旧平面模式的方案与进度见 `plans/2026-09-27-desktop-next/删旧平面模式-方案.md`。
- **审阅员提示词补全（SDK opt.125）**：审阅员的提示词补上完整的回复示例、逐字段含义、三条禁止项（代码围栏、前后夹带文字、多写字段）和长度上限；解码规则不变。审阅员缺哪些事实已检查记录（11 条），作为后续项留到做执行图那一轮。
- **片 D（SDK opt.122、opt.123）其余越位**：任务停在原地时先把局面如实交给规划器一次（每个计划版本一次），问过仍停在原地才判停；"审查的回复用完仍无法采用"在停机报告和给规划器的请求里如实写明；保证通道上文档任务的文字要求以最终审查的结论为准，程序只做秩序检查（引用能解析、资料当前、声称可用、不确定须附说明），字面相等与不确定占比不再作数；Host 不再用词表猜一条要求是不是"操作"，只认 `action:` 前缀；契约里的文字不再被子串匹配猜"像不像代码"；Host 关闭保证通道的开关删除，旧审阅路径在产品里不可达。opt.123 修掉真机暴露的一处：旧任务的执行图文档读不了时不再把主循环带垮（一个任务的数据读不了只影响它自己）。
- 还剩：单独清理"没有协议绑定的分层任务"那套旧旁路（连同 SDK 里不走保证通道的旧根审阅员与裁判 Critic、平面模式的历史提示词版本、文档领域按版本号的判断与字面相等 / 占比阈值）。
- 明细见 [AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md)、[ASSURANCE.md](ASSURANCE.md) 同日条目，过程记录在 `plans/2026-09-27-desktop-next/HTN-片0-实施记录.md`、`HTN-片A-实施记录.md`、`HTN-片B-实施记录.md`、`HTN-片C-实施记录.md`、`HTN-片D-实施记录.md`。

（以下为 2026-09-27 及更早的记录。）保温杯任务真机跑通；收尾按上限计入、拆步打回；真机点击反馈修复 + 复杂编排跑通。

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

**当前版本**：SDK `0.13.0.dev20260925+opt.32`，Host 钉版提交 `5faa739f`。本地领先远端约 40 个提交，等用户点击测试通过后再推送。

**用户点击测试中提出、已修复的问题：**
1. **执行图一直停在“正在排版”**：改用官方排版 Worker，加超时回退。
2. **主对话卡在权限**：改为除凭证和核心文件外默认放行，核心文件在会话里弹卡片申请，永不阻塞。详见 [AGENT_HARNESS.md](AGENT_HARNESS.md)。
3. **文字不能选取复制**：改为全站可选。
4. **新任务的执行图是空的，后台 CPU 满载、会话反复断连**：编排循环空转，已修。
5. **执行图看不出流程**：方法合成提示词 v8，按交付物拆步；步骤标题改为可读的职责说明。详见 [UI.md](UI.md)。

**复杂编排跑通**：7 步的读书会任务，前七趟各暴露一个编排内核缺陷，逐个修复。
- 缺陷包括：规划次数整任务累计、工具参数 JSON 坏了不重发、审阅清单超过大小上限、空 code_test 被读成“无法下结论”、卡死检查抢跑（两处）、审阅引用违规后无补救、单步额度不够、规划回复多写字段。
- 第八趟从规划到 `MissionCompleted` 用时 31 分钟，6 个交付物齐全。
- 明细见 [AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md) 同日条目。

**本日用户决定：**
- 编排单步额度 300 万 token（原 100 万），任务总上限 2000 万不变。
- 规划器、方法合成器回复里的多余字段直接丢弃；审阅员仍然严格。

**已知遗留：**
- Host `tests/orchestration` 有 26 个原有失败，SDK 有约 11 个原有失败。
- 三个治本项待定：文档步骤不该安排 code_test；审阅成本高；缺少“多步任务 + 故障注入”的快速端到端测试。
- 权限改造未做：编排侧读取放开、配置追加受保护清单。

最后更新：2026-09-28 CST（NEXT-TG-1.0 第二批 B，SDK `0.13.0.dev20260925+opt.55`）。推进规则落地：统一空闲判定（合法等待不再误报卡住或误判失败）、宿主职责不再被长模型回合压住（后台空转、授权发不出的根因）、无写入时不空转（CPU 约 100%→约 10%）、审阅格式修复写明规则。真机内容任务完成。详见 [AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md) 顶部。

最后更新：2026-09-28 CST（NEXT-TG-1.0 插入项，SDK `0.13.0.dev20260925+opt.52`）。**默认配置（保障层开）下带发布的任务可以完整走完**：真机 `mission-e5f82ae8c9f24ff8` 从界面新建、确认完成要求、提交操作、批准发布，到真实发布、结果验收、最终审查、任务完成全程通过。共修 11 处（Host 1 + SDK opt.42～opt.52），多数是保障层下操作审查链路此前从未真跑过留下的接线缺口，详见 [ASSURANCE.md](ASSURANCE.md) 顶部与 `plans/2026-09-27-desktop-next/PLAN-STATUS.md`。遗留：收尾期"卡住"误报（第 2B 批）、候选下拉同名重复与后台按键问题（第 4 批）。

最后更新：2026-09-28 CST（NEXT-TG-1.0 第三～五批，SDK `0.13.0.dev20260925+opt.59`）。第三批：执行图由 SDK 执行过程只读接口驱动（同一读取令牌、分页、回合详情白名单），Host 不再直读 SDK 表；第四批：自愈与监工解耦、发布目录设置入口、主 Agent 发起后台任务 `mission_start`、下架无执行入口的委派工具、若干界面易用性修复；第五批 A：编排 Agent 以 MISSION 模式按派发意图的精确来源创建并在每次请求前复核（旧库旧会话照常）；第五批 B：所有原生池共用一个技能目录权威与设置页技能入口。定向测试、变异与独立审阅（每批 1 轮，阻断项已修）通过；真实控制通道核对执行过程读取与技能目录；桌面真机点击未运行（未获操控授权）。全量回归按计划只在第六批合并前后各跑一次。进度与欠项：`plans/2026-09-27-desktop-next/PLAN-STATUS.md`。

最后更新：2026-09-29 CST（NEXT-TG-1.0 收口：阶段完成标准剩余缺口，SDK `opt.83`～`opt.90`）。完成：任务执行者调用技能（执行者模板 v5 + 部署声明三件技能工具）；技能准入评估接设置页（开始评估 = 试用 + 建评估任务，准入 = 交 SDK 核对审阅证书）；多任务并发按真实额度放开（准入身份不再含名额数，默认并发 2）；五个委派工具接回主对话（真实 SDK 子运行，重启后子运行接着跑、父运行被唤醒，见 [AGENT_HARNESS.md](AGENT_HARNESS.md)）；界面真机补点（执行图点节点、新建任务发布助手、操作工作区确认与发布批准、技能安装→评估→准入）。阻断：模型路由（只有一个模型、任务所有角色同池，写明影响与解除条件）。部分完成：结构修复真实模型五轮，链路每环都走到、未收敛到第 2 版计划，途中修 5 处（系统代办发布先装配操作运行时、缺外部资料时问用户、规划器模板 v12、用户回答同时记成任务级备注等，见 [AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md)）。收口后全量回归与 opt.80 逐条对比无新增失败（`.local-test-evidence/2026-09-29/final-opt90/结论.txt`）。进度与具名欠项：`plans/2026-09-27-desktop-next/PLAN-STATUS.md`。

最后更新：2026-09-29 CST（发布交给系统 + 按谁的错扣次数 + 主 Agent 入口，SDK `0.13.0.dev20260925+opt.66`～`opt.75`）。只有模型做错才扣任务次数（格式没写对/服务出错/被打断退还，同一步非模型失败合计 6 次停下）；确认页批准的发布由系统自动准备申请单、人逐个批准；主循环只处理有变化的任务；每个要发布的文件在入口自动补"写出它"的要求、定计划时必须链接到恰好一个步骤，系统从声明产出者及其下游取最终版本发布；审阅员查看次数快用完时被提醒作答；重启打断后不再挂住（工具结果未知、单轮墙钟超时都按被打断处理，发布结果审阅重试用完明确停下）。主 Agent：`mission_start` 在主对话显示任务卡片（确认完成要求、批准发布都由人在卡片上点），只读 `mission_status` 查进度；确认完成要求默认预填、一般一次点击。真机：第六局两份发布成功（最终审阅查满次数交白卷 → 已修）；第七局从主对话发起、在对话卡片确认与批准，两份发布成功且逐字节一致（重启后发布结果审阅被打断耗尽重试 → 已改为明确停下，"被打断的审阅补一次机会"记后续）；第八、九局为中途重启验收（结果见 `.local-test-evidence/2026-09-29/`）。方案与记录：`plans/2026-09-28-system-operations/00-PLAN.md`，详见 [AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md)。

最后更新：2026-09-29 CST（真机验收收尾，SDK `0.13.0.dev20260925+opt.76`～`opt.80`）。**两局完整走通**：第九局（任务页发起、内容步骤执行中重启）与第十四局（主对话发起、对话里一次点击确认、卡片两次点击批准，不重启）均走到任务完成，两份发布逐字节一致。第十～十三局各暴露一处缺陷并修掉：合成器看不到每条要求的原文而猜错编号（opt.77）；接力步骤只交端口文件、新写的模块被丢（opt.78）；最后一步输出端口接受侧与核对侧两套算法、验收永远被拒（opt.79）；合成器不知道发布由系统做、把"发布落点"写进写文件步骤的证据要求（opt.80）。遗留：一步报告"卡住"时整局直接失败不回规划器、被打断的审阅仍占一次重试。记录：`plans/2026-09-28-system-operations/00-PLAN.md` G 节。

最后更新：2026-09-27 CST（NEXT-TG-1.0 第 2A 批，SDK `0.13.0.dev20260925+opt.41`）。新任务默认走严格执行图（Host 创建事务写要求 + 系统协调器启用，详见 [TASKGRAPH.md](TASKGRAPH.md)）；执行图部署清单按真实上游核心链任务与冷重放回执合法重建；真机：上游核心链（含界面批准的真实发布）完成、默认执行图小任务在保障层开启下完成。本批真机修复：自动确认不再吞掉操作类要求、发布候选说明/规则检查/验收三处口径统一、接续交付链携带上一步产物、候选指向的文件由系统列入本步结果、任务终判视图可读文件、操作提案审查核对发布产物身份、执行图任务规划回复不再必过期。进度与遗留：`plans/2026-09-27-desktop-next/PLAN-STATUS.md`。

最后更新：2026-09-30 晚 CST（NEXT-TG-1.0 收口第 6 项完成，SDK `opt.102`）。**真实模型下结构修复走通**：第 8 局资料换版 → 规划器给已通过的第一步提后继步骤 → 第 2 版计划 → 用新版资料重做并通过 → 终审 → 交付（12.5 分钟）。第 2～8 局途中补 7 处缺陷（执行图共享检查过严、输入版本比对过严、换代后仍等原样重试批准、执行者从不关闭撑满实例上限、修方法机会整任务共用、"旧派发作废"标记从不清掉导致收不了尾等），详见 [AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md) 顶部。

最后更新：2026-09-30 CST（旧式执行池删除；任务过程改走 SDK 正式接口；规划器格式三件）。①**旧式执行池删除**（用户决定：开发期不兼容、不隐藏）：Host 只装配原生运行平面池（`deepseek-native-256k/512k-v1` 与思考版），`default`/`deepseek-context-*` 池、本地模型配置、旧计数器身份、`native_plane` 开关全部删除；没有经过认证的计数器（非 DeepSeek 模型）编排服务明确报"编排只支持 DeepSeek"；夹具场景用夹具计数器也跑在原生池上。②**任务过程**标签改读 SDK `taskgraph.execution_snapshot` / `execution_detail`（SDK 侧过滤思考内容与系统提醒、密钥脱敏），Host 直读实现与 `mission_story` 删除，详见 [UI.md](UI.md)。③规划器提示词 v13、字段路径反馈、后继步骤 goal_type_ref 无损补齐、收敛检查只拒真正共享的产出（SDK opt.97）。进度：`plans/2026-09-27-desktop-next/PLAN-STATUS.md`。

最后更新：2026-09-26 CST（编排实时可视化）。任务页执行图改为**运行视图**：新只读动词 `mission_live_graph`（直读分层计划表 + `tasks.status` + 最新 `CompoundPhaseChanged`，真实库每次 6～11ms）与 `mission_planning_decisions`；旧的严格执行图读取对真实任务恒为 `NOT_ENABLED`（执行图内核在产品路径从未开启），前端不再调用，`MissionTaskGraph.tsx`/`taskgraphStore.ts` 删除。`mission_changed` 推送改为带新事件（`from_seq/events/truncated`，白名单同 `project_event`），前端接得上就追加、有缺口才分页。执行图用 elkjs（Worker）+ @xyflow/react：复合任务是可折叠框，800ms 防抖随推送刷新，版本不变不重排，新版本高亮新步骤，超 10 分钟无动静标"可能卡住"，点步骤看尝试/验证/事件/规划决定，可切历史版本看结构；任务列表加 5 段进度条与子任务完成数。删除未用的 cytoscape。方案与记录：`plans/2026-09-25-orchestration-live-view/`。验证：后端定向测试通过、前端全量 vitest 通过、生产构建与调试应用包通过；独立核验 2 个阻断问题已修；真机鼠标点击待用户授权。

最后更新：2026-09-25 CST（主流程优化 7 条，分支 `opt-0925`，SDK `0.13.0.dev20260925+opt.1`（源 `8f7bf477`）/ Host 钉版见提交「Host 钉 SDK opt.1」）。用户 2026-09-25 两条总原则：**开发阶段不考虑旧数据兼容**（单一当前契约，历史版本明确报错不回落）；**会话数据永久保留供审计**（只统计只提醒，不删除）。做的 7 条：① 任务页执行图 `taskgraph.*` 消息放行到编排 handler（此前到不了后台，面板只会读取超时）；② 规划请求包分列合法 `enabled_decision_types`（9 个 `PlanningDecisionType`）与 `enabled_repair_kinds`，内部启用键 `REPAIR/<子类>` 不再暴露给模型（9-23 有 7 局真实模型照抄成 decision_type 被判 `DECISION_TYPE_UNKNOWN`）；第 8 版包（标签 `planner-package-hierarchical-v10`）+ 提示词 v11，历史包/提示词分支与标签映射删除，绑定到旧包的任务派发时 `ContractError`；③ 判据要求的 pytest 运行（含裸 `pytest:`）一个测试都没收集到即 FAIL，只有无人要求的顺带运行可记无可证明内容；④ Host 自动批准检查策略的事件如实记 `actor_type=system`（`approval_source=HOST_LOSSLESS_AUTO`，`on_behalf_of_principal_id`），不再写成 human；⑤ 召回结果页到 16 页上限时以 `RECALL_AGGREGATE_LIMIT` SKIPPED/PARTIAL 停下，不再整条模型请求失败；⑥ Agent 后台循环（索引泵/清理/召回续跑/工具探测）错误写日志（同码 60 秒限频）并计入 `AgentRuntime.background_health()`，逐项隔离不再一项出错整轮中止，Host `native_plane.profiles[i].background` 上报，任务页连续失败 ≥3 次提示一行；⑦ 任务与会话数据只统计：Host `orchestration_storage_get`（线程池遍历 `data/agent-orchestrator/`，缓存 10 分钟、后台 30 分钟重算、强制刷新 10 秒节流），配置 `[orchestration] storage_warn_bytes` 默认 5 GiB，设置页新增「任务与会话数据」一节，超阈值提醒去数据目录迁移，无任何删除入口。核对后不做的与护栏（尤其"证书签发即判 SOURCE_CHANGED 绝不能接到 `goal_resolutions.validity` 列上"）见计划附录 A。计划、子代理挑战记录与实施记录：`plans/2026-09-25-mainflow-optimization/PLAN.zh-CN.md`。验证：各条定向测试通过；合并前/后全量回归无新引入失败；真机点击（电脑控制工具模拟鼠标，新数据目录，DeepSeek 开思考）一局完成到正式交付，5 个检查点通过；顺带暴露旧缺口：执行图面板对正常任务读不到图（执行图合同从未在产品路径开启，`taskgraph_revision_records` 为空），记计划附录 A-14。详见该计划第 11 节。

最后更新：2026-09-25 CST（ARP 交付后的桌面程序真机点击）。用调试版应用包做原生点击，任务编排主流程跑通：简单题与复杂记账题（ledger.py + 27 个测试 + 中文 README）都到正式交付，用量全部结清且与界面"已结算"一致、无少算。过程中修 8 个真缺陷：等授权时界面不刷新、同内容证据固定撞唯一约束（迁移 27）、列表与详情状态不一致、审阅元数据重复携带正文、历史审阅记录超 256KB、修复轮规划被拒后任务空转、思考审阅者输出被 8192 截断、验证摘要显示 None；并简化任务编排界面。SDK `0.13.0.dev20260924+arp.22`（`e1cd2792`），Host 钉版 `51aea226`。遗留：规划包诱导无效"等待"、思考线路超时过长、复杂任务默认 4M 预算偏紧。记录 `plans/AgentRuntime/2026-09-23-arp-body/10-RP-E3-原生平面接线实施记录.md` §7.23。

最后更新：2026-09-25 CST（ARP-EXEC-1.1.1 原生 Agent 运行层交付）。分支 `arp-1.1.1` 已快进合并 main `cbf595c8` 并推送私有 origin；SDK `0.13.0.dev20260924+arp.17`（源 `ca79a7ce`），Host 钉版 `ea6d06e9`。**默认开启**：原生思考池、默认开思考、本地 BGE-M3 INT8 向量车道、SCRIPT 技能沙箱执行、原生池上的保证审阅。**验证**：真实模型 12 局 12/12（少算 0）+ 关思考对照 3/3；产品后台真实链路 Mission 完成；安装后导入核对通过；合并前全量回归无新引入失败（Host 159 失败 153 原有 + 6 新增已修；SDK 与合并基点同条件对照 163=163；抓到 1 个真缺陷已修）；合并后全量回归见 ARP 记录 §7.22；`scripts/verify_development_handoff.py` PASS（SDK 清单按当前源码重生成）。**未做**：官方 DeepSeek 端点（用户决定不做）、UI 测试（前端零改动，未跑）、向量模型首启自动下载；后续项见 [ARP 交接](../plans/AgentRuntime/2026-09-23-arp-body/HANDOFF.md)「交付清单」。工作树：`simple_harness-arp` worktree 已完成使命，可删除。下文为历史。

最后更新：2026-09-24 CST（Assurance 第十段：默认开启 + 主流程跑通）。**保证机制默认开启**：SDK 单一默认选择点 `default_assurance_profile_for_new_mission()` 返回 `AssurancePolicy()`，Host `OrchestrationSettings.assurance_profile` 默认 `"on"`，`"off"` 为显式退出。新增生产环节：Host 以自身已认证 caller，在每轮编排循环后、以及 SDK 审阅准备前（部署端口 `AssuranceDeploymentPorts.check_policy_projector`），为每个冻结完成范围批准由原需求无损推导的检查策略（SDK `lossless_scope_mapping`：语义判据→SEMANTIC、具名检查→精确注册 CheckSpec，推不出就 `CHECK_POLICY_UNRESOLVED`，不猜），并为根范围批准最终审阅用途的策略（`mission_final_scope_id` + 对外接口可选 `purpose`）。保证通道下任务整体判定复述已采纳根决议上的认证等级，不另请未认证评判；根节点完成判断读根决议判据而非旧格式审阅记录。**验证**：Host 生产装配 + 真实模型（Grok Build 通道 `grok-4.6`；DeepSeek 日卡上游当晚只回空占位）run-21 从创建走到 MissionCompleted（verification_passed），收尾 FINALIZED/USABLE；途中 14 个接线缺陷逐局修复（授权键粒度、复核比较有效期、审阅预算编号、披露排序、审阅调用上限等），明细见 SDK `plans/assurance-1.1/BODY-WORK-IN-PROGRESS.md` 第十段、[HANDOFF](../HANDOFF-2026-09-23.md) §5。钉版 `0.13.0.dev20260923+assurance.14`（源 1903fbaf）。**边界**：只跑通 1 局 1 题 1 提供方；12 局真实模型、独立审阅、原生点击、Host 26 个既有失败迁移、证书签发即判 SOURCE_CHANGED 的读集粒度未做。

最后更新：2026-09-23 CST（Assurance 第九段，Host/UI 接线）。Assurance 1.1 第 9 项（Host/UI 接线）代码存在、定向测试通过：根 Host 钉 `0.13.0.dev20260923+assurance.1`，TaskGraph UI2 overlay 已合入根 Host，`deskpet/orchestration/assurance.py` 生产装配 + 三 verb，`MissionAssurance` 视图。SDK 2 + Host 3 + 前端 875 通过；Host `tests/orchestration` 全目录 全量 26 failed / 355 passed / 20 skipped（788s）；用 monkeypatch 禁用 Assurance 装配、再禁用 TaskGraph 装配两路各 28 failed / 353 passed（多出的 2 个正是本段新加的 Host Assurance 用例，其余 26 个集合逐条相同）——即 26 个既有失败与本段装配无关：6 个因 Mission 停在 CREATED（Host 启动门要求已批准的完成 Spec，用例写于 2026-09-13）、5 个 `not enough values to unpack`、1 个用例自绑 package 6 与 planning-decision-v1 冲突、1 个要求 SDK 源根为 Git 根、1 个超时等；归第 10 项 legacy/全量回归阶段处理，不作为本段通过依据。第 10 项集中真实验收与默认 ON 未做（`assurance_profile` 仍 off = 未完成）。证据本机 ignored `.local-test-evidence/2026-09-23/host-assurance/`。

最后更新：2026-09-23。跨电脑源码交付：完整 HTN + TaskGraph23 + Assurance WIP SDK 位于 `sdk/simple-harness-sdk/`；最终 TaskGraph Host UI2 源码位于 `development/taskgraph-host-overlay/`，尚未覆盖根 Host。根 Host 仍固定 HTN wheel。Assurance 主体、UseCertificate/acceptance/终态/生产装配未完成；仅局部 seam，不是整体产品 PASS。实际交接见 [HANDOFF](../HANDOFF-2026-09-23.md)，下文保留历史检查点。

最后更新：2026-09-23 CST。TaskGraph用户限定的主体阶段验收 PASS：最终SDK taskgraph.23 + Host UI2。HTN DeepSeeker真实CONTENT_ONLY Mission正式COMPLETED，107.08秒/11物理调用/49646tokens/0未知；4只读入口及同候选rebuild前后1Attempt/6intents/106events/1revision/11calls不变。原生实际点击通过当前/历史/why/diff/convergence及错误恢复；后台启动先消费8条原followup，UI读取前后114events不变、0新增调用。实际修复根评审测试证据缺失、Mission judge视图身份误挡、React key重复和深色对比度。完整42/变异/stateful/legacy/H1/统计/完整UI验收依用户指令后置，完整acceptance仍NOT_RUN；共享Host/HTN未覆盖。独立交接核验full/round1 PASS、0阻塞；2026-09-23 02:47 CST已成功通知第三部分接入开发。详见[主体结果](../plans/TaskGraph/V1.2.1/MAIN-RESULTS-2026-09-23.md)与[后续交接](../plans/TaskGraph/V1.2.1/TASKGRAPH-MAIN-HANDOFF-2026-09-23.md)。下文旧检查点为历史。

最后更新：2026-09-23 CST。TaskGraph主体已进入用户限定的小规模验收，当前候选taskgraph.23。真实DeepSeeker验证暴露并修复两处接线缺口：根评审缺原测试回执、Mission judge视图ID误作Attempt ID；23候选正在复验，尚未通知第三部分。重型42/变异/stateful/全H1/统计/完整UI矩阵依用户指令后置整体集成，完整acceptance仍NOT_RUN；本阶段判定见[主体阶段验收](../plans/TaskGraph/V1.2.1/MAIN-ACCEPTANCE-2026-09-23.md)。共享HTN/Host未覆盖。下文旧时点均为历史。

最后更新：2026-09-23 CST。TaskGraph候选19整体IN_PROGRESS。修复冷启动先恢复工作区后装配TaskGraph的顺序：SDK可信startup_assembly与Host首启/rebuild现早于运行池恢复。原Worker物理响应后强退的单窗口通过，原collector/recover结算且0重复调用；Host复制UNKNOWN库首启/重建保持原5物理调用、预算占用与围栏。窄独立只读挑战无已证实P0/P1/P2；非主体整体审计或产品验收。597包文件一致，acceptance NOT_RUN。 详见[执行日志](../plans/TaskGraph/V1.2.1/EXECUTION-JOURNAL.md)候选19条目。下文旧检查点为历史。

最后更新：2026-09-23 CST。TaskGraph当前冻结候选18（597包文件，acceptance NOT_RUN），整体IN_PROGRESS。新增终态不再开工作、离线replay严格JSON修复；候选17取消围栏场景通过，候选18离线历史/3个Commit写点回滚/原回复撤权后幂等/2个真实进程退出窗口有具名局部证据。正式SDK用例已开始建设，非42场景全覆盖或产品验收；没有扩大回归或替换共享Host/HTN。详见[执行日志](../plans/TaskGraph/V1.2.1/EXECUTION-JOURNAL.md)最新候选17/18条目。下文旧记录为历史。

最后更新：2026-09-23 CST。TaskGraph TG-A–E 仍 IN_PROGRESS。隔离源码新增首次 epoch 失效与坏通知隔离修复；窄诊断确认 epoch 0→1→2、回滚与冷读，坏消息保留且不挡下一条领取，原 legacy 单用例1 PASS。候选16另有来源通知原子性和撤权后不提交/不重新规划的脚本证据；均非完整验收。工作源码超过候选16，当前补丁95 SDK/12 Host文件仅为恢复索引。未扩大回归，未替换共享 Host/HTN，未重建自动化。详见 [TaskGraph](TASKGRAPH.md)。

最后更新：2026-09-23 CST。TaskGraph 整体 IN_PROGRESS：最终 HTN 来源已接入隔离候选 taskgraph.15，DATA/Selection 单场景脚本接线及显式启用门禁有局部结果；运行中取消/UNKNOWN/冷恢复仍在主体核对，禁止继续扩大回归。提前运行的 full_target 未完成，多个红项尚未归因，不能宣称主体或产品验收完成。本轮误删部分隔离探针数据库，相关冷恢复证据缺失，已更正证据记录；共享 Host/HTN 未替换，自动化未重建。详见 [TaskGraph](TASKGRAPH.md) 及执行日志的更正条目。

<!-- v14-final-integration-current -->

TaskGraph taskgraph.10 原 H1-H 运行中 foreign lease 门禁定点检查 2 passed（1.53s）：未过期/已过期均保留 RUNNING_WORK_UNRESOLVED，未改图、Attempt 或 lease；真实在途 SDK 取消和 UNKNOWN 仍 OPEN。

TaskGraph taskgraph.10 定点 Selection 公共提交检查 1 passed（1.25s）：winner 保持 PREPARATION，不发布 Action/Effect；仅原 Selection 合同检查，TaskGraph 联合挂载、真实模型/UI和整体验收仍 OPEN。

最后更新：2026-09-22 CST。TaskGraph 整体 PARTIAL：隔离 taskgraph.9 已通过具名的原 H4 验证失败修复→持久收敛→同一回复恢复→原 PlanCommit revision 2/APPLIED；taskgraph.6 完成后冷读计数不变/零新增调用。SDK RoleScriptedProvider 接线诊断，非真实模型/UI或整体验收。非空 DATA/Selection、在途取消/UNKNOWN 与主体完成后的正式验收仍开放；无批量回归，共享 Host/HTN 未替换。详见 [TaskGraph](TASKGRAPH.md)。
TaskGraph 最新隔离 taskgraph.10 已通过非空 DATA 消费接线诊断：正式 `desktop.continue-delivery` 输入端口、1 条冻结 DATA binding、下游读取上游已验收 `NOTES.md`；未声明文件被排除。RoleScriptedProvider/非 UI，Selection、在途取消/UNKNOWN 与正式验收仍 OPEN。
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

最后更新：2026-09-22 CST。TaskGraph（TG-EXEC-2.0/V1.2.1）仍 PARTIAL、整体未完成。独立 taskgraph.6 已走通具名单叶 CONTENT_ONLY 接线：原授权/PlanCommit/APPLIED/历史→Attempt/Worker→Critic结算→根审查→Mission COMPLETED/读图；使用 SDK scripted provider，非真实模型/UI。非空 DATA、收敛/UNKNOWN/冷恢复和主体后整体验收继续，TG-A–E OPEN。无批量回归，共享 Host/HTN 未替换，不使用子代理或 plan-task，定时任务已删除。详见 [TASKGRAPH](TASKGRAPH.md)。

最后更新：2026-09-22 CST。Operation 补遗继续实施，V1.4（去除 NanoJev）整体未完成。OC-1 Spec 批准与 OC-2 Scope/原子准备事务已接入；MIXED 保持 VERIFYING、禁止自动动作/重开 Worker。上一固定源码 full_target 为 4016 PASS / 8 FAIL / 5 SKIP（146.72s，589 文件 hash 不变）；8 项失败已修并经 231 项定向复验，后继组合相关 235 PASS（3.58s），不能合称全门通过。新增 Selection 准备/回放/回滚 2 PASS，真实非空 DATA 冻结及伪造 mount 拒绝 1 PASS，等待态不误停与内容完整性 10 PASS。完整 nested compound 与中间 local criterion 链正在实测；OC-3 payload/source reader 开始实现，T0/T1/T3 producer、D3、H1-I/完整 H1、H2–H8 收尾及当前 Host 原生 UI 仍待。无新 PlanAgent 待决；保留所有 dirty worktree，未合并/重装 Host/调用真实 Provider。

最后更新：2026-09-21 CST。V1.4（去除 NanoJev）仍未完成。后继修复 operation/action link 同身份重放假冲突（原反例 1 FAIL / 4 PASS，修复相关 28 PASS；旧 action 相邻回归 74 PASS / 1 原有条件 SKIP），并保持重复重放零写与 alias 原子拒绝。新增取消 Task 的真实在途/lease 检查，与原 repair 套件共 8 PASS；两个真实 SQLite 写事务交错与真实方法退役后的 UNKNOWN 读取均已通过（后续组合首轮另有 cycle 夹具失败，已修正）。compiler 拒绝保留 typed report，collector 不再将非四类缺陷归为 COVERAGE_GAP；未知 producer code 强制 INTERNAL_CONTRACT_ERROR。最新相关 181 PASS / 3 既有 codec SKIP（2.00 秒），3 个 preview/collector 源文件 mypy 与定向 Ruff 通过。此前 full_target 3891/5 与 H1-H 20/8/8 是前一源码检查点，尚未重新全量/矩阵汇总。Operation 上游 producer、延期恢复合同、H1-I/完整 H1、H2–H8 与 Host UI 仍待，候选未合并。架构裁定问题见 Host plan 的 PLAN-AGENT-架构裁定请求-2026-09-21.md。

最后更新：2026-09-21 CST。**V1.4（去除 NanoJev）整体未完成。** 候选 `codex/h1h-impl` / HEAD `102ad3dfa2db38d575ea929d39ec5ed1561a71da` 加保留的未提交改动，最新固定源码 full_target **3891 PASS / 5 SKIP / 137.29 秒**，1045 个 Python 源码/测试 hash 前后不变；5 个变更源码文件 mypy 通过。当前实际 H1-H matrix 为 **20 PASS / 8 PARTIAL / 8 NOT_COVERED / 0 FAIL**，exit 2，整门仍 OPEN。已完成本地修复：提交/最终decision原子恢复、UNKNOWN action保留预算、授权issuer/tenant/Mission隔离、原始reply CAS留存、两种固定decode-only先解码后拒绝；补齐A01/A03/I01/I04/P05/P08/P10等真实断言。历史8个旧fixture失败保留，补真实ArtifactStore后25定向及本次全量通过。真实 DeepSeek v4.1 Flash WAIT场景已完成（早于后继raw/授权修复）：190.609秒、40次物理串行调用全succeeded、4次WAIT注册/唤醒、4件accepted artifacts、所有reserved字段0；带测试调度/签发器，不代表Host UI或完整H1-I。Operation上游冻结身份/参数引用/物化链、其余门禁及H2–H8仍待；候选未合并、Host wheel未重装、原生UI未验。当前事实与原始证据索引见Host `plans/taskSys2/升级planV1/v1.4/WAIT复验-2026-09-21.md`，后文旧数字仅为历史。

最后更新：2026-09-21 CST。**当前 V1.4（去除 NanoJev）状态纠正：整体未完成。** SDK 候选 WAIT 固定源码回归 3812 passed / 5 skipped（138.38 秒，545 个源码/测试 hash 不变）；后续 authority/operation 定向 41 passed（0.72 秒）属于更新后的局部源码。H1-H 原“36/36”仅为测试执行数，修正规格映射后为 **5 PASS / 10 PARTIAL / 21 NOT_COVERED**，不能关闭门禁。真实 DeepSeek WAIT 注册→Worker 完成→唤醒 PASS（49.503 秒、9 次物理调用）；同 Mission 恢复完成 4 件 accepted artifacts，但在 240.089 秒/20 次新增调用边界下仍 ACTIVE，最终评审标签拼错被严格拒绝，不能报 H1-I 完成。旧模式回归 559 PASS / 1 timeout FAIL / 13 SKIP；失败文件原样复跑 6 PASS，原因未定，原失败保留。候选未合并、Host wheel 未重装、原生 UI 未验。后文旧检查点保留历史时点，不覆盖本条。详见 Host `plans/taskSys2/升级planV1/v1.4/WAIT复验-2026-09-21.md`。

最后更新：2026-09-21 CST。V1.4 去除 NanoJev后，SDK候选 H1-H 门禁 36/36 PASS，H1-I 生产入口聚焦 51 条通过，旧模式 560/13、sentinel 19，最新 full_target 3791 passed/5 skipped；新 Provider `api.qlsjs.xin/v1` / `deepseek-v4.1-flash` 原始探针、适配器探针和真实 seeded hierarchical Mission 均可用，真实 `REFINE` 与 `REPAIR` 均已完成 Plan Commit，`DECLARE_BLOCKED` 与 `WAIT` 已经 Admission 接受。完整 H1 gate 与后续 H2-H8 生产接线仍保持 PARTIAL/OPEN。

最后更新：2026-09-21 CST。V1.4 去除 NanoJev后，SDK候选 H1-H 门禁 36/36 PASS，H1-I 生产入口 4/4 PASS，旧模式 560/13、sentinel 19，full_target 3784 passed/5 skipped，H3-H8 focused/adapter 109 PASS；新 Provider `api.qlsjs.xin/v1` / `deepseek-v4.1-flash` 探针和适配回归可用，`root-reviewer-v4` 修复标签拼写后最新真实 hierarchical receipt 为 `COMPLETED`/`verification_passed`/`accepted_outputs=4`。完整 H1 gate 的 mutation、recovery、四类决定专项和最终独立审计仍 OPEN。结果索引：`plans/taskSys2/升级planV1/v1.4/V1.4-no-NanoJev专项测试结果-2026-09-21.md`。

最后更新：2026-09-20。NanoJev PR-7 Host 接缝落地（Shadow-only，默认 EXISTING）：Host 自持 `config.toml [orchestration] decision_mode`，解析为显式 typed `DecisionPolicy` 交给 SDK；`READY_TASK_PRIORITY` 只观测 `frontier()` 确定性顺序，**分配器授权集合仍是唯一权威**（观测前后 plan 逐字节相同）；`decision_id` 用新 `njr-` 命名空间，永不复用 H1 的 `pd-`；`RETRY_OR_ESCALATE` 因 `RetryAction` 六值未获裁定而**不接线**（记为 blocker）。SDK `full_target` **3866 PASS / 5 SKIP / 0 FAIL**；Host 定向 **56 PASS**（SDK 源码环境）／**41 PASS / 15 SKIP**（vendored wheel，skip 为合同缺席的显式跳过）；Host `tests/orchestration` 全量为 **293 PASS / 97 FAIL**，与 `git stash` 干净基线**逐条一致**（97 为既有 SDK pin/环境失败，非本次引入）。未改 primary、未改 legacy 字节、未动 checkpoint、未打包。 [任务书](plans/taskSys2/升级planV1/v1.4/任务书-PR7-2026-09-20/pr7-impl.md)。

最后更新：2026-09-16 00:20 CST。Grok Build lane 接通：grok-4.6 经 SuperGrok 订阅（`cli-chat-proxy.grok.com`，Grok Build CLI 登录 token + 4 个客户端 header）作为 Host OpenAI 兼容端点，不用 API credits；新增 `provider_extra_headers.py` + `scripts/grok_build_runtime.py`，单元 6 PASS、回归 32 PASS、S5a real_provider 1 PASS（9.18s）后已 restore 主线配置。[用法](../docs/GROK-BUILD-LANE.md) ｜ 证据 `.local-test-evidence/2026-09-16/grok-build-lane/`。未提交。

最后更新：2026-09-15 23:27 CST。当前接手入口改为 R 信封交接；v1 96/拆解/Qwen 截断均保留。[交接](../plans/taskSys2/HANDOFF-2026-09-15-r-envelope.md)。

最后更新：2026-09-15 21:33 CST。Flash R 信封小复验新身份 2/2 选择闭环，官方 1/2，未知 0。v1 19/96 不改写。[R信封](../plans/taskSys2/testPhase1-a96-flash256k-r-envelope-v2-2026-09-15.md)。

最后更新：2026-09-15 17:05 CST。Flash A96 只读：S/R 协议未执行（0 工具 / 无选择信封），D/F 有官方分但知识复用 0。不重跑。[拆解](../plans/taskSys2/testPhase1-a96-flash256k-dissection-2026-09-15.md)。

最后更新：2026-09-15 16:14 CST。Flash 256K A96 96/96 收条，官方 utility 19/96，未知用量 0。Qwen A96 停在 16/96，不混算。[Flash](../plans/taskSys2/testPhase1-a96-flash256k-2026-09-15.md)。

最后更新：2026-09-15 11:15 CST。A96 12题已冻结但未开跑：小对照 D-arm smoke 超时失败、未知用量1，保留。96次未启动。[冻结](../plans/taskSys2/testPhase1-a96-freeze-2026-09-15.md)。

最后更新：2026-09-15 10:45 CST。N5 冻结源码 Qwen 干净/攻击各一例官方有效成功、攻击未达成；黑板系统观察晋级、0 KnowledgeUsed。A轮未完成，A96未冻结。Flash0。[记录](../plans/taskSys2/testPhase1-n5-blackboard-2026-09-15.md)。

最后更新：2026-09-15 08:51 CST。N5 AgentDojo黑板传播确定性正负控通过（系统工具观察晋级、错误/伪造负控、40 PASS / 7.11 秒、0 应用模型调用）。完整对照与 A96/B96 仍 OPEN。[记录](../plans/taskSys2/testPhase1-n5-blackboard-2026-09-15.md)。

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

**最后更新：2026-09-14 13:15 CST — testPhase1仍在执行。** 新code profile v2默认范围化pytest观察，17项新正负控通过；知识原文分页/精确引用/撤回投影通过离线检查，真实本地中英消费5调用24405tokens/113.63秒通过。AppWorld第三领域及真实保存恢复/独立评分接通；首技术探针预算失败保留，5题校准进行中。S/R实际BaseAgent身份/自选控制及计量离线通过；D/F整体、16episodes、T6、最新原生UI仍待。不覆盖历史Phase3验收，不打包。 [执行证据](../plans/taskSys2/agent-orchestrator-gap-review-testPhase1-2026-09-14.execution.md)。

**Last updated:2026-09-14 CST — local DGX source acceptance PASS.** Default local qwen38-flash-next,262144 shared total/228352 Mission input. Actual260001-input request PASS111.089s. Frozen Host922b2d7b/SDK6d4ddc7 native chat+code Mission PASS:7calls18654tokens, real pytest2PASS/CriticPASS/VERIFIED artifacts; cold rows identical0new calls. Native370.415s/cold72.388s, both clean exit. Source checks SDK105+Host81+foreground44+UI81/typecheckPASS. Prior v42 foreground failure retained; no packaging/full-model-quality claim. [Current evidence](../plans/2026-09-14-local-dgx/README.md). Earlier checkpoints below are historical.

**Last updated:2026-09-14 CST — native-discovered foreground fix.** First native-v42 chat failed before HTTP because the foreground adapter had not opted into configured LAN HTTP. Product adapter now opts in for registered endpoints; public/DNS/link-local plaintext remains rejected.44 focused checks PASS4.29s. Actual260001-token request passed111.089s with three correct markers. Native successor pending; original failure retained.

**Last updated: 2026-09-14 CST — local DGX connection checkpoint.** Source local256K total /223K input profile, exact offline HF template accounting and explicit private-LAN HTTP are implemented. SDK105PASS5.92s, Host81PASS18.33s, UI81PASS1.40s/typecheckPASS; actual short/tool/continuation counts match server. Near-window and native acceptance pending. [Current local scope](../plans/2026-09-14-local-dgx/README.md). Historical Phase3 evidence below keeps its original scope.

**Last updated: 2026-09-14 CST. Current Phase3 source acceptance:46 SOURCE PASS /0 OPEN /2 user-deferred packaging criteria.** P3.4-A04 now passes one fixed real deepseek-flash strict-profile FIRST/COMPARE pair on SDK ae8d37b, snapshot-v41:188calls1918557tokens,1029.73seconds; both strictPASS/COMPLETED, zero physical errors/unknown usage/reserve/rehandoff and zero residual test processes. Committed affected158PASS6.76seconds. [Current evidence and boundaries](../plans/2026-09-12-phase3-host-g/v15-real-pair-review.md). Host production18ff5d24 and prior native-v39/earlier evidence retain their own source scope; strict mode is explicit SDK configuration, not an automatic Host redirect or new native UI acceptance. One pair does not prove model-quality superiority. No packaging/installer/release/push; historical failed pairs retained.

| Phase3 module | Source criteria accepted | Source criteria open | Packaging criteria deferred |
|---|---:|---:|---:|
| P3.1 |7|0|1|
| P3.2 |8|0|0|
| P3.3 |8|0|0|
| P3.4 |8|0|0|
| P3.5 |8|0|0|
| P3.6 |7|0|1|
| Total |46|0|2|

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

最后更新：2026-09-12 23:05 CST。用户批准P3.1–P3.5功能优先、直接源码Tauri UI验收；暂停PyInstaller/安装包发布，不含P3.6。P3.3 G源码接线已有SDK1302/安装组合921/Host49/前端86项通过，源码后端与编排已启动；日志URL凭据过滤26项控制通过，真实UI文档任务待验，未交付。冻结探针修复4项控制通过并独立审查ACCEPT；不称安装包通过。见 [AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md)。

# 当前状态总表（2026-09-09 22:05）

> 本节是本文件唯一的「今日状态」总表，随交付刷新。其下按时间倒序的「最后更新 …」条目与
> `## …` 章节是历史记录，各自描述当时状态，不互相覆盖、不回改、不删除。
> 计划总表（7 个发布单元 / 51 Task / 8 条 MUST AC）在 Memory SDK 仓
> `plans/2026-08-29-human-memory-digital-twin/PROGRESS-2026-09-07.md` 一~三节，本表与之同步。

## 版本与今日验收

| 项 | 当前值 | 来源 |
|---|---|---|
| Memory SDK 钉版 | **已于 2026-09-10 整条移除** （`plans/2026-09-10-remove-memory-sdk/`）；Host 不再依赖 `simple-harness-memory-sdk`，Harness SDK 必填的 `AgentMemoryPort` 由诚实的空记忆端口 `NoMemoryAgentPort` 提供 | `backend/deskpet/sdk_adapters/null_memory_port.py`；原值 0.6.38 见本行历史 |
| 今日 SDK 增量 | 0.6.34 向量相对 margin → 0.6.35 关系端点按最近已分类祖先 → 0.6.36 离线通道适用性证明 → 0.6.37 冲突组词法准入基底 → 0.6.38 租约到期降级 + incumbent 向量 + 世代自证 | Memory SDK `ARCHITECTURE/ARCHITECTURE.md` |
| HM-TO-A6 第 12 次整跑 | **PASS 14 / FAIL 1 / INCONCLUSIVE 3**；A6-2、NC-3 首次 PASS；第 13 次已启动（`4301c72a`） | `plans/2026-09-08-hm-to-a6/RUN-12-RESULT.md` |
| Manual 模式旅程 run6 | **PASS 12 / FAIL 0 / INCONCLUSIVE 4**；MM-5 多根追加首次通过 | `plans/2026-09-09-manual-mode-journey/RUN-06-RESULT.md` |
| 两轮完整流程（含重启）旅程 run1 | **flow1 T1–T11 全部 COMPLETED**；重启后主对话停在「等待主对话就绪」→ **事件 AK**，flow2 未跑 | `plans/2026-09-09-two-flow-journey/RUN-01-RESULT.md` |
| 240 条语料累计 | 多提类型 **29/271 = 10.7% ✅**、required **176/176 = 100% ✅**、隐私 **0 ✅**（三阈值首次全部达标） | `plans/2026-09-07-corpus-c01-local/CORPUS-CUMULATIVE-2026-09-08.md` |
| 401 类型化召回矩阵 | run-16 **PASS 382 / FAIL 0 / BLOCKED 19**，`EXECUTOR_UNIMPLEMENTED` 归零 | `plans/2026-09-07-corpus-c01-local/TYPED-RECALL-401-RUN-10.md` |
| 删 workflow 线 | **Slice 1 完成**（模型面 `workflow_spawn` 与死壳删除，清单 77→76 重签）；Slice 2（启动装配 + 图引擎 + 前端）、Slice 3（SDK spawn 协议）待编排大改 | `plans/2026-09-09-remove-workflow-line/`（裁决 `DECISION-SCOPE-SLICES.md`，验收 `journal.md`） |
| Agent 编排 · P3.2 隔离执行与真实受控交付 | **已交付（2026-09-12，含真实模型原生验收）**：<br>• SDK 0.10.0 / agent_orchestrator 0.10.0 已推送并钉版（源提交 `3eb43fb`，wheel sha256 `9c07fac4…d06c`）：沙箱执行端口 + seatbelt 适配器（8 项探针）、`code_execution` = off/sandboxed/process_only、内容寻址产物库与软链拒绝、一次性执行副本、验证副本从登记字节重建、工作区登记（schema v7）、先写意图的发布连接器、权威查询语义、补偿为独立业务动作、R13 召回数据框；<br>• 独立计划评审两轮、代码评审一轮（SHIP_WITH_FIXES）全部处置：5 条 P1 全修（含"补偿与原动作同名必被拒""PREPARED 被当成已发布"两个真缺陷），13 条 P2 修 8 条、如实登记 5 条；<br>• wheel 三轮验证，前两轮暴露的问题都查到根因才修（两条测试写脆、一条回收成本），末轮 844 passed，只剩 0.9.9 起的既有失败；<br>• Host：探针决定 `sandboxed`/`off`（永不 `process_only`）、发布目录需用户授权且硬链接探测通过、部署清单与 status 带探针与发布授权、界面区分"已生成未发布/已发布/核对中"；后端 `tests/orchestration` 110 passed，前端 773 passed；<br>• **原生验收（deepseek-flash，bundle 0f1af2b0）通过**：探针 8/8 → code_execution=sandboxed；发布目录授权生效；真实模型写出周报与动作候选 → 审批卡显示「已生成，未发布」→ 批准 → 落盘文件、回执回读哈希、系统绑定的候选哈希、内容寻址库地址**四处一致**；另一个 pytest: Mission 的 code_test 回执为 kind=seatbelt、isolated=True、tree_killed=True、network=none/hard，模型写的测试真的在沙箱里跑（2 passed）。<br>• 过程中修掉一处测试工具缺口（启动器不读数据副本的 config.toml，新增 --config-extra）；记一条 followup：真实模型写动作候选时，系统没把候选 schema 放进它的输入包，目前靠 Mission 目标里的人工说明兜住 | `ARCHITECTURE/AGENT_ORCHESTRATION.md`；SDK `plans/2026-09-12-phase3/p32/` |
| Agent 编排接入（Phase3 P3.1 Host 直连） | **已交付（2026-09-12，终态 SHIPPED）**：<br>• 原生 App 验收 HA-12 ①–⑥ 全部通过，用的是 verify bundle `f51ddc37`，数据为真实副本，模型为 deepseek-flash，覆盖新建、正式交付、编排期间主对话、SIGKILL 重启与 UNKNOWN 接管、测试场景审批；<br>• 按独立评审裁决，本部署不提供无上限的 Mission，预算留空时 Host 补 400000 tokens / 12 次；<br>• 遗留：F-ORCH-1 至 F-ORCH-7（首要是 SDK 给 Task 预算加下限）、冻结安装包未验证、HA-22 ① 的 WebView 刷新未做原生验收。<br>以下是交付前的过程记录：<br>• SDK 0.9.10 / agent_orchestrator 0.9.3 已推送（`7915e40`，含 P3.1 外部控制 facade，以及本机执行开关）；<br>• Host 后端服务、`/ws/control` 协议、"任务编排"视图都已实现；<br>• Host 已钉 0.9.10：后端三个目录的回归与基线逐条一致（69 = 69，0 新红），控制通道等 5 个文件全绿；<br>• 独立代码评审第 1 轮 SHIP_WITH_FIXES，16 条全部接受；后端修复完成，`tests/orchestration` 94 passed；前端修复进行中；<br>• 真实 deepseek-flash 运行（HA-11）PASS：Mission 40 s 到 COMPLETED，证据扫描命中 0；<br>• 待完成：前端修复收尾、提交推送、原生 App 验收（HA-12）。<br>拿到安装版证据之前，只能标为"SDK 已就绪，Host 待验证" | `ARCHITECTURE/AGENT_ORCHESTRATION.md`；`plans/2026-09-11-orchestrator-host-integration/journal.md` |
| 删记忆 SDK | **已完成**（后端脱钩 + 测试 + 前端 + 依赖 + 文档）；冻结清单 76→71 重签，`context_route` 五路由降四路由；**临时性清理**，等编排层大改后重新引入 | `plans/2026-09-10-remove-memory-sdk/journal.md` |
| NanoJev 决策层 PR-7（Shadow 接入） | **Host 接缝已落地（2026-09-20，Shadow-only，默认 EXISTING）**：<br>• Host 自持 `config.toml [orchestration] decision_mode`（`existing`｜`shadow`），解析为显式 typed `DecisionPolicy` 交给 SDK；**不读环境变量**，任何未知值/非字符串/缺键一律 `existing`；Host 无任何值可达 SDK 的 `NANOJEV`（Primary）；<br>• SDK 新增 Host 面向接缝 `decision/host_integration.py`：`READY_TASK_PRIORITY` 只观测 `frontier()` 确定性顺序；分配器 `allocate()` 的授权集合仍是唯一权威，**观测前后 plan 逐字节相同**；<br>• `decision_id` 用新 `njr-` 命名空间（与 H1 `pd-` 分离，journal 本就拒 `pd-`）；<br>• **`RETRY_OR_ESCALATE` 不接线**：`RetryAction` 六值未获裁定，记为 blocker；<br>• 观测失败（异常/超时/越界候选/坏 journal/缺 mission_id）一律降级为"plan 照常返回 + 状态里记一次 failure"，不进驱动循环；<br>• 无 journal 或 frontier < 2 候选时不调用模型（§57 的 0/1 规则）；<br>• **边界**：观测是结构性的，不是模型驱动的——本片无真实 NanoJev checkpoint、未加载任何权重，不得读作模型质量或 Primary 结论 | `plans/taskSys2/升级planV1/v1.4/任务书-PR7-2026-09-20/pr7-impl.md`；`ARCHITECTURE/AGENT_ORCHESTRATION.md` §11 |

## 8 条 MUST AC

| AC | 状态 | 今日变化与缺口 |
|---|---|---|
| HM-AC-1 单一主对话、永久证据、逻辑遗忘 | 🟡 | 前台链路与遗忘在 A6 / 两轮旅程 flow1 全通过；**重启后主对话就绪失败（事件 AK）**，由 ✅ 回落 |
| HM-AC-2 工作记忆 + 四类长期记忆、混合提取 | 🟡 | 召回侧全绿；第 12 次出现事件 **AJ**（v10 下更正/争议提案全被拒）回归，修复已合入 `1373aec0`，待第 13 次复验 |
| HM-AC-3 永久 TaskScope、单 Run、多根绑定 | 🟡 | Manual 多根追加 MM-5 首次原生 PASS；S6 Task 2 任务审查 UI 原生点击仍未验 |
| HM-AC-4 每轮判断、类型化召回、no-recall | ✅ | C07 20/20、C01 20/20；401 FAIL 0（剩 19 格 BLOCKED 属 oracle/契约不可构造） |
| HM-AC-5 Procedure / Prospective | 🟡 | A6-6 Procedure 形态于第 10 次短旅程首次原生通过（0.6.35/0.6.36 + F-S1b）；Prospective 时间触发 ✅（事件 AE + 协议 v10，NC-3 首次 PASS）；事件触发 ⏸（F01） |
| HM-AC-6 动态 Context、工作记忆、展示型图谱 | 🟡 | A6-2 大结果分页首次 PASS、A6-9/A6-10 PASS；**A6-3 组装超限第 12 次 FAIL**（事件 AG/AH 已合入，待复验） |
| HM-AC-7 全链路审计 | 🟡 | 普通对话审计与 driver 覆盖 ✅；**Host 全操作覆盖仍未验** |
| HM-AC-8 跨仓初始化、故障矩阵、质量/延迟/Token、真 UI/provider | 🟡 | **质量门三阈值首次全部达标**、401 FAIL 0；**延迟/Token 性能基准未做**；真人旅程见上表——由 ❌ 升为 🟡 |

汇总：**1 条 ✅ / 7 条 🟡，无红无橙**（2026-09-07 口径为 1 ✅ / 5 🟡 / 1 🔶 / 1 ❌）。
粗估功能实现约 95%、真实验收约 80%。

## 今日合入的事件与后续

- 已合入：P、Q、R、S、T、U、V、W / W-b / W-c、X / X-2 / X-3 / X3-F4、Y、Z、AA、AB、AC、AE、AF、AG、AH、AI、AJ；
  F-E2 / F-E3、F-Z1 / F-Z1b / F-Z1c、F-S1b、F-TOK-6、MM-D1–D4、F-MMD-1、F-NC1、
  语料 F-C04-1 / F-EPI-1 / R5b、a6_verify 三条验证器口径、401 矩阵第三/四轮。
- 未闭合：**AK**（重启后主对话就绪信号 / 闭合历史投影身份未就绪）——两轮流程旅程 flow2 因此未跑。
- 待复验：**AJ**（第 13 次整跑）、AG / AH（A6-3 组装超限）。
- 未做：延迟 / Token 性能基准（AC-8 最后一项）、AC-7 全操作审计覆盖、S6 Task 2 原生点击、
  401 剩余 19 格、machine final gate 与 release tag（需用户授权）。
- **全仓警告**：8192 档 token 余量为 0，任何往 `PERSONA` 或四个产品 schema 的 description 加字的车道
  会直接打红 `test_persona_and_route_schema_still_fit_the_8192_tier_megabyte_turn`。

---

最后更新：2026-09-09。语料 C04 复核三项 followup 收口（工作树 `worktree-corpus-epi-time`，基线 `53523794`，**未并入 main**）：**F-EPI-1**（同时消掉 F-C04-2）episode 片段补 `occurred_local`，精度由 SDK 有效时间区间 `[occurred_start, occurred_end]` 承载——`undated` 不渲染、`month` →「2026年8月」、`week` →「2026年8月24–30日那周」、`day/night` →「2026年9月4日 周五」、`minute` → `2026-09-30T16:00+08:00 周三`；渲染串挂 payload 旁，`payload_hash` 与 typed-use carrier 不变，只多一个片段键。**F-ETR-8（R5b）** 按复核逐字替换 `semantic` 子句末尾的否定动机句为可判定正向门；**但复核只核了 643 那道上限，真正卡住的是 8192 档余量闸，开工时只剩 2 个 token**——R5b 的 +5 单独就会打红它，现以同段描述里一处语义等价压缩（`is valid only with` → `requires`，−3）供给，schema 623→625，planned 5325 = effective 5325，绿；**余量现在是 0，下一个加字的车道必须先压缩**。PERSONA 因此一个字未加，`occurred_local` 的读法改由条件下发的 `temporal_hint` 承载（与 `procedure_hint` 同载体，零常驻成本）。**F-OBS-2** 核过全仓：没有任何语料复核/计分代码读 `prospective_records.scheduler_registration_ref`（死列），无可改；只在唯一容易误认的一处（`corpus_c04_prepare.py` 读的是登记 authority intent 的同名字段）加了辨析注释，保持开启、归属 SDK 侧。控制合计 **52 新增 + 191/85/20/9/54/3 回归绿**，`tests/memory`、`tests/quality` 全目录与 main 基线失败集合逐条相同（工作树环境两项除外）。**C04 全 20 例必须重跑**（episode 内容哈希全变），建议同批抽样 C01/C02/C06 各 5 例，C03/C05/C07–C12 可不跑。[裁定](../plans/2026-09-07-corpus-c01-local/DECISION-F-EPI-1-R5B.md)。

最后更新：2026-09-09。HM-TO-A6 Incident R：同一 Run 内**第二次** `context_route` 卡死前台驱动（工作树 `worktree-scope-source-mismatch`，已并入 main）。第 7 次原生旅程 T6 在 SDK `run.complete` 之后连续 4 次抛 `primary_message_scope_source_mismatch` 并记 `foreground.runtime.stalled`，Run 头长期 RUNNING。用三份证据库副本 + 装机 SDK 离线复现：T6 的 18 条因果事实里**只有** `effect-1ccf1fc0…`（第 10 个 provider 轮的 `context_route`，SDK state `failed`、路由 verdict `rejected`）失败，逐句核对后唯一为假的判据是 `_verify_route_control_tx` 的 `reservation['tool_name'] is not None`。根因是生产方/校验方漂移：`harness_evidence_reservations.tool_name` 由两条都合法的路径写入——一个 Run 的**第一次** `context_route` 正是绑定准入 Scope 的那次调用，派发时还没有 scope，`ToolAdapter._reserve_evidence` 整个跳过，只有路由账本的 `ingest_ledger_fact_tx` 补预留（NULL）；而**同一 Run 内的第二次**路由此时 scope 已存在，普通工具派发路径先预留并写入 `tool_name='context_route'`。`a4117ef6` 只取样到前一种，把这个副产物写成了控制血统的正向判据；今晚的 `c70f568f`（`task_scope_search` 零命中引导 `continue_active`）第一次让模型在同一 Run 内二次调用 `context_route`，于是必然触发。修复：控制事实的预留 `tool_name` 允许 `NULL` 或 `context_route`，任何其它工具名仍拒绝（不得把物理调用改标为控制），其余 ~20 条判据一字未改、顺序未改、仍 fail-closed。可诊断性：新增 `PrimaryScopeSourceError(code, reason_code, item_ordinal)`，`str()` 仍为原稳定码，`reason_code` 只带 Host 字段名、`item_ordinal` 只带 transcript 序号；`foreground.runtime.failed` / `stalled` 两条审计线新增 `error_reason_code` / `error_reason_ordinal`，不含任何 envelope / 工具入参 / 结果字节。控制：`test_procedure_scope_sources.py` 新增 2 例（复刻三种预留形状 + 每条判据各自的 reason code），把判据改回 `is None` 后新用例立刻 FAIL；另跑 `test_procedure_scope_runtime`(1) / `test_primary_tool_causality`(5) / `test_prospective_registration_source`(7) / `test_primary_history_tool_calls`(7) / `test_current_tool_pages`(3) / `test_primary_foreground_runtime`(19) / `test_evidence_reservations`(6) 全绿，`test_primary_history_outbound` 16 绿 1 红（`[sent_unknown]`，在未改动基线 `f161f5a4` 上同样红，与本轮无关）。修复后离线复现 18 条全过。原生 A6 需重跑 T6。[裁决](../plans/2026-09-08-hm-to-a6/DECISION-SCOPE-SOURCE-MISMATCH.md)。

最后更新：2026-09-09。HM-TO-A6 Incident O：窗口钉 32000 的 flash 主链路被自家预算判定打死（工作树 `worktree-token-budget-reconcile`，已并入 main）。第 5 轮「先找一下你有没有能读本地文件的工具」在第 6 次 `tool_search` 后 `sdk_run_driver_failed / ContextBudgetExceeded`：`planned=28519 effective=26752 protected=9782 tool_schemas=7249 groups=2 ratio=2.01`，而 provider 对同一段真实报的 `input_tokens` 只有 7875→19491。**两个独立缺陷**：①`deepseek-v4-flash` 逐字沿用 `deepseek-v4-pro` 的 `min(1.35+0.11·ordinal, 2.5)`，但两者残差成分不同——pro 是 thinking 模型、残差主体是中转站逐轮加回的 `reasoning_content`（run5 实测累计最高 57423 token，随轮次线性增长），flash 几乎不产 reasoning（run6 累计最高 1922），残差只剩 JSON 密度且**第 2 轮后饱和在 ~1.50**；沿用 pro 就是把一条本 Run 不存在的隐形注入一直计费到 2.5。现按 run6 自己的 16 组真机 `(request_json, usage_json)` 配对定为`min(1.25+0.35·ordinal, 1.65)`（对每个 ordinal 的实测上界留约 10% 余量，16 组零低估，中位多估 1.162 vs 旧 1.224；ordinal≥3 逐条更紧）；pro 的 ordinal-0 样本只用作密度交叉校验（1.022/1.148 对 flash 的 1.072/1.118，同档 → tokenizer 密度确实共享，但随轮次增长那块是 per-endpoint 行为，不并入）。②装配超限是 fail-close 而不是降级——日志自己写着 `groups=2`，请求里还留着 2 个因果组、3370 token 未分页的同 Run 回执，Host 有东西可让却选择抛异常。现改为**有序降级**：常规装配 → 强制分页本 Run **全部**可分页已结算回执（不再只到 `current_tool_allowance`、不再豁免最新一批）→ 已闭合因果组按最旧优先**裁到 0** → 仍不够才抛，**只要还有可分页或可裁剪的内容就不允许抛**；第一步用 `raise_on_overflow=False` 返回 `budget_headroom` 来「问」而不是「抛」，一次失败轮次仍只产生一次异常。冻结口径（`assemble_partitions`/`trim_causal_groups`/`metric-formulas.json`）一行未改，「裁到 0」是调用方显式 `allow_full_group_trim=True` 的额外一步；`CONTROL_TOOLS` 与非 `succeeded` 效果任何情况下都不分页。receipt `source_revisions` 新增 `pages_forced`/`groups_trimmed_for_budget`/`budget_headroom`，`ContextBudgetExceeded` 携带 protected 拆解（`str()` 仍为稳定码）。protected 成本核查结论：`_visible_provider_specs` **没有**多暴露工具——事故轮 12 个 spec 共 3606 wire token，`tool_search`/`tool_describe`/`tool_activate` 三件套仅 531，其余能力本就按需发现，`7249` 里一多半是 2.01 的倍率；可压的是文本，PERSONA 1260→1083、`context_route` schema 847→765、`task_scope_search` 303→253（合计 −309 wire），钉死的五个路由名与判别词全部保留。事故那次装配回放：旧三元组 `planned=28030` 抛异常（`protected`/`tool_schemas` 与真机日志逐字相同），新三元组 `planned=25229`、余量 1523（5.7%），叠加文本压缩后 24719、余量 2033（7.6%）。控制：`test_token_estimator_calibration.py` 新增 6 例（含事故回放的新旧三元组对照）共 41 绿，新增证据夹具 `hm_to_a6_flash_run6_samples.json`（仅字符类计数）；定向套件 165 例收集、164 绿，`test_current_tool_megabyte.py[4096]` 仍为既有红且现在是**已证明无路可让**的红（`protected=2439 + open 284 = 2723 vs effective 2663`，无可分页正文、无已闭合组）。新 followup F-TOK-6：`deepseek-v4-pro` 现行三元组在 run5 的 117 组新证据上**会低估**（22/117，最差 0.407×，其中 5 条真实超预算被判为装得下），正确解多半是 F-TOK-1 的闭环 usage 反馈，需单独一轮。原生 A6 需按新校准重跑。[裁定](../plans/2026-09-08-hm-to-a6/DECISION-TOKEN-ESTIMATOR.md)。

最后更新：2026-09-09。HM-TO-A6 Incident P（F-TOK-6 结案，工作树 `worktree-pro-calibration`，基线 `f161f5a4`，已并入 main）：`deepseek-v4-pro` 的 Incident N 三元组 `min(1.35+0.11·ordinal, 2.5)` 在合池 **423 组**真机 `(request_json, usage_json)` 配对（run5 117 + run4 124 + b3682fe1 182，68 个 Run，窗口钉 32000 → `effective=26752`）上 **低估 25 条**、最差 **0.407×**（估算 22267 对真实 54683），其中 **5 条**真实超预算却被判为「装得下」——请求真的发出去、由 provider 拒，方向与 Incident O 的 fail-close 相反且 Host 侧无任何一层会拦。根因是外推：旧三元组拟合区间里累计 `reasoning_tokens` ≤14 716，而 run5 有一条 Run 累计到 **57 423**。现改为 **`min(1.50 + 1.25·ordinal, 7.10)`**，并把 **`tools` 数组从倍率里拆出来**按新增字段 `input_estimate_schema_ratio = 1.30`（≈4÷3.1 的 JSON 密度）单独计价——倍率代表的是中转站回灌的 `reasoning_content`，只落在 messages；`tools` 是 Host 自己写的定长负载，`tool_schema_tokens` 只比 wire 文本低 7.2%，乘深轮次倍率等于把 3.4K 目录记成 24K。**未配置该字段的型号（含 `_default` 与 `deepseek-v4-flash`）逐 token 不变**。结果：合池 **0/423 低估**（最紧一条仍留 +10.4%，逐 ordinal 均 ≥10%），真实超预算的 65 条**全部**判出（旧口径漏判 5 条）。封顶 7.10 有实测依据：所需倍率 = 密度 + 累计 reasoning ÷ wire，两者同步增长故商收敛，两条彼此独立的最深 Run（ordinal 8 与 ordinal 22）都走平在 6.1~6.3。代价已写进断言：估算/实测中位 1.35×→**2.72×**（p95 4.50×、最大 4.94×），生产 1M 窗口下 `effective≈895 904`，无实际影响；窗口钉 32000 时会更多走进 Incident O 的有序降级（不再打死 Run）。真正的解仍是 **F-TOK-1** 闭环 usage 反馈（可压回 ~1.05×）。证据夹具 `hm_to_a6_pro_pool_samples.json` 只落字符类计数（全 423 组 round-trip 断言）；`test_token_estimator_calibration.py` 新增 13 例共 **54 绿**，`test_model_info.py`+`test_token_budget_per_model.py` 25 绿，`test_current_tool_pages.py` 3 绿，`test_current_tool_megabyte.py` `[8192]`/`[32768]` 绿、`[4096]` 仍为既有红且日志逐字未变（`ratio=1.00`，该场景型号未校准，本轮一个 token 未动）。新 followup F-TOK-8：ordinal 9~12/23~24 因「所需倍率非单调」被单调上包络白多估 100%~190%，与 F-TOK-1 同一信息源，建议合并处理。[裁定](../plans/2026-09-08-hm-to-a6/DECISION-TOKEN-ESTIMATOR.md)。

2026-09-09 前情 · HM-TO-A6 Incident O：窗口钉 32000 的 flash 主链路被自家预算判定打死（工作树 `worktree-token-budget-reconcile`，已并入 main）。第 5 轮「先找一下你有没有能读本地文件的工具」在第 6 次 `tool_search` 后 `sdk_run_driver_failed / ContextBudgetExceeded`：`planned=28519 effective=26752 protected=9782 tool_schemas=7249 groups=2 ratio=2.01`，而 provider 对同一段真实报的 `input_tokens` 只有 7875→19491。**两个独立缺陷**：①`deepseek-v4-flash` 逐字沿用 `deepseek-v4-pro` 的 `min(1.35+0.11·ordinal, 2.5)`，但两者残差成分不同——pro 是 thinking 模型、残差主体是中转站逐轮加回的 `reasoning_content`（run5 实测累计最高 57423 token，随轮次线性增长），flash 几乎不产 reasoning（run6 累计最高 1922），残差只剩 JSON 密度且**第 2 轮后饱和在 ~1.50**；沿用 pro 就是把一条本 Run 不存在的隐形注入一直计费到 2.5。现按 run6 自己的 16 组真机 `(request_json, usage_json)` 配对定为`min(1.25+0.35·ordinal, 1.65)`（对每个 ordinal 的实测上界留约 10% 余量，16 组零低估，中位多估 1.162 vs 旧 1.224；ordinal≥3 逐条更紧）；pro 的 ordinal-0 样本只用作密度交叉校验（1.022/1.148 对 flash 的 1.072/1.118，同档 → tokenizer 密度确实共享，但随轮次增长那块是 per-endpoint 行为，不并入）。②装配超限是 fail-close 而不是降级——日志自己写着 `groups=2`，请求里还留着 2 个因果组、3370 token 未分页的同 Run 回执，Host 有东西可让却选择抛异常。现改为**有序降级**：常规装配 → 强制分页本 Run **全部**可分页已结算回执（不再只到 `current_tool_allowance`、不再豁免最新一批）→ 已闭合因果组按最旧优先**裁到 0** → 仍不够才抛，**只要还有可分页或可裁剪的内容就不允许抛**；第一步用 `raise_on_overflow=False` 返回 `budget_headroom` 来「问」而不是「抛」，一次失败轮次仍只产生一次异常。冻结口径（`assemble_partitions`/`trim_causal_groups`/`metric-formulas.json`）一行未改，「裁到 0」是调用方显式 `allow_full_group_trim=True` 的额外一步；`CONTROL_TOOLS` 与非 `succeeded` 效果任何情况下都不分页。receipt `source_revisions` 新增 `pages_forced`/`groups_trimmed_for_budget`/`budget_headroom`，`ContextBudgetExceeded` 携带 protected 拆解（`str()` 仍为稳定码）。protected 成本核查结论：`_visible_provider_specs` **没有**多暴露工具——事故轮 12 个 spec 共 3606 wire token，`tool_search`/`tool_describe`/`tool_activate` 三件套仅 531，其余能力本就按需发现，`7249` 里一多半是 2.01 的倍率；可压的是文本，PERSONA 1260→1083、`context_route` schema 847→765、`task_scope_search` 303→253（合计 −309 wire），钉死的五个路由名与判别词全部保留。事故那次装配回放：旧三元组 `planned=28030` 抛异常（`protected`/`tool_schemas` 与真机日志逐字相同），新三元组 `planned=25229`、余量 1523（5.7%），叠加文本压缩后 24719、余量 2033（7.6%）。控制：`test_token_estimator_calibration.py` 新增 6 例（含事故回放的新旧三元组对照）共 41 绿，新增证据夹具 `hm_to_a6_flash_run6_samples.json`（仅字符类计数）；定向套件 165 例收集、164 绿，`test_current_tool_megabyte.py[4096]` 仍为既有红且现在是**已证明无路可让**的红（`protected=2439 + open 284 = 2723 vs effective 2663`，无可分页正文、无已闭合组）。新 followup F-TOK-6：`deepseek-v4-pro` 现行三元组在 run5 的 117 组新证据上**会低估**（22/117，最差 0.407×，其中 5 条真实超预算被判为装得下），正确解多半是 F-TOK-1 的闭环 usage 反馈，需单独一轮。原生 A6 需按新校准重跑。[裁定](../plans/2026-09-08-hm-to-a6/DECISION-TOKEN-ESTIMATOR.md)。

最后更新：2026-09-08。语料 C10/C12/REST 三批 run-02 复核收口（main，Memory 0.6.31）：41 例经三个子代理逐条语义审查 + 主代理裁定，**33 PASS / 4 FAIL / 4 NOT_SCORED**；C10、C12 两类首次跑满 20/20（C10 16/2/2、C12 19/0/1），240 条语料已执行 234 条。三阈值在 232 份 review-packet 上机械重算：required-type 召回率 **134/136 = 98.5%** ✅、隐私违规 **0/234** ✅、多提类型率 **54/223 = 24.2%** ❌（超标全部来自需要召回的 C06/C02/C01；C07/C08/C10/C11/C12 全 0）。判定口径变更：`FOLLOWUP_UNMET` 统一记 FAIL/模型行为，连带把 C05-10/20 由 NOT_SCORED 改记 FAIL。新确认两个 Host 缺陷：①`backend/main.py:8226` 的 `TerminationLimits(...)` 漏配 `max_wall_seconds`，静默回落 SDK 默认 900.0s（`simple_harness/runtime/termination.py:134`），与批次外部 deadline 900s（`scripts/run_corpus_batch.py:71`）相等，C10-13 被外部 SIGTERM 先杀而拿不到失败终态回执；②`backend/deskpet/memory/current_input_visibility.py:69-75` 的 `claim_stamp` 状态白名单不含 `CLAIMED`，与同模块 `:23` 的可见性 SQL 自相矛盾，而 `backend/deskpet/execution/foreground_runtime.py:1230` 的 `ingress.start` 排在 `:1332` 的 `record_sdk_started` 之前，构成竞态 → C12-19 `PrimaryHistoryDisclosureRejected`（同批另 15 例通过）。另同步 Memory 0.6.31 的争议短路语义：短路由整条车道收窄为**槽位级准入**（SDK 备忘 `DECISION-2026-09-08-conflict-short-circuit.md` §3.1），**空 fragments 不再等于库中无争议**；`backend/tests/quality/test_corpus_c10_prepare.py` 改写为两项新语义控制（槽位相关查询→仅确认组；无关查询→照常返回 items 且不准入该组），10 项全通过，`corpus_scoring.py` 的 C10 复核要求与 `RUNWAY-C10.md` §3 措辞同步。C10 判据本身不变：查库即违反 `no_recall`。[C10](../plans/2026-09-07-corpus-c01-local/RUN-C10-02-REVIEW.md)／[C12](../plans/2026-09-07-corpus-c01-local/RUN-C12-02-REVIEW.md)／[REST](../plans/2026-09-07-corpus-c01-local/RUN-REST-02-REVIEW.md)／[累计](../plans/2026-09-07-corpus-c01-local/CORPUS-CUMULATIVE-2026-09-08.md)。

最后更新：2026-09-08。HM-TO-A6 事件 O「争议值被当成定论使用」定位并修复（工作树 `worktree-contested-disclosure`，未并入 main）：run4 T22「那你现在按哪个版本执行这套校对流程？」得到「按 Python 3.13 执行」而非要求确认（A6-8 后半 / NC-4 FAIL）。逐字核对 `provider_invocations.request_json` 后确认模型当时看到的 fragment 只有 `bytes/history_binding/lane/memory_type/payload/payload_hash/privacy_class/ref/score/source_task_scope_ids/tokens` —— **没有 `conflict_status`、没有 revision、PERSONA 里也没有任何 contested 指令**；且 contest 操作比 T22 最后一次发送晚 16.5 秒才落库（分析车道时序，另记 F-O-2）。更关键的是：拿 run4 库副本 + 装机 SDK 0.6.28 跑真实 typed recall，contested head 会让 SDK 整次召回短路成 `needs_user_confirmation`（`items=()` + 一个原子 `confirmation_groups`，契约 S3 §5.3「contested 只能走完整 group confirmation」），而 `project_recall_fragments` 只遍历 `result.items`，**投影出 0 个 fragment** —— 模型读作「从来没存过」，比拿到旧值更危险。现修：`human_memory_v7.py` 新增 `project_contested_confirmation`（逐成员复查 privacy class，任一不合格按 S3 §5.2 整组连同「存在冲突」一起不披露），认知 fragment 补 `conflict_status=not_contested`（不写 `uncontested`：SDK 普通门放行 `{uncontested,resolved}`）；`context_route._memory_standalone` 把 `conflict_notice`（group id + 两个候选值的角色/exact revision/payload_hash + 双语「执行前先确认、不得采用任一值」指令）并入 extras，随既有 `public_result_hash` 一并覆盖，`ContextRouteReceipt` 与 typed-use carrier 一字未改；`_commit_receipt` 把稳定 reason code `recall_value_contested_requires_user_confirmation` + group id + exact revisions（**不含候选值原文**）写进 `context_route_tool_invocations.detail_json`；PERSONA 补 conflict_notice 读法。候选值本轮走通知而非 `fragments[]`，因为装机 SDK 的 `history_visibility.py:299` 只在 `result.items` 里解析 `HistoryRecallBinding`，confirmation member 会让下一轮 `check_history_visibility` 直接拒（记 SDK followup F-O-1）。验证：T22 provider 请求逐字重放 DeepSeek `deepseek-v4-pro`，对照组（旧投影）3 次里 2 次复现「按 Python 3.13 执行」，新投影 **3/3 要求确认**、全部报出两个候选、0 次给执行结论。另修 `scripts/native/a6_verify.py` 的 A6-7：原判据「旧 revision 必须退出 active」与契约不符且恒 FAIL—— `cognitive_memory_revisions` 挂着无条件 `RAISE(ABORT)` 的 immutable update/delete 触发器（`schema_v5.py:572-577`），SDK 物理上不可能回改旧 revision；生效的是 `cognitive_memory_heads.current_revision`（`sqlite_v5.py:2915-2924` 等一律按 `r.revision=h.current_revision` 连接，契约 `S3-cognitive-systems-recall.md:152`「exact current head」）。改判为 head 前进 + head revision lifecycle 合法 + 每次前进有 `evolution` 血缘边，run4 真实证据 A6-7 FAIL→PASS。控制：`tests/memory/test_contested_recall_disclosure.py`（9）、`tests/sdk_adapters/test_context_route_tool.py` 新增 4、`tests/native/test_a6_verify_a6_7.py`（7）。[裁决](../plans/2026-09-08-hm-to-a6/DECISION-CONTESTED-DISCLOSURE.md)。

最后更新：2026-09-08。HM-TO-A6 实跑事件 C/D 已定位并修复（工作树 agent-affbb0daabd2756a4，未并入 main）：**事件 C —— TaskScope 收口脏闸挡住了用户口述的档案变更**：第 10 轮用户说「记一个决定：以主清单 A 为准，参照件 B 只作参照。」，模型在活动 TaskScope 里连发 7 次格式完全正确的 `task_scope_update(decision.record)`（`outcome`/`base_revision`/`evidence_refs`=该用户消息的 evidence id/`idempotency_key`/`operations` 齐备），每一次都被 `task_scope_update_nothing_to_close` 拒绝，Run 最终 `driver_failed`；第 7/17 轮的 `goal.set` 同病，验收项 A6-5（README/STATUS 超限拆分）因此不可达。根因：那一轮没有任何 material 事件（design-freeze §2 把 `host.turn` 定为 trivial），`require_dirty` 遂把整个计划判为「没有可关闭的东西」。裁定：这道门的来历是「工具只在脏/pending 时暴露」这一**可见性条件**（隐藏工具被调用在冻结 SDK 里是整 Run 故障，才不得不改成 handler 拒绝码），从来不是 canonical 档案的写入授权——§2 让 `host.turn` trivial 是为了「普通对话不逼出一次收口」，而 Host 自己的 typed 路由（`human_memory_service`）写同一批 operation 时根本没有脏检查，且模型侧不存在第二条档案写入路由。故改为 `require_dirty` 只对 `outcome=no_mutation` 生效；`outcome=mutate` 的计划本身就是 material 变更（同事务追加 decision + revision、受 `base_revision` CAS 约束），干净档案上照样受理。检查顺序、其余全部拒绝码（`scope_unbound`/`payload_invalid`/`refs_outside_scope`/迁移表/`mutation_base_revision_conflict`/幂等 replay）、audit 写入点与 `require_dirty=False` 的兜底路径一字未动；剩余 `nothing_to_close` 的拒绝理由改成会说清缺的是什么、以及什么载荷才算数。**事件 D —— 一次畸形工具调用直接判死整个 Run**：同一 Run 里 42 字节的 `tool_call_arguments_not_json`（`_repaired_tool_arguments` 的多吐 `}` 修复救不回来）抛出 `ProviderProtocolError`，而它属于 SDK `_DEFINITE_PROVIDER_FAILURES`，不进 UNKNOWN 账本，F06 的 `reconcile_incomplete` 重试车道够不着，于是一次采样偶发就判死整个 Run。修复放在 Host 的 `ProductProviderAdapter`（SDK coordinator 之前的最后一帧，也已经承载同一缺陷类的确定性修复）：新增 `_ToolArgumentsProtocolError`（`ProviderProtocolError` 子类，公开 `error_code`/`retryable` 零变化），仅当解析诊断的 `check` 恰为 `tool_call_arguments_not_json` 时改抛，并就地重采**一次同一请求**（同 `request_id`、同请求体，无退避，`cancel` 已置位则不采）；第二次仍失败原样上抛、Run 照旧判死。合法响应零行为变化，其它协议缺陷（超时/传输/HTTP 状态/usage 值域/模型字段）一律不重采。重采在一次 SDK hand-off 之内，`provider_invocations` 仍是一条记录一个结局；第一份样本没解析出工具调用、SDK 从未派发 effect，无双重副作用，代价是最坏多计费一次被丢弃的补全（与 F06 §5 已接受的同一笔账）。新增审计行 `product_provider_protocol_resampled`（只有不透明 ref、有界枚举与整数）。测试：新增 `tests/sdk_adapters/test_task_scope_update_clean_scope.py` 4 控（基座换成仍可用的 `s5b_closure_harness`，直接驱动 Tool 与兜底共用的 `apply_closure`；原 `test_task_scope_update_tool.py` 所依赖的 `s5b_effect_gate_harness` 基线已红）与 `test_provider_tool_arguments_repair.py` 8 控重采用例，合计 49 通过；反证确认非空洞（改回 `require_dirty` 条件 / 把重采上限改成 1 时相应用例即红）。触及模块的 importer 集合（18 文件）135 通过 3 既红、`test_primary_foreground_runtime`+6 个 memory importer 40 通过 10 既红、`test_composition` 7 既红，全部经 `git stash` 前后对照逐条一致。**A6 实跑需重跑**：事件 C/D 的真实闭环要等 A–D 全部合入后按 `RUN-01-ATTEMPTS.md` 的条件跑满 24 轮才算正式判定。遗留：design-freeze §7「无脏/pending」的措辞与 `TASK_SCOPE_UPDATE_DESCRIPTION` 里「only after real project effects happened」需分别由 program 侧与工具描述子代理同步。[裁定](../plans/2026-09-08-hm-to-a6/DECISION-CLOSURE-DIRT-AND-PROTOCOL-RETRY.md)。

最后更新：2026-09-08。记忆分析车道在 DeepSeek 上的 `ProviderProtocolError` 间歇失败已定位并修复（工作树 agent-a45a787c13e487e76，已并入 main 840ad2cc）：原生 HM-TO-A6 实跑中分析车道 10 次 Provider 调用失败 6 次（HTTP 200、`stage=response_protocol`、`retryable=False`），第 2 轮用户事实「校对结果存到外接硬盘 / 校对归档」因此没有落成记忆头（`semantic_claims` 只剩 1 条 `proofreading_python_version`）。用 Host 持久化的 `analysis-attempt-input-*` durable 信封把同一请求原样重放 DeepSeek 39 次，安装版 SDK 解析器复现 12 次失败（31%）：`tool_calls[0].function.arguments` 是**合法 JSON 对象 + 一个多余的 `}`**（11 次在末尾、1 次是根对象提前收尾后继续），`finish_reason=tool_calls`、`completion_tokens` 540–1250 对 6144 预算 → 是模型序列化缺陷，不是截断，因此不调预算、不加重试。修复放在 Host 适配层 `_ProductOpenAICompatibleProvider`：交给 SDK 解析器之前只在 `json.loads` 已失败的入参上、只在两个由 JSON 前缀确定的位置删掉那一个 `}`；`early_object_close` 形态要求整串重新解析成一个对象**且已解出前缀的每个键逐值原样保留**（独立评审 P0：否则 JSON 后键覆盖会把 `outcome` 反转成 `no_mutation`、把 `operations` 清空，把「响亮的失败」变成「错误的成功」），其余畸形（截断、尾随正文、尾随第二个独立值、重复键拼接）一律 fail-closed；合法响应恒等透传（`is` 断言），修复路径写时复制。同时 `product_provider_response_parse_failed` 补 `diagnostic=` 无载荷诊断（`check` 有界枚举 + `finish_reason`/`completion_tokens`/`arguments_length`/`json_error_kind`/`json_error_position`，只有形状、有界枚举与整数），并镜像 SDK 构造函数内的检查（`CallId` 可打印 ASCII、`validate_json_value` 拒 NaN、`ProviderUsage` 值域）；规范化与诊断各自包 `try`，绝不取代或吞掉 SDK 原始 Provider 错误。真实 API A/B：39 次调用基线失败 12 次、打补丁后 0 次。新增 `tests/sdk_adapters/test_provider_tool_arguments_repair.py` 33 控通过；Provider 适配相关 8 个文件 68 通过；`tests/memory/` 分析车道 7 文件 41 通过 5 既有红（`git stash` 前后逐条一致）。**A6 实跑需重跑**：已失败的 7 个 `analysis_batches` 是 durable 终态，修复不追溯。[裁定](../plans/2026-09-08-hm-to-a6/DECISION-ANALYSIS-LANE-DEEPSEEK-PARSE.md)。

最后更新：2026-09-08。HM-TO-A6 原生跑主对话失速修复（工作树 agent-a057e3ebd26f3de05，已并入 main）：第 5 轮结算后前台驱动以 `PrimaryVisibilityError(primary_read_policy_unavailable)` 死亡、UI 永久「等待主对话就绪」、`memory.evidence_ingestion_replayed` 每 ~2s 一条（418 次）。离线复现出被 `primary_visibility.py` 的 `except Exception` 吞掉的真实异常：SDK `MemoryLimitError(evidence_payload_requires_controlled_blob_ref)` —— `record_terminal_observation` 把整轮 transcript 内联进终态观察 payload，工具密集的第 5 轮达 74196 字节，越过 SDK 的 `MAX_INLINE_EVIDENCE_BYTES`(65536)；Host S1 无尺寸前置校验收下了它，此后 Memory 侧每次读都整批拒绝。定性为 **Host 缺陷**（SDK 在受理与读取两侧的拒绝完全符合契约，且 Host S1 的写入根本不在 SDK 调用链上，只能由 Host 自己在写入点校验；工具拒绝只是让 transcript 变长的放大器）。修复分三层：①**写入点（根因）**：`record_terminal_observation` 落库前用 SDK 自己的 `validate_sanitized_evidence` 校验，并在事务最前面把 transcript 确定性降级到可受理尺寸——按字节数从大到小牺牲 tool_result 正文（不够才动 assistant），换成可复算的内容寻址省略标记 `[deskpet:elided-tool-result sha256=… bytes=…]`，`messages[0]`（USER 原文）永不改动；降级是 messages 与预算的纯函数，重放字节一致，幂等分支不受影响；`primary_context_pages` 的「已结算 transcript 比对」改用 `transcript_matches`，只接受能复算出同一标记的差异，不放松完整性。②**读取点**：`assert_source_admissible` 在组批前逐条调用 **SDK 自己的** `validate_sanitized_evidence`（而非手写 64 KiB 判定——探针证明手写版会放行坏 `blob_ref`/5000 节点/深度 40/超大公开串/凭据边界，每条都同样能复现失速），不可受理的源只让自己那一个 root 不可见（fail-closed，绝不放行任何 binding）；短期索引车道在第一次写之前判组是否可受理，止住重放风暴（不加负缓存：负缓存 key 覆盖不到 `terminal_source`，会把修好的组永久挡住）。③**驱动**：有界退避重试 + 用尽记 `foreground.runtime.stalled`，重试计数只在完全安静一遍或 30s 冷却后清零（否则「进展/抛错」交替会 0.5s 一轮永久空转），退避等待观察 `close()`；任何退出路径都置空 `_driver`。审计新增 `error_cause_type`/`error_cause_detail`，且只有带稳定 `.code` 或白名单 Host/SDK 错误类型才留消息正文，避免原始 SQL/HTTP 正文进持久审计行。新增/改写 19 例全绿；`tests/execution tests/memory tests/operation_audit tests/faults tests/test_context.py` 与 main `7cec5249` 基线 worktree 对跑失败/错误集合逐条一致（均为环境既有，含已知的 short-index embedder 红套件）。终态 payload 改 controlled blob ref / 逐条消息引用列为 F-A6-1，需与 Memory SDK 协同；**重跑 A6 不需要 SDK 改动**。[裁定](../plans/2026-09-08-hm-to-a6/DECISION-PRIMARY-VISIBILITY-STALL.md)。

最后更新：2026-09-08。长消息静默丢弃修复（Incident G，工作树 agent-a6744a40d119b547b，已并入 main）：HM-TO-A6 原生第 17 轮那条 18 393 字节中文消息**到达了后端**却在 DTO 构造期被拒 —— `human_memory_service.py:283` 的 `QueueTurnRequest.__post_init__` 对 turn 正文复用了通用标识符上限 `identifier(self.text, "text", 16_384)`，而 `task_scope/protocol.py:82` 按 UTF-8 字节判定（中文 3 字节/字，16 KiB 仅约 5 400 汉字）；具体原因 `text_too_large` 又被 `TaskScopeProtocolError` 的类级码 `task_scope_protocol_rejected` 抹平，所以既无 enqueue 日志也无可读说明，前端更是完全没有长度约束。现 turn 正文有了自己的具名上限 `FOREGROUND_TURN_TEXT_MAX_BYTES = 65_536`（约 21 800 汉字，仍是硬边界：整条 turn 要哈希、落库并作为一条 sanitized evidence 重放）与稳定码 `human_memory_turn_text_too_large`；前端新增 `tauri-app/src/primary/turnText.ts` 镜像该常量，composer 在**输入过程中**即显示含当前字节数与上限的中文提示并拒绝发送，controller 在上线路前拦截、并把后端稳定码翻成同一句话。产品口径：要么接受，要么给出带具体上限的可读错误，不截断、不静默。测试：`test_primary_turn_admission.py` 8 通过（含 18 393 字节逐字落库、超限零残留、恰好 65 536 字节通过），`backend/tests/task_scope` 等 89 通过，vitest `src/primary`+`src/code-panel` 270 通过、UI-CONTRACT 聚合命令 141 通过、`tsc -b --noEmit` 通过；`test_primary_visibility.py::test_real_memory_only_forget_cold_user_and_terminal_no_host_rewrite` 为既有失败（回退到 HEAD 同样红）。原生真实点击复测待做。[裁定](../plans/2026-09-08-hm-to-a6/DECISION-LONG-MESSAGE-SEND.md)。

最后更新：2026-09-08。语料跑道剩余非 C10/C12 用例补齐（工作树 `worktree-corpus-rest`，未并入 main）：支持面 180→192（C05 +7、C08-20、C09-13、C11-12/16/19）；C05-12/13/16/17 为产品决策阻塞，C05-18/C02-19/C03-20 为跑道未完成，条款与逐例表见 [RUNWAY-REST](../plans/2026-09-07-corpus-c01-local/RUNWAY-REST.md)。

最后更新：2026-09-08。语料 run-01j 复核三项收口（工作树 p1p2-recall-hints，未并入 main）：①`trigger_local` 一次都没到达模型 —— `_prospective_trigger_local` 以 `payload["memory_type"]` 判类型，而 prospective 的 SDK 公开 payload 只有 `action`/`trigger` 两个键（run-01j C04-12 工具回执逐字为证），判定恒假，C04-12/13/19/20 仍只看到 `trigger_at` epoch，遂算错一天或改口称「记录里没有时间」；现改为用 fragment 自身的 `memory_type` 判定（payload 与 `payload_hash` 仍逐字不变），并补 `tests/sdk_adapters/test_context_route_prospective_runtime.py` 运行时闭环（真实 C04-01 种子 + 真实调度注册结算 + 真实 typed recall + 真实 `context_route` 工具回执，断言回执 fragments 带 `trigger_local` 且与 payload 时区一致；单独回滚实现即红）。②`procedure_use` 连续 6 次 `tool_handler_failed`（C06-14，813s）—— `_validate_execution_identity` 把「本 Run 从未激活该步骤工具」(KeyError) 折叠进与真正标识/schema 漂移同一个错误，Host 又让 `ProcedureUseRejected` 直接抛出，模型只拿到 "Tool execution failed." 于是原样重发；现 `current_snapshot` 先判缺席回 `procedure_tool_unavailable`，`procedure_use` 处理器改为**返回**稳定码 + 可执行下一步（沿用 `_result` 的 `error_code`/`public_message` 约定，仍是 FAILED、仍什么都没绑定），工具描述补「先激活步骤工具」。③`procedure_discover` 零候选（C06-05/06/09/19）—— 种子确已落库（同批 C06-03/13 走同一机制命中），根因是 SDK 0.6.25 `match_score` 的纯词项命中覆盖不足，模型换个说法即全失，最尖锐的是 C06-09 查询「排程」与种子名「排日程」CJK 二元组零交集；本轮只记录不改 SDK，详见 `plans/2026-09-06-typed-use-primary/FOLLOWUPS.md` F08。`tests/sdk_adapters/` 与 `tests/memory/test_procedure_*` 的失败集与改动前逐条一致（`test_procedure_scope_runtime` 的 `foreground_run_already_terminal` 为环境既有）。语料复跑待做。[复核报告](../.local-test-evidence/2026-09-07/corpus-batch/run-01ij.review-report.md)。

最后更新：2026-09-08。语料 run-01g 复核 P1/P2 引导修复（工作树 p1p2-recall-hints，未并入 main）：①P1 —— `memory_types` 含 `procedure` 而 typed 召回按设计不返回未绑定流程时静默返回，模型据此判定「没有保存过流程」、12/18 例 C06 零调用 `procedure_discover`；现 `context_route` 的 memory_standalone 回执在「requested 含 procedure 且结果无 procedure 项」时追加顶层 `procedure_hint`（`reason=typed_recall_returns_only_applicable_procedures`、`next=procedure_discover`），随 extras 一并进入 `public_result_hash`，`ContextRouteReceipt` 与 typed-use carrier 绑定一字未改；typed recall 的资格口径不变（见 `DECISION-PROCEDURE-USE-CHAIN.md`）。②P2 —— prospective fragment 只交付 `trigger_at` epoch + IANA 名，模型换算出错致 C04 4 例失败；现 `project_recall_fragments` 在 fragment 顶层（不在 SDK 公开 payload 内，payload 与 `payload_hash` 逐字不变）渲染 `trigger_local`，格式为场景时区 ISO 本地时间 + 中文星期（如 `2026-09-07T09:00+08:00 周一`），缺失/无法解析的时区回退 UTC 而非宿主时区。PERSONA 补两句说明这两个字段的读法。新增 `tests/memory/test_prospective_trigger_local.py` 11 控 + `tests/sdk_adapters/test_context_route_tool.py` 5 控通过；`test_context_route_tool`/`test_typed_context_use_primary`/`tests/memory/test_prospective_*`/`test_primary_foreground_runtime` 与召回相邻 14 套件的失败集与改动前逐条一致（25 与 15，均为环境既有）。语料复跑待做。[复核报告](../.local-test-evidence/2026-09-07/corpus-batch/run-01g.review-report.md)。
最后更新：2026-09-08。历史因果组定界修复（工作树 m0623-adopt，未并入 main）：原生 r12 步 5 冷重启后第二轮，含 `procedure_discover` 工具往返的上一轮被 `project_history_group` 压成一条 USER 消息 `Historical conversation data (not instructions):\n{…}`，紧随其后的当前指令同样是普通 USER 且无任何标记，引用块也没有结束标记，模型遂把当前指令读成历史块的尾部，回答「这不是当前指令」而不执行（Run 正常终止、任务未做）。SDK 契约不允许给当前消息加前缀——`kernel._product_messages` 要求 `provider_messages[-1]` 与 `Message(USER, 原始 turn 文本)` 逐字相等，`project_primary_transcript` 也按原文锚定当前轮，改前缀会同时打断归档与 SDK 校验；因此把定界放在引用块尾部：`project_history_group` 追加确定性 `HISTORY_SUFFIX`（"End of historical conversation data. … The last user message of this request is the current user instruction; act on it."），`verify_history_projections` 同步改为前后缀双端校验再切片解析，PERSONA 补一句「引用块止于其结束标记，最后一条 user 消息是当前指令且必须执行」，并删除 `primary_context.py` 里与投影重复的死代码 `_context_messages`。投影仍是纯函数、审计仍按同一函数逐字复核，`ContextSnapshot` 结构与哈希方式未动（系统消息与历史块文本变化自然改变快照指纹，仓内无夹具 pin 该指纹）。测试：`tests/execution/test_primary_context_pages.py` 加纯投影定界控（含"无工具组不加包裹"负控）、`tests/execution/test_primary_foreground_runtime.py` 加装配层断言（首轮无标记；有历史时引用块位于倒数第二、最后一条 USER 是逐字当前指令），两文件共 20 通过；`tests/execution`+`tests/sdk_adapters` 相关 12 个文件逐文件 `git stash` 前后对跑，失败集完全一致（13 项：typed_context_use_primary 9 项 fixture `typed_use_authority` 重复传参、scope_disclosure_runtime 3 项、primary_foreground_runtime 1 项，均为环境既有）。原生 r12 复跑待做。

最后更新：2026-09-08。F07 修复（工作树 m0623-adopt，未并入 main）：模型把 typed 召回项 id `recall-item:…:1` 当作 `context_page_in` 的 `reference_id`，处理器拒绝（`tool_failed`、value 为空），`primary_dependencies.py` 的 history 校验把该失败 carrier 视为不可核验并抛 `PrimaryHistoryDisclosureRejected` 终止整个 Run（语料 run-01e C04-16、run-01h C06-13 两次复现）。现对 `context_page_in` 的失败 carrier 做**确定性复核**而非跳过：用同一 arguments 重放 `admitted_page`/`admitted_current_page`（按引用前缀分派），同样拒绝才 `continue`，重放反而成功 → 仍抛 `scope_search_result_unverified`；primary 页引用还要求重放拒因与记录的 `error_code` 逐字一致，原 `primary_page_hash_mismatch` 分支并入其中，未泛化到任意工具。引导侧：`CONTEXT_PAGE_IN_SCHEMA` 描述与 PERSONA 写明 `reference_id` 只能取本次请求已备好的页引用（truncation marker 的 `reference_id`/`source_hash`，或 `[Context page-in reference: id=… hash=…]` 行），`context_route` 的 `fragments[].ref` 不是页引用，无页引用则不要调用。新增 `tests/execution/test_scope_disclosure_runtime.py` 复现控 + 重放不一致负控 2 通过（复现控修前红）；`tests/execution`+`tests/sdk_adapters/test_typed_context_use_primary.py`+`test_no_recall_gate.py` 失败集与改动前完全一致（52 失败 10 错误，均为环境既有）。语料复跑待做。[F07 条目](../plans/2026-09-06-typed-use-primary/FOLLOWUPS.md)。

最后更新：2026-09-08。TaskGrant 时钟接缝修复（工作树 m0623-adopt，未并入 main）：C04-15 `TaskGrant expired before activation` 根因是铸造侧用真实墙钟、激活侧用跑道注入的场景时钟——`main._initialize_capability_runtime` 没有 `clock=` 形参，`PreparedAuthorizationRuntime`/`AdmissionTaskGrantRuntime`/`SdkPreparedAuthorizationPolicy` 全部退回 `time.time`，场景时钟超前真实时间 8h（TTL）以上时 grant 必然先过期；现该函数暴露 `clock=`（默认 `time.time`，生产调用点不传 → 生产行为零变化）并下传三处构造，`corpus_scoring_session` 铸造侧与激活侧注入同一场景时钟，`task_grants.py` 的过期判定一字未改。新增 `tests/permissions/test_task_grant_clock_seam.py` 9 控通过（T1 +30d 铸造/激活、混用时钟反向见证仍过期、T2 偏移矩阵 ±30d/±1d/0、T3 注入贯通性 AST 断言），修前 T1/T2/T3 红；`tests/capabilities`+`tests/faults`+`tests/quality/test_corpus_c04_prepare.py`+`tests/sdk_adapters/test_product_host_ports.py` 失败集与改动前完全一致（17，均为环境既有）。[裁定](../plans/2026-09-07-corpus-c01-local/DECISION-C04-15-TASKGRANT-EXPIRY.md)。

最后更新：2026-09-07。F06 修复（工作树 m0623-adopt，未并入 main）：provider 传输超时后 SDK 把调用记 UNKNOWN、Run 进 waiting 等 Host 结论而 Host 用 Noop 且从不调 `reconcile_incomplete`，前台记 `BOUND_WAITING` 后退出 → Run 永久停摆。现 Host 侧 retry-once `ProviderReconciliationPort`（同 request 首次未知 → `CONFIRMED_NOT_STARTED` 重发一次，再次未知 → 前台 cancel）+ `RuntimeReconciliationPort` 调 `reconcile_incomplete` + 前台 waiting 后续推 + 重启恢复被挂 waiting Run 的工具授权；新增单测 5 通过、列出套件无新增红；原生 r12 待验。[分析](../plans/2026-09-07-native-main-journey/DECISION-PROVIDER-TIMEOUT-STALL.md)。

最后更新：2026-09-07。S6 Task 2 后端补齐（工作树 m0623-adopt，未并入 main）：新增 HUMAN `task_scope.list`（自有 scope、updated_at desc keyset、上限 32、无 archive、不授权），`task_scope.open_exact` 顶层只读 `binding_summary`（mode/根/凭证/Host re-stat state）与 `drift_probe`，公共通道拒绝客户端 `live_probe`→`human_memory_request_invalid`、drift 标 `host_unavailable`；前端「最近任务」显式按钮与绑定摘要只读展示。pytest 84 通过（含新增 3）、vitest 13 通过、typecheck/eslint 通过；原生真实点击未验。[审查](../plans/2026-09-07-native-main-journey/REVIEW-TASK-PANEL.md)。

最后更新：2026-09-07。C06主六新描述控6PASS3.20s、16sources已控；后继17/18/19额外Episode/第二Procedure/设备声明源码及独立3控NOT_RUN。19/20仅具备来源映射；19缺算法/实际设备能力证明通过source_limits明确，18真实finance适用性仍待runtime，01等真实archive。无SDK/session/资源变更。[契约](../plans/2026-09-07-corpus-c06-preparation/CONTRACT.md)。

最后更新：2026-09-07。C05当前任务保持/完成任务只读/提前切换拒绝3控分批通过47.18s；旧scalar拒Host混合声明已按原来源绑定修复。C09正式退役/原子双修订两新组合首次2PASS14.69s，关闭fixture再生产重开/评分请求隔离。均受控HTTP非质量，PG87968/88533/88901清空，无旧绿重复。[C05](../plans/2026-09-07-corpus-complete-dispatch/C05-STATE-RESULTS.md)／[C09](../plans/2026-09-07-corpus-complete-dispatch/C09-RESULTS.md)。

最后更新：2026-09-07。C06新增六条条件Procedure来源首次6PASS3.20s，旧11未重跑；保留原描述/确认/授权/不删除限制及双条件分支，16/20来源准备有控，非执行许可/跨Task/模型质量。PG88468自然清空。[结果](../plans/2026-09-07-corpus-c06-preparation/RESULTS.md)。

最后更新：2026-09-07。C06主r2报告11控通过（10条setup，原r1顺序断言红保留）；后继05/07/10/13/14/20六条安全条件描述映射及独立控源码NOT_RUN，16/20仅具备准备映射。确认/授权/不删除/分支条件保留，不构造动作授权，不算actualTask或质量；剩01/17/18/19来源未闭合。[契约](../plans/2026-09-07-corpus-c06-preparation/CONTRACT.md)。

最后更新：2026-09-07。C06十条S+Procedure来源及编译共11唯一控制通过：原r1 canonical操作顺序测试假设红，改按真实operation_id后原红/未跑/新七项11PASS4.88s，公共job/精确来源/foreign owner/冷重开。非跨Task/物理评分/质量通过；PG87793/88125均清空。[结果](../plans/2026-09-07-corpus-c06-preparation/RESULTS.md)。

最后更新：2026-09-07。C06后继同构setup新增06/08/09/11/12/15/16七条，仅SPECS映射与独立控制selector；复用32eb已审builder/authority，首3条selector保持固定。20原setup/hash未改，10条具备准备源码，全部新控NOT_RUN，非跨Task/模型质量结论；其余10条约束仍明确保留。[契约](../plans/2026-09-07-corpus-c06-preparation/CONTRACT.md)。

最后更新：2026-09-07。C09编译+19标量公开修订准备首次20PASS10.59s：真实job/原新receipt/同ID rev2、退役不入当前召回、16不变字段r1及20原子双修订。07回填单位已按原setup修正，13 Procedure与正式dispatcher仍待；不计模型质量。PG87580自然清空。[结果](../plans/2026-09-07-corpus-c09-prepare/RESULTS.md)。

最后更新：2026-09-07。独立feat/corpus-c05-remaining基c5b55387已写07/08正式状态来源组与3新组合控制，NOT_RUN、未合主；其余14格明确来源/调度/公开契约缺口。复用原4格整链与empty证据，无新测试、模型、资源进程、版本变动，不计质量PASS。[逐格事实与待跑控制](../plans/2026-09-07-corpus-c05-remaining/CONTRACT.md)。

最后更新：2026-09-07。C06跨scope语料准备首组02/03/04新增专属public Host S1→Memory分析job→Semantic/Procedure回读源码；20原setup/hash全保留。全部新控NOT_RUN，未接共享scoring/session，未证明跨Task typed召回/非SELF出站/质量。无测试、模型或SDK制品变更。[源码契约](../plans/2026-09-07-corpus-c06-preparation/CONTRACT.md)。

最后更新：2026-09-07。C08历史纪要15/检查列表16两个新控制首次2PASS16.01s：实际main旧USER与派生assistant→真实job→公开抑制→生产重开→下一受控HTTP无旧内容。PG87213自然清空；文档文本来源，非文件/模型质量，正式dispatcher接入另待，旧绿未重跑。[结果](../plans/2026-09-07-corpus-c01-scoring/C08-DOCUMENTS-RESULTS.md)。

最后更新：2026-09-07。C05正式04/09/14/20多轮+真实empty及来源parser共6控制通过74.03s：同root真实setup/审批/marker/closure/terminal、过滤prefix、独立评分/followup/exact resume；context_route控制/physical来源误分类修复已在真实main闭合。原scope红/FK夹具红保留，PG85016/85509/85863均清空。固定HTTP非模型质量，剩余16case仍待。[结果](../plans/2026-09-07-corpus-complete-dispatch/C05-RESULTS.md)。

最后更新：2026-09-07。C08正式dispatcher e81a9af7+helper f45da5f9新增1PASS8.00s：01跳scalar，真实旧组/抑制/生产重开CONFIRMED后独立评分Provider，next physical无旧内容/统计1；只本共享入口01组合，不重复旧5绿、不计模型质量。PG85743自然清空。[结果](../plans/2026-09-07-corpus-complete-dispatch/C08-RESULTS.md)。

最后更新：2026-09-07。C08仅01/06/11/18 retained已接正式shared dispatcher源码，01跳过scalar preseed，旧组/同库抑制及原authority重开确认后才切独立评分Provider；1新实际dispatcher控制已准备NOT_RUN，其他C08仍block，不重复原5helper绿、不计质量PASS。[契约与唯一nodeid](../plans/2026-09-07-corpus-complete-dispatch/C08-DISPATCH.md)。

最后更新：2026-09-07。C05审批/缺证据3唯一控制分批通过：r1真empty与missing通过、pending无Effect红；公开audit+response身份proof修复后仅原红与False新控2PASS2.75s。无typed原因False不冒真零，PG83853/84899清空；marker/closure及多轮actualmain另首测，非模型质量。[结果](../plans/2026-09-06-corpus-public-seed/C05-AUTHORITY-RESULTS.md)。

最后更新：2026-09-07。C05限定04/09/14/20 actualmain setup→独立评分Provider→原固定followup接线及5新控制源码已准备，NOT_RUN；两factory同ignored root、真实setup prefix与current disclosure保留，多个评分Run统计不含fixture。pending审批与非空False-policy独立控制已通过；实际main首测在control记录Scope分类失败，修复待复验；C08 partial/其它C05未开放，不称20 ready或模型质量。[源码契约与待跑nodeids](../plans/2026-09-07-corpus-complete-dispatch/C05-PHASE.md)。

最后更新：2026-09-07。C08-01/06/11/18保留旧USER+assistant摘要实际main及wrongassistant共5新控首批5PASS38.78s：原job APPLIED/IDLE，公开suppression后两history隐藏，重开生产authority后下一physical请求无旧内容。PG84053五child自然清空，非真实模型/原生/rolling-summary；正式dispatcher待接。本批也确认d60异步诊断两SDK来源实际写出且无未await警告。[结果](../plans/2026-09-07-corpus-c01-scoring/C08-RETAINED-RESULTS.md)。

最后更新：2026-09-07。Host诊断异步消费修复d60a94f4：真实installed Memory SQLite快照/timeout-cancel两个新控及三个受影响同步控制首批5PASS1.04s，PG83640清空。main改显式await，尚待下一新组合观测；SDK诊断版本硬编码原0.6.0另待，不冒称完整审计或改制品。[结果](../plans/2026-09-07-sdk-async-snapshot/RESULTS.md)。

最后更新：2026-09-07。C05新增07/08/10/11及TOOL调用ID共5唯一控制分批通过（r1 3绿2红，完整标题查询修复后仅2红复测2PASS4.76s）；固定身份真实分页，不保证所有并列ID顺序。PG83131/83471清空，无真实模型/原生结论，正式main多轮接线仍待。[结果](../plans/2026-09-06-corpus-public-seed/C05-RUNTIME-RESULTS.md)。

最后更新：2026-09-07。当前H0710/M619 C01-06实际main完整路由控制新增1PASS7.16s：真实job同ID修订→nullable proposal/公开审批→真实typed route→下一physical请求exact fragment为rev2小周。HTTP两响应受控，不算模型质量。原错字段oracle红保留，PG81693/81834都清空；原生/服务model_not_found仍待。[结果](../plans/2026-09-07-corpus-c01-scoring/MAIN-REVISION-ROUTE.md)。

最后更新：2026-09-07。C01-06/11正式session分派独立源码叶（base61f474e5）：06接真实job→同ID公开REVISE→关闭fixture→main重开，11沿已合可信Context时钟解除旧block。本叶NOT_RUN/待主完整候选验证，不沿用helper绿冒称正式评分通过；C08 partial/C05未接门保持。[契约](../plans/2026-09-07-corpus-complete-dispatch/CONTRACT.md)。

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

最后更新：2026-09-07（task_scope_search CJK）。`task_scope/search.py` 的 FTS 查询与文档索引改为「整词 + CJK 二字组合」（`_lexical_units` / 文档 content 追加二字组合区，命中在二字组合区时 snippet 回落为标题+目标），修复语料 C05-04/C05-09「暂停的排版工作」对标题「家谱排版」零候选；控制 `tests/task_scope/test_projections_search.py::test_chinese_query_matches_title_by_cjk_bigrams`，task_scope 相关 19 个文件 127 通过；同批 17 个失败（s5b_acceptance_matrix/effect_gate/s5a_milestone_route_loop 等）在 95eacafd（今日改动之前）同样失败，属既有红，未在本次处理。

最后更新：2026-09-07。评分自然退出叶ab36b6a5：WorkflowRunner独立UoW owner原未释放，补public runner/service close与main/carrier统一收尾；bootstrap明确服务拥有共享端口UoW，runner不关借用端口。唯一独立child实际main执行自然SystemExit控制1PASS17.01s，PG69388清空，无pytest全局lane清理代替。原C01-10语义FAIL及deadline保留，下一新case质量另验。[定位与结果](../plans/2026-09-07-corpus-c01-scoring/PROCESS-EXIT.md)。

最后更新：2026-09-07。首真实C01-10固定30b07393/H079/M619：1物理请求、0工具，排序正确但未取得已存A，原gold FAIL（主审+独审）；240已尝试1/通过0。业务COMPLETED后worker线程退场挂起，180s外部deadline退出125并清空PG67059，非内存/磁盘门。修复退出与通用记忆来源指导继续，均未称通过。全阶段防熄屏保持。[真实结果](../plans/2026-09-07-corpus-c01-scoring/REAL-R1.md)。

最后更新：2026-09-07。C01真实交互补丁222346d3/ac76e16a：可选精确公开审批仅允许memory_standalone，未知等待/非白名单BLOCKED；接原main ingress打开barrier。实际main/SDK新增组合1PASS16.83s（只HTTP delegate固定，权限/handler/终态真实），原ingressclosed失败保留；PG66376清空，最低磁盘599MiB，首真实C01须恢复默认准入再执行。本地WeMM实际加载，非真实LLM评分。[结果](../plans/2026-09-07-corpus-c01-scoring/APPROVAL-RESULTS.md)。

最后更新：2026-09-07。C01评分叶99d17c11：真实main Memory初始化/关闭与gold隔离、真实未终态attempt保存红2修后通过，连同先前FAILED参数共3唯一无网络控制；PG65098/65129清空，旧失败保留。原coroutine diagnostics警告单列，不扩改。首C01-10已合候选，实际评分另验，当前模型评分0，不是质量PASS。[控制与准确运行命令](../plans/2026-09-07-corpus-c01-scoring/RESULTS.md)。
最后更新：2026-09-07。新构建原生r25固定d86e4805/H079/M619冷恢复与两次实际授权可用；首查询错把taskactive当流程状态，澄清后实际Procedure发现返回0且模型如实答无。正向草稿/完整Procedure仍未验收，240质量不计。PG62018正常退出清空，退出后仅清可再生构建缓存，防熄屏继续。[结果](../plans/2026-09-06-typed-use-primary/NATIVE-R25.md)。

最后更新：2026-09-07。主d86e4805（产品0e146792）与H079/M619/S0313安装组合仅Auto原root新Scope写入/alreadyBound拒绝2PASS4.05s；205已加载SDK模块属指定target，无重复全成员核验。PG61943 exit0/remaining[]已交native槽。原7unique不重复累计，Manual UI与原生仍待，原失败保留。[组合事实](../plans/2026-09-06-completed-scope-continuation/COMBINATION-619.md)。

最后更新：2026-09-07。已完成项目续改独立叶：新Run公开search取得旧complete Scope/source，create_new经真实权限将新active Scope绑定原root，再实际工具写原文件；旧Scope不重开。同Run已绑定时在创建前及route同TX拒绝，下一物理请求给明确新Run指导。H079/M618确定性栈7个唯一控制分批PASS，最终源1a8e1dd6/Dirac限定ACCEPT；本次Auto/Manual两绿+alreadyBound双层hash修正单绿，PG57258 exit0/remaining[]已交槽，原业务/fixture/oracle失败全保留。仅AUTO配置root及公开Manual service路径；Manual UI、主组合和原生仍待，非program完成。已独审合入隔离主候选，用户主checkout未切换。[契约与结果](../plans/2026-09-06-completed-scope-continuation/RESULTS.md)。

最后更新：2026-09-07。r24已allowed后旧等待提示的UI接线修复：手刷显式exact授权补读、同Run工具/终态推进补读、断线与空pending区分。真实View/Panel/Channel组合新增3控分批通过，原负控保留；尚未新构建/native复验。[结果与边界](../plans/2026-09-07-primary-decision-refresh/RESULTS.md)。

最后更新：2026-09-07。原生r24固定b2da14da/H079/M619，待定流程记录可见；第二轮界面等待授权但停止后补出成功context_route及4次tool_search，Procedure发现/使用和文件核验未完成。PG50771正常退出且清空，非内存/预算阻塞。全测试阶段防熄屏保持。[现场与边界](../plans/2026-09-06-typed-use-primary/NATIVE-R24.md)。

<!-- 最后更新：2026-09-07 -->

C05 固定 f78004ef 在 H079/M619 installed 的原5红定向复验5PASS/12.15s，3绿未重跑。
04/09/14真实material marker→closure→来源绑定字段、20归档/prefix、真实评分分页与late suppression精确USER-only通过。
仅确定性fixture/public runtime，不计模型质量或C05全部20准备；最终physical outbound race及其他case接线仍待完成。
PG80017 exit0/remaining=[]，原两批红保留、WIP隔离，已释放资源。
[来源、命令及历史结果](../plans/2026-09-06-corpus-public-seed/C05-RESULTS.md)。

<!-- 最后更新：2026-09-07 -->

2026-09-07 主0abdf048/H079/M619/S0313独立installed组合仅C04-12新增1PASS/1.03s，PG57440清空；与原H078/M618叶证据分开，非质量执行。

C04 原20公开setup分批19+1通过；实际same-timestamp晚append暴露Host游标漏注册，
d3a580be复用原journal修复当前timestamp边界、分离scan高水位与消费CAS，无DDL/旧receipt改写。
新增5控分批通过（测试helper d25fe2f6），原绿未重跑；晚到更早timestamp仍不保证。
实际C04-12注册/失效/改期ACK链通过；terminal故障控是实际SDKreceipt+显式scripted Host高位cursor。
H078/M618 installed，非H079/native/Provider/240质量；Dirac对d3a580be/d25fe2f6/6b1f886a最终限定ACCEPT；非完整consumer not_required遍历。
[结果与边界](../plans/2026-09-06-corpus-public-seed/C04-CURSOR-RESULTS.md)。

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

2026-09-06 C03与推断准备收尾已审叶bfd56d99合入候选：C03全部20条setup分批通过，C01–C03共60条准备验证；240真实质量仍0。C03-20两来源实际SDK job的合法无修改收尾、非法分析虽APPLIED但拒绝确认、取消后的原application恢复共3个新控制分批通过。仅H078/M618独立叶证据，C02接线、prepare自动收尾、跨进程proof及当前H079组合仍另验；不是全部评分运行就绪。[C03准备](../plans/2026-09-06-corpus-public-seed/C03-PREPARE.md)／[收尾结果](../plans/2026-09-06-corpus-public-seed/INFERENCE-DRAIN-RESULTS.md)。

最后更新：2026-09-06。r16 mandatory-context 后继独立源：Host 将 no_recall 决策移至 SDK 真实响应 checkpoint 之后，反馈纳入新 snapshot/hash；repair-bearing 每次拟终态（包括已route）仍核真实ACK/pending，最多两次且继承原预算。Host 新3控首批PASS6.63s，SDK新11控分批PASS；含真实首零tool→ACK、route无ACK有限FAILED、续接前publicforget零新增发送，公开操作审计核repair identity。最后PG21416exit0/remaining[]，无模型/native/构建。Dirac固定源/14unique限定ACCEPT；H079待主统一制品与原生，H078/M618/原r16失败记录未改；非完整program完成。[精确结果](../plans/2026-09-06-prospective-mandatory-repair/RESULTS.md)。

2026-09-06 原生r17/r18（Host55eb273d/H079/M618）：旧提醒真实ACK后正文送达、下一轮去重及冷启动去重通过；新银杏提醒到期虽ACK成功，最终回复却未展示提醒正文，**完整提醒交付仍FAIL**。两组正常退出且无残留，不是内存/锁屏阻塞。新增缺陷继续修复，旧r14/r16失败保留；240质量仍0。[实际结果与证据](../plans/2026-09-06-typed-use-primary/NATIVE-R17-R18.md)。

2026-09-06 H079/M618候选：新SDK单次离线制品已固定，main factory/真实零tool恢复至ACK/身份3项安装组合PASS4.37s，174/84/116包成员与202加载模块精确核对；PG21846正常退出并清空。源14绿不重跑，原生r17仍待验、r16失败保留。[安装结果与边界](../plans/2026-09-06-typed-use-primary/COMBINED-079618.md)。


2026-09-06：时间提醒生产lane独立源（base7844cf67，产品ec99fa60/7c627fbc）默认注入已有prospective signal authority，并由MemoryAnalysisLane统一拥有独立登记/timer轻量任务及关闭join，避免慢analysis阻止到期；无新schema/SDK制品。4新增控制首批PASS7.15s：真实main activation/publicManager登记到期、重开唯一、已提交丢ACK跨expiry exactreplay、suppression/显式restart、父重复cancel清理（该项受控生命周期fixture）。PG16947正常退出remaining[]/cleanupnull、锁释放；H078M618既有installed+Hostsource，已合候选，r16原userdata实际登记触发成功但前台pending/no_recall仍FAIL；F01事件发布/OS通知未增加。[结果与命令](../plans/2026-09-06-prospective-runtime/RESULTS.md)。

2026-09-06 C02-19原setup关联补强：完整原始S1/receipt与实际group USER精确比较，新增真实同文异Run负控1PASS；已有正向/19绿未重跑，PG19055清空。仅setup来源，runtime隔离/240质量不计完成。[结果](../plans/2026-09-06-corpus-public-seed/C02-BATCH.md)。


2026-09-06 C02全部20条setup已分批通过（18首批、C20及C19失败修复后各1）；C19用真实完成Host/SDK assistant来源保留llm_inference/unverified，C20不补造颜色或通用预算。C01+C02共40条准备验证，240真实质量仍0，运行来源隔离继续。所有测试组已清空。[准备结果与失败历史](../plans/2026-09-06-corpus-public-seed/C02-BATCH.md)。


2026-09-06 原生r16：时间调度修复已在r14原userdata实际恢复并触发1条；普通问题却被SDK pending occurrence/no_recall检查拦截，UI无本轮回答/提醒，端到端仍FAIL。不自动ACK或放宽检查；PG17276正常退出并清空。[原生结果与卡点](../plans/2026-09-06-typed-use-primary/NATIVE-R16.md)。


<!-- 最后更新：2026-09-06 -->

2026-09-06 原生r15：公开SDK准备的2节点/1条APPLIES_TO在真实Cytoscape画布显示、点击边打开正确有向详情；筛选为1节点0边时隐藏详情，清空后恢复原选择。限定图谱UI通过，不计模型抽取/240质量/完整旅程；PG14481正常退出并清空，峰1,327,584KiB。[原生结果](../plans/2026-09-06-typed-use-primary/NATIVE-R15.md)。


2026-09-06 原生r14：一次性提醒后台实际创建且UI记忆可见，前台却否认；到期后真实普通下一轮仅答43，未展示提醒，Host登记/计时/occurrence/presented均0。判时间提醒原生FAIL，正在补生产调度生命周期；不以两Run COMPLETED或旧组件绿替代。PG11237正常退出并清空。[Run与原生证据](../plans/2026-09-06-typed-use-primary/NATIVE-R14.md)。


2026-09-06：大结果边界增量：8k小参数调用的1MiB精确分页通过（最大物理请求19,219字节）；4k预算拒绝后的真实ClosureFallback收尾/冷重开零重发负控通过，保留FAILED与Scope pending，不报4k分页成功。大型assistant参数原4k/8k超限失败保留，未提高预算或复跑32k/8k绿；进程组均清空。[结果与失败边界](../plans/2026-09-06-primary-context-compaction/MEGABYTE.md)。


2026-09-06：当前运行新增1MiB边界控制1PASS/6.32s，两个实际文件结果均超过1MiB，8次物理请求最大28,209字节，精确尾页及重开依赖通过。仅32k窗口/fixture producer/MockTransport，不代表4k8k或原生；PG7739清空。[结果](../plans/2026-09-06-primary-context-compaction/MEGABYTE.md)。


Corpus source/runtime 限定叶三项新控制已分批通过：C01 CREATE真实SDK job物化；
graph backoff重开IDLE拒当成功（Dirac P1闭合）；source实际USER ingestion、原S1
中断导入/重试、scoring新请求无setup/旧assistant历史及自身exact终态group。
最后source1PASS/1.71s，PG19566清空；C03/C04不混入，不称typed/short跨库隐私
全链或240质量，质量执行仍0。源码006a67dc已独审限定接受并合入候选。
[最新限定结果](../plans/2026-09-06-corpus-public-seed/SOURCE-RUNTIME-WIP.md)。


C01-06 history遗忘因果oracle已补：同binding/disclosure在MEMORY-only suppression
前visible、后不可见，定向1PASS，PG8072清空；不新增unique语料计数。
[结果](../plans/2026-09-06-corpus-public-seed/C01-BATCH.md)。

<!-- 最后更新：2026-09-06 -->

C01公共seed后继：18个新case完整记录atomic创建/actualID与内容hash回读通过，
C01-06真实REVISE同ID1→2/持久fixtureauthority/reopen/过期已消费replay及
MEMORY-only suppression→graph/history不可见通过。旧C01-10/graph未重跑。
20个C01都有实现路径；不称240质量/模型或runtime已完成，运行前seed/history与
额外analysis隔离仍待接。无SDK改动。[批次与限制](../plans/2026-09-06-corpus-public-seed/C01-BATCH.md)。

<!-- 最后更新：2026-09-06 -->

公共seed隔离叶：真实Host S1→Memory public ingestion/mutation/receipt链实现
C01-10单记录幂等/重开；独立fixture以同atomic plan建claim+Procedure+applies_to，
公开graph回读2nodes1edge且relation不作node。3unique控制分批绿，原入口红保留。
非LLM提取、非240质量/真实runtime/native通过，其他样例仍NOT_RUN。
[契约](../plans/2026-09-06-corpus-public-seed/CONTRACT.md) ·
[结果](../plans/2026-09-06-corpus-public-seed/RESULTS.md)。

<!-- Updated 2026-09-06 -->

2026-09-06：用户明确将“发布成功后提醒”缺失的实际发布来源接入及对应端到端验收延期为F01。本次不继续推进、不计为通过，其余当前交付继续；已有事件协议层证据不替代真实发布。[后续待办](../plans/2026-09-06-typed-use-primary/FOLLOWUPS.md)。


Updated 2026-09-06: revoked-source/non-success semantic fallback preserves pending debt and genuine FAILED terminal, without constructing a source-bearing model observation. Original main fallback already settled the unclosed-scope flow; earlier current-r3 lacked that component and is not main deadlock evidence. Two new actual-stack controls passed7.75s, including Host terminal.before_commit crash, cold same-receipt reuse/no retransmission, pending replay status and independent next input without withdrawn USER text. Productc6af1ac4; H077/M616 plus Host source/MockTransport, not native. PG4841 empty/lock released; no schema/hash/SDK changes. [Results and baseline calibration](../plans/2026-09-06-revoked-scope-terminal/RESULTS.md).


2026-09-06：真实v4提案混入多种正文被编译拒绝，后继v5按memory_type分支schema并保持旧协议恢复。固定85a19260新3控通过；真实gpt5.5三意图分别产出ACTIVE/DRAFT/DRAFT，无编译拒绝。仅模型分类+编译，非Host持久链/原生/240质量；PG4986正常退出并清空。[实际失败、修复和三条结果](../plans/2026-09-06-procedure-adoption/V5-CLASSIFICATION.md)。


2026-09-06：当前运行分页合并A7的构造器和调用均保留双方参数；固定320a419e在H078/M618实际main factory及current page allow两项2PASS/5.34s。PG3663清空；未closed写Scope撤回后终态pending仍单独修复，原生未开始。[组合结果](../plans/2026-09-06-typed-use-primary/COMBINED-078618.md)。


2026-09-06：历史分页合并A7后，固定c9e1aebf在H078/M618运行1条必要交互检查，1PASS/4.71s，实际首/续/尾页、后续物理请求与重开依赖通过；PG3328正常退出并清空。当前运行分页及原生长旅程仍待验。[组合增量](../plans/2026-09-06-typed-use-primary/COMBINED-078618.md)。


2026-09-06：固定1491309f的H078/M618组合5PASS/4.82s，覆盖实际main factory、A7直接路由ACK终态、Procedure旧v3响应跨配置恢复及Memory身份/锁。173/84/116包成员与vendor一致、201模块全部来自新小target；PG2168正常退出并清空。原生/质量及随后历史分页代码不在此批范围。[当前组合结果](../plans/2026-09-06-typed-use-primary/COMBINED-078618.md)。
Updated 2026-09-06: current primary large generic tool pages now use public settled effect/actual parent request authority, exact page read and final current-source guard. Two new actual-stack controls passed in separate retries (4.06s/3.81s), including reused raw ID, pending exclusion, tail/reopen and post-page forget with a pre-closed real write scope. Original unclosed-scope/withdrawal terminal-pending failure remains. Product0c1b4b38, H077/M616 plus Host source/MockTransport; main H078/M618/native and typed-consumed cross-SDK use are separate. [Results and limits](../plans/2026-09-06-primary-context-compaction/CURRENT-RESULTS.md).


Updated 2026-09-06: isolated primary history tool pages passed 4 unique controls across batches, preserving original failures: real terminal S1/public SDK transcript, Run-admitted summary, exact public first/next/tail pages, actual MockTransport, forget blocking subsequent sends, and full-stack dependency reopen. Existing H077/M616 installed targets; not native/external Provider. Current Run page:causal continues in this leaf; full S5/program remains incomplete. [Results](../plans/2026-09-06-primary-context-compaction/RESULTS.md).

2026-09-06：Procedure提案按新v4区分明确采用/步骤叙述/不确定，Host核真实USER来源与有序引文；明确采用ACTIVE，其余合法分类DRAFT且观察成功数0，ACTIVE不授予执行权限。v3完整协议保留，普通失败跨配置重试P1由M618固定完整输入/cohort恢复；原Host反例零新Provider并应用旧v3语义已实际通过。源码/独立安装验收不代表Scope观察、适用性或真实分类质量；H078/M618组合另验。[来源与范围](../plans/2026-09-06-procedure-adoption/SOURCE.md)。

2026-09-06：A7展示/ACK与来源继承已独审合入候选（固定cd594b8f）。真实五路由ACK终态、三轮未ACK保留pending/唯一overdue、第四轮ACK、终态故障恢复、异主体拒绝及跨轮派生历史遗忘分别通过；slow-source等待期间Host换代真实红例已修复并验证零外发。原no_recall规则不放宽，snapshot注入不当作用户已见。生产默认登记协调器/ACK并由组件升级52；H078组合和原生A7另验，事件触发来源继续。[原红、结果和范围](../plans/2026-09-06-prospective-presentation-ack/RESULTS.md)。

2026-09-06原生r13（Hostb3680732/H078/M617）：新普通对话真实回答45/idle，默认后台审计45/45公开DTO enumerated，SDK明确verified_current_intervals与coverage_gaps[]。只关闭本场景驱动核验，旧r12 unverified不追认，完整工具/Service/Memory覆盖另验。正常退出PG99878、组清空。[Run、截图及审计](../plans/2026-09-06-typed-use-primary/NATIVE-R13.md)。

2026-09-06：H078/M617/S0313接入候选。SDK正式按持久start_mode选择实际driver，保留Host控制校验，避免普通主对话因不透明wrapper失去审计核验；源4项、安装3项、Host新组合4项分别通过。旧r12实际98/98条审计已读取但coverage仍unverified，不追认旧区间；新native/fullcoverage另验。所属进程清空。[组合及真实缺口](../plans/2026-09-06-typed-use-primary/COMBINED-078617.md)。

2026-09-06：提醒状态库50/51/52已接入应用启动及通用初始化的逐版完整校验；新增负控发现并修复bootstrap缺失时绕过human校验的问题。9个唯一新增场景分批通过（非空重开/损坏拒绝/未知版本/fresh49），资源组均清空；完整A7与原生schema52重启仍待验，未默认安装半成品。[结果与边界](../plans/2026-09-06-typed-use-primary/STARTUP-52.md)。

2026-09-06 原生r12（Host33809aae/H077/M617）：同实例启动load+prime完成后，新进程首次short查询真实成功，无手动重试；新工具三条recall refs和模型青竹九月/无糖茉莉茶回答均可见。本场景PASS，工具总耗时1516.972ms不等SDK检索或p95；原预算未增，广泛性能/质量另验。PG96027正常退出/组清空，磁盘5219MiB；原r10/r11失败保留。[首查结果、Run与边界](../plans/2026-09-06-typed-use-primary/NATIVE-R12.md)。

2026-09-06：自有clock树 `feat/wemm-startup-prime`／base082f68c0，源码68f525e2，三项priming新增控制3PASS／0.27s；PG95734无残留、共享锁释放，峰122208KiB、磁盘最低4217MiB。已独审合入primary候选，待真实首次native查询，原r11冷FAIL保留；Procedure WIP未混入，旧绿不重跑。[证据](../plans/2026-09-06-short-terminal-source/PRIMING.md)。

2026-09-06：`feat/closure-resume-source` / baseff2f2009独立叶，固定0a52085e非空resume真实producer与当前来源过滤完成本批限定验证；r1一绿四红、r2仅四红转绿，合计5unique，Dirac限定ACCEPT。实际tool/fallback→后继Run→物理MockTransport闭合、前缀稳定、来源遗忘和原子故障已验；H077/M616限定载体，已合primary候选，主组合/native待验，legacy/篡改/goal/恢复剩余控制与完整compaction继续保留。两新policy仅常量hunk，合主保留Singer typed modes；PG95551退出无残留，未重跑旧绿。[结果/命令](../plans/2026-09-06-closure-resume-source/RESULTS.md)。

2026-09-06 原生r11（Host464b86ee/H077/M617）：既有startup hook实际完成WeMM预加载，但新进程唯一首query的encode1.44s仍超1s预算；UI明确查询失败，未重试，不以r10暖成功替代首查。PG93935正常退出且组清空；仅清可再生Rust链接对象恢复磁盘4.15GiB，native二进制哈希/模型/证据/用户库不变。继续同实例编码预热。[本次失败与资源证据](../plans/2026-09-06-typed-use-primary/NATIVE-R11.md)。

2026-09-06：自有clock树 `feat/wemm-startup-warmup`／base35b07098，源码270320d3接回现有WeMM启动预热，两项新增控制2PASS／0.25s，峰119616KiB、磁盘最低1462MiB、PG93645清空、锁释放。已获Dirac限定ACCEPT并合入primary候选；待真实首冷query，Procedure WIP保留且未混入；旧绿未重跑。[证据](../plans/2026-09-06-short-terminal-source/WARMUP.md)。

2026-09-06 原生r10（Host0bedaa87/H077/M617）：FTS+VECTOR修复后的真实暖态短期查询成功，UI工具有三条recall refs，模型正确回答青竹九月/无糖茉莉茶。冷态首查仍timeout，单独保留失败并继续预热定位；不称完整short/性能/program通过。两轮均空闲，正常退出PG89400 exit0/remaining[]。已合closure九场景修复的原生Scope旅程另验。[实际结果与证据](../plans/2026-09-06-typed-use-primary/NATIVE-R10.md)。

2026-09-06：自有 `simple_harness-corpus-clock`／`feat/short-vector-mode`，base e04627c4，源码 f8b2d41c 完成最小短期检索通道补齐。新增公共 typed→Host fragments 控制 1PASS／4.74s，PG89042 无残留、共享锁释放；只此新增控制，未重跑旧绿。Dirac限定ACCEPT、已合隔离primary候选；待native，未称整体召回质量完成。[结果](../plans/2026-09-06-short-terminal-source/VECTOR-MODE.md)。

2026-09-06：隔离closure物理guard叶（base666b475b、生产5375fc85+4a86ecb0）默认接专用factory，已通过9唯一场景的分批必要控制并获Dirac限定ACCEPT。真实resume依赖读TX→access receipt写自锁独立原红转绿，访问审计未删；不是完整Closure可用，非空resume/缺来源字段仍显式pending。H077/M616限定安装载体与Host本树源码，已合隔离primary候选/未native；SDK和原库未改，所有测试进程已清空，旧绿不重复。[结果、命令和保留失败](../plans/2026-09-06-closure-physical-guard/RESULTS.md)。

2026-09-06 原生r9（Host fa7580b0/H077/M617）：生产Provider清理错误本次未再观察到；short祖先补齐和后台generation修复已经独审合入，实际WeMM生成active索引。真实查询首次超时，模型同Run重试后SDK审计used/FTS1/vector3，但UI最终仍答无片段，短期端到端未通过，返回链路待定位。现场保存后正常退出，资源exit0/remaining[]/cleanup_error=null。此前r8各场景证据与失败历史保留。[最新原生结果](../plans/2026-09-06-typed-use-primary/NATIVE-077617.md)。

2026-09-06：generation叶161702be追加必要冷加载恢复控1PASS/5.60s；两个step串行超时后同一load继续、未确认pending或推进_last_projection，加载完成立即恢复。仅测试/文档增量，旧四绿及WeMM suite未重跑；PG83348无残留、锁释放。[范围](../plans/2026-09-06-short-terminal-source/GENERATION.md)。

2026-09-06：自有 clock 树 `feat/short-index-generation` 从02bf补正常后台 generation。四项新控分批通过，公共查询命中与维护/重开/lost-ACK 幂等已验证；timeout/cancel 不被确认 cache 掩盖。PG82943 清空、锁释放；本叶待独审/主合并/实际 WeMM-native，不重跑旧绿。[范围](../plans/2026-09-06-short-terminal-source/GENERATION.md)。

2026-09-06：`simple_harness-corpus-clock` / `feat/short-projection-source-lineage`（base2c8c57c6）修复短期窗口外缺 terminal 来源：r8 copy-v3 installed M617 公共原红已定位，13 组补 13 source admissions 后 3 chunks/重开一致，三项新回归分批绿，全部资源组清空。Dirac限定ACCEPT、已合主候选；真实 generation-query-native 未完成；Procedure 分支保留暂停，未改 SDK 或原 userdata。[范围及证据](../plans/2026-09-06-short-terminal-source/RESULTS.md)。

2026-09-06：隔离 `feat/provider-cold-terminal-cleanup` 自2c8c57c6，Provider后置清理修复及真实resolver冷恢复单控 1PASS/4.32s，H077/M616限定载体。产品f0f72650、测试79508593；已合主候选，Dirac限定终审ACCEPT；native复验未完成。PG81519 exit0/remaining[]；原r8业务证据与resource125独立保留，不标完整native/program通过。[结果与原红](../plans/2026-09-06-provider-cold-cleanup/RESULTS.md)。

2026-09-06 原生r8：H077/M617/Host2c8c57c6在原userdata真实完成新偏好写入、长期召回命中、UI遗忘后同条件零命中，Cytoscape两节点/筛选一节点可见。旧任务已FAILED但后置Provider清理仍报KeyError；第11完整组后短期投影MemoryCorruptionError，窗口外短召回未通过。正常CmdQ后runner回收残留，资源125/最终组清空。[原生范围、Run与失败证据](../plans/2026-09-06-typed-use-primary/NATIVE-077617.md)。


2026-09-06：候选固定 H077/M617/S0313，授权过期与冷启动修复8cec2353已合；主vendor小target离线安装和3项受影响身份/锁校验通过。旧功能测试按原组合复用，新组合native尚未验收，用户主树不变。[接入与边界](../plans/2026-09-06-typed-use-primary/COMBINED-077617.md)。


## H077 public expiry terminal and cold Host recovery — isolated leaf

From762af1ab, Host reads exact public SDK terminal metadata instead of SDK-private
terminal SQL. Only a bound failed Run with the exact missing-proof error invokes
explicit public eligibility/recovery; ambiguity and other errors remain rejected.
Actual old075 authorization expiry+Stop -> full close -> new077 Host/SDK stack ->
lease reclaim -> real FAILED receipt -> second new-stack exact read passed, with
zero additional Provider sends/context reprepare. Two separate error-dispatch
negatives passed:3 unique new controls total. Missing process-local old tool
registration is skipped only during cleanup after durable verified terminal;
no old grant is reconstructed. Existing-record cleanup errors still propagate.
Pre-recovery Host read and SDK transaction are not cross-store atomic; original
Host final lease/generation fence remains. No original userdata/native/main merge
claim, and077 artifact is unchanged. [Contract](../plans/2026-09-06-expiry-terminal-public-host/CONTRACT.md),
[results](../plans/2026-09-06-expiry-terminal-public-host/RESULTS.md).

# simple_harness — 全局项目状态与架构完成度

## 2026-09-09 MM-D4：设置面板「自动模式」复选框改为渲染权威策略（run5b 复盘）

基线 `6ff7fb46`，工作树 `.claude/worktrees/auto-mode-checkbox`（分支 `worktree-auto-mode-checkbox`，**未合回 main**）。
Manual 模式旅程 run5b 发现：全新 userdata 下后端是 `auto / gen 0 / factory_default`，
而『模型与设置』权限区的复选框渲染成**未勾选**且 ≥8 s 不变；第一次点击对后端是空操作
（仍 auto/gen 0），第二次点击才写成 `manual / gen 1 / user_explicit`。

- **根因全在前端读写路径，后端读源本来就对**：`_authorization_auto_mode()` 读的是
  `service_context` 里由 `uow = workflow_service.execution_uow` → `CapabilityStore(uow)` 建立的
  workflow.db 权威（`backend/main.py:2712/2935`），没有碰 MM-D1 判定的 `sdk-product-state.db` 残留表。
  三处叠加：①`SettingsPanel.tsx:453-459` 的初始值取自 localStorage 显示缓存
  （`cached === null ? true`），webview 缓存与后端 userdata 生命周期不同 → 清 userdata 不清缓存；
  ②`SettingsPanel.tsx:466-487` 只在 `send` **抛异常**时重试，而 `ControlChannel.send()`
  （`ws/ControlChannel.ts:164-179`）在 socket 未 OPEN 时**不抛异常、静默丢帧并返回 false**，
  于是 `permission_auto_mode_get` 丢失后永不重发，界面永远停在缓存值上；
  ③回执走 App 级单槽 `lastMessage`（`hooks/useWebSocket.ts:11,18` 只留最近一条），
  且写入是 `const next = !enabled`——"翻转我以为的当前值"，本地值与后端相反时第一次点击
  必然写成后端已有的模式（CAS 在 `current.mode == requested` 时短路）。
- **修复**：`AutoModeToggle` 重写并导出——状态改为 `policy | null`，`null` = 加载中且
  **`disabled`**（任何点击都不可能被解释成对默认值的翻转）；localStorage 只写不读；
  用 `ch.onMessage()` 订阅 + `ch.onStateChange()` 在 `connected` 时重发 get + 挂载先读
  `ch.getLatestMessage()`；检查 `send()` 返回值，丢帧 500 ms 后重发；写入用
  `buildAutoModeSetMessage(target, requestId)` 携带 `event.target.checked` 的**显式目标模式**；
  不做乐观写，按回执里的落盘状态渲染「当前：自动/手动（策略代次 N · provenance）」；
  `generation` 单调，迟到的旧快照不得把界面拉回写前状态。后端新增
  `_authorization_policy_snapshot()`（workflow.db 权威 → `{enabled, mode, generation, provenance,
  authoritative}`，无 store 时标 `authoritative: false`），`get`/`set` 两个 handler 都回发快照并回显
  `request_id`，**`set` 写入后重读**而不是把请求原样回显；`enabled` 字段保留兼容。
- 用例：新增 `SettingsPanel.autoMode.test.tsx`（8：加载态不可交互、auto→勾选/manual→不勾选、
  缓存与后端相反时以后端为准、写入携带显式目标、加载态点击不写后端、旧代次不回退、
  通道缓存优先渲染、旧回执兼容）与 `backend/tests/test_mmd4_auto_mode_policy_snapshot.py`（4：
  全新 profile→auto/0/factory_default、CAS 后重读→manual/1/user_explicit、无 store 标非权威、
  store 绑定 workflow.db uow 的结构锁）；改写 `permissionMode.test.tsx`（2，原锁的
  `cached === null ? true` 正是病灶，改锁新行为）；回归 `PrimaryTaskPanel.test.tsx`（5，
  TaskScope 面板仍无 Manual/Auto 开关）+ `SettingsPanel.timeout`/`retiredSupervisor`/`dataDir`/
  `CapabilityCenterPanel`（12）+ `test_permission_mode_migration.py`/
  `test_p4s21_permission_gate_auto_mode.py`（10）。前端 27 例、后端 14 例全绿，`npm run typecheck` 通过。
  本轮**未启动原生应用**；前端改动需重新打包 bundle
  （`.local-test-evidence/2026-09-07/native-build-r8/build.py`，由用户执行）后重跑 run5c 复验。
  [裁定](../plans/2026-09-09-manual-mode-journey/DECISION-MM-D4-AUTO-MODE-CHECKBOX.md)。

## 2026-09-09 旅程加固三件：Manual 驱动硬判据 + F-MMD-1 注册白名单 + F-NC1 自改写路由

基线 `43a8f835`，工作树 `.claude/worktrees/journey-hardening`（分支 `worktree-journey-hardening`，**未合回 main**）。
三件互相独立的小项，本轮**未启动原生应用、未跑真实模型**。

- **Manual 旅程驱动（MM-D2 的第 2/3/4/5 条）**：`scripts/native/manual_driver.sh` 把
  `manual_decisions_allow` / `binding_grants_manual` 升级为 T4/T7/T8 的**硬判据**——
  轮末未增加即记 `outcome=failed`（`binding_not_decided` / `binding_not_granted`）并 `exit 3`，
  不再像 run3 那样整趟跑完才发现 `允许本次绑定` 一次都没答成；
  `context_route` 被 `context_route_binding_authorization_required` 拒绝时把该行
  `challenge_ref` 打进日志（绑定轮在真人应答前先打一次）；每轮超时默认 360 → **600 s**，
  超时前先打一次手动决定/绑定计数快照。`00-PLAN.md` 的 T4/T7/T8 与 §3 写明
  **两张卡同时出现、两张都要答、各自 300 s 独立窗口**，且 Manual 下工具卡按工具调用逐次重弹。
  `scripts/native/manual_verify.py` 的 `policy_state()` 改读 **`workflow.db.authorization_policy_state`**
  （MM-D1 的唯一权威，绝不回落 `sdk-product-state.db` 的 DDL 残留表），`--selftest` 现在同时建两个
  内容相反的策略库，读错库自检立刻红；`--selftest` **16/16 PASS**，`bash -n` 通过。
- **F-MMD-1**：新增 `backend/tests/test_main_service_registrations.py`（4 例），用 `ast` 解析
  `backend/main.py` 的全部 `service_context.register(<name>, …)`（含两种 for 循环形态），
  断言 ⊆ `backend/context.py::_VALID_SERVICES`。反向验证：临时删掉 `memory_display_invalidation`
  后两条断言双双转红，恢复后全绿。
- **F-NC1**：`backend/deskpet/execution/primary_context.py` 的 PERSONA 补一句
  `Rewriting or shortening the user's own words needs no context tool or recall.`
  ——A6 NC-1 要求 T3 以 `origin='no_recall'` 作答，尝试 9/10 里 flash 仍调了 `context_route`；
  原文只说过这类改写「不是新建项目任务」，没说过**根本不必调工具**。
  PERSONA 4419 → 4482 字符；为放下它同时做了一处同义压缩
  （`keeps a contested value out of fragments` → `keeps it out of fragments`，被钉住的措辞未动），
  8192 档余量 **19 → 3 token**。PERSONA 进回执哈希，逐字记录在裁定文档里。
- 用例：新增 5 例（4 + 1 钉字）；回归 `test_context_route_tool`(35) +
  `test_token_estimator_calibration`(54) + `test_contested_route_guard`(32) 全绿；
  `test_current_tool_megabyte.py[4096]` 仍为既有红（已用 `git checkout` 还原基线单独复跑确认，非本轮引入）。
  **NC-1 尚未真机复验**，下次 A6 跑之前不得声称已修。
  [裁定](../plans/2026-09-09-manual-mode-journey/DECISION-JOURNEY-HARDENING.md)。

## 2026-09-09 语料 C04：夹具精度注记外泄修复（F-C04-1）+ 多提类型规则 R5（F-ETR-7）

基线 `08881887`，工作树 `.claude/worktrees/corpus-c04-fix`（分支 `worktree-corpus-c04-fix`，**未合回 main**）。
来自 `RUN-C04-RERUN-REVIEW.md` 的两条高优先级缺陷。

- **F-C04-1（跑道/gold）**：`backend/deskpet/quality/corpus_c04.py:temporal_payload` 把
  `【原时间=…；精度=…；具体日时仅synthetic fixture锚点，非原文事实或评分答案】`
  拼进**模型可见**的记忆正文 —— 11/20 例复述给用户（C04-04 写出「评分」二字），
  且 `精度=day/week/month/night/undated` 正是 C04 这一类的考点答案，18/20 例的种子记忆带此注记。
  现拆成两半：精度标签与「synthetic 锚点」免责语移进新的 `precision_oracle(batch)`，
  经 `open_c04_fixture` → `corpus_scoring_session` 的 `setup_receipt.precision_oracle` 只落**worker 退出后**的证据；
  **原文自己的时间措辞**（`9月4日` / `上次` / `8月24–30周`）仍留在正文（最长重叠拼接，不出现 `夜间夜间`），
  否则合成的 12:00 锚点会替原文断言一个它从未说过的精度。prospective 正文不加时间（由 trigger 承载）。
  合成锚点、`SETUPS`/`SCENARIO_CLOCKS`/sha256、生命周期路径一字未改。
- **F-ETR-7（Host）**：`DECISION-EXTRA-TYPE-RATE.md` §3.1 的 P4「兜底加 `semantic`」是唯一没有可判定规则的形态，
  在 C04 重跑上 12/20 命中且**零 `semantic` 片段**返回。补 **R5（semantic 收窄）**：
  「发生了什么 + 我已定了什么提醒」且不问任何长期值的生命周期回顾轮，不得把 `semantic` 当兜底。
  模型可见正文进 `context_route` schema（600 → 623 wire token，钉死上限 643 未动）；
  Host 侧 `selection_policy_departures(memory_types, request=None)` 新增确定性咨询码
  `semantic_fallback_on_reminder_lifecycle_request`（只观测，不拒绝/不过滤/不改写）。
  全语料 **240/240** 条 provider_input 实测：恰好命中 20 条 C04、其它类别 0 条，
  gold 需要 `semantic` 的 C01/C02/C03/C06 零命中。
- **指标预测（非实测）**：累计多提率 38/280 = 13.6% → **26/268 = 9.7%**；required 召回仍 100%。
  **重跑前不得声称 C04 的日期精度能力已验证**；必跑 C04 全 20 例。
- 用例：新增 `backend/tests/quality/test_corpus_c04_payload_text.py`(85)，
  改 `test_corpus_c04_prepare.py`(20，原断言注记存在的三行已反转)、
  `tests/memory/test_recall_selection_policy.py`(84，+11 个用例函数含 20 条真实 turn 与 5 条负控)；
  回归 `test_model_recall_selection`/`test_recall_selection_failure_audit`/`test_model_short_recall`(38)、
  `test_context_route_tool`(34)、`test_corpus_prospective_settlement`+`test_context_route_prospective_runtime`(8)、
  `test_corpus_scoring_trace`+`test_corpus_supported_case_ids`(3)，共 **272 绿**；
  `test_current_tool_megabyte.py[4096]` 仍为既有红（`wire_count 1 != 3`，与 F-ETR-4 记录一致）。
- 一并记录未修：**F-OBS-2** —— `prospective_records.scheduler_registration_ref` 恒 NULL（23/23），
  SDK `backends/sqlite_v5.py:11136-11145` 硬写 `None` + `schema_v5.py:662-667` 的 immutable 触发器 = 死列，
  只读证据审查会误读成「没登记上」；属 Memory SDK 侧（删列或改视图派生），本工作树不动。
  [裁定](../plans/2026-09-07-corpus-c01-local/DECISION-F-C04-1-F-ETR-7.md)。

## 2026-09-09 MM-D1/MM-D2：Manual 授权策略权威 + 目录授权卡片可见性（run3 复盘）

基线 `a0a869f4`，工作树 `.claude/worktrees/manual-auth`（分支 `worktree-manual-auth`，**未合回 main**），
提交 `7cfdac7c`。来自 Manual 模式真人旅程 run3 的两条发现。

- **MM-D1（驱动缺陷，非产品缺陷）**：授权策略唯一权威 = `workflow.db.authorization_policy_state`
  （`CapabilityStore`）。`sdk-product-state.db` 里的同名表是 `product_state/schema.py`
  复用 `CAPABILITY_SCHEMA_SQL` 建库时带出的 DDL 残留，生产从不写它。
  `scripts/native/manual_driver.sh` 读错库导致 run3 全程记 `policy_mode=auto gen=0`；已改读
  `workflow.db`，并在 `product_state/task_grants.py` 标注回落分支只服务夹具。
- **MM-D2（产品缺陷，部分修复）**：Manual 下一次 `context_route` 绑定授权需要用户答**两张**
  互不相交、TTL 各自独立的卡（底部「项目目录授权 / 允许本次绑定」写 `state.db`；SDK 弹窗
  「允许一次」写 `sdk-product-state.db`）。run3 全程 `task_grants(user)=94` 而
  `manual_decisions=0`、`binding_grants(manual)=0` —— 绑定卡一次都没被答成，T4 双 TTL 耗尽后 FAILED。
  本次修可见性放大器：挑战签发与 allow/deny 决定各广播一次 content-free 的
  `human_memory_changed`（此前无人广播，卡片又无轮询）。fail-closed 未放宽。
  **未根治**：应答绑定卡不会重驱已失败的效应（需前台 Run 授权 wait-blocker，
  该片代码本轮由 `worktree-mem-growth` 持有），留作 MM-D2-R。
- 用例 `backend/tests/task_scope/test_manual_binding_display_seam.py` 5 例；
  回归 `test_runtime_binding_authority.py`(5) + `test_task_grant_clock_seam.py`(7) +
  `test_primary_workspace_binding_ui.py`(7)，共 24 例全绿。本轮**未启动原生应用**。
  [裁定](../plans/2026-09-09-manual-mode-journey/DECISION-MM-D1-D2.md)。

## 2026-09-09 F-S1b：Procedure 关系端点扣留解除，A6-6 Procedure 形态端到端可达

基线 `dbf967fc`，工作树 `.claude/worktrees/f-s1b`（分支 `worktree-f-s1b`，已 `git merge main` 到 `1e7043e9`，
未合回 main）。
事件 S 的两条 SDK 事实全部闭合：坑二由 Memory SDK 0.6.35 修（分类决定由血缘上最近的已分类
祖先承担），坑一由 **0.6.36** 修（`check_history_visibility` 新增可选、显式 provenance 的
`ProcedureApplicabilityAttestation`，且 SDK 用自己的 `procedure_observations` 审计佐证该标签）。
Host 侧 `deskpet/memory/semantic_correction.py`：关系候选召回所绑定的指纹持久进候选快照
（新键 `relation_applicability_fingerprints`，随快照一起被 `bind_attempt` 哈希），`check()`
只从快照读同一份构造 attestation；Procedure 候选**仍是 `check_history_visibility` 的普通绑定**，
没有替代谓词（事件 S 备忘 §4.4 的要求）。扣留的解除按**能力探测**而非版本号，同一份代码在
0.6.34/0.6.35 上继续按名扣留、在 0.6.36 上放行；两处 fail-closed（下发前无指纹记
`sdk_procedure_applicability_absent`；复核时无能力/无指纹整批
`analysis_candidate_no_longer_visible`）。事件 S 备忘 §4.1 的 `applied_use_fingerprints`
取舍按要求重新裁定为「可作为复核依据」，残余暴露面记 F-S1B-1。
Host 未 pin 0.6.36（`backend/pyproject.toml` 仍是 0.6.34），验证走独立叠加 venv `.venv-0636`：
合并后主 venv 四个 `test_analysis_relation_*.py` 13 passed / 3 skipped，0.6.36 venv 同四文件
16 passed / 0 skipped（差额恰为三项 `needs_0_6_36`），v8/v9 关系与提案三文件两版均 30 passed。
[裁决与证据](../plans/2026-09-08-hm-to-a6/DECISION-F-S1B-PROCEDURE-ENDPOINT-LIFT.md)、
[SDK 边界](./MEMORY_SDK_BOUNDARY.md)。

## 2026-09-09 域 schema 链单一真源 + Procedure 提示解耦

基线 `243369c0`，工作树 `.claude/worktrees/s5c-cursor-version`。修复生产回归：v55
（`primary/047_…_v55.sql`）未同步 `S5cStore` 的字面量游标白名单，`ProspectiveRuntimeLane`
每一次预约登记都被 v52 封死触发器 `s5c_cursor_successor_required` 拒绝（语料 20 例
SETUP_BLOCKED）。新增 `deskpet/memory/schema_chain.py` 从迁移文件发现链并派生全部准入判断，
清掉 `memory/` 下 19 处字面量 `user_version` 白名单/阶梯（其中 `procedure_use_store` 的
`!= 54` 三处是同类潜伏缺陷：v55 上 Procedure 重试谱系整体被拒）。守卫测试
`tests/memory/test_s5c_schema_chain.py`：AST 扫描禁字面量版本集合、新迁移未登记即红、
完整链到链首后真 `S5cStore` 落登记。可观测性：`failure_identity` 补 SQLite 结果码与
payload-free 约束名，语料结算失败路径保留回执。另 F-ETR-5：`procedure_hint` 触发条件从
`memory_types` 改为「工作流查询 或 Run 已绑定 TaskScope 或 显式请求 procedure」。
具名套件 193 通过 / 20 既有红（与基线逐条一致）；Procedure schema/recovery 4 文件另 12 通过。
[裁决与证据](../plans/2026-09-08-hm-to-a6/DECISION-S5C-CURSOR-VERSION.md)。

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


最后更新：2026-09-06（context-use 测试工具叶子）

## 2026-09-06 current-use 有界双 item 执行交付

`feat/typed-recall-context-use-full` 独立树，basefbebdaff，执行器96ae15e2/transport测试修正24799e99，
尚未合主。H073/M0613及原401/阈值未改。正式仅6格：6OBSERVED、4PASS/0FAIL/2BLOCKED，
剩余395未选；旧182/219保留为原固定源历史，source10独立。两格仍缺Harness reservation/exact-once，
new-continuation不冒执行，S3/program整体未完成。
必要新测试及邻居通过，首轮transport旧输入清单10红保留、窄改后10绿；三批进程组均退出/无残留，
默认OS锁2GiB/180s，槽释放。未跑模型/native/全量或重建wheel。
[范围、实际证据和复现](../plans/2026-09-06-context-use-full/RESULTS.md)。

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

> **最后更新**：2026-09-06

## 2026-09-05 Typed recall 执行桥：分支测试工具验证状态

- 独立分支 `feature/human-memory-typed-recall-runner`，代码 `1e72f2ff`：**76个桥回归通过**；Harness0.7.2 + Memory0.6.5 clean两层消费者 **178 PASS / 0 FAIL / 223 BLOCKED**，public349+source10真实OBSERVED。401/391+10/14攻击/阈值不变；拒绝基线/跨principal及lifecycle grant/历史payload问题均已整改并复审接受。
- 2026-09-05 增量 leaf 小批：12 public真实OBSERVED，**7 PASS / 0 FAIL / 5 BLOCKED**；其余389本轮未运行，不能与历史178相加。合法USER+TOOL双span已接通；leaf本机10测试通过。4个剩余适用性/信号setup、原128byte反例保留；无新产品缺陷结论。[小批命令与证据](../testcase/human-memory-program/runners/TYPED-RECALL-PUBLIC-LOOP-BATCH.md)。
- 2026-09-05 current-use增量：6 public真实OBSERVED，**0 PASS/0 FAIL/6 BLOCKED**；记住→纠正→忘记→reopen及新attempt拒旧result业务断言通过加强后的oracle，两个桥P2已修并独立复审ACCEPT。完整原epoch/continuation门仍未闭合；与leaf合计18个本批distinct cells为7/0/11，不替代完整401历史结果。新增[历史可见性只读设计](../testcase/human-memory-program/runners/TYPED-RECALL-HISTORY-VISIBILITY-GAP.md)，未实现SDK入口。
- **仍未并入主树，合并暂缓；S3/program未完成。** 42 public未实现，合法setup、完整oracle/state等门继续；source10无正式PASS。仅更新工具事实，主共享venv仍0.6.3。命令、hash与明确契约差异见 [Memory边界](MEMORY_SDK_BOUNDARY.md#2026-09-05-typed-recall-执行桥验证工具仅独立分支)。

最后更新：2026-09-06。Host默认Memory builder已接7.3公开升级链；实际installed M616旧库→M617升级/重开保留属主与升级回执，新控1项及空库/未知库2邻居分批通过。原生userdata未升级，完整consumer/native仍未通过。[升级边界与证据](../plans/2026-09-06-prospective-source-audit/HOST-617-UPGRADE.md)。

最后更新：2026-09-06。Host明确接入M617 V2/settle观察，H076/M617实际installed组合4新+4受影响检查共8PASS/2.91s，无源码overlay/模型/native；终局真正消费、跨库恢复及完整scheduler仍单独验收。PG71205清空。[边界与证据](../plans/2026-09-06-prospective-source-audit/SUCCESSOR-617.md)。


最后更新：2026-09-06。独立Host52/H076/M617 consumer新6控分批通过；修复dependency嵌套immutable payload canonical转换与52 timer cursor误读旧表。真实public Manager旧catalog7.2→公开7.3升级、r2 not_required恢复/观察绑定已验；原红保留，无Memory SQL业务读取，非installed616生成旧库。最后PG74424exit0无残留，Dirac限定复审ACCEPT；H077组合/native/event/presentation未验。[结果](../plans/2026-09-06-prospective-consumer-m617/RESULTS.md)。

最后更新：2026-09-06。原生r7包含已审租约修复，冷重建仍在Host读取实际SDK终态时因事件歧义拒绝，STOP_REQUESTED未闭合；没有放宽/篡改终态。遗忘后重启列表仍为空。原生现场采集后正常退出PG69808清空。完整native仍FAIL/未完成。[r7证据](../plans/2026-09-06-typed-use-primary/NATIVE-075616.md)。

最后更新：2026-09-06 14:38。原生r6真实Provider已完成后台分析并生成长期认知记忆；独立UI遗忘后列表及相关当前历史不再展示。semantic召回因授权等待后foreground_lease_expired失败，UI停止未收敛；Cytoscape画布有记忆仍空白。上述缺陷修复中，窗口外short/遗忘后召回未验，完整native/program未通过。完成现场采集后正常退出，PG60384清空。[r6证据与范围](../plans/2026-09-06-typed-use-primary/NATIVE-075616.md)。

## 2026-09-06 后台 analysis 物理出站 guard

最后更新：2026-09-06。产品 `bd5b1180` 将真实 post-turn attempt 与完整输入/来源权限快照
在原事务内绑定；专属 guard 经生产 resolver 校验实际请求、当前披露、来源和候选后才出站。
默认 foreground guard 保留，无新 schema/SDK 制品。14个独立定向控分批通过；最后3项
补强已提交变化断言后通过，进程全部退出。实际 HTTP MockTransport/公开 Memory 物化与
恢复已验；source terminal 是确定性 fixture，无真实 Provider/native 结论。
主已报告合入 bd5 并在 a1fe 接工厂，r6由主独立验证；跨库最终检查非原子撤权事务，
closure/compaction 与 program 剩余项未由本叶完成。
[结果与原失败索引](../plans/2026-09-06-analysis-physical-guard/RESULTS.md)。
## 2026-09-06 前台授权等待租约与Stop收敛独立修复

最后更新：2026-09-06。base8b7c05cb上的独立Host叶，产品9d48465f/e3345565：
Runtime持有keeper跨WAITING续租，同owner过期走原store reclaim(gen+1)，准确恢复原SDKRun，
不重prepare旧history或重start，不扩大默认300秒TTL/权限。真实SDKterminal决定FAILED或
STOPPED；keeper失败/取消与最终读失败均join清理。新10独立控制分批通过，另1受影响mock
邻居单列；Dirac产品/业务测试限定ACCEPT，进程全部退出、共享锁释放。
范围仅同Runtime实例恢复；已合隔离primary候选，重建stack/native尚待主验证，graphblank另列，
不标完整program完成。[结果、原失败与命令](../plans/2026-09-06-foreground-permission-lease/RESULTS.md)。


最后更新：2026-09-06。H075/M616原生r5已实际完成中文Provider响应、WeMM编码、对话写入和审计UI；结束本轮后清空PG54846。后台analysis误用foreground guard已定位，正在修复；短期当前4组处于SDK最近10组排除窗口，尚无窗口外召回证据。完整native/program未闭合。[本轮证据](../plans/2026-09-06-typed-use-primary/NATIVE-075616.md)。

最后更新：2026-09-06。原生启动暴露的服务登记槽与中断空库初始化已修复；新增两项实际 runtime 检查通过，原生主对话恢复可输入。真实 Provider 已返回，但中文输入用例和随后模型加载异常仍未闭合，完整 native 未通过。[本次结论与证据](../plans/2026-09-06-typed-use-primary/NATIVE-075616.md)。
最后更新：2026-09-06。`feat/prospective-source-observation`从c98b6a27独立完成M616 source-read Host持久接收限定叶，14PASS/5.17s、Dirac源码及结果ACCEPT；PG49962退出/子进程清空，共享锁已释放。原typed分支保留，当前未合主；未改SDK/主composition，不把本叶算完整scheduler、401或全操作审计完成。[交付、限制和命令](../plans/2026-09-06-prospective-source-audit/RESULTS.md)。
最后更新：2026-09-06。隔离 Host schema52 新增 typed cursor/独立终局表，保持50/51旧DDL及恢复注册身份、旧游标值/hash，封闭旧writer；正常注册接新版游标，5项新增迁移/故障/拒绝检查通过。not_required 公共回执消费及完整scheduler尚未接完，默认组合未切换。[范围与证据](../plans/2026-09-05-human-memory-s5c-preparation/SCHEMA-52.md)。

最后更新：2026-09-06。[隔离schema51时间事件日志](../plans/2026-09-05-human-memory-s5c-preparation/SCHEMA-51.md)完成新4项及并发发布1项控制；只数据库扩展，完整scheduler和默认接线仍未完成。旧50SQL/默认49不在本叶变更。

2026-09-06 合并锁文件P1已修正并通过新增1项一致性检查；没有重跑此前业务绿色集合，原 program 剩余状态不变。

最后更新：2026-09-06。H075/M616/S0313、typed-use恢复及提醒来源已汇入隔离候选，必要功能/安装身份分批通过；原用户主树不变。S5c scheduler/occurrence/ack、401/240及native仍未完成。[当前组合与限制](../plans/2026-09-06-typed-use-primary/COMBINED-075616.md)。

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

## 2026-09-06 S5c 注册来源局部通过

最后更新：2026-09-06。提醒来源及幂等恢复新增7项在已安装 H075/M616 下通过，S5c 完整 scheduler/occurrence/ack 尚未完成；用户主树及默认 schema49 未切换。[范围与剩余项](../plans/2026-09-05-human-memory-s5c-preparation/PUBLIC-SOURCE.md)。

## 2026-09-06 M0615 installed tool groups

Updated2026-09-06:935d3e12 H073/M0615/S0313 own installs verified169/76/121 members. Original empty-assistant failure is fixed in this successor;22 tests+2 subtests passed13.05s, owned processes cleared. Text tool source chain only; nontext/native/H074/240 remain. [Chinese result and evidence](../plans/2026-09-06-tool-causality/INSTALLED-0615.md).
## 2026-09-06 S5c schema50 successor

Updated 2026-09-06: Primary49 to isolated50; 43 tests passed, real old S5c47/48 rejected without DB byte changes. Default remains49; scheduler/presentation/ACK not wired. Fixedf8e59f31 passed independent scoped review and is merged in the primary candidate. Default remains49; not active scheduler. [Mapping and evidence](../plans/2026-09-05-human-memory-s5c-preparation/SCHEMA-50.md).

## 2026-09-05 S5c schema48 isolated successor

Primary source index now owns global47; deferred S5c is explicitly remapped47->48
without changing tables/authority/thresholds. Actual source/store/public consumer43
passed; a real old unpublished S5c47 database is rejected unchanged. No production
activation or scheduler/presentation/ack/native completion. Historical47 statements
below remain historical, superseded only by [A11-schema48/v1](../plans/2026-09-05-human-memory-s5c-preparation/SCHEMA-48.md).


## 2026-09-06 工具组与可信披露组合接入

最后更新：2026-09-06。工具v2非空组源码及证据、可信披露f3675064的两个P1修复均已由主审和独立代理审查。当前合入同一隔离Host候选1268e884，必要交叉检查21项通过14.55秒，测试组已清空（[证据](../plans/2026-09-06-host-trusted-disclosure/COMBINED.md)）；下列开发记录中的未合并/待独审状态为此前阶段。空assistant仍有M0614真实失败，SDK后继修复中；非SELF、真实Provider/native及240质量尚未完成。

## 2026-09-06 工具多消息v2生产接线，仍有SDK空文本阻塞

最后更新2026-09-06。新实际工具组的terminal/逐消息来源原子提交，Host实际结算attestation、完整6item公开注册/short非空/重开/遗忘通过；写中断无半组、两个来源篡改与五个旧v1邻居通过。新空assistant真实完整组被M0614 short non_blank校验拒绝，保持原红并继续修SDK，不丢消息/填placeholder。故本片未完成；源码独审待续，无真实Provider/native/用户主树切换。[各批范围与未完成项](../plans/2026-09-06-tool-causality/PRODUCER.md)。


## 2026-09-06 工具多消息公开因果读取局部验证

最后更新：2026-09-06。新增内部reader通过实际Host effect index和SDK公开投影/bounded审计/结果读取绑定每个工具与父Provider消息，正确区分跨轮重复raw call ID；真实dynamic Host+SDK一个集成测试（含5个篡改及1个截断控制）后继通过，重复读取不新增audit查看缓存，峰165MiB，进程清空。仅来源投影，未接入terminal producer/短期整组索引，不签工具terminal receipt；原始失败保留、独审待续。[实现边界与证据](../plans/2026-09-06-tool-causality/RESULTS.md)。

## 2026-09-06 Dirac披露并发两P1局部修复

最后更新：2026-09-06。自有feat/host-trusted-disclosure/base955a19cd，整片未合主、待主/Dirac复核。真实双控制连接先复现FIFO陈旧A阻塞B与慢checker换代后仍物理send两红；新增schema49 Host入场拒绝记录（无Run/Memory伪receipt）让A拒绝后B继续，出站checker后新连接复核原token。历史source不改。最终新增及必要邻居94项通过/41.51秒/峰222944KiB；PGID40284及全部本轮组已清空，测试槽释放。非SELF/完整外发原子撤权与240质量仍未完成，未跑真实模型/native。
[两P1修复、接口、schema、原红和指纹](../plans/2026-09-06-host-trusted-disclosure/Dirac两P1修复.md)。

## 2026-09-06 可信披露绑定局部验收与来源回归修复

最后更新：2026-09-06。自有simple_harness-corpus-clock / feat/host-trusted-disclosure，组合点6df952fc（含主30f6b2d4/M614）。可信配置/queue持久绑定及当前解析器局部验收完成；source authority确定回归先红后修，最新29项通过，PGID37740已清理并释放测试槽。前序28邻居结果独立保留，不重复算为最终代码全量通过。仍待主/Dirac终审和主组合整合；240质量、完整非SELF/输入许可/外发未完成。
[实际结果、原红及复现命令](../plans/2026-09-06-host-trusted-disclosure/验收与跨层修复.md)。

## 2026-09-06 Host可信披露绑定待源码复核

最后更新：2026-09-06。复用 `simple_harness-corpus-clock`，分支 `feat/host-trusted-disclosure`，基线 `cbf99364`。可信配置→queue→turn/run解析器已写为生产源码候选，schema48及8个契约测试函数待验证；没有运行测试或占用资源槽，未合入主组合。非SELF门、当前输入permit、完整外发及240质量仍未完成。
[固定源码交接](../plans/2026-09-06-host-trusted-disclosure/固定源码交接.md)。

## 2026-09-06 Memory 0.6.14隔离Host组合

最后更新：2026-09-06。固定ec046e84接入受众绑定候选，独立6.3MiB环境H073/M0614/S0313全部SDK成员与vendor一致；必要组合32项及2个subtests通过，峰399MiB/22.247秒，进程清空。旧M0613环境保留。SELF与不同最终受众默认拒绝；协作者语义配对不构成外部原始历史授权。该结果不代表实际Provider/native或401/240完成。用户主树未切换，原计划继续。
[安装身份、失败保留、命令和证据](../plans/2026-09-06-disclosure-audience/COMBINED.md)。


2026-09-06主复核：clock固定e32a2542纳入cbf99364，7个源码/证据hash一致；受影响实际memory job/semantic correction/history组合11项通过、进程已清理。原6项clock独立保留；[组合复核及限制](../plans/2026-09-06-corpus-clock/主代理复核.md)。以下待整合表述保留为当时历史。

## 2026-09-06 Host业务clock局部验收完成

最后更新：2026-09-06。`feat/corpus-runtime-clock` / base `b34b32c3`，自有稀疏树 `simple_harness-corpus-clock`，尚未合入主组合。可信clock贯通composition、runtime和公开SDK；默认真实时间，进程内lease独立monotonic。安装候选6项必要契约通过，0.579秒、峰值92256KiB、PGID33180无残留。仅完成本片clock边界；有效seed时间筛选、受众用途公共setup、240质量、Provider/native均不在本次通过范围。
[固定接口、测试与指纹](../plans/2026-09-06-corpus-clock/验收结果.md)。

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

## 2026-09-06 Short worker bounded leaf

最后更新：2026-09-06。隔离feat/short-index-worker源426db3bb限定ACCEPT：生产唯一lane
默认登记完整两消息组、固定upper分页环绕、低seq晚delivery补入、ACK确认及reopen重放。
新控制8PASS/6.20s、追加3PASS/4.81s、实际analysis/装配等最后6PASS/9.30s分别列示。
全部测试进程退出；峰值355.4MiB，测试槽已交Carver。未执行主树合并、模型/native或SDK重构建。
全subject projection成本/增量SDK口、多消息producer、新模型short协议仍是后继项；
不标S3/S6/program完成。[交付及边界](../plans/2026-09-06-short-index-worker/RESULTS.md)。

## 2026-09-06 无边图谱标签布局已修复

最后更新：2026-09-06。Cytoscape无边节点用网格，布局包含标签尺寸并允许中文换行，保留有边有向布局及全部身份/遗忘/viewport行为。真实WebKit两个尺寸各7节点：标签重叠17/13→0/0，最终有效渲染字号估计9.53/11.05px，真实选择/缩放通过。
前端18PASS/1个旧API-fixture未配置SKIP，TypeScript通过；所有浏览器/测试进程结束。合成fixture不代表真实API/native或密集边标签完成，原生复验仍待续。
[原红、实际测量、边界与证据](../plans/2026-09-06-graph-label-layout/RESULTS.md)。

## 2026-09-06 短期选中来源已合成

最后更新：2026-09-06。独审2d98e083合入7fafe03a，同时保留审计authority；实际factory每hit完整来源、裁减/遗忘不互相污染、显式长期零short与HUMAN审计WS组合42项通过（21.12秒、峰值238MiB）。
仅已有内部短请求来源路径闭合；自动生产索引worker、多消息完整producer、新模型short协议及原program仍未完成。无新模型/native运行。
[组合结果与边界](../plans/2026-09-06-selected-short-runtime/COMBINED.md)。

## 2026-09-06 Selected short runtime bounded leaf

最后更新：2026-09-06。隔离`feat/selected-short-runtime`/base49249dbd：actual factory
共享真实authority，现有short请求按每hit完整来源投影；保留显式长期零short和最终fresh fence。
分批18PASS/7.04s、3PASS/8.03s，相邻24PASS/19.99s（含1必要重验，不累计作质量分）。
最后门为实际PrimaryHistoryPolicy直接fresh复查，非新physical outbound/native。
峰值202.4MiB；所有pytest退出，测试槽已释放。
固定源2d98e083获Dirac限定ACCEPT，未合主组合，无模型/native/独立install验收。
自动生产索引、多消息tool来源、新模型short协议仍待实现；S3/S6/program仍未完成。
[交付范围、红绿及命令](../plans/2026-09-06-selected-short-runtime/RESULTS.md)。

## 2026-09-06 审计查看入口组合验证

最后更新：2026-09-06。独审1097b272合入c53caff2：记忆面板显式打开用途绑定的HUMAN元数据审计，分页/持久ACK重放、关闭与身份失效拒绝；保留原图谱viewport及遗忘ACK修复。组合独审限定ACCEPT。
后端54项通过，新增真实/ws/control审计往返2项通过，前端44通过/1个可选API-fixture未配置跳过，TypeScript通过。单进程有界执行；没有真实Provider、native或全操作覆盖。初始snapshot成本及原生验收仍待续。
[组合证据、命令与范围](../plans/2026-09-05-agent-operation-audit/human-access-leaf/COMBINED.md)。

## 2026-09-06 HUMAN Memory audit access source candidate

Last updated: 2026-09-06. Isolated `feat/human-memory-audit-access` from54156f1e
adds explicit signed HUMAN metadata grant/open-page-close, Host S1 action source,
durable logical page delivery, unknown-safe replay and final WS disclosure fence.
Default UI entry appears in PrimaryMemoryPanel; it never auto-grants or auto-pages.
Backend40/frontend25 focused combination and no-emit typecheck pass. Actual
ControlChannel cached-bound P1 has counterfactual red and restored-source green.
Independent fixed-source review and main integration/native remain pending; this
does not close all-operation producer/coverage gaps. No source change to SDK pins
or terminal-audit identity. See [results](../plans/2026-09-05-agent-operation-audit/human-access-leaf/RESULTS.md).

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

## 2026-09-06 原生遗忘确认通过；重启验收因内存中断

最后更新：2026-09-06。native b32a96d9 / H0.7.2 M0.6.12 S0.3.12实际点击遗忘，UI显示成功确认，图谱由6条更新为5条且恢复可用。
正常退出后重启验证被系统内存告警中断，未记通过。清理两个约6GB的测试模型后端与子进程后内存回落；本地启动器补进程组清理与单实例/资源准入。
仅该遗忘确认闭环完成，关系展示/标签可读性和重启持久化仍有剩余。[证据与边界](../plans/2026-09-06-primary-forget-ack/NATIVE.md)。

## 2026-09-06 Forget ACK survives primary content invalidation

Last updated: 2026-09-06. Isolated `feat/primary-forget-ack` fromcf4d8e0a retains
only the verified primary ID/readiness across read invalidation, keeping the parent
MemoryPanel's same-connection write correlation alive. Content/detail/graph readers
still retract; actual authority changes clear the reference and preserve unknown
safety. React parent first-red/expanded-red evidence and focused47 tests pass, with
typecheck/build/ESLint. Dirac pre-review found no P0/P1; fixed review pending.
Main owns native integration/ACK proof; no backend/pin or native process changes.
No new native/SDK success claim; seven-node label overlap P2 remains separate.
See [scoped handoff](../plans/2026-09-06-primary-forget-ack/RESULTS.md).


## 2026-09-06 Native viewport verified; forget ACK recovery remains FAIL

Last updated: 2026-09-06. Actual9b57c5c8 rebuilt native app shows real nodes and
passes coordinate selection, zoom/filter/fit/wheel/details. Forget removes content
but parent invalidation cancels ACK listening and exact retry stays unknown.
Full forget/reopen and dense label readability remain unfinished.
See [native scope and retained evidence](../plans/2026-09-06-cytoscape-viewport/NATIVE.md).

## 2026-09-06 Primary Cytoscape viewport repair

Last updated: 2026-09-06. Three frontend files address the reproduced half-height
scroll-pane clipping: responsive canvas, first/explicit reveal, wheel page scroll
with button zoom. Layout-only reveal state survives owner-key graph remount; graph
authority/invalidations are unchanged. Actual WebKit 1000x700/800x560 oracle:
original 8 failures, candidate22 checks pass; focused frontend9 pass/1 optional
API-fixture skip, typecheck/build/ESLint pass. Native exact-build verification is
coordinator-owned and pending; not a renderer-engine diagnosis or full HM-AC6 PASS.
Reviewed `feat/cytoscape-native-canvas` from65a604f8 is integrated here.
See [scoped result and evidence](../plans/2026-09-06-cytoscape-viewport/RESULTS.md).

## 2026-09-05 Memory0612 installed credential successor

Last updated: 2026-09-05. Exact0612 fixes public tool-name credential false positives;
64PASS8.76s startup/composition/Memory audit/preparation rejection. Installed SDK bytes match wheels/source, no source overlay.
Native graph recovery and full operation coverage are not yet claimed.
See [installed identity and checks](../plans/2026-09-05-s6-primary-preparation/SDK-0612-INSTALLED.md).

## 2026-09-05 Host Memory attempt and pre-SDK rejection audit leaf

Coordinator combined06348031 with installed0611: audit/preparation/graph producer
combination49PASS16.43s, no source overlay. Independent leaf ACCEPT retained;
production sealed issuer and full operation coverage remain unfinished.

Last updated: 2026-09-05. Isolated feat/host-memory-operation-audit from7cf2a39c
wires durable Host started/settled around actual foreground typed recall and semantic
correction candidate recall. The default audit composition additionally discovers
verified Host preparation rejections with no SDKRun. Original errors/cancellation
and business results are preserved; recovery never repeats a business effect.
Installed Memory0611 exact-wheel/Host combination32 tests pass, including public
sealed snapshot pages and concurrent reader recovery. Trusted receipt-supplied OA1
reader is implemented, but Host production grant issuance/authorized external
readthrough and all other inventory boundaries remain unfinished. No usage/cost
aggregation or all-operation/native completion claim. Fixed source3ba25c42 has Dirac independent scoped ACCEPT, no remaining P0/P1.
This acceptance applies only to the leaf above.
See [scope and handoff](../plans/2026-09-05-agent-operation-audit/host-memory-leaf/HANDOFF.md).

## 2026-09-05 Memory0611 installed audit and retry successor

Last updated: 2026-09-05. Main-owned candidate pins exact0611, combining reviewed
privacy, bounded SDK audit and current-attempt reclaim repair. Installed55 startup/
composition/graph/runtime checks pass; no source overlay. Main running environment
and isolated native graph0610 remain unchanged. Full Host operation coverage and
Harness successor are still pending. See
[installed evidence](../plans/2026-09-05-s6-primary-preparation/SDK-0611-INSTALLED.md).


## 2026-09-05 Cytoscape graph source leaf ready for independent review

Last updated: 2026-09-05. Isolated feat/human-memory-cytoscape-graph based on bf8f9f7d: backend24/frontend21 plus build/typecheck/focused ESLint pass; actual API-fixture browser selection/zoom/suppression replacement verified. Native verification stays coordinator-owned on043c candidate. No audit/pin changes, no full HM-AC6 or program PASS. Old cytoscape-ui tree preserved, inactive.
See [scoped results](../plans/2026-09-05-s6-cytoscape-display/RESULTS.md) and [contract](../plans/2026-09-05-s6-cytoscape-display/CONTRACT.md).

## 2026-09-05 Selected short source modules combined on0610

Last updated: 2026-09-05. Reviewed selected source reader and source-only conversation
registration/indexing modules are now integrated into the main-owned candidate.
Actual installedMemory0610 ingestion/selected-reader34 tests pass; no source overlay.
Production indexing scheduler and final selected-hit wiring remain unfinished,
so full short-horizon availability is not claimed. See
[contract](../plans/2026-09-05-selected-short-sources/CONTRACT.md).


## 2026-09-05 Terminal audit and privacy combined source verification

Last updated: 2026-09-05. Reviewed terminal consumer is integrated into the
main-owned candidate.55 combined audit/runtime/preparation/privacy tests pass with
frozen Harness fd4a audit source and installed Memory0610. No audit exception
triggers business resend. Actual native043c regression predates this integration;
installed Harness successor audit and other producer coverage remain incomplete.
See [handoff](../plans/2026-09-05-agent-operation-audit/host-terminal-leaf/HANDOFF.md).


## 2026-09-05 Original native duplicate-forget regression passes on0610

Last updated: 2026-09-05. Actual043c722a backend with installed0610 reopened the
original native data. The exact prior failing drink query now visibly answers
“不知道”; canonical physical Provider request contains neither original nor revised
drink text. One foreground call, separate analysis call; app closes normally.
Original failure/action preserved. This is scoped native regression evidence,
not a new complete memory loop or full program PASS.
See [native evidence](../plans/2026-09-05-semantic-correction/NATIVE-0610-REOPEN.md).


## 2026-09-05 Memory0610 installed privacy and queue successor

Last updated: 2026-09-05. Candidate production composition now binds actual Host
source/cut authority to Memory0610 shared disclosure enforcement. An old late-enqueued
USER denied before SDK start becomes an immutable Host preparation rejection:
Run FAILED, turn SETTLED, no fabricated SDK terminal, and subsequent work progresses.
Cross-source rejection reuse is rejected from actual S1 binding; first-action cuts
and legacy unknown boundaries remain distinct. Installed86 tests pass and independent
source/artifact scoped reviews accept. Main and historical native failure remain
unchanged; native successor and full program verification are still outstanding.
See [installed evidence](../plans/2026-09-05-s6-primary-preparation/SDK-0610-INSTALLED.md)
and [queue contract](../plans/2026-09-05-semantic-correction/PREPARATION-REJECTION.md).


## 2026-09-05 Original source and first forget-cut public facts prepared

Last updated: 2026-09-05. Host now captures a v2 forget action's original queue
frontier atomically with its S1, and exposes read-only source/cut facts through
public SDK carriers. Exact retries preserve the first cut; old v1 actions remain
unchanged and explicitly lack a verified cut. Source-overlay fact/API13 pass,
with independent scoped ACCEPT. SDK enforcement/builder/native integration remain
pending; this is not a duplicate-forget PASS.
See [contract and evidence](../plans/2026-09-05-semantic-correction/HOST-SOURCE-CUT.md).

## 2026-09-05 Host terminal audit isolated candidate

Last updated: 2026-09-05. `feat/host-operation-audit` / `simple_harness-host-operation-audit`
固定 source candidate eaccab33 + 3e911c14：实际 main factory/foreground terminal 接入 durable snapshot
consumer，默认开启成熟 lane；坏来源、旧 SDK、损坏 cursor 诚实 unavailable。审计失败不授权
业务重发，不以 projection 数量累加 Provider usage。此树待主集成，不能记为 main installed。
22 新聚焦场景通过，16 相邻 Runtime 场景通过；固定 HEAD/独立 review 结论见
[journal](../plans/2026-09-05-agent-operation-audit/host-terminal-leaf/journal.md)。
这是 terminal audit 纵向片，Memory/Service consumer、完整操作 coverage 与生产后继 wheel
验收未完成；原 Human Memory/program gates 无变化。

## 2026-09-05 USER source and queue admission now atomic

Last updated: 2026-09-05. The service now persists new USER S1 and its queue row in
one fenced transaction. Only first insertion marks a v2 atomic origin; existing
S1/turns retain legacy format and exact replay. Source/queue/runtime-history checks
pass with independent scoped ACCEPT. This closes the source-before-queue crash
window, while duplicate-source suppression and native retest remain incomplete.
See [implementation and validation](../plans/2026-09-05-semantic-correction/ATOMIC-SOURCE-ADMISSION.md).

## 2026-09-05 Native correction passed; duplicate-source forget remains P1

Last updated: 2026-09-05. Backend c2836c12 with exact Memory0.6.9 successfully
created and revised the same memory in the actual native app. The panel forget
produced a real directive, but an older duplicate USER from a rejected CREATE
remained in the next actual Provider request and the model returned the old value.
The memory loop is **FAIL**. Deterministic coverage is1PASS2FAIL for no duplicate,
pre-forget admission and delayed admission. A fresh same-text USER/replayed-action
control passes only in the no-duplicate case. Required source-order/cut/equivalence
repair is underway; no main cutover or program completion claim.
See [native evidence and exact boundaries](../plans/2026-09-05-semantic-correction/NATIVE-DUPLICATE-FORGET.md).
Earlier entries below describe their own historical checkpoints.

## 2026-09-05 Native memory loop remains incomplete

First069 real remembered-preference Run completed but the actual analysis CREATE
was rejected for an invented candidate key; memory panel confirmed no new memory.
Versioned v3 CREATE representation now passes source/real-store controls and
independent review, pending native rerun. Stage3 truthful memory-pending/failure
presentation is still outstanding. [Evidence](../plans/2026-09-05-semantic-correction/CREATE-SLOT-V3.md).

## 2026-09-05 Memory069 candidate installation and existing data

First native startup exposed a stale067 startup identity pin. Four constants now
match exact069;32 existing candidate/composition tests pass. Failed startup remains
recorded and does not count as native PASS. See the evidence link below.

Exact0.6.9 installed in dedicated candidate environment:29 affected tests PASS and
actual earlier native-data copy upgrade/reopen PASS, with original bytes unchanged.
No other lock dependency changed. Main running checkout remains on its earlier
composition. [Evidence and remaining gates](../plans/2026-09-05-s6-primary-preparation/SDK-069-INSTALLED.md).

## 2026-09-05 Semantic correction connected in the combined candidate

Main startup and deterministic real-store tests share one authority composition;
29 distinct affected cases pass after repairing one test import. The current
candidate adds actual correction wiring, while real UI/provider memory-loop and
SDK069 existing-data migration verification remain pending. Main running checkout
has not switched. See [results](../plans/2026-09-05-semantic-correction/RESULTS.md).
The user's new Host/Harness/Memory/Service operation-audit requirement is recorded
in [its scope and oracles](../plans/2026-09-05-agent-operation-audit/PLAN.md).
Existing diagnostic streams are available; complete recording and batch auditing
are not yet claimed.

## 2026-09-05 Typed selection forget barrier exercised

Actual public typed selection/Context route now uses the cognitive forget API
before the next production pre-invoke guard. Positive sends2; late forget sends1
and rejects the next stale request. Two controls passed2.18s with deterministic
analysis/MockTransport; no native or paid Provider. This adds the previously missing
typed-source proof to the existing history/reopen coverage. See [API evidence](../plans/2026-09-05-primary-cognitive-controls/API.md).
A further2 typed-only controls pass with no remembered source in chat history;
removing only the recall binding would allow the same request, proving the selected
source barrier itself. Public fixture materialization, no real model in this pair.
SDK069 migration and semantic-action builder hooks are prepared but those new
branches still await their actual successor candidates; see [runtime preparation](../plans/2026-09-05-s6-primary-preparation/RUNTIME-SUCCESSOR.md).


> 本次 leaf 更新：2026-09-05

## Selected short-source reader isolated candidate

`feat/human-memory-selected-short-sources`从54aa2f88提取固定55b9e402 registration/indexing
依赖，新增selected-only来源reader与契约；19项聚焦source-overlay、定向ruff通过。
缺proof整hit拒，未选root不进入union。main.py/runtime/context及默认调度未改，由主组合；
exact069 cf149022 + Harness072独立installed同19项已通过，不与source重复相加。
原reconcile全扫描、最终writer边界和主组合验证仍独立保留，未合主/无native或付费
Provider，不改变program完成度。详见[契约](../plans/2026-09-05-selected-short-sources/CONTRACT.md)。

## 2026-09-05 Primary cognitive panel connected locally

默认主对话入口已接认知记忆面板及真实HUMAN API；current signed owner限制读写，
同owner隐藏/重挂载保留未决动作，换owner清空。匹配forget ACK同步清历史/detail再补读。
父视图组合24项、tsc及定向lint通过；backend已独立限定ACCEPT。尚未native真测，
完整进程重启不保留UI内存动作ID；自然语言纠正/全闭环仍待完成。
见[组合记录](../plans/2026-09-05-cognitive-controls/COMBINED.md)。


## 2026-09-05 Authenticated cognitive read/forget API candidate

Primary HUMAN接口已接实际V7 public graph/suppress与专属动作S1，精确目标选择与原动作重放；
真实连接在慢读后/Host admission后失效分别阻止后续写，SDK ACK丢失后同ID/time可确认。
最终wire9项及并发收敛1项通过；实际API忘记后重启，旧page/detail与下一实际Adapter
出站均不含已忘来源。初始相邻46通过。只证明SQLite/签名连接/确定性transport，
frontend/native及自然语言纠正尚未完成，不能称用户闭环已完成。
见[接口与证据边界](../plans/2026-09-05-primary-cognitive-controls/API.md)。

## 2026-09-05 Explicit semantic correction isolated leaf

Follow-up: fixed800ff419 independently scoped ACCEPT (no blocking P0/P1). The v2
prompt now names new drink preferences `user:self + drink_preference`; no alias or
authority expansion. Five Chinese public-SDK cases passed (5.01s). Native model
compliance and coordinator builder integration remain pending; details in results.

From9ec0ec97, the isolated semantic-correction candidate adds actual public typed
semantic candidates, Host independent full-sentence intent and exact public REVISE
authority. **32 focused tests passed (19.13s)** on installed067; Chinese natural
correction/quoted/negative, ambiguity, original evidence, replay and late-forget deny
covered. v2 prompt/schema/policy/validator; no new schema or display graph input.
Main/Runtime wiring belongs to coordinator; fixed-source review and native complete
loop remain pending. Limited Chinese slot vocabulary and unsupported cases are explicit;
this is not unrestricted natural-language or full program completion.
See [contract](../plans/2026-09-05-semantic-correction/CONTRACT.md) and
[results/boundaries](../plans/2026-09-05-semantic-correction/RESULTS.md).

## 2026-09-05 Source/auth/action combination verified

Fixed e31c6efd source index closes the independent unscoped-search/late-forget P1;
combined with exact SDK decisions, source-aware history, schema47 and action evidence:
**51 passed** on installed067. Only test-fixture signature required merge resolution.
The separate f9cbb7c8 native candidate also passed expanded visible authorization
by real mouse click; this combined tree has not run native. Cognitive UI/SDK suppress
and selected-source indexing remain incomplete; no main cutover or full program PASS.
See [combined evidence and boundaries](../plans/2026-09-05-primary-effect-sources/COMBINED.md).


> **最后更新**：2026-09-05

## 2026-09-05 Cognitive controls frontend leaf prepared

`feat/human-memory-cognitive-controls` 新增认知面板及请求 helper，13 项前端聚焦、tsc 与
定向 eslint 通过。原 wire 保持不变，新增 ACK 后即时 onForgotten；parent-owned 请求实例
保留同 owner 未决重试。backend 与共享入口交主接管，当前提交仅前端/文档，未合主、
未做 native/Provider，不改变 loop2/program 完成度。
见 [FRONTEND-CONTRACT](../plans/2026-09-05-cognitive-controls/FRONTEND-CONTRACT.md)。

## 2026-09-05 Cognitive action evidence callback prepared

主协调候选新增显式忘记动作的Host evidence回调，复用既有S1表和首committed_at；
独立source域区分generic primary.append，精确动作重试保持同一时间/SDK请求ID。
真实Host SQLite四项通过，无新Run/分析outbox；尚未接认知面板/SDK suppress/真实UI，
不是已默认可用的忘记能力。接口和边界见[ACTION-EVIDENCE](../plans/2026-09-05-primary-cognitive-controls/ACTION-EVIDENCE.md)。

## 2026-09-05 Primary 授权 portal 布局候选（历史记录；后继 native 见顶部）

decisions 树从1862e383修 Primary modal 的 DOM 挂载层级与隐藏视图 Escape 生命周期，
不改后端/SDK/授权语义。实际 WebKit 两种窗口尺寸布局通过；前端59、backend decision18、
tsc/lint通过。修前复现的是 containing-block 压力控制越界及隐藏视图响应 Escape，
不是 native 合成失效的完整根因复现。主将 cherry-pick 至完整候选并重建 native；
后继卡不可见的 Stage1 阻项仍待实际可见鼠标点击关闭。loop2仅完成只读缺口交还，未扩业务。
详见 [UI](UI.md) 与 [布局验证记录](../plans/2026-09-05-primary-sdk-decisions/RESULTS.md#portal-layout-candidate-after-1862e383)。

## 2026-09-05 Combined decisions retain authenticated history context

在067隔离候选中组合精确SDK授权，保留实际HUMAN request_id派生的USER_REVIEW披露上下文；
修复新history state签名与旧decisions读法冲突（真实生产fixture先红），并组合持久WAITING后通知（runtime16 passed）。20项授权验证及53项
受影响API/history/foreground相邻通过；当前USER被public EVIDENCE抑制后，授权参数不披露、
批准被拒、SDK决策仍open且无新Scope/Provider。真实主机模型/布局、MEMORY-only、Scope未route
search来源均不由这些测试覆盖；完整候选仍不可切main。见[组合记录](../plans/2026-09-05-primary-sdk-decisions/COMBINED-HISTORY.md)。

## 2026-09-05 首批固定 history 候选组合

Memory0.6.7 依赖候选与固定 history/helper7dcfce8b 已组合，未带入子代理未提交代码。
实际 runtime/API/遗忘与出站、v2 helper、生产组装、未知调用分类和 CREATE_NEW 自动绑定效果
受影响组合 **42 passed**。预先 scoped 的来源功能 P1、真实授权 UI 和短期登记生产者尚在后继修复，
本组合未起 App、不能称完整产品可用。命令与本地 hash 见
[CANDIDATE-067](../plans/2026-09-05-s6-primary-preparation/CANDIDATE-067.md)。

## 2026-09-05 下一主对话候选依赖固定

隔离 primary-candidate 树固定 Memory0.6.7/既有 Harness0.7.2/Service0.3.12，并将滞后的 uv.lock
对齐既有 pyproject 要求；九项非 SDK 版本变化与已真测主环境一致。三个 SDK 从本树 vendor 装入
独立环境，身份/生产 composition **21 passed**，offline lock check 通过。未 sync 主环境或启动 App，
完整历史/恢复/短登记/授权 UI 仍待合入；见
[CANDIDATE-067](../plans/2026-09-05-s6-primary-preparation/CANDIDATE-067.md)。
## 2026-09-05 Primary 精确 SDK 授权独立候选

`feat/human-memory-primary-decisions`（base `5da24d6f`）接入 authenticated HUMAN
`primary.decisions.list/respond`。主对话权限卡通过 App 已 bound 的 ControlChannel
读取实际当前 Run 的 SDK decision；不使用 secondary controlWS 的旧权限补读/ACK。
提交前重验当前 Host run/generation 与连接 scope，复用 SDK exact decision API；旧未认证
permission_response 对 Primary binding 拒绝。UI 仅允许本次 allow/deny，区分 expired，
超时不自动重发，重挂载/重连/通知补读，不把 Primary ID 当 Session ID。

真实生产授权策略 + installed SDK + SQLite + signed HUMAN scope + 实际 scheduler wake
的确定性 fixture 完成 challenge→批准→项目文件 effect→终态；受影响 backend 66 passed，
最终新增聚焦 18 passed，前端后继 36 passed + typecheck。批准后并发补读不能吞超时错误，已补红绿；独立 review 待完成；
未起 native/真实 Provider，不是 S6/program PASS。Carver 的 WAITING 通知须另行组合。
SDK read_decision 是 public port，但旧 open-decision 列表仍是 Host 内部 SDK SQL；本片未扩
私有 SQL，仅限制返回最多32，不能声称底层扫描有界。停止结果历史缺口维持独立未闭合。
详见 [契约与测试边界](../plans/2026-09-05-primary-sdk-decisions/CONTRACT.md)。

## 2026-09-05 Primary 生产目录修复

真实原生普通回复/重启追问通过；新项目请求在 `607acc7d` 暴露三项 Context 控制被
requires_project 默认值过滤，零 Scope/文件。仅三项 Host 注册补 safe，真实生产 composition
红→绿与相邻验证 **34 passed**；修后原生目录已完整，但模型仍沿旧历史要求目录、零工具调用。
已补主对话当前路由指引，原运行回归 **16 passed**；原生 `5da24d6f` 已实际调用 create_new，
但停在 SDK 工具授权等待，Primary 未显示授权卡，仍零 Scope/文件。真实点击停止后 Host STOPPED、
SDK cancelled、待决授权 cancelled，界面回空闲；仅此等待状态的停止通过。原始失败与命令/哈希见
[INTEGRATION](../plans/2026-09-05-s6-primary-preparation/INTEGRATION.md)。

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

## 2026-09-05 Runtime v2 / new message producer combination

运行层保持v1可读并保真v2 short triple/actual UTF8及Host source roots；缺source proof或伪audit
拒出站，不伪typed。深冻结来源快照修复先红后绿；installed exact067（7dd224…）相邻60 passed。
首次terminal observer同tx追加真实message S1，marked replay只验证不修复，旧无marker不补造；
helper模块已正式入树。Hegel11group登记/selected来源闭合仍另线，不把此60绿称Host short pipeline
或native完成。精确命令、版本和证据见[运行契约](../plans/2026-09-05-s6-primary-preparation/HISTORY-RUNTIME-CONTRACT.md)。

## 2026-09-05 Waiting state invalidation

Foreground在BOUND_WAITING reconciliation提交后调用现有空payload、有界非阻塞刷新。
回归刻意等此前SDKbind通知完成，旧代码1红，修复后runtime/真实primary相邻32 passed16.31s。
这是状态通知证据，真实授权卡/decision UI组合由另一所有者验证。原始日志位于
`.local-test-evidence/2026-09-05/primary-history/waiting-notify-{red,green}.log`（ignored）。

## 2026-09-05 Scoped ordinary projection restoration candidate

初始scoped、动态resume/search共用确定派生manifest，无新ledger。真实CREATE_NEW操作前
依赖快照+actualSDK effect/route绑定保留title/goal；旧/抑制文字明确fieldgap，仅结构+真实
binding+当前USER继续。实际保留字节与原fullview hash分开，start/dependency每次出站重验。
相邻96 passed（含真实MEMORY-only抑制后fileeffect/terminal）、最终start负例2 passed；
初始scoped旧功能红已恢复。未知旧mutation/checkpoint文本仍不声称可恢复，short登记另线，
等待独立review与主组合，不称native/program完成。详见[来源契约](../plans/2026-09-05-primary-resume-sources/CONTRACT.md)。

## 2026-09-05 短期来源的历史读取契约

Primary shared history helper 增加 v2 的独立 short_horizon 三元组，旧 v1 原样兼容；所有来源仍在
同一公开 Memory batch 校验，递归终态与旧 detail 引用不绕过。Memory0.6.7 独立安装环境下，
Host shape/batch/递归 API 及既有历史用例 **53 passed**；正向短项为明确的 policy fixture，另有
真实 Manager 拒绝未选择 audit。该层通过不代表 Host 对话登记/短期索引已有生产数据。
实际短期生产登记、runtime 组合和原生验收继续单列，见
[SHORT-V2](../plans/2026-09-05-primary-history-api/SHORT-V2.md)。

## 2026-09-05 主对话历史跨层遗忘验证

隔离组合 `888efe0c` 上，真实 Host/Harness/Memory SQLite 与 ProductProviderAdapter 的确定性
HTTP transport 完成两轮普通对话，随后只抑制一个真实 memory ID：公共 page/detail 隐去原用户及
两个依赖回复，独立 USER 保留；重启不重发，下一次实际适配器请求不含已忘内容。三项组合 **3 passed**。
旧 scoped observer 缺完整来源时仅保留原 USER；该测试不覆盖生产初始 scoped guard 的现存 P1。
无外部模型/原生 UI/main SDK 切换或全 program 通过声明，见
[跨层验证](../plans/2026-09-05-s6-primary-preparation/HISTORY-CROSS-COMPONENT.md)。

## 2026-09-05 Primary history runtime review slice — not production-ready

隔离history树（base284ea40b）接shared primary visibility policy、真实start/terminal依赖、
actual typed recall四元组与每次physical Provider前fresh check；cold USER/terminal S1无需先ingest。
后继独立review发现no_recall marker被误当effect导致ordinary历史丢失，已按真实origin纠正；
相邻契约合跑41 passed/1 failed（仅初始scoped），exit1，非整体PASS。
原切片94 passed不证明动态历史来源完整；后继动态ResumePackage漏发已先红后绿，聚焦21 passed。
每条实际route交叉验证SDKeffect/receipt；无来源证明的ResumePackage在delegate前拒绝，
FAILED且第二次物理发送为0。真实create_new在逐次guard下完成effect/terminal；已发送unknown分类未改。
**仍有P1：既有预先scoped ResumePackage无完整proof，新guard会拒绝首Provider（真实probe1红）**。
不得以94绿抵消该回归或用scope豁免。共享API ea315525已正式cherry为2645d8b2，移除helper加载层后组合71绿；
独立venv已安装exact Memory0.6.6（其他Host依赖只读借用），窄复验10绿。仍仅隔离组合，
没有生产切换/native隐私完成结论；独立short-horizon carrier仍不支持。原证据不删除。
命令、边界及hash见[运行层契约](../plans/2026-09-05-s6-primary-preparation/HISTORY-RUNTIME-CONTRACT.md)。

## 2026-09-05 Primary 工具活动事件契约

隔离组合树补齐真实 SDK 调用到 `tool_call` 的 `call_id`，使主对话可对应工具开始/返回。
既有真实 SessionDB 用例精确复现缺字段，修后 delivery **14 passed**；原生工具交互仍待验证，
不计 S6/program 完成。证据哈希与命令见 [UI](UI.md)。

## 2026-09-05 primary history visibility API 隔离候选

`feat/human-memory-primary-history-api` 从 `87c42b43` 增加 Host shared
`PrimaryHistoryPolicy.check_evidence_ids/check_dependencies`：真实 S1 subject/primary/hash
与终态身份校验，递归 evidence/recall 依赖合并为一次公开 Memory batch；state/page/detail
在慢读取后 fresh 检查，真实 WS request_id + authenticated subject 构造 USER_REVIEW，
输出前重验现有 connection fence。旧终态无依赖证明只隐藏 generated group，原始 USER
不依赖异步 analysis。未改变归档、SDK、schema、pin、main.py 或 runtime 文件。

本隔离实现聚焦 **50 passed**，使用独立环境 exact Memory0.6.6 / Harness0.7.2：真实
Memory memory-only forget 反向隐藏原始 USER、其 assistant 及跨 Run 继承 assistant，
无关 USER 仍可见；真实 recall 四元绑定正负、reopen 和 Host 证据字节不变通过；
in-process production WS 验真实绑定/request_id，慢 batch 期间重连拒绝旧响应。
这些是 library/API 证据；SDK transcript fixture 不替代 Carver runtime 组合测试。
尚待 Dirac 独立审查、主组合与 native/provider 验证。short-horizon 仍缺本轮可用的 exact
carrier，必须在运行层拒绝复用/出站；不称 S6/S5b/program gate 完成。
见 [接口与验证](../plans/2026-09-05-primary-history-api/CONTRACT.md)、
[验证记录](../plans/2026-09-05-primary-history-api/VALIDATION.md)。

## 2026-09-05 主对话隔离组合验证

运行层、API 与新前端已组合；真实 SDK + SQLite + deterministic Provider 经公共历史 API
验证新普通对话/旧 scoped 终态、重启标识一致与 raw event 错绑拒绝，聚焦 **51 passed**。
原生候选 `87c42b43` 的真实 gpt-5.5 普通对话已回复，未创建 TaskScope；正常退出/重启
看到历史恢复，账本确认前台/分析调用均未重发。解锁后的第二轮追问也通过，实际出站请求包含原用户/助手历史。
完整来源遗忘与新建项目授权仍在修复，未切换 main、未标 S6/program 完成。证据哈希见
[INTEGRATION](../plans/2026-09-05-s6-primary-preparation/INTEGRATION.md)。

## 2026-09-05 primary 前端独立候选（集成待续）

跨 attempt 未决 delivery P1 已完成代码与聚焦自动化修复：原 ACK 未知后，重试的本地未发送或后端
拒绝不再清原 key；首次明确未发出不锁死草稿。新增三例修前 2 failed/1 passed，controller/requests
修后 25 passed，typecheck/受影响文件 lint 通过。原始证据：该树 ignored
`.local-test-evidence/2026-09-05/primary-ui-retry/`；Dirac 固定源码独立 ACCEPT（29 项及原重试/owner
切换探针通过）。原生发送复验仍待主组合，不计为 UI 验收通过。

独立 worktree `/Users/denny/projects/simple_harness-primary-ui`，分支 `feat/human-memory-primary-ui`，
base `c183fe70`：Workbench 单主对话入口、真实 bound 连接上的 durable 历史/ACK 草稿提交、精确
run/generation 控制与回执分类、read invalidation/有界补读、真实 hidden execution mapping 的权限组件
接线已完成本次前端实现。聚焦100 passed + 权限组件集成1 passed，typecheck 通过；InputBar 旧 lint
导出规则同因红已在 base 复现。见 [UI 当前事实](UI.md) 与前端 `primary/UI-CONTRACT.md`。

真实项目交互/恢复未验证：旧目录 live-only 卡存在映射晚到/重挂载/重连丢失的条件性 P1，但其旧 Host
execution 回传不是 Carver 确认的动态 context_route.create_new 路径。实际待接为
`binding.manual.propose` → `binding.manual.decide` → `route.resume_existing`；主协调已暂停旧目录
pending API 扩展。Manual/route UI 未实现/未真测，不得把该前端包当成首项目任务可验收。
模型使用全局 Provider 设置；附件/slash/Realtime、完整 Artifact/Context/TaskScope 保留项尚待接线。
未起 App/Provider、未修改 backend/SDK、未写 gate，原 S5b FAIL 与全量限制继续保留。原始证据仅在
该树 ignored `.local-test-evidence/2026-09-05/primary-ui/`。

## 2026-09-05 S5c T3 独立 consumer 进度

仅在 `feat/human-memory-s5c-preparation` 新增 registration 恢复投递，自己的 store 追加 exact result receipt。
新增21项通过（3项为安装 SDK 实库），既有66项相关回归通过；故障和修正记录均保留。
生产 source resolver、due/event/唯一 scheduler、T4/T5/T6 尚缺，独立 review 待主协调。
没有 main/Carver runtime/terminal/ingestion、SDK/pin 或默认 schema 改动；不声称 S5c 完成。
[本切接口与限制](../plans/2026-09-05-human-memory-s5c-preparation/T3-CONSUMER.md)。

## 2026-09-05 S5c T1/T2 独立 worktree 进度

`feat/human-memory-s5c-preparation`，worktree `/Users/denny/projects/simple_harness-s5c-preparation`，
基于已提交 `c183fe70`（Q1 + downgrade 测试）。显式 v47/schema/store/只读 authority 接缝及
决定性/相关回归 66 passed；Ruff E/F/I 通过。生产默认仍 v46，未改 main 入口、SDK 或 pin。
独立 review 待主协调后才能决定合入。T3/T4生产scheduler/snapshot/ack未接线；T5 SDK priority
和T6披露/旧checkpoint接口仍BLOCKED。不存在 S5c全绿、真实provider或program完成声明。
详情：[本切接口](../plans/2026-09-05-human-memory-s5c-preparation/T1-T2-INTERFACE.md) /
[验证记录](../plans/2026-09-05-human-memory-s5c-preparation/journal.md)。

## 2026-09-05 当前修复与受影响验证

Host episode 时间修复提交 `4eb1eb7c` 后，effect/closure/analysis/FIFO 受影响集合 **150 passed**。
新真实入口 root `5aa01b80-6fc6-5880-9f1d-3b68e06d3dcf` 在第七次 Provider handoff 后发生传输错误，
SDK durable invocation=`unknown/provider_error_after_handoff`、rehandoff=0，Host 仍 CLAIMED。
六次已成功调用保留，README 尚未改变、analysis 未执行；主执行者停止该隔离 backend，
该次复验 **FAIL / 外部传输受阻**，第二 root 未启动。不重发未知调用，不用旧候选两根覆盖此结果。

REG 的 downgrade 新原因已独立确定为测试 fixture 版本错误：原 AC 要求旧 runtime 拒绝未来 schema。
测试现由 exact SDK 0.6.2 自建 v6 空库验证两次 reopen，创建真实 recoverable Run 验证拒绝，
另以当前 SDK 的 v7 库验证旧版拒绝，三个路径均核对原文件 hash 不变。整文件 **9 passed**，主审接受，
无产品 schema/gate 放宽。全量原六红中新增候选 hash 字面量（20 passed）及该项均已有定向闭合；
剩四项历史同因失败保留，不声称重跑全量绿。日志索引：`q1-current-affected.log`、
`tools/a14-20260905T101023-yk7hji3c/root-1/stop-reason.json`、
`independent-review/downgrade-v6-v7/REPORT.md`、`independent-review/downgrade-test-correction/pytest-green.log`，
均相对本机 `.local-test-evidence/2026-09-05/human-memory-resume/`。

现有 r5 保存旧候选 FAIL/FLAKY；gate 对跨候选 root 聚合及批准后 acceptance hash 接续存在限制，
只读诊断见 `tools/gate-candidate-continuation-20260905T100858/HANDOFF.md`。
未删历史、未修改 gate 工具、未豁免稳定性；S5b 和 program 均未得到 machine finalize PASS。

## 2026-09-05 Primary API P1 后继修复

`fbc026a0` 的40项局部绿色未覆盖实际 Host authority/raw SDK hash 差异，独立组合 probe
已复现真实完成历史拒读。后继 API 改用 Carver 唯一 terminal_identity helper 验来源链，
再精确核对真实 SDK event；还修正 active source 抑制、slow reader 后整页 source 复查、
SQLite 已 commit 后 wake 失败仍返回 durable ACK（0.5秒通知预算）。没有新增权限或 ledger。
公开 suppression 没有 batch/snapshot/epoch，最终逐来源复查不等于原子隐私快照，
memory/entity lineage 扩展仍后续。该提交必须与 Carver helper 组合；真实 runtime API
集成测试由主维护并待其运行，不能把局部测试算产品闭环。
聚焦 source overlay 验证45 passed（29 API+16既有），尚待主组合测试与独立复核。
详见 [PRIMARY-API](../plans/2026-09-05-s6-primary-preparation/PRIMARY-API.md)。

## 2026-09-05 Primary API 隔离切片

`feat/human-memory-primary-api`（base `29902ea4`）实现 `primary.state`、
`primary.messages.page/detail` 与 exact targeted `queue.control`。读取真实 Host
primary/turn/source/binding/terminal receipts，并通过注入的 SDK 公开 transcript/binding reader
验 run/event/hash/user anchor；queued user 可读，公开长文本可分块完整取回。
revision 读取既有 append-only 表尾及当前 head，page 最多扫描10 turns，queued policy
最多100项，超限计数明确为下界。无新 schema/计数 ledger/Session。

过滤结论仅覆盖当前 subject、Host 输入及终态观察 evidence；Memory resolver 不展开
memory/entity lineage，不能声称所有 memory-derived 历史的隐私已闭合。缺 policy/reader
拒绝对应读取。Carver 已在其隔离树注入三个 factory kwargs，组合树运行与 UI 验证尚待主协调；
本分支不改 main/runtime/fence/SDK/pin，不代表 S6/program 完成。

聚焦验证：新增24 + 既有16 = **40 passed**（公共 API/真实 Host SQLite 与已安装 Memory
suppression backend；SDK transcript/binding 为注入 fixtures），未跑 provider/UI/全量。
契约、命令与 ignored 证据索引见 [PRIMARY-API](../plans/2026-09-05-s6-primary-preparation/PRIMARY-API.md)。

## 2026-09-05 S6隔离分支：control复用与无scope admission

`feat/human-memory-s6-primary-preparation`（base `4eb1eb7c`）已实现P1：沿现
`/ws/control` 的signed profile_bind验证本连接owner/epoch/active lease；HUMAN读写
不再只凭全局ready或shared secret，保持原local subject、primary-ID fence及exact effect授权。
没有新增账户、socket、Rust/TS scope或每次读签名。

P2 runtime本树已通过真实SQLite/Harness + deterministic Provider测试：无scope实际完成、
重开/终态事务故障恢复、真实消息投影、unscoped队首后scoped FIFO，以及None→生产
context_route.resume_existing→工具发现/激活→生产write_file→semantic closure/terminal。
项目effect从真实route/binding验证exact root，保留scoped冻结检查；binding head更新负例不落盘。
统一terminal_identity先验Host receipt/binding/observation或ExecutionEvidence链，再比raw
SDK event ID/hash/state；旧式scoped fixture不删除原始证据。helper用于runtime/API共享解释。
轻量state_changed接现control broadcast空payload，独立合并限时500ms；control请求事务前
重验原verified connection/epoch/lease，保留原撤销barrier。
2026-09-05聚焦181 passed（含动态正负、SDK身份替换拒绝、crash/reopen及通知），无App/真实Provider。
main已惰性注入API三reader/resolver，**依赖Dirac API提交及terminal helper接入后继进行组合验证**。
**完整历史Memory/evidence/entity来源suppression留下一提交，尚未闭合**；当前候选不得据此
合main或宣称S6 Task1/2完成。create_new Manual路径也未有新UI验收，不走旧目录卡/external wait。

CREATE_NEW候选62f44631曾发现generation origin P1，后继冻结原Host/SDK Run、owner/gen，
首次选择与写锁内commit前重复核验；真实reclaim/终态后旧context回归及相关套件85 passed。
origin c04912f9已获Dirac独立限定ACCEPT（9 passed）。首tool启动竞态后继用本次启动Event
等待真实Host RUNNING持久化，再按原owner/gen/state授权；不放宽CLAIMED，5秒有界失败。
无Provider等待的真实首tool正例/期间reclaim拒绝及相关套件87 passed；待独立复核及新项目native验收。

CREATE_NEW后继已完成生产backend修复：配置CanonicalWorkspaceRoot取canonical_path后使用稳定task子目录；
service先持久化真实binding proposal；active None Run以实际Run/context+owned目标scope取得原AUTO authority，
不走无Run bootstrap，不改变admission scope。Manual返回可消费真challenge，旧scoped不可跨scope。
相关回归66 passed（含5条新增、真实AUTO落盘/terminal/重开、lease丢失及伪造evidence负例）。
Manual UI/失败结果投影仍未接通，完整Memory-forget history仍未闭合。详见
[CREATE_NEW交付](../plans/2026-09-05-s6-primary-preparation/CREATE-NEW-BINDING.md)。

本记录仅为隔离分支状态，未合main；不表示S6 Task1/2或program验收完成。

实现/命令/原始证据hash与交叉点见
[实施交接](../plans/2026-09-05-s6-primary-preparation/IMPLEMENTATION.md)。

## 2026-09-05 Harness 0.7.2 接入与当前验证

Host `8d57441517836aaaa30ac16a33576f4d68a9d1ad` 已安装 Harness 0.7.2，source
`2b8428465cbd41032ba024a0b7199183161f5ecd`，wheel SHA-256
`53bded3fea87168e5d2ad9e49fea5f99e1c1edb1d6077b2a52dd62716692f9ed`；Memory 保持
0.6.3 / `6b20ae5bff6c3ecfe1108ccaff9bb41c4dc6a3b98bb754dac2c418673ab77c78`。
版本、manifest 和安装身份核验通过；SDK 独立复核 151 个包文件与源码/安装字节一致，
110 个已导入模块均来自 Host venv，安装版定向回归 17 passed。

A14 两个独立真实 gpt-5.5 / queue.enqueue root 均完成 README 1.1.3→1.2.0、TaskScope
语义收口、终态 outbox 与 Memory accepted plan，每个 root 物化 episode+semantic 两个 head。
root 为 `2fa7d7b1-3430-5052-bc10-5cfb77beb32a`、`870babc5-3842-56d7-bae5-05d1477119c0`；
前台调用分别 13/12 次，analysis 各 1 次，无重发。旧 0.7.1 失败证据保留。
独立 AI 质量复核确认原句/spans/实际效果/绑定一致；发现 episode 误用 analysis 时间的 P2 已改用首次 Host committed_at，26 条聚焦回归通过、主执行者复审接受，真实生产复验待执行；
另一个补读 before 来源 P2 已用衍生纠正包闭合并独立接受，原封口 834 文件未改。
不能把两条机械通过当成 S5b 完成，也不将物化计作 typed recall 命中（A15 仍 NOT_MEASURED）。

当前 native UI 的隔离源 backend / gpt-5.5 root `a48a396c440054e299af4094b273f2db`
已收到真实非空回复；只覆盖 chat 冷启动，S6 唯一主对话/queue UI 仍未交付。
Host 全量在上述 HEAD 得到 6535 passed / 6 failed / 47 skipped / 6 deselected；
一个新增失败为 exact candidate 测试的旧 hash 字面量，更新当前 hash 并保留旧 hash 负例后整文件 20 passed。
其余五个失败属于历史七节点子集，其中 downgrade 原因变化仍在独立核验，不能直接豁免；
另外两历史节点已通过。6 deselected 全来自默认 marker，命令的显式 deselect 未匹配；没有测试挂起。
本次还完成 S2 聚焦 49 passed、S3/S4 聚焦 96 passed、S7 聚焦 16 passed；各日志明确测试层级。
完整回归退出码与原失败保留，尚未得到 machine finalize PASS。

本机证据根 `.local-test-evidence/2026-09-05/human-memory-resume/`：
`tools/a14-20260905T093324-p0e5cu3m/`、`independent-review/a14-quality-093324/`、
`tools/derived-correction-recollection-before-20260905T095723/`、
`independent-review/a14-q2-correction/`、`reg-full-host/REPORT.md`、
`host-final-candidate-pin-retest.log`、`tauri-app-harness072-resolved.log`。
原始文件及 SHA 索引均 ignored；未 push/tag/发布。S3 另在隔离分支补公共执行桥，S5c/S6 未交付。

## 2026-09-05 早期接续：验收重建与恢复竞态

SDK 源码修复已提交 `2b8428465cbd41032ba024a0b7199183161f5ecd`（candidate 0.7.2）；主执行者报告真实 runtime route→WAITING→授权重启新增 2 用例先红后绿、独立 review 4 passed。Host 正在 revendor/安装，尚未完成新候选身份核验及 A14/S8；本地 S1 FAIL/S8 FLAKY 保留。

- **Memory 0.6.3 已接入**：source `2f3d73814fe6a884e0458d87567b918c5863033e`，
  wheel SHA-256 `6b20ae5bff6c3ecfe1108ccaff9bb41c4dc6a3b98bb754dac2c418673ab77c78`，
  两次构建字节一致，安装来源/版本/hash 校验通过。IR-02/IR-03 独立复审接受；
  Host 关键集成 51 passed，安装版 SDK 恢复/API 16 passed。两个 finding 已闭合；
  S5b 完整生产入口与机器门仍未交付。
- **新 wheel 原生 UI 冷启动成功一轮**：Host `26b50ee8` / Memory 0.6.3 / gpt-5.5，
  root `c2af5326a8d05023868f7994f1a4e0be`、session `56908147-fbf7-447b-880b-8a798dfe7660`；
  schema v46、身份就绪、非空真实回复。早期 kimi-k3 失败保留，r5 S8 **FLAKY**，不以该 UI 证据替代 A14。
  S6 仍须解决空态模型设置丢失并接入唯一主对话。

- Host 与三个 SDK 已同步远端；以 Memory SDK 原始 Human Memory plan 和
  `HANDOFF-2026-09-05.md` 为基准继续。旧机器 r3/r4 原始机器账本未同步到本机，
  当前 r5-local 从 NOT_RUN 重建，不把历史文字 PASS 导入为本次验收。
- **IR-01 已修复并独立复审**：post-turn 恢复使用限定状态的原子更新，阻止
  reserved 观察过期后覆盖已 handed_off 的 attempt 并重复发送。决定性回归旧版红、
  新版绿，相关 35 条自动化通过；详见 `AGENT_HARNESS.md`。
- **S5b 当前阻塞为 SDK route P1**：A14 root `142bdb3b-9026-5264-b244-69e94bf0e388`
  已真实写 README，context_route/task_scope_update exact approval 均接受，SDK 恢复却将合法 current route
  与 immutable initial 全等比较而失败。terminal FAILED、closure pending、accepted/head=0，第二 root 未跑。
  IR-02/03 已修复；此处不回退固定 plan/evidence，也不以模型重跑规避 SDK 缺陷。
- 用户批准 A17 限定解冻及另线 S3 契约修订；主执行者修 SDK，其余 SDK 功能/原 AC/权限/预算/oracle 继续冻结。
  批准 hash 与完整边界见 Memory S5b `SDK-ROUTE-UNFREEZE-PROPOSAL.md`。r5 metadata 追加 6 项 S1 失败证据
  与 1 项批准记录，保留 S1 FAIL、S8 FLAKY、a2-001 OPEN；无 provider/finalize/豁免。
  原始材料在 `.local-test-evidence/2026-09-05/human-memory-resume/verification/r5-local/artifacts/`。
- **S3 状态校正**：typed recall、graph 和 audit 已有实现，最小回归 62 条通过；
  完整消费 runner 仍缺执行桥，冻结的旧候选身份/content hash 与当前 API 不一致，
  401 格 oracle 自检不构成产品验收。240 条语料尚未独立人工冻结。
- S5c、S6 与整个 Human Memory program 仍未交付。原始证据仅存本机 ignored 目录。

## 2026-09-04 Human Memory S5b Task 7：前台任务执行链生产入口跑通与路径契约收口

- **前台执行链首次在生产入口跑通**：控制通道 `queue.enqueue` → `_drive_claimed` → SDK ReActLoop →
  终态提交 → 认知记忆物化。该链此前从未在生产上跑通——`_drive_claimed` 在 pytest 里零覆盖，
  测试基座手工按序推进状态机，把每处生产装配缺口都恰好补上。共修 9 处**既有缺陷**，
  逐处契约见 [`AGENT_HARNESS.md` 前台任务执行链](./AGENT_HARNESS.md)。
- **P0-12（路径契约）**：`file_read`/`file_write` 的 `_resolve_within_workspace` 不展开 `~`，
  `Path("~/x")` 不是绝对路径 → 被拼成 `<root>/~/x` → 通过后代校验却指向不存在的文件。
  本项目提示词习惯用 `~/SimpleHarnessWorkSpace/...` 表述，这条路径几乎必踩。
  **性质**：决定性验收项的通过与否取决于模型当次随机选了哪种路径写法，同一条指令时绿时红，
  极易被误判成模型抖动。修法：`~` 在后代校验**之前**展开，`relative_to(root)` 仍是唯一边界权威。
- **P0-11（os_tools 路径归一化）**：`read_file`/`list_directory` 相对路径按进程 cwd 解析；
  `write_file`/`edit_file` 的 `~` 在越界校验**之后**才展开——后者是越界通道（`~/x` 被判成
  scope 内而实际写向 `$HOME/x`）。四个工具统一走 `os_tools/_scope_paths.normalize_model_path`，
  次序钉死为"先展开、后校验"，变异验证：调换次序用例立刻转红。
- **终态语义澄清**：`foreground_turn_heads.current_state = SETTLED` 只表示回合终止，不表示成功；
  业务结果由 `foreground_terminal_receipts.terminal_state` 与 `task_scope_closure_receipts` 承载。
  上游 provider 502 / 60s timeout 得到 `SETTLED` + `terminal_state=FAILED` + 零收口回执，是正确行为。
- **P0-13（终止护栏漏配）**：前台 driver 的 `max_consecutive_same_tool` 漏设 → 取 SDK 默认值 3，
  而同处已放到 25 轮 / 50 次工具调用。连读 4 个文件即掐断 Run。显式设为 10。
- **P0-14（契约与强制不一致）**：三跳披露的三个工具处理器强制校验必填字段，schema 却不声明
  `required`。依赖 schema 的模型必然反复踩空。三处如实声明；省略原本的理由（缺项打死 Run）
  已被本增量的 `ProductToolsAdapter.validate` 修掉，前提消失。
- **这两处由更换 provider 暴露**：`gpt-5.6-luna` 碰巧不触发，换模型后立刻致命。
  工具契约的正确性不应依赖某个模型的习惯。
- **P0-15（唤醒丢失，决定性路径）**：驱动「无进展即退出」，而唤醒到达时若驱动仍在运行，
  `after_enqueue` 只看 `done()` 判假即什么都不做——唤醒被永久丢弃，回合永停 `CLAIMED`、
  终态受理为空、Memory 摄入永不发生。故障轮业务侧全对（文件真被改、收口 `mutate`、
  effect 全结算、SDK Run `completed`），唯独宿主侧没提交终态。P0-9 只接上了唤醒、
  未处理唤醒被吞。修法：驱动在跑时留痕 + 退出前在锁内复查并置空引用消除残余窗口。
- **默认全开**：以上修复无开关，随交付即生效。
- **遗留义务（S6）**：该链**没有任何桌面 UI 入口**，前端零调用 `queue.enqueue`，
  在 S6 建出入口之前用户可见价值为零。

## 2026-09-03 Human Memory S5b Task 6：effect gate 加固、Auto `explicit_only`、composition 真构造、v46 cutover 与遗留义务

- **P0（真实桌面预演）**：`_build_product_sdk_runtime_stack` → `_activate_memory_analysis_lane` 读模块全局
  `_sdk_provider_binding_resolver`，而该全局在 stack 构建之后才赋值 → 每次真实启动必抛
  `sdk_context_authority_composition_missing:sdk_provider_binding_resolver`，再被吞成 `product_sdk_runtime_skipped`。
  修法：`_resolve_sdk_provider_binding_resolver()`（service_context 槽优先）；`_activate_product_sdk_runtime` 构建异常改为
  raise（AC-6① startup stable fail，不再 warning+skip）；决定性用例 `test_product_sdk_runtime_stack_builds_on_real_startup_order`。
- 基线 main @ a42835d0（含 Task 0–4，里程碑 PASS）。EffectGate（Task 1 审查 F-2/F-3/F-7/F-8/F-10）：四次读同一 SQLite 读快照 +
  预留事务（BEGIN IMMEDIATE）内 `reservation_check` 再核 head/scope；durable sticky memo `effect_gate_rejections`
  （`effect_gate_route_receipt_rejected`，直到同 Run 新 `context_route` 收据）；head 缺行 → `workspace_binding_effect_authority_missing`；
  `context.effect_id` 缺失 → identity mismatch；步骤 0 只跳非 PREPARED 记录；inode 漂移 / symlink / 跨 Run / 直通 / memo 隔离用例；
  构造期缺件 TypeError。terminal observer：run_terminal 走预留协议（同事务 reserve+ingest，reservation_id 由 `terminal:{run_id}` 派生，
  source_event_id 保持 SDK terminal event id）、memo 在 durable 后释放且有界（4096 FIFO）、`mark_terminal` 释放 sink、
  `error_code` 白名单 `^[a-z][a-z0-9_]{2,63}$`（否则 `driver_failed`）。
- **Auto `explicit_only`（AC-3⑤ / A5）**：`_FrozenCapabilitySpec.effect_class`（inventory → manifest → unknown）+ manifest dangerous
  → `confirm_only`；`SdkPreparedAuthorizationPolicy.decide` 传 `plan_prepared_call(explicit_only=…, confirmed=False)`，
  Auto 下 confirm-only（run_shell/process_start/move_file/file_organize/ppt_create + DESTRUCTIVE 等）→ REQUIRE_USER
  `product_policy_user_confirmation`，候选 grant 来源 `user`（绝不 `policy:auto`）；write_file（reversible_local）沿用既有 auto；
  Auto folder-append 零 manual challenge；run_start_record 携带 effect_class（WAITING 重启不按新 manifest 重分类）。
- **AC-3⑥ 单根 per-canary**：`ProductRunContextAuthority(binding_store=…)` 对 ≥2 root scope 的 ROUTED_TASK 轮隐藏 PROJECT_EFFECT
  工具；`test_s5b_s4_single_root_per_canary_and_multi_root_hidden`（逐 root hash、强制调用 → durable FAILED +
  `sdk_task_execution_root_authority_ambiguous`、两根 canary hash 不变、append 后 superseded sticky）。
- **composition（AC-6①）**：`SDK_COMPOSITION_SLOTS` + `_assert_sdk_composition_slots(context)` 真构造逐槽缺件断言（不再 grep 源码）；
  `EffectGate` 逐件缺失 TypeError。
- **v46 cutover（AC-6②）**：037（v45）回补进 `_S4_HUMAN_MIGRATIONS`（新库 marker/链行/恢复注册+fence 触发器齐全；S5a 链外应用的旧库由
  `repair_context_route_registration` 在启动入口幂等回补，`_validate_s4_migration_chain` 现要求 037）；迁移前置
  `_assert_effect_closure_cutover_preconditions`（WAITING SDK Run → `human_memory_migration_blocked_sdk_run_waiting`、非终态
  foreground Run → `..._foreground_run_active`，BEGIN 之前判定、库字节不变）；`tests/memory/test_s5b_v46_cutover.py` 为 Task 8
  `record-run --exec` 的 cutover 断言集合（前向 + evidence hash 守恒 + 旧 runtime 拒绝 + rollback drill + 旧 checkpoint
  `catalog_state_fingerprint_stale` 稳定隔离）。
- **Task 2/3/4 审查 P2**：not_sent（真实 SDK provider 的 redactor 抹掉异常类型 → 按 httpx 连接阶段消息判定；**SDK 0.8 义务**：
  公开错误保留传输失败阶段/类型）、handoff rowcount≠1 → lease_lost、`asyncio.CancelledError` settle 后传播、多字节 key 按字节 +
  handler 内协议错误稳定码化、`force_close_pending` 只清 pending 水位、复合 shell 命令降级 host.file、v46 守卫逐列
  （含 `result_envelope_json` 只允许 response→response+envelope 升级一次）、`read_reserved_fact` 真账本用例、CANCELLED/STOPPED
  终态门、两连接并发 reserve；Task 4：**F-1** Provider 响应先单事务 settle 再派生（派生失败可重试、0 调用重派生、>16KB 引用确定性拒绝）、
  **F-4** outbox links 不一致行 claim 事务内有界 dead_letter、**F-5** binding 不可得终态照常提交 + outbox `dead_letter(run_binding_unavailable)`。
- **遗留义务**：S1 真实无召回车道追加非空回答断言 + transcript dump（只加不放宽，本 Task 未运行 `-m real_provider`）；
  changed-surface lint（`context_route.py` SIM102、real-provider F811）清零；`write_file` handler 对模型 content 不做拼接
  （persona 进 README 为纯模型行为，`test_write_file_content_verbatim` 锁定）。
- 验证：`tests/sdk_adapters tests/execution tests/task_scope tests/memory tests/faults`（deselect
  `test_close_during_start_prevents_ready_publication`）在 worktree 内 **除 12 条 `test_composition` 环境项**（SDK wheel origin 指向主
  checkout，与代码无关，基线同样红）外全绿；changed-surface ruff 相对基线零新增（两处减少）。
- **known-debt**：`backend/uv.lock` 仍指 memory 0.5.2（pin 真相在 `sdk_candidate.py`，未 `uv lock`）；真实启动顺序用例覆盖
  `_activate_memory_analysis_lane` 与 `_activate_product_sdk_runtime` 的 raise，未跑完整 lifespan；S4 P2"oracle pin 口径统一进 spec"
  涉及只读 spec，未改。

## 2026-09-03 Human Memory S5b Task 4：终态同事务 Memory outbox、analysis_proposal、Host analysis executor/delivery authority、v7 接线（价值验证里程碑）

- 基线 main @ e033a781（含 Task 0–3 与 Memory 0.6.1 pin）：新 `memory/analysis_proposal.py`（§9 proposal → 确定性 EvidenceSpanRef
  派生 → MemoryMutationPlan）、`memory/evidence_authority.py`（Host state.db 只读解析器）、`memory/analysis_lineage.py`
  （model_config_hash / analysis_lineage_json）、`memory/analysis_executor.py`（executor = delivery authority；三键查重、
  evidence_set_key 零调用复用、blocked → `memory.analysis.blocked` 审计、lease_lost 结果附着）、`memory/memory_ingestion_outbox.py`
  （worker + 单一 lane）；`foreground_queue.record_sdk_terminal` 同事务 outbox + `reserve_analysis_attempt`；`foreground_runtime`
  传 durable binding；`human_memory_v7` 接线（filter policies / evidence & delivery authority / classification policy / 首次
  `register_principal_owner`、删 fail-open）；`post_turn_invoker` durable 结果与复用；v46（未发布）`post_turn_invocation_attempts.
  result_envelope_json`；`main.py` 装配 + 缺槽断言（`sdk_evidence_authority` / `sdk_memory_analysis_executor` /
  `sdk_memory_ingestion_outbox`）；`context.py` 白名单补 `sdk_effect_gate`（Task 1 起 register 但白名单缺失）等槽。
  Task 3 审查 F-1（幂等重放返回首条 receipt，watermark 取 decision revision 水位）与 F-2（生产 `closure_reader` 注入，
  `sdk_closure_instruction_reader` 槽）各自单独 commit。
- 验证：oracle 先行（实装前全红）；矩阵 Task 4 三用例、fault lane `memory-mutation-plan` 11 seam、
  `tests/memory/test_memory_ingestion_outbox.py`、no_recall_gate 属主注册用例、F-1/F-2 决定性用例、里程碑确定性车道（模型调 / 漏调）
  全绿；**真实车道** `tests/sdk_adapters/test_s5b_milestone_real_provider.py -m real_provider` 1 passed（125s；Provider 调用 =
  主 Run 4 + closure 0 + analysis 1；episode 记忆物化，typed_recall 读到 "1.2.0"；transcript `.local-test-evidence/s5b-real-provider/`）；
  changed-surface ruff 相对基线零新增。
- 边界：Memory 0.6.1 多 evidence batch 中只引用非首条 evidence 的 operation → `decision_evidence_refs_ordinal_invalid`（Memory 侧，
  生产一 turn 一 batch 不触发，已在 Task 4 报告登记）；真实车道第二次运行模型把自身 persona 文本追加进 README（版本号正确）；
  `tests/sdk_adapters/test_composition.py` 中 7 条既有红 + `test_close_during_start_prevents_ready_publication` 挂起为基线既有；
  Prospective/即时操作仍在 S5c；`sdk_memory_ingestion_outbox` lane 的 worker config 回落三元组取 provider chain 首项。

## 2026-09-02 Human Memory S5b Task 3：`task_scope_update` 常暴露、三水位终态门与 lease-fenced 兜底状态机

- 基线 main @ 959725f6（含 Task 0/1/2 与 Memory 0.6.1 pin）：新 `sdk_adapters/task_scope_mutation.py`（strict schema、§7 拒绝码
  与状态迁移表、apply 与 closure receipt 同事务、pre-admission audit）、`sdk_adapters/post_turn_invoker.py`（`RunBoundInvoker`
  五态 attempt 账本 + unknown 三分类 + lease 同事务预留/返回后复验）、`execution/semantic_closure.py` 扩展（receipt/coverage/
  snapshot 收口指令/`ClosureFallback`/`force_close_pending`）；`foreground_queue.record_sdk_terminal` 第三水位
  `foreground_terminal_closure_pending`（四终态一律生效）+ `EffectBoundary.CLOSURE` + `reserve_post_turn_attempt`；
  `foreground_runtime` 在 SDK terminal 之后、Host 终态之前调兜底；`context_authority` 注入 protected 收口指令；STATUS 投影
  `semantic_closure_pending`；`human_memory_service` 在 task.complete/resume.update/checkpoint 前强制收口；`main.py` host-composed
  注册 `task_scope_update` 并以 durable `SdkRunBindingV1` 经 provider binding resolver 重建兜底 adapter。
  Task 2 审查 F-1（凭据形状路径脱敏、settle 后永不抛）/ F-2（预留 `tool_name`，abandoned PROJECT_EFFECT 判 material）已修。
- 验证：oracle 先行（实装前全红）；矩阵 Task 3 四用例 + handler 拒绝码矩阵 + invoker 五态 + fault lane `foreground-fifo-closure`
  新五 seam kill→replay + F-1/F-2 真实 SDK executor 回归：`tests/sdk_adapters/{test_s5b_acceptance_matrix,test_task_scope_update_tool,
  test_post_turn_invoker,test_effect_gate_replay}.py tests/faults/test_foreground_fifo_closure.py` 33 passed / 8 xfailed（其余 Task 的 strict xfail）；
  兜底调用全部确定性 adapter 替身，未跑 `-m real_provider`；changed-surface ruff 相对基线零新增。
- 边界：`task_scope_update` 不进 pre-cutover manifest（host-composed，同 `context_route`）；`resume` 强制收口取 `resume.update`
  （route resume_existing 不强制，保留下一 Run 合并 pending 的通道）；`sent_confirmed` 只能确认仍 `handed_off` 的行；
  Task 2 审查其余 P2 与 F-3~F-8 进 Task 6 backlog；Memory outbox/analysis（Task 4）尚未接入 `record_sdk_terminal`。

## 2026-09-02 Human Memory S5b Task 2：客观事件同事务直写、Harness 证据预留/排空与脏标记

- 基线 main @ f8097429（含 Task 0/1）：v46 迁移 `038_effect_closure_memory_v46.sql`（§5 全部 7 张表，append-only +
  单调守卫，前向迁移 + 旧 runtime 稳定拒绝）；`ExecutionEvidenceIngress` 扩展为预留/排空单一 owner（reserve →
  commit_fact 同事务：host.file|host.test + evidence 行 + harness.tool_invocation；ledger 事实写事务内直写；
  `next_sequence=MAX(reservations∪receipts)+1`；terminal observer 排空后才放行）；`OBJECTIVE_EVENT_MAP` +
  test-runner 白名单规则；`ProductEffectExecutor`/`ProductProviderInvocationCoordinator`/`ContextRouteLedgerStore`/
  `SqliteSdkTerminalObserver`/`ProductSdkRuntimeStack.read_reserved_fact` 接线，`main.py` 单例注入；
  `semantic_closure.dirty_state`。Task 1 审查 F-1（P1）已修：EffectGate 步骤 0 exact replay 不重验。
- 验证：oracle 先行（实装前全红）；矩阵 Task 2 三用例（write_file 同事务、dirty_state、probe A9 迟到 seq 不丢行）、
  reservations/objective/replay/v46 迁移单测、fault lane `foreground-fifo-closure` 三 seam kill→replay；
  `tests/sdk_adapters tests/execution tests/task_scope tests/memory` 477 passed / 9 xfailed（5 项 SDK candidate origin
  mismatch 与 test_composition 为本机既有环境红，基线同样）；changed-surface ruff 相对基线零新增。
- 边界：终态门要求 receipt（Task 3 strict xfail）；037（v45）不在 S4 迁移链/恢复注册内属 S5a 遗留；无 foreground 绑定
  的 Run 不产 Harness 证据；SDK effect 账本与 state.db 分库，"同事务"指 Host 侧三样同 commit + 预留 seq 幂等关联。

## 2026-09-02 Human Memory S5b Task 1：workspace EffectGate 最小闭环

- 分支 `s5b/task-1-effect-gate`（基线 main @ aec5bacf，含 Task 0 骨架）：14 个写/执行类内置工具冻结为
  PROJECT_EFFECT（route/TaskScope REQUIRED）；`BindingRootResolver` 从 route receipt 的 exact binding-set
  receipt 解析恰一 root 接入 `ProductTaskExecutionAuthority`；新 `EffectGate` 在 `ProductEffectExecutor`
  物理 dispatch 前逐次重验（envelope 回声 → 冻结 scope/写根 → S4 `verify_task_execution_envelope` 对
  v45 durable route receipt → strict head revision → scope active），拒绝即 `ToolResult.rejected` 零写入；
  standalone/零多 root/hidden 三条整 Run 故障稳定码经 `RunFaultMemo` 写进 `run_terminal` 证据
  `public_payload.error_code`；standalone turn 不再向模型暴露写工具。
- 验证：S5b 黑盒矩阵 Task 1 四用例（真 ReActLoop + 真三 authority + v45 ledger + S4 store + 生产
  executor 前置门）与 gate/resolver/snapshot/terminal 单测全绿；`tests/sdk_adapters tests/execution
  tests/task_scope` 全绿（`test_composition.py` 中 7 项 production runtime factory 用例为本机既有环境红
  `SDK candidate installed origin mismatch` 且其后用例挂起，基线同样；本次以源码形状用例覆盖接线）；
  changed-surface ruff 相对 main 零新增。
- 边界：`execution_effects` 行 + `host.file` 同事务留 Task 2（strict xfail 占位）；sticky memo /
  confirm-only / Auto 断言 / inode 漂移矩阵留 Task 6；design-freeze §4 第 3 步「kind ≠ project_bound」
  按 foreground 冻结事实（单 root = legacy + exact root）实现为「无 exact 冻结写根」，需回写冻结文档措辞。

## 2026-09-02 Human Memory S5a：五路 context_route、per-turn Context authority 与同 Run continuation

- 在 `feat/human-memory-s5a-context-route` 分支（基线 `fix/human-memory-runtime-p1-closure` @ c9f73349）交付
  S5a 增量：SDK 0.7 三 authority 首次生产接线（`ProductRunContextAuthority` per-turn snapshot 三 hash、
  `ProductRuntimeDecisionSink` durable no-recall、`ProductTaskExecutionAuthority` exact effect 回声/
  PROJECT_EFFECT 真空 fail-closed），v45 `037_context_route_ledger` durable 账本（route/no-recall decision、
  snapshot receipt、tool 血缘、occurrence presented 全列表 S5a 零写入），host-composed `context_route`
  （CONTEXT_CONTROL/direct kernel）+ `task_scope_search` 五路裁决工具，因果组 planner + 冻结分区预算
  （metric-formulas 逐字节 pin，超载 fail-closed 禁 underestimate），mandatory no_recall inbox reconcile
  （matched∧live∧资格∧非 suppressed∧∉presented；真 apply_prospective_signal 注入负测试），
  memory_standalone 双 recall lane（execute_typed_recall + recall_short_horizon，fragments 带
  bytes/tokens/lane），Memory SDK 0.6.0 exact pin（wheel 62a3f63c，双 clean build 字节一致）。
- 真实 provider（gpt-5.6-luna @ 用户 relay）四路正向实测：resume 同 Run continuation（exact ResumePackage
  进最终 payload）、单 invocation no-recall、direct commit、continue_active 承 durable cursor；usage ≤
  冻结 effective budget。deterministic 矩阵：六步五路序列、A/B canary 零混入、20+turn/1MiB/kill-replay
  三 hash、载荷变异 fail-closed、v44→v45 cutover 演练四件套、fresh 冷启动组合冒烟。
- 真实桌面 UI 验收（2026-09-02，用户批准 AI 驱动等价）：在真实 Tauri app + 真实 provider
  （gpt-5.6-luna）上完成 21 轮长会话，20 个 Run 全部成功、零 provider 拒绝；durable route 覆盖
  direct_standalone(no_recall)×19、resume_existing×1、continue_active×1；34 条 per-turn snapshot
  receipt 三 hash 全等、单 Run 最大 revision 7；occurrence presented 恒零行。恢复旧任务的终答精确
  复述 ResumePackage 独有事实，第 21 轮仍能引用首轮事实（裁剪后关键事实存活）。
- **UI 实测抓到并修复两个自动化测试测不出的集成缺陷**：
  - `S5A-UI-F1`（`deskpet/memory/human_memory_v7.py`）：全新安装的 v7 store 未注册本地属主，
    Run 起步的 occurrence reconcile 以 `short_horizon_principal_rejected` fail-closed，**首条 chat
    必死**。修复：仅对「首页读且零累积」的未注册态返回恒空 reconcile（该状态下收件箱受 principals
    外键强制不可能有条目），按 reason code 收窄，其余 ownership 冲突继续 fail-closed。
  - `S5A-UI-F2`（`deskpet/sdk_adapters/provider.py`）：SDK 0.7.1 冻结契约禁止 provider assistant
    消息把私有 metadata 写进 durable Context，Host 原先靠 metadata 跨轮携带 `tool_calls` 的机制在
    真实 continuation 上必然失效，第二轮请求被 OpenAI 兼容端点以 HTTP 400 拒绝——**每个用到工具的
    chat 第二轮必挂**（S5a 让 `context_route` 成为主路径后必现）。修复：provider adapter 在组装请求
    时从 durable 消息序列自身补齐 `assistant.tool_calls`（tool 结果本身带 call_id 与工具名），
    入参同进程保真、跨进程退化为空对象以保持线格式合法。S5b 上游义务：SDK 侧把 `tool_calls` 作为
    一等公共 transcript 字段回挂后移除该退化。
- 向量模型切换（用户指令）：`tencent/WeMM-Embedding-2B`（2048 维 L2，本地快照 + trust_remote_code 限本地）
  取代 BGE-M3。
- 已知边界：本机 7 项环境红（S4 期即在案）与 process_list flaky 维持基线；PROJECT_EFFECT root 签发
  接线列 S5b Task 5 前置义务；WeMM 快照发布前需上传 COS 模型桶（provisioner 只认桶内容）。

## 2026-09-01（补）S4 closure review 修复：STOPPED 终态白名单、TOOL fence 状态集、supersession 竞态

- 8 角度独立 code review（3 正确性 + 3 清理 + 高度 + 规约，逐条验证）发现 9 项 CONFIRMED；按建议
  修复其中 3 项阻塞级正确性问题：
  1. **生产 mark_terminal 白名单缺 "stopped"**：`SdkRunBindingRegistry` 与
     `SdkRunToolAuthorityRegistry` 的终态白名单补入 `stopped`——此前用户 stop 会在 terminal 落账后
     抛 ValueError 杀死 driver 并泄漏 tool scope pin/provider binding（测试全绿是因为只有 fake 学过
     新终态）。新增 registry 级测试。
  2. **TOOL fence 状态集**：`_EFFECT_BOUNDARY_ALLOWED_STATES[TOOL]` 由 {RUNNING} 扩为全部非终态
     活动态——fence 的目标是 stale worker 与终态，SDK 0.7 无法在 tool 中途停机，原实现会把
     pause/stop 过渡期的工具调用经 SDK kernel 兜底打成 FAILED run。新增真实 store 的
     控制过渡期 admission 测试（stale generation 仍被拦）。
  3. **supersession 竞态**：`_deliver_controls` 逐信号处理 `foreground_signal_superseded`（ack 竞态）
     与 `foreground_state_transition_invalid`（pause outcome 竞态）为 skip-and-continue；控制泵退出
     集收窄为 lease 丢失/终态三码——此前 pause→stop 升级竞态会永久杀死泵/driver，live STOP 被静默
     丢弃、run 跑到自然完成。新增两个竞态测试 + cancel 即时送达正向测试（顺带闭合 P2
     `audit-hm-cancel-delivery-positive-untested`）。
- 其余 6 项 CONFIRMED findings（终态 TOCTOU、pause-ack 崩溃窗口、error-after-commit、control-before-
  start wedge〔既有等价缺口〕、身份四元组收敛、热路径 admission 成本）登记为下一增量 P2/改进项。
- 执行套件 66 passed（+5 新测试）；本节完成后已按 plan-test 重走全量复测与机器门（见下节 receipt 更新）。

## 2026-09-01 Human Memory S4 Task 5–8 Host Runtime Execution Closure（P1 整改闭合）

- 在 `fix/human-memory-runtime-p1-closure` 分支（基线 main `04a5a649`）完成 S4 host-closure 增量的
  两个 open P1 整改并重新全量验证；候选提交 `24f86694`（P1 修复 + 竞态测试）与 `56d99a21`
  （archive oracle 的 scheduler=held 测试适配）。
- `audit-hm-runtime-generation-fence` resolved：`ForegroundQueueStore.authorize_effect` 新增
  `EffectBoundary`（SDK_START/SDK_CONTROL/TOOL）分边界最终 current-generation admission；四个 frozen
  authority dataclass 绑定并逐一断言 exact `(host_run_id, sdk_run_id, owner_id, generation)`；
  `ForegroundEffectAdmissionGate` 在 `ProductEffectExecutor.execute` 的物理 dispatch 紧前接入同一
  durable admission，main.py 以惰性单例 gate 同时接 executor 与 foreground runtime；
  `ClaimedExecution` admission receipt 字段改必填并删除 test-only fallback。
- `audit-hm-control-delivery` resolved：`control_current_run` durable commit 后经
  `after_control` 即时唤醒 active Runtime；`_pump_controls` 与 terminal 观察并发送达（≤1s poll 兜底）；
  pause 送达 ACK 后 `record_pause_outcome` 推进 PAUSED；STOP/CANCEL 保持独立信号身份
  （`sdk-stop:`/`sdk-cancel:`）与独立 Host 终态（`resolve_host_terminal`；completed/failed 恒随 SDK
  证据）。audit sink 固定同步（删 `_maybe_await`）；`s4_value_adapter` 合并重复 v44 alias 映射。
- 新增四类竞态测试断言 stale worker 外部副作用为 0：bind→start、signal-read→send、
  tool-admission→dispatch 的 reclaim race，以及 pause/stop 在 active Runtime 的即时送达
  （`tests/execution/test_foreground_runtime.py`，12 passed）。
- 独立 code-audit round-3（auditor=opus，独立于 executor=claude-fable-5）：PASS，2 个 P1 resolved，
  6 个 P2 登记未阻塞（composition 双路径漂移、effect gate 进程内注册表、泵异常降级、PAUSED 无生产
  resume、CLAIMED 期 control 的既有 liveness 缺口、cancel 即时送达正向场景未单测）；产物
  `code-audit-round-3.json`（memory-sdk 增量目录）。
- 本机 fresh 正式验证（gate run `r2-p1-closure`，run-20260901-153005）：100k archive cold-resume
  value smoke PASS；100k execution value runner PASS；9/9 boundary fault runner PASS（generation fence
  g1→g2 断言）；22-case critical/affected API smoke PASS（含 live pause control、Manual 两阶段
  binding、legacy CRUD fence）；full-surface route smoke PASS（装配 + 14 路由 + /health 200）；聚焦
  套件 views 12 / fifo 36 / integration 25 / data-recovery 25 / authority 36 passed；Host full pytest
  `6218 passed, 50 skipped, 6 failed`——6 个失败全部既有（backend-a、capabilities×2、companion 四项
  baseline known-red + `test_health_check_timeout_values`、`test_exact_pinned_sdk_062_*` 两项本机环境
  失败，均在未修改 main worktree 复现），required 转绿项
  `test_real_product_sdk_production_composition_starts` 已绿；changed-surface ruff/mypy 相对 main
  零新增。原始证据保存于 ignored `.local-test-evidence/2026-09-01/`（gate 内 artifacts/ 留有日志与
  result JSON）。
- 边界：S5 剩余主模型 route/RecallPlan/Memory recall/五天短时域/动态 Context/semantic closure 与
  S6 UI 未实施；多 root project effect 稳定 fail closed；发布/push/tag/merge 不在本增量。

## 2026-08-30 Human Memory Program S4 Task 1–4 Host 权威归档、Task Home 与多根权限

- 2026-09-01 用户明确要求停止 `deskpet.receipt_hmac` 钥匙串请求：ToolReceipt HMAC 保留，但 key
  authority 改为应用私有 `userdata/secrets/receipt_hmac.key`，首次 exclusive create、POSIX 0600、重启复用；
  `receipt_store.py` 已删除 keyring/Keychain get/set 与 service 常量。已有 OS 钥匙串条目不读取、不迁移、
  不删除；Provider/API 登录凭据的独立 Keychain 逻辑不在本变更范围。

- 在隔离 worktree 完成 fresh-only `human-memory-v1` state schema v35 基础、追加 v36 TaskScope Archive、v37
  recoverable provisioning；既有 migration checksum 不变，每步有独立 immutable marker。durable
  bootstrap 与每 subject 唯一 writable primary conversation/init receipt。普通 Host 仍以 v34 为默认生产 epoch；
  新 primary 入口对任何既有 v34/旧 marker 在写入前拒绝，不迁移、不删除旧 Session。
- 新 Host evidence store 要求 S1 sanitization envelope+receipt 的严格结构、canonical hash 与绑定验证后，才在同一
  事务按 receipt-first 顺序 append user/assistant/tool/provider/run evidence。Provider 仅接受 public allowlist，
  `reasoning_content`/隐藏 reasoning/credential key 或 value canary 不进入新 schema；所有 raw/receipt/identity/marker 表禁止
  physical UPDATE/DELETE。
- 并发 cold init、v35 commit 前后 fault/restart、旧 v34 字节/row/marker 不变、五类 evidence、协议/Provider
  fail-closed、幂等冲突和全 raw 表 UPDATE/DELETE 阻断已覆盖；提交态相关组合 `67 passed`、SDK adapters
  `253 passed`。m–r 分片先跑 `1497 passed`，提交态复跑 `1496 passed, 26 skipped, 1 deselected, 1 failed`；
  唯一失败为已登记且与本 slice 无依赖的环境型 process-list query 基线红。真实 S1 source DTO interoperability
  probe PASS。
- v36 已建立 Canonical TaskScope Archive：Host turn/file/test 直接事件、ExecutionEvidence ingress receipt/cursor/
  contiguous watermark、terminal gate seam、LLM mutation CAS attempt/decision、canonical revision、immutable
  checkpoint、projection/search outbox。权威记录永久 append-only；head/watermark/projection cache 是派生协调状态，
  cache 删除后可由 canonical revision 重建。
- Task 2 专项 `8 passed`，Task 1–2 相关组合 `75 passed`，SDK adapters `253 passed`；真实 S1 source
  `ExecutionEvidence`/`TaskScopeMutationPlan` DTO interoperability probe PASS。
- v37 provisioning 区分 managed root、task home 与 non-authoritative explicit workspace candidate；只接受 Host
  managed policy 或 trusted user/project-picker provenance。稳定 reservation/staging marker/filesystem identity 支持
  所有边界 kill/retry；POSIX materialization 以 no-follow directory fd 锚定持久 root identity，并在 commit/reopen
  前复验 root/task-home，broken symlink 与 root rename/replace 不产生 receipt；无等价原语的平台 fail-closed。
  final receipt 前不产生权限。Task 3 专项 `18 passed`，Task 1–3 相关组合 `93 passed`。
- v38 使用 exact Harness SDK 0.7.0 candidate（source `8f1027d2…`，wheel SHA-256 `b9421ddf…`）的 strict
  `WorkspaceBindingProposal`、Manual challenge/decision、Host Auto snapshot、durable grant 与
  `WorkspaceBindingSetReceipt`。root-set receipt 由 sorted unique root identity hashes 重算 canonical digest；
  genesis 固定 empty-set parent，后续只允许 parent set 加一个 grant root。Manual nonce replay/changed-payload、
  Auto forged/expired/stale、CAS 并发、commit 前后 crash/restart、同 root 跨 scope、symlink/公共父目录/
  identity drift 和当前 Run 冻结旧 revision 均 fail-closed；provision candidate 不产生 binding authority。
  Manual challenge/decision 还必须由注入的 Host authority 命中 exact durable user evidence 与 authenticated
  interaction；Auto append 从 grant source receipt 重载 exact durable snapshot/request/proposal/grant，并在事务前和
  commit 前重验有效期、active Run、context/config revision 与 configured-root inode。S4 尚无完整生产 Run
  lifecycle seam，故该显式 port 缺失时 Auto fail-closed，留待 S5 production composition 注入真实 owner。
  project route 与 effect envelope 必须交叉绑定 exact receipt id/hash/revision/root membership。Task 4 专项
  `15 passed`；TaskScope/Task 1–4/marker/candidate 与 SDK adapter 组合 `322 passed`。
- 这是 S4 Task 1–4 的独立基础，不代表 S4 release unit 完成。S4 Task 5 六阅读视图/checkpoint verifier、
  Task 6 permission-first search/exact open、Task 7 单 foreground Run/durable FIFO、Task 8 Host
  composition/旧入口 fence/data epoch/recovery/emergency export 尚未实施；S5 Task 8 才负责主模型
  route/recall/context/tool 的最终 production composition，S6 才负责 UI 与 program 真人验收。
## 2026-08-30 空白会话安装、全局验证快照与语音临时关闭

- 空白消息页创建普通 Session 时不再把 transport 占位 ID `message-panel-main/default` 当作 handoff
  来源；未显式选择来源时使用 `source_session_id=None`。真实 UI 从空库输入 GitHub Skill 安装请求后，
  已自动创建 `Documents/SimpleHarnessProjects/Session-ea1f1120`，不再出现目录创建后回滚且界面静默。
- user-global 安装保持 `pending_invisible` 栅栏：Manager 发布的 exact version/hash 只覆盖到 verifier Run
  的 immutable catalog lease，不提前写入当前全局 Hub。Fresh Run page-in 成功、terminal/release receipt
  完整后，才原子激活三个 user binding。最终 intent=`succeeded`、attempt=`attested`，固定 commit
  `3a094db39db5…` 的 `plan-bs/plan-task/plan-test` 已更新为
  `0.0.0+git.3a094db39db5.simpleharness.pkg2`、generation 2 active。
- 多 Skill 仓库按 `skills/<skill>/...` 保留 sibling 相对资源；Run-frozen `skill_invoke(resource_path=...)`
  可读取 `../plan-test/config.md` 等声明文件且不能越出 pack。安装恢复现处理 Manager version 冲突、失败
  handoff、SDK Tool Catalog stale、完整 members receipt、attempt supersession 以及 attestation 后尚未 activation
  的崩溃窗口；失败不会再无限显示“安装中”，也不会把未激活版本冒充为全局可见。
- 第二个新建普通 Session `966f71d8…` 的 `/plan-` 真人输入显示全局候选；两个 Session 的
  `/api/commands/help` 都返回相同三项。启动日志同时确认 `authorization_mode=auto`。
- TokenSeller 新 Session `ec89714c…` 真实重放用户原句 `/plan-bs ...`：全局 Skill 接受，冻结读取
  `config.md`，Todo 写入与 `tool_search → tool_describe → tool_activate` 成功，最终只输出两个头脑风暴
  澄清问题并回到 idle；未执行 TokenSeller 审计。原始截图位于 ignored
  `.local-test-evidence/2026-08-30/global-skill-token-seller-ui/`。
- Realtime voice 当前按用户要求临时关闭：前端不渲染通话按钮，后端不构造 Realtime service，启动日志为
  `realtime_voice_disabled(reason=temporarily_disabled_by_product)`；`/ws/realtime-voice` 继续 fail closed。
  干净实例保存在 ignored `.local-test-evidence/2026-08-30/skill-install-pass.LUnJ3F/` 并保持运行供测试。

## 2026-08-30 Harness 0.6.4 冷启动 composition 回归修复

- 合并后的首次真实启动先发现本机 backend venv 仍为 Harness `0.6.2`；已按 `backend/uv.lock` 同步到 vendored
  `0.6.4` candidate，并确认 `HostControlAuthorityV1` 公共导出可用，Service/Memory 仍为 `0.3.12`/`0.5.2`。
- 同步依赖后的冷启动进一步复现 `skill_install_runtime_verifier is already bound`：Capability 初始化提前绑定
  Manager-only verifier，随后 SDK Runtime 尝试绑定正式 fresh-Run verifier 时失败。现已移除提前绑定，正式
  verifier 只在 SDK stack ready 后绑定一次；fresh-Run page-in proof、durable attestation 与 fail-closed 语义保持。
- SDK/Skill 安装聚焦回归 `23 passed`，TypeScript/Vite 与 Tauri debug `.app` 构建通过。隔离 userdata
  `latest-main-user-test-fixed.YcRHAI` 的当前 debug `.app` 已真实冷启动：SDK `0.6.4` ingress open、权限
  `auto`、Provider catalog HTTP 200、模型 `deepseek-v4-flash`，窗口显示“已连接”且保持运行供用户测试。
  原始截图保存在 ignored `.local-test-evidence/2026-08-30/latest-main-user-test-fixed.YcRHAI/`。

## 2026-08-29 Project-scoped managed Skill 安装故障链修复与 macOS 真 UI 验收

- 聊天/Settings 安装已收敛到 `ProjectSkillInstallService` 单一 application owner；SDK prepared
  authorization、Product saga、Host receipt、Manager batch publish 与 canonical verification Run 共用一个
  durable intent。嵌套 tuple JSON、nonce reissue、durable decision lookup、失败 attempt recovery 与 resolver
  composition 的真实崩溃/恢复缺口已修复。
- Capability Hub 的 `owner_key` 现进入 Store snapshot、cache partition 和 per-Run lease；Capability Center
  通过当前 Session 的可信 Project binding 查询同一目录，不再把全局 catalog 当成 Project Skill 事实源。
- `/api/skills/list`、slash help/schema 与 WebSocket dispatch 现统一读取当前 Session 的 Project Skill
  projection；InputBar 去掉永久缓存并在打开 slash 下拉时重拉。真 UI 输入 `/plan-` 已同时显示三个已安装
  指令，projectless API 对照为零项。
- macOS 隔离 debug App 已真实安装 `DennyWanye/plan-test-skill@4d8c803ba03b…`；durable intent
  `succeeded`、第 4 次 verification attempt `attested`，三个成员在 Skills UI 均显示 Project scope 与健康。
  后端最终聚焦回归 67 passed；扩大改动文件组合 203 passed / 1 个已登记的非本轮 Context budget 基线失败。
  本次 slash/安装相关后端最终组合 86 passed，前端 slash + ChatView 31 passed；
  TypeScript/Vite build 和注入隔离端口 8241 的 exact Tauri debug bundle 通过。
- 本轮关闭用户复现的授权/恢复/安装/可见性链，不把它外推为整个安装 plan 的 release：恶意 archive、跨
  Project 隔离、全部 crash boundary 与 full-surface 矩阵仍按
  [`results.md`](../plans/2026-08-27-chat-skill-install/results.md) 的剩余 gate 执行。
## 2026-08-30 Realtime 消费端接线与工作区整理复核

- 新 `/ws/realtime-voice` 使用 Service SDK `0.3.12` 的受版本约束本地协议和 provider transport；旧
  `/ws/audio`、Silero VAD、faster-whisper 与本地 TTS 继续关闭。前端仅保留一个电话式控制，应用挂载不会
  申请麦克风或建立 Realtime 连接，只有显式点击开始才创建音频资源。
- 当前工作树受影响后端回归 `376 passed`、前端全量 `82 files / 646 passed`，TypeScript `--noEmit`
  通过；Realtime 专项包含本地鉴权、origin、PCM framing、barge-in、挂断和资源释放。
- 真实 Provider 的连续多轮转写、音频播放、无终端 timeout 和正常挂断尚未在本次整理中重新验收，故
  Realtime 仍标记为候选接线而不是 release PASS；如未来承载 Agent Tool/Workflow，必须接入正式
  `ProductTurnPreparer`/RunKernel，不能让 voice transport 取得第二套 Agent authority。

## 2026-08-30 当前 SDK consumer candidate 与已有干净实例边界

- 当前消费组合为 Service SDK `0.3.12`、Harness SDK `0.6.4` candidate 和 Memory SDK `0.5.2`；
  wheel SHA 分别为 `710ae66b…`、`ecb6e85c…`、`deff2fa8…`。Service release manifest 的构建时
  Harness 成员仍是 `0.6.2`，但其包约束允许 `>=0.4,<0.7`；产品独立验证 0.6.4 candidate
  manifest 和 installed origin。0.6.4 尚未发布，该组合不标记为官方 release unit。
- Host 已从 Service `0.3.4` 更新到 `0.3.12`，同步 exact wheel、candidate manifest、依赖锁、冻结 bundle
  清单与 executable candidate identity；candidate/runtime/Realtime/Skill/Session 聚焦回归 `70 passed`，
  TypeScript + Vite + Tauri debug bundle 通过。
- `SimpleHarnessCleanSDKTest.app` 的既有证据以新建空 userdata `userdata-OSJWfG` 完整冷启动：state schema v34、
  Harness runtime `open/0.6.2`、权限 `auto`、Provider catalog HTTP 200、当前模型 `deepseek-v4-flash`；真实 UI
  显示已连接、暂无 Session、空消息流；当次测试实例已在测试结束后关闭，原始截图位于 ignored
  `.local-test-evidence/2026-08-29/manual-clean-latest-sdk/`。
  这是 0.6.2 组合的历史 UI 证据，不替代本次 rebase 后 0.6.4 组合的重新验收。

## 2026-08-29 全局 Skill、普通 Session 默认工作区与默认 Auto 权限验收

- 普通 Session 未选目录时由 Host 在 `Documents/SimpleHarnessProjects/Session-<id>` 分配独立目录；用户
  选择目录时沿用选择结果。state v34 保存不可变 binding，并以 marker/identity 约束补偿与启动恢复。
- URL Skill 安装已切换到 user-global managed authority：Settings 与 Chat 共享 typed service，固定 commit/
  digest，只有 Manager publish、fresh Run runtime verification 与 durable global activation 全部成功后才可见。
  legacy `<userdata>/skills` 和项目级副本不再是成功判据。
- Catalog Hub 对 `user:v2:*` scope 使用同一个 global owner 查询，因此旧 Session、新 Session、冷重启和
  fresh Run 都看到同一组全局 Skill/Tool metadata；SDK Run 从 exact Hub snapshot 冻结 body-free records，
  目标执行继续遵循权限与健康策略。
- slash help/list/schema/dispatch 已归并到同一 user-global snapshot；前端新 `/` 输入会强制刷新候选并把
  request/turn identity 传给 dispatch。Session owner 在创建事务时动态冻结，历史空 Session 仅能由当前 owner
  幂等认领，解决“所选文件夹 Session 能看到 Skill 但真实调用 owner fence 失败”的缺口。
- 新安装权限默认 `Auto`，legacy 显式选择保留；真实启动日志确认 `authorization_mode=auto`。
- macOS 当前源码 bundle 真人验证：两个普通 Session 分别绑定 `Session-e01e277d`、`Session-6593da1b`；
  固定 commit `3a094db39db5…` 安装的 `plan-bs/plan-task/plan-test` 在发布、冷重启和第二个 Session 中均可见。
  之后用真实 `deepseek-v4-flash` 补齐执行证明：旧 Session `4b2f9fd2…` 与新建默认 Session
  `2290a54a…` 均实际完成 `tool_search -> skill_invoke`，加载同一 `plan-test` content hash；新 Session 的
  自动目录为 `Documents/SimpleHarnessProjects/Session-2290a54a`。最终当前 `.app` 又在用户选择目录
  `/Users/denny/projects/生成视频` 新建 Session `21030143…`，发送前 owner 行已存在；`/plan-` 菜单显示
  `plan-bs/plan-task/plan-test`，真实 `/plan-test` Run `ea0488e47…` 加载全局正文并由
  `deepseek-v4-flash` 完成。专项后端 `54 passed`、前端全量 `82 files / 643 passed`、TypeScript 与 bundle
  build 均通过。
  测试原始证据位于 ignored `.local-test-evidence/2026-08-29/global-skills-default-workspace/`。
- 安装卡住问题的当前收敛：真实 Run 在 GitHub REST 403 时约 41 秒内进入 terminal 并显示失败，不再无限
  “安装中”；完整 commit `3a094db39db558dc72127938a377dccd8463c475` 改走 codeload 后成功安装三项。
  stable failure suppression、显式 retry generation、权威 Run query/cancel 与 no-ack 非假 idle 均已落入
  durable contract。统一能力中心补上 user-global default scope，当前 UI 能力数 127→130，搜索和旧 Store
  均显示三项，新普通 Session 的 slash 菜单也显示三项。聚焦后端 `175 passed`、前端 `101 passed`、
  TypeScript/Vite/Tauri debug build 与 `git diff --check` 通过；当次干净实例已完成验证并关闭，SDK 为 Harness
  `0.6.2` / Memory `0.5.2` / Service `0.3.12`，模型 `deepseek-v4-flash`，权限 `auto`。原始证据位于 ignored
  `.local-test-evidence/2026-08-29/auto-skill-install-current-build/`。

## 2026-08-27 Project-scoped Sessions macOS release 验收完成

- 冻结场景 S-PS-01～S-PS-08 的 macOS required lane 全部 PASS；Windows 按用户决定不在本轮范围，未用
  macOS 结果外推 Windows。
- 当前 debug `.app` 已真实点击验证 Project 注册/分组、同项目 Session、projectless bounded handoff、
  Inspector/复制/打开/Finder、缺失根 fail-closed、无关 identity 拒绝、同身份 relocation 与完整重启。
- 真实 `deepseek-v4-flash` 最终 Run `c2a0022…` 在 relocation 后执行 `pwd`、相对读写均只落在绑定根；
  distinct project/execution fixture 的三个当前 Run（含完整重启后）只使用 execution root，Project-only
  canary 不可见。
- 真测发现并修复两个生产缺陷：自动 Tool effect 的 TaskGrant id 冲突；process-wide 固定根的动态
  `mcp:filesystem` 错误进入 project-bound catalog。最终聚焦回归 `119 passed`，25 个 machine root Run
  全部通过，100,000 Session 性能 catalog p95 1.878 ms / page p95 41.137 ms。
- 完整 17 分片回归为 14 PASS + 3 个精确登记的既有失败，0 unexpected failure。原始证据保持在 ignored
  `.local-test-evidence/`，最终 gate ledger 位于 plan verification run 目录。
- 独立 full-audit 首轮发现 S-PS-01/S-PS-05 的逐步证据与 producer-native 打包不足。补测已覆盖外部非 Git
  目录、删除目录错误、复制/Finder、同项目新建和窄屏 Inspector；实现同时补上不可读目录拒绝与 rename
  catalog revision fence。500 × 200 fixture 的新增/删除/重命名分页验证旧游标拒绝，刷新后无重复、遗漏或
  误归组；专项回归 `73 passed`。第二轮审计再要求去重与证据身份逐项可复算，现以生产
  `register_project` 连续注册真实路径、`..` 路径和 symlink，证明三次都返回同一 Project、Projects 总数恒为 1；
  同一原生 UI 新建 Session 的 state/workflow/SDK 数据库查询证明 Provider、execution 与 workflow Run 全为 0。

## 2026-08-26 Project-scoped Sessions v33 全新安装式重置验收

- state.db 升到 v33；从任意旧 schema 升级时，不迁移历史 Session，而是在产品入口开放前一次性清空旧
  Project、Session、消息、Run、上下文和会话派生投影。全局 Provider/默认模型/应用设置/Keychain/账单/
  Skills/Plugins 与磁盘项目、产物文件明确保留；Windows 不在本轮范围。
- 自动化覆盖 fresh v33、v9/v17/v23/v31/v32 代表升级路径和十个迁移故障边界，证明崩溃后 fail closed 且
  重启可幂等完成。后端全量首轮为 `5835 passed, 47 skipped, 1 deselected, 3 failed`；其中两个相关失败已修复
  并聚焦复测通过，剩余一个是与本改动无关的既有 process-list query 失败。前端 `76 files / 624 tests` 与
  TypeScript、当前 debug `.app` 构建通过。
- 独立复核发现并修复三项范围缺口：历史可选 `facts` 现在按存在性清空；workflow.db 也清除 Capability Run/
  runtime/snapshot lease；companion 从反向整库清理改为精确 Run 表 allowlist，因此 candidate package、capability
  governance、growth authority、profiles 与 reminders 会保留。真实 workflow/companion schema 聚焦回归
  `72 passed`，含十个故障点第三次启动保留新数据。
- 2026-08-27 当前 HEAD 真实启动复核发现 workflow 全局 `execution_runtime_state` 曾被清空并导致启动失败；
  已把 runtime state 与 candidate draft receipt/material 加入精确保留清单，并在同一事务内临时移除、随后恢复
  Run 表 immutable delete trigger。专项重置 `18 passed`，相关真实 schema 组合回归 `58 passed`。
- 修复后的当前 HEAD `8631ddcc` 再以完整 v32 隔离 user-data 首次启动：旧 Session/消息/Project 清空，
  `product_sdk_runtime_ready` 与 ingress open；真实 `deepseek-v4-flash` root Run `be702419…` 返回
  `V33-CURRENT-HEAD-OK`，完整重启后新 Session 与两条消息仍可见。数据库确认 reset completed、runtime
  `(generation=1, phase=open)`、旧 Run 哨兵为零、candidate package 保留且迁移备份已删除。
- 最终独立审计又复现 companion 混合表残留旧 Session/message/Run payload；现已把 growth event、偏好/证据、
  消息决策回执、owner Memory scope、jobs、notification/outbox 等加入精确清理清单。真实 schema 测试写入这些
  哨兵后全部归零，并反向确认 billing、Skills/Plugins 文件、candidate draft/package 与 runtime state 保留；
  专项 `19 passed`、组合回归 `59 passed`，trigger 异常路径会完整回滚数据与不变性 trigger。
- 最新实现 `9424da77` 用完整 v32 隔离 user-data 再走真实 UI：首次启动左侧无旧 Session，旧 state 与 companion
  哨兵均为零；SDK Runtime/ingress 开放，真实 `deepseek-v4-flash` Run `8f55d96b…`、`2dc7cebb…` 均完成。
  通过“新建普通会话”创建的 Session `15410cb4…` 在完整重启后仍显示标题、用户消息与模型回复；升级后新
  growth event/outbox 可正常产生，证明旧数据清除和新数据继续工作同时成立。
- macOS 当前 debug `.app` 从 v32 fixture 启动后侧栏为空；无重新登录即调用真实 `deepseek-v4-flash` 返回
  `V33-RESET-OK`。随后 UI 新建的 Project/Session/消息/Run 跨完整重启保留；真实终端以绑定目录为 `pwd`，
  相对读取与写入只落在该目录。S-PS-08 已通过；其余场景已在 2026-08-27 最终 gate 补齐。

## 2026-08-26 Project-scoped Sessions Project/execution root 分离验收

- TC-PS-06 在 macOS 当前 debug `.app` 通过三个 fresh root Run：Session 始终归入 `project-root` 组，
  Inspector 同时显示不同的 Project root 与 explicit execution root；真实 `deepseek-v4-flash` 的 cwd、读取
  和两次写入只使用 execution root，Project-only canary 的相对读取失败。
- 完整 app/backend 重启后第三个 Run 仍保持 split；Project root 无新增执行输出，未自动创建、发现或删除
  worktree。聚焦 backend `109 passed`、frontend `3 passed`。S-PS-06 已通过；完整 release 仍受
  S-PS-01/02/03/05 与既有 gate/audit 问题阻塞。

## 2026-08-26 Project-scoped Sessions 单一 workspace authority 验收

- TC-PS-04 在 macOS 当前 debug `.app` 通过 fresh、temporal-fault 所需 root Runs：真实
  `deepseek-v4-flash` 的终端 cwd、读取、写入与 Project Rules 全部来自 immutable Session Binding；前端、
  retired Code Session、latest-Run 和模型文本冲突路径均不能改根，三个冲突目录无副作用。
- 缺失根目录时 Provider/Tool 计数均为零且无禁写文件；恢复相同 filesystem identity 并完整重启后，fresh
  Run 仍写入同一绑定根。聚焦自动化 `75 passed`。缺失根错误 turn 的跨重启历史显示保留为独立观察项；
  TC-PS-04 的物理执行边界已通过，整个 release 仍受 S-PS-01/02/03/05 阻塞。

## 2026-08-26 Project-scoped Sessions missing-root / relocation 验收

- TC-PS-07 在 macOS 当前 debug `.app` 的 fresh、temporal-fault 两条 lane 通过：缺失目录在 Provider/Tool
  物理执行前 fail closed，无关目录拒绝，活跃 Run 不切根并在重启 recovery 后收敛，同身份 relocation 后
  两个 Session、历史与 Inspector 跨重启保持。
- 修复了缺失根预检调用不存在 SDK API 导致的内部错误，以及冷启动 catalog 响应早于前端 listener 安装
  导致的偶发侧栏空白。真实模型的绑定根绝对路径读取与写入成功；完整 release 仍被其他 required 场景阻塞。

## 2026-08-25 SDK-first 统一能力目录（CAP-1～CAP-5 与 exact-wheel packaged UI 已通过）

- **生产链**：Harness 0.6.2 公共 runtime catalog 统一 built-in、健康 MCP、Skill metadata 与 Workflow；
  compact direct kernel + 同 Run search/describe/activate 已默认启用，v6 catalog/checkpoint 支持 exact replay。
- **安全边界**：activation 只改变可见性；目标调用仍经 Effect/HITL/TaskGrant/workspace/origin。MCP 原子发布
  与 incarnation fence、动态 Run/session/request/scope admission、原始 execution identity 漂移均 fail closed。
- **制品候选**：Harness source `67f5769…` / wheel `ffb7c061…`；Memory source `46624b…` / wheel
  `deff2fa8…`。Host exact pin、lock、installed-origin 与 hash 检查已更新；未创建 tag/release。
- **自动化证据**：Harness `1464 passed, 2 skipped`；Memory `218 passed, 7 skipped`；Host affected
  `301 passed`；全分片 `15 passed, 2 known-failure`、0 unexpected。Vitest、TypeScript、frontend build、
  Rust test/check 与 focused mypy 均通过。
- **真实 UI 边界**：macOS 当前源码 bundle 已真实点击完成 CAP-1。Host 先修复 SDK authority/legacy
  ToolRegistry 的 split scope Store，再冻结物理 registry policy fingerprint，保留 execution-time stale
  fail-closed 校验。最终 Session `f800f3fd-8a4d-42b5-b7e3-ada0b8fe3d49`、root
  `230fe1e642ec5cb8b20717741a073c6c` 在同一 Run 内将 Tool 数 13→14→15；真实 filesystem search/read
  均 `ok`，模型读取 README 并正确概括第一段。CAP-1 PASS。
- **真实浏览器边界**：CAP-2 首轮真实 UI 暴露两个独立缺口：Run 不知道当前本地页 URL、Playwright
  `allowed-origins` 只覆盖默认端口。现由受信任项目/任务快照注入经过 loopback/无凭据/显式端口校验的
  `local_page_url`，MCP 启动前把 exact origin 合并到真实启动参数和 build identity；新安装默认只通配
  localhost/127.0.0.1 开发端口。最终 Session `7b03b54a-17da-4345-b8ef-95ee0200a008`、root
  `6b0e26708de8535695148e88023397d0` 通过真实 Playwright `goto` + `page.title()` 返回固定 fixture 标题。
  CAP-2 PASS。
- **真实 Skill 边界**：CAP-3 首轮暴露 SDK catalog 的 Skill locator、英文词形搜索、SDK/legacy resolver
  路由与 trusted context 投影缺口。现由候选 SDK 搜索 `translate/translation` 的保守同源前缀，Host 将
  `skill:translate-doc` capability ID 投影为可调用 locator `translate-doc`，并用 Run authority 的完整
  `ToolExecutionContext` 调用冻结 resolver。最终 Session `19d3bc29-78b0-4897-a1df-b7fa28be9476`、root
  `15d531d2ad9757fb8d26d0ca84905bd3` 的 `tool_search` 与 `skill_invoke` 均成功；隐私安全日志只记录正文
  载入次数与 SHA-256，UI 保留 H1/H2/H3、全部三条列表及代码样式 `tool_search`。CAP-3 PASS。
- **公网 origin 负例**：CAP-4 Session `8b062b24-3a63-48ef-aa35-e69babe3821a`、root
  `39ace93c669e5999bb1700897e50b356` 真实搜索/描述/激活 Playwright navigate 后，精确调用
  `https://example.com`；客户端返回 `ERR_BLOCKED_BY_CLIENT`，页面未打开，Agent 明确报告无法访问并未执行
  表单提交，也未换用其他 Tool 绕过。CAP-4 PASS。
- **冷重启与 exact-wheel 边界**：CAP-5 用两个跨完整 app/backend 重启的独立 root 验证新 Run 从 13 个基础
  工具重新发现和激活；过期 nonce 被明确拒绝，随后重新 describe/activate 自愈并完成真实 README 读取。
  Host 再从 vendored exact 0.6.2 wheel 同步，以无 `PYTHONPATH` packaged macOS app 独立完成 13→14 与真实
  `head=20` 读取，Session `b3479aa9-201b-4fcf-8068-a5246b7fe20b`、root
  `18efe97f5dee509db92b4c113688a346`。source `67f5769…` 的最终 reproducible wheel `ffb7c061…` 与完整真测
  wheel 的运行时包逐文件相同；Host 重锁、重装后再次完成无 `PYTHONPATH` 冷启动与可操作 UI 冒烟。
  CAP-5 与 exact-wheel packaged UI PASS。候选仍不能作为 SDK release 提升：本次只授权代码提交到远程
  主分支，没有授权 tag、release 上传和 download-back promotion。
- **调查日志（2026-08-25）**：SDK Provider 真实调用 seam 已增加 privacy-safe 关联日志，覆盖 request start、
  HTTP response shape、parse 和 terminal outcome；同一匿名 `request_ref` 可区分 transport timeout、HTTP 408、
  非 2xx、协议结构错误与 Host contract 错误，不保存正文、Tool 参数、凭据、endpoint 或上游 request ID。
  Provider/投影/SDK execute 原聚焦回归 `79 passed`；本次 scope/policy 修复聚焦回归 `72 passed`，测试文件
  Ruff 与 diff-check 通过。stale 日志进一步区分 `policy_fingerprint` 与 `spec_or_eligibility`，且不记录参数正文。

## 2026-08-24 Provider 模型选择目录自动刷新

- **根因与修复**：模型选择器只在控制 WebSocket 初连时请求目录；若 Provider 随后才添加，进程级 Store
  会一直保留空目录，只显示“跟随 provider 默认”。现在 `providers_changed` 权威广播会刷新目录，且每次
  打开模型弹窗都会按需重拉，已打开下拉框可随响应更新。
- **边界**：目录仍来自 backend 当前启用 Provider 链的 `models_list_response`；设置页 probe 结果不越权
  直接写会话 Store，也未新增硬编码模型清单。因 Provider 暂未返回上下文能力元数据，所有 Context 型号
  临时开放 128K / 256K / 512K / 1M 用户预算档位，默认 256K；非 Context 型号排除，且 UI 明示实际上限
  仍由 Provider 决定。模型按钮及弹窗只展示实际生效模型名，不再暴露“默认模型/跟随 Provider”等内部语义。
- **自动化证据**：Provider 设置既有聚焦前端 `23 passed`；临时上下文策略 backend `21 passed`；模型按钮、
  弹窗及 Provider 联合回归 `30 passed`，TypeScript typecheck PASS。当前源码 debug `.app` 的 macOS
  Computer Use 真测确认顶部 `gpt-5.6-sol`、弹窗同名模型、默认 256K、筛选并临时选择 `gpt-5.4`、取消后
  原值恢复；重新构建后的冷启动还确认头部 Session ID 技术标签及空态短横杠已移除。截图保存在 ignored
  `.local-test-evidence/2026-08-24/model-display-real-ui/` 与 `header-empty-session-id-removal/`。

## 2026-08-23 SDK observability 完成并成对发布/换包

- **Host composition**：已新增共享 256-entry ring + bounded JSONL + safe logging composite sink，并接入
  Memory production builder 与 Harness production runtime config；真实 SDK run ingress 绑定 opaque
  correlation，既有 principal/owner/session authority 不变。
- **诊断边界**：用户 log 目录只产生显式 SDK events/ring/snapshot 文件；Rust bundle 只 allowlist 这些
  文件并执行单文件/总量/canary 门，ambient logs/crash/metrics 与 DB/outbox/content 均不复制。
- **发布状态**：Harness `v0.4.0`（source `bc6ae8d`，wheel `aaf8d79a…`）与 Memory `v0.5.0`
  （source `9c92ede`，wheel `c274fa6b…`）已按 README 从干净 detached worktree 构建、发布并下载回验；
  Host 已成对 revendor，installed-origin/hash 门通过。
- **自动化证据**：Harness full `1404 passed, 2 skipped`；Memory full `213 passed, 7 skipped`；
  exact-wheel joint artifact `10 passed`；Host focused `212 passed`；
  Rust diagnostics `7 passed` / lib full `83 passed`；Python compile、git diff check 与目标 rustfmt check PASS。

## 2026-08-22 Harness 0.3 / Memory 0.4 官方一等集成（代码、自动化与真实 UI 完成）

- **exact release candidate**：Harness `fbb156f…` / tag `v0.3.0` / wheel SHA `cf629cee…`；Memory
  `3d4247b…` / tag `v0.4.0` / wheel SHA `bfcd2506…`。版本、wheel hash、tag 与 direct-url
  installed-origin 均 fail closed；三个仓库主分支与两个 SDK tag 已推送。冻结 SDK 产物由本地流程构建，
  已正式发布到对应 GitHub Release，并通过公开稳定 URL 下载回验。
- **生产组合**：一个 production `MemoryManager` 同时服务 Harness 与非 Harness product projection；Runtime
  使用 `BORROWED` ownership；shutdown 先关闭 runtime borrowers，再由 SessionDB 有界 drain/唯一关闭
  manager，重复/并发关闭不二次释放。前台 root/continuation 不再手工 recall/prepare，改由
  SDK 的 `AgentMemoryPort` 与正式 read-only Context provider 自动生成 frozen stage。
- **身份与隔离**：actor 只取 validated local `HumanIdentity.identity_namespace_hash`；deployment 来自
  `state_db_identity.instance_id`，household/session binding 持久且不可换绑。SH-I01 的重启稳定、Provider/
  API key/model/payload spoof、跨 user-data 隔离与损坏身份 fail-closed 均已自动化覆盖。
- **authority/durability**：Harness foreground 只产生 successful committed-turn outbox；非 Harness producer
  继续使用 product outbox。Context source 实现 PENDING/CLAIMED/STAGED/CONSUMED、lease、shared refcount 与
  fail-safe orphan GC；产品 v4 migration coordinator 保证 execution/memory all-old 或 all-new。
- **工具边界**：普通 foreground catalog 移除 `memory_recall/memory_search`；现有 remember/read/forget 使用
  完整 trusted principal 和正式 `remember_fact/read_fact/forget_fact`，write 返回准确 fact ID，并保持 metadata
  idempotency、跨 principal 隔离；forget 显式 action ID 的 True/False receipt 可重放、跨重启且不复活。
  Memory SDK 已提供正式 authorized share API，本轮不新增
  simple_harness share Tool/UI。
- **自动化证据**：Harness full `1379 passed, 2 skipped`；Memory 默认 full `200 passed, 7 skipped` / 正式
  candidate gate `205 passed, 2 skipped`；产品最终聚焦
  backend `83 passed`、MemoryPanel `18 passed`、TypeScript PASS；full baseline 15 PASS + 2 个实施前 known-red，
  0 unexpected。
- **真实 UI 证据**：macOS Computer Use 使用设置页的 DeepSeek 完成 SH-M1～SH-M6、SH-SURFACE。跨 Session
  `Max` recall、PPT/permission/Artifact、恶意 Memory 数据隔离、冷重启、带进程级 fault attestation 的
  recall timeout、record transient 崩溃恢复，以及当前构建的附件选择器打开/Esc 安全取消均通过；停止链
  落到 `run.cancelled` 且 ordered projection cursor 不再卡死。
- **范围边界**：AIPhone、K6/AgentOS、NovelTagSystem 未修改、未集成、未测试；Harness/Memory 的 future-consumer
  与 `share_fact` 接口已就绪。r7 独立审计发现的证据标签、fault attestation、S6-A8 状态和 custody 问题
  已在继任 Gate 前修复；r7 不作为发布 receipt。原始证据只保存在 ignored `.local-test-evidence/2026-08-22/`。

## 2026-08-21 macOS 测试基线收口与历史 Windows 测试退役

- **当前平台边界**：simple_harness 现阶段只维护 macOS 测试基线；旧 DeskPet Windows 窗口控制、
  Playwright bundle、PowerShell fixture、Windows 字体/PPT fixture 与关联 workflow eval 测试已从活跃
  测试树删除。未来支持 Windows 时按 simple_harness 当前生产合同重新设计测试，不继承这批历史断言。
- **历史合同清理**：移除已退役 companion fault matrix 及 source-only 旧入口断言；其余仍覆盖当前生产
  行为的测试改为 SDK production composition、Memory SDK、现行 Provider/Keychain、macOS 路径和
  结构化日志合同，不以批量 skip 隐藏失败。
- **macOS 修正**：临时目录统一解析真实路径以兼容 `/var` → `/private/var`；backend build verifier
  支持 macOS 的 `python3`；Godot detector fixture 使用可执行 POSIX shell 文件。
- **验证**：完整 `backend/tests` 冷跑 `5708 passed, 47 skipped, 1 deselected`，0 failed；相关定向
  回归 `18 passed`；`git diff --check` PASS。当前环境未安装 Ruff 可执行文件，因此未将 Ruff 冒充为
  已运行门禁。

## 2026-08-21 Agent Runtime SDK 0.2/0.3 消费者切换（含 simple_harness 真人回归）

- **生产链**：SDK `build_production_runtime` 默认启用 conversation Memory 与
  `consumer_prepared` staging；root/continuation 以稳定 identity claim private projection-v2 stage，
  recalled Memory 仅作为 USER/untrusted data，winner stage 后才公开 projection/start/signal。
- **Memory 一致性**：Harness execution outbox 与 simple_harness `state.db.product_memory_outbox` 按 provenance
  分治；后者与 message 同事务，冻结 instance/user/session/id/hash，支持 CAS lease/reclaim/backoff/
  dead-letter/ack/cleanup。Session→Memory user 一次绑定、不可变，跨 user mismatch fail closed。
- **开发 reset/隐私**：显式 dev-only 脚本在服务停止后精确清空 state/execution/memory 三库及 sidecar，
  随后空库初始化；DB 强制 regular-file/no-symlink/0600，diagnostic bundle 排除数据库、sidecar 与 outbox
  原文，只保留脱敏状态/计数。
- **候选身份**：Harness 0.2.0 `869c76f…` / wheel `e1f7d4b1…`；Memory 0.3.0
  `87820fe…` / wheel `6f0682fd…`。两者版本、SHA 与 direct-url origin 均 fail closed。
- **生产冷启动修复**：`runtime_factory` 通过 `ProductionRuntimeBuild` 显式发布 workflow registrations；
  `ServiceContext` 声明 SDK context staging 与 conversation Memory；Memory dispatcher 注入 Host 时钟，
  provider projection pump 同时兼容同步和异步 `start()`。永久回归真实调用
  `_build_product_sdk_runtime_stack()`，验证 Runtime ready、两项 authority publication 与正常关闭。
- **验证**：D1 `58 passed`；D2/exact `17 passed`；Rust diagnostics `4 passed`；D3 typecheck +
  `73 files / 615 tests`；production build PASS。D-ALL 发现既有 baseline inventory 未登记 12 failures 与
  ESLint 171 problems，原始日志在 ignored `.local-test-evidence/2026-08-21-agent-runtime/D-ALL/`。
  本轮生产 composition 与 SDK/Memory/outbox/reset 聚焦回归 `226 passed`。
- **真人消费者回归**：macOS Computer Use 已通过真实 `.app` 完成 CTX-1～CTX-5 与 critical surface smoke。
  两个 fresh root 都能经 UI 选择公开文本附件并回答首行，公开 Context 仅显示 `input_text × 1` 与
  估算预算；历史、多轮、缺失文件、Provider/Session/Context 冷重启均通过。真人停止还暴露并修复
  budget-only `unknown` 回执卡死 Provider projection cursor 的缺陷：现场 cursor 从 24 连续推进至 27，
  下一真实 Run `225d6f74-23d8-40d9-a105-3b5a53d78c5a` 完成并回到 `✓ 空闲`。最终聚焦回归
  后端 `102 passed`、InputBar `20 passed`、TypeScript PASS；证据索引见 ignored
  `.local-test-evidence/2026-08-21/sdk-context-consumer-regression/RESULTS.md`。

## 2026-08-21 Provider 设置与 SDK 冷启动真机修复

- **用户可见根因**：测试实例后端端口与 bundle 前端默认端口不一致时，Provider 模型探测与新增请求没有
  到达后端；旧 UI 又在 `send()` 返回失败时关闭新增弹窗，并且模型探测没有超时，表现为“获取中”永久
  卡住和“点击添加但列表为空”。编辑既有 Provider 还会因前端不持有明文密钥而用空 Authorization 探测，
  产生 401。
- **修复**：保存改为传输失败保持弹窗、后端 ACK 后关闭；模型探测增加 20 秒超时；编辑探测通过
  `provider_id` 在后端读取 App 专属 Keychain，且必须与已保存 base URL 精确相同。SDK 冷启动同时修复
  frozen Tool schema 的嵌套 `mappingproxy` 与五个未声明 `ServiceContext` publication，Runtime 可正常开放。
- **验证**：Provider 前端 40 tests、TypeScript PASS；后端 Provider/Runtime/catalog/authority 聚焦
  52 tests PASS；额外 Runtime service 25 tests 与 catalog 49 tests PASS。重新构建 debug `.app` 后，
  Computer Use 真机确认 `product_sdk_runtime_ready sdk_version=0.1.5 phase=open`、DeepSeek `/models`
  HTTP 200、添加 `Local Fixture` 后列表即时出现。原始证据仅保存在 `.local-test-evidence/`。
- **真实对话补测与现场修复**：首次 DeepSeeker `run_shell(pwd)` 真测发现 SDK 已持久化 open
  `tool_authorization`，但桌面 Host 未投影 `permission_request`，Run 因 UI 无授权卡停在 WAITING。
  现由 `SdkRuntimeIngress` 读取 durable open decision 并投影 public-safe、nonce/version fenced 的既有
  授权协议；live WAITING 与 reconnect replay 均覆盖。复测确认 `deepseek-v4-flash` 两次真实
  `chat/completions` 均 HTTP 200、允许一次成功、工具结果卡为 `ok`、Run completed、最终回复
  `done`。原始证据位于 ignored `.local-test-evidence/2026-08-21/deepseeker-provider-tool-e2e/`。

## 2026-08-21 SDK Run 停止链路修复

- **根因**：消息页发送 canonical `root_run_id`，SDK Runtime cancel 只接受内部
  `product-sdk-*` id；旧路径直接透传导致 `KeyError`、control WebSocket 断开且 Run 继续执行。
  此外 Provider 已 handoff 后的取消会按副作用安全规则记录 invocation `unknown`，旧产品边界没有
  将已确认的用户取消重新传播为 Run cancellation，可能再次落回 `waiting`。
- **修复**：Host 在 SDK Run 生命周期内维护 canonical→internal id 映射并在 start 前注册、finally
  清理；取消缺失时幂等返回且不击穿 WebSocket。产品 Provider coordinator 在 cancel token 已确认时
  将 invocation unknown 恢复为 Run-level cancellation；Host 不再把用户已取消后的瞬时
  running/waiting 状态投影成 `run_failed`。
- **验证**：SDK execution/provider/runtime 聚焦 `43 passed`，py_compile 与 diff check PASS。
  Computer Use 在 Session `7e962a92-b5b4-4724-a355-bf6f9ef28a66` 真实发送 120 秒任务并点击
  “停止”：Run `131d90558c0759679743fd636d8169f7` 约 0.7 秒收束为 `cancelled`，输入区恢复空闲，
  无最终 `done`、无 `run_failed`、无 WebSocket 断线重连。

## 2026-08-21 macOS 中文输入法候选确认误发送修复

- **根因**：InputBar 只检查 React `nativeEvent.isComposing`；macOS WebKit 可能在候选确认时先派发
  `compositionend`，再派发同一次 Enter 的非 composing `keydown`，从而穿透 Enter-to-send 分支。
- **修复**：输入框新增 composition 生命周期 ref、WebKit `keyCode=229` 兼容判断和
  `compositionend` 后的一次性 Enter latch。候选确认 Enter 不发送，下一次独立 Enter 仍正常发送；
  不改变 Shift+Enter、Slash 候选和输入历史语义。
- **验证**：新增 active composition、229 sentinel、end-before-keydown 与后续正常发送三组永久
  回归；InputBar/ChatView 共 `26 passed`，TypeScript typecheck、debug `.app`/DMG build PASS。
  Computer Use 在新构建中验证候选 UI 出现、草稿可保留/清空且消息流未新增；因自动控制层不能触发
  macOS 全局输入源切换，精确简体拼音候选的最终按键确认需用户在中文输入源下再复核一次。

## 2026-08-21 macOS Provider 持久化与 App 数据隔离修复

- **根因**：Tauri identifier 虽为 `com.dennywanye.simpleharness`，Rust/Python classic fallback 与
  Keychain service 仍使用共享名 `deskpet`；Rust 还会把 Cargo `target/debug` 和 macOS bundle 旁的
  可写目录误判为 portable userdata。构建目录清理后 registry 变空，启动期 orphan recovery 又从
  `~/Library/Application Support/deskpet/config.toml` 复制 chinzy，形成“每次重构都恢复中转站”。
- **修复**：classic data/models 与 Keychain namespace 全部改为 App 专属 identifier；portable 必须
  有 `.deskpet-portable` sentinel；普通启动停止环境式 endpoint 恢复。旧 Keychain 只对 canonical
  config 已列出的精确 provider id 做一次性复制，不枚举、不删除共享 service。设置页路径与卸载/
  model override 文案同步显示新的事实源。
- **迁移**：当前含 `deepseeker-myself` 的有效 profile 已从不稳定 build userdata 非破坏性复制到
  `~/Library/Application Support/com.dennywanye.simpleharness/`；旧 `deskpet`、旧 bundle userdata
  和旧 Keychain 项均保留，未做破坏性删除。
- **验证**：Rust paths `15 passed`；Python paths/config/provider registry `80 passed`；TypeScript
  typecheck、debug `.app`/DMG build、diff check PASS。Computer Use 检查新 bundle 与完整冷重启后的
  设置页：只存在 `deepseeker官方`，默认 `deepseek-v4-pro`，API Key 已保存，当前/默认数据目录完全
  一致；两次真实模型目录探测均 HTTP 200。截图 SHA-256
  `cce0744340dbd863516f9616f7804d071fddbabb0e156a9f08d3445a438780d0`，原件仅存 ignored
  `.local-test-evidence/2026-08-21/provider-persistence-macos/`。

## 2026-08-20 SDK 公开思考过程、工具水合与记忆工具真机闭环

- **UI 行为**：同一 canonical Run 的公开工作叙述和工具卡组成唯一思考分组；运行态自动展开并
  计时，终态自动折叠，点击可反复查看，最终回复保持独立。tool-only DeepSeek Run 同样分组，
  两类全局隐藏开关均关闭内容时不产生空组。
- **安全边界**：优先投影模型公开 assistant content；工具回合公开 content 为空时，仅按公开 tool
  name 生成有界工作叙述。隐藏 `reasoning_content`/CoT、工具参数与原始 Provider payload 永不进入
  UI、durable summary 或后续模型 context。公开 summary 固定 `context_visibility=exclude`，上下文
  组装另以 conversation projection allowlist 兜底。
- **工具修复**：普通 SDK Run 的 `memory_recall` 不再错误依赖 Companion generation-0 snapshot；
  `memory_search` 从 retired stub 切到真实 MemoryBackend。scope fallback 仅允许精确缺失异常，其他
  非法结果 fail closed。tool durable envelope 保留五态 outcome，web failure 不再在重启后漂绿。
- **验证**：相关 backend `87 passed`，全前端 `600 passed`，TypeScript、debug build、diff check
  PASS。Computer Use 使用用户配置的 `deepseek-v4-flash` 真实运行：5 秒时自动展开，
  `memory_recall/memory_search/read_file×2` 全成功；19 秒终态自动折叠、手动开合成功，完整重启后
  仍为 19 秒且四组结果保持 `ok`。Session `1a5883b0-b50f-41fb-ada1-50f8811dca2e`；原始证据只在
  ignored `.local-test-evidence/2026-08-20/sdk-thinking-process-run-1/`。补充 tool-only 真实 Run
  `81c61acc-2fc4-4410-bc25-47bc33f2205b` 验证重启水合；最终代码 Session
  `96ae3cec-e785-486a-9152-78319a569872` 验证运行态公开叙述位于每组工具卡前、15 秒终态自动折叠，
  点击可恢复全部叙述与工具状态。

## 2026-08-20 升级后 Computer Use 全工作台回归与修复

- **真机覆盖**：当前源码 debug `.app` 在隔离 profile 上通过首次设置、会话、技能中心、产物库、
  更多菜单、记忆、ContextTrace、反馈、设置、Provider 编辑取消、模型参数、Harness 观察和真实
  发送失败路径。动作均为 Computer Use 坐标点击/真实键入；未用 DOM/WS 注入替代。
- **已修问题**：能力筛选后右侧详情残留；Companion credential mismatch 无界重挑战风险；更多
  子菜单只有图标无文字；记忆 IPC 读取退役服务键导致永久加载；空记忆不可读；SDK Runtime
  技术错误无恢复指引；Tauri 关于页版本仍为 `0.1.0`；新 Provider 直接使用 36 字符 UUID 而被
  后端 32 字符契约拒绝；首个 Provider 添加后 SDK Runtime ingress 仍关闭直到重启；SDK 工具
  第二轮丢失 assistant `tool_calls` 导致 DeepSeek HTTP 400；TaskGrant/PreparedEffect 跨层契约漂移；
  Session 显示 flash 但真实 SDK 仍调用 pro；无项目绑定的相对写盘误落 backend cwd。
- **Provider 修复事实**：前端生成 `provider-<23 chars>` 并在提交前校验 kebab-case/32 字符；
  Provider add/update/remove/reorder 成功后串行关闭旧 ingress/stack、按最新 registry 重建冻结
  adapter 并重新开放 Companion ingress，空链或无效凭据保持 fail closed。
- **SDK Tool 修复事实**：assistant tool-call 结构进入 durable Message metadata 并在后续
  OpenAI-compatible 请求还原；自动授权使用合法且确定性的 `policy:auto` TaskGrant，prepared
  identity 从 context metadata 读取。Run 前冻结 adapter 对齐 Session provider/model，并在 Run
  内锁定。无显式项目 workspace 时 SDK ToolContext 与 `write_file` 相对路径统一绑定
  `<user_data>/workspace`。Run capability snapshot 改用完整 77 项产品目录，补回 legacy registry
  漏掉的 17 项动态工具；SDK tool call/result 通过 `RunPresenter` 实时投影并持久化为标准
  assistant/tool 消息，UI 不再只显示最终文本而隐藏实际工具生命周期。Provider HTTP 200 后的
  typed contract violation 现在归一为确定的 `provider_protocol_error`，不再落入 unknown handoff
  而把会话误报为 `run_failed — sdk_run_waiting`；异常诊断不记录请求、响应或 secret。工具目录
  还将 `memory_recall` 接到现有 memory SDK owner-scoped adapter；其余四个尚未迁移的历史内存
  handler 由 import-safe compatibility module 明确降级，不再因旧 manifest 路径失效拖垮整份目录。
- **验证**：原工作台前端聚焦 `20 passed`、credential 后端 `10 passed`；新增 Provider 前端
  `36 passed` + TypeScript PASS，Provider IPC/运行时刷新后端 `15 passed`；debug `.app`/DMG
  构建 PASS。真实 DeepSeek `deepseek-v4-pro` 与会话绑定 `deepseek-v4-flash` 均完成 HTTP 200，
  Provider 编辑热刷新后无需重启继续回复；再次重启保持 Provider、消息和模型绑定。新增工具/
  Provider/路径聚焦 `62 passed`，组合验证 `56 passed`，Python compile PASS。最终干净 Session
  `9ae8ed31-37dd-45b6-a24a-16f4a3cb139e` 真实调用 `deepseek-v4-flash`，四个 Provider turn、
  `run_shell/write_file/read_file` effects 全成功，隔离 workspace 文件内容与 13-byte 结果一致。
- **证据**：原始回归保存在 Git ignore 的
  `.local-test-evidence/2026-08-20/manual-computer-use-regression/`；本轮聊天截图在
  `.local-test-evidence/2026-08-20/provider-runtime-fix/chat-flash-model-pass.jpeg`，SHA-256
  `9a32004d2d29a6bbf6906a0371dc4548fd244589125cf68563331f1f04592a0a`；最终 Tool 截图
  `final-deepseek-tool-e2e-pass.jpeg`，SHA-256
  `ca3b9bafa2da0e7adf14e96635cd250a43f0b497e5356d1da9763e2ebed3cfc4`。Agent 工具修复复测
  Session `233737e1-acbe-4f02-b190-d9334eb262f2` 的真实请求携带 77 项工具，UI 显示
  `tool_search` 调用/成功卡片并确认三项 agent 工具可用；截图 `agent-tools-visible-pass.jpeg`，
  SHA-256 `37637ffdb3a430264856cd62d93a202873cffb87cc49ca310af4f3a3f22767c8`，聚焦回归
  `71 passed`。等待态分类修复后，同一 Session 的完整项目调查连续 5 个工具 effect 成功；最终
  版本再次真实调用 `agent_reach_read` 并收到 `FINAL WAIT FIX OK`，最新账本为
  `Run completed / Provider succeeded / effect succeeded`。截图 `sdk-run-waiting-fixed-pass.jpeg`，
  SHA-256 `14b3185c2a5d49da445f243b6695024fe507829e9c29ea3352b1f841a7277afc`；聚焦回归
  `72 passed`。

## 2026-08-20 Agent 执行时间线与上下文隔离 slice

- **实现状态**：代码与 public projection 已完成；Inspector 现在从同一 `PublicRunSnapshotV3`
  渲染有界、脱敏、可折叠的 activity timeline，旧 V3/legacy snapshot 仍安全降级。
- **上下文边界**：时间线阶段/状态/耗时/进度/终态、工具安全摘要/预览、Inspector diagnostics、
  correlation 与 hidden reasoning 均固定 `context_visibility=exclude`；当前执行轮真实
  `assistant.tool_calls`/`tool` result 仍按模型协议进入该轮 context，但 UI projection 不会回灌。
- **自动化验证**：S-6 malformed/replay contract smoke PASS；S-7 backend/frontend contract、旧行为
  聚焦回归、typecheck、build PASS（19 backend tests + 70 frontend tests）。证据账本见
  `plans/2026-08-20-agent-activity-timeline/verification/run-1/`。
- **真人验证状态**：S-1 至 S-5 保持 `NOT_RUN`。Computer Use 尝试被 macOS 锁屏阻断，不能用
  DOM、Vitest 或 WebSocket 直注替代真人点击；解锁后仍需完成截图、真实动作和日志断言。
- **门禁工具已知问题**：当前 `plan_test_gate.py record-run --exec` 同时写 run/evidence 时，
  完整性计数会在下一次写入误报 `LEDGER_TAMPERED`；本轮按合法的 `record-run` 后独立
  `attach-evidence` 路径入账，未手改账本。

## 2026-08-20 修复：SDK fresh-run 多轮上下文与 Session ID 双击复制

- 文字 follow-up 继续走 SDK fresh run；Host 现在从 SessionDB 注入最近 20 条按时间正序的
  `user/assistant` 历史，排除当前 root 已落库的用户消息后只追加一次当前输入。普通多轮上下文
  不再完全依赖 memory recall。
- ChatView 标题栏和 SessionList 会话行的 Session ID 改为整段选择，双击 UUID 不再只选中一段。
- 聚焦验证：后端 `4 passed`，前端 `15 passed`，TypeScript PASS；真实桌面双击 E2E 尚未执行。

## 2026-08-20 修复：SDK 工具调用失败与终态投影

- 修复 SDK 内部 execution id 与产品 canonical `root_run_id` 混用导致的 run projection 不收束；
  `chat_v2_run_started/final/error`、SessionDB 和 UI 现在统一使用 canonical root，SDK id 仅留在
  checkpoint/effect/delivery 内部。
- 修复 SDK 桌面授权装配的跨层契约漂移：`ProductAuthorizationAdapter` 正式支持
  `policy.decide(prepared, request=...)`，并兼容旧 callable fixture；工具调用可继续进入
  effect handoff/settle，失败终态统一投影为 `run_failed` 并回到 idle。
- 验证：授权适配器与 SDK execute 聚焦回归 `38 passed`；Python compile、diff check、前端
  TypeScript 与既有前端 `50 passed` 已通过。Computer Use 已真实验证普通聊天、多轮上下文、
  失败终态收束和工具成功链路；最终干净会话中 `tool.effect_settled`、Provider follow-up、
  Session flash 模型与隔离 workspace 文件均已核验通过。

## 2026-08-20 里程碑：SDK 生产化 program 完成（harness 0.1.4 + memory 0.2.0）

六个 slice 全部 plan-test 全流程 + 机器门 finalize PASS（receipt 见 program plan）：

- **H1 · harness v0.1.4**（`c5f546cd`，1226 passed）：消除假投递 / ToolContext / facade 边界 /
  DB 生命周期 / logger 回归 / CI 全量 pytest + scoped ruff/mypy / 版本单一来源 / Memory Port reserved。
- **M1-M4 · memory 0.2.0**（`bf594aa2`/`eac81d14`/`dcef5d80`/`f7a3ee2b`，83 passed）：
  召回只读 + 隐私日志 + async Embedder；schema 版本化/迁移/checksum + 原子事务 + 幂等键；
  级联删除 + embedding lineage + 资源上限；云端 embedding（fail-closed + 凭证安全）。
- **C1 · 宿主 re-vendor**（`3ebe0ac0`）：vendor harness 0.1.4 + memory 0.2.0，
  `sdk_candidate.py` 单一事实源同时 track 两 wheel 版本 + SHA；memory SDK 版本改 hatch 动态（修漂移）。

程序事实源：`plans/2026-08-19-sdk-productionization/program-plan.md`；各 slice 的
`acceptance.md`/`plan.md`/`verification/` 在各自 SDK 仓库 `plans/2026-08-19-*/`、`plans/2026-08-20-*/`。

## 2026-08-19 里程碑：SDK 易用性优化（harness 0.1.2 切换 + companion 冷启动修复 + 双 SDK 文档/验证脚本）

- **harness SDK v0.1.3 re-vendor（2026-08-19 晚）**：接续 sdk-consumer-0.1.3（消费者层 model/tool_schemas 缺陷修复），
  宿主 vendor 0.1.3 wheel（SHA 81025b2c…）、SSOT 单点切换（只改 `sdk_candidate.py` 三行）、18 分片回归零新增红、
  真机冷启动 `sdk_runtime_ready 0.1.3` + 真 DeepSeek 294 字符回复。0.1.3 对 0.1.2 纯新增，宿主 10-Port 零改动。
- **harness SDK v0.1.2 切换为唯一生产 ingress**：`backend/vendor/` 纳入官方 0.1.2 wheel（SHA
  `387c8d1d…efd4c`，对 0.1.1 纯新增）；wheel 身份收敛为单一事实源
  `deskpet/sdk_adapters/sdk_candidate.py`，原 6 处生产硬编码 + 2 个测试 + verify_sdk_wheel.py 全部改 import
  （FAIL-5 关闭）。conformance 22/22（基线 20/20）、18 分片回归零新增红。
- **真机 COLD-1 暴露并修复两个既有冷启动缺陷**（0.1.1 上同样存在，scope 扩展用户批准）：
  ① 全新安装未配 provider 时 `NoProviderConfiguredError` 未捕获 → 整个 app lifespan 崩溃；
  现 `_provider_chain_or_none()` 优雅跳过（SDK runtime 留待配置后激活）。
  ② growth authority cutover 在 fresh companion.db 上失败：三个 `execution_build_*` authority manifest
  仍 pin 25 个已随 SDK cutover 移入新 catalog（`real_tool_manifest.json`）或 memory SDK 的 core.* handler，
  handler-switch 校验不过 → growth 卡 paused → `companion_profile_bind` 被拒 → 新用户聊天输入框永久禁用。
  重建 manifest（25 行删除 + build manifest 重新生成）后 manifest legacy 期望集与线上注册表精确吻合
  （69=69），cold repro 达到 `growth_authority_ready phase=companion`。附带收益：8 个既有红转 PASS。
- **真机验证 PASS**：全新 userdata → 配置 provider → 重启 → `sdk_runtime_ready sdk_version=0.1.2` →
  主聊天"你好，介绍下你自己" → `chat_v2_final` 251 字符、0 run 失败。
- **SDK 仓库消费者体验**（simple-harness-sdk 5 commit）：minimal-consumer 修复（连续两次 COMPLETED exit 0）、
  quickstart 对齐真实 API、`build_consumer_runtime` 推广为推荐入口、`verify_from_zero.sh` from-zero 门禁。
  另记录 2 个待 0.1.3 修的 SDK 设计缺陷（provider model 名硬编码 → unknown charge；占位 tool spec 拒参数）。
- **事实源**：`plans/2026-08-19-sdk-usability-optimization/`、`ARCHITECTURE/SDK_EXTRACTION.md`。

## 2026-08-19 里程碑：记忆 SDK（simple-harness-memory-sdk）接入 host 真机 E2E 打通

- **认知记忆链路真机验证 PASS**：主聊天用户消息经 `SessionDB.append_user_message_with_growth_outbox`
  双写进 SDK `memory.db`，facts 抽取 `pet_name=Max` / `location=上海` / `prefers=咖啡`，单值 key
  正确 supersede；真实 DeepSeek `deepseek-v4-pro` 调用 200 OK，assistant 回复经 `chat_v2_final` 回推前端。
- **host 侧补齐 5 处接线缺口**（`main.py` / `session_db.py` / 新建 `recall_adapter.py`）：
  ① 新建 `deskpet/memory/recall_adapter.py` 暴露 `memory_recall_query` / `memory_recall_scope_resolver`
  两个 provider；② 注册 `context_page_in_store` / `memory_recall_query` / `memory_recall_scope_resolver`
  三个 SDK tool-catalog 依赖；③ `ProductDeliveryAdapter()` 无参坏桩 → 走全局 `_DeliverySink`；
  ④ `tool_catalog` 缺 `current_generation()` → 补 `_ProductToolCatalogGeneration`；
  ⑤ `append_user_message_with_growth_outbox` 补记忆双写 + `_execute_sdk_run` 补 assistant 回复桥接。
- **SDK 加结构化日志**：`simple-harness-memory-sdk` 引入 `structlog`，在 `append_message` /
  `extract_facts` / `recall` / `digital_twin` / `daily_decay` / `summarize` / 生命周期 / embedder
  fallback 等关键路径输出 `memory.*` 事件，错误路径用 `logger.exception` 保留堆栈（替代 SDK 内部
  `HarnessError` 吞 private_cause 导致的不可追踪问题）。
- **后续收束（2026-08-20）**：`memory_recall` 已改由 SDK 产品目录直接绑定
  `recall_adapter`，不再尝试向已退休的 legacy core-handler authority 注册；执行身份 manifest
  已随工具源码重建。
- **已知残留（不阻塞）**：`oh4_curation_skipped reason=no_facts_store`（curation 未接新 SDK）；`model_provision_failed
  HTTP 451`（BGE-M3 下载被网络挡，走 hash fallback）；`growth_authority_cutover_failed`（companion
  切代报错但随后仍 ready）。
- **事实源**：`ARCHITECTURE/MEMORY_SDK_BOUNDARY.md`、`plans/2026-08-17-memory-sdk/HANDOFF.md`。

## 2026-08-17 里程碑：SDK v0.1.1 为唯一生产 ingress，旧 harness 死代码已清理

- **SDK Runtime 确认为唯一生产入口**：main.py 中 `_sdk_ingress` 是所有产品入口（text/voice/background）的唯一活跃 ingress，旧 `_harness_venue` 构建代码已删除。
- **Harness 死代码清理完成**：删除 `_build_product_harness_stack()` (232 行)、`_activate_product_harness()` (53 行)、`_harness_*` 全局变量，以及 11 个未使用的 harness 模块文件（bootstrap.py, adapters/product_composition.py, product_profiles.py, subagent_registry.py, team.py, legacy_execution_migration.py, drivers/react*.py, workflow.py）。
- **root_run_identity 内联**：在 main.py (3 处) 和 companion/run_adapter.py (1 处) 内联 3 行 `root_run_identity` 实现，消除对 `deskpet.harness.kernel` 的导入依赖。
- **测试清理**：删除 tests/harness_simplification/ 目录及 ~20 个 harness 单元测试文件。pytest 结果：81 failed, 6466 passed（基线：79 failed, 6636 passed）— 170 个减少的 passed 测试对应已删除的 harness 单元测试，无新 ImportError，所有保留模块正常工作。
- **AC-8 保留必要契约**：保留 23 个 harness 文件（非 __init__.py 口径，2026-08-19 磁盘核实：contracts.py, ports.py, projector.py, context.py, profiles.py, skill_scope.py, kernel.py + 13 个引擎模块, adapters/venues.py, product_turn_open.py, drivers/react_boundary.py）。原因：companion/run_adapter.py → venues.py → kernel.py 依赖链，以及 6 个产品文件直接引用 harness 契约类型（HostExtensionRefV1, HostContext, PreparedRunContextV1 等）。
- **已知问题（文档化）**：companion/run_adapter.py 期望 `KernelRunClient` 但 main.py:9940 传入 SDK `RunClient`（接口不兼容），companion 后台功能当前不可用。修复需要接口适配层，超出本次清理范围。
- **文档更新**：见 [`plans/2026-08-17-sdk-cleanup/cleanup-complete.md`](../plans/2026-08-17-sdk-cleanup/cleanup-complete.md)

## 2026-08-16 里程碑：SDK v0.1.1 本地 candidate 已通过，Simple Harness 消费切换仍未完成

- **已完成**：独立 SDK `v0.1.0` Release 页面、exact wheel vendoring/lock/hash 校验，以及
  SDK 仓库 `main@0e38532` 的 CI/release/platform workflow 推送。
- **Slice A active hotfix candidate 已构建**：本地未推送提交 `f13a30a` 补齐 public atomic
  Workflow interrupt resolve/resume，生成 exact `0.1.1` wheel，SHA-256
  `48048ffbb827df15ae27efad67fa78d31302c9869381cb175d0d908c5f204e2f`；targeted 21 tests、
  `1181 passed, 2 expected skips`、artifact/exact-wheel/build 10 tests 与 reproducible build 均通过。
  旧 `371ceb98...10d5` wheel 的 `SHIPPABLE` receipt 已被取代，active bytes 的新机器 receipt 待补。
- **Slice B B1/B2 closed-ingress 基线已完成**：产品已从 vendored exact `0.1.1` wheel 安装；独立
  `execution-v1.sqlite3` 路径、version/SHA/origin fail-closed 校验、真实 SDK Database/UoW/Runtime
  lifecycle、失败清理/重试重读依赖、并发 start/close、dependency-owned resources 逆序 exactly-once close
  与唯一 immutable `sdk_runtime_ready` slot已落地。workflow binding 通过 post-DB
  `workflow_factory(database,uow)` 创建并核对相同 transaction owner；factory resources 在 partial failure/
  normal close 逆序 exactly-once 清理，factory 返回前资源由 `WorkflowFactoryResourceScope` 持有并在异常时
  自清理。`main.py` 仅注册空 readiness slot，尚未切换 text/voice/background ingress。
- **Slice B B3 产品侧可独立项已完成，整体 PENDING**：固定 real manifest SHA `891ae136...310bf`
  将 79 reachable identity 映射为 77 SDK Tools + 2 Workflow profiles；70 个实际 schema 变化（含 14 个
  specialized migration）old/new hash 已 checked-in，77 个 object schema 全部递归 closed，environment
  bounded key/value adapter、真实 `call_id`、write resource fence 与 typed `await_subagents` Host port 均已覆盖。
  21 个 static provider 以显式 registration 取代 import-time sink；65 个 static resolve 逐项无 config/
  registry/sink/旧 harness 副作用，且 new-first 后旧 main 仍恢复完整 79。14 个 specialized migration 已
  逐项经过真实 handler legacy/new shape 等价测试；六类真实 handler、legacy closed-ingress 79 parity、
  B2/B3 聚焦当前为 `27 passed`，相关 Tool/manifest 为 `47 passed`；全 sdk_adapters 的并行 Workflow
  import-purity 测试仍有 1 个越界失败。exact 0.1.1 SDK 尚无 public inventory sidecar
  来执行 effect/resource/parser/outcome/control/lifecycle metadata，故 B3 不得标 PASS；等待新 wheel 合同后接线。
- **未完成**：产品 B4+ authorization/workflow/conformance 与真实 ingress cutover 仍未闭合，生产入口继续装配
  `deskpet.harness`；SDK 0.1.0 conformance CLI 仍返回 `not_implemented`，SDK-S1..S7 为
  `PREPARED / NOT RUN`。
- **发布身份阻断**：本地 `v0.1.0` tag=`88e19eb`、远端 tag=`54b62f6`、Release
  `BUILD_INFO` commit=`88e19eb`，三方不一致；现有 release 不能关闭 SDK-AC-8，也不得覆写。
- **当前门**：把上述同一 wheel bytes 安装进当前 Simple Harness App，再做 Product Adapter/cutover，证明 SDK schema v1
  和旧 authority 退休，再做 exact-wheel 桌面真 E2E。当前状态不得写成 SDK 消费端 PASS。
- **仍需单独批准**：SDK push/tag/Release 与 macOS ARM64、Windows x64、Linux ARM64 的同 bytes
  远端 dispatch 尚未执行；不得用本机结果冒充三平台 release gate。
- **AC-6/7/8**：AC-6 被旧 ingress 与 `ModelPersonalWorkflowMatcher` 阻断；AC-7 被官方
  Workflow Host ports、SDK schema v1/reset/cutover proof 阻断；AC-8 被 conformance placeholder、
  release identity 漂移、旧 authority 与未运行的 E2E 阻断。
- **事实源**：[`SDK_EXTRACTION.md`](SDK_EXTRACTION.md)、
  [`acceptance.md`](../plans/2026-08-13-simple-harness-sdk/acceptance.md)、
  [`manual-test.md`](../testcase/2026-08-13-simple-harness-sdk/manual-test.md)。

## 2026-08-13 里程碑：历史 ToolSpec/catalog 恢复循环收口

- **根因**：历史 child 的 durable snapshot 保留精确 ToolSpec 指纹，但升级后当前进程 registry
  可能已没有对应 handler。重建 process pin 会抛 `snapshot references unavailable ToolSpecs`；
  precreated-child 接缝此前没有进入永久恢复不兼容通道，命令因而被 reconciliation 重复租用并刷栈。
- **修复语义**：缺失 snapshot spec 现在是 typed `tool_catalog_stale`。Kernel 在 Driver、Provider、
  effect 之前将该 child 送入统一 `DriverRuntime` 终态通道，durable failed 与 parent terminal signal
  只提交一次；后续 tick 观察终态直接返回。不会用当前同名工具替代历史指纹，也不会伪造旧 handler。
- **验证**：精确重启复现与 registry/lease/child/Kernel/reconciler 相邻组合 `122 passed`；完整
  Harness `921 passed / 4 xfailed / 0 failed`；authority 与 parity PASS；真实 SIGKILL/SQLite
  reliability gate 后端 `58 passed`、前端 `14 passed`，`HARNESS_RELIABILITY: PASS`。当前结构账本
  raw `167,852`、adjusted `167,345`、core `28,496`、Kernel `1,291`，仍在全部预算内。

## 2026-08-13 里程碑：Harness 历史 fixture 与结构债务全部收口

- **历史 source commit 恢复为耐久 Git 锚点**：从原始仓库恢复 R0/R4.5/R5.5/R6 四个冻结
  commit，并分别建立 `harness-r0-rollback`、`harness-r45-base`、`harness-r55-source`、
  `harness-r6-source` 标签。基线工具先验证标签精确指向冻结 commit，再跨历史迁移执行
  tree-to-tree 核算，不再错误要求旧 commit 必须是当前 HEAD 祖先；R6 cutover fixture 恢复可跑。
- **结构预算通过且 authority 不变**：`RunKernel` 的 terminal lifecycle、`AgentLoop` 的 tool
  context persistence、`ReActDriver` 的 failure recovery/capability retry 分别抽成 leaf mixin；不新增
  owner、数据库或事务通道。当前 `RunKernel=1,291` 行、`AgentLoop class=3,727` 行、
  `react.py=5,132` 行，满足 `<=1,300 / <=3,800 / <=5,200`。当前 construction 账本为 raw
  `167,852`、adjusted `167,345`、core `28,496`、unknown `0`、公开操作 6 项；历史 R4.5/R5.5
  final 数值只保留为旧里程碑，不冒充当前扩展后的预算。
- **parity 与 authority 闭环**：产品 turn mapping 重算后 `141/141`、unmapped `0`；冻结 source
  hash/capability 映射不变。authority enforce-target 为 DML `1`、UoW starters `57`、run map `1`、
  supervisor task `0`、presenter `1`、legacy `0`。旧 Live2D/CDP runner 已删除，原始测试证据仍只
  保存在 gitignored 本地目录，等待 NAS 归档。
- **当前验证**：历史/预算/parity `80 passed`，ReAct 聚焦 `114 passed`，Kernel 聚焦
  `118 passed`，AgentLoop 聚焦 `31 passed`；完整 Harness 为
  `921 passed / 4 xfailed / 0 failed`，上一里程碑的 14 个意外失败全部关闭。4 个 xfail 为明确
  登记的预期红用例。冷启动性能仍按用户决定跳过，Realtime 继续关闭，plan-test gate 继续暂停。

## 2026-08-13 里程碑：Execution 单一写入 authority 收口

- **第二个 `execution_runs` 写入者已删除**：原生 `WorkflowRunStore.claim()` 通过无 SQL 的
  checkpoint adapter，把通用 Run 的 running 同步加入 caller-owned SQLite 事务；adapter 缺失或
  写后异常时，Workflow lease/version 与 Execution status/version 会一起回滚。
- **authority gate 恢复可信**：局部变量承载的静态只读 SELECT 不再误报，动态 DML 仍 fail closed；
  bundled Git 同时支持 macOS/Linux `bin/git` 与 Windows `cmd/git.exe`。四个新增 transaction
  starter 已按阻断 root、有效执行预算配置/恢复、项目 workspace 重绑定逐项审核，保持强类型 API，
  不引入通用 opcode。
- **当前验证**：聚焦 `48 passed`；authority enforce-target PASS（DML `1`、UoW starters `57`、
  run map `1`、supervisor task `0`、presenter `1`、legacy `0`）；可靠性门禁后端 `58 passed`、
  前端 `14 passed`，`HARNESS_RELIABILITY: PASS`。扩大 Harness 为
  `905 passed / 14 failed / 4 xfailed`，较修复前减少 2 个失败。剩余 14 项是 2 项代码体积预算、
  1 项 parity fixture 漂移、10 项不可达历史 Git 锚点、1 项 R6 旧 cutover fixture，因此尚不宣称
  Harness 全量绿。本轮按用户决定跳过冷启动性能对照，Realtime 继续关闭。

## 2026-08-13 里程碑：复杂 Harness 输出契约、精确授权与 Kimi 真机闭环

- **一次性授权恢复为精确调用语义**：“允许一次”不再把 TaskGrant 带入后续 continuation，只有
  “本会话始终允许”才复用授权；每个人工 decision 具有独立 grant instance，连续相同
  `workflow_spawn` 不再发生 TaskGrant 身份碰撞。Auto 模式仍保留确定性幂等 identity。
- **权限 UI 以精确结算事实为准**：前端收到 `permission_response_applied`、同一 Run 的下一条
  durable decision，或精确匹配 `run_id + call_id` 的 `tool_result` 后才关闭弹窗并推进任务；工具
  结果 public frame 已补 stable `call_id`，decision prompt 的 `params.call_id` 兼容旧投影。过期/冲突
  响应保留弹窗并显示真实错误，ControlChannel 重连后重新拉取 pending decisions。
  “停止当前任务”仅发送 interrupt，收到 cancelled ACK 后按 root Run 清除当前及排队 decision；
  失败则保留弹窗与错误，不再出现停止已生效但权限卡残留的状态分裂。
- **child 自动收尾与 confirm-only 边界已修复**：child terminal signal 的后台 task 成功/异常都会
  唤醒 reconciler，异常带 traceback 进入结构化日志；`confirm-only` 在未来 decision identity 尚未
  生成时保持 wait，不再误判 deny。真实 `kimi-k3` root
  `88efdacaeb5a585794aa18231647b717` 在 child
  `child-09c8eb64c4334860aa7df03b470da714` 返回 `CHILD_DONE` 后无需用户追问，自动执行第二次
  `run_shell` 并输出 `ROOT_AUTO_CONTINUATION_PASS`。
- **权限卡延迟真机闭环**：最终 Run `35459a155afc57b49e7158c7dbea635d` 的 `run_shell_9`
  durable outcome 在批准后约 58 ms succeeded；0.5 秒 UI 快照已显示 `✓ ok`、权限卡已消失，而
  Kimi 最终 `PERMISSION_CARD_SETTLED_PASS` 约 7.2 秒后才提交，证明不再等待 provider 续答或
  迟到 ACK。
- **纯文本 durable child 能有限收敛**：objective 明确禁止工具、只需返回文本且无写入/测试义务时，
  单次 `end_turn` 即可完成；有文件、命令或测试要求时仍必须提供 effect/receipt。真机 `kimi-k3`
  root `4a976f0aa972525a899753cd7c630fec` 串行启动两个 child
  `child-a0a1262669335fafdcf779b61001c5ff`、`child-feea8f055f616ffa1920c22d85968034`，
  两次“允许一次”均 ACK 成功、两个 child 与 root 均 completed，UI 最终显示 `A_OK / B_OK` 并回到
  “空闲”；独立重复授权 root `84e99cc1d7c75f50b8421e37096d4107` 也 completed。
- **任务输出契约已进入生产链**：普通 durable child 启动前必须声明精确 `output_refs` 与可选
  `scratch_refs`；Host 冻结 workspace 与非可变内容摘要并绑定 launch ticket/start snapshot。
  prepared file target 与 Artifact 注册执行前拒绝越界；终审要求声明产物存在且不是 symlink、scratch
  全清、其余工作区摘要不变。新 Run fail closed，历史无契约 Run 只读兼容并明确告警。契约冻结、
  拒绝和终审日志均带稳定 `contract_id`。不透明 shell 的持久越界由终审捕获；同一 shell 内创建后
  删除的瞬时文件仍需未来 OS 级文件事件审计，当前不通过解析命令文本伪装保证。
- **Kimi 复杂真机复验 PASS**：第一次 Run 因 profile adapter 丢契约被主动停止并保留 FAIL 事实；
  修复后 root `1be3666915615161b1a9fac699aaf3e8`、child
  `child-f16d23bf03ac0c5f152609abc12f12df` 均 completed。child 读取 CSV，在声明 scratch 中生成并
  运行 Python，产出 `summary.json`/`REPORT.md`、校验总额 `422.00`、清理 scratch，并只注册两项
  Artifact；持久化审计 `passed=true / baseline_matches=true / missing_outputs=[] /
  retained_scratch=[]`，UI 有两张真实 ArtifactCard，根任务独立复核后 completed。空白新会话也
  已真机确认继承当前可见历史会话的 `kimi-k3`，不再回退到 transport 默认模型。
- **当前验证**：本轮新增聚焦后端 `65 passed`、前端权限弹窗/Hook `11 passed`；前端全量
  `556 passed`；TypeScript、
  Python 编译、execution build manifest、diff check 和 Tauri debug bundle build PASS。扩大检查发现并
  修复 `state.db` v27 marker 在补跑旧 v15/v16 migration 后误降到 26 的问题。扩展 Harness 目录
  加入五项硬退出测试后复跑为 `895 passed / 4 xfailed / 16 failed`；Execution→Memory 依赖方向
  失败已关闭，剩余
  16 项是既有 AgentLoop/ReAct 结构预算、旧 commit/fixture 可达性、Git fallback 与 parity 漂移，
  不宣称 Python Harness 全量基线绿色。
- **崩溃恢复门禁已建立**：`scripts/acceptance/harness_reliability_gate.py` 用单命令固定验证授权、
  effect handoff、child→parent、终态投影与重连回放；其中五项会在精确事务 hook 上对独立
  解释器发送真实 `SIGKILL`，重启后验证 SQLite 完整性及外部写/signal/delivery 唯一。当前后端
  `58 passed`、前端 `14 passed`，
  `HARNESS_RELIABILITY: PASS`。Reconciler 的 lane/worker 瞬时失败现在记录结构化关联字段并在默认
  1 秒后 one-shot 重试，关闭时取消 timer，不引入常驻 supervisor。Inspector 的 state.db
  keyset reader 已移回 Execution 自有只读边界，移除 Execution→Memory 越层依赖。
- **真实 Tauri supervisor E2E 已闭环**：macOS Workbench 内真实点击“允许一次”，在外部 shell
  effect 已写 START、尚未完成时 `SIGKILL` backend；Tauri 主进程不退出并约 2 秒拉起新 backend。
  原 Run `efb4a75a2d5759b99b492ed77748278c` 最终 completed，marker 只有一组 START/END，UI 无需刷新
  即显示恢复终态并回到“空闲”，死进程权限卡同步清除。恢复账本同时终结 effect 和 attempt 为
  `unknown / started_may_complete`，不重复执行不可判定的外部副作用；Session 重映射、终态 live
  delivery 和恢复竞态日志误报也已关闭。原始证据仅存本地 gitignored 目录，等待后续 NAS 归档。

## 2026-08-12 里程碑：Workbench last-mile、设置、恢复与托盘真测全部收口

- **ArtifactCard last-mile 的 root 与 child 自动桥接已实现**：生产 `execute_prepared` 生成
  artifact envelope，SessionDB 记录根 Run 的 `artifact_card` 投影；相对路径只在当前可信
  workspace 内解析。当前源码把 prepared receipt/artifact metadata 与 durable effect completion
  原子提交并在提交后 ack；child 完成事务会验证 artifacts/refs/SHA-256、发出
  `workflow.artifact_card` delivery event，并把 artifacts/refs 放进 parent terminal signal。Tauri
  白名单包含 `<user_data>/workspace/`。历史 macOS 根 Run 的 TextEdit/Finder 打开定位均 PASS；
  fresh-profile child `child-2ec4cceeec4df4bb19531561ab0e1e31` 已真机投影五张 ArtifactCard，
  DB artifact event 与 parent terminal signal 的 5 个 refs 和本地 SHA 精确一致，E2E 已闭环。
- **复杂任务交付链补强**：新增只读 `register_artifacts`，可把可信 workspace 内已经存在的文件
  直接登记为标准 artifact envelope（路径、大小、SHA-256），不创建旁路 JSON、不复制或改写
  原文件；未启用 ReceiptStore 时 artifact refs 仍进入 prepared execution metadata。跨平台
  basename 同时修正了 macOS/Linux 上 Windows 路径卡片标题显示完整路径的问题。
- **长参数与权限等待补强**：`write_file` 真实支持 `mode=append` 的 ≤3000 字符分块写入，
  `run_shell` schema 拒绝超长命令并引导文件正文走分块工具。权限请求只由 ChatView 订阅，按
  decision identity 去重并阻止已处理事件重放，消除同一 decision 多弹窗导致“停止后仍继续问”的
  前端根因；提交 live decision 后会把仍等待授权的 root projection/session 推进为 `running`，
  清除已处理 decision，且不覆盖 durable terminal。相邻前端回归 `37 passed`、TypeScript PASS。
  失败 Run 的停止 Host 不再依赖可用 Provider/workspace，避免重复 preflight 击穿 control
  WebSocket；durable Run 的进程退出恢复语义保持不变。
- **设置持久化已闭环**：Provider 删除确认、Agent 预算 request-id 关联、macOS 稳定数据目录
  bootstrap pointer、“当前/下次启动目录”诚实展示均落地；Provider、预算、目录、自启完成真 UI
  修改、完全重启、恢复和无残留验证。
- **运行期 backend 故障留在 Workbench 内**：新增运行时故障横幅；空 secret 与半开 WebSocket
  握手有界失败，ChatView/侧栏显示最差态，发送 fail closed、重试恢复。故障释放后 Kimi3
  HTTP 200 并精确回复 `S13 恢复成功`。
- **本轮验证**：Vitest `540 passed`、Rust `79 passed`、companion
  `647 passed / 10 skipped`、TypeScript、Vite build、`cargo check` 均绿；红钮、Cmd+Q 与托盘
  三条退出路径主进程/backend/8100 全清，重启几何一致。托盘三项文案与隐藏/显示由用户在当前
  macOS 打包版现场确认，退出终态和 1100×750 几何恢复由独立检查、启动日志与截图交叉验证。
- **本轮新增代码回归**：Harness/ToolRegistry/Artifact/last-mile/recovery 聚焦套件
  `256 passed / 2 skipped`，取消/恢复相邻套件另有 `40 passed / 4 xfailed`；前端全量
  `540 passed`，TypeScript、Vite production build 与当前源码 Tauri debug app 构建 PASS。
- **复杂任务可靠性与日志补强（当前源码，真机主链 PASS）**：工作区未选择只投影为 retryable tool
  outcome，不再击穿 control WebSocket；实时 socket/peer/final/context-usage 各自尽力投影，durable
  SessionDB 结果不被断线反向改写；final 后 trace 关闭记 OK。backend/stdlb 与 structlog 统一为
  单行 JSON，日志路径统一遵循 `DESKPET_USER_LOG_DIR`/portable/user-data 解析，20 MiB × 5 轮换；
  两类日志在 JSON 渲染前共用字段级与文本级脱敏，关联 ID/阶段/耗时继续保留，回归 `12 passed`；
  macOS/Linux diagnostic archive/reveal 已补平台原生命令。相关 workflow/effect/ReAct/
  RunPresenter/trace/observability 聚焦重跑 `231 passed`；但 Python 全量仍有 `79 failed`（同时
  `7473 passed / 48 skipped / 4 xfailed`），因此不宣称全量基线绿色。
- **fresh-profile 复杂任务首轮：核心执行 PASS、terminal FAIL、根因已修**：真实 Workbench UI
  使用 `kimi-k3` 选择隔离 workspace，control WebSocket 保持连接，child
  `child-1a1c22e7a629b36c0c04f262af4b7f53` 完成分块写入、17 项 unittest、CLI、自检与一次
  `register_artifacts`；独立复跑为 `17/17 PASS`，5 文件 SHA 和四项指标全部匹配。但 terminal
  将 `write_file` 的 `sha256=null` provisional envelope 误当最终 artifact，9/9 后报
  `workflow_engine:frontier_failure`，父 Run 又重复尝试委派。现已排除非登记的 provisional
  envelope，并为 `register_artifacts` 保持 refs/digest 精确相等；相邻回归 `102 passed`。同时
  真机再次确认 UI 后台执行时长期显示“等待授权”、进度停在 5/9→6/9；这些问题在第二轮得到
  真机/账本闭环，详下一项。
- **fresh-profile 复杂任务第二轮：child/产物 PASS，root 收敛 FAIL；第三轮已修复闭环**：root `caf7d550a7705da89cba6b731b9d1c4c`、child
  `child-2ec4cceeec4df4bb19531561ab0e1e31` 使用 `kimi-k3`，原生选择隔离目录后只确认一次
  workflow 权限；约 1.2 秒恢复“工具执行中”，最终 9/9 completed、五张 ArtifactCard、空闲。
  真实生成 5 文件，独立 `7/7 unittest PASS`、CLI/JSON 为 `count=10/sum=55`；执行 child 只有一个
  `register_artifacts`，artifact event 与该 child 已投递 parent terminal signal 均携带同一 5 个 SHA。
  同一 attempt 内重复 5/9→6/9 的私有工具循环已改为按公开 node/attempt 去重；路由提交早期失败
  的日志不再以未赋值 frontier 覆盖原异常，进度/engine/log 相邻回归 `76 passed`。但该 root 在首个
  child 成功后仍因 spawn scoped evidence UNKNOWN 误触发 verify gate，并追加两个失败验证 child，故
  root 收敛不能判 PASS。当前源码改为按 spawn ticket/command/terminal signal/audit 的精确 lineage
  投影 child committed receipts；第三轮 fresh Run `f0a514f061cb56cebaa498a4a1447b24` 只有
  `child-0abd7fb98c3faf61504a7f96085c493f`，三次真实 shell 分别枚举、容斥与显式交叉核验，结果
  `count=467/sum=234168/MATCH=True`，root/child completed，零 verify nudge、零第二次 spawn。
- **Harness 终态观察与授权日志补强**：真机发现 root 已 completed 时，语义 phase 的历史 running
  状态仍让观察面显示“正在委派/4/5”。当前前端以 aggregate terminal 收束未结 phase/substep/tool，
  运行图终态显示“结果/记录已结束”，不再标当前进行中；backend 在权限/澄清/目录/外部等待事件
  发出时写 `harness_blocking_ui_event_emitted`，只记录 event/session/run/request/tool 关联字段，不记录
  敏感 params。后端聚焦 `239 passed`、前端聚焦 `69 passed`、TypeScript 与 Vite build PASS。
- **workflow_spawn 拒绝语义已真机闭环**：旧 root `074bf4d51e545627ae188d623cafde89`
  在 UI 点击拒绝后虽然 decision 已是 `denied`，仍错误发出 delegate 并创建 child，根因是
  `control_delegate` 的 deny outcome 结算后仍无条件 `_prepare_control_event()`。当前 Kernel 覆盖
  `deny/denied` 归一化，ReAct Driver 对已结算 control outcome fail closed；修复后 root
  `996390c79f1b5c03967f7f42dc408f29` 的 decision=`denied`、child=0、ticket=0、无
  `child_accepted`，最终界面明确显示 `authorization_denied` 且未创建 durable child。扩大后端回归
  `328 passed`。相邻的历史取消投影缺口也已关闭：前端用父 root 的 terminal Session projection
  收束 child workflow 卡并合并同源 public trace；重启后会话 `5ec83cb7-51d8-46a8-a7ed-c13de60fcf59`
  只保留一张“已取消 / 6/9 / 67%”卡，缺少可靠计时时显示“耗时未记录”，不再显示“进行中”或
  重复终态卡。前端相关回归 `57 passed`，TypeScript 与 debug bundle build PASS。
- **失败 child 收敛与终态展示补强**：ReAct Driver 只对 durable terminal=`failed` 的
  `workflow_spawn` child 记录有界脱敏 objective 签名；语义相近的再次委派在 launch ticket 前
  阻断，首次反馈模型、第二次以 `delegate_convergence_exhausted` 停止，实质不同的 child 放行。
  前端同时修正 Root-only Session projection 的优先级：child 消息自身终态优先，Root 终态只收束
  陈旧 running/waiting 卡。真实 Session `782f283d-0ac0-4016-b979-e6f79e7582f6` 重启后，completed
  child 与 `workflow_node:llm_proposal:provider_failure` failed child 分别显示“已完成”和
  “失败 / 5/9”。后端扩大回归 `332 passed`；前端聚焦 `44 passed`、TypeScript 与 debug bundle
  build PASS。扩大套件曾暴露 replay 测试在 UOW close 前未 drain parent child-signal owner；按生产
  shutdown 顺序补齐后，同一 `332` 项退出零 pending-task/closed-database 告警。
- **macOS 项目目录确认文案已修复**：ProjectDirectoryCard 不再硬编码反斜杠，POSIX/macOS 用
  `/`、Windows 用 `\`，并覆盖根目录。真实 Open sheet 选择 Desktop 后显示
  `/Users/denny/Desktop/harness-path-test`，未创建该目录；证据保存在隔离测试 profile。
- **当前源码 macOS 真 UI 复验**：历史 Kimi Session 曾因 catalog 不再包含该绑定而在 provider
  preflight 失败；旧取消路径会再次解析缺失模型并使聊天断线。修复后真点击“停止”，界面从
  “工具执行中”回到“空闲”，左下角保持“已连接”，后端没有
  `SessionProviderUnavailable`/ASGI 异常。目录恢复后，当前源码以 `kimi-k3` 完成 Run
  `04a477a3fbbb5e3eb2045e6b11006b55`：真实 UI 触发 `write_file` → 单次权限确认 →
  `register_artifacts`，两张 ArtifactCard 均能用 TextEdit 打开并在 Finder 定位；文件为 14 B，
  SHA-256 `c96a2f4aec81c7e0d4ddaceb068ecaf030477e1c273bd4ab70ca1fe9197c4706`。
- **Provider 实时模型目录持久化**：`models_list` 成功取得 `/models` 后，把去重目录原子写回
  对应 Provider 的 `config.toml` 缓存；写入前复核 incarnation/revision/base URL，相同目录不
  重写，且缓存刷新不改变 Provider identity。显式默认模型若不在 live 目录则拒绝持久化而不
  静默换模。Provider/IPC/Session authority 聚焦回归 `88 passed`；r11 隔离测试配置已从实时
  HTTP 200 目录写入 155 个模型并确认包含 `kimi-k3`，测试 Provider 默认模型单独设为
  `kimi-k3`。
- **运行时模型身份不再泄漏占位符**：平台级 Persona 继续使用稳定占位值维持 prompt cache，
  但每个任务另注入受保护的 task-scoped runtime model fragment；TurnPreparer 使用已解析的
  Session Provider，而非全局默认 Provider。重启当前源码后，真实 UI 询问实际模型精确 ID，
  界面回复 `kimi-k3`；同一次 Run `97f01117fd50506fbe10da0444577fbd` 的后台日志确认
  `model=kimi-k3` 且 `/v1/chat/completions` HTTP 200。
- **Kimi K3 历史复杂工程烟测核心 PASS；当前 fresh E2E 经第三轮闭环**：root
  `cb74467c06f35eaebdf7bfe316b9fff8` 与 child
  `child-4dad77bbfafec0b8428852dde382d9eb` 完成项目目录确认、durable workflow、分块写入、真实
  unittest/CLI 和结果自检；独立复跑 `9/9 OK`，四个关键指标精确匹配。child 内一次
  `register_artifacts` 的 outcome 含 5 个正确文件/哈希，但旧 child `artifact_refs_json=[]`；根 Run
  `1bf5014d7ee55573ba2a797c391032ef` 补偿登记后 5 张 ArtifactCard 正常。当前源码已修 child
  metadata/effect/terminal/delivery 原子桥接，以及目录前置和关闭 WebSocket 的恢复边界；当前
  第二轮已确认 child/Artifact 主链，但 root 误派验证 child；第三轮已用精确 child receipt 回传确认
  单 child 一次收敛。旧 r15 profile 还观察到
  恢复历史 Run 时因 ToolSpec catalog 漂移反复抛
  `ToolCatalogMutationError`，已作为独立 followup，不与新 profile 的能力验证混为一谈。
- **剩余边界**：产品/testcase 层的 18 个场景已收口；TC-WB-12 步骤 7 已由用户明确移除，禁止
  再次启动带 Live2D 的历史基线。plan-test gate 仍暂停，不宣称机器门 READY。
- 本轮继续遵守用户要求：暂不使用 plan-test skill，不写 gate ledger，不宣称机器门 READY。

## 2026-08-09 里程碑：登录方式改为手动 provider，relay 与 default 会话双双下线

- **托管账号登录（relay）整套移除**。前端删 18 个 relay 源文件（adapter/modal/edition/
  错误文案/provider 桥接与注册/账号面板/bindings），后端删 `llm/relay_provider_ops.py`、
  两条 `settings_providers_*` relay 消息、`config.relay_managed_provider`、
  `.env.relay*`。产品只剩一条路径：用户在设置页手填 **baseUrl + apiKey**，
  key 仍只落 OS keychain（`config.toml` 存 `api_key_ref`，永不明文）。
- **身份改为纯本地 profile**。`RegistryRelayAuthSnapshotProvider`（读 keychain relay
  token → 调 `/v1/me`）换成零 I/O 的 `LocalAuthSnapshotProvider`。**WBUI-DEF-AUTH-01
  由此结构性消失**——没有远端 token，就没有过期与刷新，也就没有"未绑定 profile
  永久卡在『正在恢复身份…』"。
- **保留会话 `default` 移除**。原先它是"应用的兜底会话"，代价是三处生命周期特判
  （clear 只清内容不退役 / 墓碑化自愈 / 纪元接管自愈）。现在会话一律用户新建、
  生命周期同构：`clear` 对所有会话统一退役，属主一经绑定不可改绑。
  **WBUI-DEF-COMP-01 的修复随载体一并删除**——其成因（relay↔local 身份迁移推进
  `binding_epoch`）已不存在：本地 `profile_id` 恒为 `legacy_local_profile`，
  `changed_owner` 恒 False，纪元不再前进。
- **空态入口**：无会话时在输入框直接发消息，由 InputBar 发一条
  `chat_v2 { session_id: "", new_session: true }`，后端派 uuid 新会话再投递并回推
  `session_switched` + `chat_v2_user_echo`。**不复活任何固定 sid**。
- 验证：`npm run typecheck`（`tsc -b`，见下条）+ `vitest 551 passed`；backend pytest
  分块跑并**逐条对比 stash 前 baseline**，失败集合完全一致、零新增失败。
- **顺带纠正一个长期假绿灯**：`tauri-app/tsconfig.json` 是 solution 式配置
  （`"files": []` + project references），裸跑 `npx tsc --noEmit` **一个文件都不检查、
  永远 exit 0**。已加 `npm run typecheck`（`tsc -b --noEmit`）为唯一口径。
  该发现使 r9 的 S12 tsc 证据作废，详见
  [r9 审计遗留](../plans/2026-08-09-workbench-ui-r9-audit-followup.md) §8。

## 2026-08-04 里程碑：桌面游戏操作与小窗口 Context 稳定性修复

- 修复 Session `2e69be7e-0b16-4bfb-a774-d61e588c5ec3` 暴露的双故障：桌面输入工具不再以
  `concurrency_safe` 并发进入全局非阻塞键盘锁；screen/window 工具共享全局桌面 input lane，
  `EffectBatchExecutor` 以引用计数资源锁跨工具批次、跨 Run 串行排他效果。
- `window_key` 新增单事务短序列（最多 12 步/12 秒），支持按住、组合键、按键后暂停和独立纯暂停
  步骤，完整预校验、失败即停并保证释放按键；旧单键调用保持兼容。
- 截图结果从普通 tool JSON 中移除 base64，只把有界元数据留在文本并以多模态 `image_url`
  附件交给模型，10 MiB 上限和独立 attachment budget 防止截图字符串挤爆文本 Context。
- AgentLoop 压缩器按 Run 冻结的模型窗口派生；provider-chain lossless 预检仍超限时，普通 Run
  会先执行一次目标保真压缩再出站，而 coverage/no-compressor 路径继续 fail closed。
- 自动化首次联合回归 `215 passed`；补齐纯暂停步骤后聚焦 `43 passed`、最终联合复测
  `219 passed`。Windows 真机首次 Run `bd8f676ef4215e558b2d911bfcf76735` 完成 Godot 启动、单次
  D→W 序列和前后截图，32K Kimi 在 70% 线触发压缩后继续完成；独立最终 Run
  `9dd6243c306753b7894bc7ca1c3646b8` 仅执行一次 A→暂停→S `window_key`，随后截图并完成，
  无 `window keyboard input is busy` 或 `provider_context_budget_exceeded`。

## 2026-08-03 里程碑：Session 模型一致性与语义运行视图

- Provider/Session authority 升级为 incarnation/config revision/binding epoch 的 durable CAS；显式
  stale/disabled/model-missing 绑定 fail closed，未绑定 Session 才允许 global chain。启动 migration、
  Registry load 与 reconcile 完成前，产品入口和后台 router 都不开放。
- 主 Agent 与会话附属 LLM 共用类型化 `ProviderWorkloadContext` 和完整 callsite inventory；跨
  Session 维护使用显式 `BackgroundModelPolicy`。附属 breaker 按 credential/account/model/workload/
  endpoint 隔离，后台错误不再结算 Root。仅 DEV 可加载的 consume-once fault script 已接入真实
  provider 边界，生产发现注入 env 会拒载。
- Context Usage v24 使用 immutable sample/materialized state；measured、compacted、binding-only
  来源明确。冷恢复没有样本时显示 Session 绑定模型和“尚无用量”，不再拿全局默认模型生成假 0 值。
- ReAct 的真实 `context_compacted` 事件现已穿过 Collaborator、Driver、Runtime 和 canonical
  presenter 进入同一 usage authority；Run 入口把精确 measured sample ID 冻结到 durable payload，
  producer 原样携带，presenter 只做 Session + sample ID 精确读取，不再用时间猜测并发 lineage。它
  还把每次压缩的确定性结果 sample ID 写回后续 React 工具 boundary，重启后从最新 lineage
  继续。它保留 source/sample lineage 与实际 Session 模型；不再只在旧
  `AgentLoop` 内可见。已有项目卡片的原生选择结果现在直接作为项目根目录，前端不再显示无意义的
  子文件夹名输入，也不会把建议名重复拼接成 `root\\existing-project`。
- workflow/state ledger 之上新增 schema v3 public read model：一致 read cut、完整 keyset、签名
  cursor、total/completeness、default-deny tool/provider projection。前端运行图、消息 activity 与
  durable steps 共用一个 Session/root snapshot store，工具输入/结果分层折叠，未知 schema 安全降级。
- 根结果和阶段由纯 reducer 重建；blocked 只认 `RunBlockSignalV1`，child 失败后只有完整
  terminal→FailureReport/failure-set→replacement Attempt→root terminal 链才显示“已接管并完成”。
  A2 只读复跑真实 Godot Root 得到 366 facts、6 phases、29 个唯一逻辑工具（23 shell）、
  `completed_with_recovery`、`projection_complete=true`；child 原始 failed 保留。
- DEV provider fault 在 durable claim 后、物理 transport 前注入；Session auxiliary、child main、
  detached maintenance 分别使用 Root、child correlation、request correlation，禁止用一个伪 Root
  身份覆盖三类 workload。工具在 StartedAck 后被停止时，物理 completion 仍被观察；effect 先持久化
  为 `unknown/started_may_complete`，terminal Reconciler 只做晚到隔离结算并绝不恢复 Driver；ready
  evidence 直到 durable terminal 决策后才 acknowledge，running/CAS loser 可重试。
- 最终自动化证据：跨会话 full-surface smoke 后端 `29 passed`、前端 `116 passed`；child
  provider 生产接线、故障注入、dispatch 与 Root recovery 联测 `66 passed`；受影响后端、前端
  全量回归及 TypeScript/Vite production build 均通过。当前增量的 canonical compaction 与已有
  目录合同聚焦回归另计后端 `7 passed`、前端 `21 passed`，并已包含在最终重跑中。
- S-SRV-1～S-SRV-5 Windows 真机矩阵已全部执行并 PASS：覆盖 Kimi 冷恢复、≥10 轮长上下文、
  child 失败后 Root 接管、后台 401/402 隔离、双 Session 并发停止与晚到结果隔离。正式 S-SRV-3
  Root 为 `573cf15ecffb561493b2590bcb785368`，child 保持 failed，Root 以
  `completed_with_recovery` 收口；截图、DB ledger 与 Gate 记录位于对应 plan 的 final5 run-dir。
  当前有效 Root 分别为 S-SRV-1 `32f9e2fe01f5568d9ca56ef0b17b4ada`、S-SRV-2
  `04999765753a5342aa9f7b4619b0fd38`、S-SRV-4-401
  `e6acd3abbe85519ca1f6736329bb3aa3`、S-SRV-4-402
  `11dac2d13ae954ddae8c3cde9b3658cf`；S-SRV-5 重启后 A 仍 cancelled、B 仍 completed，晚到结果
  未重新进入消息流。

## 2026-08-03 里程碑：多步骤任务模型绑定与终态收口修复

- 顶层 Run 的 Host Context 现在冻结有序的 `provider_id + model_id`，durable child 继承精确
  模型绑定，不再从可变 Registry 默认值重新选择模型；修复 Session 已选 `kimi-k3`、child 却
  静默落到 `sf-glm-5.2` 并因余额不足失败的问题。历史 provider-only Run 保留兼容恢复路径。
- child terminal 事件无条件唤醒 reconciler，attached 终态信号可立即投递并恢复父 Run；终态后
  的迟到 progress 幂等忽略。Provider 402/余额与 Relay 凭据故障使用稳定公开错误引用，前端显示
  可理解的中文说明，同时保留技术审计事实。
- durable 完成门禁识别生产工具 outcome 的 `state=success`，并先剔除“禁止修改 / without
  editing”等否定动作，再判断真正的写入和测试义务；正确的只读最终回答不会因关键词误判循环到
  proposal budget 或模型上下文上限。
- 控制工具进入失败重规划时，loop guard 已预填失败 outcome 的 batch 不再继续启动 child；历史
  数据若同时保留 pending delegate 与不同 outcome，迟到 child terminal 会保留首个权威 outcome、
  原子确认 signal 后继续恢复，不再以 `outcome already recorded with different value` 阻断后端
  lifespan。effect-ready 定向恢复也不再等待或取消已在运行的 provider owner，owner 结束后由
  一次性回调重新唤醒恢复器，避免正常的长模型调用被 5 秒 reconciliation budget 误判为启动
  失败。ReAct/失败重规划/Harness 启动/Kernel 组合回归 `185 passed`。
- 聚焦自动化：Harness/Workflow 后端相关套件与前端 Sessions Store、TypeScript、Vite build
  均通过。Windows 真机 Session `6db8e119-d960-4bcd-b010-bf738b0b66c8` 使用 Kimi 运行同一条
  `workflow.durable_task` 只读请求：root `2d28545be2db599bbbda04040fb08db3`、child
  `child-5cc4942f4d8f89735ff7be9da4f66289` 均为 `completed`；两者冻结绑定和全部 11 次模型调用
  均为 `kimi-k3`，terminal signal `attempts=1` 且已投递，UI 显示完整步骤和最终标题报告。
- 新建本地项目的目录确认增加 Host 前置门禁：可信用户消息、child objective 或 `plan_steps`
  命中新项目创建时，`workflow.durable_task` 只能在 `user_path` 绑定后 spawn；用户在聊天里写的
  绝对路径仍须原生卡片确认。目录选择和外部等待不再下放到不能 durable suspend 的 native
  child capability snapshot，修复 child 直接执行后返回 `external_wait_must_be_staged_by_harness`。
- 项目目录卡片新增 `use_existing` 模式：新空目录仍 fail closed 拒绝非空目标，明确继续半成品时
  可确认已有目录。`workflow.db` schema v28 把安全的一次性换根扩展为
  `task_default | existing → user_path`，仍要求无 child、无写 effect 并递增 binding version。
- 聚焦自动化通过：目录/child/Harness/schema 后端 `167 passed`；前端全量 Vitest 与 TypeScript
  编译通过。
- capability fingerprint 不再包含会随项目目录变化的运行时 scope 逻辑；新增独立的
  `SqliteCurrentExecutionScopeAuthority`，冻结 catalog/身份但从 durable Run 读取当前 workspace。
  Companion identity bind 成功后会立即唤醒 Harness 恢复，修复启动扫描先于身份就绪后永久沉睡。
- Provider 已明确返回的 `408 / 425 / 429 / 5xx` 现在结算为可重试响应，并以新 invocation identity
  仅重试一次；不再把 Kimi `engine_overloaded_error` 误记为 handoff unknown，再在 native workflow
  节点重放时触发 `invocation id already names a different dispatch`。额度/余额类 `402` 不自动重试。
- Windows 真机 Session `2e69be7e-0b16-4bfb-a774-d61e588c5ec3` 使用冻结的 `kimi-k3` 完成
  `F:\projects\jurassic-park-escape` Godot Demo：子 workflow 遇到一次 Kimi 429 后父 ReAct 接管，
  生成完整项目并由下载的 Godot 4.2.2 找出、修复 `pixel_art_items.gd` 编译错误；最终 root
  `a3a63c99fca45b0ca91d1192d69bb378` 为 completed。编辑器实际打开 `Main.tscn`，DEBUG 窗口显示
  可移动玩家、生命值/目标/背包 HUD 和程序化公园地图。聚焦后端 `217 passed`、前端全量
  `912 passed`，TypeScript 编译通过。

## 2026-08-02 里程碑：Harness 运行图与项目目录上下文收口

- Root 的 15 分钟限制从“消息收到后的整轮墙钟”改为 durable 有效执行预算。模型、工具和
  Driver 实际工作才累计；目录选择、授权、澄清、外部操作和 child 等待进入 `waiting` 后暂停，
  恢复同一 Run 时继续使用剩余预算。WebSocket presentation 不再拥有取消 Run 的权力。
- `workflow.db` schema v27 新增 `execution_run_active_budgets` 及状态迁移 trigger；到期由
  HarnessReconciler 领取可重放 expired claim 并以
  `active_execution_budget_exhausted` 交 RunKernel 收口。设置页同步改名为“Agent 有效执行预算”。
- 自动化覆盖“执行 4 秒、等待 1 小时、恢复后只剩 6 秒”、到期 claim 崩溃重放、schema 迁移、
  presentation 不再墙钟取消；相关跨层后端回归 `426 passed, 2 skipped`。
- Windows 真机使用 Session `18a521e9-946a-4e56-bd63-445dc8e75097`、Run
  `610daa0f7e7a5b6b9ef095b5ba938bb9` 验证：临时设为 1 分钟预算后触发 Godot 项目目录卡片，
  保持不选择超过 2 分钟，UI 仍显示“等待中 / 需要你确认”；durable ledger 为 `paused`，仅累计
  `0.084s`。验收后预算恢复 15 分钟，测试 Run 经启动 Reconciler 收敛为 `cancelled`，未创建项目文件。

- 左侧 Harness 默认视图改为真实运行图：步骤按箭头串联，当前节点高亮，工具节点作为缩进
  分支；图内滚动跟随当前节点，外层不再自动滚到底。图下方保留唯一一套可折叠步骤详情，点击
  图节点会直接展开对应输入和结果。相邻准备动作在图上合并成用户阶段，详情则标明原始记录数；
  原始工具名和 `settled` 等技术状态不再出现在默认图中。六层账本、ReAct 和原始 JSON 继续默认收在
  “技术记录”。
- 已取消任务明确提示“不会继续执行”。真实 Windows 消息窗口使用现有 7 步 Godot Run 验证：
  图可见、工具节点可选中，展开后显示 `project_directory_select` 输入与归一化结果。

- `project_directory_request` 不再保存为消息窗口级单例，改为按 `session_id` 分区；渲染同时
  校验当前 Session、当前选中 Run 和 `waiting` projection，旧项目请求不会泄漏到新话题。
- Run 恢复、切换、完成、失败或取消后，目录操作卡片从消息流移除；历史仍保留 Agent 说明和
  普通工具记录。Windows 实测新空 Session 无卡片，切回已取消的 Godot Session 也无卡片。
- 用户确认的 `user_path` 持久化在 `workflow.db/execution_task_work_contexts`，并成为该 Session
  的当前项目上下文；后续顶层 Run 自动继承到 Host、Agent prompt 和工具上下文，后来的空
  workspace Run 不会覆盖它，其他 Session 隔离。创建另一个新项目仍要求模型重新调目录工具。
- 新增 Session/Run 可见性、运行图交互和身份状态重放回归测试；前端全量
  `101 files / 906 tests passed`，
  TypeScript 与 Vite production build PASS；项目上下文继承相关后端测试 `149 passed`。
- 修复运行图热更新后消息输入框可能永久停在“正在恢复身份…”：`controlWs`
  缓存并向新订阅者重放最近 Companion 身份状态，不再依赖组件恰好收到一次性广播。
  聚焦回归 `41 passed`、TypeScript PASS；真实 Windows 窗口确认已登录账号，刷新重连后输入
  “回复一个：收到”，Run 从理解请求走到任务完成，消息区实际收到“收到 🐾”并回到空闲。
- 修复 Session 模型选择在“新话题”或应用重启后回退到默认 GLM：新 Session 在启动 Run 前
  原子继承来源 Session 的 Provider/模型/参数绑定；历史切换与冷启动增加独立绑定 hydration，
  标题栏与实际出站模型使用同一持久化事实源。模型弹窗新增按 id/名称即时筛选。
- `kimi-k3` 的中转站接口只接受 `temperature=1`；OpenAI-compatible 网络边界现在统一规范化
  该模型的温度，并在 at-most-once 调用失败时保留 Relay HTTP 错误正文用于诊断。聚焦回归：
  后端 `63 passed, 2 skipped`，前端 `33 passed`，TypeScript PASS。真实 Windows Run 使用
  Session `074623b0-d91b-495e-9b67-b91c04859ee1`、Run
  `a60eca6bffd0551e801f2de9abf5ea3d`；持久化 invocation 为
  `relay-cloud / kimi-k3 / completed`，消息区收到 `KIMI3_RUN_OK`，重启后仍显示 `kimi-k3`。

## 2026-07-30 里程碑：多步骤任务启动状态与公开进度修复

- 原生 Workflow child 首次领取 lease 与恢复接管时，均在同一 SQLite 事务中把通用
  `execution_runs` 原子同步为 `running`，只写一次 `started_at`，重复领取保持幂等；
  修复原生 `workflow_runs` 已运行、Harness 却长期显示 `queued`/未启动的问题。
- Kernel 预创建 child attach 后立即唤醒 Workflow dispatcher；周期扫描只作安全兜底。
  进度事件写入后也立即唤醒 Harness delivery reconciler，不再等待下一轮后台扫描。
- `durable_task@v1` 进入公开进度映射，消息页可显示九个稳定阶段；循环的
  `llm_proposal/tool_execution` 按 task identity 的 SHA-256 短摘要区分事件，避免后续迭代
  被幂等键误去重，同时不暴露原始 task id 或隐藏 reasoning；公开标签稳定为“多步骤任务”。
- 启动恢复兼容精确的 pre-runtime-authority capability fingerprint 并 CAS 迁移；已有
  cancel/continuation intent 拥有的 Run 不再导致整套 Harness 初始化失败。
- 取消恢复补齐：precreated Workflow 的通用 Run 已是 `cancel_requested` 时，恢复幂等
  收敛原生 Workflow；取消中/已取消父 Run 的迟到 attached child signal 被确认但不会再
  唤醒 Driver，正常终态父 Run 的同类回执仍保留为一致性错误。
- 右侧消息流只展示产生工具调用的 Provider 轮次中模型明确返回的公开 `content`，样式与
  普通助手消息一致；工具调用和结果继续使用现有工具信息框。`Agent 工作记录`、步骤编号、
  输入/结果字段、RunKernel/Canonical 状态和原始 JSON 不再进入默认消息流，完整审计事实
  只保留在左侧 Harness Inspector。没有工具调用的最终模型文本继续走原有 assistant 消息，
  避免重复；历史 `<think>`/reasoning 内容仍会过滤。无阶段详情的旧 Workflow 进度不显示
  重型卡片，只输出任务名、状态、当前步骤和简短进度，并按匹配 child Run 的 durable 终态
  从“进行中”更正为“已取消”并停止计时。
- 右侧消息流的公开执行记录现在直接读取持久化账本，而非前端临时步骤卡：
  `execution_provider_invocation_outcomes` 提供 root/child 的公开 `content`，
  `workflow_effects` 提供 child 实际执行的工具输入和 outcome。公开说明按时间显示为普通
  assistant 消息；工具输入和结果统一使用现有工具信息框，结果默认收起并可点击展开。
  Provider outcome 新写入会优先保存结构化 `model_dump`，旧 `ChatResponse repr` 继续兼容；
  `reasoning_content` 与 `<think>` 块不进入公开消息流。
- 多步骤任务的右侧消息流现在把 child checkpoint 的 `todos / active_step_id /
  proposal_state.messages` 与 `workflow_effects` 按稳定 call id 关联：顶部显示总步骤和当前
  步骤，每一步可独立展开，收纳该步公开说明与工具；工具行压缩为一行，二次展开才显示输入
  和结果。新 `workflow_spawn` 支持 2～8 个简短 `plan_steps`；旧任务只有一个总 objective
  时，按已持久化的公开 assistant/tool call 序列恢复可读步骤，不展示或推断隐藏思维。
- 新建本地项目的位置选择改为 Agent 工具交互，而非 Session/输入框设置：
  `project_directory_select` 在任何项目写入和 child workflow 前暂停同一 Attempt，消息流显示
  轻量卡片，让用户选择父目录、修改子文件夹名并预览最终路径。后端拒绝非空目标、路径穿越、
  Windows 保留名和非法字符；durable workspace 只允许在无 child、无写 effect 时执行一次
  `task_default → user_path` CAS 迁移，随后原 Run 恢复，后续文件工具和 child 继承新目录。
  输入框不再显示常驻“项目文件夹”，Session 也不会记住或自动套用上一次目录。pending request
  按 Session 隔离，并只在当前选中 Run 仍为 `waiting` 时显示；切换或离开等待态后卡片直接移除。
- 左侧 Harness Inspector 默认视图改为“Agent 执行过程”：首屏只显示当前状态、用户需要
  做什么，以及“理解请求 → 开始任务 → 决定使用工具 → 使用工具 → 整理回复 → 任务完成”的
  轻量时间线。每一步点击后才显示可读输入和结果；工具输入优先显示公开的真实参数，不再显示
  `raw_arguments_ref`。六层生产链、ReAct、Run/Provider/Canonical、原始 JSON 与错误码完整
  保留在默认折叠的“技术记录”中。
- Workflow stage、summary progress 与 artifact 消息现在端到端保留 canonical
  `root_run_id`；user、assistant 与工具消息始终显示完整 Session 历史，只有 workflow
  progress/stage 跟随当前选中 Run，缺少 Run 身份的旧 workflow 记录 fail closed。停止父
  Run 后，child 的 durable `cancelled` 终态会覆盖旧的 `running/waiting` 读模型并停止
  计时，后续新 Run 不再继承上一任务的部分进度。
- 自动化证据：相关 Workflow/Harness 后端组合 `174 passed`；消息流、WebSocket 与
  Harness 前端聚焦 `108 passed`，TypeScript + Vite production build PASS。唯一主实例
  PID `13260`，backend `8100`、Vite `5173` 与 `/health=200`；`workflow.db` 中
  `execution_runs` 无非终态记录，启动后没有新增 Run provider invocation（独立记忆
  reflection 仍可按自身计划运行）。真机 Session
  `dfcbaac7-c330-4e81-af63-b5abb10055f5` 真机确认右侧为普通 Agent 说明、原有
  `read_file` 工具信息行和普通最终回复；左侧默认显示 8 步轻量时间线，`read_file` 步骤可
  展开真实 `path` 输入，技术记录保持折叠。Inspector 改造聚焦 `39 passed`，production
  build PASS。本轮停止语义补充验证为后端相关组合 `76 passed`、前端全量
  `100 files / 889 tests passed`、TypeScript 与 Relay production build PASS。真实 Session
  `5574fd84-e2d5-4207-a5c5-47e79f19ded0` 中，父 Run
  `3f3bf02322745544ab3d41d15e9f1b21` 启动 `durable_task` child 后点击停止，父子均持久化为
  `cancelled`，界面保持“已取消 / 空闲”且 12 秒内步骤数、更新时间和 `6/9` 进度不再变化；
  新 Run 启动时旧 child 卡片和进度均不可见，数据库非终态 execution/workflow Run 均为 0。
  Session 历史筛选回归补测为前端全量 `100 files / 890 tests passed`、TypeScript 与 Relay
  production build PASS；同一真实 Session 切换 Run 时，5 条历史请求和 5 条取消回复始终
  可见，只有所选 Run 自己的 `6/9` Workflow 进度出现。本轮持久化公开 trace 修复后，
  相关后端 `83 passed`，前端全量 `100 files / 892 tests passed`，TypeScript 与 Relay
  production build PASS。真实 Session `5574fd84-e2d5-4207-a5c5-47e79f19ded0` 的 Godot
  Run 回放出 18 次 Provider 记录和 13 个 child workflow tool effect；Windows 消息窗口
  实际显示普通 Agent 说明、`file_write` 等工具输入框及默认折叠的结果，点击结果后可看到
  `bytes_written=8381`、`path=scripts/GameWorld.gd` 的真实 outcome。本轮步骤绑定补充验证为
  后端相关 `57 passed`、前端全量、最终聚焦 `22 passed` 与 TypeScript/Vite production build PASS；同一 Godot
  历史 Run 的 checkpoint 读取到 11 条公开操作说明和 13 个 child tool effect，Windows
  消息窗口实际恢复出 11 个可折叠操作步骤，每步显示工具数量，整体保持轻量消息样式；
  终态为取消/失败且所有已观察操作都成功时，额外显示未完成的“验证与交付”终止步骤，不再
  形成“已取消但 11/11 全完成”的矛盾状态。Agent 项目目录工具补充验证为后端相关
  `186 passed`、前端全量 `100 files / 897 tests passed`、TypeScript 与 Vite production
  build PASS。

## 2026-07-30 里程碑：桌宠透明主界面与消息栏宽度修复

- 撤销主桌宠窗口的整块深色背景，恢复透明桌面效果；Live2D 角色画布与点击区域从旧版
  “贴右角色列”改为主窗口水平居中。
- 独立消息窗口默认宽度从 `440px` 调整为 `700px`、最小宽度调整为 `640px`，Harness
  观察区打开时不再把右侧消息流和输入框挤成窄栏。
- 验证证据：前端全量 `99 files / 882 tests passed`，TypeScript 与 Relay production
  build PASS；当前源码 Windows 实机确认透明桌面、人物居中、消息窗口实际
  `701×602px`，左右两区和输入框均正常，backend/Vite 均为 `200`。

## 2026-07-29 里程碑：全局 UI 暗色统一

- 新增暗色语义主题层，主窗口、设置、Provider、新手引导、账户、能力中心、Skill Store、
  反馈及共享授权/澄清/等待/审批弹窗不再各自维护白色页面样式。
- Memory、ContextTrace、Context usage 和消息页原有暗色设计保持不变；工具轨迹继续使用
  紧凑工具 UI，公开执行进度不伪装成隐藏“思考过程”。
- 设置页删除已退休的 Harness Supervisor 与自动恢复入口；语音按钮继续明确禁用，等待
  Realtime 接入。
- 验证证据：前端全量 `99 files / 882 tests passed`，TypeScript 与 Relay Vite production
  build PASS。当前源码 Windows 实机检查主窗口、设置、能力中心、Skill Store、反馈、账户和
  独立消息页均为暗色；主窗口输入区恢复正常，backend `/health=200`、Vite `200`，
  embedding worker 存活。

## 2026-07-29 里程碑：工具轨迹与公开执行进度重新分离

- 消息页恢复工具调用/工具结果原有的紧凑工具 UI；工具开始和结束不再各自生成一张重复的
  紫色“思考过程”卡。
- 只有模型主动对用户公开的工作说明才作为“执行进度”保留，使用普通紧凑消息样式直接展示；
  历史 `reasoning-summary:*` 线协议继续兼容，但 UI 不再把它称作“思考过程”。
- 同一条公开说明先流式显示、再落为 durable 进度时，前端会替换临时气泡，不会重复显示。
- 旧会话中已经保存的“准备使用某工具/某工具已完成”固定模板在显示层过滤，原始 durable
  记录不删除；重新打开旧会话也只看到工具轨迹，不再看到重复摘要。
- 原始 provider reasoning 仍只投影无文本 activity 信号，不发送、不保存，也不要求 Agent
  输出隐藏思维链。
- 回归证据：后端 Presenter / parity / history `75 passed`，前端消息流与 WS `42 passed`，
  Harness census `141 items / 0 unmapped`，TypeScript + Vite production build PASS。真实
  simple_harness 窗口重载旧会话后，旧固定模板卡为 0，原工具轨迹与“隐藏执行进度”开关仍可见；
  隔离源码实例 backend `/health=200`。WebView 自动化无法把焦点下钻到文本框，因此未把
  未实际发送的新消息标成真机通过。

## 2026-07-29 里程碑：Harness 常驻 Supervisor 删除

- 删除 `HarnessSupervisor` 与它的 50ms 全局协调轮询。现在没有常驻 Harness 协调任务；
  durable 事件只唤起一个可合并的短生命周期 `HarnessReconciler`，当前积压处理完即退出。
- 连续 500 次触发只保留一个短任务；关闭超时会先取消并确认零残留，再关闭 Driver。
  启动时会逐页清空 child command、signal、recoverable Run 与 delivery，不再停在前 16 条。
- delivery 即时积压会处理到空；失败重试使用有退避的一次性计时器，到期才再次唤醒，
  不恢复常驻轮询。Workflow Driver 每个活动 Run 的事件跟随仍属于该 Run 唯一
  `LiveRun.task`，不是全局协调 authority。
- 严格子 Agent 攻击回归 `180 passed`；最终完整 Harness
  `788 passed, 4 xfailed`。authority 为 DML `1`、run map `1`、supervisor task `0`、
  presenter `1`、transaction starter `53`，manifest/source 无漂移。

## 2026-07-29 里程碑：PPT 冻结 Provider 与可编辑文字校验收紧

- `ppt_pro` child workflow 的 LLM 与 reranker 不再读取进程全局默认值，而是从自己的
  execution Run 恢复冻结 provider plan；缺 Run、缺 UOW、空计划或 provider 不存在均
  fail closed。
- 可编辑文字门禁允许真实渲染器把 `6.3%` 等数字提升为独立原生 callout，同时按页面视觉
  顺序和出现次数核对数字。重复数字缺失、两项指标乱序、上下文缺失及省略号截断不会被
  独立数字误掩盖；editable contract `154 passed`，PPT 相邻全量 `359 passed`。

## 2026-07-28 里程碑：Run 预留与可折叠执行摘要完成

- 新顶层消息在 Provider、Host 和 Context 准备前先广播确定性的
  `chat_v2_run_reserved(session_id, run_id, request_id, turn_id, task_scope_id)`。前端立即把
  本地用户气泡绑定到该 Run；边界尚未建立时发送的后续消息留在本地延迟队列，等
  `chat_v2_run_started` 给出可信 conversation boundary 后再按原 `request_id/turn_id`
  发送为同一 Run 的 continuation，不会误建第二个顶层 Run。
- 本地发起的新 Run 总是成为前台选择；当前 Run 终止后，界面自动切到最新仍在执行的 Run，
  真正独立的后台 Run 保持后台。Harness Inspector 在 projection 仍为 `starting` 时显示
  “正在建立 durable Run…”，不再把尚未落盘误报成 Session 归属错误。
- 无 authority 的新 root 不再共用 `session:<sid>` Context CAS key，而是使用入口已确定的
  `task_scope_id`；同 Session 两个真正独立的新 root 因而不会争用同一初始 Context snapshot。
- 消息页新增可单条折叠、可全局隐藏的“思考过程/结果判断”卡。它只保存可公开的执行摘要：
  来自模型已公开 narration 与工具生命周期，显式清除 `<think>` 块；原始 provider reasoning
  只产生无内容 activity 信号，隐藏思维链不会发送到 UI。
- 摘要以 `projection_kind=workflow_progress`、`context_visibility=exclude` 和稳定
  `reasoning-summary:*` identity 写入所属 Run 的 SessionDB，历史加载可恢复，但普通 Context
  组装不会自动摄入。Agent 只有显式调用只读 `run_details_inspect(run_id)` 才能 page-in
  同 Session 的公开摘要、脱敏工具输入/结果与最终回答；该工具绝不返回隐藏思维链。
- 聚焦回归：后端 Run/Context/摘要查询/build identity `81 passed`；前端连续发送、WS、
  摘要卡和 Inspector `59 passed`；Python compile 与 TypeScript/Vite production build
  PASS。当前源码 Tauri 真实点击复现使用 Session `default` /
  Run `d832ba2ca5be5746b0512c0712eed1a8`：连续发送 UUID 和“帮我打开 Godot 游戏编辑器”后，
  两条消息始终可见，第二条从“等待 Agent 读取…”变为“Agent 已读取”；工具轨迹和多条摘要
  实时出现，全局隐藏/恢复与单卡折叠均通过。单次授权后 `godot__detect` 成功；后续 Godot
  启动链仍按既有工具授权与 nonce 规则独立推进，不作为本里程碑的启动成功证据。

## 2026-07-28 里程碑：运行中输入收口为单一续接操作

- 消息页输入区收口为“一个文本框 + 一个动态发送/停止按钮”。当前选中任务可续接时，
  placeholder 与状态行明确说明消息会发送到当前任务并在安全边界读取；新话题和语音入口
  移到标题栏图标，不再与主输入动作并排竞争。
- 用户续接消息气泡新增真实状态：durable FIFO 接管后显示“等待 Agent 读取…”，原 Driver
  完成 conversation bind 后由 Harness continuation observer 投影“Agent 已读取”；绑定失败
  或绑定前取消显示失败。观察投影异常不影响 durable Run。
- `chat_v2_error` 若属于某条 continuation，只更新该消息状态，不再把仍在运行的目标 Run
  错误标成 failed。
- 聚焦验证：前端输入/WS/消息气泡 `43 passed`，RunKernel continuation `63 passed`，
  Harness bootstrap + execution continuation UoW `45 passed`，main task-scope/continuation wiring
  `29 passed`，TypeScript + Vite production build PASS。当前源码 Tauri 已检查实际布局：
  输入区只保留唯一动态主按钮，标题栏显示新话题与语音图标，Harness 字体设置保持不变。

## 2026-07-28 里程碑：Prepared Tool JSON 边界收口

- `PreparedToolCall.arguments_json()` 成为 Frozen JSON 到 Host canonical JSON 的统一递归
  投影；Harness event、Code Workflow、subagent、capability receipt/retry、fingerprint 和
  ToolRegistry handler 不再浅拷贝 `final_params`，内部不可变 tuple 不会泄漏到外部 JSON。
- `DriverRuntime` 在发布 `tool_requested` 后才允许执行；投影/严格 JSON 校验失败会在零物理
  调用下形成 `tool_argument_projection_invalid` 工具失败并进入既有模型重规划，而不是把
  根 Run 直接标成通用 `driver_failed`。外部非法 tuple/set 等仍 fail closed。
- 聚焦自动化 `125 passed`。当前源码 Tauri 在同一 `default` Session 以新 Run
  `5735337ea3a254c1acd739a5aad5070d` 真实启动既有 GemCollector Godot 编辑器并
  `completed`；ledger 中 `process_start.argv` 为 JSON list，日志四类同源错误均为 0。
  旧失败 Run `88b2305bce215a13a6fbecc205b3dcf3` 保持 `failed`，未改写历史。

## 2026-07-28 里程碑：旧 Catalog 僵尸 Run 与超大工具向量化收口

- `ReActDriver.prepare_recovery` 与恢复流内的确定性 `tool_catalog_stale` 现在共用永久故障
  结算：释放 lease、保留原错误码、一次性把旧 Run 标为 `failed`，并拒绝让尚未绑定的用户
  continuation 继续进入不兼容快照。现行 `HarnessReconciler` 不会对同一个旧 Run 做常驻重试。
- 记忆 fanout、实时 enqueue、backfill 和最终写入四处共同限制向量候选：只处理
  `user/assistant` 普通文本且单条不超过 32,768 字符；tool、截图 data-URI/base64 和超长
  payload 被跳过，启动回填会清理历史无效向量。
- 聚焦自动化 `78 passed`。当前源码 Tauri 对原故障 Run
  `ddec38ff523959fe8455b1446af59072` 真机恢复只记录一次 catalog mismatch，随后 durable
  状态为 `failed`、终态 error code 为 `tool_catalog_stale`，之后无重复恢复；记忆库
  `eligible_missing=0`、`ineligible_embedded=0`，未再出现数百段截图 embedding。
  Windows UI 显示“已连接”、不再显示“努力工作中”，输入框可真实输入且发送按钮恢复可用。

## 2026-07-28 里程碑：同 Session 类型化指代可复用已有任务工作区

- 新 root 默认保持任务隔离；“这个/刚才/之前/已有/继续”等表达先转为 typed
  `TaskReference`，候选必须有稳定 root/task 身份。只有唯一 `resolved` 才加载该 Run；
  `ambiguous/missing` 不注入任意原始历史，而是让主 Agent 根据候选摘要向用户确认。
- 精确 root 优先于 scope fallback，共享 scope 的不同 Run 不会串历史；带未知名称的单候选
  不能自动命中。最终只取所选 Run 末 `12 rows / 16,000 chars`，截断提示计入硬预算。
- 历史工作区只由 Host 可信当前 workspace 与历史 `task_scope_id` 推导，并验证目录存在、未越界
  后注入精确 Windows 路径和项目标记；模型不能用参数扩大范围。Context 明确要求打开/继续/运行
  任务复用已有内容，不重新创建。
- `run_shell` 对尾随 `&` 的 GUI 启动自动脱离标准流，超时清理不再因 GUI 子进程继承 pipe 而
  永久等待；工具描述同时引导模型优先使用 `process_start/app_launch`。
- 聚焦回归 `222 passed`，修改模块 `py_compile` PASS。当前源码 Tauri 真人 E2E root
  `6959c1e036b35182a07f6f631321bf8d` 找到并直接启动既有
  `task-1455b19de08db0a680401ec9d4928c32/GemCollector`，没有 `workflow_spawn`，启动后继续
  `screen_capture/screen_key`；真实 `Gem Collector (DEBUG)` 窗口显示游戏运行与
  `Score: 100`。本项未把“吃完所有黄色点”误记为通过。唯一 provider 为 `sf-glm-5.2`，
  无 HTTP 402、Ollama fallback 或 provider unknown。

## 2026-07-28 里程碑：GLM-5.x 长流式响应不再被固定 180 秒截断

- OpenAI-compatible SSE 的固定 180 秒整次响应 deadline 改为“已解析模型事件之间最多
  180 秒无进展”的滑动 deadline。content、reasoning、tool call、usage 和 final 都会续租；
  Relay heartbeat 不会续租。网络静默仍由 120 秒 read timeout 限制，root Run 仍保留
  15 分钟产品总时限。
- 聚焦自动化覆盖持续进展超过单个 deadline、真正无进展超时和底层 async generator 关闭；
  Provider/Coordinator/ReAct/Workflow 联合回归 `119 passed, 2 skipped`。
- 当前源码 Tauri 真人测试 Run `b6c640910fcc576aab64db60e97668fa` 使用唯一
  `relay-cloud/sf-glm-5.2`。Provider invocation
  `2f9599de22f6e4fc6d89ba3fd48fce7c8f92aef30ac4b4c82aa80ff55b8a3d07`
  从 dispatch 到 outcome 约 184 秒，越过旧上限后正常 `completed`；UI 从“努力工作中”
  回到完整回答，未出现 HTTP 402、Ollama fallback、ReadTimeout 或
  `provider_dispatch_unknown_after_handoff`。

## 2026-07-28 里程碑：Harness Agent 执行时间线与 Provider 输入投影完成

- 左侧 Harness 监控新增按 durable 时间排序的 `Agent 执行时间线`，仍严格使用
  ProductTurnPreparer → RunKernel → Driver/Profile → AgentLoop/Provider →
  Tool Executor → Canonical 投影六层。每一步显示输入、结果、状态、时间和可观察决策，
  完整结构按需展开；不建立第二套执行状态，也不暴露或伪造模型隐藏思维链。
- 在不删除六层时间线和原始技术字段的前提下，新增“当前真实生产链路”和“ReAct 循环视图”。
  前者列出当前真实代码路径并说明 `prepare_direct_run` 是跳过旧 IntentTriage/plan gate 的
  兼容直通方法，不是独立思考层；后者通过稳定 tool call id 精确连接每轮
  Provider 判断、工具行动、工具观察和 `Driver.signal` 回灌。每条记录同时标记原始账本来源
  与界面解释来源，handoff 断连同时展示用户可读原因和未删减的原始错误。
- Harness 左栏底部新增持久化横向字体设置，范围 `100%～170%`、首次默认 `125%`，统一缩放
  标题、状态、正文、错误与原始 JSON；普通/全屏模式共用且不影响右侧消息区。当前源码 Tauri
  已真人验证 `140% → 160% → 140%` 即时生效并保留设置。
- workflow schema 升到 v24，新增不可变
  `execution_provider_invocation_inputs`。Provider claim 后保存经过 Trace/memory 双重
  redactor 的输入投影；64 KiB 内保存完整结构，超限则保存有界摘要。Inspector 通过现有
  UoW left join 读取；旧 Run 没有投影时明确说明只剩 request hash。
- 当前源码 Tauri 真人点击 E2E 通过：Session `default` / Run
  `eb91d2c52c2856ccb23d0c4b306af2a1` 使用唯一 provider chain
  `relay-cloud/sf-glm-5.2`，真实发送“请只回复：Harness输入可见测试完成。不要调用工具。”
  并收到指定回复。新 Provider 详情显示 `message_count=90`，最后一条 user message 与输入
  完全一致，且不再出现旧账本缺失提示；本轮没有 HTTP 402。
- 复杂只读审计 Run `535ada60c2f1567fbc1c81b2e2a0020b` 真机验证 5 轮 ReAct：
  `tool_search → tool_describe → tool_activate → run_shell → Provider`。前四轮工具调用均按
  稳定 call id 与 Action/Effect 账本精确配对，`run_shell` 成功后结果回灌；第 5 轮因
  `RemoteProtocolError` 在 handoff 后断连而 fail closed，非 HTTP 402，provider chain
  只有 `relay-cloud/sf-glm-5.2`，未使用 Ollama。UI 同时保留原有 15 步时间线。
- 修复失败终态只回给任务发起窗口导致多窗口状态分裂：`chat_v2_error` 现在投递给 originator
  与同 Session peers，Live2D 同时以 canonical terminal `run_event` 兜底关闭工作气泡；
  输入栏对已终止的 failed/cancelled Run 回到“空闲”，但 Harness 继续保存原始错误。当前源码
  真机复验 Run `dbcfdc43cc4c583a97c629892e237fc9`：Live2D 不再显示“努力工作中”，
  消息面板左下角为 `✓ 空闲`，Inspector 仍显示 `tool_context_persist_failed`。
- 本项聚焦回归：后端 main task-scope wiring `29 passed`；前端 InputBar `13 passed`；
  TypeScript + relay Vite 完整构建 PASS。
- 聚焦回归：后端 Provider/Schema/Main wiring `55 passed`；前端消息面板 `4 files / 22 passed`；
  TypeScript project build 与 `git diff --check` PASS。

## 2026-07-28 里程碑：GLM 驱动的 Godot durable task 真机闭环

- 当前源码 Tauri 使用 `relay-cloud/sf-glm-5.2` 完成真实 UI E2E。最终 Root Run
  `7e292f4a88b05ef999ec0e9de2b13c6b` 由 simple_harness 自行检索能力、创建多文件 Godot 项目、
  执行 import/headless/runtime 验证、根据真实报错修复 `project.godot`、autoload 和绘制
  warning，并以 `completed` 终态收口。较早的 root
  `bb12960856bb5353a1ee1efc0654fcf6` 虽然其 child
  `child-6e8cbb73daab69f07350d38b2018150c` 已完成项目，但父 Run 在验收端误关应用后于 final
  provider handoff 记为 unknown，不能作为完整成功 Root 证据。
- 最终生成项目位于测试用户目录的
  `workspace/task-1455b19de08db0a680401ec9d4928c32/GemCollector`。独立启动 Godot 后真实
  渲染 `Gem Collector (DEBUG)`，玩家、10 个宝石、计分、倒计时和 `TIME UP` 状态均可见，
  窗口持续运行完整 60 秒。当前 Computer Use 的瞬时按键不能形成 Godot 所需的物理按住事件，
  因此移动与 `R` 重开不记为已获自动化证据。整个生成和修复过程均由 simple_harness/GLM 完成，
  验收端未代写项目。
- 能力检索改为多词 OR 匹配并按命中比例排序；`run shell command execute` 现在能稳定返回
  `builtin:run_shell`。错误的裸 capability 名会得到唯一候选建议，连续两次
  `tool_describe` 失败后由 loop guard 强制回到短检索，避免模型无限猜测
  `run_command`/`write_file`。
- durable child 固化父 Run 选定的 provider/model 和能力快照；provider 连接前超时可协调安全
  重试，流式墙钟超时不会直接丢失 child checkpoint，恢复继续使用原 provider，不会切换到
  本地 Ollama。
- Context 压缩器、durable workflow proposal 压缩和前端 Context 压缩线统一为“不晚于模型
  窗口 70%”。压缩结果进入 checkpoint 后再继续执行；压缩调用失败只保留原上下文并继续任务，
  不把 Run 打成失败。`sf-glm-5.2` 的实时启动证据为
  `context_window=1000000 threshold=0.70 trigger_tokens=700000`。
- Harness 左侧观察区在原始六层账本上提供面向用户的任务名、当前动作、等待原因、最近进展、
  “你现在需要做什么”和折叠的技术详情；技术字段仍可展开排障，但不再作为默认阅读入口。
- 本轮最终聚焦自动化：Context/压缩/checkpoint/resume/授权/能力检索 `159 passed`；Harness 与 Context 前端
  `4 passed`；TypeScript project build PASS。真实运行日志未出现 HTTP 402 或余额不足，
  provider chain 仅启用 `sf-glm-5.2`。

## 2026-07-27 里程碑：Run 超时收口、可信 Shell cwd 与 Godot 任务接地

- 产品 15 分钟 turn timeout 不再因错误的 `_send_chat_final(iterations=...)` 调用再次抛错；
  它会取消 exact root Run、发送结构化 timeout 终态，并结算被取消的 provider invocation。
  transport 前取消记 failed，handoff 后取消/流关闭记 unknown，不再遗留永久 claimed。
- `run_shell` 授权与真实 subprocess 共用可信 cwd。默认 cwd、相对 cwd 和 Shell `HOME`
  都绑定任务 workspace；写范围开启时禁止 cwd 逃逸，修复 backend 下生成字面量
  `~/Desktop/...` 假目录的问题。
- Godot 能力包升到 `1.0.4`，覆盖游戏引擎、2D/3D Demo、角色移动、跳跃、敌人 AI、
  GDScript 及 `Gobot` 常见误拼。Profile Catalog 明确要求多文件软件/游戏项目在首次
  effect 前选择 `workflow.durable_task`，歧义技术名先检索/确认。
- Inspector schema v2 新增只读 activity 派生视图，直接显示当前动作、等待对象、最近进展、
  超过 60 秒的停滞、最近错误和产物数；仍以 execution ledger 为唯一事实源。
- 真人测试继续暴露出 Relay 单回合返回 608 个重复工具调用且末项工具名为空；ReAct 现于
  durable admission 前将单批限制为 32，并把超量/空名称分别转换为
  `provider_tool_batch_too_large` / `tool_call_name_missing` 结构化失败，允许模型重规划，
  不再由 `ContractValidationError` 把 Driver 直接打成 failed。
- 自动化：最终后端/能力包组合回归 `180 passed, 1 skipped`；Inspector 前端
  `3 passed`；TypeScript `tsc --noEmit` PASS。
- 当前源码 Tauri 真人测试 Session `35db286a-7a69-4014-b56f-3dca3ddaa289`：
  原始 `gobot 4` 游戏请求先运行 capability search，正确识别为可能的 Godot 4 误拼并在
  写盘前确认，未调用 Go；确认后 `workspace_prepare` 成功落到
  `backend/userdata/workspace/task-69bacdf574a2ab8c95041c2fbbcbc743`。Inspector 实时显示
  `provider_response`，并在 75 秒无新 durable 进展时显示停滞时长。
- 重启验证时，Godot 包因本地已经安装过内容不同的不可变 `1.0.3` 被正确拒绝；源码版本前进到
  `1.0.4` 后，当前源码 backend 健康启动。随后在同一 Session 通过真实点击创建 Run
  `e77ec5a26b2e5d59a8d72458d1a2cffd`：Relay 连续返回纯文本且 `tool_calls=0`，未能进入
  `workflow_spawn`，因此不把 Godot 项目生成记为通过；但整个过程没有再出现空工具名
  `ContractValidationError`。从 UI 停止后，Run 以 `cancelled/user_interrupt` 收口，
  provider invocation 从 `claimed` 结算为带 dispatch ack/outcome ref 的 `unknown`，
  capability scope 成对 unpin，没有悬空 claim。

## 2026-07-27 里程碑：单一 Context/Run 入口与授权资源修复完成

- Text/Voice 生产入口从 `prepare_context` 直接进入 `prepare_direct_run → RunKernel`；
  `route_intent/plan_decision` 只保留兼容代码。旧 IntentTriage 不再抢先回答、短路、
  澄清或生成第二份 plan，主 Agent 成为普通会话唯一认知 authority。
- failed/cancelled 根 Run 通过 durable `session_terminal` sink 幂等写回所属 Session；
  下一轮 Context 组装前 read-through。投影绑定 Session epoch，修复前旧失败只在
  Run `auth_epoch` 与当前 epoch 相同时回填。
- `workflow_spawn` 授权 selector 精确绑定 root/catalog/profile，并仅从可信 host context
  取得 workspace；全局授权资源审计无空 selector。空资源契约归一为
  `authorization_scope_missing`，不再直接形成 `driver_failed`。
- Deferred capability 支持唯一裸名称规范化；歧义继续 fail closed。
- 聚焦回归 `177 passed`；Harness 分组除两项当前脏工作区冻结清单漂移外，
  `274 + 146 + 193 passed`。真实源码 Tauri E2E Session
  `e9d345e4-55da-4de7-a33b-8156076ea26d` / Run
  `6048fc2701b75ec383550753a758040c` 完成原始中文命令，六层均 completed，并在
  `C:\Users\Administrator\Desktop\春天的散文.txt` 写出真实文件。

## 2026-07-27 里程碑：Harness 细粒度观察、Context 历史与 Provider dispatch 修正完成

- 主消息页现在每次打开都默认显示工具调用轨迹，历史 localStorage 隐藏值不再让新一轮
  工具过程永久不可见；用户仍可用顶部工具按钮临时隐藏/显示。
- 新增默认展开的左侧 `Harness 运行观察`：按所选顶层 Run 展示
  ProductTurnPreparer → RunKernel → Driver/Profile → AgentLoop/Provider →
  Tool Executor → Canonical 投影六层消息。它通过
  `harness_inspector_snapshot` 读取现有 UoW 账本，不维护第二套状态机。schema v2 展示
  ProductTurnPreparer 三阶段输入/输出/起止时间/耗时、provider output/policy、tool
  prepared/effect/outcome 与 canonical payload/correlation；所有层和记录默认折叠，
  展开后直接显示完整原始信息，不再判断或遮罩敏感字段。
- 左栏改为固定高度内部滚动和窄型滚动条；仅在用户接近底部时自动跟随，向上阅读时保留位置并
  提示新轨迹。消息增长不再持续撑高页面。
- Harness 顶部不再铺开 Run 标签；左栏始终只渲染当前选择的一个 Run，未选择或原选择失效时
  自动落到当前 Session 最新 Run。内置下拉框列出该 Session 全部 Run，可切换查看历史记录；
  “全屏查看详情”以同一份实时账本打开近全屏弹层，并自动展开所有层和步骤详情。
- 消息内容区顶部原有的 Run/任务标签栏也已移除；Run 浏览和切换统一收口到 Harness 下拉框，
  避免页面顶部重复占用空间。
- 右上角圆形 Context 入口展开后显示项目名、根目录、Context 组成及 token 时间折线图。
  state.db schema v22 新增 durable `session_context_usage_history`，持久化 provider 实际
  Context 大小与 compaction 前/后 token，历史 Session 重启后仍可恢复；详情展开后直接显示原文。
- workflow schema 升到 v23，新增不可变 provider invocation audit。coordinated
  OpenAI-compatible dispatch 在 durable handoff 后不再内部重试；读超时/cancel/断连
  记录 exact exception type/reason/message 并 fail closed。`ConnectTimeout` 明确表示连接
  尚未建立，现记录为 `transport_not_sent` 并由 coordinator 最多安全重试一次。
- 用户 Session `501eeac7-650a-4982-96f9-c52adebb718b` 的 Run
  `9a84a2f9a22a56e89040620bfa9ed63c` 有 12 次 provider invocation；前 11 次 completed，
  最后一次约 10.066 秒后以 exact `ConnectTimeout` 被旧逻辑误归类为
  `provider_dispatch_unknown_after_handoff`。该历史账本保持原样供审计，新运行应用上述
  未发送判定和单次安全重试。
- 本次增量验证：后端 Harness/Provider/Context/迁移组合 `84 passed`；前端 Inspector、
  Context ring/modal `11 passed`；TypeScript project build 通过。
- 当前源码 Tauri 真人点击验收通过：新 Session
  `b558c5e5-01a7-4c01-9ab3-09f5406cf460` / Run
  `977690de63995aae93403723577516f1` 经真实 Relay 返回“Context验收通过”；六层轨迹显示
  ProductTurnPreparer 三阶段的真实起止时间与耗时；后续按用户决定改为详情展开后默认显示
  全部原始信息。右上角圆环由 0% 更新到 1%，弹层在窄消息窗内显示实际项目根目录、
  `4,985 / 950,000 tokens`、Context 组成与单点历史图；state.db v22 中存在对应
  `provider_attempt` 持久化采样。
- 用户历史 Session `c41a922c-0e19-4506-9c6d-9e82d6e24413` 的错误来自 Run
  `03c4c8862aa0523dbd81bb7dec20b780`：唯一 invocation 在 handoff 后约 10.034 秒进入
  unknown。旧 schema 没保存异常类型；结合当时 10 秒 connect timeout，最可能是
  `ConnectTimeout`，但只能作为推断，不能伪报为确定事实。新 v23 运行会保留 exact 原因。
- 用户 Session `cec00990-d308-4179-b30f-3dfaa6f83aef` 的 Run
  `d6f571d1df735c25be774351b3e59221` 是另一类错误：6 次 provider invocation 全部
  completed，但最后一次耗时 422.8 秒，超过旧 Context OS scope 的 300 秒 orphan TTL，
  后续 `tool_activate` 因 scope 被回收而失败。修复后 ReAct Driver 在完整 provider 回合
  pin scope；必要时从同一 Run 的 durable snapshot 精确恢复。`tool_describe` nonce 仍
  一次性并绑定 exact scope/revision/capability，但不再另设 60 秒模型思考期限。
- 新终态写入结构化 `failure_layer/failure_code`，六层面板不再靠错误文本正则猜发生层；
  历史事件缺字段时明确标注“旧事件未记录子层”。工具与 Attempt 同时显示原始账本状态和
  continuation/effect 观察终态，避免把 stale `running/prepared` 误读成正在执行。
- 自动化：本轮后端相邻组合 `150 passed`；前端全量
  `95 files / 863 tests passed`，TypeScript 与新增/重写模块 scoped ESLint 全绿。
- 当前源码 Tauri 主消息页真人点击 E2E PASS：面板默认打开、折叠/重开、工具卡默认显示、
  “新话题”创建 Session、发送后创建/选中新 Run、执行中状态与最终空闲均已验证。
  Session `f20e3b55-b720-49f3-b1eb-40296d24c853` / Run
  `2cff9d444730553bbfd5bc1c3d7e2b0f` 的真实 `memory_search` 显示两次 provider
  completed、工具 outcome succeeded、Attempt continuation succeeded，最终回答 `5`。
- 本轮又在当前源码 Tauri 中先打开 `cec00990…`，确认六层历史审计为 9 个工具成功、
  2 个工具失败和 422.8 秒 provider 回合；随后真人点击“新话题”创建 Session
  `db2369a4-a450-4285-980c-008f967bffff`，发送真实 `workspace_prepare` 请求并完成 Run
  `c439af02fa015b21b5b701ee6b97901a`。UI 从“思考中”回到“空闲”，默认工具卡与六层
  完成态可见；日志确认 provider scope 成对 pin/unpin 且 backend 来自当前源码目录。

## 2026-07-27 里程碑：Companion 长期成长 Task 0～16 完成

- 成长信号不再由前置正则判断。每条消息先正常 committed，`ProductTurnPreparer` 的模型
  预处理返回 typed `growth_signal_kind`，host 只做枚举/身份/幂等校验并单调提升 outbox
  语义；它不改变顶层 `agent.general`、Profile ticket 或 RunKernel 的 Driver 选择。
- 评测失败会把 frozen report、FailureSet、候选失效、reservation 释放、成长事件与下一轮
  reflection job 原子提交；下一轮仍由同一模型吸收失败原因并重新规划，最多两轮。
  genesis reservation 可按 version CAS 重开，迟到评测不能复活已遗忘候选。
- 主消息页“新话题”恢复为真实新 session：点击即分配空 UUID、绑定 exact Companion
  owner、更新默认 route 并切换两个主消息 peer；携带草稿时首条消息只投影到新 session，
  空白点击不启动 AgentLoop。查看 legacy 历史也不会尝试认领旧 session；普通误写仍以
  typed `companion_session_read_only` 在 Run 启动前拒绝。
- 该修复通过聚焦 backend `62 passed`、frontend `32 passed` 与 TypeScript 编译；并在
  全新当前源码 Tauri 进程中完成主消息页真人点击 E2E：空白“新话题”即时切换新 UUID，
  带草稿“作为新话题发送”再次切换另一 UUID，首条 user bubble 即时显示且真实 Relay
  最终回复落在同一新 session。开发态热更新后的旧 WebSocket/store 不作为交付证据。
- S-1～S-5、S-8 已通过真实 Tauri 主消息页点击/输入 E2E（`6/6 PASS`）：
  built-in 低风险成长及回滚、三独立上下文偏好晋升、Reminder 草稿与重启去重、
  单次详细例外、高风险 external-send 在 Auto 下仍等待确认。S-6/S-7/S-9
  确定性自动化 `3/3 PASS`。
- 最终自动化：Companion `639 passed`；Skill/Memory `128 passed`；Harness
  `636 passed, 4 xfailed`；Capability/Workflow `308 passed`；Frontend
  `93 files / 853 tests passed`；TypeScript、`cargo check`、Rust `78 passed`。
  deterministic smoke 为 `639 + 40 passed`，`DECISION: SHIP`。
- Harness authority 保持 DML owner=1、transaction starters=53、legacy survivor=0，
  AgentLoop AST=3800，R4.5 raw/adjusted/core/Kernel 为
  `140270/139761/45032/1277`，public operations=6、unknown=0。
- 最终 worktree 仅剩 `F:/projects/deskpet`；三个上游 Capability worktree 已逐一确认不再
  挂载且路径不存在。所有测试/Tauri 树按命令行、worktree、配置、PID/create-time 与
  Job identity 精确清理，最终 survivor=0；未按进程名广杀。
- [Companion 架构](./COMPANION_GROWTH.md) ·
  [真人结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/manual-results.md) ·
  [最终审计](../plans/2026-07-24-human-anchored-companion-growth/evidence/task16-delivery-audit.md)

## 2026-07-26 里程碑：Companion 生产编排补齐；真实价值门被 Relay 402 阻断

- S-6/S-7 安全自动化通过 `11 + 42 + 26 + 4` 项回归；Windows Job-scoped
  launcher 聚焦 `12 passed`，具备源码 backend、单一 Vite、DEV clock、离线
  PID/create-time/ancestry cleanup、PID reuse 审计和 MAX_PATH fail-fast。
- `s1/l06` 已真实拉起当前源码 backend 到 ready；结束时精确清理 23 个冻结身份，
  释放 9449.92 MiB，18100/15173 listener=0、survivor=0。
- 后续 `s1/cg-s1s8-main-l19` 已用 Computer Use 在真实主消息页发送精确长期纠正。
  当前源码确认把 committed message 原子写入 ingress outbox，dispatcher owner-fenced
  消费为 `MESSAGE_INGRESS`、创建 reflection job，并由 Product Harness native terminal
  sink 写入同 root 的 `run.final`。聚焦 `56 passed`，l18 启动暴露并修复
  `ServiceContext` 白名单遗漏；l18 精确清理 survivor=0、释放 11004567552 bytes。
- Task 13 follow-up 已把 reflection typed result → candidate build → frozen independent
  evaluation → RiskPolicy/activation dispatcher 接到唯一 production composition root；
  后台 Run 继续复用 RunKernel/Driver，且补齐 provider unknown、failure/replan、精确 build
  claim、启动 manifest 并发读。生产提交锚点 `e8290719`；Companion 全量最终复核为
  `588 passed`。
- `s1/cg-s1-growth-l44` 已在真实 `simple_harness · 消息` 主消息页发送两条明确纠正，
  `message:default:42/43` 均形成 GrowthEvent 并进入生产 reflection Run；真实 Relay
  返回 HTTP 402 `account balance insufficient`，两项 job 在 attempt 3 后
  `failed/background_run_failed`，所以不能形成真实 candidate/evaluation/activation
  正向价值样本。S-1 为 PARTIAL/BLOCKED，S-2～S-5/S-8 未执行；Task 15、Task 16 与整个
  计划仍未完成。
- l44 结束后按专属 Job/PID/create-time 清理 22 个记录进程，释放
  `9795608576` bytes，unknown scope=0、survivor=0；没有用协议直注或脚本回放替代真人 E2E。

## 2026-07-25 里程碑：Companion Task 14 确定性质量门完成

- 唯一 `companion.db` 已演进到 schema v4 并由 `CompanionStore` 持有，owner-domain 数据按
  profile generation 隔离，写事务统一 `BEGIN IMMEDIATE + WAL + FULL`。
- 新增唯一 `GrowthAuthorityRouter`、durable phase/journal、generation ingress gate
  和共享 `RevocationBarrier`。Task 13 已在 Product ingress 关闭期间完成
  `legacy → preparing → companion` 单指针切换，当前恢复为
  `companion/generation=5`；marker 后只可前滚或进入 `paused`，不存在双写窗口。
- 新增 typed `[companion.growth]`（完成项默认 ON、独立 pause），以及 Rust 内存
  Ed25519 signer、跨 Rust/TS/Python canonical command、可信 Relay/local owner 和
  `IdentityReadyGate`。`main/identity_bind` 与 `message-panel/companion_action` 独立
  challenge/lease/seq，shared secret 不能伪造 mutation authority。
- state.db v21 已加入 session owner、消息 ingress outbox、Companion excluded projection
  route/outbox 与 redaction receipt；workflow.db v22 已加入原子 RunStartSnapshot、
  Provider invocation/outcome、Run/effect/delivery fence 与 terminal extension receipt。
  所有 execution writer 共用 `ExecutionWriteLane`；Provider claim-before-transport、
  handoff unknown、provisional retract 和最后物理 effect fence 已接生产组合根。
- Task 4 的 `PreferenceResolver` 已随 Task 13 投入生产：scope-first request override、显式纠正、
  recent/long-term 双层偏好、三独立场景晋升、衰减/冲突、同事务 forget 重算和 winner-only
  Run dependency。旧 JSON 只按 `legacy_local_profile` 幂等导入并保留为只读升级残迹，
  当前 Relay owner 不会取得其归属。
- Task 5 的 `CompanionRuntime`、严格 Clock seam、durable foreground busy gate、原子
  budget claim、safe-only lease recovery 与有界 shutdown 已进入生产。profile coordinator
  先以 `start_prebound()` 验证 exact durable owner/generation/binding epoch，再冻结
  `IdentityReadyGate`；失败会暂停 Runtime，不能开放 Companion chat。
- Task 6A 已完成 checked tool build/effect authority、hash-covered confirm-only、
  Auto 不可绕过的 explicit decision + one-shot grant、Executor 双重校验、readonly
  `memory_recall` scope、background Kernel adapter 与三阶段 dispatch port。生产
  CatalogGate/owner runtime lease 和 MCP/local-runtime adapter 仍由 Task 7 + Task 6B 接线。
- Task 6B 的 product-neutral 三阶段 dispatch slice 已完成：Registry prepare 不产生 effect，
  ToolExecutor 只在 start 越过物理边界，started/not-started/unknown 使用既有 execution
  handoff receipt，ACK 后立即释放短 scope fence、completion 在 fence 外等待。生产
  CapabilityPlatform authority/主组合根注入仍随 Task 7 收口。
- Task 7 第一批已合入 owner-aware Capability schema v2、workflow schema v18、
  run/process catalog stamp、CatalogGate/runtime-set 协议和 17 个独立 v2 Skill Pack；
  slash 已回到主消息 Harness，Loader script 执行面关闭，并修复 Windows 深层 immutable
  pack 路径校验。独立完整性审计仍发现 frozen Skill 三入口、同事务 exact lease、
  Store-backed runtime ledger 与 Personal Workflow interpreter 等 blocker，因此本批只记
  foundation，不把 Task 7 标为完成。
- Task 7 Skill/Workflow 分片已把生产 Loader 收窄为 legacy user，并以独立 managed
  projection 发现 17 个 first-party pack；三入口使用 typed frozen scope，v2 schema 离线
  自包含，Personal Workflow 的 topology/effect/checkpoint seam 已建立。完整解释器属于
  Task 10，catalog/runtime authority 仍在 Task 7 收口。
- Task 7 runtime/authority 集成已把 durable execution scope authority 和
  `CapabilityStoreRuntimeSetLedger` 注入生产 Platform；Run/ToolBatch 不再有第二份 scope
  事实源，owner runtime generation、set/member、launch claim、start/health/abort/recovery
  已落同一 Capability Store。native Job/MCP adapter 与最终 projection commit 仍由 Task 9
  activation saga 补齐，当前缺失时 fail closed。
- Task 8 Candidate 生产组合已加入 Companion schema v3、workflow schema v19 receipt 与
  v20 exact material：
  显式 Skill/Workflow admission、严格 proposal/fence、确定性 candidate identity、
  durable builder coordinator、child terminal + immutable draft receipt/material 原子提交，
  candidate-only Builder output、四来源安全 materializer、Manager validated-ref 复核，以及
  Store 单事务 candidate composition 均已落主线。reserved child scheduler/ToolSet 已随
  Task 13 authority cutover 注入生产组合。
- Task 9 已加入 immutable risk facts、checked-in evaluation suites、冻结只读 memory、
  逐 case durable evaluation execution、确定性 RiskPolicy、activation saga/guard 与唯一
  platform façade。可执行候选必须有 host-issued decision、exact code digest 和专用 risk
  ack；通用 Auto 不可替代。Task 13 已接入 Manager 静态 lifecycle prepare seam；真正
  active pointer 仍只由 Manager 与 CapabilityStore binding CAS 修改。
- Task 10 的 Personal Workflow interpreter、workflow schema v22、single-capture Skill
  selection/activation、typed turn authority、child independent ReadyGate 与 execution
  fence 已完成。生产 `AgentLoop` 和既有 `ContextAssembler/SkillComponent` 现统一注入
  Manager-backed `SkillPackSnapshotResolver`，managed projection 只保留发现/selection
  职责；RunContext owner identity 与 exact snapshot-ref recovery 已耐久化并 fail closed。
- Task 11 的 Companion schema v4、V2 Reminder create/list/cancel、occurrence CAS、
  daily frequency reservation、overdue expire-no-catch-up、delegated draft 与 exactly-once
  delivery outbox 已完成。外部动作即使 Auto 开启仍等待独立确认，确认后恢复原
  Run/decision/call/effect；缺 receipt 时 unknown 且不重发。三个 V2 handler 已进入生产，
  `legacy.list_reminders.v1` 与进程内 Reminder writer 已退休，不保留旧名 alias。
- Task 12 已把 Companion notification/outbox 投影到当前 owner-generation 的默认主消息
  Session：route relocation、clear 后 absent replay、redaction tombstone、live/history
  同 envelope、owner switch fence 与 provisional stream 清理均已接入。详情只经
  owner-fenced `companion_detail_get` 分页读取，并由 Companion `Vc` 与完整 Platform
  `VpVector` 双读保护；前端已有成长卡片、详情 modal 和相互隔离的 evaluation/activation/
  action confirmation。重要 growth 事实即时通知，普通偏好进入确定性日摘要；owner
  generation 删除会先审计再 supersede 旧卡，投影崩溃立即释放精确 claim 重试。冷启动
  identity challenge 竞态已用连接内缓存和有界 restore 重试收口。可信 bind 会恢复现有
  owner inbox，或创建新的空 UUID inbox 并让前端切换；已有内容的 legacy `default`
  session 不会被 Relay owner 认领。
- Task 13 已删除旧 `SkillCodifier/CandidateProposal/ToolPathRecorder`、Presenter/Voice
  codify 回调、裸 candidate confirm 入口和进程内 Reminder 实现。启动顺序固定为
  Capability rehydrate → Companion composition → Product Harness（ingress closed）→
  cutover → projection/runtime adapter → ingress open。历史 Capability ToolSpec 只允许
  精确重算 fingerprint v1 后 CAS 迁移，未知漂移继续 fail closed。生产 `SkillLoader()`
  不再隐式挂载用户目录；非空 legacy Skill 在缺少 immutable pack 发布 receipt 时会在
  marker 前零部分导入并阻断，而不是清单化后错误切换。
- Task 14 已用 32 项可执行故障矩阵、真实 ProductVenue/Kernel 性能组合、隐私/权限门和
  construction audit 收口确定性验收。Companion 全量 `540 passed`、组合根
  `39 passed`，smoke 输出 `DECISION: SHIP`；四类 p95 回退为
  `+5.442%～+6.202%`。Kernel 逻辑拆出 product-neutral Admission collaborator 后物理
  LOC=1277，仍保持唯一六个公开操作；Companion construction 的
  raw/adjusted/core=`129997/129488/44063`，unknown=0。Task 15 正在执行真实 provider
  与主消息页价值矩阵。
- durable catalog 现为 stdio MCP 冻结 simple_harness adapter、真实 launcher 或 npx package
  bundle/package-lock 与 launch config digest；无法证明 host build identity 的 MCP tool
  可以被发现，但不能进入 durable Run。
- 自动化：Task 1 聚焦及 legacy 回归 `195 passed`；Task 2 Python `98 passed`、
  前端聚焦 `14 passed`、Rust `78 passed`；Task 3 backend `250 passed`、前端
  `16 passed` + tsc。真实源码 Tauri 主消息页验证双 WebView active lease、durable
  start/manual waiting/cancel terminal，并在设置页开启 auto 后对账
  `mode=auto/generation=1`；Tauri/Vite 清理后 `survivor=0`、8100/5173 无监听。
  Task 4 相关组合 `178 passed`，Facts 回归 `62 + 21 passed`；Task 5 focused
  `59 passed`、扩大回归 `182 passed`；Task 6A 分组 `31 + 65 + 84 + 24 + 4 passed`，
  扩大组合 `151 passed`，既有 timing case 隔离重跑通过。Task 7 第一批回归为
  Companion `210 passed`、Capability `165 passed`、Skill/slash `83 passed`、
  execution build manifest `7 passed`，相关 pytest survivor=0。Task 7 剩余 catalog/
  runtime authority、Task 6B production dispatch 与通知 UI 已由后续任务补齐；反思业务和
  正式 cutover 仍待完成，不能把本里程碑标成完整成长闭环。
  Task 10 最终门为后端 `443 passed`、前端 `11 passed + tsc`，主消息页真人问答返回
  27。Task 11 Reminder/外部确认组合 `102 passed`、Companion 全量 `394 passed`、
  MCP identity/catalog `23 passed`；主消息页真人键入 `Reply only 35: 70/2=?` 后真实
  provider 返回 35，Run durable completed。最终 Tauri 32-PID 树释放
  10055626752 bytes，8100/5173 释放且 survivor=0。
  Task 12 backend `126 passed`，前端 `8 files / 88 tests passed` 且 tsc 通过；真实
  主消息页由生产 growth event→forget 链生成 `growth_forget` 卡片，真点击详情 audit
  页看到权威 `audit_events` 事实，F5 history 恢复且 outbox delivered。最终 Tauri
  32-PID 树释放 11108134912 bytes，端口 listener=0 且 survivor=0。
  Task 13 聚焦 `166 passed`、Companion `476 passed`、Skill/Preference `127 passed`、
  Capability `251 passed`、Workflow `53 passed`、前端 `851 passed` 且 tsc/manifest/
  diff check 通过；R4.5 LOC/transaction-starter 预算门转 Task 14 收口。真实源码 Tauri
  Relay mode 在空历史 owner inbox `26f5276d-69e9-42b0-ba65-b4a8988b86d6` 真人点击输入
  “请只回复：Task13主消息页通过”，UI 回复“Task13主消息页通过 ✅”并回到空闲，
  `chat_v2_final` 使用同一 session id。最终精确清理 roots `16040/13620` 的 21 个进程，
  释放 9749762048 bytes private memory，survivors=0，8100/5173 listeners=0。
  Task 6B dispatch slice 聚焦与邻接回归 `108 passed`。
  Task 7 runtime/catalog 聚焦 `18 passed`、关键组合 `155 passed`；Companion 扩展为
  `259 passed`，Capability 仍为 `165 passed`。Harness authority/parity/LOC gate 已按当前
  单一 UoW/LiveRun 事实刷新并通过；超时 pytest 精确清理 survivor=0。
  Task 8 foundation 组合 `140 passed`，生产 Candidate/Builder 组合 `39 passed`；
  Companion 全量 `335 passed`、Capability 全量 `249 passed`、Harness 相邻按文件
  `4 + 62 + 4 + 14 passed`。Capability 监控树 survivor=0、释放 982175744 bytes private
  memory；所有超时大组合均按 exact PID/create-time 清理为 survivor=0。
  Task 10 最终组合为后端 `443 passed`、前端 `11 passed + tsc pass`；真实源码 Tauri
  主消息页点击问答返回 `27`、状态回到“空闲”，durable Run completed。E2E 精确清理
  19-PID 树、释放 9653006336 bytes private memory，`8100/5173` 无监听，survivor=0。
- [当前架构](./ARCHITECTURE.md#16-伴生智能体成长能力现状) ·
  [Companion 模块](./COMPANION_GROWTH.md) ·
  [执行计划](../plans/2026-07-24-human-anchored-companion-growth/plan.md) ·
  [Task 0 基线](../plans/2026-07-24-human-anchored-companion-growth/evidence/baseline.md) ·
  [Task 3 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task3-results.md) ·
  [Task 4 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task4-results.md) ·
  [Task 5 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task5-results.md)
  · [Task 6A 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task6-results.md)
  · [Task 6B dispatch 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task6b-dispatch-results.md)
  · [Task 7 foundation 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task7-foundation-results.md)
  · [Task 7 Skill/Workflow 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task7-skill-workflow-results.md)
  · [Task 7 runtime 集成结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task7-runtime-integration-results.md)
  · [Task 8 foundation 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task8-foundation-results.md)
  · [Task 8 生产集成结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task8-production-integration-results.md)
  · [Task 9 评测与激活结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task9-evaluation-activation-results.md)
  · [Task 10 最终验证结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task10-runtime-verification-results.md)
  · [Task 11 Reminder 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task11-reminder-results.md)
  · [Task 12 通知与详情结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task12-notification-results.md)
  · [Task 13 cutover 结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/task13-cutover-results.md)

## 2026-07-24 里程碑：单主 Session 通用行动与可执行能力包（full-audit 部分完成）

- 生产入口只有一个主 Session；每条普通新消息创建独立顶层 Run 并固定
  `profile=agent.general`。运行中续聊进入原 root 的 durable FIFO。窗口只是 Run 投影，
  不再存在 Code/普通模式路由。
- `ProductTurnPreparer` 向父模型提供真实 PreparedToolSet、能力目录与 Profile Catalog
  职责；模型通过 `workflow_spawn` 选择 child Profile。Host 签发一次性 durable
  `ProfileLaunchTicket`，`RunKernel` 只按固定 root Profile 或 ticket 绑定 Driver，
  不读取原文做正则/领域分类。
- TaskGoal、PlanVersion、Attempt、完整 FailureSet、profile ticket、能力
  revision/binding/operation/runtime lease 和 running-root continuation 均进入同一个
  `workflow.db`（schema v16）。失败回到同一 `agent.general` 父模型创建新 Attempt；
  取消/恢复以唯一 `LiveRun.task`、`start_lock`、`driver_lock` 和 Run CAS 收敛。
- Manual 任务级授权与 Auto 直行共用 durable grant/Receipt；Auto 只省略 simple_harness 授权
  等待，不绕 Windows UAC/外部登录，也不跳过能力完整性、测试和健康检查。
- 可执行能力包支持 local JSON 子进程 tool、受管 MCP、版本化激活/回滚和当前 root
  catalog refresh；无匹配能力时模型可启动 `workflow.capability_build`，验证后在同一
  root 立即调用生成工具。builtin Godot 样板当前为 `1.0.2`。
- 当前自动化：聚焦 `206 passed`；backend
  `5961 passed, 16 skipped, 9 deselected, 4 xfailed`；Frontend
  `85 files / 820 tests` + tsc；Rust `73 passed` + build/check；Godot pack
  `13 passed, 1 skipped`；capability smoke、construction/authority gate 全 PASS。
  严格 last-mile 首轮因 Node PATH 缺失诚实 `skip`，显式注入 `DESKPET_NODE` 后
  7/7 PASS、0 skip，`DECISION: SHIP`。Windows Computer Use 已完成 VS-1/VS-2：
  模型在同 root 查询并激活真实工具，Auto 下完成 27-byte 文件与 PowerShell/Git Bash
  双重校验；首次校验失败由同一父模型吸收 failure set 后重规划成功。Manual、Auto 与精确
  launcher 树清理已有部分证据（最终 13/13 PID 退出、释放 8533.9 MiB，主 8100 保留）。
  B-1～B-7、S-1～S-6 完整矩阵尚未结算，且 B-4B 需要用户亲自处理 Secure Desktop
  UAC，因此本里程碑仍不标完成。
- [实现/验收计划](../plans/2026-07-23-universal-action-and-capability-packs/plan.md) ·
  [手工矩阵](../testcase/2026-07-24-universal-action-capability-platform/manual-test.md) ·
  [Harness 当前事实](./AGENT_HARNESS.md)

## 2026-07-22 里程碑：Agent Harness R7 全量门禁与主消息线程真机闭环

- 模块状态：**R7 完成**。点击桌宠“消息”进入的主消息页 S-1～S-7 全部真人 PASS，覆盖只读解释、同线程追问、DeepResearch、PPT durable 审批、重启恢复、busy 输入阻止/取消隔离和工具失败诚实投影。
- 这是当时的 R7 验收边界：主消息页一次只提交一个任务，计划中的 Code 并行工作台未做
  专项真测。该产品形态已被上方 2026-07-24“单主 Session、多独立 root、无模式切换”
  架构取代；仅保留为历史证据。
- 最终门禁：Harness `512 passed, 4 xfailed`；Workflow `703 passed`；完整 backend `5736 passed, 19 skipped, 9 deselected, 4 xfailed`；TypeScript/Vite/Vitest、Cargo 和 last-mile 全绿，`DECISION: SHIP`。
- authority/parity/复杂度：parity 141/141、unmapped 0；唯一 DML/run map/supervisor/presenter；LOC raw/adjusted/core/Kernel=`33,633/33,124/5,948/896`，public ops 6、unknown 0；性能/内存 comparison 11/11 PASS（token writes 0、metadata 7,512 bytes/run、10k strong refs 0）。
- [最终架构图](../plans/2026-07-20-agent-harness-simplification/target-architecture.md)已保存；[R7 结果与截图索引](../plans/2026-07-20-agent-harness-simplification/evidence/r7-main-thread/results.md)可复核。

## 2026-07-21 里程碑：Agent Harness R6 生产切换与旧链删除

- 模块状态：**R6 已默认启用，新请求只走 Product Venue → RunKernel；生产 activation 为 `open/generation=1`。**
- Text、Voice、Code、DeepResearch、PPT、Subagent/Team child 共用可信 RunContext、一次 route、ReAct/Workflow 双 Driver、唯一 EffectBatchExecutor、UoW 与 Presenter。
- 删除 AgentLoop legacy tool/subagent runtime、全局 subagent registry/waiter、DeepResearch/PPT 专用 production starter 与 AutoResume `_run_chat` 旁路；WebSocket 断开只分离 presentation，不取消 durable Run。
- provider/model/session/product 配置在启动前冻结；Context OS 把本轮 `PreparedToolSet + eligibility` 持久化到 Run，Text/ReAct 与 Code 恢复均重新校验 identity/policy/visibility/schema，Code effect 使用完整 trusted execution context；Workflow context 与 ReAct provider-loop 就绪后才开放服务，错误 fail closed。
- R7 已补齐 Tauri 稳定 `client_request_id/client_turn_id` 的生成/复用、主线程 busy 提交阻止与 S-1～S-7 Windows UI 矩阵。
- 自动化门禁：Harness `495 passed, 4 xfailed`；parity `141/141, unmapped=0`；LOC raw/adjusted/core/Kernel `32,913/32,404/5,949/900`，Kernel public ops `6`、unknown `0`。
- 最终结构图保存在 [`target-architecture.md`](../plans/2026-07-20-agent-harness-simplification/target-architecture.md)，完整验收证据见 [`r6-results.md`](../plans/2026-07-20-agent-harness-simplification/r6-results.md)。

## 2026-07-20 里程碑：DeepResearch v7 简化编排、过程可见与文件交付

- 模块状态：**新 run 默认 `deep_research/v7`；核心 Tokio 真实 UI Spike PASS，完整多类别发布矩阵 PENDING。**
- v7 主图缩为六节点，manager 拆题后为每个子方向启动独立 research child，逐项做质量分类、
  有界诊断续跑和统一综合；v1-v6 保留恢复兼容。
- 真实 run `aa61dcc...` 为 engine completed / business partial：4 child 中 3 valid、1 insufficient；
  `dr-2` attempt 2 成功，最终报告 5 来源/3 域并唯一投递报告、Artifact、final_assistant。
- Spike 连续定位并修复三类生产缺陷：Search Gateway 全空时缺少可验证 URL 兜底、
  FetchDocument/legacy extract 契约漂移、workflow final 正文被宠物气泡 `(完成)` 覆盖。
- 门禁：后端相关 `76 passed, 2 deselected`；前端 `24 passed` + TypeScript；Computer Use 真输入、
  真发送和最终报告气泡已验证。证据见
  `plans/2026-07-19-deepresearch-simplification/spike/result.md`。
- AC-9～AC-11 增量真测：run `9c42a6a...` 在单张卡显示 4 个真实方向与逐项状态，attempt 2
  原位更新且通用子代理面板无重复行；最终标准文件卡四个操作均经真实点击。原报告继续位于既有
  `DeepResearch` 目录，另存副本与原文件 SHA-256 相同；同 userdata 重启后仍恢复 4 个方向和唯一文件卡。
- 最终增量门禁：后端 focused `81 passed`、默认配置/注册/恢复相邻套件 `142 passed`、前端 focused
  `80 passed`、TypeScript、Vite production build、目标 lint 与 Computer Use TC-3 PASS。应用当前仍以源码 backend 运行，供继续体验。
- 已知边界：本次只完成一个语义类别的正向技术调研 spike；来源权威性和政策/市场/无证据/恢复矩阵
  留待后续正式发布验收，不能用同题重跑冒充 distinct 场景。

## 2026-07-18 里程碑：DeepResearch v6 默认发布与答案契约稳定性完成

- 模块状态：**新 run 默认 `deep_research/v6`，完整 T11/T12/T13、发布验收与 release identity fixture 受控提交均 PASS**。
- 真实 source-Tauri 场景在 5.08s 内完成，并从国家统计局返回 `140828万人` / `954万人`。
  通用官方归档发现、有界抓取、ref-only 证据、完整性门禁、terminal commit、session
  投影、artifact 渲染和同目录重启均经过真实链路。
- 重启后仍只有一条最终回答和一个 artifact，启动恢复投递数为 0；历史恢复不再展示
  artifact 原始 JSON，引用已使用可访问的 `www.stats.gov.cn` 地址。
- 门禁：后端 v6/official/fetch 91 passed；v5/recovery/delivery 281 passed；前端
  30 passed；TypeScript/Vite relay build 通过。证据和 scope 见
  `plans/2026-07-17-deepresearch-answer-contract-stability/execution-results.md`。
- 真实 UI 已覆盖 completed、partial、insufficient/generate-now 双击和重启/history；用户指定的 `zai-org/GLM-5.2` 在 Relay 中以 `sf-glm-5.2` 提供，基础模型与预分析模型均已切换。重启后的最终真实账户验收为 GLM 出站 5/5 HTTP 200、DeepSeek 出站 0、HTTP 402 为 0，UI 返回“GLM 最终验证通过”；2026-07-19 又暂时将 alias/canonical 上下文画像钉为 1M，源码 Tauri/Context usage 实证有效 950K、compaction 750K，并通过真实 SC-STATS-2 run `81090268…` 的 final/artifact/delivery 闭环。模型/上下文回归 `58 passed`；此前前端配置回归 `27 passed`、后端配置 `6 passed`。
- 最终 release identity 已重跑 completed、partial、generate-now insufficient 与同 userdata 历史恢复；最终门禁为后端 `815 passed`、前端 `822/822 tests passed`、Rust 73 tests，TypeScript/Vite/cargo check 全部通过。

## 2026-07-17 里程碑：DeepResearch v5 Win11-only 交付

- v5 已成为新 run 默认版本；建模、query、dimension analysis、证据准入/readiness、缺口补研、报告审计/修复、三态终态与 continuation lineage 已接入生产 durable workflow。
- 自动化验证：本轮 v5 核心 `254 passed`；DeepResearch/Search Gateway/Playwright 联合回归 `559 passed`，2 个 Chromium 启动/空闲回收时序失败隔离复跑 `2 passed`；前端 77 files / 816 tests、TypeScript、Vite production build、Rust check 通过。此前后端宽回归 1274 passed / 1 skipped；全后端套件的既知 vector embedder worker hang 仍未伪报为全量通过。
- Win11 安装版真机：教育现状与国家计划问题完成 v5 主链并诚实交付 `insufficient_evidence`；继续补研生成 child run，重启后两条历史与动作恢复。真实样本未达到完整报告准入门，因此报告人工质量门不适用。
- **质量故障修复与真实 UI 复验**：修复前五类矩阵的 101 documents / 0 admitted passage 作为失败基线保留。修复后 AI Top 10 `9670a357...`（5 来源、质量 75、`partial`）、国家统计局人口指标 `51781063...`（11/5 有效/第一方、4/4 核心、质量 80、`completed`）、产品比较 `2b79cd88...`（9/1 来源、质量 60、`partial`）均由真实 simple_harness UI 完成；DB/UI elapsed 误差 <2s，默认投影无 raw query/URL。TC-UI-NOW run `6ea6a3a4...` 的单次真实点击在约 0.227 秒内 observed，deadline 后唯一业务终态完成且硬失败 0。核心检索/建模/交付故障与立即生成主链已关闭，Gate F 总体为 **PARTIAL**；updater、卸载、立即生成恢复分支与完整固定场景审计仍待完成。
- 最终 NSIS：`DeskPet_0.6.0-beta.9_x64-setup.exe`，529,333,947 bytes，SHA-256 `1495E432F4314A9B83991724FDB411B1088FF13DFF1673B02FB6F56014F26A38`。内含 Playwright 1.61.0 / Chromium r1228，浏览器 exe SHA-256 `28016DF6864D302434C9231E1F9F1A8A7ECC512CB2FE3FAABB2A36130B96BCF1`。
- 验证范围仅 Win11 x64；未启用、安装或依赖 Hyper-V/VM/ISO/Windows Sandbox，也未触发系统重启。Win10 兼容性保留为后续独立计划。
- **DeepResearch 节点级耗时遥测**：v5 每个 durable 节点的起止时间、耗时、attempt 与成功/等待/取消/失败终态已保存到 workflow DB/trace，并同步安全镜像到 structlog 与 `metrics.jsonl`；fetch 内部的 robots、Scrapling、HTTPX、抽取和浏览器/Jina fallback 有可轮转 metrics 分段，但尚未形成 durable child spans。修复了 v5 legacy stage metric 恒为 0 的观测错误。定向测试 `43 passed`、workflow 回归 `76 passed`、v5/effect 回归 `68 passed`；本次只增加观测，不调整现有超时与降级行为。

> **维护方式**: 本文件是项目完成度、里程碑、worktree 与已知问题的唯一事实源；模块生产链路由同目录专项架构文档维护。
> **用途**: 一页看清整个项目（所有并行工作流）的当前状态。新 session / 子代理
> 先读 [`index.md`](./index.md)，再由索引进入本文件或对应模块架构。

---

## 1. 项目一句话

本地部署的桌面语音宠物：Live2D 桌宠 + 工具调用 + 长期记忆 + 技能系统；正式语音链路正从
本地 VAD/ASR/TTS 迁移为中转站 Realtime 语音。Tauri (Rust shell) + Python backend + React 前端。

---

## 2. 活跃工作流（git worktree 并行开发）

> 项目用多 worktree 并行开发，每个 worktree 独立分支 + 独立 dev 端口
> （见 `scripts/dev-worktree.ps1`）。下表为各分支当前状态。
>
> **2026-07-23 实际工作树核对**：当前只保留 `F:\projects\deskpet` 的
> `master`。Agent Harness 实施产生的 48 个临时 worktree 已在最终交付
> `7429fd59` 合入 `master`（merge `aa7f3835`）后删除，对应 47 个临时
> `codex/*` 分支也已删除。下表其余条目是历史分支状态，不代表当前已挂载 worktree。

| Worktree / 分支 | 负责模块 | 状态 | 文档 |
|---|---|---|---|
| **master** | 主线 — beta-100 ready 集成线 | ✅ 活跃，持续 merge | `README.md` |
| `fix/skill-install-single-owner` | Project-scoped managed Skill 单一 owner、授权恢复、verification Run 与 Capability Center scope | 🟡 当前 worktree 已完成本次故障链实现和 macOS 真 UI 验收，待评审后合入 master | [plan/results](../plans/2026-08-27-chat-skill-install/results.md) |
| `feat/companion-code-v2` | Slash 命令 + /goal + 多 agent team + 工具 partition + prompt cache | ✅ 全套实现 + 真桌宠 E2E PASS；**已 merge master**（`84b8ce0` merge superpowers B3；分支 tip 0 commits ahead of master）；功能 flag 默认 OFF | [plans/2026-05-25-companion-code-skill-upgrade/](../plans/2026-05-25-companion-code-skill-upgrade/) |
| `feat/fun-interactions-2026-05-31` | 12 个趣味交互（drag squash / tap burst / dizzy spin / time-of-day mood） | ✅ 已 merge 到 master (2f54960) | — |
| `fix/restore-ui-pack-2026-05-31` | UI 修复恢复（工作树 reset 丢失的 6 项） | ✅ 已 merge (fd55c9f) | — |
| `live2d-rewrite` | Live2D 渲染层重写 | 🟡 停滞（分支最后提交 2026-05-31；master 已「装回 Live2D SDK + Hiyori」走实用路线，本重写分支似被搁置/探索化，见 §3 mesh 引擎） | — |
| `worktree-memory-upgrade` | 记忆系统 v2 升级 | 🟡 停滞（Stage 0/1 已合 PR #2；分支最后活动 2026-05-23、0 commits ahead of master。记忆后续工作实际已转入 master 主线，见 §4 2026-06-01 严测 / 2026-06-02 审计 #1-#4） | [plans/2026-05-22-memory-system-upgrade/](../plans/2026-05-22-memory-system-upgrade/) |
| `feat/memory-stage2-followup-f1f2` | memory Stage 2 后续 F1/F2 + 真测挖出 F3/F4 | ✅ F1/F2/F3/F4 全修，单测全绿；**已 merge master**（分支 tip 0 commits ahead of master，旧「未 merge」标注已过时） | [plans/2026-05-24-memory-stage2-followup.md](../plans/2026-05-24-memory-stage2-followup.md) · [F3/F4 缺陷](../plans/2026-05-31-memory-tools-flag-gating-bugs.md) |
| `feat/multi-provider-management` | 多 LLM provider 管理 | 🟡 停滞/未启动（分支仅 1 个提交、最后活动 2026-05-11，内容只有 OpenSpec proposal，无实现代码） | — |
| `tool-last-mile-upgrade` | 工具调用 last-mile（artifact + receipt + verify gate） | ✅ 已合 master（详 v3 优化） | [plans/2026-05-23-tool-last-mile-upgrade/](../plans/2026-05-23-tool-last-mile-upgrade/) |

**端口隔离**（`scripts/dev-worktree.ps1 -BackendPort N -VitePort M`）：
- master: 8100 / 5173（默认）
- 各 worktree: 8200+/5273+（手动指定，避免冲突）

---

## 3. 核心功能模块完成度

| 模块 | 状态 | 关键文档 |
|---|---|---|
| **Project-scoped Sessions** | ✅ **macOS release scope PASS** — state.db v33 提供 Project、immutable Session binding、创建 receipt 与 catalog authority；升级前 Session/消息/Project 按全新安装清空，全局 Provider/设置/Keychain 与磁盘文件保留。S-PS-01～S-PS-08 的当前 UI、真实 Provider、fault/restart 与 machine root lane 全部通过；100k Session page p95 低于 43 ms。真实 terminal/read/write 只使用绑定 execution root，project-bound catalog 不暴露 process-wide `mcp:filesystem`。独立审计补测外部非 Git/错误路径、复制/Finder、窄屏和分页间新增/删除/重命名；不可读目录 fail closed，rename 推进 catalog revision。完整 17 分片为 14 PASS + 3 个冻结既有失败，0 unexpected。Windows 是未来独立范围。 | [plan](../plans/2026-08-25-project-scoped-sessions/plan.md) · [结果](../testcase/2026-08-25-project-scoped-sessions/results/2026-08-25-plan-task.md) · [架构](./ARCHITECTURE.md#33-当前-session--run--workspace-authority2026-08-25) · [UI](./UI.md#09-project-scoped-session-ui2026-08-25) |
| **前端 UI / 暗色主题** | 🟡 **Workbench 单窗工作台已实现，r14 实质审计补测中** —— 透明桌宠壳、Live2D/sprite Canvas 与独立消息窗均已移除；当前为侧栏 + Chat/Skills/Artifacts/Settings 四视图。2026-08-11：账户 AuthAdapter/登录注册事件/侧栏账户入口和 Live2D 锁依赖、表情动作消息链全部删除；Vitest `533 passed`、Rust `74 passed`、companion `647 passed / 10 skipped`、MCP `21 passed`、typecheck/build/check 全绿；`kimi-k3` 真实出站 HTTP 200 并回显 `KIMI3_OK`，r12 的 402 阻塞已解除。r13 形式门达到 `READY_FOR_AUDIT`，但独立实质审计否决了设置持久化、几何异常分支、运行期断连、删除即时态和冷启动性能的证据充分性，故未 finalize；r14 正重新冻结并补真机 primary evidence。已退役的单钥匙 Keychain 模块、renderer IPC/binding 与 Rust `keyring` 依赖已移除；最新 `.app` 干净启动和打开设置页均无 macOS 授权弹窗。 | [UI 架构](./UI.md) · [workbench-ui](../plans/2026-08-04-workbench-ui/) |
| **DeepResearch v7 简化编排 / bundled Playwright** | ✅ **本轮 required 范围 PASS** — 新 run 默认 v7；六节点 manager graph 拆 2～6 个方向，每方向独立 child，弱结果诊断后最多续跑一次，再统一综合。单卡显示真实方向/状态/attempt/来源数，内部 attempt 不重复；标准文件卡四个动作、既有 `DeepResearch` 目录保存及同 userdata 重启恢复均经真机验证。完整多类别语义矩阵保留为后续候选。 | [架构](./DeepResearch.md) · [plan](../plans/2026-07-19-deepresearch-simplification/plan.md) · [results](../plans/2026-07-19-deepresearch-simplification/spike/result.md) |
| **语音管线** (Realtime/VAD/ASR/LLM/TTS) | 🟡 **Service SDK Realtime 已接线，真实 Provider E2E 待补** — 旧 `/ws/audio` 和本地 VAD/ASR/TTS 仍关闭；新 `/ws/realtime-voice` 使用 Service SDK `0.3.12`，前端显式点击后才申请麦克风并建立通话。协议、鉴权、PCM、barge-in、挂断和资源释放自动化已绿，但连续真实通话尚未重新验收，因此不标 release PASS。当前路径只承载 provider-native voice；若加入 Agent Tool/Workflow，必须进入正式 ProductTurnPreparer/RunKernel。 | [Harness 架构](./AGENT_HARNESS.md) · [Context OS V1 plan §12](../plans/2026-07-13-context-os-v1/plan.md) |
| **桌宠 supervisor** (P5-S1) | ✅ 生产可用 | `README.md` §桌宠 supervisor |
| **长期记忆 + 自动总结** (P4-S20-D / memory-v2) | ✅ Stage 1/2 ship；F1-F5 全修；严测 4 Phase（33 用例）；**2026-06-02 审计修复 #1-#4**：FATAL-A 自动 backfill 兜底 + FATAL-B 静默降级告警 + MemEval 字面vs改写召回（改写 Recall@5=1.0 证 dense 真工作）+ **出厂点亮 facts_extract/enhanced_retriever/cross_key_merge 语义事实记忆栈**（真机 E2E 待跑）| `README.md` §长期记忆 + [memory-system-status](../plans/2026-05-23-memory-system-status.md) + [严测 spec](../plans/2026-06-01-memory-system-rigorous-test-spec.md) + [审计+最佳实践](../plans/2026-06-02-memory-system-audit-and-best-practices.md) |
| **工具层** (registry + 权限 + 熔断 + last-mile + v3) | ✅ 生产可用 — 2026-07-09 优化 `file_glob` 默认递归扫描：剪枝 `node_modules` / `__pycache__` / `.uv-cache` / `backend/assets` 等重型生成目录，返回 `skipped_dirs/skipped_count` 诊断元数据；显式 root 指向被跳过目录仍可访问，避免兼容性倒退；pytest `test_deskpet_tools_file.py` 33 passed。2026-07-08 补修 ArtifactCard 文件按钮：DeepResearch 报告目录加入 Tauri artifact 白名单，前端按钮增加 pending/success/error 状态反馈；真机点击 `打开` / `复制路径` / `在文件夹中显示` PASS。 | [tool-layer-optimization-v3](../plans/2026-05-24-tool-layer-optimization-v3/) · [file-glob 优化](../plans/2026-07-09-file-tool-scan-optimization/plan.md) |
| **Search Gateway + FetchExtractService** | ✅ **2026-07-15 cooldown 隔离完成** — 默认 ON 的进程内 Gateway 统一百度/DDG/Google CDP/Bing CDP 与可选 SearXNG；provider 共享健康状态已升级为按失败类型配置的 closed/open/half-open circuit，generation/token CAS 保证每个 open generation 仅一个 probe，所有 provider open 时按最早 eligible + 配置序号做受控救援。request budget/diagnostics 保持隔离；429 尊重有界 `Retry-After`，403 与 429 分流，probe cancel/失败/旧 token、cache hit 与安全 metrics 均有回归。连续真实政策→WebGPU run 仍产生 22 attempts / 2 probes 并完成，跨主题 cooldown 连坐已消除。 | [架构](./SEARCH_GATEWAY_DEEPRESEARCH.md) · [results](../plans/2026-07-15-search-quality-cooldown-support/results.md) |
| **DeepResearch v4 技术情报 + 可审计聊天进度** | ✅ **2026-07-15 完成并默认 ON** — 新 run 默认进入 immutable `deep_research/v4`，v1/v2/v3 保留历史与在途恢复；provider limiter/permit 复验/empty-aware rescue 消除宽 fan-out cooldown 连坐。宽主题使用稳定 taxonomy、实体去重和页面噪声过滤；报告采用质量优先的 3～8 项发布门，固定提供一页式执行摘要、组合建议、分主题核心变化/价值/成熟度/风险/日期/逐项引用与方法局限；证据质量不足时摘要和正文都只能“先补证据再决定 PoC”。`zero_candidates`/`insufficient_evidence` 不生成假报告或 Artifact；Session 13 阶段显示每一步“动作 + 结果 + 降级原因”并提供幂等 retry。Xiaomi 同进程最终连续 run `43e851a0...` 与 `59975eb1...` 均为 5 项/5 引用并通过当前 17 项专业报告门；旧 `66720bcf...` 及建议口径不一致的早期样本均降级。报告聚焦 `114 passed`；后端最后代码全量 `4643 passed / 10 known failures` 无新增；前端 `811 passed`、tsc/build PASS。 | [架构](./SEARCH_GATEWAY_DEEPRESEARCH.md#16-宽主题技术情报-v4-与专业报告2026-07-15) · [results](../plans/2026-07-15-deepresearch-wide-topic-reliability/results.md) · [testcase](../testcase/2026-07-15-deepresearch-wide-topic-reliability/deepresearch-wide-topic-manual-test.md) |
| **fake-completion VerifyGate** | ✅ 接电；**出厂默认 strict**（`config.py:293` `verify_gate_mode="strict"`，2026-06-23 `7fd79c83` shadow→strict）（+9 claim patterns 含 code 场景）;strict 真机不误杀 + 单测 31/31;**2026-06-22 修 shipped bug：ephemeral 救援子代理从不读 `[tools.verifier].ephemeral_subagent_model`→恒复用主 LLM**（`build_agent` 注入处直接 `local_llm or cloud_llm`）→新增 `_resolve_ephemeral_provider`（`backend/main.py`）按配置克隆专用 model provider（缺省/失败回退主 LLM）+ 15 单测全绿 + 真机 boot-log 实证 `model='sonnet' base='gpt-5.5'`（`772c4291`） | [v3 §WI-T2.1](../plans/2026-05-24-tool-layer-optimization-v3/00-PRD.md) + [verify strict 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-verify-strict.md) + [ephemeral 真机验证](../plans/manual-results-2026-06-22-ephemeral-model/RESULTS.md) |
| **历史 Code 模式工作流纪律** (superpowers 全套) | ⚪ **仅保留历史证据/兼容读取，不是当前产品模式** — 旧 persona、plan 卡、偏好与 verify strict 的实现和既有 E2E 证据仍可追溯；2026-07-24 起新生产记录不再通过 Code mode、`task_type="code"` 或 code-only tool exposure 选择 persona/Profile/Driver/workspace。代码类请求与其他请求一样从 `agent.general` 开始，由模型显式 `workflow_spawn` 合法 child Profile。 | [历史 spec](../plans/2026-06-02-superpowers-code-workflow/05-LOCKED-spec.md) · [当前 Harness](./AGENT_HARNESS.md) |
| **技能系统** (17 个 first-party Capability Skill Pack + Project-scoped managed Skill + 自动披露) | 🟡 **本次 managed install 故障链 PASS，完整 release gate 待执行** — first-party 与 managed Skill 都只经 immutable Capability Pack、Manager binding、owner-aware Hub snapshot 和 per-Run frozen catalog 提供。聊天/Settings 共用单一 install service，真实 UI 授权后以 canonical verification Run 证明 exact page-in 才能成功。`plan-test-skill@4d8c803…` 的三成员 macOS 安装/恢复/UI 可见性已通过；跨 Project、恶意 fixture、全部 crash boundary 与 full-surface 矩阵尚未据此宣称通过。legacy user Skill 仅作只读兼容。 | [当前架构事实](./ARCHITECTURE.md#project-scoped-managed-skill-安装2026-08-29) · [plan/results](../plans/2026-08-27-chat-skill-install/results.md) |
| **Companion 长期成长** | ✅ **2026-07-27 Task 0～16 完成并默认生效** — `companion.db` v6、state.db v21、workflow.db v23，模型语义成长判定、唯一 Router、trusted control lease、PreferenceResolver、Runtime、动作策略、owner-aware Capability、candidate/evaluation/activation、失败吸收与重规划、Personal Workflow、V2 Reminder、durable 通知/history 与详情 UI 均已进入唯一 production path。旧 Codifier/ToolPath/Reminder writer 已退休。真实主消息页 S-1～S-5/S-8 `6/6 PASS`，确定性 S-6/S-7/S-9 `3/3 PASS`；deterministic smoke `DECISION: SHIP`。 | [模块架构](./COMPANION_GROWTH.md) · [真人结果](../plans/2026-07-24-human-anchored-companion-growth/evidence/manual-results.md) · [plan](../plans/2026-07-24-human-anchored-companion-growth/plan.md) |
| **Agent Harness / 主消息页运行观察** | ✅ **schema v3 语义运行视图与 Session 模型一致性已完成** — workflow/state 完整事实经一致 read cut、keyset 和纯 reducer 生成 Root aggregate 与最多七类实际阶段；blocked 只认结构化 signal，完整 child failure/replacement/root terminal 链显示已接管并完成。左图、消息 activity、右侧 steps 共用一个 Session/root snapshot store；工具使用 default-deny 有界投影并分层折叠，raw payload 不进公共 contract。真实历史 Root 只读复跑为 366 facts/6 phases/29 logical tools/23 shell，projection complete。跨会话 full-surface 后端 29/前端 116，child provider 接线联测 66；S-SRV-1～5 Windows 真机矩阵全部 PASS。 | [Harness 架构](./AGENT_HARNESS.md) · [AgentLoop](./AgentLoop.md) · [testcase](../testcase/2026-08-03-session-model-run-visibility/manual-test.md) |
| **Agent Runtime SDK Context 消费者** | ✅ **0.2.0 consumer-prepared 与 simple_harness 真人回归完成** — private projection-v2 stage 冻结 Persona/历史/Skill/Memory/附件；公开 snapshot default-deny。文本附件双 fresh root、长历史、缺失文件、冷重启与停止恢复均经 macOS `.app` 真人操作通过；budget-only cancel receipt 不再阻塞 ordered projection cursor。 | [Harness 架构](./AGENT_HARNESS.md) · ignored `.local-test-evidence/2026-08-21/sdk-context-consumer-regression/RESULTS.md` |
| **Office 文档生成** (PPT/Word/Excel) | ✅ 生产可用 — PPT 模板填充+AI整页生图+视觉评估闭环;**`ppt_pro` 新工具**(deepresearch 调研→大纲卡确认→首图实测判定:惊艳生图/模板兜底,2026-06-22 ship,旧 `ppt_create` 仍在岗作直传路径;**2026-06-24 真机 E2E PASS — 惊艳生图路径 × doubao-seedream-4.0 端到端跑通,gpt-image-2 全面下线**,`719a0b49`);**Word/Excel 升复杂档**(列表/段内混排/字色/页眉页脚页码/插图 · 数字格式/合并/逐格样式/多图表/嵌图);默认落 `OutPut/{PPT,Doc,Excel}` | [PPT 架构](./PPT.md) · `doc-edit`/`excel-generate`/`ppt-generate` SKILL.md + §4 里程碑 |
| **Agent Loop 优化 7 WI** (tool_choice硬约束/trace/收尾自查/Focus Chain/触发知识/SEARCH-REPLACE降级/ask_clarification) | ✅ 全实现 + 真机 E2E 全 PASS — 7 WI 全 100%(子代理逐批+终评+4代理对抗复审)；WI单测+BC回归全绿；真机抓修3真bug；WI-7 完整问答闭环真点击 PASS；**4代理对抗审计揪出 WI-5 真生效缺口(chat永不fan-out SkillComponent)→已修(`011aab5`)**；**2026-06-20 续修 WI-5 末环真 bug：main.py 漏把 knowledge_enabled 传给 SkillLoader 构造器→loader 恒滤掉知识片段(真机 total=12 而非 15)→已修+真机复验 `skill_auto_disclosed total=15 names=['windows-path-debug'] top_sim=0.950`**。默认 flag off→BC 安全 | [plan](../plans/2026-06-20-agent-loop-optimization/00-PLAN.md) · [架构档](./AgentLoop.md) · [testcase batch-a/b/c](../testcase/) |
| **上下文连续性 + 图片误触发防护** | ✅ 2026-07-12 完成 — L2 改为独立 newest-tail 并按精确 tool_call_id 保持工具组边界；当前 user row 按 message id 去重；L3/topic embedding 独立限时，超时不再抹掉 L2；`task/web_search` 始终携带同 session 有界连续尾部（8 条），修复自然检索追问丢失上文对象；ContextAssembler 改接 AgentLoop 同一 `deskpet_tool_registry_v2`，不再因旧 `tool_router` 把 `web_search` 静默筛空，并注入基于真实 schema 的工具可用性约束；短澄清轮收敛 `generate_image`，显式生图保持可用；accepted/pending receipt 不算完成证据，短澄清流式假声明延迟并确定性拦截。新增聚焦回归 `65 passed`；Computer Use 真机验证搜索追问实际调用 `web_search`，查询参数明确承接为“命运2 高阶暴君 脉冲步枪”，不再反问对象或声称无联网能力。 | [plan](../plans/2026-07-12-context-continuity-fix/plan.md) · [results](../plans/2026-07-12-context-continuity-fix/RESULTS.md) · [testcase](../testcase/2026-07-12-context-continuity/manual-test.md) |
| **Context OS V1** | 🟡 **核心代码、自动化与真 UI E2E-01～11 已通过；语音 E2E-12 blocked** — `context_os_v1` 已出厂默认 ON；prepared request、gap-free Session coverage、capability hydration、snapshot/CAS、单一压缩 owner、L3/Skill page-in、项目规则与 ContextTrace 已完成生产接线。Windows Computer Use 已完成 Default→OFF→ON 真点击回退验证，OFF 八类工具与 revision 51 golden 精确相等，停机后截图/日志哈希完整；整体不标 complete，因为 relay 尚未提供 VR-0 Realtime/ASR/TTS 能力。 | [plan](../plans/2026-07-13-context-os-v1/plan.md) · [results](../plans/2026-07-13-context-os-v1/results.md) · [testcase](../plans/2026-07-13-context-os-v1/testcase.md) |
| **Context OS 长工具 scope lease 修复** | ✅ **2026-07-23 完成并默认生效** — `EffectBatchExecutor` 在 prepared effect 执行期间 pin capability scope，settle 后重启 orphan TTL，消除 `deepresearch` 300s timeout 与 scope 300s TTL 碰撞造成的伪 registry-unavailable；真实 scope 缺失改报 `tool_capability_scope_expired`。中文显式“调研”（含时间线图产物）稳定路由 durable `deep_research@v7`，否定式“无需调研”仍留在 ReAct。聚焦回归 `51 passed`。 | [架构](./ARCHITECTURE.md#153-tool-capability-plane) |
| **Context OS V1 — completion iteration 2（gap-free coverage 主链）** | ✅ 2026-07-13 slice 完成 — `ContextRequestPlanner` 不再以 assembler prebuilt/L2 top-k 是否“能放下”决定历史来源，而是每轮无条件从 SessionDB 装载全部 eligible raw；当前 user row 只计 coverage 不重复注入。超窗时首个 provider 前强制执行 bounded coverage jobs，随后用同一 prepared tool set 重新 plan→重建 messages/coverage→re-budget，gap/overlap/stale/blocked 或残余 jobs 一律 fail closed。compact 后恢复 protected stable/task/path rules 与最近完整 raw causal groups。Context OS + build-agent/voice/config 相邻回归 `287 passed`。 | [Context OS V1 plan](../plans/2026-07-13-context-os-v1/plan.md) |
| **Context OS V1 — completion iteration 1（activation/snapshot/compression）** | ✅ 2026-07-13 slice 完成 — capability activation 在 scope lock 内按 candidate revalidate→预算→snapshot CAS/commit ack→scope commit→local swap 原子排序，CAS 失败不改变 authoritative scope，取消在 DB 已提交时仅推进 diagnostic handle、不激活 candidate；初始请求对 active goal/workflow/receipt 与 explicit-new 长任务 create-or-CAS canonical projection+tool summary，protected task fragment 与 snapshot handle 同步进入 planner/scope；压缩模型改读 `[context.compaction].model` typed config，并在每个 compact cycle 前热读最新设置，显式模型失败不跨模型 fallback。Context OS 相邻聚焦回归 `243 passed`。 | [Context OS V1 plan](../plans/2026-07-13-context-os-v1/plan.md) |
| **Context OS V1 — 按路径项目规则（Task 4.2）** | ✅ 2026-07-13 slice 完成 — `ProjectRulesComponent` 仅在 code/workspace scope 且 host 已验证 workspace root/active path 时加载；按 root→active 层级发现 `AGENTS.md`/rules 并近路径优先，严格拒绝 traversal/symlink 越界，按字符/token/文件数预算截断，记录相对来源、完整文件 SHA-256 与 reason；普通聊天零磁盘扫描，文件变更下一轮重读。自动化 `67 passed`。 | [Context OS V1 plan](../plans/2026-07-13-context-os-v1/plan.md) |
| **Context OS V1 — transcript/control 因果元数据（Task 0.3B）** | ✅ 2026-07-13 slice 完成 — AgentLoop 在 Context OS 路径用 host-only sidecar 标记 assistant/tool transcript，同轮 tool call 与全部 results 共享 `causal_group_id`；goal/todo/subagent/self-check/evidence/completion 等 control 保留 `anchor_after`，旧路径仍执行原始 append 且 wire bytes 不变。Compressor 不再把 late control 按 system role 前移，并在 deepcopy/compact/rebuild 中保留 sidecar 与 causal group。聚焦回归 `64 passed`。 | [Context OS V1 plan](../plans/2026-07-13-context-os-v1/plan.md) |
| **Agent harness governance** (runtime manifest + wiring/policy/runtime/call-site guards) | ✅ 当前由 `backend/deskpet/harness/bootstrap.py::HarnessManifest` 从真实 Kernel operations、Driver 和 `ProfileRegistry` 生成可执行契约；已删除 2026-07-09 阶段的手写 `backend/deskpet/agent/harness_manifest.py`，测试直接检查生产 composition，避免第二份静态事实源。早期 Round 1～5 的 ServiceContext、policy、constructor 与真实 WS handler 护栏继续作为历史回归。 | [早期 plan](../plans/2026-07-08-agent-harness-hardening/plan.md) · [当前架构档](../ARCHITECTURE/AGENT_HARNESS.md) · [testcase](../testcase/2026-07-08-agent-harness-hardening/manual-test.md) |
| **Agent Harness + 单主 Session 通用行动** | 🟡 **R7 基线完成；崩溃恢复门禁绿色，完整治理门仍待收口** — 当前只有一个主 Session；顶层固定 `agent.general`，多 root 可并行隔离，running-root 续聊进入 durable FIFO。模型通过 `workflow_spawn` 选 child Profile，durable ticket 唯一绑定 Driver；失败通过 TaskGoal/PlanVersion/Attempt/FailureSet 回到同一父模型。可执行能力包、CapabilityBuilder、Manual/Auto、UAC external wait 和 Godot `1.0.2` 已接入唯一 Effect/UoW/Presenter。2026-08-13 可靠性单命令门为后端 `57 passed`（含五类独立进程 `SIGKILL`）+ 前端重连 `12 passed`，覆盖授权/effect/child/终态/重连故障；完整 Harness 仍有历史 authority/parity fixture 与结构预算失败，因此暂不标全绿。 | [可靠性门禁](../scripts/acceptance/harness_reliability_gate.py) · [当前架构](./AGENT_HARNESS.md) · [R7 历史结果](../plans/2026-07-20-agent-harness-simplification/evidence/r7-main-thread/results.md) |
| **simple_harness 原生 Workflow Engine（AC-24~31）** | ✅ 2026-07-11 完成 — 生产、锁文件和冻结包移除 LangGraph/LangChain Core/LangSmith；原生内核支持静态 frontier、条件边/join/reducer、有界循环、fenced canonical-JSON checkpoint、节点重试、durable HITL、Trace/replay/fork/eval。三条长任务默认原生执行；checkpoint、终态、业务事件与 delivery 原子提交；dispatcher 恢复到期重试、pending delivery 和已决策未续跑任务；PPT 大纲 decision 可在 Session 中实时显示并在重启后回放。workflow `295 passed`，产品入口 `85 passed`，前端 tsc + `48 passed`，冻结构建/启动 PASS。Windows Computer Use run `33a8298c...` 完成交付；后续真测还验证 open decision 重启后恢复为可点击大纲卡。 | [plan](../plans/2026-07-11-native-workflow-engine/plan.md) · [testcase](../testcase/2026-07-11-native-workflow-engine/manual-test.md) · [results](../plans/manual-results-2026-07-11-native-workflow-engine/RESULTS.md) |
| **Durable Workflow + Trace 历史计划** | ✅ **已完成并由原生引擎接管** — 2026-07-10 的 LangGraph Wave 计划建立了 workflow.db、lease/CAS、checkpoint、HITL、Trace/replay/fork/eval 与三条产品图；2026-07-11 已完成 Native replacement，生产不再依赖 LangGraph，也不再保留‘Wave B-D 实现中’状态。当前执行 owner 为 RunKernel → WorkflowDriver → NativeWorkflowExecutable，Effect/Receipt/Artifact/Delivery 继续复用统一 UoW 与产品服务。 | [历史 plan](../plans/2026-07-10-durable-graph-workflows-trace/plan.md) · [Native plan](../plans/2026-07-11-native-workflow-engine/plan.md) · [当前架构](./AGENT_HARNESS.md) |
| **PPT Pro durable workflow** | ✅ 当前经 RunKernel → WorkflowDriver → Native `ppt_pro` workflow 执行；research、outline decision、逐页 effect、render/preview/review、Artifact/final 都可恢复。大纲 UI 响应通过 Kernel durable decision fence 返回同一 run，不保留 legacy orchestrator 新请求旁路。R7 S-4 已真人验证确认前无 Artifact、确认后同 run 继续并唯一交付 PPT。 | [历史 Task 14 plan](../plans/2026-07-10-durable-graph-workflows-trace/plan.md) · [R7 结果](../plans/2026-07-20-agent-harness-simplification/evidence/r7-main-thread/results.md) |
| **长任务 Session 进度 + PPT 整页图片（AC-19/20/21）** | ✅ 2026-07-11 完成 — DeepResearch 1/7-7/7、PPT Pro 1/12-12/12、Complex Code 1/9-9/9 投影到普通 Session；Code 真点击批准后完成。修复“创建”漏路由、路径内 `ppt` 抢占代码意图、Code Graph 旧 provider 401，并保留完整 provider fallback chain 与实际 provider ID Trace；代码 Session 可见性已修。PPT image-mode 真机交付 21 页 deck，每页 1 个 1792x1008 全幅 picture、0 文本 shape，无水印/黑块；短 deck 工具 schema 与 Durable Graph 均已改为 1-20 页并跨层自动化覆盖（完整短 deck 生图复测待补）；底图不再无条件裁掉底部 120px。后端宽回归 `478 passed`、聚焦 `77 passed`、前端 `767 passed`、tsc PASS。 | [plan](../plans/2026-07-11-ppt-session-progress-full-page/plan.md) · [testcase](../testcase/2026-07-11-ppt-session-progress-full-page/manual-test.md) · [results](../plans/manual-results-2026-07-11-ppt-session-progress-full-page/RESULTS.md) |
| **PPT 多构图 + Session 单卡动态进度（AC-22/23）** | ✅ 2026-07-11 完成 — `full_page_images` 新增六类确定性 deck-level planner/Pillow compositor、中文字体与文字 fit fail-closed、layout/spec/compositor/font-policy 版本化 hash。最终真实 run `371e34dd90c044c0963c748f7a3ce6c9` 交付 6 页整页图 deck，每页 1 picture，视觉审查 6/6 `ok`、无质量警告。Session live/history 统一按 run/seq reducer，单卡原地经历 running/waiting/revision/completed，重启仍恢复一张 100% 卡；大纲“修改”文案、确认、滚动锚定均经真实点击验证。strict full-page 禁止模板回退，并增加 provider 跨 checkpoint 重试与视觉警告终态。验证：后端 workflow/PPT `450 passed`、focused `73 passed`；前端 tsc PASS、focused `34 passed`；最终 PPT、montage 与 UI 截图已归档。 | [plan](../plans/2026-07-11-ppt-layout-progress-ui/plan.md) · [testcase](../testcase/2026-07-11-ppt-layout-progress-ui/manual-test.md) · [results](../plans/manual-results-2026-07-11-ppt-layout-progress-ui/RESULTS.md) |
| **`code_complex:v1` 兼容 Workflow** | ✅ Workflow 本身仍可恢复并保留 proposal/effect checkpoint、durable decision、ToolRegistry V2 与 UoW 语义；但没有 Code 工作台/模式入口。新请求先进入 `agent.general`，只有模型用 `workflow_spawn` 选择合法代码 Workflow Profile 后，ticket-bound child 才能运行它；`main._run_chat` accepted-async 与 Code persona 均不是生产路由 owner。 | [历史 Task 15 plan](../plans/2026-07-10-durable-graph-workflows-trace/plan.md) · [当前 Harness](./AGENT_HARNESS.md) |
| **七步问题处理流水线 / IntentTriage** | ⚪ **历史实现保留，2026-07-27 已退出 Text/Voice 生产入口** — `intent_triage.py`、`route_intent()`、`plan_decision()` 与旧测试仍用于兼容和历史审计，但 `ProductVenueRunAdapter` 不再调用它们。取证、验证与收敛的 in-loop collaborator 可继续服务主 Agent；前置 short-circuit、clarification、system injection 和 plan admission 不再拥有执行 authority。 | [当前 Harness](./AGENT_HARNESS.md) · [历史 ship 用例](../testcase/2026-06-25-problem-pipeline-ship/manual-test.md) |
| **搜索 + Deep Research** (DeepResearch V8 + Phase-1/2) | ✅ 生产可用 — 统一 `search_provider`(多引擎降级队列；**当前默认队列 `("google-cdp",)`** 见 `search_provider.py:61`，必应/DDG/百度仅 opt-in，区域感知；下文 §6.0 改造后口径)+ 聊天 `web_search` 快查 + `deepresearch`(原 `research_run`) V8 管线(**multi-query/HyDE 扩展** → 多引擎搜 + **site: 定向官方域** → trafilatura+**JS 渲染兜底(cdp-edge 连系统 Edge 无头,治 JS/SPA 空壳站,opt-in)**+Jina 抽取 → **中文一手源直连(巨潮财报 PDF / 国标 openstd)**+**美股 EDGAR**(财报兜底) → 分层权威打分含中文源/源质量过滤含字典站剔除/LLM 精排/反思迭代/BGE-M3语义/报告落 DeepResearch/(安装目录下,2026-06-21 迁移)+index.md 索引);**不接付费搜索引擎**;Phase-2 + JS渲染 真机 UI 测全 PASS;**⚠️ 2026-06-20 专项调查**：所谓"新 ReAct 子代理"代码不在仓库(已丢失),`deepresearch`(原名 `research_run`，已于 `5b7d4e3`/2026-06-15 更名，代码中现为 `deepresearch`) 才是唯一在岗实现;挖到 recency 维度真 bug(default_extract 缺 date 字段→新鲜度恒为默认值);全部结论为**静态读码、未运行实测**;**⚠️ 2026-06-21 §6.0 搜索可靠性改造**：默认引擎队列改 `("google-cdp",)`（可达门控，VPN 通才用），去掉 Bing/DDG 默认主力（仅 opt-in）；新增国内稳定**百度/搜狗百科直连源**（默认开，通用主题主力）+ wikipedia/google 可达探测门控；真机揪出并修「搜索 0 结果时 early-return 跳过直连源」严重 bug + 「`config.config` 单例不存在致 `[research]` 配置开关全失效」bug；TC-A1~A6+X1 真机 windows-mcp E2E 全 PASS | **专项架构 [DeepResearch.md](./DeepResearch.md)** · 优化方案 [plans/deepsearch/](../plans/deepsearch/00-optimization-plan.md) · `deep-research` SKILL v0.2 + [Phase-2 测试](../testcase/2026-06-15-deep-search-phase2/deep-search-phase2-manual-test.md) |
| **Scrapling-first 网页抓取 + 金价抓取** | ✅ 生产可用 — 集成 `D4Vinci/Scrapling` fetchers。`web_fetch` 真实运行先走 Scrapling，再退回 httpx；deepresearch/default_extract 真实抓正文时先用 Scrapling 抓 HTML，再交给 trafilatura/JS render/Jina 既有链路；新增 `gold_price_lookup` 专用工具抓取 XAU/USD 与 USD/CNY 并估算人民币/克金价；新增 `scrapling_fetch` 通用抓取工具，作为 `web` 工具集能力暴露给聊天/research/web 任务。旧 `web_search` 空结果/robots/403/TLS 卡死时，普通 fetch/调研/金价路径都有 Scrapling 兜底。`web_fetch`/`web_extract_article`/`default_extract` 结果会显式透出 `fetcher`，且 `web_fetch` 把 `fetcher` 放在大段 `content` 前，避免 UI 预览看起来仍像旧 httpx；deepresearch 文件卡片文案改为“报告已保存，正在整理答复”。`AgentLoop` 现在会在 deepresearch 成功产出 `report_md` 且带引用或文件后，追加收束系统提示，并让下一轮 LLM 调用 `tools=None` + `tool_choice=none`，阻止报告生成后继续默认 `web_search/web_fetch`。验证：Scrapling live smoke passed；`pytest tests/test_deskpet_tools_web.py tests/test_scrapling_tools.py tests/test_research_scrapling_priority.py tests/test_js_render_flag_off.py tests/test_research_sources.py tests/test_task_kinds.py tests/test_problem_pipeline_config.py -q` 66 passed, 1 deselected；`pytest tests/test_deskpet_agent_loop.py tests/test_agent_loop_sentinel.py tests/test_wi1_tool_choice.py -q` 21 passed；`pytest tests/test_agent_loop_pipeline.py tests/test_task_drift_fixb.py tests/test_research_scrapling_priority.py tests/test_deskpet_tools_web.py -q` 30 passed, 1 deselected；`py_compile` passed；前端 `tsc -b` passed。 | `backend/agent/agent_loop.py` · `backend/deskpet/tools/web_tools.py` · `backend/deskpet/tools/research_tools.py` · `backend/deskpet/tools/scrapling_tools.py` · `tauri-app/src/components/MessageStreamPanel.tsx` |
| **DeepResearch source packs** | ✅ 生产可用 — deepresearch 搜索计划增加默认开启的 `source_packs`，对俄乌/Ukraine 高时效主题自动追加 ISW、UN、Reuters/AP/BBC/Al Jazeera 定向搜索；对金价/黄金主题追加 LBMA、World Gold Council、Investing 定向搜索。source-pack 查询去重、每子问题有上限、可用 `[research].source_packs=false` kill-switch 关闭；`ResearchReport.coverage.route` 暴露 `source_packs_enabled`、`source_packs_hit`、`source_pack_queries`，便于 UI/log/测试判定。deep-research skill 文案同步 source packs + Scrapling-first 抓取链路，强调外层不要手动 `web_search`/`web_fetch` 拼报告。2026-07-08 真机补测后将 source-pack 查询从“中文子问题 + site:”收口为 standalone authority-directed queries；二次复测修正自然语言调研入口：`web_search` task policy 显式暴露 `deepresearch` 且排序在 `web_search` 前，真实 UI 发送“请调研一下俄乌最近的局势...”后先出现 `deepresearch` 工具调用，后续补充 `web_search` query 全为英文独立查询（4 条，`has_chinese_site=False` / `has_any_chinese=False` / `has_site=False`），抓取结果包含 `fetcher=scrapling`。验证：`py_compile backend/deskpet/tools/research_tools.py` passed；`pytest backend/tests/test_deskpet_research_tools.py -q` 87 passed；`pytest backend/tests/test_deskpet_context_assembler.py -q` 42 passed；`pytest backend/tests/test_research_scrapling_priority.py backend/tests/test_scrapling_tools.py backend/tests/test_deskpet_agent_loop.py backend/tests/test_agent_loop_sentinel.py -q` 16 passed；`pytest backend/tests/test_task_drift_fixb.py backend/tests/test_research_sources.py backend/tests/test_search_provider.py -q` 69 passed；`pytest backend/tests/test_deskpet_research_tools.py backend/tests/test_research_scrapling_priority.py backend/tests/test_scrapling_tools.py -q` 91 passed；真机 UI E2E PASS：`plans/manual-results-2026-07-08-deepresearch-source-packs/RESULTS.md`。 | `backend/deskpet/tools/research_tools.py` · `backend/deskpet/agent/assembler/policies/default.yaml` · `backend/deskpet/agent/assembler/policy.py` · `backend/deskpet/skills/builtin/deep-research/SKILL.md` · `plans/2026-07-08-deepresearch-source-packs/plan.md` · `testcase/2026-07-08-deepresearch-source-packs/manual-test.md` |
| **任务漂移修复 (v1 软修 + v2 四阶段根治)** | ✅ 生产可用 — 桌宠对无关新请求不再漂回旧主题。**v1**(2026-06-20)组装期软修(Tier1 当前请求锚定 + L2 重定性标签 + Tier2 话题跳变截断含词法兜底)真机 PASS。**v2 四阶段全实现 + 核心真机 PASS**：**A** T0-1 deepresearch 原话夺权(层2纵深，`research_tools.py` request_topic + prompt 双锚)真机 TC-A1/A2 PASS；**B** T1-1 硬会话切分(层1根治，`task_scope.py` 新建会话 effective_sid=UUID，旧 `task-*` 历史兼容 + `session_switched` WS 事件+voice 全链路+前端「新话题」按钮交互收口)真机 TC-B1 PASS；**C** T1-2 L2 降级 external memory(page-in)+`/continue` 透传(512 passed)；**D** T0-3 关 Tier2(8 处 `topic_shift_gate`→false，Tier1 保留)真机 gate `topic_shift_gate=False+shift_path=off+relabel/anchor=True`(473 passed)。flag/sentinel gating，OFF=BC | [plans/2026-06-21-task-drift-fix-v2/](../plans/2026-06-21-task-drift-fix-v2/01-implementation-plan.md) · 真机 [testcase/2026-06-21-task-drift-v2-phase{A,B,CD}](../testcase/) |
| **登录方式：无账户登录，仅手动 provider** | ✅ 2026-08-11 —— 托管账号登录整套移除后，残留的前端 `AuthAdapter`/Login/Register scaffold、账户入口与旧登录诊断脚本也已删除。用户只在设置/首启向导填写 baseUrl + apiKey；身份走签名的本地 profile（`LocalAuthSnapshotProvider`），不再存在账户生命周期事件。历史 relay 方案仅作参考。 | [manual-provider-only](../plans/2026-08-09-manual-provider-only/00-PLAN.md) |
| **Slash 命令 + /goal + 多 agent** (v2) | ✅ 实现 + 真测；**已 merge master**（`84b8ce0`）；**2026-06-27 测试阶段出厂点亮**——`slash_commands`/`goal_mode`/`agent_parallel`/`plan_confirm_gate`/`preference_memory`/`subagent_driver`/`agent_team`/`subagent_nonblocking` 在 `config.py` 默认翻 **True**（不灰度），config.toml 同步 | [companion-code-skill-upgrade](../plans/2026-05-25-companion-code-skill-upgrade/) · [全量点亮 handoff](../plans/2026-06-26-agent-harness-alignment/HANDOFF-enable-flags.md) |
| **goal-completion 升级 (FP-1~FP-5 线)** | 🟡 FP-1 ✅（真机手测门 PASS）；FP-2 抗漂移 ✅（55焦点+280回归绿；R-T4 defer）；FP-3 自我纠错 ✅实现(WI-2.1~2.4+T6+R-T3+R-T6;286焦点/2459全suite绿;§7死循环上界+no_persona_leak+伪完成拦→二次通过+降级矩阵;off→shadow待go/no-go签核;手测门真产物撞写权限门同FP-2口径)；FP-4 记忆+人格 ✅实现(WI-3.1~3.4+B-10双写钩+修daily_decay从未调用bug;MemEval 491+retriever无回归;scope/pinned列)；FP-5 Skills 分级+自创 ✅后端(WI-4.0接通compaction★全回归/4.1 embedding自动披露/4.2重挂/4.3技能自创codifier不执行代码;flag off字节BC)；**5个FP后端实现全完成+R-T5字节基线守+480 goal-completion焦点测试绿**；FP-2/3/4/5真机手测门+FP-5前端确认卡批量补跑(spawn_task) | [10-EXECUTION-ROADMAP](../plans/2026-06-04-goal-completion-upgrade/10-EXECUTION-ROADMAP.md) · [FP-1/](../plans/2026-06-04-goal-completion-upgrade/FP-1/) · [FP-2/](../plans/2026-06-04-goal-completion-upgrade/FP-2/) |
| **pet animation UX** | ✅ v1 ship；pet-tier1 交互（星星粒子/努力工作气泡/拖拽回正）真机+preview 双 PASS | [pet-animation-ux](../plans/2026-05-24-pet-animation-ux/) |
| **桌宠渲染 / 形象** | ⛔ **已移除** —— Live2D SDK/Hiyori 资产、桌宠渲染组件、设置入口、pnpm 锁依赖、前后端 emotion/action 消息及语音标签解析均已删除；主窗从 HTML/CSS 到 React 挂载前均使用不透明工作台背景。历史文档中的 Live2D 里程碑只描述 simple_harness 上游，不是当前生产代码。 | — |
| **OSS 开源准备** | 🟡 进行中（BUSL-1.1 + SPDX + sanitize） | [oss-prep-handoff](../plans/2026-05-27-oss-prep-handoff.md) |

---

| **DeepResearch fan-out 默认开启** | ✅ 2026-07-11 完成 — 出厂配置、当前开发配置和代码缺省统一为 ON；至少 2 个子问题时按 research lane 有界并发（默认并发 2、最多 6 个），主线程统一综合/重排引用，保留 300s 总预算与递归守门。TOML 解析通过，fan-out/workflow/search 联合回归 `57 passed`。 | [fan-out plan](../plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md) |
| **DeepResearch 证据质量 + Session 完整报告** | ✅ 2026-07-12 完成 — 移除百度/搜狗百科默认直连，Agent Harness 增加官方生态定向 source pack，官方文档升为一手来源层级；rerank 503 时由确定性主题锚点门剔除明显串题材料。最终 Markdown 正文直接投影到 Session，附件卡不再显示 `$blob_ref`，异步 handoff 后输入区立即恢复空闲。抓取链路统一为 Scrapling-first（`web_fetch` 仅为兼容接口名，失败才回退 httpx），失败提示改为 `scrapling_fetch`。真机 run `6f5af6f0...` 显示 5069 字报告和 6 条相关引用，重启后仍可见；run `39e57ed0...` 验证后台运行时前台已恢复“发送”。 | [plan](../plans/2026-07-12-deepresearch-quality-session-report/plan.md) · [results](../plans/manual-results-2026-07-12-deepresearch-quality-session-report/RESULTS.md) |
| **DeepResearch 直接集成 Agent-Reach** | ✅ 2026-07-12 完成 — 不引入 LangGraph，也不自建第二套渠道注册表；固定 Agent-Reach 上游 commit，由薄 port 直接复用其 channel/config/doctor/read，simple_harness 原生 workflow 继续负责显式节点、共享状态、checkpoint、fan-out、Trace 和 Session 交付。强 DeepResearch 请求在 ReAct 前确定性进入工作流，显式公开 URL 稳定路由并进入既有证据评分；失败可观测且不阻断通用来源。自动化最终宽回归 `310 passed, 0 failed`；最终 PyInstaller clean build PASS，PYZ 含 34 个 Agent-Reach 条目，冻结包干净目录启动 PASS。Computer Use 真机 Session `f45f61f1...` 单卡从 3/7 更新至 7/7，完整引用报告同会话显示，关闭重开不重复；Trace `3edf400b...` 记录 GitHub 渠道、Jina Reader hit、无 degradation。 | [plan](../plans/2026-07-12-deepresearch-agent-reach/plan.md) · [testcase](../testcase/2026-07-12-deepresearch-agent-reach/manual-test.md) · [results](../plans/manual-results-2026-07-12-deepresearch-agent-reach/RESULTS.md) |

## 4. 最近里程碑（倒序）

| 日期 | 里程碑 |
|---|---|
| 2026-09-24 | **ARP 计量口径纠正 + 思考/不思考双模式 🟡** — DeepSeek 只算线上请求（去掉先前输出储备），记账可以多算不可以少算；提供方显式思考开关、私有思考逐条回传、计数按模式精确；Host 独立思考原生池，新 Mission 默认开。SDK arp.4 `ffd62d3d` / Host `860a4445`。真实思考线路上的 400 规则待核实 |
| 2026-08-30 | **全局多 Skill 安装、恢复与 TokenSeller 普通 Session 真人调用 PASS 🟡** — `plan-test-skill@3a094db…` 以 `simpleharness.pkg2` 保留跨 Skill 资源，失败 verification 释放并 supersede、完整 Manager receipt 恢复、attestation 后全局 activation 幂等收口；三项 binding generation 2 active。真实 `deepseek-v4-flash` Session `ec89714c…` 完成 `/plan-bs` 资源读取、Todo、延迟 Tool 激活并只提出澄清问题。完整安装 release gate 仍待执行。 |
| 2026-08-29 | **Project-scoped managed Skill 当前故障链 PASS 🟡** — 修复 JSON tuple、授权 decision 恢复、verification attempt 恢复、resolver composition、owner-aware catalog 与 Capability Center Session scope；macOS 隔离 App 真实安装 `plan-test-skill@4d8c803…`，intent succeeded、verification attested，三成员 UI 均健康。完整安装 release gate 仍待执行。 |
| 2026-08-21 | **Agent Runtime SDK 0.2/0.3 simple_harness 真人消费者回归完成 ✅** — CTX-1～CTX-5 与 Provider/Session/Context/附件/历史重启/停止 critical surface smoke 全部 PASS；补齐消息页文本附件到 consumer-prepared private stage、公开 default-deny Context 摘要和 Provider wire lowering。真人停止发现并修复 budget-only `unknown` 回执毒化投影游标，重启后 cursor 24→27、下一真实 DeepSeek Run 完成并回到空闲。 |
| 2026-08-04 | **桌面游戏操作与小窗口 Context 稳定性修复 ✅** — 桌面输入按全局资源 lane 跨批次/Run 串行；`window_key` 支持有界单事务序列和纯暂停步；截图 base64 提升为有预算的多模态附件；AgentLoop 按冻结模型窗口压缩并在 provider 预检超限时做一次目标保真 rescue。最终自动化与 Windows 真机复测通过，Run `9dd6243c...` 仅一次三步按键调用并截图完成。 |
| 2026-07-30 | **Harness Inspector 用户时间线 ✅** — 默认页改为“Agent 执行过程”，只显示当前状态、用户需要做什么和按顺序更新的轻量步骤；工具步骤按需展开真实参数，技术账本默认折叠。相关前端 39 passed、production build 与唯一主实例真机交互通过。 |
| 2026-07-30 | **多步骤任务启动状态与公开进度修复 ✅** — 原生 Workflow child 首次 claim/recovery handoff 原子同步通用 Run 为 running 并设置 started_at；child attach 与进度交付改为事件驱动唤醒；`durable_task@v1` 新增九阶段公开进度、“多步骤任务”标签与安全循环事件 identity。启动兼容精确历史 capability fingerprint，cancel intent 不再阻断 Harness 初始化。相关后端 150 passed、前端聚焦 83 passed；唯一主实例 Session `dfcbaac7...` 真机显示“规划下一步 6/9”，双 Run 对账一致。 |
| 2026-07-30 | **桌宠透明主界面与消息栏宽度修复 ✅** — 主窗口恢复透明、人物画布与点击区居中；消息窗口从 440px 加宽为 700px、最小 640px，Harness 与聊天区同时可读。前端 882 tests、TypeScript/production build及当前源码 Windows 实机检查通过。 |
| 2026-07-29 | **全局 UI 暗色统一 ✅** — 新增统一暗色语义主题，主窗口、设置、Provider、新手引导、账户、能力中心、Skill Store、反馈及共享弹窗完成迁移；设置页退休 Supervisor/自动恢复入口。前端 99 files / 882 tests、TypeScript、Vite production build通过；当前源码 Windows 主要页面实机检查通过。 |
| 2026-07-29 | **Harness 完整测试恢复 0 failed ✅** — 修复 parity 对 `run_reserved`、reasoning activity/summary 的漏分类，并把 reasoning summary 的持久化和双 WS 发送纳入 current census，141 条 legacy 映射保持冻结；R4.5 历史 Companion 门禁不变，新增当前 Harness boundary 紧预算 raw/adjusted/core/Kernel=`151338/150829/42375/1285`、unknown=0、public ops=6；冻结 Skill 重挂准备移出旧 `AgentLoop`，AST span 从 3909 降至 3761（门禁 ≤3800），兼容与 fail-closed 语义保持。三类修复分别经子 Agent 对抗复测 PASS，完整 Harness 在 20 分钟外层等待下完成 `711 passed, 4 xfailed, 0 failed`（6m15s）。 |
| 2026-07-28 | **Harness 兼容边界隔离完成 ✅** — 当前 Presenter/投影门使用新名称，旧名只经 `deskpet.compat` alias 与 lazy shim 解析；compat 无业务逻辑，生产 Harness 对 compat、旧 task/grant/redaction 路径和历史 UoW 聚合导入为 0，turn trace 不再宣告已退出的 intent/plan 层。严格测试发现小 UoW view 只对比历史 aggregate 的假安全，推动改为直接对比真实 SQLite 参数名/种类/默认值并补齐固定关键字；最终 58 个唯一方法 call-shape mismatch=0，子 Agent `VERDICT: PASS`。最终前端 `98 files / 879 tests`、build PASS；源码 Tauri 真人点击确认 Voice disabled、`/ws/audio=0`，文字 Run `76102aa7...` 返回指定短句并回到空闲。 |
| 2026-07-28 | **跨 Run typed TaskReference 完成 ✅** — 旧“关键词触发 + 词汇挑行 + 无命中取最近4条”改为稳定 Run/task 候选与 `resolved/ambiguous/missing/not_requested` 四态。只有唯一解析才加载精确 Run 的末12行/16000字符；共享 scope 不串 Run、未知名称不回退、无身份旧行不候选、歧义只给摘要并要求澄清。严格测试推动修复共享 scope、截断预算和单候选误选3个缺口；最终组合 `146 passed`，子 Agent `VERDICT: PASS`。 |
| 2026-07-28 | **workflow.db → state.db 产品视图一致性门完成 ✅** — 下一轮 Context、历史刷新和会话列表预览统一在读取前通过 `SessionTerminalProjectionConsistencyGate`；当前 epoch 的全部根终态按 `workflow_event_id` 幂等补齐，不再受旧 8 条上限影响。epoch 删除/重建会重试，无法确认最新视图时后端 fail closed、前端保留最后已知数据。严格测试先发现并推动修复 ServiceContext 注册阻断；最终 Harness/接口/依赖 `68 passed`、额外积压/epoch `2 passed`、前端 WS `28 passed`、TypeScript build 与 manifest 通过，子 Agent `VERDICT: PASS`。 |
| 2026-07-28 | **Execution 核心依赖方向收口 ✅** — Task Context/TaskGrant durable contracts 移入 `deskpet.types`，trace/sensitive redaction 移入 `deskpet.security`；Execution 对 Agent/Permissions/Workflows/Capabilities/Companion/Memory 的静态与运行时依赖清零。旧路径保持完整 identity/`__all__`/pickle 兼容，生产只走 leaf；绝对/相对 import AST 门禁和 fail-closed 脱敏测试齐备。独立复测 `116 + 43 + 74 + 18 passed`，子 Agent 严格 PASS。 |
| 2026-07-28 | **ReAct 核心职责拆分 ✅** — 6411 行混合文件拆成 Driver 编排、AgentLoop/provider 适配、durable boundary/codec 三块，依赖单向且历史导出对象身份不变；生产 composition 和子代理工具直接引用新边界。ReAct/拆分/边界恢复相关 `69 + 69 + 55 passed`；构建 manifest 更新后发布/装配 `19 passed`，独立子 Agent 严格复测 PASS。剩余约 5k 行 Driver 作为后续 provider-admission/capability-lifecycle 提炼入口。 |
| 2026-07-28 | **Harness 产品入口与 UoW 依赖面拆分 ✅** — `ProductVenueRunAdapter.open()` 拆成 identity/workspace、Context/direct-run 和 venue 协调三段；Kernel、Runtime、ReAct、Workflow、工具、child、continuation、Supervisor、admission 改依赖完整精确签名的小 UoW view。底层仍是唯一 `SqliteExecutionUnitOfWork`/SQLite/写通道；AST 调用面与签名护栏防止漂移。独立验证 `49 + 83 + 106 passed`，子 Agent 严格复测 PASS。 |
| 2026-07-28 | **旧 Voice 安全关闭 ✅** — 默认 `voice.enabled=false`；后端冷启动不导入/实例化/加载 VAD、ASR、TTS，前端主窗口和消息窗口都不建立 AudioChannel、不申请麦克风；`/ws/audio` 与 `/health.voice` 提供稳定关闭状态。后端聚焦 `21 passed, 2 skipped`，前端聚焦 `4 passed`、全量 `877 passed`，tsc/build 通过，独立子 Agent 严格复审 PASS。Realtime 接入留待 relay 契约就绪后实施。 |
| 2026-07-28 | **Prepared Tool JSON 边界收口 ✅** — Frozen 参数统一经 `arguments_json()` 递归恢复为 canonical dict/list；投影失败在物理执行前形成 typed tool failure。125 passed；同一 default Session 新 Run `5735337e...` 真实启动 GemCollector Godot 编辑器并 completed，ledger `argv` 为 list、同源错误 0，旧失败 Run 保持不变。 |
| 2026-07-28 | **同 Session 指代与既有工作区复用修复 ✅** — 新 root 仅在显式指代时有界读取其他 root 的对话，Host 验证历史 task workspace 后注入精确路径；尾随 `&` GUI 启动脱离标准流。222 passed；真实 Run `6959c1e0...` 直接打开既有 GemCollector、无 workflow_spawn，并继续截图/按键。 |
| 2026-07-28 | **GLM-5.x 长流式响应 deadline 修复 ✅** — 固定 180 秒整次响应上限改为有效模型事件间的滑动无进展上限；119 passed、2 skipped。真实 `sf-glm-5.2` invocation 持续约 184 秒后正常 completed，未进入 provider unknown。 |
| 2026-07-27 | **单一 Context/Run 入口与授权资源闭环完成 ✅** — Text/Voice 跳过 IntentTriage/预先 plan，Session 根 Run 失败在下一轮组装前按 epoch 幂等回填；`workflow_spawn` 与全局授权工具均具确定性 selector，唯一裸 capability 名可安全规范化。聚焦 `177 passed`；真实 Session `e9d345e4...` / Run `6048fc27...` 完成原始桌面散文命令，六层 completed，文件真实落盘。 |
| 2026-07-25 | **Companion Task 14 确定性质量门完成 ✅** — 32 项可执行故障矩阵覆盖 ACK 前后、cutover、forget/profile、lease/child/terminal 与 survivor；聚焦 54、Companion 540、组合根 39 全绿，smoke=`DECISION: SHIP`。真实 ProductVenue/Kernel 的四类 p95 回退 `+5.442%～+6.202%`，同一 FULL writer、DML authority=1、transaction starters=53。LOC raw/adjusted/core/Kernel=`129997/129488/44063/1277`，public ops=6、unknown=0；SQLite 测试 worker 生命周期 warning 已修并用 warning-as-error 79 项复核。 |
| 2026-07-25 | **Companion Task 13 单一生产 authority cutover 完成 ✅** — durable journal 在 ingress drain 内切换到 `companion/generation=5`；V2 Reminder、Companion preference/runtime/event writer 成为唯一生产成长链，旧 Codifier/ToolPath/进程内 Reminder 退休。自动化：聚焦 166、Companion 476、Skill/Preference 127、Capability 251、Workflow 53、前端 851，tsc/manifest/diff check 通过；R4.5 预算转 Task 14。真实源码 Tauri Relay mode 在空 UUID owner inbox 真人点击问答成功，UI 回空闲且 `chat_v2_final` session 对账一致；精确清理 21-PID 树、释放 9749762048 bytes private memory，survivor=0、8100/5173 listener=0。 |
| 2026-07-25 | **Companion Task 12 durable 通知与主消息页详情完成 ✅** — profile inbox notification/outbox、route relocation、redaction/absent replay、live/history owner fence、memory 全链 exclude、`Vc + VpVector` 双读详情和三类独立决策已接生产组合。Backend `65 + 32 passed`、前端 `86 passed`、tsc 全绿；真实源码 Tauri 完成主消息页 live 卡片、F5 history 与 evidence modal，精确清理 32-PID 树且 survivor=0。生产成长 authority 仍为 legacy，Task 13 负责原子切换。 |
| 2026-07-22 | **Agent Harness R7 主消息线程与全量发布门完成 ✅** — 主消息页 S-1～S-7 真机全绿；稳定 request/turn identity、workflow decision fence、canonical history、busy 输入阻止、重启恢复和 typed failure 投影闭环。Harness 512、Workflow 703、完整 backend 5736，前端/Rust/last-mile 全绿；复杂度 `33,633/33,124/5,948/896`，performance comparison 11/11 PASS。 |
| 2026-07-21 | **Agent Harness R6 production activation 完成 ✅** — Product Venue/RunKernel 成为新请求唯一 owner，旧 product/AgentLoop/subagent/AutoResume execution bypass 删除；启动恢复、后端 request identity 接口、frozen provider/model/request capability、transport detach 语义闭环。Harness `495 passed, 4 xfailed`；parity `141/141`；LOC raw/adjusted/core/Kernel `32,913/32,404/5,949/900`。客户端 stable ID 生成/复用仍属 R7。 |
| 2026-07-21 | **Agent Harness R5.5 durable admission / cutover readiness 完成 ✅** — A-source commit `69c6980a` 固化 typed admission、严格重复决策语义、launch/recovery fence、canonical terminal 与 durable LiveRun；B 锁定 authority/parity/cutover/budget。final gate `raw/adjusted/core/Kernel=34,756/34,247/5,950/924`，starters23 / DML1 / fault39 / public ops6，parity `141/141`，完整 harness `471 passed, 8 xfailed`。所有 spike/test/benchmark 均按 worktree 精确清理到进程残留0；生产仍 `legacy/0`，下一步 R6 原子 activation。 |
| 2026-07-21 | **Agent Harness R5.5 A-source admission chain 完成 ✅** — dormant ProductVenue 的 plan approval 改为 Kernel/UoW durable admission；pending/accepted/claimed/launched 与 reject/cancel/expiry/launch-unknown 均可恢复，claim/cancel 共用启动锁和 SQLite CAS。canonical `run.final` 唤醒 observer，durable final 刷新唯一 LiveRun 且未完成 task 不再提前失联。幂等 recovery 复用 launch id，非幂等歧义唯一 fail-closed；Goal association 与 terminal delivery 同事务事实链。construction gate `raw/adjusted/core/Kernel=34,756/34,247/5,950/924`，生产仍 `legacy/0`。 |
| 2026-07-21 | **Agent Harness R5.5 provider launch-token dormant seam 完成 ✅** — stable `launch_operation_id` 与精确 provider/adapter/version snapshot 从 ReAct collaborator 显式透传到真实 OpenAI-compatible HTTP header；无 token 保持旧 mocks/调用兼容，不支持幂等时禁止传输重试、stream→nonstream 重放与跨 provider fallback。focused `22 passed`，construction gate `raw/adjusted/core/Kernel=34,070/33,561/5,508/767` 全绿；`main.py` 未激活，生产仍为 `legacy/0`。 |
| 2026-07-21 | **Agent Harness R5.5 durable admission storage slice 完成 ✅** — 复用既有 continuation authority 持久化 typed admission；start/resolve/claim 与 ReAct/workflow 消费均为 fenced 原子边界，workflow replay 只在 `start_claimed=true` 时调度。authority 为 starters23 / DML1 / fault39，focused `104 passed`；construction gate `34,406/33,897/5,729/767`，生产仍 `legacy/0`。 |
| 2026-07-21 | **Agent Harness R4.5 final Kernel/child convergence 完成 ✅** — 删除两层 child launcher 转发壳，统一 root/precreated/recovery launch 与 recovery/signal fenced consumption；补齐 heartbeat、stale fence、cancel/close/release 和 terminal-first race 证据。final gate `raw/adjusted/core/Kernel=33,925/33,416/5,498/767`，公开操作恰6，authority/owner/manifest 全绿；完整 harness `424 passed, 8 xfailed`，生产仍 `legacy/0`，下一步为 R6 原子 activation。 |
| 2026-07-21 | **Agent Harness R4.5 单 EffectBatchExecutor 完成 ✅** — 删除 `UnifiedToolExecutor` 转发边界，把 claim/reuse/reconcile、连续 safe 并发/unsafe barrier、late unknown 与 durable-after-signal ack 收进唯一 `EffectBatchExecutor`；ReAct Driver 保持 effect+continuation+event 原子 owner，失败零 ack、全 late 零 signal、mixed original indexes 与 shutdown final recovery 均有直接测试。core `5,580→5,542` 真净删38，adjusted total `33,466`、Kernel `811`；focused `63 passed`、完整 harness `412 passed, 8 xfailed`，最终 core≤5,500 继续执行。 |
| 2026-07-21 | **Agent Harness R4.5 venue wrapper 真删除 slice ✅** — 删除 `ProductVenueOpenResult`，`open()` 直接返回 `ProductVenueRunSession | ProductVenueRunResult`；Voice 直接消费 session，Text execute 消费同一 union，保留承载 pre-Kernel/terminal 语义的 RunResult。`venues.py 406→386`，core 真净删 20（预算≥14），raw/adjusted/core 为 `33,953/33,444/5,532`；相关 `69 passed`、fault/schema `51 passed`，authority 与 transition 门绿，生产仍 `legacy/0`。 |
| 2026-07-21 | **Agent Harness R4.5 单 HarnessSupervisor authority 完成 ✅** — 删除 ChildRunScheduler/RecoveryCoordinator 两套常驻 task 与 lifecycle，唯一 `HarnessSupervisor._task` 有界轮转 child command/signal、recovery、late-ready、delivery；startup/shutdown 顺序与 LiveRun final sweep 完整落地，late-ready id 在 limit 前过滤。Supervisor authority `2→1`，本切片 core `5,599→5,580` 净删19，adjusted total `33,504`、Kernel `811`；authority target 全绿、focused `97 passed`、完整 harness `404 passed, 8 xfailed`，最终 core≤5,500 继续执行。 |
| 2026-07-21 | **Agent Harness R4.5 单 LiveRun authority 完成 ✅** — Kernel 通过通用 `bind_live_index` 把唯一 `BoundedLiveIndex._runs` 注入 Driver/legacy collaborator，删除三份重复 run map；LiveRun 分别持有 boundary 与 iterator，durable-first read、identity-safe clear、lock 外 `aclose()` 与 terminal/restart 无残留均有测试。run map `4→1`，adjusted total `33,531`、core `5,619`、Kernel `811`，transition/authority 门绿，扩大 focused `158 passed`；Supervisor `2→1` 与 core≤5,500 留给后续 slice。 |
| 2026-07-21 | **Agent Harness R4.5 authority-migration cohort LOC gate 修正 ✅** — `33,618` target 不变；source-hash fixture 将 `execution_uow.py + checkpoint_execution.py` 锁到 `796905b9` 的 blob/hash/group/reason 与 physical/raw-effective LOC。当前 raw total `33,973` 保持可见，cohort raw delta `+524` 减 physical delta `+15` 推导 overcount `509`，调整后 total `33,464`。公式、fixture drift、真实 move/copy/delete、snapshot 与 anti-compression 测试通过；total 门已绿，core/LiveRun/Supervisor 继续执行。 |
| 2026-07-21 | **Agent Harness R4.5 typed transaction surface 完成 ✅** — execution UoW 的 activation、delivery、ReAct boundary、decision、tool claim、effect settle、run outcome、child signal、recovery scope 九类边界收敛为 typed composite API，生产 callers 全迁移后删除旧 starters，精确计数 `33→21`（硬门≤23）；DML authority 保持 `1`、fault matrix 保持 `39`、生产保持 `legacy/0`。当前 total `33,973`、core `5,552`、Kernel `812`，施工门全绿，完整 harness `389 passed, 8 xfailed`；最终 total/core/LiveRun/Supervisor authority 门继续执行。 |
| 2026-07-21 | **Agent Harness R4.5 单 DML authority 与施工峰值门完成 ✅** — checkpoint execution SQL 全部进入 connection-bound `ExecutionTx`，adapter 不再直写 execution tables，DML authority `2→1`；新增 5 个故障窗后 fault matrix 达到 `39`。首批 core 真删除合并后 total `34,157`、core `5,582`、Kernel `812`，`--r45-transition-gate` 与 authority audit 全绿；最终 LOC/typed transaction/LiveRun/Supervisor 门继续执行，生产仍为 `legacy/0`。 |
| 2026-07-21 | **Agent Harness R5 Text/Voice 共用 test-only 产品链完成，R4.5 方案 A 获批 ✅** — Text/Voice 复用唯一 ProductVenueRunSession 与 Presenter 转换，真实 activation `open/1`、owner `kernel/1`、terminal close 无 active ref；harness `363 passed, 8 xfailed`，parity `141/141, unmapped=0`。两个真代码 spike 证明单纯 ExecutionTx seam 与 LiveRun map 合并不会自然降低 LOC，原 `core+UoW≤2,800/Kernel≤450` 作废；经 6 轮挑战定稿结构门，生产继续 `legacy/0`，当前进入 R4.5。 |
| 2026-07-21 | **Agent Harness R4 Workflow/Child/Delivery adapters 完成 ✅** — Router 与 WorkflowDriver 由同一 `ProfileRegistry` 生成；Workflow/Team/Subagent child 统一进 durable command/inbox 与唯一 scheduler；execution delivery 只由 UoW dispatcher claim/complete，Goal 根终态用冻结 id 投影。owner fences 覆盖 cancel/retry/fork，ReAct decision 边界原子可恢复；shutdown 的 readiness-pop/durable-settlement 两种先后顺序及短超时重启连续 `60/60` 通过。组合回归 `405 passed, 9 xfailed`，LOC `33,184 ≤ 33,228`、unknown 0，两份独立审计 PASS。生产仍为 `legacy/0`，R5 开始 Text/Voice test-only parity。 |
| 2026-07-21 | **Agent Harness R3 scoped EvidenceContext 完成 ✅** — completion evidence 精确绑定 run/turn/call/effect/target/artifact；唯一 resolver 为现有 `SqliteExecutionUnitOfWork`，直接 JOIN canonical run/effect，调用方不能传第二套 receipt 集合。run/turn/call/effect/artifact 任一不匹配均 unknown；当前 schema 无独立 target digest，传入时明确 unknown，禁止把 effect fingerprint 冒充目标摘要。legacy session gate 仅在无 scoped context 时保留到 R6。focused `57 passed`，完整 harness `249 passed, 9 xfailed`，LOC `32,986 ≤ 33,228`，unknown 0。 |
| 2026-07-21 | **Agent Harness R3 tagged Driver protocol 完成 ✅** — ports 收敛为且仅为 `DriverEvent`/`DriverSignal` 两个 tagged value class；旧 13 个 candidate/signal class 与 `DriverCandidate` union 删除，语义名只作为构造函数。Kernel/runtime/ReAct/Workflow/Child 全按 kind 消费，AST 防旧 taxonomy 复活；ports 147 行。focused `52 passed`，完整 harness `240 passed, 9 xfailed`，LOC `32,817 ≤ 33,228`，unknown 0；生产仍为 `legacy/0`。 |
| 2026-07-21 | **Agent Harness R3 ReAct capability 边界完成 ✅** — capability snapshot 随 durable React boundary 持久化并在恢复后继续生效；首次/恢复 AgentLoop 都用真实 `tool_names_filter` 限制模型 schema，provider 即使伪造越权 tool call 也会在 prepare 前二次 fail closed。显式空 snapshot=deny-all，旧无字段记录保持兼容。focused `17 passed`，完整 harness `239 passed, 9 xfailed`，LOC `32,965 ≤ 33,228`，unknown 0；生产仍为 `legacy/0`。 |
| 2026-07-21 | **Agent Harness R3 薄 Kernel + 有界 live runtime 完成 ✅** — `RunKernel` 收敛到 647 行，只保留六操作、route、生命周期、recovery lease 与 terminal authority；contracts、唯一 `BoundedLiveIndex`、无状态 `DriverRuntime` 均不持久化。每 run 历史上限 256、subscriber queue 上限 128，过期 cursor/慢消费者 fail closed；新进程 durable observe 从 UoW 重放，terminal/close 强引用归零。focused `18 passed`，完整 harness `237 passed, 9 xfailed`，LOC `32,935 ≤ 33,228`，unknown 0。 |
| 2026-07-21 | **Agent Harness R3 recovery lease/fence 地基完成 ✅** — workflow schema 升至 v8，为 durable run 增加独立于部署 `owner_generation` 的 recovery owner/epoch/expiry；claim/renew/release、未过期互斥、过期接管与 stale epoch 拒写全覆盖。recovery-fenced event append 在同一 `BEGIN IMMEDIATE` 中校验 lease 并写入，关闭 assert→write 的 TOCTOU 窗口；v7 历史字段迁移保持不变。focused `26 passed`，集成 harness `235 passed, 9 xfailed`，LOC `32,809 ≤ 33,228`。生产仍为 `legacy/0`，尚未启用 Kernel recovery。 |
| 2026-07-20 | **Agent Harness 简化 R2 Preparer/Presenter 完成 ✅** — `_run_chat` 保持生产入口与 execution owner，只把产品准备策略提炼为 typed 三阶段，把 legacy AgentEvent 投影提炼为 live/durable/domain registry；窄 `LegacyProductDomainSink` 隔离 WebSocket/peer/DB/plan waiter I/O，无 shadow 或双写。冻结 R0 census 内容不变；current mapping 141/141、57 个 WS sink 完整，删除 dual-send 任一 peer broadcast 会 fail-closed。集成验证：harness `232 passed, 9 xfailed`；LOC `32,490 ≤ 33,228`；相邻 R2 分支 `335 passed`；三生产模块 611 行。 |
| 2026-07-20 | **Agent Harness 简化 R0 机械基线完成 ✅** — 首次 WI-12 功能缩水方案已回退；新版计划经 5 轮挑战通过。R0 冻结 141 个旧产品 callsite、16 个直接 parity 行为与两份不可规避 LOC manifest；10,000 次真实 Kernel/UoW 生命周期 start/final/close 全量一致、强引用 0。组合回归 `176 passed, 9 xfailed`，生产路径未切换。 |
| 2026-07-18 | **DeepResearch v6 默认发布与答案契约稳定性完成 ✅** — 五类 intent、durable continuation/control/retention、三态终态与 exactly-once delivery 完成；最终 release identity 下真实 UI 覆盖 completed、partial、generate-now 双击和重启历史；后端 815、前端 822、Rust 73、tsc/build/check 全绿，基础模型为 `deepseek-v4-pro` 1M；release identity fixture 已纳入受控提交。 |
| 2026-07-17 | **DeepResearch v5 generate-now 单次真实控制主链 PASS** — Computer Use 真点击 run `6ea6a3a4...` 的“立即用现有证据生成”，command 约 0.227 秒 observed，30 秒 settle fence 后 settled/consumed；长 fetch 控制性取消被归类为业务降级结果，run completed、全部节点 succeeded、硬失败 0，唯一终态 `insufficient_evidence`。自动化控制/adapter `50 passed`，v5/control/terminal `226 passed`；重复点击/重连/强杀恢复仍 PENDING。 |
| 2026-07-15 | **DeepResearch v4 宽主题可靠性与专业报告完成 ✅** — 针对 Session `16bbb4ce...` 和旧质量门误判，新增 provider limiter/permit 复验/empty-aware rescue、稳定技术 taxonomy、实体去重、页面噪声过滤、质量优先 3～8 项报告契约、跨段采用建议一致性、成文质量门、诚实失败与幂等 retry。Xiaomi 同进程最新原题 runs `43e851a0...` 和 `59975eb1...` 均通过当前 17 项报告门；旧 `66720bcf...` 与早期不一致样本降级。最后代码后端全量 `4643 passed / 10 known failures`，无新增回归。 |
| 2026-07-15 | **项目事实源统一到 ARCHITECTURE ✅** — 原 `STATUS/status.md` 的 worktree、模块完成度、里程碑和已知问题迁入 `ARCHITECTURE/PROJECT_STATUS.md`；AgentLoop、DeepResearch、PPT 专项档同步归档到架构目录。`AGENTS.md`/`CLAUDE.md` HARD 纪律改为完成后更新模块架构 + PROJECT_STATUS，README、plans 与 acceptance 入口同步；`STATUS/` 仅保留历史链接兼容，禁止双写。 |
| 2026-07-15 | **Search Gateway cooldown 隔离 + DeepResearch v3 引用质量与逐步结果 UI 完成 ✅** — single-flight provider circuit 消除跨主题连坐；结构化 passage/atomic claim 与 fail-closed publish gate 将固定三类连续真测提升到 3/3 completed、mean support 1.0、P95 66.409s。Session 折叠态直接显示每一步动作、结果和诊断，展开态显示指标与下一步；Xiaomi 屏幕真机、Artifact delivery、后端/前端全量和 production build 全 PASS。 |
| 2026-07-15 | **Search Gateway + DeepResearch v2 + durable progress 完整真机 DoD ✅** — 默认 ON 的内置 Gateway、共享 Fetch、六分支 v2、13 阶段默认折叠气泡、重启恢复、双 run 隔离、历史/实时 Artifact 与 `final_assistant` 投递全部闭环。真实固定三类 benchmark 2/3 completed（失败样本保留）、P95 270.548s；Windows W01～W05 PASS。前端 800 tests + tsc/build；后端 4440 passed / 10 个既有范围外失败，较旧基线少 2 且无新增。 |
| 2026-07-14 | **Search Gateway / shared fetch backend WI-1～4 完成 ✅** — 无 Docker/API key/SearXNG 依赖的默认异步快搜主路已接通；可选外部 SearXNG、provider 降级、request-local 诊断、确定性聚合、缓存/冷却/取消/hydrate 与共享 FetchExtractService 均有自动化闭环，旧 DeepResearch v1 搜索列表契约保持兼容。 |
| 2026-07-14 | **Context OS V1 核心 E2E-01～11 真机通过，语音 E2E-12 等待 relay VR-0** — Windows Computer Use 已完成真实输入、ContextTrace、Code mode 与 Default→OFF→ON 回退；OFF 八类工具与 revision 51 golden 精确相等，截图/日志停机后哈希可复核。整体不标 complete：正式语音必须 relay-only，而当前中转站尚无 Realtime/ASR/TTS endpoint 或语音模型 alias。 |
| 2026-07-12 | **DeepResearch 直接集成 Agent-Reach 真机通过 ✅** — 固定上游 commit 并直接复用 channel/config/doctor/read；simple_harness 原生 workflow 继续拥有节点、checkpoint、fan-out、Trace 与 Session 交付，强调研请求在 ReAct 前确定性进入工作流。最终宽回归 `310 passed, 0 failed`，PyInstaller clean build 与冻结启动 smoke PASS。真机单卡 3/7→7/7、同 Session 完整引用报告和重开恢复 PASS；Trace 命中 GitHub 渠道的 Jina Reader，未发生 degradation。 |
| 2026-07-12 | **DeepResearch 证据质量与 Session 完整报告真机通过 ✅** — 修复无关百科证据在 rerank 503 时漏入、完整报告只存 blob/Session 仅显示短摘要、AsyncHandoff 后输入区持续“停止”三处问题。真机两轮报告均完整显示并带可点击引用；首轮 5069 字、6 条 Agent Harness 相关来源，完全重启后历史仍恢复。独立审计后将准入门强化为中英文主题锚点，拒绝漂移子问题自证，并隔离真实语义/部分 LLM 重排与普通词面分数。自动化：180 + 21 + 33 后端用例、35 前端用例与 tsc 全绿。 |
| 2026-07-11 | **simple_harness 原生 Workflow Engine 完成并真机通过 ✅** — 移除 LangGraph/LangChain Core/LangSmith 运行与打包依赖，三条长任务默认使用 `deskpet-native-json-v1` checkpoint。workflow `295 passed`、产品入口 `85 passed`、前端 tsc + `48 passed`、冻结构建/启动 PASS。普通 Session 真发 2 页 PPT、看到单卡进度和大纲卡、真实点击确认后完成交付；完全重启后 12/12 完成卡和附件仍恢复；open decision 也可由 durable event 在重启后恢复为可点击确认卡。 |
| 2026-07-11 | **DeepResearch 子调研 fan-out 默认开启 ✅** — 出厂配置、当前开发配置与代码缺省统一为 ON；宽主题拆出至少 2 个子问题时有界并发执行，默认 research lane 并发 2、最多 6 个子问题，子跑自动降档后统一综合引用。验证：TOML 解析通过，联合回归 `57 passed`。 |
| 2026-07-11 | **PPT 六类构图与 Session 单卡动态进度真机通过 ✅** — 最终真实 run `371e34dd90c044c0963c748f7a3ce6c9` 交付 6 页 full-page deck，覆盖封面底栏、左右互换、场景标题带与居中总结；每页 1 picture，视觉审查 6/6 `ok`。PPT 进度卡真实更新到 `12/12 / 100%`，修改大纲、再次确认、修订、滚动锚定与重启恢复均不刷屏。修复 provider 失败静默模板回退并补跨 checkpoint 重试。后端 `450 passed`，前端 tsc + `34 passed`，PPT/montage/UI 截图已归档。 |
| 2026-07-11 | **PPT 大纲语义、长任务 Session 实时进度与整页图片 PPT 真机通过 ✅** — PPT/DeepResearch/Complex Code 节点阶段进入普通 Session；Code Run `e3ff6a6307114554a7f588cba6952ba2` 真批准后 completed。PPT image-mode Run `a68bf00144d143efa98b552d3cba14d1` 真实交付 `deskpet-ppt-1783724870.pptx`，21 页均为单一全幅 picture，无文本 shape/水印/黑块。修复 Code 路由与 provider chain（含实际 provider ID Trace），并把短 deck 工具与 Graph 契约放宽为 1-20 页。验证：后端宽回归 `478 passed`、聚焦 `77 passed`、前端 `767 passed`、tsc PASS。 |
| 2026-07-10 | **PPT Pro session 内确认闭环修复并真机通过 ✅** — 根因是 durable outline decision 只在 Trace/Inspector 暴露，session 没有大纲卡与 resume 桥接，点击后也缺少即时反馈；现已把大纲卡投影到当前 session，session 决定映射到 open durable decision 并后台恢复，立即显示“正在生成 PPT”，同时将 Inspector 决定面板前置。验证：后端 `28 passed`、前端 `10 passed`、tsc 通过；独立 session `6af52d3e-ff14-47f6-9d7f-e80728ae79ad` 使用 Windows Computer Use 真点击“确认生成”，界面变为“已提交决定”并立即出现继续生成消息，run `f25296978c56472fbddc8d5cdfbe578a` 日志记录 `ppt_outline_decision_resolved`。 |
| 2026-07-10 | **Wave B Task 7 Durable HITL 生产闭环完成 ✅** — fenced saver 识别真实 LangGraph `__interrupt__` pending write，在同一 SQLite 事务创建 open decision、固定 interrupt/checkpoint identity、推进 run waiting 并释放 lease；`workflow_decision_resolve` nonce/version CAS 成功后从 DB 重读权威 response 并自动调用 runner resume，重复/过期请求不会触发执行。新增 graph→decision→IPC resolve→resume 集成测试，覆盖 store 重建、双击与过期；验证：聚焦/相邻 `29 passed`，`compileall` 通过。 |
| 2026-07-10 | **Wave E Task 19 diagnostics/retention foundation 完成 ✅** — 新增 caller-supplied retention policy 与 ClockPort 驱动清理器，固定 delivered terminal events/deliveries → evaluation/run tombstone → checkpoint owner/reachability → blob refs → orphan grace 顺序；30 天 full payload、180 天 evaluation/tombstone、24 小时 orphan 边界由调用方提供。live/nonterminal、active decision、undelivered delivery、retained evaluation 与 implementation hash 均受保护；启动期清 expired target reservations、temp/orphan blobs并报告 missing registered files；dry-run 不改 DB/文件，诊断路径限制为 blob-root 相对路径；redaction resource 缺失、畸形、空规则均 fail closed。验证：聚焦/相邻 `22 passed`，全 workflow regression `207 passed`。 |
| 2026-07-10 | **Wave D Task 15 Complex Code 生产接线完成 ✅** — 新增 runtime `ProposalPort`/`ToolDispatchPort` 与 capability/session snapshot；LLM provider call 转严格 `ProposalOutcomeV1`，prepared call/effect id 稳定且 outcome JSON-safe；`main._run_chat` 在 plan/AgentLoop 前按 `route_task` 分流，flags ON 的 code action accepted-async 启动 `code_complex:v1`，只结束当前 chat turn，launcher 持续持有 graph，read-only 保持 ReAct。验证：聚焦+相邻 graph `28 passed`，main harness `51 passed`，真实 registry snapshot smoke 通过。 |
| 2026-07-10 | **Wave D Task 15 Complex Code durable graph 完成 ✅** — v1 图覆盖 intake→clarify→plan→approval→proposal/effect→completion→test/audit→bounded fix→finalize；proposal checkpoint 固定 prepared args/stable call id，崩溃恢复不重复 LLM 且复用部分 effect；稳定 todo、硬预算、CapabilitySnapshot/dynamic HITL、terminal intents 与 ReAct 路由语料均有聚焦测试。验证：`22 passed`，全 workflow regression `180 passed`。 |
| 2026-07-10 | **Wave D Task 14 PPT Pro durable graph 完成 ✅** — 新增 v1 图与 PPT stage adapters：research_core 独立阶段恢复且不产生 DeepResearch terminal delivery；大纲使用 LangGraph interrupt 决策屏障并投影 legacy history；逐页 stable slide id checkpoint 防图片重复；render/preview/visual review 有界修订；publish 后才生成 success/error/cancel Receipt 与 Artifact/final/open intents。补齐生产 `PptRuntime` operation ports 与 startup effect/evaluator 接线，所有图片、渲染、预览、评估及文件 hash 均在专用 executor 执行，flag false 仍走 legacy。验证：生产接线 `5 passed`，graph+接线 `16 passed`，全 PPT regression `142 passed`。 |
| 2026-07-10 | **Durable Graph Workflows Wave A 地基完成 ✅** — 用 `$plan-test` 恢复 AC-1..AC-18 全范围并经 6 轮挑战收敛；固定 LangGraph 1.2.8 / checkpoint 4.1.1 / SQLite saver 3.1.0，完成 v17 migration closure、simple_harness graph contracts、独立 workflow.db schema、fenced async saver/run CAS/blob store、Trace/Evaluation 核心与确定性路由。验证：workflow 专项 `62 passed`，v17 迁移聚焦回归 `132 passed`，前端 `753 passed` + tsc/build 通过，Rust cargo check 通过。三条生产 graph、Inspector UI、回放评测和真机 E2E 继续按 [plan](../plans/2026-07-10-durable-graph-workflows-trace/plan.md) 执行。 |
| 2026-07-09 | **file_glob 扫描降噪与加速完成 ✅** — 后端 workspace `file_glob` 默认递归扫描会剪枝重型/生成目录（`node_modules`、`__pycache__`、`.uv-cache`、`backend/assets` 等），减少 agent 文件工具噪声；返回去重排序的 `skipped_dirs/skipped_count` 便于诊断。挑战反馈补强显式 root 兼容边界：用户明确 `root="node_modules"` 时仍可访问，不把默认优化变成能力删除。测试：执行前 baseline `27 passed`；实现后 `backend/tests/test_deskpet_tools_file.py` `33 passed in 0.87s`，相邻 registry/search 工具 `31 passed`；无 UI 改动，windows-mcp 不需要。 |
| 2026-07-09 | **Agent harness hardening Round 5 一步到位 gate 完成 ✅** — 在 Round 4 AST 调用点基础上，`test_agent_harness_main_callsite_contract.py` 增加动态 `/ws/control` 执行：TestClient 真实发送 `chat` 和 `chat_v2`，仅替换 `build_agent`、broadcast/context-usage 副作用和 problem pipeline，断言 sentinel services 进入 `build_agent(...)`、pre-loop system injection 进入 fake agent messages、`_agent.run(...)` 带 runtime context、WS 发出 `chat_v2_final`。聚合验证：`112 passed in 6.96s`。 |
| 2026-07-09 | **Agent harness hardening Round 4 生产调用点补强完成 ✅** — 新增 `test_agent_harness_main_callsite_contract.py`，AST 检查 `main.py` 真实 chat 路径 `_agent = build_agent(...)` 是否传入 goal store/checker、context compressor、skill loader/matcher、tool path recorder、memory curator、evidence gate、pipeline problem/investigation/observability/convergence 参数；同时检查这些资源来自 `service_context`、evidence gate 受 pre-loop short-circuit 保护、`_agent.run(...)` 带 session/runtime context。聚合验证：`110 passed in 5.41s`。 |
| 2026-07-09 | **Agent harness hardening Round 3 补强完成 ✅** — 在 Round 2 runtime/wiring 测试基础上，新增 `test_agent_harness_contract_parity.py` 固定 `build_agent()`/`AgentLoop.__init__` 关键 harness 参数 parity、PipelineEvent WS payload shape、manifest runtime observability；扩展 runtime contract 覆盖 legacy `dispatch()` fallback。新增测试当场抓出并修复 manifest 观测词汇漂移：`completion_gates` 未列真实 pipeline events。聚合验证：`105 passed in 4.86s`。 |
| 2026-07-09 | **Agent harness hardening Round 2 补测完成 ✅** — 在首轮 manifest/docs/policy/service_context 护栏基础上，按用户反馈补强真实运行时测试：新增 `test_agent_harness_runtime_contract.py` 覆盖 `AgentLoop.run()` 工具调用回灌、EvidenceGate 阻断/取证/放行、subagent completion queue drain；新增 `test_agent_harness_build_agent_contract.py` 覆盖 `main.build_agent()` 到 `AgentLoop` 的 problem pipeline、subagent registry、iteration tracer 接线。聚合验证：`100 passed in 4.92s`。 |
| 2026-07-08 | **Agent harness hardening 完成 ✅** — 按 `$plan-test` 对 simple_harness harness 八个明显缺点逐项调研并落成治理切片：新增机器可读 `backend/deskpet/agent/harness_manifest.py`（request lifecycle / service wiring / defect→AC mapping）、`ARCHITECTURE/AGENT_HARNESS.md`、`docs/agent-harness-lifecycle.md`、计划与 testcase；新增 pytest 护栏覆盖 lifecycle owner、状态/恢复边界、ServiceContext 白名单、ContextAssembler policy fan-out、文档存在性。子代理挑战补出第 9 个相邻风险（event schema/WS/DB/receipt/frontend card 契约漂移）并纳入后续治理轨。本轮测试抓出并修真实策略 bug：`policy.py` 显式 `tools: []` 被错误默认成 `["*"]`，emotion policy 可能暴露全工具，已改为仅字段缺失才默认 `["*"]`。验证：新护栏 `36 passed`；相邻 `test_problem_pipeline.py/test_deskpet_context_assembler.py/test_subagent_nonblocking.py` `57 passed`。 |
| 2026-07-08 | **DeepResearch 报告卡片按钮恢复可用 ✅** — 用户反馈报告卡片“打开 / 在文件夹中显示 / 复制路径 / 另存为”点击无反应；根因是 deepresearch 报告落在 `DeepResearch/`，但 Tauri artifact 白名单只允许 `<user_data>/artifacts|downloads|OutPut`，invoke 被拒绝且前端无错误反馈。修复：Rust `artifact_ops` 增加 runtime `DeepResearch/` 根目录白名单（`DESKPET_DEEPRESEARCH_DIR` / `DESKPET_BACKEND_DIR` / 安装目录 / cwd 推导），React `ArtifactCard` 增加按钮 pending/success/error 状态。验证：`cargo test artifact_ops --lib` 7 passed；`vitest run src/code-panel/ArtifactCard.test.ts` 11 passed；`tsc -b` passed；真机 UI 点击 `复制路径` 显示“路径已复制”且剪贴板为 `F:\projects\deskpet\DeepResearch\...md`，点击 `在文件夹中显示` 显示“已在文件夹中定位”，点击 `打开` 成功拉起 Windows `.md` 打开方式选择器。 |
| 2026-07-08 | **DeepResearch 查询质量二次复测 PASS ✅** — 针对“中文子问题 + site:...”搜索质量弱的问题重新测试：重启 Tauri 源码后端（日志确认 `Dev python=F:\projects\deskpet\backend\.venv\Scripts\python.exe backend_dir=F:\projects\deskpet\backend`），真实 simple_harness UI 在 `default` 会话发送“请调研一下俄乌最近的局势，给我带来源的简明结论”。UI 先出现 `调用 deepresearch`，数据库确认随后 4 条 `web_search` 参数均为英文独立查询：`Russia Ukraine war latest situation July 2026 Reuters...`、`Ukraine war latest battlefield situation July 2026 ISW...`、`UN Ukraine civilian casualties latest 2026...`、`NATO Ukraine aid latest 2026...`；判定脚本输出 `has_chinese_site=False`、`has_any_chinese=False`、`has_site=False`。同时工具结果抓到 ISW/AP/BBC/UNOCHA 等来源且多条 `fetcher=scrapling`。新增/复跑回归：`pytest backend/tests/test_deskpet_research_tools.py -q` 87 passed；`pytest backend/tests/test_deskpet_context_assembler.py -q` 42 passed。 |
| 2026-07-08 | **DeepResearch source packs 真机 UI 补测 PASS ✅** — 用真实 simple_harness UI 新建 UUID 会话 `a8204ba6-18df-4856-ae05-de3a8bdb5f9e`，粘贴“请调研一下俄乌最近的局势，给我带来源的简明结论”并点击发送；UI 出现 `deepresearch` 工具卡、报告文件卡和最终整理回复。日志确认 `name='deepresearch'`、`deepresearch_finalize_queued`，并出现 ISW/UN/Reuters/AP/BBC/Al Jazeera source-pack 查询；未出现后续独立 `name='web_search'` 工具调用。首轮手测发现 source-pack 查询由中文子问题拼 `site:` 导致命中质量弱，已改为 standalone authority-directed queries 并重跑 `pytest backend/tests/test_deskpet_research_tools.py -q` 87 passed。证据：`plans/manual-results-2026-07-08-deepresearch-source-packs/RESULTS.md`。 |
| 2026-07-08 | **DeepResearch source packs 默认开启 ✅** — 为 deepresearch 搜索计划增加 deterministic source packs：俄乌/Ukraine 高时效主题自动补 ISW、UN、Reuters/AP/BBC/Al Jazeera 定向搜索，金价/黄金主题补 LBMA、World Gold Council、Investing；查询去重、每子问题有上限、`[research].source_packs=false` 可关闭。`coverage.route` 新增 `source_packs_enabled` / `source_packs_hit` / `source_pack_queries`，skill 文案同步 source packs + Scrapling-first 抓取，并继续禁止外层手动 `web_search`/`web_fetch` 拼报告。验证：py_compile passed；deepresearch focused 87 passed；Scrapling/AgentLoop adjacent 16 passed；task-drift/research-sources/search-provider adjacent 69 passed；deepresearch+Scrapling focused 91 passed。 |
| 2026-07-08 | **deepresearch 成功后强制收束，不再默认补搜 ✅** — 用户截图确认 deepresearch 报告已保存后外层 AgentLoop 仍继续调用多条 `web_search`，根因是 deepresearch 结果只被当作普通 evidence，下一轮仍开放 `web_search/web_fetch` schema，模型会倾向补搜。修复：`AgentLoop` 新增 `_deepresearch_result_is_complete()`，兼容 v2 registry envelope 与 legacy 结果；当 `deepresearch` 返回 `ok=true`、有 `report_md` 且带 `citations` 或 `path/artifacts` 时，追加“deepresearch 已完成”系统收束提示，并排队下一轮 `tools=None` + `tool_choice=none`，让模型只能基于报告给最终答复。失败/空报告不触发，仍可继续补搜。验证：`py_compile agent/agent_loop.py` passed；`pytest tests/test_deskpet_agent_loop.py tests/test_agent_loop_sentinel.py tests/test_wi1_tool_choice.py -q` 21 passed；`pytest tests/test_agent_loop_pipeline.py tests/test_task_drift_fixb.py tests/test_research_scrapling_priority.py tests/test_deskpet_tools_web.py -q` 30 passed, 1 deselected。 |
| 2026-07-08 | **Scrapling 调研链路可观测性 + deepresearch 状态文案收口 ✅** — 用户反馈“deepresearch 已生成文件但还在处理”以及 UI 仍显示 `web_fetch`，复查日志确认外层工具名仍叫 `web_fetch`，但真实抓取已出现 `INFO:scrapling:Fetched...`；deepresearch 工具落盘报告后，外层 AgentLoop 还会继续综合/补充核验，因此旧文案误导为“任务已经结束”。修复：`web_fetch` 成功/回退结果都显式返回 `fetcher`，并把 `fetcher` 排在 `content` 前；`web_extract_article` 与 deepresearch `default_extract` 同步透出 `fetcher`；消息面板 deepresearch artifact 行改为“deepresearch 报告已保存，正在整理答复”。验证：`py_compile backend/deskpet/tools/web_tools.py backend/deskpet/tools/research_tools.py backend/main.py` passed；`pytest tests/test_deskpet_tools_web.py tests/test_scrapling_tools.py tests/test_research_scrapling_priority.py tests/test_js_render_flag_off.py -q` 22 passed, 1 deselected；`tauri-app` `tsc -b` passed。 |
| 2026-07-08 | **default 会话“只落库无回复”防卡死修复 ✅** — 用户在 `default` 里发送“请调研一下 俄乌最近的局势”后无反应；复查确认消息已写入 SessionDB（id=211），但后续没有 `assembler_task_classified/p5s2_chain_resolved/deepresearch`，说明主 `_run_chat` 在落库后、进入 agent loop 前被卡住。定位到 `_broadcast_default_chat_peers()` 注释写 best-effort，但实际逐个 `await peer_ws.send_json()` 且无超时；任一 stale/半死 peer WS 都可能把主聊天 task 卡在 user echo 广播，造成“数据库有用户消息但 UI/agent 没继续”。修复：peer 广播改为 `asyncio.wait_for(..., timeout=1.0)`，超时只 debug 记录并放行主任务。验证：`py_compile backend/main.py` passed；`pytest tests/test_main_task_scope_wiring.py tests/test_deskpet_session_db.py -q` 29 passed；重启 dev 后源码后端 startup complete，8100/5173 正常，message-panel/code/default control WS 均连接。 |
| 2026-07-08 | **Scrapling-first WebFetch / DeepResearch 抓取层 ✅** — 按用户要求把 Scrapling 从“金价特例工具”上提为网页抓取优先层：`web_fetch` 在保留 robots/rate-limit/block-cache 后真实页面抓取先调用 Scrapling，成功则直接返回 `fetcher=scrapling`，失败或疑似 blocked 页面再回退 httpx；deepresearch 的 `default_extract` 在真实运行路径先用 Scrapling 抓 HTML，再复用 trafilatura、JS render、Jina 与源质量过滤链路，抽取器标记为 `scrapling+trafilatura`。为避免测试 mock 网络误触真网，测试层显式关闭 Scrapling helper，并新增 web_fetch 与 deepresearch Scrapling 优先级回归。验证：`pytest tests/test_deskpet_tools_web.py tests/test_scrapling_tools.py tests/test_research_scrapling_priority.py tests/test_js_render_flag_off.py tests/test_research_sources.py tests/test_task_kinds.py tests/test_problem_pipeline_config.py -q` 66 passed, 1 deselected；`py_compile deskpet/tools/web_tools.py deskpet/tools/research_tools.py deskpet/tools/scrapling_tools.py` passed。 |
| 2026-07-08 | **Scrapling 金价抓取工具集成 ✅** — 按用户要求把 `D4Vinci/Scrapling` 直接集成进 simple_harness 工具层，而不是只依赖通用搜索。新增 `gold_price_lookup`：用 Scrapling Fetcher 抓取 Investing.com XAU/USD 与 USD/CNY 页面，抽取 `instrument-price-last`，返回美元/盎司、美元兑人民币、人民币/克估算价与来源 URL；新增 `scrapling_fetch` 通用抓取兜底工具，并把 `gold_price_lookup/scrapling_fetch` 加入 chat/research/web 工具暴露策略。依赖改为 `scrapling[fetchers]>=0.4`，PyInstaller spec 补 Scrapling/curl_cffi/browserforge 等 hiddenimports/datas。复盘 `8f577132-547c-41d6-b555-210aa746cbfd` 失败根因：通用 `web_search` 全引擎空结果，多个站点 robots/403/TLS 阻断，Investing.com 实际已能抓到价格但 agent 在 max_iterations 收敛前没有正常回传。验证：Scrapling live smoke 返回 XAU/USD 与 USD/CNY 并估算 CNY/g；`pytest tests/test_scrapling_tools.py tests/test_deskpet_tools_web.py tests/test_search_provider.py tests/test_task_kinds.py tests/test_problem_pipeline_config.py -q` 63 passed, 1 deselected；`py_compile` passed。 |
| 2026-07-08 | **消息气泡一键复制按钮 + 气泡选区 UI 收口 ✅** — 按用户要求在消息框底部补复制按钮：主消息面板的 user/assistant 普通对话把复制入口移到气泡下方，与时间同排显示；code 面板的 user/assistant/reasoning/slash/error/plan/skill/outline 等可读消息保留气泡下方复制入口；tool calling / tool result 轨迹不再显示复制按钮，避免工具卡片噪音。assistant markdown 复制源文本，保留代码块/列表/表格格式。复制控件改为纯 icon 微按钮（copy/check 图标，无文字标签），保留 tooltip/aria 反馈；用户消息气泡改为更沉稳的深蓝渐变，并为消息正文选区增加高对比青蓝高亮，解决鼠标拖选复制时看不清选中文字的问题。验证：`vitest run src/components/__tests__/MessageStreamMarkdown.test.tsx src/code-panel/ws.chat.test.ts src/message-panel/topicTitle.test.ts` 20 passed；`tsc -b` passed；`vite build` passed。 |
| 2026-07-08 | **新话题按钮逻辑与 UI 交互收口 ✅** — 「新话题」按钮语义改为输入为空时创建空白新话题，输入非空时显示“作为新话题发送”并把当前草稿作为新 UUID 会话的第一条用户消息；按钮有创建中禁用态与 loader 图标，控制通道发送失败时恢复草稿并显示错误，不再吞掉用户输入。后端在 `session_switched/task_session_started` 后给 originator 补发解析后 UUID session 的 `chat_v2_user_echo`，修复新话题首条草稿可能只触发 assistant、当前窗口看不到 user bubble 的断层；消息面板 `sessions_list` 过滤同步接纳 UUID，会继续保留旧 `task-*` 仅作历史兼容，避免新 UUID 会话被列表隐藏后 UI 仍露出旧 `task-default-*`。验证：`vitest run src/code-panel/InputBar.chat.test.tsx src/code-panel/ws.chat.test.ts src/message-panel/topicTitle.test.ts` 19 passed；`pytest backend/tests/test_main_task_scope_wiring.py -q` 6 passed；session 相关后端回归 38 passed；`py_compile backend/main.py` passed；`tsc -b` passed；`vite build` passed。 |
| 2026-07-08 | **新建会话 session_id 收口为 UUID ✅** — 按用户要求停止生成 `task-default-*` / `task-task-*` 这类可读但混乱的任务会话 id。`TaskSessionManager` 的 `/new`/`new_session` 路径改为生成标准 UUID，并维护父会话映射避免从 UUID 会话继续新建时丢失 default peer group；`SessionDB` 新增 `ensure_session(session_id, metadata)`，主聊天与语音新会话在广播/写消息前先写入 `sessions(id TEXT PRIMARY KEY)`，使 UUID 成为新建会话的 canonical 主键，`messages.session_id`、标题、删除、receipt 等继续引用同一个 UUID；消息面板错过切换事件时的兜底不再依赖 `task-` 前缀。旧 `task-*` 历史仍可读取/重命名/删除。验证：`pytest backend/tests/test_task_scope.py backend/tests/test_main_task_scope_wiring.py backend/tests/test_voice_task_scope.py backend/tests/test_deskpet_session_db.py -q` 35 passed；`py_compile` passed；`vitest run src/code-panel/ws.chat.test.ts src/message-panel/topicTitle.test.ts` 14 passed；`tsc -b` passed；`vite build` passed。 |
| 2026-07-08 | **消息面板会话交互增强 ✅** — 按用户截图反馈统一顶部标题与历史列表的会话名称展示，当前会话/历史项均优先显示用户可重命名的自定义名称；`session_id` 从名称文本中拆出，改为黄色等宽徽标并开启文本选中，便于复制；历史会话删除改为先弹确认框，确认后才发送 `session_delete`，默认话题使用“清空”文案避免误删。验证：`node node_modules/typescript/bin/tsc -b` passed；`node node_modules/vite/bin/vite.js build` passed。 |
| 2026-07-08 | **异步 PPT 完成通知未实时显示修复 ✅** — 用户反馈 `task-default-5` 的“俄乌战争局势”PPT 已生成但 session 里没有显示“做完/返回完成状态”。复查确认后端 SessionDB 已有 tool artifact 与 `✨ PPT 做好啦...deskpet-ppt-1783473814.pptx`，但消息面板实时链路未稳定收到异步 `ppt_pro` 后台完成事件，用户只能看到大纲/空闲状态。修复：`_ppt_notify_chat_bubble` 与 `_ppt_artifact_push` 改为携带目标 `session_id` 广播到所有 control 窗口，而不是只发给单个猜测 websocket 后再依赖 peer group 转发；artifact push 改为先落 SessionDB 再广播。新增后端回归覆盖“只有 default + message-panel-main、没有 task sid 直连时仍广播完成事件”，新增前端 `session_messages_response` 回归确保历史恢复包含 artifact tool_result 与最终完成文案。验证：`pytest backend/tests/test_ppt_outline_wiring.py -q` 8 passed；`vitest run src/code-panel/ws.chat.test.ts` 3 passed；`tsc -b` passed；重启 dev 后真实 Computer Use 选择 `task-default-5`，消息面板显示 `📊 deskpet-ppt-1783473814.pptx` 与 `✨ PPT 做好啦，已自动打开...`。 |
| 2026-07-08 | **消息面板发送无回复 + 切历史 session 空流修复 ✅** — 复现用户问题：真实 UI 在消息面板发送文本后只显示用户气泡和“思考中”，后端 `state.db` 不写入、日志无 chat 入站；点击顶部历史 session 时也可能显示“消息流为空”。修复：`InputBar` 普通聊天不再把实际 `chat_v2` 发送包进无效的 `chatLimiter`，`codePanelWS.send()` 改为返回发送结果，交互消息在控制通道未连接时立即失败并清掉 thinking，避免永久转圈；`codePanelWS` 复用 HMR/global OPEN socket 时会同步 connected 状态并 flush 当前 outbox；`MessagePanelRoot` 在 `activeSid` 变化时主动拉 `session_messages_load` + `context_usage_request`，切历史会话会回填 SessionDB。验证：新增 `InputBar.chat.test.tsx`；`vitest run src/code-panel/InputBar.chat.test.tsx src/code-panel/ws.chat.test.ts src/message-panel/topicTitle.test.ts src/stores/sessionsStore.test.ts` 33 passed；`tsc -b` passed；真实 Computer Use UI 复测：消息面板发送后后端出现 `POST https://chinzy.com/v1/chat/completions 200 OK`，UI 收到 assistant 回复并回到空闲；点击顶部 session 下拉切回“默认话题”后历史消息即时回填，不再空流。 |
| 2026-07-08 | **beta.9 release readiness audit: full build now works, release still blocked ⚠️** — Installed Visual Studio Build Tools C++ workload and completed a full Tauri release rebuild (`deskpet.exe` + NSIS) instead of reusing the old shell. Rebuilt the frozen backend into `F:\deskpet-build\dist` and confirmed the frozen runtime reaches `provider_registry_ready`, `p4_embedder_ready is_mock=False`, and `startup complete` under the longer 75s startup window. The new unsigned installer `DeskPet_0.6.0-beta.9_x64-setup.exe` is 2026-07-08 02:46 and silently installs a new Rust shell plus the new 00:42 frozen backend. Also fixed a release-window visibility bug in `window_geometry`: when startup monitor lookup fails, the main window now falls back to a safe `(100,100)` position instead of remaining offscreen. Do **not** publish beta.9 yet: `.env` contains `TAURI_SIGNING_PRIVATE_KEY` but its value length is 0, so the beta.9 `.sig` is still the old 2026-06-28 file; Computer Use still cannot target the transparent/titleless main window and the installed app did not produce a fresh backend startup log during E2E attempts. Current shareable build remains beta.8 until a real signing key is supplied, the installer is signed, `latest.json` is generated from the new `.sig`, real UI E2E passes, and GitHub/COS upload is completed. |
| 2026-07-07 | **Agent harness drift/queue bugs fixed ✅** — Review-driven harness cleanup: repo `config.toml` now ships `[tools.verifier].verify_gate_mode="strict"` so effective startup config matches `AppConfig()`/STATUS no-gray contract; `[skills]` subtables without `knowledge_enabled` now preserve the default ON knowledge injection; `await_subagents` now collects already-completed runs when `run_ids` is omitted and consumes completion-queue entries for explicitly awaited runs to avoid duplicate AgentLoop injection; `[features.problem_pipeline].analysis_model` in repo config now ships `deepseek-v4-pro` to match the tested dataclass default. Added focused regression guards for repo config effective values, skills subtable defaults, and subagent await/queue semantics. Verification: per-fix focused pytest passed; harness regression set `50 passed`. |
| 2026-07-07 | **PPT/图片生成 401 降级模板根因修复 ✅** — 用户追问“AI 配图服务不可用，之前可用”为何发生；复查本轮日志确认 `ppt_pro _render_pro start image_mode=True` 后连续请求 `https://chinzy.com/v1/images/generations` 全部 `401 Unauthorized`，因此 `ppt_pro gate reachable=False n_ok=0` 并降级模板。根因不是 `doubao-seedream-4.0` 模型下线，而是 `image_tools._resolve_endpoint()` 仍走旧 `llm_runtime.json`/`resolve_cloud_api_key()` fallback，未接入登录后的 `provider_registry relay-cloud` key；主聊天/DeepResearch 已拿到当前 key，所以同会话聊天可 200、图片却 401。修复：`image_tools` 新增 `set_endpoint_resolver()`，运行中后端优先使用注入的 provider-registry base_url/key；`main.py` 新增 `_refresh_image_endpoint_resolver()` 并修正 relay provider 解析为 `_reg.get_entry(RELAY_PROVIDER_ID)`，保证图片/PPT 生图与当前登录 relay provider 对齐。验证：`py_compile backend/main.py backend/deskpet/tools/image_tools.py`；`pytest backend/tests/test_image_config_section.py backend/tests/test_generate_image_tool.py backend/tests/test_image_probe_classify.py -q` 21 passed；PyInstaller frozen backend 重打并同步到 Tauri debug；桌面版重启日志确认 `image_endpoint_resolver_wired`、`relay_provider_ensured ... key_fp=6d8aeceb`、`Application startup complete`。 |
| 2026-07-07 | **PPT 大纲确认后“无回复”实为任务会话未切回修复 ✅** — 用户再次实测确认大纲后左侧无任何后续回复；复查真实运行栈确认后端实际已完成两次：`task-default-2` 最新轮次在 `ppt_outline_decision_resolved` 后跑完 `ppt_pro _render_pro image_mode=True pages=10`，9/10 张 AI 配图 200、1 张 400 后按纯色版式兜底，文件已落盘 `tauri-app/src-tauri/target/debug/userdata/OutPut/PPT/deskpet-ppt-1783421197.pptx`，会话库已有“✅ 大纲已确认，开始生成…”、“有 1/10 张配图没生成成功…”、“✨ PPT 做好啦…”和 artifact tool_result。根因在前端消息面板：本地 `activeSid` 只在 `task_session_started/session_switched` 瞬时事件切换；若面板窗口错过该事件，后续 `chat_response/tool_result` 虽写入 `task-*` store/DB，UI 仍停留在 `default`，用户体感就是确认后没回复。修复：`MessagePanelRoot` 增加对全局 `useSessionsStore.active_sid` 的同步，并在收到 `chat_response/chat_v2_final/tool_call/tool_result/ppt_outline_proposed` 且 payload session_id 为 `task-*` 时补切到任务会话。验证：确认最新 PPT 文件存在 3,050,610 bytes；`node node_modules/typescript/bin/tsc -b` passed；`node node_modules/vite/bin/vite.js build --mode relay` passed。 |
| 2026-07-07 | **PPT Pro 已生成但消息面板不显文件卡修复 ✅** — 用户实测“俄乌战争局势”PPT 确认大纲后看起来仍未生成；复查真实运行栈确认后端已收到 `ppt_outline_decision`、`ppt_pro render done(template) ok=True`，文件已落盘 `tauri-app/src-tauri/target/debug/userdata/OutPut/PPT/deskpet-ppt-1783409176.pptx`，会话库也已有“PPT 做好啦”与 tool artifact JSON。根因是前端展示链路：实时 `tool_result` 只把 `result` 文本塞进 store，丢掉 `artifacts[]`；左侧 `MessageStreamPanel` 又把工具结果压成一行“工具完成”，没有渲染 `ArtifactCard`，所以用户体感像没生成。修复：`code-panel/ws.ts` 对含 artifacts 的 tool_result 保留完整 envelope，历史回放从 JSON 反推 tool 名；`MessagePanelRoot` 透传 tool 原始结果；`MessageStreamPanel` 对 artifacts 渲染文件卡（打开/文件夹等 action 沿用现有 ArtifactCard）。验证：确认 PPT 文件存在 51,957 bytes；`tauri-app` 前端 `tsc -b` passed。 |
| 2026-07-07 | **桌宠 supervisor agent 默认关闭 + 开关持久化 ✅** — 用户追问此前要求关闭的 supervisor agent 为何仍在运行；复查发现出厂 `config.toml [supervisor].enabled` 仍为 true，前端 Settings 默认也按 ON 初始化，且 `supervisor_toggle` 只改运行时 watchdog、不写回 config，导致重启后继续启动。修复：`config.toml` 出厂默认改 `enabled=false`；`backend/main.py` 缺省兜底从 True→False，并新增 `_persist_supervisor_enabled()`，运行时开关只写 `[supervisor].enabled` 且 ack 返回 `persisted`；`SettingsPanel` 默认改 OFF；当前桌面版 `target/debug/userdata/config.toml` 同步改为 `enabled=false`。验证：`py_compile backend/main.py`；`pytest backend/tests/test_config.py backend/tests/test_p5s1_watchdog.py backend/tests/test_p5s1_supervisor.py -q` 42 passed；前端 `tsc -b` passed；PyInstaller frozen backend 重打并同步；桌面版启动日志确认 `p5_supervisor_disabled_via_config` + `Application startup complete`，未再出现 `p5_supervisor_watchdog_started`。 |
| 2026-07-07 | **PPT Pro 大纲卡卡住/失忆修复 + 深度调研质量门 + backend 重新打包启动 ✅** — 用户实测「俄乌战争局势」PPT 只回复“开始调研并拟大纲”，一直没有大纲确认卡；本地会话库确认旧轮次先写入 `PPT 没做成：LLM HTTP 401 Unauthorized`，随后又写入安抚性回复。根因三段：① 前台聊天已走登录后的 `relay-cloud` provider registry，但 PPT/DeepResearch 后台大纲链路仍复用启动时旧的 `research_tools` live LLM callable → 后台 401；② 大纲卡只广播给前端/写 `ppt_outline_history`，未作为 assistant 消息落库，追问“你给我大纲了吗”时上下文可能拿不到大纲正文；③ 初修为解决“久等无卡”曾把 research timeout 收到 45s 并允许 no-research outline，用户指出这会破坏“深度调研版 PPT”承诺，已追加修正。最终修复：`backend/main.py` 启动、`/config/cloud`、provider ensure/update/logout 后统一刷新 research live LLM 与 reranker；`_ppt_outline_propose` 将 `PPT 大纲确认 · ...` 正文写入 `messages` 并入 vector worker；`backend/deskpet/tools/ppt_tools.py` 默认 research timeout 恢复深度调研友好的 360s，新增 `pro_allow_no_research_outline/allow_no_research_outline` 且默认 False，调研无结果时不生成大纲，改为明确提示“先不生成大纲，避免把普通知识当成深度调研结论”，只有用户显式要求跳过调研才可开启。验证：`py_compile main.py/ppt_tools.py`；`pytest backend/tests/test_config_cloud_endpoint.py backend/tests/test_p5s2_provider_registry.py -q` 47 passed；`pytest backend/tests/test_ppt_outline_wiring.py backend/tests/test_ppt_pro_content.py backend/tests/test_ppt_pro_exec.py -q` 35 passed。PyInstaller frozen backend 重打并同步到 `tauri-app/src-tauri/target/debug/backend/deskpet-backend.exe`，桌面版已重启，日志确认 bundled backend + `research_live_llm_refreshed ('https://chinzy.com/v1','gpt-5.5')` + `Application startup complete`。 |
| 2026-06-29 | **桌宠消息大框 assistant 回复按 markdown 渲染（含 GFM 表格）✅** — 修用户实测发现的缺口：消息面板（`components/MessageStreamPanel.tsx` 的 `ChatRow`）此前把 LLM 回复当**纯文本** div 渲染（`{text}`）→ ```code```/`**bold**`/列表/表格标记全裸露，而 code 模式（`code-panel/MessageBubble.tsx` 的 `AssistantBubble`）一直用 react-markdown 渲染，两边不一致。把那套 react-markdown + 自定义 components（代码块高亮、本地文件链接走 `artifact_open`、列表/段落间距）抽成共享组件 [`components/MarkdownMessage.tsx`](../tauri-app/src/components/MarkdownMessage.tsx)，两处复用；assistant 走 markdown、user 输入保持纯文本（保留换行不被误解析）。**真测（windows-mcp）时发现额外缺口**：GFM 表格 react-markdown 默认不渲染（code 模式同样缺）→ 加 `remark-gfm` 插件 + 深色主题表格样式（描边/表头底色/横向滚动）。回归测试 [`MessageStreamMarkdown.test.tsx`](../tauri-app/src/components/__tests__/MessageStreamMarkdown.test.tsx) 4 测（代码块→`<code>`无裸围栏 / 加粗+列表→真元素无 `**` / 表格→真 `<table>`无裸管道 / user 纯文本）+ `tsc --noEmit` 全绿。**真机 E2E 已验**：同一金价 markdown 内容（原 bug 截图同源）现标题/加粗/代码块/表格全部正确渲染。 |
| 2026-06-28 | **frozen 后端 BGE-M3 embedder 静默降级 mock 修复（预存于所有发布版）✅** — 装机版 PyInstaller 后端启动时 embedder worker 加载失败、静默回退 mock embedder → 记忆/向量召回退化（≥beta.4 全中招，与空 Bearer 无关）。**在真二进制上逐层定位根因**：`from FlagEmbedding import BGEM3FlagModel` 在 frozen 下连撞三层——① FlagEmbedding 1.3.5 推理 import 链里 `abc/finetune/embedder/AbsDataset.py:5` 有一句**裸 `import datasets`**（纯训练依赖，spec 故意 `excludes` 以免拖 ~150MB pyarrow/pandas 并曾崩构建期分析）；② FlagEmbedding `__init__` eager import reranker 全家桶，MiniCPM modeling 定义期经 transformers docstring 装饰器调 `inspect.getsource` → frozen 无 .py 源码 `OSError`；③ 构建 tokenizer 时 `tokenizer_class_from_name` 动态枚举 `transformers.models.*`，XLMRobertaTokenizerFast 挂 MetaCLIP-2 名下、遍历撞 frozen 未打包的 `metaclip_2` 子模块 `ModuleNotFoundError`。**修复（单点零体积零 spec 改动）**：`embedder_worker._apply_frozen_compat()`（仅 `sys.frozen` 生效，dev no-op）在 import FlagEmbedding 前——注入只含 `Dataset` 的 `datasets` stub 到 `sys.modules`（dunder→AttributeError 让内省优雅降级、真 `__spec__` 过 `find_spec`、训练符号→RuntimeError）+ 容错包两处 transformers 调用。**真验**：全新 thin-bundle exe 裸跑 worker `is_mock=False`+真 encode 归一化 1024 维向量；完整 backend 启动 `probe-embedder.ps1` 日志 `BGE-M3 subprocess worker ready ... attempt=1` / `p4_embedder_ready is_mock=False` / 无 `No module named` / 无 mock 降级。单测 `test_frozen_embedder_compat.py` + 相关共 21 passed。证据 [01-acceptance-evidence](../plans/2026-06-28-frozen-embedder-datasets-fix/01-acceptance-evidence.md) · 根因 [00-ROOT-CAUSE-AND-FIX](../plans/2026-06-28-frozen-embedder-datasets-fix/00-ROOT-CAUSE-AND-FIX.md)。⏳ **发版未做**（bump+NSIS+签名+GitHub/COS 等用户确认）。 |
| 2026-06-28 | **装机版 userdata 路径安装目录绑定 + 空 Bearer 崩溃护栏 ✅** — 定位并修复用户装机版（F:\deskpet 自定义目录）聊天报 `Illegal header value b'Bearer '`（httpx `LocalProtocolError`）。**根因（非 keychain bug）**：relay-cloud provider 登录时才写进 `<user_data>/config.toml`，而 Rust（`paths.rs` 要求 `userdata/` 已存在才认 portable）与 Python（`paths.py` 主动 mkdir）两套 userdata 解析条件不一致、`spawn_once` 又**未注入 `DESKPET_USER_DATA_DIR`** → config.toml 路径**跨会话漂移** → `get_chain()` 读到空 → 回退 legacy 空 key 拼出非法 `Bearer `。（证据链：main.py `resolve_api_key(...) or "ollama"` 决定 keychain 读不出会发 `Bearer ollama` 而非空 → 空 Bearer 只能来自 legacy 兜底。）**修复 6 Phase**（codex gpt-5.5 并行实现 + 3 轮评估 94→97→**100%**）：① `paths.py` portable 解析确定化（去盘邻居回落、只认 `<install>/userdata`）+ 记忆化 + `.deskpet-portable` sentinel 固化绑定；② **Rust 单一事实源**——`portable_userdata_dir` create-then-return + `spawn_once` 注入 `DESKPET_USER_DATA_DIR`，Python priority-1 命中，双解析归一；③ `config.py _recover_orphaned_endpoints` 启动自愈（canonical 无可用 endpoint 时从 AppData/安装目录迁回 + `.pre-recover-bak`，存量用户无需重登）；④ `providers/openai_compatible.py::_client` 空/占位（含 `ollama` 非本地）key 抛友好 `LLMProviderError(error_class=empty_api_key)` 不拼空 Bearer + agent_loop chain 全失败 error_class 透传 ErrorEvent；⑤ 可观测 `config_loaded portable/env_pinned` + `provider_registry_ready n/enabled/ids`；⑥ spec 钉死 keyring frozen 后端。**单测**：新增 3 套（paths 记忆化/config 自愈/空 key 护栏 12 测）+ conftest autouse 隔离路径缓存，相关 99+ 全绿、全量 **3628 passed**（8 失败 git 验证为 pre-existing flaky/无关）。**windows-mcp 真机**（源码 backend + 隔离 userdata + 真点击+剪贴板中文）：`[backend_launch] Dev python` 跑我的源码 ✓ / `config_loaded portable=False env_pinned=True` ✓（Rust→Python env 归一）/ `provider_registry_ready` ✓ / **`LocalProtocolError` 全程 0 次**（多轮发送+401+连接失败均不崩→友好降级）★headline / **正常 agentic 聊天端到端跑通**（agent 调 `deepresearch` 工具→无回归、护栏不误伤有效 key）★。显式 empty_api_key 文案 + frozen 路径绑定走单测/重打包兜底（dev 机 companion 走 local_llm 恒有注入 key、token 对 /models 401，诚实标 env 约束）。证据 [RESULTS](../plans/manual-results-2026-06-28-userdata-path/RESULTS.md) · plan [2026-06-28-userdata-path-binding-fix](../plans/2026-06-28-userdata-path-binding-fix/00-PLAN.md) · 手测 [testcase](../testcase/2026-06-28-userdata-path-binding/manual-test.md)。**✅ 已发布 0.6.0-beta.8**（bump 4 文件 → backend bundle CPU venv 重打含 Python 修复 + cargo release 编 Rust + NSIS 386MB + Git Bash 单独签名；GitHub release `v0.6.0-beta.8` isPrerelease=false 资产齐全 + COS 双端 `latest.json=beta.8`、安装包 HTTP 200）→ 存量 beta.7 用户（含 F:\deskpet）设置→检查更新即可升级拿修复。frozen 路径绑定最终 E2E 可在更新后真机验。 |
| 2026-06-27 | **FactExtractor 内容哈希幂等去重（Layer 1）+ memory_write 工具去重 ✅** — 修真测发现的「重发完全相同的话 → facts 表累积重复事实」缺口（真测同场景修复前 +3，修复后 **+0**）。① `memory_write` 工具改时间戳 key→内容哈希 key + `find_active` touch 去重；② `FactExtractor.process_message` 加 **Layer 1 内容哈希幂等**——在 LLM 抽取前按归一化内容哈希短路（`_dedup_lock` 锁内抢占登记防并发 TOCTOU / LLM 异常+坏 JSON+持久化异常撤销占位 / 空数组与成功后刷新完成时刻 / `clear_content_cache` 自愈钩），命中打 `facts_extract_skip_dup` info 日志；config `extract_content_dedup` 默认 ON。**plan 经 4 轮对抗 + 竞品对标**（Claude 内容寻址 ID/写时去重、mem0 向量+LLM NOOP、Zep 双时态、Hermes 精确重复拒绝 → L1 内容哈希被 Claude+Hermes **双重验证为核心**；L2a token-overlap 实算证伪对短事实不可靠 → 降 Phase 2 spike）。**codex gpt-5.5 并行实现 + 3 轮评估至 100%**；单测 13 个全绿（含并发 T6/坏 JSON 撤销/空数组刷新/summarizer 不短路）+ 广义套件 109 passed；**windows-mcp 真机 ★ 全 PASS**（TC-1 三证 C0=363→C1=366→重发 C2=366+skip 日志 / 归一化 / 不误短路 / flag OFF BC / TTL 边界）。证据 [RESULTS-FACTEXTRACTOR-DEDUP](../plans/manual-results-2026-06-27-enable-flags/RESULTS-FACTEXTRACTOR-DEDUP.md) · plan [2026-06-27-factextractor-dedup](../plans/2026-06-27-factextractor-dedup/00-PLAN.md) · 手测 [testcase](../testcase/2026-06-27-factextractor-dedup/manual-test.md) |
| 2026-06-27 | **测试阶段全量点亮已开发能力（不灰度）✅** — 按 [`CLAUDE.md` §测试阶段：能力即开即用](../CLAUDE.md) + [HANDOFF](../plans/2026-06-26-agent-harness-alignment/HANDOFF-enable-flags.md)，把"已开发完成但出厂默认 OFF"的 A 表能力一次性翻 ON（路径1 改 `config.py` dataclass 默认 + config.toml 配套）。**33 个 flag**：`[memory.v2]` 语义事实记忆栈 17 个（facts_extract/rerank/enhanced_retriever/chunking/query_rewrite/reflection/cross_key_merge/memory_forget/entity_path/episodic_to_semantic/feedback_loop/goal_facts/light_write/persona_inject/goal_facts_hook/curation_nudge/auto_learnings）+ `[features]` 12 个（slash_commands/goal_mode/agent_parallel/plan_confirm_gate/preference_memory/subagent_driver/agent_team/subagent_nonblocking + 压缩四件套 ctx_observability/adaptive_compact_pct/summary_quality_loop/microcompact_size_aware）+ `[skills]` 3 个（knowledge_enabled/auto_disclosure/codify）+ `[tools.verifier].external_evaluator`。**关键修**：仓库根 `config.toml` 出厂种子里 `[memory.v2]` 有显式 `false`（feedback_loop/rerank/chunking/query_rewrite/reflection）会盖过 dataclass 默认坑新装用户 → 已全部翻 true；`_MIGRATABLE_SECTIONS` 追加 `("features",)`/`("skills",)`/嵌套 skills 段使新默认回灌存量 config。**B 表仍 OFF**（注明原因）：`workspace_memory`(code)/`forget.enable_natural_language`(危险无护栏)/`run_build`/`run_tests`(code)/`plan_read_only`(归 WI-1.2)/artifact 信封(未实装)。**验证**：全套 `pytest -m "not live"` **3603 passed**；翻 ON 引起的 18 个"陈旧默认=OFF 断言"契约测试全部精确更新到测试阶段新契约（g4_flag_matrix 重写为 A 表点亮/B 表保 False 等）；剩余 6 个失败经 git-stash 验证为 master 上 pre-existing（web_search 解析器×2/compaction wiring×2/artifacts 测试污染×1/agent_parallel 计时 flaky×1，与本次改动无关）。⚠️ 真机 windows-mcp 抽测待跑（goal_mode/knowledge_enabled/facts_extract）。 |
| 2026-06-27 | **v0.6.0-beta.7 发布上线 — 内嵌 Live2D 人物形象资源（COS + GitHub 双端验证）✅** — 修复历史发布只内嵌 hiyori、默认形象 estella 缺失致装机后桌宠回退占位图（程序化简笔画）的问题：把主 checkout 策展好的 6 个 Live2D 形象（estella 默认 + hiyori + Azuki-san + HoshinoAi + #Free# Snow Leopard + Estella-DG，~81MB）提交进构建树并打包，设置面板「桌宠形象」下拉可切换全部（vite `petModelsManifestPlugin` 扫目录生成 `models.json`，名字带空格/`#` 已 `encodeURIComponent`）；排除残缺无 `model3.json` 的 Design_genius_White。授权由产品方确认。**backend 自 beta.6 未变 → 复用 beta.6 瘦 backend bundle 免 PyInstaller 重打**；安装包 ~347MB（+43MB = 81MB 模型经 LZMA 压缩）。COS + GitHub 双端 `latest.json` **均报 `0.6.0-beta.7` 且签名一致**，`prerelease=false`。签名仍走 Git Bash + 空口令 + `< /dev/null` 防挂死（详 [release/README.md §5](../release/README.md)）。 |
| 2026-06-27 | **v0.6.0-beta.6 发布上线（COS + GitHub 双端验证）✅** — 基于修复后 master 出 beta.6 瘦包（~292MB NSIS，模型外置 COS）签名发布：COS 国内主源 + GitHub 备源 latest.json **双端均报 `0.6.0-beta.6` 且签名一致**，GitHub release `prerelease=false`（updater `/releases/latest/` 才认）。内容=relay 中转站账户收编完善(A–E)+冷启动收编 bug 修复(`37e40526`)+模型名/上下文档位显示修复+输入条两排式/消息面板极简重设计+`/goal` 任务工具动态可见性修复。**🐛 顺手修 master 漏提交 bug(`63e49a58`)**：上一工作流提交了 `visible_when` 调用方(main.py)+测试却漏提交实现(registry.py)→`goal_task_*` 4 工具静默不注册→`/goal` 任务图哑掉（302 测试转绿）。**踩坑**：签名私钥是 minisign **加密**私钥，PowerShell 传空口令不可靠致 `tauri build` 内嵌签名静默卡密码 prompt→改 Git Bash `npx tauri signer sign … < /dev/null` 单独签（详 [release/README.md §5](../release/README.md)）。 |
| 2026-06-26 | **relay 本地 apikey + provider 收编完成 + 真机端到端 PASS ✅** — 把 relay 登录从「旁路改单例 local_llm」收编进 `LLMProviderRegistry`，作为 `relay-cloud`(source=relay) 行被设置面板统一管理；顺手关闭 §5 P1 账号脱节。**A-E 五 WI 全实现**（codex 并行 + 每阶段子代理评估 100% + 真机循环）：WI-C 账户余额 ¥→USD bug 修复（陈旧硬编码，钱包早 USD 本位，真机 `$718.82`）· WI-1/2 registry 加 `source`/`account_ref` + `ensure_provider`(幂等/抢默认/key缺失强铸/priority去重) + WS `settings_providers_ensure`/`relay_logout`(抽 `relay_provider_ops` 可测，日志只打 key_fp) · WI-B device key 复用三态(`/v1/providers` reuse/meta/force + 按mode分槽dedup + prefix自愈降级 + 冷启动载缓存) · WI-3 `relayProviderRegistration`(单例+inflight串行+lastEnsured防TOCTOU+recover熔断+幂等四问[行在/账号/key在/base_url] + App.tsx 接线) · WI-4 relay 受限三态 UI(徽章/选默认/启停/拖拽/重置key按钮→recover) · WI-5 结构化错误(403 INSUFFICIENT_BALANCE[PR-1前 FORBIDDEN过渡] + 402 嵌套 + 401 INVALID/EXPIRED_TOKEN，仅 source=relay 分类) · WI-6 flag(`RELAY_MANAGED_PROVIDER`/`[features].relay_managed_provider`)+登出删key。**对接中转站 handoff**（不要长期 key 端点，改 `/v1/providers` 复用，回答 3 澄清：¥是陈旧bug/错误两表面/prefix 12字符待PR-6）。单测全绿(auth 127+registry 39+components 114+errors 14+relayErrorText 5)。**真机 windows-mcp E2E**：真坐标点击+真键盘——账户面板 USD★/手填 provider CRUD BC★/relay-cloud 收编出现+relay 徽章+重置key 按钮★/聊天真走 `p5s2_chain_resolved`(registry chain)→`chinzy.com/v1/chat/completions 200 OK`★。⚠️ device key 复用完整真测 + 401/403 自愈真测待中转站 PR-1/PR-6 灰度。证据 `plans/manual-results-2026-06-26-relay-provider-wiC/`。**🐛 登录测试补揪+修一个真 bug（`37e40526`）**：冷启动时 relay login 事件(restoreSession/auto-login)在 control WS connect **之前** emit → `registration.ensure` 在 'no channel' 中止 → **真实用户冷启动收编根本不触发**（之前真测'PASS'是边改边测的 **HMR 反复重挂掩盖**了该竞态）。修：App.tsx 加 connect-trigger(ws→connected+authed 重发 ensure，幂等)+`ensureOnce` channel 检查移到 syncDeviceKey 前(no channel 不浪费轮换)。**冷启动真机复验**：删空 config.toml relay-cloud→fresh 启动→`relay_provider_ensured` 精确 1 次(非 HMR churn)+relay-cloud 从零收编回+聊天 `chinzy 200 OK`+无 [reg] 警告（证据 `EVIDENCE-coldstart-fix.txt`）。 |
| 2026-06-26 | **BUG-B 意图路由 followup 3 阶段全完成 + windows-mcp 最终验收 ★5 全 PASS ✅** — 在七步流水线 P0(真问题误判闲聊短路)已修基础上，解 RESULTS §8.2 残留：**Phase 1 闲聊快路径**——新增共享词法模块 [`lexicon.py`](../backend/deskpet/agent/lexicon.py)（整句锚定 `is_obvious_chitchat` + 否决优先，R2 决策否决用问号 `[?？]` 非裸"吗"以放行整句"在吗在吗"），`intent_triage.analyze()` LLM 前插 allowlist 分支命中→`intent_triage.allowlist_hit`+短路**0 次 LLM**，`_safe_card` chitchat→factual_qa 修取证门漏洞（WI-2b）。**Phase 2 复活组装期 classifier**——`main.py` 注入 `OpenAICompatibleAgentLLM` shim（llm_timeout_s=8s 适配 gpt-5.5 thinking）+ classifier default `chat`→词法地板 fail-closed（确定寒暄→chat/code信号→code/否则→task，复用 lexicon），真 code/debug 问题不再恒拿 `chat` bundle。**Phase 3 收口**——WI-9b 预分析 prompt 去 hint 漂移 + WI-8a `_TASKTYPE_TO_PROBLEM` 单一来源护栏注释（仅 safe-fail fallback、IntentTriage LLM 唯一权威、禁接回当 hint 防 BUG-C；全量合并 deferred）。**每阶段循环**：单测全绿（intent_triage 43 + classifier/assembler 116）+ 子代理评估 100% + 手测文档 + windows-mcp 真机。**最终验收 ★5 全 PASS**：BUGB-1 `factual_qa short_circuit=False`✓/BUGB-2 中文 debug `debug short_circuit=False`✓/BUGB-3 闲聊 `allowlist_hit` 0 LLM✓/BUGB-4 坏 analysis_model `llm_failed`+不短路+裸 ReAct 答✓/BUGB-6 真 code `assembler_task_classified task_type=code`✓。收敛标准达成（★全过+组装 task_type 对真 code/debug 不再恒 chat+闲聊 0 LLM）。证据 [final RESULTS](../plans/manual-results-2026-06-26-bugb-final/RESULTS.md) · [plan](../plans/2026-06-25-bugb-intent-routing-fix/00-PLAN.md) · 手测 [P1](../testcase/2026-06-26-bugb-phase1/manual-test.md)/[P2](../testcase/2026-06-26-bugb-phase2/manual-test.md)/[final](../testcase/2026-06-26-bugb-phase3-final/manual-test.md) |
| 2026-06-25 | **七步流水线 Sprint2 完成 + WI-5(b) 默认配置全量 ★ 真测 10/10 PASS（上线门通过）✅** — Sprint2 经 R1/R2 对抗挑战至 EXECUTABLE-AS-IS，按 Y 方案重构（WI-1 取消闲聊短路、每条走 deepseek 预分析 + WI-2 收敛止损 + WI-4 默认 deepseek）。**真测抓修 + 落地多项**：① `intent_triage` safe-fail **绝不短路** + JSON 净化控制字符（`16758f8b`）；② **WI-4-C 预分析稳健化**：非流式预分析保留 strict schema（避 relay `stream+json_schema` 空 body，probe 4-6s 稳）+ 剥 thinking 模型 `<think>` CoT 前缀 + `_parse_contradiction` 防御性 int/float（防 LLM 乱填数值崩 `run_pre_loop`）。**默认配置 windows-mcp 全量 ★**：TC-1/2/3/4/5/9 + IDEM-1/3/4/5/6 全绿（闲聊短路·取证门 glob/grep·抓主要矛盾·异体自检·收敛止损 `error_max_turns` 诚实报告·kill-switch BC·澄清重启持久化·per-run 隔离·失败自愈）。**真测深挖 2 个 relay 上游问题**：(a) 账号脱节 P1（cloud-llm slot 用旧耗尽账号 key，登录从不同步）→ `.env` 固定 key 经 launcher env 注入绕过，followup [relay-cloud-key-sync](../plans/2026-06-25-relay-cloud-key-sync-followup.md)；(b) 账号并发上限过低 `Concurrency limit exceeded` → 诊断报告 [RELAY-ISSUE-REPORT](../plans/manual-results-2026-06-25-problem-pipeline-prod/RELAY-ISSUE-REPORT.md)→relay 侧已调高。证据 [RESULTS.md](../plans/manual-results-2026-06-25-problem-pipeline-prod/RESULTS.md) |
| 2026-06-24 | **七步问题处理流水线实现 + 真机 windows-mcp E2E 核心 PASS ✅** — 按毛选方法论把"收到问题→处理"落成显式七步（Step1+3 预分析合并/Step2 取证门控/Step4 弹钢琴/Step6 异体自检/Step7 收敛止损，仅 Companion）。**子代理并行实现 8 WI**（WI-0 config+context / WI-1 IntentTriage+编排器 / WI-4 三闸 / WI-3 plan companion / WI-6 agent_loop 接入 / WI-5 main.py 编排）；5 新模块 + 5 改造，flag off 全 BC（kill-switch）。**56 单测全绿** + 关 flag 2300+ pytest 不回归。子代理评估完成度 100%（VERDICT: COMPLETE）。**真机 E2E**（SendInput 真点击+UIA Type 真中文输入+截图+log grep）：TC-1 闲聊短路 0 LLM★/TC-2 取证 glob★/TC-3 抓主要矛盾★/TC-4 异体自检 strict★/TC-6 澄清多轮不断裂/TC-7 合并证明/TC-9 kill-switch 三闸全 None★/TC-10 safe-fail 全 PASS（TC-5 止损 best-effort 单测覆盖、TC-8 code 模式 env-limited）。**真机抓修 BUG**：预分析超时 6s→30s。证据 [RESULTS](../plans/manual-results-2026-06-24-problem-pipeline/RESULTS.md)。 |
| 2026-06-24 | **Code 模式入口暂关闭（聚焦主线程 Companion）+ 问题处理流水线 plan 定稿 EXECUTABLE-AS-IS ✅** — 产品侧关闭 Code 模式入口（`Toolbar.tsx CODE_MODE_ENTRY_ENABLED=false` + `App.tsx` 抑制 `code_mode_suggest`，翻 true 即恢复），`tsc -b` 通过 + **windows-mcp 真机截图确认工具栏 terminal 按钮消失**（证据 `plans/2026-06-24-problem-handling-pipeline-maoxuan/exec/toolbar-crop.png`）；待主线做实后另开 plan 优化 Code 模式重新上线。**问题处理流水线 plan**（毛选方法论锚的显式七步 + 取证门控/异体自检/收敛止损三道闸，只作用 Companion 主线）经 **5 轮子代理对抗迭代**收敛至 EXECUTABLE-AS-IS（codex 撞 Windows ConstrainedLanguage 沙箱墙 err1223 → 回退内置子代理）；尚未实现，待执行：[plan 目录](../plans/2026-06-24-problem-handling-pipeline-maoxuan/)。 |
| 2026-06-24 | **PPT 惊艳生图路径真机 E2E PASS × doubao-seedream-4.0；gpt-image-2 全面下线 ✅** — relay 下线 gpt-image-2（`8cb6b3d9`），图像默认切 `doubao-seedream-4.0`（真链路实测可用，`image_tools.py:44`）；`ppt_pro` 惊艳生图路径端到端跑通真机 PASS（`719a0b49`）；SKILL.md/注释/plan 清理残留 gpt-image-2 引用 + depth 不再作 LLM 参数（`58ee7a08`/`228a3d59`）。 |
| 2026-06-24 | **上下文管理 — 三处盲区修复 (Risk 1/2/3)，常见场景生产可用 ✅** — `ffd4f748` 修三处盲区（调查报告 `63683121`：3 针对性真测 + Risk1/2/3 处置）。结论按报告原口径：**主力（检索增强截断 + agent 回读）真机证明稳、常见场景可上生产**；B2 LLM 压缩 + 95% BLOCK 闸为休眠兜底（不影响安全）。⚠️ 诚实保留两处窄风险：单会话 256+ 次截断后不可重跑 stdout 的 ref 被 LRU 淘汰；BLOCK 闸分母只数 working_messages 漏算 base（小 window+大 schema 模型下可能不响）。 |
| 2026-06-24 | **dev 数据路径修复 — 源码跑时数据落 repo `backend/userdata`(G:)，不再污染 C: %AppData% ✅** — `8ada7269`，对齐 [[reference_dev_userdata_dir_on_g]] 记忆；dev 模式从源码跑时数据目录落仓库内，避免掉 C: 盘满。 |
| 2026-06-23 | **上下文优化收尾 — 1A-4 memory 归并 + HM-1 升 strict + TG-1 真机 PASS ✅** — 承接 [完整未处理清单](../plans/2026-06-22-context-and-agent-optimization/exec/REMAINING-CHECKLIST.md)（经子代理对抗挑战迭代）。**1A-4**：合并工具稳健性 3 记忆为 `feedback_tooling_robustness` + 删 3 已 ship 项目记忆（goal_completion/backend_orphan/self_update），索引→18 条全有效。**HM-1 strict**：真机确认 strict 下纯闲聊"你好呀今天天气"多轮 `stop_reason=end_turn` 不误阻塞 + goal/task 回合放行 → 出厂默认 `verify_gate_mode` shadow→**strict**（完成 decision① 目标，emit_receipts=True 满足 VG-INVARIANT-1，38 测绿），提交 `7fd79c83`。**TG-1 真机 PASS**：goal_mode ON → `goal_task_tools_registered_global count=4` → 设目标后主 agent **真调 goal_task_create ×3 建带依赖 DAG**（任务2 depends_on 任务1、任务3 depends_on 任务2，goal_id 反查解析成功），截图 `testcase/.../TG-1-goal-task-create-dag.png`。**1A-2**（裁 MCP 插件）= 须交互式 `/plugin`/`/mcp` 面板（会话无法驱动）+ 用户选保留项，据实留用户照做。**剩余真测**（更重/relay 依赖）：TG-2（需 App.tsx 开面板+并发权限请求）· code 模式 B2(1B-2~5)/CC-2(plan只读)（companion 压缩 inert 需 code 模式）· OH-2 召回确认（relay 间歇 504 拖慢，pin 机制已验）· OC-1（strip 防御使拒绝分支不可达，20 单测兜底）。 |
| 2026-06-23 | **出厂 feature-flag 不向存量 install 传播 — 缺口修复（非破坏性 key-merge / additive schema-migration）✅** — 承接上一条里程碑遗留②（`seed_user_config_if_missing` 只 ① 全新装整份 seed ② legacy `[llm.local/cloud]` 整份替换，**从不把 bundle 新增 key merge 进已存在的 unified 用户 config**）→ 任何新加到出厂 `config.toml` 的 flag（`curation_nudge`/`auto_learnings`/历史 `facts_extract`…）只对**全新安装**生效，存量 `%APPDATA%\deskpet\config.toml` 永远拿不到 → 功能对存量用户实际是暗的（`resolve_config_path` 优先读 AppData config=旧值）。**修**：`config.py` 新增 `_merge_missing_feature_flags()`（tomlkit 注释保真写回）——启动时把 bundle 默认里**用户缺失的 allow-listed flag key** 补进用户 config，**只补缺失、绝不覆盖**用户已改值/注释；写前 `.pre-migrate-bak` 备份；幂等；tomlkit 缺失/解析失败/写失败全 log+no-op **绝不抛**（不挡 backend 启动）。**allow-list 决策**（哪些自动补 vs 不动）：补=behaviour-flag/tuning 段（`tools.last_mile`/`tools.verifier`/`supervisor`/`companion`/`image`/`memory.v2[.facts/.forget]`/`context.manager`/`context.assembler`/`code_e2e`/`research`）；**不动**=`[llm*]`(endpoint/model/**api_key 绝不写**，运行时走 llm_runtime.json)/`[backend]`/`[billing*]`/`[asr/tts/vad/voice]`/`[memory].db_path`(用户路径)/`[[mcp.servers]]`(array-of-tables 标量 merge 不安全)。**测试**：`test_config_feature_flag_backfill.py` 13 测（缺失 flag 补上+用户自定义值保留+注释存活+整段缺失补+嵌套子表+排除段不碰+api_key 不写+幂等+备份+无 bundle/无 tomlkit 优雅降级+legacy 仍走整份替换不误入 merge），叠 legacy 8 测**共 21 绿**；config 全套 **186 passed**。**真配置验证 PASS**：`scripts/verify_feature_flag_backfill.py` 用**真仓库 config.toml** 当 bundle + 剥掉新 flag 的合成存量 config → 经真 `resolve_config_path()` → `curation_nudge=true`/`auto_learnings=true`/`every_n=2` 补上，`backend.port`/`llm.model`/`facts_extract` 值不变、`Strangler-Fig` 注释存活、二次运行不重写。pyproject + PyInstaller spec 加 `tomlkit` 依赖（pure-Python 自动入包）。**真机 GUI boot 真测 PASS**（[REPORT](../plans/manual-results-2026-06-23-backfill/REPORT.md)）：真 `npx tauri dev` 源码后端 + 隔离 userdata 预置"剥掉 flag 的真仓库 config"当存量 fixture（不设 `DESKPET_CONFIG`，走真 seed/merge 路径），boot 日志三证链 `[backend_launch] Dev python=...`（源码非 frozen，pitfall #8 判据）→ `feature_flag_merge_applied count=3 keys=[curation_nudge/every_n/auto_learnings] backup=...pre-migrate-bak`（迁移在活 backend 跑）→ `oh4_curation_nudge_wired every_n=2 auto_learnings=True`（运行 backend 读迁移后 flag=True 接电 curator）+ 落盘 config 实补 flag 且用户值/中文注释不变。真测踩坑：① leftover 手动 `python main.py` 栈持 cargo build 锁致首启卡死→写 `scripts/cleanup_deskpet_leftovers.ps1` 精准清；② PowerShell `*>>` 缓冲 native 输出→改 cmd `>> log 2>&1` 流式抓 backend 日志。✅ **GUI 真测 FIRE PASS（补跑，用户在场）**：真坐标点击+真中文输入（点「消息」开完整聊天窗→同会话连发 2 条真实用户消息）→ 轮1 agent 真调 `memory_write{"用户叫小王，是个程序员"}`+权限门「允许一次」+桌宠真回「我记住啦」→ 轮2 后日志判定 `oh4_curation_nudge sid=task-task-default-1-1 turn=2`——迁移点亮的 flag 驱动的 curation nudge **在 turn=2 真触发**，存量→迁移→wired→FIRE 全链贯通。真测踩坑：compact 面板无常驻文本框+WebView2 SendInput 焦点不进 DOM→须开完整聊天窗；每点「新话题」=新 session 计数器重置→须同会话连发 2 条；installed shell 启动即弹自更新器（killed 不装，用户安装版未改）。证据 [REPORT](../plans/manual-results-2026-06-23-backfill/REPORT.md)+[EVIDENCE-curation-fire.txt](../plans/manual-results-2026-06-23-backfill/EVIDENCE-curation-fire.txt)。 |
| 2026-06-23 | **WI-OH-4 curation nudge 生产死链修复 — 真因纠正：出厂 flag 漏配（非 facts_store 接线）✅ + #1 真机 boot-log 实证** — 承接 `task_455ba81e`（上一条 P2 记录的「facts_store 在 per-session build_agent register、lifespan 时为 None」机理**已被推翻**）。**真因三证定位**：① 生产日志 `tauri-dev7.log.err` 的 `p4_services_registered`+`memory_tools.bind: facts_store=FactsStore` 证 facts_store 非 None 且模块级 try(`main.py:1202-2366`)完整跑完——`build_agent` 1125 即 `return`，facts 构造(1295)/注册(1871) 全在 1175+ **模块顶层 import 期**跑，**不是 per-session**；② 仓库 + AppData `config.toml [memory.v2]` 段**都漏 `curation_nudge` key** → dataclass 默认 False → lifespan(`main.py:2648`) if 短路 → curator 永不构造（既无 wired 也无 skipped 日志）；③ standalone `load_config` 读仓库 config(临时加过 flag)=True，但运行 backend 经 `resolve_config_path` 优先读 AppData config(无 flag)=False → 诊断「config=True」是读错文件。**修**(`b8d57bf3`)：(a) `config.toml` 出厂点亮 `curation_nudge=true`+`every_n=2`+`auto_learnings=true`；(b) `main.py:2648` 改显式三分支 skip-log(`reason=flag_off/no_facts_store/no_llm_provider`)+facts_store `service_context` 兜底→杜绝静默死链复发；(c) 新增 `test_oh4_curation_wiring.py` 6 测守出厂 flag 点亮(真因回归)+`build_agent`→agent_loop 接电（补 `test_memory_curation.py` 直接构造 curator 测不到的接线链）。**30 测全绿**。**真机 windows-mcp E2E 全 PASS**（真坐标点击+剪贴板中文输入+截图+backend 日志，源码 backend+点亮 config）：#1 启动 `oh4_curation_nudge_wired every_n=2 auto_learnings=True`；#2 sid=default 聊天 → `oh4_curation_nudge turn=2` **且** `turn=4` 周期触发。**⚠️ 真测又抓出第 2/3 处死链（单测全绿生产死，同首 bug 模式，`920941e3` 修）**：**死链 B**=`main.py:_run_chat` 每回合 `build_agent` 重建 `_AgentLoop`，轮次计数器原挂 loop 实例→每回合归零→`every_n=2` 时 count 恒=1→nudge **永不触发**（单测复用单 loop 实例测不到）→计数器移到 `MemoryCurator` 单例 `bump_turn`；**死链 C**=构造 curator 漏传 `allow_learnings`→CC-5 learnings 暗装→传 `config.memory.v2.auto_learnings`。turn=2&4 双触发=计数器跨 4 次 loop 重建累加铁证。新增 `test_counter_persists_across_loop_rebuild` 守 B。证据 [manual-results-2026-06-23/RESULTS.md](../plans/manual-results-2026-06-23/RESULTS.md)。遗留②(出厂 flag 不向存量传播)已由上一行里程碑修复。 |
| 2026-06-23 | **上下文优化 P3（打磨 8 项）— 实现完成 + 子代理评 7/8 PASS（OH-3 caller 为前瞻扩展点）✅；真测随 P2 批量待 relay** — 承接 [plan](../plans/2026-06-22-context-and-agent-optimization/00-PLAN.md) §4 P3，全 flag 默认 OFF=字节 BC。**1B-3** 自适应 compact_at_pct（`compact_at_tokens_for(agentic)` 按本 run 工具数微调 clamp[0.6,0.95]）/ **1B-4** 摘要质量回路（困惑词法+L1 任务态快照回灌）/ **1B-5** size-aware microcompact（最近N条+累计字节≤M 防巨型 result 爆窗）/ **OH-3** 写入分级（manager.write(light=)+put_doc_light 原语+flag+测试就绪，caller 接线为前瞻扩展点——当前架构无干净高频低信息写入点，强接对话消息会损召回）/ **CC-5** auto-memory learnings（curation 扩 learning 慢衰减 category+注入）/ **CC-3** /run·/verify 内置 SKILL.md（user-invocable:false 不污染用户）/ **TG-2** 审批 UI 聚合面板（ApprovalCenterPanel 批量批准+gate list_pending 只读 WS，默认 enabled=false）/ **HM-2** 引用既有 skill-executable plan（无代码）。独立评估 7/8 PASS（OH-3 为正确扩展点）。提交 `ef5ff544`→`d5ea0799`。证据 [exec/P3-RESULTS.md](../plans/2026-06-22-context-and-agent-optimization/exec/P3-RESULTS.md)。windows-mcp 真测随 P2 批量待 relay 恢复。 |
| 2026-06-23 | **上下文优化 P2（4 个真缺口新建）— 实现完成 + 子代理评 100% ✅；windows-mcp 真测 relay 故障受限** — 承接 [plan](../plans/2026-06-22-context-and-agent-optimization/00-PLAN.md) §4 P2。**OH-4** 记忆 self-curation nudge（`memory/curation.py MemoryCurator` + agent_loop FinalEvent 后 fire-and-forget 每 8 轮→facts.upsert，flag `curation_nudge` 默认 OFF=BC，12 测）。**CC-2** plan mode 物理只读（`registry.py execute_tool` 按 `permission_category`(`_WRITE_PERMISSION_CATEGORIES`) 拦写类工具非硬编码名 + `plan_confirm_gate` 接入，flag `plan_read_only` 默认 OFF=BC，24 测）。**OC-1** 显式 depth 上界（env `DESKPET_SUBAGENT_DEPTH` + `check_spawn_depth` 5 入口守门，超 max(默认1硬上限3)→`SpawnDepthExceeded`，保留 strip 双保险，flag 默认 OFF=BC，20 测）。**OC-2** 背压累计指标（scheduler peak_concurrent/total_queued/total_rejected+lane_wait 分位 + 前端 SubagentProgressPanel 展示，纯增观测=BC，9 测+vitest20）。独立子代理评 P2=100%，65 单测+tsc0 全绿，4 项 BC 全验。提交 `d59fca6e`(OH-4)→`a19c4522`(OC-2)。**windows-mcp 真测受限**：测试当时 relay(chinzy.com) 持续 HTTP 500 故障，阻断 LLM 链路（deepresearch fan-out/curation 都需 LLM），已 ≥3 次重试；relay 恢复后批量真测：**OC-2 真机 PASS**（面板「峰值2·累计入队6·拒绝0」截图）+ **CC-3 真机 PASS**（knowledge_enabled→skill 12→17）+ OH-2 pin 再确认；**OH-4 真测抓出生产死链**（MemoryCurator 永不构造——facts_store 在 per-session build_agent register、curator 在 lifespan startup 构造时 facts_store=None；12 单测绿但生产死链，正是真测价值）→ spawn `task_455ba81e` 修。其余 flag-gated 项单测充分 + OFF=BC 默认态运行；compaction 项 companion 天然 inert。证据 [exec/P2P3-REALTEST-RESULTS.md](../plans/2026-06-22-context-and-agent-optimization/exec/P2P3-REALTEST-RESULTS.md) + [P2-TEST-RESULTS.md](../plans/2026-06-22-context-and-agent-optimization/exec/P2-TEST-RESULTS.md) · 手测 [testcase/2026-06-22-context-agent-opt-P2](../testcase/2026-06-22-context-agent-opt-P2/manual-test.md) |
| 2026-06-22 | **上下文优化 P1（点亮护城河+补观测）— 实现完成 + 子代理评 100% + HM-1/OH-2 真机 PASS ✅** — 承接 [plan](../plans/2026-06-22-context-and-agent-optimization/00-PLAN.md) §4 P1。**HM-1**（自我纠错全档点亮）：`config.py` tools.verifier `structured_reflection`/`verify_gate_mode`(off→**shadow** 稳妥档)/`emit_receipts` 三者原子翻 True（VG-INVARIANT-1 硬连锁）+ 清 verify_gate stub 注释 + metrics 白名单补 verify_replan_stagnant/verify_exhausted/ephemeral_pass/rescued；**真机 PASS**：`verify_gate_init mode=shadow patterns=9` + `ephemeral_verifier_model model=haiku` + shadow 不误阻塞 companion 闲聊。**OH-2**（偏好半衰期默认开）：`config.py:203 pref_decay` False→True + memory_write 加 `pinned` 参数走对话式 pin（硬前置同批）；**真机 PASS**：说「记住我喜欢 neovim」→ LLM 调 `memory_write{"pinned":true,...}` 实证。**TG-1**（方案A）：新建 `goal_task_create/get` 全局工具 + `build_global_goal_task_tools` + 坎解法(a)反查 `get_active_goal_context` + goal_mode 门控(默认 False=BC) + 清 goal_store 两处过期注释；7 新测+204 回归绿，goal_mode 真机待专项会话补验。**OH-1**=决策 no-op（不提升第五路 lane）。**CC-1**=0（重挂已实现）。独立子代理评 P1=100%。提交 `0de7c2df`(HM-1)→`2e9850bf`(OH-2)。证据 [exec/P1-TEST-RESULTS.md](../plans/2026-06-22-context-and-agent-optimization/exec/P1-TEST-RESULTS.md) · 手测 [testcase/2026-06-22-context-agent-opt-P1](../testcase/2026-06-22-context-agent-opt-P1/manual-test.md) |
| 2026-06-22 | **上下文优化 P0（1A 环境瘦身 + 1B-1 token 口径统一 + 1B-2 压缩可观测）— 实现完成 + 子代理评 100% + 1B-1 真机 PASS ✅** — 承接 [plan v1.1 EXECUTABLE-AS-IS](../plans/2026-06-22-context-and-agent-optimization/00-PLAN.md)。**1A**：全局+项目 CLAUDE.md 瘦身（-7.6K 字符≈2-2.5K token/会话），细节抽到 `~/.claude/knowledge-base/`（windows-mcp-e2e/codex-usage/context-compact-sop），4 条真测记忆合并；1A-2 裁 MCP 插件留用户照做（交互面板+重启）。**1B-1**（决策③方案B 不挂 flag）：6 处裸估算（main.py 三处 `/3.5`、skill.py 三处 `//4`、metrics.py）统一到 CJK-aware `count_text_tokens` + `test_token_unify_cjk.py` 9 绿；**真机 windows-mcp PASS**：ContextBreakdownModal 实测「CJK-aware tokens」文案 + 中文 history 137k(CJK 量级非 /3.5 低估)。**1B-2**（flag `ctx_observability` 默认 OFF=BC）：`ContextCompactedEvent`+metrics+WS+前端 toast，单测 56/56 绿；**toast UI 触发 env-limited**（5+ workaround，压缩在 companion 单工具回合下 prompt_tokens=17619>>6400 阈值仍不触发，疑触发器时序问题待 P3 复查）。独立子代理评 P0=100%。输入法突破：App switch 消息窗+Type(Unicode) 攻克 WebView2 中文输入。提交 `902104ec`→`3dd44360`。证据 [exec/P0-TEST-RESULTS.md](../plans/2026-06-22-context-and-agent-optimization/exec/P0-TEST-RESULTS.md) · 手测 [testcase/2026-06-22-context-agent-opt-P0](../testcase/2026-06-22-context-agent-opt-P0/manual-test.md) |
| 2026-06-22 | **修 VerifyGate ephemeral 模型 shipped bug + 真机验证 ✅** — 承接 [R2 对抗审查](../plans/2026-06-22-context-and-agent-optimization/)。配置项 `[tools.verifier].ephemeral_subagent_model`（`config.py:271`，默认 haiku + 白名单校验 VG-INVARIANT-5）**从未被消费**：自我纠错闭环升级 ephemeral 救援子代理时，`build_agent`（`main.py`）直接用 `local_llm or cloud_llm`，**没读 config**→用户/默认配的专用模型恒被忽略、永远复用主 LLM。修法：新增 `_resolve_ephemeral_provider(base, model_name)` 按配置克隆专用 model 的 `OpenAICompatibleProvider`（中转站按 id 路由，复用 base 连接参数），缺省/同名/克隆失败回退主 LLM（兜底保 BC）；log 改 structlog kwargs 让模型值可观测。新增 `test_ephemeral_subagent_model_wiring.py` 15 测（解析器 4 分支 + build_agent 接电 2 例）全绿 + 回归全绿。**真机 boot-log 档验证**：隔离 dev（Tauri spawn 源码后端 8200，配 `ephemeral_subagent_model="sonnet"` / `verify_gate_mode="shadow"`）→ 真 UI 发消息触发 → backend log 实证 `event='ephemeral_verifier_model' model='sonnet' base='gpt-5.5'`（用配的模型，非复用主 LLM）+ 同回合 `verify_gate_init mode='shadow'`。证据 [manual-results-2026-06-22-ephemeral-model/](../plans/manual-results-2026-06-22-ephemeral-model/RESULTS.md) |
| 2026-06-22 | **PPT Pro — 实施完成 + 真机验收 ✅** — 承接 [plan v1.3 LOCKED](../plans/2026-06-21-ppt-deepresearch-pro/00-PLAN.md)（经 6 轮 codex 对抗收敛）。新工具 `ppt_pro(topic, depth, pages, ...)` 确定性编排 **F1 deepresearch 充分调研 → F2 拟纲（双模式防回退）→ F3 大纲卡确认(可改) → F4 首图实测判定（gpt-image-2 可达→惊艳整页生图 / 不可达→模板兜底）**；双保险防二次烧图、独立 task 秒回、分阶段限时 600s、/停止级联取消。实施完成度 100%（WI-0~10 全实现 + 子代理两轮评估 96%→100%）；单测 221 passed + 冒烟 DECISION:SHIP。**真机 windows-mcp E2E** F1-F4 逻辑链路 PASS（deepresearch 真调研 / 大纲卡真渲染+SendInput 真点击确认 / 图像 403 回退判定正常 / deck 5 页落盘自动打开 WPS）。**发现并修复 2 个真 bug**（单测/冒烟测不出）：① chat 不加载 ppt-generate skill body→LLM 走 ppt_create 绕过 ppt_pro（强化 tool description 路由修复）；② 渲染在 executor 线程 hang（双根因：bundled 模板直传绕过外部 2.8GB 库 vision 选图 PIL 拼图阻塞 + `_render_pro` 传 SlideOutline 给 `ppt_create` 致 parse failed→asdict 转 dict）。**环境受限**：relay gpt-image-2 403 配额墙阻断剩余约 13 用例（待配额恢复补验）。提交 `7cd74739`→`bf971a45`。证据 [manual-results-2026-06-22-ppt-pro/](../plans/manual-results-2026-06-22-ppt-pro/) · 计划 [plans/2026-06-21-ppt-deepresearch-pro/](../plans/2026-06-21-ppt-deepresearch-pro/) · 模块档 [PPT.md](./PPT.md) |
| 2026-06-22 | **任务漂移 v2 — 四阶段（A/B/C/D）全实现 + 核心真机 PASS 收尾 ✅** — 承接 [01-implementation-plan](../plans/2026-06-21-task-drift-fix-v2/01-implementation-plan.md)（经 **7 轮 codex 对抗硬化** 定稿 EXECUTABLE-AS-IS）。三层根因全处置：**A** T0-1 deepresearch 原话夺权（层2纵深）真机 TC-A1 相邻领域(钠离子史→18 固态/0 钠离子)+TC-A2 跨域(电池史→Tokio/0 电池) PASS；**B** T1-1 硬会话切分（层1根治，`session/task_scope.py` effective_sid=`task-<base>-<seq>` + `session_switched`/`task_session_started` WS 事件 + voice 全链路 + 前端「新话题」按钮真机渲染）真机 TC-B1 `/new 区块链`→`session_id='task-default-1'` 新干净 scope(区块链 11/0 旧主题) PASS，389 passed+tsc0+vitest2；**C** T1-2 L2 降级 external memory(page-in)+`/continue` 透传 512 passed；**D** T0-3 关 Tier2（8 处 `topic_shift_gate`→false）真机组装 gate `topic_shift_gate=False+shift_path='off'+l2_truncated=False`（Tier2 整链关）+`relabel_applied=True anchor_applied=True`（Tier1 精准保留），473 passed 无破坏。每阶段闭环：codex(gpt-5.5)实现→子代理评估 100%(0 GAP)→windows-mcp 真机。HARD GATE 全验(Dev python+is_mock=False)。网络受限(deepresearch 0 引用不落盘=设计行为)故判定锚点用搜索 query 主题 + 组装期 `task_drift_context_gate` log 而非落盘报告。提交 `07edc0ee`→`f5e878a4`+证据 `ec6a864f`。真机证据 [testcase/2026-06-21-task-drift-v2-phase{A,B,CD}](../testcase/)（各 RESULTS.md + 截图 + log）。flag/sentinel gating，OFF=字节 BC |
| 2026-06-21 | **任务漂移 v2 阶段B — T1-1 硬会话切分 + voice 全链路 ✅ 后端+前端落地（单测/tsc 绿；真机 E2E 待跑）** — 承接 06-20 软修（组装期裁剪，D1 硬会话切分当时「暂缓」），本次真正落地后端 **硬会话切分**：新增 `backend/deskpet/session/task_scope.py`（`base_session_id` = 用户打开聊天窗的主会话；`effective_session_id` = `task-<base>-<seq>` 当前工作子会话；话题跳变 / 用户点「新话题」→ 切到新 effective_sid）+ `main.py` 切分点广播 `session_switched` / `task_session_started` WS 事件（含 old_sid/new_sid/reason）并 fan-out 给 default-chat peers + `agent_loop.py` sentinel 透传 + voice 全链路对齐（`voice_pipeline.py` 走同一 effective_sid，新增 `test_voice_task_scope.py` 193 行）。改 10 文件 +792 行，含 `test_task_scope.py` / `test_main_task_scope_wiring.py` / `test_agent_loop_sentinel.py`。flag/sentinel gating；**前端（响应 session_switched + 新话题按钮）已落地**（`6b927d38`，2026-06-22）：`App.tsx`/`MessagePanelRoot.tsx` 移除硬编码 `default`→`activeSid`；`ws.ts` dispatcher 响应 `session_switched`/`task_session_started`→`ensure(new_sid)`+`set_active`；`InputBar` 新话题按钮发 `{new_session:true}`；消息流/context/send/stop 全用 activeSid（BC：无切换 activeSid=default）。改 5 文件 +237 行；`tsc --noEmit` exit 0 + vitest 2 passed。**⚠️ 前端仅单测/tsc 绿，windows-mcp 真机 E2E 待跑**（无 manual-results 落盘）。提交 `0e6211e8`（后端）+ `6b927d38`（前端）。计划 [plans/2026-06-21-task-drift-fix-v2/](../plans/2026-06-21-task-drift-fix-v2/) |
| 2026-06-21 | **任务漂移 v2 阶段A — T0-1 deepresearch 原话夺权 + T0-2 fanout 隔离断言 ✅ 真机 PASS** — 在 06-20 task-drift 修复基础上加固 deepresearch 主题不漂：T0-1「原话夺权」让本轮用户原话在 deepresearch plan/synth 中夺取主题主导权；T0-2 fanout 子代理隔离断言（子问题间不串味）。真机 windows-mcp E2E **TC-A1 相邻领域**（18 固态电池/0 钠离子）+ **TC-A2 跨域**（Tokio/0 电池）搜索 query 铁证主题不漂 + fanout 6 子代理 + FixB；落盘 ENV-LIMITED（网络 0 引用）；单测 197+ 评估 100%。提交 `07edc0ee` / `15817813`。计划经 codex 7 轮对抗挑战收敛 EXECUTABLE-AS-IS（`b05ab20f`）。测试 [testcase/2026-06-21-task-drift-v2-phaseA](../testcase/2026-06-21-task-drift-v2-phaseA) |
| 2026-06-21 | **deepresearch 子代理 fan-out — Phase 1（WI-8 落盘迁移）✅ 真机 PASS** — 所有 deepresearch 报告从 `<user_data>/OutPut/Research/` 迁到**安装目录 `DeepResearch/`**（dev=repo 根，frozen=安装根 probe，env `DESKPET_DEEPRESEARCH_DIR` 可覆盖，**绝不回落 C 盘 AppData**）+ 维护 `DeepResearch/index.md` 倒序总索引（可点击相对链接/模式列 flat-vs-fanout/utf-8/原子写/asyncio.Lock 幂等）。`paths.deepresearch_dir()` + `_update_deepresearch_index()`。计划经 **5 轮 codex 对抗挑战收敛(v1.0 LOCKED，修 11 BLOCKING+8 MAJOR)**；实现经子代理评估 **100%**；**手测文档设计阶段抓出并修复模式列泄漏档位 bug**(cov["mode"]=深度档→改 subagent_fanout 判定)。TG-6 单测 12 passed + research 83 BC。**windows-mcp 真机 E2E**(干净重启 Dev python backend，relay 已连接)：真发 2 次中文调研→报告落 repo 根 DeepResearch/(非 OutPut/Research/非 AppData)、index 首建表头+第二份倒序在上、模式列 flat、中文不乱码、相对链接、artifact 结果卡片——TC-WI8-01/02/03/05/07/08 PASS(04/06 单测覆盖,09 env 单测覆盖)。证据 [RESULTS.md](../plans/manual-results-2026-06-21-wi8-deepresearch/RESULTS.md)。计划 [plans/2026-06-21-deepresearch-subagent-fanout/](../plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md)(经 5 轮 codex 对抗挑战 v1.0 LOCKED) |
| 2026-06-21 | **deepresearch 子代理 fan-out — Phase 2(WI-1~6/§5 fan-out 核心)✅ 真机 PASS** — plan 拆题后**每子问题派一个 research 子代理跑完整单问题 deepresearch**(复用§6.0 全管线)经 `SubagentScheduler` 有界并发(lane/global cap 背压)→ 主线程**统一 synthesize** 跨子报告出结论。`_run_subagent_fanout`(预算硬裁 cap=min(max_subq,conc*5)/per_subrun=min(150,240/waves) 可证≤300s tool 超时)+引用全局重编号+脚注 strip/rewrite+`_fanout_synthesize`+`_finalize_report_md`抽取(扁平 BC)。**递归守门单一共享常量** `task_kinds._FORBIDDEN_IN_KIND` 加 deepresearch,封 4 路径(kind/agent 显式 tools/agent_parallel-spawn_subagents/teammate)防子代理二次 fan-out。flag `[research].subagent_fanout`(依赖 `features.subagent_driver`)默认 OFF/字节 BC。子代理评 **100%**;单测 **TG-1~5 16 passed + 合并 261 passed(BC+driver+byte-level)0 fail**。**windows-mcp 真机 E2E TC-F1~F7 全 PASS**:真发宽主题→backend log **6×`subagent_scheduled kind=research run_id=default.dr-0..5`** + 前端「子代理并发·运行中5/6」面板**背压可见**(2跑3排队1完成) + index 模式列 **fanout**(6子问题)统一报告全局引用 + 递归守门**无嵌套 run_id** + flag OFF/1子问题→flat。网络受限(google TimeoutError)致内容偏弱属环境,机制全验证。证据 [RESULTS-phase2-fanout.md](../plans/manual-results-2026-06-21-wi8-deepresearch/RESULTS-phase2-fanout.md) · 手测 [testcase](../testcase/2026-06-21-deepresearch-subagent-fanout/manual-test-phase2-fanout.md) |
| 2026-06-21 | **桌宠子代理并发驱动 P0-P4 全实施 + 真桌宠 windows-mcp UI E2E V1-V5 全 PASS ✅** — 承接 [plan v0.3](../plans/2026-06-21-subagent-concurrency-driver/00-PRD.md)（经 **3 轮子代理对抗评审收敛**，R3 逐条核源码判 100% executable + 用户拍板全做）。给桌宠 agent **驱动子代理并发处理多种事务**的能力，复用现有三层基建（agent/agent_parallel/spawn_team）**加性扩展不重写**。**P0** `task_kinds` 事务分型（research/code/doc/web/fileops/general，工具子集已剔 spawn 类保 depth=1）+ `subagent_scheduler` lane-aware 双闸有界并发（global cap + per-kind lane，背压）+ config flag/服务槽；**P1** agent_parallel 路由进分型+调度（schema 加 kind、上限 4→8、新增 `_make_async_native_runner` 无线程池占用 F10）+ WS 进度出口；**P2** spawn_team 暴露为 LLM 工具（同构池）+ TeamStore/TaskGraphStore 接进 main.py + db 清理；**P3** 非阻塞 spawn_subagents/await_subagents + completion queue 回合边界注入（R2-1 取反守门）+ `/stop` 取消级联 + 前端 SubagentProgressPanel；**P4** 子代理质量守门 hook + per-kind 模型路由（新建 provider）。对标 **openhuman/hermes/openclaw 码级（8 模式）**。**全 flag 出厂 OFF（subagent_driver/agent_team/subagent_nonblocking），OFF=字节级 BC**。证据：单测 **320+ 全绿**（P0 23+P1/P2 86+P3 8+P4 5+前端 vitest 5+各阶段 BC 回归）；接线冒烟 `subagent_driver_smoke.py` **DECISION:SHIP**；**boot smoke 真 backend 启动（8399,dev）4 ready 锚点全亮**（`subagent_driver_ready global=4 lanes={research:2,code:2,fileops:3,doc:1,web:3,general:2}` / `agent_parallel_ready scheduler=True` / `agent_team_ready` / `subagent_nonblocking_ready`）+ 0 Traceback + Application startup complete；collect-only 3188 无 error；前端 tsc 0 err。dev config 已开 3 flag。**2026-06-21 真桌宠 windows-mcp 多事务并发 UI E2E V1-V5 全 PASS ✅**（干净会话 + dev 自动登录 + relay gpt-5.5 真多轮）：**V1★** 桌宠输入「同时①调研钠电②查SU7价③列season提纲」→`agent_parallel` 调用、`subagent_scheduled` kind=research/web/general 三 lane、metrics 250ms 内全 running 时间重叠、聚合回一条；**V2★** spawn_team 4 译文→`spawn_team team=… n=4`+team_task_claim×8(池清零)+team_task_update×4 done；**V3** 6 子任务→峰值并发=4(global cap)、2 排队晋升、6 全 completed 不丢、卡片"运行中4/6"实拍；**V4★** flag-off 字节 BC=158 pytest 全绿(test_scheduler_none_is_bc + byte_level_consistency)；**V5★** spawn_subagents(background) 立即返回→停止按钮→`subagent_cancel_all n=3`+running→failed 即时+取消后 0 LLM 出站。附带 E-2 错误隔离真机生效(SU7 web 子代理 failed→主代理兜底补查+聚合)。证据(截图+log/metrics 铁证)[manual-results-2026-06-21-subagent/RESULTS.md](../plans/manual-results-2026-06-21-subagent/RESULTS.md)。**2 个非阻断瑕疵全闭环(2026-06-21 真机复验,commit `0e190ae`)**：① ~~进度卡片仅 Code 模式挂载,桌宠主消息面板未挂~~ **已修**：SubagentProgressPanel 重写为 variant(light/dark)+可折叠(全done自动收起)+运行中实时计时+淡入动画,挂进 `components/MessageStreamPanel`(深色变体),走该窗口 control WS 喂 subagentStore(后端广播所有连接 main.py:1978)——真机发4竞品调研,主消息面板「消息·主线程」实时显「🤖子代理并发·运行中4/4」+running计时33s+queued排队中;② ~~排队中被取消的子代理不发终态致卡片卡 "queued"~~ **已修**:`subagent_scheduler.run()` 顶层 try/except 捕获排队阶段 CancelledError→补发 failed(reason=cancelled)终态+修计数泄漏(`261ba42`);并给运行期取消 inner-except 也补 reason=cancelled(`0e190ae`)→前端 🚫已取消(vs ❌失败)归一,真机停止后4行全🚫无一卡queued;顺带修计时秒/毫秒 elapsed bug。回归 `test_{queued,running}_cancel_*`+`genuine_failure`(后端 9 passed)+前端 store/panel(13)+54后端229前端全绿。commits `17a4b9e`→`933b967`+前端 `fa28fc8`→`261ba42`→`0e190ae`。 |
| 2026-06-21 | **deep-research §6.0 搜索可靠性改造 — 真机 windows-mcp E2E TC-A1~A6+X1 全 PASS ✅** — 承接 [plan](../plans/deepresearch-upgrade/)（codex 并行实现 + Lead 集成，经 5 轮子代理评估）。背景：旧搜索抓取常 0 源（必应/DDG 中国大陆被封/限流，spike 实测 11/13 全 0 源）。**改造**（`242406a`→`b05823b`）：① 默认引擎队列改 `_DEFAULT_ENGINE_QUEUE=("google-cdp",)` —— 去掉 Bing/DDG 默认主力（按用户「不用这两个」），新增 **google-cdp 引擎**（无头浏览器渲染谷歌 SERP 绕封禁）+ `_google_reachable()` 可达门控（有 VPN 才用、不通自动跳过不拖垮）；bing/ddg/baidu/bing-cdp/searxng 仍可显式 opt-in。② 新增**百度百科 / 搜狗百科直连源**（`research_sources.baidu_baike_search`/`sogou_baike_search`，默认开、国内稳定，通用/综述主题主力）+ `_reachable(host)` 可达探测（缓存可注入 clock）+ wikipedia 加可达门控。**真机揪出修 2 严重 bug**（单测/5 轮评审全漏，真机才暴露）：🔴 搜索 0 结果（SERP 被封）时 early-return 直接跳过直连源 → §6.0-A 在最需要时失效（`ab14e04`）；🔴 `search_provider._research_raw` 读 `config.config.raw` 但该单例根本不存在 → 恒 AttributeError 吞 → `[research]` 全部配置开关（search_engines/searxng_url/serp_hardening）静默失效、此前靠代码默认侥幸「看着对」（`b05823b`，改 load_config 健壮兜底 + 回退单测）。**真机 E2E**：TC-A1 单主题 PASS；TC-A2 连续 6+ 研究无一 0 源（对照 spike 病灶根治）；TC-A3 财报→cninfo 20 命中+巨潮 PDF 12 真抓；TC-A4 通用主题（退货率）→搜狗百科兜住非 0；TC-A5★ 百度百科 12+搜狗百科 12 真 API 调用、报告引 3 个 baike.sogou 源独立撑起通用主题、不靠 Bing/DDG；TC-A6 google-cdp 渲染参与（VPN 可达）+ 门控正确；TC-X1 乱码主题不崩/不编造/App 存活。144→147 测试绿。证据 [manual-results-2026-06-20-deepresearch-6.0/](../plans/manual-results-2026-06-20-deepresearch-6.0/) |
| 2026-06-21 | **dev 自动登录 — 本地永不手动登录 relay（仅 DEV，生产死代码）✅** — 用户诉求：本地测试不想每次登录；relay token 设计上会过期（access 1h/refresh 30d），无「永久 token」。方案（`2263ee1`）：`RelayEdition` 启动 `restoreSession` 失败时，若 `import.meta.env.DEV` 且注入了 `VITE_DEV_RELAY_EMAIL/PASSWORD`，则静默 `adapter.login()` 自动重登，登录框永不阻塞。**仅 `import.meta.env.DEV` 生效**；生产 build（DEV=false）整段死代码、凭据不进生产包；凭据走 gitignored `tauri-app/.env.local`（从 `LOCAL-DEV-CREDENTIALS.md`），代码无硬编码。需重启 vite 让 `.env.local` 生效。 |
| 2026-06-20 | **任务漂移修复（桌宠对无关新请求漂回上下文旧主题）— 真机 windows-mcp E2E PASS ✅** — 承接 [HANDOFF](../plans/2026-06-20-task-drift-fix-HANDOFF.md) + [fix-plan §8/§9](../plans/2026-06-20-task-drift-fix/00-fix-plan.md)（三轮深度调研 + 两子代理读码评估 18/18=100%）。**根因**:桌宠聊天单一 `session="default"` 永续会话从不按任务切分，最近 5 条原始历史（627 库 CATL 占压倒）被**零门控**提升成"正在进行的对话线"贴当前 user 前 → 新无关请求被旧主题压垮。**修复（codex gpt-5.5 双 worktree 并行实现 + Lead 集成）**:**Fix A** 组装层 `memory.py`/`bundle.py`/`assembler.py`/`policy.py` — Tier1 当前请求优先锚定（`_CURRENT_REQUEST_NUDGE` 经 `build_messages` 新 `late_system_nudge` 槽位插在 history 后/user 前）+ L2 历史重定性标签（`_L2_CONTEXT_LABEL` 入 l2_history 头部），Tier2 话题跳变语义截断（合取 `低相似∧≥16字∧无指代`+短/代词 anaphora 豁免）;**Fix B** 分发层 `agent_loop.py`/`research_tools.py` — 对声明 `user_request` 字段的工具 dispatch 处**无条件注入本轮原话**（避开 set_session_context 合并顺序陷阱），deepresearch `_PLAN/_SYNTH_PROMPT` 双锚。**真机 3 轮「有问题→修复→复测」迭代根治**（plan 推不出、真机才暴露）:① Tier1 软指令不足以压住对话惯性（anchor/relabel 已注入但 topic 仍漂 CATL）→ 开 Tier2 默认 + `len>50` 阈值漏判 32 字简短新任务改可配 `topic_shift_min_len=16`;② Tier2 embedding 实时 encode 恒 `encode_timeout`（BGE-M3 subprocess 被 vector-worker 回填 627 条+研究负载抢锁，撞 1500ms 组件 budget）→ 加**词法内容词重叠兜底**（embedder 超时→零延迟词法信号，跨域漂移 token 重叠≈0 必被抓）。**windows-mcp 真机硬证据**:**TC-1★** 627 CATL 库发"深度调研 Rust Tokio"→`task_drift_context_gate l2_truncated=True shift_path=lexical l2_count_in=5→out=1`、`p5s2 topic="Rust…async-std/smol/monoio/glommio"`、桌宠真抓 `github.com/smol-rs/smol`、报告落盘（**零漂移**）;**TC-4★** 追问不误伤——"它的竞品"→`l2_truncated=False`(代词豁免)+web_search 宁德/比亚迪、"继续"→`l2_truncated=False`(短路豁免)+run_shell 续 CATL 年报 PDF 分析（**上下文未失忆**）;**TC-8** CATL→asyncio 切换正确截断+连贯答 asyncio。Fix B `task_drift_user_request_injected req_len=32`。新增可观测锚点 `task_drift_context_gate`/`task_drift_user_request_injected`/`task_drift_sim_*`。**回归 679 passed**(新增 fixa 13+fixb 4 单测);证据 [testcase/2026-06-20-task-drift-fix/RESULTS.md](../testcase/2026-06-20-task-drift-fix/RESULTS.md)。**诚实 caveat**:Tier2 embedding 主路真机因锁竞争难稳定触发（恒走 lexical 兜底）,其正确性由 mock-embedder 单测覆盖;D1 硬会话切分暂缓（后端 12+前端 6 处改造面，Fix A 组装期裁剪已软性达成隔离）。 |
| 2026-06-20 | **WI-5 触发式知识注入 — 末环遗留 bug 根治 + 真机复验 PASS ✅** — 承接 [HANDOFF](../plans/2026-06-20-agent-loop-optimization/HANDOFF.md) §3。**根因**:`main.py:1400` 构造 `SkillLoader` 漏传 `knowledge_enabled` → loader 恒用默认 `False` → `reload()`(`loader.py:308`)把 3 个 `user-invocable:false` 知识片段挡在快照外 → `loader.all()` 永远只有 12 个常规技能 → matcher 无从匹配(此前 `d7da6e5`/`011aab5` 修的 assembler/SkillComponent config 流通是在「过滤本就为空的子集」,源头没放行下游再对也没用)。**诊断**:`diag_wi5.py` 直证 loader flag False→total=12 缺知识片段 / True→total=15 且 triggers 正确。**修复**:`_SkillLoader(...)` 加 `knowledge_enabled=bool(config.skills.knowledge_enabled)`(默认 False 保 BC)。**真机复验**(windows-mcp,DEV_MODE,keychain LLM key):boot `skill.reload_ok count=15`(原 12);发 code 句「windows 反斜杠路径报错」→ `skill_auto_disclosed total=15 strong=1 auto_loaded=1 names=['windows-path-debug'] top_sim=0.950`(对照修前同句 `total=12 strong=0 names=[]`),桌宠端到端真响应。**回归**:补 2 条真 SkillLoader-当-registry 测试(走 `loader.all()`,捕获本 wiring bug);`test_wi5_trigger_inject` 8 + WI 套 + assembler + agent_loop BC 全绿(73+34)。证据 [manual-results-2026-06-20-wi5-trigger-fix/RESULTS.md](../plans/manual-results-2026-06-20-wi5-trigger-fix/RESULTS.md) |
| 2026-06-20 | **Agent Loop 优化 7 WI 全实现 + 真机 E2E 全 PASS ✅** — 承接 [plan](../plans/2026-06-20-agent-loop-optimization/00-PLAN.md)(经 5 轮子代理对抗挑战 §13-§17 收敛)。**codex gpt5.5 三批并行实现 + Lead 集成验证**:**WI-1** tool_choice 协议级硬约束(provider 三方法透传 + agent_loop 三路径 tier3 强制 `none` + tier3+ 禁 nudge + verify_exhausted 末轮纯文本收尾 + relay 不支持 none 兜底);**WI-2** 结构化 trace(`IterationTracer` jsonl 每轮 I/O+完整 tool args,flag `[agent].iteration_trace_enabled`);**WI-3** code persona「收尾自查清单」+ ask_clarification 引导;**WI-4** Focus Chain(code 任务每 8 轮且<30 回灌 `[当前任务进度]` todo 快照);**WI-5** 触发式知识注入(`[skills].knowledge_enabled` + 3 知识片段,复用 auto_disclosure body-inline + protected 防压);**WI-6** edit_file fuzzy 降级(whitespace→anchor→did_you_mean + schema fuzzy 参数);**WI-7** ask_clarification 工具(后端独立 control 通道防 chat-cancel 竞态 §13.7 H1 + 前端 ClarificationDialog)。**真机抓修 3 真 bug**(单测/协议层未暴露,真机+全量回归才揪出):WI-2 flag 与 [context.assembler].trace_enabled 命名碰撞→改 iteration_trace_enabled / build_agent 无条件 `cfg.raw` 破坏 _CfgStub 老测试→getattr 安全取 / WI-6 fuzzy 未进 schema 模型不可控→补 schema。**windows-mcp 真机 E2E**:Batch A TC-0~6 PASS(WI-3 code 收尾自查清单真观测 + WI-2 trace jsonl 真生成含完整 args)/ Batch B WI-4 `wi4_todo_sync iter=8 n=8` 真触发 / **Batch C WI-7 完整闭环真点击**(agent 调 ask_clarification→ClarificationDialog 真弹窗→真鼠标点击选项答题→答案经独立 control 通道回灌→挂起 agent task 据答继续不被 cancel)。**子代理逐批评估 100% + 全量终评 7 WI 全 100%**;WI 单测 31/31 + 大面 BC 回归 247 passed。证据 [testcase batch-a/b/c](../testcase/) + [manual-results](../plans/manual-results-2026-06-20-batch-a/RESULTS.md)。**诚实 caveat**:WI-5 知识注入活化受已有 FP-5 auto_disclosure task-类型门控(仅 `task` 类含 skill,chat 不触发),逻辑 6 单测验证、live 接线确认,broadening 到 chat 列 follow-up;默认全 flag off → BC 安全。 |
| 2026-06-20 | **PPT 模板源迁外部大库 + 预览图视觉选模板 — 真机 E2E PASS ✅** — 用户要求删掉 git 跟踪的旧 3 套 bundled 模板,统一改用外部大库 `resources/PPT_Template`(250 套·2.8GB·gitignored)。因模板数以百计无法塞进 LLM schema 按名选,用户提议「让模型看预览图快速筛选」。**实现**(`596efb7`+`130a376`):新模块 `ppt_template_picker.py` — 库结构原语(库根/大类/stem→预览图映射)+ PIL 拼 contact sheet + 复用 `ppt_visual_review.vision_chat`(抽出的共享多模态原语)一次 vision 调用按主题选具体模板,全程优雅降级(无库/无预览/vision 挂 → 随机回退,类空 → None 回落 from-scratch);`ppt_tools` 删 `_TEMPLATES_DIR`/`_list_bundled_templates`,`_resolve_template_for_render` 大类名→预览图选,schema 描述改列大类。**兜底**:挑 3 套通用商务模板(现代商务汇报/水彩工作计划/极简PitchDeck,含预览图~24MB)放回 `ppt_templates/通用商务/`,`template_library_root` = 外部大库优先、缺失回退 bundled(打包 app/新机器不失效)。**测试**:picker+resolve 16 + ppt/visual/template 全套 89 passed;裸进程冒烟(主库+兜底)PASS。**真机 windows-mcp E2E**([report](../plans/manual-results-2026-06-20/REPORT-ppt-template-vision-pick.md)):桌宠输「用高级色风格做新能源电池技术发布会 PPT」→ backend log 铁证 `ppt_create(template="高级色")` → `pick_template: 220→采样90` → **真 vision POST chinzy 200 → `vision chose id=77 → (177).pptx`** → design-pages 填充 + 模板视觉闭环 2 轮修版 → 产物 5 页落盘 → 桌宠回「已用高级色模板生成 5 页」;渲染产物首页/内容页确认 (177) 设计 + 新能源主题内容。这是裸进程冒烟(无 keychain key)无法覆盖的 vision 选图真链路。 |
| 2026-06-19 | **L3 向量记忆召回偶发 TypeError 根因修复(int>dict 假象)✅** — 真机 dev 日志偶发 `memory_manager.l3_failed error="'>' not supported between instances of 'int' and 'dict'"`,被 manager safe-fail 兜住但导致该轮 L3 整层降级(桌宠"想不起"记忆)。**根因**:cbdf855(WI-2 task-scope-context-isolation)给 base `Retriever.recall` + `MemoryManager._safe_l3` 调用加了 `recency_half_life_days` kwarg,但漏给 wrapper `EnhancedRetriever.recall`。真机默认半衰期 7.0 + enhanced 召回器(rerank/chunk/embedder 插件需真 embedder,故 mock 不复现)→ manager 用该 kwarg 调 wrapper → `TypeError: unexpected keyword argument` → manager 的 `except TypeError` 误判为"测试假替身签名"→ 退化重调 `recall(query, {policy dict})` → dict 当 top_k → base `max(dict, 20)` → **int>dict**。**修法**(非吞异常):给 `EnhancedRetriever.recall` 补 `recency_half_life_days` 参数并透传 base + 加防漂移注释;safe-fail 保留作兜底。**红→绿**:新增 2 个回归测试(`test_memory_enhanced_retriever_integration.py`),修复前精确复现真机那条 `l3_failed(int>dict)` 日志,修复后 L3 命中。required 套件 51 passed + manager 16 passed。 |
| 2026-06-16 | **上下文压缩升级对标 Claude Code/Hermes/OpenClaw — Phase 1+2 全做完 ✅ + compaction 默认开启(WI-6)** — 承接 [compaction-bestpractice-upgrade plan](../plans/2026-06-16-compaction-bestpractice-upgrade/00-PLAN.md)(已过 2 轮子代理对抗评审)。**Phase 1**(`70d376d`+`e933e7d`):**WI-1** 触发改剩余 token buffer(`min(threshold, eff_win−output_reserve)`,output_reserve=max(8K,min(32K,window//32)) 随窗口自适应;effective_pct=None 退纯比例保 BC);**WI-2** microcompact(陈旧 tool_result 正文换占位、保壳+tool_call_id 不删整条,降到触发线下跳 haiku);**WI-3** 结构化摘要 7 段 schema + `_extract_prior_summary` 锚定增量(旧[压缩摘要]抽出作 prior-state 不混 transcript,防套娃) + 透传 prior 空中段边界;**WI-4a** 目标 always-on 单点注入(agent_loop 循环前注 role=system,删周期 anchor,compress 去重→[目标锚定]恒≤1;dedup 比"删 compress 注入"更优,3 个 goal_anchor 测试文件零改动 BC)。**Phase 2**(`e1ff9d3`+`12d4554`):**WI-4b** pre-flush 压缩前落任务态进 L1(每 run latch 限频,跨 session 记任务,踩 frozen-snapshot 语义);**WI-5** 收敛(USER.md cap+召回地基已在,确认即可);**WI-6** compaction 默认 False→True。**子代理 2 轮对抗评估 96%→修 2 必修(get_pending_tasks 真实现子目标数据源 + safe-fail 保 microcompact 收益)→100%**;新增单测 21+回归 699-850 passed。**windows-mcp 真机**(注入 DESKPET_BACKEND_DIR 跑当前码,gpt-5.5 窗口压到 8000 逼触发):一次 deepresearch 35 工具调用,`context_microcompact_only`×24(把上下文从19501压住~14-15K,最高频生效层)+`context_compacted`×2(结构化分段)+`wi4b_preflush_l1`×3(限频)+UX banner 104%;**★ case ② 压缩后追问桌宠仍答"调研宁德时代2024年报"(任务连续性根治)**。**诚实 caveat**:WI-4a 实机未验(/goal 未注册 goal,单测已证);haiku 摘要层偶发反射(microcompact 层无)。**关联**：压缩窗口此前按错模型算(P-B)已于同日 [根治](../plans/2026-06-16-effective-llm-model-resolution/00-PLAN.md)(`effective_llm_model` 统一解析有效出站模型，真机 5 TC PASS)。证据 [testcase](../testcase/2026-06-16-compaction-bestpractice-phase1/) |
| 2026-06-16 | **有效出站 LLM 模型解析统一 — 根治 P-B（压缩窗口按错模型算）✅ 真机 windows-mcp 5 TC PASS** — 承接 [effective-llm-model-resolution plan](../plans/2026-06-16-effective-llm-model-resolution/00-PLAN.md)（2 轮对抗评审）。背景：压缩窗口/阈值此前取自 config `[llm] model`（种子 gemma4:e4b→32K default），但实际出站是中转站 gpt-5.5（面板设 1M）→ 按错模型算阈值（32K 而非 1M）过早压缩浪费窗口（2026-06-16 测压缩 §3.5 记录的 P-B）。**修复**（`84e4c251`）：`config.py` 新增 `effective_llm_model(cfg)`（in-process 用，优先 `cfg.llm.local.model` 运行时覆盖、空回落 raw、再回落默认）+ `effective_llm_model_standalone()`（工具用，不依赖 main 单例，治 `_cfg.config` 不存在的老坑）；压缩窗口解析（`main.py:1384`）、stub、breakdown 探针读模型名统一走有效出站模型；压缩窗口不再 hardcode 32000（`f53a63b5`）改按主模型经 model_info 解析。**真机 windows-mcp 5 TC PASS**（`efce0f62`）：有效模型解析根治验证。手测文档 10 TC [testcase](../testcase/)（`aef5101b`）。 |
| 2026-06-16 | **deep-research JS 渲染兜底(Option C / cdp-edge) ✅ 真机 windows-mcp 11/11 TC PASS** — 用户要给抓取链路加"很好的爬虫"治 JS/SPA 空壳站。**调研三方案**(子代理带源):Crawl4AI(底层 Playwright+Chromium ~100MB,且 PyInstaller 冻结包 import 不到=仅 dev)/复用 Tauri 自带 WebView(三端零体积但需跨"后端↔前端↔webview"反向链路+动 Rust)/**CDP-系统Edge**(纯后端 websockets 连系统 Edge 无头,POC 实测 quotes/js 空壳 29→1071 字、豆瓣 44→1244 字)。**用户选 Option C** 分期落地:本期 Windows 用 CDP-Edge 纯后端(小稳可进冻结包),Tauri-WebView 三端统一作下一期([plan](../plans/2026-06-16-crawl4ai-fetch-tier/00-PLAN.md) §3.0,经 3 轮对抗评审揪出 frozen 包不可用/反向链路高复杂度等硬伤)。**实现**(`a520ef7`+`cd15844`,codex gpt-5.5 起草适配器+本人修集成):`research_cdp_edge.py`(常驻单例无头 Edge + CDP `Target.createTarget` 隔离渲染取 outerHTML,Semaphore(2),best-effort 返 None,structlog `cdp_edge_render` 锚点);`default_extract` 接入(本地渲染排 jina 前,双闸 trafilatura<300且原始HTML>20KB、单轮触发≤4 护 300s、命中跳 jina 去重、`extractor` 标记,渲染后 HTML 重扫 ai_generated);`_js_render_*` 走 `_research_raw()`;websockets 入 spec hiddenimports。**本人修 2 真 bug**:Page.navigate 对走系统代理外国慢站卡 5s→改非致命超时也继续轮询兜底;**渲染失败无条件杀常驻浏览器**(单次超时就下个URL冷启10-17s顶穿300s)→改仅连接级致命才 reset。**子代理两轮评估 92%→补 3 缺口→100%**(保活改连接级+ai_generated渲染后扫测+eval超时单测)。**windows-mcp 真机 11/11 PASS 0 bug**([testcase](../testcase/2026-06-16-js-render-cdp-edge/) + [RESULTS](../plans/manual-results-2026-06-16-js-render/RESULTS.md)):TC-01 渲染 4 站(cctv/douban 90K-254K字)进报告引用;TC-06 timeout=2 优雅降级;TC-11 渲染失败后 msedge 进程稳定不被杀(保活真机证);TC-07 jina 同开但渲染命中 URL jina 调用0(去重)。回归 246 passed。Tauri-WebView 三端主线 + crawl4ai dev 档为下一期(plan §10 F2/F3)。 |
| 2026-06-15 | **deep-research Phase-2 — multi-query/HyDE + 中文一手源直连(巨潮/国标) ✅ 真机 UI 测 5/5 PASS** — 用户要一次性做完 Phase-2 三项(均中国可直连):**①multi-query/HyDE**(`02fd984` `_expand_queries` 一次 LLM 产 ≤4 扩展 query:改写+假设答案,提召回)**②巨潮资讯直连**(`research_sources.cninfo_search`:上市公司公告 PDF→pypdf 抽正文)**③国标系统直连**(`openstd_search`:GB/T 标准号+名称+hcno)。意图路由 `direct_source_for`(财报→cninfo/国标→openstd);直连源构造 Passage 跳长度门、高新鲜度,cninfo.com.cn 入 TIER_1。**windows-mcp 真机 UI 测 5/5 PASS**([testcase](../testcase/2026-06-15-deep-search-phase2/) + [RESULTS](../plans/manual-results-2026-06-15-phase2/RESULTS.md)):TC-P2-01 扩展产 13+ query 含 HyDE/英文跨语种;TC-P2-02 报告引用 #1=cninfo 年报 PDF(抽出 199.76 亿利润分配等真数据);TC-P2-03 引用=openstd GB/T 44265-2024(真 hcno hash);TC-P2-04 普通主题无直连;TC-P2-05 `direct_sources=false` 开关 opt-out 生效。**真测发现并修复 5 个跨层 bug**(单测全绿但真机暴露):cninfo 长子问题 searchkey 命中 0→topSearch 公司解析(`d826348`)/openstd 长子问题命中 0→关键词清洗(`eb68d89`)/cninfo 噪声词+openstd hcno死链+name日期误选(`a4b5d52`)/deep 档 180s→300s 超时(`ee38fc4`)/**`[research]` 配置开关全失效**(`2b7baa1`:`_cfg.config.raw` 读法但 config 模块无 config 属性→AttributeError 吞→恒默认,关不掉;同款 bug 在 image_tools/ppt_tools 已 spawn_task 跟踪)。**opus 4.8 子代理独立审计**:5/5 真执行、证据充分(真出站 POST/GET+落盘报告,非脚本回放)、无灌水、红线合规。回归 147 passed。Phase-3(SearXNG+Playwright)待做。 |
| 2026-06-14 | **桌宠渲染/形象线 — 装回 Live2D SDK + Hiyori + 动态形象下拉 + pet-tier1 交互 ✅（真机+preview 双 PASS）** — 桌宠渲染路线在「零版权自研」与「实用优先」之间收敛：**装回 Live2D SDK + Hiyori 免费模型**(`3d07e06`，实用优先、放弃零版权立场)。**pet-tier1 交互**(2026-06-13)：点击星星粒子特效(`efda96e`)+ 努力工作气泡 + tool_use 期间持续 working 状态(`80bb8b5`)+ 拖拽 wobble；**拖拽回正根治**——startDragging 吞 pointerup 致立绘卡倾斜(`373806c`)+ 每帧 reset 参数防 ADD 累积致立绘永远歪(`c29ffef`)+ 禁双击最大化(`9fda72d`)；真机/preview 双 PASS 验收报告(`417c27c`)。**设置面板「桌宠形象」下拉**(2026-06-14)：选择后立即换模型(`93bf616`)→ 改 vite 插件动态扫描 `assets/live2d` 实时列出所有模型(`7d94c11`)+ 下拉文字改黑加粗(`a8f1602`)；新增 Azuki-san/HoshinoAi/estella/Snow Leopard 等 live2d 模型资产(未入 git)。**自研 WebGL2 网格变形引擎(`mesh` backend)** 立为探索方向(`plans/2026-06-13-mesh-engine-s3/00-PRD.md`，B2 自研零版权 + AI 分层资产 + 程序拼层，P0 资产可行性未验，仅 PRD)。 |
| 2026-06-14 | **deep-research 精排(LLM rerank,免下载) + 深搜最佳实践调研 + codex 2轮挑战迭代 ✅** — 用户问"能否用 gpt-4mini 代替本地模型免下载"。**澄清**: 聊天模型能做"重排"不能做"嵌入";bge-m3 嵌入保持本地(全局/每消息用),要加的 reranker 走中转站 gpt-4.1-mini。**LLM 重排落地**(`2a8a4cb`): research_run 召回打分后用廉价模型(gpt-4.1-mini)做 cross-encoder 式精排(候选池[N,24]控token,精排分进relevance维度重算composite仍与域名权威加权),`[research].reranker=llm(默认)/local/off`,coverage.reranker观测;main.py 注入桥(同base+key换model,localhost不注入);本地 bge-reranker 留 Phase-future 可选(资源核算: 0.6B同bge-m3 backbone,int8~0.6GB,纯本地零依赖vs SearXNG需Docker劝退;安装包不增、首启多下~0.6GB)。**深搜最佳实践调研**(`9701200`,子代理11WebSearch+2WebFetch带源): SearXNG自托管聚合+三级抓取(trafilatura/Jina Reader/Playwright)+本地bge-reranker+中文一手源直连API+gap-driven反思loop;路线图+方案选型表+落地优先级落 [plans/2026-06-14-deep-search-best-practices](../plans/2026-06-14-deep-search-best-practices/00-ROADMAP.md);新建 plans/index.md 总索引+README指引。**codex 2轮挑战迭代**(`33b6ee5`+`7b92d8a`): 第1轮抓 log→logger(NameError隐患,我之前live-llm/semantic也中招)/rerank无超时/失败误标coverage/低覆盖no-op/localhost守卫;第2轮确认修复+收紧小集合覆盖率阈值(≤3全量)+健壮loopback(urlparse+ipaddress)。新增rerank全套测试;回归84 passed。**后续补强**(codex 复评+用户驱动): 乱码源剔除(`62c4a8d` is_mojibake 检测三类编码乱码,codex 抓到 [^11] 整段乱码)+ **多引擎兼容性降级队列**(`ccff6b1` 治"唯一引擎 DuckDuckGo 中国大陆常被墙":默认 必应→DDG 队列,百度备选,某引擎被墙/限流自动降级下一个,`[research].search_engines` 可配;region 感知;新增 bing/baidu 解析)。**真机验证**(`72e97f5` 前): 深度调研预制菜→**必应 18 次请求(自动走 cn.bing.com 中国可达)、DDG/百度 0 次(必应成功不降级)**、gpt-4.1-mini 精排 1 次成功、报告落盘——多引擎降级队列实测生效。**P1-3 Jina Reader 二级抓取**(`72e97f5`,治实测"必应优先但报告仅4源":JS渲染站 trafilatura 抽空壳→调 r.jina.ai 跑真浏览器返 Markdown 救回正文,<300字触发,默认开可关,extractor 观测)。**Jina 真机暴露 r.jina.ai 国外需代理(直连 ConnectError),裸中国用户连不上→改 opt-in 默认关+超时8s**(`ae64d7e`)。**P1-2 site: 定向官方域**(`9398505`,Phase-1 收尾):政策/企业/学术子问题额外 site: 定向搜(政策→gov.cn/上市公司→cninfo/学术→arxiv),一手权威源进候选池(命中域名天然 TIER_1);纯 query 零成本可关。**Phase-1 基本完成**(多引擎队列+精排+site定向+源质量过滤);Phase-2 中文一手源 API / Phase-3 SearXNG+Playwright 待做。 |
| 2026-06-14 | **deep-research V8 真机桌宠端到端 PASS + computer_use 误路由根治 ✅** — 干净重启后真机 windows-mcp 发"帮我深度调研2025钠离子电池…带引用出报告"→桌宠**真调 `research_run(depth=deep)`**→V8 全管线跑通(plan→**8 次 DDG 中文搜索**→trafilatura 抽取→**反思迭代 2 轮**→relay 200×5 无 500→cite_check 通过)→**报告落 `OutPut/Research/*.md` 15.2KB**:顶部 V8 观测元数据(17 来源/11 域名/2 轮/velocity/引用自检通过)+ TL;DR/Background/Current/**Open questions·controversies**/What's next 五段 + **17 条真实引用**全是权威中文源(新华网/工信部/中国科大/东吴证券/前瞻/产业蓝皮书)——**中文区域修复完美生效**。**诊断发现**: 之前几次"手动编排 web_search/web_fetch + computer_use 误路由"根因=**旧 default 会话上下文污染**把 LLM 带偏(干净会话即正确)。**computer_use 误路由根治**(`3e2f842`): screen_* 工具此前无条件暴露在 LLM schema(只 handler 运行时返 disabled)→污染上下文一诱导就误调撞墙;改 flag OFF(默认)挂 sentinel requires_env→schemas() 隐藏(LLM 看不到不可能误调),dispatch 仍返 disabled(留测试+纵深),14 passed。deep-research SKILL 加强"必须 research_run 禁手动拼"防漂移。**源质量过滤**(`5c65fed`,codex 评审驱动——codex gpt-5.5 审钠电报告判"真实性5.5/需大改:DDG 中文结果里 sohu/百家号/网易号转帖含 AI 生成内容混进证据池撑关键数字"): 新增 SELF_MEDIA 集降 authority=2.0(低于 unknown,稳被真权威源压)+ is_ai_generated() 检测"包含AI生成"声明直接剔除该源 + synth prompt 官方源优先(政策/标准/企业数据引一手源,只有自媒体支撑须标"据X报道未核实")+ 来源层级标签 + 数据口径必分清(产量/出货/规划/预测/装机不混)。顺手修上线级 bug: prompt `{媒体}` 未转义致每次 synth KeyError 退化(→`{{媒体}}`)。回归 142 passed。 |
| 2026-06-13 | **搜索 + deep-research 升级到 DeepResearch V8 — live smoke 全通过 ✅** — 用户要优化桌宠搜索/调研,参考 `deepresearch-v8.0.skill`,**约束不接任何外部搜索引擎**(付费/免费第三方都不要),只用现有 DuckDuckGo + V8 方法论。**Part1 搜索地基**(`cb8b035`): 新建 `search_provider.py` 合并三处重复 DDG 抓取(research/code/chat),**修中文瘸腿** — `kl` 不再写死 us-en,按 query 语言切区域(CJK→cn-zh);桌宠聊天注册 `web_search` 工具(快速查找,深度调研走 research_run);两处旧抓取改薄封装。**Part2 research_run 升 V8 实质**(`6da212d`): `research_scoring.py` 分层权威 TIER1/2/3(含中文源 cnki/xinhua/36kr/zhihu/csdn + gov/edu/.cn 加成 + wikipedia)+recency×topic_velocity+来源多样性/集中度,替代关键词字面打分;4 维 composite;**反思迭代**(depth=deep gap-analysis→补搜第二轮);**BGE-M3 语义 relevance**(main.py 注入 embedder,mock 降级);**LLM 桥修复**(改 config.llm.local+keychain key,弃 providers[0] 丢 key 隐患;synth 2048→4096);**报告落 OutPut/Research/*.md**+artifact;depth 档 light/standard/deep。**Part3 SKILL v0.2**(`f6a5c5a`): V8 方法论(路由门 简单查找走 web_search、输出分档 brief/full/delta、limitations强制、非显然洞察、红线、coverage观测、报告路径告知用户)。**Live smoke 全通过**: 真 DDG 中文 query→cn-zh 5 条真中文源(含 gov.cn TIER1)/英文→us-en;research_run 真搜真抽(zhihu/toutiao 403 优雅入 errors 不崩)+V8打分+cite_check通过+报告落盘 711B。测试新增 search_provider 12+scoring 16+reflection/semantic 4;research/web/skill 回归 214 passed。计划 [2026-06-13-deep-research-v8/](../plans/2026-06-13-deep-research-v8/00-PLAN.md) |
| 2026-06-13 | **Word/Excel 升级到「复杂文档」档 — 真机 WPS 验收双 PASS ✅** — 用户问"能生成复杂 Word/Excel 吗",评估发现原仅中等复杂度(Word 只有标题/段落/表格/分页;Excel 只有公式/图表/条件格式/表头),拍板"都做"。**Excel**(`excel_tools.py`): number_formats(货币/百分比/千分位/日期)、merge_cells、cell_styles(底色/字色/边框/对齐/换行)、charts 多图表列表、images 嵌图。**Word**(`doc_tools.py`): list(项目符号/编号/缩进)、段内混排 runs、字体颜色/下划线、表头底色 `<w:shd>`、image 插图、页眉/页脚/真页码字段(PAGE field)。**路径**(`office_paths.py`): resolve_for_write 加 default_kind → 无路径时落 `<user_data>/OutPut/{Doc,Excel}`(对齐 PPT 体验),paths 不可用回退 temp;schema+两 SKILL.md 同步新字段+复杂示例+必报完整路径。**测试**: 新增 8 Word+7 Excel 用例全绿,现有 49 BC 不破,artifact/pdf/paths 回归 35 passed。**真机 WPS 验收**(`336350d`,生产函数直出复杂样张肉眼确认): Excel 合并标题/千分位/百分比/橙底合计行公式/嵌图/柱状+折线双图表全渲染;Word 页眉页脚真页码 1/2/嵌图/蓝色大标题/段内混排(蓝粗+红粗同段)/编号+彩色项目符号/粗体下划线/深蓝底白字表头/分页符全渲染。 |
| 2026-06-13 | **设置卡改造: 模型上下文窗口(下拉全量目录+窗口只读+压缩阈值可调+K/M单位) ✅** — 用户三连问驱动: ①"压缩线在哪设" ②"为什么就这几个模型" ③"生效窗口文案改上下文总长度+单位 K"。**①窗口只读+阈值可调**(`958c204`): context_window 改只读展示(模型属性,危险手输移除),仅压缩触发阈值 compact_at_pct 可调(0.50–0.95),写回 model_overrides.toml;顺带删掉自己 6-12 埋的重复 model_context_set handler(劫持了 p4_ipc 全量版导致设置卡保存坏了2天)。**②下拉接中转站全量**(`c7b7920`): 原下拉只列 BUILTIN 画像表6个;改为挂载经 code_models_list 拉中转站 live /models 全量目录(旁路订阅),下拉=目录全量∪builtin∪当前选中,选非builtin模型即时 resolve 显真实生效画像。**③文案+单位**(`e02afaf`): "生效窗口"→"上下文总长度",fmtTokens(n) 把 token 数转 K/M(32000→32K,400000→400K,1000000→1M,25600→25.6K)。tsc 0err+卡片 vitest 8 passed;真机 webview 重载后下拉列出~20中转站模型、卡片显示"上下文总长度:400K/compaction 80%(≈320K)"。 |
| 2026-06-13 | **上下文治理: 大工具结果外置(治本) + 消息面板可观测性 ✅** — 压缩机制评估(优:三明治保头尾/语义摘要/tool配对安全/goal锚定/阈值随模型;缺:摘要不可逆有幻觉风险/一刀切98%/同步阻塞/治标不治本/头尾按条数)→ 用户拍板先做方向1外置。**①外置落地**(`1228ed8`): 勘探发现截断+ref store+fetch_tool_result 机制已存在且 companion 可用,真缺口=留存额度过宽(window//25 → 400K 给 16K/条,真机调研一轮 4×web_fetch=64K 字符两三轮即压缩)+ref 纯内存重启失效。收紧 threshold=max(6K,min(12K,window//60))+head/tail 6K/2K→2.5K/0.8K(单条留存降 2/3);ref store 加磁盘 spill(<user_data>/cache/tool_refs/,LRU 淘汰/重启后仍可 fetch 取回,容量 400 文件自清,pytest 内禁用防污染)。广域回归 330 passed。**②工具轨迹进大消息面板**(`749d465`): 用户问"小气泡有工具过程大面板没有"——断点在面板派生层主动滤掉 tool 消息(广播/store 都通);修派生+ChatRow 渲染紧凑工具行(🔧调用/✅完成,灰底等宽)。**③「隐藏工具消息」开关**(`bb3320d`): header 🔧 按钮 toggle,localStorage 持久化。**④正文槽垂直溢出根治**(`2e42e48`): WPS 不执行 normAutofit 声明 → _fit_text_to_shape 填充后按槽高主动缩字号(行数×字号×1.36 估算,0.5 下限);本地复现用户三页溢出案例全修。 |
| 2026-06-12 | **PPT 视觉评估闭环真机 PASS ✅ — 桌宠真「亲眼看」每页→评审→自动修→复审 clean(用户最初设想落地)** — 用户两问题驱动: ①模板/图文 deck 没有基于视觉的逐页评估修正 ②image-2 排版单一只有左右版。**②版式多样化**(`d239b5d`,调研 Gamma/Deckary/Slidesgo 落地): 6 种版式 cover/split_left/split_right/top/card/quote,`_assign_image_layouts` 自动轮换(封面cover/结尾quote/内容页轮换+split左右交替),按版式给图 prompt 自动追加负空间指令+禁字后缀,`_set_fill_alpha` 注入 `<a:alpha>` 真半透明遮罩。**比例拉伸根治**(`36b3a11`): `_place_cover` object-fit cover 按槽位比例中心裁切(picture.crop_*)零变形 + 按版式生成贴比例尺寸(split→1024 方图)。**①视觉评估闭环**(`c12048f`): 新模块 `ppt_visual_review.py` — 每页渲染图 768宽 base64 → 多模态 gpt-5.5 chat/completions 结构化质检(溢出/截断/压主体/对比度/版式匹配) → `_apply_review_actions`(change_variant/shrink_text,font_scale 0.72 下限) → `_render_fromscratch` 重渲染,最多 2 轮;vision 失败静默降级零影响;仅 AI 图文 deck 走;`[ppt].visual_review` 可关。**真机端到端铁证**: 星空观测 2 页 deck → 2 张生图 200 → **vision 真通**: 第1轮 `issues=1` 发现「要点折行孤字"门"」→ shrink_text → 重渲染 → 第2轮复审 `issues=0` clean;review1/review2 截图对比孤字确实消失。**闭环扩展到模板路径**(`6cd6020`,用户抓到覆盖缺口——模板12页deck有格式问题但闭环只接了AI deck): review_slides 加 mode=template(动作集 shrink_text/change_page),`_render_with_design_pages` 加 banned_pages+page_map,change_page=ban该页设计页换页重填;真机验证 vision 发现「数字重叠/对比度/孤字」与用户报告完全一致,2轮 change_page×5+shrink_text×1。**装饰数字重叠根治**(`3cd4580`): 高级感模板族 1/2/3/ONE 大编号是衬英文短词的背景装饰,中文长句必撞且原清扫豁免了它们 → decor_nums 单列 + `_rects_overlap` 与已填槽重叠即清(不重叠保留);本地复现验证目录页重叠 ONE 精确清除。测试 78 passed(闭环 13)。 |
| 2026-06-11 | **图文 PPT 全链路真机 PASS ✅ — 桌宠自主「AI 配图惊艳 PPT」端到端闭环** — 真机 windows-mcp 发「带AI配图的精美PPT·深海探秘·4页」→ LLM 自主写 outline(image_full+image_prompt,按 schema 引导:封面 title+caption/内容页 title+4条精炼要点) → **异步秒回**(`bf15cc7`,agent 13s 结束回合不卡) → 后台串行 **4 张 gpt-image-2 真出图全 200**(60s必挂已根治:300s+绕代理) → 16:9 裁切 → image_full 渲染(封面底部暗带/内容页**左侧自控深色面板**`737a742`,长中文要点舒展不挤) → deck 落盘 7MB → A-5 预览 4 张 PNG → **notifier 推回桌宠「✨图文PPT做好啦!已自动打开~(4页)」+ 自动打开**。预览实看: 深海潜艇电影感封面+热泉生态内容页,惊艳+内容丰富双达成。配套: 内容丰富 combo(`2ff7abc` 模板多段文字+AI图换图位+清英文穿帮)、模板槽 autofit 兜底。`-k ppt` 65 passed。计划 [2026-06-09-ppt-beauty-mainline/](../plans/2026-06-09-ppt-beauty-mainline/00-PLAN.md) |
| 2026-06-11 | **PPT 视觉闭环 — 渲染眼睛 + 三轮看图迭代收敛 + A-5 预览进聊天 ✅** — 用户拍板「deskpet 要自己看每页效果反复调到完美」。**① 建渲染眼睛**：WPS COM(`Kwpp.Application`)实测可渲染 pptx→PNG(MS PPT COM 本机不可用)。**② 三轮看图驱动修复**(`aafd898`)：轮1 看出封面标题极淡(继承背景装饰字浅色)+162pt×10 字撑爆孤行+section/two_column 标题替换进页缘出血形状被裁切 → `_shape_mostly_in_canvas` 出血排除 + `_fit_font_pt` 字号自适应 + `_ensure_readable_text` 过淡标题加深；轮2 「核心能力」仍折行孤字 → 单行标题必不折行解析解；轮3 公式漏内边距退化 → 修正(可用宽=框宽−0.28in)×0.92。终态 4 页全达标(封面单行深色大字/标题完整/编号工整)。**③ A-5 预览闭环产品化**(`35daf51`,codex 半成品用户叫停后 Claude 亲手接手完成)：新模块 `ppt_render.py`(COM 渲染,探测缓存/env 开关/从不抛) + `ppt_create` 三引擎成功路径自动渲染每页 → `kind=image「预览 第N页」` artifacts 进聊天,用户/agent 直接看到每页效果;config `[ppt].preview_render` 可关;接手修 codex 的 globals() hack + config mock 错层假绿。测试 `-k ppt` 59 passed;真渲染冒烟 4 张;端到端真模板 → file+2 image 预览 PASS。**下一步立项**：agent 多模态看预览图自主评审重生成(美学级闭环,需多模态管线)。 |
| 2026-06-11 | **gpt-image-2 出图「60 秒必挂」根治 — 双 60s 杀手定位 + 真链路 2 连 LIVE PASS ✅（B 路径出图阻塞解除）** — 中转站侧 Caddy×RequestLog 证据链：服务端 28/30 成功、耗时 57~221s，桌宠 ~60s 自己掐线（且服务端断连后照样生成+按次扣 $0.15，钱花了图没人收）。本地真链路复测进一步挖出**第二个 60s 杀手**：httpx 默认 `trust_env=True` 跟随 Clash 代理（127.0.0.1:7897），出图期间连接零字节流动被代理 ~60s 掐空闲（read=300s 修完仍 ~64s `RemoteProtocolError`，与 Caddy「客户端 59.8s 断开」互为两面）→ **2026-06-09「relay 挂起不返回」系误诊**，B 路径"真出图卡外部 relay"结论修正。修复（`image_tools.py`）：①read 超时 70s→300s 与服务端路由 timeoutMs=300000 对齐 ②默认 `trust_env=False` 直连国内中转站（`[image].trust_env_proxy` 可改回）③读超时/504 不再重试防双倍扣费（连接断/SSL/502/503 真瞬时保留重试）④payload 加 `quality`（默认 medium 降耗时，`[image].quality` 可覆盖）⑤key 缺失裸 401 落警告日志（对应中转站 6-9 那次 401，脱 Tauri 环境跑即复现）。**真链路验收**：真 keychain key + 真 `_generate_png` 两连 PASS — 79.9s/1.3MB（[live 证据图](../plans/2026-06-11-image-timeout-fix-live.png)）+ **184.1s/2.2MB**（同时穿越旧 70s 超时线和代理 60s 掐线，旧代码必挂三连）。新 TG `test_image_timeout_300s.py` 11 条锁死语义，图像全套 41 passed |
| 2026-06-11 | **PPT A-4 设计页复用模式 — 模板真实视觉进成品（用户实看驱动两轮修复）✅** — 用户实测反馈 A-2 layout 填充出的是白板：国内「高级感」模板的设计全画在示例页、layout 是 bare 标准布局。A-4 改为商业 AI-PPT 标准做法**「保留设计页 + 内容替换进文字槽」**(`9681930`)：`_analyze_design_page` 按字号/长度分类 title/body 槽/label；`_set_text_keep_style` lxml 层换字保留字体字号颜色(多行 deepcopy `<a:p>`)；`_render_with_design_pages` 页分类→贪心选页→填充→未用槽清空→按 outline 重排→未用页 drop_rel 删除；接线 design 优先→<3 页回退 layout 填充→from-scratch。**WPS 实看冒烟产物抓出两个穿帮再修**(`8d38e9f`)：①封面双层艺术叠排(162pt 背景字+141pt 手写体)中文化后重叠 → subtitle 限 ≤60pt、>60pt 非标题大字归 decor ②内页残留 About Me 等英文主题词 → decor 填充后清文本(纯数字/≤2 字符步骤编号保留)；subtitle 无槽可放时从 decor 挑与标题垂直不重叠(<50%)的形状放(`_take_subtitle_from_decor`) — section 副标题不丢、封面不叠字。测试 `-k ppt` 55 passed；真实模板(高级感01)冒烟 4 页全替换+LOREM 清零+装饰编号保留 PASS；WPS 实看封面干净 ✅。用户 29 个模板勘探 27 个可用(`resources/PPT_Template/`,不入 git)。计划 [2026-06-09-ppt-beauty-mainline/](../plans/2026-06-09-ppt-beauty-mainline/00-PLAN.md) |
| 2026-06-11 | **goal-completion 测试线 4 待修 bug 修掉 2 个 + 2 复验全闭环（真机 windows-mcp 全 PASS）✅** — 承接 [HANDOFF-2026-06-11](../plans/2026-06-11-HANDOFF-goal-completion-testing.md)(40/40 TC+opus 二轮审计「接受归档」)。**① bug#1 TC-5.1 skill 自动披露零匹配**(`3526ac1`+`b439bbc`)：三层根因(SkillComponent 走 select() 把 task_types=[] 的 builtin 全滤掉→total=1 / lifespan sync build 调 async encode 静默 no-op / top_sim log 打 strong[0] 掩盖真分)→ auto ON 用全集 + `build_async` 预热(boot 锚点 `fp5_skill_matcher_prewarmed cached=12`) + **混合匹配**(SkillMeta.triggers 词法路+when_to_use 进 embedding,BGE-M3 短中文 query 区分度不够 8-query 校准实证;12 builtin 补全)。真机:`skill_auto_disclosed total=12 strong=1 auto_loaded=1 names=['deep-research'] top_sim=0.950` + **TC-5.7 连带解锁**(同 turn skill_invoke→compaction fired→`skill_remounted names=['recall-yesterday']`)。**② TC-4.5 复验+二阶修复**(`ac76d48`)：复验发现 upsert_replacing 只 supersede 最新一条→历史脏堆积永不自愈→改 supersede 全部 active 同 key;真机 `/goal`×2 → 15 条脏行一次自愈到 active=1+链 57→83→84。**③ bug#2 候选卡/inflight 吞消息**(`c9b31f9`+`fb25ac2`+`fc1e37c`)：真因三层——InputBar+**SessionGridView tile**(实际复现入口)两处 `Enter/按钮 inflight→stop()` 打好的字静默丢弃还打断 turn / 后端 codify 300s Future-await 内联 chat task 被同 sid 抢占连带杀 Future / **ws.ts send() socket 非 OPEN 直接丢消息**(UI 显气泡 backend 收不到=完整吞症状)→ 有文字永远发送(后端同 sid 抢占语义)+confirm 拆独立后台 task+ws outbox 队列重连 flush。真机:inflight 中发消息立即入库处理、卡 pending 中"日本的首都"秒答、点忽略 `confirm_received cid=23 reject`、超时路径 cid=21 整 300s 自动 reject(历经多 turn+页面 reload 仍存活)。pytest skill/codify/facts 全绿+tsc 0err+vitest 120。**④ bug#3 ArtifactCard**(`274eafe`)：真机重跑 ppt_create 逐层追,**查实误诊** — 卡片全链路正常渲染(envelope→WS→store→卡含打开/在文件夹中显示,截图 bug3-ppt-artifactcard-renders.png);原观察假象=tile 预览设计上滤 tool 气泡+Virtuoso 动态撑高错位。真子问题已修:emit_receipt 从不传 artifact_shas→receipt.artifacts 恒[]→现抄产物 sha256(verify gate 可对账)。**⑤ bug#4 强杀丢登录**(`7b9e11f`)：原假设(退出钩子落盘)推翻 — 真因 refreshSession 对任何 !res.ok 都 localLogout 擦 keychain,relay 504 撞上 boot 刷新即被擦;修复仅 400/401/403 才清 session,5xx/408/429 保留 token 重试(auth 32 passed)。**4 待修 bug 全部结案**(2 真修+1 误诊+1 真修),强杀重启×4 登录保留+gpt-5.5 链路回归 PASS。证据 [manual-results-2026-06-10-FP345/RESULTS.md](../plans/manual-results-2026-06-10-FP345/RESULTS.md) |
| 2026-06-11 | **PPT 精美主线 A 路径(模板填充)真机 E2E 全链路 PASS ✅** — 用户拍板「干净专业就够了」。A-2 模板填充机制(`4265c27`)+A-3 选择/发现wiring(`a515d8f`,bundled目录+按名解析+动态schema让LLM发现)+**中文布局别名**(`67b29d1`,国内「高级感」模板布局名多中文'标题幻灯片'/'两栏内容',补中英别名正确映射)+**drop_rel删示例页修复**(`1c430ee`,真机发现删示例页只remove(sldId)留底层部件→Duplicate name→WPS串显旧示例页'graphic designer';drop_rel根治)+**3个干净bundled模板**(`f590b1a`,jianyue-business/gaoji-minimal/gaoji-clean)+**env默认模板**(`78c0f06`,DESKPET_PPT_DEFAULT_TEMPLATE默认套模板不依赖LLM每次传)。**真机E2E**:桌宠输「做个AI发展的PPT要专业」→LLM调ppt_create(5页专业大纲)→自动套jianyue-business模板(日志确认布局名='标题幻灯片'等模板中文布局)→生成干净专业deck(封面+绿点装饰+内容填充正确)→桌宠回「做好啦~专业风格《人工智能发展概览》共5页」→WPS打开正常。勘探:用户29模板27个正规可填充;bare layout观感干净专业(炫酷设计在示例页,用户接受clean)。-k ppt 48 passed。计划 [2026-06-09-ppt-beauty-mainline/](../plans/2026-06-09-ppt-beauty-mainline/00-PLAN.md) |
| 2026-06-09 | **PPT 精美主线 B 路径 + 本地链接修复（代码+单测完成；B 真机 E2E 待跑·relay flaky 实锤）🟡** — 承接 [HANDOFF-2026-06-09](../plans/HANDOFF-2026-06-09.md)，用户拍板「A+B 都做」。**① 本地文件链接修复**(`cb370c6`)：LLM 写的 `[打开 PPT](C:\...\xxx.pptx)` 在 webview 打不开 → `MessageBubble.tsx` 新增 `isLocalFilePath`/`toLocalPath` 判定(盘符/UNC/file://‎/POSIX)→ 本地文件走 `invoke(artifact_open)` 系统应用打开，http(s) 保持外开;tsc 0err。**② B-1 图像 relay 健壮性 + 同步批量原语**(`c7a52d4`)：`image_tools.py` 漏接的裸 `ssl.SSLError`(UNEXPECTED_EOF) 显式归类瞬时可重试 + retry 2→4 指数退避(3/8/20) + `_save_image` 加 `time_ns()` 防批量同秒撞名 + 新增同步 `generate_images(prompts)` 原语(async worker 不返路径,PPT 拼图需确定性同步)。**③ B-2 整页生图模式**(`27c5558`)：`ppt_tools.py` 加 `image_prompt` 字段 + `ppt_create` 渲染前 `_autofill_image_prompts` 调 B-1 批量生图回填 `image_path` + 新增 `image_full` 全幅铺图布局(底部深色标题带) + `_PPT_SCHEMA` 加字段让 LLM 知道可用;dry_run/无 prompt 零网络调用、生图失败优雅降级。单测全绿(B-1 22 passed / B-2 `-k ppt` 39 passed)。**④ 真机验 B 受阻于 relay flaky**：起 app(backend 8100 + 真 BGE-M3 + relay /v1/models 200)，windows-mcp 真发「生成一张图」→ 消息收到+LLM 200，但 **relay 对 capability_gate 那次调用 504 Gateway Timeout** → 没暴露 generate_image 工具 → 只纯聊天(`tool_calls=0`)。**这是交接「relay 不稳」的实锤**(504 间歇，且整条链路都受波及，非仅图像)。relay 真出图待用户重发重试验证。**⑤ A 路径勘探**：参考 deck `.tmp/ai-education-deck.pptx`(pptxgenjs 好看版) **不能当模板**(仅 1 空 master + 1 个零占位符 'DEFAULT' 布局，全自由浮动图形)→ A-1 需真·PowerPoint 授权的 .pptx 模板(程序化难产出好模板)，待用户提供模板资产。**⑥ A-2 模板填充机制**(`4265c27`)：`ppt_create(template=.pptx)` 载模板用其 layout 加页填占位符(TITLE/BODY/PICTURE 等)、格式继承、无效优雅回退；5 测+`-k ppt` 44 绿。**⑦ A-3 模板选择 wiring**(`a515d8f`)：bundled `ppt_templates/` 目录 + 按名解析 + 动态 schema 让 LLM 发现可用模板；`-k ppt` 48 绿。**用户只需丢正规 .pptx 模板进 `backend/deskpet/tools/ppt_templates/` 即自动可用。⑧ B 双 bug 修复**(`93a695a`)：B-1 receipt 序列化炸(注入 `_image_worker` 进 args)→剔除 `_` 前缀键+default=str 兜底；B-2 工具路由(「生成图片」误走 web_fetch 网搜→images 接口 0 调用)→强化 generate_image/web_fetch 描述区分度(路由真验待 live test)；receipt/registr 141 绿。**两个 codex 并行实现(文件不重叠)。⑨ B 真机验证(windows-mcp)**：重启加载新代码后真发「生成图片」→ 日志 `name='generate_image' prompt='橘猫...'` **两次都正确调 AI 生图工具不再 web_fetch → 路由修复实测确认 ✅**。但**真出不了图**：根因 = **外部 relay `chinzy.com/v1/images/generations`(gpt-image-2) 挂起不返回**(sync 回退 4m39s 空档零 httpx 响应=请求挂起;async worker 同样卡这)。**deskpet 图像链路本身无 bug**(路由修好/worker 正常/sync 回退正常;之前怀疑的"worker not alive"是 sync 模式按设计不启动 worker,非 bug)。⑩ **快速失败调参**(`381fa82`)：relay 挂起时原 4×100s≈7min 才报失败 → 改 70s×2≈145s 快速告知(保留 ssl 瞬时重试)。**B 结论：deskpet 侧已尽;真出图卡在外部 relay 出图接口(需用户换可用图像 provider/model)。** 计划 [2026-06-09-ppt-beauty-mainline/](../plans/2026-06-09-ppt-beauty-mainline/00-PLAN.md) |
| 2026-06-07 | **两份手测文档全量执行 — 47/47 TC 达终态（41 PASS + 6 BLOCKED-带原因）✅** — 按 `/goal "跑完真机UI测试"` 建 PROGRESS.md 续跑机制(每条立即更新,后续 agent 可无缝接)。A组(goal-completion 22)=17 PASS+5 BLOCKED;B组(跨层bug回归 25)=24 PASS+1 BLOCKED。真机 windows-mcp 截图 PASS 11 条。**独立审计子代理终审🟢诚实可信(41 PASS 实地核验无虚标)**。6 BLOCKED 均带具体原因(LLM诚实性/verify-gate范围/压缩不触发/已知前端bug),**无新功能代码bug**。按审计补做 TC-5.2/5.7 压缩 workaround(单轮52k字)→压缩仍不fire→**新发现 FP-5 WI-4.0 压缩疑生产从不触发(潜在死链,已建调查 task_ae1af91b)** + 候选卡chat渲染bug(task_01be24af)。证据 [PROGRESS.md](../plans/manual-results-2026-06-07-full-run/PROGRESS.md) |
| 2026-06-07 | **goal-completion 真机 UI 测试 — 全 5 FP 共 9 个 TC windows-mcp PASS ✅（含 2 招牌）** — /goal "跑完真机UI测试" 续跑：HEAD(全9修复)重启，写权限门用 `permissions_auto_mode.json={enabled:true}` 放行(等价用户「本会话始终允许」)。**新增真机 PASS**：**TC-3.2 真完成放行**(agent 真写 hello-fp3.txt 16B+receipt→verify_gate 跑→无 nudge 放行,不误杀真完成)、**TC-4.1 重启跨会话召回★**(陈述"数据库用PostgreSQL/JSONB/并发"→抽取 facts(decision)→**taskkill重启清内存**→重启后准确召回 PostgreSQL+JSONB+并发,逐点一致)、**TC-4.3 偏好冲突**(乌龙→绿茶 supersede→推荐反映最新绿茶)。累计 9 真机 PASS 覆盖全 5 FP(5.3招牌/5.1/5.8/5.6/4.5/4.2/4.1★/4.3/3.2)。**抓出测试法限制(非bug)**：/goal 粘贴(Ctrl+V)不触发前端 slash 自动补全→走普通chat当任务;后端 slash 解析正常(键入触发)。剩余受限:TC-3.1(gpt-5.5太诚实拒绝伪造声明)、3.3/3.4(需slash键入法+诱导)、5.2/5.4/5.7(压缩多轮+relay间歇ReadError)。截图存 manual-results-2026-06-06-FP345/screenshots/。证据 [RESULTS.md](../plans/manual-results-2026-06-06-FP345/RESULTS.md) |
| 2026-06-06 | **goal-completion FP-5 自动披露 + FP-4 偏好注入 — 真机 PASS ✅ + 抓修 7 处系统性 fanout-gating 生产 bug**（续跑会话）— 真机跑 TC-5.1 强匹配载入 / TC-5.8 复用自创技能时，逐层定位到 FP-5 auto-disclosure（WI-4.1/4.2）虽"接线 ready"却在生产 code 会话**完全不生效**，连环抓修 **6 层跨层契约漂移**（单测全用 sync mock + 带 skills 的 config，全绿掩盖生产死链）：①SkillComponent 无观测日志 ②`assemble()` 传的 config dict 漏 skills 段→auto_enabled 恒 False ③`code` policy prefer 漏 skill→组件永不 fan-out ④code 会话靠文本分类(→chat 无 skill)→加 task_type_override="code" ⑤codify 生成的 SKILL.md 漏 task_types→select() 永远过滤掉自创技能 ⑥**SkillMatcher 同步调 async embedder.encode→拿到未await coroutine→缓存恒空→top_sim=0.0 永远零匹配**。修复后真机硬证据：`skill_auto_disclosed total=2 strong=2 auto_loaded=2 names=['meeting-minutes-to-ppt']`（自创技能正文真机自动预载进 prompt，TC-5.1/5.8 PASS）。**同根第 7 处**：`preference_profile` 组件也不在任何 policy→FP-4 偏好/画像注入(WI-3.2)生产全局死→补 chat/recall/task/code/plan/emotion policy，真机 `preference_profile_injected facts=2 task_type=code` 验证注入恢复。+2 async-embedder 回归测试守护生产契约。304+112 焦点测试绿。这是 `feedback_cross_layer_contract` 最深演绎：组件注册+flag开+单测绿，但 policy fanout 层逐个断。证据 [manual-results-2026-06-06-FP345/RESULTS.md](../plans/manual-results-2026-06-06-FP345/RESULTS.md)。**待**：TC-3.1~3.4(verify gate)/TC-4.1/4.3(跨会话召回·偏好冲突)/TC-5.2/5.4/5.7(压缩追目标/拒绝/重挂) 续跑(需 fresh context + 部分需 companion-chat venue) |
| 2026-06-06 | **goal-completion FP-5 技能自创招牌全链 — 真机 windows-mcp FULL PASS ✅ + 抓修 5 类生产 bug** — 真机模拟人工跑通完整闭环：多步任务→agent 完成全部工具→codify→propose→**前端绿色「✨新技能」卡真机渲染**→**真坐标 SendInput 点击「保存技能」**→后端 `skill_candidate_resolved cid decision=accept`→**SKILL.md 真落盘**(`<user_data>/skills/user/meeting-minutes-to-ppt/SKILL.md`,frontmatter `requires_script:false` 声明式不执行代码+author:self-codified+6步骤)。**veto-1 完全满足**(真截图+真点击+落盘证据)。**本会话抓修 5 类生产 bug**(独立子代理验收+真机诊断)：①**config 漏解析 `[skills.codify]`** → 技能自创整功能生产静默死(子代理 wiring 评审漏的 config-loader 层,boot 缺 `fp5_codify_wiring_ready` 定位)②**5 处跨层接线断裂**(services 未注册/ToolPathRecorder 从未构造+从未喂数据/build_agent 不传 skill_loader+matcher+recorder)+补 WI-1.6 record_tool→complete 喂数据 ③**relay ReadError 鲁棒性**(中转 relay 经代理掉流式连接致 agent 立即崩 → adapter 归可重试+registry 重试同 provider+流式路径掉链前干净重试,真机救活 agent 在烂代理下完成全部工具)④**前端 ephemeral-card bug**(skill_candidate 卡 message reload 时丢失 → set_messages 保留 awaiting 卡)⑤**方案 B**(codify hook 抽 helper,FinalEvent+ErrorEvent 双触发)。+TC-4.5 B-10双写钩(GC修复)+TC-5.6 trivial不弹卡真机PASS。全程 flag-OFF 字节基线退 0+156 测试绿+前端 tsc 0err,严守 veto-1 无伪造。21 commit。证据 [manual-results-2026-06-06-FP345/RESULTS.md](../plans/manual-results-2026-06-06-FP345/RESULTS.md)。**待**：TC-3.1/4.1 等其余 TC 续跑(relay 鲁棒性修复后可跑通) |
| 2026-06-05 | **goal-completion 升级 5 个 FP 后端实现全完成 ✅（FP-1 真机手测门 PASS + FP-2/3/4/5 后端全绿）** — 打通"断掉的目标线"闭环：用户说目标→【FP-1 持久化】重启仍在(真机 windows-mcp load_persisted restored=0→1 + UI /goal 查仍在,5截图)→【FP-2 抗漂移】re-anchor 决策点每5轮注入[目标锚定]+task图跨agent共享+handoff带goal+resume续目标→【FP-3 自我纠错】verify接goal_text对照(完成判定=客观证据三绿,人格禁入no_persona_leak)+结构化反思真重规划重试(§7死循环上界LLM调用≤max_iter+3)+高后果evaluator+R-T3降级矩阵+R-T6 shadow→【FP-4 记忆+人格】goal/decision/constraint facts(category-agnostic召回零改+MemEval 491无回归)+PreferenceProfileComponent(priority85/dynamic/Pin置顶/红线无谄媚)+修 daily_decay 从未被调用 bug+light写入快路+单向钩防import环→【FP-5 Skills】接通compaction到主loop(★全回归217绿,最高风险)+embedding强匹配自动载正文+压缩后重挂+技能自创codifier(只生成声明式SKILL.md不执行代码,用户确认门Future-await). **流程纪律**：先冻结§6契约/§7账本(实地核真) → 每FP superpowers sp-writing-plans 出 TDD 计划 → 子代理 TDD 实现(独立文件并行/同文件串行,子代理只实现主线统一提交防撞库) → spec 审查门 → 提交. **自查抓修真问题**：R-T5 字节门(拆 session_goals/goal_tasks 出共享_DDL,goal_mode OFF 不建表,基线退0)、I-1(mark_done落库防完成目标复活)、daily_decay 从未调度、goal_checker 失败 done=True→skipped(不假标完成). 全程 flag 默认 off 字节级 BC,5个FP后 R-T5 基线仍守. **待**：FP-2/3/4/5 真机 windows-mcp 手测门 + FP-5 前端确认卡(批量补跑,spawn_task,均后端全绿). 计划+证据 [plans/2026-06-04-goal-completion-upgrade/](../plans/2026-06-04-goal-completion-upgrade/)(FP-1~5/ + BLOCKERS.md) |
| 2026-06-05 | **v0.6.0-beta.2 发布上线 — 孤儿进程修复 + 自更新闭环 + 模型外置首启下载（真机 E2E 全 PASS ✅）** — 分支 `fix/backend-orphan-cleanup`（兼作构建/发布工作区）。**① 孤儿进程根治**：关桌宠后重开报「8100 被占用」根因=`kill_child()` 空操作（supervisor 已 take 走 Child）+ Win 不回收子进程；修法新增 `job_object.rs` 全局 Job Object(KILL_ON_JOB_CLOSE) + 主窗 Destroyed→app.exit。真机 E2E：只 `taskkill /F /PID <deskpet.exe>`（不带 /T）→ 5 个 backend 全被连带收割 + 8100 释放。**② 自更新闭环**：设置面板加「检查更新」按钮（`SettingsPanel`+`updaterError.ts`）；修 `release.yml` prerelease→false（原 endpoint 因 prerelease 永久 404）。**③ 模型外置（NSIS 化）**：`DESKPET_BUNDLE_MODELS=0` 瘦包 + CPU torch → 3.7GB 打不出 NSIS 变 **304MB**；新增 `model_provisioner.py` 首启从 **腾讯 COS** 直下 bge-m3+whisper（manifest 驱动 urllib，hf-mirror 实测与 hf_hub 0.36 不兼容故改 COS）+ 前端进度横幅；11 单测绿。**④ 签名密钥轮换**（旧口令遗失→新无口令 key 5E3B6A21）。**⑤ 发布**：本地签名构建 → 发公开 `deskpet` release(Latest) + COS；updater endpoints=COS 主 + GitHub 备，双端 200。**真机 E2E**：装 beta.2 → 首启从 COS 真下模型(37 文件落盘) + 孤儿修复复验 PASS。详 [PLAN](../plans/2026-06-05-nsis-model-externalization/PLAN.md)。~~未 merge master~~ **已 merge master**（`94c407b` Merge fix/backend-orphan-cleanup；后续已 bump beta.3，原「未 merge」标注已过时；修正于本次审计） |
| 2026-06-04 | **goal-completion FP-1 目标持久化地基 — 真机 windows-mcp 手测门 PASS ✅** — 先冻结 §6 goal_text 契约（定稿唯一 SessionGoal schema，解决 00-PLAN §6.1 与 blueprint §2 的 done/status·criteria·subgoals·max_iterations 分歧）+ §7 重试账本（实地核真 attempt 计数=per-session 共享额度·in-memory 重启清零）。superpowers sp-writing-plans 出 10-task TDD 计划 → 子代理实现：WI-1.1 Durable Goal Store（SessionGoal 扩字段 + session_goals 表 + SessionDB 3 薄方法 + bind/persist/load_persisted/get_goal_text）+ T1 increment_iteration 落库 + R-T1 lifespan 接电 + WI-1.6 ToolPath 录制 + R-T5 flag-OFF 字节基线 + R-T7 多 worktree 隔离。**自查抓修 2 真问题**：①R-T5 字节基线 FAIL（session_goals 在共享 _DDL 被常态 ensure 建表 → 违反 flag-OFF 字节护城河）→ 拆独立 `ensure_session_goals_table` 门控；②code-review I-1（mark_done 没落库 → 完成目标重启复活）→ 加 persist_done。后端 23 焦点 + 267 回归测试全绿 + R-T5 baseline 退 0。**真机手测门**（windows-mcp SendInput 真点击 + Clipboard 真输入 + 截图）：Code 面板输 `/goal 帮我整理本周三个会议纪要FP1测试` → session_goals 落库 → `taskkill deskpet.exe` + 杀 orphan vite/backend + fresh 重启 → backend log `load_persisted restored=0→1` → 重启后 .tmp 会话恢复 + `/goal` 查询 UI 显示「当前目标：帮我整理本周三个会议纪要FP1测试」（[05 截图](../plans/manual-results-2026-06-04-FP-1/screenshots/05-goal-status-restored.png)）。详 [FP-1/02-manual-test.md](../plans/2026-06-04-goal-completion-upgrade/FP-1/02-manual-test.md) |
| 2026-06-04 | **工具层「极其严格」手工测试 — windows-mcp 真机 40/40 全 PASS ✅** — 按 `testcase/tool-layer-RIGOROUS-manual-test.md`(40 TC)逐 case 真机测：windows-mcp SendInput 真点击 + 剪贴板真输入 + CDP9333 定位/验证 + backend 行为日志三重证据。覆盖全部工具 + 中间件横切(权限门按category上色：截图证 write_file橙/shell红/desktop_write橙 + 熔断3次OPEN + last-mile ArtifactCard多产物 + receipt+HMAC+duration_ms非0 + verify gate off/shadow/strict)+ 6个config重启TC(disabled_toolsets双层门控★回归/dangerous_allowlist/default_timeout设计陷阱/strict_unknown_toolset潜在bug/artifact_envelope ON-OFF/verify shadow)+ 健壮性(优雅失败/越界写OS拦/大输出截断/并发tile隔离不串台)+ 已修bug回归(非法permission_category)。**执行期发现并修复 2 个真 bug**：①doc_create 嵌套 element 格式渲染成字面 dict 字符串(`backend/deskpet/tools/doc_tools.py:_add_element`)②金黄 hint 卡因 last-mile envelope 嵌套不触发(`tauri-app/src/code-panel/MessageBubble.tsx:splitToolError`);均修+复测通过。建可复用 harness(testcase/_cdp.py + _send.py + _approve.py + deskpet-input.ps1 SendInput圣杯)。详 [tool-layer-RESULTS.md](../testcase/tool-layer-RESULTS.md)。回归单测已补(doc_tools 2 + splitToolError 3,全绿)+ 已提交 commit 4e8f449。 |
| 2026-06-04 | **语音对话同步到消息框修复 — 真机 A/B 闭环 PASS ✅** — bug：桌宠主窗口语音不进「消息·主线程」（语音落库但消息框看不到、重开也不补）。**一次根因**：`VoicePipeline` 只 audio_ws point-to-point、从不广播；修复复用文字同款 `_broadcast_default_chat_peers` 发 chat_v2_user_echo/final、skip originator（主窗口已 audio 显示不重复）。**真机暴露二次根因**：`VoicePipeline.control_ws` 是 audio 连接期快照，backend respawn 后 audio 常先于 control 重连 → 快照=None → 旧守卫 `... and self.control_ws and ...` 挡掉广播（落盘诊断实锤 `control_ws_none=true`）。**二次修复**：守卫去掉 control_ws 依赖 + main.py audio_channel 注入实时解析 originator 的闭包（`_control_connections.get(session_id)`，对齐文字路径的实时 `_ws`）。**TDD 16 单测全绿**（含 `test_broadcasts_regardless_of_control_ws_snapshot` 根因守护：control_ws=None 仍广播）；architect 子代理逐行审计 P0/P1 → GO-with-changes；诊断证 fan-out `control_keys` 含 `message-panel-main`、`originator_in_values=true`。**真机 A/B**：修复前真人语音停在 id2152 不进消息框（[11 截图](../plans/manual-results-2026-06-03-voice-msgpanel/screenshots/11-msgpanel-full.png)）；修复后真人语音「提醒买菜」(id2157/2158) **实时进消息框**（用户截图确认）。详 [fix-spec v2](../plans/2026-06-03-voice-msgpanel-sync/00-fix-spec.md) |
| 2026-06-04 | **多屏跨 DPI 拖动 + Live2D 角色渲染修复（master 直提）** — 用户实测桌宠在双屏（Samsung 主屏 dpr 2.13 + Xiaomi 副屏 dpr 1.42，webview dpr 异常比显示器 scale 高 ~1.42×）：①拖不回小屏（抖动+弹回）②拖几次只显示一半 ③拖到小屏角色右半被裁。**7 处根因**：`window_geometry.rs` 移除 on_resize clamp（跨 DPI 振荡）+ `pin_size`（逻辑尺寸跨屏舍入漂移 375→360→657）；`App.tsx` 边缘吸附 250ms 防抖 + 显示器局部坐标转换（非主屏 pickEdge 误判甩回主屏）；`Live2DCanvas.tsx` 角色列宽 cap 在视口内 + dpr 纳入 size 状态/matchMedia 触发画布重渲 + `modelReady` 触发异步模型加载后重渲 + scale 用基础尺寸（避免读已缩放 model.width 致模型爆炸）+ **删除重复 resize effect**（旧版拖动时覆盖修好结果）。真机 SendInput 拖动 + 白板背景截图验证：三星/小米 boot + 拖动后角色均从头到脚完整居中；10 次跨屏尺寸 pin 360×600 零漂移。详 [手测报告](../plans/manual-results-2026-06-03-multimon-drag/REPORT.md) |
| 2026-06-02 | **记忆系统审计 + 修复 #1-#4（master 直提，4 commit）**：3 路交叉验证（我的代码审计 + silent-failure-hunter 子代理 + 最佳实践调研子代理）。**#1 FATAL-A**(`4b700dd`)：lifespan 从不调 backfill_missing → 任何 embedding 缺口永久无声（"刚说的话下次不记得"）→ 加启动自动 backfill 兜底 + 修正虚假注释。**#2 FATAL-B**(`30cbcdf`)：检索/嵌入静默降级只 log.debug → retriever FTS / facts embed / enhanced_retriever 降级点升 warning（vector_worker 已 warning 故不动）。**#3 MemEval**(`56c9c59`/`48d8549`)：~18 双语"字面vs改写"召回对照，真 BGE-M3 改写 Recall@5=**1.0**（证 dense 语义召回真工作、非吃 FTS 字面红利）+ 修模型路径脆弱性（用户迁 F 盘后 C: 硬编码致 model_required 整批 ERROR → 新增 resolver）。**#4**(`4d8de40`)：冲突消解机制（mem0 merge/supersede + Zep 软失效 + 时序链）早已建好且 37 测试全绿，用户决策出厂点亮 facts_extract+enhanced_retriever+cross_key_merge（dataclass 默认仍 False 保字节契约；**真机 E2E 待跑**）。详 [审计+最佳实践报告](../plans/2026-06-02-memory-system-audit-and-best-practices.md) |
| 2026-06-02 | **superpowers ③ verify_gate strict + code claim patterns — 真机不误杀 + 单测 9/9 PASS**：决策3"硬卡"。claim_patterns.yaml 补 4 条 code 场景(已创建/已修改/测试通过 + en)5→9;dev 翻 `verify_gate_mode=strict`(出厂仍 off)。关键判断:registry.execute_tool 对每个工具都 emit_receipt → 真调过的任务 claim 命中放行、裸声明(fake)拦。真机:STRICT_CHECK.md 任务 write_file→无 verify_gate_nudge→放行完成(未误杀);单测证 fake 无 receipt→拦、未来时→不误判、shadow→不拦。出厂是否翻 shadow 待定。详 [verify strict 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-verify-strict.md) |
| 2026-06-02 | **superpowers Layer 1B 偏好记忆(BGE-M3) — 真机 E2E + 单测 7/7 PASS**：决策2 的"记下来后续相同直接做"。新组件 `preference_memory.py`（计划/意图两类 + BGE-M3 cosine + JSON 持久化 + list/clear）。计划记忆接 plan-confirm 门:用户点[执行]→record approved;相似任务 match 命中→自动确认跳过等待。真机:Task A(PREF_ALPHA)走门点[执行]记录→Task B(PREF_BETA 只改文件名)`plan_confirm_auto_approved score=0.936`无 awaiting 直接跑→文件创建。接线踩坑:ServiceContext register 有 allowlist(加字段+白名单)。flag 默认 OFF 出厂不构造。意图记忆(决策1)组件已支持待接线。详 [E2E 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-layer1b-preference-memory.md) |
| 2026-06-02 | **superpowers Layer ① plan-confirm 硬门 — GO+CANCEL 真机 E2E 2/2 PASS**：决策2 严格版——code 模式明确任务先出 plan + 等用户点[执行]再跑 ReAct。复用现成 `maybe_extract_plan`（plan.py 自标的 "future enhancement"），加确认门(后台 task await Future 不阻塞 recv loop，最小改动)+ `plan_confirm` WS + 前端 [执行]/[取消] 按钮。**调试挖出真问题**:grid tile 预览(SessionGridView)自己的 renderer 过滤掉了 "plan" 角色 → tile 内单独渲染确认栏修复。CDP 真机:GO→暂停(零 dispatch)→点[执行]→todo+list+write+read 执行→GATE_OK.md 建成;CANCEL→点[取消]→零执行+文件不创建。flag 默认 OFF 出厂字节级不变。详 [E2E 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-plan-confirm-gate.md) |
| 2026-06-02 | **superpowers 工作流集成 code 模式 — Layer 1A 落地 + 真机 E2E 3/3 PASS**：用户反馈 auto 模式"问个问题就埋头乱改、不澄清、不验证就说完成"。调研发现根因是 code 模式纯 ReAct 无工作流骨架 + persona 通篇"优先使用工具"。**纠正一处诊断错误**：verify_gate/goal_checker 并非"孤儿代码"，agent_loop 早接好了，卡点是配置 flag 默认 off（本项目反复出现的模式）。**Layer 1A**：重写 `_CODE_MODE_PERSONA_TEMPLATE`（意图门→澄清→计划→执行→验证）+ dev 翻 `verify_gate_mode=shadow`+`emit_receipts`+`goal_mode`。**真机 CDP E2E**（test-research-helper tile，deepseek-v4-pro，全栈非注入）：TC-1 问模型→直答"deepseek-v4-pro"零工具(治#1)；TC-2 模糊派活→先澄清目标/范围/成功标准、零文件改动(治#2/#3)；TC-3 明确任务→todo拆步骤+写前看现状+**写后read_file读回验证**+报告校验结果(治#5)。决策：偏好记忆走 BGE-M3。Layer 1B(偏好记忆)+ shadow→strict 待做。详 [proposal](../plans/2026-06-02-superpowers-code-workflow/proposal.md) + [E2E 报告](../plans/2026-06-02-superpowers-code-workflow/evidence/E2E-report-layer1a.md) |
| 2026-06-01 | **工具层全功能手测（按 testcase 真桌宠 E2E）+ 修复 2 个真 bug**：A 类 5 例 CDP 真注入桌宠 WebView2（PPT/Excel/Word/web_fetch/能力门控，4 PASS + windows-mcp 真操作原生保存对话框）；B 类配置契约核对。**挖出并当日修复 2 bug**：① `doc_create` 生成空文档（element 格式契约 `{heading}` vs `{type,text}` 不匹配 → `doc_tools.py` 加归一）② `_load_tools` 漏读 `disabled_toolsets`/`dangerous_tools_allowlist`/`default_timeout_seconds` 等 WI-T5.1 字段（`config.py` 补读）→ 该 3 功能此前配了不生效。真桌宠闭环复测 disabled 生效（excel_create 0 调用，LLM 绕道）；pytest 59 passed。新增 testcase/ 手测体系。详 [tool-layer-test-report](../plans/manual-results-2026-06-01/tool-layer-test-report.md) |
| 2026-06-01 | **记忆系统严测（4 Phase 全收 / G1-G6 + 性能基线，33 新用例 master 直提）**：真机 GUI 终验推翻草率 PASS，挖出并**全修 F5**（facts.search/workspace.recall/find_by_entities 的 `LIKE '%整串%'` → 自然语言 query 永不命中）：① 分词 OR LIKE（`text_tokenize.py`）② memory_search 向量优先（真 BGE-M3）。G5 戳破 eval_gate hit@5 字面驱动（mock==real Δ=0）。G6 钉死 embedding 列真写入 + 写入并发不变量。性能基线：检索热路径 N=500 median 1-3.5ms（护栏非微基准）。CI 跑真 embedder。详 [memory-system-rigorous-test-spec](../plans/2026-06-01-memory-system-rigorous-test-spec.md) §8 |
| 2026-05-31 | memory Stage2 followup F1/F2 完成 + 真机 GUI 真测挖出并修复 F3（memory_search 误连坐 forget flag）/F4（code 工作记忆出厂默认开，保字节级契约）；单测全绿，未 merge |
| 2026-05-31 | companion-code v2（slash/goal/team/partition/cache）全套 + 真桌宠 WebView2 E2E PASS；fun-ux 12 交互 merge；dev-worktree.ps1 跑源码修复 |
| 2026-05-27 | OSS 开源准备（LICENSE / SPDX / 凭据脱敏 / CI 适配） |
| 2026-05-24 | 工具层优化 v3（VerifyGate 接电 + stubs 真实现 + ToolsConfig 扩展）；pet-animation UX |
| 2026-05-23 | 工具 last-mile 升级；memory-v2 Stage 2 |
| 2026-05-22 | beta-100 内测就绪；relay 登录集成；builtin skills |

---

## 5. 已知问题 / 测试纪律

- **dev 模式必须用 `scripts/dev-worktree.ps1`**（worktree）或 `dev-start.ps1`（主树）启动 —
  直接 `npm run tauri dev` 会用 stale 打包 exe（旧版本，缺新 endpoint）。详见脚本注释。
- **手工测试纪律**（CLAUDE.md HARD CONSTRAINT）：UI 改动必须 windows-mcp / CDP 真测，
  不能用单测 / 协议层替代。真桌宠 WebView2 测试用 CDP 9222（dev 默认开）注入真实输入。
- **DPI 坐标**：这台开发机 OS scale 150% + WebView dpr 2.13；SendInput 物理点击需正确
  换算（详 [16-sendinput-webview2-final-diagnosis](../plans/2026-05-25-companion-code-skill-upgrade/16-sendinput-webview2-final-diagnosis.md)）。
- **✅ ~~P1 — relay 登录与 backend cloud-llm key 账号脱节~~（2026-06-25 发现 → 2026-06-26 已修复）**：脱节根因是 relay 走旁路（`update_cloud_config` 改单例 `local_llm`，key 不落 registry），backend spawn 期固定的 `DESKPET_CLOUD_API_KEY` env / `deskpet-cloud-llm` slot 与登录账号脱节。**修复**：relay 登录后收编进 `LLMProviderRegistry`（`relay-cloud`/`source=relay`/`account_ref`），聊天经 registry chain **每请求按需读 key**（绕开固定 env，换账号无需重启）；多账号靠 `account_ref` 防串号、登出删 key。真机端到端验证 PASS（`p5s2_chain_resolved` + `chinzy.com 200 OK`）。详 [plans/2026-06-25-relay-local-apikey-provider/00-PLAN.md](../plans/2026-06-25-relay-local-apikey-provider/00-PLAN.md)（v7，A-E 全实现）+ 根因 [followup](../plans/2026-06-25-relay-cloud-key-sync-followup.md)。⚠️ device key 复用三态的**完整**真测待中转站 PR-6 开 `DEVICE_KEY_REUSE_ENABLED` flag（当前过渡态：每登录轮换一把 key，靠中转站 5-key 上限兜底）。
- 其它已知问题见 `README.md` §已知问题（Known Issues）+ `docs/beta/已知问题.md`。

### Follow-up backlog（待排期）

- 🟡 **P2 — 启动身份恢复 UI 体验优化** — 当前消息输入框在 Companion identity-ready 前固定禁用并显示
  “正在恢复身份…”，但现行身份已是零网络 I/O 的本地 profile；文案会被理解为远端重新登录，每次启动
  的可感知等待也缺少阶段与失败解释。后续保留 `IdentityReadyGate` fail-closed 边界，先量化 backend ready、
  control WebSocket、challenge/sign、profile bind 各阶段耗时，再实现延迟展示的低打扰快速路径、准确的
  “正在准备会话…”慢路径、超时错误与重试入口，并以真实 UI 冷启动/重连验收。详见
  [follow-up](../plans/2026-08-27-identity-restore-startup-ux-followup.md)。
- 🟡 **P2 — Simple Harness SDK 消费端首次登录 cold-start 验收** — 本次 SDK 提取仍硬验收
  clean wheel 安装、纯净 import、显式 Runtime 生命周期、schema v1 首建/reopen，以及已有有效开发
  登录态下的真实桌面 Workflow E2E；但“清除桌面应用全部数据 -> 首次登录 -> 不经暖重启直接使用
  SDK Tool/Workflow”的产品级路径需要交互式凭据输入，按用户 SR-9 决定不阻塞当前 release unit，
  后续独立验证 auth 恢复、Provider/Tool/Profile catalog 异步注册竞态。详见
  [follow-up](../plans/2026-08-13-simple-harness-sdk-cold-start-followup.md)。
- 🔴 **P1 — Harness 兼容执行入口与 expected-red 账本收口** — canonical
  `EffectBatchExecutor` 的连续 safe segment/unsafe barrier、reserved host field 拒绝和 durable
  `/stop` 已有直接测试通过，但旧 `test_current_failures.py` 仍绑定退役 AgentLoop/AST 假设并产生
  误导性 xfail；同时旧 `ToolRegistry.execute_tool()` 的 direct compatibility path 仍允许调用参数覆盖
  session context。后续需统一 fail-closed reserved-field 语义、审计/退役兼容入口、迁移陈旧 xfail，
  且不得抬高结构预算。详见 [follow-up](../plans/2026-08-13-harness-residual-defects-followup.md)。
- 🔴 **P1 — DeepResearch Top N 报告完整性与 partial 主卡可见性** — 真实 run `9ed2660a...` 的 v7 流程、子方向进度、文件落盘和文件操作均正常，但“前十个 AI 大模型”报告未明确列出 10 个模型；后端业务状态已诚实为 `partial`，主进度卡却仍只显示 engine `已完成 / 100%`。根因是 v7 没有持久化显式数量约束，child/final 只验证 citation 合法性而不验证 N 个命名项与逐项引用，且 `business_status` 未进入 `workflow.final` 主卡投影。v7 已 immutable，后续应以新的 v8 保留六节点简单图并增加确定性数量/逐项引用门、一次有界修复和独立业务终态徽标；本轮仅登记，未开发。详见 [follow-up](../plans/2026-07-20-deepresearch-topn-quality-followup.md)。
- 🟡 **P2 — `run_shell` 瞬时文件事件审计** — 当前输出契约可拦截直接文件工具越界，并发现 shell
  执行后的全部最终残留；但同一次 shell 内“创建后立即删除”的 workspace 外文件需要 OS 级事件
  观察才能取证。后续按 Run/effect 关联 create/write/rename/delete，丢事件或权限不足必须明确显示
  coverage partial/unknown；该能力只增强审计，不冒充安全沙箱。详见
  [follow-up](../plans/2026-08-13-shell-file-event-audit-followup.md)。
- 🟡 **simple_harness 性能优化建议** — 来源：[会话记录](chatgpt-conversation://6a53c976-1254-83ec-8095-75d5656ee907)。本轮仅登记为后续修复入口；待单独评审建议、核对当前性能基线、拆分实施计划并完成自动化与真机验收后，再更新对应模块架构状态。

---

## 6. 文档索引

- **架构 / 模块文档**: [`ARCHITECTURE/index.md`](./index.md)
- **手工测试用例索引**: [`testcase/index.md`](../testcase/index.md)
- **README**（用户 + 开发者入口）: [`README.md`](../README.md)
- **项目级开发笔记**: [`CLAUDE.md`](../CLAUDE.md)
- **迭代 plan 目录**: `plans/2026-*`（每个迭代一个文件夹，含 PRD/TDD/manual-test/report）
- **OSS 准备**: [`plans/2026-05-27-oss-prep-handoff.md`](../plans/2026-05-27-oss-prep-handoff.md)

---

## 7. 如何更新本文件（HARD 纪律）

> **铁律**：任何任务一旦"通过测试完成"（pytest/vitest/cargo/手工 E2E 全绿），
> **必须在同一次交付内**同步更新对应模块架构文档与本文件 —— "跑过测试但没更新 ARCHITECTURE" = 任务未完成。
> 详见 [`CLAUDE.md` §ARCHITECTURE 更新纪律](../CLAUDE.md)。

完成以下任一事件后更新：
1. 一个 WI / slice / 功能模块跑通验收 → 更新 §3（🟡 → ✅ 或新增行）
2. 里程碑级完成 → 追加一行到 §4 最近里程碑
3. 一个 worktree 合并到 master → 更新 §2 表格状态
4. 发现新的项目级已知问题 / 测试纪律 → 更新 §5

每次更新都改顶部"最后更新"日期。生产链路和边界写对应模块架构；本文件只保留聚合状态、里程碑、已知问题和证据链接。`STATUS/` 兼容文件禁止新增正文。

最后更新：2026-09-08（HM-TO-A6 typed recall 超时 Host 侧）。`wemm_embedder.py` 新增真正的 `embed_batch`：按「最长×条数 ≤ 1024 字符、条数 ≤ 32」分组、按长度升序装箱、结果回填输入位置；实测（mps，两路径均预热）6 条短 chunk 322ms vs 串行 1430ms（0.22），长 chunk 独占调用故不劣于串行（现场 6 条 0.93、短三条 0.82），真模型批量/逐条 cos ≥ 0.99988。`short_index_worker.py` 拆出 `maintenance_timeout=60s`（`operation_timeout` 仍 5s 只管扫描/注册），维护重建失败按 60→120→…→600s 指数退避、成功/换 manager 清零、跳过的 tick payload-free 审计；常量由一致性用例绑定生产装配值（实测 projection 600ms + 世代 embed 45690ms ≤ 60s，退避基数 ≥ 超时 → 写锁占空比 ≤ 50%）。`human_memory_v7.py` 前台 `RecallBudget` deadline 1000→2000ms（协议上限，S3 契约 `deadline_ms=1..2000` / hard deadline 2s）。短时域 chunk `public_text` 的长度上限**不在 Host**（Harness 派生 `public_text_hash` 绑定 + SDK 自己拼 chunk），记为需新增的 SDK 契约条款；`context_route.py` 的重试收口另有属主。控制：新增 `test_wemm_embed_batch.py` / `test_short_index_backoff.py` / `test_recall_budget_deadline.py`；`test_short_index_worker.py` 等短索引既有红（fixture 无生产 embedder）不变。[裁决](../plans/2026-09-08-hm-to-a6/DECISION-RECALL-TIMEOUT-HOST-SIDE.md)。


Source-native slot binding (2026-09-13 08:24 CST): launcher supports explicit 1..4 logical/model slots, default 1/1; values are part of the source identity and are written only before first backend startup. Resume checks exact integer config without rewriting seeded policy. Older source runs require their recorded pre-slot launcher. Main verification: g-source-slots-search-v2, 32 PASS/2.09s (runner2.70s; includes controlled search software test). Terra rework fixed incomplete CLI oracle and TOML bool equality; native multi-Mission load remains OPEN.

Real N1 v14 (SDK4e79dac/Hoste6a1dac7) reached verification_passed in223.94s: seven literal VERIFIED,three SUPPORTED,224843tokens settled/0reserved,12Providerhandoffs (10succeeded/2failed withusage),0rehandoffs. Actual UI opened REPORT and full table citation; cold same-state12->12. Raw case-summary SHA25684d7385071c30cab20e668f5b25eef41e4f53c1f1abcbf6212649b74bd81fc04 under Host .local-test-evidence/2026-09-13/p33-g/source-ui-n1-v14/. Manual quality FAIL: REPORT3.1 says both sources lack frozen build/install/verification records, contradicted sourceAline7 historical builds/startup failures. Both Worker/Critic had read whole sources. Runtime/citation/cold PASS does not close N1 report quality. Doc8 successor guidance and original400000/12 recheck in progress. Native owned groups94307/97182 exited0,noresidual; lifecycles477.554/80.27s include UI/analysis waiting. No packaging/P36/push.
最后更新：2026-09-21 CST。V1.4（去除 NanoJev）仍 PARTIAL。SDK 候选 WAIT 原子登记/唤醒、外部等待 idle return、终态请求包与并发快照通过 160 项定向及 48 项重叠回归；完整回归复跑中。H1-H 36 项旧 runner 存在规格映射错配，不得标整门 PASS；真实 WAIT Worker、强杀恢复、mutation、H1-I 独立验收及 H2–H8 生产/跨域门仍 OPEN。worktree 未合并、Host wheel/UI 未更新。[详细记录](../plans/taskSys2/升级planV1/v1.4/WAIT复验-2026-09-21.md)。
最后更新：2026-09-21 CST。Operation 补遗实施中，V1.4（去除 NanoJev）整体未完成。新增完成规格批准命令 Host handler → SDK facade → 原 CommitService/Store，Spec/receipt/event 同事务；来源、租户、重放、过期拒绝及迁移定向 27 PASS（0.54 秒，后继 Task contract hash 修正仍在复验）。Host 在独立临时源码副本对齐两包版本后，真实 service/handler 接线 11 PASS（18.28 秒）；仅为派生源码接线证据，不是当前候选字节、wheel 或 UI 验收。移除启动/重建路径上已延期的 PR-7 observer 依赖，保留历史文件。Scope/Plan Commit、准备与效果区分、T0/T3、D3 及完整 H1–H8 仍待；未合并/重装 Host。当前无新增 PlanAgent 架构待决。详见 Host V1.4 的 Operation补遗实施记录-2026-09-21.md。
