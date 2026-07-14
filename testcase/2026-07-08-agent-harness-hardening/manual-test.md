# Agent Harness Hardening Testcase

## 2026-07-09 Round 2 Runtime Cases

### TC-A5 AgentLoop runtime contract
1. Run `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_runtime_contract.py -q`
2. Expected: tool-use loop feeds v2 tool envelopes back as `role=tool`; EvidenceGate blocks an unsupported first claim, injects a system nudge, then releases after investigative tool evidence; subagent completion is drained exactly once into the parent context.

### TC-A6 build_agent wiring contract
1. Run `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_build_agent_contract.py -q`
2. Expected: `main.build_agent()` passes problem-pipeline knobs, `subagent_registry`, and iteration tracer config into the actual `AgentLoop` instance.

### TC-A7 focused harness regression bundle
1. Run `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_manifest.py tests/test_context.py tests/test_context_assembler_policy_manifest.py tests/test_agent_harness_docs.py tests/test_agent_harness_runtime_contract.py tests/test_agent_harness_build_agent_contract.py tests/test_problem_pipeline.py tests/test_deskpet_context_assembler.py tests/test_subagent_nonblocking.py -q`
2. Expected: `100 passed`.

## 2026-07-09 Round 3 Contract Cases

### TC-A8 legacy registry fallback
1. Run `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_runtime_contract.py::test_runtime_legacy_dispatch_result_is_json_fed_back_before_final -q`
2. Expected: a registry without `execute_tool()` uses legacy `dispatch(name,args,task_id)`, JSON-serializes dict/list results, and feeds that tool message back before final.

### TC-A9 factory/loop parity and event schema
1. Run `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_contract_parity.py -q`
2. Expected: `build_agent()` and `AgentLoop.__init__` share explicit harness kwargs; loop-only harness kwargs stay visible; `PipelineEvent` WS payload shape remains stable; manifest observability names include runtime event types.

### TC-A10 expanded focused harness regression bundle
1. Run `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_manifest.py tests/test_context.py tests/test_context_assembler_policy_manifest.py tests/test_agent_harness_docs.py tests/test_agent_harness_runtime_contract.py tests/test_agent_harness_build_agent_contract.py tests/test_agent_harness_contract_parity.py tests/test_problem_pipeline.py tests/test_deskpet_context_assembler.py tests/test_subagent_nonblocking.py -q`
2. Expected: `105 passed`.

## 2026-07-09 Round 4 Production Call-Site Cases

### TC-A11 main.py production call-site wiring
1. Run `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_main_callsite_contract.py -q`
2. Expected: the real `_agent = build_agent(...)` chat-path call passes goal, compaction, skill, tool-path, curator, evidence-gate and pipeline kwargs; fetches their services from `service_context`; guards `pipeline_evidence_gate` by pre-loop short-circuit; derives pipeline flags from `_pre` and config; and calls `_agent.run(...)` with session/runtime context.

### TC-A12 production call-site regression bundle
1. Run `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_manifest.py tests/test_context.py tests/test_context_assembler_policy_manifest.py tests/test_agent_harness_docs.py tests/test_agent_harness_runtime_contract.py tests/test_agent_harness_build_agent_contract.py tests/test_agent_harness_contract_parity.py tests/test_agent_harness_main_callsite_contract.py tests/test_problem_pipeline.py tests/test_deskpet_context_assembler.py tests/test_subagent_nonblocking.py -q`
2. Expected: `110 passed`.

## 2026-07-09 Round 5 One-Shot Gate

### TC-A13 one-shot harness gate
1. Run `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_manifest.py tests/test_context.py tests/test_context_assembler_policy_manifest.py tests/test_agent_harness_docs.py tests/test_agent_harness_runtime_contract.py tests/test_agent_harness_build_agent_contract.py tests/test_agent_harness_contract_parity.py tests/test_agent_harness_main_callsite_contract.py tests/test_problem_pipeline.py tests/test_deskpet_context_assembler.py tests/test_subagent_nonblocking.py -q`
2. Expected: `112 passed`; includes static manifest/docs/policy guards, `AgentLoop.run()` runtime contracts, build_agent parity/wiring, main.py production call-site AST checks, and dynamic `/ws/control` execution for both `chat` and `chat_v2`.

## 范围

本组用例覆盖 agent harness 架构契约、service_context 接线、ContextAssembler policy、生命周期文档和测试策略路由。

## 自动化用例

### TC-A1 lifecycle manifest
1. 运行 `cd backend && python -m pytest tests/test_agent_harness_manifest.py -q`
2. 预期：生命周期阶段顺序固定、owner 文件存在、状态/恢复边界明确、8 个缺点覆盖 AC-1..AC-8。

### TC-A2 service_context whitelist
1. 运行 `cd backend && python -m pytest tests/test_context.py -q`
2. 预期：manifest 声明的 harness services 都可 `register/get`，关键服务可注册 `None` 占位。

### TC-A3 ContextAssembler policy drift
1. 运行 `cd backend && python -m pytest tests/test_context_assembler_policy_manifest.py tests/test_deskpet_context_assembler.py -q`
2. 预期：有工具暴露的 policy 都 fan-out `tool` component，默认 assembler 注册全部 policy components。

### TC-A4 lifecycle docs
1. 运行 `cd backend && python -m pytest tests/test_agent_harness_docs.py -q`
2. 预期：开发者 lifecycle 文档存在并覆盖 `build_agent`、`AgentLoop`、`ContextAssembler`、`ServiceContext`、`completion_queue`、trace、test routing。

## 手工测试路由

本次变更是文档和后端 pytest 护栏，不改变用户可见 UI、WebSocket payload、权限弹窗、ArtifactCard 或 Tauri 行为，因此不需要 windows-mcp。

后续若修改以下内容，必须新增真机用例：
- 前端卡片、权限弹窗、消息流、复制/打开文件等 UI。
- `main.py` 的 WS payload mapping。
- `ToolRegistry.execute_tool` 的用户可见 artifact/receipt/permission 行为。
- Tauri shell/file operation bridge。

## 幂等性

- 所有 pytest 可重复运行。
- 文档和 manifest 为静态文件，无运行时副作用。
- 不创建用户数据、不启动 Tauri、不调用真实 LLM。
