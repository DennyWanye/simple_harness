# Slice C — Atomic product cutover, authority retirement and E2E

> 状态：FINALIZED — 已获用户批准，执行仍依赖 Slice A 与 B receipts  
<!-- plan-status: finalized -->
> 仓库：`/Users/denny/projects/simple_harness`  
> AC：SDK-AC-5/6/7/8 的 production cutover/E2E 部分  
> 风险面：3（Harness、Product Session、UI/desktop runtime）  
> 不可逆边界：真实开发执行库 reset 与远端 Release 均需单独批准

## 出口与失败边界

本 slice 必须在同一个 production commit 中切换 text、voice、companion background 与 terminal
delivery，随后退休旧 generic authority；不允许 chat-first、fallback flag、shadow双跑或 default-OFF。
自动化和桌面矩阵全部required PASS 才可请求远端 v0.1.1 发布批准；任何 PENDING/BLOCKED 不能写完成。

## C1 — One ingress facade and deterministic identity [AC-5,6,8]

- `sdk_adapters/ingress.py` 是唯一 start/signal/cancel/query/recover facade；ProductTurn先由产品拥有的
  Session/turn preparation生成，再转 SDK `RunStart`。确定性 root identity算法与当前 client
  request/session/turn语义保持兼容；same request replay只返回同一 root。
- `backend/main.py:_build_product_harness_stack` 替换为 Slice B `build_product_sdk_runtime()`；
  `_harness_runtime/_harness_venue/_harness_accepting` 收敛为单个 `SdkRuntimeReady|None` + ingress
  barrier，不再导入 `root_run_identity/HostContext/ProductVenueRunResult`或访问`.kernel/.reconciler`。
- `/ws/control` text/chat_v2、voice host、companion `BackgroundRunAdapter` 同一次提交改取同一
  `ready.client`；HITL continuation、cancel与supervisor recovery也只经facade。
- required Adapter/definition/Port缺失时lifespan fail closed或入口503，不回旧 Harness。测试三个入口
  各创建最小root、same request replay、continuation FIFO、cancel/query/recover与ready generation CAS。

## C2 — Exactly-once product delivery projection [AC-5,8]

- `sdk_adapters/delivery.py` 将 SDK terminal/outbox event 映射到 product
  `RunPresenter/SessionDB/WebSocket/ArtifactCard/TTS/companion growth`；SDK Slice A pump是唯一 generic
  dispatcher owner。
- live presenter 与 restart backfill共用 `(delivery_id,idempotency_key,session_epoch)`；SessionDB追加、
  artifact projection与websocket notify分层：durable transcript/projection先幂等提交，在线 notify
  best effort，不因无 websocket 重投 durable content。
- session tombstone/epoch/bound-state每次delivery尝试动态读取；terminal generation/fence不匹配
  quarantine。测试 live+backfill竞争、sink crash after SessionDB before settle、websocket absent、
  duplicate terminal/artifact、session reopen/supervisor restart；每个root最终一条assistant terminal。

## C3 — Retire duplicate generic authority without feature loss [AC-6,8]

- 以 `cutover-manifest.md`/symbol disposition为准删除或迁移 product generic：Harness
  `bootstrap/kernel/kernel_terminal/runtime/drivers/child/reconciler/effect/projector/live_index/
  admission/start_snapshot/user_continuations` owners，以及 product Workflow
  `contracts/control/definition/errors/lease/native/recovery/replay/runner/execution_ports` owners。
- 保留 product-owned Adapter/Turn preparation/Session/UI/provider/tool/permission/capability/companion及
  新`definitions/sdk_v7`与`definitions/sdk_v2`；旧`definitions/v1..v7`、
  `WorkflowService/Launcher/runtime_adapters/product_profiles` generic composition退役，必要产品物理
  handlers与business helpers移入`sdk_adapters/product_workflows/` typed Host ports/stages。旧源码只留
  Git pre-cutover history，不复制到runtime/fixture；新模块AST/import test禁止旧contracts/control/
  definition和module-global monkeypatch，因此不会反向保留旧type authority。
- 删除 `ModelPersonalWorkflowMatcher` production wiring、ticketless child、legacy router/fallback；
  tests不再断言`kernel/1` owner或`NotImplementedError/None/skip`。
- `scripts/acceptance/assert_sdk_cutover.py` 做AST/import/runtime manifest检查：production零旧owner、零
  SDK private/deep import、零matcher/router/ticketless/fallback；五个product Workflow profile来自一个
  SDK catalog；active product definitions精确为DeepResearch v7-sdk1/PPT v2。获批reset删除旧workflow
  DB中的engine checkpoints；production同时零旧reader/registration且不存在隐藏legacy recovery registry。

## C4 — Audited reset and frozen backend [AC-7,8]

- `scripts/dev/reset_sdk_execution_data.py` 默认dry-run，输出resolved path/inventory/size/SHA/backup
  index与一次性nonce；把冻结“exact dev execution DB”精确绑定为
  `<user-data>/data/workflow.db`及WAL/SHM，只允许该旧schema 1–29目标；明确拒绝新SDK execution DB、
  `product_state.db`、Product SessionDB、root/home/repo/evidence、symlink/traversal。
- 实际开发数据reset不由本计划自动推断授权：应用必须停止；先向用户展示旧workflow DB dry-run/
  backup hash与exact target，得到单独确认后才删除。新SDK schema与`product_state.db`已由Slice B在
  独立路径clean-create；不搬旧Run/capability/policy/TaskGrant行，built-in/configured capability catalog
  重导入。未确认保持PENDING。
- frozen backend从vendored 0.1.1 exact wheel构建；日志只输出SDK version/wheel SHA/Runtime owner/
  schema version，不输出secret。PyInstaller module inventory含SDK和product adapters，零旧generic owner。
- clean user-data首建schema、close/reopen、supervisor restart；若真实reset获批，reset后同样复验。

## C5 — Automated fault and regression matrix [AC-5,6,7,8]

- SDK candidate full suite与Slice B conformance重跑；product adapter/contracts、cutover audit、reset
  negatives/temporary positive、secret scan、text/voice/background/API full-surface、DeepResearch/PPT、
  frontend vitest/typecheck/lint/build、Rust tests/check、frozen backend startup全部入账。
- SDK-S6：malformed/unknown/duplicate/out-of-order/oversize/refuse-tool；校验no wrong correlation、no
  unbounded context/log/DB、诚实终态。
- SDK-S7：Provider/Tool/authorization/delivery在prepare/handoff/receipt/settle前后crash；reopen后
  physical call/projection严格不多于一次，unknown只经explicit reconciliation。
- 与`baseline.md`比较：8个原绿色分片必须保持绿；9个known-failure可转绿但不得出现新fingerprint。

## C6 — Value smoke then true desktop E2E [AC-5,6,7,8]

- 昂贵矩阵前先跑自然语言 SDK-S1直接回答、SDK-S2单Tool、SDK-S3 durable_task Run A；每个都必须
  有真实provider、非空有效业务结果、人工质量检查、SDK root/child/effect/provider/delivery对账。
  任一失败立即早停，不继续打包/全矩阵。
- 按 frozen `manual-test.md` 真实桌面点击/输入 SDK-S1..S5；动作前记录
  `坐标=(x,y)|动作=...|期望=...`，截图与原始日志只进`.local-test-evidence`。禁止WS注入、pytest、
  backend import或脚本回放替代UI。
- S3/S4/S5各两个独立root；S3第二root在≥10轮历史，另跑HITL拒绝/取消；全部做supervisor restart
  对账。至少一个required root由真人驾驶；若希望全AI驾驶，必须先记录用户明确批准hash。
- Provider `/models` 是model name authority；执行前验证实际可用模型，不沿用handoff中的拼写猜测。
- 另跑两个required入口UI root：真点击麦克风触发voice root并完成terminal；真点击/配置Companion可见
  入口触发一次background root并观察可见projection。两者都要与同一`SdkRuntimeReady` generation、
  SDK root/delivery对账；仅脚本调用voice/background adapter不算UI证据。

## C7 — Facts, audit, receipt and release approval [AC-8]

- 每个case记录Session/root/child/effect/provider/decision/delivery IDs、证据相对索引与SHA-256；Git不含
  截图、录屏、原始日志、DB/receipt/diagnostic archive。
- 独立auditor做AC→code→testcase→primary evidence追踪、入口inventory、old authority absence、
  committed-HEAD clean revalidation。发布前先生成 **C-prepublish checkpoint**：除SDK-C7外所有required
  scenario已有root PASS、auditor对当前facts PASS，但`finalize`仍因C7 NOT_RUN合法失败；这不是receipt。
- 同一交付更新`ARCHITECTURE/SDK_EXTRACTION.md`、`ARCHITECTURE/PROJECT_STATUS.md`、模块事实源、
  SDK release notes/API/Handoff/ac-trace；未全绿只写PENDING/BLOCKED。
- Slice A/B receipts + C-prepublish checkpoint + 本地candidate/产品vendor全一致后，向用户请求远端
  publish批准。获批后才创建与
  commit/BUILD_INFO/SHA256SUMS一致的v0.1.1 tag/Release；再从远端下载bytes复验并与product vendor
  比较；同一wheel SHA还必须在macOS ARM64、Windows x64、Linux ARM64 Python 3.11完成clean import、
  schema create/reopen与provider/tool/runtime/workflow四套conformance，全部通过后才记录SDK-C7 root PASS。
  随后重新audit，Slice C `finalize` exit 0；任一不同或平台失败则FAIL，绝不覆盖asset。

## Slice C machine gate

- 独立manifest/run-dir/challenge/auditor/receipt，program oracle实现前冻结且保持byte不变。
- 完成定义：所有生产入口只有SDK authority；S1-S5桌面、S6-S7自动化、full-surface、frozen backend、
  baseline regression、architecture docs、commit-state全部PASS。真实reset或远端发布若未获批准，分别
  如实列为PENDING授权点，不以其他绿色替代。
- frozen `manual-test.md:20-24` 目前要求桌面测试前已有private immutable release，与acceptance规定的
  “产品exact-wheel验收后再发布”冲突。开工前必须由用户批准
  `behavior-change-candidate-before-release.md`，把前置改为本地只读candidate目录+exact commit/BUILD_INFO/
  SHA；否则Slice C在桌面测试前BLOCKED，不得自行改oracle或偷偷先发布。
