#!/usr/bin/env python3
"""Loopback OpenAI-compatible data/control plane for Context OS E2E."""
from __future__ import annotations
import argparse, hashlib, json, os, re, threading, time, uuid, urllib.request
from dataclasses import dataclass,field
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

MARKER=re.compile(r"(?:CTX|RAW|DECISION|GOAL|PENDING|ARTIFACT)-[A-Z0-9_-]+")
SEQUENCE=re.compile(r"(?:sequence\s*=\s*|\"sequence\"\s*:\s*)(\d+)",re.IGNORECASE)
DETAIL_REF=re.compile(r"session_history_page_in\(segment_id=['\"]([^'\"]+)['\"]\)")
def canon(v): return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":"))
def token_count(v): return max(1,(len(canon(v).encode("utf-8"))+3)//4)
def marker_from(messages):
    # A page-in tool result can contain older CTX markers.  The latest user
    # message is the routing authority for the current turn.
    for m in reversed(messages or []):
        if m.get("role")!="user":continue
        content=m.get("content",""); text=content if isinstance(content,str) else canon(content)
        hit=MARKER.search(text)
        if hit:return hit.group(0)
    for m in reversed(messages or []):
        content=m.get("content",""); text=content if isinstance(content,str) else canon(content)
        hit=MARKER.search(text)
        if hit:return hit.group(0)
    return "CTX-NORMAL-UNMARKED"
def sequence_from(messages):
    for m in reversed(messages or []):
        content=m.get("content",""); text=content if isinstance(content,str) else canon(content)
        hit=SEQUENCE.search(text)
        if hit:return int(hit.group(1))
    return 1
OFF_TASK_TYPES={
    "CTX-OFF-CHAT":"chat",
    "CTX-OFF-RECALL":"recall",
    "CTX-OFF-TASK":"task",
    "CTX-OFF-CODE":"code",
    "CTX-OFF-WEB_SEARCH":"web_search",
    "CTX-OFF-PLAN":"plan",
    "CTX-OFF-EMOTION":"emotion",
    "CTX-OFF-COMMAND":"command",
}
OFF_PROBLEM_TYPES={
    "chat":"chitchat",
    "recall":"factual_qa",
    "task":"creation",
    "code":"debug",
    "web_search":"research",
    "plan":"multi_task",
    "emotion":"chitchat",
    "command":"factual_qa",
}
def off_auxiliary_response(marker,purpose,payload):
    """Return deterministic, parser-valid auxiliary output for E2E-CTX-11.

    The rollback oracle verifies the real outbound agent tool payload.  Its
    upstream classifiers must therefore receive valid fixture responses;
    ``AUX-ACK`` intentionally remains the default for every other scenario.
    """
    task_type=OFF_TASK_TYPES.get(marker)
    if task_type is None:return None
    if purpose=="capability_gate":return "PASS"
    if purpose!="classifier":return None
    if payload.get("response_format") is None:return task_type
    problem_type=OFF_PROBLEM_TYPES[task_type]
    complex_type=problem_type in {"creation","debug","research","multi_task"}
    return canon({
        "restated_intent":marker,
        "problem_type":problem_type,
        "ambiguity_score":0.0,
        "clarifying_questions":[],
        "needs_investigation":problem_type in {"factual_qa","debug","research"},
        "needs_decomposition":problem_type in {"creation","multi_task"},
        "contradiction":({
            "contradictions":[{"id":1,"desc":marker,"severity":1.0,"aspect":"execute"}],
            "principal":1,
            "principal_aspect":"execute",
            "attack_order":[1],
            "rationale":"deterministic E2E-CTX-11 fixture",
        } if complex_type else None),
    })
def fixture_tool_name(tools):
    remote="mcp_ctx_fixture_tool_001"
    names=[t.get("function",{}).get("name") for t in (tools or [])]
    if remote in names:return remote
    qualified=[name for name in names if isinstance(name,str) and name.endswith("_"+remote)]
    return qualified[0] if len(qualified)==1 else None
def exposed_tool_name(tools,name):
    names=[t.get("function",{}).get("name") for t in (tools or [])]
    if name in names:return name
    qualified=[item for item in names if isinstance(item,str) and item.endswith("_"+name)]
    return qualified[0] if len(qualified)==1 else None
def nested_value(value,key,depth=0):
    if depth>10:return None
    if isinstance(value,str):
        try:return nested_value(json.loads(value),key,depth+1)
        except (TypeError,ValueError):return None
    if isinstance(value,dict):
        # The registry's outer ``error`` can be null while a normally
        # returned handler result carries a domain error.  Keep descending
        # instead of letting that null shadow the inner failure.
        if key in value and value[key] is not None:return value[key]
        for nested in value.values():
            found=nested_value(nested,key,depth+1)
            if found is not None:return found
    if isinstance(value,list):
        for nested in value:
            found=nested_value(nested,key,depth+1)
            if found is not None:return found
    return None
def result_error(value):
    error=nested_value(value,"error")
    return str(error) if error else None
def catalog_identity():
    base=os.environ.get("DESKPET_CONTEXT_OS_E2E_DAEMON_URL","").rstrip("/")
    if not base:return None
    try:
        with urllib.request.urlopen(base+"/catalog-meta?tools=mcp_ctx_fixture_tool_001",timeout=.5) as response:
            value=json.load(response)
        item=(value.get("tools") or {}).get("mcp_ctx_fixture_tool_001") or {}
        return (int(item.get("fixture_epoch",-1)),str(item.get("fixture_spec_hash","")),str(item.get("fixture_spec_version","")))
    except Exception:return None
def wait_describe_barrier(request_id,capability_id,schema_hash):
    base=os.environ.get("DESKPET_CONTEXT_OS_E2E_DAEMON_URL","").rstrip("/")
    if not base:return
    body=canon({"request_id":request_id,"capability_id":capability_id,"schema_hash":schema_hash}).encode()
    request=urllib.request.Request(base+"/provider-describe-barrier",body,{"Content-Type":"application/json"})
    with urllib.request.urlopen(request,timeout=305) as response:json.load(response)
def tool_call(call_id,name,arguments):
    return {"id":call_id,"type":"function","function":{"name":name,"arguments":canon(arguments)}}
def product_scenario(marker):
    if marker.startswith(("CTX-GOAL-PENDING", "PENDING-A-0713")):
        return ("goal_task_create",{"title":"PENDING-A-0713 核对重启恢复","note":"[decision] DECISION-A-0713: Markdown format; keep pending"})
    if marker.startswith(("CTX-ARTIFACT-CREATE", "ARTIFACT-A-0713")):
        return ("file_write",{"path":"context-os-e2e/ARTIFACT-A-0713.md","content":"ARTIFACT-A-0713\nDECISION-A-0713\nPENDING-A-0713 remains pending\n","mode":"overwrite"})
    return None
def scenario_ack(marker,payload):
    if marker.startswith(("CTX-GOAL-PENDING", "PENDING-A-0713")):
        task_id=nested_value(payload,"task_id")
        if not task_id:return None
        return f"PENDING-A-0713 status=pending task_id={task_id}; DECISION-A-0713"
    if marker.startswith(("CTX-ARTIFACT-CREATE", "ARTIFACT-A-0713")):
        path=nested_value(payload,"path")
        if not isinstance(path,str) or not path.endswith("ARTIFACT-A-0713.md"):return None
        return f"ARTIFACT-A-0713 path={path}"
    invocation=invocation_id_from(payload)
    return f"TOOL-ACK:{marker}:{invocation}" if invocation else None
def named_tool(tools,name):
    names=[t.get("function",{}).get("name") for t in (tools or [])]
    return name if name in names else None
def first_detail_ref(messages):
    for message in messages or []:
        content=message.get("content",""); text=content if isinstance(content,str) else canon(content)
        hit=DETAIL_REF.search(text)
        if hit:return hit.group(1)
    return None
def invocation_id_from(value,depth=0):
    if depth>8:return None
    if isinstance(value,str):
        try:return invocation_id_from(json.loads(value),depth+1)
        except (TypeError,ValueError):return None
    if isinstance(value,dict):
        invocation=value.get("invocation_id")
        if isinstance(invocation,str) and invocation:return invocation
        for nested in value.values():
            found=invocation_id_from(nested,depth+1)
            if found:return found
        return None
    if isinstance(value,list):
        for item in value:
            found=invocation_id_from(item,depth+1)
            if found:return found
    return None
def value_shape(value,depth=0):
    if depth>5:return "depth_limit"
    if isinstance(value,str):
        try:return {"json_string":value_shape(json.loads(value),depth+1),"chars":len(value)}
        except (TypeError,ValueError):return {"string_chars":len(value)}
    if isinstance(value,dict):return {"object":{str(k):value_shape(v,depth+1) for k,v in value.items()}}
    if isinstance(value,list):return {"list_len":len(value),"items":[value_shape(v,depth+1) for v in value[:3]]}
    return type(value).__name__
@dataclass
class State:
    log:Path; lock:threading.RLock=field(default_factory=threading.RLock); models:dict=field(default_factory=lambda:{"ctx-primary":8192,"ctx-fallback":8192})
    faults:dict=field(default_factory=dict); fallback:dict|None=None; force:dict|None=None; paused:set=field(default_factory=set); pause_tools:dict[str,str]=field(default_factory=dict); gate:threading.Event=field(default_factory=threading.Event); scenarios:dict=field(default_factory=dict); completed:dict=field(default_factory=dict)
    def __post_init__(self):self.gate.set()
    def event(self,event,**kw):
        row={"ts":time.time(),"event":event,**kw};self.log.parent.mkdir(parents=True,exist_ok=True)
        with self.log.open("a",encoding="utf-8") as f:f.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+"\n")
STATE:State
class H(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def body(self):
        raw=self.rfile.read(int(self.headers.get("Content-Length",0)));return json.loads(raw or b"{}")
    def sendj(self,code,v):
        raw=canon(v).encode();self.send_response(code);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        p=urlparse(self.path).path
        if p=="/v1/models":return self.sendj(200,{"object":"list","data":[{"id":k,"object":"model","context_window":v} for k,v in STATE.models.items()]})
        if p=="/__control/state":
            with STATE.lock:return self.sendj(200,{"ok":True,"models":STATE.models,"faults":STATE.faults,"fallback":STATE.fallback,"force_finish":STATE.force,"paused":sorted(STATE.paused),"pause_tools":dict(STATE.pause_tools),"scenario_count":len(STATE.scenarios)})
        self.sendj(404,{"error":{"message":"not_found","type":"fixture_error"}})
    def do_DELETE(self):
        p=urlparse(self.path).path
        if p.startswith("/__control/model-fault/"):
            with STATE.lock:STATE.faults.pop(p.rsplit("/",1)[1],None)
            return self.sendj(200,{"ok":True})
        self.sendj(404,{"error":"not_found"})
    def do_POST(self):
        p=urlparse(self.path).path;b=self.body()
        if p=="/__control/stop":
            self.sendj(200,{"ok":True})
            threading.Thread(target=self.server.shutdown,daemon=True).start()
            return
        if p.startswith("/__control/"):return self.control(p.removeprefix("/__control/"),b)
        if p!="/v1/chat/completions":return self.sendj(404,{"error":{"message":"not_found","type":"fixture_error"}})
        if self.headers.get("Authorization")!="Bearer ctx-e2e-local-key":return self.sendj(401,{"error":{"message":"invalid_api_key","type":"authentication_error"}})
        return self.chat(b)
    def control(self,c,b):
        with STATE.lock:
            if c=="pause-before-attempt":
                marker=str(b.get("marker","*") or "*");STATE.paused.add(marker)
                required_tool=str(b.get("tool","") or "").strip()
                if required_tool:STATE.pause_tools[marker]=required_tool
                else:STATE.pause_tools.pop(marker,None)
                STATE.gate.clear()
            elif c=="resume-attempt":STATE.paused.clear();STATE.pause_tools.clear();STATE.gate.set()
            elif c=="fallback":STATE.fallback=dict(b)
            elif c=="force-finish":STATE.force={**b,"round":0}
            elif c=="catalog":STATE.models[str(b["model"])]=int(b.get("window",8192))
            elif c=="model-fault":STATE.faults[str(b["model"])]=str(b.get("error","fixture_model_fault"))
            else:return self.sendj(404,{"error":"unknown_control"})
            STATE.event("control_"+c,marker=b.get("marker"),model=b.get("model"),required_tool=b.get("tool"))
        self.sendj(200,{"ok":True})
    def chat(self,b):
        model=str(b.get("model",""));messages=b.get("messages") or [];tools=b.get("tools");stream=bool(b.get("stream"));marker=marker_from(messages)
        purpose=self.headers.get("X-DeskPet-Purpose","agent_response");rid=self.headers.get("X-DeskPet-Request-Id",str(uuid.uuid4()));aid=self.headers.get("X-DeskPet-Attempt-Id",str(uuid.uuid4()))
        digest=hashlib.sha256(canon(b).encode()).hexdigest()
        if model not in STATE.models:return self.sendj(404,{"error":{"message":"unknown_model","type":"invalid_request_error"}})
        catalog_before=catalog_identity()
        with STATE.lock:
            # ``pause_before_attempt`` models the final provider-send barrier
            # for the agent request.  Auxiliary classifier/planner/memory
            # calls can carry the same marker but must not consume the fault
            # or invalidate the catalog before the prepared agent payload is
            # assembled.
            armed_marker=marker if marker in STATE.paused else ("*" if "*" in STATE.paused else None)
            required_tool=STATE.pause_tools.get(armed_marker or "","")
            tool_names=[t.get("function",{}).get("name") for t in (tools or [])]
            paused=(
                purpose == "agent_response"
                and armed_marker is not None
                and (
                    not required_tool
                    or any(
                        name == required_tool
                        or str(name or "").endswith("_" + required_tool)
                        for name in tool_names
                    )
                )
            )
            STATE.event("attempt_received",purpose=purpose,request_id=rid,attempt_id=aid,model=model,marker=marker,body_sha256=digest,tools_count=len(tools or []),tool_names=[t.get("function",{}).get("name") for t in (tools or [])],stream=stream)
            if paused:STATE.event("attempt_paused",request_id=rid,attempt_id=aid,marker=marker,required_tool=required_tool or None)
        if paused:
            STATE.gate.wait(300)
            catalog_after=catalog_identity()
            if catalog_before is not None and catalog_after != catalog_before:
                with STATE.lock:
                    STATE.scenarios.pop((rid,marker),None)
                    STATE.event("scenario_stale",request_id=rid,attempt_id=aid,marker=marker,stale_phase="pre_provider",catalog_before=catalog_before,catalog_after=catalog_after)
                return self.sendj(409,{"error":{"message":"tool_catalog_stale","type":"scenario_error","code":"tool_catalog_stale"}})
        with STATE.lock:
            fault=STATE.faults.get(model)
            if fault:return self.sendj(503,{"error":{"message":fault,"type":"fixture_model_error","code":fault}})
            if STATE.fallback and marker==STATE.fallback.get("marker") and model==STATE.fallback.get("from","ctx-primary"):
                STATE.event("attempt_error",request_id=rid,attempt_id=aid,status=503,marker=marker)
                return self.sendj(503,{"error":{"message":"fixture_primary_unavailable","type":"fixture_error","code":"fixture_primary_unavailable"}})
        content=None;tool_calls=None;finish="stop"
        # A request carries the complete conversation.  Tool results from an
        # earlier turn must never advance the new turn's scenario.  Only the
        # messages after the latest user message belong to the current tool
        # loop (and remain present for VerifyGate rebounds).
        latest_user_index=max((i for i,m in enumerate(messages) if m.get("role")=="user"),default=-1)
        current_turn=messages[latest_user_index+1:]
        tool_result=next((m for m in reversed(current_turn) if m.get("role")=="tool"),None)
        if purpose in {"context_compression", "compressor"}:
            if tools is not None:return self.sendj(409,{"error":{"message":"compressor_tools_must_be_null","type":"scenario_error"}})
            content=canon({"objective":marker,"decisions":[],"completed":[],"pending":[],"artifacts":[],"blockers":[],"source_ranges":[],"marker_hash":hashlib.sha256(marker.encode()).hexdigest()})
        elif purpose != "agent_response":
            # Auxiliary calls can carry the same user marker but must not
            # consume or mutate the agent_response scenario state.
            content=off_auxiliary_response(marker,purpose,b) or f"AUX-ACK:{purpose}"
        elif marker.startswith("CTX-RAW-RECALL"):
            seen=[x for x in ("RAW-01","RAW-06","RAW-12") if any(x in canon(m) for m in messages if m.get("role") in ("user","assistant"))]
            if len(seen)!=3:return self.sendj(409,{"error":{"message":"raw_markers_missing","type":"scenario_error"}})
            content="/".join(seen)
        elif marker.startswith("CTX-PAGEIN-RECALL"):
            key=(rid,marker);expected=STATE.scenarios.get(key,{}).get("tool_call_id")
            if tool_result is not None:
                if not expected or tool_result.get("tool_call_id")!=expected:
                    STATE.event("scenario_error",request_id=rid,marker=marker,reason="page_in_tool_result_causality_mismatch")
                    return self.sendj(409,{"error":{"message":"page_in_tool_result_causality_mismatch","type":"scenario_error"}})
                value=canon(tool_result.get("content",""))
                if "CTX-CALL-02" not in value:
                    return self.sendj(409,{"error":{"message":"page_in_marker_missing","type":"scenario_error"}})
                STATE.scenarios.pop(key,None)
                content="CTX-CALL-02 sequence=2 result=TOOL-ACK; called session_history_page_in"
            else:
                target=named_tool(tools,"session_history_page_in");segment_id=first_detail_ref(messages)
                if target is None or not segment_id:
                    return self.sendj(409,{"error":{"message":"page_in_reference_missing","type":"scenario_error"}})
                tcid=f"page-in-{rid[-8:]}-1"
                # The fixture stores four messages per tool turn and causal
                # grouping yields user / assistant+tool / final.  CTX-CALL-02
                # therefore begins at group cursor 3.  Start there so the
                # bounded host tool-result preview cannot truncate the target
                # marker behind CTX-CALL-01.
                tool_calls=[{"id":tcid,"type":"function","function":{"name":target,"arguments":canon({"segment_id":segment_id,"cursor":3})}}]
                STATE.scenarios[key]={"state":"P1","tool_call_id":tcid,"segment_id":segment_id};finish="tool_calls"
        elif marker.startswith(("CTX-DISCLOSE-", "CTX-GOAL-PENDING", "CTX-ARTIFACT-CREATE", "PENDING-A-0713", "ARTIFACT-A-0713")):
            key=(rid,marker);scenario=STATE.scenarios.get(key);done=STATE.completed.get(key)
            product=product_scenario(marker);target_name=product[0] if product else "mcp_ctx_fixture_tool_001"
            STATE.event(
                "disclosure_observed",
                request_id=rid,
                attempt_id=aid,
                phase=(scenario or {}).get("phase"),
                expected_tool_call_id=(scenario or {}).get("tool_call_id"),
                actual_tool_call_id=(tool_result or {}).get("tool_call_id"),
                result_error=result_error((tool_result or {}).get("content", "")),
                result_status=nested_value((tool_result or {}).get("content", ""), "status"),
                result_shape=value_shape((tool_result or {}).get("content", "")),
            )
            if done is not None and tool_result is not None:
                if tool_result.get("tool_call_id")!=done.get("tool_call_id"):
                    return self.sendj(409,{"error":{"message":"completed_tool_result_causality_mismatch","type":"scenario_error"}})
                content=str(done.get("result") or "")
            elif scenario is None:
                search=exposed_tool_name(tools,"tool_search")
                if search is None:return self.sendj(409,{"error":{"message":"tool_search_missing","type":"scenario_error"}})
                tcid=f"search-{rid[-8:]}-1"
                tool_calls=[tool_call(tcid,search,{"query":target_name,"limit":10,"cursor":0})]
                STATE.scenarios[key]={"phase":"await_search","tool_call_id":tcid,"target_name":target_name};finish="tool_calls"
            elif tool_result is None or tool_result.get("tool_call_id") != scenario.get("tool_call_id"):
                STATE.event("scenario_error",request_id=rid,marker=marker,reason="disclosure_tool_result_causality_mismatch",phase=scenario.get("phase"))
                return self.sendj(409,{"error":{"message":"tool_result_causality_mismatch","type":"scenario_error"}})
            else:
                phase=str(scenario.get("phase",""));payload=tool_result.get("content","");error=result_error(payload)
                if error:
                    STATE.scenarios.pop(key,None)
                    STATE.event("scenario_stale",request_id=rid,attempt_id=aid,marker=marker,stale_phase=phase,error=error)
                    content="TOOL-CATALOG-STALE"
                elif phase=="await_search":
                    matches=nested_value(payload,"matches")
                    capability=(matches[0].get("capability_id") if isinstance(matches,list) and matches and isinstance(matches[0],dict) else None)
                    describe=exposed_tool_name(tools,"tool_describe")
                    if not capability or describe is None:return self.sendj(409,{"error":{"message":"disclosure_search_result_invalid","type":"scenario_error"}})
                    tcid=f"describe-{rid[-8:]}-2";tool_calls=[tool_call(tcid,describe,{"capability_id":capability})]
                    STATE.scenarios[key]={"phase":"await_describe","tool_call_id":tcid,"capability_id":capability,"target_name":scenario.get("target_name",target_name)};finish="tool_calls"
                elif phase=="await_describe":
                    capability=nested_value(payload,"capability_id");schema_hash=nested_value(payload,"schema_hash");nonce=nested_value(payload,"describe_nonce");activate=exposed_tool_name(tools,"tool_activate")
                    if not all((capability,schema_hash,nonce,activate)):return self.sendj(409,{"error":{"message":"disclosure_describe_result_invalid","type":"scenario_error"}})
                    wait_describe_barrier(rid,capability,schema_hash)
                    tcid=f"activate-{rid[-8:]}-3";tool_calls=[tool_call(tcid,activate,{"capability_id":capability,"schema_hash":schema_hash,"describe_nonce":nonce})]
                    STATE.scenarios[key]={"phase":"await_activate","tool_call_id":tcid,"capability_id":capability,"schema_hash":schema_hash,"target_name":scenario.get("target_name",target_name)};finish="tool_calls"
                elif phase=="await_activate":
                    status=nested_value(payload,"status");target=exposed_tool_name(tools,str(scenario.get("target_name",target_name)))
                    # AgentLoop consumes the host-only activation proposal and
                    # exposes only the committed state to the next model turn.
                    # Seeing ``activation_proposed`` here would leak the
                    # internal control envelope and race the scope commit.
                    if status!="activated" or target is None:return self.sendj(409,{"error":{"message":"disclosure_activation_result_invalid","type":"scenario_error"}})
                    tcid=f"remote-{rid[-8:]}-4";sequence=sequence_from(messages);args=(product[1] if product else {"marker":marker,"sequence":sequence});tool_calls=[tool_call(tcid,target,args)]
                    STATE.scenarios[key]={"phase":"await_remote","tool_call_id":tcid,"sequence":sequence,"target_name":scenario.get("target_name",target_name)};finish="tool_calls"
                elif phase=="await_remote":
                    ack=scenario_ack(marker,payload)
                    if not ack:return self.sendj(409,{"error":{"message":"scenario_result_invalid","type":"scenario_error"}})
                    STATE.scenarios.pop(key,None);STATE.completed[key]={"tool_call_id":scenario["tool_call_id"],"result":ack}
                    content=ack
                else:return self.sendj(409,{"error":{"message":"disclosure_state_invalid","type":"scenario_error"}})
        elif marker.startswith(("CTX-TASK-READBACK","CTX-RESTART-READBACK")):
            rendered=canon(messages);required=("GOAL-A-0713","DECISION-A-0713","PENDING-A-0713","ARTIFACT-A-0713")
            missing=[item for item in required if item not in rendered]
            if missing:return self.sendj(409,{"error":{"message":"task_snapshot_markers_missing","type":"scenario_error","missing":missing}})
            content="GOAL-A-0713; DECISION-A-0713; PENDING-A-0713 status=pending; ARTIFACT-A-0713 path=context-os-e2e/ARTIFACT-A-0713.md"
        elif tool_result and marker.startswith("CTX-CALL-"):
            key=(rid,marker); expected=STATE.scenarios.get(key,{}).get("tool_call_id"); done=STATE.completed.get(key)
            if done is not None:
                if tool_result.get("tool_call_id") != done.get("tool_call_id"):
                    STATE.event("scenario_error",request_id=rid,marker=marker,reason="completed_tool_result_causality_mismatch",id_matches=False)
                    return self.sendj(409,{"error":{"message":"tool_result_causality_mismatch","type":"scenario_error"}})
                content=f"TOOL-ACK:{marker}:{done['invocation_id']}"
            elif not expected or tool_result.get("tool_call_id") != expected:
                STATE.event("scenario_error",request_id=rid,marker=marker,reason="tool_result_causality_mismatch",has_expected=bool(expected),id_matches=bool(expected and tool_result.get("tool_call_id")==expected))
                return self.sendj(409,{"error":{"message":"tool_result_causality_mismatch","type":"scenario_error"}})
            else:
                inv=invocation_id_from(tool_result.get("content",""))
                if inv is None:
                    STATE.event("scenario_error",request_id=rid,marker=marker,reason="invocation_id_missing",tool_result_shape=value_shape(tool_result.get("content","")))
                    return self.sendj(409,{"error":{"message":"invocation_id_missing","type":"scenario_error"}})
                STATE.scenarios.pop(key,None);STATE.completed[key]={"tool_call_id":expected,"invocation_id":inv}
                content=f"TOOL-ACK:{marker}:{inv}"
        elif marker.startswith("CTX-CALL-"):
            target=fixture_tool_name(tools)
            if target is None:return self.sendj(409,{"error":{"message":"fixture_tool_missing","type":"scenario_error"}})
            sequence=sequence_from(messages);tcid=f"call-{rid[-8:]}-1";args={"marker":marker,"sequence":sequence};tool_calls=[{"id":tcid,"type":"function","function":{"name":target,"arguments":canon(args)}}];STATE.scenarios[(rid,marker)]={"state":"S1","tool_call_id":tcid,"sequence":sequence};finish="tool_calls"
        elif STATE.force and marker==STATE.force.get("marker"):
            with STATE.lock:
                n=int(STATE.force["round"]);limit=int(STATE.force.get("tool_rounds",3))
                if n<limit:
                    target=fixture_tool_name(tools)
                    if target is None:return self.sendj(409,{"error":{"message":"force_finish_tools_missing","type":"scenario_error"}})
                    n+=1;STATE.force["round"]=n;tcid=f"force-{rid[-8:]}-{n}";tool_calls=[{"id":tcid,"type":"function","function":{"name":target,"arguments":canon({"marker":marker,"sequence":900+n})}}];finish="tool_calls"
                else:
                    if tools is not None:return self.sendj(409,{"error":{"message":"force_finish_expected_tools_null","type":"scenario_error"}})
                    content="FORCE-FINISH-ACK";STATE.force=None
        else:
            content=f"ACK:{marker}"
            if STATE.fallback and marker==STATE.fallback.get("marker") and model==STATE.fallback.get("to","ctx-fallback"):STATE.fallback=None
        msg={"role":"assistant","content":content}
        if tool_calls is not None:msg["tool_calls"]=tool_calls
        completion=token_count(msg);prompt=token_count({"messages":messages,"tools":tools,"tool_choice":b.get("tool_choice")})
        response={"id":"chatcmpl-"+aid[-12:],"object":"chat.completion","created":int(time.time()),"model":model,"choices":[{"index":0,"message":msg,"finish_reason":finish}],"usage":{"prompt_tokens":prompt,"completion_tokens":completion,"total_tokens":prompt+completion}}
        with STATE.lock:STATE.event("attempt_terminal",request_id=rid,attempt_id=aid,purpose=purpose,model=model,marker=marker,finish_reason=finish,prompt_tokens=prompt,body_sha256=digest,observed_tools_none=tools is None)
        if not stream:return self.sendj(200,response)
        self.send_response(200);self.send_header("Content-Type","text/event-stream");self.end_headers()
        delta={"role":"assistant"};
        if content is not None:delta["content"]=content
        if tool_calls is not None:delta["tool_calls"]=tool_calls
        chunk={"id":response["id"],"object":"chat.completion.chunk","created":response["created"],"model":model,"choices":[{"index":0,"delta":delta,"finish_reason":None}]}
        for value in (chunk,{**chunk,"choices":[{"index":0,"delta":{},"finish_reason":finish}]}):self.wfile.write(("data: "+canon(value)+"\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n");self.wfile.flush()
def main():
    p=argparse.ArgumentParser();p.add_argument("--host",default="127.0.0.1");p.add_argument("--port",type=int,default=18992);p.add_argument("--log",default="plans/2026-07-13-context-os-v1/test-results/provider-seam.jsonl");a=p.parse_args()
    if os.environ.get("DESKPET_DEV_MODE")!="1" or a.host not in ("127.0.0.1","localhost","::1"):raise SystemExit("provider fixture requires dev mode and loopback host")
    global STATE;STATE=State(Path(a.log).resolve());STATE.event("provider_start",pid=os.getpid());ThreadingHTTPServer((a.host,a.port),H).serve_forever()
if __name__=="__main__":main()
