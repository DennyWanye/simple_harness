# Slice 1 手工测试用例 (windows-mcp 真测)

**纪律**: 严格遵守 `~/.claude/CLAUDE.md` HARD CONSTRAINT 和项目 CLAUDE.md "手工测试纪律":
- ❌ 不允许 ws/http 直连 backend 替代 UI 测试
- ❌ 不允许 pytest/vitest 替代 windows-mcp 真测
- ❌ 不允许 import 检查替代 UI 验证
- ✅ 每 case: 真坐标点击/输入 + 截图 + log 证据 + PASS/FAIL 判定
- ✅ 每动作前 declare: `坐标=(x,y) | 动作=... | 期望=...`
- ✅ 截图存盘 `plans/2026-05-28-live2d-rewrite/manual-results-2026-05-28/screenshots/`

**Worktree 端口**: backend 8400 / vite 5473 (不撞 master 8100, memory 8200, tool-last-mile 8300)

---

## MR-S1-01: 默认 live2d 后端零回归

**前置**:
```powershell
cd G:\projects\deskpet\.claude\worktrees\live2d-rewrite
Remove-Item env:VITE_PET_ENGINE -ErrorAction SilentlyContinue
powershell -File scripts/dev-worktree.ps1 -BackendPort 8400 -VitePort 5473
```

**步骤**:
1. 等 Tauri 窗口出现 (~10s)
2. windows-mcp Screenshot → 存 `screenshots/MR-S1-01-01-launched.png`
3. 观察 5s 是否 Hiyori 渲染 + 自然 blink + idle motion 播放
4. windows-mcp Screenshot → 存 `screenshots/MR-S1-01-02-after-5s.png`
5. grep backend log `tail -200 .dev-userdata/logs/*.log`

**期望**:
- 窗口出现 Hiyori 角色 (非紫猫 fallback)
- console log 含 `[Live2D] model loaded:` (来自 Live2DCanvas:548)
- console log **不含** `[NullPetEngine]` warning
- 2 张截图对比可见 blink / idle motion 帧差异 (角色姿态不同)

**判定**: PASS / FAIL / RETRY-N

---

## MR-S1-02: VITE_PET_ENGINE=null 占位后端

**前置**:
```powershell
# 杀掉上一轮 dev (HARD CONSTRAINT: TaskStop 不清 orphan, 必 taskkill)
taskkill /F /IM deskpet.exe 2>$null
Get-Process node | Where-Object { $_.CommandLine -like "*vite*" } | Stop-Process -Force

$env:VITE_PET_ENGINE = "null"
powershell -File scripts/dev-worktree.ps1 -BackendPort 8400 -VitePort 5473
```

**步骤**:
1. 等 Tauri 窗口出现
2. windows-mcp Screenshot → `screenshots/MR-S1-02-01-null-backend.png`
3. 等 3s 观察是否 Canvas2D 紫猫 fallback 显示 (来自 Live2DCanvas.startCanvas2D 第 668 行)
4. windows-mcp Screenshot → `screenshots/MR-S1-02-02-canvas2d-cat.png`
5. 触发 pet-anim motion: 找 Idle motion 是否被 NullEngine 拦截 → console grep `[NullPetEngine] playMotion`

**期望**:
- 窗口出现紫色卡通猫 (Canvas2D fallback)
- console log 含 `[NullPetEngine] playMotion(Idle) — null backend, no animation will play` (单次, warnOnce)
- console log **不含** `[Live2D] model loaded:`
- 紫猫的 blink + breath 动画照常 (因为 Canvas2D fallback 是 self-driven, 与 pet-anim overlay 解耦)

**判定**: PASS / FAIL / RETRY-N

---

## MR-S1-03: pet-anim 零回归 (overlay 接受 null backend)

**前置**: 续 MR-S1-02 (VITE_PET_ENGINE=null, dev 已跑起)

**步骤**:
1. 在 Tauri DevTools (Ctrl+Shift+I) 打开
2. Console 跑:
   ```js
   const d = window.__deskpet_anim_debug_v2
   console.log('held_state:', d?.held_state, 'mouth_fade:', d?.mouth_fade_mode, 'low_energy:', d?.low_energy)
   ```
3. windows-mcp Screenshot DevTools → `screenshots/MR-S1-03-01-debug-v2.png`
4. Console 跑 `window.__deskpet_anim_overlay.setEmotion('happy', performance.now())`
5. 再读一次 `__deskpet_anim_debug_v2.current_emotion`
6. Screenshot → `screenshots/MR-S1-03-02-emotion-set.png`

**期望**:
- `__deskpet_anim_debug_v2` 返回完整对象 (15 个字段), 证明 AnimationOverlay 在 null 后端下完整运转
- setEmotion 调用后 `current_emotion === 'happy'` (overlay 写参数到 NullCoreModel, 写入静默丢弃但不抛)
- console **无** 异常或 stack trace

**判定**: PASS / FAIL / RETRY-N

---

## MR-S1-04: 后端切回 live2d 仍工作

**前置**:
```powershell
taskkill /F /IM deskpet.exe 2>$null
Get-Process node | Where-Object { $_.CommandLine -like "*vite*" } | Stop-Process -Force

Remove-Item env:VITE_PET_ENGINE
powershell -File scripts/dev-worktree.ps1 -BackendPort 8400 -VitePort 5473
```

**步骤**:
1. 等 Tauri 窗口出现
2. windows-mcp Screenshot → `screenshots/MR-S1-04-01-back-to-live2d.png`
3. 对比 MR-S1-01 截图, 应该是同样的 Hiyori (像素级近似, blink 帧除外)

**期望**:
- 角色 = Hiyori, 非紫猫
- console `[Live2D] model loaded` 回来
- 证明: env flag 的切换无副作用, 默认行为零漂移

**判定**: PASS / FAIL / RETRY-N

---

## 报告格式 (每 case 必须填)

```
case:     MR-S1-XX
后端:     live2d / null
坐标:     N/A (本批是 launch + DevTools 验证, 无点击)
动作:     启动 dev → 截图 → DevTools 跑 JS → 截图
截图:     screenshots/MR-S1-XX-*.png (列全部)
console log 证据:
  - "[Live2D] model loaded: ..." (或 NullPetEngine warning)
  - "[pet-anim] ..." (零异常)
判定:     PASS / FAIL / RETRY-N
备注:     <异常细节, 若 FAIL>
```

## 已知技术陷阱回顾 (项目 CLAUDE.md)

- **Tauri dev 启动后留 orphan**: 切换 env 重启前必 `taskkill /F /IM deskpet.exe` + 杀 Vite node
- **windows-mcp Click(loc=[x,y]) schema bug**: 用 PowerShell `[Win]::SetCursorPos + SendInput`
- **WebView2 不响应 mouse_event**: 用 SendInput API (本批 case 不需要点击桌宠, 暂不踩)
- **DPI scaling**: 主屏可能 200% 缩放, 截图前先 `Get-WmiObject Win32_VideoController | select VideoModeDescription`

## 失败 retry 策略

- Tauri 窗口未出现 (15s 超时) → kill all + 重试 1 次
- VITE_PET_ENGINE 未生效 → 检查是否 `.env.example` 写错 / 是否在 tauri-app 子目录跑
- DevTools 不响应 → Tauri 在 release build 默认禁用, 确认是 dev build
- 任何 case 失败 retry ≥ 3 次仍不过 → 标 FAIL 并停下来等用户裁决, **绝不绕过**
