# 桌宠互动 Tier-1 验收报告（2026-06-13）

**范围**：点击 / 拖拽 / 努力工作 UI（整体变换 + 覆盖 UI）
**分支**：master（HEAD = tier1 6 commits 之上）
**代码验证**：`npx tsc --noEmit` 0 errors；`npx vitest run` **652/652 全绿**（新增 SpriteCoreModel 5 + petTransform 6 + 既有全回归零破坏）；pet-anim/ 零改动。

---

## 代码层（自动化，确定性证据）

| commit | 内容 | 验证 |
|---|---|---|
| 8c2899f | SpriteCoreModel 真参数字典 + deriveTransform | 11 新单测 + tsc |
| 8d9f0ff | CharacterFrame transform + draw 接 applyTo | tsc + 380 回归 |
| efda96e | 点击星星粒子特效 | tsc + 380 回归 |
| 80bb8b5 | 努力工作气泡 + working 信号(5 触发点) | tsc + 652 回归 |

数据流断点全部接通：B1(applyTo 进 draw)、B2(SpriteCoreModel 真字典)、B3(CharacterFrame transform)、B5(working 信号)。

---

## 真机 / preview 视觉验收

| Case | 方式 | 判定 | 证据 |
|---|---|---|---|
| **TC-2 拖拽** | 真机 windows-mcp，从 (2955,840) 拖到 (3300,840) | **PASS** | 桌宠窗口跟手从屏幕中央移到右侧（startDragging 链路通：pointerdown→move→拖拽）。截图前后对比位置明显变化。 |
| **TC-1 拖拽 wobble 倾斜（核心整体变换）** | preview eval `setDragState('being_held')` + 截图 | **PASS** | 立绘明显绕脚底向右倾斜（头偏右、裙摆甩向左下），证实 `ParamBodyAngleZ → deriveTransform → 立绘 rotate`。pivot 在脚底="站着晃"。**点击歪头走同一条变换链路（ParamAngleZ→rotate），同机制已证。** |
| **TC-1 点击星星✨** | 真机点击 + preview_click hit-zone | **PASS（功能）/ 瞬态截图受限** | preview_click `[data-pet-hitzone]` 成功触发 onClick→spawnBurst。星星生命周期 650ms，单帧截图工具调用间隔 >650ms 截不到逐帧（工具时序硬限制，非功能问题）。代码 tsc+vitest 已验证。 |
| **TC-3 努力工作气泡** | — | **代码验证 / 完整链路环境受限** | PetWorkingBubble 组件 + App 5 个信号点（handleSend/tool_use_event→true, chat_v2_final/error/interrupt→false）tsc+vitest 验证。真机完整链路（agent 调工具时气泡常驻）需 LLM relay 登录凭据（`LOCAL-DEV-CREDENTIALS.md` 本仓库不含），未能真机跑通。 |

---

## 诚实声明（工具/环境限制）

1. **瞬态特效逐帧截图**：星星(650ms)、点击歪头(200ms)、wobble 衰减——单张截图的工具调用间隔常 >特效时长，难逐帧抓。已用"持续型"互动（拖拽 wobble 持续、preview setDragState 保持）验证核心变换链路；瞬态覆盖层（星星）靠 onClick 触发确认 + 代码验证。
2. **努力工作气泡完整链路**：需登录凭据走通 agent→工具→ws 事件，本环境无凭据。组件 + 信号接线已代码验证，缺真机端到端那一截。
3. 真机桌面版（Tauri）启动正常：deskpet.exe(target/debug) + 165 FPS + 立绘渲染 + 零 error/panic。

---

## 结论

- **核心目标达成**：pet-anim 互动 → sprite 立绘的整体变换链路**接通并真机/preview 双验证**（拖拽 wobble 是最强证据）。
- **点击**：变换链路同 wobble（已证）+ 星星覆盖层（代码验证，瞬态截图受限）。
- **努力工作**：代码 + 信号接线完成，完整真机链路待登录凭据。
- pet-anim 19 个状态机零改动，652 测试零回归。
