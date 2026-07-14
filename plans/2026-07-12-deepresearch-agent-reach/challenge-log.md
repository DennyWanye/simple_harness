# Plan challenge log

## Superseded direction

The first four challenge rounds covered a DeskPet-native channel registry and
GitHub REST adapter. A real Session run completed but source metadata was too
thin. The user then selected direct Agent-Reach integration, so those work items
and their implementation were removed rather than maintained in parallel.

## Direct integration review

### Round 1 - boundary and failure semantics

Result: revise.

- Removed process-global PATH mutation entirely.
- Limited this slice to one-to-four explicit public URLs; removed the synthetic
  GitHub owner/repo parser and platform-keyword promise.
- Added local/private/credential-bearing URL rejection.
- Bounded doctor channels/backends/text, isolated metadata failures and expanded
  diagnostic redaction.
- Counted a hit only after evidence is accepted into DeskPet's passage pool.
- Made Agent-Reach mandatory during frozen build instead of warning and skipping.

Rounds 2 and 3 cover upstream API contracts, multi-URL behavior, package
reproducibility, fan-out/Trace parity and mechanically testable UI evidence.

### Round 2 - run-level routing and upstream contract

Result: revise.

- Promoted explicit URLs to one run-level list: original request first,
  subquestions second, stable deduplication, four total.
- URL-bearing tasks stay on the parent research flow so planner fan-out cannot
  erase or rewrite the requested URLs.
- Added URL to every hit/degraded record and merged doctor snapshots by channel.
- Serialized access to Agent-Reach's mutable channel instances without touching
  process environment.
- Added a real pinned-upstream registry/web method contract test and multi-URL
  execution, ordering, cap and fan-out-bypass tests.
- Kept durable Trace parity and frozen artifact smoke as final gates.

### Round 3 - distribution and product ingress

Result: revise.

- Rebuilt the frozen backend from scratch; startup smoke passed and the
  PyInstaller PYZ manifest contains Agent-Reach channels.
- Added installed-source URL/hash assertions and successful-node Trace checks.
- Real UI testing found two deeper bypasses: the assembled tool whitelist was
  not passed to AgentLoop, and a model may decline even the only offered tool.
- Wired the whitelist across main.py -> AgentLoop and added deterministic
  pre-AgentLoop ingress for strong DeepResearch requests.

### Round 4 - final gate

Result: PASS after closing the independent review's two documentation gates.

- Final Computer Use run 4221571b81314ac9b0210d4214de4bbb completed in Session
  f45f61f1-5ee2-43a5-b9c7-ee1273bafdf4.
- One card updated from 3/7 to 7/7 and 100%; the cited report appeared in the
  same Session and survived panel close/reopen without duplication.
- Durable Trace records the exact URL, github channel, Jina Reader hit, no
  degradation, and ok direct-through-persist nodes.
- Clarified that `74 passed` was a fully green focused suite, not 74 of 76;
  the final broader gate is `310 passed, 0 failed`.
- Retained both 3/7 and 7/7 UI screenshots, a concise durable Trace extract,
  final frozen rebuild evidence and startup smoke results. STATUS and both
  indexes now point to the completed evidence.
- Independent final re-review: `VERDICT: PASS`; no remaining blocker.
