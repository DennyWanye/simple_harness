# Baseline And Verification

## 2026-07-09 Round 2 Verification

## 2026-07-09 Round 3 Contract Verification

## 2026-07-09 Round 4 Production Call-Site Verification

## 2026-07-09 Round 5 One-Shot Harness Gate

Extended `tests/test_agent_harness_main_callsite_contract.py` beyond AST inspection with a dynamic `/ws/control` test:

- Starts the real FastAPI control websocket with `TestClient`.
- Sends both `chat` and `chat_v2` messages through the production handler.
- Replaces only `build_agent`, broadcasting/context-usage side effects, and the problem pipeline with hermetic fakes.
- Verifies the real chat path dynamically passes the sentinel service_context resources into `build_agent(...)`.
- Verifies the fake agent receives runtime context in `_agent.run(...)` and a system injection from pre-loop.
- Verifies the websocket emits `chat_v2_final`.

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_main_callsite_contract.py -q
```

Result: `7 passed in 5.60s`.

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_manifest.py tests/test_context.py tests/test_context_assembler_policy_manifest.py tests/test_agent_harness_docs.py tests/test_agent_harness_runtime_contract.py tests/test_agent_harness_build_agent_contract.py tests/test_agent_harness_contract_parity.py tests/test_agent_harness_main_callsite_contract.py tests/test_problem_pipeline.py tests/test_deskpet_context_assembler.py tests/test_subagent_nonblocking.py -q
```

Result: `112 passed in 6.96s`.

Added `tests/test_agent_harness_main_callsite_contract.py` to inspect `main.py` with AST and pin the production chat path, not just the factory:

- The `_agent = build_agent(...)` call must pass goal, compaction, skill, tool-path, curator, evidence-gate and pipeline kwargs.
- The chat path must fetch the corresponding services from `service_context`.
- `pipeline_evidence_gate` must be guarded by pre-loop short-circuit state.
- pipeline flags must derive from `_pre` and `config.features.problem_pipeline`.
- `_agent.run(...)` must receive session/runtime context (`session_id`, `stream`, `provider_chain`, `loop_user_request`, `is_sentinel_run`).

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_main_callsite_contract.py -q
```

Result: `5 passed in 0.41s`.

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_manifest.py tests/test_context.py tests/test_context_assembler_policy_manifest.py tests/test_agent_harness_docs.py tests/test_agent_harness_runtime_contract.py tests/test_agent_harness_build_agent_contract.py tests/test_agent_harness_contract_parity.py tests/test_agent_harness_main_callsite_contract.py tests/test_problem_pipeline.py tests/test_deskpet_context_assembler.py tests/test_subagent_nonblocking.py -q
```

Result: `110 passed in 5.41s`.

Added:

- `tests/test_agent_harness_contract_parity.py`: checks `main.build_agent()` and `AgentLoop.__init__` share the explicit harness kwargs, loop-only harness kwargs remain constructor-visible, PipelineEvent WS payload shape stays stable, and manifest observability names include runtime event types.
- Extended `tests/test_agent_harness_runtime_contract.py`: covers legacy `dispatch()` registry fallback and PipelineEvent minimum fields (`task_id`, positive integer `iteration`, `blocked/reason/nudge_count` payload).

This round caught a real manifest drift: `completion_gates.observability` did not include `chat_v2_evidence_gate` / `chat_v2_selfcheck` / `chat_v2_convergence`. Fixed in `backend/deskpet/agent/harness_manifest.py`.

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_runtime_contract.py tests/test_agent_harness_contract_parity.py -q
```

Result: `8 passed in 3.70s`.

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_manifest.py tests/test_context.py tests/test_context_assembler_policy_manifest.py tests/test_agent_harness_docs.py tests/test_agent_harness_runtime_contract.py tests/test_agent_harness_build_agent_contract.py tests/test_agent_harness_contract_parity.py tests/test_problem_pipeline.py tests/test_deskpet_context_assembler.py tests/test_subagent_nonblocking.py -q
```

Result: `105 passed in 4.86s`.

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_runtime_contract.py -q
```

Result: `3 passed in 0.23s`.

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_build_agent_contract.py -q
```

Result: `4 passed in 24.29s`.

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_manifest.py tests/test_context.py tests/test_context_assembler_policy_manifest.py tests/test_agent_harness_docs.py tests/test_agent_harness_runtime_contract.py tests/test_agent_harness_build_agent_contract.py tests/test_problem_pipeline.py tests/test_deskpet_context_assembler.py tests/test_subagent_nonblocking.py -q
```

Result: `100 passed in 4.92s`.

## Environment

- Workspace: `F:\projects\deskpet`
- Python: `backend/.venv/Scripts/python.exe`
- Note: `python`, `uv`, and `git` were not available on PATH in this shell, so verification used the checked-in backend virtualenv directly.

## Commands

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest tests/test_agent_harness_manifest.py tests/test_context.py tests/test_context_assembler_policy_manifest.py tests/test_agent_harness_docs.py -q
```

Result: `36 passed in 0.35s`.

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest tests/test_problem_pipeline.py tests/test_deskpet_context_assembler.py tests/test_subagent_nonblocking.py -q
```

Result: `57 passed in 1.65s`.

## Routed Test Decision

This change adds docs, manifest data, pytest guardrails, and a policy-loader bug fix. It does not alter UI payloads, frontend rendering, permission popups, ArtifactCard behavior, Tauri shell operations, or real LLM routes. Per `testcase/2026-07-08-agent-harness-hardening/manual-test.md`, windows-mcp is not required for this slice.
