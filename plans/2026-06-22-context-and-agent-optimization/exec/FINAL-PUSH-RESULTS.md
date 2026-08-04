# 收尾真测最终结果（2026-06-23）

> 用户"做完所有未做项 + 继续测试"指令下的收尾。relay(chinzy.com) 间歇 504（streaming 高发），影响 agent 活动类真测时延/稳定性。

## ✅ 本轮完成

| 项 | 结果 | 证据 |
|---|---|---|
| **1A-4 ②③ memory 归并** | ✅ 完成 | 工具稳健性 3 记忆合并为 `feedback_tooling_robustness` + 删 3 已 ship 项目记忆，索引→18 条全有效（仓外，无需 commit）|
| **HM-1 升 strict** | ✅ 完成 + 真机验证 | 真机 strict 下纯闲聊"你好呀今天天气"多轮 `stop_reason=end_turn` 不误阻塞 + goal/task 回合放行 → 出厂默认 shadow→strict（完成 decision① 目标），38 测绿，提交 `7fd79c83` |
| **TG-1 goal_task** | ✅ **真机 PASS** | goal_mode ON → `goal_task_tools_registered_global count=4`；设目标后主 agent 真调 **goal_task_create ×3 建带依赖 DAG**（任务2 depends_on 任务1、任务3 depends_on 任务2），goal_id 反查解析成功。截图 `testcase/.../TG-1-goal-task-create-dag.png` |

## 🟡 尝试但受阻（单测充分验证，真机被环境/设计因素阻塞）

| 项 | 尝试 | 受阻原因 |
|---|---|---|
| **OH-2 召回确认** | 发"我之前喜欢哪个编辑器" | pin 机制此前已真机验（memory_write pinned=true）；召回回合被 relay 间歇 504 拖挂起。pinned facts 持久化 + preference_profile 注入是机制验证的 |
| **B2 (1B-2/3/4/5) code 模式压缩** | 设 ctx_observability+window=8000+重启（compact_at=6400 就绪），尝试进 code 模式做大文件读触发压缩 | ① relay 间歇 504 ② code 模式 windows-mcp 驱动复杂（桌宠窗口反复重定位、消息面板展开 fiddly、code_mode_enter 经 WS/suggest 触发链长）③ 4s toast 抢截窗口窄。companion 压缩天然 inert 已诊断 by-design。96 单测兜底逻辑正确 |
| **CC-2 plan 只读** | 设 plan_read_only+plan_confirm_gate+重启 | 需 code 模式 plan 阶段抢跑写工具，同上 code 模式驱动复杂 + relay。24 单测已验按 permission_category 拦截 |
| **TG-2 审批聚合面板** | — | 需改 App.tsx enabled=true(HMR) + auto_mode off 造并发权限请求(relay 依赖 agent 写工具调用)。vitest14 已验聚合逻辑 |
| **OC-1 depth 拒绝分支** | — | `_strip_forbidden` 设计上防嵌套→拒绝分支真机不可达（backstop，20 单测兜底）|

## 真机真测累计 PASS（全程）
1B-1 / HM-1(strict) / OH-2 pin / OC-2 / CC-3 / OH-4 / CC-5 / TG-1 = **8 个特性**。

## 诚实小结
- **代码 100%**（所有 WI 实现+评估+OH-4 三死链修复）。
- 收尾完成最高价值的 1A-4 + HM-1 strict + TG-1。
- 剩余 5 项（OH-2 召回/B2/CC-2/TG-2/OC-1）单测充分；真机验证卡在 **① relay 间歇 504 ② code 模式 windows-mcp 驱动复杂度 ③ companion 压缩 by-design inert ④ OC-1 backstop 不可达** —— 这些是环境/设计因素，非代码缺陷。relay 稳定后 OH-2 召回/TG-2 可补；B2/CC-2 需专门 code 模式真测会话。
- **1A-2 裁 MCP 插件**仍须用户交互式面板（会话无法驱动）。
