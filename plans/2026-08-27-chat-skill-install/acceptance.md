# 验收标准：聊天安装 Project-scoped Skill

## 范围

### 包含

- 用户在已绑定 Project 的主消息页用自然语言提供 GitHub Skill 仓库 URL 时，Agent 通过正式安装能力处理，不用通用 shell 猜目录。
- “安装到当前项目”持久化为该 Project 可见的 managed Skill/Capability，其他 Project 与 projectless Session 不继承。
- 聊天入口与设置页 URL 入口共用同一个 stage、校验、确认、发布和 reload 服务。
- 单 Skill 与包含多个 `SKILL.md` 的仓库都先展示待安装清单、来源和权限；用户一次确认后再原子发布。
- 安装成功以当前 Project 的新 Run catalog 可发现、可冻结并可调用为准，不以文件复制成功为准。

### 明确不包含

- 不兼容 Claude Code 的 `.claude/skills` 或 Codex 的 `.codex/skills` 目录约定。
- 不把“安装到当前项目”静默提升为用户全局安装。
- 不允许模型绕过正式安装工具，用 `git clone`、`cp` 或 `run_shell` 后宣称安装完成。
- 不在本 slice 实现非 GitHub 来源、私有仓库认证或 Skill marketplace 搜索推荐。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|---|---|---|---|
| AC-SI-1 | 聊天意图进入正式安装工具 | 在绑定 Project 的 Session 输入“请你安装这个skill到当前项目：<GitHub URL>”，Provider 首个安装副作用必须调用 typed Skill installer；不得通过 shell 写 `.claude/skills`/`.codex/skills`，也不得在未取得安装结果前声称成功 | 必须 |
| AC-SI-2 | Project scope | 安装完成后，同一 Project 的新 Run 能发现目标 Skill；另一个 Project 与 projectless Session 均不可发现，磁盘发布位置和 durable binding 能追溯到 exact `project_id + project_revision/root identity` | 必须 |
| AC-SI-3 | Stage 与确认 | 单 Skill 和多 Skill 仓库都先完成 URL 解析、manifest/frontmatter、工具/权限与路径安全校验，再显示一次包含所有候选项和权限的确认卡；拒绝不产生正式安装，确认只提交清单中 exact digest | 必须 |
| AC-SI-4 | 原子、幂等与失败语义 | 同 URL/exact revision 重试不产生重复安装；batch 任一发布失败不得留下“部分成功但整体宣称成功”；网络、格式、权限、确认过期和 catalog publish 失败均返回结构化错误与可重试出口 | 必须 |
| AC-SI-5 | Runtime 可用性证明 | UI 只有在 Manager receipt 持久化、Project catalog 刷新且新 Run resolve 到 exact manifest/content hash 后显示安装成功；随后自然语言调用 `/plan-bs` 或等价触发可实际 page-in 该 Skill | 必须 |
| AC-SI-6 | 设置页一致性与兼容 | 设置页“从 URL 添加”复用同一服务和确认卡；既有 17 个 first-party Skill、Capability Pack、权限卡、Session Project 隔离和普通 shell 工具行为不回归 | 必须 |

## 非功能 / 边界

- **安全**：只接受 HTTPS GitHub URL；stage 有下载大小、文件数量、路径穿越、symlink、manifest、`allowed-tools` 和已知工具校验；日志不得记录 token/cookie。
- **权限**：第三方 Skill 安装属于 `skill_install` 高风险类别，必须由真实 UI 确认；LLM 不能替用户确认。
- **原子性**：stage 与 publish 分离；确认绑定 URL canonical form、resolved revision、候选清单 digest、Project identity 和 expiry。
- **幂等**：相同 Project + content digest 重放返回既有 receipt；不同 revision 视为明确升级，不静默覆盖。
- **兼容**：现有 user-data marketplace inventory 不自动转成生产 authority；legacy 文件只读，不因本修复重新开放裸 Loader。
- **可观测**：记录 stage、confirm、publish、catalog refresh、runtime resolve 的 stable code、opaque correlation 和有界耗时。
- **性能**：确认后本地 publish + catalog refresh 在 2 秒内完成；网络 staging 单独显示进度且有界超时。

## Assurance contract 摘要

- Profile：standard。
- 受保护资产：Project 文件边界、用户 Skill/Capability inventory、安装确认权、runtime catalog 完整性。
- 可信假设：当前开发者账户、OS/kernel、GitHub HTTPS 与仓库内既有 Capability Manager/permission gate 可信。
- 范围内失败/对手：错误 URL、恶意仓库路径/manifest、模型误用 shell、跨 Project 泄漏、partial publish、重复/过期确认。
- 明确范围外：私有 GitHub 认证、hostile host、GitHub 账户接管、非 GitHub 下载源。
- 最大可接受影响：失败最多留下可回收 staging 与明确失败 receipt；不得修改 Project 源码或其他 Project catalog。

## 测试场景矩阵

| scenario_id | input_class | exact_input | primary_risk | gate_type | required | manual_required | terminal_expectation | quality_bar |
|---|---|---|---|---|---:|---:|---|---|
| SI-M1 | 自然中文 + 多 Skill GitHub 仓库 | `请你安装这个skill到当前项目：https://github.com/DennyWanye/plan-test-skill` | 原始问题、正式工具路由、batch 确认与真实可用 | positive-value | 是 | 是 | 用户确认后 completed，3 个 Skill 可见且可调用 | 不出现 shell 猜目录；清单准确；新 Run 能 page-in `plan-test` |
| SI-M2 | 单 Skill URL / 中英混合 | `Install this skill into this project: <single-skill GitHub URL>` | single stage、Project scope 和自然语言泛化 | positive-value | 是 | 是 | confirmed + exact one Skill published | 另一 Project 不可见；receipt/hash 可追溯 |
| SI-M3 | 拒绝安装 | `把这个 skill 装到当前项目：<valid URL>`，确认卡点击拒绝 | LLM 不得代确认、拒绝零副作用 | negative-safety | 是 | 是 | denied，catalog 不变 | 无正式目录、binding 或 Manager receipt |
| SI-M4 | 非法/恶意仓库 | `安装这个 skill：<fixture URL>` | path traversal/symlink/manifest/unknown tool fail-closed | negative-safety | 是 | 是 | rejected + structured error | 不写 Project，不产生部分安装，不宣称成功 |
| SI-M5 | 冷启动与隔离 | fresh userdata → 注册两个 Project → 在 A 直接执行 SI-M1 → 完整重启 → A/B 分别检查 | stateful init、durable binding、catalog 恢复 | positive-value | 是 | 是 | A 重启后仍可用，B 不可见 | 无暖启动依赖；Project identity 保持精确 |

## LLM 行为变异清单

- **乱序**：confirm/result 晚于其他 UI 状态到达时，只按 installation id/revision 归约，不把未提交 stage 显示为 installed。
- **重复**：模型重复调用 stage 或同一 confirm 时，返回同一 stage/receipt 或结构化 already-settled，不重复发布。
- **schema 违约**：缺 URL、scope、Project identity 或错误字段时 typed tool 拒绝，UI 不创建伪确认卡。
- **超长载荷**：超长 URL、候选列表或 manifest 摘要有硬上限与截断提示，不能撑爆确认卡或 prompt。
- **拒不调用工具**：模型只给手工复制命令或声称已安装时，completion gate 拦截成功声明并提示使用正式安装能力。

## 测试义务矩阵

| obligation_id | type | ac_id | risk | min_decisive_test | required_reason |
|---|---|---|---|---|---|
| TO-SI-1 | delivery | AC-SI-1 | — | catalog/Provider 集成测试 + SI-M1 真实聊天 | 直接证明聊天入口不再退化 shell |
| TO-SI-2 | delivery | AC-SI-2 | — | A/B/projectless 三域发现矩阵 + 重启 | 直接证明 Project scope |
| TO-SI-3 | delivery | AC-SI-3 | — | single/batch stage、approve/deny、digest mismatch | 证明确认权与 exact 清单绑定 |
| TO-SI-4 | delivery | AC-SI-4 | — | 重放、batch 故障注入、过期确认、网络失败 | 证明副作用原子与诚实错误 |
| TO-SI-5 | delivery | AC-SI-5 | — | receipt→catalog→new Run resolve→page-in 端到端 | 文件存在不足以冒充成功 |
| TO-SI-6 | delivery | AC-SI-6 | — | 设置页 URL smoke + first-party/catalog/shell 回归 | 证明共用服务且不破坏既有入口 |
| TO-SI-R1 | change-risk | AC-SI-1 | ROUTE-BYPASS | 模型尝试 shell 安装时 success-claim gate 拦截 | 防止模型换措辞继续旁路 |
| TO-SI-R2 | change-risk | AC-SI-2 | CROSS-PROJECT | 两 Project 同名 Skill/不同 revision 隔离测试 | 修改共享 catalog，必须防串域 |
| TO-SI-R3 | change-risk | AC-SI-3 | CONFIRM-REPLAY | confirm 绑定 scope/digest/expiry 的重放测试 | 高风险安装确认不可复用 |
| TO-SI-R4 | change-risk | AC-SI-6 | STARTUP-REGRESSION | fresh 与现有 userdata 启动 smoke | 改启动装配和持久 catalog |

## 完成定义

- 所有 MUST AC 与 required obligation 有当前 run 的决定性 PASS。
- 自动化、真实 macOS 主消息页和设置页 URL 安装均通过；原始证据保存在 `.local-test-evidence/`。
- 没有把文件复制、installed list 或 Loader reload 当作 runtime 可用证明。
- 对应 `ARCHITECTURE/COMPANION_GROWTH.md`、`ARCHITECTURE/PROJECT_STATUS.md` 与 testcase index 同步。
- plan-test gate `finalize` exit 0，且提交态门满足。
