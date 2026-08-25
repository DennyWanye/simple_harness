---
id: TC-PS-03
purpose: Verify projectless chat remains usable while local development fails closed and project continuation creates a new Session
status: active
surface: desktop-ui
type: hybrid
obligations:
  - TO-A3
tags:
  - projectless
  - handoff
  - fail-closed
entrypoint: ordinary chat
revision: 1
---

# TC-PS-03 — 无项目会话与“在项目中继续”

## 前置

- 隔离 user-data 中存在一个可注册测试 Project。
- 配置中即使存在全局默认 workspace，也不得赋予无项目 Session 本地开发权限。

## 步骤与预期

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 从普通“新建会话”入口创建无项目 Session，并发送普通文本消息。 | 会话位于无项目区域，普通聊天正常；右侧明确显示无项目状态。 |
| 2 | 在该 Session 请求读取、写入或打开任意本地项目文件，并观察可用工具/错误。 | 本地项目开发工具不可用或在入口明确 fail closed；没有使用全局 workspace、最近 Run 或旧项目路径执行。 |
| 3 | 点击“在项目中继续”，选择测试 Project 并确认。 | 创建一个新的项目 Session，原无项目 Session 不被原地改绑；新 Session 出现在所选 Project 分组。 |
| 4 | 检查新 Session 首屏中的交接内容，再切回原 Session。 | 新 Session 只包含有界、公开、结构化的目标/对话交接和来源 Session 身份；没有复制完整消息、Memory、工具原始结果、私有推理或凭据；原 Session 历史与无项目归属不变。 |
| 5 | 完全重启应用后分别打开新旧 Session。 | 新 Session 仍绑定 Project；原 Session 仍是无项目且本地开发 fail closed；二者标题、消息不串线。 |

## 通过条件与证据

- 步骤 1～5 全部满足；任何无项目本地文件访问成功均为 FAIL。
- UI primary：新旧 Session 分组、交接可见内容与重启恢复截图。
- runtime primary：新旧 Session/root 关联、工具 admission 负向结果、交接字段/大小上限检查与敏感 canary 扫描。
