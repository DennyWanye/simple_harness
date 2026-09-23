# ARP-EXEC-1.1.1 主体施工 RP-E1：Assurance 接入（BW09）与验收场景对账

日期：2026-09-24。分支 `arp-1.1.1`（worktree `simple_harness-arp`），已合并私有 `origin/main` `bd0f6d59`（Assurance 1.1 主流程完成、默认开启）；合并无交叉文件，合并后 ARP 333 passed、legacy 两套与基线一致。规格依据：SKILL-CATALOGUE §3–§4（admit 必须核 official EvaluationAcceptance，不自签 PASS）、BODY-WIRED BW09、TEST-PLAN §1 执行顺序。测试计划按用户 2026-09-23 拍板的四层执行（见记忆 `arp-acceptance-test-plan-2026-09-23`）。

## 1. 交付物

| 文件 | 内容 |
|---|---|
| `agents/arp/assurance_acceptance.py`（新） | `AssuranceSkillAcceptance(store, clock_ms, root_incarnation=None)`：`SkillAcceptancePort` 的真实实现，只读 Assurance 账本（`missions` / `assurance_use_certificates` / `commit_receipts` 的 `AssuranceUseCertified` 回执 / `acceptances`）。`acceptance_ref` pin = 证书（id=certificate_id，revision=0，content_hash=certificate_hash，两侧 canonical JSON 编码一致）。通过条件全部成立才接受：①评估有派发链接（mission_id + task_id）；②该 mission 存在且其 `idempotency_key == evaluation_mission_key(evaluation)`（Host 创建评估 Mission 时必须用这个键，编排库按 tenant 不可变保存——评估与 Mission 的绑定因此在编排库里，不只是目录侧的断言）；③证书 hash、回执 hash 一致，purpose=ACCEPT、consumer=ACCEPTANCE；④证书的 mission 与派发相同，`acceptances` 行存在、task 与派发相同、`acceptance_id == 证书 consumer_id`、validity=CURRENT；⑤证书签发时间与 acceptance 时间都不早于派发登记时间（旧 acceptance 不能事后挂上）；⑥decision=USABLE、未过期；⑦给了根读口时根 incarnation 相同。任一不成立即以具名错误拒绝（`SKILL_EVALUATION_INCOMPLETE` 带 reason / `SOURCE_HASH_CONFLICT` / `SOURCE_UNAVAILABLE`），从不写入 |
| `agents/arp/lifecycle.py` | `record_evaluation_dispatch(evaluation, mission_id, task_id, caller, command_id)`：目录侧登记"这次评估派给了哪个原 Assurance Mission 的哪个任务"的 durable intent 链接（SKILL-CATALOGUE §3），task_id 必填，一评估一 mission、一 mission 一评估（跨 namespace 全局唯一）、命令号幂等、不可改；`dispatch_for(evaluation)` |
| `sql/execution_additive_v1_1_1.sql` | 新表 `arp_skill_evaluation_dispatches`（PK namespace+evaluation+revision，UNIQUE(mission_id) 全局、UNIQUE(namespace,command_id)，task_id NOT NULL，禁改禁删触发器） |
| `agents/arp/runtime.py` | 装配时若 acceptance 端口有 `bind_lifecycle` 则绑定生命周期服务（真实适配器需要读派发链接；测试替身不受影响） |
| `tests/agents/arp/test_arp_assurance_acceptance.py`（新，2 项） | 真实组件：assured 夹具运行时（真实 Store/Commit/critic 入口/official record/原 acceptance writer，脚本化 reviewer）铸出真实 ACCEPT 证书；ARP 运行时经真实目录/生命周期/派发链接准入。见 §3 |
| `tests/agents/arp/test_arp_acceptance_gaps.py`（新，5 项） | 对账补缺：跨 root 符号链接、查询文本安全 + 短中文回退、依赖环/深度、向量归一化 + 稳定并列、策略上限边界。见 §2 表 |
| `tests/agents/arp/skill_fixture.py` | `native_bundle(..., required_skill_refs=)` |

未新增错误码；未改 23 个 Host 动词合同；Host 侧不必新增动词——派发登记是 Host 在派发评估 Mission 时直接调用的服务方法。

## 2. 87 条验收场景对账（第一层）

口径：覆盖 = 现有 ARP 定向测试已按场景的 then 断言；继承 = ARP 未改该路径，由 legacy `tests/agents` / `tests/execution`（基线 16/17 失败为既有）覆盖；真机/真模型 = 第四层；Mission 模式 = 创建协议目前只开 STANDALONE_CHAT，Mission 来源（TaskGraph / InputManifest / Assurance 读用途）按名拒绝（`SOURCE_UNAVAILABLE`），不在本轮验收内；Host = 需要 Host 接线后才能测。测试文件名省略 `tests/agents/arp/test_arp_` 前缀。

| 场景 | 判定 | 覆盖测试 / 说明 |
|---|---|---|
| R01 同 creation key 重复创建 | 覆盖 | creation::create_binds…、create_many… |
| R02 同 input_id 重提 | 继承 | 原 BaseAgent.submit/Journal 未改 |
| R03 进程退出同 root 重启 | 覆盖 | creation::crash_windows…、index_recall::reprepare… |
| R04 连续两个 Turn | 覆盖 | context_prepare::closure_and_turn_counting…、history_tools |
| R05 close 后新输入 + 旧调用返回 | 部分 | session_lifecycle::lost_embedding…（迟到费用入账）；close 拒新输入属原 runtime（继承） |
| R06 UNKNOWN 结果时 destroy | 覆盖 | session_lifecycle::destroy_waits…、lost_embedding… |
| R07 destroy rename 前后崩溃 | 覆盖 | session_lifecycle::crash_between…、partial_delete… |
| R08 destroy 后向量迟到 | 覆盖 | session_lifecycle::lost_embedding…、destroy_makes_an_unfinished_recall_stale… |
| R09 跨 root symlink | **本片补** | acceptance_gaps::test_cross_root_symlink… |
| R10 旧备份还原新 root | Host | 根 transfer/隔离由 Assurance root gate + Host restore 流程，ARP 侧 rebuild 门已测（session_lifecycle::rebuild_only…） |
| C01 公式动态 N | 覆盖 | context_prepare::every_provider_request…、shrink_drops… |
| C02 巨大中间组停止 | 覆盖 | context_prepare::shrink_drops…（RP-B 记录 §3） |
| C03 tool_calls 乱序 | 继承 | 原协议组解析未改；RP-B ProtocolGroupSnapshot |
| C04 召回与 recent 重叠去重 | 覆盖 | index_recall::recall_finds_the_early_secret… |
| C05 必需块超预算 | 覆盖 | context_prepare::required_content_over… |
| C06 全 wire 计量 | 覆盖 | context_prepare::every_provider_request…、meter_refuses… |
| C07 冻结重试 | 覆盖 | index_recall::reprepare_of_the_same_request… |
| C08 策略变更只影响后续 | 覆盖 | runtime_plane::settings_round_trip… |
| C09 曝光未入 input 不签 | 部分 | ARP 侧 ToolSnapshot 冻结已测（catalogue::bootstrap…）；Assurance 曝光导入属 Mission 模式 |
| C10 超大工具结果 | 继承 | 原 presenter/CAS 未改 |
| M01 超 2000 条仍可查 | 覆盖 | index_recall::recall_finds_the_early_secret… |
| M02 闭组后杀进程 | 覆盖 | index_recall::a_poison_index_job…、recall_resumes… |
| M03 真实 embedding | 真机 | 第四层；替身拒绝已测（creation 真值表） |
| M04 多向量空间 | 覆盖 | index_recall::partition_identity_and_generation…；NaN/zero/dim 具名拒绝见本片 acceptance_gaps::test_vector_normalisation… |
| M05 覆盖洞 highwater | 覆盖 | index_recall::a_poison_index_job… |
| M06 跨 session | 覆盖 | index_recall::session_access_scope_hash…、history_tools::a_cursor_never_crosses_purposes |
| M07 权限撤回不披露 | Mission 模式 | Assurance 读用途门 |
| M08 扫描超时 PARTIAL | 覆盖 | index_recall::recall_resumes…、codec::pagination_progress… |
| M09 查询文本安全/短中文 | **本片补** | acceptance_gaps::test_query_text_is_never_sql… |
| M10 零命中与降级分开 | 覆盖 | codec::scanning_cannot…/complete_index_cannot…、index_recall::empty_query… |
| K01 provider 过滤先于优先级 | 覆盖 | catalogue::capability_resolution_filters… |
| K02 UNKNOWN 不 fallback | 覆盖 | codec::no_replacement_ordinal…、catalogue binds_once |
| K03 zip 安全 | 覆盖 | skills::bundle_rejections…、hidden_or_unreadable…、directory_entries… |
| K04 渐进装载 | 覆盖 | skill_use::load_binds…、load_is_refused… |
| K05 执行冻结版 | 覆盖 | skill_use::script_skills_run_only… |
| K06 依赖环/深度/未准入 | **本片补** | acceptance_gaps::test_dependency_cycle…；未准入依赖 skills::skill_without_a_complete_lock… |
| K07 模型不能自晋级 | 覆盖（本片改为真实） | assurance_acceptance（真实账本）、skill_lifecycle::the_catalogue_itself_never_admits… |
| K08 热撤销 | 覆盖 | catalogue::suspending_a_tool…、skill_use::load_and_execute_refuse… |
| K09 workflow 即工具 | 覆盖 | skill_use::workflow_skills_are_refused_by_name（无 executor → UNAVAILABLE） |
| K10 私有 trace 存全局 skill | 设计排除 | 目录没有 Agent 自提 Skill 的入口，只接受认证安装命令 |
| T01 同名工具命名空间 | 覆盖 | catalogue::bootstrap…、catalogue_pages… |
| T02 未曝光工具 | 覆盖 | catalogue::suspending_a_tool_removes…from_dispatch |
| T03 严格 codec 与权限 | 覆盖 | codec::bool_is_never…、unknown_field_refused… |
| T04 沙箱逃逸 | 真机/Host | 执行器是 Host 端口；SDK 无 runner 即拒绝（skill_use::script_execution_refuses_missing_runner…） |
| T05 外部效果走 OPS | Mission 模式 | |
| T06 超大/二进制结果 | 继承 | 同 C10 |
| T07 调用中工具升级 | 覆盖 | catalogue::health_refresh_keeps_the_epoch…、ToolSnapshot 精确 ref |
| T08 资源死锁 | 继承 | 原 admission（tests/execution） |
| T09 exit0 不等于通过 | Mission 模式 | Assurance |
| T10 四入口同权 | 覆盖 | skill_use::script_execution_refuses…、delegate（workflow 为 UNAVAILABLE） |
| I01 执行库迁移 | 覆盖 | schema_v11 全部 7 项 |
| I02 双库重入 | 覆盖 | index_recall::reprepare…、session_lifecycle::lost_embedding… |
| I03 GC 与正式 pin | 部分 | session_lifecycle::retention_permit…；Assurance pin GC 属 Mission 模式 |
| I04 原生 UI 查看/更新 | 真机 | |
| I05 断线/旧 cursor/坏 DTO | SDK 覆盖 | runtime_plane::summary_manifest_pages_and_envelope_rules、codec cursor 测试；UI 侧真机 |
| I06 旧路径字节不漂移 | 继承 | legacy 两套与基线一致 + 安装后导入核对（第二层） |
| I07 新默认工厂 | Host | Host 接线 |
| I08 新 domain 注册 | 覆盖 | catalogue::resolution_requires_the_capability_schemas |
| I09 平台实测 | 真机 | |
| I10 真实模型 12 局 | 真模型 | 用户要求补齐 12 局 |
| N01 Pin 种类 | 覆盖 | codec::all_reported_pin_kinds…、cross_kind_pin_refused |
| N02 搜索分页/用途隔离 | 覆盖 | history_tools 3 项、index_recall::search_cursor_replay… |
| N03 policy/adoption/回执 | 覆盖 | runtime_plane::settings_round_trip… |
| N04 合并链 wire guard | 部分 | 本片 Assurance 接入 + creation::factory_refuses_allow_all；TaskGraph 输入属 Mission 模式 |
| N05 计量分账 | 覆盖 | context_prepare meter 测试 |
| N06 embedding 真值表 | 覆盖 | creation::create_requires_trusted_caller_and_embedding_truth_table |
| N07 bootstrap 不靠 AllowAll | 覆盖 | catalogue::bootstrap…、creation::factory_refuses_allow_all |
| N08 creation intent/kernel | 覆盖 | creation::crash_windows…、create_binds… |
| N09 取消/ordinal | 覆盖 | codec::no_replacement_ordinal…、session_lifecycle::destroy_with_an_open_turn… |
| N10 N 按完整 Turn | 覆盖 | context_prepare::closure_and_turn_counting…、shrink_drops… |
| N11 索引代际 | 覆盖 | index_recall::partition_identity…、session_lifecycle::rebuild_only… |
| N12 RRF/f32 | **本片补** | acceptance_gaps::test_vector_normalisation_and_stable_ranking_ties |
| N13 三种 job/SUMMARY 不启用 | 覆盖 | codec::summary_jobs_not_enabled、session_lifecycle PURGE、index_recall poison |
| N14 drain vs destroy/七集合 | 覆盖 | codec::deletion_requires_all_seven…、session_lifecycle |
| N15 目录 owner/health | 覆盖 | catalogue::health_refresh…、operator_revocations… |
| N16 导入→准入完整 | 覆盖 | skills + skill_lifecycle + assurance_acceptance；diamond 冲突见 skills::native_bundle… |
| N17 800 文件分页 | 覆盖 | codec::catalogue_800_files…、catalogue::catalogue_pages… |
| N18 错误目录/严格事件 | 覆盖 | codec::every_schema_type_has_a_fixture、runtime_plane error_json |
| N19 迁移/FK/retention | 覆盖 | schema_v11、session_lifecycle::retention_permit… |
| N20 过程要求 | 本文 | |
| RV_R1 destroy 恢复矩阵 | 覆盖 | session_lifecycle 崩溃/双目录/marker 三项 |
| RV_R2 检索真值表 | 覆盖 | codec 真值表 4 项 |
| RV_R3A/B/C 召回分页 | 覆盖 | index_recall 前三项 |
| RV_R4 稳定并列 | **本片补** | acceptance_gaps::test_vector_normalisation_and_stable_ranking_ties |
| RV_R5 上限边界 | **本片补** | acceptance_gaps::test_policy_limits_hold_at_the_boundary… |

汇总：覆盖 58，本片补 5，部分 5，继承 6，Host/真机/真模型 8，Mission 模式 4，设计排除 1。

## 3. 测试与回归

- 定向：`test_arp_assurance_acceptance.py` 2 项——assured 夹具跑完 critic → official record → `accept_result` 一次事务写出 Acceptance + 证书 + 回执；派发缺 task_id → MISSING_FIELD；mission 键不是评估键 → MISSION_KEY_MISMATCH；证书未出时按名拒绝（CERTIFICATE_MISSING）；同 id 异 hash 的 pin 拒绝（SOURCE_HASH_CONFLICT）且仍 TRIAL；有派发链接 + USABLE ACCEPT 证书 → ADMITTED，admission 记录该证书，重放同命令同 row_version；第二个评估未派发 → EVALUATION_NOT_DISPATCHED；同一 mission 不能再挂第二个评估（SOURCE_HASH_CONFLICT）；派到账本里不存在的 mission → MISSION_MISSING；派发链接幂等且不可改；根 incarnation 不同 → ROOT_CHANGED；过期 → CERTIFICATE_EXPIRED；未绑定目录 → SOURCE_UNAVAILABLE。第二项（审阅回归）：Mission 已带评估键、acceptance 先写出、之后才登记派发 → ACCEPTANCE_BEFORE_DISPATCH，仍 TRIAL。`test_arp_acceptance_gaps.py` 5 项见 §2。
- ARP 全量：340 passed（333 + 7）。
- legacy `tests/agents`（排除 arp）与 `tests/execution`：合并 main 后与本片修完后各跑一次，16 failed / 172 passed / 6 skipped 与 17 failed / 144 passed，与基线一致；本片改动只在 ARP 包内。
- assured 夹具单次约 1 秒，不构成成本。

## 4. 独立核验

opus 5.5 子代理按"只报阻断级"审阅（1 轮，自己写了一次性探针复现后删除，工作区未留痕），回 1 条阻断，已修复并补回归测试：

| # | 发现 | 处置 |
|---|---|---|
| 1 | 派发链接只是 Host 的断言，可以事后挂到账本里任何一个更早已接受的 Mission 上（复现：先 `accept_now` 铸证书，再导入 Skill、试用、登记派发到那个旧 mission、admit → ADMITTED）；task_id 可省略时同一 mission 多任务只要一个被接受就能准入；唯一性按 namespace 使同一证书可在每个 namespace 各准入一次 | ①评估与 Mission 的绑定移到编排库：Host 必须以 `evaluation_mission_key(evaluation)` 作为评估 Mission 的 idempotency_key 创建，适配器读 `missions` 行核对；②证书签发时间与 acceptance 时间都必须不早于派发登记时间；③task_id 必填并核对 `acceptances.task_id`，另核对 `acceptance_id == 证书 consumer_id`；④`UNIQUE(mission_id)` 改为全局。回归：`test_an_acceptance_issued_before_the_dispatch_was_recorded_cannot_admit` |

其余核对无阻断：`read_view()` 与 ARP 事务分属两库无锁交叉；回执字段与 writer 一致；STALE/REVOKED 拒绝、重放幂等、无回执的证书行拒绝；生命周期方法不在模型工具面上；测试走真实账本无 monkeypatch。

## 5. 未做 / 下一步

- 第二层接缝其余两处：Host 处理器（读/写/重放各一条）与安装后导入核对——依赖 Host 接线。Host 侧接线要求：创建评估 Mission 时 `idempotency_key = evaluation_mission_key(evaluation)`，在同一流程里调用 `record_evaluation_dispatch(evaluation, mission_id, task_id=评估任务, …)`，随后才允许 acceptance；`ArpPorts.acceptance = AssuranceSkillAcceptance(store=orchestrator.store, clock_ms=与编排库同源的毫秒钟, root_incarnation=gate.require_execution)`。两库时钟须同源（时间先后比较）。
- 状态机随机测试小号版（6 动作、2000 步）——本片之后。
- Mission 模式来源（TaskGraph / InputManifest / Assurance 读用途 / 曝光导入 / 正式 pin GC）：创建协议仍只开 STANDALONE_CHAT，按名拒绝；Assurance 线的"证书签发即 SOURCE_CHANGED"（读集粒度）问题不影响本适配器（准入只看 acceptance 行 validity 与证书 decision，不比 epoch）。
- 真机路径与真实模型 12 局：需 Host 接线与 runner。
