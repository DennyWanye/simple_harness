# 多屏跨 DPI 拖动 + Live2D 角色渲染修复 — 手测报告

**日期**: 2026-06-03/04
**环境**: Samsung Odyssey(主屏, 物理 3840×2160, webview dpr 2.13) + Xiaomi(副屏, 物理 1920×1080 @ x=3840, webview dpr 1.42)
**测法**: PowerShell DPI-aware `SetProcessDpiAwarenessContext(-4)` + Win32 `SendInput` 真模拟鼠标拖动(WebView2 需 SendInput,非 mouse_event)+ 真物理坐标截图。

---

## 用户报告的 3 个问题

1. 从三星拖不回小米(剧烈抖动 + 弹回三星)
2. 拖动几次后角色只显示一半
3. 从三星拖到小米后角色显示不全(右半被裁)

## 7 处根因 + 修复

### ① 拖不回小米(抖动/弹回)— `window_geometry.rs` + `App.tsx`
- **Rust on_resize clamp**:跨 DPI → WM_DPICHANGED → Resized → clamp → set_position → 又跨界 → 振荡。移出 resize 路径(clamp 只在 boot apply_saved_geometry 跑一次)。
- **前端边缘吸附在拖动中触发**(App.tsx `onMoved`):持续 setPosition 吸边阻止跨界。改 250ms 防抖(松手后才吸)。
- **吸附坐标系错**:`pickEdge` 用全局物理坐标对比显示器局部尺寸 → 非主屏永远误判右边缘 → `snapTarget` 把桌宠 setPosition 回主屏坐标区。改显示器局部坐标转换(减 monitor.position,吸附结果加回)。

### ② 拖几次只显示一半 — `window_geometry.rs`
- `on_resize` + `on_move` 都用 `physical/scale_factor` 反算逻辑尺寸,跨 DPI 舍入误差累积漂移(实测 json 375×610 → 360×657)→ 窗口变窄 → 角色裁。`pin_size`:缓存 boot 权威逻辑尺寸,拖动只改位置绝不改尺寸。

### ③ 拖到小米角色显示不全 — `Live2DCanvas.tsx`(4 处)
- **dpr 异常 + 列宽溢出**:webview dpr(2.13/1.42)比显示器 scale 高 ~1.42×(疑似 Windows 文本缩放 142%)→ 视口 innerWidth 仅 ~253 CSS < 固定角色列宽 282 → `<img>` 比视口宽 → 溢出裁切。把列宽 cap 在视口内:`Math.min(petWidth, innerWidth)`。
- **DPR 变化画布不重渲**:resize effect 只 dep `[size.w, size.h]`,跨 DPI 时逻辑尺寸不变 → effect 不跑 → 画布卡旧 DPR。把 `devicePixelRatio` 纳入 size 状态 + effect dep `size.dpr` + matchMedia(resolution) 监听。
- **模型异步加载完 effect 不重跑**:Live2D 模型 load 异步,加载完时 size 没变 → resize effect 不跑 → 画布停在 init 尺寸 → 直接 boot 某屏时角色错位。加 `modelReady` 状态(模型 ready 置位)纳入 effect dep。
- **scale 用已缩放宽 → 模型爆炸**:effect 用 `model.width`(pixi 返回 scale×localBounds = 已缩放宽,渲染后才更新)算 scale → scale≈1 → 模型放大到原始 2976px(只剩胸口)。加载时存**基础尺寸** `modelBaseRef`,scale + 居中一律用它。
- **两个重复的 resize effect**:文件里有两个职责相同的 resize useEffect,第二个(旧版,用 model.width + window.devicePixelRatio + 无 modelReady)拖动时后跑、覆盖第一个修好的结果 → 巨大。删除重复,单一权威路径。

---

## 真机验证矩阵(白板背景排除透明窗口背景干扰)

| 场景 | 结果 | 证据 |
|---|---|---|
| 三星 boot | 角色完整居中 ✓ | `pet-samsung-boot-capfix.png` |
| 小米 boot | 角色完整居中 ✓ | `pet-xiaomi-FINAL.png` |
| **拖 Samsung→Xiaomi 后** | 角色从头到脚完整居中 ✓ | `pet-xiaomi-dedup-FINAL.png` |
| 10 次跨屏拖动 | 逻辑尺寸全程 pin 360×600 零漂移 ✓ | `multi_drag_test.ps1` 输出 |
| 拖动期 clamp_position | 0 次(旧 bug 反复触发)✓ | tauri dev 日志 |

**修复前对照**: `pet-xiaomi-whitebg.png` — 角色右半被窗口右边缘裁掉。

## 复现/回归脚本
- `multi_drag_test.ps1 [N]` — SendInput 真拖动 N 次跨屏,每次报告逻辑尺寸是否漂移 + 是否真 MOVED。

## 诊断日志(保留)
- `[Pet] viewport: <w> x <h> dpr: <dpr> ... size.w: <capped>` — 每次 viewport/dpr 变化。
- `[Live2D] renderer resize -> <W>x<H> (size .. dpr ..)` — 画布每次重渲。
对排查这类多屏 DPI bug 极有用,落进 tauri dev 日志(structlog → stderr)。
