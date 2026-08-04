"""Strictly dev/loopback-only Context OS E2E HTTP hooks."""
from __future__ import annotations
import json, os, sys, urllib.request
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse

def _loopback(url: str) -> bool:
    try:
        p=urlparse(url); return p.scheme=="http" and p.hostname in {"127.0.0.1","localhost","::1"}
    except Exception:return False

@dataclass(frozen=True)
class ContextOSE2EHooks:
    daemon_url:str; provider_url:str=""; timeout_seconds:float=.5
    @classmethod
    def from_env(cls):
        daemon=os.environ.get("DESKPET_CONTEXT_OS_E2E_DAEMON_URL","").rstrip("/");provider=os.environ.get("DESKPET_CONTEXT_OS_E2E_PROVIDER_URL","").rstrip("/")
        if os.environ.get("DESKPET_DEV_MODE")!="1" or not daemon or not _loopback(daemon) or (provider and not _loopback(provider)):return None
        try:timeout=max(.05,int(os.environ.get("DESKPET_CONTEXT_OS_E2E_HOOK_TIMEOUT_MS","500"))/1000)
        except ValueError:timeout=.5
        return cls(daemon,provider,timeout)
    def _request(self,path,body=None):
        data=None if body is None else json.dumps(body,separators=(",",":")).encode();req=urllib.request.Request(self.daemon_url+path,data=data,headers={"Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(req,timeout=self.timeout_seconds) as r:value=json.load(r)
        except Exception as exc:raise RuntimeError("tool_policy_unavailable") from exc
        if not isinstance(value,dict):raise RuntimeError("tool_policy_unavailable")
        return value
    def visible(self,*,session_id,tool):return self._request("/visibility?"+urlencode({"session_id":session_id,"tool":tool})).get("allowed") is True
    def observe_describe(self,*,session_id,request_id,tool,schema_hash):
        try:self._request("/describe-observed",{"session_id":session_id,"request_id":request_id,"tool":tool,"schema_hash":schema_hash})
        except RuntimeError as exc:raise RuntimeError("tool_catalog_stale") from exc
    def catalog(self,names):
        try:return self._request("/catalog-meta?"+urlencode({"tools":",".join(sorted(names))}))
        except RuntimeError as exc:raise RuntimeError("tool_catalog_stale") from exc
    def fault(self,name:str):
        """Consume one dev-only deterministic fault from the fixture daemon."""
        try:return self._request("/fault/"+str(name)+"?consume=1").get("value")
        except RuntimeError as exc:raise RuntimeError("context_os_fixture_unavailable") from exc

def trusted_context_os_e2e_case(*, allowed_cases, session_id:str|None=None)->bool:
    """Return true only for a launcher-bound, local source-checkout E2E case."""
    if getattr(sys,"frozen",False) or ContextOSE2EHooks.from_env() is None:return False
    case_id=os.environ.get("DESKPET_CONTEXT_OS_E2E_CASE_ID","").strip()
    expected_session=os.environ.get("DESKPET_CONTEXT_OS_E2E_SESSION_ID","").strip()
    if case_id not in set(allowed_cases) or not expected_session:return False
    return session_id is None or str(session_id)==expected_session

def consume_context_os_e2e_fault(name:str):
    """Return the armed fault value, or ``None`` outside trusted E2E runs."""
    hooks=ContextOSE2EHooks.from_env()
    return None if hooks is None else hooks.fault(name)

def trusted_provider_headers(base_url):
    hooks=ContextOSE2EHooks.from_env()
    if hooks is None or not hooks.provider_url or hooks.provider_url.removesuffix("/v1").rstrip("/")!=base_url.removesuffix("/v1").rstrip("/"):return {}
    from agent.context_messages import current_provider_attempt_options,normalize_provider_purpose
    o=current_provider_attempt_options()
    if o is None:return {}
    return {"X-DeskPet-Purpose":normalize_provider_purpose(o.purpose),"X-DeskPet-Request-Id":str(o.request_id or ""),"X-DeskPet-Attempt-Id":str(o.attempt_id or "")}
