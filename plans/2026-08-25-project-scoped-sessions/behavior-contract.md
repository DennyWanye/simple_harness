# 行为契约：Project、Session、Run 与工作目录

> 状态：用户已于 2026-08-25 确认；批准消息 SHA-256：
> `36f33adaf0942634a8ece1eec4a6f30d44dec73e1ef8704b29d983a57f2e09ae`。
> 本文只冻结用户可见行为，不代表当前代码已经实现。

## 术语与实体关系

| 术语 | 定义 | 关系 |
|------|------|------|
| Project | 用户注册的一个逻辑本地项目，拥有稳定 `project_id` 和当前物理 `project_root` | 一个 Project 可包含多个 Sessions |
| Session | 一条持久对话历史；可以是无项目普通会话，也可以在创建时绑定一个 Project | 一个 Session 可包含多个 fresh root Runs；项目绑定创建后不可改 |
| Run | Session 中一次用户消息触发的独立执行；冻结本次执行上下文 | Run 继承 Session 的项目绑定，不能反向修改 Session |
| `project_root` | Project 当前物理位置，也是左侧分组身份的用户可见目录 | Project 合法重新定位时可原子更新 |
| `execution_root` | 项目 Session 实际执行和写入的目录 | 普通项目等于 `project_root`；未来 worktree 可不同 |
| 无项目会话 | `project_id = null` 的普通聊天 Session | 无本地项目开发 workspace；可创建新的项目 Session 继续 |

## Before / After

| ID | 当前行为（Before） | 目标行为（After） | 处理 |
|----|--------------------|-------------------|------|
| B-1 | `sessions_list` 只从有消息的 Sessions 聚合扁平列表；空 Session 在首条消息前不稳定可见。 | 列表从 durable active Session rows 出发，按 Project 分组；新建空项目 Session 立即可见。 | 改变 |
| B-2 | 新话题只传 `new_session=true`，后端生成 UUID 并继承模型绑定，不携带正式 Project Binding。 | 新建入口明确选择“无项目”或携带 `project_id`；后端在发布切换事件前持久化 Session Project Binding。 | 改变 |
| B-3 | 项目上下文可能来自 `code_sessions.project_root`、Session 最新历史 Run workspace 或全局配置 fallback。 | Session Project Binding 是新 Run 的唯一项目 workspace authority；历史来源只用于一次迁移。 | 改变 |
| B-4 | 无项目 Session 的 Host 可能从全局 `companion.workspace_root` 获得默认 `write_scope_root`。 | 无项目普通会话不获得本地项目 workspace 或项目文件开发能力；选择项目会创建一个新 Session。 | 改变 |
| B-5 | Run 的 `TaskWorkContext.workspace_root` 是 root-local immutable binding，但下一 fresh Run 可从 Session 的 latest Run 推导项目。 | 每个 fresh Run 从不可变 Session Binding 冻结 `execution_root`；前端、旧 Code Mode 或模型文本不能覆盖。 | 改变 |
| B-6 | `code_sessions` 以 base Session 保存项目目录，并可通过 upsert 改写 `project_root`。 | 迁移后的 Project/Session Binding 禁止改绑；`code_sessions` 只作旧数据读取兼容，停止新写入。 | 改变 |
| B-7 | 左侧是单层 Session 列表；当前项目目录只在部分上下文/卡片中出现。 | 左侧为 Project → Sessions；右侧固定只读显示 Project 和 execution root，无“修改根目录”。 | 改变 |
| B-8 | 普通新话题继承来源 Session 的模型配置。 | 新建同项目 Session 继续继承来源 Session 模型配置；从无项目会话转入项目时也继承模型配置和有界交接。 | 保留并扩展 |
| B-9 | 删除会话清空消息并墓碑化 delivery/owner，历史列表不再显示。 | 删除语义保持；Session 不可复活，Project Binding 与创建 receipt 作为不可变 provenance 保留但不再进入列表；不删除 Project 或用户文件。 | 保留并澄清 |
| B-10 | Session 标题、消息、Memory、归档/删除和 Provider binding 各有现有 authority。 | 这些 authority 保持；Project Binding 不吞并、不复制其职责。 | 保留 |
| B-11 | 项目目录缺失可能在 Run preflight 时才表现为 workspace unavailable。 | Session 历史和项目分组仍可见；UI 明确标记目录不可用，项目开发 Run 在入口即 fail closed。 | 改变 |
| B-12 | 已存在 root-local 的项目目录选择允许在首个子执行/写 effect 前 rebind 当前 Run。 | 该工作流创建项目目录的能力保留为 Run 内生成/选择产物位置；它不得改写 Session 所属 Project。 | 保留但澄清边界 |

## 显式不变式

1. `Session.project_id` 只允许从 `NULL`（创建无项目会话）或某个 Project（创建项目会话）开始，创建后不执行 update。
2. “在项目中继续”总是创建新 Session；不把当前无项目 Session 原地改绑。
3. Project 重新定位只修复同一逻辑项目的物理路径，不改变任何 Session 的 `project_id`。
4. UI 展示目录、Run workspace、工具 write scope 和项目规则发现必须能追溯到同一 Session Binding 版本。
5. `project_root != execution_root` 时，左侧归组只看 Project，所有本地执行只看 execution root。

## 保留、删除与改变清单

- 保留：无项目普通聊天、Session 多 fresh Runs、标题/消息/Memory/Provider binding、Session 删除、Run 内项目产物目录选择。
- 删除：既有项目 Session 的根目录编辑能力（本来就不应成为目标）；`code_sessions` 作为新项目 authority；全局 workspace 对无项目会话的隐式本地开发授权。
- 改变：新建 Session 协议、会话列表查询与分组、Run workspace 解析入口、缺失目录恢复、旧数据迁移。
