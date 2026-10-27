# 任务书（给 Codex）：阶段 1——编排 SDK 的会话层（Session + 委派 + inbox）

日期：2026-10-25。发包人：Simple Harness 主代理（Claude）。执行：Codex 桌面版。检查：Claude。
本文自包含。开工前把"项目规则"读一遍，做的过程中按"交付物"写记录。

## 0. 项目规则（硬性）

1. 回复、文档、提交说明全部用中文。
2. 仓库：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness`（git，主干 `main` 直接开发，提交后直接推送 `origin main`）。编排 SDK 在 `sdk/simple-harness-sdk`（当前版本 `0.13.0.dev20260925+opt.216`）。
3. **不许跑全量回归**。只跑改动文件对应的测试；新增调用点所在函数的测试也算"对应"。
4. **工具说明、提示词、模板文字就是执行池身份**：改了旧池起不来。所以聊天池 v1（`…-chat-v1`）的提示词、工具表、池编号一字不动；新能力全放到新开的聊天池 v2。
5. 开发期不做向后兼容层；旧路径该删就删（但本阶段不删桌面端代码，见第 3 节第 9 条）。
6. 改了 SDK 源码必须重生成部署清单，否则启动报 `installed source bytes changed`：在 SDK 目录执行
   `uv run --frozen python scripts/build/taskgraph_manifest.py generate --upstream ../../.local-test-evidence/2026-10-05/f2-upstream/evidence.json`。
7. 改到 `accounting_recovery.py` 等登记段时，`tests/orchestrator/acceptance_assets/mutations.json` 的锚点要同步，发版门会拦。
8. 仓库外/真实模型验收必须用发版轮子，不用源码。
9. 密钥只从 `simple_harness/.env` 读（`DEEPSEEKER_BASEURL / DEEPSEEKER_MODEL / DEEPSEEKER_APIKEY`），不打印、不写进日志或文档。
10. 判断交给模型，Harness 只管约束与秩序；不用关键词、长度之类的硬编码规则替代模型判断；同一件事只留一条路径。

## 1. 背景与目标

AIPhone 要求 SDK 提供"统一主会话"：日常对话直接答；模型判断需要时，用 SDK 内置的委派命令把工作交给现有 HTN/TaskGraph 任务系统；结果回到原会话。用户已拍板架构："会话是 SDK 唯一前门，聊天/任务/语音是它下面的引擎"。阶段 1 只做文本：会话实体、内置委派命令、结果 inbox、统一事件流。语音是后面的阶段。

先读（都在 `plans/2026-10-24-unified-session-realtime/`）：
- `架构研究-会话作为唯一前门.md` §3.2（接口目标）、§3.3（机制）、§3.4（目标程序，就是验收标准）。
- `评估与深度计划.md` §4"阶段 1"的 8 条。
- `阶段0-可行性门-记录.md` 的 S0-6：委派说明与系统提示的底稿是 `.local-test-evidence/2026-10-25/unified-session/phase0/scripts/s06_routing.py` 里的 `SYSTEM`（v2）和 `TOOLS` 里 `delegate_task` 的描述。

再读现有代码：
- `src/agent_orchestrator/host/chat.py`：聊天池（`owner_mode="STANDALONE_CHAT"`），工具名单首句冻结，幂等键回放，按上限预留结清。
- `src/agent_orchestrator/host/host.py`：`door()`、`create_mission()`、`cancel()`、`decide()`、`answer_planning_question()`、`events()`、`approvals()`、`output_stream`。
- 桌面端三样要搬进 SDK 的逻辑：`backend/deskpet/orchestration/chat_tool.py`（`mission_start` 工具：幂等键从聊天 Run 与调用派生、上限、door、auto 代批）、`notices.py`（通知台账：按终态事件 id 去重、从上次 seq 补读、人才能 ack）、`pump.py`（只读看 mission 状态与最高 seq，变化就推白名单事件）。
- `docs/orchestration-host.md` §2b/§2c（现有聊天与逐字输出的接入说明，新章节照这个口气写）。

## 2. 公开接口（目标形态）

| 方法 | 作用 |
|---|---|
| `Session.open(root, principal, model, tools=, connectors=, policy=)` | 打开或恢复（同一会话号再次打开就是恢复） |
| `session.send(text, idempotency_key)` | 文字回合；回执含 `turn_id`、`state`、`text`、工具调用、用量；同键重发原样返回 |
| `session.events(after=)` | 一条异步事件流：正文片段、回合提交、已委派、任务进度摘要、需要人答、结果已到、错误 |
| `session.respond(request_id, decision, version, ...)` | 回答规划问题或审批；走现有 `decide` / `answer_planning_question` |
| `session.interrupt()` | 停当前回合；不动任务 |
| `session.tasks()` / `session.task(id)` / `session.cancel_task(id)` | 会话名下的任务；取消走现有 `cancel` |
| `session.close()` | 关会话；任务保留 |

事件带 `schema_version`、`event_id`、`session_id`、`seq`、`kind`，以及适用的 `turn_id / delegation_id / mission_id / request_id`。正文片段不落盘不重放（沿用 §2c 规则）；其余事件持久、可按 `after` 补读。
现有 53 个 Host 方法不删、不改签名，降为"高级层"。

模型在会话里看到的内置工具：`delegate_task`、`task_status`、`task_cancel`、`session_history_read`、`session_history_search`，加宿主注册的只读 `DomainTool`。

## 3. 要做的事（按顺序）

1. **聊天池 v2**：新池编号含 `-chat-v2-`，带配置版本号；v1 原样保留，旧会话继续在 v1 上聊。`Session` 只用 v2。
2. **会话实体与台账**：数据目录 `sessions/<session_id>/`，持久记录：已提交回合、委派关联、inbox、事件序号。恢复 = 读台账 + 续上现有聊天 Agent（沿用 `chat.py` 的 generation 机制）。
3. **委派命令**：不是 `DomainTool`，是 SDK 注入的控制命令。模型只给 `goal`、`success_criteria`、`sources`（可选）、`reason`；SDK 注入 principal、session_id、turn_id、call_id。把 `chat_tool.py` 的幂等键派生、12 条成功条件/8 份资料上限、`door()`、auto 模式代批搬过来。幂等键 = 会话号 + 已提交输入回合号 + 目标规范化哈希。同键异规范拒（`idempotency_conflict`）；新回合同目标可建新任务；默认同时在途最多 1 个，超出回 `busy`。委派立即返回（`delegation_id`、`mission_id`、`created`），不等任务完成。
4. **inbox**：mission 终态事件 → inbox 记录（按终态事件 id 去重，从上次 seq 补读，至少一次、幂等应用）→ 下一回合作为"资料"注入历史（标明来源是任务结果，不是指令）→ 事件流发 `result_ready`。默认不派生新任务。人能 ack，模型不能。
5. **事件流**：吸收 `pump.py` 的做法（只读看 seq，变化就推，payload 走 `project_event` 白名单），加上逐字片段与 inbox 通知，合成一条流。
6. **预算**：会话账本只引用聊天台账与任务账，不重复结算；给结果回传留固定额度，耗尽时仍能无模型读任务事实。
7. **取消与审批**：创建中取消先持久化、创建成功后收敛；已完成的任务不倒改成取消；`respond` 绑定 request_id 与版本，过期/越权明确拒绝。
8. **测试**：
   - 幂等矩阵四格：同键同规范 / 同键异规范 / 新键同目标 / 新 call_id 同意图。
   - 四个持久点各注一次故障再恢复：session 台账写入后、delegation 记录后、mission 建成后、inbox 写入后；要求无重复任务、无丢结果、无重复应用。
   - 一局真实模型（`.env` 的 DeepSeek 线路）：文本委派 → 任务跑完 → 结果回到会话 → 进程重启 → `events(after=)` 补到 `result_ready`。
   - 只跑改动文件对应的测试。
9. **不做**：桌面端切换到 `Session` 与删除 `chat_tool.py / notices.py / pump.py` 是下一阶段；语音不碰；旧 `chat()` 行为不变。

## 4. 交付物

1. SDK 发版：重生成部署清单 → Host 的 `scripts/release_sdk_opt.sh <旧> <新> "<说明>" <核验回执.json>`（回执由 `sdk/simple-harness-sdk/scripts/acceptance/write_review_receipt.py` 生成）→ 版本号 opt.217 或当时的下一号 → Host 钉版（`backend/vendor/` 轮子与清单）→ 提交、推送。
2. 文档：`docs/orchestration-host.md` 新增"§2d 会话"（接口、事件格式、错误码、恢复规则），`ARCHITECTURE/AGENT_ORCHESTRATION.md` 加一条。
3. 仓库外示例：`~/sh-ext-accept6/`，只装发版轮子，脚本按 §3.4 的 30 行目标程序写，跑通并留日志。
4. 记录：`plans/2026-10-24-unified-session-realtime/阶段1-记录.md`，写明：做了什么、版本与轮子 SHA-256、测试清单与结果、真实模型一局的事件摘要、A01～A05 逐项"通过/阻断/未执行"、剩余限制、留项。
5. 发版后在本文末尾追加一行"完成于 <时间>，版本 <opt.x>"，并通知 Claude 检查。

## 5. 验收项（Claude 会按这个检查）

| 编号 | 场景 | 判定 |
|---|---|---|
| A01 | 普通文本/只读查询 | 主会话正常响应，有真实工具回执；不产生 mission |
| A02 | 文本复杂任务 | 真实模型自己决定委派；一个关联任务实际规划、执行、审阅并交付可校验文件；回到原会话 |
| A03 | 幂等与竞态 | 同键重试一个任务、异规范冲突；创建中/运行中/完成后取消状态确定；新意图不被误去重 |
| A04 | 故障注入 | 四个持久点退出并恢复，无重复/失联任务、丢结果或重复应用 |
| A05 | 预算/权限/审批 | 无越权、双记费用、失控预留；耗尽仍可读任务事实；审批绑定正确请求，过期回复被拒 |
