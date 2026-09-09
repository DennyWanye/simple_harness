# 裁决备忘：workflow 整条线删除的切片边界（2026-09-09）

> 独立评审裁决，非建议。主 agent 按本文写 plan 与 acceptance，不再就以下问题询问用户。
> 依据：`acceptance.md`（草稿）、`survey-host.md`、`survey-sdk.md`、`baseline.md`，以及本次对 Host 源码的复核（复核结论标注在各条理由里）。

## 1. 一句话裁决

本轮只做 **Slice 1 = 把 `workflow_spawn` 从模型视野里摘掉 + 删掉生产零引用且不进入任何持久化 payload 的死壳**，`main.py`、`deskpet/workflows/`、前端、Harness SDK 四者本轮 diff 必须为零；启动装配（execution_uow 归属、图引擎、IPC、前端视图）整体推到 Slice 2，SDK 的 spawn 线删除推到 Slice 3 并与用户即将进行的 memory/task 编排大改合并，本轮保持 0.7.10 不动。

## 2. 切片表

| | Slice 1（本轮） | Slice 2（下一轮，须再拆 2a/2b） | Slice 3（延后，合并做） |
|---|---|---|---|
| **目标** | 模型再也看不到 workflow 线；主流程冒烟原样通过 | Host 启动装配与图引擎整体下线，`deskpet/workflows/` 只剩 B 层内核与 C 层账本 | Harness SDK 的 spawn 机器与 schema 一次性删除 |
| **包含（file 级）** | `deskpet/tool_catalog/real_tool_manifest.json`（删条目/`workflows={}`/`tool_count=76`/新 `manifest_sha256`）、`tool_catalog/manifest.py`、`tool_catalog/schema_migrations.json`、`tool_catalog/providers.py`、`sdk_adapters/conformance.py`、`sdk_adapters/tools.py`、`sdk_adapters/tool_authority.py`、`capabilities/manifest.py`、`tools/prepared_snapshot.py`、`tools/skill_tools.py`、`tools/orchestration_controls.py`（spawn 段）、`agent/turn_preparer.py`（`_inject_profile_catalog` + `core_names`）、`companion/turn_authority.py:716-724`（route_prompt 文案）、`harness/execution_profiles.py`（`ProfileLaunchTicket` / `WorkflowSpawnRequest` 两个 dataclass）、`companion/workflows.py`（整删）+ 其单一测试；受影响测试改写 | 2a：`main.py` 3563-5007 全段 + 6152-6198/6773-6883/6904-6991/15581-15594/15811-15817/17079-17110 + `execution_uow` 归属重构 + `backend/context.py::_VALID_SERVICES` + 前端 `components/workflows/`、Sidebar ContextTrace 入口、`ContextTracePanel` workflow IPC、`controlWs.ts` workflow 分支、PPTOutlineCard。2b：`deskpet/workflows/` A 层文件物理删除（runner/service/launcher/native/definition/definitions/adapters/evaluation/replay/recovery/ipc/progress/human/outbox/control/routing/proposal_state/output_contract/terminal_projection/delivery/retention/startup_recovery/runtime_adapters/execution_ports/bootstrap/lease/trace-observer）+ `companion/turn_authority.py:50-52` 解耦 + `sdk_adapters/workflows.py` + `capability_catalog.py` workflow 记录 + `harness/router.py` + `child_runs.py` ticket 分支 + `harness/ports.py` DelegateRun ticket 字段 + `tools/research_tools.py` 图定义引用 + `quality/corpus_scoring_session.py` + 脚本与文档 | `runtime/workflow_spawn.py`、`runtime/drivers/workflow.py`、`kernel.py` spawn 面、`orchestration.py` 的 WorkflowSpawn*/WorkflowCatalog*/WorkflowLaunchTicket 族、`uow.py` 约 20 个 spawn 方法、7 张 `workflow_*` 表（schema v10）、`TerminationState` 四字段、`tools/contracts.py` 的 `workflow_spawn_context`、public-api 快照收缩；Host 重钉 |
| **明确不包含** | `main.py` 任何一行；`deskpet/workflows/` 任何一行；`child_runs.py` / `harness/ports.py` / `harness/router.py`；`sdk_adapters/workflows.py` / `capability_catalog.py`；前端任何一行；SDK 任何一行；`PRODUCT_ROUTE_OWNED_TOOL_NAMES` 的 deny 列表；`agent_loop.py` 的 `accepted_async` 拆批机制；5 条残留 orchestration 工具的 `execution_build_identity` 重算 | `deskpet/workflows/store/`、`contracts.py`、`effects.py`、`errors.py`、`deadlines.py`、`trace/`（除 observer）；SQLite 表结构与迁移链；`spawn_subagents` 五件套；SDK | 不做 0.7.11 单独发版；不为删除单独动 checkpoint 形状（只能随编排大改的 schema 版本一起做） |
| **独立验收动作** | ①`twoflow_driver.sh` 的 `TF_TURNS` 10 轮冒烟全绿（新 userdata + 交付版旧 userdata 各一次重启段）；②从冒烟的 provider 请求记录 `grep -c workflow_spawn` == 0；③启动无 `RuntimeError`（清单四处守卫一致）；④触及目录 pytest 失败集合与 `baseline.md` 逐条相同 | 同一 10 轮冒烟；`grep -rn` Host 侧 workflow 线符号只剩 store 与历史文档；前端 `npm run typecheck` + vitest 全绿 + 一次 AX 枚举确认无 workflow 入口 | SDK 全量 pytest 自建基线对比；`uv build` 可复现；Host 重钉后同一 10 轮冒烟 |
| **规模** | 16 个文件 + 约 8 个测试文件；净删约 400–600 行 | 2a：约 25 个文件（`main.py` 单文件净删 ~1500 行）+ 35 个 uow 消费点改写；2b：约 120 个文件（含 ~100 个测试）净删上万行 | 约 40 个文件，净删约 12000 行（含 8131 行单测试文件） |
| **风险** | 低。唯一启动致命项是冻结清单四处守卫，`baseline.md` 的 spike 已验证哈希可重算 | 高。穿透 execution_uow / checkpointer import 链 / turn_authority 图定义三条边界 | 高。穿透 checkpoint 哈希与公共 API 快照 |

Slice 2 单独一个 release unit 会同时超 AC≤8 与 Task≤10，写 plan 时**必须**按上表拆成 2a（断引用，主链）与 2b（删文件，清扫），各自独立验收、各自跑一次冒烟。

## 3. SDK 侧裁决与理由

**裁决：选 (a) —— 本轮（Slice 1 与 Slice 2）不动 SDK，保持 0.7.10；(b) 不单独立项；(c) 是最终形态，但必须等到用户的 memory/task 编排大改动 schema 时一次做掉，届时版本号走 0.8.0 而非 0.7.11。**

理由（按权重）：

1. **删了不解决任何问题**。Host 在 Slice 1 后不再注册 `workflow_spawn`，Host 从不注册 Workflow Driver（`main.py` 只传 react driver、`workflow_factory=None`），SDK 侧 spawn 机器已经是模型不可达的死路径。删它不改变模型视野，不改变主流程行为，价值为零。
2. **(b) 做不到"只删代码不动字段"**。`TerminationState` 的四个 workflow 字段在 ReAct checkpoint 的 canonical JSON 里（survey-sdk §6.3），删字段 → `checkpoint_hash` 全变 → 存量 checkpoint 被判 corrupt（`uow.py:13228-13236`），直接打死 acceptance 的"旧 userdata 必须能重启"。`tools/contracts.py:112` 的 `workflow_spawn_context` 位置在 `call_id`/`effect_id` 之前，删字段改变位置参数顺序。所以 (b) 的真实内容是"删一半留一半"，恰好是最差形态：既留死壳又要重跑全量。
3. **无基线可对**。SDK 仓没有 0.7.x 全量 pytest 基线（survey-sdk §5），"失败集合与基线逐条相同"这条验收目前无法成立，得先花一轮自建基线。用户已明确"相关大量测试不重跑"。
4. **公共 API 只增不减**。删 `WorkflowRuntimeDriver` / `build_workflow_runtime_driver` 会同时打红 `public-api.json` 与五组 0.7.x 兼容断言。这是一次有意的 API 收缩，应当伴随 minor 版本跳变与 CHANGELOG breaking 条目，不该藏在一个 patch 版里。
5. **马上要改第二次**。用户已决定 memory/task 编排大改，SDK schema 大概率随之动（`child_commands` 的二选一 CHECK、`profile_launch_tickets` 都在编排的核心路径上）。现在做 v10、下个月再做 v11，等于把 checkpoint/schema 破坏做两次、迁移风险吃两次。
6. **回滚成本**。不动 SDK ⇒ 本轮回滚 = Host 单仓 `git revert` 一个提交，不涉及 vendor wheel / `uv.lock` / installed target 重建，也不需要与原生进程串行化。

配套要求：Slice 2 里 Host 侧对 SDK workflow 符号的 import（`sdk_adapters/conformance.py:97-109`、`workflows.py:12`、`personal_catalog.py:9`、`capability_catalog.py:25`、`definitions/sdk_v2|sdk_v7`）照删不误 —— 那是 Host 的代码，与 SDK 发版无关。`composition.py:415` 的 `workflow_runner=` kwarg 保留传 `None`，不改 SDK 接口。

## 4. Slice 1 逐项边界表

判定原则（本表唯一口径）：**进 Slice 1 = 模型能看见的东西，或生产零引用且不出现在任何持久化 payload / 启动装配里的死壳。**

| 项 | 判定 | 理由 | file:line |
|---|---|---|---|
| `real_tool_manifest.json` 的 `workflow_spawn` 条目 | 进 | 模型视野的唯一来源：生产注册表由冻结清单构建（`register_orchestration_controls` 无生产调用者），删条目即删工具 | `deskpet/tool_catalog/real_tool_manifest.json` tools[73]（`:7180-7310`） |
| `real_tool_manifest.json` 的 `workflows` 投影 | 进（置 `{}`，保留键） | 唯一去向是 `ExplicitProductToolCatalog.workflows`，该字段全仓无消费者（复核确认）；置 `{}` 而非删键，与 `baseline.md` spike 算出的 `df979c0e04…` 口径一致，字段类型不变 | `providers.py:797`、`:150-152` |
| `tool_count` / `MANIFEST_SHA256` / 计数与投影断言 | 进 | 不同步改，`load_tool_manifest` 启动即 `RuntimeError` | `manifest.py:15,55-86`（`77`×2、`pre_cutover_count=79` 保留、workflows 集合断言） |
| `schema_migrations.json` 的 `workflow_spawn` 行 + 其 `manifest_sha256` | 进 | `migrate_tool_schemas` 有 `changed != set(expected)` 硬断言，条目删了迁移行不删就 `RuntimeError`；该文件内嵌的 `manifest_sha256` 也必须同步为新值 | `schema_migrations.json:685` + 顶层 `manifest_sha256`；`manifest.py:190,225-231` |
| `manifest.py::_migrate_schema` 的 `workspace_ref` 专项迁移 | 进 | 与上条同生共死，条目没了分支永不触发 | `manifest.py:152-157` |
| `providers.py::_CONTROL_TOOLS` + `len(names) != 77` | 进 | 名字登记面 + 启动计数守卫 | `providers.py:37,793` |
| `sdk_adapters/conformance.py` 的 `len(inventory) != 77` | 进（survey 漏记，本次复核补） | 同为 77 硬编码，不改则 conformance 目录测试与静态目录构建路径报 `real product Tool catalog is incomplete` | `conformance.py:211` |
| `sdk_adapters/tools.py` 的 `PRODUCT_TOOL_NAMES` | 进 | 冻结名单，与清单必须一致 | `sdk_adapters/tools.py:41-43` |
| `sdk_adapters/tool_authority.py` 的 `SDK_DIRECT_TOOL_KERNEL` | 进 | acceptance 明列的模型视野断言项 | `tool_authority.py:173` |
| `capabilities/manifest.py` 的 `CORE_RESERVED_TOOL_NAMES` | 进 | 名字登记面；该常量无其他 workflow 语义 | `capabilities/manifest.py:98` |
| `tools/prepared_snapshot.py` / `tools/skill_tools.py` 的 `_SKILL_SCOPE_WIDENING_CONTROLS` | 进 | 名字登记面，删名字不改机制 | `prepared_snapshot.py:28`、`skill_tools.py:314` |
| `agent/turn_preparer.py` 的 `_inject_profile_catalog` | 进（整个方法与调用点删除，`self._profile_registry` 字段与构造参数保留） | 这是 system 提示词，直接教模型"call workflow_spawn"，是模型视野里最硬的一块；`profiles.model_spawnable` 只剩 `agent.general`，注入内容本已无意义。保留字段是因为 `ProfileRegistry` 仍是 kernel root profile 的权威（`kernel.py:82,133-154`），改构造签名会外溢 | `turn_preparer.py:278-300,575`（`:284` 是全仓仅有的另一处 `model_spawnable` 消费） |
| `agent/turn_preparer.py` 的 `core_names` 元组 | 进 | `required_direct_names` 会把它强推进模型可见工具集 | `turn_preparer.py:520-529` |
| `agent/turn_preparer.py` 的 `deny_selectors` / `PRODUCT_ROUTE_OWNED_TOOL_NAMES` | **不进（且 Slice 2 也不动）** | 删 deny 会把 `ppt_create` / `deepresearch` / `ppt_pro` 重新放回模型视野 = 新增行为，不是删除；保持不变即零行为变更。是否在 workflow 线删完后重新开放这三个直连工具，另立 followup **F-WF-1**，由用户在编排大改时决定 | `turn_preparer.py:119-123,548-555`；`main.py:143,9514` |
| `companion/turn_authority.py:722` 的 route_prompt | 部分进：只把 `route_prompt` 表达式改成恒为 `""`（删掉那段文案），保留 `selected` / `query_hash` / 匹配机制与 `:50-52` 的 import | 文案里含 `workflow_spawn` 与 `profile_key=workflow.personal_v1`，属模型视野；但 `personal_workflow_source` 全仓无生产构造点（复核确认，默认 `None`），机制本就不触发，删机制会牵出耦合点 3（图编译器），留给 Slice 2b | `turn_authority.py:716-724`（保留 `:186` 的对齐不变式与 `:735,752-754`） |
| `harness/router.py` | 不进 | 生产零引用不假，但删它要改 `kernel.py:81,131,150-152,398-400` 的构造签名与分支，`kernel.py` 是主链文件，与 Slice 1 的价值无关 | `harness/router.py`；`kernel.py:81` |
| `harness/execution_profiles.py` 的 `ProfileLaunchTicket` / `WorkflowSpawnRequest` | 进（只删这两个 dataclass，文件与 `ExecutionProfileDescriptor` / `LaunchPolicy` 保留） | 生产零引用的纯死壳，且与 `execution/contracts.py:1924` 的同名账本契约重名，留着是主动埋雷（survey §1.4） | `harness/execution_profiles.py:94`；保留 `:13`、`profiles.py:16` |
| `child_runs.py` 的 ticket 分支 + `harness/ports.py` 的 `DelegateRun` ticket 字段 | 不进 | 这两个字段出现在 ReAct 委派命令的**持久化 payload** 里（`react_boundary.py:405-409` 写、`:420` 读），`spawn_subagents` 走的是同一条 `DelegateRun` 序列化路径。删字段会改变旧 userdata 里已有行的形状，风险性质与 SDK checkpoint 相同，且对模型视野零贡献 | `child_runs.py:14,78-80,337-345,407-412`；`ports.py:203-233`；`react_boundary.py:405-420` |
| `sdk_adapters/workflows.py` 与 `capability_catalog.py` 的 workflow 记录 | 不进 | `build_product_workflow_registrations` 在每次启动时被调用，产出喂 `SdkRunToolAuthorityRegistry`（`main.py:8213-8223`），是活的启动装配路径，不是死壳 | `sdk_adapters/workflows.py:24-25`；`main.py:8206-8223` |
| `companion/workflows.py` | 进（整删 + 删 `tests/companion/test_workflow_pack_adapter.py`） | 生产零引用，唯一引用是它自己的测试 | `companion/workflows.py`；`tests/companion/test_workflow_pack_adapter.py:5` |
| `main.py` 全部旧启动器段（3573-3730 / 3732-3748 / 3749-3789 / 3907-4653 / 4655-4968 / 4969-4995 / 6904-6991 / 17079-17110 / 15581-15594 / 2111-2112） | 全部不进 | 这些段共享 `_workflow_service`，而它是全产品唯一 `execution_uow` 持有者。动其中任何一段都要先解决 execution_uow 归属（见 §6），那是 Slice 2a 的第一件事。**Slice 1 对 `main.py` 的 diff 必须为 0**，这是 Slice 1 能靠一次冒烟证伪自己的前提 | `main.py:3563-5007` 等 |
| `deskpet/workflows/` 里的图引擎文件 | 不进 | 同上；且穿透耦合点 2（`effects.py:27` → `store/__init__.py` → `checkpointer` → `human`/`native`/`outbox`），删一个文件就可能让 `import deskpet.tools.registry` 直接 ImportError | `deskpet/workflows/` A 层 |
| 前端 `components/workflows/`、Sidebar ContextTrace 入口、`components/workflow/` 进度组件、PPTOutlineCard | 不进 | 与 IPC 分发（`main.py:17079-17110`）同生共死，分两轮删只会制造"按钮在、后端没了"或反之的中间态；且 `workflow_progress` / `workflow_stage` 被当前 ReAct 思考摘要复用（耦合点 5），必须与后端投影一起判断 | `components/workflows/`、`Sidebar.tsx:222-227`、`ContextTracePanel.tsx`、`messageVisibility.ts:10-18` |
| 相关测试 | 部分进 | 只改被 Slice 1 打红的：`tests/sdk_adapters/test_tool_catalog.py`（计数/哈希/投影/`adapt_model_arguments`/`workflow_spawn` 用例）、`tests/capabilities/test_failure_receipts.py:273`、`tests/test_deskpet_agent_loop.py:129-274`（`accepted_async` 参数化改用 `tool_activate`，**保留机制测试本身**）、`tests/companion/test_skill_runtime_snapshot.py:594-616`、`tests/test_p5s2_sse_diagnostic.py:220-312`、删 `tests/companion/test_workflow_pack_adapter.py`。Deep Research / PPT / 图引擎约 100 个测试文件不动 | 见 survey §6 |
| 脚本 | 不进（唯一例外见右） | `scripts/generate_harness_public_fixture.py:169` 引用 `workflow_spawn` 名字，但它是离线 fixture 生成器、不在启动路径；与其他脚本一起进 Slice 2b | `scripts/generate_harness_public_fixture.py:169` |
| 文档 | 部分进 | Slice 1 只写：`CHANGELOG` 一条 + `ARCHITECTURE/AGENT_HARNESS.md` / `ARCHITECTURE/AgentLoop.md` 各一条中文记录（"`workflow_spawn` 工具面已下线，图引擎与启动装配待 Slice 2"）+ 本备忘的已知漂移条。DeepResearch.md / PPT.md 等整篇改写进 Slice 2b | — |
| 5 条残留 orchestration 工具的 `execution_build_identity.artifacts` | 不进（记为已知漂移） | 编辑 `orchestration_controls.py` 会让 `workspace_prepare` / `capability_build` / `capability_repair` / `external_action_wait` / `project_directory_select` 五条清单项里钉的该文件 sha256 与磁盘不符。复核确认：这五条**不在** `execution_build_sources.json` 的 72 个 handler 里，`scripts/generate_execution_build_manifest.py --check` 不覆盖它们，`validate_core_registry_handler_set` 只被测试调用，**无任何运行时或测试守卫**；重算 `build_digest` 需要复刻一套不适用于它们的生成算法。故明写为已知漂移，与 `orchestration_controls.py` 的最终形态一起在 Slice 2b 处理 | `real_tool_manifest.json` 五条的 `execution_build_identity`；`build_identity.py:141-143,179-196,343-356` |

## 5. survey-host §8 五个耦合点在 Slice 1 的处理

| # | 耦合点 | Slice 1 处置 |
|---|---|---|
| 1 | `service_context["workflow_service"]` 是唯一 `execution_uow` 持有者（`main.py:3792`；`2696-2735`、`3824`、`3876`、`11297-11300` 都吃它） | **保持不动**。Slice 1 不碰 `main.py`、不碰 `bootstrap.py`、不碰 `context.py::_VALID_SERVICES`。归属重构是 Slice 2a 的 Task 1（见 §6） |
| 2 | `effects.py:27` → `store/__init__.py:5` eager 导入 `checkpointer` → 懒 import `human`/`native`/`outbox` | **绕开**。Slice 1 不删 `deskpet/workflows/` 下任何文件，import 链原样保留。Slice 2b 的第一步必须是"先把 `checkpointer` 对 `human`/`native`/`outbox` 的依赖摘掉，再删这三个文件"，顺序反了 `import deskpet.tools.registry` 即崩 |
| 3 | `companion/turn_authority.py:50-52` → `definitions/personal_workflow` → `definition`（图编译器） | **绕开**。import 与匹配机制原样保留，只删 `:716-724` 的提示词文案。复核确认 `personal_workflow_source` 无生产构造点，删文案零行为变更。真正解耦（用 Host 本地实现替换 `personal_workflow_query_hash`，或整条 personal workflow 一起删）留给 Slice 2b |
| 4 | `agent_loop.py:4497-4530` 排他拆批按 `completion_semantics == "accepted_async"` | **只删名字，机制一行不改**。复核确认冻结清单里 `workflow_spawn` 的 `completion_semantics` 是 `sync`，真正走 `accepted_async` 的是 `tool_activate` 与运行时改写路径；`tests/test_deskpet_agent_loop.py:186` 的参数化把 `workflow_spawn` 换成 `tool_activate` 即可，**不得删掉这个测试** |
| 5 | `projection_kind="workflow_progress"` 被 ReAct 思考摘要复用（`run_presenter.py:902`、`session_db.py:90,104`、`messageVisibility.ts:18`、`controlWs.ts:1792-1800`） | **保持不动**。Slice 1 明令禁止按 `workflow` 字符串做全局清理；这五处一个字符都不改。Slice 2a 处理前端时必须先给这个 projection 改名或明确保留，再动 `components/workflow/` |

## 6. execution_uow 归属裁决

**裁决：Slice 1 不动；Slice 2a 的第一个 Task 把 `SqliteExecutionUnitOfWork` 从 `workflows/bootstrap.py` 剥离，新增 `service_context` 键 `execution_uow`，并把 `workflow_service` 键从 `_VALID_SERVICES` 删除。不接受"保留 `workflow_service` 键、只摘图注册"的折中方案。**

理由：

1. **保留键就删不掉 A 层**。`workflow_service` 键持有的是 `WorkflowService` 实例（`workflows/service.py`），而 `service.py` 是 A 层要整删的文件。保留键 ⇒ 保留 `WorkflowService` 类 ⇒ 保留 `runner`/`registry`/`research_repository`/`human_store`/`retention` 一串构造依赖 ⇒ Slice 2b 的 `grep` 验收（`deskpet/workflows/` 只剩 store 与 B 层）根本无法通过。"改动最小"在这里换来的是"永远删不干净"。
2. **名字骗人是真实成本**。一个叫 `workflow_service` 的对象里没有任何 workflow，是比空壳更差的形态：后来者会顺着名字把图能力接回去。用户要的是"整条线没了"，不是"整条线改了个藏法"。
3. **35 个消费点的实际改动量远小于看上去**。复核确认其中约 22 处已经写成 `getattr(workflow_service, "execution_uow", None)` 或 `workflow_service.execution_uow`，改成 `service_context.get("execution_uow")` 是逐行机械替换；真正需要判断的只有三处：`main.py:2696-2735`（无则 `RuntimeError`）、`main.py:11297-11300`（SDK Runtime 激活，无则 `RuntimeError`）、`main.py:5991-5994`（关闭时 `close()`）。另有 `deskpet/tools/code_tools/spawn_subagents_tool.py:112-126` 一处在 Host 工具侧，同样替换。
4. **`_attach_workflow_history_events` 顺带收敛**。`main.py:11566-11650` 现在是"execution_uow 主路径 + `hydrate_session_history_event_ids`/`human_store`/`run_store` 兼容回退"。换键时把回退分支一并删掉，只留 execution_uow 路径 —— 这是本次剥离唯一有语义的改动，必须单列为一个 Task 并在冒烟里覆盖历史 hydrate。
5. **执行顺序**（写进 Slice 2a 的 Task 顺序，不可颠倒）：① `_VALID_SERVICES` 先加 `execution_uow`（不删旧键）；② 新增独立构造函数（建议放 `deskpet/workflows/store/bootstrap.py` 或 `deskpet/execution/bootstrap.py`：只做 `WorkflowRunStore.initialize()` 所需的 `initialize_workflow_db` + `SqliteExecutionUnitOfWork(db_path)`，**不碰 `store/schema.py`**），`main.py:182` 的早绑定改为 `execution_uow`，在 `build_workflow_service` 之前 register；③ 逐个替换 35 个消费点并跑一次冒烟（此时两个键并存，可随时回退）；④ 冒烟绿后再删图注册、启动恢复、`build_workflow_service` 调用与 `workflow_service` 键。

## 7. 需要回写 `acceptance.md` 的改动

**总原则**：现有 `acceptance.md` 描述的是"整条线"，必须整体降级为 Slice 1 的验收文档，标题改为「验收标准：删掉 workflow 线 · Slice 1（模型视野与死壳）」，并在开头加一行指向本备忘。Slice 2/3 的 AC 另建文件，不混在同一份里。

### 7.1 逐条改动

| 现有条目 | 怎么改 |
|---|---|
| 抬头引言 | 保留用户原话与背景；**新增一句**：「本文只覆盖 Slice 1；切片划分与 SDK 裁决见 `DECISION-SCOPE-SLICES.md`，Slice 2/3 的验收另立文档。」路径行 `delivery / FULL` 改为 `delivery / STANDARD`：本轮不跨仓、不动启动装配，只动冻结工具清单。 |
| 矛盾分析「主要矛盾」 | 改为：「把 `workflow_spawn` 从模型视野里摘掉之后，主对话的主流程还能不能原样跑通」。 |
| 矛盾分析「矛盾的主要方面」 | 改为：「冻结工具清单的四处一致性守卫（`manifest.py` 常量与计数、`schema_migrations.json` 的内嵌哈希与迁移行、`providers.py:793`、`conformance.py:211`）是否同步。守卫对了，删除是机械活；漏一处，启动即 `RuntimeError`。」原「边界划分是否正确」上移到 Slice 2 的 acceptance。 |
| 矛盾分析「最小验证动作」 | **不改**（同一 `TF_TURNS` 10 轮），但补一句：重启段跑两次 —— 一次新 userdata，一次交付版旧 userdata。 |
| 范围 · 包含 | 整段重写为 §4 表里判定为「进」的 16 个文件 + 8 个测试文件，逐个点名。删除现有段落里所有 `main.py` / `deskpet/workflows/` / 前端 / SDK / `child_runs` / `router.py` / `sdk_adapters/workflows.py` 的表述。 |
| 范围 · 包含里的「Harness SDK（出 0.7.11）」整段 | **整段删除**。替换为一行：「Harness SDK 本轮保持 0.7.10，不动一行；理由见裁决备忘 §3。」 |
| 范围 · 包含里的「Host 重钉 SDK 0.7.11」整段 | **整段删除**（vendor wheel / candidate manifest / `sdk_candidate.py` / `pyproject.toml` / `uv.lock` / installed target 全部不动）。 |
| 范围 · 明确不包含 | 保留现有四条，**新增五条**：① `main.py` 与 `deskpet/workflows/` 任何一行；② 前端任何一行；③ `child_runs.py` / `harness/ports.py` 的 DelegateRun ticket 字段 / `harness/router.py`；④ `sdk_adapters/workflows.py` 与 `capability_catalog.py`；⑤ `turn_preparer.py` 的 `deny_selectors` 与 `PRODUCT_ROUTE_OWNED_TOOL_NAMES`。 |
| **AC-1**（主流程原样跑通） | **保留，措辞不变**，仅在验收条件末尾追加：「同一 10 轮再用交付版 `ec7b28c7` 产生的旧 userdata 跑一次重启段（启动就绪 + 一轮 COMPLETED）」。矛盾地位仍为「决定性」。 |
| **AC-2**（模型视野里没有 workflow 线） | **保留并扩写**。`tool_count` 明确写 `77 → 76`；`workflows` 投影明确写「保留键、置为 `{}`」（不是删键）；新增三项断言：④ 启动后 system 提示词里无 profile catalog 注入段、无 `workflow_spawn` 字样（从冒烟的 provider 请求记录断言，与 tool schema 同一份记录）；⑤ `PRODUCT_TOOL_NAMES` / `_CONTROL_TOOLS` / `CORE_RESERVED_TOOL_NAMES` / `_SKILL_SCOPE_WIDENING_CONTROLS` 均无 `workflow_spawn`；⑥ `companion/turn_authority` 的 `route_prompt` 不再产出 `workflow_spawn` 文案。矛盾地位仍为「决定性」。 |
| **新增 AC-3**（冻结清单一致性，替换原 AC-3） | 「四处守卫同步且启动无 `RuntimeError`：`manifest.py` 的 `MANIFEST_SHA256` 常量、JSON 内嵌 `manifest_sha256`、两处 `77`、workflows 投影断言；`schema_migrations.json` 删除 `workflow_spawn` 迁移行且内嵌 `manifest_sha256` 更新为新值；`providers.py:793`；`conformance.py:211`。验证：全新 userdata 冷启动到 startup complete + `python -c "from deskpet.tool_catalog.manifest import load_tool_manifest, migrate_tool_schemas; m=load_tool_manifest(); migrate_tool_schemas(m)"` 无异常。」次要 / 必须。 |
| **原 AC-3**（Host 代码删干净，宽口径 grep） | **降级并窄化**为新 AC-4：「`backend/` 下 `grep -rn "workflow_spawn"` 只剩 `deskpet/workflows/`、`main.py`、前端、脚本、历史文档与 Slice 2 待删项；`harness/execution_profiles.py` 无 `ProfileLaunchTicket` / `WorkflowSpawnRequest`；`deskpet/companion/workflows.py` 已删除。」原文里的 `WorkflowLauncher` / `_workflow_service` / `workflow.deep_research` 等 grep 项**全部移到 Slice 2 的 acceptance**。 |
| **原 AC-4**（SDK 出 0.7.11 并重钉） | **整条删除**，不保留、不改写。 |
| **原 AC-5**（durable 存储不动） | 改为新 AC-5，措辞收紧为：「`deskpet/workflows/`、`main.py`、`tauri-app/src/`、`simple-harness-sdk/` 四者本轮 `git diff` 为空；交付版旧 userdata 用新版本打开可正常重启并继续对话（与 AC-1 的旧 userdata 段合并取证）。」 |
| **原 AC-6**（非 ticket 委派不回归） | **保留**为新 AC-6，措辞不变，只把「`SDK_DIRECT_TOOL_KERNEL` 里这五个工具仍在」补上「且 `PRODUCT_TOOL_NAMES` 计数为 83 时这五个仍在其中（原写 76 有误：76 是清单基数，`PRODUCT_TOOL_NAMES` 另含 7 个 host-composed 工具）」。 |
| **原 AC-7**（测试与类型基线） | 改为新 AC-7：目录清单改为 `tests/sdk_adapters/test_tool_catalog.py`、`tests/capabilities/test_failure_receipts.py`、`tests/test_deskpet_agent_loop.py`、`tests/companion/test_skill_runtime_snapshot.py`、`tests/test_p5s2_sse_diagnostic.py`、`tests/test_execution_build_manifest.py`、`tests/test_provider_runtime_refresh.py`；判定口径改为「失败集合是 `baseline.md` 那两条的子集」——**允许变绿、不允许新增红**。删掉「前端 `npm run typecheck` 与 vitest」（本轮不改前端）。 |
| **原 AC-8**（文档回写） | 窄化为新 AC-8：「`CHANGELOG` 一条；`ARCHITECTURE/AGENT_HARNESS.md` 与 `ARCHITECTURE/AgentLoop.md` 各加一条中文记录（`workflow_spawn` 工具面已下线、图引擎与启动装配待 Slice 2、SDK 保持 0.7.10）；`ARCHITECTURE/PROJECT_STATUS.md` 记一行切片进度。」`docs/agent-harness-lifecycle.md`、`ARCHITECTURE/index.md`、DeepResearch/PPT 各篇移到 Slice 2b。 |
| 非功能 / 边界 · 「启动兼容」 | 保留，措辞不变。 |
| 非功能 / 边界 · 「冻结清单一致性」 | 保留，补上 `conformance.py:211` 与 `schema_migrations.json` 内嵌哈希两处（原文只写了 `manifest.py` 与 `test_tool_catalog.py`）。 |
| 非功能 / 边界 · 「service_context 键」 | **整条删除**（本轮不动 `main.py` 与 `_VALID_SERVICES`），改写进 Slice 2a 的 acceptance。 |
| 非功能 / 边界 · 「SDK 钉版原子性」 | **整条删除**。 |
| 非功能 / 边界 · 「回滚出口」 | 改为：「Host 单仓一个提交，回滚 = `git revert` 一个提交；不涉及 vendor / `uv.lock` / installed target。」 |
| 非功能 / 边界 · **新增「已知漂移」** | 「`real_tool_manifest.json` 里 `workspace_prepare` / `capability_build` / `capability_repair` / `external_action_wait` / `project_directory_select` 五条的 `execution_build_identity.artifacts` 仍钉着编辑前的 `orchestration_controls.py` sha256。该五条不在 `execution_build_sources.json` 的 72 个 handler 内，`generate_execution_build_manifest.py --check` 与 `validate_core_registry_handler_set` 均不覆盖，无运行时守卫。本轮不重算，Slice 2b 与该文件最终形态一起处理。」 |
| 非功能 / 边界 · **新增「followup F-WF-1」** | 「`deny_selectors` 仍屏蔽 `deepresearch` / `ppt_create` / `ppt_pro`。workflow 线删完后是否重新对模型开放这三个直连工具，由用户在 memory/task 编排大改时决定，本轮与 Slice 2 均不改。」 |
| Assurance 摘要 | 「受保护资产」保留；「可信假设」删掉「SDK 仓 main 是 0.7.10 的源」；「范围内失败」删掉 SDK 相关，保留「清单哈希不同步导致启动 RuntimeError」「旧 userdata 重启失败」，新增「`migrate_tool_schemas` 的 `unapproved schema migration` / `ledger does not match code`」。`assurance-contract.json` 同步。 |
| 测试场景矩阵 | 保留「不适用」，删掉「若前端有 workflow 相关可见入口被删，只做一次 AX 枚举」（本轮不改前端，该句移到 Slice 2a）。 |
| DoD 摘要 | 保留结构，把「Host、SDK 两仓」改为「Host 单仓」；「journal 有终态行；retro 一行」保留。 |

### 7.2 Slice 2 的 AC（另立 `acceptance-slice2.md`，本轮不写实现）

- **2a-AC-1**：`execution_uow` 键切换后同一 10 轮冒烟全绿；`workflow_service` 键从 `_VALID_SERVICES` 与全部消费点消失；`_attach_workflow_history_events` 只剩 execution_uow 路径且历史 hydrate 在冒烟里被覆盖。
- **2a-AC-2**：`main.py` 不再 import `build_workflow_service` / `WorkflowLauncher` / 任何 `deskpet.workflows.definitions.*`；启动日志无 `workflow_service_ready`；`activate_and_recover_workflows` 不再被调用。
- **2a-AC-3**：`workflow_*` IPC 分发删除后前端无未处理请求；`npm run typecheck` + vitest 全绿；一次 AX 枚举确认「更多 → ContextTrace」入口不存在。
- **2a-AC-4**：`projection_kind="workflow_progress"` 的思考摘要气泡在冒烟中仍可见（耦合点 5 的反向断言）。
- **2b-AC-1**：`deskpet/workflows/` 只剩 `store/`、`contracts.py`、`effects.py`、`errors.py`、`deadlines.py`、`trace/`（除 `observer.py`）；`import deskpet.tools.registry` 成功。
- **2b-AC-2**：`companion/turn_authority.py` 不再 import `definitions.personal_workflow`；`sdk_adapters/workflows.py` 与 `capability_catalog.py` 的 workflow 记录删除后 `SdkRunToolAuthorityRegistry` 在 `workflows=()` 下正常构造。
- **2b-AC-3**：`harness/router.py`、`child_runs.py` ticket 分支、`ports.py` DelegateRun ticket 字段删除后，旧 userdata 里已有的委派命令行可正常反序列化（旧 userdata 重启 + 一次 `spawn_subagents` 冒烟）。
- **2b-AC-4**：文档整篇回写（DeepResearch.md / PPT.md / AGENT_HARNESS.md / index.md / docs/*）+ 脚本清理。

### 7.3 Slice 3 的 AC（延后，随 memory/task 编排大改一起立）

- **3-AC-1**：SDK 全量 pytest 自建基线先落地，才允许动删除。
- **3-AC-2**：schema v10 与 `TerminationState` 字段变更同属一次版本跳变（0.8.0），存量 checkpoint 有明确的迁移或作废口径。
- **3-AC-3**：`public-api.json` 的收缩是显式记录的 breaking change，五组只增不减断言同步调整。

## 8. 主 agent 写 plan 时最容易犯的三个错误

1. **以为冻结清单只有一处哈希**。实际有四处互相绑定的守卫：`manifest.py` 的 `MANIFEST_SHA256` 常量 + JSON 内嵌 `manifest_sha256`（`load_tool_manifest` 两个都校验）、`schema_migrations.json` 顶层内嵌的 `manifest_sha256` + 那条 `workflow_spawn` 迁移行（`migrate_tool_schemas` 有 `changed != set(expected)` 硬断言，只删条目不删迁移行 → `schema migration ledger does not match code`）、`providers.py:793` 的 `77`、`conformance.py:211` 的 `77`（survey 未记，本次复核补出）。另外 `baseline.md` 里 spike 算出的 `df979c0e04…` **只有在"仅删条目 + `workflows={}` + `tool_count=76`、不动 JSON 里任何其他字节"时才成立** —— 一旦顺手去重算那五条的 `execution_build_identity`，这个 sha 立刻作废。
2. **按 `workflow` 字符串做全局清理**。三个地方会被误伤：`projection_kind="workflow_progress"` / `workflow_stage` 是当前 ReAct 思考摘要在用的（删了气泡消失）；`completion_semantics == "accepted_async"` 的排他拆批是 `tool_activate` 也在用的机制（`tests/test_deskpet_agent_loop.py:186` 的参数化要改成 `tool_activate`，不是删掉这个测试）；`deny_selectors` / `PRODUCT_ROUTE_OWNED_TOOL_NAMES` 删掉不是"删除"而是"新增"—— 会把 `ppt_create` / `deepresearch` 重新放回模型视野。`RuntimeStartAdmission` / `admit_runtime_start` 名字带 Workflow、语义是所有 Run 的启动准入，同理。
3. **在 Slice 1 里"顺手"把 `main.py` 或 SDK 一起做掉**。两者都会穿透到 `execution_uow` 归属与持久化形状（Host 的 `DelegateRun` 序列化、SDK 的 checkpoint canonical JSON），一旦混进 Slice 1，冒烟失败时就无法区分是清单改错还是装配改错，Slice 1 「一次冒烟即证伪」的价值直接归零。**Slice 1 的第一条自检就是 `git diff --stat` 里不出现 `main.py`、`deskpet/workflows/`、`tauri-app/`、`simple-harness-sdk/`。**
