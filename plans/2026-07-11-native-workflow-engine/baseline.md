# Green Baseline — 2026-07-11

## Backend

- Command: `backend/.venv/Scripts/python.exe -m pytest -q backend/tests -k "workflow or ppt"`
- Result: `450 passed, 3641 deselected in 136.47s`

## Frontend

- TypeScript: `node node_modules/typescript/lib/tsc.js --noEmit` — PASS.
- Focused Vitest: sessions store + workflow progress component + WS adapter — `34 passed`.

## Dependency Lock

- `uv lock --check --project backend` — PASS, 414 locked package records / 412 unique names.
- LangGraph family: `langgraph`, `langgraph-checkpoint`, `langgraph-checkpoint-sqlite`, `langgraph-prebuilt`, `langgraph-sdk`.
- Related transitive records present: `langchain-core`, `langchain-protocol`, `ormsgpack`, `xxhash`.

This snapshot is the pre-change comparator. The worktree contains substantial unrelated/user changes; implementation must not revert them.
