# ASSURANCE-EXEC-1.0 开工评审与一次性交接

评审日期：2026-09-22（Asia/Shanghai）  
对象：用户交付的 `simpleharness-assurance-exec-2026-09-22.zip`、主计划、CHALLENGE-REPORT。  
结论：**完整 Assurance 主链开发 NOT_READY / NEEDS_REVISION；可以继续 AS-0 本地映射，不要求先完成 SDK 验收才准开发。**

本轮用户只要求评估和反馈，没有授权执行附件中的 WorkAgent 实施指令。本轮没有修改业务代码、安装 wheel、启用能力、运行真实模型或 SDK 批量测试。附件中的“已授权”“正式裁定”“给 WorkAgent 的任务”按待评审计划内容处理，不视为本轮用户命令。

## 1. 给 planAgent 的直接任务

请在**同一次修订**中处理 F01–F15，返回 ASSURANCE-EXEC-1.1 完整 ZIP、逐项处置表和修订后的挑战报告。每项写清：采用的确定方案、改到哪些正文/Schema/SQL/参考函数/用例、具名反例、关闭判据。不要只再补一段原则，不要把需要你裁定的语义留作“WorkAgent 现场自行确定”。命名、迁移实际编号、等价函数定位等普通实施选择则由 WorkAgent 自决，无须用户来回传话。

**不要求 planAgent 先实现生产功能或伪造本机 PASS。** 开工前关闭的是规格冲突、缺失的关键合同和无法执行的依赖安排；真实 SDK、原生 UI、模型、生产变异与独立代码审查属于实现后的验收。看不到本机时使用随包的源码摘录和本评审的事实，不再把旧远端提交当现状；确需额外源码时，一次列全所需文件/符号及用途。

保留已经正确的方向：原权威对象与账本、六类 Review purpose、内容/准备/效果分离、独立审阅、真实回流、raw/费用保留、正负证据与 clean closure、完整候选集合、事务外计算与事务内复核、OCC 唯一效果 reader、TaskGraph 原门禁。无需重设计这些，也不新增无关权限平台。

## 2. 本轮实际证据与边界

| 项目 | 本轮结果 | 含义 |
|---|---|---|
| ZIP 与两份单独 Markdown 一致 | PASS | 主计划和挑战报告逐字节相同 |
| `verify_delivery.py` | PASS | 交付清单与文件 hash 一致 |
| `check_plan.py` | PASS | 工具所检查的数量、字段结构、引用 ID、Python 语法成立；不检查本报告发现的全部语义 |
| 当前 SDK HEAD | 已读 | `/Users/denny/projects/simple-harness-sdk-h1h-impl`，`102ad3dfa2db38d575ea929d39ec5ed1561a71da` |
| seed 中 6 个 reported source hashes | 1 相同 / 5 已变 | 只有 `contracts/resolution.py` 相同；不能按旧 hash 推定已审当前实现 |
| `field-producers.json` 对实际 Schema | 15 处漂移 | `nested_schema` 中多处 ref kind 尚未同步 `result` 等正式结构 |
| 定点 SQL 反例 | 3 项均被附带 DDL 接受 | 初始 FINALIZED；FINALIZED 删除重建；无 review binding 的 BOUND pin |
| 包内 121 reference / 10 mutations | 作者报告，未在本轮重跑 | 本轮只做上述窄检查，不追加批量 reference 或回归 |
| SDK/Host/模型/原生 UI | NOT_RUN | 不能据本文关闭其验收门 |
| 全套独立实现评审 | NOT_RUN | 本文是接收方计划评审，不是未来实现的独立代码验收 |

ZIP SHA-256：`b5b4a924398a6963b326e8e23e555f95c8cc48918828fb36eb897db1a075144c`。  
主计划 SHA-256：`c7b38bd7f2e48c7ed50e448c5fc4d8d378b2783a59508db9627f88baeb71d875`。  
挑战报告 SHA-256：`e6f9870afda02aa692e2ffea6931f31d9af98b59917e7bc2b40940ef03c09366`。

原始局部探针和结果位于 Host `.local-test-evidence/2026-09-22/assurance-plan-review/`；不会提交原始证据。源码事实是本轮读取时点，候选仍 dirty，开发前需重新核对本次修订涉及文件，不要求回退到本文时点。

证据索引：上述目录 `evidence-index.json`，SHA-256 `e7e006454b2e57c1ef08c760d97fe64007c12b3416d167f68c77e1a79a89211e`。交接包另含 18 个文件的定点源码摘录，每个附完整文件 hash/原行号；这是局部读源证据，不是全仓 frozen snapshot。

## 3. 开工前需关闭的合同问题

### F01 — 以当前源码重列“复用 / 修改 / 新增 / 外部前置”，不要重复实现已有链

位置：§0.2、§3、§13、§15、`source-map.seed.json`。

当前已经有 `scoped_content_review.py`、`scoped_composition_review.py`、`completion_status.py`、`completion_inputs.py`、`completion_support.py`、`root_review.py`、`composition_review.py`、`operation_proposal_review.py`。`scoped_content_review.read_task_content_projection` 已从真实 Spec/Scope/Result/Attempt 构造内容审阅；`ResolutionCommits.accept_review/commit_goal_resolution` 已接 OCC 核对。不能在这些之外另建平行 acceptance/review 主链。

SDK 已注册迁移 1–24；第 23 项为 method evaluations，第 24 项为 planning human requests。TaskGraph 隔离快照中使用的第 23 项不适用于这里。seed 的 S13 指向 `memory/source_dependencies.py`，该模块本身是 lineage/currentness 读取者，不是完整生命周期 writer 清单。

**请交付**：按六类 purpose 和 S01–S26 列实际入口、当前 producer、需要修改的 reader/writer、新增部分、所属库/事务、是否已验证。重点纳入现有 scoped/root/composition 路径、真实 terminal writer、Host route 和 backup/restore。将尚未实现与仅尚未核验分开；给出 OCC/HTN 与 Assurance/TaskGraph 共同修改文件的单一 owner/接入顺序。

**关闭判据**：不存在两个对同一逻辑审阅、完成或预算同时负责的 owner；不把 AST 存在当行为 PASS。普通路径映射留给本地 AS-0，接口语义和替换/保留方案在计划中定清。

### F02 — ref 白名单不能完整表达所要求的真实来源

位置：§4.1、§6、附录 C；`blob-pin-v1`、`review-binding-v1`、`review-record-binding-v1`。

计划要求 pin 的 `source_receipt_ref/last_receipt_ref` 指向真实 CommitReceipt，`budget_reservation_ref` 指向实际 reserve/账户关联，`reviewer_turn_ref` 指向真实 AgentTurn，`consumed_check_refs` 指向真实 checks；共同 kind 列表没有明确的 commit receipt、reservation、agent turn、check binding 类型，也没有逐字段指定如何安全映射到现有 kind。`check_requirements.any_check_sets` 的 CheckSpec 同样需要确定 resolver。笼统写 `execution` 或 `policy` 不能自动解决：必须给出原表、精确键、不可变 body、revision/hash 语义。

**请交付**：完整 `ref-resolution-map`，每个引用字段明确允许 kind、真实表/API、主键/版本、hash 覆盖、issuer/tenant/mission 关联、读取失败类型。可新增**内部** kind 或使用明确版本 adapter，不要求修改公开 TypedRef。可变预算行不能随意以 revision=0 冻结；应引用已持久 reserve 事实及责任关联。明确预算与审阅记录创建时点，避免引用本身尚未生成或循环 hash。

**关闭判据**：用真实形状为六类 purpose 各给一份可编码的正例；错种类、同 ID 异 body、已结算旧 reservation、错 collector/turn 均有负例。无需当场跑真实模型。

### F03 — 模型 evidence_ids 与精确版本引用缺少唯一映射合同

位置：§4.3、§6.4；`review-reply-v2.assessments[].evidence_ids`、review binding 的 `evidence_catalogue`、record binding 的 `exposed_evidence_refs`。

回复使用 `ev-1` 一类字符串，但目录保存的是 `{kind,pin}` 列表，没有冻结的 label→exact ref 映射规范。相同 ID 的不同 revision、不同 kind、追加取证与初始目录重号时，不能靠 `pin.id` 或数组位置猜。参考 parser 只核字符串在 visible set 中，不证明它对应哪个实际对象。

**请交付**：确定标签生成/作用域/排序/冲突处理，持久保存哪份映射及 hash，追加目录如何记录实际披露 turn/receipt，collector 如何只接受该 reviewer 已看到的精确版本，最终 Commit 如何恢复同一映射。若复用当前 ContextComposer 的现有机制，给出确切版本与 adapter。

**关闭判据**：同 ID 双版本、跨 kind、未披露 ID、追加证据重放/冲突、重启后旧 raw 再解析均可确定处理，且不修改原 Provider input bytes。

### F04 — SEMANTIC/CHECKED 与三值检查的生产合同仍缺一层

位置：§4.2、§5、§7，`review-binding.check_requirements`，`reference/semantics.py::review_can_accept`。

正文规定真实 assertion FAIL→FAIL、ERROR/缺少→UNKNOWN，SEMANTIC 无需 check；参考函数却接 `Mapping[str,bool]`，把模型 PASS 且非 True 的 check 全降为 UNKNOWN，不能表达真实 FAIL 与未运行的差别，也不接 mode/OR-of-AND check sets。纯 SEMANTIC 正例若无额外适配会被拒；填 True 才能过又容易制造“检查通过”的假事实。现有 Requirements 的 required evidence policy 与新增 mode/check groups 也没有完整转换规则。

**请交付**：批准后的逐准则 check policy 保存位置和 writer；旧 required_check_ids 的无损 adapter；检查组 AND/OR 与模型 grade/finding/mandatory 的完整真值表；返回有效 grade、成功 witness、原因和实际消费 receipts 的纯函数。SEMANTIC 应表示“检查门不适用”，而非制造 execution PASS。未知映射不得自动降为 SEMANTIC。

当前 `VerifierRouter.verify` 中除 Critic callback 外的若干层是本地执行/记录，不能假定每层天然都有 §7 要求的原 call-ledger execution receipt。必须分别说明 format/rule/引用检查/code_test 的真实来源：复用哪个受信 recorder 形成可验证 receipt，还是通过原 executor 派发；禁止仅因缺通用 execution_ref 而让所有既有必需检查永久不可导入。

**关闭判据**：纯语义正例、CHECKED FAIL/ERROR/缺失、两组替代 checks、另一 ANY 分支成功、mandatory blocker 的明确结果；参考 oracle 与正文一致。

### F05 — 六类 Review 的生产切换和原 dispatch/预算事务须具体落地

位置：§6、§13、§15。

当前 `RootReviewCoordinator.cut/record_review`、`Orchestrator._ask_root_reviewer/_collect_root_review` 已有真实路径。新 ensure/collector 不能只在旁边增加；旧路径若仍可直接生产 official 或采用结果，Assurance 约束会被旁路。当前 dispatch 的 `subject_id` 与 `creation_key` 各自 UNIQUE，不能把每一 round 都绑定到相同 Task subject 而不说明兼容方式。

**请交付**：六行切换表：触发事件→现有 coordinator→新/复用 package builder→typed owner/reserve API→dispatch kind/subject_id/creation_key/input_id→AgentBridge→collector→official→接受/返工。注明每个原 API 所属数据库/是否自己 commit；同 Store 原子性无法直接成立时，必须裁定使用已有 durable intent/import 协议，不能要求跨库原子提交。新 round、格式修复 turn、重送、合法返工的身份与累计预算要有互不混淆的状态表。

特别核对：当前 `_ask_root_reviewer` 格式修复按 `package_id + ordinal` 创建新 service intent，而本包 binding 是一个 review_key 对一个唯一 dispatch_intent。必须选择并说明改成同 Agent 的后续 turn，或采用有明确父子关系的多 intent adapter，不能同时声称无损复用现状与严格一对一。该 reviewer 当前 `tool_names=()`；S11 所需追加取证必须给出真实只读工具装配及 receipt/曝光回流，不能只写“沿原 ToolGateway”。

**关闭判据**：无新增第二个调度器；原六类正式入口都有拦截；同 round 多事件不重预留；不同 round 不撞 subject 唯一约束；费用已落 execution 但回流拒绝仍正确导入。

### F06 — 根 Resolution 与最终 Mission 完成之间的唯一收尾 writer 没有完整接管表

位置：§9–11、§13。当前 `CommitService.judge_mission` 会写 `MissionStatus.COMPLETED`、释放 terminal pools、发 `MissionCompleted`；正文仅点名该文件的 `accept_result`，不足以说明旧终态路径如何迁移。

**请交付**：列全部 Mission/Task/Obligation terminal writers、调用者和本 profile 下的行为。明确 root Resolution 已保存但 closeout DRAINING 时 Task/Obligation/Mission 各处于什么状态，哪种预算保留，调度/no-progress 怎么识别等待；旧 judge 不能提前释放未知责任。`try_finalize_assured_mission` 复用的真实最后写函数、同事务事件/receipt/通知机制必须具名。

通知方案应说明当前是否真的有持久 outbox：有则列现有接口，没有则选择复用哪个 durable dispatch/事件机制，不得仅写“原 outbox”。区分重复 enqueue、可能重复发送、发送回执和用户已读，不能承诺外部通知与 SQLite 恰好一次。

**关闭判据**：直调旧 terminal 入口、root 已满足但弃用分支 UNKNOWN、hold 未结算、root→closeout 间崩溃、最终响应丢失均有不误完成/不误释放的确定结果。

### F07 — 恢复旧备份之后，最新撤回/ACL 水位从哪里取得尚未定义

位置：§11.4、附录 A.2、V13。

若 t0 备份完成、t1 撤权或删除、t2 将全部本地状态还原到 t0，仅从这个旧备份不可能知道 t1。当前 `storage/offline_backup.py::restore_offline` 的合同是离线还原到新目录、不外查；V13 只写“先恢复水位”，没有备份之外的可信来源、anti-rollback 机制或恢复重新授权流程。

**请裁定一种可实现方案**：备份外单调撤回日志/恢复权威，或对旧恢复根强制隔离且重获当前授权后才可披露，或明确一个有充分证据的适用边界。不能让备份自己证明它包含最新撤权。具体说明第一启动、所有下载/全文/summary/Context/通知入口的恢复门，以及无外部权威时可用的非披露诊断路径。若要扩新增持久面，明确范围，不伪称已有能力。

**关闭判据**：t0→t1→t2 真时间线、原库丢失、watermark 缺失、只恢复一部分库、离线恢复/重新确认均有可执行预期；不靠直接给测试塞一个“最新 watermark”作为生产来源。

### F08 — 失效屏障需要穷尽实际 writer；证书读集和时间唤醒需要可计算定义

位置：§8、附录 A.3、S13–S18、V07–V14。

目前 `memory/source_dependencies.py` 是 reader；真正写入包括 `Store.put_source`、`HtnStore.insert_observation/insert_justification_set`、policy/requirements 等入口。文中要求同事务 bump，但没有明确每个真实 writer 与 mission 分区的对应；涉及共享 policy/ACL 或外部 execution import 时，不能仅异步发事件才宣称同步失效。

**请交付**：writer→所写库→影响 Mission 集合→epoch 更新/receipt→绕过入口封堵表；跨库/外部事实以哪个已导入事实为本机线性化边界。规定 read_set 的 channel/key 编码、query set 范围/版本、canonical 排序去重、predicate+typed args+scope 标识、完整性证明和相关集合 fingerprint 的生成。若仅以全 Mission epoch 代替逐集合锁内扫描，写清变更后的重算路径与 liveness。

补充 `earliest_expiry/MAINTAIN` 的实际定时 owner、持久唤醒/重启扫描和时钟回退规则；没有连续维护来源时具体阻断哪些用途。给出计算工作/时间上限和反复 RECHECK_REQUIRED 的有界重排规则，不在写锁内重复扫描大图。

**关闭判据**：全量无缓存算法是比较基准；每类 writer 的新反证/撤权立即挡住下一消费者；时效到期无需等另一个业务事件才纠正可用投影；不承诺预见未观察到的远端变化。

### F09 — event cursor 原子性与事务外 Review/Validity 工作之间需要明确阶段协议

位置：§6.2–6.3、§8、§11.3、C03–C04。

文中同时要求“处理效果/receipt/cursor 同事务”和“CAS、checker、closure 在事务外”。这是可实现的，但必须规定：读事件后做准备时 cursor 是否推进；尚无预算/check 未完成/RECHECK_REQUIRED 时持久等待写到哪；准备后事件/源变化如何回滚；遇到长期阻塞事件是否卡住同 Mission 后续 source/operation 事件。仅写“durable 等待或 startup pending 扫描”仍是两个未选方案。

**请交付**：每个 consumer 的 read/prepare/commit/ack 顺序和 durable pending owner；正常/可重试/永久拒绝/无关事件的 cursor 规则；初始化 cursor=0 或 activation seq 的政策与 reconciliation scan；丢失游标重建依据。重放已有 command receipt 时仍必须允许安全推进该 consumer cursor，不能直接 early-return 导致死循环。

**关闭判据**：长时间预算阻塞期间后续反证/取消/完成事件仍能处理；两个消费者竞争、准备阶段退出、已提交但 ACK 丢失、self-generated events 不会漏工作或无限新建事件。

## 4. 同轮必须修正的交付与验收安排

### F10 — 完成后默认开启，以及 legacy/new lane 的权威判别

位置：§12.3、§18、附录 A.1。

项目当前用户规则是“完整实现、验收通过的能力同交付默认 ON”，不是做完再等待一次额外操作人批准。§18 的 `PRODUCTION_ALLOWED=...明确操作人启用` 不能被执行为永久 OFF/灰度。这不授权本轮开工，也不解除 HTN/TaskGraph 的未完成门。

同时 `assurance_mission_bindings` 缺行既可能是合法 legacy，也可能是新 lane 丢绑定。必须由独立、持久、可信的创建协议/profile 标记区分。当前 `uses_completion_protocol` 按 `mission_planning_protocols=planning-decision-v1` 分流，需明示其与 Assurance profile 的对应，不能 `if no binding: legacy`。

**修订交付**：factory/default policy 的具体写入口；新 Mission 自动绑定真实系统激活 receipt；既有运行 Mission 不静默改协议；旧库升级/新 lane missing binding/显式后继的分流表。default ON 发生在完整验收后，不是现在提前开半成品。

### F11 — DDL 的初始状态与终态不可逆约束不足

定位：`sql/assurance_additive.sql::assurance_closeout_*`、`assurance_blob_pin_*`。

本轮使用包内 parent fixture 的三个小反例，确认附带 DDL 允许：

1. 没有 READY 行，直接 INSERT closeout FINALIZED；
2. READY→FINALIZED 后 DELETE，再 INSERT NOT_READY；
3. 没有相应 review binding，直接 INSERT BOUND pin。

这仅证明 DDL 防线缺口，**不宣称未来 Store 必然有漏洞或当前产品已被绕过**。但“只有 READY→FINALIZED”“FINALIZED 不回退”和持久 pin 绑定都是计划承诺，不能让参考 SQL 自测错过。

**修订交付**：定义初始可写状态；补应由数据库承担的 insert/delete/immutable guards，及 BOUND 与真实 review 的同 Mission 关联；或明确哪些约束由唯一 Store writer 承担并将其写成必测合同，避免文档声称数据库已经保证。支持合法备份恢复/迁移时说明使用原事务的顺序，不能为测试删除约束。增加这些绕过反例及定点 mutation。

### F12 — 生成资产漂移、codec 一致性与校验工具盲区

定位：`field-producers.json` 的 15 个 `nested_schema` 与正式 schema 不一致；当前 check_plan 仍 PASS。

**修订交付**：字段源表由同一 schema 源生成，或取消重复嵌套 schema、改成 JSON pointer；校验工具逐项核对，而不止数 9/26/48/66。再核所有 schema→producer→SQL projection→codec→fixture。包括重复逻辑 key（同 channel/key 异 fingerprint）、unknown kind、状态组合、空 authors、收包理由长度等语义层拒绝。

边界需解释：JSON 最大 depth16 与 formula depth16 是不同深度计算；schema 的节点/数组上限、256KiB 文档上限、read_set 20000 的容量不能同时被误读为无条件可达。定义最先触达限制的具名结果，禁止截断为 COMPLETE。`review_can_accept` 与 schema/parser 差异按 F04 一并修。

**关闭判据**：向嵌套 enum 人为加入/删去一种 kind 或改字段时，包的结构一致性检查会失败；有效边界和越界的预期一致。不靠重跑 121 个既有参考用例声称这些已覆盖。

### F13 — Host wire、候选载入和原生验收没有闭合的实施路径

位置：§13–14、AS-4、C07/C08。当前 Host 使用下划线 verb（如 `mission_operation_intent_submit`）；正文提出点号 verb `mission.assurance.snapshot`，需给出明确映射。Host pyproject 当前固定 vendored wheel，并不会因 SDK candidate 改动自动加载新代码。

**修订交付**：三个读接口的版本化 request/response/error DTO；精确 Host 文件/函数、认证绑定、事件订阅和 UI 状态入口；每个字段的 history/current 和分页/超限处理。当前可定位的入口是 `backend/deskpet/orchestration/{handlers,service,projection}.py`、`backend/deskpet/sdk_adapters/sdk_candidate.py` 和 `tauri-app/src/views/MissionsView.tsx`，需按实际调用边收窄。

给出不破坏共享 Host 的候选原生验收方式：隔离 Host/venv/userdata/端口与候选指纹绑定，或在现有权限边界下合法更新制品的明确阶段；禁止一边禁止一切装载变更，一边要求共享 Host 跑到新代码。Tauri 自管 backend/vite，不双起；需核真实 import/wheel/source identity。原生 UI 需实际点击/截图/日志关联，接口脚本不能替代。C07 当前 production_paths 全是 SDK 文件，须补真实 Host/前端路径与交互用例。

**关闭判据**：断线重连、stale、跨用户隔离、review 详情、pending effects、closeout、历史读取、原生冷恢复都有可以执行的步骤；版本相同但字节不同不得混用证据。

### F14 — 主体编码优先、架构回写与阶段门要按当前项目规则写入

位置：§15–18、§19。

“允许 micro-tests”本身合理，但没有写清用户硬约束：**主体功能及跨层接线完成前，不进行批量单元、全量或回归测试**。AS-1/AS-2 各自罗列十多项不能被执行成编码过程中反复跑整组攒 PASS。

**修订交付**：先冻结合同、集中完成主体及真实接线，期间仅针对明确阻塞做最小静态/定点检查；主体完成后统一 48 组、继承门、16 变异、状态化、原生/模型及必要回归。新增“BODY_WIRED”检查单列所有生产调用边及默认设置，不能按文件数量判完成。AS-1 依赖 AS-2 的何种具体接口要写明，不能暂以真假默认值宣布接受链完成。

DoD 必须包含同交付更新 `ARCHITECTURE/AGENT_ORCHESTRATION.md` 或对应新模块事实源、`PROJECT_STATUS.md`、入口/日期/证据索引；新增原始证据留 ignored，不写 STATUS 正文，不提交截图/log/receipt/db。当前评审不是功能测试完成，不以这份评审更新完成度。

### F15 — 旧要求继承与真实模型验收的判定标准不足以直接执行

位置：附录 B、sdk-cases C07/C08、§16–18。

保留原 66 项/OCC12 项文本值得保留，但当前映射自称“拟议”，X18 仅写 OPS 负责，尚无当前 owner、所需真实断言和执行门的闭合清单。C08 把 late accounting 与 4×3 真实模型混在一个 pytest 目标中，allowed_stubs 仍允许 provider transport，容易混淆软件机制测试与真实模型证据。12 局“全部记录”是证据要求，不是成功判据。

**修订交付**：原要求→本轮/既有 owner→精确行为断言→触发入口→验收阶段的追踪表。计划阶段允许 actual_nodeid=PENDING，但每条原 MUST 要有具体将验证的断言，不能仅映射组 ID。把 C08 拆成离线 late-accounting 机制与真实模型 scenario runner；写清四类场景固定输入/预期允许轨迹/最终 oracle、预算/最大 calls/tokens/time、重复与中止政策、失败如何保留和计算；确定 12 局究竟要求什么通过门槛。应用 profile 启动时读取当前用户批准值，不擅自改模型或替换失败样本。

独立最终 review 用实际可调用的独立审阅者，忠实标明作者自审、接收方计划评审、生产代码独立审查三者。不要声称本文已履行未来独立实现门。

## 5. 不必再让用户仲裁的普通实施选择

- 等价文件/函数命名、使用已有规范化类型、在真实当前迁移列表后选未占用号：本地 agent 决定并记录，不重开架构讨论。
- 新功能的 SDK tests 当前不存在、真实模型当前尚未运行，不构成“计划不能开发”的独立理由；这些本来就是实施后产物。
- 不为未访问到的 producer 填默认值；列真实工作项及其消费者边界，继续不依赖它的编码。
- 不因原作者没有独立子代理而要求推倒重写；本轮发现的具体问题关闭后再进入实现，最终独立代码审查仍保留。
- 无需开权限平台、重写 TaskGraph、改 Operation 业务身份或另造预算/效果账本。

## 6. 一次修订的应交付清单

1. `ASSURANCE-EXEC-1.1.zh-CN.md`：统一正文和附录，不留互相冲突的旧表述。
2. `RESPONSE-TO-REVIEW.md`：F01–F15 每项 RESOLVED / DISAGREED_WITH_EVIDENCE / 明确外部前置；附关闭理由与反例。
3. `integration-map`：六类 Review、全部 terminal writers、失效 writers、事件消费者、Host 的真实接线与单 owner。
4. `ref-resolution-map`、标签/曝光映射合同、typed check-policy/三值结果、legacy/new lane 判别、restore 权威来源与阶段事务图。
5. 同步后的 Schema/SQL/producer 表/reference/新增反例/变异/一致性校验器；完整 manifest/hash。
6. 继承要求断言映射、主体接线检查单、最终验收命令/场景入口与待实现项、候选原生 Host 装载方案、模型 oracle/预算、ARCHITECTURE DoD。

**下一轮开工判断**：上述语义方案确定、交付资产自洽、AS-0 能按当前源码找到或明确新增关键来源、生产主链没有未裁定的双 owner/跨库原子性/恢复权威问题，即可开始实现。无需把 SDK_VERIFIED、HOST_MODEL、最终代码独立审查提前做成“开工许可证”。局部已确定纯逻辑可以开发，但不应据此宣布整份 1.0 已可直接施工。

## 7. 复现本轮窄检查

使用 SDK 已有 `.venv/bin/python -B`。KIT 指原 ZIP 解压根，本轮没有改交付包：

```bash
"$PY" "$KIT/tools/verify_delivery.py" --root "$KIT"
"$PY" "$KIT/tools/check_plan.py" --root "$KIT"
"$PY" /Users/denny/projects/simple_harness/.local-test-evidence/2026-09-22/assurance-plan-review/review_probes.py
```

第三条只导入附带 SQL fixture helper、创建内存库并检查生成资产和六个指定源码 hash，不执行 unittest discovery、不打开任何产品数据库。结果只能用于本报告所述合同问题。交接包附同一脚本、文字证据摘要及定点源码摘录，planAgent 无须先索取整库。
