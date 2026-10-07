# Assurance 1.1 原始计划对照（严格评估，2026-10-06）

**结论摘要**
- 对照对象：`ASSURANCE-EXEC-1.1`（2026-09-22）正文 + `implementation/` 全部资产 + F01～F15；代码 main `b4f1d314`（SDK opt.162）。只读，未跑测试。
- 正文可检验要求 **330 条**（旧清单 314 + 本次补出 16）：按计划在用 209、做法不同 49、部分 36、A 级排除 21、未做 10、已删 2、只在测试 2、做了没接上 1。
- 资产逐条：integration-map 61 项在用 38；ref-map kinds 29 在用 21（4 类无解析器、3 类无人构造）；SQL 129 列全部一致、48 触发器全在；sdk-cases 48 组只有 18 组断言完整覆盖；OCC 12 只有 1 条；继承 66 只有 21 条（X 组 18 条 + CA-I10 无对应）；计划改坏 28 条按计划 18 条（M05、F-M12 无；M01/M02/M03/M07/M10/M12 只有 09-23 旧 AM；M06 登记不绑）。
- F01～F15：按计划 2（F10、F11）、部分 10、做法不同 1（F05）、未做 1（F15）、A 级 1（F07）。
- **C 级无记录偏离 40 条**（代码 29、验收 11）；B 级子代理裁决 12 条 + 只有实施自述的 B′ 22 条；D 级待办 4 项。
- 最严重的三条：① 危险效果结果未知时不收尾（BLOCKED_UNKNOWN）全测试库无一处断言，F01/F02/F03/F06/F08 反例被映射表标"COVERED"而实际缺关键断言（C-30、C-31）；② 收尾最后事务仍不比纪元、不重查证书到期与时钟（opt.162 只补了资料变更一项，C-01/B-1）；③ 今天正在跑的真实模型脚本把"超预算"判为通过、场景 3/4 预算放宽、同局号重跑覆盖旧记录，与计划口径相反（C-39），且 M02 当前 0/3。

## 读法与口径

- 对照基线：`plans/Assurance/specs/1.1/ASSURANCE-EXEC-1.1.zh-CN.md` 全文、`implementation/` 全部资产、`RESPONSE-TO-REVIEW.md`（F01～F15）、`VALIDATION.md`、`contracts/`、`sql/`、`tests/`。
- 代码版本：main `b4f1d314`（Host 钉 SDK opt.162）。本次只读：没跑任何 pytest，没改任何源码与文档。SQL 列与触发器的比对是在 scratchpad 里用 SDK 现行迁移链新建一个空库后读 `PRAGMA`/`sqlite_master` 得到的。
- 正文要求的编号沿用 `Assurance-现状对照-2026-10-05.md` 附三"要求清单全表（314 条）"的编号（只借用它的拆分，结论全部在当前 HEAD 重核），另有本次补出的"新增"条目。
- 路径简写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/orchestration/`；`FE/` = `tauri-app/src/`；`P/` = `plans/Assurance/specs/1.1/`；`SP/` = `sdk/simple-harness-sdk/plans/assurance-1.1/`；`D/` = `plans/2026-09-27-desktop-next/`；`E/` = `.local-test-evidence/`；`WIP` = `SP/BODY-WORK-IN-PROGRESS.md`；`ASSUR` = `ARCHITECTURE/ASSURANCE.md`。
- 文件简写（都在 `SDK/orchestrator/` 下，除非另注）：EH=`event_handler.py`、CS=`commit_service.py`、RC=`resolution_commits.py`、TR=`assurance_review_transport.py`、COL=`assurance_review_collect.py`、CON=`assurance_review_consumer.py`、IMP=`assurance_review_import.py`、RT=`assurance_review_runtime.py`、PR=`assurance_purpose_reviews.py`、CR=`assurance_content_review.py`、CP=`assurance_check_policy.py`、LC=`assurance_local_checks.py`、VAL=`assurance_validity.py`、ACON=`assurance_consumers.py`、FW=`assurance_final_writer.py`、TICK=`assurance_tick.py`；RD=`SDK/storage/assurance_reads.py`、WORK=`SDK/storage/assurance_work.py`、CK=`SDK/assurance/checks.py`、RET=`SDK/verification/reviewer_evidence_tools.py`。
- 产品入口链（"按计划在用"的共同起点，已核）：`Host/service.py:205-221,332-348` → `SDK/deployment/assembly.py:65-97`（`install_assurance`）→ `SDK/orchestrator/assurance_assembly.py:349-470`（四个消费者、工厂、tick、读接口）；主循环 EH:3709 停老任务、EH:3713 跑保证通道 tick。六类审阅触发点：做法 EH:6822、内容 EH:8269/8619、终审 EH:9845、组合 `composition_review.py:221`、操作提案/结果 `operation_runtime.py:128/311`。
- 判定用语严格按 `00-评估口径与排除清单.md` 第五节；命中第三节用户排除的写"A 级排除（#编号）"。"记录"一栏区分：**用户原话**、**子代理裁决**、**实施自述**（实施记录/架构文档/清单里的自我说明，没有裁决也没有用户原话）、**无记录**。

## 第一节 · 逐条对照大表

### 1.1 正文 §0～§6（清单 1～122）

| # | 原文行 | 计划条目 | 判定 | 代码证据 | 差距说明 / 记录 |
|---|---|---|---|---|---|
| 1 | L7 | OCC 完成范围、Operation 身份、D3 raw 恢复语义不变 | 按计划在用 | ACON:481 经 `completion_status.read_current_effect`；`operation_materialization.py:466` 仍由原 envelope 签 operation_id | — |
| 2 | L9 | 迁移号取下一空号，不占 TaskGraph 的 23 | 按计划在用 | `SDK/storage/schema.py:1401-1402`（TaskGraph 25、Assurance 26） | 26 号发布前原地改过（WIP:1055，实施自述） |
| 3 | L11 | 复用 scoped/root/composition/operation 链，不另建主链 | 按计划在用 | CR:72；`composition_review.py:221`；`operation_runtime.py:128,311` | — |
| 4 | L15 | 完整交付后新 Mission 默认 ON | 做法不同 | `SDK/orchestrator/assurance_factory.py:50` `selects`、`:152` 没装工厂拒建任务；Host 无 `assurance_profile` | 比计划更进一步：没有关的开口。先开后验收是用户 09-23 拍板（WIP:1245）；删开关属 A#3/#17 |
| 5 | L15 | 既有活动 Mission 不静默换协议 | 按计划在用 | EH:1498-1556 `_refuse_unsupported_contract` 按名停老任务 | 以"停"满足"不换"；旧任务不能再跑属 A#17 |
| 6 | L15 | 默认 ON 不解除 TaskGraph 自身门禁 | 按计划在用 | `taskgraph_policy.py:124-131` `require_enable` | TaskGraph 独立开关已删属 A#3 |
| 7 | L15 | 审阅模型读当前批准 profile，不改 DeepSeek 配置 | 按计划在用 | RT:232、:433 `_route_service` 取 `decision.profile_id` | — |
| 8 | L15 | 真实模型未实跑不填 PASS | 按计划在用 | ASSUR:7"仍没做：真实模型四场景各 3 局" | — |
| 9 | L19 | I1 不改旧 V1 合同，新增走旁挂绑定 | A 级排除（#9、#17） | 旁挂在用 IMP:412；但 `contracts/resolution.py` ReviewPackage 加字段、`Criterion.phase` 删 | H-3（10-04 用户逐项定）、开发期不兼容旧数据 |
| 10 | L20 | I2 六 purpose 不增、原派发/预算、无第二调度器 | 按计划在用 | `contracts/resolution.py:554`；TR:380 `create_service_intent`；EH:3713 | — |
| 11 | L21 | I3 效果只经 OperationCompletionReader | 按计划在用 | `completion_status.py:59`；`operation_outcomes.py:56` | — |
| 12 | L22 | I4 正式审阅/接受/终态/源改动全部接管 | 按计划在用 | `SDK/storage/htn_store.py:957-966`（只认 `PreparedOfficialReview`）；RC:673,1056-1059；FW:178 是全包唯一写 COMPLETED；屏障 `assurance_barrier_v26.sql` | 终态写点枚举测试不全见 #131 |
| 13 | L23 | I5 缺数据具名失败，合法空读有完整读证明 | 按计划在用 | RD:47-61、RD:294 | — |
| 14 | L24 | I6 raw/费用/历史不丢，UNKNOWN 不升 PASS | 按计划在用 | COL:55；CON:368 `AssuranceReviewLateTurn`；CK:202 | "人裁决通过"是用户决定（A#20），"空 code_test 视为满足"（A#15） |
| 15 | L25 | I7 本机撤回立即使旧使用证书无效 | 部分 | 新使用一律重核纪元（VAL:686-713,768）；**已签证书在收尾时只重读钉住对象**（`assurance_recheck.py:43`），纪元不比（ACON:362 `VOLATILE`），资料变更靠 ACON:469-473 `SOURCE_CHANGE_OPEN` | 依据之外的写入（纪元已动）、访问/策略变化在收尾拦不住。opt.110 方案（D/收尾前复查依据-方案.md）写了"只比钉住对象"，未写其余后果 → B 级（见第三节 B-1） |
| 16 | L26 | I8 恢复用新隔离根 + 新授权 | A 级排除（#4） | 全库无 `offline_backup`/`restore_offline` | 10-02 决定② |
| 17 | L38 | TASK_CONTENT 先读投影再构包，accept_review+OCC 消费 | 按计划在用 | CR:35、:72；RC:673 `_require_assured_use` | — |
| 18 | L38 | 已有受信 Critic 经绑定适配，不重问 | 按计划在用 | RT:108、RT:399-427 复用已有记录/调用 | 旧 Critic 导入支路删（原文"可"，非必须） |
| 19 | L39 | METHOD_PLAN 建原审阅包，由原方法准入写方消费 | 做法不同 | EH:6822；消费改在计划提交闸门 `plan_commits.py:381` → `method_plan_reviews.py:118` | 用户 10-01 定"新做法须过独立审阅"（D/HTN-后续-方案.md:6）；闸门位置是实施（D/HTN-片A-实施记录.md:35） |
| 20 | L39 | 结构检查不冒充语义审阅；只准入方法不完成 Task | 按计划在用 | `plan_commits.py:381-400` | — |
| 21 | L40 | COMPOSITION 先组合后接受 | 按计划在用 | `composition_review.py:221`；RC:1059,1762 | — |
| 22 | L41 | ACTION_PROPOSAL 正式 Review 决定能否进 T1 | 按计划在用 | `operation_runtime.py:113-128`；`operation_materialization.py:341` 起只认正式记录 | 消费时不核使用证书（计划未要求，见 #278） |
| 23 | L41 | 审阅通过不代替用户审批 | 按计划在用 | `action_commits.py:545` 起仍进 AWAITING_APPROVAL | — |
| 24 | L42 | OPERATION_OUTCOME 先读完整效果链；唯一效果写方 | 按计划在用 | `operation_runtime.py:295-311`；`operation_outcomes.py:552` | — |
| 25 | L43 | MISSION_FINAL 每轮独立 package | 按计划在用 | `root_review.py:840` 每次 cut 新包；PR:64 | — |
| 26 | L45 | cut 委托统一 builder，不双发 | 按计划在用 | `root_review.py:840-859` 只切包；EH:9832-9845 只经 `ensure_mission_final` | `root_review.py:1072` `request()` 无调用者（死代码，无记录） |
| 27 | L45 | record_review 须已验证来源，裸结论拒绝 | 按计划在用 | `htn_store.py:957-966`；IMP:574 | 旧 `record_review` 删属 A#3 |
| 28 | L57 | 内部 AssuranceRef，不改公开 TypedRef | 按计划在用 | `SDK/assurance/refs.py:62` | — |
| 29 | L57 | 种类唯一来源 common.schema.json | 部分 | schema 字节同计划；但 `refs.py:12-44` `REF_KINDS` 是手写副本，不从 schema 读，无测试钉两者一致 | 无记录 |
| 30 | L57 | 只用 ref-resolution-map 字段子集，禁反射解析 | 按计划在用 | RD:111-129 `_EXACT`；`SDK/assurance/event_kinds.py:4` | — |
| 31 | L59 | 返回 `ResolvedRef(body,pin,tenant,mission,issuer,state_witness)` 或 SourceUnavailable | 做法不同 | RD:148 返回 `ExactMetadata(ref,body_json,lifecycle_json)`，失败抛 `AssuranceError` | 缺 tenant/issuer/state_witness 字段；无记录 |
| 32 | L59 | 归属以真实查询为准 | 按计划在用 | RD:141、:167-198 | — |
| 33 | L59 | 过期拒新动作，历史 raw/成本可导入 | 按计划在用 | CON:292、CON:368 `_prepare_late` | — |
| 34 | L59 | 未知 kind/缺失/同 ID 异内容/跨 Mission/错 collector 各自拒绝码 | 部分 | RD:157-229 有 `REF_KIND_UNSUPPORTED/SOURCE_UNAVAILABLE/REF_BODY_CONFLICT/REF_SCOPE_MISMATCH/REF_ISSUER_MISMATCH` | 计划 ref-resolution-map 每 kind 的 `SOURCE_NOT_CURRENT` 全包未实现（grep 为零）；无记录 |
| 35 | L63 | commit_receipt：不可变、rev=0、真实 Commit writer 签发 | 部分 | RD:127、:191 只核 `body.mission_id`；回执表无写方列 | 不核写者/回执种类（各消费方自核 kind，如 TR:740）；无记录 |
| 36 | L64 | reservation_fact 桥事件，含原 reserve 身份/账户/上限 | 按计划在用 | TR:388-418 | 金额上限改 token 上限（A#1 金额计价删） |
| 37 | L65 | agent_turn_receipt 核 profile/intent/agent/turn/hash 后导入 | 按计划在用 | `SDK/runtime/assurance_turn_sources.py:36-85`；COL:110-131 | — |
| 38 | L66 | execution_receipt 受信 importer 核调用身份 | 按计划在用 | LC:61、:189-330 | 只有 code_test 走到 |
| 39 | L67 | local_check_receipt 实际执行后导入，不收 API bool | 按计划在用 | `SDK/verification/assurance_local.py:135`；LC:529 | — |
| 40 | L68 | check_binding 写方只接两种真实来源 | 按计划在用 | `assurance_check_import.py:24-62` | — |
| 41 | L69 | check_spec 在 registry 安装时由固定系统写 | 做法不同 | LC:478-511 每任务首次用到时懒注册 | 无记录 |
| 42 | L70 | check_policy 引用精确要求版本与范围 | 按计划在用 | CP:265-271；`assurance_schema.sql:260` | — |
| 43 | L71 | disclosure_receipt 由清单导入方写且先于结论 | 按计划在用 | RET:419、:489 | — |
| 44 | L72 | review_package 由六 builder 产、精确匹配、rev=0 | 按计划在用 | RD:115、:164；TR:55-75 | — |
| 45 | L73 | result 只取不可变 envelope | 按计划在用 | RD:198 | — |
| 46 | L74 | task 不用心跳行版本 | 按计划在用 | RD:113 | — |
| 47 | L74 | method_instance 读明确实例与 plan pin | 按计划在用 | RD:124；PR:741-745 | — |
| 48 | L75 | artifact 完整字节、nofollow、核 hash，URI 不是权限 | 按计划在用 | `SDK/storage/assurance_blobs.py:172-178` | — |
| 49 | L76 | source/observation/review/acceptance/resolution 精确 body，可用性另查 | 按计划在用 | RD:111-129；VAL:686 | observation、resolution 解析器生产无人构造（见 1.2 ref-map） |
| 50 | L77 | completion_scope/spec 用原 OCC reader | 做法不同 | RD:119-120 直接读表 | 无记录 |
| 51 | L78 | operation/tool_receipt 只读核对 | 部分 | operation 在用（RD:125）；`tool_receipt` 读取报 `REF_KIND_UNSUPPORTED`（RD:157），却仍被 `SDK/assurance/reviews.py:246` 允许作 OPERATION_OUTCOME 审阅对象 | 无记录（只在 `T/acceptance_assets/assurance_findings.json` F02 的 gap 里登记为缺口） |
| 52 | L79 | policy/authority/capability 读当前生效 | 部分 | 三类均 `REF_KIND_UNSUPPORTED`；当前权限改由读集 ACCESS/POLICY 通道（`assurance_check_use._permission`） | 无记录（同上登记） |
| 53 | L81 | 事件 Ref hash 覆盖原 Event 全体 | 按计划在用 | RD:209-229（`Event.to_json`） | — |
| 54 | L81 | 同来源同 body 幂等，异 body 拒 | 按计划在用 | COL:85-91；LC:270-296；no_update/no_replace 触发器 | — |
| 55 | L85 | 创建顺序 pin→包+预留+intent→Linked→invocation→回执 | 按计划在用 | CR:274；TR:275-465 同一 `atomic` | — |
| 56 | L85 | Binding 不引用自身 hash/未来 official | 按计划在用 | `SDK/assurance/reviews.py`；CR:198-260 | — |
| 57 | L87 | 收包：raw/费用→TurnImported→official→side binding | 按计划在用 | COL:55→:110→:165→CON | — |
| 58 | L87 | 已 SETTLED 的预留也能导入历史审阅/费用 | 按计划在用 | TR:467-473、:576-579 | — |
| 59 | L87 | 只有新 invocation 要新真实预留 | 按计划在用 | TR:393-401 | — |
| 60 | L87 | 新 invocation 不新建账户，累计同 owner | 按计划在用 | TR:236-237 | 账户选择见 #106 |
| 61 | L94 | 标签 = ev-+sha256(kind,review_key,ref) | 按计划在用 | `SDK/assurance/evidence.py:15` | — |
| 62 | L97 | 完整 64 hex；同 ID 异 kind/rev/hash 不同标签 | 按计划在用 | `codec.py:148` | F03 反例 `test_review_findings.py:49` |
| 63 | L97 | 同 label 异 ref 拒；字典序；catalogue hash 存 binding | 按计划在用 | `evidence.py:34-43`；CR:259 | — |
| 64 | L97 | 重复 exact ref 去重，标签不靠位置 | 按计划在用 | `evidence.py:36-41` | — |
| 65 | L101 | 初始曝光：输入 hash 对齐冻结目录，生成 batch 0 | 按计划在用 | COL:276；`SDK/assurance/review_input.py:144-179` | — |
| 66 | L102 | 追加证据两只读工具，经 ToolGateway | 按计划在用 | `SDK/runtime/tool_gateway.py:154`；RET:77、:342 | — |
| 67 | L102 | 工具返回但未进模型输入不算曝光 | 按计划在用 | RET:489-520 | — |
| 68 | L103 | 同 turn 靠消息 ID 关联，缺证明保持 UNEXPOSED | 按计划在用 | `assurance_turn_sources.py:94-126`；COL:145-151 | — |
| 69 | L104 | batch 字段固定；同号同体重放不新增，异体冲突 | 按计划在用 | RET:432-445；IMP:271-298 | 追加冲突没有反例用例（见 F03） |
| 70 | L104 | 初始与追加同一 ref 同一标签 | 按计划在用 | 标签是纯函数 | — |
| 71 | L105 | 格式修复新输入带目录/材料 | 按计划在用 | TR:830-852；TR:243 | — |
| 72 | L105 | 旧 Agent 私有上下文不给新 Agent | 按计划在用 | TR:229 序号 2 新 Agent；CON:418-466 | — |
| 73 | L106 | official 只认产生结论那一轮实收曝光 | 按计划在用 | IMP:261、:285-293 | — |
| 74 | L106 | Replay 只用 raw+binding+disclosure，不查 latest | 按计划在用 | CON:159-180；IMP:183-322 | — |
| 75 | L108 | 追加读取受工具预算/scope/只读约束 | 按计划在用 | RET:41（32 次）、:107、:358；RT:44 | — |
| 76 | L108 | 根审阅员新模板显式装两工具 | 按计划在用 | RT:247、:449 | "旧模板空集合"半句属 A#3 |
| 77 | L114 | approve_assurance_check_policy 唯一批准写方 | 按计划在用 | CS:632 → CP:40；唯一插入 CP:285 | — |
| 78 | L114 | 在原 Requirements 批准同一事务里写 | 做法不同 | `SDK/deployment/duties.py:160` `project_check_policies` 每轮投影（`HOST_LOSSLESS_AUTO`），审阅前 RT:476 再投一次 | 实施自述（WIP:1259-1267，"Host 没人写"），无裁决无用户原话 → 第三节 B′ |
| 79 | L114 | 政策域绑要求 hash+scope hash，builder 改不了 mode | 按计划在用 | CP:265-283；`assurance_schema.sql:260,389-393` | — |
| 80 | L116 | 无损适配：具名检查→CHECKED，仅语义才 SEMANTIC，其余 UNRESOLVED | 按计划在用 | CP:412-439、:241-250 | — |
| 81 | L116 | 只有批准的替代组才能 OR | 按计划在用 | CP:252-258 | 产品里实际只有单 AND 组 |
| 82 | L116 | TASK_CONTENT 用 scoped projection 判据，不因缺 execution_ref 永久拒 | 按计划在用 | CP:207、:401 | — |
| 83 | L120 | format/rule 外包 recorder，事务外执行再导入 | 按计划在用 | `verifier_router.py:150-163,207-209`；`assurance_local.py:135` | — |
| 84 | L120 | 运行前固定 checker digest/subject/env/run id；逐断言 hash | 按计划在用 | `SDK/assurance/local_checks.py:86-111` | — |
| 85 | L120 | 抛错记 ERROR；单断言键；缺断言 UNKNOWN；重复拒 | 按计划在用 | `local_checks.py:114-134,191-203` | — |
| 86 | L122 | citation 只证明指定段落 | A 级排除（#8） | `SDK/assurance/check_specs.py:108` 只剩 format_check、rule_check | 文档引用交审阅员判（D/HTN补齐计划-2026-10-02.md 表一 12，opt.134） |
| 87 | L122 | code_test 含 nodeid/平台/环境/目标；exit0 不单独代表正确 | A 级排除（#15） | `SDK/assurance/executor_checks.py:42-160`；:126-145 无目标+收集不到→PASS | 09-24 用户决定 |
| 88 | L122 | executor 跑过就用其回执，不重跑 | 按计划在用 | `verifier_router.py:217-237`；`assurance_local.py:199` | — |
| 89 | L122 | 未持久的检查仍 UNKNOWN，不靠 cache 猜 PASS | 按计划在用 | `verifier_router.py:200-203` | — |
| 90 | L126 | 执行记录真值表 | 按计划在用 | CK:180-206、:217；`local_checks.py:143` | — |
| 91 | L128 | CHECKED 组内 AND、组间 OR | 按计划在用 | CK:217-238 | — |
| 92 | L128 | SEMANTIC 检查门 NOT_APPLICABLE | 按计划在用 | CK:220-221 | 改坏 AS-FM03 10-05 被抓 |
| 93 | L130 | 模型等级 × 检查门三值表 | 按计划在用 | CK:449-451 逐格一致 | — |
| 94 | L136 | BLOCKER 强制 FAIL；mandatory 单独 ALL | 按计划在用 | CK:455-464 | — |
| 95 | L136 | 全局安全 finding 须映射 mandatory，映射不了拒整条回复 | 未做 | CK:337-339 finding 只要求挂在某条准则上（`FINDING_SCOPE`），没有"全局安全"类 | 只在 `assurance_findings.json` F04 gap 登记为缺口（实施自述），无裁决 → 第三节 B′ |
| 96 | L136 | ALL/ANY 三值；见证按冻结顺序 | 按计划在用 | CK:101-116 | — |
| 97 | L138 | decide_review 返回五字段 | 按计划在用 | CK:418-476 | — |
| 98 | L138 | 模型非 ACCEPT 时公式通过也不批 | 按计划在用 | CK:465；IMP:479-480 | — |
| 99 | L138 | 失败组反证仍在有效性中核对；硬门不受 ANY 绕过 | 部分 | 硬门独立（VAL:686-713）；有效性只用被消费的检查（`SDK/knowledge/assurance_sources.py:305-320`） | 子代理裁决 D/HTN补齐-阶段D-开工裁决与施工清单.md:55-62（偏差单 3，"已告知用户"）→ B 级 |
| 100 | L144 | 一个 round 一个 Package 至多两次调用 | 按计划在用 | TR:228；invocations 唯一约束 | — |
| 101 | L144 | 格式修复不是新 round；独立二审要新 round/package | A 级排除（#20） | 二审改为同包第 2 次调用（CON:418 `_prepare_second_opinion`） | 09-30 用户决定 |
| 102 | L144 | 每包唯一 official 约束保留 | 按计划在用 | `SDK/storage/htn_schema.py:355-356` | — |
| 103 | L147 | subject_id/creation_key/input_id 格式 | 按计划在用 | TR:229-230、:385-386 | 改坏 AS-FM04 被抓 |
| 104 | L150 | kind='plan' | 做法不同 | TR:381 TASK_CONTENT 用 'critic' | 实施自述 WIP:337；原文"沿当前 dispatch 类型"，偏差很小 |
| 105 | L151 | config 带 role 与保证字段 | 按计划在用 | TR:246-257 | — |
| 106 | L154 | 按 account_for_purpose 记账 | 做法不同 | TR:177-199 `review_budget_subject`：组合/根终审记任务总账，操作类记准备操作的叶子 | 实施自述 D/PLAN-STATUS.md:306（opt.45）、D/HTN-片B-实施记录.md:178；HTN补齐计划表一 22 写"保持"→ B 级 |
| 107 | L154 | 预留/intent/包/绑定/事件/回执同一外层事务 | 按计划在用 | TR:275；`SDK/storage/store.py:426-441` | — |
| 108 | L154 | 原方法自开事务则提取 `_create_service_intent_locked` | 做法不同 | 未提取，原 `create_service_intent` 并入外层 savepoint（CS:839） | 实施自述 WIP:286-287 |
| 109 | L154 | 不声称跨库原子；原 dispatch 执行后导入 | 按计划在用 | COL:28-41 | — |
| 110 | L156 | 六类切换表 | 按计划在用 | 六触发点见"读法"；COL:28；CON:124 | METHOD_PLAN 下一步见 #19 |
| 111 | L165 | 新 profile 不接裸 verdict；accept/record 查绑定+TurnImported | 按计划在用 | IMP:574、:183-245；`htn_store.py:963-966` | — |
| 112 | L165 | legacy 按持久 lane 分派 | A 级排除（#3） | `assurance_factory.py:172` 只认 ASSURANCE_1_1 | — |
| 113 | L171 | 同通知/命令重送回原回执，不重预留 | 按计划在用 | TR:284-298 | — |
| 114 | L172 | 首次合法请求：ordinal1，真预留后派发 | 按计划在用 | TR:380-401、:609 | — |
| 115 | L173 | 可解析 REWORK/INCONCLUSIVE 不自动 ordinal2 | A 级排除（#20） | INCONCLUSIVE 首次复审一次（CON:305-309） | REWORK 照旧 |
| 116 | L174 | 不可解析：同包 ordinal2+新真实预留，旧费用照算 | 按计划在用 | CON:515；TR:797-878 | 扩展到"能解码不能导入"（ASSUR 09-26 第 4 条，实施自述）；第二次仍坏问人属 A#20 |
| 117 | L175 | Provider UNKNOWN 不能靠 ordinal2 重发 | 做法不同 | COL:219 `abandon_assurance_review`：超时记 TURN_FAILED 再走第 2 次调用（`TURN_RETRY`），旧预留按上限计 | 子代理裁决 D/HTN补齐-阶段B卡住缺陷-裁决.md:271-300（第 6 类），引用用户 09-28"基础设施失败原地重试"（A#14 只讲"不计次数"，不等于允许重发）→ B 级 |
| 118 | L176 | 获准二审或内容返工：新 round/package | A 级排除（#20） | 内容返工新结果新包（在用）；二审同包第 2 次调用 | — |
| 119 | L176/179 | round 上限用原冻结责任政策 | 做法不同 | 绑定 `round_no` 恒为 1（PR:283、CR:206）；上限落在 `root_review.py:104` 与 Task 尝试次数 | 有界，但轮次字段名存实亡；无记录 |
| 120 | L177 | 另一 invocation 晚到：存 raw/费用，不占 official | 按计划在用 | CON:169-174、:368 | — |
| 121 | L179 | 格式修复上限 1 次 | 按计划在用 | TR:792；序号 ≤2 | — |
| 122 | L179 | binding 不放 dispatch 字段，另建 invocations；按真实调用关联 | 按计划在用 | TR:336-362、:451-465；COL:39 | — |
| 新-1 | L59 | 解析时核"要求的用途"和"当前访问" | 做法不同 | RD:148 只核正文身份（注释"不断言当前可用"）；用途/权限在使用点另核（CR:186、RET:358） | 无记录；新使用点漏调 `_permission` 就只凭正文放行 |

### 1.2 正文 §7～§9（清单 123～184）

| # | 原文行 | 计划条目 | 判定 | 代码证据 | 差距说明 / 记录 |
|---|---|---|---|---|---|
| 123 | L187 | accept_result 原样保存，内容接受走原路径+guard | 按计划在用 | CS:2773 → CS:2865 → RC:589 | — |
| 124 | L188 | accept_review：OCC/official/检查/证书同事务 | 按计划在用 | RC:664、:673 → :1746 `commit_use_locked`；VAL:710-718 | — |
| 125 | L189 | commit_goal_resolution 要完整 bundle，直调也绕不过 | 按计划在用 | RC:1052-1066（根/组合，其余 REVIEW_PURPOSE_MISMATCH） | 改坏 AS-A18 被抓 |
| 126 | L190 | completion_* 按用途读；MIXED 未满足效果不被内容接受顶替 | 按计划在用 | `completion_status.py`；`review_adjudication.accepted_or_adjudicated` | 抽查 |
| 127 | L191 | 正式记录只能由统一收集器写 | 按计划在用 | `htn_store.py:936-966` | — |
| 128 | L192 | DeliveryReceipt 沿用 T3 精确绑定 | 按计划在用 | `htn_store.py:1913`，唯一调用者 RC:796 | 改要求后同一发布两张回执（子代理裁决，B 级 B-5） |
| 129 | L193 | judge_mission 只请求 closeout，不直接 COMPLETED/不提前释放 | 按计划在用 | CS:3133-3149：`require_assured` 读不到按名拒 → `request_assured_closeout` | opt.161/162 已删直写尾巴；"legacy 保持"属 A#3 |
| 130 | L194 | 取消/失败/超时保留 UNKNOWN 与 hold | 按计划在用 | CS:1582-1597 `_cascade_stop`；CS:2235-2242；EH:1687-1720 | 判定"不满足"的 FAILED 也走 `_assured_terminal_notice`（CS:3165） |
| 131 | L195 | 枚举全部 terminal 写点并用测试证明无旁路 | 部分 | `T/full_target/test_terminal_unknown_release.py:59-76` 只数 EH 里 4 个调用；`:148-164` 只用正则扫 `next_mission(...COMPLETED` | 未枚举：`SDK/api/facade.py:608`、`api/missions.py:147` 取消、`human_commits.py:559` 接管停止、CS:3150 判定写失败、`operation_outcomes.py:667` 写 Task 完成、`__main__.py:61`；无记录 |
| 132 | L197 | 唯一 final writer：同事务 update→安全释放 terminal pools→MissionCompleted；不公开 | 部分 | FW:125-236 只认 READY、重读行/结论/版本/判定；生产只装为收尾 finalizer（`assurance_assembly.py:411`） | 不调任何释放；FW:11 说明仍写"releases the terminal pools"（系统尾池随删旧平面模式删，A#3）；`CommitService.finalize_assured_mission`（CS:3281-3290）无调用者；说明过时无记录 |
| 133 | L201 | root 满足后 Mission 仍 ACTIVE，调度视为收尾中 | 按计划在用 | EH:9133；`progress.py:64` CLOSEOUT_CONVERGING | — |
| 134 | L203 | 默认成功策略四档 | 按计划在用 | ACON:523-563 `_drain_decision` | BLOCKED_UNKNOWN 全测试库无一处引用（见 F06） |
| 135 | L203 | KEEP_HOLD_PENDING 只在政策允许时用 | A 级排除（#13） | ACON:576 `_upper_bound_plan`、:709 `settle_at_upper_bound` | 09-26 用户决定"按上限结清" |
| 136 | L205 | closeout INSERT NOT_READY/v1→READY→FINALIZED；最终事务重读 epoch/权限/effect/运行集合 | 部分 | ACON:666 先插 NOT_READY v1；SQL 守卫 `assurance_schema.sql:151-155,363-367,407`；提交前重评（ACON:720-735）重读效果、意图、预留、过期证书、`SOURCE_CHANGE_OPEN`（ACON:473，opt.162 新加） | **纪元被排除比对**（ACON:362 `VOLATILE={"evaluated_at_ms","epochs"}`），**不重查证书到期与时钟状态**，权限不比 → B 级 B-1（opt.110 方案） |
| 137 | L209 | 最后事务同写 MissionCompleted、通知请求、NOTIFY 工作与 receipt | 做法不同 | FW:181-226 同事务写完成事件、回执、`AssuranceStatusNotificationRequested`；NOTIFY 待办行下一轮 tick 读入才建 | 事件已持久，等价；无记录 |
| 138 | L209 | 只推 {mission_id,event_id,state_version}；Host 去重，重连按 seq 补读 | 做法不同 | ACON:798-805；`Host/notices.py:58-86` 去重，`backfill` 只在启动时（`Host/service.py:345`） | 运行中断线重连不按 seq 续读；无记录 |
| 139 | L211 | 发送前查读权/恢复门 | 按计划在用 | NOTIFY 准备与提交都调 `self._root()` | 单身份无 ACL（WIP:876 实施自述） |
| 140 | L211 | 已读无回执即 UNKNOWN；本地通知不完成外部效果 | 按计划在用 | `Host/notices.py:88-110` | — |
| 141 | L217 | 保留 validity_epochs；mission 聚合由 factory 建；无行不当 0 | 按计划在用 | `SDK/storage/assurance_store.py:182-185`；RD:47-60 | — |
| 142 | L217 | environment_state 只存全局 epoch/时钟 | 按计划在用 | `assurance_store.py:72,713` | — |
| 143 | L221 | put_source/替代/撤回/删除推 epoch，归属不明推 global | 按计划在用 | `SDK/storage/store.py:1583` → `assurance_barrier_v26.sql:12-80`；`source_commits.py:505-545` | 改坏 AS-FM07 被抓 |
| 144 | L222 | insert_observation 首次插入才 bump，正反证同等 | 按计划在用 | `htn_store.py:1279-1330` + observations 触发器 | 改坏 AS-M09 被抓 |
| 145 | L223 | justification/support/rule 准入撤回同事务 | 已删 | 迁移 39 删两表与触发器（`SDK/storage/schema.py:775-787`） | 子代理裁决（D/HTN补齐-阶段D-开工裁决与施工清单.md:55；D/HTN补齐-阶段C-偏差裁决-知识支持集合.md:11）→ B 级 B-3 |
| 146 | L224 | Requirements/OCC Spec/Scope 变化 bump | 按计划在用 | requirements_revisions、operation_completion_specs/scopes 触发器 | — |
| 147 | L225 | Method/Task 合同或采纳输入变更与 PlanCommit 同事务 | 按计划在用 | task_semantics、method_instances、plan_revisions 等触发器 | — |
| 148 | L227 | operation 更正/反证/执行结果导入 bump | 按计划在用 | actions/operation_*/results 触发器；`assurance_barrier_v26.sql:3631` | — |
| 149 | L228 | artifact 删除/访问、Check/Review 有效性更正 bump | 按计划在用 | artifacts、review_records、assurance_check_bindings、verifications 触发器 | — |
| 150 | L229 | scope/grant 撤回 bump | 按计划在用 | planning_lane_grants、completion_scopes 触发器 | — |
| 151 | L226 | ACL/policy/permission 变化推 global | 做法不同 | method_contracts、policy_versions/activations 触发器推全局（迁移 41）；无 ACL 表，`FixedPrincipalAuthority`（`assurance_assembly.py:56-150`） | 实施自述 WIP:876 → B′ |
| 152 | L231 | 下层写处同事务调 `_mark_assurance_change_locked` | 做法不同 | 无此函数，SQL 触发器替代（188 个 `assurance_source_*`） | 实施自述 WIP:44 → B′；效果更强 |
| 153 | L231 | 所有直接 SQL writer 扫描登记 owner | 做法不同 | 冻结清单 `SDK/storage/assurance_source_inventory.py`；`T/full_target/assurance_exec/test_review_findings.py:246-251` 断言每张表三道触发器 | 新表是否算依据表没有专门守护；实施自述 WIP:44-46 |
| 154 | L235 | CompleteRead 完整字段，没读完不 COMPLETE | 按计划在用 | RD:237-373 | 改坏 AS-M08 被抓 |
| 155 | L237 | 命题键 canonical({predicate,typed_args,namespace,scope}) | 做法不同 | `SDK/knowledge/predicates.py:294-301`：`id@version#`+32 位哈希，无 namespace/scope | 无记录 |
| 156 | L237 | 读集 (channel,key) 唯一，同指纹重复也拒 | 按计划在用 | `evidence.py:94-104` | — |
| 157 | L239-242 | OBJECT/QUERY_SET/ACCESS/POLICY 四种 key | 做法不同 | ACCESS key 加 ref（`assurance_assembly.py:115-131`）；QUERY_SET 加 selection、指纹不含纪元（RD:246-263） | 实施自述 WIP:1318、ASSUR opt.110 条 → B′ |
| 158 | L244 | 按通道/key 排序，拒未知通道 | 按计划在用 | `evidence.py:74-75,105` | — |
| 159 | L248 | 原正负支持最小不动点 + clean closure | 做法不同 | `assurance_sources.py:304-360` 只用审阅锚+检查锚+固定接受规则（`SDK/assurance/grounding.py`） | 子代理裁决（D/HTN补齐计划-2026-10-02.md:310，3.11 版）→ B-3 |
| 160 | L248 | 四个上限，超了 INCOMPLETE | 按计划在用 | `SDK/knowledge/bounded_closure.py:35-52`；`grounding.py:45-78` | 改坏 AS-M11 被抓 |
| 161 | L250 | 一致读→事务外计算→短写事务比 epoch | 按计划在用 | VAL:686-740 → RD:71-84 | — |
| 162 | L252 | 证书 ≤256KiB、读集 ≤20000、带根实例号 | 按计划在用 | `codec.py:15`；`evidence.py:103`；VAL:836 | — |
| 163 | L256 | 半开区间，最早界 | 按计划在用 | `SDK/assurance/certificates.py:220-223` | — |
| 164 | L256 | MAINTAIN 要持续监测，没有就挡 | 按计划在用（2026-10-07 A26） | 签发方唯一入口 `orchestrator/assurance_point_use.py` 缺持续监测即以 `MAINTAIN_MONITOR_UNAVAILABLE` 挡；`justifications.py` 里到不了的分支已删 | 无使用者归 A（#9） |
| 165 | L258 | 唯一计时者：发证同事务登记最早到期唤醒，启动重建 | 做法不同 | `SDK/assurance/expiry.py` `emit_due` 每轮扫证书表（TICK:160-166），启动 `reconcile_startup` 扫 | 实施自述 WIP:34 → B′ |
| 166 | L258 | 证书每次使用核 now | 按计划在用 | `certificates.py:220-223` | — |
| 167 | L260 | 时钟高水位/回拨/代次/STABLE 恢复 | 做法不同 | `SDK/assurance/clock.py:29-35`；`assurance_clock.py:19-90`；高水位每 10 秒落库一次 | "联测裁决 2026-10-05"（`ARCHITECTURE/AGENT_ORCHESTRATION.md:4`，子代理裁决）→ B 级 |
| 168 | L260 | 时钟不可信时拒时间敏感用途，raw/费用照常 | 按计划在用；PLAN/CONTEXT/RECOVERY 也拒（B 级 #43） | TICK:217-221；`facade.py:877`；时点签发共用最终核对 | `推后第1批-P1b-偏差裁决.md` 第 4 件 |
| 169 | L260 | lease 过期只回收协调权 | 按计划在用 | WORK:213-235 | 改坏 AS-M15 被抓 |
| 170 | L262 | 3 次立即→500/1000/2000ms；32 次/300s→MANUAL_REQUIRED | 按计划在用 | WORK:351-401、:249 | — |
| 171 | L262 | 新事件唤醒同一 work，累计上限保持 | 按计划在用 | WORK:141-155 | — |
| 172 | L266 | 持久 pending inbox + 原 tick | 按计划在用 | TICK:200 ← EH:3713 | — |
| 173 | L268 | target_epoch=触发 seq；更大合并，同 seq 异指纹冲突 | 按计划在用 | WORK:110-156 | — |
| 174 | L270 | 四 consumer 各自 cursor | 按计划在用 | WORK:20；`assurance_schema.sql:111-118` | — |
| 175 | L272-273 | 读页不推 cursor；同事务入队+推 cursor；禁先 ACK | 按计划在用 | WORK:157-195 | 改坏 AS-FM08 被抓 |
| 176 | L274 | 事务外 prepare；预算卡住的审阅不挡后续撤权 | 部分 | TICK:211-216 先全部 ingest 再 claim | 代码结构成立，**无专门用例**（F09 反例缺；ASSUR:7 实施自述） |
| 177 | L275 | 提交重读；效果+receipt+DONE 同事务；已有 receipt 也 ACK | 按计划在用 | WORK:300-327；ACON:84-99 | — |
| 178 | L276 | BUDGET_WAIT/CHECK_PENDING/RECHECK 各存 WAITING+原因；永久非法 REJECTED | 部分 | 只有 RECHECK 一类（CON:225）；预算/其它错误按错误码进 `recheck`（TICK:301-315） | 预算等待与"需重算"共用 32 次/300s 上限，可变 MANUAL_REQUIRED；无记录 |
| 179 | L276 | profile 未绑定/隔离存成等待，不触发格式重试 | 做法不同 | TICK:201,229,251 根闸门不过整轮不跑 | 不逐行存原因；无记录（受管恢复已删，影响小） |
| 180 | L277 | 准备后来源变了旧 worker 不能 ACK | 按计划在用 | `_enqueue` row_version+1 → `_assert_claim`；TICK:287-290 | — |
| 181 | L279 | 激活同事务 cursor=activation seq，并形成对账清单 | 按计划在用 | `assurance_factory.py:55-127`；`assurance_store.py:186-199`；`assurance_assembly.py:196` | 只有新建任务能激活，清单必为空 |
| 182 | L279 | cursor 丢失从 0 重建；源缺 SOURCE_UNAVAILABLE | 按计划在用 | TICK:173-183；ACON:99-110 | — |
| 183 | L281 | self events IGNORE | 按计划在用 | ACON:76 `DIAGNOSTIC_EVENTS` | — |
| 184 | L281 | 每 tick ≤128 页、每 consumer ≤8 | 按计划在用 | WORK:165,174；TICK:226 | — |

### 1.3 正文 §10～§16 与附录 A（清单 185～265）

| # | 原文行 | 计划条目 | 判定 | 代码证据 | 差距说明 / 记录 |
|---|---|---|---|---|---|
| 185 | L287 | 离线恢复到新目录、新根编号、隔离标记文件 | A 级排除（#4） | `SDK/assurance/root_gate.py:137-144`（`restore_manifest_hash` 恒 null） | 10-02 决定② |
| 186 | L289 | 根闸门早于 API/搜索/Context/调度 | 按计划在用 | EH:646-664；`SDK/api/facade.py:115-123`；`api/missions.py:126`、`runtime/actions.py:100`、`context/knowledge_tools.py:159`、`deployment/native_pools.py:176` | 名为 `require_execution`（等价名） |
| 187 | L289 | marker 缺失/不匹配/部分库 → QUARANTINED | 做法不同 | `SDK/deployment/assembly.py:65-75` 每次启动安装；`assurance_root_commits.py:132-138,167-173` 有旧回执就覆盖写状态文件 | 状态文件缺失/内容不对会被自动修回，不进隔离。只有 ASSUR:7"仍没做…未改"（实施自述）→ B′。A#4 删的是恢复，不是隔离 |
| 188 | L289 | 新空根经认证安装；非空未知根不当空库 | 按计划在用 | `assurance_root_commits.py:98-113` `_native_origin` | — |
| 189 | L293 | reauthorize_restored_read 入口链 | A 级排除（#4） | 全库无此函数 | — |
| 190 | L295 | 外部权威当前确认、READ_ONLY_REAUTHORIZED | A 级排除（#4） | 同上 | — |
| 191 | L297 | 先写库回执再原子写状态文件，崩溃幂等补 | 按计划在用 | `assurance_root_commits.py:40-64,167-170` | 只服务原生安装 |
| 192 | L297 | 备份导出排除可用根 grant | A 级排除（#4） | 无备份导出 | — |
| 193 | L299 | 恢复后读授权 24h、精确范围 | A 级排除（#4） | 仅剩同值 `assurance_assembly.py:53` `DEFAULT_READ_TTL_MS`（另一套固定身份权威） | — |
| 194 | L299 | 只授权披露，不复活旧执行 | A 级排除（#4） | 隔离时执行一律拒（`require_execution`） | — |
| 195 | L303 | 隔离时只给非披露诊断 | 只在测试 | `root_gate.py:195-207`；`facade.py:125-128`；唯一调用者 `sdk/simple-harness-sdk/scripts/assurance_seams/root-gate-seam.py:102` | ASSUR:6"保留"（实施自述）；产品里隔离态基本走不到（#187、新-11） |
| 196 | L307 | creation_contracts 不可变判别表 | 按计划在用 | 三触发器与计划逐字相同；`assurance_factory.py:119-126` | — |
| 197 | L307 | 新 lane 同事务写激活/绑定/epoch/cursor，缺一报错 | 按计划在用 | `assurance_factory.py:55-134,165-168`；`assurance_store.py:135-145` | — |
| 198 | L311-312 | LEGACY/COMPLETION_V1 保持原行为 | A 级排除（#3、#17） | `assurance_factory.py:172-180`；EH:1544-1556 | 迁移 26 仍给老库写这两类分类（`assurance_upgrade.py:80-108`，死数据） |
| 199 | L314 | 缺分类/矛盾 → CREATION_CONTRACT_UNRESOLVED | 按计划在用 | `assurance_store.py:245-250`；`assurance_factory.py:177-178`；CS:3144-3148 | 循环入口 `is_assured` 吞错码，见新-9 |
| 200 | L316 | 升级只按迁移前事实分类，留回执，不授权 | 按计划在用 | `SDK/storage/assurance_upgrade.py:18-108` | — |
| 201 | L316 | 从旧任务建后继才用新 profile | A 级排除（#17） | `assurance_store.py:280` 只剩枚举值 | — |
| 202 | L318 | 默认选择点 `default_assurance_profile_for_new_mission()` | 做法不同 | 函数已删；`assurance_factory.py:50-53` `selects()` 恒真 | 删开关属 A#3/#17；ARCHITECTURE/AGENT_ORCHESTRATION.md:109 |
| 203 | L318 | 完整验收后同批默认 ON | 做法不同 | 09-23/24 先开 | 用户 09-23 拍板（WIP:1245-1247）；验收后补算 D 级 |
| 204 | L318 | 风险动作原用户审批继续有效 | 按计划在用 | `SDK/deployment/duties.py:54-96` | A#11 |
| 205 | L318 | TaskGraph 独立开关继续有效 | A 级排除（#3） | — | — |
| 206 | L322 | 1.1 增量；1.0 校验和保留；取下一空号 | 按计划在用 | `schema.py:1402`（26）、`:1403`（27 后继） | — |
| 207 | L324 | 9 原表 + 6 新窄表 | 按计划在用 | 15 张表名完全一致（本次建库核对） | `assurance_pending_work` 多 `rechecks`、`recheck_started_at_ms` 两列 |
| 208 | L326 | closeout 初始 NOT_READY v1、只能 READY→FINALIZED、禁删禁替换 | 按计划在用 | 四触发器逐字相同 | 改坏 AS-FM09/10A/10B 被抓 |
| 209 | L326 | pin PREPARING/BOUND 同 Mission review/RELEASED 不重开；cursor/队列单调 | 按计划在用 | 对应触发器逐字相同 | pin 唯一键改按对象（迁移 27，见新-3）；改坏 AS-FM11 被抓 |
| 210 | L328 | Store 负责 hash 重算/回执归属/blob/当前授权 | 按计划在用 | RD；`assurance_store.py:135-145` | 回执写者不核见 #35 |
| 211 | L330 | 物理 SQLite 备份还原含终态行 | A 级排除（#4） | — | — |
| 212 | L330 | 空库按历史重放、不准 DROP 触发器、不足标 INCOMPLETE | 做法不同 | `SDK/observability/business_replay.py:1-30`（v3 只逐表核对，不重建库） | 子代理裁决（阶段 G 开工裁决）→ B 级 B-4 |
| 213 | L334 | check_plan.py 递归核字段/引用/DDL 列映射 | 未做 | 只在 `P/tools/`；SDK 无调用；`sql-column-producers.json` 无 rechecks 两列 | `assurance_findings.json` F12 PARTIAL（实施自述）；改坏 AS-FM12 自判"不适用" → B′ |
| 214 | L336 | 严格 JSON 各项、深度 64 | 按计划在用 | `SDK/assurance/codec.py:15,53-58,80-133` | 8MiB 读例外见新-6 |
| 215 | L336 | 公式深度 16/512 节点/256 准则；set 排序、分支保序 | 按计划在用 | CK:55-95；`codec.py:73-77`；`reviews.py:198`；`policy.py:12-25` | — |
| 216 | L336 | 长 raw 存 CAS，拒绝不丢费用 | 按计划在用 | COL:54-69 | — |
| 217 | L338 | CHECKED 组非空、SEMANTIC 无组 | 按计划在用 | CK:118-140 | — |
| 218 | L338 | authors 非空；METHOD_PLAN 无作者回 SOURCE_UNAVAILABLE；reason ≤2000 | 按计划在用 | `reviews.py:200-201`；PR:190；CK:330,344 | — |
| 219 | L346-348 | 三个下划线读 verb 薄委托 | 按计划在用 | `Host/handlers.py:42-44,257-259`；`Host/service.py:1412-1414`；`Host/assurance.py:59-82`；`facade.py:193-202` | — |
| 220 | L350 | 请求无 tenant/principal，caller 固定 | 按计划在用 | `SDK/api/assurance.py:614`；`facade.py:183-188`；`Host/assurance.py:66-67` | — |
| 221 | L350 | ≤100 项；CURRENT 同 seq；HISTORY 带 seq 按当前权限 | 按计划在用 | `api/assurance.py:35,618` | — |
| 222 | L350 | 返回 hash/body 不带 secret | 按计划在用 | review 只回原因码 | — |
| 223 | L352 | cursor 编码、排序、SNAPSHOT_CHANGED、history 钉 seq、truncated | 按计划在用 | `api/assurance.py:121-136,585-610` | — |
| 224 | L354 | projection.py 只映射正式状态 | 做法不同 | Host 不经 `projection.py`，原样转发 SDK 回复（`Host/assurance.py:72-82`） | 语义相同；无记录 |
| 225 | L354 | MissionsView 显示各块；非法 DTO 保留上次画面；不另开 WS | 按计划在用 | `FE/views/MissionAssurance.tsx:40-90,141-176`；`FE/stores/assuranceStore.ts:35,168`；`FE/views/MissionsView.tsx:1244` | — |
| 226 | L358-362 | 隔离 Host 副本+独立 venv+候选 wheel+prepare_native_manifest | 做法不同 | 共享 Host 钉 vendored wheel（`backend/deskpet/sdk_adapters/sdk_candidate.py:29-35`）；`prepare_native_manifest.py` 从未使用 | 实施自述（ARP HANDOFF §7.23）→ B′ |
| 227 | L364 | 启动记录 `__file__`/版本/wheel+模块 sha/PID/地址/指纹 | 部分 | 组装处实测 wheel 哈希（`composition.py:362`、`runtime_paths.py:41-66`）；`Host/assurance.py:34-52` wheel 哈希是常量；PID/地址/模块路径不逐局记录 | 只在旧对照文档列出（实施自述） |
| 228 | L368 | 七步原生点击 | 部分 | 只有主流程点击（ARP 实施记录、F2 联测） | 用户 10-05 定"排最后" → D 级；冷恢复一步属 A#4 |
| 229 | L368 | 多用户/跨 tenant 负例走接口 | 按计划在用 | `T/full_target/assurance_exec/test_c07_host_api.py:81-84`；`backend/tests/orchestration/test_assurance_host_api.py` | 本次未运行 |
| 230 | L368 | UI 不因 ID 是否存在泄露对象存在 | 按计划在用 | `facade.py:204-209`；EH:1381-1385 | — |
| 231 | L374 | AS-0 合同/来源固定 | 部分 | `SP/baseline.md` 在；`HANDOFF-SOURCE-MANIFEST.json` 已删（3a8c4697）；`E/2026-09-22` 本机不存在 | 本机证据已缺 |
| 232 | L378-382 | AS-BODY 1～5 项 | 按计划在用 | `SDK/orchestrator/assurance_*.py` 全套 | 隔离装载见 #226 |
| 233 | L386 | BODY_WIRED 按生产调用边逐条登记关闭 | 未做 | `SP/BODY-WIRED.md` 停在 09-23；WIP:3 仍写"BODY_WIRED 未通过" | 本次逐边核了代码（见 1.6），但计划要的登记从未做；无用户决定 |
| 234 | L390 | AS-VERIFY 集中验收顺序 | 做法不同 | — | 用户 09-23 拍板调整顺序（WIP:1245） |
| 235 | L390 | SQL 迁移/竞争/强退 | 按计划在用 | `T/full_target/assurance_exec/test_c_assurance.py`；`E/2026-09-23/assurance-1.1-verify/sdk-vc-groups.txt` | — |
| 236 | L390 | legacy/全量 H1 回归 | 部分 | 09-23 SDK 全量停在 89% 无汇总；10-01 `E/2026-10-01/final-opt112/结论.txt` 全过 | 10-02/03 删旧通道后未再跑全量（用户 10-01 规定不许自行跑全量，属流程约束） |
| 237 | L390 | 16 原 + F 定点变异按名执行 | 部分 | 现行清单 `T/acceptance_assets/mutations.json` AS-* 22 条 + FIN-01/SRC-05；`E/2026-10-05/mutations/results-185642.json` 26/26 KILLED | M01、M02、M03、M05、M07、M10、M12、M14 八条无现行改坏条目（只有 09-23 旧 AM 近似、A′ 后未重跑）；详见 1.7 |
| 238 | L390 | 状态化 200×50 | 做法不同 | `test_stateful_assurance.py:124` 6 种子 × 每局 ≤8 轮 | "不用 Hypothesis"有子代理裁决（HTN补齐计划阶段 F 开工裁决），**规模缩到约 1/200 无记录** → C 级 |
| 239 | L390 | 独立代码审查 | 部分 | `E/2026-09-25/assurance-plan-challenge/README.md`；10-05 只有一次针对 E06 的偏差裁决 | 09-25 之后大量改动无整体独立审查 |
| 240 | L392 | 缺依赖不装运行时依赖 | 做法不同 | 往 venv 装了 jsonschema（接缝脚本用），未改 pyproject | 实施自述 WIP:1207 |
| 241 | L396 | 48 组保留编号；C08 只做离线迟到记账 | 部分 | 见 1.7：48 组只 14 组完整覆盖 | `SP/sdk-cases.json` 49 个实际用例名 16 个已不存在 |
| 242 | L396 | 继承 66 逐条可追踪 | 部分 | 见 1.7：完整覆盖 20 条 | — |
| 243 | L396 | 继承 X 组 18 条 | 未做 | 18 条仍 `NOT_REMAPPED`；X06、X11 连疑似承接都没有 | ASSUR:7"仍没做"（实施自述），无用户决定不做 |
| 244 | L396 | OCC 12 逐条 | 部分 | 见 1.7：只 OCC-02 完整覆盖 | — |
| 245 | L396-398 | 4×3 单列 runner；传输不 stub；connector 为真实本地服务 | 部分 | 10-06 另一会话正跑 `backend/scripts/assurance_model_scenarios/run.py`（**未入库**，git `??`）；证据 `E/2026-10-06/assurance-model/` | M04 用文件发布 + `hooks/sitecustomize.py` 丢首个回执，不是受控 connector 服务；另加第 5 题 billing-tool（脚本注释称用户 10-06 加）。D 级（用户 10-05 定排最后） |
| 246 | L400 | 每局 300k/16/32/900s；INVALID_ENV 不减分母；不补抽 | 做法不同 | `run.py:64,75` 场景 1、2 一致；`:88,98` 场景 3、4 放宽到 24/48/450k/1200s；`:352-357`"通过"不看预算；accurate-report 第 1 局失败后同局号重跑、旧记录被覆盖 | 与计划"超预算即 FAIL、新 attempt 留历史"相反；出处只有未入库脚本注释 → C 级 |
| 247 | L402 | 通过门 12/12、M03 total=232、M04 效果计数=1、每局值+中位数 | 未做（进行中） | `summary.jsonl`（11:30 读取）：M01 3 局通过（其中 2 局超预算，1 局是重跑）；**M02 0/3**（`official_rejection_exists`、`rejection_names_290` 等未过）；M03 第 1 局不过、第 2、3 局"通过"但超预算（第 3 局 114 万 token）；M04 第 1 局不过（`effect_acceptance_recorded`），第 2 局在跑 | 按计划口径目前不可能 12/12；D 级 |
| 248 | L408 | 同交付更新四份架构文档 | 按计划在用 | 四份首行均为 10-05 opt.162；`ARCHITECTURE/index.md:1` 已写明 `assurance_profile` 已删 | — |
| 249 | L408 | 记真实入口/版本/日期/未完成/相对证据索引；WIP 与验收分开 | 部分 | ASSUR:1-7 有 | `WIP` 停在 09-24 仍写 IN_PROGRESS；无统一相对证据索引 |
| 250 | L410 | 分开记 SPEC_RESOLVED/BODY_WIRED/SDK_VERIFIED/… | 做法不同 | 架构文档不用这些标签 | 无记录 |
| 251 | L412 | 独立审阅须真独立，没工具标 NOT_RUN | 按计划在用 | 09-25 挑战由独立子代理；`SP/baseline.md` NOT_RUN | — |
| 252 | L410 | 默认 ON 后最终回归 | 按计划在用 | `host-orchestration-final.txt` 26 失败与开启前相同；10-01 全绿 | — |
| 253 | L416 | 证据目录被忽略、新目录不覆盖 | 部分 | `git check-ignore` 命中 | 10-06 真实模型目录同局号被覆盖（#246） |
| 254 | L429-433 | verify_delivery/check_plan/capture_identity 生成 source-map | 已删 | 替代脚本与清单 3a8c4697 删 | 阶段 B 收尾裁决（D/HTN补齐-实施记录.md:247，子代理裁决）→ B 级 |
| 255 | L436-442 | 主体后跑 assurance_exec 留 junit | 按计划在用 | `E/2026-09-23/assurance-1.1-verify/sdk-*.junit.xml` | — |
| 256 | L444 | gate-commands.local.json | 未做 | 全库无此文件 | 无记录 |
| 257 | L444 | 真实模型 runner `tools/run_assurance_model_scenarios.py` | 部分 | 同 #245（名字/位置不同、未入库、进行中） | — |
| 258 | L450 | integration-map 单 owner 映射随代码维护 | 未做 | 只在计划包 | 无记录 |
| 259 | L451-453 | ref-resolution/field-producers/sql-column-producers 维护 | 未做 | 没回写；缺 rechecks 两列 | 无记录 |
| 260 | L454 | contracts/*.schema.json 是活动结构 | 做法不同 | host 7 份 6 份字节同、`host-error-v1` 一处 `RESTORE_QUARANTINED→ROOT_QUARANTINED`；内部 8 份字节同（只作身份哈希）；blob-pin/closeout/criterion-policy/disclosure-batch/pending-work/policy/review-reply-v2/success-formula 只在计划包；运行时 Host 回复不按 schema 校验 | 无记录（旧对照文档列为发现） |
| 261 | L455 | additive.sql + queries.sql | 按计划在用 | 见 1.8 | — |
| 262 | L456 | reference protocol_v11/semantics 只作参考 | 按计划在用 | 留在计划包 | — |
| 263 | L458 | model-scenarios.json 接真实运行时 | 部分 | 同 #245 | — |
| 264 | L459 | 继承/OCC 覆盖逐条可追踪 | 部分 | 见 #241-244 | — |
| 265 | L457 | F01～F15 决定性反例落 SDK | 部分 | `T/full_target/assurance_exec/test_review_findings.py` 7 条（:49-440）+ 映射表 `T/acceptance_assets/assurance_findings.json` | 计划 15 个独立用例改为"新写 7 + 映射已有"；逐条见 1.5 |

### 1.4 附录 C 与测试清单（清单 266～314）

| # | 原文行 | 计划条目 | 判定 | 代码证据 | 差距说明 / 记录 |
|---|---|---|---|---|---|
| 266 | TEST-MAP L7 | 16 原 + 12 F 定点变异按指定用例执行 | 部分 | 同 #237 | 详见 1.7 |
| 267 | TEST-MAP L8 | 状态化 200×50 | 做法不同 | 同 #238 | C 级 |
| 268 | C.1 | bind_assurance_profile_locked | 按计划在用 | `assurance_store.py:76` ← `assurance_factory.py:121` ← CS:803 | 名字不同 |
| 269 | C.1 | approve_assurance_check_policy 由要求批准调用 | 做法不同 | CS:632 ← `duties.py:221,319`、RT:372,459 | 同 #78 → B′ |
| 270 | C.1 | ensure_assurance_review | 按计划在用 | CS:637 ← CR:307、PR:369 | — |
| 271 | C.1 | ensure_format_repair_invocation 只用于格式错 | 做法不同 | TR:797 ← CON:460（复审）、:505（解读错误）、:541（格式错/调用失败） | 复审 A#20；解读错误实施自述（ASSUR 09-26 第 4 条）；调用失败子代理裁决（#117） |
| 272 | C.1 | record_local_check_run | 按计划在用 | `assurance_local.py:135` ← `verifier_router.py:163` | — |
| 273 | C.1 | import_assurance_check_locked | 按计划在用 | CS:647 ← LC:588 | — |
| 274 | C.1 | import_reviewer_disclosure_locked | 按计划在用 | RET:419 ← COL:301 | — |
| 275 | C.1 | import_official_assurance_review | 按计划在用 | IMP:751/:542；`htn_store.py:961-966` | — |
| 276 | C.1 | read_complete_evidence_snapshot | 按计划在用 | RD:294 ← VAL:516、PR:267、CR:180 | — |
| 277 | C.1 | compute_assurance_use（事务外） | 按计划在用 | VAL `_prepare_use` | — |
| 278 | C.1 | commit_assurance_use_locked 守 accept/disclose/handoff | 部分 | VAL:768，调用者只有 RC:1746/1795/1849 | 交接改用 HTN 有效性见证（D/HTN补齐计划-2026-10-02.md 表二 9，计划内记载 → B 级）；**披露（原生下载/摘要）无记录** |
| 279 | C.1 | accept_assured_review_locked | 按计划在用 | RC:1708 | — |
| 280 | C.1 | try_finalize_assured_mission | 按计划在用 | ACON:424、:620 | 等价 |
| 281 | C.1 | _finalize_assured_mission_locked | 部分 | 同 #132 | — |
| 282 | C.1 | ingest_assurance_events_locked | 按计划在用 | WORK:157 ← TICK:185 | — |
| 283 | C.1 | commit_assurance_work_locked | 按计划在用 | WORK:300 ← TICK:282 | — |
| 284 | C.1 | reauthorize_restored_read | A 级排除（#4） | 无 | — |
| 285 | C.1 | *_locked 用原 Store 事务；read/prepare 不写业务表 | 按计划在用 | ACON:277,721,778 `ASSURANCE_PREPARATION_INSIDE_TRANSACTION` | — |
| 286 | C.1 末 | 四种读者都过共同 read_use | 部分 | 验收/目标结论过证书；交接用 HTN 见证（`action_commits.py:1111`）；输入/上下文按知识是否当前过滤；原生下载只核归属（`facade.py:856-866`） | 交接/输入有计划内记载（B 级）；**原生下载/summary 无记录** → C 级 |
| 287 | C.1 末 | use_check 只诊断不返回许可 | 部分 | `api/assurance.py:798,803`（diagnostic_only、certificate_ref=None） | 诊断把候选写进与真实验收共用的内存缓存再清掉（`api/assurance.py:763-792`、VAL:188-201），可能清掉一份已准备未提交的真实候选；无记录（见新-13） |
| 288 | C.2 | 建任务事务依次写任务/要求→判别→激活→绑定/epoch/cursor | 按计划在用 | `assurance_factory.py:55-127`；`assurance_store.py:76-199` | — |
| 289 | C.2 | 检查策略随后写，读者不要求未建的审阅/预留 | 做法不同 | `duties.py:160` | 同 #78 → B′ |
| 290 | C.2 | 新空环境 global epoch/时钟行由安装回执产生 | 按计划在用 | `assurance_root_commits.py:69-90` | — |
| 291 | C.2 | 回执不递归含自身 hash；deferred FK | 按计划在用 | `assurance_schema.sql:7,19` | — |
| 292 | C.2 | 历史非空根首次安装经认证入口登记 | 按计划在用 | `assembly.py:65-75` → `assurance_root_commits.py:113-175` | 每次启动自动安装（#187） |
| 293 | C.3 | 预留事件不含未来 invocation hash | 按计划在用 | TR:402-419 | — |
| 294 | C.3 | 披露事件含增量 hash 不含本批 hash | 按计划在用 | RET:452-472 | — |
| 295 | C.3 | TurnImported 不含未来绑定 | 按计划在用 | COL:114-125 | — |
| 296 | C.3 | pin 回执不序列化含自身 hash 的对象 | 按计划在用 | `SDK/storage/assurance_pins.py:20` | — |
| 297 | C.3 | 用量导入沿用原计价与身份，不改写过去 | 按计划在用 | 用量身份不改写 | 计价部分 A#1（金额计价删） |
| 298 | C.4 | pin acquire/bind/release，唯一 writer AssuranceStore | 按计划在用 | `assurance_review_pins.py:17,154,174,201`；`assurance_store.py:558,625` | 文件名不同 |
| 299 | C.4 | 先 PREPARING 再读 CAS；失败 release + SOURCE_UNAVAILABLE | 按计划在用 | CR:257,318-320；PR:325,379 | — |
| 300 | C.4 | GC 删除门查 pins，缺覆盖不删 | 按计划在用 | 应用层无 GC、无删除（`SDK/artifacts/store.py`） | 计划明文允许"没有 GC 就不新建" |
| 301 | C.4 | 没有 GC 不新建定时 GC | 按计划在用 | 同上 | — |
| 302 | C.4 | SQL 只保证 pin 身份/状态 | 按计划在用 | `assurance_schema.sql:368-373,411-413` | — |
| 303 | C.5 | 按事件类别表登记，不按前缀猜 | 按计划在用 | ACON:40-76；CON classify | — |
| 304 | C.5 | 一条事件喂多个 consumer，cursor 独立 | 按计划在用 | TICK:169-197 | — |
| 305 | C.5 | MANUAL_REQUIRED=WAITING+原因，due 排除，可被更高 seq 重开 | 按计划在用 | WORK:249,141-155 | — |
| 306 | C.5 | RUNNING lease 过期只回收协调，原 intent 复用 | 按计划在用 | WORK:213-235 | — |
| 307 | C.5 | 新源事件升级 target，旧 claim 失效 | 按计划在用 | `_enqueue` + `_assert_claim` | — |
| 308 | C.5 | 通知前先查同一逻辑通知回执 | 按计划在用 | ACON:787-805 | — |
| 309 | C.6 | history_state 取固定 seq；current_use 按当前 | 部分 | `api/assurance.py:341-363` | 历史切面准则固定取激活时那一版要求（`:514-519`），改要求后显示旧准则；无记录 |
| 310 | C.6 | review 响应字段齐、正文走获准 artifact | 按计划在用 | `api/assurance.py:648-738` | — |
| 311 | C.6 | use_check diagnostic_only/certificate_ref=null | 按计划在用 | `api/assurance.py:798,803` | — |
| 312 | C.6 | 无完整历史 → HISTORY_UNAVAILABLE | 做法不同 | `api/assurance.py:487,754` 用 SOURCE_UNAVAILABLE 码、消息带 HISTORY_UNAVAILABLE | 计划自带 host-error 枚举本无此码（计划自相矛盾）；无记录 |
| 313 | C.6 | current 翻页 epoch 变 → SNAPSHOT_CHANGED | 按计划在用 | `api/assurance.py:585-597` | — |
| 314 | C.6 | 分页不当作证据集合完整声明 | 按计划在用 | truncated / next_cursor | — |

### 1.4′ 清单之外补出的正文要求（新增）

| # | 出处 | 计划条目 | 判定 | 代码证据 | 差距说明 / 记录 |
|---|---|---|---|---|---|
| 新-2 | L193/L197 | 读不出创建合同不得当老任务 | 做法不同 | FW:42-47 `is_assured` 读错一律 False；EH:1544 按"老任务"停；FW:111 **终态通知静默不发**；FW:139 终写仍拒 | 不通向完成（旧对照第五节，实施自述）；"通知静默不发"无记录 |
| 新-3 | L326 | pin 唯一键 | 做法不同 | `SDK/storage/assurance_pin_object_schema.py`：改为 (mission,review_key,object_ref_json) 且仅非 RELEASED 唯一；计划表级 `UNIQUE(mission_id,review_key,blob_hash)` 不存在 | 模块注释说明（实施自述） |
| 新-4 | L326 | 不可替换触发器 | 做法不同 | 6 个 no_replace 触发器多了自然键条件（更严） | 落实 `SP/acceptance.md` R01 反例 |
| 新-5 | §8 | 计划外 200 个触发器（188 个来源屏障 + 12 个守卫） | 做法不同 | 本次建库核对 | 是 §8 屏障的落地方式；additive.sql 无，WIP:44 有自述 |
| 新-6 | L336 | 256KiB 上限 | 做法不同 | `codec.py:16-21` `MAX_RECORD_BYTES`=8MiB 用于清单/快照读取 | 实施自述（ASSUR 09-26 条第 1 项） |
| 新-7 | L336 | 审阅回复严格解码 | A 级排除（#6） | CK:397-407 剥围栏、忽略空值多余字段 | 10-02 用户定 |
| 新-8 | L336/L454 | review-reply-v2 `additionalProperties:false` | 做法不同 | CK:309-360 回复增加 claims/methods/summary（格式版本 3） | 随 HTN 阶段 C/C3（用户 10-02 定做法复用/知识库）的实施 → B 级 |
| 新-9 | L314 | 循环入口具名错误码 | 做法不同 | EH:1544-1556 停机原因写 `unsupported_unbound_mission`，不是 `CREATION_CONTRACT_UNRESOLVED` | 仍停、不当老任务；旧对照第五节自述 |
| 新-10 | L295 | 隔离状态精确只读分支 | 只在测试 | `facade.py:866-889` `require_read`：读权威只在装配成功后才设，而装配成功时不在隔离态 | ASSUR:6"保留"（实施自述） |
| 新-11 | L289 | 换库后应进隔离只开管理读 | 做法不同 | 库里没有安装回执但状态文件还在时，`assurance_root_commits.py:148-149` 抛 `ROOT_INSTALL_COMMAND_REQUIRED`；该回调在 EH:656-657 的 `try` 之外调用，整个编排启动失败 | fail-closed，但不是计划的"隔离 + 管理读"；**无记录** |
| 新-12 | queries Q09 | 反向依赖索引 | 做了没接上 | `assurance_store.py:544` 只写，全 SDK 无读取 | 无记录（计划说是优化） |
| 新-13 | C.1 L496 | use_check 不影响真实执行 | 部分 | 见 #287 | 无记录 |
| 新-14 | L201 | 收尾前原有运行责任真实收敛 | 部分 | ACON:478-490 只查根范围必需效果的 RECONCILIATION_REQUIRED、未关意图、RESERVED 预留、未知用量 | 不查首个审阅员的尾部预留（`budget_tail_holds`）与范围外已交出且结果不明的动作；可达性未核实；无记录 |
| 新-15 | ref-map 公共 | 失败码 `SOURCE_NOT_CURRENT` | 未做 | 全 SDK grep 为零 | 无记录 |
| 新-16 | — | 残留不可达分支 | 做法不同 | CS:3310-3311 `_require_root_resolution` 的 `if network is None: return`；ACON:409-410 `if network is None: raise`（`_judgment_network` 现在不会返回 None）；`root_review.py:1072` `request()`、CS:3281-3290 包装无调用者 | 旧对照第五节称"network is None 旧分支一并删"，只删了 judge_mission 里那一处；违反用户"旧路径直接删"口径（A#17 的精神），无记录 |

### 1.5 F01～F15（RESPONSE-TO-REVIEW 承诺 · 代码 · 反例）

反例的目标文件是 `T/full_target/assurance_exec/test_review_findings.py`（10-05 新建，7 条用例）；其余承接登记在 `T/acceptance_assets/assurance_findings.json`，守护用例 `T/acceptance_assets/test_acceptance_assets.py:116-128` 只核"点名的用例/改坏在不在、PARTIAL 要写 gap"，不核断言是否对题。改坏执行结果 `E/2026-10-05/mutations/results-185642.json`（26 条全部 KILLED，本次未重跑）。

| F | 计划承诺（关闭判据 + 反例断言） | 代码是否做到 | 反例用例是否存在、是否真覆盖 | 判定 |
|---|---|---|---|---|
| F01 | 复用 scoped/root/composition/OCC，单 owner；cut 与 ensure 不双发；每 purpose 一次通知只建一个逻辑 review/原预算；所有终态 writer 有 owner | 基本做到：六入口单路径（1.1 #3/#26/#110）；写 COMPLETED 只有 FW:178 | `test_no_parallel_scoped_review`（`test_review_findings.py:390`）跑一个带发布的任务，断言每类审阅一次、每包至多一条正式记录、每次调用一笔预留、空转不增。**缺**：COMPOSITION 一类不在断言的 purposes 里（只覆盖 5 类）；没有"同一通知重送"注入；终态写方枚举只到 COMPLETED 与 EH 内 4 个调用（#131） | 部分 |
| F02 | 内部 Ref kind + 事件桥；错 kind/body/collector 拒；已结算 reserve 不能用于新 dispatch，旧费用照入 | 部分：4 类 kind 无解析器（#51/#52），commit_receipt 不核写者（#35），`SOURCE_NOT_CURRENT` 未实现（新-15） | `test_internal_ref_resolvers`（:257）覆盖 body 哈希错、修订错、跨任务、跨租户、非系统事件、跨任务回执、未知 kind。**缺**：错 collector；"拿已结算预留开新调用被拒"没有用例（映射的 `test_review_turn_retry_e2e.py` 只证明每次调用各有预留，不尝试复用）；"各 purpose 可编码"未测 | 部分 |
| F03 | review_key+exact ref 稳定标签；曝光批次绑定实际输入；同 ID 双版本/跨 kind 区分、未曝光拒、追加冲突/冷恢复稳定 | 做到（1.1 #61-74） | `test_labels_exact_disclosure`（:49）覆盖双版本、跨 kind、未曝光、目录外、伪造目录、跨审阅、重复标签；改坏 AS-FM01 被抓。**缺**：追加批次冲突（同号异体）没有反例；"冷恢复"只是纯函数重算 | 部分 |
| F04 | 批准的 SEMANTIC/CHECKED 政策；三值 OR-of-AND；SEMANTIC 不造 check；完整真值表 | 做到，缺全局安全 finding 一类（#95） | `test_checked_policy_truth_table`（:79）补替代组；其余格在 `test_a_assurance.py`（A02/A04/A05）；改坏 AS-FM02/FM03 被抓 | 部分（因 #95 未做） |
| F05 | 一 round 一包多 invocation；同 round 通知去重；ordinal2 新真实 reserve；不同 round 不撞 subject；**UNKNOWN 不新问** | "UNKNOWN 不新问"被改为"调用没回来→第 2 次调用"（#117，子代理裁决） | 映射 `test_review_turn_retry_e2e.py`、`test_a_assurance.py::test_check_budget_and_format_bounds`、`product_world/test_review_call_unanswered.py`；改坏 AS-FM04 被抓。"不同 round 不撞 subject"只有纯函数 `test_review_new_round_identity` | 做法不同（B 级 B-2） |
| F06 | judge 成功段提取唯一 final writer；root 满足与 closeout 分开；直调 judge 不提前终态或释放；root 后 UNKNOWN/hold/退出、ack 丢失正确 | 做到：judge 只请求收尾（CS:3133-3149），opt.162 删直写尾巴；收尾加 `SOURCE_CHANGE_OPEN` | `test_direct_judge_waits_closeout`（:319）覆盖执行中直调被拒、判定后收尾被挡再调仍 ACTIVE 且预留不动、放行后只完成一次；改坏 FIN-01、SRC-05 被抓。**缺**：全测试库没有一处断言 BLOCKED_UNKNOWN（"root 后 UNKNOWN"）；"root 保存后进程退出"只在 p35 一条反向场景（死调用不挡收尾）；ack 丢失靠 C05 接缝间接 | 部分 |
| F07 | 恢复隔离 + 当前重新授权 | 已删 | 映射只剩根闸门用例 | A 级排除（#4） |
| F08 | 源 writer 完整列表、聚合/global epoch、精确读集、tick 唤醒；每类 writer/新增反证同事务挡旧证书；timer expiry/clock rollback；无缓存 oracle 一致 | 做到（1.2 #143-170） | `test_all_writers_barrier_and_expiry`（:207）：只用"登记资料"一类真实写方验证同事务推纪元与旧纪元被拒，其余表只断言触发器存在；观察类在 `product_world/test_desktop_preconditions.py`；到期/回拨在 `test_v_assurance.py`/`test_c_assurance.py`。**缺**："每类 writer"的行为级反例、"无缓存 oracle 一致" | 部分 |
| F09 | cursor=已持久入 pending；prepare 事务外；预算等待不阻后续反证；ACK 丢失/self event/并发不重复不丢 | 代码结构做到（#175-#183），但预算等待与需重算共用上限（#178） | `test_c_assurance.py::test_event_cursor_atomicity`（C04）、`test_two_connection_concurrency`；改坏 AS-FM08 被抓。**"预算等待不阻后续反证"无用例**（映射表自认 PARTIAL） | 部分 |
| F10 | 独立持久 creation discriminator；完整验收后默认 ON；缺 binding 不能 legacy；不静默迁移 | 做到（1.3 #196-#205）；默认 ON 提前于验收（用户 09-23） | `test_c_assurance.py::test_real_migration_and_legacy`、`product_world/test_unbound_legacy_mission.py`；改坏 AS-F10 被抓 | 按计划在用 |
| F11 | DDL initial/delete/replace/同 Mission review 约束；三原反例 + REPLACE/跨 Mission 变体被 SQLite 拒 | 做到（#208/#209） | `test_sql_bypass_initial_and_delete`（:125）覆盖出生即 FINALIZED、DELETE、INSERT OR REPLACE、非法 UPDATE、无审阅/他任务审阅的 BOUND pin；改坏 AS-FM09/10A/10B/11 被抓 | 按计划在用 |
| F12 | 共享 schema 唯一源；pointer+resolved hash；校验器查内容；nested 变更必被检测；限制先到明确失败 | 后半做到（具名 limit）；前半未做（#213、#29） | 只有边界用例（strict_boundary、bounded、complete_collection）；漂移检查不存在；AS-FM12 自判"不适用" | 部分 |
| F13 | underscore verbs + 隔离 wheel/venv/userdata/端口原生验证；精确 wheel/import + 指纹；实际点击重连/历史/隔离/closeout | 接口做到；隔离装载做法不同（#226）；指纹部分（#227） | `test_c07_host_api.py` 两条；原生点击只主流程 | 部分（点击部分 D 级） |
| F14 | BODY_WIRED 按实际边；DoD 回写 ARCHITECTURE/PROJECT_STATUS 与相对证据索引 | 文档已回写（#248）；BW 登记未做（#233）；证据索引无（#249） | 无用例（映射表 RECORD_ONLY，gap 指向旧对照文档附四） | 部分 |
| F15 | 66+OCC12 逐条 owner/断言；C08 离线；4×3 固定 oracle/budget；独立代码审查另列 | 未达成：X 组 18 条无映射（#243），48/12/66 多数只部分覆盖（1.7），真实模型进行中且口径偏离（#245-#247），独立审查只有 09-25 一次 | 无用例（RECORD_ONLY） | 未做（真实模型部分 D 级） |

F 小结：按计划在用 2（F10、F11）；部分 10（F01、F02、F03、F04、F06、F08、F09、F12、F13、F14）；做法不同 1（F05）；未做 1（F15）；A 级排除 1（F07）。`assurance_findings.json` 把 F01、F02、F03、F04、F06、F08 标为 COVERED，与上表不一致（第六节）。

### 1.6 implementation 接线类资产（逐行；"在用"行合并列出 id）

#### integration-map.json（55 行 + host 6 项；seams.json 26 行与其中 seams 逐字节相同，合并核）

| 资产行 | 要求 | 判定 | 证据 | 差距 / 记录 |
|---|---|---|---|---|
| S01 S02 S03 S04 S05 S09 S10 S11 S14 S19 S23 S25 | 各接缝在原位置改造/复用 | 按计划在用 | Host/service.py:348；`facade.py:131,195-201`；`scoped_content_review.py:168`；`scoped_composition_review.py:34`；`assurance_blobs.py:140-171`；`root_review.py:840`；EH:9832-9850；RET:489；`htn_store.py:1279`；RC:589；`accounting_recovery.py:159`；`api/assurance.py:156` | — |
| S21 | judge_mission 管根与终态 | 按计划在用 | CS:3046-3166 | 残留不可达分支（新-16） |
| S06 S07 S08 S16 S17 S18 S24 | 新建文件/函数名 | 做法不同 | 实际落点：`assurance_review_pins.py`、CS:632/CP:40、LC + `verification/assurance_local.py`、RD:294、VAL:151、SQL 触发器、TICK:73 + `assurance_work.py` | 实施自述（WIP:1044/213/698/40/428/44/65） |
| S12 | `_collect_root_review` 做正式收集 | A 级排除（#3） | 统一 `collect_assurance_review`（COL:28） | 旧根审阅员删 |
| S13 | `Store.put_source` 加屏障 | 做法不同 | SQL 触发器 `assurance_barrier_v26.sql:15,36,57` | 实施自述 WIP:44 |
| S15 | `insert_justification_set` 屏障 | 已删 | 迁移 39 | 子代理裁决（B-3） |
| S20 S22 | 计划写的文件位置 | 做法不同 | `operation_completion.py:302`；`runtime/planning_operations.py:567` | 计划写错位置；无记录 |
| S26 | `offline_backup.restore_offline` | A 级排除（#4） | 不存在 | — |
| reviews: TASK_CONTENT METHOD_PLAN ACTION_PROPOSAL OPERATION_OUTCOME | 复用原构造器、统一派发、原收集 | 按计划在用 | 见 1.1 #17-#24 | OPERATION_OUTCOME 允许 `tool_receipt` 但无解析器 |
| reviews: COMPOSITION | 记上级复合任务账 | 做法不同 | TR:177-199 记任务总账 | B 级（#106） |
| reviews: MISSION_FINAL | 原 record_review/collect | A 级排除（#3） | 正式记录由 REVIEW 消费者写 | — |
| TW1 TW2 TW3 TW4 TW8 | 各终态写方 | 按计划在用 | CS:2773；RC:673；RC:960；CS:3133-3149；CS:3267 | — |
| TW5 | 新私有终写 | 做法不同 | 独立模块 FW:125；CS:3281 包装无调用者 | #132 |
| TW6 | record_review 要求来源见证 | A 级排除（#3） | 已删 | — |
| TW7 | update_mission/task 调用点清单 | 部分 | grep 无旁路；枚举测试不全 | #131 |
| IW1 IW2 IW4 IW5 IW6 IW7 IW8 | 各源写方屏障 | 按计划在用 | 对应触发器；`assurance_clock.py:19` | IW5 ACL 用固定主体（B′） |
| IW3 | 理由集/支持成员屏障 | 已删 | 迁移 39 | B-3 |
| EC REVIEW VALIDITY CLOSEOUT NOTIFY | 同事务入队+游标；事务外准备；效果+回执 | 按计划在用 | CON:70-129；ACON:156,178,346,378,744,770 | — |
| restore.entry / chosen_policy / external_revocation_log | 受管恢复 | A 级排除（#4） | — | 残留诊断/只读分支见 #195、新-10 |
| host.handlers service sdk_load ui verbs | Host 接线 | 按计划在用 | `Host/handlers.py:42-44,257-259`；`Host/service.py:348,1412`；`sdk_candidate.py:81,118-170`；`MissionsView.tsx:1244` | `host_fingerprint` wheel 哈希常量 |
| host.projection | 走 Host projection | 做法不同 | 原样转发 | 无记录（#224） |

条数：要求 61，按计划在用 38，做法不同 13，部分 1，已删 2，A 级排除 7。

#### event-consumer-map.json（7 行 + 2 条规则）

| 行 | 判定 | 证据 | 差距 / 记录 |
|---|---|---|---|
| ACTUAL_REVIEW_TURN_AVAILABLE→REVIEW；SOURCE_OR_AUTHORITY_CHANGED→VALIDITY,CLOSEOUT；NotificationRequested→NOTIFY；DIAGNOSTIC_ONLY；activation 规则；missing_source 规则 | 按计划在用 | COL:114-125、CON:113-129；ACON:43-75；FW:36,104-122；ACON:77；`assurance_store.py:182-199`；WORK:329-339 | NOTIFY 在 `is_assured` 读错时静默不发（新-2） |
| CANDIDATE_READY→REVIEW | 做法不同（B 级，计划已按裁决改写，#39） | 原入口直接 `ensure_*`，靠 review_key 幂等（附录 C.1 原文即如此） | `推后第1批-P1a-偏差裁决.md` |
| ACTUAL_CHECK_AVAILABLE→REVIEW,VALIDITY | 按计划在用（2026-10-07 A25） | 三个消费者都经屏障写的资料变更事件收检查；内容审阅缺必检检查时导入先等（CHECK_PENDING）；原登记的两个检查事件名无写方，已删 | `推后第1批-车道P1a-记录.md` |
| BUSINESS_OR_RUNTIME_SETTLED→REVIEW,CLOSEOUT | 按计划在用（计划已按裁决改写，#40；REVIEW 侧唤醒预算等待由 A25 补上） | CLOSEOUT 在用；组合审阅与终审仍由主循环每轮扫描触发 | `推后第1批-P1a-偏差裁决.md` |

条数：要求 9，按计划在用 6，做法不同 1，部分 2。

#### BODY-WIRED.md（BW01～BW16）

| 边 | 判定 | 证据（从产品入口起） | 差距 / 记录 |
|---|---|---|---|
| BW01 BW03 BW04 BW05 BW06 BW07 BW08 BW09 BW11 BW12 BW13 BW15 BW16 | 按计划在用 | CS:804→`assurance_factory.py:152-169`；CS:637→TR:202；TR:380,393-417,797；EH:8065-8085→LC:588→`assurance_check_import.py:179`；EH:5425、COL:83-143；EH:5520→COL:114→CON:296→IMP:622→`leaf_acceptance.py:519`；VAL:446-447,516,768；触发器；EH:10550→CS:3149→ACON→FW:125；TICK:156,200；TICK:84-105、`assurance_assembly.py:222-274`；Host 三 verb→`api/assurance.py`→`MissionsView.tsx`；`assurance_factory.py:165-169` + ASSUR/index/STATUS 10-05 | BW08 的恢复闸、BW15 的隔离 wheel 不在（A#4、#226） |
| BW02 | 做法不同 | `duties.py:221,319`→`facade.py:131`→CS:632 | #78（B′） |
| BW10 | 按计划在用（2026-10-07 A26） | 接受/根/组合/披露核证书；PLAN（开规划请求、回复准入复核）、CONTEXT（装上下文与运行中 `knowledge_read`）、RECOVERY（重启恢复在途尝试）、START（对外操作交接）在 `assurance_point_use.py` 一个入口签发；执行尝试开工按 TaskGraph 见证合同（B 级 #41）；MAINTAIN 挡（A，#9） | `推后第1批-P1b-偏差裁决.md` |
| BW14 | A 级排除（#4） | — | — |
| （门本身）逐边登记 candidate HEAD+hash 并关闭 | 未做 | 无登记 | #233 |

条数：要求 17（16 边 + 登记程序），按计划在用 13，做法不同 1，部分 1，A 级排除 1，未做 1。

#### ref-resolution-map.json

**kinds（29）**

| kind | 判定 | 证据 | 差距 / 记录 |
|---|---|---|---|
| requirements task method method_instance artifact source review acceptance operation input_manifest completion_scope result reservation_fact agent_turn_receipt check_binding check_spec check_policy local_check_receipt execution_receipt disclosure_receipt review_package | 按计划在用 | RD:111-128 `_EXACT`；`event_kinds.py:4-11`；`assurance_blobs.py:34-89`；生产构造点各有（如 VAL:468,471、PR:507、TR:117,417、COL:120、LC:326,497、RET:472、`assurance_store.py:383,444`） | completion_scope 直接读表（#50）；check_spec 懒注册（#41） |
| commit_receipt | 部分 | RD:128,191 | 不核写者（#35） |
| observation resolution completion_spec | 做了没接上 | 解析器在（RD:114,118,119），生产 0 处构造 | 无记录 |
| tool_receipt policy authority capability | 未做 | 无解析器无构造；`reviews.py:246` 仍允许 tool_receipt 作审阅对象 | 无记录（findings.json F02 gap 登记） |
| 公共失败码 `SOURCE_NOT_CURRENT` | 未做 | grep 为零 | 新-15 |

条数：要求 29（+1 公共项），按计划在用 21，部分 1，做了没接上 3，未做 4（+1）。

**field_contracts（9）**：按计划在用 7（review-invocation.reservation_fact_ref、review-record-binding.reviewer_turn_ref/consumed_check_refs/disclosure_refs、check-binding.execution_ref、review-binding.any_check_sets/criterion_policy_ref，证据 `reviews.py:84-95,210,331`、`check_bindings.py:50`、CK:162）；做法不同 2（blob-pin.source_receipt_ref/last_receipt_ref 用 SQL 外键列 `assurance_schema.sql:227-228`，无记录）。

**all_reference_fields（53）**：allowed_kinds 限制实现在 `refs.py:84-90`（`REF_KIND_FORBIDDEN`）、dataclass 构造期检查（`check_bindings.py:48-50`、`disclosure.py:54-56`）、用途矩阵（`reviews.py:241-260`、VAL:83-89）。按计划在用 42；做法不同 8（blob-pin 两回执 #51/#53/#50/#52 为 SQL 列；closeout-v1 的 `unsettled_operation_refs`/`accounting_pending_refs`/`dangerous_work_refs`/`report_ref` 四字段不存在，收尾体改用 id 列表；host 响应 #6、#45 只靠构造不过 schema；均无记录）；A 级排除 3（restore-authorization 三字段，#4）。

**pin_fields（17）**：按计划在用 10（review-binding.package_ref/owner_task_ref/completion_scope_ref/method_instance_ref/requirements_ref、check-binding.adapter_ref、use-certificate.policy_ref、criterion-policy.requirements_ref、review-record-binding.package_ref/record_ref）；做法不同 6（reviewer_policy_ref、context_policy_ref 用代码常量/现算无注册表读者；check-spec 的 input/result_schema_ref、scope_rule_ref 用随包文件哈希；closeout.root_resolution_ref 用 id 外键无哈希 Pin；均无记录）；未做 1（closeout.requirements_ref：收尾不钉要求版本，无记录）。

#### schema-source-rules.json

- `default_field_owner: UNMAPPED_MUST_FAIL`：按计划在用（`codec.py:54-59 fields()`）。
- field_specific（实为 19 条）：按计划在用 15；做法不同 4——criterion_policy_ref（#78，B′）、root_incarnation_id（启动自动写回，#187，B′）、round_no 恒 1（#119，无记录）、ordinal 也用于复审（A#20）。
- schema_owners（24 条，三个 request 各计一条）：按计划在用 17；做法不同 6——blob-pin 落点、check-spec 懒注册、closeout 写方结构、criterion-policy 投影、host-response/host-review-response 不经 Host 投影（后两条无记录）；A 级排除 1（restore-authorization）。

#### field-producers.json（368 条，24 个 schema 文件）

| 组 | 条数 | 判定 | 证据 | 差距 |
|---|---|---|---|---|
| SDK 随带且与计划字节相同的 8 份内部 schema（common、check-binding-v2、check-spec-v1、local-check-receipt-v1、review-binding-v2、review-invocation-v1、review-record-binding-v2、use-certificate-v2） | 164 | 做法不同 | `SDK/assurance/schemas/`、`contracts/common.schema.json` 与 `P/contracts/` `cmp` 相同；但只用作 CheckSpec 身份哈希（`check_specs.py:16-30`），运行时字段由 Python dataclass 校验 | 没有任何工具核对 Python 字段与 schema 一致（F12 前半未做） |
| host-*-v1 七份 | 85 | 部分 | 6 份字节同；`host-error-v1` 一处 `RESTORE_QUARANTINED→ROOT_QUARANTINED`（随 A#4 改名）；校验器 `assurance/contracts/__init__.py:141` 只在测试与前端解析用 | 生产不按 schema 校验（#260） |
| policy-v1、success-formula-v1 | 19 | 按计划在用 | `SDK/assurance/policy.py:12-25` 常量与 schema 一致；`CK:55-95` Formula | schema 文件不随包 |
| criterion-policy-v1、disclosure-batch-v1、pending-work-v1、blob-pin-v1 | 56 | 做法不同 | 字段名均能在 CK/`disclosure.py`/`assurance_work.py`/SQL 列找到；blob-pin 的 `last_receipt_ref` 为 `last_receipt_id` 列 | schema 不随包，结构靠 Python/SQL |
| closeout-v1 | 17 | 部分 | 收尾体（ACON:424-521）字段与 schema 不同 | `root_resolution_ref`、`completion_spec_hash`、`pending_effect_keys`、`unsettled_operation_refs`、`accounting_pending_refs`、`dangerous_work_refs`、`report_ref`、`as_of_ms` 8 个字段不存在；无记录 |
| review-reply-v2 | 15 | 做法不同 | 现行格式版本 3（CK:309-360）加 claims/methods/summary | 放宽两处 A#6；加字段随阶段 C（B 级） |
| restore-authorization-v1 | 12 | A 级排除（#4） | — | — |

条数：要求 368；schema 相同或语义等价可找到 324；closeout-v1 缺 8 字段；restore 12 条 A 级排除；其余为"结构由 Python/SQL 承担、无一致性核对"。

#### sql-column-producers.json（129 条）

本次用 SDK 迁移链（至迁移 43）新建空库，读 15 张 `assurance_*` 表的 `PRAGMA table_info`：**129 列名、类型、非空、主键位置全部一致**；代码多 2 列（`assurance_pending_work.rechecks`、`recheck_started_at_ms`，opt.110，资产未回写）。逐列 writer/source_rule 没有逐条重核（抽查 closeout、pin、pending 三表与资产一致）。判定：按计划在用 129，资产缺 2 列（#259）。

#### schema/SQL 触发器与 queries（附带，计划 `sql/`）

- 计划 48 个触发器名全部存在；与计划不同的 7 个都是更严（6 个 no_replace 加自然键、`pending_update` 加 rechecks 单调）；代码多 200 个（188 来源屏障 + 12 守卫）。
- 索引：计划表级 `UNIQUE(mission_id,review_key,blob_hash)` 不存在，改为迁移 27 的 `assurance_blob_pin_object_live_uq`（新-3）。
- queries：Q01/Q02/Q07/Q10～Q15/Q17/Q18、领取/完成/等待/租约回收有对应；Q03 分步读；Q04 只剩精确对象读；Q05/Q06 随迁移 39 删（B-3）；Q08 由触发器自增代替应用层 CAS；Q09 只写不读（新-12）；Q16 不适用（无 GC）。

### 1.7 implementation 验收类资产（逐条）

用例简写：AA=`T/full_target/assurance_exec/test_a_assurance.py`、VV=`…/test_v_assurance.py`、CC=`…/test_c_assurance.py`、RF=`…/test_review_findings.py`、PV=`T/full_target/operation_completion/test_publish_variants.py`、RA=`T/product_world/test_requirements_amend.py`、OP=`T/product_world/test_operation.py`、RCT=`T/full_target/test_resolution_commits.py`、C07=`…/test_c07_host_api.py`。判定按"用例当前存在 + 断言覆盖 given/when/then 全部要点"严格判：缺一个要点即"部分"。

#### sdk-cases.json（48 组）

| id | 判定 | 当前用例 | 缺什么 / 记录 |
|---|---|---|---|
| A01 | 部分 | AA:110 | 只测纯编解码；缺"拒后无正式记录/验收/新调用"与"raw/费用保存" |
| A02 | 按计划在用 | AA:160 | — |
| A03 | 部分 | AA:229 | 缺"delivered 降 received"；"用户确认新版"一半按 AA 头注释删，理由"第 2 版产品上无写入方"已过时（RA:68 已写第 2 版） |
| A04 | 部分 | AA:257 | 只换 checkspec 身份；平台/target/env/manifest 未逐项换 |
| A05 | 按计划在用 | AA:310 | — |
| A06 | 部分 | AA:346 | 只有"钉材料"一个切点；reserve/package/binding/intent/receipt 切点、CAS 与 GC 争用、TTL 未测 |
| A07 | 部分 | AA:388 | 只有正向；负例一半按头注释删（"伪造作者集合"）；RCT:1187 变体疑似承接未登记 |
| A08 | 部分 | AA:412 | 缺"错绑定不算 official"本身、重放与费用 |
| A09 | 部分 | AA:445 | 纯函数；缺"旧 official 不覆盖""重送无新预留" |
| A10 | 部分 | AA:459 | 缺撤权后取材 |
| A11 | 部分 | AA:471 | "v2"一半按头注释删（理由过时）；缺无关变化复核 |
| A12 | 部分 | AA:487 | 只测格式修复一次；超长包/缺额度/无限新 key 未测 |
| A13 | 部分 | PV:97 | 并入（AA:7-9 记录）；缺根任务 VERIFYING、Obligation 未满足、准备贡献行 |
| A14 | 部分 | PV:97 | 并入；缺纯 ORDER、缺 manifest |
| A15 | 按计划在用 | AA:495 | — |
| A16 | 按计划在用 | PV:97 | 并入（AA:9）；分诊表:136 却写"删"（记录冲突） |
| A17 | 部分 | PV:97 | 并入；缺"无 intent""两个 MUST""已发危险动作收尾" |
| A18 | 部分 | RCT:1187（findings.json 登记）；疑似 RF:319 未登记 | 三处记录互相矛盾（AA:10-11 写删、分诊表写并入、findings.json 写 COVERED）；无"只有准备验收时直调根结论"变体与保留预算断言 |
| V01 | 按计划在用 | VV:144 | — |
| V02 | 部分 | VV:191 | 缺"规划器提规则写 Justification 被拒"、独立蕴含审阅 |
| V03 V04 V05 | 按计划在用 | VV:208、:232、:272 | — |
| V06 | 部分 | VV:473 | 缺"缺索引""未导入回执" |
| V07 | 部分 | VV:532、`product_world/test_desktop_preconditions.py:204`、RF:207 | 原名不存在；无"插负观察后旧证明在终闸被拒"同链断言 |
| V08 | 按计划在用 | VV:557 | — |
| V09 | 部分 | VV:595 | 未逐个消费方走一遍 |
| V10 V11 V12 | 按计划在用 | VV:621、:641、:296 | — |
| V13 | A 级排除（#4） | 残留 VV:670 只测根闸门 | — |
| V14 | 按计划在用 | VV:322、:688 | — |
| E01 E03 | 按计划在用 | PV:97、:564、:429；`test_completion_plan_commit.py:146` | 并入有记录 |
| E02 | 部分 | OP:182 | 缺 T0/审阅自动降级尝试、"HTTP200 不记 delivered"；`D/HTN补齐-实施记录.md:305` 写"E02 永不降级删除"与 findings.json COVERED 矛盾 |
| E04 | 部分 | PV:524 | 只参数化 3 轴（共约 10 轴）；"非空 operation_id 不充分"未测 |
| E05 | 部分 | PV:464 | 缺克隆 binding 被拒、UNKNOWN/无 intent 状态 |
| E06 | 部分 | RA:921、:1115、:1134（opt.162 新写） | 缺"旧回执晚到再用"变体与费用断言 |
| E07 | 部分 | PV:167（opt.162 新写） | 缺"最终引用写入方"那一路 |
| E08 | A 级排除（#3） | — | 随非保证通道删 |
| C01 | 部分 | CC:72 | 只三个切点；"提交后重送同 receipt"未测 |
| C02 | 部分 | CC:118 | 缺两审阅并发、撤权与接受竞争 |
| C03 | 部分 | CC:205 | 缺费用恢复、lease 过期断言 |
| C04 | 按计划在用 | CC:226 | — |
| C05 | 部分 | CC:276 | 缺弃分支危险在途/UNKNOWN 保留、晚回执、通知前撤权 |
| C06 | 按计划在用 | CC:323 | — |
| C07 | 部分 | C07:67、:188 | 原生点击未做（D 级） |
| C08 | 按计划在用 | `test_review_turn_retry_e2e.py:99`；`product_world/test_late_usage.py:36` | 并入有记录 |

条数：要求 48；按计划在用 18（A02 A05 A15 A16 V01 V03 V04 V05 V08 V10 V11 V12 V14 E01 E03 C04 C06 C08）；部分 28（A01 A03 A04 A06 A07 A08 A09 A10 A11 A12 A13 A14 A17 A18 V02 V06 V07 V09 E02 E04 E05 E06 E07 C01 C02 C03 C05 C07）；A 级排除 2（V13 E08）。

#### occ-coverage.json（12 条）

| id | 判定 | 当前用例 | 缺什么 / 记录 |
|---|---|---|---|
| OCC-01 | 部分 | PV:97 | 缺贡献行、"申请单读报告"显式断言、Obligation 未满足 |
| OCC-02 | 按计划在用 | `test_completion_contract.py:142-304`（8 条）、`test_completion_plan_commit.py:15,146`、`test_completion_spec_approval.py:110`、OP:182 | — |
| OCC-03 | 部分 | OP:68 | 三链关联、根审阅前不完成、重送只一份未测 |
| OCC-04 | 部分 | PV:524、:167 | 同 E04，只 3 轴 |
| OCC-05 | 部分 | 记录写删（`test_completion_contract.py:12`，理由"第 2 版无写入方"已过时）；疑似 RA:338、:376、:415、:457、:921 未登记 | 记录与实际不符 |
| OCC-06 | 部分 | PV:464 | 同 E05 |
| OCC-07 | A 级排除（#3） | — | 随旧通道删 |
| OCC-08 | 部分 | 登记 PV:97 不直调终提交；疑似 RF:319、RCT:1187 未登记 | — |
| OCC-09 | 部分 | PV:564、:599；CC:72 | 缺通知切点、真实子进程提交前后退出 |
| OCC-10 | 部分 | 登记的 `test_completion_plan_commit.py:55/:95` 不对题；疑似 `test_completion_scope_compiler.py:207`、`test_completion_data_dispatch.py:80` | — |
| OCC-11 | 部分 | PV:564 | 结果审阅各点冷重放、两连接竞争、费用未测 |
| OCC-12 | 部分 | PV:97、:211；OP:361 未登记 | — |

条数：要求 12；按计划在用 1，部分 10，A 级排除 1。

#### inherited-coverage.json（66 条）

| 组 | 判定 | id 与当前用例 | 缺什么 / 记录 |
|---|---|---|---|
| 完整覆盖 | 按计划在用 | CA-A01 A02（AA:160、:110）、A13（PV:97）、A17（CC:276）、A20（AA:110/:257）；CA-K01 K02 K04 K05 K06 K07 K08 K09 K12 K13 K14 K15 K17 K18（VV 各条）；CA-I06 I07（CC:323、:226） | 21 条 |
| 部分覆盖 | 部分 | CA-A03（缺 A 的未决动作核对）、A04（无 Commit 层拒绝与记录保留）、A05（运行时一半未登记）、A06（缺日志/身份保留）、A07、A08、A09、A10、A11、A12（缺子历史不删）、A14、A15（只同命令 CAS）、A16、A18；CA-K03、K10、K11、K16；CA-I01（无一用例同时核五样）、I02、I03、I04、I05（原生点击未做）、I08 | 25 条 |
| 映射不对题 | 未做 | CA-I10 外部评分器隔离——映射到迟到记账，从无对应用例 | 1 条；无记录 |
| 已删 | A 级排除（#4） | CA-A19、CA-I09 | 2 条 |
| X 组 | 未做 | CA-X01～X18：无逐条映射。疑似承接（均未登记）：X01 PV:299、`test_h1h_operation_alias.py:146`；X02 `test_p32_publish_regressions.py:64`；X03 `test_h1h_operation_alias.py:175`；X04 PV:211；X05 PV:211、`tests/integration/execution/test_effect_reconcile.py:561`；X07 `tests/integration/runtime/test_handoff_runtime_lease.py:107`；X08 PV:299、OP:261；X09 OP:312、PV:318；X10 OP:361；X12 弱疑似；X13 `T/step07/test_action_execution.py:45,82`；X14 Host `test_layered_scripted_lane.py:341`（不模拟丢回执）；X15 `test_review_turn_retry_e2e.py:99`；X16 同上 :137；X17 `test_htn_end_to_end.py:413`；X18 `test_atomic_child_terminal_signal.py:236`。**X06、X11 未找到任何承接**（搜了 retention/idempotency_window/expir*、contradict/RECEIPT_CONFLICT/quarantin） | 18 条；ASSUR:7 登记"仍没做"（实施自述），无用户决定 |

条数：要求 66；按计划在用 21，部分 25，未做 19（I10 + X 组 18），A 级排除 2。

#### mutations.json（16）+ additional-mutations.json（12）

现行改坏清单 `T/acceptance_assets/mutations.json`（执行器 `sdk/simple-harness-sdk/scripts/acceptance/run_mutations.py`）；09-23 的旧 AM 清单在 `SP/mutations.json`（独立脚本 `SP/tools/run_assurance_mutations.py`，A′ 后未重跑，不在现行清单里）。

| 计划 id | 判定 | 现行条目 / 结果 | 说明 |
|---|---|---|---|
| M01 mandatory_bypass | 部分 | 无现行条目；仅旧 AM02（09-23） | 锚点仍在，未纳入现行清单、未重跑 |
| M02 fake_official | 部分 | 仅旧 AM06/07/08 近似 | 同上 |
| M03 wrong_subject_check | 部分 | 仅旧 AM03 近似 | 同上 |
| M04 first_current_acceptance | 按计划在用 | AS-A18 → RCT:1187，KILLED | — |
| M05 empty_effect_catalog | 未做 | 无（旧 AM13 目标用例已删） | 无记录 |
| M06 effect_receipt_nonempty | 部分 | AS-M06 登记但不绑用例 | 理由"身份比对没有产品路径能单独碰到"（实施自述） |
| M07 latest_input | 部分 | 仅旧 AM12 近似 | — |
| M08 M09 M11 M13 M15 M16 | 按计划在用 | AS-M08/M09/M11/M13/M15/M16，全部 KILLED | M15 绑定 p35 用例而非计划指定的 C03/C05 |
| M10 cached_anchor_cycle | 部分 | 仅旧 AM22 近似 | — |
| M12 purpose_ignored | 部分 | 仅旧 AM14 近似 | — |
| M14 ack_before_intent | 按计划在用 | AS-FM08（同一改坏语义）→ C04，KILLED | — |
| F-M01～F-M04、F-M07～F-M11 | 按计划在用 | AS-FM01/02/03/04/07/08/09/10A/10B/11，全部 KILLED | — |
| F-M05 old judge releases pools | 按计划在用 | FIN-01 → `test_terminal_unknown_release.py:148`，KILLED | — |
| F-M06 | A 级排除（#4） | AS-FM06 不绑 | — |
| F-M12 drop resolved schema hashes | 未做 | AS-FM12 自判"不适用" | 理由"SDK 不随包带 schema 哈希"，实施自述，无裁决 → B′ |

条数：要求 28；按计划在用 18（M04 M08 M09 M11 M13 M14 M15 M16、F-M01 02 03 04 05 07 08 09 10 11），部分 7（M01 M02 M03 M06 M07 M10 M12），未做 2（M05、F-M12），A 级排除 1（F-M06）。另：10-05 结果文件 26/26 KILLED 是本次唯一可查的执行证据，本次没有重跑。

#### findings-sdk-tests.json（15）

见 1.5：按计划在用 2、部分 10、做法不同 1、未做 1、A 级排除 1。计划要求的 15 个独立 nodeid（`test_review_findings.py::test_*`）实际存在 7 个（F01 F02 F03 F04 F06 F08 F11）；F05/F09/F10/F12/F13 用映射代替，F07 A 级，F14/F15 无用例。

#### model-scenarios.json（M01～M04 × 3）

| 项 | 判定 | 证据 | 说明 |
|---|---|---|---|
| runner 接真实运行时、provider 不 stub | 部分 | `backend/scripts/assurance_model_scenarios/run.py`（未入库，10-06 09:42 起出现） | 不是计划的 `tools/run_assurance_model_scenarios.py`；D 级进行中 |
| M04 受控真实 connector 本地服务、指定切点丢首个回执 | 做法不同 | `hooks/sitecustomize.py` 注入；file_publish 到接收目录 | 无记录 |
| 预算（300k/16/32/900s）、超预算即 FAIL | 做法不同 | `run.py:88,98` 放宽；`:352-357` 通过不看预算 | 与计划相反，无记录 → C 级 |
| 预登记 trial、不补抽、新 attempt 留历史 | 做法不同 | accurate-report 第 1 局失败后同局号重跑并覆盖 | 无记录 → C 级 |
| 12/12 通过门 | 未做（进行中） | 11:30 快照：M01 3 局"通过"（2 局超预算）；M02 0/3；M03 1 败 2"过"（均超预算）；M04 第 1 局败、第 2 局在跑 | D 级 |

#### TEST-MAP.md / VALIDATION.md / P/tests/

- TEST-MAP 第 1～7 条分别对应上面 sdk-cases、findings、inherited/occ、mutations、stateful（#238 C 级）、原生（#228 D 级）、真实模型（D 级）、独立审阅（#239 部分）。"actual_nodeids 执行后同 fingerprint 回写"：计划包与 `SP/` 回写版都没有更新（`SP/*.json` 中 39 个用例名已不存在），未做。
- VALIDATION.md 与 `P/tests/`（168 条参考 unittest、19 个参考变异）按计划只验证规格资产、不计入产品通过——按计划在用（不进 SDK 是计划本意）。`P/tests/test_review_findings.py` 是计划自带的参考版本，不等于 SDK 版。

## 第二节 · C 级无记录偏离清单（穷尽）

口径：代码或验收与原始计划不一致，且在 `WIP`、`ASSUR`、`ARCHITECTURE/AGENT_ORCHESTRATION.md`、`ARCHITECTURE/PROJECT_STATUS.md`、`D/` 下全部 `HTN补齐*`/`Assurance-补改*`/方案/裁决文件、`T/acceptance_assets/*.json` 里都找不到登记。旧对照文档（`Assurance-现状对照-2026-10-05.md`）第一～二节、附一～附五只是"发现"，不算登记；它第五节是 opt.162 的处理记录，可算登记。按影响排序。

### 2.1 代码（会影响结论或运行）

| # | 条目（对照行） | 原计划 | 现状 | 影响 |
|---|---|---|---|---|
| C-01 | 收尾最后一次事务不重查证书到期与时钟（#136） | §7.2"最终事务重读当前 epoch/权限/effect/运行集合才 READY→FINALIZED" | 重评只看效果/意图/预留/钉住对象/资料变更；到期、时钟回拨、纪元都不比（ACON:362） | 证书刚过期或时钟回拨期间仍可能定稿。opt.110 方案只说了"只比钉住对象"（B-1），没说到期与时钟 |
| C-02 | 换库后整个编排起不来，而不是隔离（新-11） | §10.1 marker 不匹配/部分库 → QUARANTINED，只开非披露诊断 | `assurance_root_commits.py:148-149` 抛错在 EH:656 的 `try` 之外 | fail-closed，但用户看到的是服务起不来而不是隔离提示 |
| C-03 | 原生下载/摘要/披露不经使用证书（#278、#286） | C.1"四种读者都不得绕开共同 read_use"；commit_use 守 disclose | 只核归属与根门（`facade.py:856-866`） | 单身份桌面影响小；多身份即越权 |
| C-04 | 诊断接口会清掉共用缓存里的真实候选（#287、新-13） | use_check 只读诊断 | `api/assurance.py:763-792` 写入并 `forget` VAL 共用缓存（VAL:188-201） | 可能让一次真实验收/根结论提交被拒、下一轮重来；并发时序未验证 |
| C-05 | 预算等待与"需重算"共用 32 次/300s 上限（#178） | §9 第 5 步 BUDGET_WAIT/CHECK_PENDING 各存原因 | 都进 `recheck`（TICK:301-315） | 长时间预算等待会被打成 MANUAL_REQUIRED，要新事件才重开 |
| C-06 | `is_assured` 读错时终态通知静默不发（新-2） | §7.3 终态同事务写通知意图 | FW:111 读错返回 None | 用户收不到结束通知；不通向误完成 |
| C-07 | 收尾不查首个审阅员尾部预留与范围外结果不明的已交出动作（新-14） | §7.2"原 operational 责任…真实收敛" | ACON:478-490 只看根范围必需效果等 | 可达性未核实 |
| C-08 | 失败码 `SOURCE_NOT_CURRENT` 未实现（新-15） | ref-resolution-map 每 kind 的 failure_codes | grep 为零 | 当前性错误用别的码报，调用方无法按名区分 |
| C-09 | commit_receipt 解析不核写者/回执种类（#35） | §3.1"真实 Commit writer" | RD:191 只核 mission_id | 新消费方忘了自核 kind 就会把任意回执当来源 |
| C-10 | 解析器返回形状不同，解析时不核用途/访问（#31、新-1） | §3.1 `ResolvedRef(...)`，"校验…要求的用途和当前访问" | `ExactMetadata`，用途/访问在使用点另核 | 新使用点漏调 `_permission` 即只凭正文放行 |
| C-11 | 3 类解析器生产无人构造（observation、resolution、completion_spec） | 各 kind 有真实生产者 | 只有解析器 | 死代码；与"一件事一条路径"不符 |
| C-12 | 种类表手写副本（#29） | 种类唯一来源 common.schema.json | `refs.py:12-44` 手写，无一致性测试 | 以后单边修改会静默分叉 |
| C-13 | completion_scope/spec 直接读表（#50） | 经原 OCC reader | RD:119-120 | 归属/occurrence 校验与 OCC reader 可能不一致 |
| C-14 | check_spec 每任务懒注册（#41） | registry 安装时固定系统写入 | LC:478-511 | 身份仍不可变，影响小 |
| C-15 | 审阅绑定 `round_no` 恒为 1（#119） | 显式独立复审请求才新 round | PR:283、CR:206 | 审计时看不出第几轮 |
| C-16 | 命题键无 namespace/scope，哈希截 32 位（#155） | canonical({predicate,typed_args,namespace,scope}) | `predicates.py:294-301` | 不同作用域同名命题算同一键；观察已不进验收公式，影响小 |
| C-17 | closeout 文档结构不按 closeout-v1（1.6 field-producers、pin_fields） | 收尾体含 root_resolution_ref/requirements_ref/completion_spec_hash/pending_effect_keys/unsettled_operation_refs/accounting_pending_refs/dangerous_work_refs/report_ref/as_of_ms | 用 id 列表、id 外键，不钉要求版本 | 收尾记录不能独立证明是按哪版要求收的尾 |
| C-18 | 审阅员政策/上下文政策 Pin 无注册读者；check-spec 三 Pin 用随包文件哈希（pin_fields） | 已注册政策/SchemaCatalog/范围政策解析器 | 代码常量、运行时现算、文件 sha256 | 政策改了只要常量没变就看不出 |
| C-19 | blob-pin 两个回执引用改为 SQL 外键列（field_contracts） | JSON `*_receipt_ref`，只收 commit_receipt | `source_receipt_id`/`last_receipt_id` | 外键+回读核 hash，等价 |
| C-20 | NOTIFY 待办不与完成事件同事务建；Host 断线重连不按 seq 续读（#137、#138） | §7.3 | 下一轮 tick 才入箱；Host 只在启动时 backfill | 运行中断线后通知可能晚到 |
| C-21 | 隔离/未绑定时整轮停，不逐行存等待原因（#179） | §9 第 5 步 | TICK:201,229,251 | 排查看不到哪项在等 |
| C-22 | Host 不经 projection、Host DTO 运行时不按 schema 校验（#224、#260、host.projection、schema_owners） | §13.1、F13 | 原样转发；校验器只在测试与前端 | SDK 回复漂移时 Host 不会拒 |
| C-23 | 历史视图准则固定第 1 版要求（#309） | C.6 history_state 取固定 seq 的原状态 | `api/assurance.py:514-519` | 改要求后历史切面显示错的准则（仅显示） |
| C-24 | HISTORY_UNAVAILABLE 挂在 SOURCE_UNAVAILABLE 码下（#312） | C.6 | 消息里带名 | 计划本身与 host-error 枚举矛盾，需计划修订 |
| C-25 | 反向依赖索引只写不读（新-12） | queries Q09 | `assurance_store.py:544` | 死写入 |
| C-26 | 残留不可达分支与死代码（新-16、#26、#132） | 旧路径直接删（用户口径） | CS:3310-3311、ACON:409-410、`root_review.py:1072`、CS:3281-3290；FW:11 说明仍写"releases the terminal pools" | 误导后来者；opt.162 自称已删 |
| C-27 | 事件消费表 REVIEW 侧三行 | event-consumer-map | 已处理（2026-10-07）：第 2 行补做，第 1、3 行计划改写（B 级 #39、#40） | — |
| C-28 | 五种使用用途签发 | PLAN/START/CONTEXT/RECOVERY/MAINTAIN 证书 | 已处理（2026-10-07）：四种时点用途已签发，MAINTAIN 按 §8.4 挡；时点证书不进观察（B 级 #42） | — |
| C-29 | 计划写错的文件位置（S20、S22） | — | `operation_completion.py:302`、`planning_operations.py:567` | 只影响按资产找代码 |

### 2.2 验收（测试、改坏、试验）

| # | 条目 | 原计划 | 现状 | 影响 |
|---|---|---|---|---|
| C-30 | **BLOCKED_UNKNOWN 收尾态全测试库无一处断言**（F06） | F06"root 后 UNKNOWN/hold/退出正确" | `grep -r BLOCKED_UNKNOWN tests` 为零；`assurance_findings.json` 把 F06 标 COVERED | 危险效果未知时不收尾这条核心保证没有回归保护 |
| C-31 | F01 反例缺 COMPOSITION 一类与"同通知重送"注入；F02 缺"已结算预留开新调用被拒"与错 collector；F03 缺追加批次冲突；F08 只有一类真实写方 | findings-sdk-tests.json | 1.5 | findings.json 把这些标 COVERED |
| C-32 | 终态写点枚举测试不全（#131、TW7） | §7.1"AS-0 枚举所有 terminal 写入点，测试证明无旁路" | 只数 EH 4 个调用 + 正则扫 COMPLETED | 产品取消、接管停止、判定写失败、命令行取消等新增旁路测不出 |
| C-33 | 状态化随机序列规模 6 种子 × ≤8 轮（#238） | TEST-MAP 200×50 | `test_stateful_assurance.py:124` | 约 1/200；"不用 Hypothesis"有裁决，缩规模无记录 |
| C-34 | 计划改坏 M05 无任何条目；M01/M02/M03/M07/M10/M12 只有 09-23 旧 AM、不在现行清单、A′ 后未重跑 | mutations.json | 1.7 | ASSUR:5 称"计划点名的改坏（M 组、F-M 组）在现行改坏清单里登记为 AS-*"，与事实不符 |
| C-35 | 48/12/66 覆盖清单大量"部分覆盖"：sdk-cases 28、OCC 10、继承 25 | 每条 given/when/then 全部断言 | 1.7 | 只有删除/并入有记录，"并入后断言变少"无记录 |
| C-36 | CA-I10 外部评分器隔离映射不对题，从无对应用例 | inherited-coverage | 映射到迟到记账 | — |
| C-37 | 删除理由过时：A03/A11 半条、OCC-05 以"要求书第 2 版产品上无写入方"删除，现 RA:68 已有第 2 版写入 | sdk-cases、occ | 测试头注释未更新，未回挂 RA 用例 | 改要求后旧证据不复用这一组保证登记失真 |
| C-38 | 记录互相矛盾：A18（三处三说）、A16、OCC-05、E02（"永不降级删除" vs COVERED） | — | 1.7 | 清单不可信 |
| C-39 | 真实模型脚本口径与计划相反：超预算不判 FAIL、场景 3/4 预算放宽到 450k/1200s、同局号重跑覆盖旧记录（#246、#253） | §15 每 trial 预算、不补抽、新 attempt 留历史 | 未入库脚本 `backend/scripts/assurance_model_scenarios/run.py` | 若按此出"通过"结论会与计划口径冲突 |
| C-40 | 计划要的程序性登记都没做：BW 逐边登记（#233）、`gate-commands.local.json`（#256）、integration-map/ref-map/field/sql 资产回写（#258、#259）、状态标签（#250）、WIP 停在 09-24 仍写 IN_PROGRESS（#249）、`SP/*coverage*.json` 39 个失效用例名 | §14、§16、附录 A | — | 无用户决定不做 |

C 级合计 **40 条**（代码 29、验收 11）。

## 第三节 · B 级偏离（子代理裁决 / 补齐计划"保持现状" / 间接认可）

| # | 计划原文（要点） | 现状 | 裁决出处 | 有无用户原话 |
|---|---|---|---|---|
| B-1 | §7.2 最终事务重读当前 epoch/权限；I7 撤回立即使旧证书无效 | 收尾只逐项重读证书钉住的对象，纪元列为可忽略，访问/策略不比；opt.162 补了资料变更（`SOURCE_CHANGE_OPEN`） | `D/收尾前复查依据-方案.md`；`D/PLAN-STATUS.md:524`（opt.110） | 无。用户只给了红线"不写 goal_resolutions.validity"（`assurance_recheck.py:13`）与"做第 4 项"，没有对"不比纪元"表态 |
| B-2 | §6.2 原 Provider UNKNOWN 不能靠 ordinal2 重复请求（F05 反例） | 调用没回来 → 记 TURN_FAILED → 第 2 次调用（新会话），旧预留按上限计 | `D/HTN补齐-阶段B卡住缺陷-裁决.md:271-300`（第 6 类） | 无直接原话；裁决引用用户 09-28"服务端报错/重启打断不计次数、原地重试"（A#14），但该决定讲的是计次，不是允许对结果不明的调用重发 |
| B-3 | §8.1/§8.3 justification/support/rule 屏障与正负支持 clean closure；§5.3 失败组反证仍核对；queries Q05/Q06 | 两张支持表删（迁移 39），验收支持只用审阅锚+检查锚+固定规则，失败组回执不参与 | `D/HTN补齐-阶段D-开工裁决与施工清单.md:55-62`（偏差单 3）；`D/HTN补齐-阶段C-偏差裁决-知识支持集合.md:11`；`D/HTN补齐计划-2026-10-02.md:310`（3.11 版） | 无；文中写"已告知用户"，是告知不是批准 |
| B-4 | §12 新空库按历史重放重建投影，不足标 INCOMPLETE | 业务重放 v3 只逐表比对，不重建库 | 阶段 G 开工裁决（`D/HTN补齐-阶段G-开工裁决与施工清单.md`）；`D/HTN补齐计划-2026-10-02.md:42`"全业务重放 v3 取代 v2" | 计划开工经用户同意（间接）；无逐条原话 |
| B-5 | E06：旧接受不满足新要求，可新 scope 审原事实 | 结果审查与存储层归属核对从"整份 task_ref 相等"放宽为"同 task id 且要求版本不倒退"；同一次发布在两版要求下各一张交付回执 | `D/Assurance-补改-偏差裁决-发布后改要求.md`（10-05 独立裁决子代理，选修法 C） | 无 |
| B-6 | §6.1 按 account_for_purpose 记账（组合记 parent_compound_task） | 组合/根终审记任务总账，操作类记准备操作的叶子 | `D/HTN补齐计划-2026-10-02.md` 表一 22"保持"（文中称"都是用户逐项定过"）；`D/HTN-片B-实施记录.md:178`；`D/PLAN-STATUS.md:306` | 计划文档自称用户定过，本次未找到原话 |
| B-7 | §8.4 高水位只增、每次观察持久 | 高水位每走 10 秒才落库一次 | `ARCHITECTURE/AGENT_ORCHESTRATION.md:4`"联测裁决 2026-10-05"；`SDK/assurance/clock.py:12-14` 注释 | 无 |
| B-8 | §16 verify_delivery/check_plan/capture_identity 生成 source-map | 替代脚本与清单删除 | 阶段 B 收尾裁决（`D/HTN补齐-实施记录.md:247`） | 无 |
| B-9 | TEST-MAP 状态化随机序列（Hypothesis 风格） | 固定种子随机序列（规模缩减另见 C-33） | `D/HTN补齐计划-2026-10-02.md` 第二节"阶段 F 开工裁决新增的偏离"（"随机状态机不用 Hypothesis"） | 无 |
| B-10 | C.1 交接、Input/Context 读者过共同 read_use | 交接用 HTN 有效性见证（`action_commits.py:1111`），上下文按知识是否当前过滤 | `D/HTN补齐计划-2026-10-02.md` 表二 9；`D/HTN补齐-开工前裁决-2026-10-02.md:28` | 计划开工经用户同意（间接） |
| B-11 | review-reply-v2 `additionalProperties:false` | 格式版本 3 加 claims/methods/summary | HTN 补齐阶段 C/C3 实施（e42a738e） | 用户 10-02 定做"做法复用/知识库"（间接）；加字段本身无原话 |
| B-12 | §8.1 屏障对所有任务 | 迁移 41：全局触发器只给未结束任务写证据变更事件（全局纪元照旧 +1） | `D/HTN补齐计划-2026-10-02.md` 第二节 | 计划开工经用户同意（间接） |

### 3.1 B′：只有实施自述（无裁决、无用户原话），严格讲接近 C 级

| # | 计划原文（要点） | 现状 | 自述出处 |
|---|---|---|---|
| B′-1 | §5.1 在原 Requirements 批准事务里批检查政策（#78、#269、#289、BW02） | Host 每轮按冻结范围无损投影（`HOST_LOSSLESS_AUTO`），审阅前再投一次 | WIP:1259-1267 |
| B′-2 | §5.3 全局安全 finding 必须映射 mandatory（#95） | 未做 | `T/acceptance_assets/assurance_findings.json` F04 gap |
| B′-3 | §3.1 tool_receipt/policy/authority/capability 精确读（#51、#52） | 读不了，tool_receipt 仍被允许作审阅对象 | findings.json F02 gap |
| B′-4 | §10.1 状态文件缺失/不匹配 → 隔离（#187、schema-source-rules root_incarnation_id） | 启动自动写回 | ASSUR:7"与原计划 §10 不同，未改" |
| B′-5 | §9 预算卡住的审阅不挡后续撤权需有反例（F09、#176） | 只有代码结构 | ASSUR:7；findings.json F09 PARTIAL |
| B′-6 | §15 继承 X 组 18 条（#243） | 无映射 | ASSUR:7 |
| B′-7 | F12 / §12.1 check_plan 对实际资产运行（#213）；F-M12 | 未做；AS-FM12 自判不适用 | findings.json F12；mutations.json AS-FM12 note |
| B′-8 | M06 改坏须被 E04/E07 杀 | AS-M06 登记不绑 | mutations.json AS-M06 note |
| B′-9 | §13.2 隔离 Host 副本 + 独立 venv + prepare_native_manifest（#226） | 共享 Host 钉 vendored wheel | ARP HANDOFF §7.23 |
| B′-10 | §13.2 启动记录实测指纹（#227） | wheel 哈希常量 | 旧对照文档第五节"3～9 未改，属记录项" |
| B′-11 | §8.1 `_mark_assurance_change_locked`；直接 SQL writer 登记（#152、#153、S13、S18） | SQL 触发器 + 冻结清单 | WIP:44-46 |
| B′-12 | §8.1 ACL/权限变化推 global（#151） | 无 ACL 表，`FixedPrincipalAuthority` | WIP:876 |
| B′-13 | §8.2 四种 key 定义（#157） | ACCESS 加 ref、QUERY_SET 指纹不含纪元 | WIP:1318；ASSUR opt.110 条 |
| B′-14 | §8.4 发证同事务登记到期唤醒（#165） | 每轮扫证书表 | WIP:34 |
| B′-15 | §8.4 MAINTAIN 持续监测（#164） | 归 A（命中 #9：无使用者，签发方挡住） | `推后第1批-P1b-偏差裁决.md` 第 1 件 |
| B′-16 | §6.1 kind='plan'（#104）；提取 `_create_service_intent_locked`（#108） | TASK_CONTENT 用 critic；未提取 | WIP:337、WIP:286-287 |
| B′-17 | §12 pin 唯一键 `UNIQUE(mission,review_key,blob_hash)`（新-3） | 迁移 27 改按对象、仅非 RELEASED | `assurance_pin_object_schema.py` 模块注释 |
| B′-18 | §12.1 记录 ≤256KiB（新-6） | 清单/快照读 8MiB | ASSUR 09-26 第 1 项 |
| B′-19 | §11 循环入口具名 CREATION_CONTRACT_UNRESOLVED（新-9） | 停机原因 `unsupported_unbound_mission` | 旧对照第五节 |
| B′-20 | §10.3 非披露诊断与隔离只读分支（#195、新-10） | 保留但只在测试 | ASSUR:6 |
| B′-21 | §14 默认 ON 与验收同交付 → 先开后验 | 用户 09-23 拍板（WIP:1245"用户拍板的顺序调整"） | 这一条有用户原话（WIP 转述），实为 D 级排序决定 |
| B′-22 | §392 不为验收装运行时依赖（#240） | venv 装 jsonschema | WIP:1207 |

## 第四节 · A 级排除命中清单

| 编号（00 文件第三节） | 命中条目 | 证明 |
|---|---|---|
| #1 金额计价删 | #36（reserve 上限改 token）、#297（计价部分） | 迁移 38 删金额；`D/HTN补齐-阶段C-删金额计价-清单.md` |
| #3 旧通道/旧审阅路径/旧根审阅员删 | #6（TaskGraph 独立开关）、#27（旧 record_review）、#76（旧模板空工具）、#112、#129（legacy 保持）、#132（系统尾池）、#198、#205、S12、reviews.MISSION_FINAL、TW6、E08、OCC-07 | `D/HTN补齐计划-2026-10-02.md:161`；ASSUR:6"10-03 起保证通道是唯一通道"；`assurance_factory.py:172` |
| #4 离线备份连受管恢复删 | #16、#185、#189～#194、#211、#284、restore×3、BW14、V13、CA-A19、CA-I09、F07、F-M06、restore-authorization schema（field-producers 12、all_reference 3、schema_owners 1） | `D/HTN补齐计划-2026-10-02.md:5,120,165-166`；全库无 `offline_backup`/`reauthorize_restored_read` |
| #6 审阅员回复格式口径 | 新-7（剥围栏、忽略空值多余字段） | CK:397-407；`D/HTN补齐计划-2026-10-02.md:191,258` |
| #8 判断交给 LLM | #86（citation 交审阅员判，表一 12 opt.134） | `check_specs.py:108` |
| #9 HTN 一致性 8 处 | #9（`Criterion.phase` 删、审阅包加字段的一部分） | `D/HTN一致性补改方案-2026-10-04.md:62` |
| #13 未知用量按上限结清 | #135（KEEP_HOLD_PENDING） | ASSUR:41；ACON:576,709 |
| #15 文档任务 code_test 无可证内容不计必过 | #87 | WIP:1254-1257 |
| #17 开发期不兼容旧数据、旧路径删 | #5（老任务只停不换）、#9、#198、#201 | 用户 09-25/27/30 |
| #20 判不下来复审一次再问人 | #101、#115、#118、ordinal 用于复审、#271 中复审部分、#116 第二次仍坏问人 | `D/审阅升级-方案.md:3` |
| #11 纯内容自动确认 | #204（风险动作仍等人，按计划在用） | `deployment/duties.py:54-96` |

A 级排除共计：正文 21 行（第一节 1.1～1.4′）+ 资产 31 条（含 F07、F-M06；field-producers 的 restore-authorization 12 条逐条计）。

## 第五节 · D 级待办（用户明说排后，不算不做，算未完成）

| # | 待办 | 用户依据 | 当前状态 |
|---|---|---|---|
| D-1 | 真实模型 M01～M04 各 3 局（F15、#245～#247、model-scenarios） | 10-05 用户定"排最后"（00 文件第四节）；09-23 用户"12 局记为后续项"（WIP:1245） | 10-06 另一会话正在跑（脚本未入库）。11:30 快照：M01 3 局"通过"（其中 1 局重跑、2 局超预算）；**M02 0/3**；M03 1 败、2 "过"但超预算（第 3 局 114 万 token）；M04 第 1 局失败（`effect_acceptance_recorded`）、第 2 局在跑。按计划口径（超预算即 FAIL）当前不可能 12/12；另有 C-39 的口径偏离 |
| D-2 | 保证视图非主流程真机点击：审阅详情、待效果/收尾、断线重连、历史/当前切换、撤回（#228、C07、CA-I05、F13） | 10-05 用户定"排最后" | 只有主流程点击记录 |
| D-3 | 独立代码审查（#239、F15） | 09-23 用户"独立审阅记为后续项" | 只有 09-25 一次；之后 opt.32～opt.162 大量改动未整体审 |
| D-4 | legacy/全量回归（#236） | 用户 10-01"不许自行跑全量回归"（流程约束，需用户点名才跑） | 10-01 最后一次全绿；10-02/03 删旧通道后未跑 |

## 第六节 · 与 `Assurance-现状对照-2026-10-05.md` 不一致处

| # | 旧文档写法（位置） | 本次结论 | 谁对 |
|---|---|---|---|
| 6-1 | 第五节"`network is None` 旧分支一并删" | 只删了 `judge_mission` 里那处；`_require_root_resolution`（CS:3310-3311）与收尾（ACON:409-410）仍有同类不可达分支；`CommitService.finalize_assured_mission` 包装、`root_review.request()` 无调用者 | 本次（新-16） |
| 6-2 | 第五节"有用例守着 F01～F06、F08、F10、F11"；`assurance_findings.json` 把 F01/F02/F03/F04/F06/F08 标 COVERED | F01 缺 COMPOSITION 与重送注入；F02 缺已结算预留复用被拒；F03 缺追加冲突；F04 全局安全 finding 未做；F06 全库无 BLOCKED_UNKNOWN 断言；F08 只一类真实写方。真正按计划的只有 F10、F11 | 本次（1.5、C-30、C-31） |
| 6-3 | 第五节"计划点名的改坏…登记 `AS-*` 22 条"；ASSUR:5"计划点名的改坏（M 组、F-M 组）在现行改坏清单里登记为 AS-*" | M05 无条目；M01/M02/M03/M07/M10/M12 只有 09-23 旧 AM，不在现行清单、A′ 后没重跑；F-M12 自判不适用 | 本次（C-34） |
| 6-4 | 第五节"E07 新写；A18、E02 登记到现有用例；E06 见上（已修）" | E07 缺"最终引用写入方"一路；A18 登记的 RCT:1187 不测"只有准备验收时直调根结论"与保留预算，三处记录互相矛盾；E02 缺降级尝试与 HTTP200；E06 缺"旧回执晚到再用"变体 | 本次（1.7） |
| 6-5 | 附四 §6"存在 32"、附二 §15"48 组确定性场景 验收已做" | 当前完整覆盖只有 18 组，28 组只部分覆盖 | 本次（C-35）；旧文档按"用例名在不在"判，没读断言 |
| 6-6 | 附四 §6/§12 E06、E07"缺失"、A18"已删" | opt.162 后 E06/E07 有新用例（部分覆盖），A18 登记到 RCT:1187 | 旧文档在其时点对；同文第五节已更新但附四未改，文内自相矛盾 |
| 6-7 | 附一 7.9 判"在用"；附三误读 7 改为"部分做到：失败类已枚举，完成类未枚举"；第五节称已"把完成补进写方枚举" | 补了 COMPLETED 的正则扫描，但产品取消、接管停止、判定写失败、命令行取消等仍不在枚举里 | 本次（#131、C-32） |
| 6-8 | 第一节第 1 条"收尾不核资料变更"→第五节"已改" | 资料变更这一项确已改（ACON:469-473）；但同一条计划原文里的纪元、证书到期、时钟、权限仍不在收尾最后事务里核 | 本次（#136、B-1、C-01）；旧文档只修了它点名的那一半 |
| 6-9 | 第一节第 3 条"状态文件缺失/不匹配会被自动修好"，未提换库 | 另有新-11：库里没有安装回执但状态文件在时，编排整体起不来（不是隔离） | 本次补充 |
| 6-10 | 第二节第 11 条/附二 §15.1"`index.md` 仍把已删的 `assurance_profile` 开关写成现状"、BW16"部分（文档没回写）" | 10-05 已回写，`ARCHITECTURE/index.md:1` 明确写已删、较早条目为历史 | 旧文档当时对，现已过期 |
| 6-11 | 附二 §13.2"启动记录导入身份、wheel 哈希、Host/SDK 指纹 在用"；第一节第 8 条又判"做法不同未记录" | 部分：组装处实测，`host_fingerprint` 的 wheel 哈希是常量，PID/地址/模块路径不逐局记 | 本次（#227）；旧文档自相矛盾 |
| 6-12 | 附四 5.1 kinds 统计"在用 22"，commit_receipt 判"在用" | commit_receipt 不核写者应判"部分"；另 `SOURCE_NOT_CURRENT` 失败码全包未实现，旧文档未提 | 本次（#35、新-15） |
| 6-13 | 旧文档未提的问题 | `use_check` 清共享候选缓存（C-04）；`is_assured` 读错静默不发通知（C-06）；预算等待共用重算上限（C-05）；收尾不查尾部预留与范围外动作（C-07）；closeout-v1 结构 8 字段缺与不钉要求版本（C-17）；政策/schema Pin 无注册读者（C-18）；五种用途从不签发（C-28）；反向依赖索引只写不读（C-25）；全测试库无 BLOCKED_UNKNOWN（C-30） | 本次新增 |
| 6-14 | 第五节"受影响的用例文件 420 条全过；改坏 26 条全部被抓到" | 结果文件 `E/2026-10-05/mutations/results-185642.json` 确为 26 条 KILLED（时间 18:56，早于提交 18:57）；"420 条全过"无汇总文件可核，本次按规则不跑测试 | 改坏部分属实；用例数未复核 |
| 6-15 | 第五节"仍没做"清单（真实模型、点击、X 组、F09 用例、清单回写） | 一致；另需加：C-30～C-40 各项、M05 等改坏、真实模型脚本口径偏离（C-39） | 本次补充 |
| 6-16 | 附二 §15"stateful 验收已做（固定种子）"→附三改为"做法不同未记录" | 同意附三：规模约为计划的 1/200，且"不用 Hypothesis"其实有阶段 F 裁决（B-9），只有缩规模无记录 | 附三对，本次细化 |
| 6-17 | 附一 7.15 / BW10 "做法审阅与操作提案采用不核使用证书"（附五已改为"计划外风险观察"） | 同意附五：计划没把这两处列为证书读者；但计划列了"披露、原生下载/摘要"，这两处确实不过证书（C-03） | 附五对；本次补出真正的缺口 |
