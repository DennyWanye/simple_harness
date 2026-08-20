# Plan：Agent 细粒度执行时间线与上下文隔离

<!-- plan-status: finalized -->

## 主要矛盾

当前 DeskPet 已有 `HarnessPublicReadService -> semantic_projection -> PublicRunSnapshotV3`
以及 `HarnessInspectorPanel -> DurableTaskSteps`，但用户看到的是合并后的语义阶段和工具摘要，
不能像 Codex 红框一样顺着时间线看到“读取文件、运行命令、检查结果、完成”等真实公开动作。
主要矛盾不是缺少新的执行能力，而是**同一份 canonical Run ledger 的公开事实没有被展平成细粒度、
可折叠、可恢复的 activity feed**。实现必须在既有 public projection 边界内解决，不能把 raw ledger
或隐藏 reasoning 直接交给前端，更不能创建第二套 Run 状态机。

## 关联验收标准

覆盖 `AC-1` 至 `AC-7`；上下文边界以 `acceptance.md` 数据分类表为不可变契约。

## 现状与关键证据

- `backend/deskpet/execution/harness_public_read_service.py:247-329` 同时读取 workflow.db/state.db，
  生成带 read cut、projection id、diagnostics 的 immutable manifest。
- `backend/deskpet/execution/semantic_projection.py:799-957` 用稳定 fact identity、因果顺序和阶段 taxonomy
  构建 `semantic_phases.items`，重复 identity 冲突会使 projection incomplete。
- `backend/deskpet/security/tool_public_projection.py:145-217` 对工具输入/结果实行 allowlist、redaction、
  field/total byte cap 和 fail-closed truncation。
- `tauri-app/src/stores/harnessPublicSnapshotStore.ts:190-255` 是唯一 transport compatibility seam；组件不应
  直接解析原始 WebSocket payload。
- `tauri-app/src/components/AgentActivityMessage.tsx:116-215` 已按 phase/tool 归并公开消息；
  `tauri-app/src/components/workflow/DurableTaskSteps.tsx:124-193` 只渲染阶段和工具，未渲染所有公开 phase items。
- `tauri-app/src/chat/HarnessInspectorPanel.tsx:300-327` 是左侧 Inspector 的用户入口，当前只显示运行图和步骤详情。
- `backend/deskpet/memory/session_db.py:1740-1797, 2890-2999` 与 `backend/deskpet/agent/run_presenter.py:880-900`
  已将 workflow progress/companion event 作为 `context_visibility='exclude'` 写入；conversation history
  查询明确过滤 `context_visibility='conversation'`。

## 目标行为

1. Inspector 中新增“执行记录”区域，按公开事实发生顺序显示细粒度条目：阶段/narration、工具调用、
   子任务、等待、验证、失败和终态。
2. 每条条目显示用户可理解的动作、目标、状态、耗时；工具输入/结果仍单独展开，并沿用既有 public detail loader。
3. 条目按 stable id 去重、按 snapshot 的 phase/order 信息排序；终态后不显示为运行中。
4. snapshot 明确带 `context_visibility: 'exclude'`（后端默认输出，前端严格校验/降级），并新增回归测试
   证明 activity feed 不进入 `build*Context` 或 follow-up message assembly。
5. 旧 V3/legacy snapshot 仍可渲染；缺少 activity items 时从现有 `semantic_phases.items` 安全派生，不虚构步骤。

## 数据流与上下文边界

```text
canonical Run ledger
  -> HarnessPublicReadService / semantic_projection
  -> PublicRunSnapshotV3 (public projection, context_visibility=exclude)
  -> normalizePublicRunSnapshot (唯一 FE seam)
  -> buildActivityTimeline (纯函数、stable-id dedupe)
  -> ActivityTimeline / DurableTaskSteps / Inspector
```

允许进入模型上下文的仍只有既有模型协议消息：用户/assistant conversation、当前执行轮要求的
`assistant.tool_calls` 与对应 `tool` result、以及既有 memory/context assembler 明确选入的内容。
时间线阶段、UI 摘要、脱敏详情、Inspector 技术记录、provider/ledger diagnostics、event correlation
和 hidden reasoning 全部是 `context_visibility=exclude`，不写入新的 conversation message，不参与
follow-up history，也不进入 memory recall。

## 文件影响清单

| 文件 | 职责 | 本次改动 |
|---|---|---|
| `backend/deskpet/execution/run_read_model.py` | Public snapshot contracts | 增加 typed `context_visibility=exclude`、activity items 和 eager safe tool/message views |
| `backend/deskpet/execution/harness_public_read_service.py` | public read boundary | 生成与前端一致的 V3 public fields；继续 lazy-load 大详情 |
| `backend/deskpet/execution/semantic_projection.py` | causal reducer | terminal status conflict fail-closed，避免 lexicographic winner 改写终态 |
| `tauri-app/src/types/messages.ts` | Public snapshot contracts | 增加 `context_visibility` 与公开 activity item 类型 |
| `tauri-app/src/stores/harnessPublicSnapshotStore.ts` | transport normalization | 校验/默认 `context_visibility=exclude`，归一化 activity items，拒绝未知/缺根身份 |
| `tauri-app/src/components/AgentActivityMessage.tsx` | 纯 projection helpers | 增加 `buildActivityTimeline`：从显式 items 或 phase items 生成去重、有序、终态收束的 feed |
| `tauri-app/src/components/workflow/ActivityTimeline.tsx` | 用户可见时间线 | 新增可折叠细粒度条目视图，工具详情调用既有 loader；不读取 raw payload |
| `tauri-app/src/chat/HarnessInspectorPanel.tsx` + `.css` | 入口与布局 | 在运行图/阶段详情附近挂载时间线，保持 bounded scroll 和技术记录默认折叠 |
| `tauri-app/src/components/AgentActivityMessage.test.tsx` | projection tests | 覆盖正常、乱序、重复、缺字段、终态收束和 excluded 标记 |
| `tauri-app/src/chat/HarnessInspectorPanel.test.tsx` / 新 `ActivityTimeline.test.tsx` | UI tests | 覆盖入口、折叠、详情、终态、legacy fallback |
| `backend/tests/test_harness_public_read_service.py` 或现有 projection tests | contract tests | 断言 public projection 不含 raw reasoning/secret，activity metadata excluded |
| `testcase/2026-08-20-agent-activity-timeline/` | black-box/manual tests | S-1..S-5 分步用例、上下文泄漏断言、真人截图索引 |
| `ARCHITECTURE/*.md`, `ARCHITECTURE/PROJECT_STATUS.md` | 事实源回写 | 通过测试后记录生产链、边界、验证状态 |

## 任务清单

### Task 1 — Public snapshot 数据边界与后端契约  [AC-2, AC-3, AC-5]

- 改动文件：`backend/deskpet/execution/run_read_model.py`、`harness_public_read_service.py`、
  `semantic_projection.py`、`tauri-app/src/types/messages.ts`、`harnessPublicSnapshotStore.ts`。
- 修改方式：新增 typed `PublicActivityProjection`/`PublicActivityItem`，固定
  `context_visibility='exclude'`，V3 snapshot 明确携带 activity items、safe tool views 和 public messages；
  大输入/结果仍只在 `tool_details` lazy page 返回。Reducer 发现不同 terminal status 时 fail-closed
  为 incomplete/unknown，不再按 stable id 任意取胜。前端 normalize 对旧 V3/legacy 保留安全派生路径。
- 验证：后端 service/semantic tests、TS normalization tests；断言 secret/raw reasoning 不会落入任何 public field，
  terminal conflict 不会显示为成功或运行中。

### Task 2 — 细粒度 activity 纯函数投影  [AC-1, AC-4, AC-5]

- 改动文件：`tauri-app/src/components/AgentActivityMessage.tsx`。
- 修改方式：新增 `buildActivityTimeline(snapshot)`；优先使用 snapshot.activity_items，否则遍历 semantic
  phase items 与 tool views；按 phase/order/created_at/stable_id 排序，stable_id 去重，终态后把 open item
  settle 为 completed/failed/cancelled；缺 kind/title 时使用“未命名执行记录/记录不完整”，不得伪造成功。
- 验证：Vitest 覆盖乱序、重复、缺字段、超长/截断标记、晚到终态和无工具普通回答。

### Task 3 — 可折叠时间线 UI  [AC-1, AC-2, AC-6]

- 改动文件：新建 `tauri-app/src/components/workflow/ActivityTimeline.tsx` 及测试/CSS。
- 修改方式：使用稳定 icon 状态和紧凑单行摘要，条目独立 `<details>`/按钮折叠；工具条目显示 action + safe target +
  duration/status，输入/结果按钮复用 `requestHarnessPublicToolDetails`；内容超过 public cap 只显示已有截断提示。
  不在组件中解析 WebSocket、SQL、provider payload 或 reasoning。
- 验证：组件测试点击条目/工具输入/结果；检查敏感原文不渲染，长结果显示安全截断。

### Task 4 — Inspector 接线与交互状态  [AC-1, AC-2, AC-4, AC-6, AC-7]

- 改动文件：`HarnessInspectorPanel.tsx`, `HarnessInspectorPanel.css`, `ChatView.tsx`（仅必要入口文案/按钮）。
- 修改方式：在现有 Inspector 同一 read model 下增加“执行记录”区域；保持 selected root fence、polling、terminal
  settle、bounded internal scroll 和技术记录默认折叠；空态显示“Agent 还没有产生执行记录”，legacy 显示“旧版记录不可用”。
- 验证：HarnessInspectorPanel tests、TypeScript、真实桌面点击入口/展开/关闭/历史恢复。

### Task 5 — Context exclusion contract regression  [AC-3, AC-6]

- 改动文件：`tauri-app/src` context/follow-up tests、`backend/tests` context/session projection tests；必要时新增
  小型 lint/assert helper。
- 修改方式：对 normalized activity/snapshot 增加不可变 excluded 标记；context assembly 只接受 conversation-visible
  SessionDB rows，显式拒绝 activity projection；follow-up payload snapshot test 检查没有 activity item/Inspector detail。
- 验证：后端/前端聚焦回归；抓取实际 provider payload fixture 做 negative assertion。

### Task 6 — Black-box testcase、smoke 与文档 [AC-1..AC-7]

- 改动文件：`testcase/2026-08-20-agent-activity-timeline/`、`testcase/index.md`、必要时 `scripts/`。
- 修改方式：冻结 S-1..S-5 的步骤/预期，分别记录正向工具任务、无工具问答、失败文件、长上下文 follow-up、冷启动/恢复；
  保存可复跑的 snapshot normalization/context-leak smoke；原始截图/日志写入 `.local-test-evidence/`。
- 验证：testcase challenger、gate manifest applicability（input_sensitive=true, llm_payload_driven=true,
  stateful_init=true）和 phase-4 真人兑现表。

### Task 7 — Architecture/status 回写与最终门禁  [AC-1..AC-7]

- 改动文件：`ARCHITECTURE/ARCHITECTURE.md`, `AGENT_HARNESS.md`, `UI.md`, `PROJECT_STATUS.md`。
- 修改方式：通过测试后写入实际生产链、context exclusion、风险边界、测试证据与里程碑；不把计划内容冒充生产事实。
- 验证：re-attest、full-audit、`finalize`、提交态硬门和干净 checkout smoke。

## Assurance / 信任与失败边界

- Profile：`standard`，绑定 `assurance-contract.json` 的 ASSET/FAIL IDs。
- 信任边界：canonical workflow/state ledger 是事实源；public projector/redactor 是展示边界；前端只信任
  `normalizePublicRunSnapshot` 后的数据；Context OS 不信任 UI projections 作为输入。
- 失败语义：projection 不完整只能显示“记录不完整”；不能把缺字段当成功、不能重复执行工具、不能跨 root
  读取、不能因晚到事件复活终态。
- 停止追踪点：不展示 raw provider messages、完整 shell 参数/文件内容、credentials、reasoning token；不把
  timeline 发送到 provider；不改变 existing model protocol。

## 关键技术假设与 spike

- 假设 A：现有 semantic phase items 已覆盖公开工具/工作流事实；由 `semantic_projection.py:900-950` 与
  `harnessPublicSnapshotStore.ts:203-241` 静态核对确认，无需新 ledger 表。
- 假设 B：activity projection 可在前端纯函数中完成而不改变 context assembler；由已有
  `buildWorkflowTaskTraces` 和 `context_visibility` 查询路径核对确认。
- 假设 C：React 测试环境可验证折叠与 loader 交互；执行阶段用现有 Vitest harness 做 spike/回归。

## 交付单元

- MUST AC: 7；Tasks: 7；高风险子系统：frontend public projection、backend context boundary、desktop UI（3）。
- 这是一个垂直 slice，未引入 provider、权限或数据库迁移；所有任务可在当前仓库完成。

## Challenge 回填

Phase-0 architecture challenge 发现并已纳入本 plan 的 P1：

1. `context_visibility` 原先只有文档/列约定，补成 typed public projection + runtime/contract test。
2. backend V3 snapshot 原先没有前端声明的 `tool_public_views/public_messages`，补齐同一 public read cut
   的 eager safe views，详情仍 lazy，消除双 authority 漂移。
3. terminal status 冲突原先由 stable id 打破平局，改为 fail-closed incomplete/unknown。

Plan scope 未扩大到新执行能力或数据库迁移；这些修复是 AC-2/AC-3/AC-5 的必要架构边界。
