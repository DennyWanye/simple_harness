# DeepResearch v6 完成交接

> 交接时间：2026-07-18  
> 仓库：`F:\projects\deskpet`  
> 分支 / 起始提交：`master` / `0117ad76`  
> 当前结论：T11/T12/T13、自动化发布门、真实 UI 验收和 release identity fixture 受控提交均已完成。本文后续“剩余任务”正文仅作为历史记录。

## 0. 最终状态（取代下方旧交接指令）

- 新 run 默认 `deep_research/v6`，v1-v5 历史恢复兼容保留。
- 后端 815、前端 822、Rust 73、TypeScript/Vite/cargo check 全绿。
- 最终 release identity 下真实 UI completed (`e58b0281…`)、partial (`2ed15e00…`)、insufficient/generate-now 双击 (`eeab90ba…` / command `9582bb5c…`) 与重启 history 全通过，证据在 [`evidence/t13-release-20260718/`](./evidence/t13-release-20260718/)。
- 健康网络三次校准 0 timeout/cancel/预算违规，详见 [`timing-calibration.json`](./timing-calibration.json)。
- 基础模型已切换并实证为 `deepseek-v4-pro`（1M）；生成请求当前受中转站余额不足 402 限制。

## 1. 新 session 的任务

继续执行已定稿的 [`plan.md`](./plan.md)，完成全部剩余实现、自动化测试、故障矩阵、性能校准、真实 UI 测试、默认版本切换和架构事实源回写。

不要重新做需求澄清、架构挑战或 plan 迭代；Round 7 已 PASS，计划已由用户确认。先重新锁定当前绿色基线，若没有回归失败，不要从头重做 Q1。

必须使用 `plan-test:plan-test` 流程继续收尾。禁止使用 Agent-Reach。

## 2. 接手后先读

按顺序完整阅读：

1. 仓库根目录 `AGENTS.md`
2. [`plan.md`](./plan.md)
3. [`acceptance.md`](./acceptance.md)
4. [`v6-contracts.md`](./v6-contracts.md)
5. [`execution-results.md`](./execution-results.md)
6. [`architecture-baseline.md`](./architecture-baseline.md)
7. `ARCHITECTURE/index.md`、`ARCHITECTURE/DeepResearch.md`、`ARCHITECTURE/PROJECT_STATUS.md`

`acceptance.md` 仍是验收唯一真相来源。`execution-results.md` 记录已验证事实，本文件只负责交接边界。

## 3. 已完成且不要重复的结果

Q1 真实场景已经通过：

```text
深入调研：2024年中国总人口和出生人口分别是多少？优先国家统计局。
```

- run id：`1b91f6d7a6044cfaad6f9b9b909485b0`
- workflow：`deep_research@v6`
- 总耗时：5.08 秒
- 答案：2024 年末总人口 `140828 万人`；出生人口 `954 万人`
- 一手来源：`https://www.stats.gov.cn/sj/zxfb/202502/t20250228_1958817.html`
- 阶段耗时：compile 58ms；官方档案发现 2.78s；目标页抓取约 1.10s；collect-pages 4.14s；extract 153ms；assess/render 354ms
- 重启后 exactly-once：1 user、1 final_assistant、1 artifact_card、1 completed run、0 个未交付 delivery，`recovered_deliveries=0`
- 自动化基线：v6/official/fetch `91 passed`；v5/recovery/delivery `281 passed`；前端 `30 passed`；`npm run build:relay` PASS（只有 warning）

证据目录：[`evidence/q1-20260718-r9/`](./evidence/q1-20260718-r9/)

关键截图：

- [`final-ui-before-restart.jpg`](./evidence/q1-20260718-r9/final-ui-before-restart.jpg)
- [`final-ui-after-restart.jpg`](./evidence/q1-20260718-r9/final-ui-after-restart.jpg)

已完成的基础切片还包括：v6 runtime identity/activation、strict scalar contracts、官方源 resolver、deadline/timing 基础、evidence/provenance closure、integrity gates、terminal materialization、SessionDB durable projection，以及 Q1 的真实源码 Tauri 入口和重启恢复。

注意：这些结果只证明 Q1；不能据此把完整计划或 v6 发布状态标为完成。

## 4. 剩余执行顺序

### T11：完整 intent 和 conditional fan-out

完成并验收：

- comparison：subject × axis matrix，每个 cell 独立 assessment；区分 fact / inference / preference
- Top-N：动态候选、unique key、统一 ranking/as-of、`min_items=N`，不足时必须是 partial
- policy：issuer/document/date/commitment 与 impact inference 分离
- open research：conclusion/limitation/counterevidence/uncertainty 最低覆盖
- fan-out 只在独立 work groups ≥ 3、merge key 明确且预算允许时开启；exact fact 必须保持 fan-out off
- 所有 lane 写同一个 fact-batch protocol，主 graph 做唯一 assessment

必须补齐 golden spec、assessment matrix、renderer snapshot、deterministic merge 和 budget/fan-out 测试。退出条件是四类 requirement 仍只有一个语义 owner，并完全复用三道门和 terminal manifest。

### T12：continuation head、控制命令、兼容恢复

严格按 `plan.md` 的 SQL 和 closure 约束实现：

- v6 continuation 只能从服务端已验证 terminal manifest 取得 snapshot
- `workflow_research_continuation_heads` 保证每个 parent 只有一个 canonical child
- 两个 repository connection、不同 idempotency key 并发 continue 也不能产生 sibling
- `manifest_ref` 对 v6 物理保存 bare snapshot blob digest；wire ref 只在 domain 输出格式化
- inherited refs 必须恰好等于 server-side snapshot closure，并验证 parent checkpoint ownership
- generate-now/cancel/accepted/observed/settled/consume 继续复用已有 control journal
- v1..v5 historical reads、v5 continuation、v6 continuation、terminal replay 全部保持兼容
- retention 必须覆盖 head、snapshot、spec blob reachability

必须覆盖 fault injection、notify 前崩溃恢复、pin 过期后返回既有 child、child→grandchild、pre-v4 v6 fail-closed migration、900s cap 和资源预算竞争。

### T13：完整验收和发布门

按计划完成：

1. scoped 后端/前端测试，以及 workflow/retrieval 回归、TypeScript/Vite、Rust check。
2. fault matrix：blob tamper、checkpoint/restart、commit/push 间崩溃、SessionDB 重试、fenced epoch、无 WS client、duplicate continue。
3. Computer Use 真实 UI：completed、partial、insufficient/generate-now、reconnect/history、duplicate action 五类 case。
4. 保存截图、日志、checkpoint/manifest/receipt/SessionDB 交叉证据。
5. 生成 `timing-calibration.json`：
   - 固定时钟/慢阶段自动化各 ≥ 20 次
   - 离线 official-exact-fact 完整路径 ≥ 20 次，每次 ≤ 10 秒
   - 健康网络 SC-STATS-2 ≥ 3 次
   - 记录 sample count、p50、p95、max、timeout/cancel count 和 environment status
   - healthy run 任一次 > 120 秒或任一 page > 20 秒都算 deadline violation
6. 所有开发态 P0/P1 全绿后，才冻结 release hashes、把新 root 默认切到 v6。
7. 清除 dev override，用全新隔离 user-data 以“默认 v6 release bundle”重跑 identity/recovery、SC-STATS-2、reconnect/history 和 scoped regression。
8. 完成度审计和测试覆盖审计都 PASS 后，同一次交付更新 architecture、testcase 和 results。

## 5. 当前版本边界

- v5 仍是默认版本。
- v6 只通过隔离开发 override 开启。
- 只有 T13 全量验收通过后才能把新 root 默认切到 v6；不能提前开启。
- 旧 v5 run 永远按 v5 identity 恢复。
- v6 release hash 冻结后，如 graph/contracts/hash 再变化，必须升 v7。

## 6. 工作树边界

当前工作树非常脏，包含大量既有和本轮变更，且许多 v3/v4/v5/v6 文件仍是 untracked。没有提交，也不能假设所有 dirty 文件都属于本计划。

接手第一步运行：

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe' status --short
```

必须遵守：

- 不执行 `reset --hard`、`clean`、`checkout --` 或批量丢弃变更。
- 不覆盖用户的无关修改。
- 提交前逐文件核对和窄 staging；除非用户明确要求，不要把整个脏工作树一起提交。
- 本计划目录本身目前也是 untracked；所有 Q1 证据都在其中。

本轮最关键的新/改文件包括但不限于：

```text
backend/deskpet/workflows/definitions/deep_research_v6_*.py
backend/deskpet/workflows/definitions/v6/
backend/deskpet/workflows/adapters/deep_research_v6_evidence_runtime.py
backend/deskpet/retrieval/official_sources.py
backend/deskpet/retrieval/contracts.py
backend/deskpet/retrieval/fetch_extract.py
backend/deskpet/workflows/runtime_adapters.py
backend/deskpet/workflows/store/research_repository.py
backend/deskpet/workflows/store/checkpointer.py
backend/deskpet/workflows/store/schema.py
backend/deskpet/workflows/terminal_projection.py
backend/deskpet/memory/migrations/011_message_projection_visibility_v19.sql
backend/deskpet/memory/session_db.py
backend/deskpet/memory/migrator.py
backend/main.py
tauri-app/src/code-panel/ws.ts
tauri-app/src/code-panel/ws.chat.test.ts
tauri-app/src/stores/sessionsStore.ts
tauri-app/src/types/messages.ts
backend/tests/test_deep_research_v6_*.py
backend/tests/test_workflow_deep_research_v6_q1.py
backend/tests/test_workflow_research_repository.py
backend/tests/test_memory_v19_projection_visibility.py
```

## 7. 基线验证建议

先用仓库虚拟环境跑聚焦回归，确认接手时没有漂移：

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_deep_research_v6_contracts.py tests/test_deep_research_v6_compiler.py tests/test_deep_research_v6_evidence_ledger.py tests/test_deep_research_v6_evidence_runtime.py tests/test_deep_research_v6_exact_fact.py tests/test_deep_research_v6_exact_report.py tests/test_deep_research_v6_integrity.py tests/test_deep_research_v6_delivery.py tests/test_workflow_deep_research_v6_q1.py tests/test_official_sources.py tests/test_fetch_extract_service.py
```

然后根据改动面补跑 v5/recovery/delivery、repository/retention、memory v19、frontend 和 build。最终以 `plan.md` 的 T13 全量矩阵为准，不以这条聚焦命令代替 DoD。

前端常用入口：

```powershell
cd F:\projects\deskpet\tauri-app
npm run test -- --run
npm run build:relay
cargo check --manifest-path src-tauri\Cargo.toml
```

如果 ambient `npm/node` 不可用，使用 Codex 自带 runtime：

```text
C:\Users\Administrator\AppData\Local\OpenAI\Codex\runtimes\cua_node\03b1cdac8af3a530\bin\npm.cmd
```

## 8. 真实源码 Tauri 启动纪律

本次交接时，Q1 的 Tauri/deskpet/backend 进程仍在运行；这只是瞬时状态，新 session 必须重新检查，不能假设仍然有效。

真实测试需要给 Tauri 进程注入：

```text
DESKPET_DEV_MODE=1
DESKPET_DEV_DEEPRESEARCH_VERSION=v6       # 仅开发态 T11/T12/T13 前半段
DESKPET_USER_DATA_DIR=<每组测试独立目录>
DESKPET_BACKEND_DIR=F:\projects\deskpet\backend
DESKPET_PYTHON=F:\projects\deskpet\backend\.venv\Scripts\python.exe
DESKPET_BACKEND_PORT=8100
DESKPET_VITE_PORT=5173
```

从 `F:\projects\deskpet\tauri-app` 启动：

```text
C:\Users\Administrator\AppData\Local\OpenAI\Codex\runtimes\cua_node\03b1cdac8af3a530\bin\npm.cmd run tauri:dev
```

同时把上述 runtime 的 `bin` 放到启动进程的 `PATH` 前部，确保 `node` 可发现。

硬约束：

- 不要手动启动 backend；Tauri 会自己 spawn。
- 不要手动再启动 Vite；`tauri dev` 的 `beforeDevCommand` 会启动唯一 Vite。
- 日志必须出现 `[backend_launch] Dev python=... backend_dir=F:\projects\deskpet\backend`。若出现 `Bundled exe=...`，说明测的是旧 frozen backend，证据无效。
- 真实 UI 必须用 Computer Use 真点击/真输入；每个动作前声明坐标、动作和期望。
- 禁止用 WebSocket 直注、launcher/API 直调、pytest 回放或数据库手工注入冒充 UI E2E。
- 每个 case 都保存动作前后截图和 backend/Tauri 日志；失败至少尝试三种合规 workaround 后才能归因环境。

T13 默认 v6 复验时必须移除 `DESKPET_DEV_DEEPRESEARCH_VERSION=v6`，使用全新 user-data，证明默认发布链路而不是 override。

## 9. 已知容易误判的点

- engine `completed` 不等于答案已交付；必须同时检查 immutable manifest、delivery receipts、SessionDB projection 和 UI/history。
- WebSocket 只是实时加速，不是 durable owner；断线/重启后必须由 SessionDB 恢复。
- artifact envelope 不能以 raw JSON 显示，也不能靠前端造正文。
- v6 exact fact 不能在生产代码硬编码题目、140828、954 或国家统计局页面。
- timing log 要记录每个实际阶段，并能从 run → node → search/fetch child span 重建；不能只记录总耗时。
- 网络外因只能分类为 `environment_blocked`，不能通过扩大 deadline 把超时变成 PASS。
- T11/T12 会继续改变开发态 v6 implementation hash，所以不要承诺跨 build 恢复；T13 冻结 release hash 后再做发布恢复矩阵。

## 10. 完成定义

只有以下全部满足才能结束新 session：

- T11、T12、T13 的动作、测试和退出门全部完成。
- `acceptance.md` 的所有 AC/场景逐条可追溯，完成度审计 PASS。
- 测试覆盖审计 PASS。
- 自动化、fault matrix、性能校准和真实 UI 五类 case 全部有证据。
- 默认 v6 release bundle 复验全绿，v5 historical recovery 不回归。
- `ARCHITECTURE/DeepResearch.md`、`ARCHITECTURE/PROJECT_STATUS.md`、testcase、execution results 和 plan 状态在同一次交付回写。
- 没有把无关 dirty changes 混入提交。

## 11. 可直接粘贴到新 session 的首条指令

```text
请在 F:\projects\deskpet 继续执行 DeepResearch v6 已定稿计划。先完整阅读仓库 AGENTS.md，以及 plans/2026-07-17-deepresearch-answer-contract-stability/ 下的 HANDOFF.md、plan.md、acceptance.md、v6-contracts.md、execution-results.md，再阅读 ARCHITECTURE/index.md。使用 plan-test:plan-test 流程；Q1 已 PASS，不重做 plan 迭代，也不要从头重复已完成工作，先锁定绿色基线后从 T11 开始，依次完成 T11→T12→T13、全部 AC/固定场景/DoD、自动化测试、fault matrix、timing-calibration 和 Computer Use 真实 UI/重启恢复测试。禁止使用 Agent-Reach；保留当前脏工作树和用户无关改动，不要 reset/clean，不要手动启动 backend 或 Vite。只有所有门禁全绿后才把新 root 默认切到 v6，并在同一次交付回写 ARCHITECTURE、testcase 和 execution results。
```
