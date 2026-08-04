# TC-WB-06 — SkillsView 技能中心页面化

> 对应 AC：WB-6 ｜ 行为契约：B6
> manual_required: true

> 判定全集（裁决第 1 条，`plans/2026-08-04-workbench-ui/oracle-clarifications.md`）：以下 6 项即"详情/操作不回归"的完整判定清单。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 点击侧栏「🧩技能」。 | 技能中心以**页面**呈现：填满内容区，无背景遮罩（backdrop）、无浮层弹窗定位、无"关闭 ×"式弹窗交互（页面切换即离开）。 |
| 2 | 【判定项 ①】寻找并点击 capabilities / operations 两个 tab。 | 两个 tab 存在且可互相切换，切换后内容区随之变化。 |
| 3 | 【判定项 ②】在 capabilities tab 观察能力列表。 | 能力列表正常渲染（有既有能力数据时非空；条目含名称等基本信息，无渲染报错/空白区块）。 |
| 4 | 【判定项 ③】点击任一能力条目打开详情。 | 详情正常打开并展示内容；关闭/返回后回到列表。 |
| 5 | 【判定项 ④⑤⑥】切到 operations tab，逐一核对 cancel / rollback / uninstall 三个动作**入口存在**（不要求真机执行 uninstall）。 | 三个动作入口均可见可点（点到确认弹层即可停手，不执行破坏性动作）。 |
| 6 | 【判定项：旧 Skill Store 入口】点击「旧 Skill Store」入口进入再返回。 | 入口**可达**；SkillStore 在**技能视图内切换**呈现（非弹窗），可返回能力中心；不弹出独立浮层遮罩。 |

判定：6 项判定清单（两 tab 切换、列表渲染、详情打开、cancel/rollback/uninstall 三入口存在、旧 Skill Store 入口可达）**全部满足**且步骤 1 页面化成立才 PASS；任一项缺失即 FAIL（功能回归）。
