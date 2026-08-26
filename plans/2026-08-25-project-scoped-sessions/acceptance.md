# 验收标准：以本地项目为根目录的 Session

## 主要矛盾

开发 Session 必须从创建、恢复到每次 Agent Run 都可靠地工作在同一个用户可见项目中；同时不能把
路径仅作为提示词或可随时修改的 UI 字段，从而造成会话历史、工具执行目录和写入边界相互漂移。

## 范围

- 包含：本地 Project 身份与根目录持久化；项目会话与无项目普通会话；新建会话时绑定项目；Git
  仓库根目录识别与显式子目录选择；左侧项目分组；右侧只读项目上下文；Session/Run/Harness/工具
  工作目录统一；项目目录缺失与重新定位；升级时一次性清空旧会话域数据。
- 包含：数据模型区分 `project_root`（项目归属）与 `execution_root`（实际执行目录）；普通目录两者
  相同，模型允许未来 worktree 使用不同执行目录。
- 明确不包含：修改既有项目 Session 的项目归属；自动创建/删除 Git worktree；跨设备同步项目；
  Git clone；云端项目注册；删除用户项目文件；Windows 平台验收（用户于 2026-08-25 明确暂不考虑，
  Windows probe 与 UI 真测保留为后续非阻断工作）。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| AC-1 | 注册本地项目 | 用户选择本地文件夹时，界面预览最终 `project_root`；检测到 Git 仓库默认采用仓库根目录，用户可显式选择将所选子目录注册为独立项目；非 Git 目录采用所选目录；规范化后的同一目录不会重复创建 Project。 | 必须 |
| AC-2 | 创建不可变的项目 Session | 从项目分组新建 Session 时，新 Session 持久绑定该 `project_id` 与 `execution_root`；应用重启后绑定保持；既有项目 Session 不提供更换项目/根目录入口。 | 必须 |
| AC-3 | 保留无项目普通会话 | 用户仍可创建 `project_id = null` 的普通会话；普通会话不获得项目文件开发能力；选择“在项目中继续”时创建新的项目 Session，携带有界结构化交接和来源 Session ID，原普通会话及其归属不变。 | 必须 |
| AC-4 | 单一工作目录 authority | 项目 Session 每次 Run 都从持久化 Session Binding 派生 `TaskWorkContext.workspace_root`、Harness `workspace`/`write_scope_root`、终端 cwd、文件工具根目录和项目规则发现根目录；实际执行证据显示这些值一致，不能由前端临时字段或模型文本覆盖。 | 必须 |
| AC-5 | 项目化会话 UI | 左侧以 Project 分组列出 Sessions，并提供“新建同项目 Session”；右侧固定只读显示项目名、`project_root`、`execution_root` 和可用的 Git 状态，提供复制路径与在 Finder/Explorer 打开，但不提供修改根目录。 | 必须 |
| AC-6 | Project 与 execution root 分离 | 当测试 fixture 中 `project_root != execution_root` 时，会话仍归属 `project_root` 项目组，所有开发执行仅落在 `execution_root`，右侧同时显示二者；本次不要求自动创建或发现 worktree。 | 必须 |
| AC-7 | 目录不可用与重新定位 | 恢复会话时若目录不存在，历史 Session 仍可见并显示“项目目录不可用”，开发工具 fail-closed；用户重新定位并通过同项目身份校验后，Project 路径原子更新、该 Project 下 Sessions 一起恢复；选择无关目录必须拒绝。 | 必须 |
| AC-8 | 升级后按全新安装处理旧会话数据 | 从任意 `user_version <= 32` 首次启动时，应用在开放 Session/Run/UI 入口前一次性清空升级前的 Session、消息、Project 及其 Run/上下文/投影/对话派生 Memory；保留全局 Provider、默认模型、应用设置、Keychain 和真实项目文件。重置中断后必须 fail closed 且下次启动可继续；完成标记幂等，升级后新建数据在后续重启中不得再被清除。 | 必须 |

## 非功能 / 边界

- 错误态：空路径、文件而非目录、无权限目录、已删除目录、无关目录重新定位都必须给出明确错误，且不产生半绑定 Session。
- 幂等：重复注册同一规范化目录、重复启动迁移、重复提交同一次 Session 创建不得生成重复 Project 或冲突绑定。
- 一致性：Session 创建与 Project 绑定必须处于同一事务边界；不存在已进入开发 UI 但没有持久绑定的项目 Session。
- 路径身份：本轮必须覆盖当前目标平台 macOS 的 symlink、同卷 rename 与大小写表现；不得只用未经处理的显示字符串作为身份。Windows 等价路径表示保留 probe，但不阻断本轮验收。
- 性能：含 500 个项目、每项目 200 个 Session 的本地 fixture 下，项目会话列表查询 p95 不高于 200 ms；UI 首屏允许分页/虚拟化，不要求一次渲染全部会话。
- 升级边界：旧会话域数据只做逻辑重置，不承诺取证级安全擦除；完成后不得保留可被产品恢复或召回的旧会话备份。本次不改变 Harness SDK 公共协议。
- UI 验证：确定性桌面 UI，必须在当前实际构建上完成真实点击、重启恢复与截图/日志证据；自动化测试不能替代真人 UI 证据。
- 测试阶段默认开启：能力通过验收后默认启用，不保留默认 OFF 的灰度开关。

## Assurance contract 摘要

- Profile：standard。
- 受保护资产：用户项目文件、全局 Provider/模型/应用设置、工作目录/写入边界、升级后新建的 Session 数据。
- 可信假设：本地 OS 与用户账户可信；用户通过系统文件夹选择器明确选择目录；绝对系统路径工具可信。
- 范围内失败：路径漂移、重复项目、跨项目误写、缺失目录仍执行、旧会话残留可见/可恢复/可召回、误删全局配置或真实项目文件、UI 与执行 authority 不一致。
- 明确范围外条件：恶意宿主机/内核、用户主动篡改数据库、跨设备同步冲突、自动 worktree 生命周期管理。
- 最大可接受影响：旧会话域数据按用户批准全部删除；除此之外，不得修改项目文件、全局 Provider/模型/应用设置，也不得让执行越过已绑定 `execution_root`。

## 适用性声明

- `input_sensitive=false`：功能是确定性的项目/Session CRUD、导航与执行上下文绑定，输出不随自然语言语义变化。
- `llm_payload_driven=false`：没有 LLM 结构化输出驱动此 UI 或绑定状态机。
- `stateful_init=false`：不依赖异步远程配置或首次登录；但 schema 迁移和应用重启恢复作为 AC-2/AC-8 的明确必测路径。

## 测试义务矩阵（Test Obligation Matrix）

| obligation_id | type | ac_id | risk | min_decisive_test | required_reason |
|---------------|------|-------|------|-------------------|-----------------|
| TO-A1 | delivery | AC-1 | — | 用 Git 根、Git 子目录显式独立、非 Git 目录各注册一次，并重复注册等价路径 | 直接证明项目识别、用户选择和去重规则 |
| TO-A2 | delivery | AC-2 | — | 从项目新建 Session，执行一轮后重启应用并恢复，确认绑定不变且无编辑入口 | 直接证明不可变持久绑定 |
| TO-A3 | delivery | AC-3 | — | 创建普通会话，验证开发工具不可用，再“在项目中继续”并核对新旧 Session 与交接 | 直接证明普通聊天保留且不会原地改绑 |
| TO-A4 | delivery | AC-4 | — | 从真实项目 Session 发起一次文件读取和一次受控写入，核对 Run/Context/终端/工具观测根目录 | 主要矛盾的端到端决定性测试 |
| TO-A5 | delivery | AC-5 | — | 真实桌面 UI 检查项目分组、新建同项目 Session、右侧只读字段、复制与打开目录 | 用户可见交付必须真人验证 |
| TO-A6 | delivery | AC-6 | — | 使用 project/execution root 不同的 fixture，核对分组、展示和实际落盘目录 | 防止把项目归属和执行目录错误合并 |
| TO-A7 | delivery | AC-7 | — | 移走测试项目目录后恢复 Session，验证阻断；分别用同项目和无关目录重新定位 | 证明缺失目录不会导致误写或历史消失 |
| TO-A8 | delivery | AC-8 | — | 用 v9/v17/v23/v31/v32 代表性旧库及 fresh fixture 启动，证明所有会话域数据已清空、全局配置与项目文件 hash 不变；再新建完整 Session/Run/Memory 数据链并重启 | 证明任意旧版本可沿正式迁移链进入一次性重置，且不会删新数据 |
| TO-R1 | change-risk | AC-2 | FAIL-TXN | 故障注入使 Session 创建事务中断，确认没有孤儿 Project/半绑定 Session | 改动跨 Project 与 Session 持久化事务 |
| TO-R2 | change-risk | AC-4 | FAIL-AUTHORITY | 构造前端路径、旧 code-session 路径与持久绑定冲突，断言执行只接受 Session Binding | 防止多份路径 authority 漂移 |
| TO-R3 | change-risk | AC-5 | FAIL-SIDEBAR | 项目分组分页/刷新后核对 Session 不消失、不重复、不误归组 | 左侧分组新增索引与缓存风险 |
| TO-R4 | change-risk | AC-8 | FAIL-RESET | 在 v33 commit 前后、外库清理阶段与 completion 前后注入中断，每次重启必须继续且不开放半清理状态 | 防止跨库重置中断后暴露孤儿或重复删除新数据 |
| TO-R5 | change-risk | AC-1 | FAIL-PATH-ID | macOS symlink/大小写/同卷 rename fixture 验证等价/非等价判定；Windows probe 后续执行 | 路径字符串漂移会造成重复项目和误归属 |
| TO-E1 | exploratory | — | 多卷挂载、网络盘和 inode 变化 | 对可移动/网络卷运行扩展恢复测试 | 非当前 AC，记录为后续探索，不阻断交付 |

## 完成的定义（DoD 摘要）

- AC-1～AC-8 全部通过对应 required 测试义务。
- 自动化覆盖 schema/事务/路径规范化/绑定 authority/v33 一次性重置，且受影响 surface smoke 无新增回归。
- 在当前实际桌面构建上完成项目注册、新建项目 Session、无项目转项目、重启恢复、目录缺失与重新定位的真实 UI 验证；另以隔离旧库验证升级空态、不重新登录的 Provider 调用，以及升级后完整新数据链再次重启仍保留。
- 新能力默认开启；原始截图和日志仅存 `.local-test-evidence/`，Git 只保存结论、索引与 SHA-256。
- 同次交付更新 `ARCHITECTURE/` 对应事实源及 `ARCHITECTURE/PROJECT_STATUS.md`。
