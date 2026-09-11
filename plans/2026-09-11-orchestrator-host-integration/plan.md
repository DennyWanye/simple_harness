# Agent 编排框架接入 Host（产品接线）· 计划

- 日期：2026-09-11
- 状态：**第 3 版**。
  - 第 2 版处置了 plan review 第 1 轮（`reports/plan-review-round1.md`，处置表在 `journal.md` §1）。评审原话："改完 P0 和 P1-1 到 P1-4 后可以进入实现，不需要再做一轮完整评审。"
  - 第 3 版按用户 2026-09-11 22:39 放入的 Phase3 计划对齐，见下面 §0.1。

## 0.1 与用户 Phase3 计划（P3-v1.0）P3.1 的关系

用户的 `plans/taskSys2/agent-orchestrator-phase3-plan.zh-CN.md` 把"真实 App Mission 控制闭环"列为 P3.1，并写明"P3.1 当前 UI 工作直接并入，不另建一套控制台""先完成 P3.1"。本计划就是 **P3.1 的 Host 直连路径实现**：Host 在进程内直连 SDK，按 P3.1"只选实际主路径"的要求，不再经 Service SDK 绕一圈。两者不一致的地方以 Phase3 计划为准。第 3 版补齐的内容如下：

| P3.1 要求 | 本计划落点 |
|---|---|
| §3.3 SDK Facade 严格映射请求字段；未知字段拒绝，暂不开放的字段明确报错 | SDK 切片 S2（0.9.9），`api/facade.py` |
| §3.3 `api/read_models.py`：带 through_seq / graph_version，按归属过滤 | S2：`MissionControlV1.snapshot/events`，由 `Store.read_view()` 保证一致性读取 |
| 外部操作 `mission.create/snapshot/events/cancel`、`artifact.read`、`approval.decide`、`human.comment` | §3.4 协议：补上 `mission_artifact_read`；快照带 through_seq |
| §3.4 Principal 来自 Host；按归属检查读写，不泄露对象是否存在 | S2 按 tenant 检查归属；Host 的 Principal 见 §3.6 |
| §3.4 事件页有上限；UI 按 seq 去重，发现缺口就重新取快照 | §3.4 与前端 store |
| §3.4 UI 状态词汇：已接收 / 排队 / 运行 / 待验证 / 待人 / UNKNOWN / 正式交付 | §3.9 |
| §3.3 Host 启动时写 `DeploymentManifestV1` | §3.11（新增） |
| §3.1 P3.2 通过之前，不让任意生成的代码在带真实凭证的 Host 进程里执行 | 去掉 `allow_local_tests` 选项，Host 部署固定 `local_code_execution=False`（§3.5） |
| §3.5 分开记录"UI 重连成功"与"后端进程重启后恢复成功"；没有安装版证据时，最多标"SDK 已就绪，Host 待验证" | 验收 HA-12 与 HA-22（新增） |
| §11 P3.1-A01 至 A08 | 验收 §E 对照表 |
- Host 基线：`simple_harness` main `49466560`（钉 SDK 0.8.0）
- SDK 基线：`simple-harness-sdk` main `ae8f0ec`（0.9.7 / agent_orchestrator 0.9.0，wheel 源 `88e5582`）。本计划先在 SDK 上做 **0.9.8 / agent_orchestrator 0.9.1**（切片 S1），再做 **0.9.9 / agent_orchestrator 0.9.2**（切片 S2：P3.1 外部控制面），Host 钉 0.9.9。
- 依据：
  - SDK `plans/2026-09-11-agent-orchestrator/HANDOFF.md` §2 第 1 项（Host 产品接线）；
  - 编排纲要 `plans/taskSys2/agent-orchestrator-incremental-build-plan-phase2-zh-CN.md`（ORCH-BUILD-v1.0）第 15 行："进入产品 UI/Host 接线时，应把真实 Host commit 和装配位置登记到实施记录中"；
  - 原文 `plans/taskSys2/agent-orchestration-layer-complete-design.md` §5、§12、§14.1、§16.4、§17.6、§21、§22、§23；
  - 术语以 `plans/taskSys2/agent-orchestration-theory/` 为准。
- 编排方式：不用 plan-test skill。测试先行，按切片提交并推送；回归红集 ⊆ 基线；独立代码评审；真实模型证据只用 deepseek-flash；原生 App 真实 UI 验收；中文记录。每个切片提交时同步更新 `journal.md` §0.1（handoff）。

## 0. 术语（按定义使用）

| 术语 | 定义（出处） | 本次落点 |
|---|---|---|
| Mission | 一次完整运行的任务章程：目标、成功条件、预算、允许工具、风险等级、停止条件（原文 §5） | 用户在"任务编排"视图里新建；Host 只经 SDK 的编排入口创建（§3.1 S1-b） |
| Task / Task DAG | 可检查的契约与依赖图（原文 §6） | Planner 生成；Host 只读 |
| Attempt | 对 Task 的一次具体尝试，只有一个执行者（原文 §12.1） | Host 只读，连同逐层验证结果 |
| Verifier 分层 | 格式 → 规则 → Critic → 测试 → 形式化 → 人工；不需要的层记 NOT_REQUIRED，不伪造 PASS（原文 §14.1） | 详情逐层显示；NOT_REQUIRED 不画成通过 |
| Lease / Heartbeat | 任务租给执行者并有有效期，心跳续租；心跳消失 → LOST（原文 §17.6） | 每个进程一个 owner；重启后靠租约过期接管（§3.3） |
| Durable Execution | 崩溃后从最近可靠步骤恢复（原文 §16.4） | App 退出方式是 SIGKILL（`process_manager.rs:208-221`），恢复只能靠持久状态 + 租约过期，不靠优雅关停 |
| Human-in-the-loop / 审批 | L0 / L1 自动；L2 一次审批；L3 双重审批；人工动作都是 Event（原文 §22） | SDK 三类请求：`review`、`arbitration`、`action`；另有接管（`takeover`：stop / retry_with_note） |
| Principal | 已认证的人类决定者，身份只来自 API 调用方（SDK `governance/permissions.py`） | Host 本机身份（§3.6） |
| 策略版本 | 白名单参数的内容寻址版本；Mission 创建时绑定（第 9 步） | Host 只读 |
| Host 授权模式（auto / manual） | **Host 自己的概念**：只管主对话里 SDK 工具效果要不要逐次确认 | 与 §22 审批分开（§3.7） |

## 1. 目标与非目标

### 1.1 目标

1. 在 App 里打开"任务编排"视图，新建一个 Mission（目标、成功条件每行一条、token 与尝试次数预算），它会自动开始运行。
2. 看到进度：
   - Mission 状态与停止原因；
   - Task（依赖、状态、改图历史）；
   - Attempt 与逐层验证；
   - 事件时间线（增量加载）；
   - 用量（token；金额"未计价"）；
   - 阻塞原因（例如"等待审批""回合结果未知"）。
3. 取消运行中的 Mission。
4. 处理人工审批：批准；拒绝（必须写理由）；复核通过或不通过；仲裁（必须写依据）；接管卡住的 Task（停止或带说明重试）；评论。模型写的文本标注"模型生成，未核实"。
5. 看策略状态（只读）：ACTIVE 版本、待决提议、配置漂移、每个 Mission 绑定的版本。
6. **App 被关闭（SIGKILL）后再打开**：
   - 已完成的 Task 不重跑；
   - 回合已提交的工作在租约过期后（≤ 120 s）继续跑完；
   - 在模型调用中途被杀、结果未知的回合，如实显示"结果未知"，可以接管；
   - 列表与详情不丢。
7. 主对话不受影响：
   - 编排服务启动失败、没有配置模型、或另一个实例占用了编排目录时，主对话照常可用；
   - "任务编排"视图显示"不可用"与原因。

### 1.2 非目标（登记，不在这次做）

- 替换 `deskpet/workflows` 图引擎（workflow 线 Slice 2 仍独立；界面叫"任务编排 / Mission"，不叫 workflow，也不叫 Run）。
- 策略的提议、评测与晋级 UI（CLI 保留；UI 只读）。
- 在产品里开启真实连接器（部署默认不启用；`action:` 条件在门口拒绝，测试场景除外）。
- PyInstaller 打包（`deskpet-backend.spec` 还钉在 0.6.4，另行登记）。
- `run_tests` 的网络和文件系统隔离（SDK 遗留 L2-4）。在隔离交付之前，Host 默认**完全不在本机执行模型写的代码**（§3.1 S1-a、§3.5）。
- 多用户认证；DeepSeek 价目注入（金额记 null）；金额预算（未计价时 SDK 不能执行金额上限，`assembly.py:204-209`，表单不提供）。

## 2. 现状事实（摸底 + 评审核对，2026-09-11）

| 项 | 事实（文件:行） |
|---|---|
| 钉版 | `backend/deskpet/sdk_adapters/sdk_candidate.py:23-30`；`backend/pyproject.toml:169,184,193`；`backend/uv.lock:17`；`backend/vendor/*.whl` 与 `.candidate-manifest.json`；校验 `runtime_paths.py:39`（`composition.py:361` 调用） |
| SDK 0.8.0 → 0.9.7 的 `simple_harness` | 只改了 `execution/sqlite/uow.py`（+14 行）和版本号；没有新迁移，执行库仍是 schema 10；wheel 新增顶层包 `agent_orchestrator`，Host 里没有同名模块 |
| Host 装配 SDK | `main.py:7975 _build_product_sdk_runtime_stack`；lifespan `main.py:3304`；`_activate_product_sdk_runtime()` 在 `:10996`，于 `:5670` 调用 |
| 进程与退出 | FastAPI 与 SDK 同一进程、同一事件循环。Tauri 退出时对后端 `child.kill()`（SIGKILL，`lib.rs:239-241`、`process_manager.rs:208-221`），**lifespan 的关停代码不执行**。没有单实例插件，也没有数据目录锁 |
| 数据目录 | `backend/paths.py:220 user_data_dir()`；SDK 执行库 `<user_data>/data/simple-harness-sdk/execution-v6.sqlite3`（`runtime_paths.py:18`） |
| 控制通道 | `ws://127.0.0.1:<port>/ws/control`（`main.py:13928`），信封 `{type, payload}`；前端 `tauri-app/src/ws/ControlChannel.ts`（`send` / `onMessage`），分发在 `code-panel/controlWs.ts`；视图接收 `channel: Pick<ControlChannel,"send"｜"onMessage"> ｜ null`（`views/SkillsView.tsx:20`）；`ControlMessage` 是宽松接口（`types/messages.ts:4`） |
| 服务登记 | `backend/context.py:10 _VALID_SERVICES` |
| Provider | Host 口径：`get_chain()` 的第一个启用项；全新安装时这里会抛异常（`main.py:10985-10991`）；`LLMProviderRegistry`（`config.toml [[llm.providers]]` + 钥匙串） |
| 编排运行时 | `evidence_root` 下放 `orchestrator.db` / `execution.db` / `workspaces/`（`runtime/assembly.py:192-201,320-326`）；`run()` 开头先 `recover()`，空闲才返回，有在途回合时不返回（`event_handler.py:790-812`）；Store 是单连接 + RLock，事务不跨 await，跨任务进入会抛 StoreError（`store.py:367-393`） |
| 本机代码执行 | 是否跑 `code_test`，只看 Task 的 `verification_policy`，而这个策略由 Planner（模型）填写（`role_templates.py:59`）；Manager 和改图的默认策略也含 code_test（`graph/changes.py:146`、`planning/manager.py:30,113`）。没有 `pytest:` 条件时照样对整个验证副本跑 pytest（`deterministic_checks.py:183-184`）。code_test 属于安全边界，不能消融（`assembly.py:57-69`）。"是否已部署"的判定点有三处：`task_graph.py:265`、`changes.py:514`、`commit_service.py:753` |
| 创建入口 | `MissionApi.create` 不检查动作条件，也不传 provider_kind / policy_defaults / policy_pin（`missions.py:51-70` 对照 `event_handler.py:734-742`；provider_kind 默认记为 `"unknown"`） |
| owner 与租约 | SDK 把同一 owner 名下的有效租约都当成自己的（`commit_service.py:2335-2341,2466`；`simple_harness tools/executor.py:329`）；设计上每个实例一个 owner（`event_handler.py:219-221`）；换 owner 后等租约过期接管，已有测试证明（`step03/test_multi_scheduler.py:82-150`） |
| 阻塞回合 | 进程在 provider 调用中途被杀时，SDK 回合为 UNKNOWN 并阻塞；阻塞回合不触发停滞超时（`event_handler.py:1420`） |

## 3. 设计

### 3.1 SDK 切片 S1：0.9.8 / agent_orchestrator 0.9.1（在 SDK 仓库）

**S1-a 本机代码执行开关（处置 P0-1）**

- `DeploymentPolicy.local_code_execution: bool = True`。SDK 默认值不变，CLI 与既有测试的行为也不变。
- 为 False 时：
  1. 已部署验证层 = `STEP2_IMPLEMENTED_LAYERS − {code_test}`，由新函数 `deployed_layers(deployment)` 计算。三个判定点（初始图、改图、Commit 提议）都改用它，含 code_test 的 Task 按现有的 `verification_policy_undeployed` 拒绝，并写明原因。
  2. Manager 与改图的默认策略、冲突 Task 的 `CONFLICT_POLICY` 都去掉 code_test；Planner 和 Manager 的角色模板里也不再列出 code_test。
  3. 带 `pytest:` 成功条件的 Mission 在创建时拒绝。
  4. `allowed_tools` 里有 `run_tests` 时，`DeploymentPolicy.__post_init__` 报错。
- 要证明的一件事：code_test 关闭后，冲突仲裁不能靠测试证据，必须转人工仲裁（`request_arbitration`），不能死锁。

**S1-b 编排感知的创建入口（处置 P1-1）**

- 新增 `Orchestrator.create_mission(*, tenant_id, request) -> (Mission, created)`，一步完成：
  - 请求解析；
  - `validate_spec`：工具用部署政策的允许集；
  - 动作条件检查：沿用 `_check_action_criteria`；
  - 本机代码执行检查；
  - 传入 provider_kind / policy_defaults / policy_pin。
- 它与 `submit_mission` 共用同一段实现。
- `MissionApi` 增加可选参数 `orchestrator=`；传入时走这条入口。CLI `mission create` 也改走这条入口。

**S1 的交付要求**：测试先行（`tests/orchestrator/host_support/`）；SDK 全量回归红集 ⊆ 73 条基线；wheel 在干净 venv 中验证；独立代码评审；SDK 仓库记录 `plans/2026-09-11-agent-orchestrator/host-support-0.9.8/journal.md`；推送。这个切片只改部署政策与校验，不涉及模型行为，真实模型证据放到 Host 的 HA-11 一起取。

### 3.2 钉版 0.9.9（切片 H1）

1. 按 HANDOFF §6 的方法，从 S2 的提交可复现地构建 wheel，复制到 `backend/vendor/`，并写 `simple_harness_sdk-0.9.9.candidate-manifest.json`（`execution_schema: 10`）。
2. 改 `sdk_candidate.py` 与 `pyproject.toml` 三处，然后执行 `uv cache clean simple-harness-sdk` → `uv lock` → `uv sync --extra dev`。
3. 删除 0.8.0 的 wheel 与 manifest（删之前 grep 确认没有其他引用）。
4. 新建 installed target `.local-test-evidence/2026-09-11/installed-h099-s0313`。
5. 启动迁移不变（v9、v10）。

### 3.3 后端编排服务 `backend/deskpet/orchestration/`（切片 H2）

| 模块 | 职责 |
|---|---|
| `paths.py` | 正式目录 `<user_data>/data/agent-orchestrator/`，测试场景目录 `<user_data>/data/agent-orchestrator-test/`；复用 `runtime_paths` 的软链与越界检查；与 `execution-v6.sqlite3` 分开（D1） |
| `lock.py` | 编排目录上的 `fcntl.flock(LOCK_EX｜LOCK_NB)` 锁文件 `.instance.lock`；拿不到锁就把服务标为 `unavailable("另一个实例正在使用编排目录")`，不派发任何任务（P0-2、HA-16）；SIGKILL 后由内核自动释放 |
| `settings.py` | `config.toml [orchestration]`，见下表 |
| `provider.py` | 启动时对 Host provider 做快照（`get_chain()` 第一个启用项，密钥从钥匙串或既有来源取）；解析失败 → `unavailable("未配置模型")`（HA-17）；模型 id 校验见下文；构造 SDK `OpenAICompatibleProvider(httpx.AsyncClient(), base_url, model, Secret(key))`；`price_table=None` |
| `service.py` | `OrchestrationService` |
| `projection.py` | 快照、事件、审批投影为界面 JSON（字段白名单、长度上限、模型文本标注 `source: "model"`、阻塞原因） |
| `pump.py` | `MissionChangePump`：用**只读连接**（`store.py:180`）每 1 s 看一次 `max(seq)` 与各 Mission 状态，有变化就广播 `mission_changed`；每个写入口执行后另外立即推送一次（D7） |
| `handlers.py` | 控制通道协议（§3.4）：纯函数 `handle(service, type, payload)`，任何异常都不抛进 socket 循环 |

**`[orchestration]` 设置**

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | true | 按 CLAUDE.md，测试阶段完成的能力默认开启 |
| `max_concurrency` / `max_concurrent_model_calls` | 1 / 1 | 与主对话共用 provider 限额。**只在编排库第一次 seed 时进入 ACTIVE 策略**；之后改配置只会记一条 `PolicyConfigDrift`，不生效，除非走策略晋级（P1-5；设置说明与 ARCHITECTURE 都要写明） |
| （不提供）本机执行测试的开关 | — | 第 3 版按 P3.1 §3.1 去掉：P3.2 的隔离执行通过之前，不让任何模型生成的代码在带真实凭证的 Host 进程里执行；部署固定为 `local_code_execution=False` |
| `lease_seconds` | 60 | 只供测试缩短；产品不暴露 |

**provider 模型 id**：base_url 是 DeepSeek 官方端点、配置写的是 `deepseek-v4-flash` 时，按 SDK 真实端点的结论改用官方 id `deepseek-flash`（HANDOFF §4），并在 status 里如实写出"配置 id → 请求 id"。其他组合原样使用。换 provider 或模型后重启，只影响新 Attempt；在途回合如果回显不符，由 SDK 记录，界面照实显示（登记为限制）。

**`OrchestrationService` 生命周期**

- **启动**：lifespan 在 `_activate_product_sdk_runtime()` 之后调用 `start()`：
  1. 解析目录，拿锁；
  2. provider 快照；
  3. 构造 `OrchestratorConfig`（Host 部署政策见 §3.5），`owner = f"deskpet-orchestrator-{pid}-{random8}"`（D2 修改）；
  4. 进入 `Orchestrator`；
  5. 登记 `service_context["orchestration"]`（加进 `_VALID_SERVICES`）；
  6. 启动驱动循环与推送。
  任何异常只记进 `startup_errors` 的 `orchestration` 一项，服务标为 unavailable，后端照常 `startup complete`。
- **驱动循环**：`while not closing: await wake.wait(timeout=tick)` → `await orchestrator.run()`。
  - tick：有可推进的工作或在途回合时 2 s；只剩等人（`waiting_on` 非空、没有在途回合）时 20 s；空闲 30 s（P2）。
  - 每个写入口执行后立即唤醒。
  - `run()` 抛异常：记录并指数退避（上限 60 s）；连续 3 次失败就关闭并重建 Orchestrator；连续 5 次失败标为 `degraded(原因)`。
- **写入口**（每个都在 Host 门口先检查，再唤醒并推送）：
  - `create_mission` → `Orchestrator.create_mission(tenant_id="local-desktop", request=门口规整后的请求)`；
  - `cancel_mission`；
  - `decide`（approve / reject / review_pass / review_fail / arbitrate）；
  - `takeover`（stop / retry_with_note，basis 必填）；
  - `comment`。
- **门口检查**：
  - 目标与每条成功条件非空；
  - 成功条件里没有 `pytest:`；没有 `action:`（测试场景除外，P1-1）；请求里没有 Facade 未开放的字段（S2 严格映射）；
  - `allowed_tools` 一律强制为部署政策的允许集，忽略客户端传入的值；
  - 预算只收 `max_tokens` / `max_attempts`；
  - 人写的每段文本都过 `find_secrets(text, extra=(当前 provider 密钥,))`，命中就拒绝（错误信息不回显原文）。
- **读入口**：`status` / `list_missions` / `mission_detail` / `events(after_seq, limit)` / `approvals` / `policy_status`（`PolicyApi.status()`，只读）。
- **并发**：全部在 FastAPI 事件循环里执行；Store 的事务不跨 await。要补一个测试：`run()` 正在等回合时，并发调用 create / approve / cancel（P2）。
- **关停**：`close()` 只在测试和正常 lifespan 结束时执行（先停循环与推送，再 `Orchestrator.__aexit__`，再关 httpx，最后释放锁）。产品的真实退出是 SIGKILL，**正确性不依赖 close()**。
- **重启恢复**：新进程拿到新 owner：
  - `recover()` → 旧 owner 的租约在 `lease_seconds`（SDK 回合租约为它的一半）后过期 → LOST → 新 Attempt；
  - 已完成的 Task 不重跑；每条 `usage_ref` 最多导入一次；
  - 在模型调用中途被杀的回合是 UNKNOWN 且阻塞：详情显示"回合结果未知（进程在模型调用中途退出）"，提供接管按钮（P0-3）。

### 3.4 控制通道协议（切片 H3）

请求带 `request_id`，响应 `<type>_response {request_id, ok, data ｜ error_code, error}`；服务不可用时返回 `orchestration_unavailable`（`orchestration_status` 除外，它总是返回 ok 和状态）。

| 请求 | payload | data |
|---|---|---|
| `orchestration_status` | — | `{available, state, reason, orchestrator_version, sdk_version, model:{configured, requested}, active_missions, pressure, allowed_tools, test_scenario, deployment_manifest}` |
| `mission_create` | `{goal, success_criteria[], budget{max_tokens?, max_attempts?}, idempotency_key}` | `{mission_id, created}` |
| `mission_list` | `{limit?}` | 摘要列表（id、目标前 120 字、状态、停止原因、创建时间、待审批数、是否阻塞） |
| `mission_get` | `{mission_id}` | 投影详情（mission、tasks、attempts、results + 验证层、approvals、waiting_on、blocked、graph_changes、mission_policy、usage） |
| `mission_events` | `{mission_id, after_seq≥0, limit≤200}` | `{events[], last_seq, has_more}` |
| `mission_cancel` | `{mission_id}` | `{status}` |
| `mission_approval_list` | `{mission_id?}` | 待决审批列表 |
| `mission_approval_decide` | `{request_id, decision: approve｜reject｜review_pass｜review_fail｜arbitrate, reason?, note?, ruling?, basis?}` | `{request_state, receipt_hash}` |
| `mission_takeover` | `{task_id, action: stop｜retry_with_note, basis, note?}` | 接管结果 |
| `mission_comment` | `{target_id, text}` | `{comment_id}` |
| `mission_artifact_read` | `{artifact_id}` | `{artifact_id, path, content_hash, size_bytes, content}`：按不可变 id 读取；读前核对 hash；只读文本，有大小上限；归属不符时返回 `not_found`（P3.1 §3.4） |
| `orchestration_policy_status` | — | `PolicyApi.status()` 的只读投影（含漂移） |

- 推送：`mission_changed {mission_id, status, last_seq}`。
- 错误码：`invalid_request`、`not_found`、`local_tests_disabled`、`action_criteria_disabled`、`secret_rejected`、`orchestration_unavailable`、`test_scenario_single_mission`、`conflict`（同一 key 不同内容）、`sdk_refused`（SDK 拒绝，附原因）。
- 协议规定死的细节：
  - reject 必须有 reason；arbitrate 必须有 basis；
  - review 映射为 SDK 的 verdict `pass` / `fail`；
  - 拒绝一个 action 审批后，Mission 结束为 FAILED，停止原因是 `approval_rejected`（沿用 SDK step07 的语义）。
- 没有任何修改策略的消息，由结构测试守着。

### 3.5 Host 部署政策

| 项 | Host 默认 | 理由 |
|---|---|---|
| `local_code_execution` | False（固定） | P3.2 隔离执行交付之前，不在本机执行模型写的任何代码（P0-1、P3.1 §3.1）；不提供打开的开关 |
| `allowed_tools` | 三个工作区工具（没有 `run_tests`） | 同上 |
| `enabled_connectors` | 空（测试场景除外） | 真实动作不在这次范围内 |
| `max_action_level` | L2 | 单机做不到 L3 双人审批；L2 与 SDK 语义一致（`policies.py:36-38`） |
| 编排 Agent 的工具 | 只有工作区工具，拿不到 Host 的任何工具 | 原文 §21.1 |

### 3.6 身份

- `Principal(principal_id="local-user:<load_or_create_local_identity() 的 id>", display="本机用户")`（`companion/identity.py:15-25`）。
- 限制：单机没有认证，身份等于"能操作这台 App 的人"。

### 3.7 与 Host 授权模式的关系

- auto / manual 只管主对话的工具效果授权。编排审批按原文必须由人决定：auto 不会自动批准，manual 也不会弹主对话的授权窗。
- 编排待审批数显示在侧栏"任务编排"的角标上，不混进 `ApprovalCenterPanel`。

### 3.8 仅测试用的场景（处置 P1-2 / D6）

- **生效条件**：环境变量 `DESKPET_ORCHESTRATION_TEST_SCENARIO=approval-action`，**并且**解析出的 user_data 路径位于某个 `.local-test-evidence/` 目录下。不依赖 `DESKPET_DEV_MODE`。
- **生效后**：
  - 使用独立目录 `agent-orchestrator-test/`，正式库一行不写；
  - provider 换成 SDK 的 `demo_approval_action_provider()`；
  - 部署政策 `enabled_connectors=("test_config",)`，同时把 `connectors={"test_config": TestConfigService(<测试目录>/test-services/config.json)}` 传进 `Orchestrator`；
  - 创建时自动加上 `workspace_seed=APPROVAL_SEED`；
  - 放行 `action:` 条件；
  - 只允许一个 Mission：第二个返回 `test_scenario_single_mission`，因为夹具脚本用完会挂起。
- 界面和 `orchestration_status` 都标出"测试场景"。

### 3.9 前端（切片 H4）

- **要改的文件**：
  - `WorkbenchView` 加 `"missions"`；
  - `Sidebar.tsx` 加"任务编排"一项（`Icon.tsx` 加图标；待审批数用角标）；
  - `WorkbenchShell.tsx` 加分支；
  - 新建 `views/MissionsView.tsx` 与 `stores/missionsStore.ts`；
  - 新消息类型直接用宽松的 `ControlMessage` 发出；响应在视图里按 `type` 过滤；
  - 修改 `WorkbenchShell.test.tsx`。
- **布局**：
  - 左列：Mission 列表（状态徽标、待审批、阻塞标记）、"新建 Mission"、策略小卡（只读，含漂移提示）、"测试场景"横幅。
  - 右侧：新建表单（目标、成功条件每行一条、token 与尝试次数），或 Mission 详情。
  - 详情内容：
    - 状态头、停止原因、阻塞原因；
    - "取消 Mission"；
    - Task 与改图记录；
    - Attempt 与逐层验证（NOT_REQUIRED 用中性色）；
    - 审批卡（批准 / 拒绝 + 理由 / 复核通过 / 复核不通过 / 仲裁 + 依据）；
    - 接管（停止 / 带说明重试 + 依据）；
    - 事件时间线（最近 50 条，可"加载更多事件"）；
    - 用量（token；金额"未计价"）。
- **状态**：不可用（原因）、空态、加载、错误（错误码的中文说明）。
- **AX 名称**（原生验收用）：`任务编排`、`新建 Mission`、`Mission 目标`、`成功条件`、`提交 Mission`、`取消 Mission`、`批准`、`拒绝`、`拒绝理由`、`复核通过`、`复核不通过`、`仲裁依据`、`接管：停止`、`接管：重试`、`加载更多事件`。"提交 Mission"变为可点，作为 AX 设值后 React 状态已更新的判据。
- **样式**：沿用现有 CSS 变量令牌，不引入新依赖。

### 3.10 文档（与交付同一批提交）

- 新建 `ARCHITECTURE/AGENT_ORCHESTRATION.md`（装配位置、数据目录与锁、协议、部署政策、身份、SIGKILL 下的恢复语义、P1-5 设置语义、限制）。
- 更新 `ARCHITECTURE/index.md`；`PROJECT_STATUS.md` 加钉版行与能力行。
- 修正 `AGENT_HARNESS.md` 第 1 行的版本漂移（写的是 0.7.10）。
- 按 ORCH-BUILD 第 15 行，把 Host 提交与装配位置登记进 `journal.md` §3。

### 3.11 部署清单 `DeploymentManifestV1`（P3.1 §3.3，P3.1-A08）

服务启动时写入 `<编排目录>/deployment-manifest.json`（每次启动覆盖），同时放进 `orchestration_status.deployment_manifest`。不包含任何密钥。字段如下：

- `host_commit`：打包或启动时的 Host 提交；拿不到就写 `unknown`，不伪造。
- `distributions`：`simple_harness`、`agent_orchestrator` 两个包的实际导入路径（`module.__file__`）、`importlib.metadata` 版本，以及 `sdk_candidate.py` 里的 wheel sha。版本与钉版不一致时，服务标为 unavailable。这样确认的是"实际导入了哪个版本"，不靠 PYTHONPATH 或旧状态文件。
- `schemas`：编排库的 schema 版本，以及 SDK 执行库的 schema 版本。
- `features`：生效的部署政策（`to_json`）、测试场景，以及并发设置。
- `model`：provider id、配置的模型 id 与实际请求的模型 id、价目（`unpriced`）。

## 4. 切片与提交顺序

| 片 | 仓库 | 内容 | 门槛 |
|---|---|---|---|
| H0 | Host | 本目录文档、评审报告、ORCH-BUILD 纲要入库 | 评审意见已处置 |
| S1 | SDK | 0.9.8 / 0.9.1：本机代码执行开关、编排感知的创建入口 | SA-1 至 SA-7；SDK 全量回归 ⊆ 基线；wheel 在干净 venv 中验证；独立代码评审；推送 |
| S2 | SDK | 0.9.9 / 0.9.2：P3.1 外部控制面（`api/facade.py`、`Store.read_view()`）；包含 S1 代码评审的修改 | SB-1 至 SB-6；全量回归 ⊆ 基线；wheel；代码评审；推送 |
| H1 | Host | 钉 0.9.9 | HA-1；§5 回归 ⊆ 基线；启动冒烟 |
| H2 | Host | 服务、锁、provider、设置、lifespan、`_VALID_SERVICES` | HA-2、3、5、8、9、14、15、16、17、18、20 的后端测试 |
| H3 | Host | 协议、推送、投影 | HA-4、6、7、19 的契约测试 |
| H4 | Host | 前端 | HA-10 |
| H5 | Host | 独立代码评审、真实 flash、bundle、原生 AX 验收、ARCHITECTURE、journal 终态 | HA-11、12、13 |

## 5. 回归口径

- **基线**：在 `49466560`、改动前取，见 `baseline.md`：
  - 后端 `tests/sdk_adapters tests/execution tests/permissions`：69 条红；
  - 控制通道、启动、context 相关 5 个文件：全绿；
  - 前端：typecheck 通过，lint 161 个问题（签名见 `baseline-frontend-lint.tsv`），vitest 723 条全过。
- **每个切片之后**：同一套命令再加上 `tests/orchestration`。失败集合必须 ⊆ 基线；lint 不得出现新签名；新测试全绿。
- **SDK 侧**：HANDOFF §7 的全量回归口径，红集 ⊆ 73 条。
- 不在 App 运行时跑 `tests/sdk_adapters/test_composition.py`；一次只跑一个 pytest 进程；并发子代理不超过 2–3 个。

## 6. 真实模型与安全

- 只用 deepseek-flash（请求 id `deepseek-flash`）。后端真实测试只在 `-m real_provider` 时运行，凭证在 scratchpad 脚本里 source，进程内脱敏。
- 原生验收的 userdata 副本里，`llm_runtime.json` 改为 flash（不存密钥）。
- 归档前扫描：`\bsk-[A-Za-z0-9_-]{20,}` 为 0，并与真实密钥逐字节比对，只打印计数。
- 不写 `.env` 或 `secrets/`；不手动 `python main.py`（冒烟用真实数据副本 + 独立端口）；远程是私有仓库。

## 7. 风险

| 风险 | 缓解 |
|---|---|
| 同一事件循环里的长回合 | 并发 1 / 1；HA-12③ 验证主对话不受影响 |
| SIGKILL 后在途回合 UNKNOWN 阻塞 | 如实显示 + 接管（HA-15）；租约过期接管（HA-8） |
| 两个实例同时写一个目录 | flock（HA-16） |
| 本机执行模型代码 | S1-a + 默认关闭（HA-14） |
| 证据目录膨胀 | 显示目录位置；自动清理登记为遗留 |
| 模型文本被当成系统说明 | 投影层标注来源 |
| 与 workflows 旧引擎的概念混淆 | 命名隔离；不动 `workflow_service` |
