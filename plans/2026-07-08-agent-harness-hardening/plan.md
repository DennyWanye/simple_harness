# Plan: Agent Harness Hardening

## 关联验收标准

覆盖 `acceptance.md` 的 AC-1 到 AC-8。

## 深度调研结论

- LangGraph 官方定位是 long-running stateful agents 的低层 orchestration runtime，核心价值是 durable execution、human-in-the-loop、persistence 和 state/transition 可观察性。DeskPet 不应整体迁移，但应借鉴“显式 lifecycle + checkpoint 边界”。
- CrewAI 官方把 Flows 定位为 event-driven workflow state/control，把 Crews 定位为多代理协作单元。DeskPet 不应把当前 sidecar subagent 伪装成 CrewAI-style runtime，但应借鉴 role/task/process 语义用于未来 team DSL。
- 当前代码证实 8 个缺点都成立，子代理复核还补充第 9 个相邻风险：AgentLoop events、WS payload、SessionDB rows、receipts、metrics、frontend cards 的 schema hand-map 漂移。

## 文件影响清单

| 文件 | 职责 | 本次改动 |
|------|------|----------|
| `acceptance.md` | plan-test 唯一真相来源 | 替换为本次 harness hardening 验收标准 |
| `backend/deskpet/agent/harness_manifest.py` | 机器可读 harness 合约 | 新增 lifecycle/services/defect remediation manifest |
| `backend/tests/test_agent_harness_manifest.py` | manifest 合约测试 | 新增 lifecycle、owner path、service whitelist、AC 覆盖测试 |
| `backend/tests/test_context.py` | ServiceContext 单元测试 | 增加 harness service whitelist/None placeholder 护栏 |
| `backend/tests/test_context_assembler_policy_manifest.py` | policy 漂移测试 | 增加 task policy/component/tool fan-out 护栏 |
| `docs/agent-harness-lifecycle.md` | 开发者生命周期手册 | 新增 service、factory、assembler、loop、subagent、trace、test routing 说明 |
| `backend/tests/test_agent_harness_docs.py` | 文档存在性护栏 | 确保生命周期文档覆盖关键契约 |
| `ARCHITECTURE/AGENT_HARNESS.md` | 架构基线 | 新增当前 harness 架构、弱点、框架对比和测试路由 |
| `ARCHITECTURE/index.md` | 架构索引 | 登记 agent harness 架构基线 |
| `testcase/2026-07-08-agent-harness-hardening/manual-test.md` | 长期测试用例 | 新增脚本测试/手工测试路由说明 |
| `STATUS/status.md` | 全局状态 | 完成测试后追加里程碑 |

## 任务清单

### Task 1 — 建立验收与架构基线 [AC-1, AC-2, AC-8]
- 改动文件：`acceptance.md`, `ARCHITECTURE/AGENT_HARNESS.md`, `ARCHITECTURE/index.md`
- 现状：`STATUS/AgentLoop.md` 只覆盖 AgentLoop 局部，`ARCHITECTURE/ARCHITECTURE.md` 主要是 DeepResearch scoped 基线。
- 修改方式：新增 harness 专项架构文档，明确 lifecycle、状态/恢复、弱点和 LangGraph/CrewAI 对比。
- 验证：文档测试 + manifest owner path 测试。

### Task 2 — 新增机器可读 harness manifest [AC-1, AC-2, AC-4, AC-5, AC-6, AC-8]
- 改动文件：`backend/deskpet/agent/harness_manifest.py`
- 现状：stage 名称、服务职责、缺点修复映射散在文档和注释中。
- 修改方式：新增纯数据 dataclass manifest，不参与运行时聊天路径。
- 验证：`test_agent_harness_manifest.py`。

### Task 3 — 接线与策略漂移护栏 [AC-3, AC-4, AC-6]
- 改动文件：`backend/tests/test_context.py`, `backend/tests/test_context_assembler_policy_manifest.py`
- 现状：service_context 漏白名单、policy 漏 component fan-out 是历史高频故障。
- 修改方式：参数化测试 manifest 服务名；测试所有有 tools 的 policy 都运行 `tool` 组件；测试默认 assembler 注册所有 policy component。
- 验证：pytest focused bundle。

### Task 4 — 子代理和测试成本边界文档 [AC-5, AC-7]
- 改动文件：`docs/agent-harness-lifecycle.md`, `backend/tests/test_agent_harness_docs.py`, `testcase/2026-07-08-agent-harness-hardening/manual-test.md`
- 现状：subagent sidecar 语义和 test routing 散在 STATUS/plan。
- 修改方式：文档化 scheduler/registry/completion_queue ownership；明确 pytest vs windows-mcp 路由。
- 验证：docs test + testcase index。

### Task 5 — 收尾同步 [AC-1..AC-8]
- 改动文件：`STATUS/status.md`
- 现状：STATUS 未记录本次 harness hardening。
- 修改方式：测试通过后更新模块完成度和里程碑。
- 验证：status 更新纪律满足。

## 挑战反馈吸收

- Explorer Fermat 确认 8 个问题均成立，并补充 “event schema contract drift” 相邻风险。本轮不重写 WS event schema，但在 manifest/lifecycle doc 中固定 trace stage vocabulary，后续可扩成 `TraceEvent` schema。
- Explorer Russell 建议的 guardrails 已吸收：ServiceContext whitelist、ContextAssembler policy manifest、docs presence test。AgentLoop/build_agent 更深 wiring manifest 暂作为下一切片，避免本轮触碰 `main.py` 大型导入副作用。

## 测试计划

```powershell
cd F:\projects\deskpet\backend
python -m pytest tests/test_agent_harness_manifest.py tests/test_context.py tests/test_context_assembler_policy_manifest.py tests/test_agent_harness_docs.py -q
python -m pytest tests/test_problem_pipeline.py tests/test_deskpet_context_assembler.py tests/test_subagent_nonblocking.py -q
```

本次不改 UI/desktop 行为，不触发 windows-mcp；若后续改 WS payload、ArtifactCard、权限弹窗或 Tauri interaction，必须走真机 E2E。
## 2026-07-09 Round 2 Runtime And Wiring Tests

User feedback: the first round was too static and did not prove enough real harness behavior. This round adds two runtime-facing guardrails:

- `backend/tests/test_agent_harness_runtime_contract.py`: runs `AgentLoop.run()` golden paths for tool_use -> v2 `execute_tool()` -> tool-message feedback -> final, EvidenceGate block -> system nudge -> investigative tool -> release, and subagent completion queue drain into the parent context exactly once.
- `backend/tests/test_agent_harness_build_agent_contract.py`: verifies `main.build_agent()` wires problem-pipeline knobs, `subagent_registry`, and the agent iteration tracer into the actual `AgentLoop` instance.

Verification:

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_runtime_contract.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_build_agent_contract.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_manifest.py tests/test_context.py tests/test_context_assembler_policy_manifest.py tests/test_agent_harness_docs.py tests/test_agent_harness_runtime_contract.py tests/test_agent_harness_build_agent_contract.py tests/test_problem_pipeline.py tests/test_deskpet_context_assembler.py tests/test_subagent_nonblocking.py -q
```

Result: `100 passed in 4.92s`.

## 2026-07-09 Round 3 Contract Tightening

User feedback: "难道不能更好点吗？" The third pass tightens contract tests around seams that still allowed silent drift:

- Extended `backend/tests/test_agent_harness_runtime_contract.py` with the legacy registry path: registries without `execute_tool()` must still use `dispatch(name,args,task_id)`, JSON-serialize dict/list results, and feed the tool message back before final.
- Added `backend/tests/test_agent_harness_contract_parity.py` to keep `main.build_agent()` and `AgentLoop.__init__` key harness kwargs aligned, keep loop-only harness kwargs constructor-visible, pin `PipelineEvent` WS payload shape, and cross-check manifest observability against runtime event names.
- Fixed a real drift found by the new parity test: `completion_gates.observability` in `harness_manifest.py` did not list `chat_v2_evidence_gate`, `chat_v2_selfcheck`, or `chat_v2_convergence`.

Verification:

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_runtime_contract.py tests/test_agent_harness_contract_parity.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_manifest.py tests/test_context.py tests/test_context_assembler_policy_manifest.py tests/test_agent_harness_docs.py tests/test_agent_harness_runtime_contract.py tests/test_agent_harness_build_agent_contract.py tests/test_agent_harness_contract_parity.py tests/test_problem_pipeline.py tests/test_deskpet_context_assembler.py tests/test_subagent_nonblocking.py -q
```

Result: `105 passed in 4.86s`.

## 2026-07-09 Round 4 Production Call-Site Contract

The previous rounds proved the factory and loop can be wired correctly. This pass pins the production chat call site that actually constructs the per-turn agent:

- Added `backend/tests/test_agent_harness_main_callsite_contract.py`.
- AST-checks the real `_agent = build_agent(...)` call in `main.py`.
- Ensures the call passes goal store/checker, context compressor, skill loader/matcher, tool path recorder, memory curator, evidence gate, pipeline problem type, investigation flag, observability flag and convergence flag.
- Ensures the chat path fetches these resources from `service_context`, guards `pipeline_evidence_gate` behind pre-loop short-circuit state, derives pipeline flags from `_pre` / config, and calls `_agent.run(...)` with runtime session context.

Verification:

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_main_callsite_contract.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_manifest.py tests/test_context.py tests/test_context_assembler_policy_manifest.py tests/test_agent_harness_docs.py tests/test_agent_harness_runtime_contract.py tests/test_agent_harness_build_agent_contract.py tests/test_agent_harness_contract_parity.py tests/test_agent_harness_main_callsite_contract.py tests/test_problem_pipeline.py tests/test_deskpet_context_assembler.py tests/test_subagent_nonblocking.py -q
```

Result: `110 passed in 5.41s`.

## 2026-07-09 Round 5 One-Shot Gate

The final hard gate now combines static contracts, runtime AgentLoop contracts, production call-site AST checks, and a dynamic websocket execution path:

- `tests/test_agent_harness_main_callsite_contract.py` now sends real `chat` and `chat_v2` messages through `/ws/control` using `TestClient`.
- The test keeps the path hermetic by replacing only `build_agent`, broadcast/context-usage side effects, and the problem pipeline.
- The dynamic assertions verify sentinel `service_context` resources reach `build_agent(...)`, pre-loop system injections reach the fake agent's message stack, `_agent.run(...)` receives runtime context, and the websocket emits `chat_v2_final`.

Verification:

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_main_callsite_contract.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_manifest.py tests/test_context.py tests/test_context_assembler_policy_manifest.py tests/test_agent_harness_docs.py tests/test_agent_harness_runtime_contract.py tests/test_agent_harness_build_agent_contract.py tests/test_agent_harness_contract_parity.py tests/test_agent_harness_main_callsite_contract.py tests/test_problem_pipeline.py tests/test_deskpet_context_assembler.py tests/test_subagent_nonblocking.py -q
```

Result: `112 passed in 6.96s`.
