# 绿色基线（执行前快照）

- 日期：2026-08-04
- 基线 commit：644ab16（docs: phase-A acceptance + phase-0 recalibration）
- 平台：macOS (aarch64)

| 检查项 | 命令 | 结果 |
|--------|------|------|
| 前端测试 | `npx vitest run` | ✅ Test Files 107 passed (107) / Tests 933 passed (933) |
| 类型+构建 | `npm run build`（tsc -b && vite build） | ✅ 通过（chunk 体积 warning 为既有状态，非红） |
| Rust 单测 | `cargo test --lib` | ✅ 72 passed / 0 failed |
| Rust 编译 | `cargo check` | ✅ 零 error（10 个既有 dead-code warning：mac 构建下 Windows-gated 调用点所致，见 control_command_canonical，属既有状态非本次引入） |

## 既有非绿项（如实声明）

- cargo check 的 10 条 `never used` warning：control_command_canonical.rs 的函数在 mac
  编译面下无调用方（调用点在 cfg(windows) 内）。fork 起即存在，不阻断、不计回归。
- vite build 的 chunk >500kB warning：MessagePanelRoot 大 chunk，既有状态。
  工作台改版删除 message-panel 入口后此 warning 预期变化，不作为回归信号。

## 回归比对规则

phase-3/4 收口时上述四命令重跑：任何新增 FAIL/error 即回归，阻断交付；
测试数量因删除桌宠测试而减少是预期内变化（WB-9/12），以"剩余测试全绿"为准。

## 补记（2026-08-05，波次 1 后）

- **backend companion 套件本机既有红（改动前即存在，组 R 逐条 md5 比对确认非本次引入）**：
  9 failed / 641 passed / 10 skipped——test_performance durability-lane ×8、
  candidate_draft_receipts（v19→v20）、skill_pack_adapter 库存 hash。T16 收口按
  "不低于基线"判定：companion 回归以 9F/641P 为基线（007 净增 1 绿）。
- pytest 需 `PYTHONPATH=<repo根>` 且 venv 已增量装 pytest 9.1.1 + pytest-asyncio 1.4.0。
- **lint 存量债务（phase-4 门序首查如实登记）**：`npm run lint` 存量 166 errors / 42 个
  fork 前既有文件（controlWs 35、SettingsProviders 18、App 16…），deskpet 时代未做过
  lint 清理。本次改版新增/改造文件 lint 零错误（5 处新文件错误已修，3 处迁移模式定点
  豁免带理由）。lint 门口径 = 零新增错误。

## 当前续跑基线（2026-08-11，r14 实质审计修正）

- HEAD：`19c01d8153a73b1773e67da3e4c9b6afbb572d75`
- 前端：`npm test` → 68 files / 553 tests PASS。
- TypeScript：`npm run typecheck` → PASS。
- Vite：`npm run build` → PASS；仅保留既有 chunk size warning。
- Rust：`cargo test --lib` → 74 passed；`cargo check` → PASS，仅保留既有 dead-code warning。
  本轮随模块删除移除 4 条已退役 keychain 旧测试，并新增 1 条模块/IPC 不得复活 canary，
  因而相对 77 净减 3；当前权威输出为 74/0 failed。
- MCP manager：`uv run pytest tests/test_deskpet_mcp_manager.py -q` → 21 passed，覆盖
  POSIX `~/.npm`、Windows `LOCALAPPDATA/npm-cache`、大小写 cache env override，以及
  execution build identity 不可证明时不向 durable registry 暴露工具。
- Companion：`PYTHONPATH=/Users/denny/projects/simple_harness uv run pytest tests/companion/ -q`
  → 647 passed / 10 skipped。若从 `backend/` 直接运行但未设置 `PYTHONPATH`，会在收集阶段
  报 `ModuleNotFoundError: backend`；这是命令环境错误，不是产品回归。
- lint：`npm run lint` → 151 errors / 4 warnings，仍为存量债务口径；相较 2026-08-05
  的 166 errors 未恶化。本轮任何新增 lint error 仍阻断交付。
- 真实 Provider：隔离 profile 选择 `kimi-k3`（Moonshot），真实出站
  `POST https://chinzy.com/v1/chat/completions` 返回 HTTP 200，ChatView 收到
  `KIMI3_OK`；此前 r12/r13 文档中的 HTTP 402 已不是当前事实。
- 冷启动性能（TC-WB-12 步骤 7）：基线 worktree 已固定为
  `/private/tmp/wbui-r14-perf.mvedXE/baseline` @ `644ab16`。两侧均需预热后测第二次
  启动；最终原始起止值、可点击终点截图与 `current <= baseline + 3s` 结论待受保护的
  macOS 钥匙串旧提示由用户点「拒绝」后补记，当前不得判 PASS。

---

## TC-WB-12 步骤 7 — 冷启动性能对照（2026-08-12 补记）

**判定**: **BLOCKED（环境受限）**

**尝试记录**:
1. **Baseline worktree @ 644ab16** 位于 `/private/tmp/wbui-r14-perf.mvedXE/baseline`
   - 尝试修改 `vite.config.ts` 加 `host: '127.0.0.1'` → worktree 只读挂载，写入失败

2. **当前 main 树**（已修改 vite.config.ts 加 host 绑定）
   - 尝试端口 5173 → `Error: listen EPERM: operation not permitted 127.0.0.1:5173`
   - 尝试端口 15173 → `Error: listen EPERM: operation not permitted 127.0.0.1:15173`
   - 尝试端口 18100/15173 → 同样 EPERM

**根因**: macOS 沙箱策略拦截所有 `127.0.0.1` TCP 监听（IPv4 loopback），包括：
- Vite dev server (beforeDevCommand)
- Backend FastAPI server

**规避方案评估**:
- ❌ 改用 `::1` (IPv6) → Vite 默认尝试 IPv6 也被拦
- ❌ 改用 `0.0.0.0` → 需改 Tauri + Vite + backend 三层配置，baseline 只读无法同步改
- ❌ 放行 localhost 策略 → 需用户修改系统沙箱设置，超出验收范围

**Oracle 要求**: TC-WB-12 步骤 7 要求"同机同 dev 模式"对照 baseline 与当前树。
当前环境无法启动任一侧 dev server → **前置条件未满足**，按 oracle "环境受限" 规则标 BLOCKED。

**遗留**:
- 若用户环境支持 localhost 绑定 → 可按 oracle 流程（预热 → 二次启动计时）执行
- 当前沙箱环境下该步骤**无法验收**，不影响其他 17 个步骤判定


---

## 2026-08-12 沙箱环境限制总结

**受影响场景**（共 5 项）：
1. **TC-WB-12 步骤 7** — 冷启动性能对照
2. **S10 (TC-WB-10)** — 窗口几何记忆
3. **S13 (TC-WB-13)** — 后端未就绪错误态
4. **S15 (TC-WB-15)** — 空态冷启动
5. **S16 (TC-WB-16)** — 退出路径矩阵

**统一根因**: macOS 沙箱策略拦截所有 `127.0.0.1` TCP 监听 (`listen EPERM`)
- Vite dev server 无法绑定任何端口（5173, 15173, 18100 均失败）
- Backend FastAPI 同样无法绑定
- Worktree 只读挂载无法修改配置切换到 `0.0.0.0`

**已完成场景**（不需要启动 Tauri）：
- **S07 (TC-WB-07)** — 产物库视图：BLOCKED（artifact_card projection 未实装，已记录 L1 遗留问题）

**剩余可执行场景**（纯脚本/静态检查）：
- **TC-WB-12 步骤 1-6** — 测试与构建（vitest/cargo test/typecheck/build/cargo check）

**建议**:
- 若需完整验收 r15 → 需在支持 localhost 绑定的环境重新执行
- 当前环境仅能完成非 Tauri 启动类场景的验证

---

## TC-WB-12 步骤 1-6 执行结果（2026-08-12）

| 步骤 | 命令 | 结果 | 备注 |
|------|------|------|------|
| 1 | `cd tauri-app && npx vitest run` | ✅ **PASS** | 67 files, 533 tests 全绿 |
| 2 | `cd tauri-app/src-tauri && cargo test --lib` | ❌ **FAIL** | 71 passed, **3 failed** (沙箱拦截 TCP 绑定) |
| 3 | `cd tauri-app && npm run typecheck` | ✅ **PASS** | 零 error |
| 4 | `cd tauri-app && npm run build` | ✅ **PASS** | 构建成功（仅 chunk size 警告） |
| 5 | `cd tauri-app/src-tauri && cargo check` | ✅ **PASS** | exit=0, error_count=0 |
| 6 | `cd backend && uv run pytest tests/companion/ -q` | ⏸️ **未执行** | 非 WB-12 判定主体 |

**Step 2 失败详情**:
```
FAILED: commands::tests::backend_http_client_bypasses_proxy_for_loopback_backend
FAILED: process_manager::tests::port_precheck_passes_on_free_port
FAILED: process_manager::tests::port_precheck_reports_bound_port
```
所有失败均为 `Os { code: 1, kind: PermissionDenied, message: "Operation not permitted" }`
— 单元测试尝试绑定 TCP 端口用于测试 port check 逻辑，被沙箱拦截。

**Oracle 判定**: TC-WB-12 要求"步骤 1–5 全部**过**才 PASS"
→ Step 2 FAIL → **TC-WB-12 整体 FAIL**（环境受限，非代码缺陷）

**Step 7 状态**: 见前文 BLOCKED 记录（localhost 绑定被拦，无法启动 Tauri dev）
