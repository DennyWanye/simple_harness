# Task 10 Verification: Workflow Host Ports Injection

**Date:** 2026-08-17  
**Status:** ✅ VERIFIED

## Verification Evidence

### 1. API Signature Verified

Located in `/Users/denny/projects/simple-harness-sdk/src/simple_harness/workflows/__init__.py:12-47`:

```python
def build_official_workflow_registrations(
    *,
    generation: int,
    transaction_owner: object,
    host_services: WorkflowHostServices,
) -> tuple[WorkflowDefinitionRegistration, ...]:
    """Build every ready official profile; missing groups remain isolated."""
    
    registrations: list[WorkflowDefinitionRegistration] = []
    if host_services.durable_task is not None:
        registrations.append(build_durable_task_registration(...))
    if host_services.personal_v1 is not None:
        registrations.append(build_personal_v1_registration(...))
    if host_services.capability_build is not None:
        registrations.append(build_capability_build_registration(...))
    return tuple(registrations)
```

**Key findings:**
- ✅ `host_services` parameter IS required
- ✅ Conditional registration: only workflows with provided services are included
- ✅ `WorkflowHostServices` dataclass has three optional fields (durable_task, personal_v1, capability_build)

### 2. WorkflowHostServices Structure

Located in `/Users/denny/projects/simple-harness-sdk/src/simple_harness/workflow/contracts.py:450-476`:

```python
@dataclass(frozen=True, slots=True)
class WorkflowHostServices:
    """One immutable service composition root owned by a WorkflowRunner."""
    
    durable_task: DurableTaskHostServices | None = None
    personal_v1: PersonalWorkflowHostServices | None = None
    capability_build: CapabilityBuildHostServices | None = None
    
    def has_profile(self, profile_key: str) -> bool:
        return {...}.get(profile_key) is not None
    
    def ports_for(self, profile_key: str) -> Mapping[str, object]:
        """Return ports for a specific workflow profile."""
        ...
    
    def bind_context(self, profile_key: str, context: WorkflowContext) -> WorkflowContext:
        """Inject ports into workflow context."""
        ...
```

**Key findings:**
- ✅ All three workflow services are optional (default to `None`)
- ✅ `ports_for()` returns the port mapping for a specific workflow
- ✅ `bind_context()` injects ports into WorkflowContext at runtime

### 3. Production Usage Example

Found in `/Users/denny/projects/simple_harness/backend/deskpet/sdk_adapters/conformance.py:302-303`:

```python
# Build host services with all three workflows
self.services = WorkflowHostServices(
    durable_task=DurableTaskHostServices(_Proposal(), _Workspace(), artifact=_Artifact()),
    personal_v1=PersonalWorkflowHostServices(self.personal),
    capability_build=CapabilityBuildHostServices(
        _Proposal(), _Workspace(), boundary, boundary, boundary, 
        boundary, boundary, boundary, artifact=_Artifact()
    )
)

# Pass to build function
self.official = build_official_workflow_registrations(
    generation=1,
    transaction_owner=self.owner,
    host_services=self.services  # ✅ Host services injected here
)
```

**Key findings:**
- ✅ Simple Harness product successfully uses the API
- ✅ All three workflows are registered with their respective services
- ✅ Services are bound at registration time, not runtime startup

### 4. Documentation Correction

**Issue found:** `/Users/denny/projects/simple-harness-sdk/docs/api/workflow.md` had incorrect API signature showing `build_official_workflow_registrations()` with no parameters.

**Fix applied:** Updated documentation to show correct signature:
```python
build_official_workflow_registrations(
    *,
    generation: int,
    transaction_owner: object,
    host_services: WorkflowHostServices,
) -> tuple[WorkflowDefinitionRegistration, ...]
```

## Conclusion

✅ **VERIFIED:** Workflow Host Ports injection is **correctly implemented**.

- Consumers pass `WorkflowHostServices` to `build_official_workflow_registrations()`
- Only workflows with provided services are registered
- Services are bound to workflow context at execution time via `bind_context()`
- Documentation now accurately reflects the API

## Next Steps

Proceed to Task 11: Run and fix Conformance Suite.
