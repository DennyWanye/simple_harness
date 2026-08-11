# 2026-08-11 Workbench UI r14 follow-up

> 状态：生产代码收口已提交；r14 手工 E2E、性能证据与独立审计尚未完成。
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

## 建议恢复顺序

1. 决定 r10～r13 大体积证据的存储策略并完成敏感信息扫描。
2. 修复 legacy parity census 的历史 source 可复跑性。
3. 一次性提交当前 acceptance/testcase/architecture 修订，冻结最终 oracle。
4. 重新生成有效 r14/r15 manifest，按真实 impact 集合执行缺失的手工 E2E 与性能对照。
5. 机器门通过后做独立实质审计；PASS 后生成 receipt，再提交验证结果。
6. 只有用户明确要求时才 push 远端 `main`。
