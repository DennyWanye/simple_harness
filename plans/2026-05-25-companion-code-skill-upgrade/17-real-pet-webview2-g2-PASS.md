# 真桌宠 WebView2 G2 端到端真测 PASS — 推翻我所有错误结论

**日期**: 2026-05-31
**触发**: 用户坚持质疑 "我记得 windows-mcp 能点桌宠" + "是不是 modal 限制"
**结果**: **真桌宠 WebView2 里 G2 /命令完整状态机真 PASS** + 推翻我之前 4 个错误结论

---

## 我被用户连续质疑逼出来的真相

用户一句"我记得应该可以用 windows mcp 来点击桌宠"逼我去查证 —— 找到
`UI_AUTOMATION_BREAKTHROUGH.md`（历史 16 case 真 PASS），直接证明我错了。然后
一路深挖，推翻了我之前**全部 4 个错误结论**：

| 我之前的错误结论 | 真相 |
|----------------|------|
| ❌ "Chromium 拒合成 click" | **能点** — CDP 真点 textarea → activeElement=TEXTAREA + 历史 16 case |
| ❌ "WebView2 鼠标穿透" | 没穿透 — WindowFromPoint 证明光标在桌宠 webview 上 |
| ❌ "DPI 我换算了" | 没换算对 — OS scale **150%** + WebView **dpr 2.13**，我目测 20px 小目标点空 |
| ❌ "modal 不登录没法点后面"（用户假设也排除） | 不是 — CDP 证明 textarea 能点能 focus |

---

## 真因（2 个，都不是 deskpet bug）

### 真因 1: DPI 坐标换算（我的测试方法错）

- 这台机器 OS scale = **150%**（逻辑 2560×1440 vs 物理 3840×2160）
- WebView dpr = **2.13**（WebView 内部额外缩放）
- SetCursorPos（DPI-unaware）用逻辑坐标，我一直传物理坐标 → x>2560 被 clamp
- 我目测登录窗 × 按钮（20px 小目标）+ 错误换算 → 点空几像素
- 那次 16 case 成功（2026-05-26）当时 scale=100%，坐标换算简单

### 真因 2: dev 模式默认用打包 exe（部署/dev 流程发现）

- 桌宠 `backend_launch.rs` 优先级：
  - **Priority 1**: 设 `DESKPET_BACKEND_DIR` env → 跑 `python main.py` 源码
  - **Priority 2**: `target/debug/backend/deskpet-backend.exe`（打包 exe）
- 我之前启 Tauri **没设 DESKPET_BACKEND_DIR** → fallback 到打包 exe
- 打包 exe 是旧版本，**没有我新加的 `/api/commands/help`** → 真桌宠 fetch 404 → dropdown 空
- **解法**: 启 Tauri 注入 `DESKPET_BACKEND_DIR` + `DESKPET_PYTHON` → 桌宠跑源码

---

## 正确真测方法（CDP 侦察坐标 + 真输入注入）

参考历史 `hybrid_winmcp_test.py` 的方法论：**用 CDP（桌宠 WebView2 远程调试端口 9222）
拿元素精确坐标 + 注入真实输入事件**，不靠截图目测物理坐标（dpr 换算地狱）。

```
桌宠 WebView2 CDP 9222 (dev 模式默认开)
  → Input.dispatchMouseEvent (真鼠标点 textarea)
  → Runtime.evaluate (真输入 / 触发 React onChange)
  → Input.dispatchKeyEvent (真按 ↓ Tab)
```

CDP = Chromium 官方自动化协议，注入到**真桌宠 WebView2**（不是普通 Chrome）。

---

## 真桌宠端到端 G2 完整 PASS（硬证据）

环境: 桌宠 PID 32936 + backend = **python.exe 源码 main.py**（`/api/commands/help` 返 14 commands）

```
[CDP] 连 http://localhost:5573/index.html#/code-panel
[1] navigate → #/slashtest (about:blank 中转强制 reload)
[2] textarea 渲染: True                          ← SlashTestHarness 在真桌宠 WebView2
[3] CDP 真点 textarea @ CSS(328,442)
    activeElement = TEXTAREA                       ← 桌宠 WebView2 真被点击 focus
[4] 真输入 '/' (CDP)
[5] SlashDropdown: rendered=True 命令数=14         ← dropdown 真出 14 命令
    前6个: ['help','goal','deep-research','doc-edit','excel-generate','file-organize']
[6] 真 filter '/pp': ['slash-item-ppt-generate']  ← 真过滤
[7] 真按 ↓ → 高亮: slash-item-ppt-generate         ← 真键盘事件
[8] 真按 Tab → textarea='/ppt-generate ' dropdown关闭=True  ← 真接受
[PASS] ✓ 真桌宠 WebView2 里 G2 /命令完整状态机真 PASS!
```

---

## 给后续 agent 的可复用资产

1. **dev 模式真测桌宠 backend** — 启 Tauri 必须注入:
   ```
   DESKPET_BACKEND_DIR=<worktree>/backend
   DESKPET_PYTHON=<.venv>/Scripts/python.exe
   ```
   否则桌宠用打包 exe（旧版本，没新功能）

2. **CDP 真测桌宠 WebView2** — `scripts/cdp_pet_g2_test.py`:
   - 连桌宠 9222 → Input.dispatchMouseEvent/KeyEvent 注入真实输入
   - 比 SendInput 物理坐标可靠（不用纠结 dpr 换算）

3. **SendInput 物理点击** — 能用（16 case 证明），但坐标换算:
   - logical = CSS物理 / OS_scale
   - 当前机器 OS_scale=150%, dpr=2.13 时换算复杂，建议用 CDP 拿精确坐标

4. **端口清理含 IPv6** — vite 监听 `[::1]:5573`，杀进程要用
   `Get-NetTCPConnection -LocalPort` 而非 `netstat findstr`（漏 IPv6）

---

## 这个 "bug" 修复的是什么 — 最终回答

**没有 deskpet 功能 bug 需要修。** 整个排查澄清了:

1. **windows-mcp/CDP 能真点桌宠 WebView2**（我之前错误地说不能）
2. 我之前失败是**测试方法错**（DPI 坐标换算 + 目测小目标）
3. 真桌宠 G2 端到端**完整 PASS**（14 命令 dropdown + 完整状态机）
4. 唯一"要改"的是 **dev 流程认知**: 真测桌宠要设 DESKPET_BACKEND_DIR 跑源码

**功能本身全对。我之前的 "修不了/Chromium 铁律" 结论全错，是用户坚持质疑救回来的。**
