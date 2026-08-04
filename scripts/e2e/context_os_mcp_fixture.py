#!/usr/bin/env python3
"""Stateless MCP stdio proxy for the Context OS E2E daemon."""
from __future__ import annotations
import atexit, json, os, sys, urllib.error, urllib.request

BASE=os.environ.get("DESKPET_CONTEXT_OS_E2E_DAEMON_URL","http://127.0.0.1:18991").rstrip("/")

def post(path, body):
    req=urllib.request.Request(BASE+path, json.dumps(body).encode(), {"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=5) as r: return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 410: raise SystemExit(0)
        raise

def emit(obj):
    raw=json.dumps(obj,ensure_ascii=False,separators=(",",":"))
    sys.stdout.write(raw+"\n"); sys.stdout.flush()

def result(i,value): emit({"jsonrpc":"2.0","id":i,"result":value})
def error(i,code,message): emit({"jsonrpc":"2.0","id":i,"error":{"code":code,"message":message}})

def main():
    if os.environ.get("DESKPET_DEV_MODE") != "1" or not BASE.startswith(("http://127.0.0.1:","http://localhost:")):
        raise SystemExit("MCP fixture requires dev mode and loopback daemon")
    pid=os.getpid(); post("/proxy-register",{"pid":pid}); atexit.register(lambda: _unregister(pid))
    for line in sys.stdin:
        try:
            msg=json.loads(line); mid=msg.get("id"); method=msg.get("method",""); params=msg.get("params") or {}
            if mid is None: continue
            if method == "initialize":
                result(mid,{"protocolVersion":params.get("protocolVersion","2024-11-05"),"capabilities":{"tools":{"listChanged":False}},"serverInfo":{"name":"context-os-e2e","version":"1"}})
            elif method == "ping": result(mid,{})
            elif method == "tools/list":
                response=post("/rpc/list",{})
                tools=[]
                for item in response["tools"]:
                    annotations={k:item[k] for k in ("fixture_epoch","fixture_spec_hash","fixture_spec_version")}
                    tools.append({"name":item["name"],"description":item["description"],"inputSchema":item["inputSchema"],"annotations":annotations,"_meta":annotations})
                result(mid,{"tools":tools})
            elif method == "tools/call":
                response=post("/rpc/call",{"name":params.get("name"),"arguments":params.get("arguments") or {}})
                value=response["result"]
                result(mid,{"content":[{"type":"text","text":json.dumps(value,ensure_ascii=False,sort_keys=True)}],"structuredContent":value,"isError":False})
            else: error(mid,-32601,"method not found")
        except SystemExit: raise
        except Exception as exc: error(msg.get("id") if isinstance(msg,dict) else None,-32603,type(exc).__name__)

def _unregister(pid):
    try: post("/proxy-unregister",{"pid":pid})
    except Exception: pass

if __name__ == "__main__": main()
