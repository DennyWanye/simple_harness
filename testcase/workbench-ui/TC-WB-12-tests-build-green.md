# TC-WB-12 — 测试与构建全绿（脚本可判）

> 对应 AC：WB-12 ｜ mac 上执行；在仓库根目录起步
> manual_required: false（纯脚本判定）
>
> **2026-08-12 用户裁决**：步骤 7 冷启动性能对照从本轮范围移除，不执行、不阻断交付；
> 禁止为该项再次启动 `644ab16` 的带 Live2D 历史基线。步骤 1～6 的功能/构建判定不变。

| 步骤 | 命令 | 判定（过/不过） |
|---|---|---|
| 1 | `cd tauri-app && npx vitest run` | **过**：退出码 0，输出无 `failed`（删除桌宠测试后全绿；skip 需逐条列出理由入账）。 |
| 2 | `cd tauri-app/src-tauri && cargo test --lib` | **过**：退出码 0，`test result: ok`，0 failed。 |
| 3 | `cd tauri-app && npm run typecheck`（= `tsc -b --noEmit`） | **过**：退出码 0，零 error。 |
| 4 | `cd tauri-app && npm run build` | **过**：退出码 0（vite build 产物生成）。 |
| 5 | 分两步避免管道吞退出码：`cd tauri-app/src-tauri && cargo check > /tmp/wb12-cargo-check.log 2>&1; echo "exit=$?"; grep -c "^error" /tmp/wb12-cargo-check.log; true`（或等价 `set -o pipefail` 后再用 tee） | **过**：`exit=0` 且 error 计数为 0（mac）——退出码以 cargo check 本身为准，不被 tee/grep 掩盖。 |
| 6 | （plan T16 扩围旁证，非 WB-12 判定主体）`cd backend && uv run pytest tests/companion/ -q` | **过**：全绿——companion 白名单/SQL 迁移 007 回归；失败时单独立项，不并入 WB-12 结论但阻断 DoD。 |
| 7 | **已移除（2026-08-12 用户裁决）**：不再启动 `644ab16` 历史基线，也不执行改版侧计时。 | **SKIPPED_BY_USER**：本轮不阻断交付；不是 PASS，也不得用旧截图补判。 |

判定：步骤 1–5 全部**过**才 WB-12 PASS；步骤 6 仍作为 companion DoD 旁证；步骤 7 经用户
裁决移除，不再阻断 DoD。所有实际执行命令保留原始输出。
路径备注：若 vitest/build 脚本名不同（如 `npm test`），以 package.json scripts 等价命令替换并记录，不得降低覆盖。
