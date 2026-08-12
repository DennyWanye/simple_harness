# 2026-08-11 Workbench UI r14 follow-up

> 状态：r15 产品修复、自动化与真机 E2E 已收口；macOS 托盘由用户现场手测确认，退出终态和
> 重启几何已独立对账。plan-test/gate 按用户要求继续暂停。
> 约束：用户要求暂时不使用 plan-test，因此本文件只记录事实与后续顺序，不执行 gate。
> 本地 `main` 锚点：`1283998`（MCP provenance fail-closed）与 `6032a50`
>（Live2D/账户认证残留退役）。尚未推送远端。

## 已完成并验证

- Live2D/Cubism/Pixi 锁依赖、角色表情/动作消息、语音标签解析和透明启动壳已删除。
- 前端 `AuthAdapter`、Login/Register 生命周期、侧栏账户入口、旧 relay key 同步与登录诊断脚本已删除。
- 本地 profile 的签名 `identity_bind` 与手动 Provider/API Key 配置保留；两者不是账户登录。
- 自动化：Vitest `533 passed`、Rust `74 passed`、Companion `647 passed / 10 skipped`、
  MCP manager `21 passed`、语音/身份聚焦 `14 passed`；TypeScript、Vite production build、
  `cargo check` 均通过。
- 其它工作分支 `wt/group-v` 与 `claude/adoring-wescoff-ae159d` 均无 `main` 之外的提交。

## F1 — r14 manifest 与当前代码锚点失效

现有未跟踪 `verification-manifest-r14.json` 仍写：

- baseline HEAD `19c01d8`
- dirty patch 全零

当前生产 HEAD 已是 `6032a50`，且 testcase/acceptance 文档仍有待提交修改。因此该 manifest
只能视为草稿，不能 init、导入证据或宣称代表当前被测对象。

后续恢复 gate 前必须一次性定稿 acceptance/testcase/architecture，再从最终 HEAD 重新生成
r14（若 r14 名称已被任何工具初始化则改开 r15），并重新计算源码变化影响场景。不得复用旧
manifest 的 baseline 数字。

## F2 — 实质验收仍缺 primary evidence

以下为 r13 独立实质审计留下、r14 必须补齐的缺口：

1. **TC-WB-08 设置持久化**：Provider/模型/API Key、数据目录、Agent 预算、自启需要分别执行
   可逆的 UI 修改 → 保存/即时应用 → 重启后的 UI 与 backend/系统对账 → 恢复原值。
2. **TC-WB-10 几何异常分支**：步骤 5–10 原文要求预置真实 userdata 文件并启动应用；单元测试
   不能默认替代 manual-required 步骤。若要改为等价替代，须先取得明确口径批准。
3. **TC-WB-13 运行期断连**：现有 primary 图只证明启动失败对话框，未证明 Workbench 可见时
   ChatView 状态条、侧栏双连接最差态、发送不黑洞与重试入口。
4. **TC-WB-18 删除即时态**：需要删除当前会话前后 primary UI/AX 证据，证明立即切换且无已删
   消息残留；仅有重启后截图不够。
5. **TC-WB-12 冷启动性能**：需要 `644ab16` 与当前改版侧都预热后测第二次启动，记录原始计时、
   可交互截图和“改版不超过基线 +3 秒”的判定。

本轮又删除了账户/AuthAdapter、Live2D 消息和启动壳代码，恢复测试时必须按最终 manifest 的
impact mapping 重新计算 fresh-run 集合，不能沿用 r13 的“18 项均 PASS”结论。

## F3 — legacy parity census 引用不存在的 Git 对象

`scripts/acceptance/harness_parity_census.py` 默认读取历史对象 `4d38979e`，但当前仓库
`git cat-file -t 4d38979e` 返回 `Not a valid object name`，且仓库不是 shallow clone。

影响：`test_product_turn_parity.py` 中依赖重建 legacy census/mapping 的两项测试无法运行。
当前源码的 `context_usage` 分类遗漏已修并聚焦通过，但不能掩盖历史对象缺失。

处理原则：恢复确切历史对象，或把所需 legacy source 作为带哈希的只读 fixture 入库；不得把
base commit 静默改指一个不等价提交，也不得删除 fail-closed 校验来换绿灯。

**2026-08-11 已修复**：从本机 `~/projects/deskpet` fetch 回确切对象
`4d38979ec9d965afdef32243fe6492e1627eb8ec`，挂在保活 ref `refs/legacy-parity/base`
（旁支历史 1348 commits，与 main 无共同祖先，不随 main push）。census fixture 重建后
与冻结版逐字节一致，证明对象精确；mapping fixture 因近期重构导致 current callsite 行号
漂移，已用 `scripts/acceptance/harness_parity_census.py --write --write-mapping` 重生成并
提交。`test_product_turn_parity.py` 14 项全过。

跨机器恢复命令（新 clone 上跑 parity 测试前执行一次）：
`git fetch <deskpet-repo> 4d38979ec9d965afdef32243fe6492e1627eb8ec:refs/legacy-parity/base`。
若 deskpet 仓库将来不可得，届时再落地"legacy source 只读 fixture 入库"方案。

## F4 — 临时性能基线 worktree 未收口

`/private/tmp/wbui-r14-perf.mvedXE/baseline` 仍停在 detached `644ab16`，包含：

- `tauri-app/src-tauri/src/process_manager.rs` 的测试专用安全修改
- 未跟踪 `backend/.venv`

其后台进程已终止、8100 端口已释放；该修改只用于避免旧基线读取已退役 Keychain，不是产品
代码，不能合入 `main`。继续性能测试前应保留并复核该 worktree；决定放弃时再显式移除，避免
把临时目录里的未提交补丁误当作已保存证据。

## F5 — 未跟踪验证证据体积过大

r10～r13 四个未跟踪 verification 目录合计约 `917 MiB / 648 files`（2026-08-11 复测实际
占用 `3.6 GiB`），包含大量跨轮复制的截图与日志。不得直接 `git add -A`：应先做凭据/隐私
扫描、去重并决定使用 Git LFS、外部归档，或只提交 manifest、receipt、auditor output 与最小
primary evidence。

**2026-08-11 用户定调**：证据全部留在本地不入库，`.gitignore` 已挡住
`plans/2026-08-04-workbench-ui/verification/` 与各轮 `verification-manifest-r*.json`。
后续计划：搭建自动归档到局域网 NAS 的流程（rsync/定时同步，归档前完成凭据/隐私扫描），
落地前证据仅存在于本机工作区，注意不要误删。

**2026-08-13 扩展为项目级长期规则**：后续所有测试的原始证据均不得上传 Git，不再局限于
Workbench verification。新证据统一落到 `.local-test-evidence/<日期>/<scenario-or-run>/`；Git
只提交文字结论、命令、状态、Run/scenario ID、相对索引与 SHA-256。历史工具目录的 artifacts、
screenshots、logs、recordings 也由通用 ignore 规则覆盖。NAS 路径尚未配置；未来归档必须先做
凭据/隐私扫描，复制后逐文件校验 SHA-256，确认 NAS 副本完整后才可删除本地原件。旧 Git 历史
中的证据暂不改写，若要瘦身另开经用户批准的 history/LFS 迁移任务。

## F6 — pnpm 11 构建脚本审批影响标准命令

`pnpm run typecheck` / `pnpm test` 在依赖状态检查阶段触发安装，并因
`ERR_PNPM_IGNORED_BUILDS: esbuild@0.21.5` 提前退出。直接执行仓库内 `tsc`、`vitest`、`vite`
均已通过，说明代码与安装产物可用，但标准 package scripts 的新环境可复跑性仍需修。

后续应采用项目级 pnpm 构建依赖白名单或受控 `approve-builds` 配置，并在干净安装环境复跑；
不要关闭全局 pnpm 安全策略。

**2026-08-11 已修复**：`tauri-app/pnpm-workspace.yaml` 原有占位符
`allowBuilds: esbuild: "set this to true or false"`（无效值导致审批一直挂起），改为
`esbuild: true` 项目级白名单。复跑 `pnpm run typecheck`（tsc 通过）与
`pnpm test`（Vitest 67 files / 533 passed）均绿，esbuild postinstall 正常执行。
全局 pnpm 安全策略未动。

## F7 — r15 续测后的真实产品缺陷与已闭环场景（2026-08-12）

续测事实详见 `plans/2026-08-12-workbench-ui-r15-test-resume.md`。本轮仍按用户要求暂停
plan-test，没有创建或修改 gate ledger；以下是代码锚点 `7415f76` 上的真实 UI 结果：

- **TC-WB-05 PASS**：会话 A/B/C 隔离、新会话真往返、重命名保持与删除均已完成。
- **TC-WB-17 PASS**：800×560、30 会话/30 产物、80 字符标题与四视图切换通过。
- **TC-WB-18 PASS**：当前会话删除即时切换、删除后归属、重启不复活、删至零空态、空态
  新建真往返全部通过。
- **TC-WB-08 FAIL**：Provider 新增/停用/重启/清理和 macOS 自启开关链通过，但 Provider 删除
  无确认、Agent 预算无法稳定持久化、macOS 数据目录重启不生效，且 testcase 把实际包含
  压缩配置编辑能力的模型上下文卡整体误标为只读。
- **TC-WB-13 FAIL**：运行期 backend 持续不可用时，`StartupOverlay` 全屏遮住 Workbench，无法
  满足 ChatView 状态条、发送明确失败、侧栏最差态与运行期重试的冻结口径。
- **TC-WB-07 BLOCKED**：消息流 `artifact_card` 的最后一公里投影仍未完成，不能只翻 flag。

续测同时推翻了旧的“当前环境禁止 localhost 监听”结论：本机 IPv4/IPv6、Vite 5173 与
backend 8100 均可正常监听。此阶段当时仍缺 TC-WB-10/14/16 托盘路径与 TC-WB-12 步骤 7
同机冷启动性能对照；其后状态以 F8 为准，不再按统一沙箱阻断处理。

该阶段修复顺序为：先锁定兼容的 Tauri JS/Rust 版本并恢复 debug bundle；再修 TC-WB-08、
13、07；完整自动化后补退出真机证据。实际收口结果见 F8；实现或 testcase 发生变化后，应
保留 r15 为历史记录，将来用户恢复 plan-test 时开干净的新轮次，不把旧证据包装成最终验收。

## F8 — r15 修复后复测与托盘收口（2026-08-12）

已完成并有当前 macOS 真 UI 证据：

- **S07 PASS**：生产 `execute_prepared` 补齐 artifact envelope，消息流真实渲染 ArtifactCard；
  “打开”由 TextEdit 显示精确内容，“在文件夹中显示”由 Finder 选中目标文件。
- **S08 PASS**：Provider 删除确认、预算 request/response 关联、macOS 稳定数据目录偏好与
  “当前/下次启动”诚实状态全部修复；Provider、预算、数据目录、自启均完成修改、重启、恢复和
  无残留闭环。
- **S13 PASS**：运行期 backend 故障保持在 Workbench 内，横幅/ChatView/侧栏最差态一致，
  发送 fail closed、重试可用；恢复后 Kimi3 HTTP 200 并精确回复 `S13 恢复成功`。
- **S16 路径 A/B PASS**：红钮与 Cmd+Q 后主进程、backend、8100 均零残留，两次重启均恢复
  1100×750、位置 (400,200)。
- **S10/S14/S16 托盘路径 PASS**：用户在当前 macOS 打包版现场确认三项托盘文案、隐藏/显示、
  托盘退出、非默认几何退出后恢复均正常；托盘退出后独立检查主进程/backend/8100 零残留，
  当前版本重启日志与 1100×750 截图确认几何恢复。托盘 UI 动作来源明确记为用户现场手测，
  未伪造 Computer Use 菜单截图。
- 自动化：Vitest `539 passed`、Rust `79 passed`、companion `647 passed / 10 skipped`，
  TypeScript、Vite build、`cargo check` 均绿；
  companion 应从仓库根目录用 `backend/.venv/bin/python -m pytest backend/tests/companion -q`
  执行。旧文档中的 `cd backend && uv run pytest tests/companion/ -q` 会因包根不在
  `sys.path` 于收集阶段报 `ModuleNotFoundError: backend`，需后续统一修正文档入口。

剩余边界：

- **TC-WB-12 步骤 7** 已由用户在 2026-08-12 明确批准移除：不再执行、不阻断交付，禁止
  再次启动带 Live2D 的 `644ab16` 历史基线。
- plan-test skill 仍按用户要求暂停；没有创建/修改 gate ledger，也不宣称机器门 READY。

## 建议恢复顺序

1. 产品/testcase 层的 r15 手测已收口；保留本地证据与用户现场确认来源说明。
2. 仅在用户明确恢复 plan-test 后，新开干净轮次完成机器门与独立审计。
3. 只有用户明确要求时才 push 远端 `main`。

## F9 — Harness 复杂任务交付补强与 Provider 阻断解除（2026-08-12）

本轮在暂停 plan-test 的前提下完成了产品代码补强：可信 workspace 内的现有文件可通过只读
`register_artifacts` 进入标准 artifact envelope；`write_file` 支持 ≤3000 字符的 append 分块，
`run_shell` 拒绝携带超长文件正文；权限弹窗收敛为 ChatView 单一订阅者，并按 decision identity
去重与阻止重放。

当前源码 macOS debug app 真测发现并修复一个独立缺陷：历史 Kimi Session 因 catalog 中已无
对应模型而 preflight failed 后，界面短暂仍显示“停止”；旧 `_cancel_product_harness_run` 会为取消
重新要求可用 Provider/workspace，重复得到 `bound_model_missing` 并击穿聊天 WebSocket。取消 Host
现显式允许这两项 unavailable，因为授权只依赖持久化 Session/principal/auth epoch。修复后真点击
“停止”回到“空闲”，连接保持“已连接”，日志无 `SessionProviderUnavailable`/ASGI 异常；聚焦回归
`40 passed / 4 xfailed`。

新增 Artifact/权限链已经用当前源码真实模型闭环。macOS 真 UI Run
`04a477a3fbbb5e3eb2045e6b11006b55` 由 `kimi-k3` 调用 `write_file` 创建
`harness-k3-artifact-check.txt`，只出现一次写权限确认；随后只读 `register_artifacts` 登记同一
文件而不再弹写权限。两个 effect 均成功并显示 ArtifactCard，TextEdit 打开与 Finder 定位通过；
文件 14 B，SHA-256 为 `c96a2f4aec81c7e0d4ddaceb068ecaf030477e1c273bd4ab70ca1fe9197c4706`。

**2026-08-12 更新**：已修复“模型选择器读 live `/models`、执行校验却只认旧 TOML models”的
目录漂移。成功的 live 目录现在通过 Registry 原子写回对应 Provider 缓存，且 cache-only 刷新
不递增 config revision；Provider 并发编辑、endpoint 变化或显式 default 缺失时拒绝落错缓存。
聚焦回归 `88 passed`。当前 r11 隔离配置已用真实 HTTP 200 目录刷新为 155 个模型，包含
`kimi-k3`；测试 Provider 的显式默认模型已按用户测试偏好单独设置为 `kimi-k3`。

真实 Artifact Run 同时暴露了 Persona 的 cache-stable `runtime-model` 占位符被模型当作身份回答的
问题。现将真实 model/base URL 拆为受保护的 task-scoped fragment，并让 `TurnPreparer` 使用已经
解析的 Session Provider；平台片段仍保持稳定以保留 prompt cache。重启当前源码后的独立 Run
`97f01117fd50506fbe10da0444577fbd` 在界面精确回复 `kimi-k3`，后台也记录
`model=kimi-k3`、`/v1/chat/completions` HTTP 200。F9 产品功能与真实 E2E 至此闭环；plan-test
机器门仍按用户要求暂停，不据此宣称 gate READY。

## F10 — Kimi K3 复杂工程烟测：核心执行 PASS，委派交付 PARTIAL（2026-08-12）

在继续暂停 plan-test 的前提下，用当前源码 macOS app 和 `kimi-k3` 执行隔离复杂任务：在可信
workspace 内创建交易账本 CSV、Python 标准库 Decimal 分析器、9 项 unittest、JSON/Markdown
汇总与 RESULTS 报告；禁止网络、禁止修改仓库源码，并要求一次 `register_artifacts` 登记 5 个
交付文件。

成功事实：

- root `cb74467c06f35eaebdf7bfe316b9fff8` 与 child
  `child-4dad77bbfafec0b8428852dde382d9eb` 均为 `completed`；child 真实执行分块 `write_file`、
  两次 unittest、CLI 和一次 `register_artifacts`。
- 独立复跑 `python3 -m unittest -v test_ledger_analyzer.py` 为 `9/9 OK`；独立 CLI 与落盘
  `summary.json` 均为 `valid_count=10`、`invalid_count=3`、`grand_total="400.00"`、
  `top_category="Travel"`。源码导入仅含 Python 标准库与本地 `ledger_analyzer`。
- 5 个文件均存在且 child outcome 的大小/SHA-256 与独立重算一致；根 Run
  `1bf5014d7ee55573ba2a797c391032ef` 直接补偿登记后，SessionDB 生成一条真实
  `artifact_card`，effect 的 5 个 `artifact_refs_json` 非空，UI 显示 5 张带四种动作的卡片。
  “在文件夹中显示”真机通过，Finder 精确选中 `RESULTS.md`。

仍需修复：

1. **child artifact bridge 丢失**：child 的 `register_artifacts` outcome 含 5 个 artifact，但该
   workflow effect 与 child terminal 的 `artifact_refs_json=[]`，SessionDB 没有对应
   `artifact_card`；父回复却错误宣称“ArtifactCard 已出现”。需从 child effect/terminal 把 refs
   原子桥接到 root delivery，并让完成声明由真实投影事实门控。
2. **目录前置异常击穿控制通道**：首次 root `4acdb330974b5a9f806fe6675597abe1`
   在未完成 `project_directory_select` 时调用 `workflow_spawn`，
   `project_workspace_selection_required` 以未捕获 `ValueError` 终止 ASGI/control WebSocket；点击
   停止后出现 `Cannot call "send" once a close message has been sent`。应投影为可恢复的目录等待
   或普通工具失败，不得关闭控制通道。
3. **状态投影漂移**：child 后台持续执行 `run_shell/write_file` 时，UI 进度长期在 5/9 与 6/9
   间反复并显示“等待授权”；完成后才跳到 9/9。需要以 durable event 的权威阶段去重/单调投影。
4. **打开动作尚未取得可见终态**：卡片回报“已请求系统打开文件”，但 TextEdit/VS Code 未显示
   `RESULTS.md`；Finder 定位已通过。后续应让 `artifact_open` 返回可验证的 launch/open 结果，
   并补默认应用存在/不存在两条 macOS 真机回归。

截至上述历史真测，Harness 已能完成真实的多文件、多步骤、带测试与自检的复杂工程任务，但因
委派 child 的产物交付、前置失败恢复与状态诚实性缺口，只能评为“有人监督下可用”。当前源码
修复与新的验收边界见 F11；未完成 fresh E2E 前仍不能评为完全可靠的无人值守执行器。

## F11 — 复杂任务可靠性与结构化日志补强（2026-08-12）

当前源码已完成以下修复，plan-test 继续暂停，本节不写 gate ledger：

1. child `execute_prepared` 将 receipt/artifact refs 与 effect completion 原子提交，提交成功后才
   ack prepared metadata；durable task terminal 事务验证 artifact envelope、refs 与 SHA-256，
   发出 `workflow.artifact_card` delivery event，并将同一 artifacts/refs 写入 parent signal。
2. `project_workspace_selection_required` 被精确转换为 retryable tool outcome，让模型可继续调用
   `project_directory_select`；其它 `ValueError` 仍按原错误语义传播。
3. RunPresenter 对 originator socket、peer broadcast、final 和 context-usage 分别 best-effort；
   SessionDB durable 写入先于 live delivery，旧 socket 关闭不会把已完成任务改写成失败。
4. AgentLoop 在 terminal event 后收到 `GeneratorExit`/cancel 时把 trace span 记为 OK；真正的
   pre-terminal cancel 才记 CANCELLED。workflow runner/node handler 同时记录原始异常堆栈。
5. backend stdlib 与 structlog 统一为单行 JSON，日志目录统一走 user-log/user-data/portable
   resolver，`backend.log` 采用 20 MiB × 5 轮换；Tauri diagnostic archive/reveal 增加 macOS/Linux
   原生命令。
6. 权限 UI 在 live decision 成功提交后，把仍处于 `awaiting_permission` 的 root
   projection/session 乐观推进为 `running` 并清除 stale decision；durable terminal 仍有最高
   权威，不会被这一乐观状态覆盖。hook 与 WebSocket 相邻回归 `37 passed`，TypeScript PASS。
7. stdlib 与 structlog 的共同 JSON 出口增加最终脱敏处理器：递归隐藏敏感字段，并对消息内
   credential/JWT/邮箱/手机号/卡号形态脱敏，保留 run/request/node/timing 关联字段；脱敏与
   observability 回归 `12 passed`。

自动化事实：相关 workflow/effect/ReAct/RunPresenter/trace/observability 聚焦重跑
`231 passed`；前端
`540 passed`、TypeScript、Vite build 全绿；Rust `79 passed`、`cargo check` 全绿；日志 smoke
确认 44 行全部可 JSON 解析且同时含 stdlib/structlog。Python 全量不是绿色：
`7473 passed / 48 skipped / 4 xfailed / 79 failed`。失败集中在 macOS 执行 Windows-only 用例、
临时目录大小写断言、过时的 authority/hash/LOC fixtures、缺失 bundled git 与历史 evaluation/
memory 环境假设；本轮已修与改动直接相邻的 durable semantic-router、artifact flag 和 mutation
fixture 回归，但不能把剩余 79 项伪报为通过。

新观察到的独立问题：用历史 r15 profile 启动当前源码时，恢复旧 Run 会因当前 catalog 已不含
历史 ToolSpecs 而反复抛 `ToolCatalogMutationError: snapshot references unavailable ToolSpecs`。
这不是 fresh-profile 能力验证的失败，也不能靠忽略日志解决；后续需给历史 Run/catalog 漂移定义
有界 fail-closed 终态，避免启动恢复循环刷栈。

下一验收已于下述第二轮完成：使用隔离 fresh profile + `kimi-k3` 从真实 Workbench UI 选择可信
workspace，执行多文件、测试、CLI、自检和一次 `register_artifacts` 的 child durable task，并以
UI、Session ArtifactCard、workflow DB refs/delivery、独立复跑和 JSON log 交叉判定。

### F11 fresh-profile 真机首轮结果（2026-08-13）

隔离 profile `.testenv/harness-reliability-20260812`、模型 `kimi-k3`、root
`7c028380997a5b22b81219617762238e`、child
`child-1a1c22e7a629b36c0c04f262af4b7f53`。真实 UI 从未选择目录开始：模型调用
`project_directory_select`，用户通过 macOS Open sheet 选择 fresh workspace 并点击“在这里
创建”；全程侧栏保持“已连接”，证明目录前置不再击穿 control WebSocket。macOS 路径确认文案
把最后一个分隔符显示为 `\`，另列展示 followup。

child 实际完成 10 次分块 `write_file`、多次 `run_shell`、一次 `register_artifacts`。独立复核：
`17/17 unittest PASS`；CLI 为 `valid_count=10`、`invalid_count=3`、
`grand_total=400.00`、`top_category=Travel`；`expenses.csv`、`expense_analyzer.py`、
`test_expense_analyzer.py`、`summary.json`、`REPORT.md` 五个 SHA-256 与登记 refs 精确相等。

最终 verdict 仍为 FAIL：finalize 前所有 test/audit node succeeded，但 `finalize` 留在
`succeeded_pending`，workflow 终态为 `workflow_engine:frontier_failure`；父 Run 随即重复尝试
`workflow_spawn`，后两条 child 又以 provider failure 结束。结构化日志当时仍只暴露笼统 frontier
错误；现已给 native commit 增加原始异常类型/stack 日志。

根因是 write tool 与登记工具的 artifact 合同不同：每次 `write_file` outcome 会产生给 UI 的
provisional artifact（路径有效但 `sha256=null`），digest 只在 prepared metadata；terminal 聚合
把这些中间版本与最终 `register_artifacts` 一起严格比对，空 SHA 必然触发 ref mismatch。修复后：

- 非 `register_artifacts` 只有具备非空 SHA 且在 refs 中的标准产物才进入 terminal 聚合；
  provisional envelope 不再污染最终交付。
- `register_artifacts` 仍要求每个 artifact 有 SHA，且 refs 集合与 artifact digest 集合精确相等。
- 回归测试加入“provisional write artifact + final register artifact”真实形态，相关相邻套件
  `102 passed`。

### F11 fresh-profile 真机第二轮结果（2026-08-13）

隔离 profile `.testenv/harness-reliability-20260813`、root
`caf7d550a7705da89cba6b731b9d1c4c`、child `child-2ec4cceeec4df4bb19531561ab0e1e31`。
真实 UI 选择 `workspace/numbers-sum-project`，只确认一次 `workflow_spawn`；授权后约 1.2 秒从弹窗
恢复“工具执行中”，连接全程正常。child 真实生成 5 文件、运行 7 项 unittest/CLI、自检并一次
登记；界面最终 9/9 completed、五张 ArtifactCard、空闲。独立复跑 `7/7 PASS`，CLI/JSON 为
`count=10/sum=55`；该执行 child 终态 `completed/error=null`，只有一个 register effect，artifact
event 与该 child 已投递 parent terminal signal 含同一 5 个 refs，均与本地 SHA 精确相等。首轮
`frontier_failure` 未再出现，首个 child 的复杂任务执行与产物交付主链 verdict 为 PASS；但整个 root
仍不能判 PASS：child 返回后 `workflow_spawn` scoped evidence 为 UNKNOWN，verify gate 触发后又派出
两个验证 child（`child-33b7…`、`child-3913…`，均 provider failure）。因此本段历史记录不再使用
“仅一个 child / root 一次收敛”的表述。

本轮日志同时证明私有工具循环反复发 5/9→6/9；当前公开 durable-task event identity 已改为
node/attempt/transition，同 attempt 内去重，且 early route failure 的日志不再读取未赋值 frontier；
相关进度/engine/log 回归 `76 passed`。

### F11 fresh-profile 真机第三轮结果（2026-08-13）

为隔离 macOS 原生目录选择器偶发 `Open` disabled，本轮使用不写文件、不联网的双算法任务，专门
验证 durable child receipt → parent verify gate 的收敛链。隔离 profile
`.testenv/harness-reliability-20260813-retest`，root
`f0a514f061cb56cebaa498a4a1447b24`、child
`child-0abd7fb98c3faf61504a7f96085c493f`。真实 UI 选择 `kimi-k3` 并点击一次
`workflow_spawn` 的“允许一次”；账本只生成一个 consumed launch ticket 和一个 child。

child committed 三个 `run_shell` receipts：①暴力枚举输出 `A count: 467 / A sum: 234168`；
②容斥公式输出 `B count: 467 / B sum: 234168`；③显式比较输出
`A: (467, 234168) / B: (467, 234168) / MATCH: True`。child 与 root 均 completed，日志只有一次
`workflow_spawn`，没有 `verify_gate_nudge_injected`、没有第二个 child。修复采用精确 lineage：只有
spawn call id、launch ticket、acked child command、已投递 terminal signal、terminal event id、
child completed、`audit.passed=true` 全部对齐时，parent 才能看到 child 的 committed receipts；任何
条件不符继续 UNKNOWN。

真测同时抓出两项可观测性问题并完成代码修正：① root completed 后语义 phase 仍可能保留 running，
观察面曾同时显示 completed 与“正在委派/4/5”；现 aggregate terminal 收束未结 phase/substep/tool，
终态显示“结果/记录已结束”。② UI 已进入“等待授权”时，JSON log 只有后续
`permission_response_applied`；现阻塞事件发出即记录 `harness_blocking_ui_event_emitted`，且不记录
params。后端聚焦 `239 passed`，前端聚焦 `69 passed`，TypeScript 与 Vite build PASS。

上述两个 follow-up 已在 F13 收口：macOS 目录确认文案按平台分隔符渲染；跨 provider 回合增加
只基于 durable failed child 的有界脱敏 objective convergence guard，不误伤实质不同的合法 child。

### F12 workflow_spawn 拒绝旁路与取消态历史卡（2026-08-13）

真机点击 `workflow_spawn` 权限卡的“拒绝”时，旧 root
`074bf4d51e545627ae188d623cafde89` 已把 decision 正确写成 `denied`，却仍在同一秒生成 consumed
launch ticket 与 child `child-a27a142c676cd2096debb6b484f5444d`。根因分两层：Kernel 旧的文本
归一化漏掉 `deny/denied`；补上后，ReAct `control_delegate` 又在 deny outcome 已结算的情况下仍
无条件执行 `_prepare_control_event()`。当前修复同时覆盖两层：统一 negative decision 词表，且
control outcome 非空时禁止 prepare；deny 路径把 `authorization_denied` 反馈给同一模型，不发
delegate。

修复后 fresh UI root `996390c79f1b5c03967f7f42dc408f29` 点击同一“拒绝”按钮：decision 为
`denied`，即时及 8 秒延迟复查均 `child_count=0`、`ticket_count=0`，事件只有
`run.waiting/run.resumed/run.final`，root completed；界面明确显示调用失败为
`authorization_denied` 且没有创建 durable child。后端扩大回归 `328 passed`。

相邻展示缺口也已关闭：历史消息按 child workflow 的 `root_run_id` 查找 parent terminal Session
projection，用 completed/failed/cancelled 覆盖缓存的 running summary，并把同源 public task trace
合并为同一张卡；没有可信起止时间时显示“耗时未记录”，不再伪造“0 秒”。重启后重新打开 Session
`5ec83cb7-51d8-46a8-a7ed-c13de60fcf59`，界面只显示一张
“多步骤任务 / 已取消 / 6/9 / 67% / 耗时未记录”卡，没有“进行中”或重复终态卡。证据：
`.testenv/harness-reliability-20260813-retest/evidence/cancelled-history-projection-fixed.png`；前端相关
回归 `57 passed`，TypeScript 与 debug bundle build PASS。

### F13 macOS 路径、失败委派收敛与 child 终态优先级（2026-08-13）

`ProjectDirectoryCard` 原先无条件用 `\` 拼接目标目录，导致 macOS 原生 Open sheet 选择 Desktop
后把确认文案显示为 `/Users/denny/Desktop\harness-path-test`。当前按所选路径识别 POSIX/Windows
分隔符并处理 `/` 根目录。真实 UI 再次选择 Desktop 后显示
`/Users/denny/Desktop/harness-path-test`；测试在确认卡取消，磁盘复核 `NO_FOLDER_CREATED`。证据：
`.testenv/harness-reliability-20260813-retest/evidence/macos-project-path-separator-fixed.png`
（SHA-256 `10820c586a32c266d6deb6548fb846f1ff404f522242b72602bcc128a58959e0`）。

ReAct Driver 新增跨回合委派 convergence guard。只有 `child_terminal` 明确记录
`terminal_status=failed` 且来源为 `workflow_spawn` 时，才保存最多 8 个脱敏 objective 签名；
后续同 Profile 的相近目标在调用 collaborator/签发 launch ticket 前被拒绝，第一次把
`duplicate_failed_delegation` 回灌模型，第二次以 `delegate_convergence_exhausted` 终止。签名只含
归一化 English token/CJK bigram 的 hash，不存原 objective；目标实质不同或 Profile 不同仍放行。
四条定向回归覆盖相近改写、不同目标、第二次耗尽与 bounded signature，扩大后端套件
`332 passed`。真实 Kimi Run `75f78382a41a5ccaa166d56acb084ad3` 的首个 child 虽在回答文本中
描述 `exit 7` 为失败，但 durable terminal/audit 是 completed；第二个 child 才因 provider failure
进入 failed，因此本次真机没有发生第三次相似 spawn，不能把该 Run 夸大为 convergence guard 的
live 命中证据。

该 Run 另外暴露 Root-only Session projection 的显示缺陷：父 root completed 会把第二个 failed
child 卡错误覆盖成绿色“已完成”。当前 workflow 卡先采用 child 消息自身明确终态；只有 child
仍为 running/waiting 时，才用父 Root projection 收束。重建并重启同一 Session
`782f283d-0ac0-4016-b979-e6f79e7582f6` 后，第一张 child 卡保持“已完成”，第二张显示
“失败 / 5/9 / workflow_node:llm_proposal:provider_failure”。修复前/后证据分别为
`child-failed-parent-completed-before.png`（SHA-256
`baf263242cb8b89f4a3aca7ee67ecf0fabaa3d8cb547655d71d742ae3bc9e183`）与
`child-terminal-over-parent-fixed.png`（SHA-256
`076a25547371b740aa0056f7f3e78e016991356dc973b593b540923bf23c4fd7`）。前端聚焦回归
`44 passed`、TypeScript 与两次 debug bundle build PASS。

扩大 Python 套件曾在测试退出时打印
`Task exception was never retrieved` / `Cannot operate on a closed database`。按 deterministic signal ID
反查后，来源是 `test_scheduled_child_replay_uses_authoritative_terminal_row`：测试创建第二个 fresh UOW
并触发 parent accepted child-signal owner，却未在返回前 drain Kernel，也未关闭两个 UOW。当前测试
按生产 `HarnessRuntime.close` 的同一所有权顺序执行：先 `_drain_active()`，再关闭 fresh/original UOW；
另外两条手动 child provider fault 集成测试也在 SQLite close 前显式 drain。相同扩大套件复跑
`332 passed`，退出零 pending-task/closed-database 告警。修复只校正测试生命周期，不在生产查询层
吞掉数据库关闭错误。
