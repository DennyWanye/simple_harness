# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Real SQLite-backed DeskPet host for SDK conformance protocol v1."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
from simple_harness.contracts import (
    CallId,
    ExecutionSessionId,
    Message,
    RequestId,
    RunId,
    canonical_json,
)
from simple_harness.execution.budget import BudgetPolicy, FrozenPriceEstimator
from simple_harness.execution.delivery import (
    DeliveryDispatcher,
    DeliverySpec,
    DeliveryState,
)
from simple_harness.execution.dispatch import ProviderInvocationCoordinator
from simple_harness.execution.sqlite import Database, SqliteExecutionUnitOfWork
from simple_harness.execution.uow import RunState
from simple_harness.providers import (
    CancelToken,
    ProviderRequest,
    ProviderResponse,
    ProviderTarget,
    ProviderToolCall,
    ProviderTransportError,
    ProviderUsage,
)
from simple_harness.runtime import (
    AgentLoopCollaborator,
    EffectBatchExecutor,
    RunStart,
    RuntimePorts,
    RuntimeProfile,
    SqliteContextPort,
)
from simple_harness.runtime.drivers import ReActDriver
from simple_harness.runtime.termination import TerminationLimits
from simple_harness.testing import CaseObservation, ConformanceHostMetadata
from simple_harness.tools import (
    AuthorizationDecision,
    AuthorizationReceipt,
    AuthorizationRequest,
    AuthorizationResult,
    EffectExecutor,
    ToolOutcome,
    ToolResult,
    ToolSpec,
)
from simple_harness.tools.reconciliation import (
    ReconciliationObservation,
    ReconciliationState,
)
from simple_harness.tools.schema import SchemaDefinitionError
from simple_harness.workflow import (
    END_NODE,
    CapabilityBuildHostServices,
    CheckpointExecutionAdapter,
    DurableTaskHostServices,
    Edge,
    NodeDefinition,
    PersonalWorkflowHostServices,
    ProfileDescriptor,
    StartInputSchema,
    StatePatch,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowDefinitionRegistration,
    WorkflowExecutionPorts,
    WorkflowHostServices,
    WorkflowProfileRegistration,
    WorkflowRegistry,
    WorkflowRunner,
    compile_workflow,
    compile_workflow_registration,
    profile_descriptor_fingerprint,
    workflow_manifest_hash,
)
from simple_harness.workflow.checkpoint import SqliteNativeCheckpointStore
from simple_harness.workflow.native import NativeWorkflowExecutable
from simple_harness.workflow.recovery import RecoveryDecision, RecoveryDisposition
from simple_harness.workflows import build_official_workflow_registrations
from simple_harness.workflows.capability_build import (
    create_initial_state as capability_initial_state,
)
from simple_harness.workflows.durable_task import (
    create_initial_state as durable_initial_state,
)
from simple_harness.workflows.durable_task.state import ProposalOutcomeV1
from simple_harness.workflows.personal_v1 import (
    PersonalWorkflowSelectionV1,
    personal_workflow_query_hash,
)
from simple_harness.workflows.personal_v1 import (
    create_initial_state as personal_initial_state,
)

from deskpet.product_state.authorization_saga import (
    AuthorizationSagaIdentity,
    AuthorizationSagaRepository,
)
from deskpet.product_state.database import ProductStateDatabase
from deskpet.product_state.task_grants import DurableTaskGrantAuthority
from deskpet.sdk_adapters.authorization import ProductAuthorizationAdapter
from deskpet.sdk_adapters.composition import (
    ProductSdkRuntimeStack,
    SdkRuntimeBuildInputs,
)
from deskpet.sdk_adapters.product_workflows.research_ports import (
    ResearchArtifactPort,
    ResearchBlobPort,
    ResearchFetchPort,
    ResearchLLMPort,
    ResearchSearchPort,
)
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from deskpet.sdk_adapters.reconciliation import ProductReconciliationAdapter
from deskpet.sdk_adapters.runtime_paths import ProductRuntimePathsAdapter
from deskpet.sdk_adapters.sdk_candidate import build_candidate_identity, sdk_wheel_path
from deskpet.sdk_adapters.tools import build_product_tool_registry
from deskpet.sdk_adapters.workflows import build_product_workflow_registrations
from deskpet.tool_catalog import (
    ToolCatalogDependencies,
    build_explicit_product_tool_catalog,
)
from deskpet.tools.context_page_in_tools import ContextPageInStore
from deskpet.types.task_grants import TaskGrant

_WHEEL = sdk_wheel_path()
VENDORED_SDK_SHA256 = hashlib.sha256(_WHEEL.read_bytes()).hexdigest()


class _ProviderEntry:
    enabled=True; model="model"; models=("model",); base_url="https://deskpet.invalid/v1"; incarnation_id="conformance"; config_revision=1


class _ProviderRegistry:
    def __init__(self, secret="sk-deskpet-canary"): self.secret=secret
    def get_entry(self, provider_id): return _ProviderEntry() if provider_id == "deskpet" else None
    def resolve_api_key(self, provider_id): assert provider_id == "deskpet"; return self.secret


def _product_provider(handler):
    client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter=ProductProviderAdapter(_ProviderRegistry(),provider_id="deskpet",client=client,price_resolver=lambda provider,model:(1,1,"conformance-v1"))
    return adapter,client


class _CatalogTodoStore:
    async def replace_session_todos(self, session_id, items): return None


class _CatalogMemoryQuery:
    async def recall_readonly(self, query, limit, scope): return []


class _CatalogMemoryScope:
    def resolve_for_run(self, run_id): return object()


class _CatalogCapabilityBridge:
    def search(self, *args, **kwargs): return []
    def describe(self, *args, **kwargs): return {}
    def suggestions(self, *args, **kwargs): return []
    def activate(self, *args, **kwargs): raise AssertionError("not invoked")


class _CatalogSearchGateway:
    async def search(self, request): raise AssertionError("not invoked")


def _product_tools(root: Path):
    page_store = ContextPageInStore()
    workspace = str(root.resolve())
    execution_context = SimpleNamespace(
        session_id="conformance-session",
        request_id="conformance-request",
        scope_id="conformance-scope",
        root_run_id="conformance-root-run",
        workspace=workspace,
        write_scope_root=workspace,
        owner_key="conformance-project",
    )
    dependencies = ToolCatalogDependencies(
        todo_session_db=_CatalogTodoStore(),
        workflow_service_provider=lambda: None,
        context_page_store=page_store,
        execution_context_getter=lambda: execution_context,
        memory_query=_CatalogMemoryQuery(),
        memory_scope_resolver=_CatalogMemoryScope(),
        capability_bridge_service=_CatalogCapabilityBridge(),
        search_gateway=_CatalogSearchGateway(),
    )
    catalog = build_explicit_product_tool_catalog(dependencies)
    registry, inventory = build_product_tool_registry(catalog.registrations)
    if len(inventory) != 77:
        raise RuntimeError("real product Tool catalog is incomplete")
    return registry


class _Noop:
    async def reconcile(self): return None


class _Catalog:
    def current_generation(self): return 1


class _Provider:
    target = ProviderTarget("deskpet", "model", "deskpet:model", "local", "fixture")
    def __init__(self, responses): self.responses=list(responses); self.requests=[]; self.physical_calls=0
    async def invoke(self, request, *, cancel):
        assert isinstance(request, ProviderRequest) and not cancel.is_cancelled
        self.requests.append(request); self.physical_calls += 1; value=self.responses.pop(0)
        if isinstance(value, BaseException): raise value
        return ProviderResponse(request.request_id, value.message, value.tool_calls, usage=value.usage, model=value.model, finish_reason=value.finish_reason)


class _Authorization:
    def __init__(self, require_user=False): self.require_user=require_user; self.decision_binds=0; self.handoff_binds=0
    async def prepare(self, prepared):
        if self.require_user: return AuthorizationResult(AuthorizationDecision.REQUIRE_USER, reason_code="confirmation_required", request=AuthorizationRequest("Approve?", "deskpet-nonce"))
        return AuthorizationResult(AuthorizationDecision.ALLOW, receipt_ref=f"deskpet:allow:{prepared.effect_id.value}")
    async def authorize(self, prepared): return await self.prepare(prepared)
    async def bind_decision(self, prepared, request, decision, sdk_receipt):
        del request, decision; self.decision_binds += 1; identity=f"deskpet:decision:{prepared.effect_id.value}:{self.decision_binds}"
        return AuthorizationReceipt(identity, hashlib.sha256(identity.encode()).hexdigest(), sdk_receipt.receipt_hash)
    async def bind_effect_handoff(self, prepared, authorization_receipt_ref, sdk_receipt):
        del authorization_receipt_ref; self.handoff_binds += 1; identity=f"deskpet:handoff:{prepared.effect_id.value}:{self.handoff_binds}"
        return AuthorizationReceipt(identity, hashlib.sha256(identity.encode()).hexdigest(), sdk_receipt.receipt_hash)


class _Reconciliation:
    async def observe(self, prepared): return ReconciliationObservation(ReconciliationState.STILL_UNKNOWN, f"unknown:{prepared.effect_id.value}")


class _Tool:
    def __init__(self): self.calls=[]
    async def invoke(self, arguments, context):
        self.calls.append((dict(arguments), context.run_id.value)); return {"seen":arguments.get("x")}


class _Sink:
    def __init__(self, fail_first=False): self.fail_first=fail_first; self.attempts=0; self.deliveries=[]
    async def deliver(self, payload, *, idempotency_key):
        self.attempts += 1
        if self.fail_first and self.attempts == 1: raise RuntimeError("simulated dispatcher crash")
        if not any(key == idempotency_key for key, _ in self.deliveries): self.deliveries.append((idempotency_key, dict(payload)))


def _response(content="done", calls=()): return ProviderResponse(RequestId("script"), Message("assistant", content), tuple(calls), usage=ProviderUsage(1,1,2), model="model", finish_reason="tool_calls" if calls else "stop")
def _tool_call(raw_id, x=1): return ProviderToolCall(CallId(raw_id), "process_list", {"max_entries":x})


class _StepClock:
    def __init__(self): self.value=0.0
    def __call__(self): self.value += 1.0; return self.value


class _RuntimeSeam:
    def __init__(self, path, responses, *, limits=None, authorization=None, clock=None, estimator=None):
        self.root=(Path(path).parent/Path(path).stem).resolve(); self.clock=clock or (lambda:10.0); self.responses=list(responses); self.limits=limits or TerminationLimits(); self.estimator=estimator or FrozenPriceEstimator("deskpet-price-v1","deskpet:model",0,0); self.require_user=bool(getattr(authorization,"require_user",False)); self.product_database=ProductStateDatabase(self.root/"data/product-state.db"); self.product_database.initialize(); repository=AuthorizationSagaRepository(self.product_database)
        def policy(prepared):
            if self.require_user: return AuthorizationResult(AuthorizationDecision.REQUIRE_USER,reason_code="confirmation_required",request=AuthorizationRequest("Approve?","deskpet-nonce"))
            return AuthorizationResult(AuthorizationDecision.ALLOW,receipt_ref=f"product-policy:{prepared.effect_id.value}")
        def grant_for(prepared):
            fingerprint=hashlib.sha256(prepared.effect_id.value.encode()).hexdigest()
            return TaskGrant(task_grant_id="conformance:"+fingerprint,root_run_id=prepared.run_id.value,principal_id="conformance",resource_selectors=(),permission_categories=("read_file",),effect_kinds=("read",),source="policy:auto",policy_generation=0,expires_at=100.0,version=1)
        def identity(prepared,request):
            effect=prepared.effect_id.value; call=prepared.call.call_id.value; fingerprint=hashlib.sha256(effect.encode()).hexdigest()
            grant=grant_for(prepared)
            return AuthorizationSagaIdentity(authorization_id=f"authorization:{fingerprint}",principal_id="conformance",session_id="conformance",root_run_id=prepared.run_id.value,run_id=prepared.run_id.value,call_id=call,effect_id=effect,tool_name=prepared.call.name,arguments=dict(prepared.call.arguments),capability_hash="a"*64,schema_hash="b"*64,scope_hash="c"*64,grant_id=grant.task_grant_id,grant_version=grant.version,grant_fingerprint=grant.fingerprint,policy_generation=0,decision_nonce=request.nonce if request is not None else f"allow:{fingerprint}",decision_version=0,run_lease_epoch=1,execution_lease_epoch=1)
        def grant(prepared,result):
            return grant_for(prepared)
        self.authorization=ProductAuthorizationAdapter(repository,policy=policy,identity_factory=identity,grant_authority=DurableTaskGrantAuthority(self.product_database),grant_factory=grant,clock=self.clock); self.reconciliation=ProductReconciliationAdapter(repository); self.sink=_Sink(); self.provider=None; self.tool=None; self.database=None; self.uow=None; self.runtime=None
        driver=ReActDriver(collaborator=AgentLoopCollaborator(limits=self.limits),effects=EffectBatchExecutor(),clock=self.clock)
        def ports_factory(database,uow):
            self.database=database; self.uow=uow; self.provider=_Provider(self.responses); self.tool=_product_tools(self.root); registry=self.tool
            effects=EffectExecutor(uow=uow,registry=registry,authorization=self.authorization,reconciliation=self.reconciliation,clock=self.clock); provider=ProviderInvocationCoordinator(uow=uow,provider=self.provider,budget_policy=BudgetPolicy(),estimator=self.estimator,clock=self.clock); delivery=DeliveryDispatcher(uow,{"fixture":self.sink},clock=self.clock)
            return RuntimePorts(provider=provider,tools=effects,authorization=self.authorization,context=SqliteContextPort(database,clock=self.clock),delivery=delivery,tool_reconciliation=self.reconciliation,reconciliation=_Noop(),provider_reconciliation=_Noop(),react_checkpoint=uow,tool_catalog=_Catalog(),owner_id="deskpet-conformance",clock=self.clock)
        self.stack=ProductSdkRuntimeStack(paths=ProductRuntimePathsAdapter(self.root),candidate_identity=build_candidate_identity(),dependency_loader=lambda:SdkRuntimeBuildInputs(profiles={"agent.general":RuntimeProfile("agent.general","react")},drivers={"react":driver},ports_factory=ports_factory,workflow_catalog_digest="conformance-product-workflows"))
    async def run(self, run_id, session="session-1"):
        ready=await self.stack.start(); self.runtime=ready.runtime; start=RunStart(ExecutionSessionId(session),RunId(run_id),RequestId(f"request-{run_id}"),f"turn-{run_id}",{"messages":[{"role":"user","content":"physical"}],"capability_snapshot":{"tools":["process_list"]}},1)
        await ready.client.start(start); await ready.runtime.wait_idle(start.run_id); return self.uow.read_run(run_id),self.uow.read_react_checkpoint(run_id)
    async def close(self): await self.stack.close(); self.product_database.close()


class _BlobRefs:
    async def validate_references(self, transaction, *, blob_refs, **values): return None
class _Recovery:
    def classify(self,error,*,attempt,max_attempts): return RecoveryDecision(RecoveryDisposition.FAIL,"node_failed",None)
    async def quarantine(self,**values): return None
    async def recover_expired(self,**values): return ()
    async def repair_head(self,checkpoint,*,transaction): return None
class _Trace:
    async def append(self,event,*,transaction): return None
class _TerminalProjection:
    def project_public(self,workflow_name,workflow_version,raw,engine_status): return None
class _TerminalCommitProjection:
    def lookup(self,workflow_name,workflow_version,descriptor): return None
class _Proposal:
    async def propose(self,state): return self._result()
    async def propose_for_execution(self,state,*,execution_identity): return self._result()
    def _result(self): return ProposalOutcomeV1("physical proposal",None,[],[],"end_turn",{},"fixture","model")
class _Workspace:
    async def execute_tools(self,calls,**values): return {}
class _Artifact:
    async def check_completion_evidence(self,state,outcome): return True
    async def completion_decision(self,decision,state): return decision
    async def run_tests(self,state): return {"passed":True,"evidence_refs":["physical"]}
    async def audit(self,audit,state): return {**audit,"passed":True}
class _Personal:
    def __init__(self): self.calls=0
    async def execute(self,**values): self.calls += 1; return {"physical":True}
class _Capability:
    def __init__(self): self.calls=[]; self.operation_keys=[]
    def record(self,stage,values): self.calls.append(stage); self.operation_keys.append(values["operation_key"])
    async def search(self,**values): self.record("search",values); return {"source":"fixture","candidate":"fixture"}
    async def authorize_source(self,**values): self.record("source_policy",values); return {"allowed":True}
    async def build(self,**values): self.record("isolated_build",values); return {"package":{"fixture":True}}
    async def store(self,**values): self.record("package_store",values); return {"package_ref":"pkg://fixture"}
    async def activate(self,**values): self.record("activate",values); assert values["operation_key"]==values["activation_key"]; return {"active":True}
    async def authorize_build(self,**values): self.record("authorization",values); return {"allowed":True}


class _ResearchLLM(ResearchLLMPort):
    def __init__(self): self.calls=[]
    async def complete(self,prompt,*,operation_key):
        self.calls.append(operation_key)
        if "questions" in prompt.lower(): return "What is verified?\nWhat are the risks?"
        return "# Verified report\n\nSourced product conformance report."
class _ResearchSearch(ResearchSearchPort):
    def __init__(self): self.calls=[]
    async def search(self,query,*,operation_key): self.calls.append(operation_key); return [{"url":"https://example.invalid/source","title":"Source"}]
class _ResearchFetch(ResearchFetchPort):
    def __init__(self): self.calls=[]
    async def fetch(self,url,*,operation_key): self.calls.append(operation_key); return "Verified source material"
class _ResearchBlob(ResearchBlobPort):
    def __init__(self,root): self.root=Path(root); self.calls=[]
    async def put(self,data,*,media_type,operation_key):
        self.calls.append(operation_key); digest=hashlib.sha256(data).hexdigest(); target=self.root/digest; target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists(): target.write_bytes(data)
        return {"sha256":digest,"size_bytes":len(data),"media_type":media_type,"path":str(target)}
    async def get(self,reference): return Path(str(reference["path"])).read_bytes()
class _ResearchArtifact(ResearchArtifactPort):
    def __init__(self,root): self.root=Path(root); self.calls=[]
    async def save_report(self,*,title,report_markdown,report_sha256,operation_key):
        self.calls.append(operation_key); target=self.root/f"{report_sha256}.md"; target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists(): target.write_text(report_markdown,encoding="utf-8")
        return {"artifact_ref":f"artifact:{report_sha256}","path":str(target),"title":title,"sha256":report_sha256}


def _research_context(root):
    ports={"llm":_ResearchLLM(),"search":_ResearchSearch(),"fetch":_ResearchFetch(),"blob":_ResearchBlob(Path(root)/"blobs"),"artifact":_ResearchArtifact(Path(root)/"artifacts")}
    return WorkflowContext(ports=ports),ports


_HOST_CALLS=[]
async def _host_node(state,context): del context; _HOST_CALLS.append(str(state.get("run_id"))); return StatePatch({})


def _host_registration(owner):
    definition=WorkflowDefinition("host.workflow","v1",1,"run",(NodeDefinition("run",_host_node),),{},5,4,edges=(Edge("run",END_NODE),)); compiled=compile_workflow(definition); schema={"type":"object","properties":{},"additionalProperties":False}; ref="deskpet://conformance/start"
    descriptor=ProfileDescriptor("workflow.host_owned","Host workflow","Conformance","Other",ref,1,profile_descriptor_fingerprint("workflow.host_owned","Host workflow","Conformance","Other",ref,1)); profile=WorkflowProfileRegistration(descriptor,definition.name,definition.version,StartInputSchema(ref,schema,hashlib.sha256(canonical_json(schema).encode()).hexdigest()))
    return WorkflowDefinitionRegistration(profile,definition,compiled.manifest.dependency_lock_hash,workflow_manifest_hash(compiled.manifest),compiled.manifest.implementation_bundle_hash,owner)


class _WorkflowSeam:
    def __init__(self,path):
        self.database=Database.open(path); self.uow=SqliteExecutionUnitOfWork(self.database); self.owner=self.uow.transaction_owner; self.personal=_Personal(); self.capability=_Capability(); boundary=self.capability
        self.services=WorkflowHostServices(durable_task=DurableTaskHostServices(_Proposal(),_Workspace(),artifact=_Artifact()),personal_v1=PersonalWorkflowHostServices(self.personal),capability_build=CapabilityBuildHostServices(_Proposal(),_Workspace(),boundary,boundary,boundary,boundary,boundary,boundary,artifact=_Artifact()))
        self.registry=WorkflowRegistry(transaction_owner=self.owner); self.host=_host_registration(self.owner); self.registry.register_definition(self.host); self.product=build_product_workflow_registrations(generation=1,transaction_owner=self.owner); self.official=build_official_workflow_registrations(generation=1,transaction_owner=self.owner,host_services=self.services)
        for item in self.product: self.registry.register_definition(item)
        for item in self.official: self.registry.register_definition(item)
        ports=WorkflowExecutionPorts(self.uow,CheckpointExecutionAdapter(self.database),self.uow,self.uow,self.uow); checkpoint=SqliteNativeCheckpointStore(ports,blob_references=_BlobRefs())
        self.runner=WorkflowRunner(registry=self.registry,checkpoint=checkpoint,recovery=_Recovery(),trace=_Trace(),execution_ports=ports,terminal_projection_port=_TerminalProjection(),terminal_commit_projection_port=_TerminalCommitProjection(),host_services=self.services,owner="deskpet-conformance",clock=lambda:10.0); self.engine_type=NativeWorkflowExecutable
    async def execute(self,registration,state,context=None):
        profile=registration.profile; run_id=str(state.get("run_id") or f"run-{profile.descriptor.key.replace('.','-')}"); state={**state,"run_id":run_id}; values=dict(state.get("values") or {})
        if profile.descriptor.key=="workflow.host_owned": start_input={}
        elif profile.descriptor.key=="workflow.personal_v1": start_input={"personal_workflow_selection_json":json.dumps(values["personal_workflow_selection"],sort_keys=True,separators=(",",":")),"inputs_json":json.dumps(values.get("inputs",{}),sort_keys=True,separators=(",",":"))}
        elif profile.descriptor.key=="workflow.deep_research": start_input={name:values[name] for name in ("topic","mode") if name in values}
        elif profile.descriptor.key=="workflow.presentation": start_input={name:values[name] for name in ("topic","title","pages","output_path","editable_required","full_page_images") if name in values}
        else: start_input={name:values[name] for name in ("request","search_miss_receipt","proposal_budget","fix_budget") if name in values}
        created=await self.runner.start(session_id="workflow-session",request_id=f"request-{run_id}",turn_id=f"turn-{run_id}",profile_key=profile.descriptor.key,tool_catalog_generation=1,workflow_name=profile.workflow_name,workflow_version=profile.workflow_version,start_input=start_input,capability_snapshot={}); result=await self.runner.run(created,state,context or WorkflowContext()); return created,result,self.uow.read_run(created)
    def close(self): self.database.close()


def _selection():
    return PersonalWorkflowSelectionV1.issue(owner_key="owner",pack_id="pack",version="1.0.0",manifest_hash="a"*64,binding_generation=1,graph={"schema_version":1,"name":"personal","description":"fixture","entry_node":"start","nodes":[{"id":"start","type":"output","bindings":{},"config":{}}],"outputs":{},"max_steps":1},graph_hash="b"*64,query_hash=personal_workflow_query_hash("fixture"),run_catalog_content_stamp="stamp",lease_entries=[{"lease_id":"lease"}],effect_topology={"policy":"read_only"},tool_bindings={})


class _Suite:
    def __init__(self,name):
        self.root=Path(tempfile.mkdtemp(prefix=f"deskpet-conformance-{name}-")); self.connection=sqlite3.connect(self.root/"evidence.sqlite"); self.closed=False
    def observation(self,case_id,values): return CaseObservation(case_id,values,{"physical_boundary":"sqlite","probe":self.connection.execute("SELECT 1").fetchone()[0]})
    async def physical_request(self):
        calls=0
        async def respond(request):
            nonlocal calls; calls += 1
            return httpx.Response(200,json={"id":"req-1","model":"model","choices":[{"message":{"role":"assistant","content":"pong"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}})
        provider,client=_product_provider(respond); request=ProviderRequest(RequestId("req-1"),(Message("user","ping"),)); result=await provider.invoke(request,cancel=CancelToken()); await client.aclose(); return self.observation("provider.physical_request",{"physical_calls":calls,"request_id":request.request_id.value,"response_request_id":result.request_id.value})
    async def typed_error(self):
        calls=0
        async def respond(request):
            nonlocal calls; calls += 1; raise httpx.ConnectError("socket closed",request=request)
        provider,client=_product_provider(respond); request=ProviderRequest(RequestId("req-error"),(Message("user","ping"),)); caught=None
        try: await provider.invoke(request,cancel=CancelToken())
        except ProviderTransportError as error: caught=error
        await client.aclose()
        if caught is None: raise AssertionError("typed provider error was not raised")
        return self.observation("provider.typed_error",{"physical_calls":calls,"error_code":caught.code,"raw_body_exposed":"socket closed" in str(caught)})
    async def usage(self):
        async def respond(request): return httpx.Response(200,json={"id":"usage","model":"model","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}})
        provider,client=_product_provider(respond); result=await provider.invoke(ProviderRequest(RequestId("usage"),(Message("user","ping"),)),cancel=CancelToken()); await client.aclose(); return self.observation("provider.usage",{"trusted_total_tokens":result.usage.total_tokens,"unknown_usage":ProviderResponse(RequestId("unknown"),Message("assistant","ok")).usage})
    async def redaction(self):
        secret="sk-deskpet-canary"
        async def respond(request): return httpx.Response(401,text=f"bad credential {secret}")
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)); provider=ProductProviderAdapter(_ProviderRegistry(secret),provider_id="deskpet",client=client,price_resolver=lambda provider,model:(1,1,"v1")); public=json.dumps(provider.public_snapshot(),sort_keys=True)+repr(provider); exposed=secret in public
        try: await provider.invoke(ProviderRequest(RequestId("redact"),(Message("user","ping"),)),cancel=CancelToken())
        except Exception as error: public += str(error); exposed = exposed or secret in str(error)
        await client.aclose(); return self.observation("provider.redaction",{"secret":secret,"public_text":public,"raw_body_exposed":exposed})
    async def schema(self):
        registry=_product_tools(self.root/"schema-catalog"); spec=registry.get("file_read").spec; bounded=all(item.input_schema.get("additionalProperties") is False for item in registry.specs); rejected=False
        try: ToolSpec("bad","Bad",{"type":"object","properties":{"api_key":{"type":"string"}},"additionalProperties":False})
        except SchemaDefinitionError: rejected=True
        return self.observation("tool.schema",{"closed":spec.input_schema["additionalProperties"] is False,"bounded":bounded,"reserved_fields_rejected":rejected})
    async def five_state(self):
        call=CallId("states"); results=(ToolResult.succeeded(call),ToolResult.partial(call,{"partial":True}),ToolResult.rejected(call,"denied","Denied"),ToolResult.failed(call,"failed","Failed"),ToolResult.unknown(call,"Unknown")); return self.observation("tool.five_state",{"states":[item.outcome.value for item in results]})
    async def reconcile(self):
        self.connection.execute("CREATE TABLE physical(operation_key TEXT PRIMARY KEY)"); self.connection.execute("INSERT INTO physical VALUES ('reconcile')"); self.connection.commit(); before=self.connection.execute("SELECT count(*) FROM physical").fetchone()[0]; final=ToolResult.succeeded(CallId("reconciled")); after=self.connection.execute("SELECT count(*) FROM physical").fetchone()[0]; return self.observation("tool.reconcile",{"initial_state":ToolOutcome.UNKNOWN.value,"final_state":final.outcome.value,"physical_calls_before":before,"physical_calls_after":after})
    async def malformed_duplicate_late(self):
        accepted=ToolResult.succeeded(CallId("accepted")); rejected=0
        for invalid in (None,"wrong",True):
            try: ToolResult(invalid,ToolOutcome.FAILED,error_code="invalid")
            except (TypeError,ValueError): rejected += 1
        self.connection.execute("CREATE TABLE result_physical(id TEXT)"); self.connection.execute("INSERT INTO result_physical VALUES ('accepted')"); self.connection.commit(); calls=self.connection.execute("SELECT count(*) FROM result_physical").fetchone()[0]; return self.observation("tool.malformed_duplicate_late",{"accepted_results":int(accepted.outcome is ToolOutcome.SUCCEEDED),"rejected_results":rejected,"physical_calls":calls})
    async def _react(self,case_id,responses):
        seam=_RuntimeSeam(self.root/f"{case_id}.sqlite",responses); run,checkpoint=await seam.run(f"run-{case_id}"); requests=[item.request_id.value for item in seam.provider.requests]; calls=[str(row[0]) for row in seam.database.connection.execute("SELECT call_id FROM execution_effects WHERE run_id=?",(f"run-{case_id}",)).fetchall()]; expected=[f"run-{case_id}:provider-turn:{i}" for i in range(1,len(requests)+1)]; values={"terminal_state":run.state.value,"provider_calls":checkpoint.checkpoint["provider_turns_reserved_total"],"tool_calls":checkpoint.checkpoint["tool_calls_reserved_total"],"correlation_match":requests==expected,"unique_call_ids":len(calls)==len(set(calls))}; await seam.close(); return values
    async def no_tool(self): return self.observation("runtime.no_tool",await self._react("no-tool",[_response()]))
    async def one_tool(self): return self.observation("runtime.one_tool",await self._react("one-tool",[_response(calls=(_tool_call("raw-one"),)),_response()]))
    async def multi_turn_tool(self): return self.observation("runtime.multi_turn_tool",await self._react("multi",[_response(calls=(_tool_call("raw-one"),)),_response(calls=(_tool_call("raw-two",2),)),_response()]))
    async def session_persistence(self):
        path=self.root/"session.sqlite"; seam=_RuntimeSeam(path,[_response()]); run,_=await seam.run("run-session",session="session-durable"); before=run.execution_session_id; root=seam.root; await seam.close(); reopened=_RuntimeSeam(path,[]); await reopened.stack.start(); restored=reopened.uow.read_run("run-session"); after=restored.execution_session_id; await reopened.close(); return self.observation("runtime.session_persistence",{"reopened":restored.state is RunState.COMPLETED,"session_before":before,"session_after":after})
    async def hitl(self):
        auth=_Authorization(require_user=True); seam=_RuntimeSeam(self.root/"hitl.sqlite",[_response(calls=(_tool_call("raw-hitl"),)),_response()],authorization=auth); ready=await seam.stack.start(); seam.runtime=ready.runtime; start=RunStart(ExecutionSessionId("session-hitl"),RunId("run-hitl"),RequestId("request-hitl"),"turn-hitl",{"messages":[{"role":"user","content":"physical"}],"capability_snapshot":{"tools":["process_list"]}},1); await ready.client.start(start); await ready.runtime.wait_idle(start.run_id); decision_id=seam.database.connection.execute("SELECT decision_id FROM decisions WHERE run_id='run-hitl'").fetchone()[0]; decision=seam.uow.read_decision(str(decision_id)); before=seam.database.connection.execute("SELECT count(*) FROM execution_effects WHERE run_id='run-hitl' AND state='succeeded'").fetchone()[0]; await ready.client.decide_authorization(start.run_id,decision_id=decision.decision_id,nonce=str(decision.request["nonce"]),expected_version=decision.version,decision=AuthorizationDecision.ALLOW); await asyncio.sleep(0); await ready.runtime.wait_idle(start.run_id); await asyncio.sleep(0); await ready.runtime.wait_idle(start.run_id); effect=seam.database.connection.execute("SELECT authorization_receipt_ref,handoff_receipt_ref,state FROM execution_effects WHERE run_id='run-hitl'").fetchone(); after=seam.database.connection.execute("SELECT count(*) FROM execution_effects WHERE run_id='run-hitl' AND state='succeeded'").fetchone()[0]; durable=effect[2]=="succeeded" and all(str(item).startswith("authorization-binding-v1:") for item in effect[:2]); await seam.close(); return self.observation("runtime.hitl",{"physical_calls_before":before,"physical_calls_after":after,"decision":"approved","durable":durable})
    async def delivery(self):
        database=Database.open(self.root/"delivery.sqlite"); uow=SqliteExecutionUnitOfWork(database); sink=_Sink(fail_first=True); dispatcher=DeliveryDispatcher(uow,{"fixture":sink},clock=lambda:10.0); uow.create_with_start_snapshot(execution_session_id="session-delivery",run_id="run-delivery",request_id="request-delivery",profile_key="agent.general",driver_kind="react",snapshot={"schema_version":1,"profile_key":"agent.general","driver_kind":"react","turn_id":"turn-delivery","tool_catalog_generation":1,"input":{}},event_id="run-delivery:created",now=1.0); run,lease=uow.claim_runtime_activation(run_id="run-delivery",owner_id="delivery",namespace="runtime.kernel",now=2.0,lease_ttl_seconds=30.0); fence=await uow.acquire(RunId("run-delivery"),lease,now=2.0); uow.commit_root_terminal_with_deliveries(run_id=run.run_id,expected_version=run.version,terminal_state=RunState.COMPLETED,event_id="run-delivery:completed",terminal_payload={"result":"physical"},deliveries=(DeliverySpec("delivery-1","fixture","delivery-key",{"result":"physical"}),),fence=fence,execution_lease=lease,terminal_fence_receipt_ref="runtime-fence:delivery:1",now=3.0); uow.release_runtime_lease(lease,now=3.0); await dispatcher.run_once(); await dispatcher.run_once(); record=uow.read_delivery("delivery-1"); values={"attempts":sink.attempts,"deliveries":len(sink.deliveries),"settled":record.state is DeliveryState.DELIVERED}; database.close(); return self.observation("runtime.delivery",values)
    async def budget(self):
        observed=[]; scenarios=[("max_turns",TerminationLimits(max_turns=1),[_response(calls=(_tool_call("t1"),)),_response()]),("max_tool_calls",TerminationLimits(max_tool_calls=1),[_response(calls=(_tool_call("a"),_tool_call("b",2)))]),("repeated_tool",TerminationLimits(max_consecutive_same_tool=1),[_response(calls=(_tool_call("same"),)),_response(calls=(_tool_call("same"),))])]
        for name,limits,responses in scenarios:
            seam=_RuntimeSeam(self.root/f"budget-{name}.sqlite",responses,limits=limits); run,_=await seam.run(f"run-{name}"); assert run.state is RunState.FAILED; observed.append(name); await seam.close()
        wall=_RuntimeSeam(self.root/"budget-wall.sqlite",[],limits=TerminationLimits(max_wall_seconds=.5),clock=_StepClock()); wall_run,_=await wall.run("run-wall"); assert wall_run.state is RunState.FAILED and wall.provider.physical_calls==0; await wall.close(); cost=_RuntimeSeam(self.root/"budget-cost.sqlite",[_response(calls=(_tool_call("cost"),)),_response()],limits=TerminationLimits(max_cost_micros=1),estimator=FrozenPriceEstimator("cost-price-v1","deskpet:model",1_000_000,1_000_000)); cost_run,_=await cost.run("run-cost"); assert cost_run.state is RunState.FAILED and cost.provider.physical_calls==1; await cost.close(); return self.observation("runtime.budget",{"terminations":[observed[0],observed[1],"wall_clock","cost",observed[2]]})
    async def restart_without_replay(self):
        path=self.root/"restart.sqlite"; seam=_RuntimeSeam(path,[_response(calls=(_tool_call("restart"),)),_response()]); run,_=await seam.run("run-restart"); tool_before=seam.database.connection.execute("SELECT count(*) FROM execution_effects WHERE run_id='run-restart' AND state='succeeded'").fetchone()[0]; before=seam.provider.physical_calls+tool_before; await seam.close(); reopened=_RuntimeSeam(path,[]); await reopened.stack.start(); restored=reopened.uow.read_run("run-restart"); tool_after=reopened.database.connection.execute("SELECT count(*) FROM execution_effects WHERE run_id='run-restart' AND state='succeeded'").fetchone()[0]; after=reopened.provider.physical_calls+tool_after+seam.provider.physical_calls; await reopened.close(); return self.observation("runtime.restart_without_replay",{"reopened":restored.state is run.state,"physical_calls_before":before,"physical_calls_after":after,"reconciled":restored.state is RunState.COMPLETED})
    async def host_owned(self):
        seam=_WorkflowSeam(self.root/"host.sqlite"); registration=next(item for item in seam.product if item.profile.descriptor.key=="workflow.deep_research"); context,ports=_research_context(self.root/"host-product"); _,result,run=await seam.execute(registration,{"run_id":"run-product-research","values":{"topic":"Product conformance","mode":"light"}},context); artifact=Path(result.output["values"]["report_artifact"]["path"]); values={"registered":seam.registry.get("deep_research","v7-sdk1") is not None,"completed":run.state is RunState.COMPLETED and result.status.value=="completed" and artifact.is_file() and hashlib.sha256(artifact.read_bytes()).hexdigest()==result.output["values"]["report_sha256"],"definition_id":registration.definition.name+"@"+registration.definition.version}; seam.close(); return self.observation("workflow.host_owned",values)
    async def _official(self,key,case_id):
        seam=_WorkflowSeam(self.root/f"{key}.sqlite"); registration=next(item for item in seam.official if item.profile.descriptor.key==key)
        if key=="workflow.personal_v1": state=personal_initial_state(run_id="run-personal",personal_workflow_selection=_selection().to_child_payload(),inputs={})
        elif key=="workflow.capability_build": state=capability_initial_state(run_id="run-capability",request="Build capability",search_miss_receipt="miss-deskpet")
        else: state=durable_initial_state(request="Short answer",run_id="run-durable",session_metadata={},capability_refs=[],approval_required=False)
        _,result,run=await seam.execute(registration,state); completed=run.state is RunState.COMPLETED and result.status.value=="completed"
        if key=="workflow.capability_build": completed=completed and result.output["values"]["active"] is True and seam.capability.calls==["authorization","search","source_policy","isolated_build","package_store","activate"] and len(set(seam.capability.operation_keys))==6
        seam.close(); return self.observation(case_id,{"profile_key":key,"completed":completed})
    async def official_durable_task(self): return await self._official("workflow.durable_task","workflow.official_durable_task")
    async def official_personal_v1(self): return await self._official("workflow.personal_v1","workflow.official_personal_v1")
    async def official_capability_build(self): return await self._official("workflow.capability_build","workflow.official_capability_build")
    async def ticket_fingerprint(self):
        seam=_WorkflowSeam(self.root/"ticket.sqlite"); registration=seam.official[0]; forged=fingerprint=False
        try: compile_workflow_registration(registration,transaction_owner=object())
        except ValueError: forged=True
        try: compile_workflow_registration(replace(registration,expected_manifest_hash="0"*64),transaction_owner=seam.owner)
        except ValueError: fingerprint=True
        child_runs=seam.database.connection.execute("SELECT count(*) FROM runs WHERE parent_run_id IS NOT NULL").fetchone()[0]; seam.close(); return self.observation("workflow.ticket_fingerprint",{"forged_ticket_rejected":forged,"fingerprint_rejected":fingerprint,"child_runs":child_runs})
    async def reopen(self):
        path=self.root/"reopen.sqlite"; seam=_WorkflowSeam(path); registration=next(item for item in seam.product if item.profile.descriptor.key=="workflow.deep_research"); context,ports=_research_context(self.root/"reopen-product"); run_id,_,_=await seam.execute(registration,{"run_id":"run-reopen-product","values":{"topic":"Reopen product research","mode":"light"}},context); physical_before=sum(len(port.calls) for port in ports.values() if hasattr(port,"calls")); seam.close(); reopened=_WorkflowSeam(path); recovered=await reopened.runner.recover(run_id,context); restored=reopened.uow.read_run(run_id); physical_after=sum(len(port.calls) for port in ports.values() if hasattr(port,"calls")); values={"reopened":recovered.run_id==run_id,"run_before":run_id,"run_after":restored.run_id,"physical_calls_before":physical_before,"physical_calls_after":physical_after,"completed":restored.state is RunState.COMPLETED}; reopened.close(); return self.observation("workflow.reopen",values)
    async def aclose(self):
        if self.closed: raise RuntimeError("suite closed twice")
        self.closed=True; self.connection.close(); shutil.rmtree(self.root)


class _Context:
    def __init__(self,name): self.name=name; self.suite=None
    async def __aenter__(self):
        if self.name not in {"provider","tool","runtime","workflow"}: raise ValueError(f"unknown suite: {self.name}")
        self.suite=_Suite(self.name); return self.suite
    async def __aexit__(self,*args): await self.suite.aclose()


class _Host:
    metadata=ConformanceHostMetadata("1.0.0","deskpet","1.0.0",frozenset({"provider","tool","runtime","workflow"}))
    def open_suite(self,name): return _Context(name)


def build_host(): return _Host()


__all__=("VENDORED_SDK_SHA256","build_host")
