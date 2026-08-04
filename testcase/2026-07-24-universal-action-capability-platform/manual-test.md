# DeskPet 通用电脑行动与可执行能力包真人验收

> 状态：FROZEN — challenger 多轮复核通过；取消、续聊 FIFO、崩溃恢复与单 Driver 所有权竞态均已纳入必测边界
>
> 对应计划：`plans/2026-07-23-universal-action-and-capability-packs/plan.md`
>
> 验收事实源：`plans/2026-07-23-universal-action-and-capability-packs/acceptance.md`
>
> 平台：Windows 11 x64；真实 DeskPet 主消息入口；真实 provider

## 1. 验收结论边界

本组用例验证一条完整产品链，而不是“Godot detector 能调用”：

```text
普通消息
→ 固定 agent.general
→ 模型发现能力或 workflow_spawn profile
→ manual/auto 授权
→ 安装或自建能力
→ 通用 OS/文件/浏览器/桌面工具执行
→ Receipt 与真实应用验证
→ 失败回到同一模型创建新 Attempt
→ 继续原 TaskGoal
```

以下任一情况发生，本组总门直接 FAIL：

- 要求用户切到 Code 模式、隐藏入口或重发请求；
- S-1、S-2、S-3 任一没有达到自己的业务质量线；
- S-4 的损坏包代码被执行；
- S-5 没有在同一 root run 立即调用新工具；
- S-6 的第一次失败没有回到同一父模型，或重启后重复副作用；
- B-7 没有通过真实 `workflow_spawn` 产生并消费 ProfileLaunchTicket；
- fixture 准备器未 PASS、manifest 缺失或夹具在测试前已被污染；
- 任一 required 用例仍为 PENDING、PARTIAL、NOT RUN 或只有自动化旁证。

## 2. 环境、夹具与证据纪律

### 2.1 唯一启动拓扑

1. 真实 LLM 前置检查：
   - 只从 gitignored 的 `LOCAL-DEV-CREDENTIALS.md` 读取开发账号；
   - 完成 onboarding 后先关闭登录窗口再截图；
   - 截图、日志、报告和 fixture manifest 都不得出现账号、密码、token 或 device key。
2. 只启动一个 Tauri；不得手动启动 backend 或 Vite。
3. 给 Tauri 进程注入：
   - `DESKPET_BACKEND_DIR=F:\projects\deskpet\backend`
   - `DESKPET_PYTHON=F:\projects\deskpet\backend\.venv\Scripts\python.exe`
   - `DESKPET_BACKEND_PORT=18120`
   - `DESKPET_VITE_PORT=15193`
   - `DESKPET_DEV_MODE=1`
   - `DESKPET_USER_DATA_DIR=F:\projects\deskpet\.e2e-universal-action\userdata`
   - `DESKPET_WORKSPACE_DIR=F:\projects\deskpet\.e2e-universal-action\workspaces`
   - `CARGO_TARGET_DIR=F:\projects\deskpet\.build\cargo-universal-action-validation`
   - 从 fixture manifest 逐字注入
     `DESKPET_CAPABILITY_SOURCES_JSON` 与 `DESKPET_ULTRAFORGE_CANARY`。

S-1～S-5 使用下面这一条可复制启动命令：

```powershell
Set-Location F:\projects\deskpet
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\plans\2026-07-23-universal-action-and-capability-packs\manual-results-2026-07-24\run-isolated-tauri.ps1 `
  -ScenarioBatch S1-S5
```

launcher 读取 fixture manifest 并注入上述环境；使用
`tauri-dev-config.json` 把静态 `devUrl` 改为 `http://localhost:15193`，并让 Tauri
通过 bundled Node 自行启动唯一 Vite。独立 Cargo target 避免覆盖或结束用户现有
`target\debug\deskpet.exe`。

启动后用本轮 user-data 和端口过滤 `Win32_Process`，并用
`Get-NetTCPConnection -LocalPort 18120,15193` 对账；只允许一个 Tauri、一个
Tauri 自管 Vite 和一个 Tauri 自管 backend。

4. S-1～S-5 **不得**设置 `DESKPET_CAPABILITY_E2E_CASE_ID`。只有进入 S-6
   前才按 §5 的独立重启步骤设置
   `DESKPET_CAPABILITY_E2E_CASE_ID=UA-GODOT-FAIL-ONCE`。
5. 日志必须出现 Dev python 指向本 checkout；若出现 bundled backend，立即 FAIL 并重启正确实例。
6. Tauri 自行执行唯一 `beforeDevCommand`。不得再启动第二个 Vite。
7. 每批测试结束后，只按本次端口、用户数据目录、工作区和命令行定位并清理该进程树；记录精确 PID、父 PID、command line、监听端口和释放的 private memory，验证这些 PID 与端口归零，禁止按进程名广泛结束用户进程。另登记本轮下载的 portable Godot/Blender、S-5 user binding、UltraForge env/canary、UAC/Godot marker、fixture workspace 与临时下载；只清理隔离路径内本轮物料，不删除用户原有安装。

### 2.2 必备测试夹具

夹具只制造外部环境条件，不得注入模型答案、tool result、成功 terminal 或 UI 消息：

| 夹具 | 真实条件 | 禁止行为 |
|---|---|---|
| `UA-ULTRAFORGE-BADHASH` | 配置的本地能力源中有 `ultraforge` manifest，但一个声明文件的真实 SHA-256 不匹配；包入口若被运行会写 canary | 不得直接向 UI 注入 `hash_mismatch` |
| `UA-GODOT-FAIL-ONCE` | S-6 第一次真实 Godot headless 启动返回 `godot_executable_not_found`；第二次必须基于新探测证据，可使用状态已更新的同一路径或新用户级/portable 路径 | 不得伪造第二次成功，也不得改 provider 回复 |
| `UA-UAC-WAIT` | fixture 准备器生成的良性 elevation probe；由 Authenticode 有效的 Microsoft PowerShell 申请真实 UAC，只在隔离 workspace 写完成 marker | 不得自动点击 Secure Desktop，不得把等待写成失败，不得写系统目录 |
| `UA-PHOTOS` | 3 张带不同 EXIF 拍摄日期的照片、1 张无 EXIF 图片、1 个非图片文件；目录含中文和空格 | 不得预先生成目标能力包 |

启动 Tauri 前必须运行：

```powershell
.\backend\.venv\Scripts\python.exe `
  scripts\acceptance\prepare_universal_action_fixtures.py `
  --user-data-dir F:\projects\deskpet\.e2e-universal-action\userdata `
  --workspace F:\projects\deskpet\.e2e-universal-action\workspaces `
  --output F:\projects\deskpet\plans\2026-07-23-universal-action-and-capability-packs\manual-results-2026-07-24\fixture-manifest.json
```

命令必须返回 `status=PASS`。manifest 是本轮夹具事实源，至少记录：

- UltraForge source JSON、manifest hash、canary 路径与测试前不存在；
- Photos 输入/错误输入目录、各文件 hash、EXIF 与预期改名策略；
- UAC signed executable、签名状态、脚本 hash、launch argv、marker 与测试前不存在；
- Godot fail-once marker、S-6 专用环境和测试前不存在。

任一 required 夹具、hash、签名或“不存在”前态不满足时直接 `TOTAL FAIL`，
不得进入昂贵真人矩阵。

### 2.3 证据绑定

1. 每次输入带唯一标记：`UA-<case>-A<attempt>-<UTC timestamp>`。
2. 每个真人动作前声明：`坐标=(x,y)|动作=...|期望=...`。
3. 每次点击/输入前截图，动作后重新截图。中文使用真实焦点 + 剪贴板粘贴或真实键盘输入。
4. 每个 root 记录：
   - testcase/scenario ID、exact input、UI 提交时间；
   - session/task_scope/root run/Attempt/plan version；
   - provider call、tool call/effect/child、Receipt/Artifact；
   - 前后截图路径与 SHA-256；
   - 业务终态和人工质量检查。
5. 日志与 SQLite 只读查询只能给真实 UI run 对账，不能替代 UI 输入、真实应用窗口、真实浏览器操作或产物检查。
6. retry、重放、同意图改写和 continuation 单独记账，不得增加 distinct scenario 数。
7. 证据目录：
   `plans/2026-07-23-universal-action-and-capability-packs/manual-results-2026-07-24/`。
8. 禁止用 WebSocket 直注、backend API、直接 import registry、pytest、数据库写入或脚本回放冒充 UI 操作；SQLite 和日志只能在 UI 动作后只读对账。
9. 标记“环境受限”前至少尝试 3 种不同 workaround；任何 required case 的跳过必须先得到用户明确确认，否则为 FAIL。
10. 每条记录增加窗口标题、焦点控件、日志起止 offset、只读 SQL 文本、fixture hash，以及 root/task/Attempt/provider identifiers。

## 3. 昂贵矩阵前的价值 smoke

### VS-1：能力事实接地与单入口

输入：

> `UA-VS1-...`：你现在能不能在我的工作区创建文件、运行 PowerShell、启动应用并操作桌面？如果需要低频工具请先查真实能力目录，不要凭记忆回答。

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 打开桌宠“消息”，点击“新话题”，输入并发送上述文本 | 创建一个普通顶层 root，profile=`agent.general`；没有 Code/模式选择 UI |
| 2 | 等待最终回复并展开工具轨迹 | 回复基于本轮 PreparedToolSet/能力搜索；明确存在真实文件、Shell、应用与桌面能力，不回答“只有文档工具” |
| 3 | 只读对账 root 与上下文 | 原始输入没有被正则映射到 Driver；`task_type` 不改变 persona、provider、workspace 或工具目录 |

### VS-2：通用文件 + Shell 正向闭环

输入：

> `UA-VS2-...`：在当前测试工作区新建 `smoke hello/hello.txt`，写入 `DeskPet universal action OK`，再用 PowerShell 读取并核对内容。

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 设置为 Manual，发送上述文本 | 副作用前出现一次包含目标目录、写文件与 PowerShell 的 DeskPet 授权；不出现模式选择 |
| 2 | 真点击允许并等待 | 目录和文件真实创建；PowerShell 退出码为 0，读回内容完全一致 |
| 3 | 打开 Artifact/结果并核对 Receipt | 同 root 内完成；写与执行各有真实 effect/Receipt；无重复文件、无重复 terminal |

VS-1 或 VS-2 失败时不得继续 S-1～S-6。

## 4. 确定性 UI 与授权边界

### B-1：能力中心投影

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 点击工具栏“能力中心” | 打开标题为“能力中心”的真实窗口/面板，显示当前授权模式 |
| 2 | 搜索 `godot`，选择 Godot 项 | 显示稳定 ID、1.0.1 或当前实际版本、builtin 来源、scope、健康状态与可用动作 |
| 3 | 切换“操作”页，再返回能力页 | 页面可重入；重复进入不新增 install operation，不改变 active binding |
| 4 | 搜索不存在的随机 ID | 显示空态，不崩溃、不沿用上一项详情 |

### B-2：Manual 一次授权与越界再授权

固定输入：

> `UA-B2-A-...`：在本任务工作区的 `manual-scope-a` 新建 `one.txt` 和 `two.txt`，分别写入 `ONE`、`TWO`；然后以这个目录为工作目录，依次运行只读 PowerShell 命令 `Get-ChildItem -Name` 和 `Get-FileHash -Algorithm SHA256 one.txt`。
>
> continuation `UA-B2-B-...`：现在改到同一任务工作区的 `manual-scope-b` 写入 `three.txt`；随后为我安装 manifest 中不存在的测试应用 `UA-B2-NOT-INSTALLED`。如果没有可信来源就停在可恢复错误，不要编造。

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 设置中关闭“Agent 全开模式”，重启 DeskPet 后重新打开设置 | 开关仍为关闭；能力中心显示 `Manual（按需确认）` |
| 2 | 新话题发送 `UA-B2-A` 固定输入 | 第一次副作用前出现简短计划、目录和主要动作；同 task/目录/类别只确认一次 |
| 3 | 真点击允许并等待四个动作 | 四个动作连续完成，不为每个文件/命令重复弹窗 |
| 4 | 同一 root 发送 `UA-B2-B` continuation | 新目录/安装类别产生新的 durable decision；未点击前无对应副作用 |
| 5 | 点击拒绝 | 只拒绝新增范围；已完成文件保留；模型收到结构化拒绝并诚实说明 |

### B-3：Auto 持久化、直行与关闭恢复

固定输入：

> `UA-B3-...`：在当前任务工作区创建 `auto-scope/status.txt` 写入 `AUTO`，运行 PowerShell 读回并计算 SHA-256；从 `https://www.example.com/` 下载到 `auto-scope/example.html`（最大 1 MiB，先取得并使用真实预期 hash）；再启动当前用户的记事本打开 `status.txt`。最后启动一个 task-scoped、foreground awaited 的 180 秒 heartbeat，每秒写一行到 `auto-scope/heartbeat.log`，保持 running 让我测试取消。全部进程保留精确 lease。

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 设置中开启“Agent 全开模式”，关闭并重开 DeskPet | 开关仍开启；能力中心显示 `Auto（直接执行）` |
| 2 | 新话题发送 `UA-B3` 固定输入 | 全部直接进入执行；0 个等待 permission/plan decision；UI 操作卡显示“Auto 已授权” |
| 3 | UI 已显示 heartbeat running 且记录 PID/lease 后点击取消 | Auto 没有关闭取消、Receipt、日志或错误守门；只停止该 task scope 精确进程 |
| 4 | 关闭 Auto 后发送新的写文件请求 | 立即恢复 Manual 授权，不需重启 |

### B-4：UAC 外部等待不是失败

固定输入：

> `UA-B4-...`：读取 `F:\projects\deskpet\plans\2026-07-23-universal-action-and-capability-packs\manual-results-2026-07-24\fixture-manifest.json` 的 `fixtures.uac_wait`。先调用 `external_action_wait` 暂停；`required_action` 必须逐字显示 manifest 的 launcher、marker 和 launch argv，让我从该 argv 启动 probe 并亲自处理 Windows UAC。等我点击“外部操作已处理，检查结果”后，重新读取 completion marker；marker 真实存在且 `elevated=true` 前不要声称成功。

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | Manual 下新 root 发送固定输入 | 先得到 DeskPet 授权；允许后任务进入 `waiting_external`，UI 明确展示 manifest 中的 launch argv 和 Windows UAC 动作 |
| 2 | 暂不操作系统确认，等待 30 秒并查看任务 | TaskGoal/Attempt/provider call 保持；无 FailureSet、无 terminal tool result、无新 plan version、不消耗三次同因预算 |
| 3（B-4A 拒绝） | 按提示启动 probe，在 Secure Desktop 亲自点“否”；回 DeskPet 点“外部操作已处理，检查结果” | 从原 call/checkpoint 继续；marker 仍不存在，模型经真实复查后诚实失败/未完成，不得把“已处理”当“已成功” |
| 4（B-4B 同意） | 用新 root 重做；在 Secure Desktop 亲自点“是”；回 DeskPet 点“外部操作已处理，检查结果” | 从原 call/checkpoint 继续；二次探测看到 marker 存在且 `elevated=true` 后才可声称成功 |
| 5 | 对账两条 root | 两次等待都不产生 FailureSet 或重规划预算；拒绝/同意的最终业务结果由真实 marker 区分 |

## 5. Required 场景矩阵

### S-1：Godot 塔防完整正向

Exact input：

> `UA-S1-...`：帮我做一个能玩的 Godot 塔防小游戏。没装 Godot 的话你自己准备环境，做好后运行起来检查，有问题继续修。

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 0 | 测试前记录机器已有 Godot 的完整路径、版本与来源 | 若已安装，只能验证探测/使用，不能声称本 case 验证了安装；若未安装，记录真实缺失证据 |
| 1 | Auto 关闭，新话题发送 exact input | 同主 Session 创建独立 root，固定 `agent.general`；不要求切模式/重发；Manual 计划列出工作区、环境准备、项目生成与运行验证 |
| 2 | 真点击允许 | 模型先查真实工具/能力目录，发现并使用 Godot 包；缺 Godot 时选择可信当前用户/portable 来源，下载前后有来源与哈希证据 |
| 3 | 观察能力中心“操作”和消息进度 | Godot 包安装/健康检查/激活可见且同 run 刷新；模型继续原请求，不创建第二个顶层 root |
| 4 | 等待项目生成与第一次 headless 检查 | `project.godot`、场景与 GDScript 真实存在；保存生成树、源码 hash 与内置 Godot 包模板树的差异，证明不是复制硬编码塔防模板；Godot CLI 退出码/diagnostics 可见 |
| 5 | 若真实检查失败，继续等待模型修复 | 同 TaskGoal 下新 Attempt/或同 plan 成功续做；失败原因与修改策略可见，不无变化重复 |
| 6 | DeskPet 启动真实游戏窗口后截图 | 窗口与项目对应；UI 不把“进程已启动”单独当完成 |
| 7 | 人工在游戏窗口实际游玩：放置两种塔、等待三波、观察金币/生命/伤害、触发胜负或重新开始 | 一条敌人路径可见；两种塔都可放置且行为可区分；三波递进；金币、生命、伤害、消灭与终局逻辑成立 |
| 8 | 关闭后从交付项目再次打开运行 | 可再次运行且无解析/启动错误；项目 Artifact、运行截图、CLI Receipt、最终 terminal 各一次 |

### S-2：Blender 低多边形小屋（非 Godot 通用性）

Exact input：

> `UA-S2-...`：帮我装好 Blender，做一个低多边形的小屋场景，配好材质、灯光和相机，再渲染一张图给我。

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 0 | 测试前记录机器已有 Blender 的完整路径、版本与来源 | 若已安装，只能验证探测/使用，不能声称本 case 验证了安装 |
| 1 | 开启 Auto 并确认持久化后，在全新话题发送 exact input | 新 root=`agent.general`；0 个 DeskPet permission/plan waiting；不依赖 Godot 专用工具 |
| 2 | 等待真实 Blender 探测与环境准备 | 若缺失，选择可信当前用户/portable 安装并二次探测；若系统只给管理员安装则按 B-4 等待，不绕 UAC |
| 3 | 观察项目/脚本/应用执行 | 通用文件、Shell、应用控制或 Blender 脚本 API 构建场景；过程 Receipt 可见但敏感参数不投影 |
| 4 | 打开 `.blend` 与渲染图 | `.blend` 非空可再次打开；渲染图非空且不是占位图 |
| 5 | 人工视觉检查 | 主体是可辨识低多边形小屋；至少两种材质；主体完整在画面内；另存 Blender Outliner 与材质、灯光、相机对象截图，不能只凭最终渲染图判定结构 |

### S-3：待办 Web 应用与真实浏览器操作

Exact input：

> `UA-S3-...`：帮我做一个简单好看的待办网页，能新增、完成和删除，刷新后内容还在。做好后自己打开浏览器检查。

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | Auto 开启，新话题发送 exact input | 新 root=`agent.general`；无需专用 Web/Godot/Blender 包；通用文件、Shell 与浏览器能力可发现 |
| 2 | 等待项目和依赖准备 | 源码落在独立 workspace；安装/启动命令有 exit code；后台服务有 task-scoped owner 和取消入口 |
| 3 | DeskPet 打开真实浏览器后截图 | 页面无空白、明显溢出或致命控制台错误；显示输入区与待办列表 |
| 4 | 在真实浏览器新增 `UA-S3-A`、`UA-S3-B` 两项 | 两项都出现，文字准确 |
| 5 | 完成 A，删除 B | A 有明确完成态，B 消失 |
| 6 | 刷新浏览器 | A 仍存在且完成态保持，B 不恢复 |
| 7 | 关闭服务后按交付说明重新启动并打开 | 可重复启动；源码 Artifact 与说明可用；取消/收尾后端口和精确进程树归零 |

### S-4：UltraForge 损坏能力包拒绝

Exact input：

> `UA-S4-...`：帮我用 UltraForge 做一个可以直接打开的机械零件模型，缺什么工具你自己装。

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 从 manifest 逐字注入 `DESKPET_CAPABILITY_SOURCES_JSON` 与 `DESKPET_ULTRAFORGE_CANARY`，确认 canary 不存在；Manual 下发送 exact input | 模型通过真实能力搜索找到同名包，安装纳入当前任务授权 |
| 2 | 真点击允许 | host 在激活/运行入口前校验 manifest 与全部声明文件哈希 |
| 3 | 等待终态并查看能力中心操作卡 | 任务 blocked/failed，明确 `hash_mismatch`/integrity 错误与可恢复选择；不得退化为泛泛教程 |
| 4 | 只读核对 canary、绑定与目录 | canary 仍不存在且 hash 前态未变化；无 active binding、无“已安装但不可用”半状态；原工作区无伪造模型 |
| 5 | 重试同一安装请求 | 幂等重试不重复创建版本/操作副作用；仍诚实失败，可换来源或让用户提供本地包 |

### S-5：照片重命名能力自动自建并立即使用

Exact input：

> `UA-S5-...`：把这个测试目录里的照片按拍摄日期统一重命名，以后我还会经常用；如果没有现成能力，你自己做一个可复用工具，验证好后现在就用。

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 从 fixture manifest 记录 `UA-PHOTOS` 正向/错误输入全目录 hash；Manual 下新话题发送 exact input 并附 `input_dir` | 必须先出现 durable `capability_search` Receipt，含合法当前 `catalog_stamp` 与 `search_receipt_ref`，且 `matches` 中不存在达到 Builder 阈值的 `executable=true` 项；随后 Builder admission 成功、真实调用 `capability_build` 并由 `workflow_spawn` 选择 `workflow.capability_build`；直接用一次性 PowerShell/Python 改名判 FAIL |
| 2 | 允许生成/安装计划 | staging 中产生 manifest、closed tool schema、入口代码、依赖、权限/effect、healthcheck 与测试；不修改 `backend/deskpet/tools/*.py` |
| 3 | 观察验证阶段 | 至少执行真实 happy-path、对 manifest 的 `invalid_input_dir` 执行错误输入、healthcheck 与输出路径/副作用核验；错误输入目录的两个 hash 完全不变；验证前不进入生产目录 |
| 4 | 观察发布与 catalog refresh | 选择 user scope（“以后经常用”）；不可变版本原子激活；当前 root 重新冻结 ToolSet，schema/capability/grant fingerprint 更新 |
| 5 | 不发送第二条用户消息，等待继续 | host/模型在同 root 调用新注册工具；照片按 EXIF 日期确定性改名；无 EXIF 图按明确策略处理，非图片不变 |
| 6 | 在同一 root 让模型再次检查结果，不重发原始任务 | 已处理文件不二次改名、不重复安装同版本；无 EXIF 与非图片策略和 manifest 一致 |
| 7 | 重启 DeskPet，在**新 root**用自然口语“把这批相片按拍摄时间整理一下” | 能力仍可发现并复用；不重新生成；证明复用来自 user-scope active binding 而不是旧上下文记忆；旧版本和 lineage 可审计 |

### S-6：Godot 首次失败后同模型重规划

使用 S-1 exact input，唯一场景标记改为 `UA-S6-...`。S-6 开始前：

1. 只停止本轮端口、用户数据目录和命令行对应的隔离 Tauri 进程树；
2. 从 fixture manifest 取得精确 fail-once marker，验证它不存在；若存在，只有在
   隔离进程树已归零后才可用准备器
   `--reset-fail-once --reset-only` 删除这个文件；
3. 保持同一个隔离 user-data/workspace，重新启动唯一 Tauri，并额外设置
   `DESKPET_CAPABILITY_E2E_CASE_ID=UA-GODOT-FAIL-ONCE`；
4. 明确从 DeskPet InputBar 点击“新话题”并发送 S-1 exact input，不得协议注入。

S-6 启动命令：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\plans\2026-07-23-universal-action-and-capability-packs\manual-results-2026-07-24\run-isolated-tauri.ps1 `
  -ScenarioBatch S6
```

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 记录项目目录不存在和 marker 不存在；发送 exact input | 新 root/TaskGoal/Attempt 1；parent profile=`agent.general`；真实开始项目与 Godot 执行 |
| 2 | 等待第一次真实 headless 启动失败 | canonical tool result 为 `godot_executable_not_found`；FailureReport 绑定 call/effect/evidence；已生成项目保留；marker 出现并记录 hash |
| 3 | 观察下一模型轮次 | root、parent run/profile、provider/model snapshot 均不变；同一父模型看到原目标、checkpoint、完整 failure set 与历史策略；Attempt N+1 的 `trigger_failure_set` 指向 N，创建新 plan version并基于新证据重新探测/改路径，而不是原样重试或创建新顶层 root |
| 4 | 等待第二次真实执行 | 修复证据可以是同一路径状态更新或新路径；以原 canonical 参数经新 prepare/effect 重试，`retry_of_effect_id` 指向旧 effect，新 fingerprint/grant 生效 |
| 5 | 真机游玩并检查项目目录 | 达到 S-1 质量线；第一次失败后没有从头覆盖项目；旧失败 child/Receipt/Attempt 保留，新 Attempt 有 trigger/supersedes |
| 6 | 对账传输与循环守门 | host effect/child/backfill 恰好一次；provider transport 只有真实支持稳定幂等键时标 exactly-once，否则标 at-least-once；同因最多 3 次，超限诚实停止 |

“failure set 已提交、下一 Attempt 尚未派发”等不可稳定人工卡住的崩溃窗口不做
竞速点击；它们由 §7 指定的 fault-injection node IDs 证明。手工 S-6 只验证真实
failure → same-parent-model replan → 成功。

## 6. 并行、取消、恢复与隐私

### B-5：单主 Session 下三个并行顶层 Run

三个固定输入：

> Root A `UA-B5-A-...`：在当前 root 的 workspace 创建 `a.txt` 写入 `UA-B5-A` 并计算 SHA-256；再启动一个 task-scoped、foreground awaited 的 180 秒 heartbeat，每秒只向 `a-heartbeat.log` 追加一行，保持本 root nonterminal 等待我取消。
>
> Root B `UA-B5-B-...`：启动一个 task-scoped、foreground awaited 的 180 秒 heartbeat，每秒只向本 root workspace 的 `heartbeat.log` 追加 `UA-B5-B:<second>`，保持本 root nonterminal 等待我取消。
>
> Root C `UA-B5-C-...`：在本 root workspace 创建只显示 `UA-B5-C` 的小网页，使用当前隔离实例分配的空闲端口以前台受管进程启动；等待该进程至少 180 秒或直到我取消，保持本 root nonterminal。

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 发送 Root A，等 UI 显示 heartbeat running 后点“新话题”；Root B 同样确认 running 后再建 Root C | 三个 root 同时 nonterminal/active；每个有独立 task_scope/workspace/Attempt/进程树/Artifact |
| 2 | 在 A/B/C 均仍显示 running 时切换投影，每个 root 各发送一次只包含自身 sentinel 的 continuation | 三次都返回 `continuation_queued`，conversation boundary version 各自只增 1，root 总数仍为 3；当前动作边界结束后由同一父模型按 FIFO 继续。每个 provider history 对另外两个 sentinel 命中数均为 0 |
| 3 | 只在 A/B/C 已存在的任务投影间切换，不点击“关闭” | 切换只改变当前可见投影；不取消、不重路由、不改 Driver/工具集/授权。当前产品没有已关闭任务的 UI reopen 入口，本 case 不伪造该能力 |
| 4 | 记录 B 的精确 PID/parent/command line/lease 后只取消 B | 只清理 B 的精确进程树；A heartbeat/hash 与 C 服务状态不变 |
| 5 | 在 A/C 仍 nonterminal 时重启；重启后从现有任务投影打开 A/B/C 并核对 root ID | 终态/恢复中状态与 Artifact 各归原 root，无串线、重复 child 或重复 terminal |

### B-6：敏感投影与资源回收

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | 查看 S-1～S-6 的工具轨迹、能力操作卡与 Artifact | 显示阶段、能力/能力包、授权/auto、最近结果、取消与产物；不显示密钥、完整内部 schema、完整敏感命令参数或文件内容 |
| 2 | 结束/取消所有任务并关闭 DeskPet | 能力 worker 按需回收；本测试进程树与端口归零；不结束用户原有同名应用 |
| 3 | 检查日志、manifest、Artifact | 不含明文凭据；路径含中文和空格的 S-5 仍成功 |

### B-7：模型选择 Profile 与一次性启动票据

固定输入：

> `UA-B7-...`：请启动一个可恢复的深度调研子任务，比较 Godot 4 与当前 Blender 在制作低多边形塔防素材上的分工，输出带来源的简短结论；主任务等待子任务结果后再总结。

| 步骤 | 真人操作 | 预期结果 |
|---|---|---|
| 1 | Manual 下从新话题发送固定输入并允许 | root 固定 `agent.general`；模型通过真实 `workflow_spawn` 选择当前 catalog 中合法的 research profile，host 不用正则或领域关键词改写 |
| 2 | 等待 child 创建后只读对账最终 ticket | ticket 有 `issued_at`、当前为 consumed，并绑定 root/parent/task/Attempt/provider turn/call/profile/driver/generation/snapshot/grant/fingerprint；不要求真人捕捉瞬态 issued 状态 |
| 3 | 核对 child/link/Driver | ticket CAS、child command、child Run、link 与 snapshot hook 已原子提交；RunKernel 只使用 ticket 冻结的 Driver |
| 4 | 等待父任务总结 | child terminal 回填原 provider call，父 root 继续 |

同一 fenced call replay、issued 瞬态、payload/route/request tamper、stale generation
与 CAS 崩溃窗口不能从真人 UI 稳定制造，明确由 §7 的
`test_workflow_spawn_is_dynamic_prepared_control` 等精确节点覆盖，不冒充真人步骤。

## 7. 自动化回归登记

以下自动化是 manual 场景的契约旁证，不替代 S-1～S-6：

```powershell
.\backend\.venv\Scripts\python.exe scripts\e2e_capability_platform.py
.\backend\.venv\Scripts\python.exe -m pytest -q backend\tests\capabilities
.\backend\.venv\Scripts\python.exe -m pytest -q backend\tests\harness_simplification
.\backend\.venv\Scripts\python.exe scripts\acceptance\last_mile_smoke.py
node tauri-app\node_modules\vitest\vitest.mjs run
node tauri-app\node_modules\typescript\bin\tsc -b
cargo test --manifest-path tauri-app\src-tauri\Cargo.toml
```

必须另登记以下精确 node IDs 的命令、退出码、时间与报告路径，不能只写“整个目录通过”：

```text
backend/tests/harness_simplification/test_model_workflow_spawn.py::test_workflow_spawn_is_dynamic_prepared_control
backend/tests/harness_simplification/test_model_workflow_spawn.py::test_profile_launch_claim_crash_rolls_back_ticket_child_and_link
backend/tests/harness_simplification/test_model_workflow_spawn.py::test_profile_launch_rejects_payload_route_and_request_tampering
backend/tests/harness_simplification/test_model_workflow_spawn.py::test_pre_atomic_consumed_ticket_is_reconciled_as_frozen_legacy
backend/tests/harness_simplification/test_model_workflow_spawn.py::test_workflow_spawn_rejects_stale_catalog_generation
backend/tests/harness_simplification/test_wi5_react_driver.py::test_external_action_wait_survives_restart_and_resumes_same_attempt
backend/tests/harness_simplification/test_wi5_react_driver.py::test_raw_admission_failure_is_durable_and_replans_in_same_root
backend/tests/harness_simplification/test_wi5_react_driver.py::test_replan_appends_durable_plan_and_links_the_next_attempt
backend/tests/harness_simplification/test_wi5_react_driver.py::test_repair_refresh_automatically_reprepares_original_call
backend/tests/harness_simplification/test_wi5_react_driver.py::test_queued_user_continuation_keeps_a_recoverable_resume_marker
backend/tests/harness_simplification/test_run_kernel.py::test_running_root_queues_continuation_and_supersedes_stale_terminal
backend/tests/harness_simplification/test_run_kernel.py::test_terminal_enqueue_race_retries_fifo_after_run_version_fence
backend/tests/harness_simplification/test_execution_continuations_uow.py::test_running_user_continuations_reserve_fifo_and_survive_restart
backend/tests/harness_simplification/test_execution_schema.py::test_v15_to_current_adds_durable_user_continuation_fifo
backend/tests/harness_simplification/test_durable_task_mode_free.py::test_new_durable_task_never_invokes_legacy_semantic_router
backend/tests/capabilities/test_builder_validation.py::test_builder_admission_uses_current_search_and_managed_staging
backend/tests/capabilities/test_builder_validation.py::test_builder_rejects_sufficient_executable_match
backend/tests/capabilities/test_builder_validation.py::test_builder_completion_is_closed_and_manager_owned
backend/tests/capabilities/test_builder_validation.py::test_generated_pack_installs_and_rehydrates_after_restart
backend/tests/capabilities/test_failure_receipts.py::test_failure_receipt_is_host_validated_and_repair_budget_is_three
backend/tests/capabilities/test_failure_receipts.py::test_repair_admission_derives_parent_args_scope_and_draft
backend/tests/capabilities/test_pack_manager.py::test_update_creates_new_version_and_rollback_switches_pointer
backend/tests/capabilities/test_local_tool_runtime.py::test_nonzero_exit_is_classified_as_crash
backend/tests/capabilities/test_local_tool_runtime.py::test_timeout_cleans_only_observed_lease_tree
backend/tests/capabilities/test_local_tool_runtime.py::test_cancel_releases_runtime_lease
backend/tests/capabilities/test_universal_action_fixture_preparer.py::test_preparer_emits_real_hashed_fixtures_and_launch_environment
backend/tests/capabilities/test_universal_action_fixture_preparer.py::test_preparer_is_idempotent_until_a_fixture_is_contaminated
backend/tests/capabilities/test_universal_action_fixture_preparer.py::test_preparer_rejects_photo_outputs_or_stale_user_data
backend/tests/capabilities/test_universal_action_fixture_preparer.py::test_fail_once_reset_is_explicit_and_exact
```

重点幂等断言必须覆盖：

- repeated platform initialize / install / update 不重复注册或安装；
- control call replay 不重复消耗 repair 三次预算；
- refresh/重启后 effect、child、provider backfill 不重复；
- running root 的 queued continuation 不新建 root；terminal/入队竞态由 Run CAS 重仲裁，
  bound 后崩溃由 `pending_resume_signal` 恢复且消息历史以 stable event id 去重；
- S-4 失败不留下 active 半状态；
- S-5 重复遍历不二次改名，失败项仍可重试；
- Capability Center 重进不发起 mutation；
- Auto 设置重启持久化，但只跳过 DeskPet 授权，不跳过验证/UAC。

## 8. AC 可追溯矩阵

| AC | testcase / scenario | 必须证据 |
|---|---|---|
| AC-1～AC-3 | VS-1、S-1、S-5 | agent.general、PreparedToolSet/search、same-root refresh |
| AC-4 | VS-2、S-1～S-3 | 文件/Shell/进程/下载/浏览器/桌面 effect + Receipt |
| AC-5 | B-2、S-1、S-5 | durable manual decisions 与范围缓存 |
| AC-6 | B-3、S-2、S-3 | 0 waiting decision、持久化、关闭即恢复 |
| AC-7～AC-8 | S-1、S-2、B-4 | 探测/安装/二次探测、waiting_external |
| AC-9～AC-11 | B-1、S-4 | manifest/hash/lifecycle/幂等/无半状态 |
| AC-12 | S-1 | Godot adapter + 非模板项目 + CLI/GUI 规则 |
| AC-13～AC-14 | S-1～S-3 | 同 root 跨工具链、Receipt 与真实运行证据 |
| AC-15～AC-16 | S-6、B-5 | failure set、新 Attempt、restart/cancel/exactly-once 边界 |
| AC-17 | B-1、B-6、S-1～S-6 | 用户可见进度、授权、取消、产物、脱敏 |
| AC-18 | VS-1、B-5、自动化全回归 | 默认 ON、无生产 Code 路由、旧冻结迁移 |
| AC-19～AC-21 | S-1、S-2、S-3 | 三类真实业务质量线 |
| AC-22 | S-4、S-6 超限分支 | 诚实阻塞与恢复入口 |
| AC-23～AC-25 | S-5 | Builder 草案/验证/scope/恢复/卸载 |
| AC-26～AC-29 | S-5、S-6、自动化 repair | 不可变派生、原子切换、refresh/retry、子进程隔离 |
| AC-30 | B-5 | 三并行 root、历史零串线、单独取消 |
| AC-31 | S-1～S-3、B-5、B-7 | 模型 workflow_spawn、durable ticket、Kernel ticket binding |
| AC-32 | S-6 | Attempt=batch、failure set、trigger/supersedes、恢复不重复 |
| AC-33 | B-5 | running-root conversation reservation、durable FIFO、React resume marker、取消结算与重启恢复 |

## 9. 输入广度与语义等价账本

真正 distinct 的 required 输入类别为 6：

1. S-1 游戏开发 / 陌生应用 / 专用能力包；
2. S-2 3D 内容创作 / 通用桌面软件；
3. S-3 Web 开发 / 浏览器交互；
4. S-4 不可信扩展 / integrity 负向；
5. S-5 无现成能力 / 自动自建可复用工具；
6. S-6 首次执行失败 / 模型恢复。

S-6 使用 S-1 的核心输入，不计新的业务领域类别，只计独立 recovery 风险类。S-5 重启后的自然口语是等价改写，只证复用和路由，不增加 distinct 数。B-1～B-7 是确定性 UI/生命周期边界，不冒充输入语义类别。

正向价值样本：S-1、S-2、S-3、S-5，共 4 个；负向安全样本：S-4；恢复样本：S-6。

## 10. 结果账本（执行时回写）

| scenario | root run | retry | continuation | engine 终态 | 业务终态 | quality_bar | 证据 | 状态 |
|---|---|---:|---:|---|---|---|---|---|
| VS-1 | `4eeb23a0d3f05fb38a32e1d8e7727054` | 0 | 0 | completed | completed | 工具事实接地 | sentinel `UA-VS1-A7-20260724T143200Z`；task `task-d6bbaa25f2a417b8e14e3d24c904d393` | PASS |
| VS-2 | `74e21a97b26852b3a22047b7fb2eb37a` | 1 | 0 | completed | completed | 文件+Shell 真闭环 | 27-byte `hello.txt`；PowerShell + Git Bash；task `task-e3112209099f5cbbab21c2e297e90e2f` | PASS |
| B-1 | — | 0 | 0 | — | — | 能力中心真实投影 | — | PENDING |
| B-2 | `4eeb23a0d3f05fb38a32e1d8e7727054` | 0 | 0 | completed | partial | Manual 范围授权 | 真点击“允许一次”并复用同 task grant；新目录/安装未测 | PARTIAL |
| B-3 | `74e21a97b26852b3a22047b7fb2eb37a` | 1 | 0 | completed | partial | Auto 直行/取消/关闭恢复 | Auto 持久化与 0 等待授权已测；heartbeat 取消/重启未测 | PARTIAL |
| B-4A | — | 0 | 0 | — | — | UAC 拒绝后真实复查 | — | PENDING |
| B-4B | — | 0 | 0 | — | — | UAC 同意后真实复查 | — | PENDING |
| B-5 | — | 0 | 3 | — | — | 三 root 并行隔离 | — | PENDING |
| B-6 | — | 0 | 0 | completed | partial | 脱敏与精确清理 | 13 PID 精确树、survivor=0、释放 8533.9 MiB；完整隐私矩阵未测 | PARTIAL |
| B-7 | — | 0 | 0 | — | — | 模型 profile + ticket binding | — | PENDING |
| S-1 | — | 0 | 0 | — | — | 可玩塔防 | — | PENDING |
| S-2 | — | 0 | 0 | — | — | 可打开 blend + 合格渲染 | — | PENDING |
| S-3 | — | 0 | 0 | — | — | 浏览器 CRUD + 刷新持久 | — | PENDING |
| S-4 | — | 0 | 0 | — | — | integrity fail-closed | — | PENDING |
| S-5 | — | 0 | 1 | — | — | 自建并立即/重启后复用 | — | PENDING |
| S-6 | — | 1+ | 0 | — | — | failure→replan→成功 | — | PENDING |

执行前所有 PENDING 是正常的；一旦开始 full-audit，任一 required 行仍为 PENDING/PARTIAL/NOT RUN 都使总门 FAIL。
