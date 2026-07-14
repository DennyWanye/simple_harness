# DeepResearch Agent-Reach baseline

## Existing DeskPet flow

DeskPet already owns the durable research workflow: planning, fan-out, generic
search, Scrapling-first extraction, scoring, synthesis, citations, checkpoints,
progress and in-session delivery. That orchestration remains unchanged.

## Upstream boundary

Agent-Reach owns platform discovery, channel health and backend selection. Its
official design is a selector/installer/health-check/router rather than a proxy
service. DeskPet should call its Python channel API directly and consume the
actual upstream backend result.

## Current gap

- DeepResearch cannot route an explicit GitHub/YouTube/RSS URL through one
  platform-aware entry point.
- Channel health and the selected backend are not visible in workflow Trace.
- Generic search remains useful, but it should be the fallback rather than a
  second implementation of every platform.

## Target boundary

- Pin the audited Agent-Reach commit as a runtime dependency.
- Add only a thin DeskPet port and two tool registrations: doctor and URL read.
- Feed accepted Agent-Reach evidence into the existing research core.
- Record requested channel, active backend, health and degradation in Trace.
- Keep cookie/login setup, system-package installation and Agent-Reach updates
  explicit; DeskPet must not perform them silently.
