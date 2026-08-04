<!-- plan-status: finalized -->
# Plan：DeepResearch 简化编排与调研 Spike

## 主要矛盾

决定成败的核心问题不是检索/抓取/引用能力缺失，而是生产默认 v6 把普通调研绑定到 11 节点专题证据图；仓库已有的 manager-style fan-out core 没成为 durable 默认，而且坏子结果只被跳过，没有“诊断—续跑—再验收”。本计划只替换新 run 的编排层，复用全部搜索、抓取、Scheduler、报告、Artifact 和 durable runner。

## 最佳实践与本项目适配

- OpenAI Agents SDK 的官方 orchestration 文档区分“manager agent 把 specialist agents 当工具调用”和 handoff；本需求要求主 Agent 保留最终答案控制权，因此采用 manager/agents-as-tools，不把用户会话 handoff 给任一子代理。来源：`https://openai.github.io/openai-agents-python/multi_agent/`。
- Python `asyncio.TaskGroup` 会在首个非取消异常时取消其余 sibling；调研子方向必须独立失败、其余方向继续，因此本项目继续使用现有 `SubagentScheduler` + `asyncio.gather(..., return_exceptions=True)`，由主 Agent逐项分类，而不把 TaskGroup 的 fail-fast 语义硬套进来。来源：`https://docs.python.org/3/library/asyncio-task.html#task-groups`。
- 本项目是单机单事件循环，已有 global/research lane 双 semaphore、取消传播和进度事件；不引入 Celery、Redis、外部 agent framework 或第二套任务注册表。
- workflow 版本和 checkpoint identity 已冻结为 immutable 合同，因此新增 v7 并保留 v1-v6，而不原地改 v6 manifest。

## 关联验收标准

覆盖 AC-1 至 AC-11；测试映射以 `acceptance.md` 为唯一事实源。

## 文件影响清单

| 文件 | 职责 | 本次改动 |
|---|---|---|
| `backend/deskpet/tools/research_tools.py` | legacy research core、fan-out、报告综合 | 抽出子结果评估/诊断/续跑收集逻辑；保留兼容 `_run_subagent_fanout()` |
| `backend/deskpet/workflows/definitions/v7/deep_research.py` | 新简化 durable graph | 新增 normalize/decompose/research_children/synthesize/persist/finalize |
| `backend/deskpet/workflows/contracts.py` | workflow runtime port allowlist | 增加显式 `subagent_scheduler` port，避免借用 observer/tool 等错位端口 |
| `backend/deskpet/workflows/definitions/v7/__init__.py` | v7 注册面 | 导出并注册 immutable v7 |
| `backend/deskpet/workflows/bootstrap.py` | 全版本恢复注册 | 注册 v7，同时保留 v1-v6 |
| `backend/main.py` | runtime adapter 与新 run 入口 | 增加 v7 state/context adapter；context 复用 Search Gateway + scheduler；默认选择 v7 |
| `backend/config.py`、`config.toml`、`backend/userdata/config.toml` | factory/dev 默认 | 允许并默认 v7；新增 retry 上限配置时默认开启 |
| `backend/deskpet/workflows/progress.py` | 单卡安全进度 | 为 v7 四个业务阶段提供稳定中文映射，child 细节只进安全 metrics/log |
| `backend/tests/test_deepresearch_simplified_v7.py` | 新行为焦点测试 | 覆盖拆题、并行、坏结果诊断续跑、耗尽降级、统一报告、单次交付、恢复兼容 |
| `ARCHITECTURE/*`、`testcase/*` | 事实源与验收 | 回写 v7 生产链路、完成度、spike 证据和用例索引 |

## 任务清单

### Task 1 — 建立可测试的子结果监控与续跑内核 `[AC-2, AC-3, AC-4, AC-8]`

- 改动文件：`backend/deskpet/tools/research_tools.py`。
- 现状：`_run_subagent_fanout()` 一次 gather；异常/非法类型直接 failed，任意 `ResearchReport` 都记 completed，即使 0 引用/空正文；没有 attempt、诊断或续跑。
- 修改方式：
  - 新增内部 `FanoutCollection`（`sub_reports`、`child_records`、`errors`、`route`）作为“收集”和“综合”的明确边界；`child_records` 只存稳定 id、question、attempt、status、diagnosis code、source/domain count 与耗时。
  - 新增稳定 child id、attempt record 与 `valid/retryable/insufficient/fatal` 分类。
  - 最低 valid 条件：非空报告正文、至少一个 citation/source、引用检查不出现悬空引用；否则 retryable。
  - retryable 时由主 Agent LLM 基于“子方向 + 错误码 + coverage 摘要”生成短诊断与 continuation instruction；解析失败使用确定性补救指令。
  - 同一子方向通过 Scheduler 继续，task/run id 带 attempt；最多 2 次补救；其他 sibling 不因单项失败取消。
  - 抽出“只收集不综合”结果，旧 `_run_subagent_fanout()` 调它后继续调用现有 `_fanout_synthesize()`，保持 legacy 兼容。
- 验证：mock 一个 child 首轮 0 citation、第二轮有效；断言 attempt=2、diagnosis 非空、最终只接纳第二轮。再测永久异常只变 insufficient、不取消有效 sibling。

### Task 2 — 新增简化 immutable v7 graph `[AC-1, AC-5, AC-6, AC-7]`

- 改动文件：`backend/deskpet/workflows/definitions/v7/deep_research.py`、`__init__.py`。
- 修改方式：
  - `normalize`：冻结 topic/mode/2–6 子方向上限与 child retry budget。
  - `decompose`：复用 `_PLAN_PROMPT`/`parse_sub_questions`；不足 2 项时使用两个通用但互补的 fallback 方向。
  - `research_children`：调用 Task 1 收集器，有界并行、监控和续跑；将 child records 与子报告写入 JSON-safe/blob-backed checkpoint。
  - scheduler 缺失时 fail-closed 为可理解的 `subagent_scheduler_unavailable`，不静默退回单 pipeline；测试阶段默认开关确保正常新 run 始终有 scheduler。
  - `synthesize`：只重建 valid 子报告，调用现有 `_fanout_synthesize()`；把耗尽项写入 Caveats；0 valid 时生成诚实不足报告。
  - `persist`/`finalize`：保存唯一 report 并发出唯一 report/artifact/final_assistant delivery intents；engine 仍由通用 native runner 管理。
  - graph 只暴露上述业务阶段；搜索、抓取、评分仍是子代理内部能力，不膨胀主图。
  - 子报告序列化沿用 v1 的 citation/report JSON 形状和 `deep_research_nodes.encode_large_value()`；不把 Python dataclass 直接塞进 checkpoint。
- 验证：compiled graph 节点/edge/manifest identity 固定；state JSON-safe；focused runner 完成后报告/Artifact/assistant 各 1 个。

### Task 3 — 注册 runtime 并默认切到 v7 `[AC-6, AC-7, AC-8]`

- 改动文件：`backend/deskpet/workflows/bootstrap.py`、`backend/main.py`、`backend/config.py`、两份 TOML。
- 修改方式：
  - bootstrap 同时注册 v1-v7；v7 context 复用 `_deep_v2_context_factory` 的 Search Gateway/Fetch/Blob/Artifact ports，并额外注入当前 `SubagentScheduler`。
  - 注册 v7 runtime adapter；v1-v6 adapter 不删。
  - resolver factory default、dataclass default、bundle config 和 dev config 同步为 v7；非法值回落 v7，显式 v5/v6 仅保留兼容 pin。把 `_merge_missing_feature_flags()` 的一次性迁移从“仅 v5/revision5 → v6/revision6”改成“仅准确继承上一 factory revision 的用户配置 → 当前 bundle revision”，显式 pin 保持不动。
  - capability snapshot 沿用通用 research budget；不注册 v6 专用 continuation/terminal extension。
- 验证：resolver、bootstrap versions、main wiring 与旧 run recovery tests。

### Task 4 — 安全进度与可观测 `[AC-3, AC-8]`

- 改动文件：`backend/deskpet/workflows/progress.py`、`research_tools.py`。
- 修改方式：v7 单卡阶段映射为“理解方向 / 拆分子方向 / 子代理调研与补救 / 综合报告 / 保存 / 交付”；日志记录 parent run、child id、attempt、status、diagnosis code、source count 和 duration，不记录页面正文或完整 prompt。
- 验证：progress payload schema、attempt 日志字段、敏感正文不进入 metrics。

### Task 5 — Spike、回归与事实源收尾 `[AC-1..AC-8]`

- 先运行 focused tests 与当前 DeepResearch 关键回归，锁定旧版本恢复不退化。
- 价值 smoke：从真实生产入口运行本轮唯一 required 场景 S-3；检查至少 3 个有效子方向、引用可追溯、出现统一分析和选型建议、只交付一份报告；重试耗尽时允许诚实 `partial`。
- retry spike：通过受控失败注入或真实低证据方向证明至少一个 child 经 diagnosis 续跑；若真实网络未自然失败，以同一 production v7 handler 的故障注入测试作为重试机制证据，不伪造 UI PASS。
- 本轮按用户“快速修改 + 一个调研结果 spike”的范围，只把 S-3 设为 required 正向场景；S-1、S-2、
  S-4 是后续回归候选，不计入本轮完成门。S-3 必须分别核对 engine/business 终态；若部分方向证据
  耗尽，允许以明确局限的 partial 交付，不能把 engine completed 冒充业务全量成功。
- child 在节点执行中只做读型检索/LLM；进程中断可能重放尚未 checkpoint 的 child attempt，但 final report/artifact/assistant 仍由 parent outbox exactly-once。该快速版本不承诺每个 child attempt 跨进程 exactly-once，测试必须明确验证最终交付唯一。
- 记录 run id、child attempt、终态、报告路径、引用数、耗时与人工质量结论。
- 更新 `testcase/2026-07-19-deepresearch-simplification/`、`testcase/index.md`、`ARCHITECTURE/DeepResearch.md`、`ARCHITECTURE/ARCHITECTURE.md`、`ARCHITECTURE/PROJECT_STATUS.md` 顶部日期与完成度。

## 方案取舍

- 不直接恢复 v1 为默认：v1 虽简单但没有子代理 fan-out/监控续跑。
- 不原地改 v6：会破坏 immutable checkpoint/manifest 和历史恢复。
- 不让每个 child 成为完整 durable child workflow：本轮重点是快速简化；Scheduler attempt + parent checkpoint 足够验证价值，未来若 spike 证明需要跨进程保留进行中的单个 child，再把 child attempt 提升为 durable child run。
- 不用 TaskGroup fail-fast：子方向应该故障隔离，主 Agent需要收集全部结果后逐项处理。

## Phase 2 关键假设 Spike

- 命令：在 `backend/.venv` 中直接调用 `NativeWorkflowExecutable._terminal_projection/_terminal_intents`，构造 `workflow_version=v7` 和 report/artifact/final_assistant 三个 delivery intents；同时查询 `WorkflowLauncher._legacy_deep_extension('v7')` 与 `public_stage_for(..., 'v7')`。
- 实际输出：`status='completed'`；生成 `workflow.report`、`workflow.artifact_card`、`workflow.final_assistant`、`workflow.final`；v7 legacy extension `new_runs_enabled=True`；`normalize/plan/search/synth/persist/finalize` 均有现成安全进度标签。
- 结论：v7 不必复制 v6 terminal projector/extension；只要 final state 使用通用 `delivery_intents`，native engine 即可 exactly-once 提交终态 outbox。v7 进度节点采用现有公共 node id 即可复用单卡安全映射。

## 阶段出口

- AC-1 至 AC-11 覆盖完整、代码级改动点明确。
- 下一阶段用 plan challenger 检查隐藏复杂度、恢复语义和测试缺口；不通过则修 plan 后再实施。

## 2026-07-20 增量实施：过程可见与文件交付 `[AC-9..AC-11]`

### 核心矛盾

v7 已经按 2–6 个稳定 `child_id` 执行调研，也已经把 Markdown 写入既有 `DeepResearch` 目录；但 UI 当前消费的是 scheduler 的“每次 attempt”事件，所以 4 个方向加一次重试被错误显示成 5 个子代理。同时，finalize 把文件描述放在 `payload.artifact`，通用交付适配器只识别 `payload.artifacts[]`，导致真实文件被降级为无路径的纯文本卡片。

### Task 6 — 持久化 v7 子方向业务进度 `[AC-9]`

- `backend/deskpet/workflows/progress.py::WorkflowProgressReporter` 新增 `report_deep_research_v7_children(identity, children)`：严格校验 v7/search 身份、2–6 条记录、稳定 `dr-N` id、允许状态与字段上限；canonical JSON 做 SHA-256，event key 为 `progress:v7:research_children:<snapshot_hash>`。同一快照 replay 命中同一 outbox event，不重复投递；变化快照各自成为有序 durable `workflow.progress` 事件。
- `backend/deskpet/workflows/definitions/v7/deep_research.py::plan_handler` 通过已有 `context.ports["progress"]` 在问题生成后发布全部方向的 `queued` 快照；`search_handler` 把同一 reporter callback 传给 collector。
- `backend/deskpet/tools/research_tools.py::collect_subagent_research` 增加可选 async snapshot callback：以 parent-owned map 维护每个方向最后状态，在 attempt 开始发布 `running`，第二轮开始前发布 `retrying`，验收后发布 `valid/insufficient`。collector 内用一个本次 fan-out 私有 `asyncio.Lock` 串行执行“更新 map → 复制完整 snapshot → `asyncio.wait_for(callback, 5s)`”，避免并发 child 的旧状态后到并覆盖新状态；callback 异常或超时只记 warning 并释放锁，不改变调研结果。跨 search replay 不使用本地 revision 判序：durable outbox 的 event `seq` 是唯一排序事实，完全相同 snapshot 命中旧 event 后会因旧 seq 被 reducer 忽略，不同 snapshot 获得新 seq 并覆盖旧 UI。父节点若在 checkpoint 前崩溃会按既有 at-least-once 语义重跑 child；最终 child records 仍只在父 search checkpoint 一次性提交，不做来源追加。每个方向总共最多 2 次尝试，即首轮加最多 1 次补救。
- `tauri-app/src/stores/sessionsStore.ts::applyWorkflowEvent` 解析 `kind=research_children/schema_version=7`，按整个 snapshot 替换 `workflow_v7_children`，不按 attempt append。方向快照使用独立 `workflow_v7_children_seq`：在全局 `workflow_terminal/workflow_seq` stale guard 之前先比较并合并 children snapshot，只更新 children/children_seq 字段，不回退主卡 stage/status/terminal。这样实时 outbox/WebSocket 即使乱序，synth/final 的更高全局 seq 先到，稍后到达的 child terminal snapshot 仍能收口；同类旧 children seq 才被拒绝。绝不使用单次 fan-out 的本地 revision。snapshot 缺失/非法则保留上一份，旧 v1-v6 消息不受影响。`ws.ts::session_messages_response` 与实时事件共用该 reducer。
- `tauri-app/src/components/workflow/WorkflowProgressGroup.tsx` 把 v7 纳入 DeepResearch 卡，并按 `workflow_v7_children` 渲染方向文本、中文状态、`attempt/max_attempts` 与来源数。`tauri-app/src/code-panel/SubagentProgressPanel.tsx` 过滤 `kind=research` 且 run id 匹配 `.dr-N.aN` 的内部 attempt；其它通用子代理继续显示。
- 测试：`backend/tests/test_workflow_progress.py` 断言 schema、安全字段、相同 snapshot 幂等；`test_deepresearch_simplified_v7.py`/`test_deepresearch_subagent_fanout.py` 断言 callback 状态序列，用两个受控并发 child 证明终态不回退，并用 never-return callback 证明 5 秒有界降级后研究仍完成；`sessionsStore.test.ts` 与 `WorkflowProgressGroup.test.tsx` 构造 4 方向、其中 1 个方向 attempt=2，断言仅 4 行；另覆盖 `child running seq10 → synth seq12 → child valid seq11` 仍更新 children，以及 `final seq20 → child terminal seq19` 仍更新 children 且主卡保持 terminal；同类旧 children seq 不得回退。`ws.chat.test.ts` 用真实历史事件序列断言刷新后完成态等价；Subagent 面板测试断言 v7 5 个内部 attempts 为 0 条通用行。

### Task 7 — 恢复真实文件 Artifact 与历史兼容 `[AC-10, AC-11]`

- `backend/deskpet/workflows/definitions/v7/deep_research.py::finalize_handler` 同时输出通用 `artifacts: [file_artifact]`，其中只包含 persist port 返回的 canonical absolute path/sha/size/mime/title；保留旧 `artifact` 字段供既有 checkpoint 恢复。
- `backend/deskpet/workflows/adapters/product_delivery.py::_artifact_list` 在 `artifacts[]` 之后兼容读取单个嵌套 `artifact` 并归一成一项列表，再走既有 `extract_artifacts_from_result` 与 `_materialize_file_evidence`。一个 terminal intent 只产一个 artifact；outbox event/receipt id 继续按 intent/event 唯一，retry/replay 不生成第二张卡。
- `tauri-app/src/code-panel/ws.ts::session_messages_response` 若 persisted assistant 行是旧 text Artifact，则读取同一行 `workflow_event.payload.payload.artifact`（同时兼容一层 payload），构造标准 `artifact_create` file envelope 后再进入现有历史 `tool_result` 恢复分支；若 path 缺失或 kind 不是 file，则保持原 text card。该转换只作用于展示，不重放 outbox、不复制报告。
- 继续复用已有 `FileArtifactCard` 的“打开 / 另存为 / 在文件夹中显示 / 复制路径”，不新增第二套系统命令。Tauri 官方 dialog API 的 save 返回目标路径或取消，opener API 的 `revealItemInDir` 交给系统文件管理器定位文件，和现有实现一致。
- 自动化验证：`test_deepresearch_simplified_v7.py` 断言 canonical path 指向 `DeepResearch/<topic>-<run_id>.md` 且 finalize 只有一个 file artifact；`test_workflow_product_delivery.py` 覆盖新 `artifacts[]`、旧 nested artifact 与重复 delivery 只落一条消息；`ws.chat.test.ts` 覆盖旧 text session row + 原始 nested artifact 事件重建 file 卡；`ArtifactCard.test.ts` 保持四个 invoke 契约，并保留/扩展 invoke reject 与空路径用例，断言 `role=alert` 显示可见错误。

### Task 8 — 重启与真人点击验收 `[AC-9..AC-11]`

- 从 source backend 启动 Tauri，确认日志中的 backend dir、workflow version 与进度快照。
- 使用 Computer Use 通过真实坐标输入并发起等价 DeepResearch：截图记录 2–6 个具体方向与状态推进；若真实网络自然触发 retry（本轮既有 S-3 已发生一次），额外截图同一方向 attempt 从 1 更新到 2。retry 的确定性门禁由同一 production collector 的受控自动化用例承担，明确不把该自动化冒充 UI 点击证据，也不为了制造 retry 向真实 UI/协议注入故障。完成后从运行日志/最终 checkpoint 核对 backend child records。然后完整退出 Tauri（同时确认其托管 backend/前端进程退出），用相同 `DESKPET_USER_DATA_DIR` 从 source 重新启动，重新打开同一历史 Session，截图确认最终方向状态与 file Artifact 仍在且和重启前一致。
- 真实坐标点击“打开”并截图系统默认应用、“在文件夹中显示”并截图 Explorer 选中项、“另存为”并在系统对话框保存测试副本、“复制路径”并截图卡片可见反馈。随后只用文件系统只读校验副本存在且 SHA-256 与原报告一致；不得用 WebSocket 注入、直接 invoke 或测试函数代替点击。
- 完成后同步 `ARCHITECTURE/DeepResearch.md`、`ARCHITECTURE/ARCHITECTURE.md`、`ARCHITECTURE/PROJECT_STATUS.md` 与 testcase 索引，清除其中的“AC-9～AC-11 修复前链路”当前态描述。
