# DeepResearch direct Agent-Reach results

## Decision

PASS.

## Automated evidence

- Focused direct-integration suite: 74 passed, 0 failed.
- Final broader backend routing/research/workflow regression: 310 passed,
  0 failed. The second run supersedes the focused run as the final gate.
- Final uv lock --check passed; uv sync --frozen --dry-run resolved 411
  packages after the frozen rebuild.
- Real upstream read: github channel, Jina Reader backend, 15,376 characters,
  process PATH unchanged.

## Frozen evidence

- PyInstaller clean rebuild of the final source completed successfully on
  2026-07-12 in 162.9 seconds.
- PYZ-00.toc contains 34 Agent-Reach entries, including channels, config and
  backends.
- Frozen backend startup smoke passed from F:/deskpet-build/dist with a clean
  temporary user-data directory.

## Computer Use evidence

- Session: f45f61f1-5ee2-43a5-b9c7-ee1273bafdf4
- Workflow run: 4221571b81314ac9b0210d4214de4bbb
- Trace: 3edf400b85e1495da29c0d9e7313b70a
- The UI showed one DeepResearch card updating at 3/7 (43%) and completing at
  7/7 (100%). The full Markdown report and clickable repository citation
  appeared immediately below it.
- Closing and reopening the message panel restored the same completed card and
  report without duplication.
- Screenshots: `01-progress-3-of-7.png` and
  `02-complete-7-of-7-report.jpg`.

## Trace evidence

- workflow status: completed
- planned URL: https://github.com/Panniantong/Agent-Reach
- planned channel: github
- hit backend: Jina Reader
- degraded: empty
- direct through persist node spans: ok
- durable query extract: `trace-evidence.json`

## Findings fixed during real UI testing

1. Outer AgentLoop exposed Agent-Reach/web tools and bypassed the workflow.
2. ContextAssembler computed a whitelist, but main.py did not pass it to
   AgentLoop.
3. Even with one tool visible, the model could decline to call it.

Strong DeepResearch requests now start the native workflow deterministically
before AgentLoop, so progress, checkpoints, Trace and Session delivery no longer
depend on model tool-choice compliance.

Independent plan challenge verdict: PASS, with no remaining blocker.
