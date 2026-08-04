# Green Baseline — 2026-07-11

## Backend

- Command: `.venv/Scripts/python.exe -m pytest tests/test_ppt_full_page_workflow.py tests/test_workflow_ppt_pro_graph.py tests/test_workflow_progress.py tests/test_workflow_service.py tests/test_workflow_runner.py -q`
- Result: `56 passed in 20.04s`.

## Frontend

- Command: bundled Node `typescript/bin/tsc -b --pretty false`.
- Result: PASS.
- Command: Vitest `ws.chat.test.ts sessionsStore.test.ts MessageStreamMarkdown.test.tsx`.
- Result: `3 files, 29 tests passed`.
- Existing non-fatal stderr: jsdom has no Tauri `invoke`, so the eager WS probe logs `get_shared_secret failed`; tests still pass and this is baseline behavior.

## Baseline Decision

- Relevant backend and frontend baseline is green.
- Final regression must be no worse than these results and must add AC-22/23 coverage.
