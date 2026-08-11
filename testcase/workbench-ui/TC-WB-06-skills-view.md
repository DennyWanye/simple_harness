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
| 5 | 【判定项 ④⑤⑥】**2026-08-10 修正（behavior_change WBUI-BC-13，用户批准）**：切到 operations tab。原文假设三个入口在默认状态下直接可见——该假设与产品的授权设计冲突，实测机制如下（`ui_projection.py:234-243` + `ui_service.py:140-227`）：<br>· **uninstall** 要求 `requested_scope != "builtin"`——first-party 包一律 builtin，**刻意不给卸载入口**（防用户删掉自带能力）<br>· **rollback** 要求 `kind ∈ {update, repair, rollback}`——install 类操作没有可回滚的前版本<br>· **cancel** 要求 `status == "running"` 且 `ui_service._tasks` 中有在飞任务<br>三者皆为**合理产品行为，非缺陷**。改判为两段：<br>**(a) 可见性对账（本轮 release gate）**：核对当前 operations 列表每条操作的 kind/scope/status，并逐条说明其为何不满足上述条件（真机截图 + 后端投影字段对账）；<br>**(b) 条件渲染路径（非本轮阻断项）**：`CapabilityOperationCard` 的自动化测试证明 `available_actions` 非空时渲染对应按钮；待存在满足条件的真实操作后再补真机证据。 | (a) 对账一致且条件渲染自动化全绿即过；(b) 真机取证单独跟踪，不冒充已完成。cancel 的真实在飞操作依赖范围外缺陷 `WBUI-DEF-BUILD-01` 修复。 |
| 6 | 【判定项：旧 Skill Store 入口】点击「旧 Skill Store」入口进入再返回。 | 入口**可达**；SkillStore 在**技能视图内切换**呈现（非弹窗），可返回能力中心；不弹出独立浮层遮罩。 |

判定：步骤 1–4、6 与步骤 5(a) 必须全部满足，且 `CapabilityOperationCard` 条件渲染自动化全绿才 PASS；步骤 5(b) 的真实在飞操作截图作为 `WBUI-DEF-BUILD-01` 修复后的后续证据，不阻断本轮经用户批准的 WB-6 口径。
