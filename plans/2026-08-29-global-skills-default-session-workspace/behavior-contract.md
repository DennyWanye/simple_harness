# 行为契约：全局 Skill 与普通会话

## 术语与实体

| 术语 | 唯一定义 |
|---|---|
| 普通会话 | UI 中不归入用户显式 Project 的 conversation Session；目标态仍拥有一个 automatic workspace binding。 |
| automatic workspace | 系统为一个普通 Session 在 OS Documents/SimpleHarnessProjects 下创建的独占目录及其内部 Project authority；UI 不把它展示为用户显式 Project。 |
| 用户选择目录 | 用户经 native folder picker 明确选择并注册的 Project root；优先于 automatic workspace。 |
| 全局 Skill | 绑定到当前 validated local owner 的 `user` capability scope、对其所有 Session 可发现的 managed immutable Skill。 |
| 可发现 | 可被 catalog/search/describe 列出，并带当前 eligibility；不等于可执行。 |
| 可执行 | 当前 Run 具备 handler、workspace、platform、health 与 authorization 后允许真实调用。 |
| 自动权限模式 | policy 对 auto-eligible 且 exact scope 可安全派生的 effect 自动授予有界 grant；Skill 安装仍须完整预检并留下 durable auto-approved receipt，但无需人工点击；deny/unavailable 不自动放行。 |

## Before / After

| 行为 | Before | After |
|---|---|---|
| 未选目录的新普通 Session | `projectless`，没有 execution root | 自动创建独占 workspace，底层 project-bound，UI 仍列为普通会话 |
| 用户已选目录的新 Session | 使用显式 Project | 保持使用显式 Project，不创建 automatic workspace |
| 旧 projectless Session | 无 workspace | 首次 fresh Run 前一对一迁移到 automatic workspace，分类不变 |
| Skill 安装作用域 | managed installer 仍绑定 Project identity | 安装到 validated owner 的 user-global scope |
| 旧 Project Skill binding | 可因 scope precedence 覆盖 user binding | 合法终态迁移到 user binding 后退休；非终态 superseded |
| Session 的 Skill catalog | 受 Project binding 影响 | 所有 Session fresh Run 冻结同一 owner-global generation |
| Tool catalog | projectless/project-bound 会静默裁剪 | search/describe 共享完整 descriptor catalog；不可执行项显示原因 |
| Provider callable Tool | 仅 eligible ToolSpec | 保持仅 eligible ToolSpec，不把 unavailable descriptor 注入模型 |
| fresh 权限默认 | manual | auto，带 factory-default provenance |
| 用户权限选择 | SQLite mode，无明确 provenance | CAS 写入 user-explicit；升级和重启不覆盖 |
| Auto 下 Skill 安装 | 直接 ALLOW，未建立 install intent/decision receipt，handler 必然失败 | 先 stage/validate，再绑定 auto-approved receipt，随后 publish/verify；无需人工确认 |
| 安装失败后的 Run/UI | 错误被压成 `Tool execution failed`，Run 可停在无出口 waiting，UI 继续显示安装中 | 稳定错误透传；Run terminal 或展示真实 waiting+恢复/取消；前后端状态一致 |

## 保留、改变、删除

- 保留：用户选择目录优先、immutable Session binding、Tool 执行授权、Skill Git 内容校验、运行中 attempt 的 frozen catalog。
- 改变：普通会话从物理 projectless 改为 automatic workspace；Skill 可见 scope 从 Project 改为 user-global；fresh 权限默认改为 auto；Auto 下 `skill_install` 从人工确认改为预检后 durable 自动批准；安装失败必须驱动 Run/UI 收敛。
- 删除/退休：Project-scoped Skill 作为当前安装语义、Settings 安装必须依赖当前 Project、按 Session 静默裁剪 descriptor catalog、误导性的“auto allows everything”表述。
- 不改变：用户显式 Project 的 UI 分组与根目录；Windows 当前非验收平台；不可执行 Tool/Skill 不承诺执行成功。

## 用户批准来源

- 原始需求：全局安装 Skill；普通会话未选择时在 Documents/SimpleHarnessProjects 建目录，选择时使用用户目录；所有 Session 可访问所有 Skill 和 Tool。
- 增量需求：默认权限自动模式开启。
- 批准状态：用户已于 2026-08-29 明确回复“确认”，随后补充并要求继续执行上述增量要求。
- 2026-08-29 增量批准：用户在审阅“Auto 下完整预检并自动批准、失败 Run/UI 收敛”的处理方案后回复“好的，确认”；原文 SHA-256 为 `6b84b0709b71298582d32cc57259dea8a3bcb7c9a2ed9841d23776f4ccacf226`。
