# 裁定：整体 UI 改版「克制的高级感」（2026-09-09）

- **触发**：真人验收原话「现在的整体 UI 和 UI 交互很难看，你能帮我优化，我需要有高级感」。
- **worktree**：`worktrees/ui-premium`（分支 `worktree-ui-premium`，自 `main@6fe4a7ca`）。未合并 main、未 push、未启动原生应用。
- **改前证据**：`.local-test-evidence/2026-09-09/ui-redesign-shots/01..08.png`（八张视图截图）。

---

## 1. 设计原则（已由用户授权，本轮不再讨论）

| # | 原则 | 落地方式 |
|---|---|---|
| P1 | 深色为主，石墨/近黑**三级表面** | `bg`（窗体底）/ `surface`（侧栏·顶栏·抽屉）/ `card`（消息·列表项），外加 `raised` 作 hover 与内嵌高一级 |
| P2 | **唯一**低饱和强调色 | 选 **雾青（misty teal）**：深色 `#7FB2AA`，浅色 `#3F7F77`。放弃琥珀金，因为琥珀与 `warning` 同色相，会破坏「状态色只用于状态」 |
| P3 | 状态色只用于状态 | `success/warning/danger/info` 仅出现在健康度、告警、错误、连接态；不再当装饰色或选中色 |
| P4 | 去掉纯蓝用户气泡 | 用户气泡 = 强调色**极淡填充** `accentSoft` + 1px `accentBorder` 细描边 |
| P5 | 浅色是同一套令牌的**镜像**，不是补丁 | 令牌全部以 CSS 变量下发；`:root` 是浅色基线，系统深色与显式 `data-theme="dark"` 覆盖，显式 `data-theme="light"` 永远赢 |
| P6 | 版式：8pt 网格、明确字号阶、行高 1.5–1.6 | 见下表 |
| P7 | 阴影只在弹层用一层柔和阴影 | `shadow.sm/md` 直接置为 `none`；只保留 `shadow.overlay` |
| P8 | 交互三态 + 明确 cursor + 焦点环 | 统一 `.sh-interactive` 类；过渡 120–160ms；`prefers-reduced-motion` 全局关闭动效 |
| P9 | 不引新运行时依赖 | 零新增 npm 依赖；图标一律内联 SVG（`components/Icon.tsx`，本轮新增 `file`/`layers`） |

## 2. 令牌表（`tauri-app/src/theme/tokens.ts` 单一真相源）

### 2.1 颜色（`palette`，深/浅镜像；DOM 侧一律用 `var(--sh-*, 深色兜底)`）

| 令牌 | CSS 变量 | 深色 | 浅色 | 用途 |
|---|---|---|---|---|
| `bg` | `--sh-bg` | `#0E1013` | `#F4F2EF` | 窗体底 |
| `surface` | `--sh-surface` | `#15181C` | `#FAF9F7` | 侧栏 / 顶栏 / 抽屉 / 弹层 |
| `card` | `--sh-card` | `#1C2026` | `#FFFFFF` | 消息气泡 / 列表卡片 / 输入 |
| `raised` | `--sh-raised` | `#232830` | `#F0EEEA` | hover / 工具折叠条 |
| `hairline` | `--sh-hairline` | `rgba(255,255,255,.075)` | `rgba(18,20,24,.09)` | 1px 半透明分隔 |
| `hairlineStrong` | `--sh-hairline-strong` | `rgba(255,255,255,.14)` | `rgba(18,20,24,.17)` | 按钮/输入描边 |
| `text` | `--sh-text` | `#E8E5DF`（暖调灰白） | `#1A1C20` | 正文 |
| `text2` | `--sh-text-2` | `#A7A29A` | `#5B5E65` | 次级 / meta |
| `text3` | `--sh-text-3` | `#736F69` | `#8A8D94` | 弱化 / placeholder |
| `accent` | `--sh-accent` | `#7FB2AA` | `#3F7F77` | 唯一强调色 |
| `accentHover` / `accentPress` | `--sh-accent-hover` / `-press` | `#93C3BB` / `#6A9C95` | `#4B9089` / `#336862` | 同色相深浅，不换色相 |
| `accentSoft` | `--sh-accent-soft` | `rgba(127,178,170,.13)` | `rgba(63,127,119,.10)` | 用户气泡 / 侧栏选中面色 |
| `accentBorder` | `--sh-accent-border` | `rgba(127,178,170,.34)` | `rgba(63,127,119,.30)` | 细描边 / 聚焦描边 |
| `accentText` | `--sh-accent-text` | `#9FCDC5` | `#2F6862` | 强调色文字 |
| `onAccent` | `--sh-on-accent` | `#0E1013` | `#FFFFFF` | 实心强调按钮上的文字 |
| `success` / `warning` / `danger` / `info` | 同名 | `#6FBF8F` / `#D9A441` / `#D9645F` / `#6E9FD1` | `#2E8B57` / `#B07818` / `#BE4640` / `#3C6FA8` | **只用于状态** |
| `scrim` | `--sh-scrim` | `rgba(6,8,10,.56)` | `rgba(28,30,34,.34)` | 抽屉/弹层遮罩 |
| `shadowOverlay` | `--sh-shadow-overlay` | `0 16px 48px rgba(0,0,0,.44)` | `0 16px 40px rgba(20,22,26,.16)` | 唯一阴影 |
| `focusRing` / `selection` / `scrollThumb(Hover)` | 同名 | — | — | 焦点环 / 选区 / 滚动条 |

### 2.2 版式与形状

| 维度 | 值 |
|---|---|
| 字体栈 | `-apple-system, "SF Pro Text", "PingFang SC", system-ui, "Segoe UI", "Microsoft YaHei UI", …` |
| 字号阶 | **12 / 13 / 14 / 16 / 20 / 24**（`text.sm/base/md/lg/xl/xxl`） |
| 行高 | 1.5–1.6（`sm` 1.5，`base/md` 1.6，标题 1.3–1.4） |
| 字重 | 标题 600、正文 400、次级 500；**最重只到 600**（`weight.bold` 也是 600） |
| 字距 | 标题 `-0.01em` 细微收紧，中文正文不加字距 |
| 数字 | 全局 `font-variant-numeric: tabular-nums` |
| 间距 | 8pt 网格：2/4/8/12/16/24/32/48 |
| 圆角 | 按钮 **8**、卡片/面板 **12**、气泡 **14**、pill 999 |
| 控件高度 | **36**（侧栏项、按钮、输入行、tab）；密集面板内的次级按钮 28 |
| 阴影 | 只有弹层一层 `shadowOverlay`；其余 `none` |
| 动效 | `fast 120ms` / `base 160ms`；`prefers-reduced-motion` 时全部降到 0.001ms |

### 2.3 令牌下发机制（本轮的关键结构决定）

- `tokens.ts` 导出 `palette`（**字面值**，深浅两套）、`cssVarName`（令牌↔变量名映射）、`THEME_CSS`（生成的变量表）和 `v(key)`（`var(--sh-x, 深色兜底)`）。
- `theme/applyTheme.ts` 在 React 挂载前把 `THEME_CSS` 注入 `<style id="sh-theme">`，并按当前主题刷 html/body 底色（冷启动不闪色），同时监听系统深浅切换。
- **好处**：`color.*` / `dark.*` 的键名一个没动，31 个引用令牌的组件零改动即自动跟随主题；两套主题不可能漂移（同一份 `palette` 生成）。
- **唯一例外**：`primary/graphStyle.ts`。cytoscape 渲染在 `<canvas>` 上、无法解析 CSS 变量，因此那里按 `resolvedTheme()` 取 `palette` 的字面值——这是全项目唯一允许出现字面色值的渲染路径，已在文件头注明。

---

## 3. 每个视图改了什么

### 3.1 侧栏（`components/Sidebar.tsx`）
- emoji（💬🧩📄⚙️）→ 内联 SVG 图标（`message` / `layers` / `file` / `settings`）。
- 项高统一 36；选中态 = **左侧 2px 强调条**（`.sh-nav-item[aria-current]::before`）+ `accentSoft` 极淡面色 + 正文色，不再整块蓝框。
- hover/active/focus 从 JS `onMouseEnter` 改为 CSS 三态（`.sh-interactive`）。
- 宽度 240 → **232**；Logo 区去掉分隔线，靠留白分层；连接徽章降为 12/弱化色。

### 3.2 主对话（`views/PrimaryChatView.tsx`）
- 顶栏：标题 20/600 + 三个**幽灵按钮**（记忆 / 模型与设置 / 刷新状态）。
- 状态：抢眼横条 → **单行 meta**（12、次级色、tabular-nums），通知/错误跟在同一列里。
- 消息区：**最大宽 760 居中**；
  - 用户：右对齐气泡，`accentSoft` 填充 + `accentBorder` 细描边，圆角 14，**气泡内正文左对齐**（多行中文右对齐会出现锯齿左边缘，这正是"难看"的来源之一）；
  - 助手：左对齐 `card` 气泡 + 前置 **8px 强调色小头像点**；
  - 工具：`raised` 底、等宽字体、整行可点折叠（`.sh-tool-row`），保留 `▸ 工具 · 名称` 结构。
- 「查看更早消息」从按钮块 → **消息区顶部居中的细文字链接**。
- 空态：细线图标 + 一句提示（原来是一行裸文字）。
- 记忆面板：从"占 50% 高度的内嵌块" → **右侧抽屉宽 420 + 遮罩，160ms 淡入**（`sh-drawer-in`），点遮罩关闭。

### 3.3 输入区（`code-panel/InputBar.tsx`）
- 三个并排方块（附件框 / 输入框 / 发送块）→ **一张悬浮输入卡片**（`card` 底 + hairline 描边 + 12 圆角），附件图标与发送按钮**内嵌**其中，聚焦时卡片描边转强调色。
- 附件图标 📎 → 内联 SVG；附件 chip 改为描边 pill。
- 发送按钮：有草稿时实心强调色，空闲时幽灵；`■ 停止` 文案与语义不变。
- 状态药丸去 emoji（`✓ 空闲` → **状态色圆点 + 文字**），底部 meta 12/弱化色。
- 顺带修一个真 bug：首帧读到的 `scrollHeight` 会把**空**输入框撑成三行（截图里就是这样）——空草稿时清掉内联高度，交给 `minHeight: 36`。

### 3.4 记忆面板三页 + 任务页（`components/PrimaryMemoryPanel/Graph/AuditPanel/TaskPanel`）
- 抽屉宿主：面板顶栏（标题 + 刷新/关闭 幽灵按钮）→ **下划线 tab**（选中态 2px 强调下划线，替代原来的浅灰药丸）→ 可滚动内容区。
- 记忆列表：卡片化（`card` + hairline + 12 圆角），空态改为细线图标 + 一句提示；未确认的忘记操作用左侧 2px `warning` 细条。
- 关系图：画布底色从写死的 `#111827` 改为主题令牌；cytoscape 样式改为**按主题取字面值**，记忆类型靠**形状**区分（椭圆/圆角矩形/六边形/菱形）而不是四种彩色，节点统一强调色、争议态用 `warning` 描边、连线细化到 1px。
- 操作记录 / 任务：这两页有大量未内联样式的裸 `button`/`input`/`details`/`pre`（原先落到浏览器默认的浅灰按钮，就是截图里最扎眼的地方）。新增 **`.sh-prose`** 一次性给它们统一外观（28 高描边按钮、32 高输入、弱化 summary、卡片化 pre），内联样式仍然优先级更高。

### 3.5 技能中心（`components/CapabilityCenterPanel.tsx`）
- tab 从"蓝色下划线 + 青色文字"改为统一下划线 tab（强调色下划线 + 正文色）。
- 能力列表项选中态从蓝色填充 → `accentSoft` + `accentBorder`；健康度四色改为语义状态令牌。
- 通知条改左侧 2px 强调细条；空态走统一空态样式；输入 36 高、次级按钮 28 高。

### 3.6 产物库（`views/ArtifactsView.tsx`）
- 顶栏统一 `viewHeader` + 20/600 标题 + 幽灵「刷新」；列表最大宽 760 居中。
- 文件类型 emoji（🖼️📕📊📈📝📄）→ 内联 SVG。
- 空态从"虚线大框"改为细线图标 + 标题 + 一句提示。

### 3.7 设置（`components/SettingsPanel.tsx` 及其子卡片）
- 顶栏去掉蓝色圆角图标底板，改为线性图标 + 20/600 标题。
- 分区最大宽 760、间距上 8pt 网格；按钮统一 36 高；**每屏只有一个实心强调主按钮**（如「+ 添加」），其余描边。
- 危险区、错误/成功状态条改为「左侧 2px 状态色细条 + 透明底」，不再整块红/绿填充。
- `SettingsProviders` / `AddProviderModal` / `EmbedderStatusCard` / `ModelContextCard` 中的 `#2563eb` / `#b91c1c` / `#10b981` 等硬编码色值全部换成令牌。

### 3.8 全局（`index.css`）
- 全文件零硬编码色值，只写形状/状态/动效。
- 新增：`.sh-interactive`（三态 + cursor）、`.sh-nav-item`（左强调条）、`.sh-tool-row`、`.sh-prose`、`sh-drawer-in`、`prefers-reduced-motion` 全局关闭动效。
- 焦点环统一 `2px solid accent + 2px offset`，`:focus-visible` 触发（键盘可见、鼠标不打扰）。
- 滚动条、选区色改主题令牌。
- 顺带发现 `App.css` 从未被任何文件 import（死文件，含一条会污染全局 `button` 的规则）——本轮未删除，只记录。

---

## 4. 保留的可访问性名称清单（自动化脚本与验收依赖，逐条核对通过）

**按钮**：`主对话`、`技能中心`、`产物库`、`更多`、`设置`、`记忆`、`模型与设置`、`刷新状态`、`发送`、`查看更早消息`、`忘记这条记忆`、`关闭`、`刷新关系`、`刷新`。

**记忆面板 tab（必须保持 `aria-pressed`，macOS AX 映射为 `AXCheckBox`）**：`记忆列表`、`关系图`、`操作记录`、`任务`。

**关系图**：`AXImage` 可访问文本「记忆关系图：N条记忆，M条关系。可使用下方文字列表选择。」；文字列表 `aria-label` 「图中记忆」「图中关系」。

**状态文本**：「空闲 · 排队 N」「等待主对话就绪」。

**输入框**：placeholder「输入消息，Enter 发送…」；停止按钮「■ 停止」。

**遗忘提示**：「已忘记该记忆；保留原始历史档案。」

**`data-testid`**：全部未动，含 `primary-message-user/assistant/tool/artifact/reminder`、`view-chat/skills/artifacts/settings`、`workbench-shell/content/sidebar/banner`、`nav-chat/skills/artifacts/settings`、`chat-header/chat-title`、`sidebar-logo/more-toggle/more-group/conn-badge`、`memory-toggle/trace-toggle/feedback-toggle`、`artifact-item/artifact-name/artifacts-list/artifacts-empty/artifacts-refresh/artifact-action-*`、`composer-error/attachment-list/text-attachment-input/continuation-target`、`autostart-toggle` 等。

**交互约定**：Enter 发送 / Shift+Enter 换行未变；「查看更早消息」仍是 `<button>`（只是视觉降为文字链接）；工具折叠仍靠 `aria-expanded` 表达。

## 5. 改动的既有测试（三处，均为"锁旧视觉字面量"的断言，行为语义不变）

| 文件 | 原断言 | 新断言 | 理由 |
|---|---|---|---|
| `theme/darkTheme.test.ts` | 锁 `panelBg === "#14161f"`、`backdropStyle.background === "rgba(2,6,23,0.72)"` 等字面色值 | 改锁新契约：深浅两套令牌键集合一致、每个变量在变量表里出现 3 次（浅色 1 + 深色 2）、令牌以 `var()` 下发、次级控件与未选中 tab 不做填充、控件高度 36/圆角 8-12-14/动效 120-160、字号阶 12-13-14-16-20-24 | 令牌值本身就是本轮要改的东西；新断言比旧的更强（多锁了浅色镜像与版式阶） |
| `components/WorkbenchShell.test.tsx` | 锁"当前项文字色 === dark.accent"、JS hover 改色 | 锁 `aria-current` + `.sh-nav-item` 类 + `accentSoft` 面色 + 正文色 + 36 高度 + `.sh-interactive` | 选中态语言从"整块强调色"改成"左侧强调条 + 淡面色"，hover 从 JS 改 CSS |
| `code-panel/InputBar.chat.test.tsx` | `getByText("✓ 空闲")` / `queryByText("🔧 工具执行中")` | `getByText("空闲")` / `queryByText("工具执行中")` | 状态药丸去 emoji 改「状态色圆点 + 文字」；断言锁的仍是同一状态语义 |

新增测试：`darkTheme.test.ts` 从 3 例扩到 9 例。

## 6. 验证结果

| 项 | 基线（main@6fe4a7ca） | 改后 |
|---|---|---|
| `npx tsc -b --noEmit` | 通过（无输出） | **通过** |
| `npx vitest run` | 104 文件 / 801 通过 + 1 跳过 | **104 文件 / 807 通过 + 1 跳过** |
| `npx vite build` | — | **通过**（`✓ built`） |

无新增红。基线在本 worktree 内先跑过一遍确认为全绿，改后逐文件回归 + 全量回归各一次。

浏览器侧（vite dev，非原生应用）人工目视核对：主对话空态与消息区、记忆抽屉、技能中心、产物库、设置在**深浅两套主题**下均渲染正确，`data-theme="light"` 切换即时生效、布局不变、对比度可读。

## 7. 残余风险

1. **未在原生应用里看过**。本轮按约束没有启动 Tauri，目视是在浏览器 vite dev（`invoke` 不可用，产物库/设置的数据区显示读取失败横幅，属预期）。真机需重新打 bundle 才生效。
2. **主对话的真实消息流未接后端验证**。消息气泡的目视是按最终样式在浏览器里复刻 DOM 做的；结构与样式来源于同一份组件代码与令牌，但"真消息 + Markdown 渲染 + 长内容折叠"的组合仍待真人验收。
3. **主题跟随系统但没有应用内切换入口**。用户目前只能靠系统深浅偏好切换；若要在设置页加开关，需要一个持久化项（本轮未做，也未被要求）。
4. **关系图切换主题不即时**。cytoscape 样式在建图时按当时主题取字面值，系统深浅切换后需要刷新关系图才会重绘。
5. **`.sh-prose` 是"面板内裸元素"的兜底**，作用域限于挂了该类的容器（当前是记忆抽屉）。其它老面板（ContextTracePanel、workflows/* 等）本轮未纳入，仍是旧观感——它们不在本次八张截图的验收范围内。
6. **`MessageBubble.tsx` / `MemoryPanel.tsx` / `workflow*` 等历史组件**仍有硬编码色值（约 200 处），不在本轮八视图路径上，未改。
7. **`App.css` 是死文件**（无人 import，且含一条会把所有 `button` 刷成 `#0f0f0f` 的全局规则）。本轮只记录，未删除，避免超出改版范围。
8. **`shadow.sm/md` 被置为 `none`** 而不是删除键，历史调用点因此静默失去阴影——这是有意的（P7），但如果某处依赖阴影表达层级，会显得扁。目视未发现。
