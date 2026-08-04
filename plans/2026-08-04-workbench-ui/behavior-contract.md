# 行为契约：Workbench UI 改版

> 用途：冻结进 gate 账本（init manifest 的 behavior_contract）。acceptance 的行为断言
> 均回溯此表。用户确认事件与原始需求消息一并入账。

## 术语表与实体关系

- **主窗（main）**：唯一应用窗口。改版前 = 透明桌宠竖条；改版后 = 普通工作台窗口。
- **消息面板窗（message-panel）**：改版前的第二个隐藏窗口，承载聊天/Harness 观察；改版后不存在，其内容区成为主窗 ChatView。
- **会话（session）**：后端 SessionDB 的对话单元；一个会话多条消息、多个 Run。会话 ≠ 窗口 ≠ 连接。
- **控制连接**：主窗持有两条 WS——身份通道（identity_bind，relay 登录/设置面板用）与聊天通道（companion_action，sid=message-panel-main 历史命名保留）。改版不改连接拓扑，只改聊天通道的窗口身份声明（message-panel→main）。
- **视图（view）**：工作台内的四个页面 chat/skills/artifacts/settings，App 层 state 切换，非窗口、非路由。

## before / after 行为表

| # | 现有行为（before） | 目标行为（after） | 类别 |
|---|---|---|---|
| B1 | 透明无边框桌宠窗 500×640，跳过任务栏，角色立绘+动画渲染 | 普通窗口 1000×700（min 800×560），系统标题栏，进 Dock/任务栏，无任何角色渲染 | 改变 |
| B2 | 点「▶消息」弹出独立消息面板窗（聊天+Harness 观察） | 聊天+Harness 观察即主窗 ChatView（默认视图），无第二窗口 | 改变 |
| B3 | 会话列表在消息面板 header 下拉里（切换/新建/重命名/删除） | 同功能移入侧栏「会话」区，协议不变（sessions_list/chat_v2+new_session/session_rename/session_delete） | 保留（换位置） |
| B4 | 发消息→后端往返→markdown 渲染；模型选择/ContextRing/Harness 巡检在面板 header | 完全保留，移入 ChatView 头部条 | 保留 |
| B5 | companion 特权动作（卡片确认等）仅 message-panel 窗可发起 | 仅 main 窗可发起（五处硬编码同步迁移：前端常量/Rust 白名单/Python 白名单/ingress 标签/SQL CHECK+迁移 007） | 改变 |
| B6 | 设置/能力中心/SkillStore/记忆/Trace/反馈 = 浮层弹窗，入口在右上 Toolbar | 设置/技能 = 侧栏页面；记忆/Trace/反馈入口移侧栏「更多」组；浮层三项（设置/能力中心/SkillStore）页面化，其余全局弹窗不动 | 改变（能力不减） |
| B7 | 产物卡片只在消息流里出现 | 新增产物库视图（按时间倒序 + 打开/在文件夹显示）；消息流内卡片照旧 | 新增 |
| B8 | 窗口位置/尺寸记忆：仅记程序化设置值，用户拖拽尺寸被 pin 丢弃 | 用户拖拽缩放/移动均持久化恢复；旧记录低于新 min 时回退默认（一次性迁移代价） | 改变 |
| B9 | 语音按钮禁用（等 Realtime） | 保留禁用占位于输入栏旁，tooltip 说明 | 保留 |
| B10 | 托盘菜单：显示桌宠/隐藏桌宠/退出 DeskPet | 显示主窗/隐藏主窗/退出 Simple Harness（行为同，文案改） | 保留（改文案） |
| B11 | 桌宠动画/形象选择/点击穿透/FPS 徽章/DialogBar 气泡 | 删除（acceptance only-add 显式例外清单） | 删除 |
| B12 | 自启开关在 Toolbar | 移入设置页 | 保留（换位置） |
| B13 | 关闭主窗（现状：Destroyed→exit(0)，连带回收后端，防 8100 孤儿） | 标题栏红钮=完全退出（窗口+托盘+进程+后端全清），与托盘「退出」等价；隐藏仅经托盘「隐藏主窗」 | 保留（QA 第 1 轮 M1 补行：沿用现有生命周期语义，非新决策） |

## 明确不变的行为

后端全部 API/WS 协议、SessionDB schema（companion.db 迁移 007 除外）、密钥存储、
relay 登录流、权限/澄清/外部等待弹窗、budget/context toast、StartupOverlay、
onboarding 向导（文案清理另立需求）、`#/slashtest` 测试路由。
