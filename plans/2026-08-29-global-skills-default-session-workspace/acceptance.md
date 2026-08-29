# 验收标准：全局 Skill 与普通会话默认工作区

## 范围

- 包含：通过产品安装入口安装的 Skill 进入全局 managed Capability authority；安装成功后，新旧所有 Session 都能发现并调用该 Skill。
- 包含：所有 Session 使用同一套全局 Skill catalog 与产品 Tool catalog；Session/Project 不再裁剪 Skill 或 Tool 的可发现集合。
- 包含：普通会话在用户未选择目录时，自动在当前用户“文档/SimpleHarnessProjects”下创建一个独立文件夹，并把它冻结为该 Session 的工作目录。
- 包含：用户显式选择目录时，Session 使用该目录，不再创建或改用默认目录。
- 包含：新建普通会话的权限模式出厂默认值为“自动”；用户后续显式修改时持久化其选择。
- 包含：macOS 当前生产路径、持久化、升级兼容、错误态、自动化测试和真实桌面 UI 验收。
- 明确不包含：绕过 Tool 的授权、确认、平台支持、健康状态或执行期安全策略；把不可用 Tool 伪装成可成功执行。
- 明确不包含：Windows 真实 UI 验收、远程 Skill marketplace 设计重做、历史 Git 证据迁移。
- 明确不包含：继续实现 2026-08-27 “Project-scoped Skill”旧行为；其尚未交付的 project binding 方案由本需求取代。

## 主要矛盾

当前 Session 的 durable Project/workspace authority 与 Manager-owned immutable Capability Pack authority 是两条独立生产链。目标不是把 Skill 文件复制到每个项目，而是让全局 Capability catalog 成为所有 Session 的共同输入，同时仍让每个 Session 拥有确定、隔离且可恢复的工作目录。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|---|---|---|---|
| AC-1 | 默认工作区 | 新建普通会话且未选择目录时，系统在 OS Documents 下的 `SimpleHarnessProjects` 中原子创建一个该 Session 独占、名称冲突安全的子目录；持久化绑定的 effective execution root 与该目录的 canonical identity 一致，重启后不漂移。 | 必须 |
| AC-2 | 用户选择优先 | 新建普通会话时若用户显式选择有效目录，Session 冻结使用该 canonical 目录；系统不为这次会话创建默认项目目录。取消选择按“未选择”处理并走 AC-1，非法/不可用目录显示明确错误且不得产生半绑定 Session。 | 必须 |
| AC-3 | 全局 Skill 安装 | 从设置页或聊天中的正式安装入口安装一个合法 Skill 后，只产生一个全局 managed install/publish 结果与可审计 receipt；不得写入项目级 `.claude/skills`、`.codex/skills` 或每 Session 副本。重复安装同一精确版本幂等。 | 必须 |
| AC-4 | 全 Session Skill 可见 | AC-3 成功后，安装前已存在的 Session、安装后新建的默认目录 Session、用户选目录 Session，在下一次 fresh Run/明确 catalog refresh 后都能发现并调用该 Skill；应用冷重启后仍成立。 | 必须 |
| AC-5 | 全 Session Tool 可见 | 每个 Session 的 runtime catalog 都能搜索/描述产品注册的全部 Tool；不因普通会话、Project、默认目录或用户选目录而静默删减。Tool 的实际执行仍由既有 authorization/confirmation/health/platform policy 决定，并返回真实不可用或拒绝结果。 | 必须 |
| AC-6 | 原子失败与恢复 | Skill fetch/校验/publish/bind 任一步失败时，不对任何 Session 暴露半安装版本，不破坏上一有效全局 catalog；重试可安全恢复。默认目录创建或 Session 持久化失败时，不留下可被误认作有效 Session 的半绑定记录。 | 必须 |
| AC-7 | 生产入口一致 | Settings 与 Chat 安装入口、fresh Run 与 continuation、冷重启恢复均消费同一个全局 authority；legacy `<userdata>/skills` loader 或 dormant RunKernel 路径不能成为成功判据。 | 必须 |
| AC-8 | 用户可见反馈 | UI 清楚显示默认/已选工作目录，以及 Skill 全局安装的进行中、成功、失败与已安装状态；成功提示只有在全局 publish receipt 可验证且 runtime 可见性证明成立后出现。 | 必须 |
| AC-9 | 权限自动模式默认开启 | 新建普通会话时，权限模式默认显示并持久化为“自动”；fresh Run、continuation 和冷重启均采用该值。用户显式切换到其他受支持模式后保持其选择。`skill_install` 在自动模式下必须先完成完整 Git/包预检并生成可审计的 durable auto-approved receipt，然后无需人工确认继续 publish/verify；手动模式仍要求确认。拒绝、健康、平台、workspace 与 source validation 门禁不得绕过。 | 必须 |
| AC-10 | 安装失败与 Run/UI 收敛 | Chat 或 Settings Skill 安装在任一阶段失败时，调用方收到稳定错误码、公开说明与 retryable 标记；不可重试的相同请求不得无限重放。Chat Run 必须进入 completed/failed/cancelled，或进入带明确恢复/取消出口的真实 waiting 状态；UI 在后端状态变化后 5 秒内停止显示“仍在安装/工具执行中”，且健康检查不得只清本地状态而遗留无出口的后端 Run。 | 必须 |

## 非功能与边界

- 安全：Git Skill 输入继续执行 symlink、path traversal、materialized-package 和 manifest 校验；日志、截图、receipt 不包含 token、密码或 API key。
- 一致性：全局 catalog 发布采用不可变版本/快照与原子 current pointer；运行中的 attempt 使用其冻结快照，下一 fresh Run 或显式刷新采用新版本，避免半程漂移。
- 并发：两个 Session 同时安装同一 Skill，或同时新建默认工作区时，结果不得相互覆盖；目录名和 install operation 均需冲突安全。
- 幂等：相同安装 request/version 重放返回稳定结果；不得重复复制或产生多个 active binding。
- 兼容：保留现有 Project/session durable authority；旧 Session 在迁移后仍能打开。对既有 project-scoped prereq 数据采取显式、可测试的兼容/退休策略，不静默误读。
- 性能：全局 Skill/Tool metadata catalog 不按 Session 复制；fresh Run catalog 构建不得引入网络依赖。
- 测试证据：原始截图、日志、DB 与 receipt 写入 `.local-test-evidence/2026-08-29/...` 并保持 Git ignored；Git 仅保存结论、索引、hash 与状态。

## Assurance contract 摘要

- Profile：standard。
- 受保护资产：全局 Capability catalog、Skill 包完整性、Session 工作目录绑定、用户已有文件、Tool 授权边界。
- 可信假设：当前用户账户、OS/kernel、项目内 gate 与绝对路径系统程序可信；用户明确选择的目录代表授权意图。
- 范围内失败：错误作用域、半发布、目录碰撞/漂移、旧 Session 不可见、catalog 裁剪、把“可发现”误实现为“绕过授权”、失败提示先于 runtime 证明、自动模式跳过安装预检、失败后 Run/UI 状态不收敛。
- 范围内对手：恶意或畸形 Skill Git 内容（symlink、traversal、无效 manifest）；不假设宿主 OS 被攻破。
- 明确范围外：hostile-host、Windows 真人测试、Skill 供应链签名基础设施重做。
- 最大可接受影响：失败最多使当前安装或当前 Session 创建失败；不得污染上一有效全局 catalog、其他 Session 的目录绑定或用户选择目录中的既有内容。

## 条件门适用性

- `input_sensitive=false`：这是确定性的 Session/设置/catalog UI 与持久化流程，输出不取决于自然语言语义质量。
- `llm_payload_driven=false`：Skill/Tool catalog 与目录绑定不由 LLM 结构化载荷直接驱动；聊天安装 Tool 仍走 typed host effect。
- `stateful_init=true`：全局 catalog、旧 Session 迁移和默认工作区绑定需要在全新数据与冷重启路径验证。

## 测试场景矩阵

| scenario_id | 场景 | required | manual_required | 终态断言 |
|---|---|---:|---:|---|
| S-GS-01 | 全新用户数据，新建普通会话，不选择目录 | 是 | 是 | Documents/SimpleHarnessProjects 下创建独占目录，UI 与持久化 execution root 一致 |
| S-GS-02 | 新建普通会话，显式选择现有目录 | 是 | 是 | 使用所选目录且不创建该会话的默认目录 |
| S-GS-03 | 从正式 UI 安装合法测试 Skill | 是 | 是 | 显示全局安装成功，receipt 可验证，无项目级副本 |
| S-GS-04 | 安装前旧 Session、默认目录新 Session、用户选目录新 Session 分别 fresh Run | 是 | 是 | 三者均可搜索并实际调用同一已安装 Skill |
| S-GS-05 | 冷重启应用后打开旧/新 Session | 是 | 是 | Skill 与 Tool catalog 仍可见，工作目录绑定不漂移 |
| S-GS-06 | 安装恶意/损坏 Skill 或模拟 publish 失败 | 是 | 否 | 失败原子、上一 catalog 保持可用、无半安装可见 |
| S-GS-07 | 在不同 Session 搜索代表性 built-in、MCP/健康外部 Tool、全局 Skill | 是 | 是 | catalog 不按 Session 裁剪；不可执行 Tool 显示真实 policy/health 结果 |
| S-GS-08 | 并发创建默认会话并重放同版本安装请求 | 是 | 否 | 目录不冲突、安装幂等、只有一个 active 版本 |
| S-GS-09 | 新建普通会话并检查权限模式，再切换模式、continuation 与冷重启 | 是 | 是 | 初始为自动；用户覆盖值可持久化；强制确认/拒绝路径仍按 policy 生效 |
| S-GS-10 | Auto 下从 Chat 安装合法 Skill，并分别触发无效 URL、无 Skill 仓库和 provider unknown/健康超时收敛 | 是 | 是 | 合法安装无人工确认且 receipt 完整；失败返回稳定错误并停止“安装中”；真实 waiting 有恢复/取消出口且前后端状态一致 |

## 测试义务矩阵

| obligation_id | type | ac_id | risk | min_decisive_test | required_reason |
|---|---|---|---|---|---|
| TO-A1 | delivery | AC-1 | — | fresh profile 创建默认会话并核对 UI、DB、磁盘、重启 | 直接证明默认 workspace authority |
| TO-A2 | delivery | AC-2 | — | 选择目录、取消选择、非法目录三路径 | 直接证明用户选择优先与错误原子性 |
| TO-A3 | delivery | AC-3 | — | UI 安装合法 Skill，核对全局 receipt 与无项目副本 | 直接证明全局 managed install |
| TO-A4 | delivery | AC-4 | — | 旧/默认/选择三类 Session fresh Run 调用同一 Skill | 直接证明跨 Session 可见与可执行 |
| TO-A5 | delivery | AC-5 | — | 三类 Session 搜索/描述代表性完整 Tool 集并验证 policy 拒绝 | 证明可见性与授权边界没有混淆 |
| TO-A6 | delivery | AC-6 | — | 注入 fetch/validate/publish/persist 失败并恢复 | 证明原子失败和上一版本保护 |
| TO-A7 | delivery | AC-7 | — | 生产 object graph/运行时探针覆盖 Settings、Chat、fresh、continuation、cold restart | 防止接到 dormant/legacy seam |
| TO-A8 | delivery | AC-8 | — | 当前 build 真人点击核对状态文案与成功时序 | 证明用户看到的状态来自真实 authority |
| TO-A9 | delivery | AC-9 | — | 新会话默认值、用户覆盖、continuation、冷重启和强制确认/拒绝路径 | 直接证明自动模式默认开启且没有变成授权绕过 |
| TO-A10 | delivery | AC-10 | — | Chat 安装失败、provider unknown 与健康超时各执行一次并核对前后端终态 | 直接证明失败不会形成假性“仍在安装”或无出口 waiting |
| TO-R1 | change-risk | AC-1,AC-2 | FAIL-WORKSPACE-AUTHORITY | SessionProjectBinding 回归 + canonical identity/冲突测试 | 修改 Session 默认创建与 durable root |
| TO-R2 | change-risk | AC-3,AC-4,AC-7 | FAIL-GLOBAL-CATALOG-WIRING | 服务导出到真实 SDK runtime ingress 的接线断言 | 修改共享 capability/runtime 基础设施 |
| TO-R3 | change-risk | AC-5,AC-9 | FAIL-AUTH-BYPASS | catalog 全集、自动模式与执行授权的分离测试 | “所有 Tool 可访问”和自动权限容易误伤安全边界 |
| TO-R4 | change-risk | AC-6 | FAIL-ATOMICITY | 并发/故障注入与恢复测试 | 存在共享可变 current pointer 和目录副作用 |
| TO-R5 | change-risk | AC-1..AC-8 | FAIL-REGRESSION | critical + affected + 因共享 runtime 触发的 full-surface smoke | 改动跨 Session、Capability 与 runtime 公共入口 |
| TO-R6 | change-risk | AC-9,AC-10 | FAIL-AUTO-INSTALL-CONVERGENCE | Auto/Manual 两种授权链、结构化错误映射、durable cancel/settle 接线测试 | 本次修复同时触及授权 saga 与 SDK Run/UI 收敛 |

## 完成定义与停止条件

- 全部 AC 与 required obligation 有当前 HEAD 的决定性 PASS 证据；当前 macOS build 完成真实 UI 点击与真实 runtime/Provider 路径验证。
- 运行中的 attempt 可冻结旧快照，但下一 fresh Run/显式 refresh 必须获得新全局 catalog；不得靠重启 backend 才生效。
- `ARCHITECTURE/` 对应事实源和 `PROJECT_STATUS.md` 同步更新；不写 `STATUS/`。
- 工作树中本任务路径形成可审阅、独立提交；不纳入用户现有实时语音等无关改动。
- `plan_test_gate.py finalize` exit 0 且产生有效 receipt 才能宣布完成。
- 本次共有 9 条 MUST，超过单 slice 默认阈值，因此 Phase 1 必须形成 program plan，并拆成各自独立验收的垂直 slices；不得为满足数字而压缩条款。
- 若真实 object graph 证明上述全局 authority 或权限默认值需要破坏现有授权边界、需不可逆迁移、或超过 10 Tasks/3 个高风险子系统，则停止并提交修订后的 program plan 给用户重新确认，不用兼容 hack 绕过。
