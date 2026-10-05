# Assurance 现状对照（2026-10-05，第 2 版）

**用途**：与《HTN 与原始计划一致性复核》《TaskGraph 现状对照》同口径，逐条对照 Assurance 原始计划，查现行代码哪些按计划在用、哪些做法不同、没做、只在测试或已删。

**第 2 版改了什么**：用户要求"再仔细对照一遍、确认按原始计划对照"。第 1 版只对了正文，漏了计划自带的接线清单、用例清单、变异清单和 F01～F15 关闭判据，还有若干误读。第 2 版加了三路独立复核（Opus 只读）：
- 覆盖审计：把正文与附录逐句拆成 314 条要求，查第 1 版漏核与误读；F01～F15 关闭判据逐条核（附三）。
- 接线资产逐行核：`implementation/` 下 integration-map 55 行、seams 26、事件消费 7、引用种类 29 与字段约定 9、BODY-WIRED 16 边、sdk-cases 48、occ 12、继承 66、变异 16+12（附四）。
- 反方复核：第 1 版全部非"在用"行逐条翻出处、56 条"在用"抽查重读原文（附五）。
- 最关键的"收尾不核资料变更"我亲自读代码确认（`SDK/orchestrator/assurance_recheck.py:43-58` 只重读证书钉住的对象；`SDK/orchestrator/root_review.py` 的 `source_change_open` 只挡根终审，收尾 `SDK/orchestrator/assurance_consumers.py:440-470` 不查）。

**基准确认**：Assurance 有三代文件——09-16《目标验收、证据有效性与执行恢复联合设计》（设计稿，已作为"验收附件"在 10-04 HTN 复核里对过）、09-22 实施计划 1.0、09-22 实施计划 1.1。1.1 首行写明"替代 1.0 的正文、活动 Schema/SQL 与执行安排"，仓内各副本哈希一致，没有更新版本。按用户 10-02 定的 TaskGraph 口径（原始计划 = 代码级实施计划，不是设计稿），本文对照 **`plans/Assurance/specs/1.1/ASSURANCE-EXEC-1.1.zh-CN.md`** 及其 `RESPONSE-TO-REVIEW.md`、`implementation/`、`contracts/`、`sql/`。

**口径**
- 已定结论不算缺口：实施记录 `sdk/simple-harness-sdk/plans/assurance-1.1/BODY-WORK-IN-PROGRESS.md`、根 `HANDOFF-2026-09-23.md`、`HTN补齐计划-2026-10-02.md` 偏离段、`HTN补齐-阶段A撇-*.md`、`TaskGraph-补全-方案.md` 第七节、台账 `需求与场景状态.md`；用户口径（判断交给 LLM、一件事一条路径、旧路径直接删、功能默认开、审阅员回复放宽两处、判不下来复审一次再问人、金额计价删、受管恢复删、暂不做十项）。判"已记录"必须找到出处原句。
- 代码版本：`main` `5bae3e92`（SDK opt.160，含今天"资料换版本"）。只读，没有跑测试。
- 路径简写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`。

## 一句话结论

**代码主体基本按原计划接上了，差距集中在验收侧。**
- 正文 314 条要求：主体条款（证据标签与曝光、检查政策与三值判定、六类审阅、根结论唯一写方、屏障与纪元、事件摄取、Host 三个读接口、默认开启）都在产品默认路径上。接线清单 55 行里 46 行在用或有记录，16 条生产调用边通了 13 条，另 1 条按决定删、1 条做法不同、1 条只差文档。
- **代码上有 3 处和计划不符且没记录**，其中 1 处有实际后果：根终审通过后、正式完成前换资料版本，收尾拦不住。
- **验收侧缺口比第 1 版说的大**：F01～F15 反例 15 条从没写；计划点名的变异 28 条没有按名执行，其中 6 类完全没有对应；继承 X 组 18 条没映射；真实模型四场景×3 局没跑；全量回归当时没跑完。这些"没做"大多没有任何记录说不做。
- F01～F15 关闭判据：成立 5（F03、F08、F09、F10、F11），部分成立 4（F02、F04、F05、F13），生产成立但旧尾巴没删 2（F01、F06），不成立 3（F12、F14、F15），不适用 1（F07，受管恢复已删）。

## 一、代码与计划不符（按影响排序）

| # | 条目（原计划出处） | 状态 | 影响与依据 |
|---|---|---|---|
| 1 | §7.2 / I7 / C.1.11："最终事务重读当前纪元…才 READY→FINALIZED"；撤回立即让旧使用证书失效 | 做法不同未记录 | **有实际后果**。收尾复查（opt.110）只重读证书钉住的对象，资料不在钉住对象里，纪元又被列为可忽略字段；今天 opt.160 加的"资料变更未处理时根终审等着"只挡根终审，不挡收尾。所以根终审通过后、收尾等未结算用量的那段时间里换资料或撤资料，任务照样完成，规划器不会被问。opt.110 记了"不比纪元"，没记这个后果 |
| 2 | §7.1 / 7.9 / 7.10 / §11 / F01 / F06：唯一终态写方；缺判别行不得当老任务 | 做法不同未记录（残留旧路） | `SDK/orchestrator/commit_service.py:3136-3160` `judge_mission` 非保证分支直接写完成；`SDK/orchestrator/assurance_final_writer.py:42-47` `is_assured` 出错一律当"不走保证通道"（吞掉 `CREATION_CONTRACT_UNRESOLVED`）。产品里走不到（循环入口 `_refuse_unsupported_contract` 先停老任务、工厂只建保证通道），但不是结构上保证；eb749104 自称旧分支全删，这处漏了。另 `:3087-3092` `network is None` 旧分支同类。原计划要的"枚举写点 + 测试证明无旁路"只枚举了失败/取消/停止（`T/full_target/test_terminal_unknown_release.py:59`），没含"完成" |
| 3 | §10 状态文件缺失/不匹配 → 隔离 | 做法不同未记录 | Host 每次启动按部署身份重装根，用库里已有回执把状态文件写回（`SDK/deployment/assembly.py:65-75`、`SDK/orchestrator/assurance_root_commits.py:138-173`）。状态文件缺失、不匹配、同路径换回旧库都会被自动修好而不进隔离。A″ 删的是离线备份与受管恢复，没写这一点 |
| 4 | §3.1 引用种类表 | 没做 4 / 无人构造 3 | tool_receipt、policy、authority、capability 读不了（`REF_KIND_UNSUPPORTED`）；observation、resolution、completion_spec 有解析器但生产没人构造。现无影响；以后拿工具回执做审阅对象会直接报错 |
| 5 | §5.3 / 5.12 全局安全问题必须挂到必须项 | 做法不同未记录 | 只在"或"公式里可能被绕过；"或"已交审阅员按原话判，公式基本都是"且"，影响很小 |
| 6 | C.1.18 原生下载、摘要读取走统一 use 检查 | 做法不同未记录 | 只过归属与根门；单用户桌面无权限表，影响小 |
| 7 | 事件消费表 3 行（审阅侧 2 行、"候选就绪"1 行） | 做法不同未记录 | "候选就绪"不走事件，由原入口直接触发审阅，效果等价 |
| 8 | §13.2 启动核导入身份、wheel 哈希、Host/SDK 指纹 | 做法不同未记录（小） | 编排服务启动只核版本号；wheel 哈希是常量、`host_fingerprint` 不是实测值。"字节不同不混用"在运行时组装那步（`backend/deskpet/sdk_adapters/composition.py:362`）仍成立 |
| 9 | 小的写法差异：命题键无 namespace、哈希截 32 位；读取器返回形状；commit_receipt 只核任务不核写者；检查规格按任务懒注册；完成范围直接读表；审阅绑定 `round_no` 恒为 1；钉子行两个回执引用用外键列代替；8 份内部 schema 用 Python 校验代替；integration-map S20/S22 计划写错文件位置；BW10；历史视图准则固定第 1 版要求 | 做法不同未记录（小） | 功能等价或只影响显示，补记录即可 |
| 10 | 受管恢复删后残留：隔离状态精确只读分支、`assurance_root_diagnostic`、门面 `install_assurance_root`；另 5 个无调用者函数（`is_bound_task_review_subject`、`normalize_local_check`、`AssuranceStore.has_live_pin`、`_exact_pin`、`_is_system_key`） | 只在测试 / 死代码 | 可删；只读分支生产一律拒读，偏保守 |

**计划外的风险观察（不算偏离计划）**：做法审阅的结论在新计划里被重新采用时，不核它是按哪一版要求审的（`SDK/orchestrator/method_plan_reviews.py:61-115`）。用户改要求后，规划器原样采用旧做法，闸门照放。原计划的使用证书只守接受、披露、交接和四类读者，没要求守做法采用，所以第 1 版把它当"偏离计划且可能判错"是误读。实际后果只是少了一次本该按新要求重做的做法审阅；叶子、组合、根三层审阅都按现行要求，不会让完成判错。操作提案审阅那一半不成立：物化事务里重读全部依据，交接前还核纪元。

## 二、验收与资产（按影响排序）

| # | 条目 | 状态 | 说明 |
|---|---|---|---|
| 1 | F01～F15 决定性反例 15 条（`implementation/findings-sdk-tests.json`，目标 `test_review_findings.py`） | 验收没做，无记录 | SDK 里一条都没有，也没映射到已有用例；`HANDOFF-2026-09-23.md:89` 列为待做，之后没有关闭记录 |
| 2 | 计划点名变异：原 16 条（M01～M16）+ F 定点 12 条（F-M01～F-M12） | 没按计划执行，无记录 | 实际跑的是自选 22 条（AM01～AM22），没有登记映射。M 组 10 条有近似对应，**6 类完全没有**：M06 交付回执只校非空、M08 查询失败当完整空集、M11 冲突路径放行、M13 接受时内部提前提交、M15 租约过期直接重发、M16 未知用量记 0 并释放预留。F-M 12 条从没写（其中 F-M05"旧 judge 直接放池"正对应第一节第 2 条；F-M09～11 是收尾/钉子的 SQL 守卫）。AM 组本身 A′ 后部分过期：2 条原文改写、AM20 目标用例改名后不测原语义 |
| 3 | 真实模型 M01～M04 各 3 局（不假完成、拒坏候选、撤回后不复用旧证书、外部效果恰好一次） | 验收没做 | 驱动脚本也没有。近似覆盖：10-05 资料换版本真机局接近"撤回后不复用"，09-26 真实发布局接近"外部效果恰好一次"，都不是计划规定的形式 |
| 4 | 继承 66 条 | 部分 | 存在 38、个别并入或改名 3、部分 2、并入 2、换名但反证没做 1（CA-K11）、按 A″ 删 2、**X 组 18 条从没映射**（同一操作跨尝试不重复、外部键防重）；说明里"由既有套件承接"的 9 个套件已删 3 个。CA-I10"外部评分器隔离"当初映射到迟到记账用例，语义不对，实际从没有对应用例 |
| 5 | sdk-cases 48 组 | 部分 | 存在 32、并入 9、删 3（A18、V13、E08）、换名但反证一半没做 1（V07）、承接不明 1（E02）、**缺失 2（E06、E07，分诊表写"并入"，实际没有承接用例）**。E06 管"要求变了旧回执不能复用"，现在要求修订有真实写方（`mission_amend`），风险不低；E07 管"绕过直写交付回执不能算效果完成"。"直接调最终提交、绕过有效性闸"这件事现在没有任何登记用例守着（A18 删、AM10 目标没了、F06/F-M05 没写） |
| 6 | occ-coverage 12 | 部分 | 并入 9、存在 1（OCC-02 52 个名字里 51 个在）、删 2（OCC-05、OCC-07）。分诊表与实际相反 3 处：A16 表写删、实际并入；A18、OCC-05 表写并入、实际删 |
| 7 | 状态化随机序列 200×50 | 做法不同未记录 | 实际 6 种子 × 14 步；实施记录只写了"不用 hypothesis" |
| 8 | 旧路径与全量 H1 回归 | 当时没跑完 | SDK 全量证据停在 89% 无汇总（多进程用例挂死），Host 26 个既有失败未处置；"默认 ON 后失败数与开启前相同"只说明开启没新增失败 |
| 9 | BODY_WIRED 16 边逐条关闭、integration-map 等资产回写、`check_plan.py` 对实际 DDL 运行 | 没做 | 代码其实接上了 13 条边（"做了没回写"）；BW14 作废未更新；`sql-column-producers.json` 没随 pending_work 加列更新（F12 不成立的原因） |
| 10 | 保证视图真机点击 | 部分 | 只点了主流程；审阅详情、历史/当前切换、断线重连、撤回没点 |
| 11 | 文档回写 | 部分 | `ARCHITECTURE/ASSURANCE.md` 停在 10-02、`ARCHITECTURE/index.md` 停在 09-26，都没记 10-03 删旧通道、关闭开关、受管恢复，`index.md` 仍把已删的 `assurance_profile` 开关写成现状 |

## 三、第 1 版更正

| 第 1 版写法 | 更正 |
|---|---|
| "唯一可能判错"：做法/操作提案审阅采用时不核使用证书 | 不是原计划要求，改为"计划外风险观察"；操作提案那一半不成立；后果是规划质量，不会让完成判错。真正有后果的是第一节第 1 条（收尾不核资料变更），第 1 版判成了"在用" |
| `judge_mission` 尾巴"只在缺创建合同时走到" | 触发条件还包括老通道、缺绑定和 `is_assured` 读错；产品走不到，但属"出错就放行" |
| 没有"删了却没记录"的 | 有：F 反例与 F-M 变异从没写也没记"不做"；E06、E07 删了，记录写的是"并入" |
| 变异"验收已做" | 计划点名的 28 条没按名执行，自选 22 条无映射 |
| 默认开启后回归"验收已做" | 全量回归当时没跑完 |
| "四件事都没在真实模型下实测" | 过头：有两件有近似覆盖 |
| 8MiB 读上限"未记录" | 已记录（`ARCHITECTURE/ASSURANCE.md:35`、`ARCHITECTURE/AGENT_ORCHESTRATION.md:243`，opt.18） |
| 7.12/7.14 收尾复查"在用"、§12.1 严格 JSON"在用" | 做法不同已记录（opt.110；审阅员回复放宽两处） |
| §16 source-map"做法不同已记录" | 已删（有决定）：替代脚本 3a8c4697 删，阶段 B 收尾裁决 7-3 |
| C.3.5"用户定不做"、0.4"已删" | 都是"在用"：删的只是金额计价；老任务直接被拒、没有静默换协议 |
| §10"24h 读授权 在用" | 那条随恢复授权一起删了（有决定），第 1 版挪用了另一套机制的有效期 |
| 7.9"没看到枚举测试" | 有，但只枚举了失败/取消/停止，没含"完成" |
| 第 1 版自报 243 条 | 按逐句拆是 314 条；第 1 版把 §3.1 种类表 17 行、§8.1 屏障表 9 行各并成一行，§14 验收各步和计划包测试资产没有单列，共漏核 49 条（其中 41 条核下来在用） |

## 四、建议

- **代码（小改动）**：① 收尾评估也查"资料变更未处理"，有就 NOT_READY，复用 opt.160 的 `source_change_open`；② 删 `judge_mission` 非保证尾巴和 `network is None` 旧分支，`is_assured` 读错改为报错；③ 删受管恢复残留与 5 个死函数。各配一条用例和一条改坏。
- **验收**：补 F01～F15 反例（可并入现有用例，但要登记映射）；对 M06/M08/M11/M13/M15/M16 与 F-M05、F-M09～11 补变异；补 E06、E07 与"绕过有效性闸直调最终提交"用例；X 组 18 条做映射或登记不做。真实模型四场景×3 局和保证视图点击等用户定时间。
- **记录**：把第一节未记录的偏离、状态化规模、变异替换、分诊表 3 处相反写进记录；更新 `ARCHITECTURE/ASSURANCE.md` 与 `index.md`。

---

# 附：明细

- 附一、附二：第 1 版逐条对照（前段 §0～§9、后段 §10～§16）。**其中被附五改判的行以附五为准。**
- 附三：覆盖审计（314 条要求、漏核 49 条、F01～F15 关闭判据、误读）。
- 附四：接线资产逐行核。
- 附五：反方复核（改判表、56 条在用抽查）。

## 附一 · §0 使用规则、范围与证据身份

| 条目 | 现状 | 代码依据（file:line 与函数名） | 说明 |
|---|---|---|---|
| 0.1 复用现有 scoped_content / scoped_composition / completion_* / root_review / composition_review / operation_proposal_review，不另建一条平行主链 | 在用 | `SDK/orchestrator/assurance_content_review.py:35` `ensure_task_content_review`；`composition_review.py:167` `resolve_one`；`operation_runtime.py:128,294` | 六类审阅都挂在原入口上，没有第二条主链 |
| 0.2 迁移号取本地下一个空号并冻结 | 在用 | `SDK/storage/assurance_schema.sql`（迁移 26）；后面接着迁移 38/39/41 等 | 实施记录写明 26 号在发布前原地改过（WIP），之后改动都另开新号 |
| 0.3 交付后新 Mission 默认开启 | 做法不同已记录 | `SDK/orchestrator/assurance_factory.py:50` `selects`、`:152` `record_mission_creation`（没装 factory 就拒绝建任务）；Host 已经没有 `assurance_profile` 开关 | 比"默认开"更进一步：没有关闭选项，只有这一条路。依据：2026-10-03 删旧路（补齐计划阶段 A′） |
| 0.4 已在跑的 Mission 不悄悄换协议（用持久 lane 区分 legacy） | 已删（有决定） | `assurance_factory.py:172` `validate_creation_replay` 要求必须是 `ASSURANCE_1_1` | 开发期不兼容旧数据，旧 lane 直接删除，旧库不能启动 |

## 附一 · §1 核心不变量与唯一 owner

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| I1 不改旧 V1 正式合同，新增内容放在旁挂绑定里 | 做法不同已记录 | 旁挂绑定仍在用：`SDK/orchestrator/assurance_review_import.py:412`（语义 PASS 在 V1 记成 UNKNOWN，加标记 `ASSURANCE_SEMANTIC_GRADE_IN_BOUND_MANIFEST`） | 后来按"开发期不兼容"改过 V1 合同（如 H-3 删 `Criterion.phase`、审阅回复升到 v4），属于用户定的口径 |
| I2 六类审阅不加新种类；沿用原派发和预算；不建第二个调度器 | 在用 | `SDK/contracts/resolution.py:554` `ReviewPurpose`（6 个）；`assurance_review_transport.py:380` 走原 `create_service_intent`；`assurance_tick.py:200` 由原主循环调用（`event_handler.py:3694`） | — |
| I3 效果只从原 OCC 读取器读 | 在用 | `assurance_consumers.py:422` `_evaluate_locked` 调用 `completion_status.read_current_effect`；`operation_outcomes.py:549` | — |
| I4 所有正式审阅、接受、终态写入、来源改动都要经过保证检查 | 在用（有两处缺口，见 7.15、C1.18b） | `htn_store.py:936` 正式记录只认 `PreparedOfficialReview`；`resolution_commits.py:1051-1062`；`assurance_final_writer.py:125` | 主链都接上了；缺口是做法审阅和操作提案审阅消费时不检查当前使用证书 |
| I5 缺数据要报出具体名字，不能补 true 或空结果；合法的空读也要有完整读取证明 | 在用 | `SDK/storage/assurance_reads.py:47` `read_epochs_locked`（没有行就报错，不当成 0）；`:294` `read_complete_evidence_snapshot` | — |
| I6 原始数据、费用、历史结论不丢；UNKNOWN 不会变成 PASS | 在用 | `assurance/checks.py:41` `tri_all`；`assurance_review_collect.py:28` 先存原始回复；`assurance_review_consumer.py:378` 迟到的回复记 `LateTurn` | "判不下来 + 用户裁决通过"可以用，这是用户的决定，不是程序把 UNKNOWN 升级 |
| I7 本机提交的撤回立即让旧使用证书失效 | 在用 | 触发器屏障 `storage/assurance_barrier_v26.sql`；`assurance_validity.py:686` `require_current_locked` → `assurance_reads.py:71` `require_epochs_locked` | — |
| I8 恢复时要用新的隔离根并重新授权 | 已删（有决定） | `assurance/root_gate.py:137-144`（`restore_manifest_hash` 恒为 null） | 补齐计划阶段 A″：离线备份和受管恢复整套删除 |

## 附一 · §2 六类审阅的复用、修改与新增分区

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 2.1 TASK_CONTENT：先读投影再构造审阅包，不要求已有结论；正式消费走 accept_review + OCC | 在用 | `assurance_content_review.py:35`；`resolution_commits.py:1708` `_require_assured_use` | — |
| 2.2 METHOD_PLAN：草案建原审阅包，由原方法准入写入口消费 | 做法不同已记录 | 挂钩点 `event_handler.py:6803`；消费改成计划提交时的审阅闸门 `plan_commits.py:381` `_check_method_reviews` → `method_plan_reviews.py:118` | 片 A（PLAN-STATUS）：规划器自己提做法，采用前必须审过 |
| 2.3 COMPOSITION：先组合再接受，交给原 commit_goal_resolution | 在用 | `composition_review.py:221`（发起）、`:265`（证书）；`resolution_commits.py:1762` `_require_assured_compound_use` | opt.109 补上了"中间层审阅的结论有人读" |
| 2.4 ACTION_PROPOSAL：正式审阅决定能否进 T1 | 在用 | `operation_runtime.py:128`；`operation_materialization.py:373` 物化时要求正式记录 | 消费时不检查证书，见 7.15 |
| 2.5 OPERATION_OUTCOME：先读完整效果链再审，由 accept_operation_outcome 消费 | 在用 | `operation_runtime.py:294`；`operation_outcomes.py:549,581` `prepare_outcome_use` | — |
| 2.6 MISSION_FINAL：`cut` 交给统一 builder 构包，不能出现"cut 发一次、ensure 再发一次" | 在用 | `root_review.py:831` `cut` 只切包、不派发；`event_handler.py:9813` `_ask_root_reviewer` → `ensure_mission_final` | 旧根审阅员已在阶段 A′ 删除 |
| 2.7 record_review 必须有已验证的正式来源，裸结论拒绝；legacy 保持原样 | 在用 | 旧的 `record_review` 已经没有；唯一的正式记录写入口是 `htn_store.py:936` `insert_review_record(official=True)`，要求 `PreparedOfficialReview`（`assurance_review_import.py:542`） | legacy 部分属于"已删（有决定）" |
| 2.8 S01–S26 职责保持；source-map 用新 hash 初始化 | （流程项，不计数） | — | 没有代码含义 |

## 附一 · §3 内部 Ref 与精确 resolver

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 3.1 内部引用形状 `{kind, pin:{id,revision,content_hash}}`，和公开 TypedRef 分开 | 在用 | `SDK/assurance/refs.py:62` `AssuranceRef` | — |
| 3.2 每个适配器返回 `ResolvedRef(body,pin,tenant,mission,issuer,state_witness)` 或 `SourceUnavailable` | 做法不同未记录 | `assurance_reads.py:148` `read_exact_metadata` 返回 `ExactMetadata(ref, body_json, lifecycle_json)`，失败直接抛异常 | 形状不同，但归属、修订号、hash 都查了，效果等价；只是没有写进记录 |
| 3.3 禁止用反射式 getattr 解析，字段只能是固定子集 | 在用 | `assurance_reads.py:117` 固定表 `_EXACT`、`assurance/event_kinds.py:4` | — |
| 3.4 未知种类、缺失、同 ID 内容不同、跨 Mission、发件人不对，各给不同的拒绝码 | 在用 | `assurance_reads.py:164-200,209-235`（REF_KIND_UNSUPPORTED / SOURCE_UNAVAILABLE / REF_BODY_CONFLICT / REF_SCOPE_MISMATCH / REF_ISSUER_MISMATCH） | — |
| 3.5 种类表：17 类存表的 + 6 类事件桥 + artifact/source | 在用 | `_EXACT`、`EVENT_REF_KINDS`、`storage/assurance_blobs.py` `read_blob_metadata` | task 用语义绑定修订号（不用心跳行版本），result 只取 envelope 不取可变外壳 |
| 3.6 `tool_receipt / policy / authority / capability` 这几类引用的读取 | 做法不同未记录 | `refs.py` 的 `REF_KINDS` 里有这几类，但 `read_exact_metadata` 遇到会报 `REF_KIND_UNSUPPORTED`；`assurance/reviews.py:246` 允许 OPERATION_OUTCOME 的审阅对象是 `tool_receipt` | policy/authority 改由读集里的 POLICY/ACCESS 两个通道承担；生产里没有地方构造这几类引用，所以现在没有影响 |
| 3.7 事件类引用的 hash 覆盖整条原 Event；同来源同内容重送幂等，内容不同就拒绝 | 在用 | `assurance_reads.py:209` `_event_metadata`（用 `Event.to_json`）；`assurance_barrier_v26.sql` 开头的 no_update / no_delete / no_replace 触发器 | — |
| 3.8 创建顺序：pin → 同一事务里写审阅包 + 预留 + intent → ReservationLinked → invocation → ensure 回执 | 在用 | `assurance_review_transport.py:202-466` `ensure_review_invocation` | — |
| 3.9 收到回复：先存原始回复和费用 → TurnImported → 正式记录 → 旁挂绑定 | 在用 | `assurance_review_collect.py:28,114`；`assurance_review_import.py:542,648` | — |
| 3.10 只有新建 invocation 才需要新的预留，不复用已结算的预留 | 在用 | `assurance_review_transport.py:393-401`（要求 RESERVED）；`:833-835` 第二次调用前必须先结算 | — |
| 3.11 如果原方法自己开事务，就提取 `_create_service_intent_locked` | 做法不同已记录 | 没有提取，原 `create_service_intent` 直接并入外层 savepoint（`transport.py:275` `atomic`） | 实施记录"审查 transport…续写"段有说明 |

## 附一 · §4 证据标签、曝光与冷恢复

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 4.1 标签 = `ev-` + 完整 sha256（含 kind、review_key、ref） | 在用 | `SDK/assurance/evidence.py:15` `evidence_label` | — |
| 4.2 build_catalogue 遇到同标签不同引用就拒绝；按字典序；目录 hash 存在绑定里 | 在用 | `evidence.py:34` `build_catalogue`；绑定里的 `catalogue_hash` | — |
| 4.3 初始曝光：调用的真实输入 hash 和冻结目录对齐，生成第 0 批 | 在用 | `assurance_review_collect.py:276` `_import_initial_exposure` | — |
| 4.4 追加证据走两个只读工具，经原 ToolGateway；只有之后的模型请求里真的带上了才算看过 | 在用 | `runtime/tool_gateway.py:154`；`verification/reviewer_evidence_tools.py:259` `invoke`、`:489` `import_reviewer_disclosure` | — |
| 4.5 原 runtime 没有消息清单时，在请求冻结处补导入，不改旧的输入字节 | 在用 | `runtime/assurance_turn_sources.py:94` `_exposure`、`:278` `_selection_binds_request` | — |
| 4.6 每一批固定 previous_hash / review_key / 审阅员 / turn / input hash / 消息 ID；同号同内容重放不新增，内容不同就冲突 | 在用 | `reviewer_evidence_tools.py:419` `record_disclosure_batch`；导入时逐批核对 `assurance_review_import.py:271-298` | — |
| 4.7 格式修复的那次调用，新输入里要带上目录和材料 | 在用 | `assurance_review_transport.py:900-920`（沿用原 config.message，只加一段格式说明） | — |
| 4.8 正式导入只认"给出这个结论的那一轮"实际看过的证据 | 在用 | `assurance_review_import.py:261` `disclosed_to_turn`、`:381` `resolve_evidence_ids` | — |
| 4.9 根审阅员要显式配上这两个工具；旧 profile 保持没有工具 | 在用 | `assurance_review_runtime.py:247,449` `tool_names=ASSURANCE_EVIDENCE_TOOLS` | 旧 profile 已删 |

## 附一 · §5 批准的检查策略与三值判定

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 5.1 `approve_assurance_check_policy` 是唯一的批准写入口 | 在用 | `commit_service.py:632` → `assurance_check_policy.py:40` `approve_check_policy`；门面 `api/facade.py:131` | — |
| 5.2 在原 Requirements 细化/批准的同一事务里写入 | 做法不同已记录 | 实际在范围冻结后由部署按范围逐个自动投影：`deployment/duties.py:160` `project_check_policies`（`approval_source=HOST_LOSSLESS_AUTO`），审阅前还会经 `check_policy_projector` 先补一次（`deployment/assembly.py:96`） | 实施记录第十段（五）和 run-7：原来"只有人批准"这条路在 Host 上没人走 |
| 5.3 无损适配：有 required_check_ids 就是 CHECKED（一个 AND 组）；没有的只有明确是语义评估才算 SEMANTIC；其它情况报 UNRESOLVED | 在用 | `assurance_check_policy.py:412` `_lossless_mapping`、`:242-254` | — |
| 5.4 只有批准过的替代组才能 OR | 在用 | `assurance_check_policy.py:255-258`（每一组都必须包含全部必需检查）；`assurance/checks.py` `CriterionPolicy` | — |
| 5.5 本地检查的真实记录器（format/rule）：事务外执行，结果导入原 Commit | 在用 | `verification/assurance_local.py:135` `LocalVerificationRecorder.run`；生产调用 `event_handler.py:8066,8269` | — |
| 5.6 函数抛错记 ERROR；一个 CheckSpec 一个断言键；成功但缺这个断言算 UNKNOWN；同一个键重复就拒绝 | 在用 | `assurance/local_checks.py:122,191-204` | — |
| 5.7 citation 检查只证明指定的来源段落 | 已删（有决定） | `check_specs.py:108` 只剩 `format_check`、`rule_check` | opt.134：文档引用交给审阅员判 |
| 5.8 code_test 用执行器的真实回执，exit 0 不能单独算通过 | 做法不同已记录 | `assurance/executor_checks.py:42` `executor_run_facts`；`:127` "没有目标 + 一个测试都没收集到"算通过 | 实施记录第十段（四）缺陷 1；和用户口径"文档任务没东西可证明的不算必过检查"一致 |
| 5.9 三值真值表（模型等级 × 检查结论） | 在用 | `assurance/checks.py:428` `decide_review`（`tri_all(verdict, gate)`） | 逐格核对过，和原表一致 |
| 5.10 SEMANTIC 的检查结论是"不适用"，不伪造执行 PASS | 在用 | `checks.py` `evaluate_check_gate`（返回 `CheckGate(None…)`） | — |
| 5.11 准则上有 BLOCKER 就判 FAIL；必须项单独做 ALL | 在用 | `checks.py` `decide_review`（BLOCKER→FAIL；`mandatory` 再做一次 `tri_all`） | 必须项 = HARD_CONSTRAINT（`assurance_content_review.py:224`） |
| 5.12 全局安全问题必须挂到必须项上，挂不上就拒收整条回复 | 做法不同未记录 | `checks.py` `ReviewReply.from_json`：finding 只要求挂在某条准则上（`FINDING_SCOPE`），没有"全局安全"这一类 | 在 OR 分支里，挂在非必须准则上的 BLOCKER 可能被另一条分支绕过；不过 H-3 之后"或"交给审阅员判，公式基本都是 AND，实际影响很小 |
| 5.13 ALL/ANY 三值；成功见证按冻结顺序选，不按模型打分 | 在用 | `checks.py` `Formula.evaluate` | — |
| 5.14 模型结论不是 ACCEPT 时，公式通过也不自动批准 | 在用 | `checks.py` `decide_review`：`acceptable = verdict=="ACCEPT" and PASS` | — |

## 附一 · §6 六类审阅的切换、预算与调用身份

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 6.1 一个 review_key → 一个审阅包 → 最多两次调用 | 在用 | `assurance_review_transport.py:228` `ordinal = 1 or 2` | — |
| 6.2 subject_id / creation_key / input_id 的格式 | 在用 | `transport.py:229-230,385-386` | — |
| 6.3 kind 一律用 'plan' | 做法不同已记录 | `transport.py:381`：TASK_CONTENT 用 `critic`，其余用 `plan` | 实施记录"原 Critic runner…续写"段：TASK_CONTENT 接在原 `_run_critic` 上 |
| 6.4 config 里有 role、assurance_protocol、package、review_key、ordinal | 在用 | `transport.py:246-257` | — |
| 6.5 按 `account_for_purpose` 决定记到谁的账上 | 做法不同已记录 | `transport.py:177` `review_budget_subject`：组合审阅和根终审记在任务总账上，操作类审阅记在准备这个操作的叶子任务上 | PLAN-STATUS opt.45、片 B（"中间目标的组合审阅记在任务总账上"） |
| 6.6 预留、intent、审阅包、绑定、事件、回执都在同一个 Store 事务里 | 在用 | `transport.py:275` `with atomic(commit.store)` | — |
| 6.7 六类切换表：触发 → builder → 调用 → 收集 → 下一步 | 在用 | `assurance_review_runtime.py:289-368`（`ensure_*`）；生产挂钩 `event_handler.py:6803,9826`、`composition_review.py:221`、`operation_runtime.py:128,294`、`assurance_review_runtime.py:464` | — |
| 6.8 新 profile 不接受裸结论；所有接受/记录路径都要查记录绑定 + TurnImported | 在用 | `htn_store.py:957-966`；`assurance_review_import.py:574` `require_locked` | — |
| 6.9 legacy 按持久 lane 分派，不改旧 prompt/hash | 已删（有决定） | — | 2026-10-03 只剩一条路 |
| 6.10 同一通知/命令重送：返回原回执，不再预留 | 在用 | `transport.py:284-298` | — |
| 6.11 能解析的 REWORK/INCONCLUSIVE 不自动发第二次 | 做法不同已记录 | REWORK 照旧；INCONCLUSIVE 会在新会话里复审一次（`assurance_review_consumer.py:407-431`，`SECOND_OPINION`） | 用户 09-30 定"判不下来 → 复审一次 → 问人"（PLAN-STATUS opt.104） |
| 6.12 解析不了：同一个包，第二次调用 + 新的真实预留；格式修复最多 1 次 | 在用 | `transport.py:778` `_require_format_repair`、`:861` `ensure_format_repair_invocation`；`format_retries != 1` 就拒绝 | 另外"能解码但没法导入"（引用了没看过的证据等）也走这条路（2026-09-26，阶段 C 记录） |
| 6.13 原 Provider 结果 UNKNOWN 时，不能靠第二次调用重复请求 | 做法不同已记录 | `assurance_review_collect.py:218` `abandon_assurance_review`：调用超时没回来就记 `TURN_FAILED/REVIEW_CALL_ABANDONED`，再走第二次调用（新会话） | 阶段 B 裁决第 6 类；旧预留按上限计费（"多算不少算"）。另有 `assurance_review_wait.py:52` 记对账要求 |
| 6.14 获准的独立二审要开新一轮、新包 | 做法不同已记录 | 二审改成同一个包里的第二次调用（`SECOND_OPINION`） | 同 6.11 |
| 6.15 迟到的另一次调用：保存原始回复和费用，不占正式记录 | 在用 | `assurance_review_consumer.py:378` `AssuranceReviewLateTurn` | — |
| 6.16 绑定表不放派发字段，另建 invocations 表；收集按真实调用关联 | 在用 | `transport.py:445-465`（`assurance_review_invocations`） | — |

## 附一 · §7 接受与全部终态写入

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 7.1 `accept_result`：原结果和费用照常保存，内容接受经原路径 + 保证检查，不另建一份审阅 | 在用 | `commit_service.py:2773` `accept_result`、`:2865` `_prepare_assured_acceptance`、`:2891` `_lock_assured_acceptance` | — |
| 7.2 `accept_review`：原 OCC 检查 + 正式记录 + 检查结论 + 使用证书在同一事务 | 在用 | `resolution_commits.py:589`、`:669-673`、`:1708` `_require_assured_use`（`commit_use_locked` 在 `:1746`） | — |
| 7.3 `commit_goal_resolution` 要求完整的保证材料，直接调用也绕不过去 | 在用 | `resolution_commits.py:1051-1062`（根 → `_require_assured_root_use`；中间层 → `_require_assured_compound_use`；其它情况拒绝） | — |
| 7.4 completion_status / inputs / support、accepted_outputs 按用途读；MIXED 范围里没满足的效果不能被内容接受顶替 | 在用 | `completion_status.py:135,633`；`completion_support.py:29`；`review_adjudication.accepted_or_adjudicated`（opt.106） | — |
| 7.5 RootReviewCoordinator.record_review 等写入口必须由统一的收集器调用 | 在用 | 唯一入口 `htn_store.py:936` | 旧 `record_review` 已删 |
| 7.6 DeliveryReceipt 写入沿用原 OCC T3 精确绑定 | 在用 | `htn_store.py:1913` `record_delivery_receipt`，唯一调用者 `resolution_commits.py:796`（操作结果验收路径） | `ResolutionCommits.record_delivery_receipt` 这个直写入口在阶段 G 删了（有决定） |
| 7.7 `judge_mission` 在新 profile 下只请求收尾，不直接 COMPLETED | 在用 | `commit_service.py:3137-3144` → `assurance_final_writer.request_assured_closeout` | 注意：`:3145-3160` 还留着非保证通道的直接 COMPLETED 尾巴，只有 `is_assured` 为假（缺创建合同）时才会走到，属于没删干净的旧路径（死代码） |
| 7.8 取消、失败、超时：保留 UNKNOWN 和预留，不靠"失败"丢掉没结清的责任 | 在用 | `commit_service.py:2225` `_settle_subject` 必须经过 `AssuranceSettlement.require_settled_locked`；各终态写入口都调 `_assured_terminal_notice`（`:1468,1488,1523,3174,3464`） | — |
| 7.9 `Store.update_task/update_mission` 只能由合法的 Commit 调用；要枚举并用测试证明没有旁路 | 在用 | grep：`update_mission` 只在 `commit_service.py`、`assurance_final_writer.py`、`plan_commits.py` 被调用；写 COMPLETED 的只有 `assurance_final_writer.py:178` 和 7.7 说的旧尾巴 | 代码层面没发现旁路；但"枚举 + 专门测试"没看到 |
| 7.10 唯一终态写入口 `_finalize_assured_mission_locked` | 在用 | `assurance_final_writer.py:125` `finalize_assured_mission`（只接受 READY 行，重读行/决议/Mission 版本）；`commit_service.py:3290` 是 Python 内部的薄包装，不对 API 或模型工具开放 | 名字不同，等价 |
| 7.11 根目标满足后 Mission 保持 ACTIVE，收尾 DRAINING/BLOCKED_UNKNOWN；调度当作"收尾中"，不判失败 | 在用 | `event_handler.py:9114`；`progress.py:64` `CLOSEOUT_CONVERGING` | — |
| 7.12 默认成功策略：效果 UNKNOWN → BLOCKED_UNKNOWN；还在写/费用没结 → DRAINING；依据失效 → NOT_READY | 在用 | `assurance_consumers.py:422` `_evaluate_locked`、`:514` `_drain_decision`；依据失效由 `stale_certificates` 判（opt.110） | — |
| 7.13 只有政策明确允许时才用 KEEP_HOLD_PENDING；不能自己加"免费/零费用"的做法 | 做法不同已记录 | `assurance_consumers.py:576` `_upper_bound_plan`、`:698` `settle_at_upper_bound` | 改成"费用未知的按上限结清再收尾"（用户 2026-09-26：多算不少算）；没有 KEEP_HOLD_PENDING |
| 7.14 收尾第一次只能插入 NOT_READY/v1 → 再转 READY → FINALIZED；不回退，不删了重建 | 在用 | `assurance_consumers.py:611-710` `_write_locked`；`:723-731` 提交时重新评估 | 第一次插入后，同一事务里可能马上转 READY 并完成，仍然先插 v1 |
| 7.15 做法审阅（METHOD_PLAN）和操作提案审阅（ACTION_PROPOSAL）的正式结论被消费时，要检查当前使用证书 | 做法不同未记录 | `plan_commits.py:381` / `method_plan_reviews.py:118`、`operation_materialization.py:373` 只认"正式记录（或用户裁决）"，不调 `prepare_*_use` / `commit_use_locked`；`assurance_validity.py:248-325` 只给内容、根、中间层、操作结果四种用途准备证书 | 实施记录只写过"先做 TASK_CONTENT"，后来补了另外三种，这两种一直没补，也没说明为什么 |
| 7.16 最终事务里同时写 MissionCompleted 和通知请求 | 在用 | `assurance_final_writer.py:234` `request_assured_notification`；失败/取消走 `commit_service.py:3276` | 通知待办由下一轮读事件生成，事件本身在同一事务里 |
| 7.17 只推送 `{mission_id,event_id,state_version}`；Host 按 event_id 去重，重连时补读 | 在用 | `assurance_consumers.py:768-812`（NOTIFY）；`backend/deskpet/orchestration/notices.py` `record`（按 event_id 去重）/`backfill`（启动时读 `AssuranceStatusNotified`） | 补读方式是启动回填，不是按 seq 续读；效果一样 |
| 7.18 发送前仍检查当前读权限和恢复门 | 在用 | `assurance_consumers.py:787,805` `self._root()` | 单个 principal 的部署没有单独的 ACL，只查根门 |

## 附一 · §8 证据屏障、读集、时效与活性

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 8.1 保留 validity_epochs；Mission 汇总分区由 factory 创建；没有行不当 0 | 在用 | `assurance_store.py:76` `bind_profile_locked`；`assurance_reads.py:47` | — |
| 8.2 `assurance_environment_state` 只存全局纪元和时钟 | 在用 | `assurance_reads.py:56-58` | — |
| 8.3 每个来源改动入口在同一事务里调 `_mark_assurance_change_locked` 推进纪元 | 做法不同已记录 | 改用 SQL 触发器：`storage/assurance_barrier_v26.sql`（212 个触发器，覆盖 sources/observations/requirements/OCC/计划/结果/动作/审批/策略等表）；迁移 41 只给没结束的任务写变更事件 | 实施记录"迁移 26 增加同事务来源屏障"；补齐计划阶段 G |
| 8.4 ACL/政策改动推进全局纪元；外部权威要在真实导入时推进 | 做法不同已记录 | `assurance_assembly.py:57` `FixedPrincipalAuthority`：单个 principal、单个租户，不查外部 ACL | 实施记录第六段 |
| 8.5 所有直接写 SQL 的地方都要扫描并登记 owner | 在用 | `storage/assurance_source_inventory.py`；G0 覆盖清单和守护测试（补齐计划阶段 A/G） | — |
| 8.6 CompleteRead：SQL 全量结果、schema、范围、条数、摘要、两级纪元；缺表或没读完不能算完整 | 在用 | `assurance_reads.py:237-373` | — |
| 8.7 命题键 = canonical({predicate:{id,version,hash}, typed_args, namespace, scope}) | 做法不同未记录 | `knowledge/predicates.py:294` `proposition_key_of` = `id@version#` + 32 位 hash({predicate, arguments})，没有 namespace/scope | 沿用的是 HTN 原有公式；1 和 true 仍然能区分开。影响小 |
| 8.8 读集 (channel,key) 唯一、排序、拒绝未知通道、≤20000 | 在用 | `assurance/evidence.py` `ReadItem`、`canonical_read_set` | — |
| 8.9 OBJECT / QUERY_SET / ACCESS / POLICY 四种 key 的定义 | 做法不同已记录 | ACCESS 的 key 多加了来源 ref（`assurance_assembly.py:115-131`，run-8/9）；QUERY_SET 指纹不再含时钟（`assurance_reads.py:249-263`，opt.110） | 实施记录第十段 run-8/9；PLAN-STATUS opt.110 |
| 8.10 有界计算 1 万字面量 / 2 万规则 / 100 万次访问 / 250ms，超了报 INCOMPLETE | 在用 | `knowledge/bounded_closure.py:37-47` | — |
| 8.10b 用原来的正负支持最小不动点和 clean closure，输入包括反证和观察 | 做法不同已记录 | `knowledge/assurance_sources.py:310` `evaluate_acceptance_support` 现在只用"审阅锚 + 检查锚 + 固定接受规则"；观察和存储规则输入已删 | 阶段 D 1.5/D3（偏差单 3，已告知用户）；收尾复查也不再比查询集 |
| 8.11 一致读快照 → 事务外计算 → 短写事务比较纪元；不在写锁里扫描候选 | 在用 | `assurance_validity.py:326` `_prepare_use`、`:686` `require_current_locked`、`:768` `commit_use_locked` | — |
| 8.12 证书 ≤256KiB；读集 ≤20000；带根实例号 | 在用 | `assurance/codec.py:15` `MAX_BYTES`；`evidence.py` `READSET_LIMIT`；`UseIdentity.root_incarnation_id` | — |
| 8.13 有效期是半开区间；证书到期取所有依据里最早的那个 | 在用 | `knowledge/assurance_sources.py` `earliest_expiry_ms`；`assurance_validity` 取最小 deadline | — |
| 8.14 MAINTAIN 用途要有持续监测，没有就挡住 MAINTAIN | 没做 | 生产里没有 MAINTAIN 用途的证书 | 实施记录第六、七段写了"MAINTAIN 连续监测不存在"；现在没有用途，没有影响 |
| 8.15 唯一计时者是 VALIDITY worker：发证时在同一事务里登记最早到期唤醒；启动时重建 | 做法不同已记录 | `assurance/expiry.py:59` `emit_due` 每轮扫证书表发到期事件（`assurance_tick.py:160`），启动时也扫（`assurance_assembly.py:222` `reconcile_startup`） | 实施记录"R03 到期唤醒"、第六段 |
| 8.16 时钟包装：持久高水位，回拨记一次 TimeDiscontinuity，代次 +1，恢复后回到 STABLE | 做法不同已记录 | `assurance/clock.py`；`orchestrator/assurance_clock.py:19` `observe_assurance_clock`；高水位每走 10 秒才落一次库 | 联测裁决 2026-10-05（代码注释 `clock.py:12-14`） |
| 8.17 回拨期间拒绝对时间敏感的用途，原始数据和费用照常 | 在用 | `assurance_reads.py:84-85` `TIME_DISCONTINUITY`；`assurance_tick.py:220-223` 回拨时只读入、不领取 | — |
| 8.18 RECHECK 退避：前 3 次立即，之后 500/1000/2000ms；累计 32 次或 300 秒转 MANUAL_REQUIRED | 在用 | `storage/assurance_work.py:351-383` `recheck`；`:248` 查到期时排除 MANUAL_REQUIRED | — |

## 附一 · §9 事件摄取、准备、提交和 ACK

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 9.1 持久待办箱 + 原主循环 tick；不另开调度器 | 在用 | `assurance_tick.py:200` `tick`，由 `event_handler.py:3694` 调用 | — |
| 9.2 target_epoch = 触发事件的 seq；seq 更大就合并，seq 相同但指纹不同就冲突 | 在用 | `assurance_work.py:110-156` `_enqueue` | — |
| 9.3 四个 consumer 各有自己的 cursor | 在用 | `assurance_work.py` `CONSUMERS`；`assurance_event_cursors` | — |
| 9.4 读一页 → 短事务里比对 cursor，入队和推进 cursor 同时完成 | 在用 | `assurance_work.py:157` `ingest` | — |
| 9.5 按行版本/租约领取；准备在事务外；先读入再处理，一个卡预算的审阅不挡后面的撤销 | 在用 | `assurance_tick.py:211-216,226-250` | — |
| 9.6 提交时重读领取/目标；效果 + 回执 + DONE 同一事务；已有回执也要 ACK | 在用 | `assurance_work.py:300` `commit`（前后两次 `_assert_claim`） | — |
| 9.7 WAITING / REJECTED 存原因和下次时间 | 在用 | `assurance_work.py:329` `wait`、`:351` `recheck` | — |
| 9.8 准备完来源又变了：旧 worker 不能 ACK | 在用 | `_assert_claim` 比对 row_version / target | — |
| 9.9 激活时在同一事务把 cursor 设到激活 seq，并列出已有工作形成对账清单 | 在用 | `assurance_factory.py:55-134` `create`；`assurance_assembly.py:196` `activation_inventory` | 因为只能激活新建的 Mission，清单一定是空的，代码会核实这一点 |
| 9.10 cursor 丢了就从 0 重建，按稳定 work_key 去重 | 在用 | `assurance_tick.py:170-180`；`reconcile_startup` | — |
| 9.11 自己发的事件分类为 IGNORE | 在用 | `assurance_consumers.py:76` `DIAGNOSTIC_EVENTS` | — |
| 9.12 每轮每页 ≤128，每个 consumer ≤8 项 | 在用 | `assurance_work.py:165,174`；`assurance_tick.py:225`；`mission_limit=8`（`:82`） | — |

## 附一 · 附录 C.1 固定新增接口与实际调用者

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| C1.1 `bind_assurance_profile_locked`（由 Mission factory 在同一事务调用） | 在用 | `assurance_factory.py:55` `AssuranceMissionFactory.create` → `assurance_store.py:76` `bind_profile_locked`；调用者 `commit_service.py:804` | 名字不同，等价 |
| C1.2 `approve_assurance_check_policy`（由原 Requirements 批准调用，不由 package builder 批准） | 做法不同已记录 | 调用者是 `deployment/duties.py:160` 和审阅运行时的 `check_policy_projector`（审阅准备前先投影） | 同 5.2 |
| C1.3 `ensure_assurance_review`（六类原 coordinator 调用） | 在用 | `assurance_purpose_reviews.py:157` `prepare_purpose_review`、`assurance_content_review.py:35` → `commit_service.py:637` `ensure_assurance_review_invocation` | — |
| C1.4 `ensure_format_repair_invocation`（只给确实结束的格式错误用） | 做法不同已记录 | `transport.py:861`，调用者 `assurance_review_consumer.py:433,478,514` | 现在还用于：判不下来的复审、解读错误、调用没回来（见 6.11–6.13） |
| C1.5 `record_local_check_run` | 在用 | `verification/assurance_local.py:135` `run`；`assurance_local_checks.py:529` `prepare` | — |
| C1.6 `import_assurance_check_locked` | 在用 | `commit_service.py:647`；`assurance_local_checks.py:189` `_import` | — |
| C1.7 `import_reviewer_disclosure_locked` | 在用 | `reviewer_evidence_tools.py:419,489`，调用者 `assurance_review_collect.py:140` | — |
| C1.8 `import_official_assurance_review` | 在用 | `assurance_review_import.py:542` `PreparedOfficialReview`；REVIEW consumer `assurance_review_consumer.py:103` | — |
| C1.9 `read_complete_evidence_snapshot` | 在用 | `assurance_reads.py:294`；调用者在 validity 和 purpose reviews | — |
| C1.10 `compute_assurance_use`（事务外有界计算） | 在用 | `assurance_validity.py:326` `_prepare_use` | — |
| C1.11 `commit_assurance_use_locked` | 在用 | `assurance_validity.py:768` `commit_use_locked`；调用者 `resolution_commits.py:1746,1795,1849` | 交接和披露没有经过它，见 C1.18 |
| C1.12 `accept_assured_review_locked`（接在 accept_review 里） | 在用 | `resolution_commits.py:1708` `_require_assured_use` | — |
| C1.13 `try_finalize_assured_mission` | 在用 | CLOSEOUT consumer `assurance_consumers.py:422,711` | — |
| C1.14 `_finalize_assured_mission_locked`（没有公开的裸调用入口） | 在用 | `assurance_final_writer.py:125`；装配 `assurance_assembly.py:401` | 同 7.10 |
| C1.15 `ingest_assurance_events_locked` | 在用 | `assurance_work.py:157` `ingest` | — |
| C1.16 `commit_assurance_work_locked` | 在用 | `assurance_work.py:300` `commit` | — |
| C1.17 `reauthorize_restored_read` | 已删（有决定） | — | 阶段 A″ |
| C1.18a 输入/上下文、工具交接都走统一的 `read_use` | 做法不同已记录 | 交接前核对 `action_commits.py:1111` `_validity_refusal`（用 HTN 有效性见证，不用保证证书）；执行者上下文按知识是否当前过滤 | 补齐计划表二 9、阶段 C 实施记录 |
| C1.18b 原生下载/摘要读取也走统一的 `read_use` | 做法不同未记录 | Host 读取只经过门面的归属检查和根门（`api/facade.py` `@_native_root`），没有 use 检查 | 单个 principal、没有 ACL 的部署，影响很小；但计划里这个读者没有出现在任何偏离记录里 |
| C1.19 `api/assurance.py` 的 use_check 只做诊断，不返回能当许可用的签名 | 在用 | `api/assurance.py:740,798,803`（`diagnostic_only=True`、`certificate_ref=None`） | — |
| C1.20 `*_locked` 都用原 Store 事务；read/prepare/compute 不写业务表 | 在用 | 各 prepare 函数一开头都查 `ASSURANCE_PREPARATION_INSIDE_TRANSACTION`；`commit_use_locked` 要求在事务里 | — |

## 附一 · 附录 C.3 原始数据、受信导入与无 hash 循环

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| C3.1 预留事件正文不含将来 invocation 的 hash；invocation 引用已经存好的预留事件 | 在用 | `transport.py:402-425` | — |
| C3.2 披露事件含标签增量 hash，不含本批的最终 hash；批次再引用事件 | 在用 | `assurance_review_import.py:276-287`（`delta_hash`）；`reviewer_evidence_tools.py:419` | — |
| C3.3 TurnImported 引用原 Provider 请求和输出，不含将来的记录绑定 | 在用 | `assurance_review_collect.py:114-125` | — |
| C3.4 pin 获取先有稳定的回执 ID；回执里不序列化含自身 hash 的 pin | 在用 | `storage/assurance_pins.py:20` `require_pin_receipt`；`assurance_review_pins.py:17,120` | — |
| C3.5 用量导入沿用原调用冻结的计价和身份，不改写过去的费用 | 用户定不做 | 金额计价已删（迁移 38）；用量身份不改写这部分照常在用 | 暂不做的十项之一：金额计价 |

## 附一 · 附录 C.5 事件适配、时间与队列状态

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| C5.1 按事件类别表逐个登记，不按名字前缀猜 | 在用 | `assurance_consumers.py:40-73`（`CLOSEOUT_SOURCE_EVENTS`、`VALIDITY_SOURCE_EVENTS`）；`assurance_review_consumer.py:50` `classify` | — |
| C5.2 一条事件可以喂多个 consumer，cursor 各自独立 | 在用 | `assurance_tick.py:169-197` | — |
| C5.3 MANUAL_REQUIRED = WAITING + 原因；查到期时排除；有更高 seq 的新事件可以合并重开；用户重试不重置累计上限 | 在用 | `assurance_work.py:248`、`:146-155`（重开时保留 tries） | — |
| C5.4 RUNNING 租约过期只回收协调权；原 service intent 照样复用 | 在用 | `assurance_work.py:213` `claim_due`；`transport.py:284-298` 复用已有调用 | — |
| C5.5 新来源事件升级目标，旧领取失效、不能 ACK | 在用 | `assurance_work.py:146-155`、`_assert_claim` | — |
| C5.6 发通知前先查同一条逻辑通知有没有回执 | 在用 | `assurance_consumers.py:788,790` | — |

## 附一 · 本段小结

### 状态统计（不含流程项 2.8）

| 状态 | 条数 |
|---|---|
| 在用 | 116 |
| 做法不同已记录 | 21 |
| 做法不同未记录 | 6 |
| 没做 | 1 |
| 只在测试 | 0 |
| 已删（有决定） | 5 |
| 已删（无记录） | 0 |
| 用户定不做 | 1 |
| 合计 | 150 |

（I4 是"在用（有缺口）"，算在"在用"里，缺口分别在 7.15 和 C1.18b 单独计数。）

### 需要关注的条目（做法不同未记录 / 没做；"只在测试"和"已删（无记录）"这次没找到）

1. **7.15 做法审阅和操作提案审阅的结论被消费时不检查使用证书（做法不同未记录）**：正式记录在导入时已经核过来源、曝光和独立性，但从导入到消费之间，如果来源被撤或纪元变了，不会再查一次。会不会让保证结论出错：**有一定风险**。被采用的做法或被批准的操作，可能依据的是已经过时的材料。不过操作提案在交接前还有一道有效性见证核对（C1.18a），所以实际风险主要在做法审阅上。是这次发现里影响最大的一条。
2. **C1.18b 原生下载/摘要读取没有统一的 use 检查（做法不同未记录）**：单个 principal、没有 ACL 的桌面部署里，只是少了一道"依据是否当前"的检查，不会让保证结论出错；属于文档和计划对不上。
3. **5.12 没有"全局安全问题必须挂到必须项"这一类（做法不同未记录）**：只有在 OR 公式里，审阅员把 BLOCKER 挂在非必须的分支准则上时才可能被绕过。H-3 之后"或"交给审阅员判，公式基本都是 AND，实际影响很小。
4. **8.7 命题键没有 namespace/scope，hash 截到 32 位（做法不同未记录）**：沿用的是 HTN 原来的公式，128 位不会碰撞，类型也能区分。不影响结论，只是和计划写的不一样。
5. **3.2 读取器返回形状和计划不同（做法不同未记录）**：功能等价，只是文档不符。
6. **3.6 tool_receipt / policy / authority / capability 这几类引用读不了（做法不同未记录）**：生产里没有地方构造这几类引用，而 policy/authority 已经由读集通道承担，现在没有影响。以后如果真把 tool_receipt 用作操作结果审阅的对象，会直接报 `REF_KIND_UNSUPPORTED`，属于提前埋下的坑。
7. **8.14 MAINTAIN 持续监测（没做）**：现在没有 MAINTAIN 用途，没有影响；实施记录里写过。

### 顺带发现的死代码 / 残留（不是计划条款，供清理参考）

- `commit_service.py:3145-3160` `judge_mission` 里非保证通道的直接 COMPLETED 尾巴：只有缺创建合同时才走得到，按"旧路径直接删"的规矩应该删掉。如果哪天走到了，会绕过收尾和唯一终态写入口，所以虽然很少触发，还是建议删。
- 生产代码里没有任何调用者的函数：`assurance_review_transport.py:609` `is_bound_task_review_subject`（opt.129 删掉调用者后剩下的）、`assurance/local_checks.py:207` `normalize_local_check`、`storage/assurance_store.py` `AssuranceStore.has_live_pin`、`assurance_purpose_reviews.py` `_exact_pin`、`knowledge/assurance_sources.py:102` `_is_system_key`。测试里也没有引用，都是纯死代码。


---

## 附二 · §10 旧备份恢复与当前授权

| 条目 | 现状 | 代码依据（file:line 与函数名） | 说明 |
|---|---|---|---|
| 离线备份还原到新目录、新 root id、`restore-quarantine.json` | 已删（有决定） | `SDK/assurance/root_gate.py:127-135` `_state_locked`（回执里 `restore_manifest_hash` 恒为空）；全库已无 `offline_backup`/`restore_offline` | 用户 10-02 决定②，A″ 连同受管恢复整套删除 |
| 根闸门先于 API/Context/调度等一切入口 | 在用 | `SDK/orchestrator/event_handler.py:646-662`（`__aenter__` 建闸门，失败只开管理）；`SDK/api/facade.py:115` `_require_native_root`；`api/missions.py:128`、`api/taskgraph.py:102`、`runtime/actions.py:102`、`context/knowledge_tools.py:161`、`deployment/native_pools.py:179` | 名字叫 `AssuranceRootGate.require_execution`，不叫 `open_assurance_root_gate`，作用相同 |
| 状态文件缺失/不匹配 → 隔离 | 在用 | `root_gate.py:95-140` `_state_locked`；`SDK/deployment/assembly.py:65-75` `assurance_root_setup`；`SDK/orchestrator/assurance_root_commits.py:116-177` `install_native_root` | 恢复删了以后只剩原生根；Host 每次启动用部署身份重装，状态文件丢了会被补回，不会长期隔离 |
| 非空未知根不当成新空库 | 在用 | `assurance_root_commits.py:98-113` `_native_origin` | 安装回执如实记 EMPTY / HISTORICAL |
| `reauthorize_restored_read`（重新授权读取） | 已删（有决定） | 全库无此函数；`HTN补齐-实施记录.md` A″ | A″ 删提交与两个对外入口 |
| 来源 ACL 当前权威确认、`READ_ONLY_REAUTHORIZED` 许可 | 已删（有决定） | 同上 | 随受管恢复一起删 |
| 先写库回执、再原子写根状态文件，中途崩溃保持隔离、同命令幂等补文件 | 在用 | `assurance_root_commits.py:40-67` `_publish`、`:116-177` `install_native_root` | 现在只服务原生安装 |
| 读授权默认 24h、按用途/对象精确 | 在用 | `SDK/orchestrator/assurance_assembly.py:53` `DEFAULT_READ_TTL_MS`、`:98-156` `FixedPrincipalAuthority._grant`；`root_gate.py:152-191` `require_read` | 改为"固定认证身份 + 本租户"的当前权威，每次使用证书都带它 |
| 隔离状态下按当前授权精确只读（artifact 读走 `require_read`） | 只在测试 | `SDK/api/facade.py:870-895`；`assurance_assembly.py:452` 才设 `_assurance_read_authority` | 隔离时启动装配不跑，读权威永远是空，生产一律拒；只有 `scripts/assurance_seams/root-gate-seam.py:120` 走到。恢复删后成了死分支（偏保守，不会错放） |
| 只授权披露，不复活旧执行/写工具/旧审批 | 在用 | 各执行入口 `require_execution`（见第 2 行） | 隔离时执行一律拒 |
| 非披露诊断（只回状态位、不回标题/路径/正文） | 只在测试 | `root_gate.py:193-207` `diagnostic`；`api/facade.py:125` `assurance_root_diagnostic` | Host 与前端都没调用，只有 `root-gate-seam.py:102` 调 |

## 附二 · §11 Lane、首次启用和默认 ON

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| `assurance_creation_contracts` 不可变创建判别 | 在用 | `SDK/storage/assurance_schema.sql:2-7`、不许改删触发器 `:385-389`；`SDK/orchestrator/assurance_factory.py:119` | |
| 新通道在建任务同一事务写激活事件/回执、绑定、纪元、四个游标，缺一即报错不回退 | 在用 | `assurance_factory.py:51-127` `AssuranceMissionFactory.create`；`SDK/storage/assurance_store.py:76-140` `bind_profile_locked`；SQL 触发器 `assurance_activation_lane` | |
| LEGACY / COMPLETION_V1 两条老通道保持原行为 | 已删（有决定） | `assurance_factory.py:142-163` `record_mission_creation`（非 planning-decision 或没装工厂一律拒）、`:166-180` `validate_creation_replay`（非 ASSURANCE_1_1 拒） | A′ 第 3 条"每个分层任务都走保证通道"。残留：SQL 枚举与迁移 26 的分类仍会给老库写这两种标签，但没有任何路径再跑它们（死数据） |
| 缺分类/来源矛盾 → `CREATION_CONTRACT_UNRESOLVED` | 在用 | `assurance_factory.py:171-178`；`assurance_store.py:145,250` | |
| 升级只按迁移前事实分类、留分类回执、不授新权 | 在用 | `SDK/storage/assurance_upgrade.py:18-108` | 只作为旧迁移文本保留（旧迁移不改的规矩），分类出的任务不能再运行 |
| 明确从旧任务建后继才用新 profile（`EXPLICIT_SUCCESSOR`） | 已删（有决定） | `assurance_store.py:280` 只剩枚举值，无写方 | 开发期不兼容旧任务（HTN 补齐计划表一 11） |
| 默认选择点 `default_assurance_profile_for_new_mission()` + Host 设置 `assurance_profile` | 做法不同已记录 | `assurance_factory.py:46-49` `selects`（每个 planning-decision 任务都走保证通道） | 函数与设置项都已删，没有"关"的开口；记于 `ARCHITECTURE/AGENT_ORCHESTRATION.md:107`、提交 eb749104 |
| 默认 ON 放在全部验收通过之后同批打开 | 做法不同已记录 | — | 用户 09-23 拍板先默认开启、验收后补（WIP 第十段（四）） |
| 风险动作原用户审批继续有效 | 在用 | `SDK/deployment/duties.py:54-122` `auto_confirm_content_completion` | 只有纯内容要求自动确认，带 `action:` 的等人点 |
| TaskGraph 独立开关继续有效 | 已删（有决定） | — | A′ 删"不开执行图"分支（HTN 补齐计划表一 16） |

## 附二 · §12 SQL、codec、引用资产一致性

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 9 张原表 + 6 张新窄表（creation_contracts、criterion_policies、review_invocations、disclosure_batches、pending_work、environment_state） | 在用 | `SDK/storage/assurance_schema.sql` | 15 张表名与计划 `sql/assurance_additive.sql` 完全一致；`assurance_pending_work` 多了 `rechecks`、`recheck_started_at_ms` 两列（`:313-314`，收尾复查用） |
| 收尾行初始 NOT_READY v1、只能 READY→FINALIZED、禁删禁替换 | 在用 | `assurance_schema.sql:151-155` `assurance_closeout_update_guard`、`:363-367`、`:407-409` | |
| pin 初始 PREPARING、BOUND 须同任务审阅绑定、RELEASED 不重开、不可变表禁改删、游标/队列单调 | 在用 | `assurance_schema.sql:237-247`、`:368-376`、`:385-404`；迁移 27 `SDK/storage/assurance_pin_object_schema.py` | 迁移 27 把"活 pin 唯一"从按字节改为按对象，理由写在模块注释 |
| Store 负责 hash 重算、回执归属、blob 真实存在、当前授权（不推给数据库） | 在用 | `SDK/storage/assurance_reads.py`（`read_exact_metadata` 等）、`assurance_store.py` | |
| 正常备份按物理 SQLite 还原含终态行 | 已删（有决定） | — | 离线备份整体删除（A″） |
| 空库按历史事件重放重建投影、不准临时 DROP 触发器 | 做法不同已记录 | 阶段 G 的 business-replay-v3 | 重放只做"逐表比对"，不重建库；见 HTN 补齐计划"阶段 G 开工裁决新增的偏离" |
| 1.0 校验和保留、取下一空闲迁移号 | 在用 | `SDK/storage/assurance_schema.py`（迁移 26） | |

### §12.1 一致性唯一源

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 共同 Ref/pin/读项在 `common.schema.json` | 在用 | `SDK/assurance/contracts/common.schema.json` = `SDK/assurance/schemas/common.schema.json` | 与计划包字节相同 |
| `tools/check_plan.py` 核字段表、ref 解析、fixture、DDL 列映射 | 没做 | 只在 `plans/Assurance/specs/1.1/tools/check_plan.py` | 没有对 SDK 实际 DDL 跑过；pending_work 加列后 `sql-column-producers.json` 也没更新 |
| 严格 JSON：≤256KiB、重复键/NaN/Inf/溢出/孤立代理/布尔当整数/未知字段拒绝、容器深度 64 | 在用 | `SDK/assurance/codec.py:15,53-58,80-137`（`fields`、`_validate_tree`、`_pairs`、`decode`） | |
| 例外：输入清单/证据快照读放宽到 8MiB | 做法不同未记录 | `codec.py:16-21` `MAX_RECORD_BYTES`；用于 `storage/assurance_reads.py:178,338,342`、`orchestrator/assurance_review_collect.py:62`、`runtime/assurance_turn_sources.py:91` | 只在代码注释说明（09-25 真机 211KB 清单超限）；编码不变、哈希不变，不影响结论 |
| 公式逻辑深度 16、512 节点、256 准则，分支保序 | 在用 | `SDK/assurance/checks.py:55-95` `Formula.from_json`；`assurance/policy.py:12-25` | |
| CheckPolicy：CHECKED 至少一组且组非空、SEMANTIC 无组 | 在用 | `checks.py:118-140` `CriterionPolicy.__post_init__` | |
| 作者必须非空、METHOD_PLAN 无可证作者回 SOURCE_UNAVAILABLE；理由限 2000 | 在用 | `assurance/reviews.py:200-201`；`orchestrator/assurance_purpose_reviews.py:195`；`checks.py:330,344` | |

## 附二 · §13 Host wire、隔离载入、原生验收

### §13.1 三个读 verb

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 三个下划线 verb 注册与薄委托 | 在用 | `Host/handlers.py:42-44,257-259`；`Host/service.py:1412` `assurance_read`；`Host/assurance.py:59-84` `read_assurance`；`SDK/api/facade.py:193-214` | |
| 请求不带租户/身份，调用者固定 | 在用 | `facade.py:193` `_assurance_read`；`SDK/api/assurance.py:613` 严格字段；`Host/assurance.py:66-67` 先过 `_mission` 归属 | |
| 每页 ≤100；CURRENT 同一序号切面；HISTORY 必须带序号且按当前权限算可用性 | 在用 | `api/assurance.py:35` `MAX_ITEMS`、`:613-646` `snapshot` | |
| 游标 = base64url(规范 JSON)，按 (kind,id) 排序，状态变了报 SNAPSHOT_CHANGED，历史页钉序号 | 在用 | `api/assurance.py:121-136` `_cursor_encode/_decode`、`:585-610` `_page` | |
| use_check 只诊断、不签可执行证书 | 在用 | `api/assurance.py:740-807` `use_check`（算完即 `forget` 候选） | |
| host-*-v1 DTO 合同 | 在用 | `SDK/assurance/contracts/*.json`、`contracts/__init__.py` `validate` | 运行时不校验，只在 `T/full_target/assurance_exec/test_c07_host_api.py` 与前端解析里校验；错误码 `RESTORE_QUARANTINED` 随 A″ 改名 `ROOT_QUARANTINED` |
| projection 只映射正式状态，不重推完成 | 在用 | `Host/assurance.py`（DTO 原样透传）；`FE/stores/assuranceStore.ts` 解析 | Host 不再自己推导 |
| 任务页保证视图：准则/审阅/效果/收尾/当前与历史；非法回复保留上次画面并报错；复用现有通道 | 在用 | `FE/views/MissionAssurance.tsx:19-185`；`FE/views/MissionsView.tsx:1244` | |

### §13.2 候选原生装载

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 隔离 Host 源码副本 + 独立 venv + 固定端口 + 候选 wheel（`prepare_native_manifest.py`） | 做法不同已记录 | `backend/deskpet/sdk_adapters/sdk_candidate.py:29-34`；`backend/pyproject.toml:174-201` | 保证线合入 main 后共享 Host 直接钉 vendored wheel；真机点击改用"调试版应用包 + 独立数据目录"（`plans/AgentRuntime/2026-09-23-arp-body/HANDOFF.md` §7.23、F2 联测记录 §三）。`prepare_native_manifest.py` 没用过 |
| 启动记录导入身份、wheel 哈希、Host/SDK 指纹，字节不同不混用 | 在用 | `backend/deskpet/sdk_adapters/runtime_paths.py:191-220` `verify_runtime_identity`；`sdk_candidate.py:154`；`Host/assurance.py:34-52` `host_fingerprint`；`api/assurance.py:138-155` `sdk_fingerprint`（每个响应都带） | |

### §13.3 原生场景

| 条目 | 现状 | 代码依据 / 证据 | 说明 |
|---|---|---|---|
| 真机点击：审阅详情→待效果/收尾→断线重连→历史与当前切换→撤回→冷恢复根隔离 | 验收没做 | 主流程点击：ARP HANDOFF §7.23（09-25，新建→审阅→验收→交付）；`plans/2026-09-27-desktop-next/HTN补齐-阶段F2-联测记录.md` §三（10-05，9 局） | 只点过主流程；保证视图的审阅详情、历史切换、断线重连、撤回没有点击记录；冷恢复已随 A″ 不适用 |
| 多用户/跨租户负例走接口验证 | 验收已做 | `T/full_target/assurance_exec/test_c07_host_api.py`；Host `test_assurance_host_api.py`（伪造租户字段拒绝），见 `HANDOFF-2026-09-23.md` §5 第九段 | |

## 附二 · §14 施工顺序与 BODY_WIRED

| 条目 | 现状 | 代码依据 / 证据 | 说明 |
|---|---|---|---|
| AS-0 合同/来源固定 | 验收已做 | `sdk/simple-harness-sdk/plans/assurance-1.1/baseline.md`；`HANDOFF-SOURCE-MANIFEST.json` | |
| AS-BODY 第 1～5 项主体实现 | 在用 | WIP 第一～九段；`SDK/orchestrator/assurance_*.py` 全套 | |
| BODY_WIRED 16 条生产调用边逐条登记关闭 | 没做 | `plans/assurance-1.1/BODY-WIRED.md` 只有门清单；WIP:3 仍写"BODY_WIRED 未通过"；`HANDOFF-2026-09-23.md:74` "16 条未关闭" | 之后没有任何关闭记录；BW14（受管恢复）已随 A″ 作废也没更新 |
| AS-VERIFY 集中验收顺序 | 做法不同已记录 | WIP 第十段（四） | 用户拍板先跑通主流程、默认开启，12 局/独立审阅等后补 |

## 附二 · §15 真实模型 oracle、预算、继承与完成标准

| 条目 | 现状 | 证据 | 说明 |
|---|---|---|---|
| 48 组确定性 SDK 场景 | 验收已做 | `证据/2026-09-23/assurance-1.1-verify/sdk-avce-occ-groups.*` 等；09-24 `assurance_exec` 77 passed | A′（693d215e）把 E 组、C08 等并入代表用例（`HTN补齐-阶段A撇-分诊表.md:140,144` 有记录），但 `sdk-cases.json` 49 个实际用例名里 16 个已不存在，清单没回写 |
| 继承 66 中 48 条（经映射用例） | 验收已做 | `plans/assurance-1.1/inherited-coverage.json`（48 条 EXECUTED） | 其中 11 处用例名已随 A′ 删除，未回写 |
| 继承 66 中 18 条 X 组（同操作跨尝试、外部键防重复等） | 验收没做 | `inherited-coverage.json`（18 条 `NOT_REMAPPED`） | 原计划要求由操作线执行，至今没映射也没跑 |
| OCC 12 | 验收已做 | `occ-coverage.json` 12/12（09-23） | A′ 改写为代表用例（分诊表:281 有记录），清单里 12 个用例名全部失效未回写 |
| 定点变异 16+ | 验收已做 | `证据/2026-09-23/assurance-1.1-verify/mutations-final/report.json`（22/22 抓到） | 现状过期：AM09、AM11 改坏原文已不在代码里，AM10/11/13/20 目标用例已删；A′ 后没重跑 |
| stateful 随机序列 | 验收已做 | `T/full_target/assurance_exec/test_stateful_assurance.py`；`sdk-stateful.*` | 固定种子，不用 Hypothesis（与 HTN 阶段 F 裁决同口径） |
| 真实模型 4 场景 × 3 局（M01–M04）+ runner `run_assurance_model_scenarios.py` | 验收没做 | 无 runner、无证据；只有 `证据/2026-09-23/assurance-1.1-verify/host-real-model/run-21`（1 局 Grok 主流程）和 F2 的 9 局（DeepSeek，HTN 场景题） | `完成度评估-2026-09-30.md` 第 13 项列为未做；用户"每场景 1 局"的决定是针对 HTN 42 组，不覆盖这里 |
| 每局预算 30 万 token/16 调用/32 工具/900s、INVALID_ENV 不减分母等试验纪律 | 没做 | 只存在于 `plans/Assurance/specs/1.1/implementation/model-scenarios.json` | 随 runner 一起没做；产品侧"格式修复最多 1 次"在用（`assurance/policy.py:31-34`、SQL `ordinal IN (1,2)`） |

### §15.1 文档与最终门

| 条目 | 现状 | 依据 | 说明 |
|---|---|---|---|
| 同交付回写 `ARCHITECTURE/AGENT_ORCHESTRATION.md`、`ASSURANCE.md`、`index.md`、`PROJECT_STATUS.md` | 在用 | 四份都有保证线条目 | |
| `ARCHITECTURE/ASSURANCE.md` 记 10-03 的两次删除 | 没做 | `ASSURANCE.md:1` 最后更新 10-02 | A′ 删老通道/关闭开关、A″ 删受管恢复只写在 `AGENT_ORCHESTRATION.md:53,107`、`PROJECT_STATUS.md:134-142`；`ASSURANCE.md:56` 还留着已删的 `default_assurance_profile_for_new_mission` 说法（历史条目） |
| 分开记 SPEC_RESOLVED / BODY_WIRED / SDK_VERIFIED / HOST_NATIVE_VERIFIED / MODEL_VERIFIED / INDEPENDENT_CODE_REVIEW | 做法不同未记录 | — | 文档用大白话写"做了什么、没做什么"，不用这些标签；没有说明换了写法 |
| 独立代码审查 | 验收已做 | `证据/2026-09-25/assurance-plan-challenge/README.md`（opus 只读挑战：阻断 2、重要 5） | 阻断 1（收尾不复查证书）由 opt.110 修（`PLAN-STATUS.md:524`）；之后大量改动没再审 |
| 默认 ON 后最终回归 | 验收已做 | `证据/2026-09-23/assurance-1.1-verify/host-orchestration-final.*` | 26 个失败与开启前逐条相同 |

## 附二 · §16 实际执行命令与交付

| 条目 | 现状 | 依据 | 说明 |
|---|---|---|---|
| 用候选环境/冻结依赖跑；证据目录先确认被忽略、每次新目录 | 在用 | `.local-test-evidence/` 被 git 忽略；目录按日期/run-N | |
| `verify_delivery.py`/`check_plan.py`/`capture_identity.py` 生成 source-map | 做法不同已记录 | `scripts/verify_development_handoff.py` + `HANDOFF-SOURCE-MANIFEST.json`（`HANDOFF-2026-09-23.md` §1、`plans/Assurance/HANDOFF-VALIDATION-2026-09-23.md`） | 没有 `source-map.local.json` |
| 主体后跑 `assurance_exec` 并留 junit | 验收已做 | `证据/2026-09-23/assurance-1.1-verify/sdk-*.junit.xml` | |
| `gate-commands.local.json` 登记遗留用例/变异/H1 命令 | 没做 | 无此文件 | 变异命令在 `plans/assurance-1.1/tools/run_assurance_mutations.py` |

## 附二 · 附录 A：资产与新接口落点

| 条目 | 现状 | 依据 | 说明 |
|---|---|---|---|
| `integration-map.json` 单 owner 映射 | 没做 | 只在计划包 | 没随代码维护，"备份"一栏已随 A″ 失效 |
| `ref-resolution-map`/`field-producers`/`sql-column-producers` | 没做 | 只在计划包 | 代码按 `assurance_reads.py`/`refs.py` 实现，没有自动核对；加列后未更新 |
| `contracts/*.schema.json` | 做法不同未记录 | SDK 只随包 16 份（7 份 host + 9 份内部，内部的只当身份哈希用，见 `assurance/check_specs.py:16-30`） | blob-pin、closeout、criterion-policy、disclosure-batch、pending-work、policy、review-reply-v2、success-formula 这 8 份只在计划包，代码用 Python 字段校验代替；restore-authorization 随 A″ 删 |
| `sql/assurance_additive.sql` | 在用 | `SDK/storage/assurance_schema.sql` | 见 §12 |
| `reference/protocol_v11.py`、`semantics.py` | 在用 | 计划包 | 按计划只当参考实现，不进产品 |
| `model-scenarios.json` | 没做 | 计划包有，SDK `plans/assurance-1.1/` 有副本 | 没有 runner 接真实运行时 |
| `inherited-coverage.json`/`occ-coverage.json`（及 `sdk-cases.json`）随用例回写 | 没做 | `plans/assurance-1.1/*.json` | A′ 后共约 39 处用例名指向已删用例（见 §15） |

## 附二 · 附录 C.2：第一次启动和批准政策的先后

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 建任务事务里依次写任务与要求→创建判别→激活事件/回执→绑定、纪元、四游标 | 在用 | `assurance_factory.py:51-127`；`SDK/orchestrator/commit_service.py:803-804` | |
| 检查策略随后写，读者不要求还没建的审阅/预留 | 做法不同已记录 | `SDK/deployment/duties.py:160` `project_check_policies`；`assurance_assembly.py` `check_policy_projector` | 由部署在首次审阅前按原要求无损推导并批准（WIP 第十段（五）、run-7/run-15）。09-25 挑战指出它记成 actor_type=human |
| 新空环境的全局纪元/时钟行由真实安装回执产生 | 在用 | `assurance_root_commits.py:69-95` `_initialize_clock`；`assurance_store.py` `initialize_environment` | |
| 回执不递归包含自身哈希；纪元/游标用同事务延迟外键 | 在用 | `assurance_factory.py:96-118`；SQL `DEFERRABLE INITIALLY DEFERRED` | |
| 历史非空根首次安装须经当前认证安装入口登记 | 在用 | `deployment/assembly.py:65-75`；`assurance_root_commits.py:98-113` | 每次启动以部署身份自动安装，不是额外的人工启用批准 |
| 门面显式安装入口 `install_assurance_root` | 只在测试 | `SDK/api/facade.py:178-189` | Host 不调（部署直接调提交层）；死入口 |

## 附二 · 附录 C.4：Pin/GC

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| acquire/bind/release，唯一写者 AssuranceStore | 在用 | `SDK/orchestrator/assurance_review_pins.py:17-200`；`assurance_store.py` `acquire_pin`/`transition_pin` | 文件名与计划的 `artifacts/assurance_pins.py` 不同（计划允许等价名） |
| 先记 PREPARING 再读 CAS；读失败就释放并报 SOURCE_UNAVAILABLE | 在用 | `orchestrator/assurance_content_review.py:247,308-310`；`assurance_purpose_reviews.py:330,384` | |
| 启动对账释放孤儿 PREPARING | 在用 | `assurance_assembly.py` `reconcile_startup` → `assurance_review_pins.py:201` `release_orphan_preparations` | |
| 没有 GC 时不新建定时 GC、数据保留 | 在用 | `SDK/artifacts/store.py` 无删除；`Host/storage_usage.py` 只统计提醒 | 与"会话数据永久保留"一致 |
| SQL 只保证 pin 身份/状态，不保证字节存在 | 在用 | `assurance_schema.sql:221-247`；迁移 27 | |

## 附二 · 附录 C.6：Host DTO 字段语义

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| `history_state` 取固定序号状态，`current_use` 按当前权限/根/有效性算 | 在用 | `api/assurance.py:341-363` `_item`、`:374-583` | |
| review 响应：官方状态、逐准则 model/effective 等级与检查门、披露标签；正文走获准 artifact 读 | 在用 | `api/assurance.py:648-738` `review`；`FE/views/MissionAssurance.tsx:162-171` | |
| use_check：`diagnostic_only=true`、`certificate_ref=null` | 在用 | `api/assurance.py:796-806` | |
| 没有完整历史覆盖 → HISTORY_UNAVAILABLE | 在用 | `api/assurance.py:487,754` | 错误码按合同用 SOURCE_UNAVAILABLE，消息带 HISTORY_UNAVAILABLE（`host-error-v1` 枚举里本来就没有它） |
| 当前视图翻页途中状态变化 → SNAPSHOT_CHANGED，不混页 | 在用 | `api/assurance.py:590-597`（filter_hash 含纪元、根、末序号） | |
| 分页不当作证据集合完整的声明 | 在用 | `truncated`/`next_cursor`；前端"尚有后续页" | |
| 历史视图的准则列表 | 做法不同未记录 | `api/assurance.py:514-519` `_history_items`（按激活时的要求哈希取第 1 版） | 阶段 E 上线"中途改要求"后，改要求之后的历史切面仍列旧版准则；当前视图用最新版（`:383`）。只是显示偏差 |

## 附二 · 本段小结

### 状态统计（共 93 条）

| 状态 | 条数 |
|---|---|
| 在用 | 50 |
| 做法不同已记录 | 7 |
| 做法不同未记录 | 4 |
| 没做 | 9 |
| 只在测试 | 3 |
| 已删（有决定） | 7 |
| 已删（无记录） | 0 |
| 用户定不做 | 0 |
| 验收已做 | 10 |
| 验收没做 | 3 |

### 需要关注的条目（非"在用/已记录/用户定不做/验收已做"）

| 条目 | 状态 | 影响 |
|---|---|---|
| 真实模型 M01–M04 × 3 局 | 验收没做 | 大：保证机制在真实模型下"不假完成、拒坏候选、撤回后不复用旧证书、外部效果恰好一次"四件事都没有实测，只有 1 局主流程跑通 |
| 继承 X 组 18 条 | 验收没做 | 中：同一操作跨尝试不重复执行、外部键防重复等语义没有对应用例被执行过 |
| 保证视图真机点击（审阅详情/历史切换/断线重连/撤回） | 验收没做 | 中：界面只点过主流程，这些交互可能有界面缺陷，不影响后台保证结论 |
| 每局预算与试验纪律 | 没做 | 小：只属于 12 局试验的规矩，随 runner 一起缺 |
| BODY_WIRED 16 边登记 | 没做 | 中：没有一张"每条生产边都接上"的核对表，接线完整性只能靠后续真机局间接证明 |
| 覆盖清单回写（sdk-cases/occ/继承） | 没做 | 中：A′ 后约 39 处用例名指向已删用例，从清单无法证明这些保证仍有测试守着（用例多数已并入代表用例，有记录） |
| 变异清单过期（并入 §15"变异"行说明） | 验收已做但过期 | 中：2 条改坏原文、4 条目标用例已不存在，A′ 后没重跑，保护是否还在没有证据 |
| `check_plan.py`/字段与列映射核对 | 没做 | 小：设计资产与代码不同步（如 pending_work 新增两列），只是文档不符 |
| `integration-map.json`、各映射表 | 没做 | 小：计划资产没随代码维护，文档不符 |
| `model-scenarios.json` 无 runner | 没做 | 同第一条 |
| `gate-commands.local.json` | 没做 | 小：命令登记文件缺，变异命令另有脚本 |
| `ASSURANCE.md` 未记 10-03 两次删除 | 没做 | 小：事实源文档过期，接手人可能以为还有关闭开关/受管恢复 |
| 隔离状态下精确只读（`require_read` 分支） | 只在测试 | 小：受管恢复删后成死分支，生产一律拒读，偏保守，不会错放 |
| 非披露诊断 `assurance_root_diagnostic` | 只在测试 | 小：死代码，Host/界面不展示根隔离原因 |
| 门面 `install_assurance_root` | 只在测试 | 小：死入口，生产走部署安装 |
| 8MiB 记录读上限 | 做法不同未记录 | 小：只放宽读上限，编码和哈希不变，不影响结论；应补一句记录 |
| 状态标签不用 | 做法不同未记录 | 小：文档写法不同 |
| 8 份内部 schema 未随包、用 Python 校验代替 | 做法不同未记录 | 小～中：结构是否与合同逐字段一致没有自动核对，出偏差也不会被发现，但不直接让结论出错 |
| 历史视图准则固定第 1 版要求 | 做法不同未记录 | 小：改过要求的任务，历史切面显示旧准则；只是显示，不影响保证结论 |


---

## 附三 · 覆盖审计

## Assurance 原计划覆盖性复核（第二遍细核，2026-10-05）

**对象**：第一遍对照 `plans/2026-09-27-desktop-next/Assurance-现状对照-2026-10-05.md`（下称『对照文档』）是否逐条覆盖原计划 `plans/Assurance/specs/1.1/ASSURANCE-EXEC-1.1.zh-CN.md`（下称『原计划』）§0～§16、附录 A、B、C.1～C.6，以及 `RESPONSE-TO-REVIEW.md` F01～F15 关闭判据。代码版本 main `5bae3e92`（含对照文档提交）。只读，没跑测试。

**路径简写**：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`；`FE/` = `tauri-app/src/`。

**口径**：一句话一个要求；表格每行一条；纯流程项（单一集成人、不 reset/rebase、子代理模型约束、source-map 初始化、四视角自审写法、附录 B 外部链接、截图入库方式）不计。原计划正文把测试清单放在 `implementation/TEST-MAP.md` 等包内资产里，`RESPONSE-TO-REVIEW.md` F14/F15 把它们列为『已修改资产』，所以变异、F 反例、stateful 规模三条按包内资产要求计入。

### 一、数字

| 项 | 数 |
|---|---|
| 要求清单总数（不含流程项） | 314 |
| 对照文档已覆盖（有对应行且核到具体内容） | 265 |
| 漏核（无对应行，或只被笼统合并） | 49 |
| 漏核中现状不是『在用 / 验收已做』的（新发现的非在用） | 8 |

说明：
- 漏核集中在三处：§3.1 种类表 17 行被并成对照文档 3.5 一行；§8.1 屏障表 9 行被并成 8.3 一行；§14 AS-VERIFY 的各步与包内测试资产（F 反例、变异清单、stateful 规模）。
- 漏核的 49 条里 41 条本次核到『在用』或『验收已做』，说明主体代码确实按计划接上了；对照文档的主要问题不在代码结论，而在**验收侧**：把『自选 22 条变异』『6×14 stateful』当成计划要求已完成，漏了 F01～F15 反例这一整组验收。
- 对照文档自报 243 条，与本清单数目不同主要因为上面三处合并，以及它把若干『顺带发现』（死代码、门面入口）算成了条目。

### 二、漏核条目

| 原文行号 | 要求 | 现状 | 代码依据 | 说明 |
|---|---|---|---|---|
| L7 | 冲突以本正文为准；OCC 完成范围、Operation 身份、D3 原 raw 恢复语义不变 | 在用 | `SDK/orchestrator/assurance_consumers.py:422` 效果只经 `completion_status.read_current_effect`；`operation_materialization.py:465` 仍由原 `OperationEnvelope` 签 operation_id | 对照文档无此行；抽查未见 Assurance 改写 OCC/Operation 身份 |
| L15 | 应用（审阅）模型从当前已批准 profile 读取，不改用户 DeepSeek 配置 | 在用 | `SDK/orchestrator/assurance_review_runtime.py:234-283` 用原路由 `decision.profile_id`（含冷却 `REVIEW_ROUTE_UNAVAILABLE`） | 对照文档无此行 |
| L38 | TASK_CONTENT：已有受信 Critic 经绑定适配，不重问同一审阅 | 在用 | `SDK/orchestrator/assurance_review_transport.py:381` TASK_CONTENT 用 `critic` kind 接原 `_run_critic`；`T/full_target/assurance_exec/test_assurance_critic_owns_first_tail.py` | 对照文档 6.3 只记 kind 不同，没核『不重问』 |
| L39 | METHOD_PLAN：结构检查不伪装语义 Review；只准入方法、不完成 Task | 在用 | `SDK/orchestrator/plan_commits.py:381` `_check_method_reviews` 只认正式记录/人裁决，不判做法好坏、不写 Task 终态 | 对照文档无此行 |
| L41 | ACTION_PROPOSAL：审阅通过不代替用户审批 | 在用 | `SDK/orchestrator/operation_materialization.py:503` → `action_commits.py:550` 需审批时仍进 `AWAITING_APPROVAL` | 对照文档无此行 |
| L43 | MISSION_FINAL：每 round 独立 package | 在用 | `SDK/orchestrator/assurance_purpose_reviews.py:201` review_key=purpose+mission+package_id，`root_review.py:831` 每次 cut 新包 | 对照文档 2.6 只核『不双发』 |
| L59 | 过期拒绝用于新动作，但历史 raw/成本可导入 | 在用 | `SDK/orchestrator/assurance_review_consumer.py:165` 主题已停→`_prepare_late`，`:378` 写 `AssuranceReviewLateTurn` 只存 raw/费用 | 对照文档无此行 |
| L63 | commit_receipt：不可变 body、revision=0、真实 Commit writer 签发 | 做法不同未记录（小） | `SDK/storage/assurance_reads.py:111-196` `_EXACT['commit_receipt']` 按 commit_id 全库查，只核 `body.mission_id`，不核写者（表无 issuer 列） | 对照文档把 17 行种类表合成 3.5 一行『在用』，没逐行核；事件类 ref 核 `actor_type=system`，回执类没有对等的写者核对 |
| L64 | reservation_fact：AssuranceReservationLinked 含原 reserve 身份/账户/subject/上限/原 receipt，同事务；是桥不是余额 | 在用 | `SDK/orchestrator/assurance_review_transport.py:393-418`（核 RESERVED/账户/tokens 后发事件，正文带整条 reserve） | 金额上限改成 token 上限，随金额计价删除（用户口径） |
| L66 | execution_receipt：受信 importer 以 provider/call 身份与实际结果核对；review/read 不跨库原子写 | 在用 | `SDK/assurance/event_kinds.py:7`；`SDK/orchestrator/assurance_local_checks.py` 执行器导入；`assurance_reads.py:209` 事件桥要求 `actor_type=system` | 对照文档无单独核对 |
| L69 | check_spec：不可变注册事件，registry 安装时由固定系统 owner 写入 | 做法不同未记录（小） | `SDK/orchestrator/assurance_local_checks.py:487-509` 每个 Mission 首次需要时由检查导入器懒注册（system 事件+回执） | 不是安装时一次登记；身份仍不可变，影响小 |
| L70 | check_policy：引用精确 requirements revision 与 scope | 在用 | `SDK/orchestrator/assurance_check_policy.py:225-265`（policy_id 由 mission+requirements_ref+domain 生成）；`storage/assurance_schema.sql:435-436` 同域重写拒绝 | 对照文档 5.2 只核写入时点 |
| L72 | review_package：原六 purpose builder 产、Mission/round/purpose 精确匹配、revision=0 | 在用 | `SDK/storage/assurance_reads.py:115`（按 mission 过滤、revision 必须 0）；purpose/round 由绑定 `subject.purpose` 与 `round_no` 核 | 对照文档无单独核对 |
| L74 | method_instance：读明确 instance 与 plan pin，不查最后一个 | 在用 | `SDK/storage/assurance_reads.py:124` 以 `plan_revision` 作精确修订号 | 对照文档无单独核对 |
| L75 | artifact/input_manifest：完整字节、nofollow/hash 读取，不把 URI 当权限 | 在用 | `SDK/storage/assurance_blobs.py:178` `open_nofollow`；`artifacts/store.py:44-47` O_NOFOLLOW | 对照文档无单独核对 |
| L76 | source/observation/review/acceptance/resolution：精确 body，另查当前可用性，不改历史 body | 在用 | `SDK/storage/assurance_reads.py:111-196` 只读精确 body；当前可用性在 `assurance_validity.py:686` `require_current_locked` | 对照文档无单独核对 |
| L77 | completion_scope/spec：归属、requirement hash、plan occurrence 用原 OCC reader | 做法不同未记录（小） | 通用 resolver `assurance_reads.py:119-120` 直接读 `operation_completion_scopes/specs` 表；只有批准政策时用 `OperationCompletionReader.read_scope`（`assurance_check_policy.py:155`） | body hash 仍核，功能等价；与计划写法不同 |
| L87 | 已完成调用的 reservation 可已 SETTLED，导入历史 review/费用不要求 ACTIVE | 在用 | `SDK/orchestrator/assurance_review_collect.py:166` 结算后照常导入；迟到回复走 LateTurn | 对照文档无此行 |
| L97 | 重复 exact ref 规范去重，标签不靠数组位置 | 在用 | `SDK/assurance/evidence.py:34-43` `build_catalogue`（setdefault 去重、按 label 排序） | 对照文档无此行 |
| L105 | 旧 Agent 私有上下文不自动继承给新 Agent | 在用 | `SDK/orchestrator/assurance_review_consumer.py:391-399` 复审在新会话、原样重发冻结请求，不带第一位结论 | 对照文档无此行 |
| L106 | Replay 从 raw+binding+disclosure chain 恢复，不检索 latest、不改原 input_id/hash | 在用 | `SDK/orchestrator/assurance_review_consumer.py:145-164`、`assurance_review_import.py:626-644`（已有正式记录时只读不可变旁挂） | 对照文档无此行 |
| L108 | 追加 fetch 受真实工具预算、scope、只读边界 | 在用 | `SDK/orchestrator/assurance_review_runtime.py:33-47` `REVIEW_TOOL_CALLS`；`verification/reviewer_evidence_tools.py:163-174,264-292` 按审阅 scope、带预算提示、limit≤50 | 对照文档 4.4 只核『经 ToolGateway』 |
| L114 | 政策域绑定 Requirements hash+scope hash；一份批准不能被后来 builder 改 mode | 在用 | `SDK/orchestrator/assurance_check_policy.py:225-275`（同回执异 body→IMMUTABLE_IDENTITY_CONFLICT）；`storage/assurance_schema.sql:435-436` 同 mission+requirements+scope 再插入拒绝 | 对照文档无此行；check_policy_projector 先投影也改不了已批准的 mode |
| L116 | TASK_CONTENT 用现有 scoped projection 的 criteria/证据政策，不因旧 LayerResult 无 execution_ref 永久拒绝 | 在用 | `SDK/orchestrator/assurance_check_policy.py:196-208` 用 `read_task_check_policy_projection` | 对照文档无此行 |
| L120 | 运行前固定 checker digest、subject/input/env、run identity；逐 assertion 输出 hash | 在用 | `SDK/assurance/local_checks.py:86-111` `record_run` 先冻结 environment/implementation/started_at；`verification/assurance_local.py:139-171` | 对照文档无此行 |
| L122 | 断电前未持久的检查结果仍 UNKNOWN；无副作用可重跑；不从 cache 猜 PASS | 在用 | `SDK/verification/assurance_local.py:173-190` 先运行再 persist，没回执就没有 check binding，门判 UNKNOWN | 对照文档无此行 |
| L126 | 执行记录真值表：SUCCEEDED×PASS/FAIL/UNKNOWN；ERROR/CANCELLED/NOT_RUN/RUNNING/缺失/过期/错范围→UNKNOWN；畸形拒绝导入 | 在用 | `SDK/assurance/checks.py` `evaluate_check_gate`；`assurance_local.py:186-190` 非 SUCCEEDED 一律 ERROR；变异 AM04『来源无效的检查结果不是 PASS』 | 对照文档 5.9 只核模型等级×检查门那张表 |
| L138 | 失败组反证仍在完整候选/有效性中核对；权限/hash/Scope 硬门不受 ANY 绕过 | 在用 | 权限/hash/Scope 在 `assurance_validity.py:686-800` 与 `resolution_commits.py:1708` 独立于公式；反证输入随阶段 D 收窄（见 8.10b，已记录） | 对照文档无此行 |
| L144 | 旧 review_records 每包唯一 official 约束保留 | 在用 | `SDK/storage/htn_schema.py:355-356` `review_records_official_idx`（package_id WHERE official=1） | 对照文档无此行；复审同包也受它约束（第一位 INCONCLUSIVE 不入正式记录） |
| L154 | 不声称跨 execution.db 原子；真实 Agent 由原 dispatch consumer 执行，call 账本后导入 | 在用 | `SDK/orchestrator/assurance_review_collect.py:28,114,166`（原 dispatch 收尾后导入 turn、结算 intent） | 对照文档无此行 |
| L176/L179 | round 上限用原冻结责任政策，同责任累计预算 | 做法不同未记录（小） | `SDK/orchestrator/assurance_content_review.py:207`、`assurance_purpose_reviews.py:288` `round_no` 恒为 1；轮次体现为新包/新 review_key，上限落在原 Task `Budget.max_attempts`（`plan_commits.py:1448,1556`）与规划次数 | 效果上有界，但 binding 的 round_no 字段已名存实亡，无记录 |
| L211 | 用户已读无回执即 UNKNOWN；外部交付走原 Operation，本地通知不完成外部效果 | 在用 | `Host/notices.py:88-110` `pending/ack` 有已读回执；外部效果只经 `operation_materialization.py` | 对照文档无此行 |
| L221 | 屏障：put_source/替代/撤回/tombstone；归属不明或跨 Mission 共享→global | 在用 | `SDK/storage/assurance_barrier_v26.sql`：sources/observations/justification_sets/support_members/requirements_revisions/operation_completion_*/task_semantics/method_*/plan_*/actions/operation_*/artifacts/review_records/acceptances/planning_lane_grants 均有 insert/update/delete 触发器；method_contracts/policy_* 推全局 epoch（`:3226-3440`）；sources 带 mission_id，不存在跨 Mission 共享 | 对照文档 8.3 一行概括『212 个触发器』，没逐行核；本次逐表核到触发器 |
| L222 | 屏障：insert_observation 幂等首次插入才 bump，正反证同等 | 在用 | `SDK/storage/assurance_barrier_v26.sql`：sources/observations/justification_sets/support_members/requirements_revisions/operation_completion_*/task_semantics/method_*/plan_*/actions/operation_*/artifacts/review_records/acceptances/planning_lane_grants 均有 insert/update/delete 触发器；method_contracts/policy_* 推全局 epoch（`:3226-3440`）；sources 带 mission_id，不存在跨 Mission 共享 | 对照文档 8.3 一行概括『212 个触发器』，没逐行核；本次逐表核到触发器 |
| L223 | 屏障：justification/support member/rule 准入与撤回同事务 | 在用 | `SDK/storage/assurance_barrier_v26.sql`：sources/observations/justification_sets/support_members/requirements_revisions/operation_completion_*/task_semantics/method_*/plan_*/actions/operation_*/artifacts/review_records/acceptances/planning_lane_grants 均有 insert/update/delete 触发器；method_contracts/policy_* 推全局 epoch（`:3226-3440`）；sources 带 mission_id，不存在跨 Mission 共享 | 对照文档 8.3 一行概括『212 个触发器』，没逐行核；本次逐表核到触发器 |
| L224 | 屏障：Requirements、OCC Spec/Scope 正式变化 | 在用 | `SDK/storage/assurance_barrier_v26.sql`：sources/observations/justification_sets/support_members/requirements_revisions/operation_completion_*/task_semantics/method_*/plan_*/actions/operation_*/artifacts/review_records/acceptances/planning_lane_grants 均有 insert/update/delete 触发器；method_contracts/policy_* 推全局 epoch（`:3226-3440`）；sources 带 mission_id，不存在跨 Mission 共享 | 对照文档 8.3 一行概括『212 个触发器』，没逐行核；本次逐表核到触发器 |
| L225 | 屏障：Method/Task adopted input 或 contract 变更，与 PlanCommit 同事务 | 在用 | `SDK/storage/assurance_barrier_v26.sql`：sources/observations/justification_sets/support_members/requirements_revisions/operation_completion_*/task_semantics/method_*/plan_*/actions/operation_*/artifacts/review_records/acceptances/planning_lane_grants 均有 insert/update/delete 触发器；method_contracts/policy_* 推全局 epoch（`:3226-3440`）；sources 带 mission_id，不存在跨 Mission 共享 | 对照文档 8.3 一行概括『212 个触发器』，没逐行核；本次逐表核到触发器 |
| L227 | 屏障：operation 更正/反向证据、execution 结果 import | 在用 | `SDK/storage/assurance_barrier_v26.sql`：sources/observations/justification_sets/support_members/requirements_revisions/operation_completion_*/task_semantics/method_*/plan_*/actions/operation_*/artifacts/review_records/acceptances/planning_lane_grants 均有 insert/update/delete 触发器；method_contracts/policy_* 推全局 epoch（`:3226-3440`）；sources 带 mission_id，不存在跨 Mission 共享 | 对照文档 8.3 一行概括『212 个触发器』，没逐行核；本次逐表核到触发器 |
| L228 | 屏障：artifact 删除/访问状态、Check/Review 有效性更正 | 在用 | `SDK/storage/assurance_barrier_v26.sql`：sources/observations/justification_sets/support_members/requirements_revisions/operation_completion_*/task_semantics/method_*/plan_*/actions/operation_*/artifacts/review_records/acceptances/planning_lane_grants 均有 insert/update/delete 触发器；method_contracts/policy_* 推全局 epoch（`:3226-3440`）；sources 带 mission_id，不存在跨 Mission 共享 | 对照文档 8.3 一行概括『212 个触发器』，没逐行核；本次逐表核到触发器 |
| L229 | 屏障：scope/grant 撤回 | 在用 | `SDK/storage/assurance_barrier_v26.sql`：sources/observations/justification_sets/support_members/requirements_revisions/operation_completion_*/task_semantics/method_*/plan_*/actions/operation_*/artifacts/review_records/acceptances/planning_lane_grants 均有 insert/update/delete 触发器；method_contracts/policy_* 推全局 epoch（`:3226-3440`）；sources 带 mission_id，不存在跨 Mission 共享 | 对照文档 8.3 一行概括『212 个触发器』，没逐行核；本次逐表核到触发器 |
| L258 | 证书即时使用总是检查 now，timer 延迟也不放行 | 在用 | `SDK/orchestrator/assurance_validity.py:686-760` `require_current_locked(now_ms)` 每次复核纪元/权限/到期 | 对照文档无此行 |
| L276 | profile 未绑定/恢复隔离：保存等待，不触发模型格式重试 | 在用 | `SDK/orchestrator/assurance_tick.py:201,229,251,268` 每段先 `require_execution_root()`，隔离时整轮不领取 | 对照文档无此行；做法是整轮停而非逐行 WAITING |
| L350 | 返回 hash/body 字段不带 secret | 在用 | `SDK/api/assurance.py:687-697` review 只回 reason_codes（check:/limitation:），不回自由正文 | 对照文档无此行 |
| L368 | UI/接口不因是否找到 ID 泄露对象存在 | 在用 | `SDK/api/facade.py:216-220` 不存在与不归属同报 `not_found`；`Host/assurance.py:66-67` 先过 `_mission` | 对照文档无此行 |
| L390 | AS-VERIFY：SQL 迁移/竞争/强退 | 验收已做 | `T/full_target/assurance_exec/test_c_assurance.py` C01–C06（双连接 CAS、SIGKILL 子进程、真实迁移/触发器）；证据 `.local-test-evidence/2026-09-23/assurance-1.1-verify/sdk-vc-groups.*` | 对照文档没有单列；A′ 后 C 组是否完整保留未复核 |
| L390 | AS-VERIFY：legacy/full H1 已批准回归 | 验收没做完 | `.local-test-evidence/2026-09-23/assurance-1.1-verify/sdk-full-regression.txt` 停在 89%（无汇总行，WIP 记『p33 多进程用例挂死』）；Host `host-orchestration-legacy.txt` 26 failed；`HANDOFF-2026-09-23.md:89` 把『26 个既有失败迁新协议、SDK 全量挂死』列为未做 | 对照文档只写『默认 ON 后最终回归 验收已做（26 失败与开启前相同）』，没区分 legacy/full H1 这一步本身没跑完 |
| L457（+TEST-MAP L5、findings-sdk-tests.json） | F01–F15 决定性反例落到 SDK（15 个 target nodeid `test_review_findings.py::*`） | 验收没做 | SDK `tests/orchestrator/full_target/assurance_exec/` 无 `test_review_findings.py`；`sdk/simple-harness-sdk/plans/assurance-1.1/` 没有 findings-sdk-tests 副本或映射；计划包 `implementation/findings-sdk-tests.json` 15 条仍 `PENDING_SDK_EXECUTION`；`HANDOFF-2026-09-23.md:89` 明列『F反例』待做 | 对照文档完全没提 F 反例这一组验收 |
| TEST-MAP L7（mutations.json 16 + additional-mutations.json 12） | 原 16 条 SDK 变异 + 12 条 F 定点变异，按指定 must_be_killed_by 执行，import/语法错不算 KILLED | 做法不同未记录 | SDK `plans/assurance-1.1/mutations.json` 换成自选 22 条 AM01–AM22，没有与 M01–M16/F-M01–F-M12 的映射；无对应的至少有：M05 空效果目录、M06 Delivery 只核非空、M07 读 latest 输入、M08 失败查询返回 COMPLETE 空、M11 删 clean closure、M13 Acceptance 内部提前 commit、M15 lease 过期直接重发、M16 未知用量记 0 释放、F-M01 证据只按 id 键、F-M03 SEMANTIC 伪造 True、F-M04 按 Task 键 invocation、F-M05 旧 judge 直接放池、F-M09/10/11 SQL 初始 FINALIZED/DELETE-REPLACE/无审阅 BOUND、F-M12 删 schema hash | WIP 第十段（三）只写『22 条定点变异』，没说替换了计划清单；对照文档把它当作『16+ 验收已做』 |
| TEST-MAP L8 | stateful 200×50（状态化随机序列规模） | 做法不同未记录 | `T/full_target/assurance_exec/test_stateful_assurance.py` 6 种子×14 步（`sdk-stateful.txt` 6 passed） | WIP 只记『不用 hypothesis』，没记规模从 200×50 降到 6×14 |

#### 新发现的非在用条目（按影响排序）

1. **F01～F15 决定性反例从未落地**（验收没做）：计划包 `findings-sdk-tests.json` 给了 15 个目标用例，SDK 里一个都没有，也没映射到已有用例；HANDOFF 第 10 项明确列为待做，之后没有关闭记录。对照文档没提这一组。
2. **变异清单被整体替换且无映射**（做法不同未记录）：计划要求原 16 条 + F 定点 12 条按指定杀手执行，实际跑的是自选 22 条。约 15 条计划变异没有对应——其中 F-M05『旧 judge 直接放池』正对应对照文档发现的 `judge_mission` 旧尾巴，F-M09～11 是 closeout/pin 的 SQL 守卫，M16『未知用量记 0 释放』对应『多算不少算』，这些保护目前没有变异证据。
3. **legacy/全量 H1 回归没跑完**（验收没做完）：SDK 全量回归证据停在 89% 无汇总（WIP 记 p33 多进程用例挂死），Host 26 个既有失败未处置；对照文档把它算成『默认 ON 后最终回归 验收已做』。
4. **stateful 规模从 200×50 降到 6×14**（做法不同未记录）：只记了『不用 hypothesis』，没记规模缩减。
5. 四条小的做法差异（做法不同未记录）：commit_receipt 引用只核 mission 不核写者（L63）；CheckSpec 每 Mission 懒注册而非安装时登记（L69）；completion_scope 通用 resolver 直读表而非原 OCC reader（L77）；review binding 的 `round_no` 恒为 1、轮次上限靠原 Task 预算（L176/L179）。都不影响当前结论，补记录即可。

### 三、F01～F15 关闭判据

| F | 关闭判据（RESPONSE-TO-REVIEW 原文要点） | 现在成立吗 | 依据 |
|---|---|---|---|
| F01 | 每 purpose 唯一生产/消费 owner；AS-0 核实际 source；cut 与 ensure 不双发；只有 AST 不证明接线 | 基本成立，有残留 | 六类挂钩见对照文档 6.7；`SDK/orchestrator/root_review.py:831` cut 只切包。残留：`commit_service.py:3136-3160` `judge_mission` 非保证尾巴直接写 COMPLETED（工厂只建保证通道，生产不可达），`:3087-3092` `network is None` 的 legacy 分支；BODY_WIRED 16 边从未逐条关闭（『只有 AST 不证明接线』这一半没有正式证据） |
| F02 | 六 purpose 结构可编码，每字段有 resolver/body/revision/归属；错 kind/body/collector、已结算 reserve 用于新调用被拒 | 部分成立 | `SDK/storage/assurance_reads.py:111-128` 17 类 + 事件桥 + artifact/source 有精确 resolver；`tool_receipt/policy/authority/capability` 四类 `REF_KIND_UNSUPPORTED`（对照文档 3.6）；commit_receipt 不核写者（本文 L63）；`assurance_review_transport.py:393-401` 已结算预留拒绝复用成立 |
| F03 | 标签可逆定位精确引用；追加不重写旧输入；不以目录当披露 | 成立 | `SDK/assurance/evidence.py:15,34`；`assurance_review_import.py:261,381`；`verification/reviewer_evidence_tools.py:419` |
| F04 | 真值表与纯 oracle 一致；旧 required checks 不丢；UNKNOWN 不降级 | 成立（一处小缺） | `SDK/assurance/checks.py:428` `decide_review`；`assurance_check_policy.py:241-258` 必需检查不丢；缺『全局安全 finding 必须挂 mandatory』（对照文档 5.12）。F-M02/F-M03 变异未按计划执行（AM04 近似覆盖 F-M02） |
| F05 | 新 profile 旧入口均 guard；同库原子、跨库导入分开；反例：重复通知重预留、第二轮撞 unique subject、UNKNOWN 当格式重试 | 部分成立（有已记录偏离） | guard 与同库原子成立（`htn_store.py:957-966`、`transport.py:275`）。F05 具名反例『UNKNOWN 当格式重试』现在正是生产做法：调用没回来→`abandon_assurance_review`（`assurance_review_collect.py:219`）记 TURN_FAILED 后发第二次调用；有阶段 B 裁决记录（`HTN补齐-阶段B-完成评估.md`），属有决定的偏离，但判据原文不再成立 |
| F06 | 任一终态入口不绕 closeout；原事件/receipt/通知意图同事务；反例：直调旧 judge 释放 UNKNOWN hold、root 保存后进程退出 | 生产路径成立，代码未清 | `assurance_final_writer.py:125` 唯一终态写方；`judge_mission` 保证分支只请求 closeout。旧尾巴生产不可达但仍在；对应变异 F-M05 从未执行；`T/full_target/test_terminal_unknown_release.py:59` 只枚举 fail/cancel/stop 写点，不含 COMPLETED |
| F07 | 无当前权威只有非披露诊断；新 read grant 不复活旧执行 | 不适用（已删，有决定） | 受管恢复整套删（补齐计划阶段 A″）；隔离时执行一律拒（`root_gate.py` `require_execution`）；非披露诊断 `api/facade.py:125` 只在测试被调用 |
| F08 | 写时 barrier、使用时证书复核、有界重算、无假连续监测 | 成立 | `storage/assurance_barrier_v26.sql` 触发器屏障；`assurance_validity.py:686` `require_current_locked`；`knowledge/bounded_closure.py:37-47`；MAINTAIN 无用途、也没有假监测 |
| F09 | 相关事件不丢、不重复实质效果、自己状态事件不循环 | 成立 | `storage/assurance_work.py:157,300` ingest/commit；`assurance_consumers.py:76` IGNORE 自身事件；C04 用例在 `test_c_assurance.py` |
| F10 | 旧活动 Mission 不静默换；默认 ON 不解除 TaskGraph 未完门 | 成立（按用户决定） | `assurance_factory.py:166-180` 非 ASSURANCE_1_1 直接拒，不静默换；TaskGraph 独立开关按 A′ 决定删除 |
| F11 | 初始 FINALIZED、DELETE/REPLACE 后倒退、无 review BOUND 及 REPLACE/跨 Mission 变体被 SQLite 拒绝 | 成立（变异证据缺） | `storage/assurance_schema.sql:146-155,363-376,407-409`（含『BOUND pin lacks same-mission review』）；C 组真实迁移/触发器用例；计划要求的 F-M09/10/11 变异没跑 |
| F12 | 变更必被一致性/语义测试检测；限制先到明确失败、不截断 COMPLETE | 不成立 | 后半成立（具名 limit，`codec.py`、`evidence.py` READSET_LIMIT）；前半不成立：`check_plan.py` 从未对 SDK 实际 DDL/schema 运行，8 份内部 schema 未随包，`sql-column-producers.json` 未随 pending_work 加列更新，F-M12 变异未跑 |
| F13 | 全 request/response/error 与各页面状态清楚；runtime 证据和点击另验 | 部分成立 | 接口与错误合同成立（`api/assurance.py`、`T/full_target/assurance_exec/test_c07_host_api.py`、`Host/assurance.py:34-52` 指纹）；点击只覆盖主流程，审阅详情/历史切换/断线重连/撤回没点 |
| F14 | BODY_WIRED 按实际边；DoD 更新 ARCHITECTURE/PROJECT_STATUS 与相对证据索引 | 不成立 | `plans/assurance-1.1/BODY-WIRED.md` 16 边无关闭记录（BW14 已随 A″ 作废未更新）；`ARCHITECTURE/ASSURANCE.md` 停在 10-02 |
| F15 | 66/OCC12 全部可追踪；12 预登记 trial 按 oracle 判定；独立代码审查另列 | 不成立 | 继承 X 组 18 条 NOT_REMAPPED；A′ 后约 39 处用例名失效；M01–M04×3 未跑、无 runner；独立审查只有 09-25 一次 |

### 四、对照文档里的误读

| # | 原计划原文 | 对照文档写法 | 正确理解 |
|---|---|---|---|
| 1 | §14 L390『16+新增定点mutation』；TEST-MAP L7『16 原 SDK mutations＋12 项 F 定点变异分别保留』，各条有 must_be_killed_by | §15『定点变异 16+ 验收已做（22/22 抓到）』，只注『A′ 后过期』 | 计划要的是指定的 28 条按指定杀手执行；实际 22 条是另选的，约 15 条计划变异没有对应。应判『做法不同未记录 / 验收部分』，不是『验收已做』 |
| 2 | §1 I4、C.1 L488/L496：证书守的是 accept/disclose/handoff 与四类读者（Input/Context、下载/summary、Acceptance/GoalResolution、handoff） | 7.15 把『做法审阅、操作提案审阅的结论被消费时要核使用证书』当成计划要求，并列为影响最大的差距 | 原计划没有把『做法准入』『操作物化』列为证书读者；ACTION_PROPOSAL 往下的 handoff 另有核对（对照文档 C1.18a 已写）。这条是合理的风险观察，但不是『偏离原计划』，状态应写成『计划外风险』而非『做法不同未记录』 |
| 3 | C.3 L512『Usage 导入始终使用原调用的冻结计价与身份，不因 review 拒绝/源码更换/subject 失效改写过去费用』 | C3.5 判『用户定不做』 | 被删的只是『计价（金额）』部分；『不改写过去用量、沿用原身份』这一核心要求仍在用。应判『在用（计价部分随金额删）』 |
| 4 | §0 L15『既有活动 Mission 不静默换协议』 | 0.4 判『已删（有决定）』 | 要求本身仍满足：旧任务直接拒绝运行（`assurance_factory.py:166-180`），没有静默换协议；被删的是 legacy lane 继续运行的能力。应判『在用（以拒绝方式满足）』 |
| 5 | §10.2 L299『授权默认 24h 或当前政策更短，精确读取允许集合』——指恢复后重新授权读取的 grant | §10『读授权默认 24h…在用』，依据 `FixedPrincipalAuthority.DEFAULT_READ_TTL_MS` | 那是另一套『当前固定身份权威』的 TTL；计划这条随 reauthorize 一起删了。应判『已删（有决定）』，另注有同值 TTL。现在把它算成『在用』抬高了在用数 |
| 6 | §6.1 L150『kind = "plan"（沿当前 service dispatch 类型）』 | 6.3 判『做法不同已记录』（TASK_CONTENT 用 critic） | 括号里的『沿当前 dispatch 类型』本就允许按原类型；TASK_CONTENT 接原 Critic 用 critic 类型可视为符合，不必算偏离（小） |
| 7 | §7.1 L195『AS-0 枚举所有 terminal 写入点，测试证明无新 profile 旁路』 | 7.9『代码层面没发现旁路；但「枚举 + 专门测试」没看到』，判在用 | 有专门测试 `T/full_target/test_terminal_unknown_release.py:59`，但只枚举 fail/cancel/stop 四个写点，不含 COMPLETED（正是 judge 旧尾巴）。应判『部分做到：失败类已枚举，完成类未枚举』 |
| 8 | C.2 L500 政策批准来源 | C.2 第 2 行说明『09-25 挑战指出它记成 actor_type=human』 | 已过期：`assurance_check_policy.py:296-303` 现在对 HOST_LOSSLESS_AUTO 写 `actor_type="system"`、`actor_id=HOST_AUTO_APPROVER_ID`，这一问题已修 |
| 9 | §14 L390 AS-VERIFY『legacy/fullH1已批准回归』单列一步 | 只有『默认 ON 后最终回归 验收已做（26 个失败与开启前相同）』 | 『与开启前相同』只证明默认 ON 没新增失败；legacy/full H1 回归本身没跑完（SDK 全量挂死、26 个既有失败未处置）。应单列为『验收没做完』 |
| 10 | TEST-MAP L8『状态化 200×50』 | §15『stateful 验收已做（固定种子，不用 Hypothesis）』 | 规模只有 6×14，未记录缩减。应判『做法不同未记录』 |

### 附：要求清单全表（314 条）

| # | 原文行号 | 要求 | 对照文档对应行 |
|---|---|---|---|
| 1 | L7 | 冲突以本正文为准；OCC 完成范围、Operation 身份、D3 原 raw 恢复语义不变 | **漏核** → 见第二节 |
| 2 | L9 | 迁移号取本地下一空闲号并冻结，不占 TaskGraph 的 23 | 0.2 |
| 3 | L11 | 复用现有 scoped/root/composition/operation 链，不另建平行主链 | 0.1 |
| 4 | L15 | 完整交付后新 Mission 默认 ON，不再等操作人批准 | 0.3 |
| 5 | L15 | 既有活动 Mission 不静默换协议 | 0.4 |
| 6 | L15 | 默认 ON 不解除 TaskGraph 自身门禁 | §11『TaskGraph 独立开关』 |
| 7 | L15 | 应用（审阅）模型从当前已批准 profile 读取，不改用户 DeepSeek 配置 | **漏核** → 见第二节 |
| 8 | L15 | 真实模型未实跑不填 PASS | §15.1 状态标签行 |
| 9 | L19 | I1 不改旧 V1 合同，新增走旁挂绑定 | I1 |
| 10 | L20 | I2 六 purpose 不增、原派发/预算、不建第二 scheduler | I2 |
| 11 | L21 | I3 效果只经原 OperationCompletionReader | I3 |
| 12 | L22 | I4 所有正式 Review/接受/终态/源 mutation 被接管 | I4 |
| 13 | L23 | I5 缺数据具名失败，合法空读有完整证书 | I5 |
| 14 | L24 | I6 raw/费用/历史不丢，UNKNOWN 不升 PASS | I6 |
| 15 | L25 | I7 本机撤回立即使旧证书无效 | I7 |
| 16 | L26 | I8 恢复新隔离根+新授权 | I8 |
| 17 | L38 | TASK_CONTENT：投影先于正式审阅构包；消费=accept_review+OCC contribution | 2.1 |
| 18 | L38 | TASK_CONTENT：已有受信 Critic 经绑定适配，不重问同一审阅 | **漏核** → 见第二节 |
| 19 | L39 | METHOD_PLAN：草案建原 ReviewPackage，原准入 writer 消费 | 2.2 |
| 20 | L39 | METHOD_PLAN：结构检查不伪装语义 Review；只准入方法、不完成 Task | **漏核** → 见第二节 |
| 21 | L40 | COMPOSITION：先组合后接受 | 2.3 |
| 22 | L41 | ACTION_PROPOSAL：正式 Review 决定能否进 T1 | 2.4 |
| 23 | L41 | ACTION_PROPOSAL：审阅通过不代替用户审批 | **漏核** → 见第二节 |
| 24 | L42 | OPERATION_OUTCOME：先读完整效果链；唯一效果 writer | 2.5 |
| 25 | L43 | MISSION_FINAL：每 round 独立 package | **漏核** → 见第二节 |
| 26 | L45 | cut 委托统一 builder，不能 cut 发一次 ensure 再发一次 | 2.6 |
| 27 | L45 | record_review 需已验证 OfficialReviewSource，裸 verdict 拒绝 | 2.7 |
| 28 | L57 | 内部 AssuranceRef 形状，不改公开 TypedRef | 3.1 |
| 29 | L57 | 通用种类唯一源 common.schema.json | §12.1 第 1 行 |
| 30 | L57 | 字段只允许 ref-resolution-map 子集，禁反射式 resolver | 3.3 |
| 31 | L59 | adapter 返回 ResolvedRef(...) 或 SourceUnavailable | 3.2 |
| 32 | L59 | 归属以真实查询得到，不信调用参数 | 3.4 |
| 33 | L59 | 过期拒绝用于新动作，但历史 raw/成本可导入 | **漏核** → 见第二节 |
| 34 | L59 | 未知 kind/缺失/同 ID 异内容/跨 Mission/错 collector 各自拒绝码 | 3.4 |
| 35 | L63 | commit_receipt：不可变 body、revision=0、真实 Commit writer 签发 | **漏核** → 见第二节 |
| 36 | L64 | reservation_fact：AssuranceReservationLinked 含原 reserve 身份/账户/subject/上限/原 receipt，同事务；是桥不是余额 | **漏核** → 见第二节 |
| 37 | L65 | agent_turn_receipt：核 profile/intent/agent/turn/hash 后导入，含原 runtime 回执引用 | C3.3 |
| 38 | L66 | execution_receipt：受信 importer 以 provider/call 身份与实际结果核对；review/read 不跨库原子写 | **漏核** → 见第二节 |
| 39 | L67 | local_check_receipt：实际执行后导入，不可从 API 提交 bool | 5.5 / C1.6 |
| 40 | L68 | check_binding：writer 只接两种真实检查来源 | C1.6 |
| 41 | L69 | check_spec：不可变注册事件，registry 安装时由固定系统 owner 写入 | **漏核** → 见第二节 |
| 42 | L70 | check_policy：引用精确 requirements revision 与 scope | **漏核** → 见第二节 |
| 43 | L71 | disclosure_receipt：由 Provider 输入 manifest importer 写，先于结论 | 4.4 / 4.5 |
| 44 | L72 | review_package：原六 purpose builder 产、Mission/round/purpose 精确匹配、revision=0 | **漏核** → 见第二节 |
| 45 | L73 | result：取不可变 envelope，不取可变 verification 外壳 | 3.5（说明） |
| 46 | L74 | task：不用心跳 row version | 3.5（说明） |
| 47 | L74 | method_instance：读明确 instance 与 plan pin，不查最后一个 | **漏核** → 见第二节 |
| 48 | L75 | artifact/input_manifest：完整字节、nofollow/hash 读取，不把 URI 当权限 | **漏核** → 见第二节 |
| 49 | L76 | source/observation/review/acceptance/resolution：精确 body，另查当前可用性，不改历史 body | **漏核** → 见第二节 |
| 50 | L77 | completion_scope/spec：归属、requirement hash、plan occurrence 用原 OCC reader | **漏核** → 见第二节 |
| 51 | L78 | operation/tool_receipt：只读核对，不签新 operation id | 3.6 |
| 52 | L79 | policy/authority/capability：静态声明不作结果证据 | 3.6 |
| 53 | L81 | 事件 Ref hash 覆盖原 Event 全体，不剔字段 | 3.7 |
| 54 | L81 | 同来源同 body 重送幂等，异 body 拒绝 | 3.7 |
| 55 | L85 | 创建顺序 pin→同事务 Package+reserve+intent→ReservationLinked→invocation→ensure receipt | 3.8 |
| 56 | L85 | Binding 不引用自身 hash 或未来 official record | C3.1～C3.4 |
| 57 | L87 | 收包：先存 raw/费用→TurnImported→official→side binding | 3.9 |
| 58 | L87 | 已完成调用的 reservation 可已 SETTLED，导入历史 review/费用不要求 ACTIVE | **漏核** → 见第二节 |
| 59 | L87 | 只有新 invocation 需新真实预留，不复用已结算 | 3.10 |
| 60 | L87 | 新 invocation 不新建预算账户，累计同 owner/Obligation | 6.5 |
| 61 | L94 | 标签算法 ev-+sha256(kind,review_key,ref) | 4.1 |
| 62 | L97 | 完整 64 hex；同 ID 异 kind/rev/hash 不同标签 | 4.1 |
| 63 | L97 | 同 label 异 ref 拒绝；字典序；catalogue hash 存 binding | 4.2 |
| 64 | L97 | 重复 exact ref 规范去重，标签不靠数组位置 | **漏核** → 见第二节 |
| 65 | L101 | 初始曝光：Provider input_hash 与冻结 catalogue 对齐，生成 batch 0 | 4.3 |
| 66 | L102 | 追加证据走两只读工具经 ToolGateway | 4.4 |
| 67 | L102 | 工具返回但未进模型输入不算曝光 | 4.4 |
| 68 | L103 | 同 turn 工具消息靠 Journal/消息 ID 关联，缺证明保持 UNEXPOSED | 4.5 |
| 69 | L104 | batch 固定字段；同号同体重放不新增，异体冲突 | 4.6 |
| 70 | L104 | 初始与追加同 exact ref 用同 label | 4.1（标签是纯函数） |
| 71 | L105 | 格式修复 invocation 新输入含目录/材料 | 4.7 |
| 72 | L105 | 旧 Agent 私有上下文不自动继承给新 Agent | **漏核** → 见第二节 |
| 73 | L106 | official 只认产生结论那一轮实收曝光集 | 4.8 |
| 74 | L106 | Replay 从 raw+binding+disclosure chain 恢复，不检索 latest、不改原 input_id/hash | **漏核** → 见第二节 |
| 75 | L108 | 追加 fetch 受真实工具预算、scope、只读边界 | **漏核** → 见第二节 |
| 76 | L108 | 根 reviewer 新模板显式装两工具，非万能工具 | 4.9 |
| 77 | L114 | approve_assurance_check_policy 唯一批准 writer | 5.1 |
| 78 | L114 | 在原 Requirements 批准事务中写 | 5.2 |
| 79 | L114 | 政策域绑定 Requirements hash+scope hash；一份批准不能被后来 builder 改 mode | **漏核** → 见第二节 |
| 80 | L116 | 无损适配：有 required_check_ids→CHECKED；无→仅语义才 SEMANTIC；其他 UNRESOLVED | 5.3 |
| 81 | L116 | 只有批准的替代组才能 OR | 5.4 |
| 82 | L116 | TASK_CONTENT 用现有 scoped projection 的 criteria/证据政策，不因旧 LayerResult 无 execution_ref 永久拒绝 | **漏核** → 见第二节 |
| 83 | L120 | 本地 format/rule 外包 recorder，事务外执行，导入原 Commit | 5.5 |
| 84 | L120 | 运行前固定 checker digest、subject/input/env、run identity；逐 assertion 输出 hash | **漏核** → 见第二节 |
| 85 | L120 | 抛错记 ERROR；单 assertion_key；缺断言 UNKNOWN；重复拒绝；不捏造输出 | 5.6 |
| 86 | L122 | citation 只证明指定段落 | 5.7（已删有决定） |
| 87 | L122 | code_test 回执含 nodeid/平台/环境/目标；exit0 不单独代表正确 | 5.8 |
| 88 | L122 | code_test 已经 executor 跑过则用其回执，不重复本地执行 | 5.8 |
| 89 | L122 | 断电前未持久的检查结果仍 UNKNOWN；无副作用可重跑；不从 cache 猜 PASS | **漏核** → 见第二节 |
| 90 | L126 | 执行记录真值表：SUCCEEDED×PASS/FAIL/UNKNOWN；ERROR/CANCELLED/NOT_RUN/RUNNING/缺失/过期/错范围→UNKNOWN；畸形拒绝导入 | **漏核** → 见第二节 |
| 91 | L128 | CHECKED 组内 AND 组间 OR | 5.13 |
| 92 | L128 | SEMANTIC 检查门 NOT_APPLICABLE，不伪造 PASS | 5.10 |
| 93 | L130 | 模型等级×检查门三值表 | 5.9 |
| 94 | L136 | BLOCKER 强制该准则 FAIL；mandatory 单独 ALL | 5.11 |
| 95 | L136 | 全局安全 finding 映射 mandatory，否则拒整条回复 | 5.12 |
| 96 | L136 | ALL/ANY 三值；success witness 按冻结顺序 | 5.13 |
| 97 | L138 | decide_review 返回字段 | 5.9 |
| 98 | L138 | 模型 verdict 非 ACCEPT 不因公式通过自动批准 | 5.14 |
| 99 | L138 | 失败组反证仍在完整候选/有效性中核对；权限/hash/Scope 硬门不受 ANY 绕过 | **漏核** → 见第二节 |
| 100 | L144 | 一个 review_key→一个 Package→至多两次 invocation | 6.1 |
| 101 | L144 | 格式修复不是新 round；独立二审须新 round/package | 6.14 |
| 102 | L144 | 旧 review_records 每包唯一 official 约束保留 | **漏核** → 见第二节 |
| 103 | L147 | subject_id/creation_key/input_id 格式 | 6.2 |
| 104 | L150 | kind='plan'（沿当前 dispatch 类型） | 6.3 |
| 105 | L151 | config.role + assurance 字段 | 6.4 |
| 106 | L154 | 经 create_service_intent，owner 依 account_for_purpose | 6.5 |
| 107 | L154 | reserve/intent/Package/binding/event/receipt 同一 Store 外层事务 | 6.6 |
| 108 | L154 | 原方法自开事务则提取 _create_service_intent_locked | 3.11 |
| 109 | L154 | 不声称跨 execution.db 原子；真实 Agent 由原 dispatch consumer 执行，call 账本后导入 | **漏核** → 见第二节 |
| 110 | L156 | 六类切换表 | 6.7 |
| 111 | L165 | 新 profile 不接裸 verdict；accept/record 路径验证 binding+TurnImported | 6.8 |
| 112 | L165 | legacy 按持久 lane 分派 | 6.9 |
| 113 | L171 | 相同通知/command 重送回原 receipt | 6.10 |
| 114 | L172 | 首次合法请求：新 round ordinal1 真预留后派发 | 3.8 / 6.1 |
| 115 | L173 | 可解析 REWORK/INCONCLUSIVE 不自动 ordinal2 | 6.11 |
| 116 | L174 | 不可解析：同包 ordinal2+新真实预留，旧费用仍算 | 6.12 |
| 117 | L175 | Provider UNKNOWN 不能靠 ordinal2 重复请求 | 6.13 |
| 118 | L176 | 获准二审/内容返工：新 round/package | 6.14 |
| 119 | L176/L179 | round 上限用原冻结责任政策，同责任累计预算 | **漏核** → 见第二节 |
| 120 | L177 | 另一 invocation 晚到：保存 raw/费用，不占 official | 6.15 |
| 121 | L179 | 格式修复上限 1 次 | 6.12 |
| 122 | L179 | binding 不含 dispatch 字段，另建 invocations 表；collector 按实际 invocation 关联 | 6.16 |
| 123 | L187-197 | accept_result 原样保存，内容接受经原路径+guard | 7.1 |
| 124 | L187-197 | accept_review：OCC+official+typed checks+证书同事务 | 7.2 |
| 125 | L187-197 | commit_goal_resolution 要求完整 bundle，直调绕不过 | 7.3 |
| 126 | L187-197 | completion_* 按用途读；MIXED 未满足效果不被内容接受顶替 | 7.4 |
| 127 | L187-197 | record_review 等由统一 collector 调用 | 7.5 |
| 128 | L187-197 | DeliveryReceipt 沿 T3 精确绑定 | 7.6 |
| 129 | L187-197 | judge_mission 新 profile 只请求 closeout，不直接 COMPLETED/不提前 release | 7.7 |
| 130 | L187-197 | cancel/fail/timeout 保留 UNKNOWN/hold | 7.8 |
| 131 | L187-197 | update_task/update_mission 只合法 caller；枚举并测试无旁路 | 7.9 |
| 132 | L187-197 | 唯一最终 writer `_finalize_assured_mission_locked`，同事务、不公开 | 7.10 |
| 133 | L201 | root 满足后 Mission ACTIVE、closeout DRAINING/BLOCKED_UNKNOWN、调度不开 Worker 不误判 FAILED | 7.11 |
| 134 | L203 | 默认成功策略四档 | 7.12 |
| 135 | L203 | KEEP_HOLD_PENDING 只在政策允许；不新增免费 | 7.13 |
| 136 | L205 | closeout NOT_READY/v1→READY→FINALIZED；不回退、不删重建 | 7.14 |
| 137 | L209 | 原 Event+NOTIFY 行；最后事务同写 MissionCompleted/通知请求/receipt | 7.16 |
| 138 | L209 | 推送 {mission_id,event_id,state_version}；Host event_id 去重、重连补读 | 7.17 |
| 139 | L211 | 发送前检查读权/恢复门 | 7.18 |
| 140 | L211 | 用户已读无回执即 UNKNOWN；外部交付走原 Operation，本地通知不完成外部效果 | **漏核** → 见第二节 |
| 141 | L217 | validity_epochs 保留；Mission 聚合分区由 factory 建；无行不当 0 | 8.1 |
| 142 | L217 | environment_state 只存全局 epoch/时钟 | 8.2 |
| 143 | L221 | 屏障：put_source/替代/撤回/tombstone；归属不明或跨 Mission 共享→global | **漏核** → 见第二节 |
| 144 | L222 | 屏障：insert_observation 幂等首次插入才 bump，正反证同等 | **漏核** → 见第二节 |
| 145 | L223 | 屏障：justification/support member/rule 准入与撤回同事务 | **漏核** → 见第二节 |
| 146 | L224 | 屏障：Requirements、OCC Spec/Scope 正式变化 | **漏核** → 见第二节 |
| 147 | L225 | 屏障：Method/Task adopted input 或 contract 变更，与 PlanCommit 同事务 | **漏核** → 见第二节 |
| 148 | L227 | 屏障：operation 更正/反向证据、execution 结果 import | **漏核** → 见第二节 |
| 149 | L228 | 屏障：artifact 删除/访问状态、Check/Review 有效性更正 | **漏核** → 见第二节 |
| 150 | L229 | 屏障：scope/grant 撤回 | **漏核** → 见第二节 |
| 151 | L226 | 屏障：ACL/当前 policy/permission → global epoch | 8.4 |
| 152 | L231 | 下层 mutation 处同事务 `_mark_assurance_change_locked` | 8.3 |
| 153 | L231 | 所有直接 SQL writer 扫描登记 owner；禁无审阅生产 SQL 直写 | 8.5 |
| 154 | L235 | CompleteRead 内容；缺表/没读完不能 COMPLETE；空集 count0 | 8.6 |
| 155 | L237 | 命题键 canonical(predicate,typed_args,namespace,scope) | 8.7 |
| 156 | L237 | read_set (channel,key) 唯一，同 fingerprint 重复也拒 | 8.8 |
| 157 | L239-242 | OBJECT/QUERY_SET/ACCESS/POLICY 四种 key | 8.9 |
| 158 | L244 | 按 channel/key 排序 hash，拒未知通道；reason_refs 不是读集 | 8.8 |
| 159 | L248 | 原正负支持最小不动点+clean closure，不从 VERIFIED 缓存开始 | 8.10b |
| 160 | L248 | 10k/20k/1M/250ms 上限→INCOMPLETE，不截断 COMPLETE | 8.10 |
| 161 | L250 | 一致读→事务外计算→短写事务比较 epoch；变了 RECHECK_REQUIRED | 8.11 |
| 162 | L252 | 证书 256KiB、读集 20000、含 root incarnation | 8.12 |
| 163 | L256 | 半开区间；证书 expiry 取最早界 | 8.13 |
| 164 | L256 | MAINTAIN 需持续监测，缺则阻断 MAINTAIN | 8.14 |
| 165 | L258 | 唯一计时 owner VALIDITY worker；启动重建 | 8.15 |
| 166 | L258 | 证书即时使用总是检查 now，timer 延迟也不放行 | **漏核** → 见第二节 |
| 167 | L260 | 时钟高水位/ROLLBACK/代次/恢复 STABLE；修时不降高水位 | 8.16 |
| 168 | L260 | 时钟不可信期间拒绝时间敏感用途，raw/费用继续 | 8.17 |
| 169 | L260 | 超 lease 只回收协调 owner | C5.4 |
| 170 | L262 | RECHECK 退避与 MANUAL_REQUIRED | 8.18 |
| 171 | L262 | 新源事件唤醒同 logical work，累计预算保持 | C5.3 |
| 172 | L266 | 持久 pending inbox+原 tick，pending 不执行 Agent | 9.1 |
| 173 | L268 | target_epoch=触发 seq；合并/冲突 | 9.2 |
| 174 | L270 | 四 consumer 各自 cursor；cursor 只表示已入队 | 9.3 |
| 175 | L272-273 | 读页不推进 cursor；短事务入队+推进；禁先 ack | 9.4 |
| 176 | L274 | lease claim、事务外准备、撤权不被预算堵住 | 9.5 |
| 177 | L275 | 提交重读、效果+receipt+DONE 同事务、已有 receipt 也 ACK | 9.6 |
| 178 | L276 | WAITING/REJECTED 存原因 | 9.7 |
| 179 | L276 | profile 未绑定/恢复隔离：保存等待，不触发模型格式重试 | **漏核** → 见第二节 |
| 180 | L277 | 准备后来源变化旧 worker 不能 ACK，不重复预算 | 9.8 |
| 181 | L279 | 激活同事务 cursor+reconciliation manifest | 9.9 |
| 182 | L279 | cursor 丢失从 0 重建；源缺 SOURCE_UNAVAILABLE | 9.10 |
| 183 | L281 | self events IGNORE | 9.11 |
| 184 | L281 | 每 tick page≤128、每 consumer≤8 | 9.12 |
| 185 | L287 | 离线 restore 新目录/新 root id/quarantine marker | §10 第 1 行 |
| 186 | L289 | root gate 早于 API/搜索/summary/Context/通知/下载/调度 | §10 第 2 行 |
| 187 | L289 | marker 缺失/不匹配/部分库→QUARANTINED | §10 第 3 行 |
| 188 | L289 | 新空 root 由认证 create-root 初始化，非空未知不当新空库 | §10 第 4 行 |
| 189 | L293 | reauthorize_restored_read 入口链 | §10 第 5 行 |
| 190 | L295 | 外部 ACL 当前权威、READ_ONLY_REAUTHORIZED | §10 第 6 行 |
| 191 | L297 | 先库 receipt 再原子写根状态文件，崩溃隔离、幂等补 | §10 第 7 行 |
| 192 | L297 | 备份导出排除可用根 grant | §10 第 1 行（备份整体删） |
| 193 | L299 | 授权默认 24h、精确集合 | §10 第 8 行 |
| 194 | L299 | 只授权披露，不复活旧执行/写工具/旧审批 | §10 第 10 行 |
| 195 | L303 | QUARANTINED 只给非披露诊断 | §10 第 11 行 |
| 196 | L307 | creation_contracts 不可变判别 | §11 第 1 行 |
| 197 | L307 | 新 lane 同事务 activation/binding/epoch/cursors，缺一报错不 fallback | §11 第 2 行 |
| 198 | L311-312 | LEGACY/COMPLETION_V1 保持原行为 | §11 第 3 行 |
| 199 | L314 | 缺分类/来源矛盾→CREATION_CONTRACT_UNRESOLVED | §11 第 4 行 |
| 200 | L316 | 升级只按迁移前事实分类、不授新权 | §11 第 5 行 |
| 201 | L316 | 明确后继才用新 profile | §11 第 6 行 |
| 202 | L318 | 默认选择点集中在 factory 函数 | §11 第 7 行 |
| 203 | L318 | 完整验证后同批默认 ON | §11 第 8 行 |
| 204 | L318 | 风险动作原用户审批继续有效 | §11 第 9 行 |
| 205 | L318 | TaskGraph 独立开关继续有效 | §11 第 10 行 |
| 206 | L322 | 1.1 增量目标；1.0 校验和保留；取下一空闲号 | §12 第 7 行 |
| 207 | L324 | 9 原 + 6 新窄 side tables，无另一套任务/真假/余额 | §12 第 1 行 |
| 208 | L326 | closeout 初始 NOT_READY/v1、只能 READY→FINALIZED、禁删禁替换 | §12 第 2 行 |
| 209 | L326 | pin 初始 PREPARING、BOUND 需同 Mission review、RELEASED 不重开、不可变表、队列单调 | §12 第 3 行 |
| 210 | L328 | Store 负责 hash/issuer/blob/当前授权，不推给 DB | §12 第 4 行 |
| 211 | L330 | 物理 SQLite backup 还原含终态行 | §12 第 5 行 |
| 212 | L330 | 空库重放不 DROP trigger，历史不足 INCOMPLETE | §12 第 6 行 |
| 213 | L334 | check_plan.py 递归核字段/ref/fixture/DDL 列映射 | §12.1 第 2 行 |
| 214 | L336 | 严格 JSON（256KiB、重复键、NaN 等、深度 64） | §12.1 第 3、4 行 |
| 215 | L336 | formula 深度 16/512 节点/256 准则；set 排序、分支保序 | §12.1 第 5 行 |
| 216 | L336 | 长 raw 由 CAS 保存，拒绝解释后不丢费用 | I6 |
| 217 | L338 | CheckPolicy 结构规则 | §12.1 第 6 行 |
| 218 | L338 | authors 非空；METHOD_PLAN 无作者 SOURCE_UNAVAILABLE；reason≤2000 | §12.1 第 7 行 |
| 219 | L346-348 | 三个下划线 verb 薄委托 | §13.1 第 1 行 |
| 220 | L350 | 请求无 tenant/principal，caller 固定 | §13.1 第 2 行 |
| 221 | L350 | ≤100 项；CURRENT 同 seq；HISTORY 带 at_seq 但按当前权限 | §13.1 第 3 行 |
| 222 | L350 | 返回 hash/body 字段不带 secret | **漏核** → 见第二节 |
| 223 | L352 | cursor 编码、(kind,id) 排序、SNAPSHOT_CHANGED、history 钉 seq、truncated | §13.1 第 4 行 |
| 224 | L354 | projection 只映射正式状态 | §13.1 第 7 行 |
| 225 | L354 | MissionsView 显示五项；非法 DTO 保留上次画面；不另开 WebSocket | §13.1 第 8 行 |
| 226 | L358-362 | 隔离 Host 副本+独立 venv/端口/userdata+候选 wheel+prepare_native_manifest | §13.2 第 1 行 |
| 227 | L364 | 启动记录 `__file__`/wheel/模块 sha/指纹，字节不同不混用 | §13.2 第 2 行 |
| 228 | L368 | 实际点击七步场景 | §13.3 第 1 行 |
| 229 | L368 | 多用户/跨 tenant 负例接口验证 | §13.3 第 2 行 |
| 230 | L368 | UI/接口不因是否找到 ID 泄露对象存在 | **漏核** → 见第二节 |
| 231 | L374 | AS-0 合同/来源固定 | §14 第 1 行 |
| 232 | L378-382 | AS-BODY 1～5 主体实现 | §14 第 2 行 |
| 233 | L386 | BODY_WIRED 按生产调用边确认 | §14 第 3 行 |
| 234 | L390 | AS-VERIFY 集中验收顺序 | §14 第 4 行 |
| 235 | L390 | AS-VERIFY：SQL 迁移/竞争/强退 | **漏核** → 见第二节 |
| 236 | L390 | AS-VERIFY：legacy/full H1 已批准回归 | **漏核** → 见第二节 |
| 237 | L390 | AS-VERIFY：16+ 定点 mutation | §15『定点变异』行（见第四节误读） |
| 238 | L390 | AS-VERIFY：stateful | §15『stateful』行（见第四节误读） |
| 239 | L390 | AS-VERIFY：独立代码审查 | §15.1 第 3 行 |
| 240 | L392 | 缺依赖不自动装新 runtime 依赖、不改断言跳过 | （WIP 第十段（二）记装了 jsonschema 到 venv、未改 pyproject——已记录） |
| 241 | L396 | 48 组保留 ID；C08 只做离线 late accounting | §15 第 1 行 |
| 242 | L396 | 继承 66 逐条 owner/断言/target_nodeid（48 条） | §15 第 2 行 |
| 243 | L396 | 继承 66 中 X 组 18 条 | §15 第 3 行 |
| 244 | L396 | OCC 12 逐条 | §15 第 4 行 |
| 245 | L396-398 | 4 场景×3 单列 runner，真实传输不 stub，connector 为真实本地服务 | §15 第 7 行 |
| 246 | L400 | 每 trial 预算 300k/16 调用/32 工具/900s；INVALID_ENV 不减分母；读当前批准 profile 存 hash | §15 第 8 行 |
| 247 | L402 | 通过门 12/12、M03 total=232、M04 效果计数=1、每局值+median | §15 第 7 行 |
| 248 | L408 | 同交付更新四份 ARCHITECTURE 文档 | §15.1 第 1 行 |
| 249 | L408 | 记真实入口/版本/日期/未完成/相对证据索引；WIP 与验收分开 | §15.1 第 2 行 |
| 250 | L410 | SPEC_RESOLVED/BODY_WIRED/SDK_VERIFIED/... 状态标签分记 | §15.1 第 3 行 |
| 251 | L412 | 独立审阅须实际独立；没工具标 NOT_RUN | §15.1 第 4 行 |
| 252 | L410 | 默认 ON 后最终回归 | §15.1 第 5 行 |
| 253 | L416 | 候选 venv/uv frozen；证据目录 ignored 且新 run 不覆盖 | §16 第 1 行 |
| 254 | L429-433 | verify_delivery/check_plan/capture_identity 生成 source-map | §16 第 2 行 |
| 255 | L436-442 | BODY_WIRED 后跑 assurance_exec 留 junit | §16 第 3 行 |
| 256 | L444 | gate-commands.local.json 登记遗留 nodeid/变异/H1 argv | §16 第 4 行 |
| 257 | L444 | 真实模型 runner `tools/run_assurance_model_scenarios.py` | §15 第 7 行 |
| 258 | L450 | integration-map.json 单 owner 映射 | 附录 A 第 1 行 |
| 259 | L451-453 | ref-resolution-map/field-producers/sql-column-producers | 附录 A 第 2 行 |
| 260 | L454 | contracts/*.schema.json 活动结构 | 附录 A 第 3 行 |
| 261 | L455 | sql/assurance_additive.sql+queries.sql | 附录 A 第 4 行 |
| 262 | L456 | reference/protocol_v11.py、semantics.py | 附录 A 第 5 行 |
| 263 | L458 | model-scenarios.json 接真实 runtime | 附录 A 第 6 行 |
| 264 | L459 | inherited/occ coverage 逐条 | 附录 A 第 7 行 |
| 265 | L457（+TEST-MAP L5、findings-sdk-tests.json） | F01–F15 决定性反例落到 SDK（15 个 target nodeid `test_review_findings.py::*`） | **漏核** → 见第二节 |
| 266 | TEST-MAP L7（mutations.json 16 + additional-mutations.json 12） | 原 16 条 SDK 变异 + 12 条 F 定点变异，按指定 must_be_killed_by 执行，import/语法错不算 KILLED | **漏核** → 见第二节 |
| 267 | TEST-MAP L8 | stateful 200×50（状态化随机序列规模） | **漏核** → 见第二节 |
| 268 | L478 | C.1 接口 `bind_assurance_profile_locked` 及其唯一 caller | C1.1 |
| 269 | L479 | C.1 接口 `approve_assurance_check_policy` 及其唯一 caller | C1.2 |
| 270 | L480 | C.1 接口 `ensure_assurance_review` 及其唯一 caller | C1.3 |
| 271 | L481 | C.1 接口 `ensure_format_repair_invocation` 及其唯一 caller | C1.4 |
| 272 | L482 | C.1 接口 `record_local_check_run` 及其唯一 caller | C1.5 |
| 273 | L483 | C.1 接口 `import_assurance_check_locked` 及其唯一 caller | C1.6 |
| 274 | L484 | C.1 接口 `import_reviewer_disclosure_locked` 及其唯一 caller | C1.7 |
| 275 | L485 | C.1 接口 `import_official_assurance_review` 及其唯一 caller | C1.8 |
| 276 | L486 | C.1 接口 `read_complete_evidence_snapshot` 及其唯一 caller | C1.9 |
| 277 | L487 | C.1 接口 `compute_assurance_use` 及其唯一 caller | C1.10 |
| 278 | L488 | C.1 接口 `commit_assurance_use_locked` 及其唯一 caller | C1.11 |
| 279 | L489 | C.1 接口 `accept_assured_review_locked` 及其唯一 caller | C1.12 |
| 280 | L490 | C.1 接口 `try_finalize_assured_mission` 及其唯一 caller | C1.13 |
| 281 | L491 | C.1 接口 `_finalize_assured_mission_locked` 及其唯一 caller | C1.14 |
| 282 | L492 | C.1 接口 `ingest_assurance_events_locked` 及其唯一 caller | C1.15 |
| 283 | L493 | C.1 接口 `commit_assurance_work_locked` 及其唯一 caller | C1.16 |
| 284 | L494 | C.1 接口 `reauthorize_restored_read` 及其唯一 caller | C1.17 |
| 285 | L474 | `*_locked` 用原 Store 事务；read/prepare/compute 不写业务表 | C1.20 |
| 286 | L496 | 四类读者（Input/Context、下载/summary、Acceptance/GoalResolution、handoff）不绕 read_use | C1.18a/b |
| 287 | L496 | use_check 只诊断，不返回可当许可的签名 | C1.19 |
| 288 | L500 | 建任务事务先 Mission/Requirements 再判别/激活/绑定/纪元/四游标 | C.2 第 1 行 |
| 289 | L500 | check policy 随后写，读者不要求未建 Review/Reservation | C.2 第 2 行 |
| 290 | L500 | 新空环境 global epoch/clock 由安装 receipt 产生 | C.2 第 3 行 |
| 291 | L502 | factory 回执不递归自身 hash；deferred FK | C.2 第 4 行 |
| 292 | L504 | 历史非空 root 首次安装经认证入口登记 | C.2 第 5 行 |
| 293 | L508 | C.3 第 1 条 | C3.1 |
| 294 | L509 | C.3 第 2 条 | C3.2 |
| 295 | L510 | C.3 第 3 条 | C3.3 |
| 296 | L511 | C.3 第 4 条 | C3.4 |
| 297 | L512 | C.3 第 5 条 | C3.5 |
| 298 | L516 | pin acquire/bind/release，唯一 writer AssuranceStore | C.4 第 1 行 |
| 299 | L516 | 先 PREPARING 再读 CAS，失败 release+SOURCE_UNAVAILABLE | C.4 第 2 行 |
| 300 | L516 | GC 删除门检查 pins；缺覆盖不删 | C.4 第 4 行（无 GC） |
| 301 | L516 | 没有 GC 时不新建定时 GC | C.4 第 4 行 |
| 302 | L516 | SQL 只保证 pin 身份/状态，不保证字节 | C.4 第 5 行 |
| 303 | L520-522 | C.5 第 1 条 | C5.1 |
| 304 | L520-522 | C.5 第 2 条 | C5.2 |
| 305 | L520-522 | C.5 第 3 条 | C5.3 |
| 306 | L520-522 | C.5 第 4 条 | C5.4 |
| 307 | L520-522 | C.5 第 5 条 | C5.5 |
| 308 | L520-522 | C.5 第 6 条 | C5.6 |
| 309 | L526 | history_state 固定 seq；current_use 按当前权限/根/有效性 | C.6 第 1 行 |
| 310 | L526 | review 响应字段；正文走获准 artifact 读 | C.6 第 2 行 |
| 311 | L526 | use_check diagnostic_only=true、certificate_ref=null | C.6 第 3 行 |
| 312 | L528 | 无完整历史投影→HISTORY_UNAVAILABLE | C.6 第 4 行 |
| 313 | L528 | current 翻页 epoch 变→SNAPSHOT_CHANGED | C.6 第 5 行 |
| 314 | L528 | 分页不作证据集合 COMPLETE 声明 | C.6 第 6 行 |


---

## 附四 · 接线资产逐行核

## Assurance 接线资产逐行复核（第二遍细核，2026-10-05）

**范围**：`plans/Assurance/specs/1.1/implementation/` 下 integration-map / seams / event-consumer-map / ref-resolution-map / BODY-WIRED / sdk-cases / occ-coverage / inherited-coverage / mutations / additional-mutations，逐行对现行代码。
**代码版本**：任务给的是 `67fe1f5c`；本机 main 实际 HEAD 为 `5bae3e92`（其后两次提交：资料换版本真机记录、合并对照文档，未动本次涉及的 SDK 源码与测试）。只读，没跑测试。
**路径简写**：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`；`WIP` = `sdk/simple-harness-sdk/plans/assurance-1.1/BODY-WORK-IN-PROGRESS.md`；`分诊表` = `plans/2026-09-27-desktop-next/HTN补齐-阶段A撇-分诊表.md`；`PV` = `T/full_target/operation_completion/test_publish_variants.py`。
**用例类口径**：计划包里的 sdk-cases/occ/inherited 只有计划名（actual 为空），SDK 回写版（`sdk/simple-harness-sdk/plans/assurance-1.1/*.json`，09-23 第十段回写）有实际用例名；本文按回写版的实际名逐个 grep `def 名字(`。
**生产调用链根**：`Host/service.py:205,217` 建 `UserMissionDeployment` → `SDK/deployment/assembly.py:90` `install_assurance(...)` → `SDK/orchestrator/assurance_assembly.py:349-470` 装四消费者、工厂、tick、读接口。下文"在用"都能沿这条链 grep 到生产调用点。

### 1. integration-map.json · seams（26 行；与 seams.json 完全相同，合并核一次）

`python json.load(seams.json) == integration-map.json['seams']` 为 True，下表同时代表 seams.json 26 行。"计划用例"一栏的状态取自本文 §6。

| 行（id/名字） | 计划要求 | 现状 | 代码/用例依据 | 说明 |
|---|---|---|---|---|
| S01 caller/factory | `api/facade.py::MissionControlV1` 改造 | 在用 | Host/service.py:348 构造；`SDK/api/facade.py:131`(批准检查政策)、`:178`(装根)、`:193-211`(三个读)；建任务 `SDK/orchestrator/commit_service.py:803` → `assurance_factory.py:165` | 计划用例 A03 存在 |
| S02 Requirements | `contracts/resolution.py::RequirementsRevision` 复用 | 在用 | `SDK/contracts/resolution.py:434`；检查政策投影 `SDK/deployment/duties.py:221,319` → facade:131 → `commit_service.py:632` | 计划用例 E01 已并入 PV:97 |
| S03 Task/Obligation/Scope | `scoped_content_review.read_task_content_projection` | 在用 | 定义 `scoped_content_review.py:168`；调用 `leaf_acceptance.py:414`、`scoped_content_review.py:364` | A15 存在 |
| S04 selected plan | `scoped_composition_review.py` 复用 | 在用 | `read_compound_projection` 调用 `composition_review.py:192`、`resolution_commits.py:1031`、`assurance_check_policy.py:213` | A14 已并入 PV:97 |
| S05 artifact/CAS | `ArtifactStore.read` | 在用 | `SDK/storage/assurance_blobs.py:146-171`（`cas.path_for`）；CAS 取自 `assurance_assembly.py:379` `assembled.workspaces.artifact_store` | A11 存在 |
| S06 pins/GC | 新 `artifacts/assurance_pins.py::acquire_review_pins` | 做法不同已记录 | 文件不存在；实为 `SDK/orchestrator/assurance_review_pins.py:17 ensure_review_blob_pins`（调用 `assurance_content_review.py:257`、`reviewer_evidence_tools.py:361`），`:154 bind_review_blob_pins`（`assurance_review_transport.py:367`），`SDK/storage/assurance_pins.py` | WIP:1044 记了实际文件 |
| S07 CheckSpec/policy | 新 `assurance_policy_commits.py::approve_assurance_check_policy` | 做法不同已记录 | 文件不存在；实为 `commit_service.py:632`，facade:131，生产调用 `deployment/duties.py:221,319` | WIP:213 |
| S08 checks | 新 `verification/assurance_checks.py::record_local_check_run` | 做法不同已记录 | 文件不存在；实为 `SDK/orchestrator/assurance_local_checks.py`(`AssuranceLocalChecks`) + `verification/assurance_local.py`；生产调用 `event_handler.py:8050,8270`（`local_check_factory` 进 router.verify） | WIP:700 |
| S09 review preparation | `RootReviewCoordinator.cut` | 在用 | `root_review.py:840`；调用 `event_handler.py:9269` | A06 存在 |
| S10 dispatch/reservation | `Orchestrator._ask_root_reviewer` | 在用 | `event_handler.py:9813`，调用 `:9279,:9284` → `ensure_mission_final` `:9826` → `assurance_review_transport.py:380 create_service_intent`、`:403 AssuranceReservationLinked` | A12 存在 |
| S11 catalogue/exposure | 新 `reviewer_evidence_tools.import_reviewer_disclosure` | 在用 | `reviewer_evidence_tools.py:489`；调用 `assurance_review_collect.py:143` | A10 存在 |
| S12 official collector | `Orchestrator._collect_root_review` | 做法不同已记录 | 根专用收集器已不在；统一 `collect_assurance_review`（`event_handler.py:5500,5592,6118`）→ REVIEW 消费者 | WIP:624；旧根审阅员随"批三"删（`HTN补齐-实施记录.md` A′-2 批三） |
| S13 source lifecycle writers | `Store.put_source` 加屏障 | 做法不同已记录 | 屏障改为 SQL 触发器：`SDK/storage/assurance_barrier_v26.sql:15,36`（sources insert/update），`assurance_schema.py:20` 装入 | WIP:44"migration 26 同事务来源屏障" |
| S14 Observation | `HtnStore.insert_observation` | 在用 | `htn_store.py:1279`；同事务 `record_source_change` `:1331` + observations 触发器 | V01 存在 |
| S15 rules/support | `HtnStore.insert_justification_set` | 已删（有决定） | 方法与表都不在；迁移 39 删表与触发器 `SDK/storage/schema.py:777-786` | `HTN补齐-阶段D-开工裁决与施工清单.md:55,165`（删 justification_sets/support_members） |
| S16 complete snapshot | 新 `knowledge/assurance_snapshot.py` | 做法不同已记录 | 实为 `SDK/storage/assurance_reads.py:294`；调用 `assurance_validity.py:516`、`assurance_purpose_reviews.py:272` | WIP:40 |
| S17 validity compute | `knowledge/validity.py::ValidityService` | 做法不同已记录 | 无 ValidityService；实为 `SDK/orchestrator/assurance_validity.py`（`:185` 绑到 commit），使用 `resolution_commits.py:1721,1769,1823`、`hierarchical_dispatch.py:2227` | WIP:428 |
| S18 atomic barriers | 新 `assurance_store.mark_assurance_change_locked` | 做法不同已记录 | 无此函数；由触发器在同事务推 `validity_epochs` 并写 `AssuranceEvidenceChanged`（`assurance_barrier_v26.sql`，`SDK/storage/source_records.py:43`） | WIP:44 |
| S19 scoped acceptance | `ResolutionCommits.accept_review` | 在用 | `resolution_commits.py:589`，守卫 `:673 _require_assured_use`；调用 `leaf_acceptance.py:519`、`operation_outcomes.py:654` | A13 已并入 |
| S20 OCC effects | `completion_status.py::OperationCompletionReader` | 做法不同未记录（仅位置） | 类在 `SDK/orchestrator/operation_completion.py:302`；调用 `deployment/root.py:223`、`operation_intent_sources.py:93`、`runtime/operation_ref_resolver.py:214` | 计划写错文件，低影响；E03 已并入 |
| S21 root/terminal | `CommitService.judge_mission` | 在用（残留旧尾巴） | `commit_service.py:3046`；调用 `event_handler.py:10531,10825`；保证分支 `:3140-3144 request_assured_closeout`；`:3145-3158` 非保证直写 COMPLETED 的尾巴仍在 | 第一遍已记；计划用例 A18 已删 |
| S22 actual running work | `completion_support.py::read_running_work` | 做法不同未记录（仅位置） | 在 `SDK/runtime/planning_operations.py:567`；调用 `event_handler.py:7010`、`planning_admission_commits.py:235` | 低影响；C05 存在 |
| S23 accounting | `accounting_recovery.py` 迟到记账导入 | 在用 | `accounting_recovery.py:159 import_late_accounting`；调用 `event_handler.py:798,822,2146,3693` | C08 已并入 test_review_turn_retry_e2e |
| S24 cursor/pending consumers | 新 `assurance_events.py::AssuranceEventConsumer` | 做法不同已记录 | 文件不存在；实为 `assurance_tick.py:73 AssuranceTick` + `assurance_consumers.py`/`assurance_review_consumer.py`；装配 `assurance_assembly.py:405-433`；主循环 `event_handler.py:3694` | WIP:65 |
| S25 Host/native | 新 `api/assurance.py::AssuranceApi` | 在用 | `api/assurance.py:156`；`assurance_assembly.py:445-455`；Host `handlers.py:257-259` → `service.py:1412` | C07 存在 |
| S26 restore/recovery | `storage/offline_backup.py::restore_offline` | 已删（有决定） | 文件不存在 | A″ 提交 6cf1643a"删离线备份与受管恢复"；用户口径受管恢复已删；C03 用例仍在（只测冷重开） |

统计：在用 13（含 S21 残留尾巴）、做法不同已记录 9、做法不同未记录 2（S20/S22，只是计划写错位置）、已删有决定 2。

### 2. integration-map.json · reviews（6 行）

六个用途都要求"复用原入口 + 统一保证适配器 + `create_service_intent` 派发 + 原收集器 + 不开平行审阅"。统一派发点 `SDK/orchestrator/assurance_review_transport.py:380`（`create_service_intent`），统一收集 `event_handler.py:5500` `collect_assurance_review`。

| 行（id/名字） | 计划要求 | 现状 | 代码/用例依据 | 说明 |
|---|---|---|---|---|
| TASK_CONTENT | 原 scoped 内容/叶验收/VerifierRouter 构造；记任务账 | 在用 | `event_handler.py:8600 run_task` → `assurance_review_runtime.py:464 ensure_task_content_review`（`assurance_content_review.py:35`） | — |
| METHOD_PLAN | 原 MethodProposal 审阅；记 mission_planning 账 | 在用 | `event_handler.py:6803 ensure_method_plan`（`assurance_review_runtime.py:289`） | 第一遍差距 1：采用做法时不核使用证书（`plan_commits.py:381`），未记录 |
| COMPOSITION | 原 scoped_composition/composition_review；记 parent_compound_task 账 | 在用，记账做法不同已记录 | `composition_review.py:221 ensure_composition`；记账改到 Mission 账 `assurance_review_transport.py:177-199 review_budget_subject` | `HTN-片B-实施记录.md:176`（复合目标 0 额度，真机第 4 局） |
| ACTION_PROPOSAL | 原 operation_proposal_review / T0；记 operation_task 账 | 在用 | `operation_runtime.py:128 ensure_action_proposal` | 第一遍差距 1 同样适用（`operation_materialization.py:373`） |
| OPERATION_OUTCOME | 原 operation_outcomes / OperationCompletionReader | 在用 | `operation_runtime.py:294 ensure_operation_outcome` | 目标种类允许 tool_receipt，但该种类无解析器（见 §4） |
| MISSION_FINAL | 原 RootReviewCoordinator.cut/record_review + `_ask/_collect_root_review` | 在用（收集与正式写方做法不同，已记录） | `event_handler.py:9826 ensure_mission_final`；正式记录由 REVIEW 消费者写，`RootReviewCoordinator.record_review` 已删 | 批三"删旧根审阅员" |

统计：在用 6（其中 2 行带已记录的做法差异）。

### 3. integration-map.json · terminal_writers（8）/ invalidation_writers（8）/ event_consumers（4）/ restore（3）

| 行（id/名字） | 计划要求 | 现状 | 代码/用例依据 | 说明 |
|---|---|---|---|---|
| TW1 `CommitService.accept_result` | 范围贡献，不提升效果 | 在用 | `commit_service.py:2773`，`:2865 _prepare_assured_acceptance`；调用 `event_handler.py:8141` | — |
| TW2 `ResolutionCommits.accept_review` | OCC 守卫 + 正式/检查/使用绑定 | 在用 | `resolution_commits.py:673,1708 _require_assured_use` | 绑定守卫的变异 AM10 已无目标用例（§9） |
| TW3 `commit_goal_resolution` | 所有直接入口守卫、精确效果闭包 | 在用 | `resolution_commits.py:960`，`:1053-1059` 根/复合使用证书；调用 `composition_review.py:321`、`hierarchical_dispatch.py:2340` | — |
| TW4 `CommitService.judge_mission` | 只请求收尾，不提前放行 | 在用（残留死尾巴） | `commit_service.py:3140-3144`；`:3145-3158` 非保证 COMPLETED 分支仍在 | 第一遍已记"应删" |
| TW5 `_finalize_assured_mission_locked`（新私有终写） | 同事务唯一终写 | 做法不同已记录 | 独立模块 `assurance_final_writer.py:125 finalize_assured_mission`，CLOSEOUT 消费者经 `assurance_assembly.py:411` 调用；`commit_service.py:3290` 包一层 | HANDOFF-2026-09-23 第 7 项 |
| TW6 `RootReviewCoordinator.record_review` | 要求来源见证 | 已删（有决定） | `root_review.py` 已无此方法（只剩 :21 注释） | 批三删旧根审阅员 |
| TW7 `Store.update_mission/update_task` 调用点清单 | 不留无守卫终态调用 | 在用，清单未成文 | 我 grep：写 `MissionStatus.COMPLETED` 只有 `assurance_final_writer.py:178` 与 judge 尾巴 `commit_service.py:3147` 两处；missions 表无 SQL 终态守卫 | 计划要的"调用点清单"没有成文 |
| TW8 cancel/fail/timeout 清理 | 保留实际效果/占用，不是捷径 | 在用 | `_assured_terminal_notice` 于 `commit_service.py:1468,1488,1523,3174,3464` | HANDOFF 第 7 项"六个原终态写口的通知请求" |
| IW1 Store.put_source/撤/删 | mission 或 global 屏障 | 在用 | `assurance_barrier_v26.sql:15,36` 起 sources 触发器 | — |
| IW2 HtnStore.insert_observation | mission 屏障 | 在用 | observations 触发器 + `htn_store.py:1331` | 反证插入的用例见 §6 V07 |
| IW3 justification_set/support members/规则准入 | mission 屏障 | 已删（有决定） | 迁移 39 `schema.py:777-786` 删表与触发器 | 阶段 D 开工裁决:55 |
| IW4 Requirements/OCC/Plan/Task 合同写方 | mission 屏障 | 在用 | 触发器覆盖 requirements_revisions、operation_completion_specs/scopes、plan_revisions、task_semantics 等（`assurance_barrier_v26.sql` 触发器清单） | — |
| IW5 当前 ACL/policy/grant 写方 | global 或 mission | 在用（ACL 做法不同已记录） | 触发器覆盖 policy_versions/proposals/activations、approvals、planning_lane_grants、mission_policies；ACL 是部署固定主体 `assurance_assembly.py:381 FixedPrincipalAuthority` | WIP 第六段 |
| IW6 执行与操作更正导入 | 编排导入事务 | 在用 | events/actions/results/verifications 触发器 | — |
| IW7 artifact/check/review 生命周期 | mission/global | 在用 | artifacts、assurance_check_bindings、review_records、review_packages 触发器 | — |
| IW8 时间不连续探测 | 全局 clock_generation | 在用 | `assurance_clock.observe_assurance_clock`，调用 `assurance_tick.py:142,208,232`、`facade.py:891` | — |
| EC REVIEW | 同事务 pending+游标 CAS；事务外 prepare；效果+回执+DONE | 在用 | `assurance_review_consumer.py:50,83-100`；装配 `assurance_assembly.py:394,406` | — |
| EC VALIDITY | 同上 | 在用 | `assurance_consumers.py:154,176`；`assurance_assembly.py:407` | — |
| EC CLOSEOUT | 同上 | 在用 | `assurance_consumers.py:344,376`；`assurance_assembly.py:408-412` | — |
| EC NOTIFY | 同上 | 在用 | `assurance_consumers.py:735,761`；`assurance_assembly.py:413-415`；tick `event_handler.py:3694` | — |
| restore.entry | `offline_backup.restore_offline` | 已删（有决定） | 文件不存在 | A″ 6cf1643a |
| restore.chosen_policy | 新根隔离 + 当前只读再授权 | 已删（有决定），残留死分支 | 根闸门对"根状态文件丢失"仍隔离；隔离下按授权精确只读 `facade.py:870-895` 生产走不到 | 第一遍已记（只在测试） |
| restore.external_revocation_log | NOT_ASSUMED | 不适用（随恢复删） | — | — |

统计：terminal 8 = 在用 6（含 TW4 残留、TW7 清单未成文）、做法不同已记录 1、已删 1；invalidation 8 = 在用 7、已删 1；event_consumers 4 = 在用 4；restore 3 = 已删 2、不适用 1。
integration-map 合计 55 行：在用 36、做法不同已记录 10、做法不同未记录 2、已删有决定 6、不适用 1（"在用"里有 2 行带已记录差异、2 行带残留问题）。

（另：integration-map 的 `host` 块不在 55 行内，顺带核了：三动词 `Host/handlers.py:42-44,257-259`、`Host/service.py:1412`、UI `tauri-app/src/views/MissionsView.tsx:34` 引 `MissionAssurance.tsx`、`backend/deskpet/sdk_adapters/sdk_candidate.py` 存在——在用。）

### 4. event-consumer-map.json（7 行）

| 行（id/名字） | 计划要求 | 现状 | 代码/用例依据 | 说明 |
|---|---|---|---|---|
| CANDIDATE_READY → REVIEW | 候选就绪事件唤醒，准备一次原 scoped 审阅 | 做法不同未记录 | 不经事件分类：六个原入口直接 `ensure_*`（§2），靠 review_key 幂等（如 `event_handler.py:9821-9831`） | 低影响：等价于"原入口一次"；没写成偏差 |
| ACTUAL_REVIEW_TURN_AVAILABLE → REVIEW | 只解析真实回复，正式或有限格式修复 | 在用 | 收集 `event_handler.py:5500` 写 `AssuranceReviewTurnImported`（`assurance_review_collect.py:114`）→ REVIEW 分类 `assurance_review_consumer.py:83-100`（Classified/FormatRejected） | — |
| ACTUAL_CHECK_AVAILABLE → REVIEW, VALIDITY | 重评检查，不自动重复模型调用 | VALIDITY 在用；REVIEW 侧做法不同未记录 | `VALIDITY_SOURCE_EVENTS` 含 LocalCheckImported/ExecutorCheckImported（`assurance_consumers.py:71-73`）；REVIEW 分类不收检查事件，检查在审阅准备里同步跑（`event_handler.py:8050`） | WIP:889 只记了接法 |
| SOURCE_OR_AUTHORITY_CHANGED → VALIDITY, CLOSEOUT | 屏障已同事务提交，排队有界重算 | 在用 | 触发器写 `AssuranceEvidenceChanged`（`source_records.py:43`）；VALIDITY `:72`、CLOSEOUT `:43`；REVIEW 也用它唤醒等待中的导入 `assurance_review_consumer.py:51` | WIP:863 显式登记 |
| BUSINESS_OR_RUNTIME_SETTLED → REVIEW, CLOSEOUT | 新事实解开原等待工作 | CLOSEOUT 在用；REVIEW 侧做法不同未记录 | `CLOSEOUT_SOURCE_EVENTS` `assurance_consumers.py:41-69`；"下一个组合审阅"由 `composition_review.py:221` 直接触发，不经事件 | 低影响 |
| AssuranceStatusNotificationRequested → NOTIFY | 至少一次本地状态通知 | 在用 | `assurance_final_writer.py:36`、`assurance_consumers.py:761`；Host 通知 transport 经 `deployment/assembly.py:93` | — |
| DIAGNOSTIC_ONLY → 无 | 忽略自身日志，推进游标不递归 | 在用 | `assurance_consumers.py:75 DIAGNOSTIC_EVENTS` | — |

统计：在用 4、部分在用（REVIEW 侧做法不同未记录）2、做法不同未记录 1。

### 5. ref-resolution-map.json

#### 5.1 kinds（29 种）

解析器：`SDK/storage/assurance_reads.py:111-129 _EXACT`（17 种表）、`SDK/assurance/event_kinds.py:4-11`（6 种事件）、`SDK/storage/assurance_blobs.py:34`（artifact/source）；其余种类 `assurance_reads.py:157` 报 `REF_KIND_UNSUPPORTED`。"生产构造/解析"一栏是我 grep 的 `AssuranceRef("种类", …)` 或 `kinds={…}` 的生产位置。

| 行（id/名字） | 计划要求 | 现状 | 代码/用例依据 | 说明 |
|---|---|---|---|---|
| requirements | requirements_revisions 精确版本 | 在用 | 解析 `_EXACT:112`；构造 `assurance_purpose_reviews.py:223`、`assurance_review_transport.py:77`、`assurance_check_use.py:290`、`assurance_validity.py:468` | — |
| task | task_semantics(task_id,binding_revision) | 在用 | `_EXACT:113`；`assurance_content_review.py:140`、`assurance_purpose_reviews.py:547,754` | — |
| method | method_contracts 身份/版本 | 在用 | `_EXACT:125`；`assurance_purpose_reviews.py:512` | — |
| method_instance | method_instances | 在用 | `_EXACT:124`；`assurance_purpose_reviews.py:263`、`assurance_review_transport.py:117` | — |
| artifact | artifacts 或 operation_payload_objects，拒双定位 | 在用 | `assurance_blobs.py:34,61-64`；`facade.py:886`、`assurance_content_review.py:160`、`assurance_validity.py:411` | — |
| source | sources | 在用 | `assurance_blobs.py:34`；`reviewer_evidence_tools.py:205`、`assurance_content_review.py:167` | — |
| observation | observations | 有解析器，生产无人构造 | `_EXACT:114`；生产无 `AssuranceRef("observation")` | 死解析器；无记录 |
| review | review_records | 在用 | `_EXACT:115`；`assurance_validity.py:454,744`、`api/assurance.py:677` | — |
| acceptance | acceptances | 在用 | `_EXACT:117`；`assurance_purpose_reviews.py:128` | — |
| resolution | goal_resolutions | 有解析器，生产无人构造 | `_EXACT:118`；生产无构造 | 死解析器；无记录 |
| operation | operation_bindings | 在用 | `_EXACT:126`；`assurance_purpose_reviews.py:702` | — |
| tool_receipt | 原执行回执适配器 | 没做 | 无解析器、无构造；但 `assurance/reviews.py:246` 仍允许它作 OPERATION_OUTCOME 目标 | 第一遍已记（做法不同未记录） |
| policy | 原政策注册表 | 没做 | 无解析器、无构造 | 同上；政策走读集通道 |
| authority | 原授权回执读者 | 没做 | 无解析器、无构造 | 同上 |
| capability | 原适配器注册表 | 没做 | 无解析器、无构造 | 同上 |
| input_manifest | input_manifests | 在用 | `_EXACT:127`；`assurance_local_checks.py:582`、`assurance_review_collect.py:85`、`commit_service.py:1253`、`operation_proposal_review.py:586` | — |
| completion_scope | OCC scope | 在用 | `_EXACT:120`；`assurance_purpose_reviews.py:589,697`、`assurance_local_checks.py:561` | — |
| completion_spec | OCC 批准 Spec | 有解析器，生产无人构造 | `_EXACT:119`；生产无构造 | 死解析器；无记录 |
| result | results（只取 envelope） | 在用 | `_EXACT:121`，`:191` 剥掉验证外壳；`assurance_content_review.py:139`、`assurance_local_checks.py:543` | — |
| commit_receipt | commit_receipts | 在用 | `_EXACT:128`；`assurance_review_pins.py:100,149`、`assurance_root_commits.py:92,171` 等 | — |
| reservation_fact | AssuranceReservationLinked 事件 | 在用 | `event_kinds.py:5`；`assurance_review_transport.py:418` | — |
| agent_turn_receipt | AssuranceReviewTurnImported 事件 | 在用 | `event_kinds.py:6`；`assurance_review_collect.py:120` | — |
| check_binding | assurance_check_bindings | 在用 | `_EXACT:122`；`assurance_store.py:444` | — |
| check_spec | AssuranceCheckSpecRegistered 事件 | 在用 | `event_kinds.py:9`；`assurance_local_checks.py:497` | — |
| check_policy | assurance_criterion_policies | 在用 | `_EXACT:123`；`assurance_store.py:383`、`assurance_purpose_reviews.py:243` | — |
| local_check_receipt | AssuranceLocalCheckFinished 事件 | 在用 | `event_kinds.py:8`；`assurance_local_checks.py:326`（`_LOCAL_NAMES`） | — |
| execution_receipt | AssuranceExecutionImported 事件 | 在用（仅 code_test 层走到） | `event_kinds.py:7`；`assurance_local_checks.py:187,326`（`_EXECUTOR_NAMES`） | — |
| disclosure_receipt | AssuranceEvidenceDisclosed 事件 | 在用 | `event_kinds.py:10`；`reviewer_evidence_tools.py:472` | — |
| review_package | review_packages 精确体 | 在用 | `_EXACT:116`；`assurance_validity.py:467`、`assurance_review_handoff.py:116`、`assurance_review_transport.py:604` | — |

统计：在用 22、有解析器但生产无构造 3（observation/resolution/completion_spec）、没做 4（tool_receipt/policy/authority/capability）。

#### 5.2 field_contracts（9 条）

| 行（id/名字） | 计划要求 | 现状 | 代码/用例依据 | 说明 |
|---|---|---|---|---|
| blob-pin-v1.source_receipt_ref | 只收 commit_receipt 引用 | 做法不同未记录 | 钉子不是 JSON 对象，是 SQL 行列 `source_receipt_id`（`SDK/storage/assurance_pins.py:82,125` 回读核 hash）；SDK 无 blob-pin-v1 schema | 低影响：外键 + 回执 hash 核对等价 |
| blob-pin-v1.last_receipt_ref | 只收 commit_receipt | 做法不同未记录 | `last_receipt_id`（`assurance_schema.sql:128,228` 外键；`assurance_pins.py:133-142`） | 同上 |
| review-invocation-v1.reservation_fact_ref | reservation_fact | 在用 | `SDK/assurance/reviews.py:331`；构造 `assurance_review_transport.py:418` | — |
| review-record-binding-v2.reviewer_turn_ref | agent_turn_receipt | 在用 | `reviews.py:84` | — |
| review-record-binding-v2.consumed_check_refs[] | check_binding | 在用 | `reviews.py:91` | — |
| check-binding-v2.execution_ref | local_check_receipt / execution_receipt | 在用 | `SDK/assurance/check_bindings.py:48` | — |
| review-binding-v2.…any_check_sets[][] | check_spec | 在用 | `SDK/assurance/checks.py:137` | — |
| review-binding-v2.criterion_policy_ref | check_policy | 在用 | `reviews.py:210`；`assurance_review_transport.py:87` | — |
| review-record-binding-v2.disclosure_refs[] | disclosure_receipt | 在用 | `reviews.py:92`；`assurance_review_import.py:939` | — |

统计：在用 7、做法不同未记录 2。

### 5′. BODY-WIRED.md（16 条生产调用边）

第一遍只看了"有没有逐条登记关闭"（没有）；这里按代码核每条边是否真的接通。

| 行（id/名字） | 计划要求 | 现状 | 代码/用例依据 | 说明 |
|---|---|---|---|---|
| BW01 | 工厂→持久建任务通道→激活回执→纪元/游标/待办初始化；缺绑定不回落旧通道 | 在用 | `commit_service.py:803` → `assurance_factory.py:165-169`（未装即 `ASSURANCE_FACTORY_UNBOUND`）；激活清单 `assurance_assembly.py:196,418-423` | — |
| BW02 | 原要求批准→CheckPolicy+CheckSpec | 在用 | `deployment/duties.py:221,319` → `facade.py:131` → `commit_service.py:632` | — |
| BW03 | 六个构造器→统一轮次传输 | 在用 | 见 §2；`assurance_review_transport.py:380` | — |
| BW04 | 真实预留→intent→ReservationLinked→调用；第 2 序号有真实前次失败与新预留 | 在用 | `assurance_review_transport.py:228,380,403,418` | — |
| BW05 | 原格式/规则/引用记录器/代码执行器→真实回执→CheckBinding | 在用 | `event_handler.py:8050`；`assurance_local_checks.py:174-187,326`；`assurance_check_import.py:179` | — |
| BW06 | 初始+只读追加工具→真实输入清单→标签曝光批次 | 在用 | `assurance_review_runtime.py:103-105` 装工具；`assurance_review_collect.py:85,143` | — |
| BW07 | 原收集器→TurnImported→正式记录+绑定→原 accept_review/OCC | 在用 | `event_handler.py:5500` → `assurance_review_collect.py:114-125` → REVIEW 消费者 → `leaf_acceptance.py:519` | — |
| BW08 | 完整来源→闭包→纪元/全局/时间/恢复闸→UseCertificate | 在用（恢复闸随 A″ 删） | `assurance_validity.py:516`；`resolution_commits.py:1708` | — |
| BW09 | 所有源写方→同事务屏障 | 在用（理由集一类已删有决定） | `assurance_barrier_v26.sql` 全表触发器；迁移 39 删 justification 两表 | 见 §3 IW1-8 |
| BW10 | Input/Context/Scope/accepted_outputs/根就绪/终提交→统一使用规则 | 做法不同未记录（部分） | 叶/根/复合接受都核证书（`resolution_commits.py:673,1053-1059`）；做法审阅与操作提案采用不核（`plan_commits.py:381`、`operation_materialization.py:373`） | 第一遍差距 1 |
| BW11 | judge_mission→收尾→唯一终写；旧直写路径封堵 | 在用（残留死尾巴） | `commit_service.py:3140-3144`；尾巴 `:3145-3158` | 第一遍已记；"直接调终提交"的用例 A18 已删（§6） |
| BW12 | 事件入口→同事务待办+游标→事务外准备→效果+ACK | 在用 | `assurance_tick.py:156,200`；`event_handler.py:3694` | — |
| BW13 | tick 到期/时钟回退/启动对账/租约回收 | 在用 | `assurance_tick.py:142,208`；`assurance_assembly.py:222,456 reconcile_startup` | — |
| BW14 | 受管恢复→新隔离根→当前精确读授权 | 已删（有决定） | — | A″；HANDOFF:74 仍写"16 条未关闭"未更新 |
| BW15 | Host 三动词→固定调用者→投影→MissionsView；隔离 wheel | 在用 | `Host/handlers.py:257-259`；`Host/service.py:1412`；`Host/assurance.py`；`MissionsView.tsx:34`；`sdk_candidate.py` | 原生点击验收另算（第一遍已记） |
| BW16 | 新工厂默认开启、旧通道持久判别、ARCHITECTURE/STATUS 回写 | 部分 | 默认开启：`assurance_factory.py:162-165`（每个任务必装）；旧通道判别：随"旧路径直接删"一并删；文档回写：没做（第一遍差距 16） | — |

统计：在用 13（含 BW08/BW09/BW11 三条带说明）、做法不同未记录 1（BW10）、已删有决定 1（BW14）、部分 1（BW16）。

### 6. sdk-cases.json（48 组确定性场景；用例名以 SDK 回写版 `sdk/simple-harness-sdk/plans/assurance-1.1/sdk-cases.json` 的 actual_nodeids 为准）

注：名字仍在但断言少了一半的有 3 条——A03、A11 的"要求书第 2 版"一半、A07 的"伪造作者集合"一半已删（`T/full_target/assurance_exec/test_a_assurance.py:11-13` 头注释：第 2 版产品上无写入方 / 靠替换产品读函数造状态，裁决①不许）。下表仍记"存在"。

| 行（id/名字） | 计划要求 | 现状 | 代码/用例依据 | 说明 |
|---|---|---|---|---|
| A01 | 逐项拒绝；无official Review/Acceptance/新调用，raw审计与真实费用可保存 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_strict_boundary |  |
| A02 | mandatory不得被ANY绕过；未评估UNKNOWN；非法公式不进入正式要求 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_formula_truth_tables |  |
| A03 | 前者无权改变；后者新revision+receipt，旧bytes保持 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_requirement_authority |  |
| A04 | 错误回执不能满足准则；真实失败/ERROR/未跑不记PASS | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_check_receipt_scope |  |
| A05 | B成功时A不阻塞；privacy失败必阻塞；未运行检查不得伪造结果；后者可选择第二分支，不能因数组次序无条件拒绝或放宽第 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_any_branch_not_mandatory |  |
| A06 | 同事务全回滚；CAS孤儿无权；重送一次真实dispatch；durable pin与原GC真实门互斥，未知工作不得TTL | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_review_dispatch_atomic |  |
| A07 | 拒绝前三者；B沿正式turn回流；不得通过换显示名伪造独立 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_reviewer_independence |  |
| A08 | 错绑定不official；同来源幂等；记录真实消耗不丢 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_review_source_binding |  |
| A09 | 新round新package/intent；旧official不覆盖；原round重送无新费用预留；不同通知event  | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_review_new_round_identity |  |
| A10 | 前者可在追加manifest使用；后两者阻断且不泄露对象内容；原请求bytes保持 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_extra_evidence_exposure |  |
| A11 | 保存原v1 Review与费用；不能批准v2；无关变化精确复核可继续 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_scope_pinned_not_latest |  |
| A12 | 明确BLOCKED或INCONCLUSIVE，不能免费调用或重置Obligation计数 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_check_budget_and_format_bounds |  |
| A13 | Attempt可结束、准备贡献可读；Task VERIFYING、Obligation/Mission不满足、actio | 并入代表用例（有记录，代表用例存在） |  | `test_preparation_does_not_complete` → 并入有记录；承接：T/full_target/operation_completion/test_publish_variants.py::test_content_acceptance_is_preparation_only_until_the_publish_completes（:97，注释标 A13～A17）；出处：HTN补齐-阶段A撇-分诊表.md:136；test_a_assurance.py:9-10 头注释 |
| A14 | DATA按purpose可读；ORDER不提前释放；缺manifest不回退空 | 并入代表用例（有记录，代表用例存在） |  | `test_preparation_data_not_order` → 并入有记录；承接：T/full_target/operation_completion/test_publish_variants.py::test_content_acceptance_is_preparation_only_until_the_publish_completes（:97）；出处：HTN补齐-阶段A撇-分诊表.md:136；test_a_assurance.py:9-10 |
| A15 | 不以all(children.completed)通过；不同Obligation/occurrence范围不能混用 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_composition_not_all_children |  |
| A16 | 等待仍可见；不重复生成报告，不false complete/no-progress失败，正式Review/核对可推进 | 并入代表用例（有记录，代表用例存在） |  | `test_pending_effect_no_worker_loop` → 并入有记录（两处记录不一致）；承接：T/full_target/operation_completion/test_publish_variants.py::test_content_acceptance_is_preparation_only_until_the_publish_completes（:97）；出处：分诊表:136 写"删"，test_a_assurance.py:10 写"同上（并入）" |
| A17 | 没有intent仍欠两效果；未选路线不强制内容完成，但已发危险动作仍收尾 | 并入代表用例（有记录，代表用例存在） |  | `test_root_requirement_effect_catalogue` → 并入有记录；承接：T/full_target/operation_completion/test_publish_variants.py::test_content_acceptance_…（:97）；另 ::test_the_final_review_sees_the_accepted_root_effect_and_its_readback（:385）；出处：HTN补齐-阶段A撇-分诊表.md:136；test_a_assurance.py:9 |
| A18 | 事务内同样拒绝；Mission/Task不误完成，保留预算/未决责任 | 已删（有决定/记录） |  | `test_direct_final_commit_guard` → 已删（有记录，但与分诊表不符）；承接：无；出处：test_a_assurance.py:10-11 记"A18 删：要关有效性服务才碰得到（裁决①不许）"；分诊表:136 原写"并入代表用例 3 变体" |
| V01 | source_says仅来源陈述；空查询不是FALSE；合格负观察建立负literal | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_anchors_and_explicit_negation |  |
| V02 | 未准入规则不参与；rule policy/独立蕴含review具体来源可追 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_rule_admission_required |  |
| V03 | 无锚不自证；撤后重新从当前源计算，不保活旧VERIFIED | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_cycle_without_anchor |  |
| V04 | K可继续；原报告引用不能悄悄替换；不重跑无关Worker | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_alternate_support |  |
| V05 | 冲突路径不用于执行；clean E路径可用；K本身冲突阻断；无逻辑爆炸 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_conflicted_paths |  |
| V06 | 不得写COMPLETE/USABLE；明确INCOMPLETE/UNAVAILABLE，不显示隐私证据 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_complete_collection_not_topk |  |
| V07 | 源+epoch同事务；旧proof失效；最终guard检测新的集合而非只查旧ref | 换芯改名（有记录），反证观察一半未做 |  | `test_insert_counterevidence_barrier` → 换芯改名（反证一半未做）；承接：T/full_target/assurance_exec/test_v_assurance.py::test_a_concurrent_source_change_is_an_insert_barrier（:532）；出处：分诊表:160 记"A 换芯"；新用例注释自述"反证观察那一半等带观察器的测试世界"；疑似由 T/product_world/test_desktop_preconditions.py::test_file_change_flips_observation_and_moves_epoch 承接（未登记） |
| V08 | 新用阻断；历史事实仍在；采样无连续保证；不靠timer刷新窗口延迟放行 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_authority_and_expiry |  |
| V09 | 每个都核同一源与用途；查库VERIFIED/summary不能绕过 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_all_consumers_share_validity |  |
| V10 | 全部拒绝；hash不是authority；同上下文新鲜重读可用 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_read_certificate_context |  |
| V11 | CAS拒旧投影；保留历史诊断但不清epoch8 dirty | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_invalidation_racing_cache |  |
| V12 | 复制不凑独立计数；真正不同执行来源按批准policy判断 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_independent_provenance_roots |  |
| V13 | 隔离根在新授权前只允许非披露诊断；旧备份grant/证书不放行。新授权仅匹配new_root+manifest+exac | 已删（有决定/记录） |  | `test_restore_quarantine_and_current_reauthorization` → 已删（有决定 A″）；承接：剩 test_v_assurance.py::test_root_quarantine_and_current_read_authority（:670）只管根闸门；出处：6cf1643a"删离线备份与受管恢复"；用户口径受管恢复已删 |
| V14 | 边界可计算；超界不宣称无反证；事件重放不重复bump和派发 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_bounded_restartable_evaluation |  |
| E01 | 不要求Mission先完成，无循环；缺Spec不推CONTENT_ONLY | 并入代表用例（有记录，代表用例存在） |  | `test_completion_spec_before_intent` → 并入有记录；承接：T/full_target/operation_completion/test_publish_variants.py::test_content_acceptance_…（:97，注释标 E01）；出处：HTN补齐-阶段A撇-分诊表.md:144 |
| E02 | 明确能力不足；不改变批准准则、不把HTTP200记delivered | 承接不明（有总括记录） |  | `test_profile_does_not_choose_milestone` → 承接不明（有总括记录）；承接：疑似 T/product_world/test_operation.py::test_the_confirmation_page_refuses_a_milestone_the_profile_cannot_reach（:182，未标 E02）；出处：HTN补齐-阶段A撇-分诊表.md:144 总括"并入代表用例 3" |
| E03 | 原effect Acceptance+Contribution+必要Delivery原子，root仍需组合 | 并入代表用例（有记录，代表用例存在） |  | `test_exact_outcome_chain` → 并入有记录；承接：T/product_world/test_operation.py::test_a_publishing_mission_completes_on_the_product_deployment（:68）+ T/full_target/operation_completion/test_publish_variants.py:97（标 E03）；出处：HTN补齐-阶段A撇-分诊表.md:144 |
| E04 | 均不能效果完成；真实执行事实照存；非空operation_id不充分 | 并入代表用例（有记录，代表用例存在） |  | `test_outcome_identity_variants` → 并入有记录；承接：T/full_target/operation_completion/test_publish_variants.py::test_a_tampered_outcome_binding_is_never_accepted（:480，标 E04）；出处：HTN补齐-阶段A撇-分诊表.md:144 |
| E05 | 拒绝重复/歧义；两项各有合法链才完成；UNKNOWN/待审批单独可见 | 并入代表用例（有记录，代表用例存在） |  | `test_multiple_effects_single_receipt` → 并入有记录；承接：T/full_target/operation_completion/test_publish_variants.py::test_two_required_publishes_each_need_their_own_chain（:420，标 E05）；出处：HTN补齐-阶段A撇-分诊表.md:144 |
| E06 | 保存原事实/费用；旧接受不满足新要求；可新scope审原事实，禁止重发凑证据 | 缺失（记录与实际不符） |  | `test_late_fact_new_requirement` → 缺失（记录与实际不符）；承接：未找到"晚到事实/新要求"变体；出处：分诊表:144 写并入该变体；同义的 OCC-05 在 test_completion_contract.py:12 以"要求书第 2 版产品无写入方"删，E06 本身无删除记录 |
| E07 | 新lane必须完整来源链；PERSISTED/ENQUEUED通知不冒充动作效果 | 缺失（记录与实际不符） |  | `test_delivery_writer_bypass` → 缺失（记录与实际不符）；承接：未找到；出处：分诊表:144 只总括"并入代表用例 3"；绕过 T3 直写交付回执的反例无承接 |
| E08 | 新CONTENT_ONLY正常完成但不加效果或启动action；legacy bytes/事件/ID不变 | 已删（有决定/记录） |  | `test_content_legacy_compatibility` → 已删（有决定）；承接：无；出处：分诊表:144"随非保证通道删"；用户口径旧审阅路径已删 |
| C01 | 未commit整组回滚；commit后重送同receipt，不重复通知/扣费 | 存在 | T/full_target/assurance_exec/test_c_assurance.py::test_acceptance_atomic_faults |  |
| C02 | 唯一采用、异体冲突、最新源guard、无lost update，无新副作用 | 存在 | T/full_target/assurance_exec/test_c_assurance.py::test_two_connection_concurrency |  |
| C03 | 原intent/raw/request/费用恢复；不能凭lease过期新发模型或重新编码 | 存在 | T/full_target/assurance_exec/test_c_assurance.py::test_review_cold_resume |  |
| C04 | 同源命令幂等；缺cursor显式重建；不先ACK内存工作，无无限自触发 | 存在 | T/full_target/assurance_exec/test_c_assurance.py::test_event_cursor_atomicity |  |
| C05 | 保留未决责任；正确收敛后事务结束；不推过期正文，通知队列不等于送达；只能READY→FINALIZED；不能从NOT_R | 存在 | T/full_target/assurance_exec/test_c_assurance.py::test_closeout_and_notification |  |
| C06 | 旧DDL hash/数据/预算/Event保持；不以参考parent schema结果充数 | 存在 | T/full_target/assurance_exec/test_c_assurance.py::test_real_migration_and_legacy |  |
| C07 | 同快照水位、权限隔离、旧画面标stale；不由UI自行宣布完成；另有原生实际点击/截图/断线与冷恢复记录，接口pytes | 存在 | T/full_target/assurance_exec/test_c07_host_api.py::test_contract_checker_rejects_drift；T/full_target/assurance_exec/test_c07_host_api.py::test_snapshot_review_use_check_contracts_and_errors |  |
| C08 | 原责任正确计费一次；未知费用保留hold；不复活任务/权限；本例不包含真实模型12局 | 并入代表用例（有记录，代表用例存在） |  | `test_late_accounting_is_separate` → 并入有记录；承接：T/full_target/assurance_exec/test_review_turn_retry_e2e.py::test_provider_server_error_first_turn_is_retried_once_and_imported（注释"外加 C08"）；另 T/product_world/test_late_usage.py；出处：分诊表:140 |

### 7. occ-coverage.json（12 条；回写版）

| 行（id/名字） | 计划要求 | 现状 | 代码/用例依据 | 说明 |
|---|---|---|---|---|
| OCC-01 | 准备Acceptance与Contribution存在；获准DATA和ACTION_PROPOSAL可读取报告；MIXE | 并入代表用例（有记录，代表用例存在） |  | `test_report_preparation_does_not_finish_effect` → 并入有记录；承接：T/full_target/operation_completion/test_publish_variants.py::test_content_acceptance_…（:97）；出处：test_completion_contract.py:9；分诊表:281 |
| OCC-02 | 前四项具名拒绝/待确认，不从候选/profile/无intent推断要求；正确的Spec在原要求批准链持久化。 | 存在（个别名并入，有记录） | 现存：T/full_target/operation_completion/test_completion_contract.py::test_occ02_byte_codec_normalizes_deep_and_oversize_json_to_contract_error；T/full_target/operation_completion/test_completion_contract.py::test_occ02_duplicate_json_key_cannot_replace_an_effect_requirement；T/full_target/operation_completion/test_completion_contract.py::test_occ02_from_json_rejects_non_array_identifier_fields；T/full_target/operation_completion/test_completion_contract.py::test_occ02_primitive_mixed_root_is_a_complete_spec_scope；T/full_target/operation_completion/test_completion_contract.py::test_occ02_scope_coverage_and_owner_are_checked_across_real_scope_documents；T/full_target/operation_completion/test_completion_contract.py::test_occ02_scope_revalidation_refuses_missing_or_duplicate_effect_owner；T/full_target/operation_completion/test_completion_contract.py::test_occ02_spec_rejects_unapproved_or_ambiguous_document_shape；T/full_target/operation_completion/test_completion_contract.py::test_occ02_strict_spec_codec_preserves_a_approved_effect_mapping。 | `test_completion_mapping_missing_or_ambiguous` → 并入有记录；承接：T/full_target/operation_completion/test_completion_plan_commit.py::test_occ09_no_plan_is_published_before_the_completion_mapping_is_confirmed（:146）；出处：test_completion_contract.py:11 |
| OCC-03 | 真实输入桥、Effect Acceptance、Contribution和Delivery关联一致；根Review前不能 | 并入代表用例（有记录，代表用例存在） |  | `test_exact_effect_reaches_outcome_review_and_completion` → 并入有记录；承接：T/product_world/test_operation.py::test_a_publishing_mission_completes_on_the_product_deployment（:68，代表用例 3）；出处：test_completion_contract.py:9 |
| OCC-04 | 每个变体均不能完成；不能用operation_id非空/SENT/CONFIRMED越过完整链；原真实效果事实仍归档。 | 并入代表用例（有记录，代表用例存在） |  | `test_every_outcome_identity_axis_is_checked` → 并入有记录；承接：T/full_target/operation_completion/test_publish_variants.py::test_a_tampered_outcome_binding_is_never_accepted（:480）；出处：test_completion_contract.py:9 |
| OCC-05 | 旧证据不适配新要求/输入；历史保留，不自动重发；无关plan变化按局部身份/Validity复核，不伪造新要求。 | 已删（有决定/记录） |  | `test_requirement_input_revocation_blocks_old_contribution` → 已删（有记录，与分诊表不符）；承接：无；出处：test_completion_contract.py:12"OCC-05 删：要求书第 2 版产品上无写入方"；分诊表:281 原写改为"要求/输入撤销"变体 |
| OCC-06 | A不能完成B；待办可见；克隆A的binding用于B拒绝；A/B独立合格并经根验收后才满足。 | 并入代表用例（有记录，代表用例存在） |  | `test_multiple_required_effects_are_not_collapsed` → 并入有记录；承接：T/full_target/operation_completion/test_publish_variants.py::test_two_required_publishes_each_need_their_own_chain（:420）；出处：test_completion_contract.py:10 |
| OCC-07 | 新报告正常完成且不发明动作；legacy不写新增表/事件、不改变canonical结果；缺Spec的新lane不可伪装l | 已删（有决定/记录） |  | `test_report_only_and_legacy_remain_compatible` → 已删（有决定）；承接：无；出处：test_completion_contract.py:12-13"随旧通道删"；分诊表:281 F 1 |
| OCC-08 | 完整Root Completion gate仍拒绝缺少匹配effect proof的完成；拒绝发生在写事务，状态/预算未 | 并入代表用例（有记录，代表用例存在） |  | `test_direct_final_commit_cannot_skip_effect_gate` → 并入有记录（承接弱）；承接：T/full_target/operation_completion/test_publish_variants.py::test_content_acceptance_…（:97）；出处：test_completion_contract.py:9"OCC-01/08/12 → 第一条"；该条不直接调最终提交 |
| OCC-09 | 无孤立的可消费接受或假完成；rollback保留原事实；重启同command查原receipt，不再次执行connect | 并入代表用例（有记录，代表用例存在） |  | `test_scope_acceptance_delivery_and_receipt_are_atomic` → 并入有记录；承接：T/full_target/operation_completion/test_publish_variants.py::test_an_effect_acceptance_write_failure_rolls_back_and_is_written_once_after_restart（:520）；出处：test_completion_contract.py:10 |
| OCC-10 | CONTENT不会等待所有siblings效果；MIXED不会因共享Obligation的别个接受完成；DATA按范围可 | 并入代表用例（有记录，代表用例存在） |  | `test_shared_obligation_uses_per_occurrence_completion_scope` → 并入有记录；承接：T/full_target/operation_completion/test_completion_plan_commit.py 范围断言（:55/:95）；出处：test_completion_contract.py:10-11 |
| OCC-11 | 复用原fact/request/Review/command身份；不重复Operation/不伪造Reviewer At | 并入代表用例（有记录，代表用例存在） |  | `test_outcome_review_callback_cold_replay` → 并入有记录；承接：T/full_target/operation_completion/test_publish_variants.py::test_an_effect_acceptance_write_failure_…（:520）；出处：test_completion_contract.py:10 |
| OCC-12 | 不因为worker队列空自动完成或连续重开Worker；正式Review/reconcile可推进；超时取消不写成功、不 | 并入代表用例（有记录，代表用例存在） |  | `test_effect_pending_is_work_not_false_completion_or_retry` → 并入有记录；承接：T/full_target/operation_completion/test_publish_variants.py::test_content_acceptance_…（:97）；出处：test_completion_contract.py:9 |

### 8. inherited-coverage.json（66 条；回写版）

注：CA-I10"外部评分器隔离"回写时映射到 C08（迟到记账），语义对不上，等于这条从来没有对应用例；X 组 18 条的候选承接套件 9 个里有 3 个已不存在（`T/full_target/test_provider_grant_rehandoff.py`、`T/full_target/test_h1h_operation_current_gates.py`、`T/full_target/operation_completion/test_scoped_reconciliation.py`）。部分 X 语义在新用例里有疑似承接但未登记，例如 X04（效果发生丢回执）≈ `PV::test_a_lost_reply_is_reconciled_and_never_resent`、X10（取消后迟到 APPLIED）≈ `T/product_world/test_operation.py::test_a_cancelled_mission_names_its_unsettled_publish_and_says_when_it_is_known`。

| 行（id/名字） | 计划要求 | 现状 | 代码/用例依据 | 说明 |
|---|---|---|---|---|
| CA-A01 | 空成功公式：codec拒绝，不生成已满足目标 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_formula_truth_tables；T/full_target/assurance_exec/test_a_assurance.py::test_strict_boundary |  |
| CA-A02 | 悬空或重复准则：稳定字段错误；不静默忽略 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_formula_truth_tables；T/full_target/assurance_exec/test_a_assurance.py::test_strict_boundary |  |
| CA-A03 | 替代路线不误阻塞：有效B可满足公式；仍核对A的未决动作 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_any_branch_not_mandatory |  |
| CA-A04 | 强制约束不被OR绕过：Commit拒绝，保留失败和审阅记录 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_any_branch_not_mandatory |  |
| CA-A05 | 未知不当成功：Review可正常保存，但Task不被接受 | 部分存在（其余已删/并入，有记录） | 现存：T/full_target/assurance_exec/test_a_assurance.py::test_formula_truth_tables。 | `test_direct_final_commit_guard` → 已删（有记录，但与分诊表不符）；承接：无；出处：test_a_assurance.py:10-11 记"A18 删：要关有效性服务才碰得到（裁决①不许）"；分诊表:136 原写"并入代表用例 3 变体" |
| CA-A06 | 真实检查失败优先：不接受；原测试日志/执行身份保留 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_check_receipt_scope |  |
| CA-A07 | 审阅与作者隔离：被拒，不能改完批准自己的版本 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_reviewer_independence |  |
| CA-A08 | 旧产物Review：计费/保存H1历史但不能批准H2 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_scope_pinned_not_latest |  |
| CA-A09 | 要求修订：不解释为r2已满足，未影响成果按显式映射重判 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_requirement_authority；T/full_target/assurance_exec/test_a_assurance.py::test_scope_pinned_not_latest |  |
| CA-A10 | 无关分支变更：按相关readset允许，不要求全图所有version相同 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_scope_pinned_not_latest；T/full_target/assurance_exec/test_c_assurance.py::test_two_connection_concurrency |  |
| CA-A11 | 准则遗漏与伪回执：明确报错，有限格式修复计费不补PASS | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_review_source_binding；T/full_target/assurance_exec/test_a_assurance.py::test_strict_boundary |  |
| CA-A12 | 局部通过组合错误：拒绝父Resolution，子历史不删除 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_composition_not_all_children |  |
| CA-A13 | 预执行候选：内容候选可接受；已发送准则仍未满足 | 并入代表用例（有记录，代表用例存在） |  | `test_preparation_does_not_complete` → 并入有记录；承接：T/full_target/operation_completion/test_publish_variants.py::test_content_acceptance_is_preparation_only_until_the_publish_completes（:97，注释标 A13～A17）；出处：HTN补齐-阶段A撇-分诊表.md:136；test_a_assurance.py:9-10 头注释<br>`test_root_requirement_effect_catalogue` → 并入有记录；承接：T/full_target/operation_completion/test_publish_variants.py::test_content_acceptance_…（:97）；另 ::test_the_final_review_sees_the_accepted_root_effect_and_its_readback（:385）；出处：HTN补齐-阶段A撇-分诊表.md:136；test_a_assurance.py:9 |
| CA-A14 | 同候选重复接受：单个正式接受/下游消息，回执相同 | 存在 | T/full_target/assurance_exec/test_c_assurance.py::test_acceptance_atomic_faults |  |
| CA-A15 | 不同候选并发通过：按选择CAS只绑定一个；不覆盖另一个历史 | 存在 | T/full_target/assurance_exec/test_c_assurance.py::test_two_connection_concurrency |  |
| CA-A16 | 评审预算身份：各走正确账户；Judge不要求伪Worker Attempt | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_check_budget_and_format_bounds |  |
| CA-A17 | Mission收敛门：显示收尾/核对，不能悄悄完成 | 存在 | T/full_target/assurance_exec/test_c_assurance.py::test_closeout_and_notification |  |
| CA-A18 | 终态后查询回执：当前读取授权允许时返回旧回执，不创建新任务 | 存在 | T/full_target/assurance_exec/test_c_assurance.py::test_acceptance_atomic_faults；T/full_target/assurance_exec/test_c_assurance.py::test_closeout_and_notification |  |
| CA-A19 | 已删除敏感依据：保留非敏感事实和限制，不补造原文 | 已删（有决定/记录） |  | `test_restore_quarantine_and_current_reauthorization` → 已删（有决定 A″）；承接：剩 test_v_assurance.py::test_root_quarantine_and_current_read_authority（:670）只管根闸门；出处：6cf1643a"删离线备份与受管恢复"；用户口径受管恢复已删 |
| CA-A20 | 非法类型组合：预期类型错误/归属拒绝，非缺依赖假失败 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_strict_boundary |  |
| CA-K01 | 缺观察不是否定：UNKNOWN，不是FALSE | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_anchors_and_explicit_negation |  |
| CA-K02 | 显式反证：FALSE并给出负证据，不借未命中生成 | 存在（个别名换芯改名，有记录） | 现存：T/full_target/assurance_exec/test_v_assurance.py::test_anchors_and_explicit_negation。 | `test_insert_counterevidence_barrier` → 换芯改名（反证一半未做）；承接：T/full_target/assurance_exec/test_v_assurance.py::test_a_concurrent_source_change_is_an_insert_barrier（:532）；出处：分诊表:160 记"A 换芯"；新用例注释自述"反证观察那一半等带观察器的测试世界"；疑似由 T/product_world/test_desktop_preconditions.py::test_file_change_flips_observation_and_moves_epoch 承接（未登记） |
| CA-K03 | 冲突不爆炸：当前命题CONFLICT，无关项仍UNKNOWN | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_conflicted_paths |  |
| CA-K04 | AND支持不全：不成立/UNKNOWN；不可用 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_alternate_support；T/full_target/assurance_exec/test_v_assurance.py::test_anchors_and_explicit_negation |  |
| CA-K05 | OR独立支持：K可用C见证；不重做无关Worker | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_alternate_support |  |
| CA-K06 | 重复来源不算独立：不满足独立性要求 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_independent_provenance_roots |  |
| CA-K07 | 无锚循环：两者不自证TRUE | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_cycle_without_anchor |  |
| CA-K08 | 撤回循环锚：从当前锚重算，不能从旧TRUE续算 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_cycle_without_anchor |  |
| CA-K09 | 冲突路径另有替代：按无冲突见证C使用，A不用于强制门 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_conflicted_paths |  |
| CA-K10 | 规则不是引用列表：无合法RuleAdmission/独立Review不得启用 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_rule_admission_required |  |
| CA-K11 | 新增反证的幻读：集合epoch冲突并重判，不能只查旧正证据版本 | 换芯改名（有记录），反证观察一半未做 |  | `test_insert_counterevidence_barrier` → 换芯改名（反证一半未做）；承接：T/full_target/assurance_exec/test_v_assurance.py::test_a_concurrent_source_change_is_an_insert_barrier（:532）；出处：分诊表:160 记"A 换芯"；新用例注释自述"反证观察那一半等带观察器的测试世界"；疑似由 T/product_world/test_desktop_preconditions.py::test_file_change_flips_observation_and_moves_epoch 承接（未登记） |
| CA-K12 | 撤回与异步缓存：dirty gate立即阻断或最新同步重算 | 存在（个别名换芯改名，有记录） | 现存：T/full_target/assurance_exec/test_v_assurance.py::test_invalidation_racing_cache。 | `test_insert_counterevidence_barrier` → 换芯改名（反证一半未做）；承接：T/full_target/assurance_exec/test_v_assurance.py::test_a_concurrent_source_change_is_an_insert_barrier（:532）；出处：分诊表:160 记"A 换芯"；新用例注释自述"反证观察那一半等带观察器的测试世界"；疑似由 T/product_world/test_desktop_preconditions.py::test_file_change_flips_observation_and_moves_epoch 承接（未登记） |
| CA-K13 | 过期但定时器迟到：按now拒绝当前用途；后续事件便于重放 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_authority_and_expiry |  |
| CA-K14 | START与ACCEPT不同：不抹掉START；若用户要求当前值另行取证 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_authority_and_expiry；T/full_target/assurance_exec/test_v_assurance.py::test_read_certificate_context |  |
| CA-K15 | 权限与真值分开：禁止披露；不把事实写FALSE | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_all_consumers_share_validity；T/full_target/assurance_exec/test_v_assurance.py::test_authority_and_expiry |  |
| CA-K16 | 来源声称与现实：只确认source_says；实测主张另需证据 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_anchors_and_explicit_negation |  |
| CA-K17 | 范围/版本不同：不合并不同FactKey，不复用不匹配Acceptance | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_read_certificate_context |  |
| CA-K18 | 不完整依赖覆盖：标记coverage不足并保守复审，不称精确最小影响 | 存在 | T/full_target/assurance_exec/test_v_assurance.py::test_bounded_restartable_evaluation；T/full_target/assurance_exec/test_v_assurance.py::test_complete_collection_not_topk |  |
| CA-X01 | 同操作跨Attempt：同Operation与外部key，避免重复应用 | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X02 | 合法第二次同目标：不同Operation；不被旧target key合并 | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X03 | 同key不同参数：拒绝；不能覆盖原回执 | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X04 | 效果发生丢回执：读取原回执，一次应用且正确结算 | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X05 | handoff后延迟到达：保持UNKNOWN；不以瞬时not found重发 | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X06 | 幂等保存期耗尽：不自动再发；不能证明未发生 | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X07 | 旧writer仍活：无真实fence不能冲突新handoff | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X08 | 终局未应用后重试：同key/spec安全再发；新成功不与旧未执行矛盾 | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X09 | 查询权威不足：UNKNOWN而不是NOT_APPLIED_FINAL | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X10 | 取消后迟到APPLIED：保存真实后果/费用，不授权新动作 | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X11 | 矛盾权威收据：quarantine/conflict，无last-write-wins | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X12 | 补偿失败：原成功不删；补偿独立失败/UNKNOWN记录 | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X13 | 审批绑定过期：原批准不能用于H2，重新审批 | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X14 | SDK提交编排丢回执：原creation_key/input_id查回，不创建新工作 | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X15 | 结果导入重复：业务/费用导入一次；不同hash冲突 | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X16 | 未知费用后确认：保留未知预留到可信结算，不提前记0占死消费键 | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X17 | 观察失败不是不存在：ObservationUnavailable，不能生成新Attempt | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-X18 | 过期通知worker确认：不能确认新claim；相同消费者业务事实仍去重 | 缺失（有说明，无逐条映射） | — | 原 core_assurance nodeid 不存在于本仓，Assurance 1.1 计划也未给 X 组分配新用例 id。这些是既有 D3 操作/回执语义，由既有套件承接（候选文件见 candidate_suites），在 legacy |
| CA-I01 | 全部三链集成：版本/接受/真实效果/费用/通知一致 | 存在（个别名并入，有记录） | 现存：T/full_target/assurance_exec/test_c_assurance.py::test_closeout_and_notification。 | `test_exact_outcome_chain` → 并入有记录；承接：T/product_world/test_operation.py::test_a_publishing_mission_completes_on_the_product_deployment（:68）+ T/full_target/operation_completion/test_publish_variants.py:97（标 E03）；出处：HTN补齐-阶段A撇-分诊表.md:144 |
| CA-I02 | compound不伪执行：不创建假父Worker；实际组合审阅和GoalResolution | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_composition_not_all_children |  |
| CA-I03 | 相同scope多Manager：相关冲突被发现，不相关安全rebase成立 | 存在 | T/full_target/assurance_exec/test_c_assurance.py::test_two_connection_concurrency |  |
| CA-I04 | 安全注入跨共享：引用始终是数据，权限和操作key不可模型控制 | 存在 | T/full_target/assurance_exec/test_a_assurance.py::test_extra_evidence_exposure；T/full_target/assurance_exec/test_a_assurance.py::test_review_source_binding；T/full_target/assurance_exec/test_v_assurance.py::test_all_consumers_share_validity |  |
| CA-I05 | 真实UI与坏消息：保留stale画面，不能显示完成或业务UNKNOWN | 存在 | T/full_target/assurance_exec/test_c07_host_api.py::test_contract_checker_rejects_drift；T/full_target/assurance_exec/test_c07_host_api.py::test_snapshot_review_use_check_contracts_and_errors；tauri-app/src/views/MissionAssurance.test.tsx (9 个 vitest 用例，第九段证据 frontend-vitest-typecheck-eslint.txt) |  |
| CA-I06 | 旧库冷恢复：字节与身份不变，legacy规则不被猜成v2 | 存在 | T/full_target/assurance_exec/test_c_assurance.py::test_real_migration_and_legacy |  |
| CA-I07 | 新投影重建：重建相同事实，无模型/工具调用；coverage边界明确 | 存在 | T/full_target/assurance_exec/test_c_assurance.py::test_event_cursor_atomicity；T/full_target/assurance_exec/test_v_assurance.py::test_invalidation_racing_cache |  |
| CA-I08 | 跨库备份缺资产：禁新副作用并报告缺口，不重发弥补 | 部分存在（其余已删/并入，有记录） | 现存：T/full_target/assurance_exec/test_c_assurance.py::test_real_migration_and_legacy；T/full_target/assurance_exec/test_c_assurance.py::test_review_cold_resume。 | `test_restore_quarantine_and_current_reauthorization` → 已删（有决定 A″）；承接：剩 test_v_assurance.py::test_root_quarantine_and_current_read_authority（:670）只管根闸门；出处：6cf1643a"删离线备份与受管恢复"；用户口径受管恢复已删 |
| CA-I09 | 删除后恢复备份：不复活正文/索引/缓存，历史限制可见 | 已删（有决定/记录） |  | `test_restore_quarantine_and_current_reauthorization` → 已删（有决定 A″）；承接：剩 test_v_assurance.py::test_root_quarantine_and_current_read_authority（:670）只管根闸门；出处：6cf1643a"删离线备份与受管恢复"；用户口径受管恢复已删 |
| CA-I10 | 外部评分器隔离：看不到隐藏答案；错误完成由外部评分独立判定 | 并入代表用例（有记录，代表用例存在） |  | `test_late_accounting_is_separate` → 并入有记录；承接：T/full_target/assurance_exec/test_review_turn_retry_e2e.py::test_provider_server_error_first_turn_is_retried_once_and_imported（注释"外加 C08"）；另 T/product_world/test_late_usage.py；出处：分诊表:140 |
### 9. mutations.json（计划 16 条 M01～M16）

计划只写了改坏的意图和应当杀死它的用例 id，没有改坏原文；实际执行的是另起的 22 条 AM（`sdk/simple-harness-sdk/plans/assurance-1.1/mutations.json`，WIP 第十段（三）），**两者之间没有登记映射**。下表"近似 AM"是我按语义对的，不是记录。

| 行（id/名字） | 计划要求 | 现状 | 代码/用例依据 | 说明 |
|---|---|---|---|---|
| M01 mandatory_bypass | mandatory gate改成ANY可忽略；须被 A02,A05,A18 杀死 | 未按计划执行；有近似 AM | 杀手用例现状：A02:存在、A05:存在、A18:已删 | AM02（强制准则 AND） |
| M02 fake_official | 信模型official而不验dispatch；须被 A07,A08 杀死 | 未按计划执行；有近似 AM | 杀手用例现状：A07:存在、A08:存在 | 近似 AM06/AM07/AM08（原始字节 hash、调用序号、评审者≠作者），无"信模型 official"原样变异 |
| M03 wrong_subject_check | 删除check目标/manifest/assertion绑定；须被 A04 杀死 | 未按计划执行；有近似 AM | 杀手用例现状：A04:存在 | AM03/AM04（他 spec 回执冒充、来源无效） |
| M04 first_current_acceptance | 根只找任意CURRENT Acceptance；须被 A13,A17,A18 杀死 | 未按计划执行；有近似 AM | 杀手用例现状：A13:并入、A17:并入、A18:已删 | 近似 AM10（证书绑定本次接受身份），AM10 目标用例已删 |
| M05 empty_effect_catalog | 无intent时effects=[]；须被 A17,E01 杀死 | 未按计划执行；有近似 AM | 杀手用例现状：A17:并入、E01:并入 | 近似 AM13（准备≠完成），AM13 目标用例已并入 |
| M06 effect_receipt_nonempty | Delivery只校operation_id非空；须被 E04,E07 杀死 | 未执行（无对应） | 杀手用例现状：E04:并入、E07:缺失 | 无对应 AM |
| M07 latest_input | 旧Review读取latest输入；须被 A11,A14 杀死 | 未按计划执行；有近似 AM | 杀手用例现状：A11:存在、A14:并入 | 近似 AM12（需求已变旧作用域不接受） |
| M08 empty_read_success | 失败查询返回COMPLETE空；须被 V06,V09 杀死 | 未执行（无对应） | 杀手用例现状：V06:存在、V09:存在 | 无对应 AM（完整读失败当空集） |
| M09 no_negative_epoch | 新反证不bump屏障；须被 V07 杀死 | 未按计划执行；有近似 AM | 杀手用例现状：V07:换芯改名 | AM20（观察表插入推动纪元），AM20 目标用例已换芯且不再插观察 |
| M10 cached_anchor_cycle | 用旧VERIFIED作闭包锚；须被 V03 杀死 | 未按计划执行；有近似 AM | 杀手用例现状：V03:存在 | 近似 AM22（非 CURRENT 候选不作锚点） |
| M11 conflict_path_allowed | 删除clean closure校验；须被 V05 杀死 | 未执行（无对应） | 杀手用例现状：V05:存在 | 无对应 AM |
| M12 purpose_ignored | 删除consumer/purpose/scope绑定；须被 V10 杀死 | 未按计划执行；有近似 AM | 杀手用例现状：V10:存在 | 近似 AM14（非 USABLE 证书不过绑定检查） |
| M13 inner_commit | Acceptance内部提前commit；须被 C01,C02 杀死 | 未执行（无对应） | 杀手用例现状：C01:存在、C02:存在 | 无对应 AM（接受内部提前 commit） |
| M14 ack_before_intent | cursor先于真实pending工作持久化；须被 C04 杀死 | 未按计划执行；有近似 AM | 杀手用例现状：C04:存在 | 近似 AM16（游标版本过期冲突） |
| M15 lease_means_absent | lease过期直接重发模型/操作；须被 C03,C05 杀死 | 未执行（无对应） | 杀手用例现状：C03:存在、C05:存在 | 无对应 AM（租约到期直接重发） |
| M16 release_unknown_cost | 未知用量记0释放reservation；须被 C05,C08 杀死 | 未执行（无对应） | 杀手用例现状：C05:存在、C08:并入 | 无对应 AM（未知用量记 0 释放） |

小计：{'未按计划执行；有近似 AM': 10, '未执行（无对应）': 6}


### 10. additional-mutations.json（计划 12 条 F-M01～F-M12）

杀手用例是 F03～F12，来自 `findings-sdk-tests.json`（F01～F15，目标文件 `T/full_target/assurance_exec/test_review_findings.py`）。**该文件从未存在**（`git log -S test_review_findings` 只有计划包入库的两次提交），F 反例 15 条与 F-M 12 条都没执行，WIP / HANDOFF 第 10 项没有把它们列为"未做"，第一遍对照也没提。

| 行（id/名字） | 计划要求 | 现状 | 代码/用例依据 | 说明 |
|---|---|---|---|---|
| F-M01 | key evidence by id alone；须被 F03 杀死 | 没做（杀手用例不存在，无记录） | `test_review_findings.py` 不存在 | 近似 AM03（检查身份） |
| F-M02 | ERROR -> PASS；须被 F04 杀死 | 没做（杀手用例不存在，无记录） | `test_review_findings.py` 不存在 | 近似 AM04（来源无效不是 PASS） |
| F-M03 | SEMANTIC requires synthetic True check；须被 F04 杀死 | 没做（杀手用例不存在，无记录） | `test_review_findings.py` 不存在 | 近似 AM01/AM02 |
| F-M04 | key all invocations by Task subject；须被 F05 杀死 | 没做（杀手用例不存在，无记录） | `test_review_findings.py` 不存在 | 无 |
| F-M05 | old judge directly releases pools；须被 F06 杀死 | 没做（杀手用例不存在，无记录） | `test_review_findings.py` 不存在 | 无（judge_mission 非保证尾巴仍在，见 terminal_writers） |
| F-M06 | restored binding treated as current auth；须被 F07 杀死 | 作废（有决定） | `test_review_findings.py` 不存在 | 作废（受管恢复已删 A″） |
| F-M07 | put_source bypass epoch；须被 F08 杀死 | 没做（杀手用例不存在，无记录） | `test_review_findings.py` 不存在 | 近似 AM20（但 put_source 走 SQL 触发器） |
| F-M08 | cursor advances before pending insert；须被 F09 杀死 | 没做（杀手用例不存在，无记录） | `test_review_findings.py` 不存在 | 近似 AM16 |
| F-M09 | allow initial FINALIZED；须被 F11 杀死 | 没做（杀手用例不存在，无记录） | `test_review_findings.py` 不存在 | 无（有 SQL 触发器 assurance_closeout_initial 守着） |
| F-M10 | allow final DELETE/REPLACE；须被 F11 杀死 | 没做（杀手用例不存在，无记录） | `test_review_findings.py` 不存在 | 无（有 *_no_delete / no_replace 触发器守着） |
| F-M11 | allow BOUND pin without review；须被 F11 杀死 | 没做（杀手用例不存在，无记录） | `test_review_findings.py` 不存在 | 无（有 assurance_blob_pin_bound_review 触发器守着） |
| F-M12 | drop resolved schema hashes；须被 F12 杀死 | 没做（杀手用例不存在，无记录） | `test_review_findings.py` 不存在 | 无 |

小计：没做（杀手用例不存在、无记录）11，作废（有决定）1。

F01～F15 反例本身（`findings-sdk-tests.json`，不在本次 55+… 的点名范围，但 F-M 依赖它）：15 条目标全在不存在的 `test_review_findings.py`，`actual_nodeids` 全空、`PENDING_SDK_EXECUTION`。其中 F07（恢复）随 A″ 作废；F10（默认开启）、F13（原生 Host）、F15（继承+模型 oracle）的意图分别由默认开启实现、第一遍记的"原生点击没做""真实模型 12 局没跑"覆盖；其余 11 条（F01～F06、F08、F09、F11、F12、F14）没有对应用例，也没有任何文档说没做。


### 11. 附：实际执行的 22 条 AM 现状（第一遍引用的就是这批）

| AM | 守卫（文件） | 改坏原文现在 | 目标用例现在 | 说明 |
|---|---|---|---|---|
| AM01 | 非 ACCEPT 判决不得可接受（assurance/checks.py） | 在 | 在 |  |
| AM02 | 强制准则必须 AND 在公式之上（assurance/checks.py） | 在 | 在 |  |
| AM03 | 他 spec 的回执不得冒充（assurance/checks.py） | 在 | 在 |  |
| AM04 | 来源无效的检查结果不是 PASS（assurance/checks.py） | 在 | 在 |  |
| AM05 | ANY 取冻结公式顺序的首个 PASS，不是模型排序（assurance/checks.py） | 在 | 在 |  |
| AM06 | 原始输出字节必须与钉住的 hash 一致（assurance/reviews.py） | 在 | 在 |  |
| AM07 | 调用序号只允许 1（原始）/2（格式修复）（assurance/reviews.py） | 在 | 在 |  |
| AM08 | 评审者不得是作者（orchestrator/assurance_review_import.py） | 在 | 在 |  |
| AM09 | Attempt 终态后 TASK_CONTENT 评审主题已停止（orchestrator/assurance_review_import.py） | 不在（[0]） | 在 | 原文不在：守卫在 TaskGraph 第四批 f6370f85 改写，现为 `assurance_review_import.py:152` `return task.status in TERMINAL_TASK or attempt.status in TERMINAL_ATTEMPT`，守卫还在，变异文本过期 |
| AM10 | 使用证书必须绑定本次接受身份（orchestrator/resolution_commits.py） | 在 | 不在 | 目标 A18 已删；守卫原文 `resolution_commits.py` 仍在（count=1），**现在没有登记的用例会杀它** |
| AM11 | profile 不得替用户降级里程碑（runtime/operation_profiles.py、orchestrator/operation_materialization_inputs.py） | 不在（[0, 1]） | 不在 | 两处原文一处不在：`operation_profiles.py` 在 HTN B3 d963b8c0 改写为 `:220 effect.required_milestone`；目标 E02 用例已删；疑似由 test_operation.py:182 承接（其注释自带改坏检验） |
| AM12 | 需求已变时旧作用域不得继续接受（orchestrator/operation_completion.py） | 在 | 在 |  |
| AM13 | 内容准备就绪不等于完成（orchestrator/completion_status.py） | 在 | 不在 | 目标 A13 已并入 test_publish_variants.py:97；未在新用例上重跑 |
| AM14 | 非 USABLE 证书不得通过绑定检查（assurance/certificates.py） | 在 | 在、在 |  |
| AM15 | 检查锚点上限 256 必须生效（knowledge/assurance_sources.py） | 在 | 在 |  |
| AM16 | 游标版本过期的摄取必须冲突（storage/assurance_work.py） | 在 | 在、在 |  |
| AM17 | 纪元移动后必须重查（storage/assurance_reads.py） | 在 | 在、在 |  |
| AM18 | 不可变行的不同内容重放必须冲突（storage/assurance_store.py） | 在 | 在 |  |
| AM19 | 分页快照变化必须让 Host 重来（api/assurance.py） | 在 | 在 |  |
| AM20 | 观察表插入必须推动有效性纪元并发事件（storage/assurance_source_inventory.py） | 在 | 不在 | 目标 V07 换芯改名为 test_a_concurrent_source_change_is_an_insert_barrier，新用例改由"确认完成映射"触发，不再插观察行——**改坏观察表触发器大概率杀不死**；疑似 test_desktop_preconditions.py::test_file_change_flips_observation_and_moves_epoch 能杀（未验） |
| AM21 | 过期证书不得通过（assurance/certificates.py） | 在 | 在 |  |
| AM22 | 非 CURRENT 候选不得成为锚点（knowledge/justifications.py） | 在 | 在 |  |
### 12. 小结

#### 12.1 各资产统计

| 资产 | 行数 | 统计 |
|---|---|---|
| integration-map.json（seams 26 + reviews 6 + terminal 8 + invalidation 8 + consumers 4 + restore 3） | 55 | 在用 36、做法不同已记录 10、做法不同未记录 2、已删有决定 6、不适用 1、已删无记录 0 |
| seams.json | 26 | 与 integration-map.seams 逐字相同，合并核：在用 13、做法不同已记录 9、做法不同未记录 2、已删有决定 2 |
| event-consumer-map.json | 7 | 在用 4、部分在用（REVIEW 侧做法不同未记录）2、做法不同未记录 1 |
| ref-resolution-map.json kinds | 29 | 在用 22、有解析器但生产无构造 3、没做 4 |
| ref-resolution-map.json field_contracts | 9 | 在用 7、做法不同未记录 2 |
| BODY-WIRED.md | 16 | 在用 13、做法不同未记录 1（BW10）、已删有决定 1（BW14）、部分 1（BW16） |
| sdk-cases.json | 48 | 存在 32、并入代表用例（有记录，存在）9、已删有决定/记录 3、换芯改名（反证一半未做）1、承接不明 1、缺失（记录与实际不符）2 |
| occ-coverage.json | 12 | 并入（有记录，存在）9、存在（个别名并入）1、已删（有记录）2 |
| inherited-coverage.json | 66 | 存在 38、存在（个别名并入/换芯）3、部分存在（其余已删/并入有记录）2、并入（有记录）2、换芯改名（反证一半未做）1、已删（有决定 A″）2、缺失（有说明无逐条映射，X 组）18 |
| mutations.json（计划） | 16 | 未按名执行：有近似 AM 10、完全无对应 6 |
| additional-mutations.json | 12 | 没做（杀手用例不存在、无记录）11、作废（恢复已删）1 |

#### 12.2 所有不在"在用 / 已记录 / 存在 / 并入有记录"里的行（每条一句影响）

| 行 | 状态 | 影响 |
|---|---|---|
| seams S20、S22 | 做法不同未记录（仅位置） | 无功能影响；资产写错文件，接手人按资产找不到 |
| terminal TW4 / seam S21 `judge_mission` | 在用但残留非保证直写 COMPLETED 尾巴 | 现在走不到（每个任务都装保证通道），但违背"唯一终写"，且没有用例守着（见 A18） |
| terminal TW7 | 调用点清单未成文 | 我 grep 只有两处写 COMPLETED；没有清单，以后新增直写不会被发现 |
| event-map CANDIDATE_READY、BUSINESS_OR_RUNTIME_SETTLED（REVIEW 侧） | 做法不同未记录 | 原入口直接触发审阅，等价；没写成偏差 |
| event-map ACTUAL_CHECK_AVAILABLE（REVIEW 侧） | 做法不同未记录 | 检查在审阅准备里同步跑，晚到的检查不会唤醒审阅；当前无此场景 |
| ref kinds observation / resolution / completion_spec | 有解析器，生产无人构造 | 死代码，不影响结论 |
| ref kinds tool_receipt / policy / authority / capability | 没做 | 第一遍已记；tool_receipt 仍被 OPERATION_OUTCOME 允许，一旦有人构造就报 REF_KIND_UNSUPPORTED |
| field blob-pin source/last_receipt_ref | 做法不同未记录 | SQL 外键 + 回执 hash 核对，等价，无影响 |
| BW10 | 做法不同未记录 | 第一遍差距 1：做法审阅/操作提案采用时不核使用证书，可能判错 |
| BW16 | 部分 | ARCHITECTURE/STATUS 未回写，第一遍已记 |
| sdk-cases A18 / OCC-05 | 已删，但分诊表写"并入" | 记录互相矛盾；A18 删后"直接调终提交绕闸"无用例 |
| sdk-cases A16 | 并入，分诊表写"删" | 记录互相矛盾，代表用例存在 |
| sdk-cases V07 / inherited CA-K11 | 换芯改名，反证一半未做 | "新增反证（负观察）同事务挡旧证书"没有登记用例 |
| sdk-cases E02 | 承接不明 | 里程碑不被档案降级——疑似 `test_operation.py:182` 承接，未登记 |
| sdk-cases E06 | 缺失（分诊表写并入） | 晚到事实/要求变更后旧回执不得复用——无用例；产品上要求书第 2 版暂无写入方，现实风险低 |
| sdk-cases E07 | 缺失（分诊表写并入） | 绕过 T3 直写交付回执不能算效果完成——无用例，守卫有无回归无证据 |
| inherited X01～X18 | 缺失（有说明，无映射） | 同一操作跨尝试不重复、外部键防重等 18 条没逐条用例；候选套件 3 个已删 |
| inherited CA-I10 | 映射语义错配 | "外部评分器隔离"被映射到迟到记账，实际从没有对应用例 |
| mutations M01～M16 | 未按名执行（10 近似 / 6 无对应） | M06、M08、M11、M13、M15、M16 六类改坏没有任何变异证明被测试抓住 |
| additional F-M01～F-M12（除 F-M06） | 没做，无记录 | 11 类改坏（含"旧 judge 直接放行""put_source 绕纪元""游标先于待办"）没跑；其中 F-M09/10/11 有 SQL 触发器兜底 |
| AM09、AM11 | 改坏原文过期 | 守卫仍在（被改写），变异脚本跑不了，保护无新证据 |
| AM10 | 目标用例已删 | 接受时证书必须绑定本次接受身份的守卫，现在没有登记用例会抓 |
| AM13 | 目标并入 | 未在新代表用例上重跑 |
| AM20 | 目标换芯改名 | 新用例不再插观察行，改坏观察表触发器大概率抓不住 |

### 13. 与第一遍对照文档的矛盾

| 第一遍说法（`Assurance-现状对照-2026-10-05.md`） | 本次核实 |
|---|---|
| 覆盖清单"约 39 处指向 A′ 已删用例""用例多数已并入代表用例，有记录"（:42,409-412,508） | 数字对：sdk-cases 16 + OCC 12 + 继承 11 = 39。构成是：并入有记录 23、已删 9、换芯改名 4、承接不明 1、**真缺失 2（E06、E07，分诊表写的是"并入"）**；另有 3 处分诊表与测试头注释说法相反（A16 表写删实为并入；A18、OCC-05 表写并入实为删） |
| OCC"清单里 12 个用例名全部失效"（:412） | 11 行全失效；**OCC-02 的 52 个用例名里 51 个仍在**，只有运行时那 1 个并入 `test_completion_plan_commit.py:146` |
| 继承 X 组"18 条从没映射也没执行"（:41,411,504） | 一致（18 条 `NOT_REMAPPED`）；补充：执行说明里"由既有套件承接"的 9 个候选套件已有 3 个被删，这句说明已不成立 |
| 定点变异"16+ 验收已做，22/22 抓到；现状过期：2 条原文不在、4 条目标已删"（:42,413,509） | 过期数字对，但**"验收已做"不成立**：计划点名的 M01～M16 没有一条按名执行，22 条 AM 是另起的一套且无映射，6 条 M 无任何对应；additional 12 条 F-M 与 F01～F15 反例从未写/跑，第一遍完全没提。另：AM09/AM11 守卫仍在只是被改写，AM20 目标不是"删了"而是换芯改名后不再测原语义 |
| 48 组确定性场景"验收已做"（:409） | 现在 48 组里 A18、V13、E08 已删，E06、E07 无承接，E02 承接不明，V07 只剩一半——不能再说 48 组都有用例守着 |
| `integration-map.json` 单 owner 映射"没做"、BODY_WIRED 16 条"没做"（:402,441） | 作为"资产没回写/没逐条登记关闭"成立；但逐行看代码，55 行里 46 行在用或做法不同已记录，16 条边 13 条接通、1 条按决定删——是"做了没回写"，不是"没做" |
| "没有发现'删了没记录'"（一句话结论） | 有：F 反例 15 条 + F-M 12 条从未执行且无"没做"记录；E06/E07 删了而记录写"并入"；3 类引用（observation/resolution/completion_spec）有解析器但无生产构造，第一遍只提了另外 4 类 |


---

## 附五 · 反方复核

## Assurance 现状对照 · 对抗式复核（2026-10-05）

- 复核对象：`plans/2026-09-27-desktop-next/Assurance-现状对照-2026-10-05.md`（第一遍，243 条）。
- 代码版本：main HEAD `5bae3e92`（在第一遍提交 67fe1f5c 之后多了 46448d9b"资料换版本真机重跑"和一次合并，Assurance 相关源码没有变化）。只读复核，没有改源码，也没有跑测试。
- 路径简写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`，`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`，`Host/` = `backend/deskpet/orchestration/`，`WIP` = `sdk/simple-harness-sdk/plans/assurance-1.1/BODY-WORK-IN-PROGRESS.md`。
- 分工：非"在用"的 77 行和两条重要结论由我逐条核对。"在用"抽查共 56 行，由两位 Opus 子代理分前后两段只读核对，部分成立的条目我又亲自复核过。

**改判合计 14 条**，按方向分：
- 在用 → 做法不同未记录：6 条
- 在用 → 做法不同已记录：3 条
- 在用 → 部分没做：2 条
- 做法不同未记录 → 做法不同已记录：1 条
- 做法不同未记录 → 收窄并降级（一半其实在用）：1 条
- 做法不同已记录 → 已删（有决定）：1 条

---

### 一、改判表

| 第一遍条目 | 第一遍判 | 复核判 | 依据 | 理由 |
|---|---|---|---|---|
| I7 本机提交的撤回立即让旧使用证书失效 | 在用 | **做法不同未记录**（部分成立） | `SDK/orchestrator/assurance_consumers.py:360`（收尾的 `VOLATILE` 含 `epochs`，纪元不参与比较）、`:458-462`；`SDK/orchestrator/assurance_recheck.py:43-58`（只重读证书里钉住的 OBJECT）；`SDK/orchestrator/assurance_validity.py:461-473`（钉住对象里没有资料 source，资料只进 `blobs`）；`SDK/storage/assurance_reads.py:149`（精确读"不断言当前可用"） | 接受和目标结论两个消费点会因纪元变化被挡住（`require_current_locked`），这部分成立。但收尾时不比纪元，复查的钉住对象里也没有资料。**根结论形成后、FINALIZED 之前撤回资料或换资料版本，不会拦住完成。**原计划 §7.2 要求"最终事务重读当前 epoch…才 READY→FINALIZED"。opt.110 记了"不比纪元、只比钉住对象"，但没记"资料不在钉住对象里，所以撤回不拦收尾"这个后果 |
| C1.11 `commit_assurance_use_locked` | 在用 | **做法不同未记录**（部分成立） | `SDK/orchestrator/resolution_commits.py:1746,1795,1849` 是唯一的调用者 | 接受、组合、根三处成立。收尾 finalize 不经过它，也没有等价的纪元比对（与 I7 同源） |
| 7.12 默认成功策略："依据失效 → NOT_READY" | 在用 | **做法不同已记录** | `assurance_recheck.py`；`PLAN-STATUS.md:524`（opt.110 第 4 项） | "依据失效"不按纪元判，改成逐项重读钉住对象，由规划器决定是否重做，并且不写 `goal_resolutions.validity`（用户红线）。有记录，但遗留上一行 I7 的缺口 |
| 7.14 最终事务重读当前 epoch/权限/effect 才 READY→FINALIZED | 在用 | **做法不同已记录** | `assurance_consumers.py:360,723-731`；opt.110 记录 | 重评时纪元被列为可忽略字段，权限也不比（"没有权限评估器，不比"，见 `assurance_recheck.py` 模块注释）。不回退、不删了重建这部分成立 |
| 7.10 唯一终态写入口 | 在用 | **做法不同未记录**（残留旧路） | `SDK/orchestrator/commit_service.py:3137-3159`；`SDK/orchestrator/assurance_final_writer.py:42-47` `is_assured` | `is_assured` 出错时一律当成"不走保证通道"，然后走旧尾巴直接写 COMPLETED。这是"出错就放行"的写法，唯一写方并不是结构上保证的。提交 eb749104 自称"否则走旧路 B 的分支全部删 B"，这一处漏删，也没有记录。产品里走不到（见第三节 (b)） |
| 7.9 `update_task/update_mission` 只能由合法 Commit 调用，要枚举并用测试证明没有旁路 | 在用 | **部分没做** | `update_mission` 调用点：`commit_service.py:818,1457,1485,1515,3151,3166,3449`、`plan_commits.py:1598`、`assurance_final_writer.py:94,182`；`T/full_target/assurance_exec/` 下没有旁路枚举测试 | 代码层面写 COMPLETED 的仍有两处（final writer 和 3151 旧尾巴）。原计划明确要求的"枚举 + 测试证明"不存在，第一遍自己也写了"没看到"，却判了在用 |
| §11 缺分类/来源矛盾 → `CREATION_CONTRACT_UNRESOLVED` | 在用 | **做法不同未记录**（部分成立） | `SDK/storage/assurance_store.py:249-258` 会报错；`assurance_final_writer.py:44-47` 吞掉报错 | 读通道和重复建任务校验都会报这个错。但 `is_assured` 把它和 `ASSURANCE_PROFILE_UNBOUND` 一起吞成"否"，等于"缺判别行当老任务"，原计划禁止这样。靠循环入口检查 `event_handler.py:1537-1556` 兜底（与 7.10 同源） |
| §10 状态文件缺失/不匹配 → 隔离 | 在用 | **做法不同未记录** | `SDK/deployment/assembly.py:65-75`；`SDK/orchestrator/assurance_root_commits.py:138-173`（回执号由"根路径+租户+身份+命令号"决定，每次启动都一样） | Host 每次启动按部署身份重装根，会用库里已有的回执把状态文件写回去。所以状态文件缺失、不匹配、同一路径换回旧库，在产品里都会被自动修好，不会进隔离。只有"库里没回执但状态文件还在"这一种会让启动失败。A″ 删的是离线备份和受管恢复，没写"换回旧库会被自动接受" |
| §13.2 启动记录导入身份、wheel 哈希、Host/SDK 指纹，字节不同不混用 | 在用 | **做法不同未记录**（小） | `backend/deskpet/orchestration/manifest.py:104-138`（只核版本号）；`Host/assurance.py:34-52`（wheel 哈希是钉死的常量，"脏"只是一个开关位）；`backend/deskpet/sdk_adapters/composition.py:362`（运行时组装才核字节） | "字节不同不混用"在运行时组装那一步仍然成立。但编排服务启动时只核版本号；`host_fingerprint` 不是实测值，两份内容不同的脏构建会得到同一个指纹 |
| §12.1 严格 JSON：未知字段拒绝等 | 在用 | **做法不同已记录** | `SDK/assurance/checks.py:380-405` `decode_review_reply` | 编解码本身从严，但审阅员回复放宽了两处：允许整段包一层代码围栏，允许值为空的多余字段。用户 2026-10-02 定（代码注释写明"格式口径"，第一遍口径里也列了"审阅员回复放宽两处"），第一遍没在这一行反映 |
| §15.1 同交付回写 `AGENT_ORCHESTRATION.md`/`ASSURANCE.md`/`index.md`/`PROJECT_STATUS.md` | 在用 | **部分没做** | `ARCHITECTURE/index.md:1-3` 停在 09-26；`ARCHITECTURE/ASSURANCE.md:1` 停在 10-02 | 四份都有保证线条目，但 `index.md` 也没记 10-03 的两次删除（第一遍只点了 `ASSURANCE.md`），仍把已删的 `assurance_profile` 开关写成当时状态 |
| 后段"8MiB 记录读上限" | 做法不同未记录 | **做法不同已记录** | `ARCHITECTURE/ASSURANCE.md:35`；`ARCHITECTURE/AGENT_ORCHESTRATION.md:243`（opt.18，"审阅员输入清单的解码上限"，附测试 `test_review_import_large_manifest.py`） | 第一遍说"只在代码注释说明"，不对。两份架构文档都写了原因、改法和测试 |
| 7.15 做法审阅/操作提案审阅消费时不核使用证书 | 做法不同未记录（"可能判错"） | **收窄并降级**：只剩 METHOD_PLAN 一半，仍未记录；ACTION_PROPOSAL 一半改判"在用（等价做法）" | ACTION_PROPOSAL：`SDK/orchestrator/operation_materialization.py:398-453`（物化事务里现读 active plan/唯一归属范围/要求 pin/`Validity.CURRENT` 的准备验收/`VERIFIED` 候选/当前 slot 头）+ `SDK/orchestrator/operation_proposal_review.py:620-723` + 交接前 `action_commits.py` `_validity_refusal`（按当前作用域纪元）。METHOD_PLAN：`SDK/orchestrator/method_plan_reviews.py:61-115` | 操作提案虽然不走证书，但同一事务里重读了全部依据，交接前还核纪元，效果相当于核依据。做法审阅确实不核，但后果是"按旧要求审过的做法被重新采用"，**不会让完成判错**：叶子、组合、根三层审阅都按当前要求、带证书。阶段 E 开工裁决 §1.4"不作废、不自动改"记了"改要求后旧做法仍采纳"，但没记"新计划重新采用时也不核要求版本"。详见第三节 (a) |
| §16 `verify_delivery.py`/`check_plan.py`/`capture_identity.py` 生成 source-map | 做法不同已记录 | **已删（有决定）** | 第一遍给的依据 `scripts/verify_development_handoff.py`、`sdk/simple-harness-sdk/HANDOFF-SOURCE-MANIFEST.json` 在 HEAD 已不存在，提交 3a8c4697 删除；`HTN补齐-阶段B收尾-偏差裁决.md:428-432`（7-3"过时的交接快照文件"）、`HTN补齐-实施记录.md:247,333` | 替代做法也已经删了，现在没有任何 source-map 生成或校验 |

---

### 二、维持表（非"在用"行，一行一条）

**做法不同已记录（28 → 维持 27，1 条改判已删）**
- 0.3 默认开启：维持。`ARCHITECTURE/AGENT_ORCHESTRATION.md:107` 与提交 eb749104 都在；`HTN-片D-实施记录.md:93`、`HTN实现与原始计划差距评估-2026-10-02.md:190` 也有。
- I1 V1 合同改过：维持。`HTN一致性补改方案-2026-10-04.md:62`（H-3 删 `Criterion.phase`）。
- 2.2 METHOD_PLAN 消费改成计划提交闸门：维持（片 A）。注意它连带第三节 (a) 的缺口。
- 3.11 没有提取 `_create_service_intent_locked`：维持。WIP"审查 transport…续写"段写了"放同一个 savepoint UoW"。
- 5.2 / C1.2 检查策略自动投影：维持。WIP 第十段（五）、run-7（:1317）。现在实现搬到了 `deployment/duties.py`，说的是同一件事。
- 5.8 code_test"没收集到测试算通过"：维持。WIP:1254 缺陷 1。
- 6.3 TASK_CONTENT 用 critic：维持。WIP:337。
- 6.5 记账对象：维持。PLAN-STATUS:306（opt.45）、`HTN-片B-实施记录.md:178`（opt.119）。
- 6.11 / 6.14 / C1.4 判不下来复审一次：维持。PLAN-STATUS:515（opt.104）。
- 6.13 调用没回来就作废、开第二次调用：维持。`HTN补齐-实施记录.md:292`（第 6 类）。
- 7.13 按上限结清：维持。`ARCHITECTURE/ASSURANCE.md:32`（用户 09-26，opt.28/29）。
- 8.3 SQL 触发器屏障：维持。WIP:44"migration 26 增加同事务来源屏障"；阶段 G 偏离（迁移 41）。
- 8.4 固定 principal：维持。WIP 第六段。
- 8.9 ACCESS 键带来源、QUERY_SET 不含时钟：维持。WIP:1318（run-8/9）、PLAN-STATUS:524（opt.110）。
- 8.10b 只用审阅锚和检查锚：维持。`HTN补齐-阶段D-开工裁决与施工清单.md:62`（偏差单 3）。
- 8.15 到期扫证书表：维持。WIP:34（R03）、第六段。
- 8.16 时钟 10 秒落库：维持。`HTN补齐-阶段F2-联测记录.md:150`，`clock.py:12-14`。
- C1.18a 交接用 HTN 有效性见证：维持。`HTN补齐计划-2026-10-02.md:112`（表二 9）、`HTN补齐-阶段C-开工裁决与施工清单.md:207-210`（C7）。
- §13.2 隔离 Host 改为调试版应用包：维持。第一遍引的"HANDOFF §7.23"实际在 `plans/AgentRuntime/2026-09-23-arp-body/10-RP-E3-原生平面接线实施记录.md:394`，HANDOFF.md:31 有摘要。
- §11 默认选择点删除：维持（同 0.3）。
- §11 默认 ON 先于验收：维持。WIP:1245"用户拍板的顺序调整"。
- §12 空库重放改为逐表比对：维持。`HTN补齐计划-2026-10-02.md:84-90`（阶段 G 偏离）。
- §14 AS-VERIFY 顺序：维持。WIP:1245。
- C.2 检查策略随后写：维持。WIP 第十段（五）。

**已删（有决定）12 → 维持 12**
- 0.4、6.9、§11 LEGACY/COMPLETION_V1：提交 eb749104，表一 11（开发期不兼容旧任务）。
- I8、C1.17、§10 离线备份、§10 `reauthorize`、§10 ACL 权威、§12 正常备份：`HTN补齐计划-2026-10-02.md:120,165-166,262`（决定②⑤，A″），`HTN补齐-实施记录.md:249`。
- 5.7 citation：opt.134（`HTN补齐-阶段A撇-清点.md:13`，严格引用选 A）。
- §11 EXPLICIT_SUCCESSOR：表一 11。
- §11 TaskGraph 独立开关：表一 16。

**用户定不做 1 → 维持**
- C3.5：维持，但分类不太准。计价部分是已经删了（迁移 38，"金额计价删"），不只是"不做"；"不改写过去的费用"那一半仍在用。

**验收已做 10 → 维持 10（两条补注）**
- 多用户/跨租户负例、48 组、继承 48、OCC 12、stateful、独立代码审查（`.local-test-evidence/2026-09-25/assurance-plan-challenge/README.md` 存在）、默认 ON 后最终回归（WIP:1360，26 个失败与开启前相同）、`assurance_exec` junit：都维持。
- AS-0：维持，但依据过期。`HANDOFF-SOURCE-MANIFEST.json` 已在 3a8c4697 删除，只剩 `baseline.md`。
- 变异：维持"做过但已过期"。我逐条核对了 `mutations.json`：AM09 改坏前的原文不在代码里，AM10/AM13/AM20 目标用例已删，AM11 是多处编辑型；与第一遍一致。

**做法不同未记录 10 → 维持 8（8MiB 改判已记录；7.15 收窄）**
- 3.2 读取器返回形状：维持。`assurance_reads.py:87,148`。全库文档搜不到 `ResolvedRef`/`ExactMetadata`。
- 3.6 tool_receipt/policy/authority/capability 读不了：维持。`assurance_reads.py:157`。
- 5.12 没有"全局安全问题"一类：维持。原计划第 136 行原文确实有这一条。另有缓解：`decide_review` 要求模型结论是 ACCEPT 才放行（5.14），全局 BLOCKER 想被绕过，审阅员还得同时判 ACCEPT。
- 8.7 命题键：维持。`knowledge/predicates.py:293-300`。
- C1.18b 原生下载：维持。`api/facade.py:869-897`：原生根下直接返回 None，不做 use 检查。
- 状态标签不用：维持（全库搜不到 SPEC_RESOLVED 等）。
- 8 份内部 schema：维持。SDK `assurance/contracts/` 有 8 份、`assurance/schemas/` 有 10 份，计划包 `contracts/` 有 25 份；缺的 8 份与第一遍列的一致。第一遍写"随包 16 份"，计数稍有出入，不影响结论。
- 历史视图准则取第 1 版：维持。`api/assurance.py:514-519`。

**没做 10 → 维持 10**
- 8.14 MAINTAIN：维持（WIP:930、1016 有记录，属于"没做但已登记"）。补充：`contracts/htn.py:249` 有 `MAINTAIN_CONTINUOUS`，`knowledge/justifications.py:679` 只在缺 `monitor_interval_ms` 时拒绝，没有真实的持续监测。
- `check_plan.py`、`integration-map.json`、各映射表、`model-scenarios.json`：维持，只在 `plans/Assurance/specs/1.1/` 下。
- BODY_WIRED 16 边：维持。`BODY-WIRED.md` 只有门清单；其中 BW11 写"旧 terminal direct 路径封堵"，正好对应 7.10 的残留。
- `gate-commands.local.json`：维持，全库没有。
- `ASSURANCE.md` 未记 10-03：维持，并扩到 `index.md`（见改判表 §15.1）。
- 覆盖清单回写：维持。
- 每局预算与试验纪律：维持。

**只在测试 3 → 维持 3**
- 隔离状态精确只读：维持。`commit._assurance_read_authority` 只在 `assurance_assembly.py:452` 设置；而 Host 启动时根门不过就直接失败（`event_handler.py:657-663` 那条"只开管理"的分支在 Host 下走不到）。
- `assurance_root_diagnostic`、门面 `install_assurance_root`：维持。生产代码、Host、前端都没有调用者，部署走 `deployment/assembly.py:72` 直调提交层。

**验收没做 3 → 维持 3（M01–M04 那条改说明）**
- 保证视图非主流程真机点击：维持，全库没有点击记录。
- 继承 X 组 18 条：维持。`inherited-coverage.json` 里 18 条是 `NOT_REMAPPED_ORIGINAL_D3_OPERATION_SEMANTICS`，备注写"由既有套件承接、在全量回归阶段整体执行"。实际没有逐条映射；F2 的 K08/K09（发布服务只调一次）脚本用例只部分覆盖 X01/X04。
- 真实模型 M01–M04 × 3：状态维持，但第一遍"这四件事没在真实模型下实测"说过头了。有零散的真实局可作近似证据：M03 撤回后不复用旧证书，见 46448d9b（10-05 资料换版本真机，在审那一步"拿旧版资料的凭证失效，导入空转 32 次后放弃"）；M04 外部效果恰好一次，见 PLAN-STATUS:317（`mission-e5f82ae8`，真实发布全链路）；M01 不假完成，见 run-21 以及 F2 九局。这些都不是计划规定的 runner、局数和判分方式。

---

### 三、两条重要结论的复核详情

#### (a) 做法审阅、操作提案审阅采用时不核依据有效性：**METHOD_PLAN 成立，ACTION_PROPOSAL 不成立；"可能判错"说过头**

**METHOD_PLAN：成立，能构造出具体路径。**
- 闸门 `plan_commits.py:381` `_check_method_reviews` → `method_plan_reviews.py:118` `unreviewed_adopted_methods` → `adoption_refusal` → `review_of`（`:61-91`）。
- `review_of` 只按方法引用（id/版本/内容哈希）取本任务最近一份 METHOD_PLAN 包的正式记录，结论是 ACCEPT（或人裁决通过）就放行。它**不比**包的 `requirements_revision`，不比规划主体任务的合同版本，不看纪元，也没有证书。
- 规划器候选清单 `planning_selection.py:41` 用的是同一个函数。
- 别处有没有补救，逐一查过：
  - 正式记录表 `review_records` 有禁改、禁删触发器（`storage/schema.py:1165-1167`），旧审阅不会被作废。
  - 屏障触发器（`assurance_barrier_v26.sql`）只给纪元加 1，不拦写入。
  - 计划提交的读集检查（`plan_commits.py:611-634`）不包含做法审阅。
  - VALIDITY 消费者只观察证书，做法审阅没有证书。
  - 导入时核过一次纪元（`assurance_review_import.py:590-640` `require_epochs_locked`），导入之后再也不核。
- 具体路径：
  1. 要求第 1 版下，规划器对目标 G 提出做法 M；做法审阅（包摘要含 requirements=1）判 ACCEPT。
  2. 用户在主对话 `mission_amend`，要求改到第 2 版（例如删掉或改写一条 M 覆盖的判据，或加一条"全部用英文"）。屏障给本任务纪元加 1。
  3. 规划器收到 `RequirementsUpdated`，提交第 2 版下的新计划，原样采用 M（候选清单照样列出 M，因为它"审过"）。
  4. `_check_method_reviews` 读到第 1 版的 ACCEPT 记录，放行。M 在第 2 版下从没被审过，却被采用了。
- 有没有记录：`HTN补齐-阶段E-开工裁决与施工清单.md` §1.4 记了"已有计划不作废、不自动改，根的旧做法还采纳着"，覆盖了"旧计划里已采纳的做法"这一半；"新计划重新采用时不核要求版本"没有记录。仍判未记录。
- 影响：**不会让"任务完成"判错。**采用 M 之后，叶子内容审阅、中间层组合审阅、根终审都按第 2 版要求切包，并在 `accept_review` 和 `commit_goal_resolution` 处用证书加 `require_current_locked` 核纪元。后果只是规划质量：一个没按新要求审过的做法进了计划，可能白跑一轮再被内容审阅打回。第一遍"可能判错"应改成"做法采纳绕过了一次本该重做的审阅"。

**ACTION_PROPOSAL：不成立（等价做法在用）。**
- `materialize_reviewed_operation`（`operation_materialization.py:373-453`）在物化的同一事务里调用 `prepare_operation_intent_sources`（`operation_intent_sources.py:63-200`），重读以下依据：Mission 必须 ACTIVE、当前 active plan、唯一归属的完成范围、要求 Spec 哈希、候选文件必须 `VERIFIED` 且哈希一致、准备验收必须 `Validity.CURRENT` 并且仍在准备集合里。
- 冻结输入哈希不同报 `OP_INPUT_NOT_CURRENT`；`_operation_slot` 要求仍是当前分支头；`validate_materialization_review`（`operation_proposal_review.py:620-723`）核要求 pin 与当前要求一致、检查回执逐条一致、`require_assured`。
- 交接前 `action_commits.py` 还有 `_validity_refusal`，按当前作用域纪元核有效性见证（阶段 C7）。
- 要求换版、验收被取代、候选被替换，都会被拒绝。剩下没覆盖的只有"审阅员看过的其它披露材料被撤"，而操作提案的依据本来就是上面这些。

#### (b) `judge_mission` 非保证分支（`commit_service.py:3145-3159`）：**成立。产品里走不到，是死分支；但它是"出错就放行"的写法，第一遍对触发条件的说法不全**

- `is_assured`（`assurance_final_writer.py:42-47`）= `AssuranceStore.lane(m) == "ASSURANCE_1_1"`；遇到 `AssuranceError`（`CREATION_CONTRACT_UNRESOLVED`、`ASSURANCE_PROFILE_UNBOUND`）就返回 False。
- 为假的情形逐一看：
  1. 通道是 LEGACY 或 COMPLETION_V1：只有老库任务会这样（迁移 26 分类，或 10-03 以前建的）。
  2. 没有创建判别行：新任务一定会写，因为 `commit_service.py:803` 无条件调 `record_mission_creation`；没装工厂报 `ASSURANCE_FACTORY_UNBOUND`，不是 planning-decision 报 `ASSURANCE_PLANNING_PROTOCOL_REQUIRED`。
  3. 通道是 ASSURANCE_1_1 但缺绑定：与判别同事务写入，绑定表禁改、禁删、禁替换（`assurance_schema.sql:166-175`），不可能出现。
  4. 没有判别表：只有 schema 8/9 的只读审计快照，`is_assured` 不吞这个错，会直接报错退出。
- 1、2 这类老任务在每轮循环入口 `_cycle_inner` → `_refuse_unsupported_contract`（`event_handler.py:3687-3690` → `:1537-1556`）就按 `unsupported_unbound_mission` 叫停，轮不到判定。
- 新路线走不到这里还有一个独立原因：`_require_root_resolution`（`commit_service.py:3301-3335`）对分层任务要求根结论存在，而 `commit_goal_resolution` 先 `require_assured`（`resolution_commits.py:1051`）。非保证任务形成不了根结论。
- 建任务入口（Host `create_mission`/`create_mission_with_sources` → `MissionControlV1` → `Orchestrator.create_mission` → `commit.create_mission`；`api/missions.py:139`、`source_commits.py:137`）全部收口到 `record_mission_creation`。`judge_mission` 在产品里只由 `event_handler.py:10531,10825` 调用，Host 和门面都不直接调。
- 需要修正的地方：
  1. 第一遍说"只在缺创建合同时走到"，其实还包括 LEGACY/COMPLETION_V1 通道和 PROFILE_UNBOUND。
  2. 这段代码是"出错就当非保证通道然后直接完成"。今天全靠另一处循环入口检查兜底，`judge_mission` 又是公开方法。
  3. 提交 eb749104 自称已删除全部"否则走旧路 B"的分支，这一处漏了；BODY_WIRED BW11"旧 terminal direct 路径封堵"也没关。
- 建议：删掉 3145-3159，或把 `is_assured` 改成 `require_assured`。这与"旧路径直接删"的口径一致。

---

### 四、40+ 条在用抽查结果（共 56 行，标出不成立/部分成立）

没有完全不成立的。部分成立 13 行：
- 9 行在第一节改判：I7、C1.11、7.9、7.10、§10 状态文件、§11、§12.1、§13.2、§15.1。
- 4 行只补注、不改判：I4、6.8、8.11、§16 证据目录。

7.12、7.14 不在抽样里，是复核 I7 时牵出来改判的。

**前段（31 行）**

| 条目 | 结论 | 要点 |
|---|---|---|
| 0.1 六类审阅不另建主链 | 成立 | `assurance_review_runtime.py:289-368`；`_run_critic` 只是转调 |
| 0.2 迁移号 | 成立 | `storage/schema.py:1401-1418`（26～43） |
| I2 不建第二个调度器 | 成立 | `create_service_intent` 生产调用者只有 `transport.py:380` 和 `event_handler.py:4823` |
| I3 效果只从 OCC 读 | 成立 | `assurance_consumers.py:472` |
| I4 全部接管 | **部分** | 除 7.15、C1.18b 外还有两处：收尾不核纪元（I7）、`judge_mission` 旧尾巴（7.10） |
| I6 UNKNOWN 不升 PASS | 成立 | 人裁决通过属于用户决定 |
| I7 撤回使旧证书失效 | **部分 → 改判** | 收尾不拦 |
| 2.1 / 2.3 / 2.6 | 成立 | 2.3 生产调用 `event_handler.py:1473`；2.6 `cut` 只切包、不派发 |
| 3.4 拒绝码 | 成立 | 发件人不对（REF_ISSUER_MISMATCH）只出现在事件类引用 |
| 3.8 创建顺序 | 成立 | 同一事务，没有 hash 循环 |
| 4.4 / 4.8 曝光 | 成立 | 原生路径也核线上请求字节（`assurance_turn_sources.py:187-227`） |
| 5.1 唯一批准入口 | 成立 | `assurance_store.py:377` |
| 5.9 / 5.11 真值表、BLOCKER | 成立 | 逐格与原表一致 |
| 6.8 不接裸结论 | 成立（写入侧） | 写入侧唯一入口是 `assurance_review_import.py:648`；消费侧见 7.15 |
| 6.12 格式修复一次 | 成立 | 格式修复、复审、调用失败共用第 2 次调用，所以合计最多一次 |
| 7.3 根结论绕不过 | 成立 | 写 `goal_resolutions` 的只有 `resolution_commits.py:1178` |
| 7.9 无旁路 | **部分 → 改判** | 没有枚举测试；有两个 COMPLETED 写点 |
| 7.10 唯一写方 | **部分 → 改判** | `is_assured` 出错就放行；原计划要求的"安全释放 terminal pools"不在 writer 里（全库已没有 `release_terminal_pools`） |
| 7.11 收尾中不判卡死 | 成立 | `event_handler.py:9114,2585`；`progress.py:64` |
| 8.6 CompleteRead | 成立 | 缺表抛 sqlite 原生错误而不是具名码，但都不会判成"读完整" |
| 8.11 一致读、事务外计算 | 成立（限证书消费点） | 收尾不在其中 |
| 9.1 / 9.6 | 成立 | `assurance_work.py:299-326` |
| C1.11 | **部分 → 改判** | 同 I7 |
| C1.13 | 成立 | 默认 finalizer 装好（`assurance_assembly.py:408-412`） |
| C3.3 / C5.3 | 成立 | 生产代码里没有任何地方把重算计数清零 |

另有风险提示：审阅回复导入会写 sources 表（`assurance_review_collect.py:98`），触发屏障让任务纪元加 1，在途的证书候选可能频繁要求重算。只影响性能，不影响结论。

**后段（25 行）**

| 条目 | 结论 | 要点 |
|---|---|---|
| §10 根闸门先于一切入口 | 成立 | Host 两条启动路径都传 `assurance_root_setup`（`Host/service.py:337-341,802-806`） |
| §10 状态文件 → 隔离 | **部分 → 改判** | 自动修复，不进隔离 |
| §10 非空未知根 | 成立 | EMPTY/HISTORICAL 标签如实记，但没有任何地方读它 |
| §10 读授权 24h | 成立 | 过期由 `assurance_check_use.py:45-70` `_permission` 检查 |
| §11 同事务激活 | 成立 | 没装工厂报 `ASSURANCE_FACTORY_UNBOUND`，不回退 |
| §11 CREATION_CONTRACT_UNRESOLVED | **部分 → 改判** | `is_assured` 吞掉报错 |
| §11 风险动作仍要用户审批 | 成立 | `deployment/duties.py:54-160`，只有 `action:` 前缀才等人点 |
| §12 收尾行 / pin 状态机 | 成立 | 触发器仍在 |
| §12.1 严格 JSON | **部分 → 改判（已记录）** | 审阅回复放宽两处 |
| §12.1 作者非空 | 成立 | 生产调用 `assurance_review_runtime.py:272` |
| §13.1 调用者固定 / 每页 ≤100 / use_check 只诊断 / 保证视图 / projection | 成立 | use_check 有一个副作用：诊断会挤掉同一条记录还没提交的候选，最多导致重算，不放行 |
| §13.2 wheel 哈希与指纹 | **部分 → 改判** | 启动只核版本号；指纹用的是常量 |
| §15.1 四份文档回写 | **部分 → 改判** | `index.md` 也没写 10-03 的删除 |
| §16 证据目录先确认被忽略 | 部分（不改判） | `.gitignore:305` 生效；但"跑之前先确认、每次新建目录"只有 `scripts/native/launch_frozen_orchestrator.py` 一个脚本做到 |
| C.2 建任务事务顺序 / 纪元与时钟行 | 成立 | 激活回执写在判别之前，但同一事务，没有循环引用 |
| C.4 PREPARING / 孤儿对账 | 成立 | Host 每次启动都跑 |
| C.6 history/current、SNAPSHOT_CHANGED | 成立 | 翻页标记含纪元、根、末尾序号 |
| 默认 ON：新建 Mission 必走保证通道 | 成立 | 所有建任务入口都收口到 `record_mission_creation`，`selects` 恒为真 |
