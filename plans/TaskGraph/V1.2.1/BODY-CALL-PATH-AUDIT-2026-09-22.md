# 主体调用路径核对（非验收）

## 2026-09-22 主体核对增量

- TaskGraph H4 失败修复已进入原 `planning_repair_requests` → `method_applicability` → 原 PlanningDecision → `taskgraph_preview.ensure_convergence` → 原 PlanCommit participant；候选 taskgraph.9 具名通过 revision 2/APPLIED。
- DATA 入口已实际走 `TaskGraphDispatchBinding.prepare` → `resolved_inputs`/`overlay_attempt_inputs` → `require_current_manifest_use` → `TaskGraphAttemptMaterialized`；候选 taskgraph.10 两节点诊断冻结 1 条正式 DATA binding，未声明文件没有进入 manifest。
- Selection 入口已核对 `create_attempt(selection_decision_id)`、`selection_materials`、`taskgraph_inputs` 联合集合、`require_mounts` 与 `TaskGraphAttemptMaterialized`；原 Selection winner 单项检查 1 passed，TaskGraph 联合候选材料的真实场景仍待。
- 运行中 foreign lease 门禁保持原 `RUNNING_WORK_UNRESOLVED`；原 H1-H 两参数定点检查 2 passed。真实在途 SDK cancel/unknown recovery 仍未验收。


最新：taskgraph.9 已完成具名的原 H4 失败修复→TaskGraph 收敛→原 PlanCommit revision 2/APPLIED；taskgraph.6 单叶完成后冷读无新增调用。非空 DATA/Selection 联合入口已有脚本化证据；在途取消/UNKNOWN 及整体验收仍待完成，详见 [主体进度](BODY-WORK-IN-PROGRESS.md)。均为脚本化 SDK 接线诊断，非真实模型/UI。

## 2026-09-22：实际授权与初始读图入口通过，继续原计划提交（整体未完成）

独立候选 `0.13.0.dev20260922+taskgraph.4` 已通过具名入口检查：实际 SDK 方法生成/准入 → 原 planning request → Host 原授权事务启用 TaskGraph → 当前初始图读取。使用 SDK RoleScriptedProvider，仅验证本地接线；真实模型与原生 UI 未运行，不能当作产品验收。595 个包内源码文件与冻结清单一致；wheel SHA-256 `b09725de105ea604c25dacc137dd489a44c9711de89072643be2e0d2dda0ca42`，manifest SHA-256 `55b5a59a36b710f8b76c8de46b46bd357ad931033a21c69d24ddaee641cddfc5`。本地证据：`.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-4/{entry.log,entry-result.json,package-identity.json}`；其中旧探针的 model_execution=NOT_RUN 指没有真实模型请求，实际存在 scripted SDK invocation。

本次修正原默认 workspace TargetRules 的读取、原 Commit 内 dispatcher 接线、完整 DATA workspace 输入冻结及原事务物化回执、初始 epoch 0 来源一致性，以及无 orchestrator grant 的原 SDK 用量导入。缺失用量/价格仍保留原预算；有 grant 的调用继续原严格核验。新输入绑定为 v2，保留 v1 读取；旧候选和 codec manifest 未覆盖。

继续提交时发现初始 compound 根已有原语义绑定但没有实体 Task，原 TaskGraph 预览只枚举实体 Task 而漏掉根。已统一为读取原 Mission 全部当前语义绑定，正在沿同一入口验证。原 PlanCommit/APPLIED/历史读取尚未通过；派发、收敛、恢复与 Host UI 仍待完成。主体阶段无批量单测/回归，无真实模型请求；共享 Host/HTN 环境、DB 与候选未改动，定时任务未重建。


## 2026-09-22：独立安装包与入口接线继续推进（整体未完成）

补齐 HTN 完成来源进入 TaskGraph 预览/提交的 acceptances 通道：原 completion spec、scope、贡献、outcome binding 通过原 Store 身份校验读取，同一快照核对当前正式内容/效果状态和 dirty 记录。修正初始语义 compound 尚无实体 Task 时的预算读取：保留 Mission 账户，实体 Task 才要求对应 Task 账户；缺失 primitive 仍拒绝。相关每次1–2文件类型检查通过。

独立候选 `0.13.0.dev20260922+taskgraph.1` 已构建并安装到 `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/candidates/taskgraph-1/env`，当前 Host 环境未变。wheel SHA-256 `cbf114418258b347d57f085367d46179e11c929d16441734bfd9c0a34972ed45`；候选 manifest SHA-256 `a59ee79c4f6ebca62d5365a089fbb7d71514996c7abf29bee865363dff6b64d0`；部署身份 `7e9642cc1876323e367d5fde93ced3ef2fec3e8e0bb3a0641cd628c1f3a5c1f8`。594 个包内文件与冻结清单逐字节一致，安装位置和 package-owned reader 验证通过。构建时的2个版本文件改写单独记录，未覆盖旧 codec/候选。`taskgraph_acceptance=NOT_RUN`，不是功能验收通过。

安装入口检查首先发现隔离 Host 缺少 sdk_adapters；补充只读捕获712个Host源码/配置文件，采集前后hash一致，原已捕获文件无漂移。隔离 Host 已绑定新包的版本/hash/manifest、pyproject和lock，主Host未变。原 start 和新Mission创建已实际到达，未启用后台driver或调用真实Provider。入口探针先因漏传原 facade 必需的 request_id 被拒，补完成要求后遇到空方法库正常进入 synthesis，尚无可授权的 planning request；未伪造request/grant/APPLIED来绕过。该探针没有完成授权→读图，不计ENTRY_PASS或产品验收。保留 `entry.log`、`entry-r2.log`、`entry-r3.log` 与本地库；后续须沿真实方法产生/规划请求入口继续，不把探针前置不足误记成产品故障。

本轮没有批量单测、回归、H6/H8评测或真实模型/UI测试。仍待真实授权→原Plan Commit→派发/收敛/恢复→图视图全链验收及最终应用接入；未完成整体TaskGraph。局部检查点不撤销持续推进授权，定时任务没有重建。

最新源码补丁：SDK `5508e48e4c50cb463cc8b812b194a647e07052c63636659b527bd0100be04124`；Host `81b87195908bdee094431a6fe588e6a2cf2b50b1c572b36d63fa1737773698b5`。打包 provenance、依赖采集与原始入口证据保存在上述 ignored 目录。


## 2026-09-22：接入 HTN 最终核心交付（当前源码 WIP，未验收）

HTN 已正式交接 READY_FOR_TASKGRAPH_WIRING，旧“只能离线准备/仍等待 ready”仅为历史。交接范围为 V1.4 核心集成和原生完整效果闭环；语义回放仍 PARTIAL，H6/H8 大评测依用户要求移出阶段，不记作通过或重新启动。

当前工作区为 `.local-test-evidence/2026-09-22/taskgraph/htn1-integration/working/{sdk,host}`。只读捕获当前 dirty SDK/Host，532 个 SDK 构建输入与原 manifest 核对，采集前后源 hash 不变；528 个 wheel 包内文件核对，版本改写有原 manifest 依据。原 HTN wheel 为 `0.13.0.dev20260922+htn.1`，SHA-256 `af9e273061ceeeb3204ccbb4e4d32965fb3bba568e6c7e63f1af1b18244b9800`。原始采集/合并清单位于该隔离目录。

完整 TaskGraph 增量已三方合并到该交付，8 个 SDK 冲突已处理；保留 H4 repair 分支、原 Action 完整负证明及单 Mission world。TaskGraph 迁移改为追加 25，保留 HTN 的 23/24；codec 新增 v5，未覆盖旧 manifest。固定装配、执行/预览/派发/baseline/恢复和 policy 读取改为解析目标 Mission dispatcher。完成判断接入正式内容/效果/scope，界面区分等待效果审查、需对账及真正完成，不以 Action.SUCCEEDED 或旧 Task.status 推断整体完成。

隔离 Host 已接默认可信部署 reader 和真实规划授权事务内启用。部署 reader 校验派生包完整源码身份及原 HTN 验收证据索引；派生 TaskGraph 验收状态仍 NOT_RUN。构建冻结脚本已准备但未执行，未制造 TaskGraph PASS 或用户授权。尚未构建/安装 TaskGraph wheel、迁移真实库或启动产品。只进行解除接线问题所需的局部类型/语法/导入检查，没有 pytest/vitest/大批量回归/模型/UI 验收。

剩余工作：最终源码跨层一致性审计、派生包冻结及安装、真实执行/收敛/恢复与 Host 界面验收。SOURCE_MAP_COMPLETE 尚待完整入口审计；H1_H_READY 采用本次具名核心接线范围 READY，原历史全门不关闭；TG_A_VALIDATED 和 TG-A–E 整体验收仍 OPEN。用户授权持续有效，不以局部检查点结束整体任务。不使用子代理或 plan-task；定时任务已删除，不重建。

当前 SDK 补丁 SHA-256 `92ef69db21950eef31c729abb56534d37110d2da37e7bb472f523638ae6a57c1`；Host 补丁 SHA-256 `bcb43062175618f814d3db152cdf6e24e1f4181159cf9b386ed59500e07a2c16`。对应 `HTN1-INTEGRATION-WIP-SDK.patch` / `HTN1-INTEGRATION-WIP-HOST.patch`，是源码身份而非完成度。

以下保留历史检查点及场景索引；当前状态以上述为准。

最后更新：2026-09-22 CST。范围：V1.2.1的42组合同。此表仅记录隔离源码的调用路径对应关系，不是独立审查、可运行性结论或场景PASS。所有行为验证仍为**NOT_RUN**，遵守主体代码与跨层安装完成前不跑批量测试的要求。

SDK相对路径基于 `.local-test-evidence/2026-09-21/taskgraph/complete-plan-prep/body-implementation/snapshot/sdk/src/agent_orchestrator/`；Host相对路径基于同级 `snapshot/host/`。原H1路径必须用最终稳定源码重新核对，当前检查点不能关闭SOURCE_MAP_COMPLETE或H1_H_READY。

本次只读核对发现并修正：Action误走Agent结算检查；Operation效果已确定但Action预算尚未释放未纳入局部工作；结构预算超限错误被公共API混报GRAPH_INTEGRITY。均只有局部静态检查，行为待最终验证。

| ID | 已定位的源码责任 | 入口文件 | 尚需完成/验证 |
|---|---|---|---|
| S01 | 原历史结构/完整祖先链/tenant | storage/taskgraph_store.py; api/taskgraph.py | 最终迁移与真实两revision/恢复 |
| S02 | 原codec/grounder和候选编译 | graph/network_codec.py; planning/plan_preview.py | 最终H1原Method合同 |
| S03 | 完整来源→纯候选→Commit重读 | orchestrator/taskgraph_plan_sources.py; orchestrator/taskgraph_preview.py | 原始H1来源对齐和删除来源负控 |
| S04 | 原AND-OR网络及结构检查 | graph/task_network.py; graph/taskgraph_validation.py | 真实采用链 |
| S05 | 原compound阶段/typed frontier | orchestrator/hierarchical_dispatch.py; orchestrator/event_handler.py | 最终H1组合执行与预算证据 |
| S06 | 完整结构/投影/环检查 | graph/taskgraph_validation.py; api/taskgraph.py | 坏图分别进入preview/read/物化 |
| S07 | 显式pending和完整来源门 | graph/taskgraph_validation.py; orchestrator/taskgraph_plan_sources.py | pending与NOT_CHECKED负控 |
| S08 | 绑定GraphStructureBudget和公共边界 | graph/taskgraph_validation.py; api/taskgraph.py | 边界/+1和真实性能记录 |
| D01 | 原manifest resolver到联合物化 | orchestrator/taskgraph_dispatch.py; orchestrator/taskgraph_materialization.py | 真实工作区输出集合 |
| D02 | 严格frozen空manifest与未取得拒绝 | artifacts/taskgraph_inputs.py; orchestrator/taskgraph_dispatch.py | 无输入/缺必需/来源失败对照 |
| D03 | 原schema/版本选择及当前使用权限 | artifacts/taskgraph_inputs.py; artifacts/input_bindings.py | 真实converter与歧义负控 |
| D04 | 原结论+完整物理/账目结算 | orchestrator/taskgraph_settlement.py; orchestrator/taskgraph_runtime_imports.py | 最终Action负证明与真实UNKNOWN |
| D05 | TargetRules/精确CAS/挂载冲突与symlink | orchestrator/taskgraph_materialization.py; artifacts/versioning.py | 真实文件系统负控 |
| D06 | 原Attempt冻结绑定和恢复 | storage/taskgraph_attempt_inputs.py; orchestrator/taskgraph_dispatch.py | 实际两Attempt/重启 |
| A01 | 原tenant/issuer委派和显式启用 | orchestrator/taskgraph_policy_sources.py; orchestrator/taskgraph_policy.py | 实际H1部署验收reader及最外层部署绑定；service初启/重建已有源码接线 |
| A02 | 原H1 request/package/visible refs | orchestrator/event_handler.py; planning/decision_codec.py | 最终H1合同，不可由TaskGraph代造 |
| A03 | 原规划授权及同事务Commit | orchestrator/taskgraph_convergence_authority.py; orchestrator/taskgraph_plan_commit.py | 撤权/过期/成功回放竞态 |
| A04 | 原Validity/START/consumer与当前冻结输入 | artifacts/taskgraph_inputs.py; orchestrator/taskgraph_execution_sources.py | 真实反证/epoch/用途负控 |
| A05 | 实际policy、tool注册与物化资源 | orchestrator/taskgraph_execution_policy.py; orchestrator/taskgraph_materialization.py | 最终runtime profiles/真实工具权限 |
| A06 | 原BudgetLedger与Agent/Action独立结算 | orchestrator/accounting_recovery.py; orchestrator/taskgraph_action_settlement.py | 守恒/并发/UNKNOWN原收据 |
| R01 | 共享来源/原冻结输入/完整需求集合 | graph/taskgraph_sharing.py; orchestrator/taskgraph_sharing_inputs.py | 真实并存消费者与退休 |
| R02 | 保守revalidate与精确取消范围分离 | graph/convergence.py; orchestrator/taskgraph_notifications.py | 支持关系变化和保守覆盖标记 |
| R03 | 合法性先于fence/取消 | orchestrator/taskgraph_preview.py; orchestrator/taskgraph_convergence_authority.py | 非法repair时原工作不受影响 |
| R04 | 持久job/fence/原取消与原reply恢复 | orchestrator/taskgraph_runtime.py; orchestrator/taskgraph_resume.py | 最终H1强退恢复和实际Commit |
| R05 | 原Store事务内完整before/candidate比较 | orchestrator/taskgraph_plan_commit.py; storage/taskgraph_convergence.py | 两连接barrier竞态 |
| R06 | 全部Operation历史链接/原mapper | runtime/taskgraph_operation_sources.py; orchestrator/taskgraph_reconciliation_proof.py | 最终H1完整负证明/UNKNOWN |
| R07 | 原Attempt输入关联与当前代次 | storage/taskgraph_attempt_inputs.py; orchestrator/taskgraph_dispatch.py | 晚到Verifier/原accept链 |
| R08 | 当前grant/原fence与认证abandon | orchestrator/taskgraph_operator.py; orchestrator/taskgraph_convergence_authority.py | 取消/撤权/重复唤醒与责任接管边界 |
| C01 | 原Commit participant和APPLIED writer | orchestrator/taskgraph_plan_commit.py; orchestrator/plan_commits.py | 真实写点故障与幂等 |
| C02 | 原revision CAS及重新验证 | orchestrator/plan_commits.py; graph/taskgraph_validation.py | 两SQLite连接提交 |
| C03 | 原事务/意图/SDK回执恢复 | orchestrator/event_handler.py; runtime/dispatch_history.py | 四个真实进程强退点 |
| C04 | 完整历史校验→冻结文档重算member/method/demand pins→离线投影v2 | observability/taskgraph_replay.py; storage/taskgraph_store.py | 新离线目标/源hash篡改 |
| C05 | 原事件cursor/持久消费回执与ACK | orchestrator/taskgraph_followups.py; storage/taskgraph_followups.py | 强退后原command复用 |
| C06 | 原runner追加迁移 | storage/schema.py | 最终H1迁移编号冲突解决，旧库copy |
| C07 | 原Store FK/触发器/完整源JSON核验 | storage/schema.py; storage/taskgraph_store.py | 跨Mission实际负控 |
| C08 | Attempt/reserve/manifest/intent同事务 | orchestrator/commit_service.py; storage/taskgraph_attempt_inputs.py | 实际回滚/冷恢复 |
| V01 | 原组合Verifier和root resolution | orchestrator/hierarchical_dispatch.py; orchestrator/taskgraph_outcomes.py | 最终H1实际组合验收 |
| V02 | 不改图决定跳过plan来源/preview | orchestrator/event_handler.py | 最终H1 WAIT/NO_CHANGE/BLOCKED真实入口 |
| V03 | 以上路径的真实完整Method替换 | orchestrator/event_handler.py; orchestrator/taskgraph_resume.py | 最终H1真实Planner/Worker/Verifier整链 |
| V04 | SDK固定只读API与隔离Host视图 | api/taskgraph.py; Host orchestration/taskgraph.py; Host taskgraphStore.ts | 最终安装、重连、认证切换、跨历史/收敛水位、原生UI |
| V05 | 原默认/旧协议路径保留 | orchestrator/event_handler.py; orchestrator/taskgraph_dispatch.py | 最终scope核对及legacy golden，不借旧成绩 |
| V06 | 真实模型与规模 | 最终验收runner（待按稳定源码冻结） | H1就绪、获准配置、12trial/全部成本与失败原样记录 |

当前接入缺口：真实H1-H部署验收reader；稳定最终H1源码及迁移/codec版本对齐；Host最外层可信部署绑定及安装（service初启/重建已有源码接线）。操作者命令为内部认证入口，图UI仍只读。没有测试运行、真实Provider调用或HTN环境变更。


## 2026-09-22：DATA 物化器接入核对

候选 taskgraph.11 的 `_bind_workspace()` 首次 TaskGraph workspace 构建已实际调用冻结 manifest 的 `materialise_v2()`；计数式隔离探针记录 6 次调用，并在相同 DATA+Selection 联合场景中完成 Mission。恢复路径仍先 `verify_materialized()`，不会以最新输入覆盖 ACTIVE workspace。该切片为脚本化 SDK 接线证据，真实模型/UI与负向冲突矩阵仍未运行。


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


2026-09-23 最新：候选 16 增加原来源/审批变化通知并修正 LOST/TIMED_OUT 的物理占用保留；已核对在途取消和 UNKNOWN 冷恢复的具名局部路径。主体及正式整体验收仍 IN_PROGRESS，详细源码、失败、哈希和覆盖边界统一见 [执行日志](EXECUTION-JOURNAL.md) 的候选 16 条目。


2026-09-23最新：候选19修复恢复前可信装配顺序，原Worker强退收集/账目恢复及Host首启/rebuild保留UNKNOWN状态的窄路径通过；主体完整性审计继续，整体未完成。来源身份与边界统一见[执行日志](EXECUTION-JOURNAL.md)候选19条目。
