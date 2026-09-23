# PR-7 NanoJev Decision Host Integration — Task Brief

Date: 2026-09-20 (CST)
Baseline: Host `main@6c457908` · SDK `main@51dbed2`
Plan source: `NanoJevAdd.md` §57 PR-7 (Shadow 接入) + `执行workplan-HTN-H1-NanoJev-2026-09-19.zh-CN.md` 阶段 4
Blocker input (read-only): SDK `plans/2026-09-20-nanojev-pr7-shadow/PR7-BLOCKER.md`

## 1. Parent-approved ruling this brief implements

B-1 and B-2 from the SDK blocker report are both answered by the parent, and the ruling
narrows what "PR-7 integration" means:

1. **The Host owns `decision.mode`.** It parses it from its own configuration carrier and
   passes an **explicit typed policy/adapter** into the SDK. The SDK does not gain a config
   file, a config field, or an environment variable; `DecisionMode.EXISTING` stays the
   default and is never re-derived from an ad hoc environment variable.
2. **`READY_TASK_PRIORITY` is Shadow-only observation of the deterministic `frontier()`
   ordering, while the allocator's grant set stays authoritative.** The observation may not
   influence which Tasks are granted, in any mode.
3. **`RETRY_OR_ESCALATE` is deferred.** `RetryAction` has six values; the ruling does not
   authorise mapping them onto the two-value `DecisionType` contract yet. No retry seam is
   wired in this slice.
4. **`decision_id` gets a new deterministic namespace and never reuses `pd-`.** The H1
   planning-decision namespace is closed to NanoJev (already refused by
   `DecisionEventJournal.is_accepting_decision_id`).
5. Not permitted: ad-hoc environment variable, Primary, legacy behaviour change, checkpoint
   change, public packaging/release.

## 1a. Authorized local runtime-closure extension (2026-09-20)

The parent approved a controlled extension after the first source-level seam probe showed
that the Host's real process imported SDK `0.11.1` while the Host pin was `0.12.2`, and that
the live Mission driver still called `event_handler._decide()` → `allocate()` directly.
The following are now explicitly in scope:

- the SDK `event_handler.py` caller, but only the `READY_TASK_PRIORITY` call site;
- a local, non-public vendored SDK/runtime synchronization sufficient to run the real Host
  process against the PR7 source (no release or remote publication);
- the smallest real Mission driver and SQLite event-journal checks needed to prove the
  caller is reached.

This extension does not authorize changing the allocator algorithm, compiler, checkpoint,
legacy protocol behavior, retry mapping, or Primary. The exact local artifact identity and
runtime source identity must be recorded with the evidence.

### 1b. Authorized local development-candidate wheel (2026-09-20)

The parent subsequently approved replacing the editable-source probe with one
locally built, non-public development candidate. The candidate is intentionally
distinct from the immutable `0.12.2` wheel: `0.13.0.dev20260920`. Its manifest
records the base commit, dirty source snapshot hash, input count, and
`release_published: false`; the dirty tree is provenance, not a clean release
claim. Host pin, lock entry, candidate constants, wheel and candidate manifest
must agree byte-for-byte. Installation is restricted to the local backend
environment and must run without `PYTHONPATH` or editable installation.

## 2. Scope

### SDK (`/Users/denny/projects/simple-harness-sdk`)

New module `src/agent_orchestrator/decision/host_integration.py` — the Host-facing adapter
the blocker report asked for, with nothing invented:

- `compute_ready_task_decision_id(...)`: deterministic `njr-` id over a canonical
  `\x1f`-delimited material string (the `pd-` convention), **never** `pd-`.
- `frontier_priority_candidates(tasks)`: the deterministic `frontier()` ordering as an
  ordered candidate set. Verified live that `allocate()` sorts by `TaskScore` and only falls
  back to `(-priority, ordinal)` when a Task has no score, so the two orderings are different
  functions; this slice makes the observed order explicit rather than claiming agreement.
- `observe_frontier_priority(...)`: build the typed request → run the injected
  `DecisionService` → record the observation. Fail-open by construction: any failure is
  returned as a recorded non-completion, never raised into production.
- **`shadow_observation_enabled(policy)`**: the plan §57 hard constraint "0/1 candidate does
  not call the model", read off the typed policy instead of off an environment variable.
- `ready_task_priority_decision(...)`: one allocator decision as a Host-facing call —
  compute grants, optionally observe, return the unchanged plan.

Existing files edited (minimal, additive): `src/agent_orchestrator/decision/__init__.py`
(re-export), `src/agent_orchestrator/scheduling/allocator.py` (three `__all__` entries only —
no behaviour).

### Host (`/Users/denny/projects/simple_harness`)

| File | Change |
|---|---|
| `backend/deskpet/orchestration/settings.py` | Read `[orchestration] decision_mode`; unknown/absent/bad-type → `"existing"`. Never read from the environment |
| `backend/deskpet/orchestration/decision.py` (new) | The seam: turn the Host setting into a typed SDK `DecisionPolicy`, build the journal/gate/observation seam, drive one allocator decision |
| `backend/deskpet/orchestration/service.py` | Build the seam once in `_open()` from the effective policy, expose a read-only status projection |
| `backend/tests/orchestration/test_decision_shadow.py` (new) | Focused behaviour tests |
| `config.toml` | Document the key (absent = `existing`) |
| SDK `event_handler.py` | Route the legacy `allocate()` call through the Host-provided decision seam; preserve the returned allocation plan and legacy default. |
| local vendored SDK artifact | Build/install only for the local runtime probe; record identity, never publish. |

Untouched: the compiler, the checkpoints, any legacy byte or default,
`DecisionMode.NANOJEV` reachability (the Host parser has no value that produces it), and
`RETRY_OR_ESCALATE`.

## 3. Behaviour requirements (must be pinned by tests)

1. Default `EXISTING`: no `decision_mode` key, an unknown value, a non-string, and a
   NANOJEV-shaped value all resolve to the existing path.
2. Shadow failure isolation: a raising, timing-out, or malformed shadow provider leaves the
   production `AllocationPlan` byte-identical and does not raise.
3. Deterministic ids: same `(mission, task, candidates, context)` → same id; a different
   ordinal/candidate set/context → a different id; the id never starts with `pd-`.
4. Frontier observation with unchanged grants: the grants, scores, `open_attempts`,
   `eligible` and `slots` are equal with and without observation.
5. No retry integration: observing a `ready_task_priority` decision writes no
   `retry_or_escalate` decision, and the module exposes no retry adapter.

## 4. Blockers / limits recorded, not worked around

- The observations are **structural**, not model-driven: there is no real NanoJev checkpoint
  and no `decision.mode: shadow` model runtime in this slice. The seam is real, the model is
  not.
- `frontier()` order ≠ `allocate()` grant order when scores exist. The observation records
  the frontier order as dictated; it does not claim the two agree.
- `RETRY_OR_ESCALATE` remains unwired (§1.3).
- The Host's write path for decision events needs a live `Orchestrator` store; the focused
  tests drive the seam with a stub store, so "event rows land in the real orchestrator.db"
  is covered only at the seam level, not in a real Mission.

The runtime-closure extension is complete only when the last limitation is replaced by a
real Mission probe; otherwise the result remains source-level only.
