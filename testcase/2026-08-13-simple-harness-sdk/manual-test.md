# Simple Harness SDK v0.1 black-box acceptance

> 状态：PREPARED / NOT RUN（Phase 3D black-box oracle；执行结果必须写到 `results/` 或 gate run-dir，不能回填本文件）  
> 事实源：`plans/2026-08-13-simple-harness-sdk/acceptance.md`  
> 适用范围：SDK-AC-1～SDK-AC-8、SDK-S1～SDK-S7  
> 禁止输入：实现源码、git diff、执行代理中间输出  
> 原始证据：`.local-test-evidence/2026-08-14/simple-harness-sdk/<gate-run-id>/`

## 1. 判定纪律

- 本文件定义预期，不记录实际 PASS。所有 required 场景必须形成新的 root run；retry、同义改写和 continuation 不算新的 distinct scenario。
- UI 场景只接受真实桌面窗口点击/输入。每次动作前记录 `坐标=(x,y)|动作=...|期望=...`，随后截图并用 Run/ledger 证据对账；WebSocket 注入、直接 import backend、pytest 回放不能替代 UI 证据。
- UI 使用已有有效开发登录态。按 SR-9，本 release 不清除全部产品数据并重新登录；这不豁免 clean wheel、schema v1 首次创建、close/reopen 或 supervisor restart。
- 只启动 Tauri；不得另起 backend 或第二个 Vite。日志必须确认 Tauri 管理的唯一 backend。
- 每份 primary evidence 必须绑定 testcase/scenario、精确输入、时间、Session ID、Run ID 和 SHA-256。不得保存密码、API key、token、cookie、二维码或完整 Provider body。
- `positive-value` 只有在业务结果非空且达到 quality bar 时才 PASS。诚实失败只可满足 `negative-safety`。
- SDK-S3、SDK-S4 与 SDK-S5 各跑两个独立完整 root；SDK-S3 的第二次运行必须在同一 Product Session 已有至少 10 轮历史后开始。这些第二 root 是随机性/长上下文采样，不增加 distinct input class。
- 至少一个 required UI root 由真人驾驶；若全部由 AI Computer Use 驾驶，必须先取得用户对“全 AI 驾驶”的显式批准并把原话 hash 入 gate 账本，否则 UI 门不成立。

## 2. 测试前置

1. 从 private immutable release 取得同一份 candidate wheel、wheel SHA-256、tag、commit、release manifest；不使用 SDK checkout、editable install、`PYTHONPATH` 或 path dependency。
2. 运行 `prepare-black-box-env.sh`。预期：wheel hash 匹配；clean Python 3.11 venv 可安装；全部输出只进入本地 evidence 根。
3. 宿主准备 `black-box-fixtures.md` 中的公开行为 fixture：单一只读摘要 Tool、两个 Personal candidate、一个无现成能力但允许构建的安全任务、受控 Provider fault variants、可查询的 ledger/delivery sink。
4. 确认桌面应用已有有效开发登录态，关闭 onboarding/凭据窗口后再截图。
5. 为每个平台与每个 UI root 预建独立 evidence 子目录；不得复用旧截图、旧数据库或旧日志冒充本次证据。

## 3. 自动化与制品门

### TC-AC1 — exact wheel、纯净 import 与三平台

覆盖：SDK-AC-1、SDK-AC-8。

| 步骤 | 操作 | 明确预期 |
|---|---|---|
| 1 | 在 Linux ARM64、macOS ARM64、Windows x64 原生 Python 3.11 runner 分别下载 release candidate wheel，计算 SHA-256。 | 三个平台的 SHA-256 与 release manifest 完全相同；wheel 名为 pure-Python `py3-none-any`。 |
| 2 | 每个平台创建无源码注入的 clean venv，仅安装 exact wheel（测试 extra 可另装但不得加入 SDK source）。 | 安装成功；依赖清单不强制 Node、Rust、Tauri、Torch、Whisper、Playwright 或浏览器。 |
| 3 | 在 audit hook 下执行 `import simple_harness`。 | 不联网、不写文件/目录、不读遍环境变量、不启动线程或 async task；未导入 FastAPI/Tauri/Torch/Whisper/Playwright/DeskPet。 |
| 4 | 只调用公开 lifecycle：显式 build、start、close，并用 async context manager 再跑一次。 | import 本身无生命周期副作用；两种显式生命周期均正常释放资源，重复 close 有确定语义。 |
| 5 | 两个 clean build 使用固定构建输入并比较 canonical 内容及 SHA。 | wheel/sdist canonical 内容一致；不得以“同版本重新构建的另一份 wheel”替代已测试 bytes。 |

失败条件：任一 runner 缺席、hash 不同、import 有副作用、出现 forbidden dependency，均为 required gate FAIL。

### TC-AC2 — 公共合同、immutability、identity 与 secret canary

覆盖：SDK-AC-2。

| 步骤 | 操作 | 明确预期 |
|---|---|---|
| 1 | 仅从 public namespace 构造 Message、Provider request/response、ToolSpec/Call/Result、typed event、全部 correlation ID 与错误。 | 类型可用且文档一致；ID 类型不能互换；公共对象不可原地修改。 |
| 2 | 对 JsonValue 输入依次提交合法递归 JSON、NaN、bytes、任意对象。 | 合法值往返保持；后三者在持久化/handler 前被稳定错误拒绝。 |
| 3 | 用同一 canary 作为 Provider secret、Tool 私有字段和底层异常片段，走成功、401、429、5xx、Tool failed、trace 与 SQLite 路径。 | canary 不出现在模型消息、repr、异常公开字段、日志、trace、SQLite、Tool context 或 JSON report。 |
| 4 | 重复触发同一非法请求。 | error code/public message/retryable 分类稳定；private cause 不被序列化。 |

### TC-AC3 — Provider 单次调用、structured Tool、取消与错误分类

覆盖：SDK-AC-3。

| 步骤 | 操作 | 明确预期 |
|---|---|---|
| 1 | 以自定义 base URL/model 和受控 HTTP server 发起一个含 structured tools 的请求。 | server 只收到一次物理请求；response 的 tool call、usage、run/request/call correlation 正确。 |
| 2 | 分别返回 401/402/429/5xx 与超时。 | 映射为稳定 typed error；不无限 retry、不泄漏 body/secret；Provider 不创建 Run、Session 或 Tool 状态。 |
| 3 | 在请求前取消、handoff 后取消各执行一次。 | 请求前取消不出站；handoff 后不盲目再次出站，durable coordinator 收敛为确定 outcome 或 unknown。 |
| 4 | 检查 public request/response 和 adapter 扩展点。 | v0.1 无需完成 streaming，但合同保留不破坏现有一次调用语义的扩展空间。 |

### TC-AC4 — Tool schema、五态、重复/迟到/cancel 与无 shell

覆盖：SDK-AC-4、SDK-S6。

| 步骤 | 操作 | 明确预期 |
|---|---|---|
| 1 | 注册 typed read-only Tool；分别提交缺字段、额外字段、错类型、非法枚举、超长值和 reserved host field。 | handler 调用计数保持 0；每种输入返回稳定且最小披露的错误。 |
| 2 | 查询未知 Tool 与未知 Workflow control。 | fail closed；无 handler/effect/child 副作用。 |
| 3 | 让 handler 分别产生 succeeded/partial/rejected/failed/unknown。 | 五态可区分；原始异常、stderr、HTTP body 和私密值不直接回传模型。 |
| 4 | 对同一 call/effect 注入重复、乱序、迟到与 cancel 后 result。 | 结果只绑定原 `run_id + call_id + effect_id`；不跨调用、不二次 settle、不提前终结 Run。 |
| 5 | 枚举默认 SDK Tool catalog。 | 不存在默认 shell Tool；产品权限系统仍在真正执行前决定授权。 |

### TC-AC5 — durable Kernel、原子边界、预算与恢复

覆盖：SDK-AC-5、SDK-S7。

| 步骤 | 操作 | 明确预期 |
|---|---|---|
| 1 | fake Provider/Tool 依次完成 no-tool、one-tool、multi-turn/multi-tool。 | root 固定 `agent.general`；每个 root 有唯一 start snapshot、事件序列与 terminal；多 Tool correlation 不串线。 |
| 2 | 启动 root + 2 attached child；分别发送 continuation、signal、cancel。 | child 只由 frozen one-shot ticket 创建；parent/child 可追溯；FIFO continuation、signal 和 cancel 有确定终态。 |
| 3 | 在 root start、child launch、workflow progress、child finish、root finish、Provider dispatch、continuation、Tool effect 的每个写点前后 fault/reopen。 | 每个命令只有完整 before/after；无半写、孤儿 link、重复 signal、重复 delivery；FK/integrity check 通过。 |
| 4 | 分别超过 max turns、Tool 次数、wall clock、cost 与连续同 Tool上限。 | 系统代码硬终止并给稳定原因；不能靠 prompt 自律；缺 usage 不按 0 成本继续。 |
| 5 | Tool 已出站但 outcome receipt 前强杀，随后 reopen/reconcile。 | effect 为 unknown/待对账；同一 effect_id 不自动执行第二次；confirmed_not_started 才允许安全重试，completed 只 settle 一次。 |
| 6 | Provider handoff 后强杀并 reopen。 | invocation ledger 为 authority；不产生第二次物理请求；累计 usage/cost 不回退。 |
| 7 | root terminal 后 delivery sink 首次失败再恢复。 | root 不重开；generic outbox 重试并只 complete 一次。 |
| 8 | 并发启动两个不同 execution session 的 root，并让 Provider/Tool result 交错返回。 | identity、budget、effect 与 terminal delivery 完全隔离；一个 root 的 cancel/failure 不终结另一个。 |

### TC-AC6 — fixed root、Agent 自主选择与 anti-forgery

覆盖：SDK-AC-6。

| 步骤 | 操作 | 明确预期 |
|---|---|---|
| 1 | 对 direct answer、ordinary Tool、三个 Workflow 使用不同文本/venue/mode 创建新顶层请求。 | 所有 root 都是 `agent.general`；没有 classifier/router 改写 Profile。 |
| 2 | 向主 Agent 提供 frozen descriptor catalog；观察 `workflow_spawn`。 | 选择来自同一个主 Agent 的显式 control call，使用当轮 exact key/generation。 |
| 3 | 注入未知 Profile、stale generation、复用 ticket、伪造 driver/graph/owner/version。 | Host 确定性拒绝；不创建或启动未授权 child；错误可诊断且无语义二次路由。 |
| 4 | 扫描生产可达 public surface 与 cutover receipt。 | 不存在 regex/关键词 classifier、legacy router、ticketless child 或 Personal 前置 matcher；Adapter 不代理旧 authority。 |

### TC-AC7 — 三个官方 Profile、schema v1 与 reset 边界

覆盖：SDK-AC-7。

| 步骤 | 操作 | 明确预期 |
|---|---|---|
| 1 | 用 fake host 运行 durable_task：plan → HITL → Tool loop → test/audit → bounded repair → output audit。 | graph/节点编排来自 SDK；receipt、declared output、terminal delivery 一致；reopen 后不重复 effect。 |
| 2 | 给 personal_v1 两个候选并选择其一；reopen 于 Native execute checkpoint。 | 模型只提交 candidate ID；Host 绑定 frozen graph/owner/version；只允许 idempotent_read/deterministic_reusable Tool。 |
| 3 | 对 capability_build 先产生 current-stamp catalog miss，再授权 build/test/install/activate。 | 它是 durable_task 特化；每步有 receipt/授权；成功后同 Run 可见且默认启用。 |
| 4 | 分别缺少每个 optional module/required Port，再补齐。 | 缺失时仅该 Profile 不注册且 Core 可启动；齐全即进入 catalog，无默认 OFF/shadow 双轨。 |
| 5 | 向显式临时 SQLite path 首次启动、close/reopen；再执行 schema v1 后续 migration fixture。 | schema v1 干净创建；Run 可恢复；migration 幂等；不读取产品 Session/Message/UI 数据。 |
| 6 | 对 reset 工具做 dry-run、wrong nonce、symlink/path traversal、root/home/repo/evidence/Product Session 目标和 exact allowlist confirm。 | 前七类拒绝且无删除；只有 exact dev execution DB/WAL/SHM 在显式确认后删除，inventory/hash 可审计。 |

### TC-AC8 — conformance、cutover、license、release 与 Handoff

覆盖：SDK-AC-8。

| 步骤 | 操作 | 明确预期 |
|---|---|---|
| 1 | 在只安装 wheel、无 SDK tests/source 的环境运行公开 conformance CLI 和 pytest protocol。 | Provider/Tool/Runtime/Workflow/Session persistence suites 可复用；report 含 SDK/protocol/host/platform；major mismatch fail closed。 |
| 2 | 在 Simple Harness clean checkout 安装 vendored exact wheel并构建 frozen backend。 | wheel bytes/hash 与 release 完全一致；无 path/editable/PYTHONPATH；PyInstaller inventory 包含 SDK。 |
| 3 | 执行 cutover public gate。 | 产品只 import SDK public API；无 `_internal` deep import、旧 Kernel/Workflow/ledger writer、matcher/router/ticketless branch 或 feature-flag fallback。 |
| 4 | 检查 source manifest、REUSE、LICENSE、NOTICE、SBOM 和 third-party notices。 | 每个进入 Apache SDK 的文件来源/作者/重许可结论完整；未批准文件阻断发布；产品层保持 BUSL-1.1。 |
| 5 | 核对 exact tag/commit/wheel hash、attestation、API reference、quickstart、migration/reset guide、changelog。 | 所有标识相互一致且指向已测试 artifacts；发布不重建。 |
| 6 | 按 AIPhone Handoff 在 clean Linux ARM64 环境安装同一 wheel并运行 fake Mobile Host Adapter conformance。 | 命令可执行，边界/三 Profile/known limits 清楚；本 release 未修改或部署手机端。 |
| 7 | 完成 SDK-S1～SDK-S5 桌面 exact-wheel E2E，并在 supervisor restart 后重查。 | 真实用户入口可用；同 Run ID 恢复，无重复 Provider/Tool/child/ArtifactCard/delivery。 |

## 4. required 桌面价值场景

以下场景必须在 exact-wheel frozen backend 上执行。每步先 Snapshot/Screenshot，再声明坐标并点击/输入；失败至少尝试三种 UI workaround 后才可上报环境受限，且 required 状态保持 NOT_RUN。

### SDK-S1 — 纯回答，不臆造执行

输入：`用一句话解释什么是幂等性。`

1. 打开主 Chat，创建新的对话上下文并粘贴精确输入。预期：界面显示本次输入，生成新的 root Run。
2. 等待 terminal。预期：非空、准确、简洁的中文一句话；不声称调用了 Tool 或 Workflow。
3. 对账。预期：root Profile=`agent.general`、status=`completed`，child/effect 数均为 0。

### SDK-S2 — 单个只读宿主 Tool

输入：`读取当前项目摘要，然后用中文告诉我重点。`

1. 确认只读摘要 Tool fixture 已注册；从主 Chat 粘贴精确输入。预期：创建新的 `agent.general` root。
2. 等待 Tool 与回答完成。预期：恰好一个受信 Tool 成功，不启动 Workflow。
3. 人工对照 fixture 的 public summary。预期：回答忠实、非空，不出现 private canary、异常原文或不存在的执行。
4. 对账。预期：一个 call/effect settled，一次 terminal delivery。

### SDK-S3 — durable 多步骤任务（2 个独立 root）

输入：`分析这个项目的测试缺口，形成计划，执行获准的检查并给出可审计结论。`

1. Run A：在新会话输入精确文本。预期：root 为 `agent.general`，由主 Agent 显式选择 `workflow.durable_task`，创建一个 ticket-bound child。
2. HITL 出现时真点击批准受控只读检查。预期：只有获准步骤执行；计划、进度与中间状态可见。
3. 等待完成。预期：结论引用真实检查 receipt，声明输出与实际产物一致，无重复 effect/delivery。
4. Run B：在已有至少 10 轮历史的 Product Session 再输入同一 exact_input。预期：形成新的独立 root，不复用 Run A 身份；仍完整收尾。
5. supervisor restart 后回到两次结果。预期：Run/child lineage、terminal 与 artifact/delivery 各唯一且可恢复。
6. 另建两个 UI root：一个在 HITL 点击“拒绝”，另一个在执行中点击“取消”。预期：拒绝 root 的未获准 Tool 调用计数为 0；两个 Run 分别进入可解释 rejected/cancelled 出口，不留下重复 child、effect 或 delivery。

### SDK-S4 — Personal 候选绑定（2 个独立 root）

Fixture：`weekly_work_planner` 与 `fitness_training_coach` 两个受信候选。  
输入：`安排下周项目优先级，并提醒我周五做一次复盘。`

1. 从主 Chat 输入文本。预期：同一个 `agent.general` 看见两个 bounded descriptor。
2. 等待选择。预期：显式选择 `workflow.personal_v1` + `weekly_work_planner` candidate ID；不先运行独立 matcher。
3. 对账 ticket。预期：graph/owner/version/fingerprint 来自 Host frozen binding，而非模型参数。
4. 第二个独立 root 重跑一次，并另做伪造 graph/version 的受控 negative。预期：合法选择稳定；伪造字段在 child 启动前拒绝。

### SDK-S5 — Capability 缺口构建（2 个独立 root）

输入：`完成一项当前 catalog 没有能力处理、且允许安装新能力的任务。`

1. 先在同一 Product Session 用普通历史消息建立 F3 的安全目标 `fixture.text.normalize`，但不触发构建；随后从主 Chat 输入上面的精确文本。预期：本次输入创建新的 root，先执行 current-stamp catalog search，产生可审计 miss receipt。
2. 主 Agent 选择 `workflow.capability_build`。预期：它绑定官方 durable_task 特化，无另一个隐藏 engine。
3. 在 install/activate 前真点击授权。预期：source/build/test/install/activate 每步 receipt 可见；测试失败或坏 hash 时不得安装。
4. 成功路径等待完成。预期：新能力同 Run catalog refresh 后可用，默认开启，无 shadow/默认 OFF。
5. 用第二个独立 root 重跑缺口/授权边界；随后 supervisor restart。预期：active package identity、hash 与数量保持不变，不产生第二份安装；activation/rollback 与 terminal delivery 可追溯。

### TC-UI-B1 — 长载荷桌面边界

1. 通过受控合法 Tool 让 SDK-S2 类 root 返回接近公开长度上限的文本，内容首尾带不同 marker。预期：创建一个新 root，Tool 只执行一次。
2. 等待桌面渲染。预期：窗口仍可滚动/切换/取消；若内容被截断，UI 明示截断并提供 artifact 或诊断出口，不能静默伪造完整结果。
3. 对账 Context、日志与 SQLite 大小/终态。预期：增长受预算限制；marker/correlation 未串到其他 root；terminal 可诊断。

### 自然表达鲁棒性 probes（不计 distinct class）

- durable 口语：`这个项目哪块测试最薄弱？先列个靠谱计划，得到我同意后检查一下，再告诉我结论。`
- Personal 中英混合：`帮我排一下 next week 的项目优先级，周五记得提醒我 review。`
- Capability 无实现关键词：`现在还不会做这件事也没关系，先找找有没有现成办法；没有的话，在我确认后补上这个本事再完成。`

每条作为对应 SDK-S3/S4/S5 的额外 retry/root 鲁棒性证据；预期仍由同一主 Agent 正确选择或澄清，Host 不靠关键词强路由。它们不能替代场景矩阵中的 exact input，也不增加 distinct scenario 计数。

## 5. 自动化负向场景

### SDK-S6 — malformed/unknown/乱序/重复/极端载荷

受控 Provider 逐项返回：未知 Tool、未知 Workflow、缺字段、额外字段、错类型、非法枚举、stale generation、重复 result、乱序 result、超长参数/ToolResult、拒不调用必须 Tool。

1. 每个 variant 使用独立 root 和独立 evidence 子目录。预期：schema 违约在 handler/child/effect 前拒绝。
2. 重复/乱序按原 run+call+effect 对账。预期：不串线、不二次执行、不提前 terminal。
3. 超长载荷运行到确定出口。预期：Context/log/SQLite 有界，Run 可诊断。
4. Provider 拒不调用 Tool。预期：max-turn/termination gate 使其诚实失败或降级，不无限循环、不伪造结果。
5. 扫描 canary。预期：所有 evidence/report/SQLite 均无 secret。

### SDK-S7 — crash 后 unknown 与 reconciliation

1. Tool handler 刚完成物理出站、尚未提交 outcome receipt 时强制终止进程。预期：原 root 未被错误标 completed。
2. reopen 同一 SQLite 并启动 reconciliation。预期：同一 effect_id 为 `unknown`/待对账，不自动再次执行。
3. Adapter 返回 `still_unknown`。预期：保持 fail closed；无第二次副作用。
4. 分别返回 `completed` 和 `confirmed_not_started`。预期：completed 只 settle；confirmed_not_started 才允许受 fence 的安全重试。
5. 对账 terminal/provider/effect/delivery。预期：ledger 与业务终态一致，无 duplicate child/Tool/ArtifactCard。

## 6. AC → testcase → evidence 映射

| AC | Testcase | 主要 primary evidence |
|---|---|---|
| SDK-AC-1 | TC-AC1 | 三平台命令回执、wheel hash、import audit |
| SDK-AC-2 | TC-AC2 | contract report、canary scan、SQLite/trace scan |
| SDK-AC-3 | TC-AC3、SDK-S2/S6/S7 | mock server request count、typed error/cancel report |
| SDK-AC-4 | TC-AC4、SDK-S2/S6/S7 | handler/effect counters、validation report |
| SDK-AC-5 | TC-AC5、SDK-S1/S2/S3/S7 | Run/child/effect/provider/delivery ledger 与 UI 截图 |
| SDK-AC-6 | TC-AC6、SDK-S1～S5 | UI root/profile/ticket evidence、cutover negative scan |
| SDK-AC-7 | TC-AC7、SDK-S3/S4/S5/S7 | workflow receipt、schema/reopen/reset report |
| SDK-AC-8 | TC-AC1/8、SDK-S1～S7 | conformance reports、exact release、桌面证据、Handoff report |

## 7. 执行出口

- 本文件及 gate manifest 在昂贵测试前冻结；后续实际结果写入 `results/<run-id>.md` 或 gate report。
- 任一 required 场景 NOT_RUN/PARTIAL、任一平台未验证、任一正向场景仅得到 insufficient，均不得完成。
- 产品级首次登录 cold-start 只登记 SR-9 follow-up，不得在本 release 中伪称 PASS。
