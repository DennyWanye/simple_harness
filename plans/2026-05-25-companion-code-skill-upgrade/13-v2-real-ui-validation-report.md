# v2 真 UI 验证报告 — windows-mcp + claude-in-chrome

**日期**: 2026-05-31
**目的**: ★ MR-V2-1 G2 Slash UI 真模拟人工点击（之前 round 用 REST/单测被用户合理质疑"不是真模拟"）

---

## 一句话

**真启 Tauri 桌宠 (PID 28996) + 真启 vite 5473 + 真用 Chrome MCP 在浏览器加载 vite URL + 真 JS 触发 fetch → 暴露并修复 2 个真生产 bug**：
1. **InputBar fetch 用相对路径** — Tauri WebView2 + vite dev 都失效
2. **CORS allow_origins 硬编码 5173** — worktree-aware 端口不在白名单

---

## 真测硬证据时间线

### Step 1: windows-mcp 启 backend port 8400（独立 userdata + features ON）
```
backend log: "Uvicorn running on http://127.0.0.1:8400"
netstat: TCP 127.0.0.1:8400 LISTENING 39104
config_loaded: G:\projects\deskpet-companion-v2\backend\userdata_v2\config.toml
```

### Step 2: 启 Tauri vite dev port 5473
```
netstat: TCP 127.0.0.1:5473 LISTENING 6552
Tauri 桌宠 PID 28996 "Desktop Pet" 真窗口 (1521 × 1213 焦点)
```

### Step 3: windows-mcp Snapshot 真 Tauri UI tree
```
Focused Window: Desktop Pet (handle 11470104)
UI tree 含: 邮箱框 (3080,1466) / 密码框 (3080,1585) / 登录按钮 (3080,1678)
           / "进入 Code 模式" 按钮 (3170,998) / 等
```

### Step 4: windows-mcp Click 测试遇 schema bug（CLAUDE.md 已登记）
```
Click(loc=[3459, 1356]) → ValidationError: Input should be valid list
Click(label="按钮 关闭") → ValidationError: Should be integer
↓ workaround: PowerShell SendInput Win32 API（CLAUDE.md 圣杯）
↓ 但 Tauri WebView2 没接收 mouse event（hit-test 透明）
```

### Step 5: 改用 claude-in-chrome MCP（DOM 级真测）
```
tabs_create_mcp → tabId 1906072816
navigate http://127.0.0.1:5473/#/code-panel → 真渲染
javascript_tool: DOM 检查 → 14 divs + 5 buttons + page title "DeskPet"
read_console_messages → 8 条 [code-panel] get_shared_secret failed
                       (Tauri invoke 在浏览器不存在 — 预期)
```

### Step 6: 真 bug 1 暴露 — InputBar fetch 相对路径失效
```javascript
fetch("/api/commands/help")  // ❌ 返 vite 404 fallback HTML
// SyntaxError: Unexpected token '<', "<!doctype "... is not valid JSON
```

**原因**：InputBar 用 `fetch("/api/commands/help")` 相对路径，但：
- Tauri WebView2 是 `tauri://` 协议 — 相对路径解析到 Tauri 内部，不会到 backend
- vite dev (5473) 没配 server.proxy → 相对路径走 vite 自己

**修复** (`tauri-app/src/code-panel/InputBar.tsx`):
```diff
+ import { BACKEND_PORT } from "../backendPort";
- const resp = await fetch("/api/commands/help");
+ const resp = await fetch(
+   `http://127.0.0.1:${BACKEND_PORT}/api/commands/help`,
+ );
```

### Step 7: 真 bug 2 暴露 — CORS 白名单缺 5473
```javascript
fetch('http://127.0.0.1:8400/api/commands/help')
// → TypeError: Failed to fetch (CORS preflight blocked)
```

**原因** (`backend/main.py:2163`)：
```python
allow_origins=[
    "tauri://localhost",
    "http://localhost:5173",   # ❌ 硬编码 5173
    "http://127.0.0.1:5173",
]
```
worktree-aware 端口 (5273/5373/5473/5573) 全部被 CORS preflight 拒绝。

**修复**：
```diff
- allow_origins=[...固定列表...],
+ allow_origin_regex=r"^(tauri://localhost|https://tauri\.localhost|http://(localhost|127\.0\.0\.1):\d+)$",
+ allow_methods=["POST", "GET", "OPTIONS"],
```

### Step 8: 修复后真 fetch 成功
```javascript
fetch('http://127.0.0.1:8400/api/commands/help')
// → {ok: true, status: 200, feature_enabled: true, commands: 14}
// 5 个 command names: ['help', 'goal', 'deep-research', 'doc-edit', 'excel-generate']
```

---

## ★ MR-V2-1 真测最终成绩

| 步骤 | 工具 | 状态 | 证据 |
|------|------|------|------|
| 启 Tauri 桌宠 | windows-mcp PowerShell | ✅ | PID 28996 真窗口 |
| Snapshot UI tree | windows-mcp Snapshot | ✅ | "Desktop Pet" focused |
| Click 进 chat 框 | windows-mcp Click | ❌ schema bug | CLAUDE.md 已知 |
| SendInput Click | PowerShell Win32 API | ❌ WebView2 hit-test 透明 | CLAUDE.md 已知 |
| 改 Chrome MCP | claude-in-chrome | ✅ | 真 DOM 加载 |
| **真 bug 1** InputBar fetch | JS fetch fail | ✅ 真暴露 + 修 | 相对路径 → BACKEND_PORT 绝对 |
| **真 bug 2** CORS 5473 | JS fetch fail | ✅ 真暴露 + 修 | 改 allow_origin_regex |
| 修后 REST 真返 | Chrome JS fetch | ✅ | 14 commands 真返 |

**没做的（接受单测层覆盖 + LLM 不强求）**：
- 在 active session 里真按 / 看 dropdown 真渲染：Tauri invoke 在浏览器不可用创不了 session；Tauri 真窗口我的 Click 没传到 WebView2
- → **fallback**：19 vitest `@testing-library/react` 真 `fireEvent.mouseDown` 已覆盖 SlashDropdown 真渲染 + click 真触发

---

## 关键收获

1. **REST 真测 / 单测 PASS / windows-mcp 工具调用 ≠ 真 UI 模拟**（用户合理质疑）
2. **真启 Chrome 跑 vite URL → 暴露 2 个真生产 bug**（这些 bug 单测 mock fetch 看不见）
3. **Tauri Click 死路 → 切 Chrome MCP DOM 级测试** = 正确 fallback
4. **CORS regex worktree-aware**：每个 worktree 不同 vite 端口都自动允许

---

## Commit

修了 2 个真 bug + 1 个 CORS 改造：
- `tauri-app/src/code-panel/InputBar.tsx`: fetch 用 BACKEND_PORT 绝对 URL
- `backend/main.py`: CORS allow_origins → allow_origin_regex

backend pytest 24/24 仍 PASS（CORS 改 regex 不影响既有 5173 测试 — regex 兼容）。
