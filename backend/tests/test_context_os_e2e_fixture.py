from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "e2e"


def _free_port():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close(); return port


def _request(base, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


def _wait(base, path="/health"):
    for _ in range(100):
        try: return _request(base, path)
        except Exception: time.sleep(.03)
    raise AssertionError(f"fixture unavailable: {base}{path}")


def _stop_provider(base, process):
    try:
        _request(base, "/__control/stop", {})
        process.wait(timeout=5)
    except Exception:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)


@pytest.fixture
def daemon(tmp_path):
    port = _free_port(); base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "DESKPET_DEV_MODE": "1"}
    p = subprocess.Popen([sys.executable, str(SCRIPTS / "context_os_mcp_daemon.py"), "--port", str(port), "--log", str(tmp_path / "fixture.jsonl")], env=env)
    _wait(base)
    yield base, p, tmp_path
    if p.poll() is None:
        try: _request(base, "/stop", {})
        except Exception: p.terminate()
        p.wait(timeout=5)


def test_daemon_catalog_epoch_visibility_and_hot_replace(daemon):
    base, _, _ = daemon
    first = _request(base, "/catalog-meta?tools=mcp_ctx_fixture_tool_001")
    assert len(first["tools"]) == 1
    assert len(first["tools"]["mcp_ctx_fixture_tool_001"]["fixture_spec_hash"]) == 64
    assert _request(base, "/visibility?session_id=A&tool=mcp_ctx_fixture_tool_001")["allowed"] is False
    _request(base, "/allow-session", {"session_id": "A"})
    assert _request(base, "/visibility?session_id=A&tool=mcp_ctx_fixture_tool_001")["allowed"] is True
    changed = _request(base, "/hot-replace", {"tool": "mcp_ctx_fixture_tool_001", "version": 2})
    second = _request(base, "/catalog-meta?tools=mcp_ctx_fixture_tool_001")
    assert changed["fixture_epoch"] == first["fixture_epoch"] + 1
    assert second["tools"]["mcp_ctx_fixture_tool_001"]["fixture_spec_version"] == 2
    assert second["tools"]["mcp_ctx_fixture_tool_001"]["fixture_spec_hash"] != first["tools"]["mcp_ctx_fixture_tool_001"]["fixture_spec_hash"]


def test_single_stateless_proxy_lists_500_and_calls_handler(daemon):
    base, _, _ = daemon
    env = {**os.environ, "DESKPET_DEV_MODE": "1", "DESKPET_CONTEXT_OS_E2E_DAEMON_URL": base}
    proxy = subprocess.Popen([sys.executable, str(SCRIPTS / "context_os_mcp_fixture.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
    def rpc(mid, method, params=None):
        proxy.stdin.write(json.dumps({"jsonrpc":"2.0","id":mid,"method":method,"params":params or {}}) + "\n"); proxy.stdin.flush()
        return json.loads(proxy.stdout.readline())
    assert rpc(1, "initialize")["result"]["serverInfo"]["name"] == "context-os-e2e"
    listed = rpc(2, "tools/list")["result"]["tools"]
    assert len(listed) == 500
    assert set(listed[0]["annotations"]) == {"fixture_epoch", "fixture_spec_hash", "fixture_spec_version"}
    called = rpc(3, "tools/call", {"name":"mcp_ctx_fixture_tool_001","arguments":{"marker":"CTX-CALL-TEST","sequence":7}})["result"]["structuredContent"]
    assert called["marker"] == "CTX-CALL-TEST" and called["sequence"] == 7 and len(called["sha256"]) == 64
    health = _request(base, "/health"); assert len(health["registered_proxy_pids"]) == 1
    proxy.stdin.close(); proxy.wait(timeout=5)
    for _ in range(50):
        if not _request(base, "/health")["registered_proxy_pids"]: break
        time.sleep(.02)
    assert _request(base, "/health")["registered_proxy_pids"] == []
    assert _request(base, "/counters")["counters"] == {"list":1,"call":1,"handler":1}


def test_provider_tool_and_fallback_state_machine(tmp_path):
    port=_free_port(); base=f"http://127.0.0.1:{port}"; env={**os.environ,"DESKPET_DEV_MODE":"1"}
    p=subprocess.Popen([sys.executable,str(SCRIPTS/"context_os_provider_fixture.py"),"--port",str(port),"--log",str(tmp_path/"provider.jsonl")],env=env)
    _wait(base,"/v1/models")
    headers={"Content-Type":"application/json","Authorization":"Bearer ctx-e2e-local-key","X-DeskPet-Purpose":"agent_response","X-DeskPet-Request-Id":"req-1","X-DeskPet-Attempt-Id":"att-1"}
    def chat(body):
        req=urllib.request.Request(base+"/v1/chat/completions",json.dumps(body).encode(),headers)
        with urllib.request.urlopen(req,timeout=5) as r:return json.load(r)
    tools=[{"type":"function","function":{"name":"mcp_ctx_fixture_tool_001","parameters":{"type":"object"}}}]
    first=chat({"model":"ctx-primary","messages":[{"role":"user","content":"CTX-CALL-TEST sequence=7"}],"tools":tools})
    call=first["choices"][0]["message"]["tool_calls"][0]
    assert json.loads(call["function"]["arguments"])["sequence"] == 7
    final=chat({"model":"ctx-primary","messages":[{"role":"user","content":"CTX-CALL-TEST"},{"role":"assistant","tool_calls":[call]},{"role":"tool","tool_call_id":call["id"],"content":json.dumps({"marker":"CTX-CALL-TEST","invocation_id":"inv-1"})}],"tools":tools})
    assert final["choices"][0]["message"]["content"] == "TOOL-ACK:CTX-CALL-TEST:inv-1"
    rebound=chat({"model":"ctx-primary","messages":[{"role":"user","content":"CTX-CALL-TEST"},{"role":"assistant","tool_calls":[call]},{"role":"tool","tool_call_id":call["id"],"content":json.dumps({"marker":"CTX-CALL-TEST","invocation_id":"inv-1"})},{"role":"assistant","content":"TOOL-ACK:CTX-CALL-TEST:inv-1"},{"role":"system","content":"verify again"}],"tools":tools})
    assert rebound["choices"][0]["message"]["content"] == "TOOL-ACK:CTX-CALL-TEST:inv-1"
    second=chat({"model":"ctx-primary","messages":[{"role":"user","content":"CTX-CALL-TEST"},{"role":"assistant","tool_calls":[call]},{"role":"tool","tool_call_id":call["id"],"content":json.dumps({"marker":"CTX-CALL-TEST","invocation_id":"inv-1"})},{"role":"assistant","content":"TOOL-ACK:CTX-CALL-TEST:inv-1"},{"role":"user","content":"CTX-CALL-SECOND sequence=2"}],"tools":tools})
    second_call=second["choices"][0]["message"]["tool_calls"][0]
    assert json.loads(second_call["function"]["arguments"])["sequence"] == 2
    second_final=chat({"model":"ctx-primary","messages":[{"role":"user","content":"CTX-CALL-TEST"},{"role":"assistant","tool_calls":[call]},{"role":"tool","tool_call_id":call["id"],"content":json.dumps({"marker":"CTX-CALL-TEST","invocation_id":"inv-1"})},{"role":"assistant","content":"TOOL-ACK:CTX-CALL-TEST:inv-1"},{"role":"user","content":"CTX-CALL-SECOND sequence=2"},{"role":"assistant","tool_calls":[second_call]},{"role":"tool","tool_call_id":second_call["id"],"content":json.dumps({"marker":"CTX-CALL-SECOND","invocation_id":"inv-2"})}],"tools":tools})
    assert second_final["choices"][0]["message"]["content"] == "TOOL-ACK:CTX-CALL-SECOND:inv-2"
    nested_first=chat({"model":"ctx-primary","messages":[{"role":"user","content":"CTX-CALL-NESTED"}],"tools":tools})
    nested_call=nested_first["choices"][0]["message"]["tool_calls"][0]
    nested_content={"ok":True,"result":json.dumps({"isError":False,"structured_content":None,"content":[{"type":"text","text":json.dumps({"result":{"invocation_id":"inv-nested"}})}]})}
    nested_final=chat({"model":"ctx-primary","messages":[{"role":"user","content":"CTX-CALL-NESTED"},{"role":"assistant","tool_calls":[nested_call]},{"role":"tool","tool_call_id":nested_call["id"],"content":json.dumps(nested_content)}],"tools":tools})
    assert nested_final["choices"][0]["message"]["content"] == "TOOL-ACK:CTX-CALL-NESTED:inv-nested"
    _request(base,"/__control/fallback",{"marker":"CTX-FALLBACK-1","from":"ctx-primary","to":"ctx-fallback"})
    with pytest.raises(Exception): chat({"model":"ctx-primary","messages":[{"role":"user","content":"CTX-FALLBACK-1"}]})
    headers["X-DeskPet-Attempt-Id"]="att-2"
    assert chat({"model":"ctx-fallback","messages":[{"role":"user","content":"CTX-FALLBACK-1"}]})["choices"][0]["message"]["content"]=="ACK:CTX-FALLBACK-1"
    assert _request(base,"/__control/state")["fallback"] is None
    headers["X-DeskPet-Purpose"]="classifier"
    headers["X-DeskPet-Request-Id"]="aux-1"
    headers["X-DeskPet-Attempt-Id"]="aux-att-1"
    aux=chat({"model":"ctx-primary","messages":[{"role":"user","content":"CTX-CALL-TEST"}],"tools":[]})
    assert aux["choices"][0]["message"]["content"] == "AUX-ACK:classifier"
    off_classifier=chat({"model":"ctx-primary","messages":[{"role":"user","content":"CTX-OFF-RECALL"}],"tools":[]})
    assert off_classifier["choices"][0]["message"]["content"] == "recall"
    off_structured=chat({"model":"ctx-primary","messages":[{"role":"user","content":"CTX-OFF-PLAN"}],"response_format":{"type":"json_schema"}})
    off_card=json.loads(off_structured["choices"][0]["message"]["content"])
    assert off_card["problem_type"] == "multi_task"
    assert off_card["needs_decomposition"] is True
    assert off_card["contradiction"]["principal"] == 1
    headers["X-DeskPet-Purpose"]="capability_gate"
    off_gate=chat({"model":"ctx-primary","messages":[{"role":"user","content":"CTX-OFF-COMMAND"}]})
    assert off_gate["choices"][0]["message"]["content"] == "PASS"
    assert _request(base,"/__control/state")["scenario_count"] == 0
    headers["X-DeskPet-Purpose"]="compressor"
    headers["X-DeskPet-Request-Id"]="compress-1"
    headers["X-DeskPet-Attempt-Id"]="compress-att-1"
    compressed=chat({"model":"ctx-primary","messages":[{"role":"user","content":"CTX-COMPACT-TEST"}]})
    summary=json.loads(compressed["choices"][0]["message"]["content"])
    assert summary["objective"] == "CTX-COMPACT-TEST"
    assert summary["marker_hash"]
    headers["X-DeskPet-Purpose"]="agent_response"
    headers["X-DeskPet-Request-Id"]="page-in-1"
    headers["X-DeskPet-Attempt-Id"]="page-in-att-1"
    page_in_tools=[{"type":"function","function":{"name":"session_history_page_in","parameters":{"type":"object"}}}]
    page_in_first=chat({"model":"ctx-primary","messages":[
        {"role":"assistant","content":"[Session history summary]\nold\n[detail_ref: session_history_page_in(segment_id='ctxseg-old')]"},
        {"role":"user","content":"CTX-PAGEIN-RECALL: return CTX-CALL-02"},
    ],"tools":page_in_tools})
    page_in_call=page_in_first["choices"][0]["message"]["tool_calls"][0]
    assert page_in_call["function"]["name"] == "session_history_page_in"
    page_in_args=json.loads(page_in_call["function"]["arguments"])
    assert page_in_args["segment_id"] == "ctxseg-old"
    assert page_in_args["cursor"] == 3
    page_in_final=chat({"model":"ctx-primary","messages":[
        {"role":"assistant","content":"[Session history summary]\nold\n[detail_ref: session_history_page_in(segment_id='ctxseg-old')]"},
        {"role":"user","content":"CTX-PAGEIN-RECALL: return CTX-CALL-02"},
        {"role":"assistant","tool_calls":[page_in_call]},
        {"role":"tool","tool_call_id":page_in_call["id"],"content":json.dumps({"ok":True,"messages":[{"content":"CTX-CALL-02 sequence=2"}]})},
    ],"tools":page_in_tools})
    assert "CTX-CALL-02 sequence=2" in page_in_final["choices"][0]["message"]["content"]
    _stop_provider(base,p)


def test_provider_progressive_disclosure_and_describe_stale_recovery(daemon,tmp_path):
    daemon_base,_,daemon_tmp=daemon
    port=_free_port();base=f"http://127.0.0.1:{port}";env={**os.environ,"DESKPET_DEV_MODE":"1","DESKPET_CONTEXT_OS_E2E_DAEMON_URL":daemon_base}
    p=subprocess.Popen([sys.executable,str(SCRIPTS/"context_os_provider_fixture.py"),"--port",str(port),"--log",str(tmp_path/"provider-disclose.jsonl")],env=env)
    _wait(base,"/v1/models")
    headers={"Content-Type":"application/json","Authorization":"Bearer ctx-e2e-local-key","X-DeskPet-Purpose":"agent_response","X-DeskPet-Request-Id":"req-disclose","X-DeskPet-Attempt-Id":"att-1"}
    bridge=[{"type":"function","function":{"name":name,"parameters":{"type":"object"}}} for name in ("tool_search","tool_describe","tool_activate")]
    remote={"type":"function","function":{"name":"mcp_ctx_fixture_tool_001","parameters":{"type":"object"}}}
    messages=[{"role":"user","content":"CTX-DISCLOSE-A sequence=301"}]
    def chat(messages,tools):
        headers["X-DeskPet-Attempt-Id"]=f"att-{len(messages)}"
        req=urllib.request.Request(base+"/v1/chat/completions",json.dumps({"model":"ctx-primary","messages":messages,"tools":tools}).encode(),headers)
        with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)
    try:
        search=chat(messages,bridge)["choices"][0]["message"]["tool_calls"][0]
        assert search["function"]["name"]=="tool_search"
        messages += [{"role":"assistant","tool_calls":[search]},{"role":"tool","tool_call_id":search["id"],"content":json.dumps({"matches":[{"capability_id":"mcp:fixture:tool-001"}]})}]
        describe=chat(messages,bridge)["choices"][0]["message"]["tool_calls"][0]
        assert describe["function"]["name"]=="tool_describe"
        messages += [{"role":"assistant","tool_calls":[describe]},{"role":"tool","tool_call_id":describe["id"],"content":json.dumps({"capability_id":"mcp:fixture:tool-001","schema_hash":"schema-1","describe_nonce":"nonce-1"})}]
        activate=chat(messages,bridge)["choices"][0]["message"]["tool_calls"][0]
        assert json.loads(activate["function"]["arguments"])=={"capability_id":"mcp:fixture:tool-001","schema_hash":"schema-1","describe_nonce":"nonce-1"}
        messages += [{"role":"assistant","tool_calls":[activate]},{"role":"tool","tool_call_id":activate["id"],"content":json.dumps({"status":"activated"})}]
        remote_call=chat(messages,[*bridge,remote])["choices"][0]["message"]["tool_calls"][0]
        assert remote_call["function"]["name"]=="mcp_ctx_fixture_tool_001"
        assert json.loads(remote_call["function"]["arguments"])["sequence"]==301
        messages += [{"role":"assistant","tool_calls":[remote_call]},{"role":"tool","tool_call_id":remote_call["id"],"content":json.dumps({"invocation_id":"inv-disclose"})}]
        assert chat(messages,[*bridge,remote])["choices"][0]["message"]["content"]=="TOOL-ACK:CTX-DISCLOSE-A:inv-disclose"

        stale_messages=[{"role":"user","content":"CTX-DISCLOSE-STALE"}]
        headers["X-DeskPet-Request-Id"]="req-stale"
        stale_search=chat(stale_messages,bridge)["choices"][0]["message"]["tool_calls"][0]
        stale_messages += [{"role":"assistant","tool_calls":[stale_search]},{"role":"tool","tool_call_id":stale_search["id"],"content":json.dumps({"matches":[{"capability_id":"cap-stale"}]})}]
        stale_describe=chat(stale_messages,bridge)["choices"][0]["message"]["tool_calls"][0]
        _request(daemon_base,"/pause-after-describe",{})
        stale_messages += [{"role":"assistant","tool_calls":[stale_describe]},{"role":"tool","tool_call_id":stale_describe["id"],"content":json.dumps({"capability_id":"cap-stale","schema_hash":"schema-1","describe_nonce":"nonce-stale"})}]
        with ThreadPoolExecutor(max_workers=1) as pool:
            activate_response=pool.submit(chat,stale_messages,bridge)
            fixture_log=daemon_tmp/"fixture.jsonl"
            for _ in range(100):
                if fixture_log.exists() and '"describe_barrier_enter"' in fixture_log.read_text(encoding="utf-8"):break
                time.sleep(.02)
            else:raise AssertionError("provider did not enter post-describe barrier")
            _request(daemon_base,"/disconnect",{})
            _request(daemon_base,"/resume-describe",{})
            stale_activate=activate_response.result(timeout=5)["choices"][0]["message"]["tool_calls"][0]
        assert stale_activate["function"]["name"]=="tool_activate"
        stale_messages += [{"role":"assistant","tool_calls":[stale_activate]},{"role":"tool","tool_call_id":stale_activate["id"],"content":json.dumps({"error":"tool_catalog_stale","retriable":False})}]
        terminal=chat(stale_messages,bridge)["choices"][0]["message"]
        assert terminal["content"]=="TOOL-CATALOG-STALE" and "tool_calls" not in terminal
        _request(daemon_base,"/reconnect",{})
        headers["X-DeskPet-Request-Id"]="req-recovered"
        recovered=chat([{"role":"user","content":"CTX-DISCLOSE-STALE"}],bridge)["choices"][0]["message"]
        assert recovered["tool_calls"][0]["function"]["name"]=="tool_search"
    finally:
        _stop_provider(base,p)


def test_provider_goal_artifact_and_snapshot_readback_scenarios(tmp_path):
    port=_free_port();base=f"http://127.0.0.1:{port}";env={**os.environ,"DESKPET_DEV_MODE":"1"}
    p=subprocess.Popen([sys.executable,str(SCRIPTS/"context_os_provider_fixture.py"),"--port",str(port),"--log",str(tmp_path/"provider-product.jsonl")],env=env)
    _wait(base,"/v1/models")
    headers={"Content-Type":"application/json","Authorization":"Bearer ctx-e2e-local-key","X-DeskPet-Purpose":"agent_response","X-DeskPet-Request-Id":"req-product","X-DeskPet-Attempt-Id":"att-1"}
    bridge=[{"type":"function","function":{"name":name,"parameters":{"type":"object"}}} for name in ("tool_search","tool_describe","tool_activate")]
    def chat(messages,tools):
        headers["X-DeskPet-Attempt-Id"]=f"att-{len(messages)}"
        req=urllib.request.Request(base+"/v1/chat/completions",json.dumps({"model":"ctx-primary","messages":messages,"tools":tools}).encode(),headers)
        with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)["choices"][0]["message"]
    def run(marker,target,result):
        messages=[{"role":"user","content":marker}]
        search=chat(messages,bridge)["tool_calls"][0]
        assert json.loads(search["function"]["arguments"])["query"]==target
        messages += [{"role":"assistant","tool_calls":[search]},{"role":"tool","tool_call_id":search["id"],"content":json.dumps({"matches":[{"capability_id":f"builtin:{target}"}]})}]
        describe=chat(messages,bridge)["tool_calls"][0]
        messages += [{"role":"assistant","tool_calls":[describe]},{"role":"tool","tool_call_id":describe["id"],"content":json.dumps({"capability_id":f"builtin:{target}","schema_hash":"schema-1","describe_nonce":"nonce-1"})}]
        activate=chat(messages,bridge)["tool_calls"][0]
        messages += [{"role":"assistant","tool_calls":[activate]},{"role":"tool","tool_call_id":activate["id"],"content":json.dumps({"status":"activated"})}]
        target_schema={"type":"function","function":{"name":target,"parameters":{"type":"object"}}}
        call=chat(messages,[*bridge,target_schema])["tool_calls"][0]
        assert call["function"]["name"]==target
        messages += [{"role":"assistant","tool_calls":[call]},{"role":"tool","tool_call_id":call["id"],"content":json.dumps(result)}]
        return call,chat(messages,[*bridge,target_schema])["content"]
    try:
        # Use the exact markers required by testcase.md.  The older CTX-*
        # aliases remain supported for targeted fixture diagnostics, but the
        # product E2E must exercise the prompts a human actually sends.
        goal_call,goal_ack=run("PENDING-A-0713", "goal_task_create", {"ok":True,"task_id":"task-1","status":"pending"})
        goal_args=json.loads(goal_call["function"]["arguments"])
        assert "PENDING-A-0713" in goal_args["title"]
        assert goal_args["note"].startswith("[decision] DECISION-A-0713")
        assert goal_ack=="PENDING-A-0713 status=pending task_id=task-1; DECISION-A-0713"
        headers["X-DeskPet-Request-Id"]="req-artifact"
        artifact_call,artifact_ack=run("ARTIFACT-A-0713", "file_write", {"ok":True,"path":"context-os-e2e/ARTIFACT-A-0713.md"})
        artifact_args=json.loads(artifact_call["function"]["arguments"])
        assert "ARTIFACT-A-0713" in artifact_args["content"]
        assert artifact_ack.endswith("context-os-e2e/ARTIFACT-A-0713.md")
        headers["X-DeskPet-Request-Id"]="req-readback"
        readback=chat([
            {"role":"system","content":"goal GOAL-A-0713; decision DECISION-A-0713; pending PENDING-A-0713; artifact ARTIFACT-A-0713"},
            {"role":"user","content":"CTX-TASK-READBACK"},
        ],bridge)
        assert "PENDING-A-0713 status=pending" in readback["content"]
        assert "ARTIFACT-A-0713.md" in readback["content"]
    finally:
        _stop_provider(base,p)


def test_provider_paused_attempt_hot_replace_fails_closed_and_next_request_recovers(daemon,tmp_path):
    daemon_base,_,_=daemon
    port=_free_port();base=f"http://127.0.0.1:{port}";env={**os.environ,"DESKPET_DEV_MODE":"1","DESKPET_CONTEXT_OS_E2E_DAEMON_URL":daemon_base}
    p=subprocess.Popen([sys.executable,str(SCRIPTS/"context_os_provider_fixture.py"),"--port",str(port),"--log",str(tmp_path/"provider-hot.jsonl")],env=env)
    _wait(base,"/v1/models")
    marker="CTX-DISCLOSE-HOT";headers={"Content-Type":"application/json","Authorization":"Bearer ctx-e2e-local-key","X-DeskPet-Purpose":"agent_response","X-DeskPet-Request-Id":"req-hot","X-DeskPet-Attempt-Id":"att-hot"}
    bridge=[{"type":"function","function":{"name":name,"parameters":{"type":"object"}}} for name in ("tool_search","tool_describe","tool_activate")]
    body={"model":"ctx-primary","messages":[{"role":"user","content":marker}],"tools":bridge}
    _request(base,"/__control/pause-before-attempt",{"marker":marker})
    aux_headers={**headers,"X-DeskPet-Purpose":"classifier","X-DeskPet-Request-Id":"aux-hot","X-DeskPet-Attempt-Id":"aux-att-hot"}
    aux_req=urllib.request.Request(base+"/v1/chat/completions",json.dumps({**body,"tools":None}).encode(),aux_headers)
    with urllib.request.urlopen(aux_req,timeout=5) as response:
        assert json.load(response)["choices"][0]["message"]["content"]=="AUX-ACK:classifier"
    def paused_chat():
        req=urllib.request.Request(base+"/v1/chat/completions",json.dumps(body).encode(),headers)
        try:
            urllib.request.urlopen(req,timeout=5)
        except urllib.error.HTTPError as exc:
            return exc.code,json.loads(exc.read())
        raise AssertionError("paused stale attempt unexpectedly succeeded")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(paused_chat)
            log=tmp_path/"provider-hot.jsonl"
            for _ in range(100):
                if log.exists() and '"attempt_paused"' in log.read_text(encoding="utf-8"):break
                time.sleep(.02)
            else:raise AssertionError("provider attempt did not pause")
            _request(daemon_base,"/hot-replace",{"tool":"mcp_ctx_fixture_tool_001","version":2})
            _request(base,"/__control/resume-attempt",{"marker":marker})
            code,error=future.result(timeout=5)
        assert code==409 and error["error"]["code"]=="tool_catalog_stale"
        headers["X-DeskPet-Request-Id"]="req-hot-recovered";headers["X-DeskPet-Attempt-Id"]="att-hot-recovered"
        req=urllib.request.Request(base+"/v1/chat/completions",json.dumps(body).encode(),headers)
        with urllib.request.urlopen(req,timeout=5) as response:recovered=json.load(response)
        assert recovered["choices"][0]["message"]["tool_calls"][0]["function"]["name"]=="tool_search"
    finally:
        _stop_provider(base,p)


def test_provider_pause_can_wait_until_activated_tool_is_in_payload(daemon,tmp_path):
    daemon_base,_,_=daemon
    port=_free_port();base=f"http://127.0.0.1:{port}";env={**os.environ,"DESKPET_DEV_MODE":"1","DESKPET_CONTEXT_OS_E2E_DAEMON_URL":daemon_base}
    p=subprocess.Popen([sys.executable,str(SCRIPTS/"context_os_provider_fixture.py"),"--port",str(port),"--log",str(tmp_path/"provider-tool-pause.jsonl")],env=env)
    _wait(base,"/v1/models")
    marker="CTX-DISCLOSE-HOT";headers={"Content-Type":"application/json","Authorization":"Bearer ctx-e2e-local-key","X-DeskPet-Purpose":"agent_response","X-DeskPet-Request-Id":"req-tool-pause","X-DeskPet-Attempt-Id":"att-tool-pause"}
    bridge=[{"type":"function","function":{"name":name,"parameters":{"type":"object"}}} for name in ("tool_search","tool_describe","tool_activate")]
    target={"type":"function","function":{"name":"mcp_ctx_fixture_tool_001","parameters":{"type":"object"}}}
    try:
        _request(base,"/__control/pause-before-attempt",{"marker":marker,"tool":"mcp_ctx_fixture_tool_001"})
        first=urllib.request.Request(base+"/v1/chat/completions",json.dumps({"model":"ctx-primary","messages":[{"role":"user","content":marker}],"tools":bridge}).encode(),headers)
        with urllib.request.urlopen(first,timeout=5) as response:
            assert json.load(response)["choices"][0]["message"]["tool_calls"][0]["function"]["name"]=="tool_search"

        def target_attempt():
            target_headers={**headers,"X-DeskPet-Request-Id":"req-tool-pause-target","X-DeskPet-Attempt-Id":"att-tool-pause-target"}
            req=urllib.request.Request(base+"/v1/chat/completions",json.dumps({"model":"ctx-primary","messages":[{"role":"user","content":marker}],"tools":[*bridge,target]}).encode(),target_headers)
            with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(target_attempt)
            log=tmp_path/"provider-tool-pause.jsonl"
            for _ in range(100):
                if log.exists() and '"required_tool": "mcp_ctx_fixture_tool_001"' in log.read_text(encoding="utf-8"):break
                time.sleep(.02)
            else:raise AssertionError("target-bearing provider attempt did not pause")
            _request(base,"/__control/resume-attempt",{"marker":marker})
            assert future.result(timeout=5)["choices"][0]["message"]["tool_calls"][0]["function"]["name"]=="tool_search"
    finally:
        _stop_provider(base,p)


def test_evidence_join_redacts_and_indexes(tmp_path):
    sys.path.insert(0,str(SCRIPTS))
    from context_os_evidence import join
    p=tmp_path/"events.jsonl";p.write_text(json.dumps({"ts":1,"purpose":"agent_response","request_id":"r","attempt_id":"a","content":"secret"})+"\n",encoding="utf-8")
    value=join({"provider":p},"r")
    assert value["events"][0]["content"]=="<redacted>"
    assert "agent_response|r|a" in value["attempt_index"]


def test_evidence_join_accepts_powershell_utf16_logs(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    from context_os_evidence import join

    path = tmp_path / "tauri-dev.log"
    path.write_text(
        json.dumps(
            {
                "ts": 1,
                "purpose": "agent_response",
                "request_id": "r-utf16",
                "attempt_id": "a-utf16",
            }
        )
        + "\n",
        encoding="utf-16",
    )

    value = join({"backend": path}, "r-utf16")

    assert value["event_count"] == 1
    assert "agent_response|r-utf16|a-utf16" in value["attempt_index"]


def test_e2e01_raw_marker_oracle_requires_exact_value():
    sys.path.insert(0, str(SCRIPTS))
    from context_os_evidence import (
        EvidenceOracleError,
        RAW_MARKER_ORACLE,
        validate_e2e01_raw_value,
    )

    assert validate_e2e01_raw_value(RAW_MARKER_ORACLE)["passed"] is True
    with pytest.raises(EvidenceOracleError, match="raw value mismatch"):
        validate_e2e01_raw_value("RAW-12/RAW-06/RAW-01")
    with pytest.raises(EvidenceOracleError, match="raw value mismatch"):
        validate_e2e01_raw_value("RAW-01 RAW-06 RAW-12")


def test_e2e11_off_golden_is_explicit_and_compared_without_on_derivation():
    sys.path.insert(0, str(SCRIPTS))
    from context_os_evidence import (
        EvidenceOracleError,
        OFF_TASK_TYPES,
        load_off_golden,
        validate_e2e11_off_observation,
    )

    golden_path = ROOT / "backend/tests/fixtures/context_os_off_golden.json"
    golden = load_off_golden(golden_path)
    assert tuple(golden["task_tools"]) == OFF_TASK_TYPES
    assert golden["registry_revision"] == 51
    assert golden["task_tools"]["code"] == [
        "file_read",
        "file_write",
        "workspace_recall",
    ]
    assert golden["task_tools"]["task"] == []
    assert golden["task_tools"]["plan"] == []
    assert golden["task_tools"]["emotion"] == []
    assert all(
        "*" not in names for names in golden["task_tools"].values()
    )
    observed = {
        "registry_revision": golden["registry_revision"],
        "task_tools": golden["task_tools"],
    }
    assert validate_e2e11_off_observation(observed, golden)["passed"] is True
    changed = json.loads(json.dumps(observed))
    changed["task_tools"]["recall"].reverse()
    with pytest.raises(EvidenceOracleError, match="checked-in legacy golden"):
        validate_e2e11_off_observation(changed, golden)
    source = (SCRIPTS / "context_os_evidence.py").read_text(encoding="utf-8")
    validate_source = source.split("def validate_e2e11_off_observation", 1)[1].split(
        "def _read_json", 1
    )[0]
    assert "direct_selectors" not in validate_source
    assert "discoverable" not in validate_source


def test_case_evidence_records_isolation_sources_and_exact_oracle(tmp_path):
    sys.path.insert(0, str(SCRIPTS))
    from context_os_evidence import RAW_MARKER_ORACLE, build_case_evidence

    userdata = tmp_path / "userdata"
    userdata.mkdir()
    provider = tmp_path / "provider.jsonl"
    provider.write_text(
        json.dumps({"ts": 1, "request_id": "req-raw", "event": "terminal"})
        + "\n",
        encoding="utf-8",
    )
    evidence = build_case_evidence(
        case_id="E2E-CTX-01",
        session_ids=["raw-a"],
        userdata=userdata,
        paths={"provider": provider},
        request_id="req-raw",
        observation={"raw_marker_value": RAW_MARKER_ORACLE},
    )
    assert evidence["isolation"] == {
        "userdata": str(userdata.resolve()),
        "session_ids": ["raw-a"],
    }
    assert evidence["sources"]["provider"]["exists"] is True
    assert len(evidence["sources"]["provider"]["sha256"]) == 64
    assert evidence["oracles"][0]["passed"] is True


def test_evidence_runner_allocates_per_case_userdata_and_session(tmp_path, monkeypatch):
    sys.path.insert(0, str(SCRIPTS))
    import run_context_os_evidence as runner

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {"id": "AT-CTX-01", "assertions": [], "command": ["noop"]},
                    {"id": "AT-CTX-02", "assertions": [], "command": ["noop"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["env"].copy()))
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(
        runner, "_reset_controls", lambda _env: {"daemon": {"status": "clean"}}
    )
    out = tmp_path / "out"
    assert runner.run_automation_isolated(
        ["--manifest", str(manifest), "--out", str(out)]
    ) == 0
    assert len(calls) == 2
    userdata = [Path(env["DESKPET_USER_DATA_DIR"]) for _, env in calls]
    sessions = [env["DESKPET_CONTEXT_OS_E2E_SESSION_ID"] for _, env in calls]
    assert userdata[0] != userdata[1]
    assert all(path.is_dir() for path in userdata)
    assert len(set(sessions)) == 2
    evidence = json.loads(
        (out / "evidence-manifest.json").read_text(encoding="utf-8")
    )
    assert [item["case_id"] for item in evidence["cases"]] == [
        "AT-CTX-01",
        "AT-CTX-02",
    ]


def test_launcher_enforces_case_isolation_reset_and_evidence_manifest():
    source = (SCRIPTS / "launch_context_os.ps1").read_text(encoding="utf-8")
    assert "[string]$CaseId" in source
    assert '("cases\\" + $CaseId)' in source
    assert 'Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:18991/reset"' in source
    assert "foreach ($sid in $SessionId)" in source
    assert '"evidence-manifest.json"' in source
    assert 'Only E2E-CTX-08 may explicitly reuse E2E-CTX-07 userdata' in source
    assert '$fixtureCatalogEnabled = $CaseId -ne "E2E-CTX-11"' in source
    assert 'keep the loopback daemon/provider trust gate' in source
    assert 'DESKPET_CONTEXT_OS_E2E_FIXTURE_CATALOG = "0"' in source
    assert 'DESKPET_CONTEXT_OS_E2E_CASE_ID = $CaseId' in source
    assert 'DESKPET_CONTEXT_OS_E2E_SESSION_ID = [string]$SessionId[0]' in source
    assert 'DESKPET_CONTEXT_OS_E2E_MAX_ITERATIONS = "4"' in source
    assert '[ValidateSet("Default", "On", "Off")]' in source
    assert 'context_os_mode = $ContextOSMode' in source
    assert 'exactly one context_os_v1 assignment' in source
