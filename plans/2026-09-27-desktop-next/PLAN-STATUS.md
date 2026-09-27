# NEXT-TG-1.0 进度（plan-status）

后续 Agent 先读本文件，再读 [NEXT-TG-1.0-计划.md](NEXT-TG-1.0-计划.md)（PlanAgent 方案，已获用户同意执行）与 [HANDOFF.md](HANDOFF.md)（背景）。每完成一步就更新本文件：做了什么、证据在哪、还剩什么。

## 总览

> 测试节奏（用户 2026-09-28 确认）：全部批次做完之前，每批/每次推送前只跑相关定向测试 + 真机；**全量回归只在全部批次完成后跑**。下文 2A.5 是此前的做法，不再沿用。

| 批 | 内容 | 状态 | 最后更新 |
|---|---|---|---|
| 0 | 开工检查与第一份回报（计划 §14） | 完成 | 2026-09-27 |
| 1 | 缺陷修复：收集拒绝隔离、输入清单同源、CI 哈希 | **完成并已推送**（de8a5bcc / 491ff0ad / 6d20b7f0，SDK opt.33）；CI 前端、后端测试通过；「开源卫生检查」失败在推送前的 4a1678ae 就已存在，未处理 | 2026-09-27 |
| 2A | 合法重建来源清单 + 新任务默认严格 TaskGraph | **完成（待用户确认后推送）**：上游核心链真实跑通（opt.39，任务完成 + 冷重放一致）→ 部署清单合法重建；新任务默认严格执行图接入 Host；真机小验收（opt.41，保障层开）任务完成、四个执行图只读入口有回复 | 2026-09-27 |
| 2B | 推进规则（FAST/WAIT/SLOW/STOP）接入原循环 | 进行中（2026-09-28 起） | 见下文「第二批 2B」 |
| 3 | SDK 正式执行过程只读接口 + 默认执行图 | 未开始 | — |
| 4 | 产品入口与已完成能力接通 | 未开始 | — |
| 5A | ARP MISSION 精确来源 | 未开始 | — |
| 5B | 统一能力目录与 Skill 入口 | 未开始 | — |
| 6 | 同制品跨层验收与交付 | 未开始 | — |

用户约束（2026-09-27）：本机未提交的任务过程视图**不提交**、不覆盖；做好的功能默认开启，但用户明确关闭的保持关闭（监工代理、实时语音、DeepSeek 官方端点、自然语言遗忘、两人审批、产品内策略晋升、NanoJev/JEV）；每批做完向用户汇报。

## 第 0 步：开工检查

### 0.1 施工基线（已核对）

| 项 | 值 |
|---|---|
| Host HEAD | `4a1678ae`（= origin/main） |
| 内嵌 SDK | `sdk/simple-harness-sdk/src`，版本 `0.13.0.dev20260925+opt.32`，`SDK_SOURCE_COMMIT=9e6686fe` |
| 钉住 wheel | `backend/vendor/simple_harness_sdk-0.13.0.dev20260925+opt.32-py3-none-any.whl`，SHA-256 `0480aadc…3044`，与 `sdk_candidate.py` 声明一致 |
| 实际导入位置 | `backend/.venv/lib/python3.12/site-packages/{agent_orchestrator,simple_harness}`（wheel 模式）；与内嵌源码逐文件比对 **0 差异** |
| 本机未提交改动 | 7 个已跟踪文件 + `story.py`、`test_mission_story.py`、`tauri-app/src/views/missionStory/`、本目录；逐文件 SHA-256 与差异备份在 `.local-test-evidence/2026-09-27/dirty-backup/`（`tracked.diff`、`untracked.tgz`） |
| SDK 出新版流程 | 改内嵌源码 → 两处 `version.py` 升版 → 提交 → `sdk/simple-harness-sdk/scripts/build/development_candidate.py` 出 wheel + candidate manifest → 放 `backend/vendor/` → 改 `sdk_candidate.py`、`backend/pyproject.toml`、`uv.lock` → `uv sync`（参照提交 `9e6686fe`、`5faa739f`） |

### 0.2 改动前失败基线（逐 nodeid）

| 范围 | 结果 | 名单 |
|---|---|---|
| Host `backend/tests/orchestration`（4a1678ae + 任务过程视图，2026-09-27 上午） | 26 failed / 427 passed / 20 skipped | `.local-test-evidence/2026-09-27/baseline/host-orchestration-failed-nodeids.txt` |
| SDK `tests/orchestrator/gap_phase1` | 1 failed / 299 passed / 38 skipped | `…/baseline/sdk-gap-phase1-failed-nodeids.txt` |
| SDK `tests/orchestrator`（不含 gap_phase1），**按子目录分进程跑** | 87 个失败/错误（full_target 61+1 收集错误、p32 7、p33 7、p35 8、step08 1、step09 2；其余目录全过），无超时 | `…/baseline/sdk-orchestrator-failed-nodeids.txt`，各目录日志 `…/baseline/sdk-parts/` |
| SDK 整套单进程跑 | **会挂住**：跑到 90% 后恢复/超时类测试（`step02/test_recovery_matrix.py`、`step02/test_review_round1.py` 超时重试）空等或空转；单独跑或按目录跑都通过 → 测试间状态污染，改动前已存在，不是产品缺陷。**今后 SDK 回归一律按目录分进程跑**：脚本见 `…/baseline/run_sdk_suite.py` | `…/baseline/recovery-hang.log` |
| 前端 vitest（本机 Node 22） | 958 / 958 通过 | — |

SDK 测试命令：`uv run --frozen --group dev --extra local-capacity pytest tests/orchestrator --ignore=tests/orchestrator/gap_phase1 --continue-on-collection-errors`；gap_phase1 另加 `--with 'pydantic>=2' --with pytest-asyncio`。已知收集错误：`full_target/test_h1i_raw_artifact_collector.py` 导入兄弟测试模块失败（既有）。

### 0.3 第一批修复位置（已定位）

| 缺陷 | 位置 | 修法要点 |
|---|---|---|
| 收集拒绝分支访问 `intent.id` | `agent_orchestrator/orchestrator/event_handler.py:3319,3321` | 改 `intent.intent_id`；双任务真实回归（BudgetError / CommitRejected 各一） |
| 准入时 manifest 缺失补空 | `orchestrator/hierarchical_dispatch.py:2842-2856`（`admissions`） | 禁止补空；改为复用 `read()` 里就绪判定实际用的那份解析结果（`NetworkView` 增加每个 occurrence 的 ResolutionResult），缺失即具名拒绝——当前 admissions 重新解析一次且未传 `now_ms`，与就绪判定不是同一份 |
| 无 DATA 要求即给空 manifest | `hierarchical_dispatch.py:1919-1950`（`resolved_inputs`）、`:1441`（`input_result`） | 合法无输入保留；但必需输入端口（`PortSpec.required`）未绑定 DATA 时不得给空成功（参照 `planning/htn/compiler.py:1430` 的未绑定端口检查） |
| CI 哈希 | `tauri-app/src/auth/controlCommandCanonical.ts:150`；CI Node 20（`.github/workflows/frontend-tests.yml:36`），本机 Node 22 | **用户决定（2026-09-27）：CI 统一改 Node 22**，不装 Node 20。写明原因推断（jsdom 的 TextEncoder 产出跨 realm 的 ArrayBuffer，Node 20 webcrypto 拒收，Node 22 放宽；未在 Node 20 复现）与"开发/测试统一 Node 22"规则；保留真 SHA-256 与跨语言向量；在真实桌面应用确认一次身份认证链路 |

### 0.4 已知前置障碍（第 2A 批）

TaskGraph 来源清单 `taskgraph_deployment_manifest.json` 的上游证据（`mission-5bb7c1fef5597956` 任务回执、冷重放回执，文档记录在 `.local-test-evidence/2026-09-22/v14-host/native-final-01/`）**本机所有仓库都不存在**，只有文档里的哈希。按计划 §6.2 步骤 2，需要在隔离环境重跑一次真实 HTN 核心链（含效果审批、file_publish、冷重放）产出新回执，不能抄旧哈希。

### 0.5 TaskGraph 启用路径（排查结论，2026-09-27）

SDK 前缀 `sdk/simple-harness-sdk/src/agent_orchestrator/orchestrator/`，Host 前缀 `backend/deskpet/orchestration/`。

- **启用前置**（`taskgraph_policy.py:105-162`）：同 command_id 幂等返回旧回执 → 调用者须是装配 principal → 已装配政策一致 → **有效规划委托**（`planning_lane_grants`，scope=mission，TTL 24h，允许 REFINE 与 REPAIR/REPLACE_METHOD；`taskgraph_policy_sources.py:62-88`）→ Mission 政策版本一致 → 部署来源读取器通过（**当前必失败**：清单 597 / 实际 726）→ hierarchical + planning-decision-v1 + 非终态 → 尚未绑定 → 首次 PlanCommit 前无 ACTIVE revision 时跳过基线捕获。
- **写入**：`taskgraph_policy_bindings`（禁改删触发器）+ `commit_receipts`(EnableTaskGraphContract) + 事件 `TaskGraphContractEnabled`，**`actor_type="human"` 写死（:147-150），须改为系统代办**。不同 command_id 并发后到者冲突 → id 须从 Mission 稳定派生。
- **授权 → 首次 PlanCommit**：`_start_planning`（event_handler.py:3954）建规划意图与请求 → `awaits_authority` 等授权（:5546）→ 自动模式 `service.py:809-849` `_auto_authorize_planning`（command_id `host-auto-planning:{req}`）/ 手动 `handlers.py:258`→`facade.py:265`→`api/planning_authorization.py:114` → `_dispatch` 若确定性 REFINE 走 `dispatch_local`（:5557）**同一轮直接提交、不调模型** → `planning_admission_commits.py:267` → `plan_commits.py:294`。
- **缺口**：全仓没有"本任务必须严格"标记；现有检查都是"有绑定才走严格分支"（plan_commits.py:333-347、planning_admission_commits.py:297-301、hierarchical_dispatch 多处）。enable 失败时授权已落库，下一轮照常提交成非严格计划。
- **挂钩方案**：①创建事务里写 required 标记（service.py:1244 / commit_service.py:956）；②SDK `_dispatch` 在 `awaits_authority` 之后、`dispatch_local` 之前加"required 且未绑定 → 等待"（不改 `awaits_authority`，界面授权卡复用它）；③提交门 plan_commits.py:333 与 planning_admission_commits.py:297 对"required 无绑定"拒绝；④Host 自动/手动签发后调用同一协调方法，`_drive` 每轮对账"required + 有效授权 + 未绑定"（内存集合重启会丢，对账器兜底）。
- **注意**：`_request_method_synthesis`（:3385）**不需要规划授权**，可能在启用前就调模型——2B 要纳入同一推进规则。参考时序：SDK 夹具 `tests/orchestrator/full_target/taskgraph_exec/production_fixture.py:67-84`（开规划意图 → 带 request_id 签发 → 确认未绑定 → enable → 运行）。
- **清单**：格式见 `taskgraph_deployment.py:39-77`（upstream 9 字段 + source_files 覆盖两个包 + deployment_id=去掉自身后 canonical JSON 的 sha256）；**仓库里没有生成脚本**，旧 deployment_id 在另一台机器生成（`plans/TaskGraph/V1.2.1/EXECUTION-JOURNAL.md:474-478`）；可复用 `scripts/build/development_candidate.py` 产出 wheel/manifest/source_inputs 三个哈希。

### 0.6 规划器唤醒入口（2B 要统一的对象）

`_start_planning`、`_refine_open_compounds`（每 revision 一次）、`_request_method_synthesis`（无需授权）、`_resume_planning_services`（服务事件，阶梯上限 `max_planning_attempts=2`）、`_wake_planning_waits`、`wake_blocks`（planning_runtime_block.py:142，**无次数上限，来源 A↔B 振荡会一直唤醒**）、修复请求 `collect_triggers`、`_dispatch_h4_repair_trigger`、`_planning_rejected`、`_retry_deferred_planning`（**内存，重启丢失**）、`_retry_deferred_repair`、`_after_synthesis_round`、`_repair_after_root_review`（上限 1）、`_escalate_read_only_rewrite` / `_escalate_repeated_verification`（各 1）、TaskGraph 通知（只处理已绑定任务）。
已知重叠：①同一失败先记修复请求再由 `_escalate_*` 直接开回合，之后 `_resume_planning_services` 又因该请求再开一轮；②根评审被 `_decide` 与 TaskGraph REQUEST_COMPOSITION 两路推进；③`_planner_intents_in_flight`（:7337）把根评审/操作评审回合也算作规划在途，会压住续规划；④合成与细化/根评审修复在 empty_planner_skip 分支可能同时进入。

### 0.7 "做好未进产品"逐项（真实消费者 / 开启条件 / 阻断）

| 能力 | 真实消费者（现状） | 开启条件 | 阻断 / 处理批次 |
|---|---|---|---|
| 严格 TaskGraph 内核 | 无（`service.py:1466` 无调用方） | 来源清单通过 + 有效规划委托 + 首次提交前 | 清单过期、上游证据缺失、无 required 标记 → 2A |
| 执行过程只读接口 | 无（`story.py`/`live_graph.py` 是 Host 直读 SDK 表） | SDK 新接口 | → 3 |
| 发布目录 | `service.py:289` 仅当 `publish_dir` 非空才装 file_publish | 用户选目录 | 设置页无入口（功能倒退）→ 4 |
| 任务中途增删资料 | 后端 `handlers.py:30-32` 有动词，前端 0 调用 | UI | → 4 |
| 聊天主 Agent 发起任务 | 无工具 | 工具适配器调 `mission_create(_with_sources)` | → 4 |
| 委派工具 agent/agent_parallel/spawn_team/spawn_subagents | 模型可见，调用固定返回 `delegation_unavailable`（`tools/code_tools/spawn_subagents_tool.py:63-109`；入口 `build_subagent_batch_delegate` 零调用） | 接 Harness 委派入口 | → 4（接不通则先下架并记欠项） |
| 工具熔断 / 自动续跑 | 与监工同在 `if supervisor.enabled`（`main.py:5257`），用户关了监工所以一起失效 | 拆出独立开关 | → 4 |
| 模型路由 / 升级 / 回退 | SDK `runtime/model_router.py`；Host 构造 Orchestrator 未传 routing（`service.py:342-347`） | 传 RoutingRules | → 4 |
| 多任务并发 | `settings.py:41-42` 固定 1/1（注释：聊天共用服务商额度） | 按真实配额配置 | → 4 |
| 多候选择优 / 仲裁预留 | `candidates_per_task=1`；`conflict_reserve_tokens` 默认 0，Host 不补 | 已有触发与限额 | → 4 |
| 金额计价 | `price_table=None`（计划列为非目标） | 价格表 | 计划不做，保留 |
| 写着开却无读取方的开关 | `[features]` plan_confirm_gate / subagent_driver / compaction_enabled、`[tools.verifier]` extractor_fallback_enabled、`[tools.last_mile]` outline_preview_default、`[agent]` interrupt_enabled、`[tools]` strict_unknown_toolset、`[memory.v2.forget]` enable_natural_language | 逐个接上或删除 | → 4 |
| ARP MISSION 模式 | `creation.py:217` 拒绝；Host `native_plane.py:311` 固定 STANDALONE_CHAT | 真实来源绑定 | → 5A |
| 统一能力目录 / 技能入口 | 各池 `root_id` 含 profile_id；`agent_` 前缀未放行 | 同 realm 单一目录 | → 5B |
| 语义模型自动下载 | 规格写"不自动下载" | — | 计划不做 |
| 保持关闭（用户/设计） | 监工代理、实时语音、DeepSeek 严格工具参数、自然语言遗忘、两人审批、产品内策略晋升、NanoJev | — | 不动 |

### 0.8 待完成

- [x] TaskGraph 启用前置、授权入口、首次 PlanCommit 先后关系（§0.5）
- [x] 所有 Planner / 合成 / 修复唤醒入口清单（§0.6）
- [x] "做好未进产品"逐项（§0.7）
- [ ] SDK 主套件失败基线
- [ ] 向用户交第一份回报

## 第一批：缺陷修复（2026-09-27 完成，已推送）

| 项 | 做了什么 | 证据 |
|---|---|---|
| 收集拒绝隔离（计划 §5.1） | `event_handler.py` 拒绝分支 `intent.id`→`intent.intent_id`；同一 intent 同一原因只记一次，收集成功后清除 | 新测试 `tests/orchestrator/test_collection_refusal_isolation.py`：两个真实任务，A 被拒 3 次（两种原因），B 照常完成，A 随后完成、无多余模型调用、记录 2 条。改动前报 `AttributeError: 'DispatchIntent' object has no attribute 'id'` |
| 输入清单同源（计划 §5.2） | `NetworkView.resolutions` 保存就绪判定用的那次解析；`admissions()` 与 `taskgraph_dispatch.prepare()` 直接用它，不再二次解析、不再用空清单顶替（缺失→具名拒绝 `input_resolution_absent`）；`ReadinessReport.input_manifest_hash` 记下检查过的清单指纹，`admit_for_dispatch` 只收同一份；`input_result()` 仅在"无数据要求且无必需输入端口"时视为合法无输入，否则交解析器报 `UNBOUND_REQUIRED_PORT` | 新测试 `tests/orchestrator/full_target/test_input_manifest_same_source.py` 6 条（改动前 5 失败 1 通过——撤权那条原本就对）。独立审阅确认：所有准入调用方同源；桌面"接续上一步"步骤的必需端口无数据来源时，计划提交阶段已被 `validate_execution_projection` 拒收，不会出现以前能跑现在被拒 |
| CI Node 22 | `.github/workflows/{frontend-tests,release}.yml` 改 `node-version-file: tauri-app/.nvmrc`（内容 `22`）；原因写在工作流注释与提交说明 | 本机实测 vitest jsdom 环境 `globalThis.ArrayBuffer !== Node 的 ArrayBuffer`；推送后看 CI |
| SDK 出版与钉版 | opt.33：`development_candidate.py` 构建（工作树干净），wheel `20751209…8942`，清单 `f06d709b…20b8`，源提交 de8a5bcc；Host 钉版、`uv sync --inexact` 只替换 SDK 一个包；安装内容与内嵌源码逐文件 0 差异 | Host 钉版身份相关测试 75 通过 |
| 回归（定向） | SDK `full_target` 61 失败/4313 通过、`step02` 42 通过、根目录 15 通过；与基线逐个比对 **无新增失败** | `.local-test-evidence/2026-09-27/batch1/` |
| 独立审阅 | 另一模型一轮：**无阻断问题**；自跑 278 个相关测试全过 | — |
| 真机 | 桌面应用 opt.33 启动，编排可用；主对话显示"空闲 · 排队 0"（身份绑定成功，WebView 中真实 SHA-256 链路正常）；新建两步任务（facts.md → quiz.md，第二步依赖第一步产出）6 分钟正式交付，2 次执行 0 重试，第二步首个动作读到 facts.md；暂不派发原因均正常，无 `input_resolution_absent` | 截图 `.local-test-evidence/2026-09-27/batch1/batch1-native.png` |

已推送（用户 2026-09-27 同意）。任务过程视图等未提交文件未动。

## 第二批 2A：来源清单 + 新任务默认严格 TaskGraph（完成，待推送）

### 2A.1 上游真实核心链复验（计划 §6.2 步骤 2）

旧原件本机缺失，按计划重跑。环境：隔离用户目录 `.local-test-evidence/2026-09-25/opt/ui-full/`（`launch-app.sh` 启动），已安装 SDK opt.33（wheel `20751209…8942`），`config.toml [orchestration] publish_dir` 指向同目录下 `isolated-published/`（只在证据目录）。

| 步骤 | 状态 | 说明 |
|---|---|---|
| 真机新建任务（写 slugify.py + 测试 + NOTES.md，再把 NOTES.md 发布到授权目录） | 第一次作废 | `mission-80ac49950d12c9da`：自动模式把"发布"这条要求当成纯内容**自动确认**，发布会被静默丢掉 → 界面取消（保留记录），先修缺陷（见 2A.1a） |
| 修复后重建（opt.33） | 失败 | `mission-4605f80049e1e94e`：自动确认修复生效（停在"已创建"✔）；界面映射发布效果后，规划被拒 3 次任务失败——见 2A.1b |
| opt.34 重跑 | 失败（缺陷见 2A.1d） | `mission-cd635f4320cb1644`：规划通过（opt.34 修复生效），模型合成 4 步（写模块→写测试→写说明→发布）；前两步通过，第三步"写说明"因工作区缺 `slugify.py` 被顺带的 pytest 检查判失败，修补重跑仍失败，判卡死，任务失败。25 次模型调用、26.6 万 token，全部结算 |
| 改用短链重跑（opt.34） | 作废（缺陷见 2A.1e） | `mission-6dc5c57868b8e450`：两步（写 NOTES.md → 发布候选）都通过验收，但发布步骤把候选写成根目录 `action_candidate.json`（自编格式），操作工作区只认 `actions/*.json`，界面无候选可提交；界面"取消任务"点击无反应（未弹确认框），待重启后取消 |
| opt.35 重跑 | 作废（缺陷见 2A.1f） | `mission-bc2c094e9dc5e3b5`：候选这次正确写到 `actions/action_candidate.json`，但规则检查按"任务输出文件列表"核对，判为未声明，同一步失败 3 次后转重规划；第 6 次规划模型输出解析失败，第 7 次因方法实例 id 与已存实例冲突（UNIQUE）被拒；界面取消 |
| opt.36 重跑 | 作废（缺陷见 2A.1h） | `mission-f2cecb7766f23e41`：候选文件与规则检查都对了，验收事务里第三处按 `task.outputs` 判"未声明"（`undeclared_action_output`），同一步重试；另见 2A.1g 循环空转；界面取消 |
| opt.37 重跑 | 作废（缺陷见 2A.1i） | `mission-e0e19e2a81d75d20`：两步通过、候选 `actions/action_candidate.json` 验证通过并出现在界面候选列表（2A.1e/1f/1h 修复生效）；界面选中候选后预览 `publish → NOTES.md`、参数 `{artifact_path: NOTES.md}` 正确；点"提交此操作供审查"被拒 `CHECK_POLICY_UNRESOLVED: ACTION_PROPOSAL` |
| 上游复验（保障层关，opt.37） | 作废（缺陷见 2A.1j） | `mission-5796f88601600f8b`：两步通过、候选选中、操作意图提交成功（首次走到操作审查）；模型审阅员两次都以"参数所指产物≠候选文件"判不通过（4 项确定性检查全过）；界面取消 |
| 上游复验（保障层关，opt.38） | 失败（缺陷见 2A.1k/1l） | `mission-fa554d9068c70cb0`：**核心链首次走通到效果**——候选提交→操作提案审查通过（2A.1j 修复生效）→界面批准→`file_publish` 真实写入 `isolated-published/NOTES.0c0ac4dd7d6f.v1.md`（1442 字节）→操作结果验收通过→根结论提交（目标判定满足）；随后旧式任务终判评审员读文件全被拒（2A.1l），判不满足，任务失败 |
| 上游复验（保障层关，opt.39） | **完成** | `mission-a22c9fc39b8abed8`：第一步写 NOTES.md（含测试）通过；发布步骤第一次候选因没把收到的 NOTES.md 列进产物被拒（模型没遵守 2A.1k 的说明，见 2A.1m），修补重试列入后通过 |
| 提交操作意图 → 审批 → 发布 → 回读 → 任务完成 | **完成** | 界面选候选→提交→操作提案审查通过→界面核对目标（产物 `artifact-8b41…`，哈希 `7c0a97…`）后批准→`file_publish` 写入 `isolated-published/NOTES.638371dc4c39.v1.md`，**字节哈希与验收过的 NOTES.md 一致**→操作结果验收→根结论提交→目标判定满足→**MissionCompleted**。3 次执行尝试、30 次模型调用、23.2 万 token，全部结算（无未知用量） |
| 任务回执 + 冷启动回放回执 | **完成** | `.local-test-evidence/2026-09-27/batch2a-upstream/`：`mission-receipt.json`（sha `69edb861…`）；应用退出（端口与进程均消失）→冷启动→同一只读脚本重算 `mission-receipt.after-cold-restart.json`，**逐字节一致**；`cold-replay-receipt.json`（`unchanged: true`）；`evidence.json` 与上游 wheel/候选清单原件一并归档 |
| 部署清单合法重建 | **完成** | `taskgraph_manifest.py generate --upstream evidence.json`（上游 = opt.39 wheel `58c7307a…`、任务 `mission-a22c9fc39b8abed8`）；`verify --wheel` 通过，4 个反例（缺文件/改字节/改清单/缺证据）全部拒绝；Host 虚拟环境里读取器接受。清单随源码变化重新生成：opt.40（`34e47fca…`）、opt.41（`bbd3b888…`） |

### 2A.1a 真机发现并修复：自动确认吞掉"发布"要求

- 现象：自动模式下，Host 只把以 `action:` 开头的要求当"操作类"，用户用普通中文写"NOTES.md 已发布到授权的发布目录"会被当成纯内容自动确认——任务照常"完成"，但什么也没发布。违背 2026-09-26 用户决定（操作类仍等用户确认）。
- 修复：`backend/deskpet/orchestration/service.py` 新增 `_OPERATION_WORDS`（发布/上传/发送/部署/推送/上线/发到/寄出/提交到/publish/upload/deploy/send/post to/push to），任务原文或完成要求文字命中就不自动确认，留按钮给用户（宁可多等一次，不可少等）。
- 测试：`backend/tests/orchestration/test_auto_confirm_content_completion.py` 新增一条（中文发布、英文 upload/deploy、完成要求文字里的"推送"），3 条全过。真机复现后重建的任务确实停在"已创建"。
- 顺带记下（第 4 批界面处理）：新建任务后左侧列表不刷新，要切一下页面才出现新任务。

### 2A.1b 真机发现并修复：带发布要求的多步任务规划必失败（SDK opt.34）

- 现象：模型合成的方法有 4 步（写模块/写测试/写说明/发布），桌面叶子步骤类型笼统声明"全部根要求"（含发布这条效果要求），完成范围编译把每个被方法链接的叶子都当成"带效果要求却没有负责方"，拒绝；同一确定性细化原样重试 3 次后任务失败（重试不变也照样重试，属 2B 推进规则要管的 STOP 场景）。上午 9e7cc928 只对"内容要求"忽略了这种笼统声明，漏了效果要求。
- 修复：`planning/htn/completion_scopes.py` 方法已链接的叶子不因笼统声明而认领效果，效果仍归义务根。
- 测试：`test_completion_scope_compiler.py` 新增一条（改前红：`carries an effect criterion`）；操作完成目录 170 条全过。
- 出版：SDK 提交 b60feec2（opt.34，含 Host 自动确认修复），wheel `702c39e9…d95c`、清单 `fbac3338…6faf`；Host 钉版提交 3302f7b9；钉版身份相关 67 条通过。**均未推送。**

### 2A.1c 记下的产品缺口（第 4 批处理）

- 执行步骤只有在完成要求里写了 `action:file_publish.publish:<目标>` 这种格式时，才会拿到"产出发布候选 actions/*.json"的说明；用户用普通中文写"发布到…"并在界面上映射发布效果，执行步骤不会产出候选，任务会卡在等候选。需要让界面映射的发布效果也驱动候选说明。
- 新建任务后列表不刷新；点列表项偶尔详情空白，要切页面才出现。
- 下拉框（所属目标/完成标准）只能用鼠标或键盘展开再选，键盘回车不生效（真机点击时发现）。

### 2A.2 SDK：新任务必须走严格执行图（代码已写，未提交，待 opt.35）

| 项 | 做了什么 |
|---|---|
| 持久标记 | 新迁移 29：`taskgraph_requirements`（任务 id、内核版本、来源、时间；禁改禁删触发器）。`orchestrator/taskgraph_requirement.py`：`require_taskgraph()` 只能在事务里、只对还没有计划和执行尝试的新任务写（旧任务不转换、不补历史）；`missions_awaiting_taskgraph()` 列出"要求了但还没绑定"的未终结任务；`enable_command_id()` 从任务 id + 内核版本稳定派生启用命令 id |
| 派发等待 | `event_handler._dispatch`：授权之后、本地/模型派发之前，要求了但未绑定的规划请求原地等待（每个请求只记一条说明）；`_has_pending_planning_waits` 把它算作外部等待，不当卡死、不另起规划。方法合成不受影响（不是规划请求） |
| 提交门 | `plan_commits.py` 与 `planning_admission_commits.py`：要求了但未绑定 → 拒绝 `TASKGRAPH_REQUIRED_NOT_BOUND`，绝不提交非严格计划 |
| 启用身份 | `taskgraph_policy.py`：启用事件从写死的 `human` 改为 `system`，关联真实授权人，负载加 `enabled_by=HOST_DELEGATED` |
| 测试 | 新增 `tests/orchestrator/full_target/taskgraph_exec/test_taskgraph_required.py` 3 条：授权后未绑定时规划不派发、不调模型、无计划；绑定后同一请求提交出带执行图修订记录的计划；同一命令 id 再启用读回同一回执、只一条绑定；启用事件为系统代办；绕过派发门时提交门仍拒绝；标记只能创建时写。**变异**：去掉派发门 → 第一条红；去掉提交门 → 第二条红 |
| 来源清单生成脚本 | `sdk/simple-harness-sdk/scripts/build/taskgraph_manifest.py`：`generate --upstream 证据.json`（对证据**文件**求哈希，核对候选清单确实指这个 wheel、任务回执确是该 wheel 上完成的、冷回放回执确证回执未变；清单内容取自实际打出的 wheel 里两个包的文件，排除规则同读取器；身份 = 去掉自身后的规范 JSON 哈希；验收字段固定 NOT_RUN）。`verify --wheel`：解包到全新目录、从那里导入、跑原读取器，并证明 4 个反例被拒（缺文件、同名文件字节变、清单被改、缺上游证据）。假证据演练通过，身份两次生成一致 |

### 2A.1d 真机发现（待修，列入第二批后续）：多步"接续交付"链条不累积文件

- 现象：`desktop.continue-delivery` 步骤的输入端口和输出端口同为 delivery（"在收到的交付上交出下一版"），但它交出去的只有自己新写的文件，收到而没改的文件不随之交给下一步。4 步链"模块→测试→说明→发布"里，第三步只拿到 `test_slugify.py`、拿不到第一步的 `slugify.py`；检查策略对每步顺带跑一次 pytest，于是导入失败判不通过；修补重跑同样失败，任务判卡死失败。
- 影响：任何 3 步以上、后面步骤需要前面第二步以前文件的桌面任务（尤其带测试的代码任务）都会失败。
- 进一步排查：计划里的输出端口在没有声明基数时一律按"单值"处理（`parse_port_claims` 中 `single = set(single_valued) or known`，对应设计 §24.1 决定 3"单值端口只有一个绑定"），桌面 delivery 端口因此每步只能交一个文件；严格执行图的输入清单也只含直接上游边。所以"累积交付"不能靠补登记产物解决，要改契约：候选方向 ①桌面 delivery 声明为多值端口（工作区快照），②接续类步骤以全部祖先已验收产物为只读基线（类似 P2.3o 种子覆盖），③方法合成时要求需要跨步文件的步骤显式声明多条数据边。需要独立审阅裁决后再做。
- 原拟修法（已否决，违背单值端口约束）：接续类步骤验收时，把"收到但未改写（同路径未被新产物覆盖）"的上游产物按原内容哈希一并登记到其 delivery 输出端口（来源仍指原产物，不复制不改写），下游输入清单与工作区因此完整、可追溯。需要同时核对严格执行图的输入来源记录与"声明完成"规则。修完用 4 步代码任务真机复验。
- **独立裁决（opus，2026-09-27）：采用方案②**。只对"接续边"生效：上游 delivery 输出端口 → 下游同名 delivery 输入端口，且下游是"输入端口都是输出端口"的接续类型。下游输入 = 直接上游自己验收的文件 + 直接上游那次被验收的执行当初冻结的输入（逐级递归）；同一路径以离下游最近的版本为准；每一项都保留真实生产者、产物编号和内容哈希，并写入精确输入清单。只有顺序依赖（没有数据边）的前驱，仍然不带任何文件。
  - 改动位置：`artifacts/bound_workspace.py` 新增一个纯函数；`hierarchical_dispatch.overlay_attempt_inputs` 在接续边上调用它；`taskgraph_dispatch._intent_inputs` 的生产者核对放宽到"直接生产者冻结清单的传递闭包"，并逐项核对哈希。
  - 否决的方案：①多值端口（登记只允许本次写过的文件，而且与"产物必须属于该生产者"的检查冲突）；③靠模型每次都连数据边（规划契约禁止同一输入端口有多条边，而且模型会漏连）；④只收紧 pytest 的顺带运行（只是掩盖问题，写说明那一步照样看不到代码）。
  - 必须有的测试：3 步链在严格执行图下，第 3 步的输入和工作区同时有两个 .py、哈希一致、整树 pytest 通过（改前必失败）；中间步骤改写了某文件时，下游拿到新版本；只有顺序依赖的前驱仍然不带文件；冻结记录被篡改时拒绝执行；恢复后读回的输入与派发时一致。

### 2A.1e 真机发现并修复：分层步骤拿不到"发布候选格式"说明

- 现象：Worker 只有在任务的输出文件列表里含 `actions/*.json` 时才会收到候选格式说明；分层（HTN）叶子任务的输出文件列表是空的，所以从没收到。于是模型自编格式、写在根目录，操作工作区找不到候选，任务无法提交发布。
- 修复：SDK `runtime/action_schema.py` 定义操作候选端口常量 `action_candidate` 和文件 `actions/action_candidate.json`；`event_handler._action_candidate_outputs`：分层叶子声明了该端口时，把这个文件加入下发候选说明所用的输出列表。Host 桌面声明今后改用这个常量（Host 批次一起改）。
- 测试：`test_continuation_delivery_chain.py` 中 1 条（变异：去掉补充 → 红）。

### 2A.1d 实现（按裁决方案②）

- `hierarchical_dispatch.carried_inputs()`（模块级，只依赖存储）：生产者是接续类型、且有被验收的结果时，读取它被验收那次执行冻结的输入，逐项复核：确属本任务、已被其真实生产者验收、路径和哈希都一致。不带 `actions/` 下的候选文件。`overlay_attempt_inputs` 在直接输入和已有覆盖之后，按"最近版本优先"补上这些项。
- `taskgraph_dispatch._intent_inputs`：冻结核对允许"直接生产者冻结清单的传递闭包"内、且逐项等值的条目，其余条目仍拒绝 `TASKGRAPH_FROZEN_DATA_PRODUCER_MISMATCH`。
- 测试：新文件 `tests/orchestrator/full_target/test_continuation_delivery_chain.py`，其中 3 条覆盖：接续生产者会带下文件；准备类生产者不带；被篡改或未验收的条目不带；闭包放行、闭包外拒绝。变异：去掉闭包放行 → 红。真实 4 步代码任务留到 opt.35 真机复验。

### 2A.2 追加：独立审阅（opus）结论与处理

- 阻断 1：授权过期或被撤销后，等待绑定的任务会永久卡住。已修：等待门只在"有一份当前有效的规划授权"时才等（`current_planning_grant`，口径与启用时相同），否则放回原有准入流程，走过期拒绝再重新申请的老路。同时去掉 `planning_admission_commits` 里那道提前的门禁，只保留 `plan_commits` 这一处；它是计划的唯一写入口，且在原授权校验之后执行。新测试：撤销授权后请求不再挂起、也不产生计划（变异：去掉有效授权条件 → 红）。
- 隐患 2：写标记时没做资格检查。已修：`require_taskgraph` 要求分层语义加 planning-decision-v1 协议，否则拒绝 `TASKGRAPH_REQUIREMENT_MISSION_NOT_ELIGIBLE`；新测试覆盖。
- 审阅方自行核实：提交门覆盖了唯一写入口；迁移 29 兼容旧数据；完成范围的修改没有放过应该报错的情形；清单脚本在全新虚拟环境里安装后，原读取器能通过。
- 仍待办：把"等待执行图绑定 / 启用故障"写进任务快照和界面，满足计划 §6.4"明确等待"的要求。这部分放在 Host 批次。

### 2A.1f 真机发现并修复：规则检查与候选说明口径不一

- 现象：opt.35 下 Worker 按说明把候选写到 `actions/action_candidate.json`，但 `_action_problems` 仍按 `task.outputs`（分层叶子为空）核对，判"不是该任务声明的输出"，同一步重试 3 次失败。
- 修复：`_action_problems` 改用与候选说明相同的 `_action_candidate_outputs(mission, task)`。测试 `test_continuation_delivery_chain.py` 新增 1 条（变异：改回 `task.outputs` → 红）。
- 另记（未修，列入 2B/后续）：重规划时模型复用了已存的方法实例 id，提交报 `UNIQUE constraint failed: method_instances`，按"提案不成立"拒绝——应在提交前识别同一实例并给出可修复的具名拒绝，或由系统派生实例 id。

### 2A.1g 真机发现（待查）：编排循环空转、自动授权发不出

- 现象（opt.36，10:55–11:07）：新任务方法合成完成后进入"多个可用方法由模型选"，规划意图等授权；后台 CPU 84–96%，日志 11 分钟完全不动，规划授权始终没有发出，任务停在"规划中"。重启应用后授权立即发出，任务继续推进。
- 推测：Host 每轮先 `await orchestrator.run()`，结束后才自动授权；`run()` 只在空闲时返回，若 `_cycle()` 每轮都报"有进展"，就会不睡眠地一直转（10000 轮上限），Host 永远轮不到自动授权。具体是哪一步误报进展，尚未确认。
- 已布置：应用改为把 stderr 写入 `logs/app-stderr.log`；看门狗在日志 5 分钟不动且 CPU 高时发 `USR1` 抓全部线程调用栈。复现后定位。
- 另外：`launch-app.sh` 以前的启动方式把 stderr 丢进了 /dev/null，诊断栈抓不到——今后启动一律重定向到文件。

### 2A.1h 真机发现并修复：验收事务里第三处"候选是否已声明"

- 修复：`runtime/action_schema.declared_action_outputs(store, mission_id, task)` 作为唯一判定；候选说明（`_action_candidate_outputs`）、结果规则检查（`_action_problems`）、验收事务（`action_commits._action_candidates`）三处共用。全仓只有这两处消费候选路径（`is_action_path`），均已改。
- 测试：`test_continuation_delivery_chain.py` 再加 1 条（变异：验收事务改回 `task.outputs` → 红），共 6 条。

### 2A.1i 真机发现（待修，独立设计项）：审查保障层下"操作提案审查"从未接通

- 现象：保障层默认开启（2026-09-24 起）后，带发布的任务提交操作时，SDK 在提交事务内启动"操作提案审查"，需要该效果负责范围上已批准的 `ACTION_PROPOSAL` 检查策略；没有 → `CHECK_POLICY_UNRESOLVED`，操作无法提交。
- 根因（逐层）：① Host `assurance.project_check_policies` 只为 CONTENT 和 MISSION_FINAL 自动批准；② SDK 对外批准接口 `facade.approve_assurance_check_policy` 只接受这两种用途；③ `lossless_scope_mapping` 也只支持这两种；④ 操作提案的 4 项确定性检查（intent-scope / parameters-and-candidate / effect-capability / write-safety）从未注册为检查规格（注册表只有本地/执行器检查层），所以即使放开用途，也做不出"无损映射"。OPERATION_OUTCOME 同理，要等发布之后才会暴露。
- 影响：保障层开着时，所有带发布或外部操作的任务都走不完。
- 处理：需要设计——把操作提案/操作结果的确定性检查接进检查规格注册与结果导入，或另定这两类用途的策略口径；交独立裁决后实现，用真机带发布任务在保障层开启下复验。

### 2A.1j 真机发现（待修）：操作提案审查标准措辞让模型误判

- 现象：`mission-5796f88601600f8b` 首次提交操作，4 项确定性检查全部通过，但模型审阅员把"参数与候选"判为不通过：它认为参数里要发布的产物（`NOTES.md`，artifact-4ff55…）应当与候选描述文件（`actions/action_candidate.json`，artifact-53346…）一致。实际上两者按设计就是不同的文件：候选是操作描述，参数指向要发布的已验收内容。
- 根因：标准原文 "The frozen parameters bind the exact candidate bytes and accepted inputs."（`operation_proposal_review._criteria`）语义含糊。
- 处理：界面"提交修订版并替代原请求"重提一次（审阅是模型判断，有波动）；标准措辞的修订要注意它是否进入已发布模板或池身份（见"工具说明即池身份"的教训），单独评估后再改。

- 2A.1j 修复（opt.38，待出版）：`operation_proposal_review._published_artifact` 真正核对"参数要发布的产物属于冻结的已验收输入、id 和哈希一致"，不一致就直接拒绝（不交给审阅员判断）；核对结果写入"参数与候选"检查事实 `published_artifact`；标准措辞改为明确"候选是操作描述、被发布的是那份已验收产物，两者本来就是不同文件"。审查策略身份只由 id 决定，改措辞不影响已有策略。
- 顺带发现：迁移 29 使 `assurance_exec/test_root_lookup_index.py`（假定最后一个迁移是索引）从 opt.35 起失败——之前的定向回归没覆盖该目录；已改为按名字查找。另：该测试注释提到 09-26 修过一次"编排循环不睡眠导致 CPU 满载"，与 2A.1g 现象相似，查空转时优先对照。
- 全量 SDK 回归（按目录分进程）已开跑，结果写 `.local-test-evidence/2026-09-27/batch2a-full/`。

### 2A.1k 真机发现并修复：后续步骤的候选指向上一步的文件

- 现象：opt.38 下"发布"步骤（第二步）也按必填的候选端口写了候选，指向第一步产出的 `NOTES.md`，但没把它列进自己的产物 → `artifact_not_in_result` 被拒 → 修补重试时 Worker 把收到的 `NOTES.md` 原样列进产物后就通过了（已有机制：信封里列出的绑定输入计入本结果）。
- 曾尝试把候选端口改为可选，但与现有设计决定冲突（`test_finalizer_output_ports.py::test_an_optional_port_of_a_criterion_linked_step_is_not_owed`：可选端口不纳入"声明端口"，声明即必须），已撤回。
- 修复：候选说明新增 `artifact_path_rule`——"artifact_path 必须是本步骤结果里的文件；要发布的是原样收到的输入时，把它也列进结果产物"。

### 2A.1l 真机发现并修复：任务终判评审员读不到任何文件

- 现象：根结论已提交后，旧式任务终判（`_judge_mission`，工作区视图 id 为 `<任务>-judge-<所有者>`）的所有文件读取都被执行权限检查拒绝（`attempt_unavailable`，因为它不是执行尝试），评审员据此判"内容不可读、不满足"，任务失败。保障层开启时走 MISSION_FINAL 审查，所以之前没暴露。
- 修复：`event_handler._tool_execution_refusal` 识别任务级终判视图，任务未结束时放行。测试 `test_continuation_delivery_chain.py` 新增 1 条（变异：去掉放行 → 红）。

### 2A.1m 真机发现并修复：候选指向的文件由系统列入本步结果（SDK opt.40）

- 现象：opt.39 上游复验第一次发布候选仍因"原样收到的 NOTES.md 未列进产物"被拒——2A.1k 加的说明模型没遵守（结果模板要求列"你修改或新增的文件"）。
- 修复：`runtime/action_schema.with_candidate_targets`：解析结果信封时，若 `actions/*.json` 候选的 `artifact_path` 指向本次工作区里真实存在的文件而信封没列，系统替它列上（不跟随符号链接、不越出工作区、不列另一个候选）；身份仍由 `bind_artifact_params` 从验收结果绑定。测试 `test_candidate_target_listed.py` 3 条。

### 2A.2b Host：新任务默认严格执行图（已提交 99b18d8b）

- `settings.strict_taskgraph` 默认开（只有 `[orchestration] strict_taskgraph = false` 能关）。
- 两个创建事务内调用 `_require_strict_taskgraph`（只对真正新建的任务写要求，幂等重放不转换）。
- 协调器 `_enable_required_taskgraphs`：自动授权后、手动授权后、每轮循环都跑；命令 id 由任务派生（重试同一命令）；"尚无授权"是正常等待，其它拒绝记为部署故障（同一原因只记一次日志），任务继续等待、绝不退回无执行图规划。
- 任务详情新增 `taskgraph: {required, waiting, fault}`。
- 测试 `test_strict_taskgraph_default.py` 4 条；Host 编排全目录对照基线**无新增失败**（另有 2 条旧失败转绿）。

### 2A.3 真机小验收（计划 §6.5）

| 轮次 | 结果 | 说明 |
|---|---|---|
| opt.40 | 发现缺陷（2A.3a） | `mission-9d7dba608eb2aaf2`（写 ENUMERATE.md）：创建事务写入要求 ✔（`deployment_default`）→ 自动授权 → 系统启用执行图 ✔（`TaskGraphContractEnabled`，部署清单 `34e47fca…`）→ 规划回复连续 3 次判"范围纪元已移动"→ 任务失败 |
| opt.41 | **通过** | `mission-8cb840d96b04beba`：要求写入 → 自动授权 → 系统启用执行图 → 规划提交携带执行图修订（`TaskGraphRevisionRecorded`）→ 执行 → 保障层终审通过 → **MissionCompleted**。四个只读入口走真实控制通道全部有回复（快照/为何未就绪/差异/收敛）；详情 `taskgraph={required:true, waiting:false}`；旧任务仍 `NOT_ENABLED`。证据 `.local-test-evidence/2026-09-27/batch2a-behaviour/`（`SUMMARY.md`、`taskgraph-reads.json`） |

### 2A.3a 真机发现并修复：执行图任务的规划回复必过期（SDK opt.41）

- 根因：保障层把自己的证据纪元（`assurance:mission`）存在同一张纪元表里，写入规划授权/请求授权绑定时会推进它；执行图模式读取纪元时读全表，于是把它算进规划请求的范围摘要。Host 是在请求绑定**之后**才发授权的，所以每次回复都判过期（上游复验时保障层关、也没走执行图，所以没暴露）。
- 修复：`taskgraph_epochs.planning_scope_digest`：请求绑定与判定两处共用，摘要不计 `assurance:mission`（它是保障层自己检查新鲜度的信号，不改变计划含义）；其它纬度照旧。测试 `test_planning_scope_digest.py` 3 条；规划判定/执行图/保障层相关测试对照基线无新增失败。

### 2A.5 本批全量回归（推送前一次）

- SDK（按目录分进程，opt.41）：失败 69 个，**全部在改动前基线内，无新增**；基线 87 个中 18 个转绿。结果 `.local-test-evidence/2026-09-27/batch2a-full-opt41/`。
- Host 编排目录：对照基线无新增失败（2 条旧失败转绿）。

### 2A.4 遗留（不阻断本批，已排入后续批）

- 根结论提交后、保障层收尾期间误报一次"卡住"（`HierarchicalMissionStalled`）→ 第 2B 批推进规则一并处理。
- `taskgraph.diff` 请求不存在的修订返回 GRAPH_INTEGRITY（应是"修订不存在"类的明确错误）→ 第 3 批只读接口。
- 2A.1g 循环空转、2A.1i 保障层下操作提案审查未接通、重规划方法实例 id 冲突、2A.1c 界面问题 → 按原计划排在第 2B/4 批。

## 插入项（用户 2026-09-27 21:00 指定先做）：保障层开启时带发布的任务能走完

- 用户指示：先修"默认配置下带发布的任务提交操作被拒"；开发阶段不考虑"影响正常使用"。
- 根因（即 2A.1i）：保障层要求每类审查在其范围上有已批准的检查策略；Host 只自动批准内容审查和终审两类，SDK 对外批准接口与无损映射也只认这两类；操作提案四项被声明成"必须由已注册检查器出结果"的确定性检查，但系统里没有这种检查器，所以谁都批不了。
- 独立裁决（opus）：**方案 C**——四项本是代码已强制的事实复述（冻结时、审阅构建时、物化时三道关卡都会拒绝不一致），改由审阅员对冻结事实作语义判断；方案 A（为提案新造检查器、把整条检查使用链路从"结果"推广到"文件"）改动大、收益为零。另指出两案都绕不开：保障层导入的正式记录把语义通过记成"未知"、不带证据引用，物化复核会拒——必须同时修。
- 实现（SDK opt.42，803efd07；Host 钉版 df836962）：
  - SDK 门面与无损映射放开"操作提案""操作结果"两类（结果类带效果键），只在拥有该效果的范围上成立；
  - 提案四项改为语义判断（说明写明理由）；三道代码关卡一个未删；
  - 物化复核新增保障层分支：从已认证清单读有效等级（必须全部通过且总结论为接受），四张检查回执直接按任务意图查出后照旧严格复核；
  - 保障层提案审阅材料加入"将要发布的已验收文件"；
  - Host 策略投影：每轮为拥有效果的范围自动批准这两类策略（系统身份记账）。
- 测试：保障层/操作/提案相关 357 条全过；新增映射正反例与四项口径测试；Host 保障层相关测试通过。
- 真机复验（保障层开）逐层暴露并修复（每条都是这条路径此前从未真跑过留下的）：
  | 版本 | 真机现象 | 修复 |
  |---|---|---|
  | Host c09e8788 | 策略投影遇到旧任务"计划不一致"报错，整轮中断，新任务一条策略都没批 | 单个范围失败只记日志；已结束任务跳过 |
  | opt.43 | 点"提交此操作供审查"报 CAS_READ_INSIDE_TRANSACTION：保障层提案审查在提交事务里读材料文件 | 提交事务只存审查包与四张核对回执；循环里在事务外开审查轮（每个审查包只开一轮） |
  | opt.44 | 事务外开轮报"操作运行时不可用" | 先装好操作运行时 |
  | opt.45 | 每轮 BudgetExhausted：审查记账到效果所属的根复合任务（额度按设计为 0） | 操作类审查按准备该操作的叶子任务记账（与不走保障层时一致）；补做步骤接住所有异常 |
  | opt.46 | 结算核对 taskgraph_runtime_account_owner_mismatch | 结算推导记账主体用同一函数 |
  | opt.47 | 审阅员看不到连接器注册、条件写入核对事实，两项判"无法判定"，整体 INCONCLUSIVE（`mission-8ca58f3c68e4b13d`） | 四张系统核对回执（加任务 id）作为审阅材料 |
  | opt.48 | 操作结果审查已接受，结果验收被拒 USE_CERTIFICATE_REQUIRED（仍用旧的自签见证） | 保障层下结果验收在事务外准备使用许可证书、命令点名、与验收同事务提交；证书准备器支持操作结果用途 |
  | opt.49 | 终审已接受，根结论被拒"根效果要求失去已审证据"，任务失败（`mission-3a3a408ff73d122b`；文件已真实发布） | 保障层正式记录不带证据引用：改为核对证书认定的有效等级为通过；"终审须包含当前效果验收"的核对保留 |
  | opt.50 | 发布步骤把交付说明写成 `actions/delivery.json`，被当成未声明候选连拒三次，重规划失败，任务失败（`mission-eeb96754d8692d37`） | `actions/` 下未声明的 JSON 只有自称是操作（带 connector/operation）才按候选把关；读不懂的照旧拒绝；拒绝提示写明只有声明的候选文件可放在 `actions/` |
  | opt.51 | 第六局 MAX：提交→提案审查通过→界面批准→真实发布（`isolated-published/MAX.08ecc6644c73.v1.md`）→结果审查通过→结果验收通过，但最终审查判"无法确认"，任务失败：发布结果的验收记在根任务自己身上，不在最终审查拿到的子步骤验收里，审阅员只看到发布候选、看不到已执行的事实 | 最终审查材料补上根任务自己的已验收操作结果（当前有效的）及其发布读回回执；新增测试 1 个，保障层相关 222 个通过、两个接缝脚本通过；Host 钉 opt.51 |
  | opt.52 | 第七局 MIN：最终审查通过、根目标已解决、任务判定成功，但收尾一直停在"排空中"（原因：还有未释放的额度预留）：保障层的结算检查只认模型执行者的执行意图，发布动作没有执行意图，预留被永久持有 | 保障层下动作预留改用与执行图同一个"原始操作证明"检查（动作已真实生效或确认未生效、且无未知用量才放行，否则照旧持有）；新增测试 3 个；重启后同一任务预留结清→收尾通过→**任务完成** |
  | 测试环境配置 | 审阅员第一次调用后被判"费用未知"终止（`mission-4977bdaf6a4b83d3`）：中转线路刚开始回显 `deepseek/deepseek-v4.1-flash`，未登记的名字不被信任 | 隔离配置 `response_model_aliases` 追加该名字（非代码问题） |
- 第五局（`mission-62b5b1c24dbda5bc`，LEN.md）：操作失误——一次点击未送达，随后的 Tab/空格落到了"取消任务"上，任务被取消。之后每次按键前先截图确认焦点。
- 第六局（`mission-a3272b79f2d4d360`，写 MAX.md 并发布，opt.50）：发布及结果验收全部走通，最终审查因看不到发布事实判"无法确认"而失败 → opt.51。
- 第七局（`mission-e5f82ae8c9f24ff8`，写 MIN.md 并发布，opt.51→opt.52）：**全链路走通、任务完成**。界面新建任务→确认完成要求（内容一条、发布效果一条）→两步完成→界面选候选并提交→保障层提案审查通过→界面批准→真实发布 `isolated-published/MIN.7561516b7c84.v1.md`（内容正确：三句介绍 + 可运行例子）→结果审查通过→结果验收通过→最终审查通过（opt.51 修复生效）→根目标解决→收尾卡在未释放预留 → opt.52 重启后结清 → `MissionCompleted` + `AssuranceMissionFinalized`。
- 插入项结论：**默认配置（保障层开）下带发布的任务可以完整走完**。共修 11 处（Host 1 + SDK opt.42–opt.52）。
- 本项验证 = 相关定向测试 + 真机一局完成。**不跑全量回归**：用户 2026-09-28 确认，主体任务（全部批次）完成前，推送前只跑定向测试 + 真机，全量只在主体全部完成后跑。（曾误开一次全量，已跑完的 13 组无新增失败，其余被叫停，不作为正式回归。）
- 收尾期仍有一次"卡住"误报（`HierarchicalMissionStalled`，根目标已解决、等收尾时报）→ 第 2B 批推进规则处理。
- 界面记录（第 4 批）：同一候选文件在下拉框里出现两次（两步各产出一次 `actions/action_candidate.json`，名字相同分不清）；后台模式下下拉框按键会滚动页面而不是打开列表，需要让应用在前台再操作。
- 界面（第 4 批）：新建/打开任务后前端经常卡住数秒到很久（后台 CPU 约 10%，不是后台卡），需重启应用；下拉框要"按下打开→再按下→空格"分三次操作。


## 第二批 2B：推进规则接入原循环（2026-09-28 起，进行中）

- 测试节奏：只跑定向测试 + 真机，不跑全量（见总览说明）。
- 2B.1 已修（未发版）：重规划时模型选了与当前在用完全相同的方法和参数，系统派生的方法实例编号撞库，被记成"内部错误"（真机 `mission-bc2c094e9dc5e3b5`）。改为提交前识别：拒绝码 `REPAIR_NOT_ALLOWED`，说明写明"修复必须换方法或换参数"，整次提交回滚。新增测试 1 个；修复相关 7 个测试文件无新增失败。
- 2B.2 梳理完成（子代理只读分析，opus）：结论与建议已采纳，要点——
  - 2A.1g 空转确认是"主循环不返回"一类：事发 11 分钟事件表零增长；主循环只要任一处有在途工作就不返回，Host 的自动授权/策略批准/执行图启用/自动确认都排在返回之后，一个任务的长回合会压住所有任务的授权。
  - 卡住判定的"记录"与"确认"两处各有一份合法等待清单，已不一致；还漏了：结果未知的动作、待人批准、根目标已解决未判定、修复续接等待中、收尾中（仅记录处漏）。
  - "有进展"可能在无持久变化时上报（收敛唤醒心跳、保障层待办只改行版本等）。
- 2B.3 已做（SDK 待发 opt.53）：
  - 主循环新增"宿主周期职责"挂钩 `set_between_cycles`：每轮之间按间隔调用宿主职责，失败只记日志；Host 把自动授权等四项注册上去（间隔 = 活跃轮询间隔 2 秒），`run()` 返回后照旧也做一遍。
  - 新增 `orchestrator/progress.py`（纯函数，不读库不写库）：`idle_verdict` 把空闲任务判成"带具名唤醒来源的等待"或"卡住候选"；卡住记录与卡住确认共用它（确认处仍只读一次计划）。新增等待：收尾中、根目标已解决待判定、在跑、结果未知核对中、待人批准、操作结果待定、保障层待办、修复续接等待、规划等待、执行图来源等待；归不了类的一律"等待 + 需系统诊断"，不再默认判卡住。
  - 主循环用持久水位（非观察类事件序号 + 意图/尝试/结果行）识别"声称有进展但什么都没写"的轮次：照样计入轮次上限（原有判定不变），但必须睡一个轮询间隔，不再空转占满 CPU。
  - 保留原决定：保障层待办转"需人工处理"仍按卡住处理（界面没有处理入口，挂起更糟）。
  - 测试：新增 SDK 23 个（推进判定 16、宿主职责与空转 4、实例冲突 1 等），Host 1 个；卡住相关 17 个测试文件、代表性主循环 10 个文件均无新增失败（失败项都在改动前名单）。
