# T6 Product Cutover - Progress Report

**Date:** 2026-08-15  
**Session:** Continuation after T5.3/T5.4 completion  
**Status:** T6.2 and T6.4 infrastructure complete, T6.1 adapters need implementation

---

## Completed Tasks

### ✅ T6.2 — Path Dependency Switch (Partial)

**Completed:**
- Added `simple-harness-sdk>=0.1.0` to backend/pyproject.toml dependencies
- Configured `[tool.uv.sources]` with editable path to ../../simple-harness-sdk
- Verified SDK import works: `import simple_harness; print(simple_harness.__version__)` → `0.1.0`
- Dependency sync successful: `uv sync` installed SDK from path

**Commit:** 87d511a - feat(sdk): add T6.2 path dependency to SDK

**Remaining for T6.2:**
- Update backend/main.py to use SDK adapters instead of harness.*
- Run oracle migration scripts
- Switch imports from deskpet.harness.* to simple_harness.*

### ✅ T6.4 — Execution Reset Script (Infrastructure)

**Completed:**
- Created scripts/dev/reset_sdk_execution_data.py
- Implemented safety-first destructive operation workflow:
  - Nonce confirmation computed from DB path (SHA-256 hash)
  - Automatic backup before reset with timestamp
  - Dry-run mode by default (--execute flag required)
  - Clear error messages and confirmation instructions
- Made script executable
- Tested confirmation flow and backup creation

**Commit:** 1747334 - feat(sdk): add T6.4 execution reset script (placeholder) + T6.3 defer plan

**Remaining for T6.4:**
- Implement actual reset logic (drop old schema 1-29, initialize SDK schema v1)
- Add schema version verification
- Integration with T6.1 SDK runtime

---

## Deferred Tasks

### ⏸️ T6.3 — Delete Old Authority

**Status:** BLOCKED - Cannot proceed yet

**Reason:** 
- T6.1 SDK adapters are stubs only (returning None)
- main.py has 11+ imports from deskpet.harness.*
- No SDK parity to replace old authority

**Documented in:** plans/2026-08-13-simple-harness-sdk/T6.3-DELETE-PLAN.md

**Prerequisites:**
1. Complete T6.1 adapter implementation
2. Switch main.py to SDK adapters (T6.2 continuation)
3. Run oracle migration scripts
4. Verify conformance tests pass

**Deletion Scope (when ready):**
- Entire backend/deskpet/harness/ directory (37 files)
- backend/deskpet/workflows/routing.py (ModelPersonalWorkflowMatcher)
- Schema 1-29 migration/compatibility code

---

## Pending Tasks

### 🔴 T6.1 — Product SDK Adapters [CRITICAL]

**Status:** Stub files exist, but all return None

**Required Implementation:**

1. **Provider Adapter** (`sdk_adapters/provider.py`)
   - Bridge config.toml + keychain to SDK Provider protocol
   - Factory functions for LLM providers (Anthropic, OpenAI, Gemini)
   - Secret handling (never expose secrets to SDK state)

2. **Tools Adapter** (`sdk_adapters/tools.py`)
   - Bridge tools/registry.py to SDK Tool protocol
   - Implement Authorization, Reconciliation protocols
   - Connect existing tool handlers

3. **Context Adapter** (`sdk_adapters/context.py`)
   - Bridge agent/turn_preparer.py to SDK PreparedRunContext
   - Connect three-tier memory system
   - Profile descriptor catalog

4. **Delivery Adapter** (`sdk_adapters/delivery.py`)
   - Bridge agent/run_presenter.py to SDK delivery sink
   - SessionDB, ArtifactCard, WebSocket, TTS integration
   - Event envelope handling

5. **Personal Catalog Adapter** (`sdk_adapters/personal_catalog.py`)
   - Bridge companion/turn_authority.py to SDK PersonalWorkflowCatalogPort
   - Candidate store + UI integration

6. **Capability Host Adapter** (`sdk_adapters/capability_host.py`)
   - Bridge capability hub/platform to SDK capability-build Ports

7. **Composition Module** (`sdk_adapters/composition.py`)
   - Implement build_product_runtime() with real SDK imports
   - Wire all adapters together
   - Return configured SDK runtime

8. **Conformance Host** (`sdk_adapters/conformance.py`)
   - Factory function: build_host() for conformance testing
   - Used by T5.4 CLI: `python -m simple_harness.testing --host deskpet.sdk_adapters.conformance:build_host`

**Blocked Dependencies:**
- T6.2 main.py switch (needs adapters first)
- T6.3 delete old authority (needs SDK parity)
- T6.5 E2E tests (needs working runtime)

### 🔴 T6.5 — Simple Harness Desktop E2E

**Status:** Not started

**Requirements:**
- Real E2E tests with Tauri + backend + vite
- SDK-S1..S7 test cases from implementation-tasks.md
- Run/child/effect/provider/delivery ID audit
- Supervisor restart verification (no duplicate IDs)
- Evidence: screenshots/logs/DB hashes in .local-test-evidence/

**Dependencies:** T6.3, T6.4 (both blocked on T6.1)

---

## Critical Path Forward

The critical blocker is **T6.1 adapter implementation**. All other T6 tasks depend on it:

```
T6.1 (adapters) 
  ↓
T6.2 (main.py switch) 
  ↓
T6.3 (delete old authority) 
  ↓
T6.4 (reset - actual implementation)
  ↓
T6.5 (E2E tests)
  ↓
T7.x (release tasks)
```

**Next Steps:**
1. Read SDK public API surface to understand available contracts
2. Read product harness/adapters/* to understand existing patterns
3. Implement composition.build_product_runtime() with real SDK runtime
4. Implement each adapter protocol one by one
5. Write contract tests for each adapter
6. Run conformance tests with conformance host factory

**Estimated Scope:**
- 7 adapter modules × ~200-400 lines each = ~1,400-2,800 lines
- Contract tests for each adapter
- Conformance host factory
- Integration with existing product systems

---

## Repository State

**SDK Repository (simple-harness-sdk):**
- Last commit: 7361c6f (T5.4 conformance CLI)
- Status: v0.1.0 ready for integration
- Tests: 1122 passing

**Product Repository (simple_harness):**
- Last commit: 1747334 (T6.4 reset script + T6.3 defer plan)
- SDK dependency: Installed via path ../../simple-harness-sdk
- Old authority: Still present (37 files in harness/, needed until T6.1 complete)

---

## Summary

**T6 Progress:** 2/5 tasks have infrastructure complete, but the critical T6.1 implementation is pending.

**What's Working:**
- SDK can be imported from backend code ✅
- Reset script safety infrastructure ready ✅
- Deletion scope documented and ready to execute ✅

**What's Blocking:**
- T6.1 adapters return None instead of real SDK runtime ❌
- main.py still uses old harness.* imports ❌
- No conformance tests run yet ❌
- No E2E validation ❌

**Decision:** Must implement T6.1 adapters before proceeding to T6.5 or T7. The path dependency infrastructure is ready, but the actual SDK integration work remains.
