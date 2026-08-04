# Baseline - 2026-07-11

Code baseline before implementation.

## Backend focused suite

Command:

`backend/.venv/Scripts/python.exe -m pytest tests/test_workflow_launcher.py tests/test_workflow_ppt_pro_graph.py tests/test_workflow_ppt_production_wiring.py tests/test_ppt_outline_wiring.py -q`

Result: `32 passed in 11.21s`.

## Frontend focused suite

Command: direct bundled Node invocation of Vitest for:

- `src/code-panel/__tests__/PPTOutlineCard.test.tsx`
- `src/code-panel/ws.chat.test.ts`

Result: `2 files, 11 tests passed`.

## TypeScript

Command: direct bundled Node invocation of `typescript/bin/tsc --noEmit`.

Result: passed with no diagnostics.

## Environment note

The bundled `pnpm` wrapper attempted a dependency status install and stopped on the existing ignored `esbuild@0.21.5` build-script policy. No dependency change was made. Direct checked-in `node_modules` binaries provide the non-mutating test/typecheck baseline above.

