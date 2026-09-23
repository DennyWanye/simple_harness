# TaskGraph 整体执行记录

## 2026-09-22：运行中工作项保守门禁定点检查

候选 taskgraph.10 环境运行原 H1-H `test_h1h_retired_running_work.py`：`2 passed in 1.53s`。未过期和已过期的 foreign lease 都被完整纳入 RuntimeWorkSnapshot，PlanCommit 均拒绝为 `RUNNING_WORK_UNRESOLVED`；图版本、Attempt 状态、lease owner 和 lease expiry 均保持不变。该检查验证原收敛保护边界，不等于 TaskGraph 真实模型/在途 SDK 取消验收。


## 2026-09-22：Selection 公共提交定点核对

候选 taskgraph.10 环境运行单项 Selection 公共提交检查 `test_oc2_selection_winner_commits_preparation_not_effect_or_action`：`1 passed in 1.25s`。检查确认 Selection winner commit 保持 PREPARATION 语义，不发布 Action/Effect，不把候选材料直接当正式完成。该检查来自原 Selection/Completion 合同，尚未证明 TaskGraph 与真实模型/UI 的整体 Selection 场景；TaskGraph 联合 DATA+Selection 挂载仍需独立场景接线。


## 2026-09-22：正式 DATA 消费类型与非空输入接线诊断

候选 `0.13.0.dev20260922+taskgraph.10` 增加桌面部署的正式 `desktop.continue-delivery` primitive 类型，声明 `delivery` 输入端口；原 `desktop.prepare-delivery` 合同保持不变。隔离 Host 两节点脚本走通：上游接受 `delivery` 输出后，下游实际读取同一已验收文件，完成 Mission；TaskGraph Attempt 冻结 manifest 含 1 个 DATA binding，upstream 为正式 `NOTES.md` artifact。未声明的 `upstream.txt` 被正确排除，确认不会从 workspace overlay 猜测输入。

当前 wheel SHA-256 `7bc0fab6a1a3c90946b700d6c6b3e11d6356ba33dd309edf8bb2651cc009d164`；manifest `34b133affa5ccf18830bb10a079f7d9bc815e6479d22d954c4d9b476c84725fd`；596 包内文件匹配。SDK 补丁保持 `58440d069aed66e0d8c0e35d05ed3fe79ffc7964cf415ee182f450cc32e26083`；Host 补丁 `eb8ea902c9a237cc4d995c498c4cfefc505515bc0a438f3f927e55ae6e395cf3`。证据只在 ignored `candidates/taskgraph-10/{data-v2.log,data-result-v2.json,data-observed-v2.json,package-identity.json}`。这是 RoleScriptedProvider 的接线诊断，真实模型/UI及 Selection 独立验收仍未运行。

下一步继续核对 Selection 材料与 DATA 的联合挂载，以及运行中取消/UNKNOWN恢复；主体完成前不做大批量回归。整体仍 PARTIAL，TG-A–E OPEN。


## 2026-09-22：原 H4 修复进入 TaskGraph 收敛并原事务提交（局部接线验证）

候选 `0.13.0.dev20260922+taskgraph.9` 的隔离 Host 已走到真实验证失败 → 原 H4 repair request → 原方法库替代方法 → Planner REPLACE_METHOD → 持久 fence/收敛 READY → 同一原回复恢复 → 原 PlanCommit revision 2 与收敛 APPLIED。使用 SDK RoleScriptedProvider；这是具名的接线诊断，不是模型、取消在途调用、UNKNOWN、共享消费者或完整 TG-A–E 验收。旧方法失败为诊断预设，失败证据保留。

主体修正：原 H4 受影响复合目标进入方法可见性/适用性读取；preview/普通提交/solver 提交统一 TaskGraph 命令身份；候选完整消费原 binding_rewrites，fence 前复用原语义与已验收工作检查；保留全 Mission UNKNOWN 保守提交门禁。旧 HTN 非 TaskGraph 分支保持原提交身份。taskgraph.6 的完成后冷启动只读诊断亦通过：Attempt/intent/event/revision/input/用量条数不变，零新增 SDK 调用。

当前 wheel SHA-256 `7173e342f577a094d51b4517cacf6e3f75107c90e8551e87eeed3e2783b7bdba`；manifest `83152194773a151e62df07df1efca038e78f9d3e584f58d1fde01af79a696bd6`；596 包内文件匹配。源码补丁 SDK `58440d069aed66e0d8c0e35d05ed3fe79ffc7964cf415ee182f450cc32e26083`、Host `23533a82f91d2d32ae1e957169cf5f552dfa6b2750543564240da112943f11e1`。原始证据仅 `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-9/{repair.log,repair-result.json,repair-observed.json,package-identity.json}`，冷读证据在 taskgraph-6/cold-read-result.json。taskgraph.7/8 失败诊断保留。

继续集中完成非空 DATA/Selection、运行中取消与恢复等主体核对，之后才做正式验收；未跑大批量单元/回归，未替换共享 Host/HTN 源码、环境或 DB，定时任务保持删除。整体 PARTIAL，TG-A–E OPEN。


## 2026-09-22：原提交与单叶执行闭环已到达（具名局部验证）

候选 `0.13.0.dev20260922+taskgraph.6` 的隔离 Host 已实际走通：原方法准入/规划授权 → 原 PlanCommit 同事务 APPLIED、完整图记录与历史读回 → Attempt 冻结输入与物化回执 → Worker 原工具写文件/结果收集 → Critic 原内容审查与实际用量结算 → 根审查 → Mission COMPLETED → 完成后当前读图。使用 SDK RoleScriptedProvider，本地单叶 CONTENT_ONLY 接线检查；不是产品 E2E、真实模型、非空 DATA 或全部 TG-A–E 验收。

修正初始 compound 无实体 Task 时的完整语义绑定来源；修正 Critic 通过原 Attempt 关联 Task 账户的读取，有 grant 的原物理调用仍严格验证。工作区默认代码检查在首个诊断中发现未提供测试文件，后续探针通过原 workspace_seed 提供一个读取 NOTES.md 实际字节的检查；仅该工作区内 1 项，未跑 SDK/Host 批量单测或回归。旧 Critic fixture 没有按新 task_content_scope 返回 criterion id、根角色脚本缺失均已修正探针，保留失败证据，不记为产品缺陷。

最新 wheel SHA-256 `bad4f55555097b58268f968229cc605e10a8d6452be45ebc3fd1f007673ce156`；manifest `c9eb835cf429c5f23be329c11aebfe7b89daaf9d0d2ae45d99b928e089234890`；部署身份 `1f942a426638d2019901719802fbc4cc6c87efc6cdcbb64ec997a4d742d06f4f`，596 个包内文件与冻结清单一致。证据 `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-6/{dispatch-r4.log,dispatch-r4-result.json,dispatch-r4-observed.json,package-identity.json}`。SDK 补丁 `733543be78e42a2fb4fd40c716bbe562e7106dba3bd090b1e68f9bbdc4c87f1e`；Host 补丁 `535ea05891330c01e3fc37cab263751a87350b2bbc84658a3bf6f4c80ecb7e36`。

仍需非空 DATA/Selection、收敛/取消/UNKNOWN 与冷恢复接线核对，以及主体完成后的正式验收和原生 UI。共享 Host、HTN 源码、环境和 DB 未替换，旧候选和 manifest 保留；整体任务继续，TG-A–E 不因本局部结果关闭。


## 2026-09-22：实际授权与初始读图入口通过，继续原计划提交（整体未完成）

独立候选 `0.13.0.dev20260922+taskgraph.4` 已通过具名入口检查：实际 SDK 方法生成/准入 → 原 planning request → Host 原授权事务启用 TaskGraph → 当前初始图读取。使用 SDK RoleScriptedProvider，仅验证本地接线；真实模型与原生 UI 未运行，不能当作产品验收。595 个包内源码文件与冻结清单一致；wheel SHA-256 `b09725de105ea604c25dacc137dd489a44c9711de89072643be2e0d2dda0ca42`，manifest SHA-256 `55b5a59a36b710f8b76c8de46b46bd357ad931033a21c69d24ddaee641cddfc5`。本地证据：`.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-4/{entry.log,entry-result.json,package-identity.json}`；其中旧探针的 model_execution=NOT_RUN 指没有真实模型请求，实际存在 scripted SDK invocation。

本次修正原默认 workspace TargetRules 的读取、原 Commit 内 dispatcher 接线、完整 DATA workspace 输入冻结及原事务物化回执、初始 epoch 0 来源一致性，以及无 orchestrator grant 的原 SDK 用量导入。缺失用量/价格仍保留原预算；有 grant 的调用继续原严格核验。新输入绑定为 v2，保留 v1 读取；旧候选和 codec manifest 未覆盖。

继续提交时发现初始 compound 根已有原语义绑定但没有实体 Task，原 TaskGraph 预览只枚举实体 Task 而漏掉根。已统一为读取原 Mission 全部当前语义绑定，正在沿同一入口验证。原 PlanCommit/APPLIED/历史读取尚未通过；派发、收敛、恢复与 Host UI 仍待完成。主体阶段无批量单测/回归，无真实模型请求；共享 Host/HTN 环境、DB 与候选未改动，定时任务未重建。


**2026-09-22主体编码续写（未验收）**：集中补齐冻结输入/原workspace/ReviewPackage消费、原图pin与handoff校验、历史祖先链及完整revision事件、输入替换候选代次预测、本地运行账目完整读取、固定通知消费者与部署装配入口。独立Host已有四个只读verb、严格前端模型及MissionsView执行图；共享Host/HTN candidate/依赖/DB/服务未修改。仅必要的小范围mypy、语法与两个新装配模块import检查；没有pytest/vitest/批量单元/回归/模型调用。最新可审阅源码见 `BODY-WIP-SDK.patch`、`BODY-WIP-HOST.patch`，原字节和完整变更索引保留于ignored `complete-plan-prep/body-implementation`。

**当前依赖边界（2026-09-22刷新）**：只读检查HTN任务revision 6，最新turn `01a0c532-eb89-75a0-880e-304c03607818` 仍inProgress；共享PROJECT_STATUS最新记载V1.4验收修复中，真实效果验收/Host命令入口已有专项证据，但DeepSeek最小聊天503、真实模型/H6/H8及完整H1门禁未关闭，原生UI待验，明确未通知TaskGraph ready。TaskGraph完整ExecutionReadContext/PlanMutationReadContext、当前执行授权/可靠导入、真实preview/APPLIED/baseline/repair authority与最终部署仍未闭合；不中途采纳候选为最终交接。已补完整SDK effect观察、ORDER facts、不可变当前结构读取、保守重评估及Host stale，均为未验收WIP。完整TG-A至TG-E与42场景/原生UI等尚未运行，不沿用父检查点PASS。

**2026-09-22定时任务取消**：按用户明确要求，通过应用 automation_update 删除 `htn-taskgraph`，返回 `deleteStatus=deleted`。后续在当前会话继续集中开发，不再依靠定时唤醒，也不得自动重建该任务。整体授权、HTN 隔离及主体完成前禁批量测试的约束继续有效。

**2026-09-22恢复持续编码**：将“最终接入等待HTN”扩大为暂停独立编码属于执行判断错误，已纠正。续写持久收敛tick唤醒、baseline捕获事务/上下文证明绑定、原根目标历史结果读取、精确SDK取消/Action lookup以及物理调用/导入用量观察，并接固定装配。仅局部静态检查；无批量测试、无模型/服务/共享环境操作。最新源码补丁与未接H1端口见 `BODY-WORK-IN-PROGRESS.md`，整体门禁未关闭。

**2026-09-22集中编码调整**：用户确认当前由主代理集中完成主体功能及跨层接线；编码子代理已停止，不再拆分主体链路。教训是过早成组测试、频繁交接和接口返工拖慢主体开发，后续按真实功能链路推进。主体编码完成前禁止批量单元/回归/全量测试；现有 body-implementation 新代码为未完成接线的WIP，不能沿用父检查点156 PASS作为新代码验收。

**2026-09-22用户执行顺序覆盖：主体编码任务全部完成前禁止大规模回归。** 优先完成主体功能与接线，只允许定位具体实现问题必要的小检查；不再每补一个边界就跑整组156项、全量SDK/H1或大矩阵。原最终验收保留，延后到主体代码完成。该用户要求覆盖skill中的逐片广回归顺序，不降低授权/来源门禁。当前实现工作区切到 `complete-plan-prep/body-implementation/snapshot/sdk`，保留已冻结history-pins；本轮先推进真实历史Store及通知存储实现，不以测试数量表示进度。

<!-- plan-status: finalized (scope inherited from TG-EXEC-2.0 and TG-READY-1.2.1; execution authorized by user 2026-09-21) -->

用户本轮授权：完成当前整个 TaskGraph plan，持续处理直到完成。继承不干扰 HTN 测试/修复、HTN 完成后优先接入的边界。此标记表示目标和执行授权明确，不表示全部前置依赖已通过。

原始 MUST / 验收事实源保持在 V1.2.1 原包 inputs 的 TG-EXEC-2.0、业务 kit 的 implementation/ACCEPTANCE.zh-CN.md、42场景、两套各12变异与H1-H 36项；不另写缩减版AC。V1.2.1门禁限定现阶段为TG-A离线准备，不凭本记录打开TG-B。

执行方式：主任务推进独立迁移准备；gpt-5.6-terra只读核对全范围/验收映射；gpt-5.6-sol独立审查新增工具。两者不运行SDK suite、provider、服务或改HTN目录。模型遵循用户AGENTS约束。生产实施按FULL路径，尚未进入正式SDK实现/交付run；现阶段spike和诊断证据不可充作最终机器验收。

## 待办与当前基线

- [x] 原附件与本地派生工具身份分开，99工具测试通过；见 PARALLEL-PROGRESS。
- [x] 冻结源码/完整真实schema离线扩展8项通过，有界非空样本15项通过。
- [x] M07隔离迁移范围：两个legacy Mission/Task/结果/产物/事件/预算保持；后继追加两个真实HTN Plan Commit及replay。
- [x] M06隔离迁移范围：真实注册源码下DDL中、提交前、提交后三次SIGKILL恢复与重开。
- [x] 工具独立review与必要修复；102/102通过。
- [x] 全部MUST→TG-A/B/C/D/E→真实测试及Host/model矩阵映射；见SOURCE-AND-HOST-REVIEW，映射不是执行成绩。
- [ ] HTN最终完成交接、完整source-map、C01–C10行为和H1-H证明。
- [ ] 正式TG-A注册/验证、TG-B/C/D/E实现及独立review、42SDK case和12TG mutation、legacy/fullH1/stateful。
- [ ] Host/API/UI与指定模型验收、默认开启、制品身份、ARCHITECTURE回写、整体DoD。

当前绿色基线只是离线工具与迁移探针范围。HTN仍active/inProgress；实际H1-H覆盖不闭合，不运行全仓基线争用其资源。生产切片开工前对最终隔离基线补分片build/lint/test与真实入口smoke。

## 本轮先验检查目标

M07：通过实际Store/CommitService创建两条合法Mission→Task→Attempt→Result/Artifact链，保留预算预留/用量、事件、产物文件hash。升级前所有旧表的canonical行哈希与升级后相同（仅迁移登记允许精确追加一个descriptor），同时验证FK、旧checksum、schema、重开幂等。原文件/数据库不可修改。未覆盖的HTN特有非空组合明确保留。

M06：在新临时库调用实际Store runner；在TaskGraph DDL已部分执行但未提交时对子进程强退，重开观察旧schema/data完整恢复；成功提交后退出再开不得重复登记。只注入终止观察点，不修改冻结Store源码/DDL；子进程无模型、网络或共享数据库。

## TG-A 迁移技术项推进（尚非完整TG-A）

用户最新整体执行授权覆盖独立实施；不再把旧“本次prep不注册”当新授权的阻塞。技术条件与隔离边界仍保留，已独立审查：真实完整22项父schema、runner、原DDL、M06/M07通过后，在全新派生副本生成注册补丁，不回迁HTN candidate、不接Host、不启用kernel、不打开TG-B。

- 新工具独立review：2 P1（缺supporting被nullable吞掉、重叠被去重）和2 P2（alias escape、source variant遗漏）均修复；3个新增回归方法，102/102工具测试通过；复核无剩余正确性finding。旧包及报告保留。
- M07补齐两个Mission、Task、Attempt、Result、Artifact实体链、事件、预算预留/用量/一条结算。生产者是实际CommitService/Store，执行器receipt是离线fixture，无模型调用。实际业务状态ACTIVE/VERIFYING/SUBMITTED/PENDING明确记录，未称两Mission完成。`m07-run3/report.json` 14检查通过，producer/tool/source字节绑定。
- M06三次真实SIGKILL，覆盖DDL中、COMMIT前、COMMIT后。独立review发现post_commit缺完整schema断言后已修，加入known-good schema和真实DROP-trigger负控；`m06-run2/report.json`通过。只证明进程崩溃，不证明物理断电。该历史探针仍为process-local descriptor，最新注册测试重新生成自己的legacy baseline，不混用旧库。
- 独立注册副本：`.local-test-evidence/2026-09-21/taskgraph/complete-plan-prep/registered-tga/snapshot/sdk`。生产只有2个文件变化：schema.py import+append两行、taskgraph_schema.py保存原18626 bytes DDL。
- 测试直接使用派生源码真实MIGRATIONS→Store.open，没有运行时migration注入；旧基线由冻结旧SDK独立子进程产生。7项：append-only、新建/升级/全部旧行/产物/reopen、checksum/FK拒绝、3个SIGKILL恢复切点、丢trigger负控。
- run1：4 PASS/3 FAIL（比较两次独立升级时误要求新migration.applied_at相同）；修正为旧22行逐字不变、新行version/name/checksum精确、只允许新增行安装时间不同，重开仍比较全部行。run2：7 PASS；格式调整后run3：7 PASS/1.305s，三新文件Ruff PASS。错误与旧XML均保留。
- 最终variant：`43f397234b8979940429b0fa08afdc6e26dc32122089285d65069c079842227d`。base fingerprint未变。新schema只暂用23，最终HTN链变化必须重新编号/验证，不能把23当永久预约。
- 最终独立语义及身份审查gpt-5.6-sol通过：恰好4个changed paths（2生产+2测试），DDL与原件SHA一致，最终manifest与run3核对通过。
- 补丁：`TG-A-ISOLATED-REGISTRATION.patch`，SHA-256 `25aa583e18cf97dec89c753004bdfaacc6fd0c53535a00435bb1c385dfb92757`。XML SHA-256 `0b75db7881b9d9b9d8e737d7bc35192f22b8a575c53ca725a89d2c1b313e079a`。

状态：`REGISTERED_DERIVED_MIGRATION_TESTS_PASS_NOT_FULL_TG_A`。不能标完整TG-A、C06/C07全通过、SDK ready或Host ready。尚未提交/合并SDK，无产品完成声明；共享ARCHITECTURE未写入虚假完成度。进入下一独立TG-A技术项前继续核对实际codec与来源合同。

## 下一派生副本：codec与非空HTN迁移

位置：`.local-test-evidence/2026-09-21/taskgraph/complete-plan-prep/network-codec/snapshot/sdk`，从已验证registered-tga复制；前一最终variant保持不变。

- NetworkDocument使用原七种codec；`task_semantics` identity中binding_revision对应`TaskSemanticBindingV1.contract_revision`，原计划§6.4已有明确说明，HtnStore.put_task_semantics的SQL列和值再次印证。没有新增另一种storage revision。
- 新迁移夹具由旧22项SDK的独立进程、真实CommitService/grounder/compiler/HtnStore创建两Mission和两PlanCommit，持久化requirements/method/child/membership/DATA/budget/event/receipt。使用既有测试domain helper；无模型、无伪造APPLIED admission。
- 通过真实HtnStore再保存合同revision2并退役方法；fixture与父测试都显式读回revision1/2并存和RETIRED，升级前后验证。
- 升级由派生源码真实注册的23项Store runner执行。所有旧表canonical rows不变、原22migration行保持、9个TG扩展表为空、新建/升级schema一致、FK/integrity通过；升级后原命令重放同receipt且全库零写。
- 独立review发现一个P2（只写入高revision/RETIRED而未显式读回）已修。新增no-op retirement负控命中专用断言，review复核闭合。
- `network-codec/htn-run1.xml`：1 PASS，格式前历史收据；`htn-run2.xml`：2 PASS / 1.523s，含修复负控。新增两测试文件Ruff通过。完整新variant待codec一起冻结。

这里只增加M07非空HTN保存证据。业务42场景中的C07、H1-H、真实准入、Host、模型均未因此通过；九张TG表全空，不能声称TG pins/certificate/跨Mission约束已验。NetworkDocument与PreviewBinding codec分别并行实现，仍不改HTN candidate或Host。

## 2026-09-22：文档合同检查点与历史来源推进

- 独立 validation-env 使用固定且带 hashes 的 `validation-requirements.lock`（pytest/httpx/Ruff/mypy/jsonschema）。没有向HTN候选环境安装依赖；SDK原uv.lock未改。原候选环境缺jsonschema的收集失败保留，不用skip掩盖。
- NetworkDocument逐个调用七种原codec，严格canonical/hash/identity/lossless；对象8MiB、全文16MiB、0..JS安全整数、UTF-8、重复/缺端点负测。manifest v4锁定10个实际源码（含codec自身），旧v1/v2/v3保留，改变源字节即拒绝；当前仅支持v4，不冒称兼容旧实验格式。
- PreviewBinding固定12个显式COMPLETE source channel，pending/convergence集合必须提供；没有默认成功来源。合同校验不是来源真实性证明。
- 结构diff比较完整对象集合，按kind/identity给出增删改原hash；顶层roots等变化由完整文档hash体现，不创造Schema外字段。输入重新decode，跨Mission拒绝、容量超限拒绝，不读取runtime overlay。
- `network-codec/combined-run2`：131 PASS / 3.15s（preview77、原注册迁移7、非空HTN迁移2、network20、diff25）；Ruff与3个生产文件mypy通过。源码检查前后相同，1283文件冻结manifest与测试源hash逐个一致。
- 检查点variant `330349fc8bf0a01129cea30a28d8e6f82bad7e2045b29ec379f633c0457de537`；增量patch `TG-A-ISOLATED-CODECS.patch` SHA `6ad69c4bdbb30f137bea525c4be9e7ef7aeab3b419b508b9bb0d4b90639a6d74`，应用于前一registered-tga副本。XML SHA `0839e6839fddcaf2bde46eaf3009ee9875a866277a7f6f5d30621103fe7bf939`。代码和文字可审阅，原始XML/log/DB仍只在ignored evidence。
- 独立review：Network和Preview已无P0/P1/P2；diff后继发现1个P2（公开合同接受不同revision但相同全文hash），已在新的 `history-pins` 派生副本修正并加反例，旧检查点保持原样；后继整组复验待完成。因此131 PASS不是最终整个技术片review闭合。
- 历史来源真实Store初测：候选network的根binding含adopted_method_instance_id，而同revision持久binding该字段为None。不能把候选overlay当历史合同；准确文档取**指定revision**的真实binding，adoption保留独立显式集合，绝不取latest。新的source helper只校核原不可变来源，不宣称已验证TG record/certificate/policy/APPLIED。首轮3 FAIL/6 PASS保留；对应来源组复验9 PASS/0.98s，新增专用候选overlay反例待合并验证。

下一项：完成member/method/demand pin完整集合算法及实际Store来源检查的独立review、固定后继身份。HTN任务仍active/inProgress；只读可见的migration链仍止22，不视为最终基线。TG-A整体、TG-B/C/D/E、H1-H、Host/API/UI和模型验收均未关闭。

## 2026-09-22：pins与真实来源组件检查点

上述下一项已形成后继隔离实现，位置 `complete-plan-prep/history-pins/snapshot/sdk`；不覆盖前一冻结副本。

- member pins逐occurrence绑定原 `binding.content_hash()`；首次实现误用嵌套contract_hash已纠正，正向测试与真实task_semantics.content_hash逐行比较。method pins保留全部draft，采用只取document；demand逐adopted child slot，共享同producer不合并不同slot。
- 全量集合核对接受无序完整行集，拒绝缺/多/重复/错Mission/revision/hash；公开行类型拒绝bool整数等值、非法枚举、错误hash/UTF8和可变容器别名。原projection几何helper的obligation与grounding语义不同导致首轮失败，修成真实bound occurrence/obligation对应后复验；失败XML保留。
- source helper通过真实CommitService/grounder/HtnStore创建两Mission，精确读r1而r2和RETIRED共存；缺member、额外ORDER、缺/错binding、缺child、错requirements、跨Mission、candidate overlay均拒绝。新增双SQLite连接交错验证同一read_view内不会拼混两时点来源，下一读准确发现损坏。helper仍非完整TaskGraphStore.read_revision。
- 独立审查闭合pins/原来源组件问题；diff的不同revision同全文hash反例已修并通过。最新 `history-pins/combined-run2` 为 **156 PASS / 3.31s**：preview77/network20/diff26/pins13/原来源11/迁移7/非空HTN迁移2；Ruff、5个生产文件mypy通过；前后源码一致。
- variant `4415a4430ed5334115c0d7a1dad847d96d6d420111da871784ad701b522e8d0c`，1287文件。相对前一检查点仅6个path变化；原冻结基线与两个父检查点逐文件校验保持。
- 最新增量补丁 `TG-A-ISOLATED-HISTORY-SOURCES.patch` SHA `cfa6821ee602f23d74dd18caffd34916aff575e395c9ac22cddd59064c578a48`。在原capture上依序应用REGISTRATION、CODECS、HISTORY-SOURCES；不能直接覆盖仍dirty的HTN candidate。
- XML SHA `ed219f96f1b0b38677135636554732cfe86ed5cdf8af859509f8df854d344466`。本机索引 `history-pins/evidence-index.json` SHA `fc39757e2024daad2aace5296ccdeb414369520734a7181ac6e2efaee0cd7600`。源码、测试源码可审阅，原始证据不进入Git。
- 架构事实已回写 `ARCHITECTURE/TASKGRAPH.md`、index和PROJECT_STATUS，只记录隔离组件PASS与整体PARTIAL，未改HTN事实条目或宣称产品完工。

HTN最新只读核对：任务一轮completed/idle，但2026-09-22事实源仍明确V1.4未完成，Operation/完整H1等仍待，因此没有触发“最终HTN接入”。真实record/policy/certificate/APPLIED producer、完整read_revision、当前execution context、派发/收敛/Host与最终42场景仍未完成。既有每小时跟进保持；后续以最终HTN源码/验收交接为优先，重新capture并复验迁移号与codec manifest，不能把旧快照身份当最新通过。

补丁可复现性已验证：在新的ignored临时副本从原冻结基线依序应用3份patch，全部exit 0，重建后1287文件hash逐个等于最新variant。证据 `complete-plan-prep/patch-application-check/report.json`；这只证明补丁可重建，不代表已接入HTN最终代码。相关文档 `git diff --check` 通过，原始XML确认ignored。既有 `htn-taskgraph` 每小时自动续接已更新为整个plan目标和最新检查点，保持ACTIVE，无变化时静默；不创建重复自动化、不把它当本次实现完成。

## 2026-09-22：集中编码与原执行链接线（未验收）

主体编码改由主代理集中进行。历史Store、Attempt输入、通知、收敛、policy/source、离线replay与只读API已有隔离WIP；本轮进一步接入原PlanCommit/PlanningAdmission、Attempt创建及Provider/Tool/Action handoff。实际来源/APPLIED/baseline/执行授权适配仍待，不能因为挂上调用点就称链路已完成。详见 [BODY-WORK-IN-PROGRESS.md](BODY-WORK-IN-PROGRESS.md)。未运行批量测试、回归或全量检查；旧156 PASS仅为父检查点。HTN仍在进行中，无最终交接，保持隔离。

## 2026-09-22：继续集中实现，不以检查点结束任务

原H1 Commit已新增真实APPLIED writer；固定preview/source/participant管线、COMPILED恢复身份、同一Store冻结输入、Task终态代次证明及首次root seed路径已写入隔离副本。详见 [主体进度](BODY-WORK-IN-PROGRESS.md)。全部为未验收WIP；局部静态检查用于接线，未运行测试套件、真实模型或UI。当前执行policy/可靠runtime导入、精确共享impact/收敛接续、最终Host装配仍未闭合。

`htn-taskgraph` 已按用户直接指令删除，历史ACTIVE描述不再代表当前状态，不重建。HTN任务只读快照仍inProgress；当前PROJECT_STATUS明确H4主体编码缺口，未发最终ready交接。继续隔离编码，不改候选/环境/DB/Host，不发送协调消息。SDK补丁SHA-256 `4516f28f1829e747f70d4184043a21b6d16340d89f44769bc6c5219be6a57b54`；Host补丁SHA-256 `4f6d43ac44b5fbddf7ad67ffa8290551036a07855996ac734aa281b7bc97dd72`。旧156 PASS不能覆盖这些改动。


2026-09-22当前权限来源续写（未验收）：新增固定execution policy reader，从原持久版本、部署、角色权限交集、selection替代角色、profile及路由输入生成当前来源；不序列化Provider/凭据，不使用planning grant代替执行权限。固定装配外部来源缩为最终H1可靠runtime导入，原执行核验端口继续必填。仅两文件局部mypy通过，无pytest/vitest/批量测试/模型/UI。最新SDK源码补丁56文件 SHA-256 `6119806eb511216bb966d5bd24c1f2bc38c5e536d547dc96c32c0c3a4275cb31`，Host8文件身份不变。主体、最终HTN接入和整体验收均未完成；详见BODY-WORK-IN-PROGRESS.md。

## 2026-09-22：共享需求、收敛恢复与当前有效性（隔离源码，未验收）

- 共享保留按候选显式成员、剩余slot/root与原独立需求事件检查；独立授权缺少明确Task/occurrence身份时拒绝推断。新增SHARE_ACTIVE检查原生产者START、消费者及祖先方法的真实当前前提、所有相关ORDER和原结算证明，不把PLAN见证当START。
- 原collector保存COMPILED后由固定preview入口创建精确收敛job/fence；WAITING停止重复Planner调用。READY消费者读取原内容寻址reply并回到原collector重新检查、Commit，原COMPILED预览不覆盖，合法来源更新另留不可变事件。
- 固定convergence authority包装器复核原history、12来源、候选、结构预算、原规划授权、SDK工作/账目及目标身份；真实H1/operator authority仍必需，没有自行签发授权或quiescence。
- 原Attempt创建事务新增当前执行policy核验：普通/selection角色、实际权限交集、Task版本、model profile、provider admission与调用上限，原预算/物化/Action检查保留。
- REEVALUATE先调用原START/DATA见证生产者，再重算typed readiness并记录实际witness IDs；没有清空无关validity_dirty。TaskGraph输入解析读取实际epoch（含尚无witness的新scope）和本次评估时间，不继承安装时缓存的epoch/时间。
- 12来源捕获复用同次证据/方法读取；Obligation的真实消耗保留在budget来源重新检查，需求/生命周期/failure/fuel/shape仍参与严格需求身份比较，避免单纯结算被误作需求变化。

以上均为源码WIP，尚无功能验收。本轮仅对新增接口做小范围mypy和AST语法检查；没有pytest/vitest、批量单测/回归、模型或UI调用，旧156 PASS不适用于本工作区。

当前独立SDK补丁59文件 SHA-256 `e0783bf50a332927c3fa2d80d4b0b8ecd8782309d1d830ee06e82c892c993e32`；Host8文件 SHA-256 `4f6d43ac44b5fbddf7ad67ffa8290551036a07855996ac734aa281b7bc97dd72`。数量仅为恢复索引，不表示完成率。

尚未闭合：最终H1可靠runtime导入、原Admission/Commit的精确Operation与running-work范围、selection DATA/候选材料联合物化、真实启用/baseline/operator authority和SDK/Host实际安装。旧隔离H1的全Mission Operation门及退休方法running-work门仍存在；因此收敛恢复代码存在不等于真实在途修复已经可用。需在HTN稳定完成后优先接入并验证，不能用空reader或放宽门禁绕过。

HTN只读检查仍inProgress；最新事实已进入H6/H8等验收收尾并有新的真实Mission证据，但仍明确未通知TaskGraph ready。旧“H4主体未补齐/Provider失败”记录只代表历史。未改HTN candidate/环境/DB/Host，未派工或发消息。定时任务已删除且没有恢复。SOURCE_MAP_COMPLETE/H1_H_READY/TG_A_VALIDATED及整体验收保持OPEN。

## 2026-09-22：共享编译、精确收敛准入和Selection联合输入（未验收）

- 纯预览原本固定传入sharing=None；现从同次Store来源捕获原SharedGoalIndex，传入原grounder/compiler。准确引用唯一CURRENT Acceptance的合同版本/hash；没有合格原记录或多个接受记录时不猜选。新增共享slot在freeze与Commit重新核对真实索引、Acceptance及未变更的生产者/DATA。
- 预准入只允许TaskGraph已安装路径推迟running-work检查以生成候选；最终准入携带从完整before/candidate计算的范围。Commit仍重新读取全量原Operation/runtime来源、比较完整digest，并拒绝目标Operation未收敛；原participant必须持有READY job并重新核验完整物理/账目静止。无关兄弟工作不被伪装成空来源，旧模式保留原门禁。
- Operation范围使用原link中的producer_task_id/producer_htn_occurrence_id，核对持久列、原link_json/hash与完整Operation snapshot；不混用Operation occurrence与HTN occurrence。ORDER结算和收敛观察复用同一核验reader。旧服务rehandoff的完整历史导入仍须与最终H1对齐，来源不足继续拒绝。
- 每次Attempt未来handoff重新核对当前执行权限、原冻结manifest的精确Acceptance/输入版本、当前consumer见证与epoch；不会改成最新输入。收敛还处理真实相关service intent和创建Agent后尚未提交Turn的窗口，没有制造cancel_turn回执或推断未知账目为零。
- Selection综合Attempt分别读取原DATA manifest与原Selection receipt材料，创建、恢复、后续handoff都比较完整联合输入。原片段冻结器接受由Commit实际核验的(artifact ID,path,hash)集合，同一产物的两个不同合法mount不被字典覆盖；候选材料不会写成已接受DATA。

以上均为隔离源码WIP。只做为接线排除错误的每次1–3文件mypy、改动大文件AST及assembly import；无pytest/vitest/批量单测/回归/模型/UI，无Store/runtime启动。不是功能PASS，旧156 PASS不覆盖本次。当前SDK源码补丁63文件 SHA-256 `948c0685d80dc6e00c021a85c437f60dcb5845447b9e7491a94aeac92237a15a`，Host8文件SHA-256 `4f6d43ac44b5fbddf7ad67ffa8290551036a07855996ac734aa281b7bc97dd72`。

仍需完成最终H1稳定源码接入、完整可靠runtime/历史handoff导入、实际执行授权/物化核验端口、启用/baseline/operator真实来源，以及SDK/Host最终安装。共享完整输入版本与最终H1合同仍需对照，精确范围和Selection新接线尚未行为验收。主体完成后才进入42场景等最终统一验收。HTN最新只读快照仍inProgress，未有最终ready交接；未触碰候选/环境/DB/Host，未派工/消息/模型服务，已删除的定时任务不恢复。


2026-09-22同轮后续（未验收）：新增共享slot逐项核对原方法DATA声明与保留生产者的完整入边及policy，拒绝借旧边掩盖新slot漏声明输入。修复产品run-loop对持久WAITING/READY收敛job的空闲判断：上一通知ACK与下一次唤醒之间仍保持可推进工作；BLOCKED通知不获得自动重试。唤醒沿原scheduler tick，不是Codex定时任务。相关单/双文件静态检查通过，无行为测试。最终HTN仍无ready交接，当前补丁SHA以上方最新值为准。

## 2026-09-22：完整执行历史、物理结算与输入物化（源码WIP，未验收）

- 新增固定 runtime 来源读取器：在原 Store 与 SDK 只读快照中校验全部历史 rehandoff 的 Agent/Turn/input 身份、Provider grants/invocations、effects 和账目，不再只观察最新 Agent。原服务重新派发补记精确来源字段，历史导入不受事件分页截断影响。
- TaskGraph 终态和取消路径保留 UNKNOWN 费用占用。实际历史调用、工具效果及账目未结算时不得释放；取消每个实际历史 Turn 并保留原 SDK 回执，不把取消已受理当作物理停止。旧模式保持原清理规则。
- 固定执行前核验当前授权、原冻结输入、真实工具与容量；工作区校验完整 DATA/Selection/source/fragment 挂载冲突、字节与路径，恢复先查篡改再刷新保护文件。修正同 hash 产物物化时 artifact 与 producer 的对应关系。
- baseline 静止证明由真实启用事务、完整 runtime/Operation/账目读取生成，历史读取回查原 receipt/event。没有制造 APPLIED、部署验收或启用权限。共享工作读取原 Attempt/Acceptance 的冻结输入版本并核对当前合法使用证据。

本检查点SDK补丁70个源码文件 SHA-256 `f88a1db4205189c9067e4037870faccace291e005798d9bbb26ea0edf7bc1713`；Host仍8个源码文件 SHA-256 `4f6d43ac44b5fbddf7ad67ffa8290551036a07855996ac734aa281b7bc97dd72`。文件数仅用于源身份，不代表完成度。仅针对改动做1–3文件类型检查、AST与assembly import；未运行pytest/vitest/批量单测/回归/模型/UI，未启动Store/runtime。原156 PASS仅覆盖父检查点。

剩余：实际启用/部署/规划权限与operator来源、完整收敛权限接线、最终H1稳定源码对齐以及SDK/Host安装；主体完成后再统一验收42场景等门禁。HTN只读任务快照虽idle，架构事实仍V1.4 IN_PROGRESS且未通知TaskGraph ready，故未接入候选、未动环境/DB/Host、未派工/发消息。已删除的定时任务不恢复。所有正式门禁保持OPEN。

## 2026-09-22：Action独立结算、原收敛证明和操作者命令（源码WIP）

Action预算按原reservation_subject精确连接Operation和真实效果证明，不再误当Agent intent；物理效果或历史负证明不完整时先保存真实结果并保留账户，原accounting recovery在证明到齐后结算。局部工作来源同时包含Action账户，不能以Operation完成代替账目收敛。

收敛的begin/静止/已启动检查已改为固定实际来源实现：原规划授权、完整12来源、实际SDK取消控制命令、当前Action handoff对应的原对账事件或真实在途owner。TaskGraph诊断通知本身不作为完成依据。新增认证操作者服务，绑定真实enable签发者及tenant；放弃候选须旧图需求未变且物理/账目静止，原事务记录命令回执并重新评价；BLOCKED通知重试须原消息和当前来源可读，再以明确操作者命令做Q13 CAS。未注册模型工具，未新增Host写入口。

检查点SDK补丁73源码文件SHA-256 `9f0bcca3ccb23ffe2bd28019644cde20723267647697a34ed406c5d8f4158bae`，Host补丁未变。仅局部类型/语法和assembly import检查，无行为测试；不是TG-A或产品PASS。实际部署验收/启用policy authority、最终H1接入、Host安装及最终验收仍待，旧156 PASS不覆盖本次。

## 2026-09-22：固定启用policy来源（源码WIP，未启用）

固定装配已构造StoreTaskGraphPolicyAuthority：从原Store读取当前规划委派lineage、签发者/tenant/scope/TTL/原命令receipt hash，读取原Mission候选参数版本与实际缓存一致性，冻结安装的schema registry、TargetRules、部署policy和GraphStructureBudget。启用结果保存实际部署验收SourceRef，历史policy读取同样校验该引用格式；没有为任何Mission签发新委派或执行enable。

部署验收reader仍是必须从最终HTN交付安装的唯一具名验收来源端口；它必须核验实际源码/配置和原验收收据，不能返回自造PASS或只声称complete。最终H1-H尚未就绪，本轮未构造该事实、未安装共享Host。最新HTN只读快照为active/inProgress，架构仍明确未通知TaskGraph ready。

当前SDK补丁74源码文件SHA-256 `24b44bea7d8ac2167b28003a41160658449e38906f0606922b73e21603b89539`；Host8源码文件SHA-256 `4f6d43ac44b5fbddf7ad67ffa8290551036a07855996ac734aa281b7bc97dd72`。只做每次1–3文件mypy及改动AST/assembly import，没有批量测试、模型或UI。文件数与静态通过不表示主体或门禁完成。

2026-09-22当前源码检查点：SDK74文件补丁SHA-256 `756daf45a3cef552fcb2cd0a5423cd391f49db1e16d3bd1deb32c1e2f5fbbf78`；Host8文件补丁仍为`4f6d43ac44b5fbddf7ad67ffa8290551036a07855996ac734aa281b7bc97dd72`。新增[42组调用路径核对](BODY-CALL-PATH-AUDIT-2026-09-22.md)，全部行为NOT_RUN；超限错误改为BOUND_REACHED、Action原账户纳入局部工作。HTN只读任务revision11仍active/inProgress，无final ready；未接入/触碰HTN。当前剩余接入依赖：实际部署验收来源、稳定H1/迁移/codec对齐、最终SDK/Host安装。主体跨层完成审计之前不跑批量测试，正式TG-A–E均未关闭。

## 2026-09-22：Host生命周期接线及读取水位（源码WIP）

固定SDK装配自行创建同Store的TaskGraphStore，直接绑定认证Principal与原Mission tenant；当前读取由完整execution reader核验，删除外部history/read/current成功回调。唯一待最终交接落实的验收来源仍为deployment_acceptance，不能用空实现替代。

隔离Host service已在首次open与rebuild时安装所有TaskGraph协作者，close/重建失败清除旧引用；重建创建新Store绑定，不复用旧读写对象。内部enable_taskgraph_contract入口使用固定认证身份和原policy事务，成功后唤醒原driver/change pump；未注册IPC写verb或模型工具。服务可接收可信部署ports，但应用最外层wiring仍须在最终H1交接后绑定实际验收reader和冻结预算；当前未安装/启用运行Host。

前端reducer保留当前图/收敛读取的最高事件水位，跨历史切换不回退；新图使旧收敛结果stale，新收敛结果使旧当前图stale并清除旧why；认证/连接重置仍清空全部状态。行为验证NOT_RUN。

仅做SDK assembly单文件mypy、Host service AST、前端store单文件tsc，均通过；未跑批量单测/回归/模型/UI，未启动Store或runtime。当前SDK74文件补丁SHA-256 `11072a0eb430e42356c5dec020cc9d61fa9e2c6113f66f0206686e9d92473b4e`，Host8文件补丁SHA-256 `6a73bfa2f217e980e7d1723720c26e2755dd9442d509db48c1c50f1dfa990743`。完整字节在ignored body-implementation，文件数不是完成度。

HTN只读任务仍revision11 active/inProgress；当前共享架构记载H8 HTTP524/unknown保留及H6 cohort-4继续，仍明确未通知TaskGraph ready。未接入活动candidate，不改其依赖/DB/服务/共享Host，不派工/发消息。当前仍需最终H1源码/迁移/codec对齐、实际验收来源和最外层安装、主体完成审计及之后的全套验收。SOURCE_MAP_COMPLETE/H1_H_READY/TG_A_VALIDATED保持OPEN，定时任务未恢复。


## 2026-09-22：继续推进离线重放重建（源码WIP）

上轮保存局部源码检查点后主动结束，并非整体完成。用户持续推进授权仍有效，不将局部检查点作为整体停止条件；HTN缺最终交接只阻塞其依赖路径。

C04离线replay改为从已核验的不可变NetworkDocument调用版本化pins reducer，重新生成member/method/demand关联，不直接复制源库pins。纯图目标库补全member binding revision/hash，并参与读回projection hash；目标格式更新为taskgraph-offline-graph-replay-v2，不覆盖旧目标文件或codec manifest。仍明确RUNTIME_RESUME_NOT_AUTHORIZED，不把重放图当可恢复执行库。

仅改动文件mypy通过；没有重放真实/测试数据库、没有行为测试、没有批量回归。SDK74文件补丁SHA-256 `f5da43a45316f4208b46f156bd65ec4dfe508ccaff1e3871a67706324cea10bf`；Host保持上一检查点。HTN只读快照revision11仍active/inProgress，未接入或打扰其工作。实际最终部署/最外层接线及完整验收仍未完成。


## 2026-09-22：接入 HTN 最终核心交付（当前源码 WIP，未验收）

HTN 已正式交接 READY_FOR_TASKGRAPH_WIRING，旧“只能离线准备/仍等待 ready”仅为历史。交接范围为 V1.4 核心集成和原生完整效果闭环；语义回放仍 PARTIAL，H6/H8 大评测依用户要求移出阶段，不记作通过或重新启动。

当前工作区为 `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/working/{sdk,host}`。只读捕获当前 dirty SDK/Host，532 个 SDK 构建输入与原 manifest 核对，采集前后源 hash 不变；528 个 wheel 包内文件核对，版本改写有原 manifest 依据。原 HTN wheel 为 `0.13.0.dev20260922+htn.1`，SHA-256 `af9e273061ceeeb3204ccbb4e4d32965fb3bba568e6c7e63f1af1b18244b9800`。原始采集/合并清单位于该隔离目录。

完整 TaskGraph 增量已三方合并到该交付，8 个 SDK 冲突已处理；保留 H4 repair 分支、原 Action 完整负证明及单 Mission world。TaskGraph 迁移改为追加 25，保留 HTN 的 23/24；codec 新增 v5，未覆盖旧 manifest。固定装配、执行/预览/派发/baseline/恢复和 policy 读取改为解析目标 Mission dispatcher。完成判断接入正式内容/效果/scope，界面区分等待效果审查、需对账及真正完成，不以 Action.SUCCEEDED 或旧 Task.status 推断整体完成。

隔离 Host 已接默认可信部署 reader 和真实规划授权事务内启用。部署 reader 校验派生包完整源码身份及原 HTN 验收证据索引；派生 TaskGraph 验收状态仍 NOT_RUN。构建冻结脚本已准备但未执行，未制造 TaskGraph PASS 或用户授权。尚未构建/安装 TaskGraph wheel、迁移真实库或启动产品。只进行解除接线问题所需的局部类型/语法/导入检查，没有 pytest/vitest/大批量回归/模型/UI 验收。

剩余工作：最终源码跨层一致性审计、派生包冻结及安装、真实执行/收敛/恢复与 Host 界面验收。SOURCE_MAP_COMPLETE 尚待完整入口审计；H1_H_READY 采用本次具名核心接线范围 READY，原历史全门不关闭；TG_A_VALIDATED 和 TG-A–E 整体验收仍 OPEN。用户授权持续有效，不以局部检查点结束整体任务。不使用子代理或 plan-task；定时任务已删除，不重建。

当前 SDK 补丁 SHA-256 `92ef69db21950eef31c729abb56534d37110d2da37e7bb472f523638ae6a57c1`；Host 补丁 SHA-256 `bcb43062175618f814d3db152cdf6e24e1f4181159cf9b386ed59500e07a2c16`。对应 `HTN1-INTEGRATION-WIP-SDK.patch` / `HTN1-INTEGRATION-WIP-HOST.patch`，是源码身份而非完成度。

## 2026-09-22：独立安装包与入口接线继续推进（整体未完成）

补齐 HTN 完成来源进入 TaskGraph 预览/提交的 acceptances 通道：原 completion spec、scope、贡献、outcome binding 通过原 Store 身份校验读取，同一快照核对当前正式内容/效果状态和 dirty 记录。修正初始语义 compound 尚无实体 Task 时的预算读取：保留 Mission 账户，实体 Task 才要求对应 Task 账户；缺失 primitive 仍拒绝。相关每次1–2文件类型检查通过。

独立候选 `0.13.0.dev20260922+taskgraph.1` 已构建并安装到 `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-1/env`，当前 Host 环境未变。wheel SHA-256 `cbf114418258b347d57f085367d46179e11c929d16441734bfd9c0a34972ed45`；候选 manifest SHA-256 `a59ee79c4f6ebca62d5365a089fbb7d71514996c7abf29bee865363dff6b64d0`；部署身份 `7e9642cc1876323e367d5fde93ced3ef2fec3e8e0bb3a0641cd628c1f3a5c1f8`。594 个包内文件与冻结清单逐字节一致，安装位置和 package-owned reader 验证通过。构建时的2个版本文件改写单独记录，未覆盖旧 codec/候选。`taskgraph_acceptance=NOT_RUN`，不是功能验收通过。

安装入口检查首先发现隔离 Host 缺少 sdk_adapters；补充只读捕获712个Host源码/配置文件，采集前后hash一致，原已捕获文件无漂移。隔离 Host 已绑定新包的版本/hash/manifest、pyproject和lock，主Host未变。原 start 和新Mission创建已实际到达，未启用后台driver或调用真实Provider。入口探针先因漏传原 facade 必需的 request_id 被拒，补完成要求后遇到空方法库正常进入 synthesis，尚无可授权的 planning request；未伪造request/grant/APPLIED来绕过。该探针没有完成授权→读图，不计ENTRY_PASS或产品验收。保留 `entry.log`、`entry-r2.log`、`entry-r3.log` 与本地库；后续须沿真实方法产生/规划请求入口继续，不把探针前置不足误记成产品故障。

本轮没有批量单测、回归、H6/H8评测或真实模型/UI测试。仍待真实授权→原Plan Commit→派发/收敛/恢复→图视图全链验收及最终应用接入；未完成整体TaskGraph。局部检查点不撤销持续推进授权，定时任务没有重建。

最新源码补丁：SDK `5508e48e4c50cb463cc8b812b194a647e07052c63636659b527bd0100be04124`；Host `81b87195908bdee094431a6fe588e6a2cf2b50b1c572b36d63fa1737773698b5`。打包 provenance、依赖采集与原始入口证据保存在上述 ignored 目录。

## 2026-09-22：Operation 合同读取核对更正

原 `HtnStore.get_task_semantics` 缺失时通过 `_one` 抛出 `StoreConflict`，不会返回 None。先前判定存在 AttributeError 缺口有误，新增判空已撤销；该核对不记为功能修复或验收。原 `_close_attempt` 已对 TaskGraph 保留未结算预算并保留 SUBMITTED intent，取消顺序未改动。继续主体联合接线核对。


## 2026-09-22：DATA + Selection 联合入口脚本化切片通过（主体仍未验收）

候选 `0.13.0.dev20260922+taskgraph.10` 的隔离 Host/SDK 场景已完成 `SELECTION_DATA_ENTRY_PASS`。场景实际创建两个候选 worker 和两个 synthesis 流程，最终 synthesis 同时读取已接受 DATA manifest 与两个候选材料挂载；候选材料使用不同 hash/mount，未被误写为正式 DATA。最终 Mission 状态为 `COMPLETED`，`actual_workspace_verified=true`，冻结 DATA 数量为 1，冻结输入回读包含正式 `NOTES.md` 以及两个不同 candidate-inputs 内容。

证据只保存在 ignored 路径：
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-10/selection-data.log`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-10/selection-data-result.json`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-10/selection-data-observed.json`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-10/selection-data-userdata/`

这是 `SDK RoleScriptedProvider` 的真实隔离运行栈接线诊断，`real_model_execution=NOT_RUN`、原生 UI=`NOT_RUN`，不关闭 TG-A 或产品验收门禁。没有因此启动大批量测试；运行中 cancel/UNKNOWN recovery、完整 H1/部署验收 reader、最终 SDK/Host 安装和 TG-A–E 仍待主体编码收口后统一验收。候选 10 的 wheel/manifest/deployment identity 沿用本文件前述冻结值，未覆盖 codec manifest；当前隔离源码补丁 SDK=`4689867f01f1367cd5d1b27f37015922dd60e76d9bc40aebe12b0384f5006936`、Host=`a94f70ce244f878b17fd28b582e5fd9b44f921b348fa135fc6d973941600d413`。


## 2026-09-22：严格 DATA 物化器接入生产 workspace binder（候选 11，定向切片通过）

发现并修复了一个主体缺口：`artifacts.versioning.materialise_v2()` 虽已实现，但 TaskGraph 的 `_bind_workspace()` 仍把 DATA 当作普通 `inputs` 交给旧的 `WorkspaceManager.create()`；现在首次 TaskGraph workspace 构建改为由冻结 `InputManifest` 调用 `materialise_v2()`，恢复 ACTIVE workspace 仍先验证原文件，不覆盖篡改证据。非 TaskGraph Attempt 保持旧路径。

候选 `0.13.0.dev20260922+taskgraph.11` 重新运行同一隔离 DATA+Selection 场景，计数式探针记录 `materialise_v2_calls=6`，并得到 `SELECTION_DATA_ENTRY_PASS`：Mission=`COMPLETED`、`actual_workspace_verified=true`、冻结 DATA=1、两个候选材料均被 synthesis 读取。候选身份：wheel SHA-256 `56d05ae4136e7e4f689bfbc7972b16f4f712c7281c63efe4990f251dd7c80e61`；candidate manifest `a7c95d4015eee92a6096369c140292bd5c070bf63e7e7e9088dcec1899946a5a`；deployment id `652e674c3f889b959a81c9cf87f00d18fb3a6ff2bcccc7ded556c15b5d831374`；596 个包内文件匹配；`taskgraph_acceptance=NOT_RUN`。

证据只在 ignored 路径：
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-11/selection-data-v2.log`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-11/selection-data-result.json`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-11/selection-data-observed.json`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-11/selection-data-userdata-v2/`

该切片仍是 `SDK RoleScriptedProvider` 的隔离接线诊断，真实模型与原生 UI 均 `NOT_RUN`，不关闭 TG-A 或产品验收。源码检查只覆盖两处定向 `py_compile`；没有启动大批量测试。


## 2026-09-22：DATA 物化归属校验与候选 12 定向切片

在 TaskGraph `_bind_workspace()` 调用冻结 `InputManifest` 的 `materialise_v2()` 前，新增 fail-closed 身份核对：每个 manifest artifact 必须同时匹配当前 mission、manifest 声明的 producer task 以及冻结 `content_hash`；缺失、复用 ID、跨任务或内容漂移均拒绝物化。非 TaskGraph 路径不变，ACTIVE workspace 恢复仍先验证已有文件。

候选 `0.13.0.dev20260922+taskgraph.12` 已在隔离环境构建：wheel SHA-256 `d13e763f31b2b167ed38edac9aeeea3a3279cbfc2f894d35efd78b7ffe31db35`；candidate manifest SHA-256 `201164318407923121b7261cf44bed5b2eeae906f0496cd8a89f1e3b6430615e`；deployment id `a90d10767eaa47c205f3ade892d094137c4fcfb5edba908cce511289cdf1ba2e`；596 个包内文件匹配；`taskgraph_acceptance=NOT_RUN`。

同一 DATA+Selection 隔离窄探针通过：`SELECTION_DATA_ENTRY_PASS`、`mission_status=COMPLETED`、`actual_workspace_verified=true`、`materialise_v2_calls=6`、冻结 DATA=1；仍为 `SDK RoleScriptedProvider`，真实模型和原生 UI 均 `NOT_RUN`。证据只保存在 ignored 路径：
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-12/selection-data-v3.log`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-12/selection-data-result.json`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-12/selection-data-observed.json`
- `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-12/selection-data-userdata-v3/`

本轮仅运行 `event_handler.py` 定向 `py_compile` 和上述窄场景；没有启动 pytest/vitest、全量、回归、H6/H8、真实模型或 UI。当前隔离源码检查点：SDK 补丁 SHA-256 `414424eaafe841fb72dc1d6dd5a91090eda892bace8730a7b5b4b841a71113a6`；Host 补丁 SHA-256 `a94f70ce244f878b17fd28b582e5fd9b44f921b348fa135fc6d973941600d413`。这只是主体接线进展，TG-A 至 TG-E、H1 完整部署来源、最终安装和产品验收门禁仍保持 OPEN。


## 2026-09-22：候选 12 Host 身份回写

候选构建脚本为隔离 Host 写入 `taskgraph.12` 的 package identity 常量；重新保存后的当前源码检查点为 SDK 补丁 SHA-256 `414424eaafe841fb72dc1d6dd5a91090eda892bace8730a7b5b4b841a71113a6`、Host 补丁 SHA-256 `4428d2346d2bbc6b16858363fbe8bf6b891eb562a4e411fb2b5017ae8ede07ae`。该 Host 变化只反映候选身份，不改共享 Host，也不改变候选 12 的 `taskgraph_acceptance=NOT_RUN`。


## 2026-09-22：候选 12 部署 manifest 读取核对

在候选 12 环境直接调用 package-owned `InstalledHtnWiringAcceptance._read()`，成功核对部署身份 `a90d10767eaa47c205f3ade892d094137c4fcfb5edba908cce511289cdf1ba2e`、完整源码 inventory 与 `taskgraph_acceptance=NOT_RUN`。这只是安装包来源完整性检查，不是 H1 或 TaskGraph 验收 PASS；没有创建 Store、启用 policy 或启动产品。


## 2026-09-22：DATA 与 Overlay/Selection 分层物化（候选 13）

继续审计发现：`overlay_attempt_inputs()` 会在冻结 manifest DATA 之外加入已接受生产者文件和 Selection 材料；若 TaskGraph workspace binder 把全部 upstream 都交给严格 `materialise_v2()`，这些额外挂载会被丢失。现已修正 `_bind_workspace()`：先依据冻结 manifest 计算正式 DATA 路径，正式 DATA 仍只由 `materialise_v2()` 写入；其余已验证、同 mission/producer task/content hash 的 overlay/Selection artifact 通过受控 `WorkspaceManager.create(inputs=...)` 写入。所有 upstream 同时增加 mission 与 producer task 归属校验。

候选 `0.13.0.dev20260922+taskgraph.13` 已构建：wheel SHA-256 `a713bb2414c6d0960bf87bc05c48fa5b0be3ca03601e22bab6db5e59dce1d75b`；candidate manifest SHA-256 `354d3b44981a91c889a039459cbad27e58b4f24947f7045ab142389cd4327afa`；deployment id `0fb49b02a39ec7a30fc29333f09234d99b7a739b9e8ce6bbd23973af6e05b11b`；596 个包内文件匹配；`taskgraph_acceptance=NOT_RUN`。候选 13 的同一 DATA+Selection 隔离窄探针通过：`SELECTION_DATA_ENTRY_PASS`、`mission_status=COMPLETED`、`actual_workspace_verified=true`、`materialise_v2_calls=6`、冻结 DATA=1。

本轮只运行两文件 `py_compile`、Ruff `E9/F` 语法检查和该窄场景；真实模型、原生 UI、大批量回归仍未运行。当前源码检查点：SDK `38e1320652c0c8e005b82e5286d345bfa48c3c1b4858afe5cffa87dd01b46988`；Host `4428d2346d2bbc6b16858363fbe8bf6b891eb562a4e411fb2b5017ae8ede07ae`。证据位于 ignored 路径 `.../candidates/taskgraph-13/selection-data-v4.log`、`selection-data-result.json`、`selection-data-observed.json` 和 `selection-data-userdata-v4/`。


## 2026-09-22：候选 13 窄探针确认已启用 TaskGraph 路径

候选 13 的 `selection_data_probe.py` 通过 `service.planning_authorization()`；Host 现有授权入口在首次 active grant 后自动调用 `enable_taskgraph_contract()`。该隔离数据库的 `taskgraph_policy_bindings` 已记录 mission 与 `taskgraph-enable:<mission>` 收据，随后同一场景在 policy 已启用的 TaskGraph 路径完成 `SELECTION_DATA_ENTRY_PASS`。之后再次显式启用返回预期的 `TASKGRAPH_POLICY_ALREADY_BOUND`，说明一次性 CAS/幂等保护生效，不计作功能失败。


## 2026-09-22：候选 13 Host 身份回写（最终当前检查点）

候选 13 构建脚本将隔离 Host 的 SDK candidate 常量更新到 `taskgraph.13`；重新保存后的当前源码检查点为 SDK `38e1320652c0c8e005b82e5286d345bfa48c3c1b4858afe5cffa87dd01b46988`、Host `b5b79889da11f5f5b984223dd7ce8b0de3b87802e01ccf80c26fbee3aa797768`。Host 变化只属于隔离候选身份，未改共享 Host。


## 2026-09-22：分离规划授权与 TaskGraph 显式启用（隔离源码，未验收）

审计发现 Host `planning_authorization()` 在 active grant 后自动调用 `enable_taskgraph_contract()`，会把规划委派误当成 TaskGraph kernel 启用，并可能绕过计划要求的显式认证启用及已有 ACTIVE 图的 baseline/quiescence 门禁。已在隔离 Host 副本移除该自动旁路；规划授权现在只返回原始授权 receipt，TaskGraph 仍只能通过认证的内部 `enable_taskgraph_contract(mission_id, command_id)` 入口启用。

定向 `py_compile` 与 `git diff --check` 通过；未运行 pytest/vitest、批量回归、模型或 UI。新的隔离源码检查点：SDK 补丁 SHA-256 `38e1320652c0c8e005b82e5286d345bfa48c3c1b4858afe5cffa87dd01b46988`；Host 补丁 SHA-256 `18efd001f70ed60f049c329eb1b8aab03310366724cfb3430445d80063e7c6e7`。候选 13 的历史窄探针仍保留为旧自动启用行为的证据，不能继承为当前源码验收。


## 2026-09-22：显式启用修复后的候选 14 冻结

隔离候选 14 已重新构建以包含“规划授权不自动启用 TaskGraph”的修复：版本 `0.13.0.dev20260922+taskgraph.14`，wheel SHA-256 `d63744850fd5a0a08f347f86fea061cadf6b5369804b6cad1824839cf2a30bc1`，candidate manifest SHA-256 `fddb04b082bc0e9b03831eda1ce1c50317c1aa5023bdf0b3746a0b614e15cee2`，deployment id `54eb14a6d7cf43a3ffa61fda4f0870bbe8f19de6a20c1889ce1601d0ff33b289`，596 个包文件匹配；TaskGraph acceptance 仍为 `NOT_RUN`。候选构建同时把隔离 Host 的 SDK identity 更新为候选 14。当前源码检查点：SDK `38e1320652c0c8e005b82e5286d345bfa48c3c1b4858afe5cffa87dd01b46988`；Host `7ebf681e2674b96a867c3177a617accf4c590bafdd9af27a7fc11ca673a9cb78`。未运行批量测试、模型或 UI。

## 2026-09-23：最终 HTN 接入与候选 15 冻结（主体接线检查点）

HTN 最终交接已在独立只读捕获中核对为 `READY_FOR_TASKGRAPH_WIRING`：SDK 源码 `/Users/denny/projects/simple-harness-sdk-h1h-impl`，HEAD `102ad3dfa2db38d575ea929d39ec5ed1561a71da`；Host 源码 `/Users/denny/projects/simple_harness`，HEAD `6c457908e49757035c21c6dc2b252415107a494c`。最终捕获清单为 SDK 1404 个文件、Host 285 个文件；HTN wheel `0.13.0.dev20260922+htn.1` 的 528 个包文件与 dirty SDK 源码逐项匹配，wheel SHA-256=`af9e273061ceeeb3204ccbb4e4d32965fb3bba568e6c7e63f1af1b18244b9800`，HTN manifest SHA-256=`f77d817d265053e89711643310483be2687bd8b2f308c454ab980a89d6207809`。HTN 自身语义回放保持 `PARTIAL`（23 个未知事件类型、1 个未覆盖字段、UI ledger 未对齐）；这不被 TaskGraph 误报为全门通过。

TaskGraph WIP 已在独立副本完成三方合并：SDK 92 个文件、补丁 SHA-256=`38e1320652c0c8e005b82e5286d345bfa48c3c1b4858afe5cffa87dd01b46988`；Host 12 个文件、当前补丁 SHA-256=`0d9f82f2c46432badf659a5a1b31a5a9d5ea646159a74b6196978c1d919f4b7b`；无冲突。最终 HTN 接口定向 `py_compile` 已覆盖完成效果读取、操作完成读取、运行中工作、计划提交、层级派发、TaskGraph 运行时/来源等入口；Host 关键入口亦通过定向 `py_compile`。Host `planning_authorization()` 已移除自动启用 TaskGraph 的旁路，显式认证的 `enable_taskgraph_contract()` 保持独立，避免规划授权绕过 baseline/quiescence 门禁。

候选 15 已在该最终 HTN 接线副本构建并锁定：版本 `0.13.0.dev20260922+taskgraph.15`；wheel SHA-256=`f7470f35708187f298da94c06774e12ebb41bcb04f6570c34de791bc17dba5d5`；candidate manifest SHA-256=`a54beaabc1acaf417e55dbf2f14e536658a9db057efadd06eb97bc0d8fdbecae`；deployment id=`30d2a234fd058a996c659cd97454c91b4864dcf564f6d81c6d72748e743b3b92`；596 个包文件匹配。随后从候选环境直接调用 package-owned `InstalledHtnWiringAcceptance._read()` 成功返回上述部署身份、HTN 来源与 `taskgraph_acceptance=NOT_RUN`；该读回只证明来源/身份一致，不是 TaskGraph/H1 验收 PASS。窄读回记录在 ignored `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-15/package-acceptance-read.json`。

本轮仍未启动 pytest/vitest、全量/回归、TG-A 至 TG-E、H6/H8、真实模型或原生 UI；共享 Host、HTN 工作树、依赖、DB 和定时任务均未修改。主体跨层接线和最终接口审计继续进行，SOURCE_MAP_COMPLETE、TG_A_VALIDATED、H1 完整部署来源及产品验收门禁保持 OPEN。
## 2026-09-23：候选 15 最终 HTN 接线窄场景通过

在最终 HTN 源码与 TaskGraph WIP 的独立合并副本上，用完整 Host 包装副本（共享 Host 只读复制，候选 15 的 12 个 Host 改动和 vendor 身份覆盖）运行单一 `DATA+Selection` 接线探针。显式流程为：真实 Mission 创建 → Completion Spec → 原方法合成/规划请求 → `planning_authorization()` → 显式 `enable_taskgraph_contract()` → 原 PlanCommit/revision → 两个 Worker 产出 → Selection synthesizer 实际读取两个候选 → Critic/root review → Mission `COMPLETED`。结果为 `SELECTION_DATA_ENTRY_PASS`，`actual_workspace_verified=true`，`materialise_v2_calls=6`，冻结 DATA=1，6 个 Attempt，真实模型和 UI 均 `NOT_RUN`。候选 15 的 package-owned deployment manifest 仍报告 `taskgraph_acceptance=NOT_RUN`；本探针是主体接线的隔离脚本证据，不提升 TG-A/H1/产品验收门禁。

原始结果仅保存在 ignored `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-15/full-host/selection-data-{observed,result}.json` 及同目录日志；未修改共享 Host、HTN、依赖、DB 或定时任务。
## 2026-09-23：显式启用门禁窄核对

在同一完整隔离 Host 包装副本的独立数据库中，单场景探针在 `planning_authorization()` 返回 active 后先查询 `taskgraph_policy_bindings`，绑定数为 `0`；随后仅通过认证内部 `enable_taskgraph_contract()` 写入绑定并完成同一 DATA+Selection 流程。结果仍为 `SELECTION_DATA_ENTRY_PASS`、Mission `COMPLETED`。这确认规划授权与 TaskGraph kernel 启用已经分离；没有把 planning grant 当作 TaskGraph 已启用，也没有改变共享 Host。
## 2026-09-23：提前执行的定向用例（不证明主体完成）

主体完整性尚未确认时，提前在最终 HTN+TaskGraph 独立 SDK 副本使用其冻结依赖运行 10 个直接相关现有测试文件（wiring、operation admission/current/live/tenant、retired running/unknown action、H1-I production entry、terminal UNKNOWN release、input manifest resolution）。结果 `164 passed in 11.52s`，JUnit 原始文件仅保留在 ignored `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-15/targeted-htn-taskgraph.junit.xml`。这不是 42 组 TaskGraph 验收、stateful、真实模型或 Host/UI 全门；全量回归仍未启动。
## 2026-09-23：回归与证据记录更正

在运行中取消、UNKNOWN 恢复及冷恢复链路仍需核对时启动 `tests/orchestrator/full_target`，不符合用户“主体编码全部完成后才允许大规模回归”的要求；已停止扩大测试，返回主体实现。两次 full_target 均未完成，多个失败尚未归因。已定位的一项 H1-I 恢复失败及后续报告错误包含 ENOSPC；该单文件随后 5 PASS，不能据此将所有失败归因于磁盘或宣布整轮通过。10 文件 164 PASS 仅保留为当时的局部结果。

清理时误删了本轮 candidate15 下 pytest-tmp、full-host/explicit-enable-userdata-v2、explicit-enable-userdata、selection-data-userdata-v5（含原始数据库），违反证据保留要求；之前“未删除原始 TaskGraph 证据”的记录不实，现予更正。已保留的日志和结果 JSON 不等同于可冷恢复的数据库证据；相关场景数据库复核/冷恢复记 NOT_COVERED。停止进一步删除，旧候选、源码与共享 HTN/Host 数据未在该清理中删除。完整验收、42 场景和原生 UI 仍 OPEN。


## 2026-09-23：补齐状态变化通知及超时撤权（候选 16）

主代理继续在最终 HTN 隔离副本编码，未启动大规模回归、未修改共享 Host/HTN 或重建自动化。

- `storage/taskgraph_source_events.py` 新增真实来源写入的持久变化引用；原 `HtnStore` observation/witness/epoch 写入、`PlanningAdmissionStore.put_grant` 与引用同事务提交。只对已经显式启用 TaskGraph 的 Mission 写新事件，旧模式不追加。通知不授予规划或执行权限，消费者仍重新读取原来源。
- `taskgraph_notifications.py` 补入审批请求/批准/拒绝/撤回/过期/取消/替代事件及上述来源变化；源事件字段损坏拒绝消费，不把缺来源当空。
- `CommitService.mark_attempt_lost/mark_attempt_timed_out` 修正先结算再取消的顺序：TaskGraph 保留预算占用并保存 LOST/TIMED_OUT，后续原取消与 late-accounting 读取真实物理回执再结算。原先物理未结束时 settlement guard 会拒绝整笔超时事务，后续取消无法到达。旧模式保持原结算规则。

定点证据（脚本 Provider，非真实模型或原生 UI）：

1. 候选 15 在途取消：真实 SDK 请求被 gate 保持在途；原 H4 合法替换创建围栏并取消目标，调用未返回时 reservation=RESERVED/revision=1；真实响应及用量返回后同一 Planner 回复提交 revision=2/APPLIED。证据 `candidates/taskgraph-15/inflight-cancel/`。
2. 候选 16 活跃超时：上述链路改由原超时 writer 先撤执行权，确认 TIMED_OUT 事件一次、在途时 RESERVED，响应后 SETTLED 与 revision=2/APPLIED。探针最后错误要求无证据前提的场景产生 witness，原日志保留为 harness failure；独立只读数据库复核确认 timeout 主路径，witness 通知覆盖保持 NOT_COVERED。结果 `candidates/taskgraph-16/timeout-cancel/timeout-path-readback.json` SHA-256 `74d9d5947cabbf1acc021ebc504ed2c02ae103e16416ca3471278d6109900f5e`。
3. 候选 16 UNKNOWN：慢请求返回 handoff 后未知结果，原循环在 45 秒探针时限到达时仍正确 WAITING；未记该限时探针整体 PASS。随后原 Host 冷启动并推进两次原 cycle，2 Attempt/5 intent/1 revision/5 physical call/1 UNKNOWN 前后不变，零新 Provider 调用，预算 RESERVED，围栏仍 WAITING。`candidates/taskgraph-16/unknown-cancel/cold-read-result.json` SHA-256 `e00fa7192e6d8dbbf9ae4c94ca267c644ec704098f986614e68d81dfcbd4090e`。该结果不证明 UNKNOWN 已解决，也不替代 Operation UNKNOWN 或强杀窗口完整验收。

上述相对证据路径均位于 ignored `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/`，本轮新增原始 DB/日志均保留。5 个修改模块通过语法检查。新冻结版本 `0.13.0.dev20260922+taskgraph.16`，597 包文件匹配；wheel SHA-256 `0254d6cde402fdd91e54108bc8d2d36573cdfd511a2037c4ded6fe650d25eb04`，manifest SHA-256 `94f4191c43dd2e779e022acde7c90b4af3495ab8ee06b422a2b238832c4ecbbe`，deployment `32ebc086348e6f6b70b6f6848aa47086f941cb83da0882342644482a950b9455`，TaskGraph acceptance 仍 NOT_RUN。当前可审阅补丁 SDK 95 文件 SHA-256 `602a721723a0ecccf814967bc3afe052040be383590c7ca9dbedee46af325431`、Host 12 文件 SHA-256 `39fc281389e403b2ff31ab28376e420959af79e9abdae8809e1b341ed81cfee6`。

接下来继续完成来源通知的事务核对、当前授权撤回与 Host 全链完整性收口；42 场景、两套 mutation/stateful/legacy、真实模型、原生 UI 及独立评审没有因本局部结果关闭。整体 IN_PROGRESS。


## 2026-09-23：首次有效性失效与坏通知隔离（候选16之后的工作源码）

主体核查发现两项具体缺口并由主代理修正：

- 原 HtnStore 对不存在的 scope 首次 bump_epoch 写入0，而 reader 的默认值已经是0。已启用 TaskGraph 的 Mission 现在首次写1；旧模式初始化约定保持不变。变化通知与原 epoch 同事务。
- Followup 在领取阶段解码失败原会回滚 claim 并退出整个 pump，反复挡住后续消息。现以原同事务将坏行转为 BLOCKED、清 lease、递增版本、保存固定错误码；保留 payload/source/fence，不执行消费者、不制造 ACK。损坏 cursor 的读取异常转为具名错误；故障记录使用未知水位 null，不猜0或推进游标。
- Host 图组件移除未使用默认 React 导入，两个 TaskGraph TS 文件已通过定向 tsc（--ignoreConfig、noUnusedLocals/noUnusedParameters）。构建辅助脚本排除只读 node_modules 链接，避免打包依赖目录；未改共享依赖。

窄验证仅为上述编码问题：

| 范围 | 结果 | 本地证据及 SHA-256 |
|---|---|---|
| 原 Store、复制的已启用 Mission：首次 epoch、回滚、通知、冷读 | FIRST_INVALIDATION_ADVANCES_PASS；0→1→2；派发重验证未测 | `.local-test-evidence/2026-09-23/taskgraph/epoch-invalidation/result.json`；`42a0b3643ff8cb410343e184fc5c2d5a09b6a22e51d5b560b9dad24418c7e544` |
| 原 legacy epoch 单用例 | 1 PASS / 0.15s | 同目录 `legacy.log`；`976ea3fe6dfa65b827ec05fc16bf49cec94d3fda3b2d6ad4719708e74a38b32d` |
| 原通知行的坏 payload/游标负控 | CORRUPT_DELIVERY_ISOLATION_PASS；坏消息零consumer调用、下一有效消息可领取 | `.local-test-evidence/2026-09-23/taskgraph/notification-isolation/result.json`；`36fde60d7b5eb4e7fa098b2bdff75d722fbe07705ea2b369c6a48d2fbcb8607b` |

坏通知负控只在复制DB中临时移除不可变更新触发器注入损坏，随后恢复原SQL，再执行原 Store/pump；生产schema未放松。首轮被触发器拒绝的DB保留，第二轮另存 copied-source-r2.db。此项不等同于多Mission完整E2E。5个修改Python模块语法检查通过，未启动批量回归或真实Provider/UI。

补记候选16已完成的局部路径（不继承为更新源码全门PASS）：

- source-notification：原 observation/witness/epoch 与3通知同事务提交，显式回滚均不落盘。`candidates/taskgraph-16/source-notification/result.json` SHA-256 `8df91670b5b79797bbb2247896b924ad0237b7c823a9fe5ed766b463988ed08f`；负事实存储诊断，graph admission NOT_TESTED。
- revoked-convergence：真实 Host 授权入口在围栏建立后撤回 grant，原在途调用随后返回；同一 repair 回复被 COMMIT_REJECTED，revision保持1，job READY仍fenced，Planner调用保持1。`candidates/taskgraph-16/revoked-convergence/repair-result.json` SHA-256 `bc6f60cc2f2c9defe76e9c7f3fba619db9272b5431bb1c3993d4089ed477e576`。路径前缀为 `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/`，脚本Provider，非真实模型/UI。

validity_dirty 的 dispatch_generation_revoked 历史标记不被无依据清除；原 H4 trigger 明确不将其重新解释成新证据失败。当前重算仍从原 witness producer 与 reducer读取，保留 pending诊断。

当前源码检查点：SDK95文件 `006d98556a617d30bfe5d05996130eca666ffa5f836864a45255bb3486d46c5a`；Host12文件 `b7e190cde1b0563cc3805ad9dec939cbd4b96941fba0d62e3f72da63e243fc66`。冻结候选16未覆盖；新改动尚未重新打包。整体主体收口及42场景/mutation/stateful/真实模型/原生UI/独立审查继续，门禁未关闭。


## 2026-09-23：终态停止新工作、离线重建修复及原 Commit 故障窗口（候选17/18）

主代理在隔离源码补上 terminal Mission 边界：原 MissionCancelled/Failed/Completed 事件唤醒已有收敛责任；终态 recheck/composition 返回 MISSION_TERMINAL，不再签 witness 或建立新组合工作。READY job 恢复原回复前明确拒绝 terminal Mission，继续保留围栏。原 Host cancel_mission 单场景核对通过：Mission CANCELLED，revision1，job READY fenced，3Task/5intent/4脚本Provider入口调用前后相同，Planner1次，decision保持COMPILED；两终态入口零新事件。候选17证据 `candidates/taskgraph-17/cancelled-convergence/repair-result.json` SHA-256 `794d5a6d321eb6c5268e43cb63fcb427540bedae5db301eed5a26d88eb989d9b`（前缀 `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/`）。脚本入口计数不冒充完整物理调用账。

候选17 wheel `ad5167349307583bd70d63b65ec234cc8d1f35e8a9b852ac8cda4c6424eda171`，manifest `afb80c55b057444b19d19d83a92daca2ac86339ed3b9f4f0b15e3e9411d46f02`，deployment `85a5f6244ab692caac32e2ae6b48074b08c1f4ff9f1005064be5820fbd327e29`；597包文件匹配。

开始将具名断言写入 SDK `tests/orchestrator/full_target/taskgraph_exec/`。fixture 必须读真实安装包自有manifest，使用原 code-domain world/observers、授权API、显式启用和原规划派发/收集/Commit；不制造 APPLIED、来源或验收。首场景原 H3 自动选择唯一方法，故真实行为是 native selection、零Planner模型调用；首次测试错误期待1调用已修正，失败原记录保留。入口用例1 PASS/1.32s（`production-seed-r2.log`，SHA-256 `f0a26259c64fcacbb2157c5d37928dbfa090dd0f8b1e463556c55ded9c4d0245`）。

历史/权限文件首次4 PASS/1 FAIL（3.80s）找到真实实现缺口：离线重建把 tuple SQL行传给严格canonical_json。已将 projection与readback统一为JSON list，保持完整hash核对和离线不可执行边界。候选18仅复验该失败用例1 PASS/2.33s，未重复其余通过用例。候选18冻结版本 `0.13.0.dev20260922+taskgraph.18`，wheel SHA-256 `f53957bc638d2edcba8694c8d8d77b0cf9b60a4b6141f40d781b2996e10c3c74`，manifest `f38fb62b0bdba8f8dd03fcdcda5209186c9464351f10f6353b3fd8eba9d927bd`，deployment `8e22b3a84132d9830bb0cbd136e25265af5d4a18512fc640bfa9c14acb18a313`，597包文件匹配，acceptance仍 NOT_RUN。

其后仅围绕剩余提交/恢复接口做定点检查：

| 范围 | 实际结果 | 原始证据（前缀 `.local-test-evidence/2026-09-23/taskgraph/`） |
|---|---|---|
| r1/r2原历史不同bindings/method，当前reader冷读与离线重建；另复制库篡改source hash | TWO_REVISION_OFFLINE_REPLAY_PASS；source候选16，reader候选18，零Provider，完整S01/C04仍PARTIAL | `replay-two-revisions/result.json` SHA-256 `6f058d1be606b103298ffc4da2e09f7e3df7fbb9c64116f2ccc04f25d6e2de65` |
| APPLIED/record/followup三个原SQLite写点失败 | 3 PASS/2.18s；无部分plan/record/pins/task/account/intent/事件；零Provider | `commit-atomicity-r1.log` SHA-256 `2a003ec602a1a06a548d13335804dfbf235de2221c8fd3d98080c47422892510` |
| 已提交原回复在grant撤回后重收；变字节拒绝 | 1 PASS/1.10s；total_changes与Provider均不增加 | `commit-replay-r2.log` SHA-256 `5beef0f5d302fb12666a7ce7ac1117654f854b8eed2924bb1a949e6a3bd31241` |
| 真子进程os._exit：事务中record前、durable Commit后 | 2 PASS/3.23s；分别0/1 revision恢复，原CAS回复/原command恢复后均唯一revision，零新Provider/Attempt | `process-recovery-r1.log` |

commit replay第一版fixture误将原native attempt ordinal写死为1，实际为0；改为原函数读取真实ordinal后通过，旧失败日志保留。三个SQL abort是测试自有触发器注入故障，没有替换compiler/authority/Commit。进程强退测试只有前述两个窗口，不等于全部C03四窗口。历史负控只损坏独立copy并恢复原触发器后读取，保留原始来源。

工作源码检查点SDK102文件（95源码+7验收文件）补丁 `c744b771792ab5a9786875fc08d3fa0f9ff3fbd279e46a836637203bd4d88288`、Host12文件 `7a7b8515720085a895da64b681561ec5d67ca9aa71de6ed442ffb13057f5c468`。验收用例建设仍未覆盖42全表；mutation/stateful/全H1兼容/真实模型/原生UI/独立审查未执行。无新大规模回归、共享Host/HTN变更或自动化。磁盘约908MiB剩余，已询问是否清理约1.9GB共享uv下载缓存；未获答复前不清理，原始证据不删除。


## 2026-09-23：启动前可信装配与原 Worker 强退恢复（候选19）

发现并修复真实冷启动缺口：已有 TaskGraph RUNNING Attempt 的工作区在 Orchestrator.__aenter__ 内恢复，但 Host 原来在 enter 返回后才安装 TaskGraph，因而触发 TASKGRAPH_EXECUTION_ASSEMBLY_REQUIRED。现以仅可信部署传入的同步 startup_assembly 回调，在 Store/Commit/SDK构造完成后、原账目恢复/工作区恢复/SDK运行池启动之前安装 HTN 和 TaskGraph；ActionExecutor 同时提前构造供原操作来源读取，未执行连接器。Host 首启与 rebuild 均走此顺序，rebuild 仍在成功后才发布新实例。普通 SDK 调用的可选参数默认 None；未装配不获得派发权。没有更改 IPC payload、执行账本合同或制造授权。

定点结果：

| 范围 | 结果和边界 | 本地证据 SHA-256 |
|---|---|---|
| 新增原 reserve/物理响应强退窗口 | 1 PASS/1 FAIL，after_executor 冷启动未装配，失败证据保留 | `attempt-recovery-r1.log`；`c79c722de87ca1849bdd6241150343f70a478da211282ca799097bbde342c0f1` |
| 候选19：物理结果返回后、collector前 os._exit84 | 1 PASS/6.57s；原2次物理调用、1 Attempt、revision1不变；原收集及recover导入费用，0新Provider | `attempt-recovery-c19-r4.log`；`bb35adfcb6802ab5bfc35610d06704e7a0bf4f1b220b63bb857decd7dcba1bf9` |
| 真实Host start和rebuild；复制候选16 UNKNOWN库 | PASS；2 Attempt/5 intent/1 revision/5 physical/1 UNKNOWN不变，0新Provider，原预算RESERVED、job仍fenced | `host-recovery-c19/cold-read-result.json`；`a73132684d0e019bfedb2b5f53ff65bc1e7868686d91f377047e620b8674c0be` |

证据前缀 `.local-test-evidence/2026-09-23/taskgraph/`。Worker fixture 的前三次候选19复验分别暴露测试驱动的普通文本无envelope、envelope未认领原facts输出口、未调用原recover账目导入；全部失败保留，不调整生产guard来接受坏结果。最终驱动通过原workspace_write_file真实产生facts.md，再回传绑定原Attempt身份的envelope；真实os._exit后由原collector+recover处理，未以手写结果/费用替代原回执。本项未继续Worker内容验收或运行完整Mission。

Sol只读独立挑战启动改动及直接installer/cleanup依赖：未发现可证明P0/P1/P2；未跑测试/服务，不能替代整体独立审查。候选19包身份：
- version: `0.13.0.dev20260922+taskgraph.19`
- wheel_sha256: `15a1d585b2c4179937dc0ac4a2fa74e6bb7bd2a36e25e6e1caf12244f89c4367`
- manifest_sha256: `dfa82e07662ef3ffd4a5f6f6c28f4b3b69884ca653f5ad0e22ea443996dc914e`
- deployment_id: `7704cb11c675678c5d3e4d75fa77b93bc41c1dc965a98bd1a87185886da6746e`

597包文件匹配，acceptance NOT_RUN。共享Host/HTN及自动化未修改。只做此失败窗口和对应Host装配的定点验证，未开启批量回归；主体完整性审计继续，全部验收门仍开放。

## 2026-09-23 主体真实验证发现根评审证据缺口；候选21待复测

用户将本阶段限定为主体功能小规模验收，DeepSeeker真实链路加基本Host图交互，重型42/变异/stateful/全H1回归后置整体集成。候选19真实 seeded Mission 的叶任务已有原 VerificationPassed/code_test PASS 和正式 Acceptance，但根评审只拿到产物与叶结论，缺原pytest回执，错误进入证据不足修复循环；这是实际跨层缺口，未记PASS。更早无测试seed和误选doc domain的试验均保留为失败，未抹除UNKNOWN。原始证据：`.local-test-evidence/2026-09-23/taskgraph/real-main-c19*/`。

修复在隔离SDK root_review + taskgraph_review_evidence：仅TG启用时，为各贡献附原不可变VerificationPassed摘要；核对正式ACCEPT review、package/acceptance全坐标、原StoredResult DONE/PASS、事件result/attempt；固定layer集合与数目，scope投影身份和计数，摘要/target有截断标记，receipt限定标量与长度；不改变根评审独立裁决、不伪造授权或PASS。只读原库3条Acceptance回读RECORDED，每条约1.7K字符；这只是诊断，不是真模型复验。

candidate20保留未测字节，修复独立挑战指出的摘要无界/身份闭包后新冻结candidate21。wheel `2388d0451a257c4a97854058253955d874e9919560914f5099768cc41689dda3`；manifest `64cf540ec9e3476bdf00096635605f8c493fdaad2c69cd404f418f321200eb7b`；deployment `c1b3b2d681c2fb47538e28e49b99b0e56e84cf2f22300dfae602705bd1458cb6`；598包文件核对。全TaskGraph acceptance依旧NOT_RUN。新的real-main-c21待运行，第三部分尚未通知。

### candidate22真实链路增量：根评审通过，Mission judge派发身份错误

独立增量挑战修正code_test未绑定scope仍展示PASS、target=None旧协议兼容后冻结candidate22，wheel `6eb579c6c6e1327e0f3e25ee12fc32b2d0c21c373dabf709b3d412e5cdb07e54`。real-main-c22 Mission `mission-6d3140200fb7b91f` 已有原Acceptance和MISSION_FINAL GoalResolutionCommitted；未正式COMPLETED，最终Mission judge停PENDING。9次真实DeepSeeker调用全部succeeded、40754tokens、0unknown，在无在途请求的边界SIGINT自有runner，原库/结果不改。

原因已定位：原_run_critic把verification view ID放在config.attempt_id；Mission judge本来不是Worker Attempt，原_critic_subject_stopped已有显式兼容。但TaskGraph require_handoff一律要求真实Attempt，误挡最终judge。修复仅对原critic+注册judge workspace+同Mission原BudgetReserved无Task/Attempt事件认可Mission judge视图，从该workspace原artifacts取真实producerTask继续原fence校验；任意缺失Attempt仍拒绝。只读原库身份定位及错kind/错Mission拒绝的两个定点诊断通过；真实候选复验待后续，不宣称主体PASS。原证据`.local-test-evidence/2026-09-23/taskgraph/real-main-c22/`。

## 2026-09-23 主体阶段验证完成，等待独立交接核验

最终candidate23真实Mission `mission-24dfcf57110f9dbe` COMPLETED：107.08秒、11次DeepSeeker调用/49646tokens/0unknown。原Host四读入口与rebuild前后1Attempt/6intents/106events/1revision/11calls相同。实际输出NOTES.md通过系统pytest及正式验收。SDK来源598包文件。

原生基本验证使用HTN二进制载体、candidate23真实后端/安装包、最终Host UI2前端：修复重复React key及深色节点浅字问题后，实际点击读取当前/历史、两视图、SVG节点原因、节点及根结果展开、1→1零差异、空收敛、999错误保留旧图和清空恢复当前均可见。启动阶段消费原107–114共8条尾部followup，正式读图检查前后114events不变/0新调用。最后一次采集出现image destination错误，同次AX已返回CURRENT，下一次完整AX独立重取保存为ui2-restored-current.ax.txt；没有把截图采集错误冒充截图PASS。自有native/backend/Vite已停止。

最终源码是SDK candidate23加 `candidates/taskgraph-23-host-ui2/host`；原candidate23 host前端保留为历史未修UI。本轮SDK补丁104文件SHA `d6298679cb2b5c3c1e9ab344bcfeb48370a385d3297b6c10278e3f2e67c1dade`，Host补丁12文件SHA `e41b2e7687186ebb8cdb46b5b70952f7431c30ac96850a8cd0b7ef6d89cf9ce2`。完整manifest acceptance仍NOT_RUN。MAIN-RESULTS保存逐项结果/失败历史/本地索引与SHA。ARCHITECTURE/TASKGRAPH和PROJECT_STATUS同次回写；旧定时任务未重建。独立Sol full/round1交接核验正在执行，通知第三部分尚未发送。

### 本阶段方法复盘

遵守主体编码先行后，只做一条真实关键链路及针对实际故障的复验，比堆批量单测更直接暴露根评审证据/最终judge接线缺口。静态审查未发现问题不等于真实链路完成；本阶段最终通过也不扩张到重型门。原生基本截图仍有价值，发现静态/协议检查看不到的重复面板与深色文字。下一次原生验证先做资源包/installed origin和端口的廉价预检查；复制只用明确源码白名单或APFS clone，不能让copytree跟随node_modules链接。失败案例和UNKNOWN必须保留，环境修复不复跑模型任务。
