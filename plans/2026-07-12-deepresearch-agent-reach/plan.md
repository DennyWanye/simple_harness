# Plan: direct Agent-Reach integration for DeepResearch

## Goal

Use Agent-Reach directly for platform-aware public URL access while preserving
DeskPet's native workflow, checkpoints, fan-out, scoring and Session delivery.

## Acceptance mapping

| Work item | Acceptance |
|---|---|
| WI-1 Pinned dependency and thin port | AC-AR-1, AC-AR-2 |
| WI-2 DeepResearch routing and fallback | AC-AR-3, AC-AR-4 |
| WI-3 Trace, fan-out and packaging | AC-AR-5, AC-AR-6 |
| WI-4 Automated and real UI verification | AC-AR-7 |

## WI-1: Direct upstream boundary

- Pin Agent-Reach to commit e825f6740d24c6c315c3b0dc41907e6c87ff39a5
  in backend/pyproject.toml and regenerate uv.lock.
- Add agent_reach_port.py. Import Agent-Reach's own channel registry and
  config; do not copy its descriptors, URL matchers or backend-selection rules.
- Do not mutate process-global PATH. Agent-Reach probes only tools already
  visible to the DeskPet process; missing optional CLIs remain doctor warnings.
- Normalize output into bounded, JSON-safe evidence and redact diagnostic
  secrets. A platform lacking a read method may use Agent-Reach's Web/Jina
  backend while retaining the requested platform name.
- Register agent_reach_doctor and agent_reach_read for bounded diagnostics and
  focused URL reads, but keep strong DeepResearch requests on the workflow-only
  ingress so the outer ReAct loop cannot bypass checkpoints and progress.

## WI-2: DeepResearch routing

- Route explicit supported public URLs to the direct Agent-Reach source from
  both the original request and planner subquestions, up to four stable URLs.
- Insert accepted text into the existing passage scoring and citation pipeline.
- Empty results remain empty. Off/error/degraded results are observable but do
  not abort generic search, Scrapling extraction or report synthesis.
- Remove the superseded DeskPet-native GitHub REST channel and shared query
  budget so only one platform implementation remains.

## WI-3: Observability and distribution

- Store one route.agent_reach object containing planned channels, doctor
  snapshot, successful reads and real degradations.
- Merge fan-out observations in stable first-seen order and project the same
  JSON-safe object into successful DeepResearch node Trace attributes.
- Include Agent-Reach modules and package data in PyInstaller analysis.
- Pass ContextAssembler's tool-name whitelist through main.py into AgentLoop.
- Route strong DeepResearch triggers directly to the native workflow before
  AgentLoop. The workflow alone owns Agent-Reach reads, progress and delivery.
- Do not silently install system binaries, import browser cookies or configure
  authenticated social channels.

## WI-4: Verification

- Unit-test doctor filtering, read fallback, bounded tool JSON and invalid URLs.
- Test DeepResearch success, degradation fallback, stale-error cleanup,
  fan-out merge, durable Trace parity and existing research regressions.
- Run a real Agent-Reach read against its public GitHub repository.
- Restart latest DeskPet source and submit the repository-focused DeepResearch
  prompt through Computer Use. Verify one updating progress card and a complete
  cited report in the same Session, then retain screenshots and backend logs.
- Update architecture, testcase indexes, results and STATUS after all gates pass.

## Out of scope

- Replacing DeskPet's native workflow engine with Agent-Reach.
- Automatic login/cookie extraction or unattended system package installation.
- Maintaining a second DeskPet channel registry.
- Claiming authenticated GitHub/social capabilities when doctor reports them off.
