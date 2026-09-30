# NEXT-TG-1.0 进度（plan-status）

**2026-09-29 最新交接：[HANDOFF-2026-09-29.md](HANDOFF-2026-09-29.md)（阶段完成标准剩余缺口与接手顺序）。** 后续 Agent 先读它，再读 [HANDOFF-2026-09-28.md](HANDOFF-2026-09-28.md)（最新交接：基线、剩余批次、规则、操作配方），再读本文件与 [NEXT-TG-1.0-计划.md](NEXT-TG-1.0-计划.md)（PlanAgent 方案，已获用户同意执行）；[HANDOFF.md](HANDOFF.md) 是 09-27 开工前背景。每完成一步就更新本文件：做了什么、证据在哪、还剩什么。

## 总览

> 测试节奏（用户 2026-09-28 确认）：全部批次做完之前，每批/每次推送前只跑相关定向测试 + 真机；**全量回归只在全部批次完成后跑**。下文 2A.5 是此前的做法，不再沿用。

| 批 | 内容 | 状态 | 最后更新 |
|---|---|---|---|
| 0 | 开工检查与第一份回报（计划 §14） | 完成 | 2026-09-27 |
| 1 | 缺陷修复：收集拒绝隔离、输入清单同源、CI 哈希 | **完成并已推送**（de8a5bcc / 491ff0ad / 6d20b7f0，SDK opt.33）；CI 前端、后端测试通过；「开源卫生检查」失败在推送前的 4a1678ae 就已存在，未处理 | 2026-09-27 |
| 2A | 合法重建来源清单 + 新任务默认严格 TaskGraph | **完成并已推送**：上游核心链真实跑通（opt.39，任务完成 + 冷重放一致）→ 部署清单合法重建；新任务默认严格执行图接入 Host；真机小验收（opt.41，保障层开）任务完成、四个执行图只读入口有回复 | 2026-09-27 |
| 2B | 推进规则（FAST/WAIT/SLOW/STOP）接入原循环 | 完成（2026-09-28，SDK opt.53–55，待推送） | 见下文「第二批 2B」 |
| 3 | SDK 正式执行过程只读接口 + 默认执行图 | 完成（2026-09-28，SDK opt.56–57，待推送）；界面点击未运行（桌面操控未获授权） | 见下文「第三批」 |
| 4 | 产品入口与已完成能力接通 | 完成（2026-09-28，待推送）；委派、模型路由、并发调高记欠项；真机点击未运行 | 见下文「第四批」 |
| 5A | ARP MISSION 精确来源 | 完成（2026-09-28，SDK opt.58–59，待推送）；真实模型任务的全部 Agent 以任务模式创建 | 见下文「第五批 A」 |
| 5B | 统一能力目录与 Skill 入口 | 完成（2026-09-28，SDK opt.59，待推送）；准入评估界面、任务 Agent 调用技能记欠项 | 见下文「第五批 B」 |
| 6 | 同制品跨层验收与交付 | 完成（2026-09-28，SDK opt.60–61，待推送）；全量回归无新增失败；强制退出真机发现并修复收尾缺陷；E3 真实模型与全部界面点击未运行 | 见下文「第六批」 |
| 收口 | 阶段完成标准剩余缺口（2026-09-29 起） | 第 1～4、6、7 项完成（第 6 项 09-30 经架构方案 C 走通）；第 5 项阻断（写明）；SDK opt.83～91；09-29 全量回归无新增失败 | 见下文「收口：阶段完成标准剩余缺口」 |

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
- 2B.4 真机（opt.53，内容任务 `mission-655daf8071519553`，写 ABSVAL.md）：两步都完成，终审审阅员的回复第一条准则理由 2069 字，超过 2000 字上限，被判"文本不合法"；格式修复那一轮只告诉它错误码 `TEXT_INVALID`，它原样再发一遍，修复额度用完，任务以"没有可执行的工作"失败。另观察到：等模型回合期间后台 CPU 持续 100%（每 50 毫秒跑一整轮，10 秒内零写入）——原有问题，事发 2A.1g 同源。
- 2B.5 已做（SDK 待发 opt.54）：
  - 等待分支退避：连续无写入的等待从轮询间隔起翻倍，上限 1 秒，有真实进展立即复位（测试用零间隔不受影响）。
  - 格式修复反馈写明规则：`TEXT_INVALID`（理由 1～2000 字、其他字段 1～2048 字）、数组大小、缺字段/多字段、枚举取值、版本号、JSON 形状等，逐码给出改法；测试钉住反馈里的上限与解码器一致。
  - 停止报告：终审回复两次都读不了时，报告里写明 `final_review.reason = REVIEW_FORMAT_REPAIR_EXHAUSTED`，不再只有"没有可执行的工作"。
  - "规划在途"不再把操作提案/结果审查回合算进去（根审仍算，避免并行规划让根审作废）。
  - 规划池冷却时的"延期规划"改存调度状态表，重启不丢，等待起点也保留。
  - 暂不改：运行时阻断来回唤醒、过期重试的修复请求——两者最终都消耗规划次数上限，不会无限调模型。
- 2B.6 已做（待发）：取消任务时偶发的 `TASKGRAPH_TERMINAL_TRANSACTION_REQUIRED`（每轮编排都报错）——"任务结束"事件在事务外写出，而执行图终态记录要求同一事务。改为需要执行图终态记录时，事件与记录一起在一个事务里写。新增测试 1 个（改动前必红）。
- 2B.7 真机（opt.54，内容任务 `mission-cde0847b29d7177f`，写 ROUND.md）：**完成**。途中终审一次格式被拒，按新反馈改正后通过；全程无"卡住"误报。但后台 CPU 仍约 100%：两个早先卡住的旧任务（等人重新提交操作）让 Host 一直用 2 秒短节拍，每次都重跑整套恢复。
- 2B.8 已做（SDK opt.55 / Host c32a38d6）：Host 记下每轮结束时的持久水位，与上一轮相同就改用 20 秒长节拍（人的点击、提交照样立即唤醒）；SDK 公开 `durable_watermark()`。重启后 CPU 由持续约 100% 降到约 10%，每约 20 秒一次短峰。新增 Host 测试 1 个。
- 2B.9 决定不改：方法合成不需要规划授权——合成不提交计划、不受严格执行图约束；新任务第一步常是合成，那时还没有可授权的规划请求，加授权门会互锁。
- 2B.10 验收测试（子代理 opus 补写，只加测试）：计划 §7.3 第 1 条（链式三步含一条数据依赖一条顺序依赖，完成后不再请求规划器、计划版本不变）、第 3 条（同一结构缺口重复收集/重复敲入口/冷重开都只有一条请求一轮规划）、第 4 条（C 等数据或待细化时 D 照常派发）、第 6 条（未知费用按上限结清后未知动作状态不变、不重发、预算守恒）共 7 个用例全过，未发现产品缺陷。第 2 条（同合同返工）与第 7 条（各中断点恢复）沿用已有测试（旧路径与执行图恢复测试，后者部分在改动前失败名单里）。第 5 条由 2B.3 的空闲判定与空转测试覆盖。
- 第二批 B 结论：推进规则以"纯函数空闲判定 + 宿主周期职责 + 持久水位"三件落地，没有另建规则引擎；`_decide` 的按范围推进经第 4 条测试证实本就不被其他分支阻断，未重写。遗留：运行时阻断来回唤醒、过期重试修复请求无单独上限（受规划次数上限兜底）。


## 第三批：SDK 正式执行过程只读接口 + 默认执行图（2026-09-28）

| 项 | 做了什么 | 证据 |
|---|---|---|
| SDK 接口 | `api/taskgraph.py` 新增 `execution_snapshot`（严格执行图 + 执行过程，同一个 `Store.read_view`；键集分页，游标绑定任务/调用者/计划版本/清单哈希/执行内容哈希，变了报 `SNAPSHOT_CHANGED`，心跳不影响）与 `execution_detail`（按执行意图绑定的执行池、精确 agent 读回合记录；白名单：模型可见原话、工具名与整形后的工具事实、提交摘要、审阅结论理由；不输出 instructions / user_input / feedback / metadata / 思考块，全部过密钥脱敏）。投影模块 `orchestrator/taskgraph_execution_view.py`：节点 attempt / check / review / planning / repair_request / plan_revision / operation，边 attempt_of / rework_of / review_of / reviews / repair_requested / decision_for / retry_authorized / committed_as / supersedes / operation_of，全部用记录下来的身份连接（意图配置、尝试行、结果行、提交回执、修补事件），不进调度图。另返回每个步骤的方法步骤名与职责（`occurrence_labels`） | SDK 测试 `taskgraph_exec/test_execution_view.py`（真实编排：启用、规划提交、执行、审阅；同一令牌、边、详情、分页、篡改游标、只读无副作用）、`test_execution_view_edges.py`（返工回路的因果边、白名单）；变异：去掉思考过滤、去掉返工边各红一次 |
| 遗留一并修 | 已要求严格执行图但尚未启用的任务读图报 `ACTIVATION_PENDING`；`diff` 请求不存在的修订报 `REVISION_NOT_FOUND`（原为 GRAPH_INTEGRITY，2A.4） | 同上 |
| Host | 控制通道新增 `taskgraph.execution_snapshot` / `taskgraph.execution_detail`（只校验请求、查归属、原样映射 SDK 拒绝码）；删除直读 SDK 表的 `live_graph.py` 与 `mission_live_graph` / `mission_planning_decisions` 动词 | `test_taskgraph_execution_reads.py` 8 条、路由与动词清单测试 |
| 前端 | `liveGraph/` 重写：复合步骤为框，每个步骤框里是"执行、审阅、修补请求、规划、再执行"链，再执行画成红色箭头；步骤卡片"状态 + 一句话进展"；「时间线」标签按时间列同一份执行过程；点节点读回合详情；未启用/启用中如实说明；读不到保留旧画面并标"可能已过期"；执行过程与状态都没变不换画面、节点集合不变不重新排版 | vitest 18 条；提交态（临时工作树）任务页 + 执行图 112 条通过 |
| 独立审阅（opus，1 轮） | 阻断 1 项：回合详情会输出兜底类 `feedback` 记录（SDK 强制上下文控制消息带指令原文），opt.57 去掉该类，并去掉大工具结果的预览副本重复 | 非阻断备注：断线后前端停在"正在读取"没有断线提示（记第 4 批界面） |
| 真实控制通道 | 隔离应用 opt.57 开发模式启动一次，只读读取 4 个真实任务：返工三次的 ABS 任务 18 个执行节点（4 次执行、4 次审阅、3 个修补请求、6 次规划）19 条边；带发布的 MIN 任务含 3 个审查与 1 个发布操作；卡住的 ZIP 任务 11 节点；未启用的旧任务返回 NOT_ENABLED。回合详情 9 次读取全部 COMPLETE；输出扫描无系统提示词、思考、密钥、内部绝对路径。读完已按正常模式重启 | `.local-test-evidence/2026-09-28/batch3/execution-reads.json`、`read_execution_graph.py` |
| 真机点击 | 第六批补做：打开任务、执行图、时间线、步骤/执行详情通过；画布上直接点节点未验证（见第六批补） | — |
| 本机任务过程视图 | 未提交文件保持未提交；因 `live_graph.py` 删除，把它的三个读取助手搬进本机 `story.py`、测试助手搬进 `test_mission_story.py`，本机任务页默认标签改为「执行图」（「任务过程」为第二个标签）。改前备份 `.local-test-evidence/2026-09-28/batch3/dirty-backup/` | 本机任务页与任务过程测试 108 条通过 |
| 提交 | SDK b53fd1d5（opt.56；该提交的暂存区里顺带带上了 Host 的 live_graph.py 删除）、Host 55b8db2d、SDK 4e0c32bf（opt.57）、Host dd3581fa | 未推送 |


## 第四批：产品入口与已完成能力接通（2026-09-28）

| 条目 | 结果 | 说明 / 证据 |
|---|---|---|
| 工具熔断与自动续跑 | **接通** | 从监工开关下拆出，新 `[self_healing]`（默认开）；监工关时自动续跑用按失败原因的固定提示（`DeterministicResumePolicy`，不调模型），次数仍受 `max_auto_resume_attempts`；隔离环境启动日志确认两者在监工关闭时已装配。测试 `test_self_healing_without_supervisor.py` + 原熔断/续跑测试 31 条 |
| 写着开却无人读取的配置 | **删除** | plan_confirm_gate / subagent_driver / compaction_enabled / extractor_fallback_enabled / outline_preview_default / interrupt_enabled / strict_unknown_toolset（grep 后端与前端均无读取方）；相关测试改为断言已删 |
| 发布目录设置入口 | **接通** | 设置页「任务发布目录」；后台 `orchestration_publish_dir_get/set`：校验存在、是目录、能硬链接、不与应用数据目录重叠（两个方向），写回 config.toml，重启编排服务生效；空路径撤销授权，已发布文件不动。测试后台 5 条、前端 3 条 |
| 任务中途增删资料 | 已接通（核对） | 前端 `MissionDocument` 已调用 `mission_source_register/supersede/revoke`；代码类任务领域没有资料根，不适用 |
| 主 Agent 在聊天里发起任务 | **接通** | 新工具 `mission_start`（直接可见），走任务页同一个 `create_mission`（门口检查、预算默认值、严格执行图要求）；幂等键由对话回合+调用派生，重放不重复创建；编排不可用时明确失败。测试 7 条；工具目录相关测试 10 条旧失败与改动前相同 |
| 委派工具 | **下架 + 欠项** | agent / agent_parallel / spawn_team / spawn_subagents / await_subagents 在前台运行里没有执行入口（一调就回 delegation_unavailable），不再放进模型可见目录；需要后台做的事走 `mission_start`。**欠项**：把子运行执行入口接进前台运行 |
| 模型路由 | **不接 + 欠项** | SDK 路由规则与"每个任务自选上下文池"在创建时互相校验冲突，且目前只有一个模型可路由；需要第二个模型或放弃按任务选池后再接 |
| 多任务并发 | **保持 1 + 欠项** | 试过默认改 2：并发上限属于模型调用准入身份，SDK 启动时逐条核对全部历史执行意图，已有数据目录的编排服务起不来（隔离环境实测"provider admission identity differs"），已撤回。**欠项**：SDK 把并发上限从准入身份中拆出（只核对未结束意图）后再调高。另记：旧平铺路径（只在测试场景用）在并发 2 时有"管理者介入前又派一次重试"的竞态 |
| 多候选择优 / 冲突仲裁 | **保持默认 + 说明** | 候选数只能由策略晋升提高（用户已关）；冲突仲裁会新建不在计划图里的任务，与严格执行图冲突，默认预留保持 0，手填预留仍对旧式任务生效 |
| 普通中文写"发布到…" | **修（界面）** | 系统只认 `action:file_publish.publish:<文件名>`（执行步骤的候选格式说明和候选范围检查都依赖它）。新建任务表单加「完成后发布文件」自动生成这一行；普通中文提到发布时就地提醒；发布目录未授权时说明 |
| 下拉框难用、候选同名两条 | **修** | 完成要求的所属目标/完成标准改单选按钮，只有一个选项时自动选好；操作候选改单选列表，每条写明"操作 → 目标"和理由 |
| 执行图断线、启用故障提示 | **修** | 断线保留旧画面并提示；任务详情里的执行图启用故障直接显示原因 |
| 界面卡顿 / 列表不刷新 | **部分修（未能实机复现）** | 实测任务列表一次约 0.35 秒、任务详情约 0.6 秒，都占住后台事件循环，而任务运行时每次推送都会重拉。已改：已结束任务不再读整份事件记录；推送没改变任何任务状态时列表最多 5 秒重拉一次。列表不刷新、详情偶发空白的确切原因未在实机复现 |
| 顺带 | — | 浏览器预览入口（`?secret=`）恢复可用；但任务页需要桌面壳签发的身份凭据，浏览器里无法操作任务页 |
| 真机点击 | 第六批补做（见第六批补）；新建任务表单与操作工作区未点 | — |
| 提交 | 9e9d5b87、99e643fe、493ad9e5、034c48ac、ff45806d+76b033f0（撤回）、36697423、b98cea43、f927c97f、3cff1344、a4c08153、4fd3e08f | 未推送 |
| 独立审阅（opus，1 轮） | 阻断 1 项：自动续跑取会话活动记录，后台从未注册（旧缺陷，监工开时同样），续跑累加次数时报错、上限失效。4fd3e08f 启动时总是注册，测试改用真实记录；非阻断：慢定时器等待中状态变化也要等 5 秒，已一并改为 1 秒节奏 | 其余 6 项无阻断 |
| 失误记录 | 应用列表优化补丁时写文件先截断后读取，本机 service.py 被清空；已按"已提交版本 + 本机任务过程视图那 4 行"恢复，与出错前一致 | — |


## 第五批 A：ARP 任务模式按精确来源创建 Agent（2026-09-28）

| 项 | 做了什么 | 证据 |
|---|---|---|
| SDK（ARP 侧） | 任务模式（MISSION）创建必须经"任务来源读取器"读出按角色区分的来源集：执行者（执行尝试 + 冻结输入清单身份：步骤、合同哈希、输入绑定修订、派发代次、清单哈希；非执行图任务写明原因）、审阅者（审阅包目的/哈希/要求哈希/判据）、单步审阅（被审尝试）、终判（终判视图 + 编排器原有终判检查）、规划者/方法合成/服务回合（写明"没有执行尝试"，不借用）。缺读取器、指向别的意图、未知类型、Agent 配置与意图冻结的不同、创建时已失效，都具名拒绝且不写入任何行。来源集按会话写成不可变回执，哈希进入创建命令哈希：同一创建键不能重放到别的任务/步骤/输入。每次新请求冻结前复核，来源钉写进上下文清单的授权引用与读取集；已冻结的请求原样重放（冻结后恢复）。委派子 Agent 继承父会话来源。会话的"所有者合同"按其创建时的配置修订读取 | `tests/agents/arp/test_arp_mission_mode.py` 6 条 |
| SDK（编排器侧） | `runtime/mission_sources.py` 只读编排器自己的记录（派发意图、尝试、经已装配的执行图读取器取冻结输入、审阅包）；复核 = 重新派生并比对 + 意图未停止 + 任务未结束 + 原有的执行图交接检查与审阅交接检查（不重复实现租约/预算，它们在交付模型前照旧检查） | 真实严格执行图世界：执行者模型调用进行中探针——伪造调用方（别的任务/不存在的意图/非派发回执）、别的步骤、输入清单被换、旧派发代次、任务被暂停（事务内暂停后回滚）均具名拒绝；兄弟分支变化不误杀；意图结束后拒绝；规划者合法路径；审阅包目的不符拒绝；终判视图绑定；创建被拒不再打断编排循环 |
| Host | 编排执行池 ARP 配置改为 MISSION，**配置修订 2**（旧库里修订 1 的独立模式配置行原样保留，旧会话照常运行；配置行从不原地改），接入 SDK 的任务来源读取器 | Host 测试：池为 MISSION 修订 2；真实方法合成会话记录了来源、冻结请求带来源钉；隔离环境旧库（586 个旧会话）以新版本启动正常，四个原生池都新增修订 2、修订 1 保留；**真实模型任务** `mission-e8a294d104b20b8f` 的 8 个 Agent（方法合成、执行者两次、审阅三次、规划、终审）全部以任务模式创建，每个恰好 1 份来源回执 |
| 独立审阅（opus，1 轮） | 阻断 1 项：终判审阅者（其"尝试"是终判视图）会被当伪造拒绝，且拒绝抛出编排循环、所有任务一起卡住。已修：终判按原有终判检查绑定；`_dispatch` 接住创建时的具名拒绝，只停该意图并记一次说明。非阻断采纳：来源集带意图冻结的 Agent 配置哈希，创建时核对 | 变异：去掉冻结前复核、去掉命令哈希里的来源、去掉创建时复核、去掉派生比对、去掉执行图交接检查、去掉审阅目的检查、去掉回执比对、去掉终判分支、去掉循环保护、去掉配置核对，各红一次 |
| 已知窄窗口 | 升级那一刻若有意图停在"已认领未建 Agent"且留有修订 1 的创建记录，重建会被拒（现在只停该意图，不再卡循环）；开发期不做兼容 | — |
| 提交 | SDK c179582a（opt.58）、Host 9f01e9a5；审阅修复并入 SDK 4dd6c75f（opt.59）、Host 25997025 | 未推送 |

## 第五批 B：同部署统一技能目录与技能入口（2026-09-28）

| 项 | 做了什么 | 证据 |
|---|---|---|
| 设计 | SDK 的目录把"命名空间级条目"（技能版本与状态）和"会话级记录"（工具暴露、能力绑定、技能使用）放在同一执行库里，直接让所有池共用一个物理目录会把会话记录拆到两个库。改为：**一个所有者池的目录是唯一权威**，其余池保存镜像，使用前一律先问所有者 | — |
| SDK | `arp/shared_catalogue.py`：所有技能命令只在所有者执行；成员池拒绝直接安装/试用/准入/暂停/评估派发（`REF_OUTSIDE_SCOPE`）；同步时用所有者的原始包字节导入，得到同一技能/版本/哈希，状态沿原状态机跟随，"准入"复制所有者已记录的正式验收（从所有者自己的记录读，不由调用方提供）；成员按自己的工具核对依赖，缺工具就在该池不可用；每次使用先问所有者同一版本是否可用——所有者一次暂停，所有池下一次使用立即被拒（不等同步）；重启后同步幂等；另有总览/安装/暂停恢复退役三个入口供 Host 用 | `tests/agents/arp/test_arp_shared_catalogue.py` 5 条（一次导入两池同版本同验收、各池各自使用各记各的、所有者暂停后两池下一次使用被拒、恢复后可用、成员池直接写被拒、未准入版本不可用、缺工具的池不可用、重启后一致、入口只收技能动词）；变异 3 处各红一次 |
| Host | 256K 非思考池为目录所有者，其余原生池为成员；编排器装好后同步一次；新 `skill_catalogue.py`：总览、从本地 .zip 安装（放入产物库→所有者安装→镜像）、暂停/恢复/退役；`agent_runtime_request` 里的技能动词一律走统一目录；技能评估派发记在所有者上；控制通道只放行三个明确列出的 `agent_*` 技能动词，会话销毁等其它 `agent_*` 仍不放行 | Host 测试 2 条 + 消息清单测试；**真实控制通道**（隔离应用开发模式）：四个原生池（含两个思考池）一次安装同版本、重复安装同一条、未准入版本暂停被拒、退役四池同步、未列出的动词不路由（`.local-test-evidence/2026-09-28/batch5/skill-probe.json`） |
| 界面 | 设置页新增「任务技能目录」：列出技能与各档位能否使用（缺工具写明原因）、从本地 .zip 路径安装、暂停/恢复/退役（退役先确认）、被拒显示原因 | vitest 4 条 |
| 欠项 | ① 准入评估（试用→评估任务→准入）还没接到界面，安装后显示"待评估"；② 任务里的执行者当前工具集不含技能工具，统一目录里的技能还不能被任务 Agent 调用（需在执行策略里开放技能工具，会改变工具集，按"改工具说明会让旧执行池起不来"的教训单独做）；③ 真机点击：第六批补做，安装/退役通过 | — |
| 提交 | SDK 4dd6c75f（opt.59）、Host 25997025、界面 a021c174 | 未推送 |

## 第六批：跨层验收与交付（2026-09-28）

| 项 | 结果 | 证据 |
|---|---|---|
| E1–E8 场景 | 八项逐一对应确定性测试与真实线路证据；E3（结构修复）真实模型未运行；界面点击第六批补做（画布直接点节点未验证） | `.local-test-evidence/2026-09-28/batch6/E-SCENARIOS.md` |
| 真实模型任务 | `mission-e8a294d104b20b8f`（DeepSeek，默认思考档）：方法合成→执行→审阅→修补规划→再执行→审阅→终审→**完成**；8 个 Agent 全部以任务模式创建，每个 1 份来源回执 | `batch6/e5-after.txt` |
| 强制退出（E5） | 在第一次尝试的内容审阅模型调用途中 `kill -9` 后台：执行不重复创建、被打断的调用不重发、步骤重做后判定成功。**真机发现缺陷**：那条"等待原调用核对"的审阅意图在 opt.59 进程里让收尾停在"排空中"约 21 分钟（任务已判定成功）；再重启一次后原有回收流程把它判失败，收尾就绪、费用按上限计入、任务完成。为不依赖重启，SDK opt.60/61 让"只等无法回答的原调用核对、费用未知"的审阅意图不再阻塞已判定任务的收尾。独立审阅（opus，1 轮）指出上一版测试只测了辅助函数、且真机完成其实是重启回收促成的——已把收尾判定抽成与收尾评估同一个函数并补 3 种情况的测试（三处变异各红），记录如实改写：**这条新路径没有在真机上单独复现** | `batch6/e5-before-kill.txt`、`e5-after.txt`；`test_unknown_usage_upper_bound.py` 新增 2 条 |
| 多任务隔离（E6） | 两个等人重新提交操作的旧任务始终活动，新任务照常完成；另一个测试任务因目标含"发布/上线"字样，自动确认按设计留给人确认（已取消，是测试用语问题） | 同上 |
| 全量回归（推送前一次） | SDK、Host、前端**均无本次引入的失败**；SDK 编排比改动前少 18 个失败；前端 937 条全过、类型检查通过；opt.61 后改动所在目录再跑一次仍无新增 | `batch6/REGRESSION.md`、`full-1/` |
| 制品身份 | SDK opt.61（wheel 哈希与声明一致，源提交 da122cb6），已安装包与内嵌源码 733 个文件逐字节一致；隔离环境配置哈希已记录 | `batch6/artifact-identity.txt` |
| 遗留：两个卡住的旧任务 | 结论：它们在等人重新提交操作，是合法的等待；第二批已让它们不再占满 CPU，本批真机证明它们不阻塞新任务。属于用户数据，不替用户取消 | — |
| 遗留：运行时阻断来回唤醒、过期重试修复请求无单独上限 | 结论：不另设上限。每轮规划请求次数（`max_planning_attempts`）、根审阅修复次数（`max_root_review_repairs`）与任务令牌预算共同约束，不会无限调用模型；记为决定 | — |
| 真机点击 | 用户授权后补做（见下节） | — |
| 提交 | SDK 19e549f2（opt.60）、da122cb6（opt.61）；Host d1630b81、ecd16064；证据与本记录 afb3e89c、f16e0431 | 已推送 |

### 第六批补：真机点击（2026-09-28 中午，用户授权后）

隔离应用（opt.61 调试版），后台模拟鼠标点击，逐项结果见 `.local-test-evidence/2026-09-28/batch6/E-SCENARIOS.md` 文末。

| 项 | 结果 |
|---|---|
| 任务详情、执行图（两次执行 + 审阅 + 修补 + 终审）、时间线展开回合详情 | 通过 |
| 全部步骤 → 步骤详情（执行过程 6 条）→ 单次执行详情（模型回复、工具调用、提交候选） | 通过 |
| 画布上直接点节点 | 真机发现**真实缺陷**：节点不可选/不可拖时 React Flow 让节点收不到鼠标，点标题没反应（此前以为是点击工具问题；用户开程序坞自动隐藏后前台点击才到达画布，才看出来）。修 `LiveGraph.css`（节点内按钮单独开 pointer-events）；重建后真机点执行节点、步骤框、终审节点都打开详情。另修已完成步骤详情仍写"未选入执行"（vitest +1，变异红）。执行图与任务页 118 条通过、类型检查通过。CSS 修复没有自动测试（测试环境不渲染画布），靠真机验证 |
| 设置：发布目录已授权显示；技能目录从 .zip 安装（四档位同一版本"待评估"）、退役（确认框后四档位同时"已退役"） | 通过 |
| 新建任务表单发布助手、操作工作区 | 未运行 |
| 真机发现并修（界面） | ① 修补请求显示审阅层英文原文 → 换成大白话；② 已完成的根步骤框下面写"等待拆分" → 已结束的步骤不再显示就绪原因。vitest 新增 1 条（去掉判断即红），执行图与任务页 117 条通过、类型检查通过；重建应用真机复看两处都已正确 |
| 未改的小问题 | 技能安装成功后路径输入框不清空 |

### 第六批补二：复杂真机场景（2026-09-28 下午）

wordfreq 三文件 + 两个发布 + 中途重启，DeepSeek 真实模型。**任务失败（规划次数 12 用完，其中 5 次是模型服务端错误与重启打断）**，4 步完成 3 步，未走到提交/批准发布。新建任务表单发布提醒/助手、两个效果的操作工作区真机点通。真机发现并修：执行图三处文字、操作候选按目标文件筛选（Host f362511d）、**快速重启后规划回合永久挂住**（SDK opt.62 c33c2d90，真机快速重启验证 1 秒内接手）。待决定：服务端错误/重启打断是否计入规划上限；有调用在跑时后台 CPU 满载；接手后重发调用冲突。详见 `.local-test-evidence/2026-09-28/batch7/README.md`。

## 收口：阶段完成标准剩余缺口（2026-09-29 起）

接手自 [HANDOFF-2026-09-29.md](HANDOFF-2026-09-29.md) 第 3 节。开工核对：main `b4a70ceb`（领先远端 1 个交接提交）；SDK opt.82 已安装包与内嵌源码逐文件一致，钉版哈希一致。回归对照基线 `.local-test-evidence/2026-09-29/final-opt80/`。

| 顺序 | 缺口 | 状态 | 做了什么 / 证据 |
|---|---|---|---|
| 1 | 任务里的执行者调用技能 | **完成**（SDK opt.83 `dd67cfe8`，Host `41f72a6a`）。真实模型：评估任务 `mission-ca430334ee233edb` 的两个执行者（模板 v5）在成员池里各装载/执行技能 2 次，任务完成；准入后的普通表格任务 `mission-33d8916ba4f070b8` 的执行者也装载了已准入技能，任务完成（`.local-test-evidence/2026-09-29/skills-real/`） | SDK：部署策略新增"技能工具"声明，只能是查目录、装载、执行三件，旧部署的文档字节不变。执行者层级模板 v5 = v4 原文 + 一段技能说明 + 三件技能工具；v4 保留原字节并冻结哈希，无人机模板改钉 v4。派发时所选执行池不是原生平面，就不冻结技能工具（旧式池没有这些工具）。Host：原生平面开启时，部署声明并放行这三件工具，新任务默认带上。每次使用照旧向统一目录所有者复核。测试：SDK `taskgraph_exec/test_worker_skills.py`，走真实派发，覆盖执行者查目录 → 执行已准入技能 → 暂停后下一次被拒，以及旧式池不冻结；Host `test_worker_skills_host.py`。3 处变异各自让测试失败。回归：SDK 执行图组 47 个失败 + 1 个报错，与基线逐条相同；ARP 组全过；Host 编排组 26 个失败，与基线逐条相同。已有数据目录在 opt.83 上正常启动。独立审阅（opus，1 轮）：无阻断 |
| 2 | 技能准入评估接界面 | **完成**（界面点击已在第 7 项补齐）（SDK opt.84 `b68e00ff`、opt.86 `5ae239c7`，Host/界面 `1f3ed823`、`8d16c34d`）。真实模型三轮：第一轮失败并修两处；第二轮"重新评估"后评估任务完成，但没认出"通过"，已修第三处（见下）；第三轮评估显示通过→准入成功，四档位"已准入、可用"。真机点击未运行（本次桌面操控未获授权，改走设置页用的同一控制通道消息）；前端 6 条单元测试覆盖按钮与状态 | 设计取舍：按技能目录规格第 3 节"评估任务真实执行技能"。原 SDK 只允许已准入的技能被使用，评估任务用不了试用中的技能。改为**试用中的技能只能被它自己的评估任务使用**，条件是：评估派发链接指向的就是这个任务、试用未过期、目录所有者状态为试用。每次使用都复核；别的任务、没有任务的会话、过期、暂停都拒绝；发现页只在该任务里把它标为可用。Host 两个动作：「开始评估」= 开始试用 + 用评估专用幂等键建评估任务 + 当即挂到该任务的根步骤（根步骤语义在建任务时就写入，所以执行者第一次调用就能用）；「准入」= 取根步骤当前验收的"可用"证书，交给 SDK 核对整条链，不自己判通过。评估失败、取消或过期后可"重新评估"：先暂停旧试用，再开新的。设置页：状态用大白话，有开始评估、准入、重新评估、刷新四个按钮。测试：SDK 共享目录 2 条（成员池也只在评估任务里可用；过期即拒）；执行图真实派发 1 条（挂接前拒绝，挂接后发现页标为可用并能执行）；Host `test_skill_evaluation_host.py` 2 条；前端 6 条。变异 4 处，3 处让测试失败；"所有者侧再核对任务"那处没有单独失败，因为成员池本地判断时已经问过所有者，它属于第二道保险 |
| 3 | 多任务并发按真实额度放开 | **完成**（SDK opt.85 `3eda9aef`，Host `211c889f`）。真实模型：名额 2（本机转发批量名额同步设为 2）下，评估任务与普通任务的方法合成请求同一秒提交、同时运行，两任务都完成；09-28 在名额 2 下起不来的隔离数据目录，现在正常启动。独立审阅（opus，1 轮）：无阻断 | 根因：模型调用准入身份的哈希里含"物理名额数"，每条冻结的执行意图都带着它；改名额后全部历史意图对不上，启动核对拒绝，编排服务起不来（09-28 实测）。做法：名额数是容量，不影响单个请求怎么记账，于是准入身份 v3 不再包含名额数；旧版冻结的 v2 身份如果只差名额数（1～16，含各池名额变体），仍认作同一身份；估算器、协议或价格不同的照旧拒。授予记录与票据改用意图冻结时的身份，所以后续逐项相等核对都一致。"池里是否冻结过准入身份"的读取只区分有或没有，v2 与 v3 并存不算不一致。Host 默认并发由 1 改为 2（即 SDK 自身默认值）；配额更小的部署在 config.toml 里调低。测试：SDK `p35/test_admission_slot_change.py`：字节级核对旧 v2 身份；估算器不同必须拒；第一阶段模拟旧版、名额 1、跑完任务，第二阶段新版、名额 2 能启动，两个任务同时占两个名额。变异（只认完全相同的身份）让测试失败。Host `test_concurrency_raise_host.py`：名额 1 冻结、名额 2 重启后可用。隔离真机数据目录（09-28 用名额 2 起不来的那个）在 opt.85、名额 2 下正常启动。回归：SDK p35（8 个失败）、p34（全过）、执行图组（47 个失败 + 1 个报错），都与基线逐条相同。Host 编排组新增 1 个失败：旧平铺路径夹具在名额 2 下多派一次重试。已知后续（具名）：**旧平铺路径在并发 2 时，前一次尝试的审阅还没结束就可能再派一次重试**。只有夹具场景走这条路，产品的分层路径不受影响；该夹具测试显式保持串行设置。另外，已有数据目录里"单个任务内并行步骤数"仍按当时写入的策略版本（为 1），因为用户关闭了策略晋升；多任务同时推进由名额决定，不受这点影响 |
| 4 | 委派工具接回 | **完成**（提交 `5e4af5ec` 及其后续；Host 委派服务 `deskpet/sdk_adapters/delegation.py`，方案与实施记录见 [委派工具接回-方案.md](委派工具接回-方案.md)） | 五个委派工具跑在真实 SDK 子运行上，回到模型可见目录，无项目对话也能用。真实模型：主对话调用 `agent`，子运行完成，主对话原样转达子助手结论；子运行回答只出现在工具结果里；4 次模型调用都记账、上下文用量只算主对话 3 次（`.local-test-evidence/2026-09-29/delegation-real/run1.*`）。独立审阅（opus，1 轮）2 条阻断 + 2 条次级已修；重启实测又发现并修 5 处（见下）；换上游后第 7 轮"子运行进行中重启"通过：父运行挂起 → 子运行跑完 → 父运行被唤醒并完成 → 结论写回主会话（`delegation-real/run7*`）。测试 24 条（服务层 21、接线 3）；Host 编排 + 适配层两组与基线逐条比对无新增失败 |
| 5 | 模型路由 | **阻断（写明阻断与影响）** | 核对结果：Host 没有给编排器传路由规则。现有 4 个原生池（256K/512K × 思考/不思考）是同一个 DeepSeek 模型，只差上下文大小和是否思考；另有 2 个旧式池。SDK 的设计是：任务一旦选定执行池（Host 给每个任务都选一个，默认 256K），规划、执行、审阅所有角色都固定在这个池，路由、升级、回退都不许离开，这样冻结请求的上下文身份和任务模式来源都留在同一个执行库里（`event_handler.py::_router_for`）。**阻断 1**：只有一个模型，"按角色换模型""失败后升级到更强模型""服务商坏了回退到另一家"都无处可路由。**阻断 2**：在同一模型的不同池之间按角色路由，与"每个任务自选上下文池、所有角色同池"冲突，要改这条设计。**影响**：所有角色用任务选的同一个池；反复失败时不会换更强的模型（靠方法合成、规划修补和重试次数兜住）；中转线路出错时原地重试（不扣次数，有宽限上限），不会切换服务商。**解除条件与建议**：配置第二个模型（例如 Grok 或 DeepSeek 官方 pro 线路；DeepSeek 官方端点用户已决定不做）之后，按角色在"同样上下文大小"的池之间路由（审阅、终判用更强的模型，执行者留在任务选的池），同时放宽上面那条设计，让审阅角色可以离开任务选定的池 |
| 6 | 结构修复场景用真实模型跑 | **完成**（2026-09-30 第 6 轮走通闭环，证据与结论 `.local-test-evidence/2026-09-29/e3-real/README.md`） | 第 6 轮（SDK opt.91，回答附成资料）：执行者报缺数据 → 修复请求 → 规划器问人 → 回答登记成资料 → 原方法重试 → 执行者读到文件写出正确报告 → **任务完成**（747 秒）。此前五轮：链路每一环都走到过：执行者如实报卡住 → 修复请求（影响范围正确、无关已通过贡献保留）→ 规划器提案（能读懂、会问人）→ 用户回答 → 再规划；没走到收敛，卡点是用户给的数据到不了执行者手里（第 6 轮已解）。途中真机发现并修 5 处：①系统代办发布没先装配操作运行时，带发布的任务内容完成后空转（SDK opt.87，修后同一任务发布成功、完成）；②规划决定"影响范围"写成文字整份被判读不懂（opt.88）；③④缺外部资料时任务直接失败不问人（用户同意的修法，opt.89）：规划器宣告受阻且没有方法合成可接手时转成阻塞式问题等用户回答，规划器模板 v12 = v11 + 修复轮缺外部资料用升级给人提问（同配第 8 版包，已绑 v11 的任务不变）；⑤用户回答只进规划器、执行者看不到（opt.90：回答同时记成任务级用户备注，每个执行者开工都能看到）。具名后续：规划器格式容错（目标类型引用缺版本号、修复轮加重试次数）；新增"按用户给的内容补一个前置步骤"的修复动作 |
| 7 | 界面真机补点 | **完成**（opt.90 上真实鼠标键盘，证据 `.local-test-evidence/2026-09-29/ui-clicks/README.md`，截图 11 张） | ①执行图画布点节点：步骤详情、系统发布步骤详情都能打开；②新建任务表单发布助手：自动加入发布那条成功条件，没填目标时提交被拦；③操作工作区：界面提交真实任务 → 确认区默认映射正确 → 确认 → 系统生成的发布申请卡片 → 点批准 → 任务完成、文件已发布；④设置页技能目录（补第 2 项）：界面安装新技能 → 开始评估 → 评估任务与普通任务并发跑完、执行者真实调用技能 4 次 → 评估已通过 → 准入 → 四档位可用。本次未发现新缺陷 |

委派接回重启实测（子运行跑到一半重启应用，共 5 轮，证据 `delegation-real/run2-*`～`run5-*`）发现并修：
- **关应用时去取消子运行**：等待被中断就取消子运行，取到已关闭的运行时报错。改为只有父运行真的结束或被用户停止才取消；关应用不取消，子运行重启后接着跑。
- **父运行重放委派调用被授权记录判冲突**：重启后重放会重新签发授权票据，Host 授权记录按"同一调用、不同授权编号"判冲突，父运行失败。改为从不重放：子运行还在跑就让父运行挂起，子运行结束后替父运行写"已完成 + 结论"并唤醒。
- **唤醒后找不到模型绑定**：启动恢复名单只含"运行中"和"卡在模型调用上"的等待运行。已加入"卡在委派调用上"的等待运行。
- **父运行完成了但结论没写回会话**：主对话的恢复展示在父运行转入等待时就退出了。唤醒后重新挂上。
- **子运行自己卡在等待**（重启后它的模型调用又撞上游超时，结果不明）：主对话有兜底，子运行没有。监视器发现子运行等待超过 2 分钟先请内核对账一次，再停 2 分钟取消，父运行拿到"已取消"的结果继续回答。

收口后的架构改动方案：[架构修改-方案.md](架构修改-方案.md)（第 2 版，2026-09-29；独立审阅 3 阻断 11 应改已按裁定改写）。**2026-09-30 进展**：用户同意 B 的两个前置按我的建议（终审加"资料版本变了"过时原因；修复请求由系统在影响范围全部重验通过/被替代时消费）和顺序 A、C 并行先做。**A 已完成**（Host `d7d95694`，测试 5 条 + 相关旧组 131 条，变异 4 条失败）；**C 已完成**（SDK opt.91 `2cb347db`、Host 钉版 `0395e4ed`、界面同 `d7d95694`；SDK 测试 1 条 + 相关组 39 条，前端 2 条，变异失败）；**第 6 项第 6 轮真实模型走通闭环，任务完成**（`run6.*`），收口第 6 项就此完成。**B 已完成**（SDK opt.92 `c8f17b2e`、Host 钉版 `af8a1503`）：资料换版本/撤销只对有证据的受影响对象发一条证据失效修复请求；终审切包记资料版本集、变了报 SOURCES_MOVED；修复请求新增系统消费出口。测试 3 条（真实严格执行图派发 1 + 终审 2），变异 3 处各自失败；相关组无新增失败。真实模型验证**通过**（`.local-test-evidence/2026-09-30/b-real/README.md`，`mission-f7cc803f42ccf0d4`）：跑到一半换资料 → 系统当轮发修复请求 → 旧数据的结果被审阅员按当前资料拒 → 规划器一次重试处理掉两条请求 → 新尝试用新数据通过 → 任务完成（报告 999）。后续已做（SDK opt.93）：尝试在跑时资料变了先不评估、等它跑完再评估（此前规划器两次回复 WAIT 被修复轮拒绝、白花两轮）；测试改成先失败后通过，相关组 136 条通过；真实模型第 2 局见 `b-real/run2.*`。D（换模型）**用户 2026-09-30 决定暂不做**。同日开始**老测试欠账清理**：基线（final-opt90）里约 244 条既有失败/报错按三类处理——环境问题修环境或明确跳过、测已移除功能/替身过时的更新或删除、疑似真问题逐条核实（交三个子代理分 SDK 编排组 / SDK 非编排组 / Host 做体力活，疑似真问题由我核）。

**老测试欠账清理（2026-09-30）**：基线里的既有失败按三类处理——环境问题修复或注明缺什么后跳过；过时断言与替身更新到现行契约；疑似真问题逐条核实。SDK（opt.94 `1115a7ab`）：编排组 84 条、非编排组 89 条；Host（`dab3b2a6`）：71 条 + 范围外 2 个文件。删除测试 3 条（Host，测的是已有意取消的行为）。核出并修的真问题 3 个：①工具网关在工具被取消后等待物理调用用 shield，Python 3.13+ 下调用报错原文会被打进事件循环错误日志（可能带出参数/密钥）→ 改用 asyncio.wait；②SDK 写死的依赖锁哈希没随依赖升级更新 → 已更新；③同步进 Host 时丢了来源锁文件与 H0 提示词记录 → 从独立仓库历史取回。按现行设计改写而非放宽：p35 离线备份按保障层"恢复后只读、绝不复活执行"；存储层约束按本意收窄为"只有 storage 能写"（读不算越界，0 处写越界）；自动模式故障用例按 09-07 决定断言直接放行。新增具名后续：脚本化模型替身迁到新规划协议（现在约 20 条服务机制用例走旧协议夹具通道，默认分层路径缺脚本化端到端覆盖）；恢复库重新授权后门面"读引用原文"仍拒绝（只认本机原生根）。清理后全量回归**全绿**（`.local-test-evidence/2026-09-30/final-opt94/结论.txt`）：SDK 19 组 0 失败 0 报错、Host 两组 0 失败（1217 通过）、前端 952 全过；以后回归直接看红不红，不再需要逐条对比。

**完成度评估后的修复（2026-09-30）**：评估报告 [完成度评估-2026-09-30.md](完成度评估-2026-09-30.md)。用户说"继续"，按我的建议先做第 1、2、6 项；待决事项按建议暂记——"任务过程"标签不动（非本会话代码，等用户定）、HTN §15 长期承诺与 P7 迁移快照/归档/删除标记暂不做、保障层"屏障 + 使用前重验"替代原设计的"脏标记 + 局部重算"。①（SDK opt.95 `d2eaec8b`）"修复轮拒绝 WAIT"的真因不是规则而是缺陷：规划器清单里同一步有内容哈希与合同哈希两个引用，规划器选合同哈希回 WAIT，"能不能等"只认内容哈希；现两种都认（同一步、同一修订、正在跑），错的照旧拒。同一规划请求格式重试用完就整局失败 → 改为规划总次数还有剩就开新请求（拒绝照样计入总次数，有界）。测试先失败后通过，相关 20 个文件 + 执行图目录共 515 条通过（3 条钉旧规则的已按新规则改）。⑥（Host `f6ff15a2`）装了原生池时新建任务不再列旧式池，指名旧式池被拒。②真实模型"出第 2 版计划"：严格引用两步任务，第一步通过后换资料版本，看规划器是否为已完成的一步提出后继步骤（`.local-test-evidence/2026-09-30/struct-repair/`）。

**用户决定（2026-09-30）**：①完成度评估的三件待决事项按我的建议："任务过程"标签改为走 SDK 正式接口并补过滤与脱敏（用户授权修改这批原未提交代码；按"不隐藏"原则不下架）；HTN §15 长期承诺与 P7 迁移快照/归档/删除标记暂不做；保障层"屏障 + 使用前重验"正式替代原设计的"脏标记 + 局部重算"。③开发阶段**直接删除旧式执行池**，不做兼容也不隐藏（今天上午"装了原生池就不列旧式池"的隐藏做法作废，改为删除）。

收口中新发现、与本项无关的具名欠项（未修）：
- **自动工作区目录名撞名**：目录名只取会话 ID 前 8 个字符（`project_binding.py::_prepare_automatic_workspace`），前缀相同的两个会话会撞同一目录，主对话直接报"唯一约束失败"。界面生成的会话 ID 是随机的，一般碰不到；测试会话名撞上了。
- **重启后重放工具调用会被授权记录拒绝**：任何工具被判"确认未开始"后重放，都会因授权票据重签、授权编号变化而判冲突，运行失败（Host 授权记录通用缺口）。委派已绕开；其它工具只有在"交出前被打断又重启"时才会遇到。

失误记录：做变异时用 `git checkout` 还原 `lifecycle.py`，把还没提交的新方法也一起还原掉了。已按原样补回，重跑通过；之后变异一律用副本还原。

真机发现并修（第 2 项第一轮评估任务 `mission-7eced23b63bfaf1b` 失败）：
- **方法合成回合没得到模型回复也算一次询问**：两次都以服务商"工具调用解析失败"结束，两问用完，整局失败。规划器早有"没回复的回合不算答错，宽限 6 次"，方法合成器没有。SDK opt.85 补齐：没回复的回合原地再问、不占询问次数，宽限 6 次，第一次真正的回答仍按第一问对待。测试 2 条，变异让测试失败。
- **评估任务目标里写了工具名**：没有工具的方法合成器看到 skill_discover 等名字，试着去调用。Host 改成只写要做什么；这三件工具只有执行者有。测试断言目标与要求里不含工具名。
- **内容类评估任务的"通过"认不出来**（第二轮 `mission-ca430334ee233edb` 已完成，页面仍显示未通过）：内容任务的根步骤没有验收行，整任务通过由最终独立审阅的根解析证书认定；SDK 技能验收读取器和 Host 取证书原来都只认步骤验收证书。SDK opt.86 让读取器也认根解析证书，并同样核对整条链：已采纳、当前、ACCEPT、审阅记录一致、步骤就是挂接的步骤、签发晚于挂接。Host 先取根解析证书，取不到再退回步骤验收证书。测试 3 条（通过；挂到别的步骤被拒；先签发后挂接被拒），根解析证书由原写入链产生（沿用终审写入缝合脚本的步骤，不伪造证书），变异让测试失败。
- 独立审阅（opus，第 2 轮，覆盖 opt.84～85 与 Host 对应提交）：无阻断。非阻断已处理：一条测试说明写得比测试实际覆盖的多，已改准确。非阻断记账：①旧配置各档位名额不相等时，旧 v2 身份认不出来（改动前就有；Host 旧默认全为 1，能覆盖）；②给规划器看的"剩余合成次数"在出现没回复的回合后会偏少，只影响展示。


**结构修复真机第 2～3 局与随后的修复（2026-09-30 下午）**：
- 第 2 局（`mission-f609654fb74248dd`，失败）：规划器只重做了下游第二步，但"第一步引用了旧资料"那条修复请求也被算作处理掉了（判断只看影响范围，而影响范围含下游），第一步从没重做，终审判返工。**SDK opt.96**（`9b0c9770`，Host 钉版 `9be13be0`）：修复请求记下"触发范围"（触发的步骤和它的上级目标；不含义务编号，因为同一目标的兄弟步骤共用它），只有决定处理到触发范围（或新增工作）才算处理了；没有具体步骤的任务级触发照旧按影响范围。测试先失败后通过，相关组 515 + 358 条通过；变异（关掉触发范围）让测试失败。
- 第 3 局（`mission-8f23cb82b8453809`，opt.96，失败）：规划器这次判断完全正确——第一步已通过的结果引用了旧版资料、已通过的不能原样重跑，于是为第一步提出**后继步骤**。但被三件事挡住：①第一次回复 goal_type_ref 缺 version（格式错）；②同一请求的格式重试，模型把 32K 输出额度全用在思考上、没有输出（按"没回复"处理，开了新请求）；③第三次回复格式正确，却在执行图收敛检查处被拒（"共享产出被悄悄改义"），修复轮用完，任务失败。
- 修复（SDK 源码，待发布 opt.97）：
  - **规划器格式三件**（用户同意）：提示词 v13 = v12 + 每个子结构的全部字段（assumptions / uncertainties / alternatives / replan_triggers / 引用四元组 / goal_type_ref）+ 两个把这些列表填满的示例（测试逐条解码通过）+ "上一次被拒"怎么读；新任务绑 v13，已绑 v11/v12 的不变。**字段路径反馈接通**：同一请求的格式重试，包不变，消息末尾附上上一次的 previous_feedback（错误码、JSON 指针位置、原因）——此前原消息一字不差重发，模型不知道错在哪；新请求的包里 previous_feedback 填本任务最近一次被拒的决定。**唯一的无损补齐**：后继步骤的 goal_type_ref 三个字段只缺一个、另两个在请求包 successor_types 里恰好对上一条时照那条补上，补了什么记进评估事件；对不上、对上多条、写错都照旧严格拒。
  - **收敛检查误拒后继步骤**：后继步骤会给父目标换一个新方法实例，于是旧实例下的步骤都算"受影响"；第二步输入改指新第一步算"变了"；检查把"受影响 + 变了 + 仍被需要"一律当成共享产出改义。改为只有真正共享才拒：候选里仍按共享方式（share_active / reuse_accepted）需要它，或一个修订前后都在的使用方仍需要它。只被同一父目标需要的第二步照常列为"输入已替换"去重做。测试（真实编译的后继修订上直接调检查）先失败后通过；共享方式的反例照旧拒；变异（关掉检查）让反例测试失败。
  - 测试：新增 `test_planner_format_v13.py`（29 条），改 `test_planning_request_retry_scope.py`、`test_h4_package7_entry.py`（缺 version 仍提交 + 收敛），钉 v12 为默认的 4 个旧测试改为 v13；编排 full_target 4542 条通过（唯一失败是钉"重试消息与原消息相同"的旧断言，已按新行为改）。
- 未修、记账：思考模式下规划器偶尔把输出额度全用于思考（本局 1 次），系统已按"没回复"再问，不扣次数。
- 第 4 局（`mission-9a849007a8fa5a86`，SDK opt.97，失败）：格式问题已消失，规划器三次都正确提出"给第一步换后继步骤"，全部被执行图挡住（`TASKGRAPH_INDEPENDENT_WORK_STILL_REQUIRED`），修复轮用完。另有 1 次规划器把 32K 输出额度全用于思考（按没回复再问）。查下去是**执行图开着时后继步骤链路上连着三处缺陷**，都修在 SDK 源码（待发布 opt.98）：
  1. **独立需要的工作检查过严**：后继步骤给父目标（这里是任务根目标）换新方法实例，根目标记录换代；根目标被"任务本身"独立需要，检查把"独立需要的工作在改动目标里"一律拒掉——执行图开着时任何改根目标方法的结构修复（后继、换方法、再细分）都提交不了。改为只拒**移除**独立需要的工作；保留下来、只是换代的放行。
  2. **开工许可被当成不可变的证据**：修复需要等下游收敛，期间别的就绪步骤一开工，开工许可/执行许可就变了；它们原来归在"证据"来源里（续跑时必须一字不差），于是修复永远提交不了。改为归"正在跑的工作"（续跑允许变化）；计划见证仍在证据里。
  3. **换代后开工核对过严**：换代的步骤输入内容与原来一字不差时，输入清单绑定表里同一步骤同一份内容只有一行（首次绑定时的版本号），新版本开工报"清单没绑到确切输入版本"。确切版本由步骤语义记录与逐次尝试记录保证，这里改为"在这一版或更早绑定过同一份内容"（两处代码核对 + 数据库第 30 号迁移重建开工身份触发器）。
  - 测试：新增 `taskgraph_exec/test_successor_with_taskgraph.py`（真实编排器、真实严格执行图：计划生效 → 给第一步提后继 → 第 2 版计划提交 → 换代步骤重新开工），修前依次撞上这三处、修后通过；三处各做变异都让测试失败。数据库版本号断言 29→30。
- 第 5 局（`mission-63ae2145bc0d217a`，SDK opt.98，失败但**第一次出了第 2 版计划**）：换资料后规划器一次就给第一步提后继步骤，提交成功（计划第 2 版），"第一步引用旧资料"的请求按触发范围算处理完；新第一步按第 2 版资料重做（一次没过、原样重试后通过）。卡在第二步：它在修复前做过一次没通过，尝试停在"等规划器批准原样重试"；修复把它换代（输入改指新第一步、派发代号 +1）后，旧规则仍要"原样重试"批准，规划器却不会再被问，第二步派不出去，任务以"没有可继续的工作"失败。
  - 修复（SDK 源码，待发布 opt.99）：失败属于旧一代（尝试开始时的派发代号小于当前代号，按尝试开始前最后写入的那版语义记录算）就不要求重试批准，新一代直接开工；新一代自己失败照旧要批准。派发门与提交服务建尝试的门走同一个判断，一处改两处生效。测试 `test_retry_after_regeneration.py` 先失败后通过，变异让它失败；重试/执行图/终审相关 137 条通过。
  - 另：opt.98 回归里漏改一处数据库版本号断言（29→30），已改。

**用户决定（2026-09-30 16:58）**：模型路由暂不考虑，现在只做 DeepSeek。收口第 5 项由"阻断"改为"按用户决定不做"。
- 第 6 局（`mission-da3f6d0570fdfc82`，SDK opt.99→100，失败）：一局里走通两种结构修复——旧第一步引用格式连错三次 → 换方法（第 2 版计划）；资料换版 → 给第一步提后继（第 3 版计划）。中途卡住：执行池已有 1000 个执行者（每个模型回合建一个、从不关，上限数建过的全部），新步骤建不出执行者，报错还写成"创建身份冲突"。**SDK opt.100**（`ef7ccaf0`）：上限只数没关闭的；编排器定期关闭已结束任务的执行者（只改状态，记录全留）。重启后该局自己恢复。最后新第一步（后继）引用连错三次，而"反复失败后修一次方法"整个任务只有 1 次、已用在换方法上，任务结束。
  - **用户决定（18:15）：选 A**——反复失败后修方法的机会按步骤算，新出现的步骤（如后继）重新获得机会，总数仍受规划总次数限制。**SDK opt.101**：计数按步骤；失败报告只列本步骤自己的说明（原来拼进了上一步的说明，看起来在说旧步骤）。测试先失败后通过，变异让它失败；编排 full_target 4546 条全过。
