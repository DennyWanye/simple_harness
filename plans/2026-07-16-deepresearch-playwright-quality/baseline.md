# Baseline — DeepResearch v5 / Playwright quality plan

> Captured: 2026-07-16 (Asia/Shanghai)
> Plan status: `READY_WIN11_ONLY` after the user-approved 2026-07-16 scope revision
> Product code changed by this run: **none**

## 1. Repository snapshot

- Branch: `master` (`origin/master` ahead by 29 commits at capture time).
- HEAD: `0117ad764f593d018392332541d475a1d52a07ac`.
- Existing worktree before this plan-task run: 57 modified files, 159 untracked paths, no staged changes.
- Existing tracked diff: 4,800 insertions / 427 deletions across 57 files, including in-progress DeepResearch v4, Search Gateway, workflow UI, architecture, and unrelated local work.
- This run intentionally did not stage, reset, rewrite, or absorb those existing changes.

## 2. Historical Task 0 Gate A preflight (superseded by scope revision)

| Check | Observed result | Gate result |
|---|---|---|
| Host OS | Windows 11 Pro x64, version `10.0.22621`, build `22621` | Host side identified |
| Hyper-V feature | `Microsoft-Hyper-V-All = Disabled` | Not required by revised scope |
| Hyper-V PowerShell module | `Get-VM` / `Get-VMSwitch` unavailable | Not required by revised scope |
| Win10 VM/checkpoint | Unavailable | Deferred; not part of this plan's DoD |
| Authorized Win10 ISO | `DESKPET_WIN10_22H2_ISO` is empty | Not required by revised scope |

This preflight originally stopped the run because the first finalized scope required Windows 10 and Windows 11. The user subsequently revised the plan to Windows 11 x64 only. The observations above remain historical evidence, but they no longer block Tasks 0–15. Hyper-V, VM provisioning, ISO handling, and Windows 10 certification are deferred to a later independent plan.

### Current Playwright development state

- Python environment: `backend/.venv`, Python 3.11.9.
- Playwright package: `1.61.0`.
- Bundled metadata candidate: Chromium / chromium-headless-shell revision `1228`, browser version `149.0.7827.55`.
- Default browser cache `%LOCALAPPDATA%/ms-playwright`: absent.
- This is only a development input. No browser was downloaded and no version was accepted for product use during the original preflight. The revised Windows 11-only Task 0 may now perform the frozen spike.

## 3. Green/red baseline

### Backend focused regression — PASS

Command scope: current DeepResearch, Search Gateway, DeepResearch workflow v1–v4 compatibility, delivery, progress, and service tests listed explicitly from `backend/tests/`.

- Result: **311 passed in 30.91s**.
- Valid invocation was run from repository root so `scripts.acceptance` is importable.
- An earlier invocation from `backend/` was discarded as an invalid sampling command because three tests could not import repository-root `scripts.acceptance` modules.

### Frontend focused regression — PASS

The installed local CLIs were invoked directly because the pnpm wrapper performs a dependency-policy preflight before every script.

- Vitest: **3 files passed, 52 tests passed**.
  - `src/stores/sessionsStore.test.ts`
  - `src/components/workflow/WorkflowProgressGroup.test.tsx`
  - `src/components/MessageStreamPanel.workflow.test.tsx`
- TypeScript: `tsc -b --pretty false` — **PASS**.
- Vite production build: **PASS**, 1,395 modules transformed.
- Existing build warnings:
  - `edgeWatcher.ts` is both statically and dynamically imported.
  - `SubagentProgressPanel` output chunk exceeds 500 kB.

### Rust — PASS with existing warning

- `cargo check --manifest-path src-tauri/Cargo.toml` — **PASS**.
- Existing warning: `src/paths.rs::resolve_with` is unused.

### Existing red baselines — not introduced by this run

1. `pnpm test`, `pnpm exec tsc`, `pnpm run lint`, and `pnpm run build` are rejected by the local pnpm supply-chain preflight before the requested script starts:
   - `ERR_PNPM_IGNORED_BUILDS: Ignored build scripts: esbuild@0.21.5`.
   - Direct installed CLIs were used above to establish source/test/build state without changing approval policy.
2. Repository-wide `eslint .` is red and traverses frozen artifacts under `src-tauri/target/debug/backend/_internal/...`:
   - observed summary: 1,957 problems (1,940 errors, 17 warnings).
3. Focused ESLint on the plan-related frontend files is also red with four existing errors, all in `src/message-panel/MessagePanelRoot.tsx`:
   - line 239: synchronous `setState` in an effect;
   - line 244: explicit `any`;
   - line 368: synchronous `setState` in an effect;
   - line 383: explicit `any`.

## 4. Resume conditions after scope revision

No external machine or reboot prerequisite remains. Resume `/plan-task` from Task 0 using the current Windows 11 x64 host, isolated install/user-data paths, a cleared development browser cache, and offline execution evidence. Windows 10 support must remain explicitly unclaimed until a later compatibility plan passes.
