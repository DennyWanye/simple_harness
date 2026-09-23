# TaskGraph 主体编码工作进度

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

最后更新：2026-09-22 CST。状态：**隔离源码 WIP，主体未全部完成，未验收，未启用。**

用户要求当前由主代理集中编码；主体功能和接线完成之前禁止成批单元、回归和全量测试。已停止编码子代理，不用局部检查数或文件数表示功能完成。本阶段仅按接线问题做每次1–3个变更文件的类型检查，以及改动大文件AST/assembly import；发现类型或身份接线问题就局部修正，未扩展为批量测试。

工作区：`.local-test-evidence/2026-09-21/taskgraph/complete-plan-prep/body-implementation/snapshot/sdk`。父检查点为 `history-pins`，原156项PASS不适用于新增代码。HTN候选、环境、DB、运行服务和共享Host源码未被本任务修改；共享仓库仅回写本TaskGraph文字进度。

最新主体核对：[42组调用路径对应表](BODY-CALL-PATH-AUDIT-2026-09-22.md)。仅源码核对，不是42组执行或独立验收；本次另修正Action账户纳入局部收敛，以及读取超限返回BOUND_REACHED。

## 2026-09-22：继续推进离线重放重建（源码WIP）

上轮保存局部源码检查点后主动结束，并非整体完成。用户持续推进授权仍有效，不将局部检查点作为整体停止条件；HTN缺最终交接只阻塞其依赖路径。

C04离线replay改为从已核验的不可变NetworkDocument调用版本化pins reducer，重新生成member/method/demand关联，不直接复制源库pins。纯图目标库补全member binding revision/hash，并参与读回projection hash；目标格式更新为taskgraph-offline-graph-replay-v2，不覆盖旧目标文件或codec manifest。仍明确RUNTIME_RESUME_NOT_AUTHORIZED，不把重放图当可恢复执行库。

仅改动文件mypy通过；没有重放真实/测试数据库、没有行为测试、没有批量回归。SDK74文件补丁SHA-256 `f5da43a45316f4208b46f156bd65ec4dfe508ccaff1e3871a67706324cea10bf`；Host保持上一检查点。HTN只读快照revision11仍active/inProgress，未接入或打扰其工作。实际最终部署/最外层接线及完整验收仍未完成。

## 2026-09-22：Host生命周期接线及读取水位（源码WIP）

固定SDK装配自行创建同Store的TaskGraphStore，直接绑定认证Principal与原Mission tenant；当前读取由完整execution reader核验，删除外部history/read/current成功回调。唯一待最终交接落实的验收来源仍为deployment_acceptance，不能用空实现替代。

隔离Host service已在首次open与rebuild时安装所有TaskGraph协作者，close/重建失败清除旧引用；重建创建新Store绑定，不复用旧读写对象。内部enable_taskgraph_contract入口使用固定认证身份和原policy事务，成功后唤醒原driver/change pump；未注册IPC写verb或模型工具。服务可接收可信部署ports，但应用最外层wiring仍须在最终H1交接后绑定实际验收reader和冻结预算；当前未安装/启用运行Host。

前端reducer保留当前图/收敛读取的最高事件水位，跨历史切换不回退；新图使旧收敛结果stale，新收敛结果使旧当前图stale并清除旧why；认证/连接重置仍清空全部状态。行为验证NOT_RUN。

仅做SDK assembly单文件mypy、Host service AST、前端store单文件tsc，均通过；未跑批量单测/回归/模型/UI，未启动Store或runtime。当前SDK74文件补丁SHA-256 `11072a0eb430e42356c5dec020cc9d61fa9e2c6113f66f0206686e9d92473b4e`，Host8文件补丁SHA-256 `6a73bfa2f217e980e7d1723720c26e2755dd9442d509db48c1c50f1dfa990743`。完整字节在ignored body-implementation，文件数不是完成度。

HTN只读任务仍revision11 active/inProgress；当前共享架构记载H8 HTTP524/unknown保留及H6 cohort-4继续，仍明确未通知TaskGraph ready。未接入活动candidate，不改其依赖/DB/服务/共享Host，不派工/发消息。当前仍需最终H1源码/迁移/codec对齐、实际验收来源和最外层安装、主体完成审计及之后的全套验收。SOURCE_MAP_COMPLETE/H1_H_READY/TG_A_VALIDATED保持OPEN，定时任务未恢复。

## 2026-09-22：固定启用policy来源（源码WIP，未启用）

固定装配已构造StoreTaskGraphPolicyAuthority：从原Store读取当前规划委派lineage、签发者/tenant/scope/TTL/原命令receipt hash，读取原Mission候选参数版本与实际缓存一致性，冻结安装的schema registry、TargetRules、部署policy和GraphStructureBudget。启用结果保存实际部署验收SourceRef，历史policy读取同样校验该引用格式；没有为任何Mission签发新委派或执行enable。

部署验收reader仍是必须从最终HTN交付安装的唯一具名验收来源端口；它必须核验实际源码/配置和原验收收据，不能返回自造PASS或只声称complete。最终H1-H尚未就绪，本轮未构造该事实、未安装共享Host。最新HTN只读快照为active/inProgress，架构仍明确未通知TaskGraph ready。

当前SDK补丁74源码文件SHA-256 `756daf45a3cef552fcb2cd0a5423cd391f49db1e16d3bd1deb32c1e2f5fbbf78`；Host8源码文件SHA-256 `4f6d43ac44b5fbddf7ad67ffa8290551036a07855996ac734aa281b7bc97dd72`。只做每次1–3文件mypy及改动AST/assembly import，没有批量测试、模型或UI。文件数与静态通过不表示主体或门禁完成。

## 2026-09-22：Action独立结算、原收敛证明和操作者命令（源码WIP）

Action预算按原reservation_subject精确连接Operation和真实效果证明，不再误当Agent intent；物理效果或历史负证明不完整时先保存真实结果并保留账户，原accounting recovery在证明到齐后结算。局部工作来源同时包含Action账户，不能以Operation完成代替账目收敛。

收敛的begin/静止/已启动检查已改为固定实际来源实现：原规划授权、完整12来源、实际SDK取消控制命令、当前Action handoff对应的原对账事件或真实在途owner。TaskGraph诊断通知本身不作为完成依据。新增认证操作者服务，绑定真实enable签发者及tenant；放弃候选须旧图需求未变且物理/账目静止，原事务记录命令回执并重新评价；BLOCKED通知重试须原消息和当前来源可读，再以明确操作者命令做Q13 CAS。未注册模型工具，未新增Host写入口。

检查点SDK补丁73源码文件SHA-256 `9f0bcca3ccb23ffe2bd28019644cde20723267647697a34ed406c5d8f4158bae`，Host补丁未变。仅局部类型/语法和assembly import检查，无行为测试；不是TG-A或产品PASS。实际部署验收/启用policy authority、最终H1接入、Host安装及最终验收仍待，旧156 PASS不覆盖本次。

## 2026-09-22：完整执行历史、物理结算与输入物化（源码WIP，未验收）

- 新增固定 runtime 来源读取器：在原 Store 与 SDK 只读快照中校验全部历史 rehandoff 的 Agent/Turn/input 身份、Provider grants/invocations、effects 和账目，不再只观察最新 Agent。原服务重新派发补记精确来源字段，历史导入不受事件分页截断影响。
- TaskGraph 终态和取消路径保留 UNKNOWN 费用占用。实际历史调用、工具效果及账目未结算时不得释放；取消每个实际历史 Turn 并保留原 SDK 回执，不把取消已受理当作物理停止。旧模式保持原清理规则。
- 固定执行前核验当前授权、原冻结输入、真实工具与容量；工作区校验完整 DATA/Selection/source/fragment 挂载冲突、字节与路径，恢复先查篡改再刷新保护文件。修正同 hash 产物物化时 artifact 与 producer 的对应关系。
- baseline 静止证明由真实启用事务、完整 runtime/Operation/账目读取生成，历史读取回查原 receipt/event。没有制造 APPLIED、部署验收或启用权限。共享工作读取原 Attempt/Acceptance 的冻结输入版本并核对当前合法使用证据。

本检查点SDK补丁70个源码文件 SHA-256 `f88a1db4205189c9067e4037870faccace291e005798d9bbb26ea0edf7bc1713`；Host仍8个源码文件 SHA-256 `4f6d43ac44b5fbddf7ad67ffa8290551036a07855996ac734aa281b7bc97dd72`。文件数仅用于源身份，不代表完成度。仅针对改动做1–3文件类型检查、AST与assembly import；未运行pytest/vitest/批量单测/回归/模型/UI，未启动Store/runtime。原156 PASS仅覆盖父检查点。

剩余：实际启用/部署/规划权限与operator来源、完整收敛权限接线、最终H1稳定源码对齐以及SDK/Host安装；主体完成后再统一验收42场景等门禁。HTN只读任务快照虽idle，架构事实仍V1.4 IN_PROGRESS且未通知TaskGraph ready，故未接入候选、未动环境/DB/Host、未派工/发消息。已删除的定时任务不恢复。所有正式门禁保持OPEN。

## 2026-09-22：共享编译、精确收敛准入和Selection联合输入（未验收）

- 纯预览原本固定传入sharing=None；现从同次Store来源捕获原SharedGoalIndex，传入原grounder/compiler。准确引用唯一CURRENT Acceptance的合同版本/hash；没有合格原记录或多个接受记录时不猜选。新增共享slot在freeze与Commit重新核对真实索引、Acceptance及未变更的生产者/DATA。
- 预准入只允许TaskGraph已安装路径推迟running-work检查以生成候选；最终准入携带从完整before/candidate计算的范围。Commit仍重新读取全量原Operation/runtime来源、比较完整digest，并拒绝目标Operation未收敛；原participant必须持有READY job并重新核验完整物理/账目静止。无关兄弟工作不被伪装成空来源，旧模式保留原门禁。
- Operation范围使用原link中的producer_task_id/producer_htn_occurrence_id，核对持久列、原link_json/hash与完整Operation snapshot；不混用Operation occurrence与HTN occurrence。ORDER结算和收敛观察复用同一核验reader。旧服务rehandoff的完整历史导入仍须与最终H1对齐，来源不足继续拒绝。
- 每次Attempt未来handoff重新核对当前执行权限、原冻结manifest的精确Acceptance/输入版本、当前consumer见证与epoch；不会改成最新输入。收敛还处理真实相关service intent和创建Agent后尚未提交Turn的窗口，没有制造cancel_turn回执或推断未知账目为零。
- Selection综合Attempt分别读取原DATA manifest与原Selection receipt材料，创建、恢复、后续handoff都比较完整联合输入。原片段冻结器接受由Commit实际核验的(artifact ID,path,hash)集合，同一产物的两个不同合法mount不被字典覆盖；候选材料不会写成已接受DATA。

以上均为隔离源码WIP。只做为接线排除错误的每次1–3文件mypy、改动大文件AST及assembly import；无pytest/vitest/批量单测/回归/模型/UI，无Store/runtime启动。不是功能PASS，旧156 PASS不覆盖本次。当前SDK源码补丁63文件 SHA-256 `948c0685d80dc6e00c021a85c437f60dcb5845447b9e7491a94aeac92237a15a`，Host8文件SHA-256 `4f6d43ac44b5fbddf7ad67ffa8290551036a07855996ac734aa281b7bc97dd72`。

仍需完成最终H1稳定源码接入、完整可靠runtime/历史handoff导入、实际执行授权/物化核验端口、启用/baseline/operator真实来源，以及SDK/Host最终安装。共享完整输入版本与最终H1合同仍需对照，精确范围和Selection新接线尚未行为验收。主体完成后才进入42场景等最终统一验收。HTN最新只读快照仍inProgress，未有最终ready交接；未触碰候选/环境/DB/Host，未派工/消息/模型服务，已删除的定时任务不恢复。

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

## 已写出的主体与接线

### 本次持续编码：原Commit证明、预览来源与seed（2026-09-22，未验收）

- 原 `planning_admission_commits.py` 在真实授权/Operation/runtime复核后保留本事务检查身份，只有原PlanCommitReceipt已存在且实际ACTIVE revision匹配时写 `taskgraph-h1-applied/v1`。证书reader核对完整APPLIED detail、receipt、decision、12来源及preview身份；部署不再提供任意APPLIED成功回调。
- `taskgraph_preview.py` 固定系统候选绑定与participant工厂；`taskgraph_candidate.py` 抽出纯冻结bindings算法。原collector将H1输入与TaskGraph来源放入同一Store read_view，退出后编译，在最终admit前冻结候选；COMPILED记录preview，恢复时异身份拒绝，不覆盖旧preview。
- `taskgraph_plan_sources.py` 固定从实际Request/Decision/密封package、history、完整Task binding集合、registry/catalog/schema、Evidence/witness、Operation、接受回执、需求、预算及manifest集合生成12来源。执行policy已由固定reader读取实际持久Mission policy、部署限制、普通/selection角色权限输入、profile及路由声明；外部仍须提供最终H1可靠runtime import。完整来源未安装时拒绝current/plan/dispatch读取，不把本地空集合当来源完整。
- 独立需求reader核对原Obligation demand事件与当前账本，保留命名consumer和真实provenance。当前结构读取、通知及派发重新核验已接固定来源reader。独立需求尚未全部接入取消impact；新SHARE_ACTIVE的追溯ORDER/START约束仍需补齐。
- `taskgraph_terminal.py` 在原Task终态事件同事务记录精确Task版本/合同/输入版本/dispatch generation；结果读取不再借旧代次Task状态释放当前ORDER。该记录不是物理结算，真实Attempt/SDK/Operation核对继续必需；不影响图外原辅助Task。
- pending compound合同已贯通原preview/validate_delta、真实policy预算、原Commit与当前API；仅跳过明确未展开root的覆盖要求，PARTIAL_CHECK仍拒绝。
- `SeedStructuralReadContext` 显式读取原hierarchy的唯一compound root及实际requirements/原binding/policy；首版提交前没有revision record或历史证书。首版规划和当前只读视图支持该seed；历史请求仍必须有真实record，不伪造baseline/APPLIED。

新增 `taskgraph_execution_policy.py` 直接核对持久policy版本/hash与原runtime解析结果，读取原权限交集及普通/selection角色各自限制，不合并两类权限；profile采用原公开无凭据序列化，路由仅收集实际输入，不调用会写事件的router。固定装配的外部来源端口缩为可靠runtime导入；原执行授权/Action/物化检查仍保留。两个相关文件局部mypy通过，未做行为验收。

仅做解除接线问题的局部mypy/AST检查，修正类型变量冲突和可空事件seq；未运行任何pytest/vitest/回归/模型或原生UI。补丁含56个SDK变更文件、8个Host文件，仅用于恢复和审阅，不是功能完成度。HTN最新架构明确主体仍有H4完整11动作缺口、暂停批量验收、未通知TaskGraph ready，因此仍不采集它作为最终基线。


2026-09-22继续编码（未验收）：纠正把最终HTN接入依赖当成全体编码暂停条件的错误，继续完成不依赖最终交接的链路。没有恢复定时任务，没有派发子代理，没有运行测试套件或模型请求。

- `taskgraph_wakeups.py` 在原Store事务内保存周期唤醒事件、CONVERGE通知和下次时间；重启从持久状态继续，未ACK/租约/重试/BLOCKED不被新的周期唤醒绕过。唤醒由原调度tick驱动，不是新的进程或应用定时任务。
- `taskgraph_baseline.py` 实现已有ACTIVE图的显式捕获：原network/requirements、shape hash、本地全部运行账目、真实外部静止证明、原command receipt、revision record/pins/event及REEVALUATE一起提交。历史读取增加baseline命令、policy、Mission/revision/manifest/采集事件边界的交叉绑定；外部证明核验器现在必须接收完整 `BaselineProofContext`，不再只判断一个ref的hash。
- `taskgraph_resolutions.py` 从原GoalResolutionCommitted、acceptance commit receipt与resolution完整字节读取根目标记录；旧版本截止于下一次revision event之前，不使用当前adopted状态。不将结果引用解释为当前有效性授权。固定装配已使用实际reader。
- `taskgraph_runtime.py` 通过原SDK持久cancel_turn命令取消精确冻结Attempt，保留SUBMITTED与未知预算等待原collector；Operation通过原identity bridge选择精确Action。原ActionExecutor新增单Action核对入口，收敛不rehandoff，外部lookup后重新检查原身份与handoff ordinal。
- `taskgraph_runtime_observation.py` 对目标相关原intents读取SDK实际Agent/Turn，使用SDK自身creation identity检测创建后未绑定的窗口，核验实际物理调用及已导入用量；缺失来源/孤立turn/未结算/UNKNOWN不成为quiescent。该当前SDK适配实现仍需与HTN最终可靠导入与Operation合同对齐，不能替代H1门禁。
- 固定装配现在构造上述runtime/observer、baseline capture和resolution reader。该历史切片当时仍缺H1授权、完整preview来源、APPLIED、baseline外部proof及收敛authority；下节记录本次新增的原Commit APPLIED writer和固定preview来源管线。review/AcceptReview读取额外拒绝被fence的目标，结果保留为历史证据。
- Host补充结构化错误中的重试/修复提示与根目标结果来源展示。共享Host源码、HTN候选与运行环境未改动。
- `taskgraph_settlement.py` / `graph/settlement.py` 为 SETTLED_TERMINAL 提供真实相关工作/账目/Operation/SDK调用观察，原 ORDER gate 只消费同Mission、revision、occurrence、合同及代次的匹配事实。`taskgraph_outcomes.py` 按原接受回执、事件、ACCEPT witness与指定合同/requirements读取结果，拒绝歧义回执；接受过的ORDER事实不会仅因见证到期被抹掉，DATA/实时guard继续独立核验。
- `taskgraph_validation.py` 区分未展开compound与检查不完整；只推迟明确pending root的分解覆盖，不忽略PARTIAL_CHECK/悬空边/端口错误，拒绝无显式合同的零子节点方法。Commit participant核对真实policy预算与完整pending集合。
- 当前hierarchy从完整不可变revision record读取结构，仅叠加当前Task控制binding；不再用today方法状态重新拼图或猜根。baseline必须显式走原图捕获入口且事务内不得已有TaskGraph历史。
- support来源尚未完整时，effect set真正扩大为全部保留节点重评估，取消仍限于精确targets。通知重评估修正ReadinessReport没有to_json的接线错误，序列化完整typed read set，并记录实际评估节点；不以重评估回执冒充清除Validity dirty或新授权。
- SDK UOW新增只读完整effect inventory；Turn结束和provider用量结算后仍检查工具原始身份及实际效果，PREPARED/HANDED_OFF/PARTIAL/UNKNOWN阻止收敛。已有未知工具账目不会被未知字符串当作已完成。
- Host收敛结果独立跟踪stale，在任务事件、失败、旧水位响应时提示旧结果；why详情已展示。最新局部检查仅两个reader的mypy、前端store类型及panel语法，未执行产品功能/回归测试。


检查范围只含新增接线的局部mypy与一次assembly import（不创建Store、不启动runtime）；不等于功能验收，未运行pytest/vitest/全量/回归/真实模型/UI。此前源码补丁检查点（已被顶部记录替代）：SDK `6119806eb511216bb966d5bd24c1f2bc38c5e536d547dc96c32c0c3a4275cb31`；Host `4f6d43ac44b5fbddf7ad67ffa8290551036a07855996ac734aa281b7bc97dd72`。后续源码变化应重写补丁及此hash，不能沿用本检查点宣称验收。

2026-09-22后续编码：用户要求取消 `htn-taskgraph`，已通过应用工具删除（`deleteStatus=deleted`）；不再依赖定时唤醒，也不得自动重建。以下均为当前会话的隔离源码实现，未经整体验收。

- 新增 `artifacts/taskgraph_inputs.py` 的严格冻结manifest与TargetRules解码，恢复/原workspace消费读取实际Attempt输入；原ReviewPackage、AcceptReview及输出端口绑定实际Attempt的原图，不因后续消费者变化改写旧工作合同。
- `taskgraph_store.py` 验证完整祖先链；`graph/revision_events.py` 固定新事件全部身份字段与effect_set_hash；原 `PlanRevisionCommitted` 内容保持。Attempt原始身份hash、原图pin与当前控制在后续Provider handoff再次核对；普通和预算派发路径均已接校验。
- `taskgraph_candidate.py` 在preview冻结前去除原network的adoption装饰，并用原 `_superseded` 预测保留节点输入替换后将持久化的版本/代次；Commit重算并验证实际写入，不能提交后修改候选hash。
- `taskgraph_notifications.py` 提供三个固定消费者、原事件投影、cursor原子推进、同一消费者实际回执重放，以及单Mission来源故障隔离。原Intent结算、预算释放、Action交接和Knowledge变化纳入唤醒；最终HTN事件目录仍需接入核对。
- `runtime/taskgraph_local_work.py` 读取全部原Attempt/intent/预算占用/Provider账目，不用旧reader遗漏状态的SQL筛选；收敛宣称quiescent前额外核验目标Task相关本地工作与账目。该本地读取不替代H1真实外部回执和Operation核对。
- `taskgraph_execution_sources.py` 从实际hierarchical/world/Store/BudgetLedger读取绑定、方法登记、witness/START/licence、epoch、AcceptedOutputs、输入policy、TargetRules、Obligation及预算/部署声明，并绑定完整集合digest。类型明确为 `LocalExecutionSources`，尚不能冒充完整ExecutionReadContext或执行授权。
- `taskgraph_assembly.py` 新增固定部署装配，将dispatch、三个通知消费者、只读API和policy服务作为整体构造；H1原`commit_planning_revision`已接入同事务participant factory，保留已有Commit重放优先。当时真实authority/来源/plan participant为必填端口；本次已将participant改为SDK固定工厂，外部仅供应可靠导入及权限核验，执行政策由固定reader读取真实来源；装配函数存在不代表最终部署已安装。convergence diagnostics直接引用原持久followup并重新核对ACK的实际消费者回执，不用空列表代替来源失败。
- 独立Host工作区 `body-implementation/snapshot/host` 已接原 `/ws/control` 四个只读verb、service/handler、严格前端模型与MissionsView执行图面板，含层级/执行依赖、历史、why、diff及convergence。错误保留stale画面、同token校验、跨任务/连接/现有身份消息清空状态。共享Host未修改，未启动或安装此副本，未做原生UI验收。

新增检查仅限为接线消除类型/语法问题的少量静态检查，未运行pytest/vitest/回归/全量或模型调用。旧156项成绩不覆盖本节。可审阅代码补丁为 `BODY-WIP-SDK.patch` / `BODY-WIP-HOST.patch`；它们是未验收工作检查点，不是可直接覆盖dirty最终源码的发布补丁。

- `graph/revision_records.py`、`storage/taskgraph_store.py`：完整revision record写入/读取、证书union、精确原plan/requirements/pins/history sources、真实plan receipt和APPLIED check核验；写入加入原Store事务，不提交外层。
- `graph/attempt_inputs.py`、`storage/taskgraph_attempt_inputs.py`：每个Attempt独立记录冻结manifest、intent、contract/input/generation身份；历史恢复不resolve latest。
- `graph/notification_contracts.py`、`storage/taskgraph_followups.py`、`orchestrator/taskgraph_followups.py`：三种通知、去重/异payload冲突、版本CAS领取、实际下游记录后ACK、1/2/4/8秒退避/第5次BLOCKED；原scheduler_state事件水位和派生通知同事务推进。
- `graph/convergence.py`、`storage/taskgraph_convergence.py`、`orchestrator/taskgraph_convergence.py`：共享consumer保留、精确取消targets、状态CAS、READY仍保留fence、原取消/核对接口的事务外推进；reverse DATA需要重验与真正取消分开。尚无完整support集合producer，因此effect coverage明确为CONSERVATIVE。
- `orchestrator/taskgraph_policy.py`、`orchestrator/taskgraph_sources.py`：认证装配的一次性policy启用事务、显式baseline调用、policy/receipt一致性和结构来源读取。实际H1部署验收与完整baseline/quiescence仍为必需的未接适配器。
- `observability/taskgraph_replay.py`：只创建新离线纯图验证库，保留完整NetworkDocument并重建投影；源端使用同一read_view；失败只清理自己创建的目标，原已存在目标不会被删。明确不授权runtime恢复。
- `api/taskgraph.py`：固定tenant/principal只读snapshot/why/diff/convergence；历史图不可执行；当前图需完整运行来源验证；why包含同次read_set。当前binding控制变化与历史结构分开，不拿最新binding改写历史。
- `orchestrator/taskgraph_plan_commit.py`已接入原`plan_commits.py`与`planning_admission_commits.py`：启用kernel时必须有同Store真实participant；在原事务检查preview、重算impact、写真实Commit后图记录/通知；仅撤回系统算出的targets，保留共享任务。APPLIED writer已由原H1 Commit入口绑定，只消费本事务真实检查结果和原PlanCommitReceipt；缺少成功检查或直接绕过H1入口则整个事务拒绝。
- `orchestrator/taskgraph_dispatch.py`已接入原`CommitService.create_attempt`：在预算预留/Attempt创建的原事务重新读取typed readiness与冻结manifest，并持久保存Attempt关联。原event_handler派发、Provider权限检查、工具执行与Action begin_handoff已加未来handoff fence；不改变已发请求的事实。

## 接下来必须完成，不能以接口存在代替

1. HTN正式完成后优先capture最终源码并核验源字节、migration与接口；新源字节使用新codec manifest版本，不能覆盖已测试v4。重新采集期间源码变化则该采集不具验收资格，不要求HTN停工。
2. 当前固定runtime历史导入、执行资源/物化、baseline、收敛证明和operator服务已写隔离源码，但仍需最终H1合同对齐；实际deployment acceptance reader尚未安装，必须读取真正完成的部署收据及匹配源身份，不能用布尔值或局部测试数代替。
3. 将dispatch、通知、policy/operator及read API固定装配接到最终SDK/Host部署；核对原事件目录、Service rehandoff、Action负证明、当前权限、模型profile和隔离迁移，做主体跨层完成审计。真实权限/验收缺失仍明确拒绝。
4. 将独立Host transport/store/UI接到最终安装SDK；只读视图与内部operator命令分开，保留认证tenant/principal；不得覆盖共享Host已有dirty修改。
5. 上述主体完成后才开始正式TG-A至TG-E、42场景/12变异/stateful/旧模式/H1/真实模型/原生UI及独立审查；按实际结果修复并回写架构，达到完成条件后默认开启。当前不提前跑这些验收。

SOURCE_MAP_COMPLETE、H1_H_READY、TG_A_VALIDATED仍未关闭。没有发布、合并、安装wheel或覆盖HTN候选。

2026-09-22同轮后续（未验收）：新增共享slot逐项核对原方法DATA声明与保留生产者的完整入边及policy，拒绝借旧边掩盖新slot漏声明输入。修复产品run-loop对持久WAITING/READY收敛job的空闲判断：上一通知ACK与下一次唤醒之间仍保持可推进工作；BLOCKED通知不获得自动重试。唤醒沿原scheduler tick，不是Codex定时任务。相关单/双文件静态检查通过，无行为测试。最终HTN仍无ready交接，当前补丁SHA以上方最新值为准。

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


2026-09-23 最新：候选 16 增加原来源/审批变化通知并修正 LOST/TIMED_OUT 的物理占用保留；已核对在途取消和 UNKNOWN 冷恢复的具名局部路径。主体及正式整体验收仍 IN_PROGRESS，详细源码、失败、哈希和覆盖边界统一见 [执行日志](EXECUTION-JOURNAL.md) 的候选 16 条目。


2026-09-23最新：候选19修复恢复前可信装配顺序，原Worker强退收集/账目恢复及Host首启/rebuild保留UNKNOWN状态的窄路径通过；主体完整性审计继续，整体未完成。来源身份与边界统一见[执行日志](EXECUTION-JOURNAL.md)候选19条目。
