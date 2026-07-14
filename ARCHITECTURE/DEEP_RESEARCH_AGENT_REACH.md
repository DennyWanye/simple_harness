# DeepResearch and Agent-Reach

## Ownership

DeskPet owns the research task: durable graph execution, fan-out, evidence
scoring, citations, report synthesis, progress, Trace and Session delivery.

Agent-Reach owns internet channel selection: platform URL matching, setup
diagnostics and selection of an installed upstream backend.

## Runtime flow

request/subquestions
  -> DeskPet native DeepResearch graph
  -> explicit platform URL detected
  -> thin AgentReachPort
  -> Agent-Reach channel + active backend
  -> normalized evidence
  -> DeskPet scoring/citations/report

Agent-Reach is not a workflow engine and does not own DeskPet checkpoints. The
adapter never copies the upstream channel registry. For a GitHub URL, the
requested channel can remain github while Agent-Reach's current active reader
is Web/Jina Reader.

## Failure behavior

Agent-Reach empty/off/degraded results never abort the research graph. Existing
generic search and Scrapling extraction remain available. Coverage and Trace
store only bounded doctor data plus channel/backend/status/reason, never headers,
cookies, tokens, exceptions or raw command output.

## Operational boundary

The dependency is pinned. Public channels are default-on. DeskPet does not
silently import cookies, log in to platforms, install system packages or update
Agent-Reach; those actions need explicit user intent.
