# 验收标准：删掉 workflow 线 · Slice 1（模型视野与死壳）

> 2026-09-09 用户原话：「删掉这个 workflow 整个一条线」。调查确认生产装配里 Workflow Driver 从未注册、`workflow_spawn` 可选 profile 只有 `agent.general`、9 月证据里 0 次调用；用户已决定 memory/task 编排将大改，先删空壳。
> **本文只覆盖 Slice 1**；切片划分与 SDK 裁决见 [DECISION-SCOPE-SLICES.md](DECISION-SCOPE-SLICES.md)，Slice 2（启动装配 + 图引擎 + 前端）与 Slice 3（SDK）的验收见文末登记。第 1 轮挑战后的修订见 [challenge-round-1-synthesis.md](challenge-round-1-synthesis.md)。
> 路径：delivery / FULL（动冻结工具清单，向高判）；`MACHINE_GATE` 不启用，完成记录 = journal。

## 矛盾分析（全文档的骨架）

- **主要矛盾**：把 `workflow_spawn` 的工具定义、清单条目与提示词从代码里删掉之后，主对话的主流程还能不能原样跑通。
- **产生原因**：这条线在生产里是空壳，实测交付版 43 次 provider 请求里 `workflow_spawn` 与 profile catalog 提示词出现 0 次，模型本来就看不见它；风险全在「删的过程」——它的定义被冻结清单的五处一致性守卫钉死，漏改一处启动即 `RuntimeError`；而按 `workflow` 字符串全局清理又会误伤当前 ReAct 在用的机制（排他拆批、思考摘要投影、拒绝集、deny_selectors）。
- **解决方向**：只删定义、清单条目、提示词与生产零引用的死壳，五处守卫同一提交内同签；`main.py`、`deskpet/workflows/`、前端、Harness SDK 本轮 diff 为零；拒绝集与 deny_selectors 不动。
- **最小验证动作**：守卫自检（`load_tool_manifest` + `migrate_tool_schemas`）→ 全新 userdata 冷启动到 startup complete 且主对话就绪 → 一轮 COMPLETED → 对 `provider_invocations.request_json` 断言 tools 名字集合不含 `workflow_spawn`、请求体不含 catalog 提示词。分钟级。
- **矛盾的主要方面**：冻结工具清单五处一致性守卫（`manifest.py` 常量与两处 77、JSON 内嵌哈希、`schema_migrations.json` 内嵌哈希与迁移行、`providers.py:793`、`conformance.py:211`）是否同步。守卫对了，删除是机械活；漏一处，启动即失败。

## 范围

- 包含（Slice 1 改动清单）：
  - 冻结清单：`deskpet/tool_catalog/real_tool_manifest.json`（删 `workflow_spawn` 条目、`tool_count` 77→76、`workflows` 置 `{}`、重签）、`schema_migrations.json`（删迁移行、重签）、`manifest.py`（常量、计数、投影断言改为「必须为空」、删 `workspace_ref` 专项迁移分支）。
  - 计数守卫：`tool_catalog/providers.py:793`、`sdk_adapters/conformance.py:211`。
  - 登记/暴露集去名：`sdk_adapters/tools.py` `PRODUCT_TOOL_NAMES`（84→83）、`sdk_adapters/tool_authority.py` `SDK_DIRECT_TOOL_KERNEL`（20→19）、`tool_catalog/providers.py` `_CONTROL_TOOLS`、`agent/turn_preparer.py` `core_names`。
  - 模型视野文案：`agent/turn_preparer.py` 的 `_inject_profile_catalog`（整个方法与调用点删除）；`companion/turn_authority.py` 的 `route_prompt` 文案置空。
  - 注释/docstring 措辞：`agent/turn_preparer.py:543`、`agent/assembler/components/skill.py:41`、`harness/profiles.py` 模块 docstring。
  - 控制工具 spec：`tools/orchestration_controls.py` 的 spawn 段。
  - 死壳：`harness/execution_profiles.py` 的 `WorkflowSpawnRequest` / `ProfileLaunchTicket`；`companion/workflows.py` 整删。
  - 测试：`tests/sdk_adapters/test_tool_catalog.py`、`tests/test_deskpet_agent_loop.py`、`tests/test_p5s2_sse_diagnostic.py` 改；`tests/companion/test_workflow_pack_adapter.py` 删。
  - 文档：`CHANGELOG.md`、`ARCHITECTURE/AGENT_HARNESS.md`、`ARCHITECTURE/AgentLoop.md`、`ARCHITECTURE/PROJECT_STATUS.md` 各一条。
- 明确不包含：
  - `main.py` 与 `deskpet/workflows/` 任何一行（Slice 2a/2b）。
  - 前端任何一行（Slice 2a）。
  - `child_runs.py` / `harness/ports.py` 的 DelegateRun ticket 字段 / `harness/router.py`（Slice 2b）。
  - `sdk_adapters/workflows.py` 与 `capability_catalog.py`（Slice 2b）。
  - **三个拒绝集**（`capabilities/manifest.py` `CORE_RESERVED_TOOL_NAMES`、`tools/prepared_snapshot.py` 与 `tools/skill_tools.py` 的 `_SKILL_SCOPE_WIDENING_CONTROLS`）与 `turn_preparer.py` 的 `deny_selectors` / `PRODUCT_ROUTE_OWNED_TOOL_NAMES`：去名是放宽不是删除，随 followup F-WF-1 由用户在编排大改时决定。
  - Harness SDK：本轮保持 0.7.10，不动一行；理由见裁决备忘 §3。
  - `deskpet/workflows/store/` 与 SQLite 表结构；`spawn_subagents` 一族；memory / task 编排行为及其长旅程、语料测试；旧 bundle 与旧证据清理。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 矛盾地位 | 优先级 |
|----|--------|-------------------|----------|--------|
| AC-1 | 主流程原样跑通 | 交付版同款 10 轮冒烟（`TF_TURNS="1 2 3 5 9 11"` flow1、同 userdata 重启、`TF_TURNS="12 14 16 22"` flow2）：startup complete、主对话就绪；10 轮全部首次发送即 COMPLETED（`sends==1`）；T12 答出「霜降素材 / 成片」、T14 从 task scope 复述决定；交付版记录的两条已知偏差（T16 可能不落 `inventory.md`、T22 因子集未跑 T7 可能答「已完成」）与交付版一致即 PASS，出现新形态才算回归；重启后主对话就绪。另用交付版 `primary-ui-ncw5jyyg` 的旧 userdata 跑一次重启段（就绪 + 一轮 COMPLETED，只证启动兼容） | 决定性 | 必须 |
| AC-2 | 模型视野与代码里没有 workflow_spawn 的定义 | ① `real_tool_manifest.json` 无 `workflow_spawn` 条目、`tool_count` 76、`workflows == {}`、`manifest_sha256` 与 `manifest.py` 常量同为 `df979c0e…`；② `SDK_DIRECT_TOOL_KERNEL`（19）、`PRODUCT_TOOL_NAMES`（83，且 `set(P) == set(manifest.tool_names) | set(HOST_COMPOSED_TOOL_NAMES)`）、`_CONTROL_TOOLS`、`core_names` 均无 `workflow_spawn`；`orchestration_controls` 无 `WORKFLOW_SPAWN` / `workflow_spawn_schema`；③ 冒烟全新 userdata 的 `execution-v6.sqlite3` `provider_invocations.request_json`：tools 名字集合不含 `workflow_spawn`、请求体不含「Execution profiles are model-selected」（改前亦为 0，这是不回归断言；`workflow_spawn_*` 表名与 checkpoint 字段名属 SDK schema，不计）；④ `turn_preparer.py` 无 `_inject_profile_catalog`；⑤ `companion/turn_authority` 的 `route_prompt` 恒为空串 | 决定性 | 必须 |
| AC-3 | 冻结清单一致性 | 五处守卫同步且启动无 `RuntimeError`：`python -c "from deskpet.tool_catalog.manifest import load_tool_manifest, migrate_tool_schemas; m=load_tool_manifest(); mig,rec=migrate_tool_schemas(m); assert len(m.tools)==76 and len(rec)==70"` 无异常；两个 JSON 保持 `indent=2, ensure_ascii=False` + 结尾换行的原格式；全新 userdata 冷启动到 startup complete | 次要 | 必须 |
| AC-4 | 死壳与残留措辞删干净 | 在 `backend/` 下跑 `git ls-files -z | xargs -0 grep -lw workflow_spawn | sort`（只比对 stdout，不看退出码），命中文件集合恰好等于白名单：三个拒绝集文件（`deskpet/capabilities/manifest.py`、`deskpet/tools/prepared_snapshot.py`、`deskpet/tools/skill_tools.py`）、`deskpet/workflows/store/execution_uow.py`、`main.py`、`main.py.bak2` / `.bak3` / `.bak4`（历史备份，本轮不动）、`scripts/generate_harness_public_fixture.py`、`tests/companion/test_skill_runtime_snapshot.py`（拒绝集样例）、`tests/test_ppt_editable_contract.py`（提示词文本）、21 个 `vendor/simple_harness_sdk-*.whl`（共 11 个非 vendor 文件 + 21 个 wheel = 32 个）；另注 `scripts/perf/x2_memory_lanes_growth.py:364` 只含 `_workflow_spawn_ready_activations` 子串，`-w` 不命中，不在等式内；**第二条等式（防 `WORKFLOW_SPAWN` / `workflow_spawn_schema` / `_workflow_spawn_resources` / `workflow-spawn-parent-scope` 这类 `-w` 盲点残留）**：`git ls-files -z | xargs -0 grep -li 'workflow[ _-]*spawn' | sort` 的非 vendor 命中恰好 = 上述 11 个白名单文件 + `scripts/perf/x2_memory_lanes_growth.py`（共 12 个），vendor 仍为 21 个 wheel；改前该命令还命中 `agent/assembler/components/skill.py`、`agent/turn_preparer.py`、`companion/turn_authority.py`、`harness/profiles.py`、`sdk_adapters/tool_authority.py`、`sdk_adapters/tools.py`、`tool_catalog/{manifest.py,providers.py,real_tool_manifest.json,schema_migrations.json}`、`tools/orchestration_controls.py`、`tests/{sdk_adapters/test_tool_catalog.py,test_deskpet_agent_loop.py,test_p5s2_sse_diagnostic.py}`，改后这些都必须不再命中；`harness/execution_profiles.py` 无 `ProfileLaunchTicket` / `WorkflowSpawnRequest`；`deskpet/companion/workflows.py` 已删除；`harness/profiles.py`、`assembler/components/skill.py`、`turn_preparer.py:543` 的注释不再提 `workflow_spawn` | 次要 | 必须 |
| AC-5 | 边界之外零改动 | Host 仓 `git diff --stat`（相对 baseline `03de5052`）不含 `backend/main.py`、`backend/deskpet/workflows/`、`tauri-app/`；`simple-harness-sdk` 仓相对 baseline 不变（HEAD `fd12e7dd`，`git status --porcelain` 仍只有 ` M .gitignore`）；交付版旧 userdata 用新代码可重启并继续一轮（与 AC-1 旧 userdata 段合并取证） | 次要 | 必须 |
| AC-6 | 非 ticket 委派路径与 ReAct 机制不回归 | `SDK_DIRECT_TOOL_KERNEL` 里 `agent` / `agent_parallel` / `spawn_subagents` / `spawn_team` / `await_subagents` 仍在且 `PRODUCT_TOOL_NAMES` 仍含它们；`tests/test_deskpet_agent_loop.py` 的排他拆批机制测试保留（`accepted_async` 与 `tool_activate` 两个分支各有覆盖）并通过；`projection_kind="workflow_progress"` 相关代码零改动 | 次要 | 必须 |
| AC-7 | 测试基线 | baseline.md 的七文件命令加上本轮新增的决定性回归 `tests/test_turn_preparer_static_helpers.py`（code review P0 的回归）单进程一起跑，失败集合是那两条既有红的子集（允许变绿，不允许新增红）；四个相对断言型测试文件（`test_product_host_ports` / `test_s5b_acceptance_matrix` / `test_tool_activate_unavailable_disclosure` / `test_tool_authority`）已核无硬编码计数，不入门 | 次要 | 必须 |
| AC-8 | 文档回写 | `CHANGELOG.md` 一条；`ARCHITECTURE/AGENT_HARNESS.md` 与 `ARCHITECTURE/AgentLoop.md` 各一条中文记录（`workflow_spawn` 工具面已下线、图引擎与启动装配待 Slice 2、SDK 保持 0.7.10、已知漂移、F-WF-1）；`ARCHITECTURE/PROJECT_STATUS.md` 一行切片进度 | 次要 | 必须 |

## 非功能 / 边界

- **启动兼容**：旧 userdata 用新代码启动不得报错；不做数据迁移。已知交付版 userdata 无 ticket 行、无 workflow run 行、Run 快照不含该名字，所以旧 userdata 段只证明启动兼容，证明不了「含 ticket 行的旧库」场景（本机没有这种库）。
- **冻结清单一致性**：五处守卫必须同一提交内同步；重签前先 pop `manifest_sha256` 再算 `canonical_hash`，写回时 `manifest_sha256` 仍为首键，文件以 `indent=2, ensure_ascii=False` + 结尾换行写出。
- **已知漂移（不在本轮处理）**：`real_tool_manifest.json` 里 `workspace_prepare` / `capability_build` / `capability_repair` / `external_action_wait` / `project_directory_select` 五条的 `execution_build_identity.artifacts` 仍钉着编辑前的 `orchestration_controls.py` sha256；它们不在 `execution_build_sources.json` 的 72 个 handler 内，无运行时或测试守卫。Slice 2b 与该文件最终形态一起处理。
- **followup F-WF-1**：`deny_selectors` 仍屏蔽 `deepresearch` / `ppt_create` / `ppt_pro`；三个拒绝集仍含 `workflow_spawn`。是否放开由用户在编排大改时决定。
- **禁止按 `workflow` 字符串全局清理**：`projection_kind="workflow_progress"` / `workflow_stage`、`completion_semantics == "accepted_async"` 机制、`RuntimeStartAdmission` 等一字不改。
- **回滚出口**：Host 单仓一个提交，回滚 = `git revert`；不涉及 vendor / `uv.lock` / installed target。

## Assurance 摘要

- Profile：standard
- 受保护资产：证据账本与 task scope 的 durable 数据；交付版 `ec7b28c7` 的主流程行为；当前 ReAct 在用的机制（思考摘要投影、排他拆批、deny_selectors、拒绝集）。
- 可信假设：本机 venv、installed target、DeepSeek flash 凭据与 09-09 冒烟时一致。
- 范围内失败：清单守卫不同步导致启动 `RuntimeError` 或 `migrate_tool_schemas` 报错；误伤 ReAct 机制；旧 userdata 重启失败或主对话不就绪；非 ticket 委派集合被误删。
- 最大可接受影响：冒烟任一轮首次发送不 COMPLETED 即本次 FAIL，不得交付。
- 另附 `assurance-contract.json`。

## 测试场景矩阵

不适用：删除线不是输入语义敏感功能，AC-1 复用交付版冒烟的固定 10 轮脚本。

## 完成的定义（DoD 摘要）

- AC-1 与 AC-2 实测达成（任一 FAIL 时其余 PASS 不能救场）。
- AC-3 至 AC-8 每条有实测证据；Host 单仓提交态干净；journal 有终态行；retro 一行。

## Slice 2 / Slice 3 的验收（本轮不实现，先登记）

- **2a-AC-1** `execution_uow` 键切换后同一 10 轮冒烟全绿；`workflow_service` 键从 `_VALID_SERVICES` 与全部消费点消失；历史 hydrate 只剩 execution_uow 路径且被冒烟覆盖。
- **2a-AC-2** `main.py` 不再 import `build_workflow_service` / `WorkflowLauncher` / `deskpet.workflows.definitions.*`；启动日志无 `workflow_service_ready`；`activate_and_recover_workflows` 不再被调用。
- **2a-AC-3** `workflow_*` IPC 分发删除后前端无未处理请求；`npm run typecheck` + vitest 全绿；一次 AX 枚举确认「更多 → ContextTrace」入口不存在。
- **2a-AC-4** `projection_kind="workflow_progress"` 的思考摘要气泡在冒烟中仍可见。
- **2b-AC-1** `deskpet/workflows/` 只剩 `store/`、`contracts.py`、`effects.py`、`errors.py`、`deadlines.py`、`trace/`（除 `observer.py`）；`import deskpet.tools.registry` 成功。
- **2b-AC-2** `companion/turn_authority.py` 不再 import `definitions.personal_workflow`；`sdk_adapters/workflows.py` 与 `capability_catalog.py` 的 workflow 记录删除后 `SdkRunToolAuthorityRegistry` 在 `workflows=()` 下正常构造。
- **2b-AC-3** `harness/router.py`、`child_runs.py` ticket 分支、`ports.py` DelegateRun ticket 字段删除后，旧 userdata 里已有的委派命令行可正常反序列化（旧 userdata 重启 + 一次 `spawn_subagents` 冒烟）。
- **2b-AC-4** 文档整篇回写（DeepResearch.md / PPT.md / AGENT_HARNESS.md / index.md / docs/*）+ 脚本清理；三个拒绝集与 deny_selectors 按 F-WF-1 决定。
- **3-AC-1** SDK 全量 pytest 自建基线先落地，才允许动删除。
- **3-AC-2** schema v10 与 `TerminationState` 字段变更同属一次版本跳变（0.8.0），存量 checkpoint 有明确迁移或作废口径。
- **3-AC-3** `public-api.json` 的收缩是显式记录的 breaking change，五组只增不减断言同步调整。
