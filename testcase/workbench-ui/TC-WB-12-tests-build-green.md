# TC-WB-12 — 测试与构建全绿（脚本可判）

> 对应 AC：WB-12 ｜ mac 上执行；在仓库根目录起步
> manual_required: false（纯脚本判定）

| 步骤 | 命令 | 判定（过/不过） |
|---|---|---|
| 1 | `cd tauri-app && npx vitest run` | **过**：退出码 0，输出无 `failed`（删除桌宠测试后全绿；skip 需逐条列出理由入账）。 |
| 2 | `cd tauri-app/src-tauri && cargo test --lib` | **过**：退出码 0，`test result: ok`，0 failed。 |
| 3 | `cd tauri-app && npx tsc --noEmit` | **过**：退出码 0，零 error。 |
| 4 | `cd tauri-app && npm run build` | **过**：退出码 0（vite build 产物生成）。 |
| 5 | 分两步避免管道吞退出码：`cd tauri-app/src-tauri && cargo check > /tmp/wb12-cargo-check.log 2>&1; echo "exit=$?"; grep -c "^error" /tmp/wb12-cargo-check.log; true`（或等价 `set -o pipefail` 后再用 tee） | **过**：`exit=0` 且 error 计数为 0（mac）——退出码以 cargo check 本身为准，不被 tee/grep 掩盖。 |
| 6 | （plan T16 扩围旁证，非 WB-12 判定主体）`cd backend && uv run pytest tests/companion/ -q` | **过**：全绿——companion 白名单/SQL 迁移 007 回归；失败时单独立项，不并入 WB-12 结论但阻断 DoD。 |
| 7 | **冷启动对照（非功能·性能；阈值 ±3s，裁决第 4 条；基线锚点配方=裁决第 10 条）**：<br>① **基线锚点**：`git worktree add <tmp> 644ab16` 检出基线构建，先启动一次完成预热编译并关闭，再启动**第二次**并秒表计时（进程拉起 → 窗口可点击），记入 `plans/2026-08-04-workbench-ui/baseline.md` 补记行；<br>② **改版侧**：同机同 dev 模式，同样预热一次后测**第二次启动**计时。 | **过**：改版后第二次启动耗时 ≤ 基线第二次启动 + **3 秒**。两次计时原始值 + 起止判定点截图入账；未做预热直测首次启动（含编译时间）的计时无效。 |

判定：步骤 1–5 全部**过**才 WB-12 PASS；步骤 7 判定非功能·性能条款（独立结论，不并入 WB-12 但阻断 DoD）；所有命令原始输出入账（phase-4 账本）。
路径备注：若 vitest/build 脚本名不同（如 `npm test`），以 package.json scripts 等价命令替换并记录，不得降低覆盖。
