#!/usr/bin/env python3
"""Stateless CLI for Context OS fixture control planes."""
from __future__ import annotations
import argparse, json, os, subprocess, sys, urllib.request
from pathlib import Path

BASE=os.environ.get("DESKPET_CONTEXT_OS_E2E_DAEMON_URL","http://127.0.0.1:18991").rstrip("/")
PROVIDER=os.environ.get("DESKPET_CONTEXT_OS_E2E_PROVIDER_URL","http://127.0.0.1:18992/v1").removesuffix("/v1").rstrip("/")
HERE=Path(__file__).resolve().parent

def request(base,path,body=None,method=None):
    data=None if body is None else json.dumps(body).encode(); req=urllib.request.Request(base+path,data,{"Content-Type":"application/json"},method=method)
    with urllib.request.urlopen(req,timeout=10) as r:return json.load(r)
def required(value, flag):
    if value is None or (isinstance(value, str) and not value.strip()):
        raise SystemExit(f"{flag} is required")
    return value

def main():
    p=argparse.ArgumentParser(); p.add_argument("command",choices=["start","status","reset","allow-session","deny-session","pause-after-describe","resume-describe","disconnect","reconnect","hot-replace","set-model","set-fault","clear-fault","model-fault","clear-model-fault","stop","provider-state","pause-before-attempt","resume-attempt","fallback","clear-fallback","force-finish","clear-force-finish","provider-reset"])
    p.add_argument("--session-id"); p.add_argument("--tool"); p.add_argument("--version",type=int); p.add_argument("--name"); p.add_argument("--value"); p.add_argument("--model"); p.add_argument("--window",type=int); p.add_argument("--error"); p.add_argument("--marker"); p.add_argument("--from",dest="from_model"); p.add_argument("--to",dest="to_model"); p.add_argument("--tool-rounds",type=int,default=3); p.add_argument("--log")
    a=p.parse_args(); c=a.command
    if c=="start":
        env={**os.environ,"DESKPET_DEV_MODE":"1"}; cmd=[sys.executable,str(HERE/"context_os_mcp_daemon.py")]
        if a.log: cmd += ["--log",a.log]
        proc=subprocess.Popen(cmd,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)); out={"ok":True,"pid":proc.pid}
    elif c=="status": out=request(BASE,"/health")
    elif c=="provider-state": out=request(PROVIDER,"/__control/state")
    elif c in ("allow-session","deny-session"): out=request(BASE,"/"+c,{"session_id":required(a.session_id,"--session-id")})
    elif c=="hot-replace": out=request(BASE,"/hot-replace",{"tool":required(a.tool,"--tool"),"version":a.version})
    elif c=="set-fault": out=request(BASE,"/fault",{"name":required(a.name,"--name"),"value":a.value if a.value is not None else True})
    elif c=="clear-fault": out=request(BASE,"/fault/"+str(required(a.name,"--name")),method="DELETE")
    elif c=="set-model": out=request(PROVIDER,"/__control/catalog",{"model":required(a.model,"--model"),"window":required(a.window,"--window")})
    elif c=="model-fault": out=request(PROVIDER,"/__control/model-fault",{"model":required(a.model,"--model"),"error":a.error or "fixture_model_fault"})
    elif c=="clear-model-fault": out=request(PROVIDER,"/__control/model-fault/"+str(required(a.model,"--model")),method="DELETE")
    elif c in ("pause-before-attempt","resume-attempt"):
        out=request(PROVIDER,"/__control/"+c,{"marker":a.marker,"tool":a.tool})
    elif c=="fallback": out=request(PROVIDER,"/__control/fallback",{"marker":required(a.marker,"--marker"),"from":a.from_model or "ctx-primary","to":a.to_model or "ctx-fallback"})
    elif c=="clear-fallback": out=request(PROVIDER,"/__control/fallback",method="DELETE")
    elif c=="force-finish": out=request(PROVIDER,"/__control/force-finish",{"marker":required(a.marker,"--marker"),"tool_rounds":a.tool_rounds})
    elif c=="clear-force-finish": out=request(PROVIDER,"/__control/force-finish",method="DELETE")
    elif c=="provider-reset": out=request(PROVIDER,"/__control/reset",{})
    else: out=request(BASE,"/"+c,{})
    print(json.dumps(out,ensure_ascii=False,sort_keys=True))
if __name__=="__main__": main()
