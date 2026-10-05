# Assurance 现状对照（2026-10-05）

**用途**：与《HTN 与原始计划一致性复核》《TaskGraph 现状对照》同口径，逐条对照 Assurance 原始计划，查现行代码哪些按计划在用、哪些做法不同、没做、只在测试或已删。

**口径**
- 原始计划：`plans/Assurance/specs/1.1/ASSURANCE-EXEC-1.1.zh-CN.md`（2026-09-22，下称"原计划"）及配套 `implementation/`、`contracts/`、`sql/`。
- 已定结论不重复算缺口：Assurance 实施记录 `sdk/simple-harness-sdk/plans/assurance-1.1/BODY-WORK-IN-PROGRESS.md`、根 `HANDOFF-2026-09-23.md`、`HTN补齐计划-2026-10-02.md` 偏离段、`TaskGraph-补全-方案.md` 第七节、台账 `需求与场景状态.md`；用户已定口径（判断交给 LLM、同一件事一条路径、旧路径直接删、功能默认开启、审阅员回复放宽两处、判不下来复审一次再问人、金额计价删、暂不做十项）。
- 代码版本：`main` `d12455b0`（SDK opt.159）。两位子代理（Opus）分两段只读代码与证据目录，没有跑测试；我抽查了 A 段第 1、2 条。
- 路径简写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`；`FE/` = `tauri-app/src/`。

## 一句话结论

主干（证据标签与曝光、检查政策与三值判定、六类审阅、唯一完成写方、屏障与纪元、事件摄取、Host 三个读接口、默认开启）都在产品默认路径上，**没有发现"删了没记录"或"机制只在测试"的核心条款**。真正的差距是两类：
1. **一处可能判错**：做法审阅（和操作提案审阅）的结论在被采用时不再核"依据是否仍有效"；另有 `judge_mission` 里一段绕过唯一完成写方的旧尾巴应删。
2. **验收证据缺或过期**：真实模型四场景各 3 局没跑；继承用例 X 组 18 条从没执行；阶段 A′ 合并用例后覆盖清单约 39 处、变异清单 6 条失效未重跑；保证视图的非主流程交互没真机点击。

## 总计（243 条，不含纯流程项）

| 现状 | 前段 §0～§9、C.1/C.3/C.5 | 后段 §10～§16、附录 A、C.2/C.4/C.6 | 合计 |
|---|---|---|---|
| 在用 | 116 | 50 | 166 |
| 做法不同已记录 | 21 | 7 | 28 |
| 已删（有决定） | 5 | 7 | 12 |
| 用户定不做 | 1 | 0 | 1 |
| 验收已做 | — | 10 | 10 |
| **做法不同未记录** | 6 | 4 | **10** |
| **没做** | 1 | 9 | **10** |
| **只在测试** | 0 | 3 | **3** |
| **验收没做** | — | 3 | **3** |
| 已删（无记录） | 0 | 0 | 0 |

按计划或按已记录决定的：217 条（89%）；有差距的 26 条，其中会影响保证结论的 1 条，其余是验收证据、死代码与文档不符。

## 需要处理的差距（按影响排序）

| # | 条目 | 状态 | 影响 |
|---|---|---|---|
| 1 | 做法审阅、操作提案审阅的结论被采用时不核使用证书（`SDK/orchestrator/plan_commits.py:381` `_check_method_reviews`、`SDK/orchestrator/operation_materialization.py:373`） | 做法不同未记录 | **可能判错**：导入后依据被撤或纪元变了，做法仍被采用。操作提案交接前另有有效性核对兜底，风险主要在做法 |
| 2 | `SDK/orchestrator/commit_service.py:3145-3160` `judge_mission` 非保证分支直接写完成 | 残留旧路径 | 只在缺创建合同时走到，但走到就绕过收尾与唯一完成写方；按"旧路径直接删"应删 |
| 3 | 真实模型四场景（不假完成、拒坏候选、撤回后不复用旧证书、外部效果恰好一次）× 3 局 | 验收没做 | 大：只有 1 局 Grok 主流程 + F2 九局 HTN 场景题，这四件事没在真实模型下实测；驱动脚本也没有 |
| 4 | 继承 66 条中 X 组 18 条（同一操作跨尝试不重复、外部键防重） | 验收没做 | 中：从没映射也没执行 |
| 5 | 覆盖清单（sdk-cases / occ / 继承）约 39 处指向 A′ 已删用例；22 条变异中 2 条原文不在、4 条目标用例已删，A′ 后没重跑 | 没做 / 过期 | 中：无法从清单证明这些保护仍有测试守着 |
| 6 | BODY_WIRED 16 条生产调用边没逐条登记关闭（BW14 受管恢复已作废未更新） | 没做 | 中：接线完整性只靠真机局间接证明 |
| 7 | 保证视图真机点击只覆盖主流程（审阅详情、历史/当前切换、断线重连、撤回没点） | 验收没做 | 中：可能有界面缺陷，不影响后台结论 |
| 8 | 原生下载、摘要读取没有统一"依据是否当前"检查 | 做法不同未记录 | 小：单用户桌面无权限表，主要是计划不符 |
| 9 | 没有"全局安全问题必须挂到必须项"一类 | 做法不同未记录 | 小：只在"或"公式里可能绕过；"或"已交审阅员判，公式基本都是"且" |
| 10 | 8 份内部 schema 没随包，用 Python 校验代替 | 做法不同未记录 | 小～中：结构与合同是否逐字段一致没有自动核对 |
| 11 | tool_receipt / policy / authority / capability 四类引用读不了 | 做法不同未记录 | 现无影响；以后拿工具回执当审阅对象会直接报 `REF_KIND_UNSUPPORTED` |
| 12 | 历史视图准则固定取第 1 版要求 | 做法不同未记录 | 小：改过要求的任务历史切面显示旧准则，只影响显示 |
| 13 | 受管恢复删后留下：隔离状态精确只读分支、`assurance_root_diagnostic`、门面 `install_assurance_root` | 只在测试 | 小：死代码；只读分支生产一律拒读，偏保守 |
| 14 | 另 5 个无调用者函数：`assurance_review_transport.py:609` `is_bound_task_review_subject`、`assurance/local_checks.py:207` `normalize_local_check`、`AssuranceStore.has_live_pin`、`assurance_purpose_reviews.py` `_exact_pin`、`knowledge/assurance_sources.py:102` `_is_system_key` | 死代码 | 小：可删 |
| 15 | 命题键无 namespace/scope、哈希截 32 位；读取器返回形状不同；8MiB 读上限；状态标签不用 | 做法不同未记录 | 小：功能等价，补记录即可 |
| 16 | `ARCHITECTURE/ASSURANCE.md` 停在 10-02，没记 10-03 删旧通道、关闭开关、受管恢复；`integration-map.json` 等计划资产、`check_plan.py`、`gate-commands.local.json` 没随代码维护 | 没做 | 小：文档不符，接手人可能误以为还有关闭开关 |
| 17 | MAINTAIN 持续监测、每局预算与试验纪律 | 没做 | 现无用途 / 随第 3 条一起缺 |

---

# 附：逐条对照明细

## 前段 · §0 使用规则、范围与证据身份

| 条目 | 现状 | 代码依据（file:line 与函数名） | 说明 |
|---|---|---|---|
| 0.1 复用现有 scoped_content / scoped_composition / completion_* / root_review / composition_review / operation_proposal_review，不另建一条平行主链 | 在用 | `SDK/orchestrator/assurance_content_review.py:35` `ensure_task_content_review`；`composition_review.py:167` `resolve_one`；`operation_runtime.py:128,294` | 六类审阅都挂在原入口上，没有第二条主链 |
| 0.2 迁移号取本地下一个空号并冻结 | 在用 | `SDK/storage/assurance_schema.sql`（迁移 26）；后面接着迁移 38/39/41 等 | 实施记录写明 26 号在发布前原地改过（WIP），之后改动都另开新号 |
| 0.3 交付后新 Mission 默认开启 | 做法不同已记录 | `SDK/orchestrator/assurance_factory.py:50` `selects`、`:152` `record_mission_creation`（没装 factory 就拒绝建任务）；Host 已经没有 `assurance_profile` 开关 | 比"默认开"更进一步：没有关闭选项，只有这一条路。依据：2026-10-03 删旧路（补齐计划阶段 A′） |
| 0.4 已在跑的 Mission 不悄悄换协议（用持久 lane 区分 legacy） | 已删（有决定） | `assurance_factory.py:172` `validate_creation_replay` 要求必须是 `ASSURANCE_1_1` | 开发期不兼容旧数据，旧 lane 直接删除，旧库不能启动 |

## 前段 · §1 核心不变量与唯一 owner

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

## 前段 · §2 六类审阅的复用、修改与新增分区

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

## 前段 · §3 内部 Ref 与精确 resolver

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

## 前段 · §4 证据标签、曝光与冷恢复

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

## 前段 · §5 批准的检查策略与三值判定

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

## 前段 · §6 六类审阅的切换、预算与调用身份

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

## 前段 · §7 接受与全部终态写入

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

## 前段 · §8 证据屏障、读集、时效与活性

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

## 前段 · §9 事件摄取、准备、提交和 ACK

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

## 前段 · 附录 C.1 固定新增接口与实际调用者

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

## 前段 · 附录 C.3 原始数据、受信导入与无 hash 循环

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| C3.1 预留事件正文不含将来 invocation 的 hash；invocation 引用已经存好的预留事件 | 在用 | `transport.py:402-425` | — |
| C3.2 披露事件含标签增量 hash，不含本批的最终 hash；批次再引用事件 | 在用 | `assurance_review_import.py:276-287`（`delta_hash`）；`reviewer_evidence_tools.py:419` | — |
| C3.3 TurnImported 引用原 Provider 请求和输出，不含将来的记录绑定 | 在用 | `assurance_review_collect.py:114-125` | — |
| C3.4 pin 获取先有稳定的回执 ID；回执里不序列化含自身 hash 的 pin | 在用 | `storage/assurance_pins.py:20` `require_pin_receipt`；`assurance_review_pins.py:17,120` | — |
| C3.5 用量导入沿用原调用冻结的计价和身份，不改写过去的费用 | 用户定不做 | 金额计价已删（迁移 38）；用量身份不改写这部分照常在用 | 暂不做的十项之一：金额计价 |

## 前段 · 附录 C.5 事件适配、时间与队列状态

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| C5.1 按事件类别表逐个登记，不按名字前缀猜 | 在用 | `assurance_consumers.py:40-73`（`CLOSEOUT_SOURCE_EVENTS`、`VALIDITY_SOURCE_EVENTS`）；`assurance_review_consumer.py:50` `classify` | — |
| C5.2 一条事件可以喂多个 consumer，cursor 各自独立 | 在用 | `assurance_tick.py:169-197` | — |
| C5.3 MANUAL_REQUIRED = WAITING + 原因；查到期时排除；有更高 seq 的新事件可以合并重开；用户重试不重置累计上限 | 在用 | `assurance_work.py:248`、`:146-155`（重开时保留 tries） | — |
| C5.4 RUNNING 租约过期只回收协调权；原 service intent 照样复用 | 在用 | `assurance_work.py:213` `claim_due`；`transport.py:284-298` 复用已有调用 | — |
| C5.5 新来源事件升级目标，旧领取失效、不能 ACK | 在用 | `assurance_work.py:146-155`、`_assert_claim` | — |
| C5.6 发通知前先查同一条逻辑通知有没有回执 | 在用 | `assurance_consumers.py:788,790` | — |

## 前段 · 本段小结

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

## 后段 · §10 旧备份恢复与当前授权

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

## 后段 · §11 Lane、首次启用和默认 ON

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

## 后段 · §12 SQL、codec、引用资产一致性

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

## 后段 · §13 Host wire、隔离载入、原生验收

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

## 后段 · §14 施工顺序与 BODY_WIRED

| 条目 | 现状 | 代码依据 / 证据 | 说明 |
|---|---|---|---|
| AS-0 合同/来源固定 | 验收已做 | `sdk/simple-harness-sdk/plans/assurance-1.1/baseline.md`；`HANDOFF-SOURCE-MANIFEST.json` | |
| AS-BODY 第 1～5 项主体实现 | 在用 | WIP 第一～九段；`SDK/orchestrator/assurance_*.py` 全套 | |
| BODY_WIRED 16 条生产调用边逐条登记关闭 | 没做 | `plans/assurance-1.1/BODY-WIRED.md` 只有门清单；WIP:3 仍写"BODY_WIRED 未通过"；`HANDOFF-2026-09-23.md:74` "16 条未关闭" | 之后没有任何关闭记录；BW14（受管恢复）已随 A″ 作废也没更新 |
| AS-VERIFY 集中验收顺序 | 做法不同已记录 | WIP 第十段（四） | 用户拍板先跑通主流程、默认开启，12 局/独立审阅等后补 |

## 后段 · §15 真实模型 oracle、预算、继承与完成标准

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

## 后段 · §16 实际执行命令与交付

| 条目 | 现状 | 依据 | 说明 |
|---|---|---|---|
| 用候选环境/冻结依赖跑；证据目录先确认被忽略、每次新目录 | 在用 | `.local-test-evidence/` 被 git 忽略；目录按日期/run-N | |
| `verify_delivery.py`/`check_plan.py`/`capture_identity.py` 生成 source-map | 做法不同已记录 | `scripts/verify_development_handoff.py` + `HANDOFF-SOURCE-MANIFEST.json`（`HANDOFF-2026-09-23.md` §1、`plans/Assurance/HANDOFF-VALIDATION-2026-09-23.md`） | 没有 `source-map.local.json` |
| 主体后跑 `assurance_exec` 并留 junit | 验收已做 | `证据/2026-09-23/assurance-1.1-verify/sdk-*.junit.xml` | |
| `gate-commands.local.json` 登记遗留用例/变异/H1 命令 | 没做 | 无此文件 | 变异命令在 `plans/assurance-1.1/tools/run_assurance_mutations.py` |

## 后段 · 附录 A：资产与新接口落点

| 条目 | 现状 | 依据 | 说明 |
|---|---|---|---|
| `integration-map.json` 单 owner 映射 | 没做 | 只在计划包 | 没随代码维护，"备份"一栏已随 A″ 失效 |
| `ref-resolution-map`/`field-producers`/`sql-column-producers` | 没做 | 只在计划包 | 代码按 `assurance_reads.py`/`refs.py` 实现，没有自动核对；加列后未更新 |
| `contracts/*.schema.json` | 做法不同未记录 | SDK 只随包 16 份（7 份 host + 9 份内部，内部的只当身份哈希用，见 `assurance/check_specs.py:16-30`） | blob-pin、closeout、criterion-policy、disclosure-batch、pending-work、policy、review-reply-v2、success-formula 这 8 份只在计划包，代码用 Python 字段校验代替；restore-authorization 随 A″ 删 |
| `sql/assurance_additive.sql` | 在用 | `SDK/storage/assurance_schema.sql` | 见 §12 |
| `reference/protocol_v11.py`、`semantics.py` | 在用 | 计划包 | 按计划只当参考实现，不进产品 |
| `model-scenarios.json` | 没做 | 计划包有，SDK `plans/assurance-1.1/` 有副本 | 没有 runner 接真实运行时 |
| `inherited-coverage.json`/`occ-coverage.json`（及 `sdk-cases.json`）随用例回写 | 没做 | `plans/assurance-1.1/*.json` | A′ 后共约 39 处用例名指向已删用例（见 §15） |

## 后段 · 附录 C.2：第一次启动和批准政策的先后

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| 建任务事务里依次写任务与要求→创建判别→激活事件/回执→绑定、纪元、四游标 | 在用 | `assurance_factory.py:51-127`；`SDK/orchestrator/commit_service.py:803-804` | |
| 检查策略随后写，读者不要求还没建的审阅/预留 | 做法不同已记录 | `SDK/deployment/duties.py:160` `project_check_policies`；`assurance_assembly.py` `check_policy_projector` | 由部署在首次审阅前按原要求无损推导并批准（WIP 第十段（五）、run-7/run-15）。09-25 挑战指出它记成 actor_type=human |
| 新空环境的全局纪元/时钟行由真实安装回执产生 | 在用 | `assurance_root_commits.py:69-95` `_initialize_clock`；`assurance_store.py` `initialize_environment` | |
| 回执不递归包含自身哈希；纪元/游标用同事务延迟外键 | 在用 | `assurance_factory.py:96-118`；SQL `DEFERRABLE INITIALLY DEFERRED` | |
| 历史非空根首次安装须经当前认证安装入口登记 | 在用 | `deployment/assembly.py:65-75`；`assurance_root_commits.py:98-113` | 每次启动以部署身份自动安装，不是额外的人工启用批准 |
| 门面显式安装入口 `install_assurance_root` | 只在测试 | `SDK/api/facade.py:178-189` | Host 不调（部署直接调提交层）；死入口 |

## 后段 · 附录 C.4：Pin/GC

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| acquire/bind/release，唯一写者 AssuranceStore | 在用 | `SDK/orchestrator/assurance_review_pins.py:17-200`；`assurance_store.py` `acquire_pin`/`transition_pin` | 文件名与计划的 `artifacts/assurance_pins.py` 不同（计划允许等价名） |
| 先记 PREPARING 再读 CAS；读失败就释放并报 SOURCE_UNAVAILABLE | 在用 | `orchestrator/assurance_content_review.py:247,308-310`；`assurance_purpose_reviews.py:330,384` | |
| 启动对账释放孤儿 PREPARING | 在用 | `assurance_assembly.py` `reconcile_startup` → `assurance_review_pins.py:201` `release_orphan_preparations` | |
| 没有 GC 时不新建定时 GC、数据保留 | 在用 | `SDK/artifacts/store.py` 无删除；`Host/storage_usage.py` 只统计提醒 | 与"会话数据永久保留"一致 |
| SQL 只保证 pin 身份/状态，不保证字节存在 | 在用 | `assurance_schema.sql:221-247`；迁移 27 | |

## 后段 · 附录 C.6：Host DTO 字段语义

| 条目 | 现状 | 代码依据 | 说明 |
|---|---|---|---|
| `history_state` 取固定序号状态，`current_use` 按当前权限/根/有效性算 | 在用 | `api/assurance.py:341-363` `_item`、`:374-583` | |
| review 响应：官方状态、逐准则 model/effective 等级与检查门、披露标签；正文走获准 artifact 读 | 在用 | `api/assurance.py:648-738` `review`；`FE/views/MissionAssurance.tsx:162-171` | |
| use_check：`diagnostic_only=true`、`certificate_ref=null` | 在用 | `api/assurance.py:796-806` | |
| 没有完整历史覆盖 → HISTORY_UNAVAILABLE | 在用 | `api/assurance.py:487,754` | 错误码按合同用 SOURCE_UNAVAILABLE，消息带 HISTORY_UNAVAILABLE（`host-error-v1` 枚举里本来就没有它） |
| 当前视图翻页途中状态变化 → SNAPSHOT_CHANGED，不混页 | 在用 | `api/assurance.py:590-597`（filter_hash 含纪元、根、末序号） | |
| 分页不当作证据集合完整的声明 | 在用 | `truncated`/`next_cursor`；前端"尚有后续页" | |
| 历史视图的准则列表 | 做法不同未记录 | `api/assurance.py:514-519` `_history_items`（按激活时的要求哈希取第 1 版） | 阶段 E 上线"中途改要求"后，改要求之后的历史切面仍列旧版准则；当前视图用最新版（`:383`）。只是显示偏差 |

## 后段 · 本段小结

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
