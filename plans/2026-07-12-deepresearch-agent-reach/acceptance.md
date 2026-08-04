# Feature acceptance: direct Agent-Reach integration

| ID | Given / When | Expected evidence |
|---|---|---|
| AC-AR-1 | Inspect dependency and imports. | Agent-Reach is pinned to one audited GitHub commit; DeskPet imports its channel registry directly and contains no copied channel registry or GitHub REST implementation. |
| AC-AR-2 | Run doctor/read with healthy, unavailable and broken channels. | At most 24 channels and 8 backends per channel are returned; text fields are bounded, one channel cannot break the others, diagnostics contain no credentials, and process PATH is never mutated. |
| AC-AR-3 | Research one to four explicit supported public platform URLs. | Private/local/credential-bearing URLs are rejected; Agent-Reach selects each requested channel in stable order, returns citation-ready text through its active backend, and accepted evidence enters DeskPet's existing scoring pipeline. |
| AC-AR-4 | Make Agent-Reach return empty/off/error/degraded while generic evidence succeeds. | DeepResearch still completes. Empty is not recorded as a failure; real degradation has channel, backend, status and reason code. |
| AC-AR-5 | Run flat core, durable v1 and fan-out. | Coverage and successful node Trace contain the same JSON-safe route.agent_reach projection with stable planned/hit/degraded ordering. |
| AC-AR-6 | Build/import the packaged surface and inspect routing. | PyInstaller collects Agent-Reach modules/data. Strong DeepResearch triggers deterministically start the native workflow before AgentLoop; the outer model cannot replace it with doctor/read/web_fetch calls. No automatic cookie or system-package setup occurs. |
| AC-AR-7 | Submit a repository-focused DeepResearch task in the real Windows UI. | One progress card updates in place and a complete cited Markdown report appears in the same Session. Retained Trace/log evidence identifies the requested channel and active backend or an explicit degradation. |

## Runtime state meanings

- planned_channels: channels inferred from the request and subquestions.
- hits: Agent-Reach reads that produced accepted evidence.
- degraded: only off/error/degraded calls, never a normal empty result.
- doctor: Agent-Reach's bounded current setup snapshot.

## Safety boundary

Public unauthenticated channels may run by default. Cookie import, account login,
system package installation and credential configuration require explicit user
action and are outside this change.
