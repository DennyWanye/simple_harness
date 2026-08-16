# Slice B — Product SDK adapters and closed-ingress composition

> 状态：FINALIZED — 已获用户批准，执行仍依赖 Slice A receipt  
<!-- plan-status: finalized -->
> 仓库：`/Users/denny/projects/simple_harness`  
> AC：SDK-AC-5/6/7/8 的 consumer Adapter 部分  
> 风险面：3（Harness、Provider、权限）  
> 明确不做：不切 text/voice/background ingress，不执行真实开发数据 reset，不删旧 authority

## 出口与失败边界

本 slice 在独立临时 user-data 中证明产品可只用 Slice A exact wheel public API 组装完整 SDK
Runtime/Workflow/Adapters，并把 DeepResearch/PPT 迁成 SDK engine 上的 host-owned definitions。
production `main.py` ingress 继续使用旧 stack，避免中途双 authority。任一 Adapter/conformance/readiness/
产品 Workflow 回归失败，slice B 不出 receipt，slice C 不做原子切换。

## B1 — Pin exact candidate and split SDK/product storage ownership [AC-7,8]

- 从 Slice A receipt 读取 version/SHA/BUILD_INFO，不接受手填或 path/editable/PYTHONPATH；vendor
  `backend/vendor/simple_harness_sdk-0.1.1-py3-none-any.whl`，更新 `backend/pyproject.toml`、`uv.lock`
  与 PyInstaller inventory。
- `sdk_adapters/runtime_paths.py` 只从显式 product user-data root 解析
  `<user-data>/data/simple-harness-sdk/execution-v1.sqlite3`；拒绝 home/repo/evidence root、symlink escape、
  traversal、Product `state.db/workflow.db` 重用。
- 新增 `product_state/{schema,database}.py`，唯一解析
  `<user-data>/data/product_state.db`；`ProductStateDatabase.initialize()` 用 `BEGIN IMMEDIATE` 原子安装
  product schema v1（capability v2最终DDL、policy、legacy import、扩展TaskGrant、authorization saga/
  receipt），设置 `foreign_keys=ON`、WAL、`synchronous=FULL`、`busy_timeout=5000`，执行
  `foreign_key_check` 后才提交 `user_version=1`。失败rollback且ready不发布。
- `CapabilityStore` 改由 `ProductStateDatabase` 拥有并迁移schema；`bind(db)`只接受product transaction，
  不再接受SDK/旧execution connection。`CapabilityBuilderHost`直接接收同一`CapabilityStore`，不再从
  execution UoW隐式构造。SDK DB只含Run/decision/effect/checkpoint/delivery；product DB只含capability/
  policy/TaskGrant/saga；SessionDB仍独立。
- 历史开发数据不迁移：Slice C获批reset清空frozen oracle所指的exact旧执行库
  `<user-data>/data/workflow.db`及WAL/SHM。新SDK execution与`product_state.db`均首次创建为空库，不是
  reset target。built-in/configured catalog冷启动重导入；`permissions_auto_mode.json`只走现有一次性
  配置导入。
- 测试 clean `uv sync --frozen`、installed distribution origin/hash/version、三个DB相互absence、schema
  first-open/reopen、migration rollback、two-owner fencing与partial-init close。

## B2 — ProductSdkRuntimeStack and readiness barrier [AC-5,7,8]

- `sdk_adapters/composition.py` 返回 product-owned lifecycle facade
  `SdkRuntimeReady(generation,runtime,client,workflow_catalog_digest,workflow_registrations,ready_at)`；
  `ServiceContext` 只新增这个不可变单 slot，不发布多个可相互漂移的 boolean/object。内部只 import SDK public symbols与
 本目录 product adapters，禁止旧 `HarnessRuntime/ProductHarnessUnitOfWork/kernel._uow/reconciler`。
- 构造顺序：验证已有登录态 keychain/provider registry与SessionDB → 初始化product-state DB、capability
  platform、permission runtime与product Workflow definitions → SDK DB/UoW → Ports → frozen profile/definition registry →
  Runtime → `await start()`。所有对象先留局部变量；全部成功才原子发布 `sdk_runtime_ready`。
- provider registry 只要求结构加载与session-binding reconcile完成；某个 credential 当前不可调用不
  是启动阻断。动态按 Run 重读 provider chain/keychain/permission/capability catalog；只冻结进入该 Run snapshot 的
 值，不在启动时永久缓存默认值。失败逆序 close delivery pump→Runtime→Workflow resources→DB；
  ingress barrier保持 closed，FastAPI lifespan fail-fast或health/ingress明确503，不继续旧 ReAct；retry
  只能在 lifecycle lock + ingress closed 下创建全新 generation/Runtime并从头重读依赖。
- 测试 dependency missing、delayed ready、first failure then retry、double start/close、close during
  attempted ingress、partial-build leak、同一 ready barrier 被 text/voice/background observer 读取。
- 现有顺序基线：SessionDB init `main.py:3980-3986`；provider registry/session binding
  `:3987-4019`；旧 WorkflowService 在 adapters 完成前提前发布 `:4069-4297`；DeepResearch/PPT
  activate/recover `:5106-5481`；capability/permission authority `:3675-3968`；旧 Harness 最后启动
  `:9309-9360` 并在 companion bind 后开放 `:9363-9414`。实现必须消除 `:4293-4297` 半初始化可见
  状态和 `:5491-5501` 失败后继续旧 ReAct 路径。

## B3 — Provider, Tool and reconciliation adapters [AC-5,8]

- `provider.py`：product selected provider/model/base URL/keychain secret → SDK ProviderTarget/Secret；
  在 Run start 冻结 provider/pricing snapshot，使用 Slice A policy fingerprint；401/402/408/429/5xx/
  timeout/cancel 分类，secret/provider body 不入 ledger/log/report。
- `tools.py`：把可信 ToolSpec/handler 注册为 SDK ToolRegistry，保留 reserved host fields、schema/长度、
  五态与 catalog generation；control calls 只来自 SDK child/authorization contracts。
- `backend/deskpet/tools/__init__.py`变为无副作用exports，删除import-time `pkgutil`扫描；新
  `tool_catalog/{contracts,builtin}.py`定义frozen`ProductToolRegistration`与显式provider manifest。
  每个产品Tool模块暴露`registrations(deps)`且模块顶层不碰singleton。composition在provider/permission/
  capability ready后逐项加载required modules，import/registration失败直接阻断ready，禁止log-and-skip。
- `tools/registry.py`拆除generic dispatch/effect/UoW职责：ToolSpec、PreparedToolCall、Outcome、EffectPolicy、
  ToolInventoryEntry改用SDK public contracts；产品只保留handler/schema/permission/category/source/
  build identity metadata并由`sdk_adapters/tools.py`转换注册。`orchestration_controls.py`、
  `project_group_send.py`等顶层旧Workflow contract imports同步迁移；`research_tools.py/ppt_tools.py`只import
  B5的新`product_workflows` helpers，不能经package auto-discovery拉回旧authority。
- 把pre-cutover可达registration matrix冻结为受审阅intended manifest：base44、code10、context1、
  memory-recall1、orchestration6、OS16（`web_fetch`去重）、deferred新增2，合计79 unique。最终SDK Tool
  catalog为77 keys：`deepresearch`和`ppt_pro`不再作为direct bypass Tool，分别映射required
  `workflow.deep_research`和`workflow.presentation`；`ppt_create`保留。每项比较name/schema/toolset/
  permission/dangerous/visibility/timeout/concurrency/source/version/build/resource/effect/outcome/dispatch/
  lifecycle/presentation metadata；`stubs.py`不进production，缺required provider fail startup。
- 对真实77 schemas先跑SDK validator全量审计并冻结所有`tool/path/error`，禁止逐个撞错。普通object发布
  新spec version并显式`additionalProperties:false`；`process_start/app_launch`等环境map使用Slice A新增的
  typed bounded scalar-map（key pattern、maxProperties、string maxLength），不得允许裸`true`。每个迁移
  记录old/new canonical hash、兼容理由与真实handler等价test；无法安全表达的开放对象改成显式
  key/value array并更新同一Tool的迁移说明，不放宽SDK全局validator。
- 已知批量审计结果固定为63项原样通过、14项显式迁移：environment map=`app_launch/process_start`；
  bounded key/value=`capability_build`；exact-length+handler validate=`capability_repair/download_file/
  move_file`；bounded JSON string=`doc_create/doc_edit/excel_create/ppt_create`；minimum+handler validate=
  `window_capture/window_focus/window_key`；omitted-or-string=`workflow_spawn.workspace_ref`。实现时必须核对
  临时report的全部old/new hash并checked-in，不可重新凭感觉分类。
- `main.py`顶层不得import`ppt_tools`等provider；`research_tools`的semantic/workflow globals、
  `project_group_send` transport setter等改factory显式依赖。builtin catalog验证完成后一次性sealed；
  SDK projection、authorization view、UI projection共享同一generation/digest。
- `reconciliation.py`：Tool/Provider/startup reconciliation；handed-off/unknown 只有 Host evidence 为
  `confirmed_not_started` 才允许新 attempt，否则保持 unknown，不盲重放。
- 测试 one/multi Tool、invalid/oversize/duplicate/late/cancel、provider physical count、unknown crash
  windows、pricing unknown refuse、secret canary；使用真实SDK UoW/Driver，仅物理边界double。另冻结
  pre-cutover完整Tool inventory的name/schema/handler/build/permission hash，新进程正常import
  `deskpet.tools`断言零provider import/注册/DB/config/keychain/network/thread/task/旧Workflow module；逐个
  handler import同样零副作用。显式装配后77 Tool+2 Workflow mapping与79项manifest逐项parity且无
  silent skip；schema migration manifest逐项old/new hash可追；重复名/metadata缺失/required provider
  异常均不发布partial ready。
- 合规spike见`spike-tool-import-detachment.md`：真实pre-cutover registry/family builders已导出
  79 identities→77 Tools+2 canonical Workflow mappings，真实metadata manifest SHA-256=
  `891ae13615229ee98715f8b18f39a5a045c1f995a29e984a4b86c4eaa2f310bf`。最终PASS还要求77个真实
  schemas/sidecar已注册到SDK ToolRegistry、sidecar hash一致并实际调用sync/async/context/staged/control/
  provider六类真实handler，证明依赖反转可行；其余71项仍需正式机械绑定，未被spike冒充完成。

## B4 — Authorization, context, personal and capability host [AC-5,6,7]

- `authorization.py`：现有 `PreparedAuthorizationRuntime` 只读取product repository的policy/TaskGrant；
  `admission.py`返回`PreparedTaskGrantIntent`，不再要求旧execution UoW同事务写grant。legacy
  `PermissionGate`只作镜像不能成为authority。`task_grants.status`扩为
  `prepared|active|expired|revoked`，带version/creator/timestamps；查询授权只返回active且未过期行。
- 新增 `product_state/authorization_saga.py`。稳定`authorization_id`冻结exact request fingerprint、
  principal/session/root/run/call/effect/tool/args/capability/schema/scope、TaskGrant id/version/fingerprint、
  policy generation、decision nonce/version和run/execution lease fences。product durable states固定为
  `prepared → decision_bound → effect_bound → handoff_committed → settled`，允许handoff前转
  `aborted|expired|revoked|quarantined`；handoff后只可`dispatch_unknown → settled|quarantined`。
- 所有product mutation使用`expected_saga_version` CAS；同identity+receipt hash幂等返回原receipt，
  同effect/call但identity或hash不同永久quarantine。SDK DB拥有decision/effect/continuation/exact grant；
  product DB拥有policy/TaskGrant/saga。两边各自receipt存canonical SHA-256并交叉绑定，任何一库单独
  的ALLOW/active状态都不能调用物理Tool。
- manual顺序固定为：product prepare(P1) → SDK decision allow但Run保留binding blocker(S1) → product
  激活grant/decision_bound(P2) → SDK消费blocker(S2)。effect顺序固定为SDK PREPARED(S3) → product
  effect_bound(P3) → SDK HANDOFF_PENDING且尚未调用(S4) → product handoff_committed/consume receipt(P4) →
  SDK验证双receipt并HANDED_OFF(S5) → 唯一物理调用 → SDK terminal/unknown(S6) → product settle(P5)。
  auto policy跳过S1/P2 decision绑定，但不能跳过effect双receipt门。
- S5之前crash/cancel/expiry/permanent CAS conflict必须`confirmed_not_started`且物理调用=0；S5之后崩溃
  一律`UNKNOWN/dispatch_unknown`并要求Tool reconciliation，不因revoke/补偿推断未执行。S1有P2无、
  P4有S5无均以原receipt补齐；receipt mismatch或product row缺失永久quarantine。startup同时扫描
  product reconcilable saga、SDK authorization waits和nonterminal authorized effects直至收敛。
- `context.py`：ProductTurn preparation/memory/tool/catalog facts → typed RunStart/Context；ReAct messages与
  checkpoint 只写 SDK `SqliteContextPort`，SessionDB只保存用户/助手 transcript投影。
- `personal_catalog.py`：读取受信 candidate descriptors并绑定 frozen graph/owner/version/fingerprint；
  不调用 `ModelPersonalWorkflowMatcher`，不按关键词/正则语义选择。
- `capability_host.py`：catalog search、source policy、isolated build/test、package store、activate/rollback、
  authorization receipts；成功能力同 Run refresh并默认 active。
- fault hooks逐写点覆盖`after_product_prepare/sdk_decision_bind/product_decision_bind/sdk_effect_prepare/
  product_effect_bind/sdk_handoff_intent/product_handoff_commit/sdk_handoff_commit/handler_return/
  sdk_effect_settle/before_product_settle`；每点kill/reopen两个DB，断言receipt唯一、S5前call=0、S5后
  call≤1、cancel/expiry/policy drift/revoke/permanent conflict按唯一分支收敛且无常驻nonterminal saga。
- 测试 Tool permission approve/deny/cancel/restart、candidate forgery、capability miss→build→active、
  install idempotency/restart；missing optional port只影响对应 Profile。

## B5 — Version and migrate product-owned DeepResearch/PPT to SDK engine [AC-6,8]

- 新建互不覆盖的`backend/deskpet/workflows/definitions/sdk_v7/deep_research.py`与
  `definitions/sdk_v2/ppt_pro.py`；旧v7/PPT v1字节、manifest/fingerprint在迁移前保持不变。新模块只
  import SDK public definition/contracts，产品Ports经`backend/deskpet/sdk_adapters/workflows.py`注入。
- 把仍有价值但当前反向import旧`workflows.contracts`/旧v1 graph的host business helpers迁到
  `backend/deskpet/sdk_adapters/product_workflows/{research_ports,research_stages,payloads,ppt_nodes}.py`：
  `research_core.py`的typed Ports/stages、`deep_research_nodes.py`payload codec、`ppt_pro_nodes.py`和PPT
  复用的research handlers/constants均在此归宿。它们只import SDK public identity/context/StatePatch和
  product tools，不得import旧definitions/contracts/control/definition；新sdk_v7/sdk_v2 definitions也只
  import该包。禁止在runtime改写module globals来伪装SDK类型。
- 只迁当前生产功能：DeepResearch发布新cutover identity `deep_research@v7-sdk1`；PPT现有v1的
  interrupt handler在回答后写product store，不满足SDK pure pre-interrupt合同，因此新建
  `ppt_pro@v2`，把`wait_outline_decision`缩为纯barrier，把回答后的outline-store读写移到独立
  idempotent effect节点。不得给现有v1补假`pure`或覆盖其manifest/fingerprint。
- 两图只import `simple_harness.workflow` public construction API。DeepResearch v7的
  `normalize/finalize`纯，`plan/search/synth/persist`经typed Host Ports；无selector/interrupt。PPT v2的
  7个只读selector逐一声明`selector_effect_policy="pure"`，interrupt声明
  `pre_interrupt_effect_policy="pure"`；所有LLM/search/fetch/blob/artifact/notifier/render/evaluator/
  receipt effect带run/node/effect idempotency key。
- 新模块handlers直接返回SDK public`StatePatch`；`ResearchPorts` Adapter实现产品精确typed classes。
  pure selectors接收SDK frozen JSON并显式`thaw_json()`后调用复用逻辑。DeepResearch terminal envelope
  改成bounded summary+artifact/blob ref（depth≤8），完整report hash与可打开artifact/ref进入oracle；
  不放宽SDK全局payload depth安全上限。
- 把 `backend/deskpet/harness/adapters/product_profiles.py` 的 DeepResearch/PPT descriptor/payload factory
  迁到 `sdk_adapters/workflows.py`，用 Slice A public host-definition registration只注册
  `workflow.deep_research`→`v7-sdk1`与`workflow.presentation`→`v2`。catalog/descriptor显式记录
  cutover epoch与manifest fingerprint；无旧engine wrapper或隐藏legacy recovery registry。
- `backend/deskpet/workflows/{contracts,control,definition,errors,lease,native,recovery,replay,runner,
  execution_ports}.py` 的 generic authority 不进入新 stack并在Slice C删除；
  `WorkflowService/WorkflowLauncher` 的 generic runner/checkpointer/recovery ownership 不进入新 stack；
  existing delivery handlers/content resolver/research/search/fetch/PPT artifact components作为 typed Host
  ports 注入。Slice C 才删除旧 owner。
- 当前第二套 factory authority 位于 `workflows/runtime_adapters.py:43-225`，main 手工注册
  DeepResearch `:5106-5158`、PPT `:7276-7355`；这些改成不可变 SDK registration，不能继续包裹旧
  registry/runner。
- Slice B在closed-ingress下只新增新模块、不删旧模块。Slice C parity通过且获批SDK DB reset后，删除
  production旧`definitions/v1..v7`及其generic contract依赖；旧源码只由Git pre-cutover commit/history
  归档，不复制到runtime package或测试fixture。acceptance明确不迁开发历史Run，因此不保留旧engine
  recovery；cutover audit断言production package零旧definition import/registration。
- 真实detachment gate在临时环境把旧definitions/contracts/control/definition从import path屏蔽，断言
  `sys.modules`零旧模块；exact identity=`v7-sdk1`，PPT interrupt只经Slice A public
  `resolve_and_resume()`，不得低层UoW commit+resume。必须通过真实无副作用`deskpet.tools` import与
  显式完整inventory装配；禁止手工向`sys.modules`注入fake tools package。
- 测试active catalog精确为DeepResearch v7-sdk1/PPT v2；agent child ticket→DeepResearch/PPT SDK
  Runner；真实handler compile/register/start/fault/reopen，PPT interrupt answer只经public
  `resolve_and_resume()`；DeepResearch continuation/source/citation/
  terminal delivery；PPT editable/full-page/output path/ArtifactCard；crash/reopen不重复 provider/tool/artifact。
- 复用强 oracle：`test_workflow_bootstrap.py:9-22`、
  `harness_simplification/test_workflow_execution_seams.py:1337+`、
  `harness_simplification/test_product_workflow_profiles.py:179-195`、
  `test_model_workflow_spawn.py:535-616`、`test_workflow_deep_research_v2_recovery.py:26+`、
  `test_deep_research_v6_production_runner.py:686,913`、`test_workflow_ppt_pro_graph.py:799-908`、
  `test_workflow_v6_delivery_receipts.py:90-205,299+`。
- 技术spike见`spike-product-workflows.md`：临时public patch下DeepResearch完成后重开稳定；PPT在真实
  interrupt处close/reopen/resolve/resume完成，旧13个effect零重交。该spike只证明拓扑和SDK恢复路线，
  不替代本slice真实LLM/search/render/Artifact门。
- 真实handler spike见`spike-real-product-handlers.md`：真实节点与exact typed Ports已过类型/执行边界；
  PPT v2 pure interrupt close/reopen/resume闭环；DeepResearch terminal payload depth已用扁平Blob/ref
  adapter复跑到completed/reopen，不再存在未知`isinstance`或terminal阻断。
- 最终detachment spike见`spike-workflow-detachment.md`：旧definitions/contracts/control/definition均被
  import guard物理屏蔽且`sys.modules`为零；独立sdk_v7/sdk_v2用exact identity、真实typed calls/BlobStore、
  public`resolve_and_resume()`完成terminal/reopen，旧physical calls零重放。BlobStore与PPT outline核心
  逻辑必须机械迁入consumer新模块，不能重新引用旧workflow package。

## B6 — Real product conformance with ingress closed [AC-5,7,8]

- `sdk_adapters/conformance.py:build_host` 实现 Slice A exact `ConformanceHost`，每个 suite使用独立 temp
  user-data/DB并在 async context exit完整 close；不得返回 None、skip required case或导入 SDK tests。
- exact wheel clean environment运行 provider/tool/runtime/workflow四 suite；report metadata中的 artifact
  SHA 必须等于 vendor bytes。
- 新 cutover-preflight audit：new composition/Adapters 不导入旧 generic authority或SDK private symbol；
  old production ingress此时仍可存在，但不得被 new stack包装或调用。
- slice B baseline复用 program baseline并补 adapters/DeepResearch/PPT targeted pre-change run；post-change
  必跑 backend affected tests、SDK conformance、frontend/Rust compile smoke与full-surface inventory。

## Slice B machine gate

- 独立 manifest/run-dir/plan challenge/auditor/receipt；testcase oracle在实现前冻结。
- 完成判据：temporary closed-ingress stack从 public exact wheel start/recover/close；四 suite真 PASS；
  DeepResearch/PPT只用 SDK generic engine；无 secret泄漏、无新 baseline failure。
- receipt 后只把 exact stack contract和candidate SHA交给Slice C；不把“Adapter测试绿”宣称为产品切换完成。
