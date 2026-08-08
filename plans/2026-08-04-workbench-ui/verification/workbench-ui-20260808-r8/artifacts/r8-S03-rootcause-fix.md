# WBUI-DEF-S03-01 根因测量与修复记录（2026-08-08）

## 测量方法（浏览器道诊断，避开 devtools 停靠压缩 inner_size 的坑）

- backend 单独跑（`DESKPET_DEV_MODE=1`，端口 8100，无 Tauri，无端口双占）+ vite dev
- Browser 视口设 800×560（= min 尺寸），注入 `__TAURI_INTERNALS__` stub 过 boot 门
- 缺陷**完整复现**，与 r8 真机记录逐字一致：
  - 右列「健康」橙字硬裁片段 Instructi/or curren/unavailab/for direc/executio
    ⇔ 实际文案 "Instructional or currently unavailable for direct execution."
  - 能力列表下方 + 详情面板底部各一条横向滚动条
- JS 全树扫描 `scrollWidth > clientWidth`（排除 text-overflow:ellipsis 刻意裁剪）

## 量出的根因（两条独立溢出链，同一缺陷类：隐式 grid 轨道 min-content 下限）

1. **列表道**：`capability-list` 容器 `display:grid` 无显式轨道 → 隐式轨道
   base = 行 min-content。行内版本串 `0.1.1 · builtin:first-party:skill-…`
   nowrap 不可断（min-content 276px）→ 行 min-content 315px → 轨道 316.8px
   > 容器 213px → 列表下横向滚动条。
2. **详情道**：`capability-detail` article（隐式轨道）+ `detailGridStyle`
   `72px 1fr`（1fr 隐式 min=auto=值列最长单词）→ min-content 192px > 可用
   152px → 详情底横向滚动条；健康橙字在越界轨道内换行 → 每行同一 x 硬裁。

## 交接单候选核销

- ❌ `:589 min(920px,96vw)`（r8 已自纠，overlay 变体专用，无关）
- ✅ `detailGridStyle "72px 1fr"` —— 属实（详情道主因之一）
- ✅ `detailPaneStyle 无 overflowX` —— 表象相关（overflow-y:auto 使 x 计算为
  auto，出滚动条），但根因是轨道超宽，非缺 overflowX
- ❌ `bodyGridStyle minmax(250px,…)` —— 非溢出根因（左列已获 250px，行自身
  min-content 315px 仍溢）；但它把详情值列挤到 ~47px 致逐字断行，作为
  次生问题一并调为 200px

## 修复（tauri-app/src/components/CapabilityCenterPanel.tsx）

- capability-list 容器：`gridTemplateColumns: "minmax(0, 1fr)"`
- capability-detail article：`gridTemplateColumns: "minmax(0, 1fr)"`
- detailGridStyle：`"72px 1fr"` → `"72px minmax(0, 1fr)"` + `overflowWrap:"anywhere"`
- operationListStyle：补 `minmax(0, 1fr)`（同类防御，操作卡片道）
- bodyGridStyle：左列 `minmax(250px,…)` → `minmax(200px,…)`（值列可读性）

## 修复后复测（浏览器道）

- 800×560 能力 tab：offenders=0；健康整句可读、无横向滚动条（截图核对）
- 800×560 操作 tab：offenders=0
- 1260×840 能力/操作 tab：offenders=0（无回归）
- vitest 全套 76 files / 673 tests 全绿；tsc --noEmit 干净

## 状态

- 本记录是**根因诊断与修复证据**，不是 S03 场景判定。S03 真机重跑
  （required lane = manual-mcp）待 relay 身份恢复后按纪律执行，本轮
  不预记任何 PASS/FAIL。
