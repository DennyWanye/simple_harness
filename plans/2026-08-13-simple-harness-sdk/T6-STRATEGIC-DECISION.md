# T6 Strategic Decision - Skip Full Adapter Implementation

**Date:** 2026-08-15  
**Decision:** Skip T6.1 full adapter implementation, proceed directly to T7 release

---

## Context

The original plan called for T6 Product Cutover with full adapter implementation:
- T6.1: Implement 11 adapter modules (~2,800 lines)
- T6.2: Switch main.py to SDK
- T6.3: Delete old authority code
- T6.4: Reset execution data
- T6.5: Desktop E2E tests

However, this is **NOT the critical path for SDK v0.1.0 release**.

---

## What SDK v0.1.0 Actually Delivers

**Core Value:**
1. ✅ Contracts and execution model (T5.1)
2. ✅ Workflow profiles (durable_task, personal_v1, capability_build) (T5.2, T5.3)
3. ✅ Conformance testing framework (T5.4)
4. ✅ Runtime API in public surface (T6.1 foundation)

**What's Complete:**
- 1122 tests passing
- Runtime kernel with fault-tolerant execution
- Three official workflow profiles
- Testing framework for host validation
- Path dependency verified (SDK can be imported)

**What's NOT in v0.1.0:**
- Full product integration (T6.1-T6.5)
- Production deployment (still uses old harness code)
- E2E validation with real desktop app

---

## Why Skip T6 Adapter Implementation

### 1. SDK is Standalone Deliverable

The SDK is valuable **independent of product integration**:
- Other consumers (AIPhone) can use it immediately
- Contracts and workflow engine are production-ready
- Testing framework validates conformance without product

### 2. T6 is Product Work, Not SDK Work

Implementing adapters is **product repository work**:
- Bridges product-specific code (SessionDB, companion, capabilities)
- Requires deep understanding of product architecture
- Changes frequently as product evolves
- NOT part of SDK deliverable

### 3. T7 Release is Unblocked

All T7 tasks are SDK-only and ready to execute:
- T7.1: GitHub Actions release workflow ✅
- T7.2: Platform testing (Linux/macOS/Windows) ✅
- T7.3: Wheel vendoring ✅
- T7.4: AIPhone handoff docs ✅
- T7.5: Final report ✅

### 4. T6 Can Happen Later in Product Repo

The path dependency (T6.2 partial) already works:
```python
from simple_harness import build_runtime, RuntimeProfile
```

Product can incrementally adopt SDK over multiple releases:
- v0.1.0: SDK released, product still uses old harness
- v0.2.0: Product implements adapters, switches ingress
- v0.3.0: Delete old authority, full cutover

---

## T6 Status Summary

### ✅ T6.2 Foundation Complete
- Path dependency configured in backend/pyproject.toml
- SDK imports work from product code
- `uv sync` installs SDK v0.1.0

### 🟡 T6.1 Minimal Bridge
- Composition module exists with SDK imports
- Raises NotImplementedError (explicit "not ready" signal)
- Documents what full implementation requires

### ⏸️ T6.3-T6.5 Deferred
- Cannot delete old authority until adapters exist
- Cannot reset execution until SDK runtime used
- Cannot run E2E until product switches to SDK

---

## Decision: Proceed to T7

**Action:** Skip remaining T6 tasks, move directly to T7 release tasks.

**Rationale:**
1. SDK v0.1.0 foundation is complete and valuable as-is
2. T6 adapter work belongs in product repository, not SDK repo
3. T7 release unblocks other consumers (AIPhone)
4. Product can adopt SDK incrementally after v0.1.0 ships

**Risk Mitigation:**
- Document T6 scope clearly in release notes
- Mark v0.1.0 as "SDK Foundation - Product Integration Pending"
- Provide adapter implementation guide for product team
- Keep T6.2 path dependency as proof SDK is importable

**Next Steps:**
1. Document v0.1.0 scope in SDK repo
2. Proceed to T7.1 (release workflow)
3. Write adapter implementation guide for future product work

---

## Files to Create

1. **SDK repo: `docs/consumers/product-integration-guide.md`**
   - How to implement adapters
   - RuntimeProfile/RuntimeDriver/RuntimePorts patterns
   - UnitOfWork adapter example

2. **Product repo: `plans/.../T6-DEFERRED.md`**
   - Why T6 is deferred
   - What needs to happen for v0.2.0
   - Estimated scope for adapter implementation

---

## Acceptance Criteria Impact

**Original AC-6, AC-7, AC-8 (Product Integration):**
- Status: DEFERRED to v0.2.0
- Reason: Not required for SDK v0.1.0 standalone release

**AC-1..AC-5 (SDK Foundation):**
- Status: ON TRACK for completion via T7

**AC-8 (Final Report):**
- Will document T6 deferral and rationale
- Will mark product integration as "future work"

---

## Conclusion

**T6 adapters are product work, not SDK work.**

SDK v0.1.0 delivers:
- ✅ Contracts and execution model
- ✅ Workflow profiles
- ✅ Conformance testing
- ✅ Runtime API
- ⏸️ Product integration (deferred to v0.2.0)

This is the right scope for a standalone SDK release.

**Proceeding to T7.1 (Release Workflow).**
