# Plan：删掉 workflow 线 · Slice 1（模型视野与死壳）

> 切片划分与 SDK 裁决见 [DECISION-SCOPE-SLICES.md](DECISION-SCOPE-SLICES.md)；边界调查见 [survey-host.md](survey-host.md) / [survey-sdk.md](survey-sdk.md)；基线与 spike 见 [baseline.md](baseline.md)；挑战第 1 轮 synthesis 见 [challenge-round-1-synthesis.md](challenge-round-1-synthesis.md)。
> 路径：delivery / FULL（动冻结工具清单）；`MACHINE_GATE` 不启用。执行模式：**集中兵力**（同一文件簇、顺序依赖、单切面）。

## 矛盾分析（第 1 轮挑战后重述）

- **主要矛盾**：把 `workflow_spawn` 的工具定义、清单条目与提示词从代码里删掉之后，主对话的主流程还能不能原样跑通。
- **事实前提**（挑战第 1 轮实测）：交付版 43 次 provider 请求里 `workflow_spawn` 与 profile catalog 提示词出现 0 次，tools 名字集合 14 个不含它。模型视野在改动前已经是空的，本轮对模型的运行时 delta 为零；风险全在「删的过程」。
- **主要方面**：① 冻结清单五处一致性守卫（`manifest.py` 常量 + 两处 77、JSON 内嵌哈希、`schema_migrations.json` 内嵌哈希 + 迁移行、`providers.py:793`、`conformance.py:211`）是否同一提交内同步——漏一处启动即 `RuntimeError`；② 不误伤当前 ReAct 在用的机制（`accepted_async` 排他拆批、`workflow_progress` 思考摘要、deny_selectors、拒绝集）。
- **最小验证动作**（价值验证里程碑 = Task 5a）：守卫自检（`load_tool_manifest` + `migrate_tool_schemas`）→ 全新 userdata 冷启动到 startup complete 且主对话就绪 → T1 一轮 COMPLETED → 对 `provider_invocations.request_json` 断言 tools 名字集合不含 `workflow_spawn`、请求体不含 catalog 提示词。分钟级。
- **决定性深测**（Task 5b）：交付版同款 10 轮冒烟 + 同 userdata 重启 + 旧 userdata 重启段，证明主流程原样跑通（AC-1）。

## 现状调查结论（本次真读）

- 生产工具注册表由冻结清单构建：`deskpet/tool_catalog/providers.py:672-800` 从 `real_tool_manifest.json` 建 77 条注册；`register_orchestration_controls`（`orchestration_controls.py:324`）生产无调用者。**删清单条目就是删工具定义。**
- 清单守卫链：`manifest.py:55-86 load_tool_manifest` 校验 JSON 内嵌 `manifest_sha256` == 常量 == 重算值，`tool_count==77`、`len(tools)==77`、`pre_cutover_count==79`、workflows 投影恰好两键（`:74-79`）；`migrate_tool_schemas`（`:187-231`）校验 `schema_migrations.json` 的 `manifest_sha256` 与清单一致、且 `changed == set(expected)`；`providers.py:793` 与 `sdk_adapters/conformance.py:211` 各一个 77 硬编码。
- 模型视野文案与登记面：`agent/turn_preparer.py:278-300` `_inject_profile_catalog`（实测从未进入 provider 请求，因为 `self._profile_registry` 在生产路径为 None 或被后续 Context OS 组装覆盖——无论哪种，删除是零 delta）；`:519-529` `core_names`；`sdk_adapters/tool_authority.py:143-175` `SDK_DIRECT_TOOL_KERNEL`（20 个，实跑 `len()`）；`sdk_adapters/tools.py:41-43` `PRODUCT_TOOL_NAMES`（84 = 清单 77 + `:49-51` `HOST_COMPOSED_TOOL_NAMES` 7，实跑）；`tool_catalog/providers.py:37` `_CONTROL_TOOLS`；`companion/turn_authority.py:716-726` route_prompt 文案。
- 拒绝集（本轮不动）：`capabilities/manifest.py:98 CORE_RESERVED_TOOL_NAMES`、`tools/prepared_snapshot.py:28` 与 `tools/skill_tools.py:314` 的 `_SKILL_SCOPE_WIDENING_CONTROLS`。去名 = 放宽，与 deny_selectors 同一原则保留，随 F-WF-1 决定。
- 注释/docstring 残留：`turn_preparer.py:543`、`agent/assembler/components/skill.py:41`、`harness/profiles.py:5`——改措辞，不动代码。
- 死壳：`harness/execution_profiles.py:48-108` 的 `WorkflowSpawnRequest` / `ProfileLaunchTicket` 生产零引用（`child_runs.py:14` 用的是 `deskpet.execution.contracts.ProfileLaunchTicket`）；`companion/workflows.py` 只被自己的测试引用。
- 不动的东西（裁决 §4/§5）：`main.py`、`deskpet/workflows/`、前端、SDK、`child_runs.py` / `harness/ports.py` 的 DelegateRun ticket 字段、`harness/router.py`、`sdk_adapters/workflows.py` / `capability_catalog.py`、`turn_preparer.py` 的 `deny_selectors`、`projection_kind="workflow_progress"`、`completion_semantics == "accepted_async"` 机制、三个拒绝集。
- 旧 userdata 事实：交付版冒烟 userdata 无 ticket 行、无 workflow run 行、Run 快照不含该名字。旧 userdata 段只能证明「启动兼容」，证明不了 ticket 行场景（已如实写进 acceptance）。

## 方案与权衡

- 选定：Slice 1 只改「模型能看见的定义与文案 + 生产零引用的死壳」，守卫五处同签，一次冷启动 + 一轮即可证伪，10 轮冒烟证明主流程原样。
- 放弃：本轮同时动 `main.py` / 图引擎 / 前端 / SDK（裁决备忘 §1–§3）；顺手清理拒绝集与 deny_selectors（那是放宽行为，不是删除）。
- 外部实践参照：内容寻址的 schema registry 删条目一律「改内容 + 重签」，不绕守卫；本项目守卫五处必须同签。

## 关键假设与实践证据

全部见 [baseline.md](baseline.md) 的 Spike 表：清单重签哈希（`df979c0e…`）、`migrate_tool_schemas` 删行后通过（76/70/71）、JSON 字节级往返（`indent=2, ensure_ascii=False` + 换行）、交付版 provider 请求统计（43 条、0 次）、`operation-audit.db` 命中词形拆解（全为 SDK 表名）、冒烟前置条件存在、SDK wheel 可重复构建（留 Slice 3）。

## 关联验收标准

AC-1（决定性）← Task 5b；AC-2（决定性）← Task 1–3 + Task 5a；AC-3 ← Task 1 + 5a；AC-4 ← Task 2/4；AC-5 ← Task 5b + Task 8 自检；AC-6 ← Task 2/6；AC-7 ← Task 6；AC-8 ← Task 8。

## 文件影响清单

| 文件 | 职责 | 现状 | 本次改动 |
|---|---|---|---|
| `backend/deskpet/tool_catalog/real_tool_manifest.json` | 冻结工具清单 | 77 条 + 两键 workflows；首键 `manifest_sha256` | 删 tools[73]（name=workflow_spawn）；`tool_count` 77→76；`workflows` 置 `{}`；`manifest_sha256` 改为 `df979c0e…` |
| `backend/deskpet/tool_catalog/schema_migrations.json` | schema 迁移账本 | 71 行 + 内嵌 manifest_sha256 | 删 `name=="workflow_spawn"` 行；`manifest_sha256` 改为新值 |
| `backend/deskpet/tool_catalog/manifest.py` | 清单加载与守卫 | `:15` 常量；`:68-70` 77；`:74-79` 投影集合；`:152-157` workspace_ref 专项迁移 | 常量改新值；77→76；投影断言改为「必须为空」；删 `:152-157` |
| `backend/deskpet/tool_catalog/providers.py` | 显式注册表 | `:37` `_CONTROL_TOOLS`；`:793` 77 | 集合去名；77→76 |
| `backend/deskpet/sdk_adapters/conformance.py` | 静态目录构建 | `:211` 77 | 77→76 |
| `backend/deskpet/sdk_adapters/tools.py` | `PRODUCT_TOOL_NAMES` | 84 名 | 去名 → 83 |
| `backend/deskpet/sdk_adapters/tool_authority.py` | `SDK_DIRECT_TOOL_KERNEL` | `:143-175`，20 名 | 去 `:173` 一行 → 19 |
| `backend/deskpet/tools/orchestration_controls.py` | 控制工具 spec | spawn 段 `:15,23,89-214,329-353,569,572` | 删 `WORKFLOW_SPAWN` 常量、`CORE_CONTROL_NAMES` 去名、删 `workflow_spawn_schema` / `_workflow_spawn_resources`、删注册分支、`__all__` 去两项；`_fail_closed` 保留 |
| `backend/deskpet/agent/turn_preparer.py` | 每轮准备 | `:278-300` 注入 catalog；`:520-529` core_names；`:543` 注释 | 删 `_inject_profile_catalog` 方法与 `:575` 调用（`self._profile_registry` 字段与构造参数保留）；core_names 去名；`:543` 注释改措辞；`:548-555` deny_selectors 代码不动 |
| `backend/deskpet/agent/assembler/components/skill.py` | 注释 | `:41` | 注释改措辞 |
| `backend/deskpet/harness/profiles.py` | docstring | `:1-7` | docstring 去掉 `workflow_spawn` 措辞 |
| `backend/deskpet/companion/turn_authority.py` | companion 轮准备 | `:716-726` route_prompt | 表达式改为恒 `""`；其余保留 |
| `backend/deskpet/harness/execution_profiles.py` | profile 契约 | `:48-108` 两个死 dataclass；`hashlib` / `json` / `Mapping` 三个 import 只被 `WorkflowSpawnRequest.fingerprint` 用 | 删两个 dataclass、`__all__` 对应项与三个独占 import；保留 `Literal`、`dataclass`、`JsonValue`；docstring 去掉 launch contracts 措辞 |
| `backend/deskpet/companion/workflows.py` | workflow overlay 校验 | 生产零引用 | 整文件删除 |
| `backend/tests/companion/test_workflow_pack_adapter.py` | 上者的测试 | — | 删除 |
| `backend/tests/sdk_adapters/test_tool_catalog.py` | 清单测试 | 见 Task 6 | 见 Task 6 |
| `backend/tests/test_deskpet_agent_loop.py` | 排他拆批与 reasoning 重试测试 | `:129-174,:186,:216-220,:260-274` | 样例名改 `capability_build` / `tool_activate`，条件同步，机制测试保留 |
| `backend/tests/test_p5s2_sse_diagnostic.py` | SSE 诊断 | `:220,254,280,312` 任意工具名 | 改 `tool_activate` |
| `CHANGELOG.md`、`ARCHITECTURE/AGENT_HARNESS.md`、`ARCHITECTURE/AgentLoop.md`、`ARCHITECTURE/PROJECT_STATUS.md` | 文档 | — | 各一条中文记录 |

不动：`tests/companion/test_skill_runtime_snapshot.py`（拒绝集不动，测试仍成立）、`tests/capabilities/test_failure_receipts.py`（不引用 spawn）。

## Complexity inventory

| 复杂度表面 | 本次是否新增 | 理由 |
|---|:---:|---|
| 新 service_context 键 / 新模块 / 新脚本 | 否 | 不动装配；复用 `twoflow_driver.sh` |
| 新测试文件 | 否 | 只改既有测试 |

## 任务清单（最短价值路径优先）

### Task 1 — 冻结清单与守卫五处同签  [AC-2、AC-3]
- 改动：`real_tool_manifest.json`、`schema_migrations.json`、`manifest.py`、`providers.py:793`、`conformance.py:211`。
- 步骤（scratchpad 一次性脚本，不入库）：读 JSON → `sha_old = raw.pop("manifest_sha256")` → 删 `workflow_spawn` 条目、`tool_count=76`、`workflows={}` → `sha_new = canonical_hash(raw)`；断言 `sha_new == "df979c0e044112338e0531d53e6d5906767b6ef0fbdaa312fec7d8f162790bc4"`（不等即说明动了别的内容，停）→ 以 `{"manifest_sha256": sha_new, **raw}` 重建 dict → `json.dumps(obj, indent=2, ensure_ascii=False) + "\n"` 写回。`schema_migrations.json` 同法（先对原文做一次 dump 往返断言逐字节相同，再改）。`manifest.py`：常量；`:68-70` 两个 77→76；`:74-79` 改为 `if workflows: raise RuntimeError("real Tool manifest workflow projection must be empty")`；删 `:152-157`。`providers.py:793`、`conformance.py:211` 77→76。
- 什么算对：`backend/.venv/bin/python -c "from deskpet.tool_catalog.manifest import load_tool_manifest, migrate_tool_schemas, MANIFEST_SHA256; m=load_tool_manifest(); assert MANIFEST_SHA256.startswith('df979c0e'); assert len(m.tools)==76 and 'workflow_spawn' not in m.tool_names and m.workflows=={}; mig,rec=migrate_tool_schemas(m); assert len(mig)==76 and len(rec)==70"` 退出 0；`git diff --stat` 两个 JSON 的改动行数只在被删条目与三个字段附近。
- 依赖：无。

### Task 2 — 模型视野与登记面去名 + 注释措辞  [AC-2、AC-4、AC-6]
- 改动：`sdk_adapters/tools.py`、`sdk_adapters/tool_authority.py`、`tool_catalog/providers.py:37`、`agent/turn_preparer.py`（删 `_inject_profile_catalog` 与 `:575` 调用、core_names 去名、`:543` 注释）、`agent/assembler/components/skill.py:41` 注释、`harness/profiles.py` docstring、`companion/turn_authority.py` route_prompt 置空。
- 不动：`deny_selectors` / `PRODUCT_ROUTE_OWNED_TOOL_NAMES`；三个拒绝集。
- 什么算对：`python -c "from deskpet.sdk_adapters.tool_authority import SDK_DIRECT_TOOL_KERNEL as K; from deskpet.sdk_adapters.tools import PRODUCT_TOOL_NAMES as P, HOST_COMPOSED_TOOL_NAMES as H; from deskpet.tool_catalog.manifest import load_tool_manifest as L; assert 'workflow_spawn' not in K and len(K)==19; assert set(P)==set(L().tool_names)|set(H) and len(P)==83; assert {'agent','agent_parallel','spawn_subagents','spawn_team','await_subagents'} <= K"`；`grep -rn workflow_spawn deskpet/sdk_adapters deskpet/agent deskpet/harness/profiles.py deskpet/harness/execution_profiles.py deskpet/companion/turn_authority.py deskpet/tool_catalog/providers.py` 为空。
- 依赖：Task 1。

### Task 3 — `orchestration_controls.py` 摘 spawn 段  [AC-2、AC-4]
- 改动：删 `:15` 常量、`:23` 集合项、`:89-214` schema 与资源解析器（含 `return resolve`）、`:329-353` 注册分支（`if registry.has(WORKFLOW_SPAWN): ... else: registry.register(...)` 整段，`:354` 起的 `if not registry.has(WORKSPACE_PREPARE)` 保留）、`__all__` 里 `:569` `"WORKFLOW_SPAWN"` 与 `:572` `"workflow_spawn_schema"` 两行（`:573` 的 `]` 保留）；`_fail_closed` 保留；`register_orchestration_controls(registry, profiles)` 签名不变。
- 什么算对：`python -c "import deskpet.tools.orchestration_controls as m; assert not hasattr(m,'WORKFLOW_SPAWN') and 'workflow_spawn' not in m.CORE_CONTROL_NAMES and hasattr(m,'_fail_closed')"`；`pytest tests/capabilities/test_failure_receipts.py -q` 全绿。
- 依赖：无（与 Task 2 同 commit）。

### Task 4 — 删死壳  [AC-4]
- 改动：`harness/execution_profiles.py` 删两个 dataclass、`__all__` 项与 `hashlib` / `json` / `Mapping` 三个独占 import（`ruff check --select F401` 该文件为空）；`git rm` `companion/workflows.py`、`tests/companion/test_workflow_pack_adapter.py`。
- 什么算对：`python -c "from deskpet.harness.profiles import ProfileRegistry, ProfileSpec"`；`grep -rn "companion.workflows\|companion import workflows" backend` 为空；`grep -n "ProfileLaunchTicket\|WorkflowSpawnRequest" backend/deskpet/harness/execution_profiles.py` 为空。
- 依赖：无。

### Task 5a — 价值验证里程碑：守卫自检 + 冷启动 + 一轮 + provider 请求断言  [AC-2、AC-3（决定性）]
- 前置：Task 1–4 完成，Task 6 的七文件测试已跑（便宜门）。
- 步骤：① Task 1 的自检命令；② `lsof -nP -iTCP:18120` 无监听、`pgrep -f '/Contents/MacOS/simple-harness'` 为空；③ 全新 userdata 启动（命令同交付版 SMOKE，`--evidence-root .local-test-evidence/2026-09-09/remove-workflow-slice1`），startup complete 后写 `model_overrides.toml`（`context_window = 32000`、`reasoning_mode = "fast"`），按交付版做法生效；④ `A6_SEND=… A6_AXDUMP=… TF_TURNS="1" bash scripts/native/twoflow_driver.sh <bundle-id> <E1>/userdata <E1> flow1`；⑤ 对 `<E1>/userdata/data/simple-harness-sdk/execution-v6.sqlite3` 跑 baseline.md 同款统计脚本。
- 什么算对：`native.log` 无 `RuntimeError` / `Application startup failed`；主对话就绪；T1 `COMPLETED` 且 `sends==1`；provider 请求 `workflow_spawn` 0 次、catalog 提示词 0 次、tools 名字集合 ⊆ 交付版的 14 个。
- FAIL → 立即停，A2 回炉；不得先做 Task 6–8。
- PASS → 一句用户语言汇报 + 矛盾转化再分析（写 `milestone.md`）。
- 依赖：Task 1–4。

### Task 5b — 决定性深测：10 轮冒烟 + 同 userdata 重启 + 旧 userdata 段  [AC-1、AC-5]
- 步骤：接 5a 的实例继续 `TF_TURNS="2 3 5 9 11"` flow1 → 三步杀干净 → `--userdata <E1>/userdata` 重启 → 就绪 → `TF_TURNS="12 14 16 22"` flow2；旧 userdata 段：复制交付版 `primary-ui-ncw5jyyg/userdata` 为 `<E3>/userdata`，`--userdata` 启动 → 就绪 → `TF_TURNS="12"` 一轮。
- 什么算对：`twoflow-progress.jsonl` 10 轮全部 `COMPLETED` 且 `sends==1`；T12 答出「霜降素材 / 成片」；T14 从 task scope 复述决定；已知偏差按交付版记录预期：T16 流程被召回但任务目录可能不落 `inventory.md`，T22 因子集未跑 T7 可能答「已完成」——这两条与交付版一致即 PASS，出现**新**形态才算回归；重启后主对话就绪；旧 userdata 段就绪 + 一轮 COMPLETED（只证启动兼容）。
- 依赖：Task 5a。

### Task 6 — 受影响测试对齐  [AC-6、AC-7]
- `tests/sdk_adapters/test_tool_catalog.py`：`:12-14` sha 改新值；`:131` 函数改名 `test_checked_in_real_manifest_has_exact_76_and_empty_projection`，`:138` 76，`:139-142` 改 `assert dict(manifest.workflows) == {}`；`:148-149` 的嵌套写断言在空投影下会先抛 `KeyError`（实跑），改为 `with pytest.raises(TypeError): manifest.workflows["x"] = {}`（实跑为 TypeError）；`:158` 71→70；`:168-180` specialized 去 `workflow_spawn`；`:182` 77→76；`:183` 总和 71 不变；`:228` 函数名 `..._reaches_74_...` → `..._reaches_73_...`，`:283` 与 `:297` 74→73（`:296` 是 `)`）；`:425` 与 `:429` 77→76；`:433` 函数改名 `test_each_of_63_...`，`:446` 64→63；删 `:585-588`；删 `:774-778` 用例、`:780` 12→11；`:978` 77→76。
- `tests/test_deskpet_agent_loop.py`：`:129-174` 用例的工具名改 `tool_activate`（参数 `{"name": "ppt_create"}`），reasoning 文案与断言随改；`:186` 参数化改 `["capability_build", "tool_activate"]`；`:216-220` 条件改 `exclusive_name == "capability_build"`；`:260-274` 的 `workflow_spawn` 改 `capability_build`，`completion_semantics={"capability_build": "accepted_async"}`。机制测试保留。
- `tests/test_p5s2_sse_diagnostic.py`：`:220,254,280,312` 改 `tool_activate`。
- 什么算对：baseline.md 七文件命令，失败集合 ⊆ 那两条。
- 依赖：Task 1–4。

### Task 7 — 独立 code review + 分层复验  [phase-3 A3/A4]
- 派 Opus 5 子代理对累计 diff 做正确性 review（上下文包：acceptance、本 plan、diff、裁决 §8、synthesis）；P0/P1 修复各配一条决定性测试；复验 = Task 6 全量 + 若修复动了运行时代码则重跑 Task 5a。
- 依赖：Task 5b、6。

### Task 8 — 文档回写 + 自检 + 提交  [AC-8]
- `CHANGELOG.md` 一条；`ARCHITECTURE/AGENT_HARNESS.md`、`ARCHITECTURE/AgentLoop.md` 各一条中文记录（`workflow_spawn` 工具面已下线；图引擎、启动装配、前端与 SDK 待 Slice 2/3；SDK 保持 0.7.10；已知漂移：五条 orchestration 工具的 `execution_build_identity` 钉的旧 sha256；拒绝集与 deny_selectors 随 F-WF-1）；`ARCHITECTURE/PROJECT_STATUS.md` 一行。
- 自检：`git diff --stat` 不含 `backend/main.py`、`backend/deskpet/workflows/`、`tauri-app/`；SDK 仓 `git status --porcelain` 仍只有 ` M .gitignore`。
- 单个提交（Host 仓）。
- 依赖：Task 7。

## 入口链 / 数据流 / 停止追踪点

- 入口链：`main.py` 启动 → `providers.build_explicit_product_tool_catalog`（读清单）→ `build_product_tool_registry` → `SdkRunToolAuthorityRegistry` / `PreparedToolSet` → provider 请求 tools 数组。只改清单与名字集合，入口链不动。
- 数据流：清单 → 注册表 → 每轮 `PreparedToolSet`。交付版 userdata 的 Run 快照不含 `workflow_spawn`（实测），旧 userdata 段只验证启动与继续对话。
- 停止追踪点：SDK 内核（0.7.10 不动）；`deskpet/workflows/` 与 `main.py`（Slice 2）。
