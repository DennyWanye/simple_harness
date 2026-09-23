# ARP-EXEC-1.1.1 主体施工 RP-C3：Skill 试用 / 准入 / load / execute

日期：2026-09-23。分支 `arp-1.1.1`，上一片 RP-C2 提交 `77f2f1f0`。

## 1. 交付物

| 模块 | 内容 | 规格条款 |
|---|---|---|
| `arp/lifecycle.py`（新） | `SkillLifecycleService`。`begin_trial(SkillTrialCommand)`：核 TrustedCaller、当前 activation `row_version`（CAS）、起点只能是 QUARANTINED/SUSPENDED、`dependency_lock_ref` 必须等于该修订当前的完整锁、`evaluation_policy_ref` 必须等于定义里的 `verification_policy_ref`；签发隔离评估 scope（独立 workspace、无网络、`NO_PRODUCTION_CREDENTIALS`、allowed tools = 该技能 required_tool_refs 里当前已准入且可用的工具，其余具名记入 authority 回执的 `unavailable_tool_refs`）；生成不可变 `SkillEvaluationBinding`（evaluation pin 由绑定正文哈希派生，有效期 24h）同一事务写 `arp_skill_evaluations` 并把 activation 推到 TRIAL、把 evaluation pin 挂到 activation。同命令号重送返回原绑定；同命令号换正文 `SOURCE_HASH_CONFLICT`。`admit(SkillAdmitCommand)`：只从 TRIAL；策略/scope 必须是绑定自己的；绑定未过期、锁未变；把 acceptance 交给 `SkillAcceptancePort.verify` 核验（返回必须 accepted 且指向同一 evaluation）；**同一事务**写不可变 `arp_skill_admissions`（同 evaluation 换 acceptance 冲突）并迁 ADMITTED（`transition_locked` + `admission_ref`）。重送同 acceptance 返回原结果；已准入换 acceptance `STATE_COMBINATION_INVALID`。`suspend(SkillSuspendCommand)`：TRIAL/ADMITTED → SUSPENDED，立即 usable=false。`transition(SkillLifecycleCommand)`：ADMIT 同上；RESUME 只接受准入时那份 acceptance，且绑定仍有效、锁相同，否则 `SKILL_TRIAL_REQUIRED`；RETIRE 终态；TRIAL 动作要求走 SkillTrialCommand（`MISSING_FIELD $.evaluation_policy_ref`）；所有非重送命令核 `expected_activation_revision` | §9.6–9.7；SKILL-CATALOGUE §3–§4 |
| `arp/ports.py` 改 | `SkillAcceptancePort` 协议 + `PendingAssuranceAcceptance`（BW09 接口冻结：Assurance 后继未到前每次 verify 以 `SKILL_EVALUATION_INCOMPLETE` 拒绝，detail.successor = `ASSURANCE_SUCCESSOR_PENDING`；错误码目录冻结，不新增码）；`ScriptRun` / `ScriptRunReceipt` / `ScriptRunnerPort`（SDK 不 spawn 进程，执行器是 Host 的）；`ArpPorts.acceptance / script_runner` | SKILL-CATALOGUE §4–§5 |
| `arp/skill_use.py`（新） | `SkillUseService`。`usable_skill`：非 SKILL → `CATALOGUE_KIND_MISMATCH`；QUARANTINED → `SKILL_TRIAL_REQUIRED`；非 ADMITTED/不可用 → `SKILL_NOT_ADMITTED`（带原因）；锁不完整 → `DEPENDENCY_UNRESOLVED`。`load(SkillLoadRequest)`：绑定当前 Session/Turn（开放 Turn，否则最近 Turn）/call，写 `SkillUse(mode=INSTRUCTIONS)` 到 `arp_skill_uses`（同 turn+call+mode 重送同体重放，异体冲突），写 `skill_load` 原始回执记录读取的说明文件（**只装 instructions_path 这一份**；参考文件走 details/artifact 读取）；装载前做预算门：本 Session 已装载的去重说明块 + 本次文件的 token 不得超过上下文容量的技能份额（`(min(max_context, 模型输入上限, 合并上限−输出上限) − 安全预留 − 工具余量)/2`），超过 `REQUIRED_CONTEXT_TOO_LARGE` 且不写 use。`execute(SkillExecutionRequest)`：先做权限交集——技能 required_tool_refs 必须全部在该 Session 最新 ToolSnapshot 里（`rules.effective_permissions`），否则 `TOOL_NOT_EXPOSED`；INSTRUCTIONS 返回正文（≤32000 字符 INLINE，否则 REFERENCED 指向文件 artifact，不截断报成功）；SCRIPT：无 runner 端口 `RUNNER_UNAVAILABLE`、runner_ref 必须是已准入 PROVIDER、argv 只允许整 token `{input_json}/{output_json}`（含 `{`/`}` 的其他 token → `SKILL_MANIFEST_CONFLICT`）、输入先按技能 input schema 校验且 ≤64KiB、先写 `SkillUse(mode=SCRIPT)` 再交端口；回执：UNKNOWN → status UNKNOWN + `UNKNOWN_REQUIRES_RECONCILIATION`；TIMEOUT/exit≠0/exit0 无输出或截断/非 JSON/不符 output schema → FAILED + `SKILL_OUTPUT_INVALID`；有效输出 INLINE 或 REFERENCED。WORKFLOW → `WORKFLOW_UNAVAILABLE`（不写 use）。每次 execute 写 `skill_execute` 原始回执（view + runner 回执 pin） | §9.8–9.9；SKILL-CATALOGUE §5 |
| `arp/skill_tools.py`（新） | 模型工具 `skill_discover` / `tool_discover`（CatalogueReadCommand → CataloguePage，namespace 由 Session 强制，MODEL 视图）、`skill_load`（→ SkillUse）、`skill_execute`（→ ToolResultView；SUCCEEDED → 成功结果，UNKNOWN → outcome unknown，FAILED → 具名失败）；`ArpModelTools` 把历史检索工具与技能工具合成运行时唯一的模型工具集 | §9.10；model-tools.json |
| `arp/context/skill_blocks.py`（新）+ `composer.py` 改 | prepare 时读本 Session 所有 INSTRUCTIONS 用途的 load 回执，**先复核该技能当前 ADMITTED 且可用、锁完整，不可用者不再进 E**，再按（技能修订、路径、sha256）去重，每个文件一块 E 段：USER 角色 `<skill_instructions skill= path= sha256=>` 框架（标签转义），trust `SKILL_INSTRUCTIONS`，required，token 计入 fixed 预算；manifest `sections` 增 E 块（source_refs = load 回执 pin + 文件 artifact pin），`skill_refs` 列出装载的技能 pin，read_set 记块列表；`_replay` 只从冻结 manifest 自己的 E 块重放（block_id → skill_refs → 按 sha256 核对文件字节），不读当前装载集。execute 过的 INSTRUCTIONS 技能不进 E（无 load 回执） | §9.8 |
| `arp/store.py` 改 | `put_skill_use_locked` / `read_skill_uses` / `read_skill_use` | |
| `arp/catalogue.py` 改 | `transition` 拆出 `transition_locked(connection, …)` 供调用方在自己的事务里用；新参数 `admission_ref`。存储层防线：SKILL→ADMITTED 必须①activation 挂有 evaluation（TRIAL 时挂上、准入沿用），②同一连接里 `arp_skill_admissions` 存在 (skill, revision, evaluation) 的准入记录，③`admission_ref` 等于该记录的 acceptance；三者缺一即 `SKILL_EVALUATION_INCOMPLETE`。直接调目录服务改 ADMITTED 不可能绕过验收端口 | §9.7 |
| `arp/bootstrap.py` 改 | 新工具效果类：discover/load READ_ONLY，execute SANDBOX_WRITE | |
| `arp/runtime.py` 改 | 装配 `state.lifecycle`、`state.skill_use`，模型工具集换 `ArpModelTools` | |
| DDL | 新表 `arp_skill_evaluations`（不可变，UNIQUE(namespace, command_id)）、`arp_skill_admissions`（不可变） | |

新增测试：`test_arp_skill_lifecycle.py`（7 项）：试用绑定隔离 scope 字段/幂等/换体冲突/CAS/状态；策略错、锁错、匿名 caller 拒绝；Assurance 未到时 admit 与 ADMIT 动作具名拒绝且带 successor；用验收替身走 admit → 重送/换 acceptance 冲突 → suspend 即不可用 → 换 acceptance 的 resume 拒绝 → 原 acceptance resume → retire 终态；绑定过期后 resume 需新试用且 SUSPENDED 可再开试用；生命周期动词的 CAS 与 TRIAL 动作拒绝。`test_arp_skill_use.py`（7 项）：load 绑定 SkillUse、同 call 重送同体、E 段每文件一块且紧随控制指令、manifest E 块/skill_refs/回执引用、`replay_skill_blocks` 逐字节复现、后续轮次仍只一份；QUARANTINED/TRIAL/SUSPENDED/幽灵 pin 的 load 与 execute 具名拒绝；execute INSTRUCTIONS 返回 INLINE 正文、写 use 与 execute 回执且不进 E、skill/tool discover MODEL 视图分页且无秘密字段；WORKFLOW 具名拒绝且不写 use；SCRIPT 经替身 runner：argv/输入字节/脚本字节/runner pin/workspace 固定、有效输出成功、非 JSON/exit 3/UNKNOWN/类型不符/截断各自具名、输入 schema 先校验不触发运行；无 runner、部分 token、所需工具未曝光三种拒绝。`skill_fixture.py`：包/命令构造、`AcceptingAssurance`（测试替身，只接受由绑定派生的 acceptance）、`ScriptedRunner`（测试替身）。`arp_fixture.build` 增 `acceptance` / `script_runner` 注入口。

## 2. 实现决定

1. **不新增错误码**：错误码目录与 schema 是冻结合同（SDK 内副本与规格副本逐字相同）。"Assurance 后继未到"用 `SKILL_EVALUATION_INCOMPLETE` + `detail.successor = ASSURANCE_SUCCESSOR_PENDING` 表达；脚本 exit≠0 / 超时同样归 `SKILL_OUTPUT_INVALID`（回执里保留真实原因），未确认结束用 `UNKNOWN_REQUIRES_RECONCILIATION`。
2. **activation.evaluation_ref 只挂 evaluation pin**（Store 层 `require_kind("evaluation")`）：TRIAL 时挂上绑定的 evaluation，准入沿用；acceptance 单独记在 `arp_skill_admissions`，RESUME 据此核对"同一份准入"。
3. **评估调度不在 SDK**：绑定的 `invocation_refs` 为空、`budget_owner_ref` 是以命令号命名的 task pin；真实 eval 派发/预算由原编排（AgentBridge/Assurance 后继）承担，目录只记录绑定。
4. **allowed_tool_refs 取交集而非拒绝**：技能声明的 required_tool_refs 里当前不可用的工具不阻止开试用，而是具名记入 authority 回执；执行时的权限交集另在 `execute` 硬门（缺一即 `TOOL_NOT_EXPOSED`）。
5. **E 段装载正文的入口是下一次 prepare**：load 只写 use 与回执；正文在下一请求 manifest 落 E 块与 `RuntimeContextPrepared` 事件即为曝光记录；重放只信 manifest 自己的块。
6. **SkillUse 的 input_manifest_ref / tool_snapshot_ref**：取该 Session 最近一次 ContextManifest（context pin 作 input_manifest 代理、其 tool_snapshot_ref）；没有任何 manifest 时退化为 session 派生 pin 与最近 ToolSnapshot。
7. **SCRIPT 的 argv 由执行器替换**：SDK 不知道执行器的工作区路径，只保证 argv 里除两个整 token 外没有任何 `{}` 花括号 token，且必须含 `{output_json}`。

## 3. 测试与回归

- ARP 定向：`tests/agents/arp` → **316 passed**（302 + 14，核验修复后重跑）。
- legacy `tests/agents`（排除 arp）16 failed / 172 passed / 6 skipped；`tests/execution` 17 failed / 144 passed；与基线相同。

## 4. 独立核验

一轮，opus 5.5 只读审阅，只报阻断级。结论：3 条阻断，全部处置。

| # | 审阅意见 | 处置 |
|---|---|---|
| 1 | 一次 load 把包里所有参考文件整份塞进 E 段且标 required、跨轮累积，fixed 一超容量整个 Session 每次准备都失败，永久卡死；触发者是模型可自由调用的只读工具 | load 只装 instructions_path 一份；装载前预算门（技能份额 = 容量一半），超过 `REQUIRED_CONTEXT_TOO_LARGE` 不写 use；测试：大说明拒绝、小说明照常、Session 继续可用 |
| 2 | 技能被暂停/退役/锁撤销后，说明仍注入之后每次请求（§9.7 暂停应即时拒新调用） | `skill_blocks_for` 每次准备复核 `catalogue.usable` 与锁完整，不可用者不进 E；重放只信冻结 manifest；测试：suspend 后下一请求无块、`skill_refs` 为空 |
| 3 | 存储层准入防线退化为"曾经试用过就行"：直接调 `CatalogueService.transition(…, ADMITTED)` 可绕过验收端口；准入记录与迁移分两个事务 | 存储层要求同连接存在匹配的 `arp_skill_admissions` 记录且 `admission_ref` 相等；`transition_locked` 让准入记录与迁移同事务（试用绑定与 TRIAL 迁移亦同事务）；测试：直调目录三种绕过均 `SKILL_EVALUATION_INCOMPLETE` |

## 5. 未做

- Host 侧 `agent_skill_trial / admit / suspend / resume / retire` verbs 的 DTO 解码与固定 caller 委托（Host handler 工作）。
- 真实 `SkillAcceptancePort`（Assurance 后继）与真实 `ScriptRunnerPort`（Host 的沙箱执行器：固定 cwd、环境白名单、进程组超时、stdout/stderr artifact）。
- `SkillDetailsPage` 的文件内容 range 读取走 artifact.read（Host）。
- RP-D：生命周期 / Host verbs / GC / retention（含 RP-B/C1/C2/C3 各记录 §5 的剩余项）。
